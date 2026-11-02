# -*- coding: utf-8 -*-
"""确定性图表构建器。

关键约束（文档 A08 / A12）：图表里的每个数字都来自工具返回的真实统计结果，
大模型只负责写文字解释，不参与生成任何 categories / series 数值。
这样即使模型输出跑偏，图也不会画错。
"""

MAX_CHARTS = 3


def _sentiment_chart(data):
    dist = data.get("distribution") or []
    return {
        "type": "pie",
        "title": "情感分布",
        "unit": "条",
        "categories": [d["name"] for d in dist],
        "series": [{"name": "评论量", "data": [{"name": d["name"], "value": d["value"]} for d in dist]}],
        "caption": f"分母：{data.get('denominator', 0)} 条有效评论",
    }


def _platform_sentiment_chart(data):
    rows = [p for p in (data.get("by_platform") or []) if p["total"] > 0]
    if len(rows) < 2:
        return None
    return {
        "type": "bar",
        "title": "分平台情感占比",
        "unit": "%",
        "categories": [r["platform"] for r in rows],
        "series": [
            {"name": "正面占比", "data": [r["positive_rate"] for r in rows]},
            {"name": "负面占比", "data": [r["negative_rate"] for r in rows]},
        ],
        "caption": "占比分母为该平台评论量",
    }


def _topic_chart(data):
    ranking = (data.get("ranking") or [])[:8]
    if not ranking:
        return None
    return {
        "type": "bar",
        "title": "七大维度评论量排行",
        "unit": "条",
        "horizontal": True,
        "categories": [r["dimension"] for r in ranking][::-1],
        "series": [{"name": "评论量", "data": [r["count"] for r in ranking][::-1]}],
        "caption": "多标签计数：一条评论可命中多个维度",
    }


def _negative_reason_chart(data):
    reasons = (data.get("negative_reasons") or [])[:6]
    if not reasons:
        return None
    return {
        "type": "bar",
        "title": "负面原因分布",
        "unit": "条",
        "categories": [r["reason"] for r in reasons],
        "series": [{"name": "提及数", "data": [r["count"] for r in reasons]}],
        "caption": f"负面评论共 {data.get('negative_total', 0)} 条",
    }


def _trend_chart(data):
    buckets = data.get("buckets") or []
    if not buckets:
        return None
    gname = {"day": "日", "week": "周", "month": "月"}.get(data.get("granularity"), "")
    return {
        "type": "line",
        "title": f"评论量与负面趋势（按{gname}）",
        "unit": "条",
        "categories": [b["label"] for b in buckets],
        "series": [
            {"name": "评论总量", "data": [b["total"] for b in buckets]},
            {"name": "负面评论", "data": [b["negative"] for b in buckets]},
        ],
        "caption": data.get("note") or "趋势按评论发布时间聚合",
    }


def _platform_compare_chart(data):
    rows = data.get("platforms") or []
    if len(rows) < 2:
        return None
    return {
        "type": "bar",
        "title": "双平台评论量与负面量",
        "unit": "条",
        "categories": [r["platform"] for r in rows],
        "series": [
            {"name": "评论量", "data": [r["total"] for r in rows]},
            {"name": "负面评论", "data": [r["negative"] for r in rows]},
        ],
        "caption": data.get("comparability_note") or "同一筛选口径下的同指标比较",
    }


_BUILDERS = {
    "get_sentiment_stats": [_sentiment_chart, _platform_sentiment_chart],
    "get_topic_stats": [_topic_chart, _negative_reason_chart],
    "get_time_trend": [_trend_chart],
    "compare_platforms": [_platform_compare_chart],
    "search_comments": [],
}


def build_charts(tool_results, max_charts=MAX_CHARTS):
    charts, seen = [], set()
    for res in tool_results:
        if not res.get("ok") or res.get("empty"):
            continue
        for builder in _BUILDERS.get(res["tool"], []):
            try:
                chart = builder(res.get("data") or {})
            except Exception:
                chart = None
            if not chart:
                continue
            key = (chart["type"], chart["title"])
            if key in seen:
                continue
            seen.add(key)
            charts.append(chart)
            if len(charts) >= max_charts:
                return charts
    return charts


def merge_metrics(tool_results, limit=8):
    out, seen = [], set()
    for res in tool_results:
        if not res.get("ok"):
            continue
        for m in res.get("metrics") or []:
            if m["name"] in seen:
                continue
            seen.add(m["name"])
            out.append(m)
            if len(out) >= limit:
                return out
    return out


def collect_evidence(tool_results, limit=8):
    out, seen = [], set()
    for res in tool_results:
        for ev in res.get("evidence") or []:
            ref = ev.get("comment_ref")
            if not ref or ref in seen:
                continue
            seen.add(ref)
            out.append(ev)
            if len(out) >= limit:
                return out
    return out
