/* ============================================================
   CampusPulse 前端共享运行时

   类型说明：全局契约（CPDataset / CPFilters / CPState / CPError / CPChartSpec）
   定义在 cp.d.ts，本文件的公共函数全部带 JSDoc 注解，
   由 `npm run typecheck`（tsc --noEmit + checkJs）校验。
   页面层的 DOM 操作不做强类型，重点管住"会流动的数据"。
   - 统一 API 信封处理与错误提示
   - 数据集 / 筛选条件跨页面保持（写入 URL，可直接分享）
   - ECharts 主题与「后端图表规格 → ECharts option」渲染器
   - 加载 / 空数据 / 错误 三态
   ============================================================ */
(function (global) {
    'use strict';

    const PALETTE = ['#165DFF', '#22C55E', '#94A3B8', '#F59E0B', '#8B5CF6', '#06B6D4', '#E5484D', '#475569'];
    const SENTIMENT_COLOR = { '正面': '#16A34A', '中性': '#94A3B8', '负面': '#E5484D' };
    const POS = '#16A34A', NEU = '#94A3B8', NEG = '#E5484D';

    const state = {
        dataset_id: null,
        datasets: [],
        datasetMeta: null,
        filters: { platform: [], dimension: [], sentiment: [], date_from: '', date_to: '', keyword: '' },
        health: null
    };

    /* ---------------- 基础工具 ---------------- */

    /**
     * 单元素查询。
     * @param {string} sel CSS 选择器
     * @param {ParentNode} [root] 查询根，默认 document
     * @returns {any} 实际元素类型由调用点决定（input / div / canvas 都可能）
     */
    function $(sel, root) { return (root || document).querySelector(sel); }

    /**
     * 多元素查询，返回真数组（可用 forEach / map / index）。
     * @param {string} sel
     * @param {ParentNode} [root]
     * @returns {any[]}
     */
    function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

    /**
     * HTML 转义。所有拼进 innerHTML 的动态内容都必须过这一层——
     * 评论原文来自公开平台，里面出现 <script> 并不稀奇。
     * @param {any} s
     * @returns {string}
     */
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
        ));
    }

    /**
     * 数字千分位；null/空返回 "—"（不把"没有数据"显示成 0）。
     * @param {any} v
     * @returns {string}
     */
    function num(v) {
        if (v == null || v === '') return '—';
        const n = Number(v);
        if (!isFinite(n)) return esc(v);
        return n.toLocaleString('zh-CN');
    }

    /**
     * 百分比，保留一位小数。
     * @param {any} v
     * @returns {string}
     */
    function pct(v) {
        if (v == null || v === '') return '—';
        const n = Number(v);
        return (Math.round(n * 10) / 10).toFixed(1) + '%';
    }

    function dateShort(d) { return d ? String(d).slice(0, 10) : '—'; }

    /**
     * 右上角提示，3 秒后自动消失。
     * @param {string} msg
     * @param {'ok'|'err'} [kind]
     */
    function toast(msg, kind) {
        const wrap = $('#toastWrap');
        if (!wrap) return;
        const el = document.createElement('div');
        el.className = 'toast ' + (kind === 'err' ? 'err' : kind === 'ok' ? 'ok' : '');
        el.textContent = msg;
        wrap.appendChild(el);
        setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; }, 3000);
        setTimeout(() => el.remove(), 3400);
    }

    /* ---------------- URL 状态 ---------------- */

    /** 从地址栏恢复数据集与筛选条件（刷新、分享链接都靠它） */
    function readUrl() {
        const p = new URLSearchParams(location.search);
        state.dataset_id = p.get('dataset_id') || null;
        state.filters.platform = (p.get('platform') || '').split(',').filter(Boolean);
        state.filters.dimension = (p.get('dimension') || '').split(',').filter(Boolean);
        state.filters.sentiment = (p.get('sentiment') || '').split(',').filter(Boolean);
        state.filters.date_from = p.get('date_from') || '';
        state.filters.date_to = p.get('date_to') || '';
        state.filters.keyword = p.get('keyword') || '';
    }

    /**
     * 拼当前状态的查询串。
     * @param {Record<string, any>} [extra] 额外参数（证据页的分页、排序等）
     * @returns {string}
     */
    function qs(extra) {
        const p = new URLSearchParams();
        if (state.dataset_id) p.set('dataset_id', state.dataset_id);
        if (state.filters.platform.length) p.set('platform', state.filters.platform.join(','));
        if (state.filters.dimension.length) p.set('dimension', state.filters.dimension.join(','));
        if (state.filters.sentiment.length) p.set('sentiment', state.filters.sentiment.join(','));
        if (state.filters.date_from) p.set('date_from', state.filters.date_from);
        if (state.filters.date_to) p.set('date_to', state.filters.date_to);
        if (state.filters.keyword) p.set('keyword', state.filters.keyword);
        // 保留地址栏上不属于筛选口径的其它参数（证据页的 offset / order、详情 ref）。
        // 不保留的话，core 初始化那一次 syncUrl() 就会把它们抹掉——
        // 表现是"刷新后永远回到第一页"，而且地址栏看不出任何痕迹。
        const FILTER_KEYS = new Set(['dataset_id', 'platform', 'dimension', 'sentiment',
                                     'date_from', 'date_to', 'keyword']);
        new URLSearchParams(location.search).forEach((v, k) => {
            if (!FILTER_KEYS.has(k) && !p.has(k)) p.set(k, v);
        });
        Object.keys(extra || {}).forEach(k => {
            const v = extra[k];
            if (v == null || v === '') p.delete(k); else p.set(k, v);
        });
        return p.toString();
    }

    /**
     * 把当前状态写回地址栏（replaceState，不产生历史记录）。
     * @param {Record<string, any>} [extra]
     */
    function syncUrl(extra) {
        // extra 是给单页自己的（如证据页的 offset / order）。
        // 以前这个函数不收参数，调用方传了也是白传——地址栏看着没坏，其实什么都没记下。
        const query = qs(extra);
        const url = location.pathname + (query ? '?' + query : '');
        history.replaceState(null, '', url);
        // 导航链接只带筛选，不带分页：跳到别的页面时页码没有意义
        const navQuery = qs();
        $$('.nav-item').forEach(a => {
            const base = a.getAttribute('href').split('?')[0];
            a.setAttribute('href', base + (navQuery ? '?' + navQuery : ''));
        });
    }

    /* ---------------- API ---------------- */

    /**
     * 统一接口调用：拼参数 → fetch → 解信封。
     *
     * 后端所有接口都是 {"ok":true,"data":{...}} / {"ok":false,"error":{...}}，
     * 这里把非 2xx 和 ok:false 统一转成异常，页面只需要 try/catch 一处。
     *
     * @param {string} path 例：/api/datasets
     * @param {Record<string, any>|null} [params] 查询参数；数组会展开成重复 key
     * @param {RequestInit} [opts]
     * @returns {Promise<any>} 信封里的 data
     * @throws {CPError} 带 detail（后端结构化细节）与 status（HTTP 状态码）
     */
    async function api(path, params, opts) {
        const p = new URLSearchParams();
        Object.keys(params || {}).forEach(k => {
            const v = params[k];
            if (v === undefined || v === null || v === '') return;
            if (Array.isArray(v)) v.forEach(x => p.append(k, x));
            else p.set(k, v);
        });
        const url = path + (p.toString() ? '?' + p.toString() : '');
        const init = Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {});
        const resp = await fetch(url, init);
        let body = null;
        try { body = await resp.json(); } catch (e) { body = null; }
        if (!resp.ok || !body || body.ok === false) {
            const msg = (body && body.error && body.error.message) || ('请求失败（HTTP ' + resp.status + '）');
            // 后端错误信封比 Error 多两个字段：detail（结构化细节）与 status。
            // 断言成 CPError，调用方才敢直接读 e.detail —— 否则 checkJs 下到处都是报错。
            const err = /** @type {CPError} */ (new Error(msg));
            err.detail = body && body.error ? body.error.detail : null;
            err.status = resp.status;
            throw err;
        }
        return body.data;
    }

    /**
     * 当前筛选条件 + 数据集，作为查询参数对象。
     * @param {Record<string, any>} [extra]
     * @returns {Record<string, any>}
     */
    function filterParams(extra) {
        return Object.assign({
            dataset_id: state.dataset_id,
            platform: state.filters.platform,
            dimension: state.filters.dimension,
            sentiment: state.filters.sentiment,
            date_from: state.filters.date_from,
            date_to: state.filters.date_to,
            keyword: state.filters.keyword
        }, extra || {});
    }

    /* ---------------- 三态 ---------------- */
    /**
     * 三态占位：加载中 / 空结果 / 失败重试。
     * @param {HTMLElement|null} el
     * @param {'loading'|'empty'|'error'} kind
     * @param {string} [msg]
     * @param {() => void} [onRetry]
     */
    function setState(el, kind, msg, onRetry) {
        if (!el) return;
        if (kind === 'loading') {
            el.innerHTML = '<div class="state"><div class="spinner"></div><div>正在计算…</div></div>';
        } else if (kind === 'empty') {
            el.innerHTML = '<div class="state"><div class="state-title">当前筛选范围内没有数据</div>' +
                '<div>' + esc(msg || '可以放宽时间范围或切换数据集后再看。') + '</div></div>';
        } else if (kind === 'error') {
            el.innerHTML = '<div class="state state-error"><div class="state-title">加载失败</div>' +
                '<div>' + esc(msg || '请稍后重试') + '</div>' +
                '<button class="btn btn-sm" data-retry>重试</button></div>';
            const btn = el.querySelector('[data-retry]');
            if (btn && onRetry) btn.addEventListener('click', onRetry);
        }
    }


    /* ---------------- 主题 ---------------- */
    const THEME_KEY = 'cp-theme';

    function cssVar(name, fallback) {
        const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return v || fallback || '';
    }

    function getTheme() {
        return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    }

    /** 图表要用的颜色——统一从 CSS 变量读，深色模式下自动跟着变 */
    /**
     * 图表要用的颜色，统一从 CSS 变量读取。
     * @returns {{ink:string, ink2:string, ink3:string, ink4:string, line:string, line2:string, surface:string, dark:boolean}}
     */
    function themeColors() {
        return {
            ink: cssVar('--ink', '#0F172A'),
            ink2: cssVar('--ink-2', '#334155'),
            ink3: cssVar('--ink-3', '#64748B'),
            ink4: cssVar('--ink-4', '#94A3B8'),
            line: cssVar('--line', '#E9EEF6'),
            line2: cssVar('--line-2', '#F2F5FA'),
            surface: cssVar('--surface', '#fff'),
            dark: getTheme() === 'dark'
        };
    }

    function applyTheme(t, opts) {
        t = t === 'dark' ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', t);
        try { localStorage.setItem(THEME_KEY, t); } catch (e) { /* 隐私模式禁写，忽略 */ }
        const btn = $('#themeToggle');
        if (btn) {
            btn.setAttribute('aria-pressed', t === 'dark' ? 'true' : 'false');
            btn.title = t === 'dark' ? '切换到浅色主题' : '切换到深色主题';
        }
        const meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.setAttribute('content', t === 'dark' ? '#0B1220' : '#F7F9FC');
        // 图表颜色是"烘焙"进 option 的，变量变了必须重建：
        // 各页面监听 cp:theme 重新渲染（数据在本地，重取一次是毫秒级）
        document.dispatchEvent(new CustomEvent('cp:theme', { detail: { theme: t } }));
        return t;
    }

    function toggleTheme() {
        return applyTheme(getTheme() === 'dark' ? 'light' : 'dark');
    }

    function bindTheme() {
        const btn = $('#themeToggle');
        if (btn) {
            const t = getTheme();
            btn.setAttribute('aria-pressed', t === 'dark' ? 'true' : 'false');
            btn.addEventListener('click', () => toggleTheme());
        }
        // 系统偏好变化时，仅在用户没手动选过的情况下跟随
        if (window.matchMedia) {
            const mq = window.matchMedia('(prefers-color-scheme: dark)');
            const onChange = e => {
                let stored = null;
                try { stored = localStorage.getItem(THEME_KEY); } catch (err) {}
                if (!stored) applyTheme(e.matches ? 'dark' : 'light');
            };
            if (mq.addEventListener) mq.addEventListener('change', onChange);
            else if (mq.addListener) mq.addListener(onChange);
        }
    }

    /* ---------------- 移动端抽屉导航 ---------------- */
    function bindNavDrawer() {
        const toggle = $('#navToggle'), mask = $('#navMask');
        if (!toggle) return;
        const setOpen = open => {
            document.body.classList.toggle('side-open', open);
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        };
        toggle.addEventListener('click', () => setOpen(!document.body.classList.contains('side-open')));
        if (mask) mask.addEventListener('click', () => setOpen(false));
        document.addEventListener('keydown', e => { if (e.key === 'Escape') setOpen(false); });
        // 点了导航就关抽屉，否则跳转后遮罩还盖着
        $$('.nav-item').forEach(a => a.addEventListener('click', () => setOpen(false)));
    }

    /* ---------------- ECharts ---------------- */
    const charts = new WeakMap();
    /**
     * 已实例化的图表集合。
     * WeakMap 不能遍历，而"窗口 resize / 主题切换时统一 resize"需要遍历——
     * 只存 WeakMap 的话，那个场景就只能退化成"把所有 .chart 全 init 一遍"，
     * 等于把惰性初始化又亲手废掉。
     */
    const liveCharts = new Set();

    /* ---------------- 图表惰性初始化 ---------------- */
    // 洞察页有 11 张图，ECharts 每张 init 约 30–80ms：全部在首屏建完 ≈ 半秒卡顿，
    // 而其中 8 张根本不在第一屏。这里只把"可见"的图建出来，其余的等滚到附近再建。
    // 用 __CP_NO_LAZY 可以在同一台机器上做 A/B 对比（性能数据就是这么测的）。
    const LAZY_MARGIN = 160;              // 提前 160px 建图，滚到时已经画好了
    const pendingOptions = new WeakMap(); // el -> option（等可见）
    const pendingHooks = new WeakMap();   // el -> [(inst) => void]（等可见再绑交互）
    const visibleCbs = new WeakMap();     // el -> [() => void]（可见时执行一次，与实例无关）
    let lazyObserver = null;

    function lazyEnabled() {
        return typeof IntersectionObserver !== 'undefined' && !global.__CP_NO_LAZY;
    }

    function nearViewport(el) {
        const r = el.getBoundingClientRect();
        // 宽高为 0 说明容器被隐藏（如未激活的标签页），这种"看不见"也要延后
        if (!r.width && !r.height) return false;
        return r.top < window.innerHeight + LAZY_MARGIN && r.bottom > -LAZY_MARGIN;
    }

    function flushLazy(el) {
        const opt = pendingOptions.get(el);
        pendingOptions.delete(el);
        if (opt !== undefined) applyOption(el, opt);
        const hooks = pendingHooks.get(el) || [];
        pendingHooks.delete(el);
        if (hooks.length) {
            const inst = charts.get(el);
            if (inst) hooks.forEach(h => { try { h(inst); } catch (e) { /* 交互绑定失败不影响显示 */ } });
        }
        const cbs = visibleCbs.get(el) || [];
        visibleCbs.delete(el);
        cbs.forEach(cb => { try { cb(); } catch (e) { /* 回调失败不影响其它图表 */ } });
    }

    /**
     * 元素进入视口附近时执行一次。用于"滚到才加载的资源"（如词云插件）——
     * 这类资源没法挂在 setOption 上等，因为绘制它本身就需要资源先到位。
     * @param {HTMLElement|null} el
     * @param {() => void} cb
     */
    function whenVisible(el, cb) {
        if (!el || typeof cb !== 'function') return;
        if (!lazyEnabled() || nearViewport(el)) { cb(); return; }
        const arr = visibleCbs.get(el) || [];
        arr.push(cb);
        visibleCbs.set(el, arr);
        observeLazy(el);
    }

    function observeLazy(el) {
        if (!lazyEnabled()) return false;
        if (!lazyObserver) {
            lazyObserver = new IntersectionObserver(entries => {
                entries.forEach(en => {
                    if (!en.isIntersecting) return;
                    lazyObserver.unobserve(en.target);
                    flushLazy(en.target);
                });
            }, { rootMargin: LAZY_MARGIN + 'px' });
        }
        lazyObserver.observe(el);
        return true;
    }

    function themeBase() {
        const c = themeColors();
        return {
            color: PALETTE,
            backgroundColor: 'transparent',
            textStyle: {
                fontFamily: '-apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
                color: c.ink3
            },
            animationDuration: 420,
            tooltip: {
                backgroundColor: c.surface,
                borderColor: c.line,
                borderWidth: 1,
                padding: [8, 10],
                textStyle: { color: c.ink, fontSize: 12 },
                extraCssText: c.dark
                    ? 'box-shadow:0 8px 24px -10px rgba(0,0,0,.7);border-radius:10px;'
                    : 'box-shadow:0 8px 24px -10px rgba(15,23,42,.24);border-radius:10px;'
            }
        };
    }

    function axisCommon() {
        const c = themeColors();
        return {
            axisLine: { lineStyle: { color: c.line } },
            axisTick: { show: false },
            axisLabel: { color: c.ink4, fontSize: 11 },
            splitLine: { lineStyle: { color: c.line2, type: 'dashed' } }
        };
    }

    /**
     * 取（或惰性创建）ECharts 实例。按元素缓存，重复调用不会重复 init。
     * @param {HTMLElement|null} el
     * @returns {any} ECharts 实例；ECharts 未加载时返回 null
     */
    function chart(el) {
        if (!el || typeof echarts === 'undefined') return null;
        let inst = charts.get(el);
        if (!inst) {
            inst = echarts.init(el, null, { renderer: 'canvas' });
            charts.set(el, inst);
            liveCharts.add(inst);
            if (typeof ResizeObserver !== 'undefined') {
                const ro = new ResizeObserver(() => { try { inst.resize(); } catch (e) {} });
                ro.observe(el.parentElement || el);
            } else {
                window.addEventListener('resize', () => { try { inst.resize(); } catch (e) {} });
            }
        }
        return inst;
    }

    /**
     * 设置图表配置。notMerge=true：切筛选时整份替换，
     * 否则上一次的 series 会残留在图里（最典型的"数据变了但柱子没少"）。
     * @param {HTMLElement|null} el
     * @param {any} option
     * @returns {any}
     */
    function applyOption(el, option) {
        const inst = chart(el);
        if (!inst) return null;
        inst.setOption(option, true);
        setTimeout(() => { try { inst.resize(); } catch (e) {} }, 30);
        return inst;
    }

    function setOption(el, option) {
        if (!el) return null;
        // 有实例就直接画；没有实例且还没滚到附近，就先存着，别为了"看不见的图"卡住首屏
        if (!charts.has(el) && lazyEnabled() && !nearViewport(el)) {
            pendingOptions.set(el, option);
            observeLazy(el);
            return null;
        }
        return applyOption(el, option);
    }

    /**
     * 图表就绪回调。用于"渲染完要绑点击下钻"这类逻辑——
     * 惰性初始化下实例可能还不存在，直接取只会拿到 null，下钻就静默失效了。
     * @param {HTMLElement|null} el
     * @param {(inst: any) => void} cb
     */
    function onChart(el, cb) {
        if (!el || typeof cb !== 'function') return;
        const inst = charts.get(el);
        if (inst) { cb(inst); return; }
        const arr = pendingHooks.get(el) || [];
        arr.push(cb);
        pendingHooks.set(el, arr);
    }

    /** 只 resize 已经建好的图表；绝不触发新的 init */
    function resizeCharts() {
        liveCharts.forEach(i => { try { i.resize(); } catch (e) { /* 已销毁的实例忽略 */ } });
    }

    /**
     * 后端图表规格 → ECharts option。Agent 动态图表统一走这里，
     * 前端不参与任何数值计算——数字算错这件事只允许有一个地方发生。
     * @param {CPChartSpec} spec
     * @returns {any}
     */
    function specToOption(spec) {
        const base = themeBase();
        const unit = spec.unit || '';
        const axis = axisCommon();

        if (spec.type === 'pie') {
            const data = (spec.series && spec.series[0] && spec.series[0].data) || [];
            return Object.assign(base, {
                tooltip: Object.assign(base.tooltip, {
                    trigger: 'item',
                    formatter: p => `${esc(p.name)}<br/><b>${num(p.value)}</b> ${unit} · ${p.percent}%`
                }),
                legend: { bottom: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 11, color: themeColors().ink3 } },
                series: [{
                    type: 'pie', radius: ['48%', '70%'], center: ['50%', '46%'],
                    avoidLabelOverlap: true,
                    itemStyle: { borderColor: themeColors().surface, borderWidth: 2 },
                    label: { show: false },
                    emphasis: { label: { show: true, fontSize: 13, fontWeight: 600, formatter: '{b}\n{d}%' } },
                    data: data.map(d => Object.assign({}, d, {
                        itemStyle: { color: SENTIMENT_COLOR[d.name] || undefined }
                    }))
                }]
            });
        }

        const horizontal = !!spec.horizontal || spec.type === 'bar-horizontal';
        const series = (spec.series || []).map((s, i) => ({
            name: s.name, type: spec.type === 'line' ? 'line' : 'bar',
            data: s.data,
            smooth: spec.type === 'line',
            symbol: 'circle', symbolSize: 5,
            showSymbol: (spec.categories || []).length <= 24,
            lineStyle: { width: 2 },
            areaStyle: spec.type === 'line' && i === 0 ? { opacity: .08 } : undefined,
            barMaxWidth: 22,
            itemStyle: { borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] }
        }));

        const catAxis = Object.assign({ type: 'category', data: spec.categories || [] }, axis, {
            splitLine: { show: false },
            axisLabel: { color: themeColors().ink4, fontSize: 11, hideOverlap: true }
        });
        const valAxis = Object.assign({ type: 'value', name: unit, nameTextStyle: { color: themeColors().ink4, fontSize: 10, padding: [0, 0, 0, 6] } }, axis);

        return Object.assign(base, {
            tooltip: Object.assign(base.tooltip, { trigger: 'axis', axisPointer: { type: 'shadow' } }),
            legend: series.length > 1 ? { top: 0, right: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 11, color: themeColors().ink3 } } : undefined,
            grid: { left: 8, right: 16, top: series.length > 1 ? 30 : 18, bottom: 4, containLabel: true },
            xAxis: horizontal ? valAxis : catAxis,
            yAxis: horizontal ? catAxis : valAxis,
            series: series
        });
    }

    /**
     * 渲染后端图表规格。
     * @param {HTMLElement|null} el
     * @param {CPChartSpec} spec
     */
    function renderSpec(el, spec) {
        if (!el || !spec) return;
        setOption(el, specToOption(spec));
    }

    /* ---------------- 筛选栏 ---------------- */
    const DIMENSIONS = ['宿舍住宿', '食堂餐饮', '招生报考', '教学学业', '校园环境', '交通出行', '校园活动'];
    const PLATFORMS = ['抖音', '小红书'];

    /**
     * 渲染筛选栏并双向绑定 state.filters。
     * @param {HTMLElement} container
     * @param {{keyword?: boolean}} [opts] keyword=false 时不出关键词输入框
     * @returns {{sync: () => void, apply: () => void}}
     */
    function renderFilterBar(container, opts) {
        opts = opts || {};
        const withKeyword = opts.keyword !== false;
        container.classList.add('filterbar');
        container.innerHTML = `
            <span class="filter-label">平台</span>
            <div class="chip-group" data-group="platform">
                ${PLATFORMS.map(p => `<button class="chip" data-value="${p}">${p}</button>`).join('')}
            </div>
            <span class="filter-sep"></span>
            <span class="filter-label">维度</span>
            <select class="select" data-role="dimension" style="min-width:104px;">
                <option value="">全部维度</option>
                ${DIMENSIONS.map(d => `<option value="${d}">${d}</option>`).join('')}
            </select>
            <span class="filter-sep"></span>
            <span class="filter-label">情感</span>
            <div class="chip-group" data-group="sentiment">
                <button class="chip" data-value="positive">正面</button>
                <button class="chip" data-value="neutral">中性</button>
                <button class="chip" data-value="negative">负面</button>
            </div>
            <span class="filter-label" style="margin-left:4px;">区间</span>
            <input class="input" type="date" data-role="date_from" style="width:132px;">
            <span class="filter-label">至</span>
            <input class="input" type="date" data-role="date_to" style="width:132px;">
            ${withKeyword ? `<input class="input" data-role="keyword" placeholder="关键词" style="width:120px;">` : ''}
            <button class="btn btn-sm btn-ghost" data-role="reset" style="margin-left:auto;">重置筛选</button>
            <span class="badge" data-role="range-hint">—</span>
        `;

        const sync = () => {
            // 按 data-group 统一同步：屏幕上的选中态必须和 state.filters 完全一致，
            // 否则「对话带过来的筛选」会在界面上"看不见但生效"，用户既不知道也取消不掉
            $$('[data-group]', container).forEach(group => {
                const key = group.dataset.group;
                const cur = state.filters[key] || [];
                $$('.chip', group).forEach(c => {
                    c.classList.toggle('on', cur.includes(c.dataset.value));
                });
            });
            const dim = $('[data-role="dimension"]', container);
            dim.value = state.filters.dimension[0] || '';
            dim.title = state.filters.dimension.length > 1
                ? `实际已选 ${state.filters.dimension.length} 个维度：${state.filters.dimension.join('、')}（此处仅显示第一个）`
                : '';
            $('[data-role="date_from"]', container).value = state.filters.date_from || '';
            $('[data-role="date_to"]', container).value = state.filters.date_to || '';
            if (withKeyword) $('[data-role="keyword"]', container).value = state.filters.keyword || '';
            const span = state.datasetMeta ? (state.datasetMeta.time_start + ' ~ ' + state.datasetMeta.time_end) : '';
            $('[data-role="range-hint"]', container).textContent = span ? '数据覆盖 ' + span : '—';
        };

        function apply() {
            syncUrl();
            document.dispatchEvent(new CustomEvent('cp:filter', { detail: state.filters }));
        }

        container.addEventListener('click', e => {
            const el = /** @type {HTMLElement} */ (e.target);
            const chip = /** @type {HTMLElement} */ (el.closest('.chip'));
            if (chip) {
                const group = /** @type {HTMLElement} */ (chip.closest('[data-group]'));
                const key = group ? group.dataset.group : 'platform';
                if (!Array.isArray(state.filters[key])) state.filters[key] = [];
                const arr = state.filters[key];
                const v = chip.dataset.value;
                const i = arr.indexOf(v);
                if (i >= 0) arr.splice(i, 1); else arr.push(v);
                sync(); apply();
                return;
            }
            if (el.closest('[data-role="reset"]')) {
                state.filters = { platform: [], dimension: [], sentiment: [], date_from: '', date_to: '', keyword: '' };
                sync(); apply();
            }
        });

        container.addEventListener('change', e => {
            const el = /** @type {HTMLInputElement} */ (e.target);
            const role = el.dataset.role;
            if (role === 'dimension') state.filters.dimension = el.value ? [el.value] : [];
            else if (role === 'date_from') state.filters.date_from = el.value;
            else if (role === 'date_to') state.filters.date_to = el.value;
            else return;
            apply();
        });

        if (withKeyword) {
            let timer = null;
            const kw = $('[data-role="keyword"]', container);
            kw.addEventListener('input', () => {
                clearTimeout(timer);
                timer = setTimeout(() => { state.filters.keyword = kw.value.trim(); apply(); }, 450);
            });
        }

        sync();
        return { sync, apply };
    }

    // 最近一次渲染的筛选栏实例。切换数据集时要用它把「数据覆盖」提示重算一遍——
    // 以前这个返回值被直接丢掉了，导致切了数据集，KPI 和图表都变了，
    // 唯独筛选栏还写着上一个数据集的时间范围（最容易被评委抓住的那种不一致）
    let filterBarApi = null;

    /* ---------------- 数据集选择 ---------------- */
    /**
     * 重建顶栏数据集下拉。
     *
     * 单独抽出来是因为"导入新数据集后立刻切过去"会把下拉卡住：
     * 新数据集此刻还不在 option 列表里，sel.value = 新 id 会静默变成空值，
     * 页面看着像没切换成功。凡是能改变数据集集合的操作，都要调这个。
     */
    function renderDatasetSelect() {
        const sel = $('#dsSelect');
        if (!sel) return;
        sel.innerHTML = state.datasets.map(d =>
            `<option value="${d.dataset_id}">${esc(d.name)} · ${num(d.valid_count)} 条</option>`).join('');
        sel.value = state.dataset_id;
        if (sel.__cpBound) return;
        sel.__cpBound = true;
        sel.addEventListener('change', () => {
            state.dataset_id = sel.value;
            state.datasetMeta = state.datasets.find(d => d.dataset_id === sel.value) || null;
            syncUrl();
            updateSnapshotBadge();
            // 数据覆盖范围跟着换，否则筛选栏会和图表说的是两回事
            if (filterBarApi) filterBarApi.sync();
            document.dispatchEvent(new CustomEvent('cp:dataset', { detail: state.dataset_id }));
        });
    }

    async function loadDatasets() {
        const data = await api('/api/datasets');
        state.datasets = data.datasets;
        if (!state.dataset_id || !state.datasets.some(d => d.dataset_id === state.dataset_id)) {
            state.dataset_id = data.default || (state.datasets[0] && state.datasets[0].dataset_id);
        }
        state.datasetMeta = state.datasets.find(d => d.dataset_id === state.dataset_id) || null;

        renderDatasetSelect();
        updateSnapshotBadge();
        syncUrl();
        return state.datasets;
    }

    /** 顶栏"历史快照·数据截至 X"角标：把"这不是实时数据"写在用户一眼能看到的地方 */
    function updateSnapshotBadge() {
        const b = $('#snapshotBadge');
        const meta = state.datasetMeta;
        if (!b || !meta) return;
        b.textContent = '历史快照 · 数据截至 ' + (meta.time_end || '—');
        b.title = `数据集：${meta.name}\n原始 ${num(meta.raw_count)} 条 / 有效 ${num(meta.valid_count)} 条\n` +
            `最后分析：${meta.analyzed_at || '—'}\n本系统展示历史采集快照，不代表实时全网监测。`;
    }

    async function loadHealth() {
        try {
            const h = await api('/api/health');
            state.health = h;
            const dot = $('#engineDot'), txt = $('#engineText');
            if (dot && txt) {
                const on = h.llm_enabled;
                dot.className = 'dot ' + (on ? 'on' : 'off');
                txt.textContent = on ? 'DeepSeek 工具调用已就绪' : '规则模式（AI 未启用）';
            }
            return h;
        } catch (e) {
            return null;
        }
    }

    /* ---------------- 初始化 ---------------- */
    /* ---------------- 待办角标 ---------------- */
    /**
     * 顶栏待办角标。工单没开、或当前角色无权看工单时，接口返回 can_view=false，
     * 这里直接把元素藏起来——而不是留一个"0"误导用户以为没有待办。
     * 失败静默：角标是锦上添花，它挂了不该影响页面主流程。
     */
    async function loadTicketBadge() {
        const el = $('#ticketBadge');
        const txt = $('#ticketBadgeText');
        if (!el || !txt) return null;
        try {
            const d = await api('/api/tickets/badge');
            if (!d || !d.can_view) { el.style.display = 'none'; return null; }
            const n = d.mine || 0;
            el.style.display = '';
            txt.textContent = n > 99 ? '99+' : String(n);
            el.classList.toggle('is-overdue', !!d.overdue);
            // 数字是"等我这边的单"，提示里再给全量：只写"待处置 3 单"会让人
            // 盯着角标上的 1 问为什么对不上。
            el.title = `我的待办 ${n} 单 · 全部待处置 ${d.pending} 单`
                       + (d.overdue ? ` · 超期 ${d.overdue} 单` : '');
            return d;
        } catch (e) {
            el.style.display = 'none';
            return null;
        }
    }

    /**
     * 页面初始化：主题 → URL 状态 → 数据集 → 健康检查 → 筛选栏 → 触发 cp:ready。
     * @param {{filterBar?: boolean, filterOptions?: any}} [opts]
     */
    async function init(opts) {
        opts = opts || {};
        bindTheme();
        bindNavDrawer();
        readUrl();
        try {
            await loadDatasets();
        } catch (e) {
            toast('数据集加载失败：' + e.message, 'err');
            return;
        }
        loadHealth();
        loadTicketBadge();
        const bar = $('#filterBar');
        if (bar && opts.filterBar !== false) {
            filterBarApi = renderFilterBar(bar, opts.filterOptions);
        }
        document.dispatchEvent(new CustomEvent('cp:ready', { detail: state }));
    }

    global.CP = {
        state, $, $$, esc, num, pct, dateShort, toast, api, qs, syncUrl, filterParams,
        setState, chart, setOption, onChart, resizeCharts, whenVisible, renderSpec, specToOption,
        themeBase, axisCommon,
        renderFilterBar, init, loadHealth, loadTicketBadge, PALETTE, POS, NEU, NEG, SENTIMENT_COLOR, DIMENSIONS, PLATFORMS,
        renderDatasetSelect, loadDatasets,
        getTheme, applyTheme, toggleTheme, themeColors
    };

    // 自动初始化：页面脚本在 DOMContentLoaded 之前注册好 cp:ready 监听即可
    function autoInit() {
        if (global.__CP_NO_AUTOINIT) return;
        init();
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', autoInit);
    } else {
        autoInit();
    }
})(window);
