/* PAGE-04 热点与证据中心 */
(function () {
    'use strict';
    const { $, $$, esc, num, pct, api, state, toast, setState } = CP;

    let PAGE_SIZE = 20;
    const PAGE_SIZES = [20, 50, 100];
    // 初始页码与排序从 URL 读：否则翻到第 5 页后一刷新就回第 1 页，
    // 也没法把"这一页的证据"发给别人复核
    const _qp = new URLSearchParams(location.search);
    let offset = Math.max(0, parseInt(_qp.get('offset') || '0', 10) || 0);
    let order = ['likes', 'time_desc', 'time_asc'].includes(_qp.get('order')) ? _qp.get('order') : 'likes';
    let total = 0;
    // 在 core.js 规范化 URL 之前先抓住 ref，否则会被 syncUrl 清掉
    const INIT_REF = new URLSearchParams(location.search).get('ref');

    /* ---------------- 021 展示层占位符清理 ----------------
     * 采集时正文里夹着 [@用户] / [呃R] 这类平台占位符，原样摆出来像乱码。
     * 只在渲染前替换成可读的中性标记：@提及 → 〔用户〕，以 R 结尾的方括号
     * 表情占位符（[呃R] / [微笑R] 这类）→ 〔表情〕。
     * 113：不再匹配不带 R 的纯中文方括号词，[重要] / [已解决] 是正常语气标注，
     * 不能被吞成〔表情〕。不动后端返回的原始 snippet，也不写回库。 */
    function cleanPlaceholder(s) {
        return String(s == null ? '' : s)
            .replace(/\[@[^\]]*\]/g, '〔用户〕')
            .replace(/\[[\u4e00-\u9fa5A-Za-z]{1,8}R\]/g, '〔表情〕');
    }

    /* ---------------- 038 常用检索（证据页本地保存，不进 core.js） ----------------
     * 只存「证据页当前这一批检索」：URL query 串 + 一个可读名称，落在 localStorage。
     * 这不是全站筛选视图——其它页的筛选由 core.js 管；这里恢复时整页跳到保存的
     * query，由 core.js 的 readUrl 复原筛选与分页。如实说明，不冒充全站视图。 */
    const SAVED_KEY = 'cp.evidence.savedSearches';
    function readSaved() {
        try {
            const v = JSON.parse(localStorage.getItem(SAVED_KEY) || '[]');
            return Array.isArray(v) ? v : [];
        } catch (e) { return []; }
    }
    function writeSaved(list) {
        try { localStorage.setItem(SAVED_KEY, JSON.stringify(list)); }
        catch (e) { toast('本地存储不可用，检索没保存', 'err'); }
    }
    function saveCurrentSearch(name) {
        const list = readSaved();
        const label = (name || '').trim() || ('检索 ' + new Date().toLocaleString('zh-CN', {
            month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }));
        list.unshift({ name: label, query: location.search || '' });
        writeSaved(list.slice(0, 12));   // 只留最近 12 条
        toast('已保存当前检索：' + label, 'ok');
        renderSavedViews();
    }
    function restoreSaved(query) { location.href = '/evidence' + (query || ''); }
    function renderSavedViews() {
        const sel = $('#evSavedViews');
        if (!sel) return;
        const list = readSaved();
        sel.innerHTML = '<option value="">恢复已保存的检索…</option>' + list.map((v, i) =>
            `<option value="${i}">${esc(v.name)}</option>`).join('');
        sel.value = '';
    }

    /* ---------------- 038 批量导出（前端，不打接口） ---------------- */
    let currentItems = [];       // 当前列表数据，导出只从这里取
    const bulkSel = new Set();   // 选中的引用 ID

    function csvCell(v) {
        let s = String(v == null ? '' : v);
        // 107 公式注入：以 = + - @ 或制表/回车开头的单元格，Excel/Sheets 会当公式执行。
        // 前置一个单引号让电子表格按文本处理（单引号本身不显示）。中文 BOM 在 downloadCsv 里保留。
        if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
        return '"' + s.replace(/"/g, '""') + '"';
    }
    // 107 正文摘要导出前再校验一次 PII：脱敏文本理论上已去掉可识别信息，
    // 但采集 / 截图 OCR 可能漏进手机号、邮箱、微信号、URL。命中就把这段摘要
    // 整段换成占位提示，不把联系方式带出系统（昵称 / IP / 主页本就不导出）。
    const PII_RE = /1[3-9]\d{9}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|https?:\/\/|www\.|(?:微信|微信号|wx|weixin|vx)\s*[:：]?\s*[A-Za-z0-9_-]{5,}/i;
    function safeSnippet(s) {
        return PII_RE.test(s) ? '〔含联系方式，已按脱敏口径省略〕' : s;
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
        const refs = new Set(currentItems.map(x => x.comment_ref));
        Array.from(bulkSel).forEach(r => { if (!refs.has(r)) bulkSel.delete(r); });
        $$('#resultList .ev-bulk').forEach(cb => { cb.checked = bulkSel.has(cb.dataset.bulk); });
    }
    function clearBulk() {
        bulkSel.clear();
        $$('#resultList .ev-bulk').forEach(cb => { cb.checked = false; });
        renderBulkBar();
        syncSelectPageBtn();
    }

    /* 全选 / 取消本页：只作用于**当前页已渲染的那一批**，不跨页累积——
       腾讯与部门都读得懂“本页”，而“全选 1,969 条”导出一个 1,969 行的 CSV
       对这个场景没用（导出要的是“这几条拿去核对”）。 */
    function selectPage() {
        const items = currentItems || [];
        const allOn = items.length && items.every(x => bulkSel.has(x.comment_ref));
        if (allOn) items.forEach(x => bulkSel.delete(x.comment_ref));
        else items.forEach(x => bulkSel.add(x.comment_ref));
        $$('#resultList .ev-bulk').forEach(cb => { cb.checked = bulkSel.has(cb.dataset.bulk); });
        renderBulkBar();
        syncSelectPageBtn();
    }
    function syncSelectPageBtn() {
        const btn = $('#evSelectPage');
        if (!btn) return;
        const items = currentItems || [];
        const allOn = items.length > 0 && items.every(x => bulkSel.has(x.comment_ref));
        btn.textContent = allOn ? '取消全选' : '全选本页';
        btn.setAttribute('aria-pressed', allOn ? 'true' : 'false');
    }
    function renderBulkBar() {
        const bar = $('#bulkBar');
        if (!bar) return;
        const n = bulkSel.size;
        bar.hidden = n === 0;
        if (!n) { bar.innerHTML = ''; return; }
        bar.innerHTML = `<span class="bulk-count">已选 <b>${n}</b> 条证据</span>
            <div class="bulk-acts">
                <button class="btn btn-sm" data-bulk-act="csv" type="button">导出 CSV</button>
                <button class="btn btn-sm btn-ghost" data-bulk-act="clear" type="button">取消选择</button>
            </div>`;
        $$('#bulkBar [data-bulk-act]').forEach(b => b.addEventListener('click', () => {
            if (b.dataset.bulkAct === 'csv') exportEvidenceCsv();
            else clearBulk();
        }));
    }
    function exportEvidenceCsv() {
        // 只导出脱敏字段：引用 ID / 平台 / 时间 / 情感 / 主题 / 正文摘要。
        // 昵称、主页、IP 属地等可识别信息一律不取。
        const rows = currentItems.filter(x => bulkSel.has(x.comment_ref)).map(x => [
            x.comment_ref, x.platform, x.date,
            x.sentiment_label || x.sentiment || '', x.dimension || '',
            safeSnippet(cleanPlaceholder(x.snippet))
        ]);
        if (!rows.length) { toast('请先选择要导出的证据', 'err'); return; }
        const d = new Date();
        const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        downloadCsv(`评论证据_${day}.csv`,
            ['引用 ID', '平台', '时间', '情感', '主题', '正文摘要'], rows);
        toast(`已导出 ${rows.length} 条证据（仅脱敏字段）`, 'ok');
    }

    /* ---------------- 主题排行 ---------------- */
    async function loadRanking() {
        try {
            const t = await api('/api/analytics/topics', CP.filterParams());
            $('#rankTotal').textContent = `${num(t.meta.sample_size)} 条评论`;
            if (!t.ranking.length) {
                $('#rankList').innerHTML = '<div class="state"><div class="state-title">当前范围没有主题数据</div></div>';
                return;
            }
            const max = Math.max(...t.ranking.map(r => r.count)) || 1;
            $('#rankList').innerHTML = t.ranking.map(r => `
                <div class="rank-row" data-dim="${esc(r.dimension)}" role="button" tabindex="0"
                     aria-label="${esc(r.dimension)}，${num(r.count)} 条，负面 ${pct(r.negative_rate)}，点按按该主题筛选证据">
                    <span class="rank-name">${esc(r.dimension)}</span>
                    <span class="rank-track">
                        <i style="width:${Math.max(3, r.count / max * 100)}%"></i>
                    </span>
                    <span class="rank-value">${num(r.count)}</span>
                    <span class="badge rank-badge ${r.negative_rate >= 12 ? 'badge-neg' : 'badge-neu'}">负面 ${pct(r.negative_rate)}</span>
                </div>`).join('');
            $$('#rankList .rank-row').forEach(row => {
                // 键盘等价：这一行是这页主要的下钻入口，只能点就等于对键盘用户关掉了
                row.addEventListener('keydown', e => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); row.click(); }
                });
                row.addEventListener('click', () => {
                    const dim = row.dataset.dim;
                    state.filters.dimension = state.filters.dimension[0] === dim ? [] : [dim];
                    // 再点一次同一行即取消（与筛选栏的芯片一致）：不再依赖下拉框
                    if (CP.syncFilterBar) CP.syncFilterBar();
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
            <dt>数据集</dt><dd>${esc(m.dataset_name)}</dd>
            <dt>筛选条件</dt><dd>${esc(m.filters_text)}</dd>
            <dt>命中条数</dt><dd>${num(d.total)} 条</dd>
            <dt>时间范围</dt><dd>${esc((m.time_span || []).join(' ~ ') || '—')}</dd>
            <dt>入库时间</dt><dd>${esc(String(m.data_version || '—').slice(0, 16))}</dd>`;
    }

    async function loadResults() {
        const params = CP.filterParams({ limit: PAGE_SIZE, offset, order });
        const box = $('#resultList');
        setState(box, 'loading');
        try {
            const d = await api('/api/comments', params);
            total = d.total;
            currentItems = d.items || [];
            $('#totalBadge').textContent = `共 ${num(d.total)} 条`;
            // 这里以前把 #resultSub 整段改写成「第 1–20 条，共 1,969 条」，
            // 覆盖掉了模板里那句「按点赞量排序 · 点整行看详情，或用行尾按钮复制引用 ID」——
            // 而那是全站唯一说明"这一行能点什么"的句子，行尾两枚纯图标按钮只能靠猜。
            // 条数进右上角的 badge，句子留下；没结果时才换文案。
            if (d.empty) $('#resultSub').textContent = '当前条件下没有匹配评论';
            scopeBlock(d);

            if (d.empty) {
                setState(box, 'empty', '换一个关键词或放宽时间范围试试。');
                $('#pager').innerHTML = '';
                syncBulkToCurrent();
                renderBulkBar();
                return;
            }

            // 一行只回答三件事：这条说了什么、它是哪一类、下一步能做什么。
            // 原来六个 11px 元数据加一个同权重的引用 ID 平铺，读起来像串标签，
            // 而"打开/复制"这两个动作一个都没有——用户点开靠猜。
            box.innerHTML = d.items.map(it => {
                const tone = it.sentiment === 'negative' ? 'neg'
                    : it.sentiment === 'positive' ? 'pos' : 'neu';
                const facts = [
                    esc(it.platform),
                    esc(it.date),
                    it.dimension ? esc(it.dimension) : '',
                    it.like_count != null ? `${num(it.like_count)} 赞` : '点赞未提供'
                ].filter(Boolean).join(' · ');
                return `
                <div class="evidence-item" data-ref="${esc(it.comment_ref)}"
                     data-sent="${esc(it.sentiment || 'neutral')}"
                     title="引用 ID：${esc(it.comment_ref)}（点整行看详情，或用右侧按钮复制）">
                    <label class="ev-pick" title="选中后可导出 CSV">
                        <input type="checkbox" class="ev-bulk" data-bulk="${esc(it.comment_ref)}"
                               aria-label="选择证据 ${esc(it.comment_ref)}">
                    </label>
                    <div class="ev-body">
                        <div class="ev-snippet">${highlight(cleanPlaceholder(it.snippet), it.matched_keyword)}</div>
                        <div class="ev-meta">
                            <span class="badge badge-${tone}">${esc(it.sentiment_label)}</span>
                            <span class="ev-facts">${facts}</span>
                            ${it.source_type === 'screenshot'
                                ? `<span class="badge badge-brand" title="由截图 OCR 识别并经人工复核">截图识别${it.ocr_confidence != null ? ' · ' + Math.round(it.ocr_confidence * 100) + '%' : ''}</span>`
                                : ''}
                        </div>
                    </div>
                    <div class="ev-acts">
                        <button class="ev-act" type="button" data-copy
                                aria-label="复制引用 ID ${esc(it.comment_ref)}" title="复制引用 ID">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
                                 stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                                <rect x="9" y="9" width="11" height="11" rx="2.5"/>
                                <path d="M5 15V6.5A2.5 2.5 0 0 1 7.5 4H15"/></svg>
                        </button>
                        <button class="ev-act" type="button" data-open
                                aria-label="查看这条证据的详情" title="查看详情">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                                 stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                                <path d="M9 6l6 6-6 6"/></svg>
                        </button>
                    </div>
                </div>`;
            }).join('');

            // 三行封顶之后要给出口：量一下真实溢出，只在真的被截断时才长出「展开全文」，
            // 否则每条都挂一个按了没反应的按钮。点击不能冒泡——行本身也绑了打开详情。
            $$('#resultList .ev-snippet').forEach(el => {
                if (el.scrollHeight - el.clientHeight < 8) return;
                const more = document.createElement('button');
                more.type = 'button';
                more.className = 'ev-more';
                more.textContent = '展开全文';
                more.addEventListener('click', ev => {
                    ev.stopPropagation();
                    const opened = el.classList.toggle('is-open');
                    more.textContent = opened ? '收起' : '展开全文';
                });
                el.insertAdjacentElement('afterend', more);
            });

            $$('#resultList .evidence-item').forEach(el => {
                const ref = el.dataset.ref;
                const cb = el.querySelector('.ev-bulk');
                if (cb) {
                    // 勾选不能冒泡到整行（整行是"打开详情"），否则勾一条就弹详情。
                    cb.addEventListener('click', e => e.stopPropagation());
                    cb.addEventListener('change', () => {
                        if (cb.checked) bulkSel.add(ref); else bulkSel.delete(ref);
                        renderBulkBar();
                        syncSelectPageBtn();
                    });
                }
                // 行本身可点（鼠标习惯），但键盘路径是右边两枚真按钮：
                // 给 div 加 role=button 会把里面的按钮变成嵌套交互控件，读屏更乱。
                el.addEventListener('click', e => {
                    const t = /** @type {Element} */ (e.target);
                    // 点 label 的留白也算“勾选”，不能变成“打开详情”
                    if (t.closest('.ev-pick') || t.closest('.ev-bulk')) return;
                    if (t.closest('[data-copy]')) { copyRef(ref); return; }
                    openDetail(ref, /** @type {HTMLElement} */ (t.closest('.evidence-item')));
                });
            });
            // 换页 / 换筛选后旧选择不再对应屏上的行，先对齐再画工具条
            syncBulkToCurrent();
            renderBulkBar();
            syncSelectPageBtn();

            const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
            const cur = Math.floor(offset / PAGE_SIZE) + 1;
            // 99 页只能"下一页"，想回头核对第 8 条要点 40 次。
            // 补三件事：跳到第几页、每页多少条、以及把当前检索条件整条带走。
            $('#pager').innerHTML = `
                <span class="pager-info">第 <b>${cur}</b> / ${pages} 页 · 共 ${num(total)} 条</span>
                <label class="pager-jump">跳到
                    <input class="input pager-input" type="number" inputmode="numeric"
                           min="1" max="${pages}" value="${cur}" aria-label="跳到第几页">
                    页</label>
                <span class="pager-nav">
                    <button class="btn btn-sm" ${cur <= 1 ? 'disabled' : ''} data-page="prev">上一页</button>
                    <button class="btn btn-sm" ${cur >= pages ? 'disabled' : ''} data-page="next">下一页</button>
                </span>
                <label class="pager-size">每页
                    <select class="select pager-select" aria-label="每页显示多少条">
                        ${PAGE_SIZES.map(n => `<option value="${n}"${n === PAGE_SIZE ? ' selected' : ''}>${n}</option>`).join('')}
                    </select> 条</label>
                <button class="btn btn-sm" data-copy-link title="把当前筛选条件与页码复制成链接，发给别人复核">复制检索链接</button>
                <span class="pager-saved" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
                    <span class="note note-muted" style="margin:0;">常用检索（仅证据页）</span>
                    <select class="select" id="evSavedViews" aria-label="恢复已保存的检索"></select>
                    <input class="input" id="evViewName" style="width:120px;font-size:var(--fs-xs);"
                           placeholder="名称（可选）" aria-label="给这次检索起个名字" maxlength="30">
                    <button class="btn btn-sm" data-save-view type="button"
                            title="只保存证据页当前的检索条件，不是全站筛选视图">保存当前检索</button>
                </span>`;
            $$('#pager [data-page]').forEach(b => b.addEventListener('click', () => {
                offset = b.dataset.page === 'next' ? offset + PAGE_SIZE : Math.max(0, offset - PAGE_SIZE);
                syncPagingUrl();
                loadResults();
            }));
            const jump = $('#pager .pager-input');
            if (jump) jump.addEventListener('change', () => {
                const want = Math.min(pages, Math.max(1, parseInt(jump.value, 10) || 1));
                offset = (want - 1) * PAGE_SIZE;
                syncPagingUrl();
                loadResults();
            });
            const sizeSel = $('#pager .pager-select');
            if (sizeSel) sizeSel.addEventListener('change', () => {
                const keepFirst = Math.floor(offset / PAGE_SIZE) * PAGE_SIZE;
                PAGE_SIZE = parseInt(sizeSel.value, 10) || 20;
                // 换页长后落在最近的位置，而不是跳回第一页
                offset = Math.floor(keepFirst / PAGE_SIZE) * PAGE_SIZE;
                syncPagingUrl();
                loadResults();
            });
            const cl = $('#pager [data-copy-link]');
            if (cl) cl.addEventListener('click', copySearchLink);
            // 038② 保存 / 恢复常用检索（仅证据页本地）
            const saveBtn = $('#pager [data-save-view]');
            if (saveBtn) saveBtn.addEventListener('click', () => {
                const nameEl = $('#evViewName');
                saveCurrentSearch(nameEl ? nameEl.value : '');
            });
            renderSavedViews();
            const savedSel = $('#evSavedViews');
            if (savedSel) savedSel.addEventListener('change', () => {
                const i = savedSel.value;
                if (i === '') return;
                const v = readSaved()[Number(i)];
                if (v) restoreSaved(v.query);
            });
            CP.announce(`已找到 ${num(total)} 条证据，当前第 ${cur} 页，共 ${pages} 页`);
        } catch (e) {
            setState(box, 'error', e.message, loadResults);
            toast('证据检索失败：' + e.message, 'err');
        }
    }

    /* 复制引用 ID：把"可核对"这件事变成一次点击，而不是让人肉眼抄 CMT-20260603-0417。
       剪贴板 API 需要安全上下文（localhost/https），取不到就退回 textarea 方案。 */
    async function writeClipboard(text) {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            return;
        }
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;left:-9999px;';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
    }

    /* 把"我看到的这一批"整条带走：地址栏已经带着数据集、筛选、页码、排序，
       缺的只是一个复制按钮。发给同事打开就是同一批证据，不用复述条件。 */
    async function copySearchLink() {
        const url = location.href;
        try {
            await writeClipboard(url);
            toast('已复制检索链接，打开就是这一批证据', 'ok');
            CP.announce('已复制当前检索链接');
        } catch (e) {
            toast('复制失败，链接是 ' + url, 'err');
        }
    }

    async function copyRef(ref) {
        try {
            await writeClipboard(ref);
            toast(`已复制引用 ID ${ref}`, 'ok');
        } catch (e) {
            toast('复制失败，引用 ID 是 ' + ref, 'err');
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
        const head = String(cleanPlaceholder(d.content_masked) || '').replace(/\s+/g, ' ').slice(0, 22);
        return `「${dim}」证据线索：${head}`.slice(0, 120);
    }

    async function openDetail(ref, backEl) {
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
        mask.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="evDetailTitle">
            <div style="display:flex;align-items:flex-start;gap:10px;">
                <div>
                    <div class="modal-title" id="evDetailTitle">证据详情</div>
                    <div class="card-sub">${esc(d.comment_ref)} · 内部引用 ID（可复现，不含身份信息）</div>
                </div>
            </div>
            <div class="modal-quote">${esc(cleanPlaceholder(d.content_masked))}</div>
            <dl class="kv">
                <dt>所属数据集</dt><dd>${esc(d.dataset_name || '')}</dd>
                <dt>统计口径</dt><dd>${esc(d.filters_text || '')}</dd>
                <dt>平台</dt><dd>${esc(d.platform)}</dd>
                <dt>发布时间</dt><dd>${esc(d.comment_time || d.comment_date || '')}</dd>
                <dt>情感标签</dt><dd>${esc(d.sentiment_label)}</dd>
                <dt>业务主题</dt><dd>${esc((d.dimensions || []).join(' / ') || d.dimension || '—')}</dd>
                <dt>点赞数</dt><dd>${d.like_count == null ? '未提供（不按 0 处理）' : num(d.like_count)}</dd>
                <dt>数据来源</dt><dd>${esc(d.source_label || '公开平台采集')}${
                    d.source_type === 'screenshot'
                        ? `　<span class="badge badge-brand">OCR 置信度 ${
                            d.ocr_confidence != null ? Math.round(d.ocr_confidence * 100) + '%' : '未提供'}</span>`
                          + (d.ocr_engine ? `　<span style="font-size:var(--fs-micro);color:var(--ink-4);">引擎 ${esc(d.ocr_engine)}</span>` : '')
                        : ''}</dd>
                ${d.time_inferred ? '<dt>时间口径</dt><dd>截图内未包含发布时间，按导入日期计入（需结合原图核对）</dd>' : ''}
                ${d.negative_reason ? `<dt>负面原因</dt><dd>${esc(d.negative_reason)}</dd>` : ''}
            </dl>
            <div class="note note-muted" style="margin-top:12px;">${esc(d.privacy_note || '')}</div>
            <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end;">
                ${withTicket ? '<button class="btn btn-sm" data-ticket>据此建单</button>' : ''}
                <button class="btn btn-primary" data-close>关闭</button>
            </div>
        </div>`;
        document.body.appendChild(mask);
        // 和删除工单/建单弹窗同一套：Esc 关闭、Tab 锁在弹窗内、关掉后焦点回到
        // 打开它的那一行。以前这里自己 append 节点 + 手挂 Escape，三件只有一件。
        const close = () => { CP.closeModal(mask); mask.remove(); };
        // back 显式给行尾那颗「查看详情」按钮：点整行时 document.activeElement 是 body，
        // 不传的话关闭后焦点落在 body 上，键盘用户等于被扔回页首。
        CP.openModal(mask, { onEsc: close,
            back: (backEl && backEl.querySelector('[data-open]')) || backEl || undefined });
        mask.addEventListener('click', async e => {
            const target = /** @type {Element} */ (e.target);
            if (target.closest('[data-ticket]')) {
                const btn = /** @type {HTMLButtonElement} */ (target.closest('[data-ticket]'));
                // 053 这是全站唯一「点下即 POST 建单」却零确认的入口。先走统一确认框，
                // 写清优先级与去向、给取消，再动手写库；优先级沿用默认 P2 不变。
                const ok = await CP.confirmModal({
                    title: '按这条证据建一张处置工单？',
                    sub: '将按 P2 优先级为这条证据建单，进入「待确认」后由人决定是否分派。',
                    note: '建单会写进工单时间线，不可撤销（审计日志留痕）。',
                    okLabel: '按 P2 建单',
                    danger: false
                });
                if (!ok) return;
                btn.disabled = true;
                btn.textContent = '建单中…';
                try {
                    const t = await api('/api/tickets', null, {
                        method: 'POST',
                        body: JSON.stringify({
                            title: ticketTitle(d),
                            summary: `由证据 ${d.comment_ref} 发起：${String(cleanPlaceholder(d.content_masked) || '').slice(0, 160)}`,
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
            if (target === mask || target.closest('[data-close]')) close();
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
            badge.textContent = r.flags.length ? `${r.flags.length} 条提示` : '近 7 天无异常';
            badge.className = 'badge ' + (r.flags.length ? 'badge-neu' : 'badge-ok');

            const thHtml = `<details class="risk-th"><summary>阈值定义</summary>
                    <div class="risk-th-list">
                        ${Object.entries(r.thresholds || {}).map(([k, v]) =>
                            `<span class="badge" style="background:var(--surface-sunken);color:var(--ink-2);">${esc(TH_LABELS[k] || k)}：${esc(String(v))}</span>`).join('')}
                    </div>
                </details>`;

            const card = $('#riskCard');
            if (!r.flags.length) {
                /* 没有异常时，这张卡原来是 210px 高、正文只写"没事"，
                   把主题排行和证据列表整整推下去一屏。压成一行，把阈值定义留在一个可点开的入口里。 */
                if (card) card.classList.add('is-quiet');
                list.innerHTML = `<div class="risk-quiet">
                        <span class="risk-quiet-text">近 7 天没有指标越过设定阈值。这不等于"没有问题"，
                            只表示在当前样本里没有统计异常。</span>
                        ${thHtml}
                    </div>`;
                $('#riskNote').classList.add('is-hidden');
                return;
            }
            if (card) card.classList.remove('is-quiet');
            list.innerHTML = r.flags.map(f => `
                    <div style="padding:10px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:8px;background:var(--surface-3);">
                        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                            <span class="badge ${f.level === '关注' ? 'badge-neg' : 'badge-neu'}">${esc(f.level)}</span>
                            <span style="font-size:var(--fs-base);font-weight:600;color:var(--ink);">${esc(f.title)}</span>
                        </div>
                        <div style="font-size:var(--fs-sm);color:var(--ink-2);margin-top:5px;line-height:1.65;">${esc(f.detail)}</div>
                        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;">
                            ${Object.entries(f.basis || {}).map(([k, v]) =>
                                `<span class="badge" style="background:var(--surface-sunken);color:var(--ink-2);">${esc(k)}：${esc(String(v))}</span>`).join('')}
                        </div>
                        ${(f.evidence || []).length ? `<div style="margin-top:7px;display:flex;flex-direction:column;gap:4px;">
                            ${f.evidence.map(ev => `<a href="javascript:void(0)" data-ref="${esc(ev.comment_ref)}"
                                style="font-size:var(--fs-xs);color:var(--brand-text);text-decoration:none;">
                                ${esc(ev.comment_ref)} · ${esc(ev.platform)} · ${esc(ev.date || '')} · 点赞 ${num(ev.like_count)}
                                <span style="color:var(--ink-3);">「${esc(cleanPlaceholder(ev.snippet))}」</span></a>`).join('')}
                        </div>` : ''}
                    </div>`).join('');
            $$('#riskList a[data-ref]').forEach(a => {
                a.addEventListener('click', () => openDetail(a.dataset.ref));
            });

            const note = $('#riskNote');
            note.classList.remove('is-hidden');
            note.innerHTML = `${esc(r.disclaimer)}${thHtml}`;
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

    const selPageBtn = $('#evSelectPage');
    if (selPageBtn) selPageBtn.addEventListener('click', selectPage);

    $('#orderSelect').addEventListener('change', e => {
        order = e.target.value;
        offset = 0;
        syncPagingUrl();
        loadResults();
    });
})();
