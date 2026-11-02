"""最小 MCP 客户端 —— 用来验证 tools/mcp_server.py 真的符合协议，而不是"我觉得符合"。

它做的事和一个真实 MCP 客户端（Claude Desktop 等）完全一样：
    拉起子进程 → initialize → notifications/initialized → tools/list → tools/call → 关闭 stdin

为什么需要它：
    MCP 的价值主张是"换一个客户端也能用"。如果只用本项目的 Agent 测过工具层，
    那证明的只是"工具函数能跑"，证明不了"工具层与界面解耦"。
    这个脚本是解耦的证据：它不认识 Flask、不认识 HTML，只认识 JSON-RPC。

用法：
    .venv/Scripts/python.exe tools/mcp_client.py            # 跑全部校验
    .venv/Scripts/python.exe tools/mcp_client.py -v         # 打印原始报文
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable
SERVER = os.path.join(ROOT, "tools", "mcp_server.py")


class McpClient:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.log_path = tempfile.NamedTemporaryFile(
            prefix="cp_mcp_", suffix=".log", delete=False).name
        self._logf = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [PY, SERVER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._logf,
            cwd=ROOT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self.next_id = 0
        self.transcript = []

    # ---------------- 传输 ----------------
    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.next_id += 1
            msg["id"] = self.next_id
        raw = json.dumps(msg, ensure_ascii=False)
        if self.verbose:
            print(f"  → {raw[:160]}")
        self.proc.stdin.write(raw.encode("utf-8") + b"\n")
        self.proc.stdin.flush()
        if notify:
            return None
        return self._read()

    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("服务端没有响应就关闭了 stdout（协议流中断）")
        text = line.decode("utf-8").strip()
        if self.verbose:
            print(f"  ← {text[:200]}")
        self.transcript.append(text)
        return json.loads(text)

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        self._logf.close()
        return self.proc.returncode

    def stderr_text(self):
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return ""


def run(verbose=False):
    checks = []

    def check(name, fn):
        try:
            detail = fn()
            checks.append((name, True, detail))
            print(f"  [PASS] {name}　{detail}")
        except AssertionError as e:
            checks.append((name, False, str(e)))
            print(f"  [FAIL] {name}　{e}")
        except Exception as e:
            checks.append((name, False, f"{type(e).__name__}: {e}"))
            print(f"  [ERR ] {name}　{type(e).__name__}: {e}")

    c = McpClient(verbose=verbose)
    state = {}

    try:
        print("一、握手（initialize）")

        def t_initialize():
            r = c.send("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "campuspulse-selftest", "version": "1.0"},
            })
            assert "result" in r, f"没有 result：{r}"
            res = r["result"]
            assert res.get("protocolVersion") == "2024-11-05", \
                f"协议版本未协商一致：{res.get('protocolVersion')}"
            info = res.get("serverInfo") or {}
            assert info.get("name") == "campuspulse", f"serverInfo 异常：{info}"
            assert "tools" in (res.get("capabilities") or {}), "未声明 tools 能力"
            assert res.get("instructions"), "缺少 instructions（模型不知道边界）"
            state["init"] = res
            return f"protocol={res['protocolVersion']} server={info['name']} {info.get('version')}"

        check("initialize 版本协商 + 能力声明", t_initialize)

        def t_initialized_notify():
            c.send("notifications/initialized", notify=True)
            # 通知不能有回复；这里用一个 ping 探测流是否仍然健康
            r = c.send("ping", {})
            assert "result" in r, f"通知之后流断了：{r}"
            return "通知无回复，协议流正常"

        check("notifications/initialized 按通知处理（不回复）", t_initialized_notify)

        print("二、工具发现（tools/list）")

        def t_tools_list():
            tools = (c.send("tools/list", {})["result"] or {}).get("tools") or []
            assert len(tools) == 6, f"工具数量应为 6，实际 {len(tools)}"
            names = [t["name"] for t in tools]
            for n in ("get_sentiment_stats", "get_topic_stats", "get_time_trend",
                      "compare_platforms", "search_comments", "retrieve_context"):
                assert n in names, f"缺少工具 {n}"
            for t in tools:
                assert t.get("description"), f"{t['name']} 缺 description"
                sch = t.get("inputSchema") or {}
                assert sch.get("type") == "object", f"{t['name']} inputSchema 不是 object"
                assert "properties" in sch, f"{t['name']} inputSchema 缺 properties"
            state["tools"] = tools
            return f"{len(tools)} 个工具，inputSchema 全部完整"

        check("tools/list 返回 6 个工具且 Schema 完整", t_tools_list)

        print("三、工具执行（tools/call）")

        def t_call_stats():
            r = c.send("tools/call", {
                "name": "get_sentiment_stats",
                "arguments": {"dataset_id": "ds_hist_all"},
            })
            res = r.get("result") or {}
            assert res.get("isError") is False, f"调用返回错误：{res}"
            text = res["content"][0]["text"]
            assert "1,803" in text or "1803" in text, f"结果里没有真实分母：{text[:200]}"
            assert "指标" in text, "结果结构不对（缺少指标段）"
            state["stats_text"] = text
            return f"{len(text)} 字符，含真实分母 1,803"

        check("tools/call 统计工具返回真实数据", t_call_stats)

        def t_call_rag():
            r = c.send("tools/call", {
                "name": "retrieve_context",
                "arguments": {"query": "宿舍住几个人", "top_k": 3},
            })
            res = r.get("result") or {}
            assert res.get("isError") is False, f"调用返回错误：{res}"
            text = res["content"][0]["text"]
            assert "CMT-" in text, f"检索结果没有脱敏引用编号：{text[:200]}"
            assert "宿舍" in text, "检索结果与查询无关"
            return "返回带 CMT 编号的脱敏片段"

        check("tools/call RAG 检索返回可引用片段", t_call_rag)

        def t_call_bad_args():
            # ① 非法枚举：直接拒绝
            r = c.send("tools/call", {
                "name": "get_sentiment_stats",
                "arguments": {"platform": ["微博"]},
            })
            res = r.get("result") or {}
            assert res.get("isError") is True, "非法平台没有被拒绝"
            assert "不支持" in res["content"][0]["text"], \
                f"错误信息不明确：{res['content'][0]['text'][:150]}"

            # ② 超上限：按设计「收敛」而不是「失败」（模型写 9999 不该让整轮崩掉），
            #    但必须真的收敛到上限内，不能悄悄放大查询量
            r2 = c.send("tools/call", {
                "name": "search_comments",
                "arguments": {"dataset_id": "ds_hist_all", "limit": 9999},
            })
            res2 = r2.get("result") or {}
            assert res2.get("isError") is False, f"超限被误判为失败：{res2}"
            text2 = res2["content"][0]["text"]
            # 摘要里写的是 clamp 之后的真实展示条数，用它验证"收敛到上限"而不是"收敛到我的输出截断"
            assert "展示 50 条" in text2, \
                f"limit=9999 未被收敛到上限 50：{text2[:160]}"

            # ③ 类型错误：必须明确报错，不能沉默纠正（说明调用方理解错了接口）
            r3 = c.send("tools/call", {
                "name": "retrieve_context",
                "arguments": {"query": "宿舍", "top_k": "很多"},
            })
            res3 = r3.get("result") or {}
            assert res3.get("isError") is True, "非整数 top_k 没有被拒绝"
            assert "整数" in res3["content"][0]["text"], \
                f"类型错误信息不明确：{res3['content'][0]['text'][:150]}"
            return "枚举拒绝 / limit=9999 收敛至上限 50 / 类型错误明确报错"

        check("非法参数被拦截（枚举拒绝·超限收敛·类型报错）", t_call_bad_args)

        def t_call_unknown_tool():
            r = c.send("tools/call", {
                "name": "drop_table_comments",     # 不存在的工具
                "arguments": {},
            })
            res = r.get("result") or {}
            assert res.get("isError") is True, "不存在的工具居然执行成功了"
            text = res["content"][0]["text"]
            assert "不存在" in text or "只允许" in text, f"错误信息不明确：{text[:150]}"
            return "插件式工具名被白名单拦截"

        check("非白名单工具被拒绝（安全边界）", t_call_unknown_tool)

        print("四、协议健壮性")

        def t_unknown_method():
            r = c.send("resources/list", {})
            assert "error" in r, f"未知方法应返回 error：{r}"
            assert r["error"]["code"] == -32601, f"错误码应为 -32601：{r['error']}"
            return "未知方法返回 -32601 Method not found"

        check("未知方法按 JSON-RPC 规范报错", t_unknown_method)

        def t_ping():
            r = c.send("ping", {})
            assert "result" in r, f"ping 失败：{r}"
            return "存活"

        check("ping 存活检查", t_ping)

        print("五、优雅退出")

        def t_shutdown():
            code = c.close()
            assert code == 0, f"退出码 {code}（stderr 见下）"
            return "stdin 关闭后退出码 0"

        check("stdin 关闭后正常退出", t_shutdown)

    finally:
        try:
            if c.proc.poll() is None:
                c.proc.kill()
        except Exception:
            pass

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print()
    print(f"MCP 端到端：{passed}/{total} 通过")
    if passed != total:
        err = c.stderr_text()
        if err:
            print("--- 服务端 stderr（末尾 40 行）---")
            print("\n".join(err.strip().splitlines()[-40:]))
    return passed == total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true", help="打印原始 JSON-RPC 报文")
    args = ap.parse_args()
    ok = run(verbose=args.verbose)
    sys.exit(0 if ok else 1)
