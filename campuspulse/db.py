# -*- coding: utf-8 -*-
"""SQLite 访问层。

单文件数据库足够承载数千条评论，读多写少；统一在这里建表和取连接，
让「驾驶舱 / 洞察 / Agent / 证据检索」共用同一份存储，避免多处各读各的内存态。

P2 起多了一层：`get_conn()` 返回 `TenantConn` 包装器，检查每条 SQL 是否
在触碰租户业务表时漏掉了 `tenant_id`。这是"越权可见"这类事故唯一的
自动化防线——代码评审看不出来的漏写，这里能数出来。
"""
import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime

from . import config

logger = logging.getLogger("campuspulse")
_local = threading.local()


class TenantLeakError(RuntimeError):
    """开发模式下发现疑似跨租户全表查询。生产模式不会抛，只记录。"""


# 受租户隔离保护的表。碰它们却不带 tenant_id，等于把两所学校的数据混在一起。
PROTECTED_TABLES = ("datasets", "comments", "reports", "agent_runs", "guard_events",
                    "tickets", "ticket_events", "ticket_links")

# 这些语句天然不需要租户条件：DDL、事务控制、PRAGMA
_SKIP_PREFIX = ("create ", "alter ", "drop ", "pragma ", "begin", "commit",
                "rollback", "vacuum", "analyze", "savepoint", "release")

_leak_lock = threading.Lock()
_leak = {"total": 0, "tables": {}, "samples": []}


def _sensitive_table(sql):
    """返回这条 SQL 触碰到的第一张受保护表名；不需要隔离时返回 None。"""
    low = sql.lower()
    if "tenant_id" in low:
        return None
    if low.lstrip().startswith(_SKIP_PREFIX):
        return None
    # 查 sqlite_master / sqlite_schema 是"看有哪些表"，不是"读某租户的数据"。
    # 不排除的话，任何形如 name='tickets' 的结构探测都会被误判成漏写，
    # 排障脚本第一步就卡住——误报比漏报更糟，因为它会训练人忽略这条告警。
    if "sqlite_master" in low or "sqlite_schema" in low:
        return None
    for t in PROTECTED_TABLES:
        # 用前后边界避免 idx_comments_ds 这类索引名被误判
        if re.search(r"(?<![a-z0-9_])" + t + r"(?![a-z0-9_])", low):
            return t
    return None


def _record_leak(table, sql):
    with _leak_lock:
        _leak["total"] += 1
        _leak["tables"][table] = _leak["tables"].get(table, 0) + 1
        if len(_leak["samples"]) < 20:
            _leak["samples"].append({"table": table, "sql": " ".join(sql.split())[:160]})


def leak_stats():
    """给 /api/health 与 verify_isolation 用：还有多少 SQL 没带 tenant_id。"""
    with _leak_lock:
        return {"total": _leak["total"],
                "tables": dict(_leak["tables"]),
                "samples": list(_leak["samples"]),
                "enforce": bool(config.CP_TENANT_ENFORCE)}


def reset_leak_stats():
    with _leak_lock:
        _leak["total"] = 0
        _leak["tables"].clear()
        _leak["samples"].clear()


class TenantConn:
    """sqlite3.Connection 的薄包装，只多做一件事：检查租户条件。

    刻意不用 `Connection` 子类：sqlite3 的连接是 C 对象，子类化后
    `backup()` 等内部方法会绕过我们的检查，反而造成"以为在管、其实没管"。
    """

    __slots__ = ("_raw",)

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        if config.CP_AUTH:  # 不启用多租户时不做任何额外判断，保证 v1.0 行为逐字节一致
            table = _sensitive_table(sql)
            if table:
                _record_leak(table, sql)
                if config.CP_TENANT_ENFORCE:
                    raise TenantLeakError(f"SQL 未带 tenant_id（表 {table}）：{' '.join(sql.split())[:120]}")
                if config.CP_DEV:
                    logger.warning("tenant_leak_suspected: table=%s sql=%s", table,
                                   " ".join(sql.split())[:180])
        return self._raw.execute(sql, params)

    def executemany(self, sql, seq):
        if config.CP_AUTH:
            table = _sensitive_table(sql)
            if table:
                _record_leak(table, sql)
                if config.CP_TENANT_ENFORCE:
                    raise TenantLeakError(f"SQL 未带 tenant_id（表 {table}）：{' '.join(sql.split())[:120]}")
        return self._raw.executemany(sql, seq)

    def executescript(self, script):
        # 建表脚本天然不含 tenant_id，不必检查
        return self._raw.executescript(script)

    # 其余方法（commit/rollback/backup/close/row_factory 等）全部原样透传
    def __getattr__(self, name):
        return getattr(self._raw, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._raw.__exit__(*exc)


SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id     TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    source_label   TEXT,
    note           TEXT,
    status         TEXT NOT NULL DEFAULT 'ready',
    is_demo        INTEGER NOT NULL DEFAULT 1,
    data_version   TEXT,
    created_at     TEXT,
    analyzed_at    TEXT,
    raw_count      INTEGER DEFAULT 0,
    valid_count    INTEGER DEFAULT 0,
    time_start     TEXT,
    time_end       TEXT,
    platforms      TEXT,
    quality        TEXT
);

CREATE TABLE IF NOT EXISTS comments (
    comment_ref     TEXT PRIMARY KEY,
    dataset_id      TEXT NOT NULL,
    platform        TEXT,
    content         TEXT,
    content_masked  TEXT,
    comment_time    TEXT,
    comment_date    TEXT,
    comment_month   TEXT,
    comment_hour    INTEGER,
    comment_weekday INTEGER,
    like_count      INTEGER,
    dimension       TEXT,
    dimensions      TEXT,
    sentiment       TEXT,
    negative_reason TEXT,
    positive_point  TEXT,
    cleaning_status TEXT,
    filter_reason   TEXT,
    content_len     INTEGER,
    source_file     TEXT,
    source_record   TEXT
);

CREATE INDEX IF NOT EXISTS idx_comments_ds_status
    ON comments (dataset_id, cleaning_status);
CREATE INDEX IF NOT EXISTS idx_comments_ds_date
    ON comments (dataset_id, comment_date);
CREATE INDEX IF NOT EXISTS idx_comments_ds_platform
    ON comments (dataset_id, platform);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id      TEXT PRIMARY KEY,
    dataset_id  TEXT,
    question    TEXT,
    engine      TEXT,
    tool_steps  TEXT,
    ok          INTEGER,
    error       TEXT,
    duration_ms INTEGER,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    report_id   TEXT PRIMARY KEY,
    dataset_id  TEXT,
    engine      TEXT,
    filters     TEXT,
    payload     TEXT,
    created_at  TEXT
);

-- 输入侧拦截审计：只记类别与规则编号，不存完整原文
-- （原文本身可能就是攻击载荷，原样入库等于给日志投毒）
CREATE TABLE IF NOT EXISTS guard_events (
    event_id     TEXT PRIMARY KEY,
    dataset_id   TEXT,
    category     TEXT,
    rule_id      TEXT,
    question     TEXT,
    question_len INTEGER,
    action       TEXT,
    created_at   TEXT
);
"""


def get_conn():
    """返回带租户检查的包装连接；真实连接仍按线程缓存。"""
    raw = getattr(_local, "conn", None)
    if raw is None:
        raw = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute("PRAGMA synchronous=NORMAL")
        raw.execute("PRAGMA busy_timeout=4000")
        _local.conn = raw
    return TenantConn(raw)


def raw_conn():
    """绕过租户检查的原始连接，**只允许迁移与备份使用**。

    留这个口子不是为了方便，是为了让"哪里绕过了检查"可以被 grep 出来：
    全项目只有 migrations.py 和本文件的备份逻辑会用到它。
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        get_conn()
        conn = _local.conn
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate()


def _migrate():
    """在建完基础表后补齐 P2 结构。

    放在 init_db 内部而不是入口文件里，是为了让所有入口（run.py、测试脚本、
    命令行工具）都不会漏掉迁移——漏一次就会在别处报"no such column"。
    """
    from . import migrations
    try:
        migrations.upgrade(verbose=config.CP_DEV)
    except Exception:
        logger.exception("数据库迁移失败，已回滚到迁移前状态")
        raise


def close_conn():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def row_to_dict(row):
    return dict(row) if row is not None else None


def tid():
    """当前请求的租户 ID。

    延迟导入 tenancy：tenancy 顶层 import 了 db，反过来顶层 import 会成环。
    任何异常都退回默认租户——`CP_AUTH=0` 时上下文为空，这里必须能用；
    退回默认租户不会造成越权，因为默认租户就是 v1.0 那个唯一租户。
    """
    try:
        from . import tenancy
        return tenancy.tenant_id()
    except Exception:
        return config.DEFAULT_TENANT_ID


def query_all(sql, params=()):
    return [dict(r) for r in get_conn().execute(sql, params).fetchall()]


def query_one(sql, params=()):
    row = get_conn().execute(sql, params).fetchone()
    return dict(row) if row else None


def scalar(sql, params=()):
    row = get_conn().execute(sql, params).fetchone()
    return row[0] if row else None


# ---------------- 数据集 ----------------
# 隔离约定：所有函数都显式带 tenant_id，不依赖调用方记得传。
# dataset_id 仍是全局主键（上传时带时间戳+随机后缀，天然不撞），
# 因此"同 ID 不同租户"这种状态在库里不可能出现——隔离靠 tenant_id 列，
# 而不是靠 ID 前缀，避免两套命名规则互相打架。
def upsert_dataset(meta):
    conn = get_conn()
    tid_ = meta.get("tenant_id") or tid()
    if not tid_:
        # 没有租户就写库，等于把数据扔进没人能读到的黑洞（或者更糟：被别的租户读到）。
        # 与其静默写坏，不如在这里就炸掉，让调用方补上上下文。
        raise ValueError("upsert_dataset 需要租户上下文：当前 tenant_id 为空")
    meta = dict(meta, tenant_id=tid_)
    conn.execute(
        """INSERT INTO datasets (dataset_id, name, source_label, note, status, is_demo,
                                 data_version, created_at, analyzed_at, raw_count, valid_count,
                                 time_start, time_end, platforms, quality, tenant_id)
           VALUES (:dataset_id, :name, :source_label, :note, :status, :is_demo,
                   :data_version, :created_at, :analyzed_at, :raw_count, :valid_count,
                   :time_start, :time_end, :platforms, :quality, :tenant_id)
           ON CONFLICT(dataset_id) DO UPDATE SET
               name=excluded.name, source_label=excluded.source_label, note=excluded.note,
               status=excluded.status, data_version=excluded.data_version,
               analyzed_at=excluded.analyzed_at, raw_count=excluded.raw_count,
               valid_count=excluded.valid_count, time_start=excluded.time_start,
               time_end=excluded.time_end, platforms=excluded.platforms,
               quality=excluded.quality""",
        # 注意 tenant_id 不在 UPDATE 列表里：归属（谁的数据）不是可覆盖的字段。
        # 允许它被覆盖，一次上下文错乱就能把数据集整批改到别的租户名下，且没有痕迹。
        meta,
    )
    conn.commit()


def list_datasets(tenant=None):
    rows = query_all(
        "SELECT * FROM datasets WHERE tenant_id = ? ORDER BY is_demo DESC, dataset_id ASC",
        (tenant or tid(),),
    )
    for r in rows:
        r["platforms"] = json.loads(r["platforms"]) if r["platforms"] else {}
        r["quality"] = json.loads(r["quality"]) if r["quality"] else {}
    return rows


def get_dataset(dataset_id, tenant=None):
    row = query_one(
        "SELECT * FROM datasets WHERE dataset_id = ? AND tenant_id = ?",
        (dataset_id, tenant or tid()),
    )
    if not row:
        return None
    row["platforms"] = json.loads(row["platforms"]) if row["platforms"] else {}
    row["quality"] = json.loads(row["quality"]) if row["quality"] else {}
    return row


def get_dataset_any_tenant(dataset_id):
    """跨租户查找，**只给迁移/自愈/运维脚本用**。

    正常业务路径一律用 get_dataset()：它查不到别的租户的数据集，
    这是"越权可见"最外层的那道门。
    """
    row = query_one("SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,))
    if not row:
        return None
    row["platforms"] = json.loads(row["platforms"]) if row["platforms"] else {}
    row["quality"] = json.loads(row["quality"]) if row["quality"] else {}
    return row


def cleanup_dataset_rows(dataset_id, tenant=None):
    conn = get_conn()
    conn.execute("DELETE FROM comments WHERE dataset_id = ? AND tenant_id = ?",
                 (dataset_id, tenant or tid()))
    conn.commit()


def delete_dataset(dataset_id, tenant=None):
    conn = get_conn()
    t = tenant or tid()
    conn.execute("DELETE FROM comments WHERE dataset_id = ? AND tenant_id = ?", (dataset_id, t))
    conn.execute("DELETE FROM datasets WHERE dataset_id = ? AND tenant_id = ?", (dataset_id, t))
    conn.commit()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def insert_comments(rows, tenant=None):
    """批量写评论。tenant_id 由本层统一填，不让调用方传——
    少一个可以忘的参数，就少一类串租户事故。"""
    if not rows:
        return 0
    t = tenant or tid()
    if not t:
        raise ValueError("insert_comments 需要租户上下文：当前 tenant_id 为空")
    cols = ["comment_ref", "dataset_id", "platform", "content", "content_masked",
            "comment_time", "comment_date", "comment_month", "comment_hour",
            "comment_weekday", "like_count", "dimension", "dimensions", "sentiment",
            "negative_reason", "positive_point", "cleaning_status", "filter_reason",
            "content_len", "source_file", "source_record"]
    sql = (f"INSERT OR REPLACE INTO comments ({','.join(cols)}, tenant_id) "
           f"VALUES ({','.join('?' * len(cols))}, ?)")
    conn = get_conn()
    conn.executemany(sql, [tuple(r.get(c) for c in cols) + (r.get("tenant_id") or t,)
                           for r in rows])
    conn.commit()
    return len(rows)
