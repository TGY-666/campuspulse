# -*- coding: utf-8 -*-
"""学校部门：舆情归口、风险研判、整改建议。

这一层要回答的问题只有一个：**这批负面评论，该校哪个部门该管、急到什么程度、
第一步做什么。** 三件事是它的全部内容，其余都是包装。

**一、风险百分比必须能当场复算。**
   页面上的 72% 不能是一个"看起来挺高"的数字。这里把它拆成四项可核对的量
   （量级 / 负面率 / 增速 / 互动），每项都写清分子分母与封顶规则，
   `parts` 字段原样返回给前端展示。答辩时被问"这个百分比怎么来的"，
   要能指着某一项说"这块是 34/60，那块是 12.3%/35%"。

**二、归口判断先规则、后模型。**
   部门表里配了 `dimensions`（一级归属：一个主题由一个部门主责）和
   `keywords`（同主题多部门时的细分依据）。规则命中就直接出结果；
   模型只用来在两三个候选部门之间做选择、并把建议写成人话。
   模型给的 dept_id 必须存在于候选集合里，否则整条丢弃回落到规则——
   让模型自由发挥部门名，会造出系统里根本不存在的责任主体。

**三、只建议，不建单。**
   和 `tickets.suggestions()` 同一条纪律：自动建单把"机器定性"的嫌疑引到作品上。
   这里只产出建议与证据，工单由人点一下才生成。
"""
import json
import re
import secrets
import threading
from datetime import date, datetime, timedelta

from . import analytics, config, db, reports, tenancy, tickets
from .tickets import TS_FMT, TicketError, Forbidden, _anchor, can_create

# ============================ 风险口径 ============================
# 权重和为 1。改这里等于改口径，页面上会跟着变，必须同步 README 与技术文档中的风险公式。
RISK_WEIGHTS = {"量级": 0.35, "负面率": 0.30, "增速": 0.15, "互动": 0.20}

NEG_FULL = 60          # 负面 60 条视作量级满分
RATE_FULL = 0.35       # 该主题负面占比 35% 视作满分
LIKE_FULL = 200        # 负面平均赞 200 视作互动满分
CONF_FULL = 30         # 该主题 30 条视作样本置信满分
GROWTH_FULL = 2.0      # 近 30 天是前 30 天的 2 倍视作增速满分

LEVELS = [(70, "高"), (50, "中高"), (30, "中"), (0, "低")]

# 风险等级 → 建议优先级。P0 只留给"高风险 + 量级已经很大"，
# 否则一开页就是一片 P0，优先级这个字段就失去意义了。
def priority_for(risk, neg):
    if risk >= 85 and neg >= 60:
        return "P0"
    if risk >= 70:
        return "P1"
    if risk >= 50:
        return "P2"
    return "P3"


DISCLAIMER = ("部门与负责人为示例配置，可在本页修改或替换为学校实际分工；"
              "风险指数（0–100）由公开评论的统计特征加权算出，不是负面占比，"
              "也不代表对言论真实性的认定。")

_LOCK = threading.Lock()


# ============================ 权限 ============================
def _role():
    return (tenancy.current() or {}).get("role") or ""


def can_view():
    """能进「部门与风险研判」页。

    部门账号没有 view_analysis（它不该进驾驶舱/洞察/数据页），但必须能进这一页——
    否则导航里就只剩一个孤零零的「处置工单」，看不到"这事为什么归我"。
    所以这里单独放行 dept_scope。
    """
    role = _role()
    return tenancy.has_perm(role, "view_analysis") or tenancy.has_perm(role, "dept_scope")


def can_edit():
    """部门是配置数据，权限对齐用户管理（仅管理员）。"""
    return tenancy.has_perm(_role(), "user_admin")


def _ensure_view():
    if not can_view():
        raise Forbidden("当前角色无权查看部门与风险研判")


def _scope_info(dept_id, depts):
    """给前端的范围说明。None = 全校视角（管理员/分析员/只读）。"""
    if not tenancy.is_dept_role():
        return None
    ctx = tenancy.current()
    name = ctx.get("department_name") or (depts[0]["name"] if depts else "")
    return {"dept_id": dept_id or "", "dept_name": name or "",
            "linked": bool(dept_id and depts)}


def _ensure_edit():
    if not can_edit():
        raise Forbidden("只有管理员可以维护部门配置")


# ============================ 默认部门 ============================
# 只保留三类最常被点名的责任主体：后勤（住宿 + 环境）、饮食、招生。
# 后勤合并成一个部门：真实学校里住宿与校园环境都归后勤保障处，
# 拆成两个会让人在同一批负面里要挑两次归口，答辩时反而讲不清。
# 负责人一律用「姓 + 老师」：示例配置里不该出现看着像真人的全名，
# 否则对外演示时会被当成真实通讯录。职务与联系方式同理，写成可编辑的示例值。
SEED = [
    {
        "name": "后勤保障处",
        "duties": "学生宿舍分配与调整、宿舍设施维修、水电与门禁、洗衣机与热水供应、住宿费与水电费的公示解释、"
                  "宿舍安全卫生检查；校园绿化与保洁、施工与噪声管理、路灯与公共设施维修、蚊虫消杀、垃圾分类。",
        "owner_name": "陈老师", "owner_title": "后勤保障处副处长", "contact": "0757-8668xxxx 转 2101",
        "dimensions": ["宿舍住宿", "校园环境"],
        "keywords": ["宿舍", "床位", "空调", "热水", "门禁", "洗衣机", "维修", "电费", "水费",
                     "熄灯", "断电", "蚊帐", "上床下桌", "独立卫浴", "住宿费", "调宿",
                     "环境", "绿化", "施工", "噪声", "吵", "灰尘", "卫生", "垃圾", "蚊虫",
                     "路灯", "设施", "道路", "积水", "校园面积", "图书馆", "网络"],
        "sla_hours": 48, "sort_order": 10,
    },
    {
        "name": "饮食服务中心",
        "duties": "食堂窗口与档口管理、菜品价格与卫生、食材采购与留样、夜宵与节假日供餐、校园超市与商铺的物价监督。",
        "owner_name": "黄老师", "owner_title": "饮食服务中心主任", "contact": "0757-8668xxxx 转 2205",
        "dimensions": ["食堂餐饮"],
        "keywords": ["食堂", "饭菜", "窗口", "菜价", "涨价", "口味", "卫生", "异物", "夜宵",
                     "早餐", "套餐", "超市", "物价", "饭堂", "打饭"],
        "sla_hours": 24, "sort_order": 20,
    },
    {
        "name": "招生就业处（招生办）",
        "duties": "招生计划与录取、分数线与专业介绍咨询、学费与奖助政策解释、新生报到与入学资格审查、招生宣传口径统一。",
        "owner_name": "林老师", "owner_title": "招生办主任", "contact": "0757-8668xxxx 转 1008",
        "dimensions": ["招生报考"],
        "keywords": ["招生", "录取", "分数线", "志愿", "专业", "学费", "计划", "咨询",
                     "报考", "调剂", "新生", "报到", "专升本", "免试"],
        "sla_hours": 24, "sort_order": 30,
    },
]


def _now():
    return datetime.now().strftime(TS_FMT)


def _json_list(value):
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    if not value:
        return []
    try:
        data = json.loads(value)
        return [str(x).strip() for x in data if str(x).strip()] if isinstance(data, list) else []
    except (ValueError, TypeError):
        return [s.strip() for s in re.split(r"[,，、\s]+", str(value)) if s.strip()]


def _row(r):
    return {
        "dept_id": r["dept_id"], "name": r["name"], "duties": r["duties"] or "",
        "owner_name": r["owner_name"] or "", "owner_title": r["owner_title"] or "",
        "contact": r["contact"] or "",
        "dimensions": _json_list(r["dimensions"]), "keywords": _json_list(r["keywords"]),
        "sla_hours": r["sla_hours"], "status": r["status"],
        "sort_order": r["sort_order"], "is_seed": bool(r["is_seed"]),
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def list_all(include_archived=False, scoped=True):
    """部门清单。

    scoped=True（默认）时按当前身份收窄：部门账号只看得到自己那一个部门。
    研判函数内部要拿**全量**部门做归口匹配（scoped=False）——当归口判断只能看见
    自己一个候选时，所有别的主题都会算成"未匹配到部门"，研判结论就全错了。
    """
    _ensure_view()
    sql = ("SELECT * FROM departments WHERE tenant_id = ?"
           + ("" if include_archived else " AND status = 'active'")
           + " ORDER BY sort_order ASC, name ASC")
    rows = db.query_all(sql, (db.tid(),))
    dept_id = tenancy.department_id()
    if scoped and tenancy.is_dept_role():
        rows = [r for r in rows if dept_id and r["dept_id"] == dept_id]
    return [_row(r) for r in rows]


def get(dept_id, include_archived=True):
    sql = "SELECT * FROM departments WHERE tenant_id = ? AND dept_id = ?"
    r = db.query_one(sql, (db.tid(), dept_id))
    if not r or (not include_archived and r["status"] != "active"):
        return None
    return _row(r)


def _clean(body, partial=False):
    """校验并归一化。partial=True 时只处理出现的字段（更新用）。"""
    out = {}
    if not partial or "name" in body:
        name = str(body.get("name") or "").strip()
        if not name:
            raise TicketError("请填写部门名称", 400, "invalid_department")
        if len(name) > 40:
            raise TicketError("部门名称不能超过 40 字", 400, "invalid_department")
        out["name"] = name
    if not partial or "duties" in body:
        duties = str(body.get("duties") or "").strip()
        if len(duties) > 500:
            raise TicketError("主要职责不能超过 500 字", 400, "invalid_department")
        out["duties"] = duties
    for key, limit, label in (("owner_name", 30, "负责人"), ("owner_title", 30, "职务"),
                              ("contact", 60, "联系方式")):
        if not partial or key in body:
            out[key] = str(body.get(key) or "").strip()[:limit]
    if not partial or "dimensions" in body:
        dims = _json_list(body.get("dimensions"))
        unknown = [d for d in dims if d not in config.DIMENSIONS]
        if unknown:
            raise TicketError(f"未知主题：{'、'.join(unknown)}", 400, "invalid_department")
        out["dimensions"] = dims
    if not partial or "keywords" in body:
        out["keywords"] = _json_list(body.get("keywords"))[:30]
    if not partial or "sla_hours" in body:
        try:
            h = int(body.get("sla_hours") or 0) or None
        except (TypeError, ValueError):
            raise TicketError("期望响应时长必须是数字（工作小时）", 400, "invalid_department")
        if h is not None and not (1 <= h <= 240):
            raise TicketError("期望响应时长应在 1~240 工作小时之间", 400, "invalid_department")
        out["sla_hours"] = h
    if not partial or "sort_order" in body:
        try:
            out["sort_order"] = int(body.get("sort_order") or 0)
        except (TypeError, ValueError):
            out["sort_order"] = 0
    return out


def create(body):
    _ensure_edit()
    data = _clean(body)
    dup = db.query_one("SELECT dept_id FROM departments WHERE tenant_id = ? AND name = ?",
                       (db.tid(), data["name"]))
    if dup:
        raise TicketError(f"已存在同名部门「{data['name']}」", 409, "conflict")
    dept_id = "dept_" + secrets.token_hex(6)
    ts = _now()
    conn = db.get_conn()
    conn.execute(
        """INSERT INTO departments (dept_id, tenant_id, name, duties, owner_name, owner_title,
                                    contact, dimensions, keywords, sla_hours, status,
                                    sort_order, is_seed, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,'active',?,0,?,?)""",
        (dept_id, db.tid(), data["name"], data.get("duties", ""),
         data.get("owner_name", ""), data.get("owner_title", ""), data.get("contact", ""),
         json.dumps(data.get("dimensions", []), ensure_ascii=False),
         json.dumps(data.get("keywords", []), ensure_ascii=False),
         data.get("sla_hours"), data.get("sort_order", 0), ts, ts))
    conn.commit()
    return get(dept_id)


def update(dept_id, body):
    _ensure_edit()
    old = get(dept_id)
    if not old:
        raise TicketError("部门不存在", 404, "not_found")
    data = _clean(body, partial=True)
    if "name" in data and data["name"] != old["name"]:
        dup = db.query_one(
            "SELECT dept_id FROM departments WHERE tenant_id = ? AND name = ? AND dept_id <> ?",
            (db.tid(), data["name"], dept_id))
        if dup:
            raise TicketError(f"已存在同名部门「{data['name']}」", 409, "conflict")
    sets, params = [], []
    for key, value in data.items():
        if key in ("dimensions", "keywords"):
            value = json.dumps(value, ensure_ascii=False)
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return old
    sets.append("updated_at = ?")
    params += [_now(), db.tid(), dept_id]
    conn = db.get_conn()
    conn.execute(f"UPDATE departments SET {', '.join(sets)} WHERE tenant_id = ? AND dept_id = ?",
                 params)
    conn.commit()
    return get(dept_id)


def archive(dept_id):
    """软删。历史工单存了名称快照，但研判记录与审计还要能指向这个部门。"""
    _ensure_edit()
    if not get(dept_id):
        raise TicketError("部门不存在", 404, "not_found")
    conn = db.get_conn()
    conn.execute("UPDATE departments SET status = 'archived', updated_at = ? "
                 "WHERE tenant_id = ? AND dept_id = ?", (_now(), db.tid(), dept_id))
    conn.commit()
    return {"dept_id": dept_id, "archived": True}


def seed_defaults(tenant=None):
    """首次启动写入示例部门。已存在任何部门（含归档）就不动，避免覆盖用户改过的配置。

    显式接租户参数，是因为它在 `db.init_db()` 里被调用——那个时刻没有请求上下文，
    `db.tid()` 取不到身份，写进去的部门会归属为空、任何租户都读不到。
    """
    tid = tenant or db.tid()
    if not tid:
        return 0
    with _LOCK:
        n = db.scalar("SELECT COUNT(*) FROM departments WHERE tenant_id = ?", (tid,)) or 0
        if n:
            return 0
        ts = _now()
        conn = db.get_conn()
        for item in SEED:
            conn.execute(
                """INSERT OR IGNORE INTO departments
                   (dept_id, tenant_id, name, duties, owner_name, owner_title, contact,
                    dimensions, keywords, sla_hours, status, sort_order, is_seed,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'active',?,1,?,?)""",
                ("dept_" + secrets.token_hex(6), tid, item["name"], item["duties"],
                 item["owner_name"], item["owner_title"], item["contact"],
                 json.dumps(item["dimensions"], ensure_ascii=False),
                 json.dumps(item["keywords"], ensure_ascii=False),
                 item["sla_hours"], item["sort_order"], ts, ts))
        conn.commit()
        return len(SEED)


# ============================ 风险研判 ============================
def _reason_rows(f):
    """"到底在骂什么"：负面归因按主题取。两条聚合路径共用这一份查询。"""
    where, params = analytics._where(f)
    return db.query_all(
        f"""SELECT j.value dim, c.negative_reason k, COUNT(*) n
            FROM comments c, json_each(c.dimensions) j
            WHERE {where} AND c.sentiment = 'negative' AND c.negative_reason <> ''
            GROUP BY j.value, c.negative_reason ORDER BY n DESC""", params)


def _merge_reasons(out, rows):
    """每个主题只留 top3 归因——这是"在骂什么"的最小可读单位。"""
    for r in rows:
        bucket = out.setdefault(r["dim"], {"dim": r["dim"], "n": 0, "neg": 0})
        bucket.setdefault("reasons", [])
        if len(bucket["reasons"]) < 3:
            bucket["reasons"].append({"word": r["k"], "n": r["n"]})


def _topic_stats(f, anchor):
    """按主题聚合。一次查询拿全，避免八个主题跑八遍。"""
    where, params = analytics._where(f)
    d = lambda n: (anchor - timedelta(days=n)).isoformat()   # noqa: E731
    recent_from, prev_from = d(30), d(60)

    rows = db.query_all(
        f"""SELECT j.value dim,
                   COUNT(*) n,
                   SUM(CASE WHEN c.sentiment = 'negative' THEN 1 ELSE 0 END) neg,
                   SUM(CASE WHEN c.sentiment = 'negative' AND c.comment_date >= ? THEN 1 ELSE 0 END) neg_recent,
                   SUM(CASE WHEN c.sentiment = 'negative' AND c.comment_date >= ?
                             AND c.comment_date < ? THEN 1 ELSE 0 END) neg_prev,
                   AVG(CASE WHEN c.sentiment = 'negative' THEN c.like_count END) avg_like,
                   MAX(CASE WHEN c.sentiment = 'negative' THEN c.like_count END) max_like
            FROM comments c, json_each(c.dimensions) j
            WHERE {where}
            GROUP BY j.value""",
        [recent_from, prev_from, recent_from] + params)

    out = {r["dim"]: dict(r) for r in rows}
    _merge_reasons(out, _reason_rows(f))
    return out


def _topic_stats_window(f, prev_f):
    """按给定区间聚合（定期研判口径）。

    `f` 已经限定在当月，所以 n / neg / avg_like 天然都是当月数——
    这正是月度口径与实时口径的区别：实时口径的 n 是全时段累计，只有增速项看近 30 天。

    增速项需要上一个月的负面数，必须**另开一次查询**：把上月日期塞进 `f`
    会同时把当月统计也放开（一个 where 只有一个日期区间），
    结果就变成"本月=上月"的假增速。这里宁可多一次查询。
    """
    where, params = analytics._where(f)
    rows = db.query_all(
        f"""SELECT j.value dim,
                   COUNT(*) n,
                   SUM(CASE WHEN c.sentiment = 'negative' THEN 1 ELSE 0 END) neg,
                   AVG(CASE WHEN c.sentiment = 'negative' THEN c.like_count END) avg_like,
                   MAX(CASE WHEN c.sentiment = 'negative' THEN c.like_count END) max_like
            FROM comments c, json_each(c.dimensions) j
            WHERE {where}
            GROUP BY j.value""", params)
    out = {r["dim"]: dict(r) for r in rows}

    pwhere, pparams = analytics._where(prev_f)
    prev_rows = db.query_all(
        f"""SELECT j.value dim, COUNT(*) n
            FROM comments c, json_each(c.dimensions) j
            WHERE {pwhere} AND c.sentiment = 'negative'
            GROUP BY j.value""", pparams)
    prev = {r["dim"]: r["n"] for r in prev_rows}
    for dim, st in out.items():
        # 本月负面就是"近段"，上月负面是"前段"，_risk 里的增速项直接读这两个值
        st["neg_recent"] = st.get("neg") or 0
        st["neg_prev"] = prev.get(dim, 0)

    _merge_reasons(out, _reason_rows(f))
    return out


def _samples(f, dim, limit=2, date_from=None, date_to=None):
    """代表证据：负面里点赞最高的两三条。**按正文去重**。

    同一条评论可能被两个平台各采到一次（comment_ref 不同、正文一字不差）。
    详情里并排出现两段一模一样的字，读者会先怀疑"去重没做"，
    而不是相信这条证据——所以多取几倍，再按去空白后的正文判重。

    **必须限定在研判窗口内**（第 20 轮复评）：研判说的是 2026-06-07~07-07，
    证据却是 2023 年的两条，读者立刻会问"7 月的判断为什么用 2023 年的评论"。
    窗口内确实没有负面样本时（区间研判常见），调用方会拿到空列表并按
    `samples_outside` 标注"窗口内无样本，以下为全量最高赞"。
    """
    q = dict(f)
    q["dimensions"] = [dim]
    if date_from:
        q["date_from"] = date_from
    if date_to:
        q["date_to"] = date_to
    where, params = analytics._where(q)
    rows = db.query_all(
        f"""SELECT c.comment_ref, c.content_masked, c.comment_date, c.platform,
                   c.like_count, c.negative_reason
            FROM comments c
            WHERE {where} AND c.sentiment = 'negative'
            ORDER BY COALESCE(c.like_count, -1) DESC, c.comment_date DESC LIMIT ?""",
        params + [limit * 6])
    out, seen = [], set()
    for r in rows:
        snippet = (r["content_masked"] or "").strip()
        key = "".join(snippet.split())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"comment_ref": r["comment_ref"], "snippet": snippet[:100],
                    "date": r["comment_date"], "platform": r["platform"],
                    "like_count": r["like_count"], "reason": r["negative_reason"] or ""})
        if len(out) >= limit:
            break
    return out


def _risk(st, span=("近 30 天", "前 30 天")):
    """风险百分比。每一步都留了可核对的中间值，返回给前端原样展示。

    `span` 只影响**文案**：同一套权重在两种口径下都成立，
    但定期研判的"近段/前段"是自选区间，硬写"近 30 天"会让人以为算错了。
    """
    n = st.get("n") or 0
    neg = st.get("neg") or 0
    recent = st.get("neg_recent") or 0
    prev = st.get("neg_prev") or 0
    avg_like = st.get("avg_like") or 0

    neg_scale = min(1.0, neg / NEG_FULL)
    rate = (neg / n) if n else 0.0
    rate_norm = min(1.0, rate / RATE_FULL)
    # 增速项要区分"没增长"和"没法判断"。
    # 演示数据是历史快照，近 30 天常常只有个位数负面——这时把增速记 0 分，
    # 等于用"缺数据"去惩罚每一个主题，所有人的风险都被系统性压低 15%。
    # 所以样本不足时这一项直接退出计算，其余权重归一化补上。
    growth_sample = recent + prev
    growth_ok = growth_sample >= 3
    if growth_ok:
        if prev:
            growth = recent / prev
            growth_norm = min(1.0, max(0.0, growth - 1))
        else:
            growth = GROWTH_FULL if recent else 0.0
            growth_norm = 1.0 if recent else 0.0
    else:
        growth, growth_norm = None, None
    heat = min(1.0, avg_like / LIKE_FULL)
    conf = min(1.0, n / CONF_FULL)

    items = [(RISK_WEIGHTS["量级"], neg_scale), (RISK_WEIGHTS["负面率"], rate_norm),
             (RISK_WEIGHTS["互动"], heat)]
    if growth_ok:
        items.append((RISK_WEIGHTS["增速"], growth_norm))
    w_sum = sum(w for w, _ in items)
    raw = sum(w * v for w, v in items) / (w_sum or 1)
    score = round(100 * raw * (0.55 + 0.45 * conf))

    parts = [
        {"name": "量级", "weight": RISK_WEIGHTS["量级"], "value": round(neg_scale, 3),
         "label": f"负面 {neg} / {NEG_FULL} 条封顶"},
        {"name": "负面率", "weight": RISK_WEIGHTS["负面率"], "value": round(rate_norm, 3),
         "label": f"该主题负面占 {round(rate * 100, 1)}% / {round(RATE_FULL * 100)}% 封顶"},
        {"name": "增速", "weight": RISK_WEIGHTS["增速"],
         "value": round(growth_norm, 3) if growth_ok else None,
         "label": (f"{span[0]} {recent} 条 vs {span[1]} {prev} 条" if growth_ok
                   else f"{span[0]}与{span[1]}合计负面仅 {growth_sample} 条，样本不足，本项不计分")},
        {"name": "互动", "weight": RISK_WEIGHTS["互动"], "value": round(heat, 3),
         "label": f"负面平均赞 {round(avg_like, 1)} / {LIKE_FULL} 封顶"},
    ]
    return {"score": min(100, score), "parts": parts, "confidence": round(conf, 2),
            "neg": neg, "n": n, "neg_rate": round(rate * 100, 1),
            "growth": round(growth, 2) if growth_ok and growth is not None else None,
            "growth_ok": growth_ok}


def _level(score):
    for threshold, label in LEVELS:
        if score >= threshold:
            return label
    return "低"


def _match(dim, st, depts):
    """归口打分。主题命中是主依据，关键词只用于同主题多部门时的细分。

    只在**认领了该主题**的部门里选。以前允许"纯关键词命中"也算数，
    后果是没配主责部门的主题会被某个顺带提到「宿舍」「学费」的部门抢走——
    教学学业归到后勤保障处这种结果，一眼就是错的。宁可显示"未匹配到部门"，
    也不要编一个不该负责的主体出来。
    """
    cands = [d for d in depts if d["status"] == "active" and dim in d["dimensions"]]
    if not cands:
        return None, []
    text = " ".join([r["word"] for r in (st.get("reasons") or [])])
    scored = []
    for order, dept in enumerate(cands):
        kw_hit = [k for k in dept["keywords"] if k and (k in dim or k in text)]
        scored.append((min(3, len(kw_hit)), -order, dept, True, kw_hit))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    best = scored[0]
    return best[2], best[4]


def _rule_advice(dim, st, risk, dept, hits):
    """规则版建议：每条都指向一个能立刻做的动作，不写"加强管理"这种空话。"""
    reasons = st.get("reasons") or []
    top = reasons[0]["word"] if reasons else ""
    out = []
    if top:
        out.append(f"核对「{top}」相关表述与现行规定/公示是否一致"
                   f"（该归因近 {reasons[0]['n']} 条负面），先确认事实再决定回应口径。")
    if risk["neg_rate"] >= 25:
        out.append(f"该主题负面占比 {risk['neg_rate']}%，属集中吐槽，"
                   "建议整理一份统一答复口径，避免同问题在不同平台各说一套。")
    if risk.get("growth") and risk["growth"] >= 1.5:
        out.append("本期负面较上期明显上升，建议先查是否为同一事件/同一批账号放大。")
    if st.get("max_like") and st["max_like"] >= 200:
        out.append(f"单条负面最高点赞 {st['max_like']}，传播面较广，建议优先核实该条描述是否属实。")
    if dept:
        out.append(f"由{dept['name']}牵头核实并回复"
                   f"（负责人 {dept['owner_name'] or '未填'}），"
                   f"期望 {dept['sla_hours'] or 48} 个工作小时内给出处置记录。")
    if not out:
        out.append("该主题负面量级较低，可纳入常规巡查，暂不必单独建单。")
    return out


def _ai_brief(facts, depts):
    """让模型在两个候选部门之间做选择并把建议写成人话。

    输入刻意压到最小：部门只给 id/name/duties/keywords，主题只给统计与两条样本。
    给多了模型会开始编数字——本项目所有数字都必须来自统计层。
    """
    prompt = f"""你是高校舆情处置的研判助手。下面给出若干舆情主题的统计事实，
以及学校现有部门及其职责。请为每个主题指定一个归口部门，并给整改建议。

硬性约束：
1. dept_id 必须从「部门清单」里选，不得编造；无法判断时给 null。
2. 不得新增任何数字，只能用-input 里已有的数字。
3. 建议必须是可执行动作（谁、做什么、先做什么），不要写"加强管理""高度重视"。
4. 每个主题给 2-3 条建议，每条不超过 60 字。
5. {config.TERM_RULE}

事实：
{json.dumps(facts, ensure_ascii=False)}

部门清单：
{json.dumps(depts, ensure_ascii=False)}

只输出 JSON：
{{"topics":[{{"dimension":"主题名","dept_id":"部门id或null","reason":"为什么归它，40字内","advice":["建议1","建议2"]}}],
  "summary":"一句话总览，40字内"}}"""
    raw = reports._llm_json(prompt, timeout=config.LLM_TIMEOUT + 20, max_tokens=1400)
    return raw if isinstance(raw, dict) else {}


def assess(dataset_id=None, use_ai=True, window=None):
    """主题风险研判：风险% → 归口部门 → 建议。不写库，不建单。

    `window` 是两种口径的分水岭：

    - **不给**：实时口径。锚点取数据集里最新一天，统计全时段，
      增速项拿近 30 天对比前 30 天。页面上「重新研判」走这条路。
    - **给了**：定区间口径。四段日期由调用方算好传进来（`monthly` 算窗口与上期）：
      `{"date_from","date_to","prev_from","prev_to","period"}`。
      此时 n / 负面 / 互动都只统计区间内，增速对比相邻区间。

    分成两套统计函数而不是一个带一堆 if 的函数：两种口径的"分母"根本不同，
    混在一起以后没人能说清页面上那个百分比到底是哪批评论算出来的。
    """
    _ensure_view()
    if window:
        try:
            f = analytics.build_filters(dataset_id,
                                        date_from=window.get("date_from"),
                                        date_to=window.get("date_to"))
        except analytics.FilterError as e:
            raise TicketError(str(e), 400, "invalid_filter")
        prev_f = dict(f)
        prev_f["date_from"] = window.get("prev_from")
        prev_f["date_to"] = window.get("prev_to")
        anchor = date.fromisoformat(f["date_to"]) if f["date_to"] else None
    else:
        try:
            f = analytics.build_filters(dataset_id)
        except analytics.FilterError as e:
            raise TicketError(str(e), 400, "invalid_filter")
        anchor = _anchor(f)

    # 归口匹配必须拿全量部门：只给本部门一个候选的话，别的主题会全部落成
    # "未匹配到部门"，而它们本来就该归别人——那不是数据问题，是视野问题。
    depts_all = list_all(scoped=False)
    my_dept = tenancy.department_id() if tenancy.is_dept_role() else ""
    scoped_depts = [d for d in depts_all if d["dept_id"] == my_dept] if my_dept else []
    depts = scoped_depts if tenancy.is_dept_role() else depts_all

    if not anchor:
        return {"dataset_id": f["dataset_id"],
                "topics": [], "departments": depts, "engine": "rule", "engine_label": "统计规则",
                "anchor": None, "summary": "", "notice": "当前数据集没有可用日期，无法研判",
                "disclaimer": DISCLAIMER, "weights": RISK_WEIGHTS, "uncovered": [],
                "ai_available": bool(config.DEEPSEEK_API_KEY),
                "mode": "month" if window else "rolling",
                "period": (window or {}).get("period"),
                "can_edit": can_edit(), "can_create": can_create(),
                "tickets_enabled": bool(config.CP_TICKETS),
                "scoped": _scope_info(my_dept, scoped_depts)}

    stats = _topic_stats_window(f, prev_f) if window else _topic_stats(f, anchor)
    # 增速项的说法跟着调用方走：整月叫"本月 vs 上月"，任意区间叫"本期 vs 上期"，
    # 实时口径叫"近 30 天 vs 前 30 天"。权重一个字都不变，只有文案在动——
    # 让人一眼看出这个百分比说的是哪一段时间，比多加一个字段有用。
    if window:
        span = tuple(window.get("span") or ("本期", "上期"))
    else:
        span = ("近 30 天", "前 30 天")
    topics = []
    # 证据窗口与研判窗口同一段：实时口径 = 锚点往前 30 天；区间口径 = 调用方给的起止。
    if window:
        _win_from, _win_to = window.get("date_from"), window.get("date_to")
    elif anchor:
        _win_from = (anchor - timedelta(days=29)).isoformat()
        _win_to = anchor.isoformat()
    else:
        _win_from, _win_to = None, None
    for dim in config.DIMENSIONS:
        st = stats.get(dim)
        if not st or not st.get("n"):
            continue
        risk = _risk(st, span)
        dept, hits = _match(dim, st, depts_all)
        topics.append({
            "dimension": dim,
            "total": st["n"], "negative": risk["neg"],
            "neg_rate": risk["neg_rate"],
            "risk": risk["score"], "level": _level(risk["score"]),
            "parts": risk["parts"], "confidence": risk["confidence"],
            "growth": risk["growth"], "max_like": st.get("max_like") or 0,
            "reasons": st.get("reasons") or [],
            "department": ({"dept_id": dept["dept_id"], "name": dept["name"],
                            "owner_name": dept["owner_name"], "owner_title": dept["owner_title"],
                            "contact": dept["contact"], "sla_hours": dept["sla_hours"],
                            "matched_keywords": hits} if dept else None),
            "priority": priority_for(risk["score"], risk["neg"]),
            "advice": _rule_advice(dim, st, risk, dept, hits),
            "advice_source": "rule",
            # 证据跟着研判窗口走；窗口内一条都没有时回落到全量，并**明确标注**。
            # 两条路都留，是因为"窗口内 9 条负面却一条证据都不给"同样不可信。
            "samples": (_samples(f, dim, date_from=_win_from, date_to=_win_to)
                        if risk["neg"] else []),
        })
    # 窗口内拿不到证据的主题：回落到全量最高赞，但打上标记，页面上要写清楚
    for t in topics:
        t["samples_outside"] = False
        if t["negative"] and not t["samples"]:
            t["samples"] = _samples(f, t["dimension"])
            t["samples_outside"] = bool(t["samples"])

    topics.sort(key=lambda t: -t["risk"])
    _attach_ticket_codes(topics, f["dataset_id"])

    if tenancy.is_dept_role():
        # 部门账号只看归口到自己的主题。按"归口"过滤而不是按"主题"：
        # 一个部门可能认领多个主题（后勤同时管宿舍与环境），而同一个主题
        # 也可能按关键词细分给不同部门——只有归口结果才是"这事归我"的唯一答案。
        topics = [t for t in topics if (t.get("department") or {}).get("dept_id") == my_dept]

    # 覆盖面：哪些主题没有部门认领。这比"配了几个部门"有用得多——
    # 一个没人认领的主题，出负面时只能挂着没人管。
    # 部门账号不给这个结论：它说的是"全校配置有没有缺口"，
    # 让后勤账号看到"实习就业无人认领"，既无从下手，也不是它该管的事。
    if tenancy.is_dept_role():
        uncovered = []
    else:
        covered = {d for dept in depts_all if dept["status"] == "active"
                   for d in dept["dimensions"]}
        uncovered = [d for d in config.DIMENSIONS if d not in covered]

    engine, notice, summary = "rule", "", ""
    if use_ai and topics and config.DEEPSEEK_API_KEY:
        try:
            facts = [{"dimension": t["dimension"], "total": t["total"],
                      "negative": t["negative"], "neg_rate": t["neg_rate"],
                      "risk": t["risk"], "level": t["level"],
                      "reasons": [r["word"] for r in t["reasons"]],
                      "samples": [s["snippet"] for s in t["samples"][:2]]} for t in topics]
            brief = _ai_brief(facts, [{"dept_id": d["dept_id"], "name": d["name"],
                                       "duties": d["duties"], "keywords": d["keywords"]}
                                      for d in depts if d["status"] == "active"])
            by_dim = {str(x.get("dimension") or ""): x for x in (brief.get("topics") or [])
                      if isinstance(x, dict)}
            valid_ids = {d["dept_id"] for d in depts if d["status"] == "active"}
            applied = 0
            for t in topics:
                x = by_dim.get(t["dimension"])
                if not x:
                    continue
                advice = [str(a).strip()[:80] for a in (x.get("advice") or [])
                          if str(a).strip()][:3]
                dept_id = str(x.get("dept_id") or "").strip()
                # 模型给的部门必须是候选集里真实存在的，否则整条丢弃：
                # 让它自由发挥会造出系统里没有的责任主体，点建单时才炸。
                if dept_id and dept_id not in valid_ids:
                    dept_id = ""
                if dept_id:
                    d = next((z for z in depts if z["dept_id"] == dept_id), None)
                    # 还要求它认领了这个主题。否则会出现"卡片说这个主题没人管、
                    # 表格里却写着归后勤"的自相矛盾。
                    if d and t["dimension"] in d["dimensions"]:
                        t["department"] = {"dept_id": d["dept_id"], "name": d["name"],
                                           "owner_name": d["owner_name"],
                                           "owner_title": d["owner_title"],
                                           "contact": d["contact"],
                                           "sla_hours": d["sla_hours"],
                                           "matched_keywords": []}
                if advice:
                    t["advice"] = advice
                    t["advice_source"] = "ai"
                reason = str(x.get("reason") or "").strip()[:80]
                if reason:
                    t["ai_reason"] = reason
                applied += 1
            if applied:
                engine = "deepseek"
                summary = str(brief.get("summary") or "").strip()[:60]
        except Exception as e:          # 模型不可用不该让整页失败
            notice = f"AI 研判不可用（{str(e)[:40]}），以下为统计规则结果"

    # ---- 周带的落点：每个主题"什么时候该办完" ----
    # 判断链上的时限这一环，页面要从一句话变成位置，就必须先有一个**可复算的时刻**。
    # 两种来源，绝不混：
    #   · 已建单（ticket_due 有值）→ 那张单真实的 sla_due，实心点；
    #   · 未建单但已归口 → 现在 + 部门时限（工作小时），虚线点，是"如果现在建单"的推算；
    #   · 未归口 → 不给时刻。时钟从归口开始，没有部门就没有 deadline，
    #     硬塞一个 48 小时等于替学校许了一个没人认领的承诺。
    # 放在 AI 归口之后算：模型可能改归口，改完时限也得跟着改。
    now = datetime.now()
    for t in topics:
        dept = t.get("department") or {}
        due, basis = None, ""
        if t.get("ticket_due"):
            # 与推算值统一成同一种写法（YYYY-MM-DDTHH:MM），前端只切前 10 位取日期；
            # 库里存的是 "YYYY-MM-DD HH:MM:SS"，不归一化会在同一条带上出现两种格式。
            due, basis = str(t["ticket_due"]).replace(" ", "T")[:16], "ticket"
        elif dept.get("sla_hours"):
            d = tickets.add_work_hours(now, dept["sla_hours"])
            if d:
                due, basis = d.strftime("%Y-%m-%dT%H:%M"), "projected"
        t["due"] = due
        t["due_basis"] = basis
        t["sla_hours"] = dept.get("sla_hours") or None
        # 还剩 / 已超多少（工作小时，负值＝已超）。**必须由后端算**：
        # 前端没有节假日表，自己用日期减今天会算出自然日，和工单页的工作小时对不上。
        # 驾驶舱「最该先办的三件事」直接读这个数，不在浏览器里做第二次日历运算。
        if due:
            try:
                _due_dt = datetime.strptime(due, "%Y-%m-%dT%H:%M")
                # work_minutes_between 是**有方向的**（start→end），反过来算会被钳到 0。
                # 所以先判前后：未来算剩余，过去算已超（负值），否则"已超"永远读成 0。
                _mins = (tickets.work_minutes_between(now, _due_dt) if _due_dt >= now
                         else -tickets.work_minutes_between(_due_dt, now))
                t["remaining_work_hours"] = round(_mins / 60, 1)
                # 文案也在这里给：单位怎么选（分钟 / 工作小时 / 工作日）是**口径**，
                # 只允许有一个出口（tickets._human_minutes），前端不再自己算一遍。
                t["remaining_text"] = (("已超 " if _mins < 0 else "还剩 ")
                                       + tickets._human_minutes(abs(_mins)))
            except Exception:
                t["remaining_work_hours"] = None
                t["remaining_text"] = None
        else:
            t["remaining_work_hours"] = None
            t["remaining_text"] = None

    return {
        "dataset_id": f["dataset_id"],
        "topics": topics,
        "departments": depts,
        "engine": engine,
        # 只有真正发起过 AI 研判、且确实失败时才写"AI 不可用"。
        # 页面会先用 use_ai=False 拉一版规则结果立刻出表（快），再后台补 AI；
        # 那一版若也写"AI 不可用"，等于在 AI 正常时先说一句假话。
        "engine_label": ("DeepSeek 研判" if engine == "deepseek"
                         else ("统计规则" if not use_ai else "统计规则（AI 不可用）")),
        "ai_available": bool(config.DEEPSEEK_API_KEY),
        "anchor": anchor.isoformat(),
        # mode 是给前端做文案分支用的：rolling（锚点回看 30 天）/ month（自然月）
        "mode": "month" if window else "rolling",
        "period": (window or {}).get("period"),
        "window": ({"period": window.get("period"),
                    "date_from": f["date_from"], "date_to": f["date_to"],
                    "prev_from": window.get("prev_from"), "prev_to": window.get("prev_to"),
                    "anchor": f["date_to"]} if window else
                   {"recent_30d_from": (anchor - timedelta(days=30)).isoformat(),
                    "prev_30d_from": (anchor - timedelta(days=60)).isoformat(),
                    "anchor": anchor.isoformat()}),
        "summary": summary,
        # notice 只留给"降级"这类必须让人知道的事（AI 不可用、回落规则）。
        # 以前默认塞一句"归口与建议由模型…，风险百分比由规则计算"，
        # 每个数据集都要在表格下面重复一遍，读第二眼就是噪音。
        "notice": notice,
        "weights": RISK_WEIGHTS,
        "uncovered": uncovered,
        # None = 全校视角；有值 = 部门账号，前端据此把文案与区块收窄（隐藏全局研判记录、
        # 隐藏跨页跳转），但**不靠它做权限判断**——权限只认后端。
        "scoped": _scope_info(my_dept, scoped_depts),
        "disclaimer": DISCLAIMER,
        "can_edit": can_edit(),
        # 能不能建单 = 模块开放 且 角色有权限。两件事分开传：
        # 页面要说的话不一样——"没权限"要找人开权限，"模块没开"是这个演示态本来的样子。
        "can_create": can_create(),
        "tickets_enabled": bool(config.CP_TICKETS),
    }


# ============================ 按建议建单 ============================
def _attach_ticket_codes(topics, dataset_id=None):
    """给每个主题回填"已经建过的那张单"的单号。

    少了这一步，行内的「建单」按钮永远亮着：用户点开弹窗、写完备注、按下确认，
    才在落库前被 409 挡住（tickets.create 的闸门），刷新之后按钮又长回来——
    一个"已知不能做的事，被反复邀请去做"的循环。

    认列口径：优先认 `dimension` 列，没有才从 `rule_id = 'DEPT-<维度>'` 里拆。
    只认 rule_id 是不够的——演示单是直接写库的，没有 rule_id，
    于是"库里明明有一张宿舍住宿的单，页面却说没有"。
    数据集也要对上：同一主题在另一份数据上的单，不该被当成这一份的。
    查库失败就当没有单号，宁可多显示一颗按钮，也不要因为一次 SQL 异常让整页研判崩掉。
    """
    if not topics:
        return
    try:
        rows = db.query_all(
            "SELECT code, dimension, dataset_id, rule_id, sla_due, priority FROM tickets "
            "WHERE tenant_id = ? AND status NOT IN ('closed', 'rejected') "
            # 同一主题挂两张单时，代表单取**最早到期**的那张：
            # 按建单顺序取会把"已经欠了 9.1 天"的旧单藏起来，只露较新的那张。
            "ORDER BY COALESCE(sla_due, '9999') ASC",
            (db.tid(),))
    except Exception:
        return
    by_dim = {}
    for r in rows:
        if dataset_id and r["dataset_id"] and r["dataset_id"] != dataset_id:
            continue
        dim = str(r["dimension"] or "").strip()
        if not dim:
            rid = str(r["rule_id"] or "")
            dim = rid[len("DEPT-"):] if rid.startswith("DEPT-") else ""
        if not dim:
            continue
        # 同一维度理论上只会有一张未结案的单；真出现两张时取先查到的那张，
        # 让人先去看既有那单，而不是再开一张。
        by_dim.setdefault(dim, r)
    for t in topics:
        r = by_dim.get(t["dimension"])
        if r:
            t["ticket_code"] = r["code"]
            # 周带要用**真实的** SLA 截止时刻：已建单的落点不能拿部门时限再推一遍，
            # 那张单的截止是按它自己建单那一刻 + 优先级阶梯算出来的，两者会差好几天。
            t["ticket_due"] = r["sla_due"]


def create_ticket(body):
    """把一条研判结果变成工单。

    风险值与优先级仍然**由服务端重算**，不采信前端传来的数字——
    数字是定性依据，可以被篡改成"这条不急"。
    但归口部门与建议文本允许前端回传：页面上给用户看的是模型研判的结果，
    服务端如果改用规则重算，建出来的单会和用户刚才看到的不一样（"所见非所建"）。
    两者都做白名单校验：部门必须真实存在且在岗，建议只当文本、限长限条。
    """
    dimension = str(body.get("dimension") or "").strip()
    if not dimension:
        raise TicketError("缺少主题（dimension）", 400, "bad_request")
    data = assess(body.get("dataset_id"), use_ai=False)
    topic = next((t for t in data["topics"] if t["dimension"] == dimension), None)
    if not topic:
        raise TicketError("该主题当前没有可研判的数据，请刷新后重试", 409, "conflict")

    dept = topic.get("department") or {}
    picked = str(body.get("department_id") or "").strip()
    if picked:
        row = db.query_one(
            "SELECT * FROM departments WHERE tenant_id = ? AND dept_id = ? AND status = 'active'",
            (db.tid(), picked))
        if row:
            d = _row(row)
            dept = {"dept_id": d["dept_id"], "name": d["name"], "owner_name": d["owner_name"],
                    "owner_title": d["owner_title"], "contact": d["contact"],
                    "sla_hours": d["sla_hours"], "matched_keywords": []}

    advice = [str(a).strip()[:80] for a in (body.get("advice") or [])
              if str(a).strip()][:3] or (topic.get("advice") or [])
    evidence = (topic.get("samples") or [{}])[0].get("comment_ref")
    summary = (f"风险指数 {topic['risk']}（{topic['level']}）· 负面 {topic['negative']}/"
               f"{topic['total']} 条（{topic['neg_rate']}%）。\n"
               + "\n".join(f"{i + 1}. {a}" for i, a in enumerate(advice)))
    if dept:
        summary += f"\n归口：{dept['name']}（负责人 {dept.get('owner_name') or '未填'}）"
    # 建单人补的一句话：现场情况、联系人、为什么现在开单。单独成段并标明来源，
    # 读单的人要能分清哪句是系统算的、哪句是同事补的。
    note = str(body.get("note") or "").strip()[:200]
    if note:
        summary += f"\n\n备注（建单人）：{note}"

    return tickets.create({
        "title": str(body.get("title") or
                     f"「{dimension}」风险指数 {topic['risk']} 需核实处置")[:120],
        "summary": summary,
        "priority": str(topic["priority"]),
        "dimension": dimension,
        # 存口径而不是数字：工单详情据此复现"当时在说哪一批评论"
        "filters": {"dataset_id": data.get("dataset_id"), "dimensions": [dimension]},
        "comment_ref": evidence,
        "rule_id": f"DEPT-{dimension}",
        "department_id": dept.get("dept_id"),
        "department_name": dept.get("name"),
    })
