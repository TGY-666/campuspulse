# -*- coding: utf-8 -*-
"""P1-1 截图评论导入验收：解析规则 → 识别 → 复核入库 → 证据页可查。

运行：.venv/Scripts/python.exe tests/test_vision.py

两种跑法：
  · 默认在临时库上跑（CP_DB_PATH 指向 data/_test_vision.db），
    不会把测试数据集写进演示库；
  · 加 --keep 保留临时库，方便手工打开看。

不依赖真实截图：用例里的图由 PIL 现场画，文字和版式都是已知的，
所以"识别对不对"可以被断言，而不是"看着像是对的"。
"""
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TEST_DB = os.path.join(ROOT, "data", "_test_vision.db")
if "--keep" not in sys.argv:
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(TEST_DB + suffix)
        except OSError:
            pass
os.environ["CP_DB_PATH"] = TEST_DB
os.environ["CP_TENANT_ENFORCE"] = "0"

from campuspulse import config, db, vision  # noqa: E402
from campuspulse.web import create_app  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail else ""))


# ============================ 造一张"抖音评论区"截图 ============================
COMMENTS = [
    ("小明同学", "宿舍热水又停了，晚上洗不了澡真的是服了", "3天前 · 广东", "12"),
    ("阿May", "食堂二楼新开的窗口味道不错，价格也实惠", "昨天 12:30", "86"),
    ("匿名用户", "招生办的电话一直打不通，报名咨询没人接", "05-12", "7"),
]


def make_screenshot(items=COMMENTS, header="全部评论 126"):
    from PIL import Image, ImageDraw, ImageFont
    width, height = 720, 160 + 150 * len(items)
    im = Image.new("RGB", (width, height), "#ffffff")
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 22)
        fs = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
    except OSError:
        f = fs = ImageFont.load_default()
    if header:
        d.text((20, 16), header, font=f, fill="#111111")
    y = 70
    for name, text, when, likes in items:
        d.text((20, y), name, font=fs, fill="#888888"); y += 30
        d.text((20, y), text, font=f, fill="#111111"); y += 36
        d.text((20, y), when, font=fs, fill="#999999"); y += 26
        d.text((20, y), "回复   " + likes, font=fs, fill="#999999"); y += 40
        d.line((20, y, width - 20, y), fill="#eeeeee"); y += 20
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# ============================ 1. 纯解析规则（不需要 OCR）============================
def test_parse_rules():
    print("\n【解析规则】")
    lines = [
        {"text": "全部评论 38", "score": .99, "right": 200},
        {"text": "小明同学", "score": .98, "right": 120},
        {"text": "宿舍热水又停了，晚上洗不了澡", "score": .99, "right": 690},
        {"text": "真的是服了", "score": .97, "right": 300},
        {"text": "3天前 · 广东", "score": .99, "right": 200},
        {"text": "回复", "score": .99, "right": 60},
        {"text": "12", "score": .99, "right": 40},
        {"text": "阿May", "score": .98, "right": 90},
        {"text": "食堂二楼新开的窗口味道不错，价格也实惠", "score": .99, "right": 620},
        {"text": "昨天 12:30", "score": .99, "right": 180},
    ]
    rows = vision.parse_lines(lines, platform="抖音")
    texts = [r["content"] for r in rows]
    check("区块标题（全部评论 38）不入库", all("全部评论" not in t for t in texts), str(texts))
    check("界面噪声（回复/纯数字）不入库", all(t not in ("回复", "12") for t in texts))
    check("折行合并为一条", any(t == "宿舍热水又停了，晚上洗不了澡真的是服了" for t in texts), texts[0] if texts else "")
    check("短昵称（阿May）不会被并入评论", all("阿May" not in t for t in texts))
    check("两条互不相干的评论没有被粘在一起", len(texts) == 2, f"{len(texts)} 条")
    check("时间+属地合一行能取到时间", rows and rows[0]["create_time"].endswith("08:00:00") is False
          or rows[0]["time_inferred"] == 0, rows[0]["time_text"] if rows else "")

    # "昨天 12:30" 应解析为昨天 12:30
    from datetime import datetime, timedelta
    now = datetime(2026, 11, 4, 9, 0, 0)
    dt = vision.parse_time_token("昨天 12:30", now)
    check("昨天 12:30 → 前一天 12:30",
          dt == datetime(2026, 11, 3, 12, 30), str(dt))
    dt = vision.parse_time_token("3天前", now)
    check("3天前 → 三天前", dt == datetime(2026, 11, 1, 9, 0), str(dt))
    dt = vision.parse_time_token("12-28", now)
    check("12-28 跨年回落到去年", dt is not None and dt.year == 2025, str(dt))
    check("空文本不猜时间", vision.parse_time_token("") is None)
    check("正文不当作时间", vision.parse_time_token("这周食堂好挤") is None)


# ============================ 2. 真实 OCR + 全链路 ============================
def test_pipeline():
    print("\n【OCR 与入库】")
    status = vision.engine_status()
    check("有可用 OCR 引擎", status["active"] is not None, str(status["active"]))
    if not status["active"]:
        return

    blob = make_screenshot()
    out = vision.recognize([("douyin_sample.png", blob)])
    rows = out["rows"]
    check("识别出 3 条评论", len(rows) == 3, f"{len(rows)} 条")
    check("置信度来自逐字得分", all(r["confidence"] and r["confidence"] > 0.5 for r in rows),
          str([r["confidence"] for r in rows]))
    check("截图内时间被采用", sum(1 for r in rows if not r["time_inferred"]) == 3,
          str([r["time_text"] for r in rows]))
    check("平台识别不瞎猜", rows[0]["platform"] in config.PLATFORMS + ["未知"], rows[0]["platform"])

    app = create_app()
    cli = app.test_client()

    r = cli.post("/api/vision/recognize", data={
        "files": (io.BytesIO(blob), "douyin_sample.png"),
    }, content_type="multipart/form-data")
    body = r.get_json()
    check("POST /api/vision/recognize 返回草稿", r.status_code == 200 and body.get("ok"), str(r.status_code))
    sid = (body.get("data") or {}).get("session_id")
    check("草稿有 session_id", bool(sid), str(sid))

    draft_rows = body["data"]["rows"]
    # 模拟人工复核：删掉一条，改一条的错字
    keep = draft_rows[:2]
    keep[1]["content"] = keep[1]["content"].replace("味道不错", "味道很不错")

    r = cli.post("/api/vision/commit", json={"session_id": sid, "rows": keep})
    body = r.get_json()
    ok = r.status_code == 200 and body.get("ok")
    check("POST /api/vision/commit 建数据集", ok,
          json.dumps(body.get("error"), ensure_ascii=False) if not ok else "")
    if not ok:
        return

    data = body["data"]
    ds_id = data["dataset_id"]
    check("只入库复核保留的 2 条", data["valid_count"] == 2, f"{data['valid_count']} 条")
    check("返回里带截图来源条数", data.get("screenshot_count") == 2, str(data.get("screenshot_count")))

    ds = db.get_dataset(ds_id)
    check("数据集标记为非演示数据", ds and ds["is_demo"] == 0)
    check("数据集来源标签写明截图", "截图" in (ds["source_label"] or ""), ds["source_label"])

    r = cli.get(f"/api/comments?dataset_id={ds_id}&limit=10")
    items = r.get_json()["data"]["items"]
    check("证据检索能查到截图记录", len(items) == 2, f"{len(items)} 条")
    check("证据带 source_type=screenshot", all(i["source_type"] == "screenshot" for i in items),
          str([i["source_type"] for i in items]))
    check("证据带 OCR 置信度", all(i["ocr_confidence"] is not None for i in items))
    check("人工改过的文字真的入库了",
          any("味道很不错" in i["snippet"] for i in items), str([i["snippet"] for i in items]))

    ref = items[0]["comment_ref"]
    r = cli.get(f"/api/comments/{ref}?dataset_id={ds_id}")
    detail = r.get_json()["data"]
    check("详情页标注来源为截图识别", detail["source_label"] == "截图识别", detail["source_label"])
    check("详情页带识别引擎", detail.get("ocr_engine") == "rapidocr", str(detail.get("ocr_engine")))

    # 复核会话用掉即焚，重复提交必须失败（否则同一批截图能被导入两次）
    r = cli.post("/api/vision/commit", json={"session_id": sid, "rows": keep})
    check("同一会话不能重复提交", r.status_code >= 400, str(r.status_code))

    # 非法输入
    r = cli.post("/api/vision/recognize", data={
        "files": (io.BytesIO(b"not-an-image"), "x.txt"),
    }, content_type="multipart/form-data")
    check("非图片被拒绝", r.status_code == 400, str(r.status_code))
    r = cli.post("/api/vision/commit", json={"session_id": "../etc/passwd", "rows": keep})
    check("session_id 做格式校验", r.status_code >= 400, str(r.status_code))

    db.delete_dataset(ds_id)
    print(f"  · 已清理测试数据集 {ds_id}")


def main():
    print("=" * 64)
    print("P1-1 截图评论导入验收")
    print("=" * 64)
    test_parse_rules()
    test_pipeline()
    print("\n" + "=" * 64)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print("  · " + f)
    print("=" * 64)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
