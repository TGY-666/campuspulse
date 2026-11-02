# -*- coding: utf-8 -*-
"""轻量多租户：租户上下文、角色权限、密码哈希、签名会话。

设计取舍（对应需求文档 2.7）：

- **不做服务端 session 表**：用其 own 的 `itsdangerous` 签名 Cookie，
  少一张表、少一次查询，也不需要引入 flask-login。
- **不做权限中间件框架**：4 个角色、7 个权限点，一张字典写死比任何框架都好读。
  读代码的人一眼能答"处置员能不能导数据"。
- **密码用 scrypt 而不是 pbkdf2**：Python 标准库自带，内存硬，抗 GPU 暴力破解。

S0 阶段的生产者只有"登录"这一条路径；用户管理、改密、租户切换在 S1 补齐。
但下面的哈希、会话、权限判断都是最终形态，S1 不再改——避免"先写个假的后面重写"。
"""
import hashlib
import hmac
import os
import secrets
import string
import threading
import time
from contextvars import ContextVar
from datetime import datetime

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import config, db

# ============================ 角色与权限 ============================
# 权限点刻意做得少：够用即可，多了会变成"每个新功能都要加一个"，最后没人说得清。
PERMS = {
    # admin 必须带 ticket_handle：状态机里 admin 本来就能「开始处理 / 提交复核」，
    # 权限表不给的话两处规则打架——现在不影响，等谁加一句
    # `if not can_handle(): raise` 就会变成"管理员点不动自己的按钮"。
    "admin":   {"view_analysis", "import_data", "gen_report", "ticket_manage",
                "ticket_handle", "user_admin"},
    "analyst": {"view_analysis", "import_data", "gen_report", "ticket_create"},
    "handler": {"view_ticket_evidence", "ticket_handle"},
    "viewer":  {"view_analysis"},
}


def has_perm(role, perm):
    return perm in PERMS.get(role or "", set())


# ============================ 租户上下文 ============================
# contextvars 而不是 threading.local：Flask 是线程池执行，但未来若改用
# async worker，contextvars 不需要改代码。成本一样，选不容易过时的。
_ctx = ContextVar("cp_ctx", default=None)


_OPEN_CTX = {"tenant_id": config.DEFAULT_TENANT_ID, "tenant_name": "默认学校",
             "user_id": "u_open", "username": "open", "display_name": "本机用户",
             "role": "admin", "mode": "open", "must_change": 0}

# CP_AUTH=1 但当前请求未认证时的身份。**权限必须是空的**。
# 这是本模块最重要的一处防御：如果这里给了 admin，任何一处漏掉登录校验的接口
# 都会变成"未登录也能以管理员身份操作"，而且没有任何日志会提示这件事。
_DENY_CTX = {"tenant_id": "", "tenant_name": "", "user_id": "", "username": "",
             "display_name": "未登录", "role": "", "mode": "anon", "must_change": 0}


def anonymous():
    """CP_AUTH=0 时等价于 v1.0 的单用户；CP_AUTH=1 时是"无权限"占位身份。"""
    return dict(_DENY_CTX) if config.CP_AUTH else dict(_OPEN_CTX)


def system_context(tenant=None):
    """启动期/后台任务用的身份：归属默认租户，但**不是可登录账号**。

    单独造一个而不是复用 `_OPEN_CTX`：那个是 CP_AUTH=0 时对外暴露的"本机用户"，
    两者将来可能分道扬镳（比如系统身份要能跨租户做巡检）。
    也刻意不写成 `_DENY_CTX`——空租户写库等于把数据扔进黑洞。
    """
    return {"tenant_id": tenant or config.DEFAULT_TENANT_ID, "tenant_name": "系统",
            "user_id": "u_system", "username": "system", "display_name": "系统任务",
            "role": "admin", "mode": "system", "must_change": 0}


def current():
    """当前请求的身份。没设置过就返回匿名身份（保证 CP_AUTH=0 时永远有值）。"""
    ctx = _ctx.get()
    if ctx is None:
        return anonymous()
    return ctx


def set_context(ctx):
    return _ctx.set(ctx)


def reset_context(token):
    try:
        _ctx.reset(token)
    except Exception:
        _ctx.set(None)


def is_authed():
    return current().get("mode") == "auth"


def tenant_id():
    return current()["tenant_id"]


# ============================ 密码 ============================
_SCRYPT = dict(n=16384, r=8, p=1, dklen=32)


def hash_password(password):
    """返回 `scrypt$16384$8$1$<salt_hex>$<hash_hex>`。

    参数写进字符串而不是固定在代码里：将来要调强度时，
    老密码仍能用它自带的参数验证，不需要强制所有人改密。
    """
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(str(password).encode("utf-8"), salt=salt,
                        maxmem=64 * 1024 * 1024, **_SCRYPT)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT["n"], _SCRYPT["r"], _SCRYPT["p"], salt.hex(), dk.hex())


def verify_password(password, stored):
    try:
        algo, n, r, p, salt_hex, hash_hex = (stored or "").split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(str(password).encode("utf-8"),
                            salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p),
                            dklen=len(bytes.fromhex(hash_hex)),
                            maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def random_password(length=12):
    """随机初始密码。去掉 o/O/0/l/1/I——这些字符在纸上抄会出错。"""
    alphabet = "".join(c for c in string.ascii_letters + string.digits
                       if c not in "oO0l1I")
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ============================ 会话 ============================
COOKIE_NAME = "cp_sess"
_SALT = "cp-session-v1"
SESSION_MAX_AGE = config.SESSION_HOURS * 3600
REMEMBER_MAX_AGE = config.REMEMBER_DAYS * 86400


def _serializer():
    return URLSafeTimedSerializer(config.SECRET_KEY, salt=_SALT)


def issue_session(user, max_age=None):
    payload = {
        "uid": user["user_id"], "tid": user["tenant_id"],
        "un": user["username"], "dn": user.get("display_name") or user["username"],
        "role": user["role"], "mc": int(user.get("must_change") or 0),
        "v": 1,
    }
    return _serializer().dumps(payload)


def read_session(token):
    """解析失败一律返回 None，调用方当成"未登录"即可。

    刻意不区分"过期"与"伪造"：对用户来说结果一样（重新登录），
    区分只会让前端多写一个分支。
    """
    try:
        return _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (SignatureExpired, BadSignature):
        return None
    except Exception:
        return None


def session_to_ctx(payload, tenant_name=""):
    return {
        "tenant_id": payload["tid"], "tenant_name": tenant_name or payload["tid"],
        "user_id": payload["uid"], "username": payload.get("un", ""),
        "display_name": payload.get("dn", ""), "role": payload.get("role", "viewer"),
        "mode": "auth", "must_change": int(payload.get("mc") or 0),
    }


# ============================ 登录限流 ============================
# 进程内计数器，重启即清零。对单机部署足够；
# 多进程部署时每个 worker 各算各的，实际阈值会放大 N 倍——这一条要写进 README。
_login = {"ip": {}, "user": {}}
_login_lock = threading.Lock()


def _hit(bucket, key, window):
    now = time.time()
    arr = [t for t in _login[bucket].get(key, []) if now - t < window]
    _login[bucket][key] = arr
    return len(arr)


def login_rate_blocked(ip, username):
    """返回 (是否拦截, 剩余秒数)。"""
    with _login_lock:
        ip_n, ip_win = config.LOGIN_IP_LIMIT
        us_n, us_win = config.LOGIN_USER_LIMIT
        if _hit("ip", ip, ip_win) >= ip_n:
            return True, max(1, int(ip_win - (time.time() - _login["ip"][ip][0])))
        if username and _hit("user", username, us_win) >= us_n:
            return True, max(1, int(us_win - (time.time() - _login["user"][username][0])))
    return False, 0


def record_login_failure(ip, username):
    now = time.time()
    with _login_lock:
        _login["ip"].setdefault(ip, []).append(now)
        if username:
            _login["user"].setdefault(username, []).append(now)


def clear_login_failures(ip=None, username=None):
    with _login_lock:
        if ip:
            _login["ip"].pop(ip, None)
        if username:
            _login["user"].pop(username, None)


def reset_login_limits():
    """仅 CP_DEV=1 下由接口调用，演示时别把自己锁在外面。"""
    with _login_lock:
        _login["ip"].clear()
        _login["user"].clear()


# ============================ 审计 ============================
def ip_hash(ip):
    """只存前 16 位哈希：既能关联同一来源，又不构成可追溯的个人信息。"""
    return hashlib.sha256((ip or "").encode("utf-8")).hexdigest()[:16]


def record_auth_event(action, user=None, tenant=None, ip="", detail=""):
    try:
        db.get_conn().execute(
            "INSERT INTO auth_events (event_id, tenant_id, user_id, action, ip_hash, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("ev_" + secrets.token_hex(8),
             tenant or (user or {}).get("tenant_id") or current()["tenant_id"],
             (user or {}).get("user_id"),
             action, ip_hash(ip), str(detail)[:200],
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        db.get_conn().commit()
    except Exception:
        # 审计失败不能影响登录本身——否则日志表一出问题，全站登不进去
        pass


# ============================ 用户与租户 ============================
def get_user(tenant_id_, username):
    return db.query_one(
        "SELECT * FROM users WHERE tenant_id = ? AND username = ?",
        (tenant_id_, username))


def get_user_by_id(user_id, tenant_id_=None):
    if tenant_id_:
        return db.query_one("SELECT * FROM users WHERE user_id = ? AND tenant_id = ?",
                            (user_id, tenant_id_))
    return db.query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))


def authenticate(tenant_id_, username, password):
    """返回 (user 或 None, 失败原因)。原因只用于服务端日志，不返回给前端。"""
    user = get_user(tenant_id_, username)
    if not user:
        return None, "no_such_user"
    if user.get("status") != "active":
        return None, "disabled"
    if not verify_password(password, user.get("pwd_hash")):
        return None, "bad_password"
    return user, ""


def list_tenants():
    return db.query_all("SELECT * FROM tenants ORDER BY created_at ASC")


def get_tenant(tenant_id_):
    return db.query_one("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id_,))


def create_tenant(tenant_id_, name, short_name="", note=""):
    if not tenant_id_.startswith("t_"):
        raise ValueError("tenant_id 必须以 t_ 开头（便于和 dataset_id 区分）")
    db.get_conn().execute(
        "INSERT INTO tenants (tenant_id, name, short_name, status, note, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (tenant_id_, name, short_name or name[:6], "active", note,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    db.get_conn().commit()
    return get_tenant(tenant_id_)


def create_user(tenant_id_, username, role="viewer", display_name="",
                password=None, must_change=1):
    if role not in config.ROLES:
        raise ValueError(f"未知角色：{role}")
    pwd = password or random_password()
    user_id = "u_" + secrets.token_hex(6)
    db.get_conn().execute(
        "INSERT INTO users (user_id, tenant_id, username, display_name, role, pwd_hash, "
        "must_change, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (user_id, tenant_id_, username, display_name or username, role,
         hash_password(pwd), int(must_change), "active",
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    db.get_conn().commit()
    return {"user": get_user_by_id(user_id), "password": pwd}


def set_password(user_id, password, clear_must_change=True):
    conn = db.get_conn()
    conn.execute(
        "UPDATE users SET pwd_hash = ?, must_change = ? WHERE user_id = ?",
        (hash_password(password), 0 if clear_must_change else 1, user_id))
    conn.commit()


INITIAL_ADMIN_FILE = os.path.join(config.DATA_DIR, "initial_admin.txt")


def ensure_bootstrap_admin(verbose=True):
    """首次启动且库里一个用户都没有时，建 admin 并生成随机密码。

    密码写到 `data/initial_admin.txt`（权限 600），同时打印到控制台——
    两条路都留着，因为演示时经常看不到控制台。
    改密成功后由 S1 的改密接口删除该文件；S0 阶段它一直存在是正常的。
    """
    try:
        n = db.scalar("SELECT COUNT(*) FROM users") or 0
    except Exception:
        return None
    if n:
        return None

    tid = config.DEFAULT_TENANT_ID
    if not get_tenant(tid):
        create_tenant(tid, "默认学校", "本校", "v1.0 升级时自动创建")

    created = create_user(tid, "admin", role="admin", display_name="系统管理员",
                          must_change=1)
    pwd = created["password"]
    try:
        with open(INITIAL_ADMIN_FILE, "w", encoding="utf-8") as f:
            f.write("CampusPulse 初始管理员账号\n")
            f.write("=" * 32 + "\n")
            f.write(f"租户：{tid}\n用户名：admin\n密码：{pwd}\n")
            f.write("\n首次登录后会要求修改密码，改完请删除本文件。\n")
        os.chmod(INITIAL_ADMIN_FILE, 0o600)
    except OSError:
        pass

    if verbose:
        print("=" * 60)
        print("  已创建初始管理员账号（首次启动）")
        print(f"  用户名：admin")
        print(f"  密码：  {pwd}")
        print(f"  已写入：{os.path.relpath(INITIAL_ADMIN_FILE, config.BASE_DIR)}")
        print("=" * 60)
    return {"username": "admin", "password": pwd, "path": INITIAL_ADMIN_FILE}


# ============================ CLI ============================
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="CampusPulse 租户与用户管理（轻量版）")
    sub = ap.add_subparsers(dest="cmd")

    p_status = sub.add_parser("status", help="查看租户与用户")
    p_ct = sub.add_parser("create-tenant", help="新建租户")
    p_ct.add_argument("--id", required=True)
    p_ct.add_argument("--name", required=True)
    p_ct.add_argument("--short", default="")
    p_ct.add_argument("--admin", default="admin", help="同时建的初始管理员用户名")

    p_au = sub.add_parser("add-user", help="新建用户")
    p_au.add_argument("--tenant", default=config.DEFAULT_TENANT_ID)
    p_au.add_argument("--username", required=True)
    p_au.add_argument("--role", default="viewer", choices=config.ROLES)
    p_au.add_argument("--name", default="")

    p_rp = sub.add_parser("reset-password", help="重置密码")
    p_rp.add_argument("--tenant", default=config.DEFAULT_TENANT_ID)
    p_rp.add_argument("--username", required=True)

    args = ap.parse_args(argv)
    db.init_db()

    if args.cmd == "status":
        for t in list_tenants():
            users = db.query_all(
                "SELECT username, role, status, must_change FROM users WHERE tenant_id = ?",
                (t["tenant_id"],))
            print(f"[{t['tenant_id']}] {t['name']}  用户 {len(users)} 个")
            for u in users:
                flag = "  (待改密)" if u["must_change"] else ""
                print(f"    - {u['username']:<12} {config.ROLE_LABELS.get(u['role'], u['role'])}"
                      f" {u['status']}{flag}")
        return 0

    if args.cmd == "create-tenant":
        t = create_tenant(args.id, args.name, args.short, "CLI 创建")
        print(f"已创建租户：{t['tenant_id']} / {t['name']}")
        if args.admin:
            r = create_user(args.id, args.admin, role="admin", must_change=1)
            print(f"已创建管理员：{args.admin}  初始密码：{r['password']}")
        return 0

    if args.cmd == "add-user":
        r = create_user(args.tenant, args.username, role=args.role, display_name=args.name)
        print(f"已创建用户：{args.username}（{args.role}）  初始密码：{r['password']}")
        return 0

    if args.cmd == "reset-password":
        u = get_user(args.tenant, args.username)
        if not u:
            print("用户不存在")
            return 1
        pwd = random_password()
        set_password(u["user_id"], pwd, clear_must_change=False)
        print(f"已重置：{args.username}  新密码：{pwd}")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
