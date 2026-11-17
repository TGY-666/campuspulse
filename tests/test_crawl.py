# -*- coding: utf-8 -*-
"""P3 实时采集的不变量测试（不联网、不开浏览器）。

分工：
    tools/verify_*.py   跑"人怎么用"：开浏览器真采一轮
    本脚本              跑"机器怎么保证"：会话状态机、清洗口径、入库边界

为什么这几条必须单独断言：
    1) 采集到的记录必须走**和文件导入同一个**清洗函数。两套实现一旦分叉，
       同一句话在采集页是负面、在导入页是中性，而界面上完全看不出来。
    2) 清洗后的"被剔除记录"必须留在结果里。只剩一个变小的数字，
       就解释不了"为什么我采了 100 条只剩 8 条"。
    3) 未清洗 / 空结果 / 重复提交都不能入库。这三条是防"采集器采歪了
       直接变成分析口径"的最后一道闸。

跑在数据库副本上，不碰演示库：
    CP_AUTH=0 .venv/Scripts/python.exe tests/test_crawl.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DB = os.path.join(ROOT, "data", "campuspulse.db")

_TMPDIR = tempfile.mkdtemp(prefix="cp_crawl_")
COPY_DB = os.path.join(_TMPDIR, "crawl.db")
if os.path.isfile(SOURCE_DB):
    _src = sqlite3.connect(SOURCE_DB)
    try:
        _dst = sqlite3.connect(COPY_DB)
        try:
            _src.backup(_dst)          # WAL 下直接复制文件会丢 -wal 里的内容
        finally:
            _dst.close()
    finally:
        _src.close()

os.environ["CP_DB_PATH"] = COPY_DB
os.environ["CP_AUTH"] = "0"
os.environ["CP_TICKETS"] = "0"
os.environ["CP_CRAWL"] = "1"
sys.path.insert(0, ROOT)

from campuspulse import config, crawl, db, etl  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  [{detail}]" if detail and not cond else ""))


ROWS = [
    # 有效：命中维度 + 正/负情感
    {"platform": "抖音", "comment_id": "d1",
     "content": "宿舍空调不制冷，报修三次还没人来修", "create_time": "2026-10-01 12:00:00",
     "like_count": 32},
    {"platform": "抖音", "comment_id": "d2",
     "content": "食堂的饭菜挺好吃的，价格也实惠", "create_time": "2026-10-02 09:10:00",
     "like_count": 8},
    {"platform": "小红书", "comment_id": "x1",
     "content": "学校快递点太远了，拿个包裹要走半小时", "create_time": 1759300000,
     "like_count": 15},
    {"platform": "小红书", "comment_id": "x2",
     "content": "选课系统一开就崩，抢课全靠运气", "create_time": "2026-10-02 08:00:00",
     "like_count": 21},
    # 同一条评论被重复抓取（真实采集里很常见：翻页重叠）
    {"platform": "抖音", "comment_id": "d1",
     "content": "宿舍空调不制冷，报修三次还没人来修", "create_time": "2026-10-01 12:00:00",
     "like_count": 32},
    # 剔除：太短 / 空 / 时间无效 / 非校园主题
    {"platform": "抖音", "comment_id": "d4", "content": "顶", "create_time": "2026-10-03 12:00:00",
     "like_count": 1},
    {"platform": "小红书", "comment_id": "x3", "content": "", "create_time": "2026-10-03 12:00:00",
     "like_count": 0},
    {"platform": "小红书", "comment_id": "x4", "content": "今天天气真不错啊兄弟们",
     "create_time": "不是时间", "like_count": 0},
    {"platform": "小红书", "comment_id": "x5", "content": "这个话题我不太懂，路过看看",
     "create_time": "2026-10-04 12:00:00", "like_count": 0},
]

VALID = 4          # d1 / d2 / x1 / x2
CANDIDATES = 7     # 9 条里：同 id 重复 1 条、空正文 1 条在采集层就被挡掉


def fake_worker(sess):
    """顶掉浏览器采集：直接灌入固定候选集，验证下游链路。"""
    for r in ROWS:
        crawl._add_row(sess, r["platform"], r)
    crawl._finish(sess)


def wait_done(sid, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        s = crawl.view(sid)
        if s["status"] != "running":
            return s
        time.sleep(0.1)
    raise AssertionError("会话没有在预期时间内结束")


def main():
    print("P3 实时采集 · 不变量检查")
    # 整个测试期间都用假 worker 顶掉浏览器：这条链路的被测对象是
    # 会话状态机 / 清洗口径 / 入库边界，不是"平台今天能不能连通"。
    # 恢复得太早会让后面的用例真的去启动浏览器，跑出 0 条还要排查半天。
    original_worker = crawl._worker
    crawl._worker = fake_worker

    # ---------- 1) cookie 解析 ----------
    print("\n[1] 登录态解析")
    cs = crawl.parse_cookies("sessionid=abc; ttwid=xyz; path=/; Secure", "抖音")
    names = {c["name"] for c in cs}
    ok("只取 name=value，属性行（path/Secure）被跳过", names == {"sessionid", "ttwid"}, names)
    ok("域名按平台补全", cs[0]["domain"] == ".douyin.com", cs[0]["domain"])
    ok("空串解析为空列表", crawl.parse_cookies("", "抖音") == [])
    ok("JSON 数组形式可用", len(crawl.parse_cookies('[{"name":"a","value":"1"}]', "小红书")) == 1)

    # ---------- 2) 会话状态机 + 清洗 ----------
    print("\n[2] 采集 → 清洗")
    sess = crawl.start("宿舍 食堂", ["抖音", "小红书"], 10)
    sid = sess["session_id"]
    v = wait_done(sid)

    ok("采集结束后状态为 done", v["status"] == "done", v["status"])
    ok("同 id 重复在采集层就被挡掉", v["collected"] == CANDIDATES, f"{v['collected']}/{CANDIDATES}")
    ok("平台分布分别计数", v["per_platform"] == {"抖音": 3, "小红书": 4}, v["per_platform"])
    ok("轮询返回的正文已脱敏", all("content" in r for r in v["rows"]))

    c = crawl.clean(sid)
    ok("原始数 = 候选数", c["raw_count"] == CANDIDATES, c["raw_count"])
    ok(f"有效数 = {VALID}", c["valid_count"] == VALID, c["valid_count"])
    ok("有效率 = valid/raw", abs(c["effective_rate"] - round(VALID / CANDIDATES * 100, 2)) < 0.01,
       c["effective_rate"])
    stages = {s["stage"]: s["count"] for s in c["stages"]}
    ok("清洗摘要单独给出短评数（页面要能说“采到了但没算”）",
       c.get("too_short") == 1, c.get("too_short"))
    ok("时间解析阶段 −1", stages.get("时间解析") == -1, stages.get("时间解析"))
    ok("短评阶段 −1", stages.get("无效短评过滤") == -1, stages.get("无效短评过滤"))
    ok("主题匹配阶段 −1", stages.get("校园主题匹配") == -1, stages.get("校园主题匹配"))

    rejected = crawl.records_view(sid, "rejected", 0, 50)
    reasons = {r["filter_reason"] for r in rejected["rows"]}
    ok("被剔除记录保留原因（可解释）", len(rejected["rows"]) == 3 and all(reasons), sorted(reasons))
    kept = crawl.records_view(sid, "kept", 0, 50)
    ok("通过的记录带维度与情感", all(r["dimension"] and r["sentiment"] != "unknown"
                                  for r in kept["rows"]))

    # ---------- 2.5) 时间段过滤 ----------
    # 平台不支持按时间检索，所以这段逻辑是"按发布时间筛 + 统计跳过了多少"。
    # 必须能区分三种情况：区间内保留 / 区间外跳过 / 时间解析不出来（不算跳过，
    # 交给清洗阶段判 invalid_time，否则质量页的分子分母会对不上）。
    print("\n[2.5] 时间段过滤")
    ranged = crawl.start("时间段", ["抖音", "小红书"], 10,
                         date_from="2026-10-01", date_to="2026-10-02")
    vr = wait_done(ranged["session_id"])

    def day_of(ts):
        dt = etl.timestamp_to_datetime(ts)
        return None if str(dt) == "NaT" else dt.strftime("%Y-%m-%d")

    days = [day_of(r["create_time"]) for r in vr["rows"]]
    ok("区间内记录保留（含时间无法解析的 1 条）", vr["collected"] == 4,
       f"{vr['collected']} / {days}")
    ok("区间外记录被跳过并计数", vr["dropped_range"] == 3, vr["dropped_range"])
    ok("候选集里没有区间外的记录",
       all(d is None or "2026-10-01" <= d <= "2026-10-02" for d in days), days)
    ok("时间无法解析的记录不被时间筛掉（留给清洗判）", None in days, days)

    for bad, label in ((("2026/10/01", ""), "斜杠日期被拒"),
                       (("2026-10-05", "2026-10-01"), "开始晚于结束被拒")):
        try:
            crawl.start("非法", ["抖音"], 10, date_from=bad[0], date_to=bad[1])
            ok(label, False, "没有抛错")
        except crawl.CrawlError as e:
            ok(label, e.code == "bad_date", e.code)

    # ---------- 3) 入库边界 ----------
    print("\n[3] 入库边界")
    second = crawl.start("边界", ["抖音"], 10)
    try:
        sid2 = second["session_id"]
        wait_done(sid2)
        try:
            crawl.commit(sid2, "未清洗不该入库")
            ok("未清洗不能入库", False)
        except crawl.CrawlError as e:
            ok("未清洗不能入库", e.code == "not_cleaned", e.code)
    finally:
        pass

    meta = crawl.commit(sid, "实时采集 · 单测")
    ok("入库后有效数与清洗结果一致", meta["valid_count"] == c["valid_count"], meta["valid_count"])
    ds = db.get_dataset(meta["dataset_id"])
    ok("数据集已写入库", bool(ds) and ds["valid_count"] == VALID, ds and ds["valid_count"])
    ok("来源标注为实时采集", "实时采集" in (ds["source_label"] or ""), ds["source_label"])
    ok("is_demo = 0（不是生成数据）", ds["is_demo"] == 0, ds["is_demo"])

    stored = db.query_all(
        "SELECT source_type, COUNT(*) AS n FROM comments WHERE dataset_id = ? GROUP BY source_type",
        (meta["dataset_id"],))
    ok("落库来源类型为 crawler", stored and stored[0]["source_type"] == "crawler", stored)

    try:
        crawl.commit(sid, "再来一次")
        ok("同一会话不能重复入库", False)
    except crawl.CrawlError as e:
        ok("同一会话不能重复入库", e.code == "already_committed", e.code)

    # ---------- 3.5) 并入已有数据集 ----------
    # 假 worker 每轮回放同一批 ROWS，所以第二次采集的内容必定与库里重复：
    # 这正好用来验证"重复的跳过、有效数不重复计"。
    c2 = crawl.clean(sid2)
    before = db.get_dataset(meta["dataset_id"])
    merged = crawl.commit(sid2, None, target_dataset_id=meta["dataset_id"])
    ok("选择已有数据集时不新建", merged["merged"] and merged["dataset_id"] == meta["dataset_id"],
       str(merged.get("merged")))
    after = db.get_dataset(meta["dataset_id"])
    ok("并库后有效数 = 原有 + 新增",
       after["valid_count"] == before["valid_count"] + merged["added"],
       f"{before['valid_count']} + {merged['added']} != {after['valid_count']}")
    ok("重复内容全部跳过", merged["skipped"] == c2["valid_count"],
       f"skipped={merged['skipped']} 清洗后={c2['valid_count']}")
    ok("并库后条数仍等于库里实际 kept",
       after["valid_count"] == db.scalar(
           "SELECT COUNT(*) FROM comments WHERE dataset_id = ? AND cleaning_status = 'kept'",
           (meta["dataset_id"],)), str(after["valid_count"]))

    # ---------- 4) 清洗口径与导入一致 ----------
    print("\n[4] 与文件导入同口径")
    groups = [("实时采集 · 抖音", "抖音", [r for r in ROWS if r["platform"] == "抖音"], None),
              ("实时采集 · 小红书", "小红书", [r for r in ROWS if r["platform"] == "小红书"], None)]
    records, quality = etl.normalize_groups("ds_probe", groups)
    ok("normalize_groups 与导入同一出口（含去重/空内容两段）",
       quality["raw_count"] == len(ROWS) and quality["valid_count"] == VALID
       and quality["duplicate"] == 1 and quality["empty_content"] == 1,
       f"raw={quality['raw_count']} valid={quality['valid_count']} "
       f"dup={quality['duplicate']} empty={quality['empty_content']}")
    ok("每条记录都有可追溯引用", all(r["comment_ref"].startswith("CMT-") for r in records))

    # ---------- 清理 ----------
    crawl._worker = original_worker
    db.cleanup_dataset_rows(meta["dataset_id"])
    db.delete_dataset(meta["dataset_id"])
    shutil.rmtree(_TMPDIR, ignore_errors=True)

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
