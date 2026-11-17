/* PAGE-06 风险研判与处置中心 · 总览
 *
 * 这一页只做一件事：把「哪个主题、多急、该谁管、先做什么」摆在一屏里，
 * 让管理员点一下就建单。风险百分比与归口理由全部来自后端，
 * 前端不再算第二遍——两处口径一旦不一致，页面上就没人知道该信哪个。
 *
 * 页面是一级目录的父项，子项「处置工单」才是干活的地方。
 * 所以顶部多一条工单闭环条：研判出结论、建了单、有没有超期，在这里一眼能对上，
 * 而不需要切到子页才发现"原来那三单还没人接"。
 */
(function () {
    'use strict';
    const { $, $$, esc, num, api, toast, state } = CP;

    const view = {
        depts: [], risk: null, current: null, meta: null, loading: false,
        // AI 补研判还在路上：这一版是规则结果，可以先看、可以建单，
        // 回来后再原位替换建议与归口理由（风险数字不归 AI 管）。
        aiPending: false,
        // 这一页只有一张风险表，两种口径是它的两个时间窗。
        // 'live' = 近 30 天（实时、不落库）；'custom' = 自选时段（研判一次、存档）。
        win: 'live',
        // 046 表头排序状态：只作用于当前渲染数据，切窗口时归零
        sort: { key: null, dir: 'desc' },
        // 风险表筛选：all / none（未归口）/ overdue（工单已超期）。只作用于这张表。
        filter: 'all',
        // ?dim= 深链只应用一次：之后用户自己点、自己关，不再被拽回去
        dimApplied: false,
    };

    const LEVEL_CLS = { '高': 'high', '中高': 'midhigh', '中': 'mid', '低': 'low' };
    /* 研判请求的代号：切数据集、点「重新研判」都会开启新一轮，
       上一轮还在路上的 AI 补研判回来时据此丢弃，不能把旧结果盖到新页面上。 */
    let riskSeq = 0;

    function canEdit() { return !!(view.meta && view.meta.can_edit); }
    function canCreate() { return !!(view.meta && view.meta.can_create); }
    /* 工单模块开没开。跟"有没有权限"是两件事：没权限要找人开权限，
       模块没开是这个演示态本来的样子，两句话不能共用。 */
    function ticketsOn() { return !!(view.meta && view.meta.tickets_enabled); }
    /* 建不了单时该给哪句话：先分清是"没开"还是"没权"。 */
    function noCreateWhy() {
        if (scoped()) return '部门账号不建单，开单由学校侧决定';
        return ticketsOn() ? '当前角色无建单权限'
                           : '演示模式不开放建单，可在「处置工单」里只读查看';
    }
    /* 部门账号的视角：后端已把部门清单与主题都收窄到归口本部门，
       前端据此换文案、藏掉"全校口径"的区块。**不做权限判断**——权限只认后端。 */
    function scoped() { return !!(view.risk && view.risk.scoped); }
    function scopeName() {
        return (view.risk && view.risk.scoped && view.risk.scoped.dept_name) || '本部门';
    }

    /* ---------------- 工单闭环条 ----------------
     * 数据取 /api/tickets/stats：那里已经有"在办 / 超期 / 按时率"的定义，
     * 唯一口径。前端只做展示，不自己从列表里数——否则又会出现
     * "总览说 3 单、工单页说 4 单"这种没人能解释的差。
     * 工单关闭或当前角色无权限时整条隐藏（badge 探到 can_view=false 就停手）。
     */
    async function loadTodo() {
        const box = $('#dpTodo');
        const wrap = $('#dpTodoMetrics');
        if (!box || !wrap) return;
        try {
            // 先用 badge 探测：工单未启用（CP_TICKETS=0）时它照样返回 200 + can_view=false，
            // 而 /api/tickets/stats 是 404（"功能不存在"）。直接请求 stats 会让控制台
            // 留一条红色 404——前端验证脚本按"零控制台报错"判定，会被判成页面故障。
            const probe = await api('/api/tickets/badge');
            if (!probe || !probe.can_view) { box.hidden = true; return; }
            let d;
            if (probe.readonly) {
                // 演示态：/api/tickets/stats 是 404（模块没开）。角标已经把
                // 累计 / 在办 / 超期三个数带回来了，直接用它——
                // 再去请求一次注定失败的接口，只会在控制台留一条红字。
                d = { total: probe.total, open: probe.pending, overdue: probe.overdue,
                      on_time: {}, readonly: true };
            } else {
                try {
                    d = await api('/api/tickets/stats');
                } catch (e2) {
                    // 统计挂了也要让"在办 / 超期"这两个最要紧的数字出现
                    d = { total: null, open: probe.pending, overdue: probe.overdue, on_time: {} };
                }
            }
            const ot = d.on_time || {};
            // 每一项都点得动，并且点进去看到的就是这一项的数字对应的那批单：
            // 卡片说"在办 5 单"，点开却是一屏"全部工单"，人只会觉得数字对不上。
            const items = [
                { v: d.total == null ? '—' : d.total, unit: '单', label: '累计建单',
                  foot: d.readonly ? '演示模式：工单列表只读' : '研判页创建的工单一并计入',
                  to: '/tickets?status_group=all' },
                { v: d.open, unit: '单', label: '在办', foot: '尚未归档的工单',
                  to: '/tickets?status_group=open' },
                { v: d.overdue, unit: '单', label: '已超期', tone: d.overdue ? 'neg' : 'ok',
                  foot: d.overdue ? '已过 SLA 截止时间' : '没有超期单',
                  to: '/tickets?status_group=overdue' },
                (() => {
                    // 2/2 单按时归档 = 100%，这个数和旁边「已超期 3 单」并排时
                    // 读起来像系统在自夸。样本不足 10 单就不换算百分比，
                    // 直接给"2/2 单"这个事实。脚注由 CP.onTimeFoot 统一生成，
                    // 工单页那张 KPI 用的是同一句——同一份数据两页两种说法，最伤信任。
                    const den = ot.denominator || 0;
                    const enough = den >= 10;
                    return {
                        v: den ? (enough ? ot.rate : `${ot.numerator}/${den}`) : '—',
                        unit: den ? (enough ? '%' : '单') : '',
                        label: '按时归档率',
                        tone: enough ? ((ot.rate != null && ot.rate >= 90) ? 'ok' : 'warn') : '',
                        foot: den ? CP.onTimeFoot(ot) : '还没有归档单',
                        to: '/tickets?status_group=closed'
                    };
                })()
            ];
            // 只读态没有归档率可讲：stats 没跑，四格里的最后一格会显示"— / 还没有归档单"，
            // 那是"没查到"冒充"没有"，比不显示更糟。
            if (d.readonly) {
                items.pop();
                // 只读页只有一个列表、不认 status_group，带着筛选参数跳过去
                // 会落在"未筛选"的页面上——说"这 3 单"却给出 7 单，比不链更深。
                items.forEach(i => { i.to = '/tickets'; });
            }
            wrap.innerHTML = items.map(i => `<a class="dp-todo-item" href="${esc(i.to || '/tickets')}"
                    title="点击查看这 ${esc(String(i.v))}${esc(i.unit || '')}对应的工单">
                <div class="dp-todo-label">${esc(i.label)}</div>
                <div class="dp-todo-value ${i.tone || ''}">${esc(String(i.v))}${
                    i.unit ? `<span>${esc(i.unit)}</span>` : ''}</div>
                <div class="dp-todo-foot">${esc(i.foot)}</div>
            </a>`).join('');
            box.hidden = false;
        } catch (e) {
            box.hidden = true;
        }
    }

    /* ---------------- KPI ---------------- */
    /* 第三张卡的标题在整段生命周期里只能是一个值。
       第 11 轮实测：骨架态写「建议优先建单」、数据到了变成「建议优先处置」——
       因为骨架分支只判了 scoped()，真值分支还判了 !ticketsOn()，两处谓词不一致。
       现在两处都走 todoLabel()；能力探测还没回来（view.meta 为空）时只给共同前缀
       「建议优先」，等确定了再补全，免得先摆出一个当前做不到的动作名。 */
    function todoLabel() {
        if (!view.meta) return '建议优先';
        return (scoped() || !ticketsOn()) ? '建议优先处置' : '建议优先建单';
    }
    /* 研判没回来之前，这一排不能先摆出「0 个 + 绿点」。
       第 10 轮实测：0.6s / 2.0s 两次采样都是「中高风险主题 0 个」+ 三个绿点，
       6.0s 才变成「1 个」——3 米外只看得见大字 0 和绿点，值班的人 5 秒内就会
       得出「今天没事」。所以分三种状态：加载中（骨架）、失败（破折号 + 中性）、
       成功（照常给数字与 tone）。「没有」和「还没查到」必须长得不一样。 */
    function renderKpi() {
        const bx = $('#dpKpi');
        if (!bx) return;
        const depts = view.depts;
        const topics = (view.risk && view.risk.topics) || [];
        // 研判还没到（加载中 / 失败）就先别出数字：这时 topics 是空数组，
        // 算出来的 0 会被当成"今天平安"，而它其实只是"还没查到"。
        if (!view.risk) {
            const busy = !!view.loading;
            bx.setAttribute('aria-busy', busy ? 'true' : 'false');
            const foot = busy ? '正在读取研判结果…' : '研判未完成，可点右上「重新研判」重试';
            const labels = ['部门', '中高风险主题', todoLabel()];
            bx.innerHTML = labels.map(l => `
                <div class="card kpi" data-tone="">
                    <div class="kpi-label">${esc(l)}</div>
                    <div class="kpi-value">${busy
                        ? '<span class="skeleton" style="display:inline-block;width:58px;height:26px;border-radius:6px"></span>'
                        : '—'}<span class="kpi-unit"></span></div>
                    <div class="kpi-foot">${esc(foot)}</div>
                </div>`).join('');
            return;
        }
        bx.setAttribute('aria-busy', 'false');
        const high = topics.filter(t => t.level === '高' || t.level === '中高').length;
        const todo = topics.filter(t => t.priority === 'P0' || t.priority === 'P1').length;
        // 「中高风险 1 个」与表格里 3 行标着「中高/中/中」并排时，读者只会觉得这个数算错了。
        // 把四个等级的分布直接写在脚注里：数字与表格能当场对上，不用再猜口径。
        const lvCount = { '高': 0, '中高': 0, '中': 0, '低': 0 };
        topics.forEach(t => { if (lvCount[t.level] != null) lvCount[t.level] += 1; });
        const lvFoot = topics.length
            ? `共研判 ${topics.length} 个主题 · 高 ${lvCount['高']} / 中高 ${lvCount['中高']}`
              + ` / 中 ${lvCount['中']} / 低 ${lvCount['低']}`
            : '等待研判';
        // 这里不再放「已认领主题 X/8」和「覆盖 N 个主题」：
        // 示例只配了三个部门，那两处永远亮着告警，信息量抵不上一屏红色。
        // 没配主责部门的主题，风险表里归口那一格直接留空。
        const cards = [
            scoped()
                // 部门账号：部门数恒为 1，摆出来没有信息量；换成"归口主题有几个"
                ? { label: '归口主题', value: num(topics.length), unit: '个',
                    foot: `只统计归口到「${scopeName()}」的主题`, tone: '' }
                : { label: '部门', value: num(depts.length), unit: '个',
                    foot: depts.length ? '每个部门可编辑、可归档' : '尚未配置部门',
                    tone: depts.length ? '' : 'warn' },
            { label: '中高风险主题', value: num(high), unit: '个',
              foot: lvFoot,
              tone: high ? 'neg' : 'ok' },
            // 建不了单的时候（演示态 / 部门账号 / 无权限），标题里说"建单"等于
            // 指着一个不存在的动作。文案跟着能力走：能建才叫建单，否则叫处置。
            { label: todoLabel(), value: num(todo), unit: '个',
              // 这一格以前写「只数 P1 及以上」，而整页只显示「高 / 中高 / 中 / 低」四个等级，
              // P1 在页面上根本不存在——读者只能猜它等于哪个档。改用页面自己的词，
              // 并把"为什么这两档优先"落到时限上：紧急 1 / 高 4 工作小时首次响应。
              foot: todo
                  ? (scoped() ? '紧急与高两档 · 首次响应要 1 / 4 工作小时' : '紧急与高两档 · 建议先派人核实')
                  : (high ? `等级「高」0 个；另有 ${lvCount['中高']} 个中高，建议本周确认`
                          : '等级「高」0 个；暂无需要立刻处置的主题'),
              tone: todo ? 'warn' : (high ? '' : 'ok') }
        ];
        bx.innerHTML = cards.map(c => `
            <div class="card kpi" data-tone="${esc(c.tone || '')}">
                <div class="kpi-label">${esc(c.label)}<span class="dp-dot ${c.tone}"></span></div>
                <div class="kpi-value">${esc(String(c.value))}<span class="kpi-unit">${esc(c.unit)}</span></div>
                <div class="kpi-foot">${esc(c.foot)}</div>
            </div>`).join('');
    }

    /* ---------------- 部门清单 ---------------- */
    function renderDepts() {
        const box = $('#dpList');
        if (!box) return;
        const list = view.depts;
        $('#dpCount').textContent = list.length + ' 个';
        const btn = $('#dpNew');
        if (btn) btn.hidden = !canEdit();
        if (!list.length) {
            box.innerHTML = `<div class="state">
                <div class="state-title">还没有配置部门</div>
                <div>没有部门就判不出归口。点右上角「新增部门」，或重启服务写入 3 个示例部门。</div>
            </div>`;
            return;
        }
        box.innerHTML = list.map(d => `
            <div class="dp-card" data-dept="${esc(d.dept_id)}">
                <div class="dp-card-head">
                    <div>
                        <div class="dp-name">${esc(d.name)}</div>
                        <div class="dp-owner">${esc(d.owner_name || '未填负责人')}${
                            d.owner_title ? ' · ' + esc(d.owner_title) : ''}${
                            d.contact ? ' · ' + esc(d.contact) : ''}</div>
                    </div>
                    <div class="dp-card-acts">
                        ${d.sla_hours ? `<span class="badge" title="该部门的一般响应时限，按工作小时计">响应 ${CP.workHours(d.sla_hours)}</span>` : ''}
                        ${canEdit() ? `<button class="btn btn-sm" data-edit="${esc(d.dept_id)}">编辑</button>
                        <button class="btn btn-sm btn-ghost" data-arch="${esc(d.dept_id)}">归档</button>` : ''}
                    </div>
                </div>
                <div class="dp-duties">${esc(d.duties || '（未填写主要职责，归口判断只能靠主题）')}</div>
                <div class="dp-dims">
                    ${(d.dimensions || []).map(x => `<span class="badge badge-brand">${esc(x)}</span>`).join('')
                      || '<span class="badge badge-warn">未认领主题</span>'}
                    ${(d.keywords || []).slice(0, 8).map(k => `<span class="dp-kw">${esc(k)}</span>`).join('')}
                    ${(d.keywords || []).length > 8 ? `<span class="dp-kw">+${d.keywords.length - 8}</span>` : ''}
                </div>
            </div>`).join('');
    }

    /* ---------------- 风险表 ----------------
     * 这一页只有**一张**风险表，两种口径是它的两个时间窗：
     *   · 近 30 天（实时）：以数据最后一天为锚点往回 30 天，不落库；
     *   · 自选时段：选一段区间单独研判，结论存档，可以据此建单。
     *
     * 以前是两张并排的表——左边有「优先级」列、右边没有，同一个主题左右给出
     * 31% 与 9%。读者只会得出一个结论："这两张表里有一张是错的"。
     * 现在共用一套列、一个渲染函数，切换窗口就换一份数据，屏幕上永远只有一个答案。
     */
    function windowShort(from, to) {
        if (!from || !to) return '';
        const a = String(from), b = String(to);
        // 只留月日：表头这一格宽 112px，跨年写成 2025-11-01~2026-01-31 会顶穿列宽。
        // 完整区间在表头上方的窗口行里，这里只需要"哪一段"能被认出。
        return a.slice(5) + '~' + b.slice(5);
    }

    /* 风险指数条：**四段而不是一段**。
     *
     * 以前这一格是一条按等级上色的实心条，同一行右边紧挨着「62 / 784（7.9%）」——
     * 两个百分比并排，管理者会把 57% 读成"57% 的评论是负面的"。它其实是
     * 量级 / 负面率 / 增速 / 互动 四项加权后乘以置信度的合成分。
     *
     * 所以条子直接画成构成：每段宽度就是这一项**贡献了多少分**，
     * 加起来等于左边那个数字。等级颜色留在数字上（结论），条子讲"为什么"。
     * 后端的算法见 departments._risk：score = 100 · Σ(w·v)/Σw · (0.55+0.45·conf)。
     */
    /* 「量级」是样本规模，不是负面：用中性 --risk-magnitude，不占用「红＝负面」。
       样式组已删掉 [style*="--neg"] 的权宜覆盖，这里直接写令牌，量级不会变回红色。 */
    const RISK_PART_COLOR = {
        '量级': 'var(--risk-magnitude)', '负面率': 'var(--warn)', '增速': 'var(--brand)', '互动': 'var(--neu)'
    };

    function riskBar(t) {
        const cls = LEVEL_CLS[t.level] || 'low';
        // 卡片布局没有表头，光一个 57 谁也不认识它。标签只给窄屏，
        // 桌面有表头时它隐形（.dp-lbl 默认 display:none）。
        const lbl = '<span class="dp-lbl">风险指数</span>';
        const parts = t.parts || [];
        const conf = 0.55 + 0.45 * (Number(t.confidence) || 0);
        // 权重分母只算"参与计分"的项：增速在样本不足时退出，其余三项归一化补上
        const wSum = parts.reduce((a, p) => a + (p.value == null ? 0 : p.weight), 0) || 1;
        let segs = parts
            .filter(p => p.value != null && p.value > 0)
            .map(p => ({
                name: p.name,
                label: p.label || '',
                // 这一项对最终分数的贡献（分）
                pts: 100 * p.weight * p.value / wSum * conf
            }));
        const sum = segs.reduce((a, p) => a + p.pts, 0);
        const score = Number(t.risk) || 0;
        // 归一化到分数本身：四段加起来必须正好等于左边那个数，差一分都会被人发现
        if (sum > 0 && score > 0) segs = segs.map(p => ({ ...p, pts: p.pts * score / sum }));

        const bar = segs.length
            ? segs.map(p => `<i style="width:${p.pts.toFixed(2)}%;background:${
                RISK_PART_COLOR[p.name] || 'var(--ink-faint)'}"
                title="${esc(p.name)} ${p.pts.toFixed(1)} 分 · ${esc(p.label)}"></i>`).join('')
            // 老快照里没有 parts 时退回"按等级整条"的画法，不能让这一格空掉
            : `<i class="${cls}" style="width:${Math.max(4, score)}%"></i>`;

        /* 第 11 轮：色条以前是 aria-hidden + 分项只写在 <i title> 里，
           于是读屏与键盘只能拿到总分 57，拿不到「57 由哪四段构成」——
           产品最自著的能力恰好最不可复算。现在整条挂 role="img" 与一句
           把四段分值与各自口径都念完的 aria-label：子元素不再被逐个朗读，
           但四段信息完整进入无障碍树。视觉不变，也不新增可聚焦元素
           （行本身已 tabindex=0，回车展开的四段明细在详情面板里）。 */
        const segLabel = segs.length
            ? '四段构成：' + segs.map(p => `${p.name} ${p.pts.toFixed(1)} 分，${p.label}`).join('；')
              + `；合计 ${score} 分`
            : `风险指数 ${score} 分，这份快照没有分项数据`;

        // 数字不带 %：它是 0–100 的合成分，不是占比。和右边「负面 / 总」那个 7.9%
        // 长得一样时，人会两个都读成"占比"。刻度写在表头与图例里。
        return `<div class="dp-risk-cell">${lbl}
            <div class="dp-risk-num ${cls}" title="风险指数 ${score}（0–100 合成分，不是负面占比）· 回车展开四段构成">${
                score}</div>
            <div class="dp-risk-track" role="img" aria-label="${esc(segLabel)}">${bar}</div>
        </div>`;
    }

    /* 实时口径的工单格：实时结果不挂单号（那一栏存的是区间快照），
       所以这里直接给一颗「建单」按钮——和自选时段那一列长得一样、做的事也一样，
       差别只是走 /api/departments/create-ticket。建完把单号回填到这一格。 */
    function liveTicketCell(t, i) {
        if (t.ticket_code) {
            return `<span class="dp-tk on" title="已建单">${esc(t.ticket_code)}</span>`;
        }
        if (!canCreate()) {
            return `<span class="dp-tk" title="${esc(noCreateWhy())}">—</span>`;
        }
        const orphan = !t.department;
        return `<button class="btn btn-sm dp-mk" type="button" data-mklive="${i}"
                    title="${esc(orphan
                        ? '这个主题没有归口部门：建单后会进入「待分派」，需要人工指派'
                        : '用当前近 30 天口径建单（风险、归因、归口跟着带过去）')}">${
                    orphan ? '建单（待分派）' : '建单'}</button>`;
    }

    /* 当前窗口的结论对象与行 */
    function activeRun() { return view.win === 'custom' ? monthlyRun() : view.risk; }
    function activeTopics() {
        const r = activeRun();
        return (r && r.topics) || [];
    }

    /* ---------------- 风险表筛选 ----------------
     * 八行里最需要"被挑出来"的是两类：**没有归口的**（没人认领，要先去配部门）
     * 与**工单已过期的**（正在欠账）。以前只能靠肉眼扫「归口部门」那一列。
     *
     * 只作用于这张表：周带回答的是"全部主题分别落在未来的哪一天"，
     * 筛掉一半会把它变成一个不完整的答案——于是筛选行上写明这一点。
     *
     * 「已超期」只认**真实工单的截止日**（due_basis==='ticket' 且已过今天）；
     * 未建单的虚线点是"按部门时限推算"，还没开始的时钟谈不上过期。
     */
    function isOverdueTopic(t) {
        if (!t || t.due_basis !== 'ticket' || !t.due) return false;
        const o = ymdParts(t.due);
        if (!o) return false;
        const n = new Date();
        return ymdKey(o) < ymdKey({ y: n.getFullYear(), m: n.getMonth() + 1, d: n.getDate() });
    }
    const RFILTER = { all: () => true, none: t => !t.department, overdue: isOverdueTopic };
    function filteredTopics() {
        const pred = RFILTER[view.filter] || RFILTER.all;
        return activeTopics().map((t, i) => ({ t, i })).filter(x => pred(x.t));
    }

    /* 表头上面那一行图例。它解释两件事：这个数字由四段合成、它不是负面占比。
       后者是这个页面最容易被读错的一句——57% 与同行的 7.9% 并排时，
       管理者会以为"57% 的评论是负面的"，而那是 7.9 那条。
       讲单位与口径的说明必须常驻，不能收进 tooltip（收了一次就没人再看见）。

       「在哪展开」必须按视口写：桌面详情就插在被点那一行的正下方，
       390 是单列、详情在整张表之后（第 10 轮实测 1100px 开外）。
       同一句"下方"在两个视口指的不是同一个位置，写死一句就等于骗一半的人。 */
    function riskLegend() {
        const item = (name, color) =>
            `<span class="dp-lg-item"><i style="background:${color}"></i>${name}</span>`;
        const narrow = window.matchMedia('(max-width: 720px)').matches;
        const how = narrow
            ? '点任意一行，明细在整张表格下方（页面会自动滚过去）'
            : '点任意一行（或按回车）在这一行下方展开四项分值与权重';
        return `<div class="dp-risk-legend">
            <span class="dp-lg-name">风险指数 =</span>
            ${item('量级', 'var(--risk-magnitude)')}${item('负面率', 'var(--warn)')}
            ${item('增速', 'var(--brand)')}${item('互动', 'var(--neu)')}
            <span class="dp-lg-note">四项加权 · 0–100 分 · <b>不是负面占比</b>，后者是同一行的「负面 / 总」
                · ${how}</span>
        </div>`;
    }

    /* 筛选行：三枚 chip + 一句"这张表里现在剩几个"。
       数量写在 chip 上，读者不必先点一遍才知道「未归口」有几条。 */
    function riskFilters() {
        const topics = activeTopics();
        if (!topics.length) return '';
        const shown = filteredTopics().length;
        const chips = [
            ['all', '全部', topics.length],
            ['none', '未归口', topics.filter(t => !t.department).length],
            // 工单页的「已超期」数的是**张数**，这里数的是**主题数**。
            // 同一个词两个量并排时，读者只会认为数字不一致。所以在 aria/title 里把单位写死。
            ['overdue', '已超期', topics.filter(isOverdueTopic).length]
        ];
        return `<div class="dp-risk-filters" role="group" aria-label="风险表筛选">
            <span class="dp-rf-label">只看</span>
            ${chips.map(([k, label, n]) => {
                const unit = k === 'all' ? '个主题' : (k === 'none' ? '个未归口主题' : '个主题存在超期工单');
                return `<button class="chip${view.filter === k ? ' on' : ''}"
                type="button" data-rf="${k}" aria-pressed="${view.filter === k ? 'true' : 'false'}"
                aria-label="看 ${n} ${unit}" title="按主题数计（工单张数见「处置工单」）"
                >${label} ${n}</button>`;
            }).join('')}
            <span class="dp-rf-count">显示 <b>${shown}</b> / ${topics.length} 个 ·
                只筛这张表，周带上仍是全部主题</span>
        </div>`;
    }

    /* 046 表头排序：只对当前这份 topics 排，不重新请求接口。
       data-row 仍带回原始索引，详情面板按 activeTopics()[view.current] 取数，
       所以只换 DOM 顺序、不换索引，点开的行不会串到别的主题。
       入参是已筛过、已带索引的 [{t,i}]：筛完再排，排完索引仍指回原数组。 */
    const SORT_KEYS = { risk: 1, negative: 1, total: 1 };
    function sortedTopics(pairs) {
        const key = view.sort.key;
        if (!key || !SORT_KEYS[key]) return pairs;
        const dir = view.sort.dir === 'asc' ? 1 : -1;
        return pairs.slice().sort((a, b) => {
            const av = Number(a.t[key]) || 0, bv = Number(b.t[key]) || 0;
            if (av === bv) return a.i - b.i;       // 稳定：同值保持原顺序
            return (av - bv) * dir;
        });
    }
    function applySortState() {
        $$('#dpRisk [data-sort]').forEach(el => {
            el.classList.remove('asc', 'desc');
            const active = el.getAttribute('data-sort') === view.sort.key;
            if (active) el.classList.add(view.sort.dir);   // 101：升降 class 行为保持不变
            // 101：当前列 ascending/descending，其余 .sortable 一律 none；CSS 箭头认这套值
            el.setAttribute('aria-sort', active
                ? (view.sort.dir === 'asc' ? 'ascending' : 'descending')
                : 'none');
        });
    }
    function toggleSort(key) {
        if (!SORT_KEYS[key]) return;
        if (view.sort.key === key) view.sort.dir = view.sort.dir === 'desc' ? 'asc' : 'desc';
        else { view.sort.key = key; view.sort.dir = 'desc'; }   // 首次：大的在前
        renderTable();          // 只重画当前数据
        applySortState();       // 表头刚被重建，激活态要重新贴
        placeDetail();          // 详情跟着选中行回到行内
        // 101：renderTable 用 innerHTML 重建了表头，原焦点节点随之销毁；
        // 把焦点还给同一个 data-sort 元素，键盘/点击排序后焦点不落到 body。
        const head = $$('#dpRisk [data-sort]').find(el => el.getAttribute('data-sort') === key);
        if (head) head.focus({ preventScroll: true });
    }

    function renderTable() {
        const box = $('#dpRisk');
        if (!box) return;
        // 重画之前先把详情搬回出生位置：它此刻可能正躺在表格的某一行里，
        // 表格一 innerHTML，那个节点就跟着销毁，#dpDetail 从此再也找不到。
        const d = $('#dpDetail');
        if (d && d.closest('tr.dp-detail-row')) box.insertAdjacentElement('afterend', d);
        const custom = view.win === 'custom';
        const r = activeRun();

        if (!r) {
            box.innerHTML = custom
                ? `<div class="state">
                    <div class="state-title">${monthly.previewMode ? '还没有本部门的区间研判' : '还没有研判记录'}</div>
                    <div>${monthly.previewMode
                        ? `在上面选一段时间（或点「上个月 / 近 30 天」），再点「立即研判」。这里只统计归口到「${esc(scopeName())}」的主题。`
                        : '定时任务会在每月初自动研判上个月；也可以在上面选一段时间，点「立即研判」补跑。'}</div>
                </div>`
                : '<div class="skeleton" style="height:220px"></div>';
            return;
        }

        const badge = $('#dpEngine');
        if (badge) {
            // 规则结果先出、AI 后补：补的过程中徽标要说明"屏幕上是规则结果"，
            // 不能挂着「AI 研判」让人以为模型已经看完；回来后再变回正式标签。
            const pending = !custom && view.aiPending;
            badge.textContent = pending
                ? '统计规则 · AI 研判中'
                : (r.engine_label || (custom
                    ? (r.engine === 'deepseek' ? 'AI 研判 · DeepSeek' : '统计规则')
                    : '统计规则'));
            badge.className = 'badge ' + (r.engine === 'deepseek'
                ? 'badge-brand' : (pending ? '' : 'badge-warn'));
            badge.title = pending
                ? '风险数字与归口已按统计规则算出；DeepSeek 正在补充归口理由与整改建议'
                : (custom
                    ? '这段区间研判用的引擎'
                    : '实时研判按统计规则计算，锚点回看 30 天');
        }

        const topics = activeTopics();
        if (!topics.length) {
            box.innerHTML = custom
                ? '<div class="note note-muted">这段时间没有命中任何主题的评论。</div>'
                : '<div class="note">当前数据集没有可研判的主题。</div>';
            return;
        }

        const cap = custom
            ? windowShort(r.date_from, r.date_to)
            : (r.anchor ? '截至 ' + String(r.anchor).slice(5) : '近 30 天');
        const capTitle = custom
            ? `${r.date_from} ~ ${r.date_to}（这次研判的窗口）`
            : `锚点 ${r.anchor || '—'}，往回 30 天`;

        // 先筛后排：索引始终指回 activeTopics() 原数组，点开的行不会串到别的主题
        const rows = sortedTopics(filteredTopics());

        const tableHtml = rows.length ? `<div class="table-scroll"><table class="table dp-risk-table cp-sticky-head${
                custom ? ' dp-mrun-table' : ''}">
            <caption class="sr-only">部门风险研判：风险指数、等级与归口</caption>
            <thead><tr>
                <th scope="col">主题</th>
                <th scope="col" class="dp-col-risk sortable" data-sort="risk"
                    tabindex="0" title="点击按风险值排序（再点切换升降）">风险指数<span class="dp-th-hint" title="${esc(capTitle)}">${
                    esc(cap)}</span></th>
                <th scope="col">等级</th>
                <th scope="col" class="dp-col-neg">
                    <span class="sortable" data-sort="negative" role="button" tabindex="0"
                          title="点击按负面数排序（再点切换升降）">负面数</span>
                    <span style="color:var(--ink-4);"> / </span>
                    <span class="sortable" data-sort="total" role="button" tabindex="0"
                          title="点击按评论量排序（再点切换升降）">评论量</span>
                </th>
                <th scope="col">归口部门</th>
                <th scope="col" class="dp-col-tk">工单</th>
                <th scope="col"></th>
            </tr></thead><tbody>
            ${rows.map(({ t, i }) => `
                <tr data-row="${i}" tabindex="0" class="${view.current === i ? 'on' : ''}">
                    <td class="dp-td-dim"><b>${esc(t.dimension)}</b></td>
                    <td class="dp-td-risk">${riskBar(t)}</td>
                    <td class="dp-td-lv"><span class="dp-lv ${LEVEL_CLS[t.level] || 'low'}">${esc(t.level)}</span></td>
                    <td class="num dp-td-neg"><span class="dp-lbl">负面 / 总</span>${num(t.negative)} / ${num(t.total)}
                        <span class="dp-rate">${t.neg_rate}%</span></td>
                    <td class="dp-td-dept"><span class="dp-lbl">归口</span>${t.department
                        // 以前留空：空白格既看不出"没配"还是"不用管"，建单按钮还照样亮着。
                        // 现在写「未归口」，和它右边的「建单（待分派）」是一句话的两半。
                        ? `<div class="dp-dept">${esc(t.department.name)}</div>
                           <div class="dp-dept-owner">${esc(t.department.owner_name || '')}</div>`
                        : '<span class="dp-noperm" title="这个主题还没有配主责部门，建单后会进入「待分派」">未归口</span>'}</td>
                    <td class="dp-td-tk">${custom ? ticketCell(t, r.run_id) : liveTicketCell(t, i)}</td>
                    <td class="dp-td-act"><span class="dp-caret">›</span></td>
                </tr>`).join('')}
            </tbody></table></div>` : `<div class="note note-muted"><span>当前筛选下没有符合条件的主题。
                <button class="btn btn-sm" type="button" data-rf="all">看全部 ${topics.length} 个主题</button></span></div>`;
        box.innerHTML = riskLegend() + riskFilters() + tableHtml;
        applySortState();       // 表头每次重画都要把激活的升 / 降箭头贴回去
    }

    /* ---------------- 周带 ----------------
     * 这一页原来只有一句「部门响应 48 / 24 工作小时」，读的人得自己换算成日子；
     * 表格里八行主题按分数排，却排不出"哪件今天就得有人动"。
     * 周带把时限变成位置：每个主题钉在它该办完的那一天，越靠左越急。
     *
     * 三种点，各有各的来历，绝不混着画：
     *   · 实心点 = 已建单，位置取那张单真实的 sla_due；
     *   · 虚线点 = 未建单，位置是「现在建单 + 部门时限」的推算；
     *   · 未归口 = 不进日期格，落在带尾「待归口」里。
     *    时钟从归口开始。没有部门就没有 deadline，硬塞一个 48 小时
     *    等于替学校许了一个没人认领的承诺——这正是这一页最不该做的事。
     *
     * 日期全部来自后端的 due / due_basis（后端有工作日历和节假日），
     * 前端只负责排格子，不自己加工作小时。 */
    const WD = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
    function ymdParts(s) {
        const m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
        return m ? { y: +m[1], m: +m[2], d: +m[3] } : null;
    }
    function ymdKey(o) {
        return o.y + '-' + String(o.m).padStart(2, '0') + '-' + String(o.d).padStart(2, '0');
    }
    /* 本周一 00:00。周一为一周之首；行标题「本周/下周」靠它算差值。 */
    function weekStart(ms) {
        const d = new Date(ms);
        d.setDate(d.getDate() - (d.getDay() + 6) % 7);
        d.setHours(0, 0, 0, 0);
        return d.getTime();
    }
    /* 从本周一起取 n 个工作日（周一~周五），**按自然周铺**，每格自己带 pins。
       以前是从"今天"起数五个工作日当一行——周三打开时那一行会跨两个自然周，
       标题写「本周」却有两格是下周的日期。现在一格永远属于它所在的自然周，
       本周已经过去的那几天照样占格，因为"这周一空着"本身是条信息。 */
    function bandWorkdays(n) {
        const out = [];
        const d = new Date(weekStart(Date.now()));
        for (let guard = 0; out.length < n && guard < 60; guard++) {
            const wd = d.getDay();
            if (wd !== 0 && wd !== 6) {
                out.push({
                    key: ymdKey({ y: d.getFullYear(), m: d.getMonth() + 1, d: d.getDate() }),
                    label: (d.getMonth() + 1) + '-' + String(d.getDate()).padStart(2, '0'),
                    wd: WD[wd],
                    time: d.getTime(),
                    pins: []
                });
            }
            d.setDate(d.getDate() + 1);
        }
        return out;
    }
    function pinHtml(p, withDate) {
        const t = p.t;
        const lv = LEVEL_CLS[t.level] || 'low';
        const real = t.due_basis === 'ticket';
        const fullDept = t.department ? t.department.name : '未归口';
        // 标签里去掉括注（「招生就业处（招生办）」→「招生就业处」）：
        // 157px 的一格里括注必然被省略号截断，而半截的名字比短名字更难认。
        // 完整名称仍在 aria-label 与 title 里，读屏与悬停都不会丢。
        const deptName = fullDept.replace(/[（(][^）)]*[）)]/g, '').trim() || fullDept;
        // 「催谁」= 部门 + 人。以前只有部门，人还在下方表里——读者要来回对照两次。
        // 一格只有 157px，所以部门可以省略号、责任人不能：信息按"名字能截、人不能丢"排。
        const owner = (t.department && t.department.owner_name) || '';
        const due = t.due ? String(t.due).slice(0, 10) : '';
        const aria = `${t.dimension}，风险 ${t.risk} 分（${t.level || '—'}），${fullDept}`
            + (owner ? `，负责人 ${owner}` : '') + '，' + (due
                ? (real ? `工单截止 ${due}` : `按现在建单推算 ${due} 到期`)
                : '没有时限（未归口）') + (real ? '，已建单' : '，未建单');
        return `<button type="button" class="wk-pin lv-${lv} ${real ? 'is-real' : 'is-ghost'}"
            data-row="${p.i}" aria-pressed="${view.current === p.i ? 'true' : 'false'}"
            title="${esc(aria)}" aria-label="${esc(aria)}">
            <b>${Number(t.risk) || 0}<span class="wk-unit">分</span></b>
            <span class="wk-nm">${esc(t.dimension)}${t.level
                ? `<i class="wk-lv">${esc(t.level)}</i>` : ''}<em>
                <span class="wk-dept">${esc(deptName)}</span>${
                    owner ? `<i class="wk-own">${esc(owner)}</i>` : ''}${
                    withDate && due ? `<i class="wk-due">${esc(due.slice(5))}</i>` : ''}</em></span></button>`;
    }
    function renderWeek() {
        const box = $('#dpWeek');
        if (!box) return;
        const topics = activeTopics();
        // 只有实时口径有"本周"可言。自选时段讲的是过去某一段发生了什么，
        // 把它也钉到未来两周的格子上，等于给一段历史加了一个假 deadline。
        if (view.win !== 'live' || !view.risk || !topics.length) { box.hidden = true; return; }
        box.hidden = false;

        // 铺四周（20 个工作日）再取前三周：从"本周一"起铺是为了五列永远对齐同一批星期，
        // 而本周已经过去的那几天照样占格。之所以要四周而不是三周——周日照样要能铺满
        // 「本周＋两周」三个可用行，否则周一一开工，未来的格子就只剩两行了。
        // 第三行只在真有时限落进来时出现，否则它就是五格空白，白白把人往下推一屏。
        const days = bandWorkdays(20);
        const byKey = new Map(days.map(x => [x.key, x]));
        const late = [], none = [], overdue = [];
        const now = new Date();
        const todayKey = ymdKey({ y: now.getFullYear(), m: now.getMonth() + 1, d: now.getDate() });

        topics.forEach((t, i) => {
            const p = { t, i };
            const o = ymdParts(t.due);
            if (!t.department || !o) { none.push(p); return; }
            if (t.due_basis === 'ticket' && ymdKey(o) < todayKey) { overdue.push(p); return; }
            const day = byKey.get(ymdKey(o));
            if (day) day.pins.push(p); else late.push(p);
        });

        // 按日期升序：横条里最急的排最前，读者不用自己比大小。
        const byDue = (a, b) => String(a.t.due).localeCompare(String(b.t.due));
        overdue.sort(byDue); late.sort(byDue);

        // 整周已经过去的行不占位（周六/周日打开时"本周"五格全空，看起来像页面坏了），
        // 其余每周一行，标题由周差算：本周 / 下周 / 再下一周。
        const weeks = [];
        days.forEach(day => {
            const ws = weekStart(day.time);
            let w = weeks[weeks.length - 1];
            if (!w || w.ws !== ws) { w = { ws, row: [] }; weeks.push(w); }
            w.row.push(day);
        });
        const visible = weeks.filter(w => w.row[w.row.length - 1].key >= todayKey);
        const withPins = i => !!(visible[i] && visible[i].row.some(d => d.pins.length));
        // 第三行只在它自己有事时出现；前两行始终在。
        const shown = visible.slice(0, withPins(2) ? 3 : 2);
        // 「本周/下周/再下一周」按与本周的周差算，不是按可见行序——
        // 周日在铺四周时第三行已经差到三周，写「再下一周」就错了一格。
        const WEEK_TITLES = ['本周', '下周', '再下一周', '三周后', '四周后'];
        const titles = shown.map(w => WEEK_TITLES[
            Math.round((w.ws - weekStart(Date.now())) / 6048e5)] || '');

        const rows = shown.map(({ row }, ri) => {
            const cellsHtml = row.map(day => {
                const isToday = day.key === todayKey;
                const past = day.key < todayKey;
                return `<div class="wk-cell${isToday ? ' is-today' : ''}${past ? ' is-past' : ''}">
                    <div class="wk-date">${day.label} ${day.wd}${isToday ? ' · 今天' : ''}</div>
                    <div class="wk-pins">${day.pins.map(p => pinHtml(p)).join('')}</div></div>`;
            }).join('');
            const first = row[0], last = row[row.length - 1];
            return `<div class="wk-week">
                <div class="wk-week-label"><b>${titles[ri] || (first.label + ' 起')}</b>${first.label}~${last.label}</div>
                <div class="wk-cells" style="grid-template-columns:repeat(${row.length},minmax(0,1fr))">${cellsHtml}</div></div>`;
        }).join('');

        const strip = (label, hint, list, cls) => list.length ? `
            <div class="wk-strip ${cls}">
                <div class="wk-strip-label">${label}<em>${hint}</em></div>
                <div class="wk-strip-pins">${list.map(p => pinHtml(p, true)).join('')}</div>
            </div>` : '';

        // 横条不设窗口上限：落不进窗口的主题必须被承接，且自带日期——
        // 以前它们只有名字，读者知道"更远"却不知道多远。
        // 日期写在每个 pin 上，横条的说明就不再复述一遍范围：那一列只有 108px，
        // 「11-16~12-04 之后 · 12-10」会把说明折成四行、把 pin 挤到右边。
        const span = list => list.length === 1
            ? String(list[0].t.due).slice(5, 10)
            : `${String(list[0].t.due).slice(5, 10)} ~ ${String(list[list.length - 1].t.due).slice(5, 10)}`;
        const lastDay = shown.length ? shown[shown.length - 1].row.slice(-1)[0] : days[0];
        const horizon = shown.length
            ? `${shown[0].row[0].label}~${lastDay.label}，${shown.reduce((n, w) => n + w.row.length, 0)} 个工作日`
            : '';
        /* 标题必须说实话：这条带子默认铺两行（本周 + 下周），
           只写「本周」会让下周三的截止日看起来像建错了日期。 */
        const title = shown.length >= 2 ? '本周与下周要办结的事' : '接下来要办结的事';

        // 窄屏：格子读不了（一格比一个标签还窄），折成按周分组的纵列。
        const bucket = list => list.map(p => pinHtml(p, true)).join('');
        box.innerHTML = `
            <div class="card-head wk-head">
                <div>
                    <h2 class="card-title">${title}</h2>
                    <div class="card-sub">数字是<strong>风险指数（0–100）</strong>；位置＝时限落在哪一天：
                        实心是已建单的工单截止，虚线是未建单按部门时限推算的到期日。
                        窗口 ${horizon}</div>
                </div>
                <div class="card-tools">
                    <span class="wk-key"><span><i class="solid"></i>已建单</span><span><i class="ghost"></i>未建单</span></span>
                </div>
            </div>
            ${overdue.length ? strip('已超期', '工单已过截止时间', overdue, 'is-overdue') : ''}
            <div class="wk-weeks">${rows}</div>
            ${late.length ? strip('窗口之外', '已归口，时限在窗口之后', late, 'is-late') : ''}
            ${none.length ? strip('待归口', '没有部门就没有时限', none, 'is-none') : ''}
            <div class="wk-list" aria-hidden="false">
                ${overdue.length ? `<div class="wk-group is-overdue"><div class="wk-group-head">已超期<em>工单已过截止时间</em></div>${bucket(overdue)}</div>` : ''}
                ${shown.map(({ row }, ri) => {
                    // 窄屏只用得上一套分组标题，与桌面那几行同源，避免"桌面写再下一周、
                    // 手机写本周内"这种同一个日期两个说法。
                    const list = row.reduce((a, d) => a.concat(d.pins), []);
                    if (!list.length) return '';
                    return `<div class="wk-group"><div class="wk-group-head">${titles[ri] || (row[0].label + ' 起')}<em>${row[0].label}~${row[row.length - 1].label}</em></div>${bucket(list)}</div>`;
                }).join('')}
                ${late.length ? `<div class="wk-group"><div class="wk-group-head">窗口之外<em>${span(late)}</em></div>${bucket(late)}</div>` : ''}
                ${none.length ? `<div class="wk-group"><div class="wk-group-head">待归口<em>没有部门就没有时限</em></div>${bucket(none)}</div>` : ''}
            </div>
            <p class="wk-foot scope-line">时限按<strong>工作小时</strong>计（每工作日 ${esc(String(
                (view.meta && view.meta.workday_hours) || 7))} 小时，扣周末与午休，逢节假日跳过）；
                证据截至研判锚点，时钟从现在起算。点任意一个主题，明细在下方风险表里对应那一行。</p>`;
    }

    /* ---------------- 主题详情（实时表与区间表共用） ----------------
     * 同一份结论，两处入口：上面的「实时研判 · 近 30 天」（实时口径）
     * 与下面的区间研判表（自选时间段）。渲染只写一份，是因为"风险拆解四项 +
     * 归因 + 建议 + 证据"这四块的口径必须完全一致——两处各写一遍，
     * 改权重说明时必然漏掉一处，而页面上看不出哪一处是旧的。
     *
     * opt.mode：live（实时，近 30 天 vs 前 30 天）/ month（自选区间，本期 vs 上期）。
     * opt.createAttr：建单按钮要带的 data 属性。两种表的建单接口不同
     * （实时走 /api/departments/create-ticket，区间走研判快照），
     * 但按钮长什么样、放在哪，必须一样。
     */
    function detailHtml(t, opt) {
        const isMonth = opt.mode === 'month';
        const parts = (t.parts || []).map(p => {
            const off = p.value == null;
            return `<div class="dp-part">
                <div class="dp-part-head">
                    <span>${esc(p.name)}</span>
                    <span class="dp-part-w">权重 ${Math.round(p.weight * 100)}%</span>
                </div>
                <div class="dp-part-track">
                    <i style="width:${off ? 0 : Math.round(p.value * 100)}%;${off ? 'opacity:.25' : ''}"></i>
                </div>
                <div class="dp-part-label">${esc(p.label)}</div>
            </div>`;
        }).join('');

        const advice = (t.advice || []).map((a, i) => `
            <div class="dp-advice">
                <span class="dp-advice-idx">${i + 1}</span>
                <span>${esc(a)}</span>
            </div>`).join('');

        const samples = (t.samples || []).map(s => `
            <div class="dp-sample">
                <div class="dp-sample-meta">${esc(s.platform || '')} · ${esc(s.date || '')}
                    · 赞 ${num(s.like_count || 0)}${s.reason ? ' · ' + esc(s.reason) : ''}</div>
                <div class="dp-sample-text">${esc(s.snippet || '')}</div>
            </div>`).join('');

        const dept = t.department;
        const growthText = t.growth != null
            ? (isMonth ? ' · 本期负面是上期的 ' + t.growth + ' 倍'
                       : ' · 近 30 天是前 30 天的 ' + t.growth + ' 倍')
            : '';
        const evidence = opt.evidence
            ? `<a class="btn btn-sm" href="/evidence?dataset_id=${encodeURIComponent(state.dataset_id || '')}&dimension=${encodeURIComponent(t.dimension)}&sentiment=negative">看该主题证据</a>`
            : '';
        // 没有建单权限时给一枚虚线小标签，而不是一个灰掉的按钮：
        // 禁用按钮在浅色底上几乎看不见边框，读起来像一句断掉的文字，
        // 而且每开一行都提示"你不能点"——那句话对部门账号是废话。
        const makeBtn = opt.canCreate
            ? `<button class="btn btn-sm btn-primary" ${opt.createAttr || ''}>按建议建单</button>`
            : `<span class="dp-noperm" title="${esc(opt.createTitle || '当前角色无建单权限')}">${
                esc(opt.createTitle || '当前角色无建单权限')}</span>`;

        return `<div class="dp-detail">
            <div class="dp-detail-head">
                <div>
                    <div class="dp-detail-title">${esc(t.dimension)} · 风险指数 ${t.risk}（${esc(t.level)}）</div>
                    <div class="dp-detail-sub">
                        负面 ${num(t.negative)} / ${num(t.total)} 条（${t.neg_rate}%）
                        ${t.max_like ? ' · 单条最高赞 ' + num(t.max_like) : ''}${growthText}
                    </div>
                </div>
                <div class="dp-detail-acts">${evidence}${makeBtn}${
                    opt.weekBack ? '<button class="btn btn-sm btn-ghost" type="button" data-backweek="1">回到周带 ↑</button>' : ''}</div>
            </div>

            ${dept ? `<div class="dp-assign">
                <span class="dp-assign-label">归口</span>
                <b>${esc(dept.name)}</b>
                <span>${esc(dept.owner_name || '')}${dept.owner_title ? ' · ' + esc(dept.owner_title) : ''}</span>
                ${dept.contact ? `<span class="dp-assign-contact">${esc(dept.contact)}</span>` : ''}
                ${t.ai_reason ? `<div class="dp-ai-reason">模型判断：${esc(t.ai_reason)}</div>` : ''}
                ${dept.matched_keywords && dept.matched_keywords.length
                    ? `<div class="dp-ai-reason">规则命中关键词：${dept.matched_keywords.map(esc).join('、')}</div>` : ''}
            </div>` : ''}

            <div class="dp-sec-title">风险指数拆解
                <span class="dp-sec-hint">四项加权，样本不足的项不计分（不是记 0 分）</span>
            </div>
            <div class="dp-parts">${parts}</div>

            <div class="dp-sec-title">整改建议
                <span class="dp-sec-hint">${t.advice_source === 'ai' ? 'AI 生成 · DeepSeek，数字须与上方统计一致'
                                                              : '统计规则生成'}</span>
            </div>
            <div class="dp-advices">${advice}</div>

            ${samples ? `<div class="dp-sec-title">代表证据<span class="dp-sec-hint">${
                t.samples_outside
                    // 窗口内一条负面都没有时才回落全量：不写清楚，
                    // 读者对比日期会以为“七月的判断拿三年前的评论塊脚”
                    ? '窗口内没有负面样本，以下为「全量最高赞」，不属于本次统计'
                    : (isMonth ? '仅脱敏文本，取自该区间内点赞最高的负面评论'
                               : '仅脱敏文本，取自近 30 天内点赞最高的负面评论')}</span></div>
            <div class="dp-samples">${samples}</div>` : ''}
        </div>`;
    }

    function renderDetail() {
        const box = $('#dpDetail');
        if (!box) return;
        const r = activeRun();
        const t = r && (r.topics || [])[view.current];
        if (!t) { box.innerHTML = ''; return; }
        const custom = view.win === 'custom';
        if (custom) {
            box.innerHTML = detailHtml(t, {
                mode: 'month',
                canCreate: !!monthly.canCreate && !monthly.previewMode,
                createTitle: monthly.previewMode ? '建单由学校侧发起' : noCreateWhy(),
                createAttr: (monthly.canCreate && !monthly.previewMode)
                    ? `data-mkmonth="${esc(r.run_id)}" data-dim="${esc(t.dimension)}"` : '',
                evidence: false        // 证据页只按当前数据集的实时口径筛，带不了这段区间
            });
            return;
        }
        box.innerHTML = detailHtml(t, {
            mode: 'live',
            canCreate: canCreate(),
            createTitle: noCreateWhy(),
            createAttr: canCreate() ? `data-mk="${view.current}"` : '',
            evidence: !scoped(),
            // 只有带子在的时候才有「回去」这回事（自选时段没有周带）
            weekBack: !!(view.risk && view.win === 'live' && activeTopics().length)
        });
    }


    function selectRow(i) {
        view.current = (view.current === i) ? null : i;
        $$('#dpRisk tr[data-row]').forEach(tr => {
            tr.classList.toggle('on', Number(tr.getAttribute('data-row')) === view.current);
        });
        renderDetail();        placeDetail();
        syncDimUrl();          // 选中哪一行要能发给同事（同 /tickets 的 ?ticket=）
    }

    /* 把选中的主题写进地址栏：?dim=宿舍住宿。
       以前选中行不落 URL，想让人"看宿舍住宿这一条"只能口头描述 ——
       而 /tickets 从第 10 轮起就是 ?ticket=，同一个产品两套做法。
       用 replaceState 而不是 pushState：选中是浏览动作，不是新页面，
       把历史栈塞满会让"后退"变成逐行回退。 */
    function syncDimUrl() {
        const q = new URLSearchParams(location.search);
        const t = view.current != null ? activeTopics()[view.current] : null;
        if (t && t.dimension) q.set('dim', t.dimension);
        else q.delete('dim');
        const s = q.toString();
        history.replaceState(null, '', location.pathname + (s ? '?' + s : ''));
    }

    /* 带 ?dim= 进来就直接选中那一行：链接里写的就是"要看这条"。
       只做一次（view.dimApplied），之后用户自己点、自己关不再被拽回去。 */
    function applyDeepDim() {
        if (view.dimApplied) return;
        const dim = new URLSearchParams(location.search).get('dim');
        if (!dim) { view.dimApplied = true; return; }
        const idx = activeTopics().findIndex(t => t.dimension === dim);
        if (idx < 0) return;                        // 研判还没回来：等下一轮
        // 有一半可能这一行正被筛选挡着，点开链接却发现"没有这一行"最糟
        if (!filteredTopics().some(x => x.i === idx)) {
            view.filter = 'all';
            renderTable();
        }
        view.dimApplied = true;
        selectRow(idx);
    }

    /* 换筛选 = 换一批行：旧选中行可能已经不在里面，先收起详情，
       否则屏幕上会出现"选中的那一行看不见、详情还开着"的错位。 */
    function setFilter(k) {
        if (!RFILTER[k] || view.filter === k) return;
        view.filter = k;
        view.current = null;
        renderTable();
        renderDetail();
        placeDetail();
        syncDimUrl();
        // 表格整块重画会把被点的 chip 一起换掉：焦点必须还回同一颗，
        // 否则键盘用户按一次"未归口"就掉到 body 上（下一个 Tab 从页首开始）。
        const again = /** @type {HTMLElement|null} */ (
            document.querySelector(`#dpRisk [data-rf="${k}"]`));
        if (again) again.focus({ preventScroll: true });
    }

    /* 详情紧跟在被点的那一行之后，而不是整张表的最下面。
       原来的位置是表尾：点第 1 行后页面把详情滚进视口，选中行已经滚出屏幕上方，
       行与详情之间没有任何视觉连接——八行里挑第 2、3 行时，人会怀疑"这是我点的那条吗"。
       DOM 上把 #dpDetail 搬进被点行后面的一行 <tr>（跨满整行），
       收起时再把这一行拆掉。7 = 主题/风险/等级/负面总/归口/工单/箭头，
       加列必须同步这里，否则 colspan 会短一截。 */
    /* 滚到详情，但让它的头对齐顶栏下沿。
       用 scrollIntoView({block:'center'}) 会把一个比屏幕还高的详情居中，
       结果标题被推到视口上方（实测 top=-122）——"点开之后只看到中段"。 */
    function scrollToDetail(detail) {
        const bar = document.querySelector('.topbar');
        const pad = (bar ? /** @type {HTMLElement} */ (bar).offsetHeight : 0) + 10;
        const top = detail.getBoundingClientRect().top + window.scrollY - pad;
        window.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    }

    function placeDetail() {
        const table = $('#dpRisk table');
        const detail = $('#dpDetail');
        if (!table || !detail) return;
        const host = table.querySelector('tr.dp-detail-row');
        // ≤720 必须放回表外：表格是 588px 宽的横滑容器，跨满整行的单元格也是 588，
        // 详情右半边会跑到屏幕外被裁掉（正文读起来像断在句中间）。
        // 窄屏本来就没有"行在屏幕上、详情在底下"的距离问题——一屏只放得下一件事。
        const narrow = window.matchMedia('(max-width: 720px)').matches;
        if (host) {
            if (narrow) {
                $('#dpRisk').insertAdjacentElement('afterend', detail);   // 先搬人，再拆房
                host.remove();
                // 窄屏没有"行内展开"的位置关系可依赖：详情在整张表之后，
                // 不滚过去，用户只会看到卡片变蓝、页面不动 —— 那是"点了没反应"。
                scrollToDetail(detail);
                return;
            }
            host.remove();
        }
        if (view.current == null) return;             // 收起：详情本来就被清空了
        if (narrow) {
            // 首次展开时还没有 detail-row，详情就在表后原位——滚过去就行
            scrollToDetail(detail);
            return;
        }
        const tr = table.querySelector(`tbody tr[data-row="${view.current}"]`);
        if (!tr) return;
        const row = document.createElement('tr');
        row.className = 'dp-detail-row';
        const cell = document.createElement('td');
        cell.colSpan = 7;
        cell.appendChild(detail);
        row.appendChild(cell);
        tr.insertAdjacentElement('afterend', row);
        row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    // 旋屏/拉窗口跨过 720：详情该在行内还是表外，得重新判一次
    window.addEventListener('resize', () => {
        if (view.current != null) placeDetail();
    });

    /* 从周带跳到表格行时，焦点必须跟着走。
       实测：点第一个 pin 后页面滚到 y=1017，而焦点仍留在那个 pin 上（top=-626，已在屏外）
       ——WCAG 2.4.7「焦点可见」直接失败，键盘/读屏用户的下一个 Tab 落在看不见的元素上。
       焦点交给详情卡（scrollToDetail 对齐的就是它的头），下一个 Tab 自然落到详情里第一个动作。 */
    function focusDetail() {
        const card = $('#dpDetail');
        if (!card) return;
        if (!card.hasAttribute('tabindex')) card.setAttribute('tabindex', '-1');
        card.focus({ preventScroll: true });
    }

    /* 从详情回到周带：详情有 2000px 高，"回去"在正常操作里等于一次盲滚。
       回去时焦点落到刚看过的那一枚 pin 上，而不是带子顶部。 */
    function backToWeek() {
        const band = $('#dpWeek');
        if (!band || band.hidden) return;
        const bar = document.querySelector('.topbar');
        const pad = (bar ? /** @type {HTMLElement} */ (bar).offsetHeight : 0) + 10;
        window.scrollTo({
            top: Math.max(0, band.getBoundingClientRect().top + window.scrollY - pad),
            behavior: 'smooth'
        });
        const pin = band.querySelector(`.wk-pin[data-row="${view.current}"]`);
        if (pin) /** @type {HTMLElement} */ (pin).focus({ preventScroll: true });
    }

    /* ---------------- 区间研判 ----------------
     * 服务端每小时检查一次"上个月的研判跑了没"，页面负责三件事：
     * ① 把最近一次的结论摆出来（含这段已建过的工单）；
     * ② 自选时间段后点「立即研判」补跑（演示用，或机器那几天没开）；
     * ③ 让人在某一行的「建单」上手动建单。
     * **这里没有门槛**：风险 24% 的"交通出行"该不该开单，取决于这几天
     * 有没有人在盯、别的渠道是不是也在说同一件事——系统不知道这两件事。
     * 这一块也不自己算风险：所有数字都来自 risk_runs 快照，
     * 前端再算一遍只会出现"页面上和工单里两个风险值"。
     *
     * 两种口径共用一个卡位（后端用 preview 标记区分）：
     *   · 学校侧（admin/analyst）：跑一次 → 落库 → 有历史记录、能按快照建单；
     *   · 部门账号：跑一次 → 就地出结论、不落库（monthly.preview），
     *     只含归口本部门的主题，没有历史条、没有建单按钮。
     * 为什么不给部门也建一份快照：全校快照是学校侧统一出具的，
     * 两套快照抢同一个 (数据集, 区间) 唯一键，迟早互相覆盖。
     */
    const monthly = {
        settings: null, runs: [], current: null,
        preview: null, previewMode: false,
        busy: false, canRun: false, canCreate: false
    };

    /* 当前该渲染哪份结论：学校侧是落库快照，部门侧是上一次就地研判的结果。 */
    function monthlyRun() { return monthly.previewMode ? monthly.preview : monthly.current; }


    function isoDate(d) {
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
            + '-' + String(d.getDate()).padStart(2, '0');
    }

    function fromIso(s) { return new Date(String(s) + 'T00:00:00'); }

    function shiftDays(d, n) {
        const x = new Date(d.getTime());
        x.setDate(x.getDate() + n);
        return x;
    }

    /* 上个月整月：1 号到月末（月末靠"下月 1 号减一天"算，不写 28/30/31） */
    function lastMonthRange() {
        const now = new Date();
        const first = new Date(now.getFullYear(), now.getMonth() - 1, 1);
        const end = new Date(now.getFullYear(), now.getMonth(), 0);
        return { from: isoDate(first), to: isoDate(end) };
    }

    /* 默认区间 = 上个月，但**夹到数据集覆盖范围内**：
     * 演示库只到 2026-07 时，"上个月"整段落在数据外面，点了必然报"这段没有评论"。
     * 这时退成"数据集最后一个月"（1 号 ~ 数据最后一天），至少点了有东西看。
     * 默认值和「上个月」按钮共用这一条规则，两者行为不一致会让人以为是 bug。
     *
     * 数据只覆盖到月初几天时不要那个残缺的"月"：2026-07-01~07-07 只有一周数据，
     * 点下去得到"评论 12 条、8 个主题全是 0%"，读的人会以为研判坏了。
     * 这种情况退成"数据集最后 30 天"，样本量才接近一次真正能看的研判。 */
    function rangeForDataset() {
        const r = lastMonthRange();
        const end = state.datasetMeta && state.datasetMeta.time_end;
        if (end && end < r.from) {
            const e = fromIso(end);
            if (e.getDate() < 15) {
                return { from: isoDate(shiftDays(e, -29)), to: end };
            }
            return { from: isoDate(new Date(e.getFullYear(), e.getMonth(), 1)), to: end };
        }
        return r;
    }

    /* 区间标签：整月显示「2026 年 10 月」，否则「2026-09-15 ~ 2026-10-14」。
       判整月要连"同年同月"一起判：6/1~8/31 结束日加一天也是 1 号，
       只看这一条会把它显示成「2026 年 6 月」，读的人以为系统只算了六月。 */
    function rangeLabel(from, to) {
        if (!from || !to) return '—';
        const isMonth = /^\d{4}-\d{2}-01$/.test(from)
            && String(from).slice(0, 7) === String(to).slice(0, 7)
            && isoDate(shiftDays(fromIso(to), 1)).slice(-2) === '01';
        if (isMonth) {
            return `${from.slice(0, 4)} 年 ${Number(from.slice(5, 7))} 月`;
        }
        return `${from} ~ ${to}`;
    }

    function readRange() {
        const f = $('#dpRangeFrom');
        const t = $('#dpRangeTo');
        return { from: (f && f.value) || '', to: (t && t.value) || '' };
    }

    function setRange(r) {
        const f = $('#dpRangeFrom');
        const t = $('#dpRangeTo');
        if (f) f.value = r.from;
        if (t) t.value = r.to;
    }

    /* 工单列：有单就显示单号，没单给一个「建单」按钮。
       没有"未达门槛"这种状态——这里不设门槛，开不开单由人判断。
       部门账号没有建单权限：有单显示单号（他可能就是承办人），没单留一个破折号，
       不必给一个点下去只会报 403 的按钮。 */
    function ticketCell(t, runId) {
        if (t.ticket_code) {
            const cls = t.ticket_existing ? 'dp-tk old' : 'dp-tk on';
            const tip = t.ticket_note || (t.ticket_existing ? '这段区间已有工单' : '已建单');
            return `<span class="${cls}" title="${esc(tip)}">${esc(t.ticket_code)}</span>`;
        }
        if (!monthly.canCreate) {
            return `<span class="dp-tk" title="${esc(monthly.previewMode ? '部门账号不建单，开单由学校侧决定' : noCreateWhy())}">—</span>`;
        }
        // 上一张单已驳回/归档时仍给出口：结案不等于这件事不用管，
        // 补一句备注重新开单比让人去工单页手工拼一张更接近真实工作流。
        const orphan = !t.department;
        const again = t.ticket_closed
            ? { label: '重新建单', tip: t.ticket_note || `上一张单 ${t.ticket_closed} 已结案，可重新建单` }
            : (orphan
                ? { label: '建单（待分派）', tip: '这个主题没有归口部门：建单后会进入「待分派」，需要人工指派' }
                : { label: '建单', tip: '用这份研判快照建单（风险、归因、归口、证据都跟着带过去）' });
        return `<button class="btn btn-sm dp-mk" type="button"
                    data-mkmonth="${esc(runId)}" data-dim="${esc(t.dimension)}"
                    title="${esc(again.tip)}">${again.label}</button>`;
    }

    /* 自选时段才有的"这次研判是哪一次"：周期、窗口、样本、引擎、完成时间 + 一句话结论。
       近 30 天是即时算的，没有周期也没有完成时间，这一块就整块收起来。 */
    /* 036 研判快照的完成时间对外只显示到日 / 小时：秒级留在 title。
       凌晨三点跑完的定时任务，写全 "03:07:22" 会让人以为是有人连夜操作。 */
    function displayRunTime(s) {
        const t = String(s == null ? '' : s).trim();
        if (!t) return '—';
        const m = t.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})(?::\d{2})?/);
        return m ? `${m[1]} ${m[2]}时` : t;
    }

    function renderSnap() {
        const box = $('#dpSnap');
        if (!box) return;
        const r = monthlyRun();
        if (view.win !== 'custom' || !r) { box.hidden = true; box.innerHTML = ''; return; }
        const st = monthly.settings || {};
        const c = r.counts || {};
        const linked = c.linked || 0;
        // 日期框跟着"当前展示的这次研判"走：否则点历史看某一段，再点「覆盖重跑」
        // 跑的是日期框里另外那段，不是眼前这段——这种错位没人能一眼看出来。
        if (r.date_from && r.date_to) {
            const cur = readRange();
            if (cur.from !== r.date_from || cur.to !== r.date_to) {
                setRange({ from: r.date_from, to: r.date_to });
            }
        }
        // 只留状态，不留解释：这一刻是"本部门口径"还是"这段已建了几单"，
        // 是读这一屏需要的；"建单由人决定"这句话每个数据集都说一遍，没有增量。
        const ticketNote = monthly.previewMode
            ? '本部门口径'
            : (!st.auto_ticket
                ? '工单功能未启用'
                : (linked ? `已建单 ${linked} 个主题` : ''));
        const prevText = (r.window && r.window.prev_period)
            ? ` · 比对 ${esc(String(r.window.prev_period))}` : '';
        const schedule = monthly.previewMode
            ? ''
            : (st.enabled ? `定时每 ${st.check_minutes} 分钟检查` : '定时未启用');
        box.hidden = false;
        box.innerHTML = `
            <div class="dp-mrun-head">
                <div class="dp-mrun-period">${esc(rangeLabel(r.date_from, r.date_to))}${
                    r.trigger === 'schedule' ? '<span class="dp-mrun-flag">定时</span>' : ''}</div>
                <div class="dp-mrun-meta">
                    窗口 ${esc(r.date_from)} ~ ${esc(r.date_to)}${prevText}
                    · 评论 ${num(r.sample)} 条（负面 ${num(r.negative)}）
                    · ${esc(r.engine === 'deepseek' ? 'AI 研判 · DeepSeek' : '统计规则')}
                    ${ticketNote ? '· ' + esc(ticketNote) : ''}
                    ${schedule ? '· ' + esc(schedule) : ''}
                    · 完成于 <span title="完整时间 ${esc(String(r.finished_at || r.created_at || '—'))}">${esc(displayRunTime(r.finished_at || r.created_at))}</span>
                </div>
            </div>
            ${r.summary ? `<div class="dp-mrun-sum">${esc(r.summary)}</div>` : ''}
            ${r.skipped ? `<div class="dp-mrun-foot">
                <button class="btn btn-sm" data-rerun="${esc(r.run_id)}">覆盖重跑这段</button>
            </div>` : ''}`;
    }

    function renderHist() {
        const hist = $('#dpMonthlyHist');
        if (!hist) return;
        const r = monthlyRun();
        if (view.win !== 'custom' || !r) { hist.hidden = true; hist.innerHTML = ''; return; }
        // 部门侧没有历史条：它的结论不落库，拿不出一条"某年某月跑过"的记录。
        // 摆一个空的历史栏比不摆更让人困惑，所以整块收起来。
        const others = monthly.runs.filter(x => x.run_id !== r.run_id || x.period !== r.period);
        hist.hidden = monthly.previewMode || !others.length;
        hist.innerHTML = monthly.previewMode ? '' :
            `<span class="dp-hist-label">历史研判</span>` + monthly.runs.map(x => `
                <button class="dp-hist-item ${x.run_id === r.run_id ? 'on' : ''}" data-run="${esc(x.run_id)}"
                        title="${esc((x.date_from || '') + ' ~ ' + (x.date_to || '') + ' · 评论 '
                            + x.sample + ' 条 · 建单 ' + x.tickets + ' 张 · ' + (x.finished_at || ''))}">
                    ${esc(x.period)}<i>${x.tickets ? x.tickets + ' 单' : (x.status === 'failed' ? '失败' : '无单')}</i>
                </button>`).join('');
    }


    /* 卡头与控件随窗口切换：近 30 天不需要区间选择器，自选时段不需要"重新研判"
       （那颗按钮是"重算实时口径"，自选时段的等价物是「立即研判」）。 */
    function renderShell() {
        const st = monthly.settings;
        const custom = view.win === 'custom';

        $$('#dpWinTabs .chip').forEach(c => c.classList.toggle('on', c.dataset.win === view.win));
        const range = $('#dpRange');
        if (range) range.hidden = !custom;
        // 018 两颗研判按钮严格二选一：近 30 天只露「重新研判」（重算实时口径），
        // 自选时段只露「立即研判」（研判一次并存档，且要有权限）。
        // 模板里两颗都先 hidden，避免脚本执行前闪一下两颗并列、卡头堆两层控件。
        const refresh = $('#dpRefresh');
        const showRefresh = !custom;
        const showRun = custom && !!monthly.canRun;
        if (refresh) refresh.hidden = !showRefresh;

        // 换数据集要重算默认区间：不同数据集的覆盖范围不一样
        if (st && monthly.rangeFor !== state.dataset_id) {
            setRange(rangeForDataset());
            monthly.rangeFor = state.dataset_id;
        }
        const hint = $('#dpRangeHint');
        if (hint) {
            const meta = state.datasetMeta;
            hint.textContent = (meta && meta.time_start)
                ? `数据集覆盖 ${meta.time_start} ~ ${meta.time_end}` : '';
        }

        const sub = $('#dpRiskSub');
        if (sub) {
            if (custom) {
                sub.textContent = monthly.previewMode
                    ? `选一段时间，只看归口「${scopeName()}」的主题；结论即时生成，不写全校研判记录`
                    : '选一段时间把这批评论单独研判一遍 · 结论存档 · 要不要建单由人决定';
            } else {
                const a = view.risk && view.risk.anchor;
                // 部门账号的页面标题已经写着「后勤保障处 · 风险研判」，
                // 副标题再说一遍"仅统计归口…"是同一句话说两次。
                // 「当前数据集没有可用日期」是**请求成功但没有锚点**时的结论，
                // 请求还没回来（或失败）时不能借这句话——那句"没有日期"读起来像
                // "这批数据是空的"，而真相只是"还在读"。
                sub.textContent = a
                    ? (scoped()
                        ? `锚点 ${a} · 近 30 天对比前 30 天`
                        : `研判锚点 ${a}（数据最后一天）· 近 30 天对比前 30 天 · 看完即弃，不进历史`)
                    : (view.risk
                        ? '当前数据集没有可用日期，换一个数据集或先在「数据采集与导入」里补数据'
                        : (view.loading ? '正在读取研判锚点…' : '研判未完成，可点「重新研判」重试'));
            }
            // 读屏：正在读的时候告诉 AT「这块还在更新」，读完撤掉。
            if (view.loading) sub.setAttribute('aria-busy', 'true');
            else sub.removeAttribute('aria-busy');
        }

        const runBtn = $('#dpMonthlyRun');
        if (runBtn) {
            runBtn.hidden = !showRun;      // 与 #dpRefresh 互斥
            runBtn.disabled = !!monthly.busy;
            runBtn.textContent = monthly.busy ? '研判中…' : '立即研判';
        }
        const note = $('#dpRiskNote');
        if (note) {
            const d = view.risk;
            if (!custom && d && d.notice) { note.classList.remove('is-hidden'); note.textContent = d.notice; }
            else note.classList.add('is-hidden');
        }
    }

    /* 切窗口 = 换一份数据，不是换一张表。屏幕上永远只有一张表、一个答案，
       百分比右上方那行小字始终写着它属于哪一段。 */
    function renderWindow() {
        renderShell();
        renderSnap();
        renderWeek();           // 周带在表上方，先画它：这是这一屏的"多急"
        renderTable();
        renderDetail();
        placeDetail();          // 详情要跟着选中的行走，不是回落到表尾
        renderHist();
    }

    function setWin(mode) {
        if (view.win === mode) return;
        view.win = mode;
        view.current = null;
        view.sort = { key: null, dir: 'desc' };   // 换窗口 = 换一份数据，排序归零
        renderWindow();
        syncDimUrl();                             // 另一份数据里没有原来那一行
    }

    /* 页面顶栏没选数据集时，state.dataset_id 是 null，而后端会把"null"读成
     * "不限数据集"——研判记录就会跨数据集混着列出来，和顶栏显示的那个数据集对不上。
     * 这里补一次兜底：用下拉框实际显示的那个（datasets[0]），保持"看到什么就是什么"。 */
    function activeDataset() {
        if (state.dataset_id) return state.dataset_id;
        return (state.datasets && state.datasets[0] && state.datasets[0].dataset_id) || null;
    }

    async function loadMonthly() {
        try {
            const d = await api('/api/risk-runs', { dataset_id: activeDataset() });
            monthly.settings = d.settings;
            monthly.runs = d.runs || [];
            monthly.canRun = !!d.can_run;
            // 能跑不等于能建单。后端把两件事分开给，前端就不用靠"能跑"去猜。
            monthly.canCreate = !!d.can_create;
            // preview：部门账号。跑的是就地研判，没有历史记录、没有建单入口。
            monthly.previewMode = !!d.preview;
            monthly.current = null;
            monthly.preview = null;
            if (monthly.previewMode) {
                renderWindow();
                return;
            }
            if (monthly.runs.length) {
                // 列表不带 topics（一页十几条，每条都塞整份快照太重），
                // 单独取最近一期详情；点历史里的其它期也是这条路径。
                const top = monthly.runs[0];
                monthly.current = await api('/api/risk-runs/' + encodeURIComponent(top.run_id));
            }
            renderWindow();
        } catch (e) {
            const box = $('#dpRisk');
            // 只有正看着自选时段时才把错误摊在表格位置；看实时口径时别把表清掉
            if (box && view.win === 'custom') {
                box.innerHTML = `<div class="note note-warn">研判记录加载失败：${esc(e.message)}</div>`;
            } else {
                toast('研判记录加载失败：' + e.message, 'err');
            }
        }
    }

    async function runMonthly(force) {
        if (monthly.busy) return;
        const range = readRange();
        if (!range.from || !range.to) {
            toast('请先选择起止日期（或点上个月 / 近 30 天）', 'err');
            return;
        }
        if (range.from > range.to) {
            toast('开始日期不能晚于结束日期', 'err');
            return;
        }
        monthly.busy = true;
        renderShell();
        const body = $('#dpRisk');
        if (body && view.win === 'custom') {
            body.innerHTML = `<div class="note">正在读取 ${esc(range.from)} ~ ${esc(range.to)}
                的评论并研判，含模型分析时约需 10~30 秒…</div>`;
        }
        try {
            // 两条路只有一个区别：部门侧不落库。前端不该知道这个差别之外的任何事，
            // 所以两种返回被拼成同一个形状（表格只认一套字段）。
            const r = monthly.previewMode
                ? await api('/api/departments/risk-range', null, {
                    method: 'POST',
                    body: JSON.stringify({
                        date_from: range.from, date_to: range.to,
                        dataset_id: activeDataset()
                    })
                })
                : await api('/api/risk-runs/run', null, {
                    method: 'POST',
                    body: JSON.stringify({
                        date_from: range.from, date_to: range.to,
                        dataset_id: activeDataset(), force: !!force
                    })
                });
            if (monthly.previewMode) {
                monthly.preview = r;
            } else {
                monthly.current = r;
                // 重跑会覆盖快照，本地列表也要跟着换掉同一区间那一条
                monthly.runs = [r].concat(monthly.runs.filter(x => x.run_id !== r.run_id
                    && !(x.date_from === r.date_from && x.date_to === r.date_to)));
            }
            view.current = null;
            toast(r.skipped ? `这段已研判：${r.period}`
                : `${r.period} 研判完成：评论 ${r.sample} 条，命中主题 ${
                    (r.topics || []).length} 个`, 'ok');
            renderWindow();
            loadTodo();          // 闭环条与研判可能引用同一批单，刷新一下更稳
        } catch (e) {
            // 区间里没数据是"选错了"，不是后端坏了：把可用范围直接告诉他
            const span = e.detail && e.detail.dataset_span;

            if (span && span.from) {
                toast(`这段没有评论。该数据集可用范围 ${span.from} ~ ${span.to}`, 'err');
            } else {
                toast('研判失败：' + e.message, 'err');
            }
            renderWindow();
        } finally {
            monthly.busy = false;
            renderShell();
        }
    }

    /* 建单：只建被点的那一行。
       不带 dimensions 的批量建单已取消——这里没有门槛，一旦能"全部建单"，
       等于又把"该不该开单"的判断交回给系统。 */
    async function makeMonthTicket(runId, dimension) {
        if (!dimension) return;
        const run = monthlyRun();
        const t = ((run && run.topics) || []).find(x => x.dimension === dimension) || {};
        const note = await askNote({
            title: `建单 · ${dimension}`,
            sub: t.risk != null
                ? `${run.period || ''} 研判 · 风险指数 ${t.risk}（${t.level || '—'}）· `
                  + `负面 ${t.negative}/${t.total} 条`
                : `${run.period || ''} 研判快照`
        });
        if (note === null) return;
        const buttons = /** @type {NodeListOf<HTMLButtonElement>} */
            (document.querySelectorAll('[data-mkmonth]'));
        buttons.forEach(b => { b.disabled = true; });
        try {
            const r = await api(`/api/risk-runs/${encodeURIComponent(runId)}/create-tickets`,
                null, { method: 'POST', body: JSON.stringify({ dimensions: [dimension], note }) });
            const made = (r.created || []).length;
            if (made) {
                toast(`已建单 ${(r.created[0] || {}).code || ''}（${dimension}）`.trim(), 'ok');
            } else {
                // 不是失败，只是这个主题已经有单了：用普通提示，别用红字吓人
                toast(`${dimension} 已经有同类工单了`);
            }
            monthly.current = r;
            renderWindow();
            loadTodo();                 // 闭环条要立刻反映
        } catch (e) {
            toast('建单失败：' + e.message, 'err');
        } finally {
            const again = /** @type {NodeListOf<HTMLButtonElement>} */
                (document.querySelectorAll('[data-mkmonth]'));
            again.forEach(b => { b.disabled = false; });
        }
    }

    /* ---------------- 权重说明 ---------------- */
    function renderWeights() {
        const card = $('#dpWeights');
        const body = $('#dpWeightBody');
        if (!card || !body || !view.risk || !view.risk.weights) return;
        const w = view.risk.weights;
        const rows = [
            ['量级', w['量级'], '该主题负面条数 / 60 条封顶'],
            ['负面率', w['负面率'], '该主题负面占比 / 35% 封顶'],
            // 增速的分母跟着口径走：区间研判比本期/上期，实时研判比近 30 天/前 30 天。
            // 这里只写一种口径，会让另一处的"本期 vs 上期"看起来像没用同一套公式。
            ['增速', w['增速'], '本期负面 / 上期负面 − 1，2 倍封顶（实时口径为近 30 天 / 前 30 天）'],
            ['互动', w['互动'], '负面平均点赞 / 200 封顶']
        ];
        body.innerHTML = `<div class="dp-weight-wrap">
            <table class="table">
                <caption class="sr-only">风险指数构成：权重与算法</caption>
                <thead><tr><th scope="col">构成项</th><th scope="col">权重</th><th scope="col">算法</th></tr></thead>
                <tbody>${rows.map(r => `<tr>
                    <td><b>${esc(r[0])}</b></td><td class="num">${Math.round(r[1] * 100)}%</td>
                    <td>${esc(r[2])}</td></tr>`).join('')}</tbody>
            </table>
            <div class="note note-muted" style="margin-top:8px;">
                合计后乘样本置信系数（该主题 30 条封顶，最少打 55 折）：条数越少，越不敢给高分。
                五项都是可复算的除法，没有任何调参黑箱。
            </div>
        </div>`;
        card.classList.remove('is-hidden');
    }

    /* ---------------- 加载 ---------------- */
    async function loadDepts() {
        const d = await api('/api/departments');
        view.meta = d;
        view.depts = d.departments || [];
        renderDepts();
    }

    /* 部门账号：把"全校口径"的东西收起来。
     * 部门清单只剩本部门一张卡；页面标题换成它真正在做的事。
     * 区间研判**不再收起来**——它是部门自己选时间段看自己那一摊的入口，
     * 只是换成"就地研判、不落库"的口径（见 renderShell）。 */
    function applyScope() {
        if (!scoped()) return;
        const name = scopeName();
        // 页面标题与副标题：模板里写的是全校口径（"按建议建单"部门账号点不动），
        // 到这一屏必须换成它真正能做的事，否则每个动作都在提示"你不该在这"。
        const title = document.querySelector('.page-title');
        if (title) title.textContent = name + ' · 风险研判';
        const psub = document.querySelector('.page-sub');
        if (psub) {
            psub.textContent = '只看归口到本部门的主题：这段时间多急、为什么、下一步做什么；'
                + '处置与闭环在子页「处置工单」里';
        }
        const sub = $('#dpListSub');
        if (sub) sub.textContent = '本部门的职责与关键词 · 只读（部门配置由管理员维护）';
        // 部门名要等研判结果回来才拿得到，副标题里的「只看归口「…」」得再刷一次。
        if (monthly.previewMode && monthly.settings) {
            renderWindow();
        }
    }


    async function loadRisk() {
        if (view.loading) return;
        view.loading = true;
        view.aiPending = false;
        const seq = ++riskSeq;
        const datasetId = state.dataset_id;
        const box = $('#dpRisk');
        const badge = $('#dpEngine');
        if (badge) { badge.textContent = '研判中'; badge.className = 'badge'; }
        if (box && !view.risk) box.innerHTML = '<div class="skeleton" style="height:220px"></div>';
        // 一进请求就切到 loading 态：副标题改口「正在读取研判锚点…」、KPI 变骨架。
        // 不在这里刷一次的话，骨架要等 6 秒后成功返回才出现，中间那段空白
        // 就是第 10 轮测到的「假无数据」。
        renderShell();
        renderKpi();
        try {
            // 先要**规则结果**：同样的风险数字由 SQL 直接算出（毫秒级），
            // AI 只负责重写建议、归口理由和一句总览。让一次 5 秒的模型调用
            // 挡住表格、KPI 与周带，会把整页 LCP 拖到 5 秒以上——而数字本来早就有了。
            const d = await api('/api/departments/risk', { dataset_id: datasetId, ai: 0 });
            if (seq !== riskSeq) return;
            view.risk = d;
            view.aiPending = !!(d.ai_available && (d.topics || []).length);
            if (view.current != null && !(d.topics || [])[view.current]) view.current = null;
            renderWeek();
            renderTable();
            renderDetail();
            placeDetail();
            renderShell();
            renderKpi();
            renderWeights();
            applyScope();
            // 表格铺好之后再看 ?dim=：此时 activeTopics() 才有内容可匹配
            applyDeepDim();
        } catch (e) {
            if (seq !== riskSeq) return;
            if (box) box.innerHTML = `<div class="note note-warn">研判失败：${esc(e.message)}</div>`;
            if (badge) { badge.textContent = '失败'; badge.className = 'badge badge-warn'; }
        } finally {
            if (seq === riskSeq) {
                view.loading = false;
                // 失败时也要收口：把骨架换成「— 研判未完成」，
                // 否则一轮请求失败后 KPI 会永远停在骨架上，比显示 0 更难判断。
                renderShell();
                renderKpi();
                openBootGate();
            }
        }
        // 页面已经可读、可点、可建单；AI 结果回来再做一次原位替换。
        if (seq === riskSeq && view.aiPending) upgradeRisk(seq, datasetId);
    }

    /* AI 补研判：只替换建议、归口理由与总览句，风险数字不动。
       回来时若用户已经切了数据集、切了窗口或重新研判过，按代号丢弃。 */
    async function upgradeRisk(seq, datasetId) {
        try {
            const d = await api('/api/departments/risk', { dataset_id: datasetId });
            if (seq !== riskSeq) return;
            view.risk = d;
            view.aiPending = false;
            if (view.current != null && !(d.topics || [])[view.current]) view.current = null;
            renderWeek();
            renderTable();
            renderDetail();
            placeDetail();
            renderShell();
            renderKpi();
            renderWeights();
        } catch (e) {
            // 网络层失败时保留规则结果：那一版已经是完整可用的答案。
            if (seq !== riskSeq) return;
            view.aiPending = false;
            renderTable();
            renderShell();
        }
    }

    /* ---------------- 部门表单弹窗 ---------------- */
    function openForm(dept) {
        if (!canEdit()) { toast('只有管理员可以维护部门配置', 'err'); return; }
        const isNew = !dept;
        const dims = (view.meta && view.meta.dimensions) || [];
        const chosen = new Set((dept && dept.dimensions) || []);
        const mask = document.createElement('div');
        mask.className = 'modal-mask';
        mask.innerHTML = `<form class="modal dp-form" novalidate>
            <div class="modal-title">${isNew ? '新增部门' : '编辑部门'}</div>
            <p class="dp-form-sub">职责写得越具体，归口判断越准；关键词用于同主题多部门之间的细分。</p>

            <label class="dp-field"><span>部门名称 *</span>
                <input class="input" name="name" maxlength="40" value="${esc(dept ? dept.name : '')}"
                       placeholder="例如：后勤保障处（宿管中心）" required></label>

            <label class="dp-field"><span>主要职责</span>
                <textarea class="input dp-textarea" name="duties" maxlength="500"
                    placeholder="写清管什么：宿舍分配、设施维修、水电计费……">${esc(dept ? dept.duties : '')}</textarea></label>

            <div class="dp-form-row">
                <label class="dp-field"><span>负责人</span>
                    <input class="input" name="owner_name" maxlength="30" value="${esc(dept ? dept.owner_name : '')}"></label>
                <label class="dp-field"><span>职务</span>
                    <input class="input" name="owner_title" maxlength="30" value="${esc(dept ? dept.owner_title : '')}"></label>
            </div>
            <div class="dp-form-row">
                <label class="dp-field"><span>联系方式</span>
                    <input class="input" name="contact" maxlength="60" value="${esc(dept ? dept.contact : '')}"
                           placeholder="办公电话 / 邮箱"></label>
                <label class="dp-field"><span>期望响应（工作小时）</span>
                    <input class="input" name="sla_hours" type="number" min="1" max="240"
                           value="${dept && dept.sla_hours ? dept.sla_hours : 48}"></label>
            </div>

            <div class="dp-field"><span>负责的舆情主题（可多选）</span>
                <div class="dp-dim-pick">
                    ${dims.map(x => `<label class="dp-check">
                        <input type="checkbox" value="${esc(x)}" ${chosen.has(x) ? 'checked' : ''}>
                        <span>${esc(x)}</span></label>`).join('')}
                </div>
            </div>

            <label class="dp-field"><span>职责关键词（逗号分隔）</span>
                <input class="input" name="keywords" value="${esc(dept ? (dept.keywords || []).join('，') : '')}"
                       placeholder="宿舍，空调，热水，电费"></label>

            <p class="dp-err" hidden></p>
            <div class="dp-form-acts">
                <button type="button" class="btn" data-act="cancel">取消</button>
                <button type="submit" class="btn btn-primary">${isNew ? '创建' : '保存'}</button>
            </div>
        </form>`;
        document.body.appendChild(mask);
        const form = mask.querySelector('form');
        const errBox = /** @type {HTMLElement} */ (mask.querySelector('.dp-err'));
        // 关闭要连键盘监听一起收掉：只 remove 节点的话，每开一次弹窗就留一个
        // 永不释放的 keydown 监听，攒几次后按 Esc 会连着触发一串 close。
        function onEsc(e) {
            if (e.key === 'Escape') close();
        }
        function close() {
            mask.remove();
            document.removeEventListener('keydown', onEsc);
        }

        mask.addEventListener('click', e => { if (e.target === mask) close(); });
        document.addEventListener('keydown', onEsc);
        // 「取消」是 type="button"，不会触发 submit，必须自己绑。
        // 以前只绑了遮罩点击和 Esc，点这个按钮一点反应都没有。
        const cancelBtn = mask.querySelector('[data-act="cancel"]');
        if (cancelBtn) cancelBtn.addEventListener('click', close);

        form.addEventListener('submit', async e => {
            e.preventDefault();
            const fd = new FormData(form);
            const body = {
                name: (fd.get('name') || '').toString().trim(),
                duties: (fd.get('duties') || '').toString().trim(),
                owner_name: (fd.get('owner_name') || '').toString().trim(),
                owner_title: (fd.get('owner_title') || '').toString().trim(),
                contact: (fd.get('contact') || '').toString().trim(),
                sla_hours: Number(fd.get('sla_hours')) || 0,
                dimensions: [.../** @type {NodeListOf<HTMLInputElement>} */
                    (form.querySelectorAll('.dp-dim-pick input:checked'))].map(x => x.value),
                keywords: (fd.get('keywords') || '').toString()
                    .split(/[,，、\s]+/).map(s => s.trim()).filter(Boolean)
            };
            if (!body.name) {
                errBox.textContent = '请填写部门名称';
                errBox.hidden = false;
                return;
            }
            try {
                if (isNew) await api('/api/departments', body, { method: 'POST' });
                else await api('/api/departments/' + dept.dept_id, body, { method: 'PATCH' });
                close();
                toast(isNew ? '部门已创建' : '部门已保存', 'ok');
                await loadDepts();
                loadRisk();
            } catch (e2) {
                errBox.textContent = e2.message;
                errBox.hidden = false;
            }
        });
    }

    /* ---------------- 事件 ---------------- */
    async function archiveDept(id) {
        const d = view.depts.find(x => x.dept_id === id);
        if (!d) return;
        // 以前这里是浏览器原生 confirm：同一个产品里，"删除工单"是定制弹窗、
        // "归档部门"是个灰盒子，看起来像是两个不同严重程度的事。统一成一套。
        const ok = await CP.confirmModal({
            title: `归档「${d.name}」？`,
            subHtml: '归档后该部门不再参与归口判断，<b>历史工单不受影响</b>——' +
                '工单里存的是部门名称快照，审计里仍能看到它。',
            note: '这是软删除：记录留在库里，只是从归口候选里消失。需要时可以重新建一个同名部门。',
            okLabel: '确认归档'
        });
        if (!ok) return;
        try {
            await api('/api/departments/' + id, null, { method: 'DELETE' });
            toast('已归档', 'ok');
            await loadDepts();
            loadRisk();
        } catch (e) {
            toast('归档失败：' + e.message, 'err');
        }
    }

    /* 建单前问一句备注：可以留空，限 200 字。返回 Promise<string|null>，
       null = 取消。备注进工单摘要末尾并标明"建单人"，这样单里哪句是统计事实、
       哪句是同事现场补的，一眼能分清。

       确认页要交代"派给谁、多久、建完在哪看"——建单是有外部后果的动作，
       以前只问一句备注，按下去之后人就失联了。这段交代必须写成模板里的正文，
       不能用 `{# … #}` 记：这是 JS 模板字面量，没有 Jinja 解析，注释会原样
       显示在「确认建单」上方（第 11 轮实测）。`verify_ui.py` 有一条断言盯着它。 */
    function askNote(opt) {
        return new Promise(resolve => {
            const mask = document.createElement('div');
            mask.className = 'modal-mask';
            mask.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="dpNoteTitle">
                <div class="modal-title" id="dpNoteTitle">${esc(opt.title || '建单')}</div>
                <div class="card-sub">${esc(opt.sub || '')}</div>
                <div class="note note-muted" style="margin:8px 0 4px;">
                    <span>${esc(opt.dest || '建单后可在「处置工单」里查看进度与时限。')}</span>
                </div>
                <label class="filter-label" style="margin-top:12px;">备注（可选，会写进工单摘要）</label>
                <textarea class="input" id="dpNote" rows="3" maxlength="200"
                    style="width:100%;margin:6px 0 10px;resize:vertical;"
                    placeholder="例：已和宿管确认三层热水器故障；本周校运会期间走访；联系人 王老师"></textarea>
                <div class="tk-modal-foot">
                    <button class="btn btn-sm btn-ghost" data-cancel type="button">取消</button>
                    <button class="btn btn-sm btn-primary" data-ok type="button">确认建单</button>
                </div>
            </div>`;
            document.body.appendChild(mask);
            const ta = /** @type {HTMLTextAreaElement} */ (mask.querySelector('#dpNote'));
            // 走 openModal/closeModal 而不是自己挂节点：Esc 关闭、Tab 在弹窗内循环、
            // 关掉后焦点回到触发建单的那颗按钮——这三件"删除工单"弹窗都有，
            // 这个弹窗以前三件全没有。
            const done = v => { CP.closeModal(mask); mask.remove(); resolve(v); };
            CP.openModal(mask, { onEsc: () => done(null) });
            mask.addEventListener('click', e => { if (e.target === mask) done(null); });
            /** @type {any} */ (mask.querySelector('[data-cancel]'))
                .addEventListener('click', () => done(null));
            /** @type {any} */ (mask.querySelector('[data-ok]'))
                .addEventListener('click', () => done((ta.value || '').trim()));
        });
    }

    async function makeTicket(i) {
        const t = (view.risk && view.risk.topics || [])[i];
        if (!t) return;
        const dept = t.department || {};
        const note = await askNote({
            title: `建单 · ${t.dimension}`,
            sub: `风险指数 ${t.risk}（${t.level}）· 负面 ${t.negative}/${t.total} 条 · `
                + (dept.name
                    ? `归口 ${dept.name}（负责人 ${dept.owner_name || '未填'}）`
                    : '未归口 —— 建单后会进入「待分派」，需要人工指派'),
            dest: dept.name
                // 「部门时限」与「工单首次响应」是两把尺子：部门这一格配的是
                // 期望响应（48 / 24 工作小时，按归口部门），单张工单的首次响应
                // 按优先级取（紧急 1 / 高 4 / 中 7 / 低 21 工作小时）。以前这里写
                // 「48 工作小时内首次响应」，建完单详情立刻写「首次响应要求 4 个工作小时」——
                // 同一个流程里两个数，用户只能怀疑自己点错了。两个名词分开说。
                ? `将派给 ${dept.name}${dept.owner_name ? ' ' + dept.owner_name : ''}`
                  + `${dept.sla_hours ? ' · 部门时限 ' + dept.sla_hours + ' 工作小时' : ''}`
                  + ' · 建单后在「处置工单」查看进度（首次响应按工单优先级计时）'
                : '未归口：建单后进入「待分派」池，需要有人指派责任部门 · 在「处置工单」查看'
        });
        if (note === null) return;          // 取消：什么都不做，按钮保持原样
        const btn = /** @type {HTMLButtonElement|null} */ (
            document.querySelector(`[data-mklive="${i}"]`)) ||
            /** @type {HTMLButtonElement|null} */ (document.querySelector(`[data-mk="${i}"]`));
        if (btn) { btn.disabled = true; btn.textContent = '建单中…'; }
        try {
            // 把用户看到的建议原样带过去：服务端只校验、不重写，
            // 否则"点建单 → 单里是另一套话"，人会怀疑自己点错了。
            // 注意载荷必须走 body：api() 的第二个参数是 query 串，
            // 只塞在那里的话 request.get_json() 拿不到，后端会报"缺少主题"。
            const tk = await api('/api/departments/create-ticket', null, {
                method: 'POST',
                body: JSON.stringify({
                    dimension: t.dimension,
                    dataset_id: state.dataset_id,
                    department_id: (t.department || {}).dept_id || null,
                    advice: t.advice || [],
                    note: note
                })
            });
            toast(`已建单 ${tk.code}，可到「处置工单」分派`, 'ok');
            // 单号写回这一行：这一列的用途就是"这个主题有没有人在管"，
            // 建完还显示按钮等于让人以为自己没点到。
            t.ticket_code = tk.code;
            if (view.win === 'live') renderTable();
            loadTodo();          // 在办 +1 要立刻反映到上面的闭环条，不然会以为没建成功
        } catch (e) {
            // 409 带着已有单号：刷新后行内那一格又会回到「建单」，
            // 用户再点一次才被告知"已有一单"——把单号当场填回去，别让他重试一次。
            const dup = e.detail && e.detail.dup;
            if (dup && dup.code) {
                t.ticket_code = dup.code;
                if (view.win === 'live') renderTable();
                toast(`这个主题已有工单 ${dup.code}，已标在行内`, 'err');
                return;
            }
            toast('建单失败：' + e.message, 'err');
            if (btn) {
                btn.disabled = false;
                btn.textContent = (!t.department) ? '建单（待分派）' : (view.win === 'live' ? '建单' : '按建议建单');
            }
        }
    }

    function bind() {
        const box = $('#dpRisk');
        if (box) {
            // 行内按钮排在行点击之前：先让按钮把自己的事做完，
            // 否则点「建单」会连带展开那一行，看起来像误触。
            box.addEventListener('click', e => {
                const t = /** @type {HTMLElement} */ (e.target);
                const sortEl = t.closest('[data-sort]');
                if (sortEl) { toggleSort(sortEl.getAttribute('data-sort')); return; }
                const rf = t.closest('[data-rf]');
                if (rf) { setFilter(rf.getAttribute('data-rf')); return; }
                const rr = t.closest('[data-rerun]');
                if (rr) { runMonthly(true); return; }
                const mk = t.closest('[data-mklive]');
                if (mk) { makeTicket(Number(mk.getAttribute('data-mklive'))); return; }
                const mm = t.closest('[data-mkmonth]');
                if (mm) {
                    makeMonthTicket(mm.getAttribute('data-mkmonth'), mm.getAttribute('data-dim'));
                    return;
                }
                const tr = t.closest('tr[data-row]');
                if (tr) selectRow(Number(tr.getAttribute('data-row')));
            });
            // 点一行看风险拆解是这页的主操作，只能鼠标点等于对键盘用户关掉了。
            // tr 不能改成 role=button（会破坏表格语义），用 tabindex + 回车/空格。
            // Spacebar 是老浏览器/旧输入法给的旧键名，一并认下——否则"回车行、空格不行"，
            // 键盘用户只会以为这一行点不动。
            box.addEventListener('keydown', e => {
                if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
                const sortEl = /** @type {HTMLElement} */ (e.target).closest('[data-sort]');
                if (sortEl) { e.preventDefault(); toggleSort(sortEl.getAttribute('data-sort')); return; }
                const tr = /** @type {HTMLElement} */ (e.target).closest('tr[data-row]');
                if (!tr || /** @type {HTMLElement} */ (e.target).closest('button')) return;
                e.preventDefault();
                selectRow(Number(tr.getAttribute('data-row')));
            });
        }

        // 周带上的主题点一下就选中它，和点表格里那一行是同一件事：
        // 带子上只有"多急、归谁"，四段构成与证据在下面的详情里。
        // 元素是 <button>，回车与空格由浏览器转成 click，不用再补键盘分支。
        const wk = $('#dpWeek');
        if (wk) {
            wk.addEventListener('click', e => {
                const btn = /** @type {HTMLElement} */ (e.target).closest('.wk-pin');
                if (!btn) return;
                selectRow(Number(btn.getAttribute('data-row')));
                $$('#dpWeek .wk-pin').forEach(b => b.setAttribute('aria-pressed',
                    Number(b.getAttribute('data-row')) === view.current ? 'true' : 'false'));
                // 选中的话焦点跟到详情；再点一次是收起，焦点留在原地不动
                if (view.current != null) focusDetail();
            });
        }

        const tabs = $('#dpWinTabs');
        if (tabs) {
            tabs.addEventListener('click', e => {
                const b = /** @type {HTMLElement} */ (e.target).closest('[data-win]');
                if (b) setWin(b.getAttribute('data-win'));
            });
        }

        const list = $('#dpList');
        if (list) {
            list.addEventListener('click', e => {
                const ed = /** @type {HTMLElement} */ (e.target).closest('[data-edit]');
                if (ed) {
                    const d = view.depts.find(x => x.dept_id === ed.getAttribute('data-edit'));
                    if (d) openForm(d);
                    return;
                }
                const ar = /** @type {HTMLElement} */ (e.target).closest('[data-arch]');
                if (ar) { archiveDept(ar.getAttribute('data-arch')); return; }
                // 点卡片正文展开职责全文；点按钮不触发（按钮有自己的动作）
                const card = /** @type {HTMLElement} */ (e.target).closest('.dp-card');
                if (card) card.classList.toggle('open');
            });
        }

        const detail = $('#dpDetail');
        if (detail) {
            detail.addEventListener('click', e => {
                const t = /** @type {HTMLElement} */ (e.target);
                const bw = t.closest('[data-backweek]');
                if (bw) { backToWeek(); return; }
                const mk = t.closest('[data-mk]');
                if (mk) { makeTicket(Number(mk.getAttribute('data-mk'))); return; }
                // 详情面板现在两种窗口共用，建单入口也跟着共用这一处委托。
                const mm = t.closest('[data-mkmonth]');
                if (mm) makeMonthTicket(mm.getAttribute('data-mkmonth'), mm.getAttribute('data-dim'));
            });
        }

        const nw = $('#dpNew');
        if (nw) nw.addEventListener('click', () => openForm(null));
        const rf = $('#dpRefresh');
        if (rf) rf.addEventListener('click', () => {
            view.risk = null;
            view.current = null;
            const d = $('#dpDetail');
            if (d) d.innerHTML = '';
            loadRisk();
        });

        /* 权重说明默认收起，点标题展开。
         * 它是"被问到时能当场翻开"的东西，不是每屏都要读的表：
         * 一直铺在页面底部，会让人以为这一页的重点是算法而不是"该谁管"。 */
        const wh = $('#dpWeightsHead');
        if (wh) {
            const tip = $('#dpWeightsTip');
            const toggle = () => {
                const body = $('#dpWeightBody');
                if (!body) return;
                const open = body.hidden;
                body.hidden = !open;
                // aria-expanded 挂在真正的按钮上：卡头本身已经不是按钮了
                if (tip) {
                    tip.setAttribute('aria-expanded', open ? 'true' : 'false');
                    tip.textContent = open ? '收起' : '展开看算法';
                }
                wh.classList.toggle('open', open);
            };
            // 整块卡头仍然可点（鼠标习惯），键盘只走右边那颗按钮——
            // 再给卡头绑 keydown 会让空格触发两次（按钮自身 + 委托）。
            wh.addEventListener('click', toggle);
        }

        /* ---- 区间研判 ---- */
        const mrun = $('#dpMonthlyRun');
        if (mrun) mrun.addEventListener('click', () => runMonthly(false));

        // 快捷区间：只填日期不自动跑——先让人看清"要到哪一段"再决定点不点
        const quick = $('#dpRange');
        if (quick) {
            quick.addEventListener('click', e => {
                const b = /** @type {HTMLElement} */ (e.target).closest('[data-quick]');
                if (!b) return;
                const kind = b.getAttribute('data-quick');
                if (kind === 'last-month') {
                    setRange(rangeForDataset());
                } else {
                    // "近 N 天"按数据集最后一天往前推：数据集没覆盖到今天时，
                    // 按今天算出来的区间会整段落在数据外面（点了必然报"没有评论"）。
                    const end = (state.datasetMeta && state.datasetMeta.time_end)
                        ? Math.min(Date.parse(state.datasetMeta.time_end + 'T00:00:00'), Date.now())
                        : Date.now();
                    const to = new Date(end);
                    setRange({
                        from: isoDate(shiftDays(to, -(Number(kind) - 1))),
                        to: isoDate(to)
                    });
                }
            });
        }

        const mhist = $('#dpMonthlyHist');
        if (mhist) mhist.addEventListener('click', async e => {
            const b = /** @type {HTMLElement} */ (e.target).closest('[data-run]');
            if (!b) return;
            const id = b.getAttribute('data-run');
            if (monthly.current && monthly.current.run_id === id) return;
            try {
                monthly.current = await api('/api/risk-runs/' + encodeURIComponent(id));
                view.current = null;
                renderWindow();
            } catch (err) {
                toast('加载研判记录失败：' + err.message, 'err');
            }
        });

        // 换数据集要重新研判：风险与归口都是"在这批数据里"的判断
        document.addEventListener('cp:dataset', () => {
            view.risk = null;
            view.current = null;
            const d = $('#dpDetail');
            if (d) d.innerHTML = '';
            loadRisk();
            loadMonthly();
        });

        // core 初始化完（数据集的默认值定下来、身份就位）再拉一次。
        // boot() 有可能跑在 core.init() 之前，那时 state.dataset_id 还是 null：
        // 研判记录会退化成"不限数据集"、风险表会退化成后端默认数据集，
        // 两者都与顶栏显示的那个数据集对不上——而且看起来完全正常，最难发现。
        document.addEventListener('cp:ready', () => {
            view.risk = null;
            view.current = null;
            const d = $('#dpDetail');
            if (d) d.innerHTML = '';
            loadMonthly();
            loadRisk();
        });
    }

    /* 动态区一次成型：首次研判渲染完再显示，避免整页被周带/KPI 向下推一次。
       兜底 2.5 秒：接口挂起或脚本提前出错时也要把页面交还给读者。 */
    function openBootGate() {
        const el = $('#dpBoot');
        if (!el || !el.classList.contains('dp-boot')) return;
        el.classList.remove('dp-boot');
        el.removeAttribute('aria-busy');
    }
    setTimeout(openBootGate, 2500);

    async function boot() {
        bind();
        loadTodo();
        loadMonthly();
        try {
            await loadDepts();
        } catch (e) {
            toast('部门加载失败：' + e.message, 'err');
        }
        renderKpi();
        await loadRisk();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
