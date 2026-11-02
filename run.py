# -*- coding: utf-8 -*-
"""CampusPulse 启动入口。

用法：
    .venv/Scripts/python.exe run.py            # 默认 8000，被占用时自动顺延
    PORT=8080 .venv/Scripts/python.exe run.py

首次启动会自动把项目内的真实爬虫数据清洗入库（约 2-5 秒），之后直接读 SQLite。
"""
import os
import socket
import threading
import webbrowser

from campuspulse.web import create_app

app = create_app()


def find_free_port(start=8000, tries=30):
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


if __name__ == "__main__":
    want = int(os.environ.get("PORT", 8000))
    port = find_free_port(want)
    url = f"http://127.0.0.1:{port}"
    if port != want:
        print("!" * 60)
        print(f"  端口 {want} 被占用，已自动切换到 {port}，请访问 {url}")
        print("!" * 60)
    print("=" * 60)
    print("  CampusPulse 已启动：", url)
    print("  按 Ctrl+C 停止服务")
    print("=" * 60)
    # 演示预热：把词典加载 / 检索索引 / 首次统计查询提前跑完（只读，幂等）。
    # 放开 CP_WARMUP=0 可关掉——预热只是为了"第一次点开不转圈"，不是依赖。
    if os.environ.get("CP_WARMUP", "1") != "0":
        from campuspulse import warmup
        warmup.run_async(quiet=False)
    if os.environ.get("AUTO_OPEN_BROWSER") == "1":
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
