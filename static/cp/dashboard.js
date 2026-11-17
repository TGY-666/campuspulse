/* PAGE-01 舆情总览 */
(function () {
    'use strict';
    const { $, esc, state, api, num, pct, setState, setOption, themeBase, axisCommon, toast } = CP;

    const view = { range: 'all', gran: 'auto' };
    let trendData = null;

    /* ---------------- KPI ----------------
     * 五格，不是六格。「覆盖平台 2 个」是这一屏唯一的常量：平台只有抖音和小红书，
     * 两个数下面「双平台概况」里逐平台列着，筛选栏也挂着两枚同名的芯片 ——
     * 首屏最贵的一格不该放一个永远不变的数字。删掉它以后五格正好铺满一行。
     *
     * 两条占比进度条统一走中性 --brand 阶梯：CSS 里 .kpi-bar > i 也强制成
     * --brand（配 nth-child 的 opacity 递减）。这里不再注入 --pos / --neg，
     * 免得把「红＝负面」的语义色拿来当进度条，改颜色时要同时记起两处。
     */
    const SENT_VAR = { positive: 'var(--pos)', neutral: 'var(--neu)', negative: 'var(--neg)' };
    /* 研判等级 → 颜色类（与 /departments 的 LEVEL_CLS 同值：同一档不许在
       两页显示成两种颜色）。 */
    const LEVEL_CLS = { '高': 'high', '中高': 'midhigh', '中': 'mid', '低': 'low' };


    function renderKpi(o) {
        const topCard = {
            label: '讨论最多主题', value: o.top_dimension || '—', unit: '',
            foot: o.top_dimension ? `${num(o.top_dimension_count)} 条提及（多标签）` : '暂无数据',
            bar: null, small: true, tone: 'neutral'
        };
        const likeCard = {
            label: '总点赞量', value: num(o.total_likes), unit: '赞',
            // 095：口径常驻在可见脚注里，不再只塞进 title；只数点赞，别把评论/转发/收藏算进来
            foot: `平均 ${num(o.like_avg)} 赞 / 条 · 仅统计点赞，不含评论、转发与收藏`,
            title: '总点赞量为当前筛选范围内所有评论的点赞数之和',
            bar: null, tone: 'neutral'
        };
        // 卡片是异构的：只有部分带 title / small / bar。标成 any[] 是为了
        // 让 tsc 别再拿「某张卡没有 title」当错误，渲染分支自己按存在性判断。
        /** @type {any[]} */
        const cards = [];

        if (state.filters.sentiment.length) {
            /* 筛过情感时，"正面 0.0% / 负面 100.0%"是筛选的数学结果：两张占比卡
             * 说的都是同一件事，而且说三遍（KPI 两张 + 情感条 100% + AI 复述）。
             * 换成两张回答"这一块有多大"的卡：条数与占同口径全量的比例。 */
            const scope = Number(o.scope_total) || 0;
            const share = scope ? Math.round(o.total / scope * 1000) / 10 : 0;
            cards.push({
                label: '有效评论', value: num(o.total), unit: '条',
                foot: `同口径全量 ${num(scope)} 条`, bar: null, tone: 'neutral'
            });
            cards.push({
                label: '占同口径全量', value: pct(share), unit: '',
                foot: `${num(o.total)} / ${num(scope)} 条`, bar: null, tone: 'neutral'
            });
        } else {
            cards.push({
                label: '有效评论', value: num(o.total), unit: '条',
                foot: `情感标注分母 ${num(o.denominator)} 条`, bar: null, tone: 'neutral'
            });
            cards.push({
                label: '正面占比', value: pct(o.positive_rate), unit: '',
                foot: `${num(o.positive)} / ${num(o.denominator)} 条`, bar: null, tone: 'pos'
            });
            cards.push({
                label: '负面占比', value: pct(o.negative_rate), unit: '',
                foot: `${num(o.negative)} / ${num(o.denominator)} 条`, bar: null, tone: 'neg'
            });
        }
        cards.push(topCard, likeCard);

        /* 第 19 轮：**删掉卡顶彩条**。它的长度就是大数字本身的比例，读一遍百分比就够了；
           多出来的还有一句「彩条表示指标类别、不是情感编码」的说明替它辩护——
           一个需要自己解释自己的装饰，就是该删的装饰。data-tone 仍保留，它还承担卡片状态。 */
        $('#kpiRow').innerHTML = cards.map(c => `
            <div class="card kpi" data-tone="${c.tone || 'neutral'}"${c.title ? ` title="${CP.esc(c.title)}"` : ''}>
                <div class="kpi-label">${c.label}</div>
                <div class="kpi-value" style="${c.small ? 'font-size:var(--fs-xl);letter-spacing:-.4px;' : ''}">
                    ${CP.esc(c.value)}<span class="kpi-unit">${c.unit}</span>
                </div>
                <div class="kpi-foot">${c.foot}</div>
                ${c.bar ? `<div class="kpi-bar">${c.bar.map(([color, v]) =>
                    `<i style="width:${Math.min(100, Math.max(0, Number(v) || 0))}%;background:${color}"></i>`).join('')}</div>` : ''}
            </div>`).join('');
    }

    /* ---------------- 趋势 ----------------
     * 堆积柱：柱高 = 该时段评论总量，内部三段 = 正 / 中 / 负。
     *
     * 原先是"总量折线 + 负面细柱"两条道：总量走一条实线、负面量是贴着底的一根矮柱，
     * 读者想知道"这个月负面占几成"得自己拿柱高去比线上的高度。堆起来之后同一个位置
     * 同时读出"总量多少"和"负面占多大比重"，切到「全部」那种几十个时段的视图时
     * 也不至于只剩一条起伏的线、看不出内部结构。
     *
     * 右轴的负面率折线不往这里搬：那条线在「时间趋势分析」页，用来逐时段做结构诊断；
     * 驾驶舱的定位是"30 秒建立整体认知"，两页都画同一条线就等于同一张图放了两遍。
     */
    function renderTrend(d) {
        trendData = d;
        const el = $('#chartTrend');
        if (d.empty) { setState(el, 'empty', '当前筛选范围内没有评论记录。'); return; }

        const base = themeBase();
        const axis = axisCommon();
        const cats = d.buckets.map(b => b.label);
        const C = CP.SENTIMENT_COLOR;
        // 26 是上限而不是固定宽度：ECharts 先按类目带宽算柱宽再取小值，
        // 所以"近 12 月"（13 个时段）能看到有厚度的柱子，
        // 切到"全部"（80+ 个时段）时自动被带宽压细，不会互相挤在一起
        const part = (name, key, color, extra) => Object.assign({
            name, type: 'bar', stack: 's',
            data: d.buckets.map(b => b[key] || 0),
            barMaxWidth: 26,
            itemStyle: { color }
        }, extra || {});

        setOption(el, Object.assign(base, {
            tooltip: Object.assign(base.tooltip, {
                trigger: 'axis',
                axisPointer: { type: 'shadow' },
                formatter: ps => {
                    const b = d.buckets[ps[0].dataIndex];
                    // 分母为 0 的时段不给 0%，写"—"：那几天没采到数据，
                    // 显示 0% 会被读成"一条负面都没有"，两者结论相反
                    const rate = b.total ? (b.negative / b.total * 100).toFixed(1) + '%' : '—';
                    return `<b>${b.bucket}</b><br/>评论总量：${num(b.total)} 条<br/>` +
                        `正面 ${num(b.positive)} · 中性 ${num(b.neutral)} · <span style="color:var(--neg)">负面 ${num(b.negative)}</span><br/>` +
                        `负面占比 <b>${rate}</b>`;
                }
            }),
            legend: { top: 0, right: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: CP.CHART_FS.micro, color: CP.themeColors().ink3 } },
            grid: { left: 4, right: 12, top: 30, bottom: 6, containLabel: true },
            xAxis: Object.assign({ type: 'category', data: cats, splitLine: { show: false } }, axis, {
                axisLabel: { color: CP.themeColors().ink4, fontSize: CP.CHART_FS.micro, hideOverlap: true }
            }),
            yAxis: Object.assign({ type: 'value', name: '条', nameTextStyle: { color: CP.themeColors().ink4, fontSize: CP.CHART_FS.micro } }, axis),
            series: [
                part('正面', 'positive', C['正面']),
                part('中性', 'neutral', C['中性']),
                // 圆角只给最上面一段：三段叠起来时柱顶就是负面那一段的顶，
                // 给下面两段加圆角会在柱身中间画出两道假的分界线
                part('负面', 'negative', C['负面'], { itemStyle: { color: C['负面'], borderRadius: [3, 3, 0, 0], decal: CP.NEG_DECAL } })
            ]
        }));
        const gname = { day: '日', week: '周', month: '月' }[d.granularity];
        // 读屏软件读不到 canvas，这张图对它就等于空白。用页面上同一个数据说一句
        // 人话：区间、时段数、合计、负面量、峰值在哪——全是这页已有的数字。
        const negTotal = d.buckets.reduce((n, b) => n + (b.negative || 0), 0);
        const peak = d.buckets.reduce((a, b) => ((b.total || 0) > (a.total || 0) ? b : a), d.buckets[0]);
        CP.describeChart(el, `时间趋势图（按${gname}聚合）：${cats[0]} 至 ${cats[cats.length - 1]}，`
            + `共 ${d.buckets.length} 个时段，合计 ${num(d.total)} 条，其中负面 ${num(negTotal)} 条；`
            + `峰值出现在 ${peak.label || peak.bucket}，${num(peak.total)} 条。`);
        // 副标题只留"图上读不出来"的三件事：粒度、时段数（含空段）、合计。
        // 堆积方式不写——图例已经把三段标出来了，写进副标题只会把这行撑到两行，
        // 卡片头的粒度按钮跟着换行，是"改一句话弄坏一处布局"的典型。
        $('#trendSub').textContent = `按${gname}聚合 · ${d.buckets.length} 个时段 · 合计 ${num(d.total)} 条` +
            (d.zero_buckets ? ` · ${d.zero_buckets} 个时段无采集记录` : '');
    }

    /* ---------------- 情感 ----------------
     * 一条横向堆叠条 + 一行可点的图例，替代原来的 320px 环形图。
     *
     * 环形图在这页只做了一件事：把 KPI 里已经写过的正/负占比再画一遍，
     * 代价是首屏右半栏 320px 高，而真正需要宽度的「时间趋势」被压到 760px。
     * 三段谁厚谁薄用一条 40px 的杠就能读出来，精确数字在旁边一行写着。
     *
     * 图例从 div 换成 button：原来是一排只能看、不能聚焦的东西，
     * 键盘用户根本到不了「点情感进证据中心」这个入口。
     */
    function renderSentiment(s, opts) {
        const whole = !!(opts && opts.whole);
        const picked = state.filters.sentiment || [];
        const bar = $('#sentBar'), legend = $('#sentLegend');
        const zh = { positive: '正面', neutral: '中性', negative: '负面' };
        if (s.empty) {
            bar.hidden = true;
            legend.hidden = false;
            legend.innerHTML = '<div class="state"><div class="state-title">当前筛选范围内没有数据</div>' +
                '<div>可以放宽时间范围或切换数据集后再看。</div></div>';
            $('#sentSub').textContent = '点任一段或图例，查看该类评论的原文证据';
            return;
        }
        bar.hidden = false;
        legend.hidden = false;
        // 筛了情感时这条如果是"同口径全量"，要把没选中的两段压暗——不压暗的话
        // 读者会以为这三段都来自当前范围，和上面两张 KPI 卡对不上。
        $('#sentSub').textContent = whole && picked.length
            ? `已按情感筛选（${picked.map(k => zh[k] || k).join('、')}）· 图与数是同口径全量的构成，你正在看的是其中被点亮的一段`
            : '点任一段或图例，查看该类评论的原文证据';
        // 每一段都是真按钮：这条条是页面上最像"可以点"的元素，
        // 以前它是 aria-hidden 的 span，点下去什么都不发生，副标题却在邀请你点。
        bar.innerHTML = s.distribution.map(d => {
            const w = Math.max(0, Math.min(100, Number(d.ratio) || 0));
            if (w <= 0) return '';
            const dim = whole && picked.length && picked.indexOf(d.key) < 0;
            // flex-grow 按占比分配宽度，比写 width:xx% 稳：三段各四舍五入后
            // 加起来常常是 99.9 或 100.1，会露出 1px 的白缝。
            // 负面段叠一层 45° 细斜纹：红 / 绿 对红绿色盲几乎同亮，只靠颜色分不出厚薄。
            // 给正面 / 中性也各加一种纹理，图例就变成三块花布——只给负面，因为它是唯一需要"一眼看出在哪"的一段。
            // 负面段的纹理由 CSS 类 .is-neg 提供（SVG 图案），
            // 不在行内写 repeating-linear-gradient：重复条纹在任何页面上都像装饰，
            // 而这里它承担的是“红绿色盲也能分出哪一段”。
            return `<button type="button" class="sent-seg${dim ? ' is-dim' : ''}${d.key === 'negative' ? ' is-neg' : ''}" data-sent="${d.key}"
                            aria-label="查看${CP.esc(d.name)}的 ${num(d.value)} 条评论证据"
                            title="${CP.esc(d.name)} ${num(d.value)} 条 · 查看证据"
                            style="flex:${w} 1 0;background-color:${SENT_VAR[d.key] || 'var(--neu)'}"></button>`;
        }).join('');
        legend.innerHTML = s.distribution.map(d => `
            <button class="sent-key" type="button" data-sent="${d.key}">
                <i class="sent-key-dot${d.key === 'negative' ? ' is-neg' : ''}"
                   style="background-color:${SENT_VAR[d.key] || 'var(--neu)'}"></i>
                <span>${d.name}</span>
                <b class="sent-key-value">${num(d.value)}</b>
                <span class="sent-key-unit">条</span>
                <span class="sent-key-rate">${pct(d.ratio)}</span>
            </button>`).join('') +
            `<div class="sent-legend-total">分母：${num(s.denominator)} 条具有情感标签的评论</div>`;

        // 段与图例走同一个出口：都按"这一屏实际用的筛选"拼参数。
        // 以前图例只带 sentiment，落地页给出的是全时间的条数，和卡上写的对不上。
        CP.$$('#sentBar [data-sent], #sentLegend [data-sent]').forEach(el => {
            el.addEventListener('click', () => {
                location.href = evidenceHref(/** @type {HTMLElement} */ (el).dataset.sent, opts && opts.params);
            });
        });
    }

    /* ---------------- 平台 ---------------- */
    function renderPlatforms(p) {
        const el = $('#chartPlatform');
        if (p.empty) { setState(el, 'empty'); $('#platTable').innerHTML = ''; return; }
        const base = themeBase();
        const axis = axisCommon();
        setOption(el, Object.assign(base, {
            tooltip: Object.assign(base.tooltip, { trigger: 'axis', axisPointer: { type: 'shadow' } }),
            legend: { top: 0, right: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: CP.CHART_FS.micro, color: CP.themeColors().ink3 } },
            grid: { left: 4, right: 8, top: 26, bottom: 2, containLabel: true },
            xAxis: Object.assign({ type: 'category', data: p.platforms.map(x => x.platform), splitLine: { show: false } }, axis),
            yAxis: Object.assign({ type: 'value' }, axis),
            series: [
                { name: '正面', type: 'bar', stack: 's', data: p.platforms.map(x => x.positive), itemStyle: { color: CP.SENTIMENT_COLOR['正面'] }, barMaxWidth: 40 },
                { name: '中性', type: 'bar', stack: 's', data: p.platforms.map(x => x.neutral), itemStyle: { color: CP.SENTIMENT_COLOR['中性'] } },
                { name: '负面', type: 'bar', stack: 's', data: p.platforms.map(x => x.negative), itemStyle: { color: CP.SENTIMENT_COLOR['负面'], borderRadius: [4, 4, 0, 0], decal: CP.NEG_DECAL } }
            ]
        }));

        // 两平台柱高只差几个百分点，负面率差 0.7pt 在堆叠柱里几乎看不出来；
        // 直接把负面率写成独立小数值，别让读者拿柱高去估算。口径差异也常驻说明。
        const negCells = p.platforms.map(x => `${x.platform} ${pct(x.negative_rate)}`).join(' ／ ');
        $('#platSub').textContent = (p.comparability_note
            || '两平台采集口径与展示机制不同，点赞、评论等互动数值不可直接横向比较，请比较各平台内部的结构占比')
            + ` · 负面率（独立数值，勿按柱高差估算）：${negCells}`;
        CP.describeChart(el, '双平台情感结构柱图：' + p.platforms.map(x =>
            `${x.platform} ${num(x.total)} 条（负面占比 ${pct(x.negative_rate)}）`).join('，') + '。');
        // 031/105：去掉「平均赞」这一绝对数值列，避免诱导跨平台横向比较；
        // 剩下的「评论量」是各平台采集量，口径写进表头与下方可见脚注（platSub）。
        $('#platTable').innerHTML = `<table class="table"><caption class="sr-only">双平台评论量与情感结构；评论量为各平台采集量，互动绝对值不可直接横向比较</caption><thead>
            <tr><th scope="col">平台</th><th scope="col">评论量</th><th scope="col">负面占比</th><th scope="col">最热主题</th></tr></thead><tbody>
            ${p.platforms.map(x => `<tr>
                <td>${CP.esc(x.platform)}</td>
                <td class="num">${num(x.total)}</td>
                <td class="num" style="color:${x.negative_rate > 10 ? 'var(--neg)' : 'var(--ink-2)'}">${pct(x.negative_rate)}</td>
                <td>${CP.esc(x.top_dimension || '—')}</td></tr>`).join('')}
            </tbody></table>`;
    }

    /* ---------------- AI 简述 ---------------- */
    // D2：生成中提示统一尾串，初始占位与每秒计时器共用，避免 1 秒后被计时器覆盖掉。
    const AI_PROGRESS_TAIL = ' · 这段结论基于当前筛选的 KPI 与分布生成，期间可先看下方图表';
    async function loadInsight(force) {
        const box = $('#aiInsight'), badge = $('#insightEngine'), noteBox = $('#aiInsightNote');
        // 「依据」空槽由模板组并行加入、CP.aiBasis 由 core.js 组并行实现：两者未就位时静默跳过
        const basis = document.getElementById('aiInsightBasis');
        const retryBtn = $('#btnInsight');
        // 点「重新生成」时别把旧结论抹掉：模型要 10–30 秒，这段空白既没有内容
        // 也没有依据行，读完像"点了没反应"。旧结论留着、压到 0.55 透明度、
        // 卡头写「更新中」，新结果一到就替换——等待期间屏幕上一直有东西可读。
        const keepOld = !!force && !box.querySelector('.skeleton')
            && (box.textContent || '').trim().length > 30;
        let prog = null;
        if (keepOld) {
            box.classList.add('is-refreshing');
            box.setAttribute('aria-busy', 'true');
        } else {
            // 生成中占位：复用现有 .skeleton 骨架，另起一行按秒更新耗时。
            // 不加「取消」按钮——没有 AbortController 支持，不造假控件。
            box.innerHTML = '<div class="skeleton" style="height:14px;width:100%;margin-bottom:8px;"></div>'
                + '<div class="skeleton" style="height:14px;width:78%;margin-bottom:10px;"></div>'
                + '<div class="ai-progress">已等待 0 秒 · 模型生成通常需要 10–30 秒' + AI_PROGRESS_TAIL + '</div>';
            prog = box.querySelector('.ai-progress');
        }
        const t0 = Date.now();
        const progressTimer = setInterval(() => {
            if (prog) prog.textContent = `已等待 ${Math.floor((Date.now() - t0) / 1000)} 秒 · 模型生成通常需要 10–30 秒` + AI_PROGRESS_TAIL;
        }, 1000);
        // 生成中：口径行照旧（它只依赖筛选），只把结论行改成「生成中」，不累积
        if (basis && typeof CP.aiBasis === 'function') {
            basis.innerHTML = CP.aiBasis(null, 'mixed', { state: 'pending' });
        } else if (basis) basis.innerHTML = '';
        badge.textContent = keepOld ? '更新中' : '生成中';
        noteBox.classList.add('is-hidden');
        try {
            // 不带 refresh 时后端会命中缓存（同一数据集 + 同一筛选 + 数据没变），
            // 切页面 / 切主题回来都是秒回；只有点「重新生成」才真的再问一次模型。
            const params = CP.filterParams();
            if (force) params.refresh = 1;
            const d = await api('/api/dashboard/insight', params);
            box.classList.remove('is-refreshing');
            box.removeAttribute('aria-busy');
            box.textContent = d.text;                  // 覆盖骨架与耗时行
            badge.textContent = d.engine_label;
            badge.className = 'badge ' + (d.engine === 'deepseek' ? 'badge-brand' : 'badge-warn');
            if (d.notice) { noteBox.classList.remove('is-hidden'); noteBox.textContent = d.notice; }
            else { noteBox.classList.add('is-hidden'); }
            // 成功：写「依据」行（幂等赋值，不用 insertAdjacentHTML 累积）
            if (basis && typeof CP.aiBasis === 'function') basis.innerHTML = CP.aiBasis(d, 'mixed');
        } catch (e) {
            console.warn('[dashboard] AI 简述生成失败：' + e.message);   // 原始错误只进控制台
            box.classList.remove('is-refreshing');
            box.removeAttribute('aria-busy');
            box.innerHTML = '<span style="color:var(--neg-ink)">分析暂时未生成，请稍后点「重新生成」重试。</span>';
            badge.textContent = '失败';
            badge.className = 'badge badge-neg';
            // 失败：口径行照旧，只把结论行写成「未生成」——别连"这批数据是什么"一起抹掉
            if (basis && typeof CP.aiBasis === 'function') {
                basis.innerHTML = CP.aiBasis(null, 'mixed', { state: 'failed' });
            } else if (basis) basis.innerHTML = '';
            if (retryBtn) retryBtn.disabled = false;    // 卡片本就有「重新生成」，失败时确保可用
        } finally {
            clearInterval(progressTimer);              // 不泄漏定时器
        }
    }

    /* ---------------- 加载 ---------------- */
    function renderSentimentHint(summary, params) {
        // 只在「筛了情感」时出现：此时负面率/情感分布必然是 100%，
        // 而这是筛选的数学结果、不是发现。提示必须出现在首屏，
        // 只写在页面底部的口径行里等于没写——没人会往下滚。
        const zh = { positive: '正面', neutral: '中性', negative: '负面' };
        const cur = state.filters.sentiment.map(x => zh[x] || x);
        let el = $('#sentimentHint');
        if (!cur.length) {
            if (el) el.remove();
            return;
        }
        if (!el) {
            el = document.createElement('div');
            el.id = 'sentimentHint';
            el.className = 'note note-warn note-flat dp-sent-hint';
            el.style.marginBottom = '12px';
            // 放在 KPI 行正上方，而不是紧贴筛选栏：它解释的是"下面这几张卡为什么变了"。
            // 贴筛选栏会挤在工单条上面，两片彩色底 + 两颗蓝按钮竖着排队。
            const kpi = $('#kpiRow');
            if (!kpi) return;
            kpi.parentNode.insertBefore(el, kpi);
        }
        // 数据还没回来时先给一句占位，拿到 summary 再换成"几条 + 出口"
        if (!summary) {
            el.innerHTML = `<span class="dp-sh-main">正在统计${CP.esc(cur.join('、'))}评论…</span>`;
            return;
        }
        const scope = Number(summary.scope_total) || 0;
        const share = scope ? Math.round(summary.total / scope * 1000) / 10 : 0;
        const href = '/evidence?' + qsFromParams(params || {});
        el.innerHTML = `
            <span class="dp-sh-main">正在看 <b>${num(summary.total)}</b> 条${CP.esc(cur.join('、'))}评论${
                scope ? `<span class="dp-sh-share">占同口径全量 ${num(scope)} 条的 ${share}%</span>` : ''}</span>
            <span class="dp-sh-sub">筛选后的占比恒为 100%，所以上面显示条数与全量占比</span>
            <span class="dp-sh-gap"></span>
            <a class="btn btn-sm" href="${href}">查看这 ${num(summary.total)} 条证据 →</a>`;
    }

    /* 证据页只认 query 串；这里按"这一屏实际用的那套参数"拼，
       包括时间趋势 chip 替掉的日期（否则点过去条数对不上）。 */
    /* 证据页出口：把当前筛选原样带过去，只把情感换成被点的那一类。
       不带日期的话，卡上写 117 条、落地页给 1,969 条 —— 那种对不上比没有链接更糟。 */
    function evidenceHref(sentKey, params) {
        const p = Object.assign({}, params || {});
        p.sentiment = sentKey ? [sentKey] : [];
        return '/evidence?' + qsFromParams(p);
    }

    function qsFromParams(p) {
        const q = new URLSearchParams();
        ['dataset_id', 'platform', 'dimension', 'sentiment', 'date_from', 'date_to', 'keyword']
            .forEach(k => {
                const v = p[k];
                if (!v || (Array.isArray(v) && !v.length)) return;
                q.set(k, Array.isArray(v) ? v.join(',') : v);
            });
        return q.toString();
    }

    async function loadAll() {
        renderSentimentHint();
        const params = CP.filterParams();
        if (view.gran !== 'auto') params.granularity = view.gran;
        if (view.range !== 'all' && state.datasetMeta && state.datasetMeta.time_end) {
            params.date_from = shiftMonths(state.datasetMeta.time_end, -Number(view.range));
            params.date_to = state.datasetMeta.time_end;
        } else {
            params.date_from = state.filters.date_from;
            params.date_to = state.filters.date_to;
        }
        try {
            // 筛了情感时多取一份"同口径全量"的情感构成：本范围内的构成恒为 100%，
            // 唯一有信息量的是"这一块在整体里占多大"，以及整体长什么样。
            const wholeParams = Object.assign({}, params, { sentiment: [] });
            const [summary, trend, sent, plat, whole] = await Promise.all([
                api('/api/dashboard/summary', params),
                api('/api/analytics/trend', params),
                api('/api/analytics/sentiment', params),
                api('/api/analytics/platform-compare', params),
                state.filters.sentiment.length
                    ? api('/api/analytics/sentiment', wholeParams).catch(() => null)
                    : Promise.resolve(null)
            ]);
            renderKpi(summary);
            // 筛选态的第一句应该是"你要看的那批在哪儿"，不是一段机制说明。
            renderSentimentHint(summary, params);
            renderTrend(trend);
            // 全量那份取不到就退回筛选后的那份，但绝不把筛选后的分布标成"全量"。
            const sentWhole = whole && !whole.empty ? whole : null;
            renderSentiment(sentWhole || sent, { whole: !!sentWhole, params: params });
            renderPlatforms(plat);
            // 情感筛选是 S7 才补进筛选栏的：一旦生效，情感分布图必然只剩所选情感（占比 100%）。
            // 这不是"发现"，是筛选的数学结果，必须在口径行里说清楚，否则看图的人会误解。
            const zhSent = { positive: '正面', neutral: '中性', negative: '负面' };
            const sentNote = state.filters.sentiment.length
                ? ` 已按情感筛选（${state.filters.sentiment.map(x => zhSent[x] || x).join('、')}），`
                  + `占比为筛选后口径${sentWhole && summary.meta
                        ? `；情感分布卡展示的是同口径全量 ${num(sentWhole.denominator)} 条的构成`
                        : ''}。`
                : '';
            // 045/106：覆写时保留「多标签合计可大于总数」这句，别把可见口径句吞掉。
            $('#scopeNote').textContent =
                `口径：关键词规则标注情感与主题；主题多标签，合计可大于评论总数；比例分母随筛选变化；` +
                `范围 ${summary.meta.filters_text}；有效评论 ${num(summary.total)} 条。` + sentNote;
            if (summary.empty) toast('当前筛选范围没有评论，请放宽条件', 'err');
        } catch (e) {
            console.warn('[dashboard] 数据加载失败：' + e.message);
            const failMsg = '数据暂时未加载，请稍后刷新页面重试。';
            toast(failMsg, 'err');
            ['#chartTrend', '#chartPlatform'].forEach(id =>
                setState($(id), 'error', failMsg, loadAll));
            // 情感那块已经不是图表了：空态/错误态落在图例那一行，条本身收起来，
            // 否则 setState 会把一段错误文字塞进 40px 高的横条里。
            const bar = $('#sentBar');
            if (bar) bar.hidden = true;
            setState($('#sentLegend'), 'error', failMsg, loadAll);
        }
    }

    function shiftMonths(dateStr, delta) {
        const [y, m, d] = dateStr.split('-').map(Number);
        const total = y * 12 + (m - 1) + delta;
        const ny = Math.floor(total / 12), nm = total % 12 + 1;
        const last = new Date(ny, nm, 0).getDate();
        return `${ny}-${String(nm).padStart(2, '0')}-${String(Math.min(d, last)).padStart(2, '0')}`;
    }

    /* ---------------- 事件 ---------------- */
    /* ---------------- 今天要办的三件事 ----------------
     * 驾驶舱的内容一直只回答"这批数据长什么样"，而产品的主张是
     * "这件事是谁的、多久之内必须动"。于是第一屏看不到 风险指数 / 归口 / 时限，
     * 只有 KPI 与一段 AI 长文——任何舆情 SaaS 都能原样复用。
     *
     * 这里只取研判结果的前三条（`ai=0`：规则引擎，几十毫秒，不调模型，也不花钱），
     * 排的是风险而不是热度。四条信息对应四件事：多急（分数）、是什么（主题）、
     * 归谁（部门 + 责任人）、多久之内（截止日，来自工作日历）。
     *
     * 与研判页的关系：这只是索引，判断与建单仍在 /departments。
     * 数字必须与那边逐字一致，所以两边都读同一个接口，不在这里二次计算。
     */
    async function loadToday() {
        const box = $('#todayStrip');
        if (!box) return;
        // 卡片头由服务端一次渲染；这里只换数据区，
        // 否则最宽的那行说明会在数据回来时被重绘成新的 LCP 候选。
        const body = box.querySelector('#todayBody');
        if (!body) return;
        let d = null;
        try {
            d = await api('/api/departments/risk', { dataset_id: state.dataset_id, ai: 0 });
        } catch (e) {
            // 三种结局必须长得不一样：没权限（整块不出现）、读失败（可重试）、
            // 读到了但今天确实没事（空态）。以前一律 is-hidden，失败看起来像"今天平安"。
            if (e && (e.status === 403 || e.status === 401)) {
                box.classList.add('is-hidden');
                box.setAttribute('aria-busy', 'false');
                return;
            }
            box.classList.remove('is-hidden');
            box.setAttribute('aria-busy', 'false');
            body.innerHTML = `
                <div class="note note-warn"><span>研判暂时读不到（${esc(e.message || '接口失败')}），
                    这一条现在不能替你排序。</span>
                    <button class="btn btn-sm" type="button" data-today-retry>重试</button></div>`;
            const rb = box.querySelector('[data-today-retry]');
            if (rb) rb.addEventListener('click', () => {
                box.classList.add('is-hidden');   // 先收掉旧内容，避免双击叠两层
                loadToday();
            });
            return;
        }
        /* 排序键：**先看到期，再看风险**。
         * 原来只按风险取前 3，结果"食堂餐饮 还剩 50 分钟"（风险 9）排在第 8，
         * 首页给的却是 6.9 个工作日之后的主题——"最该先办"和"多久之内"当场打架。
         * 现在：已超的在前（越负越前）→ 快到期的 → 同一时刻比风险指数。 */
        const byDue = (a, b) => {
            const A = a.remaining_work_hours, B = b.remaining_work_hours;
            if (A != null && B != null && A !== B) return A - B;
            if (A == null && B != null) return 1;
            if (A != null && B == null) return -1;
            return (b.risk || 0) - (a.risk || 0);
        };
        const topics = ((d && d.topics) || []).slice().sort(byDue);
        const high = topics.filter(t => t.level === '高' || t.level === '中高').length;
        const anchor = d && d.anchor ? String(d.anchor).slice(0, 10) : '';
        /* 一行四问：多急（分数 + 等级）· 是什么（主题）· 归谁（部门与责任人）· 多久之内（剩余 / 已超）。
         * 等级同时给文字与颜色：只靠颜色传达严重度，对红绿色盲等于没传达；
         * 57 在 /departments 是橙色「中高」，这里必须同色同级——
         * 同一档在两页两种颜色，读者会以为是两个指标。 */
        const rows = topics.slice(0, 3).map(t => {
            const cls = LEVEL_CLS[t.level] || 'low';
            const dept = t.department || null;
            const who = dept
                ? `${esc(dept.name)}${dept.owner_name ? ' · ' + esc(dept.owner_name) : ''}`
                : '未归口（建单后进「待分派」）';
            // 「还剩 / 已超」的单位与取舍由后端定（remaining_text）：同一个量
            // 在研判页写「1.9 个工作日」、在首页写「13.2 工作小时」，读者会以为两个指标。
            const when = t.remaining_text
                || (t.remaining_work_hours == null ? '没有时限'
                    : (t.remaining_work_hours < 0 ? `已超 ${CP.workHours(-t.remaining_work_hours)}`
                                                  : `还剩 ${CP.workHours(t.remaining_work_hours)}`));
            // 状态用颜色，分数用等级色：「已超」跟工单页一样是红的；
            // 「还剩」不足 1 个工作日（后端此时给的是小时/分钟，不是工作日）也是今天要动的事。
            const tone = t.remaining_work_hours == null ? ''
                : (t.remaining_work_hours < 0 ? ' is-over'
                    : (when.indexOf('个工作日') < 0 ? ' is-soon' : ''));
            // 同一个主题在工单页可能是另一个时间：已建单的用工单截止，
            // 未建单的用部门时限推算。不写清依据，两页并排时就像数据不一致。
            const basis = t.due_basis === 'ticket' ? '按工单截止' : '按部门时限推算';
            const aria = `风险指数 ${num(t.risk)} 分 · ${t.level || ''} · ${t.dimension} · `
                + `${dept ? dept.name : '未归口'} · ${when}（${basis}）`;
            return `<a class="dp-today-row" href="/departments?dim=${encodeURIComponent(t.dimension)}"
                       aria-label="${esc(aria)}"
                       title="到研判页看「${esc(t.dimension)}」的四段构成与证据；这个时限${basis}">
                <span class="dpt-score ${cls}">${num(t.risk)}</span>
                <span class="dpt-dim">${esc(t.dimension)}<i class="dpt-lv ${cls}">${esc(t.level || '—')}</i></span>
                <span class="dpt-who"><span class="dpt-dept">${who}</span>
                    <span class="dpt-due${tone}">${esc(when)}</span></span>
            </a>`;
        }).join('');
        box.classList.remove('is-hidden');
        box.setAttribute('aria-busy', 'false');
        body.innerHTML = `
            ${rows || `<div class="note note-muted"><span>近 30 天没有需要优先回应的主题${
                anchor ? `（截至 ${esc(anchor)}）` : ''}。</span></div>`}
            <div class="dp-today-foot">共研判 <b>${topics.length}</b> 个主题 ·
                高 / 中高 <b>${high}</b> 个${anchor ? ` · 锚点 ${esc(anchor)}` : ''}
                · 点某一行到研判页看它的四段构成</div>`;
    }

    /* ---------------- 工单概览一格 ---------------- */
    // 驾驶舱只回答"有没有人管"，不在这里做工单操作：
    // 大屏上的行动按钮最容易让人误点，真正的处置留在工单页。
    async function loadTicketStrip() {
        const box = document.getElementById('ticketStrip');
        if (!box) return;
        let b = null;
        try { b = await CP.loadTicketBadge(); } catch (e) { b = null; }
        if (!b || !b.can_view) { box.classList.add('is-hidden'); return; }
        box.classList.remove('is-hidden');
        box.removeAttribute('aria-busy');
        // 这一格的两个数字必须能点进对应的那张列表：说"超期 3 单"却落到默认的
        // "进行中"，人就得多点一次、自己再筛一遍，接力棒在半路掉了。
        const over = b.overdue || 0;
        const href = over > 0 ? '/tickets?status_group=overdue' : '/tickets?status_group=open';
        // 超期不为 0 时整条换配色：这一行是首屏唯一"今天就得处理"的事，
        // 它比下面任何一张图都该抢眼，原来排在六张卡上面却是一行灰字。
        box.classList.toggle('is-alert', over > 0);
        // 第 18 轮：标题从「待处置」改成「未结」。
        // pending 的口径是“所有未归档的单”（draft/triaged/assigned/processing/pending_review/reopened），
        // 而「待处置」在工单页专指「待分派」那一列。两边并排时：首页说 5 单待处置，
        // 点进去「待分派 0」——值班员会以为数字错了。
        box.innerHTML = `
            <div class="tk-strip-main">未结 <b>${b.pending}</b> 单<em class="tk-strip-sub">· 含已分派与处理中</em></div>
            <div class="tk-strip-main ${over ? 'neg' : ''}">超期 <b>${over}</b> 单</div>
            <div class="tk-strip-gap"></div>
            <span class="tk-strip-sub">工单数字不随上方筛选变化</span>
            <a class="btn btn-sm ${over ? 'btn-primary' : ''}" href="${href}">${
                over > 0 ? `先看超期的 ${over} 单` : '进入处置工单'}</a>`;
    }

    document.addEventListener('cp:ready', () => {
        loadAll();
        loadInsight();
        loadTicketStrip();
        loadToday();
    });
    document.addEventListener('cp:filter', () => { loadAll(); loadInsight(); });
    // 换数据集要重新取研判（研判是“在这批数据里”的判断）；筛选不重取，
    // 因为它是近30天全校口径，卡片上已写明不随筛选变化。
    document.addEventListener('cp:dataset', () => { loadAll(); loadInsight(); loadToday(); });
    // 切主题时图表颜色要重算（颜色是烘焙进 option 的，改不了已渲染的 canvas）
    document.addEventListener('cp:theme', () => { loadAll(); loadInsight(); });

    $('#rangeTabs').addEventListener('click', e => {
        const b = e.target.closest('[data-range]');
        if (!b) return;
        view.range = b.dataset.range;
        CP.$$('#rangeTabs .chip').forEach(x => x.classList.toggle('on', x === b));
        loadAll();
    });
    $('#granSel').addEventListener('change', e => {
        view.gran = e.target.value;
        loadAll();
    });
    $('#btnInsight').addEventListener('click', () => loadInsight(true));
    window.addEventListener('resize', () => CP.resizeCharts());
})();
