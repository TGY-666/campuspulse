# -*- coding: utf-8 -*-
"""S0 自测：迁移、租户上下文、密码哈希、会话签名。

运行：
    .venv/Scripts/python.exe tests/test_s0.py

设计原则：本脚本**只读**正式库的结构，不写入业务数据；
密码一律现场随机生成，不在代码与输出里出现任何固定口令。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campuspulse import config, db, migrations, tenancy  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail else ""))


def main():
    print("\n== 1. 迁移结构 ==")
    st = migrations.status()
    check("结构已就绪（无待执行项）", st.get("ready"), str(st.get("pending") or ""))
    for t, ok in (st.get("tenant_columns") or {}).items():
        check(f"{t}.tenant_id 存在", ok)

    print("\n== 2. 迁移幂等 ==")
    r1 = migrations.upgrade(verbose=False)
    r2 = migrations.upgrade(verbose=False)
    check("第一次无变更", len(r1["applied"]) == 0, str(r1["applied"]))
    check("第二次无变更", len(r2["applied"]) == 0, str(r2["applied"]))

    print("\n== 3. 旧索引保留（避免现有查询退化）==")
    conn = db.raw_conn()
    idx = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    for name in migrations.LEGACY_INDEXES:
        check(f"{name} 仍在", name in idx)
    check("租户索引已建", "idx_comments_tenant_status" in idx)

    print("\n== 4. 数据未受影响 ==")
    # 这一节验的是"全库口径"：库里有没有空归属、是不是全归默认租户。
    # 这类统计本来就该跨租户，走 db.scalar 会被漏写检测拦下（那是它对的地方），
    # 所以显式用裸连接，让"我要看全库"这件事写在代码里而不是绕过检测。
    raw = db.raw_conn()

    def count(sql, args=()):
        return raw.execute(sql, args).fetchone()[0]

    n_comments = count("SELECT COUNT(*) FROM comments")
    n_datasets = count("SELECT COUNT(*) FROM datasets")
    check("comments 有数据", (n_comments or 0) > 0, f"{n_comments} 条")
    check("datasets 有数据", (n_datasets or 0) > 0, f"{n_datasets} 个")
    orphan = count("SELECT COUNT(*) FROM comments WHERE tenant_id IS NULL OR tenant_id = ''")
    check("无 tenant_id 为空的评论", (orphan or 0) == 0, f"{orphan} 条")
    others = [dict(zip(("tenant_id", "c"), r)) for r in raw.execute(
        "SELECT tenant_id, COUNT(*) c FROM comments GROUP BY tenant_id").fetchall()]
    check("所有评论归属默认租户",
          len(others) == 1 and others[0]["tenant_id"] == config.DEFAULT_TENANT_ID,
          str([(o["tenant_id"], o["c"]) for o in others]))

    print("\n== 5. 租户上下文 ==")
    anon = tenancy.anonymous()
    if config.CP_AUTH:
        # 登录模式下的匿名身份必须"什么都没有"：空租户 + 无角色。
        # 这不是刻薄，是唯一能让"没登录读不到数据"成立的做法。
        check("匿名身份无租户（登录模式）", anon["tenant_id"] == "",
              f"tenant_id={anon['tenant_id']!r}")
        check("匿名身份无角色（登录模式）", anon["role"] == "")
    else:
        check("匿名身份租户正确", anon["tenant_id"] == config.DEFAULT_TENANT_ID)
        check("匿名身份模式为 open", anon["mode"] == "open")
    tok = tenancy.set_context({**anon, "mode": "auth", "role": "viewer", "username": "probe"})
    check("set_context 生效", tenancy.current()["username"] == "probe")
    check("is_authed 为真", tenancy.is_authed())
    tenancy.reset_context(tok)
    if config.CP_AUTH:
        check("reset 后回落无权限身份", tenancy.current()["tenant_id"] == "")
    else:
        check("reset 后回落匿名", tenancy.current()["mode"] == "open")

    print("\n== 6. 密码哈希（现场随机生成，不出现固定口令）==")
    pwd = tenancy.random_password()
    hashed = tenancy.hash_password(pwd)
    check("格式为 scrypt$n$r$p$salt$hash", hashed.startswith("scrypt$16384$8$1$"))
    check("同一口令验证通过", tenancy.verify_password(pwd, hashed))
    check("错误口令被拒", not tenancy.verify_password(pwd + "x", hashed))
    check("空哈希被拒（不抛异常）", not tenancy.verify_password(pwd, ""))
    check("损坏哈希被拒（不抛异常）", not tenancy.verify_password(pwd, "garbage$1$2"))
    hashed2 = tenancy.hash_password(pwd)
    check("同口令两次加盐结果不同", hashed != hashed2)
    check("初始密码长度 12", len(pwd) == 12, pwd[:2] + "***")
    check("初始密码不含易混字符", not (set(pwd) & set("oO0l1I")))

    print("\n== 7. 会话签名 ==")
    fake = {"user_id": "u_test", "tenant_id": config.DEFAULT_TENANT_ID,
            "username": "probe", "display_name": "探针", "role": "analyst",
            "must_change": 0}
    token = tenancy.issue_session(fake)
    back = tenancy.read_session(token)
    check("会话可往返", back and back["uid"] == "u_test")
    check("未登录时返回 None（空串）", tenancy.read_session("") is None)
    check("篡改后被拒", tenancy.read_session(token[:-3] + "abc") is None)
    check("随机串被拒", tenancy.read_session("not-a-token") is None)

    print("\n== 8. 角色权限 ==")
    check("admin 可管用户", tenancy.has_perm("admin", "user_admin"))
    check("analyst 不可管用户", not tenancy.has_perm("analyst", "user_admin"))
    check("handler 可处置工单", tenancy.has_perm("handler", "ticket_handle"))
    check("handler 不可看全量分析", not tenancy.has_perm("handler", "view_analysis"))
    check("viewer 只读", tenancy.has_perm("viewer", "view_analysis")
          and not tenancy.has_perm("viewer", "gen_report"))
    check("未知角色无任何权限", not tenancy.has_perm("nobody", "view_analysis"))

    print("\n== 9. 租户单一入口 ==")
    tenants = tenancy.list_tenants()
    check("默认租户存在", any(t["tenant_id"] == config.DEFAULT_TENANT_ID for t in tenants),
          f"{len(tenants)} 个租户")

    print("\n" + "=" * 52)
    print(f"  S0 自测：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        for f in FAIL:
            print("   ✗", f)
    print("=" * 52)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
