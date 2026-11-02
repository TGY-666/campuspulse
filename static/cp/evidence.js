/* PAGE-04 热点与证据中心 */
(function () {
    'use strict';
    const { $, $$, esc, num, pct, api, state, toast, setState } = CP;

    const PAGE_SIZE = 20;
    // 初始页码与排序从 URL 读：否则翻到第 5 页后一刷新就回第 1 页，
    // 也没法把"这一页的证据"发给别人复核
    const _qp = new URLSearchParams(location.search);
    let offset = Math.max(0, parseInt(_qp.get('offset') || '0', 10) || 0);
    let order = ['likes', 'time_desc', 'time_asc'].includes(_qp.get('order')) ? _qp.get('order') : 'likes';
    let total = 0;
    // 在 core.js 规范化 URL 之前先抓住 ref，否则会被 syncUrl 清掉
    const INIT_REF = new URLSearchParams(location.search).get('ref');

    /* ---------------- 维度排行 ---------------- */
    async function loadRanking() {
        try {
            const t = await api('/api/analytics/topics', CP.filterParams());
            $('#rankTotal').textContent = `${num(t.meta.sample_size)} 条评论`;
            if (!t.ranking.length) {
                $('#rankList').innerHTML = '<div class="state"><div class="state-title">当前范围没有维度数据</div></div>';
                return;
            }
            const max = Math.max(...t.ranking.map(r => r.count)) || 1;
            $('#rankList').innerHTML = t.ranking.map(r => `
                <div class="rank-row" data-dim="${esc(r.dimension)}"
                     style="display:flex;align-items:center;gap:10px;padding:8px 8px;border-radius:9px;cursor:pointer;transition:.15s;">
                    <span style="font-size:12.5px;color:var(--ink-2);width:62px;flex:0 0 62px;">${esc(r.dimension)}</span>
                    <span style="flex:1;height:8px;background:var(--line-2);border-radius:5px;overflow:hidden;">
                        <i style="display:block;height:100%;width:${Math.max(3, r.count / max * 100)}%;
                           background:linear-gradient(90deg,#BCD2FF,#165DFF);border-radius:5px;"></i>
                    </span>
                    <span style="font-size:12px;color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums;width:46px;text-align:right;">${num(r.count)}</span>
                    <span class="badge ${r.negative_rate >= 12 ? 'badge-neg' : 'badge-neu'}" style="width:74px;justify-content:center;">负面 ${pct(r.negative_rate)}</span>
                </div>`).join('');
            $$('#rankList .rank-row').forEach(row => {
                row.addEventListener('mouseenter', () => row.style.background = '#F7FAFF');
                row.addEventListener('mouseleave', () => row.style.background = 'transparent');
                row.addEventListener('click', () => {
                    const dim = row.dataset.dim;
                    state.filters.dimension = state.filters.dimension[0] === dim ? [] : [dim];
                    const bar = $('#filterBar');
                    const sel = bar && bar.querySelector('[data-role="dimension"]');
                    if (sel) sel.value = state.filters.dimension[0] || '';
                    CP.syncUrl();
                    offset = 0;
                    loadAll();
                });
            });
        } catch (e) {
            $('#rankList').innerHTML = '<div class="note note-warn">排行加载失败：' + esc(e.message) + '</div>';
        }
    }

    /* ---------------- 证据列表 ---------------- */
    function scopeBlock(d) {
        const m = d.meta;
        $('#scopeKv').innerHTML = `
            <dt>数据集</dt><dd>${esc(m.dataset_name)}（${esc(m.dataset_id)}）</dd>
            <dt>筛选条件</dt><dd>${esc(m.filters_text)}</dd>
            <dt>命中条数</dt><dd>${num(d.total)} 条</dd>
            <dt>时间范围</dt><dd>${esc((m.time_span || []).join(' ~ ') || '—')}</dd>
            <dt>数据版本</dt><dd>${esc(m.data_version || '—')}</dd>`;
    }

    async function loadResults() {
        const params = CP.filterParams({ limit: PAGE_SIZE, offset, order });
        const box = $('#resultList');
        setState(box, 'loading');
        try {
            const d = await api('/api/comments', params);
            total = d.total;
            $('#totalBadge').textContent = `共 ${num(d.total)} 条`;
            $('#resultSub').textContent = d.empty ? '当前条件下没有匹配评论'
                : `第 ${d.offset + 1}–${d.offset + d.items.length} 条，共 ${num(d.total)} 条；点击查看详情`;
            scopeBlock(d);

            if (d.empty) {
                setState(box, 'empty', '换一个关键词或放宽时间范围试试。');
                $('#pager').innerHTML = '';
                return;
            }

            box.innerHTML = d.items.map(it => `
                <div class="evidence-item" data-ref="${esc(it.comment_ref)}" style="margin-bottom:10px;">
                    <div class="ev-snippet">${highlight(it.snippet, it.matched_keyword)}</div>
                    <div class="ev-meta">
                        <span class="badge badge-${it.sentiment === 'positive' ? 'pos' : it.sentiment === 'negative' ? 'neg' : 'neu'}">${esc(it.sentiment_label)}</span>
                        <span>${esc(it.platform)}</span><span>${esc(it.date)}</span>
                        ${it.dimension ? `<span>${esc(it.dimension)}</span>` : ''}
                        ${it.like_count != null ? `<span>${num(it.like_count)} 赞</span>` : '<span>点赞未提供</span>'}
                        <span class="ev-ref" style="margin-left:auto;">${esc(it.comment_ref)}</span>
                    </div>
                </div>`).join('');

            $$('#resultList .evidence-item').forEach(el =>
                el.addEventListener('click', () => openDetail(el.dataset.ref)));

            const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
            const cur = Math.floor(offset / PAGE_SIZE) + 1;
            $('#pager').innerHTML = `
                <span style="font-size:12px;color:var(--ink-4);">第 ${cur} / ${pages} 页</span>
                <button class="btn btn-sm" ${cur <= 1 ? 'disabled' : ''} data-page="prev">上一页</button>
                <button class="btn btn-sm" ${cur >= pages ? 'disabled' : ''} data-page="next">下一页</button>`;
            $$('#pager [data-page]').forEach(b => b.addEventListener('click', () => {
                offset = b.dataset.page === 'next' ? offset + PAGE_SIZE : Math.max(0, offset - PAGE_SIZE);
                syncPagingUrl();
                loadResults();
            }));
        } catch (e) {
            setState(box, 'error', e.message, loadResults);
            toast('证据检索失败：' + e.message, 'err');
        }
    }

    function highlight(text, kw) {
        const safe = esc(text || '');
        if (!kw) return safe;
        const needle = esc(kw);
        if (!needle) return safe;
        return safe.split(needle).join(
            `<mark style="background:var(--warn-soft);color:var(--warn-ink);padding:0 2px;border-radius:3px;">${needle}</mark>`);
    }

    /* ---------------- 详情 ---------------- */
    // 工单入口只在"开了工单 + 当前角色能建单"时出现。
    // 缓存一次是因为证据弹窗会被反复打开，每次多打一个接口没必要。
    let ticketCap = null;
    async function canTicket() {
        if (ticketCap !== null) return ticketCap;
        try {
            const m = await api('/api/tickets/meta');
            ticketCap = !!m.can_create;
        } catch (e) { ticketCap = false; }
        return ticketCap;
    }

    function ticketTitle(d) {
        const dim = d.dimension || (d.dimensions || [])[0] || '议题';
        const head = String(d.content_masked || '').replace(/\s+/g, ' ').slice(0, 22);
        return `「${dim}」证据线索：${head}`.slice(0, 120);
    }

    async function openDetail(ref) {
        let d = null;
        try {
            d = await api('/api/comments/' + encodeURIComponent(ref), { dataset_id: state.dataset_id });
        } catch (e) {
            toast('证据加载失败：' + e.message, 'err');
            return;
        }
        const withTicket = await canTicket();
        const mask = document.createElement('div');
        mask.className = 'modal-mask';
        mask.innerHTML = `<div class="modal">
            <div style="display:flex;align-items:flex-start;gap:10px;">
                <div>
                    <div class="modal-title">证据详情</div>
                    <div class="card-sub">${esc(d.comment_ref)} · 内部引用 ID（可复现，不含身份信息）</div>
                </div>
                <button class="btn btn-sm btn-ghost" style="margin-left:auto" data-close>关闭</button>
            </div>
            <div class="modal-quote">${esc(d.content_masked)}</div>
            <dl class="kv">
                <dt>所属数据集</dt><dd>${esc(d.dataset_name || '')}（${esc(d.dataset_id)}）</dd>
                <dt>统计口径</dt><dd>${esc(d.filters_text || '')}</dd>
                <dt>平台</dt><dd>${esc(d.platform)}</dd>
                <dt>发布时间</dt><dd>${esc(d.comment_time || d.comment_date || '')}</dd>
                <dt>情感标签</dt><dd>${esc(d.sentiment_label)}</dd>
                <dt>业务维度</dt><dd>${esc((d.dimensions || []).join(' / ') || d.dimension || '—')}</dd>
                <dt>点赞数</dt><dd>${d.like_count == null ? '未提供（不按 0 处理）' : num(d.like_count)}</dd>
                ${d.negative_reason ? `<dt>负面原因</dt><dd>${esc(d.negative_reason)}</dd>` : ''}
            </dl>
            <div class="note note-muted" style="margin-top:12px;">${esc(d.privacy_note || '')}</div>
            <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end;">
                ${withTicket ? '<button class="btn btn-sm" data-ticket>据此建单</button>' : ''}
                <button class="btn btn-primary" data-close>关闭</button>
            </div>
        </div>`;
        document.body.appendChild(mask);
        const close = () => mask.remove();
        mask.addEventListener('click', async e => {
            if (e.target.closest('[data-ticket]')) {
                const btn = e.target.closest('[data-ticket]');
                btn.disabled = true;
                btn.textContent = '建单中…';
                try {
                    const t = await api('/api/tickets', null, {
                        method: 'POST',
                        body: JSON.stringify({
                            title: ticketTitle(d),
                            summary: `由证据 ${d.comment_ref} 发起：${String(d.content_masked || '').slice(0, 160)}`,
                            priority: 'P2',
                            dimension: d.dimension || (d.dimensions || [])[0] || '',
                            filters: { dataset_id: d.dataset_id },
                            comment_ref: d.comment_ref
                        })
                    });
                    close();
                    toast(`已建单 ${t.code}，可在工单页确认与分派`, 'ok');
                    location.href = '/tickets?ticket=' + encodeURIComponent(t.ticket_id);
                } catch (err) {
                    btn.disabled = false;
                    btn.textContent = '据此建单';
                    toast(err.message, 'err');
                }
                return;
            }
            if (e.target === mask || e.target.closest('[data-close]')) close();
        });
        document.addEventListener('keydown', function onEsc(ev) {
            if (ev.key === 'Escape') { close(); document.removeEventListener('keydown', onEsc); }
        });
    }

    /* ---------------- 异常提示（P1 · 透明阈值） ---------------- */
    const TH_LABELS = {
        volume_jump_pct: '近 7 天评论量环比增幅',
        volume_min_base: '基期最少条数',
        negative_gap_pp: '负面率高出区间的百分点',
        negative_min_recent: '近 7 天最少条数',
        reason_share_pct: '单一负面原因占比',
        reason_min_count: '该原因最少条数',
        hot_like_min: '高互动负面的点赞门槛',
    };

    async function loadRisk() {
        try {
            const r = await api('/api/analytics/risk', CP.filterParams());
            const list = $('#riskList');
            const badge = $('#riskBadge');
            if (r.empty) {
                badge.textContent = '无数据';
                list.innerHTML = '<div class="state"><div class="state-title">当前筛选范围内没有可分析的评论</div></div>';
                return;
            }
            if (r.window) {
                $('#riskSub').textContent =
                    `近 7 天 ${r.window.recent[0]} ~ ${r.window.recent[1]}（对比 ${r.window.previous[0]} ~ ${r.window.previous[1]}），阈值固定且全部披露`;
            }
            badge.textContent = r.flags.length ? `${r.flags.length} 条提示` : '未触发阈值';
            badge.className = 'badge ' + (r.flags.length ? 'badge-neu' : 'badge-ok');

            if (!r.flags.length) {
                list.innerHTML = `<div class="note note-muted" style="margin:0;">
                    近 7 天没有指标越过设定阈值（讨论量环比、负面率偏离、负面原因集中、高互动负面）。
                    这不等于"没有问题"，只表示在当前样本里没有统计异常。</div>`;
            } else {
                list.innerHTML = r.flags.map(f => `
                    <div style="padding:10px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:8px;background:var(--surface-3);">
                        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                            <span class="badge ${f.level === '关注' ? 'badge-neg' : 'badge-neu'}">${esc(f.level)}</span>
                            <span style="font-size:13.5px;font-weight:600;color:var(--ink);">${esc(f.title)}</span>
                        </div>
                        <div style="font-size:12.5px;color:var(--ink-2);margin-top:5px;line-height:1.65;">${esc(f.detail)}</div>
                        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;">
                            ${Object.entries(f.basis || {}).map(([k, v]) =>
                                `<span class="badge" style="background:#F1F5FD;color:var(--ink-2);">${esc(k)}：${esc(String(v))}</span>`).join('')}
                        </div>
                        ${(f.evidence || []).length ? `<div style="margin-top:7px;display:flex;flex-direction:column;gap:4px;">
                            ${f.evidence.map(ev => `<a href="javascript:void(0)" data-ref="${esc(ev.comment_ref)}"
                                style="font-size:12px;color:var(--brand);text-decoration:none;">
                                ${esc(ev.comment_ref)} · ${esc(ev.platform)} · ${esc(ev.date || '')} · 点赞 ${num(ev.like_count)}
                                <span style="color:var(--ink-3);">「${esc(ev.snippet)}」</span></a>`).join('')}
                        </div>` : ''}
                    </div>`).join('');
                $$('#riskList a[data-ref]').forEach(a => {
                    a.addEventListener('click', () => openDetail(a.dataset.ref));
                });
            }

            const note = $('#riskNote');
            note.style.display = 'block';
            note.innerHTML = `${esc(r.disclaimer)}
                <details style="margin-top:6px;">
                    <summary style="cursor:pointer;color:var(--brand);">查看阈值定义</summary>
                    <div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;">
                        ${Object.entries(r.thresholds || {}).map(([k, v]) =>
                            `<span class="badge" style="background:#F1F5FD;color:var(--ink-2);">${esc(TH_LABELS[k] || k)}：${esc(String(v))}</span>`).join('')}
                    </div>
                </details>`;
        } catch (e) {
            $('#riskBadge').textContent = '加载失败';
            $('#riskList').innerHTML = `<div class="state"><div class="state-title">异常提示加载失败</div>
                <div class="state-sub">${esc(e.message)}</div></div>`;
        }
    }

    /* ---------------- 加载 ---------------- */
    function loadAll() {
        loadRanking();
        loadRisk();
        loadResults();
    }

    document.addEventListener('cp:ready', () => {
        // 从 URL 恢复排序时，下拉框必须跟着显示——否则数据和控件说的是两回事，
        // 用户会以为排序没生效，再点一次才发现"怎么又变了"
        const sel = $('#orderSelect');
        if (sel) sel.value = order;
        loadAll();
        // 从 AI 回答或其它页面带 ref 跳转过来时，自动打开对应证据
        if (INIT_REF) setTimeout(() => openDetail(INIT_REF), 700);
    });
    document.addEventListener('cp:filter', () => { offset = 0; loadAll(); });
    document.addEventListener('cp:dataset', () => { offset = 0; loadAll(); });

    // 页码与排序进 URL；offset=0 / 默认排序时清掉参数，保持地址栏干净
    function syncPagingUrl() {
        CP.syncUrl({ offset: offset || '', order: order === 'likes' ? '' : order });
    }

    $('#orderSelect').addEventListener('change', e => {
        order = e.target.value;
        offset = 0;
        syncPagingUrl();
        loadResults();
    });
})();
