# -*- coding: utf-8 -*-
"""S1 验收：租户隔离真正生效，且数据没有被"全关掉"式隔离弄丢。

和 verify_isolation.py 的分工：
    verify_isolation  只管"能不能越权"（负向：32 次交叉访问必须 0 泄露）
    本脚本            管"自己的数据还读得到吗"（正向）+ 缓存键 + 写入归属 + 空租户防护

为什么必须同时有正向断言：
    最省事的"隔离"是把所有 SQL 改成一律 `WHERE tenant_id = '<无人使用的值>'`，
    矩阵立刻全绿，系统也立刻全废。只看负向测试是发现不了这种事的。

运行：
    CP_AUTH=1 .venv/Scripts/python.exe tests/test_s1.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campuspulse import analytics, config, db, rag, reports, tenancy  # noqa: E402
from campuspulse.web import create_app  # noqa: E402

PASS = FAIL = 0
FAILED = []
PROBE = "t_s1_probe"


def check(name, fn):
    global PASS, FAIL
    try:
        detail = fn()
        PASS += 1
        print(f"  OK  {name}" + (f" — {detail}" if detail else ""))
    except Exception as e:
        FAIL += 1
        FAILED.append(name)
        print(f"  !!  {name} — {type(e).__name__}: {e}")


class Ctx:
    """临时切换到某个租户身份，退出时恢复。"""

    def __init__(self, tid, role="admin"):
        self.ctx = {"tenant_id": tid, "tenant_name": tid, "user_id": "u_" + tid,
                    "username": "u", "display_name": "u", "role": role,
                    "mode": "auth", "must_change": 0}
        self.token = None

    def __enter__(self):
        self.token = tenancy.set_context(self.ctx)
        return self

    def __exit__(self, *a):
        tenancy.reset_context(self.token)


def setup():
    """造探针租户 + 两边各一个 admin 账号，并确保默认租户有数据。"""
    if not tenancy.get_tenant(PROBE):
        tenancy.create_tenant(PROBE, "S1 验证租户（可删）", "验证",
                              "test_s1.py 自动创建，运行结束会清理")
    conn = db.get_conn()
    conn.execute("DELETE FROM users WHERE tenant_id = ? AND username IN ('s1a','s1b')", (PROBE,))
    conn.execute("DELETE FROM users WHERE tenant_id = ? AND username IN ('s1a','s1b')",
                 (config.DEFAULT_TENANT_ID,))
    conn.commit()
    a = tenancy.create_user(config.DEFAULT_TENANT_ID, "s1a", role="admin", must_change=0)
    b = tenancy.create_user(PROBE, "s1b", role="admin", must_change=0)
    return a["password"], b["password"]


def cleanup():
    conn = db.get_conn()
    conn.execute("DELETE FROM users WHERE username IN ('s1a','s1b')")
    conn.execute("DELETE FROM comments WHERE tenant_id = ?", (PROBE,))
    conn.execute("DELETE FROM datasets WHERE tenant_id = ?", (PROBE,))
    conn.execute("DELETE FROM reports WHERE tenant_id = ?", (PROBE,))
    conn.execute("DELETE FROM agent_runs WHERE tenant_id = ?", (PROBE,))
    conn.execute("DELETE FROM tenants WHERE tenant_id = ?", (PROBE,))
    conn.commit()
    rag.invalidate()          # 清掉本进程里探针租户的索引条目


def login(app, uname, pwd, tenant=None):
    # 登录限流是 5 次 / 5 分钟（同一 IP）。本脚本每次运行都要登录两次，
    # 连续跑几轮就会被自己的防护挡住——所以每轮先清一次计数。
    # 这也说明一件事：演示现场如果多人同一出口 IP 反复登录，是会被限流的。
    tenancy.reset_login_limits()
    c = app.test_client()
    body = {"username": uname, "password": pwd}
    if tenant:
        # 登录接口默认落在默认租户；跨租户账号必须显式指定归属。
        # 单校部署下用户永远不传这个字段，所以对日常使用没有影响。
        body["tenant"] = tenant
    r = c.post("/api/auth/login", json=body)
    assert r.status_code == 200, f"{uname} 登录失败 {r.status_code} {r.get_data(as_text=True)[:160]}"
    return c


def main():
    if not config.CP_AUTH:
        print("需要 CP_AUTH=1 运行：CP_AUTH=1 .venv/Scripts/python.exe tests/test_s1.py")
        return 2

    app = create_app()
    pwd_a, pwd_b = setup()
    try:
        ca = login(app, "s1a", pwd_a)
        cb = login(app, "s1b", pwd_b, tenant=PROBE)

        # ---------- 1. 正向：自己的数据读得到，数字与 SQL 对得上 ----------
        print("\n== 1. 同租户读取（防止用「全关掉」换来假隔离） ==")

        def own_datasets():
            r = ca.get("/api/datasets")
            items = r.get_json()["data"]["datasets"]
            assert items, "默认租户看不到自己的任何数据集"
            return f"{len(items)} 个数据集"

        check("默认租户能列出自己的数据集", own_datasets)

        def own_total():
            ds_id = ca.get("/api/datasets").get_json()["data"]["datasets"][0]["dataset_id"]
            api_total = ca.get(f"/api/dashboard/summary?dataset_id={ds_id}").get_json()["data"]["total"]
            sql_total = db.scalar(
                "SELECT COUNT(*) FROM comments WHERE tenant_id=? AND dataset_id=? AND cleaning_status='kept'",
                (config.DEFAULT_TENANT_ID, ds_id))
            assert api_total > 0, f"接口返回 0 条（数据被隔离条件吃掉）"
            assert api_total == sql_total, f"接口 {api_total} ≠ SQL {sql_total}"
            return f"{ds_id} = {api_total} 条，与 SQL 完全一致"

        check("汇总条数与 SQL 逐条对得上", own_total)

        def kpi_same_as_open():
            """CP_AUTH=0 时算出的 KPI，与 CP_AUTH=1 同租户算出的必须一致。

            这是最有力的一条：它同时证明"加租户条件"没有过滤掉任何本该看到的数据。
            """
            ds_id = ca.get("/api/datasets").get_json()["data"]["datasets"][0]["dataset_id"]
            with Ctx(config.DEFAULT_TENANT_ID):
                f = analytics.build_filters(ds_id)
                inside = analytics.stats_overview(f)
            token = tenancy.set_context(tenancy.system_context())
            try:
                f2 = analytics.build_filters(ds_id)
                syswide = analytics.stats_overview(f2)
            finally:
                tenancy.reset_context(token)
            for k in ("total", "positive", "neutral", "negative", "denominator"):
                assert inside[k] == syswide[k], f"{k}: 租户内 {inside[k]} ≠ 系统 {syswide[k]}"
            return f"total/情感三分组全部一致（{inside['total']} 条）"

        check("租户内 KPI 与系统口径一致", kpi_same_as_open)

        # ---------- 2. 负向：别的租户什么都看不到 ----------
        print("\n== 2. 跨租户不可见 ==")

        def other_sees_nothing():
            r = cb.get("/api/datasets")
            items = r.get_json()["data"]["datasets"]
            assert items == [], f"探针租户看到了 {len(items)} 个别人的数据集"
            return "数据集列表为空"

        check("新租户看不到默认租户的数据集", other_sees_nothing)

        def other_detail_404():
            ds_id = ca.get("/api/datasets").get_json()["data"]["datasets"][0]["dataset_id"]
            r = cb.get(f"/api/datasets/{ds_id}")
            assert r.status_code == 404, f"期望 404，实际 {r.status_code}"
            return f"{ds_id} → 404"

        check("按 ID 直取别人的数据集 → 404", other_detail_404)

        # ---------- 3. 缓存键：这是最容易被漏的一处 ----------
        print("\n== 3. 内存缓存键必须带租户 ==")

        def rag_index_keyed_by_tenant():
            ds_id = ca.get("/api/datasets").get_json()["data"]["datasets"][0]["dataset_id"]
            with Ctx(config.DEFAULT_TENANT_ID):
                idx_a = rag.get_index(ds_id)
            with Ctx(PROBE):
                idx_b = rag.get_index(ds_id)
            assert idx_a.n > 0, "默认租户索引为空"
            assert idx_b.n == 0, f"探针租户拿到了默认租户的索引（{idx_b.n} 条文档）"
            assert (config.DEFAULT_TENANT_ID, ds_id) in rag._CACHE, "缓存键里没有租户"
            return f"A={idx_a.n} 条 / B={idx_b.n} 条，键为 (租户, 数据集)"

        check("RAG 索引按 (租户, 数据集) 建键", rag_index_keyed_by_tenant)

        def keyword_cache_keyed_by_tenant():
            ds_id = ca.get("/api/datasets").get_json()["data"]["datasets"][0]["dataset_id"]
            with Ctx(config.DEFAULT_TENANT_ID):
                f = analytics.build_filters(ds_id)
                analytics.stats_distributions(f, top_words=8)
            keys = [k for k in analytics._KEYWORD_CACHE if k[1] == ds_id]
            assert keys, "关键词缓存里没有该数据集"
            assert all(len(k) == 5 and isinstance(k[0], str) and k[0] for k in keys), \
                f"缓存键首字段不是租户：{keys[:1]}"
            return f"键首字段 = 租户 ID（{keys[0][0]}）"

        check("词云缓存键带租户", keyword_cache_keyed_by_tenant)

        # ---------- 4. 写入归属 ----------
        print("\n== 4. 写入归属 ==")

        def report_not_visible():
            rid = db.scalar("SELECT report_id FROM reports WHERE tenant_id=? ORDER BY created_at DESC LIMIT 1",
                            (config.DEFAULT_TENANT_ID,))
            if not rid:
                return "库里暂无报告，跳过（acceptance 跑过才有）"
            with Ctx(config.DEFAULT_TENANT_ID):
                assert reports.get(rid) is not None, "本租户读不到自己的报告"
            with Ctx(PROBE):
                assert reports.get(rid) is None, "探针租户读到了别人的报告"
            return f"{rid}：本租户可读、探针租户不可读"

        check("报告不跨租户可读", report_not_visible)

        def comment_not_visible():
            row = db.query_one(
                "SELECT comment_ref, dataset_id FROM comments WHERE tenant_id=? AND cleaning_status='kept' LIMIT 1",
                (config.DEFAULT_TENANT_ID,))
            with Ctx(PROBE):
                got = analytics.get_comment(row["dataset_id"], row["comment_ref"])
            assert got is None, "探针租户读到了别人的评论正文"
            return "评论正文不可跨租户读取"

        check("评论详情不跨租户可读", comment_not_visible)

        # ---------- 5. 空租户防护 ----------
        print("\n== 5. 没有租户上下文时，写操作必须失败而不是写坏 ==")

        def empty_tenant_guard():
            token = tenancy.set_context(tenancy.anonymous())   # CP_AUTH=1 时 tenant_id 为空
            try:
                assert db.tid() == "", "未登录身份的 tenant_id 应为空"
                raised = 0
                try:
                    db.upsert_dataset({"dataset_id": "ds_should_fail", "name": "x",
                                       "source_label": "", "note": "", "status": "ready",
                                       "is_demo": 0, "data_version": None, "created_at": None,
                                       "analyzed_at": None, "raw_count": 0, "valid_count": 0,
                                       "time_start": None, "time_end": None,
                                       "platforms": None, "quality": None})
                except ValueError:
                    raised += 1
                try:
                    db.insert_comments([{"comment_ref": "x", "dataset_id": "y"}])
                except ValueError:
                    raised += 1
                assert raised == 2, f"应抛 2 次 ValueError，实际 {raised}"
                assert db.scalar("SELECT COUNT(*) FROM datasets WHERE tenant_id = ? "
                                 "AND dataset_id = 'ds_should_fail'", (config.DEFAULT_TENANT_ID,)) == 0, \
                    "脏数据被写进去了"
            finally:
                tenancy.reset_context(token)
            return "upsert / insert 均拒绝写入"

        check("空租户写库直接报错", empty_tenant_guard)

        def no_tenant_reads_nothing():
            token = tenancy.set_context(tenancy.anonymous())
            try:
                assert db.list_datasets() == [], "未登录身份读到了数据集"
                assert db.get_dataset(config.DEFAULT_DATASET_ID) is None, "未登录身份读到了数据集详情"
            finally:
                tenancy.reset_context(token)
            return "未登录身份读不到任何数据集"

        check("空租户读不到任何数据", no_tenant_reads_nothing)

    finally:
        cleanup()

    print("\n" + "=" * 66)
    if FAIL:
        print(f"结果：{PASS}/{PASS + FAIL} 通过，失败 {FAIL} 项：{'、'.join(FAILED)}")
        return 1
    print(f"结果：{PASS}/{PASS} 项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
