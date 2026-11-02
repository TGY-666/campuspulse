# -*- coding: utf-8 -*-
"""幂等数据库迁移（P2 · S0）。

为什么不用 Alembic，也不建 `schema_version` 表：
- 本项目要保住"单文件可拷走"这个私有化卖点，多一张版本表就多一处要解释的状态；
- 2.5 万行的库，整套升级目标 < 3 秒，"探测现状再补齐"比"按版本号顺序执行"更适合；
- **幂等比有序更重要**。学生项目最常见的事故是分支切来切去导致版本号错乱，
  而"缺什么补什么"永远不会因为漏跑一步而出错。

三条纪律：

1. **先备份，再动手。** 只在真正要变更时备份，避免每次启动都堆一堆文件。
2. **失败即回滚。** 异常时先 rollback 事务；仍失败则用备份覆盖文件复原。
3. **只增不删。** S0 新增租户前缀索引 `(tenant_id, dataset_id, ...)`，
   但**故意不删**旧索引 `(dataset_id, ...)`。

第 3 条容易被当成疏漏，其实是关键：现有业务 SQL 还没带 `tenant_id`，
而 SQLite 无法用 `(tenant_id, dataset_id, x)` 这个索引去服务"只有 dataset_id"的查询
（tenant_id 是前导列）。此时若删掉旧索引，5 个页面会从走索引退化成全表扫描。
等 S1 把所有 SQL 改完，再用 `--prune-legacy` 删。

命令行：
    python -m campuspulse.migrations            # 执行升级（幂等）
    python -m campuspulse.migrations --status   # 只查看现状，不写库
    python -m campuspulse.migrations --dry-run  # 打印将要执行的动作
    python -m campuspulse.migrations --rollback # 回滚到最近备份
"""
import argparse
import glob
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime

from . import config

BACKUP_DIR = os.path.join(config.DATA_DIR, "backups")

# 需要租户隔离的业务表（v1.0 已有的 + M3 会新增的）
TENANT_TABLES = ["datasets", "comments", "reports", "agent_runs", "guard_events"]

NEW_TABLES = {
    "tenants": """
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id  TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            short_name TEXT,
            status     TEXT NOT NULL DEFAULT 'active',
            note       TEXT,
            created_at TEXT NOT NULL
        )""",
    "users": """
        CREATE TABLE IF NOT EXISTS users (
            user_id      TEXT PRIMARY KEY,
            tenant_id    TEXT NOT NULL,
            username     TEXT NOT NULL,
            display_name TEXT,
            role         TEXT NOT NULL,
            pwd_hash     TEXT NOT NULL,
            must_change  INTEGER NOT NULL DEFAULT 0,
            status       TEXT NOT NULL DEFAULT 'active',
            last_login   TEXT,
            created_at   TEXT NOT NULL,
            UNIQUE (tenant_id, username)
        )""",
    "auth_events": """
        CREATE TABLE IF NOT EXISTS auth_events (
            event_id   TEXT PRIMARY KEY,
            tenant_id  TEXT,
            user_id    TEXT,
            action     TEXT NOT NULL,
            ip_hash    TEXT,
            detail     TEXT,
            created_at TEXT NOT NULL
        )""",
    # ---- M3 处置工单 ----
    # ticket_links 也带 tenant_id：虽然是纯关联表，但少了这一列就等于
    # 给隔离检测留了一个盲区（db 层的漏写检测只认带 tenant_id 的表）。
    "tickets": """
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id         TEXT PRIMARY KEY,
            tenant_id         TEXT NOT NULL,
            code              TEXT NOT NULL,
            title             TEXT NOT NULL,
            summary           TEXT,
            priority          TEXT NOT NULL DEFAULT 'P2',
            status            TEXT NOT NULL DEFAULT 'draft',
            dimension         TEXT,
            dataset_id        TEXT,
            filters           TEXT,
            reporter_id       TEXT,
            owner_id          TEXT,
            rule_id           TEXT,
            sla_due           TEXT,
            first_response_at TEXT,
            closed_at         TEXT,
            resolution        TEXT,
            review_note       TEXT,
            reopen_count      INTEGER NOT NULL DEFAULT 0,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            UNIQUE (tenant_id, code)
        )""",
    "ticket_events": """
        CREATE TABLE IF NOT EXISTS ticket_events (
            event_id    TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            ticket_id   TEXT NOT NULL,
            type        TEXT NOT NULL,
            from_status TEXT,
            to_status   TEXT,
            actor_id    TEXT NOT NULL,
            note        TEXT,
            created_at  TEXT NOT NULL
        )""",
    "ticket_links": """
        CREATE TABLE IF NOT EXISTS ticket_links (
            tenant_id TEXT NOT NULL,
            ticket_id TEXT NOT NULL,
            link_type TEXT NOT NULL,
            link_ref  TEXT NOT NULL,
            PRIMARY KEY (ticket_id, link_type, link_ref)
        )""",
}

# append-only 用数据库触发器兜住，而不是靠"我们不写那行代码"。
# 需求文档 AC-M3-07 的原话是"接口层面无入口"——但"没有接口"只是约定，
# 一次手快的 UPDATE 就能改掉处置记录，而工单的全部价值就是这份记录改不了。
# 触发器让它在任何路径下都改不动，测试脚本可以直接验证。
NEW_TRIGGERS = {
    "trg_ticket_events_no_update": """
        CREATE TRIGGER IF NOT EXISTS trg_ticket_events_no_update
        BEFORE UPDATE ON ticket_events
        BEGIN SELECT RAISE(ABORT, 'ticket_events is append-only: UPDATE denied'); END""",
    "trg_ticket_events_no_delete": """
        CREATE TRIGGER IF NOT EXISTS trg_ticket_events_no_delete
        BEFORE DELETE ON ticket_events
        BEGIN SELECT RAISE(ABORT, 'ticket_events is append-only: DELETE denied'); END""",
}

# 新索引：(名称, 目标表, 列)
TENANT_INDEXES = [
    ("idx_datasets_tenant", "datasets", "tenant_id, is_demo, dataset_id"),
    ("idx_comments_tenant_status", "comments", "tenant_id, dataset_id, cleaning_status"),
    ("idx_comments_tenant_date", "comments", "tenant_id, dataset_id, comment_date"),
    ("idx_comments_tenant_platform", "comments", "tenant_id, dataset_id, platform"),
    ("idx_reports_tenant", "reports", "tenant_id, dataset_id, created_at"),
    ("idx_agent_runs_tenant", "agent_runs", "tenant_id, created_at"),
    ("idx_guard_events_tenant", "guard_events", "tenant_id, created_at"),
    ("idx_users_tenant", "users", "tenant_id, username"),
    ("idx_auth_tenant", "auth_events", "tenant_id, created_at"),
    ("idx_tickets_tenant", "tickets", "tenant_id, status, priority, created_at"),
    ("idx_tickets_code", "tickets", "tenant_id, code"),
    ("idx_ticket_events", "ticket_events", "ticket_id, created_at"),
    ("idx_ticket_links", "ticket_links", "tenant_id, ticket_id"),
]

# S1 之后才允许删的旧索引（现在留着，见文件头第 3 条）
LEGACY_INDEXES = ["idx_comments_ds_status", "idx_comments_ds_date", "idx_comments_ds_platform"]


# ============================ 探测 ============================
def _connect(path=None):
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=4000")
    return conn


def _tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r["name"] for r in rows}


def _columns(conn, table):
    try:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.DatabaseError:
        return set()


def _indexes(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return {r["name"] for r in rows}


def _triggers(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
    return {r["name"] for r in rows}


def _has_data(conn):
    """库里已经有实际业务数据吗？决定要不要备份。"""
    for t in ("comments", "datasets"):
        if t in _tables(conn):
            try:
                if conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone():
                    return True
            except sqlite3.DatabaseError:
                pass
    return False


# ============================ 计划 ============================
def plan(conn):
    """返回 (actions, notes)。只探测，不写库。"""
    acts, notes = [], []
    tables = _tables(conn)
    idx = _indexes(conn)

    if not tables:
        notes.append("数据库为空（全新库）：将按 db.SCHEMA 建基础表后补齐租户字段")
        return acts, notes

    for name, ddl in NEW_TABLES.items():
        if name not in tables:
            acts.append(("table", f"新建表 {name}", ddl.strip()))

    for t in TENANT_TABLES:
        if t not in tables:
            continue
        if "tenant_id" not in _columns(conn, t):
            acts.append(("column", f"{t} 增加 tenant_id",
                         f"ALTER TABLE {t} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT '{config.DEFAULT_TENANT_ID}'"))

    for name, tbl, cols in TENANT_INDEXES:
        if tbl not in tables and f"{tbl}" not in NEW_TABLES:
            continue
        if name not in idx:
            acts.append(("index", f"新建索引 {name}",
                         f"CREATE INDEX IF NOT EXISTS {name} ON {tbl} ({cols})"))

    trg = _triggers(conn)
    for name, ddl in NEW_TRIGGERS.items():
        target = "ticket_events"
        if target not in tables and target not in NEW_TABLES:
            continue
        if name not in trg:
            acts.append(("trigger", f"新建触发器 {name}", ddl.strip()))

    if "tenants" in tables:
        try:
            row = conn.execute(
                "SELECT 1 FROM tenants WHERE tenant_id = ?", (config.DEFAULT_TENANT_ID,)
            ).fetchone()
            if not row:
                acts.append(("row", f"插入默认租户 {config.DEFAULT_TENANT_ID}", None))
        except sqlite3.DatabaseError:
            pass

    # 归属为空的记录回填默认租户。
    # 这类脏数据来自"没有租户上下文时写库"（例如启动期导入、后台脚本），
    # 它不会报错、也不会被任何租户读到，只会让数据凭空消失——
    # 所以必须有一个每次都检查的修复动作，而不是靠人工发现。
    for t in TENANT_TABLES:
        if t not in tables or "tenant_id" not in _columns(conn, t):
            continue
        try:
            bad = conn.execute(
                f"SELECT 1 FROM {t} WHERE tenant_id IS NULL OR tenant_id = '' LIMIT 1"
            ).fetchone()
            if bad:
                acts.append(("sql", f"{t} 归属为空的行回填默认租户",
                             f"UPDATE {t} SET tenant_id = '{config.DEFAULT_TENANT_ID}' "
                             f"WHERE tenant_id IS NULL OR tenant_id = ''"))
        except sqlite3.DatabaseError:
            pass

    return acts, notes


# ============================ 备份 ============================
def backup_path():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(BACKUP_DIR, f"campuspulse-{stamp}.db")


def _make_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = backup_path()
    src = config.DB_PATH
    # 用 sqlite 的在线备份 API，而不是 shutil.copy：
    # WAL 模式下直接拷 .db 文件可能丢掉尚未 checkpoint 的 -wal 内容。
    src_conn = sqlite3.connect(src)
    try:
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    return dst


def latest_backup():
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "campuspulse-*.db")))
    return files[-1] if files else None


# ============================ 执行 ============================
def upgrade(dry_run=False, verbose=True, prune_legacy=False):
    """执行升级。幂等：第二次调用应当 applied 为空。"""
    started = time.time()
    result = {"applied": [], "notes": [], "backup": None, "ms": 0, "ok": True}

    if not os.path.isfile(config.DB_PATH):
        result["notes"].append("数据库文件不存在，由 db.init_db() 负责建基础表")
        result["ms"] = int((time.time() - started) * 1000)
        return result

    conn = _connect()
    try:
        acts, notes = plan(conn)
        result["notes"] += notes

        if not acts:
            if verbose:
                print("[migrate] 已是最新结构，无需变更")
            result["ms"] = int((time.time() - started) * 1000)
            return result

        if verbose:
            print(f"[migrate] 待执行 {len(acts)} 项：")
            for kind, desc, _ in acts:
                print(f"  · [{kind}] {desc}")

        if dry_run:
            result["applied"] = [d for _, d, _ in acts]
            result["ms"] = int((time.time() - started) * 1000)
            return result

        # 只有结构性变更（建表/加列/建索引）和改数据（回填归属）才值得备份。
        # 补一行租户数据也备份的话，每次启动都会堆一个几十 MB 的文件。
        need_backup = any(k in ("table", "column", "index", "trigger", "sql")
                          for k, _, _ in acts)
        bp = None
        if need_backup and _has_data(conn):
            bp = _make_backup()
            result["backup"] = bp
            if verbose:
                print(f"[migrate] 已备份 -> {os.path.relpath(bp, config.BASE_DIR)}")

        for kind, desc, sql in acts:
            if kind == "row":
                # kind="row" 且无 SQL：只有"插入默认租户"这一种，写法固定
                conn.execute(
                    "INSERT OR IGNORE INTO tenants (tenant_id, name, short_name, status, note, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (config.DEFAULT_TENANT_ID, "默认学校", "本校", "active",
                     "v1.0 升级时自动创建；改名请用 CLI 或直接改库",
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
            else:
                conn.execute(sql)
            result["applied"].append(desc)
            if verbose:
                print(f"  ✓ {desc}")

        # 兜底：租户行必须在 tenants 表**建好之后**才能插，而 plan() 是在建表前
        # 探测的——那时表还不存在，这一项会被漏掉。所以这里单独补一次（幂等）。
        if "tenants" in _tables(conn):
            existed = conn.execute(
                "SELECT 1 FROM tenants WHERE tenant_id = ?", (config.DEFAULT_TENANT_ID,)
            ).fetchone()
            if not existed:
                conn.execute(
                    "INSERT OR IGNORE INTO tenants (tenant_id, name, short_name, status, note, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (config.DEFAULT_TENANT_ID, "默认学校", "本校", "active",
                     "v1.0 升级时自动创建；改名请直接改库或走 CLI",
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
                result["applied"].append(f"插入默认租户 {config.DEFAULT_TENANT_ID}")
                if verbose:
                    print(f"  ✓ 插入默认租户 {config.DEFAULT_TENANT_ID}")

        # 双保险：万一历史遗留过 tenant_id 为 NULL 的行（比如手工加过列），一并回填
        for t in TENANT_TABLES:
            if t in _tables(conn) and "tenant_id" in _columns(conn, t):
                n = conn.execute(
                    f"UPDATE {t} SET tenant_id = ? WHERE tenant_id IS NULL", (config.DEFAULT_TENANT_ID,)
                ).rowcount
                if n:
                    result["applied"].append(f"{t} 回填 tenant_id（{n} 行）")
                    if verbose:
                        print(f"  ✓ {t} 回填 tenant_id（{n} 行）")

        if prune_legacy:
            idx = _indexes(conn)
            for name in LEGACY_INDEXES:
                if name in idx:
                    conn.execute(f"DROP INDEX {name}")
                    result["applied"].append(f"删除旧索引 {name}")
                    if verbose:
                        print(f"  ✓ 删除旧索引 {name}")

        conn.commit()
        if verbose:
            print(f"[migrate] 完成，用时 {int((time.time() - started) * 1000)}ms")
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        result["ok"] = False
        result["error"] = str(exc)
        if verbose:
            print(f"[migrate] 失败：{exc}")
        raise
    finally:
        conn.close()
        result["ms"] = int((time.time() - started) * 1000)
    return result


def recover(backup=None):
    """用备份覆盖当前库文件。Windows 下必须先确保没有其它连接持有文件。"""
    src = backup or latest_backup()
    if not src or not os.path.isfile(src):
        raise FileNotFoundError("没有可用的备份文件")
    from . import db
    db.close_conn()
    for suffix in ("-wal", "-shm"):
        p = config.DB_PATH + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    shutil.copy2(src, config.DB_PATH)
    return src


def status():
    """给 health_check / 排障用：当前结构是否齐备。"""
    if not os.path.isfile(config.DB_PATH):
        return {"ready": False, "reason": "数据库文件不存在"}
    conn = _connect()
    try:
        acts, _ = plan(conn)
        return {
            "ready": not acts,
            "pending": [d for _, d, _ in acts],
            "tables": sorted(_tables(conn)),
            "tenant_columns": {t: ("tenant_id" in _columns(conn, t))
                               for t in TENANT_TABLES if t in _tables(conn)},
        }
    finally:
        conn.close()


# ============================ CLI ============================
def main(argv=None):
    ap = argparse.ArgumentParser(description="CampusPulse 幂等迁移")
    ap.add_argument("--status", action="store_true", help="只查看现状")
    ap.add_argument("--dry-run", action="store_true", help="打印将执行的动作，不写库")
    ap.add_argument("--rollback", action="store_true", help="回滚到最近备份")
    ap.add_argument("--prune-legacy", action="store_true",
                    help="删除 v1.0 旧索引（**仅在所有 SQL 已带 tenant_id 后使用**）")
    args = ap.parse_args(argv)

    if args.status:
        import json
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if args.rollback:
        src = recover()
        print(f"[migrate] 已回滚：{src} -> {config.DB_PATH}")
        return 0

    res = upgrade(dry_run=args.dry_run, prune_legacy=args.prune_legacy)
    verb = "计划" if args.dry_run else "变更"
    tail = "（未写库）" if args.dry_run else ""
    print(f"[migrate] ok={res['ok']} {verb} {len(res['applied'])} 项，{res['ms']}ms{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
