# -*- coding: utf-8 -*-
"""Flask 应用与 REST API。

接口清单对齐文档 11.2；页面路由直接返回 Jinja 模板。
所有接口返回统一信封 {"ok": true, "data": ...} / {"ok": false, "error": {...}}，
前端只需处理一种结构，异常也能给出可读提示。
"""
import gzip
import json
import logging
import os
import shutil
import time
import uuid
from logging.handlers import RotatingFileHandler
from urllib.parse import quote

from flask import (Flask, Response, g, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_cors import CORS

from . import (agent, analytics, config, db, etl, guard, migrations, rag, reports,
               taxonomy, tenancy, tickets, tools, warmup)

logger = logging.getLogger("campuspulse")


# ============================ 通用工具 ============================
def _ok(data=None, **extra):
    payload = {"ok": True, "data": data}
    payload.update(extra)
    return jsonify(payload)


def _err(message, code=400, detail=None, error_code="bad_request"):
    return jsonify({"ok": False, "error": {
        "code": error_code, "message": message, "detail": detail}}), code


def _multi(name):
    """支持 ?platform=抖音&platform=小红书 与 ?platform=抖音,小红书 两种写法。"""
    values = []
    for raw in request.args.getlist(name):
        values += [v.strip() for v in str(raw).replace("，", ",").split(",") if v.strip()]
    return values or None


def _filters_from_request():
    return analytics.build_filters(
        dataset_id=request.args.get("dataset_id"),
        platform=_multi("platform"),
        dimension=_multi("dimension"),
        sentiment=_multi("sentiment"),
        date_from=request.args.get("date_from"),
        date_to=request.args.get("date_to"),
        keyword=request.args.get("keyword"),
    )


def _int_arg(name, default):
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


# ============================ 页面 ============================
PAGES = [
    {"id": "dashboard", "path": "/", "name": "智能驾驶舱", "code": "PAGE-01"},
    {"id": "agent", "path": "/agent", "name": "AI 智能分析工作台", "code": "PAGE-02"},
    {"id": "insights", "path": "/insights", "name": "数据洞察中心", "code": "PAGE-03"},
    {"id": "evidence", "path": "/evidence", "name": "热点与证据中心", "code": "PAGE-04"},
    {"id": "data", "path": "/data", "name": "数据与报告中心", "code": "PAGE-05"},
    {"id": "tickets", "path": "/tickets", "name": "处置工单", "code": "PAGE-06"},
]

TICKET_PERMS = ("ticket_manage", "ticket_create", "ticket_handle")


def can_see_tickets():
    """能不能进工单页。开关关掉、或角色一个工单权限都没有，就不该看见入口。"""
    if not config.CP_TICKETS:
        return False
    ctx = tenancy.current()
    if not ctx:                      # 没有身份（启动期/后台线程）时按开关走
        return True
    role = ctx.get("role") or ""
    return any(tenancy.has_perm(role, perm) for perm in TICKET_PERMS)


def visible_pages():
    """导航只列当前身份真能进的页面。

    留一个点进去就 403 的入口，比不显示更糟：人会以为是系统坏了。
    """
    if can_see_tickets():
        return PAGES
    return [p for p in PAGES if p["id"] != "tickets"]


def create_app():
    base = config.BASE_DIR
    app = Flask(__name__, static_folder=None, template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES * config.MAX_UPLOAD_FILES
    app.config["JSON_AS_ASCII"] = False
    try:
        app.json.ensure_ascii = False
    except Exception:
        pass
    CORS(app)

    # 静态资源缓存 1 小时；URL 上带文件修改时间做版本号，文件一改就自动失效
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600

    # 开发模式（CP_DEV=1）才开模板热重载：否则改完模板必须重启进程，
    # 很容易出现"代码改了但页面没变"的假象。生产/演示环境保持关闭，避免每请求比对 mtime。
    if os.environ.get("CP_DEV") == "1":
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.jinja_env.auto_reload = True

    def static_url(filename):
        path = os.path.join(base, "static", filename)
        try:
            v = int(os.path.getmtime(path))
        except OSError:
            v = int(time.time())
        return url_for("static", filename=filename, v=v)

    app.jinja_env.globals["static_url"] = static_url

    @app.route("/static/<path:filename>", endpoint="static")
    def static_files(filename):
        """自定义静态路由：把文件读进内存，才能被下面的 gzip 中间件压缩。

        Flask 默认用 send_file 的直通模式（少一次拷贝），但那样响应体是文件句柄，
        中间件拿不到内容，1MB 的 echarts.min.js 会原样明文传输。
        静态资源体积很小，读进内存的代价可以忽略。
        """
        resp = send_from_directory(os.path.join(base, "static"), filename,
                                   max_age=app.config["SEND_FILE_MAX_AGE_DEFAULT"],
                                   conditional=True)
        if resp.is_streamed:
            try:
                resp.set_data(b"".join(resp.response))
            except Exception:
                pass
        resp.direct_passthrough = False
        return resp

    @app.after_request
    def _gzip(resp):
        """零依赖 gzip：只压缩文本类响应，SSE 流与已压缩内容一律跳过。

        没有引第三方压缩中间件，是因为演示环境可能装不上包；
        这段逻辑只有十几行，行为可解释、出问题也容易定位。
        """
        try:
            if resp.mimetype == "text/event-stream":
                return resp
            if resp.is_streamed or resp.direct_passthrough:
                return resp
            if resp.status_code < 200 or resp.status_code >= 300 or resp.status_code == 204:
                return resp
            if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
                return resp
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if not any(t in ctype for t in ("text/", "json", "javascript",
                                            "image/svg", "xml", "font/")):
                return resp
            data = resp.get_data()
            if len(data) < 800:
                return resp
            packed = gzip.compress(data, 6)
            if len(packed) >= len(data) * 0.95:
                return resp
            resp.set_data(packed)
            resp.headers["Content-Encoding"] = "gzip"
            resp.headers["Content-Length"] = str(len(packed))
            resp.headers["Vary"] = "Accept-Encoding"
        except Exception:
            pass
        return resp

    _setup_logging()

    # 启动期的数据库初始化和内置数据集导入不属于任何请求，
    # 因此没有 Flask 的请求上下文，tenancy.current() 会返回"未登录"身份
    # （tenant_id 为空）。若不给它一个显式身份，导入的数据会被写成归属空租户，
    # 之后所有租户都查不到——这是必须在这里修好、而不是靠 db 层兜底的问题：
    # db 层一旦把"空租户"自动当成默认租户，未登录请求也就顺带能读默认租户的数据了。
    boot_token = tenancy.set_context(tenancy.system_context())
    try:
        db.init_db()
        if config.CP_AUTH:
            # 首次启动且库里无用户时生成管理员账号，密码写 data/initial_admin.txt
            tenancy.ensure_bootstrap_admin(verbose=True)
        try:
            etl.bootstrap_presets()
        except Exception as e:
            logger.exception("初始化内置数据集失败：%s", e)
    finally:
        tenancy.reset_context(boot_token)

    # ---------------- 身份装载 ----------------
    # 公开路径：登录页本身、登录接口、健康检查、favicon。
    # 其余一律要求登录（仅 CP_AUTH=1 时生效）。
    PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/health", "/favicon.ico"}

    def _safe_next(nxt):
        """只允许站内相对路径，挡掉 //evil.com 这类开放重定向。"""
        nxt = str(nxt or "").strip()
        if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt:
            return "/"
        return nxt[:500]

    def _client_ip():
        # 只认 remote_addr。刻意不读 X-Forwarded-For：
        # 该头可被客户端随意伪造，一旦用来做限流等于把闸门交给攻击者。
        # 部署到反向代理后置时再改成"仅信任可信代理"的白名单解析。
        return request.remote_addr or "0.0.0.0"

    @app.before_request
    def _load_identity():
        if request.path.startswith("/static/"):
            return None

        if not config.CP_AUTH:
            # v1.0 行为：不登录、不拦截，身份固定为默认租户的管理员
            g.cp_token = tenancy.set_context(tenancy.anonymous())
            return None

        token = request.cookies.get(tenancy.COOKIE_NAME)
        payload = tenancy.read_session(token) if token else None
        if payload:
            tenant = tenancy.get_tenant(payload.get("tid"))
            if tenant and tenant.get("status") == "active":
                g.cp_token = tenancy.set_context(
                    tenancy.session_to_ctx(payload, tenant.get("name") or tenant["tenant_id"]))
                return None

        # 未认证：仍然设置"无权限"身份，保证后续代码拿到的对象不为 None
        g.cp_token = tenancy.set_context(tenancy.anonymous())
        if request.path in PUBLIC_PATHS or request.path.startswith("/api/auth/"):
            return None
        if request.path.startswith("/api/"):
            return _err("请先登录", 401, error_code="unauthorized")
        nxt = request.full_path if request.query_string else request.path
        return redirect(f"/login?next={quote(nxt, safe='')}")

    @app.teardown_request
    def _drop_identity(_exc=None):
        tok = getattr(g, "cp_token", None)
        if tok is not None:
            tenancy.reset_context(tok)

    # ---------------- 登录 ----------------
    @app.route("/login")
    def login_page():
        if not config.CP_AUTH:
            return redirect("/")
        if tenancy.is_authed():
            return redirect(_safe_next(request.args.get("next")))
        return render_template(
            "login.html",
            next=_safe_next(request.args.get("next")),
            version=__import__("campuspulse").__version__,
            dev=config.CP_DEV,
            default_tenant=config.DEFAULT_TENANT_ID,
        )

    @app.route("/api/auth/login", methods=["POST"])
    def api_login():
        if not config.CP_AUTH:
            return _err("当前未启用登录（CP_AUTH=0）", 400, error_code="auth_disabled")
        body = request.get_json(silent=True) or {}
        username = str(body.get("username") or "").strip()[:64]
        password = str(body.get("password") or "")
        remember = bool(body.get("remember"))
        tenant = (str(body.get("tenant") or "").strip() or config.DEFAULT_TENANT_ID)[:40]
        ip = _client_ip()

        blocked, wait = tenancy.login_rate_blocked(ip, username)
        if blocked:
            return _err(f"尝试过于频繁，请 {wait} 秒后再试", 429, error_code="rate_limited")

        user, why = tenancy.authenticate(tenant, username, password)
        if not user:
            tenancy.record_login_failure(ip, username)
            tenancy.record_auth_event("login_fail", {"tenant_id": tenant},
                                      ip=ip, detail=why)
            # 统一文案：不区分"账号不存在"与"密码错误"，否则等于送人一个账号枚举接口
            return _err("账号或密码不正确", 401, error_code="unauthorized")

        tenancy.clear_login_failures(ip, username)
        token = tenancy.issue_session(user)
        resp = _ok({
            "user": {"id": user["user_id"], "name": user.get("display_name") or user["username"],
                     "username": user["username"], "role": user["role"],
                     "role_label": config.ROLE_LABELS.get(user["role"], user["role"]),
                     "must_change": bool(user.get("must_change"))},
            "tenant": {"id": user["tenant_id"]},
            "mode": "auth",
        })
        resp.set_cookie(
            tenancy.COOKIE_NAME, token,
            max_age=tenancy.REMEMBER_MAX_AGE if remember else None,  # None = 会话级
            httponly=True, samesite="Lax", path="/",
            secure=bool(os.environ.get("CP_HTTPS") == "1"),
        )
        try:
            db.get_conn().execute("UPDATE users SET last_login = ? WHERE user_id = ?",
                                  (db.now_str(), user["user_id"]))
            db.get_conn().commit()
        except Exception:
            pass
        tenancy.record_auth_event("login_ok", user, ip=ip)
        return resp

    @app.route("/api/auth/logout", methods=["POST"])
    def api_logout():
        if config.CP_AUTH and tenancy.is_authed():
            tenancy.record_auth_event("logout", None, ip=_client_ip())
        resp = _ok({"ok": True})
        resp.delete_cookie(tenancy.COOKIE_NAME, path="/")
        return resp

    @app.route("/api/auth/me")
    def api_me():
        ctx = tenancy.current()
        tenant = tenancy.get_tenant(ctx["tenant_id"]) if ctx["tenant_id"] else None
        return _ok({
            "mode": ctx["mode"],
            "auth_enabled": bool(config.CP_AUTH),
            "user": ({"id": ctx["user_id"], "name": ctx["display_name"],
                      "username": ctx["username"], "role": ctx["role"],
                      "role_label": config.ROLE_LABELS.get(ctx["role"], ctx["role"]),
                      "must_change": bool(ctx.get("must_change"))}
                     if ctx["mode"] == "auth" else None),
            "tenant": ({"id": tenant["tenant_id"], "name": tenant["name"],
                        "short_name": tenant.get("short_name")}
                       if tenant else None),
            "permissions": sorted(tenancy.PERMS.get(ctx["role"], set())),
        })

    # ---------------- 页面路由 ----------------
    def _render(page_id, template):
        return render_template(template, pages=visible_pages(), current=page_id,
                               version=__import__("campuspulse").__version__)

    @app.route("/")
    def page_dashboard():
        return _render("dashboard", "dashboard.html")

    @app.route("/agent")
    def page_agent():
        return _render("agent", "agent.html")

    @app.route("/insights")
    def page_insights():
        return _render("insights", "insights.html")

    @app.route("/evidence")
    def page_evidence():
        return _render("evidence", "evidence.html")

    @app.route("/data")
    def page_data():
        return _render("data", "data.html")

    @app.route("/tickets")
    def page_tickets():
        # 直接敲地址也要挡：页面对只读角色是空壳，进去只会看到"加载失败"
        if not can_see_tickets():
            return render_template("404.html", pages=visible_pages(), current=""), 404
        return _render("tickets", "tickets.html")

    @app.route("/favicon.ico")
    def favicon():
        return ("", 204)

    # ---------------- 健康检查 ----------------
    def _health_tickets():
        """排障用：一眼看出工单开关与待处置数量。

        工单表挂的是 append-only 触发器、状态机有 10 条迁移规则，
        这两件事"装没装上"从界面上看不出来，所以要能在健康检查里读到。
        """
        if not config.CP_TICKETS:
            return {"enabled": False}
        try:
            st = tickets.stats()
            return {"enabled": True, "open": st["open"], "overdue": st["overdue"],
                    "total": st["total"]}
        except Exception as e:
            return {"enabled": True, "error": str(e)[:120]}

    @app.route("/api/health")
    def health():
        ctx = tenancy.current()
        leak = db.leak_stats()
        return _ok({
            "status": "ok",
            "version": __import__("campuspulse").__version__,
            "datasets": len(db.list_datasets()),
            "llm_enabled": agent.api_available(),
            "engine": agent.ENGINE_AI if agent.api_available() else agent.ENGINE_RULES,
            "server_time": db.now_str(),
            # 下面三项是 P2 新增，用来在排障时一眼看出"现在跑的是哪套身份"
            "auth": {"enabled": bool(config.CP_AUTH), "mode": ctx["mode"],
                     "tenant": ctx["tenant_id"], "role": ctx["role"]},
            "tenant_leaks": leak["total"],
            "tenant_enforce": leak["enforce"],
            "tickets": _health_tickets(),
            # 引用核验有没有在挡：这个开关关掉时系统照常工作、界面上也照常有个角标，
            # 只是那个角标从"已核验"变成了"需复核"——不写进 health，很容易漏看。
            "citation_guard": {
                "enabled": bool(config.CP_CITATION_GUARD),
                "mode": "enforce" if config.CP_CITATION_GUARD else "record_only",
            },
        })

    # ---------------- 元数据 ----------------
    @app.route("/api/meta/definitions")
    def meta_definitions():
        return _ok({
            "definitions": config.DEFINITIONS,
            "dimensions": config.DIMENSIONS,
            "platforms": config.PLATFORMS,
            "rule_revision": taxonomy.DIMENSION_REVISION_NOTES,
            "tools": tools.tool_catalog(),
            "agent_max_tool_calls": config.AGENT_MAX_TOOL_CALLS,
            "comment_limit": config.MAX_COMMENT_LIMIT,
        })

    # ---------------- 数据集 ----------------
    @app.route("/api/datasets")
    def list_datasets():
        rows = db.list_datasets()
        data = [{
            "dataset_id": r["dataset_id"], "name": r["name"], "note": r["note"],
            "source_label": r["source_label"], "raw_count": r["raw_count"],
            "valid_count": r["valid_count"], "time_start": r["time_start"],
            "time_end": r["time_end"], "platforms": r["platforms"],
            "analyzed_at": r["analyzed_at"], "data_version": r["data_version"],
            "is_demo": bool(r["is_demo"]), "status": r["status"],
            "effective_rate": (r["quality"] or {}).get("effective_rate", 0),
        } for r in rows]
        return _ok({"datasets": data, "default": config.DEFAULT_DATASET_ID})

    @app.route("/api/datasets/<dataset_id>")
    def dataset_detail(dataset_id):
        ds = db.get_dataset(dataset_id)
        if not ds:
            return _err("数据集不存在", 404, error_code="not_found")
        return _ok(ds)

    @app.route("/api/datasets/<dataset_id>/quality")
    def dataset_quality(dataset_id):
        ds = db.get_dataset(dataset_id)
        if not ds:
            return _err("数据集不存在", 404, error_code="not_found")
        return _ok({
            "dataset_id": ds["dataset_id"], "name": ds["name"],
            "raw_count": ds["raw_count"], "valid_count": ds["valid_count"],
            "time_span": [ds["time_start"], ds["time_end"]],
            "platforms": ds["platforms"], "quality": ds["quality"],
            "effective_rate": (ds["quality"] or {}).get("effective_rate", 0),
            "definitions": {
                "raw_count": config.DEFINITIONS["raw_count"],
                "valid_count": config.DEFINITIONS["valid_count"],
                "effective_rate": config.DEFINITIONS["effective_rate"],
            },
            "analyzed_at": ds["analyzed_at"], "data_version": ds["data_version"],
        })

    @app.route("/api/datasets/select", methods=["POST"])
    def select_dataset():
        body = request.get_json(silent=True) or {}
        ds = db.get_dataset(body.get("dataset_id"))
        if not ds:
            return _err("数据集不存在", 404, error_code="not_found")
        return _ok({
            "dataset_id": ds["dataset_id"], "name": ds["name"],
            "valid_count": ds["valid_count"], "time_span": [ds["time_start"], ds["time_end"]],
            "facets": analytics.facets(ds["dataset_id"]),
            "data_version": ds["data_version"],
        })

    @app.route("/api/datasets/import", methods=["POST"])
    def import_dataset():
        files = request.files.getlist("files") or request.files.getlist("file")
        files = [f for f in files if f and f.filename]
        if not files:
            return _err("未接收到文件，请选择抖音或小红书的爬虫 JSON", 400)
        if len(files) > config.MAX_UPLOAD_FILES:
            return _err(f"一次最多上传 {config.MAX_UPLOAD_FILES} 个文件", 400)

        stamp = time.strftime("%Y%m%d_%H%M%S")
        dataset_id = f"ds_up_{stamp}_{uuid.uuid4().hex[:4]}"
        target = os.path.join(config.UPLOAD_DIR, dataset_id)
        os.makedirs(target, exist_ok=True)

        def _drop_upload_dir():
            """失败路径必须把自己建的目录清掉。

            不清理的后果是实打实的：项目里一度攒了 87 个空的 ds_up_* 目录，
            全是"清洗后无有效评论"留下的，名字还都带时间戳——
            看上去像 87 次成功导入，实际一个数据集都没有。人不敢删，因为分不清哪个在用。
            """
            try:
                shutil.rmtree(target, ignore_errors=True)
            except Exception:
                pass

        saved, rejected = [], []
        for f in files:
            name = os.path.basename(f.filename)
            if not name.lower().endswith(".json"):
                rejected.append({"file": name, "reason": "只支持 .json 格式"})
                continue
            blob = f.read()
            if len(blob) > config.MAX_UPLOAD_BYTES:
                rejected.append({"file": name, "reason": "文件超过 20MB 限制"})
                continue
            try:
                json.loads(blob.decode("utf-8-sig"))
            except Exception as e:
                rejected.append({"file": name, "reason": f"JSON 解析失败：{e}"})
                continue
            path = os.path.join(target, name)
            with open(path, "wb") as out:
                out.write(blob)
            saved.append(path)

        if not saved:
            _drop_upload_dir()
            return _err("没有可用的数据文件", 400, detail=rejected, error_code="invalid_file")

        try:
            meta, quality = etl.build_dataset(
                dataset_id, f"上传数据集 · {time.strftime('%m-%d %H:%M')}", saved,
                source_label="用户上传的爬虫导出文件", note="通过数据与报告中心导入",
                is_demo=False,
            )
        except Exception as e:
            logger.exception("导入失败")
            _drop_upload_dir()
            return _err(f"清洗失败：{e}", 500, error_code="etl_failed")

        if not meta["valid_count"]:
            db.delete_dataset(dataset_id)
            _drop_upload_dir()
            return _err(
                "清洗后没有有效评论：请确认评论内容为校园相关文本，且 create_time 可解析。",
                400, detail=quality.get("stages"), error_code="empty_dataset")

        return _ok({
            "dataset_id": dataset_id, "name": meta["name"], "raw_count": meta["raw_count"],
            "valid_count": meta["valid_count"],
            "effective_rate": quality["effective_rate"],
            "time_span": [meta["time_start"], meta["time_end"]],
            "platforms": meta["platforms"], "quality": quality, "rejected": rejected,
        })

    @app.route("/api/datasets/<dataset_id>/reanalyze", methods=["POST"])
    def reanalyze(dataset_id):
        """重新执行清洗（M06）：上传类数据集重跑源文件，内置数据集重建。"""
        ds = db.get_dataset(dataset_id)
        if not ds:
            return _err("数据集不存在", 404, error_code="not_found")
        preset = next((p for p in config.PRESET_DATASETS if p["dataset_id"] == dataset_id), None)
        if preset:
            paths = [os.path.join(config.BASE_DIR, f) for f in preset["files"]]
            meta, quality = etl.build_dataset(dataset_id, preset["name"], paths,
                                              preset["source_label"], preset["note"], True)
        else:
            folder = os.path.join(config.UPLOAD_DIR, dataset_id)
            paths = [os.path.join(folder, f) for f in sorted(os.listdir(folder))] \
                if os.path.isdir(folder) else []
            if not paths:
                return _err("该数据集的源文件已被清理，无法重新分析，请重新上传。", 400)
            meta, quality = etl.build_dataset(dataset_id, ds["name"], paths,
                                              ds["source_label"] or "", ds["note"] or "", False)
        return _ok({"dataset_id": dataset_id, "raw_count": meta["raw_count"],
                    "valid_count": meta["valid_count"], "quality": quality,
                    "analyzed_at": meta["analyzed_at"]})

    @app.route("/api/datasets/restore-demo", methods=["POST"])
    def restore_demo():
        imported = etl.bootstrap_presets(force=True)
        return _ok({"restored": imported, "datasets": len(db.list_datasets())})

    # ---------------- 驾驶舱 ----------------
    @app.route("/api/dashboard/summary")
    def dashboard_summary():
        f = _filters_from_request()
        return _ok(analytics.stats_overview(f))

    @app.route("/api/analytics/sentiment")
    def analytics_sentiment():
        return _ok(analytics.stats_sentiment(_filters_from_request()))

    @app.route("/api/dashboard/insight")
    def dashboard_insight():
        f = _filters_from_request()
        return _ok(reports.dashboard_insight(f))

    @app.route("/api/analytics/chart-insights")
    def chart_insights():
        f = _filters_from_request()
        return _ok(reports.chart_insights(f))

    @app.route("/api/analytics/topics")
    def analytics_topics():
        f = _filters_from_request()
        return _ok(analytics.stats_topics(f, _int_arg("top_n", config.DEFAULT_TOPIC_TOP_N)))

    @app.route("/api/analytics/trend")
    def analytics_trend():
        f = _filters_from_request()
        return _ok(analytics.stats_trend(f, request.args.get("granularity")))

    @app.route("/api/analytics/platform-compare")
    def analytics_platform():
        return _ok(analytics.stats_platform_compare(_filters_from_request()))

    @app.route("/api/analytics/distributions")
    def analytics_distributions():
        return _ok(analytics.stats_distributions(_filters_from_request()))

    @app.route("/api/analytics/risk")
    def analytics_risk():
        """P1 · 简易异常提示：固定阈值 + 分子分母全披露，不做事件定性。"""
        return _ok(analytics.risk_flags(_filters_from_request()))

    @app.route("/api/facets")
    def facets():
        return _ok(analytics.facets(request.args.get("dataset_id") or config.DEFAULT_DATASET_ID))

    # ---------------- 评论 / 证据 ----------------
    @app.route("/api/comments")
    def comments():
        f = _filters_from_request()
        return _ok(analytics.search_comments(
            f, limit=_int_arg("limit", config.DEFAULT_COMMENT_LIMIT),
            offset=_int_arg("offset", 0), order=request.args.get("order", "likes"),
        ))

    @app.route("/api/comments/<comment_ref>")
    def comment_detail(comment_ref):
        dataset_id = request.args.get("dataset_id") or config.DEFAULT_DATASET_ID
        row = analytics.get_comment(dataset_id, comment_ref)
        if not row:
            return _err("评论证据不存在或不在当前数据集内", 404, error_code="not_found")
        f = analytics.build_filters(dataset_id=dataset_id)
        row["filters_text"] = analytics.filters_text(f)
        row["dataset_name"] = f["dataset_name"]
        return _ok(row)

    # ---------------- Agent ----------------
    # ---------------- 输入侧安全 ----------------
    def _client_key():
        """频次限制的桶：来源 IP（反代后取 X-Forwarded-For 首段）。

        单校区私有化部署时这是靠谱的；但如果整个学校走一个出口 NAT，
        所有人会共享同一个桶——这一点写进文档，不假装它是完美方案。
        """
        fwd = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return fwd or request.remote_addr or "unknown"

    @app.route("/api/warmup")
    def warmup_now():
        """演示预热（幂等，只读）：把词典加载、检索索引、首次统计查询提前跑完。

        用 GET 是因为它不改变任何状态，重复调用结果一致；
        返回每一步耗时，答辩时可以现场演示"第二遍 12ms 全部命中缓存"。
        """
        return _ok(warmup.run())

    @app.route("/api/guard/catalog")
    def guard_catalog():
        """规则清单：只给类别与条数，不给正则（正则本身是绕过说明书）。"""
        return _ok({"categories": guard.catalog(),
                    "max_question_chars": config.AGENT_MAX_QUESTION_CHARS,
                    "rate_limit": {"per_minute": config.AGENT_RATE_LIMIT_PER_MIN},
                    "max_rounds": config.AGENT_MAX_ROUNDS})

    @app.route("/api/guard/reset", methods=["POST"])
    def guard_reset():
        """清空限流计数——只在 CP_DEV=1 下存在。

        生产环境不提供这个接口：能重置自己的限流，等于没有限流。
        演示前想清场，重启进程即可，不需要留一扇后门。
        """
        if not config.CP_DEV:
            return _err("该接口仅在开发模式（CP_DEV=1）下可用", 404, error_code="not_found")
        guard.reset_limits()
        return _ok({"reset": True, "limiter": guard.LIMITER.stats()})

    @app.route("/api/guard/stats")
    def guard_stats():
        return _ok(guard.stats(dataset_id=request.args.get("dataset_id"),
                               limit=_int_arg("limit", 20)))

    @app.route("/api/agent/tools")
    def agent_tools():
        return _ok({"tools": tools.tool_catalog(), "enabled": agent.api_available(),
                    "engine": agent.ENGINE_AI if agent.api_available() else agent.ENGINE_RULES})

    @app.route("/api/agent/chat", methods=["POST"])
    def agent_chat():
        body = request.get_json(silent=True) or {}
        question = str(body.get("question") or "").strip()
        if not question:
            return _err("请输入问题后再提问", 400, error_code="empty_question")
        # 长度/注入/越权/频次统一交给 guard：拦截结果走正常返回体，
        # 前端只渲染一张拒绝卡片，不必区分"400 错误"与"拒答"
        dataset_id = body.get("dataset_id") or config.DEFAULT_DATASET_ID
        history = body.get("history") or []
        if not isinstance(history, list):
            history = []
        try:
            result = agent.chat(question, dataset_id, history=history,
                                client_key=_client_key())
        except analytics.FilterError as e:
            return _err(str(e), 400, error_code="invalid_filter")
        except agent.AgentError as e:
            return _err(str(e), 400, error_code="agent_error")
        return _ok(result)

    @app.route("/api/agent/chat/stream", methods=["POST"])
    def agent_chat_stream():
        """SSE：把 Agent 的每一步真实执行过程推给前端（非动画）。"""
        body = request.get_json(silent=True) or {}
        question = str(body.get("question") or "").strip()
        if not question:
            return _err("问题不能为空", 400, error_code="invalid_question")
        dataset_id = body.get("dataset_id") or config.DEFAULT_DATASET_ID
        history = body.get("history") or []
        if not isinstance(history, list):
            history = []
        # 必须在返回 Response 之前取：生成器是在请求上下文弹出后才执行的，
        # 在 gen() 里调 request 会抛 "Working outside of request context"，
        # 而它会被 except 吞成一个 error 事件——看起来像"流式坏了"，其实是取错了位置
        client_key = _client_key()

        def gen():
            try:
                for ev in agent.chat_stream(question, dataset_id, history=history,
                                            client_key=client_key):
                    yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
            except Exception as e:                      # 兜底：不让生成器异常变成空响应
                yield "data: " + json.dumps(
                    {"type": "error", "message": str(e)[:300]}, ensure_ascii=False) + "\n\n"

        return Response(gen(), mimetype="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",      # 关掉反向代理缓冲，保证逐条到达
            "Connection": "keep-alive",
        })

    # ---------------- RAG 检索 ----------------
    @app.route("/api/rag/search")
    def rag_search():
        """混合检索调试接口：返回片段、三路分数与候选统计，可当众演示召回过程。"""
        q = (request.args.get("q") or "").strip()
        if not q:
            return _err("缺少检索语句参数 q", 400, error_code="empty_query")
        if len(q) > 200:
            return _err("检索语句过长，请控制在 200 字以内", 400, error_code="query_too_long")
        try:
            f = _filters_from_request()
        except analytics.FilterError as e:
            return _err(str(e), 400, error_code="invalid_filter")
        fusion = request.args.get("fusion") or "weighted"
        if fusion not in ("weighted", "rrf"):
            fusion = "weighted"
        result = rag.retrieve(q, f["dataset_id"], filters=f,
                              top_k=max(1, min(_int_arg("top_k", 6), 12)), fusion=fusion)
        result["filters_text"] = analytics.filters_text(f)
        return _ok(result)

    @app.route("/api/rag/stats")
    def rag_stats():
        dataset_id = request.args.get("dataset_id") or config.DEFAULT_DATASET_ID
        started = time.time()
        try:
            stats = rag.index_stats(dataset_id)
        except Exception as e:
            return _err(f"索引不可用：{e}", 500, error_code="rag_index_error")
        stats["served_ms"] = int((time.time() - started) * 1000)
        return _ok(stats)

    # ---------------- 报告 ----------------
    @app.route("/api/reports/generate", methods=["POST"])
    def generate_report():
        body = request.get_json(silent=True) or {}
        try:
            f = analytics.build_filters(
                dataset_id=body.get("dataset_id"),
                platform=body.get("platform"), dimension=body.get("dimension"),
                sentiment=body.get("sentiment"),
                date_from=body.get("date_from"), date_to=body.get("date_to"),
            )
        except analytics.FilterError as e:
            return _err(str(e), 400, error_code="invalid_filter")
        report = reports.generate(f, force_rule=bool(body.get("rule_only")))
        return _ok(report)

    @app.route("/api/reports")
    def report_history():
        return _ok({"reports": reports.history(request.args.get("dataset_id"),
                                               _int_arg("limit", 10))})

    @app.route("/api/reports/<report_id>/export.md")
    def report_export_md(report_id):
        """导出 Markdown：学院要交的周报是 Word，PDF 反而不好改。"""
        rep = reports.get(report_id)
        if not rep:
            return _err("报告不存在", 404, error_code="not_found")
        md = reports.to_markdown(rep)
        filename = reports.markdown_filename(rep)
        from urllib.parse import quote
        return Response(md, mimetype="text/markdown", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
        })

    @app.route("/api/reports/<report_id>")
    def report_detail(report_id):
        r = reports.get(report_id)
        if not r:
            return _err("报告不存在", 404, error_code="not_found")
        return _ok(r)

    # ---------------- 处置工单（M3）----------------
    # 开关关掉时这些路径一律 404，而不是返回空列表：
    # 空列表会让前端以为"还没建过单"，404 才说明"这个功能不存在"。
    def _tickets_on():
        return None if config.CP_TICKETS else _err("处置工单未启用（CP_TICKETS=0）", 404,
                                                   error_code="not_found")

    @app.route("/api/tickets/meta")
    def ticket_meta():
        off = _tickets_on()
        if off:
            return off
        tickets.ensure_can_view()
        role = tenancy.current().get("role")
        return _ok({
            "statuses": [{"key": s, "label": tickets.STATUS_LABELS[s]} for s in tickets.ALL_STATUSES],
            "priorities": [{"key": p, "label": config.SLA_LABELS[p]} for p in config.TICKET_PRIORITIES],
            "dimensions": config.DIMENSIONS,
            "assignable": tickets.assignable_users() if tickets.can_manage() else [],
            "can_create": tickets.can_create(),
            "can_manage": tickets.can_manage(),
            "role": role,
            "sla": {"unit": "工作小时",
                    "work_hours": "周一至周五 09:00-12:00、14:00-18:00",
                    "archive": config.SLA_ARCHIVE_HOURS,
                    "first_response": config.SLA_FIRST_HOURS,
                    "holidays": sorted(config.HOLIDAYS)},
            "disclaimer": tickets.DISCLAIMER,
        })

    @app.route("/api/tickets/badge")
    def ticket_badge():
        off = _tickets_on()
        if off:
            return off
        return _ok(tickets.dashboard_badge())

    @app.route("/api/tickets/stats")
    def ticket_stats():
        off = _tickets_on()
        if off:
            return off
        return _ok(tickets.stats())

    @app.route("/api/tickets/trend")
    def ticket_trend():
        off = _tickets_on()
        if off:
            return off
        return _ok(tickets.trend(_int_arg("days", 14)))

    @app.route("/api/tickets/suggestions")
    def ticket_suggestions():
        """规则建议：**只评估、不落库**（AC-M3-09）。"""
        off = _tickets_on()
        if off:
            return off
        return _ok(tickets.suggestions(request.args.get("dataset_id")))

    @app.route("/api/tickets/from-suggestion", methods=["POST"])
    def ticket_from_suggestion():
        off = _tickets_on()
        if off:
            return off
        return _ok(tickets.from_suggestion(request.get_json(silent=True) or {}))

    @app.route("/api/tickets", methods=["GET"])
    def ticket_list():
        off = _tickets_on()
        if off:
            return off
        params = {
            "status": _multi("status"),
            "priority": _multi("priority"),
            "status_group": request.args.get("status_group"),
            "dimension": request.args.get("dimension"),
            "mine": request.args.get("mine") in ("1", "true"),
            "q": request.args.get("q"),
            "limit": _int_arg("limit", config.TICKET_PAGE_SIZE),
            "offset": _int_arg("offset", 0),
        }
        return _ok(tickets.search(params))

    @app.route("/api/tickets", methods=["POST"])
    def ticket_create():
        off = _tickets_on()
        if off:
            return off
        return _ok(tickets.create(request.get_json(silent=True) or {}))

    @app.route("/api/tickets/<ticket_id>")
    def ticket_detail(ticket_id):
        off = _tickets_on()
        if off:
            return off
        return _ok(tickets.get(ticket_id))

    @app.route("/api/tickets/<ticket_id>/transition", methods=["POST"])
    def ticket_transition(ticket_id):
        off = _tickets_on()
        if off:
            return off
        return _ok(tickets.transition(ticket_id, request.get_json(silent=True) or {}))

    @app.route("/api/tickets/<ticket_id>/comment", methods=["POST"])
    def ticket_comment(ticket_id):
        off = _tickets_on()
        if off:
            return off
        return _ok(tickets.comment(ticket_id, request.get_json(silent=True) or {}))

    # ---------------- 处理状态 ----------------
    @app.route("/api/processing/status")
    def processing_status():
        rows = db.list_datasets()
        return _ok({
            "datasets": [{"dataset_id": r["dataset_id"], "status": r["status"],
                          "valid_count": r["valid_count"], "analyzed_at": r["analyzed_at"]}
                         for r in rows],
            "llm": {"enabled": agent.api_available(),
                    "model": config.DEEPSEEK_MODEL if agent.api_available() else None},
            "server_time": db.now_str(),
        })

    # ---------------- 错误处理 ----------------
    @app.errorhandler(analytics.FilterError)
    def handle_filter_error(e):
        return _err(str(e), 400, error_code="invalid_filter")

    @app.errorhandler(tickets.TicketError)
    def handle_ticket_error(e):
        """工单业务异常：状态码由异常自己带（404 越权 / 403 角色 / 409 跳步）。

        跳步必须返回 409 而不是 400：前端要能区分"你这一步点错了"（刷新后重试）
        和"参数填错了"（改输入）。把两者合成 400，界面就只能说"操作失败"。
        """
        payload = {"ok": False, "error": {"code": e.code, "message": str(e),
                                          "detail": e.extra or None}}
        return jsonify(payload), e.status

    @app.errorhandler(413)
    def handle_413(e):
        return _err("上传文件过大，单文件上限 20MB", 413, error_code="too_large")

    @app.errorhandler(404)
    def handle_404(e):
        if request.path.startswith("/api/"):
            return _err("接口不存在", 404, error_code="not_found")
        return render_template("404.html", pages=visible_pages(), current=""), 404

    @app.errorhandler(Exception)
    def handle_error(e):
        logger.exception("未处理异常 %s %s", request.method, request.path)
        return _err("服务器内部错误，请查看日志", 500, detail=str(e)[:200],
                    error_code="server_error")

    return app


def _setup_logging():
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    path = os.path.join(config.LOG_DIR, "campuspulse.log")
    handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
