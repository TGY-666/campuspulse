/* PAGE-05.1 数据采集与导入
 *
 * 三条来路（实时采集 / 公开平台导出文件 / 评论截图）放在同一页的三个分段里：
 * 它们回答的是同一个问题——"数据从哪进来"。合成一个侧栏目录项，
 * 是因为拆成三个子项后，侧栏会变成五项并列，层级反而看不出主次。
 *
 * 实时采集的流程是强制的四步，任何一步都不能跳过：
 *   采集 → 看候选集 → 清洗 → 由人确认是否入库
 * 尤其是最后一步：清洗完自动入库，等于把"采集器采歪了"直接写进分析口径。
 * 文件导入与截图识别后来也补齐了同样的后两步（见 renderIntake）：
 * 预览清洗结果 → 逐条核对 → 人点「确认导入」才建数据集。
 */
(function () {
    'use strict';
    const { $, $$, esc, num, pct, api, state, toast, setState } = CP;

    /* ==================== 分段切换 ==================== */
    function setupTabs() {
        const seg = $('#intakeSeg');
        if (!seg) return;
        seg.addEventListener('click', e => {
            const btn = e.target.closest('[data-tab]');
            if (!btn) return;
            $$('#intakeSeg button').forEach(b => b.classList.toggle('on', b === btn));
            $$('[data-panel]').forEach(p => { p.hidden = p.dataset.panel !== btn.dataset.tab; });
        });
    }

    /* ==================== 实时采集 ==================== */
    const cw = {
        sid: null, cursor: 0, timer: null, engine: null,
        platformSel: [], cleaned: null, rowsSeen: 0, keyword: '',
        page: 0, filter: 'all', starting: false,
        lastRun: null,   // {keyword,target,from,to,platforms}：登录失效后原样重跑
        sel: null,
    };

    /* ==================== 逐条选择 ====================
     * 默认全选保留原来的"确认即整批入库"；用户取消一条时只记排除项，
     * 不必先把所有 comment_ref 拉到前端，翻多少页都成立。
     * "全不选"切成 inclusion 模式：只把用户真正点过的 ref 提交上去。 */
    function newSelection() {
        return { mode: 'all', excluded: new Set(), included: new Set() };
    }

    function pickedCount(sel, validCount) {
        return sel.mode === 'all'
            ? Math.max(0, validCount - sel.excluded.size)
            : sel.included.size;
    }

    function isPicked(sel, ref) {
        return sel.mode === 'all' ? !sel.excluded.has(ref) : sel.included.has(ref);
    }

    function setPicked(sel, ref, on) {
        if (sel.mode === 'all') {
            if (on) sel.excluded.delete(ref); else sel.excluded.add(ref);
        } else {
            if (on) sel.included.add(ref); else sel.included.delete(ref);
        }
    }

    function pickAll(sel) {
        sel.mode = 'all'; sel.excluded.clear(); sel.included.clear();
    }

    function pickNone(sel) {
        sel.mode = 'none'; sel.excluded.clear(); sel.included.clear();
    }

    function selectionPayload(sel) {
        return sel.mode === 'all'
            ? { select_mode: 'all', refs: Array.from(sel.excluded) }
            : { select_mode: 'none', refs: Array.from(sel.included) };
    }

    function platformChips() {
        const box = $('#crawlPlatforms');
        box.innerHTML = CP.PLATFORMS.map(p =>
            `<button type="button" class="chip ${cw.platformSel.includes(p) ? 'on' : ''}"
                     data-plat="${esc(p)}">${esc(p)}</button>`).join('');
        $$('#crawlPlatforms [data-plat]').forEach(b => b.addEventListener('click', () => {
            const p = b.dataset.plat;
            cw.platformSel = cw.platformSel.includes(p)
                ? cw.platformSel.filter(x => x !== p)
                : cw.platformSel.concat(p);
            platformChips();
        }));
    }

    async function loadEngine() {
        try {
            const e = await api('/api/crawl/status');
            cw.engine = e;
            renderEngine(e);
        } catch (err) {
            $('#crawlEngineBadge').textContent = '检测失败';
            $('#crawlEngineBadge').className = 'badge badge-warn';
            $('#crawlEnv').innerHTML = `<div class="note note-warn">采集环境检测失败：${esc(err.message)}</div>`;
        }
    }

    function renderEngine(e) {
        const badge = $('#crawlEngineBadge');
        if (!e.enabled) {
            badge.textContent = '已关闭';
            badge.className = 'badge badge-warn';
        } else if (!e.playwright || !e.browser.ok) {
            badge.textContent = '环境不完整';
            badge.className = 'badge badge-warn';
        } else {
            badge.textContent = '引擎就绪';
            badge.className = 'badge badge-pos';
            badge.title = e.browser.path || '';
        }

        // 环境和登录态直接摆在表单上面：这两件事不满足时，点"开始采集"必然拿 0 条，
        // 与其让人等 2 分钟再猜原因，不如提前说清楚。
        const warns = [];
        if (!e.enabled) warns.push('实时采集已关闭（CP_CRAWL=0），入口仅作展示。');
        if (!e.playwright) warns.push('未安装 playwright：<code>pip install playwright</code>');
        if (!e.browser.ok) warns.push('找不到浏览器：设置 <code>CP_CRAWL_CHROME</code> 指向 chrome.exe');
        const noLogin = e.logins.filter(l => !l.configured).map(l => l.platform);
        if (noLogin.length) warns.push(`${noLogin.join(' / ')} 还没登录：点「开始采集」会先弹出登录窗口，登录完成后自动开跑。`);
        if (!e.can_collect) warns.push('当前角色没有「数据导入」权限，不能发起采集。');

        $('#crawlEnv').innerHTML = warns.length
            ? `<div class="note note-warn" style="margin-bottom:12px;">${warns.join('<br/>')}</div>` : '';

        const startBtn = $('#btnCrawlStart');
        // 有任务在跑时按钮必须保持禁用：保存登录态会重渲染这一段，
        // 这里若只看环境，按钮会突然变亮，用户点了只会拿到 429。
        // 用定时器判断"是否真的在跑"，而不是 sid 是否存在——采集结束后
        // sid 还要留着给清洗和入库用。
        startBtn.disabled = !e.enabled || !e.playwright || !e.browser.ok
            || !e.can_collect || !!cw.timer || !!cw.starting;
        $('#crawlTarget').max = String(e.limits.max_target);
        $('#crawlTarget').title = `单次最多 ${e.limits.max_target} 条；采满或点「停止」即结束。`
            + '搜索页会自动向下加载更多结果，每个视频的评论会翻到底。';
        // 操作约束常驻可见：数值只取接口 limits.max_target，控件名与表单逐字一致（目标条数 / 停止）。
        // 只建一次（幂等，不重复插入），放在实时采集表单组下面独立成行，样式由 .scope-line 提供。
        const crawlForm = document.querySelector('.crawl-form');
        if (crawlForm) {
            let scopeLine = document.getElementById('crawlTargetScope');
            if (!scopeLine) {
                scopeLine = document.createElement('div');
                scopeLine.id = 'crawlTargetScope';
                scopeLine.className = 'scope-line';
                crawlForm.insertAdjacentElement('afterend', scopeLine);
            }
            scopeLine.textContent = `目标条数：单次最多 ${e.limits.max_target} 条；采满或点「停止」即结束。`;
        }

        renderLogin(e);

        // 刷新页面不该把正在跑的任务"弄丢"：采集在后台线程里继续，
        // 前端却不知道 sid，于是点开始只得到"已有任务在运行"，也没法停。
        if (e.active && !cw.sid) resume(e.active);
    }

    /** 重新接上一个已经在跑的采集任务（刷新 / 换标签页回来时用）。 */
    function resume(sid) {
        cw.sid = sid;
        cw.cursor = 0;
        cw.rowsSeen = 0;
        $('#crawlFeed').innerHTML = feedShell();
        $('#btnCrawlStop').classList.remove('is-hidden');
        const btn = $('#btnCrawlStart');
        btn.disabled = true;
        btn.textContent = '采集中…';
        cw.timer = setInterval(poll, 1000);
        poll();
    }

    function renderLogin(e) {
        const badge = $('#loginBadge');
        // 008 只数 configured 会让「已配置 2/2」和下面橙色的「登录已失效」同屏：
        // 计数按真正可用（未失效、已配置、且带 session）来算，其余明确写成需重新登录。
        // 只改前端展示，不动后端的登录态判定。
        const total = (e.logins || []).length;
        const ready = (e.logins || []).filter(l => !l.stale && l.configured && l.has_session).length;
        const need = total - ready;
        badge.textContent = total
            ? `可用 ${ready}/${total}${need ? ` · ${need} 个需重新登录` : ''}`
            : '未配置';
        badge.className = 'badge ' + (total && !need ? 'badge-pos' : 'badge-warn');

        $('#loginBody').innerHTML = `<div class="grid g-2">
            ${e.logins.map(l => `
                <div class="login-card">
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                        <b style="font-size:var(--fs-sm);">${esc(l.platform)}</b>
                        <span class="badge ${l.stale ? 'badge-warn' : (l.configured ? (l.has_session ? 'badge-pos' : 'badge-warn') : 'badge-warn')}">
                            ${l.stale ? '登录已失效'
                                : (l.configured ? (l.has_session ? '已登录 · 含 ' + esc(l.key) : '已登录 · 缺 ' + esc(l.key)) : '未登录')}
                        </span>
                        ${l.saved_at ? `<span style="font-size:var(--fs-micro);color:var(--ink-4);">${esc(l.saved_at)}</span>` : ''}
                    </div>
                    <div style="display:flex;gap:6px;margin-top:8px;align-items:center;flex-wrap:wrap;">
                        <button class="btn btn-primary btn-sm" data-open="${esc(l.platform)}">
                            ${l.configured ? '重新登录' : '打开登录窗口'}</button>
                        ${l.configured ? `<button class="btn btn-sm" data-clear="${esc(l.platform)}">清除</button>` : ''}
                        <details style="flex:1;min-width:0;">
                            <summary style="font-size:var(--fs-micro);color:var(--ink-4);cursor:pointer;">手工粘贴 cookie</summary>
                            <div style="display:flex;gap:6px;margin-top:6px;">
                                <input class="input" data-cookie="${esc(l.platform)}"
                                       style="flex:1;min-width:0;font-size:var(--fs-xs);"
                                       aria-label="粘贴${esc(l.platform)}登录 cookie"
                                       placeholder="k=v; k=v" autocomplete="off" spellcheck="false">
                                <button class="btn btn-sm" data-save="${esc(l.platform)}">保存</button>
                            </div>
                        </details>
                    </div>
                    ${l.stale
                        ? `<div style="font-size:var(--fs-micro);color:var(--warn-ink);margin-top:5px;">
                               采集时被平台要求登录（${esc(l.stale_at || '')}）：cookie 还在，但已经过期了，点「重新登录」重扫一次。</div>`
                        : (l.configured && !l.has_session
                            ? `<div style="font-size:var(--fs-micro);color:var(--warn-ink);margin-top:5px;">缺少 ${esc(l.key)}，多半没登录成功或复制的是游客 cookie。</div>` : '')}
                </div>`).join('')}
        </div>`;

        $$('#loginBody [data-open]').forEach(b => b.addEventListener('click', () => {
            // 主动登录：登录完不开采集，只刷新这张卡片
            promptLogin(b.dataset.open).then(ok => { if (ok) toast('登录态已保存', 'ok'); });
        }));
        $$('#loginBody [data-save]').forEach(b => b.addEventListener('click', () => saveLogin(b.dataset.save, b)));
        $$('#loginBody [data-clear]').forEach(b => b.addEventListener('click', () => clearLogin(b.dataset.clear)));
    }

    async function saveLogin(platform, btn) {
        const input = $(`#loginBody [data-cookie="${platform}"]`);
        const cookie = input ? input.value.trim() : '';
        if (!cookie) { toast('请先粘贴 cookie', 'err'); return; }
        btn.disabled = true;
        btn.textContent = '保存中';
        try {
            const d = await api('/api/crawl/login', null, {
                method: 'POST', body: JSON.stringify({ platform, cookie })
            });
            input.value = '';
            toast(d.login.has_session
                ? `${platform} 登录态已保存`
                : `${platform} 已保存，但缺少 ${d.login.key}，采集可能仍然为空`, d.login.has_session ? 'ok' : 'err');
            cw.engine = d.status;
            renderEngine(d.status);
        } catch (e) {
            toast('保存失败：' + e.message, 'err');
        } finally {
            btn.disabled = false;
            btn.textContent = '保存';
        }
    }

    async function clearLogin(platform) {
        const ok = await CP.confirmModal({
            title: `清除 ${platform} 的登录态？`,
            subHtml: '清除后该平台的采集会被拦截，直到重新粘贴 cookie。<b>已经采到的评论不受影响</b>。',
            okLabel: '清除登录态'
        });
        if (!ok) return;
        try {
            const d = await api('/api/crawl/login?platform=' + encodeURIComponent(platform), null, { method: 'DELETE' });
            cw.engine = d.engine;
            renderEngine(d.engine);
            toast('已清除', 'ok');
        } catch (e) {
            toast('清除失败：' + e.message, 'err');
        }
    }

    async function startCrawl() {
        if (loginFlow) { toast('请先在登录窗口里完成登录', 'err'); return; }
        const keyword = $('#crawlKeyword').value.trim();
        if (!keyword) { toast('请先填写关键词', 'err'); $('#crawlKeyword').focus(); return; }
        if (!cw.platformSel.length) { toast('请至少选一个平台', 'err'); return; }
        const target = Number($('#crawlTarget').value || 60);
        const from = $('#crawlDateFrom').value;
        const to = $('#crawlDateTo').value;
        if (from && to && from > to) {
            toast('开始日期不能晚于结束日期', 'err');
            $('#crawlDateFrom').focus();
            return;
        }

        // 先探登录态：缺哪个平台就先弹哪个的登录，登录完成后自动接着跑。
        // 这一步不能省——未登录时采集一定拿不到数据，让它"先跑一遍再报错"
        // 只是把用户的时间浪费在等一个必然失败的任务上。
        let engine;
        try {
            engine = await api('/api/crawl/status');
        } catch (e) {
            toast('采集环境检测失败：' + e.message, 'err');
            return;
        }
        cw.engine = engine;
        renderEngine(engine);
        const missing = cw.platformSel.filter(p => {
            const l = (engine.logins || []).find(x => x.platform === p);
            return !l || !l.configured;
        });
        if (missing.length) {
            const ok = await promptLoginAll(missing);
            if (!ok) { toast('已取消：登录没有完成，采集没有开始', 'err'); return; }
        }
        await doStart(keyword, target, from, to);
    }

    /* ==================== 时间段快捷选择 ==================== */
    function ymd(d) {
        const p = x => String(x).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    }

    function setupDateQuick() {
        const sel = $('#crawlDateQuick');
        const custom = $('#crawlDateCustom');
        if (!sel) return;
        // 015 四个快捷范围收进一个「时间范围」下拉，只有选「自定义」才放出两个日期输入。
        // 保留原来的 data-days 值与全部 input id：快捷选项仍用它判断天数。
        const apply = () => {
            const opt = sel.selectedOptions && sel.selectedOptions[0];
            const days = opt ? (opt.dataset.days || opt.value) : '0';
            const isCustom = days === 'custom';
            if (custom) custom.hidden = !isCustom;
            if (isCustom) return;              // 自定义：保留用户已填的日期，不覆盖
            const n = Number(days);
            if (!n) {
                $('#crawlDateFrom').value = '';
                $('#crawlDateTo').value = '';
                return;
            }
            const today = new Date();
            const start = new Date(today.getTime() - (n - 1) * 86400000);
            $('#crawlDateFrom').value = ymd(start);
            $('#crawlDateTo').value = ymd(today);
        };
        sel.addEventListener('change', apply);
        apply();
    }

    /* ==================== 登录弹窗 ==================== */
    // 设计取舍：这里不出现账号密码输入框。密码一旦进我们的页面，就要过我们的
    // JS、日志和数据库——而收益只是"少切一次窗口"。登录发生在服务端开出的
    // 真浏览器里，我们只负责等 cookie 出现。
    let loginFlow = null;      // {platform, resolve, timer}

    function promptLoginAll(platforms) {
        return platforms.reduce(
            (p, plat) => p.then(ok => (ok ? promptLogin(plat) : false)),
            Promise.resolve(true));
    }

    function promptLogin(platform) {
        // 已有流程未结束就先收掉：否则旧 Promise 永远不 resolve，
        // 调用方（比如"登录完自动开始采集"）会一直挂着不返回。
        if (loginFlow) closeLoginModal(false);
        return new Promise(resolve => {
            loginFlow = { platform, resolve, timer: null };
            const modal = $('#crawlLoginModal');
            modal.hidden = false;
            $('#clmTitle').textContent = `登录${platform}`;
            $('#clmSub').textContent = '登录成功后会自动开始采集，不用再点一次。';
            $('#clmErr').hidden = true;
            $('#clmRetry').hidden = true;
            $('#clmFocus').hidden = false;
            $('#clmCookie').value = '';
            $('#clmBody').innerHTML = '<div class="login-state"><div class="spinner"></div>' +
                '<div><b>正在打开登录窗口…</b><div style="margin-top:4px;color:var(--ink-4);">' +
                '首次打开约 3–5 秒，请稍候</div></div></div>';
            launchLogin(platform);
        });
    }

    function stopLoginTimer() {
        if (loginFlow && loginFlow.timer) {
            clearInterval(loginFlow.timer);
            loginFlow.timer = null;
        }
    }

    async function launchLogin(platform) {
        stopLoginTimer();
        $('#clmErr').hidden = true;
        $('#clmRetry').hidden = true;
        $('#clmFocus').hidden = false;
        try {
            const d = await api('/api/crawl/login/start', null, {
                method: 'POST', body: JSON.stringify({ platform })
            });
            renderLoginStatus(d);
            loginFlow.timer = setInterval(() => tickLogin(platform), 1200);
        } catch (e) {
            $('#clmErr').textContent = e.message;
            $('#clmErr').hidden = false;
            $('#clmRetry').hidden = false;
            $('#clmFocus').hidden = true;
            $('#clmBody').innerHTML = '';
        }
    }

    async function tickLogin(platform) {
        if (!loginFlow || loginFlow.platform !== platform) return;
        try {
            const d = await api('/api/crawl/login/status');
            // 服务端可能正好在给另一个平台开窗（极少），不认就别动界面
            if (d.platform && d.platform !== platform) return;
            if (d.state === 'success') {
                stopLoginTimer();
                $('#clmBody').innerHTML = '<div class="login-state" style="border-color:var(--brand-line);">' +
                    '<div style="color:var(--pos);font-size:var(--fs-base);line-height:1.2;">✓</div>' +
                    '<div><b>登录成功</b><div style="margin-top:2px;color:var(--ink-3);">' +
                    '正在自动开始采集…</div></div></div>';
                setTimeout(() => closeLoginModal(true), 700);
                return;
            }
            if (d.state === 'opening' || d.state === 'waiting') {
                renderLoginStatus(d);
                return;
            }
            // timeout / failed / cancelled
            stopLoginTimer();
            $('#clmErr').textContent = d.message || '登录没有完成，请重试';
            $('#clmErr').hidden = false;
            $('#clmRetry').hidden = false;
            $('#clmFocus').hidden = true;
        } catch (e) {
            // 网络抖一下不终止流程：下一次轮询还能接上
        }
    }

    function renderLoginStatus(d) {
        const waited = Math.round(d.elapsed || 0);
        const limit = d.timeout || 180;
        const opening = d.state === 'opening';
        $('#clmBody').innerHTML = `
            <div class="login-state">
                <div class="spinner"></div>
                <div>
                    <b>${opening ? '正在打开登录窗口…' : '等待你在窗口里完成登录'}</b>
                    ${opening ? '' : `<div style="margin-top:4px;color:var(--ink-4);font-size:var(--fs-micro);">${esc(d.message || '')}</div>`}
                    <div style="margin-top:6px;color:var(--ink-4);font-size:var(--fs-micro);">
                        已等待 ${waited}s · 上限 ${limit}s${limit - waited < 30 ? ' · 即将超时' : ''}
                    </div>
                </div>
            </div>
            <div class="note note-muted" style="margin-top:10px;">
                <div style="line-height:1.8;">
                    登录窗口已经弹在你的屏幕上（任务栏里找 Chrome）。<br/>
                    抖音：右上角点「登录」→ 手机扫码；小红书：直接扫页面上的二维码。<br/>
                    <b>登录完成前别关那个窗口</b>——关掉就拿不到 cookie 了。
                </div>
            </div>`;
    }

    function closeLoginModal(ok) {
        stopLoginTimer();
        $('#crawlLoginModal').hidden = true;
        const flow = loginFlow;
        loginFlow = null;
        if (flow) flow.resolve(!!ok);
        if (ok) loadEngine();
    }

    function setupLoginModal() {
        $('#clmCancel').addEventListener('click', async () => {
            try { await api('/api/crawl/login/cancel', null, { method: 'POST' }); } catch (e) { /* 关窗失败也要放用户走 */ }
            closeLoginModal(false);
        });
        $('#clmFocus').addEventListener('click', async () => {
            try {
                await api('/api/crawl/login/focus', null, { method: 'POST' });
                toast('已把登录窗口切到前面', 'ok');
            } catch (e) {
                toast('找不到登录窗口：' + e.message, 'err');
            }
        });
        $('#clmRetry').addEventListener('click', () => {
            if (loginFlow) launchLogin(loginFlow.platform);
        });
        $('#clmCookieSave').addEventListener('click', async () => {
            if (!loginFlow) return;
            const cookie = $('#clmCookie').value.trim();
            if (!cookie) { toast('请先粘贴 cookie', 'err'); return; }
            try {
                await api('/api/crawl/login', null, {
                    method: 'POST',
                    body: JSON.stringify({ platform: loginFlow.platform, cookie })
                });
                toast('登录态已保存', 'ok');
                closeLoginModal(true);
            } catch (e) {
                $('#clmErr').textContent = '保存失败：' + e.message;
                $('#clmErr').hidden = false;
            }
        });
        // 点遮罩不关：关掉弹窗等于放弃等待，误触代价是整轮采集作废
    }

    async function doStart(keyword, target, dateFrom, dateTo, platforms) {
        const btn = $('#btnCrawlStart');
        const plats = (platforms && platforms.length) ? platforms : cw.platformSel;
        // 记一份参数：登录失效后点「重新登录并继续」，要能原样重跑，
        // 而不是让用户重新选平台、重填关键词——他已经表达过要采什么了。
        cw.lastRun = { keyword, target, from: dateFrom, to: dateTo, platforms: plats.slice() };
        cw.starting = true;
        btn.disabled = true;
        btn.textContent = '启动中…';
        try {
            const d = await api('/api/crawl/start', null, {
                method: 'POST',
                body: JSON.stringify({
                    keyword, platforms: plats, target,
                    date_from: dateFrom || '', date_to: dateTo || ''
                })
            });
            cw.sid = d.session_id;
            cw.keyword = keyword;
            cw.cursor = 0;
            cw.cleaned = null;
            cw.rowsSeen = 0;
            $('#crawlFeed').innerHTML = feedShell();
            renderRunning(d);
            $('#btnCrawlStop').classList.remove('is-hidden');
            cw.timer = setInterval(poll, 1000);
        } catch (e) {
            toast('启动失败：' + e.message, 'err');
            $('#liveState').innerHTML = `<div class="note note-warn">启动失败：${esc(e.message)}</div>`;
            btn.disabled = false;
            btn.textContent = '开始采集';
        } finally {
            cw.starting = false;
        }
    }

    function feedShell() {
        return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
                <span style="font-size:var(--fs-sm);font-weight:600;color:var(--ink);">实时候选集</span>
                <span style="font-size:var(--fs-micro);color:var(--ink-4);" id="feedCount">0 条</span>
                <span style="margin-left:auto;font-size:var(--fs-micro);color:var(--ink-4);">正文已脱敏 · 最新的在最上面</span>
            </div>
            <div class="table-scroll" style="max-height:320px;overflow:auto;">
                <table class="table"><caption class="sr-only">导入预览：逐条评论的清洗结果</caption><thead><tr>
                    <th scope="col" style="width:74px;">平台</th><th scope="col">评论正文</th>
                    <th scope="col" style="width:140px;">发布时间</th><th scope="col" style="width:64px;">点赞</th>
                </tr></thead><tbody id="feedBody"></tbody></table>
            </div>`;
    }

    async function poll() {
        if (!cw.sid) return;
        try {
            const d = await api('/api/crawl/' + cw.sid, { cursor: cw.cursor });
            cw.cursor = d.cursor;
            appendRows(d.rows);
            renderRunning(d);
            if (d.status !== 'running') {
                clearInterval(cw.timer);
                cw.timer = null;
                $('#btnCrawlStop').classList.add('is-hidden');
                const btn = $('#btnCrawlStart');
                btn.disabled = false;
                // 按钮文字保持"开始采集"：真正的"重新采集"在结果区里，
                // 两处都写成"重新采集"会让人以为是两个不同的操作。
                btn.textContent = '开始采集';
                renderFinished(d);
            }
        } catch (e) {
            clearInterval(cw.timer);
            cw.timer = null;
            // 轮询断了（服务重启 / 会话过期）也要把按钮放回来：
            // 否则页面停在"启动中…"且按钮灰着，用户既看不到结果也没法重来。
            const btn = $('#btnCrawlStart');
            btn.disabled = false;
            btn.textContent = '开始采集';
            $('#btnCrawlStop').classList.add('is-hidden');
            $('#liveState').innerHTML = `<div class="note note-warn">采集状态读取失败：${esc(e.message)}</div>`;
        }
    }

    function appendRows(rows) {
        if (!rows || !rows.length) return;
        const body = $('#feedBody');
        if (!body) return;
        const html = rows.slice().reverse().map(r => `<tr>
            <td><span class="badge ${r.platform === '小红书' ? 'badge-neg' : 'badge-brand'}">${esc(r.platform)}</span></td>
            <td style="font-size:var(--fs-sm);line-height:1.6;">${esc(r.content)}</td>
            <td style="font-size:var(--fs-micro);color:var(--ink-4);">${esc(fmtTime(r.create_time))}</td>
            <td class="num">${num(r.like_count)}</td>
        </tr>`).join('');
        body.insertAdjacentHTML('afterbegin', html);
        // 只留最近 150 行：采集几百条时 DOM 全留着会越滚越卡，
        // 而"最新的在最上面"已经保证用户看到的是正在进来的数据。
        while (body.children.length > 150) body.removeChild(body.lastElementChild);
        cw.rowsSeen += rows.length;
        const c = $('#feedCount');
        if (c) c.textContent = `${cw.rowsSeen} 条`;
    }

    function fmtTime(t) {
        if (t == null || t === '') return '—';
        if (typeof t === 'number' || /^\d{10,13}$/.test(String(t))) {
            const n = Number(t);
            const d = new Date(n < 1e12 ? n * 1000 : n);
            if (!isNaN(d.getTime())) {
                const p = x => String(x).padStart(2, '0');
                return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
            }
        }
        return String(t).slice(0, 16);
    }

    function renderRunning(d) {
        const pctDone = Math.min(100, Math.round(d.collected / Math.max(1, d.target) * 100));
        const running = d.status === 'running';
        const startBtn = $('#btnCrawlStart');
        if (running) {
            // 原来条件是 `running && !startBtn.disabled`，但 doStart 已经把按钮禁用了，
            // 于是这里永远不执行——按钮一路停在"启动中…"，看着像没启动成功。
            startBtn.disabled = true;
            startBtn.textContent = '采集中…';
        }
        const plat = Object.entries(d.per_platform || {})
            .map(([k, v]) => `${esc(k)} ${num(v)}`).join(' · ') || '—';
        const ranged = !!(d.date_from || d.date_to);
        const logs = (d.log || []).slice(-5).map(x =>
            `<div><b>${esc(x.at)}</b> ${esc(x.msg)}</div>`).join('');
        $('#liveState').innerHTML = `
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                ${running ? '<span class="badge badge-run">采集中</span>' : statusBadge(d.status)}
                <span style="font-size:var(--fs-sm);color:var(--ink-2);">
                    <b style="font-variant-numeric:tabular-nums;">${num(d.collected)}</b>
                    <span style="color:var(--ink-4);"> / ${num(d.target)} 条</span></span>
                <span style="font-size:var(--fs-micro);color:var(--ink-4);">平台分布：${plat}</span>
                <span style="font-size:var(--fs-micro);color:var(--ink-4);margin-left:auto;">
                    已用 ${d.elapsed}s${running ? ' · 点「停止」结束' : ''}</span>
            </div>
            <div class="live-bar"><i style="width:${pctDone}%"></i></div>
            <div class="live-meta">
                <span class="live-dot" style="${running ? '' : 'display:none'}"></span>
                <span>关键词「${esc(d.keyword)}」</span>
                ${ranged ? `<span>时间段 ${esc(d.date_from || '不限')} ~ ${esc(d.date_to || '不限')}</span>` : ''}
                ${d.raw_seen && d.raw_seen !== d.collected
                    ? `<span>平台共返回 ${num(d.raw_seen)} 条</span>` : ''}
                ${ranged && d.dropped_range
                    ? `<span style="color:var(--warn-ink);">区间外已跳过 ${num(d.dropped_range)} 条</span>` : ''}
                ${Object.entries(d.errors || {}).length
                    ? `<span style="color:var(--neg-ink);">${Object.entries(d.errors).map(([k, v]) => esc(k + '：' + v)).join('；')}</span>` : ''}
            </div>
            ${logs ? `<div class="live-log">${logs}</div>` : ''}`;
    }

    function statusBadge(s) {
        const map = {
            done: ['badge-pos', '已完成'],
            stopped: ['badge-warn', '已手动停止'],
            failed: ['badge-neg', '采集失败'],
        };
        const [cls, text] = map[s] || ['badge', s];
        return `<span class="badge ${cls}">${text}</span>`;
    }

    function renderFinished(d) {
        // 这两个码的根因都是"登录态没了"，能用重新扫码解决。
        // 别的码（关键词太窄、验证码、时间区间太老）重登录没用，不给这个按钮。
        // 除总 error_code，还要看每个平台自己的码：两个平台一起采时，
        // 抖音成功、小红书因登录失效失败，总体 status 仍是 done、error_code 为空，
        // 只判总码会漏掉"小红书该重登录"这件事。
        const AUTH_CODES = ['need_login', 'no_comment_api'];
        const platCodes = Object.values(d.error_codes || {});
        const reloginOk = (AUTH_CODES.includes(d.error_code)
            || platCodes.some(c => AUTH_CODES.includes(c))) && !!cw.lastRun;
        const failed = Object.entries(d.errors || {});

        if (d.status === 'failed') {
            const diag = d.diag || {};
            const hint = (d.error_code === 'need_login')
                ? '登录态已失效。点下面的「重新登录并继续」，扫码成功后会自动接着采，参数不用重填。'
                : (d.error_code === 'blocked')
                    ? '平台弹了验证码。可先在有头模式（CP_CRAWL_HEADFUL=1）下用同一条 cookie 手动过一次验证，再回来重试；'
                      + 'headless 更容易被判定为自动化。'
                    : (d.error_code === 'no_comment_api')
                        ? '登录态可能已过期：点「重新登录并继续」重扫一次即可。'
                        : (d.error_code === 'empty_search')
                            ? '换个更常见的说法当关键词（例如"东软 宿舍"而不是全称）。'
                            : (d.error_code === 'out_of_range')
                                ? '把时间段的起止放宽一些，或者先不设时间段采一轮看看平台能翻到多早。'
                                : '';
            $('#liveState').innerHTML = `
                <div class="note note-warn">
                    <b>采集失败：</b>${esc(d.error || '未知原因')}
                    ${hint ? '<br/>' + esc(hint) : ''}
                    ${diag.url ? `<br/><span style="opacity:.75;">停留页面：${esc(diag.title || '')} ${esc(String(diag.url).slice(0, 80))}</span>` : ''}
                    ${diag.text ? `<br/><span style="opacity:.6;">页面文案：${esc(String(diag.text).slice(0, 120))}</span>` : ''}
                </div>
                <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;">
                    ${reloginOk ? `<button class="btn btn-primary btn-sm" id="btnRelogin">重新登录并继续</button>` : ''}
                    <button class="btn btn-sm" id="btnCrawlAgain">重新采集</button>
                </div>`;
            const again = $('#btnCrawlAgain');
            if (again) again.addEventListener('click', () => resetCrawl());
            const relogin = $('#btnRelogin');
            if (relogin) relogin.addEventListener('click', () => retryAfterLogin(d));
            return;
        }
        if (!d.collected) {
            $('#liveState').innerHTML = `<div class="note note-warn">
                ${d.status === 'stopped' ? '已手动停止' : '本次没有采集到任何评论'}，没有可清洗的内容。
                ${(d.date_from || d.date_to) ? '如果设了时间段，可能是翻到的评论都不在区间内。' : ''}
            </div>`;
            return;
        }
        // 一个平台成功、另一个失败时，总体状态是 done —— 如果不在这里说，
        // 用户只会看到"共 20 条"，以为两个平台都采到了。失败信息必须露出来。
        const partial = failed.length ? `
            <div class="note note-warn" style="margin-bottom:10px;">
                <b>有平台没采到：</b>${failed.map(([k, v]) => esc(k + '：' + v)).join('；')}<br/>
                ${reloginOk
                    ? '登录态可能已过期。点「重新登录并继续」会先弹登录窗，成功后按原参数重跑'
                      + '（新一轮会重新采集，已采到的候选会被覆盖）。'
                    : '可以先清洗入库其余平台的候选，修好后再补采这个平台。'}
            </div>` : '';
        $('#liveState').innerHTML = `
            <div class="live-meta" style="margin-bottom:8px;">
                ${statusBadge(d.status)}
                <span>共 <b style="color:var(--ink);">${num(d.collected)}</b> 条候选</span>
                <span>平台分布：${Object.entries(d.per_platform || {}).map(([k, v]) => `${esc(k)} ${num(v)}`).join(' · ') || '—'}</span>
                ${(d.date_from || d.date_to)
                    ? `<span>时间段 ${esc(d.date_from || '不限')} ~ ${esc(d.date_to || '不限')}</span>`
                    : '<span>时间段：不限</span>'}
                ${d.dropped_range
                    ? `<span style="color:var(--warn-ink);">区间外已跳过 ${num(d.dropped_range)} 条</span>` : ''}
                <span>耗时 ${d.elapsed}s</span>
            </div>
            ${partial}
            <div class="note note-muted" style="margin-bottom:10px;">
                下一步是清洗：剔除重复、空内容、无法解析时间、无效短评与校园无关的内容，
                然后把「通过」和「剔除」逐条摆出来，由你决定要不要并入数据集。
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <button class="btn btn-primary" id="btnClean">开始清洗</button>
                ${reloginOk ? `<button class="btn" id="btnRelogin">重新登录并继续</button>` : ''}
                <button class="btn" id="btnCrawlAgain">重新采集</button>
            </div>`;
        $('#btnClean').addEventListener('click', startClean);
        $('#btnCrawlAgain').addEventListener('click', () => resetCrawl());
        const relogin2 = $('#btnRelogin');
        if (relogin2) relogin2.addEventListener('click', () => retryAfterLogin(d));
    }

    function resetCrawl() {
        cw.sid = null;
        cw.cursor = 0;
        cw.cleaned = null;
        cw.rowsSeen = 0;
        $('#crawlFeed').innerHTML = '';
        $('#liveState').innerHTML = '';
        const btn = $('#btnCrawlStart');
        btn.disabled = false;
        btn.textContent = '开始采集';
        $('#btnCrawlStop').classList.add('is-hidden');
    }

    /** 登录态失效后：重新扫码，成功后按原参数接着采。
     *  不这么做的话，用户得自己回到上面重配 cookie，再重新选平台、重填关键词——
     *  而他刚才已经表达过要采什么了，只是因为 cookie 过期才断的。 */
    async function retryAfterLogin(d) {
        const run = cw.lastRun;
        if (!run) { toast('没有可重跑的参数，请重新填写后开始采集', 'err'); return; }
        // 用会话自己的平台列表：它就是用户这次点的那些，比 cw.platformSel 可靠
        // （用户可能在失败后又改了平台勾选）。重跑会覆盖掉本轮已采到的候选，
        // 所以按钮文案要写明这一点，不能让人以为是在追加。
        const plats = (d.platforms && d.platforms.length) ? d.platforms : run.platforms;
        resetCrawl();
        const ok = await promptLoginAll(plats);
        if (!ok) { toast('已取消：登录没有完成，采集没有开始', 'err'); return; }
        await doStart(run.keyword, run.target, run.from, run.to, plats);
    }

    async function startClean() {
        const btn = $('#btnClean');
        btn.disabled = true;
        btn.textContent = '清洗中…';
        try {
            const d = await api('/api/crawl/' + cw.sid + '/clean', null, { method: 'POST' });
            cw.cleaned = d;
            renderClean(d);
        } catch (e) {
            toast('清洗失败：' + e.message, 'err');
            btn.disabled = false;
            btn.textContent = '开始清洗';
        }
    }

    function renderClean(d) {
        $('#btnCrawlStop').classList.add('is-hidden');
        const stages = (d.stages || []).map(s => `<tr>
            <td>${esc(s.stage)}</td>
            <td style="font-size:var(--fs-micro);color:var(--ink-4);">${esc(s.note || '')}</td>
            <td class="num" style="text-align:right;color:${(s.count || 0) < 0 ? 'var(--neg)' : 'var(--ink)'};">
                ${s.count < 0 ? '−' + num(Math.abs(s.count)) : num(s.count)}</td>
        </tr>`).join('');

        $('#liveState').innerHTML = `
            <div class="grid g-4" style="margin-bottom:12px;">
                <div class="metric"><div class="metric-name">候选（原始）</div>
                    <div class="metric-value">${num(d.raw_count)}<span class="metric-unit">条</span></div></div>
                <div class="metric"><div class="metric-name">清洗后有效</div>
                    <div class="metric-value">${num(d.valid_count)}<span class="metric-unit">条</span></div></div>
                <div class="metric"><div class="metric-name">有效率</div>
                    <div class="metric-value">${pct(d.effective_rate)}</div></div>
                <div class="metric"><div class="metric-name">点赞缺失</div>
                    <div class="metric-value">${num(d.missing_like || 0)}<span class="metric-unit">条</span></div></div>
            </div>
            <table class="table"><caption class="sr-only">清洗阶段与记录变化</caption><thead><tr><th scope="col">清洗阶段</th><th scope="col">规则说明</th>
                <th scope="col" style="text-align:right">记录变化</th></tr></thead><tbody>${stages}</tbody></table>
            ${d.too_short ? `<div class="note note-muted" style="margin-top:10px;">
                其中 <b>${num(d.too_short)}</b> 条是 1–4 字的短评论（“顶”“收到”“哈哈哈”“@好友”这类）：
                采集时<b>已经拿回来了</b>，只是按口径不计入分析。想看它们点下面的「剔除」。
            </div>` : ''}`;

        cw.sel = newSelection();
        $('#crawlFeed').innerHTML = `
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:14px 0 8px;">
                <span style="font-size:var(--fs-sm);font-weight:600;">清洗明细</span>
                <span id="rowTotal" style="font-size:var(--fs-micro);color:var(--ink-4);"></span>
                <div style="margin-left:auto;display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                    <span id="selSummary" style="font-size:var(--fs-micro);color:var(--ink-3);"></span>
                    <button type="button" class="btn btn-sm" id="selAll">全选</button>
                    <button type="button" class="btn btn-sm" id="selNone">全不选</button>
                    <div class="seg" id="rowSeg">
                        <button type="button" data-f="all" class="on">全部</button>
                        <button type="button" data-f="kept">通过</button>
                        <button type="button" data-f="rejected">剔除</button>
                    </div>
                </div>
            </div>
            <div id="rowTable"></div>
            <div id="commitBox" style="margin-top:14px;"></div>`;
        $$('#rowSeg [data-f]').forEach(b => b.addEventListener('click', () => {
            $$('#rowSeg button').forEach(x => x.classList.toggle('on', x === b));
            cw.filter = b.dataset.f;
            cw.page = 0;
            loadRows();
        }));
        $('#selAll').addEventListener('click', () => {
            pickAll(cw.sel); loadRows(); syncCommitSelection();
        });
        $('#selNone').addEventListener('click', () => {
            pickNone(cw.sel); loadRows(); syncCommitSelection();
        });
        cw.filter = 'all';
        cw.page = 0;
        loadRows();
        renderCommit(d);
    }

    async function loadRows() {
        const box = $('#rowTable');
        if (!box || !cw.sid) return;
        setState(box, 'loading');
        try {
            const p = await api('/api/crawl/' + cw.sid + '/rows', {
                status: cw.filter, offset: cw.page * 20, limit: 20
            });
            const total = p.total;
            $('#rowTotal').textContent = `共 ${num(total)} 条 · 第 ${cw.page + 1}/${Math.max(1, Math.ceil(total / 20))} 页`;
            if (!total) {
                box.innerHTML = '<div class="state">这一类没有记录</div>';
                return;
            }
            box.innerHTML = `<div class="table-scroll"><table class="table"><caption class="sr-only">导入结果：逐条评论的主题与结论，可勾选要加入数据集的评论</caption><thead><tr>
                    <th scope="col" style="width:36px;"><span class="sr-only">选择</span></th>
                    <th scope="col" style="width:70px;">平台</th><th scope="col">正文</th><th scope="col" style="width:120px;">时间</th>
                    <th scope="col" style="width:88px;">主题</th><th scope="col" style="width:120px;">结论</th>
                </tr></thead><tbody>
                ${p.rows.map(r => `<tr>
                    <td>${r.cleaning_status === 'kept'
                        ? `<input type="checkbox" class="row-pick" data-ref="${esc(r.comment_ref)}"
                                  aria-label="选择这条评论：${esc(r.content.slice(0, 24))}"
                                  ${isPicked(cw.sel, r.comment_ref) ? 'checked' : ''}>`
                        : '<span style="color:var(--ink-4);" title="清洗未通过，不计入分析，也不能选入数据集">—</span>'}</td>
                    <td style="font-size:var(--fs-micro);">${esc(r.platform)}</td>
                    <td style="font-size:var(--fs-sm);line-height:1.6;">${esc(r.content)}</td>
                    <td style="font-size:var(--fs-micro);color:var(--ink-4);">${esc((r.comment_time || '—').slice(0, 16))}</td>
                    <td style="font-size:var(--fs-micro);color:var(--ink-3);">${esc(r.dimension || '—')}</td>
                    <td>${r.cleaning_status === 'kept'
                        ? `<span class="badge badge-pos">${esc(sentimentLabel(r.sentiment))}</span>`
                        : `<span class="badge badge-neg" title="${esc(r.filter_reason || '')}">${esc(filterLabel(r.cleaning_status))}</span>`}</td>
                </tr>`).join('')}
                </tbody></table></div>
                ${total > 20 ? `<div style="display:flex;gap:8px;margin-top:8px;">
                    <button class="btn btn-sm" id="rowPrev" ${cw.page === 0 ? 'disabled' : ''}>上一页</button>
                    <button class="btn btn-sm" id="rowNext" ${(cw.page + 1) * 20 >= total ? 'disabled' : ''}>下一页</button>
                </div>` : ''}`;
            box.querySelectorAll('input[data-ref]').forEach(cb => {
                cb.addEventListener('change', () => {
                    setPicked(cw.sel, cb.dataset.ref, cb.checked);
                    syncCommitSelection();
                });
            });
            const prev = $('#rowPrev'), next = $('#rowNext');
            if (prev) prev.addEventListener('click', () => { cw.page = Math.max(0, cw.page - 1); loadRows(); });
            if (next) next.addEventListener('click', () => { cw.page += 1; loadRows(); });
            syncCommitSelection();
        } catch (e) {
            box.innerHTML = `<div class="note note-warn">明细加载失败：${esc(e.message)}</div>`;
        }
    }

    function sentimentLabel(s) {
        return { positive: '正面', neutral: '中性', negative: '负面' }[s] || '中性';
    }

    function filterLabel(s) {
        return {
            duplicate: '重复', empty: '空内容', invalid_time: '时间无效',
            too_short: '无效短评', irrelevant: '非校园主题',
        }[s] || '已剔除';
    }

    /** 入库目标：默认新建，也可以并进一个已有数据集。
     *  并库时重复记录由服务端按「平台 + 正文」跳过并回报条数，前端不预判。 */
    function targetOptions(selected) {
        const list = state.datasets || [];
        return `<option value="">＋ 新建一个数据集</option>` + list.map(d =>
            `<option value="${esc(d.dataset_id)}"${d.dataset_id === selected ? ' selected' : ''}>`
            + `并入：${esc(d.name)} · 现有 ${num(d.valid_count)} 条${d.is_demo ? '（内置）' : ''}</option>`
        ).join('');
    }

    /** 选「新建」才显示命名框；选已有数据集时把命名框收起来，避免让人以为改名了。 */
    function wireTargetPicker(sel, nameRow) {
        if (!sel) return;
        const sync = () => {
            if (nameRow) nameRow.classList.toggle('is-hidden', !!sel.value);
        };
        sel.addEventListener('change', sync);
        sync();
    }

    /** 入库前的确认区：这是用户明确要求的"问一句要不要加进数据集"。
     *  默认不勾选、不自动提交——采集到的东西要不要成为分析口径，得由人决定。 */
    function renderCommit(d) {
        const box = $('#commitBox');
        if (!box) return;
        cw.cleaned = d;
        if (!d.valid_count) {
            box.innerHTML = `<div class="note note-warn">
                清洗后没有有效评论，不能入库。本次采集作废，不会在数据集列表里留下空壳。</div>`;
            return;
        }
        const suggest = cw.keyword
            ? `实时采集 · ${cw.keyword} · ${new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).replace(/\//g, '-')}`
            : '实时采集';
        box.innerHTML = `
            <div style="border:1px solid var(--brand-line);background:var(--brand-soft);border-radius:12px;padding:12px 14px;">
                <div style="font-size:var(--fs-sm);font-weight:650;color:var(--brand-ink);" id="commitTitle"></div>
                <div style="font-size:var(--fs-xs);color:var(--ink-2);line-height:1.7;margin-top:4px;">
                    只写入勾选的评论（清洗通过 ${num(d.valid_count)} 条，当前勾选
                    <b id="commitPicked">${num(pickedCount(cw.sel, d.valid_count))}</b> 条）；
                    未勾选的通过项不会入库，被剔除的记录仍保留原因用于口径追溯。
                    新建会另起一个数据集并切换过去；并入已有数据集只追加、不覆盖，重复的按「平台 + 正文」跳过
                    （原始 ${num(d.raw_count)} 条 → 有效 ${num(d.valid_count)} 条，${pct(d.effective_rate)}）；
                    放弃则什么都不写库，本次结果只留在当前页面。
                </div>
                <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;align-items:center;">
                    <select class="select" id="commitTarget" style="flex:1;min-width:230px;font-size:var(--fs-sm);">
                        ${targetOptions('')}</select>
                </div>
                <div id="commitNewRow" style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;align-items:center;">
                    <input class="input" id="commitName" style="flex:1;min-width:200px;font-size:var(--fs-sm);"
                           value="${esc(suggest)}" maxlength="60" placeholder="数据集名称，例如：实时采集 · 宿舍 · 11-07">
                </div>
                <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;">
                    <button class="btn btn-primary" id="btnCommit">加入数据集</button>
                    <button class="btn" id="btnDiscard">放弃本次</button>
                </div>
            </div>`;
        syncCommitSelection();
        wireTargetPicker($('#commitTarget'), $('#commitNewRow'));
        $('#btnCommit').addEventListener('click', commitCrawl);
        $('#btnDiscard').addEventListener('click', async () => {
            const ok = await CP.confirmModal({
                title: '放弃本次采集结果？',
                subHtml: '页面上的候选集与清洗结果都会清空，<b>不会写入数据库</b>。已经入库的历史不受影响。',
                okLabel: '放弃本次'
            });
            if (!ok) return;
            resetCrawl();
            $('#crawlKeyword').value = '';
            toast('已放弃本次采集', 'ok');
        });
    }

    function syncCommitSelection() {
        const d = cw.cleaned;
        if (!d) return;
        const picked = pickedCount(cw.sel, d.valid_count);
        const summary = $('#selSummary');
        if (summary) summary.textContent = `已选 ${num(picked)} / ${num(d.valid_count)} 条`;
        const title = $('#commitTitle');
        if (title) title.textContent = picked
            ? `要把选中的 ${num(picked)} 条加入数据集吗？`
            : '还没有勾选任何评论';
        const pickedEl = $('#commitPicked');
        if (pickedEl) pickedEl.textContent = num(picked);
        const btn = $('#btnCommit');
        if (btn) btn.disabled = !picked;
    }

    async function commitCrawl() {
        const btn = $('#btnCommit');
        const sel = /** @type {HTMLSelectElement|null} */ ($('#commitTarget'));
        const nameEl = /** @type {HTMLInputElement|null} */ ($('#commitName'));
        const target = sel ? sel.value : '';
        const name = target ? '' : (nameEl ? nameEl.value : '').trim();
        const picked = cw.cleaned ? pickedCount(cw.sel, cw.cleaned.valid_count) : 0;
        const valid = cw.cleaned ? cw.cleaned.valid_count : 0;
        if (!picked) { toast('请至少勾选一条评论', 'err'); return; }
        btn.disabled = true;
        btn.textContent = '入库中…';
        try {
            const d = await api('/api/crawl/' + cw.sid + '/commit', null, {
                method: 'POST',
                body: JSON.stringify({
                    name, target_dataset_id: target || null,
                    ...selectionPayload(cw.sel)
                })
            });
            await CP.switchDataset(d.dataset_id);
            $('#commitBox').innerHTML = d.merged
                ? `<div class="note" style="background:var(--pos-soft);color:var(--pos-ink);">
                    已并入数据集：<b>${esc(d.name)}</b> · 本次勾选 ${num(picked)}/${num(valid)} 条，
                    新增 ${num(d.added)} 条，跳过重复 ${num(d.skipped)} 条，
                    现在共 <b>${num(d.valid_count)}</b> 条有效评论（时间范围 ${esc((d.time_span || []).join(' ~ ') || '—')}）。
                    已切换到这份数据集，<a href="/data">去看清洗链路</a>。
                </div>`
                : `<div class="note" style="background:var(--pos-soft);color:var(--pos-ink);">
                    已加入数据集：<b>${esc(d.name)}</b> · 本次勾选 ${num(picked)}/${num(valid)} 条，
                    原始 ${num(d.raw_count)} → 有效 ${num(d.valid_count)} 条
                    （${pct(d.effective_rate)}），时间范围 ${esc((d.time_span || []).join(' ~ ') || '—')}。
                    已自动切换到这份数据集，<a href="/data">去看清洗链路</a>，或直接
                    <a href="/data/reports">生成报告</a>。
                </div>`;
            toast(d.merged ? '已并入所选数据集' : '已入库并切换数据集', 'ok');
            $('#btnCrawlStop').classList.add('is-hidden');
        } catch (e) {
            toast('入库失败：' + e.message, 'err');
            btn.disabled = false;
            btn.textContent = '加入数据集';
        }
    }

    /* ==================== 待确认批次（文件 / 截图共用） ==================== */
    // 三条入口的最后一步长得完全一样：清洗漏斗 → 逐条明细 → 由人确认要不要入库。
    // 所以只写一套渲染，文件导入和截图识别各自拿到自己的容器。
    // 会话状态挂在宿主元素上（__sid/__filter/__page），两种导入同时进行也互不干扰。
    function intakeMetrics(d) {
        return `<div class="grid g-4" style="margin-bottom:12px;">
            <div class="metric"><div class="metric-name">候选（原始）</div>
                <div class="metric-value">${num(d.raw_count)}<span class="metric-unit">条</span></div></div>
            <div class="metric"><div class="metric-name">清洗后有效</div>
                <div class="metric-value">${num(d.valid_count)}<span class="metric-unit">条</span></div></div>
            <div class="metric"><div class="metric-name">有效率</div>
                <div class="metric-value">${pct(d.effective_rate)}</div></div>
            <div class="metric"><div class="metric-name">点赞缺失</div>
                <div class="metric-value">${num(d.missing_like || 0)}<span class="metric-unit">条</span></div></div>
        </div>`;
    }

    function intakeStages(d) {
        const rows = (d.stages || []).map(s => `<tr>
            <td>${esc(s.stage)}</td>
            <td style="font-size:var(--fs-micro);color:var(--ink-4);">${esc(s.note || '')}</td>
            <td class="num" style="text-align:right;color:${(s.count || 0) < 0 ? 'var(--neg)' : 'var(--ink)'};">
                ${s.count < 0 ? '−' + num(Math.abs(s.count)) : num(s.count)}</td>
        </tr>`).join('');
        return `<table class="table"><caption class="sr-only">清洗阶段与记录变化</caption><thead><tr><th scope="col">清洗阶段</th><th scope="col">规则说明</th>
            <th scope="col" style="text-align:right">记录变化</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    function renderIntake(host, d) {
        host.__sid = d.session_id;
        host.__filter = 'all';
        host.__page = 0;
        host.__sel = newSelection();
        host.__summary = d;
        host.hidden = false;
        host.innerHTML = `
            ${intakeMetrics(d)}
            ${intakeStages(d)}
            ${d.too_short ? `<div class="note note-muted" style="margin-top:10px;">
                其中 <b>${num(d.too_short)}</b> 条是 1–4 字的短评论（“顶”“收到”“哈哈哈”这类）：
                文件里<b>确实有</b>，只是按口径不计入分析。想看它们点下面的「剔除」。
            </div>` : ''}
            ${(d.rejected && d.rejected.length) ? `<div class="note note-warn" style="margin-top:10px;">
                这些文件没有进入清洗：${d.rejected.map(r => esc(r.file + '（' + r.reason + '）')).join('；')}
            </div>` : ''}
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:14px 0 8px;">
                <span style="font-size:var(--fs-sm);font-weight:600;">清洗明细</span>
                <span data-role="total" style="font-size:var(--fs-micro);color:var(--ink-4);"></span>
                <div style="margin-left:auto;display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                    <span data-role="sel-summary" style="font-size:var(--fs-micro);color:var(--ink-3);"></span>
                    <button type="button" class="btn btn-sm" data-role="sel-all">全选</button>
                    <button type="button" class="btn btn-sm" data-role="sel-none">全不选</button>
                    <div class="seg" data-role="seg">
                        <button type="button" data-f="all" class="on">全部</button>
                        <button type="button" data-f="kept">通过</button>
                        <button type="button" data-f="rejected">剔除</button>
                    </div>
                </div>
            </div>
            <div data-role="table"></div>
            <div data-role="commit" style="margin-top:14px;"></div>`;

        host.querySelectorAll('[data-role="seg"] button').forEach(b => {
            b.addEventListener('click', () => {
                host.querySelectorAll('[data-role="seg"] button')
                    .forEach(x => x.classList.toggle('on', x === b));
                host.__filter = b.dataset.f;
                host.__page = 0;
                loadIntakeRows(host);
            });
        });
        host.querySelector('[data-role="sel-all"]').addEventListener('click', () => {
            pickAll(host.__sel); loadIntakeRows(host); syncIntakeSelection(host);
        });
        host.querySelector('[data-role="sel-none"]').addEventListener('click', () => {
            pickNone(host.__sel); loadIntakeRows(host); syncIntakeSelection(host);
        });
        loadIntakeRows(host);
        renderIntakeCommit(host, d);
    }

    async function loadIntakeRows(host) {
        const box = host.querySelector('[data-role="table"]');
        const totalEl = host.querySelector('[data-role="total"]');
        const sid = host.__sid;
        if (!box || !sid) return;
        setState(box, 'loading');
        try {
            const p = await api('/api/import/' + sid + '/rows', {
                status: host.__filter, offset: host.__page * 20, limit: 20
            });
            const total = p.total;
            totalEl.textContent = `共 ${num(total)} 条 · 第 ${host.__page + 1}/${Math.max(1, Math.ceil(total / 20))} 页`;
            if (!total) {
                box.innerHTML = '<div class="state"><div class="state-title">这一类没有记录</div></div>';
                return;
            }
            box.innerHTML = `<div class="table-scroll"><table class="table"><caption class="sr-only">导入结果：逐条评论的主题与结论，可勾选要加入数据集的评论</caption><thead><tr>
                    <th scope="col" style="width:36px;"><span class="sr-only">选择</span></th>
                    <th scope="col" style="width:70px;">平台</th><th scope="col">正文</th><th scope="col" style="width:120px;">时间</th>
                    <th scope="col" style="width:88px;">主题</th><th scope="col" style="width:120px;">结论</th>
                </tr></thead><tbody>
                ${p.rows.map(r => `<tr>
                    <td>${r.cleaning_status === 'kept'
                        ? `<input type="checkbox" class="row-pick" data-ref="${esc(r.comment_ref)}"
                                  aria-label="选择这条评论：${esc(r.content.slice(0, 24))}"
                                  ${isPicked(host.__sel, r.comment_ref) ? 'checked' : ''}>`
                        : '<span style="color:var(--ink-4);" title="清洗未通过，不计入分析，也不能选入数据集">—</span>'}</td>
                    <td style="font-size:var(--fs-micro);">${esc(r.platform)}</td>
                    <td style="font-size:var(--fs-sm);line-height:1.6;">${esc(r.content)}</td>
                    <td style="font-size:var(--fs-micro);color:var(--ink-4);">${esc((r.comment_time || '—').slice(0, 16))}</td>
                    <td style="font-size:var(--fs-micro);color:var(--ink-3);">${esc(r.dimension || '—')}</td>
                    <td>${r.cleaning_status === 'kept'
                        ? `<span class="badge badge-pos">${esc(sentimentLabel(r.sentiment))}</span>`
                        : `<span class="badge badge-neg" title="${esc(r.filter_reason || '')}">${esc(filterLabel(r.cleaning_status))}</span>`}</td>
                </tr>`).join('')}
                </tbody></table></div>
                ${total > 20 ? `<div style="display:flex;gap:8px;margin-top:8px;">
                    <button class="btn btn-sm" data-page="prev" ${host.__page === 0 ? 'disabled' : ''}>上一页</button>
                    <button class="btn btn-sm" data-page="next" ${(host.__page + 1) * 20 >= total ? 'disabled' : ''}>下一页</button>
                </div>` : ''}`;
            box.querySelectorAll('input[data-ref]').forEach(cb => {
                cb.addEventListener('change', () => {
                    setPicked(host.__sel, cb.dataset.ref, cb.checked);
                    syncIntakeSelection(host);
                });
            });
            const prev = box.querySelector('[data-page="prev"]');
            const next = box.querySelector('[data-page="next"]');
            if (prev) prev.addEventListener('click', () => { host.__page = Math.max(0, host.__page - 1); loadIntakeRows(host); });
            if (next) next.addEventListener('click', () => { host.__page += 1; loadIntakeRows(host); });
            syncIntakeSelection(host);
        } catch (e) {
            box.innerHTML = `<div class="note note-warn">明细加载失败：${esc(e.message)}</div>`;
        }
    }

    /** 确认区：默认不提交、名称可改——这批数据要不要成为分析口径，得由人点。 */
    function renderIntakeCommit(host, d) {
        const box = host.querySelector('[data-role="commit"]');
        if (!box) return;
        if (!d.valid_count) {
            box.innerHTML = `<div class="note note-warn">
                清洗后没有有效评论，不能入库。这批文件不会被写进数据集列表。</div>`;
            return;
        }
        box.innerHTML = `
            <div style="border:1px solid var(--brand-line);background:var(--brand-soft);border-radius:12px;padding:12px 14px;">
                <div style="font-size:var(--fs-sm);font-weight:650;color:var(--brand-ink);"
                     data-role="commit-title"></div>
                <div style="font-size:var(--fs-xs);color:var(--ink-2);line-height:1.7;margin-top:4px;">
                    只写入勾选的评论（清洗通过 ${num(d.valid_count)} 条，当前勾选
                    <b data-role="commit-picked">${num(pickedCount(host.__sel, d.valid_count))}</b> 条）；
                    未勾选的通过项不会入库，被剔除的记录仍保留原因用于口径追溯。
                    新建会另起一个数据集并切换过去；并入已有数据集只追加、不覆盖，重复的按「平台 + 正文」跳过
                    （原始 ${num(d.raw_count)} 条 → 有效 ${num(d.valid_count)} 条，${pct(d.effective_rate)}）；
                    放弃则什么都不写库，暂存文件会被删掉。
                </div>
                <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;align-items:center;">
                    <select class="select" data-role="target" style="flex:1;min-width:230px;font-size:var(--fs-sm);">
                        ${targetOptions('')}</select>
                </div>
                <div data-role="newrow" style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;align-items:center;">
                    <input class="input" data-role="name" style="flex:1;min-width:200px;font-size:var(--fs-sm);"
                           value="${esc(d.suggest_name || '')}" maxlength="60" placeholder="数据集名称">
                </div>
                <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;">
                    <button class="btn btn-primary" data-role="ok">确认导入</button>
                    <button class="btn" data-role="drop">放弃这批</button>
                </div>
            </div>`;
        syncIntakeSelection(host);
        wireTargetPicker(box.querySelector('[data-role="target"]'), box.querySelector('[data-role="newrow"]'));
        box.querySelector('[data-role="ok"]').addEventListener('click', () => commitIntake(host, box));
        box.querySelector('[data-role="drop"]').addEventListener('click', () => discardIntake(host));
    }

    function syncIntakeSelection(host) {
        const d = host.__summary;
        if (!d || !host.__sel) return;
        const picked = pickedCount(host.__sel, d.valid_count);
        const summary = host.querySelector('[data-role="sel-summary"]');
        if (summary) summary.textContent = `已选 ${num(picked)} / ${num(d.valid_count)} 条`;
        const title = host.querySelector('[data-role="commit-title"]');
        if (title) title.textContent = picked
            ? `要把选中的 ${num(picked)} 条加入数据集吗？`
            : '还没有勾选任何评论';
        const pickedEl = host.querySelector('[data-role="commit-picked"]');
        if (pickedEl) pickedEl.textContent = num(picked);
        const btn = host.querySelector('[data-role="ok"]');
        if (btn) btn.disabled = !picked;
    }

    async function commitIntake(host, box) {
        const btn = box.querySelector('[data-role="ok"]');
        const target = box.querySelector('[data-role="target"]').value || '';
        const name = target ? '' : (box.querySelector('[data-role="name"]').value || '').trim();
        const picked = pickedCount(host.__sel, host.__summary.valid_count);
        const valid = host.__summary.valid_count;
        if (!picked) { toast('请至少勾选一条评论', 'err'); return; }
        btn.disabled = true;
        btn.textContent = '入库中…';
        try {
            const d = await api('/api/import/' + host.__sid + '/commit', null, {
                method: 'POST',
                body: JSON.stringify({
                    name, target_dataset_id: target || null,
                    ...selectionPayload(host.__sel)
                })
            });
            const extra = d.screenshot_count
                ? `，其中截图来源 ${num(d.screenshot_count)} 条（无原文时间 ${num(d.time_inferred || 0)} 条）`
                : '';
            host.innerHTML = d.merged
                ? `<div class="note" style="background:var(--pos-soft);color:var(--pos-ink);">
                    已并入数据集：<b>${esc(d.name)}</b> · 本次勾选 ${num(picked)}/${num(valid)} 条，
                    新增 ${num(d.added)} 条，跳过重复 ${num(d.skipped)} 条${extra}，
                    现在共 <b>${num(d.valid_count)}</b> 条有效评论（时间范围 ${esc((d.time_span || []).join(' ~ ') || '—')}）。
                    已切换到这份数据集，<a href="/data">去看清洗链路</a>。
                </div>`
                : `<div class="note" style="background:var(--pos-soft);color:var(--pos-ink);">
                    已加入数据集：<b>${esc(d.name)}</b> · 本次勾选 ${num(picked)}/${num(valid)} 条，
                    原始 ${num(d.raw_count)} → 有效 ${num(d.valid_count)} 条
                    （${pct(d.effective_rate)}）${extra}，时间范围 ${esc((d.time_span || []).join(' ~ ') || '—')}。
                    已自动切换到这份数据集，<a href="/data">去看清洗链路</a>，或直接
                    <a href="/data/reports">生成报告</a>。
                </div>`;
            toast(d.merged ? '已并入所选数据集' : '已入库并切换数据集', 'ok');
            await CP.switchDataset(d.dataset_id);
        } catch (e) {
            toast('入库失败：' + e.message, 'err');
            btn.disabled = false;
            btn.textContent = '确认导入';
        }
    }

    async function discardIntake(host) {
        const ok = await CP.confirmModal({
            title: '放弃这批数据？',
            subHtml: '清洗结果与暂存文件都会删掉，<b>不会写入数据库</b>。重新导入要再传一次文件。',
            okLabel: '放弃这批'
        });
        if (!ok) return;
        try {
            await api('/api/import/' + host.__sid + '/discard', null, { method: 'POST' });
        } catch (e) { /* 会话过期也算放弃成功，不必打扰用户 */ }
        host.innerHTML = '<div class="note note-muted">已放弃这批数据，没有写入任何内容。</div>';
        host.hidden = true;
        toast('已放弃', 'ok');
    }

    /* ==================== 文件导入 ==================== */
    function setupImport() {
        const zone = $('#dropZone'), input = $('#fileInput');
        zone.addEventListener('click', () => input.click());
        // 键盘等价：真正的 <input type=file> 是 display:none，进不了 Tab 序，
        // 所以『导入数据』这一步此前只有鼠标用户能完成。
        zone.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
        });
        ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, e => {
            e.preventDefault(); zone.classList.add('is-dragging');
        }));
        ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, e => {
            e.preventDefault(); zone.classList.remove('is-dragging');
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
        const host = $('#importReview');
        setState(box, 'loading');
        host.hidden = true;
        host.innerHTML = '';
        const fd = new FormData();
        Array.from(files).forEach(f => fd.append('files', f));
        try {
            const resp = await fetch('/api/import/files', { method: 'POST', body: fd });
            const body = await resp.json();
            if (!resp.ok || body.ok === false) {
                const msg = (body.error && body.error.message) || '导入失败';
                throw Object.assign(new Error(msg), { detail: body.error && body.error.detail });
            }
            const d = body.data;
            box.innerHTML = `<div class="note">
                已清洗 ${d.files.length} 个文件：原始 ${num(d.raw_count)} 条 → 有效 ${num(d.valid_count)} 条
                （${pct(d.effective_rate)}）。<b>还没入库</b>——下面逐条核对后，由你决定要不要加进数据集。
            </div>`;
            renderIntake(host, d);
        } catch (e) {
            const detail = e.detail ? (Array.isArray(e.detail)
                ? e.detail.map(x => esc(`${x.stage || x.file || ''}：${x.note || x.reason || ''}`)).join('<br/>')
                : esc(String(e.detail))) : '';
            box.innerHTML = `<div class="note note-warn">导入失败：${esc(e.message)}${detail ? '<br/>' + detail : ''}</div>`;
            toast('导入失败：' + e.message, 'err');
        } finally {
            $('#fileInput').value = '';
        }
    }

    /* ==================== 截图识别（多模态） ==================== */
    // 复核表在内存里维护：识别一次（1–2 秒/张）后，用户改字、取消勾选都在本地完成，
    // 只有点"确认导入"才写库。中途关掉页面不会留下半成品数据集。
    let visSession = null;
    let visRows = [];

    async function loadVisionStatus() {
        const badge = $('#visionEngine');
        if (!badge) return;
        try {
            const d = await api('/api/vision/status');
            if (!d.enabled) {
                badge.textContent = '未启用';
                badge.className = 'badge badge-warn';
                $('#visionDrop').classList.add('is-hidden');
                $('#visionStatus').innerHTML = '<div class="note note-warn">截图识别未启用（CP_VISION=0）。</div>';
                return;
            }
            if (!d.active) {
                badge.textContent = '无可用引擎';
                badge.className = 'badge badge-warn';
                $('#visionDrop').classList.add('is-hidden');
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
        zone.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
        });
        ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, e => {
            e.preventDefault(); zone.classList.add('is-dragging');
        }));
        ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, e => {
            e.preventDefault(); zone.classList.remove('is-dragging');
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
                <span style="font-size:var(--fs-sm);font-weight:600;color:var(--ink);">复核（${visRows.length} 条候选）</span>
                <span style="font-size:var(--fs-micro);color:var(--ink-4);">正文可直接改；不想要的取消勾选即可</span>
                <div style="margin-left:auto;display:flex;gap:6px;">
                    <button class="btn btn-sm" data-vsel="all">全选</button>
                    <button class="btn btn-sm" data-vsel="none">全不选</button>
                    <button class="btn btn-sm" data-vsel="low">只取消低置信度</button>
                    <button class="btn btn-primary btn-sm" data-vcommit>确认导入</button>
                </div>
            </div>
            <div class="table-scroll">
            <table class="table"><caption class="sr-only">截图识别结果，正文可编辑</caption><thead><tr>
                <th scope="col" style="width:34px;"></th><th scope="col">识别正文（可编辑）</th><th scope="col" style="width:150px;">发布时间</th>
                <th scope="col" style="width:96px;">平台</th><th scope="col" style="width:86px;">置信度</th><th scope="col" style="width:92px;">来源</th>
            </tr></thead><tbody>
            ${visRows.map((r, i) => `<tr data-i="${i}" style="${r.keep ? '' : 'opacity:.45;'}">
                <td><input type="checkbox" data-keep ${r.keep ? 'checked' : ''}></td>
                <td><input class="input" data-field="content" value="${esc(r.content)}"
                        style="width:100%;font-size:var(--fs-sm);" title="${esc(r.content)}"></td>
                <td><input class="input" data-field="create_time" value="${esc(r.create_time || '')}"
                        style="font-size:var(--fs-micro);width:100%;"
                        title="${esc(r.time_source || '')}">
                    ${r.time_inferred ? '<div style="font-size:var(--fs-micro);color:var(--warn-ink);margin-top:2px;">无原文时间，按导入日期</div>' : ''}</td>
                <td><select class="select" data-field="platform" style="font-size:var(--fs-micro);width:100%;">
                    ${['抖音', '小红书', '未知'].map(p => `<option value="${p}" ${r.platform === p ? 'selected' : ''}>${p}</option>`).join('')}
                </select></td>
                <td>${confBadge(r.confidence)}${r.needs_review ? '<div style="font-size:var(--fs-micro);color:var(--warn-ink);margin-top:2px;">需重点复核</div>' : ''}</td>
                <td style="font-size:var(--fs-micro);color:var(--ink-4);">${esc(r.file || '')}<br/>
                    <span style="font-family:ui-monospace,monospace;">${esc(r.engine || '')}</span></td>
            </tr>`).join('')}
            </tbody></table></div>
            <div class="note note-muted" style="margin-top:8px;">
                导入后这些记录会标记为「截图识别」，证据页可查到它们的置信度与识别引擎；
                人工复核这一步不做，OCR 的错字就会直接进统计口径。
            </div>`;

        // 输入即更新内存态：不重渲染整表，否则打字打到一半光标会被踢走
        $('#visionReview').querySelectorAll('[data-i]').forEach(tr => {
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
        $$('#visionReview [data-vsel]').forEach(b => b.addEventListener('click', () => {
            const mode = b.dataset.vsel;
            visRows.forEach(r => {
                if (mode === 'all') r.keep = true;
                else if (mode === 'none') r.keep = false;
                else if (mode === 'low') r.keep = !(r.confidence != null && r.confidence < 0.9);
            });
            renderVisionReview();
        }));
        $$('#visionReview [data-vcommit]').forEach(b => b.addEventListener('click', commitVision));
    }

    async function commitVision() {
        const rows = visRows.filter(r => r.keep && r.content.trim().length >= 5);
        if (!rows.length) { toast('请至少保留一条评论', 'err'); return; }
        const btn = $('#visionReview [data-vcommit]');
        if (btn) { btn.disabled = true; btn.textContent = '清洗中…'; }
        try {
            const d = await api('/api/vision/preview', null, {
                method: 'POST',
                body: JSON.stringify({
                    session_id: visSession,
                    rows: rows.map(r => ({
                        rid: r.rid, file: r.file, content: r.content,
                        create_time: r.create_time, platform: r.platform
                    }))
                })
            });
            // 草稿用掉即焚：复核表已经交出去了，留在内存里只会被误认成"还能再改一遍"
            visRows = []; visSession = null;
            $('#visionReview').innerHTML = '';
            $('#visionStatus').innerHTML = `<div class="note">
                复核通过 ${num(d.submitted)} 条，过清洗后有效 ${num(d.valid_count)} 条（${pct(d.effective_rate)}）。
                <b>还没入库</b>——下面逐条核对后，由你决定要不要加进数据集。
                ${(d.dropped && d.dropped.length) ? '<br/>内容过短已跳过 ' + num(d.dropped.length) + ' 条。' : ''}
            </div>`;
            renderIntake($('#visionIntake'), d);
        } catch (e) {
            toast('截图导入失败：' + e.message, 'err');
            $('#visionStatus').innerHTML = `<div class="note note-warn">导入失败：${esc(e.message)}</div>`;
        } finally {
            const b2 = $('#visionReview [data-vcommit]');
            if (b2) { b2.disabled = false; b2.textContent = '确认导入'; }
        }
    }

    /* ==================== 初始化 ==================== */
    document.addEventListener('cp:ready', () => {
        cw.platformSel = CP.PLATFORMS.slice();
        platformChips();
        loadEngine();
        setupTabs();
        setupImport();
        loadVisionStatus();
        setupVisionImport();
        setupLoginModal();
        setupDateQuick();
    });

    $('#btnCrawlStart').addEventListener('click', startCrawl);
    $('#btnCrawlStop').addEventListener('click', async () => {
        if (!cw.sid) return;
        try {
            await api('/api/crawl/' + cw.sid + '/stop', null, { method: 'POST' });
            toast('已请求停止，正在收尾', 'ok');
        } catch (e) {
            toast('停止失败：' + e.message, 'err');
        }
    });
})();
