# -*- coding: utf-8 -*-
"""历史口径复现：用「原系统规则」重跑 25,699 条原始数据，验证 1,823 条的来源。

这是文档 15.2「修正或说明 25,699 / 1,823 / 99.6% 的统计口径关系」的直接证据：
  - 25,699 = 原始记录数（抖音 17,157 + 小红书 8,542）
  - 1,823  = 用原规则清洗后的有效数（抖音 909 + 小红书 914）
  - 真实有效率 = 1,823 ÷ 25,699 = 7.09%，不是 99.6%

99.6% 与本批次无法对应：本批次任何一步都不会得到 99.6% 这个数。最接近的可能是
"时间解析成功率 100%"，或早年某一小批次的 1,823/1,830。系统已改为实时计算并展示分子分母。

运行：.venv/Scripts/python.exe tests/legacy_check.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_processor import (  # noqa: E402
    DIMENSION_KEYWORDS, clean_text, is_valid_comment, timestamp_to_datetime,
)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [
    ("抖音", os.path.join(BASE, "comparison_data", "first", "douyin_comments.json")),
    ("小红书", os.path.join(BASE, "comparison_data", "first", "xhs_comments.json")),
]


def main():
    rows = []
    for platform, path in FILES:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        for r in data:
            r["_platform"] = platform
            rows.append(r)
        print(f"读取 {platform}：{len(data)} 条")

    raw = len(rows)
    print(f"原始记录合计：{raw} 条")

    # 1) 去重（原规则：有 comment_id 按 id，否则按 content）
    seen, dedup = set(), []
    for r in rows:
        key = r.get("comment_id") or str(r.get("content") or "")
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    print(f"去重后：{len(dedup)} 条（移除 {raw - len(dedup)}）")

    # 2) 空内容
    non_empty = [r for r in dedup if str(r.get("content") or "").strip()]
    print(f"去除空评论：{len(non_empty)} 条（移除 {len(dedup) - len(non_empty)}）")

    # 3) 时间解析
    timed = [r for r in non_empty if str(timestamp_to_datetime(r.get("create_time"))) != "NaT"]
    print(f"时间解析成功：{len(timed)} 条（移除 {len(non_empty) - len(timed)}）")

    # 4) 无效短评（原规则：clean 后长度 < 5 或仅含符号）
    valid = [r for r in timed if is_valid_comment(r.get("content"))]
    print(f"通过无效短评过滤：{len(valid)} 条（移除 {len(timed) - len(valid)}）")

    # 5) 命中至少一个校园维度（原维度词典，未修订）
    related = []
    for r in valid:
        text = clean_text(r.get("content"))
        if any(any(k in text for k in kws) for kws in DIMENSION_KEYWORDS.values()):
            related.append(r)
    print(f"命中校园维度：{len(related)} 条（移除 {len(valid) - len(related)}）")

    by_plat = {}
    for r in related:
        by_plat[r["_platform"]] = by_plat.get(r["_platform"], 0) + 1
    rate = round(len(related) / raw * 100, 2)

    print("-" * 52)
    print(f"有效评论：{len(related)} 条  平台分布：{by_plat}")
    print(f"真实有效率：{len(related)} ÷ {raw} = {rate}%")
    print("-" * 52)
    ok = len(related) == 1823 and by_plat.get("抖音") == 909 and by_plat.get("小红书") == 914
    print("与作品简介中的 1,823（抖音 909 / 小红书 914）一致：" + ("是 ✅" if ok else "否 ❌"))
    print("结论：25,699 与 1,823 属于同一批次的原始量与有效量；99.6% 无法由本批次推出，"
          "应更正为 7.09%。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
