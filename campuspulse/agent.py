# -*- coding: utf-8 -*-
"""AI Agent 编排层 —— 工具增强的自然语言分析。

流程（对应文档 5.2 A01-A12）：
    用户问题 → 意图/条件交给模型 → 模型选择工具 → 后端真实计算 → 模型基于结果写解释
    → 后端用工具结果生成图表与证据 → 结构化校验 → 返回

三条底线：
  1. 数字只来自工具，图表由后端 charts.py 生成，模型不产出任何图表数值；
  2. 模型不能执行任意 SQL/命令，只能调用 tools.py 里的 5 个白名单函数；
  3. DeepSeek 不可用时不假装是 AI：降级为规则路由，仍然真实查库，并明确标注引擎类型。
"""
import json
import re
import time
import uuid

import requests

from . import (analytics, charts as chart_builder, config, db, guard, rag, tools,
               veracity)

ENGINE_AI = "deepseek"
ENGINE_RULES = "rules"


class AgentError(Exception):
    pass


# ============================ 提示词 ============================
def _system_prompt(dataset):
    dims = "、".join(config.DIMENSIONS)
    plats = "、".join(config.PLATFORMS)
    span = f"{dataset.get('time_start') or '未知'} ~ {dataset.get('time_end') or '未知'}"
    return f"""你是 CampusPulse 的高校舆情分析助手，服务对象是学校宣传与舆情工作人员。

【当前数据集】
dataset_id: {dataset['dataset_id']}
名称: {dataset['name']}
时间覆盖: {span}
有效评论: {dataset.get('valid_count')} 条
平台: {plats}
业务维度: {dims}

【硬性规则】
1. 任何数字都必须来自工具返回结果。不确定就先调用工具，禁止凭印象或常识给出数字、日期、评论。
2. 先想清楚需要哪几步，再调用工具；单轮最多 6 次工具调用，尽量用 1 次并行调用完成。
   同一个工具、同一组参数只调用一次，不要重复查询相同内容。
2b. 需要评论证据时，search_comments 整轮**最多调用 1 次**（limit 取 6-10 即可）；
   拿到结果后不要为了"多找几条"再检索一遍，直接使用已有条目。
2c. 需要"学生原话 / 具体怎么说 / 细节"时用 retrieve_context（语义检索，整轮最多 1 次，top_k 取 6）；
   只是按明确关键词精确查找时才用 search_comments。两者不要同时为了同一目的调用。
3. dataset_id 默认用当前数据集；只有用户明确要求换数据集时才改。
4. 维度是多标签计数（一条评论可命中多个维度），解释占比时说明分母。
5. 工具返回 empty=true 或 total=0 时，必须直说"当前筛选范围内没有可用数据，无法回答"，不要补造。
6. 可以给运营建议，但不得断言"某事件已证实""某用户身份/诉求真实"，也不得宣称覆盖全网。
7. 平台对比时若样本量差距大，要提示可比性有限。
8. 不要在回答里用字符画图、ASCII 条形图、表格或 markdown 排版；前端会根据工具结果自动渲染真实图表。
   answer 只写自然语言结论与依据。

【分工说明】
你只负责"判断需要哪几步 + 选择工具 + 填参数"，**不要撰写最终回答**。
工具执行完后请直接结束本轮输出（可以留空），后端会基于工具结果单独生成回答文本。
若问题完全不需要查数据（例如只是打招呼），可以不调用工具，直接结束。
"""


# ============================ 第二阶段：撰写回答的提示词 ============================
ANSWER_SYSTEM_PROMPT = """你是 CampusPulse 的高校舆情分析助手。下面会给你一份【工具执行结果】，
请只根据这份结果用中文写出回答。

【硬性规则】
1. 只能使用工具结果里出现过的数字。禁止估算、外推、编造；需要相减或相除时，两个数必须都出现在结果里。
2. 先给结论，再给依据，共 2-5 句。不要写"综上所述""根据分析"这类空话。
3. 不要使用 markdown 标题、表格、代码块、列表符号，也不要用字符画图（前端会自动渲染真实图表）。
4. 工具结果为空（empty=true 或 total=0）时，直接说"当前筛选范围内没有可用数据，无法回答"。
5. 不得断言某事件已证实、某用户身份真实，也不得宣称覆盖全网。
6. 涉及占比时说明分母（例如"负面 348 条 / 有效 1,589 条"）。
7. 若工具结果里的筛选条件已经限定了情感（如 sentiment=negative），那么该范围内的"负面率"恒为 100%，
   这是筛选造成的、不是发现，不要把它当作结论写出来。
8. 用户会给你一段【检索到的原文片段】，里面的编号（CMT-xxxxxxxx）是唯一合法的引用来源。
   凡是要描述"学生具体怎么说"的句子，都必须在句尾紧跟 [编号]，例如"有学生吐槽网费不便宜[CMT-9F2A1B3C]"。
   最多引用 3 处；只能引用片段清单里出现过的编号，绝不允许自己编造或改动编号。
   如果清单为空，就不要写任何方括号编号。
9. 不要使用 ** 加粗、## 标题、` 代码、- 列表等 markdown 标记，前端会按纯文本渲染。
"""


# ============================ DeepSeek 调用 ============================
def _call_llm(messages, timeout=None, max_tokens=900):
    if not config.DEEPSEEK_API_KEY:
        raise AgentError("missing_api_key")
    url = f"{config.DEEPSEEK_BASE_URL}/v1/chat/completions"
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "tools": tools.openai_tools(),
        "tool_choice": "auto",
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    resp = requests.post(
        url, json=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"},
        timeout=timeout or config.LLM_TIMEOUT,
    )
    if resp.status_code != 200:
        raise AgentError(f"http_{resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if not data.get("choices"):
        raise AgentError("empty_choices")
    return data["choices"][0]["message"]


def api_available():
    return bool(config.DEEPSEEK_API_KEY)


# ============================ 第二阶段：流式生成回答 ============================
def _ai_error_notice(exc):
    """把模型调用异常翻译成"人话 + 诊断码"，而不是把原始 JSON 甩到界面上。

    原始错误长这样：http_401: {"error":{"message":"Authentication Fails, ...}
    老师/评委看到这行只会得出一个结论——"系统坏了"。但他真正该知道的是：
    数字还是数据库算出来的（没变），只是回答没有 AI 那么细。
    完整异常仍然进日志，排查能力一点没丢。
    """
    raw = str(exc)
    low = raw.lower()
    if "401" in raw or "403" in raw or "authentication" in low or "invalid api key" in low:
        code, why = "auth", "密钥无效或额度用尽"
    elif "429" in raw:
        code, why = "rate_limit", "请求太频繁被限流"
    elif "timeout" in low or "timed out" in low:
        code, why = "timeout", "响应超时"
    elif re.search(r"(?<!\d)5\d\d(?!\d)", raw):
        code, why = "server", "AI 服务端暂时不可用"
    elif any(k in low for k in ("connection", "max retries", "getaddrinfo", "ssl", "proxy")):
        code, why = "network", "连不上 AI 服务（网络不可用）"
    else:
        code, why = "unknown", "调用异常"
    return (f"AI 暂不可用（{why}），已切换为规则模式：后面的数字仍由数据库直接统计，"
            f"口径与图表不受影响，只是分析的细致程度不如 AI。诊断码 {code}。")


def _log_ai_error(exc):
    """原始异常只进日志：界面上不该出现半个 JSON。"""
    try:
        print(f"[agent] AI 调用失败，已降级规则模式：{str(exc)[:300]}", flush=True)
    except Exception:
        pass


def _headers():
    return {"Content-Type": "application/json",
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"}


def stream_completion(messages, on_token=None, timeout=None, max_tokens=700):
    """以流式方式调用 DeepSeek，边收边回调。

    返回 (完整文本, 首字延迟秒数)。首字延迟是真实测量值，用于"思考可视化"里
    展示"从提问到看见第一个字"的耗时，不是前端动画估算。
    """
    if not config.DEEPSEEK_API_KEY:
        raise AgentError("missing_api_key")
    url = f"{config.DEEPSEEK_BASE_URL}/v1/chat/completions"
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "stream": True,
    }
    started = time.time()
    first_at = None
    parts = []
    resp = requests.post(url, json=payload, headers=_headers(),
                         timeout=timeout or config.LLM_TIMEOUT, stream=True)
    try:
        if resp.status_code != 200:
            body = ""
            try:
                body = resp.text[:200]
            except Exception:
                pass
            raise AgentError(f"http_{resp.status_code}: {body}")
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data in ("[DONE]", ""):
                if data == "[DONE]":
                    break
                continue
            try:
                obj = json.loads(data)
            except Exception:
                continue
            for ch in (obj.get("choices") or []):
                delta = (ch.get("delta") or {}).get("content")
                if not delta:
                    continue
                if first_at is None:
                    first_at = time.time() - started
                parts.append(delta)
                if on_token:
                    on_token(delta)
    finally:
        resp.close()
    return "".join(parts), (first_at or 0.0)


def _compose_messages(dataset, question, tool_results, history=None, rag_items=None):
    payload = [{"tool": r["tool"], "ok": r.get("ok"), "summary": r.get("summary"),
                "detail": _compact_for_llm(r).get("detail")} for r in tool_results]
    ctx = ""
    if history:
        prev = [(h.get("role"), (h.get("content") or "")[:200])
                for h in history[-4:] if h.get("content")]
        if prev:
            ctx = "【最近对话】\n" + "\n".join(
                f"{'用户' if r == 'user' else '助手'}：{c}" for r, c in prev) + "\n\n"
    context_block = rag.build_context(rag_items or [])
    user = (f"数据集：{dataset['name']}（有效评论 {dataset.get('valid_count')} 条，"
            f"覆盖 {dataset.get('time_start') or '未知'} ~ {dataset.get('time_end') or '未知'}）\n\n"
            f"{ctx}【用户问题】{question}\n\n"
            f"【工具执行结果】\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            f"{context_block}")
    return [{"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": user}]


# ============================ 引用复核与重写（M4）============================
REVISE_SYSTEM_PROMPT = """你在做引用复核后的重写。上一版回答里有编号无法溯源。

硬约束：
1. 只能使用【允许引用的编号】清单里的编号，一个都不许新增；
2. 清单不足以支撑的结论，改成定性表述（如"较多同学提到""部分反映"），不要编造编号；
3. 数字只能沿用【上一版回答】里已有的数字，不要引入新数字；
4. 保持原回答的结构与篇幅量级，不要在正文里解释你在做什么；
5. 直接输出重写后的正文，不要 JSON、不要代码块、不要"以下是重写"之类的前缀。"""


def _revise_answer(answer, problems, items):
    """引用复核不通过时的**唯一一次**重写。

    三条刻意的设计：
      · 不重新检索——复用本轮已取到的片段，避免"越查越乱"，也避免多花一次检索时间；
      · 不传 on_token——第一版已经通过打字机效果打到界面上了，再流一遍会让文字来回跳，
        重写完成后用 answer_reset 一次性替换，视觉上是"撤回并更正"；
      · 失败就返回 None——调用方据此进入剔除分支，不会因此让整轮对话失败。
    """
    allowed = [it for it in (items or []) if it.get("comment_ref")][:8]
    if not allowed:
        return None
    lines = "\n".join(f"[{it['comment_ref']}] {(it.get('text') or it.get('snippet') or '')[:160]}"
                      for it in allowed)
    issues = "\n".join(f"- [{p['ref']}] {p['status_label']}" for p in (problems or [])[:8])
    user = (f"【允许引用的编号】\n{lines}\n\n"
            f"【上一版回答】\n{answer}\n\n"
            f"【复核发现的问题】\n{issues}\n\n"
            "请重写这段回答，使其只依赖上面的编号。")
    text, _ = stream_completion(
        [{"role": "system", "content": REVISE_SYSTEM_PROMPT},
         {"role": "user", "content": user}],
        timeout=min(getattr(config, "LLM_TIMEOUT", 60) or 60, 30),
        max_tokens=800,
    )
    text = (text or "").strip()
    if text.startswith("```"):                       # 模型偶尔会套一层代码块
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    return text or None


# ============================ RAG：检索条件推导 ============================
_SENTIMENT_ZH = {"positive": "正面", "neutral": "中性", "negative": "负面"}


def _pick_richest_meta(tool_results):
    """挑「筛选条件最具体」的那份 meta，而不是第一份。

    为什么不能取第一份：Agent 常先调 get_sentiment_stats 摸情感分布，而这个工具
    会**故意清空** sentiment（已经在按情感统计了，再限情感等于自相矛盾）。
    如果取第一份，用户问「宿舍负面」，携带到驾驶舱的筛选就丢了「负面」这一项——
    数字看着对，口径已经偏了。
    """
    best, best_score = None, -1
    for r in tool_results:
        if not r.get("ok"):
            continue
        meta = (r.get("data") or {}).get("meta") or {}
        fl = meta.get("filters") or {}
        if not fl:
            continue
        d1, d2 = (fl.get("date_range") or [None, None])[:2]
        score = (len(fl.get("dimension") or []) + len(fl.get("platform") or [])
                 + len(fl.get("sentiment") or [])
                 + (1 if d1 else 0) + (1 if d2 else 0)
                 + (1 if fl.get("keyword") else 0))
        if score > best_score:
            best, best_score = meta, score
    return best


def _carry_to_views(meta, current_dataset_id=None):
    """把本轮真实筛选口径编码成可跳转的 URL（S7「对话即筛选」）。

    前端 `core.js::readUrl()` 本来就认识这 7 个参数，所以这里只负责**把口径写对**：
    不新造参数名、不在前端再拼一次，避免"两个地方各有一套筛选语义"。
    """
    from urllib.parse import urlencode

    if not meta:
        return None
    fl = meta.get("filters") or {}
    d1, d2 = (fl.get("date_range") or [None, None])[:2]
    params, chips = [], []

    if fl.get("dimension"):
        params.append(("dimension", ",".join(fl["dimension"])))
        chips.append({"key": "dimension", "label": "维度",
                      "value": "、".join(fl["dimension"])})
    if fl.get("sentiment"):
        zh = [_SENTIMENT_ZH.get(s, s) for s in fl["sentiment"]]
        params.append(("sentiment", ",".join(fl["sentiment"])))
        chips.append({"key": "sentiment", "label": "情感", "value": "、".join(zh)})
    if fl.get("platform"):
        params.append(("platform", ",".join(fl["platform"])))
        chips.append({"key": "platform", "label": "平台",
                      "value": "、".join(fl["platform"])})
    if d1 or d2:
        if d1:
            params.append(("date_from", d1))
        if d2:
            params.append(("date_to", d2))
        chips.append({"key": "date_range", "label": "时间",
                      "value": f"{d1 or '不限'} ~ {d2 or '不限'}"})
    if fl.get("keyword"):
        params.append(("keyword", fl["keyword"]))
        chips.append({"key": "keyword", "label": "关键词", "value": fl["keyword"]})

    ds = meta.get("dataset_id")
    if ds:
        params.append(("dataset_id", ds))

    applied = len(chips)
    query = urlencode(params)
    return {
        "query": query,
        "dataset_id": ds,
        "dataset_changed": bool(ds and current_dataset_id and ds != current_dataset_id),
        "chips": chips,
        "applied": applied,
        # 没有筛选就不给按钮：跳过去看到的和驾驶舱默认态一模一样，只会让用户以为按钮坏了
        # 驾驶舱的路由是 "/"（不是 /dashboard），这里写死路径而不是从别处推，
        # 免得再出现"链接生成得挺像、点开 404"的情况——已在验收里断言可达。
        "dashboard_url": f"/?{query}" if applied else "",
        "insights_url": f"/insights?{query}" if applied else "",
        "summary": "；".join(f"{c['label']}：{c['value']}" for c in chips),
        "note": "" if applied else "本轮基于全量数据，没有需要携带的筛选条件。",
    }



def _rag_filters_from(tool_results):
    """把统计工具已经确定的筛选口径传给检索层，保证"同一问题、同一范围"。

    统计口径是 sentiment=negative + dimension=宿舍住宿，检索却满库乱找，
    就会出现"数字说宿舍负面最多，引用的却是食堂好评"这种自相矛盾。
    """
    # 与 _carry_to_views 用同一个挑选函数：都取「最具体」的那份口径，不取第一份
    meta = _pick_richest_meta(tool_results)
    if not meta:
        return {}
    fl = meta.get("filters") or {}
    dr = fl.get("date_range") or [None, None]
    return {
        "platforms": fl.get("platform") or [],
        "dimensions": fl.get("dimension") or [],
        "sentiments": fl.get("sentiment") or [],
        "date_from": dr[0] if dr else None,
        "date_to": dr[1] if len(dr) > 1 else None,
    }


def _rag_query(question, tool_results):
    """检索语句 = 原问题 + 工具算出的高频主题词。

    只搜原问题会漏掉"口味太咸"这类统计里出现、但用户没提到的具体说法；
    补上负面原因 top-2 与最热维度，召回精度实测更好。
    """
    extra = []
    for r in tool_results:
        if not r.get("ok") or r.get("tool") != "get_topic_stats":
            continue
        d = r.get("data") or {}
        for rr in (d.get("negative_reasons") or [])[:2]:
            term = rr.get("reason")
            if term and term not in question and term not in extra:
                extra.append(term)
        for rk in (d.get("ranking") or [])[:1]:
            term = rk.get("dimension")
            if term and term not in question and term not in extra:
                extra.append(term)
    return (question + (" " + " ".join(extra[:3]) if extra else ""))[:120]


# ============================ 结构化补充（不依赖模型）============================
def _default_limitations(response, tool_results):
    """局限说明由后端按口径生成，比让模型自由发挥更可控。"""
    lims = []
    meta = next((r.get("data", {}).get("meta") for r in tool_results
                 if r.get("ok") and (r.get("data") or {}).get("meta")), None)
    if meta and meta.get("sample_size") is not None:
        lims.append(f"本次统计基于 {meta.get('sample_size')} 条有效评论"
                    f"（筛选：{meta.get('filters_text') or '全部数据'}）")
    if any(r.get("tool") == "get_topic_stats" for r in tool_results):
        lims.append("维度为多标签计数，一条评论可同时命中多个维度，故各维度之和大于样本数")
    lims.append("结论仅代表当前采集样本，不代表全网实时声量")
    return lims[:3]


def _default_followups(tool_results, question=""):
    out = []
    for r in tool_results:
        data = r.get("data") or {}
        if r.get("tool") == "get_topic_stats":
            rank = data.get("ranking") or []
            if rank:
                top = rank[0].get("dimension")
                # 问题里已经问的就是这个维度，就别再推荐一遍
                if top and top not in (question or ""):
                    out.append(f"「{top}」的负面主要集中在哪些具体原因？")
        if r.get("tool") == "compare_platforms":
            out.append("这个平台差异在另一个数据集上还成立吗？")
        if r.get("tool") == "get_time_trend":
            out.append("讨论量最高的那个时间段，负面占比是多少？")
    out.append("能给出支持这个结论的评论证据吗？")
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq[:3]



# ============================ 结果压缩（给模型看）============================
def _compact_for_llm(result):
    """只把必要信息喂回模型，避免几万个时间桶撑爆上下文。"""
    data = result.get("data") or {}
    tool = result["tool"]
    detail = {}
    if tool == "get_sentiment_stats":
        detail = {"counts": data.get("counts"), "denominator": data.get("denominator"),
                  "positive_rate": data.get("positive_rate"),
                  "negative_rate": data.get("negative_rate"),
                  "by_platform": [{k: p[k] for k in ("platform", "total", "positive_rate", "negative_rate")}
                                  for p in (data.get("by_platform") or [])]}
    elif tool == "get_topic_stats":
        detail = {"ranking": (data.get("ranking") or [])[:8],
                  "negative_reasons": (data.get("negative_reasons") or [])[:5],
                  "positive_points": (data.get("positive_points") or [])[:5],
                  "negative_total": data.get("negative_total"),
                  "count_mode": data.get("count_mode")}
    elif tool == "get_time_trend":
        buckets = [b for b in (data.get("buckets") or []) if b["total"] > 0]
        detail = {"granularity": data.get("granularity"),
                  "total": data.get("total"),
                  "peak_buckets": sorted(buckets, key=lambda b: -b["total"])[:3],
                  "recent": buckets[-6:]}
    elif tool == "compare_platforms":
        detail = {"platforms": data.get("platforms"),
                  "comparability_note": data.get("comparability_note")}
    elif tool == "search_comments":
        detail = {"total": data.get("total"),
                  "items": [{k: it[k] for k in ("comment_ref", "platform", "date",
                                                "sentiment_label", "dimension", "snippet")}
                            for it in (data.get("items") or [])[:6]]}
    elif tool == "retrieve_context":
        # 片段原文已经通过【检索到的原文片段】独立注入，这里只回显检索元信息，避免重复占上下文
        detail = {"query": data.get("query"),
                  "count": len(data.get("items") or []),
                  "candidates": data.get("candidates"),
                  "fusion": (data.get("trace") or {}).get("fusion")}
    return {"tool": tool, "ok": result.get("ok"), "summary": result.get("summary"),
            "filters_text": result.get("filters_text"), "detail": detail}


# ============================ 回答校验 ============================
_STEP_LABELS = {
    "get_sentiment_stats": "查询情感统计",
    "get_topic_stats": "查询维度与主题排行",
    "get_time_trend": "计算时间趋势",
    "compare_platforms": "比较两个平台",
    "search_comments": "检索评论证据",
    "retrieve_context": "混合检索原文片段",
}


def grounding_check(answer, tool_results):
    """数字核验（对外保留这个名字，实现已收拢到 veracity.number_check）。

    M4 之前这段逻辑长在 agent.py 里，和引用核验各写一套；
    两套"校验"迟早会给出不一致的结论，所以统一到 veracity 单一入口。
    """
    return veracity.number_check(answer, tool_results)


def _parse_final(content):
    """解析模型最后一条消息（要求是 JSON，但允许它带少量包裹文字）。"""
    text = (content or "").strip()
    if not text:
        return None
    obj = None
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
        except Exception:
            obj = None
    if isinstance(obj, dict) and obj.get("answer"):
        return {
            "answer": str(obj.get("answer")).strip(),
            "limitations": [str(x) for x in (obj.get("limitations") or []) if str(x).strip()][:4],
            "followups": [str(x) for x in (obj.get("followups") or []) if str(x).strip()][:4],
        }
    # 不是 JSON 就当作纯文本回答（仍然可用，只是缺少局限说明）
    if len(text) > 400:
        text = text[:400] + "…"
    return {"answer": text, "limitations": [], "followups": []}


# ============================ 规则路由（降级模式）============================
_INTENT_RULES = [
    ("compare", ["对比", "比较", "差异", "区别", "哪个平台", "两个平台", "双平台"]),
    ("trend", ["趋势", "变化", "什么时候", "何时", "上升", "下降", "波动", "走势", "峰值", "高峰"]),
    ("topic", ["关注", "热点", "最多", "最常见", "主要问题", "维度", "排行", "哪些问题", "吐槽"]),
    ("evidence", ["证据", "依据", "评论", "例子", "原文", "支持"]),
    ("sentiment", ["正面", "负面", "情感", "情绪", "好评", "差评", "口碑", "占比"]),
]


def _detect_dimensions(question):
    return [d for d in config.DIMENSIONS if d in question or d[:2] in question]


def _detect_platforms(question):
    found = [p for p in config.PLATFORMS if p in question]
    if "抖音" in question and "抖音" not in found:
        found.append("抖音")
    if ("小红书" in question or "红书" in question) and "小红书" not in found:
        found.append("小红书")
    return found


def _detect_sentiment(question):
    if "负面" in question or "差评" in question or "吐槽" in question:
        return ["negative"]
    if "正面" in question or "好评" in question:
        return ["positive"]
    return None


def _detect_dates(question):
    """识别"某年某月""近半年"等时间表达，转成数据范围内的实际区间。"""
    m = re.search(r"(20\d{2})\s*年\s*(?:(\d{1,2})\s*月)?", question)
    if m:
        y = m.group(1)
        if m.group(2):
            mo = int(m.group(2))
            last = [31, 29 if int(y) % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1]
            return f"{y}-{mo:02d}-01", f"{y}-{mo:02d}-{last:02d}"
        return f"{y}-01-01", f"{y}-12-31"
    m = re.search(r"近\s*(\d+)\s*个?月", question)
    if m:
        ds = db.get_dataset(config.DEFAULT_DATASET_ID) or {}
        end = ds.get("time_end")
        if end:
            from datetime import date
            y, mo, d = map(int, end.split("-"))
            months = int(m.group(1))
            tot = y * 12 + (mo - 1) - months + 1
            return f"{tot // 12}-{tot % 12 + 1:02d}-01", end
    return None, None


def rules_plan(question, dataset_id):
    """规则版"意图 → 工具"映射，用于 DeepSeek 不可用时仍能真实查库。"""
    dims = _detect_dimensions(question)
    plats = _detect_platforms(question)
    sent = _detect_sentiment(question)
    date_from, date_to = _detect_dates(question)

    intents = [name for name, kws in _INTENT_RULES if any(k in question for k in kws)]
    calls = []
    base = {"dataset_id": dataset_id}
    if plats:
        base["platform"] = plats
    if dims:
        base["dimension"] = dims

    def add(tool, extra=None):
        args = dict(base)
        if extra:
            args.update(extra)
        if not any(c["name"] == tool for c in calls):
            calls.append({"name": tool, "args": args})

    if "compare" in intents:
        add("compare_platforms", {"sentiment": sent} if sent else None)
    if "trend" in intents:
        add("get_time_trend", {"sentiment": sent})
    if "topic" in intents or "evidence" in intents:
        add("get_topic_stats", {"sentiment": sent})
    if "sentiment" in intents or not calls:
        add("get_sentiment_stats")
    if "evidence" in intents or dims:
        kw = None
        if not dims and "评论" in question:
            kw = None
        add("search_comments", {"sentiment": sent or (["negative"] if "负面" in question else None),
                                "limit": 8})

    for c in calls:
        if date_from:
            c["args"]["date_from"] = date_from
        if date_to:
            c["args"]["date_to"] = date_to
        # 清掉 None，避免参数校验时被当成空值
        c["args"] = {k: v for k, v in c["args"].items() if v not in (None, "", [])}
    return calls[:config.AGENT_MAX_TOOL_CALLS]


def _rule_answer(question, tool_results):
    """把工具结论拼成一段可读回答（不含任何模型生成内容）。"""
    oks = [r for r in tool_results if r.get("ok") and not r.get("empty")]
    if not oks:
        return {
            "answer": "当前筛选范围内没有可用数据，无法回答这个问题。可以放宽时间范围或换一个数据集再试。",
            "limitations": ["本次查询未命中任何有效评论记录，因此不生成结论。"],
            "followups": ["换个时间范围再问一次", "查看当前数据集覆盖了哪些时间"],
        }
    parts = [r["summary"] for r in oks]
    return {
        "answer": "".join(parts),
        "limitations": ["该回答由规则路由基于统计结果生成（DeepSeek 未启用或不可用）",
                        "结论仅代表当前采集样本，不代表全网声量"],
        "followups": ["这个问题在另一个数据集上是什么结果？", "能给出相关评论证据吗？"],
    }


# ============================ 主流程 ============================
def _tool_steps_payload(tool_results):
    steps = []
    for i, r in enumerate(tool_results):
        steps.append({
            "index": i + 1,
            "tool": r["tool"],
            "label": _STEP_LABELS.get(r["tool"], r["tool"]),
            "args": r.get("args"),
            "status": "completed" if r.get("ok") else "failed",
            "elapsed_ms": r.get("elapsed_ms", 0),
            "summary": r.get("summary"),
            "empty": r.get("empty", False),
            "reason": r.get("reason") if not r.get("ok") else None,
        })
    return steps


def _base_response(dataset_id):
    ds = db.get_dataset(dataset_id) or {}
    return {
        "answer": "",
        "dataset_id": dataset_id,
        "dataset_name": ds.get("name"),
        "data_version": ds.get("data_version"),
        "filters": {"platform": [], "dimension": [], "sentiment": [],
                    "date_range": [ds.get("time_start"), ds.get("time_end")]},
        "metrics": [],
        "charts": [],
        "evidence": [],
        "tool_steps": [],
        "limitations": [],
        "followups": [],
        "engine": ENGINE_RULES,
        "engine_label": "规则路由",
        "notice": "",
    }


def _legacy_citations(answer, items, dataset_id):
    """旧字段 `citations`：保持 v1.0 的结构与语义，给既有验收脚本和旧前端读。

    新代码请读 `citation`（带 verdict）。保留它不是兼容性洁癖——验收里有一条
    `valid + len(invalid) + len(unknown) == total` 的自洽断言，字段一改名，
    那条断言就会静默失效，而"断言没跑起来"比"断言失败"危险得多。
    """
    try:
        return rag.citation_check(answer, items, dataset_id)
    except Exception as e:
        _log_ai_error(e)
        return {"total": 0, "valid": 0, "invalid": [], "unknown": [],
                "passed": False, "note": "引用统计不可用（核验环节异常）"}


def _emit(emit, type_, **payload):
    """事件推送（只在流式模式下有 emit 回调；非流式调用时是空操作）。"""
    if not emit:
        return
    try:
        emit(dict(type=type_, **payload))
    except Exception:
        pass


def _guard_response(question, dataset_id, verdict, started, emit):
    """被输入侧拦下的提问，返回一个"正常的拒绝回答"。

    刻意不用 4xx：拦截不是接口错误，是一种合法结果。走同一返回体意味着
    流式与非流式、AI 引擎与规则引擎共用一条前端渲染路径，
    也让"没调用模型"这件事可以被断言（duration_ms 只有个位数毫秒）。
    """
    guard.record(dataset_id, question, verdict)
    response = _base_response(dataset_id)
    response["engine"] = "guard"
    response["engine_label"] = "安全闸 · 未调用模型"
    response["answer"] = verdict["reply"]
    response["answer_source"] = "guard"
    response["blocked"] = {
        "category": verdict["category"],
        "label": verdict["label"],
        "reason": verdict["reason"],
        # 规则编号刻意不回传给前端：告诉对方命中了哪条规则，等于附赠绕过说明。
        # 它只进审计表 guard_events，答辩时可以查，但攻击者拿不到。
        "retry_after": verdict.get("retry_after"),
        "suggestions": verdict.get("suggestions") or [],
    }
    response["notice"] = ("这条提问在进入模型之前就被拦下了：没有调用大模型、没有查询评论库、"
                          "也没有产生调用费用。拦截类别与规则编号已写入审计表 guard_events。")
    try:
        response["guard_total"] = guard.stats(limit=1)["total"]
    except Exception:
        response["guard_total"] = None
    response["question"] = question
    response["run_id"] = "grd_" + uuid.uuid4().hex[:10]
    response["duration_ms"] = int((time.time() - started) * 1000)
    _emit(emit, "stage", stage="guard", label="输入安全检查",
          detail=f"命中「{verdict['label']}」· 已拒绝 · 未调用模型")
    _emit(emit, "done", payload=response)
    return response


def chat(question, dataset_id=None, history=None, emit=None, client_key=None):
    """三阶段执行：① 理解与规划 ② 调用受控工具 ③ 流式撰写回答。

    emit 不为空时，每个真实发生的步骤都会立刻推送给调用方（SSE），
    前端因此可以做到"工具步骤逐条点亮、回答逐字出现"，
    而不是等整轮跑完才一次性渲染。

    client_key 用于输入侧频次限制（Web 层传来源 IP；直接调用时不限流）。
    拦截发生在模型之前，但**走同一条返回通道**——前端不需要为"被拒绝"写第二套渲染逻辑。
    """
    started = time.time()
    dataset_id = dataset_id or config.DEFAULT_DATASET_ID
    ds = db.get_dataset(dataset_id)
    if not ds:
        raise analytics.FilterError(f"数据集不存在：{dataset_id}")

    question = (question or "").strip()
    if not question:
        raise AgentError("问题不能为空")

    # ---- 输入侧安全闸（长度 / 频次 / 注入 / 越权 / 套提示词）----
    verdict = guard.inspect(question, client_key=client_key)
    if verdict["action"] == "block":
        return _guard_response(question, dataset_id, verdict, started, emit)

    response = _base_response(dataset_id)
    tool_results = []
    engine = ENGINE_RULES
    notice = ""
    plan_source = ""
    first_token_ms = None

    _emit(emit, "stage", stage="parse", label="理解问题",
          detail=f"{len(question)} 字 · 数据集「{ds['name']}」共 {ds.get('valid_count')} 条有效评论")

    if api_available():
        messages = [{"role": "system", "content": _system_prompt(ds)}]
        for h in (history or [])[-6:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": str(h["content"])[:600]})
        messages.append({"role": "user", "content": question})
        _seen_calls = {}

        try:
            for _ in range(config.AGENT_MAX_TOOL_CALLS + 1):
                if time.time() - started > config.AGENT_MAX_SECONDS:
                    notice = "本轮分析超过时间上限，已返回已完成部分的结果。"
                    break
                _emit(emit, "stage", stage="think", label="模型正在判断需要哪几步",
                      detail=f"可选工具 {len(tools.TOOL_SPECS)} 个 · 已执行 {len(tool_results)} 步")
                msg = _call_llm(messages, max_tokens=300)
                engine = ENGINE_AI
                calls = msg.get("tool_calls") or []
                if not calls:
                    break
                if len(tool_results) + len(calls) > config.AGENT_MAX_TOOL_CALLS:
                    notice = f"工具调用次数已达上限（{config.AGENT_MAX_TOOL_CALLS} 次）。"
                    break
                _emit(emit, "plan", tools=[
                    {"tool": (c.get("function") or {}).get("name"),
                     "label": _STEP_LABELS.get((c.get("function") or {}).get("name"), "")}
                    for c in calls])
                messages.append({"role": "assistant", "content": msg.get("content") or "",
                                 "tool_calls": calls})
                for call in calls:
                    name = (call.get("function") or {}).get("name")
                    raw_args = (call.get("function") or {}).get("arguments") or "{}"
                    # 去重：同名同参的重复调用直接复用上次结果，既省时也不制造重复步骤
                    key = (name, json.dumps(raw_args, ensure_ascii=False, sort_keys=True))
                    deduped = key in _seen_calls
                    if deduped:
                        result = dict(_seen_calls[key], elapsed_ms=0, deduped=True)
                    else:
                        _emit(emit, "tool_start", tool=name,
                              label=_STEP_LABELS.get(name, name),
                              args=_safe_args(raw_args))
                        result = tools.execute(name, raw_args, {"dataset_id": dataset_id})
                        _seen_calls[key] = result
                        tool_results.append(result)
                        _emit(emit, "tool_done", tool=name,
                              label=_STEP_LABELS.get(name, name),
                              ok=bool(result.get("ok")), empty=bool(result.get("empty")),
                              elapsed_ms=result.get("elapsed_ms", 0),
                              summary=result.get("summary") or result.get("reason") or "",
                              args=_safe_args(raw_args))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(_compact_for_llm(result), ensure_ascii=False),
                    })
                continue
        except AgentError as e:
            notice = _ai_error_notice(e)
            _log_ai_error(e)
            engine = ENGINE_RULES
        except requests.exceptions.Timeout as e:
            notice = "AI 响应超时，已切换为规则模式：数字仍来自数据库统计，只是分析变简。诊断码 timeout。"
            _log_ai_error(e)
            engine = ENGINE_RULES
        except Exception as e:
            notice = _ai_error_notice(e)
            _log_ai_error(e)
            engine = ENGINE_RULES

    # ---- 规划阶段没拿到工具结果：用规则路由真实查库 ----
    if not tool_results:
        plan = rules_plan(question, dataset_id)
        for step in plan:
            _emit(emit, "tool_start", tool=step["name"],
                  label=_STEP_LABELS.get(step["name"], step["name"]), args=_safe_args(step["args"]))
            result = tools.execute(step["name"], step["args"], {"dataset_id": dataset_id})
            tool_results.append(result)
            _emit(emit, "tool_done", tool=step["name"],
                  label=_STEP_LABELS.get(step["name"], step["name"]),
                  ok=bool(result.get("ok")), empty=bool(result.get("empty")),
                  elapsed_ms=result.get("elapsed_ms", 0),
                  summary=result.get("summary") or result.get("reason") or "",
                  args=_safe_args(step["args"]))
        if engine == ENGINE_AI:
            notice = "模型未调用工具，已改用规则路由查询真实数据作为回答依据。"
        plan_source = "rules"

    # ---- 2.5 阶段：混合检索原文片段（RAG）----
    # 无论模型有没有主动调用 retrieve_context，这里都保证"手上有一段可引用的原文"：
    # 没有原文，回答就只能是"统计口径的复述"，也就无法给出一句可点开验证的证据。
    rag_result = None
    rag_items = []
    for r in tool_results:
        if r.get("tool") == "retrieve_context" and r.get("ok") and not r.get("empty"):
            rag_result = r.get("data") or {}
            rag_items = rag_result.get("items") or []
            break
    if not rag_items:
        rq = _rag_query(question, tool_results)
        rf = _rag_filters_from(tool_results)
        _emit(emit, "stage", stage="retrieve", label="混合检索原文片段",
              detail="BM25 词项 + 同义扩展 + 字符 n-gram 向量，三路召回后融合重排")
        try:
            rag_result = rag.retrieve(rq, dataset_id, filters=rf, top_k=6)
            rag_items = rag_result.get("items") or []
            if len(rag_items) < 3 and rf:
                # 统计口径过窄（例如同时限定了维度+情感）时，放宽检索范围再来一次，
                # 宁可引用范围更大的原文，也不要因为 0 条而让回答失去依据。
                wide = rag.retrieve(rq, dataset_id, filters={}, top_k=6)
                if len(wide.get("items") or []) > len(rag_items):
                    rag_result = wide
                    rag_items = wide["items"]
                    rag_result["note"] = "结构化筛选命中过少，已放宽到全库检索"
        except Exception as e:
            rag_result = None
            rag_items = []
            notice = (notice + " " if notice else "") + f"原文检索暂不可用：{str(e)[:60]}"
    _emit(emit, "rag_done", count=len(rag_items),
          query=(rag_result or {}).get("query"),
          candidates=(rag_result or {}).get("candidates"),
          elapsed_ms=(rag_result or {}).get("elapsed_ms", 0),
          source=("tool" if any(r.get("tool") == "retrieve_context" for r in tool_results)
                  else "auto"))

    # ---- 第三阶段：撰写回答（同一个模型，但只能看见工具结果 + 检索片段）----
    if engine == ENGINE_AI:
        _emit(emit, "stage", stage="compose", label="基于工具结果撰写回答",
              detail=f"注入 {len(rag_items)} 条原文片段；图表数值由后端计算，模型只写文字，每个数字都会被回溯校验")
        try:
            answer, ttft = stream_completion(
                _compose_messages(ds, question, tool_results, history, rag_items),
                on_token=(lambda d: _emit(emit, "token", text=d)) if emit else None,
            )
            answer = (answer or "").strip()
            if answer:
                response["answer"] = answer
                first_token_ms = int(ttft * 1000)
            else:
                raise AgentError("empty_answer")
        except Exception as e:
            response.update(_rule_answer(question, tool_results))
            engine = ENGINE_RULES
            notice = (notice + " " if notice else "") + \
                     f"回答生成失败，已改用统计口径直接陈述。（{str(e)[:60]}）"
            _emit(emit, "answer_reset", answer=response["answer"],
                  reason="模型回答生成失败，已切换为统计口径的直接陈述")
    else:
        response.update(_rule_answer(question, tool_results))


    # ---- 结构化组装（图表与证据全部由后端生成）----
    response["engine"] = engine
    if engine == ENGINE_AI:
        response["engine_label"] = ("DeepSeek 撰写 · 规则选工具" if plan_source == "rules"
                                    else "DeepSeek 工具调用")
    else:
        response["engine_label"] = "规则路由（仅统计）"
    response["plan_source"] = plan_source or ("model" if engine == ENGINE_AI else "rules")
    response["tool_steps"] = _tool_steps_payload(tool_results)
    response["metrics"] = chart_builder.merge_metrics(tool_results)
    response["charts"] = chart_builder.build_charts(tool_results)
    response["evidence"] = chart_builder.collect_evidence(tool_results)

    best_meta = _pick_richest_meta(tool_results)
    if best_meta:
        response["filters"] = best_meta.get("filters")
        response["filters_text"] = best_meta.get("filters_text")
        response["sample_size"] = best_meta.get("sample_size")
        response["time_span"] = best_meta.get("time_span")
        # S7 对话即筛选：把这轮真实用到的口径编码成可跳转链接
        response["carry"] = _carry_to_views(best_meta, response.get("dataset_id"))

    if not response.get("limitations"):
        response["limitations"] = _default_limitations(response, tool_results)
    if not response.get("followups"):
        response["followups"] = _default_followups(tool_results, question)

    grounding = grounding_check(response.get("answer", ""), tool_results)
    response["grounding"] = grounding
    if engine == ENGINE_AI and len(grounding["unmatched"]) > 2:
        # 模型编了太多对不上的数字 → 用规则结果替换，宁可少说也不说错
        response.update(_rule_answer(question, tool_results))
        # 替换后必须重算：否则 grounding 描述的是已经被丢弃的那段文本，
        # 界面上会出现"回答是规则陈述、角标却写着需复核"的自相矛盾。
        grounding = grounding_check(response.get("answer", ""), tool_results)
        response["grounding"] = grounding
        response["engine_label"] = "规则路由（AI 输出未通过数值校验）"
        response["notice"] = (notice + " " if notice else "") + "AI 回答未通过数值校验，已替换为统计口径的直接陈述。"
        response["answer_source"] = "rule_safety"
        notice = response["notice"]
        _emit(emit, "answer_reset", answer=response["answer"],
              reason="回答中有数字无法在工具结果中溯源，已自动撤回并替换为统计口径的直接陈述")
    else:
        response["answer_source"] = "ai" if engine == ENGINE_AI else "rule"

    # ---- M4 引用核验：撑不住的引用要么改写、要么移除，绝不静默放过 ----
    # 顺序很关键：必须在 grounding 之后、rag 字段组装之前。
    # 放在这之前，后续所有展示（含前端"已引用"标记）看到的都是最终定稿的正文。
    prev_answer = response.get("answer", "")
    _emit(emit, "stage", stage="verify", label="核对引用能否溯源",
          detail=(f"可引用片段 {len(rag_items)} 条 · "
                  f"核验模式 {'硬阻断' if config.CP_CITATION_GUARD else '只记录'}"))
    vres = veracity.enforce(
        prev_answer, rag_items, dataset_id,
        # 规则模式下没有"重写"这回事（文本本身就是统计口径的直接陈述），
        # 传 None 让它直接进入剔除分支，省一次没有意义的模型调用。
        revise=(_revise_answer if engine == ENGINE_AI else None),
    )
    citation = vres["citation"]
    if vres["answer"] != prev_answer:
        response["answer"] = vres["answer"]
        _emit(emit, "answer_reset", answer=vres["answer"],
              reason=("引用复核未通过，已按「只许引用已检索片段」重写"
                      if citation["verdict"] == "revised"
                      else f"{len(citation['removed'])} 处引用无法溯源，已从回答中移除"))
    response["citation"] = citation
    response["citations"] = _legacy_citations(response["answer"], rag_items, dataset_id)

    shown_items = rag_items[:8]          # 回传给前端的片段上限，count 与它保持一致
    response["rag"] = {
        "query": (rag_result or {}).get("query"),
        "filters_relaxed": bool((rag_result or {}).get("note")),
        "count": len(shown_items),
        "items": shown_items,
        "trace": (rag_result or {}).get("trace"),
        "candidates": (rag_result or {}).get("candidates"),
        "stats": (rag_result or {}).get("stats"),
        "elapsed_ms": (rag_result or {}).get("elapsed_ms", 0),
    } if rag_result else {"count": 0, "items": []}

    if rag_items:
        response["limitations"] = list(response.get("limitations") or [])[:2] + [
            f"回答引用的 {len(rag_items)} 条原文片段来自当前数据集快照的混合检索结果，"
            f"不是全网检索，也不代表该说法在全校范围内成立"
        ]

    # 回答离开后端前再脱敏一次：模型有可能复述上下文里的号码/账号串
    response["answer"] = guard.mask_output(response["answer"])
    # verdict 一律在 done 之前发一次，不插在 token 流中间（会打断打字机效果）
    _emit(emit, "verdict",
          verdict=citation["verdict"], label=citation["verdict_label"],
          total=citation["total"], valid=citation["valid"],
          removed=citation["removed"], revised=citation["revised"],
          revised_count=citation["revised_count"], note=citation["note"])
    response["notice"] = notice
    response["first_token_ms"] = first_token_ms
    response["duration_ms"] = int((time.time() - started) * 1000)
    response["question"] = question
    response["run_id"] = "run_" + uuid.uuid4().hex[:10]
    _audit(response)
    _emit(emit, "done", payload=response)
    return response


def _safe_args(raw_args):
    """把模型的原始参数串解析成可展示的 dict；解析失败就原样给出，不抛错。"""
    if isinstance(raw_args, dict):
        return raw_args
    try:
        obj = json.loads(raw_args or "{}")
        return obj if isinstance(obj, dict) else {"raw": str(raw_args)[:120]}
    except Exception:
        return {"raw": str(raw_args)[:120]}


def chat_stream(question, dataset_id=None, history=None, client_key=None):
    """SSE 事件流：把 chat() 的真实执行过程逐条推出去。

    在独立线程里跑（SQLite 用线程本地连接，互不干扰），主线程只负责转发事件，
    因此渲染不会被数据库查询或模型等待阻塞。
    """
    import queue
    import threading

    q = queue.Queue()

    def worker():
        try:
            chat(question, dataset_id, history=history, emit=q.put, client_key=client_key)
        except Exception as e:
            q.put({"type": "error", "message": str(e)[:300]})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        ev = q.get()
        if ev is None:
            break
        yield ev


def _audit(response):
    """审计：记录工具、参数摘要、数据集版本与耗时（不含任何个人信息）。"""
    try:
        db.get_conn().execute(
            """INSERT INTO agent_runs (run_id, dataset_id, question, engine, tool_steps,
                                       ok, error, duration_ms, created_at, tenant_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (response["run_id"], response["dataset_id"], response["question"],
             response["engine"],
             json.dumps([{k: s[k] for k in ("tool", "status", "elapsed_ms")}
                         for s in response["tool_steps"]], ensure_ascii=False),
             1, None, response["duration_ms"], db.now_str(), db.tid()),
        )
        db.get_conn().commit()
    except Exception:
        pass
