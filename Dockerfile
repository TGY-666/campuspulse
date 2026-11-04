# CampusPulse 生产镜像
#
# 构建（仅核心，约 350MB）：
#     docker build -t campuspulse:1.0 .
# 构建（含截图 OCR + MCP，约 1.2GB）：
#     docker build --build-arg WITH_EXT=1 -t campuspulse:1.0-full .
# 运行：
#     docker run -d --name campuspulse -p 8000:8000 \
#         -e CP_AUTH=1 -v $(pwd)/data:/app/data campuspulse:1.0-full
#
# 为什么默认不装扩展依赖：onnxruntime 一家就 200MB，
# 而核心功能（分析 / 检索 / Agent / 工单）一个都不需要它。
# 让镜像大小反映真实依赖，而不是"反正都装上一劳永逸"。

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CP_HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

# libgl1/libglib2.0-0：opencv（rapidocr 依赖）运行时需要
# libgomp1：onnxruntime 的 OpenMP 运行时
# curl：HEALTHCHECK 用
# 即便不开 WITH_EXT 也一起装——共约 30MB，但能避免
# "开了扩展却栽在 ImportError: libGL.so.1" 这种环境坑。
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-ext.txt ./
ARG WITH_EXT=0
RUN pip install -r requirements.txt "gunicorn>=21.0,<23" \
    && if [ "$WITH_EXT" = "1" ]; then pip install -r requirements-ext.txt; fi

COPY . .

# 运行时数据（SQLite / 上传截图 / 日志 / 会话密钥）都在 /app/data，
# 必须挂出来——否则容器一重启，向导出的数据集和工单全部消失。
VOLUME ["/app/data"]
EXPOSE 8000

# start-period 给足 90 秒：首次启动要导入 2.5 万条评论建库。
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# 单 worker + 多线程：原因见 wsgi.py 顶部注释（SQLite 写锁、
# MCP 子进程单例、进程内缓存）。--timeout 120 是留给 Agent
# 一轮多步工具调用的，默认 30 秒会在复杂提问上被强杀。
CMD ["gunicorn", "-w", "1", "-k", "gthread", "--threads", "8", \
     "-b", "0.0.0.0:8000", "--timeout", "120", \
     "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]
