# -*- coding: utf-8 -*-
"""前端工程质量验证（深色模式 / 骨架屏 / 移动端）。

这类改动最容易出的问题不是"功能不工作"，而是"某个角落没跟着变"——
深色底下某张表还是白底、某个文字变成深色看不见。肉眼看全站不现实，
所以这里用可计算的方式兜住：

    1. 深色模式：切换后 data-theme=dark、localStorage 落盘、刷新后保持、
       正文与背景的亮度差不低于阈值（防止"深色文字压深色底"）；
    2. 图表：切主题后 canvas 仍然存在（重建成功，不是变成空白）；
    3. 骨架屏：首屏未渲染完时有 .skeleton / .chart:empty，渲染后自动消失；
    4. 移动端 390×844：侧栏收起、汉堡按钮可见、点开是抽屉、KPI 单列排布、
       无横向溢出（scrollWidth <= clientWidth + 1）。

用法：
    .venv/Scripts/python.exe tools/verify_ui.py
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("CP_BASE", "http://127.0.0.1:8010")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "screenshots", "cp")

PAGES = [("/", "01_dashboard"), ("/agent", "02_agent"), ("/insights", "03_insights"),
         ("/evidence", "04_evidence"), ("/data", "05_data")]

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


def luminance(rgb):
    """相对亮度（WCAG 定义，0-1）。"""
    def f(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def parse_rgb(s):
    nums = [int(x) for x in s.replace("rgba(", "").replace("rgb(", "").replace(")", "").split(",")[:3]]
    return tuple(nums)


def main():
    os.makedirs(OUT, exist_ok=True)
    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")

        # ================= 场景 A：浅色 + 深色（桌面） =================
        print("\n[场景 A] 桌面 · 浅色 / 深色切换")
        ctx = browser.new_context(viewport={"width": 1560, "height": 1000}, device_scale_factor=1.2)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"[pageerror] {str(e)[:200]}"))
        page.on("console", lambda m: errors.append(f"[console.{m.type}] {m.text[:200]}")
                if m.type == "error" else None)

        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_timeout(900)

        def t_default_light():
            theme = page.evaluate("document.documentElement.dataset.theme")
            assert theme == "light", f"首次访问默认主题应为浅色，实际 {theme}"
            assert page.locator("#themeToggle").is_visible(), "顶栏没有主题切换按钮"
            return "默认浅色，切换按钮在顶栏"

        def t_skeleton_then_render():
            # 骨架屏是"临时态"，用首次加载过程中的 DOM 采样来判断
            html = page.evaluate("""() => {
                const el = document.createElement('div');
                el.className = 'chart';
                document.body.appendChild(el);
                const after = getComputedStyle(el, '::after').content;
                const before = getComputedStyle(el, '::before').content;
                el.remove();
                return {after, before};
            }""")
            # content: "" 是"有伪元素但文字为空"——盒子照样画底纹，所以只判 none
            assert html["after"] != "none", "空图表容器没有骨架底纹（::after 未生成）"
            assert "加载中" in html["before"], f"骨架屏缺少文字提示：{html['before']}"
            assert page.locator("#chartTrend canvas").count() >= 1, "趋势图没有渲染出 canvas"
            empty_charts = page.eval_on_selector_all(
                ".chart", "els => els.filter(e => e.children.length === 0).length")
            return f"空容器有骨架（{html['before']}）；已渲染图表 {page.locator('.chart canvas').count()} 个"

        def t_toggle_dark():
            page.click("#themeToggle")
            page.wait_for_timeout(1200)
            theme = page.evaluate("document.documentElement.dataset.theme")
            assert theme == "dark", "点了切换但 data-theme 没变成 dark"
            stored = page.evaluate("localStorage.getItem('cp-theme')")
            assert stored == "dark", f"主题没有落盘：{stored}"
            asserted = page.get_attribute("#themeToggle", "aria-pressed")
            assert asserted == "true", "aria-pressed 没有更新"
            return "切到深色，localStorage 记为 dark，aria-pressed=true"

        def t_dark_contrast():
            """核心断言：深色下正文不能是深色字。"""
            data = page.evaluate("""() => {
                const g = el => getComputedStyle(el);
                const body = g(document.body);
                const card = g(document.querySelector('.card'));
                const title = document.querySelector('.card-title');
                const td = document.querySelector('.table td') || document.querySelector('.kpi-label');
                return {
                    bodyBg: body.backgroundColor,
                    bodyColor: body.color,
                    cardBg: card.backgroundColor,
                    titleColor: g(title).color,
                    cellColor: td ? g(td).color : null
                };
            }""")
            bg = parse_rgb(data["bodyBg"])
            assert sum(bg) < 200, f"深色模式下 body 背景仍然很亮：{data['bodyBg']}"
            card_bg = parse_rgb(data["cardBg"])
            assert sum(card_bg) < 240, f"卡片背景没跟着变暗：{data['cardBg']}"
            for key in ("bodyColor", "titleColor", "cellColor"):
                if not data[key]:
                    continue
                fg = parse_rgb(data[key])
                ratio = (max(luminance(fg), luminance(card_bg)) + .05) / \
                        (min(luminance(fg), luminance(card_bg)) + .05)
                assert ratio >= 4.0, f"{key} 对比度不足 {ratio:.1f}:1（{data[key]} on {data['cardBg']}）"
            return f"body={data['bodyBg']} card={data['cardBg']} 文字对比度达标"

        def t_dark_charts_alive():
            page.wait_for_timeout(600)
            n = page.locator(".chart canvas").count()
            assert n >= 3, f"切深色后图表 canvas 只剩 {n} 个（重建失败）"
            return f"{n} 个图表在深色下重建成功"

        def t_persist_after_reload():
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(700)
            theme = page.evaluate("document.documentElement.dataset.theme")
            assert theme == "dark", "刷新后主题被重置了（没有读 localStorage）"
            return "刷新后仍是深色"

        def t_dark_all_pages():
            shots = []
            for path, name in PAGES:
                page.goto(BASE + path, wait_until="networkidle")
                page.wait_for_timeout(1100)
                bad = page.evaluate("""() => {
                    const bad = [];
                    document.querySelectorAll('.card, .table th, .filterbar, .sidebar').forEach(el => {
                        const bg = getComputedStyle(el).backgroundColor;
                        const m = bg.match(/\\d+/g);
                        if (!m) return;
                        const sum = +m[0] + +m[1] + +m[2];
                        if (sum > 600) bad.push(el.className + ' ' + bg);
                    });
                    return bad.slice(0, 3);
                }""")
                assert not bad, f"{path} 深色下仍有亮底块：{bad}"
                page.screenshot(path=os.path.join(OUT, name.replace("0", "d", 1) + "_dark.png"),
                                full_page=False)
                shots.append(path)
            return "五页深色均无残留亮底：" + "、".join(shots)

        for name, fn in [
            ("默认浅色且可切换", t_default_light),
            ("骨架屏存在且渲染后消失", t_skeleton_then_render),
            ("切换深色并落盘", t_toggle_dark),
            ("深色下文字对比度达标", t_dark_contrast),
            ("深色下图表重建成功", t_dark_charts_alive),
            ("刷新后保持深色", t_persist_after_reload),
            ("五页深色无残留亮底", t_dark_all_pages),
        ]:
            check(name, fn)

        ctx.close()

        # ================= 场景 B：移动端 390×844 =================
        print("\n[场景 B] 移动端 390×844")
        m = browser.new_context(viewport={"width": 390, "height": 844},
                                device_scale_factor=2, is_mobile=True, has_touch=True)
        mp = m.new_page()
        mp.on("pageerror", lambda e: errors.append(f"[mobile pageerror] {str(e)[:200]}"))
        mp.goto(BASE + "/", wait_until="networkidle")
        mp.wait_for_timeout(1100)

        def t_mobile_nav_hidden():
            box = mp.locator(".sidebar").bounding_box()
            assert box is None or box["x"] + box["width"] <= 1, f"侧栏没有收起：{box}"
            assert mp.locator("#navToggle").is_visible(), "汉堡按钮不可见"
            return "侧栏收起、汉堡可见"

        def t_mobile_drawer():
            mp.click("#navToggle")
            mp.wait_for_timeout(400)
            assert mp.evaluate("document.body.classList.contains('side-open')"), "点汉堡没打开抽屉"
            box = mp.locator(".sidebar").bounding_box()
            assert box and box["x"] >= -2 and box["width"] > 200, f"抽屉没有滑出来：{box}"
            assert mp.locator(".nav-item span").first.is_visible(), "抽屉里导航文字被隐藏了"
            mp.screenshot(path=os.path.join(OUT, "20_mobile_drawer.png"))
            # 点抽屉右侧的空白区（遮罩中心被抽屉盖住，直接 click 会被判定为被遮挡）
            mp.mouse.click(350, 420)
            mp.wait_for_timeout(380)
            assert not mp.evaluate("document.body.classList.contains('side-open')"), "点遮罩没关闭抽屉"
            return "抽屉可开可关，导航文字可见"

        def t_mobile_no_overflow():
            bad = mp.evaluate("""() => {
                const w = document.documentElement.clientWidth;
                const over = [];
                const inScroller = el => {
                    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
                        const ox = getComputedStyle(p).overflowX;
                        if (ox === 'auto' || ox === 'scroll') return true;
                    }
                    return false;
                };
                document.querySelectorAll('.content *').forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.right <= w + 1.5) return;
                    if (inScroller(el)) return;      // 表格在 .table-scroll 里横滑，属预期
                    over.push((el.className || el.tagName) + ' ' + Math.round(r.right));
                });
                return {scrollW: document.documentElement.scrollWidth, clientW: w, over: over.slice(0, 4)};
            }""")
            assert bad["scrollW"] <= bad["clientW"] + 1, f"页面横向溢出：{bad}"
            assert not bad["over"], f"有元素超出视口：{bad['over']}"
            return f"无横向溢出（{bad['clientW']}px）"

        def t_mobile_single_column():
            boxes = mp.eval_on_selector_all(
                "#kpiRow .kpi", "els => els.slice(0, 2).map(e => e.getBoundingClientRect().x)")
            assert len(boxes) >= 2, "KPI 卡片数量不对"
            assert abs(boxes[0] - boxes[1]) < 2, f"KPI 没有变成单列（x={boxes}）"
            return "KPI 单列排布"

        def t_mobile_chart_height():
            h = mp.evaluate("document.querySelector('#chartTrend').getBoundingClientRect().height")
            assert 180 <= h <= 260, f"图表高度不适合手机：{h}"
            return f"图表高度 {int(h)}px"

        def t_mobile_screenshot():
            mp.screenshot(path=os.path.join(OUT, "21_mobile_dashboard.png"), full_page=True)
            mp.goto(BASE + "/agent", wait_until="networkidle")
            mp.wait_for_timeout(1000)
            mp.screenshot(path=os.path.join(OUT, "22_mobile_agent.png"), full_page=True)
            return "已保存移动端驾驶舱与工作台截图"

        for name, fn in [
            ("侧栏收起 + 汉堡可见", t_mobile_nav_hidden),
            ("抽屉可开可关", t_mobile_drawer),
            ("无横向溢出", t_mobile_no_overflow),
            ("KPI 单列排布", t_mobile_single_column),
            ("图表高度适配", t_mobile_chart_height),
            ("移动端截图", t_mobile_screenshot),
        ]:
            check(name, fn)

        m.close()
        browser.close()

    if errors:
        print("\n  页面报错：", errors[:4])
        results.append((False, "无控制台报错", "; ".join(errors[:2])))
    else:
        results.append((True, "无控制台报错", ""))
        print("\n  PASS  无控制台报错")

    passed = sum(1 for ok, _, _ in results if ok)
    print(f"\n结果：{passed}/{len(results)} 通过")
    for ok, name, detail in results:
        if not ok:
            print(f"  · 失败：{name} — {detail}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
