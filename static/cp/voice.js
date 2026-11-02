/* 语音输入模块（S3-a）—— Web Speech API 的薄封装
 *
 * 三条设计取舍，都是刻意选的：
 *
 * 1. **不自动发送**。识别结果只填进输入框，用户确认无误再点「分析」。
 *    语音识别错一个字就自动提交一个问题，代价远大于省一次点击——
 *    何况本系统的回答里每个数字都会被回溯校验，问题问歪了整轮白跑。
 *
 * 2. **不录音、不存音频**。用的是浏览器内置识别（Chrome/Edge 走浏览器厂商的识别服务），
 *    只把"提问这句话"送去转文字，评论数据一条都不外传，本地也不落任何音频。
 *    页面上必须明说这一点——一个宣称"数据不出校"的系统，不能悄悄打开麦克风。
 *
 * 3. **不支持就不装作支持**。Safari 只支持 webkit 前缀、Firefox 根本不支持；
 *    检测不到就保留按钮但禁用，点击给出明确说明，而不是"点了没反应"。
 *
 * 对外只暴露一个 mount()，不认识 Agent、不认识 send()，方便单独测。
 */
(function () {
    'use strict';

    var SR = window.SpeechRecognition || window.webkitSpeechRecognition || null;

    var DEFAULT_HINT = 'Enter 发送 · Shift+Enter 换行 · 最多 200 字';
    var MAX_LEN = 200;
    var MAX_MS = 30000;          // 兜底：最长听 30 秒，避免用户忘了关

    var ERROR_TEXT = {
        'not-allowed': '麦克风权限被拒绝。请在浏览器地址栏左侧的站点设置里允许麦克风后重试。',
        'service-not-allowed': '浏览器禁用了语音识别服务，可能需要在设置中开启。',
        'audio-capture': '没有检测到可用的麦克风设备。',
        'no-speech': '没有听到声音，请靠近麦克风再说一次。',
        'network': '语音识别服务连接不上（离线或网络受限环境）。可以改用键盘输入。',
        'aborted': '',
        'language-not-supported': '当前浏览器不支持中文识别。'
    };

    var api = {
        supported: !!SR,
        listening: false,
        /** 最近一次结果，供验收脚本断言 */
        lastResult: null,
        lastError: null,
        stop: function () {},
        mount: function () { return api; }
    };

    /**
     * @param {Object} opt
     * @param {HTMLTextAreaElement} opt.input   目标输入框（会追加，不覆盖已有内容）
     * @param {HTMLButtonElement}  opt.button  麦克风按钮
     * @param {HTMLElement}        opt.hint    提示文案节点（聆听中会临时改写）
     * @param {Function}           opt.onToast 可选，toast(msg, type)
     * @param {Function}           opt.isBusy  可选，返回 true 时禁用（分析进行中）
     */
    api.mount = function (opt) {
        var input = opt.input;
        var button = opt.button;
        var hint = opt.hint;
        var toast = opt.onToast || function () {};
        var isBusy = opt.isBusy || function () { return false; };

        if (!input || !button) return api;

        var rec = null;
        var finalText = '';        // 本次会话已确认的文字
        var timer = null;
        var errored = false;

        function hintTo(text) {
            if (hint) hint.textContent = text;
        }
        function resetHint() {
            hintTo(DEFAULT_HINT);
        }
        function setListening(on, label) {
            api.listening = on;
            button.classList.toggle('is-listening', on);
            button.setAttribute('aria-pressed', on ? 'true' : 'false');
            button.querySelector('.voice-label').textContent = on ? '停止' : '语音';
            if (on) {
                hintTo(label || '正在聆听… 说完停一下会自动结束，识别结果先填进输入框，不会自动发送。');
            } else {
                resetHint();
            }
        }

        function appendText(text) {
            var t = (text || '').trim();
            if (!t) return;
            var cur = input.value;
            // 中文不需要空格分隔；已有内容以英文/数字结尾时补一个空格，避免粘连
            var glue = (cur && /[A-Za-z0-9]$/.test(cur)) ? ' ' : '';
            var next = cur + glue + t;
            if (next.length > MAX_LEN) {
                next = next.slice(0, MAX_LEN);
                toast('已截断到 ' + MAX_LEN + ' 字，请删减后再发送', 'err');
            }
            input.value = next;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.focus();
            input.setSelectionRange(next.length, next.length);
        }

        function clearTimer() {
            if (timer) { clearTimeout(timer); timer = null; }
        }

        function build() {
            var r = new SR();
            r.lang = 'zh-CN';
            r.continuous = false;          // 说完一句自动结束，避免一直占用麦克风
            r.interimResults = true;       // 边说边显示，让用户知道它在听
            r.maxAlternatives = 1;

            r.onstart = function () {
                errored = false;
                finalText = '';
                setListening(true);
            };

            r.onresult = function (ev) {
                var interim = '';
                for (var i = ev.resultIndex; i < ev.results.length; i++) {
                    var res = ev.results[i];
                    if (res.isFinal) finalText += res[0].transcript;
                    else interim += res[0].transcript;
                }
                api.lastResult = { final: finalText, interim: interim };
                // 中间结果直接预览在提示行里，不污染输入框（否则用户分不清哪部分是最终文本）
                if (interim) hintTo('识别中：' + interim);
                else if (finalText) hintTo('已识别：' + finalText + '（可在输入框修改后发送）');
            };

            r.onerror = function (ev) {
                errored = true;
                api.lastError = ev.error;
                var msg = ERROR_TEXT[ev.error];
                if (msg === undefined) msg = '语音识别出错：' + ev.error;
                if (msg) toast(msg, 'err');
            };

            r.onend = function () {
                clearTimer();
                setListening(false);
                if (finalText) {
                    appendText(finalText);
                    api.lastResult = { final: finalText, interim: '' };
                } else if (!errored) {
                    toast('没有识别到内容，请再说一次', 'err');
                }
            };
            return r;
        }

        function start() {
            if (isBusy()) { toast('分析进行中，请等这一轮结束', 'err'); return; }
            if (!rec) rec = build();
            try {
                rec.start();
                setListening(true);
                clearTimer();
                timer = setTimeout(function () { if (rec) rec.stop(); }, MAX_MS);
            } catch (e) {
                // 连续快速点击会抛 InvalidStateError，忽略即可
                if (String(e && e.name) !== 'InvalidStateError') {
                    toast('无法启动语音输入：' + (e && e.message ? e.message : e), 'err');
                }
            }
        }

        function stop() {
            clearTimer();
            if (rec && api.listening) { try { rec.stop(); } catch (e) { /* 已停止 */ } }
        }

        api.stop = stop;
        api.toggle = function () { if (api.listening) stop(); else start(); };

        if (!SR) {
            // 刻意不用 aria-disabled / disabled：那会让按钮真的点不动，
            // 用户点了没反应比点了给解释更糟。这里保留可点，点击即说明原因。
            button.classList.add('is-disabled');
            button.setAttribute('aria-label', '语音输入（当前浏览器不支持）');
            button.title = '当前浏览器不支持语音输入（Chrome / Edge 支持，Firefox 不支持）';
            button.addEventListener('click', function () {
                toast('当前浏览器不支持语音输入，建议用 Chrome 或 Edge 打开；也可以直接键盘输入。', 'err');
            });
            return api;
        }

        button.title = '语音输入（浏览器内置识别，只有提问文字会送去转写，不录音、不上传评论数据）';
        button.addEventListener('click', function (e) {
            e.preventDefault();
            api.toggle();
        });
        // 空格/回车触发时也要能用
        button.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); api.toggle(); }
        });
        return api;
    };

    window.CPVoice = api;
})();
