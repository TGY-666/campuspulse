# -*- coding: utf-8 -*-
"""隔离矩阵：2 租户 × 2 角色 × 8 个带 ID 接口 = 32 次交叉访问。

这是多租户功能**唯一**的证明。别的用例只能证明"能登录""有角色"，
只有这张矩阵能回答评委真正会问的那句：**"A 校的人能不能看到 B 校的数据？"**

双向才算数：
    方向一：探针租户的用户 → 读默认租户的真实数据（16 次）
    方向二：默认租户的用户 → 读探针租户伪造的数据（16 次）
只测一个方向会漏掉"代码把 tenant_id 写反了"这类错误——那种情况下单向全绿、反向全红。

判定标准（AC-M1-08）：**32 次里 0 次返回 200**。

状态码含义：
    200       🔴 泄露。跨租户读到了数据，这是事故级问题
    404/403   🟢 正确拦截（404 优先：不泄露"该 ID 是否存在"）
    401       🟡 会话没带上，测试本身有问题
    500       🟡 服务崩溃或被 enforce 抛异常打断，需看日志

运行：
    CP_AUTH=1 .venv/Scripts/python.exe tools/verify_isolation.py
    CP_AUTH=1 .venv/Scripts/python.exe tools/verify_isolation.py --dry   # 只看矩阵结构

S0 阶段预期**全红**：业务 SQL 还一条 tenant_id 都没加，隔离尚未生效。
这是设计好的节奏——先有"能测出红"的尺子，再按尺子改造。
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campuspulse import config, db, tenancy  # noqa: E402
from campuspulse.web import create_app  # noqa: E402

PROBE_TENANT = "t_iso_probe"
PROBE_DS = "ds_iso_probe"
PROBE_REF = "CMT-ISO-PROBE-0001"
PROBE_RPT = "rpt_iso_probe"

# 8 个"带资源 ID"的接口。选它们的标准：
# 都必须携带一个属于某个租户的具体对象标识，才能构成真正的越权尝试。
# 纯列表接口（/api/meta/*）不在此列——它们本来就该返回本租户全量。
ENDPOINTS = [
    ("数据集详情", "GET", "/api/datasets/{ds}"),
    ("数据集质量", "GET", "/api/datasets/{ds}/quality"),
    # 评论详情必须显式带 dataset_id：该接口不传时默认落到 DEFAULT_DATASET_ID，
    # 此时它只会去默认数据集里找，测不出真正的越权面（会假绿）。
    ("评论详情", "GET", "/api/comments/{ref}?dataset_id={ds}"),
    ("报告详情", "GET", "/api/reports/{rpt}"),
    ("报告导出", "GET", "/api/reports/{rpt}/export.md"),
    ("证据检索", "GET", "/api/rag/search?dataset_id={ds}&q=test"),
    ("评论列表", "GET", "/api/comments?dataset_id={ds}&limit=5"),
    ("情感统计", "GET", "/api/analytics/sentiment?dataset_id={ds}"),
]

ROLES = ("analyst", "viewer")

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _no_color():
    global GREEN, RED, YELLOW, DIM, RESET
    GREEN = RED = YELLOW = DIM = RESET = ""


def verdict(status, body=""):
    """判定一次跨租户访问的结果。

    判据是"**有没有拿到数据**"，不是"状态码是不是恰好 404"。
    业务层的分析类接口在数据集不属于本租户时走 `FilterError` 分支返回 400
    （错误码 invalid_filter，文案「数据集不存在」），这同样是正确拒绝：
    跨租户时我们**刻意不区分"没有这个数据集"和"有但无权"**，
    否则状态码本身就成了探测器。
    """
    if status == 200:
        return "RED", "泄露"
    if status in (403, 404):
        return "GREEN", "已拦截"
    if status == 400 and "数据集不存在" in (body or ""):
        return "GREEN", "已拦截(不存在)"
    if status == 401:
        return "YELLOW", "会话丢失"
    if status >= 500:
        return "YELLOW", "服务异常"
    return "YELLOW", f"待判({status})"


# ============================ 目标资源 ============================
def real_targets():
    """在默认租户里挑一组"受害者资源"。

    所有查询都必须带 tenant_id——包括本脚本自己。
    强制模式（CP_TENANT_ENFORCE=1，现在默认跟随 CP_AUTH 开启）下，
    任何一条漏写都会直接抛异常，包括验证脚本。
    这不是麻烦，而是这条规则唯一的可信度来源：**规则对写规则的人同样生效**。
    """
    t = config.DEFAULT_TENANT_ID
    ds = db.query_one("SELECT dataset_id FROM datasets WHERE tenant_id = ? AND dataset_id = ?",
                      (t, config.DEFAULT_DATASET_ID)) or \
         db.query_one("SELECT dataset_id FROM datasets WHERE tenant_id = ? LIMIT 1", (t,))
    if not ds:
        return None
    ds_id = ds["dataset_id"]
    # 必须挑 cleaning_status='kept' 的记录：评论详情接口带这个过滤条件，
    # 若随手取 LIMIT 1 拿到一条被清洗掉的评论，接口会返回 404——
    # 那是"这条数据不存在"，不是"越权被拦"，矩阵会**假绿**。
    ref = db.query_one(
        "SELECT comment_ref FROM comments WHERE tenant_id = ? AND dataset_id = ? "
        "AND cleaning_status = 'kept' LIMIT 1",
        (t, ds_id))
    rpt = db.query_one("SELECT report_id FROM reports WHERE tenant_id = ? AND dataset_id = ? LIMIT 1",
                       (t, ds_id))
    return {"ds": ds_id, "ref": ref["comment_ref"] if ref else "CMT-NOT-EXIST",
            "rpt": rpt["report_id"] if rpt else "rpt_none", "has_report": bool(rpt)}


def ensure_probe_targets():
    """在探针租户下造一份最小可用的资源，供反方向越权测试。

    刻意不导入真实数据：这里只需要"一个属于 B 租户的对象"，
    导入 2 万条只会让脚本从 3 秒变成 30 秒。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO datasets (dataset_id, name, source_label, note, status, "
        "is_demo, data_version, created_at, analyzed_at, raw_count, valid_count, "
        "time_start, time_end, platforms, quality, tenant_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (PROBE_DS, "隔离探针数据集", "verify_isolation.py", "脚本自动创建，运行结束会清理",
         "ready", 0, "probe", now, now, 1, 1, now[:10], now[:10], "{}", "{}", PROBE_TENANT))
    conn.execute(
        "INSERT OR REPLACE INTO comments (comment_ref, dataset_id, platform, content, "
        "content_masked, comment_time, comment_date, comment_month, comment_hour, "
        "comment_weekday, like_count, dimension, dimensions, sentiment, cleaning_status, "
        "content_len, tenant_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (PROBE_REF, PROBE_DS, "探针", "这是隔离测试专用记录，不应被任何真实用户看到。",
         "这是隔离测试专用记录，不应被任何真实用户看到。", now, now[:10], now[:7], 12, 3,
         0, "校园环境", json.dumps(["校园环境"], ensure_ascii=False), "neutral",
         "kept", 24, PROBE_TENANT))
    conn.execute(
        "INSERT OR REPLACE INTO reports (report_id, dataset_id, engine, filters, payload, "
        "created_at, tenant_id) VALUES (?,?,?,?,?,?,?)",
        (PROBE_RPT, PROBE_DS, "probe", "{}",
         json.dumps({"content": {"summary": "隔离探针报告"}, "kpi": {}}, ensure_ascii=False),
         now, PROBE_TENANT))
    conn.commit()
    return {"ds": PROBE_DS, "ref": PROBE_REF, "rpt": PROBE_RPT, "has_report": True}


def clean_probe_targets():
    conn = db.get_conn()
    conn.execute("DELETE FROM comments WHERE tenant_id = ? AND comment_ref = ?", (PROBE_TENANT, PROBE_REF))
    conn.execute("DELETE FROM reports WHERE tenant_id = ? AND report_id = ?", (PROBE_TENANT, PROBE_RPT))
    conn.execute("DELETE FROM datasets WHERE tenant_id = ? AND dataset_id = ?", (PROBE_TENANT, PROBE_DS))
    conn.commit()


# ============================ 账号准备 ============================
def username_for(side, role):
    return f"iso_{side.lower()}_{role}"


def prepare():
    """建两边各 2 个探针用户。返回 {(side, role): (username, password)}。"""
    if not tenancy.get_tenant(PROBE_TENANT):
        tenancy.create_tenant(PROBE_TENANT, "隔离测试租户（可删）", "探针",
                              "verify_isolation.py 自动创建，运行结束会清理")
    else:
        # 上次异常退出可能留下用户：清掉换新的，避免旧口令对不上
        db.get_conn().execute("DELETE FROM users WHERE tenant_id = ?", (PROBE_TENANT,))
        db.get_conn().commit()

    creds = {}
    for side in ("A", "B"):
        tid = config.DEFAULT_TENANT_ID if side == "A" else PROBE_TENANT
        for role in ROLES:
            uname = username_for(side, role)
            old = tenancy.get_user(tid, uname)
            if old:
                db.get_conn().execute("DELETE FROM users WHERE user_id = ?", (old["user_id"],))
                db.get_conn().commit()
            r = tenancy.create_user(tid, uname, role=role, must_change=0)
            creds[(side, role)] = (uname, r["password"])
    return creds


def cleanup(creds):
    conn = db.get_conn()
    for (side, _role), (uname, _pwd) in creds.items():
        tid = config.DEFAULT_TENANT_ID if side == "A" else PROBE_TENANT
        conn.execute("DELETE FROM users WHERE tenant_id = ? AND username = ?", (tid, uname))
    conn.execute("DELETE FROM users WHERE tenant_id = ?", (PROBE_TENANT,))
    conn.commit()
    clean_probe_targets()
    conn.execute("DELETE FROM tenants WHERE tenant_id = ?", (PROBE_TENANT,))
    conn.commit()


# ============================ 主流程 ============================
def run(dry=False, verbose=True):
    if not config.CP_AUTH:
        print("需要 CP_AUTH=1 运行：CP_AUTH=1 .venv/Scripts/python.exe tools/verify_isolation.py")
        return 2

    targets_a = real_targets()
    if not targets_a:
        print("库里没有可用数据集，无法构造交叉访问")
        return 2

    app = create_app()
    if dry:
        print(f"\n方向一：探针租户 {PROBE_TENANT} → 默认租户真实资源 {targets_a['ds']}")
        print(f"方向二：默认租户 → 探针租户伪造资源 {PROBE_DS}")
        print(f"矩阵：2 租户 × {len(ROLES)} 角色 × {len(ENDPOINTS)} 接口 = "
              f"{2 * len(ROLES) * len(ENDPOINTS)} 次\n")
        for name, method, path in ENDPOINTS:
            print(f"  {method:4} {name:8} 指向 {path.format(ds='<数据集>', ref='<评论>', rpt='<报告>')}")
        return 0

    targets_b = ensure_probe_targets()
    creds = prepare()
    rows = []
    counts = {"GREEN": 0, "RED": 0, "YELLOW": 0}

    try:
        for side in ("B", "A"):
            # side=B 表示攻击者是探针租户，目标是默认租户的真实数据；A 反之
            attacker_tenant = PROBE_TENANT if side == "B" else config.DEFAULT_TENANT_ID
            victim = targets_a if side == "B" else targets_b
            victim_label = "默认租户" if side == "B" else "探针租户"

            for role in ROLES:
                uname, pwd = creds[(side, role)]
                client = app.test_client()
                r = client.post("/api/auth/login",
                                json={"username": uname, "password": pwd, "tenant": attacker_tenant})
                if r.status_code != 200:
                    print(f"登录失败（{uname}）：{r.status_code} "
                          f"{(r.get_json() or {}).get('error', {}).get('message')}")
                    return 2

                for name, method, tpl in ENDPOINTS:
                    if name in ("报告详情", "报告导出") and not victim["has_report"]:
                        rows.append((victim_label, role, name, None, "SKIP", "库里无报告"))
                        continue
                    path = tpl.format(**victim)
                    try:
                        resp = client.open(path, method=method)
                        status = resp.status_code
                        # 取前 400 字节就够判断错误码；整段读一遍只是为了不让连接悬着
                        body = resp.get_data(as_text=True)[:400]
                    except Exception as exc:
                        rows.append((victim_label, role, name, None, "YELLOW",
                                     f"异常:{type(exc).__name__}"))
                        counts["YELLOW"] += 1
                        continue
                    color, why = verdict(status, body)
                    rows.append((victim_label, role, name, status, color, why))
                    counts[color] += 1
    finally:
        cleanup(creds)

    if verbose:
        print(f"\n资源：默认租户 {targets_a['ds']} / 探针租户 {targets_b['ds']}")
        print("每一行 = 一个跨租户用户去读**别人租户**的资源\n")
        print(f"{'目标租户':<10}{'攻击者角色':<10}{'接口':<12}{'状态':<7}结论")
        print("-" * 56)
        for vlabel, role, name, status, color, why in rows:
            c = {"GREEN": GREEN, "RED": RED, "YELLOW": YELLOW, "SKIP": DIM}.get(color, "")
            st = str(status) if status is not None else "-"
            print(f"{vlabel:<10}{config.ROLE_LABELS.get(role, role):<10}{name:<12}{st:<7}{c}{why}{RESET}")

    total = sum(counts.values())
    print("\n" + "=" * 56)
    print(f"  合计 {total} 次：{GREEN}拦截 {counts['GREEN']}{RESET} / "
          f"{RED}泄露 {counts['RED']}{RESET} / {YELLOW}待查 {counts['YELLOW']}{RESET}")
    if counts["RED"]:
        print(f"  {RED}✗ 隔离未生效{RESET}：{counts['RED']} 次跨租户读到了别人的数据")
        print("    S0 阶段这是预期结果——业务 SQL 尚未加 tenant_id。")
        print("    S1 的验收标准：这一行变成「泄露 0」。")
    elif counts["YELLOW"]:
        print(f"  {YELLOW}! 有 {counts['YELLOW']} 次结果待人工判断{RESET}")
    else:
        print(f"  {GREEN}✓ 隔离成立：0 次跨租户可读{RESET}")
    print("=" * 56)
    return 0 if (counts["RED"] == 0 and counts["YELLOW"] == 0) else 1


def main():
    ap = argparse.ArgumentParser(description="租户隔离矩阵（32 次交叉访问）")
    ap.add_argument("--dry", action="store_true", help="只打印矩阵结构，不发请求")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()
    if args.no_color or os.environ.get("NO_COLOR"):
        _no_color()
    return run(dry=args.dry)


if __name__ == "__main__":
    raise SystemExit(main())
