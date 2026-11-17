/* PAGE-05 父项：数据集与质量
 *
 * 拆分说明（2026-11）：
 * 这一页原来还有文件导入、截图导入、报告生成、历史报告，一屏 1300px 以上，
 * "生成报告"按钮要滚两屏才看得到。按数据流拆成三页后，这里只留两个问题：
 *   1) 现在有哪些数据集、用的是哪个
 *   2) 这份数据被清洗掉了什么（分子分母能复算）
 * 采集/导入在 data_collect.js，报告在 data_reports.js。
 */
(function () {
    'use strict';
    const { $, $$, esc, num, pct, api, state, toast } = CP;

    /* ---------------- 数据集列表 ---------------- */
    /* 四行数字放在一起会被横着比："2022-2026 覆盖更长，为什么有效条数比 2020-2026 还少？"
       每行其实是一个独立批次，原始量与清洗命中率各不相同，跨行比大小没有意义。
       表格下面那句说明必须留着，否则这一屏会替使用者制造一个说不清的漏洞。 */
    async function loadDatasets() {
        try {
            const d = await api('/api/datasets');
            state.datasets = d.datasets;
            $('#dsTable').innerHTML = `<table class="table"><caption class="sr-only">数据集列表：原始条数、有效条数、有效率与时间覆盖</caption><thead>
                <tr><th scope="col">数据集</th><th scope="col">原始</th><th scope="col">有效</th><th scope="col">有效率</th><th scope="col">时间覆盖</th><th scope="col">平台</th><th scope="col"></th></tr></thead><tbody>
                ${d.datasets.map(x => `<tr>
                    <td>
                        <div class="ds-name">${esc(x.name)}
                            ${x.dataset_id === state.dataset_id ? '<span class="badge badge-brand" style="margin-left:4px;">当前</span>' : ''}</div>
                        <div class="ds-src">${esc(x.source_label || '')}</div>
                    </td>
                    <td class="num">${num(x.raw_count)}</td>
                    <td class="num">${num(x.valid_count)}</td>
                    <td class="num">${pct(x.effective_rate)}${x.valid_count === x.raw_count
                        ? '<span class="ds-none" title="这一批没有任何记录被清洗规则剔除">无剔除</span>' : ''}</td>
                    <td class="num ds-span">${esc(x.time_start || '—')}<br/>${esc(x.time_end || '—')}</td>
                    <td class="ds-plat">${Object.entries(x.platforms || {}).map(([k, v]) => `${esc(k)} ${num(v)}`).join('<br/>') || '—'}</td>
                    <td>
                        <div class="ds-actions">
                            <button class="btn btn-sm" data-use="${esc(x.dataset_id)}" ${x.dataset_id === state.dataset_id ? 'disabled' : ''}>切换</button>
                            <button class="btn btn-sm" data-merge="${esc(x.dataset_id)}"
                                    title="把整个数据集并入另一个数据集，并入后本数据集会被删除">并入</button>
                            <button class="btn btn-sm btn-danger" data-re="${esc(x.dataset_id)}"
                                    title="重算会覆盖当前数据版本，全站数字与历史报告口径都会跟着变">重算</button>
                            <button class="btn btn-sm btn-danger" data-del="${esc(x.dataset_id)}"
                                    title="删除数据集及其评论、历史报告与研判记录">删除</button>
                        </div>
                    </td></tr>`).join('')}
                </tbody></table>
                <div class="note note-muted" style="margin-top:10px;">
                    <span>每行是一个<b>独立批次</b>：原始条数与有效率只在本批内部成立，<b>不能跨行比较</b>——
                    不同批次的采集范围与清洗命中率不同，覆盖时段更长的那一批不一定条数更多。
                    有效率 100% 表示该批次没有任何记录被清洗规则剔除（原始文件本身已是干净样本）。</span>
                </div>`;

            $$('#dsTable [data-use]').forEach(b => b.addEventListener('click', async () => {
                await switchTo(b.dataset.use);
            }));
            $$('#dsTable [data-re]').forEach(b => b.addEventListener('click', async () => {
                // 重算会覆盖数据版本、改掉全站数字，代价比「恢复内置数据」更大，
                // 却曾是这一页唯一没有确认的一枚按钮。问清楚再动手。
                const id = b.dataset.re;
                const ds = /** @type {any} */ ((state.datasets || []).find(x => x.dataset_id === id) || {});
                const ok = await CP.confirmModal({
                    title: '重新清洗这个数据集？',
                    // 110：sub 默认会被 esc，这里内层已 esc 过名字，改用 subHtml 免得二次转义
                    subHtml: `会用仓库里的原始数据把「${esc(ds.name || id)}」的 ${num(ds.raw_count)} 条重跑一遍清洗。`,
                    note: '清洗结果覆盖当前版本：有效率、主题与情感标注都可能变化，' +
                          '所有页面上的数字会跟着变，历史报告的口径也会指向新版本。' +
                          '正在演示时不要点它。',
                    okLabel: '开始重算'
                });
                if (!ok) return;
                b.disabled = true;
                b.setAttribute('aria-busy', 'true');
                b.textContent = '重算中…';
                try {
                    const r = await api('/api/datasets/' + b.dataset.re + '/reanalyze', null, { method: 'POST' });
                    toast(`已重新清洗：原始 ${num(r.raw_count)} 条，有效 ${num(r.valid_count)} 条`, 'ok');
                    CP.announce(`重算完成：原始 ${num(r.raw_count)} 条，有效 ${num(r.valid_count)} 条`);
                    await loadDatasets();
                    if (b.dataset.re === state.dataset_id) loadQuality();
                } catch (e) {
                    toast('重算失败：' + e.message, 'err');
                } finally {
                    b.disabled = false;
                    b.removeAttribute('aria-busy');
                    b.textContent = '重算';
                }
            }));
            $$('#dsTable [data-merge]').forEach(b => b.addEventListener('click', () => {
                const ds = (state.datasets || []).find(x => x.dataset_id === b.dataset.merge);
                if (ds) mergeDataset(ds, b);
            }));
            $$('#dsTable [data-del]').forEach(b => b.addEventListener('click', () => {
                const ds = (state.datasets || []).find(x => x.dataset_id === b.dataset.del);
                if (ds) deleteDataset(ds, b);
            }));
        } catch (e) {
            $('#dsTable').innerHTML = '<div class="note note-warn">数据集加载失败：' + esc(e.message) + '</div>';
        }
    }

    /* 并入目标只能在其它数据集里选。用自定义弹窗而不是 confirmModal：
       后者只能弹一句"确定吗"，选不了目标，等于让用户先记住 id 再去猜。 */
    function pickMergeTarget(ds) {
        const targets = (state.datasets || []).filter(x => x.dataset_id !== ds.dataset_id);
        if (!targets.length) {
            toast('没有其它数据集可以并入，请先采集或导入一份新数据', 'err');
            return Promise.resolve(null);
        }
        return new Promise(resolve => {
            const mask = document.createElement('div');
            mask.className = 'modal-mask';
            mask.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="cpMergeTitle">
                <h2 class="modal-title" id="cpMergeTitle">并入其它数据集</h2>
                <div class="card-sub">「${esc(ds.name)}」的 ${num(ds.valid_count)} 条有效评论会并入目标数据集。</div>
                <label class="filter-label" for="cpMergeTarget" style="display:block;margin:14px 0 6px;">目标数据集</label>
                <select class="select" id="cpMergeTarget" data-target style="width:100%;">
                    ${targets.map(t => `<option value="${esc(t.dataset_id)}">${esc(t.name)} · 有效 ${num(t.valid_count)} 条</option>`).join('')}
                </select>
                <div class="note note-warn" style="margin-top:12px;">
                    <span>与目标重复的评论按「平台 + 正文」跳过。源数据集并入后会<b>被删除</b>，
                    它的历史报告、AI 运行记录与研判快照也会一并清理；引用它的工单会保留，
                    但原始证据会显示为失联。操作不可撤销。</span>
                </div>
                <div class="tk-modal-foot" style="margin-top:14px;">
                    <button class="btn btn-sm btn-ghost" data-cancel type="button">取消</button>
                    <button class="btn btn-sm btn-danger-solid" data-ok type="button">并入并删除源数据集</button>
                </div>
            </div>`;
            document.body.appendChild(mask);
            const sel = /** @type {HTMLSelectElement} */ (mask.querySelector('[data-target]'));
            let done = false;
            const finish = v => {
                if (done) return;
                done = true;
                CP.closeModal(mask);
                mask.remove();
                resolve(v);
            };
            CP.openModal(mask, { onEsc: () => finish(null) });
            mask.addEventListener('click', e => { if (e.target === mask) finish(null); });
            /** @type {any} */ (mask.querySelector('[data-cancel]'))
                .addEventListener('click', () => finish(null));
            /** @type {any} */ (mask.querySelector('[data-ok]'))
                .addEventListener('click', () => finish(sel.value || null));
        });
    }

    async function mergeDataset(ds, btn) {
        const targetId = await pickMergeTarget(ds);
        if (!targetId) return;
        btn.disabled = true;
        btn.textContent = '并入中…';
        try {
            const r = await api('/api/datasets/' + encodeURIComponent(ds.dataset_id) + '/merge', null, {
                method: 'POST', body: JSON.stringify({ target_dataset_id: targetId })
            });
            const affected = state.dataset_id === ds.dataset_id || state.dataset_id === targetId;
            if (affected) {
                await CP.switchDataset(targetId);
            } else {
                await CP.loadDatasets();
                await loadDatasets();
            }
            toast(`已把「${r.source_name}」并入「${r.name}」：新增 ${num(r.added)} 条，` +
                  `跳过重复 ${num(r.skipped)} 条`, 'ok');
        } catch (e) {
            toast('并入失败：' + e.message, 'err');
        } finally {
            btn.disabled = false;
            btn.textContent = '并入';
        }
    }

    async function deleteDataset(ds, btn) {
        const ok = await CP.confirmModal({
            title: '删除这个数据集？',
            subHtml: `会删除「${esc(ds.name)}」的 ${num(ds.raw_count)} 条原始记录 / ${num(ds.valid_count)} 条有效评论。`,
            note: '历史报告、AI 运行记录与研判快照会一并删除，上传的源文件也会清理；' +
                  '引用它的工单会保留，但原始证据会显示为失联。' +
                  '内置数据集可以在「维护操作 → 恢复内置数据」里重建；' +
                  '如果删的是当前数据集，会自动切换到剩下的数据集。操作不可撤销。',
            okLabel: '删除数据集'
        });
        if (!ok) return;
        btn.disabled = true;
        btn.textContent = '删除中…';
        try {
            const r = await api('/api/datasets/' + encodeURIComponent(ds.dataset_id), null,
                                { method: 'DELETE' });
            const wasCurrent = state.dataset_id === ds.dataset_id;
            if (wasCurrent && r.fallback_dataset_id) {
                await CP.switchDataset(r.fallback_dataset_id);
                toast(`已删除「${r.name}」，已切换到「${r.fallback_name}」`, 'ok');
            } else {
                await CP.loadDatasets();
                await loadDatasets();
                toast(`已删除「${r.name}」`, 'ok');
            }
        } catch (e) {
            toast('删除失败：' + e.message, 'err');
        } finally {
            btn.disabled = false;
            btn.textContent = '删除';
        }
    }

    async function switchTo(id) {
        try {
            const d = await CP.switchDataset(id);
            toast(`已切换到「${d.name}」，旧图表与 AI 回答不再代表当前数据`, 'ok');
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
            // 036：正文只显示到小时，完整秒级时间戳放 title，别把秒当成有效精度展示
            const at = String(d.analyzed_at || '');
            const hm = at.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}):\d{2}:\d{2}/);
            const coarse = hm ? `${hm[1]} ${hm[2]}时` : (at || '—');
            $('#qualitySub').textContent = `${d.name} · 最后分析 ${coarse}`;
            $('#qualitySub').title = at ? `最后分析 ${at}` : '';

            const stages = (q.stages || []).map(s => `
                <tr>
                    <td>${esc(s.stage)}</td>
                    <td style="font-size:var(--fs-micro);color:var(--ink-4);">${esc(s.note || '')}</td>
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
                        <table class="table"><caption class="sr-only">清洗阶段与记录变化</caption><thead><tr><th scope="col">清洗阶段</th><th scope="col">规则说明</th>
                            <th scope="col" style="text-align:right">记录变化</th></tr></thead>
                            <tbody>${stages}</tbody></table>
                    </div>
                    <div>
                        <div class="note note-flat" style="margin-bottom:8px;">
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

    /* ---------------- 初始化 ---------------- */
    document.addEventListener('cp:ready', () => {
        loadDatasets();
        loadQuality();
    });
    document.addEventListener('cp:dataset', () => { loadQuality(); loadDatasets(); });

    // 「恢复内置数据」和行内「重算」一样会覆盖数据版本，给它同一套危险色，
    // 别让两颗会改动全站数字的按钮和「去采集数据」长得一模一样。
    $('#btnRestore').classList.add('btn-danger');
    $('#btnRestore').addEventListener('click', async () => {
        const ok = await CP.confirmModal({
            title: '重新清洗内置数据集？',
            subHtml: '会用仓库里的原始快照重跑一遍清洗，<b>覆盖当前的内置数据版本</b>。上传的数据集不受影响。',
            note: '正在用内置数据集做演示时，重跑会把清洗结果换成与当前代码一致的那一版，历史报告里的数字可能跟着变。',
            okLabel: '重新清洗'
        });
        if (!ok) return;
        try {
            const r = await api('/api/datasets/restore-demo', null, { method: 'POST' });
            toast(`已恢复 ${r.restored.length} 个内置数据集`, 'ok');
            await loadDatasets();
            loadQuality();
        } catch (e) {
            toast('恢复失败：' + e.message, 'err');
        }
    });
})();
