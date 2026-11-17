/* PAGE-03 数据洞察中心 —— 总览 + 主题热度 / 情感 / 时间趋势 三个子页
 *
 * 四个页面共用这一个脚本。拆成四个文件的话，下钻（onChart）、词云按需加载、
 * AI 解读过滤这些公共逻辑要复制四份，改一处得想起另外三处。
 *
 * 做法是每个渲染函数先探测自己的容器在不在，不在就直接 return，
 * 于是同一份脚本落在哪个页面就只画哪个页面的图。
 */
(function () {
    'use strict';
    const { $, api, num, pct, setState, setOption, themeBase, axisCommon, toast } = CP;

    const AX = axisCommon();
    const GRID = { left: 6, right: 14, top: 22, bottom: 2, containLabel: true };
    /* 平台色不写死。品牌蓝从 #3B63F3 改到 #3358E8 时这里没跟着改，
       于是同一屏上「抖音柱」比侧栏的品牌色更旧一档；而 #F06595 既不在
       PALETTE 里，又和「负面红」同属暖红家族——两个平台是"在哪说的"，
       不是"说得好不好"，不该用红系去分。现在：一端品牌色，一端中性青。 */
    const cssVar = (name, fallback) => {
        const v = getComputedStyle(document.documentElement).getPropertyValue(name);
        return (v || '').trim() || fallback;
    };
    const PLATFORM_COLOR = () => ({
        '抖音': cssVar('--brand', '#3358E8'),
        '小红书': CP.PALETTE[1]       // 青 #14B8A6：与负面红、正面绿都拉得开
    });
    const SENT_COLOR = { positive: '#16A34A', neutral: '#CBD5E1', negative: '#E5484D' };

    function baseOpt(extra) {
        return Object.assign(themeBase(), {
            grid: GRID,
            tooltip: Object.assign(themeBase().tooltip, { trigger: 'axis', axisPointer: { type: 'shadow' } })
        }, extra || {});
    }

    /* ---------------- 页面识别 ---------------- */

    /** 用"这个页面有没有这张图的容器"判断当前是哪一页。
     *
     * 没有往 body 上加 data-page，是因为那样每加一个页面就要在模板和 JS 各改一处，
     * 而漏改的表现是"脚本跑了但什么都不画"，且不报错。
     * 容器探测的判据和渲染条件本来就是同一个，不会走岔。
     */
    function detectPage() {
        if ($('#chartTrendFlow')) return 'timeline';
        if ($('#chartSentOverview')) return 'sentiment';
        if ($('#chartDims')) return 'topics';
        if ($('#chartPlatformPie')) return 'overview';
        return null;
    }

    const CHART_IDS = {
        overview: ['#chartPlatformPie', '#chartLength', '#chartLikeTop'],
        topics: ['#chartDims', '#chartPlatDim'],
        sentiment: ['#chartSentOverview', '#chartDimSent', '#chartPareto',
                    '#chartWordPos', '#chartWordNeg'],
        timeline: ['#chartTrendFlow', '#chartHour', '#chartWeek']
    };

    /** I09 联动下钻：点击图表元素 → 带着当前筛选条件跳到证据中心
     *
     * 用 CP.onChart 而不是 CP.chart：图表是惰性初始化的，首屏看不见的图还没有实例，
     * 直接取会拿到 null 并且**静默**丢掉下钻能力——用户滚到那张图时点它没反应，
     * 而且没有任何报错，最难排查的那种问题。
     */
    function drillTo(el, mapper) {
        CP.onChart(el, inst => {
            if (!inst.on) return;
            inst.off('click');
            inst.on('click', p => {
                const extra = mapper(p);
                if (!extra) return;
                location.href = '/evidence?' + CP.qs(extra);
            });
            try {
                const zr = inst.getZr();
                if (zr && typeof zr.setCursorStyle === 'function') zr.setCursorStyle('pointer');
            } catch (e) { /* 光标样式非关键，失败不影响下钻 */ }
        });
    }

    /** 词云插件按需加载：16KB 的扩展只为一张图服务，没必要拖慢首屏 */
    let wordCloudPromise = null;
    function ensureWordCloud() {
        if (typeof window.echarts === 'undefined') return Promise.resolve(false);
        if (window.__CP_WORDCLOUD_READY) return Promise.resolve(true);
        if (wordCloudPromise) return wordCloudPromise;
        wordCloudPromise = new Promise(resolve => {
            const s = document.createElement('script');
            s.src = (window.__CP_WORDCLOUD_URL || '/static/vendor/echarts-wordcloud.min.js');
            s.async = true;
            s.onload = () => { window.__CP_WORDCLOUD_READY = true; resolve(true); };
            s.onerror = () => { wordCloudPromise = null; resolve(false); };
            document.head.appendChild(s);
        });
        return wordCloudPromise;
    }

    /* ---------------- AI 解读 ---------------- */

    /** 写 AI 卡「依据」行。
     * 空槽由模板组并行加入、CP.aiBasis 由 core.js 组并行实现；两者未就位时静默跳过，不抛错。
     * 只用 innerHTML 赋值（幂等），不 insertAdjacentHTML，避免重复累积。
     * 第 10 轮 B3：`d` 为空时不再整段清空——「数据集 + 筛选 + 分母」只依赖
     * state.filters，与 AI 无关，生成中/失败的 10–30s 里它照样成立。 */
    function setBasis(id, d, state) {
        const slot = document.getElementById(id);
        if (!slot) return;
        if (typeof CP.aiBasis !== 'function') { slot.innerHTML = ''; return; }
        slot.innerHTML = d
            ? CP.aiBasis(d, basisKind(id))
            : CP.aiBasis(null, basisKind(id), { state: state || 'pending' });
    }

    /** 一张表按槽 id 查分母口径（第 10 轮按页面重定）。
     *
     * 以前只有 sum / mixed / word-side 三个值，`ratio` 分支写好了却没有任何调用方，
     * 于是情感页那张正文写着「中性 76.3%、正面 17.8%、负面 5.9%」的纯比例卡，
     * 依据行却写「本卡是计数或均值」——同一张卡自相矛盾。
     *
     *   sentiment 情感页：正文是构成比例 → ratio
     *   topics    主题页：正文是量与排行 → sum
     *   timeline  时段页：峰值既是计数又常讲占比 → mixed
     *   overview  总览页：跨指标混排 → mixed
     */
    function basisKind(slotId) {
        const page = detectPage();
        if (slotId === 'wiBasis') return 'word-side';
        if (slotId === 'piBasis') {
            return page === 'sentiment' ? 'ratio' : (page === 'topics' ? 'sum' : 'mixed');
        }
        return page === 'sentiment' ? 'ratio' : 'mixed';
    }

    // AI 分析的请求序号：切数据集 / 切页面时会连发多次，只让最后一次的结果落地
    let piSeq = 0;

    /** 总览页的全局结论：四条一次给全，因为它们本来就是跨页面的总结。
     *
     * 三个子页不再用这个接口，改用 /api/analytics/page-insight —— 子页要回答的是
     * "为什么"（为什么这个主题最热、痛点指向哪、峰值为什么长这样），
     * 而 chart-insights 只会把排行数字换个说法再念一遍，读者看完仍然不知道原因。
     */
    async function loadInsights() {
        const box = $('#chartInsights'), badge = $('#ciEngine'), note = $('#ciNotice');
        if (!box) return;
        const seq = ++piSeq;
        if (badge) { badge.textContent = '生成中'; badge.className = 'badge'; }
        setBasis('ciBasis', null, 'pending');     // 生成中：口径行照旧，只把结论行改成「生成中」
        // 生成中占位：复用现有 .skeleton 骨架，另起一行按秒更新耗时（不加取消按钮，没有 AbortController）
        box.innerHTML = '<div class="skeleton" style="height:56px"></div>'.repeat(4)
            + '<div class="ai-progress" style="grid-column:1/-1;">已等待 0 秒 · 模型生成通常需要 10–30 秒</div>';
        const prog = box.querySelector('.ai-progress');
        const t0 = Date.now();
        const progressTimer = setInterval(() => {
            if (prog) prog.textContent = `已等待 ${Math.floor((Date.now() - t0) / 1000)} 秒 · 模型生成通常需要 10–30 秒`;
        }, 1000);
        try {
            const d = await api('/api/analytics/chart-insights', CP.filterParams());
            if (seq !== piSeq) return;              // 只认最后一次请求
            const items = d.items || [];
            if (!items.length) {
                box.innerHTML = '<div class="note note-muted">当前筛选范围没有数据，暂不生成解读。</div>';
                if (badge) badge.textContent = '无数据';
                setBasis('ciBasis', null, 'failed'); // 无数据：口径行照旧，结论行写「未生成」
                return;
            }
            box.innerHTML = items.map(it => `
                <div class="pi-reason ci-item">
                    <span class="badge badge-brand" style="flex:0 0 auto;height:20px;">${CP.esc(it.chart)}</span>
                    <span style="font-size:var(--fs-sm);line-height:1.7;color:var(--ink-2);">${CP.esc(it.text)}</span>
                </div>`).join('');
            if (badge) {
                badge.textContent = d.engine_label;
                badge.className = 'badge ' + (d.engine === 'deepseek' ? 'badge-brand' : 'badge-warn');
            }
            if (note) {
                if (d.notice) { note.classList.remove('is-hidden'); note.textContent = d.notice; }
                else note.classList.add('is-hidden');
            }
            setBasis('ciBasis', d);                 // 成功：写依据行
        } catch (e) {
            console.warn('[insights] 图表 AI 解读生成失败：' + e.message);   // 原始错误只进控制台
            box.innerHTML = '<div class="note note-warn">分析暂时未生成，请稍后刷新页面重试。</div>';
            if (badge) badge.textContent = '失败';
            setBasis('ciBasis', null, 'failed');    // 失败：口径行照旧，别连这批数据是什么一起抹掉
        } finally {
            clearInterval(progressTimer);          // 不泄漏定时器
        }
    }

    /** 子页的 AI 分析：结论 → 数据现状 → 逐条成因 → 还能往下看什么。
     *
     * 和后端的分工：模型只拿到已经算好的统计与脱敏样本评论，一个数字都不许新增；
     * 拿不到模型时后端回退成「只陈述事实」的规则版，结构相同、归因留空，
     * 前端一条渲染路径，不给两套口径。
     *
     * 为什么要有 reasons 这个结构：写成一整段时，模型讲完峰值和总量就收尾，
     * 「为什么会这样」被压缩成一句「可能与校园作息有关」。拆成条目后每条都必须
     * 先给依据再给解释，读者能一条条核对，答辩时也说得清哪句是推断。
     */
    async function loadPageInsight(page) {
        const box = $('#pageInsight'), badge = $('#piBadge'), note = $('#piNotice');
        if (!box) return;
        // 与 loadTrend 同一套序号守卫：模型生成是慢请求，连点两次数据集时
        // 先发的那次可能后到，把新结论覆盖成旧数据集的——页面看着就是"没变"。
        const seq = ++piSeq;
        // 重新生成时别把旧结论抹掉：模型要 10–30 秒，这段空白既没有内容也没有
        // 依据行，读完像"点了没反应"。旧结论留着、压到 0.55 透明度、卡头写
        // 「更新中」，新结果一到就替换——等待期间屏幕上一直有东西可读。
        const keepOld = !!box.querySelector('.pi-head, .pi-text, .pi-reasons');
        if (badge) { badge.textContent = keepOld ? '更新中' : '生成中'; badge.className = 'badge'; }
        setBasis('piBasis', null, 'pending');     // 生成中：口径行照旧
        let prog = null;
        if (keepOld) {
            box.classList.add('is-refreshing');
            box.setAttribute('aria-busy', 'true');
        } else {
            // 生成中占位：保留模板里的 .skeleton 骨架，另起一行按秒更新耗时（不加取消按钮）
            box.innerHTML = '<div class="skeleton" style="height:64px"></div>'
                + '<div class="ai-progress">已等待 0 秒 · 模型生成通常需要 10–30 秒</div>';
            prog = box.querySelector('.ai-progress');
        }
        const t0 = Date.now();
        const progressTimer = setInterval(() => {
            if (prog) prog.textContent = `已等待 ${Math.floor((Date.now() - t0) / 1000)} 秒 · 模型生成通常需要 10–30 秒`;
        }, 1000);
        try {
            const d = await api('/api/analytics/page-insight',
                Object.assign(CP.filterParams(), { page }));
            if (seq !== piSeq) return;              // 只认最后一次请求
            box.classList.remove('is-refreshing');
            box.removeAttribute('aria-busy');
            const reasons = (d.reasons || []).map((r, i) => `
                <div class="pi-reason">
                    <span class="pi-idx">${i + 1}</span>
                    <div class="pi-reason-body">
                        <div class="pi-point">${CP.esc(r.point || '')}</div>
                        <div class="pi-detail">${CP.esc(r.detail || '')}</div>
                    </div>
                </div>`).join('');
            const sugg = (d.suggestions || []).map((s, i) => `
                <div class="pi-reason">
                    <span class="pi-idx">${i + 1}</span>
                    <div class="pi-reason-body">
                        <div class="pi-point">${CP.esc(s.title || '建议')}</div>
                        <div class="pi-detail">${CP.esc(s.content || '')}</div>
                    </div>
                </div>`).join('');
            box.innerHTML = [
                d.headline ? `<p class="pi-head">${CP.esc(d.headline)}</p>` : '',
                d.summary ? `<p class="pi-text">${CP.esc(d.summary)}</p>` : '',
                reasons ? `<div class="pi-reasons">${reasons}</div>` : '',
                sugg ? `<div style="margin-top:12px;font-size:var(--fs-xs);color:var(--ink-3);">
                    <b>处置建议（需人工审核）</b></div><div class="pi-reasons">${sugg}</div>` : '',
                d.next ? `<div class="note note-muted" style="margin-top:10px;">
                    <b>还可以看：</b>${CP.esc(d.next)}</div>` : ''
            ].join('');
            if (badge) {
                badge.textContent = d.engine_label;
                badge.className = 'badge ' + (d.engine === 'deepseek' ? 'badge-brand' : 'badge-warn');
            }
            if (note) {
                if (d.notice) { note.classList.remove('is-hidden'); note.textContent = d.notice; }
                else note.classList.add('is-hidden');
            }
            setBasis('piBasis', d);                 // 成功：写依据行
        } catch (e) {
            if (seq !== piSeq) return;
            console.warn('[insights] 子页 AI 分析生成失败：' + e.message);
            box.classList.remove('is-refreshing');
            box.removeAttribute('aria-busy');
            box.innerHTML = '<div class="note note-warn">分析暂时未生成，请稍后刷新页面重试。</div>';
            if (badge) badge.textContent = '失败';
            setBasis('piBasis', null, 'failed');    // 失败：口径行照旧
        } finally {
            clearInterval(progressTimer);          // 不泄漏定时器
        }
    }

    /* ---------------- 词云解读 ----------------
     * 两朵云各一份结论，而不是把整页分析再讲一遍：
     * 上面那张卡回答"为什么会有这种情绪"，这里只回答"云里这些词到底在说什么"。
     * 单独一个序号守卫，和 piSeq 互不干扰——两个请求同时回来时，
     * 共用一个计数器会让先到的那份把自己判成过期，页面停在"生成中"。
     */
    let wiSeq = 0;

    function renderWordInsight(d) {
        const box = $('#wordInsight'), badge = $('#wiBadge'), note = $('#wiNotice');
        if (!box) return;
        const totals = d.totals || {};
        const sides = [
            { key: 'positive', name: '正面词云', cls: 'pos', color: SENT_COLOR.positive,
              total: totals.positive || 0 },
            { key: 'negative', name: '负面词云', cls: 'neg', color: SENT_COLOR.negative,
              total: totals.negative || 0 }
        ];
        box.innerHTML = sides.map(s => {
            const part = d[s.key] || {};
            const points = part.points || [];
            return `<div class="wi-side ${s.cls}">
                <div class="wi-side-head">
                    <i class="wi-dot" style="background:${s.color}"></i>
                    <span>${s.name}</span>
                    <span class="wi-total">${num(s.total)} 条评论</span>
                </div>
                ${part.headline ? `<div class="wi-head">${CP.esc(part.headline)}</div>` : ''}
                ${points.length
                    ? `<ul class="wi-points">${points.map(p => `<li>${CP.esc(p)}</li>`).join('')}</ul>`
                    : '<div class="wi-empty">这一侧的评论或词量太少，暂无可解读内容。</div>'}
            </div>`;
        }).join('');
        if (badge) {
            badge.textContent = d.engine_label || '已生成';
            badge.className = 'badge ' + (d.engine === 'deepseek' ? 'badge-brand' : 'badge-warn');
        }
        if (note) {
            if (d.notice) { note.classList.remove('is-hidden'); note.textContent = d.notice; }
            else note.classList.add('is-hidden');
        }
        setBasis('wiBasis', d);                 // 成功渲染：写依据行
    }

    async function loadWordInsight() {
        const box = $('#wordInsight');
        if (!box) return;
        const seq = ++wiSeq;
        const badge = $('#wiBadge'), note = $('#wiNotice');
        if (badge) { badge.textContent = '生成中'; badge.className = 'badge'; }
        if (note) note.classList.add('is-hidden');
        setBasis('wiBasis', null, 'pending');     // 生成中：口径行照旧
        // 生成中占位：保留模板里的 .skeleton 骨架，另起一行按秒更新耗时（不加取消按钮）
        box.innerHTML = '<div class="skeleton" style="height:96px"></div>'.repeat(2)
            + '<div class="ai-progress" style="grid-column:1/-1;">已等待 0 秒 · 模型生成通常需要 10–30 秒</div>';
        const prog = box.querySelector('.ai-progress');
        const t0 = Date.now();
        const progressTimer = setInterval(() => {
            if (prog) prog.textContent = `已等待 ${Math.floor((Date.now() - t0) / 1000)} 秒 · 模型生成通常需要 10–30 秒`;
        }, 1000);
        try {
            const d = await api('/api/analytics/word-insight', CP.filterParams());
            if (seq !== wiSeq) return;              // 只认最后一次请求
            setBasis('wiBasis', d);                 // 成功：写依据行（renderWordInsight 内也会写，幂等）
            renderWordInsight(d);
        } catch (e) {
            if (seq !== wiSeq) return;
            console.warn('[insights] 词云解读生成失败：' + e.message);
            box.innerHTML = '<div class="note note-warn note-flat" style="grid-column:1/-1;">'
                + '分析暂时未生成，请稍后刷新页面重试。</div>';
            if (badge) badge.textContent = '失败';
            setBasis('wiBasis', null, 'failed');    // 失败：口径行照旧
        } finally {
            clearInterval(progressTimer);          // 不泄漏定时器
        }
    }

    /* ================= 读屏：给每张图一个名字 =================
     * canvas 里的内容屏幕阅读器一个像素也拿不到。有数字能用数字（下面几处显式写），
     * 剩下的至少挂上"卡片标题 + 副标题"，别让整块变成"空白"。
     * 已有的 aria-label 不覆盖——显式写的那些比标题更具体。 */
    function describeCharts() {
        document.querySelectorAll('.chart').forEach(el => {
            if (el.getAttribute('aria-label')) return;
            const card = el.closest('.card');
            const t = card && card.querySelector('.card-title');
            const s = card && card.querySelector('.card-sub');
            const text = [t && t.textContent.trim(), s && s.textContent.trim()]
                .filter(Boolean).join('：');
            if (text) CP.describeChart(el, text);
        });
    }

    /* ================= 总览页：数据画像 ================= */

    function renderPlatformPie(s) {
        const el = $('#chartPlatformPie');
        if (!el) return;
        const rows = (s.by_platform || []).filter(p => p.total > 0);
        if (!rows.length) { setState(el, 'empty'); return; }
        const base = themeBase();
        const PC = PLATFORM_COLOR();
        setOption(el, Object.assign(base, {
            tooltip: Object.assign(base.tooltip, { trigger: 'item', formatter: p => `${p.name}<br/><b>${num(p.value)}</b> 条 · ${p.percent}%` }),
            legend: { bottom: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: CP.CHART_FS.micro, color: CP.themeColors().ink3 } },
            series: [{
                type: 'pie', radius: ['46%', '68%'], center: ['50%', '44%'],
                itemStyle: { borderColor: CP.themeColors().surface, borderWidth: 2 },
                label: { show: false },
                emphasis: { label: { show: true, formatter: '{b}\n{d}%', fontSize: CP.CHART_FS.xs, fontWeight: 600 } },
                data: rows.map(p => ({
                    name: p.platform, value: p.total,
                    itemStyle: PC[p.platform] ? { color: PC[p.platform] } : undefined
                }))
            }]
        }));
        drillTo(el, p => ({ platform: p.name }));
        CP.describeChart(el, '平台评论量占比：'
            + rows.map(p => `${p.platform} ${num(p.total)} 条`).join('，') + '。');
    }

    function renderLength(d) {
        const el = $('#chartLength');
        if (!el) return;
        if (!d.length_dist.length) { setState(el, 'empty'); return; }
        setOption(el, baseOpt({
            tooltip: Object.assign(themeBase().tooltip, {
                trigger: 'axis',
                formatter: ps => {
                    const r = d.length_dist[ps[0].dataIndex];
                    return `${r.label}<br/><b>${num(r.count)}</b> 条 · ${pct(r.ratio)}`;
                }
            }),
            xAxis: Object.assign({ type: 'category', data: d.length_dist.map(x => x.label), splitLine: { show: false } }, AX,
                { axisLabel: { color: CP.themeColors().ink4, fontSize: CP.CHART_FS.micro, interval: 0, rotate: 20 } }),
            yAxis: Object.assign({ type: 'value' }, AX),
            series: [{
                type: 'bar', barMaxWidth: 34, data: d.length_dist.map(x => x.count),
                itemStyle: { borderRadius: [4, 4, 0, 0], color: '#7BA6FF' }
            }]
        }));
    }

    function renderLike(d) {
        const s = d.like_stats || {};
        const stat = $('#likeStat');
        if (stat) {
            stat.innerHTML = `
                <div class="metrics-row">
                    <div class="metric"><div class="metric-name">平均点赞</div><div class="metric-value">${num(s.avg)}</div></div>
                    <div class="metric"><div class="metric-name">中位数</div><div class="metric-value">${num(s.median)}</div></div>
                    <div class="metric"><div class="metric-name">最高</div><div class="metric-value">${num(s.max)}</div></div>
                    <div class="metric"><div class="metric-name">零赞占比</div><div class="metric-value">${pct(s.zero_ratio)}</div></div>
                </div>`;
        }
        const el = $('#chartLikeTop');
        if (!el) return;
        if (!d.top_comments.length) { setState(el, 'empty'); return; }
        const rows = d.top_comments.slice(0, 6).reverse();
        setOption(el, baseOpt({
            grid: { left: 4, right: 44, top: 6, bottom: 2, containLabel: true },
            tooltip: Object.assign(themeBase().tooltip, {
                trigger: 'axis', axisPointer: { type: 'shadow' },
                formatter: ps => {
                    const r = rows[ps[0].dataIndex];
                    return `${CP.esc(r.snippet)}<br/><b>${num(r.like_count)}</b> 赞 · ${r.platform} · ${r.sentiment_label}`;
                }
            }),
            xAxis: Object.assign({ type: 'value' }, AX),
            yAxis: Object.assign({ type: 'category', data: rows.map((r, i) => 'TOP' + (rows.length - i)), splitLine: { show: false } }, AX),
            series: [{
                type: 'bar', data: rows.map(r => r.like_count || 0), barMaxWidth: 16,
                itemStyle: { borderRadius: [0, 4, 4, 0], color: '#3B63F3' },
                label: { show: true, position: 'right', fontSize: CP.CHART_FS.micro, color: CP.themeColors().ink4, formatter: p => num(p.value) }
            }]
        }));
    }

    /** 平台指标对照表。驾驶舱的「双平台概况」里有一张概览版（含最热主题），
     *  这里保留正面率与均长两列——那是详情视角，不是重复。 */
    function renderPlatTable(p) {
        const box = $('#platTable');
        if (!box) return;
        if (!p.platforms || !p.platforms.length) {
            box.innerHTML = '<div class="note note-muted">当前筛选范围内没有平台数据。</div>';
            return;
        }
        // 负面率差往往只有零点几个百分点，落在 3px 的柱差里根本读不出来：
        // 单独列成数值标注；两个平台的互动数值口径不同，注释常驻而不是只在后端给时才显示。
        const negLabels = p.platforms.map(x =>
            `${CP.esc(x.platform)} <b>${pct(x.negative_rate)}</b>`).join(' ／ ');
        box.innerHTML = `<div class="note note-muted" style="margin-bottom:8px;line-height:1.6;">
                <span>负面率（独立数值，不看柱高）：${negLabels}</span></div>
            <table class="table"><caption class="sr-only">双平台对比：评论量与情感结构；互动绝对值不可直接横向比较，请比较各平台内部的结构占比</caption><thead>
            <tr><th scope="col">平台</th><th scope="col">评论量</th><th scope="col">正面占比</th><th scope="col">负面占比</th><th scope="col">均长</th></tr></thead><tbody>
            ${p.platforms.map(x => `<tr>
                <td>${CP.esc(x.platform)}</td><td class="num">${num(x.total)}</td>
                <td class="num">${pct(x.positive_rate)}</td>
                <td class="num" style="color:${x.negative_rate >= 10 ? 'var(--neg)' : 'var(--ink-2)'}">${pct(x.negative_rate)}</td>
                <td class="num">${num(x.length_avg)}</td></tr>`).join('')}
            </tbody></table>
            <div class="note note-warn note-flat" style="margin-top:8px;">${CP.esc(p.comparability_note
                || '两个平台的点赞、评论等互动数值采集口径与展示机制不同，绝对数值不可直接横向比较；请比较各平台内部的结构占比。')}</div>`;
    }

    /* ================= 主题热度分析 ================= */

    function renderDims(t) {
        const el = $('#chartDims');
        if (!el) return;
        if (!t.ranking.length) { setState(el, 'empty'); return; }
        const rows = t.ranking.slice().reverse();
        setOption(el, baseOpt({
            grid: { left: 4, right: 44, top: 8, bottom: 2, containLabel: true },
            xAxis: Object.assign({ type: 'value' }, AX),
            yAxis: Object.assign({ type: 'category', data: rows.map(r => r.dimension), splitLine: { show: false } }, AX,
                { axisLabel: { color: CP.themeColors().ink2, fontSize: CP.CHART_FS.xs } }),
            series: [{
                type: 'bar', data: rows.map(r => r.count), barMaxWidth: 18,
                itemStyle: { borderRadius: [0, 5, 5, 0], color: '#3B63F3' },
                label: { show: true, position: 'right', fontSize: CP.CHART_FS.micro, color: CP.themeColors().ink3, formatter: p => num(p.value) }
            }]
        }));
        drillTo(el, p => ({ dimension: p.name }));
        CP.describeChart(el, '八主题评论量排行（多标签计数，合计可大于评论总数）：'
            + t.ranking.slice(0, 3).map(r => `${r.dimension} ${num(r.count)} 条`).join('，')
            + `。共 ${t.ranking.length} 个主题，分母 ${num(t.meta && t.meta.sample_size)} 条评论。`);
    }

    function renderPlatDim(p) {
        const el = $('#chartPlatDim');
        if (!el) return;
        if (!p.platforms.length) { setState(el, 'empty'); return; }
        const names = p.platforms.map(x => x.platform);
        const PC = PLATFORM_COLOR();
        setOption(el, baseOpt({
            legend: { top: 0, right: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: CP.CHART_FS.micro, color: CP.themeColors().ink3 } },
            grid: { left: 4, right: 12, top: 26, bottom: 2, containLabel: true },
            xAxis: Object.assign({ type: 'category', data: p.dimension_matrix.map(d => d.dimension), splitLine: { show: false } }, AX,
                // 两张图并排后每卡约半屏宽，7 组双柱各有 70px 左右，28° 够用了；
                // 转太多反而让 4 字主题名竖起来，读起来要歪头
                { axisLabel: { color: CP.themeColors().ink4, fontSize: CP.CHART_FS.micro, interval: 0, rotate: 28 } }),
            yAxis: Object.assign({ type: 'value' }, AX),
            series: names.map(n => ({
                name: n, type: 'bar', barMaxWidth: 18,
                data: p.dimension_matrix.map(d => d[n] || 0),
                itemStyle: { borderRadius: [3, 3, 0, 0], color: PC[n] }
            }))
        }));
    }

    /** 正 / 负面两朵词云。
     *
     * 各用各的分母：正面词只在正面评论里数，负面词只在负面评论里数。
     * 如果用全量混合词云再按语气着色，负面那一侧会被中性陈述的常用词占满，
     * "学生到底在骂什么"根本看不出来——而这是本页唯一要回答的问题。
     *
     * 每个词都可点：直接跳到证据中心，带该词 + 对应情感，看到的是包含这个词的原始评论。
     * 不在这里另拼一个"证据小列表"，是因为那等于做出第二套检索口径，
     * 两个地方的条数一旦对不上，读者就不知道该信哪个。
     */
    function renderPolarityWords(d) {
        const clouds = [
            { el: $('#chartWordPos'), words: d.positive || [], total: d.positive_total || 0,
              sentiment: 'positive', color: '#12A150', name: '正面',
              sub: '#posWordSub', badge: '#posWordBadge' },
            { el: $('#chartWordNeg'), words: d.negative || [], total: d.negative_total || 0,
              sentiment: 'negative', color: '#E5484D', name: '负面',
              sub: '#negWordSub', badge: '#negWordBadge' }
        ];
        clouds.forEach(c => {
            if (!c.el) return;
            const subEl = $(c.sub), badgeEl = $(c.badge);
            if (subEl) {
                // 只留分母与取样口径；"点击词语可追溯原文"是操作提示，
                // 词云本身有指针光标，写出来只是把数据挤到第二行。
                subEl.textContent = `${c.name}评论 ${num(c.total)} 条 · 取前 ${c.words.length} 个高频实词`;
            }
            if (badgeEl) badgeEl.textContent = `${num(c.total)} 条`;
            if (!c.words.length) {
                setState(c.el, 'empty', `${c.name}评论不足以统计高频词。`);
                return;
            }
            drawPolarityCloud(c);
        });
    }

    function drawPolarityCloud(c) {
        const el = c.el, words = c.words;
        const draw = async () => {
            const ready = (await ensureWordCloud()) === true;
            try {
                if (!ready) throw new Error('wordcloud plugin unavailable');
                setOption(el, Object.assign(themeBase(), {
                    tooltip: Object.assign(themeBase().tooltip, {
                        formatter: p => {
                            const w = words.find(x => x.name === p.name) || {};
                            return `<b>${CP.esc(p.name)}</b><br/>出现 ${num(p.value)} 次 · `
                                + `${num(w.docs || 0)} 条评论提到<br/>点击可查看原始评论`;
                        }
                    }),
                    series: [{
                        type: 'wordCloud', shape: 'circle',
                        left: 0, top: 0, right: 0, bottom: 0,
                        sizeRange: [16, 70], rotationRange: [0, 0], gridSize: 6,
                        textStyle: { fontWeight: 600, color: c.color },
                        emphasis: { textStyle: { textShadowBlur: 10, textShadowColor: c.color } },
                        data: words.map(w => ({ name: w.name, value: w.value }))
                    }]
                }));
            } catch (e) {
                // 插件没加载出来时退化为条形图：至少还剩"哪些词最常被说"这个信息
                const rows = words.slice(0, 12).reverse();
                setOption(el, baseOpt({
                    grid: { left: 4, right: 44, top: 8, bottom: 2, containLabel: true },
                    xAxis: Object.assign({ type: 'value' }, AX),
                    yAxis: Object.assign({ type: 'category', data: rows.map(w => w.name), splitLine: { show: false } }, AX,
                        { axisLabel: { color: CP.themeColors().ink2, fontSize: CP.CHART_FS.micro } }),
                    series: [{
                        type: 'bar', barMaxWidth: 14,
                        data: rows.map(w => ({ name: w.name, value: w.value })),
                        itemStyle: { borderRadius: [0, 4, 4, 0], color: c.color },
                        label: { show: true, position: 'right', fontSize: CP.CHART_FS.micro,
                                 color: CP.themeColors().ink4, formatter: p => num(p.value) }
                    }]
                }));
            }
            // 绑定必须放在 setOption **之后**。词云是"插件到位才开始画"的异步流程，
            // 若在它前面调 drillTo，实例还不存在，onChart 会把回调压进 pendingHooks，
            // 而那条队列只在 setOption 被延迟时才被唤醒——先画后绑，才不会静默失效。
            drillTo(el, p => ({ keyword: p.name, sentiment: c.sentiment }));
        };
        if (typeof CP.whenVisible === 'function') CP.whenVisible(el, draw);
        else draw();
    }

    /* ================= 情感分析 ================= */

    function renderSentOverview(s) {
        const el = $('#chartSentOverview');
        if (!el) return;
        const dist = (s.distribution || []).filter(d => d.value > 0);
        const counts = s.counts || {};
        const denom = s.denominator || 0;
        const box = $('#sentMetrics');

        if (!dist.length) {
            setState(el, 'empty', '当前范围内没有已标注情感的评论。');
            if (box) box.innerHTML = '<div class="note note-muted" style="padding:12px;">暂无数据。</div>';
            return;
        }

        const base = themeBase();
        setOption(el, Object.assign(base, {
            tooltip: Object.assign(base.tooltip, {
                trigger: 'item',
                formatter: p => `${p.name}<br/><b>${num(p.value)}</b> 条 · ${p.percent}%`
            }),
            legend: { bottom: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: CP.CHART_FS.micro, color: CP.themeColors().ink3 } },
            series: [{
                type: 'pie', radius: ['48%', '70%'], center: ['50%', '44%'],
                itemStyle: { borderColor: CP.themeColors().surface, borderWidth: 2 },
                label: { show: false },
                emphasis: { label: { show: true, formatter: '{b}\n{d}%', fontSize: CP.CHART_FS.xs, fontWeight: 600 } },
                data: dist.map(d => ({ name: d.name, value: d.value, itemStyle: { color: SENT_COLOR[d.key] } }))
            }]
        }));
        drillTo(el, p => {
            const hit = dist.find(d => d.name === p.name);
            return hit ? { sentiment: hit.key } : null;
        });

        if (box) {
            const rate = k => denom ? Math.round((counts[k] || 0) / denom * 1000) / 10 : 0;
            box.innerHTML = `
                <div class="metric"><div class="metric-name">正面 · ${rate('positive')}%</div>
                    <div class="metric-value" style="color:var(--pos)">${num(counts.positive || 0)}</div></div>
                <div class="metric"><div class="metric-name">中性 · ${rate('neutral')}%</div>
                    <div class="metric-value">${num(counts.neutral || 0)}</div></div>
                <div class="metric"><div class="metric-name">负面 · ${rate('negative')}%</div>
                    <div class="metric-value" style="color:var(--neg)">${num(counts.negative || 0)}</div></div>
                <div class="metric"><div class="metric-name">有效评论（分母）</div>
                    <div class="metric-value">${num(denom)}</div></div>`;
        }
        const badge = $('#sentBadge');
        if (badge) badge.textContent = `负面率 ${denom ? (counts.negative || 0) / denom * 100 : 0}%`.replace(/(\.\d)\d+/, '$1');
    }

    function renderDimSent(t) {
        const el = $('#chartDimSent');
        if (!el) return;
        if (!t.ranking.length) { setState(el, 'empty'); return; }
        // 不写死条数：以前固定 slice(0, 7)，加了「实习就业」之后第八个主题
        // 在这张图里根本不存在，页面上其它图都在说八类，只有它说七类。
        const rows = t.ranking.slice(0, CP.DIMENSIONS.length || t.ranking.length);
        const pctOf = (v, total) => total ? Math.round(v / total * 1000) / 10 : 0;
        setOption(el, baseOpt({
            tooltip: Object.assign(themeBase().tooltip, {
                trigger: 'axis', axisPointer: { type: 'shadow' },
                formatter: ps => {
                    const r = rows[ps[0].dataIndex];
                    return `<b>${r.dimension}</b>（${num(r.count)} 条）<br/>` +
                        ps.map(p => `${p.marker}${p.seriesName} ${p.value}%`).join('<br/>');
                }
            }),
            legend: { top: 0, right: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: CP.CHART_FS.micro, color: CP.themeColors().ink3 } },
            grid: { left: 4, right: 10, top: 26, bottom: 2, containLabel: true },
            xAxis: Object.assign({ type: 'category', data: rows.map(r => r.dimension), splitLine: { show: false } }, AX,
                { axisLabel: { color: CP.themeColors().ink4, fontSize: CP.CHART_FS.micro, interval: 0, rotate: 24 } }),
            yAxis: Object.assign({ type: 'value', max: 100, axisLabel: { formatter: '{value}%', color: CP.themeColors().ink4, fontSize: CP.CHART_FS.micro } }, AX),
            series: [
                { name: '正面', type: 'bar', stack: 'x', data: rows.map(r => pctOf(r.positive, r.count)), itemStyle: { color: '#16A34A' }, barMaxWidth: 26 },
                { name: '中性', type: 'bar', stack: 'x', data: rows.map(r => pctOf(r.count - r.positive - r.negative, r.count)), itemStyle: { color: '#CBD5E1' } },
                { name: '负面', type: 'bar', stack: 'x', data: rows.map(r => pctOf(r.negative, r.count)), itemStyle: { color: '#E5484D', borderRadius: [3, 3, 0, 0], decal: CP.NEG_DECAL } }
            ]
        }));
    }

    function renderPareto(t) {
        const el = $('#chartPareto');
        if (!el) return;
        const all = t.negative_reasons || [];
        // 「未细分原因」只是兜底桶，不参与帕累托主曲线，避免一个兜底类压过真实原因
        const rows = all.filter(r => r.reason !== '未细分原因');
        const rest = all.find(r => r.reason === '未细分原因');
        if (!rows.length) { setState(el, 'empty', '当前范围内没有可归因的负面评论。'); return; }
        const total = rows.reduce((a, b) => a + b.count, 0);
        let cum = 0;
        const cumData = rows.map(r => { cum += r.count; return total ? Math.round(cum / total * 1000) / 10 : 0; });
        const note = rest ? `另有 ${num(rest.count)} 条负面评论未命中细分规则，未计入上图。` : '';
        el.parentElement.querySelector('.card-sub').textContent =
            `柱：提及次数；线：累计占比${note ? ' · ' + note : ''}`;
        setOption(el, baseOpt({
            tooltip: Object.assign(themeBase().tooltip, {
                trigger: 'axis',
                formatter: ps => {
                    const r = rows[ps[0].dataIndex];
                    return `<b>${r.reason}</b><br/>提及 ${num(r.count)} 条<br/>占负面评论 ${pct(r.share_of_negative)}`;
                }
            }),
            xAxis: Object.assign({ type: 'category', data: rows.map(r => r.reason), splitLine: { show: false } }, AX,
                // 负面原因本身是短语（「食宿与费用负担」这种 6-8 字），26° 旋转在半宽卡里
                // 仍然会让相邻标签粘在一起，所以多转一些并缩小半号字
                { axisLabel: { color: CP.themeColors().ink4, fontSize: CP.CHART_FS.micro, interval: 0, rotate: rows.length > 5 ? 40 : 0 } }),
            yAxis: [
                Object.assign({ type: 'value' }, AX),
                Object.assign({ type: 'value', max: 100, splitLine: { show: false },
                    axisLabel: { formatter: '{value}%', color: CP.themeColors().ink4, fontSize: CP.CHART_FS.micro } }, { axisLine: { show: false }, axisTick: { show: false } })
            ],
            series: [
                { type: 'bar', data: rows.map(r => r.count), barMaxWidth: 30, itemStyle: { borderRadius: [4, 4, 0, 0], color: '#E5484D' } },
                { type: 'line', yAxisIndex: 1, data: cumData, smooth: true, symbol: 'circle', symbolSize: 5, lineStyle: { color: '#F59E0B', width: 2 }, itemStyle: { color: '#F59E0B' } }
            ]
        }));
    }

    /* ================= 时间趋势分析 ================= */

    /** 评论量（正/中/负堆叠）+ 负面率（右轴折线）。
     *
     * 和驾驶舱那张趋势图是两种视角：驾驶舱看"总量多不多"（总量线 + 负面量柱），
     * 这里看"结构变没变坏"（内部构成 + 负面率）。同一份 /api/analytics/trend 数据。
     */
    /* ---------------- 014 通用校历参考带 ----------------
     * 以下是「大多数学校的通用学期节奏」，不是本校发生过的事件、也不来自数据：
     * 开学 / 军训在 9 月、考试周在 1 月与 6 月、毕业季在 6 月、
     * 寒假在 1–2 月、暑假在 7–8 月。只用来给时间轴一个可对照的背景，
     * 绝不能写成「本校在 X 月发生了 Y」，也不表示这些时段的舆情由事件引起。
     * 数据点少时（< 6 个时段）不往图上叠淡色区间，只在图表上方给一条可见图例带，
     * 免得色块盖住本就稀疏的柱子。 */
    const SCHOOL_CALENDAR = [
        { months: [1], label: '考试周 / 寒假' },
        { months: [2], label: '寒假' },
        { months: [6], label: '考试周 / 毕业季' },
        { months: [7, 8], label: '暑假' },
        { months: [9], label: '开学 / 军训' }
    ];

    function monthOfBucket(b) {
        const s = String((b && (b.bucket || b.label)) || '');
        let m = s.match(/(?:^|\D)(\d{4})-(\d{1,2})(?:\D|$)/);
        if (m) return Number(m[2]);
        m = s.match(/(\d{1,2})\s*月/);
        return m ? Number(m[1]) : 0;
    }

    /* 图表上方那条事件图例带（幂等）。只列数据覆盖到的月份对应的通用校历事件，
       没覆盖到的月份不写——否则像在宣称「这些数据都发生在考试周」。 */
    function renderCalendarBand(bs) {
        const el = $('#chartTrendFlow');
        if (!el || !el.parentNode) return;
        let band = document.getElementById('cpCalBand');
        if (!band) {
            band = document.createElement('div');
            band.id = 'cpCalBand';
            band.className = 'note note-muted';
            band.style.cssText = 'margin-bottom:8px;line-height:1.7;overflow-wrap:anywhere;';
            el.parentNode.insertBefore(band, el);
        }
        band.hidden = false;
        const months = Array.from(new Set(bs.map(monthOfBucket).filter(m => m >= 1 && m <= 12)));
        const items = SCHOOL_CALENDAR.filter(e => e.months.some(m => months.indexOf(m) >= 0));
        const chips = items.length
            ? items.map(e => `<span class="badge" style="margin-right:6px;">${CP.esc(e.label)}（${e.months.join('、')} 月）</span>`).join('')
            : '<span>当前时段未覆盖常见校历节点。</span>';
        // 100：chips 已经逐项列出月份，下面不再用长句复述一遍，只留一行诚实声明
        band.innerHTML = `<span><b>通用校历参考（非本校事实，仅作时间对照）：</b>${chips}`
            + '<span style="display:block;font-size:var(--fs-micro);color:var(--ink-4);margin-top:4px;">'
            + '事件为通用学期常识，不代表本校安排，也不表示这些时段的舆情由事件引起。</span></span>';
    }

    /* 图上那一层淡色带：只标寒暑假，且仅在数据点够多时叠，避免遮挡稀疏柱子。 */
    function calendarMarkArea(cats, bs) {
        if (cats.length < 6) return null;
        const breakMonths = [1, 2, 7, 8];
        const pairs = [];
        // 显式标成 number|null：只在 forEach 回调里赋值的变量，tsc 推断不出
        // 「某个时刻已经是数字」，会把 cats[start] 报成「用 null 当索引」。
        /** @type {number|null} */
        let start = null;
        /** @type {number|null} */
        let prev = null;
        bs.forEach((b, i) => {
            const m = monthOfBucket(b);
            if (breakMonths.indexOf(m) >= 0) {
                if (start === null) start = i;
                prev = i;
            } else if (start !== null && prev !== null) {
                pairs.push([{ xAxis: cats[start] }, { xAxis: cats[prev] }]);
                start = null; prev = null;
            }
        });
        // start / prev 成对赋值，收起时成对置 null；显式再判一次 prev，
        // 既让 tsc 通过，也避免理论上 start 有值而 prev 为 null 时索引成 cats[null]。
        if (start !== null && prev !== null) {
            pairs.push([{ xAxis: cats[start] }, { xAxis: cats[prev] }]);
        }
        if (!pairs.length) return null;
        return { silent: true, itemStyle: { color: 'rgba(100,116,139,0.10)' },
                 label: { show: false }, data: pairs };
    }

    function renderTrendFlow(t) {
        const el = $('#chartTrendFlow');
        if (!el) return;
        const subEl = $('#trendSub'), noteEl = $('#trendNote');
        if (t.empty || !t.buckets.length) {
            setState(el, 'empty', '当前筛选范围内没有可绘制的时间记录。');
            if (noteEl) noteEl.classList.add('is-hidden');
            const band = document.getElementById('cpCalBand');
            if (band) band.hidden = true;
            return;
        }
        const bs = t.buckets;
        const cats = bs.map(b => b.label);
        // 无采集记录的时段负面率留空而不是记 0：0% 会被读成"一条负面都没有"，
        // 但真实情况是"这天没采到数据"，两者结论完全相反。
        const rate = bs.map(b => b.total ? Math.round(b.negative / b.total * 1000) / 10 : null);
        const gname = { day: '日', week: '周', month: '月' }[t.granularity] || '时段';
        const base = themeBase();
        // 014 通用校历参考带：图例常驻在图表上方，淡色区间只在数据点够多时才叠到图上
        renderCalendarBand(bs);
        const calArea = calendarMarkArea(cats, bs);

        setOption(el, Object.assign(base, {
            tooltip: Object.assign(base.tooltip, {
                trigger: 'axis',
                formatter: ps => {
                    const b = bs[ps[0].dataIndex];
                    const r = b.total ? (b.negative / b.total * 100).toFixed(1) : '—';
                    return `<b>${b.bucket}</b><br/>评论 ${num(b.total)} 条<br/>`
                        + `正面 ${num(b.positive)} · 中性 ${num(b.neutral)} · `
                        + `<span style="color:var(--neg)">负面 ${num(b.negative)}</span><br/>`
                        + `负面率 <b>${r}${b.total ? '%' : ''}</b>`;
                }
            }),
            legend: { top: 0, right: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: CP.CHART_FS.micro, color: CP.themeColors().ink3 } },
            grid: { left: 4, right: 12, top: 30, bottom: 4, containLabel: true },
            xAxis: Object.assign({ type: 'category', data: cats, splitLine: { show: false } }, AX,
                { axisLabel: { color: CP.themeColors().ink4, fontSize: CP.CHART_FS.micro, hideOverlap: true } }),
            yAxis: [
                Object.assign({ type: 'value', name: '条', nameTextStyle: { color: CP.themeColors().ink4, fontSize: CP.CHART_FS.micro } }, AX),
                Object.assign({ type: 'value', max: 100, splitLine: { show: false } },
                    { axisLabel: { formatter: '{value}%', color: CP.themeColors().ink4, fontSize: CP.CHART_FS.micro }, axisLine: { show: false }, axisTick: { show: false } })
            ],
            series: [
                { name: '正面', type: 'bar', stack: 's', data: bs.map(b => b.positive), barMaxWidth: 26, itemStyle: { color: '#16A34A' } },
                { name: '中性', type: 'bar', stack: 's', data: bs.map(b => b.neutral), itemStyle: { color: '#CBD5E1' } },
                { name: '负面', type: 'bar', stack: 's', data: bs.map(b => b.negative), itemStyle: { color: '#E5484D', borderRadius: [3, 3, 0, 0], decal: CP.NEG_DECAL } },
                Object.assign({
                    name: '负面率', type: 'line', yAxisIndex: 1, data: rate,
                    smooth: true, connectNulls: false, symbol: 'circle', symbolSize: 5,
                    lineStyle: { color: '#F59E0B', width: 2 }, itemStyle: { color: '#F59E0B' }
                }, calArea ? { markArea: calArea } : {})
            ]
        }));

        if (subEl) {
            // 图例已经标了"正面/中性/负面/负面率"，同一句话在标题下再说一遍是噪音；
            // 这里只保留聚合粒度与缺失时段——这两个是图上读不出来的。
            subEl.textContent = `按${gname}聚合 ${bs.length} 个时段`
                + (t.zero_buckets ? ` · ${t.zero_buckets} 个时段无采集记录` : '');
        }
        if (noteEl) {
            // 负面率的分母是该时段评论量。样本只有一两条时，一条负面就能把它顶到 100%，
            // 图上会出现刺眼的尖峰。不提示的话，读者会当成"这个月舆情恶化了"。
            const tips = [];
            if (t.note) tips.push(t.note);
            if (bs.some(b => b.total > 0 && b.total < 5)) {
                tips.push('部分时段样本不足 5 条，其负面率容易被单条评论拉到满值，请结合柱高一起看。');
            }
            if (tips.length) {
                // B5：口径说明常驻可见——去掉 data-hint、加 .scope-line，文字包 <span>（.note 是 flex）
                noteEl.removeAttribute('data-hint');
                noteEl.classList.add('scope-line');
                noteEl.classList.remove('is-hidden');
                noteEl.innerHTML = '<span>' + CP.esc(tips.join(' ')) + '</span>';
            } else {
                noteEl.classList.add('is-hidden');
            }
        }
    }

    function renderHour(d) {
        const el = $('#chartHour');
        if (!el) return;
        if (!d.hour.some(v => v > 0)) { setState(el, 'empty'); return; }
        setOption(el, baseOpt({
            tooltip: Object.assign(themeBase().tooltip, {
                trigger: 'axis', formatter: ps => `${ps[0].axisValue}点<br/><b>${num(ps[0].value)}</b> 条`
            }),
            xAxis: Object.assign({ type: 'category', data: d.hour.map((_, i) => i + '时'), splitLine: { show: false } }, AX,
                { axisLabel: { color: CP.themeColors().ink4, fontSize: CP.CHART_FS.micro, interval: 2 } }),
            yAxis: Object.assign({ type: 'value' }, AX),
            series: [{
                type: 'line', smooth: true, symbol: 'none', data: d.hour,
                lineStyle: { width: 2, color: '#3B63F3' },
                areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                    colorStops: [{ offset: 0, color: 'rgba(59,99,243,.22)' }, { offset: 1, color: 'rgba(59,99,243,0)' }] } }
            }]
        }));
    }

    function renderWeek(d) {
        const el = $('#chartWeek');
        if (!el) return;
        if (!d.weekday.some(v => v > 0)) { setState(el, 'empty'); return; }
        setOption(el, baseOpt({
            xAxis: Object.assign({ type: 'category', data: d.weekday_names, splitLine: { show: false } }, AX),
            yAxis: Object.assign({ type: 'value' }, AX),
            series: [{
                type: 'bar', data: d.weekday, barMaxWidth: 32,
                itemStyle: { borderRadius: [5, 5, 0, 0], color: '#7BA6FF' }
            }]
        }));
    }

    /* ================= 加载 ================= */

    // 时间趋势页的粒度由页面自己持有：不写进全局 state，
    // 因为它是"这张图怎么看"，不是"看哪些数据"，切页面时不该被带过去。
    const view = { gran: 'auto' };
    let trendInFlight = 0;

    async function loadTrend(gran) {
        const el = $('#chartTrendFlow');
        if (!el) return;
        const seq = ++trendInFlight;
        const params = CP.filterParams();
        if (gran && gran !== 'auto') params.granularity = gran;
        try {
            const t = await api('/api/analytics/trend', params);
            if (seq !== trendInFlight) return;      // 连点粒度按钮时只认最后一次
            renderTrendFlow(t);
        } catch (e) {
            console.warn('[insights] 趋势数据加载失败：' + e.message);
            if (seq === trendInFlight) setState(el, 'error', '趋势图暂时未加载，请稍后刷新页面重试。', () => loadTrend(gran));
        }
    }

    async function loadAll() {
        const page = detectPage();
        if (!page) return;
        const p = CP.filterParams();
        try {
            if (page === 'overview') {
                const [dist, sent, plat] = await Promise.all([
                    api('/api/analytics/distributions', p),
                    api('/api/analytics/sentiment', p),
                    api('/api/analytics/platform-compare', p)
                ]);
                renderPlatformPie(sent);
                renderLength(dist);
                renderLike(dist);
                renderPlatTable(plat);
                describeCharts();
                loadInsights();
            } else if (page === 'topics') {
                const [topics, plat] = await Promise.all([
                    api('/api/analytics/topics', p),
                    api('/api/analytics/platform-compare', p)
                ]);
                renderDims(topics);
                renderPlatDim(plat);
                describeCharts();
                loadPageInsight('topics');
            } else if (page === 'sentiment') {
                const [sent, topics, words] = await Promise.all([
                    api('/api/analytics/sentiment', p),
                    api('/api/analytics/topics', p),
                    api('/api/analytics/polarity-words', p)
                ]);
                renderSentOverview(sent);
                renderDimSent(topics);
                renderPareto(topics);
                renderPolarityWords(words);
                describeCharts();
                loadPageInsight('sentiment');
                loadWordInsight();
            } else if (page === 'timeline') {
                const dist = await api('/api/analytics/distributions', p);
                renderHour(dist);
                renderWeek(dist);
                await loadTrend(view.gran);
                describeCharts();
                loadPageInsight('timeline');
            }
        } catch (e) {
            console.warn('[insights] 洞察数据加载失败：' + e.message);
            const failMsg = '图表暂时未加载，请稍后刷新页面重试。';
            toast(failMsg, 'err');
            (CHART_IDS[page] || []).forEach(id => {
                const el = $(id);
                if (el) setState(el, 'error', failMsg, loadAll);
            });
        }
    }

    document.addEventListener('cp:ready', loadAll);
    document.addEventListener('cp:filter', loadAll);
    document.addEventListener('cp:dataset', loadAll);
    document.addEventListener('cp:theme', loadAll);
    // 只 resize 已创建的图：以前这里对每个 .chart 调 CP.chart()，
    // 等于把惰性初始化又全量触发了，一次窗口拖动就把所有图全建出来。
    window.addEventListener('resize', () => CP.resizeCharts());

    const granTabs = $('#granTabs');
    if (granTabs) {
        granTabs.addEventListener('click', e => {
            const b = e.target.closest('[data-gran]');
            if (!b) return;
            view.gran = b.dataset.gran;
            CP.$$('#granTabs .chip').forEach(x => x.classList.toggle('on', x === b));
            loadTrend(view.gran);
        });
    }
})();
