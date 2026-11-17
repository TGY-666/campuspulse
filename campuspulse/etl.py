# -*- coding: utf-8 -*-
"""ETL：把爬虫导出的原始 JSON 清洗成统一评论表。

复用原系统 legacy/data_processor.py 的清洗规则（主题关键词、情感词、无效评论判定、
时间解析、点赞数解析），保证与历史结果口径一致；在此基础上补齐本次参赛版要求的
可追溯字段：comment_ref（脱敏引用）、cleaning_status（清洗结论）、source_record（内部溯源）。

清洗链路与文档 10.1 一致，每一步都会计数，最终有效率 = 有效 / 原始（同批次）。
"""
import hashlib
import json
import os
import re

from . import config, db, taxonomy
from .taxonomy import DIMENSION_KEYWORDS, matched_dimensions, primary_dimension

# ---- 复用原系统的清洗工具函数（时间解析 / 文本清洗 / 无效评论判定）----
from legacy.data_processor import (  # noqa: E402
    clean_text,
    is_valid_comment,
    timestamp_to_datetime,
)

SENTIMENT_MAP = {"正面": "positive", "中性": "neutral", "负面": "negative"}

POSITIVE_WORDS = ["好", "棒", "优秀", "舒服", "满意", "推荐", "不错", "可以", "方便", "齐全", "优美",
                  "干净", "划算", "实惠", "喜欢", "爱了", "好评", "值得", "靠谱", "nice", "yyds",
                  "完美", "贴心", "赞", "强", "绝了"]
NEGATIVE_WORDS = ["差", "坑", "避雷", "烂", "贵", "吐槽", "不好", "垃圾", "劝退", "难吃", "吵", "远",
                  "偏僻", "慢", "卡", "无语", "后悔", "不值", "敷衍", "烂透", "恶心", "坑爹", "骗人",
                  "差劲", "难受", "麻烦", "严格", "崩溃", "踩雷"]

# 脱敏：手机号、QQ/微信号、邮箱、链接、@用户、长串数字
_MASK_PATTERNS = [
    (re.compile(r"1[3-9]\d{9}"), "[手机号]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[邮箱]"),
    (re.compile(r"https?://\S+|www\.\S+"), "[链接]"),
    (re.compile(r"@[^\s，。！？、]{1,20}"), "[@用户]"),
    (re.compile(r"(?<!\d)\d{6,}(?!\d)"), "[数字串]"),
    (re.compile(r"(微信|vx|VX|qq|QQ)[:：]?\s*[A-Za-z0-9_-]{5,}"), r"\1[账号]"),
]


def mask_text(text):
    """展示前的二次脱敏，确保证据页不会出现个人联系方式。"""
    s = str(text or "")
    for pattern, repl in _MASK_PATTERNS:
        s = pattern.sub(repl, s)
    return s


def make_comment_ref(dataset_id, platform, source_id):
    """稳定、不可逆的内部引用 ID：可复现但不含任何身份信息。"""
    raw = f"{dataset_id}|{platform}|{source_id}".encode("utf-8")
    return "CMT-" + hashlib.sha1(raw).hexdigest()[:10].upper()


def parse_like(value):
    """点赞数解析：缺失返回 None（不默认为 0，避免把"没有数据"算成"零互动"）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        try:
            if value != value:  # NaN
                return None
            return int(value)
        except Exception:
            return None
    s = str(value).strip().replace(",", "").replace("，", "")
    if not s or s.lower() in ("nan", "none", "null", "-", ""):
        return None
    try:
        if s.endswith("万"):
            return int(float(s[:-1]) * 10000)
        if s.endswith("千"):
            return int(float(s[:-1]) * 1000)
        return int(float(s))
    except Exception:
        return None


# 单字情感词（差/卡/慢/远/好）会被常用词误伤，而且误伤出来的结果最扎眼——
# 「我分数都差不多」里的"差"、「每日打卡」里的"卡"、「好心累」里的"好"，
# 都不是在评价。判定前先把这些**明确不带情绪**的词换成不含关键词的说法，
# 词表本身不动，这样"太远""很差""卡顿"这类真抱怨照样能命中。
_FALSE_HIT = [
    ("差不多", "大致相同"), ("打卡", "签到"), ("慢慢", "渐渐"),
    ("永远", "始终"), ("最远", "最里面"), ("好久", "很长时间"),
    ("好像", "似乎"), ("好不容易", "费了很大劲"),
    ("好多", "很多"), ("好几个", "几个"), ("好几年", "几年"),
    ("好心累", "挺累"), ("好难", "很难"), ("好想", "一直想"),
]


def _is_asking(t):
    """是不是在问，而不是在评价。

    「大三可以去实习？」「学校可以报吗」里的"可以"是**询问许可**，不是夸它好；
    「宿舍好不好啊」里的"好"同理。这类句子如果按词表硬算，会被判成正面，
    然后出现在「正面评价样本」里，读起来完全对不上。
    """
    s = t.rstrip("！!～~ ")
    return "？" in s or "?" in s or s.endswith(("吗", "呢"))


def judge_sentiment(text):
    t = str(text or "")
    for src, dst in _FALSE_HIT:
        t = t.replace(src, dst)
    ask = _is_asking(t)
    pos = 0
    for w in POSITIVE_WORDS:
        if w not in t:
            continue
        # 疑问句里"可以"和单字"好"多半是询问，不计入正面
        if ask and w in ("可以", "好"):
            continue
        pos += 1
    neg = sum(1 for w in NEGATIVE_WORDS if w in t)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def _extract_rows(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "comments", "list", "items", "results", "notes"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _detect_platform(filename, row):
    raw = str((row or {}).get("platform") or (row or {}).get("source") or "").strip()
    low = raw.lower()
    if any(k in low for k in ("douyin", "抖音", "aweme")):
        return "抖音"
    if any(k in low for k in ("xhs", "xiaohongshu", "redbook", "小红书")):
        return "小红书"
    name = str(filename).lower()
    if any(k in name for k in ("douyin", "抖音", "aweme")):
        return "抖音"
    if any(k in name for k in ("xhs", "xiaohongshu", "redbook", "小红书")):
        return "小红书"
    return raw or "未知"


def load_source_file(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return _extract_rows(json.load(f))


def _in_window(row, date_from=None, date_to=None):
    """判断一条原始记录是否落在给定的评论日期窗口内。

    时间解析不出来的记录**放行**：交给后面的「时间解析」一步去计数。
    否则同一批文件，只因导入了哪段时间，无效时间的条数也会跟着变，
    质量页的分母就对不上原始文件了。
    """
    if not date_from and not date_to:
        return True
    ts = row.get("create_time") or row.get("time") or row.get("publish_time")
    dt = timestamp_to_datetime(ts)
    if dt is None or str(dt) == "NaT":
        return True
    day = dt.strftime("%Y-%m-%d")
    if date_from and day < date_from:
        return False
    if date_to and day > date_to:
        return False
    return True


def normalize_records(dataset_id, file_paths, date_from=None, date_to=None):
    """把一个批次的原始文件清洗成评论记录（含被过滤记录，便于解释质量）。

    文件只是「一组记录」的一种包装：实时采集拿到的也是一组记录。
    所以真正的清洗在 normalize_groups 里，这里只负责读盘——
    两条路各写一遍清洗规则的话，同一句话在导入页和采集页会得到两种结论。

    date_from / date_to 限定**评论发生时间**（不是采集时间）。限定在读取之后、
    去重之前生效：窗口外的记录根本不进入质量链路，原始条数也只统计窗口内的。
    这样"原始 N 条 → 有效 M 条"的分母与数据集名字里的年份是同一件事。
    """
    groups = []
    for path in file_paths:
        filename = os.path.basename(path)
        try:
            rows = load_source_file(path)
        except Exception as e:
            groups.append((filename, None, None, e))
            continue
        groups.append((filename, _detect_platform(filename, rows[0] if rows else {}), rows, None))
    return normalize_groups(dataset_id, groups, date_from=date_from, date_to=date_to)


def normalize_groups(dataset_id, groups, date_from=None, date_to=None):
    """groups: [(文件/来源名, 平台或 None, 原始行或 None, 读取异常或 None)]

    返回 (records, quality)。
    """
    quality = {
        "raw_count": 0,
        "duplicate": 0,
        "empty_content": 0,
        "invalid_time": 0,
        "too_short": 0,
        "irrelevant": 0,
        "valid_count": 0,
        "missing_like": 0,
        "effective_rate": 0.0,
        "screenshot_count": 0,          # 本批次里来自截图识别的记录数
        "time_inferred": 0,             # 其中"截图没写发布时间、按导入日期计入"的条数
        "platform_raw": {},
        "platform_valid": {},
        "stages": [],
    }
    records = []
    seen = set()
    dropped_window = 0

    for filename, platform, rows, read_err in groups:
        if read_err is not None:
            quality["stages"].append({"stage": "读取数据", "file": filename,
                                      "note": f"读取失败：{read_err}", "count": 0})
            continue
        rows = rows or []
        platform = platform or _detect_platform(filename, rows[0] if rows else {})
        read_count = len(rows)
        file_dropped = 0
        if date_from or date_to:
            in_window = [r for r in rows if not isinstance(r, dict)
                         or _in_window(r, date_from, date_to)]
            file_dropped = read_count - len(in_window)
            dropped_window += file_dropped
            rows = in_window
        malformed = sum(1 for r in rows if not isinstance(r, dict))
        row_count = len(rows) - malformed
        quality["platform_raw"][platform] = quality["platform_raw"].get(platform, 0) + row_count
        quality["raw_count"] += row_count
        note = f"识别平台：{platform}"
        if file_dropped:
            note += f"；窗口内 {row_count} / 读取 {read_count}"
        if malformed:
            note += f"；跳过 {malformed} 条非对象记录"
        # count 与 raw_count 必须是同一个口径（窗口内、可解析的对象行），
        # 否则漏斗各段相加会和有效数对不上。非对象行保留在原位只跳过，
        # 这样后面记录的 #idx 仍是文件里的真实下标（source_record 可回溯）。
        quality["stages"].append({"stage": "读取数据", "file": filename,
                                  "note": note, "count": row_count})

        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("comment_id") or row.get("id") or f"{filename}#{idx}")
            raw_content = str(row.get("content") or row.get("text") or "").strip()
            # 来源标记：截图导入的记录由 vision.to_source_rows 带 _ 前缀字段进来，
            # 爬虫导出的 JSON 没有这些字段，落库为 crawler / NULL，两条路互不影响。
            source_type = str(row.get("_source_type") or "crawler")
            base = {
                "comment_ref": make_comment_ref(dataset_id, platform, source_id),
                "dataset_id": dataset_id,
                "platform": platform,
                "content": raw_content,
                "content_masked": mask_text(raw_content),
                "source_file": row.get("_source_file") or filename,
                "source_record": f"{filename}#{idx}",
                "like_count": parse_like(row.get("like_count")),
                "comment_time": None,
                "comment_date": None,
                "comment_month": None,
                "comment_hour": None,
                "comment_weekday": None,
                "dimension": None,
                "dimensions": "[]",
                "sentiment": "unknown",
                "negative_reason": "",
                "positive_point": "",
                "content_len": len(raw_content),
                "cleaning_status": "kept",
                "filter_reason": "",
                "source_type": source_type,
                "ocr_confidence": row.get("_ocr_confidence"),
                "ocr_engine": row.get("_ocr_engine") or None,
                "time_inferred": 1 if row.get("_time_inferred") else 0,
            }
            if source_type == "screenshot":
                quality["screenshot_count"] += 1
                if base["time_inferred"]:
                    quality["time_inferred"] += 1

            # 1) 去重
            key = source_id if row.get("comment_id") else raw_content
            if key in seen:
                quality["duplicate"] += 1
                base.update(cleaning_status="duplicate", filter_reason="与同批次已有记录重复")
                records.append(base)
                continue
            seen.add(key)

            # 2) 空内容
            if not raw_content:
                quality["empty_content"] += 1
                base.update(cleaning_status="empty", filter_reason="评论内容为空")
                records.append(base)
                continue

            # 3) 时间解析（时间口径按评论发生时间）
            ts = row.get("create_time") or row.get("time") or row.get("publish_time")
            dt = timestamp_to_datetime(ts)
            if dt is None or str(dt) == "NaT":
                quality["invalid_time"] += 1
                base.update(cleaning_status="invalid_time", filter_reason="评论时间无法解析")
                records.append(base)
                continue
            base["comment_time"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            base["comment_date"] = dt.strftime("%Y-%m-%d")
            base["comment_month"] = dt.strftime("%Y-%m")
            base["comment_hour"] = int(dt.hour)
            base["comment_weekday"] = int(dt.weekday())

            # 4) 无效短评
            if not is_valid_comment(raw_content):
                quality["too_short"] += 1
                base.update(cleaning_status="too_short", filter_reason="长度不足或仅含表情符号")
                records.append(base)
                continue

            # 5) 校园主题匹配（按主题词典，八类）
            text = clean_text(raw_content)
            dims = matched_dimensions(text)
            if not dims:
                quality["irrelevant"] += 1
                base.update(cleaning_status="irrelevant", filter_reason="未命中任何校园业务主题")
                records.append(base)
                continue

            # 6) 打标
            sentiment = judge_sentiment(text)
            neg_reason, _ = taxonomy.classify(text, dims, taxonomy.NEGATIVE_REASONS)
            pos_point, _ = taxonomy.classify(text, dims, taxonomy.POSITIVE_POINTS)
            base.update(
                dimension=primary_dimension(text),
                dimensions=json.dumps(dims, ensure_ascii=False),
                sentiment=sentiment,
                negative_reason=neg_reason if sentiment == "negative" else "",
                positive_point=pos_point if sentiment == "positive" else "",
                content_len=len(text),
            )
            quality["valid_count"] += 1
            quality["platform_valid"][platform] = quality["platform_valid"].get(platform, 0) + 1
            if base["like_count"] is None:
                quality["missing_like"] += 1
            records.append(base)

    # 时间窗口剔除的行单独记一档：不写这一行，读者会以为"文件里本来就只有这么多"。
    # count 记 0：这些行从未进入 raw_count，若再记负数和漏斗会重复扣减。
    if dropped_window:
        quality["stages"].append({
            "stage": "时间范围",
            "note": (f"只保留评论时间在 {date_from or '最早'} ~ {date_to or '至今'} 的记录，"
                     f"剔除 {dropped_window} 条更早/更晚的评论（不计入原始条数）"),
            "count": 0})

    # 同一 comment_ref 可能因重复来自不同文件，主键冲突时保留首条
    dedup = {}
    for r in records:
        dedup.setdefault(r["comment_ref"], r)
    records = list(dedup.values())
    quality["valid_count"] = sum(1 for r in records if r["cleaning_status"] == "kept")
    quality["effective_rate"] = round(quality["valid_count"] / quality["raw_count"] * 100, 2) \
        if quality["raw_count"] else 0.0
    quality["stages"] += [
        {"stage": "去重", "note": "按 comment_id（缺失时按原文）去重", "count": -quality["duplicate"]},
        {"stage": "空内容剔除", "note": "content 为空", "count": -quality["empty_content"]},
        {"stage": "时间解析", "note": "无法解析发布时间的记录剔除", "count": -quality["invalid_time"]},
        {"stage": "无效短评过滤", "note": "长度 < 5 或仅含符号表情", "count": -quality["too_short"]},
        {"stage": "校园主题匹配", "note": "需命中宿舍/食堂/招生/教学/实习就业/环境/交通/活动之一",
         "count": -quality["irrelevant"]},
    ]
    # 只有真的混入截图记录时才多一行；纯爬虫批次保持原来的五段，口径不变
    if quality["screenshot_count"]:
        quality["stages"].append({
            "stage": "来源标记",
            "note": f"截图识别 {quality['screenshot_count']} 条（无发布时间按导入日期计入 "
                    f"{quality['time_inferred']} 条）",
            "count": quality["screenshot_count"]})
    return records, quality


def build_dataset(dataset_id, name, file_paths, source_label="", note="",
                  is_demo=True, data_version=None, date_from=None, date_to=None):
    """重建一个数据集：先清空旧记录，再写入新清洗结果。"""
    records, quality = normalize_records(dataset_id, file_paths,
                                        date_from=date_from, date_to=date_to)
    return write_dataset(dataset_id, name, records, quality, source_label, note,
                         is_demo, data_version)


def build_dataset_from_records(dataset_id, name, records, quality, source_label="",
                               note="", is_demo=False):
    """把已经清洗好的记录直接落库（实时采集走的这条路）。

    没有「文件」这一步，但清洗结果的结构与导入完全一致——
    否则质量页的分子分母、证据页的可追溯字段都会有一种来源对不上。
    """
    return write_dataset(dataset_id, name, records, quality, source_label, note, is_demo, None)


def write_dataset(dataset_id, name, records, quality, source_label="", note="",
                  is_demo=True, data_version=None):
    """落库并写数据集元信息（清空旧记录 → 插入 → 汇总口径）。

    导入（文件）与采集（实时）共用这一步，保证两条路产出的数据集
    在列表、质量页、证据页里长得一模一样。
    """
    db.cleanup_dataset_rows(dataset_id)
    db.insert_comments(records)

    kept = [r for r in records if r["cleaning_status"] == "kept"]
    dates = [r["comment_date"] for r in kept if r["comment_date"]]
    platforms = {}
    for r in kept:
        platforms[r["platform"]] = platforms.get(r["platform"], 0) + 1

    meta = {
        "dataset_id": dataset_id,
        "name": name,
        "source_label": source_label,
        "note": note,
        "status": "ready" if kept else "empty",
        "is_demo": 1 if is_demo else 0,
        "data_version": data_version or db.now_str(),
        "created_at": db.now_str(),
        "analyzed_at": db.now_str(),
        "raw_count": quality["raw_count"],
        "valid_count": quality["valid_count"],
        "time_start": min(dates) if dates else None,
        "time_end": max(dates) if dates else None,
        "platforms": json.dumps(platforms, ensure_ascii=False),
        "quality": json.dumps(quality, ensure_ascii=False),
    }
    db.upsert_dataset(meta)
    return meta, quality


def bootstrap_presets(force=False):
    """首次启动时把内置真实数据集导入数据库，已存在则跳过。

    首次导入要处理 2.5 万条原始评论，实测量级是十几秒。这段时间里
    服务端口已经起来了，页面却查不到数据——如果不打日志，用户只会
    看到"打开是空白的"，然后强退重来。所以每一步都要报进度。
    """
    import time as _t

    imported = []
    todo = []
    for preset in config.PRESET_DATASETS:
        existing = db.get_dataset(preset["dataset_id"])
        if existing and not force and existing.get("valid_count"):
            continue
        todo.append(preset)

    if todo:
        print(f"  首次启动：需要导入 {len(todo)} 个内置数据集（约十几秒，请稍候）", flush=True)

    for i, preset in enumerate(todo, 1):
        paths = []
        for fn in preset["files"]:
            p = os.path.join(config.BASE_DIR, fn)
            if os.path.exists(p):
                paths.append(p)
        if not paths:
            print(f"  [{i}/{len(todo)}] 跳过 {preset['dataset_id']}：原始文件不存在", flush=True)
            continue
        started = _t.time()
        meta, _ = build_dataset(
            preset["dataset_id"], preset["name"], paths,
            source_label=preset["source_label"], note=preset["note"], is_demo=True,
            date_from=preset.get("date_from"), date_to=preset.get("date_to"),
        )
        ms = int((_t.time() - started) * 1000)
        print(f"  [{i}/{len(todo)}] 导入 {meta['name']}："
              f"{meta['raw_count']} → {meta['valid_count']} 条（{ms}ms）", flush=True)
        imported.append(meta["dataset_id"])
    if imported:
        print(f"  内置数据集就绪：{len(imported)} 个", flush=True)
    return imported
