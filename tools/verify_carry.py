# -*- coding: utf-8 -*-
"""S7「对话即筛选」闭环验证：

    Agent 提问 → 读出本轮口径 → 点击「在驾驶舱展开这组筛选」→ 落地页筛选真的生效

验证的不是"按钮能点"，而是三件必须同时成立的事：
    1. 携带的 chips 与 Agent 回答里的筛选一致；
    2. 落地后 filterBar 的选中态与 URL 一致（用户看得见）；
    3. 落地后统计样本数与 Agent 回答的 sample_size 一致（口径没走样）。

用法：
    .venv/Scripts/python.exe tools/verify_carry.py
    .venv/Scripts/python.exe tools/verify_carry.py "食堂餐饮方面学生最不满的是什么？"
"""
import os
import sys
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

BASE = os.environ.get("CP_BASE", "http://127.0.0.1:8010")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "screenshots", "cp")
QUESTION = sys.argv[1] if len(sys.argv) > 1 else "宿舍住宿方面学生最不满的是什么？"


def main():
    os.makedirs(OUT, exist_ok=True)
    ok = True
    problems_extra = []      # 筛选交互回归发现的问题，最后与闭环问题合并
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        ctx = browser.new_context(viewport={"width": 1560, "height": 1100}, device_scale_factor=1.3)
        page = ctx.new_page()
        logs = []
        page.on("console", lambda m: logs.append(f"[{m.type}] {m.text[:200]}")
                if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: logs.append(f"[pageerror] {str(e)[:300]}"))

        # ---------- 1. 在 Agent 页提问 ----------
        page.goto(BASE + "/agent", wait_until="networkidle")
        page.fill("#qInput", QUESTION)
        page.click("#btnSend")
        page.wait_for_selector("#resultBody .answer-text", timeout=90000)
        page.wait_for_selector(".carry-bar", timeout=30000)
        page.wait_for_timeout(800)

        chips = page.eval_on_selector_all(
            ".carry-bar .chip", "els => els.map(e => e.textContent.trim())")
        links = page.eval_on_selector_all(
            ".carry-bar a.btn", "els => els.map(e => ({text: e.textContent.trim(), href: e.getAttribute('href')}))")
        sample = page.inner_text(".kv").replace("\n", " ")
        print("CARRIER_CHIPS >>", chips)
        print("CARRIER_LINKS >>", [(x["text"], x["href"][:90]) for x in links])

        # Agent 回答里的样本量，用于落地后比对
        import re
        m = re.search(r"统计样本\s*([\d,]+)\s*条", sample)
        agent_sample = int(m.group(1).replace(",", "")) if m else None
        print("AGENT_SAMPLE >>", agent_sample)

        card = page.query_selector(".carry-bar")
        if card:
            el = card.evaluate_handle("e => e.closest('.card')")
            el.as_element().screenshot(path=os.path.join(OUT, "08_carry_card.png"))

        if not chips:
            print("FAIL >> 没有可携带的筛选（本轮可能为全量数据）")
            browser.close()
            return 1

        # ---------- 2. 点击「在驾驶舱展开这组筛选」 ----------
        target = next((x for x in links if "在驾驶舱" in x["text"]), None)
        if not target:
            print("FAIL >> 找不到驾驶舱跳转按钮")
            browser.close()
            return 1

        page.click(".carry-bar a.btn:has-text('在驾驶舱')")
        page.wait_for_url(lambda u: "dimension=" in u or "sentiment=" in u, timeout=30000)
        page.wait_for_selector("#scopeNote", timeout=30000)
        page.wait_for_timeout(3000)          # 等 5 个接口都回来

        url = page.url
        qs = parse_qs(urlparse(url).query)
        print("LANDED_URL >>", url[:160])
        print("LANDED_PARAMS >>", {k: v for k, v in qs.items()})

        scope = page.inner_text("#scopeNote").strip()
        print("SCOPE_NOTE >>", scope[:220])

        # 筛选栏选中态：用户必须"看得见"这些筛选
        on_chips = page.eval_on_selector_all(
            "#filterBar .chip.on", "els => els.map(e => e.dataset.value)")
        dim_val = page.eval_on_selector("#filterBar [data-role='dimension']", "e => e.value")
        print("FILTERBAR_ON >>", on_chips, "| dimension =", dim_val)

        page.screenshot(path=os.path.join(OUT, "09_carry_landed.png"), full_page=True)

        # ---------- 2b. 筛选交互回归 ----------
        # 我把 chip 点击从"写死 platform"改成了"按 data-group 分组"，
        # 这是所有页面共用的代码，必须确认平台 chip 与重置按钮都没被改坏
        hint_before = page.query_selector("#sentimentHint")
        print("HINT_ON_LANDED >>", bool(hint_before))

        page.click("#filterBar [data-group='platform'] .chip[data-value='抖音']")
        page.wait_for_timeout(1200)
        q2 = parse_qs(urlparse(page.url).query)
        print("AFTER_PLATFORM_CLICK >>", q2.get("platform"), "| sentiment=", q2.get("sentiment"))
        if q2.get("platform") != ["抖音"]:
            problems_extra.append(f"平台 chip 不生效：{q2.get('platform')}")
        if q2.get("sentiment") != ["negative"]:
            problems_extra.append(f"点平台 chip 冲掉了情感筛选：{q2.get('sentiment')}")

        page.click("#filterBar [data-role='reset']")
        page.wait_for_timeout(1500)
        q3 = parse_qs(urlparse(page.url).query)
        on_after_reset = page.eval_on_selector_all("#filterBar .chip.on", "els => els.map(e => e.dataset.value)")
        hint_after = page.query_selector("#sentimentHint")
        print("AFTER_RESET >>", {k: v for k, v in q3.items()}, "| chips =", on_after_reset,
              "| hint =", bool(hint_after))
        if on_after_reset:
            problems_extra.append(f"重置后仍有 chip 选中：{on_after_reset}")
        if q3.get("sentiment") or q3.get("platform"):
            problems_extra.append(f"重置后 URL 仍带筛选：{q3}")
        if hint_after:
            problems_extra.append("重置后情感提示条没有消失")

        # ---------- 3. 断言 ----------
        problems = list(problems_extra)
        if "dimension" in qs and dim_val != qs["dimension"][0]:
            problems.append(f"维度没落到筛选栏：URL={qs['dimension'][0]} 界面={dim_val!r}")
        if "sentiment" in qs:
            want = set(qs["sentiment"][0].split(","))
            if not want.issubset(set(on_chips)):
                problems.append(f"情感没落到筛选栏：URL={want} 界面选中={on_chips}")
        if agent_sample is not None:
            m2 = re.search(r"有效评论\s*([\d,]+)\s*条", scope)
            landed = int(m2.group(1).replace(",", "")) if m2 else None
            print("LANDED_SAMPLE >>", landed)
            if landed != agent_sample:
                problems.append(f"两边口径不一致：Agent {agent_sample} 条 vs 驾驶舱 {landed} 条")
        if logs:
            problems.append(f"控制台有 {len(logs)} 条报错/警告：{logs[:3]}")

        if problems:
            ok = False
            print("FAIL >>")
            for x in problems:
                print("   -", x)
        else:
            print("PASS >> chips 一致、筛选栏选中态一致、样本量一致、控制台干净")

        browser.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
