/* PAGE-05 数据与报告中心 */
(function () {
    'use strict';
    const { $, $$, esc, num, pct, api, state, toast, setState } = CP;

    let currentReport = null;

    /* ---------------- 数据集列表 ---------------- */
    async function loadDatasets() {
        try {
            const d = await api('/api/datasets');
            state.datasets = d.datasets;
            $('#dsTable').innerHTML = `<table class="table"><thead>
                <tr><th>数据集</th><th>原始</th><th>有效</th><th>有效率</th><th>时间覆盖</th><th>平台</th><th></th></tr></thead><tbody>
                ${d.datasets.map(x => `<tr>
                    <td>
                        <div style="font-weight:600;color:var(--ink);font-size:12.5px;">${esc(x.name)}
                            ${x.dataset_id === state.dataset_id ? '<span class="badge badge-brand" style="margin-left:4px;">当前</span>' : ''}</div>
                        <div style="font-size:11px;color:var(--ink-4);margin-top:2px;">${esc(x.source_label || '')}</div>
                    </td>
                    <td class="num">${num(x.raw_count)}</td>
                    <td class="num">${num(x.valid_count)}</td>
                    <td class="num">${pct(x.effective_rate)}</td>
                    <td class="num" style="font-size:11.5px;">${esc(x.time_start || '—')}<br/>${esc(x.time_end || '—')}</td>
                    <td style="font-size:11.5px;">${Object.entries(x.platforms || {}).map(([k, v]) => `${esc(k)} ${num(v)}`).join('<br/>') || '—'}</td>
                    <td style="white-space:nowrap;">
                        <button class="btn btn-sm" data-use="${esc(x.dataset_id)}" ${x.dataset_id === state.dataset_id ? 'disabled' : ''}>切换</button>
                        <button class="btn btn-sm" data-re="${esc(x.dataset_id)}">重算</button>
                    </td></tr>`).join('')}
                </tbody></table>`;

            $$('#dsTable [data-use]').forEach(b => b.addEventListener('click', async () => {
                await switchDataset(b.dataset.use);
            }));
            $$('#dsTable [data-re]').forEach(b => b.addEventListener('click', async () => {
                b.disabled = true;
                b.textContent = '重算中';
                try {
                    const r = await api('/api/datasets/' + b.dataset.re + '/reanalyze', null, { method: 'POST' });
                    toast(`已重新清洗：原始 ${num(r.raw_count)} 条，有效 ${num(r.valid_count)} 条`, 'ok');
                    await loadDatasets();
                    if (b.dataset.re === state.dataset_id) loadQuality();
                } catch (e) {
                    toast('重算失败：' + e.message, 'err');
                } finally {
                    b.disabled = false;
                    b.textContent = '重算';
                }
            }));
        } catch (e) {
            $('#dsTable').innerHTML = '<div class="note note-warn">数据集加载失败：' + esc(e.message) + '</div>';
        }
    }

    async function switchDataset(id) {
        try {
            const d = await api('/api/datasets/select', null, {
                method: 'POST', body: JSON.stringify({ dataset_id: id })
            });
            state.dataset_id = d.dataset_id;
            state.datasetMeta = (state.datasets || []).find(x => x.dataset_id === id) || state.datasetMeta;
            // 切换数据集后清空旧筛选，避免把上一批的条件套到新数据上
            state.filters = { platform: [], dimension: [], sentiment: [], date_from: '', date_to: '', keyword: '' };
            CP.syncUrl();
            toast(`已切换到「${d.name}」，旧图表与 AI 回答不再代表当前数据`, 'ok');
            await loadDatasets();
            // 新数据集此刻才进到列表里，必须重建下拉，否则它选不中
            if (CP.renderDatasetSelect) CP.renderDatasetSelect();
            loadQuality();
            loadHistory();
            currentReport = null;
            $('#btnPrint').style.display = 'none';
            $('#reportBody').innerHTML = '<div class="state"><div class="state-title">数据集已切换</div>' +
                '<div>旧报告属于上一份数据，请重新生成，避免把旧结论当成新数据结果。</div></div>';
            document.dispatchEvent(new CustomEvent('cp:dataset', { detail: id }));
        } catch (e) {
            toast('切换失败：' + e.message, 'err');
        }
    }

    /* ---------------- 数据质量 ---------------- */
    async function loadQuality() {
        const ds = state.dataset_id;
        if (!ds) return;
        try {
            const d = await api('/api/datasets/' + ds + '/quality');
            const q = d.quality || {};
            $('#qualityRate').textContent = `有效率 ${pct(d.effective_rate)}`;
            $('#qualitySub').textContent = `${d.name} · 最后分析 ${d.analyzed_at || '—'}`;

            const stages = (q.stages || []).map(s => `
                <tr>
                    <td>${esc(s.stage)}</td>
                    <td style="font-size:11.5px;color:var(--ink-4);">${esc(s.note || '')}${s.file ? ' · ' + esc(s.file) : ''}</td>
                    <td class="num" style="text-align:right;color:${(s.count || 0) < 0 ? 'var(--neg)' : 'var(--ink)'};">
                        ${s.count < 0 ? '−' + num(Math.abs(s.count)) : num(s.count)}</td>
                </tr>`).join('');

            $('#qualityBody').innerHTML = `
                <div class="grid g-4" style="margin-bottom:12px;">
                    <div class="metric"><div class="metric-name">原始评论数</div>
                        <div class="metric-value">${num(d.raw_count)}<span class="metric-unit">条</span></div></div>
                    <div class="metric"><div class="metric-name">有效评论数</div>
                        <div class="metric-value">${num(d.valid_count)}<span class="metric-unit">条</span></div></div>
                    <div class="metric"><div class="metric-name">有效率</div>
                        <div class="metric-value">${pct(d.effective_rate)}</div></div>
                    <div class="metric"><div class="metric-name">点赞缺失</div>
                        <div class="metric-value">${num(q.missing_like || 0)}<span class="metric-unit">条</span></div></div>
                </div>
                <div class="grid g-2-1">
                    <div>
                        <table class="table"><thead><tr><th>清洗阶段</th><th>规则说明</th>
                            <th style="text-align:right">记录变化</th></tr></thead>
                            <tbody>${stages}</tbody></table>
                    </div>
                    <div>
                        <div class="note" style="margin-bottom:8px;">
                            有效率 = ${num(d.valid_count)} ÷ ${num(d.raw_count)} × 100% = ${pct(d.effective_rate)}
                            <br/><span style="opacity:.8">分子分母来自同一批次，可复算。</span>
                        </div>
                        <div class="note note-muted" style="margin-bottom:8px;">${esc(d.definitions.valid_count)}</div>
                        <div class="note note-muted">${esc(d.definitions.effective_rate)}</div>
                    </div>
                </div>`;
        } catch (e) {
            $('#qualityBody').innerHTML = '<div class="note note-warn">质量数据加载失败：' + esc(e.message) + '</div>';
        }
    }

    /* ---------------- 导入 ---------------- */
    function setupImport() {
        const zone = $('#dropZone'), input = $('#fileInput');
        zone.addEventListener('click', () => input.click());
        ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, e => {
            e.preventDefault(); zone.style.borderColor = '#165DFF'; zone.style.background = '#EEF4FF';
        }));
        ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, e => {
            e.preventDefault(); zone.style.borderColor = '#D8E4FF'; zone.style.background = '#FBFDFF';
        }));
        zone.addEventListener('drop', e => {
            if (e.dataTransfer.files.length) upload(e.dataTransfer.files);
        });
        input.addEventListener('change', () => {
            if (input.files.length) upload(input.files);
        });
    }

    async function upload(files) {
        const box = $('#importStatus');
        setState(box, 'loading');
        const fd = new FormData();
        Array.from(files).forEach(f => fd.append('files', f));
        try {
            const resp = await fetch('/api/datasets/import', { method: 'POST', body: fd });
            const body = await resp.json();
            if (!resp.ok || body.ok === false) {
                const msg = (body.error && body.error.message) || '导入失败';
                throw Object.assign(new Error(msg), { detail: body.error && body.error.detail });
            }
            const d = body.data;
            box.innerHTML = `<div class="note">
                导入成功：${esc(d.name)}，原始 ${num(d.raw_count)} 条 → 有效 ${num(d.valid_count)} 条（${pct(d.effective_rate)}）。
                ${d.rejected && d.rejected.length ? '被拒绝：' + d.rejected.map(r => esc(r.file + '（' + r.reason + '）')).join('；') : ''}
            </div>`;
            toast('导入成功，已切换到新数据集', 'ok');
            await switchDataset(d.dataset_id);
        } catch (e) {
            const detail = e.detail ? (Array.isArray(e.detail)
                ? e.detail.map(x => `${x.stage || x.file || ''}：${x.note || x.reason || ''}`).join('<br/>')
                : esc(String(e.detail))) : '';
            box.innerHTML = `<div class="note note-warn">导入失败：${esc(e.message)}${detail ? '<br/>' + detail : ''}</div>`;
            toast('导入失败：' + e.message, 'err');
        } finally {
            $('#fileInput').value = '';
        }
    }

    /* ---------------- 截图评论导入（多模态） ---------------- */
    // 复核表在内存里维护：识别一次（1–2 秒/张）后，用户改字、取消勾选都在本地完成，
    // 只有点"确认导入"才写库。中途关掉页面不会留下半成品数据集。
    let visSession = null;
    let visRows = [];
    let visMeta = null;

    async function loadVisionStatus() {
        const badge = $('#visionEngine');
        if (!badge) return;
        try {
            const d = await api('/api/vision/status');
            visMeta = d;
            if (!d.enabled) {
                badge.textContent = '未启用';
                badge.className = 'badge badge-warn';
                $('#visionDrop').style.display = 'none';
                $('#visionStatus').innerHTML =
                    '<div class="note note-warn">截图识别未启用（CP_VISION=0）。</div>';
                return;
            }
            if (!d.active) {
                badge.textContent = '无可用引擎';
                badge.className = 'badge badge-warn';
                $('#visionDrop').style.display = 'none';
                $('#visionStatus').innerHTML = `<div class="note note-warn">
                    没有可用的 OCR 引擎：<br/>${d.engines.map(e => esc(e.label) + ' — ' + esc(e.note)).join('<br/>')}
                    <br/>安装本地 OCR：<code>pip install rapidocr_onnxruntime</code>；或配置 <code>VISION_API_KEY</code> 使用视觉模型。</div>`;
                return;
            }
            const active = d.engines.find(e => e.name === d.active);
            badge.textContent = active ? active.label : d.active;
            badge.className = 'badge badge-brand';
            badge.title = active ? active.note : '';
        } catch (e) {
            badge.textContent = '检测失败';
            badge.className = 'badge badge-warn';
        }
    }

    function setupVisionImport() {
        const zone = $('#visionDrop'), input = $('#visionInput');
        if (!zone || !input) return;
        zone.addEventListener('click', () => input.click());
        ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, e => {
            e.preventDefault(); zone.style.borderColor = '#165DFF'; zone.style.background = '#EEF4FF';
        }));
        ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, e => {
            e.preventDefault(); zone.style.borderColor = '#D8E4FF'; zone.style.background = '#FBFDFF';
        }));
        zone.addEventListener('drop', e => {
            if (e.dataTransfer.files.length) recognize(e.dataTransfer.files);
        });
        input.addEventListener('change', () => {
            if (input.files.length) recognize(input.files);
        });
    }

    function confBadge(c) {
        // 置信度缺失时显示"模型直读"而不是 0%：视觉模型通道没有逐字得分，
        // 编一个数字出来会让"置信度"这个字段失去意义。
        if (c == null) return '<span class="badge">模型直读</span>';
        const p = Math.round(c * 100);
        const cls = p >= 90 ? 'badge-pos' : p >= 70 ? 'badge-neu' : 'badge-neg';
        return `<span class="badge ${cls}" title="逐字识别置信度均值">${p}%</span>`;
    }

    async function recognize(files) {
        const box = $('#visionStatus');
        setState(box, 'loading');
        $('#visionReview').innerHTML = '';
        const fd = new FormData();
        Array.from(files).forEach(f => fd.append('files', f));
        try {
            const resp = await fetch('/api/vision/recognize', { method: 'POST', body: fd });
            const body = await resp.json();
            if (!resp.ok || body.ok === false) {
                const msg = (body.error && body.error.message) || '识别失败';
                throw Object.assign(new Error(msg), { detail: body.error && body.error.detail });
            }
            const d = body.data;
            visSession = d.session_id;
            visRows = d.rows.map(r => Object.assign({}, r, { keep: true }));
            renderVisionStatus(d);
            renderVisionReview();
        } catch (e) {
            box.innerHTML = `<div class="note note-warn">识别失败：${esc(e.message)}
                ${e.detail ? '<br/>' + esc(String(e.detail)) : ''}</div>`;
            toast('截图识别失败：' + e.message, 'err');
        } finally {
            $('#visionInput').value = '';
        }
    }

    function renderVisionStatus(d) {
        const s = d.stats || {};
        const files = (d.files || []).map(f => f.ok
            ? `<span class="badge badge-pos">${esc(f.file)} · ${f.rows} 条 · ${f.elapsed_ms}ms</span>`
            : `<span class="badge badge-neg" title="${esc(f.detail || '')}">${esc(f.file)} · 失败</span>`
        ).join(' ');
        const warn = [];
        if (s.inferred_time) warn.push(`${s.inferred_time} 条截图里没有发布时间，按导入日期计入`);
        if (s.low_confidence) warn.push(`${s.low_confidence} 条置信度低于 ${Math.round((d.min_score || 0.55) * 100)}%`);
        if ((d.rejected || []).length) warn.push('被拒绝：' + d.rejected.map(r => r.file + '（' + r.reason + '）').join('；'));
        $('#visionStatus').innerHTML = `
            <div class="note">
                识别引擎：<b>${esc(d.engine_label || d.engine)}</b> · 共 ${s.rows || 0} 条候选，
                请逐条核对后再导入。${files ? '<br/>' + files : ''}
            </div>
            ${warn.length ? `<div class="note note-warn" style="margin-top:8px;">${warn.map(esc).join('<br/>')}</div>` : ''}`;
    }

    function renderVisionReview() {
        const box = $('#visionReview');
        if (!visRows.length) {
            box.innerHTML = '<div class="state"><div class="state-title">没有识别到评论正文</div>' +
                '<div>这张图可能只是界面截图，或者文字太小/太糊。换一张试试。</div></div>';
            return;
        }
        box.innerHTML = `
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px;">
                <span style="font-size:12.5px;font-weight:600;color:var(--ink);">复核（${visRows.length} 条候选）</span>
                <span style="font-size:11.5px;color:var(--ink-4);">正文可直接改；不想要的取消勾选即可</span>
                <div style="margin-left:auto;display:flex;gap:6px;">
                    <button class="btn btn-sm" data-vsel="all">全选</button>
                    <button class="btn btn-sm" data-vsel="none">全不选</button>
                    <button class="btn btn-sm" data-vsel="low">只取消低置信度</button>
                    <button class="btn btn-primary btn-sm" data-vcommit>确认导入</button>
                </div>
            </div>
            <div class="table-scroll">
            <table class="table"><thead><tr>
                <th style="width:34px;"></th><th>识别正文（可编辑）</th><th style="width:150px;">发布时间</th>
                <th style="width:96px;">平台</th><th style="width:86px;">置信度</th><th style="width:92px;">来源</th>
            </tr></thead><tbody>
            ${visRows.map((r, i) => `<tr data-i="${i}" style="${r.keep ? '' : 'opacity:.45;'}">
                <td><input type="checkbox" data-keep ${r.keep ? 'checked' : ''}></td>
                <td><input class="input" data-field="content" value="${esc(r.content)}"
                        style="width:100%;font-size:12.5px;" title="${esc(r.content)}"></td>
                <td><input class="input" data-field="create_time" value="${esc(r.create_time || '')}"
                        style="font-size:11.5px;width:100%;"
                        title="${esc(r.time_source || '')}">
                    ${r.time_inferred ? '<div style="font-size:10.5px;color:var(--warn-ink);margin-top:2px;">无原文时间，按导入日期</div>' : ''}</td>
                <td><select class="select" data-field="platform" style="font-size:11.5px;width:100%;">
                    ${['抖音', '小红书', '未知'].map(p => `<option value="${p}" ${r.platform === p ? 'selected' : ''}>${p}</option>`).join('')}
                </select></td>
                <td>${confBadge(r.confidence)}${r.needs_review ? '<div style="font-size:10.5px;color:var(--warn-ink);margin-top:2px;">需重点复核</div>' : ''}</td>
                <td style="font-size:11px;color:var(--ink-4);">${esc(r.file || '')}<br/>
                    <span style="font-family:ui-monospace,monospace;">${esc(r.engine || '')}</span></td>
            </tr>`).join('')}
            </tbody></table></div>
            <div class="note note-muted" style="margin-top:8px;">
                导入后这些记录会标记为「截图识别」，证据页可查到它们的置信度与识别引擎；
                人工复核这一步不做，OCR 的错字就会直接进统计口径。
            </div>`;

        // 输入即更新内存态：不重渲染整表，否则打字打到一半光标会被踢走
        CP.$$('#visionReview [data-i]').forEach(tr => {
            const i = Number(tr.dataset.i);
            tr.querySelector('[data-keep]').addEventListener('change', e => {
                visRows[i].keep = e.target.checked;
                tr.style.opacity = e.target.checked ? '' : '.45';
            });
            tr.querySelectorAll('[data-field]').forEach(inp => {
                inp.addEventListener('input', () => { visRows[i][inp.dataset.field] = inp.value; });
                inp.addEventListener('change', () => { visRows[i][inp.dataset.field] = inp.value; });
            });
        });
        CP.$$('#visionReview [data-vsel]').forEach(b => b.addEventListener('click', () => {
            const mode = b.dataset.vsel;
            visRows.forEach(r => {
                if (mode === 'all') r.keep = true;
                else if (mode === 'none') r.keep = false;
                else if (mode === 'low') r.keep = !(r.confidence != null && r.confidence < 0.9);
            });
            renderVisionReview();
        }));
        CP.$$('#visionReview [data-vcommit]').forEach(b => b.addEventListener('click', commitVision));
    }

    async function commitVision() {
        const rows = visRows.filter(r => r.keep && r.content.trim().length >= 5);
        if (!rows.length) { toast('请至少保留一条评论', 'err'); return; }
        const btn = $('#visionReview [data-vcommit]');
        if (btn) { btn.disabled = true; btn.textContent = '导入中…'; }
        try {
            const d = await api('/api/vision/commit', null, {
                method: 'POST',
                body: JSON.stringify({
                    session_id: visSession,
                    rows: rows.map(r => ({
                        rid: r.rid, file: r.file, content: r.content,
                        create_time: r.create_time, platform: r.platform
                    }))
                })
            });
            visRows = []; visSession = null;
            $('#visionReview').innerHTML = '';
            $('#visionStatus').innerHTML = `<div class="note">
                已导入：${esc(d.name)}，提交 ${d.submitted} 条 → 有效 ${num(d.valid_count)} 条
                （截图来源 ${num(d.screenshot_count || 0)} 条，其中无原文时间 ${num(d.time_inferred || 0)} 条）。
            </div>`;
            toast('截图评论已入库，并切换到新数据集', 'ok');
            await switchDataset(d.dataset_id);
        } catch (e) {
            toast('导入失败：' + e.message, 'err');
            $('#visionStatus').innerHTML = `<div class="note note-warn">导入失败：${esc(e.message)}</div>`;
        } finally {
            const b2 = $('#visionReview [data-vcommit]');
            if (b2) { b2.disabled = false; b2.textContent = '确认导入'; }
        }
    }

    /* ---------------- 报告 ---------------- */
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
        $('#btnPrint').style.display = '';
        $('#btnMarkdown').style.display = '';
        const c = r.content || {};
        $('#reportBody').innerHTML = `
        <div id="reportSheet">
            <div style="display:flex;align-items:flex-start;gap:12px;flex-wrap:wrap;margin-bottom:12px;">
                <div>
                    <div style="font-size:18px;font-weight:700;letter-spacing:-.3px;">
                        ${esc(r.dataset.name)} · 舆情综合分析报告</div>
                    <div style="font-size:12px;color:var(--ink-4);margin-top:4px;">
                        来源：${esc(r.dataset.data_source || '—')} ｜ 数据版本：${esc(r.dataset.data_version || '—')} ｜
                        时间范围：${esc((r.dataset.time_span || []).join(' ~ '))} ｜ 生成时间：${esc(r.generated_at)}
                    </div>
                </div>
                <div style="margin-left:auto;display:flex;gap:6px;flex-wrap:wrap;">
                    <span class="badge ${r.engine === 'deepseek' ? 'badge-brand' : 'badge-warn'}">${esc(r.engine_label)}</span>
                    <span class="badge">${num(r.kpi.total)} 条有效评论</span>
                </div>
            </div>

            ${r.notice ? `<div class="note note-warn" style="margin-bottom:12px;">${esc(r.notice)}</div>` : ''}
            <div class="note note-muted" style="margin-bottom:12px;">AI 生成内容已标注来源与口径；系统不对评论指控的真实性作判断，建议内容需人工审核后使用。</div>

            <div class="grid g-4" style="margin-bottom:14px;">
                <div class="metric"><div class="metric-name">有效评论</div>
                    <div class="metric-value">${num(r.kpi.total)}<span class="metric-unit">条</span></div></div>
                <div class="metric"><div class="metric-name">正面占比</div>
                    <div class="metric-value">${pct(r.kpi.positive_rate)}</div>
                    <div class="metric-name">${num(Math.round(r.kpi.total * r.kpi.positive_rate / 100))} / ${num(r.kpi.denominator)}</div></div>
                <div class="metric"><div class="metric-name">负面占比</div>
                    <div class="metric-value">${pct(r.kpi.negative_rate)}</div>
                    <div class="metric-name">${num(r.kpi.negative)} / ${num(r.kpi.denominator)}</div></div>
                <div class="metric"><div class="metric-name">最热维度</div>
                    <div class="metric-value" style="font-size:20px;">${esc(r.kpi.top_dimension || '—')}</div></div>
            </div>

            <h4 style="font-size:13.5px;margin:16px 0 6px;">一、总体概况</h4>
            <p style="font-size:13px;line-height:1.85;color:var(--ink-2);margin:0;">${esc(c.summary || '')}</p>

            <h4 style="font-size:13.5px;margin:16px 0 6px;">二、主要讨论维度</h4>
            <p style="font-size:13px;line-height:1.85;color:var(--ink-2);margin:0;">${esc(c.dimension_insight || '')}</p>

            <h4 style="font-size:13.5px;margin:16px 0 6px;">三、情感表现</h4>
            <p style="font-size:13px;line-height:1.85;color:var(--ink-2);margin:0;">${esc(c.sentiment_insight || '')}</p>

            <h4 style="font-size:13.5px;margin:16px 0 8px;">四、建议（需人工审核）</h4>
            <div style="display:grid;gap:8px;">
                ${(c.suggestions || []).map((s, i) => `
                    <div style="border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--surface-3);">
                        <div style="font-size:12.5px;font-weight:600;color:var(--ink);">${i + 1}. ${esc(s.title || '建议')}</div>
                        <div style="font-size:12.5px;color:var(--ink-2);line-height:1.7;margin-top:3px;">${esc(s.content || '')}</div>
                        ${s.basis ? `<div style="font-size:11px;color:var(--ink-4);margin-top:4px;">依据：${esc(s.basis)}</div>` : ''}
                    </div>`).join('')}
            </div>

            <h4 style="font-size:13.5px;margin:16px 0 6px;">五、需人工核实与口径提示</h4>
            <ul style="font-size:12.5px;line-height:1.9;color:var(--ink-2);margin:0;padding-left:18px;">
                ${(c.cautions || []).map(x => `<li>${esc(x)}</li>`).join('')}
            </ul>

            ${(r.evidence || []).length ? `<h4 style="font-size:13.5px;margin:16px 0 8px;">六、评论证据（已脱敏）</h4>
            <div>${r.evidence.map(e => `
                <div style="border-left:3px solid var(--brand-line);padding:6px 10px;margin-bottom:6px;background:var(--surface-3);">
                    <div style="font-size:12.5px;color:var(--ink-2);line-height:1.7;">${esc(e.snippet)}</div>
                    <div style="font-size:11px;color:var(--ink-4);margin-top:3px;">
                        ${esc(e.platform)} · ${esc(e.date)} · ${esc(e.sentiment_label)} ·
                        <span style="font-family:ui-monospace,monospace">${esc(e.comment_ref)}</span></div>
                </div>`).join('')}</div>` : ''}

            <h4 style="font-size:13.5px;margin:16px 0 6px;">附：统计口径与局限</h4>
            <ul style="font-size:12px;line-height:1.85;color:var(--ink-3);margin:0;padding-left:18px;">
                ${(r.limitations || []).map(x => `<li>${esc(x)}</li>`).join('')}
            </ul>
        </div>`;
        toast('报告已生成', 'ok');
    }

    async function loadHistory() {
        try {
            const d = await api('/api/reports', { dataset_id: state.dataset_id, limit: 8 });
            if (!d.reports.length) {
                $('#historyBody').innerHTML = '<div class="state">当前数据集暂无历史报告</div>';
                return;
            }
            $('#historyBody').innerHTML = `<table class="table"><thead>
                <tr><th>生成时间</th><th>引擎</th><th>摘要</th><th></th></tr></thead><tbody>
                ${d.reports.map(x => `<tr>
                    <td style="font-size:12px;white-space:nowrap;">${esc(x.created_at)}</td>
                    <td><span class="badge ${x.engine === 'deepseek' ? 'badge-brand' : 'badge-warn'}">${x.engine === 'deepseek' ? 'DeepSeek' : '规则'}</span></td>
                    <td style="font-size:12px;color:var(--ink-2);">${esc((x.summary || '').slice(0, 90))}…</td>
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
        loadDatasets();
        loadQuality();
        loadHistory();
        setupImport();
        loadVisionStatus();
        setupVisionImport();
    });
    document.addEventListener('cp:dataset', () => { loadQuality(); loadHistory(); loadDatasets(); });

    $('#btnReport').addEventListener('click', generateReport);
    $('#btnRestore').addEventListener('click', async () => {
        if (!confirm('将重新清洗内置数据集，并覆盖当前的内置数据版本。上传的数据集不受影响。继续？')) return;
        try {
            const r = await api('/api/datasets/restore-demo', null, { method: 'POST' });
            toast(`已恢复 ${r.restored.length} 个内置数据集`, 'ok');
            await loadDatasets();
            loadQuality();
        } catch (e) {
            toast('恢复失败：' + e.message, 'err');
        }
    });
    $('#btnPrint').addEventListener('click', () => {
        if (!currentReport) { toast('请先生成报告', 'err'); return; }
        window.print();
    });
    // 导出 Markdown：直接走后端路由，前端不拼内容——
    // 否则"页面上看到的"和"导出的"迟早会不一致
    $('#btnMarkdown').addEventListener('click', () => {
        if (!currentReport) { toast('请先生成报告', 'err'); return; }
        window.location.href = '/api/reports/' + encodeURIComponent(currentReport.report_id) + '/export.md';
        toast('已开始下载 Markdown 报告', 'ok');
    });
})();
