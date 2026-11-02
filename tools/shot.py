# -*- coding: utf-8 -*-
"""页面截图与前端错误捕获（开发验收用）。

用法：
    .venv/Scripts/python.exe tools/shot.py                 # 全部页面
    .venv/Scripts/python.exe tools/shot.py /agent          # 指定页面
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("CP_BASE", "http://127.0.0.1:8010")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots", "cp")

PAGES = [
    ("/", "01_dashboard"),
    ("/agent", "02_agent"),
    ("/insights", "03_insights"),
    ("/evidence", "04_evidence"),
    ("/data", "05_data"),
    ("/tickets", "06_tickets"),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    only = sys.argv[1] if len(sys.argv) > 1 else None
    # CP_AUTH=1 之后所有页面都要登录：给用户名密码就用接口登录取 Cookie，
    # 不给就照旧截（空登录态下得到的是登录页，用来做对照也有用）。
    user = os.environ.get("CP_USER") or ""
    pwd = os.environ.get("CP_PASS") or ""
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        ctx = browser.new_context(viewport={"width": 1560, "height": 1000}, device_scale_factor=1.4)
        if user and pwd:
            r = ctx.request.post(BASE + "/api/auth/login",
                                 data={"username": user, "password": pwd})
            print(("登录成功：" if r.ok else f"登录失败（{r.status}）：") + user)
        page = ctx.new_page()
        logs = []
        page.on("console", lambda m: logs.append(f"[{m.type}] {m.text[:200]}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: logs.append(f"[pageerror] {str(e)[:300]}"))

        pages = PAGES
        if only and "?" in only:
            # 允许直接指定带参数的地址：/tickets?dataset_id=ds_2023_all
            # 有些页面（工单建议）的内容完全取决于参数，只截默认态看不出区别。
            head = only.split("?")[0].strip("/").replace("/", "_") or "root"
            pages = [(only, head + "_query")]
        for path, name in pages:
            if only and "?" not in only and only not in path:
                continue
            logs.clear()
            page.goto(BASE + path, wait_until="networkidle")
            page.wait_for_timeout(2600)
            file = os.path.join(OUT, name + ".png")
            page.screenshot(path=file, full_page=True)
            print(f"OK  {path} -> {file}")
            for line in logs:
                print("    ", line)
        browser.close()


if __name__ == "__main__":
    main()
