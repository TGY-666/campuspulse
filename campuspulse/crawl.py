# -*- coding: utf-8 -*-
"""实时采集（P3）：真实平台直连。

链路：真浏览器 → 搜索页 → 详情页 → 拦平台评论接口 → 原始记录
      → 清洗（与文件导入共用 etl.normalize_groups）→ 人工确认 → 入库。

三条边界写在这里，是为了后来改的人不必再试错一遍：

1. **必须带登录态**。实测（2026-11）匿名打开抖音搜索页能进，但搜索接口
   返回空、评论区不给数据——平台把"看得到页面"和"拿得到数据"分开了。
   所以未配置登录态时直接报 need_login，而不是安静地返回空结果：
   空结果会被读成"今天没人讨论"，那是错的结论。
2. **采集器只取原文，不做判断**。sentiment / dimension 全部交给 etl 打标，
   否则同一句话在导入页与采集页会得到两种情感。
3. **浏览器跑在后台线程**。该线程不碰 Flask 请求上下文，也不写库；
   落库发生在 commit（请求线程里），身份与租户由请求线程决定。
   tenancy._ctx 不跨线程继承，在线程里读身份只会拿到空对象。
"""
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import quote

from . import config, etl, tenancy

logger = logging.getLogger("campuspulse")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 平台侧拦截文案，分两类：
#   _HARD_BLOCK  真的被风控挡在门外（滑块/安全验证/频控），一定没数据；
#   _LOGIN_WALL  要求登录的提示。
# 为什么分开：抖音的登录弹窗里写着「验证码登录 / 获取验证码」，若只按
# "验证码"三个字判硬拦截，正常页面里的「获取验证码」也会被误判成验证码页。
# 登录墙只在"一条结果都没拿到"时才认——拿到数据就说明没被墙。
_HARD_BLOCK = ("请完成验证", "安全验证", "拖动滑块", "访问频繁", "操作频繁",
               "异常流量", "网络环境异常")
_LOGIN_WALL = ("登录后即可搜索", "登录后即可查看", "登录后可查看", "扫码登录",
               "请先登录", "登录后查看",
               # 小红书搜索页被墙时的原文（"登录后推荐更懂你的笔记"是它的固定文案）
               "登录后推荐更懂你的笔记", "新用户可直接登录")
# 详情页的登录墙。比搜索页多两条：小红书笔记页要求登录时，正文里是
# "登录后查看更多评论 / 手机号登录"，这几句不会出现在正常页面上。
# 只在"评论接口一次都没命中"时才用这个判据——拿到过数据就说明没被墙。
_DETAIL_LOGIN_HINTS = _LOGIN_WALL + ("登录后查看更多评论", "手机号登录",
                                     "登录后可查看全部评论")
# 详情页打不开时的提示。小红书常见的是前两条——笔记链接缺 xsec_token、
# 或该账号无权查看时，页面不是 404，而是一句"暂时无法浏览"，
# 不认这几句就只会看到"翻页 +0 条"，完全不知道发生了什么。
_NO_PAGE_HINTS = ("当前笔记暂时无法浏览", "笔记不存在", "页面不见了",
                  "内容无法查看", "该内容无法查看", "你访问的页面")

_VIDEO_RE = re.compile(r"/video/(\d{8,})")
# 小红书详情的三个入口：
#   /explore/<id>                  —— 卡片里那个隐藏的备用链接，**没有 token**，打开必 404
#   /search_result/<id>?xsec_token —— 搜索结果里真正能用的入口
#   /discovery/item/<id>           —— 老版本
# 只认前两种里的 /explore/ 会抓到一堆没有 token 的链接，表现是"71 个链接，打开全是
# 当前笔记暂时无法浏览，评论接口 0 次"。这个坑踩过，注释留在这里。
_NOTE_RE = re.compile(r"/(?:explore|discovery/item|search_result)/([0-9a-f]{16,})")

_lock = threading.Lock()
_sessions = {}          # sid -> session dict
_active = {"sid": None}  # 同一时刻只跑一个：浏览器很重，并发只会互相拖死


class CrawlError(RuntimeError):
    def __init__(self, message, code="crawl_failed", detail=None):
        super().__init__(message)
        self.code = code
        self.detail = detail


# ============================ 权限与运行环境 ============================

def _role():
    ctx = tenancy.current()
    return (ctx or {}).get("role") or ""


def can_collect():
    """采集等价于"往库里加数据"，权限跟导入对齐（admin / analyst）。"""
    return tenancy.has_perm(_role(), "import_data")


def _browser_path():
    """找一个能用的 Chromium。

    优先用 playwright 自带的（版本必须与 pip 包匹配，否则报
    "Executable doesn't exist"），匹配不上再退到系统 Chrome / Edge——
    评审机器上通常没跑过 playwright install，但一定有浏览器。
    """
    if config.CRAWL_CHROME:
        return config.CRAWL_CHROME if os.path.exists(config.CRAWL_CHROME) else None
    import glob
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    for d in sorted(glob.glob(os.path.join(base, "ms-playwright", "chromium-*")), reverse=True):
        for sub in ("chrome-win64/chrome.exe", "chrome-win/chrome.exe"):
            p = os.path.join(d, *sub.split("/"))
            if os.path.exists(p):
                return p
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              "/usr/bin/chromium", "/usr/bin/google-chrome",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if os.path.exists(p):
            return p
    return None


def _playwright_ok():
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


# ============================ 登录态 ============================
# 存本地文件而不是进程内存：采集一次要重启服务再看效果的话，
# 每次都要重新粘 cookie，实际用起来会放弃这个功能。
# 文件里就是用户自己的 cookie，权限 0600，接口只返回"已配置"与掩码提示。

def _load_auth():
    path = config.CRAWL_AUTH_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_auth(data):
    path = config.CRAWL_AUTH_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass                     # Windows 上 chmod 基本无效，不为它中断保存
    os.replace(tmp, path)


def parse_cookies(cookie_text, platform):
    """把浏览器里复制的 `k=v; k=v` 或 JSON 数组转成 add_cookies 的格式。"""
    text = str(cookie_text or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            arr = json.loads(text)
            return [{k: c[k] for k in ("name", "value", "domain", "path") if k in c}
                    for c in arr if c.get("name")]
        except Exception:
            return []
    domain = ".douyin.com" if platform == "抖音" else ".xiaohongshu.com"
    out = []
    for part in re.split(r"[;\n]", text):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if not name or name.lower() in ("path", "domain", "expires", "max-age",
                                        "samesite", "secure", "httponly"):
            continue
        out.append({"name": name, "value": value, "domain": domain, "path": "/"})
    return out


def _cookie_names(cookie_text):
    return [c["name"] for c in parse_cookies(cookie_text, "抖音")]


def save_login(platform, cookie_text):
    if platform not in config.PLATFORMS:
        raise CrawlError("只支持抖音 / 小红书两个平台", "bad_platform")
    cookies = parse_cookies(cookie_text, platform)
    if not cookies:
        raise CrawlError("没有解析出任何 cookie：需要形如 `k=v; k=v` 的内容", "bad_cookie")
    if len(cookies) > 120:
        raise CrawlError("cookie 条目过多，请只复制该平台域下的 cookie", "bad_cookie")
    names = {c["name"] for c in cookies}
    # 这两个是判定"真登录"的关键项，缺了基本等于没登录，提前说清楚比事后猜好
    key = "sessionid" if platform == "抖音" else "web_session"
    data = _load_auth()
    data[platform] = {"cookie": "; ".join(f"{c['name']}={c['value']}" for c in cookies),
                      "raw": cookies,
                      "source": "manual",
                      "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    _save_auth(data)
    return {"platform": platform, "count": len(cookies), "has_session": key in names,
            "key": key, "saved_at": data[platform]["saved_at"]}


def store_browser_cookies(platform, cookies):
    """把浏览器里拿到的 cookie 原样存下来（含每条的 domain）。

    手工粘贴只能按主域补 domain，而浏览器里同一次登录会同时写
    `.douyin.com` 与 `www.douyin.com` 两种作用域的 cookie——
    domain 补错的表现是"采集时仍然未登录"，且完全看不出为什么。
    所以扫码登录这条路直接存原始结构。
    """
    kept = [{"name": c.get("name"), "value": c.get("value"),
             "domain": c.get("domain"), "path": c.get("path") or "/"}
            for c in cookies if c.get("name") and c.get("value") not in (None, "")]
    if not kept:
        raise CrawlError("登录窗口里没有拿到任何 cookie", "empty_cookie")
    data = _load_auth()
    data[platform] = {
        "cookie": "; ".join(f"{c['name']}={c['value']}" for c in kept),
        "raw": [c for c in kept if c.get("domain")],
        "source": "browser_login",
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_auth(data)
    return len(kept)


def _mark_stale(platform):
    """记下"采集时被平台要求登录"，写进登录态文件。

    只存内存的话重启一次卡片又变回"已登录"，而 cookie 其实还是死的——
    用户看到的正是这种自相矛盾的画面（卡片写已登录、采集报登录失效）。
    """
    data = _load_auth()
    item = data.get(platform)
    if not item:
        return
    item["stale_at"] = _now()
    _save_auth(data)


def clear_login(platform):
    data = _load_auth()
    data.pop(platform, None)
    _save_auth(data)
    return {"platform": platform, "configured": False}


def _login_view(platform):
    item = _load_auth().get(platform) or {}
    cookie = item.get("cookie") or ""
    names = _cookie_names(cookie)
    key = "sessionid" if platform == "抖音" else "web_session"
    return {
        "platform": platform,
        "configured": bool(cookie),
        "has_session": key in names,
        "key": key,
        "saved_at": item.get("saved_at"),
        # 掩码只给"有几个字段、看起来像不像"，绝不回显值
        "hint": f"{len(names)} 个字段" if names else "",
        # cookie 存在 ≠ 还有效。采集时被平台挡回来，就在文件里标一个时间，
        # 否则页面显示"已登录"而采集报"登录失效"，用户不知道该信哪个。
        "stale": bool(item.get("stale_at")),
        "stale_at": item.get("stale_at"),
    }


# ============================ 扫码登录（有头浏览器） ============================
# 为什么是"弹一个有头浏览器窗口"而不是在页面里做账号密码表单：
#   1) 不碰用户密码。密码一旦进我们的表单，就要过我们的 JS、日志、数据库——
#      这是自己给自己造风险，而收益只是少点一次窗口；
#   2) 不绕验证码。要不要滑块、要不要手机二次确认，全由平台在它自己的页面里判；
#   3) 二维码元素的选择器一改版就失效，"把窗口交给用户"不依赖任何选择器。
# 代价是需要有桌面会话。没有桌面时明确报错，前端回落到手工粘贴 cookie。
_LOGIN_URL = {
    "抖音": "https://www.douyin.com/",
    "小红书": "https://www.xiaohongshu.com/login",
}
# 判定"真的登录了"的 cookie。**不能只看有没有**：
# 抖音未登录也有 ttwid，小红书未登录也下发 web_session（游客态），
# 所以判据是"这些键的值发生了变化"，见 _login_signal。
_LOGIN_KEY = {
    "抖音": ("sessionid", "sessionid_ss", "sid_guard"),
    "小红书": ("web_session",),
}

_login = {
    "platform": None,
    "state": "idle",          # idle | opening | waiting | success | timeout | failed | cancelled
    "message": "",
    "started_at": None,
    "t0": 0.0,
    "stop": False,
    "bring": False,           # 前端点"把窗口拉到前面"时置位，由工作线程执行
}
_login_lock = threading.Lock()


def _set_login(state=None, message=None):
    with _login_lock:
        if state:
            _login["state"] = state
        if message is not None:
            _login["message"] = message


def login_status():
    with _login_lock:
        st = {k: v for k, v in _login.items() if k not in ("stop", "bring", "t0")}
        t0 = _login["t0"]
    st["elapsed"] = round(time.time() - t0, 1) if t0 else 0
    st["timeout"] = config.CRAWL_LOGIN_TIMEOUT
    st["login"] = _login_view(st["platform"]) if st.get("platform") else None
    return st


def start_login(platform):
    """打开一个有头浏览器窗口等用户登录；登录成功即保存 cookie。"""
    if not config.CP_CRAWL:
        raise CrawlError("实时采集已关闭（CP_CRAWL=0）", "disabled")
    if not can_collect():
        raise CrawlError("当前角色没有数据导入权限", "forbidden")
    if platform not in config.PLATFORMS:
        raise CrawlError("只支持抖音 / 小红书两个平台", "bad_platform")
    if not _playwright_ok():
        raise CrawlError("未安装 playwright，无法打开登录窗口（可改用手工粘贴 cookie）",
                         "no_playwright")
    if not _browser_path():
        raise CrawlError("找不到可用的浏览器（可改用手工粘贴 cookie）", "no_browser")

    with _login_lock:
        if _login["state"] in ("opening", "waiting"):
            if _login["platform"] == platform:
                return login_status()          # 已经在等这个平台，直接复用，不重复开窗
            raise CrawlError("正在等待另一个平台的登录，请先取消", "busy")
        _login.update(platform=platform, state="opening",
                      message=f"正在打开{platform}登录窗口…",
                      started_at=_now(), t0=time.time(), stop=False, bring=False)
    threading.Thread(target=_login_worker, args=(platform,), daemon=True,
                     name="crawl-login").start()
    return login_status()


def focus_login():
    with _login_lock:
        if _login["state"] not in ("opening", "waiting"):
            raise CrawlError("当前没有等待中的登录窗口", "no_login")
        _login["bring"] = True
    return login_status()


def cancel_login():
    with _login_lock:
        _login["stop"] = True
        if _login["state"] in ("opening", "waiting"):
            _login["state"] = "cancelled"
            _login["message"] = "已取消登录"
    return login_status()


def _login_signal(platform, cookies, before, page):
    """登录成功的判据。

    抖音：`sessionid` 出现（游客只有 ttwid，没有 sessionid），值变化即可判。
    小红书：**不能用 cookie 变化判**——未登录时 web_session 本身就会轮换，
    实测 14 秒后就"变"了一次，会把游客态误当成登录成功。小红书登录后
    一定会离开 /login（这个页面除了登录框没有别的内容），所以看 URL。
    """
    now = {c["name"]: c.get("value") for c in cookies if c.get("name")}
    if platform == "抖音":
        return any(now.get(k) and now.get(k) != before.get(k)
                   for k in _LOGIN_KEY["抖音"])
    try:
        url = page.url or ""
    except Exception:
        return False
    if "/login" in url:
        return False
    return bool(now.get("web_session")) and now.get("web_session") != before.get("web_session")


def _open_douyin_login(page):
    """抖音首页未登录不弹二维码，得点右上角「登录」。

    选择器一旦改版就会失效——所以这里失败不算错：窗口已经开在用户面前，
    他自己点一下「登录」即可，流程不会卡死。这也是不把登录做进页面的好处：
    平台改版只会让"自动点一下"失效，不会让整个功能失效。
    """
    candidates = ('[data-e2e="login-button"]', 'button:has-text("登录")',
                  'a:has-text("登录")', 'span:has-text("登录")')
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=2500)
                page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    try:
        loc = page.get_by_text("登录", exact=True).first     # 完全匹配，避开"登录后查看"
        if loc.count() and loc.is_visible():
            loc.click(timeout=2500)
            page.wait_for_timeout(1500)
            return True
    except Exception:
        pass
    return False


def _login_worker(platform):
    browser = None
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        _set_login(state="failed", message="未安装 playwright，无法打开登录窗口")
        return
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False,                      # 必须是可见窗口，否则用户没法扫码
                executable_path=_browser_path(),
                args=["--disable-blink-features=AutomationControlled",
                      "--no-first-run", "--disable-infobars"],
            )
            ctx = browser.new_context(
                user_agent=UA, locale="zh-CN", viewport={"width": 1180, "height": 820},
                timezone_id="Asia/Shanghai",
                extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
            )
            page = ctx.new_page()
            before = {c["name"]: c.get("value") for c in ctx.cookies() if c.get("name")}
            page.goto(_LOGIN_URL[platform], wait_until="domcontentloaded", timeout=45000)
            for _ in range(3):
                try:
                    page.bring_to_front()
                except Exception:
                    break
                page.wait_for_timeout(300)
            if platform == "抖音":
                _open_douyin_login(page)
            _set_login(state="waiting",
                       message=f"请在弹出的窗口里完成{platform}登录（扫码或手机号均可）")

            deadline = time.time() + config.CRAWL_LOGIN_TIMEOUT
            hits = 0                    # 连续命中次数：一次命中可能是跳转瞬间的抖动
            while time.time() < deadline:
                if _login["stop"]:
                    break
                if _login["bring"]:
                    with _login_lock:
                        _login["bring"] = False
                    try:
                        page.bring_to_front()
                    except Exception:
                        pass
                if _login_signal(platform, ctx.cookies(), before, page):
                    hits += 1
                    if hits >= 2:
                        n = store_browser_cookies(platform, ctx.cookies())
                        # 登录瞬间平台还会补几条 cookie，等一拍再抓一次，
                        # 否则存下来的这份可能缺 msToken 之类，采集时又变成未登录。
                        page.wait_for_timeout(1500)
                        store_browser_cookies(platform, ctx.cookies())
                        _set_login(state="success", message=f"登录成功，已保存 {n} 个 cookie")
                        return
                else:
                    hits = 0
                page.wait_for_timeout(1500)
            else:
                _set_login(state="timeout",
                           message=f"{config.CRAWL_LOGIN_TIMEOUT} 秒内没检测到登录，窗口已关闭")
    except Exception as e:
        logger.exception("登录窗口异常")
        msg = str(e)
        if "Executable doesn't exist" in msg or "browserType.launch" in msg:
            msg = "登录窗口打不开：浏览器不可用（可改用手工粘贴 cookie）"
        _set_login(state="failed", message=f"登录窗口异常：{msg[:160]}")
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass


def health():
    """给 /api/health 的一行摘要：排障时一眼看出采集为什么跑不起来。

    不返回完整 engine_status：那个会扫浏览器路径、列每个平台的提示语，
    健康检查每开一页都会调一次，没必要。
    """
    cookies = _load_auth()
    return {
        "enabled": bool(config.CP_CRAWL),
        "playwright": _playwright_ok(),
        "browser": bool(_browser_path()),
        "logins": {p: bool((cookies.get(p) or {}).get("cookie")) for p in config.PLATFORMS},
    }


def engine_status():
    exe = _browser_path()
    cookies = _load_auth()
    notes = []
    if not _playwright_ok():
        notes.append("未安装 playwright：pip install playwright")
    if not exe:
        notes.append("找不到浏览器：设置 CP_CRAWL_CHROME 指向 chrome.exe")
    if not any((cookies.get(p) or {}).get("cookie") for p in config.PLATFORMS):
        notes.append("未配置平台登录态：抖音与小红书都会拦截未登录访问")
    return {
        "enabled": config.CP_CRAWL,
        "can_collect": can_collect(),
        "playwright": _playwright_ok(),
        "browser": {"path": exe, "ok": bool(exe)},
        "headful": config.CRAWL_HEADFUL,
        "limits": {"max_target": config.CRAWL_MAX_TARGET,
                   "max_seconds": config.CRAWL_MAX_SECONDS,
                   "max_pages": config.CRAWL_MAX_PAGES,
                   "scroll_rounds": config.CRAWL_SCROLL_ROUNDS,
                   "search_scrolls": config.CRAWL_SEARCH_SCROLLS},
        "logins": [_login_view(p) for p in config.PLATFORMS],
        "notes": notes,
        "active": _active.get("sid"),
        # 页面上只显示一个按钮，但窗口可能还开着——刷新后要能接着等，
        # 而不是让用户再点一次、开出第二个登录窗口。
        "login_session": login_status(),
    }


# ============================ 会话 ============================

def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _gc():
    """只留最近 8 个会话：浏览器跑一轮会留下几百条原文，攒着白占内存。"""
    if len(_sessions) <= 8:
        return
    olds = sorted(_sessions.values(), key=lambda s: s["started_at"])[:-8]
    for s in olds:
        if s["sid"] != _active.get("sid"):
            _sessions.pop(s["sid"], None)


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _norm_date(value, label):
    """规范成 YYYY-MM-DD；空串表示不限。

    只认 ISO 日期而不是"随便什么能解析的字符串"：日期一旦解析歪了，
    用户会以为自己筛了 9 月，实际筛的是 6 月，而页面上看不出任何异常。
    """
    v = str(value or "").strip()
    if not v:
        return ""
    if not _DATE_RE.match(v):
        raise CrawlError(f"{label}格式应为 YYYY-MM-DD，例如 2025-09-01", "bad_date")
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise CrawlError(f"{label}不是有效日期：{v}", "bad_date")
    return v


def _row_day(row):
    """从原始记录里取"哪一天"。解析不出来返回 None（交给清洗阶段判无效时间）。"""
    dt = etl.timestamp_to_datetime(row.get("create_time"))
    if dt is None or str(dt) == "NaT":
        return None
    try:
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def start(keyword, platforms, target=None, max_seconds=None,
          date_from=None, date_to=None):
    if not config.CP_CRAWL:
        raise CrawlError("实时采集已关闭（CP_CRAWL=0）", "disabled")
    if not can_collect():
        raise CrawlError("当前角色没有数据导入权限，不能发起采集", "forbidden")
    kw = str(keyword or "").strip()
    if not kw:
        raise CrawlError("请填写采集关键词（例如：广东东软学院 宿舍）", "bad_keyword")
    if len(kw) > 60:
        raise CrawlError("关键词过长（≤60 字）", "bad_keyword")
    plats = [p for p in (platforms or config.PLATFORMS) if p in config.PLATFORMS]
    if not plats:
        raise CrawlError("请至少选择一个平台", "bad_platform")

    d_from = _norm_date(date_from, "开始日期")
    d_to = _norm_date(date_to, "结束日期")
    if d_from and d_to and d_from > d_to:
        raise CrawlError("开始日期不能晚于结束日期", "bad_date")

    target = int(target or 60)
    target = max(10, min(target, config.CRAWL_MAX_TARGET))
    budget = int(max_seconds or config.CRAWL_MAX_SECONDS)
    budget = max(20, min(budget, config.CRAWL_MAX_SECONDS))

    stamp = time.strftime("%Y%m%d_%H%M%S")
    sid = "cw_" + uuid.uuid4().hex[:8]
    sess = {
        "sid": sid,
        "dataset_id": f"ds_live_{stamp}_{uuid.uuid4().hex[:4]}",
        "keyword": kw,
        "platforms": plats,
        "target": target,
        "plat_cap": target,      # 当前平台采到多少条就让位（多平台时按剩余目标分摊）
        "max_seconds": budget,
        "date_from": d_from,
        "date_to": d_to,
        "dropped_range": 0,     # 因不在时间段内被跳过的条数
        "raw_seen": 0,          # 平台实际吐出多少条（含被跳过的）：判断"评论区还有没有更多"
        "oldest": None,         # 见过的最早一天：用来判断"已经翻过区间了"
        "status": "running",
        "rows": [],
        "seen": set(),
        "per_platform": {p: 0 for p in plats},
        "errors": {},
        "error_codes": {},   # 每个平台各自的失败原因码（总体 status=done 时也要能看出来）
        "log": [],
        "diag": {},
        "started_at": _now(),
        "t0": time.time(),
        "ended_at": None,
        "error": None,
        "error_code": None,
        "records": None,
        "quality": None,
        "cleaned_at": None,
        "committed": None,
        "stop": False,
    }
    with _lock:
        if _active.get("sid"):
            raise CrawlError("已有一个采集任务在运行，请先等它结束或手动停止", "busy")
        _sessions[sid] = sess
        _active["sid"] = sid
    span = f" · 时间段 {d_from or '不限'} ~ {d_to or '不限'}" if (d_from or d_to) else ""
    _log(sess, f"任务创建：关键词「{kw}」· 平台 {'/'.join(plats)} · 目标 {target} 条{span}")
    threading.Thread(target=_worker, args=(sess,), daemon=True,
                     name=f"crawl-{sid}").start()
    _gc()
    return view(sid)


def _get(sid):
    sess = _sessions.get(sid)
    if not sess:
        raise CrawlError("采集任务不存在或已过期，请重新发起采集", "not_found")
    return sess


def _log(sess, msg):
    with _lock:
        sess["log"].append({"at": _now(), "msg": msg})
        if len(sess["log"]) > 40:
            sess["log"] = sess["log"][-40:]


def _count(sess):
    with _lock:
        return len(sess["rows"])


def _should_stop(sess):
    if sess["stop"]:
        return True
    if time.time() - sess["t0"] > sess["max_seconds"]:
        sess["stop"] = True
        _log(sess, f"到达安全上限 {sess['max_seconds']}s，自动收尾（正常操作碰不到这条）")
        return True
    if _count(sess) >= sess["plat_cap"]:
        return True
    return False


def _task_done(sess):
    """整个任务是否已经不需要再采（跨平台的总目标是否达上限）。

    和 _should_stop 分开：_should_stop 多了"本平台配额已满"与"用户点了停止"，
    用在采集循环里；这里只回答"总目标是否已经吃满"，用在平台轮转上。
    """
    return sess["stop"] or _count(sess) >= sess["target"]


def _add_row(sess, platform, row):
    cid = str(row.get("comment_id") or "").strip()
    text = str(row.get("content") or "").strip()
    if not text:
        return False
    day = _row_day(row)
    key = (platform, cid) if cid else (platform, text)
    with _lock:
        sess["raw_seen"] += 1
        # 时间段过滤发生在**入候选集之前**：用户选的是"我要这段时间的评论"，
        # 把范围外的先收进来再筛掉，只会让候选集里塞满马上要被丢弃的东西，
        # 用户还得在预览里自己找哪些是区间内的。
        # day 为 None（时间解析不出来）时保留，交给清洗阶段判"时间无法解析"——
        # 时间筛选不该顺手把"时间有问题"这类记录吃掉，否则质量页的分母对不上。
        if day:
            if sess["oldest"] is None or day < sess["oldest"]:
                sess["oldest"] = day
            too_early = sess["date_from"] and day < sess["date_from"]
            too_late = sess["date_to"] and day > sess["date_to"]
            if too_early or too_late:
                sess["dropped_range"] += 1
                return False
        if len(sess["rows"]) >= sess["plat_cap"]:
            return False
        if key in sess["seen"]:
            return False
        sess["seen"].add(key)
        sess["rows"].append({
            "platform": platform,
            "comment_id": cid or f"live_{len(sess['rows'])}",
            "content": text,
            "create_time": row.get("create_time"),
            "like_count": row.get("like_count"),
        })
        sess["per_platform"][platform] = sess["per_platform"].get(platform, 0) + 1
    return True


def view(sid, cursor=0):
    sess = _get(sid)
    with _lock:
        rows = [dict(r, content=etl.mask_text(r["content"]))
                for r in sess["rows"][int(cursor or 0):]]
        n = len(sess["rows"])
        started = sess["t0"]
        payload = {
            "session_id": sid,
            "status": sess["status"],
            "keyword": sess["keyword"],
            "platforms": sess["platforms"],
            "target": sess["target"],
            "collected": n,
            "cursor": n,
            "rows": rows,
            "per_platform": dict(sess["per_platform"]),
            "date_from": sess["date_from"],
            "date_to": sess["date_to"],
            "dropped_range": sess["dropped_range"],
            "raw_seen": sess["raw_seen"],
            "oldest": sess["oldest"],
            "elapsed": round(time.time() - started, 1) if sess["status"] == "running"
                       else round((sess.get("ended_at_t") or time.time()) - started, 1),
            "errors": dict(sess["errors"]),
            "error_codes": dict(sess["error_codes"]),
            "log": list(sess["log"])[-8:],
            "error": sess["error"],
            "error_code": sess["error_code"],
            "diag": dict(sess["diag"]),
            "cleaned": bool(sess["records"]),
            "committed": sess["committed"],
            "max_seconds": sess["max_seconds"],
        }
    if payload["status"] == "running":
        payload["remaining"] = max(0, int(sess["max_seconds"] - (time.time() - sess["t0"])))
    return payload


def stop(sid):
    sess = _get(sid)
    sess["stop"] = True
    _log(sess, "用户手动停止")
    return view(sid)


# ============================ 浏览器 ============================

def _launch(pw):
    exe = _browser_path()
    if not exe:
        raise CrawlError("找不到可用的 Chrome/Chromium：设置 CP_CRAWL_CHROME 指向 chrome.exe",
                         "no_browser")
    try:
        return pw.chromium.launch(
            headless=not config.CRAWL_HEADFUL,
            executable_path=exe,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-first-run", "--disable-infobars"],
        )
    except Exception as e:
        raise CrawlError(f"浏览器启动失败：{e}", "browser_failed")


def _new_context(browser, platform):
    item = _load_auth().get(platform) or {}
    cookie = (item.get("cookie") or "").strip()
    if not cookie:
        raise CrawlError(f"未配置{platform}登录态：未登录访问会被平台拦截，请先在页面上完成登录",
                         "need_login")
    ctx = browser.new_context(
        user_agent=UA, locale="zh-CN", viewport={"width": 1366, "height": 900},
        timezone_id="Asia/Shanghai",
        extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
    )
    # 扫码登录存的是浏览器原始 cookie（带各自的 domain），优先用它；
    # 手工粘贴的没有 raw，按主域补齐。
    raw = [c for c in (item.get("raw") or []) if c.get("name") and c.get("domain")]
    ctx.add_cookies(raw or parse_cookies(cookie, platform))
    return ctx


def _settle(page, seconds, sess=None):
    """等页面把评论接口发出去。用固定等待而不是 wait_for_load_state：
    评论是懒加载的，load 完成时接口还没发。

    传入 sess 时按 0.3 秒切片等待并检查停止标志：用户点「停止」后
    最多 0.3 秒就能退出，而不是把一个 5 秒的等待睡完再响应——
    那种"点了停止还要等几秒"的手感，会被当成没生效。
    """
    if sess is None:
        try:
            page.wait_for_timeout(int(seconds * 1000))
        except Exception:
            time.sleep(seconds)
        return
    end = time.time() + seconds
    while time.time() < end:
        if sess["stop"]:
            return
        step = min(0.3, max(0.05, end - time.time()))
        try:
            page.wait_for_timeout(int(step * 1000))
        except Exception:
            time.sleep(step)


def _scroll(page, dy=1400):
    try:
        page.mouse.wheel(0, dy)
    except Exception:
        pass


def _diag(page):
    """失败时把"当时页面长什么样"留下来。

    没有这段，用户只会看到"采集失败"，然后我们都不知道是被验证码挡了、
    被登录墙挡了、还是关键词根本没搜到东西。
    """
    try:
        text = page.inner_text("body")[:600].replace("\n", " ")
    except Exception:
        text = ""
    try:
        title = page.title()
    except Exception:
        title = ""
    return {"title": title, "url": page.url, "text": text}


def _guard(page, platform):
    """硬拦截检测（验证码 / 滑块 / 频控）。命中就停，不要继续翻页。"""
    d = _diag(page)
    hit = next((h for h in _HARD_BLOCK if h in d["text"]), None)
    if hit:
        raise CrawlError(
            f"{platform}返回了「{hit}」：当前 IP 被判定异常，或登录态已失效。",
            "blocked", detail=d)


def _page_missing(page):
    """详情页是不是打不开（笔记不可见 / 链接失效 / 无权限）。"""
    try:
        text = page.inner_text("body")[:900]
    except Exception:
        return ""
    return next((h for h in _NO_PAGE_HINTS if h in text), "")


def _login_wall(page):
    """页面是不是在要求登录。只在"没拿到任何结果"时调用。"""
    text = _diag(page)["text"]
    return next((w for w in _LOGIN_WALL if w in text), None)


def _detail_login_wall(page):
    """详情页是不是在要求登录。

    和 _login_wall 分开是因为详情页的文案不一样：小红书笔记页被墙时写的是
    「登录后推荐更懂你的笔记 / 手机号登录」，这些词在搜索页列表里是看不到的。
    """
    try:
        text = page.inner_text("body")[:900]
    except Exception:
        return ""
    return next((w for w in _DETAIL_LOGIN_HINTS if w in text), "")


def _unique_links(page, pattern, build=None):
    """收集页面上匹配的链接。

    build 传 None 时**保留原始 href**，不要按 id 重建 URL——
    小红书笔记从 2024 起必须带 `xsec_token` 才打得开，重建成
    `/explore/<id>` 会把 token 丢掉，详情页就变成"当前笔记暂时无法浏览"，
    评论接口自然一条都不返回。表现就是"翻了很多页但 +0 条"。
    """
    try:
        hrefs = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.href)")
    except Exception:
        hrefs = []
    out, index = [], {}
    for h in hrefs:
        m = pattern.search(h or "")
        if not m:
            continue
        key = m.group(1)
        if key in index:
            # 同一篇笔记会出现两条链接：一条隐藏的 /explore/<id>（无 token）、
            # 一条可用的 /search_result/<id>?xsec_token=...。后者才打得开。
            i = index[key]
            if "xsec_token=" in h and "xsec_token=" not in out[i]:
                out[i] = h
            continue
        index[key] = len(out)
        out.append(build(key) if build else h)
    return out


def _focus_comments(page, platform):
    """把鼠标移到评论区上再滚。

    抖音 PC 视频页是左右分栏：左边视频、右边评论。鼠标停在左边时，
    wheel 滚的是"下一个视频"——看起来在翻页，实际一条评论都没加载。
    这是"滚了很多次但没数据"最常见的原因。
    """
    if platform != "抖音":
        return                      # 小红书评论在页面下方，整页滚即可
    try:
        vp = page.viewport_size or {"width": 1366, "height": 900}
        page.mouse.move(vp["width"] * 0.74, vp["height"] * 0.62)
    except Exception:
        pass


def _gather_links(page, sess, pattern, build=None, rounds=None):
    """在搜索页边滚边收链接。

    搜索结果同样是懒加载的：只读一次 DOM 等于只采第一屏的那几个视频，
    用户说的"搜索出来的视频"根本不是这个量级。多滚几屏再收。
    """
    rounds = rounds or config.CRAWL_SEARCH_SCROLLS
    found = {}
    for i in range(rounds):
        for url in _unique_links(page, pattern, build):
            found.setdefault(url, True)
        if sess["stop"]:
            break
        if i < rounds - 1:
            _scroll(page, 2200)
            _settle(page, 1.4, sess)
    return list(found)


def _attach(page, sess, platform, counter):
    """拦评论接口。这是整条链路里唯一"能拿到数据"的入口：
    页面 DOM 里的评论是渲染结果，翻页、截断、虚拟滚动都会漏；
    接口返回的 JSON 才是原始记录。"""

    def on_response(resp):
        url = resp.url or ""
        if platform == "抖音":
            if "/aweme/v1/web/comment/list/" not in url:
                return
        elif "/api/sns/web/v2/comment/page" not in url:
            return
        counter["hits"] += 1
        try:
            data = resp.json()
        except Exception:
            return
        try:
            rows = _parse_comments(platform, data)
        except Exception:
            return
        got = 0
        for r in rows:
            if _add_row(sess, platform, r):
                got += 1
        if got:
            _log(sess, f"{platform}：本页新增 {got} 条（累计 {_count(sess)} 条）")

    page.on("response", on_response)


def _parse_comments(platform, data):
    if not isinstance(data, dict):
        return []
    if platform == "抖音":
        items = data.get("comments") or []
        rows = []
        for it in items:
            if not isinstance(it, dict):
                continue
            rows.append({
                "comment_id": str(it.get("cid") or ""),
                "content": it.get("text") or "",
                "create_time": it.get("create_time"),
                "like_count": it.get("digg_count"),
            })
        return rows
    node = data.get("data") if isinstance(data.get("data"), dict) else {}
    items = node.get("comments") or []
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        ct = it.get("create_time")
        # 小红书的 create_time 是毫秒；etl 的解析器认 13 位数字
        rows.append({
            "comment_id": str(it.get("id") or ""),
            "content": it.get("content") or "",
            "create_time": ct,
            "like_count": it.get("like_count"),
        })
    return rows


# ---------------- 抖音 ----------------

def _collect_douyin(ctx, sess):
    page = ctx.new_page()
    counter = {"hits": 0}
    _attach(page, sess, "抖音", counter)
    kw = quote(sess["keyword"])
    page.goto(f"https://www.douyin.com/search/{kw}?type=video",
              wait_until="domcontentloaded", timeout=45000)
    _settle(page, 5, sess)
    _guard(page, "抖音")
    links = _gather_links(page, sess, _VIDEO_RE)
    sess["diag"] = {"stage": "search", "links": len(links),
                    "title": _diag(page)["title"]}
    _log(sess, f"抖音搜索页：滚动 {config.CRAWL_SEARCH_SCROLLS} 屏，共 {len(links)} 个视频")
    if not links:
        wall = _login_wall(page)
        if wall:
            raise CrawlError(
                f"抖音要求登录后才能搜索（页面提示「{wall}」）：当前 cookie 无效或已过期。",
                "need_login", detail=_diag(page))
        raise CrawlError(
            "抖音搜索页没有返回视频。常见原因：关键词太窄、账号被限流、或页面结构变了。",
            "empty_search", detail=_diag(page))
    _walk_pages(page, sess, links, counter, "抖音")
    if counter["hits"] == 0:
        raise CrawlError(
            "进入了视频页但评论接口没有返回数据：登录态大概率已失效（cookie 过期或被踢下线）。",
            "no_comment_api", detail=_diag(page))


# ---------------- 小红书 ----------------

def _collect_xhs(ctx, sess):
    page = ctx.new_page()
    counter = {"hits": 0}
    _attach(page, sess, "小红书", counter)
    kw = quote(sess["keyword"])
    page.goto(f"https://www.xiaohongshu.com/search_result?keyword={kw}&source=web_search_result_notes",
              wait_until="domcontentloaded", timeout=45000)
    _settle(page, 5, sess)
    _guard(page, "小红书")
    links = _gather_links(page, sess, _NOTE_RE)
    sess["diag"] = {"stage": "search", "links": len(links)}
    _log(sess, f"小红书搜索页：滚动 {config.CRAWL_SEARCH_SCROLLS} 屏，共 {len(links)} 篇笔记")
    if not links:
        wall = _login_wall(page)
        if wall:
            raise CrawlError(
                f"小红书要求登录后才能搜索（页面提示「{wall}」）：当前 cookie 无效或已过期。",
                "need_login", detail=_diag(page))
        raise CrawlError(
            "小红书搜索页没有返回笔记。常见原因：关键词太窄、搜索被风控、或页面结构变了。",
            "empty_search", detail=_diag(page))
    _walk_pages(page, sess, links, counter, "小红书")
    if counter["hits"] == 0:
        raise CrawlError(
            "进入了笔记页但评论接口没有返回数据：登录态大概率已失效。",
            "no_comment_api", detail=_diag(page))


def _walk_pages(page, sess, links, counter, platform):
    """逐个详情页翻：打开 → 滚评论 → 结算 → 下一个，直到采够或到页数上限。

    结算刻意滞后一页：**评论接口的响应要等到下一次 `page.goto` 才被派发**，
    页面停留期间即使 `wait_for_timeout` 等 10 秒也拿不到（实测 DBG 里
    raw 一直是 19→19，而日志显示这页其实新增了 9 条，直到跳下一页才落地）。
    早先按"页末结算"写，导致每页都被判成 0 条、连续空页数一路涨到 10，
    平台在还有数据的时候被提前关掉——用户看到的就是"累计十页就停了"。

    所以：进新页面（派发完上一页的响应）→ 先结算上一页 → 再开始本页。
    """
    dated = bool(sess["date_from"] or sess["date_to"])
    max_pages = config.CRAWL_MAX_PAGES + (8 if dated else 0)
    opened = 0
    empty_streak = 0        # 连续几个详情页平台一条都没吐
    range_streak = 0        # 连续几个详情页"有数据但全在时间段外"
    carry = None            # 上一页待结算：(opened, 候选起点, 平台条数起点)

    def settle_carry():
        """结算上一页。返回 True 表示该结束这个平台。"""
        nonlocal empty_streak, range_streak, carry
        if not carry:
            return False
        page_no, rows_before, raw_before = carry
        carry = None
        added = _count(sess) - rows_before
        raw_added = sess["raw_seen"] - raw_before
        # 判据是"平台这条页面吐没吐"，不是"入库多少"：设了时间段时整页评论
        # 可能都落在区间外，入库 0 但平台明明有数据，按入库算会凑满连续空页。
        if raw_added:
            empty_streak = 0
            _log(sess, f"{platform}：第 {page_no} 个详情页 +{added} 条"
                       f"（平台返回 {raw_added} 条）")
            # 有数据却一条都没入候选 = 全被时间段挡掉了。评论按时间倒序返回，
            # 连续几页都落在区间外，说明这个关键词翻不到那么早的评论。
            # 不早停的话，有时间段时上限是 38 页（≈2.5 分钟），用户只会干等。
            if not added and dated:
                range_streak += 1
                if range_streak >= config.CRAWL_RANGE_STREAK:
                    _log(sess, f"{platform}：连续 {range_streak} 个详情页的评论都在"
                               f"所选时间段外，提前结束该平台")
                    return True
            else:
                range_streak = 0
            return False
        empty_streak += 1
        # 详情页在要求登录 → 立刻停，不要继续撞同一面墙。
        # 实测过一次：cookie 过期后系统翻完 11 个详情页、白耗 128 秒才报
        # "登录态大概率已失效"，而用户在第一个页面就已经被挡住了。
        # 判据必须带上 counter["hits"] == 0：拿到过评论就说明没被墙，
        # 个别笔记要求登录（作者限制）不该判成整个平台掉线。
        wall = _detail_login_wall(page) if counter["hits"] == 0 else ""
        if wall:
            raise CrawlError(
                f"{platform}详情页要求登录（页面提示「{wall}」）："
                "当前登录态已失效，需要重新登录后再采集。",
                "need_login", detail=_diag(page))
        _log(sess, f"{platform}：第 {page_no} 个详情页没有数据（连续第 {empty_streak} 个）")
        if empty_streak >= config.CRAWL_EMPTY_STREAK:
            _log(sess, f"{platform}：连续 {empty_streak} 个详情页没有数据，提前结束该平台")
            return True
        return False

    for url in links:
        if _should_stop(sess) or opened >= max_pages:
            break
        opened += 1
        nav_ok = True
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            nav_ok = False
            nav_err = type(e).__name__
        if not nav_ok:
            empty_streak += 1
            _log(sess, f"{platform}：第 {opened} 个详情页打开失败（{nav_err}），"
                       f"连续第 {empty_streak} 个")
            if empty_streak >= config.CRAWL_EMPTY_STREAK:
                _log(sess, f"{platform}：连续 {empty_streak} 个详情页打不开，提前结束该平台")
                break
            continue

        # 4 秒而不是 3 秒：小红书评论接口常在 2–4 秒才回来，等太短会把
        # "响应还没到"误判成"这页没有评论"。
        _settle(page, 4, sess)
        # 上一页的残留响应要等新页面稳定后才派发完，所以结算放在这 4 秒之后。
        # 放在 goto 紧后面会算在这之前——那样算出来永远是 0 条。
        if settle_carry():
            break
        missing = _page_missing(page)
        if missing:
            # 和"评论少"看起来一样（都是 +0 条），但原因完全不同：
            # 一个要换排序/重登录，一个只是内容冷门，所以单独记一条。
            empty_streak += 1
            _log(sess, f"{platform}：第 {opened} 个详情页无法浏览（{missing}），"
                       f"连续第 {empty_streak} 个")
            if empty_streak >= config.CRAWL_EMPTY_STREAK:
                _log(sess, f"{platform}：连续 {empty_streak} 个详情页无法浏览，提前结束该平台")
                break
            continue

        _focus_comments(page, platform)
        rows_before = _count(sess)
        raw_before = sess["raw_seen"]
        stall_raw = raw_before
        stall = 0
        # 前 4 轮无条件滚：评论是懒加载的，一上来就判"没数据"等于在响应到达前收工。
        WARMUP_ROUNDS = 4
        for i in range(config.CRAWL_SCROLL_ROUNDS):
            if _should_stop(sess):
                break
            # 已经翻到比区间起点更早的评论：这一页再往下只会更老，没必要继续滚。
            if sess["date_from"] and sess["oldest"] and sess["oldest"] < sess["date_from"]:
                break
            _scroll(page, 1600)
            _settle(page, 1.3, sess)
            if sess["raw_seen"] == stall_raw:
                stall += 1
                if i >= WARMUP_ROUNDS and stall >= 2:
                    break
            else:
                stall = 0
            stall_raw = sess["raw_seen"]
        carry = (opened, rows_before, raw_before)

    if carry and not _should_stop(sess):
        _settle(page, 0.3, sess)
        settle_carry()

    sess["diag"] = dict(sess.get("diag") or {}, opened=opened, api_hits=counter["hits"])


# ---------------- 线程入口 ----------------

def _finish(sess, error=None, code=None):
    with _lock:
        sess["ended_at"] = _now()
        sess["ended_at_t"] = time.time()
        if error:
            sess["status"] = "failed"
            sess["error"] = error
            sess["error_code"] = code or "crawl_failed"
        elif sess["stop"] and _count(sess) < sess["target"]:
            sess["status"] = "stopped"
        else:
            sess["status"] = "done"
        if _active.get("sid") == sess["sid"]:
            _active["sid"] = None
    _log(sess, f"任务结束：{sess['status']}，共 {_count(sess)} 条")


def _record_error(sess, plat, e, code=None):
    """记下第一个失败的"原因码"。

    页面靠这个码给不同的下一步建议（去配登录态 / 换关键词 / 重贴 cookie）。
    只留第一条：后面几个平台的失败往往是同一条原因，堆在一起反而看不清主因。
    """
    sess["errors"][plat] = str(e)
    code = code or getattr(e, "code", None) or "crawl_failed"
    sess["error_codes"][plat] = code
    if code == "need_login":
        _mark_stale(plat)
    if not sess.get("first_error_code"):
        sess["first_error_code"] = code or getattr(e, "code", None) or "crawl_failed"


def _worker(sess):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        _finish(sess, "未安装 playwright：pip install playwright 且 playwright install chromium",
                "no_playwright")
        return
    if not _browser_path():
        _finish(sess, "找不到可用的浏览器：设置 CP_CRAWL_CHROME 指向 chrome.exe", "no_browser")
        return
    try:
        with sync_playwright() as pw:
            browser = _launch(pw)
            try:
                plats = sess["platforms"]
                for idx, plat in enumerate(plats):
                    if _task_done(sess):
                        # 目标被前面的平台吃满了。这里必须留一条日志：
                        # 之前是直接 break，用户勾了两个平台却只拿到一个的数据，
                        # 页面上还看不出原因（实测 target=10 时小红书根本没跑）。
                        if idx:
                            _log(sess, f"总目标 {sess['target']} 条已在前面平台采满，"
                                       f"{plat} 本次不再采集")
                        break
                    # 剩余目标平均分给还没采的平台。不分摊的话第一个平台会把
                    # 目标全部占满，第二个平台一条也轮不到。
                    _left = sess["target"] - _count(sess)
                    _left_plats = len(plats) - idx
                    sess["plat_cap"] = _count(sess) + max(1, -(-_left // _left_plats))
                    _log(sess, f"{plat}：本平台配额 {sess['plat_cap'] - _count(sess)} 条"
                               f"（总目标 {sess['target']} 条）")
                    try:
                        ctx = _new_context(browser, plat)
                    except CrawlError as e:
                        _record_error(sess, plat, e)
                        _log(sess, f"{plat}：{e}")
                        continue
                    try:
                        if plat == "抖音":
                            _collect_douyin(ctx, sess)
                        else:
                            _collect_xhs(ctx, sess)
                    except CrawlError as e:
                        _record_error(sess, plat, e)
                        # 把"当时页面长什么样"一起留下：只有错误码的话，
                        # 用户看到的永远是"失败了"，我们也没法判断是被验证码挡了
                        # 还是被登录墙挡了。正文只留前 240 字，够看提示语就行。
                        extra = e.detail if isinstance(e.detail, dict) else {}
                        if isinstance(extra.get("text"), str):
                            extra = dict(extra, text=extra["text"][:240])
                        sess["diag"] = dict(sess.get("diag") or {}, last_error_code=e.code,
                                            last_error_platform=plat, **extra)
                        _log(sess, f"{plat}失败：{e}")
                    except Exception as e:
                        _record_error(sess, plat, f"{type(e).__name__}: {e}")
                        logger.exception("采集异常：%s", plat)
                        _log(sess, f"{plat}异常：{type(e).__name__}")
                    finally:
                        try:
                            ctx.close()
                        except Exception:
                            pass
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except CrawlError as e:
        _finish(sess, str(e), e.code)
        return
    except Exception as e:
        logger.exception("采集线程异常")
        _finish(sess, f"{type(e).__name__}: {e}", "crawl_failed")
        return

    if not _count(sess):
        # 有候选被时间过滤掉、却一条没留下：这不是"平台坏了"，是筛选条件太窄。
        # 报"没有采集到任何记录"会让人以为是网络或登录问题，去重试同一个条件。
        if sess["dropped_range"]:
            _finish(sess, f"翻到的 {sess['dropped_range']} 条评论都不在所选时间段内。"
                          "平台不支持按时间检索，只能往下翻页找，越久远的时间段越难翻到。"
                          "建议放宽时间段，或换一个讨论更集中的关键词。", "out_of_range")
            return
        first = next(iter(sess["errors"].values()), None) or "没有采集到任何记录"
        _finish(sess, first, sess.get("first_error_code") or "empty")
        return
    if sess["dropped_range"]:
        _log(sess, f"时间段过滤：跳过 {sess['dropped_range']} 条区间外评论")
    _finish(sess)


# ============================ 清洗与入库 ============================

def clean(sid):
    """按「通过 / 剔除 + 原因」把候选集过一遍清洗链路。

    产出与导入同构：records 里连被剔除的记录都留着（带 filter_reason），
    这样页面上能解释"为什么它没进分析"，而不是只给一个变小的数字。
    """
    sess = _get(sid)
    if sess["status"] == "running":
        raise CrawlError("采集还在进行，等结束或先停止再清洗", "still_running")
    with _lock:
        rows = list(sess["rows"])
    if not rows:
        raise CrawlError("候选集是空的，没有可清洗的内容", "empty")

    groups = {}
    for r in rows:
        groups.setdefault(r["platform"], []).append(r)
    payload = [(f"实时采集 · {p}", p, items, None) for p, items in groups.items()]
    records, quality = etl.normalize_groups(sess["dataset_id"], payload)
    with _lock:
        sess["records"] = records
        sess["quality"] = quality
        sess["cleaned_at"] = _now()
    _log(sess, f"清洗完成：原始 {quality['raw_count']} → 有效 {quality['valid_count']} 条")
    return clean_summary(sess)


def clean_summary(sess):
    q = sess["quality"] or {}
    return {
        "session_id": sess["sid"],
        "dataset_id": sess["dataset_id"],
        "raw_count": q.get("raw_count", 0),
        "valid_count": q.get("valid_count", 0),
        "effective_rate": q.get("effective_rate", 0),
        "platform_valid": q.get("platform_valid", {}),
        "stages": q.get("stages", []),
        "missing_like": q.get("missing_like", 0),
        # 单独给出短评条数：页面上要明确说"这些确实采到了，只是不计入分析"。
        # 只放在 stages 的 "-N" 里，用户会以为采集时就把短评丢了。
        "too_short": q.get("too_short", 0),
        "cleaned_at": sess["cleaned_at"],
    }


def records_view(sid, status="all", offset=0, limit=50):
    sess = _get(sid)
    if not sess["records"]:
        raise CrawlError("还没有执行清洗", "not_cleaned")
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    items = sess["records"]
    if status == "kept":
        items = [r for r in items if r["cleaning_status"] == "kept"]
    elif status == "rejected":
        items = [r for r in items if r["cleaning_status"] != "kept"]
    total = len(items)
    page = items[offset:offset + limit]
    return {
        "total": total,
        "offset": offset,
        "rows": [{
            "comment_ref": r["comment_ref"],
            "platform": r["platform"],
            "content": r["content_masked"],
            "comment_time": r["comment_time"],
            "like_count": r["like_count"],
            "dimension": r["dimension"],
            "sentiment": r["sentiment"],
            "cleaning_status": r["cleaning_status"],
            "filter_reason": r["filter_reason"],
        } for r in page],
    }


def commit(sid, name=None, target_dataset_id=None, select_mode="all", refs=None):
    """把清洗结果落成一个数据集，或者并进一个已有数据集。

    「是否加入数据集」这一步必须由人点——采集到什么、清洗剔了谁都摆在页面上，
    自动入库等于把"采集器采错了"这种事故直接写进分析口径。
    并库同理：默认还是新建，只有人明确选了目标才合并。
    勾选状态只影响清洗通过的评论；被剔除的记录仍留作质量追溯。
    """
    sess = _get(sid)
    if not sess["records"]:
        raise CrawlError("还没有执行清洗，不能入库", "not_cleaned")
    if sess["committed"]:
        raise CrawlError("这个采集任务已经入库过，请不要重复提交", "already_committed")
    kept = [r for r in sess["records"] if r["cleaning_status"] == "kept"]
    if not kept:
        raise CrawlError("清洗后没有有效评论，不入库（否则会多一个空数据集）", "empty_dataset")

    from . import intake
    try:
        records, q = intake.select_records(sess["records"], sess["quality"] or {},
                                           select_mode, refs)
    except intake.IntakeError as e:
        raise CrawlError(str(e), e.code)

    label = str(name or "").strip() or f"实时采集 · {sess['keyword']} · {time.strftime('%m-%d %H:%M')}"
    label = label[:60]
    note = (f"关键词「{sess['keyword']}」，采集于 {sess['started_at']}，"
            + (f"时间段 {sess['date_from'] or '不限'} ~ {sess['date_to'] or '不限'}，"
               if (sess["date_from"] or sess["date_to"]) else "")
            + f"原始 {q.get('raw_count', 0)} 条 → 有效 {q.get('valid_count', 0)} 条"
            + (f"（清洗通过 {len(kept)} 条，人工勾选 {q.get('valid_count', 0)} 条）"
               if q.get("valid_count", 0) != len(kept) else "")
            + "。采集通道为真实平台直连（带登录态），评论正文原样保留并统一脱敏。")

    # 三条入口共用同一套入库逻辑（新建 / 并库、跨批次去重、口径合并）
    result = intake.save_batch(
        records, q, new_dataset_id=sess["dataset_id"], name=label,
        source_label=f"抖音/小红书实时采集（浏览器通道）· {'/'.join(sess['platforms'])}",
        note=note, target_dataset_id=target_dataset_id,
        batch_tag=f"实时采集 {sess['keyword']}",
    )
    with _lock:
        sess["committed"] = {"dataset_id": result["dataset_id"], "name": result["name"],
                             "at": _now()}
    if result.get("merged"):
        _log(sess, f"已并入数据集「{result['name']}」：新增 {result['added']} 条，"
                   f"跳过重复 {result['skipped']} 条")
    else:
        _log(sess, f"已入库：{label}（有效 {result['valid_count']} 条）")
    return result
