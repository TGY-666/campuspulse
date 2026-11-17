/* PAGE-06 处置工单 —— 状态机在前端只做"展示可用动作"，
   真正的合法性由后端判定（409/403）。前端多算一遍只会造出两套规则。 */
(function () {
    'use strict';
    const { $, $$, esc, num, api, toast, state, setState } = CP;

    const view = {
        group: 'open', priority: '', q: '', offset: 0,
        currentId: null, meta: null, stats: null, list: null,
        // 地址栏带 ?ticket=xxx 进来时不要抢着选中第一条：深链要打开的那一张才是意图。
        deepLink: false
    };
    const PAGE = 20;

    /* ---------------- 038 批量与导出（前端，不新增接口） ---------------- */
    let currentTickets = [];      // 当前列表数据，CSV 只从这里取
    const bulkSel = new Set();    // 选中的 ticket_id

    function csvCell(v) {
        let s = String(v == null ? '' : v);
        // 107 公式注入：以 = + - @ 或制表/回车开头的单元格，Excel/Sheets 会当公式执行。
        // 前置一个单引号让电子表格按文本处理（单引号本身不显示）。中文 BOM 在 downloadCsv 里保留。
        if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
        return '"' + s.replace(/"/g, '""') + '"';
    }
    function downloadCsv(filename, headers, rows) {
        const lines = [headers.map(csvCell).join(',')]
            .concat(rows.map(r => r.map(csvCell).join(',')));
        // BOM 让 Excel 按 UTF-8 认中文；createObjectURL 用完立即 revoke
        const blob = new Blob(['\uFEFF' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 0);
    }
    function syncBulkToCurrent() {
        const ids = new Set(currentTickets.map(t => t.ticket_id));
        Array.from(bulkSel).forEach(id => { if (!ids.has(id)) bulkSel.delete(id); });
        $$('#tkList .tk-bulk').forEach(cb => { cb.checked = bulkSel.has(cb.dataset.bulk); });
    }
    function clearBulk() {
        bulkSel.clear();
        $$('#tkList .tk-bulk').forEach(cb => { cb.checked = false; });
        renderBulkBar();
    }
    function renderBulkBar() {
        const bar = $('#bulkBar');
        if (!bar) return;
        const n = bulkSel.size;
        bar.hidden = n === 0;
        if (!n) { bar.innerHTML = ''; return; }
        // 写操作按现有权限探测隐藏；导出是纯前端只读、不写库，仍保留
        const canBulkAssign = canCreate() || canManage();
        bar.innerHTML = `<span class="bulk-count">已选 <b>${n}</b> 张工单</span>
            <div class="bulk-acts">
                ${canBulkAssign ? '<button class="btn btn-sm" data-bulk-act="assign" type="button">批量分派</button>' : ''}
                <button class="btn btn-sm" data-bulk-act="csv" type="button">导出 CSV</button>
                <button class="btn btn-sm btn-ghost" data-bulk-act="clear" type="button">取消选择</button>
            </div>`;
        $$('#bulkBar [data-bulk-act]').forEach(b => b.addEventListener('click', () => {
            const act = b.dataset.bulkAct;
            if (act === 'assign') openBulkAssign();
            else if (act === 'csv') exportTicketsCsv();
            else clearBulk();
        }));
    }
    function exportTicketsCsv() {
        const rows = currentTickets.filter(t => bulkSel.has(t.ticket_id)).map(t => [
            t.code, t.title, t.status_label,
            `${t.priority} · ${priorityLabel(t.priority)}`,
            t.dimension || '', t.owner_name || '未分派', (t.sla || {}).text || ''
        ]);
        if (!rows.length) { toast('请先选择要导出的工单', 'err'); return; }
        const d = new Date();
        const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        downloadCsv(`处置工单_${day}.csv`,
            ['单号', '标题', '状态', '优先级', '主题', '负责人', '时限'], rows);
        toast(`已导出 ${rows.length} 张工单`, 'ok');
    }
    /* 批量分派复用单条分派的 transition({action:'assign', owner_id}) 与统一确认框，
       一次改多张单前先问清楚；合法性交给后端 403/409 判定，前端不自己编规则。 */
    function openBulkAssign() {
        const ids = Array.from(bulkSel);
        if (!ids.length) return;
        const assignable = (view.meta && view.meta.assignable) || [];
        if (!assignable.length) { toast('当前没有可分派的处置人，请联系管理员', 'err'); return; }
        const mask = document.createElement('div');
        mask.className = 'modal-mask';
        mask.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="tkBulkTitle">
            <h2 class="modal-title" id="tkBulkTitle">批量分派 ${ids.length} 张工单</h2>
            <div class="card-sub">每张单都会单独走一次与单条分派相同的流转，写进各自的时间线。</div>
            <label class="filter-label" for="bulkOwner">处置人</label>
            <select class="select" id="bulkOwner" style="width:100%;margin:6px 0 12px;">
                ${assignable.map(u => `<option value="${esc(u.user_id)}">${esc(u.name)} · ${esc(u.role_label)}</option>`).join('')}
            </select>
            <div class="tk-modal-foot">
                <button class="btn btn-sm btn-ghost" data-cancel type="button">取消</button>
                <button class="btn btn-sm btn-primary" data-ok type="button">确认分派</button>
            </div>
        </div>`;
        document.body.appendChild(mask);
        const close = () => { CP.closeModal(mask); mask.remove(); };
        CP.openModal(mask, { onEsc: close });
        mask.addEventListener('click', e => { if (e.target === mask) close(); });
        /** @type {any} */ (mask.querySelector('[data-cancel]')).addEventListener('click', close);
        /** @type {any} */ (mask.querySelector('[data-ok]')).addEventListener('click', async () => {
            const owner = /** @type {any} */ (mask.querySelector('#bulkOwner')).value;
            const okBtn = /** @type {any} */ (mask.querySelector('[data-ok]'));
            const ok = await CP.confirmModal({
                title: `把这 ${ids.length} 张工单分派给同一处置人？`,
                sub: '每张单单独执行一次分派流转，结果写进各自的时间线。',
                note: '状态不允许分派的单会被后端拒绝并计入失败数，不会影响其它单。',
                okLabel: '开始分派'
            });
            if (!ok) return;
            okBtn.disabled = true;
            let done = 0, failed = 0;
            for (const id of ids) {
                try {
                    await api(`/api/tickets/${id}/transition`, null, {
                        method: 'POST', body: JSON.stringify({ action: 'assign', owner_id: owner })
                    });
                    done += 1;
                } catch (e) { failed += 1; }
            }
            close();
            toast(`已分派 ${done} 张${failed ? ` · ${failed} 张失败（状态不允许或无权限）` : ''}`,
                  failed ? 'err' : 'ok');
            clearBulk();
            refreshAll();
        });
    }

    /* 043 规则编号只用于与后端建单接口通信，不面向用户读：tickets.py 的 RULES
       里那句中文名才是人话。skipped 只回 rule_id，这里按同一份定义兜展示名。 */
    const RULE_LABELS = {
        R1: '负面率突增', R2: '单主题负面集中', R3: '归因词突增', R4: '高互动负面'
    };
    function ruleLabel(id, name) { return name || RULE_LABELS[id] || '规则'; }

    const PRIORITY_CLS = { P0: 'p0', P1: 'p1', P2: 'p2', P3: 'p3' };
    const STATUS_TONE = {
        draft: 'warn', triaged: 'info', assigned: 'info', processing: 'run',
        pending_review: 'warn', reopened: 'neg', closed: 'ok', rejected: ''
    };

    /* 列表右侧那一格只有 86px 宽。第 19 轮起后端已经只给一个单位
       （"1.9 个工作日" / "6.4 工作时"），不再出现两个量粘在一起，
       所以不再按单位切行（旧正则会把 "1.9" 切成 "1." 丢掉小数）。 */
    function slaLines(amount) {
        const s = String(amount || '').trim();
        return s ? [s] : [];
    }

    /* ---------------- KPI ---------------- */
    function renderKpi(st) {
        const bx = $('#tkKpi');
        if (!bx) return;
        const by = {};
        (st.by_status || []).forEach(s => { by[s.key] = s.count; });
        const processing = (by.assigned || 0) + (by.processing || 0);
        const ot = st.on_time || {};
        const sc = scope();
        /* 第四、第五张卡以前共用一格：「平均处置时长 6.3 工作小时」的大数字下面
           挂着「按时归档 2/2 = 100%」的脚注。一段时长和一个比率塞进同一个卡位，
           读者会把它读成一个指标的两种说法（"6.3 小时 = 100% 按时"？）。
           现在拆成两张：时长归时长，按时率归按时率，分母与样本量各写各的。 */
        const otp = CP.onTimeParts(ot);
        const cards = [
            { label: sc ? '本部门待办' : '待分派',
              value: num(sc ? (by.assigned || 0) + (by.processing || 0) + (by.pending_review || 0)
                            : st.pending_assign), unit: '单',
              foot: sc ? `待复核 ${by.pending_review || 0} 单 · 已归档 ${by.closed || 0} 单`
                       : `待确认 ${by.draft || 0} 单 · 待复核 ${by.pending_review || 0} 单`,
              tone: 'warn' },
            { label: '处理中', value: num(processing), unit: '单',
              foot: `已分派 ${by.assigned || 0} · 处理中 ${by.processing || 0}`, tone: '' },
            { label: '已超期', value: num(st.overdue), unit: '单',
              // 「已超期」在这里数的是**张数**，而研判页的同名筛选数的是**主题数**。
              // 单位写在脚注里，两边并排时不会被当成数据不一致。
              foot: st.overdue ? '按工单张数 · 工作时间口径判定，不计周末与午休' : '当前没有超期工单', tone: st.overdue ? 'neg' : 'ok' },
            { label: '平均处置时长', value: st.avg_handle_hours == null ? '—' : st.avg_handle_hours,
              // 这个数是 work_minutes_between(创建, 归档) 的均值，单位同样是工作小时。
              // 写成裸「小时」会和 SLA 的 4/7 工作小时混成两种口径。
              unit: st.avg_handle_hours == null ? '' : '工作小时',
              foot: ot.denominator ? '已归档工单从建单到归档的平均用时' : '还没有归档工单，暂时算不出',
              tone: '' },
            // 样本不足 10 单时不给百分比：2/2 写成 100%，会和旁边的「已超期」并排放成自夸。
            // 口径与研判页「工单闭环」那一格同源（CP.onTimeParts），两页说法一致。
            { label: '按时归档', value: otp.value, unit: '',
              foot: otp.note || '尚无归档工单，按时率暂无分母',
              tone: (ot.denominator || 0) >= 10
                  ? ((ot.rate != null && ot.rate >= 90) ? 'ok' : 'warn') : '' }
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
        card.classList.remove('is-hidden');
        const list = $('#suggList');
        const items = d.suggestions || [];
        $('#suggBadge').textContent = items.length ? `命中 ${items.length} 条` : '未命中';
        $('#suggSub').textContent = d.anchor
            ? `评估锚点 ${d.anchor}（数据最后一天）`
            : '当前数据集没有可用日期';
        const fold = card.querySelector('.sugg-fold');
        if (!items.length) {
            // 没命中时这卡也压成一行：工单列表才是这一页要做的事
            card.classList.add('is-quiet');
            // 第 19 轮：未命中时**桌面也收起**。原来只在 390 收，
            // 桌面留着 215px 的“本次未命中 + 为什么不报警”说明书，把第一张单推到了 y≈550。
            if (fold) fold.open = false;
            // 收起了就得在摘要里把结论说完：否则一屏只看到一行标题，
            // 读者不知道它是“没命中”还是“还没评估”。
            $('#suggSub').textContent = `本次未命中${d.anchor ? ` · 评估锚点 ${d.anchor}` : ''}`
                + ' · 展开看判定阈值（不会为了显得智能而报警）';
            list.innerHTML = `<div class="risk-quiet">
                <span class="risk-quiet-text">当前筛选范围内没有命中规则。系统不会为了"显得智能"而报警，
                样本不足时宁可说样本不足——一次假报警，之后所有提示都会被当成噪音。</span></div>`;
        } else {
            card.classList.remove('is-quiet');
            // 有命中就展开：那是这一页的判断依据，不该藏。窄屏仍保留收起的机会（用户自己开关）。
            if (fold && !window.matchMedia('(max-width: 720px)').matches) fold.open = true;
            list.innerHTML = items.map((s, i) => `
                <div class="tk-sugg" data-i="${i}">
                    <div class="tk-sugg-head">
                        <span class="badge badge-brand">${esc(ruleLabel(s.rule_id, s.rule_name))}</span>
                        ${priorityBadge(s.priority)}
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
            note.classList.remove('is-hidden');
            // .note 是 flex 容器：全文包一个 <span>，每条再包 .sugg-rule 用「；」分隔，
            // 390 窄屏才不会把裸文本逐字竖排（CSS 组给 .sugg-rule{white-space:normal}）。
            note.innerHTML = '<span><b>未评估的规则：</b>' + skipped.map(s =>
                `<span class="sugg-rule">${esc(ruleLabel(s.rule_id))} ${esc(s.reason)}</span>`).join('；')
                + '。不评估也是一种结论。</span>';
        } else {
            note.classList.add('is-hidden');
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

    /* 009 优先级统一走语义徽章：列表、详情、建议、只读展示同一个出口，
       不再出现裸露的「高 / 中」纯文字。四档配色由样式组的 .tk-prib.p0~p3 提供。 */
    function priorityBadge(p, label) {
        const cls = PRIORITY_CLS[p] || 'p2';
        return `<span class="tk-prib ${cls}">${esc(p || '—')} · ${esc(label || priorityLabel(p))}</span>`;
    }

    function canCreate() { return !!(view.meta && view.meta.can_create); }
    function canManage() { return !!(view.meta && view.meta.can_manage); }
    /* 部门账号：后端已把列表、指标、角标全部限定在本部门，
       前端只负责把话说清楚——否则人会以为系统"只统计到这几单"，而不是"只看本部门"。 */
    function scope() { return (view.meta && view.meta.scope) || null; }

    /* ---------------- 列表 ---------------- */
    function renderList(d) {
        view.list = d;
        currentTickets = d.tickets || [];
        $('#tkTotal').textContent = `${d.total} 单`;
        const box = $('#tkList');
        if (!d.tickets.length) {
            setState(box, 'empty', canCreate()
                ? '当前筛选下没有工单。可以先从上方规则建议建单，或点"新建工单"。'
                : '当前筛选下没有归口给本部门的工单。别部门的单不显示，也打不开。');
            $('#tkPager').innerHTML = '';
            currentTickets = [];
            syncBulkToCurrent();
            renderBulkBar();
            return;
        }
        box.innerHTML = d.tickets.map(t => {
            const sla = t.sla || {};
            const tone = sla.overdue ? 'neg' : (sla.remaining_minutes != null && sla.remaining_minutes <= 120 ? 'warn' : '');
            // 108 整行不再冒充 role=button：那样会把行内的 checkbox / 链接变成
            // 嵌套交互控件，读屏与键盘都会乱。行改为 role=group，打开详情交给
            // 行内独立的「打开详情」链接（原生可聚焦，href 就是 ?ticket= 深链），
            // checkbox 单独给可访问名与 44px 触摸目标。点整行空白处仍是鼠标便利。
            return `
            <div class="tk-item ${view.currentId === t.ticket_id ? 'on' : ''}" data-id="${esc(t.ticket_id)}"
                 role="group" aria-current="${view.currentId === t.ticket_id ? 'true' : 'false'}"
                 aria-label="${esc(t.title)}，${esc(t.status_label)}">
                <input type="checkbox" class="tk-bulk" data-bulk="${esc(t.ticket_id)}"
                       aria-label="选择工单 ${esc(t.code)}" title="选中后可批量分派 / 导出">
                <i class="tk-pri ${PRIORITY_CLS[t.priority] || 'p2'}" title="${esc(t.priority)}"></i>
                <div class="tk-item-main">
                    <div class="tk-item-title">${esc(t.title)}</div>
                    <div class="tk-item-meta">
                        <span class="tk-code">${esc(t.code)}</span>
                        ${priorityBadge(t.priority)}
                        <span class="badge badge-${STATUS_TONE[t.status] || ''}">${esc(t.status_label)}</span>
                        ${t.dimension ? `<span>${esc(t.dimension)}</span>` : '<span>全部主题</span>'}
                        <span class="tk-owner">${t.owner_name ? '负责人 ' + esc(t.owner_name) : '未分派'}</span>
                        <a class="tk-open" href="?ticket=${esc(t.ticket_id)}" data-open
                           style="margin-left:6px;" aria-label="打开工单 ${esc(t.code)}：${esc(t.title)} 的详情">打开详情 →</a>
                    </div>
                </div>
                <div class="tk-item-sla ${tone}" title="${esc(sla.text || '')}${
                    sla.due ? ' · 应于 ' + esc(sla.due) : ''}">
                    <b>${esc(sla.label || '')}</b><span>${
                        slaLines(sla.amount).map(l => `<i>${esc(l)}</i>`).join('')}</span>${
                    sla.due ? `<i class="tk-due">归档 ${esc(String(sla.due).slice(5, 10))} 截止</i>` : ''}
                </div>
            </div>`;
        }).join('');
        box.querySelectorAll('[data-id]').forEach(el => {
            const cb = el.querySelector('.tk-bulk');
            if (cb) {
                cb.addEventListener('click', e => e.stopPropagation());
                cb.addEventListener('keydown', e => e.stopPropagation());
                cb.addEventListener('change', () => {
                    const id = cb.dataset.bulk;
                    if (cb.checked) bulkSel.add(id); else bulkSel.delete(id);
                    renderBulkBar();
                });
            }
            // 独立「打开详情」链接：键盘 / 读屏走这里，Enter 天然触发 click。
            // href 是 ?ticket= 深链，这里 preventDefault 走 SPA 不刷新，
            // 复制链接或新标签打开仍落在同一张单。
            const openBtn = el.querySelector('[data-open]');
            if (openBtn) openBtn.addEventListener('click', e => {
                e.preventDefault();
                e.stopPropagation();
                openTicket(el.dataset.id);
            });
            // 整行只作鼠标便利：role=group 不是交互控件，点空白处也打开详情。
            el.addEventListener('click', e => {
                const t = /** @type {Element} */ (e.target);
                if (t.closest('.tk-bulk') || t.closest('[data-open]')) return;
                openTicket(el.dataset.id);
            });
        });
        renderPager(d);
        syncBulkToCurrent();
        renderBulkBar();

        // 主从布局里右侧整块空白是最贵的浪费：列表已经按"最该先看"排好序
        // （超期在前、优先级次之），默认不选中等于让用户先自己找一张。
        // 换筛选后旧选中单可能已不在列表里，这时也落到第一条。
        if (!view.deepLink && (!view.currentId
                || !d.tickets.some(t => t.ticket_id === view.currentId))) {
            openTicket(d.tickets[0].ticket_id, { reveal: false });
        }
        view.deepLink = false;
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
    /* 工单是处置记录，落在**建单时**那批数据上；顶栏的数据集随时会切。
       两者不一致时，「按此口径重算」那三个数字是在另一个批次里算出来的，
       与当前页面上任何数字都不可比。第 10 轮实测就有这一屏：详情写「重算 387 条 ·
       负面 278 条」，而当前数据集负面只有 117 条——不解释，读者只会认定有一个数错了。 */
    /* 跳数据集的单：不要用三行黄字去解释两个数字为什么打架。
       第 18 轮：当前数据集与单不一致时，下面那组数字**降级成快照**（灰底 + 「建单时快照」），
       并给一个真正能解决问题的动作：切到那张单的口径。 */
    function isCrossDataset(t) {
        return !!(t && t.dataset_id && state.dataset_id && t.dataset_id !== state.dataset_id);
    }

    function crossDatasetNote(t) {
        if (!isCrossDataset(t)) return '';
        return `<div class="note note-warn" style="margin:0 0 6px;">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/></svg>
            <span>这张单建在<b>另一个数据集</b>上，下面是<b>建单时的快照</b>，
                不能与当前页面的统计相直比较。</span></div>`;
    }

    function drawDetail(t) {
        view.currentId = t.ticket_id;
        // 跳数据集判定只算一次：下面的快照块与提示条要同一个结论
        const crossDs = isCrossDataset(t);
        $$('#tkList .tk-item').forEach(el => {
            const on = el.dataset.id === t.ticket_id;
            el.classList.toggle('on', on);
            // 选中态不能只靠底色：读屏要知道"当前正在看的是哪一条"
            el.setAttribute('aria-current', on ? 'true' : 'false');
        });
        const box = $('#tkDetail');
        const sla = t.sla || {};
        // 054 后端演示数据把同一工单的多条事件写进同一个 created_at（同一秒），
        // 全渲染成 "10-30 17:23" 会读不出先后。后端暂不改，这里对同一时间戳的
        // 事件按写入顺序补一个步序号，保留原始到分钟的时间，让时间线能读。
        const events = t.events || [];
        const stamps = events.map(e => (e.created_at || '').slice(5, 16));
        const stampCount = {};
        stamps.forEach(s => { stampCount[s] = (stampCount[s] || 0) + 1; });
        const stampSeen = {};
        const ev = events.map((e, i) => {
            const stamp = stamps[i];
            stampSeen[stamp] = (stampSeen[stamp] || 0) + 1;
            const step = stampCount[stamp] > 1 ? ` · 第 ${stampSeen[stamp]} 步` : '';
            return `
            <li class="tk-ev">
                <i class="tk-ev-dot"></i>
                <div>
                    <div class="tk-ev-top">
                        <b>${esc(e.type_label)}</b>
                        <span>${esc(e.actor_name || e.actor_id)}</span>
                        <em>${esc(stamp + step)}</em>
                    </div>
                    ${e.from_status && e.to_status && e.from_status !== e.to_status
                        ? `<div class="tk-ev-flow">${esc(statusLabel(e.from_status))} → ${esc(statusLabel(e.to_status))}</div>` : ''}
                    ${e.note ? `<div class="tk-ev-note">${esc(e.note)}</div>` : ''}
                </div>
            </li>`;
        }).join('');

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

        /* ---- 三把尺子：两条是本单的刻度，第三条只是参照 ----
         * 以前这里是一句话，三件事挤在同一段里：部门期望（48/24）、本单的首次响应、
         * 本单的归档。读者要自己拆句子才知道"还剩多少"，而人是来救火的。
         * 现在两条画成刻度（已用 / 时限），第三条画成虚线参照框——
         * 它不属于这张单，画成同一种刻度就等于又混了一次口径。
         * 数值全部来自后端（含工作日历），前端只算宽度比例。
         *
         * **计时已停 ≠ 还在跑**：响应/归档之后，这一格如果还显示「还剩 N 工作小时」，
         * 界面就在说假话——已经响应了却告诉人还有 4 小时可用，而那正是他判断
         * "要不要现在催"的依据。所以 done 时只报已经发生的事：用量、按时/超时、时刻。
         * 单位一律走 CP.workHours："48h" 会被读成 2 个自然日，48 工作小时是 9.6 天。 */
        const cl = t.clocks || {};
        const fmtH = v => (v == null ? '—' : (Math.round(v * 10) / 10).toFixed(1).replace(/\.0$/, ''));
        const delta = (used, budget) => (budget == null || used == null ? ''
            : used > budget ? `已超 ${CP.workHours(used - budget)}`
                            : `还剩 ${CP.workHours(budget - used)}`);

        function clockTile(name, src, used, budget, foot, done) {
            const over = budget != null && used != null && used > budget;
            const pct = budget ? Math.min(100, Math.round((used || 0) / budget * 100)) : 0;
            /* 主数字 = 此刻最该知道的那一个数。
             * 首次响应与归档时限都从"建单"起算，所以两张卡的大数字永远是同一个值
             * （70.6 / 70.6）——并排读起来像重复渲染，还会让人以为看错了。
             * 现在：已超时读"已超多少"（动手的理由），其余读"用了多少"，
             * 「已用 X / 时限 Y」降为一行小字，仍然可复算。 */
            const headNum = over ? fmtH(used - budget) : fmtH(used);
            const headSub = over ? (done ? '工作小时 · 超出' : '工作小时 · 已超')
                                 : (done ? '工作小时 · 用时' : '工作小时 · 已用');
            const lim = budget == null ? ''
                : (over ? `已用 ${fmtH(used)} / 时限 ${fmtH(budget)} 工作小时`
                        : `时限 ${fmtH(budget)} 工作小时`);
            return `<div class="tk-clock${over ? ' is-over' : ''}${done ? ' is-done' : ''}">
                <div class="tc-head"><b>${esc(name)}</b><em>${esc(src)}</em></div>
                <div class="tc-num"><span>${headNum}</span><i class="tc-sub">${headSub}</i></div>
                ${lim ? `<div class="tc-lim">${lim}</div>` : ''}
                <div class="tc-rail" aria-hidden="true"><span style="width:${pct}%"></span></div>
                <div class="tc-foot">${foot}</div>
            </div>`;
        }

        const dep = cl.department || null;
        const clockGrid = (cl.first && cl.archive) ? `<div class="tk-clocks">
            ${(() => {
                const done = !!cl.first.at;
                const d1 = delta(cl.first.used, cl.first.budget);
                const foot = done
                    ? `已响应 · 用时 ${CP.workHours(cl.first.used)}${
                        cl.first.used > cl.first.budget ? ' · 超时' : ' · 按时'} · ${esc(cl.first.at.slice(5, 16))}`
                    : `${d1 ? d1 + ' · ' : ''}尚未响应`;
                return clockTile('首次响应', `按优先级 · ${t.priority_label || t.priority}`,
                    cl.first.used, cl.first.budget, foot, done);
            })()}
            ${(() => {
                const done = !!cl.archive.at;
                const d2 = delta(cl.archive.used, cl.archive.budget);
                const foot = done
                    ? `已归档 · 用时 ${CP.workHours(cl.archive.used)}${
                        cl.archive.used > cl.archive.budget ? ' · 超时' : ' · 按时'} · ${esc(cl.archive.at.slice(5, 16))}`
                    : `${d2 ? d2 + ' · ' : ''}${sla.due
                        ? '应于 ' + esc(String(sla.due).slice(5, 16)) : '未起算'}`;
                return clockTile('归档时限', `按优先级 · ${t.priority_label || t.priority}`,
                    cl.archive.used, cl.archive.budget, foot, done);
            })()}
            <div class="tk-clock is-ref">
                <div class="tc-head"><b>部门期望响应</b><em>按归口部门 · 不计本单</em></div>
                <div class="tc-num"><span>${dep && dep.hours != null ? dep.hours : '—'}</span>${
                    dep && dep.hours != null ? '<i>工作小时</i>' : ''}</div>
                <div class="tc-rail is-ref" aria-hidden="true"></div>
                <div class="tc-foot">${dep && dep.hours != null
                    ? '研判页用的就是它，不是本单的计时器'
                    : '没挂归口部门，就没有部门时限'}</div>
            </div>
        </div>
        <div class="tk-sla-foot">只计工作时间（工作日 09:00–12:00、14:00–18:00，扣周末与节假日）；
            列表里的「已超期 N 个工作日」是<b>已经用掉</b>的时间，不是时限本身。</div>`
            : `<div class="tk-sla-box ${sla.overdue ? 'neg' : ''}">
                <div><span>${esc(sla.text || '')}</span>
                    <em>归档时限 ${esc(sla.due || '未起算')}</em></div>
                <div class="tk-sla-foot">只计工作时间（工作日 09:00–12:00、14:00–18:00）；
                    首次响应要求 ${esc(String((t.first_response_hours || slaFirst(t.priority)) || '—'))} 个工作小时
                    ${t.first_response_at ? `· 实际 ${esc(t.first_response_at.slice(5, 16))}` : '· 尚未响应'}</div>
            </div>`;

        /* 顺序：三把尺子 → 可执行操作 → 建单口径 → 代表证据 → 处置时间线。
           动作块以前排在 5 条证据 + 5 步时间线之后（390 实测 y≈1500），而这一页
           是来救火的人打开的：「提交复核」要滚 1500px 才出现，途中先读完五条评论。
           现在按"此刻要做什么"排，证据与时间线回到它们本来的位置——复查用。
           注意：这段解释必须写成 JS 块注释，模板字面量里的 `{# … #}` 会原样
           显示在页面上（第 11 轮踩过）。 */
        box.innerHTML = `
            <div class="card-head">
                <div>
                    <div class="card-title"><span class="tk-code">${esc(t.code)}</span> ${esc(t.title)}</div>
                    <div class="card-sub">
                        <span class="badge badge-${STATUS_TONE[t.status] || ''}">${esc(t.status_label)}</span>
                        ${priorityBadge(t.priority, t.priority_label)}
                        ${t.dimension ? `<span class="badge">${esc(t.dimension)}</span>` : ''}
                        ${t.department_name ? `<span class="badge">归口 ${esc(t.department_name)}</span>` : ''}
                        ${t.rule_id ? `<span class="badge badge-brand">规则 ${esc(ruleLabel(t.rule_id, t.rule_name))}</span>` : ''}
                    </div>
                </div>
                <div class="card-tools">
                    <button class="btn btn-sm btn-ghost" id="tkNext" type="button"
                            title="跳到列表里下一条未关闭工单，沿用 ?ticket= 深链">下一张 →</button>
                    <button class="btn btn-sm btn-ghost" id="tkBack" type="button">← 返回列表</button>
                    <button class="btn btn-sm btn-ghost" id="tkClose" type="button">关闭</button>
                    ${canManage() ? `<span class="tk-danger-sep" aria-hidden="true"></span>
                    <button class="btn btn-sm btn-ghost btn-danger" id="tkDelete" type="button"
                        title="删除后不可恢复，需填写原因并记入审计日志">删除工单</button>` : ''}
                </div>
            </div>

            ${clockGrid}

            <div class="tk-block tk-block-acts">
                <div class="tk-block-title">可执行操作 <em>按当前状态与身份可用</em></div>
                <div class="tk-acts">${acts}</div>
            </div>

            <div class="tk-block">
                <div class="tk-block-title">建单口径 <em>存的是口径，不是当时的数字</em></div>
                ${crossDatasetNote(t)}
                <div class="note note-muted" style="margin:0 0 6px;">${esc(t.filters_text || '—')}</div>
                ${t.filter_preview ? `<div class="tk-preview${crossDs ? ' is-snap' : ''}">${
                    crossDs ? '<b>建单时快照</b> · ' : '按此口径重算：'
                    }${num(t.filter_preview.total)} 条 · 负面 ${num(t.filter_preview.negative)} 条 ·
                    负面占比 ${t.filter_preview.negative_rate == null ? '—' : t.filter_preview.negative_rate + '%'}${
                    crossDs ? `<button class="btn btn-sm tk-snap-switch" type="button"
                        data-ds="${esc(t.dataset_id)}">切到该单口径</button>` : ''}</div>` : ''}
            </div>

            <div class="tk-block">
                <div class="tk-block-title">代表证据 <em>只展示脱敏文本，不含昵称与账号</em></div>
                <div class="tk-evidence">${evid}</div>
            </div>

            <div class="tk-block">
                <div class="tk-block-title">处置时间线 <em>处置记录只追加、不修改或删除（留痕可审计）</em></div>
                <ul class="tk-timeline">${ev || '<li class="note note-muted">暂无事件</li>'}</ul>
            </div>

            ${['closed', 'rejected'].indexOf(t.status) < 0 ? `
            <div class="tk-block">
                <div class="tk-block-title">追加评论</div>
                <textarea class="input tk-comment" id="tkComment" rows="2" aria-label="追加评论"
                          placeholder="补充说明（会记入时间线，不可删改）"></textarea>
                <button class="btn btn-sm" id="tkCommentBtn" type="button">提交评论</button>
            </div>` : ''}

            <div class="note note-muted" style="margin-top:12px;">${esc(t.disclaimer || '')}</div>`;

        const backToList = () => {
            view.currentId = null;
            $$('#tkList .tk-item').forEach(el => el.classList.remove('on'));
            $('#tkDetail').innerHTML = `<div class="state"><div class="state-title">从左侧选择一张工单</div>
                <div>详情按“要做什么”排：三把尺子 → 可执行操作 → 建单口径 → 代表证据 → 处置时间线。</div></div>`;
        };
        $('#tkClose').addEventListener('click', backToList);
        // 038① 处置完直接跳到列表里下一条未关闭工单，沿用 ?ticket= 深链（刷新仍停在这张单）。
        const nextBtn = $('#tkNext');
        if (nextBtn) nextBtn.addEventListener('click', () => {
            const closed = ['closed', 'rejected'];
            const cur = currentTickets.findIndex(x => x.ticket_id === t.ticket_id);
            for (let k = cur + 1; k < currentTickets.length; k++) {
                const nx = currentTickets[k];
                if (closed.indexOf(nx.status) >= 0) continue;
                const q = new URLSearchParams(location.search);
                q.set('ticket', nx.ticket_id);
                history.replaceState(null, '', location.pathname + '?' + q.toString());
                openTicket(nx.ticket_id);
                return;
            }
            toast('后面没有未关闭的工单了', 'ok');
        });
        const back = $('#tkBack');
        if (back) back.addEventListener('click', () => {
            backToList();
            // 034 390 下详情在列表下方，关掉后把人送回列表，别让他盯着空白
            const list = $('#tkList');
            if (list) list.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
        const del = $('#tkDelete');
        if (del) del.addEventListener('click', () => openDeleteModal(t));
        /* 跳数据集的快照：「切到该单口径」就把顶栏换成它建单时的那批数据。
           不给这个动作的话，读者只能自己去顶栏下拉里猜哪一个是"它建单时的那批"。 */
        const sw = $('.tk-snap-switch');
        if (sw) sw.addEventListener('click', async ev => {
            ev.stopPropagation();
            const id = sw.getAttribute('data-ds');
            if (!id) return;
            sw.disabled = true;
            try {
                await CP.switchDataset(id);
                toast('已切到这张单建单时的数据集', 'ok');
            } catch (e) {
                sw.disabled = false;
                toast('切换失败：' + e.message, 'err');
            }
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

    /* ≤720 是单列布局：详情卡排在列表之后。第 10 轮实测 390 下点「打开详情 →」
       之后 scrollY 仍是 1136、#tkDetail.top = 1305（视口 844）——屏幕上什么都没变，
       唯一的反馈是被点那行微微变蓝；而详情里还放着「← 返回列表」，暗示这是一次
       视图切换。所以窄屏在渲染完之后把视口带过去，并把焦点移进详情标题
       （tabindex=-1），键盘 / 读屏用户不会落在原地。桌面主从两栏同屏，
       跳走反而丢掉"我在看哪一条"的上下文，所以只在窄屏做。 */
    function revealDetailOnNarrow() {
        const box = $('#tkDetail');
        if (!box) return;
        if (!window.matchMedia('(max-width: 720px)').matches) return;
        const title = box.querySelector('.card-title');
        if (title) {
            title.setAttribute('tabindex', '-1');
            title.focus({ preventScroll: true });   // 先给焦点，再由滚动统一带过去
        }
        // 顶栏是 sticky 的（390 实测两行 + 口径说明近 200px），
        // 不留出这段高度，详情标题会正好藏在它底下，看起来还是"没反应"。
        const top = document.querySelector('.topbar');
        if (top) box.style.scrollMarginTop = (top.getBoundingClientRect().height + 10) + 'px';
        box.scrollIntoView({ block: 'start' });
    }

    async function openTicket(id, opts) {
        const box = $('#tkDetail');
        setState(box, 'loading');
        try {
            drawDetail(await api(`/api/tickets/${id}`));
            // 只有"人点开的"才把视口带过去。首次进页会自动选中第一条
            // （主从布局右侧不该空着），那不是一次用户动作——390 上跟着滚，
            // 页面一打开就落到详情、列表整个看不见，比"点了没反应"更糟。
            if (!opts || opts.reveal !== false) revealDetailOnNarrow();
        } catch (e) {
            setState(box, 'error', e.message, () => openTicket(id));
        }
    }

    /* ---------------- 删除工单 ----------------
     * 不可逆的操作要先说清"不可逆"，再要一个原因——只弹一个"确定删除吗"，
     * 人只会顺手点确定，三个月后没人说得清这张单为什么没了。
     * 审计那一半由后端负责（写 delete 事件后删主体），这里不假装能撤销。
     */
    function openDeleteModal(t) {
        const settled = ['closed', 'rejected'].indexOf(t.status) >= 0;
        const mask = document.createElement('div');
        mask.className = 'modal-mask';
        mask.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="tkDelTitle">
            <h2 class="modal-title" id="tkDelTitle">删除工单 ${esc(t.code)}</h2>
            <div class="card-sub">这张单会从列表与统计里消失，<b>不可恢复</b>。
                删除记录（单号、标题、原因、操作人）写进审计日志，之后仍可查。</div>
            <div class="note note-warn" style="margin:10px 0;">
                ${settled ? '该单已结案，删除后不再计入历史指标。'
                          : '该单还在流转中，删除后在建/在办/超期等指标会同步减少。'}
                评论数据本身不受影响。</div>
            <label class="filter-label" for="dReason">删除原因（至少 4 个字，会记入审计日志）</label>
            <textarea class="input" id="dReason" rows="2" maxlength="120"
                      style="width:100%;margin:6px 0 10px;resize:vertical;"
                      placeholder="例：演示时误建；同一主题重复建单；测试数据"></textarea>
            <div class="tk-modal-foot">
                <button class="btn btn-sm btn-ghost" data-cancel type="button">取消</button>
                <button class="btn btn-sm btn-danger-solid" data-ok type="button">确认删除</button>
            </div>
        </div>`;
        document.body.appendChild(mask);
        const ta = /** @type {HTMLTextAreaElement} */ (mask.querySelector('#dReason'));
        // 关掉时要连遮罩一起摘掉（这是临时节点），所以先让 closeModal 把焦点还给
        // 触发它的那颗按钮，再 remove——反过来焦点会掉到 body 上。
        const close = () => { CP.closeModal(mask); mask.remove(); };
        CP.openModal(mask, { onEsc: close });
        mask.addEventListener('click', e => { if (e.target === mask) close(); });
        /** @type {any} */ (mask.querySelector('[data-cancel]')).addEventListener('click', close);
        /** @type {any} */ (mask.querySelector('[data-ok]')).addEventListener('click', async () => {
            const reason = (ta.value || '').trim();
            if (reason.length < 4) { toast('请填写删除原因（至少 4 个字）', 'err'); ta.focus(); return; }
            const ok = /** @type {any} */ (mask.querySelector('[data-ok]'));
            ok.disabled = true;
            try {
                const r = await api(`/api/tickets/${t.ticket_id}`, null,
                    { method: 'DELETE', body: JSON.stringify({ reason }) });
                close();
                // 034 后端没有撤销接口：不写「撤销」，给一句诚实的收尾
                toast(`${t.code} 已删除 · 刷新后生效（不可撤销，审计日志已留痕）`, 'ok');
                view.currentId = null;
                $$('#tkList .tk-item').forEach(el => el.classList.remove('on'));
                $('#tkDetail').innerHTML = `<div class="state"><div class="state-title">${esc(t.code)} 已删除</div>
                    <div>审计日志里保留了这次删除的单号、标题与原因。</div></div>`;
                loadList(); loadStats(); loadSuggestions();
            } catch (e) {
                ok.disabled = false;
                toast(e.message, 'err');
            }
        });
        if (ta) ta.focus();
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
        mask.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="tkActTitle">
            <h2 class="modal-title" id="tkActTitle">${esc(act.label)} · ${esc(t.code)}</h2>
            <div class="card-sub" style="margin-bottom:10px;">${esc(title)}</div>
            ${needsOwner ? `
                <label class="filter-label" for="mOwner">处置人（只有处置员与管理员可被指派）</label>
                <select class="select" id="mOwner" style="width:100%;margin:6px 0 12px;">
                    ${assignable.map(u => `<option value="${esc(u.user_id)}">${esc(u.name)} · ${esc(u.role_label)}</option>`).join('')}
                </select>` : ''}
            ${act.action === 'triage' ? `
                <label class="filter-label" for="mPriority">优先级（决定归档时限）</label>
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
        const close = () => { CP.closeModal(mask); mask.remove(); };
        CP.openModal(mask, { onEsc: close });
        mask.addEventListener('click', e => { if (e.target === mask) close(); });
        /** @type {any} */ (mask.querySelector('[data-cancel]')).addEventListener('click', close);
        /** @type {any} */ (mask.querySelector('[data-ok]')).addEventListener('click', async () => {
            const body = { action: act.action };
            if (needsNote || needsRes) body.note = (/** @type {any} */ (mask.querySelector('#mNote')).value || '').trim();
            if (needsOwner) body.owner_id = /** @type {any} */ (mask.querySelector('#mOwner')).value;
            if (act.action === 'triage') body.priority = /** @type {any} */ (mask.querySelector('#mPriority')).value;
            const ok = /** @type {any} */ (mask.querySelector('[data-ok]'));
            ok.disabled = true;
            try {
                const d = await api(`/api/tickets/${t.ticket_id}/transition`, null,
                    { method: 'POST', body: JSON.stringify(body) });
                close();
                drawDetail(d);
                // 034 归档等流转同样没有撤销接口，成功只陈述结果，不承诺回滚
                toast(act.action === 'approve'
                    ? `${t.code} 已归档 · 列表与统计已更新（不可撤销）`
                    : (d.message || '已更新'), 'ok');
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
        mask.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="tkNewTitle">
            <h2 class="modal-title" id="tkNewTitle">新建工单</h2>
            <div class="card-sub">会带上当前筛选口径与最多 5 条代表证据；建单后进入「待确认」，由人决定是否分派。</div>
            <label class="filter-label" style="margin-top:12px;" for="cTitle">标题</label>
            <input class="input" id="cTitle" style="width:100%;margin:6px 0 10px;" maxlength="120"
                   placeholder="例：食堂餐饮本周负面集中，窗口排队时间过长">
            <div class="tk-two">
                <div>
                    <label class="filter-label" for="cPriority">优先级</label>
                    <select class="select" id="cPriority" style="width:100%;margin:6px 0 10px;">
                        ${['P1', 'P2', 'P3', 'P0'].map(p => `<option value="${p}" ${p === 'P1' ? 'selected' : ''}>${p} · ${priorityLabel(p)}</option>`).join('')}
                    </select>
                </div>
                <div>
                    <label class="filter-label" for="cDim">主题</label>
                    <select class="select" id="cDim" style="width:100%;margin:6px 0 10px;">
                        <option value="">跟随口径</option>
                        ${dims.map(d => `<option value="${esc(d)}">${esc(d)}</option>`).join('')}
                    </select>
                </div>
            </div>
            <label class="filter-label" for="cSummary">摘要</label>
            <textarea class="input" id="cSummary" rows="3" style="width:100%;margin:6px 0 10px;"
                      placeholder="写清现象与影响范围；不要写对具体个人的判断"></textarea>
            <div class="note note-muted" id="cScope" style="margin-bottom:12px;"></div>
            <div class="tk-modal-foot">
                <button class="btn btn-sm btn-ghost" data-cancel type="button">取消</button>
                <button class="btn btn-sm btn-primary" data-ok type="button">建单</button>
            </div>
        </div>`;
        document.body.appendChild(mask);
        /** @type {any} */ (mask.querySelector('#cScope')).textContent =
            '当前口径：数据集 ' + (state.dataset_id || '默认') +
            (state.filters.dimension.length ? ' · 主题 ' + state.filters.dimension.join('、') : '') +
            (state.filters.sentiment.length ? ' · 情感 ' + state.filters.sentiment.join('、') : '') +
            (state.filters.date_from || state.filters.date_to
                ? ` · 时间 ${state.filters.date_from || '起'}~${state.filters.date_to || '今'}` : '');
        const close = () => { CP.closeModal(mask); mask.remove(); };
        CP.openModal(mask, { onEsc: close });
        mask.addEventListener('click', e => { if (e.target === mask) close(); });
        /** @type {any} */ (mask.querySelector('[data-cancel]')).addEventListener('click', close);
        /** @type {any} */ (mask.querySelector('[data-ok]')).addEventListener('click', async () => {
            const title = (/** @type {any} */ (mask.querySelector('#cTitle')).value || '').trim();
            if (!title) { toast('请填写标题', 'err'); return; }
            const body = {
                title,
                summary: (/** @type {any} */ (mask.querySelector('#cSummary')).value || '').trim(),
                priority: /** @type {any} */ (mask.querySelector('#cPriority')).value,
                dimension: /** @type {any} */ (mask.querySelector('#cDim')).value,
                filters: Object.assign({ dataset_id: state.dataset_id }, {
                    platform: state.filters.platform,
                    dimension: state.filters.dimension,
                    sentiment: state.filters.sentiment,
                    date_from: state.filters.date_from,
                    date_to: state.filters.date_to,
                    keyword: state.filters.keyword
                })
            };
            const ok = /** @type {any} */ (mask.querySelector('[data-ok]'));
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
        if (view.meta.can_create) $('#tkNew').classList.remove('is-hidden');
        // 部门账号：把"范围"写在列表标题上。后端已经过滤了，但如果页面不说，
        // 人看到 3 张单会以为全校只有 3 张，而不是"这 3 张归我"。
        const sc = view.meta.scope;
        // 这一行对所有人常驻，不只是部门账号：工单表按建单落库、不按数据集分区，
        // 一张单可能来自上一批数据、另一张来自当前这批。顶栏只有一个"当前数据集"，
        // 页面不写这句话，用户会以为切数据集能把工单也筛一遍——然后在详情里看到
        // 「重算 387 条 · 负面 278 条」，而当前数据集负面只有 117 条。
        const sub = $('#tkListSub');
        if (sub) {
            const base = sc
                ? `仅显示归口「${sc.department_name || '本部门'}」的工单`
                : '按建单时间倒序';
            sub.textContent = base + ' · 工单是处置记录，跨数据集汇总，不受顶栏数据集切换影响';
        }
        if (sc) {
            const more = $('#tkGroupMore');
            if (more) {
                const mine = more.querySelector('option[value="mine"]');
                if (mine) { mine.hidden = true; mine.disabled = true; }   // 部门账号按归口分工，"我的"等于"本部门"
            }
        }
        // 三把尺子的具体数值全部来自后端配置，不在这里写死：
        // 部门期望响应（判断链上的 48 / 24）、工单按优先级的首次响应与归档时限、
        // 以及「已超期 N 个工作日」这个已用量，是三套不同口径。
        // 写死一份就会在改配置之后变成假话——而这张页最不能出的就是假口径。
        const sla = view.meta.sla || {};
        const first = sla.first_response || {};
        const arch = sla.archive || {};
        const pkeys = Object.keys(first);
        const slaNote = $('#tkSlaNote');
        if (slaNote && pkeys.length) {
            slaNote.textContent += ' 首次响应 / 归档时限按优先级：' + pkeys.map(k =>
                `${priorityLabel(k)} ${first[k]} / ${arch[k] == null ? '—' : arch[k]} 工作小时`
            ).join('、') + '。';
        }
        if (sla.holidays && sla.holidays.length) {
            if (slaNote) slaNote.textContent += ' 已配置节假日：' + sla.holidays.join('、') + '。';
        }
    }

    async function loadStats() {
        try {
            view.stats = await api('/api/tickets/stats');
            renderKpi(view.stats);
        } catch (e) { /* 指标失败不阻塞列表 */ }
    }

    async function loadSuggestions() {
        // 建议的唯一用途是"照着建单"。没有建单权限的角色（处置员）不必请求：
        // 接口 403 会在控制台留一条 error，卡片又只能显示"没有该操作权限"——
        // 对只看单、只办单的人来说是纯噪音，还会被前端验证脚本判成失败。
        if (!canCreate()) {
            const card = $('#suggCard');
            if (card) card.classList.add('is-hidden');
            return;
        }
        try {
            const d = await api('/api/tickets/suggestions', { dataset_id: state.dataset_id });
            renderSuggestions(d);
        } catch (e) {
            const card = $('#suggCard');
            if (card) { card.classList.remove('is-hidden'); $('#suggList').innerHTML = `<div class="note">${esc(e.message)}</div>`; }
        }
    }

    async function loadList() {
        const params = {
            limit: PAGE, offset: view.offset,
            priority: view.priority || undefined,
            q: view.q || undefined
        };
        // 三个状态组走 status_group；"我的"是范围筛选，可以和状态组叠加使用的心智
        // 但后端目前是互斥的，所以这里也保持互斥——取消选中比"两个高亮"诚实。
        if (['open', 'overdue', 'closed'].indexOf(view.group) >= 0) params.status_group = view.group;
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
    const GROUPS = ['open', 'overdue', 'closed', 'mine', 'all'];

    /* 016 状态 chip 只留 3 枚，「我的 / 全部」并入 #tkGroupMore 下拉。
       chip 的 data-group 值与 ?status_group= 的 URL 同步逻辑一字未改。 */
    function applyGroupUI(g) {
        $$('#tkStatusTabs .chip').forEach(x => {
            const on = x.dataset.group === g;
            x.classList.toggle('on', on);
            x.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        const more = $('#tkGroupMore');
        if (more) more.value = (g === 'mine' || g === 'all') ? g : '';
    }
    function setGroup(g) {
        view.group = g;
        view.offset = 0;
        applyGroupUI(g);
        syncGroupUrl();
        loadList();
    }

    /* 把当前分组写回地址栏：从研判页「工单闭环」的「在办 / 已超期 / 已完成」点进来时
       带的就是这个参数，切换标签后不同步的话，刷新会跳回旧分组，看起来像筛选没生效。 */
    function syncGroupUrl() {
        const q = new URLSearchParams(location.search);
        if (view.group === 'open') q.delete('status_group');
        else q.set('status_group', view.group);
        const s = q.toString();
        history.replaceState(null, '', location.pathname + (s ? '?' + s : ''));
    }

    document.addEventListener('cp:ready', async () => {
        // 390 的规则卡默认收起：手机上首屏必须先是工单，不是一张「本次未命中」的
        // 说明书（实测收起前第一张单在 y=1455，1.7 屏之后）。桌面保持展开——
        // 那是这一页的判断依据，不该藏。只按初始视口判一次，之后由用户自己开关。
        const fold = $('.sugg-fold');
        if (fold && window.matchMedia('(max-width: 720px)').matches) fold.open = false;

        const q = new URLSearchParams(location.search);
        // 分组必须在第一次 loadList 之前定下来，否则会先按"进行中"加载一次、
        // 再闪成目标分组——点"已完成"进来却先看到进行中的单，像点错了。
        const g = q.get('status_group');
        if (g && GROUPS.indexOf(g) >= 0) view.group = g;
        applyGroupUI(view.group);
        try {
            await loadMeta();
        } catch (e) {
            setState($('#tkList'), 'error', e.message, () => location.reload());
            return;
        }
        // 深链标记必须在第一次 loadList 之前挂上：refreshAll 里的 renderList
        // 会顺带自动选中第一条，先挂这里才不会被它抢先打开另一张单。
        view.deepLink = !!q.get('ticket');
        refreshAll();
        loadSuggestions();

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
        if (b) setGroup(b.dataset.group);
    });
    const moreSel = $('#tkGroupMore');
    if (moreSel) moreSel.addEventListener('change', e => {
        if (e.target.value) setGroup(e.target.value);
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
