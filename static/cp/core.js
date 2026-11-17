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

    // 第一个色值必须与 --brand 同源：牌子换了颜色，图表不能还是上一版
    const PALETTE = ['#3358E8', '#14B8A6', '#94A3B8', '#F59E0B', '#8B5CF6', '#06B6D4', '#E5484D', '#475569'];
    const SENTIMENT_COLOR = { '正面': '#16A34A', '中性': '#94A3B8', '负面': '#E5484D' };
    const POS = '#16A34A', NEU = '#94A3B8', NEG = '#E5484D';

    const state = {
        dataset_id: null,
        datasets: [],
        datasetMeta: null,
        filters: { platform: [], dimension: [], sentiment: [], date_from: '', date_to: '', keyword: '' },
        health: null
    };

    // renderFilterBar 会把内部那份「清除筛选」实现注册到这里；setState 生成的空态
    // 「清除筛选」按钮直接复用它，避免另写第二套清空逻辑（见 setState / renderFilterBar）。
    /** @type {(() => void) | null} */
    let activeClearFilters = null;

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

    /* ---------------- 工作小时：全站唯一的时长写法 ----------------
     * SLA 的单位是「工作小时」（每工作日 7 小时）。裸写 48h 会被读成 2 个自然日，
     * 而 48 工作小时≈9.6 个自然日——差 4.8 倍、方向还相反，这正是本产品唯一的
     * 口径难点。所以业务文案里「小时」不许手拼：凡是说一个时长，都过这里。
     */
    /**
     * @param {any} v 工作小时数
     * @returns {string} 例如 "48 工作小时" / "66.6 工作小时"；null → "—"
     */
    function workHours(v) {
        if (v == null || v === '') return '—';
        const n = Number(v);
        if (!isFinite(n)) return String(v);
        return (Math.round(n * 10) / 10).toFixed(1).replace(/\.0$/, '') + ' 工作小时';
    }

    /**
     * 工作小时 → 工作天数。perDay 由接口给（`meta.sla.workday_hours`），**别写死 7**：
     * 学校的作息一改，写死的折算就会开始说谎。
     * @param {any} v 工作小时数
     * @param {any} perDay 每工作日几小时
     * @returns {string} 例如 "9.5 个工作日"；参数不全时返回空串
     */
    function workdays(v, perDay) {
        const n = Number(v), d = Number(perDay);
        if (!isFinite(n) || !isFinite(d) || d <= 0) return '';
        return (Math.round(n / d * 10) / 10).toFixed(1).replace(/\.0$/, '') + ' 个工作日';
    }

    /**
     * 按时归档率拆成「大数字 + 脚注」两半。样本不足时两半都是「说清楚为什么不算」，
     * 而不是一个四舍五入后的漂亮百分比。
     *
     * 为什么要有这一层：/departments 用 onTimeFoot() 写在脚注里，/tickets 把它做成
     * 一张独立的 KPI 卡（百分比当大数字、分子分母当脚注）。两处必须同源，
     * 否则同一个 2/2 会在一页写「不换算百分比」、在另一页写「= 100%」。
     * @param {any} ot {numerator, denominator, rate}
     * @param {number} [min] 换算百分比所需的最小分母
     * @returns {{value: string, note: string}} value 给大数字位，note 给脚注位
     */
    function onTimeParts(ot, min) {
        const den = (ot && ot.denominator) || 0;
        const m = min || 10;
        const n = (ot && ot.numerator != null ? ot.numerator : 0);
        if (!den) return { value: '—', note: '' };
        if (den < m) return { value: '—', note: `按时归档 ${n}/${den} · 样本不足 ${m} 单，不换算百分比` };
        return { value: pct(ot.rate), note: `按时归档 ${n}/${den}` };
    }

    /**
     * 按时归档率的脚注。样本 < 10 单只说分子分母、不换算百分比。
     * 研判页与工单页共用同一句：同一个 2/2 在一页写「不换算百分比」、在另一页写「= 100%」，
     * 用户会连这两个数一起不信。
     * @param {any} ot {numerator, denominator, rate}
     * @param {number} [min] 换算百分比所需的最小分母
     * @returns {string}
     */
    function onTimeFoot(ot, min) {
        const p = onTimeParts(ot, min);
        if (!p.note) return '';
        return p.value === '—' ? p.note : `${p.note} = ${p.value}`;
    }

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

    /* ---------------- 模态：焦点进出与 Esc ----------------
     * 全站有 4 处手写模态（改密、重命名数据集、新建工单、工单操作/删除确认），
     * 各自只管显示隐藏，于是三个坑一个不落：打开后焦点还留在背景按钮上、
     * Tab 能一路走到页面最底下、Esc 关不掉（或关掉了但焦点掉到 body）。
     * 这里统一处理，调用方只管内容与"关掉之后做什么"。
     */
    const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),'
        + 'select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
    const modalState = new WeakMap();   // mask -> { back: Element|null, onEsc: Function|null }

    /* 弹窗打开时锁住背景滚动。
       以前只在视觉上盖了一层遮罩：滚轮还能把底下的页面滚走，关掉后
       用户发现自己停在别处，「关了却不知道回到哪」。
       用计数而不是布尔：确认框可以叠在另一个弹窗上，先关的那个不能解锁。 */
    let scrollLocks = 0;
    function lockScroll() {
        scrollLocks += 1;
        if (scrollLocks === 1) document.documentElement.classList.add('is-modal-open');
    }
    function unlockScroll() {
        scrollLocks = Math.max(0, scrollLocks - 1);
        if (!scrollLocks) document.documentElement.classList.remove('is-modal-open');
    }

    /** @param {HTMLElement} mask @param {{onEsc?:Function, back?:HTMLElement}} [opts] */
    function openModal(mask, opts) {
        if (!mask || modalState.has(mask)) return;   // 已经开着：不重复抢焦点
        // back：关掉后焦点该回哪。默认记当前焦点，但从账户下拉里打开改密弹窗时，
        // 那颗菜单项会先被收起，焦点已经不在它身上了，所以要显式传触发按钮。
        const back = (opts && opts.back) || /** @type {Element|null} */ (document.activeElement);
        mask.hidden = false;
        lockScroll();
        const onEsc = (opts && opts.onEsc) || null;
        const onKey = (e) => {
            if (e.key === 'Escape') {
                e.stopPropagation();
                if (onEsc) onEsc(); else closeModal(mask);
                return;
            }
            if (e.key !== 'Tab') return;
            const items = /** @type {HTMLElement[]} */ (Array.from(mask.querySelectorAll(FOCUSABLE)))
                .filter(el => el.offsetParent !== null || el === document.activeElement);
            if (!items.length) return;
            const first = items[0], last = items[items.length - 1];
            if (e.shiftKey && (document.activeElement === first || !mask.contains(document.activeElement))) {
                e.preventDefault(); last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault(); first.focus();
            }
        };
        mask.addEventListener('keydown', onKey);
        modalState.set(mask, { back, onKey });
        // 首个可见输入框优先：打开就想填，比先落在"取消"上少一次 Tab。
        // 要排除 [hidden] 元素——改密弹窗里那个用于密码管理器的隐藏账号字段
        // 是 type=text，光按 type 排除会选中它，焦点就"送进空气里"了。
        const firstField = /** @type {HTMLElement|undefined} */ (
            Array.from(mask.querySelectorAll('input,select,textarea'))
                .find(el => !(/** @type {HTMLElement} */ (el)).hidden
                    && /** @type {HTMLElement} */ (el).offsetParent !== null))
            || /** @type {HTMLElement|null} */ (mask.querySelector(FOCUSABLE));
        if (firstField) setTimeout(() => firstField.focus(), 30);
    }

    /** @param {HTMLElement} mask */
    function closeModal(mask) {
        if (!mask) return;
        const st = modalState.get(mask);
        mask.hidden = true;
        if (st) {
            mask.removeEventListener('keydown', st.onKey);
            modalState.delete(mask);
            unlockScroll();
            // 焦点回到打开它的那颗按钮上；那颗按钮若不在了（例如列表重绘）就不硬塞回 body
            if (st.back && st.back.isConnected && typeof st.back.focus === 'function') {
                st.back.focus();
            }
        }
    }

    /* ---------------- 危险操作确认又 ----------------
     * 原生 confirm() 是浏览器画的一个灰盒子：和"删除工单"那种定制弹窗摆在
     * 同一个产品里，用户会以为"弹得丑的那个大概没那么严重"。危险操作只有一套样子。
     *
     * 默认焦点落在「取消」上（openModal 会把焦点给第一个可聚焦元素）——
     * 不可逆的动作不该因为手快按了回车就执行。
     *
     * @param {{title?: string, sub?: string, note?: string, subHtml?: string,
     *          noteHtml?: string, okLabel?: string, cancelLabel?: string, danger?: boolean}} opt
     * @returns {Promise<boolean>}
     */
    function confirmModal(opt) {
        const o = opt || {};
        const danger = o.danger !== false;
        // 110：sub / note 默认按纯文本转义（esc，即 CP.esc）；只有调用方显式传
        // subHtml / noteHtml 才按富文本插入。以前 o.sub / o.note 直接拼进 innerHTML，
        // 任何拼了用户输入的调用方都是注入面；需要 <b> 强调的确认框改传 subHtml。
        const subBlock = o.subHtml != null
            ? `<div class="card-sub">${o.subHtml}</div>`
            : (o.sub ? `<div class="card-sub">${esc(o.sub)}</div>` : '');
        const noteBlock = o.noteHtml != null
            ? `<div class="note ${danger ? 'note-warn' : 'note-muted'}" style="margin:10px 0;">${o.noteHtml}</div>`
            : (o.note ? `<div class="note ${danger ? 'note-warn' : 'note-muted'}" style="margin:10px 0;">${esc(o.note)}</div>` : '');
        return new Promise(resolve => {
            const mask = document.createElement('div');
            mask.className = 'modal-mask';
            mask.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="cpCfmTitle">
                <h2 class="modal-title" id="cpCfmTitle">${esc(o.title || '确认操作')}</h2>
                ${subBlock}
                ${noteBlock}
                <div class="tk-modal-foot" style="margin-top:14px;">
                    <button class="btn btn-sm btn-ghost" data-cancel type="button">${esc(o.cancelLabel || '取消')}</button>
                    <button class="btn btn-sm ${danger ? 'btn-danger-solid' : 'btn-primary'}"
                            data-ok type="button">${esc(o.okLabel || '确认')}</button>
                </div>
            </div>`;
            document.body.appendChild(mask);
            let done = false;
            const finish = v => {
                if (done) return;
                done = true;
                closeModal(mask);
                mask.remove();
                resolve(v);
            };
            openModal(mask, { onEsc: () => finish(false) });
            mask.addEventListener('click', e => { if (e.target === mask) finish(false); });
            /** @type {any} */ (mask.querySelector('[data-cancel]')).addEventListener('click', () => finish(false));
            /** @type {any} */ (mask.querySelector('[data-ok]')).addEventListener('click', () => finish(true));
        });
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
        // 导航链接只带筛选，不带分页：跳到别的页面时页码没有意义。
        // 这条是演示里"口径双向流动"的那一半（驾驶舱点筛选 → 点工作台 → Agent 只在范围内答），
        // 所以筛选要跟着走；被带过去的条件会在落地页筛选栏里亮着，
        // 不想继承时按紧挨关键词的那枚「清除筛选」一次清干净。
        // 二级子项（.nav-child）也必须一起更新。它们以前不在这个选择器里，
        // 于是切完数据集点「主题热度分析」这类子菜单，URL 里没有 dataset_id，
        // 后端只能回落到默认数据集——页面看着像"自己跳回全量历史样本了"。
        const navQuery = qs();
        $$('.nav-item, .nav-child').forEach(a => {
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
        // 下面三种都会整块覆盖容器内容。如果这块容器上挂过图表实例，
        // 必须先把实例释放掉：canvas 被 innerHTML 摘走后实例还在缓存里，
        // 下次 setOption 时 ECharts 仍去操作那个已经没有父节点的 DOM，
        // 直接抛 "Cannot read properties of null (reading 'removeChild')"。
        if (kind === 'loading' || kind === 'empty' || kind === 'error') releaseChart(el);
        // 读屏用户听不到"正在计算"和"算完了"：把忙碌状态挂在容器上，
        // 换了内容再摘掉，列表/图表的刷新才有可感知的起止。
        if (kind === 'loading') {
            el.setAttribute('aria-busy', 'true');
            el.innerHTML = '<div class="state"><div class="spinner"></div><div>正在计算…</div></div>';
        } else if (kind === 'empty') {
            el.removeAttribute('aria-busy');
            // 空态最常见的解法就是"刚才那撮筛选太窄"：给一枚直达清除的按钮，
            // 复用 renderFilterBar 注册进来的那份清空实现（不另写第二套）。
            const f = state.filters;
            const hasFilter = (f.platform && f.platform.length) || (f.dimension && f.dimension.length)
                || (f.sentiment && f.sentiment.length) || !!f.date_from || !!f.date_to || !!f.keyword;
            const clearBtn = (activeClearFilters && hasFilter)
                ? '<button class="btn btn-sm clear-filters" type="button" data-clear-filters>清除筛选</button>'
                : '';
            el.innerHTML = '<div class="state"><div class="state-title">当前筛选范围内没有数据</div>' +
                '<div>' + esc(msg || '可以放宽时间范围或切换数据集后再看。') + '</div>' +
                clearBtn + '</div>';
            const cf = el.querySelector('[data-clear-filters]');
            if (cf && activeClearFilters) cf.addEventListener('click', activeClearFilters);
            announce('当前筛选范围内没有数据');
        } else if (kind === 'error') {
            el.removeAttribute('aria-busy');
            el.innerHTML = '<div class="state state-error"><div class="state-title">加载失败</div>' +
                '<div>' + esc(msg || '请稍后重试') + '</div>' +
                '<button class="btn btn-sm" data-retry>重试</button></div>';
            const btn = el.querySelector('[data-retry]');
            if (btn && onRetry) btn.addEventListener('click', onRetry);
            announce('加载失败，' + (msg || '请稍后重试'));
        }
    }

    /**
     * 把一句话送进常驻的 live region。
     * toast 本身会被播报，但它三秒后消失——"表格刷新完了""AI 答完了"这类
     * 没有 toast 的异步结果，读屏用户此前听不到任何收尾。
     * @param {string} text
     * @param {'polite'|'assertive'} [level]
     */
    function announce(text, level) {
        if (!text) return;
        const el = document.getElementById(level === 'assertive' ? 'cpLiveAssertive' : 'cpLive');
        if (!el) return;
        el.textContent = '';
        // 同一句话连报两次需要一次空档，否则 DOM 文本没变化，读屏不会重读
        setTimeout(() => { el.textContent = text; }, 30);
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
    /**
     * 图表字号。canvas 读不到 CSS 变量，所以按 --fs-* 的同一套档位镜像一份。
     * 下限 12px：CSS 侧的半像素档已经清掉了，但 ECharts 配置里还散着
     * 10 / 10.5 / 11.5，投影和 1366 屏上最先糊掉的就是这些轴标签。
     * @type {{micro:number, xs:number, sm:number, base:number}}
     */
    const CHART_FS = { micro: 12, xs: 12, sm: 13, base: 14 };

    /* 负面段的纹理填充（色盲友好）：红/绿在红绿色盲眼里几乎同亮，堆叠柱只看颜色
       分不出"负面"占多厚。给负面段加 45° 细斜纹，形状与颜色双编码。
       只作用于负面——正面/中性各加一种纹理，图例就变成了三块花布。 */
    const NEG_DECAL = {
        symbol: 'rect', symbolSize: 1, rotation: -Math.PI / 4,
        dashArrayX: [1, 0], dashArrayY: [2, 5],
        color: 'rgba(255, 255, 255, .55)'
    };

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

    /* ---------------- 术语表弹窗 ----------------
     * base.html 里的 #glossaryBtn / #glossaryModal 由模板组提供。这里只负责开关，
     * Esc / Tab trap / 焦点回归全部复用 openModal-closeModal，不另写一套模态。
     * DOM 还没铺到时静默跳过（模板组与本文件并行改动）。 */
    function bindGlossary() {
        const btn = $('#glossaryBtn'), mask = $('#glossaryModal');
        if (!btn || !mask) return;
        btn.addEventListener('click', () => openModal(mask, { back: /** @type {HTMLElement} */ (btn) }));
        mask.addEventListener('click', e => {
            if (e.target === mask) { closeModal(mask); return; }
            if (/** @type {HTMLElement} */ (e.target).closest('[data-act="cancel"]')) closeModal(mask);
        });
    }

    /* 顶栏实际高度写进 --topbar-h：页内 sticky 的动作条要贴在它下面。
       写死像素在 720 / 1100 断点都会错位（1280 下顶栏 140px，390 下是两行）。
       resize 与字体加载都会改高度，统一在这一处同步。 */
    function watchTopbarHeight() {
        const bar = document.querySelector('.topbar');
        if (!bar) return;
        const sync = () => document.documentElement.style.setProperty(
            '--topbar-h', /** @type {HTMLElement} */ (bar).offsetHeight + 'px');
        sync();
        window.addEventListener('resize', sync);
        // 只监听 resize 不够：顶栏高度还会被"待办徽标出现""副标题换行""字体加载"改变，
        // 那时 sync 不跑，页内 sticky 就会贴着旧高度（复评实测 /agent 读到 86、实际 140）。
        if (window.ResizeObserver) {
            new ResizeObserver(sync).observe(/** @type {Element} */ (bar));
        }
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(sync).catch(() => {});
        // 徽标是异步回来的，出现后顶栏可能变高：补两次。
        setTimeout(sync, 600);
        setTimeout(sync, 2000);
    }

    /* ---------------- 顶栏「更多」 ----------------
     * 重命名 / 主题 / 术语收进一个原生 <details>。三件事都是低频动作，
     * 常驻三个图标位等于让它们和"数据集"抢同一片视觉，其中「重命名」还紧挨
     * 数据集下拉，读起来像切数据集的第二个按钮。
     *
     * 收起时必须处理焦点：菜单项就在被关掉的容器里，focus 留在里面既看不见、
     * 也 Tab 不到（WCAG 2.4.7）。所以先判断焦点是否还在菜单里，是就先交回按钮。
     *
     * 主题是就地生效的开关，点完就收起；重命名与术语会开弹窗，弹窗关掉时
     * 焦点要回到它们身上——那一刻容器必须还开着（见下面的 id 判断）。
     */
    function bindTopMore() {
        const box = /** @type {HTMLDetailsElement|null} */ ($('#topMore'));
        if (!box) return;
        const summary = /** @type {HTMLElement|null} */ (box.querySelector('summary'));
        const close = () => {
            if (!box.open) return;
            if (summary && box.contains(document.activeElement)) summary.focus();
            box.open = false;
        };
        box.addEventListener('click', e => {
            const item = /** @type {HTMLElement} */ (e.target).closest('.top-more-item');
            if (!item) return;
            if (item.id === 'dsRename' || item.id === 'glossaryBtn') return;   // 弹窗会接管焦点
            close();
        });
        document.addEventListener('click', e => {
            if (!box.open) return;
            if (!box.contains(/** @type {Node} */ (e.target))) close();
        });
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape' && box.open) close();
        });
    }

    /* ---------------- 移动端抽屉导航 ---------------- */
    function bindNavDrawer() {
        const toggle = $('#navToggle'), mask = $('#navMask'), side = $('#sidebar');
        if (!toggle) return;
        /* 抽屉是靠 translateX(-100%) 移出去的，DOM 里还在、Tab 序里也还在：
           390 下连按 12 次 Tab 全都落在屏外的链接上，焦点环一个也看不见。
           桌面端侧栏是常驻的，所以 inert 只在窄屏关闭态挂。 */
        const syncInert = () => {
            if (!side) return;
            const narrow = window.matchMedia('(max-width: 720px)').matches;
            const closed = !document.body.classList.contains('side-open');
            if (narrow && closed) side.setAttribute('inert', '');
            else side.removeAttribute('inert');
        };
        const setOpen = open => {
            document.body.classList.toggle('side-open', open);
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            syncInert();
        };
        syncInert();
        window.addEventListener('resize', syncInert);
        toggle.addEventListener('click', () => setOpen(!document.body.classList.contains('side-open')));
        if (mask) mask.addEventListener('click', () => setOpen(false));
        document.addEventListener('keydown', e => { if (e.key === 'Escape') setOpen(false); });
        // 点了导航就关抽屉，否则跳转后遮罩还盖着
        $$('.nav-item, .nav-child').forEach(a => a.addEventListener('click', () => setOpen(false)));
    }

    /* ---------------- 侧栏子项折叠 ---------------- */
    /* 第 10 轮 A2：「数据洞察中心」下面还有 3 个视图，折叠后父项上没有任何信号
       （实测 parentAria: None）。三角是独立按钮而不是塞进 <a>——交互元素嵌套会让
       键盘出现两个焦点，点标题仍然进父页、点三角只展开。aria-expanded 与
       .is-open 同步，aria-controls 指向真正的子项列表。 */
    function bindNavGroups() {
        $$('[data-nav-toggle]').forEach(btn => {
            btn.addEventListener('click', () => {
                const kids = document.getElementById(btn.getAttribute('aria-controls') || '');
                if (!kids) return;
                const open = kids.classList.toggle('is-open');
                btn.setAttribute('aria-expanded', open ? 'true' : 'false');
                const name = btn.getAttribute('data-nav-name') || '';
                btn.setAttribute('aria-label', (open ? '收起' : '展开') + name + '的子项');
            });
        });
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

    /**
     * 摘掉某个容器上的图表实例。
     * 容器被 innerHTML 覆盖（loading / error / empty 占位、整块重渲染）后，
     * 里面的 canvas 已脱离文档，但实例还留在两张缓存表里。
     * 不释放的话，下一次 setOption 会让 ECharts 继续操作那个"没有父节点"的 DOM。
     * @param {HTMLElement|null} el
     */
    function releaseChart(el) {
        const inst = charts.get(el);
        if (!inst) return;
        charts.delete(el);
        liveCharts.delete(inst);
        try { inst.dispose(); } catch (e) { /* 已经坏掉的实例忽略 */ }
    }

    /* ---------------- 图表惰性初始化 ---------------- */
    // 洞察页有 11 张图，ECharts 每张 init 约 30–80ms：全部在首屏建完 ≈ 半秒卡顿，
    // 而其中 8 张根本不在第一屏。这里只把"可见"的图建出来，其余的等滚到附近再建。
    // 用 __CP_NO_LAZY 可以在同一台机器上做 A/B 对比（性能数据就是这么测的）。
    // 按初始视口算一次的常量（IntersectionObserver 的 rootMargin 创建时固定，中途不重算）：
    // ≥1440 放宽到 1200px，让首两屏的图直接建好；1100–1440 取 800——1280×800 上
    // 1200 会把 11 张图里的 9 张一次性 init（约 0.5s 主线程占用），而第 4 屏以后的
    // 图本来就没人马上要看；窄屏仍提前 160px。
    const LAZY_MARGIN = window.innerWidth >= 1440 ? 1200
        : (window.innerWidth >= 1100 ? 800 : 160);
    const pendingOptions = new WeakMap(); // el -> option（等可见）
    const pendingHooks = new WeakMap();   // el -> [(inst) => void]（等可见再绑交互）
    const visibleCbs = new WeakMap();     // el -> [() => void]（可见时执行一次，与实例无关）
    // 图表状态机：按容器自己记录「是否尝试渲染过 / 是否真失败」，
    // 供 stalled 判定使用（不再用全局一次性 setTimeout 给所有空图一刀切加类）。
    const chartAttempted = new WeakMap(); // el -> true：调用过 setOption
    const chartFailed = new WeakMap();    // el -> true：渲染真失败（抛错 / ECharts 缺失）
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
        // 同步撤掉三个状态类；本次渲染若真失败，applyOption 已标 is-failed，
        // 这里不能把失败标记一起冲掉。
        if (!chartFailed.has(el)) {
            clearStallTimer(el);
            el.classList.remove('is-stalled', 'is-lazy-wait', 'is-failed');
        }
        const hooks = pendingHooks.get(el) || [];
        pendingHooks.delete(el);
        if (hooks.length) {
            const inst = charts.get(el);
            if (inst) hooks.forEach(h => { try { h(inst); } catch (e) { /* 交互绑定失败不影响显示 */ } });
        }
        const cbs = visibleCbs.get(el) || [];
        visibleCbs.delete(el);
        cbs.forEach(cb => { try { cb(); } catch (e) { /* 回调失败不影响其它图表 */ } });
        syncShowAllChartsBtn();   // 队列少一张 → 按钮随之收起
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
                textStyle: { color: c.ink, fontSize: CHART_FS.xs },
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
            axisLabel: { color: c.ink4, fontSize: CHART_FS.micro },
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
            // 容器里可能还留着 setState 放的 loading/error/empty 占位，
            // 不清掉的话占位文字会和 canvas 叠在一起
            if (el.querySelector('.state')) el.innerHTML = '';
            inst = echarts.init(el, null, { renderer: 'canvas' });
            charts.set(el, inst);
            liveCharts.add(inst);
            // 任何一张图在建立的那一刻就拿到名字。以前靠各页面自己记得调
            // describeChart，漏掉的页面（情感页、时间页共 8 张）对读屏就是 8 块空白。
            labelChart(el);
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
        let inst = null;
        try { inst = chart(el); } catch (e) { inst = null; }
        if (!inst) {
            // ECharts 缺失 / init 抛错：这是真失败，不是「还没滚到」
            markChartFailed(el);
            return null;
        }
        try {
            inst.setOption(option, true);
            setTimeout(() => { try { inst.resize(); } catch (e) {} }, 30);
        } catch (e) {
            // setOption 抛错：真失败
            markChartFailed(el);
            return null;
        }
        // 渲染成功：撤掉一切降级标记（含上一次的失败态）
        chartFailed.delete(el);
        clearStallTimer(el);
        setChartState(el, 'ok');
        return inst;
    }

    function setOption(el, option) {
        if (!el) return null;
        chartAttempted.set(el, true);   // 记一笔：这张图调用过 setOption
        // 有实例就直接画；没有实例且还没滚到附近，就先存着，别为了"看不见的图"卡住首屏
        if (!charts.has(el) && lazyEnabled() && !nearViewport(el)) {
            pendingOptions.set(el, option);
            observeLazy(el);
            // 纯懒加载（未进视口）：中性等待态，不是「未加载」警告
            observeChartState(el);
            setChartState(el, 'lazy-wait');
            syncShowAllChartsBtn();   // 队列里多一张 → 亮出「显示全部图表」
            return null;
        }
        const inst = applyOption(el, option);   // 内部按成功 / 失败维护状态类
        observeChartState(el);                  // 纳入按容器观察（进视口 / 空态判定）
        return inst;
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

    /**
     * 给图表容器一句人话。canvas 里的东西读屏软件一个像素也拿不到，
     * 没有这句，60% 的页面内容对屏幕阅读器就是"空白"。
     * @param {HTMLElement|null} el
     * @param {string} text 用页面自己的数字拼，别写"一张柱状图"
     */
    function describeChart(el, text) {
        if (!el || !text) return;
        el.setAttribute('role', 'img');
        el.setAttribute('aria-label', text);
    }

    /**
     * 兜底命名：显式写的 aria-label 不覆盖（那些句子带真实数字，比标题具体），
     * 没写的用所在卡片的标题 + 副标题拼一句。卡片标题本身是可见的，
     * 所以读到这里时用户手上已经有同一句话，不算新信息、也不会误导。
     * @param {HTMLElement} el
     */
    function labelChart(el) {
        if (!el) return;
        if (!el.getAttribute('role')) el.setAttribute('role', 'img');
        if (el.getAttribute('aria-label')) return;
        const card = el.closest('.card');
        const t = card && card.querySelector('.card-title');
        const s = card && card.querySelector('.card-sub');
        const text = [t && t.textContent.trim(), s && s.textContent.trim()]
            .filter(Boolean).join('：');
        if (text) el.setAttribute('aria-label', text);
    }

    /** 只 resize 已经建好的图表；绝不触发新的 init */
    function resizeCharts() {
        liveCharts.forEach(i => { try { i.resize(); } catch (e) { /* 已销毁的实例忽略 */ } });
    }

    /* 打印与整页截图之前，把还没画的图一次性建出来。
       懒加载靠 IntersectionObserver，而打印/截图不会滚动——导出的每一张图
       都写着"图表加载中"。这是答辩前导出 PDF 时最容易被看见的一处破绽。 */
    function flushAllCharts() {
        // pendingOptions 是 WeakMap，不能遍历——按 DOM 反向找。
        document.querySelectorAll('.chart').forEach(el => {
            if (!pendingOptions.has(el)) return;
            try { flushLazy(el); } catch (e) { /* 单张失败不拖累其它 */ }
        });
        resizeCharts();
        syncShowAllChartsBtn();   // flush 完队列清空，按钮随之收起
    }
    window.addEventListener('beforeprint', () => {
        flushAllCharts();
        renderPrintScope();
    });
    // 离开页面（含 bfcache）时补齐未建的图，返回时不会留一片「图表加载中」。
    // 只 flush、不写打印口径：renderPrintScope 仅服务打印/导出，页面隐藏时往 .content 注入口径行
    // 既无意义又会污染 DOM；flush 本身幂等、无网络、不改页面文案。
    window.addEventListener('pagehide', () => { flushAllCharts(); });

    /* ---------------- 「显示全部图表」就地入口 ----------------
     * 桌面把 LAZY_MARGIN 放宽后首两屏的图会直接建好，但滚不到 / 打印 / 整页截图
     * 仍需要一个就地入口把剩余待渲染图一次性建出来。按钮只在确有 pendingOptions
     * 待渲染图表时出现，点一下 flush 完自行收起；样式（≥44px 点击区）由 G2 提供。 */
    function hasPendingCharts() {
        return $$('.chart').some(el => pendingOptions.has(el));
    }
    /**
     * 把按钮挪到「第一张待渲染图表所在的卡片」之前。
     * 以前是 prepend 到 .content 顶部：390 实测出现在 y=238，比流程说明和面包屑
     * 还早，位置离它真正影响的图表有两千多像素——按下之前完全不知道会变哪一屏。
     * 按钮还没出现（没有 pending）时就先留在原地，等 sync 时再挪。
     */
    function placeShowAllChartsBtn(btn) {
        if (!btn) return;
        const first = $$('.chart').find(el => pendingOptions.has(el));
        if (!first) return;
        const card = first.closest('.card');
        // 卡片在栅格 / 弹性行里时，插到卡片前面会变成多出来的一个格（把一行挤成两行）。
        // 那种情况下退一步：插到图表本体之前，按钮仍然紧挨着它要影响的图。
        const parent = card && card.parentNode;
        const rowish = parent && /grid|flex/.test(getComputedStyle(parent).display);
        if (card && parent && !rowish) {
            if (card.previousElementSibling !== btn) parent.insertBefore(btn, card);
            return;
        }
        const host = first.parentNode;
        if (host && first.previousElementSibling !== btn) host.insertBefore(btn, first);
    }
    function syncShowAllChartsBtn() {
        const btn = document.getElementById('cpShowAllCharts');
        if (!btn) return;
        const on = hasPendingCharts();
        if (on) placeShowAllChartsBtn(btn);
        btn.hidden = !on;
    }
    function mountShowAllChartsBtn() {
        const host = document.querySelector('.content');
        if (!host || document.getElementById('cpShowAllCharts')) return;
        const btn = document.createElement('button');
        btn.id = 'cpShowAllCharts';
        btn.type = 'button';
        btn.className = 'btn btn-sm';
        btn.textContent = '显示全部图表';
        btn.hidden = true;
        host.prepend(btn);
        btn.addEventListener('click', () => {
            flushAllCharts();     // 与 CP.flushAllCharts 是同一个已导出函数
            btn.hidden = true;    // 点完自行收起
            // 一次性建十几张图，屏幕会突然长出内容；不给一句话，读屏用户只会
            // 听到"没什么变化"。用常驻 live region 播报一次。
            announce('已显示全部图表');
        });
        syncShowAllChartsBtn();
    }

    /* ---------------- 空图表的按容器状态判定 ----------------
     * 以前是「8 秒 / 10 秒后给所有 .chart:empty 一刀切加 is-stalled」：
     * 真失败和"还没滚到"长得一模一样，读屏用户还全程静默。
     * 现在按每个容器自己的状态分三类：
     *   · is-lazy-wait  纯懒加载（pendingOptions 里、还没进视口）—— 中性，不警告；
     *   · is-stalled     已调用过 setOption / 已进视口却仍空 —— 中性提示；
     *   · is-failed      真失败（setOption 抛错 / ECharts 缺失 / 接口报错）—— 警告 + 可重试。
     * 状态只在真正变化时 announce 一次，避免加载期反复打断读屏。 */
    let stallObserver = null;
    let stateWatchStarted = false;

    /** 三个状态类互斥；只在状态真正变化时才改 DOM 并播报一次。 */
    function setChartState(el, state) {
        if (!el) return;
        const cls = state === 'lazy-wait' ? 'is-lazy-wait'
            : state === 'stalled' ? 'is-stalled'
            : state === 'failed' ? 'is-failed' : '';
        const cur = el.classList.contains('is-lazy-wait') ? 'is-lazy-wait'
            : el.classList.contains('is-stalled') ? 'is-stalled'
            : el.classList.contains('is-failed') ? 'is-failed' : '';
        if (cur === cls) return;
        el.classList.remove('is-lazy-wait', 'is-stalled', 'is-failed');
        if (cls) el.classList.add(cls);
        if (cls === 'is-failed') announce('有图表未加载，请刷新页面重试', 'assertive');
        else if (cls === 'is-stalled') announce('图表仍在加载');
    }

    function clearStallTimer(el) {
        if (el && el.__cpStallTimer) {
            clearTimeout(el.__cpStallTimer);
            el.__cpStallTimer = null;
        }
    }

    /** 真失败的唯一入口：applyOption 内部调用，页面接口 catch 里也可调 CP.markChartFailed(el)。 */
    function markChartFailed(el) {
        if (!el) return;
        clearStallTimer(el);
        chartFailed.set(el, true);
        setChartState(el, 'failed');
    }

    /** 与 CSS 的 .chart:empty 对齐：没有任何子节点（含文本）才算"还空着"。 */
    function chartIsEmpty(el) {
        return el.matches(':empty');
    }

    /** 综合容器自身状态，落到正确的类上。 */
    function refreshChartState(el) {
        if (!el || !el.isConnected) return;
        if (chartFailed.has(el)) { setChartState(el, 'failed'); return; }
        if (!chartIsEmpty(el)) { setChartState(el, 'ok'); return; }  // 已有 canvas 或三态占位
        // 词云走 whenVisible / visibleCbs，不进 pendingOptions；两路等待都要归到
        // 「还没滚到」，否则折线以下那两张会一直挂着无状态类的「图表加载中」。
        // 还有些插件既不在 pendingOptions 也不在 visibleCbs：只要容器空着且还在
        // 视口外，就按「滚到才画」如实标注，绝不写成「未加载」。
        if (pendingOptions.has(el) || visibleCbs.has(el) || !nearViewport(el)) {
            setChartState(el, nearViewport(el) ? 'stalled' : 'lazy-wait');
            return;
        }
        // 空、非懒加载、已调用过 setOption 或已进视口，却仍未渲染出来
        if (chartAttempted.has(el)) setChartState(el, 'stalled');
    }

    /** 进视口后留一点加载时间再判定，避免接口还在返回时就闪 stalled / 播报。 */
    function scheduleStallCheck(el) {
        clearStallTimer(el);
        if (!chartIsEmpty(el)) { setChartState(el, 'ok'); return; }
        el.__cpStallTimer = setTimeout(() => {
            el.__cpStallTimer = null;
            refreshChartState(el);
        }, 1200);
    }

    /** 把单个容器纳入 IntersectionObserver，观察它「进没进视口」（重复调用幂等）。 */
    function observeChartState(el) {
        if (el.__cpStateObserved) return;
        if (!stallObserver) {
            if (typeof IntersectionObserver === 'undefined') return;
            stallObserver = new IntersectionObserver(entries => {
                entries.forEach(en => {
                    const el = en.target;
                    if (en.isIntersecting) {
                        if (pendingOptions.has(el) || visibleCbs.has(el)) return;  // 交给 lazyObserver / flushLazy，不抢标
                        scheduleStallCheck(el);
                    } else {
                        refreshChartState(el);   // 视口外的空容器一律如实标「向下滚动以加载图表」
                    }
                });
            }, { rootMargin: LAZY_MARGIN + 'px' });
        }
        stallObserver.observe(el);
        el.__cpStateObserved = true;
    }

    function scanChartStates() {
        $$('.chart').forEach(observeChartState);
    }

    /** 启动按容器观察；init() 经 watchChartStall() 调一次，后续新增的图由 MutationObserver 补进。 */
    function startChartStateWatch() {
        if (stateWatchStarted) return;
        stateWatchStarted = true;
        scanChartStates();
        if (typeof MutationObserver !== 'undefined') {
            new MutationObserver(() => scanChartStates())
                .observe(document.body, { childList: true, subtree: true });
        }
    }

    /**
     * 打印前在内容区顶部写一行"这批数据是什么口径"。
     * `@media print` 会把 #filterBar 整个隐掉，导出的 PDF 因此看不出筛选条件；
     * 而 PDF 最常被翻到的第一句就是"这是哪一批数据"。
     */
    function renderPrintScope() {
        const host = document.querySelector('.content');
        if (!host) return;
        let el = document.getElementById('printScope');
        if (!el) {
            el = document.createElement('div');
            el.id = 'printScope';
            host.prepend(el);
        }
        // 口径串统一由 scopeLine() 提供，行为与原先逐条拼接完全一致
        el.textContent = '数据口径：' + scopeLine() +
            ' · 情感与主题由关键词规则标注，主题为多标签计数，合计可大于评论总数';
    }

    /**
     * 当前数据集的人话名字（取不到返回空串）。
     * 单独抽出来是因为「数据集」和「筛选条件」是两件事：前者由顶栏下拉决定，
     * 后者由筛选栏决定。混在一句话里会直接说谎——见 scopeText() 的注释。
     * @returns {string}
     */
    function datasetLabel() {
        return (state.datasetMeta && state.datasetMeta.name) ? String(state.datasetMeta.name) : '';
    }

    /**
     * 当前**筛选**的人话串（不含数据集名、不含换行）。没有任何筛选时返回空串。
     *
     * 第 10 轮之前这里把 `state.datasetMeta.name` 也拼进来，于是 .ai-basis 写
     * 「当前筛选范围：2022-2026」——而 `2022-2026` 是数据集名（/api/datasets 的
     * `name`），筛选栏里根本找不到这个值。用户照着这行字去找「时间筛选」，
     * 找不到就会以为筛选没生效。现在数据集名单独说（见 scopeLine / aiBasis），
     * 这一串只讲筛选。
     * @returns {string}
     */
    function scopeText() {
        const f = state.filters;
        const bits = [];
        if (f.platform.length) bits.push('平台 ' + f.platform.join(' / '));
        if (f.dimension.length) bits.push('主题 ' + f.dimension.join(' / '));
        if (f.sentiment.length) bits.push('情感 ' + f.sentiment.join(' / '));
        if (f.date_from || f.date_to) bits.push(`${f.date_from || '最早'} ~ ${f.date_to || '当前'}`);
        if (f.keyword) bits.push('关键词「' + f.keyword + '」');
        return bits.join(' · ');
    }

    /**
     * 完整口径串：数据集 + 筛选。两者都取不到时返回「全部数据」。
     * 打印页脚与 AI 依据行共用这一句，避免两处各拼一份、条件一多就对不上。
     * @returns {string}
     */
    function scopeLine() {
        const d = datasetLabel();
        const f = scopeText();
        if (d) return '数据集 ' + d + ' · ' + (f || '未筛选');
        return f || '全部数据';
    }

    /**
     * AI 卡统一的口径行（已转义 HTML，直接赋给 .ai-basis-slot 的 innerHTML）。
     *
     * 可见部分只保留读者扫一眼要用的信息：范围、有效条数、必要时一句分母、引擎与时间。
     * 完整口径与「AI 不新增数字」放在 title 里，不再每张卡重复铺一行免责声明。
     * 四个 AI 接口没有引用核验器，绝不写「引用 n/n 已核验」；也不外露
     * dataset_id / filters_text（内部标识 / 与 scopeText 重复）。
     * @param {any} [d] 接口返回的 data；可能是 undefined
     * @param {'ratio'|'sum'|'word-side'|'mixed'} [kind] 卡型；未传 / 未知值按 mixed
     * @param {{state?: 'pending'|'failed'}} [opts] 结论还没回来时的状态：
     *       口径行只依赖 state.filters，与 AI 无关，所以生成中 / 失败都不该整段消失。
     * @returns {string}
     */
    function aiBasis(d, kind, opts) {
        const data = d || {};
        const st = (opts && opts.state) || '';
        const engine = esc(data.engine_label || '统计规则生成');
        // 只取 HH:MM；时区统一按部署地（东八区）理解，不再逐卡标注。
        const m = /(\d{2}:\d{2})(?::\d{2})?/.exec(String(data.generated_at || ''));
        const at = m ? ' · ' + esc(m[1]) : '';

        // 有效评论条数：只认真实计数（number 或纯数字字符串），取不到就整段省略。
        // 接口返回的计数对应「当前筛选口径」；datasetMeta.valid_count 是数据集全量。
        const asCount = v => {
            if (typeof v === 'number' && isFinite(v) && v >= 0) return v;
            if (typeof v === 'string' && /^\d+$/.test(v) && isFinite(Number(v))) return Number(v);
            return null;
        };
        let validCount = null, fromResponse = false;
        for (const k of ['total', 'valid_count', 'sample_size', 'count']) {
            const n = asCount(data[k]);
            if (n != null) { validCount = n; fromResponse = true; break; }
        }
        if (validCount == null && state.datasetMeta) validCount = asCount(state.datasetMeta.valid_count);
        const f = /** @type {any} */ (state.filters || {});
        const hasFilters = !!(
            (f.platform || []).length || (f.dimension || []).length || (f.sentiment || []).length ||
            f.date_from || f.date_to || f.keyword
        );
        let validNote = '';
        if (validCount != null && (fromResponse || !hasFilters)) {
            validNote = ' · ' + esc(num(validCount)) + ' 条有效评论';
        }

        // 只有读者可能误读百分比时才留一句分母；计数卡不重复解释。
        let denomText = '', denomTitle = '';
        switch (kind) {
            case 'ratio':
                denomText = '比例按已标注情感计';
                denomTitle = '比例为当前筛选内已标注情感的有效评论。';
                break;
            case 'word-side':
                denomText = '词云按对应情感计';
                denomTitle = '正面词云的分母为正面评论，负面词云的分母为负面评论。';
                break;
            case 'mixed':
            default:
                denomText = '比例按有效评论计';
                denomTitle = '比例的分母为当前筛选内的有效评论。';
                break;
            case 'sum':
                denomTitle = '本卡为计数或均值，不换算百分比。';
                break;
        }

        const scope = esc(scopeLine().replace(/^数据集\s*/, ''));
        const line = scope + validNote + (denomText ? ' · ' + esc(denomText) : '');
        const title = '数字来自统计接口，AI 不新增数字。' + denomTitle;
        const tail = st ? '' : engine + at;

        return '<div class="ai-basis" title="' + esc(title) + '">' +
            '<span>' + line + '</span>' +
            (tail ? '<span>' + tail + '</span>' : '') +
            '</div>';
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
                legend: { bottom: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: CHART_FS.micro, color: themeColors().ink3 } },
                series: [{
                    type: 'pie', radius: ['48%', '70%'], center: ['50%', '46%'],
                    avoidLabelOverlap: true,
                    itemStyle: { borderColor: themeColors().surface, borderWidth: 2 },
                    label: { show: false },
                    emphasis: { label: { show: true, fontSize: CHART_FS.sm, fontWeight: 600, formatter: '{b}\n{d}%' } },
                    data: data.map(d => Object.assign({}, d, {
                        itemStyle: { color: SENTIMENT_COLOR[d.name] || undefined }
                    }))
                }]
            });
        }

        const horizontal = !!spec.horizontal || spec.type === 'bar-horizontal';
        /* 颜色先认**语义**名，再回退到序号。
           原来一律按 series 下标取 PALETTE：「分平台情感占比」的第二条
           是「负面占比」，却被染成 PALETTE[1] 的青绿 —— 同屏环形图写着
           红=负面。情感序列必须走 SENTIMENT_COLOR，否则就是“立场不可信”。 */
        const SENT_SERIES = { '正面': SENTIMENT_COLOR['正面'], '中性': SENTIMENT_COLOR['中性'], '负面': SENTIMENT_COLOR['负面'] };
        const seriesColor = (name, i) => {
            const n = String(name || '');
            for (const k in SENT_SERIES) if (n.indexOf(k) === 0) return SENT_SERIES[k];
            return PALETTE[i % PALETTE.length];
        };
        const series = (spec.series || []).map((s, i) => ({
            name: s.name, type: spec.type === 'line' ? 'line' : 'bar',
            data: s.data,
            smooth: spec.type === 'line',
            symbol: 'circle', symbolSize: 5,
            showSymbol: (spec.categories || []).length <= 24,
            lineStyle: { width: 2, color: seriesColor(s.name, i) },
            areaStyle: spec.type === 'line' && i === 0 ? { opacity: .08 } : undefined,
            barMaxWidth: 22,
            itemStyle: { color: seriesColor(s.name, i), borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] }
        }));

        const catAxis = Object.assign({ type: 'category', data: spec.categories || [] }, axis, {
            splitLine: { show: false },
            axisLabel: { color: themeColors().ink4, fontSize: CHART_FS.micro, hideOverlap: true }
        });
        const valAxis = Object.assign({ type: 'value', name: unit, nameTextStyle: { color: themeColors().ink4, fontSize: CHART_FS.micro, padding: [0, 0, 0, 6] } }, axis);

        return Object.assign(base, {
            tooltip: Object.assign(base.tooltip, { trigger: 'axis', axisPointer: { type: 'shadow' } }),
            legend: series.length > 1 ? { top: 0, right: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: CHART_FS.micro, color: themeColors().ink3 } } : undefined,
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
    const DIMENSIONS = ['宿舍住宿', '食堂餐饮', '招生报考', '教学学业', '实习就业', '校园环境', '交通出行', '校园活动'];
    const PLATFORMS = ['抖音', '小红书'];

    /**
     * 区间按钮上的文案：没选就写「全部时间」，选了就写区间本身——
     * 让人不点开日期框也知道现在算的是哪一段。
     * @param {{date_from: string, date_to: string}} f
     */
    function rangeLabel(f) {
        const a = f.date_from, b = f.date_to;
        /** @param {string} s */
        const short = s => String(s).slice(5);
        if (!a && !b) return '全部时间';
        if (a && b) {
            return String(a).slice(0, 4) === String(b).slice(0, 4)
                ? short(a) + ' ~ ' + short(b) : a + ' ~ ' + b;
        }
        return a ? '自 ' + short(a) : '至 ' + short(b);
    }

    /**
     * 渲染筛选栏并双向绑定 state.filters。
     * @param {HTMLElement} container
     * @param {{keyword?: boolean}} [opts] keyword=false 时不出关键词输入框
     * @returns {{sync: () => void, apply: () => void, clearFilters: () => void}}
     */
    function renderFilterBar(container, opts) {
        opts = opts || {};
        const withKeyword = opts.keyword !== false;
        container.classList.add('filterbar');
        /* 区间收成一枚按钮：两枚空日期框默认占 320px，首屏上一个字都不说明。
           当前口径不点开也能读——按钮上直接写「全部时间」或「05-01 ~ 06-01」。
           点开才露出两个 date 输入（它们本来就在筛选逻辑里，只是默认藏着）。 */
        container.innerHTML = `
            <button id="cpFilterToggle" class="cp-filter-toggle" type="button"
                    aria-expanded="false" aria-controls="cpFilterBody">筛选 · 已选 0 个条件</button>
            <div class="cp-filter-body" id="cpFilterBody" data-role="filter-body">
            <span class="filter-group">
                <span class="filter-label">平台</span>
                <div class="chip-group" data-group="platform" role="group" aria-label="平台筛选">
                    ${PLATFORMS.map(p => `<button class="chip" data-value="${p}">${p}</button>`).join('')}
                </div>
            </span>
            <span class="filter-dim" data-role="dim-wrap">
                <span class="filter-sep"></span>
                <button class="chip" data-role="dim-chip" title="点击取消该主题筛选"></button>
            </span>
            <span class="filter-sep"></span>
            <span class="filter-group">
                <span class="filter-label">情感</span>
                <div class="chip-group" data-group="sentiment" role="group" aria-label="情感筛选">
                    <!-- 全不选 = 全部，但"一枚都没亮"既可能是全部也可能是没加载完；
                         给一枚显式的「全部」，取消就不必再点同一枚去猜 -->
                    <button class="chip" data-value="">全部</button>
                    <button class="chip" data-value="positive">正面</button>
                    <button class="chip" data-value="neutral">中性</button>
                    <button class="chip" data-value="negative">负面</button>
                </div>
            </span>
            <span class="filter-group">
                <span class="filter-label" style="margin-left:4px;">区间</span>
                <button class="range-btn" data-role="range-btn" type="button" aria-expanded="false">
                    全部时间</button>
            </span>
            <span class="range-fields" data-role="range-fields" hidden>
                <input class="input" type="date" data-role="date_from" style="width:132px;"
                       aria-label="开始日期">
                <span class="filter-label">至</span>
                <input class="input" type="date" data-role="date_to" style="width:132px;"
                       aria-label="结束日期">
            </span>
            ${withKeyword ? `<input class="input" data-role="keyword" placeholder="关键词"
                   aria-label="关键词筛选" style="width:120px;">` : ''}
            <button class="filter-clear" data-role="reset" type="button" hidden>
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
                     stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
                <span data-role="reset-label">清除筛选</span>
            </button>
            <span class="badge" data-role="range-hint" style="margin-left:auto;">—</span>
            </div>
        `;

        const sync = () => {
            // 按 data-group 统一同步：屏幕上的选中态必须和 state.filters 完全一致，
            // 否则「对话带过来的筛选」会在界面上"看不见但生效"，用户既不知道也取消不掉
            $$('[data-group]', container).forEach(group => {
                const key = group.dataset.group;
                const cur = state.filters[key] || [];
                $$('.chip', group).forEach(c => {
                    // data-value="" 的那一枚是「全部」：它亮 = 这一组没有任何条件
                    const on = c.dataset.value === '' ? cur.length === 0
                                                     : cur.includes(c.dataset.value);
                    c.classList.toggle('on', on);
                    // 选中态只有底色，读屏要能听出"这枚按没按下"
                    c.setAttribute('aria-pressed', on ? 'true' : 'false');
                });
            });
            // 主题不再给下拉框（下拉框只能单选，且和图表下钻是同一份状态，容易看着不一致）。
            // 现在只有"当前确实带着主题条件"时才出现一枚可点掉的标签：
            // 条件是下钻 / 榜单点击 / URL 带过来的，用户必须看得见才取消得掉。
            const dimWrap = $('[data-role="dim-wrap"]', container);
            const dims = state.filters.dimension || [];
            dimWrap.classList.toggle('on', dims.length > 0);
            const dimChip = $('[data-role="dim-chip"]', container);
            dimChip.textContent = dims.join('、') + ' ×';
            dimChip.hidden = !dims.length;
            // 选中态跟着条件走：这枚标签以前写死 class="on"，于是"没有任何筛选"时
            // 选择器里仍能找到一枚 .chip.on——判断"当前有没有筛选条件"的脚本会全部误判。
            dimChip.classList.toggle('on', dims.length > 0);
            $('[data-role="date_from"]', container).value = state.filters.date_from || '';
            $('[data-role="date_to"]', container).value = state.filters.date_to || '';
            // 区间按钮自己承担"当前是什么口径"，不用点开日期框才能看见
            const rbtn = $('[data-role="range-btn"]', container);
            if (rbtn) {
                const label = rangeLabel(state.filters);
                const on = !!(state.filters.date_from || state.filters.date_to);
                rbtn.textContent = label;
                rbtn.classList.toggle('on', on);
                rbtn.title = on ? '当前区间 ' + label + '，点击修改' : '当前不限时间范围，点击选择';
            }
            if (withKeyword) $('[data-role="keyword"]', container).value = state.filters.keyword || '';
            syncClear();
            const span = state.datasetMeta ? (state.datasetMeta.time_start + ' ~ ' + state.datasetMeta.time_end) : '';
            $('[data-role="range-hint"]', container).textContent = span ? '数据覆盖 ' + span : '—';
        };

        /** 一键清除按钮的可见性与文案：没有条件时整枚隐藏，有条件时写明清几项。
         *
         * 这里原来是最右侧一枚浅灰的「重置筛选」：颜色淡，而且离关键词输入框一整条栏那么远。
         * 从证据中心按关键词下钻回来的人，第一眼落在"关键词还留在框里"那一格，
         * 却要横穿筛选栏去找那颗字，很容易以为根本没有清除入口。
         * 所以按钮挪到关键词后面（下钻条件总是从这一侧产生的），并且只在真有条件时出现——
         * 无条件时它是个点了没反应的死按钮，占着位置反而让人以为筛选还生效。
         */
        function syncClear() {
            const f = state.filters;
            const active = [];
            if (f.platform && f.platform.length) active.push('平台');
            if (f.dimension && f.dimension.length) active.push('主题');
            if (f.sentiment && f.sentiment.length) active.push('情感');
            if (f.date_from || f.date_to) active.push('区间');
            if (f.keyword) active.push('关键词');
            const btn = $('[data-role="reset"]', container);
            if (!btn) return;
            btn.hidden = !active.length;
            const label = $('[data-role="reset-label"]', container);
            if (label) label.textContent = active.length > 1 ? `清除筛选（${active.length}）` : '清除筛选';
            // 悬浮写清"这一下会清掉什么"：点下去连区间一起清，
            // 不先说明的话，只想清关键词的人会连带丢掉自己刚选的日期。
            btn.title = active.length
                ? `点击清除：${active.join(' · ')}（回到全部数据）`
                : '清除全部筛选条件';
            // 003 / 103：「已选 N 个条件」直接复用上面这份 active 计数（激活维度数），
            // 与批量条「已选 N 张工单」区分开；sync() 每次都会走到这里，数字随之更新。
            const tgl = $('#cpFilterToggle', container);
            if (tgl) {
                // ≤720 折叠后只剩这一行摘要：并上数据覆盖区间，让时间口径不随折叠消失。
                // 字段来自 /api/datasets 的 dataset 对象：time_start / time_end（下拉另有 time_span 数组）。
                const meta = state.datasetMeta || {};
                const a = meta.time_start || (Array.isArray(meta.time_span) ? meta.time_span[0] : '');
                const b = meta.time_end || (Array.isArray(meta.time_span) ? meta.time_span[1] : '');
                const span = (a && b) ? String(a).slice(0, 7) + '~' + String(b).slice(0, 7) : '';
                tgl.textContent = '筛选 · 已选 ' + active.length + ' 个条件'
                    + (span ? ' · 数据 ' + span : '');
            }
        }

        function apply() {
            syncUrl();
            document.dispatchEvent(new CustomEvent('cp:filter', { detail: state.filters }));
        }

        container.addEventListener('click', e => {
            const el = /** @type {HTMLElement} */ (e.target);
            // 003 / 092：只剩窄屏（≤720）这一枚折叠摘要；桌面不再有「更多筛选」空折叠，
            // 数据覆盖口径徽标与分隔线常驻可见。is-expanded 开关仅由摘要按钮驱动。
            if (el.closest('#cpFilterToggle')) {
                const open = !container.classList.contains('is-expanded');
                container.classList.toggle('is-expanded', open);
                $('#cpFilterToggle', container)
                    .setAttribute('aria-expanded', open ? 'true' : 'false');
                return;
            }
            // 必须在 .chip 分支之前判断：主题标签也是 .chip，
            // 先走 chip 分支会被当成平台/情感分组去 toggle 一个不存在的值。
            if (el.closest('[data-role="dim-chip"]')) {
                state.filters.dimension = [];
                sync(); apply();
                return;
            }
            const rbtn = el.closest('[data-role="range-btn"]');
            if (rbtn) {
                const fields = $('[data-role="range-fields"]', container);
                const open = fields.hidden;
                fields.hidden = !open;
                rbtn.setAttribute('aria-expanded', open ? 'true' : 'false');
                if (open) {
                    const inp = $('[data-role="date_from"]', container);
                    if (inp) inp.focus();
                }
                return;
            }
            const chip = /** @type {HTMLElement} */ (el.closest('.chip'));
            if (chip) {
                const group = /** @type {HTMLElement} */ (chip.closest('[data-group]'));
                const key = group ? group.dataset.group : 'platform';
                if (!Array.isArray(state.filters[key])) state.filters[key] = [];
                const arr = state.filters[key];
                const v = chip.dataset.value;
                if (v === '') {
                    // 「全部」不是数组里的一个值，它是"清空"
                    arr.length = 0;
                } else {
                    const i = arr.indexOf(v);
                    if (i >= 0) arr.splice(i, 1); else arr.push(v);
                }
                sync(); apply();
                return;
            }
            if (el.closest('[data-role="reset"]')) {
                clearFilters();
                return;
            }
        });

        container.addEventListener('change', e => {
            const el = /** @type {HTMLInputElement} */ (e.target);
            const role = el.dataset.role;
            if (role === 'date_from') state.filters.date_from = el.value;
            else if (role === 'date_to') state.filters.date_to = el.value;
            else return;
            sync();      // 先让按钮上的口径跟着改，再发请求
            apply();
        });

        // 关键词输入防抖：每敲一个字就发一次请求会把接口打满，
        // 450ms 是"手感上还是即时的，但一段话只发一次"的折中。
        let kwTimer = null;
        if (withKeyword) {
            const kw = $('[data-role="keyword"]', container);
            kw.addEventListener('input', () => {
                clearTimeout(kwTimer);
                kwTimer = setTimeout(() => { state.filters.keyword = kw.value.trim(); apply(); }, 450);
            });
        }

        /* 清除筛选只留这一份实现：筛选栏的 [data-role="reset"] 与 setState 空态
           里的 .clear-filters 按钮都调它（后者经模块级 activeClearFilters 复用），
           不另写第二套清空逻辑。 */
        function clearFilters() {
            state.filters = { platform: [], dimension: [], sentiment: [], date_from: '', date_to: '', keyword: '' };
            // 清完把区间收回原位：条件没了还占着 320px 的两个日期框，
            // 看上去像"还有一个筛选在生效"
            const rf = $('[data-role="range-fields"]', container);
            const rb = $('[data-role="range-btn"]', container);
            if (rf) rf.hidden = true;
            if (rb) rb.setAttribute('aria-expanded', 'false');
            // 还在防抖窗口里那一次打字不能留到按钮之后再落地
            clearTimeout(kwTimer);
            sync(); apply();
            toast('已清除全部筛选条件', 'ok');
        }
        activeClearFilters = clearFilters;

        sync();
        return { sync, apply, clearFilters };
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
        // 四个数据集的差别在"清洗掉了多少、覆盖哪段时间"，不是"名字谁好听"。
        // 下拉里放不下这些，放进 option 的 title：鼠标悬停能读全，选之前就知道在选什么。
        sel.innerHTML = state.datasets.map(d => {
            const tip = [
                d.name,
                d.valid_count != null ? `有效 ${num(d.valid_count)} 条` : '',
                d.raw_count != null ? `原始 ${num(d.raw_count)} 条` : '',
                d.time_span && d.time_span.length ? `${d.time_span[0]} ~ ${d.time_span[1]}` : '',
                d.data_source || ''
            ].filter(Boolean).join(' · ');
            return `<option value="${d.dataset_id}" title="${esc(tip)}">${esc(d.name)} · ${num(d.valid_count)} 条</option>`;
        }).join('');
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

    /**
     * 切换数据集（全局唯一入口）。
     *
     * 放在 core 而不是某个页面的脚本里：采集入库、文件导入、截图导入、顶栏下拉
     * 都会改当前数据集，四个地方各写一份的话，总有一处忘了清空筛选——
     * 表现就是"换了数据集，图表还带着上一批的筛选条件"。
     * 页面自己的刷新动作挂 cp:dataset 事件，不在这里写死。
     */
    async function switchDataset(id) {
        const d = await api('/api/datasets/select', null, {
            method: 'POST', body: JSON.stringify({ dataset_id: id })
        });
        state.dataset_id = d.dataset_id;
        // 新数据集此刻可能还不在 state.datasets 里，重拉一次再定位
        await loadDatasets();
        state.datasetMeta = state.datasets.find(x => x.dataset_id === d.dataset_id) || state.datasetMeta;
        state.filters = { platform: [], dimension: [], sentiment: [], date_from: '', date_to: '', keyword: '' };
        syncUrl();
        updateSnapshotBadge();
        if (filterBarApi) filterBarApi.sync();
        renderDatasetSelect();
        document.dispatchEvent(new CustomEvent('cp:dataset', { detail: d.dataset_id }));
        return d;
    }

    /** 顶栏"历史快照·数据截至 X"角标：把"这不是实时数据"写在用户一眼能看到的地方 */
    function updateSnapshotBadge() {
        const b = $('#snapshotBadge');
        const meta = state.datasetMeta;
        if (!b || !meta) return;
        // 只留「历史快照」四个字：数据截至哪一天，筛选条右端已经写着
        // 「数据覆盖 2022-01-13 ~ 2026-07-07」，同一串日期在一条视线上出现两次，
        // 首屏就多了一个要读的重复项。日期移到 title 里，需要时悬停或读屏能拿到。
        b.textContent = '历史快照';
        b.title = `数据截至 ${meta.time_end || '—'}（历史采集快照，不代表实时全网监测）\n` +
            `数据集：${meta.name}\n原始 ${num(meta.raw_count)} 条 / 有效 ${num(meta.valid_count)} 条\n` +
            `最后分析：${meta.analyzed_at || '—'}`;
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
            el.removeAttribute('aria-busy');
            if (!d || !d.can_view) { el.classList.add('is-hidden'); return null; }
            /* 第 19 轮：角标该显示哪个数由后端的 scope 说算（mine/dept/all）。
               以前前端只取 mine，而管理员的 mine 恒为 0——
               3 张超期单在顶栏上一个提示都没有，而首页横幅写着超期 3。 */
            const mineOnly = d.scope === 'mine';
            const n = mineOnly ? (d.mine || 0) : (d.pending || 0);
            if (!n) { el.classList.add('is-hidden'); return d; }
            el.classList.remove('is-hidden');
            txt.textContent = n > 99 ? '99+' : String(n);
            el.classList.toggle('is-overdue', !!d.overdue);
            el.title = (mineOnly ? `我的待办 ${n} 单` : `未结 ${n} 单`)
                + ` · 超期 ${d.overdue || 0} 单`;
            // 读屏读到的必须是同一件事：以前写死了「待处置工单」，
            // 而首页横幅已经改叫「未结」——同一个数两种叫法。
            el.setAttribute('aria-label',
                `${n} 单${mineOnly ? '待我处理' : '未结'}工单`
                + (d.overdue ? `，超期 ${d.overdue} 单` : ''));
            return d;
        } catch (e) {
            el.removeAttribute('aria-busy');
            el.classList.add('is-hidden');
            return null;
        }
    }

    /* ---------------- 账户菜单（只在 CP_AUTH=1 时渲染出 DOM） ---------------- */
    /**
     * 顶栏账户菜单：修改密码 / 退出登录。
     * 没有这两个入口时，开了登录反而更难用——想换个角色演示（分析员、处置员、只读）
     * 只能去手动清 cookie。元素不存在时（CP_AUTH=0）整段直接返回。
     */
    function bindAccount() {
        const chip = $('#userChip');
        const pop = $('#userPop');
        if (!chip || !pop) return;

        const modal = $('#pwdModal');
        const form = $('#pwdForm');
        const errBox = $('#pwdErr');

        /** 关闭下拉 */
        function closePop() {
            pop.hidden = true;
            chip.setAttribute('aria-expanded', 'false');
        }

        /** 关闭改密弹窗 */
        function closePwd() {
            if (!modal) return;
            closeModal(modal);
            if (errBox) errBox.hidden = true;
        }

        chip.addEventListener('click', function (e) {
            e.stopPropagation();
            const willOpen = pop.hidden;
            pop.hidden = !willOpen;
            chip.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        });

        document.addEventListener('click', function (e) {
            if (!pop.hidden && !pop.contains(/** @type {Node} */ (e.target))) closePop();
        });

        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') return;
            closePop();
            closePwd();
        });

        pop.addEventListener('click', async function (e) {
            const btn = /** @type {HTMLElement} */ (e.target).closest('[data-act]');
            if (!btn) return;
            const act = btn.getAttribute('data-act');
            closePop();

            if (act === 'logout') {
                try {
                    await api('/api/auth/logout', null, { method: 'POST' });
                } catch (e2) {
                    /* 退出失败也让用户走：关不掉会话比留在页面上更糟 */
                }
                location.href = '/login';
                return;
            }
            if (act === 'pwd' && modal) {
                if (form) form.reset();
                if (errBox) errBox.hidden = true;
                openModal(modal, { back: /** @type {HTMLElement} */ (chip) });
            }
        });

        if (modal) {
            modal.addEventListener('click', function (e) {
                if (e.target === modal) { closePwd(); return; }
                if (/** @type {HTMLElement} */ (e.target).closest('[data-act="cancel"]')) closePwd();
            });
        }

        if (form) {
            form.addEventListener('submit', async function (e) {
                e.preventDefault();
                const oldPwd = $('#pwdOld') ? $('#pwdOld').value : '';
                const newPwd = $('#pwdNew') ? $('#pwdNew').value : '';
                const newPwd2 = $('#pwdNew2') ? $('#pwdNew2').value : '';
                /** 就地显示错误，不弹 toast：字段错误应该贴着字段 */
                function showErr(msg) {
                    if (!errBox) return;
                    errBox.textContent = msg;
                    errBox.hidden = false;
                }
                if (errBox) errBox.hidden = true;

                // 前端先挡一遍，省一次往返；真正的判定仍在后端
                if (!oldPwd) return showErr('请输入原密码');
                if (String(newPwd).length < 8) return showErr('新密码至少 8 位');
                if (newPwd !== newPwd2) return showErr('两次输入的新密码不一致');

                const submit = $('#pwdSubmit');
                if (submit) submit.disabled = true;
                try {
                    await api('/api/auth/change-password', null, {
                        method: 'POST',
                        body: JSON.stringify({ old_password: oldPwd, new_password: newPwd })
                    });
                    closePwd();
                    toast('密码已修改', 'ok');
                    // 顶栏的"待改密"提示同步消失，别让人改完了还看到红点
                    const meta = chip.querySelector('.user-meta em');
                    if (meta) meta.textContent = String(meta.textContent).replace(' · 待改密', '');
                } catch (err) {
                    showErr(err.message || '修改失败，请稍后重试');
                } finally {
                    if (submit) submit.disabled = false;
                }
            });
        }
    }

    /* ---------------- 芯片选中态的可朗读版本 ----------------
     * 全站所有 chip（平台/情感/状态分组/区间/粒度/平台切换）的选中态都只改一个 .on 类，
     * 视觉上靠底色区分，读屏听不出来。逐个切换点去补 aria-pressed 一定会漏，
     * 所以盯着 class 变化：.on 写进 DOM 的那一刻，aria-pressed 跟着走。
     * 这也让"选中态只有一个来源"这条约定继续成立。 */
    function watchChips() {
        const apply = el => {
            if (!el || el.nodeType !== 1 || !el.classList.contains('chip')) return;
            el.setAttribute('aria-pressed', el.classList.contains('on') ? 'true' : 'false');
        };
        document.querySelectorAll('.chip').forEach(apply);
        new MutationObserver(muts => {
            for (const m of muts) {
                if (m.type === 'attributes') { apply(/** @type {HTMLElement} */ (m.target)); continue; }
                m.addedNodes.forEach(n => {
                    if (n.nodeType !== 1) return;
                    const el = /** @type {HTMLElement} */ (n);
                    if (el.classList.contains('chip')) apply(el);
                    el.querySelectorAll && el.querySelectorAll('.chip').forEach(apply);
                });
            }
        }).observe(document.body, {
            subtree: true, attributes: true, attributeFilter: ['class'], childList: true
        });
    }

    /**
     * 页面初始化：主题 → URL 状态 → 数据集 → 健康检查 → 筛选栏 → 触发 cp:ready。
     * @param {{filterBar?: boolean, filterOptions?: any}} [opts]
     */
    async function init(opts) {
        opts = opts || {};
        bindTheme();
        bindGlossary();
        bindTopMore();
        watchTopbarHeight();
        bindNavDrawer();
        bindNavGroups();
        bindAccount();
        bindDatasetRename();
        watchChips();
        watchXScroll();
        watchChartStall();
        mountShowAllChartsBtn();
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

    /* ---------------- 窄屏横滑提示 ----------------
     * 表格在窄屏是"保留结构、容器横滑"（见 app.css 的约定），但滑得动这件事
     * 从来没有说出口：右边被裁一刀的数字看起来像坏了，而不是"还有一列"。
     * 这里只加一个状态类，视觉交给 CSS 的渐隐遮罩。
     */
    function watchXScroll() {
        // 阴影之外再给一句话：内阴影在深色主题上几乎看不见，
        // 而"这张表能往右滑"对手机用户是必须说出口的事实，不能靠猜。
        // 提示放在表格上方：长表在手机上要滑很久才到底，
        // 写在表尾等于"看完了才知道还能横滑"。
        const hintFor = (el) => {
            let h = el.previousElementSibling;
            if (!h || !h.classList.contains('table-scroll-hint')) {
                h = document.createElement('div');
                h.className = 'table-scroll-hint';
                h.textContent = '左右滑动查看更多列 →';
                el.insertAdjacentElement('beforebegin', h);
            }
            return h;
        };
        const update = (el) => {
            const over = el.scrollWidth > el.clientWidth + 2;
            const atEnd = el.scrollLeft + el.clientWidth >= el.scrollWidth - 2;
            el.classList.toggle('is-xscroll', over && !atEnd);
            const hint = hintFor(el);
            hint.hidden = !over;
        };
        const scan = () => $$('.table-scroll').forEach(el => {
            if (!el.__xsw) {
                el.__xsw = true;
                el.addEventListener('scroll', () => update(el), { passive: true });
                if (typeof ResizeObserver !== 'undefined') {
                    // 观察里面的表：容器自身宽度不变，是内容变宽了
                    const inner = el.firstElementChild;
                    new ResizeObserver(() => update(el)).observe(inner || el);
                }
            }
            update(el);
        });
        scan();
        window.addEventListener('resize', scan, { passive: true });
        // 表格多半是异步渲染的（研判页、报告页）：只扫一次会漏
        if (typeof MutationObserver !== 'undefined') {
            let t = 0;
            new MutationObserver(() => {
                clearTimeout(t);
                t = setTimeout(scan, 180);
            }).observe(document.body, { childList: true, subtree: true });
        }
    }

    /* 空图表状态改由「按容器观察」接管（见上方 startChartStateWatch）：
       不再有一次性的 10 秒全局 setTimeout。这里只保留 init() 里的启动入口，
       真正的判定在 setOption / flushLazy / IntersectionObserver 回调里完成。 */
    function watchChartStall() {
        startChartStateWatch();
    }

    /* ---------------- 数据集改名 ---------------- */
    /**
     * 顶栏那支笔：重命名当前数据集。
     *
     * 只改 datasets.name —— 名字是给人看的标签，不参与任何统计口径，
     * 所以这里既不重算、也不切数据集，只把下拉和角标重画一遍。
     */
    function bindDatasetRename() {
        const btn = $('#dsRename'), mask = $('#dsRenameModal'), form = $('#dsRenameForm');
        if (!btn || !mask || !form) return;
        const input = $('#dsRenameInput'), sub = $('#dsRenameSub'), err = $('#dsRenameErr');

        function close() { closeModal(mask); }
        function open() {
            const meta = state.datasetMeta;
            if (!meta) return;
            sub.textContent = `${meta.name} · 原始 ${num(meta.raw_count)} / 有效 ${num(meta.valid_count)} 条`;
            input.value = meta.name || '';
            err.hidden = true;
            openModal(mask, { back: /** @type {HTMLElement} */ (btn) });
            input.select();
        }

        btn.addEventListener('click', open);
        mask.addEventListener('click', e => { if (e.target === mask) close(); });
        form.querySelector('[data-act="cancel"]').addEventListener('click', close);
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape' && !mask.hidden) close();
        });

        form.addEventListener('submit', async e => {
            e.preventDefault();
            const name = (input.value || '').trim();
            if (!name) {
                err.textContent = '名称不能为空';
                err.hidden = false;
                return;
            }
            try {
                const d = await api('/api/datasets/' + encodeURIComponent(state.dataset_id) + '/rename',
                    null, { method: 'POST', body: JSON.stringify({ name }) });
                close();
                toast(d.changed ? '已重命名为「' + d.name + '」' : '名称没有变化', 'ok');
                // 只刷新数据集列表与角标，不触发任何重算
                await loadDatasets();
            } catch (e2) {
                err.textContent = e2.message;
                err.hidden = false;
            }
        });
    }

    /** 让页面脚本（下钻、榜单点击）改完 state.filters 后能同步筛选栏上的可见状态 */
    function syncFilterBar() {
        if (filterBarApi) filterBarApi.sync();
    }

    global.CP = {
        state, $, $$, esc, num, pct, dateShort, toast, api, qs, syncUrl, filterParams,
        workHours, workdays, onTimeFoot, onTimeParts,
        setState, chart, setOption, onChart, resizeCharts, whenVisible, renderSpec, specToOption,
        themeBase, axisCommon, CHART_FS, NEG_DECAL, syncFilterBar, openModal, closeModal, confirmModal, describeChart,
        announce, labelChart, flushAllCharts, scopeText, scopeLine, datasetLabel, aiBasis, markChartFailed,
        renderFilterBar, init, loadHealth, loadTicketBadge, PALETTE, POS, NEU, NEG, SENTIMENT_COLOR, DIMENSIONS, PLATFORMS,
        renderDatasetSelect, loadDatasets, switchDataset,
        getTheme, applyTheme, toggleTheme, themeColors
    };

    // 自动初始化：页面脚本在 DOMContentLoaded 之前注册好 cp:ready 监听即可
    function autoInit() {
        if (global.__CP_NO_AUTOINIT) return;
        init();
    }
    // 横滑提示在首帧前就占好位：等到 DOMContentLoaded 再插，
    // 表格已经画出来了，提示会把整张表往下推 25px（390 /data 实测 0.016 CLS）。
    // 脚本在 </body> 前同步执行，上面的表格容器此时已经解析完。
    watchXScroll();
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', autoInit);
    } else {
        autoInit();
    }
})(window);
