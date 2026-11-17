# -*- coding: utf-8 -*-
"""清理开发测试残留：重复报告 / 测试用户 / 测试库副本。

用法：
    python tools/clean_devdata.py          # 预演，只打印不删
    python tools/clean_devdata.py --apply  # 真正执行
"""
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "data", "campuspulse.db")
APPLY = "--apply" in sys.argv

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

# ---------- 1. 报告：同数据集 + 同筛选口径，只保留最新 1 份 ----------
rows = c.execute(
    "SELECT report_id, dataset_id, filters, created_at FROM reports "
    "ORDER BY created_at DESC").fetchall()
seen = {}
drop = []
for r in rows:
    key = (r["dataset_id"], r["filters"] or "")
    if key in seen:
        drop.append(r["report_id"])
    else:
        seen[key] = r["report_id"]

print(f"[报告] 共 {len(rows)} 份，不同口径 {len(seen)} 组，待删重复 {len(drop)} 份")
for rid in drop[:5]:
    print("    -", rid)

# ---------- 2. 测试用户 smoke1 ----------
users = c.execute("SELECT user_id, username FROM users WHERE username = 'smoke1'").fetchall()
print(f"[用户] 待删测试账号 {len(users)} 个：" + ", ".join(u["username"] for u in users))

# ---------- 3. 测试库副本 ----------
junk = os.path.join(BASE, "data", "_test_vision.db")
print(f"[文件] {'存在' if os.path.exists(junk) else '不存在'}：{junk}")

if not APPLY:
    print("\n预演结束。加 --apply 执行。")
    sys.exit(0)

c.executemany("DELETE FROM reports WHERE report_id = ?", [(r,) for r in drop])
c.executemany("DELETE FROM users WHERE user_id = ?", [(u["user_id"],) for u in users])
c.commit()
c.execute("VACUUM")
c.close()

if os.path.exists(junk):
    os.remove(junk)

print(f"\n已删除报告 {len(drop)} 份、用户 {len(users)} 个、测试库副本 1 个。")
