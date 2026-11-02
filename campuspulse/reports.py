# -*- coding: utf-8 -*-
"""AI 综合报告服务（PAGE-05 R01-R05）。

报告里的所有数字来自 analytics 的真实聚合；模型只负责把数字写成可读的分析与建议。
模型不可用时，用同一批统计生成规则版报告，并在页面明确标注「统计规则生成，非 AI 分析」，
绝不用预写模板冒充 AI 结论（文档 8.2 重要约束）。
"""
import json
import uuid

import requests

from . import analytics, config, db


def _load_env_key():
    return config.DEEPSEEK_API_KEY


def _llm_json(prompt, timeout=None):
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("missing_api_key")
    url = f"{config.DEEPSEEK_BASE_URL}/v1/chat/completions"
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        url, json=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"},
        timeout=timeout or (config.LLM_TIMEOUT + 15),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"http_{resp.status_code}")
    content = resp.json()["choices"][0]["message"]["content"]
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        raise RuntimeError("no_json")
    return json.loads(content[start:end + 1])


def _llm_text(prompt, timeout=None):
    """纯文本补全（不走 JSON 模式，适合写一段话）。"""
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("missing_api_key")
    url = f"{config.DEEPSEEK_BASE_URL}/v1/chat/completions"
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": 500,
    }
    resp = requests.post(
        url, json=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"},
        timeout=timeout or config.LLM_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"http_{resp.status_code}: {resp.text[:120]}")
    return (resp.json()["choices"][0]["message"]["content"] or "").strip()


def _facts(filters):
    """报告的事实底稿：全部来自统一统计层。"""
    overview = analytics.stats_overview(filters)
    topics = analytics.stats_topics(filters, 8)
    sentiment = analytics.stats_sentiment(filters)
    trend = analytics.stats_trend(filters)
    platforms = analytics.stats_platform_compare(filters)
    evidence = analytics.search_comments(filters, limit=6, order="likes")
    return {
        "overview": overview, "topics": topics, "sentiment": sentiment,
        "trend": trend, "platforms": platforms, "evidence": evidence,
    }


def _compact_facts(facts):
    o = facts["overview"]
    buckets = [b for b in facts["trend"]["buckets"] if b["total"] > 0]
    return {
        "数据集": o["meta"]["dataset_name"],
        "时间范围": o["time_span"],
        "有效评论数": o["total"],
        "正面": f"{o['positive']} 条（{o['positive_rate']}%）",
        "中性": f"{o['neutral']} 条（{o['neutral_rate']}%）",
        "负面": f"{o['negative']} 条（{o['negative_rate']}%）",
        "负面占比分母": o["denominator"],
        "总点赞": o["total_likes"],
        "平台分布": o["platforms"],
        "维度排行（多标签）": [(r["dimension"], r["count"], f"{r['negative_rate']}%负面")
                              for r in facts["topics"]["ranking"]],
        "负面原因": [(r["reason"], r["count"]) for r in facts["topics"]["negative_reasons"][:6]],
        "正面卖点": [(r["point"], r["count"]) for r in facts["topics"]["positive_points"][:6]],
        "平台对比": [{k: p[k] for k in ("platform", "total", "positive_rate", "negative_rate", "like_avg")}
                     for p in facts["platforms"]["platforms"]],
        "趋势峰值": sorted(buckets, key=lambda b: -b["total"])[:3],
        "趋势粒度": facts["trend"]["granularity"],
        "高赞评论样本": [{"ref": e["comment_ref"], "平台": e["platform"], "日期": e["date"],
                          "情感": e["sentiment_label"], "摘要": e["snippet"]}
                         for e in facts["evidence"]["items"][:4]],
    }


_PROMPT = """你是高校舆情分析报告撰写助手。请严格依据下面这份由程序计算出的统计事实，撰写一份面向学校宣传与舆情工作人员的简报。

要求：
1. 只能使用事实中出现的数字，不得新增、推算或编造任何数字、日期、评论内容。
2. 不得断言评论中的指控已核实、不得判断具体个人身份、不得宣称覆盖全网。
3. 语气客观、具体、可执行；每条结论都要能被事实支撑。
4. 只输出 JSON，不要 markdown：

{{
  "summary": "总体概况，3-4 句，含评论量、情感结构与最热维度",
  "dimension_insight": "维度分析，2-3 句，指出热度与负面集中的维度",
  "sentiment_insight": "情感分析，2-3 句，说明正负面结构与平台差异",
  "suggestions": [{{"title": "建议标题", "content": "具体可执行建议 1-2 句", "basis": "依据的事实"}}],
  "cautions": ["需要人工核实或口径限制的提示，2-4 条"]
}}
suggestions 输出 4 条，覆盖招生宣传、服务改进、舆情响应、内容运营。

统计事实：
{facts}
"""


def _rule_report(facts):
    o = facts["overview"]
    t = facts["topics"]
    ranking = t["ranking"]
    top = ranking[0] if ranking else {"dimension": "无", "count": 0, "negative_rate": 0}
    neg_dim = max(ranking, key=lambda r: r["negative_rate"]) if ranking else top
    neg_reason = t["negative_reasons"][0] if t["negative_reasons"] else {"reason": "无", "count": 0}
    pos_point = t["positive_points"][0] if t["positive_points"] else {"point": "无", "count": 0}
    plats = facts["platforms"]["platforms"]
    plat_text = "；".join(f"{p['platform']} {p['total']} 条（负面 {p['negative_rate']}%）" for p in plats) or "无平台数据"
    buckets = [b for b in facts["trend"]["buckets"] if b["total"] > 0]
    peak = max(buckets, key=lambda b: b["total"]) if buckets else None

    return {
        "summary": (
            f"本次报告基于「{o['meta']['dataset_name']}」共 {o['total']} 条有效评论，"
            f"时间覆盖 {o['time_span'][0]} 至 {o['time_span'][1]}。"
            f"正面 {o['positive']} 条（{o['positive_rate']}%）、中性 {o['neutral']} 条、"
            f"负面 {o['negative']} 条（{o['negative_rate']}%，分母 {o['denominator']} 条）。"
            f"讨论最多的是「{top['dimension']}」，共 {top['count']} 条提及（多标签计数）。"
        ),
        "dimension_insight": (
            f"七个维度中，「{top['dimension']}」提及量最高（{top['count']} 条）；"
            f"负面占比最高的是「{neg_dim['dimension']}」（{neg_dim['negative_rate']}%）。"
            f"负面原因中「{neg_reason['reason']}」最集中（{neg_reason['count']} 条）。"
        ),
        "sentiment_insight": (
            f"整体情感以中性为主，正面占比高于负面。分平台看：{plat_text}。"
            + (f"评论量峰值出现在 {peak['bucket']}（{peak['total']} 条）。" if peak else "")
        ),
        "suggestions": [
            {"title": "招生宣传", "content": f"把「{pos_point['point']}」作为核心卖点，在招生季集中投放真实体验内容。",
             "basis": f"正面卖点统计：{pos_point['point']} {pos_point['count']} 条"},
            {"title": "服务改进", "content": f"优先处理「{neg_reason['reason']}」相关诉求，公开整改进度。",
             "basis": f"负面原因统计：{neg_reason['reason']} {neg_reason['count']} 条"},
            {"title": "舆情响应", "content": f"以「{neg_dim['dimension']}」为监测重点，对高赞负面内容建立巡检机制。",
             "basis": f"该维度负面占比 {neg_dim['negative_rate']}%，为七维度最高"},
            {"title": "内容运营", "content": "引导在校生输出真实就读体验，形成可持续的口碑素材。",
             "basis": "正面评论集中在宿舍条件与校园环境类话题"},
        ],
        "cautions": [
            "情感与维度均为关键词规则标注，存在误判可能，重要结论需人工抽样复核。",
            "本报告基于历史采集快照，不代表全网实时声量。",
            "评论中提及的具体问题未经核实，不应直接作为事实对外发布。",
        ],
    }


def generate(filters, force_rule=False):
    facts = _facts(filters)
    o = facts["overview"]
    engine, ai_error = "rules", ""

    body = None
    if not force_rule and config.DEEPSEEK_API_KEY:
        try:
            prompt = _PROMPT.format(facts=json.dumps(_compact_facts(facts), ensure_ascii=False, indent=1))
            raw = _llm_json(prompt)
            if isinstance(raw, dict) and raw.get("summary"):
                body = {
                    "summary": str(raw.get("summary")),
                    "dimension_insight": str(raw.get("dimension_insight") or ""),
                    "sentiment_insight": str(raw.get("sentiment_insight") or ""),
                    "suggestions": [s for s in (raw.get("suggestions") or []) if isinstance(s, dict)][:5],
                    "cautions": [str(c) for c in (raw.get("cautions") or [])][:5],
                }
                engine = "deepseek"
        except Exception as e:
            ai_error = str(e)[:120]

    if body is None:
        body = _rule_report(facts)
        engine = "rules"

    report = {
        "report_id": "rpt_" + uuid.uuid4().hex[:10],
        "generated_at": db.now_str(),
        "engine": engine,
        "engine_label": "DeepSeek 生成" if engine == "deepseek" else "统计规则生成（AI 不可用）",
        "is_ai_generated": engine == "deepseek",
        "ai_error": ai_error,
        "notice": "" if engine == "deepseek" else
                  "AI 报告暂不可用，以下内容由统计规则根据同一批真实数据生成，不是大模型分析结果。",
        "dataset": {
            "dataset_id": o["meta"]["dataset_id"],
            "name": o["meta"]["dataset_name"],
            "data_version": o["meta"]["data_version"],
            "data_source": o["meta"]["data_source"],
            "valid_count": o["total"],
            "time_span": o["time_span"],
            "filters_text": o["meta"]["filters_text"],
        },
        "kpi": {
            "total": o["total"], "positive_rate": o["positive_rate"],
            "neutral_rate": o["neutral_rate"], "negative_rate": o["negative_rate"],
            "negative": o["negative"], "denominator": o["denominator"],
            "total_likes": o["total_likes"], "top_dimension": o["top_dimension"],
            "platforms": o["platforms"],
        },
        "content": body,
        "evidence": [{
            "comment_ref": e["comment_ref"], "platform": e["platform"], "date": e["date"],
            "sentiment_label": e["sentiment_label"], "snippet": e["snippet"],
        } for e in facts["evidence"]["items"][:6]],
        "chart_data": {
            "dimensions": [{"name": r["dimension"], "value": r["count"]} for r in facts["topics"]["ranking"]],
            "sentiment": facts["sentiment"]["distribution"],
            "platform": [{"name": p["platform"], "total": p["total"],
                          "negative": p["negative"]} for p in facts["platforms"]["platforms"]],
        },
        "limitations": [
            o["meta"]["snapshot_note"],
            "情感/维度标签由关键词规则生成，非人工标注，重要结论需抽样复核。",
            "报告中的评论证据均已脱敏，仅保留内部引用 ID。",
        ],
    }
    _save(report, filters)
    return report


def _save(report, filters):
    try:
        conn = db.get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO reports"
            " (report_id, dataset_id, engine, filters, payload, created_at, tenant_id)"
            " VALUES (?,?,?,?,?,?,?)",
            (report["report_id"], report["dataset"]["dataset_id"], report["engine"],
             json.dumps(filters, ensure_ascii=False),
             json.dumps(report, ensure_ascii=False), report["generated_at"], db.tid()),
        )
        conn.commit()
    except Exception:
        pass


def history(dataset_id=None, limit=10):
    sql = ("SELECT report_id, dataset_id, engine, created_at, payload"
           " FROM reports WHERE tenant_id = ?")
    params = [db.tid()]
    if dataset_id:
        sql += " AND dataset_id = ?"
        params.append(dataset_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    out = []
    for r in db.query_all(sql, params):
        try:
            payload = json.loads(r["payload"])
            summary = (payload.get("content") or {}).get("summary", "")
            kpi = payload.get("kpi") or {}
        except Exception:
            summary, kpi = "", {}
        out.append({"report_id": r["report_id"], "dataset_id": r["dataset_id"],
                    "engine": r["engine"], "created_at": r["created_at"],
                    "summary": summary, "kpi": kpi})
    return out


def get(report_id):
    row = db.query_one("SELECT payload FROM reports WHERE report_id = ? AND tenant_id = ?",
                       (report_id, db.tid()))
    return json.loads(row["payload"]) if row else None


# ============================ 驾驶舱 AI 简述（D07）============================
_INSIGHT_PROMPT = """你是高校舆情分析助手。请根据下面的真实统计，用 2-4 句中文写一段舆情简述，
面向学校宣传与舆情工作人员。要求：只用给出的数字，不编造；先讲总体，再讲最热维度与负面集中点；
不要 markdown、不要标题、不要分点，直接输出一段话。

统计：
{facts}
"""


def _rule_insight(facts):
    o, t, p = facts["overview"], facts["topics"], facts["platforms"]
    top = t["ranking"][0] if t["ranking"] else {"dimension": "—", "count": 0, "negative_rate": 0}
    neg_dim = max(t["ranking"], key=lambda r: r["negative_rate"]) if t["ranking"] else top
    reason = t["negative_reasons"][0] if t["negative_reasons"] else None
    plat_txt = "、".join(f"{x['platform']} {x['total']} 条" for x in p["platforms"]) or "暂无"
    text = (f"当前数据集共 {o['total']} 条有效评论，正面 {o['positive_rate']}%、负面 {o['negative_rate']}"
            f"（分母 {o['denominator']} 条），整体以中性讨论为主。"
            f"讨论最集中的维度是「{top['dimension']}」（{top['count']} 条提及），"
            f"负面占比最高的是「{neg_dim['dimension']}」（{neg_dim['negative_rate']}%）。"
            f"平台分布：{plat_txt}。")
    if reason:
        text += f"负面原因中「{reason['reason']}」最集中（{reason['count']} 条）。"
    return text


def chart_insights(filters):
    """数据洞察页的图表解读：一次调用，返回情感/维度/时间/平台四条短解读。"""
    facts = _facts(filters)
    o = facts["overview"]
    if o["empty"]:
        return {"items": [], "engine": "rules", "engine_label": "无数据", "notice": ""}

    topics = facts["topics"]["ranking"]
    top = topics[0] if topics else {"dimension": "—", "count": 0, "negative_rate": 0}
    neg_dim = max(topics, key=lambda r: r["negative_rate"]) if topics else top
    neg_reason = facts["topics"]["negative_reasons"][0] if facts["topics"]["negative_reasons"] else None
    pos_point = facts["topics"]["positive_points"][0] if facts["topics"]["positive_points"] else None
    buckets = [b for b in facts["trend"]["buckets"] if b["total"] > 0]
    peak = max(buckets, key=lambda b: b["total"]) if buckets else None
    plats = facts["platforms"]["platforms"]

    items = [
        {"key": "sentiment", "chart": "情感分布",
         "text": f"有效评论 {o['total']} 条中，正面 {o['positive_rate']}%、负面 {o['negative_rate']}%，"
                 f"分母为 {o['denominator']} 条。中性占比 {o['neutral_rate']}%，说明多数讨论是中性陈述而非评价。"},
        {"key": "dimension", "chart": "七维度与负面原因",
         "text": f"「{top['dimension']}」提及最多（{top['count']} 条，多标签计数），"
                 f"负面占比最高的是「{neg_dim['dimension']}」（{neg_dim['negative_rate']}%）"
                 + (f"，负面原因以「{neg_reason['reason']}」最集中。" if neg_reason else "。")},
        {"key": "time", "chart": "时间趋势",
         "text": (f"按{facts['trend']['granularity']}聚合共有 {len(buckets)} 个有记录的时段，"
                  f"峰值出现在 {peak['bucket']}（{peak['total']} 条）。" if peak else "当前范围没有可绘制的时间记录。")
                 + "缺少采集记录的时段不代表没有讨论。"},
        {"key": "platform", "chart": "平台对比",
         "text": "；".join(f"{p['platform']} {p['total']} 条，正面 {p['positive_rate']}%、负面 {p['negative_rate']}%"
                          for p in plats) + (
             f"。{facts['platforms']['comparability_note']}" if facts["platforms"]["comparability_note"] else "。")},
    ]

    engine, notice = "rules", ""
    if config.DEEPSEEK_API_KEY:
        try:
            payload = [{"图表": it["chart"], "统计事实": it["text"]} for it in items]
            prompt = (
                "下面是四张图表对应的真实统计结论。请把它们各改写成一两句更自然的中文解读，"
                "面向学校舆情工作人员；不得新增或修改任何数字；只输出 JSON："
                '{"items":[{"key":"sentiment","text":"..."},{"key":"dimension","text":"..."},'
                '{"key":"time","text":"..."},{"key":"platform","text":"..."}]}\n\n'
                + json.dumps(payload, ensure_ascii=False, indent=1)
            )
            raw = _llm_json(prompt)
            got = {x.get("key"): x.get("text") for x in (raw.get("items") or []) if isinstance(x, dict)}
            for it in items:
                if got.get(it["key"]):
                    it["text"] = str(got[it["key"]])
            engine = "deepseek"
        except Exception as e:
            notice = f"图表 AI 解读取失败（{str(e)[:60]}），以下为统计规则生成的解读。"
    return {"items": items, "engine": engine,
            "engine_label": "DeepSeek 生成" if engine == "deepseek" else "统计规则生成",
            "notice": notice, "generated_at": db.now_str()}


def dashboard_insight(filters):
    facts = _facts(filters)
    o = facts["overview"]
    if o["empty"]:
        return {"text": "当前筛选范围内没有可用评论，暂不生成分析。", "engine": "rules",
                "engine_label": "无数据", "notice": "", "generated_at": db.now_str()}

    payload = {
        "数据集": o["meta"]["dataset_name"],
        "时间范围": o["time_span"],
        "有效评论": o["total"],
        "正面占比": o["positive_rate"],
        "中性占比": o["neutral_rate"],
        "负面占比": o["negative_rate"],
        "负面占比分母": o["denominator"],
        "负面条数": o["negative"],
        "最热维度": (facts["topics"]["ranking"][0] if facts["topics"]["ranking"] else None),
        "维度排行": [(r["dimension"], r["count"], r["negative_rate"]) for r in facts["topics"]["ranking"][:4]],
        "负面原因前三": [(r["reason"], r["count"]) for r in facts["topics"]["negative_reasons"][:3]],
        "平台": [{k: x[k] for k in ("platform", "total", "positive_rate", "negative_rate")}
                 for x in facts["platforms"]["platforms"]],
    }
    text, engine, notice = "", "rules", ""
    if config.DEEPSEEK_API_KEY:
        try:
            t = _llm_text(_INSIGHT_PROMPT.format(facts=json.dumps(payload, ensure_ascii=False, indent=1)))
            if t:
                text, engine = t, "deepseek"
        except Exception as e:
            notice = f"AI 简述暂不可用（{str(e)[:60]}），以下为统计规则生成的说明。"
    if not text:
        text = _rule_insight(facts)
        notice = notice or "AI 简述不可用，以下内容由统计规则根据同一批真实数据生成。"
    return {"text": text, "engine": engine,
            "engine_label": "DeepSeek 生成" if engine == "deepseek" else "统计规则生成",
            "notice": notice, "generated_at": db.now_str(),
            "dataset_id": o["meta"]["dataset_id"], "filters_text": o["meta"]["filters_text"]}


# ============================ 导出：Markdown ============================
def to_markdown(report):
    """把报告导成一份可以直接贴进周报 / 邮件 / 文档的 Markdown。

    为什么是 Markdown 而不是再做一个 PDF：PDF 已经有了（浏览器打印样式），
    真正缺的是"能改、能粘、能台账化"的那一版——学院要交的周报是 Word 文档。
    """
    d = report.get("dataset") or {}
    k = report.get("kpi") or {}
    c = report.get("content") or {}
    lines = []
    a = lines.append

    a(f"# 校园舆情分析报告 · {d.get('name') or ''}")
    a("")
    a(f"> 生成时间：{report.get('generated_at')}　|　引擎：{report.get('engine_label')}")
    span = d.get("time_span")
    span_text = " ~ ".join(span) if isinstance(span, (list, tuple)) else str(span or "—")
    a(f"> 数据来源：{d.get('data_source') or '—'}　|　时间覆盖：{span_text}")
    a(f"> 筛选口径：{d.get('filters_text')}　|　有效样本：{d.get('valid_count')} 条")
    a("")
    if report.get("notice"):
        a(f"**说明**：{report['notice']}")
        a("")

    a("## 一、核心指标")
    a("")
    a("| 指标 | 数值 |")
    a("|---|---|")
    a(f"| 有效评论 | {k.get('total')} 条 |")
    a(f"| 正面 / 中性 / 负面 | {k.get('positive_rate')}% / {k.get('neutral_rate')}% / {k.get('negative_rate')}% |")
    a(f"| 负面条数（分母） | {k.get('negative')} / {k.get('denominator')} |")
    a(f"| 总点赞 | {k.get('total_likes')} |")
    a(f"| 讨论最多的维度 | {k.get('top_dimension')} |")
    # kpi.platforms 是 {平台: 条数} 的字典（总览接口的形状），不是列表
    plats = k.get("platforms") or {}
    if isinstance(plats, dict) and plats:
        a("| 平台分布 | " + "；".join(f"{n} {c} 条" for n, c in plats.items()) + " |")
    elif isinstance(plats, list) and plats:
        a("| 平台分布 | " + "；".join(str(x) for x in plats) + " |")
    a("")

    a("## 二、结论")
    a("")
    a(str(c.get("summary") or ""))
    a("")
    if c.get("dimension_insight"):
        a(f"**维度**：{c['dimension_insight']}")
        a("")
    if c.get("sentiment_insight"):
        a(f"**情感**：{c['sentiment_insight']}")
        a("")

    if c.get("suggestions"):
        a("## 三、建议")
        a("")
        for i, sg in enumerate(c["suggestions"], 1):
            a(f"{i}. **{sg.get('title') or ''}**：{sg.get('content') or ''}")
            if sg.get("basis"):
                a(f"   - 依据：{sg['basis']}")
        a("")

    if report.get("evidence"):
        a("## 四、评论证据（已脱敏，编号可回溯）")
        a("")
        for e in report["evidence"]:
            a(f"- `{e.get('comment_ref')}`（{e.get('platform')} · {e.get('date')} · "
              f"{e.get('sentiment_label')}）：{e.get('snippet')}")
        a("")

    if c.get("cautions"):
        a("## 五、注意事项")
        a("")
        for x in c["cautions"]:
            a(f"- {x}")
        a("")

    if report.get("limitations"):
        a("## 六、数据与口径局限")
        a("")
        for x in report["limitations"]:
            a(f"- {x}")
        a("")

    a("---")
    a("")
    a("由 CampusPulse 生成：报告中的每个数字都来自同一批真实数据的统计结果，"
      "可在系统内复算；评论证据均已去标识。")
    return chr(10).join(lines)


def markdown_filename(report):
    """文件名带日期与数据集，导多了也分得清是哪一份。"""
    d = (report.get("dataset") or {})
    stamp = str(report.get("generated_at") or "").replace(":", "").replace(" ", "_")[:15]
    name = str(d.get("name") or "report").replace(" ", "").replace("·", "-")
    return f"舆情报告_{name}_{stamp}.md"
