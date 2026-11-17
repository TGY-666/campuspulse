# -*- coding: utf-8 -*-
"""Flask 应用与 REST API。

接口清单对齐文档 11.2；页面路由直接返回 Jinja 模板。
所有接口返回统一信封 {"ok": true, "data": ...} / {"ok": false, "error": {...}}，
前端只需处理一种结构，异常也能给出可读提示。
"""
import contextvars
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

from . import (agent, analytics, config, crawl, db, departments, etl, guard, intake,
               migrations, monthly, rag, reports, taxonomy, tenancy, tickets, toolbus, tools,
               vision, warmup)

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
    {"id": "dashboard", "path": "/", "name": "舆情总览", "code": "PAGE-01"},
    # 数据洞察中心拆成「总览 + 三个分析视角」。
    # 原先 11 张图挤在一页里约 2100px 高，而且主题/情感/时间三条线索互相穿插，
    # 读者不知道该先看什么。拆开后每页只回答一个问题，页面本身就是阅读顺序。
    # 平台饼图、长度分布、点赞概况、平台指标表这几张既不是主题也不是情感时间，
    # 留在总览页作为「数据画像」——顺带让总览页不是空壳目录。
    # 注意：舆情总览已有「双平台概况」（dashboard.html），那四张图不要往总览页搬，
    # 否则同一个 platform-compare 接口会在两个页面各画一遍，表格 id 还会撞。
    {"id": "insights", "path": "/insights", "name": "数据洞察中心", "code": "PAGE-03",
     "children": [
         {"id": "insights-topics", "path": "/insights/topics", "name": "主题热度分析", "code": "3.1"},
         {"id": "insights-sentiment", "path": "/insights/sentiment", "name": "情感分析", "code": "3.2"},
         {"id": "insights-timeline", "path": "/insights/timeline", "name": "时间趋势分析", "code": "3.3"},
     ]},
    {"id": "evidence", "path": "/evidence", "name": "热点与证据中心", "code": "PAGE-04"},
    # 数据页原来一屏塞了六个板块（数据集 / 文件导入 / 截图识别 / 质量链路 / 报告 / 历史报告），
    # 要生成报告得先滚过两屏导入区。按数据流拆开：进来（采集与导入）→ 管好（数据集与质量）
    # → 出去（报告）。父项本身就是「数据集与质量」，不另设空壳总览——
    # 空目录页比长页面更浪费时间，点进去什么都没有。
    {"id": "data", "path": "/data", "name": "数据与报告中心", "code": "PAGE-05",
     "children": [
         {"id": "data-collect", "path": "/data/collect", "name": "数据采集与导入", "code": "5.1"},
         {"id": "data-reports", "path": "/data/reports", "name": "AI 分析报告", "code": "5.2"},
     ]},
    # 研判与工单合成一个一级目录：父项（总览）回答"这事该谁管、有多急"，
    # 子项回答"这件事办到哪一步"。之前是两个并列的一级项，在工单页想问
    # "这单为什么该他们管"要跳出去，在研判页建的单一转身就找不到了——
    # 这两步本来就是同一条工作流，层级应当反映流程而不是模块。
    # 父项 path 仍指向 /departments：URL 是既有文档与截图引用的地址，不跟着改名。
    {"id": "disposal", "path": "/departments", "name": "风险研判与处置中心", "code": "PAGE-06",
     "children": [
         {"id": "tickets", "path": "/tickets", "name": "处置工单", "code": "6.1"},
     ]},
    # 问答入口放在最后：导航顺序按「先看全局 → 再看细节 → 最后提问」排。
    {"id": "agent", "path": "/agent", "name": "AI 舆情助手", "code": "PAGE-02"},
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


def tickets_readonly():
    """工单模块关掉时，是否给一版只读列表。

    CP_TICKETS=0 的用途是"这个演示态不开放写操作"，不是"假装系统里没有工单"。
    可是研判页那句「下一步在子页『处置工单』里闭环」是写在页面上的承诺，
    承诺了就得有落点：没有这一版，侧栏子项是死链，建完单（或看到别人建的单）
    无处核对，读者拿到的只有一句"地址输入有误"——那是把配置问题说成用户失误。
    只读列表让这条链能讲完，同时一个写接口都不开。
    """
    return not config.CP_TICKETS


def visible_pages():
    """导航只列当前身份真能进的页面。

    留一个点进去就 403 的入口，比不显示更糟：人会以为是系统坏了。

    「风险研判与处置中心」是两级两权限：父项（总览）要 view_analysis 或 dept_scope，
    子项（工单）要工单权限。处置员 handler 只有后者——若按"父项不可见就整枝砍掉"
    处理，他会连自己要办的工单都失去入口（工单页本身是 200，只是没有导航能到）。
    所以这种情况把工单提回一级显示，名字与代码用原来的。

    部门账号（dept）走另一条路：它只做"看归口 → 办工单"这一条链，
    舆情总览/洞察/证据/数据页对它既没有权限也没有意义。导航只留这一枝，
    而不是把所有页面都列出来再让人一个个撞 404。
    """
    dept_ok = departments.can_view()
    tk_ok = can_see_tickets()
    dept_only = tenancy.is_dept_role()
    pages = []
    for p in PAGES:
        if p["id"] != "disposal":
            if not dept_only:
                pages.append(p)
            continue
        if dept_ok:
            pages.append(dict(p, children=[
                c for c in p["children"]
                if c["id"] != "tickets" or tk_ok or tickets_readonly()]))
        elif tk_ok:
            pages.append({"id": "tickets", "path": "/tickets",
                          "name": "处置工单", "code": "PAGE-06"})
    return pages


# 部门账号能进的页面（id）。与 visible_pages 是同一件事的两个方向：
# 导航决定"看不看得见"，这里决定"直接敲地址进不进得去"。
# 只靠导航收窄是不够的——地址栏里敲一个 /insights 就绕过了。
DEPT_ALLOWED_PAGES = {"disposal", "tickets"}


def me_label(ctx):
    """顶栏那行身份文案。

    部门账号的 display_name 就是部门名，再拼一次会显示成
    「后勤保障处 · 部门账号 · 后勤保障处」——看着像系统重复渲染了。
    """
    label = config.ROLE_LABELS.get(ctx.get("role") or "", "")
    name = ctx.get("department_name") or ""
    if name and name != (ctx.get("display_name") or ""):
        label = f"{label} · {name}" if label else name
    return label


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
        # 带版本指纹（?v=文件 mtime）的请求，等价于"这个 URL 的内容永不改变"，
        # 可以吃满一年的强缓存 + immutable，浏览器连 304 都不用问。
        # 不带指纹的（外部直链、手工调试）仍只缓存 1 小时——
        # 不然改完 CSS 看不到变化，会让人怀疑是代码没生效。
        if request.args.get("v"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = \
                f"public, max-age={app.config['SEND_FILE_MAX_AGE_DEFAULT']}"
        resp.headers.pop("Expires", None)
        if resp.is_streamed:
            try:
                resp.set_data(b"".join(resp.response))
            except Exception:
                pass
        resp.direct_passthrough = False
        return resp

    @app.after_request
    def _security_headers(resp):
        """补最基本的浏览器侧防护。

        演示环境同样要加：这些头不改变页面行为，只关掉三类默认风险——
        MIME 嗅探、Referrer 外泄、被别的站点 iframe 套壳。
        不放 CSP：页面有内联主题脚本与 ECharts canvas，收紧 script-src
        需要先拆内联，收益低于这次改动的风险。
        """
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
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
        # 免登录模式下以前直接 302 回首页：演示时一句"看看登录和权限"，
        # 点进去看到的是驾驶舱，等于当场说什么都没有。现在照常渲染登录页，
        # 只是把表单换成一句"演示模式·无需登录"，页面的构图与说明仍然可讲。
        if not config.CP_AUTH:
            return render_template(
                "login.html",
                next=_safe_next(request.args.get("next")),
                version=__import__("campuspulse").__version__,
                dev=config.CP_DEV,
                demo=True,
                default_tenant=config.DEFAULT_TENANT_ID,
            )
        if tenancy.is_authed():
            return redirect(_safe_next(request.args.get("next")))
        return render_template(
            "login.html",
            next=_safe_next(request.args.get("next")),
            version=__import__("campuspulse").__version__,
            dev=config.CP_DEV,
            demo=False,
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
                     "department_id": user.get("department_id") or "",
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

    # 口令最小长度：8 位。不强制大小写/符号混排——那类规则会把用户推回
    # "Passw0rd!" 这种可预测口令，反而更差；这里只挡住 3 位、abc 这类明显过短的。
    PWD_MIN_LEN = 8

    @app.route("/api/auth/change-password", methods=["POST"])
    def api_change_password():
        """修改自己的密码。

        `users.must_change` 这个标记一直在库里、也一直返回给前端，但此前没有任何
        改密入口——等于"系统会要求你改密码，却不给你改的地方"。这个接口就是补上它。
        """
        if not config.CP_AUTH:
            return _err("当前未启用登录（CP_AUTH=0）", 400, error_code="auth_disabled")
        ctx = tenancy.current()
        if ctx.get("mode") != "auth" or not ctx.get("user_id"):
            return _err("请先登录", 401, error_code="unauthorized")

        body = request.get_json(silent=True) or {}
        old_pwd = str(body.get("old_password") or "")
        new_pwd = str(body.get("new_password") or "")

        user = tenancy.get_user_by_id(ctx["user_id"], ctx["tenant_id"])
        if not user:
            return _err("账号不存在", 401, error_code="unauthorized")
        # 必须验原密码：否则 cookie 一旦泄露就等于账号被直接接管
        if not tenancy.verify_password(old_pwd, user.get("pwd_hash")):
            tenancy.record_auth_event("pwd_change_fail", ctx, ip=_client_ip(),
                                      detail="bad_old_password")
            return _err("原密码不正确", 400, error_code="bad_password")
        if len(new_pwd) < PWD_MIN_LEN:
            return _err(f"新密码至少 {PWD_MIN_LEN} 位", 400, error_code="weak_password")
        if new_pwd == old_pwd:
            return _err("新密码不能与原密码相同", 400, error_code="weak_password")

        tenancy.set_password(ctx["user_id"], new_pwd)
        tenancy.record_auth_event("password_change", ctx, ip=_client_ip())

        # 会话里带着签发时的 must_change 快照，改完必须重签，否则页面仍显示"待改密"
        fresh = tenancy.get_user_by_id(ctx["user_id"], ctx["tenant_id"])
        resp = _ok({"msg": "密码已修改", "must_change": False})
        resp.set_cookie(
            tenancy.COOKIE_NAME, tenancy.issue_session(fresh),
            max_age=None, httponly=True, samesite="Lax", path="/",
            secure=bool(os.environ.get("CP_HTTPS") == "1"),
        )
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
            # 部门账号归口：前端顶栏显示"后勤保障处"，页面文案据此从"全校"改成"本部门"。
            # 其他角色为 null，前端不必判断角色名。
            "department": ({"id": ctx.get("department_id"),
                            "name": ctx.get("department_name") or ""}
                           if tenancy.is_dept_role() else None),
        })

    # ---------------- 页面路由 ----------------
    def _render(page_id, template, **extra):
        ctx = tenancy.current()
        # 部门账号直接敲 /、/insights、/evidence 这类地址：一律按"页面不存在"返回。
        # 不用 403——它的导航里根本没有这些页面，403 反而在提示"这里有个你不该来的地方"。
        if tenancy.is_dept_role() and page_id not in DEPT_ALLOWED_PAGES:
            return render_template("404.html", pages=visible_pages(), current="",
                                   dev=config.CP_DEV,
                                   reason="这个账号只开放「风险研判与处置中心」"
                                          "与「处置工单」，其余页面不在职责范围内。"), 404
        # 导航链接在服务端就把当前查询串带上（"口径双向流动"：驾驶舱选的筛选，
        # 点侧栏到工作台提问时 Agent 只在同一范围内回答）。
        # 客户端 syncUrl() 也会补一遍，但那是 JS 跑起来之后的事——首屏这段时间里
        # 用户点侧栏，href 里没有 dataset_id，后端只能回落到默认数据集，
        # 表现就是"切了数据集，一点别的页面又变回全量历史样本"。
        # 不想要这些条件的用户，在落地页点紧挨关键词的「清除筛选」一次清空即可。
        nav_query = ("?" + request.query_string.decode("utf-8")) if request.query_string else ""
        return render_template(template, pages=visible_pages(), current=page_id, nav_query=nav_query,
                               version=__import__("campuspulse").__version__,
                               # 子页面包屑要判断"父项这个人能不能进"：
                               # 处置员（handler）没有 view_analysis，给他一个指向 404 的
                               # 「风险研判与处置中心」链接，等于在页面上放了个坏入口。
                               dept_page=departments.can_view(),
                               # 开发模式标记：左栏底部的引擎状态、版本号、快照声明只给开发者看，
                               # 正式使用时这些是杂讯，而且会暴露模型供应商与技术栈
                               dev=config.CP_DEV,
                               me=ctx,
                               # 顶栏待办角标：先按权限占住位置，数字由 core.js 再填。
                               # 不占位的话它在数据回来时宽 90px 地出现，1280 长标题页
                               # 会把顶栏挤成两行（/evidence 实测 88 → 107px）。
                               tk_badge=can_see_tickets(),
                               # 部门账号顶栏显示「部门账号 · 后勤保障处」：
                               # 光写"部门账号"等于没说清这个账号管哪一摊，
                               # 演示时换账号最容易被问的就是"这是哪个部门"。
                               me_role_label=(me_label(ctx)
                                              if ctx.get("mode") == "auth" else ""),
                               # 工单只在演示态是只读的：页面文案要跟着少承诺一件事，
                               # 不该写着"点建单"而按钮根本不存在
                               tk_readonly=tickets_readonly(),
                               # 页面级的额外上下文（只读工单列表等）由调用方按需传，
                               # 不给每个模板都塞一堆用不上的变量
                               **extra)

    @app.route("/")
    def page_dashboard():
        # 工单概览条由 JS 在首帧后插入；先告诉模板这个人能不能看，
        # 才能用同高度的占位条把位置占住，避免首屏内容整体下移。
        return _render("dashboard", "dashboard.html", ticket_strip=can_see_tickets())

    @app.route("/agent")
    def page_agent():
        return _render("agent", "agent.html")

    @app.route("/insights")
    def page_insights():
        return _render("insights", "insights.html")

    # 洞察中心三个分析视角。父项 current 传子页 id，侧边栏据此高亮子项、
    # 同时让父项知道自己处在展开状态（见 base.html 的 nav-children）。
    @app.route("/insights/topics")
    def page_insights_topics():
        return _render("insights-topics", "insights_topics.html")

    @app.route("/insights/sentiment")
    def page_insights_sentiment():
        return _render("insights-sentiment", "insights_sentiment.html")

    @app.route("/insights/timeline")
    def page_insights_timeline():
        return _render("insights-timeline", "insights_timeline.html")

    @app.route("/evidence")
    def page_evidence():
        return _render("evidence", "evidence.html")

    @app.route("/data")
    def page_data():
        # 父项 = 数据集与质量：切数据集、看清洗链路、重算。这三件事回答
        # "我现在手上的数据到底是什么"，是后面两页的前提。
        return _render("data", "data.html")

    @app.route("/data/collect")
    def page_data_collect():
        return _render("data-collect", "data_collect.html")

    @app.route("/data/reports")
    def page_data_reports():
        return _render("data-reports", "data_reports.html")

    @app.route("/tickets")
    def page_tickets():
        # 演示态（CP_TICKETS=0）：给一版只读列表，让"研判 → 建单 → 核对"这条
        # 叙事有落点。写接口仍然一个都不开，页面上写清"仅展示"。
        if tickets_readonly():
            return _render("tickets", "tickets_readonly.html",
                           readonly_rows=tickets.readonly_list())
        # 直接敲地址也要挡：页面对只读角色是空壳，进去只会看到"加载失败"
        if not can_see_tickets():
            return render_template("404.html", pages=visible_pages(), current="",
                                   dev=config.CP_DEV,
                                   reason="当前账号没有工单权限"
                                          "（需要 ticket_manage / ticket_create / ticket_handle 之一）。"), 404
        return _render("tickets", "tickets.html")

    @app.route("/departments")
    def page_departments():
        if not departments.can_view():
            return render_template("404.html", pages=visible_pages(), current="",
                                   dev=config.CP_DEV,
                                   reason="当前账号没有研判权限（需要 view_analysis 或部门归属）。"), 404
        # current 传父项 id：侧栏据此高亮「风险研判与处置中心」本身（总览页），
        # 子项「处置工单」传 tickets，两者互不干扰。
        return _render("disposal", "departments.html")

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
        minimal = {
            "status": "ok",
            "version": __import__("campuspulse").__version__,
            "server_time": db.now_str(),
        }
        # 这个路径在 PUBLIC_PATHS 里（登录页要用它判断服务是否活着），
        # 所以未认证时只回最小信息。完整版含服务器绝对路径、模型供应商、
        # 租户数与权限模式，匿名访问者没有知道的必要。
        if config.CP_AUTH and ctx.get("mode") != "auth":
            return _ok(minimal)

        leak = db.leak_stats()
        # 工具通道里带的是本机绝对路径，对外只保留脚本名，排障够用且不暴露部署结构
        channel = dict(toolbus.stats())
        if channel.get("server"):
            channel["server"] = os.path.basename(str(channel["server"]))
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
            # 工具通道：local / mcp。mcp 模式下把失败与降级次数也报出来——
            # "支持 MCP"和"这一轮实际走的是 MCP"是两件事，health 里必须能分辨。
            "tool_channel": channel,
            "vision": {"enabled": bool(config.CP_VISION),
                       "engine": vision.engine_status().get("active")},
            # 实时采集：能不能跑取决于三件事——开关、playwright、平台登录态。
            # 排障时"页面上写着引擎就绪但采不到数据"多半是登录态过期，
            # 这一行能直接把问题定到具体环节。
            "crawl": crawl.health(),
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

    # ---------------- 实时采集（P3 · 真实平台直连）----------------
    # 四个状态机动作分开：发起 / 轮询 / 停止 / 清洗 / 入库。
    # 不做成"一个接口跑到底"——采集本身要几十秒，同步返回会让页面像卡死；
    # 而清洗必须是**人看完候选集之后**的独立动作，否则"清洗"就退化成
    # "采集成功后自动发生的事"，页面上那一步复核也就没有意义了。
    def _crawl_err(e, default=400):
        code = {"not_found": 404, "busy": 429, "forbidden": 403,
                "disabled": 503}.get(e.code, default)
        return _err(str(e), code, detail=getattr(e, "detail", None), error_code=e.code)

    @app.route("/api/crawl/status")
    def crawl_status():
        # 不给 403：没有导入权限的角色也要能看见"这功能存在但你不能用"，
        # 否则页面上会是一片空白，看起来像功能坏了。
        return _ok(crawl.engine_status())

    @app.route("/api/crawl/login/start", methods=["POST"])
    def crawl_login_start():
        """打开有头浏览器登录窗口：用户扫码/手机号登录，我们只等 cookie。

        不做账号密码表单——密码一旦进我们的接口，就得过我们的日志与数据库，
        而收益只是少点一次窗口。这条边界写在这里，避免以后被"顺手优化掉"。
        """
        if not crawl.can_collect():
            return _err("当前角色没有数据导入权限", 403, error_code="forbidden")
        body = request.get_json(silent=True) or {}
        try:
            return _ok(crawl.start_login(body.get("platform")))
        except crawl.CrawlError as e:
            return _crawl_err(e)

    @app.route("/api/crawl/login/status")
    def crawl_login_state():
        return _ok(crawl.login_status())

    @app.route("/api/crawl/login/focus", methods=["POST"])
    def crawl_login_focus():
        try:
            return _ok(crawl.focus_login())
        except crawl.CrawlError as e:
            return _crawl_err(e)

    @app.route("/api/crawl/login/cancel", methods=["POST"])
    def crawl_login_cancel():
        return _ok(crawl.cancel_login())

    @app.route("/api/crawl/login", methods=["POST"])
    def crawl_login_save():
        if not crawl.can_collect():
            return _err("当前角色没有数据导入权限", 403, error_code="forbidden")
        body = request.get_json(silent=True) or {}
        try:
            info = crawl.save_login(body.get("platform"), body.get("cookie"))
        except crawl.CrawlError as e:
            return _crawl_err(e)
        return _ok({"login": info, "status": crawl.engine_status()})

    @app.route("/api/crawl/login", methods=["DELETE"])
    def crawl_login_clear():
        if not crawl.can_collect():
            return _err("当前角色没有数据导入权限", 403, error_code="forbidden")
        platform = request.args.get("platform")
        return _ok({"status": crawl.clear_login(platform), "engine": crawl.engine_status()})

    @app.route("/api/crawl/start", methods=["POST"])
    def crawl_start():
        if not crawl.can_collect():
            return _err("当前角色没有数据导入权限，不能发起采集", 403, error_code="forbidden")
        body = request.get_json(silent=True) or {}
        try:
            sess = crawl.start(body.get("keyword"), body.get("platforms"),
                               body.get("target"), body.get("max_seconds"),
                               body.get("date_from"), body.get("date_to"))
        except crawl.CrawlError as e:
            return _crawl_err(e)
        return _ok(sess)

    @app.route("/api/crawl/<sid>")
    def crawl_session(sid):
        try:
            return _ok(crawl.view(sid, _int_arg("cursor", 0)))
        except crawl.CrawlError as e:
            return _crawl_err(e)

    @app.route("/api/crawl/<sid>/stop", methods=["POST"])
    def crawl_stop(sid):
        try:
            return _ok(crawl.stop(sid))
        except crawl.CrawlError as e:
            return _crawl_err(e)

    @app.route("/api/crawl/<sid>/clean", methods=["POST"])
    def crawl_clean(sid):
        if not crawl.can_collect():
            return _err("当前角色没有数据导入权限", 403, error_code="forbidden")
        try:
            return _ok(crawl.clean(sid))
        except crawl.CrawlError as e:
            return _crawl_err(e)

    @app.route("/api/crawl/<sid>/rows")
    def crawl_rows(sid):
        try:
            return _ok(crawl.records_view(sid, request.args.get("status") or "all",
                                          _int_arg("offset", 0), _int_arg("limit", 50)))
        except crawl.CrawlError as e:
            return _crawl_err(e)

    @app.route("/api/crawl/<sid>/commit", methods=["POST"])
    def crawl_commit(sid):
        if not crawl.can_collect():
            return _err("当前角色没有数据导入权限", 403, error_code="forbidden")
        body = request.get_json(silent=True) or {}
        try:
            return _ok(crawl.commit(sid, body.get("name"), body.get("target_dataset_id"),
                                    body.get("select_mode"), body.get("refs")))
        except crawl.CrawlError as e:
            return _crawl_err(e)

    # ---------------- 导入批次（文件 / 截图共用）----------------
    # 三步分开，和实时采集同构：预览（清洗，不入库）→ 逐条核对 → 确认 / 放弃。
    # 不做成"上传即入库"：清洗会剔掉重复、短评和非校园主题，用户得先看见剔了谁，
    # 才有资格决定这批要不要成为分析口径。
    def _intake_status(e):
        return {"not_found": 404, "already_committed": 409, "forbidden": 403}.get(e.code, 400)

    @app.route("/api/import/files", methods=["POST"])
    @app.route("/api/datasets/import", methods=["POST"])   # 旧路径：语义已改成"生成待确认批次"
    def import_preview():
        if not crawl.can_collect():
            return _err("当前角色没有数据导入权限", 403, error_code="forbidden")
        files = request.files.getlist("files") or request.files.getlist("file")
        uploaded = [(f.filename, f.read()) for f in files if f and f.filename]
        try:
            return _ok(intake.create_files(uploaded))
        except intake.IntakeError as e:
            return _err(str(e), _intake_status(e), detail=e.detail, error_code=e.code)

    @app.route("/api/import/<sid>/rows")
    def import_rows(sid):
        try:
            return _ok(intake.records_view(sid, request.args.get("status") or "all",
                                           _int_arg("offset", 0), _int_arg("limit", 50)))
        except intake.IntakeError as e:
            return _err(str(e), _intake_status(e), error_code=e.code)

    @app.route("/api/import/<sid>/commit", methods=["POST"])
    def import_commit(sid):
        if not crawl.can_collect():
            return _err("当前角色没有数据导入权限", 403, error_code="forbidden")
        body = request.get_json(silent=True) or {}
        try:
            return _ok(intake.commit(sid, body.get("name"), body.get("target_dataset_id"),
                                     body.get("select_mode"), body.get("refs")))
        except intake.IntakeError as e:
            return _err(str(e), _intake_status(e), error_code=e.code)

    @app.route("/api/import/<sid>/discard", methods=["POST"])
    def import_discard(sid):
        try:
            return _ok(intake.discard(sid))
        except intake.IntakeError as e:
            return _err(str(e), _intake_status(e), error_code=e.code)

    @app.route("/api/datasets/<dataset_id>/reanalyze", methods=["POST"])
    def reanalyze(dataset_id):
        """重新执行清洗（M06）：上传类数据集重跑源文件，内置数据集重建。"""
        ds = db.get_dataset(dataset_id)
        if not ds:
            return _err("数据集不存在", 404, error_code="not_found")
        preset = next((p for p in config.PRESET_DATASETS if p["dataset_id"] == dataset_id), None)
        if preset:
            paths = [os.path.join(config.BASE_DIR, f) for f in preset["files"]]
            # 时间窗口必须跟着重建走：漏传就会把窗口外的旧评论重新灌回来，
            # 而数据集名字还写着新的年份区间。
            meta, quality = etl.build_dataset(dataset_id, preset["name"], paths,
                                              preset["source_label"], preset["note"], True,
                                              date_from=preset.get("date_from"),
                                              date_to=preset.get("date_to"))
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

    @app.route("/api/datasets/<dataset_id>/rename", methods=["POST"])
    def rename_dataset(dataset_id):
        """只改展示名，不动数据、不碰 data_version。

        名字是给人看的标签（"11-10 采集的宿舍那批"），不该混进统计口径；
        但它是唯一能让演示库里那一串时间戳变得可读的东西。
        """
        if not crawl.can_collect():
            return _err("当前角色没有数据管理权限", 403, error_code="forbidden")
        ds = db.get_dataset(dataset_id)
        if not ds:
            return _err("数据集不存在", 404, error_code="not_found")
        body = request.get_json(silent=True) or {}
        name = str(body.get("name") or "").strip()
        if not name:
            return _err("数据集名称不能为空", 400)
        if len(name) > 60:
            return _err("数据集名称最多 60 个字", 400)
        if name == ds["name"]:
            return _ok({"dataset_id": dataset_id, "name": name, "changed": False})
        db.rename_dataset(dataset_id, name)
        return _ok({"dataset_id": dataset_id, "name": name, "changed": True})

    @app.route("/api/datasets/<dataset_id>", methods=["DELETE"])
    def delete_dataset(dataset_id):
        """删除数据集本身、它的评论与派生记录（报告 / AI 运行 / 研判快照）。

        至少保留一个数据集：全站页面都按"当前数据集"取数，删空之后
        每个页面都会变成 404，而用户很难从空页面判断是自己删多了。
        """
        if not crawl.can_collect():
            return _err("当前角色没有数据管理权限", 403, error_code="forbidden")
        try:
            return _ok(intake.delete_dataset(dataset_id))
        except intake.IntakeError as e:
            return _err(str(e), _intake_status(e), error_code=e.code)

    @app.route("/api/datasets/<dataset_id>/merge", methods=["POST"])
    def merge_dataset(dataset_id):
        """把 <dataset_id> 整个并入目标数据集，成功后源数据集被删除。"""
        if not crawl.can_collect():
            return _err("当前角色没有数据管理权限", 403, error_code="forbidden")
        body = request.get_json(silent=True) or {}
        try:
            return _ok(intake.merge_datasets(dataset_id, body.get("target_dataset_id")))
        except intake.IntakeError as e:
            return _err(str(e), _intake_status(e), error_code=e.code)

    @app.route("/api/datasets/restore-demo", methods=["POST"])
    def restore_demo():
        imported = etl.bootstrap_presets(force=True)
        return _ok({"restored": imported, "datasets": len(db.list_datasets())})

    # ---------------- 截图评论导入（多模态 · P1-1）----------------
    # 两步走：先识别成草稿，人工复核后再入库。
    # 为什么不合并成一个接口：OCR 一定会认错字，让识别结果直接进分析，
    # 等于把"AI 说的话可被证伪"这个卖点亲手拆掉。复核这一步是功能的一部分，
    # 不是交互上的客套。
    @app.route("/api/vision/status")
    def vision_status():
        st = vision.engine_status()
        st["max_files"] = config.VISION_MAX_FILES
        st["max_bytes"] = config.VISION_MAX_BYTES
        st["min_score"] = config.VISION_MIN_SCORE
        st["definition"] = config.DEFINITIONS["screenshot"]
        return _ok(st)

    @app.route("/api/vision/recognize", methods=["POST"])
    def vision_recognize():
        if not config.CP_VISION:
            return _err("截图识别未启用（CP_VISION=0）", 400, error_code="vision_disabled")
        files = request.files.getlist("files") or request.files.getlist("file")
        files = [f for f in files if f and f.filename]
        if not files:
            return _err("未接收到图片，请选择抖音/小红书的评论截图", 400)
        if len(files) > config.VISION_MAX_FILES:
            return _err(f"一次最多识别 {config.VISION_MAX_FILES} 张截图", 400)

        accepted, rejected = [], []
        for f in files:
            name = os.path.basename(f.filename)
            if not name.lower().endswith(vision.ALLOWED_IMAGE_EXT):
                rejected.append({"file": name, "reason": "只支持 PNG/JPG/WEBP/BMP"})
                continue
            blob = f.read()
            if not blob:
                rejected.append({"file": name, "reason": "文件为空"})
                continue
            if len(blob) > config.VISION_MAX_BYTES:
                rejected.append({"file": name,
                                 "reason": f"超过 {config.VISION_MAX_BYTES // 1024 // 1024}MB 限制"})
                continue
            accepted.append((name, blob))

        if not accepted:
            return _err("没有可识别的图片", 400, detail=rejected, error_code="invalid_file")

        try:
            payload = vision.recognize(accepted)
        except vision.VisionError as e:
            return _err(e.reason, 400, detail=e.detail, error_code="vision_unavailable")
        except Exception as e:
            logger.exception("截图识别失败")
            return _err(f"截图识别失败：{e}", 500, error_code="vision_failed")

        payload["rejected"] = rejected
        payload["session_id"] = vision.save_draft(payload)
        return _ok(payload)

    @app.route("/api/vision/preview", methods=["POST"])
    @app.route("/api/vision/commit", methods=["POST"])   # 旧路径：语义已改成"生成待确认批次"
    def vision_preview():
        """复核后的截图记录 → 导入批次（清洗 → 逐条核对 → 确认入库）。

        只接受 content / create_time / platform 三个可编辑字段——
        置信度、引擎、是否推断时间一律以草稿为准。让前端能改置信度，
        这个字段就再也证明不了任何事情了。

        这一步**不建数据集**：OCR 的结果同样要过清洗链路，
        界面噪声、重复、短句会被剔掉，得让用户先看见剔了哪些再决定要不要这批。
        """
        if not config.CP_VISION:
            return _err("截图识别未启用（CP_VISION=0）", 400, error_code="vision_disabled")
        if not crawl.can_collect():
            return _err("当前角色没有数据导入权限", 403, error_code="forbidden")
        body = request.get_json(silent=True) or {}
        try:
            draft = vision.load_draft(body.get("session_id"))
        except vision.VisionError as e:
            return _err(e.reason, 410, detail=e.detail, error_code="draft_expired")

        by_rid, by_text = {}, {}
        for r in draft.get("rows") or []:
            if r.get("rid"):
                by_rid[r["rid"]] = r
            by_text[(r.get("file"), str(r.get("content") or "").strip())] = r

        submitted = body.get("rows")
        if not isinstance(submitted, list) or not submitted:
            return _err("没有可导入的记录：请至少保留一条复核通过的评论", 400)

        rows, dropped = [], []
        for item in submitted[:config.VISION_MAX_ROWS * config.VISION_MAX_FILES]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if len(content) < vision.MIN_TEXT_LEN:
                dropped.append({"content": content, "reason": "内容过短，已跳过"})
                continue
            origin = (by_rid.get(item.get("rid"))
                      or by_text.get((item.get("file"), content)) or {})
            rows.append({
                "content": content[:vision.MAX_TEXT_LEN],
                "create_time": str(item.get("create_time") or origin.get("create_time") or ""),
                "platform": item.get("platform") or origin.get("platform") or "未知",
                "confidence": origin.get("confidence"),
                "engine": origin.get("engine"),
                "time_inferred": origin.get("time_inferred"),
                "file": item.get("file") or origin.get("file") or "",
            })

        if not rows:
            return _err("没有可导入的记录：全部内容不足 5 个字", 400, detail=dropped)

        source_rows = vision.to_source_rows(rows)
        platforms = {r["platform"] for r in source_rows}
        note = (f"共 {len(source_rows)} 条由截图识别并经人工复核的评论，"
                f"识别引擎：{vision._engine_label(draft.get('engine'))}；"
                f"平台：{'、'.join(sorted(platforms))}。"
                "截图未包含发布时间的记录按导入日期计入，需结合原始截图核对。")
        try:
            data = intake.create_records(
                source_rows,
                source_label="抖音 / 小红书评论截图（OCR 识别 + 人工复核）",
                note=note,
                name_hint=f"截图导入 · {time.strftime('%m-%d %H:%M')}",
            )
        except intake.IntakeError as e:
            return _err(str(e), _intake_status(e), detail=e.detail, error_code=e.code)

        vision.drop_draft(body.get("session_id"))
        data["submitted"] = len(rows)
        data["dropped"] = dropped
        return _ok(data)

    # ---------------- 驾驶舱 ----------------
    @app.route("/api/dashboard/summary")
    def dashboard_summary():
        f = _filters_from_request()
        return _ok(analytics.stats_overview(f))

    @app.route("/api/analytics/sentiment")
    def analytics_sentiment():
        return _ok(analytics.stats_sentiment(_filters_from_request()))

    # AI 分析结果按 (租户, 数据集, 数据版本, 筛选口径) 缓存，切页面回来不再重算。
    # refresh=1 是"我就要重算一份"：驾驶舱的「重新生成」按钮走这里。
    def _refresh_arg():
        return request.args.get("refresh") in ("1", "true", "yes")

    @app.route("/api/dashboard/insight")
    def dashboard_insight():
        f = _filters_from_request()
        return _ok(reports.dashboard_insight(f, refresh=_refresh_arg()))

    @app.route("/api/analytics/chart-insights")
    def chart_insights():
        f = _filters_from_request()
        return _ok(reports.chart_insights(f, refresh=_refresh_arg()))

    @app.route("/api/analytics/page-insight")
    def page_insight():
        """洞察三个子页各一段 AI 分析：热度成因 / 痛点与建议 / 峰值与成因。"""
        page = request.args.get("page", "")
        if page not in ("topics", "sentiment", "timeline"):
            return _err("page 取值必须是 topics / sentiment / timeline", 400, error_code="bad_param")
        return _ok(reports.page_insight(_filters_from_request(), page, refresh=_refresh_arg()))

    @app.route("/api/analytics/word-insight")
    def analytics_word_insight():
        """正 / 负两朵词云各自的解读：一侧在夸什么、另一侧在骂什么，两边能对照着看。"""
        return _ok(reports.word_cloud_insight(_filters_from_request(), refresh=_refresh_arg()))

    @app.route("/api/analytics/polarity-words")
    def analytics_polarity_words():
        """正 / 负面两朵词云：各自按各自的分母取高频词。"""
        # 30 个词是画布能舒服容纳的上限：再多就只能靠缩小字号塞进去，
        # 结果是大词不够突出、小词糊成一片，反而不如少给几个。
        return _ok(analytics.polarity_words(_filters_from_request(), _int_arg("top_n", 30)))

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
        # 同理，身份上下文也要在这里复制。teardown_request 会把租户 reset 掉，
        # 等生成器开跑时已经拿不到当前用户，chat() 里按空租户查数据集必然失败。
        stream_ctx = contextvars.copy_context()

        def gen():
            try:
                for ev in agent.chat_stream(question, dataset_id, history=history,
                                            client_key=client_key, ctx=stream_ctx):
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

    # ---------------- 学校部门与风险研判 ----------------
    # 部门是配置数据（管理员维护），研判是分析结果（能看分析的人都能看）。
    # 两个权限分开：分析员需要看"这事归谁"才能建单，但不该改部门职责。
    @app.route("/api/departments")
    def api_departments():
        return _ok({
            "departments": departments.list_all(request.args.get("all") == "1"),
            "dimensions": config.DIMENSIONS,
            "can_edit": departments.can_edit(),
            "can_create": tickets.can_create(),
            # 周带脚注要写「每工作日 N 小时」。N 从配置来，不写死在 JS 里：
            # 作息一改，写死的那句就开始说谎，而这句正是页面上解释「时限怎么算」的那句。
            "workday_hours": config.WORKDAY_HOURS,
            "disclaimer": departments.DISCLAIMER,
        })

    @app.route("/api/departments", methods=["POST"])
    def api_department_create():
        return _ok(departments.create(request.get_json(silent=True) or {}))

    @app.route("/api/departments/<dept_id>", methods=["PATCH", "PUT"])
    def api_department_update(dept_id):
        return _ok(departments.update(dept_id, request.get_json(silent=True) or {}))

    @app.route("/api/departments/<dept_id>", methods=["DELETE"])
    def api_department_archive(dept_id):
        return _ok(departments.archive(dept_id))

    @app.route("/api/departments/risk")
    def api_department_risk():
        """风险研判：主题 → 风险% → 归口部门 → 建议。只读，不建单。"""
        ai = request.args.get("ai", "1") != "0"
        return _ok(departments.assess(request.args.get("dataset_id"), use_ai=ai))

    @app.route("/api/departments/create-ticket", methods=["POST"])
    def api_department_create_ticket():
        body = request.get_json(silent=True) or {}
        return _ok(departments.create_ticket(body))

    @app.route("/api/departments/risk-range", methods=["POST"])
    def api_department_risk_range():
        """按自选区间就地研判（不落库）。

        部门账号用它跑自己那一摊：结果只含归口本部门的主题，不留全校快照。
        学校侧仍走 /api/risk-runs/run（落库、可追溯、能按快照建单）——
        两条路刻意不合并：一个要留档，一个不该留档。
        """
        body = request.get_json(silent=True) or {}
        return _ok(monthly.preview_range(date_from=body.get("date_from"),
                                         date_to=body.get("date_to"),
                                         dataset_id=body.get("dataset_id"),
                                         use_ai=body.get("ai", True) is not False))

    # ---------------- 定期研判（按时间段读评论 → 人决定要不要建单）----------------
    # 和上面的 /api/departments/risk 分开：那个是"此刻的研判"（实时、不落库），
    # 这个是"某一段时间的研判记录"（落库、可追溯、可能已经被人建了单）。
    # 合成一个接口就只能靠参数区分两种口径，前端每处调用都要记着传对。
    @app.route("/api/risk-runs")
    def api_risk_runs():
        if not departments.can_view():
            return _err("当前角色无权查看风险研判", 403, error_code="forbidden")
        # 定期研判是**全校口径**的快照（一份记录里含所有部门的主题），
        # 部门账号不读这份快照——但它要能按时间段看自己那一摊。
        # 所以这里给 preview 标记：列表为空、可跑、但跑的是 /api/departments/risk-range
        # （就地算、不落库）。前端据此换标题与按钮，不靠它做权限判断。
        if tenancy.is_dept_role():
            return _ok({"settings": monthly.settings_info(), "runs": [], "can_run": True,
                        "can_create": False, "preview": True,
                        "scoped": {"department_id": tenancy.department_id(),
                                   "department_name": tenancy.current().get("department_name") or ""},
                        "disclaimer": departments.DISCLAIMER})
        ds = request.args.get("dataset_id") or None
        try:
            limit = int(request.args.get("limit", 12))
        except (TypeError, ValueError):
            limit = 12
        return _ok({
            "settings": monthly.settings_info(),
            "runs": monthly.list_runs(limit=limit, dataset_id=ds),
            "can_run": tickets.can_create(),
            # 能跑不等于能建单：这两件事在这一版是同一个权限，但字段分开给，
            # 前端就不用靠"能跑"去猜"能不能点建单"。
            "can_create": tickets.can_create(),
            "preview": False,
            "disclaimer": departments.DISCLAIMER,
        })


    @app.route("/api/risk-runs/run", methods=["POST"])
    def api_risk_run():
        """立刻跑一次。给 date_from+date_to 就用这个区间；只给 period 就用该自然月；
        都不给就是上个月。force=true 时同区间重跑并覆盖快照。总是只研判、不建单。"""
        if tenancy.is_dept_role():
            return _err("部门账号不能发起全校研判", 403, error_code="forbidden")
        body = request.get_json(silent=True) or {}
        return _ok(monthly.run(period=body.get("period"),
                               date_from=body.get("date_from"),
                               date_to=body.get("date_to"),
                               dataset_id=body.get("dataset_id"),
                               trigger="manual",
                               force=bool(body.get("force")),
                               use_ai=body.get("ai", True) is not False))

    @app.route("/api/risk-runs/<run_id>")
    def api_risk_run_detail(run_id):
        if tenancy.is_dept_role():
            return _err("部门账号不查看全校定期研判", 403, error_code="forbidden")
        return _ok(monthly.get_run(run_id))

    @app.route("/api/risk-runs/<run_id>/create-tickets", methods=["POST"])
    def api_risk_run_tickets(run_id):
        """只按请求里点名的主题建单（dimensions 必填），没有"全部建单"这种语义。"""
        if tenancy.is_dept_role():
            return _err("部门账号没有建单权限", 403, error_code="forbidden")
        body = request.get_json(silent=True) or {}
        return _ok(monthly.create_tickets(run_id, body.get("dimensions"),
                                          note=body.get("note") or ""))

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
        scope = ({"department_id": tenancy.department_id(),
                  "department_name": tenancy.current().get("department_name") or ""}
                 if tenancy.is_dept_role() else None)
        return _ok({
            "statuses": [{"key": s, "label": tickets.STATUS_LABELS[s]} for s in tickets.ALL_STATUSES],
            "priorities": [{"key": p, "label": config.SLA_LABELS[p]} for p in config.TICKET_PRIORITIES],
            "dimensions": config.DIMENSIONS,
            "assignable": tickets.assignable_users() if tickets.can_manage() else [],
            "can_create": tickets.can_create(),
            "can_manage": tickets.can_manage(),
            "role": role,
            # 部门账号：列表、指标、角标都只算这一个部门（后端已过滤，这里只是给前端改文案）
            "scope": scope,
            "sla": {"unit": "工作小时",
                    "work_hours": "周一至周五 09:00-12:00、14:00-18:00",
                    # 前端把工作小时折算成工作天数时用它，别在前端写死 7
                    "workday_hours": config.WORKDAY_HOURS,
                    "archive": config.SLA_ARCHIVE_HOURS,
                    "first_response": config.SLA_FIRST_HOURS,
                    "holidays": sorted(config.HOLIDAYS)},
            "disclaimer": tickets.DISCLAIMER,
        })

    @app.route("/api/tickets/badge")
    def ticket_badge():
        # 角标和别的工单接口不一样：它被**所有页面**的顶栏无条件请求一次。
        # 所以工单未启用时不能返回 404——那会让每个页面的控制台都亮一条红色失败请求，
        # 前端虽然 catch 了，但网络面板看起来像页面出故障。
        # 这里按前端约定的语义返回 200 + can_view=false，由前端把角标隐藏。
        if not config.CP_TICKETS:
            # 演示态：页面本身是只读列表（见 page_tickets），所以角标与闭环条
            # 不是"没有工单"，而是"能看不能动"。给数字、标明 readonly，
            # 前端据此不再请求注定 404 的 stats。
            try:
                return _ok(tickets.readonly_badge())
            except Exception:
                return _ok({"enabled": False, "can_view": False, "mine": 0,
                            "pending": 0, "overdue": 0, "label": "", "overdue_label": ""})
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

    @app.route("/api/tickets/<ticket_id>", methods=["DELETE"])
    def ticket_delete(ticket_id):
        """删除工单（仅管理员）。审计事件保留，主体与证据关联一并清除。"""
        off = _tickets_on()
        if off:
            return off
        body = request.get_json(silent=True) or {}
        return _ok(tickets.delete(ticket_id, body.get("reason")))

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
        return render_template("404.html", pages=visible_pages(), current="",
                               dev=config.CP_DEV), 404

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
