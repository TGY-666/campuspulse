# -*- coding: utf-8 -*-
"""WSGI 入口。

给生产 WSGI 服务器用（Linux: gunicorn，Windows: waitress）：

    gunicorn -w 1 -k gthread --threads 8 -b 0.0.0.0:8000 wsgi:app
    waitress-serve --listen=0.0.0.0:8000 wsgi:app

为什么和 run.py 分开：run.py 是开发/演示入口，它会做端口顺延、
自动开浏览器、预热、app.run() 这些"本机体验"的事；
而 WSGI 入口必须是一个干净的模块级 app 对象，否则 gunicorn 起不来。

注意 workers 数量：**必须用 1 个 worker + 多线程**，不要 -w 4。
原因有三个，都是硬约束：
  1. SQLite 在多进程下的写锁竞争会让 ETL 偶发 "database is locked"；
  2. 工具通道（toolbus）的 MCP 子进程句柄是进程内单例，
     多 worker 会各起一份 server，日志里全是重复的 initialize；
  3. 检索索引与词频缓存按租户缓存在进程内存里，多进程等于内存翻倍。
"""
from campuspulse.web import create_app

app = create_app()

if __name__ == "__main__":
    # 允许 python wsgi.py 直接跑（等价于 run.py，主要用于排查
    # "gunicorn 能起但 python 起不来" 这类环境问题）
    import os
    app.run(host=os.environ.get("CP_HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", 8000)),
            debug=False, use_reloader=False, threaded=True)
