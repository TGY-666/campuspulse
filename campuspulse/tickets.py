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
    # dept（部门账号）能做的只有"干活"这三步：开始处理、提交复核、被退回后重新处理。
    # 确认有效 / 分派 / 复核归档都是学校层面的判断，留在 admin/analyst 手里——
    # 部门自己既能定性又能归档，"闭环"就成了自己给自己结案。
    ("start",    "assigned",       "processing",     ("handler", "dept", "admin"), ()),
    ("return",   "assigned",       "triaged",        ("admin",),           ("note",)),
    ("respond",  "processing",     "pending_review", ("handler", "dept", "admin"),
     ("resolution",)),
    ("approve",  "pending_review", "closed",         ("admin",),           ("note",)),
    ("reject_review", "pending_review", "reopened",  ("admin",),           ("note",)),
    ("reopen",   "closed",         "reopened",       ("admin",),           ("note",)),
    ("restart",  "reopened",       "processing",     ("handler", "dept", "admin"), ()),
]

ACTION_LABELS = {
    "triage": "确认有效", "reject": "驳回", "assign": "分派", "start": "开始处理",
    "return": "退回重分", "respond": "提交复核", "approve": "复核通过",
    "reject_review": "复核不通过", "reopen": "重新打开", "restart": "重新处理",
    # delete 不是状态迁移，但审计事件类型要有人话名字，否则时间线上显示原始英文
    "delete": "删除工单",
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
    """剩余/超期文案。**不实时重算 sla_due**，只拿库里那个值与现在比。

    text 给人读（"已超期 8.4 个工作日"，单一单位），label/amount 给列表右侧那窄窄一格：
    一整句话塞进 84px 会在"已超期 8 个 / 工作日 4 小时"中间断开，读起来像两个数。
    """
    now = now or datetime.now()
    due = _parse(ticket.get("sla_due"))
    if not due:
        return {"due": None, "remaining_minutes": None, "overdue": False,
                "text": "未起算", "label": "", "amount": ""}
    if ticket.get("status") in ("closed", "rejected"):
        ended = _parse(ticket.get("closed_at")) or now
        ok = ended <= due
        return {"due": _fmt(due), "remaining_minutes": 0, "overdue": not ok,
                "text": "已归档" if ok else "超出时限归档",
                "label": "归档", "amount": "按时" if ok else "超时"}
    if now > due:
        mins = work_minutes_between(due, now)
        return {"due": _fmt(due), "remaining_minutes": -round(mins), "overdue": True,
                # 空格不是排版洁癖：「已超期9 个工作日」会被读成一个数粘了个后缀
                "text": "已超期 " + _human_minutes(mins),
                "label": "超期", "amount": _human_minutes(mins)}
    mins = work_minutes_between(now, due)
    return {"due": _fmt(due), "remaining_minutes": round(mins), "overdue": False,
            "text": "剩余 " + _human_minutes(mins),
            "label": "剩余", "amount": _human_minutes(mins)}


def _human_minutes(mins):
    """工作分钟 → 人话。**单位是工作小时**，所以「小时」前面必须带「工作」：
    裸写「剩余 4 小时」会让周五下午的人以为今晚截止，而它其实是下周一。

    第 19 轮：**一个量只给一个单位**。原来是「1 个工作日 6 工作小时」——
    两个单位粘在一起，读者要么把 6 当成另一件事，要么在 86px 的列表格里
    被切成两行当成两个数。现在按量级选一档，永不并存：
        不足 1 工作小时 → "34 分钟"
        不足 1 工作日   → "6.4 工作小时"
        1 个工作日及以上 → "1.9 个工作日"
    精度取 1 位小数：SLA 场景够用，也不会让人误读成自然日/自然周。
    """
    mins = max(0, int(round(mins)))
    if mins < 60:
        return f"{mins} 分钟"
    per_day = config.WORKDAY_HOURS * 60
    if mins < per_day:
        return f"{_trim1(mins / 60)} 工作小时"
    return f"{_trim1(mins / per_day)} 个工作日"


def _trim1(v):
    """1 位小数，去掉无意义的 ".0"（7.0 → 7），保持中文里数字的干净写法。"""
    return (f"{v:.1f}").rstrip("0").rstrip(".")


# ============================ 权限 ============================
def _ctx():
    return tenancy.current()


def _role():
    return _ctx().get("role") or ""


def _uid():
    return _ctx().get("user_id") or ""


def can_create():
    # 先看模块开关，再看角色。以前只看角色，于是演示态（CP_TICKETS=0）页面上
    # 照样亮着 8 颗「建单」按钮，点下去 POST 404——等于承诺了一个不存在的动作。
    # 能不能建单是两件事的乘积：这个部署开放了工单，且这个角色有建单权。
    if not config.CP_TICKETS:
        return False
    return tenancy.has_perm(_role(), "ticket_create") or tenancy.has_perm(_role(), "ticket_manage")


def can_handle():
    return tenancy.has_perm(_role(), "ticket_handle")


def can_manage():
    return tenancy.has_perm(_role(), "ticket_manage")


def can_view_any():
    """能看全部工单的角色（admin/analyst）。处置员只能看自己名下。"""
    return can_manage() or tenancy.has_perm(_role(), "ticket_create")


def dept_scope():
    """部门账号的归口部门 id。

    返回 **None 与 "" 是两件不同的事**，这个区分是本模块所有范围判断的基础：
      None —— 不是部门账号，不设范围（admin/analyst 看全部，handler 按 owner 过滤）
      ""   —— 是部门账号但没绑定部门，**什么都看不到**（而不是"看到全部"）
    把后者当"不限"会在账号配置漏填时把全公司的工单摊给一个新账号，
    而且页面上看起来一切正常。
    """
    ctx = _ctx()
    if (ctx.get("role") or "") != "dept":
        return None
    return str(ctx.get("department_id") or "")


def _scope_where(clauses, args, alias="t"):
    """把部门范围追加进 where 片段。

    没绑定部门时直接加一条恒假条件：调用方不必为"范围为空"写分支，
    列表、计数、导出都自动是空的——漏掉一处就会把全量摊出去。

    alias 要跟着查询走：列表查询是 `tickets t`，单表统计是 `tickets`，
    写死成 `t.department_id` 会让后者直接报 "no such column: t"。
    """
    dept = dept_scope()
    if dept is None:
        return True
    if not dept:
        clauses.append("0 = 1")
        return False
    clauses.append(f"{alias + '.' if alias else ''}department_id = ?")
    args.append(dept)
    return True


def _in_scope(ticket):
    dept = dept_scope()
    if dept is None:
        return True
    return bool(dept) and ticket.get("department_id") == dept


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
    """入参 → 规范化口径（顺带校验，非法主题/平台在这里就被挡掉）。"""
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
    """按口径取代表证据：负面优先、点赞高的在前。**只取脱敏文本**。

    多取几倍再去重：同一条评论可能被两个平台各采到一次（comment_ref 不同、
    正文一字不差）。「代表证据」里出现两段一模一样的字，读者第一反应是
    "去重没做"，而不是相信这条证据——所以宁可少给，也不重复。
    """
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
            LIMIT ?""", params + [limit * 6])
    out, seen = [], set()
    for r in rows:
        snippet = (r["content_masked"] or "").strip()
        key = "".join(snippet.split())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"comment_ref": r["comment_ref"],
                    "snippet": snippet[:120],
                    "date": r["comment_date"], "platform": r["platform"],
                    "dimension": r["dimension"], "like_count": r["like_count"],
                    "sentiment": r["sentiment"]})
        if len(out) >= limit:
            break
    return out


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
        raise TicketError(f"未知主题「{dimension}」", 400, "invalid_dimension")

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

    # 归口部门：存 id + 名称快照。部门后续改名或归档，历史工单上
    # 仍要能读出"当初派给了谁"——只存 id 会在部门被删除后变成一个悬空串。
    department_id = str(body.get("department_id") or "").strip()[:64] or None
    department_name = str(body.get("department_name") or "").strip()[:60] or None
    if department_id and not department_name:
        row = db.query_one("SELECT name FROM departments WHERE tenant_id = ? AND dept_id = ?",
                           (tenant, department_id))
        department_name = (row or {}).get("name")

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
                                        department_id, department_name,
                                        created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ticket_id, tenant, code, title, summary, priority, "draft", dimension or "",
                 filters["dataset_id"], json.dumps(filters, ensure_ascii=False), _uid(),
                 None, rule_id or None, None, None, None,
                 None, None, 0, department_id, department_name, _fmt(now), _fmt(now)))
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
             "t.department_id, t.department_name, "
             "t.first_response_at, t.closed_at, t.reopen_count, t.created_at, t.updated_at")
SELECT_COLS = LIST_COLS + ", t.filters, t.resolution, t.review_note"


def clocks(t, now=None):
    """详情面板的三把尺子：本单的两把（首次响应 / 归档）＋ 部门期望值（不计本单）。

    used 一律由后端算工作小时：前端只有周末概念，配置里的节假日它不知道，
    自己算会在节前节后差出一两天——而这一格正是用来判断"还剩多少"的。
    部门那一把只给出数值，不参与本单计时：它讲的是"交给这个部门一般多久有人接"，
    从研判页的交接算起，和这张单的创建时刻无关。
    """
    now = now or datetime.now()
    created = _parse(t.get("created_at"))
    if not created:
        return None
    closed = _parse(t.get("closed_at"))
    first_at = _parse(t.get("first_response_at"))
    out = {
        "first": {
            "budget": config.SLA_FIRST_HOURS.get(t["priority"]),
            "used": round(work_minutes_between(created, first_at or now) / 60.0, 1),
            "at": _fmt(first_at),
        },
        "archive": {
            "budget": config.SLA_ARCHIVE_HOURS.get(t["priority"]),
            "used": round(work_minutes_between(created, closed or now) / 60.0, 1),
            "at": _fmt(closed),
        },
        "department": None,
    }
    if t.get("department_id"):
        dep = db.query_one(
            "SELECT name, sla_hours FROM departments WHERE tenant_id = ? AND dept_id = ?",
            (db.tid(), t["department_id"]))
        if dep:
            out["department"] = {"name": dep["name"], "hours": dep["sla_hours"]}
    return out


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
    t["clocks"] = clocks(t)
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
    # 归口范围先于 owner 判断：部门账号看不到别部门的单，处置员看不到别人名下的单，
    # 两种情况对外都是 404——403 等于告诉他"这个编号是存在的"。
    if not _in_scope(t):
        raise NotFound()
    if _role() == "handler" and not _is_owner(t) and not _skip_guard:
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
    out, seen = [], set()
    for ref in refs[:config.TICKET_MAX_EVIDENCE]:
        row = analytics.get_comment(dataset_id, ref) if dataset_id else None
        if row:
            snippet = (row.get("content_masked") or "")
            key = "".join(snippet.split())
            # 历史工单可能在去重之前就收进了同文异源的两条：显示层再挡一次。
            # 失联证据（原文已不在数据集内）不参与判重——它们各自是一条断链，要逐条看见。
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            out.append({"comment_ref": row["comment_ref"],
                        "snippet": snippet[:160],
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
    _scope_where(clauses, args)
    status = [s for s in (params.get("status") or []) if s in ALL_STATUSES]
    if params.get("status_group") == "open":
        clauses.append(f"t.status IN ({','.join('?' * len(OPEN_STATUSES))})")
        args += list(OPEN_STATUSES)
    elif params.get("status_group") == "overdue":
        clauses.append("t.sla_due IS NOT NULL AND t.sla_due < ?")
        clauses.append(f"t.status IN ({','.join('?' * len(OPEN_STATUSES))})")
        args.append(_fmt(datetime.now()))
        args += list(OPEN_STATUSES)
    elif params.get("status_group") == "closed":
        # 「已完成」只认已归档。驳回不是完成——被驳回的单意味着"这张单不成立"，
        # 把它算进完成量会让处置量虚高，也会让人以为那个主题已经处理过了。
        clauses.append("t.status = 'closed'")
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


def readonly_badge():
    """演示态的角标与"工单闭环"条：只给三个数，一个动作都不给。

    形状对齐 dashboard_badge()，前端不用写第二套解析。多带一个 readonly=True，
    就是要它别再去请求 /api/tickets/stats——那个接口在模块关闭时是 404，
    前端验证脚本按"零控制台报错"判定，一次注定失败的请求会被当成页面故障。
    """
    open_clause = f"status IN ({','.join('?' * len(OPEN_STATUSES))})"
    total = db.scalar("SELECT COUNT(*) FROM tickets WHERE tenant_id = ?", (db.tid(),)) or 0
    open_n = db.scalar(f"SELECT COUNT(*) FROM tickets WHERE tenant_id = ? AND {open_clause}",
                       (db.tid(), *OPEN_STATUSES)) or 0
    overdue = db.scalar(
        f"SELECT COUNT(*) FROM tickets WHERE tenant_id = ? AND {open_clause} "
        f"AND sla_due IS NOT NULL AND sla_due < ?",
        (db.tid(), *OPEN_STATUSES, _fmt(datetime.now()))) or 0
    return {"enabled": False, "can_view": True, "readonly": True, "scope": "all", "mine": 0,
            "pending": open_n, "overdue": overdue, "total": total,
            "label": str(open_n or ""), "overdue_label": str(overdue or "")}


# ============================ 演示模式只读列表 ============================
def readonly_list(limit=30):
    """工单模块关掉时给人看的那一版：只读、无筛选、无动作。

    CP_TICKETS=0 的语义是"这个演示态不开放写操作"，不是"假装系统里没有工单"。
    研判页副标题承诺"下一步在子页『处置工单』里闭环"，这里就必须真有东西可看，
    否则那条链在最后一格断成 404，读者得到的只是一句"地址输入有误"。

    不查任何权限是有意的：走到这里的人本来就没有工单权限，查一次只会 403。
    返回的字段也刻意不含身份信息，只有"哪件事、谁在办、什么时候到期"。
    """
    rows = db.query_all(
        f"SELECT {LIST_COLS} FROM tickets t WHERE t.tenant_id = ? "
        # 办理中的排最前（那才是"现在要有人管"的），已结案的沉底
        f"ORDER BY CASE t.status WHEN 'processing' THEN 0 WHEN 'pending_review' THEN 1 "
        f"WHEN 'closed' THEN 3 WHEN 'rejected' THEN 4 ELSE 2 END, "
        f"COALESCE(t.sla_due, t.created_at) ASC LIMIT ?",
        (db.tid(), max(1, min(int(limit), 100))))
    return [_row_to_ticket(r, detail=False) for r in rows]


# ============================ 状态迁移 ============================
def transition(ticket_id, body):
    ensure_can_view()
    action = str(body.get("action") or "").strip()
    note = str(body.get("note") or "").strip()[:_NOTE_MAX]
    owner_id = str(body.get("owner_id") or "").strip()

    t = _fetch(ticket_id)
    if not t:
        raise NotFound()
    if not _in_scope(t):
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
            raise TicketError(f"未知主题「{dimension}」", 400, "invalid_dimension")
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
    if u["role"] not in ("handler", "dept", "admin"):
        raise TicketError("只能派给处置员、部门账号或管理员", 400, "invalid_owner")
    if u["role"] == "dept" and not u.get("department_id"):
        # 没绑定部门的部门账号什么都看不到，派过去等于把单扔进黑洞
        raise TicketError("该部门账号未绑定部门，无法分派", 400, "invalid_owner")
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


def delete(ticket_id, reason=""):
    """删除一张工单（不可逆）。

    **为什么是物理删除，而不是再加一个 `deleted` 状态**：软删要在列表、统计、趋势、
    顶栏角标、部门范围、建单去重、研判快照回挂……每一处查询里补一句
    `deleted_at IS NULL`。这类"漏一处"的后果是"删掉的单还在统计里"，
    而且页面上根本看不出来——比删错一张单更难发现。物理删除之后，
    所有已有查询自动是对的口径。

    审计怎么办：`ticket_events` 是 append-only，这里先写一条 `delete` 事件
    （带工单号、标题快照、删除原因），再删主体。事件行删不掉，
    所以"哪张单在什么时候被谁删了、为什么"仍然查得到，只是页面上没有入口了。

    权限只给管理员：分析师能建单、能驳回，但"让一张单从系统里消失"是运维动作，
    不该和业务判断混在同一个角色里。
    """
    if not can_manage():
        raise Forbidden("只有管理员可以删除工单")
    reason = str(reason or "").strip()
    if len(reason) < 4:
        raise TicketError("请填写删除原因（至少 4 个字），它会记入审计日志",
                          400, "note_required")
    t = _fetch(ticket_id)
    if not t:
        raise NotFound()
    if not _in_scope(t):
        raise NotFound()

    conn = db.get_conn()
    # 事件与删除放进同一个事务：先提交事件再删，一旦删除失败就会留下一条
    # "已删除"的假记录，而单还在——那比没有记录更糟。
    _event(ticket_id, "delete", t["status"], t["status"],
           note=f"删除工单 {t['code']}《{t['title']}》· 原因：{reason}",
           commit=False)
    # 证据关联跟着主体一起删：留着孤儿 link 会让"同一条证据不能重复建单"
    # 这条去重规则在 JOIN 失败时失去依据，也没人再去清理它们。
    conn.execute("DELETE FROM ticket_links WHERE tenant_id = ? AND ticket_id = ?",
                 (db.tid(), ticket_id))
    conn.execute("DELETE FROM tickets WHERE tenant_id = ? AND ticket_id = ?",
                 (db.tid(), ticket_id))
    conn.commit()
    return {"deleted": True, "ticket_id": ticket_id, "code": t["code"],
            "message": f"{t['code']} 已删除（审计日志保留删除记录）"}


def comment(ticket_id, body):
    ensure_can_view()
    note = str(body.get("note") or "").strip()[:_NOTE_MAX]
    if not note:
        raise TicketError("评论内容不能为空", 400, "empty_comment")
    t = _fetch(ticket_id)
    if not t:
        raise NotFound()
    if not _in_scope(t):
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
    # 指标必须和列表是同一个范围：部门账号看到"在办 8 单"、列表里只有 2 单，
    # 这个页面的所有数字立刻失去意义。
    clauses, args = ["tenant_id = ?"], [db.tid()]
    _scope_where(clauses, args, alias="")
    rows = db.query_all(
        "SELECT status, priority, sla_due, closed_at, first_response_at, created_at, "
        f"reopen_count FROM tickets WHERE {' AND '.join(clauses)}", args)
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
            "workday_hours": config.WORKDAY_HOURS,
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
    {"rule_id": "R2", "name": "单主题负面集中", "priority": "P1",
     "condition": "单主题负面 ≥ 20 条且占该主题 ≥ 25%（窗口 = 当前筛选范围）"},
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

    # ---- R2 单主题负面集中：窗口 = 当前筛选范围 ----
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
            "rule_id": "R2", "rule_name": "单主题负面集中", "priority": "P1",
            "dimension": hit["dim"], "title": f"「{hit['dim']}」负面 {hit['neg']} 条，占该主题 {share}%",
            "detail": f"当前筛选范围内，该主题共 {hit['n']} 条，其中负面 {hit['neg']} 条（{share}%），"
                      "满足「≥ 20 条且占比 ≥ 25%」。属常规集中吐槽，建议按议题处理，"
                      "不必上升到事件定性。",
            "basis": {"负面": hit["neg"], "该主题总数": hit["n"], "占比": f"{share}%"},
            "filters": {**span, "dimensions": [hit["dim"]]},
            "evidence": pick_evidence({**f, "dimensions": [hit["dim"]]}, 3),
        })
    else:
        skipped.append({"rule_id": "R2",
                        "reason": "没有主题同时满足「负面 ≥ 20 条」与「占比 ≥ 25%」"})

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
    """可被分派的人。部门账号也在里面：单归口到某个部门后，
    admin 可以把单直接派给这个部门的账号，而不是先找一个人。"""
    rows = db.query_all(
        "SELECT u.user_id, u.username, u.display_name, u.role, u.department_id, "
        "d.name AS dept_name FROM users u "
        "LEFT JOIN departments d ON d.dept_id = u.department_id AND d.tenant_id = u.tenant_id "
        "WHERE u.tenant_id = ? AND u.status = 'active' AND u.role IN ('handler', 'dept', 'admin') "
        "ORDER BY CASE u.role WHEN 'admin' THEN 1 WHEN 'handler' THEN 2 ELSE 0 END, u.username ASC",
        (db.tid(),))
    out = []
    for r in rows:
        label = config.ROLE_LABELS.get(r["role"], r["role"])
        if r["role"] == "dept" and r["dept_name"]:
            label = f"{r['dept_name']} · 部门账号"
        out.append({"user_id": r["user_id"], "name": r["display_name"] or r["username"],
                    "role": r["role"], "role_label": label})
    return out


def dashboard_badge():
    """/api/tickets/badge：顶栏待办角标 + 驾驶舱一格。"""
    if not can_view_any() and not can_handle():
        return {"enabled": True, "can_view": False, "mine": 0, "pending": 0, "overdue": 0}
    ensure_can_view()
    now = _fmt(datetime.now())
    # 角标必须跟着可见范围走：部门账号看到"待处置 9"、点进去只有 2 单，
    # 这个数字就变成了误导。范围口径与列表一致（dept_scope）。
    dept = dept_scope()
    base, base_args = "tenant_id = ?", [db.tid()]
    if dept is not None:
        base += " AND department_id = ?"
        base_args.append(dept)
    if _role() == "handler":
        scope_sql, scope_args = base + " AND owner_id = ?", base_args + [_uid()]
        mine_status = "('assigned','processing','reopened')"
    elif dept is not None:
        # 部门账号的"待办"= 本部门还没办完的单，不按 owner 分——
        # 部门是责任主体，工单没分到具体人时也该有人看得见。
        scope_sql, scope_args = base, base_args
        mine_status = "('assigned','processing','reopened')"
    else:
        scope_sql, scope_args = base, base_args
        mine_status = "('draft','triaged','pending_review')"
    mine = db.scalar(
        f"SELECT COUNT(*) FROM tickets WHERE {scope_sql} AND status IN {mine_status}",
        scope_args) or 0
    overdue = db.scalar(
        f"SELECT COUNT(*) FROM tickets WHERE {scope_sql} AND sla_due < ? "
        "AND status IN ('assigned','processing','pending_review','reopened')",
        scope_args + [now]) or 0
    pending = db.scalar(
        f"SELECT COUNT(*) FROM tickets WHERE {scope_sql} AND status IN "
        "('draft','triaged','assigned','processing','pending_review','reopened')",
        scope_args) or 0
    # scope 决定顶栏角标该显示哪个数：处置员看"我的"，管理员/分析员看"全校未结"。
    # 以前前端只取 mine，管理员 mine=0 时整枚角标消失——3 张超期单在顶栏上一个提示都没有。
    scope = "mine" if _role() == "handler" else ("dept" if dept is not None else "all")
    return {"enabled": True, "can_view": True, "scope": scope,
            "mine": mine, "pending": pending, "overdue": overdue,
            "label": f"未结 {pending}", "overdue_label": f"超期 {overdue}"}


def trend(days=14):
    """近 N 天建单/归档数——驾驶舱一格用，口径简单（按创建时间/归档时间落在哪一天）。"""
    ensure_can_view()
    start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    clauses, args = ["tenant_id = ?", "created_at >= ?"], [db.tid(), start + " 00:00:00"]
    _scope_where(clauses, args, alias="")
    created = db.query_all(
        f"SELECT substr(created_at, 1, 10) d, COUNT(*) n FROM tickets "
        f"WHERE {' AND '.join(clauses)} GROUP BY d", args)
    closed_clauses, closed_args = (["tenant_id = ?", "closed_at IS NOT NULL", "closed_at >= ?"],
                                   [db.tid(), start + " 00:00:00"])
    _scope_where(closed_clauses, closed_args, alias="")
    closed = db.query_all(
        f"SELECT substr(closed_at, 1, 10) d, COUNT(*) n FROM tickets "
        f"WHERE {' AND '.join(closed_clauses)} GROUP BY d", closed_args)
    cmap = {r["d"]: r["n"] for r in created}
    kmap = {r["d"]: r["n"] for r in closed}
    out = []
    for i in range(days):
        d = (datetime.now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        out.append({"date": d, "created": cmap.get(d, 0), "closed": kmap.get(d, 0)})
    return {"trend": out, "disclaimer": DISCLAIMER}
