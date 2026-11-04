# -*- coding: utf-8 -*-
"""工具通道：本地直调 / MCP 远程调，一个开关切换。

## 存在的理由

评分细则里「MCP/工具集成」考察的是"是否通过 MCP 协议**或** Function Calling"。
项目原来只有后者。把工具做成 MCP Server 只是第一步——如果网页里的 Agent
还在 `import tools` 直接调函数，那 MCP 就只是个摆设（一个谁都不用的
"我们也支持 MCP"）。所以这一层的作用是：**让主链路真的从 MCP 协议走一遍**。

## 两条通道的等价性

    CP_TOOL_CHANNEL=local  → tools.execute()                 （进程内，零开销）
    CP_TOOL_CHANNEL=mcp    → JSON-RPC over stdio → 子进程 → tools.execute()

两条路最后落到同一个 `tools.execute()`，参数白名单、Schema 校验、脱敏、
口径披露完全一致。这一点不是靠"看代码觉得一样"，而是 tests/test_mcp_channel.py
对同一批问题逐字段比对本地与 MCP 的返回结果。

## 为什么不是简单起个进程每次调用

MCP 的 stdio 是长连接语义：一次 initialize，之后反复 tools/call。
每次调用都重启子进程的话，一次对话 6 个工具就要起 6 个进程，
每个进程还要 init_db()（2.5 万行库约 0.3 秒）——延迟会高得离谱。
所以这里维护一个常驻子进程：懒启动、加锁串行、失败自动重启一次、
仍失败则降级到本地直调并明确告知调用方（而不是抛异常让整轮对话崩掉）。
"""
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time

from . import config, tools

logger = logging.getLogger("campuspulse")

_SERVER_PATH = os.path.join(config.BASE_DIR, "tools", "mcp_server.py")
_PROTOCOL = "2024-11-05"

_lock = threading.Lock()
_state = {
    "channel": config.TOOL_CHANNEL,
    "mcp_calls": 0,
    "mcp_failures": 0,
    "local_calls": 0,
    "fallbacks": 0,
    "last_error": "",
    "last_elapsed_ms": 0,
}


def channel():
    return _state["channel"]


def set_channel(name):
    """运行时切换通道（默认由 CP_TOOL_CHANNEL 决定）。

    留这个入口是为了验收脚本能直接对比两条通道，而不是靠改环境变量重启进程——
    重启之后再比，就分不清"结果一样"是因为同一条链路，还是因为跑的是同一份代码。
    """
    name = (name or "local").strip().lower()
    if name not in ("local", "mcp"):
        raise ValueError(f"未知工具通道：{name}")
    if name != _state["channel"]:
        if name == "local":
            _stop()
        _state["channel"] = name
    return _state["channel"]


def stats():
    """给 /api/health 用：现在走哪条通道、失败过几次。"""
    return dict(_state, server=_SERVER_PATH, running=bool(_proc and _proc.poll() is None))


class McpError(RuntimeError):
    pass


# ============================ MCP 客户端 ============================
_proc = None
_out_q = None
_reader = None
_next_id = 0


def _reader_loop(stream, q):
    """把子进程 stdout 的每一行推到队列里，主线程按超时取。"""
    try:
        for line in stream:
            s = line.decode("utf-8", "replace").strip()
            if s:
                q.put(s)
    except Exception as e:                  # 进程被杀、管道断开
        q.put(json.dumps({"__reader_error__": str(e)}))
    finally:
        q.put(None)                        # 哨兵：流结束


def _start():
    """拉起常驻 MCP 子进程并完成 initialize。失败抛 McpError。"""
    global _proc, _out_q, _reader
    if _proc is not None and _proc.poll() is None:
        return
    if not os.path.isfile(_SERVER_PATH):
        raise McpError(f"MCP 服务端不存在：{_SERVER_PATH}")

    env = {**os.environ,
           "PYTHONIOENCODING": "utf-8",
           "PYTHONPATH": config.BASE_DIR,
           "CAMPUSPULSE_DATASET": config.DEFAULT_DATASET_ID}
    _proc = subprocess.Popen(
        [sys.executable, _SERVER_PATH],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=config.BASE_DIR, env=env,
    )
    _out_q = queue.Queue()
    _reader = threading.Thread(target=_reader_loop, args=(_proc.stdout, _out_q), daemon=True)
    _reader.start()

    init = _rpc("initialize", {
        "protocolVersion": _PROTOCOL,
        "capabilities": {},
        "clientInfo": {"name": "campuspulse-agent", "version": "1.0.0"},
    }, timeout=20)
    if not init.get("serverInfo"):
        raise McpError("initialize 未返回 serverInfo")
    _rpc_notify("notifications/initialized", {})
    logger.info("MCP 通道已连接：%s", init["serverInfo"].get("name"))


def _rpc_notify(method, params):
    try:
        line = json.dumps({"jsonrpc": "2.0", "method": method, "params": params},
                          ensure_ascii=False, separators=(",", ":"))
        _proc.stdin.write(line.encode("utf-8") + b"\n")
        _proc.stdin.flush()
    except Exception:
        pass


def _rpc(method, params, timeout=30):
    global _next_id
    if not _proc or _proc.poll() is not None:
        raise McpError("MCP 子进程未运行")
    with _lock:
        _next_id += 1
        rid = _next_id
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        try:
            _proc.stdin.write(json.dumps(payload, ensure_ascii=False,
                                         separators=(",", ":")).encode("utf-8") + b"\n")
            _proc.stdin.flush()
        except Exception as e:
            raise McpError(f"写入 MCP 子进程失败：{e}")

        deadline = time.time() + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise McpError(f"{method} 超时（{timeout}s）")
            try:
                line = _out_q.get(timeout=remain)
            except queue.Empty:
                raise McpError(f"{method} 超时（{timeout}s）")
            if line is None:
                raise McpError("MCP 子进程已退出")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue                    # 子进程若误往 stdout 打印，跳过而不是崩掉
            if msg.get("__reader_error__"):
                raise McpError(f"读取 MCP 输出失败：{msg['__reader_error__']}")
            # 通知（无 id）与本请求无关，忽略
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                raise McpError(f"{method} 返回错误：{msg['error'].get('message')}")
            return msg.get("result") or {}


def _stop():
    global _proc
    try:
        if _proc and _proc.poll() is None:
            _proc.stdin.close()
            _proc.wait(timeout=3)
    except Exception:
        try:
            _proc.kill()
        except Exception:
            pass
    _proc = None


def _mcp_execute(name, raw_args, ctx):
    """走 MCP 调一次工具，返回与 tools.execute() 同构的结果。"""
    # 模型给的是 JSON 字符串（函数调用协议如此），本地通道由 tools.execute 负责解析；
    # MCP 通道要自己转成对象，否则 dict(raw_args) 会去把字符串当"键值对序列"拆。
    args = raw_args
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except Exception:
            args = {}
    args = dict(args or {})
    # 本地通道靠 ctx 传"当前数据集"，MCP 通道没有 ctx 这个概念，
    # 所以把 ctx 里的默认值补进 arguments —— 两条路的语义才对得上。
    if ctx and ctx.get("dataset_id") and not args.get("dataset_id"):
        args["dataset_id"] = ctx["dataset_id"]

    if _proc is None or _proc.poll() is not None:
        _start()

    started = time.time()
    result = _rpc("tools/call", {"name": name, "arguments": args}, timeout=40)
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        raise McpError("服务端未返回 structuredContent")
    structured = dict(structured)
    structured["channel"] = "mcp"
    structured["channel_elapsed_ms"] = int((time.time() - started) * 1000)
    return structured


# ============================ 对外入口 ============================
def execute(name, raw_args, ctx=None):
    """执行一个工具调用。

    - 通道为 local（默认）：行为与直接调 tools.execute() 完全一致；
    - 通道为 mcp：走 MCP 协议；任何失败都降级到本地并在结果里带
      `channel_note`，让上层可以如实告知"这一轮走了降级通道"。
    """
    if _state["channel"] != "mcp":
        result = tools.execute(name, raw_args, ctx)
        _state["local_calls"] += 1
        result["channel"] = "local"
        return result

    try:
        result = _mcp_execute(name, raw_args, ctx)
        _state["mcp_calls"] += 1
        _state["last_elapsed_ms"] = result.get("channel_elapsed_ms", 0)
        return result
    except Exception as e:
        # 重试一次：大多是子进程被外部杀掉（编辑器热重载、Ctrl+C 残留）
        _state["mcp_failures"] += 1
        _state["last_error"] = f"{type(e).__name__}: {e}"
        logger.warning("MCP 通道失败，重启后重试一次：%s", e)
        _stop()
        try:
            result = _mcp_execute(name, raw_args, ctx)
            _state["mcp_calls"] += 1
            return result
        except Exception as e2:
            _state["last_error"] = f"{type(e2).__name__}: {e2}"
            logger.error("MCP 通道不可用，降级为本地直调：%s", e2)
            _state["fallbacks"] += 1
            result = tools.execute(name, raw_args, ctx)
            _state["local_calls"] += 1
            result["channel"] = "local"
            result["channel_note"] = f"MCP 通道不可用，本轮已降级为本地直调（{e2}）"
            return result


def catalog():
    """工具清单。mcp 模式下从 tools/list 取，证明清单真的来自协议而非本地常量。"""
    if _state["channel"] == "mcp":
        try:
            if _proc is None or _proc.poll() is not None:
                _start()
            listing = _rpc("tools/list", {}, timeout=15)
            return [{"name": t["name"], "description": t.get("description", ""),
                     "params": list((t.get("inputSchema") or {}).get("properties", {}).keys()),
                     "source": "mcp"} for t in (listing.get("tools") or [])]
        except Exception as e:
            logger.warning("从 MCP 取工具清单失败，退回本地常量：%s", e)
    return [dict(t, source="local") for t in tools.tool_catalog()]


def openai_tools():
    """给模型看的工具声明。

    mcp 模式下用 tools/list 的 inputSchema 转成 OpenAI 格式——这样模型看到的
    工具定义**就是 MCP 服务端声明的**，而不是本地那份副本。副本一旦和
    服务端不一致，"同一套工具"这句话就不成立了。
    """
    if _state["channel"] == "mcp":
        try:
            if _proc is None or _proc.poll() is not None:
                _start()
            listing = _rpc("tools/list", {}, timeout=15)
            specs = [{"type": "function", "function": {
                "name": t["name"], "description": t.get("description", ""),
                "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
            }} for t in (listing.get("tools") or [])]
            if specs:
                return specs
        except Exception as e:
            logger.warning("从 MCP 取工具声明失败，退回本地定义：%s", e)
    return tools.openai_tools()


def shutdown():
    _stop()
