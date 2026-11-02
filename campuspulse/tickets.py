# -*- coding: utf-8 -*-
"""M3 处置工单：状态机、工作小时 SLA、规则建议建单。

三件事必须说清楚，否则这些代码半年后没人敢改：

**一、这是内部流转记录，不是"处理学生"。**
   工单只描述"某个议题需要谁在什么时限内回应"，不记录任何个人身份，
   也不对言论真伪做认定。页面上的免责声明不是装饰，是这条边界的公示。

**二、状态机逐行实现文档 3.4 的迁移表。**
   不允许跳步（draft→closed 返回 409 且状态不变），状态只由 `_transition`
   一处改写。所有状态变更都必须留一条 `ticket_events`，否则"可查的记录"
   就退化成"当前状态"——那就没有做的意义了。

**三、SLA 只累计工作时间（周一至周五 09:00-12:00、14:00-18:00）。**
   按自然小时算会出现"周六下午就超期，而这两天没人上班"的假超期，
   指标一旦不可信，整个工单系统的说服力就没了。节假日默认空，不猜。

**append-only**：`ticket_events` 在数据库层挂触发器，UPDATE/DELETE 一律
被 SQLite 拒绝。本模块因此**不提供**任何修改历史事件的函数——不是"没写"，
是"写不了"。
"""
import json
import re
import secrets
from datetime import date, datetime, time as dtime, timedelta

from . import analytics, config, db, tenancy

TS_FMT = "%Y-%m-%d %H:%M:%S"

# 工单状态。reopened 不是终态：它可以再回到 processing。
OPEN_STATUSES = ("draft", "triaged", "assigned", "processing", "pending_review", "reopened")
CLOSED_STATUSES = ("closed", "rejected")
ALL_STATUSES = OPEN_STATUSES + CLOSED_STATUSES

STATUS_LABELS = {
    "draft": "待确认", "triaged": "待分派", "assigned": "已分派",
    "processing": "处理中", "pending_review": "待复核", "reopened": "已退回",
    "closed": "已归档", "rejected": "已驳回",
}

# 迁移表：逐行对应文档 3.4。(动作, 起始状态, 目标状态, 允许角色, 额外要求)
TRANSITIONS = [
    ("triage",   "draft",          "triaged",        ("analyst", "admin"), ()),
    ("reject",   "draft",          "rejected",       ("analyst", "admin"), ("note",)),
    ("assign",   "triaged",        "assigned",       ("admin",),           ("owner",)),
    ("reject",   "triaged",        "rejected",       ("admin",),           ("note",)),
    ("start",    "assigned",       "processing",     ("handler", "admin"), ()),
    ("return",   "assigned",       "triaged",        ("admin",),           ("note",)),
    ("respond",  "processing",     "pending_review", ("handler", "admin"), ("resolution",)),
    ("approve",  "pending_review", "closed",         ("admin",),           ("note",)),
    ("reject_review", "pending_review", "reopened",  ("admin",),           ("note",)),
    ("reopen",   "closed",         "reopened",       ("admin",),           ("note",)),
    ("restart",  "reopened",       "processing",     ("handler", "admin"), ()),
]

ACTION_LABELS = {
    "triage": "确认有效", "reject": "驳回", "assign": "分派", "start": "开始处理",
    "return": "退回重分", "respond": "提交复核", "approve": "复核通过",
    "reject_review": "复核不通过", "reopen": "重新打开", "restart": "重新处理",
}

_RESOLUTION_MIN = 10          # 处理记录下限，写在文档 3.4 里
_TITLE_MAX = 120
_SUMMARY_MAX = 2000
_NOTE_MAX = 1000

DISCLAIMER = ("本工单为校内处置记录，不代表对相关言论真实性的认定；"
              "系统不对学生个人做任何标记或评分。")


# ============================ 异常 ============================
class TicketError(ValueError):
    """带 HTTP 语义的业务异常。web 层统一翻译成错误信封。"""

    def __init__(self, message, status=400, code="bad_request", extra=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.extra = extra or {}


class NotFound(TicketError):
    def __init__(self, message="工单不存在"):
        super().__init__(message, 404, "not_found")


class Forbidden(TicketError):
    def __init__(self, message="当前角色没有该操作权限"):
        super().__init__(message, 403, "forbidden")


class Conflict(TicketError):
    def __init__(self, message, extra=None):
        super().__init__(message, 409, "conflict", extra)


# ============================ 工作时间 / SLA ============================
def _parse(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.strptime(str(value)[:19], TS_FMT)
    except ValueError:
        return None


def _fmt(dt):
    return dt.strftime(TS_FMT) if dt else None


def _segments(day):
    """某一天的工作时段列表；非工作日返回空列表。"""
    if day.weekday() >= 5 or day.isoformat() in config.HOLIDAYS:
        return []
    return [(datetime.combine(day, dtime(a)), datetime.combine(day, dtime(b)))
            for a, b in config.WORK_HOURS]


def _next_start(dt):
    """严格晚于 dt 的第一个工作时刻。"""
    day = dt.date()
    for _ in range(400):
        for s, _e in _segments(day):
            if s > dt:
                return s
        day += timedelta(days=1)
    return dt          # 400 天内没有工作时段 = 配置写错了，原样返回而不是死循环


def add_work_hours(start, hours):
    """从 start 起累计 hours 个工作小时后的时刻（分钟精度）。

    用例（写进文档，答辩会当场验）：
        周五 17:00 + 7h  → 下周一 17:00（跳过周末）
        周一 11:00 + 4h  → 同日 17:00（跳过午休，不是 15:00）
    """
    if start is None:
        return None
    cur = start.replace(second=0, microsecond=0)
    remaining = float(hours) * 60.0
    for _ in range(4000):
        if remaining <= 1e-6:
            break
        seg = next(((s, e) for s, e in _segments(cur.date()) if e > cur), None)
        if seg is None:
            cur = _next_start(cur)
            continue
        s, e = seg
        if cur < s:
            cur = s
        take = min((e - cur).total_seconds() / 60.0, remaining)
        cur += timedelta(minutes=take)
        remaining -= take
        if remaining <= 1e-6:
            break
        cur = _next_start(cur)
    return cur.replace(second=0, microsecond=0)


def work_minutes_between(start, end):
    """两个时刻之间的工作分钟数（用于平均处置时长等指标）。"""
    a, b = _parse(start), _parse(end)
    if not a or not b or b <= a:
        return 0.0
    total, cur = 0.0, a.replace(second=0, microsecond=0)
    for _ in range(4000):
        if cur >= b:
            break
        seg = next(((s, e) for s, e in _segments(cur.date()) if e > cur), None)
        if seg is None:
            cur = _next_start(cur)
            if cur >= b:
                break
            continue
        s, e = seg
        if cur < s:
            cur = s
        edge = min(e, b)
        if edge > cur:
            total += (edge - cur).total_seconds() / 60.0
        cur = edge
        if cur < e:
            break                       # 已到 end
        cur = _next_start(cur)
    return total


def sla_due_for(priority, start):
    hours = config.SLA_ARCHIVE_HOURS.get(priority)
    return add_work_hours(_parse(start) or datetime.now(), hours) if hours else None


def sla_state(ticket, now=None):
    """剩余/超期文案。**不实时重算 sla_due**，只拿库里那个值与现在比。"""
    now = now or datetime.now()
    due = _parse(ticket.get("sla_due"))
    if not due:
        return {"due": None, "remaining_minutes": None, "overdue": False, "text": "未起算"}
    if ticket.get("status") in ("closed", "rejected"):
        ended = _parse(ticket.get("closed_at")) or now
        return {"due": _fmt(due), "remaining_minutes": 0, "overdue": ended > due,
                "text": "已归档" if ended <= due else "超出时限归档"}
    if now > due:
        mins = work_minutes_between(due, now)
        return {"due": _fmt(due), "remaining_minutes": -round(mins), "overdue": True,
                "text": "已超期" + _human_minutes(mins)}
    mins = work_minutes_between(now, due)
    return {"due": _fmt(due), "remaining_minutes": round(mins), "overdue": False,
            "text": "剩余 " + _human_minutes(mins)}


def _human_minutes(mins):
    mins = max(0, int(round(mins)))
    if mins < 60:
        return f"{mins} 分钟"
    days, rest = divmod(mins, config.WORKDAY_HOURS * 60)
    hours, minutes = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} 个工作日")
    if hours:
        parts.append(f"{hours} 小时")
    if minutes and not days:
        parts.append(f"{minutes} 分")
    return " ".join(parts) or "0 分钟"


# ============================ 权限 ============================
def _ctx():
    return tenancy.current()


def _role():
    return _ctx().get("role") or ""


def _uid():
    return _ctx().get("user_id") or ""


def can_create():
    return tenancy.has_perm(_role(), "ticket_create") or tenancy.has_perm(_role(), "ticket_manage")


def can_handle():
    return tenancy.has_perm(_role(), "ticket_handle")


def can_manage():
    return tenancy.has_perm(_role(), "ticket_manage")


def can_view_any():
    """能看全部工单的角色（admin/analyst）。处置员只能看自己名下。"""
    return can_manage() or tenancy.has_perm(_role(), "ticket_create")


def ensure_can_view():
    if not (can_view_any() or can_handle()):
        raise Forbidden("当前角色无权查看处置工单")


def _is_owner(ticket):
    return bool(ticket.get("owner_id")) and ticket["owner_id"] == _uid()


# ============================ 编号 ============================
_CODE_RE = re.compile(r"^TK-(\d{8})-(\d{4})$")
_code_bump = {}          # (租户, 前缀) -> 本进程已发到哪个号，防并发撞号


def next_code(when=None):
    """TK-YYYYMMDD-0001，按租户、按天自增。

    序号取自库里最大值 +1，再用进程内计数器兜底：INSERT 撞 UNIQUE 时
    重新取号会拿到同一个数（那行根本没写进去），所以必须有这层内存计数。
    """
    when = when or datetime.now()
    prefix = f"TK-{when:%Y%m%d}-"
    row = db.query_one(
        "SELECT code FROM tickets WHERE tenant_id = ? AND code LIKE ? "
        "ORDER BY code DESC LIMIT 1", (db.tid(), prefix + "%"))
    n = 1
    if row and _CODE_RE.match(row["code"] or ""):
        n = int(row["code"].rsplit("-", 1)[1]) + 1
    key = (db.tid(), prefix)
    n = max(n, _code_bump.get(key, 0) + 1)
    _code_bump[key] = n
    return f"{prefix}{n:04d}"


# ============================ 口径 ============================
FILTER_KEYS = ("dataset_id", "platforms", "dimensions", "sentiments", "date_from", "date_to",
               "keyword")


def filters_to_kwargs(f):
    """把建单时存下的口径还原成 analytics.build_filters 的入参。

    建单必须存"完整的口径"，不是"当时的数字"：数字会随数据版本变，
    口径不会。存口径才能回答"这条工单当时到底在说哪一批数据"。
    """
    f = f or {}
    return {
        "dataset_id": f.get("dataset_id"),
        "platform": f.get("platforms") or f.get("platform"),
        "dimension": f.get("dimensions") or f.get("dimension"),
        "sentiment": f.get("sentiments") or f.get("sentiment"),
        "date_from": f.get("date_from"),
        "date_to": f.get("date_to"),
        "keyword": f.get("keyword"),
    }


def normalize_filters(raw):
    """入参 → 规范化口径（顺带校验，非法维度/平台在这里就被挡掉）。"""
    if not raw:
        f = analytics.build_filters()
    else:
        f = analytics.build_filters(**filters_to_kwargs(raw))
    return {k: f[k] for k in FILTER_KEYS}


def replay_filters(stored):
    """用存下的口径重新算一次统计，返回 (filters, overview)。"""
    f = analytics.build_filters(**filters_to_kwargs(stored))
    return f, analytics.stats_overview(f)


def pick_evidence(filters, limit=None, only_negative=True):
    """按口径取代表证据：负面优先、点赞高的在前。**只取脱敏文本**。"""
    limit = limit or config.TICKET_MAX_EVIDENCE
    g = dict(filters)
    if only_negative:
        g["sentiments"] = ["negative"]
    where, params = analytics._where(g)
    rows = db.query_all(
        f"""SELECT c.comment_ref, c.content_masked, c.comment_date, c.platform,
                   c.dimension, c.like_count, c.sentiment
            FROM comments c WHERE {where}
            ORDER BY (c.like_count IS NULL), c.like_count DESC, c.comment_date DESC
            LIMIT ?""", params + [limit])
    return [{"comment_ref": r["comment_ref"],
             "snippet": (r["content_masked"] or r["comment_ref"])[:120],
             "date": r["comment_date"], "platform": r["platform"],
             "dimension": r["dimension"], "like_count": r["like_count"],
             "sentiment": r["sentiment"]} for r in rows]


# ============================ 建单 ============================
def create(body):
    if not can_create():
        raise Forbidden("当前角色无权建单（需要分析员或管理员）")
    # 租户校验放在最前面：原来排在"取证据"之后，空上下文会先撞上
    # "数据集不存在"（因为按 tenant_id='' 查不到数据集），报出来的原因
    # 和真实原因完全不是一回事，排查时会往数据迁移方向白跑很久。
    tenant = db.tid()
    if not tenant:
        raise Forbidden("没有租户上下文，无法建单")
    title = str(body.get("title") or "").strip()
    if not title:
        raise TicketError("请填写工单标题", 400, "invalid_title")
    if len(title) > _TITLE_MAX:
        raise TicketError(f"标题不能超过 {_TITLE_MAX} 字", 400, "invalid_title")
    summary = str(body.get("summary") or "").strip()[:_SUMMARY_MAX]
    priority = str(body.get("priority") or "P2").upper()
    if priority not in config.TICKET_PRIORITIES:
        raise TicketError(f"优先级只能是 {'/'.join(config.TICKET_PRIORITIES)}", 400, "invalid_priority")

    try:
        filters = normalize_filters(body.get("filters"))
    except analytics.FilterError as e:
        raise TicketError(str(e), 400, "invalid_filter")

    # 同一条规则的同一条建议，不该被建成两张单。
    # from_suggestion 里虽然有 dup 检查，但那是"评估时刻"的快照——
    # 手抖双击、或两个人同时点建单，两个请求都会在各自的评估里看到"还没有重复"，
    # 然后各建一张。把闸门放到落库前，窗口缩到一次 INSERT 的时间。
    rule_id = str(body.get("rule_id") or "").strip()[:32]
    if rule_id:
        dup = db.query_all(
            "SELECT code, status FROM tickets WHERE tenant_id = ? AND rule_id = ? "
            "AND status NOT IN ('closed', 'rejected') LIMIT 1", (tenant, rule_id))
        if dup:
            raise Conflict(f"已有同类工单 {dup[0]['code']}，请直接处理那一单",
                           {"dup": {"code": dup[0]["code"], "status": dup[0]["status"]}})

    dimension = str(body.get("dimension") or "").strip()
    if not dimension and filters.get("dimensions"):
        dimension = filters["dimensions"][0]
    if dimension and dimension not in config.DIMENSIONS:
        raise TicketError(f"未知维度「{dimension}」", 400, "invalid_dimension")

    # 从单条证据发起时，把它的正文第一句也塞进摘要（人来点，人来改）
    comment_ref = str(body.get("comment_ref") or "").strip()[:80]
    evidence = []
    if comment_ref:
        row = analytics.get_comment(filters["dataset_id"], comment_ref)
        if not row:
            raise TicketError("该条证据不在当前数据集内", 400, "invalid_evidence")
        # 同一条证据不反复建单。前端按钮虽然会 disable，但按钮管不住这些：
        # 换入口建单、网络超时后的重试、两个人各点一次。
        dup = db.query_all(
            "SELECT t.code, t.status FROM ticket_links l "
            "JOIN tickets t ON t.ticket_id = l.ticket_id AND t.tenant_id = l.tenant_id "
            "WHERE l.tenant_id = ? AND l.link_type = 'comment' AND l.link_ref = ? "
            "AND t.status NOT IN ('closed', 'rejected') LIMIT 1",
            (tenant, comment_ref))
        if dup:
            raise Conflict(f"这条证据已有工单 {dup[0]['code']}，请直接处理那一单",
                           {"dup": {"code": dup[0]["code"], "status": dup[0]["status"]}})
        evidence = [{"comment_ref": row["comment_ref"],
                     "snippet": (row.get("content_masked") or "")[:120],
                     "date": row.get("comment_date"), "platform": row.get("platform"),
                     "dimension": row.get("dimension"), "like_count": row.get("like_count"),
                     "sentiment": row.get("sentiment")}]
        if not dimension and row.get("dimension"):
            dimension = row["dimension"]
    else:
        evidence = pick_evidence(filters)

    now = datetime.now()

    ticket_id = "tk_" + secrets.token_hex(8)
    conn = db.get_conn()
    code = None
    for _ in range(5):
        code = next_code(now)
        try:
            conn.execute(
                """INSERT INTO tickets (ticket_id, tenant_id, code, title, summary, priority,
                                        status, dimension, dataset_id, filters, reporter_id,
                                        owner_id, rule_id, sla_due, first_response_at,
                                        closed_at, resolution, review_note, reopen_count,
                                        created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ticket_id, tenant, code, title, summary, priority, "draft", dimension or "",
                 filters["dataset_id"], json.dumps(filters, ensure_ascii=False), _uid(),
                 None, rule_id or None, None, None, None,
                 None, None, 0, _fmt(now), _fmt(now)))
            conn.commit()
            break
        except Exception:
            conn.rollback()
            _bump_code_seed(conn)
    else:
        raise TicketError("工单编号生成失败，请重试", 500, "code_conflict")

    _link(ticket_id, "dataset", filters["dataset_id"])
    for ev in evidence:
        _link(ticket_id, "comment", ev["comment_ref"])
    _event(ticket_id, "create", None, "draft",
           note=summary[:200] or "建单",
           created_at=_fmt(now))
    return get(ticket_id, _skip_guard=True)


def _link(ticket_id, link_type, link_ref):
    if not link_ref:
        return
    conn = db.get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO ticket_links (tenant_id, ticket_id, link_type, link_ref) "
        "VALUES (?,?,?,?)", (db.tid(), ticket_id, link_type, link_ref))
    conn.commit()


def _links(ticket_id):
    rows = db.query_all(
        "SELECT link_type, link_ref FROM ticket_links WHERE tenant_id = ? AND ticket_id = ?",
        (db.tid(), ticket_id))
    comments = [r["link_ref"] for r in rows if r["link_type"] == "comment"]
    datasets = [r["link_ref"] for r in rows if r["link_type"] == "dataset"]
    return comments, datasets


def _event(ticket_id, type_, from_status, to_status, note="", actor_id=None, created_at=None,
           commit=True):
    """写一条时间线事件。

    commit=False 是给「状态变更 + 事件」同事务用的：两者必须在同一个事务里提交，
    否则 UPDATE 成功、INSERT 失败时会留下「状态变了但没有事件」的审计断链，
    而 append-only 的表没有任何办法事后补一条。
    """
    conn = db.get_conn()
    conn.execute(
        """INSERT INTO ticket_events (event_id, tenant_id, ticket_id, type, from_status,
                                      to_status, actor_id, note, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("ev_" + secrets.token_hex(8), db.tid(), ticket_id, type_, from_status, to_status,
         actor_id or _uid() or "system", (note or "")[:_NOTE_MAX],
         created_at or _fmt(datetime.now())))
    if commit:
        conn.commit()


# ============================ 读取 ============================
# 列表不带 filters：一页 20 条，每条都拿口径去跑一次统计，
# 等于把"打开列表"变成 20 次全量聚合。详情才需要口径复现。
LIST_COLS = ("t.ticket_id, t.code, t.title, t.summary, t.priority, t.status, t.dimension, "
             "t.dataset_id, t.reporter_id, t.owner_id, t.rule_id, t.sla_due, "
             "t.first_response_at, t.closed_at, t.reopen_count, t.created_at, t.updated_at")
SELECT_COLS = LIST_COLS + ", t.filters, t.resolution, t.review_note"


def _row_to_ticket(row, detail=False):
    if not row:
        return None
    t = dict(row)
    t["status_label"] = STATUS_LABELS.get(t["status"], t["status"])
    t["priority_label"] = config.SLA_LABELS.get(t["priority"], t["priority"])
    t["sla"] = sla_state(t)
    t["disclaimer"] = DISCLAIMER
    t["first_response_hours"] = config.SLA_FIRST_HOURS.get(t["priority"])
    if not detail:
        return t
    raw = t.get("filters")
    try:
        t["filters"] = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        t["filters"] = {}
    try:
        f = analytics.build_filters(**filters_to_kwargs(t["filters"]))
        t["filters_text"] = analytics.filters_text(f)
        overview = analytics.stats_overview(f)
        t["filter_preview"] = {"total": overview["total"], "negative": overview["negative"],
                               "negative_rate": overview["negative_rate"]}
    except Exception:
        t["filters_text"] = "口径不可用（数据集可能已被删除）"
        t["filter_preview"] = None
    return t


def _fetch(ticket_id):
    return db.query_one(
        f"SELECT {SELECT_COLS} FROM tickets t WHERE t.tenant_id = ? AND t.ticket_id = ?",
        (db.tid(), ticket_id))


def get(ticket_id, _skip_guard=False):
    ensure_can_view()
    t = _fetch(ticket_id)
    if not t:
        raise NotFound()
    if _role() == "handler" and not _is_owner(t) and not _skip_guard:
        # 处置员看别人的工单一律 404：403 会告诉他"这个编号存在"
        raise NotFound()
    data = _row_to_ticket(t, detail=True)
    comments, datasets = _links(ticket_id)
    data["evidence"] = _evidence_items(t["dataset_id"], comments)
    data["links"] = {"comments": comments, "datasets": datasets}
    data["events"] = list_events(ticket_id)
    data["actions"] = available_actions(data)
    return data


def _evidence_items(dataset_id, refs):
    if not refs:
        return []
    out = []
    for ref in refs[:config.TICKET_MAX_EVIDENCE]:
        row = analytics.get_comment(dataset_id, ref) if dataset_id else None
        if row:
            out.append({"comment_ref": row["comment_ref"],
                        "snippet": (row.get("content_masked") or "")[:160],
                        "date": row.get("comment_date"), "platform": row.get("platform"),
                        "dimension": row.get("dimension"), "like_count": row.get("like_count"),
                        "sentiment": row.get("sentiment")})
        else:
            out.append({"comment_ref": ref, "snippet": "（证据原文已不在当前数据集内）"})
    return out


def list_events(ticket_id):
    rows = db.query_all(
        "SELECT event_id, type, from_status, to_status, actor_id, note, created_at "
        "FROM ticket_events WHERE tenant_id = ? AND ticket_id = ? ORDER BY created_at ASC",
        (db.tid(), ticket_id))
    names = _actor_names([r["actor_id"] for r in rows])
    for r in rows:
        r["type_label"] = ACTION_LABELS.get(r["type"], r["type"])
        if r["type"] == "create":
            r["type_label"] = "建单"
        elif r["type"] == "comment":
            r["type_label"] = "评论"
        r["actor_name"] = names.get(r["actor_id"], r["actor_id"])
    return rows


def _actor_names(ids):
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    rows = db.query_all(
        f"SELECT user_id, display_name, username FROM users WHERE tenant_id = ? "
        f"AND user_id IN ({','.join('?' * len(ids))})", [db.tid()] + ids)
    return {r["user_id"]: (r["display_name"] or r["username"]) for r in rows}


def available_actions(ticket):
    """当前身份在当前状态下可做的动作——前端只渲染这些按钮，不猜。"""
    actor = {"role": _role(), "user_id": _uid()}
    out = []
    for action, src, dst, roles, requires in TRANSITIONS:
        if ticket["status"] != src or actor["role"] not in roles:
            continue
        if actor["role"] == "handler" and not (
                ticket.get("owner_id") == actor["user_id"]):
            continue
        out.append({"action": action, "label": ACTION_LABELS[action], "to": dst,
                    "to_label": STATUS_LABELS[dst],
                    "requires": list(requires),
                    "primary": action in ("triage", "assign", "start", "respond", "approve")})
    return out


def search(params):
    ensure_can_view()
    clauses = ["t.tenant_id = ?"]
    args = [db.tid()]
    status = [s for s in (params.get("status") or []) if s in ALL_STATUSES]
    if params.get("status_group") == "open":
        clauses.append(f"t.status IN ({','.join('?' * len(OPEN_STATUSES))})")
        args += list(OPEN_STATUSES)
    elif params.get("status_group") == "overdue":
        clauses.append("t.sla_due IS NOT NULL AND t.sla_due < ?")
        clauses.append(f"t.status IN ({','.join('?' * len(OPEN_STATUSES))})")
        args.append(_fmt(datetime.now()))
        args += list(OPEN_STATUSES)
    elif status:
        clauses.append(f"t.status IN ({','.join('?' * len(status))})")
        args += status
    if params.get("priority"):
        pri = [p for p in params["priority"] if p in config.TICKET_PRIORITIES]
        if pri:
            clauses.append(f"t.priority IN ({','.join('?' * len(pri))})")
            args += pri
    if params.get("dimension"):
        clauses.append("t.dimension = ?")
        args.append(params["dimension"])
    if params.get("mine"):
        clauses.append("t.owner_id = ?")
        args.append(_uid())
    if _role() == "handler":
        # 处置员即使不勾"我的"，也只能看到自己名下的
        clauses.append("t.owner_id = ?")
        args.append(_uid())
    q = str(params.get("q") or "").strip()
    if q:
        clauses.append("(t.title LIKE ? OR t.summary LIKE ? OR t.code LIKE ?)")
        args += [f"%{q}%"] * 3

    where = " AND ".join(clauses)
    total = db.scalar(f"SELECT COUNT(*) FROM tickets t WHERE {where}", args) or 0
    limit = max(1, min(int(params.get("limit") or config.TICKET_PAGE_SIZE), 100))
    offset = max(0, int(params.get("offset") or 0))
    rows = db.query_all(
        f"SELECT {LIST_COLS} FROM tickets t WHERE {where} "
        f"ORDER BY CASE t.status WHEN 'closed' THEN 1 WHEN 'rejected' THEN 2 ELSE 0 END, "
        f"t.priority ASC, COALESCE(t.sla_due, t.created_at) ASC LIMIT ? OFFSET ?",
        args + [limit, offset])
    owners = _actor_names([r["owner_id"] for r in rows] + [r["reporter_id"] for r in rows])
    items = []
    for r in rows:
        t = _row_to_ticket(r, detail=False)
        t["owner_name"] = owners.get(r["owner_id"]) if r["owner_id"] else None
        t["reporter_name"] = owners.get(r["reporter_id"]) if r["reporter_id"] else None
        items.append(t)
    return {"tickets": items, "total": total, "limit": limit, "offset": offset,
            "statuses": [{"key": s, "label": STATUS_LABELS[s]} for s in ALL_STATUSES],
            "priorities": [{"key": p, "label": config.SLA_LABELS[p]} for p in config.TICKET_PRIORITIES],
            "disclaimer": DISCLAIMER, "now": _fmt(datetime.now())}


# ============================ 状态迁移 ============================
def transition(ticket_id, body):
    ensure_can_view()
    action = str(body.get("action") or "").strip()
    note = str(body.get("note") or "").strip()[:_NOTE_MAX]
    owner_id = str(body.get("owner_id") or "").strip()

    t = _fetch(ticket_id)
    if not t:
        raise NotFound()

    rule = next((r for r in TRANSITIONS if r[0] == action and r[1] == t["status"]), None)
    if not rule:
        # 先判"这一步合法吗"，再判"你有权吗"：跳步返回 409，角色不够返回 403，
        # 两者混在一起会让前端只能弹"操作失败"，说不清下一步该找谁。
        raise Conflict(f"不能从「{STATUS_LABELS[t['status']]}」执行该操作",
                       {"current_status": t["status"],
                        "current_status_label": STATUS_LABELS[t["status"]],
                        "allowed": [q[0] for q in TRANSITIONS if q[1] == t["status"]]})
    _, _src, dst, roles, requires = rule
    if _role() not in roles:
        raise Forbidden(f"该操作仅限{'/'.join(config.ROLE_LABELS[r] for r in roles)}")
    # 到这里说明"动作 + 当前状态"是允许的，只是这个人能不能做：
    # 处置员动别人名下的单一律 404（403 等于告诉他"这个编号存在"）。
    if _role() == "handler" and not _is_owner(t):
        raise NotFound()

    if "note" in requires and not note:
        raise TicketError("请填写说明（驳回原因 / 复核意见 / 退回原因）", 400, "note_required")
    if "resolution" in requires and len(note) < _RESOLUTION_MIN:
        raise TicketError(f"处理记录至少 {_RESOLUTION_MIN} 字，请写清做了什么", 400,
                          "resolution_too_short")

    now = datetime.now()
    fields, args = {"status": dst, "updated_at": _fmt(now)}, []
    extra_note = ""
    if action == "triage":
        priority = str(body.get("priority") or t["priority"]).upper()
        if priority not in config.TICKET_PRIORITIES:
            raise TicketError("优先级不合法", 400, "invalid_priority")
        dimension = str(body.get("dimension") or t["dimension"] or "").strip()
        if dimension and dimension not in config.DIMENSIONS:
            raise TicketError(f"未知维度「{dimension}」", 400, "invalid_dimension")
        due = sla_due_for(priority, now)
        fields.update({"priority": priority, "sla_due": _fmt(due), "dimension": dimension})
        extra_note = (f"优先级 {priority}（{config.SLA_LABELS[priority]}），"
                      f"归档时限 {_fmt(due)}")
    elif action == "assign":
        target = _resolve_owner(owner_id)
        fields["owner_id"] = target["user_id"]
        budget = config.SLA_FIRST_HOURS.get(t["priority"], 4)
        extra_note = (f"已派给 {target['display_name'] or target['username']}，"
                      f"要求 {budget} 个工作小时内首次响应")
    elif action == "return":
        fields["owner_id"] = None
    elif action in ("start", "restart"):
        if not t["first_response_at"]:
            fields["first_response_at"] = _fmt(now)
    elif action == "respond":
        fields["resolution"] = note
    elif action == "approve":
        fields["closed_at"] = _fmt(now)
        fields["review_note"] = note
    elif action in ("reject_review", "reopen"):
        if action == "reopen":
            closed = _parse(t["closed_at"])
            if closed and (now - closed) > timedelta(days=config.TICKET_REOPEN_DAYS):
                raise Conflict(f"归档超过 {config.TICKET_REOPEN_DAYS} 天的工单不再重开，请新建")
        fields["reopen_count"] = int(t["reopen_count"] or 0) + 1
        fields["closed_at"] = None
    elif action == "reject":
        fields["review_note"] = note

    sets = ", ".join(f"{k} = ?" for k in fields)
    conn = db.get_conn()
    # CAS：WHERE 带上「读到的那个状态」，让"读—校验—写"这一整段不能被并发穿透。
    # 原实现只按 ticket_id 更新，实测 10 路并发分派会有 2 路同时成功，
    # 时间线里出现两条一模一样的 assigned —— append-only 审计里重复记账，
    # 等于把"谁在什么时候做了什么"这件事本身弄脏了。
    cur = conn.execute(
        f"UPDATE tickets SET {sets} WHERE tenant_id = ? AND ticket_id = ? AND status = ?",
        list(fields.values()) + [db.tid(), ticket_id, t["status"]])
    if cur.rowcount != 1:
        conn.rollback()
        raise Conflict("这条工单刚被其他人改过，请刷新后再操作",
                       {"ticket_id": ticket_id, "expected_status": t["status"]})

    # 状态与事件同事务提交：不留"状态变了、时间线没记"的审计断链
    _event(ticket_id, action, t["status"], dst, note=note or extra_note, commit=False)
    conn.commit()
    data = get(ticket_id, _skip_guard=True)
    data["message"] = _say_next(action, data, extra_note)
    return data


def _resolve_owner(owner_id):
    if not owner_id:
        raise TicketError("请选择处置人", 400, "owner_required")
    u = tenancy.get_user_by_id(owner_id, db.tid())
    if not u or u.get("status") != "active":
        raise TicketError("处置人不存在或已停用", 400, "invalid_owner")
    if u["role"] not in ("handler", "admin"):
        raise TicketError("只能派给处置员或管理员", 400, "invalid_owner")
    return u


def _say_next(action, ticket, extra):
    """点完必须说清"下一步谁收到"——只弹"操作成功"等于什么都没说。"""
    nxt = {
        "triage": "已确认有效，等待管理员分派处置人",
        "assign": extra or "已分派",
        "start": "已开始处理，处理完请写清结果后提交复核",
        "respond": "已提交复核，等待管理员复核归档",
        "approve": "已归档，处置记录已留痕",
        "reject": "已驳回，本单不再流转",
        "return": "已退回待分派，请重新指派处置人",
        "reject_review": "已退回处理人，请补充后重新提交",
        "reopen": "已重新打开，请重新指派或直接处理",
        "restart": "已重新进入处理中",
    }
    return nxt.get(action, "状态已更新")


def comment(ticket_id, body):
    ensure_can_view()
    note = str(body.get("note") or "").strip()[:_NOTE_MAX]
    if not note:
        raise TicketError("评论内容不能为空", 400, "empty_comment")
    t = _fetch(ticket_id)
    if not t:
        raise NotFound()
    if _role() == "handler" and not _is_owner(t):
        raise NotFound()
    if t["status"] in ("closed", "rejected"):
        raise Conflict("已结束的工单不能再追加评论")
    _event(ticket_id, "comment", t["status"], t["status"], note=note)
    conn = db.get_conn()
    conn.execute("UPDATE tickets SET updated_at = ? WHERE tenant_id = ? AND ticket_id = ?",
                 (_fmt(datetime.now()), db.tid(), ticket_id))
    if _is_owner(t) and not t["first_response_at"]:
        conn.execute("UPDATE tickets SET first_response_at = ? WHERE tenant_id = ? "
                     "AND ticket_id = ?", (_fmt(datetime.now()), db.tid(), ticket_id))
    conn.commit()
    return get(ticket_id, _skip_guard=True)


# ============================ 指标 ============================
def stats():
    ensure_can_view()
    rows = db.query_all(
        "SELECT status, priority, sla_due, closed_at, first_response_at, created_at, "
        "reopen_count FROM tickets WHERE tenant_id = ?", (db.tid(),))
    now = datetime.now()
    by_status = {s: 0 for s in ALL_STATUSES}
    overdue = 0
    open_n = 0
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        if r["status"] in OPEN_STATUSES:
            open_n += 1
            due = _parse(r["sla_due"])
            if due and now > due:
                overdue += 1

    closed = [r for r in rows if r["status"] == "closed"]
    on_time = sum(1 for r in closed
                  if _parse(r["sla_due"]) and _parse(r["closed_at"])
                  and _parse(r["closed_at"]) <= _parse(r["sla_due"]))
    handled = [(work_minutes_between(r["created_at"], r["closed_at"]), r) for r in closed]
    handle_mins = [m for m, _ in handled if m > 0]
    # 验证脚本里建单到归档只隔几秒，工作分钟数算出来是 0；
    # 此时应显示 0.0 而不是"暂无数据"——有已归档工单却显示无数据，会被当成 bug 追问。
    if closed and not handle_mins:
        handle_mins = [0.0]
    first_mins = [work_minutes_between(r["created_at"], r["first_response_at"])
                  for r in rows if r["first_response_at"]]
    first_mins = [m for m in first_mins if m > 0]
    reopened = sum(1 for r in rows if int(r["reopen_count"] or 0) > 0)
    rejected = by_status.get("rejected", 0)
    total = len(rows)

    def avg(xs):
        return round(sum(xs) / len(xs) / 60.0, 1) if xs else None

    return {
        "total": total,
        "open": open_n,
        "overdue": overdue,
        "by_status": [{"key": s, "label": STATUS_LABELS[s], "count": by_status.get(s, 0)}
                      for s in ALL_STATUSES],
        "pending_assign": by_status.get("triaged", 0),
        "pending_review": by_status.get("pending_review", 0),
        # 分子分母都给出（AC-M3-12）：只说"按时率 82%"会被追问"分母是什么"
        "on_time": {"numerator": on_time, "denominator": len(closed),
                    "rate": round(on_time / len(closed) * 100, 1) if closed else None},
        "avg_handle_hours": avg(handle_mins),
        "avg_first_response_hours": avg(first_mins),
        "reopen": {"numerator": reopened, "denominator": total,
                   "rate": round(reopened / total * 100, 1) if total else None},
        "false_positive": {"numerator": rejected, "denominator": total,
                           "rate": round(rejected / total * 100, 1) if total else None},
        "sla_policy": {
            "unit": "工作小时",
            "work_hours": "周一至周五 09:00-12:00、14:00-18:00",
            "archive": config.SLA_ARCHIVE_HOURS,
            "first_response": config.SLA_FIRST_HOURS,
            "holidays": sorted(config.HOLIDAYS),
        },
        "disclaimer": DISCLAIMER,
    }


# ============================ 规则建议（不落库）============================
RULES = [
    {"rule_id": "R1", "name": "负面率突增", "priority": "P0",
     "condition": "近 1 天负面率 > 前 30 天基线 × 2（近 1 天样本 < 5 条时不评估）"},
    {"rule_id": "R2", "name": "单维度负面集中", "priority": "P1",
     "condition": "单维度负面 ≥ 20 条且占该维度 ≥ 25%（窗口 = 当前筛选范围）"},
    {"rule_id": "R3", "name": "归因词突增", "priority": "P1",
     "condition": "某归因词近 7 天提及量 > 前 7 天 × 3（近 7 天 < 3 条时不评估）"},
    {"rule_id": "R4", "name": "高互动负面", "priority": "P2",
     "condition": "单条负面点赞 ≥ 500（窗口 = 当前筛选范围）"},
]


def _anchor(f):
    row = db.query_one(
        f"SELECT MAX(c.comment_date) mx FROM comments c WHERE {analytics._where(f)[0]}",
        analytics._where(f)[1])
    mx = (row or {}).get("mx")
    return date.fromisoformat(mx) if mx else None


def _count(f, d1=None, d2=None, negative=False):
    g = dict(f)
    if d1:
        g["date_from"] = d1
    if d2:
        g["date_to"] = d2
    if negative:
        g["sentiments"] = ["negative"]
    where, params = analytics._where(g)
    return db.scalar(f"SELECT COUNT(*) FROM comments c WHERE {where}", params) or 0


def suggestions(dataset_id=None):
    """评估 4 条规则，**只返回建议，不写 tickets 表**。

    两条设计纪律，都是被真实数据逼出来的：

    1. **不自动建单**。自动建单把"机器定性"的嫌疑引到作品上；人点一下再建，
       责任边界清晰，也更符合"系统给建议、人做决定"的定位。
    2. **样本不足就不评估**，并在 `skipped` 里写清原因。本项目的数据是历史快照，
       近 7 天常常只有个位数评论——此时算出"负面率上涨 300%"毫无意义。
       宁可说"样本不足"，也不要为了显得智能而报警：一次假报警，
       以后所有提示都会被当成噪音。
    """
    if not can_view_any():
        raise Forbidden("当前角色无权查看建单建议")
    try:
        f = analytics.build_filters(dataset_id)
    except analytics.FilterError as e:
        raise TicketError(str(e), 400, "invalid_filter")
    anchor = _anchor(f)
    if not anchor:
        return {"suggestions": [], "rules": RULES, "anchor": None, "skipped": [],
                "note": "当前数据集没有可用日期，无法评估规则", "disclaimer": DISCLAIMER}

    d = lambda n: (anchor - timedelta(days=n)).isoformat()   # noqa: E731
    out, skipped = [], []
    span = {k: f[k] for k in FILTER_KEYS}

    # ---- R1 负面率突增：近 1 天 vs 前 30 天基线 ----
    day_from, day_to = d(1), anchor.isoformat()
    r_all = _count(f, day_from, day_to)
    r_neg = _count(f, day_from, day_to, negative=True)
    b_all = _count(f, d(31), d(1))
    b_neg = _count(f, d(31), d(1), negative=True)
    if r_all < 5:
        skipped.append({"rule_id": "R1", "reason": f"近 1 天仅 {r_all} 条，样本不足以判断突增"})
    elif b_all < 20:
        skipped.append({"rule_id": "R1", "reason": f"基线 30 天仅 {b_all} 条，基线不可用"})
    else:
        rate_r, rate_b = r_neg / r_all, (b_neg / b_all if b_all else 0)
        if rate_b and rate_r > 2 * rate_b:
            out.append({
                "rule_id": "R1", "rule_name": "负面率突增", "priority": "P0", "dimension": "",
                "title": f"{day_to} 负面率 {round(rate_r * 100, 1)}%，为基线 2 倍以上",
                "detail": f"近 1 天负面 {r_neg}/{r_all} = {round(rate_r * 100, 1)}%，"
                          f"前 30 天基线 {b_neg}/{b_all} = {round(rate_b * 100, 1)}%（阈值 > 2 倍）。"
                          "建议核实是否为真实议题集中出现，本系统不判断真伪。",
                "basis": {"近 1 天": f"{r_neg}/{r_all}", "基线 30 天": f"{b_neg}/{b_all}",
                          "倍数": f"{round(rate_r / rate_b, 2)}×"},
                "filters": dict(span),
                "evidence": pick_evidence({**f, "date_from": day_from, "date_to": day_to}, 3),
            })

    # ---- R2 单维度负面集中：窗口 = 当前筛选范围 ----
    where, params = analytics._where(f)
    rows = db.query_all(
        f"""SELECT j.value dim, COUNT(*) n,
                   SUM(CASE WHEN c.sentiment = 'negative' THEN 1 ELSE 0 END) neg
            FROM comments c, json_each(c.dimensions) j
            WHERE {where} GROUP BY j.value ORDER BY neg DESC""", params)
    hit = next((r for r in rows
                if r["neg"] >= 20 and r["n"] and r["neg"] / r["n"] >= 0.25), None)
    if hit:
        share = round(hit["neg"] / hit["n"] * 100, 1)
        out.append({
            "rule_id": "R2", "rule_name": "单维度负面集中", "priority": "P1",
            "dimension": hit["dim"], "title": f"「{hit['dim']}」负面 {hit['neg']} 条，占该维度 {share}%",
            "detail": f"当前筛选范围内，该维度共 {hit['n']} 条，其中负面 {hit['neg']} 条（{share}%），"
                      "满足「≥ 20 条且占比 ≥ 25%」。属常规集中吐槽，建议按议题处理，"
                      "不必上升到事件定性。",
            "basis": {"负面": hit["neg"], "该维度总数": hit["n"], "占比": f"{share}%"},
            "filters": {**span, "dimensions": [hit["dim"]]},
            "evidence": pick_evidence({**f, "dimensions": [hit["dim"]]}, 3),
        })
    else:
        skipped.append({"rule_id": "R2",
                        "reason": "没有维度同时满足「负面 ≥ 20 条」与「占比 ≥ 25%」"})

    # ---- R3 归因词突增：近 7 天 vs 前 7 天，**口径同长**
    # 早期写成"7 天总量 > 日均 × 3"，那个比较天生成立（7 天总量约为日均的 7 倍），
    # 会天天报警——这类"看起来在算、其实恒真"的规则是最难被发现的错误。
    w_from = d(7)
    w_where, w_params = analytics._where({**f, "date_from": w_from, "date_to": anchor.isoformat()})
    rows = db.query_all(
        f"""SELECT c.negative_reason k, COUNT(*) n FROM comments c
            WHERE {w_where} AND c.sentiment = 'negative' AND c.negative_reason <> ''
            GROUP BY c.negative_reason ORDER BY n DESC LIMIT 5""", w_params)
    recent_total = _count(f, w_from, anchor.isoformat(), negative=True)
    if recent_total < 3:
        skipped.append({"rule_id": "R3",
                        "reason": f"近 7 天负面仅 {recent_total} 条，样本不足以判断突增"})
    else:
        for r in rows[:3]:
            kw = r["k"]
            if not kw:
                continue
            prev = db.scalar(
                f"SELECT COUNT(*) FROM comments c WHERE {where} "
                f"AND c.sentiment = 'negative' AND c.negative_reason = ? "
                f"AND c.comment_date >= ? AND c.comment_date < ?",
                params + [kw, d(14), w_from]) or 0
            if r["n"] >= 3 and r["n"] > 3 * prev:
                out.append({
                    "rule_id": "R3", "rule_name": "归因词突增", "priority": "P1",
                    "dimension": "",
                    "title": f"归因「{kw}」近 7 天 {r['n']} 条，为前 7 天的 3 倍以上",
                    "detail": f"近 7 天「{kw}」相关负面 {r['n']} 条，前 7 天 {prev} 条"
                              "（阈值 > 3 倍）。关键词集取负面归因词典，口径同长可比、可复算。",
                    "basis": {"近 7 天": r["n"], "前 7 天": prev,
                              "倍数": (round(r["n"] / prev, 1) if prev else "新增")},
                    "filters": dict(span),
                    "evidence": pick_evidence({**f, "date_from": w_from,
                                               "date_to": anchor.isoformat()}, 2),
                })
                break
        else:
            skipped.append({"rule_id": "R3", "reason": "没有归因词达到「3 倍且 ≥ 3 条」"})

    # ---- R4 高互动负面：窗口 = 当前筛选范围 ----
    hot = db.query_all(
        f"""SELECT c.comment_ref, c.content_masked, c.comment_date, c.platform,
                   c.dimension, c.like_count
            FROM comments c WHERE {where} AND c.sentiment = 'negative'
              AND c.like_count IS NOT NULL AND c.like_count >= 500
            ORDER BY c.like_count DESC LIMIT 3""", params)
    if hot:
        top = hot[0]
        out.append({
            "rule_id": "R4", "rule_name": "高互动负面", "priority": "P2",
            "dimension": top["dimension"] or "",
            "title": f"高互动负面 {len(hot)} 条（最高点赞 {top['like_count']}）",
            "detail": "单条负面点赞 ≥ 500 视为高互动，传播面更广，建议优先核实其描述是否属实。"
                      "本系统不判断内容真伪，也不对发布者做任何标记。",
            "basis": {"命中条数": len(hot), "最高点赞": top["like_count"], "阈值": "≥ 500"},
            "filters": dict(span),
            "evidence": [{"comment_ref": h["comment_ref"],
                          "snippet": (h["content_masked"] or "")[:120],
                          "date": h["comment_date"], "platform": h["platform"],
                          "dimension": h["dimension"], "like_count": h["like_count"],
                          "sentiment": "negative"} for h in hot],
        })
    else:
        skipped.append({"rule_id": "R4", "reason": "没有点赞 ≥ 500 的负面单条"})

    for s in out:
        s["dup"] = _find_dup(s["rule_id"], s["dimension"])
    return {"suggestions": out, "rules": RULES, "skipped": skipped,
            "anchor": anchor.isoformat(),
            "window": {"recent_1d": [day_from, day_to], "baseline_30d": [d(31), d(1)],
                       "recent_7d": [w_from, anchor.isoformat()]},
            "disclaimer": "以上为规则阈值的建单建议，不写入任何工单；"
                          "是否建单、如何定级由使用者决定。",
            "note": (f"命中 {len(out)} 条建议，另有 {len(skipped)} 条规则因样本不足未评估"
                     if out else "当前筛选范围内没有命中规则（原因见 skipped）")}


def _find_dup(rule_id, dimension):
    since = _fmt(datetime.now() - timedelta(hours=config.TICKET_DUP_WINDOW_HOURS))
    row = db.query_one(
        "SELECT ticket_id, code, status, dimension FROM tickets "
        "WHERE tenant_id = ? AND rule_id = ? AND created_at >= ? "
        "AND status NOT IN ('closed', 'rejected') "
        "AND (dimension = ? OR dimension IS NULL OR dimension = '') "
        "ORDER BY created_at DESC LIMIT 1", (db.tid(), rule_id, since, dimension or ""))
    return {"ticket_id": row["ticket_id"], "code": row["code"],
            "status": row["status"]} if row else None


def from_suggestion(body):
    """按建议一键建单：口径与证据从**服务端重新评估的结果**取，不信前端传的。"""
    if not can_create():
        raise Forbidden("当前角色无权建单")
    rule_id = str(body.get("rule_id") or "").strip().upper()
    if not rule_id:
        raise TicketError("缺少 rule_id", 400, "bad_request")
    evaluated = suggestions(body.get("dataset_id"))
    target = next((s for s in evaluated["suggestions"] if s["rule_id"] == rule_id
                   and (not body.get("dimension") or s["dimension"] == body["dimension"])), None)
    if not target:
        raise Conflict("该建议已不再命中（数据可能已变化），请刷新后重试",
                       {"rule_id": rule_id})
    if target["dup"]:
        raise Conflict(f"已有同类工单 {target['dup']['code']}，请直接处理那一单",
                       {"dup": target["dup"]})
    return create({
        "title": str(body.get("title") or target["title"])[: _TITLE_MAX],
        "summary": str(body.get("summary") or target["detail"])[:_SUMMARY_MAX],
        "priority": str(body.get("priority") or target["priority"]),
        "dimension": target["dimension"],
        "filters": target["filters"],
        "rule_id": rule_id,
    })


def assignable_users():
    rows = db.query_all(
        "SELECT user_id, username, display_name, role FROM users "
        "WHERE tenant_id = ? AND status = 'active' AND role IN ('handler', 'admin') "
        "ORDER BY role DESC, username ASC", (db.tid(),))
    return [{"user_id": r["user_id"], "name": r["display_name"] or r["username"],
             "role": r["role"], "role_label": config.ROLE_LABELS.get(r["role"], r["role"])}
            for r in rows]


def dashboard_badge():
    """/api/tickets/badge：顶栏待办角标 + 驾驶舱一格。"""
    if not can_view_any() and not can_handle():
        return {"enabled": True, "can_view": False, "mine": 0, "pending": 0, "overdue": 0}
    ensure_can_view()
    now = _fmt(datetime.now())
    if _role() == "handler":
        mine = db.scalar(
            "SELECT COUNT(*) FROM tickets WHERE tenant_id = ? AND owner_id = ? AND status IN "
            "('assigned','processing','reopened')", (db.tid(), _uid())) or 0
        overdue = db.scalar(
            "SELECT COUNT(*) FROM tickets WHERE tenant_id = ? AND owner_id = ? AND sla_due < ? "
            "AND status IN ('assigned','processing','pending_review','reopened')",
            (db.tid(), _uid(), now)) or 0
    else:
        mine = db.scalar(
            "SELECT COUNT(*) FROM tickets WHERE tenant_id = ? AND status IN "
            "('draft','triaged','pending_review')", (db.tid(),)) or 0
        overdue = db.scalar(
            "SELECT COUNT(*) FROM tickets WHERE tenant_id = ? AND sla_due < ? "
            "AND status IN ('assigned','processing','pending_review','reopened')",
            (db.tid(), now)) or 0
    pending = db.scalar(
        "SELECT COUNT(*) FROM tickets WHERE tenant_id = ? AND status IN "
        "('draft','triaged','assigned','processing','pending_review','reopened')",
        (db.tid(),)) or 0
    return {"enabled": True, "can_view": True, "mine": mine, "pending": pending,
            "overdue": overdue, "label": f"待处置 {pending}", "overdue_label": f"超期 {overdue}"}


def trend(days=14):
    """近 N 天建单/归档数——驾驶舱一格用，口径简单（按创建时间/归档时间落在哪一天）。"""
    ensure_can_view()
    start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    created = db.query_all(
        "SELECT substr(created_at, 1, 10) d, COUNT(*) n FROM tickets "
        "WHERE tenant_id = ? AND created_at >= ? GROUP BY d", (db.tid(), start + " 00:00:00"))
    closed = db.query_all(
        "SELECT substr(closed_at, 1, 10) d, COUNT(*) n FROM tickets "
        "WHERE tenant_id = ? AND closed_at IS NOT NULL AND closed_at >= ? GROUP BY d",
        (db.tid(), start + " 00:00:00"))
    cmap = {r["d"]: r["n"] for r in created}
    kmap = {r["d"]: r["n"] for r in closed}
    out = []
    for i in range(days):
        d = (datetime.now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        out.append({"date": d, "created": cmap.get(d, 0), "closed": kmap.get(d, 0)})
    return {"trend": out, "disclaimer": DISCLAIMER}
