# -*- coding: utf-8 -*-
"""受控分析工具层 —— Agent 唯一能碰到的数据入口。

设计要点（对应文档 5.3 / X08）：
  1. 每个工具的参数都有显式 JSON Schema，先校验再执行，绝不把模型给的字符串拼进 SQL；
  2. 只能调用白名单里的 6 个函数，不能执行任意 SQL / Python / 系统命令；
  3. 每次调用都返回"参数回显 + 分母 + 数据版本"，让回答可复核；
  4. 失败返回结构化错误（ok=False + reason），由 Agent 转成用户能读懂的提示。

第 6 个工具 retrieve_context 是 RAG 检索（campuspulse/rag.py）：
统计工具回答"多少条"，它回答"原文怎么说"，返回的 comment_ref 就是回答里
[CMT-xxxxxxxx] 引用锚点的唯一合法来源。
"""
import json
import re
import time

from . import analytics, config, rag

# ---------------- 参数片段 ----------------
_DATASET = {"type": "string", "description": "数据集 ID，例如 ds_hist_all；不确定时省略，使用当前选中的数据集"}
_PLATFORM = {
    "type": "array", "items": {"type": "string", "enum": config.PLATFORMS},
    "description": "平台筛选，可多选；不传表示两个平台都算",
}
_DIMENSION = {
    "type": "array", "items": {"type": "string", "enum": config.DIMENSIONS},
    "description": "校园业务维度筛选，可多选",
}
_SENTIMENT = {
    "type": "array", "items": {"type": "string", "enum": ["positive", "neutral", "negative"]},
    "description": "情感筛选：positive 正面 / neutral 中性 / negative 负面",
}
_DATE_FROM = {"type": "string", "description": "开始日期 YYYY-MM-DD，按评论发布时间筛选"}
_DATE_TO = {"type": "string", "description": "结束日期 YYYY-MM-DD"}

TOOL_SPECS = [
    {
        "name": "get_sentiment_stats",
        "description": "统计指定范围内的正面/中性/负面评论数量、占比与分母，可按平台拆分。"
                       "适用于“负面占比多少”“两平台情感差异”一类问题。",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": _DATASET, "platform": _PLATFORM, "dimension": _DIMENSION,
                "date_from": _DATE_FROM, "date_to": _DATE_TO,
            },
        },
    },
    {
        "name": "get_topic_stats",
        "description": "统计七大校园维度（宿舍住宿/食堂餐饮/招生报考/教学学业/校园环境/交通出行/校园活动）"
                       "的评论量排行，以及负面原因和正面卖点排行。适用于“学生最关注什么”“最常见负面问题”。",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": _DATASET, "platform": _PLATFORM, "dimension": _DIMENSION,
                "sentiment": _SENTIMENT, "date_from": _DATE_FROM, "date_to": _DATE_TO,
                "top_n": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回前 N 项，默认 10"},
            },
        },
    },
    {
        "name": "get_time_trend",
        "description": "按日/周/月返回评论量与正负面的时间序列。适用于“什么时候讨论量上升”“负面趋势”。",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": _DATASET, "platform": _PLATFORM, "dimension": _DIMENSION,
                "sentiment": _SENTIMENT, "date_from": _DATE_FROM, "date_to": _DATE_TO,
                "granularity": {"type": "string", "enum": ["day", "week", "month"],
                                "description": "时间粒度，默认按月；跨度小于 45 天建议用 day"},
            },
        },
    },
    {
        "name": "compare_platforms",
        "description": "在相同筛选口径下比较抖音与小红书的评论量、情感比例、平均点赞和维度分布。",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": _DATASET, "dimension": _DIMENSION, "sentiment": _SENTIMENT,
                "date_from": _DATE_FROM, "date_to": _DATE_TO,
            },
        },
    },
    {
        "name": "search_comments",
        "description": "按关键词做字面检索，返回脱敏片段、内部引用 ID、平台、时间和情感。"
                       "适用于用户明确给出关键词（如“断电”“外卖”）时的精确查找。",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": _DATASET, "keywords": {"type": "string", "description": "检索关键词，可为空"},
                "platform": _PLATFORM, "dimension": _DIMENSION, "sentiment": _SENTIMENT,
                "date_from": _DATE_FROM, "date_to": _DATE_TO,
                "limit": {"type": "integer", "minimum": 1, "maximum": config.MAX_COMMENT_LIMIT,
                          "description": f"返回条数，最多 {config.MAX_COMMENT_LIMIT} 条，默认 8"},
            },
            "required": ["limit"],
        },
    },
    {
        "name": "retrieve_context",
        "description": "语义检索评论原文（RAG）：混合检索（BM25 + 同义词扩展 + 字符 n-gram 向量）"
                       "召回与问题最相关的片段，返回片段原文、内部引用 ID 与相关度分数。"
                       "适用于“学生具体是怎么吐槽的”“有什么原话”“说说细节”这类需要原文依据的问题；"
                       "需要精确数字统计时请改用统计类工具。返回的编号就是写引用 [CMT-xxxxxxxx] 的唯一来源。",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": _DATASET,
                "query": {"type": "string", "description": "检索语句，用自然语言描述想找的内容"},
                "platform": _PLATFORM, "dimension": _DIMENSION, "sentiment": _SENTIMENT,
                "date_from": _DATE_FROM, "date_to": _DATE_TO,
                "top_k": {"type": "integer", "minimum": 1, "maximum": 12,
                          "description": "返回片段数，默认 6"},
            },
            "required": ["query"],
        },
    },
]

TOOL_NAMES = [t["name"] for t in TOOL_SPECS]
_TOOL_MAP = {t["name"]: t for t in TOOL_SPECS}


class ToolError(Exception):
    def __init__(self, reason, detail=""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def openai_tools():
    """转换成 DeepSeek / OpenAI function calling 需要的格式。"""
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"], "parameters": t["parameters"],
    }} for t in TOOL_SPECS]


def _coerce_list(value, field):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        # 模型偶尔会把多选写成 "抖音,小红书"
        parts = [p.strip() for p in value.replace("，", ",").split(",") if p.strip()]
        return parts
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ToolError(f"{field} 需要数组或字符串", f"收到类型 {type(value).__name__}")


def _validate_args(name, raw_args):
    """按 Schema 做白名单校验，返回清洗后的参数。"""
    if name not in _TOOL_MAP:
        raise ToolError(f"工具不存在：{name}", "只允许调用：" + "、".join(TOOL_NAMES))
    if raw_args is None:
        raw_args = {}
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except Exception:
            raise ToolError("工具参数不是合法 JSON")
    if not isinstance(raw_args, dict):
        raise ToolError("工具参数必须是对象")

    spec_keys = set(_TOOL_MAP[name]["parameters"].get("properties", {}).keys())
    unknown = [k for k in raw_args if k not in spec_keys]
    if unknown:
        # 丢弃未知字段而不是报错，避免模型多写一个字段就整轮失败
        raw_args = {k: v for k, v in raw_args.items() if k in spec_keys}

    args = {}
    for key, value in raw_args.items():
        if value in (None, "", []):
            continue
        if key in ("platform", "dimension", "sentiment"):
            args[key] = _coerce_list(value, key)
        elif key in _INT_FIELDS:
            args[key] = _as_int(key, value)
        else:
            args[key] = value
    return args


# 数值型参数：Schema 里声明了 type=integer，这里必须真的执行，
# 否则对外部调用方（MCP 客户端 / 直接调 API）就是"声明了不校验"。
# 注意：越界不报错，交给下游 clamp 收敛（模型偶尔写 limit=9999 不该让整轮失败），
#       但"不是整数"必须报错——那说明调用方对我们的接口理解错了，沉默纠正比重试更有害。
_INT_FIELDS = {"limit", "top_n", "top_k"}


def _as_int(field, value):
    if isinstance(value, bool):                 # True 会变成 1，必须拦掉
        raise ToolError(f"{field} 需要整数", f"收到布尔值 {value}")
    if isinstance(value, int):
        out = value
    elif isinstance(value, float):
        if value != int(value):
            raise ToolError(f"{field} 需要整数", f"收到小数 {value}")
        out = int(value)
    elif isinstance(value, str):
        s = value.strip()
        m = re.match(r"^-?\d+", s)              # 容忍 "10条" "10 条" 这类带单位的写法
        if not m:
            raise ToolError(f"{field} 需要整数", f"收到 {value!r}，无法解析为整数")
        out = int(m.group(0))
    else:
        raise ToolError(f"{field} 需要整数", f"收到类型 {type(value).__name__}")
    return out


def _dataset_id(args, ctx):
    return args.get("dataset_id") or (ctx or {}).get("dataset_id") or config.DEFAULT_DATASET_ID


def _filters(name, args, ctx):
    search_kw = None
    if name == "search_comments":
        search_kw = args.get("keywords")
    elif name == "retrieve_context":
        search_kw = args.get("keyword")      # RAG 的 query 只用于打分，不拼进 SQL
    return analytics.build_filters(
        dataset_id=_dataset_id(args, ctx),
        platform=args.get("platform"),
        dimension=args.get("dimension"),
        sentiment=args.get("sentiment"),
        date_from=args.get("date_from"),
        date_to=args.get("date_to"),
        keyword=search_kw,
    )


# ---------------- 各工具执行 ----------------
def _run_get_sentiment_stats(args, ctx):
    f = _filters("get_sentiment_stats", args, ctx)
    data = analytics.stats_sentiment(f)
    if data["empty"]:
        return {"summary": f"在「{analytics.filters_text(f)}」范围内没有可用评论，无法给出情感比例。",
                "data": data, "metrics": [], "evidence": []}
    c = data["counts"]
    summary = (f"共 {data['denominator']} 条有效评论：正面 {c['positive']} 条"
               f"（{data['positive_rate']}%）、中性 {c['neutral']} 条、负面 {c['negative']} 条"
               f"（{data['negative_rate']}%），负面占比分母为 {data['denominator']} 条。")
    metrics = [
        {"name": "有效评论", "value": data["denominator"], "unit": "条"},
        {"name": "正面占比", "value": data["positive_rate"], "unit": "%"},
        {"name": "负面占比", "value": data["negative_rate"], "unit": "%"},
    ]
    return {"summary": summary, "data": data, "metrics": metrics, "evidence": []}


def _run_get_topic_stats(args, ctx):
    f = _filters("get_topic_stats", args, ctx)
    top_n = int(args.get("top_n") or config.DEFAULT_TOPIC_TOP_N)
    data = analytics.stats_topics(f, top_n)
    if data["empty"]:
        return {"summary": f"在「{analytics.filters_text(f)}」范围内没有可用评论，无法生成维度排行。",
                "data": data, "metrics": [], "evidence": []}
    ranking = data["ranking"]
    top = ranking[0] if ranking else None
    neg = data["negative_reasons"][0] if data["negative_reasons"] else None
    parts = []
    if top:
        parts.append(f"讨论最多的是「{top['dimension']}」，共 {top['count']} 条提及"
                     f"（占 {top['share']}%，多标签计数）")
    if neg:
        parts.append(f"负面原因中「{neg['reason']}」最多，出现 {neg['count']} 次，"
                     f"占负面评论的 {neg['share_of_negative']}%")
    summary = "；".join(parts) + "。"
    metrics = []
    if top:
        metrics.append({"name": f"最热维度：{top['dimension']}", "value": top["count"], "unit": "条"})
    metrics.append({"name": "负面评论", "value": data["negative_total"], "unit": "条"})
    return {"summary": summary, "data": data, "metrics": metrics, "evidence": []}


def _run_get_time_trend(args, ctx):
    f = _filters("get_time_trend", args, ctx)
    data = analytics.stats_trend(f, args.get("granularity"))
    if data["empty"]:
        return {"summary": f"在「{analytics.filters_text(f)}」范围内没有可用评论，无法绘制趋势。",
                "data": data, "metrics": [], "evidence": []}
    buckets = [b for b in data["buckets"] if b["total"] > 0]
    peak = max(buckets, key=lambda b: b["total"]) if buckets else None
    neg_peak = max(buckets, key=lambda b: b["negative"]) if buckets else None
    gname = {"day": "日", "week": "周", "month": "月"}[data["granularity"]]
    summary = (f"共 {len(buckets)} 个有记录的{gname}份，合计 {data['total']} 条评论。")
    if peak:
        summary += f"评论量最高的是 {peak['bucket']}（{peak['total']} 条）。"
    if neg_peak and neg_peak["negative"] > 0:
        summary += f"负面最多的是 {neg_peak['bucket']}（{neg_peak['negative']} 条）。"
    metrics = [{"name": "统计时段", "value": len(data["buckets"]), "unit": f"个{gname}"},
               {"name": "评论总量", "value": data["total"], "unit": "条"}]
    if peak:
        metrics.append({"name": f"峰值{gname}份", "value": peak["bucket"], "unit": ""})
    return {"summary": summary, "data": data, "metrics": metrics, "evidence": []}


def _run_compare_platforms(args, ctx):
    f = _filters("compare_platforms", args, ctx)
    data = analytics.stats_platform_compare(f)
    if data["empty"]:
        return {"summary": f"在「{analytics.filters_text(f)}」范围内没有可用评论，无法做平台对比。",
                "data": data, "metrics": [], "evidence": []}
    ps = data["platforms"]
    summary = "；".join(
        f"{p['platform']} {p['total']} 条，正面 {p['positive_rate']}%、负面 {p['negative_rate']}%，"
        f"平均点赞 {p['like_avg']}" for p in ps
    ) + "。"
    if data["comparability_note"]:
        summary += data["comparability_note"]
    metrics = [{"name": f"{p['platform']}·评论量", "value": p["total"], "unit": "条"} for p in ps]
    metrics += [{"name": f"{p['platform']}·负面占比", "value": p["negative_rate"], "unit": "%"} for p in ps]
    return {"summary": summary, "data": data, "metrics": metrics, "evidence": []}


def _run_search_comments(args, ctx):
    f = _filters("search_comments", args, ctx)
    limit = args.get("limit") or 8
    data = analytics.search_comments(f, limit=limit, order="likes")
    if data["empty"]:
        return {"summary": f"在「{analytics.filters_text(f)}」范围内没有找到匹配评论，"
                           f"这本身也是结论：当前数据不支持就该问题给出评论依据。",
                "data": data, "metrics": [], "evidence": []}
    items = data["items"]
    summary = f"在「{analytics.filters_text(f)}」范围内共找到 {data['total']} 条匹配评论，以下展示 {len(items)} 条。"
    metrics = [{"name": "匹配评论", "value": data["total"], "unit": "条"}]
    evidence = [{
        "comment_ref": it["comment_ref"],
        "platform": it["platform"],
        "date": it["date"],
        "sentiment": it["sentiment"],
        "sentiment_label": it["sentiment_label"],
        "dimension": it["dimension"],
        "like_count": it["like_count"],
        "snippet": it["snippet"],
    } for it in items]
    return {"summary": summary, "data": data, "metrics": metrics, "evidence": evidence}


def _run_retrieve_context(args, ctx):
    """RAG 检索：返回的每个片段都带 comment_ref，回答里的引用只能来自这里。"""
    f = _filters("retrieve_context", args, ctx)
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("缺少检索语句", "query 不能为空")
    top_k = int(args.get("top_k") or 6)
    result = rag.retrieve(query, dataset_id=f["dataset_id"], filters=f, top_k=top_k)
    items = result.get("items") or []
    result["meta"] = analytics._meta(
        f, result["candidates"]["filtered_docs"],
        extra={"retrieval": {"fusion": result["trace"]["fusion"],
                             "candidates": result["candidates"],
                             "elapsed_ms": result["elapsed_ms"]}},
    )
    if not items:
        return {"summary": f"在「{analytics.filters_text(f)}」范围内，检索“{query}”没有召回到相关片段。",
                "data": result, "metrics": [], "evidence": []}
    summary = (f"按“{query}”混合检索：候选池 {result['candidates']['filtered_docs']} 条评论，"
               f"三路召回（词项 {result['candidates']['bm25']} / 同义扩展 {result['candidates']['expand']}"
               f" / 向量 {result['candidates']['vector']}）融合后返回最相关 {len(items)} 条，"
               f"耗时 {result['elapsed_ms']}ms。")
    metrics = [{"name": "召回片段", "value": len(items), "unit": "条"},
               {"name": "候选评论", "value": result["candidates"]["filtered_docs"], "unit": "条"}]
    evidence = [{
        "comment_ref": it["comment_ref"],
        "platform": it["platform"],
        "date": it["date"],
        "sentiment": it["sentiment"],
        "sentiment_label": it["sentiment_label"],
        "dimension": it["dimension"],
        "like_count": it["like_count"],
        "snippet": it["snippet"],
    } for it in items]
    return {"summary": summary, "data": result, "metrics": metrics, "evidence": evidence}


_RUNNERS = {
    "get_sentiment_stats": _run_get_sentiment_stats,
    "get_topic_stats": _run_get_topic_stats,
    "get_time_trend": _run_get_time_trend,
    "compare_platforms": _run_compare_platforms,
    "search_comments": _run_search_comments,
    "retrieve_context": _run_retrieve_context,
}


def execute(name, raw_args, ctx=None):
    """执行一个工具调用，永远返回结构化结果（不抛异常给上层）。"""
    started = time.time()
    try:
        args = _validate_args(name, raw_args)
        result = _RUNNERS[name](args, ctx)
        elapsed = int((time.time() - started) * 1000)
        meta = result["data"].get("meta", {})
        return {
            "ok": True,
            "tool": name,
            "args": args,
            "summary": result["summary"],
            "metrics": result["metrics"],
            "evidence": result["evidence"],
            "data": result["data"],
            "filters_text": meta.get("filters_text"),
            "dataset_id": meta.get("dataset_id"),
            "data_version": meta.get("data_version"),
            "empty": bool(result["data"].get("empty")),
            "elapsed_ms": elapsed,
        }
    except (ToolError, analytics.FilterError) as e:
        return {
            "ok": False, "tool": name, "args": raw_args,
            "reason": getattr(e, "reason", str(e)),
            "detail": getattr(e, "detail", ""),
            "summary": f"参数不合法，工具未执行：{getattr(e, 'reason', str(e))}",
            "metrics": [], "evidence": [], "data": {},
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    except Exception as e:  # 兜底：任何异常都不让整轮对话崩掉
        return {
            "ok": False, "tool": name, "args": raw_args,
            "reason": "工具执行异常", "detail": str(e),
            "summary": f"工具执行失败：{e}",
            "metrics": [], "evidence": [], "data": {},
            "elapsed_ms": int((time.time() - started) * 1000),
        }


def tool_catalog():
    """给前端展示"可用工具清单"，说明 AI 能做什么。"""
    return [{"name": t["name"], "description": t["description"],
             "params": list(t["parameters"].get("properties", {}).keys())} for t in TOOL_SPECS]
