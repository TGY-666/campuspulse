# -*- coding: utf-8 -*-
"""前端工程质量验证（深色模式 / 骨架屏 / 移动端）。

这类改动最容易出的问题不是"功能不工作"，而是"某个角落没跟着变"——
深色底下某张表还是白底、某个文字变成深色看不见。肉眼看全站不现实，
所以这里用可计算的方式兜住：

    1. 深色模式：切换后 data-theme=dark、localStorage 落盘、刷新后保持、
       正文与背景的亮度差不低于阈值（防止"深色文字压深色底"）；
    2. 图表：切主题后 canvas 仍然存在（重建成功，不是变成空白）；
    3. 骨架屏：首屏未渲染完时有 .skeleton / .chart:empty，渲染后自动消失；
    4. 移动端 390×844：侧栏收起、汉堡按钮可见、点开是抽屉、KPI 2+2+1 栅格、
       无横向溢出（scrollWidth <= clientWidth + 1）。

用法：
    .venv/Scripts/python.exe tools/verify_ui.py
"""
import os
import re
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("CP_BASE", "http://127.0.0.1:8010")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "screenshots", "cp")

PAGES = [("/", "01_dashboard"), ("/agent", "02_agent"), ("/insights", "03_insights"),
         ("/insights/topics", "03b_insights_topics"),
         ("/insights/sentiment", "03c_insights_sentiment"),
         ("/insights/timeline", "03d_insights_timeline"),
         ("/evidence", "04_evidence"), ("/data", "05_data"),
         # 数据页拆成三个入口后的两个子页：采集页有轮询与动态表，
         # 报告页有异步生成，这两个最容易留下控制台报错。
         ("/data/collect", "05b_data_collect"), ("/data/reports", "05c_data_reports"),
         # 风险研判总览也要过一遍深色/亮底检查：它的 dp-todo 条是硬编码的
         # 卡片式布局，最容易在深色下留下浅色底（这类问题只有截图能看出来）。
         ("/departments", "06a_departments")]

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
            # 第 17 轮起主题切换收在「更多」里：仍然一次点击可达，但不再是常驻图标。
            assert page.locator("#topMore").count() == 1, "顶栏没有「更多」菜单"
            page.locator("#topMore > summary").click()
            page.wait_for_timeout(250)
            assert page.locator("#themeToggle").is_visible(), "「更多」里没有主题切换"
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            return "默认浅色；切换入口在顶栏「更多」菜单里"

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
            page.locator("#topMore > summary").click()
            page.wait_for_timeout(250)
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
                /* 首页第一张 .card 是 is-alert 告警条，深色下用 14% 红色叠加在深底上。
                   直接取它的 backgroundColor 会把半透明色当成不透明来求 RGB 和（469），
                   误判成"卡片没变暗"。要验的是"正文不能是深色字"，取第一张不透明卡片。 */
                const cards = [...document.querySelectorAll('.card')];
                const solid = cards.find(e => {
                    const m = getComputedStyle(e).backgroundColor.match(/[\d.]+/g);
                    return m && (m.length < 4 || +m[3] >= 0.95);
                }) || document.querySelector('.card');
                const card = g(solid);
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
                assert ratio >= 4.5, f"{key} 对比度不足 {ratio:.1f}:1（{data[key]} on {data['cardBg']}）"
            return f"body={data['bodyBg']} card={data['cardBg']} 文字对比度达标"

        def t_dark_charts_alive():
            # 114 图表是惰性初始化：core.js 的 flushLazy 只在 .chart 进入视口时
            # 才真正 setOption 画 canvas，视口外的一直空着。旧的「滚到底 +
            # n >= min(total,2)」用滚动后的总数凑数，会掩盖「刚进视口的图表其实
            # 空白」——实测首页 2 个 .chart 都在 1000px 首屏之下，滚到底自然全画好，
            # 但这不能证明「进入视口时就被正确唤醒」。改按「已进入视口的 .chart 数」
            # 断言：先在不滚动状态记录首屏可见图表绘制数、存一张不滚动首屏截图，
            # 再逐个把图表滚进视口、立刻检查它已画出 canvas。
            page.wait_for_timeout(600)
            # 不滚动的首屏截图：保留惰性机制下首屏的真实长相（首页图表在首屏之下，
            # 这张图就是「首屏没有图表」的诚实记录，供人工核对，不靠滚动凑数）。
            page.screenshot(path=os.path.join(OUT, "10_dark_firstscreen_no_scroll.png"),
                            full_page=False)
            # 滚到底之前先记录首屏可见图表绘制数；本页首屏可见数可能为 0。
            first = page.eval_on_selector_all(
                ".chart",
                "els => { const vh = innerHeight; const vis = els.filter(e => { const r = e.getBoundingClientRect(); return r.top < vh && r.bottom > 0; }); return [vis.length, vis.filter(e => e.querySelector('canvas')).length]; }")
            visible0, drawn0 = first
            assert drawn0 == visible0, \
                f"首屏 {visible0} 个可见图表只画了 {drawn0} 个（进入视口却没被唤醒）"
            # 逐个把图表滚进视口：进入视口即应绘制，按「已进入视口的 .chart 数」断言
            charts = page.locator(".chart")
            for i in range(charts.count()):
                charts.nth(i).scroll_into_view_if_needed()
                page.wait_for_timeout(500)
                ok = charts.nth(i).evaluate("e => !!e.querySelector('canvas')")
                assert ok, f"第 {i+1} 个图表进入视口后仍是空白（惰性初始化失败）"
            # 最后滚到底，确认深色重建后 canvas 没被弄丢
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(900)
            total = page.locator(".chart").count()
            n = page.locator(".chart canvas").count()
            assert n >= total and total > 0, f"滚到底后 {total} 个图表只重建了 {n} 个"
            return (f"首屏可见 {visible0} 个图表全部已绘制；逐个进入视口的 "
                    f"{charts.count()} 个图表均成功唤醒；滚动后 {n}/{total} 个重建成功")

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
            return "各页深色均无残留亮底：" + "、".join(shots)

        for name, fn in [
            ("默认浅色且可切换", t_default_light),
            ("骨架屏存在且渲染后消失", t_skeleton_then_render),
            ("切换深色并落盘", t_toggle_dark),
            ("深色下文字对比度达标", t_dark_contrast),
            ("深色下图表重建成功", t_dark_charts_alive),
            ("刷新后保持深色", t_persist_after_reload),
            ("各页深色无残留亮底", t_dark_all_pages),
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

        def t_mobile_kpi_grid():
            # 390 下 .g-5 现在是 2 列 + 末项跨 2 列（评审 019：避免第 5 张卡
            # 独占一整行、留下半行空白）。旧断言要求前两张 x 相同，与新栅格冲突。
            boxes = mp.eval_on_selector_all(
                "#kpiRow .kpi",
                "els => els.map(e => { const r = e.getBoundingClientRect();"
                " return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width)}; })")
            assert len(boxes) >= 5, f"KPI 卡片数量不对：{len(boxes)}"
            assert abs(boxes[0]["x"] - boxes[1]["x"]) > 20, f"前两张没有并排：{boxes[:2]}"
            assert abs(boxes[0]["y"] - boxes[1]["y"]) < 4, f"前两张不在同一行：{boxes[:2]}"
            assert boxes[4]["w"] > boxes[3]["w"] * 1.6, f"末张没有跨两列：{boxes[4]}"
            return f"KPI 2+2+1 栅格（末张宽 {boxes[4]['w']}px）"

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
            ("KPI 2+2+1 栅格", t_mobile_kpi_grid),
            ("图表高度适配", t_mobile_chart_height),
            ("移动端截图", t_mobile_screenshot),
        ]:
            check(name, fn)

        m.close()

        # ================= 场景 C：桌面功能回归（第 9 轮 E7 增断言） =================
        # 不降低既有标准：对比度阈值仍 4.5（见 t_dark_contrast）、深色 / 移动端断言全部保留。
        # 本场景只用「首屏不滚动」采样，绝不用「滚到底」绕过首屏问题；.tk-open / aiBasis
        # 用构造 DOM / 直接调函数校验，不依赖演示库里是否已有工单或 AI 接口是否已返回。
        print("\n[场景 C] 桌面 · 第 9 轮功能回归")
        c = browser.new_context(viewport={"width": 1560, "height": 1000},
                                device_scale_factor=1.2)
        cp = c.new_page()
        cp.on("pageerror", lambda e: errors.append(f"[regress pageerror] {str(e)[:200]}"))
        cp.on("console", lambda msg: errors.append(f"[regress console.{msg.type}] {msg.text[:200]}")
              if msg.type == "error" else None)
        cp.goto(BASE + "/", wait_until="networkidle")
        cp.wait_for_timeout(900)

        def t_desktop_tk_open():
            # 直接构造 .tk-item-meta > a.tk-open 采样计算样式：.tk-open 由 tickets.js
            # 按工单数据动态渲染，构造法不依赖 /tickets 是否已有数据或登录态。
            # 桌面视口（1560）下必须命中 C1「移到媒体查询之外、桌面移动共用」的规则：
            # white-space:nowrap + flex:0 0 auto，让「打开详情 →」不逐字竖排、自然宽 ≥44px。
            data = cp.evaluate("""() => {
                const meta = document.createElement('div');
                meta.className = 'tk-item-meta';
                const a = document.createElement('a');
                a.className = 'tk-open';
                a.textContent = '打开详情 →';
                meta.appendChild(a);
                document.body.appendChild(meta);
                const cs = getComputedStyle(a);
                const r = a.getBoundingClientRect();
                const out = {whiteSpace: cs.whiteSpace, width: r.width, height: r.height};
                meta.remove();
                return out;
            }""")
            assert data["whiteSpace"] == "nowrap", \
                f"桌面 .tk-open 未 nowrap，会逐字竖排：{data}"
            assert data["width"] >= 44, \
                f"桌面 .tk-open 宽度不足 44px（P0-2 断链）：{data}"
            return f"桌面 .tk-open nowrap、宽 {data['width']:.0f}px（≥44）"

        def t_empty_chart_states():
            # 不滚动、首屏采样：三态按容器自身状态判定、应互斥。「假阳性」特指
            # 折线以下「还没滚到」的空图表被一刀切成 is-stalled / is-failed
            # （Round 8 已删 8s/10s 定时给所有 .chart:empty 加 stalled）。真正
            # 位于视口内且仍在加载的 is-stalled 不算假阳性，故只禁止「折线以下」
            # 的图表挂这两种状态；is-failed 在全新加载里应为 0。
            data = cp.evaluate("""() => {
                const S = ['is-lazy-wait', 'is-stalled', 'is-failed'];
                const vh = innerHeight;
                const charts = [...document.querySelectorAll('.chart')];
                const out = {total: charts.length, lazy: 0, stalled: 0,
                             failed: 0, drawn: 0, multi: 0,
                             stalledBelowFold: 0, failedBelowFold: 0};
                charts.forEach(el => {
                    const hit = S.filter(c => el.classList.contains(c));
                    if (hit.length > 1) out.multi += 1;
                    if (el.querySelector('canvas')) out.drawn += 1;
                    const below = el.getBoundingClientRect().top >= vh;
                    if (hit.includes('is-lazy-wait')) out.lazy += 1;
                    if (hit.includes('is-stalled')) {
                        out.stalled += 1;
                        if (below) out.stalledBelowFold += 1;
                    }
                    if (hit.includes('is-failed')) {
                        out.failed += 1;
                        if (below) out.failedBelowFold += 1;
                    }
                });
                return out;
            }""")
            assert data["multi"] == 0, f"同一图表挂了多个状态类（三态应互斥）：{data}"
            assert data["failed"] == 0, f"全新首屏出现 is-failed 假阳性：{data}"
            assert data["stalledBelowFold"] == 0 and data["failedBelowFold"] == 0, \
                f"折线以下的图表被误标 is-stalled / is-failed（假故障）：{data}"
            return (f"共 {data['total']} 图：已绘制 {data['drawn']}、"
                    f"is-lazy-wait {data['lazy']}、is-stalled {data['stalled']}（视口内）、"
                    f"折线以下误标 0（无假阳性）")

        def t_flush_all_charts():
            # 不滚到底：直接在首屏状态确认 flushAllCharts 可调用，且调用后
            # 待渲染图表真的建出 canvas（§2.4 的就地恢复入口，不靠滚动凑数）。
            ok = cp.evaluate(
                "() => typeof CP !== 'undefined' && typeof CP.flushAllCharts === 'function'")
            assert ok, "CP.flushAllCharts 未导出 / 不可调用"
            before = cp.locator(".chart canvas").count()
            cp.evaluate("() => CP.flushAllCharts()")
            cp.wait_for_timeout(800)
            after = cp.locator(".chart canvas").count()
            assert after > 0, \
                f"调用 flushAllCharts 后仍无 canvas（前 {before} / 后 {after}）"
            return f"flushAllCharts 可调用；.chart canvas {before} → {after}"

        for name, fn in [
            ("桌面 .tk-open ≥44px 且 nowrap", t_desktop_tk_open),
            ("空图表三态无 stalled/failed 假阳性", t_empty_chart_states),
            ("CP.flushAllCharts 调用后图表有 canvas", t_flush_all_charts),
        ]:
            check(name, fn)

        # 词云页：CP.aiBasis(d, kind) 四类分母 + 词云卡独立分母
        cp.goto(BASE + "/insights/sentiment", wait_until="networkidle")
        cp.wait_for_timeout(900)

        def t_ai_basis_denom():
            # 可见依据行只保留范围 / 条数 / 一句分母；完整口径与「AI 不新增数字」
            # 收进 title。四类 kind 的可见文案仍须各不同，词云卡分母独立。
            kinds = ["ratio", "sum", "word-side", "mixed"]
            html = cp.evaluate("""(kinds) => {
                if (typeof CP === 'undefined' || typeof CP.aiBasis !== 'function') return null;
                const out = {};
                kinds.forEach(k => {
                    try { out[k] = CP.aiBasis({}, k); }
                    catch (e) { out[k] = 'ERROR: ' + (e && e.message); }
                });
                return out;
            }""", kinds)
            assert html, "CP.aiBasis 未导出 / 不可调用"
            for k in kinds:
                h = html[k] or ""
                assert not h.startswith("ERROR:"), f"{k} 调用抛错：{h[:120]}"
                assert 'class="ai-basis"' in h, f"{k} 未返回 .ai-basis HTML：{h[:80]}"
                assert "当前范围：" not in h, f"{k} 仍在可见行重复「当前范围」：{h[:80]}"
                assert "数字来自统计接口，AI 不新增数字" in h, \
                    f"{k} 缺 title 口径：{h[:80]}"
                assert "数字由统计接口给出" not in h and "生成于" not in h and "北京时间" not in h, \
                    f"{k} 仍把免责 / 时区小字放在可见行：{h[:80]}"
                assert "已核验" not in h and "n/n" not in h, \
                    f"{k} 编造了引用核验数字（四个 AI 接口无校验器）：{h[:80]}"
                assert "分母＝当前筛选圈定的评论" not in h, \
                    f"{k} 仍用第 8 轮一刀切分母：{h[:80]}"
                assert "见卡片脚注" not in h, \
                    f"{k} 仍指向一个不存在的脚注：{h[:80]}"
            assert "词云按对应情感计" in html["word-side"], \
                "词云卡分母未独立（缺「词云按对应情感计」）"
            assert len({html[k] for k in kinds}) == len(kinds), \
                "四类 kind 的 .ai-basis 文案没有随卡型变化"
            # DOM 层：若 wiBasis 槽已填充则词云卡必须含独立分母；槽为空属
            # 契约允许（生成中 / 失败 / 无数据清空），故只在已填充时校验。
            slot = cp.evaluate("""() => {
                const el = document.querySelector('#wiBasis .ai-basis');
                return el ? el.textContent : '';
            }""")
            if slot.strip():
                assert "词云按对应情感计" in slot, f"词云卡 .ai-basis 分母不对：{slot[:80]}"
            return "可见行精简、四类分母各异、完整口径在 title"

        check(".ai-basis 可见行精简（词云独立分母）", t_ai_basis_denom)

        # ================= 场景 D：第 10 轮四条回归断言 =================
        # 第 9 轮 18/18 全过，却漏掉四处真实回归——因为它测 DOM / 文案而不测
        # getComputedStyle：移动端 .tk-open 被特异性打回 24px、#aiInsight 的扁平化
        # 从未生效、.ai-basis 的位置没人核对、.chain 的 is-done 与未开始步同色。
        # 这四条全部改成"读计算样式"判定，光看类名在不在不算过。
        print("\n[场景 D] 第 10 轮计算样式回归")

        def t_mobile_tk_open_44():
            # 390 上下文单独开：场景 C 是 1560 宽，桌面 .tk-open 本来就是 24px 高。
            d = browser.new_context(viewport={"width": 390, "height": 844},
                                    device_scale_factor=2, is_mobile=True, has_touch=True)
            p = d.new_page()
            p.goto(BASE + "/tickets", wait_until="networkidle")
            p.wait_for_timeout(400)
            data = p.evaluate("""() => {
                const meta = document.createElement('div');
                meta.className = 'tk-item-meta';
                const a = document.createElement('a');
                a.className = 'tk-open';
                a.textContent = '打开详情 →';
                meta.appendChild(a);
                document.body.appendChild(meta);
                const cs = getComputedStyle(a);
                const r = a.getBoundingClientRect();
                const out = {h: r.height, w: r.width, minH: cs.minHeight};
                meta.remove();
                return out;
            }""")
            d.close()
            assert data["h"] >= 43.5, \
                f"390 下 .tk-open 高度 {data['h']:.0f}px（min-height={data['minH']}），低于 44px"
            return f"390 .tk-open {data['w']:.0f}×{data['h']:.0f}px（≥44）"

        def t_ai_insight_flat():
            # #aiInsight 在驾驶舱；上一段停在情感页，这里回到首页再测。
            cp.goto(BASE + "/", wait_until="networkidle")
            cp.wait_for_timeout(600)
            data = cp.evaluate("""() => {
                const el = document.getElementById('aiInsight');
                if (!el) return null;
                const cs = getComputedStyle(el);
                const m = cs.backgroundColor.match(/rgba?\\(([^)]+)\\)/);
                const parts = m ? m[1].split(',').map(x => parseFloat(x)) : [];
                return {bg: cs.backgroundColor, alpha: parts.length > 3 ? parts[3] : 1,
                        radius: cs.borderRadius, pl: cs.paddingLeft, bl: cs.borderLeftWidth};
            }""")
            assert data, "首页没有 #aiInsight"
            assert data["alpha"] == 0, \
                f"#aiInsight 仍是实底（{data['bg']}）——.card .ai-summary 输给了 ID 选择器"
            assert data["radius"].startswith("0"), f"#aiInsight 仍有圆角：{data['radius']}"
            assert float(data["bl"].replace("px", "")) >= 1, \
                f"#aiInsight 的品牌左边框被抹掉了：{data['bl']}"
            return f"#aiInsight 背景透明、圆角 0、左边框 {data['bl']}"

        def t_ai_basis_after_head():
            data = cp.evaluate("""() => {
                const out = [];
                document.querySelectorAll('.ai-basis').forEach(el => {
                    const card = el.closest('.card');
                    const head = card && card.querySelector('.card-title');
                    out.push({
                        inCard: !!card,
                        afterHead: head
                            ? el.getBoundingClientRect().top >= head.getBoundingClientRect().top
                            : null
                    });
                });
                return out;
            }""")
            assert data, "情感页没有任何 .ai-basis（口径行整体缺失）"
            bad = [x for x in data if x["inCard"] and x["afterHead"] is False]
            assert not bad, f"{len(bad)} 处 .ai-basis 跑到了卡片标题之上：{bad[:2]}"
            return f"{len(data)} 处口径行都在卡片标题之下"

        def t_chain_is_not_nav():
            # .chain 已降级成一句 <p class="cp-chain-note">：不再有 nav 地标、
            # 不再有 aria-current="step"（挂在不可导航的 <span> 上是纯噪音）。
            cp.goto(BASE + "/departments", wait_until="networkidle")
            cp.wait_for_timeout(900)
            data = cp.evaluate("""() => ({
                navChain: document.querySelectorAll('nav.chain, #cpChain').length,
                stepCurrent: document.querySelectorAll('[aria-current="step"]').length,
                note: (() => {
                    const p = document.querySelector('.cp-chain-note');
                    return p ? {tag: p.tagName, text: p.textContent.trim().slice(0, 60)} : null;
                })()
            })""")
            assert data["navChain"] == 0, f"仍有 {data['navChain']} 个 nav.chain 伪导航"
            assert data["stepCurrent"] == 0, "仍有 aria-current=\"step\" 挂在非导航元素上"
            assert data["note"] and data["note"]["tag"] == "P", \
                f"没有 .cp-chain-note 段落：{data['note']}"
            return f"chain 已是 <p>（{data['note']['text'][:24]}…）、无 aria-current"

        for name, fn in [
            ("390 .tk-open ≥44px（特异性回归）", t_mobile_tk_open_44),
            ("#aiInsight 已扁平化（无实底/圆角）", t_ai_insight_flat),
            (".ai-basis 位置在卡片标题之下", t_ai_basis_after_head),
            (".chain 已降级为段落、无伪导航语义", t_chain_is_not_nav),
        ]:
            check(name, fn)

        # ================= 场景 E：第 11 轮五条回归断言 =================
        # 本轮修的都是"界面在说假话"与"量不出来就当修好了"两类：
        # JS 模板字面量里的 Jinja 注释会原样渲染、风险四段对读屏不可达、
        # 移动端主列被时限列挤到 149px、两处点击区差零点几像素、
        # 横滑表首列不钉住。全部读渲染结果或 computedStyle 判定。
        print("\n[场景 E] 第 11 轮回归")

        def t_no_jinja_in_modal():
            # JS 模板字面量里写 `{# … #}` 不会被 Jinja 解析，会原样出现在弹窗正文里。
            # 建单确认是外部后果动作，最不该在这里暴露源码。
            d = browser.new_context(viewport={"width": 1280, "height": 900})
            p = d.new_page()
            p.goto(BASE + "/departments", wait_until="networkidle")
            p.wait_for_timeout(8000)        # 判研发 5–9s 才回来，按钮要等它有
            btn = p.query_selector("[data-mklive], [data-mk]")
            if not btn:
                d.close()
                return "跳过：无可建单主题"
            btn.click()
            p.wait_for_timeout(1000)
            text = p.evaluate("""() => {
                const vis = [...document.querySelectorAll('.modal-mask')].filter(m => {
                    const r = m.getBoundingClientRect();
                    const cs = getComputedStyle(m);
                    return r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden';
                });
                if (!vis.length) return null;
                return vis[vis.length - 1].innerText;
            }""")
            d.close()
            assert text, "建单确认弹窗没有打开"
            assert "{#" not in text and "#}" not in text, \
                f"弹窗渲染了 Jinja 注释：{text[:120]}"
            assert "部门时限" in text, f"建单确认没交代部门时限：{text[:120]}"
            return "建单弹窗无 Jinja 残留，且交代了部门时限"

        def t_risk_parts_readable():
            # 色条以前 aria-hidden + 分项只在 <i title> 里：读屏只拿到总分。
            # 现在整条 role="img" + 含四项分值与口径的 aria-label。
            cp.goto(BASE + "/departments", wait_until="networkidle")
            cp.wait_for_timeout(8000)
            data = cp.evaluate("""() => {
                const tr = document.querySelector('#dpRisk tr[data-row]');
                if (!tr) return null;
                const t = tr.querySelector('.dp-risk-track');
                return {label: t && t.getAttribute('aria-label'),
                        role: t && t.getAttribute('role'),
                        hidden: t && t.getAttribute('aria-hidden')};
            }""")
            assert data, "风险表没有数据行"
            assert data["role"] == "img", f"色条 role={data['role']}，读屏仍拿不到分项"
            assert data["hidden"] is None, "色条仍标了 aria-hidden"
            lab = data["label"] or ""
            for k in ("量级", "负面率", "增速", "互动", "合计"):
                assert k in lab, f"aria-label 缺「{k}」：{lab[:80]}"
            return f"四段 aria-label 完整（{len(lab)} 字）"

        def t_sla_three_clocks():
            # 判断链必须写「工作小时」，不能写裸「48h」——48 工作小时≈7 个工作日，
            # 写成 h 会被读成自然小时。三把尺子的关系必须有一句话说清。
            cp.goto(BASE + "/tickets", wait_until="networkidle")
            cp.wait_for_timeout(1200)
            data = cp.evaluate("""() => ({
                chain: (document.querySelector('.cp-chain-note') || {}).innerText || '',
                note: (document.getElementById('tkSlaNote') || {}).textContent || ''
            })""")
            assert "工作小时" in data["chain"], f"判断链仍用裸 h：{data['chain'][:60]}"
            assert "48h" not in data["chain"], f"判断链仍有裸 48h：{data['chain'][:60]}"
            for k in ("部门响应", "优先级", "已超期"):
                assert k in data["note"], f"SLA 口径句没说清「{k}」"
            return "判断链用工作小时、三把尺子有话说"

        def t_delete_is_danger():
            # 删除工单不可逆，不能和「下一张 / 返回列表 / 关闭」同为灰 ghost。
            cp.goto(BASE + "/tickets", wait_until="networkidle")
            cp.wait_for_timeout(1200)
            data = cp.evaluate("""() => {
                const b = document.getElementById('tkDelete');
                if (!b) return null;
                return {cls: b.className, color: getComputedStyle(b).color,
                        last: b === b.parentElement.lastElementChild,
                        sep: b.previousElementSibling
                            ? b.previousElementSibling.className : ''};
            }""")
            assert data, "当前账号看不到删除按钮（无管理权限）"
            assert "btn-danger" in data["cls"], f"删除按钮没有降权视觉：{data['cls']}"
            assert data["last"], "删除按钮没有排到动作行末尾"
            assert "tk-danger-sep" in data["sep"], f"删除按钮前没有分隔线：{data['sep']}"
            return f"删除工单已降权（{data['color']}、末位、有分隔线）"

        def t_mobile_layout_44():
            # 主列被时限列挤到 149px、抽屉二级项 37.2、summary 43.1——
            # 三个都是"差一点点"，只有量出来才知道没修好。
            d = browser.new_context(viewport={"width": 390, "height": 844},
                                    device_scale_factor=2, is_mobile=True, has_touch=True)
            p = d.new_page()
            p.goto(BASE + "/tickets", wait_until="networkidle")
            p.wait_for_timeout(1200)
            tk = p.evaluate("""() => {
                const it = document.querySelector('.tk-item');
                if (!it) return null;
                const m = it.querySelector('.tk-item-main');
                return {mainW: m.getBoundingClientRect().width,
                        itemH: it.getBoundingClientRect().height,
                        over: document.documentElement.scrollWidth - document.documentElement.clientWidth};
            }""")
            p.evaluate("() => { const b = document.getElementById('navToggle'); if (b) b.click(); }")
            p.wait_for_timeout(400)
            nav = p.evaluate("""() => {
                [...document.querySelectorAll('[data-nav-toggle]')]
                    .forEach(c => { if (c.getAttribute('aria-expanded') !== 'true') c.click(); });
                const kids = [...document.querySelectorAll('.nav-child')].filter(k => k.offsetParent);
                const sum = [...document.querySelectorAll('summary')].filter(s => s.offsetParent);
                return {kid: kids.length ? kids[0].getBoundingClientRect().height : null,
                        kidN: kids.length,
                        sum: sum.length ? sum[0].getBoundingClientRect().height : null};
            }""")
            p.goto(BASE + "/data", wait_until="networkidle")
            p.wait_for_timeout(1000)
            sticky = p.evaluate("""() => {
                const box = document.querySelector('.table-scroll');
                const td = document.querySelector('.table tbody td');
                if (!box || !td) return null;
                const before = td.getBoundingClientRect().left;
                box.scrollLeft = 200;
                const after = td.getBoundingClientRect().left;
                return {pos: getComputedStyle(td).position,
                        moved: Math.round(Math.abs(after - before))};
            }""")
            d.close()
            assert tk, "390 工单列表没有 .tk-item"
            assert tk["mainW"] >= 230, f"390 工单主列只有 {tk['mainW']:.0f}px（目标 ≥230）"
            assert tk["over"] == 0, f"390 工单页横向溢出 {tk['over']}px"
            assert nav["kid"] is None or nav["kid"] >= 43.5, \
                f"抽屉二级项只有 {nav['kid']:.1f}px（{nav['kidN']} 条）"
            assert nav["sum"] is None or nav["sum"] >= 43.5, \
                f"summary 只有 {nav['sum']:.1f}px"
            assert sticky and sticky["pos"] == "sticky", "390 /data 首列没有钉住"
            assert sticky["moved"] <= 1, f"横滑 200px 后首列跟着跑了 {sticky['moved']}px"
            return (f"390 主列 {tk['mainW']:.0f}px、二级项 44、summary 44、首列 sticky")

        for name, fn in [
            ("建单弹窗无 Jinja 注释残留", t_no_jinja_in_modal),
            ("风险四段对读屏可读", t_risk_parts_readable),
            ("三把 SLA 尺子有话说", t_sla_three_clocks),
            ("删除工单已降权", t_delete_is_danger),
            ("390 主列 ≥230 且两处热区 ≥44", t_mobile_layout_44),
        ]:
            check(name, fn)

        # ================= 场景 F：第 12 轮（周带 + 装饰减法） =================
        print("\n[场景 F] 第 12 轮回归")

        def t_week_band_placement():
            """周带的落点必须等于后端算出来的 due。
            这一条挡的是最容易悄悄错的地方：前端自己加 48 小时（只有周末概念、
            没有节假日），或者把"推算"和"真实截止"画成同一个样子。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/departments", wait_until="networkidle")
            p.wait_for_timeout(2600)
            info = p.evaluate("""async () => {
                const wk = document.getElementById('dpWeek');
                const kpi = document.getElementById('dpKpi');
                if (!wk || !kpi) return {missing: true};
                const r = await fetch('/api/departments/risk?dataset_id=' + (CP.state && CP.state.dataset_id));
                const j = await r.json();
                const topics = (j.data && j.data.topics) || [];
                const cells = [...wk.querySelectorAll('.wk-cell')].map(c => ({
                    date: (c.querySelector('.wk-date') || {}).textContent || '',
                    rows: [...c.querySelectorAll('.wk-pin')].map(b => b.getAttribute('data-row'))
                }));
                const none = wk.querySelector('.wk-strip.is-none');
                return {
                    beforeKpi: !!(wk.compareDocumentPosition(kpi) & Node.DOCUMENT_POSITION_FOLLOWING),
                    hidden: wk.hidden,
                    topics: topics.map((t, i) => ({ i: i, due: t.due || '', basis: t.due_basis || '',
                                                    dept: !!t.department })),
                    cells: cells,
                    noneRows: none ? [...none.querySelectorAll('.wk-pin')].map(b => b.getAttribute('data-row')) : [],
                    ghosts: wk.querySelectorAll('.wk-pin.is-ghost').length,
                    reals: wk.querySelectorAll('.wk-pin.is-real').length
                };
            }""")
            d.close()
            assert not info.get("missing"), "周带容器 #dpWeek 或 KPI 容器不存在"
            assert info["beforeKpi"], "周带没有排在 KPI 之前（它是这一屏要回答的第一个问题）"
            assert not info["hidden"], "研判已成功但周带仍被隐藏"
            inside = {}
            for c in info["cells"]:
                m = re.match(r"(\d{1,2})-(\d{1,2})", c["date"].strip())
                if m:
                    inside["%d-%d" % (int(m.group(1)), int(m.group(2)))] = set(c["rows"])
            checked = 0
            for t in info["topics"]:
                row = str(t["i"])
                due = str(t["due"] or "")
                # due 是 "YYYY-MM-DDTHH:MM"，先切出日期再比，别用正则在整个串里
                # 找 dd-dd——"2026-11" 会先被匹配成 26-11，落点判定就全错了。
                dd = due[5:10] if len(due) >= 10 else ""
                mm = re.match(r"(\d{2})-(\d{2})", dd)
                if not t["dept"] or not mm:
                    # 未归口 / 没有时限：不许出现在任何日期格里
                    assert not any(row in v for v in inside.values()), \
                        f"第 {t['i']} 个主题没有归口或没有时限，却落在日期格里"
                    continue
                key = "%d-%d" % (int(mm.group(1)), int(mm.group(2)))
                if key not in inside:
                    continue          # 已超期或落在两周之外，由横条承接
                assert row in inside[key], f"第 {t['i']} 个主题的时限是 {due}，却没落在 {key} 那一列"
                checked += 1
            assert checked >= 1, "没有任何主题落进日期格，周带等于没画"
            return (f"周带 {checked} 个落点与后端 due 一致；未归口不进日期格；"
                    f"实心 {info['reals']} / 虚线 {info['ghosts']}")

        def t_week_band_390():
            d = browser.new_context(viewport={"width": 390, "height": 844},
                                    device_scale_factor=2, is_mobile=True, has_touch=True)
            p = d.new_page()
            p.goto(BASE + "/departments", wait_until="networkidle")
            p.wait_for_timeout(2600)
            info = p.evaluate("""() => {
                const wk = document.getElementById('dpWeek');
                const weeks = wk.querySelector('.wk-weeks');
                const list = wk.querySelector('.wk-list');
                const pins = [...list.querySelectorAll('.wk-pin')];
                return {
                    weeksShown: weeks ? getComputedStyle(weeks).display !== 'none' : null,
                    listShown: list ? getComputedStyle(list).display !== 'none' : null,
                    pins: pins.length,
                    minH: pins.length ? Math.min(...pins.map(b => b.getBoundingClientRect().height)) : null,
                    groups: [...list.querySelectorAll('.wk-group-head')].map(g => g.textContent.trim().slice(0, 6)),
                    over: document.documentElement.scrollWidth - document.documentElement.clientWidth
                };
            }""")
            d.close()
            assert info["listShown"], "390 下周带没有折成纵列（.wk-list 没显示）"
            assert not info["weeksShown"], f"390 下仍显示五列格子（{info['weeksShown']}）"
            assert info["pins"] >= 1, "390 周带一条主题都没有"
            assert info["minH"] >= 43.5, f"390 周带条目只有 {info['minH']:.1f}px 高"
            assert info["over"] == 0, f"390 研判页横向溢出 {info['over']}px"
            return f"390 纵列 {info['pins']} 条、分组 {'/'.join(info['groups'][:3])}、热区 {info['minH']:.0f}px"

        def t_decor_removed():
            """第 12 轮删的三样：侧栏 01–06、洞察入口 3.x、浅色视图的淡蓝光斑。
            它们都会悄悄长回来（复制模板、回滚样式），所以钉成断言。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/departments", wait_until="networkidle")
            found = p.evaluate("""() => ({
                navCode: document.querySelectorAll('.nav-code').length,
                bgBody: getComputedStyle(document.body).backgroundImage,
                bgMain: getComputedStyle(document.querySelector('.main')).backgroundImage,
                side: getComputedStyle(document.querySelector('.sidebar')).backgroundImage
            })""")
            p.goto(BASE + "/insights", wait_until="networkidle")
            entry = p.evaluate("document.querySelectorAll('.entry-no').length")
            d.close()
            assert found["navCode"] == 0, f"侧栏还有 {found['navCode']} 处 01–06 编号"
            assert entry == 0, f"洞察入口还有 {entry} 处 3.x 序号"
            for k, label in (("bgBody", "body"), ("bgMain", ".main"), ("side", ".sidebar")):
                assert "radial-gradient" not in found[k], f"{label} 的淡蓝光斑回来了：{found[k][:40]}"
            return "01–06 / 3.x / 三处 radial 光斑均已清除"

        for name, fn in [
            ("周带落点 = 后端 due", t_week_band_placement),
            ("390 周带折成纵列", t_week_band_390),
            ("装饰减法已落地", t_decor_removed),
        ]:
            check(name, fn)


        # ================= 场景 G：第 13 轮（三把尺子 / 复选框 / 周带窗口） =================
        print("\n[场景 G] 第 13 轮回归")

        def t_three_clocks():
            """详情面板的三把尺子：两条本单刻度 + 一条部门参照。
            数字必须来自后端 clocks（含工作日历），参照那条必须长得不像刻度——
            三格同款就等于又把「部门期望」和「本单计时」混着看了一次。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/tickets", wait_until="networkidle")
            p.wait_for_timeout(1500)
            tid = p.evaluate("""async () => {
                const r = await fetch('/api/tickets?limit=50');
                const j = await r.json();
                const list = (j.data && j.data.tickets) || [];
                const hit = list.find(t => t.department_id && t.status !== 'closed') || list[0];
                return hit ? hit.ticket_id : null;
            }""")
            assert tid, "列表里没有任何工单，无法验证详情面板"
            p.goto(f"{BASE}/tickets?ticket={tid}", wait_until="networkidle")
            p.wait_for_timeout(1800)
            info = p.evaluate("""async (tid) => {
                const r = await fetch('/api/tickets/' + tid);
                const j = await r.json();
                const t = j.data || {};
                const cards = [...document.querySelectorAll('#tkDetail .tk-clock')];
                const bg = el => getComputedStyle(el).backgroundColor;
                return {
                    n: cards.length,
                    heads: cards.map(c => (c.querySelector('.tc-head b') || {}).textContent || ''),
                    refs: cards.filter(c => c.classList.contains('is-ref')).length,
                    // 第 16 轮参照框改用 outline（border+圆角会构成"卡中卡"）：
                    // 断言看的是"有没有一条虚线框"，不是它挂在哪个属性上。
                    refBorder: cards.filter(c => c.classList.contains('is-ref'))
                        .map(c => { const st = getComputedStyle(c);
                            return (st.borderTopStyle === 'dashed' || st.outlineStyle === 'dashed')
                                ? 'dashed' : st.borderTopStyle + '/' + st.outlineStyle; }),
                    refRails: cards.filter(c => c.classList.contains('is-ref'))
                        .map(c => c.querySelector('.tc-rail').getBoundingClientRect().height),
                    nums: cards.map(c => (c.querySelector('.tc-num span') || {}).textContent || ''),
                    subs: cards.map(c => (c.querySelector('.tc-sub') || {}).textContent || ''),
                    lims: cards.map(c => (c.querySelector('.tc-lim') || {}).textContent || ''),
                    rails: cards.filter(c => !c.classList.contains('is-ref'))
                        .map(c => Math.round(c.querySelector('.tc-rail > span').getBoundingClientRect().width)),
                    clocks: t.clocks || null,
                    deptHours: t.clocks && t.clocks.department ? t.clocks.department.hours : null,
                    overN: document.querySelectorAll('#tkDetail .tk-clock.is-over').length
                };
            }""", tid)
            d.close()
            assert info["n"] == 3, f"详情面板有 {info['n']} 把尺子（应为 3）"
            assert info["refs"] == 1, f"参照框有 {info['refs']} 个（应为 1）"
            assert info["refBorder"] and all(b == "dashed" for b in info["refBorder"]), \
                f"部门参照框不是虚线（{info['refBorder']}）——它必须长得不像刻度"
            assert all(h < 1.5 for h in info["refRails"]), f"参照框画出了填充条（{info['refRails']}）"
            cl = info["clocks"] or {}
            assert cl.get("first") and cl.get("archive"), "后端没有返回 clocks（详情面板拿不到尺子数值）"
            fmt = lambda v: (("—" if v is None else f"{round(v, 1):.1f}").rstrip("0").rstrip("."))
            # 第 18 轮：主数字按状态换——未超时读「已用」，已超时读「已超多少」。
            # 两格都从建单起算，不换主数字就会永远显示同一个值（并排读像重复渲染）。
            def big(key):
                used, budget = cl[key]["used"], cl[key]["budget"]
                return fmt(used - budget) if (budget is not None and used is not None
                                              and used > budget) else fmt(used)
            assert info["nums"][0] == big("first"), \
                f"首次响应主数字是 {info['nums'][0]}，后端算出应为 {big('first')}"
            assert info["nums"][1] == big("archive"), \
                f"归档主数字是 {info['nums'][1]}，后端算出应为 {big('archive')}"
            # 两格都从建单起算：只要两格都超时且时限不同，主数字就必须不同，
            # 否则读者会以为是同一块渲染了两遍。
            b1, b2 = cl["first"]["budget"], cl["archive"]["budget"]
            if b1 is not None and b2 is not None and b1 != b2 \
                    and cl["first"]["used"] > b1 and cl["archive"]["used"] > b2:
                assert info["nums"][0] != info["nums"][1], \
                    f"两格都超时、时限不同，主数字却相同（{info['nums']}）——像重复渲染"
            assert all("工作小时" in x for x in info["lims"][:2]), \
                f"两格都应带「已用 X / 时限 Y 工作小时」小字：{info['lims']}"
            if info["deptHours"] is not None:
                assert info["nums"][2] == str(info["deptHours"]), \
                    f"部门期望显示 {info['nums'][2]}，部门配置是 {info['deptHours']}"
            return (f"3 格（{'/'.join(info['heads'])}），参照虚线且无填充条；"
                    f"数值与后端一致（{'/'.join(info['nums'])}），超时 {info['overN']} 格")

        def t_bulk_checkbox():
            """44px 热区，18px 视觉。第 11–12 轮遗留的那个"44px 空方框"是
            content-box padding 撑出来的：屏幕上只有一个大方框，勾选标记还是原生的指甲盖。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/tickets", wait_until="networkidle")
            p.wait_for_timeout(1500)
            info = p.evaluate("""() => {
                const cb = document.querySelector('.tk-bulk');
                if (!cb) return null;
                const s = getComputedStyle(cb);
                const before = getComputedStyle(cb, '::before');
                const unchecked = before.backgroundColor;
                cb.checked = true;
                const checked = getComputedStyle(cb, '::before').backgroundColor;
                const mark = !!getComputedStyle(cb, '::after').content
                    && getComputedStyle(cb, '::after').content !== 'none';
                const r = cb.getBoundingClientRect();
                return {w: r.width, h: r.height, appearance: s.appearance,
                        beforeW: before.width, beforeH: before.height,
                        unchecked: unchecked, checked: checked, mark: mark,
                        inline: cb.getAttribute('style') || ''};
            }""")
            d.close()
            assert info, "列表里没有复选框"
            assert info["inline"] == "", f"复选框还留着行内 style：{info['inline'][:40]}"
            assert info["appearance"] == "none", f"复选框仍是原生外观（{info['appearance']}）"
            assert info["w"] >= 43.5 and info["h"] >= 43.5, f"热区 {info['w']:.0f}×{info['h']:.0f}，不足 44"
            assert info["beforeW"] == "18px" and info["beforeH"] == "18px", \
                f"视觉方框 {info['beforeW']}×{info['beforeH']}（应为 18×18，不能再是大空框）"
            assert info["unchecked"] != info["checked"], "勾选后视觉方框没有变化（勾了看不出来）"
            assert info["mark"], "勾选标记（::after）不存在"
            return f"热区 {info['w']:.0f}px、视觉 18px、勾选变色"

        def t_band_calendar_rows():
            """一行必须是一个自然周。老实现是"从今天起数五个工作日"，
            周三打开时那一行会跨两个自然周，标题写「本周」却有两格是下周的日期。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/departments", wait_until="networkidle")
            p.wait_for_timeout(2600)
            info = p.evaluate("""() => {
                const wk = document.getElementById('dpWeek');
                if (!wk || wk.hidden) return {hidden: true};
                return {rows: [...wk.querySelectorAll('.wk-week')].map(r => ({
                    title: (r.querySelector('.wk-week-label b') || {}).textContent || '',
                    wds: [...r.querySelectorAll('.wk-cell .wk-date')].map(d => (d.textContent.match(/(周.)/) || [''])[0]),
                    cols: r.querySelectorAll('.wk-cell').length
                }))};
            }""")
            d.close()
            assert not info.get("hidden"), "周带被隐藏，无法验证行结构"
            allowed = {"本周", "下周", "再下一周", "三周后", "四周后"}
            for r in info["rows"]:
                assert r["cols"] == 5, f"「{r['title']}」那一行有 {r['cols']} 格（一个自然周应为 5）"
                assert r["wds"] == ["周一", "周二", "周三", "周四", "周五"], \
                    f"「{r['title']}」那一行的星期是 {r['wds']}，跨周了"
                assert r["title"] in allowed, f"行标题「{r['title']}」不是周次说法"
            return " / ".join(f"{r['title']}(5格)" for r in info["rows"])

        def t_band_strip_dates():
            """落不进窗口的主题必须自带日期。以前横条里只有名字，
            读者知道"更远"却不知道多远——那等于把信息藏进了一个形容词。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/departments", wait_until="networkidle")
            p.wait_for_timeout(2600)
            info = p.evaluate("""() => {
                const wk = document.getElementById('dpWeek');
                if (!wk || wk.hidden) return {hidden: true};
                return [...wk.querySelectorAll('.wk-strip')].map(s => ({
                    isNone: s.classList.contains('is-none'),
                    heads: [...s.querySelectorAll('.wk-pin .wk-nm em')].map(e => e.textContent.trim())
                }));
            }""")
            d.close()
            assert isinstance(info, list), "周带被隐藏，无法验证横条"
            dated = 0
            for s in info:
                for h in s["heads"]:
                    if s["isNone"]:
                        assert "未归口" in h, f"待归口横条里出现了带部门的条目：{h}"
                    else:
                        assert re.search(r"\d{2}-\d{2}$", h), f"横条条目没有说清落在哪天：{h}"
                        dated += 1
            assert dated >= 1, "已超期/窗口之外横条里一条都没有"
            return f"横条 {dated} 条均带 MM-DD；待归口仍写「未归口」"

        for name, fn in [
            ("三把尺子（两刻度 + 一参照）", t_three_clocks),
            ("复选框 44px 热区 / 18px 视觉", t_bulk_checkbox),
            ("周带每行是一个自然周", t_band_calendar_rows),
            ("横条条目自带日期", t_band_strip_dates),
        ]:
            check(name, fn)

        # ================= 场景 H：第 16 轮（工作小时口径 / 计时已停 / 手机首屏）=================
        print("\n[场景 H] 第 16 轮回归")

        def t_no_bare_hours():
            """「小时」不许裸写。SLA 的单位是工作小时（每工作日 7 小时），
            裸写「48h」会被读成 2 个自然日，而 48 工作小时≈9.6 天——差 4.8 倍、方向相反。
            这条断言扫的是渲染结果而不是源码：文案可以在任何一行拼出来。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            bad = []
            for path in ["/departments", "/tickets"]:
                p.goto(BASE + path, wait_until="networkidle")
                p.wait_for_timeout(2200)
                hits = p.evaluate("""() => {
                    const re = /(\\d+(?:\\.\\d+)?)\\s*小时/g, out = [];
                    document.querySelectorAll('body *').forEach(el => {
                        if (el.children.length) return;               // 只看叶子，避免父节点重复命中
                        const t = (el.textContent || '').trim();
                        if (!t || t.length > 120) return;
                        let m; re.lastIndex = 0;
                        while ((m = re.exec(t))) {
                            const i = m.index;
                            const before = t.slice(Math.max(0, i - 12), i);
                            // 「每工作日 7 小时」是解释工作日多长，不是时长，放行
                            if (before.includes('工作') || before.includes('工作日')) continue;
                            out.push(t.slice(0, 50));
                            break;
                        }
                    });
                    return out;
                }""")
                bad += [(path, h) for h in hits]
            d.close()
            assert not bad, f"出现不带「工作」的时长：{bad[:3]}"
            return "两页渲染文本中无裸「N 小时」"

        def t_stopped_clock():
            """计时已停 ≠ 还在跑。已响应的工单再报「还剩 N 工作小时」，
            就等于告诉来救火的人"还有时间"——而计时早就停了。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/tickets", wait_until="networkidle")
            p.wait_for_timeout(1500)
            tid = p.evaluate("""async () => {
                const r = await fetch('/api/tickets?limit=50');
                const j = await r.json();
                const list = (j.data && j.data.tickets) || [];
                const hit = list.find(t => t.first_response_at && t.status !== 'closed') || null;
                return hit ? hit.ticket_id : null;
            }""")
            assert tid, "没有已响应且未归档的工单，无法验证「计时已停」"
            p.goto(f"{BASE}/tickets?ticket={tid}", wait_until="networkidle")
            p.wait_for_timeout(1800)
            info = p.evaluate("""() => {
                const first = document.querySelector('#tkDetail .tk-clock');
                return {foot: first ? first.querySelector('.tc-foot').textContent.trim() : '',
                        done: first ? first.classList.contains('is-done') : false,
                        rail: first ? Math.round(first.querySelector('.tc-rail > span')
                            .getBoundingClientRect().width) : -1};
            }""")
            d.close()
            assert info["done"], "已响应的那一格没有 is-done（界面仍在按「还在跑」画）"
            assert "已响应" in info["foot"], f"首次响应脚注没写已响应：{info['foot']}"
            assert "还剩" not in info["foot"], f"已响应却还在报「还剩」：{info['foot']}"
            return f"脚注「{info['foot']}」"

        def t_mobile_ticket_first_screen():
            """390 的处置工单页，第一张工单必须在首屏。原来它排在 269px 的规则说明
            和 544px 的四张 KPI 之后（实测 y=1455），值班的人在手机上先读到一张说明书。"""
            d = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/tickets", wait_until="networkidle")
            p.wait_for_timeout(2600)
            info = p.evaluate("""() => {
                const it = document.querySelector('.tk-item');
                const fold = document.querySelector('.sugg-fold');
                return {y: it ? Math.round(it.getBoundingClientRect().top + scrollY) : 9999,
                        vh: innerHeight,
                        foldOpen: fold ? fold.open : null,
                        over: document.documentElement.scrollWidth - innerWidth};
            }""")
            d.close()
            assert info["foldOpen"] is False, "390 的规则卡没有收起（首屏仍被说明书占着）"
            assert info["y"] < info["vh"], f"第一张工单在 y={info['y']}，超出首屏 {info['vh']}"
            assert info["over"] <= 0, f"390 出现横向溢出 {info['over']}px"
            return f"第一张工单 y={info['y']} < 首屏 {info['vh']}，规则卡默认收起"

        def t_pin_unit_and_owner():
            """周带 pin 的主数字必须带单位，第二行必须带责任人。
            「57」没有单位时，读者要滚到页脚才知道它是风险指数；
            只有部门没有人时，「今天该催谁」答不出来。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/departments", wait_until="networkidle")
            p.wait_for_timeout(2600)
            info = p.evaluate("""() => [...document.querySelectorAll('#dpWeek .wk-cells .wk-pin')].map(b => ({
                num: (b.querySelector('b') || {}).textContent || '',
                line2: (b.querySelector('.wk-nm em') || {}).textContent || ''
            }))""")
            d.close()
            assert info, "周带里没有 pin，无法验证"
            for x in info:
                assert x["num"].strip().endswith("分"), f"pin 主数字没有单位：{x['num']}"
                assert x["line2"].strip(), f"pin 第二行为空：{x}"
                assert ("老师" in x["line2"]) or ("未归口" in x["line2"]), \
                    f"pin 第二行既没有责任人也不是未归口：{x['line2']}"
            return f"{len(info)} 个 pin 均带「分」与责任人"

        def t_pin_focus_handoff():
            """点 pin 之后焦点必须跟着走。原来页面滚到 y=1017，焦点还留在屏外的 pin 上
            （top=-626）——WCAG 2.4.7 失败，键盘用户的下一个 Tab 落在看不见的元素上。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/departments", wait_until="networkidle")
            p.wait_for_timeout(2600)
            p.locator(".wk-pin").first.click()
            p.wait_for_timeout(900)
            after = p.evaluate("""() => {
                const a = document.activeElement, r = a.getBoundingClientRect();
                return {id: a.id || '', cls: a.className || '',
                        inView: r.top > -5 && r.top < innerHeight,
                        back: document.querySelectorAll('[data-backweek]').length};
            }""")
            assert "dpDetail" in after["id"], f"点 pin 后焦点不在详情卡上（{after['id'] or after['cls']}）"
            assert after["inView"], "焦点落在屏幕外（WCAG 2.4.7）"
            assert after["back"] == 1, "详情里没有「回到周带」"
            p.locator("[data-backweek]").first.click()
            p.wait_for_timeout(900)
            back = p.evaluate("""() => {
                const a = document.activeElement, r = a.getBoundingClientRect();
                return {cls: a.className || '', inView: r.top > -5 && r.top < innerHeight};
            }""")
            d.close()
            assert "wk-pin" in back["cls"], f"回到周带后焦点不在 pin 上（{back['cls']}）"
            assert back["inView"], "回到周带后焦点仍在屏幕外"
            return "点 pin → 焦点进详情；回到周带 → 焦点回 pin，两者都在视口内"

        for name, fn in [
            ("「小时」不得裸写（全站渲染文本）", t_no_bare_hours),
            ("已响应的工单不再报「还剩」", t_stopped_clock),
            ("390 首屏能看到第一张工单", t_mobile_ticket_first_screen),
            ("周带 pin 带单位与责任人", t_pin_unit_and_owner),
            ("点 pin 焦点交还 / 回到周带", t_pin_focus_handoff),
        ]:
            check(name, fn)

        # ================= 场景 I：第 17 轮（收掉第 16 轮「仍未做」的 6 条）=================
        print("\n[场景 I] 第 17 轮回归")

        def t_actions_before_evidence():
            """「提交复核」现在必须在证据与时间线之前。
            第 16 轮实测它在 5 条证据 + 5 步时间线之后（390 约 1500px）——
            打开这一页的人是来救火的，先读五条评论才知道能做什么是反的。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/tickets", wait_until="networkidle")
            p.wait_for_timeout(2600)
            info = p.evaluate("""() => {
                const acts = document.querySelector('#tkDetail .tk-acts');
                const ev = document.querySelector('#tkDetail .tk-evidence');
                const tl = document.querySelector('#tkDetail .tk-timeline');
                const titles = [...document.querySelectorAll('#tkDetail .tk-block-title')]
                    .map(e => e.textContent.trim().split(/\\s/)[0]);
                if (!acts || !ev || !tl) return {titles, order: null};
                // 4 = 后者在前（DOCUMENT_POSITION_FOLLOWING）
                const beforeEv = (acts.compareDocumentPosition(ev) & 4) === 4;
                const beforeTl = (acts.compareDocumentPosition(tl) & 4) === 4;
                return {titles, order: beforeEv && beforeTl,
                        y: Math.round(acts.getBoundingClientRect().top + scrollY)};
            }""")
            d.close()
            assert info["order"] is not None, "详情里没有可执行操作 / 证据 / 时间线，无法判定顺序"
            assert info["order"], f"可执行操作仍排在证据或时间线之后：{info['titles']}"
            assert info["titles"][0] == "可执行操作", f"动作块不是第一块：{info['titles']}"
            return f"块顺序 {info['titles'][:3]}，动作块 y={info['y']}"

        def t_kpi_one_metric_each():
            """第四张卡不能再是"一卡两指标"：一段时长（平均处置时长）与一个比率
            （按时归档率）共用一个卡位时，读者会把它们读成同一个指标的两种说法。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/tickets", wait_until="networkidle")
            p.wait_for_timeout(2200)
            info = p.evaluate("""() => [...document.querySelectorAll('#tkKpi .kpi')].map(c => ({
                label: (c.querySelector('.kpi-label') || {}).textContent.trim(),
                value: (c.querySelector('.kpi-value') || {}).textContent.trim(),
                foot: (c.querySelector('.kpi-foot') || {}).textContent.trim()
            }))""")
            d.close()
            labels = [x["label"] for x in info]
            assert len(info) == 5, f"KPI 应为 5 张，实际 {len(info)}：{labels}"
            assert "平均处置时长" in labels, f"缺「平均处置时长」：{labels}"
            assert "按时归档" in labels, f"缺「按时归档」：{labels}"
            dur = info[labels.index("平均处置时长")]
            ot = info[labels.index("按时归档")]
            assert "按时归档" not in dur["foot"], f"「平均处置时长」卡里仍混着按时率：{dur['foot']}"
            # 样本不足 10 单时不许出现百分比，只给分子分母并说明为什么不换算
            if ot["value"] == "—":
                assert "样本不足" in ot["foot"] or "暂无分母" in ot["foot"] \
                    or "还没有归档单" in ot["foot"], f"按时率给不出数也没说原因：{ot['foot']}"
            return f"5 张卡：{labels}"

        def t_risk_filter_and_dim_url():
            """表要能按「未归口 / 已超期」筛；选中行要写进 URL，
            否则"看宿舍住宿那一条"没法发给同事（工单页从第 10 轮起就有 ?ticket=）。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/departments", wait_until="networkidle")
            p.wait_for_timeout(2800)
            chips = p.evaluate("""() => [...document.querySelectorAll('.dp-risk-filters .chip')]
                .map(c => ({k: c.dataset.rf, t: c.textContent.trim()}))""")
            assert [c["k"] for c in chips] == ["all", "none", "overdue"], f"筛选 chip 不对：{chips}"

            p.locator('.dp-risk-filters .chip[data-rf="none"]').click()
            p.wait_for_timeout(600)
            none_view = p.evaluate("""() => {
                const rows = [...document.querySelectorAll('#dpRisk tr[data-row]')];
                return {n: rows.length,
                        depts: rows.map(r => (r.querySelector('.dp-td-dept') || {}).textContent.trim()),
                        all: document.querySelectorAll('.dp-rf-count').length};
            }""")
            assert none_view["n"] > 0, "「未归口」筛完一行也没有（演示库应有未归口主题）"
            for x in none_view["depts"]:
                assert "未归口" in x, f"「未归口」筛选里混进了已归口的行：{x}"

            # 回到全部，选中第一行 → URL 要出现 dim=
            p.locator('.dp-risk-filters .chip[data-rf="all"]').click()
            p.wait_for_timeout(400)
            p.locator('#dpRisk tr[data-row]').first.click()
            p.wait_for_timeout(700)
            q = p.evaluate("() => new URLSearchParams(location.search).get('dim')")
            assert q, "选中行没有写进 URL（?dim=）"

            # 深链：带 ?dim= 重新打开，那一行要选中且详情已展开
            p.goto(BASE + "/departments?" + p.evaluate("() => location.search.slice(1)"),
                   wait_until="networkidle")
            p.wait_for_timeout(2800)
            deep = p.evaluate("""() => {
                const on = document.querySelector('#dpRisk tr[data-row].on');
                return {dim: on ? (on.querySelector('b') || {}).textContent.trim() : null,
                        detail: !!document.querySelector('#dpDetail .dp-detail')};
            }""")
            d.close()
            assert deep["dim"] and deep["detail"], f"带 ?dim= 打开没有还原选中行：{deep}"
            return f"筛出 {none_view['n']} 行未归口；URL dim={q}；深链还原到「{deep['dim']}」"

        def t_topbar_more_menu():
            """重命名 / 主题 / 术语三件事收进「更多」：顶栏不再常驻三个低频图标，
            菜单仍要一次点击可达，且每一项都是可点目标（≤720 为 44px）。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/departments", wait_until="networkidle")
            p.wait_for_timeout(1800)
            shape = p.evaluate("""() => {
                const more = document.querySelector('#topMore');
                if (!more) return null;
                const right = document.querySelector('.topbar-right');
                return {inMenu: ['dsRename', 'themeToggle', 'glossaryBtn']
                            .every(id => !!more.querySelector('#' + id)),
                        // 顶栏直接子项里不该再有三颗独立图标
                        direct: ['dsRename', 'themeToggle', 'glossaryBtn']
                            .filter(id => right.querySelector(':scope > #' + id)).length};
            }""")
            assert shape, "顶栏没有「更多」容器"
            assert shape["inMenu"], "三个低频动作没有全部进「更多」"
            assert shape["direct"] == 0, "顶栏仍直接摆着低频图标按钮"

            p.locator("#topMore > summary").click()
            p.wait_for_timeout(300)
            opened = p.evaluate("""() => {
                const pop = document.querySelector('.top-more-pop');
                const r = pop.getBoundingClientRect();
                return {vis: r.height > 0, right: r.right, left: r.left, vw: innerWidth,
                        items: [...pop.querySelectorAll('.top-more-item')].map(i => i.id)};
            }""")
            assert opened["vis"], "「更多」点开没有面板"
            assert opened["left"] >= -1 and opened["right"] <= opened["vw"] + 1, \
                f"菜单超出视口：{opened}"
            assert opened["items"] == ["dsRename", "themeToggle", "glossaryBtn"], \
                f"菜单项不对：{opened['items']}"
            # 键盘：Esc 收起并把焦点交回按钮
            p.keyboard.press("Escape")
            p.wait_for_timeout(250)
            closed = p.evaluate("""() => ({open: document.querySelector('#topMore').open,
                                           focus: document.activeElement.id})""")
            d.close()
            assert closed["open"] is False, "Esc 没收起「更多」"
            assert closed["focus"] == "topMoreBtn", f"Esc 后焦点没回到按钮（{closed['focus']}）"
            return f"菜单 {len(opened['items'])} 项、不越界；Esc 收起且焦点回按钮"

        def t_clock_foot_readable():
            """三把尺子的脚注是判断「超没超」的唯一一句话。
            micro 档落在 --neg-soft 上只有 4.76:1，超期那一格必须换更深的字色。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/tickets", wait_until="networkidle")
            p.wait_for_timeout(2600)
            cells = p.evaluate("""() => {
                function bgOf(el) {
                    let n = el;
                    while (n && n !== document.documentElement) {
                        const c = getComputedStyle(n).backgroundColor;
                        if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c;
                        n = n.parentElement;
                    }
                    return 'rgb(255, 255, 255)';
                }
                return [...document.querySelectorAll('#tkDetail .tk-clock')].map(c => {
                    const f = c.querySelector('.tc-foot');
                    const cs = f ? getComputedStyle(f) : null;
                    return {color: cs ? cs.color : '', bg: bgOf(c),
                            fs: cs ? parseFloat(cs.fontSize) : 0,
                            over: c.classList.contains('is-over')};
                });
            }""")
            d.close()
            assert cells, "详情里没有三把尺子，无法验证脚注"
            worst = None
            for c in cells:
                a, b = luminance(parse_rgb(c["color"])), luminance(parse_rgb(c["bg"]))
                hi, lo = max(a, b), min(a, b)
                ratio = (hi + 0.05) / (lo + 0.05)
                assert c["fs"] >= 12, f"脚注字号 {c['fs']}px 低于 12px"
                if worst is None or ratio < worst[0]:
                    worst = (ratio, c)
                assert ratio >= 4.5, f"脚注对比度 {ratio:.2f}:1 低于 4.5（over={c['over']}）"
            return f"最差对比度 {worst[0]:.2f}:1（over={worst[1]['over']}），字号 {worst[1]['fs']}px"

        for name, fn in [
            ("详情里动作块排在证据与时间线之前", t_actions_before_evidence),
            ("工单 KPI 一卡一指标（时长与按时率拆开）", t_kpi_one_metric_each),
            ("风险表可按未归口筛 + 选中行写 URL", t_risk_filter_and_dim_url),
            ("顶栏低频动作收进「更多」", t_topbar_more_menu),
            ("三把尺子脚注可读（≥12px 且 ≥4.5:1）", t_clock_foot_readable),
        ]:
            check(name, fn)


        # ================= 场景 J：第 18 轮（critique 后：主张上首屏 / 单行表头 / 触控）=================
        print("\n[场景 J] 第 18 轮回归")

        def t_home_speaks_product():
            """驾驶舱正文必须出现产品自己的三个词：风险指数 / 归口 / 工作小时。
            第 17 轮实测这三个词在首页出现 0 次——评委连点三页只看到 KPI 与 AI 长文，
            任何舆情 SaaS 都能原样复用。第 18 轮把研判前三条搬上首屏。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/", wait_until="networkidle")
            p.wait_for_timeout(3000)
            info = p.evaluate("""() => {
                const strip = document.querySelector('#todayStrip');
                const rows = [...document.querySelectorAll('.dp-today-row')];
                const kpi = document.querySelector('#kpiRow');
                return {shown: !!strip && !strip.classList.contains('is-hidden'),
                        rows: rows.length,
                        first: rows.length ? rows[0].getAttribute('href') : '',
                        who: rows.length ? (rows[0].querySelector('.dpt-who') || {}).textContent.trim() : '',
                        text: (document.querySelector('.content') || {}).textContent || '',
                        stripY: strip ? Math.round(strip.getBoundingClientRect().top + scrollY) : -1,
                        kpiY: kpi ? Math.round(kpi.getBoundingClientRect().top + scrollY) : -1};
            }""")
            d.close()
            assert info["shown"], "驾驶舱没有「今天要办的三件事」"
            assert info["rows"] >= 1, "三件事里一行也没有"
            for w in ("风险指数", "归口", "工作小时"):
                assert w in info["text"], f"驾驶舱正文仍没有「{w}」"
            assert "/departments?dim=" in info["first"], f"行链接没有落到研判页对应行：{info['first']}"
            assert info["who"], "行里没有归口部门与责任人"
            assert info["stripY"] < info["kpiY"], "三件事排在 KPI 之后（首屏看不到）"
            return f"{info['rows']} 行，首行「{info['who']}」，排在 KPI 之前（y={info['stripY']} < {info['kpiY']}）"

        def t_strip_not_pending_wording():
            """横幅的 pending 口径是"所有未归档"，而工单页「待分派」只是其中一格。
            首页写「待处置 5 单」、点进去「待分派 0」——值守的人会以为数字错了。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/", wait_until="networkidle")
            p.wait_for_timeout(2400)
            txt = p.evaluate("() => (document.querySelector('#ticketStrip') || {}).textContent || ''")
            d.close()
            if not txt.strip():
                return "无工单权限，横幅不显示（跳过）"
            assert "未结" in txt, f"横幅仍没写「未结」：{txt.strip()[:40]}"
            assert "待处置" not in txt, f"横幅仍在用「待处置」（与工单页「待分派」冲突）：{txt.strip()[:40]}"
            assert "不随上方筛选" in txt, "横幅没说明它不随筛选变化"
            return txt.replace("\n", " ").strip()[:60]

        def t_th_single_line():
            """`.dp-th-hint` 是 block，排序箭头落在它之后 → 表头三行、th 高 76px。
            第 18 轮改成内联 + 箭头绝对定位，必须回到单行。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/departments", wait_until="networkidle")
            p.wait_for_timeout(2800)
            info = p.evaluate("""() => {
                const th = document.querySelector('#dpRisk th.dp-col-risk');
                if (!th) return null;
                const r = th.getBoundingClientRect();
                return {h: Math.round(r.height), lines: Math.round(r.height / 18)};
            }""")
            d.close()
            assert info, "风险表没有风险指数表头"
            assert info["h"] <= 52, f"表头 {info['h']}px（>52 说明仍折成两三行）"
            return f"表头 {info['h']}px，单行"

        def t_touch_targets_390():
            """证据页 390 的可点目标：多选框（label 包裹）、展开全文、排行行都要 ≥44。
            第 17 轮实测 34 个 <44 的目标，其中 20 个是 13×13 的复选框。"""
            d = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/evidence", wait_until="networkidle")
            p.wait_for_timeout(2600)
            info = p.evaluate("""() => {
                const pick = document.querySelector('.ev-pick');
                const more = document.querySelector('.ev-more');
                const row = document.querySelector('.rank-row');
                const h = el => el ? Math.round(el.getBoundingClientRect().height) : -1;
                const w = el => el ? Math.round(el.getBoundingClientRect().width) : -1;
                // 只算"有效目标"：label 包住的 input 不算，独立可点的才算
                const targets = [...document.querySelectorAll(
                    'a,button,select,summary,[role=button],label.ev-pick')];
                const small = targets.filter(e => {
                    const r = e.getBoundingClientRect();
                    if (!r.width && !r.height) return false;
                    if (r.left < 0 || r.left > innerWidth) return false;
                    if (e.classList.contains('skip-link')) return false;
                    return r.width < 44 || r.height < 44;
                });
                return {pick: [w(pick), h(pick)], more: h(more), row: h(row),
                        small: small.length, samples: small.slice(0, 4).map(e => e.className || e.tagName)};
            }""")
            d.close()
            assert info["pick"][0] >= 44 and info["pick"][1] >= 44, f"多选框热区只有 {info['pick']}"
            assert info["more"] >= 44, f"「展开全文」高 {info['more']}px"
            assert info["row"] >= 44, f"排行行高 {info['row']}px"
            return f"多选 {info['pick']}、展开 {info['more']}、排行 {info['row']}；其余小目标 {info['small']} 个 {info['samples']}"

        def t_disabled_readable():
            """`.btn:disabled{opacity:.55}` 把字合成到 2.75:1。
            禁用控件 WCAG 有豁免，但读它的人正是靠它判断"这条不可用"。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/evidence", wait_until="networkidle")
            p.wait_for_timeout(2200)
            cells = p.evaluate("""() => {
                function bgOf(el) { let n = el;
                    while (n && n !== document.documentElement) {
                        const c = getComputedStyle(n).backgroundColor;
                        if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c;
                        n = n.parentElement; }
                    return 'rgb(255, 255, 255)'; }
                return [...document.querySelectorAll('.btn:disabled')].slice(0, 3)
                    .map(e => ({t: e.textContent.trim().slice(0, 8), c: getComputedStyle(e).color,
                                bg: bgOf(e), op: getComputedStyle(e).opacity}));
            }""")
            d.close()
            if not cells:
                return "当前没有禁用按钮（跳过）"
            worst = None
            for c in cells:
                a, b2 = luminance(parse_rgb(c["c"])), luminance(parse_rgb(c["bg"]))
                hi, lo = max(a, b2), min(a, b2)
                rr = (hi + 0.05) / (lo + 0.05)
                if worst is None or rr < worst[0]:
                    worst = (rr, c)
            assert worst[0] >= 4.5, f"禁用按钮「{worst[1]['t']}」对比度 {worst[0]:.2f}:1"
            return f"最差 {worst[0]:.2f}:1（{worst[1]['t']}，opacity={worst[1]['op']}）"

        for name, fn in [
            ("驾驶舱正文出现风险指数 / 归口 / 工作小时", t_home_speaks_product),
            ("工单横幅改口径为「未结」并说明不随筛选", t_strip_not_pending_wording),
            ("风险表头回到单行", t_th_single_line),
            ("390 证据页可点目标 ≥44px", t_touch_targets_390),
            ("禁用按钮仍可读（≥4.5:1）", t_disabled_readable),
        ]:
            check(name, fn)


        # ================= 场景 K：第 19 轮（等级色 / 单一单位 / 首屏 / 批量 / 纹理）=================
        print("\n[场景 K] 第 19 轮回归")

        def t_level_color_and_text():
            """首页 57 的等级**颜色与文字**都必须生效。
            第 18 轮这里类名写成了 lv-midhigh，而 CSS 是 .dpt-score.midhigh——
            选择器永不匹配，57/40/38 全是同一种黑。只靠颜色也不行：等级要有字。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/", wait_until="networkidle")
            p.wait_for_timeout(3000)
            rows = p.evaluate("""() => [...document.querySelectorAll('.dp-today-row')].map(r => {
                const sc = r.querySelector('.dpt-score'); const lv = r.querySelector('.dpt-lv');
                return {score: sc.textContent.trim(), cls: sc.className,
                        color: getComputedStyle(sc).color,
                        lv: lv ? lv.textContent.trim() : '',
                        lvColor: lv ? getComputedStyle(lv).color : '',
                        aria: r.getAttribute('aria-label') || '',
                        when: (r.querySelector('.dpt-due') || {}).textContent || ''};
            })""")
            d.close()
            assert rows, "首页三件事没有行"
            ink = "rgb(23, 32, 51)"
            for r in rows:
                assert "lv-" not in r["cls"], f"类名仍是 lv- 前缀（CSS 永远匹配不到）：{r['cls']}"
                assert r["lv"], f"{r['score']} 没有等级文字"
                assert r["color"] == r["lvColor"], \
                    f"分数与等级颜色不一致：{r['color']} vs {r['lvColor']}"
                assert "风险指数" in r["aria"] and r["lv"] in r["aria"], \
                    f"读屏名没有风险指数与等级：{r['aria']}"
            assert any(r["color"] != ink for r in rows), "三行分数仍全是正文色（等级色没生效）"
            return f"{len(rows)} 行；首行 {rows[0]['score']}「{rows[0]['lv']}」{rows[0]['color']}"

        def t_single_unit_duration():
            """一个量只给一个单位。以前是「1 个工作日 6 工作小时」两个单位粘在一起，
            在 86px 的列表格里被折成两行、读成两个数。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/tickets", wait_until="networkidle")
            p.wait_for_timeout(2600)
            info = p.evaluate("""() => {
                const cells = [...document.querySelectorAll('#tkList .tk-item-sla')].map(e => e.textContent.replace(/\\s+/g, ' ').trim());
                const other = [...document.querySelectorAll('#tkDetail .tk-clock .tc-lim, #tkDetail .tc-foot')]
                    .map(e => e.textContent.replace(/\\s+/g, ' ').trim());
                return {cells, other};
            }""")
            d.close()
            assert info["cells"], "列表里没有时限格"
            bad = [c for c in info["cells"] if "个工作日" in c and "工作小时" in c]
            assert not bad, f"列表里仍有两个单位并存：{bad[:2]}"
            return f"{len(info['cells'])} 格均为单一单位，例：{info['cells'][0][:24]}"

        def t_tickets_first_screen():
            """规则未命中时桌面也收起：215px 的「本次未命中 + 为什么不报警」说明书
            会把第一张单推到 y≈550，而这一页每天要做的是列表与详情。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/tickets", wait_until="networkidle")
            p.wait_for_timeout(2600)
            info = p.evaluate("""() => {
                const fold = document.querySelector('.sugg-fold');
                const hit = document.querySelector('.tk-sugg');
                const it = document.querySelector('.tk-item');
                const acts = document.querySelector('#tkDetail .tk-acts');
                return {open: fold ? fold.open : null, hasHit: !!hit,
                        y: it ? Math.round(it.getBoundingClientRect().top + scrollY) : 9999,
                        actsY: acts ? Math.round(acts.getBoundingClientRect().top) : -1,
                        actsPos: acts ? getComputedStyle(acts.closest('.tk-block')).position : '',
                        vh: innerHeight};
            }""")
            d.close()
            if not info["hasHit"]:
                assert info["open"] is False, "未命中时规则卡仍展开（首屏被说明书占着）"
            assert info["y"] < 700, f"第一张工单在 y={info['y']}，仍被上方的卡片推得太远"
            assert info["actsPos"] == "sticky", "动作块没有吸底，900px 视口下主操作会被裁"
            return f"规则卡 open={info['open']}；首单 y={info['y']}；动作块 position={info['actsPos']}"

        def t_badge_scope():
            """顶栏角标按 scope 取数：管理员取「未结」，处置员取「我的」。
            以前只取 mine，管理员 mine=0 时整枚角标消失——3 张超期单没有提示。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/departments", wait_until="networkidle")
            p.wait_for_timeout(2400)
            info = p.evaluate("""async () => {
                const r = await fetch('/api/tickets/badge'); const j = await r.json();
                const b = document.querySelector('#ticketBadge');
                return {scope: j.data && j.data.scope, pending: j.data && j.data.pending,
                        hidden: b.classList.contains('is-hidden'), aria: b.getAttribute('aria-label') || '',
                        text: b.textContent.trim()};
            }""")
            d.close()
            if not info["pending"]:
                return "当前没有未结工单（跳过）"
            assert info["scope"] in ("all", "dept", "mine"), f"接口没回 scope：{info['scope']}"
            if info["scope"] != "mine":
                assert not info["hidden"], "管理员/部门账号的角标被隐藏了"
                assert "未结" in info["aria"], f"角标读屏名没写「未结」：{info['aria']}"
            return f"scope={info['scope']}，角标「{info['text'][:6]}」，aria=「{info['aria']}」"

        def t_evidence_select_page():
            """1,969 条结果里逐条点勾选不现实。全选本页只作用于当前页，按钮同时报告状态。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/evidence", wait_until="networkidle")
            p.wait_for_timeout(2400)
            before = p.evaluate("""() => ({n: document.querySelectorAll('.ev-bulk:checked').length,
                                            btn: (document.querySelector('#evSelectPage')||{}).textContent})""")
            p.locator("#evSelectPage").click()
            p.wait_for_timeout(400)
            after = p.evaluate("""() => ({n: document.querySelectorAll('.ev-bulk:checked').length,
                                           btn: (document.querySelector('#evSelectPage')||{}).textContent,
                                           bar: (document.querySelector('#bulkBar')||{}).textContent||''})""")
            p.locator("#evSelectPage").click()
            p.wait_for_timeout(300)
            undo = p.evaluate("() => document.querySelectorAll('.ev-bulk:checked').length")
            d.close()
            assert before["n"] == 0, "初始就有勾选"
            assert after["n"] > 0, "点了全选本页没有勾上任何一条"
            assert "取消全选" in after["btn"], f"按钮文案没切换：{after['btn']}"
            assert "已选" in after["bar"], "批量条没有出现已选数量"
            assert undo == 0, "再点一次没有取消勾选"
            return f"全选 {after['n']} 条 → 取消归零"

        def t_negative_has_texture():
            """红绿对红绿色盲几乎同亮：堆叠条的负面段必须有第二编码（斜纹）。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/", wait_until="networkidle")
            p.wait_for_timeout(2600)
            info = p.evaluate("""() => {
                const seg = document.querySelector('.sent-seg[data-sent="negative"]');
                const key = document.querySelector('.sent-key-dot.is-neg');
                if (!seg) return null;
                return {segImg: getComputedStyle(seg).backgroundImage.slice(0, 24),
                        keyImg: key ? getComputedStyle(key).backgroundImage.slice(0, 24) : ''};
            }""")
            d.close()
            if not info:
                return "当前筛选下没有负面段（跳过）"
            assert info["segImg"] != "none", "负面段没有纹理（只靠颜色区分）"
            assert "svg" in info["segImg"].lower(), f"纹理不是图案而是纯色：{info['segImg']}"
            return f"负面段纹理 {info['segImg']}…；图例同步={bool(info['keyImg'] != 'none')}"

        def t_level_color_consistent():
            """同一个主题的分数，在首页、风险表、周带**必须是同一种颜色**。
            第 19 轮周带的「已超期」横条把 pin 的分数涂成红色，于是 57 在首页是橙、
            在周带是红——读者会当成两个指标；而且它是色盲用户在周带唯一的非文字线索。"""
            d = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            p = d.new_page()
            p.goto(BASE + "/", wait_until="networkidle")
            p.wait_for_timeout(2800)
            home = p.evaluate("""() => [...document.querySelectorAll('.dp-today-row')].map(r => ({
                dim: (r.querySelector('.dpt-dim') || {}).textContent || '',
                lv: (r.querySelector('.dpt-lv') || {}).textContent || '',
                color: getComputedStyle(r.querySelector('.dpt-score')).color}))""")
            p.goto(BASE + "/departments", wait_until="networkidle")
            p.wait_for_timeout(3000)
            wk = p.evaluate("""() => [...document.querySelectorAll('.wk-pin')]
                .filter(b => b.getBoundingClientRect().height > 0)
                .map(b => ({dim: (b.querySelector('.wk-nm') || {}).textContent || '',
                            lv: (b.querySelector('.wk-lv') || {}).textContent || '',
                            color: getComputedStyle(b.querySelector('b')).color}))""")
            d.close()
            assert home and wk, "首页或周带没有数据"
            pairs = 0
            for h in home:
                dim = h["dim"]
                if h["lv"] and dim.endswith(h["lv"]):
                    dim = dim[:-len(h["lv"])]
                m = [w for w in wk if w["dim"].startswith(dim)]
                if not m:
                    continue
                pairs += 1
                assert m[0]["color"] == h["color"], \
                    f"「{dim}」在首页 {h['color']}、在周带 {m[0]['color']}（同一档两种颜色）"
            assert pairs >= 1, "首页与周带没有可对照的主题"
            assert all(w["lv"] for w in wk), "周带 pin 没有等级文字"
            return f"{pairs} 个主题跨页同色；pin 均带等级文字"

        for name, fn in [
            ("首页等级颜色与文字都生效", t_level_color_and_text),
            ("同一主题在首页与周带同色", t_level_color_consistent),
            ("时限只给一个单位", t_single_unit_duration),
            ("工单页首屏先给工单与可执行操作", t_tickets_first_screen),
            ("顶栏角标按 scope 取数并说明口径", t_badge_scope),
            ("证据页全选本页可切换", t_evidence_select_page),
            ("负面段有第二编码（斜纹）", t_negative_has_texture),
        ]:
            check(name, fn)

        c.close()
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
