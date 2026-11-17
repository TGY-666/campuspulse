/* 登录页交互。
   只有一件事：把表单提交给 /api/auth/login，成功就跳 next，失败就说人话。

   刻意不引任何框架、不做"记住上次账号"之类的小聪明：
   这个页面可能在学校内网被 30 个人用，出了问题必须能一眼看懂。 */
(function () {
    'use strict';

    /* 登录页就四个控件，类型在 HTML 里已经确定，一次性断言到具体元素类型，
       后面 40 行就不用每行都写断言了。 */
    var form = /** @type {HTMLFormElement|null} */ (document.getElementById('loginForm'));
    var btn = /** @type {HTMLButtonElement} */ (document.getElementById('loginBtn'));
    var errBox = /** @type {HTMLElement} */ (document.getElementById('loginErr'));
    var nextInput = /** @type {HTMLInputElement} */ (document.getElementById('nextPath'));
    if (!form) return;

    function showError(msg) {
        errBox.textContent = msg;
        errBox.hidden = false;
    }

    function clearError() {
        errBox.hidden = true;
        errBox.textContent = '';
    }

    function setBusy(busy) {
        btn.disabled = busy;
        btn.textContent = busy ? '登录中…' : '登录';
    }

    /* 只接受站内相对路径，服务端已校验过一次，这里再挡一次：
       两处都校验不是重复，而是因为前端可以被改。 */
    function safeNext(raw) {
        var v = String(raw || '').trim();
        if (v.indexOf('/') !== 0 || v.indexOf('//') === 0 || v.indexOf('\\') >= 0) return '/';
        return v;
    }

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        clearError();

        var username = (/** @type {HTMLInputElement} */ (document.getElementById('username')).value || '').trim();
        var password = /** @type {HTMLInputElement} */ (document.getElementById('password')).value || '';
        var remember = /** @type {HTMLInputElement} */ (document.getElementById('remember')).checked;

        if (!username) { showError('请输入账号'); return; }
        // 不校验密码长度：口令规则由后端决定，前端多说一句都可能泄露策略
        if (!password) { showError('请输入密码'); return; }

        setBusy(true);
        fetch('/api/auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                // 服务端写接口统一要求这个头（跨站表单带不上自定义头）
                'X-Requested-With': 'XMLHttpRequest'
            },
            credentials: 'same-origin',
            body: JSON.stringify({ username: username, password: password, remember: remember })
        }).then(function (resp) {
            return resp.json().then(function (body) { return { status: resp.status, body: body }; });
        }).then(function (r) {
            if (r.body && r.body.ok) {
                // 首登未改密：S1 会在这里跳改密页，S0 先直接进站
                var dest = safeNext(nextInput ? nextInput.value : '/');
                /* 部门账号没有驾驶舱权限，落地页是工单页。
                   不处理的话它会落在 '/' 上——那是 404，人看到的第一屏就是
                   "页面不存在"，还以为账号没建对。 */
                var user = (r.body.data && r.body.data.user) || {};
                if (dest === '/' && user.role === 'dept') dest = '/tickets';
                location.replace(dest);
                return;
            }
            var msg = (r.body && r.body.error && r.body.error.message) || '登录失败，请稍后重试';
            if (r.status === 429) msg = '尝试次数过多，请稍后再试';
            showError(msg);
            setBusy(false);
            /** @type {HTMLInputElement} */ (document.getElementById('password')).select();
        }).catch(function () {
            // 网络层失败和"密码错了"要分开说，否则用户会一直重输密码
            showError('无法连接服务器，请检查网络或稍后重试');
            setBusy(false);
        });
    });

    // 输入时清掉上一次的错误，避免"改了密码错误还在"的困惑
    ['username', 'password'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('input', clearError);
    });
})();
