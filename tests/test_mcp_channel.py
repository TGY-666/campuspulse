# -*- coding: utf-8 -*-
"""P1-2 MCP 双通道验收：协议可用 + 两条通道结果逐字段等价 + 失败自动降级。

运行：.venv/Scripts/python.exe tests/test_mcp_channel.py

为什么一定要比"等价"：
    "我们也支持 MCP" 最容易变成一句空话——起一个 server，主链路照旧直调。
    这个脚本证明的是：同一个工具、同一套参数，走 MCP 协议和走进程内直调，
    返回的数字、证据、口径文案**完全一致**。只要有一条不一致，就说明
    工具层出现了第二份实现，那"解耦"就无从谈起。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("CP_TOOL_CHANNEL", "mcp")

from campuspulse import config, db, toolbus, tools  # noqa: E402

PASSED, FAILED = [], []
DS = config.DEFAULT_DATASET_ID

# 覆盖 6 个工具各一次的最小调用集
CASES = [
    ("get_sentiment_stats", {"dataset_id": DS}),
    ("get_topic_stats", {"dataset_id": DS, "top_n": 5}),
    ("get_time_trend", {"dataset_id": DS, "granularity": "month"}),
    ("compare_platforms", {"dataset_id": DS}),
    ("search_comments", {"dataset_id": DS, "keywords": "食堂", "limit": 3}),
    ("retrieve_context", {"dataset_id": DS, "query": "宿舍热水", "top_k": 3}),
]


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail else ""))


def strip(result):
    """去掉通道自身的元字段后做严格比对。"""
    drop = {"channel", "channel_elapsed_ms", "channel_note", "elapsed_ms"}
    return {k: v for k, v in result.items() if k not in drop}


def norm_summary(text):
    """summary 里带着"耗时 xx ms"，两次调用的毫秒数本来就不会一样。
    这里只抹掉这一个不可比的部分，其余文字照常逐字比对——
    如果把整段 summary 都放过，就验证不出"两条通道说的是同一件事"。"""
    return re.sub(r"耗时\s*\d+\s*ms", "耗时 ?ms", str(text or ""))


def diff_keys(a, b):
    ka, kb = set(a.keys()), set(b.keys())
    return f"仅本地有 {sorted(ka - kb)}；仅 MCP 有 {sorted(kb - ka)}"


def main():
    print("=" * 68)
    print("P1-2 MCP 双通道验收")
    print("=" * 68)
    db.init_db()

    print("\n【通道与协议】")
    check("默认按 CP_TOOL_CHANNEL=mcp 启动", toolbus.channel() == "mcp", toolbus.channel())
    cat = toolbus.catalog()
    check("tools/list 返回 6 个工具", len(cat) == 6, str(len(cat)))
    check("工具清单确实来自 MCP", all(t.get("source") == "mcp" for t in cat))
    check("工具名与本地白名单一致",
          [t["name"] for t in cat] == tools.TOOL_NAMES, str([t["name"] for t in cat]))
    decl = toolbus.openai_tools()
    check("模型看到的工具声明由 inputSchema 转换而来",
          len(decl) == 6 and all("parameters" in d["function"] for d in decl))

    print("\n【两条通道结果等价】")
    for name, args in CASES:
        local = tools.execute(name, dict(args), {"dataset_id": DS})
        remote = toolbus.execute(name, dict(args), {"dataset_id": DS})
        check(f"{name}：走的是 MCP 通道", remote.get("channel") == "mcp",
              str(remote.get("channel")))
        a, b = strip(local), strip(remote)
        same_ok = a.get("ok") == b.get("ok")
        same_summary = norm_summary(a.get("summary")) == norm_summary(b.get("summary"))
        same_metrics = a.get("metrics") == b.get("metrics")
        same_evidence = a.get("evidence") == b.get("evidence")
        same_filters = a.get("filters_text") == b.get("filters_text")
        check(f"{name}：summary 一致", same_ok and same_summary,
              "" if same_summary else f"{a.get('summary')!r} != {b.get('summary')!r}"[:160])
        check(f"{name}：metrics / evidence / 口径一致",
              same_metrics and same_evidence and same_filters,
              "" if (same_metrics and same_evidence and same_filters) else diff_keys(a, b))

    print("\n【参数校验在两条通道上同样生效】")
    for ch, label in (("local", "本地"), ("mcp", "MCP")):
        toolbus.set_channel(ch)
        bad = toolbus.execute("search_comments", {"limit": "不是数字"}, {"dataset_id": DS})
        check(f"{label}通道：非法参数被拒绝", bad.get("ok") is False, bad.get("reason"))
        unknown = toolbus.execute("drop_table", {}, {"dataset_id": DS})
        check(f"{label}通道：白名单外的工具名被拒绝", unknown.get("ok") is False,
              unknown.get("reason"))

    print("\n【失败自动降级】")
    toolbus.set_channel("mcp")
    toolbus.shutdown()
    # 把服务端路径指向不存在的文件，模拟"MCP 环境坏了"
    real = toolbus._SERVER_PATH
    toolbus._SERVER_PATH = os.path.join(ROOT, "tools", "_no_such_server.py")
    try:
        r = toolbus.execute("get_sentiment_stats", {"dataset_id": DS}, {"dataset_id": DS})
        check("MCP 不可用时降级为本地而不是崩掉",
              r.get("ok") and r.get("channel") == "local", str(r.get("channel")))
        check("降级事实被如实记录在结果里", bool(r.get("channel_note")),
              str(r.get("channel_note"))[:80])
        check("降级次数进了健康指标", toolbus.stats()["fallbacks"] >= 1,
              str(toolbus.stats()["fallbacks"]))
    finally:
        toolbus._SERVER_PATH = real
        toolbus.shutdown()

    print("\n【恢复后仍可用】")
    r = toolbus.execute("get_sentiment_stats", {"dataset_id": DS}, {"dataset_id": DS})
    check("通道自愈：重新拉起子进程后回到 MCP", r.get("channel") == "mcp", str(r.get("channel")))

    print("\n" + "=" * 68)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print("  · " + f)
    print("=" * 68)
    toolbus.shutdown()
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
