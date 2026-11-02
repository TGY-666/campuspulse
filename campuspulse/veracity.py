# -*- coding: utf-8 -*-
"""M4 · 可核验性（veracity）：引用与数字的单一出口。

这个模块回答一个问题：**这句话，系统撑得住吗？**

撑得住 → 原样输出，附一个"已核验"角标。
撑不住 → 先请模型改写一次；还撑不住 → 把撑不住的那一小块拿掉，并**明说拿掉了什么**。

三条不可让步：
  1. **只删标记，不删句子**。`[CMT-8F2A]` 是锚点，句子是内容。锚点假就删锚点，
     顺手把整句删掉等于用一个错误换一个信息损失。
  2. **失败必须可见**。任何降级都要在 verdict 里体现，不允许静默丢弃。
  3. **校验器比被校验的内容更容易挂**。核验环节自身抛异常时，退回"不校验"，
     绝不能让整轮对话失败——这是整条链路上最容易写错的一处。

和 `rag.citation_check` 的分工：那个函数只做三分类计数，供旧接口与验收脚本使用；
这里是**干预前**的判定，需要逐条明细（哪一条、什么状态、原文是什么），
因为剔除要按编号精确下手。
"""
import logging
import re

from . import config, db

log = logging.getLogger(__name__)

# 与 rag._REF_RE 保持一致；这里不 import 是为了让两类编号（带括号 / 裸串）都能处理
REF_RE = re.compile(r"\[\s*(CMT-[0-9A-Za-z]+)\s*\]")
_BARE_REF = re.compile(r"CMT-[0-9A-Za-z]{4,}")

STATUS_LABELS = {
    "valid": "已核验",
    "invalid": "越界引用（库里有，本次未检索到）",
    "unknown": "库中不存在（编造）",
}

# verdict → 人话。前四个是文档 4.5 规定的干预路径，
# 后两个是"系统没能力判断"时的诚实说法，不用 pass 冒充。
VERDICT_LABELS = {
    "pass": "引用全部已核验",
    "revised": "引用问题已自动修正",
    "stripped": "无法溯源的引用已移除",
    "no_anchor": "结论未标注原文锚点",
    "flagged": "引用存疑（只记录模式，未修改回答）",
    "unverified": "引用核验未完成（已原样输出）",
}


# ============================ 一、引用判定 ============================
def _snippet_of(item):
    """RAG 片段用 text，库内行用 content_masked，两者都可能缺。"""
    return (item.get("snippet") or item.get("text")
            or item.get("content_masked") or "")[:160]


def classify(answer, items, dataset_id=None):
    """把回答里的引用编号逐条分类，并给出可用于展示的原文摘要。

    valid   —— 编号在本次检索结果里（可回跳）
    invalid —— 编号在库里存在，但没被本次检索命中（越界引用：看起来有据，其实没查过）
    unknown —— 编号在库里根本不存在（编造）

    `total` / `valid` 一律按**去重后**的编号数统计，保证
    `valid + len(invalid) + len(unknown) == total` 恒成立——
    旧实现在重复引用时这条恒等式会破，前端两个数字对不上就没人信了。
    """
    text = answer or ""
    occurrences = REF_RE.findall(text)
    refs = list(dict.fromkeys(occurrences))          # 保序去重
    got = {it["comment_ref"]: it for it in (items or []) if it.get("comment_ref")}

    missing = [r for r in refs if r not in got]
    known = {}
    if missing and dataset_id:
        # 一次查完，避免逐条往返；库里查到的才算 invalid，查不到的才是 unknown
        rows = db.query_all(
            "SELECT comment_ref, content_masked FROM comments"
            " WHERE tenant_id = ? AND dataset_id = ? AND comment_ref IN (%s)"
            % ",".join("?" * len(missing)),
            [db.tid(), dataset_id] + missing)
        known = {r["comment_ref"]: r for r in rows}

    detail = []
    for ref in refs:
        if ref in got:
            status, snip = "valid", _snippet_of(got[ref])
        elif ref in known:
            status, snip = "invalid", _snippet_of(known[ref])
        else:
            status, snip = "unknown", ""
        detail.append({
            "ref": ref,
            "status": status,
            "status_label": STATUS_LABELS[status],
            "occurrences": occurrences.count(ref),
            "snippet": snip,
        })

    valid = [d for d in detail if d["status"] == "valid"]
    invalid = [d for d in detail if d["status"] == "invalid"]
    unknown = [d for d in detail if d["status"] == "unknown"]
    return {
        "total": len(refs),
        "occurrences": len(occurrences),
        "valid": len(valid),
        "invalid": [d["ref"] for d in invalid],
        "unknown": [d["ref"] for d in unknown],
        "bad": [d["ref"] for d in invalid + unknown],
        "ok": bool(refs) and not invalid and not unknown,
        "detail": detail,
    }


def strip_refs(answer, refs):
    """删掉指定编号的**标记本身**，句子与其余内容一律保留。

    删完要顺手收拾标点：`这是一句[CMT-X]。` 会变成 `这是一句。`，
    而 `[CMT-X][CMT-Y]` 连排时会留下两个空格——不收拾的话，
    读者看到的是"这里好像原本有点东西"，而我们要的是"读起来就是一句完整的话"。
    """
    bad = set(refs or [])
    removed = []

    def _repl(m):
        if m.group(1) in bad:
            removed.append(m.group(1))
            return ""
        return m.group(0)

    out = REF_RE.sub(_repl, answer or "")
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+([，。；、！？：])", r"\1", out)
    out = re.sub(r"([（(])[ \t]*[)）]", "", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip(), removed


# ============================ 二、数字判定 ============================
def collect_numbers(obj, bucket):
    """递归收集一个结构里所有可比较的数值（含字符串里的数字）。"""
    if isinstance(obj, dict):
        for v in obj.values():
            collect_numbers(v, bucket)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            collect_numbers(v, bucket)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        bucket.add(round(float(obj), 1))
    elif isinstance(obj, str):
        for m in re.findall(r"\d+(?:\.\d+)?", obj):
            try:
                bucket.add(round(float(m), 1))
            except Exception:
                pass


def number_check(answer, tool_results):
    """核对回答里出现的数字是否都能在本轮工具结果中找到。

    允许三类"合法派生值"：
      1) 四舍五入（±0.11 或 1%）；
      2) 年份（2018-2030）；
      3) 两个已出现数值之间的差值，例如"负面率相差 6.3 个百分点"（36.3 - 30.0）。
    差值可被当场验算，不属于编造；但比率、增长率这类强解释性数值不放行——
    那才是幻觉高发区。
    """
    allowed = set()
    for res in tool_results or []:
        collect_numbers(res.get("data") or {}, allowed)
        collect_numbers(res.get("metrics") or {}, allowed)
    for y in range(2018, 2031):
        allowed.add(float(y))
    allowed.add(0.0)

    if 0 < len(allowed) <= 400:          # 派生差值：基数可控时才展开，避免组合爆炸
        base = sorted(allowed)
        diffs = set()
        for i, a in enumerate(base):
            for b in base[i + 1:]:
                d = abs(a - b)
                if d <= 1000:
                    diffs.add(round(d, 1))
        allowed |= diffs

    # 引用编号里的十六进制串会被误当成数字，先剔除再查
    cleaned = REF_RE.sub(" ", answer or "")
    cleaned = _BARE_REF.sub(" ", cleaned)
    found = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", cleaned)]
    unmatched = []
    for n in found:
        ok = any(abs(n - a) <= max(0.11, abs(a) * 0.01) for a in allowed)
        if not ok:
            unmatched.append(n)
    return {
        "checked": len(found),
        "unmatched": sorted(set(unmatched))[:8],
        "passed": len(unmatched) == 0,
        "note": "回答中的数字均可在工具返回结果中找到（含四舍五入与差值）。" if not unmatched
                else "以下数字未在工具结果中出现，请谨慎采信：" + "、".join(
                    f"{n:g}" for n in sorted(set(unmatched))[:8]),
    }


# ============================ 三、主流程 ============================
def _note(verdict, first, removed=(), revised_count=0, err=""):
    if verdict == "pass":
        return f"回答中的 {first['total']} 处引用全部来自本次检索结果，可逐条回跳原文。"
    if verdict == "revised":
        return (f"首次回答有 {revised_count} 处引用无法溯源，已按「只许引用已检索片段」"
                f"重写一次并复核通过；当前 {first.get('total', 0)} 处引用均可回跳原文。")
    if verdict == "stripped":
        shown = "、".join(list(removed)[:3])
        more = f" 等 {len(removed)} 处" if len(removed) > 3 else ""
        return (f"以下引用无法溯源，已从回答中移除：{shown}{more}。"
                f"其余内容保留未改，结论中的统计数字仍来自本轮工具结果。")
    if verdict == "no_anchor":
        return "本条结论没有标注原文锚点，无法逐句回跳；可追问「这条结论的依据是哪些原文」再取证据。"
    if verdict == "flagged":
        return ("检测到引用问题，但引用核验当前为只记录模式（CP_CITATION_GUARD=0），"
                "未修改回答。需拦截请把开关置 1。")
    if verdict == "unverified":
        return f"引用核验环节未完成（{err or '内部错误'}），已原样输出回答；本次引用未经核验。"
    return ""


def _pack(answer, verdict, cls, *, removed=(), revised=False, revised_count=0,
          enforced=True, calls=0, err=""):
    return {
        "answer": answer,
        "citation": {
            "verdict": verdict,
            "verdict_label": VERDICT_LABELS.get(verdict, verdict),
            "total": cls.get("total", 0),
            "valid": cls.get("valid", 0),
            "invalid": cls.get("invalid", []),
            "unknown": cls.get("unknown", []),
            "removed": list(removed),
            "revised": revised,
            "revised_count": revised_count,
            "enforced": enforced,
            "calls": calls,
            "detail": cls.get("detail", []),
            "note": _note(verdict, cls, removed, revised_count, err),
        },
    }


def enforce(answer, items, dataset_id=None, revise=None, enabled=None):
    """引用硬阻断的唯一入口。返回 `{answer, citation}`。

    revise(answer, problems, items) -> str|None
        由调用方提供的一次性重写函数（本模块不直接调模型，便于单测注入假实现）。
        返回 None 表示重写失败或不可用，此时直接进入剔除。

    enabled=None 时读 config.CP_CITATION_GUARD：
        True  → 干预（重写 / 剔除）
        False → 只记录，回答一字不改
    **任何情况下都不抛异常**：核验失败退回不核验，不退回拒绝输出。
    """
    answer = answer or ""
    if enabled is None:
        enabled = getattr(config, "CP_CITATION_GUARD", False)

    try:
        first = classify(answer, items, dataset_id)
    except Exception as e:                       # AC-M4-09：校验器自己挂了
        log.warning("citation classify failed: %s", e, exc_info=True)
        return _pack(answer, "unverified", {"total": 0}, enforced=False, err=str(e)[:80])

    if not first["total"]:                       # AC-M4-06：没锚点照常输出
        return _pack(answer, "no_anchor", first, enforced=enabled)
    if first["ok"]:                              # AC-M4-01：全部命中
        return _pack(answer, "pass", first, enforced=enabled)
    if not enabled:                              # AC-M4-08：只记录
        return _pack(answer, "flagged", first, enforced=False)

    problems = [{"ref": d["ref"], "status": d["status"],
                 "status_label": d["status_label"]}
                for d in first["detail"] if d["status"] != "valid"]

    revised_answer, calls = None, 0
    if revise is not None:
        calls = 1                                # 只试一次，不循环（否则一个问题能烧掉整轮预算）
        try:
            revised_answer = revise(answer, problems, items)
        except Exception as e:
            log.warning("citation revise failed: %s", e, exc_info=True)
            revised_answer = None

    if revised_answer:
        try:
            second = classify(revised_answer, items, dataset_id)
        except Exception as e:
            log.warning("citation recheck failed: %s", e, exc_info=True)
            second = None
        if second and second["ok"]:
            return _pack(revised_answer, "revised", second, revised=True,
                         revised_count=len(problems), calls=calls)
        if second and second["total"]:
            # 改写没修干净：以改写稿为底继续剔除。
            # 注意问题清单必须**换成改写稿自己的**——沿用第一版的清单会误删
            # 改写稿里已经修好的编号（第一版里越界的那个，改写后可能已经换对了）。
            answer = revised_answer
            problems = [{"ref": d["ref"], "status": d["status"],
                         "status_label": d["status_label"]}
                        for d in second["detail"] if d["status"] != "valid"]

    bad = [p["ref"] for p in problems]
    final_answer, removed = strip_refs(answer, bad)
    try:
        final_cls = classify(final_answer, items, dataset_id)
    except Exception:
        final_cls = first
    return _pack(final_answer, "stripped", final_cls, removed=removed, calls=calls)


def summary(citation):
    """给审计/日志用的一行摘要（不含原文，只有计数与状态）。"""
    c = citation or {}
    return (f"{c.get('verdict')} · {c.get('valid')}/{c.get('total')} 有效"
            f" · removed={len(c.get('removed') or [])} · calls={c.get('calls', 0)}")
