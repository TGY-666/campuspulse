# -*- coding: utf-8 -*-
"""给三个部门各造一张演示工单（部门账号登录后有事可做）。

**为什么需要这个脚本**：库里原有的工单都是人工建的，没有 `department_id`，
部门账号按归口过滤后看到的是空列表——演示时点进去像坏了。

它建的是**真实流程里会出现的单**，不是凭空写库：
create（draft）→ triage（定优先级、起算 SLA）→ assign（派给该部门账号），
其中一张再走一步 start 进入"处理中"，两种状态都看得到。

工单带 `rule_id = DEPT-DEMO-<维度>` 作为标记，`--clean` 只删这些，
不会碰历史工单。删是物理删除（连带 ticket_events / links），因为它们的唯一
用途就是演示，留着反而会污染指标。
"""
import argparse
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from campuspulse import config, db, departments, tenancy, tickets  # noqa: E402

# 旧版本用 `DEPT-DEMO-<维度>` 当规则号，真实建单是 `DEPT-<维度>`。
# 规则号会原样显示在工单页上，带 DEMO 字样一眼就是"假数据"，所以改成后者；
# 清理靠标题匹配（这几条标题只有本脚本会写），旧标记也一并认。
LEGACY_MARK = "DEPT-DEMO-"
# (维度, 部门名, 用户名, 优先级, 推进到哪一步, 标题, 摘要)
CASES = [
    ("宿舍住宿", "后勤保障处", "houqin", "P1", "assigned",
     "宿舍热水高峰时段无水，住宿生连续三晚反映",
     "近一周「宿舍住宿」负面集中在热水与供水时段：晚间 21:00-23:00 高峰无水。"
     "归口后勤保障处，建议先核热水机组运行时段与用水高峰的错峰安排。"),
    ("食堂餐饮", "饮食服务中心", "yinshi", "P2", "processing",
     "食堂二楼窗口排队时间过长，午间拥堵集中",
     "「食堂餐饮」负面集中在窗口数量与排队时长，午间 12:00 前后最明显。"
     "归口饮食服务中心，建议先核实高峰期开窗数量与出餐速度。"),
    ("招生报考", "招生就业处（招生办）", "zhaosheng", "P1", "assigned",
     "招生咨询电话长期占线，志愿填报期家长无人对接",
     "「招生报考」负面集中在咨询渠道不畅：电话占线、线上咨询回复慢。"
     "归口招生就业处，建议在志愿填报期增设临时坐席并公示咨询时段。"),
]


def _clean():
    """清掉上一次造的演示单。

    `ticket_events` **删不掉**：那张表挂了 append-only 触发器，DELETE 会被数据库
    直接拒绝（这是有意的设计，演示脚本也不该有后门）。所以这里只删 tickets 与
    ticket_links，事件记录留成孤儿——所有读取都按 ticket_id 关联，孤儿事件
    不会出现在任何页面、任何统计里，只是审计表里多了几条无主的行。
    真要在正式库里彻底干净，就在造数据之前用副本跑，别在演示库里来回造删。
    """
    conn = db.get_conn()
    titles = [c[5] for c in CASES]
    rows = conn.execute(
        "SELECT ticket_id FROM tickets WHERE tenant_id = ? AND (rule_id LIKE ? OR title IN ("
        + ",".join("?" * len(titles)) + "))",
        [config.DEFAULT_TENANT_ID, LEGACY_MARK + "%"] + titles).fetchall()
    for r in rows:
        conn.execute("DELETE FROM ticket_links WHERE ticket_id = ?", (r["ticket_id"],))
        conn.execute("DELETE FROM tickets WHERE ticket_id = ?", (r["ticket_id"],))
    conn.commit()
    print(f"已清理演示工单 {len(rows)} 张（事件记录按 append-only 保留）")


def main(argv=None):
    ap = argparse.ArgumentParser(description="造/清部门演示工单")
    ap.add_argument("--clean", action="store_true", help="只清理，不新建")
    ap.add_argument("--dataset", default="", help="建单口径用的数据集（默认主数据集）")
    args = ap.parse_args(argv)

    db.init_db()
    tenancy.set_context(tenancy.system_context())
    dept_map = {d["name"]: d for d in departments.list_all(scoped=False)}
    dataset = args.dataset or config.DEFAULT_DATASET_ID

    _clean()
    if args.clean:
        return 0

    created = []
    for dim, dept_name, username, priority, stage, title, summary in CASES:
        dept = dept_map.get(dept_name)
        if not dept:
            print(f"! 找不到部门「{dept_name}」，跳过")
            continue
        user = tenancy.get_user(config.DEFAULT_TENANT_ID, username)
        if not user:
            print(f"! 找不到账号 {username}，跳过（先跑 tools/make_dept_accounts.py）")
            continue

        t = tickets.create({
            "title": title, "summary": summary, "priority": priority,
            "dimension": dim,
            "filters": {"dataset_id": dataset, "dimensions": [dim]},
            # **不写 rule_id**：这几张单是人工作出的归口处置，不来自"规则建议"。
            # 硬塞一个 `DEPT-<维度>` 会占用真实规则号，之后页面点「按建议建单」
            # 会被去重挡住（提示"已有同类工单"），验收脚本也会跟着失败。
            "department_id": dept["dept_id"], "department_name": dept["name"],
        })
        # 真实流程：确认有效（定优先级、起算时限）→ 分派。跳步会被 409 挡住，
        # 这里按顺序走，顺带验证了状态机本身。
        tickets.transition(t["ticket_id"], {"action": "triage", "priority": priority})
        tickets.transition(t["ticket_id"], {"action": "assign", "owner_id": user["user_id"]})
        if stage == "processing":
            tickets.transition(t["ticket_id"], {"action": "start"})
        final = tickets.get(t["ticket_id"], _skip_guard=True)
        created.append((final["code"], dept["name"], final["status_label"], final["title"]))

    print("\n已建演示工单：")
    for code, dept, status, title in created:
        print(f"  {code}  {dept:<14} {status:<6} {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
