/* PAGE-02 AI 舆情助手 */
/**
 * 这个页面是整套前端里最复杂的一块：SSE 流式 + 工具步骤实时点亮 + 引用核验角标。
 * 类型信息集中放在下面这几个 typedef 里，函数签名只引用名字，避免注释淹掉逻辑。
 *
 * @typedef {Object} RunEvent
 * @property {'stage'|'plan'|'tool_start'|'tool_done'|'token'|'rag_done'|'verdict'|'answer_reset'|'done'|'error'} type
 * @property {string} [stage]    stage 事件：parse / think / retrieve / compose / verify / guard
 * @property {string} [label]    阶段或工具的人类可读名称
 * @property {string} [detail]   阶段补充说明（耗时、次数等）
 * @property {string} [tool]     工具名
 * @property {bool} [ok]
 * @property {number} [elapsed_ms]
 * @property {string} [text]     token 事件：增量文本
 * @property {Object} [payload]  done 事件：完整响应（结构同 renderResult 的入参）
 * @property {string} [message]  error 事件：错误文案
 *
 * @typedef {Object} RunPanel
 * @property {(ev: RunEvent) => void} stage
 * @property {(ev: RunEvent) => void} plan
 * @property {(ev: RunEvent) => void} toolStart
 * @property {(ev: RunEvent) => void} toolDone
 * @property {(ev: RunEvent) => void} token
 * @property {(ev: RunEvent) => void} ragDone
 * @property {(ev: RunEvent) => void} verdict
 * @property {(ev: RunEvent) => void} answerReset
 * @property {() => void} finish
 */
(function () {
    'use strict';
    const { $, $$, esc, num, api, state, toast, setState } = CP;

    /* 左栏只留三条。右侧空态已经给了三个入口，两处各列六条等于九个选项——
       而这一屏真正该做的判断只有一个：问什么。 */
    const RECS = [
        '正面和负面评论各有多少？',
        '比较抖音和小红书对宿舍住宿的评价差异',
        '招生报考讨论里，学生和家长最在意什么？'
    ];

    let history = [];      // 多轮上下文（只保留最近几轮，P1 能力）
    let lastQuestion = '';
    let busy = false;
    let voice = null;      // 语音输入句柄（S3-a）
    let lastPayload = null;   // 保留最近一轮结果，切主题时用它重画（不重新问一遍）
    let guardCfg = { max_rounds: 20, max_question_chars: 200 };   // 由 /api/guard/catalog 拉取

    /* ---------------- 语音输入 ---------------- */
    function mountVoice() {
        if (!window.CPVoice) return;
        voice = window.CPVoice.mount({
            input: $('#qInput'),
            button: $('#btnVoice'),
            hint: $('#composerHint'),
            onToast: toast,
            isBusy: () => busy
        });
    }

    /* ---------------- 左侧对话 ---------------- */
    function pushMsg(role, text, isHtml) {
        const log = $('#chatLog');
        const div = document.createElement('div');
        div.className = 'msg ' + role;
        if (isHtml) div.innerHTML = text; else div.textContent = text;
        log.appendChild(div);
        log.scrollTop = log.scrollHeight;
        return div;
    }

    function renderRecs() {
        $('#recList').innerHTML = RECS.map(q =>
            `<button class="rec-chip" data-q="${esc(q)}">${esc(q)}</button>`).join('');
        $$('#recList .rec-chip').forEach(b => b.addEventListener('click', () => {
            $('#qInput').value = b.dataset.q;
            send();
        }));
    }

    /* ---------------- 右侧结果 ---------------- */
    function metricChips(metrics) {
        if (!metrics || !metrics.length) return '';
        return `<div class="metrics-row">${metrics.map(m => `
            <div class="metric">
                <div class="metric-name">${esc(m.name)}</div>
                <div class="metric-value">${esc(String(m.value))}<span class="metric-unit">${esc(m.unit || '')}</span></div>
            </div>`).join('')}</div>`;
    }

    /**
     * 工具调用步骤卡片。steps 里带 channel 字段，
     * 走 MCP 协议的那几步会多一个角标 —— 让"支持 MCP"在界面上可见。
     * @param {CPToolStep[]} steps
     * @returns {string}
     */
    function stepsBlock(steps) {
        if (!steps || !steps.length) return '';
        return `<div class="card">
            <div class="card-head">
                <div><div class="card-title" role="heading" aria-level="3">工具调用过程</div>
                <div class="card-sub">以下步骤由后端记录，反映模型真实调用与执行耗时</div></div>
                <div class="card-tools"><span class="badge">${steps.length} 次调用</span></div>
            </div>
            ${steps.map(s => `
            <div class="step ${s.status === 'failed' ? 'failed' : ''}">
                <div class="step-idx">${s.index}</div>
                <div class="step-body">
                    <div class="step-title">
                        ${esc(s.label)}
                        <code class="step-tool">${esc(s.tool)}</code>
                        <span class="badge ${s.status === 'failed' ? 'badge-neg' : s.empty ? 'badge-warn' : 'badge-pos'}">
                            ${s.status === 'failed' ? '失败' : s.empty ? '无数据' : '完成'}
                        </span>
                        ${s.channel === 'mcp'
                            ? '<span class="badge badge-brand" title="本次调用经 MCP 协议（stdio）转发到工具服务端">MCP</span>'
                            : ''}
                        <span class="step-tool">${s.elapsed_ms}ms</span>
                    </div>
                    <div class="step-summary">${esc(s.summary || '')}</div>
                    <div>${argChips(s.args)}</div>
                </div>
            </div>`).join('')}
        </div>`;
    }

    function argChips(args) {
        if (!args) return '';
        return Object.keys(args).map(k => {
            let v = args[k];
            if (Array.isArray(v)) v = v.join(',');
            if (v === null || v === undefined || v === '') return '';
            return `<span class="arg-chip">${esc(k)}=${esc(String(v))}</span>`;
        }).join('');
    }

    /* 回答文本：去掉残留 markdown，把 [CMT-xxxx] 渲染成可点芯片（点开就是原文证据） */
    const REF_RE = /\[\s*(CMT-[0-9A-Za-z]+)\s*\]/g;
    let refSeq = 0;
    function mdText(s) {
        const raw = String(s || '').replace(/\*\*/g, '').replace(/`/g, '');
        // 句子里塞 12 位机器码（CMT-05D55FF542）会把一句话切成三段，读的人得跳过去。
        // 改成上标序号 [1][2]，完整 ID 放进 title 与 aria-label，点开就是那条原文。
        // 序号每次渲染从 1 开始——mdText 每次拿到的都是完整答案，流式重绘也不会错位。
        refSeq = 0;
        // 用 <button> 而不是 <a>：<a> 没有 href 就不可聚焦，键盘用户 Tab 一辈子上不去，
        // "每句结论都能点开原文"对他们等于不存在。样式在 CSS 里按 .ref-chip 挂着，不用改。
        return esc(raw).replace(REF_RE, (m, ref) => {
            refSeq += 1;
            return `<button type="button" class="ref-chip" data-ref="${ref}"`
                + ` title="第 ${refSeq} 条引用 · ${ref}"`
                + ` aria-label="查看第 ${refSeq} 条引用（${ref}）">${refSeq}</button>`;
        });
    }
    function bindRefs(root) {
        $$('.ref-chip', root || document).forEach(el => {
            if (el.dataset.bound) return;
            el.dataset.bound = '1';
            el.addEventListener('click', e => {
                e.preventDefault();
                openEvidence(el.dataset.ref);
            });
        });
    }

    function evidenceBlock(items) {
        if (!items || !items.length) return '';
        return `<div class="card">
            <div class="card-head">
                <div><div class="card-title" role="heading" aria-level="3">评论证据</div>
                <div class="card-sub">已脱敏，仅保留内部引用 ID 与必要文本；点击查看详情</div></div>
                <div class="card-tools"><span class="badge badge-brand">${items.length} 条</span></div>
            </div>
            ${items.map(e => `
            <div class="evidence-item" data-ref="${esc(e.comment_ref)}">
                <div class="ev-snippet">${esc(e.snippet)}</div>
                <div class="ev-meta">
                    <span class="badge badge-${e.sentiment === 'positive' ? 'pos' : e.sentiment === 'negative' ? 'neg' : 'neu'}">${esc(e.sentiment_label || '')}</span>
                    <span>${esc(e.platform)}</span><span>${esc(e.date)}</span>
                    ${e.dimension ? `<span>${esc(e.dimension)}</span>` : ''}
                    ${e.like_count != null ? `<span>${num(e.like_count)} 赞</span>` : ''}
                    <span class="ev-ref" style="margin-left:auto">${esc(e.comment_ref)}</span>
                </div>
            </div>`).join('')}
        </div>`;
    }

    /* RAG 检索片段：把"为什么这么答"的原文依据摊开，每条都能点开回跳 */
    /* ---------------- 引用核验（M4） ---------------- */
    /* 角标文案由后端 verdict 决定，前端不推断状态：
       同一个 verdict 在角标、明细、日志里必须说同一句话，否则三处各说一套，没人信。 */
    const VERDICT_STYLE = {
        pass: { cls: 'badge-pos', mark: '✓' },
        revised: { cls: 'badge-pos', mark: '✓' },
        stripped: { cls: 'badge-warn', mark: '⚠' },
        no_anchor: { cls: 'badge-warn', mark: '⚠' },
        flagged: { cls: 'badge-warn', mark: '⚠' },
        unverified: { cls: '', mark: '—' },
    };
    const STATUS_STYLE = { valid: 'badge-pos', invalid: 'badge-warn', unknown: 'badge-neg' };

    function citationText(c) {
        const v = c.verdict;
        if (v === 'pass') return `引用 ${c.valid}/${c.total} 已核验`;
        if (v === 'revised') return `已自动修正 ${c.revised_count || 0} 处引用`;
        if (v === 'stripped') {
            const n = (c.removed || []).length || ((c.invalid || []).length + (c.unknown || []).length);
            return `${n} 处引用无法溯源，已移除`;
        }
        if (v === 'no_anchor') return '结论未标注原文锚点';
        if (v === 'flagged') return `引用需复核 ${c.valid}/${c.total}`;
        return '引用未核验';
    }

    function citationBadge(c) {
        if (!c || !c.verdict) return '';
        const st = VERDICT_STYLE[c.verdict] || VERDICT_STYLE.unverified;
        return `<span class="badge ${st.cls}" title="${esc(c.note || '')}">${st.mark} ${esc(citationText(c))}</span>`;
    }

    function citationBlock(c) {
        if (!c || !c.verdict) return '';
        const detail = c.detail || [];
        const removed = new Set(c.removed || []);
        const rows = detail.map(d => `
            <div class="cv-row">
                <span class="cv-ref">${esc(d.ref)}</span>
                <span class="badge ${STATUS_STYLE[d.status] || ''}">${esc(d.status_label || d.status)}</span>
                ${removed.has(d.ref) ? '<span class="badge badge-warn">已移除</span>' : ''}
                <span class="cv-snip">${esc(d.snippet || '（无原文可展示）')}</span>
            </div>`).join('');
        // 在重写稿里才出现的问题编号不会进 detail（detail 反映的是最终文本），
        // 但"删掉了什么"必须能看见，否则这句话就变成了静默丢弃。
        const extra = [...removed].filter(r => !detail.some(d => d.ref === r)).map(r => `
            <div class="cv-row">
                <span class="cv-ref">${esc(r)}</span>
                <span class="badge badge-warn">无法溯源</span>
                <span class="badge badge-warn">已移除</span>
                <span class="cv-snip">该编号不在本次检索结果中，已从回答正文里删去。</span>
            </div>`).join('');
        const body = (rows || extra)
            ? `<details class="cv-details" ${c.verdict === 'stripped' ? 'open' : ''}>
                   <summary>核验明细（${detail.length + (removed.size - detail.filter(d => removed.has(d.ref)).length)} 处）</summary>
                   ${rows}${extra}
               </details>`
            : '';
        return `<div class="card">
            <div class="card-head">
                <div><div class="card-title" role="heading" aria-level="3">引用核验</div>
                <div class="card-sub">${esc(c.note || '')}</div></div>
                <div class="card-tools">${citationBadge(c)}</div>
            </div>
            ${body}
        </div>`;
    }

    function ragBlock(rag, answer) {
        if (!rag || !rag.items || !rag.items.length) return '';
        const cited = new Set((String(answer || '').match(/CMT-[0-9A-Za-z]+/g) || []));
        const c = rag.candidates || {};
        const trace = rag.trace || {};
        const stats = rag.stats || {};
        /* 这一块以前默认铺开，一次回答整页 3695px，而「召回片段 / 候选池 / 融合分」
           是给做检索的人看的，不是给校办老师看的。默认收起来，摘要那一行说人话：
           他在几条评论里挑、挑了几段、其中几段真被写进答案。 */
        return `<div class="card">
            <div class="card-head">
                <div><div class="card-title" role="heading" aria-level="3">检索过程</div>
                <div class="card-sub">在 ${num(c.filtered_docs)} 条候选评论里取出最相关的 ${rag.items.length} 段原文，其中 ${cited.size} 条被写进上面的答案</div></div>
                <div class="card-tools"><span class="badge badge-brand">Top ${rag.items.length}</span>
                    <span class="badge">${num(rag.elapsed_ms)} ms</span></div>
            </div>
            ${rag.filters_relaxed ? `<div class="note note-warn" style="margin-bottom:8px;">结构化筛选命中过少，已放宽到全库检索</div>` : ''}
            <details class="rag-details">
            <summary>展开这 ${rag.items.length} 段原文与检索参数</summary>
            <div class="note note-muted" style="margin:8px 0;">${esc(trace.fusion || '混合检索')} · 词项 ${num(c.bm25)} / 同义扩展 ${num(c.expand)} / 向量 ${num(c.vector)} → 候选 ${num(c.filtered_docs)} 条</div>
            ${rag.items.map(it => `
            <div class="evidence-item" data-ref="${esc(it.comment_ref)}">
                <div class="ev-snippet">${esc(it.snippet)}</div>
                <div class="ev-meta">
                    <span class="badge">#${it.rank}</span>
                    <span class="badge badge-${it.sentiment === 'positive' ? 'pos' : it.sentiment === 'negative' ? 'neg' : 'neu'}">${esc(it.sentiment_label || '')}</span>
                    <span>${esc(it.platform)}</span><span>${esc(it.date)}</span>
                    ${it.dimension ? `<span>${esc(it.dimension)}</span>` : ''}
                    <span class="arg-chip">融合 ${esc(String(it.score))}</span>
                    <span class="arg-chip">BM25 ${esc(String(it.bm25))}</span>
                    <span class="arg-chip">向量 ${esc(String(it.vector))}</span>
                    ${cited.has(it.comment_ref) ? '<span class="badge badge-pos">已引用</span>' : ''}
                    <span class="ev-ref" style="margin-left:auto">${esc(it.comment_ref)}</span>
                </div>
            </div>`).join('')}
            <div class="note note-muted" style="margin-top:8px;">
                检索语料：${num(stats.docs)} 条有效评论 · 词表 ${num(stats.vocab)} · 平均长度 ${esc(String(stats.avg_doc_len || '—'))} ·
                向量维度 ${num(stats.vector_dim)} · 索引构建 ${num(stats.build_ms)} ms
            </div>
            </details>
        </div>`;
    }

    function carryBlock(r) {
        const c = r.carry;
        if (!c) return '';
        if (!c.applied) {
            return c.note
                ? `<div class="carry-bar"><span class="carry-note">${esc(c.note)}</span></div>`
                : '';
        }
        const chips = (c.chips || [])
            .map(x => `<span class="chip">${esc(x.label)}：${esc(x.value)}</span>`).join('');
        const dsChip = c.dataset_changed
            ? `<span class="chip is-ds" title="舆情总览将切换到本轮所用的数据集">数据集：${esc(c.dataset_id)}</span>`
            : '';
        return `<div class="carry-bar">
            <span class="carry-label">本轮口径</span>
            ${dsChip}${chips}
            <span class="carry-gap"></span>
            <a class="btn btn-sm btn-primary" href="${esc(c.dashboard_url)}">在舆情总览展开这组筛选 →</a>
            <a class="btn btn-sm" href="${esc(c.insights_url)}">去数据洞察</a>
        </div>`;
    }

    function boundsBlock(r) {
        return `<div class="card">
            <div class="card-head">
                <div><div class="card-title" role="heading" aria-level="3">本轮查询口径</div>
                <div class="card-sub">同一口径在舆情总览、洞察页与 Agent 之间保持一致</div></div>
            </div>
            <dl class="kv">
                <dt>数据集</dt><dd>${esc(r.dataset_name || '')}（${esc(r.dataset_id)}）</dd>
                <dt>筛选条件</dt><dd>${esc(r.filters_text || '全部数据')}</dd>
                <dt>统计样本</dt><dd>${num(r.sample_size)} 条有效评论</dd>
                <dt>时间范围</dt><dd>${esc((r.time_span || []).join(' ~ ') || '—')}</dd>
                <dt>入库时间</dt><dd>${esc(String(r.data_version || '—').slice(0, 16))}</dd>
            </dl>
            ${carryBlock(r)}
        </div>`;
    }

    /* 输入侧被拦下的提问：渲染成一张"拒答卡片"，不显示指标/图表/工具步骤——
       因为这些东西根本没产生，画出来就是编 */
    function renderBlocked(r) {
        const b = r.blocked || {};
        $('#resultEmpty').classList.add('is-hidden');
        const box = $('#resultBody');
        box.classList.remove('is-hidden');
        const sgs = (b.suggestions || []).map(q =>
            `<button class="chip" data-suggest="${esc(q)}">${esc(q)}</button>`).join('');
        box.innerHTML = `
        <div class="guard-card">
            <div class="guard-head">
                <span class="guard-icon">!</span>
                <span class="guard-title">这类提问不会被处理：${esc(b.label || '输入未通过安全检查')}</span>
            </div>
            <div class="guard-reply">${esc(r.answer || '')}</div>
            <div class="guard-meta">
                <span>判定：${esc(b.reason || '')}</span>
                <span>引擎：未调用模型（0 次工具调用）</span>
                <span>耗时：${r.duration_ms} ms</span>
                ${r.guard_total != null ? `<span>本库累计拦截：${r.guard_total} 次</span>` : ''}
            </div>
            ${sgs ? `<div class="guard-acts"><span class="guard-acts-label">换个问法：</span>${sgs}</div>` : ''}
        </div>
        <div class="card" style="margin-top:12px;">
            <div class="card-head"><div class="card-title" role="heading" aria-level="3">这道闸是怎么工作的一句话说清</div></div>
            <div class="note note-muted">提问在<b>进入模型之前</b>先过四道检查：长度、频次、意图（注入／越权／套提示词）、
            以及回答出门前的脱敏。命中即拒绝，模型全程没有被调用——所以这里既没有费用也没有数据泄露的可能。</div>
        </div>`;
        const si = $('#sessionInfo');
        if (si) si.textContent = '本轮被输入安全检查拦下 · 未调用模型';
        $$('#resultBody [data-suggest]').forEach(btn => {
            btn.addEventListener('click', () => {
                $('#qInput').value = btn.dataset.suggest;
                $('#qInput').focus();
            });
        });
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    /* 028 单列（≤720）时长对话把结果推到屏外：出结果后给一枚吸底锚，点一下
       滚回结果区。样式组只在窄屏给 .jump-result 视觉，这里也只在窄屏插，
       桌面本来就是双栏、结果就在右侧，不需要一枚多余的按钮。 */
    function mountJumpResult() {
        const stack = $('#resultStack');
        if (!stack) return;
        stack.classList.add('agent-result');
        if (!window.matchMedia('(max-width: 720px)').matches) { removeJumpResult(); return; }
        if (document.querySelector('.jump-result[data-cp-jump]')) return;   // 幂等，不重复建
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'jump-result btn btn-sm';
        btn.setAttribute('data-cp-jump', '1');
        btn.textContent = '↓ 跳到分析结果';
        btn.addEventListener('click', () => {
            const body = $('#resultBody');
            const target = body && !body.classList.contains('is-hidden') ? body : stack;
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
        stack.appendChild(btn);
    }

    function removeJumpResult() {
        const old = document.querySelector('.jump-result[data-cp-jump]');
        if (old) old.remove();
    }

    /**
     * 渲染一轮完整结果：回答正文 → 图表 → 证据 → 引用核验 → 检索与口径披露。
     * @param {any} r 后端 /api/agent/chat 的响应体（见 docs/P2能力扩展需求规格说明书 第 7 章）
     */
    function renderResult(r) {
        lastQuestion = r.question || lastQuestion;
        if (r.blocked) { renderBlocked(r); return; }
        $('#resultEmpty').classList.add('is-hidden');
        const box = $('#resultBody');
        box.classList.remove('is-hidden');

        const engineBadge = r.engine === 'deepseek'
            ? `<span class="badge badge-brand">${esc(r.engine_label || 'DeepSeek 工具调用')}</span>`
            : `<span class="badge badge-warn">${esc(r.engine_label || '规则路由（仅统计）')}</span>`;
        const g = r.grounding || {};
        const gBadge = g.passed
            ? `<span class="badge badge-pos" title="${esc(g.note || '')}">数值校验通过</span>`
            : `<span class="badge badge-warn" title="${esc(g.note || '')}">需复核数值</span>`;
        const ci = r.citation || null;
        const ciBadge = citationBadge(ci);

        box.innerHTML = `
        <div class="card">
            <div class="card-head">
                <div><div class="card-title" role="heading" aria-level="3">分析结论</div>
                <div class="card-sub">${esc(r.dataset_name || '')} · ${esc(r.question || '')}</div></div>
                <div class="card-tools">
                    <button class="btn btn-sm" id="btnRetry">重新提问</button>
                    <a class="btn btn-sm" id="btnToInsights" href="${esc((r.carry && r.carry.insights_url) || ('/insights?' + CP.qs()))}">去数据洞察</a>
                </div>
            </div>
            <div class="answer-text">${mdText(r.answer || '')}</div>
            <div class="answer-meta">
                ${engineBadge}${gBadge}${ciBadge}
                <span class="badge">${r.tool_steps.length} 次工具调用</span>
                ${r.rag && r.rag.count ? `<span class="badge">${r.rag.count} 条检索片段</span>` : ''}
                <span class="badge">${r.duration_ms} ms</span>
                ${r.answer_source === 'rule_safety' ? '<span class="badge badge-warn">AI 输出未通过校验，已替换</span>' : ''}
            </div>
            ${r.notice ? `<div class="note note-warn" style="margin-top:10px;">${esc(r.notice)}</div>` : ''}
            ${g.passed ? '' : `<div class="note note-warn" style="margin-top:8px;">${esc(g.note || '')}</div>`}
        </div>

        ${r.metrics && r.metrics.length ? `<div class="card"><div class="card-head"><div class="card-title" role="heading" aria-level="3">关键指标</div></div>${metricChips(r.metrics)}</div>` : ''}

        ${r.charts && r.charts.length ? `<div class="card">
            <div class="card-head">
                <div><div class="card-title" role="heading" aria-level="3">动态图表</div>
                <div class="card-sub">数值来自工具返回结果，由前端标准组件渲染</div></div>
            </div>
            <div class="grid ${r.charts.length > 1 ? 'g-2' : ''}" id="agCharts">
                ${r.charts.map((c, i) => `
                    <div>
                        <div style="font-size:var(--fs-sm);font-weight:600;color:var(--ink-2);margin-bottom:2px;">${esc(c.title)}</div>
                        <div class="card-sub" style="margin-bottom:4px;">${esc(c.caption || '')}</div>
                        <div class="chart" id="agChart${i}" style="height:${r.charts.length > 1 ? 240 : 280}px"></div>
                    </div>`).join('')}
            </div>
        </div>` : ''}

        ${boundsBlock(r)}
        ${citationBlock(r.citation)}
        ${ragBlock(r.rag, r.answer)}
        ${stepsBlock(r.tool_steps)}
        ${evidenceBlock(r.evidence)}

        <div class="card">
            <div class="card-head"><div class="card-title" role="heading" aria-level="3">分析局限与继续追问</div></div>
            ${(r.limitations || []).map(t => `<div class="note note-muted" style="margin-bottom:6px;">${esc(t)}</div>`).join('')}
            <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px;">
                ${(r.followups || []).map(q => `<button class="chip" data-follow="${esc(q)}">${esc(q)}</button>`).join('')}
            </div>
        </div>`;

        // 渲染图表
        (r.charts || []).forEach((spec, i) => {
            const el = document.getElementById('agChart' + i);
            if (el) CP.renderSpec(el, spec);
        });

        // 证据详情
        $$('#resultBody .evidence-item').forEach(el => {
            el.addEventListener('click', () => openEvidence(el.dataset.ref));
        });
        bindRefs($('#resultBody'));
        // 追问
        $$('#resultBody [data-follow]').forEach(b => {
            b.addEventListener('click', () => { $('#qInput').value = b.dataset.follow; send(); });
        });
        const retry = $('#btnRetry');
        if (retry) retry.addEventListener('click', () => { $('#qInput').value = lastQuestion; send(); });
        const toIns = $('#btnToInsights');
        if (toIns) toIns.setAttribute('href', '/insights?' + CP.qs());

        mountJumpResult();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    /* ---------------- 证据详情 ---------------- */
    async function openEvidence(ref) {
        let d = null;
        try {
            d = await api('/api/comments/' + encodeURIComponent(ref), { dataset_id: state.dataset_id });
        } catch (e) {
            toast('证据加载失败：' + e.message, 'err');
            return;
        }
        /* 走通用的 CP.openModal，而不是自己拼一个 .modal-mask：
           手写版只有 class，没有 role="dialog" / aria-modal / 焦点圈定 / 关闭后焦点回位，
           Esc 靠一个全局 keydown 兜——同一站上另外四个弹窗都是好的，唯独
           "最需要读内容的那一个"成了键盘黑洞（Tab 会跑到背后的页面上）。 */
        const mask = document.createElement('div');
        mask.className = 'modal-mask';
        mask.hidden = true;
        mask.setAttribute('role', 'dialog');
        mask.setAttribute('aria-modal', 'true');
        mask.setAttribute('aria-labelledby', 'evDlgTitle');
        mask.innerHTML = `<div class="modal">
            <div style="display:flex;align-items:flex-start;gap:10px;">
                <div>
                    <div class="modal-title" id="evDlgTitle">评论证据详情</div>
                    <div class="card-sub">${esc(d.comment_ref)} · 已脱敏</div>
                </div>
                <button class="btn btn-sm btn-ghost" style="margin-left:auto" data-close>关闭</button>
            </div>
            <div class="modal-quote">${esc(d.content_masked)}</div>
            <dl class="kv">
                <dt>所属数据集</dt><dd>${esc(d.dataset_name || '')}</dd>
                <dt>统计口径</dt><dd>${esc(d.filters_text || '')}</dd>
                <dt>平台</dt><dd>${esc(d.platform)}</dd>
                <dt>发布时间</dt><dd>${esc(d.comment_time || d.comment_date || '')}</dd>
                <dt>情感</dt><dd>${esc(d.sentiment_label)}</dd>
                <dt>业务主题</dt><dd>${esc((d.dimensions || []).join(' / ') || d.dimension || '—')}</dd>
                <dt>点赞数</dt><dd>${d.like_count == null ? '未提供' : num(d.like_count)}</dd>
                ${d.negative_reason ? `<dt>负面原因</dt><dd>${esc(d.negative_reason)}</dd>` : ''}
            </dl>
            <div class="note note-muted" style="margin-top:12px;">${esc(d.privacy_note || '')}</div>
            <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end;">
                <a class="btn" href="/evidence?${CP.qs({ ref: d.comment_ref })}">在证据中心定位这条</a>
                <button class="btn btn-primary" data-close>知道了</button>
            </div>
        </div>`;
        document.body.appendChild(mask);
        const close = () => CP.closeModal(mask);
        mask.addEventListener('click', e => {
            const t = /** @type {Element} */ (e.target);
            if (t === mask || t.closest('[data-close]')) close();
        });
        CP.openModal(mask, { back: document.activeElement });
    }

    /* ---------------- 实时执行面板（流式） ---------------- */
    function startRunPanel() {
        $('#resultEmpty').classList.add('is-hidden');
        const box = $('#resultBody');
        box.classList.remove('is-hidden');
        box.innerHTML = `
        <div class="card">
            <div class="card-head">
                <div>
                    <div class="card-title" role="heading" aria-level="3">执行过程
                        <span class="badge badge-run" id="liveBadge">运行中</span></div>
                    <div class="card-sub" id="liveStage">正在理解问题…</div>
                </div>
                <div class="card-tools"><code class="step-tool" id="liveClock">0.0s</code></div>
            </div>
            <div id="liveSteps"></div>
            <div class="live-foot">下面的步骤与文字都是后端实时推送的真实事件，不是前端动画：
                工具名、参数、耗时、命中的每一条文字都来自这一次实际执行。</div>
        </div>`;

        const t0 = Date.now();
        const timer = setInterval(() => {
            const el = $('#liveClock');
            if (el) el.textContent = ((Date.now() - t0) / 1000).toFixed(1) + 's';
        }, 100);

        let idx = 0;
        let bubble = null;
        let textEl = null;
        const pending = [];

        function ensureBubble() {
            if (bubble) return;
            bubble = document.createElement('div');
            bubble.className = 'msg bot';
            bubble.innerHTML = '<span class="stream-text"></span><i class="stream-caret"></i>';
            $('#chatLog').appendChild(bubble);
            textEl = bubble.querySelector('.stream-text');
        }

        return {
            el: bubble,
            stage(d) {
                const el = $('#liveStage');
                if (el) el.textContent = d.label + (d.detail ? '，' + d.detail : '');
            },
            plan(d) {
                const el = $('#liveStage');
                if (el && d.tools && d.tools.length) {
                    el.textContent = '本轮计划：' + d.tools.map(t => t.label || t.tool).join(' → ');
                }
            },
            toolStart(d) {
                idx += 1;
                const host = $('#liveSteps');
                if (!host) return;
                const row = document.createElement('div');
                row.className = 'step live-step';
                row.innerHTML = `
                    <div class="step-idx">${idx}</div>
                    <div class="step-body">
                        <div class="step-title">
                            ${esc(d.label || d.tool)}
                            <code class="step-tool">${esc(d.tool)}</code>
                            <span class="badge badge-run">运行中</span>
                        </div>
                        <div class="step-summary">${argChips(d.args)}</div>
                    </div>`;
                host.appendChild(row);
                pending.push(row);
            },
            toolDone(d) {
                const row = pending.shift();
                if (!row) return;
                const badge = row.querySelector('.badge');
                badge.className = 'badge ' + (d.ok ? (d.empty ? 'badge-warn' : 'badge-pos') : 'badge-neg');
                badge.textContent = d.ok ? (d.empty ? '无数据' : '完成') : '失败';
                const title = row.querySelector('.step-title');
                const ms = document.createElement('span');
                ms.className = 'step-tool';
                ms.textContent = d.elapsed_ms + 'ms';
                title.appendChild(ms);
                const sm = row.querySelector('.step-summary');
                if (sm) sm.innerHTML = argChips(d.args) +
                    `<div style="margin-top:4px;color:var(--ink-2);">${esc(d.summary || '')}</div>`;
            },
            token(d) {
                ensureBubble();
                textEl.textContent += d.text;
                const log = $('#chatLog');
                log.scrollTop = log.scrollHeight;
            },
            ragDone(d) {
                idx += 1;
                const host = $('#liveSteps');
                if (!host) return;
                const c = d.candidates || {};
                const row = document.createElement('div');
                row.className = 'step live-step';
                row.innerHTML = `
                    <div class="step-idx">${idx}</div>
                    <div class="step-body">
                        <div class="step-title">
                            检索原文片段（RAG）
                            <code class="step-tool">rag.retrieve</code>
                            <span class="badge badge-pos">完成</span>
                            <span class="step-tool">${d.elapsed_ms || 0}ms</span>
                        </div>
                        <div class="step-summary">命中 ${d.count || 0} 条片段 · 候选池 ${num(c.filtered_docs)} 条评论 · 词项 ${num(c.bm25)} / 扩展 ${num(c.expand)} / 向量 ${num(c.vector)}</div>
                    </div>`;
                host.appendChild(row);
            },
            verdict(d) {
                idx += 1;
                const host = $('#liveSteps');
                if (!host) return;
                const st = VERDICT_STYLE[d.verdict] || VERDICT_STYLE.unverified;
                const row = document.createElement('div');
                row.className = 'step live-step';
                row.innerHTML = `
                    <div class="step-idx">${idx}</div>
                    <div class="step-body">
                        <div class="step-title">
                            核对引用能否溯源
                            <code class="step-tool">veracity.enforce</code>
                            <span class="badge ${st.cls}">${st.mark} ${esc(d.label || '')}</span>
                        </div>
                        <div class="step-summary">${esc(d.note || '')}</div>
                    </div>`;
                host.appendChild(row);
            },
            answerReset(d) {
                ensureBubble();
                textEl.textContent = d.answer || '';
                const warn = document.createElement('div');
                warn.className = 'stream-warn';
                warn.textContent = '⚠ ' + (d.reason || '回答已撤回');
                bubble.appendChild(warn);
                bubble.querySelectorAll('.stream-caret').forEach(x => x.remove());
                bubble = null;
                textEl = null;
            },
            finish(r) {
                clearInterval(timer);
                const clock = $('#liveClock');
                if (clock) clock.textContent = ((r.duration_ms || 0) / 1000).toFixed(1) + 's';
                const badge = $('#liveBadge');
                if (badge) { badge.className = 'badge badge-pos'; badge.textContent = '完成'; }
                const st = $('#liveStage');
                if (st) {
                    const ft = r.first_token_ms ? `首字 ${r.first_token_ms}ms · ` : '';
                    st.textContent = `${r.tool_steps.length} 次工具调用 · ${ft}总耗时 ${r.duration_ms}ms`;
                }
                if (!bubble && r.answer) {
                    const el = pushMsg('bot', '', true);
                    el.innerHTML = mdText(r.answer);
                    bindRefs(el);
                } else if (bubble && textEl) {
                    const finalText = textEl.textContent.trim() || r.answer || '';
                    textEl.innerHTML = mdText(finalText);
                    bubble.querySelectorAll('.stream-caret').forEach(x => x.remove());
                    bindRefs(bubble);
                }
            },
            resetForFallback(msg) {
                if (bubble) {
                    bubble.remove();
                    bubble = null;
                    textEl = null;
                }
                const st = $('#liveStage');
                if (st) st.textContent = msg;
            },
            fail(msg) {
                clearInterval(timer);
                const badge = $('#liveBadge');
                if (badge) { badge.className = 'badge badge-neg'; badge.textContent = '失败'; }
                const st = $('#liveStage');
                if (st) st.textContent = msg;
            }
        };
    }

    /* ---------------- 消费 SSE ---------------- */
    /**
     * 读 SSE 流并驱动右侧"运行面板"。
     *
     * 这里刻意不用 EventSource：提问要 POST（问题在 body 里，不能进 URL），
     * 而 EventSource 只支持 GET。于是手写 SSE 分帧——按空行切块、取 data: 行。
     *
     * @param {string} q 用户问题
     * @param {RunPanel} run 面板控制器，事件到这里被翻译成界面变化
     * @returns {Promise<any>} 流结束后拿到的完整结构化响应
     */
    async function consumeStream(q, run) {
        const resp = await fetch('/api/agent/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: q, dataset_id: state.dataset_id, history })
        });
        if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);
        const reader = resp.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buf = '';
        let payload = null;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            let cut;
            while ((cut = buf.indexOf('\n\n')) >= 0) {
                const block = buf.slice(0, cut);
                buf = buf.slice(cut + 2);
                const line = block.split('\n').find(l => l.startsWith('data:'));
                if (!line) continue;
                let ev;
                try { ev = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
                if (ev.type === 'stage') run.stage(ev);
                else if (ev.type === 'plan') run.plan(ev);
                else if (ev.type === 'tool_start') run.toolStart(ev);
                else if (ev.type === 'tool_done') run.toolDone(ev);
                else if (ev.type === 'token') run.token(ev);
                else if (ev.type === 'rag_done') run.ragDone(ev);
                else if (ev.type === 'verdict') run.verdict(ev);
                else if (ev.type === 'answer_reset') run.answerReset(ev);
                else if (ev.type === 'done') payload = ev.payload;
                else if (ev.type === 'error') throw new Error(ev.message || '流式分析中断');
            }
        }
        if (!payload) throw new Error('连接中断，未收到完整结果');
        return payload;
    }

    /* ---------------- 发送 ---------------- */
    /** 发送一轮提问：锁定输入、启动运行面板、消费流、渲染结果 */
    async function send() {
        if (busy) return;
        const input = $('#qInput');
        const q = input.value.trim();
        if (!q) { toast('请先输入问题', 'err'); return; }
        if (q.length > 200) { toast('问题请控制在 200 字以内', 'err'); return; }

        // 发送前必须停掉识别：否则 300ms 后到达的识别结果会写进已被清空的输入框，
        // 用户会以为这句话没发出去
        if (voice && voice.listening) voice.stop();

        busy = true;
        $('#btnSend').disabled = true;
        input.value = '';
        pushMsg('user', q);
        const run = startRunPanel();
        $('#sessionInfo').textContent = '分析中…';

        let payload = null;
        try {
            payload = await consumeStream(q, run);
        } catch (streamErr) {
            // 流式通道不可用（代理限制/浏览器不支持） → 自动回退到一次性接口
            run.resetForFallback('实时通道不可用，已切换为一次性分析…（' + streamErr.message + '）');
            try {
                payload = await api('/api/agent/chat', null, {
                    method: 'POST',
                    body: JSON.stringify({ question: q, dataset_id: state.dataset_id, history })
                });
            } catch (e) {
                run.fail('这次分析没有成功：' + e.message);
                pushMsg('bot', '这次分析没有成功：' + e.message + '（可以换个说法或检查数据集）');
                toast('分析失败：' + e.message, 'err');
                $('#sessionInfo').textContent = '本轮失败';
                busy = false;
                $('#btnSend').disabled = false;
                input.focus();
                return;
            }
        }

        try {
            run.finish(payload);
            lastPayload = payload;
            renderResult(payload);
            // 被输入闸拦下的提问不进上下文：它既没有产生结论，也不该在下一轮
            // 作为"用户说过的话"被回放给模型（那等于把攻击载荷喂进上下文）
            if (!payload.blocked) {
                history.push({ role: 'user', content: q });
                history.push({ role: 'assistant', content: (payload.answer || '').slice(0, 500) });
                history = history.slice(-6);
            }
            const rounds = Math.ceil(history.length / 2);
            if (payload.blocked) {
                $('#sessionInfo').textContent =
                    `本轮被安全检查拦下 · 未调用模型 · ${payload.duration_ms}ms`;
            } else {
                $('#sessionInfo').textContent =
                    `已进行 ${rounds} 轮 · `
                    + `${payload.engine === 'deepseek' ? 'AI · DeepSeek' : '规则模式'} · `
                    + `${payload.duration_ms}ms`;
            }
            // 单会话轮次上限：上下文越长越容易串味，也越容易被人用长会话做慢速注入
            if (rounds >= guardCfg.max_rounds) {
                pushMsg('bot', '本会话已 <b>' + rounds + '</b> 轮，到了单会话上限。'
                    + '上下文太长会让结论互相污染——建议开一个新会话。'
                    + '<div style="margin-top:6px;"><button class="btn btn-sm" id="btnNewSession">开启新会话</button></div>', true);
                const nb = document.getElementById('btnNewSession');
                if (nb) nb.addEventListener('click', newSession);
            }
        } finally {
            busy = false;
            $('#btnSend').disabled = false;
            input.focus();
        }
    }

    function newSession() {
        history = [];
        lastPayload = null;
        $('#chatLog').innerHTML = '';
        $('#resultBody').classList.add('is-hidden');
        $('#resultEmpty').classList.remove('is-hidden');
        $('#sessionInfo').textContent = '会话未开始';
        $('#qInput').value = '';
        $('#qInput').focus();
        toast('已开启新会话');
    }

    async function loadGuard() {
        try {
            const d = await api('/api/guard/catalog');
            guardCfg.max_rounds = d.max_rounds || 20;
            guardCfg.max_question_chars = d.max_question_chars || 200;
        } catch (e) { /* 拿不到就沿用默认值，不阻断提问 */ }
    }

    /* ---------------- 初始化 ---------------- */
    async function loadTools() {
        try {
            const d = await api('/api/agent/tools');
            // 中文能力名当标题，函数名降为等宽副文本：这屏的读者是学工处老师，
            // 他们要判断的是"这个助手能查什么"，不是"这个函数收了几个参数"。
            $('#toolCards').innerHTML = d.tools.map(t => `
                <div class="tool-card">
                    <div class="tool-label">${esc(t.label || t.name)}</div>
                    <div class="tool-name">${esc(t.name)}</div>
                    <div class="tool-desc">${esc(t.description)}</div>
                    <div class="tool-args">${t.params.map(p => `<span class="arg-chip">${esc(p)}</span>`).join('')}</div>
                </div>`).join('');
        } catch (e) {
            $('#toolCards').innerHTML = '<div class="note note-warn">工具清单加载失败：' + esc(e.message) + '</div>';
        }
    }

    /* 空态的三张示例卡：点一下等于把这句话打进输入框并发送。
       "不知道能问什么"是这一页最大的流失点，而推荐问题在左栏底部只露出一条。 */
    function mountPrompts() {
        // 模板组已移除 #demoPrompts（推荐问题统一留在左栏 #recList）。
        // 容器不在时直接返回：CP.$$ 返回空数组本就不抛错，这里再显式守一层，
        // 免得以后有人给宿主元素本身加属性时踩到 null。
        const host = $('#demoPrompts');
        if (!host) return;
        CP.$$('#demoPrompts [data-q]').forEach(b => b.addEventListener('click', () => {
            $('#qInput').value = b.dataset.q;
            $('#qInput').focus();
            send();
        }));
    }

    document.addEventListener('cp:ready', () => {
        renderRecs();
        loadTools();
        mountPrompts();
        loadGuard();
        mountVoice();
        const meta = state.datasetMeta;
        $('#chatDataset').textContent = meta ? `${meta.name}（${num(meta.valid_count)} 条有效评论）` : '—';
    });

    // 切主题只重画图表，不重跑 Agent：重跑要花十几秒，用户只是换了个配色而已
    document.addEventListener('cp:theme', () => {
        if (!lastPayload) return;
        CP.$$('#resultBody .chart').forEach(el => { el.innerHTML = ''; });
        renderResult(lastPayload);
    });

    // 桌面把窗口拖窄到单列时，结果锚要跟着出现（样式组只在 ≤720 给吸底视觉）
    window.addEventListener('resize', () => {
        const body = $('#resultBody');
        if (lastPayload && body && !body.classList.contains('is-hidden')) mountJumpResult();
    });

    document.addEventListener('cp:dataset', () => {
        history = [];
        const meta = state.datasetMeta;
        $('#chatDataset').textContent = meta ? `${meta.name}（${num(meta.valid_count)} 条有效评论）` : '—';
        pushMsg('sys', '已切换数据集，会话上下文已重置');
        $('#sessionInfo').textContent = '会话已重置';
        $('#resultBody').classList.add('is-hidden');
        $('#resultEmpty').classList.remove('is-hidden');
        removeJumpResult();
        toast('已切换数据集，历史对话不再作为上下文', 'ok');
    });

    $('#btnSend').addEventListener('click', send);
    $('#qInput').addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    $('#btnClear').addEventListener('click', () => {
        if (!history.length) {
            toast('还没有可清空的对话', 'ok');
            return;
        }
        // 一键清空是不可逆的，而它就在发送键旁边。清空后留一条可撤销的入口，
        // 而不是给一个 5 秒后就消失的 toast——读屏用户根本来不及找到它。
        const prevHistory = history.slice();
        const prevLog = $('#chatLog').innerHTML;
        const prevSession = $('#sessionInfo').textContent;
        history = [];
        $('#chatLog').innerHTML =
            '<div class="msg bot" id="clearMsg">会话已清空。可以继续提问，例如"负面评论集中在哪些原因"。' +
            '<button class="btn btn-sm" type="button" id="btnUndoClear" style="margin-left:10px;">撤销清空</button></div>' +
            `<div class="msg sys">当前数据集：<span>${esc(state.datasetMeta ? state.datasetMeta.name : '—')}</span></div>`;
        $('#resultBody').classList.add('is-hidden');
        $('#resultEmpty').classList.remove('is-hidden');
        removeJumpResult();
        $('#sessionInfo').textContent = '会话未开始';
        CP.announce('会话已清空，可以在页面上的撤销按钮恢复');
        $('#btnUndoClear').addEventListener('click', () => {
            // 已经接着问了新问题就不再恢复，否则会把新一轮对话覆盖掉
            if (history.length) { toast('已经开始了新对话，无法恢复', 'err'); return; }
            history = prevHistory;
            $('#chatLog').innerHTML = prevLog;
            $('#sessionInfo').textContent = prevSession;
            toast('已恢复清空前的对话', 'ok');
            CP.announce('已恢复清空前的对话');
        });
    });
})();
