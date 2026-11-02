/* PAGE-06 处置工单 —— 状态机在前端只做"展示可用动作"，
   真正的合法性由后端判定（409/403）。前端多算一遍只会造出两套规则。 */
(function () {
    'use strict';
    const { $, $$, esc, num, api, toast, state, setState } = CP;

    const view = {
        group: 'open', priority: '', q: '', offset: 0,
        currentId: null, meta: null, stats: null, list: null
    };
    const PAGE = 20;

    const PRIORITY_CLS = { P0: 'p0', P1: 'p1', P2: 'p2', P3: 'p3' };
    const STATUS_TONE = {
        draft: 'warn', triaged: 'info', assigned: 'info', processing: 'run',
        pending_review: 'warn', reopened: 'neg', closed: 'ok', rejected: ''
    };

    /* ---------------- KPI ---------------- */
    function renderKpi(st) {
        const bx = $('#tkKpi');
        if (!bx) return;
        const by = {};
        (st.by_status || []).forEach(s => { by[s.key] = s.count; });
        const processing = (by.assigned || 0) + (by.processing || 0);
        const ot = st.on_time || {};
        const cards = [
            { label: '待分派', value: num(st.pending_assign), unit: '单',
              foot: `待确认 ${by.draft || 0} 单 · 待复核 ${by.pending_review || 0} 单`, tone: 'warn' },
            { label: '处理中', value: num(processing), unit: '单',
              foot: `已分派 ${by.assigned || 0} · 处理中 ${by.processing || 0}`, tone: '' },
            { label: '已超期', value: num(st.overdue), unit: '单',
              foot: st.overdue ? '按工作时间口径判定，不计周末与午休' : '当前没有超期工单', tone: st.overdue ? 'neg' : 'ok' },
            { label: '平均处置时长', value: st.avg_handle_hours == null ? '—' : st.avg_handle_hours,
              unit: st.avg_handle_hours == null ? '' : '小时',
              foot: ot.denominator
                  ? `按时归档 ${ot.numerator}/${ot.denominator} = ${ot.rate}%`
                  : '尚无归档工单，按时率暂无分母', tone: '' }
        ];
        bx.innerHTML = cards.map(c => `
            <div class="card kpi">
                <div class="kpi-label">${esc(c.label)}<span class="tk-dot ${c.tone}"></span></div>
                <div class="kpi-value">${esc(String(c.value))}${c.unit ? `<span class="kpi-unit">${esc(c.unit)}</span>` : ''}</div>
                <div class="kpi-foot">${esc(c.foot)}</div>
            </div>`).join('');
    }

    /* ---------------- 规则建议 ---------------- */
    function renderSuggestions(d) {
        const card = $('#suggCard');
        if (!card) return;
        card.style.display = '';
        const list = $('#suggList');
        const items = d.suggestions || [];
        $('#suggBadge').textContent = items.length ? `命中 ${items.length} 条` : '未命中';
        $('#suggSub').textContent = d.anchor
            ? `评估锚点 ${d.anchor}（数据最后一天）· 4 条固定规则，命中后由人决定是否建单`
            : '当前数据集没有可用日期';
        if (!items.length) {
            list.innerHTML = `<div class="note">当前筛选范围内没有命中规则。系统不会为了"显得智能"而报警，
                样本不足时宁可说样本不足——一次假报警，之后所有提示都会被当成噪音。</div>`;
        } else {
            list.innerHTML = items.map((s, i) => `
                <div class="tk-sugg" data-i="${i}">
                    <div class="tk-sugg-head">
                        <span class="badge badge-brand">${esc(s.rule_id)} ${esc(s.rule_name)}</span>
                        <span class="tk-pri-text ${PRIORITY_CLS[s.priority]}">${esc(s.priority)} · ${esc(priorityLabel(s.priority))}</span>
                        ${s.dimension ? `<span class="badge">${esc(s.dimension)}</span>` : ''}
                        ${s.dup ? `<span class="badge badge-warn">已有 ${esc(s.dup.code)}</span>` : ''}
                        <span class="tk-sugg-gap"></span>
                        <button class="btn btn-sm ${s.dup ? '' : 'btn-primary'}" data-sugg="${i}"
                                ${s.dup || !canCreate() ? 'disabled' : ''}>据此建单</button>
                    </div>
                    <div class="tk-sugg-title">${esc(s.title)}</div>
                    <div class="tk-sugg-detail">${esc(s.detail)}</div>
                    <div class="tk-sugg-basis">${Object.entries(s.basis || {})
                        .map(([k, v]) => `<span>${esc(k)} <b>${esc(String(v))}</b></span>`).join('')}</div>
                </div>`).join('');
        }
        const skipped = d.skipped || [];
        const note = $('#suggNote');
        if (skipped.length) {
            note.style.display = '';
            note.innerHTML = '<b>未评估的规则：</b>' + skipped.map(s =>
                `${esc(s.rule_id)} ${esc(s.reason)}`).join('；') + '。不评估也是一种结论。';
        } else {
            note.style.display = 'none';
        }

        list.querySelectorAll('[data-sugg]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const s = items[+btn.dataset.sugg];
                btn.disabled = true;
                btn.textContent = '建单中…';
                try {
                    const t = await api('/api/tickets/from-suggestion', null, {
                        method: 'POST',
                        body: JSON.stringify({ rule_id: s.rule_id, dimension: s.dimension,
                                               dataset_id: state.dataset_id })
                    });
                    toast(`已建单 ${t.code}，等待确认与分派`, 'ok');
                    await refreshAll();
                    openTicket(t.ticket_id);
                } catch (e) {
                    toast(e.message, 'err');
                    btn.disabled = false;
                    btn.textContent = '据此建单';
                }
            });
        });
    }

    function priorityLabel(p) {
        return ({ P0: '紧急', P1: '高', P2: '中', P3: '低' })[p] || p;
    }

    function canCreate() { return !!(view.meta && view.meta.can_create); }
    function canManage() { return !!(view.meta && view.meta.can_manage); }

    /* ---------------- 列表 ---------------- */
    function renderList(d) {
        view.list = d;
        $('#tkTotal').textContent = `${d.total} 单`;
        const box = $('#tkList');
        if (!d.tickets.length) {
            setState(box, 'empty', '当前筛选下没有工单。可以先从上方规则建议建单，或点"新建工单"。');
            $('#tkPager').innerHTML = '';
            return;
        }
        box.innerHTML = d.tickets.map(t => {
            const sla = t.sla || {};
            const tone = sla.overdue ? 'neg' : (sla.remaining_minutes != null && sla.remaining_minutes <= 120 ? 'warn' : '');
            return `
            <div class="tk-item ${view.currentId === t.ticket_id ? 'on' : ''}" data-id="${esc(t.ticket_id)}">
                <i class="tk-pri ${PRIORITY_CLS[t.priority] || 'p2'}" title="${esc(t.priority)}"></i>
                <div class="tk-item-main">
                    <div class="tk-item-title">
                        <span class="tk-code">${esc(t.code)}</span>
                        <span>${esc(t.title)}</span>
                    </div>
                    <div class="tk-item-meta">
                        <span class="badge badge-${STATUS_TONE[t.status] || ''}">${esc(t.status_label)}</span>
                        ${t.dimension ? `<span>${esc(t.dimension)}</span>` : '<span>全部维度</span>'}
                        <span>${t.owner_name ? '负责人 ' + esc(t.owner_name) : '未分派'}</span>
                        ${t.rule_id ? `<span>规则 ${esc(t.rule_id)}</span>` : ''}
                    </div>
                </div>
                <div class="tk-item-sla ${tone}">${esc(sla.text || '')}
                    <em>${esc((sla.due || '').slice(5, 16))}</em></div>
            </div>`;
        }).join('');
        box.querySelectorAll('[data-id]').forEach(el => {
            el.addEventListener('click', () => openTicket(el.dataset.id));
        });
        renderPager(d);
    }

    function renderPager(d) {
        const wrap = $('#tkPager');
        const pages = Math.max(1, Math.ceil(d.total / d.limit));
        const cur = Math.floor(d.offset / d.limit) + 1;
        if (pages <= 1) { wrap.innerHTML = ''; return; }
        wrap.innerHTML = `<button class="btn btn-sm" data-p="-1" ${cur <= 1 ? 'disabled' : ''}>上一页</button>
            <span>第 ${cur} / ${pages} 页</span>
            <button class="btn btn-sm" data-p="1" ${cur >= pages ? 'disabled' : ''}>下一页</button>`;
        wrap.querySelectorAll('[data-p]').forEach(b => {
            b.addEventListener('click', () => {
                view.offset = Math.max(0, view.offset + (+b.dataset.p) * PAGE);
                loadList();
            });
        });
    }

    /* ---------------- 详情 ---------------- */
    function drawDetail(t) {
        view.currentId = t.ticket_id;
        $$('#tkList .tk-item').forEach(el =>
            el.classList.toggle('on', el.dataset.id === t.ticket_id));
        const box = $('#tkDetail');
        const sla = t.sla || {};
        const ev = (t.events || []).map(e => `
            <li class="tk-ev">
                <i class="tk-ev-dot"></i>
                <div>
                    <div class="tk-ev-top">
                        <b>${esc(e.type_label)}</b>
                        <span>${esc(e.actor_name || e.actor_id)}</span>
                        <em>${esc((e.created_at || '').slice(5, 16))}</em>
                    </div>
                    ${e.from_status && e.to_status && e.from_status !== e.to_status
                        ? `<div class="tk-ev-flow">${esc(statusLabel(e.from_status))} → ${esc(statusLabel(e.to_status))}</div>` : ''}
                    ${e.note ? `<div class="tk-ev-note">${esc(e.note)}</div>` : ''}
                </div>
            </li>`).join('');

        const evid = (t.evidence || []).map(e => `
            <div class="evidence-item">
                <div class="ev-snippet">${esc(e.snippet || '')}</div>
                <div class="ev-meta">
                    <span class="ev-ref">${esc(e.comment_ref || '')}</span>
                    <span>${esc(e.platform || '')}</span>
                    <span>${esc((e.date || '').slice(0, 10))}</span>
                    ${e.like_count != null ? `<span>赞 ${num(e.like_count)}</span>` : ''}
                </div>
            </div>`).join('') || '<div class="note note-muted">这张工单没有关联证据（人工建单时未指定）。</div>';

        const acts = (t.actions || []).map(a => `
            <button class="btn btn-sm ${a.primary ? 'btn-primary' : ''}" data-act="${esc(a.action)}">
                ${esc(a.label)}<span class="tk-act-to">→ ${esc(a.to_label)}</span></button>`).join('')
            || '<span class="note note-muted">当前身份在此状态下没有可执行操作。</span>';

        box.innerHTML = `
            <div class="card-head">
                <div>
                    <div class="card-title"><span class="tk-code">${esc(t.code)}</span> ${esc(t.title)}</div>
                    <div class="card-sub">
                        <span class="badge badge-${STATUS_TONE[t.status] || ''}">${esc(t.status_label)}</span>
                        <span class="tk-pri-text ${PRIORITY_CLS[t.priority]}">${esc(t.priority)} · ${esc(t.priority_label)}</span>
                        ${t.dimension ? `<span class="badge">${esc(t.dimension)}</span>` : ''}
                        ${t.rule_id ? `<span class="badge badge-brand">规则 ${esc(t.rule_id)}</span>` : ''}
                    </div>
                </div>
                <div class="card-tools">
                    <button class="btn btn-sm btn-ghost" id="tkClose" type="button">关闭</button>
                </div>
            </div>

            <div class="tk-sla-box ${sla.overdue ? 'neg' : ''}">
                <div><span>${esc(sla.text || '')}</span>
                    <em>归档时限 ${esc(sla.due || '未起算')}</em></div>
                <div class="tk-sla-foot">只计工作时间（工作日 09:00–12:00、14:00–18:00）；
                    首次响应要求 ${esc(String((t.first_response_hours || slaFirst(t.priority)) || '—'))} 个工作小时
                    ${t.first_response_at ? `· 实际 ${esc(t.first_response_at.slice(5, 16))}` : '· 尚未响应'}</div>
            </div>

            <div class="tk-block">
                <div class="tk-block-title">建单口径 <em>存的是口径，不是当时的数字</em></div>
                <div class="note note-muted" style="margin:0 0 6px;">${esc(t.filters_text || '—')}</div>
                ${t.filter_preview ? `<div class="tk-preview">按此口径重算：
                    ${num(t.filter_preview.total)} 条 · 负面 ${num(t.filter_preview.negative)} 条 ·
                    负面占比 ${t.filter_preview.negative_rate == null ? '—' : t.filter_preview.negative_rate + '%'}</div>` : ''}
            </div>

            <div class="tk-block">
                <div class="tk-block-title">代表证据 <em>只展示脱敏文本，不含昵称与账号</em></div>
                <div class="tk-evidence">${evid}</div>
            </div>

            <div class="tk-block">
                <div class="tk-block-title">处置时间线 <em>append-only，写入后不可改删</em></div>
                <ul class="tk-timeline">${ev || '<li class="note note-muted">暂无事件</li>'}</ul>
            </div>

            <div class="tk-block">
                <div class="tk-block-title">可执行操作</div>
                <div class="tk-acts">${acts}</div>
            </div>

            ${['closed', 'rejected'].indexOf(t.status) < 0 ? `
            <div class="tk-block">
                <div class="tk-block-title">追加评论</div>
                <textarea class="input tk-comment" id="tkComment" rows="2"
                          placeholder="补充说明（会记入时间线，不可删改）"></textarea>
                <button class="btn btn-sm" id="tkCommentBtn" type="button">提交评论</button>
            </div>` : ''}

            <div class="note note-muted" style="margin-top:12px;">${esc(t.disclaimer || '')}</div>`;

        $('#tkClose').addEventListener('click', () => {
            view.currentId = null;
            $$('#tkList .tk-item').forEach(el => el.classList.remove('on'));
            $('#tkDetail').innerHTML = `<div class="state"><div class="state-title">从左侧选择一张工单</div>
                <div>详情包含：建单口径 → 代表证据 → 处置时间线 → 可执行操作。</div></div>`;
        });
        box.querySelectorAll('[data-act]').forEach(b => {
            b.addEventListener('click', () => {
                const act = (t.actions || []).find(a => a.action === b.dataset.act);
                openActionModal(t, act);
            });
        });
        const cb = $('#tkCommentBtn');
        if (cb) {
            cb.addEventListener('click', async () => {
                const note = ($('#tkComment').value || '').trim();
                if (!note) { toast('请先写点内容', 'err'); return; }
                cb.disabled = true;
                try {
                    const d = await api(`/api/tickets/${t.ticket_id}/comment`, null,
                        { method: 'POST', body: JSON.stringify({ note }) });
                    drawDetail(d);
                    toast('评论已记入时间线', 'ok');
                    loadList(); loadStats();
                } catch (e) { toast(e.message, 'err'); cb.disabled = false; }
            });
        }
    }

    function statusLabel(s) {
        return ({ draft: '待确认', triaged: '待分派', assigned: '已分派', processing: '处理中',
                  pending_review: '待复核', reopened: '已退回', closed: '已归档', rejected: '已驳回' })[s] || s;
    }

    function slaFirst(p) {
        const m = (view.meta && view.meta.sla && view.meta.sla.first_response) || {};
        return m[p];
    }

    async function openTicket(id) {
        const box = $('#tkDetail');
        setState(box, 'loading');
        try {
            drawDetail(await api(`/api/tickets/${id}`));
        } catch (e) {
            setState(box, 'error', e.message, () => openTicket(id));
        }
    }

    /* ---------------- 操作弹窗：必须说清"下一步谁收到" ---------------- */
    function openActionModal(t, act) {
        if (!act) return;
        const needsNote = (act.requires || []).indexOf('note') >= 0;
        const needsRes = (act.requires || []).indexOf('resolution') >= 0;
        const needsOwner = (act.requires || []).indexOf('owner') >= 0;
        const assignable = (view.meta && view.meta.assignable) || [];
        const title = {
            triage: '确认这张工单有效，起算时限',
            assign: '分派处置人',
            start: '开始处理',
            respond: '提交复核',
            approve: '复核通过并归档',
            reject: '驳回这张工单',
            return: '退回重新分派',
            reject_review: '复核不通过，退回处理人',
            reopen: '重新打开',
            restart: '重新进入处理中'
        }[act.action] || act.label;

        const mask = document.createElement('div');
        mask.className = 'modal-mask';
        mask.innerHTML = `<div class="modal">
            <div class="modal-title">${esc(act.label)} · ${esc(t.code)}</div>
            <div class="card-sub" style="margin-bottom:10px;">${esc(title)}</div>
            ${needsOwner ? `
                <label class="filter-label">处置人（只有处置员与管理员可被指派）</label>
                <select class="select" id="mOwner" style="width:100%;margin:6px 0 12px;">
                    ${assignable.map(u => `<option value="${esc(u.user_id)}">${esc(u.name)} · ${esc(u.role_label)}</option>`).join('')}
                </select>` : ''}
            ${act.action === 'triage' ? `
                <label class="filter-label">优先级（决定归档时限）</label>
                <select class="select" id="mPriority" style="width:100%;margin:6px 0 12px;">
                    ${['P0', 'P1', 'P2', 'P3'].map(p => `<option value="${p}" ${p === t.priority ? 'selected' : ''}>
                        ${p} · ${priorityLabel(p)}（${(view.meta.sla.archive || {})[p]} 个工作小时）</option>`).join('')}
                </select>` : ''}
            ${(needsNote || needsRes) ? `
                <label class="filter-label">${needsRes ? '处理记录（至少 10 字，写清做了什么）' : '说明（会记入时间线，不可删改）'}</label>
                <textarea class="input" id="mNote" rows="3" style="width:100%;margin:6px 0 12px;"
                    placeholder="${needsRes ? '例：已联系食堂承包商，本周加开两个窗口，下周复查排队情况' : '例：核实为设备临时故障，已报修'}"></textarea>` : ''}
            <div class="tk-modal-foot">
                <button class="btn btn-sm btn-ghost" data-cancel type="button">取消</button>
                <button class="btn btn-sm btn-primary" data-ok type="button">确认${esc(act.label)}</button>
            </div>
        </div>`;
        document.body.appendChild(mask);
        const close = () => mask.remove();
        mask.addEventListener('click', e => { if (e.target === mask) close(); });
        mask.querySelector('[data-cancel]').addEventListener('click', close);
        mask.querySelector('[data-ok]').addEventListener('click', async () => {
            const body = { action: act.action };
            if (needsNote || needsRes) body.note = (mask.querySelector('#mNote').value || '').trim();
            if (needsOwner) body.owner_id = mask.querySelector('#mOwner').value;
            if (act.action === 'triage') body.priority = mask.querySelector('#mPriority').value;
            const ok = mask.querySelector('[data-ok]');
            ok.disabled = true;
            try {
                const d = await api(`/api/tickets/${t.ticket_id}/transition`, null,
                    { method: 'POST', body: JSON.stringify(body) });
                close();
                drawDetail(d);
                toast(d.message || '已更新', 'ok');
                loadList(); loadStats(); loadSuggestions();
            } catch (e) {
                ok.disabled = false;
                toast(e.message, 'err');
                if (e.status === 409) { close(); openTicket(t.ticket_id); loadList(); }
            }
        });
    }

    /* ---------------- 新建工单 ---------------- */
    function openCreateModal() {
        const dims = (view.meta && view.meta.dimensions) || [];
        const mask = document.createElement('div');
        mask.className = 'modal-mask';
        mask.innerHTML = `<div class="modal">
            <div class="modal-title">新建工单</div>
            <div class="card-sub">会带上当前筛选口径与最多 5 条代表证据；建单后进入「待确认」，由人决定是否分派。</div>
            <label class="filter-label" style="margin-top:12px;">标题</label>
            <input class="input" id="cTitle" style="width:100%;margin:6px 0 10px;" maxlength="120"
                   placeholder="例：食堂餐饮本周负面集中，窗口排队时间过长">
            <div class="tk-two">
                <div>
                    <label class="filter-label">优先级</label>
                    <select class="select" id="cPriority" style="width:100%;margin:6px 0 10px;">
                        ${['P1', 'P2', 'P3', 'P0'].map(p => `<option value="${p}" ${p === 'P1' ? 'selected' : ''}>${p} · ${priorityLabel(p)}</option>`).join('')}
                    </select>
                </div>
                <div>
                    <label class="filter-label">维度</label>
                    <select class="select" id="cDim" style="width:100%;margin:6px 0 10px;">
                        <option value="">跟随口径</option>
                        ${dims.map(d => `<option value="${esc(d)}">${esc(d)}</option>`).join('')}
                    </select>
                </div>
            </div>
            <label class="filter-label">摘要</label>
            <textarea class="input" id="cSummary" rows="3" style="width:100%;margin:6px 0 10px;"
                      placeholder="写清现象与影响范围；不要写对具体个人的判断"></textarea>
            <div class="note note-muted" id="cScope" style="margin-bottom:12px;"></div>
            <div class="tk-modal-foot">
                <button class="btn btn-sm btn-ghost" data-cancel type="button">取消</button>
                <button class="btn btn-sm btn-primary" data-ok type="button">建单</button>
            </div>
        </div>`;
        document.body.appendChild(mask);
        mask.querySelector('#cScope').textContent =
            '当前口径：数据集 ' + (state.dataset_id || '默认') +
            (state.filters.dimension.length ? ' · 维度 ' + state.filters.dimension.join('、') : '') +
            (state.filters.sentiment.length ? ' · 情感 ' + state.filters.sentiment.join('、') : '') +
            (state.filters.date_from || state.filters.date_to
                ? ` · 时间 ${state.filters.date_from || '起'}~${state.filters.date_to || '今'}` : '');
        const close = () => mask.remove();
        mask.addEventListener('click', e => { if (e.target === mask) close(); });
        mask.querySelector('[data-cancel]').addEventListener('click', close);
        mask.querySelector('[data-ok]').addEventListener('click', async () => {
            const title = (mask.querySelector('#cTitle').value || '').trim();
            if (!title) { toast('请填写标题', 'err'); return; }
            const body = {
                title,
                summary: (mask.querySelector('#cSummary').value || '').trim(),
                priority: mask.querySelector('#cPriority').value,
                dimension: mask.querySelector('#cDim').value,
                filters: Object.assign({ dataset_id: state.dataset_id }, {
                    platform: state.filters.platform,
                    dimension: state.filters.dimension,
                    sentiment: state.filters.sentiment,
                    date_from: state.filters.date_from,
                    date_to: state.filters.date_to,
                    keyword: state.filters.keyword
                })
            };
            const ok = mask.querySelector('[data-ok]');
            ok.disabled = true;
            try {
                const t = await api('/api/tickets', null,
                    { method: 'POST', body: JSON.stringify(body) });
                close();
                toast(`已建单 ${t.code}，下一步：确认有效后分派`, 'ok');
                await refreshAll();
                openTicket(t.ticket_id);
            } catch (e) {
                ok.disabled = false;
                toast(e.message, 'err');
            }
        });
    }

    /* ---------------- 数据加载 ---------------- */
    async function loadMeta() {
        view.meta = await api('/api/tickets/meta');
        if (view.meta.can_create) $('#tkNew').style.display = '';
        if (view.meta.sla && view.meta.sla.holidays && view.meta.sla.holidays.length) {
            $('#tkSlaNote').textContent += ' 已配置节假日：' + view.meta.sla.holidays.join('、') + '。';
        }
    }

    async function loadStats() {
        try {
            view.stats = await api('/api/tickets/stats');
            renderKpi(view.stats);
        } catch (e) { /* 指标失败不阻塞列表 */ }
    }

    async function loadSuggestions() {
        try {
            const d = await api('/api/tickets/suggestions', { dataset_id: state.dataset_id });
            renderSuggestions(d);
        } catch (e) {
            const card = $('#suggCard');
            if (card) { card.style.display = ''; $('#suggList').innerHTML = `<div class="note">${esc(e.message)}</div>`; }
        }
    }

    async function loadList() {
        const params = {
            limit: PAGE, offset: view.offset,
            priority: view.priority || undefined,
            q: view.q || undefined
        };
        if (view.group === 'open' || view.group === 'overdue') params.status_group = view.group;
        else if (view.group === 'mine') params.mine = 1;
        const box = $('#tkList');
        if (!view.list) setState(box, 'loading');
        try {
            renderList(await api('/api/tickets', params));
        } catch (e) {
            setState(box, 'error', e.message, loadList);
        }
    }

    async function refreshAll() {
        await Promise.all([loadStats(), loadList()]);
    }

    /* ---------------- 事件 ---------------- */
    document.addEventListener('cp:ready', async () => {
        try {
            await loadMeta();
        } catch (e) {
            setState($('#tkList'), 'error', e.message, () => location.reload());
            return;
        }
        refreshAll();
        loadSuggestions();

        const q = new URLSearchParams(location.search);
        if (q.get('ticket')) openTicket(q.get('ticket'));
        if (q.get('create') === '1' && canCreate()) openCreateModal();
    });

    document.addEventListener('cp:dataset', () => {
        view.offset = 0;
        refreshAll();
        loadSuggestions();
    });
    document.addEventListener('cp:filter', () => {
        view.offset = 0;
        loadSuggestions();
    });

    $('#tkStatusTabs').addEventListener('click', e => {
        const b = e.target.closest('[data-group]');
        if (!b) return;
        view.group = b.dataset.group;
        view.offset = 0;
        $$('#tkStatusTabs .chip').forEach(x => x.classList.toggle('on', x === b));
        loadList();
    });
    $('#tkPriority').addEventListener('change', e => {
        view.priority = e.target.value;
        view.offset = 0;
        loadList();
    });
    let tmr = null;
    $('#tkSearch').addEventListener('input', e => {
        clearTimeout(tmr);
        tmr = setTimeout(() => {
            view.q = e.target.value.trim();
            view.offset = 0;
            loadList();
        }, 300);
    });
    $('#tkNew').addEventListener('click', openCreateModal);
})();
