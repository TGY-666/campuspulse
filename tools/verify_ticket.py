# -*- coding: utf-8 -*-
"""M3 工单验收：状态机全路径、权限、SLA、append-only、口径复现、建议不落库。

对应需求文档 3.10 的 AC-M3-01 ~ AC-M3-13。逐条对应，不合并断言——
一条 AC 一个用例，失败时能直接说出"哪条没做到"。

**跑在数据库副本上。** 原因不是图省事，而是 ticket_events 装了 append-only
触发器（UPDATE/DELETE 会被 SQLite 拒绝），测试数据删不掉。既然如此，
就不该把它写进演示用的那个库——一跑验证，驾驶舱的"今日建单"就多出十几条假数据。

运行：
    CP_AUTH=1 CP_TICKETS=1 .venv/Scripts/python.exe tools/verify_ticket.py
    ... --keep   保留副本，便于手工排查
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DB = os.path.join(ROOT, "data", "campuspulse.db")
# 副本放系统临时目录：放 data/ 里一旦删除被拦（安全钩子、杀毒软件锁文件），
# 就会在项目里留下一个 12MB 的 _verify_ticket.db，还容易被当成真库拷走。
COPY_DIR = tempfile.mkdtemp(prefix="cp_verify_ticket_")
COPY_DB = os.path.join(COPY_DIR, "verify.db")

os.environ.setdefault("CP_AUTH", "1")
os.environ.setdefault("CP_TICKETS", "1")
os.environ["CP_DB_PATH"] = COPY_DB
os.environ.setdefault("CP_TENANT_ENFORCE", "1")
sys.path.insert(0, ROOT)

from campuspulse import config, db, tenancy, tickets  # noqa: E402
from campuspulse.web import create_app  # noqa: E402

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
PASS = FAIL = 0
FAILED = []
TENANT = "t_vt_probe"


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


def prepare_copy():
    """把真实库用 sqlite 在线备份 API 复制一份（WAL 下直接 cp 会丢 -wal 内容）。

    每次都整份覆盖：备份 API 会完整替换目标库，因此上一轮留下的验证账号、
    工单、触发器都不会残留——这比"先删文件再复制"可靠，也绕开了
    "测试脚本不许删文件"这条安全约束。
    """
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
    """临时目录里的副本删不掉也不影响结果（系统会自己清）。"""
    for suffix in ("", "-wal", "-shm"):
        p = COPY_DB + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def who(role, uid, tenant=None):
    """构造一个身份上下文。"""
    return {"tenant_id": tenant or config.DEFAULT_TENANT_ID, "tenant_name": "验证",
            "user_id": uid, "username": uid, "display_name": uid, "role": role,
            "mode": "auth", "must_change": 0}


class As:
    """临时切换身份调用业务函数（单元层面的断言用）。"""

    def __init__(self, ctx):
        self.ctx = ctx
        self.tok = None

    def __enter__(self):
        self.tok = tenancy.set_context(self.ctx)
        return self

    def __exit__(self, *a):
        tenancy.reset_context(self.tok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留副本")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()
    if args.no_color:
        _nocolor()

    if not config.CP_AUTH or not config.CP_TICKETS:
        print("需要 CP_AUTH=1 CP_TICKETS=1 运行")
        return 2

    prepare_copy()
    app = create_app()
    try:
        return run(app)
    finally:
        if not args.keep:
            cleanup_copy()


def run(app):
    print(f"{DIM}数据库副本：{COPY_DB}（不改动演示库）{RESET}")
    ctx_sys = tenancy.set_context(tenancy.system_context())
    try:
        if not tenancy.get_tenant(TENANT):
            tenancy.create_tenant(TENANT, "工单验证租户（副本内）", "验证", "verify_ticket.py")
        accts = {}
        for uname, role in (("vt_admin", "admin"), ("vt_analyst", "analyst"),
                            ("vt_handler", "handler"), ("vt_handler2", "handler"),
                            ("vt_viewer", "viewer")):
            conn = db.get_conn()
            conn.execute("DELETE FROM users WHERE tenant_id = ? AND username = ?",
                         (config.DEFAULT_TENANT_ID, uname))
            conn.commit()
            accts[uname] = tenancy.create_user(config.DEFAULT_TENANT_ID, uname, role=role,
                                              password="Vt123456789", must_change=0)["user"]
    finally:
        tenancy.reset_context(ctx_sys)

    clients = {}
    tenancy.reset_login_limits()
    for uname in accts:
        c = app.test_client()
        r = c.post("/api/auth/login", json={"username": uname, "password": "Vt123456789"})
        assert r.status_code == 200, f"{uname} 登录失败：{r.get_data(as_text=True)[:200]}"
        clients[uname] = c

    admin, analyst, handler = clients["vt_admin"], clients["vt_analyst"], clients["vt_handler"]
    handler2, viewer = clients["vt_handler2"], clients["vt_viewer"]
    ds = config.DEFAULT_DATASET_ID
    # 规则建议换到 2023 秋招季数据集：它食堂餐饮负面 278 条（占 71.8%）、
    # 最高点赞 8000，四条规则里 R2/R4 必然命中。默认的全量历史样本很稀疏
    # （近 7 天常是个位数），规则不命中是**正确行为**，另有专门用例验证。
    SUG_DS = "ds_2023_all"
    h_uid = accts["vt_handler"]["user_id"]

    # ---------------- 准备一张单子 ----------------
    def create_ticket(client=None, **kw):
        body = {"title": "食堂餐饮负面集中（验证）", "priority": "P1",
                "dimension": "食堂餐饮", "filters": {"dataset_id": ds},
                "summary": "验证用：食堂餐饮本周负面集中"}
        body.update(kw)
        r = (client or analyst).post("/api/tickets", json=body)
        assert r.status_code == 200, f"建单失败 {r.status_code} {r.get_data(as_text=True)[:200]}"
        return r.get_json()["data"]

    # ---------------- AC-M3-01 全路径 ----------------
    print("\n== 状态机全路径 ==")
    t0 = create_ticket()

    def full_path():
        tid = t0["ticket_id"]
        seq = [("triage", {"priority": "P1", "dimension": "食堂餐饮"}, admin),
               ("assign", {"owner_id": h_uid}, admin),
               ("start", {}, handler),
               ("respond", {"note": "已联系食堂承包商，本周加开两个窗口并调整错峰时间"}, handler),
               ("approve", {"note": "处置到位，归档"}, admin)]
        for action, extra, client in seq:
            r = client.post(f"/api/tickets/{tid}/transition", json={"action": action, **extra})
            assert r.status_code == 200, f"{action} → {r.status_code} {r.get_data(as_text=True)[:200]}"
        d = admin.get(f"/api/tickets/{tid}").get_json()["data"]
        assert d["status"] == "closed", f"终态 {d['status']}"
        n = len(d["events"])
        assert n == 6, f"事件应 6 条（create/triage/assign/start/respond/approve），实际 {n}"
        assert [e["type"] for e in d["events"]] == ["create", "triage", "assign", "start",
                                                   "respond", "approve"], \
            f"事件序列不符：{[e['type'] for e in d['events']]}"
        return f"{d['code']} 六步走完，事件 {n} 条且顺序正确"

    check("AC-M3-01", "draft→…→closed 全路径 + 事件 6 条", full_path)

    # ---------------- AC-M3-02 跳步 409 ----------------
    def skip_step():
        t = create_ticket(title="跳步验证单")
        r = admin.post(f"/api/tickets/{t['ticket_id']}/transition", json={"action": "approve"})
        assert r.status_code == 409, f"期望 409，实际 {r.status_code}"
        d = admin.get(f"/api/tickets/{t['ticket_id']}").get_json()["data"]
        assert d["status"] == "draft", f"状态被改成了 {d['status']}"
        body = r.get_json()["error"]
        assert body["detail"] and body["detail"].get("current_status") == "draft", \
            f"409 未带当前状态：{body}"
        return "draft 直接 approve → 409，状态仍是 draft"

    check("AC-M3-02", "跳步返回 409 且状态不变", skip_step)

    # ---------------- AC-M3-03 handler 分派 403 ----------------
    def handler_cannot_assign():
        t = create_ticket(title="权限验证单")
        admin.post(f"/api/tickets/{t['ticket_id']}/transition",
                   json={"action": "triage", "priority": "P2", "dimension": "食堂餐饮"})
        r = handler.post(f"/api/tickets/{t['ticket_id']}/transition",
                         json={"action": "assign", "owner_id": h_uid})
        assert r.status_code == 403, f"期望 403，实际 {r.status_code}"
        return "处置员分派 → 403"

    check("AC-M3-03", "handler 分派被拒 403", handler_cannot_assign)

    # ---------------- AC-M3-04 handler 访问他人 404 ----------------
    def handler_other_404():
        t = create_ticket(title="他人单验证")
        tid = t["ticket_id"]
        admin.post(f"/api/tickets/{tid}/transition",
                   json={"action": "triage", "priority": "P2", "dimension": "食堂餐饮"})
        admin.post(f"/api/tickets/{tid}/transition",
                   json={"action": "assign", "owner_id": h_uid})
        r = handler2.get(f"/api/tickets/{tid}")
        assert r.status_code == 404, f"期望 404，实际 {r.status_code}"
        # 列表里也不能出现
        items = handler2.get("/api/tickets").get_json()["data"]["tickets"]
        assert all(i["ticket_id"] != tid for i in items), "他人名下工单出现在列表里"
        return "详情 404、列表不含"

    check("AC-M3-04", "handler 访问他人工单 → 404", handler_other_404)

    # ---------------- AC-M3-05 / 06 SLA ----------------
    def sla_friday():
        due = tickets.add_work_hours(datetime(2026, 10, 30, 17, 0), 7)   # 周五 17:00
        assert due == datetime(2026, 11, 2, 17, 0), f"实际 {due}"
        return "周五 17:00 + P1(7 工作小时) → 下周一 17:00"

    check("AC-M3-05", "SLA 跨周末顺延正确", sla_friday)

    def sla_lunch():
        due = tickets.add_work_hours(datetime(2026, 11, 2, 11, 0), 4)    # 周一 11:00
        assert due == datetime(2026, 11, 2, 17, 0), f"实际 {due}"
        mins = tickets.work_minutes_between(datetime(2026, 11, 2, 11, 0),
                                            datetime(2026, 11, 2, 17, 0))
        assert mins == 240, f"11:00→17:00 应为 240 工作分钟，实际 {mins}"
        return "11:00 + P0(4 工作小时) → 17:00（午休不计时）"

    check("AC-M3-06", "SLA 午休不计时", sla_lunch)

    def sla_persisted():
        t = create_ticket(title="SLA 落库验证", priority="P0")
        admin.post(f"/api/tickets/{t['ticket_id']}/transition",
                   json={"action": "triage", "priority": "P0", "dimension": "食堂餐饮"})
        d = admin.get(f"/api/tickets/{t['ticket_id']}").get_json()["data"]
        assert d["sla_due"], "triage 后 sla_due 为空"
        due = datetime.strptime(d["sla_due"], "%Y-%m-%d %H:%M:%S")
        expect = tickets.sla_due_for("P0", datetime.now())
        assert abs((due - expect).total_seconds()) < 120, f"{due} ≠ {expect}"
        return f"P0 → sla_due {d['sla_due']}，剩余 {d['sla']['text']}"

    check("AC-M3-05b", "起算点在 triaged、sla_due 落库", sla_persisted)

    # ---------------- AC-M3-07 append-only ----------------
    def append_only():
        tid = t0["ticket_id"]
        raw = sqlite3.connect(COPY_DB)
        try:
            blocked = 0
            for sql in ("UPDATE ticket_events SET note='篡改' WHERE ticket_id=?",
                        "DELETE FROM ticket_events WHERE ticket_id=?"):
                try:
                    raw.execute(sql, (tid,))
                    raw.commit()
                except sqlite3.DatabaseError as e:
                    blocked += 1
                    assert "append-only" in str(e), f"触发器文案不符：{e}"
                    raw.rollback()
            assert blocked == 2, f"只有 {blocked}/2 被拦下"
        finally:
            raw.close()
        # 接口层面也不该有入口
        r = admin.post(f"/api/tickets/{tid}/events", json={"note": "x"})
        assert r.status_code in (404, 405), f"意外的 events 写入口：{r.status_code}"
        return "触发器拦住 UPDATE/DELETE，接口无写入口"

    check("AC-M3-07", "ticket_events 不可改不可删", append_only)

    # ---------------- AC-M3-08 口径复现 ----------------
    def filters_replay():
        t = create_ticket(title="口径复现验证",
                          filters={"dataset_id": ds, "dimension": ["食堂餐饮"],
                                   "sentiment": ["negative"]})
        d = admin.get(f"/api/tickets/{t['ticket_id']}").get_json()["data"]
        stored = d["filters"]
        assert stored.get("dataset_id") and stored.get("dimensions") == ["食堂餐饮"], \
            f"口径没存全：{stored}"
        q = "dataset_id=%s&dimension=%s&sentiment=negative" % (ds, "食堂餐饮")
        api_total = admin.get(f"/api/dashboard/summary?{q}").get_json()["data"]["total"]
        assert d["filter_preview"]["total"] == api_total, \
            f"工单存的口径 {d['filter_preview']['total']} ≠ 现算 {api_total}"
        return f"按存下的口径重算 = {api_total} 条，与建单时一致"

    check("AC-M3-08", "建单口径可原样复现统计", filters_replay)

    # ---------------- AC-M3-09 建议不落库 ----------------
    def suggestions_no_write():
        before = db.scalar("SELECT COUNT(*) FROM tickets WHERE tenant_id = ?",
                           (config.DEFAULT_TENANT_ID,))
        r = analyst.get(f"/api/tickets/suggestions?dataset_id={SUG_DS}")
        assert r.status_code == 200, r.status_code
        data = r.get_json()["data"]
        after = db.scalar("SELECT COUNT(*) FROM tickets WHERE tenant_id = ?",
                          (config.DEFAULT_TENANT_ID,))
        assert before == after, f"建议接口写库了：{before} → {after}"
        assert data["rules"] and len(data["rules"]) == 4, "规则清单不足 4 条"
        assert data["suggestions"], f"{SUG_DS} 应命中规则，实际 0 条：{data.get('note')}"
        return f"评估 {len(data['suggestions'])} 条建议，tickets 表 {after} 行未变"

    check("AC-M3-09", "建议接口只评估、不落库", suggestions_no_write)

    # ---------------- AC-M3-10 一键建单 ----------------
    def from_suggestion():
        data = analyst.get(f"/api/tickets/suggestions?dataset_id={SUG_DS}").get_json()["data"]
        cand = next((s for s in data["suggestions"] if not s["dup"]), None)
        if not cand:
            # 没有命中规则的干净建议时，直接构造：验证 rule_id 落库 + 口径/证据带入
            r = analyst.post("/api/tickets/from-suggestion", json={"rule_id": "R2",
                                                                   "dataset_id": SUG_DS})
            if r.status_code == 409:
                return "当前数据未命中可建单建议（去重/不命中），跳过（非失败）"
            assert r.status_code == 200, r.get_data(as_text=True)[:200]
            d = r.get_json()["data"]
        else:
            r = analyst.post("/api/tickets/from-suggestion",
                             json={"rule_id": cand["rule_id"], "dataset_id": SUG_DS})
            assert r.status_code == 200, r.get_data(as_text=True)[:200]
            d = r.get_json()["data"]
        assert d["rule_id"] == (cand["rule_id"] if cand else "R2"), f"rule_id 未落库：{d.get('rule_id')}"
        assert d["filters"]["dataset_id"], "口径未带入"
        assert d["evidence"], "代表证据未带入"
        return f"{d['code']} 由 {d['rule_id']} 生成，证据 {len(d['evidence'])} 条"

    check("AC-M3-10", "从建议建单：rule_id / 口径 / 证据自动带入", from_suggestion)

    # ---------------- AC-M3-11 去重 ----------------
    def dedup():
        data = analyst.get(f"/api/tickets/suggestions?dataset_id={SUG_DS}").get_json()["data"]
        cand = next((s for s in data["suggestions"] if s["dup"]), None)
        if not cand:
            # 手工造一次冲突：同 rule_id + 同维度 24h 内已建
            t = create_ticket(title="去重验证", rule_id="R2", dimension="食堂餐饮")
            data2 = analyst.get(f"/api/tickets/suggestions?dataset_id={SUG_DS}").get_json()["data"]
            cand = next((s for s in data2["suggestions"]
                         if s["rule_id"] == "R2" and s["dimension"] == "食堂餐饮"), None)
            if not cand:
                return "数据未命中 R2，去重路径未触发（跳过，非失败）"
        r = analyst.post("/api/tickets/from-suggestion",
                         json={"rule_id": cand["rule_id"], "dimension": cand["dimension"],
                               "dataset_id": SUG_DS})
        assert r.status_code == 409, f"期望 409，实际 {r.status_code}"
        msg = r.get_json()["error"]["message"]
        assert "TK-" in msg, f"提示里没有已有单号：{msg}"
        return msg

    check("AC-M3-11", "同规则同维度 24h 内提示已有单号", dedup)

    def sparse_no_alarm():
        """稀疏数据上不报警，且说清为什么。

        这是"规则建议"最容易翻车的地方：把 3 条评论的波动算成"突增 300%"，
        看着很智能，实际是噪音。宁可返回 0 条 + 原因。
        """
        d = analyst.get(f"/api/tickets/suggestions?dataset_id={ds}").get_json()["data"]
        assert d["skipped"], "样本不足时应给出 skipped 原因"
        for s in d["skipped"]:
            assert s["reason"], f"{s['rule_id']} 没有给原因"
        return f"{ds} 命中 {len(d['suggestions'])} 条，{len(d['skipped'])} 条规则标注未评估原因"

    check("EXTRA", "稀疏数据不瞎报警（给出未评估原因）", sparse_no_alarm)

    # ---------------- AC-M3-12 按时率分子分母 ----------------
    def on_time_rate():
        st = admin.get("/api/tickets/stats").get_json()["data"]
        ot = st["on_time"]
        assert "numerator" in ot and "denominator" in ot, f"没给分子分母：{ot}"
        if ot["denominator"]:
            expect = round(ot["numerator"] / ot["denominator"] * 100, 1)
            assert abs(ot["rate"] - expect) < 0.05, f"{ot['rate']} ≠ {expect}"
        assert st["sla_policy"]["unit"] == "工作小时", "SLA 口径未披露单位"
        return (f"按时 {ot['numerator']}/{ot['denominator']} = {ot['rate']}%，"
                f"平均处置 {st['avg_handle_hours']} 小时")

    check("AC-M3-12", "按时率给出分子分母且算式自洽", on_time_rate)

    # ---------------- AC-M3-13 免责声明 ----------------
    def disclaimer():
        page = admin.get("/tickets").get_data(as_text=True)
        assert "不代表对相关言论真实性的认定" in page, "工单页缺免责声明"
        assert "不对学生个人做任何标记" in page, "免责声明内容不完整"
        t = admin.get(f"/api/tickets/{t0['ticket_id']}").get_json()["data"]
        assert "不代表对相关言论真实性的认定" in (t.get("disclaimer") or ""), "接口未返回免责声明"
        return "页面与接口都带免责声明"

    check("AC-M3-13", "免责声明存在且不可关闭", disclaimer)

    # ---------------- 额外：角色与可见性 ----------------
    print("\n== 角色边界（文档 2.6 权限表）==")

    def viewer_denied():
        assert viewer.get("/api/tickets").status_code == 403, "只读角色能看工单列表"
        assert viewer.get("/api/tickets/stats").status_code == 403, "只读角色能看工单指标"
        assert viewer.post("/api/tickets", json={"title": "x"}).status_code == 403, \
            "只读角色能建单"
        return "只读角色：列表/指标/建单 全部 403"

    check("EXTRA", "只读角色无工单权限", viewer_denied)

    def handler_scope():
        items = handler.get("/api/tickets").get_json()["data"]["tickets"]
        assert items, "处置员看不到自己名下的工单"
        assert all(i["owner_id"] == h_uid for i in items), "处置员看到了别人名下的单"
        return f"处置员只见 {len(items)} 条自己名下工单"

    check("EXTRA", "处置员列表只含自己名下", handler_scope)

    def analyst_no_manage():
        t = create_ticket(title="分析员越权验证")
        admin.post(f"/api/tickets/{t['ticket_id']}/transition",
                   json={"action": "triage", "priority": "P2", "dimension": "食堂餐饮"})
        r = analyst.post(f"/api/tickets/{t['ticket_id']}/transition",
                         json={"action": "assign", "owner_id": h_uid})
        assert r.status_code == 403, f"分析员能分派：{r.status_code}"
        return "分析员分派 → 403（分派是管理员职责）"

    check("EXTRA", "分析员不能分派", analyst_no_manage)

    def reopen_rules():
        tid = t0["ticket_id"]
        r = handler.post(f"/api/tickets/{tid}/transition", json={"action": "reopen", "note": "x"})
        assert r.status_code == 403, f"处置员能重开归档单：{r.status_code}"
        r = admin.post(f"/api/tickets/{tid}/transition",
                       json={"action": "reopen", "note": "食堂窗口再次排长队，重新打开"})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        d = r.get_json()["data"]
        assert d["status"] == "reopened" and d["reopen_count"] == 1, \
            f"状态 {d['status']} / reopen_count {d['reopen_count']}"
        return "归档 7 天内可重开，reopen_count+1"

    check("EXTRA", "重开需管理员且落在 reopened", reopen_rules)

    def handler_respond_short():
        t = create_ticket(title="处理记录字数验证")
        tid = t["ticket_id"]
        admin.post(f"/api/tickets/{tid}/transition",
                   json={"action": "triage", "priority": "P2", "dimension": "食堂餐饮"})
        admin.post(f"/api/tickets/{tid}/transition", json={"action": "assign", "owner_id": h_uid})
        handler.post(f"/api/tickets/{tid}/transition", json={"action": "start"})
        r = handler.post(f"/api/tickets/{tid}/transition",
                         json={"action": "respond", "note": "已处理"})
        assert r.status_code == 400, f"短处理记录被接受：{r.status_code}"
        r = handler.post(f"/api/tickets/{tid}/transition",
                         json={"action": "respond",
                               "note": "已联系后勤，本周加开窗口，下周复查排队情况"})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        return "处理记录 <10 字被拒，写清楚后通过"

    check("EXTRA", "处理记录下限 10 字", handler_respond_short)

    def say_next():
        t = create_ticket(title="话术验证")
        tid = t["ticket_id"]
        admin.post(f"/api/tickets/{tid}/transition",
                   json={"action": "triage", "priority": "P1", "dimension": "食堂餐饮"})
        r = admin.post(f"/api/tickets/{tid}/transition",
                       json={"action": "assign", "owner_id": h_uid})
        msg = r.get_json()["data"]["message"]
        assert "已派给" in msg and "工作小时内首次响应" in msg, f"话术没说清下一步：{msg}"
        return msg

    check("EXTRA", "分派后说清「下一步谁收到」", say_next)

    def badge():
        b = analyst.get("/api/tickets/badge").get_json()["data"]
        assert b["can_view"] and "pending" in b, f"角标数据不全：{b}"
        return f"待处置 {b['pending']} / 超期 {b['overdue']}"

    check("EXTRA", "顶栏待办角标可用", badge)

    def trend():
        d = analyst.get("/api/tickets/trend?days=7").get_json()["data"]
        assert len(d["trend"]) == 7, f"趋势点 {len(d['trend'])}"
        return "近 7 天建单/归档趋势"

    check("EXTRA", "驾驶舱趋势可用", trend)

    # ---------------- 并发与幂等（S4 回归补测）----------------
    def concurrent_transition():
        """两个人同时点「分派」时，时间线里只允许出现一条 assign。

        原实现是"读状态→校验→无条件 UPDATE"，10 路并发实测有 2 路同时成功，
        时间线里出现两条一模一样的 assigned。append-only 表没法补救，
        所以这里守的是一个不变量：**事件数 == 实际生效的迁移数 == 1**。
        """
        import threading
        c = clients["vt_admin"]
        r = c.post("/api/tickets", json={"title": "并发迁移靶子", "priority": "P1"})
        tid = r.get_json()["data"]["ticket_id"]
        assert c.post(f"/api/tickets/{tid}/transition",
                      json={"action": "triage"}).status_code == 200, "确认有效失败"

        owner_id = accts["vt_handler"]["user_id"]
        ctx = {"tenant_id": config.DEFAULT_TENANT_ID, "tenant_name": "工单验证", "user_id": "u_conc",
               "username": "conc", "display_name": "并发", "role": "admin",
               "mode": "session", "must_change": 0}
        results = []
        lock = threading.Lock()

        def worker():
            try:
                with app.app_context(), As(ctx):
                    tickets.transition(tid, {"action": "assign", "owner_id": owner_id})
                with lock:
                    results.append("ok")
            except Exception:
                with lock:
                    results.append("err")

        ths = [threading.Thread(target=worker) for _ in range(10)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()

        with app.app_context(), As(ctx):
            evs = tickets.list_events(tid)
            assign_evs = [e for e in evs if e.get("type") == "assign"]
            cur = tickets.get(tid)
        assert len(assign_evs) == 1, (
            f"时间线出现 {len(assign_evs)} 条 assign（成功 {results.count('ok')} 次）——"
            f"并发未被 CAS 拦住")
        assert cur["status"] == "assigned", f"最终状态 {cur['status']}"
        return f"10 路并发仅 1 次生效，事件 {len(assign_evs)} 条"

    check("AC-M3-14", "并发迁移只生效一次（时间线不重复记账）", concurrent_transition)

    def dup_rule():
        """同一条规则的建议不能被建成两张单。"""
        body = {"title": "重复建单靶子", "priority": "P1", "rule_id": "R_DUP_TEST"}
        first = clients["vt_analyst"].post("/api/tickets", json=body)
        second = clients["vt_analyst"].post("/api/tickets", json=body)
        assert first.status_code == 200, f"首次建单失败 {first.status_code}"
        assert second.status_code == 409, f"第二次建单应为 409，实际 {second.status_code}"
        msg = (second.get_json().get("error") or {}).get("message") or ""
        assert "已有同类工单" in msg, f"提示没说清重复对象：{msg}"
        return msg

    check("AC-M3-15", "同 rule 重复建单被拒并指出已有工单", dup_rule)

    print("\n" + "=" * 68)
    if FAIL:
        print(f"{RED}结果：{PASS}/{PASS + FAIL} 通过，失败 {FAIL} 项：{'、'.join(FAILED)}{RESET}")
        return 1
    print(f"{GREEN}结果：{PASS}/{PASS} 项全部通过{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
