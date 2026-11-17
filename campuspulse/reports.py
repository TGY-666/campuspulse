# -*- coding: utf-8 -*-
"""AI 综合报告服务（PAGE-05 R01-R05）。

报告里的所有数字来自 analytics 的真实聚合；模型只负责把数字写成可读的分析与建议。
模型不可用时，用同一批统计生成规则版报告，并在页面明确标注「统计规则生成，非 AI 分析」，
绝不用预写模板冒充 AI 结论（文档 8.2 重要约束）。
"""
import copy
import functools
import json
import re
import uuid

import requests

from . import analytics, config, db


# ============================ AI 结果缓存 ============================
# 为什么必须有：页面是整页跳转的，人在洞察三页之间来回切一次就重新问一次模型。
# 一次分析 4~7 秒、几千到上万 token，而数据一个字都没变——这是纯浪费，
# 也是"页面切回来还要等十秒"的根因。
#
# 键里放 `_data_stamp(dataset_id)`（data_version + kept 条数），而不是"过期时间"：
# 重导入数据、清理评论、换数据集都会换一份缓存，正是"数据变了才重算"的判据；
# 按分钟过期反而会在演示中途突然重算，看起来像随机卡顿。
# 键里同时带 filters_text：同一份数据换一个筛选范围，结论本来就该不同。
_AI_CACHE = {}
_AI_CACHE_MAX = 96


def _ai_key(scope, filters, extra=""):
    ds = filters.get("dataset_id")
    return (db.tid(), scope, ds, analytics._data_stamp(ds),
            analytics.filters_text(filters), extra)


def _ai_cached(scope, extra=None):
    """把"同一份数据、同一批筛选"的重复提问挡在模型外面。

    只缓存 `engine == 'deepseek'` 的结果：模型不可用时会降级成规则版，
    那份结果不该被记住——否则接口恢复了、页面还一直显示"AI 分析不可用"。

    `refresh=True` 明确要求重算（驾驶舱的「重新生成」按钮）：用户点它就是
    想看新的一份，命中缓存会让按钮看起来坏了。
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(filters, *args, refresh=False, **kwargs):
            key = _ai_key(scope, filters,
                          extra(filters, *args, **kwargs) if extra else "")
            if not refresh:
                hit = _AI_CACHE.get(key)
                if hit is not None:
                    out = copy.deepcopy(hit)
                    out["cached"] = True
                    return out
            out = fn(filters, *args, **kwargs)
            if out.get("engine") == "deepseek":
                keep = copy.deepcopy(out)
                keep.pop("cached", None)
                if len(_AI_CACHE) >= _AI_CACHE_MAX:
                    _AI_CACHE.clear()       # 与 analytics 的两份缓存同样的粗暴上限
                _AI_CACHE[key] = keep
            return out
        return wrapper
    return deco


def _load_env_key():
    return config.DEEPSEEK_API_KEY


def _llm_json(prompt, timeout=None, max_tokens=1200, system=None):
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("missing_api_key")
    url = f"{config.DEEPSEEK_BASE_URL}/v1/chat/completions"
    messages = []
    if system:
        # 角色与硬约束放 system：四个入口的稳定前缀，也方便供应商做前缀缓存
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": max_tokens,
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
    start = content.find("{")
    if start == -1:
        raise RuntimeError("no_json")
    # 只解出**第一个**完整对象。以前是 find("{") + rfind("}") 拼首尾，模型偶尔
    # 会在答案后面再补一段 JSON（或先写一个示例再写正式结果），首尾一拼就成了
    # 两段对象，报 "Extra data: line 3 column 1"，整段分析降级成规则版——
    # 页面显示"AI 分析暂不可用"，而模型其实已经答完了。
    try:
        obj, _ = json.JSONDecoder().raw_decode(content[start:])
    except ValueError:
        raise RuntimeError("bad_json")
    if not isinstance(obj, dict):
        raise RuntimeError("bad_json")
    return obj


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
    topics = analytics.stats_topics(filters, len(config.DIMENSIONS))
    sentiment = analytics.stats_sentiment(filters)
    trend = analytics.stats_trend(filters)
    platforms = analytics.stats_platform_compare(filters)
    return {
        "overview": overview, "topics": topics, "sentiment": sentiment,
        "trend": trend, "platforms": platforms,
    }


def _term(prompt):
    """给提示词补一句对外术语约束。

    单独抽出来是因为四个入口（报告 / 驾驶舱简述 / 子页分析 / 词云解读）各有一份提示词，
    术语要求只能在调用处统一追加——复制进每份文本里，改一次漏三处。
    漏了的表现很具体：界面写"主题热度"，AI 那段话写"最热维度是宿舍住宿…各维度中负面集中度较高"。
    """
    return config.TERM_RULE + "\n\n" + prompt


def _compact_facts(facts, pool):
    o = facts["overview"]
    buckets = [b for b in facts["trend"]["buckets"] if b["total"] > 0]
    ranking = facts["topics"]["ranking"]
    worst = _worst_dim(ranking, o["total"]) if ranking else None
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
        "主题排行（多标签）": [(r["dimension"], r["count"], f"{r['negative_rate']}%负面")
                              for r in ranking],
        "最热主题": ranking[0]["dimension"] if ranking else "—",
        "负面率最高主题": ({"主题": worst["dimension"], "负面率": f"{worst['negative_rate']}%",
                            "提及": worst["count"]} if worst else None),
        "负面原因": [(r["reason"], r["count"]) for r in facts["topics"]["negative_reasons"][:6]],
        "正面卖点": [(r["point"], r["count"]) for r in facts["topics"]["positive_points"][:6]],
        "平台对比": [{k: p[k] for k in ("platform", "total", "positive_rate", "negative_rate", "like_avg")}
                     for p in facts["platforms"]["platforms"]],
        "趋势峰值": sorted(buckets, key=lambda b: -b["total"])[:3],
        "趋势粒度": facts["trend"]["granularity"],
        "候选证据": [{"编号": e["ref"], "主题": e["theme"], "情感": e["sentiment_label"],
                      "平台": e["platform"], "日期": e["date"], "摘要": e["snippet"]}
                     for e in pool],
    }


_REPORT_SYSTEM = """你是高校舆情分析报告撰写助手，读者是学校宣传与舆情工作人员。
硬性约束：
1. 只能使用用户消息「统计事实」里出现的数字、日期、主题名、平台名、证据编号与评论内容；不得新增、推算、换算或编造任何数字，也不得把不同口径的数字相加。
2. evidence_refs 只能引用「候选证据」里真实存在的编号；找不到能支撑结论的证据就留空数组，不要把无关评论硬挂上去。
3. 不得断言评论中的指控已核实，不得判断或推测任何个人身份，不得宣称覆盖全网或实时监测。
4. 结论必须由事实支撑；事实不足时直接写「样本不足以判断」，不要补常识性推测。
5. 只输出 JSON 对象，不要 markdown，不要在 JSON 之外写任何文字。"""

_PROMPT = """请依据下面的统计事实写一份简报。按「钩子 → 背景 → 三个关键发现 → 建议 → 口径」组织：

1. headline 是全文钩子：一句话点出具体对象与方向（哪个主题、什么矛盾），24-32 字；
   不要写「整体态势平稳」这类换一份数据也成立的话。
2. summary 是背景：2-3 句讲清样本规模、情感基线，以及这份数据最值得注意的地方；
   数据集名称、时间范围、筛选口径页头已有，正文不要重复罗列。
3. findings 是正文骨架，输出 3 条（数据确实撑不起 3 条时输出 2 条），按重要性排序：
   ① 讨论最集中的主题是什么，规模与情感构成如何；
   ② 最热主题是否同时是负面最集中的主题——相同就直接点明，不同就指出「最热不等于最不满意」的错位，
      这是全文最关键的判断，用一句话说透，不要两头都说；
   ③ 平台或时间上的结构性差异说明了什么。
   每条写 title（12 字内）和 text（2-3 句，先给判断再给依据）；
   evidence_refs 从「候选证据」里选 1-2 个真正能支撑该条判断的编号，选不出就留空数组。
4. suggestions 是行动收尾，输出 4 条，覆盖招生宣传、服务改进、舆情响应、内容运营；
   每条写清「谁、在什么时间、做什么」，并能在事实里找到依据，
   禁止「加强沟通」「提升满意度」这类放到哪份报告都成立的空话；
   evidence_refs 同样只能引用候选证据编号，没有合适的就留空数组。
5. cautions 写 2-4 条口径限制与需人工核实的点。

只输出 JSON：
{{
  "headline": "一句话结论，24-32 字，含具体对象与方向",
  "summary": "背景说明，2-3 句",
  "findings": [{{"title": "发现小标题，12 字内", "text": "判断 + 依据，2-3 句", "evidence_refs": ["证据编号"]}}],
  "suggestions": [{{"title": "动作主题，12 字内", "content": "谁+何时+做什么，1-2 句", "basis": "对应的事实", "evidence_refs": ["证据编号"]}}],
  "cautions": ["需要人工核实或口径限制的提示"]
}}

统计事实：
{facts}
"""


# ============================ 模型输出校验 ============================
_NUM_CLAIM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:条|%)")


def _canon_number(s):
    """统一数字写法：去掉千分位与多余的 .0。

    「15」「15.0」「1,979」是同一个数，模型选哪种写法都可能；不统一的话
    一个格式差异就会被判成"编造数字"，把正常报告降级掉。
    """
    s = str(s).replace(",", "").strip()
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _fact_number_tokens(facts):
    """事实底稿里出现过的所有数字，作为模型输出的核对白名单。"""
    tokens = set()

    def walk(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            tokens.add(_canon_number(int(v) if float(v).is_integer() else v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", v):
                tokens.add(_canon_number(m.group()))

    walk(facts)
    return tokens


def _unverified_numbers(body, allowed):
    """核对报告里带单位的硬指标（N 条 / N%）。

    模型最危险的错误不是观点写偏，而是写一个数据里不存在的数字——
    它读起来和真数字一模一样，人工复核也未必能发现。只核对「N 条」「N%」，
    日期、年份等叙述性数字不在此列。返回无法在事实中找到的片段。
    """
    texts = [body["headline"], body["summary"]]
    texts += [f"{f['title']}：{f['text']}" for f in body["findings"]]
    texts += [f"{s['title']}：{s['content']}（{s['basis']}）" for s in body["suggestions"]]
    bad = []
    for t in texts:
        for m in _NUM_CLAIM.finditer(t or ""):
            if _canon_number(m.group(1)) not in allowed:
                bad.append(m.group(0).strip())
    return sorted(set(bad))


def _clean_refs(value, allowed, limit=2):
    """只保留候选证据池里真实存在的编号，顺带去重、限量。

    模型会凭印象写编号（CMT- 后面的串错一位），这种引用比没有引用更糟：
    界面上点不出对应评论，读者会以为证据丢了。宁可少引用，不留假引用。
    """
    refs = []
    for x in (value or []):
        ref = str(x).strip()
        if ref in allowed and ref not in refs:
            refs.append(ref)
        if len(refs) == limit:
            break
    return refs


def _report_body(raw, facts, pool_refs):
    """把模型 JSON 收口成前端可直接渲染的结构。

    不合格返回 None 交给规则版：缺结论、建议不足两条、关键发现少于两条都算不合格。
    半份 AI 报告比明确降级更难解释，也更容易被当成完整分析。
    """
    if not isinstance(raw, dict):
        return None
    summary = _finish(str(raw.get("summary") or "").strip(), 230)
    if not summary:
        return None

    findings, seen = [], set()
    for f in (raw.get("findings") or []):
        if not isinstance(f, dict):
            continue
        text = _finish(str(f.get("text") or "").strip(), 170)
        if not text or text in seen:
            continue
        seen.add(text)
        findings.append({
            "title": _finish(str(f.get("title") or "").strip(), 16) or "关键发现",
            "text": text,
            "evidence_refs": _clean_refs(f.get("evidence_refs"), pool_refs),
        })
        if len(findings) == 3:
            break
    if len(findings) < 2:
        return None
    # 一条对不上证据都没有的“发现”，读者无法核对，按不合格处理
    if len(pool_refs) >= 3 and not any(f["evidence_refs"] for f in findings):
        raise RuntimeError("no_evidence_refs")

    suggestions, seen = [], set()
    for s in (raw.get("suggestions") or []):
        if not isinstance(s, dict):
            continue
        title = _finish(str(s.get("title") or "").strip(), 20)
        content = _finish(str(s.get("content") or "").strip(), 130)
        key = (title, content)
        if not content or key in seen:
            continue
        seen.add(key)
        suggestions.append({
            "title": title or "建议", "content": content,
            "basis": _finish(str(s.get("basis") or "").strip(), 90),
            "evidence_refs": _clean_refs(s.get("evidence_refs"), pool_refs, 1),
        })
        if len(suggestions) == 4:
            break
    if len(suggestions) < 2:
        return None

    cautions = [_finish(str(c).strip(), 90) for c in (raw.get("cautions") or [])
                if str(c).strip()][:4]
    body = {
        "headline": _finish(str(raw.get("headline") or "").strip(), 34)
                    or _finish(summary.split("。")[0], 34),
        "summary": summary,
        "findings": findings,
        "suggestions": suggestions,
        "cautions": cautions,
    }
    bad = _unverified_numbers(body, _fact_number_tokens(facts))
    if bad:
        raise RuntimeError("unverified_numbers:" + "、".join(bad[:3]))
    return body


def _worst_dim(ranking, total):
    """负面率最高的主题，但样本量太小的不参与评比。

    30 条样本里出现 15 条负面就是 50%，比任何大主题都高——这个数字写进解读，
    读者会以为"校园活动是最差的一环"，实际只是抽到的样本太少。门槛取
    max(15, 全量 1.2%)，和 _page_facts 里那两处保持一致。
    """
    if not ranking:
        return {"dimension": "—", "count": 0, "negative_rate": 0}
    floor = max(15, int(total * 0.012))
    cand = [r for r in ranking if r["count"] >= floor] or ranking
    return max(cand, key=lambda r: r["negative_rate"])


def _hot_platform(facts):
    """负面率最高的平台，样本太少的不参与。"""
    total = facts["overview"]["total"]
    floor = max(20, total // 100)
    plats = [p for p in facts["platforms"]["platforms"] if p["total"] >= floor]
    return max(plats, key=lambda p: p["negative_rate"]) if plats else None


def _evidence_pool(filters, facts):
    """候选证据池：先覆盖结论会落到的位置，再补全量高赞样本。

    以前取的是全库点赞前 6 条，模型写结论时看不到它们，读者也没法拿它们
    核对结论。现在按「最热主题 / 负面最集中主题 / 负面率最高平台」配额取样，
    每条带编号交给模型引用，界面再把引用还原成评论。
    """
    items, seen = [], set()
    ranking = facts["topics"]["ranking"]
    top = ranking[0] if ranking else None
    worst = _worst_dim(ranking, facts["overview"]["total"]) if ranking else None
    hot_plat = _hot_platform(facts)

    def add(tag, limit=2, **over):
        if len(items) >= 12:
            return
        f2 = dict(filters)
        f2.update(over)
        try:
            got = analytics.search_comments(f2, limit=limit, order="likes",
                                            snippet_len=96)["items"]
        except Exception:
            return
        for e in got:
            ref = e["comment_ref"]
            if ref in seen or len(items) >= 12:
                continue
            seen.add(ref)
            items.append({
                "ref": ref, "tag": tag,
                "theme": "、".join(e.get("dimensions") or [e.get("dimension") or "—"]),
                "sentiment": e["sentiment"], "sentiment_label": e["sentiment_label"],
                "platform": e["platform"], "date": e["date"],
                "likes": e["like_count"], "snippet": e["snippet"],
            })

    if top:
        add("最热主题·负面", dimensions=[top["dimension"]], sentiments=["negative"])
        add("最热主题·正面", 1, dimensions=[top["dimension"]], sentiments=["positive"])
    if worst and (not top or worst["dimension"] != top["dimension"]):
        add("负面集中主题·负面", dimensions=[worst["dimension"]], sentiments=["negative"])
    if hot_plat:
        add("高负面平台·负面", platforms=[hot_plat["platform"]], sentiments=["negative"])
    add("负面样本", 2, sentiments=["negative"])
    add("全量高赞", 3)
    return items


def _rule_report(facts, pool):
    o = facts["overview"]
    t = facts["topics"]
    ranking = t["ranking"]
    top = ranking[0] if ranking else {"dimension": "无", "count": 0, "negative_rate": 0, "share": 0}
    neg_dim = _worst_dim(ranking, o["total"])
    neg_reason = t["negative_reasons"][0] if t["negative_reasons"] else {"reason": "无", "count": 0}
    pos_point = t["positive_points"][0] if t["positive_points"] else {"point": "无", "count": 0}
    hot_plat = _hot_platform(facts)
    buckets = [b for b in facts["trend"]["buckets"] if b["total"] > 0]
    peak = max(buckets, key=lambda b: b["total"]) if buckets else None
    rates = [("正面", o["positive_rate"]), ("中性", o["neutral_rate"]), ("负面", o["negative_rate"])]
    lead = max(rates, key=lambda x: x[1])

    def refs(*tags, limit=2):
        out = []
        for e in pool:
            if e["tag"] in tags and e["ref"] not in out:
                out.append(e["ref"])
            if len(out) == limit:
                break
        return out

    same = top["dimension"] == neg_dim["dimension"]
    reason_text = ("当前范围没有可归因的负面样本。" if neg_reason["reason"] == "无" else
                   f"负面原因中「{neg_reason['reason']}」最集中（{neg_reason['count']} 条）")
    if peak:
        reason_text += f"；评论量峰值出现在 {peak['bucket']}（{peak['total']} 条）。"
    else:
        reason_text += "。"
    return {
        "headline": _finish(
            (f"「{top['dimension']}」讨论最多，负面也最集中（{neg_dim['negative_rate']}%）"
             if same else
             f"「{top['dimension']}」讨论最多，负面集中在「{neg_dim['dimension']}」"), 34),
        "summary": (
            f"当前范围共 {o['total']} 条有效评论，"
            + (f"时间覆盖 {o['time_span'][0]} 至 {o['time_span'][1]}，"
               if len([s for s in (o.get("time_span") or []) if s]) >= 2 else "")
            + f"情感结构以{lead[0]}为主（{lead[1]}%）。正面 {o['positive_rate']}%、"
            f"负面 {o['negative_rate']}%（分母 {o['denominator']} 条），"
            f"讨论最集中在「{top['dimension']}」。"
        ),
        "findings": [
            {"title": "讨论最集中的主题",
             "text": (f"「{top['dimension']}」被 {top['count']} 条评论提及（多标签计数，"
                      f"占有效评论 {top['share']}%），负面率 {top['negative_rate']}%；"
                      f"这是当前范围里声量最大的主题。"),
             "evidence_refs": refs("最热主题·负面", "最热主题·正面", "全量高赞")},
            {"title": "热度与负面是否错位",
             "text": (
                 f"最热主题同时也是负面占比最高的主题（{neg_dim['negative_rate']}%），"
                 f"既最受关注也最不满意。"
                 if same else
                 f"负面占比最高的是「{neg_dim['dimension']}」（{neg_dim['negative_rate']}%），"
                 f"不是讨论最多的「{top['dimension']}」；最热不等于最不满意。"),
             "evidence_refs": refs("负面集中主题·负面", "最热主题·负面", "负面样本")},
            {"title": "平台差异" if hot_plat else "负面原因分布",
             "text": (
                 f"平台负面率最高的是「{hot_plat['platform']}」（{hot_plat['total']} 条，"
                 f"负面 {hot_plat['negative_rate']}%），与整体 {o['negative_rate']}% 相比，"
                 f"差异更可能来自平台人群与使用场景，不能直接当成全校态度。"
                 if hot_plat else reason_text),
             "evidence_refs": refs("高负面平台·负面", "负面样本")},
        ],
        "suggestions": [
            {"title": "招生宣传", "content": f"把「{pos_point['point']}」作为核心卖点，在招生季集中投放真实体验内容。",
             "basis": f"正面卖点统计：{pos_point['point']} {pos_point['count']} 条",
             "evidence_refs": refs("最热主题·正面", "全量高赞", limit=1)},
            {"title": "服务改进", "content": f"优先处理「{neg_reason['reason']}」相关诉求，公开整改进度。",
             "basis": f"负面原因统计：{neg_reason['reason']} {neg_reason['count']} 条",
             "evidence_refs": refs("负面集中主题·负面", "负面样本", limit=1)},
            {"title": "舆情响应", "content": f"以「{neg_dim['dimension']}」为监测重点，对高赞负面内容建立巡检机制。",
             "basis": f"该主题负面占比 {neg_dim['negative_rate']}%，为样本量达标的主题中最高",
             "evidence_refs": refs("负面集中主题·负面", "高负面平台·负面", limit=1)},
            {"title": "内容运营", "content": f"围绕「{pos_point['point']}」制作在校生真实体验内容，保持正面口碑的持续供给。",
             "basis": f"正面卖点统计：{pos_point['point']} {pos_point['count']} 条",
             "evidence_refs": refs("最热主题·正面", "全量高赞", limit=1)},
        ],
        "cautions": [
            "情感与主题均为关键词规则标注，存在误判可能，重要结论需人工抽样复核。",
            "本报告基于历史采集快照，不代表全网实时声量。",
            "评论中提及的具体问题未经核实，不应直接作为事实对外发布。",
        ],
    }


def generate(filters, force_rule=False):
    facts = _facts(filters)
    o = facts["overview"]
    pool = _evidence_pool(filters, facts)
    pool_refs = {e["ref"] for e in pool}
    engine, ai_error = "rules", ""

    body = None
    if not force_rule and config.DEEPSEEK_API_KEY:
        try:
            compact = _compact_facts(facts, pool)
            prompt = _PROMPT.format(facts=json.dumps(compact, ensure_ascii=False, indent=1))
            raw = _llm_json(prompt, system=_term(_REPORT_SYSTEM), max_tokens=1600)
            body = _report_body(raw, compact, pool_refs)
            if body:
                engine = "deepseek"
        except Exception as e:
            ai_error = str(e)[:120]

    if body is None:
        body = _rule_report(facts, pool)
        engine = "rules"

    unverified = ai_error.startswith("unverified_numbers")
    unanchored = ai_error.startswith("no_evidence_refs")
    report = {
        "report_id": "rpt_" + uuid.uuid4().hex[:10],
        "generated_at": db.now_str(),
        "engine": engine,
        "engine_label": "DeepSeek 生成" if engine == "deepseek" else
                        ("统计规则生成（AI 未引用可核对证据）" if unanchored else
                         "统计规则生成（AI 输出未通过数字校验）" if unverified
                         else "统计规则生成（AI 不可用）"),
        "is_ai_generated": engine == "deepseek",
        "ai_error": ai_error,
        "notice": "" if engine == "deepseek" else
                  ("AI 报告没有引用任何可核对的评论证据，已降级为统计规则版。"
                   if unanchored else
                   "AI 报告未通过数字核对：正文含统计事实中找不到的数字，已降级为统计规则版。"
                   if unverified else
                   "AI 报告暂不可用，以下内容由统计规则根据同一批真实数据生成，不是大模型分析结果。"),
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
            "total": o["total"], "positive": o["positive"],
            "positive_rate": o["positive_rate"],
            "neutral_rate": o["neutral_rate"], "negative_rate": o["negative_rate"],
            "negative": o["negative"], "denominator": o["denominator"],
            "total_likes": o["total_likes"], "top_dimension": o["top_dimension"],
            "platforms": o["platforms"],
        },
        "content": body,
        # 证据整池下发：正文里的 evidence_refs 要能在前端还原成评论，
        # 只给“被引用的”反而会让读者看不到未引用的反例。
        "evidence": [{
            "comment_ref": e["ref"], "platform": e["platform"], "date": e["date"],
            "sentiment_label": e["sentiment_label"], "snippet": e["snippet"],
            "theme": e["theme"],
        } for e in pool],
        "chart_data": {
            "dimensions": [{"name": r["dimension"], "value": r["count"]} for r in facts["topics"]["ranking"]],
            "sentiment": facts["sentiment"]["distribution"],
            "platform": [{"name": p["platform"], "total": p["total"],
                          "negative": p["negative"]} for p in facts["platforms"]["platforms"]],
        },
        "limitations": [
            o["meta"]["snapshot_note"],
            "情感/主题标签由关键词规则生成，非人工标注，重要结论需抽样复核。",
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
            summary, kpi, payload = "", {}, {}
        # 数据版本要跟着列表一起出去：三份报告的时间范围可能都写着"全量历史"，
        # 但有效评论数是 1,969 / 1,775 / 1,803——差别来自生成时刻的数据版本，
        # 不是同一批数据算了两遍。不把这列摆出来，读者只能理解成统计前后矛盾。
        out.append({"report_id": r["report_id"], "dataset_id": r["dataset_id"],
                    "engine": r["engine"], "created_at": r["created_at"],
                    "data_version": (payload.get("dataset") or {}).get("data_version") or "",
                    "summary": summary, "kpi": kpi})
    return out


def get(report_id):
    row = db.query_one("SELECT payload FROM reports WHERE report_id = ? AND tenant_id = ?",
                       (report_id, db.tid()))
    return json.loads(row["payload"]) if row else None


# ============================ 驾驶舱 AI 简述（D07）============================
_INSIGHT_PROMPT = """你是高校舆情分析助手。请根据下面的真实统计，用 2-4 句中文写一段舆情简述，
面向学校宣传与舆情工作人员。要求：只用给出的数字，不编造；先讲总体，再讲最热主题与负面集中点；
不要 markdown、不要标题、不要分点，直接输出一段话。

统计：
{facts}
"""


def _rule_insight(facts, sent_filtered=False):
    o, t, p = facts["overview"], facts["topics"], facts["platforms"]
    top = t["ranking"][0] if t["ranking"] else {"dimension": "—", "count": 0, "negative_rate": 0}
    neg_dim = _worst_dim(t["ranking"], o["total"])
    reason = t["negative_reasons"][0] if t["negative_reasons"] else None
    plat_txt = "、".join(f"{x['platform']} {x['total']} 条" for x in p["platforms"]) or "暂无"
    if sent_filtered:
        # 筛了情感就别再念"正面 0%、负面 100%"——那是筛选的结果，不是结论。
        # 这段要说的是"这一块有多大、集中在哪"。
        scope = o.get("scope_total")
        share = _share(o["total"], scope)
        text = (f"当前筛选下共 {o['total']} 条评论"
                + (f"，占同口径全量 {scope} 条的 {share}%" if scope else "")
                + f"。这些评论里讨论最集中的主题是「{top['dimension']}」（{top['count']} 条提及），"
                f"平台分布：{plat_txt}。")
        if reason:
            text += f"负面原因中「{reason['reason']}」最集中（{reason['count']} 条）。"
        return text
    text = (f"当前数据集共 {o['total']} 条有效评论，正面 {o['positive_rate']}%、负面 {o['negative_rate']}"
            f"（分母 {o['denominator']} 条），整体以中性讨论为主。"
            f"讨论最集中的主题是「{top['dimension']}」（{top['count']} 条提及），"
            f"负面占比最高的是「{neg_dim['dimension']}」（{neg_dim['negative_rate']}%）。"
            f"平台分布：{plat_txt}。")
    if reason:
        text += f"负面原因中「{reason['reason']}」最集中（{reason['count']} 条）。"
    return text


def _share(part, total):
    return round(part / total * 100, 1) if total else 0.0


@_ai_cached("chart")
def chart_insights(filters):
    """数据洞察页的图表解读：一次调用，返回情感/主题/时间/平台四条短解读。"""
    facts = _facts(filters)
    o = facts["overview"]
    if o["empty"]:
        return {"items": [], "engine": "rules", "engine_label": "无数据", "notice": ""}

    topics = facts["topics"]["ranking"]
    top = topics[0] if topics else {"dimension": "—", "count": 0, "negative_rate": 0}
    neg_dim = _worst_dim(topics, o["total"])
    neg_reason = facts["topics"]["negative_reasons"][0] if facts["topics"]["negative_reasons"] else None
    pos_point = facts["topics"]["positive_points"][0] if facts["topics"]["positive_points"] else None
    buckets = [b for b in facts["trend"]["buckets"] if b["total"] > 0]
    peak = max(buckets, key=lambda b: b["total"]) if buckets else None
    plats = facts["platforms"]["platforms"]

    items = [
        {"key": "sentiment", "chart": "情感分布",
         "text": f"有效评论 {o['total']} 条中，正面 {o['positive_rate']}%、负面 {o['negative_rate']}%，"
                 f"分母为 {o['denominator']} 条。中性占比 {o['neutral_rate']}%，说明多数讨论是中性陈述而非评价。"},
        {"key": "dimension", "chart": "八主题与负面原因",
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


# ==================== 洞察子页 AI 分析（主题 / 情感 / 时间） ====================
# 和 chart_insights 的分工：
#   chart_insights 是「一张图配一句话」，本质是复述数字；
#   这里要回答的是「为什么」——热度为什么最高、痛点指向哪里、峰值为什么长这样。
# 光给排行数字推不出成因，所以把这些主题/情感桶下的代表性评论也一并交给模型，
# 并要求它只能用"可能与…有关"这类推断措辞，且一个数字都不许新增。

_PAGE_PROMPT = {
    "topics": """你是高校舆情分析助手。下面是「主题热度」页当前筛选范围内的真实统计：
主题排行，以及把镜头推进到最热主题内部之后的构成（情感、平台、高频词、评论集中在哪些时段），
外加该主题下按点赞排序的代表性评论（已脱敏）。

请只输出 JSON，不要 markdown、不要在 JSON 之外写任何文字：

{
  "headline": "一句话结论，30 字内。必须同时点出是哪个主题、它靠什么话题撑起来的，不要写「住宿满意度最高」这种空话",
  "summary": "80-120 字。把关键数字说清楚：这一主题多少条提及、占全部评论多少、负面率多少；并与第二名做对照（差多少条、是不是断层式领先）",
  "reasons": [
    {"point": "成因小标题，12 字内", "detail": "70-100 字。先给依据，再给解释。依据必须具体到：评论区反复出现的某个说法、某个高频词、某个平台的条数占比、或评论集中在某几个时段的哪个；解释用「可能与…有关」「这通常指向」这类措辞，明确是推断"}
  ],
  "next": "还可以继续分析的一个具体角度，30 字内"
}

reasons 必须正好 2-3 条，按下面的角度各写一条（没有对应事实就跳过该角度，不要硬编）：
① 触发时间：这个主题的讨论为什么集中在「评论最集中的时段」里那几个时段/月份。
   驱动它的事件只能从两处来——事实里的时段标签本身，或代表性评论的原文。
   不要在事实之外补一个"开学/招生/军训"之类的常识性推测，那套说法只对特定数据集成立；
② 内容关切：从高频词和代表性评论看，学生在这个主题里到底在问什么、比什么、抱怨什么；
③ 平台与情绪：这个主题在哪个平台更集中；如果它的负面率明显高于全范围负面率，
   必须明确指出「最热不等于最满意」这个矛盾，并给出可能的原因。

硬性要求：只能使用下面给出的事实与评论原文，不得新增、推算或编造任何数字、日期、评论内容；
summary 和 reasons 里出现的主题名、平台名、时段标签，都必须是事实中**已经存在**的字符串，
不要用同义词改写，也不要引入事实里没有的业务分类；
不要把 summary 里的数字在 reasons 里原样重复一遍，reasons 要往前推一步，解释"为什么"；
如果事实中的样本只覆盖单一平台、单一主题或时间跨度很短，必须在 summary 里点明这一点，
不要用"全校/全网"的口气去概括；
如果某类事实为空就跳过，绝不假装有数据。

事实：
{{FACTS}}""",

    "sentiment": """你是高校舆情分析助手。下面是「情感分析」页当前范围的真实统计：
正/中/负占比、负面原因分布、负面命中哪些主题、各平台的负面率与情感构成、
正负两朵词云的高频词，以及两个情感桶里按点赞排序的代表性评论（已脱敏）。

请只输出 JSON，不要 markdown、不要在 JSON 之外写任何文字：

{
  "headline": "一句话结论，30 字内。要概括当前情感结构的特征，例如「整体偏正面，但不满集中在住宿成本上」，不要只报一个负面率",
  "summary": "80-120 字。给出正/中/负占比与负面条数；说明负面主要落在哪个主题、哪个平台负面率最高、与整体相比高多少；说明正面评价主要集中在哪几点",
  "reasons": [
    {"point": "成因小标题，12 字内", "detail": "70-100 字。先给依据（负面高频词、负面原因条数、评论原话片段），再解释为什么会形成这种情绪"}
  ],
  "suggestions": [
    {"title": "动作主题，12 字内", "content": "谁 + 在什么时间 + 做什么，1-2 句"}
  ],
  "next": "还可以继续分析的一个具体角度，30 字内"
}

reasons 必须正好 2-3 条，按下面的角度各写一条（没有对应事实就跳过，不要硬编）：
① 不满的根源：负面原因排行和负面词云共同指向什么——是价格与预期落差、服务响应慢、
   信息不对称，还是硬件本身；要说出这种情绪为什么会被放大
   （例如同类抱怨在同一平台聚集、高赞评论形成示范）。
   具体是哪一类，必须由"负面原因"和"负面高频词"里的内容决定，不要预设；
② 正面为何是这些点：正面高频词对应的是真实体验还是宣传口径，说明哪些做法被学生真正感知到了；
③ 结构性差异：如果某个主题或某个平台的负面率明显高于整体，解释这种差异可能来自哪类人群或哪种使用场景。

suggestions 必须输出 2-3 条，是给值班负责人的处置建议（需人工审核）：
每条写清"谁、在什么时间、做什么"，并对应到事实里的某个负面原因或负面率最高的主题；
禁止「加强沟通」「提升满意度」这类放到哪份报告都成立的空话。

硬性要求：只能使用给定事实与评论原文，不得新增或编造数字、评论；
负面原因里「未细分原因」的条数不要当成某一类问题来讲；建议必须落到"谁、在什么时间、做什么"，不要写"加强沟通"这类空话；
建议必须能追溯到事实里的某个主题或某个负面原因，不要给出与本数据集无关的通用建议；
如果事实中的样本只覆盖单一平台或时间跨度很短，要在 summary 里点明样本局限；
如果负面样本为空，就说明当前范围内没有可归因的负面讨论，不要硬凑痛点。

事实：
{{FACTS}}""",

    "timeline": """你是高校舆情分析助手。下面是「时间趋势」页当前范围的真实统计：
24 小时活跃度、周内分布、按时间聚合的评论量与负面率、工作日与周末对比、
各平台自己的活跃时段，以及峰值那几个小时的高赞评论摘要（已脱敏）。

请只输出 JSON，不要 markdown、不要在 JSON 之外写任何文字：

{
  "headline": "一句话结论，30 字内。说清峰值长什么样——一天的高峰、一周的高峰、按时间聚合的最高点，挑最突出的那个讲",
  "summary": "80-120 字。给出一天最活跃时段与条数、周内最多的一天与条数、按时间聚合评论量最高的时段；如果「负面率最高的时段」与「评论量最高的时段」不是同一个，必须点出这个错位",
  "reasons": [
    {"point": "成因小标题，12 字内", "detail": "70-100 字。先给依据（峰值时段的评论在谈什么、某平台晚间占比多少、工作日与周末差多少），再解释成因"}
  ],
  "next": "还可以继续分析的一个具体角度，30 字内"
}

reasons 必须正好 2-3 条，优先用「峰值时段评论」这批样本说话，按下面的角度各写一条：
① 作息节律：把峰值时段和峰值评论里的人在做什么对起来（刚下课、在等外卖、准备熄灯），
   依据只能是评论原文，不要拿"学生作息"这种先验去套；
② 平台机制：结合「各平台活跃时段」和晚间占比，判断峰值是不是某一个平台推起来的；
   如果两个平台的峰值时段不同，指出这是"人群不同"而不是"全校同时活跃"；
③ 事件驱动：结合「评论量最高的时段」和「负面率最高的时段」，判断有没有某段时间是集中爆发，
   如果是，对应的事件只能从峰值评论原文里找；原文里找不到就写"样本不足以判断是哪件事"，
   不要补一个假期、考试、开学之类的通用解释。

硬性要求：数字只能来自给出的事实，不得新增或编造；无采集记录的时段不能当成"没有讨论"；
如果「峰值时段评论」为空，就直接说"样本不足以支撑归因"，不要编造评论内容；
如果数据只有很短的时间跨度或只有单一平台，要在 summary 里点明，不要用"常年/全天候"的口气；
不要复述 summary 已经说过的数字，reasons 要解释"为什么是这个形状"。

事实：
{{FACTS}}""",
}


def _clip_text(text, limit=190):
    """按字数收口，优先在句末断开。

    模型偶尔会写到 250 字以上，直接硬切会出现「……因此建」这种半句话，
    反而比短一点更糟；所以先回退到最近的一个句末标点。
    """
    t = " ".join(str(text or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit]
    for i in range(len(cut) - 1, max(0, limit - 60), -1):
        if cut[i] in "。！？；":
            return cut[:i + 1]
    return cut.rstrip("，、；：, ") + "…"


def _finish(text, limit=190):
    """收口 + 收尾。模型常以分号结束一段话，读起来像还有下文却没写，
    这里把悬空的分号/顿号换成句号。"""
    t = _clip_text(text, limit)
    if t and t[-1] in "；;、，,":
        t = t[:-1] + "。"
    return t


def _samples(f, limit=6, **over):
    """取代表性评论：只给摘要、平台、情感、点赞，不暴露任何可识别信息。"""
    f2 = dict(f)
    f2.update(over)
    return [{"赞": s["like_count"], "平台": s["platform"], "情感": s["sentiment_label"],
             "摘要": s["snippet"]}
            for s in analytics.search_comments(f2, limit=limit, order="likes", snippet_len=64)["items"]]


def _page_facts(f, page):
    """三个子页各自的事实底稿。字段名用中文，是为了让模型少犯"字段理解"错误。"""
    o = analytics.stats_overview(f)
    if o.get("empty"):
        return {"empty": True}

    # 主题负面率的比较门槛：至少要有全量 1.2% 的样本，且不少于 15 条。
    # 小主题（比如只有二十来条的校园活动）挂一个 38% 的负面率，
    # 图上是一个不起眼的短柱，写进分析却会被当成头号问题。
    min_dim = max(15, int(o["total"] * 0.012))

    if page == "topics":
        # 主题条数跟着 config 走：写死 7 的时候，新增的第八主题
        # 在 AI 分析里根本不存在，排到第 8 的主题永远拿不到解释。
        t = analytics.stats_topics(f, len(config.DIMENSIONS))
        d = analytics.stats_distributions(f)
        plat = analytics.stats_platform_compare(f)
        top = t["ranking"][0] if t["ranking"] else None
        facts = {
            "有效评论": o["total"],
            "全范围负面率": f"{o['negative_rate']}%",
            "主题排行": [{"主题": r["dimension"], "条数": r["count"],
                          "占全部评论": f"{r['share']}%", "负面率": f"{r['negative_rate']}%"}
                         for r in t["ranking"][:6]],
            "最热主题": top["dimension"] if top else None,
            "最热主题条数": top["count"] if top else 0,
            "最热主题的负面率": f"{top['negative_rate']}%" if top else None,
            "全范围高频词": [w["name"] for w in (d.get("keywords") or [])[:12]],
            "平台分布": [f"{p['platform']} {p['total']} 条" for p in plat["platforms"]],
            # 负面率明显高于全范围的主题：热度和满意度常常是两回事，
            # 这个对比是模型能说出"热但不满"的唯一依据。
            # 门槛跟着样本量走，不写死 20：只有 29 条的主题里 11 条负面就是 38%，
            # 这个数放进分析，模型会当成"最严重的问题"讲，而它只是样本太小。
            "负面率偏高的主题": [f"{r['dimension']} {r['negative_rate']}%（{r['count']} 条）"
                                 for r in sorted([x for x in t["ranking"] if x["count"] >= min_dim],
                                                 key=lambda x: -x["negative_rate"])[:3]],
            # 样本从 6 加到 8：6 条全被同一个高赞话题占满时，模型归纳不出成因
            "最热主题代表性评论": _samples(f, 8, dimensions=[top["dimension"]]) if top else [],
        }
        if top:
            # 把镜头推进到最热主题内部：它自己的情感构成、平台构成、高频词、时间集中度。
            # 只有主题排行时，模型只能解释"它最多"，解释不了"它为什么多"。
            fd = dict(f, dimensions=[top["dimension"]])
            od = analytics.stats_overview(fd)
            dd = analytics.stats_distributions(fd, top_words=12)
            pd_ = analytics.stats_platform_compare(fd)
            trend = analytics.stats_trend(fd)
            buckets = sorted([b for b in trend["buckets"] if b["total"] > 0],
                             key=lambda b: -b["total"])[:3]
            facts.update({
                "最热主题内部": {
                    "情感构成": f"正面 {od['positive']} / 中性 {od['neutral']} / 负面 {od['negative']}"
                                f"（负面率 {od['negative_rate']}%）",
                    "平台构成": [f"{p['platform']} {p['total']} 条" for p in pd_["platforms"]],
                    "高频词": [w["name"] for w in (dd.get("keywords") or [])[:10]],
                    "评论最集中的时段": [f"{b['label']} {b['total']} 条" for b in buckets],
                    "有无采集记录的空档": trend.get("zero_buckets") or 0,
                }
            })
        return facts

    if page == "sentiment":
        t = analytics.stats_topics(f, len(config.DIMENSIONS))
        w = analytics.polarity_words(f, 30)
        plat = analytics.stats_platform_compare(f)
        return {
            "有效评论": o["total"],
            "正面占比": f"{o['positive_rate']}%",
            "中性占比": f"{o['neutral_rate']}%",
            "负面占比": f"{o['negative_rate']}%",
            "负面条数": o["negative"],
            "负面原因": [[r["reason"], r["count"], f"{r['share_of_negative']}%"]
                         for r in t["negative_reasons"] if r["reason"] != "未细分原因"][:6],
            "负面原因未细分条数": next((r["count"] for r in t["negative_reasons"]
                                       if r["reason"] == "未细分原因"), 0),
            "负面高频词": [[x["name"], x["value"]] for x in w["negative"][:16]],
            "正面高频词": [[x["name"], x["value"]] for x in w["positive"][:16]],
            # 负面落在哪些主题：说明不满是"全局的"还是"集中在某一环节"
            "负面率最高的主题": [f"{r['dimension']} {r['negative_rate']}%（{r['count']} 条）"
                                 for r in sorted([x for x in t["ranking"] if x["count"] >= min_dim],
                                                 key=lambda x: -x["negative_rate"])[:3]],
            # 负面落在哪些平台：说明是平台人群差异，还是全网一致的抱怨
            "各平台负面率": [f"{p['platform']} {p['negative_rate']}%（{p['total']} 条）"
                             for p in plat["platforms"]],
            "各平台情感构成": [f"{p['platform']} 正{p['positive']}/中{p['neutral']}/负{p['negative']}"
                               for p in plat["platforms"]],
            "负面评论样本": _samples(f, 8, sentiments=["negative"]),
            "正面评论样本": _samples(f, 6, sentiments=["positive"]),
        }

    d = analytics.stats_distributions(f)
    t = analytics.stats_trend(f)
    hour, wd, names = d["hour"], d["weekday"], d["weekday_names"]
    hmax = max(hour) if hour else 0
    wmax = max(wd) if wd else 0
    buckets = [b for b in t["buckets"] if b["total"] > 0]
    top_buckets = sorted(buckets, key=lambda b: -b["total"])[:3]
    # 负面率只在小样本以上比较：1 条评论就能把某天顶到 100%，那不是趋势
    worst = sorted([b for b in buckets if b["total"] >= 10],
                   key=lambda b: -(b["negative"] / b["total"]))[:2]
    facts = {
        "有效评论": o["total"],
        "全范围负面率": f"{o['negative_rate']}%",
        "一天最活跃时段": f"{hour.index(hmax)}时（{hmax} 条）" if hmax else None,
        "24小时前五高": [f"{i}时 {hour[i]} 条" for i in sorted(range(24), key=lambda i: -hour[i])[:5]
                         if hour[i] > 0],
        "周内最多的一天": f"{names[wd.index(wmax)]}（{wmax} 条）" if wmax else None,
        "周内各天": [f"{names[i]} {wd[i]} 条" for i in range(7) if wd[i] > 0],
        "趋势粒度": {"day": "日", "week": "周", "month": "月"}.get(t["granularity"], t["granularity"]),
        "评论量最高的时段": [f"{b['label']} {b['total']} 条" for b in top_buckets],
        "负面率最高的时段": [f"{b['label']} {round(b['negative'] / b['total'] * 100, 1)}%"
                             f"（{b['total']} 条）" for b in worst],
        "无采集记录的时段数": t.get("zero_buckets") or 0,
    }
    facts.update(analytics.timing_profile(f))
    return facts


def _rule_page_insight(facts, page):
    """无 API key / 调用失败时的兜底：只陈述事实与可复算的对照，不做没有依据的归因。

    返回与 AI 版相同的结构，好处是前端只有一条渲染路径；
    「归因」这一层明确留空，等页面上的 notice 告诉读者这些结论是统计规则生成的。
    """
    if page == "topics":
        rank = facts.get("主题排行") or []
        if not rank:
            return {"headline": "", "summary": "当前筛选范围内没有主题数据，无法判断主题热度。",
                    "reasons": [], "next": ""}
        top = rank[0]
        summary = (f"「{top['主题']}」是当前范围内讨论最多的主题，共 {top['条数']} 条提及，"
                   f"占全部评论的 {top['占全部评论']}，负面率 {top['负面率']}。")
        if len(rank) > 1:
            summary += (f"第二名「{rank[1]['主题']}」{rank[1]['条数']} 条，"
                        f"相差 {top['条数'] - rank[1]['条数']} 条。")
        reasons = []
        inner = facts.get("最热主题内部") or {}
        if inner.get("高频词"):
            reasons.append({"point": "高频词指向",
                            "detail": "该主题下出现最多的词是 "
                                      + "、".join(inner["高频词"][:8]) + "。"})
        if inner.get("评论最集中的时段"):
            reasons.append({"point": "时间集中度",
                            "detail": "评论量最集中的时段为 "
                                      + "、".join(inner["评论最集中的时段"]) + "。"})
        if inner.get("平台构成"):
            reasons.append({"point": "平台构成",
                            "detail": "该主题的平台分布为 " + "、".join(inner["平台构成"]) + "。"})
        return {"headline": f"{top['主题']}讨论最多，占 {top['占全部评论']}",
                "summary": summary, "reasons": reasons,
                "next": "跨主题对比负面率，看热度与满意度是否背离"}

    if page == "sentiment":
        neg_words = "、".join(f"「{n}」" for n, _ in (facts.get("负面高频词") or [])[:3])
        pos_words = "、".join(f"「{n}」" for n, _ in (facts.get("正面高频词") or [])[:3])
        summary = (f"负面占 {facts['负面占比']}（{facts['负面条数']} 条），"
                   f"正面占 {facts['正面占比']}，中性占 {facts['中性占比']}。")
        reasons = []
        reasons_raw = facts.get("负面原因") or []
        if reasons_raw:
            reasons.append({"point": "负面原因排序",
                            "detail": "对照负面原因统计："
                                      + "、".join(f"{r[0]} {r[1]} 条（占负面 {r[2]}）"
                                                 for r in reasons_raw[:3]) + "。"})
        if neg_words:
            reasons.append({"point": "负面词云",
                            "detail": f"负面评论高频词集中在{neg_words}。"})
        if facts.get("负面率最高的主题"):
            reasons.append({"point": "主题差异",
                            "detail": "负面率较高的主题：" + "、".join(facts["负面率最高的主题"]) + "。"})
        suggestions = []
        if reasons_raw:
            r0 = reasons_raw[0]
            suggestions.append({
                "title": f"核查「{r0[0]}」",
                "content": f"值班负责人 24 小时内抽取 10 条「{r0[0]}」负面评论核实"
                           f"（{r0[1]} 条，占负面 {r0[2]}），确认是否达到建单条件并归口。"})
        if facts.get("负面率最高的主题"):
            dim = facts["负面率最高的主题"][0].split(" ")[0]
            suggestions.append({
                "title": f"复盘「{dim}」",
                "content": f"归口部门 3 个工作日内对「{dim}」的高负面率给出原因说明与改进时间表，"
                           f"结果回填到处置工单。"})
        return {"headline": f"负面率 {facts['负面占比']}，正面率 {facts['正面占比']}",
                "summary": summary + (f"正面高频词为{pos_words}。" if pos_words else ""),
                "reasons": reasons, "suggestions": suggestions,
                "next": "抽取负面高赞评论逐条核对，确认是否达到建单条件"}

    day = facts.get("周内最多的一天")
    hour = facts.get("一天最活跃时段")
    top = (facts.get("评论量最高的时段") or [])
    summary = "。".join(x for x in [
        f"一天中最活跃的是 {hour}" if hour else "",
        f"周内以 {day} 评论最多" if day else "",
        (f"{facts['趋势粒度']}度上评论量最高的是 {top[0]}" if top else ""),
    ] if x)
    reasons = []
    if facts.get("工作日") and facts.get("周末"):
        wd, we = facts["工作日"], facts["周末"]
        reasons.append({"point": "工作日与周末",
                        "detail": f"工作日 {wd['条数']} 条、负面率 {wd['负面率']}；"
                                  f"周末 {we['条数']} 条、负面率 {we['负面率']}。"})
    if facts.get("各平台活跃时段"):
        reasons.append({"point": "平台活跃时段",
                        "detail": "；".join(f"{p['平台']} 最活跃在 {p['最活跃时段']}，"
                                           f"晚间占比 {p['晚间18-23点占比']}"
                                           for p in facts["各平台活跃时段"]) + "。"})
    if facts.get("时段分布"):
        reasons.append({"point": "时段分布形态",
                        "detail": "、".join(f"{b['时段']} {b['条数']} 条（{b['占比']}）"
                                           for b in facts["时段分布"] if b["条数"])})
    return {"headline": (f"峰值出现在 {hour}" if hour else ""),
            "summary": (summary + "。" if summary else "当前范围没有可用的时间分布数据。"),
            "reasons": reasons,
            "next": "按平台拆开 24 小时曲线，看峰值是某一个平台独有还是两个平台同步"}


@_ai_cached("page", extra=lambda f, page: page)
def page_insight(filters, page):
    """洞察子页的 AI 分析：结构化回答「数据长什么样 + 为什么会这样」。"""
    label_ok = ("topics", "sentiment", "timeline")
    if page not in label_ok:
        raise ValueError(f"page 只支持 {'/'.join(label_ok)}")

    facts = _page_facts(filters, page)
    if facts.get("empty"):
        return {"page": page, "headline": "", "summary": "当前筛选范围内没有可用评论，暂不生成分析。",
                "text": "当前筛选范围内没有可用评论，暂不生成分析。", "reasons": [], "next": "",
                "engine": "rules", "engine_label": "无数据", "notice": "",
                "generated_at": db.now_str()}

    out, engine, notice = None, "rules", ""
    if config.DEEPSEEK_API_KEY:
        try:
            prompt = _term(_PAGE_PROMPT[page]).replace(
                "{{FACTS}}", json.dumps(facts, ensure_ascii=False, indent=1))
            # 三页统一走 JSON：分析被拆成「结论 / 现状 / 逐条成因 / 还能看什么」四块。
            # 写成一整段时，模型讲完峰值和成因就收尾，最想看的"为什么"经常被压缩成一句
            # 「可能与校园作息有关」——结构化的 reasons 才逼它一条一条给依据。
            raw = _llm_json(prompt, timeout=config.LLM_TIMEOUT + 25, max_tokens=1600)
            head = _finish(str(raw.get("headline") or "").strip(), 46)
            body = _finish(str(raw.get("summary") or "").strip(), 175)
            reasons = []
            for r in (raw.get("reasons") or [])[:3]:
                if not isinstance(r, dict):
                    continue
                point = _finish(str(r.get("point") or "").strip(), 22)
                # 上限是保险丝：提示词要 70-100 字，模型常写到 110-140，
                # 卡在 140 会把最后一句切成「…「宣传说 4 人间」」这种断引号。
                detail = _finish(str(r.get("detail") or "").strip(), 175)
                if detail:
                    reasons.append({"point": point or "成因", "detail": detail})
            if body and reasons:
                suggestions = []
                for s in (raw.get("suggestions") or [])[:3]:
                    if not isinstance(s, dict):
                        continue
                    content = _finish(str(s.get("content") or "").strip(), 110)
                    if content:
                        suggestions.append({
                            "title": _finish(str(s.get("title") or "").strip(), 18) or "建议",
                            "content": content,
                        })
                out = {"headline": head, "summary": body, "reasons": reasons,
                       "suggestions": suggestions,
                       "next": _finish(str(raw.get("next") or "").strip(), 56)}
                engine = "deepseek"
        except Exception as e:
            notice = f"AI 分析暂不可用（{str(e)[:60]}），以下为按同一批统计生成的结论。"

    if not out:
        out = _rule_page_insight(facts, page)
        notice = notice or "AI 分析不可用，以下内容由统计规则根据同一批真实数据生成。"
    if not out.get("next"):
        out["next"] = _rule_page_insight(facts, page).get("next", "")
    return {"page": page,
            "headline": out.get("headline", ""),
            "summary": out.get("summary", ""),
            # text 是给不认结构化字段的调用方（旧前端 / 脚本）兜底的纯文本
            "text": " ".join(x for x in [out.get("headline", ""), out.get("summary", "")] if x),
            "reasons": out.get("reasons", []),
            "suggestions": out.get("suggestions", []),
            "next": out.get("next", ""),
            "engine": engine,
            "engine_label": "DeepSeek 生成" if engine == "deepseek" else "统计规则生成",
            "notice": notice, "generated_at": db.now_str()}


_WORD_PROMPT = """你是高校舆情分析助手。下面是「情感分析」页两朵词云背后的真实统计：
正面、负面两个情感桶各自的评论条数，各自前 16 个高频实词（词、出现次数、提及该词的评论条数），
以及两侧各若干条按点赞排序的脱敏评论样本。

请为正、负两朵云**各写一份**解读，只输出 JSON，不要 markdown、不要在 JSON 之外写任何文字：

{
  "positive": {"headline": "一句结论，30 字内", "points": ["要点1", "要点2", "要点3"]},
  "negative": {"headline": "一句结论，30 字内", "points": ["要点1", "要点2", "要点3"]}
}

每侧 points 写 2-3 条，每条 25-50 字：前半句说这个词群在说什么（点出具体的词），
后半句说这意味着什么。例如「宿舍与空调、独立卫浴反复出现，说明满意度主要来自住宿硬件本身」。
两侧要能互相对照：如果正负两侧都在说同一件事（例如都出现「宿舍」），
要点里要明确指出这是同一件事的两面，而不是各说各的。

硬性要求：
只能使用下面给出的事实，不得新增、推算或编造任何数字、词、评论；
不要写「加强沟通」「提升满意度」这类空话，每条要点都要能回到云里的某个词；
不评价具体个人、不推测发布者身份、不做群体归因；
某一侧词数或样本明显偏少时，在 headline 里直接说明「样本偏少」，不要用推断补齐。

事实：
{{FACTS}}"""


def _word_facts(f):
    """词云解读的事实底稿：只有"词 + 次数 + 提及条数 + 少量样本"，不掺主题与原因。

    刻意不把负面原因、主题负面率塞进来——那是页面上「AI 痛点分析」的活儿。
    这张卡要回答的是一个更窄的问题：这两朵云各自在说什么。
    底稿里多放一层业务分类，模型就会从"词"滑到"问题归因"，两张卡会长成同一篇文章。
    """
    o = analytics.stats_overview(f)
    if o.get("empty"):
        return {"empty": True}
    w = analytics.polarity_words(f, 20)
    return {
        "正面评论条数": w.get("positive_total") or 0,
        "负面评论条数": w.get("negative_total") or 0,
        # [词, 出现次数, 提及该词的评论条数]：第三个数字是模型判断"这是普遍说法
        # 还是少数人反复说"的唯一依据，只给词频它会把一个人的口头禅写成群体共识。
        "正面高频词": [[x["name"], x["value"], x.get("docs") or 0]
                       for x in (w.get("positive") or [])[:16]],
        "负面高频词": [[x["name"], x["value"], x.get("docs") or 0]
                       for x in (w.get("negative") or [])[:16]],
        "正面样本": _samples(f, 4, sentiments=["positive"]),
        "负面样本": _samples(f, 4, sentiments=["negative"]),
    }


def _word_side(raw):
    """解析模型给的一侧：一句结论 + 至多 3 条要点。缺字段就当没写，不硬补。"""
    if not isinstance(raw, dict):
        return {"headline": "", "points": []}
    head = _finish(str(raw.get("headline") or "").strip(), 46)
    points = []
    for p in (raw.get("points") or [])[:3]:
        t = _finish(str(p or "").strip(), 96)
        if t:
            points.append(t)
    return {"headline": head, "points": points}


def _rule_word_side(words, name):
    """无模型时的兜底：只陈述词频事实，不给"为什么"。"""
    if not words:
        return {"headline": f"{name}评论里还没有可统计的高频词。", "points": []}
    head = _finish(f"{name}侧最常出现的是{'、'.join(w[0] for w in words[:3])}", 46)
    points = [f"「{w[0]}」出现 {w[1]} 次，{w[2]} 条评论提到。" for w in words[:3]]
    return {"headline": head, "points": points}


@_ai_cached("word")
def word_cloud_insight(filters):
    """情感页两朵词云各自的解读。

    一张卡、两份结论，而不是把整页分析再写一遍：词云回答的是"具体在夸什么/骂什么"，
    页面上方的痛点分析回答的是"为什么会有这种情绪"。两件事混在一段里，
    读者会以为下面那三个主题结构图是可有可无的配图。
    """
    facts = _word_facts(filters)
    if facts.get("empty"):
        return {"positive": {"headline": "", "points": []},
                "negative": {"headline": "", "points": []},
                "totals": {"positive": 0, "negative": 0},
                "engine": "rules", "engine_label": "无数据",
                "notice": "", "generated_at": db.now_str()}

    out, engine, notice = None, "rules", ""
    if config.DEEPSEEK_API_KEY:
        try:
            prompt = _term(_WORD_PROMPT).replace(
                "{{FACTS}}", json.dumps(facts, ensure_ascii=False, indent=1))
            raw = _llm_json(prompt, timeout=config.LLM_TIMEOUT + 25, max_tokens=1200)
            pos, neg = _word_side(raw.get("positive")), _word_side(raw.get("negative"))
            if pos["points"] or neg["points"]:
                out = {"positive": pos, "negative": neg}
                engine = "deepseek"
        except Exception as e:
            notice = f"AI 词云解读暂不可用（{str(e)[:60]}），以下按词频陈述。"

    if not out:
        out = {"positive": _rule_word_side(facts.get("正面高频词") or [], "正面"),
               "negative": _rule_word_side(facts.get("负面高频词") or [], "负面")}
        notice = notice or "AI 解读不可用，以下内容由统计规则根据同一批词频生成。"

    return {**out,
            "totals": {"positive": facts.get("正面评论条数") or 0,
                       "negative": facts.get("负面评论条数") or 0},
            "engine": engine,
            "engine_label": "DeepSeek 生成" if engine == "deepseek" else "统计规则生成",
            "notice": notice, "generated_at": db.now_str()}


@_ai_cached("dash")
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
        "筛选口径": o["meta"]["filters_text"],
        "最热主题": (facts["topics"]["ranking"][0] if facts["topics"]["ranking"] else None),
        "主题排行": [(r["dimension"], r["count"], r["negative_rate"]) for r in facts["topics"]["ranking"][:4]],
        "负面原因前三": [(r["reason"], r["count"]) for r in facts["topics"]["negative_reasons"][:3]],
        "平台": [{k: x[k] for k in ("platform", "total", "positive_rate", "negative_rate")}
                 for x in facts["platforms"]["platforms"]],
    }
    # 筛了情感时，上面那三个占比必然是 0/100/0。不点破的话模型会把"100% 负面"
    # 当成发现写进简述——那是筛选的数学结果。
    sent_filtered = bool(filters.get("sentiments"))
    if sent_filtered:
        payload["同口径全量条数"] = o.get("scope_total")
        payload["本条占同口径全量"] = _share(o["total"], o.get("scope_total"))
        payload["注意"] = ("本次已按情感筛选：情感构成必然只剩所选情感（占比 0% / 100%），"
                       "这是筛选的结果、不是发现。请说明这一块占同口径全量的多少，"
                       "并讲这些评论集中讨论什么，不要再复述占比。")
    text, engine, notice = "", "rules", ""
    if config.DEEPSEEK_API_KEY:
        try:
            t = _llm_text(_term(_INSIGHT_PROMPT).format(facts=json.dumps(payload, ensure_ascii=False, indent=1)))
            if t:
                text, engine = t, "deepseek"
        except Exception as e:
            notice = f"AI 简述暂不可用（{str(e)[:60]}），以下为统计规则生成的说明。"
    if not text:
        text = _rule_insight(facts, sent_filtered)
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
    span_text = (" ~ ".join(str(x) for x in span if x) if isinstance(span, (list, tuple)) else
                 str(span or "")) or "—"
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
    a(f"| 讨论最多的主题 | {k.get('top_dimension')} |")
    # kpi.platforms 是 {平台: 条数} 的字典（总览接口的形状），不是列表
    plats = k.get("platforms") or {}
    if isinstance(plats, dict) and plats:
        a("| 平台分布 | " + "；".join(f"{n} {c} 条" for n, c in plats.items()) + " |")
    elif isinstance(plats, list) and plats:
        a("| 平台分布 | " + "；".join(str(x) for x in plats) + " |")
    a("")

    a("## 二、结论")
    a("")
    if c.get("headline"):
        a(f"**{c['headline']}**")
        a("")
    a(str(c.get("summary") or ""))
    a("")

    ev_by_ref = {e.get("comment_ref"): e for e in (report.get("evidence") or [])}

    def quote(ref, indent=""):
        e = ev_by_ref.get(ref)
        if e:
            return (f"{indent}> 证据 `{ref}`（{e.get('platform')} · {e.get('date')} · "
                    f"{e.get('sentiment_label')}）：{e.get('snippet')}")
        return ""

    findings = c.get("findings") or []
    if findings:
        a("### 关键发现")
        a("")
        for i, fd in enumerate(findings, 1):
            a(f"{i}. **{fd.get('title') or '发现'}**：{fd.get('text') or ''}")
            for ref in (fd.get("evidence_refs") or []):
                q = quote(ref, "   ")
                if q:
                    a(q)
        a("")
    else:
        # 旧报告没有 findings，保留原来的主题/情感两段
        if c.get("dimension_insight"):
            a(f"**主题**：{c['dimension_insight']}")
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
            for ref in (sg.get("evidence_refs") or []):
                q = quote(ref, "   ")
                if q:
                    a(q)
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
