# -*- coding: utf-8 -*-
"""统一统计层 —— 全系统唯一的数据口径。

驾驶舱、数据洞察、热点证据、Agent 工具、综合报告全部调用这里的函数，
保证"同一个筛选条件在三个地方算出同一个数"（文档 10.3 / 15.2 的要求）。

约定：
  * 分母永远显式返回（denominator 字段），前端不再自己算比例；
  * 主题按多标签统计（一条评论可命中多个主题），并随结果返回 count_mode；
  * 缺失点赞返回 NULL，不当作 0；
  * 空结果返回 counts=0 + empty=True，不抛异常、不补造数据。
"""
import json
import re
from collections import Counter

from . import config, db, etl

# 允许的中文/英文入参写法，统一归一
_SENTIMENT_ALIASES = {
    "positive": "positive", "正面": "positive", "好评": "positive",
    "neutral": "neutral", "中性": "neutral", "中立": "neutral",
    "negative": "negative", "负面": "negative", "差评": "negative",
}
_GRANULARITIES = ("day", "week", "month")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class FilterError(ValueError):
    """筛选参数非法（前端/Agent 都会捕获并提示用户）。"""


def _to_list(value):
    if value is None or value == "" or value == []:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def _check_date(value, field):
    if not value:
        return None
    s = str(value).strip()
    if not _DATE_RE.match(s):
        raise FilterError(f"{field}日期格式应为 YYYY-MM-DD，收到「{value}」")
    from datetime import date
    try:
        date.fromisoformat(s)
    except ValueError:
        raise FilterError(f"{field}日期不存在：{value}（月份需在 1-12，日期需在该月范围内）")
    return s


def dataset_or_raise(dataset_id):
    ds = db.get_dataset(dataset_id or config.DEFAULT_DATASET_ID)
    if not ds:
        raise FilterError(f"数据集不存在：{dataset_id}")
    return ds


def build_filters(dataset_id=None, platform=None, dimension=None, sentiment=None,
                  date_from=None, date_to=None, keyword=None):
    """归一化并校验筛选条件，返回可直接喂给 SQL 的结构。"""
    ds = dataset_or_raise(dataset_id or config.DEFAULT_DATASET_ID)

    platforms = _to_list(platform)
    for p in platforms:
        if p not in config.PLATFORMS:
            raise FilterError(f"不支持的平台「{p}」，可选：{'、'.join(config.PLATFORMS)}")

    dims = _to_list(dimension)
    for d in dims:
        if d not in config.DIMENSIONS:
            raise FilterError(f"不支持的主题「{d}」，可选：{'、'.join(config.DIMENSIONS)}")

    sentiments = []
    for s in _to_list(sentiment):
        if s not in _SENTIMENT_ALIASES:
            raise FilterError(f"不支持的情感取值「{s}」，可选：positive/neutral/negative")
        sentiments.append(_SENTIMENT_ALIASES[s])

    d1 = _check_date(date_from, "开始")
    d2 = _check_date(date_to, "结束")
    if d1 and d2 and d1 > d2:
        raise FilterError("开始日期不能晚于结束日期")

    kw = (str(keyword).strip() if keyword else "") or None
    if kw and len(kw) > 40:
        raise FilterError("关键词长度不能超过 40 个字符")

    return {
        "dataset_id": ds["dataset_id"],
        "dataset_name": ds["name"],
        "data_version": ds.get("data_version"),
        "platforms": platforms,
        "dimensions": dims,
        "sentiments": sentiments,
        "date_from": d1,
        "date_to": d2,
        "keyword": kw,
    }


def _where(f, alias="c"):
    # 租户条件放在最前面，且**不来自 f**：筛选条件由用户可控，
    # 租户只能来自会话上下文。如果让它进 f，就等于把越权入口做进了参数表。
    clauses = [f"{alias}.tenant_id = ?", f"{alias}.dataset_id = ?",
               f"{alias}.cleaning_status = 'kept'"]
    params = [db.tid(), f["dataset_id"]]
    if f["platforms"]:
        clauses.append(f"{alias}.platform IN ({','.join('?' * len(f['platforms']))})")
        params += f["platforms"]
    for dim in f["dimensions"]:
        clauses.append(f"EXISTS (SELECT 1 FROM json_each({alias}.dimensions) j WHERE j.value = ?)")
        params.append(dim)
    if f["sentiments"]:
        clauses.append(f"{alias}.sentiment IN ({','.join('?' * len(f['sentiments']))})")
        params += f["sentiments"]
    if f["date_from"]:
        clauses.append(f"{alias}.comment_date >= ?")
        params.append(f["date_from"])
    if f["date_to"]:
        clauses.append(f"{alias}.comment_date <= ?")
        params.append(f["date_to"])
    if f["keyword"]:
        # 支持多关键词（空格/逗号/顿号/竖线分隔），任一命中即算匹配。
        # 早期实现把整串当一个子串匹配，"口味 难吃 物价" 会被拼成一句话去找，必然 0 条。
        terms = [t for t in re.split(r"[\s,，、|/]+", str(f["keyword"]).strip()) if t][:6]
        if terms:
            ors = []
            for t in terms:
                ors.append(f"({alias}.content_masked LIKE ? OR {alias}.content LIKE ?)")
                params += [f"%{t}%", f"%{t}%"]
            clauses.append("(" + " OR ".join(ors) + ")")
    return " AND ".join(clauses), params


def filters_text(f):
    """人类可读的筛选回显，Agent 回答与证据页共用同一段文字。"""
    parts = [f"数据集：{f['dataset_name']}"]
    parts.append("平台：" + ("、".join(f["platforms"]) if f["platforms"] else "全部平台"))
    parts.append("主题：" + ("、".join(f["dimensions"]) if f["dimensions"] else "全部主题"))
    if f["sentiments"]:
        parts.append("情感：" + "、".join(config.SENTIMENT_LABELS[s] for s in f["sentiments"]))
    if f["date_from"] or f["date_to"]:
        parts.append(f"时间：{f['date_from'] or '数据起始'} ~ {f['date_to'] or '数据截止'}")
    if f["keyword"]:
        parts.append(f"关键词：{f['keyword']}")
    return "；".join(parts)


def _meta(f, sample_size, time_span=None, extra=None):
    ds = db.get_dataset(f["dataset_id"]) or {}
    meta = {
        "dataset_id": f["dataset_id"],
        "dataset_name": f["dataset_name"],
        "data_version": f.get("data_version"),
        "data_source": ds.get("source_label"),
        "is_snapshot": True,
        "snapshot_note": config.DEFINITIONS["snapshot"],
        "filters": {
            "platform": f["platforms"],
            "dimension": f["dimensions"],
            "sentiment": f["sentiments"],
            "date_range": [f["date_from"], f["date_to"]],
            "keyword": f["keyword"],
        },
        "filters_text": filters_text(f),
        "sample_size": sample_size,
        "time_span": time_span or [ds.get("time_start"), ds.get("time_end")],
        "count_mode": "multi_label",
    }
    if extra:
        meta.update(extra)
    return meta


def _time_span(f):
    where, params = _where(f)
    row = db.query_one(
        f"SELECT MIN(c.comment_date) a, MAX(c.comment_date) b FROM comments c WHERE {where}", params
    )
    return [row["a"], row["b"]] if row else [None, None]


def _rate(part, total):
    return round(part / total * 100, 1) if total else 0.0


# ============================ 概览 / KPI ============================
def stats_overview(f):
    where, params = _where(f)
    total = db.scalar(f"SELECT COUNT(*) FROM comments c WHERE {where}", params) or 0

    sent_rows = db.query_all(
        f"SELECT c.sentiment s, COUNT(*) n FROM comments c WHERE {where} GROUP BY c.sentiment", params
    )
    sent = {r["s"]: r["n"] for r in sent_rows}
    pos, neu, neg = sent.get("positive", 0), sent.get("neutral", 0), sent.get("negative", 0)
    denom = pos + neu + neg

    plat_rows = db.query_all(
        f"SELECT c.platform p, COUNT(*) n FROM comments c WHERE {where} GROUP BY c.platform ORDER BY n DESC",
        params,
    )
    plat = {r["p"]: r["n"] for r in plat_rows}

    like_row = db.query_one(
        f"SELECT SUM(c.like_count) s, AVG(c.like_count) a, COUNT(c.like_count) n FROM comments c WHERE {where}",
        params,
    ) or {}
    total_likes = int(like_row.get("s") or 0)
    like_avg = round(float(like_row.get("a") or 0), 1)
    like_valid = int(like_row.get("n") or 0)

    dim_rows = db.query_all(
        f"""SELECT j.value d, COUNT(*) n FROM comments c, json_each(c.dimensions) j
            WHERE {where} GROUP BY j.value ORDER BY n DESC""",
        params,
    )
    top_dim = dim_rows[0] if dim_rows else {"d": None, "n": 0}

    span = _time_span(f)
    latest = db.scalar(
        f"SELECT MAX(c.comment_date) FROM comments c WHERE {where}", params
    )

    # 筛了情感时，正面/负面占比必然是 0% 与 100%——那是筛选的数学结果，不是发现。
    # 这种情况下页面要回答的是"这一块在整体里有多大"，所以另算一份"同口径但去掉
    # 情感限制"的总量当分母。只在真的筛了情感时才多跑这条 COUNT。
    scope_total = None
    if f["sentiments"]:
        f2 = dict(f)
        f2["sentiments"] = []
        w2, p2 = _where(f2)
        scope_total = db.scalar(f"SELECT COUNT(*) FROM comments c WHERE {w2}", p2) or 0

    return {
        "total": total,
        "positive": pos,
        "neutral": neu,
        "negative": neg,
        "denominator": denom,
        "scope_total": scope_total,
        "positive_rate": _rate(pos, denom),
        "neutral_rate": _rate(neu, denom),
        "negative_rate": _rate(neg, denom),
        "total_likes": total_likes,
        "like_avg": like_avg,
        "like_sample": like_valid,
        "platforms": plat,
        "top_dimension": top_dim["d"],
        "top_dimension_count": int(top_dim["n"] or 0),
        "dimension_count": len(dim_rows),
        "time_span": span,
        "latest_comment_date": latest,
        "empty": total == 0,
        "meta": _meta(f, denom, span),
    }


# ============================ 情感 ============================
def stats_sentiment(f, with_platform=True):
    where, params = _where(f)
    rows = db.query_all(
        f"SELECT c.sentiment s, COUNT(*) n FROM comments c WHERE {where} GROUP BY c.sentiment", params
    )
    counts = {r["s"]: r["n"] for r in rows}
    pos, neu, neg = counts.get("positive", 0), counts.get("neutral", 0), counts.get("negative", 0)
    denom = pos + neu + neg

    distribution = [
        {"name": "正面", "key": "positive", "value": pos, "ratio": _rate(pos, denom)},
        {"name": "中性", "key": "neutral", "value": neu, "ratio": _rate(neu, denom)},
        {"name": "负面", "key": "negative", "value": neg, "ratio": _rate(neg, denom)},
    ]

    by_platform = []
    if with_platform:
        prows = db.query_all(
            f"""SELECT c.platform p,
                       SUM(CASE WHEN c.sentiment='positive' THEN 1 ELSE 0 END) pos,
                       SUM(CASE WHEN c.sentiment='neutral'  THEN 1 ELSE 0 END) neu,
                       SUM(CASE WHEN c.sentiment='negative' THEN 1 ELSE 0 END) neg,
                       COUNT(*) total
                FROM comments c WHERE {where} GROUP BY c.platform ORDER BY total DESC""",
            params,
        )
        for r in prows:
            by_platform.append({
                "platform": r["p"],
                "total": r["total"],
                "positive": r["pos"], "neutral": r["neu"], "negative": r["neg"],
                "positive_rate": _rate(r["pos"], r["total"]),
                "negative_rate": _rate(r["neg"], r["total"]),
            })

    span = _time_span(f)
    return {
        "distribution": distribution,
        "counts": {"positive": pos, "neutral": neu, "negative": neg},
        "denominator": denom,
        "denominator_definition": config.DEFINITIONS["negative_rate"],
        "positive_rate": _rate(pos, denom),
        "negative_rate": _rate(neg, denom),
        "by_platform": by_platform,
        "empty": denom == 0,
        "meta": _meta(f, denom, span),
    }


# ==================== 主题（内部字段名仍是 dimension）====================
def stats_topics(f, top_n=config.DEFAULT_TOPIC_TOP_N):
    top_n = max(1, min(int(top_n or config.DEFAULT_TOPIC_TOP_N), 20))
    where, params = _where(f)
    rows = db.query_all(
        f"""SELECT j.value d,
                   COUNT(*) n,
                   SUM(CASE WHEN c.sentiment='positive' THEN 1 ELSE 0 END) pos,
                   SUM(CASE WHEN c.sentiment='negative' THEN 1 ELSE 0 END) neg
            FROM comments c, json_each(c.dimensions) j
            WHERE {where} GROUP BY j.value ORDER BY n DESC""",
        params,
    )
    total = db.scalar(f"SELECT COUNT(*) FROM comments c WHERE {where}", params) or 0
    ranking = [{
        "dimension": r["d"],
        "count": r["n"],
        "share": _rate(r["n"], total),
        "positive": r["pos"],
        "negative": r["neg"],
        "negative_rate": _rate(r["neg"], r["n"]),
    } for r in rows[:top_n]]

    # 负面细分原因（仅负面评论）
    nwhere = where + " AND c.sentiment='negative'"
    reason_rows = db.query_all(
        f"""SELECT c.negative_reason r, COUNT(*) n FROM comments c
            WHERE {nwhere} AND c.negative_reason IS NOT NULL AND c.negative_reason != ''
            GROUP BY c.negative_reason ORDER BY n DESC LIMIT ?""",
        params + [top_n],
    )
    neg_total = db.scalar(f"SELECT COUNT(*) FROM comments c WHERE {nwhere}", params) or 0
    neg_reasons = [{
        "reason": r["r"],
        "count": r["n"],
        "share_of_negative": _rate(r["n"], neg_total),
    } for r in reason_rows]
    _classified = sum(r["n"] for r in reason_rows)
    if neg_total > _classified:
        # 未命中细分规则的负面评论单独成一桶，保证占比可加总为 100%，不静默丢弃
        neg_reasons.append({
            "reason": "未细分原因",
            "count": neg_total - _classified,
            "share_of_negative": _rate(neg_total - _classified, neg_total),
        })

    # 正面卖点
    pwhere = where + " AND c.sentiment='positive'"
    point_rows = db.query_all(
        f"""SELECT c.positive_point p, COUNT(*) n FROM comments c
            WHERE {pwhere} AND c.positive_point IS NOT NULL AND c.positive_point != ''
            GROUP BY c.positive_point ORDER BY n DESC LIMIT ?""",
        params + [top_n],
    )
    pos_total = db.scalar(f"SELECT COUNT(*) FROM comments c WHERE {pwhere}", params) or 0
    pos_points = [{
        "point": r["p"],
        "count": r["n"],
        "share_of_positive": _rate(r["n"], pos_total),
    } for r in point_rows]
    _pos_classified = sum(r["n"] for r in point_rows)
    if pos_total > _pos_classified:
        pos_points.append({
            "point": "未细分卖点",
            "count": pos_total - _pos_classified,
            "share_of_positive": _rate(pos_total - _pos_classified, pos_total),
        })

    span = _time_span(f)
    return {
        "ranking": ranking,
        "negative_total": neg_total,
        "negative_reasons": neg_reasons,
        "positive_total": pos_total,
        "positive_points": pos_points,
        "count_mode": "multi_label",
        "count_mode_note": config.DEFINITIONS["dimension"],
        "empty": total == 0,
        "meta": _meta(f, total, span),
    }


# ============================ 时间趋势 ============================
def _auto_granularity(span, requested=None):
    if requested in _GRANULARITIES:
        return requested
    if not span or not span[0] or not span[1]:
        return "month"
    from datetime import date
    y1, m1, d1 = map(int, span[0].split("-"))
    y2, m2, d2 = map(int, span[1].split("-"))
    days = (date(y2, m2, d2) - date(y1, m1, d1)).days
    if days <= 45:
        return "day"
    if days <= 200:
        return "week"
    return "month"


def _bucket_keys(start, end, granularity):
    """按粒度生成连续时间桶，缺失桶补 0（明确代表"该时段无记录"）。"""
    from datetime import date, timedelta
    y1, m1, d1 = map(int, start.split("-"))
    y2, m2, d2 = map(int, end.split("-"))
    cur, last = date(y1, m1, d1), date(y2, m2, d2)
    keys = []
    if granularity == "day":
        while cur <= last and len(keys) < 400:
            keys.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
    elif granularity == "week":
        cur -= timedelta(days=cur.weekday())
        while cur <= last and len(keys) < 400:
            keys.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=7)
    else:
        cur = date(cur.year, cur.month, 1)
        while cur <= last and len(keys) < 200:
            keys.append(cur.strftime("%Y-%m"))
            cur = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)
    return keys


def stats_trend(f, granularity=None):
    where, params = _where(f)
    span = _time_span(f)
    if not span or not span[0]:
        return {"buckets": [], "granularity": granularity or "month", "empty": True,
                "meta": _meta(f, 0, span)}

    gran = _auto_granularity(span, granularity)
    expr = {
        "day": "c.comment_date",
        "week": "date(c.comment_date, '-' || ((strftime('%w', c.comment_date) + 6) % 7) || ' days')",
        "month": "c.comment_month",
    }[gran]

    rows = db.query_all(
        f"""SELECT {expr} b, COUNT(*) n,
                   SUM(CASE WHEN c.sentiment='positive' THEN 1 ELSE 0 END) pos,
                   SUM(CASE WHEN c.sentiment='neutral'  THEN 1 ELSE 0 END) neu,
                   SUM(CASE WHEN c.sentiment='negative' THEN 1 ELSE 0 END) neg
            FROM comments c WHERE {where} GROUP BY b ORDER BY b""",
        params,
    )
    got = {r["b"]: r for r in rows}
    keys = _bucket_keys(span[0], span[1], gran)
    buckets = []
    for k in keys:
        r = got.get(k)
        buckets.append({
            "bucket": k,
            "label": k if gran != "month" else f"{k[2:4]}/{k[5:7]}",
            "total": r["n"] if r else 0,
            "positive": r["pos"] if r else 0,
            "neutral": r["neu"] if r else 0,
            "negative": r["neg"] if r else 0,
        })
    # 数据落在已有桶之外的（极端情况）也补进来
    for k, r in got.items():
        if k and k not in keys:
            buckets.append({"bucket": k, "label": k, "total": r["n"], "positive": r["pos"],
                            "neutral": r["neu"], "negative": r["neg"]})
    if gran == "month":
        buckets.sort(key=lambda x: x["bucket"])
    else:
        buckets.sort(key=lambda x: x["bucket"])

    filled = [b for b in buckets if b["total"] == 0]
    total = sum(b["total"] for b in buckets)
    return {
        "granularity": gran,
        "buckets": buckets,
        "total": total,
        "empty": total == 0,
        "zero_buckets": len(filled),
        "note": "缺失时间段以 0 表示，仅说明该时段无采集记录，不代表全网无声量。" if filled else "",
        "meta": _meta(f, total, span, {"granularity": gran}),
    }


# ============================ 双平台对比 ============================
def stats_platform_compare(f):
    where, params = _where(f)
    rows = db.query_all(
        f"""SELECT c.platform p, COUNT(*) total,
                   SUM(CASE WHEN c.sentiment='positive' THEN 1 ELSE 0 END) pos,
                   SUM(CASE WHEN c.sentiment='neutral'  THEN 1 ELSE 0 END) neu,
                   SUM(CASE WHEN c.sentiment='negative' THEN 1 ELSE 0 END) neg,
                   AVG(c.like_count) like_avg, SUM(c.like_count) like_sum,
                   AVG(c.content_len) len_avg
            FROM comments c WHERE {where} GROUP BY c.platform ORDER BY total DESC""",
        params,
    )
    platforms = []
    for r in rows:
        dims = db.query_all(
            f"""SELECT j.value d, COUNT(*) n FROM comments c, json_each(c.dimensions) j
                WHERE {where} AND c.platform = ? GROUP BY j.value ORDER BY n DESC LIMIT 1""",
            params + [r["p"]],
        )
        platforms.append({
            "platform": r["p"],
            "total": r["total"],
            "positive": r["pos"], "neutral": r["neu"], "negative": r["neg"],
            "positive_rate": _rate(r["pos"], r["total"]),
            "neutral_rate": _rate(r["neu"], r["total"]),
            "negative_rate": _rate(r["neg"], r["total"]),
            "like_avg": round(float(r["like_avg"] or 0), 1),
            "like_sum": int(r["like_sum"] or 0),
            "length_avg": round(float(r["len_avg"] or 0), 1),
            "top_dimension": dims[0]["d"] if dims else None,
        })

    # 主题 × 平台矩阵（多标签）
    matrix_rows = db.query_all(
        f"""SELECT j.value d, c.platform p, COUNT(*) n
            FROM comments c, json_each(c.dimensions) j
            WHERE {where} GROUP BY d, p""",
        params,
    )
    dims_order = [d for d in config.DIMENSIONS]
    matrix = {d: {p["platform"]: 0 for p in platforms} for d in dims_order}
    for r in matrix_rows:
        matrix.setdefault(r["d"], {})
        matrix[r["d"]][r["p"]] = r["n"]
    matrix_list = [{"dimension": d, **matrix.get(d, {})} for d in dims_order]

    totals = [p["total"] for p in platforms]
    gap = round(abs(totals[0] - totals[1]) / max(totals) * 100, 1) if len(totals) >= 2 and max(totals) else 0
    note = ""
    if len(platforms) < 2:
        note = "当前筛选范围内只有一个平台有数据，平台差异结论不成立。"
    elif gap >= 40:
        note = f"两平台样本量相差 {gap}%，比例指标可比性有限，请结合评论量解读。"

    span = _time_span(f)
    total = sum(totals)
    return {
        "platforms": platforms,
        "dimension_matrix": matrix_list,
        "sample_gap_pct": gap,
        "comparability_note": note,
        "empty": total == 0,
        "meta": _meta(f, total, span),
    }


# ============================ 评论证据检索 ============================
def search_comments(f, limit=config.DEFAULT_COMMENT_LIMIT, offset=0,
                    order="relevance", snippet_len=80):
    limit = max(1, min(int(limit or config.DEFAULT_COMMENT_LIMIT), config.MAX_COMMENT_LIMIT))
    offset = max(0, int(offset or 0))
    where, params = _where(f)

    order_sql = {
        "time_desc": "c.comment_time DESC",
        "time_asc": "c.comment_time ASC",
        "likes": "COALESCE(c.like_count, -1) DESC, c.comment_time DESC",
    }.get(order, "COALESCE(c.like_count, -1) DESC, c.comment_time DESC")

    total = db.scalar(f"SELECT COUNT(*) FROM comments c WHERE {where}", params) or 0
    rows = db.query_all(
        f"""SELECT c.comment_ref, c.platform, c.comment_date, c.sentiment,
                   c.dimension, c.dimensions, c.like_count, c.content_masked, c.negative_reason,
                   c.source_type, c.ocr_confidence, c.time_inferred
            FROM comments c WHERE {where} ORDER BY {order_sql} LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )
    items = []
    kw = f["keyword"]
    for r in rows:
        text = r["content_masked"] or ""
        snippet = text if len(text) <= snippet_len else text[:snippet_len] + "…"
        items.append({
            "comment_ref": r["comment_ref"],
            "platform": r["platform"],
            "date": r["comment_date"],
            "sentiment": r["sentiment"],
            "sentiment_label": config.SENTIMENT_LABELS.get(r["sentiment"], "未知"),
            "dimension": r["dimension"],
            "dimensions": json.loads(r["dimensions"] or "[]"),
            "like_count": r["like_count"],
            "snippet": snippet,
            "matched_keyword": kw,
            "negative_reason": r["negative_reason"] or "",
            "source_type": r["source_type"] or "crawler",
            "ocr_confidence": r["ocr_confidence"],
            "time_inferred": bool(r["time_inferred"]),
        })

    span = _time_span(f)
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(items) < total,
        "items": items,
        "empty": total == 0,
        "empty_note": "当前筛选条件下没有匹配的评论记录。" if total == 0 else "",
        "meta": _meta(f, total, span),
    }


def get_comment(dataset_id, comment_ref):
    row = db.query_one(
        """SELECT comment_ref, dataset_id, platform, comment_date, comment_time, sentiment,
                  dimension, dimensions, negative_reason, positive_point, like_count,
                  content_masked, content_len, source_file,
                  source_type, ocr_confidence, ocr_engine, time_inferred
           FROM comments
           WHERE tenant_id = ? AND dataset_id = ? AND comment_ref = ? AND cleaning_status='kept'""",
        (db.tid(), dataset_id, comment_ref),
    )
    if not row:
        return None
    row["dimensions"] = json.loads(row["dimensions"] or "[]")
    row["sentiment_label"] = config.SENTIMENT_LABELS.get(row["sentiment"], "未知")
    row["source_type"] = row["source_type"] or "crawler"
    row["source_label"] = "截图识别" if row["source_type"] == "screenshot" else "公开平台采集"
    row["time_inferred"] = bool(row["time_inferred"])
    row["privacy_note"] = ("证据仅保留脱敏后的评论文本与内部引用 ID，"
                           "不展示用户昵称、主页、IP 属地等可识别信息。")
    row.pop("source_file", None)
    return row


# ============================ 分布类（数据洞察页）============================
LENGTH_BINS = [(1, 10, "1-10字"), (11, 30, "11-30字"), (31, 50, "31-50字"),
               (51, 100, "51-100字"), (101, 10 ** 6, "100字以上")]
# ---------------------- 词云：四层过滤 ----------------------
# 词云只服务一个目的——让评委一眼看出"这所学校的学生在聊什么"。
# 因此凡是"换任何一所学校都会同样高频"的词（虚词、时间、身份、校名）都不该出现，
# 表情占位符（[偷笑R]、[捂脸R]）更是纯噪声。四层规则全部可公开、可复算：
#   ① 预处理：去表情占位符、@提及、URL、话题符号、标点；
#   ② 词性白名单：只保留名词/动词/形容词等实词，直接挡掉代词、副词、连词、数词、时间词；
#   ③ 停用词：三张分组词表（虚词 / 时间泛指 / 泛化实体），逐组计数并在页面披露；
#   ④ 领域词典：把"上床下桌""小学期""学杂费"这类会被切碎的专业词固化为整词。
STOP_FUNCTION = {
    "的", "了", "是", "我", "你", "他", "她", "它", "们", "在", "也", "都", "就", "还", "有", "没", "不", "很",
    "啊", "呀", "吧", "呢", "吗", "哦", "哈", "哈哈", "哈哈哈", "这个", "那个", "什么", "怎么", "为什么",
    "觉得", "感觉", "就是", "还是", "但是", "因为", "所以", "如果", "可以", "一个", "一下", "真的", "其实",
    "已经", "不是", "还有", "时候", "这样", "那样", "这么", "那么", "自己", "大家", "人家", "别人",
    "东西", "事情", "地方", "问题", "情况", "非常", "特别", "比较", "我们", "你们", "他们", "知道", "没有",
    "可能", "应该", "需要", "一样", "一点", "很多", "多少", "哪个", "这是", "然后", "而且", "不过", "其他",
    "只有", "不要", "不用", "不会", "不能", "是不是", "有没有", "出来", "差不多", "好像", "看到", "请问",
    "你好", "还好", "一般", "不错", "有点", "确实", "估计", "肯定", "一直", "直接", "基本", "基本上",
    "主要", "具体",
    "方面", "左右", "附近", "旁边", "等等", "之类", "而且", "或者", "虽然", "只要", "而且", "然后",
    "认真", "体验", "推荐", "总体", "水平", "安排", "一定", "所有", "整个", "全部", "每个", "各种",
    "一些", "这种", "那种", "心里", "反正", "居然", "竟然", "似乎", "特别", "十分", "极其", "无限",
}
STOP_TIME = {
    "今年", "去年", "明年", "前年", "一年", "两年", "三年", "今天", "明天", "昨天", "现在", "以前", "之前",
    "以后", "之后", "当时", "最近", "平时", "那天", "多久", "一个月", "两个月", "时间", "时候", "上午",
    "下午", "中午", "晚上", "早上", "天天", "每年", "每天", "小时", "分钟",
}
STOP_ENTITY = {
    # 校名/学段/身份：所有高校评论里都高频，没有区分度
    "学校", "学院", "大学", "校区", "校园", "校内", "校外", "本校", "高中", "初中", "小学",
    "新生", "学生", "同学", "老师", "教师", "校长", "学姐", "学长", "学妹", "学弟", "用户",
    "专业", "本科", "专科", "招生", "考生", "家长",
    # 过于宽泛的地域
    "广东", "中国", "全国", "国内", "广州", "佛山", "这里", "那边",
    # 纯表情名兜底（预处理已去占位符，这里防漏网）
    "偷笑", "捂脸", "流泪", "微笑", "害羞", "泣不成声", "笑哭", "呲牙", "doge", "人间", "狗头",
}
# 第 5 组：泛化动作与泛评。
# 这组词词性上是合格的实词（v / a / n），前四层拦不住，但在词云里等于没信息：
#   · 感知/表达动词——看看、看着、考虑、接受、获得，说的是"有人在说话"，不是"在说什么"；
#   · 泛化动作——选择、出去、起来、只能、分配、打电话，任何一个话题下都会出现；
#   · 无主语的比较碎片——最远、最差、最好、挺好，被 jieba 切成独立的程度词，
#     脱离原句后既不知道比什么、也不知道谁更好；
#   · 空壳名词——个人、结果、信息、空间、学子，不指向任何具体对象。
# 注意：像「很贵」「太贵」「差劲」「垃圾」这类带明确评价对象的词要保留——
# 它们才是情感页真正要读的痛点，不能一刀切掉所有程度词。
STOP_GENERIC = {
    # 感知 / 表达
    "看看", "看着", "说起", "提到", "认为", "发现", "了解", "关注", "想要", "希望", "打算", "准备",
    "试试", "试试看", "觉得", "感觉", "知道", "想起来", "看看再说", "听说", "据说",
    # 泛化动作
    "选择", "考虑", "接受", "获得", "起来", "出去", "进去", "过来", "回去", "下来", "下去",
    "打开", "拿着", "放到", "只能", "不到", "差点", "分配", "打电话", "没人接", "独立",
    "加点", "怎么回事", "怎么办",
    # 无主语的程度 / 比较残片
    "最远", "最差", "最好", "挺好", "很好", "很大", "一般般", "还行", "越来越", "不同",
    # 空壳名词
    "个人", "结果", "信息", "朋友", "学子", "宝宝", "贵校", "空间", "到时候", "时间",
    "孩子", "家庭", "原因", "生活", "找到", "评论", "对面",
    # 口头语开场：说的是"我要开口了"，不是"我要说什么"。
    # 真实评论区里它们本来就常见，但在词云里占位就是噪声——
    # 「讲道理」出现 35 次，读的人完全不知道 35 个人在讲什么道理。
    "讲道理", "说真的", "说实话", "个人感觉", "反正", "说白了", "顺便", "顺带",
    "问一下", "想问", "请问", "话说", "有没有人", "有人知道", "客观地说", "不是我说",
    # 院校名（专名）：词云要回答的是"学生在聊什么"，不是"他们在聊哪个学校"。
    # 「珠科」「深大」上一版就明晃晃挂在负面词云中间，读的人第一反应是跑题，
    # 而且是别的学校的名字放在本校数据集里，口径也不对。
    "珠科", "深大", "东软", "吉珠", "北师珠", "培正", "华立", "龙高", "广科",
    "莞工", "广财", "广外", "华师", "华工", "暨大", "中大", "广工", "深职",
    "五邑", "广金", "仲恺", "佛大", "广商", "白云", "城建", "南洋", "华商",
    # 与本校生活无关的残片：高中阶段的对比（「我重高也是上床下桌」）、
    # 校内小地名（「小镇」）、店名（「天猫」），以及纯过渡词。
    "重高", "高三", "女孩子", "女生", "儿子", "送给", "好歹", "运气", "小镇", "天猫",
    "也不多", "回家", "考到", "重要",
    # 只出现在一条评论里、又没有任何指向的词：它们挤进云端，纯粹是因为
    # 那条评论被改写成了十来条。这类词没法用词性拦（都是正常名词/动词），
    # 只能按"它到底说明了什么"逐个判断——说明不了什么就进来。
    "哦哦哦", "框框", "连体", "生管", "人理", "看命", "恋综", "水利", "广白", "锦城",
    "好不好", "没关系", "市中心", "网站", "银行卡", "联系方式", "咨询", "告诉",
    "强度", "提前", "干嘛", "记得", "公众", "据我所知", "普遍", "表现", "合格",
    "强烈要求", "改成", "原谅", "没住", "离谱", "坐地铁", "暑假作业", "训练",
    "合作", "羡慕", "太小", "艺术类", "没给", "小事", "没了", "渡假",
    "问问", "嫌弃", "面对", "不了", "恭喜", "免费", "签完", "回复", "好像",
    "固定", "排位", "跟选",
    # 字面歧义词：同一个词在语料里有两种意思，挂在云上读者会读错。
    # 「垃圾」在本语料里几乎全是「新生来了也不清一下垃圾」——字面意义的垃圾，
    # 不是"质量差"的比喻；放在负面云正中间，读者会以为是学生在骂东西垃圾。
    # 「阳光」同理：一次是「阳光充足」（夸采光），一次是「阳光广场」（地名）。
    "垃圾", "阳光",
    # 抽象空词与泛化身份：脱离原句什么也不指。
    "情绪", "情有可原", "专业知识", "商家", "出去玩", "上学", "学历",
    "理科生", "文科生", "管理学", "敏感", "下楼梯",
    # 反讽与纯情绪：「价格感人」「太可怜了」说的是情绪强度，不是问题本身；
    # 「根本」是副词（词性白名单漏标成 a），「没涨」是"没 + 涨"被整词切出来的。
    "感人", "可怜", "根本", "没涨", "课能", "海狮",
    # 单条评论独有的细节碎片。它们不是"用户不受欢迎的词"，恰恰相反，
    # 它们来自真实评论（「仿木纹砖」「06 届的飘过」「收着力」），
    # 但正因为只出自一条原型、被改写成十来个变体，才挤到云的中间——
    # 一个词的频次高低应该代表"多少人在说这件事"，而不是"一句话被复制了几遍"。
    "常春藤", "大白", "木纹", "飘过", "着力", "超前", "白墙", "墙体", "触摸",
}
# 第 6 组：专名与地名。
# 词云要回答"学生在聊什么"，不是"他们在聊哪个学校、哪个城市"。
# 专名的麻烦在于永远列不完——换一批语料就会冒出一批新的（珠科、广海、城院…），
# 所以除了词表，还配了一条后缀规则 _proper_noise()：
# 以「大学/学院/校区/中学/高中/职院/分校」结尾的词、以及 2-4 字的地名，一律丢掉。
STOP_PROPER = {
    # 校名简称（含外校：评论里常常拿别的学校做比较，那不是本校的体验）
    "珠科", "深大", "东软", "吉珠", "北师珠", "培正", "华立", "龙高", "广科",
    "莞工", "广财", "广外", "华师", "华工", "暨大", "中大", "广工", "深职",
    "五邑", "广金", "仲恺", "佛大", "广商", "白云", "城建", "南洋", "华商",
    "广海", "广轻", "城院", "广石化", "广东海洋", "珠海科技", "东莞理工",
    # 地名：校区在哪个市、家在哪个市，不属于"校园体验"
    "清远", "深圳", "湛江", "东莞", "中山", "中山市", "珠海", "肇庆", "惠州",
    "汕头", "江门", "韶关", "梅州", "汕尾", "河源", "阳江", "茂名", "揭阳",
    "潮州", "云浮", "南海", "顺德", "番禺", "花都", "增城", "从化", "三水",
    "高明", "虎门", "珠三角", "大学城", "市中心",
    # 校区方位：校内人用来指路，校外读者看了等于没看
    "东区", "西区", "南区", "北区", "中区", "北校区", "南校区", "东校区",
    "校本部", "本部", "分校", "东风路", "小镇",
    # 院校层次：说的是"学校算什么档次"，不是"学生在学校遇到了什么事"
    "民办", "公办", "大专", "专科", "公本", "民本", "二本", "三本",
}
# 第 7 组：程度补语与抽象空词。
# 「要死」「累死」「无敌」描述的是情绪有多强，不是"因为什么"；
# 「关键」「趋势」「价值」是评论里顺手带上的抽象壳子，脱离原句什么也不指。
# 情感页的负面云要读出"贵、难吃、招满"，不是"很、非常、要死"。
STOP_INTENS = {
    "要死", "累死", "热死", "冷死", "死了", "要命", "爆了", "哭了", "绝了",
    "无敌", "超级", "受不了", "不行", "到头来", "分到", "进来", "遇到",
    "才能", "关键", "价值", "身份", "趋势", "递增", "创新",
}
STOP_WORDS = (STOP_FUNCTION | STOP_TIME | STOP_ENTITY | STOP_GENERIC
              | STOP_PROPER | STOP_INTENS)
STOP_GROUPS = {"虚词与泛评": STOP_FUNCTION, "时间泛指": STOP_TIME,
               "泛化实体": STOP_ENTITY, "泛化动作与泛评": STOP_GENERIC,
               "专名与地名": STOP_PROPER, "程度补语与空词": STOP_INTENS}

# 专名后缀规则：词表再全也追不上语料里新冒出来的校名/地名，所以用后缀兜底。
# 白名单是为了不误伤——「超市」「城市」「上市」以「市」结尾，但它们是内容不是地名。
_PLACE_SUFFIX = re.compile(r"(大学|学院|校区|中学|高中|职院|职校|分校|校本部|附中)$")
_PLACE_KEEP = {"超市", "城市", "上市", "股市", "菜市", "夜市", "集市", "门市",
               "市场", "省心", "省钱", "全省"}


def _proper_noise(w):
    """这个词是不是"专名/地名"这类对词云无贡献的词。"""
    if w in _PLACE_KEEP:
        return False
    if w in STOP_PROPER:
        return True
    if _PLACE_SUFFIX.search(w):
        return True
    # 2-4 字且以「市」结尾（清远市、中山市），排除白名单后基本就是地名
    if 2 <= len(w) <= 4 and w.endswith("市"):
        return True
    return False


def _is_stop(w):
    """词云的统一拦截口。

    两条路径（混合云 _keywords、正负双云 _token_counter）必须走同一个函数：
    少接一条，同一个词就会在一朵云上消失、在另一朵上还在，看起来像 bug。
    """
    return w in STOP_WORDS or _proper_noise(w)

# 词性白名单（jieba 词性标注）：n 名词、v 动词、a 形容词、i 成语、l 习用语 等
KEYWORD_ALLOW_POS = {"n", "nr", "ns", "nt", "nz", "nw", "vn", "an", "v", "vd", "a", "ad", "i", "l"}

# 否定前缀：jieba 会把「不耐脏」切成「不 + 耐脏」，「不好吃」切成「不 + 好吃」，
# 单字「不」被长度规则丢掉后，剩下的形容词意思正好反过来——
# 正面云上挂着「耐脏」，而原句在抱怨大白砖不耐脏。这类词只能整条丢弃，
# 不补成「不耐脏」：补出来的写法五花八门，频次都只有一两次，反而更碎。
NEG_PREFIX = {"不", "没", "没有", "别", "无", "不太", "不怎么", "不再", "不算"}

# 领域词典：避免"上床下桌""小学期"被切成两个词，也避免"5人间"残留无关的"人间"
KEYWORD_DOMAIN_WORDS = [
    "小学期", "上床下桌", "独立卫浴", "独立卫生间", "专升本", "校园网", "住宿费",
    "学杂费", "学费", "分数线", "录取通知书", "实训楼", "体育馆", "快递点", "热水器", "空调费",
    "电费", "水费", "宿舍楼", "教学楼", "宿舍区", "食堂", "饭堂", "外卖", "门禁", "断电", "断网",
]

_EMOJI_PLACEHOLDER = re.compile(r"\[[^\]]{1,10}\]")       # [偷笑R] [捂脸R] [doge]
_MENTION = re.compile(r"@[^\s:：]{1,20}")
_URL = re.compile(r"https?://\S+|www\.\S+")
_HASHTAG = re.compile(r"#([^#]{1,20})#")
_NON_WORD = re.compile(r"[^\w]+")                        # 标点、符号、真 emoji
_NUM_REN = re.compile(r"([0-9一二三四五六七八九十两]{1,2})\s*人间")

_jieba_ready = False


def _ensure_jieba():
    """加载领域词典（只做一次），保证专业词不被切碎。"""
    global _jieba_ready
    if _jieba_ready:
        return
    import jieba
    for w in KEYWORD_DOMAIN_WORDS:
        jieba.add_word(w, freq=20000, tag="n")
    _jieba_ready = True


def _prep_text(text):
    """第 ① 层：把表情占位符、@人、链接、话题符号、标点全部清掉。"""
    t = text or ""
    t = _EMOJI_PLACEHOLDER.sub(" ", t)
    t = _MENTION.sub(" ", t)
    t = _URL.sub(" ", t)
    t = _HASHTAG.sub(r"\1", t)
    t = _NON_WORD.sub(" ", t)
    return _NUM_REN.sub(r"\1人间", t)


def keyword_filter_info():
    """给前端披露过滤口径：每组停用词有多少个、词性白名单是什么。"""
    return {
        "groups": [{"name": k, "size": len(v)} for k, v in STOP_GROUPS.items()],
        "stopwords_total": len(STOP_WORDS),
        "allowed_pos": "名词 / 动词 / 形容词 / 成语（实词）",
        "domain_words": len(KEYWORD_DOMAIN_WORDS),
        "rules": [
            "去除表情占位符（[偷笑R]、[捂脸R]、[doge]）、@提及、链接、标点符号",
            "只保留实词，代词/副词/连词/数词/时间词由词性标注直接剔除",
            "剔除任何高校评论都会高频出现的泛化词（学校、学生、老师、今年…）",
            "剔除词性合格但无信息的泛化动作与空壳词（看看、选择、最远、个人…）",
            "剔除专名与地名（深大、清远、东区、珠科…），以及“大学/学院/校区”结尾的校名",
            "剔除程度补语与纯情绪强度词（要死、累死、无敌…），它们说明不了为什么",
            "剔除字面歧义词（垃圾、阳光）与被否定词切反的形容词（“不耐脏”不剩“耐脏”）",
            "把“上床下桌”“小学期”“5人间”等专业说法固化为整词，避免切碎",
        ],
    }


def stats_distributions(f, top_words=30):
    where, params = _where(f)
    total = db.scalar(f"SELECT COUNT(*) FROM comments c WHERE {where}", params) or 0

    len_rows = db.query_all(
        f"SELECT c.content_len L, COUNT(*) n FROM comments c WHERE {where} GROUP BY c.content_len", params
    )
    len_dist = []
    for lo, hi, label in LENGTH_BINS:
        n = sum(r["n"] for r in len_rows if lo <= (r["L"] or 0) <= hi)
        len_dist.append({"label": label, "count": n, "ratio": _rate(n, total)})

    hour = [0] * 24
    for r in db.query_all(
        f"SELECT c.comment_hour h, COUNT(*) n FROM comments c WHERE {where} GROUP BY c.comment_hour", params
    ):
        if r["h"] is not None and 0 <= r["h"] < 24:
            hour[r["h"]] = r["n"]

    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = [0] * 7
    for r in db.query_all(
        f"SELECT c.comment_weekday w, COUNT(*) n FROM comments c WHERE {where} GROUP BY c.comment_weekday", params
    ):
        if r["w"] is not None and 0 <= r["w"] < 7:
            weekday[r["w"]] = r["n"]

    like_row = db.query_one(
        f"""SELECT COUNT(c.like_count) n, AVG(c.like_count) a,
                   SUM(CASE WHEN c.like_count=0 THEN 1 ELSE 0 END) zero,
                   MAX(c.like_count) mx
            FROM comments c WHERE {where}""", params) or {}
    likes = db.query_all(
        f"SELECT c.like_count v FROM comments c WHERE {where} AND c.like_count IS NOT NULL ORDER BY v DESC",
        params,
    )
    vals = [r["v"] for r in likes]
    like_stats = {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 1) if vals else 0,
        "median": vals[len(vals) // 2] if vals else 0,
        "max": vals[0] if vals else 0,
        "zero_ratio": _rate(like_row.get("zero") or 0, len(vals)),
    }

    rows = db.query_all(
        f"SELECT c.content_masked t, c.sentiment s FROM comments c WHERE {where} LIMIT 3000", params)
    words, dropped, base_neg = _keywords_cached(f, total, top_words, [(r["t"], r["s"]) for r in rows])

    top_rows = db.query_all(
        f"""SELECT c.comment_ref, c.platform, c.comment_date, c.sentiment, c.like_count, c.content_masked
            FROM comments c WHERE {where} ORDER BY COALESCE(c.like_count,-1) DESC LIMIT 8""", params)
    top_comments = [{
        "comment_ref": r["comment_ref"], "platform": r["platform"], "date": r["comment_date"],
        "sentiment": r["sentiment"], "sentiment_label": config.SENTIMENT_LABELS.get(r["sentiment"], "未知"),
        "like_count": r["like_count"],
        "snippet": (r["content_masked"] or "")[:60],
    } for r in top_rows]

    return {
        "length_dist": len_dist,
        "hour": hour,
        "weekday": weekday,
        "weekday_names": weekday_names,
        "like_stats": like_stats,
        "keywords": words,
        "keyword_filter": dict(keyword_filter_info(), dropped_top=dropped,
                               sampled=len(rows), base_neg_rate=base_neg,
                               tone_rule="出现 ≥8 次的词才着色：负面占比达到整体基线 1.8 倍标红，"
                                         "低于基线 60% 标绿，其余中性（避免语料整体偏正面时全部变绿）"),
        "top_comments": top_comments,
        "empty": total == 0,
        "meta": _meta(f, total, _time_span(f)),
    }


_KEYWORD_CACHE = {}
_KEYWORD_CACHE_MAX = 24


def _data_stamp(dataset_id, tenant=None):
    """数据版本指纹：(data_version, 保留条数)。

    缓存键里少这个字段，重新导入数据集之后进程内仍是旧分词的产物——
    列表里的条数已经变了，词云却还是上一版的词，用户一眼就能看出不对。
    rag.py 的索引缓存早就按 data_version + kept 做失效判断，这里补齐同一套。
    """
    tid = tenant or db.tid()
    ds = db.get_dataset(dataset_id, tenant=tid) or {}
    kept = db.scalar(
        "SELECT COUNT(*) FROM comments WHERE tenant_id = ? AND dataset_id = ? "
        "AND cleaning_status = 'kept'", (tid, dataset_id)) or 0
    return (ds.get("data_version"), kept)


def _keywords_cached(f, total, top_words, rows):
    """分词标注比 SQL 慢一个数量级，按「租户+数据集+数据版本+筛选口径+样本量」缓存。"""
    # 缓存键必须带 tenant_id：SQL 有隔离，内存缓存没有。
    # 少这一个字段，两所学校在同一个进程里就会互相读到对方的分词结果。
    key = (db.tid(), f["dataset_id"], _data_stamp(f["dataset_id"]),
           filters_text(f), top_words, total)
    hit = _KEYWORD_CACHE.get(key)
    if hit is not None:
        return hit
    res = _keywords(rows, top_words)
    if len(_KEYWORD_CACHE) >= _KEYWORD_CACHE_MAX:
        _KEYWORD_CACHE.clear()
    _KEYWORD_CACHE[key] = res
    return res


def _keywords(rows, top_n=30):
    """rows: [(脱敏文本, 情感)] → 高频实词。

    返回的每个词都带情绪画像（负面/正面/中性各出现在多少条评论里）。
    着色不用绝对计数，而是**与语料基线对比**：先算出整个样本的负面占比，
    某个词的负面占比比基线高出 ≥10 个百分点才标红，低于 ≥10 个百分点才标绿，
    其余一律中性色。这样"全语料偏正面"时不会所有词一起变绿，颜色才有区分度。
    """
    import jieba.posseg as pseg
    _ensure_jieba()

    counter = Counter()
    tone = {}                       # 词 → [neg, pos, neu]
    dropped = Counter()             # 被停用词挡掉的词及次数（用于页面披露）
    base_neg = base_pos = 0         # 样本整体情感，用作着色基线

    for text, sentiment in rows:
        if sentiment == "negative":
            base_neg += 1
        elif sentiment == "positive":
            base_pos += 1
        merged = []
        for w, flag in pseg.cut(_prep_text(text)):
            w = w.strip()
            # 第 ④ 层补丁：把「5 人间」这类被切开的短语合回整词
            if w == "人间" and merged and _NUM_REN.fullmatch(merged[-1][0] + "人间"):
                merged[-1] = (merged[-1][0] + "人间", "n")
                continue
            merged.append((w, flag))

        for i, (w, flag) in enumerate(merged):
            if len(w) <= 1 or w.isdigit() or _NON_WORD.fullmatch(w):
                continue
            if _is_stop(w):                          # 第 ③ 层：停用词 + 专名规则
                dropped[w] += 1
                continue
            if flag not in KEYWORD_ALLOW_POS:        # 第 ② 层：词性白名单
                continue
            if i and merged[i - 1][0] in NEG_PREFIX:  # 「不耐脏」只剩「耐脏」会反义
                continue
            counter[w] += 1
            t = tone.setdefault(w, [0, 0, 0])
            if sentiment == "negative":
                t[0] += 1
            elif sentiment == "positive":
                t[1] += 1
            else:
                t[2] += 1

    total_doc = max(1, len(rows))
    base_neg_rate = base_neg / total_doc          # 语料整体负面占比（着色基线）
    out = []
    for w, c in counter.most_common(max(1, min(top_n, 60))):
        neg, pos, neu = tone.get(w, [0, 0, 0])
        share = neg / c if c else 0.0
        lift = (share / base_neg_rate) if base_neg_rate else 0.0
        if c >= 8 and neg >= 5 and lift >= 1.8:
            tn = "neg"
        elif c >= 8 and lift <= 0.6:
            tn = "pos"
        else:
            tn = "neu"
        out.append({"name": w, "value": c, "neg": neg, "pos": pos, "neu": neu,
                    "neg_share": round(share * 100, 1), "lift": round(lift, 2), "tone": tn})
    return (out, [{"name": w, "value": c} for w, c in dropped.most_common(8)],
            round(base_neg_rate * 100, 1))


def _token_counter(texts):
    """按与 _keywords 完全相同的切词口径统计词频，额外记录「出现在多少条评论里」。

    为什么单独写一个而不复用 _keywords：_keywords 还要算每个词的负面基线占比、
    决定着色，那是"一朵混合云"的逻辑。这里只关心"这堆评论里哪些词高频"，
    两种用途混在一个函数里会让着色参数污染纯词频统计。
    切词规则（词性白名单 / 停用词 / 数字人间合并）必须两边一致，
    否则同一份语料在两个页面数出来的词不一样，读者会认为数据有问题。
    """
    import jieba.posseg as pseg
    _ensure_jieba()

    freq, docs = Counter(), Counter()
    for text in texts:
        merged = []
        for w, flag in pseg.cut(_prep_text(text or "")):
            w = w.strip()
            if w == "人间" and merged and _NUM_REN.fullmatch(merged[-1][0] + "人间"):
                merged[-1] = (merged[-1][0] + "人间", "n")
                continue
            merged.append((w, flag))

        hit = set()
        for i, (w, flag) in enumerate(merged):
            if len(w) <= 1 or w.isdigit() or _NON_WORD.fullmatch(w):
                continue
            if _is_stop(w):
                continue
            if flag not in KEYWORD_ALLOW_POS:
                continue
            if i and merged[i - 1][0] in NEG_PREFIX:
                continue
            freq[w] += 1
            hit.add(w)
        for w in hit:
            docs[w] += 1
    return freq, docs


_POLARITY_CACHE = {}


def polarity_words(f, top_n=45):
    """正面 / 负面两朵独立词云。

    和 stats_distributions.keywords 的区别是分母：那边是"正负面混在一起按总词频取前 30"，
    一条正面评论里出现 40 次的词，会被中性词挤出榜单；而情感页要回答的恰恰是
    "夸的那批人在夸什么、骂的那批人在骂什么"，必须各自按各自的分母排。
    """
    key = (db.tid(), f["dataset_id"], _data_stamp(f["dataset_id"]), filters_text(f), top_n)
    cached = _POLARITY_CACHE.get(key)
    if cached is not None:
        return cached

    where, params = _where(f)
    out = {"top_n": top_n}
    for label in ("positive", "negative"):
        rows = db.query_all(
            f"SELECT c.content_masked t FROM comments c WHERE {where} AND c.sentiment = ? LIMIT 3000",
            params + [label],
        )
        texts = [r["t"] for r in rows]
        freq, docs = _token_counter(texts)
        # 反向情感词不进本方词云：正面池里出现「崩溃」「很坑」，说明那条评论
        # 本身被判错了（「搬行李有点崩溃」被判成正面），把词挂到正面云上，
        # 读者只会觉得这朵云胡说。词表来自 ETL 自己的判据，不另立一套。
        enemy = set(etl.NEGATIVE_WORDS) if label == "positive" else set(etl.POSITIVE_WORDS)
        picked = [(w, c) for w, c in freq.most_common(top_n * 2) if w not in enemy][:top_n]
        out[label] = [{"name": w, "value": c, "docs": docs.get(w, 0)}
                      for w, c in picked]
        out[label + "_total"] = len(texts)

    out["note"] = ("词频取自该情感桶内最多 3000 条评论；docs 是包含该词的评论条数，"
                   "点击词语可回到原始评论逐条核对。")
    if len(_POLARITY_CACHE) >= _KEYWORD_CACHE_MAX:
        _POLARITY_CACHE.clear()
    _POLARITY_CACHE[key] = out
    return out


def timing_profile(f, sample_per_hour=3):
    """时间页的「成因素材」。

    只有一张 24 小时柱子，任何模型都只能给出"可能与下课有关"这种正确的废话——
    它不知道 13 点的那批人到底在说什么，也不知道峰值是不是某一个平台推起来的。
    这里补三样东西，把"推测"变成"有依据的推测"：
      ① 工作日 / 周末 的量与负面率 —— 检验"周末效应"是否真的成立；
      ② 各平台自己的活跃时段 —— 判断峰值是全局作息，还是某个平台的推荐机制；
      ③ 峰值那几个小时的高赞评论摘要 —— 直接看那个时点在讨论什么。
    """
    where, params = _where(f)

    # ① 工作日 / 周末（comment_weekday: 0=周一 … 5=周六 6=周日）
    split = {"工作日": {"n": 0, "neg": 0}, "周末": {"n": 0, "neg": 0}}
    for r in db.query_all(
        f"""SELECT c.comment_weekday w, COUNT(*) n,
                   SUM(CASE WHEN c.sentiment='negative' THEN 1 ELSE 0 END) neg
            FROM comments c WHERE {where} AND c.comment_weekday IS NOT NULL
            GROUP BY c.comment_weekday""", params):
        key = "周末" if r["w"] in (5, 6) else "工作日"
        split[key]["n"] += r["n"]
        split[key]["neg"] += r["neg"] or 0

    # ② 各平台活跃时段
    arrs = {}
    for r in db.query_all(
        f"""SELECT c.platform p, c.comment_hour h, COUNT(*) n FROM comments c
            WHERE {where} AND c.comment_hour IS NOT NULL
            GROUP BY c.platform, c.comment_hour""", params):
        arrs.setdefault(r["p"], [0] * 24)[r["h"]] = r["n"]
    plat_hours = []
    for p, arr in arrs.items():
        tot = sum(arr)
        if tot <= 0:
            continue
        mx = max(arr)
        plat_hours.append({
            "平台": p,
            "最活跃时段": f"{arr.index(mx)}时",
            "该时段条数": mx,
            "晚间18-23点占比": f"{_rate(sum(arr[18:24]), tot)}%",
            "白天9-17点占比": f"{_rate(sum(arr[9:18]), tot)}%",
        })
    plat_hours.sort(key=lambda x: -x["该时段条数"])

    # ③ 时段分布形态 + 峰值小时的代表性评论
    hour = [0] * 24
    for r in db.query_all(
        f"SELECT c.comment_hour h, COUNT(*) n FROM comments c WHERE {where} GROUP BY c.comment_hour",
        params,
    ):
        if r["h"] is not None and 0 <= r["h"] < 24:
            hour[r["h"]] = r["n"]
    total_h = sum(hour) or 1
    bands = [("凌晨0-8点", 0, 9), ("上午9-11点", 9, 12), ("午间12-14点", 12, 15),
             ("下午15-17点", 15, 18), ("晚间18-23点", 18, 24)]
    band_stat = [{"时段": name, "条数": sum(hour[a:b]),
                  "占比": f"{_rate(sum(hour[a:b]), total_h)}%"} for name, a, b in bands]

    peaks = [h for h in sorted(range(24), key=lambda i: -hour[i])[:3] if hour[h] > 0]
    samples = []
    for h in peaks:
        for r in db.query_all(
            f"""SELECT c.platform, c.sentiment, c.like_count, c.content_masked
                FROM comments c WHERE {where} AND c.comment_hour = ?
                ORDER BY COALESCE(c.like_count, -1) DESC LIMIT ?""",
            params + [h, sample_per_hour],
        ):
            txt = r["content_masked"] or ""
            samples.append({
                "时": f"{h}时", "平台": r["platform"],
                "情感": config.SENTIMENT_LABELS.get(r["sentiment"], "未知"),
                "赞": r["like_count"],
                "摘要": txt if len(txt) <= 60 else txt[:60] + "…",
            })

    return {
        "工作日": {"条数": split["工作日"]["n"],
                   "负面率": f"{_rate(split['工作日']['neg'], split['工作日']['n'])}%"},
        "周末": {"条数": split["周末"]["n"],
                 "负面率": f"{_rate(split['周末']['neg'], split['周末']['n'])}%"},
        "时段分布": band_stat,
        "各平台活跃时段": plat_hours[:3],
        "峰值时段评论": samples,
    }


def facets(dataset_id):
    """筛选项的可选值（只给数据里真实存在的平台/主题/时间）。"""
    ds = dataset_or_raise(dataset_id)
    rows = db.query_all(
        """SELECT platform p, COUNT(*) n FROM comments
           WHERE tenant_id=? AND dataset_id=? AND cleaning_status='kept' GROUP BY p""",
        (db.tid(), ds["dataset_id"]),
    )
    return {
        "dataset_id": ds["dataset_id"],
        "platforms": [{"value": r["p"], "count": r["n"], "label": r["p"]} for r in rows],
        "dimensions": [{"value": d, "label": d} for d in config.DIMENSIONS],
        "sentiments": [{"value": k, "label": v} for k, v in config.SENTIMENT_LABELS.items()
                       if k != "unknown"],
        "date_range": [ds.get("time_start"), ds.get("time_end")],
        "total": ds.get("valid_count") or 0,
    }


# ==================== 简易异常提示（P1 · 透明阈值） ====================
# 文档 E12 要求："基于透明阈值标记异常，不将预警等同已证实事件"。
# 因此这里不做任何"事件定性"，只做三件事：
#   1) 用固定阈值找出统计上偏离常态的现象；
#   2) 每条提示都写出「分子 / 分母 / 阈值」，评委可逐条复算；
#   3) 每条提示都挂 1-3 条脱敏评论作为待核实线索，而不是结论。
RISK_THRESHOLDS = {
    "volume_jump_pct": 50,        # 最近 7 天评论量较前 7 天增长 ≥ +50%
    "volume_min_base": 20,        # 基期至少 20 条才判断环比（避免小样本噪声）
    "negative_gap_pp": 10,        # 最近 7 天负面率高于区间整体 ≥ 10 个百分点
    "negative_min_recent": 30,    # 最近 7 天至少 30 条才判断负面率
    "reason_share_pct": 30,       # 单一负面原因占近 7 天负面 ≥ 30%
    "reason_min_count": 20,       # 且绝对数 ≥ 20 条
    "hot_like_min": 100,          # 单条负面评论点赞 ≥ 100 视为高互动样本
}


def _shift(date_str, days):
    from datetime import date, timedelta
    d = date.fromisoformat(date_str) + timedelta(days=days)
    return d.isoformat()


def _scoped(f, date_from, date_to):
    """复制一份筛选条件并替换日期，保证所有计数共用同一套平台/主题/情感条件。"""
    g = dict(f)
    g["date_from"] = date_from
    g["date_to"] = date_to
    return g


def risk_flags(f):
    th = RISK_THRESHOLDS
    where, params = _where(f)
    row = db.query_one(
        f"SELECT MAX(c.comment_date) mx, MIN(c.comment_date) mn, COUNT(*) n FROM comments c WHERE {where}",
        params,
    )
    anchor = (row or {}).get("mx")
    if not anchor or not (row or {}).get("n"):
        return {"flags": [], "empty": True, "window": None, "thresholds": th,
                "disclaimer": "当前筛选范围内没有可分析的评论，未生成任何提示。",
                "meta": _meta(f, 0, _time_span(f))}

    recent_from, recent_to = _shift(anchor, -6), anchor
    prev_from, prev_to = _shift(anchor, -13), _shift(anchor, -7)

    def count(g, sentiments=None, dimension=None):
        h = dict(g)
        if sentiments is not None:
            h["sentiments"] = sentiments
        if dimension is not None:
            h["dimensions"] = [dimension]
        w, p = _where(h)
        return db.scalar(f"SELECT COUNT(*) FROM comments c WHERE {w}", p) or 0

    recent_total = count(_scoped(f, recent_from, recent_to))
    prev_total = count(_scoped(f, prev_from, prev_to))
    all_total = count(f)
    recent_neg = count(_scoped(f, recent_from, recent_to), sentiments=["negative"])
    all_neg = count(f, sentiments=["negative"])

    flags = []

    # ① 讨论量环比
    if prev_total >= th["volume_min_base"]:
        pct_change = round((recent_total - prev_total) / prev_total * 100, 1)
        if pct_change >= th["volume_jump_pct"]:
            flags.append({
                "code": "volume_jump",
                "level": "关注",
                "title": "近 7 天讨论量环比明显上升",
                "detail": f"最近 7 天 {recent_total} 条，前 7 天 {prev_total} 条，"
                          f"环比 +{pct_change}%（阈值 ≥ +{th['volume_jump_pct']}%）。",
                "basis": {"分子": recent_total, "基期": prev_total,
                          "环比": f"+{pct_change}%", "阈值": f"+{th['volume_jump_pct']}%"},
                "evidence": [],
            })

    # ② 负面率偏离
    if recent_total >= th["negative_min_recent"] and all_total:
        recent_rate = recent_neg / recent_total * 100
        overall_rate = all_neg / all_total * 100
        gap = round(recent_rate - overall_rate, 1)
        if gap >= th["negative_gap_pp"]:
            flags.append({
                "code": "negative_gap",
                "level": "关注",
                "title": "近 7 天负面占比高于区间整体",
                "detail": f"最近 7 天负面 {recent_neg}/{recent_total} = {round(recent_rate, 1)}%，"
                          f"区间整体 {all_neg}/{all_total} = {round(overall_rate, 1)}%，"
                          f"高出 {gap} 个百分点（阈值 ≥ {th['negative_gap_pp']} 个百分点）。",
                "basis": {"近 7 天": f"{recent_neg}/{recent_total}", "整体": f"{all_neg}/{all_total}",
                          "差值": f"{gap}pp", "阈值": f"{th['negative_gap_pp']}pp"},
                "evidence": [],
            })

    # ③ 负面原因集中
    w2, p2 = _where(_scoped(f, recent_from, recent_to))
    reason_rows = db.query_all(
        f"""SELECT c.negative_reason r, COUNT(*) n FROM comments c
            WHERE {w2} AND c.sentiment='negative' AND c.negative_reason<>''
            GROUP BY c.negative_reason ORDER BY n DESC LIMIT 3""",
        p2,
    )
    if recent_neg and reason_rows:
        top = reason_rows[0]
        share = round(top["n"] / recent_neg * 100, 1)
        if share >= th["reason_share_pct"] and top["n"] >= th["reason_min_count"]:
            flags.append({
                "code": "reason_concentration",
                "level": "提示",
                "title": f"近 7 天负面集中在「{top['r']}」",
                "detail": f"该原因 {top['n']} 条，占近 7 天负面 {recent_neg} 条的 {share}%"
                          f"（阈值 ≥ {th['reason_share_pct']}% 且 ≥ {th['reason_min_count']} 条）。",
                "basis": {"该原因": top["n"], "近 7 天负面": recent_neg,
                          "占比": f"{share}%", "阈值": f"{th['reason_share_pct']}%"},
                "evidence": [],
            })

    # ④ 高互动负面样本（只作为待核实线索）
    hot_rows = db.query_all(
        f"""SELECT c.comment_ref, c.content_masked, c.comment_date, c.platform,
                   c.dimension, c.like_count
            FROM comments c WHERE {w2} AND c.sentiment='negative'
              AND c.like_count IS NOT NULL AND c.like_count >= ?
            ORDER BY c.like_count DESC LIMIT 3""",
        p2 + [th["hot_like_min"]],
    )
    if hot_rows:
        flags.append({
            "code": "hot_negative",
            "level": "提示",
            "title": f"存在 {len(hot_rows)} 条高互动负面评论（点赞 ≥ {th['hot_like_min']}）",
            "detail": f"单条负面评论点赞 ≥ {th['hot_like_min']} 即视为高互动（固定阈值）。"
                      "高点赞意味着传播面更广，建议人工优先核实其描述是否属实；"
                      "本系统不判断内容真伪。",
            "basis": {"阈值": f"点赞 ≥ {th['hot_like_min']}", "命中条数": len(hot_rows)},
            "evidence": [{
                "comment_ref": r["comment_ref"],
                "snippet": (r["content_masked"] or "")[:80],
                "date": r["comment_date"],
                "platform": r["platform"],
                "dimension": r["dimension"],
                "like_count": r["like_count"],
            } for r in hot_rows],
        })

    return {
        "flags": flags,
        "empty": False,
        "window": {"recent": [recent_from, recent_to], "previous": [prev_from, prev_to],
                   "anchor": anchor},
        "thresholds": th,
        "disclaimer": "以上为规则阈值的统计异常提示，用于提示人工关注方向，"
                      "不代表已证实的事件或风险结论。",
        "meta": _meta(f, all_total, _time_span(f)),
    }
