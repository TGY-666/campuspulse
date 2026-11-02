# -*- coding: utf-8 -*-
"""M4 引用核验验收：AC-M4-01 ~ AC-M4-10，一条 AC 一个用例。

**怎么验的**：只替换"模型"和"检索"，其余全走真实链路——
工具调用、规则选工具、引用核验、脱敏、审计都是线上那一套。
直接调 `veracity.enforce()` 只能证明函数本身对，证明不了
"chat() 有没有真的把它接上去"，而后者才是最容易漏的地方。

**为什么跑在库副本上**：每轮 chat 都会往 agent_runs 写审计行。
在演示库上跑十几次，驾驶舱的"今日问答"就会凭空多出一截。

运行：
    CP_CITATION_GUARD=1 .venv/Scripts/python.exe tools/verify_agent.py
    ... --keep     保留副本，便于手工排查
"""
import argparse
import contextlib
import logging
import os
import sqlite3
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DB = os.path.join(ROOT, "data", "campuspulse.db")
COPY_DIR = tempfile.mkdtemp(prefix="cp_verify_agent_")
COPY_DB = os.path.join(COPY_DIR, "verify.db")

os.environ["CP_DB_PATH"] = COPY_DB
os.environ.setdefault("CP_AUTH", "0")
os.environ.setdefault("CP_CITATION_GUARD", "1")
os.environ.setdefault("CP_TENANT_ENFORCE", "0")     # 单租户脚本，不需要闸门报警
sys.path.insert(0, ROOT)

from campuspulse import agent, config, db, rag, tenancy, veracity  # noqa: E402

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
PASS = FAIL = 0
FAILED = []
TRAP = None                           # main() 里挂上日志收集器
DS = "ds_2023_all"                    # 食堂餐饮负面 278 条，检索稳定
Q = "食堂问题主要集中在哪里"
TENANT = config.DEFAULT_TENANT_ID


def _nocolor():
    global GREEN, RED, DIM, RESET
    GREEN = RED = DIM = RESET = ""


def check(ac, name, fn):
    global PASS, FAIL
    try:
        detail = fn()
        PASS += 1
        print(f"  {GREEN}OK{RESET}  [{ac}] {name}" + (f" {DIM}— {detail}{RESET}" if detail else ""))
    except Exception as e:
        FAIL += 1
        FAILED.append(f"{ac} {name}")
        print(f"  {RED}!!{RESET}  [{ac}] {name} — {type(e).__name__}: {e}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}：期望 {want!r}，实际 {got!r}")


def truthy(cond, what):
    if not cond:
        raise AssertionError(what)


class LogTrap(logging.Handler):
    """收集核验模块的告警，既不让 traceback 糊满终端，又验证"确实记了日志"。

    AC-M4-09 要求"放行原文**并记日志**"。只断言放行、不看日志，
    等于允许"悄悄吞掉异常"——那种实现同样能让用例通过，但线上出问题时无从排查。
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def clear(self):
        self.records = []


# ---------------------------------------------------------------- 环境
def prepare_copy():
    if not os.path.isfile(SOURCE_DB):
        raise SystemExit("找不到 data/campuspulse.db，先跑一次应用")
    src = sqlite3.connect(SOURCE_DB)
    try:
        dst = sqlite3.connect(COPY_DB)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def cleanup_copy():
    for suffix in ("", "-wal", "-shm"):
        p = COPY_DB + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def load_items(n=6, extra=0):
    """从真实库里取编号，构造检索片段。

    extra 用于取"库里有、但不在本次检索结果里"的编号——
    那是 invalid（越界引用）与 unknown（编造）的分界线，必须真的是库里的行。
    """
    rows = db.query_all(
        "SELECT comment_ref, content_masked, platform, comment_date, dimension"
        " FROM comments WHERE tenant_id = ? AND dataset_id = ?"
        " AND cleaning_status = 'kept' ORDER BY comment_ref LIMIT ?",
        (TENANT, DS, n + extra))
    out = []
    for i, r in enumerate(rows):
        out.append({
            "comment_ref": r["comment_ref"],
            "text": (r["content_masked"] or "")[:120],
            "snippet": (r["content_masked"] or "")[:120],
            "platform": r["platform"] or "贴吧",
            "date": r["comment_date"] or "",
            "sentiment_label": "负面",
            "dimension": r["dimension"] or "食堂餐饮",
            "rank": i + 1, "score": 1.0, "bm25": 1.0, "vector": 0.1,
        })
    return out[:n], out[n:n + extra]


@contextlib.contextmanager
def fixed_items(items, counter=None):
    """把检索结果固定下来：编号可控，才能断言"哪一条被拦了"。"""
    orig = rag.retrieve

    def _ret(query, dataset_id=None, filters=None, top_k=6, **kw):
        if counter is not None:
            counter["n"] += 1
        return {"query": query, "items": items, "candidates": {"bm25": len(items)},
                "stats": {"docs": len(items), "vocab": 1}, "elapsed_ms": 1,
                "trace": {"fusion": "fixed"}}

    rag.retrieve = _ret
    try:
        yield counter
    finally:
        rag.retrieve = orig


@contextlib.contextmanager
def fake_llm(replies):
    """替换两处模型调用，返回调用计数。

    replies 依次用于"撰写回答"的每次调用：第 1 条是初稿，
    第 2 条（若触发重写）是改写稿；不足时复用最后一条。
    """
    calls = {"plan": 0, "compose": 0}
    orig_plan, orig_compose = agent._call_llm, agent.stream_completion
    orig_avail = agent.api_available

    def _plan(messages, timeout=None, max_tokens=900):
        calls["plan"] += 1
        return {"content": "", "tool_calls": []}      # 不调工具，走规则路由取真实数据

    def _compose(messages, on_token=None, timeout=None, max_tokens=700):
        i = calls["compose"]
        calls["compose"] += 1
        text = replies[min(i, len(replies) - 1)]
        if on_token:
            on_token(text)
        return text, 0.01

    agent._call_llm = _plan
    agent.stream_completion = _compose
    agent.api_available = lambda: True
    try:
        yield calls
    finally:
        agent._call_llm, agent.stream_completion = orig_plan, orig_compose
        agent.api_available = orig_avail


def run_chat(replies, items, counter=None):
    with fixed_items(items, counter), fake_llm(replies) as calls:
        r = agent.chat(Q, DS)
    return r, calls


# ---------------------------------------------------------------- AC
def ac01():
    items, _ = load_items(6)
    a, b = items[0]["comment_ref"], items[1]["comment_ref"]
    text = f"食堂排队问题突出[{a}]，宿舍空调报修也有反映[{b}]。"
    r, calls = run_chat([text], items)
    c = r["citation"]
    eq(c["verdict"], "pass", "verdict")
    eq(c["valid"], c["total"], "valid 应等于 total")
    eq(r["answer"], text, "通过时正文不得改动")
    eq(calls["compose"], 1, "pass 不应触发重写")
    return f"{c['valid']}/{c['total']} 有效 · 回答逐字未改"


def ac02():
    items, _ = load_items(6)
    a = items[0]["comment_ref"]
    bad = f"食堂排队问题突出[{a}]，宿舍报修积压[CMT-NOTEXIST]。"
    r, _ = run_chat([bad, bad], items)          # 改写也照样编 → 剔除
    c = r["citation"]
    eq(c["verdict"], "stripped", "verdict")
    truthy("CMT-NOTEXIST" not in r["answer"], f"编造编号仍在回答中：{r['answer']}")
    truthy(a in r["answer"], "真实编号被误删")
    return f"移除 {c['removed']}，真实编号保留"


def ac03():
    items, extra = load_items(6, extra=1)
    a = items[0]["comment_ref"]
    out = extra[0]["comment_ref"]                # 库里有，但不在本次检索结果里
    text = f"食堂排队[{a}]，另有一条[{out}]，还有一条[CMT-GHOST]。"

    cls = veracity.classify(text, items, DS)
    eq(cls["invalid"], [out], "越界编号应归入 invalid")
    eq(cls["unknown"], ["CMT-GHOST"], "编造编号应归入 unknown")
    truthy(out not in cls["unknown"], "invalid 与 unknown 混了")

    r, _ = run_chat([text, text], items)         # 链路上两者都要被拦
    truthy(out not in r["answer"] and "CMT-GHOST" not in r["answer"],
           f"链路上未拦住：{r['answer']}")
    return f"invalid={out} / unknown=CMT-GHOST，两者未混且都被移除"


def ac04():
    items, _ = load_items(6)
    a = items[0]["comment_ref"]
    bad = f"食堂排队[{a}]，宿舍积压[CMT-GHOST]。"
    ret = {"n": 0}
    r, calls = run_chat([bad, bad], items, counter=ret)
    eq(calls["compose"], 2, "撰写调用次数（初稿 1 + 重写 1）")
    eq(ret["n"], 1, "重写不得重新检索")
    return f"模型撰写 {calls['compose']} 次（非 3 次）· 检索 {ret['n']} 次"


def ac05():
    items, _ = load_items(6)
    a = items[0]["comment_ref"]
    bad = f"食堂排队[{a}]，宿舍报修积压[CMT-GHOST]。"
    fixed = f"食堂排队[{a}]，宿舍情况需进一步取证。"
    r, calls = run_chat([bad, fixed], items)
    c = r["citation"]
    eq(c["verdict"], "revised", "verdict")
    eq(c["revised"], True, "revised 标记")
    truthy(c["revised_count"] >= 1, "修正条数应 ≥ 1")
    eq(r["answer"], fixed, "应采用改写稿")
    eq(calls["compose"], 2, "只重写一次")
    return f"修正 {c['revised_count']} 处 · 改写稿 {c['valid']}/{c['total']} 有效"


def ac06():
    items, _ = load_items(6)
    text = "食堂问题主要集中在排队时间上，属于长期存在的结构性问题。"
    r, calls = run_chat([text], items)
    eq(r["citation"]["verdict"], "no_anchor", "verdict")
    eq(r["answer"], text, "无锚点时正文必须照常输出")
    eq(calls["compose"], 1, "缺锚点不应触发重写")
    return "正文完整保留，仅标注缺锚点"


def ac07():
    """数字核验：真实工具结果 + 真实回答，0 处未匹配。

    走规则模式（不调模型）：回答由统计口径直接拼出，
    数字若对不上，只能是核验器自己有问题——这正是要验的。
    """
    orig = agent.api_available
    agent.api_available = lambda: False
    try:
        r = agent.chat(Q, DS)
    finally:
        agent.api_available = orig
    g = r["grounding"]
    eq(g["passed"], True, f"未匹配数字：{g['unmatched']}")
    truthy(g["checked"] >= 3, f"检查到的数字太少（{g['checked']}），用例没有实际意义")
    return f"检查 {g['checked']} 个数字，0 处未匹配"


def ac08():
    """关闭开关：回答一字不改，只记录。"""
    items, _ = load_items(6)
    a = items[0]["comment_ref"]
    bad = f"食堂排队[{a}]，宿舍积压[CMT-GHOST]。"
    old = config.CP_CITATION_GUARD
    config.CP_CITATION_GUARD = False
    try:
        r, calls = run_chat([bad], items)
    finally:
        config.CP_CITATION_GUARD = old
    c = r["citation"]
    eq(r["answer"], bad, "只记录模式下正文必须与模型原文逐字相同")
    eq(c["enforced"], False, "enforced 标记")
    eq(c["verdict"], "flagged", "verdict")
    eq(calls["compose"], 1, "只记录模式不得触发重写")
    truthy(c["unknown"], "问题编号应被记录（只是不拦截）")
    return "正文逐字未改 · 问题编号仍被记录"


def ac09():
    """校验器自己挂掉：放行原文，绝不让整轮对话失败。"""
    items, _ = load_items(6)
    a = items[0]["comment_ref"]
    text = f"食堂排队[{a}]。"
    orig = veracity.classify

    def boom(*args, **kwargs):
        raise RuntimeError("模拟核验故障")

    veracity.classify = boom
    try:
        r, calls = run_chat([text], items)
    finally:
        veracity.classify = orig
    eq(r["answer"], text, "核验失败时必须放行原文")
    eq(r["citation"]["verdict"], "unverified", "verdict")
    eq(calls["compose"], 1, "不应因核验失败重写或重试")
    return "原文放行 · 标注「引用未核验」"


def ac10():
    """并发 4 轮：citation 各自独立，不串。

    每轮注入不同的编造编号，最后检查"我的 citation 里只有我的编号"。
    这里同时是租户上下文的压力测试——contextvars 不跨线程继承，
    worker 里不装身份的话，db.tid() 会是空串（S1 踩过一次）。
    """
    items, _ = load_items(6)
    n = 4
    ghosts = {i: f"CMT-GHOST{i}" for i in range(n)}
    slots = {}
    out = {}
    orig_plan, orig_compose = agent._call_llm, agent.stream_completion
    orig_avail, orig_ret = agent.api_available, rag.retrieve

    def _plan(messages, timeout=None, max_tokens=900):
        return {"content": "", "tool_calls": []}

    def _compose(messages, on_token=None, timeout=None, max_tokens=700):
        tid = threading.get_ident()
        return f"结论[{slots[tid]}]。", 0.01

    def _ret(query, dataset_id=None, filters=None, top_k=6, **kw):
        return {"query": query, "items": items, "candidates": {}, "stats": {},
                "elapsed_ms": 1, "trace": {}}

    agent._call_llm, agent.stream_completion = _plan, _compose
    agent.api_available, rag.retrieve = (lambda: True), _ret
    try:
        def worker(i):
            tenancy.set_context(tenancy.system_context())
            slots[threading.get_ident()] = ghosts[i]
            out[i] = agent.chat(Q, DS)["citation"]

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
    finally:
        agent._call_llm, agent.stream_completion = orig_plan, orig_compose
        agent.api_available, rag.retrieve = orig_avail, orig_ret

    eq(len(out), n, f"应完成 {n} 轮，实际 {len(out)}")
    for i in range(n):
        got = set(out[i]["removed"])
        eq(got, {ghosts[i]}, f"第 {i} 轮的移除清单串了")
    return f"{n} 轮并发，各自的移除清单互不污染"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--keep", action="store_true", help="保留库副本")
    args = ap.parse_args()
    if args.no_color:
        _nocolor()

    prepare_copy()
    tenancy.set_context(tenancy.system_context())
    db.init_db()
    # 核验模块的告警走自己的收集器：终端只留结论，异常细节仍完整落到 records
    _vg = logging.getLogger("campuspulse.veracity")
    global TRAP
    TRAP = LogTrap()
    _vg.addHandler(TRAP)
    _vg.propagate = False
    try:
        print(f"{DIM}数据库副本：{COPY_DB}（不改动演示库）{RESET}")
        print(f"{DIM}开关：CP_CITATION_GUARD={int(config.CP_CITATION_GUARD)} · "
              f"数据集 {DS}{RESET}\n")
        print("== M4 引用核验（AC-M4-01 ~ 10）==")
        check("AC-M4-01", "正常问答：引用全部命中，正文不受影响", ac01)
        check("AC-M4-02", "编造编号：从回答中移除，verdict=stripped", ac02)
        check("AC-M4-03", "越界编号归 invalid，编造归 unknown，两者不混", ac03)
        check("AC-M4-04", "重写只发生一次，且不重新检索", ac04)
        check("AC-M4-05", "重写成功：verdict=revised，显示修正条数", ac05)
        check("AC-M4-06", "整段无编号：verdict=no_anchor，正文照常输出", ac06)
        check("AC-M4-07", "回答中的统计数字 0 处未匹配", ac07)
        check("AC-M4-08", "关闭开关：正文逐字不改，问题仍被记录", ac08)
        check("AC-M4-09", "核验抛异常：放行原文，本轮不失败", ac09)
        check("AC-M4-10", "并发 4 轮：citation 互不串扰", ac10)
    finally:
        if not args.keep:
            cleanup_copy()

    print()
    if FAIL:
        print(f"{RED}结果：{PASS} 通过 / {FAIL} 失败{RESET}")
        for f in FAILED:
            print(f"  {RED}- {f}{RESET}")
    else:
        print(f"{GREEN}结果：{PASS} 项全部通过{RESET}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
