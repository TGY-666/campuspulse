/* PAGE-03 数据洞察中心 */
(function () {
    'use strict';
    const { $, api, num, pct, setState, setOption, themeBase, axisCommon, toast } = CP;

    const AX = axisCommon();
    const GRID = { left: 6, right: 14, top: 22, bottom: 2, containLabel: true };
    const PLATFORM_COLOR = { '抖音': '#165DFF', '小红书': '#F06595' };

    function baseOpt(extra) {
        return Object.assign(themeBase(), {
            grid: GRID,
            tooltip: Object.assign(themeBase().tooltip, { trigger: 'axis', axisPointer: { type: 'shadow' } })
        }, extra || {});
    }

    /** I09 联动下钻：点击图表元素 → 带着当前筛选条件跳到证据中心 */
    function drillTo(el, mapper) {
        const inst = CP.chart(el);
        if (!inst || !inst.on) return;
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
    }

    /* ---------------- AI 解读 ---------------- */
    async function loadInsights() {
        const box = $('#chartInsights'), badge = $('#ciEngine'), note = $('#ciNotice');
        try {
            const d = await api('/api/analytics/chart-insights', CP.filterParams());
            if (!d.items.length) {
                box.innerHTML = '<div class="note note-muted">当前筛选范围没有数据，暂不生成解读。</div>';
                badge.textContent = '无数据';
                return;
            }
            box.innerHTML = d.items.map(it => `
                <div style="display:flex;gap:10px;padding:10px 12px;border:1px solid var(--line);border-radius:11px;background:var(--surface-3);">
                    <span class="badge badge-brand" style="flex:0 0 auto;height:20px;">${CP.esc(it.chart)}</span>
                    <span style="font-size:12.5px;line-height:1.7;color:var(--ink-2);">${CP.esc(it.text)}</span>
                </div>`).join('');
            badge.textContent = d.engine_label;
            badge.className = 'badge ' + (d.engine === 'deepseek' ? 'badge-brand' : 'badge-warn');
            if (d.notice) { note.style.display = 'flex'; note.textContent = d.notice; }
            else note.style.display = 'none';
        } catch (e) {
            box.innerHTML = '<div class="note note-warn">解读生成失败：' + CP.esc(e.message) + '</div>';
            badge.textContent = '失败';
        }
    }

    /* ---------------- 平台 / 长度 / 点赞 ---------------- */
    function renderPlatformPie(s) {
        const el = $('#chartPlatformPie');
        const rows = (s.by_platform || []).filter(p => p.total > 0);
        if (!rows.length) { setState(el, 'empty'); return; }
        const base = themeBase();
        setOption(el, Object.assign(base, {
            tooltip: Object.assign(base.tooltip, { trigger: 'item', formatter: p => `${p.name}<br/><b>${num(p.value)}</b> 条 · ${p.percent}%` }),
            legend: { bottom: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 11, color: CP.themeColors().ink3 } },
            series: [{
                type: 'pie', radius: ['46%', '68%'], center: ['50%', '44%'],
                itemStyle: { borderColor: CP.themeColors().surface, borderWidth: 2 },
                label: { show: false },
                emphasis: { label: { show: true, formatter: '{b}\n{d}%', fontSize: 12, fontWeight: 600 } },
                data: rows.map(p => ({
                    name: p.platform, value: p.total,
                    itemStyle: PLATFORM_COLOR[p.platform] ? { color: PLATFORM_COLOR[p.platform] } : undefined
                }))
            }]
        }));
        drillTo(el, p => ({ platform: p.name }));
    }

    function renderLength(d) {
        const el = $('#chartLength');
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
                { axisLabel: { color: CP.themeColors().ink4, fontSize: 10.5, interval: 0, rotate: 20 } }),
            yAxis: Object.assign({ type: 'value' }, AX),
            series: [{
                type: 'bar', barMaxWidth: 34, data: d.length_dist.map(x => x.count),
                itemStyle: { borderRadius: [4, 4, 0, 0], color: '#7BA6FF' }
            }]
        }));
    }

    function renderLike(d) {
        const s = d.like_stats || {};
        $('#likeStat').innerHTML = `
            <div class="metrics-row">
                <div class="metric"><div class="metric-name">平均点赞</div><div class="metric-value">${num(s.avg)}</div></div>
                <div class="metric"><div class="metric-name">中位数</div><div class="metric-value">${num(s.median)}</div></div>
                <div class="metric"><div class="metric-name">最高</div><div class="metric-value">${num(s.max)}</div></div>
                <div class="metric"><div class="metric-name">零赞占比</div><div class="metric-value">${pct(s.zero_ratio)}</div></div>
            </div>`;
        const el = $('#chartLikeTop');
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
                itemStyle: { borderRadius: [0, 4, 4, 0], color: '#165DFF' },
                label: { show: true, position: 'right', fontSize: 10.5, color: CP.themeColors().ink4, formatter: p => num(p.value) }
            }]
        }));
    }

    /* ---------------- 时间 ---------------- */
    function renderHour(d) {
        const el = $('#chartHour');
        if (!d.hour.some(v => v > 0)) { setState(el, 'empty'); return; }
        setOption(el, baseOpt({
            tooltip: Object.assign(themeBase().tooltip, {
                trigger: 'axis', formatter: ps => `${ps[0].axisValue}点<br/><b>${num(ps[0].value)}</b> 条`
            }),
            xAxis: Object.assign({ type: 'category', data: d.hour.map((_, i) => i + '时'), splitLine: { show: false } }, AX,
                { axisLabel: { color: CP.themeColors().ink4, fontSize: 10, interval: 2 } }),
            yAxis: Object.assign({ type: 'value' }, AX),
            series: [{
                type: 'line', smooth: true, symbol: 'none', data: d.hour,
                lineStyle: { width: 2, color: '#165DFF' },
                areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                    colorStops: [{ offset: 0, color: 'rgba(22,93,255,.22)' }, { offset: 1, color: 'rgba(22,93,255,0)' }] } }
            }]
        }));
    }

    function renderWeek(d) {
        const el = $('#chartWeek');
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

    /* ---------------- 维度 ---------------- */
    function renderDims(t) {
        const el = $('#chartDims');
        if (!t.ranking.length) { setState(el, 'empty'); return; }
        const rows = t.ranking.slice().reverse();
        setOption(el, baseOpt({
            grid: { left: 4, right: 44, top: 8, bottom: 2, containLabel: true },
            xAxis: Object.assign({ type: 'value' }, AX),
            yAxis: Object.assign({ type: 'category', data: rows.map(r => r.dimension), splitLine: { show: false } }, AX,
                { axisLabel: { color: CP.themeColors().ink2, fontSize: 11.5 } }),
            series: [{
                type: 'bar', data: rows.map(r => r.count), barMaxWidth: 18,
                itemStyle: { borderRadius: [0, 5, 5, 0], color: '#165DFF' },
                label: { show: true, position: 'right', fontSize: 10.5, color: CP.themeColors().ink3, formatter: p => num(p.value) }
            }]
        }));
        drillTo(el, p => ({ dimension: p.name }));
    }

    function renderDimSent(t) {
        const el = $('#chartDimSent');
        if (!t.ranking.length) { setState(el, 'empty'); return; }
        const rows = t.ranking.slice(0, 7);
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
            legend: { top: 0, right: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 10.5, color: CP.themeColors().ink3 } },
            grid: { left: 4, right: 10, top: 26, bottom: 2, containLabel: true },
            xAxis: Object.assign({ type: 'category', data: rows.map(r => r.dimension), splitLine: { show: false } }, AX,
                { axisLabel: { color: CP.themeColors().ink4, fontSize: 10.5, interval: 0, rotate: 24 } }),
            yAxis: Object.assign({ type: 'value', max: 100, axisLabel: { formatter: '{value}%', color: CP.themeColors().ink4, fontSize: 10 } }, AX),
            series: [
                { name: '正面', type: 'bar', stack: 'x', data: rows.map(r => pctOf(r.positive, r.count)), itemStyle: { color: '#16A34A' }, barMaxWidth: 26 },
                { name: '中性', type: 'bar', stack: 'x', data: rows.map(r => pctOf(r.count - r.positive - r.negative, r.count)), itemStyle: { color: '#CBD5E1' } },
                { name: '负面', type: 'bar', stack: 'x', data: rows.map(r => pctOf(r.negative, r.count)), itemStyle: { color: '#E5484D', borderRadius: [3, 3, 0, 0] } }
            ]
        }));
    }

    /* ---------------- 负面 / 正面 ---------------- */
    function renderPareto(t) {
        const el = $('#chartPareto');
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
                { axisLabel: { color: CP.themeColors().ink4, fontSize: 10.5, interval: 0, rotate: rows.length > 5 ? 26 : 0 } }),
            yAxis: [
                Object.assign({ type: 'value' }, AX),
                Object.assign({ type: 'value', max: 100, splitLine: { show: false },
                    axisLabel: { formatter: '{value}%', color: CP.themeColors().ink4, fontSize: 10 } }, { axisLine: { show: false }, axisTick: { show: false } })
            ],
            series: [
                { type: 'bar', data: rows.map(r => r.count), barMaxWidth: 30, itemStyle: { borderRadius: [4, 4, 0, 0], color: '#E5484D' } },
                { type: 'line', yAxisIndex: 1, data: cumData, smooth: true, symbol: 'circle', symbolSize: 5, lineStyle: { color: '#F59E0B', width: 2 }, itemStyle: { color: '#F59E0B' } }
            ]
        }));
    }

    function renderPos(t) {
        const el = $('#chartPos');
        const all = t.positive_points || [];
        const rows = all.filter(r => r.point !== '未细分卖点').slice(0, 8).reverse();
        const rest = all.find(r => r.point === '未细分卖点');
        if (!rows.length) { setState(el, 'empty', '当前范围内没有可归因的正面评论。'); return; }
        el.parentElement.querySelector('.card-sub').textContent = rest
            ? `正面评论中最常被提到的点 · 另有 ${num(rest.count)} 条未细分，未计入`
            : '正面评论中最常被提到的点';
        setOption(el, baseOpt({
            grid: { left: 4, right: 44, top: 8, bottom: 2, containLabel: true },
            xAxis: Object.assign({ type: 'value' }, AX),
            yAxis: Object.assign({ type: 'category', data: rows.map(r => r.point), splitLine: { show: false } }, AX,
                { axisLabel: { color: CP.themeColors().ink2, fontSize: 11 } }),
            series: [{
                type: 'bar', data: rows.map(r => r.count), barMaxWidth: 18,
                itemStyle: { borderRadius: [0, 5, 5, 0], color: '#16A34A' },
                label: { show: true, position: 'right', fontSize: 10.5, color: CP.themeColors().ink3, formatter: p => num(p.value) }
            }]
        }));
    }

    /* ---------------- 平台维度对比 ---------------- */
    function renderPlatDim(p) {
        const el = $('#chartPlatDim');
        if (!p.platforms.length) { setState(el, 'empty'); return; }
        const names = p.platforms.map(x => x.platform);
        setOption(el, baseOpt({
            legend: { top: 0, right: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 10.5, color: CP.themeColors().ink3 } },
            grid: { left: 4, right: 12, top: 26, bottom: 2, containLabel: true },
            xAxis: Object.assign({ type: 'category', data: p.dimension_matrix.map(d => d.dimension), splitLine: { show: false } }, AX,
                { axisLabel: { color: CP.themeColors().ink4, fontSize: 10.5, interval: 0, rotate: 26 } }),
            yAxis: Object.assign({ type: 'value' }, AX),
            series: names.map(n => ({
                name: n, type: 'bar', barMaxWidth: 18,
                data: p.dimension_matrix.map(d => d[n] || 0),
                itemStyle: { borderRadius: [3, 3, 0, 0], color: PLATFORM_COLOR[n] }
            }))
        }));

        $('#platTable').innerHTML = `<table class="table"><thead>
            <tr><th>平台</th><th>评论</th><th>正面</th><th>负面</th><th>平均赞</th><th>均长</th></tr></thead><tbody>
            ${p.platforms.map(x => `<tr>
                <td>${CP.esc(x.platform)}</td><td class="num">${num(x.total)}</td>
                <td class="num">${pct(x.positive_rate)}</td>
                <td class="num" style="color:${x.negative_rate >= 10 ? 'var(--neg)' : 'var(--ink-2)'}">${pct(x.negative_rate)}</td>
                <td class="num">${num(x.like_avg)}</td><td class="num">${num(x.length_avg)}</td></tr>`).join('')}
            </tbody></table>
            ${p.comparability_note ? `<div class="note note-warn" style="margin-top:8px;">${CP.esc(p.comparability_note)}</div>` : ''}`;
    }

    /* ---------------- 词云 ---------------- */
    const TONE_COLOR = { neg: '#E5484D', pos: '#12A150', neu: '#165DFF' };

    function renderWords(d) {
        const el = $('#chartWords');
        const words = d.keywords || [];
        const kf = d.keyword_filter || {};
        if (!words.length) { setState(el, 'empty'); return; }

        if ($('#wordSub')) {
            $('#wordSub').textContent = `共 ${words.length} 个高频实词 · 取自 ${num(kf.sampled || 0)} 条评论的分词结果 · `
                + `已剔除 ${kf.stopwords_total || 0} 个停用词与非实词 · 整个范围负面占比基线 ${kf.base_neg_rate || 0}%`;
        }
        const tip = $('#wordFilterTip');
        if (tip) {
            const groups = (kf.groups || []).map(g => `${g.name} ${g.size} 个`).join('、');
            const dropped = (kf.dropped_top || []).map(x => `${x.name}(${x.value})`).join('、');
            tip.innerHTML = `<b>过滤口径</b><br>${(kf.rules || []).map((r, i) => `${i + 1}. ${r}`).join('<br>')}`
                + `<br><b>停用词 ${kf.stopwords_total || 0} 个</b>：${groups}`
                + (dropped ? `<br><b>本次被剔除最多的词</b>：${CP.esc(dropped)}` : '')
                + `<br><b>着色规则</b>：${CP.esc(kf.tone_rule || '')}（当前基线 ${kf.base_neg_rate || 0}%）。`;
        }

        try {
            setOption(el, Object.assign(themeBase(), {
                tooltip: Object.assign(themeBase().tooltip, {
                    formatter: p => {
                        const w = words.find(x => x.name === p.name) || {};
                        return `<b>${CP.esc(p.name)}</b> · 出现 ${p.value} 次<br>`
                            + `负面语境 ${w.neg || 0} 条 / 正面语境 ${w.pos || 0} 条 / 中性 ${w.neu || 0} 条<br>`
                            + `负面占比 ${w.neg_share || 0}%，整体基线 ${kf.base_neg_rate || 0}%（${w.lift || 0} 倍）`;
                    }
                }),
                series: [{
                    type: 'wordCloud', shape: 'circle',
                    left: 0, top: 0, right: 0, bottom: 0,
                    sizeRange: [14, 54], rotationRange: [0, 0], gridSize: 6,
                    textStyle: { fontWeight: 600 },
                    emphasis: { textStyle: { textShadowBlur: 8, textShadowColor: 'rgba(22,93,255,.28)' } },
                    data: words.map(w => ({
                        name: w.name, value: w.value,
                        textStyle: { color: TONE_COLOR[w.tone] || TONE_COLOR.neu }
                    }))
                }]
            }));
        } catch (e) {            // 插件缺失时退化为柱状图，保证信息仍然可读
            setOption(el, baseOpt({
                grid: { left: 4, right: 12, top: 10, bottom: 2, containLabel: true },
                xAxis: Object.assign({ type: 'category', data: words.slice(0, 15).map(w => w.name), splitLine: { show: false } }, AX,
                    { axisLabel: { color: CP.themeColors().ink4, fontSize: 10, rotate: 30, interval: 0 } }),
                yAxis: Object.assign({ type: 'value' }, AX),
                series: [{
                    type: 'bar',
                    data: words.slice(0, 15).map(w => ({ value: w.value, itemStyle: { color: TONE_COLOR[w.tone] || TONE_COLOR.neu } })),
                    itemStyle: { borderRadius: [4, 4, 0, 0] }
                }]
            }));
        }

        // 右侧高频词明细：词云之外给出可读的数值与情绪构成
        const top = words.slice(0, 12);
        const max = top.length ? top[0].value : 1;
        $('#wordTable').innerHTML = `<table class="table"><thead>
            <tr><th style="width:34px;">#</th><th>关键词</th><th>出现</th><th>负面</th><th>负面占比</th><th style="width:24%">词频</th></tr></thead><tbody>
            ${top.map((w, i) => {
                const share = w.neg_share || 0;
                const isNeg = w.tone === 'neg';
                return `<tr>
                <td class="num" style="color:var(--ink-4)">${i + 1}</td>
                <td style="font-weight:600;color:${TONE_COLOR[w.tone] || TONE_COLOR.neu}">${CP.esc(w.name)}</td>
                <td class="num">${num(w.value)}</td>
                <td class="num" style="color:${isNeg ? 'var(--neg)' : 'var(--ink-4)'}">${num(w.neg || 0)}</td>
                <td class="num" style="color:${isNeg ? 'var(--neg)' : 'var(--ink-3)'}">${share}%</td>
                <td><span style="display:block;height:6px;background:var(--line-2);border-radius:4px;overflow:hidden;">
                    <i style="display:block;height:100%;width:${Math.max(4, w.value / max * 100)}%;background:${TONE_COLOR[w.tone] || TONE_COLOR.neu};opacity:.75;"></i>
                </span></td></tr>`;}).join('')}
            </tbody></table>`;
    }

    /* ---------------- 加载 ---------------- */
    async function loadAll() {
        const p = CP.filterParams();
        try {
            const [dist, topics, sent, plat] = await Promise.all([
                api('/api/analytics/distributions', p),
                api('/api/analytics/topics', p),
                api('/api/analytics/sentiment', p),
                api('/api/analytics/platform-compare', p)
            ]);
            renderPlatformPie(sent);
            renderLength(dist);
            renderLike(dist);
            renderHour(dist);
            renderWeek(dist);
            renderDims(topics);
            renderDimSent(topics);
            renderPareto(topics);
            renderPos(topics);
            renderPlatDim(plat);
            renderWords(dist);
        } catch (e) {
            toast('洞察数据加载失败：' + e.message, 'err');
            ['#chartPlatformPie', '#chartLength', '#chartDims', '#chartPareto'].forEach(id =>
                setState($(id), 'error', e.message, loadAll));
        }
    }

    document.addEventListener('cp:ready', () => { loadAll(); loadInsights(); });
    document.addEventListener('cp:filter', () => { loadAll(); loadInsights(); });
    document.addEventListener('cp:dataset', () => { loadAll(); loadInsights(); });
    document.addEventListener('cp:theme', () => { loadAll(); loadInsights(); });
    window.addEventListener('resize', () => CP.$$('.chart').forEach(el => {
        const i = CP.chart(el); if (i) i.resize();
    }));
})();
