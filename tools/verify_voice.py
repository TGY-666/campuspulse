# -*- coding: utf-8 -*-
"""S3-a 语音输入验证：用假的 SpeechRecognition 驱动真实页面。

为什么不用真麦克风：CI 和验收环境没有麦克风，而且"真的能识别"取决于浏览器厂商的识别服务，
不可复现。这里验的是**集成契约**——真正容易写错、也真正影响用户的那几条：

    1. 中间结果只进提示行，不进输入框（否则用户分不清哪部分已定稿）
    2. 最终结果**追加**到输入框，不覆盖已有内容
    3. 识别结束**绝不自动发送**（这是本模块最重要的设计取舍）
    4. 发送前会主动停掉识别（否则结果会写进已清空的框里）
    5. 权限被拒 / 无麦克风 → 明确中文提示，且按钮回到非聆听态
    6. 浏览器不支持 → 按钮保留但禁用，点击给出说明，而不是"点了没反应"

用法：
    .venv/Scripts/python.exe tools/verify_voice.py
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("CP_BASE", "http://127.0.0.1:8010")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "screenshots", "cp")

# 假的识别器：行为与真实 Web Speech API 一致（onstart / onresult / onerror / onend），
# 额外加 __emit / __finish 供测试驱动事件。
FAKE_SR = r"""
(function () {
    var instances = [];
    function Fake() {
        this.lang = ''; this.continuous = true; this.interimResults = false;
        this.maxAlternatives = 1; this.started = false; this._ended = false;
        instances.push(this);
    }
    Fake.prototype.start = function () {
        this.started = true; this._ended = false;
        var self = this;
        setTimeout(function () { self.onstart && self.onstart({}); }, 10);
    };
    Fake.prototype.stop = function () {
        if (!this.started || this._ended) return;
        this.started = false; this._ended = true;
        var self = this;
        setTimeout(function () { self.onend && self.onend({}); }, 10);
    };
    Fake.prototype.abort = Fake.prototype.stop;
    Fake.prototype.__emit = function (items) {
        var results = items.map(function (it) {
            var alt = { transcript: it.text, confidence: 0.92 };
            var r = [alt]; r.isFinal = it.final; r.length = 1; return r;
        });
        this.onresult && this.onresult({ resultIndex: 0, results: results });
    };
    Fake.prototype.__error = function (code) {
        this.onerror && this.onerror({ error: code });
        this.stop();
    };
    Fake.prototype.__finish = function () { this.stop(); };
    window.__fakeSR = instances;
    window.SpeechRecognition = Fake;
})();
"""

results = []


def check(name, fn):
    try:
        detail = fn()
        results.append((True, name, detail or ""))
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
    except AssertionError as e:
        results.append((False, name, str(e)))
        print(f"  FAIL  {name} — {e}")
    except Exception as e:
        results.append((False, name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name} — {type(e).__name__}: {e}")


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")

        # ============ 场景 A：支持语音（注入假识别器） ============
        print("\n[场景 A] 浏览器支持语音输入")
        ctx = browser.new_context(viewport={"width": 1560, "height": 1000}, device_scale_factor=1.3)
        ctx.add_init_script(FAKE_SR)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)[:200]))
        page.on("console", lambda m: errors.append(f"[console.{m.type}] {m.text[:200]}")
                if m.type == "error" else None)

        api_calls = []
        page.on("request", lambda r: api_calls.append(r.url)
                if r.method == "POST" and "/api/agent" in r.url else None)

        page.goto(BASE + "/agent", wait_until="networkidle")
        page.wait_for_timeout(400)

        def t_btn_ready():
            btn = page.locator("#btnVoice")
            assert btn.count() == 1, "找不到语音按钮"
            assert not btn.evaluate("e => e.classList.contains('is-disabled')"), "按钮被误标为不可用"
            assert page.evaluate("!!window.CPVoice && window.CPVoice.supported"), "CPVoice 未就绪"
            return "语音按钮可用，CPVoice.supported=true"

        def t_start_listening():
            page.click("#btnVoice")
            page.wait_for_selector("#btnVoice.is-listening", timeout=3000)
            hint = page.inner_text("#composerHint")
            assert "聆听" in hint, f"提示文案没变成聆听态：{hint}"
            assert page.inner_text("#btnVoice .voice-label") == "停止", "按钮文字没切换"
            return f"进入聆听态，提示：{hint[:26]}…"

        def t_interim_not_paste():
            page.evaluate("window.__fakeSR[0].__emit([{text: '食堂饭菜', final: false}])")
            page.wait_for_timeout(80)
            assert page.input_value("#qInput") == "", "中间结果被写进了输入框（会造成半句被发送）"
            assert "识别中" in page.inner_text("#composerHint"), "中间结果没有出现在提示行"
            return "中间结果只在提示行，输入框仍为空"

        def t_final_appends():
            page.fill("#qInput", "你好，")
            page.evaluate("window.__fakeSR[0].__emit([{text: '食堂饭菜贵不贵', final: true}])")
            page.wait_for_timeout(50)
            page.evaluate("window.__fakeSR[0].__finish()")
            page.wait_for_selector("#btnVoice:not(.is-listening)", timeout=3000)
            page.wait_for_timeout(150)
            val = page.input_value("#qInput")
            assert val == "你好，食堂饭菜贵不贵", f"没有正确追加到已有内容：{val!r}"
            assert page.inner_text("#btnVoice .voice-label") == "语音", "结束后按钮文字没复原"
            assert page.inner_text("#composerHint").startswith("Enter 发送"), "提示文案没复原"
            return f"追加成功：{val}"

        def t_no_auto_send():
            assert not api_calls, f"识别结束后自动发了请求：{api_calls}"
            return "识别结束没有触发任何 /api/agent 请求"

        def t_stop_on_send():
            page.evaluate("window.__fakeSR[0].start()")
            page.wait_for_selector("#btnVoice.is-listening", timeout=3000)
            page.evaluate("window.__fakeSR[0].__emit([{text: '再补一句', final: false}])")
            page.click("#btnSend")
            page.wait_for_timeout(200)
            assert page.evaluate("window.CPVoice.listening") is False, "发送时没有停掉识别"
            return "点发送时主动停止了识别"

        def t_error_permission():
            # 等这一轮回答结束（可能较久），只验证错误路径与发送无关，先刷新页面
            page.goto(BASE + "/agent", wait_until="networkidle")
            page.wait_for_timeout(400)
            page.click("#btnVoice")
            page.wait_for_selector("#btnVoice.is-listening", timeout=3000)
            page.evaluate("window.__fakeSR[0].__error('not-allowed')")
            page.wait_for_selector("#btnVoice:not(.is-listening)", timeout=3000)
            page.wait_for_timeout(200)
            toast = page.locator("#toastWrap").inner_text()
            assert "麦克风" in toast and "权限" in toast, f"权限错误提示不明确：{toast!r}"
            return f"权限被拒提示：{toast.strip()[:30]}"

        def t_error_no_speech():
            page.click("#btnVoice")
            page.wait_for_selector("#btnVoice.is-listening", timeout=3000)
            page.evaluate("window.__fakeSR[0].__error('no-speech')")
            page.wait_for_timeout(300)
            toast = page.locator("#toastWrap").inner_text()
            assert "没有听到" in toast or "没有识别" in toast, f"空结果提示不明确：{toast!r}"
            assert page.input_value("#qInput") == "", "出错后污染了输入框"
            return "无语音输入时提示明确，输入框未被污染"

        def t_screenshot():
            page.fill("#qInput", "")
            page.click("#btnVoice")
            page.wait_for_selector("#btnVoice.is-listening", timeout=3000)
            page.evaluate("window.__fakeSR[0].__emit([{text: '宿舍住宿方面学生最不满的是什么', final: false}])")
            page.wait_for_timeout(300)
            page.locator(".composer").screenshot(path=os.path.join(OUT, "10_voice_listening.png"))
            return "已保存 composer 聆听态截图"

        for name, fn in [
            ("按钮就绪且未被降级", t_btn_ready),
            ("点击进入聆听态", t_start_listening),
            ("中间结果不进输入框", t_interim_not_paste),
            ("最终结果追加而非覆盖", t_final_appends),
            ("识别结束不自动发送", t_no_auto_send),
            ("发送时停止识别", t_stop_on_send),
            ("权限被拒有明确提示", t_error_permission),
            ("无语音有明确提示", t_error_no_speech),
            ("聆听态截图", t_screenshot),
        ]:
            check(name, fn)

        if errors:
            print("  [页面报错]", errors[:3])
            results.append((False, "无控制台报错", "; ".join(errors[:2])))
        else:
            results.append((True, "无控制台报错", ""))
            print("  PASS  无控制台报错")

        ctx.close()

        # ============ 场景 B：浏览器不支持 ============
        print("\n[场景 B] 浏览器不支持语音（删除 SpeechRecognition）")
        ctx2 = browser.new_context(viewport={"width": 1560, "height": 1000})
        ctx2.add_init_script("delete window.SpeechRecognition; delete window.webkitSpeechRecognition;")
        page2 = ctx2.new_page()
        page2.goto(BASE + "/agent", wait_until="networkidle")
        page2.wait_for_timeout(400)

        def t_unsupported():
            assert page2.evaluate("window.CPVoice.supported") is False, "误报为支持"
            cls = page2.evaluate("document.querySelector('#btnVoice').className")
            assert "is-disabled" in cls, f"不支持时按钮没有降级样式：{cls}"
            assert "不支持" in page2.evaluate("document.querySelector('#btnVoice').title"), \
                "按钮 title 没有说明不支持"
            page2.click("#btnVoice")
            page2.wait_for_timeout(200)
            toast = page2.locator("#toastWrap").inner_text()
            assert "不支持" in toast, f"点击后没有说明原因：{toast!r}"
            return "按钮保留但视觉弱化，点击说明原因（不是静默失效）"

        check("不支持时优雅降级", t_unsupported)
        page2.screenshot(path=os.path.join(OUT, "11_voice_unsupported.png"))
        ctx2.close()
        browser.close()

    passed = sum(1 for ok, _, _ in results if ok)
    total = len(results)
    print(f"\n结果：{passed}/{total} 通过")
    for ok, name, detail in results:
        if not ok:
            print(f"  · 失败：{name} — {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
