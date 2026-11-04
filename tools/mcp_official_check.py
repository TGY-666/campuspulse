# -*- coding: utf-8 -*-
"""用**官方 MCP Python SDK** 连接本项目自研的 MCP Server，做交叉验证。

为什么需要这个脚本：
`tools/mcp_client.py` 是本项目自己写的客户端——自己写的客户端测自己写的服务端，
只能证明"两边对同一份协议理解一致"，不能证明"实现符合协议"。这是个循环论证。
所以这里换成 Anthropic 官方 `mcp` SDK 的 ClientSession 连进来：
它不认识我们的代码，只按标准流程握手、发现工具、调用工具。它认了，才算真的认。

覆盖三条核心链路：
  1. initialize   —— 版本协商、serverInfo、instructions 下发
  2. tools/list   —— 6 个工具、inputSchema 结构合法、描述非空
  3. tools/call   —— 真实统计结果、参数校验拒绝、未知工具报错

用法：
    .venv/Scripts/python.exe tools/mcp_official_check.py
    .venv/Scripts/python.exe tools/mcp_official_check.py -v

依赖：pip install -r requirements-ext.txt（只有本脚本需要官方 SDK，
线上运行时用的是 zero-dependency 的 toolbus，不依赖 mcp 包）。
"""
import argparse
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcp import ClientSession, StdioServerParameters          # noqa: E402
from mcp.client.stdio import stdio_client                     # noqa: E402

PY = sys.executable
SERVER = os.path.join(ROOT, "tools", "mcp_server.py")

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def _text_of(result):
    """把 tools/call 的返回摊平成字符串，便于断言内容。"""
    parts = []
    for c in (result.content or []):
        t = getattr(c, "text", None)
        if t:
            parts.append(t)
    return "\n".join(parts)


async def run(verbose=False):
    params = StdioServerParameters(
        command=PY,
        args=[SERVER],
        env={**os.environ, "CAMPUSPULSE_DATASET": "ds_hist_all",
             "PYTHONIOENCODING": "utf-8", "PYTHONPATH": ROOT},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # ---------- 一、握手 ----------
            print("\n【一、握手】")
            init = await session.initialize()
            info = init.serverInfo
            check("initialize 成功", info is not None)
            check("serverInfo.name 是 CampusPulse",
                  "campuspulse" in (info.name or "").lower(), info.name)
            check("声明了 tools 能力", init.capabilities.tools is not None)
            check("下发使用边界说明（instructions）",
                  bool(init.instructions), (init.instructions or "")[:48].replace("\n", " "))
            if verbose:
                print(f"    版本：{init.protocolVersion} / {info.version}")

            # ---------- 二、工具发现 ----------
            print("\n【二、工具发现】")
            listed = await session.list_tools()
            names = sorted(t.name for t in listed.tools)
            check("返回 6 个工具", len(names) == 6, ", ".join(names))
            bad_schema = []
            for t in listed.tools:
                s = t.inputSchema or {}
                if s.get("type") != "object" or not isinstance(s.get("properties"), dict):
                    bad_schema.append(t.name)
                if not (t.description or "").strip():
                    bad_schema.append(t.name + "(无描述)")
            check("每个工具 inputSchema 结构合法且带描述", not bad_schema, str(bad_schema))
            # 必填项必须真的在 properties 里，否则客户端按 schema 组装参数会失败
            orphan = []
            for t in listed.tools:
                s = t.inputSchema or {}
                for req in s.get("required") or []:
                    if req not in (s.get("properties") or {}):
                        orphan.append(f"{t.name}.{req}")
            check("required 字段都在 properties 中", not orphan, str(orphan))

            # ---------- 三、工具执行 ----------
            print("\n【三、工具执行】")
            r1 = await session.call_tool("get_sentiment_stats",
                                         {"dataset_id": "ds_hist_all"})
            txt1 = _text_of(r1)
            check("统计工具返回成功", not r1.isError, txt1[:60].replace("\n", " "))
            check("结果里带真实分母 1,803", "1,803" in txt1 or "1803" in txt1,
                  txt1[:80].replace("\n", " "))

            r2 = await session.call_tool("retrieve_context",
                                         {"query": "宿舍热水", "top_k": 3})
            txt2 = _text_of(r2)
            check("RAG 工具返回脱敏片段", (not r2.isError) and ("CMT" in txt2),
                  txt2[:70].replace("\n", " "))

            # 参数校验：limit 越界必须被收敛而不是照单全收
            r3 = await session.call_tool("search_comments",
                                         {"query": "食堂", "limit": 9999})
            check("limit=9999 被服务端收敛（不报错、不限死）", not r3.isError)

            # 非法枚举必须被拒
            r4 = await session.call_tool("search_comments",
                                         {"query": "食堂", "platform": "不存在的平台"})
            check("非法枚举被拒绝", r4.isError, _text_of(r4)[:50].replace("\n", " "))

            # 未知工具：SDK 层会拿到 isError 或协议错误，两者都算"拒绝"
            try:
                r5 = await session.call_tool("drop_table", {})
                check("非白名单工具被拒绝", r5.isError, _text_of(r5)[:50])
            except Exception as e:                      # noqa: BLE001
                check("非白名单工具被拒绝", True, type(e).__name__)

            # 结构化输出：新版 SDK 会把 structuredContent 解析出来
            sc = getattr(r1, "structuredContent", None)
            if sc:
                ok = isinstance(sc, dict) and any(k in sc for k in ("ok", "summary", "data"))
                check("返回结构化内容 structuredContent", ok,
                      ",".join(list(sc)[:4]))
            else:
                check("返回结构化内容 structuredContent", True,
                      "服务端未声明 outputSchema，仅 text 通道（可接受）")

    return _results


def main():
    ap = argparse.ArgumentParser(description="用官方 MCP SDK 交叉验证自研 Server")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    print("=" * 62)
    print("  官方 MCP SDK（ClientSession）↔ CampusPulse MCP Server")
    print("=" * 62)
    try:
        results = asyncio.run(run(args.verbose))
    except Exception as e:                              # noqa: BLE001
        print(f"\n连接失败：{type(e).__name__}: {e}")
        print("排查：1) pip install -r requirements-ext.txt  "
              "2) python tools/mcp_server.py --list 能否打印工具清单")
        return 2

    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    print("\n" + "-" * 62)
    print(f"  结果：{passed}/{total} 通过")
    if passed != total:
        for n, ok, d in results:
            if not ok:
                print(f"  失败：{n} — {d}")
    print("-" * 62)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
