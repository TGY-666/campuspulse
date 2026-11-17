# -*- coding: utf-8 -*-
"""实时采集诊断：逐环节告诉你卡在哪，而不是只看到"翻了很多页 +0 条"。

用法：
    .venv/Scripts/python.exe tools/diagnose_crawl.py --platform 小红书 --keyword 广东东软学院
    .venv/Scripts/python.exe tools/diagnose_crawl.py --platform 抖音 --keyword 东软 --headful

做四件事，每步都打印实际值：
    1) 环境与登录态（浏览器路径 / cookie 字段 / 关键 session cookie 在不在）
    2) 搜索页能不能打开、抓到多少条详情链接、链接里有没有 xsec_token
    3) 第一条详情页打开后是什么（正常 / 无法浏览 / 要求登录）
    4) 评论接口命中几次、解析出多少条

只读不写：不会保存任何 cookie、不改数据库、不发起采集任务。
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from playwright.sync_api import sync_playwright  # noqa: E402

from campuspulse import config, crawl  # noqa: E402

URL = {
    "抖音": "https://www.douyin.com/search/{kw}?type=video",
    "小红书": "https://www.xiaohongshu.com/search_result?keyword={kw}&source=web_search_result_notes",
}
PATTERN = {"抖音": crawl._VIDEO_RE, "小红书": crawl._NOTE_RE}
API = {"抖音": "/aweme/v1/web/comment/list/", "小红书": "/api/sns/web/v2/comment/page"}


def head(t):
    print(f"\n{'=' * 62}\n{t}\n{'=' * 62}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="小红书", choices=["抖音", "小红书"])
    ap.add_argument("--keyword", default="广东东软学院")
    ap.add_argument("--headful", action="store_true", help="显示浏览器窗口（看清页面长什么样）")
    args = ap.parse_args()
    plat = args.platform

    head("1. 环境与登录态")
    exe = crawl._browser_path()
    print(f"  浏览器：{exe or '找不到（设置 CP_CRAWL_CHROME）'}")
    print(f"  playwright：{'可用' if crawl._playwright_ok() else '未安装'}")
    item = crawl._load_auth().get(plat) or {}
    cookie = item.get("cookie") or ""
    names = [c["name"] for c in crawl.parse_cookies(cookie, plat)]
    key = "sessionid" if plat == "抖音" else "web_session"
    print(f"  登录态：{'已配置' if cookie else '未配置'}（{len(names)} 个字段）")
    print(f"  关键 cookie `{key}`：{'在' if key in names else '不在 ← 未登录或 cookie 不完整'}")
    print(f"  来源：{item.get('source') or '—'} · 保存于 {item.get('saved_at') or '—'}")
    if not cookie:
        print("\n  结论：没有登录态，先去采集页点「打开登录窗口」扫码。")
        return 1

    hits = {"api": 0, "rows": 0}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful, executable_path=exe,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=crawl.UA, locale="zh-CN",
                                  viewport={"width": 1366, "height": 900},
                                  timezone_id="Asia/Shanghai")
        raw = [c for c in (item.get("raw") or []) if c.get("name") and c.get("domain")]
        ctx.add_cookies(raw or crawl.parse_cookies(cookie, plat))
        page = ctx.new_page()

        def on_resp(resp):
            if API[plat] not in (resp.url or ""):
                return
            hits["api"] += 1
            try:
                hits["rows"] += len(crawl._parse_comments(plat, resp.json()))
            except Exception:
                pass

        page.on("response", on_resp)

        head("2. 搜索页")
        url = URL[plat].format(kw=args.keyword)
        print(f"  打开：{url}")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        print(f"  实际 URL：{page.url[:110]}")
        try:
            print(f"  标题：{page.title()[:60]}")
        except Exception:
            pass
        wall = crawl._login_wall(page)
        print(f"  登录墙：{wall or '否'}")
        links = crawl._gather_links(page, {"stop": False}, PATTERN[plat])
        print(f"  详情链接：{len(links)} 个")
        for u in links[:3]:
            print(f"    · {u[:120]}")
        if plat == "小红书":
            with_token = sum(1 for u in links if "xsec_token=" in u)
            tip = "  ← 缺 token 会让详情页变成「暂时无法浏览」" if links and not with_token else ""
            print(f"  带 xsec_token 的链接：{with_token}/{len(links)}{tip}")

        if not links:
            print("\n  结论：搜索页没有拿到任何详情链接。可能是登录失效、被风控，"
                  "或搜索结果的 DOM 结构变了（卡片不是 <a>）。")
            browser.close()
            return 1

        head("3. 打开第一条详情页")
        page.goto(links[0], wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        print(f"  URL：{page.url[:110]}")
        missing = crawl._page_missing(page)
        print(f"  页面状态：{'打不开 → ' + missing if missing else '正常'}")
        body = ""
        try:
            body = re.sub(r"\s+", " ", page.inner_text("body"))[:160]
        except Exception:
            pass
        print(f"  正文开头：{body}")

        head("4. 评论区")
        for i in range(5):
            crawl._scroll(page, 1600)
            page.wait_for_timeout(1400)
        print(f"  评论接口命中：{hits['api']} 次")
        print(f"  解析出评论：{hits['rows']} 条")
        browser.close()

    head("结论")
    if hits["rows"]:
        print("  这条链路是通的：搜索 → 详情页 → 评论接口 都能拿到数据。")
        print("  如果采集任务里还是 0 条，问题多半在时间段过滤或关键词，而不是登录。")
        return 0
    if hits["api"] == 0:
        print("  评论接口一次都没触发。常见原因：")
        print("    ① 详情页没真正打开（缺 xsec_token / 笔记不可见）—— 看第 3 步的页面状态；")
        print("    ② 页面改版后评论要先点开（找不到评论区入口）；")
        print("    ③ 账号被限流，页面只给内容不给评论。")
        return 1
    print("  接口有命中但解析不到评论：响应结构变了（字段名不对），需要按实际 JSON 调整解析。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
