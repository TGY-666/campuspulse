# -*- coding: utf-8 -*-
"""前端巡检：把五个页面当成"第一次点进来的用户"走一遍。

只收录三类问题：
  1. pageerror  —— JS 抛异常（页面某个功能确定是坏的）
  2. console error —— 框架/资源级错误
  3. 失败请求（>=400）—— 页面要的数据没拿到

截图落到 screenshots/audit/，出问题的地方能直接看。
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8010"
OUT = pathlib.Path("screenshots/audit")
PAGES = [("/", "dashboard", "驾驶舱"), ("/insights", "insights", "洞察"),
         ("/agent", "agent", "AI 工作台"), ("/data", "data", "数据管理"),
         ("/evidence", "evidence", "证据检索")]

# 这些 4xx 是浏览器自己发的，与功能无关
IGNORE_URL = ("favicon.ico", "chrome-extension", "devtools")


def audit(pw, results):
    browser = pw.chromium.launch(channel="msedge")
    for path, slug, label in PAGES:
        ctx = browser.new_context(viewport={"width": 1560, "height": 1000})
        page = ctx.new_page()
        errs, cons, bad_req = [], [], []
        page.on("pageerror", lambda e: errs.append(str(e)[:220]))
        page.on("console", lambda m: cons.append(m.text[:220]) if m.type == "error" else None)
        page.on("response", lambda r: bad_req.append(f"{r.status} {r.url.replace(BASE,'')[:90]}")
                if r.status >= 400 and not any(k in r.url for k in IGNORE_URL) else None)

        try:
            page.goto(BASE + path, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(1200)
        except Exception as e:
            results.append((label, "load", False, f"页面加载失败 {str(e)[:120]}"))
            ctx.close()
            continue

        OUT.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(OUT / f"{slug}.png"), full_page=False)

        # 页面必须有可见主内容，不能是白屏
        text_len = len(page.inner_text("body").strip())
        results.append((label, "有内容渲染", text_len > 200, f"{text_len} 字符"))
        results.append((label, "无 JS 异常", not errs, "; ".join(errs[:2]) or "干净"))
        results.append((label, "无 console error", not cons, "; ".join(cons[:2]) or "干净"))
        results.append((label, "无失败请求", not bad_req, "; ".join(sorted(set(bad_req))[:3]) or "干净"))

        # 图表页必须有 canvas 且真的画出了像素。
        # 判据是全图稀疏采样而不是只看左上角——折线图左上角本来就是坐标轴留白，
        # 按 60×60 采样会把好图误判成空白（这个误报真出现过）。
        canvases = page.evaluate("""() => [...document.querySelectorAll('canvas')].map(c => {
            if (!c.width || !c.height) return {w: 0, h: 0, painted: false};
            const g = c.getContext('2d');
            if (!g) return {w: c.width, h: c.height, painted: false};
            const d = g.getImageData(0, 0, c.width, c.height).data;
            const stepX = Math.max(1, Math.floor(c.width / 80));
            const stepY = Math.max(1, Math.floor(c.height / 80));
            let painted = false;
            for (let y = 0; y < c.height && !painted; y += stepY)
                for (let x = 0; x < c.width; x += stepX)
                    if (d[(y * c.width + x) * 4 + 3] !== 0) { painted = true; break; }
            return {w: c.width, h: c.height, painted};
        })""")
        if canvases:
            blank = [c for c in canvases if not c["painted"] or c["w"] < 10]
            results.append((label, "图表已绘制", not blank,
                            f"{len(canvases)} 个 canvas" + (f"，{len(blank)} 个空白" if blank else "")))

        ctx.close()

        # 移动端：不能横向溢出，导航要能打开
        ctx = browser.new_context(viewport={"width": 390, "height": 844},
                                  is_mobile=True, has_touch=True)
        page = ctx.new_page()
        try:
            page.goto(BASE + path, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(900)
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            results.append((label, "390px 无横向溢出", overflow <= 2, f"溢出 {overflow}px"))
            page.screenshot(path=str(OUT / f"{slug}_mobile.png"), full_page=False)
        except Exception as e:
            results.append((label, "390px 加载", False, str(e)[:100]))
        ctx.close()

    browser.close()


def check_interactions(pw, results):
    """真实点击才能发现的问题：数据一变、提示没变；翻页刷新回第一页。

    这些都不是"页面报错"，控制台干干净净，只有点下去才知道不对。
    """
    browser = pw.chromium.launch(channel="msedge")

    # 1) 切数据集后，筛选栏的"数据覆盖"必须跟着换
    page = browser.new_page(viewport={"width": 1560, "height": 1000})
    try:
        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_timeout(1200)
        before = page.inner_text('[data-role="range-hint"]')
        page.select_option("#dsSelect", "ds_2023_all")
        page.wait_for_timeout(2200)
        after = page.inner_text('[data-role="range-hint"]')
        results.append(("交互", "切数据集后覆盖范围更新", before != after and "2023" in after,
                        f"{before} → {after}"))
    except Exception as e:
        results.append(("交互", "切数据集后覆盖范围更新", False, str(e)[:120]))
    page.close()

    # 2) 证据页翻页后刷新，必须停在同一页
    page = browser.new_page(viewport={"width": 1560, "height": 1000})
    try:
        page.goto(BASE + "/evidence", wait_until="networkidle")
        page.wait_for_timeout(1400)
        page.locator("#pager button", has_text="下一页").first.click()
        page.wait_for_timeout(1300)
        url_after = page.url
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1400)
        sub = page.inner_text("#resultSub")
        ok = "offset=20" in url_after and "第 21" in sub
        results.append(("交互", "翻页后刷新保持页码", ok,
                        f"{url_after.split('8010')[-1]} → {sub[:16]}"))
    except Exception as e:
        results.append(("交互", "翻页后刷新保持页码", False, str(e)[:120]))
    page.close()

    # 3) 排序从 URL 恢复时，下拉框要跟着显示（否则数据和控件说两件事）
    page = browser.new_page(viewport={"width": 1560, "height": 1000})
    try:
        page.goto(BASE + "/evidence?order=time_desc", wait_until="networkidle")
        page.wait_for_timeout(1500)
        val = page.input_value("#orderSelect")
        results.append(("交互", "排序从 URL 恢复并回填控件", val == "time_desc", f"控件值 {val}"))
    except Exception as e:
        results.append(("交互", "排序从 URL 恢复并回填控件", False, str(e)[:120]))
    page.close()

    browser.close()


def main():
    results = []
    with sync_playwright() as pw:
        audit(pw, results)
        check_interactions(pw, results)

    bad = [r for r in results if not r[2]]
    for label, name, ok, detail in results:
        print(("OK " if ok else "!! ") + f"[{label}] {name} — {detail}")
    print("=" * 70)
    print(f"共 {len(results)} 项，失败 {len(bad)} 项")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
