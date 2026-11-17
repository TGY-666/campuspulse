# -*- coding: utf-8 -*-
"""区间研判的不变量：日期窗口、上期推算、幂等、只研判不建单、空区间、权限。

跑在数据库副本上，不碰演示库：
    CP_AUTH=1 CP_TICKETS=1 .venv/Scripts/python.exe tests/test_monthly.py

为什么这些要单独断言，而不是"页面点一遍看着对"：
    · 上期区间算错了只有跨月、跨年那几天会错（1 月 1 日往前是去年 12 月 31 日），平时全对；
    · 幂等破了的表现是"同一段多出几条研判记录、多出一批重复工单"，
      当天看不出来，三个月后工单页没法看；
    · "研判不建单"这条最容易被好心改坏——有人觉得"顺手建一张更省事"，
      结果工单页多出一批没人认领的单，而页面上看不出是哪来的；
    · 窗口口径错了最隐蔽：数字都还在，只是它不是"那段时间"的数字。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DB = os.path.join(ROOT, "data", "campuspulse.db")

_TMPDIR = tempfile.mkdtemp(prefix="cp_monthly_")
COPY_DB = os.path.join(_TMPDIR, "monthly.db")
if os.path.isfile(SOURCE_DB):
    _src = sqlite3.connect(SOURCE_DB)
    try:
        _dst = sqlite3.connect(COPY_DB)
        try:
            _src.backup(_dst)              # WAL 下直接复制文件会丢 -wal 里的内容
        finally:
            _dst.close()
    finally:
        _src.close()

os.environ["CP_DB_PATH"] = COPY_DB
os.environ["CP_AUTH"] = "1"
os.environ["CP_TICKETS"] = "1"
os.environ["CP_MONTHLY"] = "1"
sys.path.insert(0, ROOT)

from campuspulse import config, db, monthly, tenancy, tickets      # noqa: E402
from campuspulse.tickets import Forbidden                          # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        detail = fn()
        PASS.append(name)
        print(f"  OK  {name}" + (f" — {detail}" if detail else ""))
    except Exception as e:
        FAIL.append(f"{name}: {e}")
        print(f"  !!  {name} — {type(e).__name__}: {e}")


def eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg} 期望 {b!r}，实际 {a!r}")


DS = "ds_2023_all"           # 覆盖 2023-09-01 ~ 2023-11-20，够测跨月区间
R_FROM, R_TO = "2023-10-01", "2023-10-31"      # 一个完整自然月
SUB_FROM, SUB_TO = "2023-10-05", "2023-11-03"  # 一段跨月区间（30 天）


# ============================ 1. 日期窗口 ============================
def t_month_math():
    eq(monthly.month_window("2026-02"), ("2026-02-01", "2026-02-28"), "平年 2 月")
    eq(monthly.month_window("2028-02"), ("2028-02-01", "2028-02-29"), "闰年 2 月")
    eq(monthly.month_window("2026-12"), ("2026-12-01", "2026-12-31"), "跨年 12 月")
    eq(monthly.shift_period("2026-01", -1), "2025-12", "1 月往前是去年 12 月")
    eq(monthly.shift_period("2025-12", 1), "2026-01", "12 月往后是明年 1 月")
    eq(monthly.last_closed_period(date(2026, 1, 15)), "2025-12", "上个月跨年")
    return "闰年 / 跨年 / 多条月回退都对"


def t_range_parse():
    eq(monthly.resolve_range("2026-09-15", "2026-10-14"), ("2026-09-15", "2026-10-14"), "原样返回")
    eq(monthly.resolve_range("2023-10-05", "2023-10-05"), ("2023-10-05", "2023-10-05"), "单日区间")
    eq(monthly.resolve_range(period="2026-10"), ("2026-10-01", "2026-10-31"), "整月")
    bad = [
        ("2026-10-01", None, "缺结束"),
        (None, "2026-10-01", "缺开始"),
        ("2026-10-31", "2026-10-01", "开始晚于结束"),
        ("2026-13-01", "2026-13-02", "月份非法"),
        ("2026-02-30", "2026-03-01", "日期不存在"),
        ("2026/10/01", "2026-10-02", "格式不对"),
        ("2019-01-01", "2026-12-31", "跨度超 366 天"),
    ]
    for a, b, why in bad:
        try:
            monthly.resolve_range(a, b)
        except monthly.TicketError:
            continue
        raise AssertionError(f"「{why}」应该被拒绝：{a} ~ {b}")
    return f"单日 / 整月 / 缺一头 / 反向 / 非法 / 超长（{len(bad)} 种）都按预期处理"


def t_previous_range():
    # 整月 → 上一个自然月（"9 月比 8 月"才是人话）
    eq(monthly.previous_range("2026-10-01", "2026-10-31"), ("2026-09-01", "2026-09-30"), "整月")
    eq(monthly.previous_range("2026-01-01", "2026-01-31"), ("2025-12-01", "2025-12-31"), "整月跨年")
    eq(monthly.previous_range("2028-03-01", "2028-03-31"), ("2028-02-01", "2028-02-29"), "整月闰年")
    # 任意区间 → 等长前移（跨度不变，紧挨着）
    eq(monthly.previous_range("2026-09-15", "2026-10-14"),
       ("2026-08-16", "2026-09-14"), "30 天区间前移")
    eq(monthly.previous_range("2026-10-05", "2026-10-05"),
       ("2026-10-04", "2026-10-04"), "单日区间前移")
    return "整月按自然月、任意区间等长前移，跨年与闰年都对"


def t_label_and_key():
    eq(monthly.range_label("2026-10-01", "2026-10-31"), "2026-10", "整月标签")
    eq(monthly.is_full_month("2026-02-01", "2026-02-28"), True, "平年 2 月算整月")
    eq(monthly.is_full_month("2026-02-01", "2026-02-27"), False, "少一天不算整月")
    # 6/1~8/31 是三个月，不是"六月"：判错会让区间标签撞上真正的六月，
    # 两条不同口径的记录共用同一个 (数据集, 期) 唯一键，互相覆盖。
    eq(monthly.is_full_month("2026-06-01", "2026-08-31"), False, "跨月整段不算整月")
    eq(monthly.is_full_month("2025-12-01", "2026-01-31"), False, "跨年整段不算整月")
    eq(monthly.range_label("2026-06-01", "2026-08-31"), "2026-06-01~2026-08-31", "跨月标签")
    eq(monthly.previous_range("2026-06-01", "2026-08-31"),
       ("2026-03-01", "2026-05-31"), "跨月区间等长前移（92 天对 92 天）")
    eq(monthly.range_label("2026-09-15", "2026-10-14"), "2026-09-15~2026-10-14", "区间标签")
    eq(monthly.range_key("2026-09-15", "2026-10-14"), "20260915-20261014", "规则号键")
    if len(f"RNG-{monthly.range_key('2026-09-15', '2026-10-14')}-食堂餐饮") > 32:
        raise AssertionError("规则号会超 32 字上限，被截断到维度名中间")
    return "整月用 YYYY-MM，其余用起~止；跨月不再被当成整月；规则号长度安全"


# ============================ 2. 窗口口径 ============================
def t_window_count():
    """研判的 sample 必须等于该区间的 kept 评论数，而不是全时段累计。"""
    n = db.scalar("SELECT COUNT(*) FROM comments WHERE tenant_id = ? AND dataset_id = ? "
                  "AND cleaning_status = 'kept' AND comment_date >= ? AND comment_date <= ?",
                  (db.tid(), DS, SUB_FROM, SUB_TO))
    out = monthly.run(date_from=SUB_FROM, date_to=SUB_TO, dataset_id=DS,
                      trigger="manual", use_ai=False)
    eq(out["sample"], n, "区间评论数")
    eq((out["date_from"], out["date_to"]), (SUB_FROM, SUB_TO), "日期区间")
    eq(out["period"], f"{SUB_FROM}~{SUB_TO}", "区间标签")
    return f"{SUB_FROM}~{SUB_TO} 共 {n} 条"


def t_growth_labels():
    """增速项的文案要跟着区间走：整月"本月 vs 上月"，任意区间"本期 vs 上期"。

    文案不算功能，但它是唯一能让人看出"这个百分比在讲哪段时间"的东西；
    任意区间上写"本月"，读的人会以为系统又按自然月算了。
    """
    out = monthly.run(date_from=SUB_FROM, date_to=SUB_TO, dataset_id=DS,
                      trigger="manual", use_ai=False, force=True)
    for t in out["topics"]:
        for p in t["parts"]:
            if p["name"] == "增速":
                if "近 30 天" in p["label"]:
                    raise AssertionError(f"{t['dimension']} 的增速仍写近 30 天")
                if "本期" not in p["label"] and "样本不足" not in p["label"]:
                    raise AssertionError(f"任意区间的增速该写本期/上期：{p['label']}")
                return p["label"]
    raise AssertionError("没有找到增速项")


def t_prev_month_only():
    """区间负面要跟 SQL 对齐；上期只影响增速项，不该污染当期统计。"""
    from campuspulse import departments
    p1, p2 = monthly.previous_range(SUB_FROM, SUB_TO)
    data = departments.assess(DS, use_ai=False,
                              window={"period": "x", "date_from": SUB_FROM, "date_to": SUB_TO,
                                      "prev_from": p1, "prev_to": p2})
    if not data["topics"]:
        raise AssertionError("这段区间没有主题，无法验证")
    t = data["topics"][0]
    n = db.scalar("SELECT COUNT(*) FROM comments c, json_each(c.dimensions) j "
                  "WHERE c.tenant_id = ? AND c.dataset_id = ? AND c.cleaning_status = 'kept' "
                  "AND c.sentiment = 'negative' AND j.value = ? AND c.comment_date >= ? "
                  "AND c.comment_date <= ?", (db.tid(), DS, t["dimension"], SUB_FROM, SUB_TO))
    eq(t["negative"], n, f"{t['dimension']} 区间内负面")
    eq(data["window"]["prev_from"], p1, "上期起点")
    return f"{t['dimension']} 区间负面 {n} 条与 SQL 一致；上期 {p1}~{p2}"


# ============================ 3. 研判不建单 / 建单靠人点 ============================
def _purge_range_tickets(d1, d2):
    """清掉本脚本在副本库里造的工单，让每一段互不干扰。

    刻意不碰 ticket_events：那张表是 append-only 的（触发器拦 DELETE），
    这正是要保住的性质。留下的孤儿事件只存在于临时副本里，随目录一起删掉。
    """
    key = monthly.range_key(d1, d2)
    conn = db.get_conn()
    conn.execute("DELETE FROM ticket_links WHERE tenant_id = ? AND ticket_id IN "
                 "(SELECT ticket_id FROM tickets WHERE tenant_id = ? AND rule_id LIKE ?)",
                 (db.tid(), db.tid(), f"RNG-{key}-%"))
    conn.execute("DELETE FROM tickets WHERE tenant_id = ? AND rule_id LIKE ?",
                 (db.tid(), f"RNG-{key}-%"))
    conn.commit()


def t_idempotent():
    again = monthly.run(date_from=SUB_FROM, date_to=SUB_TO, dataset_id=DS,
                        trigger="schedule", use_ai=False)
    if not again.get("skipped"):
        raise AssertionError("同一区间第二次研判没有跳过")
    rows = db.query_all("SELECT run_id FROM risk_runs WHERE tenant_id = ? AND dataset_id = ? "
                        "AND period = ?", (db.tid(), DS, f"{SUB_FROM}~{SUB_TO}"))
    eq(len(rows), 1, "同区间记录条数")
    return "同一区间只留一条记录，第二次直接返回既有快照"


def t_no_auto_tickets():
    """研判只出结论：跑完不该有任何工单，也不该有"待确认"这种中间态。

    这是这一版的核心取舍——没有门槛、不自动开单。它一旦回潮（比如有人
    又加回"风险≥70 自动建"），工单页会静默多出一批没人认领的单，
    而页面上看不出是哪来的。
    """
    _purge_range_tickets(SUB_FROM, SUB_TO)
    out = monthly.run(date_from=SUB_FROM, date_to=SUB_TO, dataset_id=DS,
                      trigger="manual", use_ai=False, force=True)
    eq(out["tickets"], 0, "研判不该建单")
    eq(out["ticket_codes"], [], "研判不该产出单号")
    if not out["topics"]:
        raise AssertionError("该区间没有主题，无法验证")
    for t in out["topics"]:
        if t.get("ticket_code"):
            raise AssertionError(f"{t['dimension']} 研判后竟带上了单号 {t['ticket_code']}")
        if "ticket_pending" in t:
            raise AssertionError(f"{t['dimension']} 还留有待确认标记")
    n = db.scalar("SELECT COUNT(*) FROM tickets WHERE tenant_id = ? AND rule_id LIKE ?",
                  (db.tid(), f"RNG-{monthly.range_key(SUB_FROM, SUB_TO)}-%"))
    eq(n, 0, "落库工单数")
    return f"{len(out['topics'])} 个主题，0 张单"


def t_manual_ticket():
    """人在页面上点某个主题 → 才建单，且单里的数字/口径来自研判快照。"""
    out = monthly.run(date_from=SUB_FROM, date_to=SUB_TO, dataset_id=DS,
                      trigger="manual", use_ai=False, force=True)
    target = out["topics"][0]
    after = monthly.create_tickets(out["run_id"], [target["dimension"]])
    made = after.get("created") or []
    eq(len(made), 1, "点名建单数")
    code = made[0]["code"]

    row = db.query_one("SELECT code, rule_id, filters, department_name FROM tickets "
                       "WHERE tenant_id = ? AND code = ?", (db.tid(), code))
    if not row:
        raise AssertionError(f"工单 {code} 没有落库")
    key = monthly.range_key(SUB_FROM, SUB_TO)
    if not (row["rule_id"] or "").startswith(f"RNG-{key}-"):
        raise AssertionError(f"规则号没带区间键：{row['rule_id']}")
    f = json.loads(row["filters"] or "{}")
    if (f.get("date_from"), f.get("date_to")) != (SUB_FROM, SUB_TO):
        raise AssertionError("工单筛选口径没带这段区间")
    if f.get("dataset_id") != DS:
        raise AssertionError("工单筛选口径没带数据集")

    # 快照要跟着更新：这一行以后显示单号，而不是永远长着一个「建单」按钮
    t2 = [t for t in after["topics"] if t["dimension"] == target["dimension"]][0]
    eq(t2.get("ticket_code"), code, "快照里的单号")
    eq(after["counts"]["linked"], 1, "已挂单主题数")
    eq(after["tickets"], 1, "记录里的已建单数")
    return f"{target['dimension']} → {code}（规则号 {row['rule_id']}）"


def t_ticket_dedup():
    """同一段区间同一主题再点一次：不许建出第二张，且把已有单号挂回快照。"""
    out = monthly.run(date_from=SUB_FROM, date_to=SUB_TO, dataset_id=DS,
                      trigger="manual", use_ai=False)
    target = [t for t in out["topics"] if t.get("ticket_code")]
    if not target:
        raise AssertionError("上一步建的工单没挂回快照，无法验证去重")
    dim = target[0]["dimension"]
    code = target[0]["ticket_code"]
    after = monthly.create_tickets(out["run_id"], [dim])
    eq(len(after.get("created") or []), 0, "重复点的建单数")
    failed = after.get("failed") or []
    if not failed or failed[0].get("code") != code:
        raise AssertionError(f"重复点时没指出已有单号：{failed}")
    n = db.scalar("SELECT COUNT(*) FROM tickets WHERE tenant_id = ? AND rule_id = ?",
                  (db.tid(), monthly._ticket_rule(SUB_FROM, SUB_TO, dim)))
    eq(n, 1, "同主题同区间的工单数")
    return f"{dim} 再点一次仍是 {code}，库里 1 张"


def t_rerun_attaches():
    """重跑研判要把已有单号挂回去：页面不该在已有单的主题上留「建单」按钮。"""
    out = monthly.run(date_from=SUB_FROM, date_to=SUB_TO, dataset_id=DS,
                      trigger="manual", use_ai=False, force=True)
    hit = [t for t in out["topics"] if t.get("ticket_code")]
    if not hit:
        raise AssertionError("重跑后没有把已有工单挂回快照")
    for t in hit:
        if not t.get("ticket_existing"):
            raise AssertionError(f"{t['dimension']} 挂的是本次新建单？研判不该建单")
    eq(out["tickets"], len(hit), "已建单数应等于挂上单号的主题数")
    return f"重跑后 {len(hit)} 个主题显示已有单号"


def t_create_needs_dimension():
    """建单必须点名主题：不允许"全部建单"，否则等于把门槛换个名字。"""
    out = monthly.run(date_from=SUB_FROM, date_to=SUB_TO, dataset_id=DS,
                      trigger="manual", use_ai=False)
    for bad in (None, [], ["", "  "]):
        try:
            monthly.create_tickets(out["run_id"], bad)
        except monthly.TicketError as e:
            if e.code != "invalid_dimension":
                raise AssertionError(f"错误码不对：{e.code}")
            continue
        raise AssertionError(f"没有点名主题也能建单：{bad!r}")
    after = monthly.create_tickets(out["run_id"], ["不存在的主题"])
    failed = after.get("failed") or []
    if not failed or "没有这个主题" not in (failed[0].get("error") or ""):
        raise AssertionError(f"未知主题没被指出来：{failed}")
    eq(len(after.get("created") or []), 0, "未知主题的建单数")
    return "缺主题 / 空列表 / 未知主题都不会建出单来"


# ============================ 3. 部门侧区间研判 ============================
def t_preview_scoped_and_ephemeral():
    """部门账号的区间研判：只含本部门主题、带齐拆解、且**不落库**。

    这条不变量比"能不能跑"重要得多。一旦把部门结果写进 risk_runs，
    同一个 (数据集, 区间) 唯一键会被覆盖——全校快照会悄悄变成
    "只有后勤"的版本，而页面上那条记录的名字一模一样，没人看得出来。
    """
    from campuspulse import departments, config as cfg
    dept = [d for d in departments.list_all() if d["name"] == "后勤保障处"][0]
    before = db.scalar("SELECT COUNT(*) FROM risk_runs WHERE tenant_id = ?", (db.tid(),))

    token = tenancy.set_context({
        "tenant_id": cfg.DEFAULT_TENANT_ID, "tenant_name": "t", "user_id": "u_dept",
        "username": "houqin", "display_name": "后勤", "role": "dept", "mode": "test",
        "must_change": 0, "department_id": dept["dept_id"],
        "department_name": dept["name"]})
    try:
        out = monthly.preview_range(date_from=SUB_FROM, date_to=SUB_TO,
                                    dataset_id=DS, use_ai=False)
    finally:
        tenancy.reset_context(token)

    after = db.scalar("SELECT COUNT(*) FROM risk_runs WHERE tenant_id = ?", (db.tid(),))
    eq(after, before, "部门区间研判不该新增 risk_runs 记录")
    eq(out.get("preview"), True, "部门侧结果应带 preview 标记")
    if not out["topics"]:
        raise AssertionError("这段区间后勤一个主题都没有，无法验证范围")
    for t in out["topics"]:
        if (t.get("department") or {}).get("dept_id") != dept["dept_id"]:
            raise AssertionError(f"{t['dimension']} 归口 {(t.get('department') or {}).get('name')}，"
                                 f"不是本部门却出现在部门侧结果里")
        # 点一行要能看到拆解/建议/证据，缺一项那行点开就是空白
        if len(t.get("parts") or []) != 4:
            raise AssertionError(f"{t['dimension']} 的风险拆解不是四项")
        if not t.get("advice"):
            raise AssertionError(f"{t['dimension']} 没有整改建议")
        if t["negative"] and not t.get("samples"):
            raise AssertionError(f"{t['dimension']} 有负面却没有代表证据")
    return (f"{len(out['topics'])} 个主题全归{dept['name']}，拆解/建议/证据齐备，"
            f"研判记录数 {before} 未变")


def t_preview_needs_view():
    """没有研判权限的角色不能借这个接口跑区间研判。"""
    token = _as("")
    try:
        try:
            monthly.preview_range(date_from=SUB_FROM, date_to=SUB_TO, dataset_id=DS, use_ai=False)
        except Forbidden:
            return "无权限角色被拒绝"
        raise AssertionError("没有研判权限也能跑区间研判")
    finally:
        tenancy.reset_context(token)


# ============================ 4. 空区间与调度 ============================
def t_empty_range():
    """区间里没数据要**明确报错并给出可用范围**，不能偷偷换一段（旧版会回落到最新月份）。"""
    try:
        monthly.run(date_from="2026-10-01", date_to="2026-10-31", dataset_id=DS,
                    trigger="manual", use_ai=False)
    except monthly.TicketError as e:
        if e.code != "no_data":
            raise AssertionError(f"错误码不对：{e.code}")
        span = (e.extra or {}).get("dataset_span") or {}
        if not span.get("from"):
            raise AssertionError("没有带回可用范围，用户不知道往哪改")
        n = db.scalar("SELECT COUNT(*) FROM risk_runs WHERE tenant_id = ? AND dataset_id = ? "
                      "AND period = ?", (db.tid(), DS, "2026-10"))
        eq(n, 0, "空区间不该留下记录")
        return f"报 no_data 并带回可用范围 {span['from']} ~ {span['to']}"
    raise AssertionError("空区间居然跑成功了")


def t_tick_guard():
    """定时任务遇到"这个月没数据"要等，不能自动去翻炒旧数据；跑过的月份要跳过。"""
    msg = monthly.tick_once(period="2026-10", dataset_id=DS)
    if "无评论数据" not in msg:
        raise AssertionError(f"没有数据时应当等待，实际返回：{msg}")
    n = db.scalar("SELECT COUNT(*) FROM risk_runs WHERE tenant_id = ? AND dataset_id = ? "
                  "AND period = '2026-10'", (db.tid(), DS))
    eq(n, 0, "无数据月份不该留下记录")

    first = monthly.tick_once(period="2023-10", dataset_id=DS)     # 整月第一次：应该真跑
    if "研判完成" not in first:
        raise AssertionError(f"有数据的月份第一次应当执行，实际返回：{first}")
    second = monthly.tick_once(period="2023-10", dataset_id=DS)    # 第二次：跳过
    if "已研判" not in second:
        raise AssertionError(f"已有记录时应跳过，实际返回：{second}")
    return "无数据等待 / 首次执行 / 再次跳过，三种都正确"


# ============================ 5. 权限 ============================
def _as(role):
    return tenancy.set_context({"tenant_id": config.DEFAULT_TENANT_ID, "tenant_name": "t",
                                "user_id": "u_" + role, "username": role,
                                "display_name": role, "role": role, "mode": "test",
                                "must_change": 0})


def t_permissions():
    tok = _as("viewer")
    try:
        for name, fn in (("跑研判", lambda: monthly.run(date_from=SUB_FROM, date_to=SUB_TO,
                                                      dataset_id=DS, use_ai=False)),
                         ("建单", lambda: monthly.create_tickets("rr_x", ["食堂餐饮"]))):
            try:
                fn()
            except Forbidden:
                continue
            raise AssertionError(f"只读角色居然能{name}")
    finally:
        tenancy.reset_context(tok)

    tok = _as("analyst")
    try:
        out = monthly.run(date_from=SUB_FROM, date_to=SUB_TO, dataset_id=DS,
                          use_ai=False, force=True)
        if out.get("skipped"):
            raise AssertionError("force 重跑却跳过了")
    finally:
        tenancy.reset_context(tok)
    return "只读不能跑、不能建单；分析员能跑"


# ============================ 主流程 ============================
def main():
    db.init_db()
    token = tenancy.set_context(tenancy.system_context())
    try:
        if not db.get_dataset(DS):
            print(f"缺少数据集 {DS}，先跑一次 python run.py 让它自动载入内置数据")
            return 2
        print("== 日期窗口 ==")
        check("自然月窗口与跨年回退", t_month_math)
        check("任意区间的解析与拒绝", t_range_parse)
        check("上期区间：整月 vs 等长前移", t_previous_range)
        check("区间标签与规则号键", t_label_and_key)

        print("\n== 窗口口径 ==")
        check("样本量限定在该区间", t_window_count)
        check("增速文案改成本期/上期", t_growth_labels)
        check("区间负面与 SQL 一致", t_prev_month_only)

        print("\n== 幂等与建单 ==")
        check("同区间不重复研判", t_idempotent)
        check("研判只出结论，不建单", t_no_auto_tickets)
        check("点名某个主题才建单", t_manual_ticket)
        check("同区间同主题不重复建", t_ticket_dedup)
        check("重跑把已有单号挂回快照", t_rerun_attaches)
        check("建单必须点名主题", t_create_needs_dimension)

        print("\n== 部门侧区间研判 ==")
        check("只含本部门、拆解齐备、不落库", t_preview_scoped_and_ephemeral)
        check("无研判权限不能跑", t_preview_needs_view)

        print("\n== 空区间与调度 ==")
        check("空区间明确报错并给出可用范围", t_empty_range)
        check("定时检查的两条守卫", t_tick_guard)

        print("\n== 权限 ==")
        check("角色边界", t_permissions)
    finally:
        tenancy.reset_context(token)
        shutil.rmtree(_TMPDIR, ignore_errors=True)

    print("\n" + "=" * 60)
    if FAIL:
        print(f"结果：{len(PASS)}/{len(PASS) + len(FAIL)} 项通过，{len(FAIL)} 项失败")
        for f in FAIL:
            print("  -", f)
        return 1
    print(f"结果：{len(PASS)}/{len(PASS)} 项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
