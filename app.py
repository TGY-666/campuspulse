from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import json
import os
import shutil
import socket
import threading
import webbrowser
from data_processor import run_analysis, detect_platform, platform_slug, _extract_rows

app = Flask(__name__)
# 允许跨域，前端后端调试必开
CORS(app)

# ==========【必须加上这两行，解决中文转义、浏览器缓存旧页面】==========
# Flask 2.3+ 已移除 JSON_AS_ASCII 配置项，需改用 app.json.ensure_ascii
try:
    app.json.ensure_ascii = False           # Flask 2.3 及以上
except AttributeError:
    app.config['JSON_AS_ASCII'] = False     # Flask 2.2 及更早版本兜底
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
# =================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE_DIR, "raw_data")
CLEANED_DIR = os.path.join(BASE_DIR, "cleaned_data")
OUTPUT_EXCEL_DIR = os.path.join(BASE_DIR, "output_excel")
OLD_DATA_PATH = os.path.join(BASE_DIR, "old_data.json")

os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(CLEANED_DIR, exist_ok=True)
os.makedirs(OUTPUT_EXCEL_DIR, exist_ok=True)

DATA_FILE = os.path.join(CLEANED_DIR, "data.json")

# 本系统只做抖音 + 小红书双平台舆情分析
ALLOWED_PLATFORMS = ["抖音", "小红书"]
# 旧批次的数据文件不删除，统一归档到这里，方便回溯
ARCHIVE_DIR = os.path.join(BASE_DIR, ".workbuddy", "raw_archive")

# ========== 上传状态标记 ==========
# 用于区分「原始示例数据」和「用户上传数据」两种状态。
# 没有这个标记时，禁止执行分析，页面只展示 old_data.json 里的示例数据，
# 避免"不上传也能刷出新图表"的困惑。
UPLOAD_STATE_FILE = os.path.join(CLEANED_DIR, "upload_state.json")


def read_upload_state():
    """读取上传状态，文件不存在或损坏时视为「未上传」"""
    default = {"uploaded": False, "analyzed": False, "files": [], "platforms": [],
               "uploadedAt": None, "analyzedAt": None}
    if not os.path.exists(UPLOAD_STATE_FILE):
        return dict(default)
    try:
        with open(UPLOAD_STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
        for k, v in default.items():
            state.setdefault(k, v)
        return state
    except Exception:
        return dict(default)


def write_upload_state(files, platforms=None, analyzed=False):
    """写入上传状态。

    上传新文件时：analyzed 置为 False（旧的分析结果立即失效，必须重新分析）。
    分析完成后：analyzed 置为 True，并记录 analyzedAt。
    """
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state = read_upload_state()
    state["uploaded"] = True
    state["files"] = files
    if platforms is not None:
        state["platforms"] = platforms
    state["analyzed"] = bool(analyzed)
    if analyzed:
        state["analyzedAt"] = now
    else:
        # 新上传 → 数据变了，之前的结果作废
        state["uploadedAt"] = now
        state["analyzedAt"] = None
    with open(UPLOAD_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return state


def reset_upload_state():
    """把状态重置为「未上传」。不删除任何文件，避免文件占用/权限导致的删除失败"""
    state = {"uploaded": False, "analyzed": False, "files": [], "platforms": [],
             "uploadedAt": None, "analyzedAt": None}
    with open(UPLOAD_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return state


def archive_raw_files():
    """把 raw_data 里现有的数据文件移到归档目录（不删除，可回溯）

    新上传一批数据前调用：保证 raw_data 里只有本次上传的文件，
    不会出现「只传了抖音、却把上次的小红书一起算进来」的串数据问题。
    """
    from datetime import datetime
    if not os.path.isdir(RAW_DIR):
        return None
    files = [f for f in os.listdir(RAW_DIR) if f.lower().endswith(".json")]
    if not files:
        return None
    target = os.path.join(ARCHIVE_DIR, datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(target, exist_ok=True)
    moved = []
    for fn in files:
        try:
            shutil.move(os.path.join(RAW_DIR, fn), os.path.join(target, fn))
            moved.append(fn)
        except Exception as e:
            # 归档失败不影响分析（分析只认本次上传清单），仅记录
            print(f"[warn] 归档 {fn} 失败：{e}")
    return target if moved else None


@app.route('/')
def index():
    # 直接访问 http://127.0.0.1:8000 即可打开前端页面
    return send_from_directory(BASE_DIR, 'index.html')


@app.route('/api/sentiment-data', methods=['GET'])
def get_sentiment_data():
    # 只有「已上传 + 已完成分析」才返回新数据，否则一律返回原始示例数据
    # 注意必须判断 analyzed：刚上传还没分析时，不能让上一次的旧结果继续展示
    state = read_upload_state()
    if state.get("analyzed") and os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        with open(OLD_DATA_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    return jsonify(data)


@app.route("/api/refresh-analysis", methods=["POST"])
def api_refresh():
    # 闸门：没有上传过数据就拒绝分析，保证首次打开只展示原始示例数据
    state = read_upload_state()
    if not state.get("uploaded"):
        return jsonify({
            "ok": False,
            "msg": "尚未上传新数据：当前展示的是原始示例数据集，无需刷新。"
                   "请先上传抖音或小红书的爬虫 JSON，再进行分析。"
        })
    files = state.get("files", [])
    if not files:
        return jsonify({"ok": False, "msg": "本次数据集为空，请重新上传数据文件。"})

    # 关键：只分析本次上传的文件，raw_data 里若有历史遗留文件一律忽略
    result = run_analysis(only_files=files)
    if result.get("ok"):
        # 平台以实际分析结果为准，避免状态栏显示与实际不一致
        stats = result.get("stats") or {}
        platforms = list((stats.get("platforms") or {}).keys()) or state.get("platforms", [])
        write_upload_state(files, platforms=platforms, analyzed=True)
    return jsonify(result)


def _batch_detail(files):
    """列出本次数据集中每个文件对应的平台与条数，供前端展示"""
    detail = []
    for fn in files or []:
        path = os.path.join(RAW_DIR, fn)
        if not os.path.exists(path):
            detail.append({"file": fn, "platform": detect_platform(fn), "count": None, "missing": True})
            continue
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                rows = _extract_rows(json.load(f))
            detail.append({"file": fn, "platform": detect_platform(fn, rows), "count": len(rows)})
        except Exception:
            detail.append({"file": fn, "platform": detect_platform(fn), "count": None})
    return detail


@app.route("/api/data-source", methods=["GET"])
def data_source():
    """返回当前页面对应的数据来源，供前端展示状态

    三种状态：
      example  —— 从未上传（或已恢复示例），展示 old_data.json 原始示例数据
      pending  —— 已上传但还没跑分析
      uploaded —— 已上传且已完成分析
    """
    state = read_upload_state()
    if not state.get("uploaded"):
        return jsonify({
            "source": "example",
            "label": "原始示例数据集",
            "files": [],
            "platforms": [],
            "detail": [],
            "uploadedAt": None
        })
    if state.get("analyzed") and os.path.exists(DATA_FILE):
        # 平台以「实际分析结果」为准，避免与实际图表不一致
        plats = state.get("platforms", [])
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                real = list((json.load(f).get("overview", {}).get("platforms") or {}).keys())
            if real:
                plats = real
        except Exception:
            pass
        return jsonify({
            "source": "uploaded",
            "label": "本次上传数据的分析结果",
            "files": state.get("files", []),
            "platforms": plats,
            "detail": _batch_detail(state.get("files", [])),
            "uploadedAt": state.get("uploadedAt"),
            "analyzedAt": state.get("analyzedAt")
        })
    return jsonify({
        "source": "pending",
        "label": "已上传、尚未分析",
        "files": state.get("files", []),
        "platforms": state.get("platforms", []),
        "detail": _batch_detail(state.get("files", [])),
        "uploadedAt": state.get("uploadedAt"),
        "analyzedAt": None
    })


@app.route("/api/reset-example", methods=["POST"])
def reset_example():
    # 重置状态 + 归档本次数据文件：保证下次上传是干净的新数据集
    # 不删除文件（只移动到 .workbuddy/raw_archive），避免占用/权限导致重置失败
    archive_raw_files()
    reset_upload_state()
    return jsonify({"ok": True, "msg": "已切换回原始示例数据集"})


@app.route("/api/upload-raw", methods=["POST"])
def upload_raw_file():
    """接收抖音 / 小红书的爬虫数据（json），存入 raw_data

    规则：
      1. 只接受抖音、小红书两个平台，其他平台直接拒绝；
      2. 上一批数据已经分析过 → 本次上传视为「新数据集」，
         先把旧文件归档移走，再保存新文件（避免新旧数据混在一起）；
      3. 上一批还没分析 → 允许多次上传累加（先传抖音再传小红书，一起分析）；
      4. 前端勾选「合并上次数据」时（append=1），不归档，直接追加/覆盖同平台文件。
    """
    if 'file' not in request.files:
        return jsonify({"ok": False, "msg": "未接收到文件"}), 400
    file = request.files['file']
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".json"):
        return jsonify({"ok": False, "msg": "只支持 .json 格式的爬虫数据文件"}), 400

    raw_bytes = file.read()
    try:
        # utf-8-sig 兼容带 BOM 的文件（部分爬虫导出工具会加 BOM）
        rows = _extract_rows(json.loads(raw_bytes.decode("utf-8-sig")))
    except Exception as e:
        return jsonify({"ok": False, "msg": f"JSON 解析失败：{e}"}), 400
    if not rows:
        return jsonify({"ok": False, "msg": "文件里没有可用的评论数据（需为 JSON 数组或含 data/comments 字段）"}), 400

    platform = detect_platform(filename, rows)
    if platform not in ALLOWED_PLATFORMS:
        other = ["微博", "B站", "快手", "知乎", "贴吧", "视频号", "Twitter", "YouTube"]
        if platform in other:
            msg = (f"识别到的平台是「{platform}」，本系统目前只支持抖音和小红书两个平台的数据，"
                   f"请上传抖音或小红书的爬虫 JSON。")
        else:
            msg = ("无法自动识别这份数据属于哪个平台。请把文件名改成包含 douyin / xhs 的名称"
                   "（如 douyin_comments.json、xhs_comments.json），或在数据里加上 platform 字段。")
        return jsonify({"ok": False, "msg": msg, "platform": platform}), 400

    append = str(request.form.get("append", "")).lower() in ("1", "true", "yes", "on")
    state = read_upload_state()
    files = list(state.get("files", []))
    platforms = list(state.get("platforms", []))

    replaced = False
    if not append and state.get("analyzed"):
        # 上一批数据已经出过结果 → 本次上传开始新数据集，旧文件归档移走
        archive_raw_files()
        files, platforms = [], []
        replaced = True

    save_name = f"{platform_slug(platform)}_comments.json"
    save_path = os.path.join(RAW_DIR, save_name)
    with open(save_path, "wb") as f:
        f.write(raw_bytes)

    if save_name not in files:
        files.append(save_name)
    if platform not in platforms:
        platforms.append(platform)
    # 写入状态：analyzed=False → 页面立即回到「待分析」，必须重新点「刷新分析」
    write_upload_state(files, platforms=platforms)

    detail = _batch_detail(files)
    return jsonify({
        "ok": True,
        "msg": f"已识别为「{platform}」数据，共 {len(rows)} 条，保存为 {save_name}"
               + ("（已归档上一批数据，本次只分析这份）" if replaced else ""),
        "platform": platform,
        "count": len(rows),
        "replaced": replaced,
        "files": files,
        "platforms": platforms,
        "detail": detail
    })


def find_free_port(start=8000, tries=20):
    """从 start 开始找一个可用端口，避免「端口被占用」直接启动失败"""
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


if __name__ == '__main__':
    want_port = int(os.environ.get("PORT", 8000))
    port = find_free_port(want_port)
    url = f"http://127.0.0.1:{port}"

    if port != want_port:
        # 端口被占用时会自动顺延，必须显式提示，否则容易误以为"页面没变化/跑不起来"
        print("!" * 58)
        print(f"  注意：{want_port} 端口已被其他程序占用（常见于上次的服务没关干净，")
        print(f"  或另一个同名项目的进程还在跑），已自动切换到 {port} 端口。")
        print(f"  请改用下面的地址访问，不要再用 {want_port}。")
        print("!" * 58)

    # use_reloader=False：刷新分析时会写入 cleaned_data / output_excel，
    # 自动重载器监听到文件变化会在请求中途重启服务，导致接口连接中断
    print("=" * 58)
    print(f"  舆情分析系统已启动，请访问：{url}")
    print("  关闭窗口或按 Ctrl+C 可停止服务")
    print("=" * 58)

    # 由启动脚本设置 AUTO_OPEN_BROWSER=1 时自动打开浏览器（端口顺延也能正确打开）
    if os.environ.get("AUTO_OPEN_BROWSER") == "1":
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    app.run(host='127.0.0.1', port=port, debug=True, use_reloader=False)
