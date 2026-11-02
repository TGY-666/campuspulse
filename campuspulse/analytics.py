# -*- coding: utf-8 -*-
"""统一统计层 —— 全系统唯一的数据口径。

驾驶舱、数据洞察、热点证据、Agent 工具、综合报告全部调用这里的函数，
保证"同一个筛选条件在三个地方算出同一个数"（文档 10.3 / 15.2 的要求）。

约定：
  * 分母永远显式返回（denominator 字段），前端不再自己算比例；
  * 维度按多标签统计（一条评论可命中多个维度），并随结果返回 count_mode；
  * 缺失点赞返回 NULL，不当作 0；
  * 空结果返回 counts=0 + empty=True，不抛异常、不补造数据。
"""
import json
import re
from collections import Counter

from . import config, db

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
            raise FilterError(f"不支持的维度「{d}」，可选：{'、'.join(config.DIMENSIONS)}")

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
    parts.append("维度：" + ("、".join(f["dimensions"]) if f["dimensions"] else "全部维度"))
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
    return {
        "total": total,
        "positive": pos,
        "neutral": neu,
        "negative": neg,
        "denominator": denom,
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


# ============================ 主题 / 维度 ============================
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

    # 维度 × 平台矩阵（多标签）
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
                   c.dimension, c.dimensions, c.like_count, c.content_masked, c.negative_reason
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
                  content_masked, content_len, source_file
           FROM comments
           WHERE tenant_id = ? AND dataset_id = ? AND comment_ref = ? AND cleaning_status='kept'""",
        (db.tid(), dataset_id, comment_ref),
    )
    if not row:
        return None
    row["dimensions"] = json.loads(row["dimensions"] or "[]")
    row["sentiment_label"] = config.SENTIMENT_LABELS.get(row["sentiment"], "未知")
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
    "你好", "还好", "一般", "不错", "有点", "确实", "估计", "肯定", "一直", "直接", "基本", "主要", "具体",
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
STOP_WORDS = STOP_FUNCTION | STOP_TIME | STOP_ENTITY
STOP_GROUPS = {"虚词与泛评": STOP_FUNCTION, "时间泛指": STOP_TIME, "泛化实体": STOP_ENTITY}

# 词性白名单（jieba 词性标注）：n 名词、v 动词、a 形容词、i 成语、l 习用语 等
KEYWORD_ALLOW_POS = {"n", "nr", "ns", "nt", "nz", "nw", "vn", "an", "v", "vd", "a", "ad", "i", "l"}

# 领域词典：避免"上床下桌""小学期"被切成两个词，也避免"5人间"残留无关的"人间"
KEYWORD_DOMAIN_WORDS = [
    "小学期", "上床下桌", "独立卫浴", "独立卫生间", "东软小镇", "专升本", "校园网", "住宿费",
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


def _keywords_cached(f, total, top_words, rows):
    """分词标注比 SQL 慢一个数量级，按「租户+数据集+筛选口径+样本量」缓存，重复访问直接命中。"""
    # 缓存键必须带 tenant_id：SQL 有隔离，内存缓存没有。
    # 少这一个字段，两所学校在同一个进程里就会互相读到对方的分词结果。
    key = (db.tid(), f["dataset_id"], filters_text(f), top_words, total)
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

        for w, flag in merged:
            if len(w) <= 1 or w.isdigit() or _NON_WORD.fullmatch(w):
                continue
            if w in STOP_WORDS:                      # 第 ③ 层：停用词
                dropped[w] += 1
                continue
            if flag not in KEYWORD_ALLOW_POS:        # 第 ② 层：词性白名单
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


def facets(dataset_id):
    """筛选项的可选值（只给数据里真实存在的平台/维度/时间）。"""
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
    """复制一份筛选条件并替换日期，保证所有计数共用同一套平台/维度/情感条件。"""
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
