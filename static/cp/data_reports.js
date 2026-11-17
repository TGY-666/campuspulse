/* PAGE-05.2 AI 分析报告
 *
 * 从「数据与报告中心」拆出来的独立页：报告是这一整套数据的**出口**，
 * 它跟"数据怎么进来"不是同一件事，混在一页里时生成按钮被压在最底部。
 * 拆开后这一页只有一个动作：选当前数据集 → 生成 → 导出 / 重开历史。
 */
(function () {
    'use strict';
    const { $, $$, esc, num, pct, api, state, toast, setState } = CP;

    let currentReport = null;

    /* 036 对外只显示到日 / 小时：秒级时间戳留在 title，需要核对时再展开。
       后端给的是 YYYY-MM-DD HH:mm:ss，也可能已是短格式，统一兜底、不编造。 */
    function displayTime(s) {
        const t = String(s == null ? '' : s).trim();
        if (!t) return '—';
        const m = t.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})(?::\d{2})?/);
        return m ? `${m[1]} ${m[2]}时` : t;
    }
    function timeTitle(s) { return String(s == null ? '' : s).trim() || '—'; }

    /* 027 空态预览：模板里那段「还没有生成报告」只有一行，下半屏是空的。
       补一层「报告包含什么」的骨架说明，等生成之前就知道会得到什么。 */
    function mountReportPreview() {
        const body = $('#reportBody');
        if (!body || body.querySelector('#cpReportPreview')) return;
        body.insertAdjacentHTML('beforeend',
            `<div id="cpReportPreview" class="note note-muted" style="margin-top:12px;line-height:1.9;">
                <b>报告包含什么：</b>一句话结论 / 总体概况 / 关键发现（每条附可回溯的证据编号与评论原文）/
                建议（需人工审核）/ 需人工核实与口径提示 / 脱敏评论证据 / 统计口径与局限；
                并附有效评论数、正负面占比、最热主题、生成引擎与生成时间标识。
            </div>`);
    }

    /* ---------------- 生成 ---------------- */
    async function generateReport() {
        const btn = $('#btnReport');
        btn.disabled = true;
        btn.textContent = '生成中…';
        setState($('#reportBody'), 'loading');
        try {
            const r = await api('/api/reports/generate', null, {
                method: 'POST',
                body: JSON.stringify({ dataset_id: state.dataset_id })
            });
            currentReport = r;
            renderReport(r);
            loadHistory();
        } catch (e) {
            $('#reportBody').innerHTML = `<div class="note note-warn">报告生成失败：${esc(e.message)}</div>`;
            toast('报告生成失败：' + e.message, 'err');
        } finally {
            btn.disabled = false;
            btn.textContent = '生成报告';
        }
    }

    function renderReport(r) {
        $('#btnPrint').classList.remove('is-hidden');
        $('#btnMarkdown').classList.remove('is-hidden');
        const c = r.content || {};
        $('#reportSub').textContent = `${r.dataset.name} · 生成于 ${displayTime(r.generated_at)}`;
        $('#reportSub').title = `完整生成时间：${timeTitle(r.generated_at)}`;

        /* 证据引用 → 评论原文。模型只输出编号，原文永远从这里取，
           保证「结论旁引用的那句评论」和证据区看到的是同一条。 */
        const evMap = {};
        (r.evidence || []).forEach(e => { evMap[e.comment_ref] = e; });
        const quoteHtml = refs => (refs || []).map(ref => {
            const e = evMap[ref];
            if (!e) return '';
            return `<div style="margin-top:6px;border-left:2px solid var(--brand-line);padding:4px 8px;background:var(--surface-2);">
                <div style="font-size:var(--fs-xs);color:var(--ink-2);line-height:1.7;">${esc(e.snippet)}</div>
                <div style="font-size:var(--fs-micro);color:var(--ink-4);margin-top:2px;">
                    ${e.theme ? esc(e.theme) + ' · ' : ''}${esc(e.platform)} · ${esc(e.date)} · ${esc(e.sentiment_label)} ·
                    <span style="font-family:ui-monospace,monospace">${esc(ref)}</span></div>
            </div>`;
        }).join('');

        const findings = c.findings || [];
        // 新报告按「关键发现」组织；历史报告没有 findings，保留原来的主题/情感两段
        const findingsHtml = findings.length ? `
            <h4 style="font-size:var(--fs-base);margin:16px 0 8px;">二、关键发现</h4>
            <div style="display:grid;gap:10px;">
                ${findings.map((f, i) => `
                    <div style="border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--surface-3);">
                        <div style="font-size:var(--fs-sm);font-weight:600;color:var(--ink);">${i + 1}. ${esc(f.title || '关键发现')}</div>
                        <div style="font-size:var(--fs-sm);color:var(--ink-2);line-height:1.8;margin-top:3px;">${esc(f.text || '')}</div>
                        ${quoteHtml(f.evidence_refs)}
                    </div>`).join('')}
            </div>` : `
            <h4 style="font-size:var(--fs-base);margin:16px 0 6px;">二、主要讨论主题</h4>
            <p style="font-size:var(--fs-sm);line-height:1.85;color:var(--ink-2);margin:0;">${esc(c.dimension_insight || '')}</p>

            <h4 style="font-size:var(--fs-base);margin:16px 0 6px;">三、情感表现</h4>
            <p style="font-size:var(--fs-sm);line-height:1.85;color:var(--ink-2);margin:0;">${esc(c.sentiment_insight || '')}</p>`;
        const sec = findings.length ? ['三', '四', '五'] : ['四', '五', '六'];

        $('#reportBody').innerHTML = `
        <div id="reportSheet">
            <div style="display:flex;align-items:flex-start;gap:12px;flex-wrap:wrap;margin-bottom:12px;">
                <div>
                    <div style="font-size:var(--fs-lg);font-weight:700;letter-spacing:-.3px;">
                        ${esc(r.dataset.name)} · 舆情综合分析报告</div>
                    <div style="font-size:var(--fs-xs);color:var(--ink-4);margin-top:4px;">
                        来源：${esc(r.dataset.data_source || '—')} ｜ 数据版本：${esc(String(r.dataset.data_version || '—').slice(0, 16))} ｜
                        时间范围：${esc((r.dataset.time_span || []).join(' ~ '))} ｜ 生成时间：<span title="完整时间 ${esc(timeTitle(r.generated_at))}">${esc(displayTime(r.generated_at))}</span>
                    </div>
                </div>
                <div style="margin-left:auto;display:flex;gap:6px;flex-wrap:wrap;">
                    <span class="badge ${r.engine === 'deepseek' ? 'badge-brand' : 'badge-warn'}">${esc(r.engine_label)}</span>
                    <span class="badge">${num(r.kpi.total)} 条有效评论</span>
                </div>
            </div>

            ${r.notice ? `<div class="note note-warn" style="margin-bottom:12px;">${esc(r.notice)}</div>` : ''}
            <div class="note note-muted" style="margin-bottom:12px;">AI 生成内容已标注来源与口径；系统不对评论指控的真实性作判断，建议内容需人工审核后使用。</div>

            ${c.headline ? `<div style="border-left:3px solid var(--brand-line);padding:8px 12px;margin:0 0 14px;background:var(--surface-3);font-size:var(--fs-lg);font-weight:700;line-height:1.55;letter-spacing:-.2px;">${esc(c.headline)}</div>` : ''}

            <div class="grid g-4" style="margin-bottom:14px;">
                <div class="metric"><div class="metric-name">有效评论</div>
                    <div class="metric-value">${num(r.kpi.total)}<span class="metric-unit">条</span></div></div>
                <div class="metric"><div class="metric-name">正面占比</div>
                    <div class="metric-value">${pct(r.kpi.positive_rate)}</div>
                    <div class="metric-name">${num(r.kpi.positive != null ? r.kpi.positive
                        : Math.round(r.kpi.total * r.kpi.positive_rate / 100))} / ${num(r.kpi.denominator)}</div></div>
                <div class="metric"><div class="metric-name">负面占比</div>
                    <div class="metric-value">${pct(r.kpi.negative_rate)}</div>
                    <div class="metric-name">${num(r.kpi.negative)} / ${num(r.kpi.denominator)}</div></div>
                <div class="metric"><div class="metric-name">最热主题</div>
                    <div class="metric-value" style="font-size:var(--fs-xl);">${esc(r.kpi.top_dimension || '—')}</div></div>
            </div>

            <h4 style="font-size:var(--fs-base);margin:16px 0 6px;">一、总体概况</h4>
            <p style="font-size:var(--fs-sm);line-height:1.85;color:var(--ink-2);margin:0;">${esc(c.summary || '')}</p>

            ${findingsHtml}

            <h4 style="font-size:var(--fs-base);margin:16px 0 8px;">${sec[0]}、建议（需人工审核）</h4>
            <div style="display:grid;gap:8px;">
                ${(c.suggestions || []).map((s, i) => `
                    <div style="border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--surface-3);">
                        <div style="font-size:var(--fs-sm);font-weight:600;color:var(--ink);">${i + 1}. ${esc(s.title || '建议')}</div>
                        <div style="font-size:var(--fs-sm);color:var(--ink-2);line-height:1.7;margin-top:3px;">${esc(s.content || '')}</div>
                        ${s.basis ? `<div style="font-size:var(--fs-micro);color:var(--ink-4);margin-top:4px;">依据：${esc(s.basis)}</div>` : ''}
                        ${quoteHtml(s.evidence_refs)}
                    </div>`).join('')}
            </div>

            <h4 style="font-size:var(--fs-base);margin:16px 0 6px;">${sec[1]}、需人工核实与口径提示</h4>
            <ul style="font-size:var(--fs-sm);line-height:1.9;color:var(--ink-2);margin:0;padding-left:18px;">
                ${(c.cautions || []).map(x => `<li>${esc(x)}</li>`).join('')}
            </ul>

            ${(r.evidence || []).length ? `<h4 style="font-size:var(--fs-base);margin:16px 0 8px;">${sec[2]}、评论证据（已脱敏，编号可回溯）</h4>
            <div>${r.evidence.map(e => `
                <div style="border-left:3px solid var(--brand-line);padding:6px 10px;margin-bottom:6px;background:var(--surface-3);">
                    <div style="font-size:var(--fs-sm);color:var(--ink-2);line-height:1.7;">${esc(e.snippet)}</div>
                    <div style="font-size:var(--fs-micro);color:var(--ink-4);margin-top:3px;">
                        ${e.theme ? esc(e.theme) + ' · ' : ''}${esc(e.platform)} · ${esc(e.date)} · ${esc(e.sentiment_label)} ·
                        <span style="font-family:ui-monospace,monospace">${esc(e.comment_ref)}</span></div>
                </div>`).join('')}</div>` : ''}

            <h4 style="font-size:var(--fs-base);margin:16px 0 6px;">附：统计口径与局限</h4>
            <ul style="font-size:var(--fs-xs);line-height:1.85;color:var(--ink-3);margin:0;padding-left:18px;">
                ${(r.limitations || []).map(x => `<li>${esc(x)}</li>`).join('')}
            </ul>
        </div>`;
        toast('报告已生成', 'ok');
    }

    /* ---------------- 历史 ---------------- */
    async function loadHistory() {
        try {
            const d = await api('/api/reports', { dataset_id: state.dataset_id, limit: 8 });
            if (!d.reports.length) {
                $('#historyBody').innerHTML = `<div class="state" style="min-height:120px;">
                    <div class="state-title">当前数据集暂无历史报告</div>
                    <div>点右上角「生成报告」后，这里按生成时刻的数据版本存档，之后导入新数据不会改写它。</div>
                    <div style="margin:10px auto 0;text-align:left;max-width:560px;font-size:var(--fs-xs);color:var(--ink-3);line-height:1.9;">
                        <b>一份报告包含：</b>① 一句话结论 ② 总体概况 ③ 关键发现（每条附证据） ④ 建议（需人工审核）
                        ⑤ 需人工核实与口径提示 ⑥ 脱敏评论证据 ⑦ 统计口径与局限，
                        以及有效评论数、正 / 负面占比、最热主题、生成引擎与时间标识。
                    </div>
                </div>`;
                return;
            }
            $('#historyBody').innerHTML = `<table class="table"><caption class="sr-only">历史报告列表</caption><thead>
                <tr><th scope="col">生成时间</th><th scope="col">引擎</th><th scope="col" class="num">有效评论</th>
                    <th scope="col">摘要</th><th scope="col"></th></tr></thead><tbody>
                ${d.reports.map(x => `<tr>
                    <td style="font-size:var(--fs-xs);white-space:nowrap;">
                        <div title="完整时间 ${esc(timeTitle(x.created_at))}">${esc(displayTime(x.created_at))}</div>
                        ${x.data_version ? `<div class="rp-ver">数据版本 ${esc(String(x.data_version).slice(0, 16))}</div>` : ''}</td>
                    <td style="white-space:nowrap;"><span class="badge ${x.engine === 'deepseek' ? 'badge-brand' : 'badge-warn'}"
                        title="${x.engine === 'deepseek' ? 'DeepSeek 大模型生成' : '统计规则降级生成'}">${
                        x.engine === 'deepseek' ? 'AI' : '规则'}</span></td>
                    <td class="num" style="white-space:nowrap;font-variant-numeric:tabular-nums;">${
                        x.kpi && x.kpi.total != null ? num(x.kpi.total) : '—'}</td>
                    <td style="font-size:var(--fs-xs);color:var(--ink-2);">${esc((x.summary || '').slice(0, 90))}…</td>
                    <td><button class="btn btn-sm" data-open="${esc(x.report_id)}">查看</button></td>
                </tr>`).join('')}</tbody></table>`;
            $$('#historyBody [data-open]').forEach(b => b.addEventListener('click', async () => {
                try {
                    const r = await api('/api/reports/' + b.dataset.open);
                    currentReport = r;
                    renderReport(r);
                    window.scrollTo({ top: $('#reportCard').offsetTop - 70, behavior: 'smooth' });
                } catch (e) {
                    toast('报告打开失败：' + e.message, 'err');
                }
            }));
        } catch (e) {
            $('#historyBody').innerHTML = '<div class="note note-warn">历史报告加载失败：' + esc(e.message) + '</div>';
        }
    }

    /* ---------------- 初始化 ---------------- */
    document.addEventListener('cp:ready', () => {
        const meta = state.datasetMeta;
        if (meta) $('#reportSub').textContent = `${meta.name} · 尚未生成`;
        mountReportPreview();
        loadHistory();
    });

    // 换数据集后旧报告必须失效：它讲的是上一批数据，留在页面上会被读成"新数据的结果"。
    document.addEventListener('cp:dataset', () => {
        currentReport = null;
        $('#btnPrint').classList.add('is-hidden');
        $('#btnMarkdown').classList.add('is-hidden');
        const meta = state.datasetMeta;
        $('#reportSub').textContent = meta ? `${meta.name} · 尚未生成` : '';
        $('#reportBody').innerHTML = '<div class="state"><div class="state-title">数据集已切换</div>' +
            '<div>旧报告属于上一份数据，请重新生成，避免把旧结论当成新数据结果。</div></div>';
        mountReportPreview();
        loadHistory();
    });

    $('#btnReport').addEventListener('click', generateReport);
    $('#btnPrint').addEventListener('click', () => {
        if (!currentReport) { toast('请先生成报告', 'err'); return; }
        window.print();
    });
    // 导出 Markdown 走后端路由：前端拼内容迟早会与页面显示不一致
    $('#btnMarkdown').addEventListener('click', () => {
        if (!currentReport) { toast('请先生成报告', 'err'); return; }
        window.location.href = '/api/reports/' + encodeURIComponent(currentReport.report_id) + '/export.md';
        toast('已开始下载 Markdown 报告', 'ok');
    });
})();
