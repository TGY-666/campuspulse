# -*- coding: utf-8 -*-
"""ETL：把爬虫导出的原始 JSON 清洗成统一评论表。

复用原系统 data_processor.py 的清洗规则（维度关键词、情感词、无效评论判定、
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
from data_processor import (  # noqa: E402
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
                  "差劲", "难受", "麻烦", "严格"]

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


def judge_sentiment(text):
    pos = sum(1 for w in POSITIVE_WORDS if w in text)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text)
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


def normalize_records(dataset_id, file_paths):
    """把一个批次的原始文件清洗成评论记录（含被过滤记录，便于解释质量）。

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
        "platform_raw": {},
        "platform_valid": {},
        "stages": [],
    }
    records = []
    seen = set()

    for path in file_paths:
        filename = os.path.basename(path)
        try:
            rows = load_source_file(path)
        except Exception as e:
            quality["stages"].append({"stage": "读取文件", "file": filename,
                                      "note": f"读取失败：{e}", "count": 0})
            continue
        platform = _detect_platform(filename, rows[0] if rows else {})
        quality["platform_raw"][platform] = quality["platform_raw"].get(platform, 0) + len(rows)
        quality["raw_count"] += len(rows)
        quality["stages"].append({"stage": "读取文件", "file": filename,
                                  "note": f"识别平台：{platform}", "count": len(rows)})

        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                quality["raw_count"] -= 1
                continue
            source_id = str(row.get("comment_id") or row.get("id") or f"{filename}#{idx}")
            raw_content = str(row.get("content") or row.get("text") or "").strip()
            base = {
                "comment_ref": make_comment_ref(dataset_id, platform, source_id),
                "dataset_id": dataset_id,
                "platform": platform,
                "content": raw_content,
                "content_masked": mask_text(raw_content),
                "source_file": filename,
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
            }

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

            # 5) 校园主题匹配（七大维度）
            text = clean_text(raw_content)
            dims = matched_dimensions(text)
            if not dims:
                quality["irrelevant"] += 1
                base.update(cleaning_status="irrelevant", filter_reason="未命中任何校园业务维度")
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
        {"stage": "校园主题匹配", "note": "需命中宿舍/食堂/招生/教学/环境/交通/活动之一",
         "count": -quality["irrelevant"]},
    ]
    return records, quality


def build_dataset(dataset_id, name, file_paths, source_label="", note="",
                  is_demo=True, data_version=None):
    """重建一个数据集：先清空旧记录，再写入新清洗结果。"""
    records, quality = normalize_records(dataset_id, file_paths)
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
        )
        ms = int((_t.time() - started) * 1000)
        print(f"  [{i}/{len(todo)}] 导入 {meta['name']}："
              f"{meta['raw_count']} → {meta['valid_count']} 条（{ms}ms）", flush=True)
        imported.append(meta["dataset_id"])
    if imported:
        print(f"  内置数据集就绪：{len(imported)} 个", flush=True)
    return imported
