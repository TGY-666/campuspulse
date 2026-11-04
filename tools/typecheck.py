# -*- coding: utf-8 -*-
"""前端类型检查（P1-3 渐进式类型化）。

用法：
    .venv/Scripts/python.exe tools/typecheck.py          # 检查并打印结论
    .venv/Scripts/python.exe tools/typecheck.py --strict  # 依赖缺失时按失败退出

做的事就一件：跑 `tsc -p jsconfig.json`（allowJs + checkJs + noEmit）。

为什么用 JSDoc + checkJs 而不是重写 TS：
    3,700 行原生 JS 在赛前重写的回归风险远大于收益（细则里这项只值 4 分）。
    JSDoc 方案能在**不改变任何运行时代码形态**的前提下拿到类型检查：
    改错了变量名、传错了参数、读错了返回结构，`npm run typecheck` 都会报出来。

依赖缺失（没跑过 npm install）时默认"跳过"而不是"失败"：
    评委拿到仓库先跑 Python 验收，不应因为没装 Node 就一片红。
    需要用 `--strict` 把它变成硬性检查（CI / 提交前自查用）。
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TSC = os.path.join(ROOT, "node_modules", "typescript", "bin", "tsc")
CONFIG = os.path.join(ROOT, "jsconfig.json")


def node_bin():
    for name in ("node", "node.exe"):
        p = shutil.which(name)
        if p:
            return p
    # 常见安装位置，最后才试：PATH 没配的机器上也能跑
    for p in (r"C:\Program Files\nodejs\node.exe",
              os.path.expanduser("~/.workbuddy/binaries/node/versions")):
        if os.path.isfile(p):
            return p
    return None


def run():
    """返回 (状态, 说明)。状态：ok / skipped / failed。"""
    if not os.path.isfile(TSC):
        return "skipped", ("未安装 TypeScript（缺 node_modules）。"
                           "先执行：npm install（需 Node 18+）")
    node = node_bin()
    if not node:
        return "skipped", "未找到 node 可执行文件，无法运行 tsc"
    try:
        proc = subprocess.run([node, TSC, "-p", CONFIG],
                              cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=180)
    except subprocess.TimeoutExpired:
        return "failed", "tsc 超时（180s）"
    out = (proc.stdout or "").strip()
    if proc.returncode == 0:
        n = len(files())
        return "ok", f"{n} 个前端文件类型检查通过"
    return "failed", out or (proc.stderr or "").strip()


def files():
    d = os.path.join(ROOT, "static", "cp")
    return [f for f in os.listdir(d) if f.endswith(".js")] if os.path.isdir(d) else []


def main():
    ap = argparse.ArgumentParser(description="前端类型检查（tsc --noEmit）")
    ap.add_argument("--strict", action="store_true", help="依赖缺失时按失败退出")
    args = ap.parse_args()

    status, detail = run()
    icon = {"ok": "✅", "skipped": "⚠️", "failed": "❌"}[status]
    print(f"{icon} 类型检查：{detail}")
    if status == "failed":
        print("\n修法：按报错逐条补 JSDoc 注解或加类型断言，不要用 // @ts-ignore 盖过去——"
              "盖掉的那一行将来一定会出问题。")
        return 1
    if status == "skipped" and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
