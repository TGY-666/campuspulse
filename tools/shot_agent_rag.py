# -*- coding: utf-8 -*-
"""对 Agent 页面做一次真实提问并截图（验证 RAG 卡片与引用芯片渲染）。"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("CP_BASE", "http://127.0.0.1:8010")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots", "cp")
QUESTION = sys.argv[1] if len(sys.argv) > 1 else "宿舍住宿方面学生最不满的是什么？给出相关评论依据。"


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        ctx = browser.new_context(viewport={"width": 1560, "height": 1100}, device_scale_factor=1.3)
        page = ctx.new_page()
        logs = []
        page.on("console", lambda m: logs.append(f"[{m.type}] {m.text[:200]}")
                if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: logs.append(f"[pageerror] {str(e)[:300]}"))

        page.goto(BASE + "/agent", wait_until="networkidle")
        page.fill("#qInput", QUESTION)
        page.click("#btnSend")
        page.wait_for_selector("#resultBody .answer-text", timeout=60000)
        page.wait_for_timeout(2500)
        page.screenshot(path=os.path.join(OUT, "06_agent_rag_top.png"), clip=None)

        # RAG 卡片单独截图
        card = page.query_selector("text=检索片段（RAG）")
        if card:
            el = card.evaluate_handle("e => e.closest('.card')")
            el.as_element().screenshot(path=os.path.join(OUT, "07_agent_rag_card.png"))

        text = page.inner_text("#resultBody")
        print("ANSWER >>", text.split("关键指标")[0][:400].replace("\n", " "))
        print("HAS_RAG_CARD >>", "检索片段（RAG）" in text)
        print("CITED_CHIPS >>", page.eval_on_selector_all(".ref-chip", "els => els.map(e => e.textContent)"))
        badges = page.eval_on_selector_all("#resultBody .answer-meta .badge", "els => els.map(e => e.textContent.trim())")
        print("BADGES >>", badges)
        print("CONSOLE >>", logs[:10])
        browser.close()


if __name__ == "__main__":
    main()
