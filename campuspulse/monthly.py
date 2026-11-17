# -*- coding: utf-8 -*-
"""区间风险研判：把任意一段时间的评论单独结一次账，结论落库、由人决定要不要开单。

`departments.assess()` 回答的是"现在这一批数据里，哪个主题该谁管、多急"，
锚点是数据集里最新那一天，看的是滚动 30 天。它的问题是**没有记忆**：
今天算完，下个月再算，上一次的结论就没了，也没人知道这个月跟上学期的差别。

这一层补三件它没有的事：

**一、窗口是一段明确的日期，不是滚动 30 天。**
   "9 月 15 日到 10 月 14 日食堂被骂了 40 条"是一句能对上具体事件的话；
   "近 30 天被骂 40 条"落不到任何一次会上。窗口由调用方给定（页面上自选起止日期），
   增速项对比**紧邻的等长区间**——恰好是整月时按上一个自然月比，因为"9 月比 8 月"
   才是人话，"8 月 31 日到 9 月 30 日"读起来像算错了。
   定时任务固定跑「上个月」，也就是一个整月区间。

**二、结论落库（risk_runs）。**
   三个月后有人问"这张工单凭什么开的"，答案只能在当期那条研判记录里。
   `topics` 存整份 JSON 快照而不是评论 ID 引用——评论会重新导入、会被清理，
   引用会失效，快照不会。

**三、研判只产出结论，开单由人点。**
   这里没有门槛、没有自动建单。哪一条该开单，取决于"这几天有没有人在盯、
   别的渠道是不是也在说同一件事"——系统不知道这两件事。它能做的是把
   该谁管、多急、凭什么摆清楚，剩下的由人在页面上挑。
   早先有过"风险≥70 自动建单 + 单次上限 3 张"的版本，问题在于它必须替人
   猜那两件事：门槛调低了刷废单，调高了漏真事。所以砍掉了。
   研判快照里只记一件事——**这段区间哪些主题已经有单了**，好让页面上
   那一行显示单号，而不是留一个"点了一定报重复"的按钮。

关于数据集：定时任务没有浏览器，取 `config.MONTHLY_DATASET`（默认主数据集）；
页面上手动研判时取当前选中的数据集——两者可以不同，各走各的。

**区间里没有数据时不再自动换区间。** 以前的"月度"是系统替人选的月份，缺数据只能回落；
现在区间是人自己选的，选错了要明确告诉他"这段没有评论，可用范围是 X~Y"，
而不是悄悄换成另一个区间交上一份不知道在说哪段时间的报告。
"""
import json
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta

from . import config, db, departments, tenancy, tickets
from .tickets import TS_FMT, Conflict, Forbidden, TicketError

# ============================ 时间窗口 ============================
# 区间的两种来源：整月（定时任务 / 旧的 --period 参数）与任意起止日期。
# 统一在 resolve_range() 里归一化成 (date_from, date_to) 两个字符串，
# 下游只认这一对，不再关心它是怎么来的。
_PERIOD_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

MAX_RANGE_DAYS = 366      # 再长就不是"这一段发生了什么"，而是"近几年的画像"了


def parse_period(text):
    """'2026-10' → (2026, 10)。格式错就报 400，不要默默用今天。"""
    s = str(text or "").strip()
    m = _PERIOD_RE.match(s)
    if not m:
        raise TicketError(f"月份格式应为 YYYY-MM，收到「{text}」", 400, "invalid_period")
    return int(m.group(1)), int(m.group(2))


def parse_date(text, field="日期"):
    s = str(text or "").strip()
    if not _DATE_RE.match(s):
        raise TicketError(f"{field}格式应为 YYYY-MM-DD，收到「{text}」", 400, "invalid_range")
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise TicketError(f"{field}不存在：{text}（月份需在 1-12，日期需在该月范围内）",
                          400, "invalid_range")


def month_window(period):
    """自然月 → ('2026-10-01', '2026-10-31')。月末靠下月 1 号减一天算，不写 28/30/31。"""
    y, m = parse_period(period)
    first = date(y, m, 1)
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return first.isoformat(), (nxt - timedelta(days=1)).isoformat()


def shift_period(period, delta):
    """往前/后挪月份。'2026-01' 前一个月是 '2025-12'。"""
    y, m = parse_period(period)
    idx = y * 12 + (m - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def current_period(today=None):
    d = today or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def last_closed_period(today=None):
    """上个月。这是定时任务的默认目标——本月还没过完，不能拿来结账。"""
    return shift_period(current_period(today), -1)


def resolve_range(date_from=None, date_to=None, period=None):
    """把「起止日期」或「月份」统一成 (date_from, date_to)。

    优先级：成对日期 > 月份 > 上个月。
    只给一头直接报错：补另一半只能靠猜（到月末？到当天？），
    猜错了用户看到的是一份"不知道在说哪段时间"的报告。
    """
    if date_from or date_to:
        if not (date_from and date_to):
            raise TicketError("起止日期需要成对填写", 400, "invalid_range")
        a, b = parse_date(date_from, "开始"), parse_date(date_to, "结束")
        if a > b:
            raise TicketError("开始日期不能晚于结束日期", 400, "invalid_range")
        span = (b - a).days + 1
        if span > MAX_RANGE_DAYS:
            raise TicketError(f"区间最长 {MAX_RANGE_DAYS} 天（当前 {span} 天），请缩小范围",
                              400, "invalid_range")
        return a.isoformat(), b.isoformat()
    return month_window(str(period or last_closed_period()).strip())


def is_full_month(d1, d2):
    """整月判定：1 号起，且结束日必须落在**同一个月**的月末。

    只判"结束日的后一天是 1 号"是不够的：6/1~8/31 满足这条，却横跨三个月。
    它带来的连锁反应比标签写错严重得多——
      · 区间标签变成 "2026-06"，于是它和真正的六月共用
        (tenant, dataset, period) 唯一键，两份口径不同的记录互相覆盖；
      · 上期按"上一个自然月"算，拿三个月去比一个月，增速直接失真；
      · 页面上写着"本月 11 条 vs 上月 3 条"，其实那是三个月。
    所以同一年、同一月这两条必须一起判。
    """
    try:
        a, b = date.fromisoformat(d1), date.fromisoformat(d2)
    except ValueError:
        return False
    return (a.day == 1 and (a.year, a.month) == (b.year, b.month)
            and (b + timedelta(days=1)).day == 1)


def range_label(d1, d2):
    """区间标签：整月用 YYYY-MM（和历史记录、定时任务一致），否则 起~止。"""
    if is_full_month(d1, d2):
        a = date.fromisoformat(d1)
        return f"{a.year:04d}-{a.month:02d}"
    return f"{d1}~{d2}"


def range_key(d1, d2):
    """紧凑键，给工单规则号用：20260901-20260930。"""
    return f"{d1.replace('-', '')}-{d2.replace('-', '')}"


def previous_range(d1, d2):
    """上期区间（增速项的对比基准）。

    整月 → 上一个自然月；任意区间 → 等长前移（9/15~10/14 的上期是 8/16~9/14）。
    两种情况都是"紧挨着的、同样长的一段"，只是整月时换成人才读得懂的说法。
    """
    if is_full_month(d1, d2):
        a = date.fromisoformat(d1)
        return month_window(shift_period(f"{a.year:04d}-{a.month:02d}", -1))
    a, b = date.fromisoformat(d1), date.fromisoformat(d2)
    span = (b - a).days + 1
    prev_end = a - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    return prev_start.isoformat(), prev_end.isoformat()


# ============================ 状态 ============================
def settings_info():
    """页面顶栏用的只读状态。

    这里**没有可改项**：研判不设门槛、不自动建单，所以没有"策略"要配。
    页面上曾经有个"建单模式"下拉（自动 / 待人工确认），随着门槛一起删掉了——
    一个始终应该是"人工"的开关，留着只会让看的人以为系统真的会自己开单。
    """
    return {
        "enabled": config.CP_MONTHLY,
        "auto_ticket": config.CP_TICKETS,
        "dataset_id": config.MONTHLY_DATASET,
        "check_minutes": config.MONTHLY_CHECK_MINUTES,
        "scheduler": _SCHEDULER.get("state", "stopped"),
    }


# ============================ 读取 ============================
def _live_codes(codes):
    """批量问一句：这些单号现在还开着吗（未归档、未驳回）？

    返回 {code: status}，只含**仍在办**的那些；查不到就是已经结案了。
    旧库里可能还没有 tickets 表，那时按"仍在办"处理——宁可多显示一个单号，
    也不要因为一张缺表就把所有研判记录显示成"可重新建单"。
    """
    codes = [c for c in dict.fromkeys(codes) if c]
    if not codes or not config.CP_TICKETS:
        return {}
    marks = ",".join("?" * len(codes))
    try:
        rows = db.query_all(
            f"SELECT code, status FROM tickets WHERE tenant_id = ? AND code IN ({marks}) "
            "AND status NOT IN ('closed', 'rejected')", [db.tid()] + codes)
    except Exception:
        return {c: "unknown" for c in codes}
    return {r["code"]: r["status"] for r in rows}


def _drop_stale_links(topics):
    """把快照里指向"已结案工单"的单号清掉，让这一行重新长出「建单」按钮。

    快照是建单那一刻的复印件，工单后来被驳回或归档，这份复印件并不知道。
    不清的话页面上永远挂着一个旧单号，那一行再也没有建单入口——
    用户看到的就是"驳回之后不能再次建单"，而且重跑研判也救不回来：
    `_attach_if_exists` 只挂未关闭的单，却不会动已经写死在快照里的 ticket_code。
    """
    linked = [t for t in topics if t.get("ticket_code")]
    if not linked:
        return topics
    live = _live_codes([t["ticket_code"] for t in linked])
    for t in linked:
        if t["ticket_code"] in live:
            continue
        t["ticket_closed"] = t["ticket_code"]
        t.pop("ticket_code", None)
        t.pop("ticket_existing", None)
        # "已结案"涵盖三种情况：驳回、归档、被管理员删除。快照只关心"这个号还能不能点"。
        t["ticket_note"] = f"上一张单 {t['ticket_closed']} 已结案或已删除，可重新建单"
    return topics


def _row(r, with_topics=False):
    out = {
        "run_id": r["run_id"], "dataset_id": r["dataset_id"], "period": r["period"],
        "date_from": r["date_from"], "date_to": r["date_to"],
        "requested": r["requested"] or r["period"],
        "trigger": r["trigger_by"],
        "status": r["status"], "engine": r["engine"], "summary": r["summary"] or "",
        "sample": r["sample"], "negative": r["negative"],
        "tickets": r["tickets"],
        "ticket_codes": _json(r["ticket_codes"], []),
        "error": r["error"] or "",
        "created_at": r["created_at"], "finished_at": r["finished_at"],
    }
    # 上期区间每次读的时候现算，而不是落库：它是 date_from/date_to 的纯函数，
    # 多存一份就多一个能和日期对不上的字段（改口径后老记录会自相矛盾）。
    try:
        p1, p2 = previous_range(r["date_from"], r["date_to"])
        out["window"] = {"prev_period": range_label(p1, p2), "prev_from": p1, "prev_to": p2}
    except (ValueError, TypeError):
        out["window"] = {}
    if with_topics:
        out["topics"] = _drop_stale_links(_json(r["topics"], []))
        out["counts"] = _counts(out["topics"])
    return out


def _json(raw, default):
    if not raw:
        return default
    try:
        v = json.loads(raw)
        return v if isinstance(v, type(default)) else default
    except (ValueError, TypeError):
        return default


def _counts(topics):
    """给页面上的"下一步该点哪个"用：几个主题、几个高危、几个已经有单。"""
    return {
        "total": len(topics),
        "high": len([t for t in topics if t.get("level") in ("高", "中高")]),
        "linked": len([t for t in topics if t.get("ticket_code")]),
    }


def list_runs(limit=12, dataset_id=None):
    departments._ensure_view()
    sql = "SELECT * FROM risk_runs WHERE tenant_id = ?"
    params = [db.tid()]
    if dataset_id:
        sql += " AND dataset_id = ?"
        params.append(dataset_id)
    sql += " ORDER BY period DESC, created_at DESC LIMIT ?"
    params.append(max(1, min(60, int(limit or 12))))
    return [_row(r) for r in db.query_all(sql, params)]


def get_run(run_id):
    departments._ensure_view()
    r = db.query_one("SELECT * FROM risk_runs WHERE tenant_id = ? AND run_id = ?",
                     (db.tid(), run_id))
    if not r:
        raise TicketError("研判记录不存在", 404, "not_found")
    return _row(r, with_topics=True)


def latest_run(dataset_id=None):
    rows = list_runs(limit=1, dataset_id=dataset_id)
    return rows[0] if rows else None


def _find(dataset_id, period):
    return db.query_one(
        "SELECT * FROM risk_runs WHERE tenant_id = ? AND dataset_id = ? AND period = ?",
        (db.tid(), dataset_id, period))


def _save(payload):
    cols = ("run_id", "tenant_id", "dataset_id", "period", "date_from", "date_to", "requested",
            "trigger_by", "ticket_mode", "status", "engine", "summary", "sample", "negative",
            "topics", "tickets", "ticket_codes", "error", "created_at", "finished_at")
    row = {k: payload.get(k) for k in cols}
    row["tenant_id"] = db.tid()
    conn = db.get_conn()
    # INSERT OR REPLACE 靠 UNIQUE(tenant_id, dataset_id, period) 兜底：
    # 同一个月重跑就覆盖，不会堆出一串同月份的重复记录。
    conn.execute(
        f"INSERT OR REPLACE INTO risk_runs ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(cols))})", [row[k] for k in cols])
    conn.commit()


# ============================ 执行 ============================
def _count_range(dataset_id, d1, d2, negative=False):
    sql = ("SELECT COUNT(*) FROM comments WHERE tenant_id = ? AND dataset_id = ? "
           "AND cleaning_status = 'kept' AND comment_date >= ? AND comment_date <= ?")
    params = [db.tid(), dataset_id, d1, d2]
    if negative:
        sql += " AND sentiment = 'negative'"
    return db.scalar(sql, params) or 0


def _dataset_span(dataset_id):
    """该数据集可用评论的日期范围，用于"你选的区间没数据"时给人一个可改的方向。"""
    row = db.query_one(
        "SELECT MIN(comment_date) a, MAX(comment_date) b FROM comments "
        "WHERE tenant_id = ? AND dataset_id = ? AND cleaning_status = 'kept'",
        (db.tid(), dataset_id))
    if row and row["a"] and row["b"]:
        return {"from": row["a"], "to": row["b"]}
    return None


def _ticket_rule(d1, d2, dimension):
    """工单规则号：RNG-<起><止>-<主题>。

    区间进规则号：同一主题换一段日期还能再开一张，同区间重复点则被去重挡住。
    用紧凑日期（20260901-20260930）而不是带横线的写法：rule_id 有 32 字上限，
    带横线会被截断到主题名中间，看不出说的是什么主题。
    """
    return f"RNG-{range_key(d1, d2)}-{dimension}"[:32]


def _attach_if_exists(topic, d1, d2):
    """研判时顺手看一眼：这段区间这个主题是不是已经有未关闭的工单。

    只读，不建单也不改库。不查这一下，页面上每一行都长着「建单」按钮，
    点下去才报"已有同类工单"——看起来像系统坏了。
    口径必须和 tickets.create 的查重一致（排除已归档/已驳回），
    否则会出现"页面显示有单、点建单却能建出来第二张"。
    """
    if not config.CP_TICKETS:
        return
    try:
        row = db.query_one(
            "SELECT code, status, sla_due FROM tickets WHERE tenant_id = ? AND rule_id = ? "
            "AND status NOT IN ('closed', 'rejected') LIMIT 1",
            (db.tid(), _ticket_rule(d1, d2, topic["dimension"])))
    except Exception:                     # 旧库里还没有 tickets 表时不影响研判
        return
    if not row:
        return
    topic["ticket_code"] = row["code"]
    topic["ticket_due"] = row["sla_due"]
    topic["ticket_existing"] = True
    topic["ticket_note"] = f"已有工单 {row['code']}（{row['status']}）"
    topic.pop("ticket_closed", None)


def _create_ticket(topic, run, period, with_evidence=True, note=""):
    """把一条研判结果落成工单。数字全部来自研判快照，不再重算。

    这里和服务端"所见即所建"的纪律一致：用户在页面上看到风险指数 76，
    工单摘要里就得是 76%——建单时重新跑一遍统计，遇到数据刚好被重导入，
    两处数字对不上，人只会认为系统在胡说。

    `with_evidence=False` 用在证据撞车时的重试：一条评论可以同时命中多个主题，
    于是它可能在几个主题里都是"点赞最高的那条负面"。第一个主题建单占用了它，
    后面几个就会被 tickets.create 的证据去重挡下来。这时去掉单条证据、
    只留筛选口径，工单依然站得住——它统计的是那批评论，不是那一条。

    `note` 是人点建单时补的一句话（现场情况、联系人、为什么现在开单）。
    单独成段放在摘要末尾并标明来源：摘要前半段是统计事实，后面这段是人写的，
    读单的人要能分清哪句是系统算的、哪句是同事补的。
    """
    dept = topic.get("department") or {}
    advice = topic.get("advice") or []
    summary = (f"{period} 研判：风险指数 {topic['risk']}（{topic['level']}）· "
               f"负面 {topic['negative']}/{topic['total']} 条（{topic['neg_rate']}%）。\n"
               + "\n".join(f"{i + 1}. {a}" for i, a in enumerate(advice)))
    if dept:
        summary += f"\n归口：{dept['name']}（负责人 {dept.get('owner_name') or '未填'}）"
    note = str(note or "").strip()[:200]
    if note:
        summary += f"\n\n备注（建单人）：{note}"
    evidence = (topic.get("samples") or [{}])[0].get("comment_ref") if with_evidence else None
    return tickets.create({
        "title": f"{period}｜「{topic['dimension']}」风险指数 {topic['risk']} 需核实处置"[:120],
        "summary": summary,
        "priority": topic["priority"],
        "dimension": topic["dimension"],
        # 存口径而不是数字：点开单详情要能复现"当时在说哪一批评论"
        "filters": {"dataset_id": run["dataset_id"], "dimensions": [topic["dimension"]],
                    "date_from": run["date_from"], "date_to": run["date_to"]},
        "comment_ref": evidence,
        "rule_id": _ticket_rule(run["date_from"], run["date_to"], topic["dimension"]),
        "department_id": dept.get("dept_id"),
        "department_name": dept.get("name"),
    })


def _make_ticket(topic, run, period, note=""):
    """建单，证据撞车时去掉证据重试一次。"""
    try:
        return _create_ticket(topic, run, period, note=note)
    except Conflict:
        return _create_ticket(topic, run, period, with_evidence=False, note=note)


def _attach_existing(topic, err):
    """建单撞上"已有同类工单"时，把那单的编号挂回研判快照。

    不把它算作本次新建（tickets 计数只认真正写进去的），但页面上要能看见
    "这个主题已经有单了"——否则同一个主题每月都显示一个可点的建单按钮，
    点一次失败一次。
    """
    extra = getattr(err, "extra", None) or {}
    dup = (extra.get("dup") or {}) if isinstance(extra, dict) else {}
    code = str(dup.get("code") or "").strip()
    if code:
        topic["ticket_code"] = code
        topic["ticket_existing"] = True
        topic.pop("ticket_closed", None)
        topic["ticket_note"] = f"已有同类工单 {code}（{dup.get('status') or '处理中'}）"
    else:
        topic["ticket_note"] = str(err)[:60]


def run(period=None, dataset_id=None, trigger="manual", force=False, use_ai=True,
        date_from=None, date_to=None):
    """跑一次区间研判。只出结论，不建单。

    窗口的三种给法（见 `resolve_range`）：

    - `date_from` + `date_to`：任意区间，页面上自选，最长 366 天；
    - `period='2026-10'`：该自然月，定时任务与命令行用这个；
    - 都不给：上个月。

    同一个 (数据集, 区间) 已研判过且 `force=False` 时直接返回既有记录，
    定时任务每小时问一次也不会重复跑。

    权限要求"能建单"（分析员 / 管理员）：研判记录是全租户共用的一份快照，
    不是"看一眼就算"的实时查询——只读角色刷新页面不该改写别人看到的结论。
    """
    if not tickets.can_create():
        raise Forbidden("当前角色无权执行区间研判（需要分析员或管理员）")
    if not db.tid():
        raise Forbidden("没有租户上下文，无法执行区间研判")

    ds = str(dataset_id or config.MONTHLY_DATASET).strip()
    if not db.get_dataset(ds):
        raise TicketError(f"数据集不存在：{ds}", 400, "invalid_filter")

    d1, d2 = resolve_range(date_from, date_to, period)
    label = range_label(d1, d2)

    if not force:
        old = _find(ds, label)
        if old and old["status"] == "done":
            return {**_row(old, with_topics=True), "skipped": True,
                    "skip_reason": f"{label} 已完成研判，不重复执行"}

    # 区间里没有评论就直接说清楚，**不再自动换成别的区间**。
    # 月度时代必须回落，是因为月份是系统替人选的；现在区间由人选，
    # 悄悄换一段等于交上一份"不知道在说哪段时间"的报告。
    if not _count_range(ds, d1, d2):
        span = _dataset_span(ds) or {}
        hint = (f"该数据集可用范围 {span.get('from')} ~ {span.get('to')}" if span
                else "该数据集没有可用日期")
        raise TicketError(f"{d1} ~ {d2} 没有可研判的评论（{hint}），请调整起止日期",
                          409, "no_data", {"dataset_span": span})

    p1, p2 = previous_range(d1, d2)
    span = ("本月", "上月") if is_full_month(d1, d2) else ("本期", "上期")
    run_id = "rr_" + secrets.token_hex(8)
    started = datetime.now().strftime(TS_FMT)

    try:
        data = departments.assess(ds, use_ai=use_ai,
                                  window={"period": label, "date_from": d1, "date_to": d2,
                                          "prev_from": p1, "prev_to": p2, "span": span})
    except Exception as e:
        _save({"run_id": run_id, "dataset_id": ds, "period": label, "date_from": d1,
               "date_to": d2, "requested": label, "trigger_by": trigger,
               "ticket_mode": "manual", "status": "failed", "engine": "rule",
               "summary": "", "sample": 0, "negative": 0, "topics": "[]",
               "tickets": 0, "ticket_codes": "[]", "error": str(e)[:300],
               "created_at": started, "finished_at": datetime.now().strftime(TS_FMT)})
        raise

    topics = data.get("topics") or []
    # 只挂已有单号，不建单。页面上那一行据此显示「TK-…」或「建单」按钮，
    # 免得给人一个点下去一定报"已有同类工单"的按钮。
    # departments.assess() 会按"同主题"挂上任意时段的未结单，那对滚动研判成立、
    # 对区间研判不成立：别的月份的单会让本区间显示"已有工单"，点建单又被 409 挡住。
    # 所以先清掉，再只认 rule_id = RNG-<本区间>-<主题> 的那一张。
    for t in topics:
        for key in ("ticket_code", "ticket_due", "ticket_note", "ticket_existing"):
            t.pop(key, None)
        _attach_if_exists(t, d1, d2)

    codes = [t["ticket_code"] for t in topics if t.get("ticket_code")]
    payload = {
        "run_id": run_id, "dataset_id": ds, "period": label, "date_from": d1, "date_to": d2,
        "requested": label, "trigger_by": trigger, "ticket_mode": "manual", "status": "done",
        "engine": data.get("engine") or "rule",
        "summary": (data.get("summary") or "")[:120],
        "sample": _count_range(ds, d1, d2),
        "negative": _count_range(ds, d1, d2, negative=True),
        "topics": json.dumps(topics, ensure_ascii=False),
        "tickets": len(codes), "ticket_codes": json.dumps(codes, ensure_ascii=False),
        "error": "", "created_at": started,
        "finished_at": datetime.now().strftime(TS_FMT),
    }
    _save(payload)
    out = _row(_find(ds, label), with_topics=True)
    out["skipped"] = False
    out["window"] = {"period": label, "prev_period": range_label(p1, p2),
                     "date_from": d1, "date_to": d2, "prev_from": p1, "prev_to": p2}
    return out


def preview_range(date_from=None, date_to=None, dataset_id=None, use_ai=True):
    """部门账号的区间研判：就地算一遍，**不落库**。

    和 `run()` 只差一件事——不写 risk_runs。差别必须存在，原因有两条：

    1. 全校快照由学校侧统一出具。部门跑的那一段只含归口本部门的主题，
       写进同一张表就会与全校快照抢 `(tenant, dataset, period)` 唯一键：
       要么把管理员刚跑的那份覆盖成"只有后勤"的版本，要么让同一段出现两份
       口径不同、标题却一模一样的记录——两种结果都没法在页面上解释。
    2. 部门不该有"一份带时间戳的正式结论"。它是这条链上的办理方，不是研判的发布方。

    返回结构与 `run()` 尽量一致（date_from/date_to/period/window/sample/negative/
    topics/counts），前端才能用同一段渲染代码——两套结构会长成两种页面。
    """
    departments._ensure_view()
    if not db.tid():
        raise Forbidden("没有租户上下文，无法执行区间研判")

    ds = str(dataset_id or config.MONTHLY_DATASET).strip()
    if not db.get_dataset(ds):
        raise TicketError(f"数据集不存在：{ds}", 400, "invalid_filter")

    d1, d2 = resolve_range(date_from, date_to)
    label = range_label(d1, d2)
    if not _count_range(ds, d1, d2):
        span = _dataset_span(ds) or {}
        hint = (f"该数据集可用范围 {span.get('from')} ~ {span.get('to')}" if span
                else "该数据集没有可用日期")
        raise TicketError(f"{d1} ~ {d2} 没有可研判的评论（{hint}），请调整起止日期",
                          409, "no_data", {"dataset_span": span})

    p1, p2 = previous_range(d1, d2)
    span_word = ("本月", "上月") if is_full_month(d1, d2) else ("本期", "上期")
    data = departments.assess(ds, use_ai=use_ai,
                              window={"period": label, "date_from": d1, "date_to": d2,
                                      "prev_from": p1, "prev_to": p2, "span": span_word})
    topics = data.get("topics") or []
    # 顺手挂上这段已有的单号：部门点开看到的是"这主题学校侧已经开过单"，
    # 而不是一个他点不动的「建单」按钮。
    for t in topics:
        _attach_if_exists(t, d1, d2)
    return {
        "preview": True,
        "dataset_id": ds, "period": label,
        "date_from": d1, "date_to": d2,
        "trigger": "preview", "status": "done",
        "window": {"period": label, "prev_period": range_label(p1, p2),
                   "date_from": d1, "date_to": d2, "prev_from": p1, "prev_to": p2},
        "sample": _count_range(ds, d1, d2),
        "negative": _count_range(ds, d1, d2, negative=True),
        "engine": data.get("engine") or "rule",
        "summary": (data.get("summary") or "")[:120],
        "topics": topics,
        "counts": _counts(topics),
        "tickets": len([t for t in topics if t.get("ticket_code")]),
        "ticket_codes": [t["ticket_code"] for t in topics if t.get("ticket_code")],
        "scoped": data.get("scoped"),
        "finished_at": datetime.now().strftime(TS_FMT),
    }


def create_tickets(run_id, dimensions, note=""):
    """按人在页面上点的主题建单。

    **必须指定 `dimensions`，不接受"全部"**：这里没有门槛，一旦允许省略，
    就等于把"该不该开单"的判断又挪回后端——一次给八个主题各开一张，
    工单页第二天就没人看了。点哪行建哪张，是这套设计的一部分。

    同一段区间重复点为同一主题会被 rule_id 去重挡下（Conflict），
    这时把已有单号挂回快照，页面上那一行就从「建单」变成单号。

    `note` 是建单人在弹窗里补的备注，原样进摘要末尾。它只属于这一次建单，
    不写回快照——下次为同一主题重新建单时，上一次现场情况留在上一张单里。
    """
    if not tickets.can_create():
        raise Forbidden("当前角色无权建单（需要分析员或管理员）")
    want = {str(d).strip() for d in (dimensions or []) if str(d).strip()}
    if not want:
        raise TicketError("请指定要建单的主题", 400, "invalid_dimension")
    note = str(note or "").strip()[:200]

    run_detail = get_run(run_id)
    topics = run_detail.get("topics") or []
    known = {t["dimension"] for t in topics}
    made, failed = [], []
    for t in topics:
        if t["dimension"] not in want:
            continue
        if t.get("ticket_code"):
            failed.append({"dimension": t["dimension"], "code": t["ticket_code"],
                           "error": t.get("ticket_note") or "该主题已有工单"})
            continue
        try:
            tk = _make_ticket(t, run_detail, run_detail["period"], note=note)
            t["ticket_code"] = tk["code"]
            t["ticket_note"] = "已建单（页面手动）"
            # 重新建成之后，"上一张单已结案"这条旧提示就该消失，
            # 否则快照里同时留着"已结案"和"已建单"两个互相矛盾的标记。
            t.pop("ticket_closed", None)
            made.append({"dimension": t["dimension"], "code": tk["code"],
                         "ticket_id": tk["ticket_id"]})
        except Conflict as e:
            _attach_existing(t, e)
            failed.append({"dimension": t["dimension"], "error": str(e)[:60],
                           "code": t.get("ticket_code")})
        except Exception as e:
            t["ticket_note"] = f"建单失败：{str(e)[:40]}"
            failed.append({"dimension": t["dimension"], "error": str(e)[:60]})
    for d in want - known:
        failed.append({"dimension": d, "error": "这次研判里没有这个主题"})

    if made or failed:
        codes = [t["ticket_code"] for t in topics if t.get("ticket_code")]
        conn = db.get_conn()
        conn.execute("UPDATE risk_runs SET topics = ?, tickets = ?, ticket_codes = ? "
                     "WHERE tenant_id = ? AND run_id = ?",
                     (json.dumps(topics, ensure_ascii=False), len(codes),
                      json.dumps(codes, ensure_ascii=False), db.tid(), run_id))
        conn.commit()
    out = get_run(run_id)
    out.update({"created": made, "failed": failed})
    return out


# ============================ 定时器 ============================
# 为什么用进程内线程而不是系统 cron：
# 项目要保住"双击 start.bat 就能跑"这个交付方式。要求评委或老师去配计划任务，
# 这个功能就等于没做。线程只做一件事——每小时问一次"上个月的研判跑了没"，
# 便宜到没有理由做成精确到秒的调度器。
#
# 代价要说清楚：进程不跑就不会触发。所以页面上永远留一个「立即研判」按钮，
# 定时失败（比如机器那几天没开）也能一键补上。
_SCHEDULER = {"state": "stopped", "thread": None, "last_check": None, "last_result": ""}
_lock = threading.Lock()


def _tick(period=None, dataset_id=None):
    """一轮检查：上个月没研判过就跑一次。返回人话，方便日志里看懂。"""
    period = period or last_closed_period()
    ds = dataset_id or config.MONTHLY_DATASET
    _SCHEDULER["last_check"] = datetime.now().strftime(TS_FMT)
    old = _find(ds, period)
    if old and old["status"] == "done":
        return f"{period} 已研判，跳过"
    if not db.get_dataset(ds):
        return f"数据集 {ds} 不存在，跳过"
    # 数据集里没有这个月的数据就等：自动回落到"最新月份"在手动模式下是对的
    # （人看着结果知道自己在看哪个月），但定时任务不该每月自动去翻炒旧数据。
    d1, d2 = month_window(period)
    if not _count_range(ds, d1, d2):
        return f"{period} 无评论数据，等待新数据导入"
    out = run(period=period, dataset_id=ds, trigger="schedule")
    return (f"{period} 研判完成：{out.get('sample', 0)} 条评论、"
            f"{len(out.get('topics') or [])} 个主题，等人工决定是否建单")


def _loop(quiet):
    time.sleep(20)                    # 让预热先跑完，别在启动瞬间抢 CPU
    while True:
        try:
            token = tenancy.set_context(tenancy.system_context())
            try:
                msg = _tick()
            finally:
                tenancy.reset_context(token)
            _SCHEDULER["last_result"] = msg
            if not quiet:
                print(f"  定期研判 · {msg}", flush=True)
        except Exception as e:
            _SCHEDULER["last_result"] = f"异常：{e}"[:160]
            print(f"  定期研判调度异常（不影响服务）: {e}", flush=True)
        time.sleep(max(300, config.MONTHLY_CHECK_MINUTES * 60))


def start_scheduler(quiet=False):
    """启动后台线程。重复调用只会有一个线程在跑。"""
    if not config.CP_MONTHLY:
        _SCHEDULER["state"] = "disabled"
        if not quiet:
            print("  定期研判定时器：未启用（CP_MONTHLY=0）", flush=True)
        return None
    with _lock:
        th = _SCHEDULER.get("thread")
        if th and th.is_alive():
            return th
        th = threading.Thread(target=_loop, args=(quiet,), name="cp-monthly", daemon=True)
        th.start()
        _SCHEDULER["thread"] = th
        _SCHEDULER["state"] = "running"
        if not quiet:
            print(f"  定期研判定时器：已启动（每 {config.MONTHLY_CHECK_MINUTES} 分钟检查一次"
                  f" · 目标数据集 {config.MONTHLY_DATASET}）", flush=True)
        return th


def tick_once(period=None, dataset_id=None):
    """给工具脚本 / 测试用：立刻检查一轮（不启动线程）。"""
    token = tenancy.set_context(tenancy.system_context())
    try:
        return _tick(period, dataset_id)
    finally:
        tenancy.reset_context(token)
