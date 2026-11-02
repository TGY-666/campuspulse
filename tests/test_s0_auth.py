# -*- coding: utf-8 -*-
"""S0 认证链路探针（需 CP_AUTH=1 运行）。

    CP_AUTH=1 .venv/Scripts/python.exe tests/test_s0_auth.py

只验证"骨架通不通"，不验证完整权限矩阵——那是 S1 的 verify_isolation.py 的活。
本脚本不会打印任何口令，只报告"能否用文件里的初始口令登录成功"。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campuspulse import config, tenancy  # noqa: E402
from campuspulse.web import create_app  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail else ""))


def read_initial_password():
    """从 bootstrap 生成的文件里读初始口令，不打印。"""
    if not os.path.isfile(tenancy.INITIAL_ADMIN_FILE):
        return None, None
    try:
        text = open(tenancy.INITIAL_ADMIN_FILE, encoding="utf-8").read()
    except OSError:
        return None, None
    user = re.search(r"用户名：(.+)", text)
    pwd = re.search(r"密码：(.+)", text)
    if not pwd:
        return None, None
    return (user.group(1).strip() if user else "admin"), pwd.group(1).strip()


def main():
    if not config.CP_AUTH:
        print("需要 CP_AUTH=1 才能跑本探针")
        return 2

    app = create_app()
    c = app.test_client()

    print("\n== 1. 未登录拦截 ==")
    for path in ("/", "/agent", "/insights", "/evidence", "/data"):
        r = c.get(path, follow_redirects=False)
        loc = r.headers.get("Location", "")
        check(f"{path} 302 到登录页", r.status_code == 302 and "/login" in loc, loc[:48])
    r = c.get("/")
    check("next 参数带回原路径", "next=%2F" in (r.headers.get("Location") or ""),
          r.headers.get("Location"))

    r = c.get("/api/datasets", follow_redirects=False)
    check("未登录调 API 返回 401", r.status_code == 401,
          str((r.get_json() or {}).get("error", {}).get("code")))

    print("\n== 2. 登录页可访问 ==")
    r = c.get("/login")
    check("登录页 200", r.status_code == 200)
    html = r.get_data(as_text=True)
    check("包含表单与脚本", 'id="loginForm"' in html and "auth.js" in html)

    print("\n== 3. 错误口令 ==")
    # 故意用运行时构造的无效口令，不在源码里留任何像口令的字面量
    wrong = "z" * 24
    r = c.post("/api/auth/login", json={"username": "admin", "password": wrong})
    body = r.get_json() or {}
    check("返回 401", r.status_code == 401)
    msg = (body.get("error") or {}).get("message", "")
    check("文案统一为「账号或密码不正确」", msg == "账号或密码不正确", msg)
    check("文案不泄露账号是否存在", "不存在" not in msg and "无此" not in msg)
    check("未设置会话 Cookie", tenancy.COOKIE_NAME not in (r.headers.get("Set-Cookie") or ""))

    print("\n== 4. 正确口令 ==")
    username, password = read_initial_password()
    if not password:
        print("  · 跳过：找不到初始口令文件（可能已改密并删除）")
    else:
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        body = r.get_json() or {}
        check("登录成功 200", r.status_code == 200, str((body.get("error") or {}).get("message")))
        cookie = r.headers.get("Set-Cookie") or ""
        check("下发会话 Cookie", tenancy.COOKIE_NAME in cookie)
        check("Cookie 带 HttpOnly", "HttpOnly" in cookie, "")
        check("Cookie 带 SameSite=Lax", "SameSite=Lax" in cookie)
        u = (body.get("data") or {}).get("user") or {}
        check("返回角色为 admin", u.get("role") == "admin")
        check("标记首登需改密", u.get("must_change") is True)

        # 用同一个 client 自动带上 Cookie
        r = c.get("/", follow_redirects=False)
        check("登录后首页 200", r.status_code == 200)
        r = c.get("/api/auth/me")
        me = r.get_json()["data"]
        check("me 返回 mode=auth", me.get("mode") == "auth")
        check("me 返回租户信息", (me.get("tenant") or {}).get("id") == config.DEFAULT_TENANT_ID)
        check("me 返回权限列表", "user_admin" in (me.get("permissions") or []))

        print("\n== 5. 登出 ==")
        r = c.post("/api/auth/logout")
        check("登出 200", r.status_code == 200)
        r = c.get("/", follow_redirects=False)
        check("登出后回到登录页", r.status_code == 302)

    print("\n== 6. 会话伪造 ==")
    c2 = app.test_client()
    c2.set_cookie(tenancy.COOKIE_NAME, "f" * 32, domain="localhost")
    r = c2.get("/", follow_redirects=False)
    check("伪造 Cookie 不被接受", r.status_code == 302 and "/login" in (r.headers.get("Location") or ""))

    print("\n" + "=" * 52)
    print(f"  认证探针：{len(PASS)} 通过 / {len(FAIL)} 失败")
    for f in FAIL:
        print("   ✗", f)
    print("=" * 52)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
