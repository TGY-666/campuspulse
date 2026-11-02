# -*- coding: utf-8 -*-
"""S2 验收：处置工单（M3）的**不变量**，不跑业务全路径。

和 verify_ticket.py 的分工：
    verify_ticket   跑"人怎么用"：建单→分派→处理→复核→归档，四角色轮流点一遍
    本脚本          跑"机器怎么保证"：工时算法、状态图、权限真值表、库层约束

为什么这些必须单独断言：
    全路径用例只能证明"照着正常流程走能通"，证明不了"绕路走不通"。
    状态图的可达性、终态没有出边、事件表改不动——这三件事一旦破，
    界面上的流程看着完全正常，只有真出问题那天才会发现。
    工时算法同理：周五 17:00 加 7 小时如果算成周六 24:00，
    只有周五傍晚建的 P1 工单会错，平时全都正常。

跑在数据库副本上，不碰演示库：
    CP_AUTH=1 CP_TICKETS=1 .venv/Scripts/python.exe tests/test_s2.py
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DB = os.path.join(ROOT, "data", "campuspulse.db")

# 必须在 import campuspulse 之前改环境：config 是在导入时读环境变量的。
_TMPDIR = tempfile.mkdtemp(prefix="cp_s2_")
COPY_DB = os.path.join(_TMPDIR, "s2.db")
if os.path.isfile(SOURCE_DB):
    _src = sqlite3.connect(SOURCE_DB)
    try:
        _dst = sqlite3.connect(COPY_DB)
        try:
            _src.backup(_dst)          # WAL 下直接复制文件会丢 -wal 里的内容
        finally:
            _dst.close()
    finally:
        _src.close()
else:
    COPY_DB = os.path.join(_TMPDIR, "s2_fresh.db")

os.environ["CP_DB_PATH"] = COPY_DB
os.environ["CP_AUTH"] = "1"
os.environ["CP_TICKETS"] = "1"
os.environ.pop("CP_HOLIDAYS", None)   # 节假日用例自己临时注入，不污染别的断言
sys.path.insert(0, ROOT)

from campuspulse import config, db, tenancy, tickets  # noqa: E402
from campuspulse.web import create_app  # noqa: E402

PASS = FAIL = 0
FAILED = []
TENANT = config.DEFAULT_TENANT_ID
ACCOUNTS = {}


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


def eq(actual, expect, what=""):
    if actual != expect:
        raise AssertionError(f"{what} 期望 {expect!r}，实际 {actual!r}")
    return actual


def truthy(value, what=""):
    if not value:
        raise AssertionError(f"{what} 期望为真，实际 {value!r}")
    return value


class Role:
    """临时切到某个角色身份，退出恢复。"""

    def __init__(self, role):
        self.role = role
        self.token = None

    def __enter__(self):
        self.token = tenancy.set_context({
            "tenant_id": TENANT, "tenant_name": "默认学校",
            "user_id": f"u_{self.role}", "username": self.role,
            "display_name": self.role, "role": self.role,
            "mode": "auth", "must_change": 0})
        return self

    def __exit__(self, *a):
        tenancy.reset_context(self.token)


# ============================ 一、工时与 SLA ============================
def t_workday_hours():
    """配置自洽：工作时段加起来必须等于 WORKDAY_HOURS。

    这个数被 _human_minutes 用来把分钟换算成"几个工作日"，
    它对不上不会报错，只会让"剩余 1 个工作日"实际是 6 小时。
    """
    total = sum(b - a for a, b in config.WORK_HOURS)
    eq(total, config.WORKDAY_HOURS, "WORK_HOURS 合计")
    return f"每天 {total} 个工作小时"


def t_same_day():
    got = tickets.add_work_hours(datetime(2026, 11, 2, 9, 0), 7)   # 周一 9:00
    eq(got, datetime(2026, 11, 2, 18, 0), "周一 9:00 + 7h")
    return "周一 9:00 + 7h → 同日 18:00"


def t_lunch_skip():
    """午休不计时：11:00 加 4 小时是 17:00，不是 15:00。"""
    got = tickets.add_work_hours(datetime(2026, 11, 2, 11, 0), 4)
    eq(got, datetime(2026, 11, 2, 17, 0), "周一 11:00 + 4h")
    return "11:00 + 4h → 17:00（12-14 点不计）"


def t_weekend():
    got = tickets.add_work_hours(datetime(2026, 10, 30, 17, 0), 7)  # 周五 17:00
    eq(got, datetime(2026, 11, 2, 17, 0), "周五 17:00 + 7h")
    return "周五 17:00 + 7h → 下周一 17:00"


def t_saturday_start():
    """周六开单：起算点必须推到周一，不能"周六 10 点就开始计时"。"""
    got = tickets.add_work_hours(datetime(2026, 10, 31, 10, 0), 4)  # 周六
    eq(got, datetime(2026, 11, 2, 15, 0), "周六 10:00 + 4h")
    return "周六 10:00 + 4h → 周一 15:00（9-12 三小时 + 14 点后一小时）"


def t_cross_day_minutes():
    """跨日按分钟续算：17:30 只剩 30 分钟，剩余 30 分钟落到次日 9:30。"""
    got = tickets.add_work_hours(datetime(2026, 11, 5, 17, 30), 1)  # 周四
    eq(got, datetime(2026, 11, 6, 9, 30), "周四 17:30 + 1h")
    return "周四 17:30 + 1h → 周五 9:30"


def t_midnight():
    """零点入参：先跳到 9:00 再开始计时。"""
    got = tickets.add_work_hours(datetime(2026, 11, 2, 0, 0), 1)
    eq(got, datetime(2026, 11, 2, 10, 0), "周一 0:00 + 1h")
    return "周一 0:00 + 1h → 10:00"


def t_holiday():
    """节假日顺延：注入 CP_HOLIDAYS 后当天整天不计时。"""
    old = config.HOLIDAYS
    try:
        config.HOLIDAYS = {"2026-11-02"}
        got = tickets.add_work_hours(datetime(2026, 11, 2, 9, 0), 7)
        eq(got, datetime(2026, 11, 3, 18, 0), "节假日 + 7h")
        return "周一设为节假日 → 顺延到周二 18:00"
    finally:
        config.HOLIDAYS = old


def t_minutes_basic():
    got = tickets.work_minutes_between(datetime(2026, 11, 2, 11, 0),
                                       datetime(2026, 11, 2, 17, 0))
    eq(round(got), 240, "11:00→17:00 工作分钟")
    return "11:00→17:00 = 240 分钟（扣午休 2 小时）"


def t_minutes_empty():
    eq(tickets.work_minutes_between(None, None), 0.0, "空值")
    eq(tickets.work_minutes_between("2026-11-03 10:00:00", "2026-11-02 10:00:00"), 0.0,
       "反向区间")
    return "空值/反向区间都返回 0，不会出现负数时长"


def t_minutes_weekend():
    got = tickets.work_minutes_between(datetime(2026, 10, 30, 17, 0),
                                       datetime(2026, 11, 2, 17, 0))
    eq(round(got), config.WORKDAY_HOURS * 60, "周五 17:00→周一 17:00")
    return "跨周末只算 1 个工作日（420 分钟）"


def t_sla_monotonic():
    start = datetime(2026, 11, 2, 9, 0)
    dues = [tickets.sla_due_for(p, start) for p in ("P0", "P1", "P2", "P3")]
    truthy(all(dues), "四档都有归档时限")
    for a, b in zip(dues, dues[1:]):
        truthy(a < b, f"时限应严格递增：{a} vs {b}")
    return " → ".join(f"{d:%m-%d %H:%M}" for d in dues)


def t_sla_state_unstarted():
    got = tickets.sla_state({"status": "draft", "sla_due": None})
    eq(got["text"], "未起算")
    eq(got["overdue"], False)
    return "draft 且无 sla_due → 未起算"


def t_sla_state_overdue():
    """超期判定用**库里那个值**比现在，不实时重算——重算会给出比实际更宽的时限。"""
    past = (datetime.now() - timedelta(days=3)).strftime(tickets.TS_FMT)
    got = tickets.sla_state({"status": "processing", "sla_due": past})
    truthy(got["overdue"], "过期时限应判定超期")
    truthy(got["text"].startswith("已超期"), f"文案应含已超期：{got['text']}")
    return got["text"]


def t_sla_state_closed():
    past = (datetime.now() - timedelta(days=3)).strftime(tickets.TS_FMT)
    got = tickets.sla_state({"status": "closed", "sla_due": past,
                             "closed_at": datetime.now().strftime(tickets.TS_FMT)})
    eq(got["text"], "超出时限归档")
    got2 = tickets.sla_state({"status": "closed", "sla_due": "2099-01-01 00:00:00",
                              "closed_at": datetime.now().strftime(tickets.TS_FMT)})
    eq(got2["text"], "已归档")
    return "归档后不再显示倒计时，改为记「按时/超时归档」"


# ============================ 二、状态机 ============================
def t_labels_complete():
    for s in tickets.ALL_STATUSES:
        truthy(tickets.STATUS_LABELS.get(s), f"状态 {s} 缺中文标签")
    for a in {r[0] for r in tickets.TRANSITIONS}:
        truthy(tickets.ACTION_LABELS.get(a), f"动作 {a} 缺中文标签")
    return f"{len(tickets.ALL_STATUSES)} 个状态、{len(tickets.ACTION_LABELS)} 个动作都有中文名"


def t_transition_count():
    eq(len(tickets.TRANSITIONS), 11, "迁移规则条数（文档 3.4）")
    keys = [(a, s) for a, s, _d, _r, _q in tickets.TRANSITIONS]
    eq(len(keys), len(set(keys)), "同一(动作,起始状态)不得重复")
    return "11 条规则，动作+起始状态无重复"


def t_targets_valid():
    for a, src, dst, roles, reqs in tickets.TRANSITIONS:
        truthy(src in tickets.ALL_STATUSES, f"{a}: 起始状态 {src} 未定义")
        truthy(dst in tickets.ALL_STATUSES, f"{a}: 目标状态 {dst} 未定义")
        truthy(src != dst, f"{a}: 起始与目标相同")
        for r in roles:
            truthy(r in config.ROLES, f"{a}: 角色 {r} 未定义")
        for q in reqs:
            truthy(q in ("note", "resolution", "owner"), f"{a}: 未知必填项 {q}")
    # 终态不能有"常规出边"，只允许 reopen
    for a, src, dst, _r, _q in tickets.TRANSITIONS:
        if src in tickets.CLOSED_STATUSES:
            eq((a, dst), ("reopen", "reopened"), f"终态 {src} 只允许 reopen")
    return "状态/角色/必填项全部合法，终态只有 reopen 一条出边"


def t_reachable_closed():
    """从 draft 出发必须能走到 closed：否则这单永远关不掉。"""
    graph = {}
    for a, src, dst, _r, _q in tickets.TRANSITIONS:
        graph.setdefault(src, set()).add(dst)
    seen, queue = {"draft"}, ["draft"]
    while queue:
        for nxt in graph.get(queue.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    truthy("closed" in seen, "draft 走不到 closed")
    missing = [s for s in tickets.OPEN_STATUSES
               if s != "draft" and s not in seen and s != "reopened"]
    eq(missing, [], "以下状态从 draft 不可达")
    truthy("closed" in seen, "closed 可达")
    return f"draft 可达 {len(seen)} 个状态，closed 在列"


def t_every_open_has_exit():
    """每个非终态至少有一条出边——否则就是"卡死状态"。"""
    outs = {}
    for a, src, _d, _r, _q in tickets.TRANSITIONS:
        outs[src] = outs.get(src, 0) + 1
    stuck = [s for s in tickets.OPEN_STATUSES if not outs.get(s)]
    eq(stuck, [], "卡死状态")
    return f"{len(tickets.OPEN_STATUSES)} 个进行中状态都有出边"


# ============================ 三、口径 ============================
def t_filter_keys():
    f = tickets.normalize_filters(None)
    eq(tuple(sorted(f.keys())), tuple(sorted(tickets.FILTER_KEYS)), "口径键集合")
    return f"{len(tickets.FILTER_KEYS)} 个口径键，与 FILTER_KEYS 一致"


def t_filter_reject_bad():
    """非法维度必须在建单前被挡，不能存进库再在详情页炸。"""
    try:
        tickets.normalize_filters({"dimensions": ["不存在的维度"]})
    except Exception as e:
        return f"非法维度被拒：{type(e).__name__}"
    raise AssertionError("非法维度没有被拒绝")


def t_filter_roundtrip():
    """口径必须能"存下来再原样复算"，这是 AC-M3-08 的算法级前提。"""
    f = tickets.normalize_filters({"dataset_id": config.DEFAULT_DATASET_ID})
    stored = json.loads(json.dumps(f, ensure_ascii=False))
    f2, ov = tickets.replay_filters(stored)
    eq(f2["dataset_id"], f["dataset_id"], "数据集还原")
    eq(ov["total"], tickets.replay_filters(stored)[1]["total"], "两次复算一致")
    truthy(ov["total"] >= 0, "总数非负")
    return f"口径 JSON 往返后复算 {ov['total']} 条"


# ============================ 四、权限真值表 ============================
def t_perms_table():
    expect = {
        # 角色: (能建单, 能处理, 能管理, 能看全部)
        "admin":   (True, True, True, True),
        "analyst": (True, False, False, True),
        "handler": (False, True, False, False),
        "viewer":  (False, False, False, False),
    }
    got = {}
    for role in ("admin", "analyst", "handler", "viewer"):
        with Role(role):
            got[role] = (tickets.can_create(), tickets.can_handle(),
                         tickets.can_manage(), tickets.can_view_any())
    eq(got, expect, "权限真值表")
    return "admin/analyst/handler/viewer × 4 项权限与文档 2.6 一致"


def t_viewer_blocked():
    with Role("viewer"):
        for fn, label in ((tickets.can_create, "建单"), (tickets.can_handle, "处理")):
            truthy(not fn(), f"只读角色不应能{label}")
        try:
            tickets.ensure_can_view()
        except tickets.Forbidden:
            return "只读角色看工单列表被 403，建单/处理权限均为 False"
        raise AssertionError("只读角色竟然能看工单列表")


def t_no_tenant_forbidden():
    """没有租户上下文时不许建单：否则会写出空归属的脏数据。"""
    token = tenancy.set_context({"tenant_id": "", "role": "admin", "user_id": "u_x",
                                 "username": "x", "display_name": "x",
                                 "mode": "auth", "must_change": 0})
    try:
        try:
            tickets.create({"title": "空租户探针"})
        except tickets.TicketError as e:
            eq(e.status, 403, "空租户建单状态码")
            return f"空租户建单被拒：{e}"
        raise AssertionError("空租户竟然建单成功")
    finally:
        tenancy.reset_context(token)


# ============================ 五、库层约束 ============================
def _master(kind, name):
    return db.scalar("SELECT COUNT(*) FROM sqlite_master WHERE type=? AND name=?",
                     (kind, name))


def t_tables_exist():
    for t in ("tickets", "ticket_events", "ticket_links"):
        eq(_master("table", t), 1, f"表 {t}")
    for idx in ("idx_tickets_tenant", "idx_tickets_code", "idx_ticket_events",
                "idx_ticket_links"):
        eq(_master("index", idx), 1, f"索引 {idx}")
    return "3 张表 + 4 个索引就位"


def t_trigger_append_only():
    """append-only 不是靠"接口里没写 UPDATE"，是靠触发器。"""
    eq(_master("trigger", "trg_ticket_events_no_update"), 1, "UPDATE 触发器")
    eq(_master("trigger", "trg_ticket_events_no_delete"), 1, "DELETE 触发器")
    row = db.query_one("SELECT event_id FROM ticket_events WHERE tenant_id = ? LIMIT 1",
                       (TENANT,))
    if not row:
        return "触发器就位（当前无事件可试删）"
    # 语句要带 tenant_id，否则会先被 db 层的漏写检测拦下——
    # 那一层拦得住不代表触发器在（换个人写原生 sqlite3 连接就绕过去了），
    # 所以这里必须真的让触发器自己报错。
    for sql in ("UPDATE ticket_events SET note='x' WHERE event_id=? AND tenant_id=?",
                "DELETE FROM ticket_events WHERE event_id=? AND tenant_id=?"):
        try:
            conn = db.get_conn()
            conn.execute(sql, (row["event_id"], TENANT))
            conn.commit()
        except Exception as e:
            truthy("append-only" in str(e), f"不是触发器拦的，报错是：{e}")
        else:
            raise AssertionError(f"事件表竟然允许：{sql.split()[0]}")
    eq(db.scalar("SELECT COUNT(*) FROM ticket_events WHERE event_id=? AND tenant_id=?",
                 (row["event_id"], TENANT)), 1, "事件仍在")
    return "UPDATE / DELETE 都被触发器拦住，事件还在"


def t_protected_tables():
    for t in ("tickets", "ticket_events", "ticket_links"):
        truthy(t in db.PROTECTED_TABLES, f"{t} 未纳入漏写检测")
    return "三张工单表都在 PROTECTED_TABLES 里，漏写 tenant_id 会抛异常"


def t_unique_code():
    """编号唯一性由库保证，不靠应用层"应该不会撞"。"""
    ddl = (db.scalar("SELECT sql FROM sqlite_master WHERE name='tickets'") or "").lower()
    ddl = " ".join(ddl.split())
    truthy("unique (tenant_id, code)" in ddl, f"缺 (tenant_id, code) 唯一约束：{ddl[-80:]}")
    idx = {r["name"] for r in db.query_all("PRAGMA index_list('tickets')")}
    truthy(any(n.startswith("sqlite_autoindex") for n in idx), "唯一约束没有生成索引")
    return "tickets 上有 tenant_id+code 唯一索引"


def t_code_format():
    code = tickets.next_code(datetime(2026, 11, 2, 10, 0))
    truthy(tickets._CODE_RE.match(code), f"编号格式不对：{code}")
    truthy(code.startswith("TK-20261102-"), f"日期段不对：{code}")
    return f"编号 {code}（按租户按天自增）"


# ============================ 六、接口最小集 ============================
def t_api_flow():
    """接口级只跑最短一条链：建单→确认→驳回，验证 403/409 与事件条数。"""
    app = create_app()
    client = app.test_client()
    r = client.post("/api/auth/login", json={"username": "s2_viewer",
                                             "password": ACCOUNTS["s2_viewer"]})
    eq(r.status_code, 200, "只读账号登录")

    r = client.post("/api/tickets", json={"title": "只读角色不该建单"})
    eq(r.status_code, 403, "只读角色建单")

    c2 = app.test_client()
    eq(c2.post("/api/auth/login", json={"username": "s2_analyst",
                                        "password": ACCOUNTS["s2_analyst"]}).status_code, 200,
       "分析员登录")
    r = c2.post("/api/tickets", json={"title": "S2 单元测试工单",
                                      "priority": "P3",
                                      "filters": {"dataset_id": config.DEFAULT_DATASET_ID}})
    eq(r.status_code, 200, f"分析员建单：{r.get_data(as_text=True)[:200]}")
    tid = r.get_json()["data"]["ticket_id"]

    r = c2.post(f"/api/tickets/{tid}/transition", json={"action": "approve"})
    eq(r.status_code, 409, "draft 直接 approve 应 409")

    r = c2.post(f"/api/tickets/{tid}/transition", json={"action": "triage"})
    eq(r.status_code, 200, "确认有效")

    # 「已分派前驳回」是分析员/管理员，「待分派后驳回」只有管理员（文档 3.4）
    r = c2.post(f"/api/tickets/{tid}/transition", json={"action": "reject", "note": "测试驳回"})
    eq(r.status_code, 403, "分析员不应能驳回已确认的单")
    c3 = app.test_client()
    eq(c3.post("/api/auth/login", json={"username": "s2_admin",
                                        "password": ACCOUNTS["s2_admin"]}).status_code, 200,
       "管理员登录")
    r = c3.post(f"/api/tickets/{tid}/transition", json={"action": "reject", "note": "测试驳回"})
    eq(r.status_code, 200, f"管理员驳回：{r.get_data(as_text=True)[:160]}")

    ev = tickets.list_events(tid)
    # create 也是一条事件：建单本身要留痕，否则"谁在什么时候建的"只能靠 tickets 表猜
    eq(len(ev), 3, "事件条数 = create + triage + reject")
    eq([e["type"] for e in ev], ["create", "triage", "reject"], "事件顺序")
    with Role("admin"):
        t = tickets.get(tid)
    eq(t["status"], "rejected", "终态")
    truthy(t.get("disclaimer"), "详情必须带免责声明")
    return f"建单→确认→驳回 3 步走通，事件 {len(ev)} 条，终态 rejected"


def setup():
    token = tenancy.set_context(tenancy.system_context())
    db.init_db()
    conn = db.get_conn()
    for u in ("s2_analyst", "s2_viewer", "s2_admin"):
        conn.execute("DELETE FROM users WHERE tenant_id=? AND username=?", (TENANT, u))
    conn.commit()
    for u, role in (("s2_analyst", "analyst"), ("s2_viewer", "viewer"),
                    ("s2_admin", "admin")):
        row = tenancy.create_user(TENANT, u, role=role, must_change=0)
        ACCOUNTS[u] = row["password"]
    tenancy.reset_context(token)


def main():
    # 第一批用例是纯函数，但口径类用例要查库：没有上下文时 db.tid() 是空字符串，
    # 查数据集会查不到并报"数据集不存在"，看起来像数据没了，其实是身份没装。
    tenancy.set_context(tenancy.system_context())
    print("== 工时与 SLA（纯函数）==")
    for n, f in [
        ("WORK_HOURS 与 WORKDAY_HOURS 自洽", t_workday_hours),
        ("同日加班时段折算", t_same_day),
        ("午休不计时", t_lunch_skip),
        ("跨周末顺延", t_weekend),
        ("非工作日开单起算点顺延", t_saturday_start),
        ("跨日按分钟续算", t_cross_day_minutes),
        ("零点入参", t_midnight),
        ("节假日整天不计时", t_holiday),
        ("工作分钟：扣午休", t_minutes_basic),
        ("工作分钟：空值/反向为 0", t_minutes_empty),
        ("工作分钟：跨周末只算一天", t_minutes_weekend),
        ("四档时限严格递增", t_sla_monotonic),
        ("未起算态", t_sla_state_unstarted),
        ("超期态", t_sla_state_overdue),
        ("归档后不再倒计时", t_sla_state_closed),
    ]:
        check(n, f)

    print("\n== 状态机不变量 ==")
    for n, f in [
        ("状态与动作都有中文标签", t_labels_complete),
        ("迁移规则条数与唯一性", t_transition_count),
        ("状态/角色/必填项合法且终态无常规出边", t_targets_valid),
        ("draft 可达 closed", t_reachable_closed),
        ("没有卡死状态", t_every_open_has_exit),
    ]:
        check(n, f)

    print("\n== 口径 ==")
    for n, f in [
        ("口径键集合", t_filter_keys),
        ("非法维度被拒", t_filter_reject_bad),
        ("口径存下来能原样复算", t_filter_roundtrip),
    ]:
        check(n, f)

    print("\n== 权限 ==")
    for n, f in [("四角色权限真值表", t_perms_table),
                 ("只读角色边界", t_viewer_blocked),
                 ("空租户不许建单", t_no_tenant_forbidden)]:
        check(n, f)

    print("\n== 库层约束 ==")
    for n, f in [
        ("表与索引就位", t_tables_exist),
        ("事件表 append-only", t_trigger_append_only),
        ("受保护表含工单三表", t_protected_tables),
        ("编号唯一约束", t_unique_code),
        ("编号格式", t_code_format),
    ]:
        check(n, f)

    print("\n== 接口最小集 ==")
    setup()
    check("建单/跳步/驳回 全链路", t_api_flow)

    print("\n" + "=" * 68)
    print(f"结果：{PASS}/{PASS + FAIL} 项通过" + (f"，失败 {FAIL} 项" if FAIL else ""))
    for n in FAILED:
        print(f"  失败：{n}")
    return 0 if not FAIL else 1


def _cleanup():
    shutil.rmtree(_TMPDIR, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _cleanup()
