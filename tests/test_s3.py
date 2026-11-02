# -*- coding: utf-8 -*-
"""S3 验收：引用核验（M4）的**不变量**，不跑业务全路径。

和 verify_agent.py 的分工：
    verify_agent   跑"一条回答从生成到定稿"：初稿→复核→重写→剔除→角标，十条 AC 全链路
    本脚本         跑"每条规则本身对不对"：分类恒等式、剔除的边界、六态与开关的组合、
                   数字核验的三类合法派生值

为什么这些必须单独断言：
    全链路用例只能覆盖"走过的那几条路"。分类在重复引用时会不会漏算、
    剔除会不会把句子一起吃掉、重写函数自己抛异常时会不会冒泡成整轮失败——
    这些分支平时一条都走不到，正好是最容易写错的地方。

跑在数据库副本上，不碰演示库：
    CP_CITATION_GUARD=1 .venv/Scripts/python.exe tests/test_s3.py
"""
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DB = os.path.join(ROOT, "data", "campuspulse.db")

# 必须在 import campuspulse 之前改环境：config 是导入时读环境变量的
_TMPDIR = tempfile.mkdtemp(prefix="cp_s3_")
COPY_DB = os.path.join(_TMPDIR, "s3.db")
if os.path.isfile(SOURCE_DB):
    _src = sqlite3.connect(SOURCE_DB)
    try:
        _dst = sqlite3.connect(COPY_DB)
        try:
            _src.backup(_dst)
        finally:
            _dst.close()
    finally:
        _src.close()
os.environ["CP_DB_PATH"] = COPY_DB
os.environ.setdefault("CP_AUTH", "0")
os.environ.setdefault("CP_CITATION_GUARD", "1")
sys.path.insert(0, ROOT)

from campuspulse import config, db, rag, tenancy, veracity  # noqa: E402

DS = "ds_2023_all"
TENANT = config.DEFAULT_TENANT_ID
PASS = FAIL = 0
FAILED = []
ITEMS = []            # 由 _load_items() 填充
OUTSIDE = ""          # 库里存在、但不在 ITEMS 里的编号


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}：期望 {want!r}，实际 {got!r}")


def truthy(cond, what):
    if not cond:
        raise AssertionError(what)


def check(name, fn):
    global PASS, FAIL
    try:
        detail = fn()
        PASS += 1
        print(f"  OK  {name}" + (f" — {detail}" if detail else ""))
    except Exception as e:
        FAIL += 1
        FAILED.append(name)
        print(f"  !!  {name} — {type(e).__name__}: {e}")


def _load_items(n=4):
    global ITEMS, OUTSIDE
    rows = db.query_all(
        "SELECT comment_ref, content_masked FROM comments"
        " WHERE tenant_id = ? AND dataset_id = ? AND cleaning_status = 'kept'"
        " ORDER BY comment_ref LIMIT ?", (TENANT, DS, n + 1))
    ITEMS = [{"comment_ref": r["comment_ref"],
              "text": (r["content_masked"] or "")[:80],
              "snippet": (r["content_masked"] or "")[:80]} for r in rows[:n]]
    OUTSIDE = rows[n]["comment_ref"]


# ---------------------------------------------------------------- 分类
def t_identity():
    """valid + invalid + unknown 必须恒等于 total。

    旧实现按出现次数算 valid、按去重算 invalid，重复引用时这条会破；
    两个数字对不上，前端就只能含糊其辞，用户也就不会再信角标。
    """
    a = ITEMS[0]["comment_ref"]
    text = f"甲[{a}]乙[{a}]丙[{OUTSIDE}]丁[CMT-GHOST]"
    c = veracity.classify(text, ITEMS, DS)
    eq(c["occurrences"], 4, "出现次数（含重复）")
    eq(c["total"], 3, "去重后的编号数")
    eq(c["valid"] + len(c["invalid"]) + len(c["unknown"]), c["total"], "三项之和应等于 total")
    return f"出现 {c['occurrences']} 次 / 去重 {c['total']} 个，恒等式成立"


def t_dedup():
    a = ITEMS[0]["comment_ref"]
    c = veracity.classify(f"[{a}] 和 [{a}]", ITEMS, DS)
    eq(c["total"], 1, "同一编号只算一个")
    eq(c["detail"][0]["occurrences"], 2, "出现次数应被记录")
    return "重复引用计 1 个编号、2 次出现"


def t_empty():
    c = veracity.classify("没有问题编号的一段话。", ITEMS, DS)
    eq(c["total"], 0, "total")
    eq(c["ok"], False, "没有编号不算通过")
    return "空引用：total=0、ok=False"


def t_invalid_vs_unknown():
    c = veracity.classify(f"[{OUTSIDE}] [CMT-GHOST]", ITEMS, DS)
    eq(c["invalid"], [OUTSIDE], "库里存在但未检索到 → invalid")
    eq(c["unknown"], ["CMT-GHOST"], "库里不存在 → unknown")
    return "invalid / unknown 未混"


def t_ref_regex_same():
    """两处正则必须一致：rag 用于旧字段统计，veracity 用于拦截判定。

    正则一旦漂移，会出现"旧角标说 3 处、新角标说 2 处"这种没法解释的差异。
    """
    eq(veracity.REF_RE.pattern, rag._REF_RE.pattern, "REF_RE 与 rag._REF_RE 不一致")
    return "pattern 一致"


# ---------------------------------------------------------------- 剔除
def t_strip_keeps_text():
    a = ITEMS[0]["comment_ref"]
    out, removed = veracity.strip_refs(f"食堂排队很久[{a}]。宿舍报修积压[CMT-GHOST]，情况明显。",
                                       ["CMT-GHOST"])
    eq(removed, ["CMT-GHOST"], "移除清单")
    truthy("食堂排队很久" in out and "情况明显" in out, f"句子被误伤：{out}")
    truthy(a in out, "有效编号被误删")
    truthy("GHOST" not in out, "问题编号仍在")
    return out


def t_strip_punctuation():
    out, _ = veracity.strip_refs("问题很集中[CMT-X][CMT-Y]，主要集中在食堂。", ["CMT-X", "CMT-Y"])
    eq(out, "问题很集中，主要集中在食堂。", "标点未收拾干净")
    eq("  " in out, False, "残留连续空格")
    return "连排编号删除后标点正常"


def t_strip_empty():
    out, removed = veracity.strip_refs("一段没有编号的话。", [])
    eq(out, "一段没有编号的话。", "无移除时不应改动正文")
    eq(removed, [], "移除清单应为空")
    return "无操作"


# ---------------------------------------------------------------- 六态
def _enforce(text, **kw):
    return veracity.enforce(text, ITEMS, DS, **kw)


def t_pass():
    a = ITEMS[0]["comment_ref"]
    r = _enforce(f"排队久[{a}]")
    eq(r["citation"]["verdict"], "pass", "verdict")
    eq(r["citation"]["calls"], 0, "pass 不应调用模型")
    eq(r["citation"]["enforced"], True, "enforced")
    return r["answer"]


def t_no_anchor():
    text = "食堂问题集中在排队时间上。"
    r = _enforce(text)
    eq(r["citation"]["verdict"], "no_anchor", "verdict")
    eq(r["answer"], text, "正文必须照常输出")
    return "正文未改"


def t_stripped():
    a = ITEMS[0]["comment_ref"]
    r = _enforce(f"排队久[{a}]积压[CMT-GHOST]", revise=lambda *a_: None)
    c = r["citation"]
    eq(c["verdict"], "stripped", "verdict")
    eq(c["removed"], ["CMT-GHOST"], "移除清单")
    eq(c["calls"], 1, "尝试过重写，应计 1 次")
    truthy(a in r["answer"], "有效编号被误删")
    return f"calls={c['calls']}"


def t_revised():
    a = ITEMS[0]["comment_ref"]
    r = _enforce(f"排队久[{a}]积压[CMT-GHOST]",
                 revise=lambda *a_: f"排队久[{a}]，积压情况需进一步取证。")
    c = r["citation"]
    eq(c["verdict"], "revised", "verdict")
    eq(c["revised"], True, "revised")
    eq(c["revised_count"], 1, "修正条数")
    return f"修正 {c['revised_count']} 处"


def t_revise_raises():
    """重写函数自己抛异常：不能冒泡成整轮失败，应退到剔除。"""
    a = ITEMS[0]["comment_ref"]

    def boom(*args):
        raise RuntimeError("模型挂了")

    r = _enforce(f"排队久[{a}]积压[CMT-GHOST]", revise=boom)
    c = r["citation"]
    eq(c["verdict"], "stripped", "verdict")
    truthy("CMT-GHOST" not in r["answer"], "问题编号未移除")
    return "重写失败 → 剔除，未冒泡"


def t_disabled():
    a = ITEMS[0]["comment_ref"]
    text = f"排队久[{a}]积压[CMT-GHOST]"
    r = _enforce(text, enabled=False)
    c = r["citation"]
    eq(r["answer"], text, "只记录模式不得改正文")
    eq(c["verdict"], "flagged", "verdict")
    eq(c["enforced"], False, "enforced")
    eq(c["calls"], 0, "不得调用模型")
    return "正文逐字未改"


def t_unverified():
    orig = veracity.classify
    veracity.classify = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        r = _enforce("任意回答")
    finally:
        veracity.classify = orig
    eq(r["answer"], "任意回答", "异常时必须放行原文")
    eq(r["citation"]["verdict"], "unverified", "verdict")
    return "放行原文"


def t_labels_complete():
    """六种 verdict 都必须有中文文案——前端角标直接用它，缺一个就是英文标识符露出来。"""
    for v in ("pass", "revised", "stripped", "no_anchor", "flagged", "unverified"):
        truthy(veracity.VERDICT_LABELS.get(v), f"verdict {v} 缺文案")
    return "6/6 有文案"


# ---------------------------------------------------------------- 数字
def t_numbers_ok():
    tr = [{"data": {"total": 278, "negative": 100, "rate": 36.0}, "metrics": []}]
    g = veracity.number_check("共 278 条，其中 100 条负面，占 36%。", tr)
    eq(g["passed"], True, f"未匹配：{g['unmatched']}")
    return f"检查 {g['checked']} 个"


def t_numbers_rounding():
    tr = [{"data": {"rate": 36.33}, "metrics": []}]
    eq(veracity.number_check("负面率 36.3%。", tr)["passed"], True, "四舍五入应放行")
    eq(veracity.number_check("负面率 36%。", tr)["passed"], True, "取整应放行")
    return "±0.11 / 1% 容差"


def t_numbers_year():
    tr = [{"data": {"total": 10}, "metrics": []}]
    eq(veracity.number_check("2023 年的数据显示问题集中。", tr)["passed"], True, "年份应放行")
    return "2018-2030 放行"


def t_numbers_diff():
    """派生差值放行：评委可以当场用两个已出现的数相减验算。"""
    tr = [{"data": {"a": 36.3, "b": 30.0}, "metrics": []}]
    eq(veracity.number_check("两者相差 6.3 个百分点。", tr)["passed"], True, "差值应放行")
    return "36.3 - 30.0 = 6.3"


def t_numbers_fake():
    tr = [{"data": {"total": 278}, "metrics": []}]
    g = veracity.number_check("共 278 条，其中 912 条极端负面。", tr)
    eq(g["passed"], False, "编造数字应被抓住")
    truthy(912.0 in g["unmatched"], f"未抓到 912：{g['unmatched']}")
    return f"抓到 {g['unmatched']}"


def t_numbers_ignore_refs():
    """引用编号里的十六进制串不能被当成数字——否则每一条正常回答都会报未匹配。"""
    tr = [{"data": {"total": 1}, "metrics": []}]
    g = veracity.number_check("食堂排队[CMT-8EDD66E0EF]。", tr)
    eq(g["passed"], True, f"编号被当成数字：{g['unmatched']}")
    return "编号不参与数字核验"


def t_summary():
    a = ITEMS[0]["comment_ref"]
    r = _enforce(f"排队久[{a}]")
    s = veracity.summary(r["citation"])
    truthy("pass" in s and "1/1" in s, f"摘要不完整：{s}")
    return s


def main():
    tenancy.set_context(tenancy.system_context())
    db.init_db()
    _load_items()
    if not ITEMS:
        raise SystemExit(f"{DS} 没有可用评论，无法构造用例")

    print("== 分类 ==")
    for name, fn in (("恒等式", t_identity), ("去重", t_dedup), ("空引用", t_empty),
                     ("invalid / unknown 不混", t_invalid_vs_unknown),
                     ("正则一致", t_ref_regex_same)):
        check(name, fn)

    print("\n== 剔除 ==")
    for name, fn in (("只删标记不删句子", t_strip_keeps_text),
                     ("标点收拾", t_strip_punctuation),
                     ("空操作", t_strip_empty)):
        check(name, fn)

    print("\n== 六态 ==")
    for name, fn in (("pass", t_pass), ("no_anchor", t_no_anchor),
                     ("stripped", t_stripped), ("revised", t_revised),
                     ("重写抛异常不冒泡", t_revise_raises), ("只记录模式", t_disabled),
                     ("核验抛异常放行", t_unverified), ("文案完整", t_labels_complete)):
        check(name, fn)

    print("\n== 数字核验 ==")
    for name, fn in (("正常数字", t_numbers_ok), ("四舍五入", t_numbers_rounding),
                     ("年份", t_numbers_year), ("派生差值", t_numbers_diff),
                     ("编造数字", t_numbers_fake), ("编号不误判", t_numbers_ignore_refs),
                     ("摘要", t_summary)):
        check(name, fn)

    print()
    if FAIL:
        print(f"结果：{PASS} 通过 / {FAIL} 失败")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print(f"结果：{PASS} 项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
