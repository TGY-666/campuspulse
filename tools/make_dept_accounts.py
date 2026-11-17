# -*- coding: utf-8 -*-
"""建三个部门账号：后勤保障处 / 饮食服务中心 / 招生就业处。

这三个账号能做的事**只有一件**：看归口到本部门的研判结论，处置归口到本部门的工单。
- 导航里只有「风险研判与处置中心 → 处置工单」；
- 工单列表、指标、顶栏角标全部按 department_id 过滤；
- 别部门的工单打不开（404，不是 403——403 等于告诉他"这个编号存在"）；
- 不能建单、不能分派、不能复核归档：那三步是学校层面的判断。

账号是**幂等**的：已存在就跳过（加 --reset 才重置密码并改绑部门），
所以换机器重跑不会建出第二套账号。

用法：
    python tools/make_dept_accounts.py                 # 建三个账号（密码 12345678）
    python tools/make_dept_accounts.py --password 自定义
    python tools/make_dept_accounts.py --reset         # 已存在的重置密码并按名字改绑部门
    python tools/make_dept_accounts.py --list          # 只看现有部门与账号
"""
import argparse
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from campuspulse import config, db, tenancy  # noqa: E402

# (用户名, 登录名对应的部门名, 展示名)
# 部门名按库里现存的写法匹配，不做模糊匹配——匹配错部门比建不出账号严重得多。
TARGETS = [
    ("houqin", "后勤保障处", "后勤保障处"),
    ("yinshi", "饮食服务中心", "饮食服务中心"),
    ("zhaosheng", "招生就业处（招生办）", "招生就业处"),
]

OUT_FILE = os.path.join(config.DATA_DIR, "dept_accounts.txt")


def _departments(tenant):
    return db.query_all(
        "SELECT dept_id, name, status FROM departments WHERE tenant_id = ? "
        "ORDER BY sort_order ASC, name ASC", (tenant,))


def _find_dept(tenant, name):
    rows = _departments(tenant)
    row = next((r for r in rows if r["name"] == name), None)
    if row:
        return row
    # 只放宽到"包含"这一种：招生就业处（招生办）这种带括号后缀的名字，
    # 库里可能被改成「招生就业处」，用包含关系就能对上，又不会串到别的部门。
    return next((r for r in rows if name in r["name"] or r["name"] in name), None)


def main(argv=None):
    ap = argparse.ArgumentParser(description="创建部门账号（只办本部门工单）")
    ap.add_argument("--tenant", default=config.DEFAULT_TENANT_ID)
    ap.add_argument("--password", default="12345678",
                    help="统一初始密码（默认 12345678，与管理员演示密码一致）")
    ap.add_argument("--reset", action="store_true",
                    help="已存在的账号：重置密码并按部门名改绑")
    ap.add_argument("--list", action="store_true", help="只列部门与现有账号")
    args = ap.parse_args(argv)

    db.init_db()
    tenant = args.tenant

    if args.list:
        print(f"租户 {tenant} 的部门：")
        for r in _departments(tenant):
            print(f"  [{r['dept_id']}] {r['name']}  {r['status']}")
        print("\n现有账号：")
        for u in db.query_all(
                "SELECT username, display_name, role, status, department_id FROM users "
                "WHERE tenant_id = ? ORDER BY role, username", (tenant,)):
            print(f"  {u['username']:<12} {config.ROLE_LABELS.get(u['role'], u['role']):<6}"
                  f" {u['status']:<7} {u['department_id'] or ''}")
        return 0

    results, missing = [], []
    for username, dept_name, display in TARGETS:
        dept = _find_dept(tenant, dept_name)
        if not dept:
            missing.append(dept_name)
            continue
        if dept["status"] != "active":
            missing.append(f"{dept_name}（已归档）")
            continue

        existing = tenancy.get_user(tenant, username)
        if existing:
            if existing["role"] != "dept":
                print(f"! {username} 已存在但角色是 {existing['role']}，跳过（不强行改角色）")
                continue
            if args.reset:
                tenancy.set_password(existing["user_id"], args.password,
                                     clear_must_change=True)
                tenancy.set_department(existing["user_id"], dept["dept_id"])
                results.append((username, dept["name"], args.password, "已重置并改绑"))
            else:
                results.append((username, dept["name"], "(未改动)", "已存在"))
            continue

        created = tenancy.create_user(
            tenant, username, role="dept", display_name=display,
            password=args.password, must_change=0, department_id=dept["dept_id"])
        results.append((username, dept["name"], created["password"], "已创建"))

    if missing:
        print("以下部门不存在或已归档，请先在页面上建好再跑：")
        for m in missing:
            print(f"  - {m}")
        print("现有部门：" + "、".join(r["name"] for r in _departments(tenant)))
        return 1

    lines = ["CampusPulse 部门账号", "=" * 40,
             f"租户：{tenant}", "",
             "这些账号登录后只有「风险研判与处置中心 → 处置工单」，",
             "且只能看到、只能办理归口到本部门的工单。", ""]
    print("\n部门账号：")
    for username, dept_name, pwd, action in results:
        print(f"  {username:<12} {dept_name:<14} {pwd:<12} {action}")
        lines.append(f"{dept_name}：用户名 {username}  密码 {pwd}")
    lines += ["", "改密码：页面右上角账户菜单 → 修改密码。", ""]

    try:
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\n已写入：{os.path.relpath(OUT_FILE, BASE)}")
    except OSError as e:
        print(f"账号文件写入失败（账号已建好，只是没落盘）：{e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
