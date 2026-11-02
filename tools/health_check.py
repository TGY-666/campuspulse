# -*- coding: utf-8 -*-
"""项目体检：把"用户真的会点到的入口"逐个打一遍，专挑边界。

与 acceptance.py 的分工：
  acceptance 测"设计是否被实现"（我知道答案，断言答案）；
  本脚本测"实现是否会被打崩"（我不知道答案，只要求不 5xx、不崩、不胡说）。

跑法：
    PORT=8010 CP_DEV=1 .venv/Scripts/python.exe run.py   # 先启动
    .venv/Scripts/python.exe tools/health_check.py
"""
import json
import sys

import requests

BASE = "http://127.0.0.1:8010"
DS = "ds_hist_all"
ROWS = []


def rec(kind, name, ok, detail=""):
    ROWS.append((kind, name, ok, detail))
    print(("OK " if ok else "!! ") + f"[{kind}] {name}" + (f" — {detail}" if detail else ""))


def get(path, **kw):
    return requests.get(BASE + path, timeout=60, **kw)


def post(path, **kw):
    return requests.post(BASE + path, timeout=180, **kw)


# ---------------- 1. 页面 ----------------
PAGES = [("/", "驾驶舱"), ("/insights", "洞察"), ("/agent", "AI 工作台"),
         ("/data", "数据管理"), ("/evidence", "证据检索")]


def tickets_on():
    """工单是开关模块：CP_TICKETS=0 时页面 404、接口 403 都是正确行为，不该记失败。"""
    try:
        return get("/api/tickets/meta").status_code == 200
    except Exception:
        return False


def check_pages():
    pages = list(PAGES)
    if tickets_on():
        pages.append(("/tickets", "处置工单"))
    for path, label in pages:
        r = get(path)
        if r.status_code != 200:
            rec("page", f"{label} {path}", False, f"{r.status_code}")
            continue
        html = r.text
        miss = [k for k in ("app.css", "core.js", "cp/") if k not in html]
        rec("page", f"{label} {path}", not miss,
            f"{len(html)} bytes" + (f" 缺 {miss}" if miss else ""))
    r = get("/this-page-does-not-exist")
    rec("page", "404 页", r.status_code == 404 and "app.css" in r.text,
        f"{r.status_code}" + ("" if "app.css" in r.text else " 用了裸 404，样式丢失"))


# ---------------- 2. 只读接口 ----------------
READ_APIS = [
    "/api/health", "/api/datasets", "/api/meta/definitions", "/api/facets",
    f"/api/datasets/{DS}", f"/api/datasets/{DS}/quality",
    "/api/dashboard/summary", "/api/dashboard/insight",
    "/api/analytics/sentiment", "/api/analytics/chart-insights", "/api/analytics/topics",
    "/api/analytics/trend", "/api/analytics/platform-compare", "/api/analytics/distributions",
    "/api/analytics/risk",
    "/api/comments?limit=5", "/api/agent/tools", "/api/guard/catalog", "/api/guard/stats",
    "/api/warmup", "/api/reports", "/api/rag/search?q=宿舍电费", "/api/rag/stats",
    "/api/processing/status",
]


def check_read_apis():
    for path in READ_APIS:
        url = path + ("&" if "?" in path else "?") + f"dataset_id={DS}"
        full = path if path in ("/api/health", "/api/meta/definitions", "/api/guard/catalog",
                                "/api/guard/stats", "/api/warmup", "/api/processing/status",
                                "/api/rag/stats") else url
        try:
            r = get(full)
        except Exception as e:
            rec("api", full, False, f"请求异常 {e}")
            continue
        if r.status_code != 200:
            rec("api", full, False, f"{r.status_code} {r.text[:100]}")
            continue
        try:
            body = r.json()
        except Exception:
            rec("api", full, False, "不是 JSON")
            continue
        ok = bool(body.get("ok"))
        detail = "ok" if ok else f"ok=false {body.get('error')}"
        # 空数据也是合法的，但 data 不能是 None
        if ok and body.get("data") is None:
            ok, detail = False, "data 为空"
        rec("api", full, ok, detail)


# ---------------- 3. 边界参数 ----------------
def check_bad_params():
    cases = [
        ("不存在的数据集", 400, f"/api/dashboard/summary?dataset_id=ds_not_exist"),
        ("数据集空串（回落到默认）", 200, "/api/dashboard/summary?dataset_id="),
        ("limit 负数", None, f"/api/comments?dataset_id={DS}&limit=-5"),
        ("limit 超大", None, f"/api/comments?dataset_id={DS}&limit=99999"),
        ("limit 非数字", None, f"/api/comments?dataset_id={DS}&limit=abc"),
        ("offset 负数", None, f"/api/comments?dataset_id={DS}&offset=-10"),
        ("倒序时间区间", None, f"/api/comments?dataset_id={DS}&date_from=2026-01-01&date_to=2020-01-01"),
        ("非法日期", None, f"/api/comments?dataset_id={DS}&date_from=hello"),
        ("非法情感值", None, f"/api/comments?dataset_id={DS}&sentiment=xx"),
        ("非法维度", None, f"/api/comments?dataset_id={DS}&dimension=xx"),
        ("非法平台", None, f"/api/comments?dataset_id={DS}&platform=xx"),
        ("granularity 非法", None, f"/api/analytics/trend?dataset_id={DS}&granularity=xx"),
        ("top_n 为 0", None, f"/api/analytics/topics?dataset_id={DS}&top_n=0"),
        ("top_n 超大", None, f"/api/analytics/topics?dataset_id={DS}&top_n=9999"),
        ("评论 ref 不存在", 404, "/api/comments/cmt_not_exist"),
        ("数据集详情不存在", 404, "/api/datasets/ds_not_exist"),
        ("质量报告不存在", 404, "/api/datasets/ds_not_exist/quality"),
        ("报告详情不存在", 404, "/api/reports/rpt_not_exist"),
        ("报告导出不存在", 404, "/api/reports/rpt_not_exist/export.md"),
        ("rag 空查询", None, "/api/rag/search?q="),
    ]
    for name, want, path in cases:
        try:
            r = get(path)
        except Exception as e:
            rec("edge", name, False, f"请求异常 {e}")
            continue
        if r.status_code >= 500:
            rec("edge", name, False, f"{r.status_code} 服务端崩溃 {r.text[:120]}")
            continue
        try:
            body = r.json()
        except Exception:
            rec("edge", name, False, f"{r.status_code} 响应不是 JSON")
            continue
        # 判据只有一条：不 5xx、响应是 JSON、4xx 必须带人话。
        # 400 和 200 都是正确行为——参数错了就该拒，参数能兜底就该兜。
        if want is not None and r.status_code != want:
            rec("edge", name, False, f"{r.status_code} 期望 {want}")
            continue
        if r.status_code >= 400:
            msg = str((body.get("error") or {}).get("message") or "")
            if not msg:
                rec("edge", name, False, f"{r.status_code} 但没给可读错误信息")
                continue
            rec("edge", name, True, f"{r.status_code} 拒绝：{msg[:44]}")
        else:
            rec("edge", name, True, "200 降级处理")


# ---------------- 3b. 工单接口边界 ----------------
# S2 上线时这 11 个端点没进体检清单，结果"工单挂了"只能靠人点页面才发现。
# 这里只打两类请求：**只读**，和**必然失败且不会落库**的写——
# health_check 跑在演示库上，不能为了测边界往真库里塞脏工单。
def check_ticket_edge():
    if not tickets_on():
        rec("edge", "工单模块未开启（CP_TICKETS=0）", True, "跳过工单边界用例")
        return
    cases = [
        ("工单不存在", "GET", "/api/tickets/tk_not_exist", None, 404),
        ("工单 id 超长", "GET", "/api/tickets/" + "x" * 200, None, 404),
        ("列表 limit 负数", "GET", "/api/tickets?limit=-5", None, None),
        ("列表 limit 非数字", "GET", "/api/tickets?limit=abc", None, None),
        ("列表 offset 负数", "GET", "/api/tickets?offset=-10", None, None),
        ("列表非法 status", "GET", "/api/tickets?status=xx", None, None),
        ("列表非法 priority", "GET", "/api/tickets?priority=xx", None, None),
        ("趋势 days 非数字", "GET", "/api/tickets/trend?days=abc", None, None),
        ("趋势 days 负数", "GET", "/api/tickets/trend?days=-5", None, None),
        ("建议：不存在的数据集", "GET", "/api/tickets/suggestions?dataset_id=ds_not_exist", None, 400),
        ("统计：不存在的数据集", "GET", "/api/tickets/stats?dataset_id=ds_not_exist", None, None),
        ("建单：空 body", "POST", "/api/tickets", {}, 400),
        ("建单：标题超长", "POST", "/api/tickets", {"title": "长" * 5000}, 400),
        ("建单：非法优先级", "POST", "/api/tickets", {"title": "t", "priority": "P9"}, 400),
        ("迁移：不存在的工单", "POST", "/api/tickets/tk_not_exist/transition",
         {"action": "triage"}, 404),
        ("评论：不存在的工单", "POST", "/api/tickets/tk_not_exist/comment",
         {"note": "x"}, None),
        ("建议建单：缺 rule_id", "POST", "/api/tickets/from-suggestion", {}, 400),
    ]
    for name, method, path, payload, want in cases:
        try:
            r = post(path, json=payload) if method == "POST" else get(path)
        except Exception as e:
            rec("edge", name, False, f"请求异常 {e}")
            continue
        if r.status_code >= 500:
            rec("edge", name, False, f"{r.status_code} 服务端崩溃 {r.text[:120]}")
            continue
        try:
            body = r.json()
        except Exception:
            rec("edge", name, False, f"{r.status_code} 响应不是 JSON")
            continue
        if want is not None and r.status_code != want:
            rec("edge", name, False, f"{r.status_code} 期望 {want}")
            continue
        if r.status_code >= 400:
            msg = str((body.get("error") or {}).get("message") or "")
            rec("edge", name, bool(msg),
                f"{r.status_code} 拒绝：{msg[:44]}" if msg else "4xx 但没给可读错误信息")
        else:
            rec("edge", name, True, "200 降级处理")


# ---------------- 4. 写流程 ----------------
def check_report_flow():
    r = post("/api/reports/generate", json={"dataset_id": DS})
    if r.status_code != 200:
        rec("flow", "生成报告", False, f"{r.status_code} {r.text[:120]}")
        return
    rep = r.json()["data"]
    rid = rep.get("report_id")
    rec("flow", "生成报告", bool(rid), f"{rid} engine={rep.get('engine')}")

    md = get(f"/api/reports/{rid}/export.md")
    rec("flow", "导出 Markdown", md.status_code == 200 and len(md.content) > 500,
        f"{md.status_code} {len(md.content)} bytes")
    if md.status_code == 200:
        txt = md.content.decode("utf-8")
        bad = [w for w in ("None", "nan", "{", "}") if w in txt]
        rec("flow", "报告无占位符残留", not bad, f"含 {bad}" if bad else "干净")
        rec("flow", "附件名 UTF-8 编码",
            "filename*=UTF-8''" in md.headers.get("Content-Disposition", ""),
            md.headers.get("Content-Disposition", "")[:50])

    # 生成两次：report_id 不能撞，列表能查到
    r2 = post("/api/reports/generate", json={"dataset_id": DS})
    rid2 = (r2.json().get("data") or {}).get("report_id") if r2.status_code == 200 else None
    rec("flow", "报告 id 不重复", bool(rid2) and rid2 != rid, f"{rid} / {rid2}")
    lst = get("/api/reports").json()["data"]
    n = len(lst.get("reports") or lst if isinstance(lst, dict) else lst)
    rec("flow", "报告列表可查", n >= 2, f"{n} 份")


def check_agent():
    r = post("/api/agent/chat", json={"question": "宿舍住宿最近负面集中在哪些方面", "dataset_id": DS})
    if r.status_code != 200:
        rec("flow", "AI 问答", False, f"{r.status_code} {r.text[:150]}")
        return
    d = r.json()["data"]
    ok = bool(d.get("answer")) and d.get("engine") in ("deepseek", "rules")
    rec("flow", "AI 问答", ok,
        f"engine={d.get('engine')} 工具{len(d.get('tool_steps') or [])}次 "
        f"图表{len(d.get('charts') or [])} {d.get('duration_ms')}ms")
    blob = json.dumps(d, ensure_ascii=False)
    leaks = [w for w in ("campuspulse.db", "E:\\\\", "sk-", "Traceback", "API_KEY") if w in blob]
    rec("flow", "回答不泄露内部信息", not leaks, f"含 {leaks}" if leaks else "干净")

    # 流式：必须能拿到 done，且 token 事件非空
    s = post("/api/agent/chat/stream", json={"question": "食堂最近评价怎么样", "dataset_id": DS})
    if s.status_code != 200:
        rec("flow", "流式问答", False, f"{s.status_code}")
        return
    evs = [json.loads(l[5:].strip()) for l in s.text.splitlines() if l.startswith("data:")]
    kinds = {}
    for e in evs:
        kinds[e.get("type")] = kinds.get(e.get("type"), 0) + 1
    done = [e for e in evs if e.get("type") == "done"]
    ok = bool(done) and kinds.get("token", 0) > 0
    rec("flow", "流式问答", ok, f"{len(evs)} 事件 {kinds}")


def main():
    print(f"体检目标：{BASE}\n")
    for fn in (check_pages, check_read_apis, check_bad_params, check_ticket_edge,
               check_report_flow, check_agent):
        print(f"---- {fn.__name__} ----")
        try:
            fn()
        except Exception as e:
            rec("fatal", fn.__name__, False, str(e)[:200])
        print()

    bad = [r for r in ROWS if not r[2]]
    print("=" * 70)
    print(f"共 {len(ROWS)} 项，失败 {len(bad)} 项")
    for k, n, _, d in bad:
        print(f"  !! [{k}] {n} — {d}")
    print("=" * 70)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
