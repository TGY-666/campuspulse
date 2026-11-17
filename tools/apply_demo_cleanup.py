# -*- coding: utf-8 -*-
"""把「部门」与「数据集名称」的演示口径同步到已有数据库。

为什么需要这个脚本：
    config.PRESET_DATASETS 与 departments.SEED 只在**库为空时**生效
    （bootstrap_presets 遇到已存在的数据集直接跳过，seed_defaults 见到任何部门就返回 0）。
    远程/本机已经在跑的演示库不会因为改代码而变，于是页面还是旧名字、旧部门。
    这个脚本按代码里的新口径把现有库对齐一遍，可重复执行（幂等）。

做什么：
    1. 部门 —— 后勤保障处（宿管中心）+ 后勤保障处（环境与设施）合并成「后勤保障处」，
       饮食服务中心、招生就业处（招生办）保留，其余示例部门置为 archived（软删，不物理删）。
    2. 数据集 —— 名称统一成「年份」，如 2019-2026 / 2023 / 2023（合并）/ 2020-2026。

用法：
    python tools/apply_demo_cleanup.py            # 预演，只打印将要做的改动
    python tools/apply_demo_cleanup.py --apply    # 真正写库（会先自动备份）
"""
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campuspulse import config, db, departments  # noqa: E402


def _write(sql, params=()):
    conn = db.get_conn()
    conn.execute(sql, params)
    conn.commit()

# 旧部门名 -> 合并后的新部门名。表里存的是当时写入的名字快照。
MERGE_INTO = "后勤保障处"
MERGE_FROM = ["后勤保障处（宿管中心）", "后勤保障处（环境与设施）"]
KEEP_NAMES = [item["name"] for item in departments.SEED]      # 后勤保障处 / 饮食服务中心 / 招生就业处（招生办）
# 已归档部门不在 SEED 里，旧全名照样换成称呼式
NAME_MAP = {
    "陈志远": "陈老师", "黄丽萍": "黄老师", "林国华": "林老师", "周敏": "周老师",
    "吴海峰": "吴老师", "郑文彬": "郑老师", "何建强": "何老师", "梁晓雯": "梁老师",
}


def patch_datasets(apply):
    plan = []
    for preset in config.PRESET_DATASETS:
        row = db.query_one("SELECT name FROM datasets WHERE dataset_id = ?", (preset["dataset_id"],))
        if not row:
            continue
        if row["name"] != preset["name"]:
            plan.append((preset["dataset_id"], row["name"], preset["name"]))
    for did, old, new in plan:
        print(f"  数据集 {did}：{old}  ->  {new}")
        if apply:
            _write("UPDATE datasets SET name = ? WHERE dataset_id = ?", (new, did))
    if not plan:
        print("  数据集名称：已是新口径，无需改动")
    return len(plan)


def patch_departments(apply):
    rows = db.query_all("SELECT * FROM departments WHERE status = 'active' ORDER BY sort_order")
    by_name = {r["name"]: r for r in rows}
    n = 0

    # 1) 合并：保留一行，改名改职责；另一行归档
    seed = next(i for i in departments.SEED if i["name"] == MERGE_INTO)
    target = by_name.get(MERGE_INTO) or by_name.get(MERGE_FROM[0]) or by_name.get(MERGE_FROM[1])
    if target:
        print(f"  部门 {target['name']} -> {seed['name']}"
              f"（主题 {'、'.join(seed['dimensions'])}，关键词 {len(seed['keywords'])} 个）")
        if apply:
            _write(
                """UPDATE departments SET name=?, duties=?, owner_name=?, owner_title=?, contact=?,
                          dimensions=?, keywords=?, sla_hours=?, sort_order=?, updated_at=?
                   WHERE dept_id=?""",
                (seed["name"], seed["duties"], seed["owner_name"], seed["owner_title"],
                 seed["contact"],
                 json.dumps(seed["dimensions"], ensure_ascii=False),
                 json.dumps(seed["keywords"], ensure_ascii=False),
                 seed["sla_hours"], seed["sort_order"],
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"), target["dept_id"]))
        n += 1
        kept_id = target["dept_id"]
    else:
        kept_id = None

    for name in MERGE_FROM:
        r = by_name.get(name)
        if r and r["dept_id"] != kept_id:
            print(f"  部门 {name}：归档（已并入 {MERGE_INTO}）")
            if apply:
                _write("UPDATE departments SET status='archived', updated_at=? WHERE dept_id=?",
                           (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), r["dept_id"]))
            n += 1

    # 2) 其余示例部门归档（不动用户自建部门：is_seed=0）
    for r in rows:
        if r["name"] in KEEP_NAMES or r["name"] in MERGE_FROM:
            continue
        if not r["is_seed"]:
            print(f"  部门 {r['name']}：保留（用户自建）")
            continue
        print(f"  部门 {r['name']}：归档")
        if apply:
            _write("UPDATE departments SET status='archived', updated_at=? WHERE dept_id=?",
                       (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), r["dept_id"]))
        n += 1
    return n


def patch_owners(apply):
    """负责人统一成「姓 + 老师」，示例配置里不留像真人的全名。

    在岗部门直接取 SEED 的值；已归档的部门没有 SEED 条目，按旧全名映射过去，
    免得翻归档记录时又冒出「郑文彬」这种名字。
    """
    seed_by_name = {i["name"]: i for i in departments.SEED}
    n = 0
    for r in db.query_all("SELECT dept_id, name, owner_name, owner_title FROM departments"):
        seed = seed_by_name.get(r["name"])
        target = seed["owner_name"] if seed else NAME_MAP.get(r["owner_name"] or "")
        title = seed["owner_title"] if seed else r["owner_title"]
        if target and (target != r["owner_name"] or title != r["owner_title"]):
            print(f"  负责人 {r['name']}：{r['owner_name'] or '（空）'} -> {target}"
                  + (f" · {title}" if title and title != r["owner_title"] else ""))
            if apply:
                _write("UPDATE departments SET owner_name=?, owner_title=?, updated_at=? WHERE dept_id=?",
                       (target, title, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), r["dept_id"]))
            n += 1
    if not n:
        print("  负责人称呼：已是新口径，无需改动")
    return n


def main():
    apply = "--apply" in sys.argv
    print("模式：" + ("执行写入" if apply else "预演（加 --apply 才会真写）"))
    if apply:
        src = config.DB_PATH
        dst = os.path.join(config.BASE_DIR, "data", "backups",
                           f"campuspulse_before_demo_cleanup_{datetime.now():%Y%m%d_%H%M%S}.db")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  已备份到 {dst}")

    print("\n[1] 数据集名称")
    a = patch_datasets(apply)
    print("\n[2] 部门与职责")
    b = patch_departments(apply)
    print("\n[3] 负责人称呼")
    c = patch_owners(apply)
    print(f"\n共 {a + b + c} 项改动" + ("" if apply else "（预演，未写库）"))
    if apply:
        print("提示：正在运行的服务读不到外部写入，请重启后再看页面。")


if __name__ == "__main__":
    main()
