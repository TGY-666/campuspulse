# -*- coding: utf-8 -*-
"""租户条件漏写扫描器 —— 逐个接口统计"没带 tenant_id 的 SQL"。

为什么需要它：
    `db.TenantConn` 只能告诉你"总共漏了多少条"，说不清"是哪条链路漏的"。
    这个脚本把 leaks 计数按接口切开：每打一个请求，前后取差，
    差值 > 0 就说明这个接口内部有 SQL 漏了租户条件，并打印出具体语句。

用法：
    python tools/scan_tenant_leaks.py              # 全部接口
    python tools/scan_tenant_leaks.py --fast       # 跳过调用大模型的慢接口
    python tools/scan_tenant_leaks.py -v           # 打印每条可疑 SQL

退出码：0 = 全部干净；1 = 仍有漏写（S1 阶段会一直是非 0，属预期）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campuspulse import config, db  # noqa: E402


def _pick_dataset(c):
    """挑一个有数据的数据集当靶子，避免扫的是空结果（空结果也会走 SQL，但覆盖不全）。"""
    r = c.get("/api/datasets")
    data = (r.get_json() or {}).get("data") or {}
    items = data.get("datasets") or data.get("items") or []
    if not items:
        return None
    for it in items:
        if (it.get("valid_count") or 0) > 0:
            return it["dataset_id"]
    return items[0]["dataset_id"]


def _pick_comment(c, ds_id):
    r = c.get(f"/api/comments?dataset_id={ds_id}&limit=1&order=likes")
    items = ((r.get_json() or {}).get("data") or {}).get("items") or []
    return items[0]["comment_ref"] if items else None


def build_cases(ds_id, ref, fast=True):
    """(名称, method, path, body)。顺序按调用链排列，方便看出是哪一层漏的。"""
    q = f"dataset_id={ds_id}"
    cases = [
        ("数据集列表", "GET", "/api/datasets", None),
        ("数据集详情", "GET", f"/api/datasets/{ds_id}", None),
        ("质量报告", "GET", f"/api/datasets/{ds_id}/quality", None),
        ("筛选项", "GET", f"/api/facets?{q}", None),
        ("驾驶舱汇总", "GET", f"/api/dashboard/summary?{q}", None),
        ("情感分布", "GET", f"/api/analytics/sentiment?{q}", None),
        ("主题分布", "GET", f"/api/analytics/topics?{q}", None),
        ("时间趋势", "GET", f"/api/analytics/trend?{q}", None),
        ("平台对比", "GET", f"/api/analytics/platform-compare?{q}", None),
        ("长度/时段分布", "GET", f"/api/analytics/distributions?{q}", None),
        ("风险标记", "GET", f"/api/analytics/risk?{q}", None),
        ("评论检索", "GET", f"/api/comments?{q}&limit=5", None),
        ("RAG 检索", "GET", f"/api/rag/search?{q}&q=食堂", None),
        ("RAG 统计", "GET", f"/api/rag/stats?{q}", None),
        ("报告历史", "GET", f"/api/reports?{q}", None),
        ("拦截统计", "GET", f"/api/guard/stats?{q}", None),
        ("健康检查", "GET", "/api/health", None),
        ("处理状态", "GET", "/api/processing/status", None),
    ]
    if ref:
        cases.append(("评论详情", "GET", f"/api/comments/{ref}?{q}", None))
    cases += [
        ("报告生成", "POST", "/api/reports/generate",
         {"dataset_id": ds_id, "force_rule": True}),
    ]
    if not fast:
        cases += [
            ("驾驶舱 AI 简述", "GET", f"/api/dashboard/insight?{q}", None),
            ("图表 AI 解读", "GET", f"/api/analytics/chart-insights?{q}", None),
            ("Agent 对话", "POST", "/api/agent/chat",
             {"dataset_id": ds_id, "question": "负面主要集中在哪些维度？"}),
        ]
    return cases


def _ensure_scan_user(app):
    """造一个临时管理员并登录。

    不依赖 data/initial_admin.txt：那个文件在首次改密后应当被删掉，
    扫描脚本不该因为它没了就跑不起来。
    """
    from campuspulse import tenancy

    tid = config.DEFAULT_TENANT_ID
    uname = "leakscan"
    old = tenancy.get_user(tid, uname)
    if old:
        db.get_conn().execute("DELETE FROM users WHERE user_id = ?", (old["user_id"],))
        db.get_conn().commit()
    made = tenancy.create_user(tid, uname, role="admin", must_change=0)

    c = app.test_client()
    r = c.post("/api/auth/login", json={"username": uname, "password": made["password"]})
    if r.status_code != 200:
        raise RuntimeError(f"扫描账号登录失败：{r.status_code} {r.get_data(as_text=True)[:200]}")
    return c, uname


def _drop_scan_user(uname):
    db.get_conn().execute("DELETE FROM users WHERE username = ?", (uname,))
    db.get_conn().commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="跳过调用大模型的慢接口")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印具体 SQL")
    args = ap.parse_args()

    from campuspulse.web import create_app
    app = create_app()

    # 漏写审计只在 CP_AUTH=1 时开启（CP_AUTH=0 是 v1.0 兼容模式，不做任何额外判断）
    need_login = bool(config.CP_AUTH)
    if need_login:
        c, uname = _ensure_scan_user(app)
    else:
        c, uname = app.test_client(), None

    try:
        ds_id = _pick_dataset(c)
        if not ds_id:
            print("没有可用数据集，无法扫描（先跑一次服务让它 bootstrap）")
            return 2
        ref = _pick_comment(c, ds_id)
        print(f"靶子数据集：{ds_id}　评论样本：{ref or '（无）'}")
        print(f"CP_AUTH={int(config.CP_AUTH)}  CP_TENANT_ENFORCE={int(config.CP_TENANT_ENFORCE)}")
        if not need_login:
            print("提示：CP_AUTH=0 时不审计租户条件，本脚本将全部显示 0 处漏写（无意义）")
        print("-" * 78)

        bad = []
        cases = build_cases(ds_id, ref, fast=args.fast)
        for name, method, path, body in cases:
            db.reset_leak_stats()
            try:
                if method == "GET":
                    r = c.get(path)
                else:
                    r = c.post(path, json=body)
                code = r.status_code
            except Exception as e:                      # 接口本身崩了也要单独报出来
                code = f"ERR {type(e).__name__}"
            st = db.leak_stats()
            n = st["total"]
            flag = "  " if n == 0 else "!!"
            print(f"{flag} {name:<16} {str(code):<6} 漏写 {n} 处"
                  + (f"  {st['tables']}" if n else ""))
            if n:
                bad.append((name, st))
                if args.verbose:
                    for s in st["samples"]:
                        print(f"      └─ [{s['table']}] {s['sql']}")

        print("-" * 78)
        if bad:
            print(f"结论：{len(bad)}/{len(cases)} 个接口仍有漏写，共 "
                  f"{sum(b[1]['total'] for b in bad)} 条 SQL 缺 tenant_id")
            return 1
        print(f"结论：{len(cases)} 个接口全部带租户条件，0 处漏写。")
        return 0
    finally:
        if uname:
            _drop_scan_user(uname)


if __name__ == "__main__":
    sys.exit(main())
