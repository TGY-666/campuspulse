# -*- coding: utf-8 -*-
"""演示数据种子：建 3 个角色账号 + 4 张覆盖不同状态的工单。

为什么需要它：工单页在空库上是四个 0，讲不出"从看到到做到"的故事；
但也**不能**自动建单——那正是我们要避免的"机器定性"。所以这份种子
只用于演示前准备，跑一次即可，且每张单都写明是演示数据。

幂等：同标题的工单已存在就跳过。账号已存在则只重置密码并打印。

运行：
    CP_AUTH=1 CP_TICKETS=1 .venv/Scripts/python.exe tools/seed_demo_tickets.py
    ... --reset-passwords   一并把已有账号的密码重置为新随机值
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CP_AUTH", "1")
os.environ.setdefault("CP_TICKETS", "1")
sys.path.insert(0, ROOT)

from campuspulse import config, db, tenancy, tickets  # noqa: E402

DS = "ds_2023_all"          # 2023 秋招季样本：食堂负面集中，规则建议会命中
OUT_FILE = os.path.join(config.DATA_DIR, "demo_accounts.txt")

ACCOUNTS = [("analyst01", "analyst", "分析员·小林"),
            ("handler01", "handler", "处置员·后勤"),
            ("viewer01", "viewer", "只读·校领导")]

# (标题, 优先级, 维度, 动作链, 结论/说明)
PLAN = [
    {
        "title": "食堂餐饮窗口排队时间过长（午间高峰）",
        "priority": "P1", "dimension": "食堂餐饮",
        "summary": "由规则 R2 命中：该维度负面 278 条，占该维度 71.8%。集中反映午间排队 20 分钟以上。",
        "path": ["triage", "assign"],       # 停在「处理中」之前 → assigned
        "owner": "handler01",
        "note": "",
    },
    {
        "title": "宿舍热水供应时段不稳定",
        "priority": "P1", "dimension": "宿舍住宿",
        "summary": "多条评论反映 22:00 后热水温度不足，影响晚自习回寝学生。",
        "path": ["triage", "assign", "start"],
        "owner": "handler01",
        "note": "",
    },
    {
        "title": "招生报考咨询回复时效偏慢",
        "priority": "P2", "dimension": "招生报考",
        "summary": "新生在评论区询问报到流程，多条反馈「问了没人回」。属流程类问题，非事件。",
        "path": [],                          # 停在 draft（待确认）
        "owner": None,
        "note": "",
    },
    {
        "title": "校园环境垃圾清运不及时（教学楼 B 座）",
        "priority": "P2", "dimension": "校园环境",
        "summary": "教学楼 B 座三层垃圾堆积两日未清运，已影响课间通行。",
        "path": ["triage", "assign", "start", "respond", "approve"],
        "owner": "handler01",
        "note": "已联系后勤保洁班组，增加每日 14:00 与 17:30 两次清运，下周复查。",
        "review": "处置到位，归档。复查结果已记录在处置说明中。",
    },
]


def ensure_accounts(reset=False):
    lines = ["CampusPulse 演示账号（seed_demo_tickets.py 生成）", "=" * 40]
    for uname, role, disp in ACCOUNTS:
        existed = tenancy.get_user(config.DEFAULT_TENANT_ID, uname)
        if existed and not reset:
            lines.append(f"{uname:<12} {config.ROLE_LABELS[role]:<4} {disp}  （已存在，密码未改动）")
            continue
        if existed:
            pwd = tenancy.random_password()
            tenancy.set_password(existed["user_id"], pwd, clear_must_change=True)
        else:
            pwd = tenancy.create_user(config.DEFAULT_TENANT_ID, uname, role=role,
                                      display_name=disp, must_change=0)["password"]
        lines.append(f"{uname:<12} {config.ROLE_LABELS[role]:<4} {disp}  密码：{pwd}")
    lines.append("")
    lines.append("以上账号仅用于演示。正式部署请删除或改密。")
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    try:
        os.chmod(OUT_FILE, 0o600)
    except OSError:
        pass
    print("\n".join(lines))
    print(f"\n已写入 {os.path.relpath(OUT_FILE, ROOT)}")


def seed():
    """默认只用已有的 admin 账号跑通全链路，不新建任何账号。

    角色账号（分析员 / 处置员 / 只读）需要用 --with-accounts 显式创建——
    自动造账号会把密码写进文件，属于"默认不该发生"的操作。
    """
    admin = tenancy.get_user(config.DEFAULT_TENANT_ID, "admin")
    if not admin:
        raise SystemExit("没有 admin 账号：先以 CP_AUTH=1 启动一次应用生成初始管理员")
    analyst = tenancy.get_user(config.DEFAULT_TENANT_ID, "analyst01") or admin
    handler = tenancy.get_user(config.DEFAULT_TENANT_ID, "handler01") or admin

    created = 0
    for item in PLAN:
        exists = db.query_one(
            "SELECT ticket_id FROM tickets WHERE tenant_id = ? AND title = ?",
            (config.DEFAULT_TENANT_ID, item["title"]))
        if exists:
            print(f"跳过（已存在）：{item['title']}")
            continue

        tok = tenancy.set_context(_ctx(analyst))
        try:
            t = tickets.create({
                "title": item["title"], "summary": item["summary"],
                "priority": item["priority"], "dimension": item["dimension"],
                "filters": {"dataset_id": DS, "dimension": [item["dimension"]]},
            })
        finally:
            tenancy.reset_context(tok)
        tid = t["ticket_id"]

        for action in item["path"]:
            actor = {"triage": admin, "assign": admin, "start": handler,
                     "respond": handler, "approve": admin}[action]
            body = {"action": action}
            if action == "triage":
                body.update({"priority": item["priority"], "dimension": item["dimension"]})
            if action == "assign":
                body["owner_id"] = handler["user_id"]
            if action == "respond":
                body["note"] = item["note"]
            if action == "approve":
                body["note"] = item.get("review") or "处置到位，归档"
            tok = tenancy.set_context(_ctx(actor))
            try:
                tickets.transition(tid, body)
            finally:
                tenancy.reset_context(tok)
        d = db.query_one("SELECT code, status FROM tickets WHERE tenant_id = ? "
                         "AND ticket_id = ?", (config.DEFAULT_TENANT_ID, tid))
        print(f"已建 {d['code']}  {tickets.STATUS_LABELS[d['status']]:<4}  {item['title']}")
        created += 1
    print(f"\n本次新建 {created} 张工单（数据集 {DS}）")


def _ctx(user):
    return {"tenant_id": user["tenant_id"], "tenant_name": "默认学校",
            "user_id": user["user_id"], "username": user["username"],
            "display_name": user.get("display_name") or user["username"],
            "role": user["role"], "mode": "auth", "must_change": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-accounts", action="store_true",
                    help="同时创建 analyst01 / handler01 / viewer01 三个演示账号")
    ap.add_argument("--reset-passwords", action="store_true", help="配合 --with-accounts 重置密码")
    ap.add_argument("--accounts-only", action="store_true", help="只建账号，不建工单")
    args = ap.parse_args()

    boot = tenancy.set_context(tenancy.system_context())
    try:
        db.init_db()
        if args.with_accounts:
            ensure_accounts(reset=args.reset_passwords)
        if not args.accounts_only:
            seed()
    finally:
        tenancy.reset_context(boot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
