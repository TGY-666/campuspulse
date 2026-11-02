/* PAGE-01 智能驾驶舱 */
(function () {
    'use strict';
    const { $, state, api, num, pct, setState, setOption, themeBase, axisCommon, toast } = CP;

    const view = { range: 'all', gran: 'auto' };
    let trendData = null;

    /* ---------------- KPI ---------------- */
    function renderKpi(o) {
        const platforms = Object.keys(o.platforms || {});
        const cards = [
            {
                label: '有效评论', value: num(o.total), unit: '条',
                foot: `情感标注分母 ${num(o.denominator)} 条`,
                bar: null
            },
            {
                label: '正面占比', value: pct(o.positive_rate), unit: '',
                foot: `${num(o.positive)} / ${num(o.denominator)} 条`,
                bar: [['#16A34A', o.positive_rate]]
            },
            {
                label: '负面占比', value: pct(o.negative_rate), unit: '',
                foot: `${num(o.negative)} / ${num(o.denominator)} 条`,
                bar: [['#E5484D', o.negative_rate]]
            },
            {
                label: '讨论最多维度', value: o.top_dimension || '—', unit: '',
                foot: o.top_dimension ? `${num(o.top_dimension_count)} 条提及（多标签）` : '暂无数据',
                bar: null, small: true
            },
            {
                label: '覆盖平台', value: platforms.length || 0, unit: '个',
                foot: platforms.map(p => `${p} ${num(o.platforms[p])}`).join(' · ') || '暂无平台数据',
                bar: null
            },
            {
                label: '总互动量', value: num(o.total_likes), unit: '赞',
                foot: `平均 ${num(o.like_avg)} 赞 / 条`,
                bar: null
            }
        ];

        $('#kpiRow').innerHTML = cards.map(c => `
            <div class="card kpi">
                <div class="kpi-label">${c.label}</div>
                <div class="kpi-value" style="${c.small ? 'font-size:21px;letter-spacing:-.4px;' : ''}">
                    ${CP.esc(c.value)}<span class="kpi-unit">${c.unit}</span>
                </div>
                <div class="kpi-foot">${c.foot}</div>
                ${c.bar ? `<div class="kpi-bar">${c.bar.map(([color, v]) =>
                    `<i style="width:${Math.min(100, Math.max(0, Number(v) || 0))}%;background:${color}"></i>`).join('')}</div>` : ''}
            </div>`).join('');
    }

    /* ---------------- 趋势 ---------------- */
    function renderTrend(d) {
        trendData = d;
        const el = $('#chartTrend');
        if (d.empty) { setState(el, 'empty', '当前筛选范围内没有评论记录。'); return; }

        const base = themeBase();
        const axis = axisCommon();
        const cats = d.buckets.map(b => b.label);
        setOption(el, Object.assign(base, {
            tooltip: Object.assign(base.tooltip, {
                trigger: 'axis',
                formatter: ps => {
                    const b = d.buckets[ps[0].dataIndex];
                    return `<b>${b.bucket}</b><br/>评论总量：${num(b.total)} 条<br/>` +
                        `正面 ${num(b.positive)} · 中性 ${num(b.neutral)} · <span style="color:var(--neg)">负面 ${num(b.negative)}</span>`;
                }
            }),
            legend: { top: 0, right: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 11, color: CP.themeColors().ink3 } },
            grid: { left: 4, right: 12, top: 30, bottom: 6, containLabel: true },
            xAxis: Object.assign({ type: 'category', data: cats, splitLine: { show: false } }, axis, {
                axisLabel: { color: CP.themeColors().ink4, fontSize: 11, hideOverlap: true }
            }),
            yAxis: Object.assign({ type: 'value', name: '条', nameTextStyle: { color: CP.themeColors().ink4, fontSize: 10 } }, axis),
            series: [
                {
                    name: '评论总量', type: 'line', data: d.buckets.map(b => b.total),
                    smooth: true, symbol: 'circle', symbolSize: 5, showSymbol: cats.length <= 24,
                    lineStyle: { width: 2.2, color: '#165DFF' },
                    itemStyle: { color: '#165DFF' },
                    areaStyle: {
                        color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                            colorStops: [{ offset: 0, color: 'rgba(22,93,255,.18)' }, { offset: 1, color: 'rgba(22,93,255,0)' }] }
                    }
                },
                {
                    name: '负面评论', type: 'bar', data: d.buckets.map(b => b.negative),
                    barMaxWidth: 14, itemStyle: { color: 'rgba(229,72,77,.72)', borderRadius: [3, 3, 0, 0] }
                }
            ]
        }));
        const gname = { day: '日', week: '周', month: '月' }[d.granularity];
        $('#trendSub').textContent = `按${gname}聚合 · ${d.buckets.length} 个时段 · 合计 ${num(d.total)} 条` +
            (d.zero_buckets ? ` · ${d.zero_buckets} 个时段无采集记录` : '');
    }

    /* ---------------- 情感 ---------------- */
    function renderSentiment(s) {
        const el = $('#chartSentiment');
        if (s.empty) { setState(el, 'empty'); $('#sentLegend').innerHTML = ''; return; }
        const base = themeBase();
        setOption(el, Object.assign(base, {
            tooltip: Object.assign(base.tooltip, {
                trigger: 'item',
                formatter: p => `${p.name}<br/><b>${num(p.value)}</b> 条 · ${p.percent}%`
            }),
            series: [{
                type: 'pie', radius: ['52%', '74%'], center: ['50%', '48%'],
                itemStyle: { borderColor: CP.themeColors().surface, borderWidth: 2 },
                label: {
                    show: true, position: 'center',
                    formatter: () => `{a|${num(s.denominator)}}\n{b|有效评论}`,
                    rich: {
                        a: { fontSize: 22, fontWeight: 700, color: CP.themeColors().ink, lineHeight: 28 },
                        b: { fontSize: 11, color: CP.themeColors().ink4 }
                    }
                },
                emphasis: { scale: true, scaleSize: 4, label: { show: true, formatter: '{b}\n{d}%', fontSize: 13, fontWeight: 600 } },
                data: s.distribution.map(d => ({
                    name: d.name, value: d.value,
                    itemStyle: { color: CP.SENTIMENT_COLOR[d.name] }
                }))
            }]
        }));
        $('#chartSentiment').onclick = null;
        $('#sentLegend').innerHTML = s.distribution.map(d => `
            <div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px dashed #F2F5FA;font-size:12.5px;cursor:pointer;"
                 data-sent="${d.key}">
                <i style="width:8px;height:8px;border-radius:50%;background:${CP.SENTIMENT_COLOR[d.name]}"></i>
                <span style="color:var(--ink-2)">${d.name}</span>
                <span style="margin-left:auto;font-variant-numeric:tabular-nums;color:var(--ink);font-weight:600">${num(d.value)}</span>
                <span style="width:52px;text-align:right;color:var(--ink-4)">${pct(d.ratio)}</span>
            </div>`).join('') +
            `<div style="font-size:11.5px;color:var(--ink-4);margin-top:8px;">分母：${num(s.denominator)} 条具有情感标签的评论</div>`;

        CP.$$('#sentLegend [data-sent]').forEach(row => {
            row.addEventListener('click', () => {
                location.href = '/evidence?' + CP.qs({ sentiment: row.dataset.sent });
            });
        });
    }

    /* ---------------- 维度 ---------------- */
    function renderDims(t) {
        const el = $('#chartDims');
        if (t.empty) { setState(el, 'empty'); return; }
        const rows = t.ranking.slice().reverse();
        const base = themeBase();
        const axis = axisCommon();
        const inst = setOption(el, Object.assign(base, {
            tooltip: Object.assign(base.tooltip, {
                trigger: 'axis', axisPointer: { type: 'shadow' },
                formatter: ps => {
                    const r = rows[ps[0].dataIndex];
                    return `<b>${r.dimension}</b><br/>提及 ${num(r.count)} 条（占全部评论 ${pct(r.share)}）<br/>` +
                        `负面 ${num(r.negative)} 条 · 负面占比 ${pct(r.negative_rate)}<br/>` +
                        `<span style="color:var(--ink-4)">多标签计数，可点击查看评论</span>`;
                }
            }),
            grid: { left: 4, right: 40, top: 8, bottom: 4, containLabel: true },
            xAxis: Object.assign({ type: 'value' }, axis),
            yAxis: Object.assign({ type: 'category', data: rows.map(r => r.dimension), splitLine: { show: false } }, axis, {
                axisLabel: { color: CP.themeColors().ink2, fontSize: 12 }
            }),
            series: [{
                type: 'bar', data: rows.map(r => r.count), barMaxWidth: 20,
                itemStyle: {
                    borderRadius: [0, 5, 5, 0],
                    color: { type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
                        colorStops: [{ offset: 0, color: '#BCD2FF' }, { offset: 1, color: '#165DFF' }] }
                },
                label: { show: true, position: 'right', color: CP.themeColors().ink3, fontSize: 11, formatter: p => num(p.value) }
            }]
        }));
        if (inst) {
            inst.off('click');
            inst.on('click', p => {
                location.href = '/evidence?' + CP.qs({ dimension: rows[p.dataIndex].dimension });
            });
        }
    }

    /* ---------------- 平台 ---------------- */
    function renderPlatforms(p) {
        const el = $('#chartPlatform');
        if (p.empty) { setState(el, 'empty'); $('#platTable').innerHTML = ''; return; }
        const base = themeBase();
        const axis = axisCommon();
        setOption(el, Object.assign(base, {
            tooltip: Object.assign(base.tooltip, { trigger: 'axis', axisPointer: { type: 'shadow' } }),
            legend: { top: 0, right: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 11, color: CP.themeColors().ink3 } },
            grid: { left: 4, right: 8, top: 26, bottom: 2, containLabel: true },
            xAxis: Object.assign({ type: 'category', data: p.platforms.map(x => x.platform), splitLine: { show: false } }, axis),
            yAxis: Object.assign({ type: 'value' }, axis),
            series: [
                { name: '正面', type: 'bar', stack: 's', data: p.platforms.map(x => x.positive), itemStyle: { color: '#16A34A' }, barMaxWidth: 40 },
                { name: '中性', type: 'bar', stack: 's', data: p.platforms.map(x => x.neutral), itemStyle: { color: '#CBD5E1' } },
                { name: '负面', type: 'bar', stack: 's', data: p.platforms.map(x => x.negative), itemStyle: { color: '#E5484D', borderRadius: [4, 4, 0, 0] } }
            ]
        }));

        $('#platSub').textContent = p.comparability_note || '同一筛选口径下的评论量与情感结构';
        $('#platTable').innerHTML = `<table class="table"><thead>
            <tr><th>平台</th><th>评论</th><th>负面占比</th><th>平均赞</th><th>最热维度</th></tr></thead><tbody>
            ${p.platforms.map(x => `<tr>
                <td>${CP.esc(x.platform)}</td>
                <td class="num">${num(x.total)}</td>
                <td class="num" style="color:${x.negative_rate > 10 ? 'var(--neg)' : 'var(--ink-2)'}">${pct(x.negative_rate)}</td>
                <td class="num">${num(x.like_avg)}</td>
                <td>${CP.esc(x.top_dimension || '—')}</td></tr>`).join('')}
            </tbody></table>`;
    }

    /* ---------------- AI 简述 ---------------- */
    async function loadInsight(force) {
        const box = $('#aiInsight'), badge = $('#insightEngine'), noteBox = $('#aiInsightNote');
        box.innerHTML = '<span style="color:var(--ink-4);font-size:13px;">正在根据当前统计生成简述…</span>';
        badge.textContent = '生成中';
        noteBox.style.display = 'none';
        try {
            const d = await api('/api/dashboard/insight', CP.filterParams());
            box.textContent = d.text;
            badge.textContent = d.engine_label;
            badge.className = 'badge ' + (d.engine === 'deepseek' ? 'badge-brand' : 'badge-warn');
            if (d.notice) { noteBox.style.display = 'flex'; noteBox.textContent = d.notice; }
            else { noteBox.style.display = 'none'; }
        } catch (e) {
            box.innerHTML = '<span style="color:var(--neg-ink)">AI 简述生成失败：' + CP.esc(e.message) + '</span>';
            badge.textContent = '失败';
            badge.className = 'badge badge-neg';
        }
    }

    /* ---------------- 加载 ---------------- */
    function renderSentimentHint() {
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
            el.className = 'note note-warn';
            el.style.marginBottom = '12px';
            const bar = $('#filterBar');
            if (!bar) return;
            bar.parentNode.insertBefore(el, bar.nextSibling);
        }
        el.textContent = `已按情感筛选（${cur.join('、')}）：下面所有指标只统计该情感，`
            + `负面率与情感分布必然出现 100%，这是筛选结果、不是新发现。`
            + `点筛选栏右侧「重置筛选」可回到全量。`;
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
            const [summary, trend, sent, topics, plat] = await Promise.all([
                api('/api/dashboard/summary', params),
                api('/api/analytics/trend', params),
                api('/api/analytics/sentiment', params),
                api('/api/analytics/topics', params),
                api('/api/analytics/platform-compare', params)
            ]);
            renderKpi(summary);
            renderTrend(trend);
            renderSentiment(sent);
            renderDims(topics);
            renderPlatforms(plat);
            // 情感筛选是 S7 才补进筛选栏的：一旦生效，情感分布图必然只剩所选情感（占比 100%）。
            // 这不是"发现"，是筛选的数学结果，必须在口径行里说清楚，否则看图的人会误解。
            const zhSent = { positive: '正面', neutral: '中性', negative: '负面' };
            const sentNote = state.filters.sentiment.length
                ? ` 已按情感筛选（${state.filters.sentiment.map(x => zhSent[x] || x).join('、')}），`
                  + `占比为筛选后口径。`
                : '';
            $('#scopeNote').textContent =
                `数据口径：情感与维度由关键词规则标注；维度为多标签计数（合计大于评论总数属正常）；` +
                `当前统计范围 ${summary.meta.filters_text}；有效评论 ${num(summary.total)} 条。` + sentNote;
            if (summary.empty) toast('当前筛选范围没有评论，请放宽条件', 'err');
        } catch (e) {
            toast('加载失败：' + e.message, 'err');
            ['#chartTrend', '#chartSentiment', '#chartDims', '#chartPlatform'].forEach(id =>
                setState($(id), 'error', e.message, loadAll));
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
    /* ---------------- 工单概览一格 ---------------- */
    // 驾驶舱只回答"有没有人管"，不在这里做工单操作：
    // 大屏上的行动按钮最容易让人误点，真正的处置留在工单页。
    async function loadTicketStrip() {
        const box = document.getElementById('ticketStrip');
        if (!box) return;
        let b = null;
        try { b = await CP.loadTicketBadge(); } catch (e) { b = null; }
        if (!b || !b.can_view) { box.style.display = 'none'; return; }
        box.style.display = '';
        box.innerHTML = `
            <div class="tk-strip-main">待处置 <b>${b.pending}</b> 单</div>
            <div class="tk-strip-main ${b.overdue ? 'neg' : ''}">超期 <b>${b.overdue}</b> 单</div>
            <div class="tk-strip-sub">按工作时间口径计时 · 只处理议题，不处理人</div>
            <div class="tk-strip-gap"></div>
            <a class="btn btn-sm" href="/tickets">进入处置工单</a>`;
    }

    document.addEventListener('cp:ready', () => {
        loadAll();
        loadInsight();
        loadTicketStrip();
    });
    document.addEventListener('cp:filter', () => { loadAll(); loadInsight(); });
    document.addEventListener('cp:dataset', () => { loadAll(); loadInsight(); });
    // 切主题时图表颜色要重算（颜色是烘焙进 option 的，改不了已渲染的 canvas）
    document.addEventListener('cp:theme', () => { loadAll(); loadInsight(); });

    $('#rangeTabs').addEventListener('click', e => {
        const b = e.target.closest('[data-range]');
        if (!b) return;
        view.range = b.dataset.range;
        CP.$$('#rangeTabs .chip').forEach(x => x.classList.toggle('on', x === b));
        loadAll();
    });
    $('#granTabs').addEventListener('click', e => {
        const b = e.target.closest('[data-gran]');
        if (!b) return;
        view.gran = b.dataset.gran;
        CP.$$('#granTabs .chip').forEach(x => x.classList.toggle('on', x === b));
        loadAll();
    });
    $('#btnInsight').addEventListener('click', () => loadInsight(true));
    window.addEventListener('resize', () => {
        CP.$$('.chart').forEach(el => {
            const inst = CP.chart(el);
            if (inst) inst.resize();
        });
    });
})();
