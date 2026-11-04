# -*- coding: utf-8 -*-
"""截图评论导入（多模态）—— 把"群里的截图"变成可统计、可溯源的评论记录。

## 为什么做这件事

抖音 / 小红书的真实舆情工作里，**截图比 API 更常见**：招生办收到家长发来的截图，
辅导员在群里看到学生转发的吐槽图，这些内容进不了任何爬虫。系统原来只吃 JSON，
等于把最容易发生的那类舆情挡在门外。

## 三个设计约束（决定了下面的实现方式）

1. **数据不出校。** 首选本地 OCR（RapidOCR，PaddleOCR 同源模型的 ONNX 版），
   图片不上传任何第三方；只有在没装 onnxruntime 的机器上才降级到视觉模型 API。
2. **口径诚实。** OCR 会认错字。所以识别结果一律先进"待复核"草稿，
   人工看过、改过才入库；每条记录带 `ocr_confidence` 与 `ocr_engine`，
   证据页可以查到"这条是截图来的、置信度多少"。
3. **不假装成功。** 没有任何 OCR 引擎时返回明确错误 + 安装指引，
   而不是"导入成功，0 条"——后者会让人以为功能坏了。

## 识别链路

    PNG/JPG ──OCR──► 文本行（含逐行置信度）
                    ├─► 行分类：正文 / 时间地区 / 界面噪声
                    ├─► 正文成组（折行合并）+ 就近取发布时间
                    └─► 草稿行 {content, create_time, platform, confidence}
                              └─► 人工复核 ──► ETL 清洗 ──► 数据集
"""
import base64
import io
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta

from . import config

DRAFT_DIR = os.path.join(config.UPLOAD_DIR, "_vision")
DRAFT_TTL_HOURS = 24            # 草稿只活一天：截图里是别人的原话，不该长期留在磁盘上

# 单条评论最短长度，与 data_processor.MIN_VALID_LEN 保持同口径
MIN_TEXT_LEN = 5
MAX_TEXT_LEN = 300


# ============================ 引擎层 ============================
_rapidocr = None
_rapidocr_error = ""


def _load_rapidocr():
    """懒加载本地 OCR。首次调用约 0.5 秒（加载模型），之后复用。"""
    global _rapidocr, _rapidocr_error
    if _rapidocr is not None:
        return _rapidocr
    if _rapidocr_error:
        return None
    try:
        from rapidocr_onnxruntime import RapidOCR
        _rapidocr = RapidOCR()
    except Exception as e:          # 没装包、onnxruntime 与 Python 版本不匹配等
        _rapidocr_error = f"{type(e).__name__}: {e}"
        return None
    return _rapidocr


def engine_status():
    """给前端 / 健康检查用：当前能用哪个引擎，为什么不能用。"""
    local_ok = _load_rapidocr() is not None
    api_ok = bool(config.VISION_API_KEY)
    return {
        "enabled": bool(config.CP_VISION),
        "engines": [
            {"name": "rapidocr", "label": "本地 OCR（离线）", "available": local_ok,
             "note": "图片不出本机，零调用成本" if local_ok else (_rapidocr_error or "未安装")},
            {"name": "vision_api", "label": f"视觉模型 API（{config.VISION_MODEL}）",
             "available": api_ok,
             "note": "需配置 VISION_API_KEY" if not api_ok else "图片会发送到所配置的服务商"},
        ],
        "active": "rapidocr" if local_ok else ("vision_api" if api_ok else None),
    }


def _ocr_local(blob):
    """本地 OCR。返回 [{text, score}]，按识别框从上到下排序。"""
    engine = _load_rapidocr()
    if engine is None:
        raise VisionError("本地 OCR 不可用", _rapidocr_error)
    result, _elapse = engine(blob)
    lines = []
    for item in (result or []):
        # RapidOCR 每项是 [box, text, score]，box 是四个角点
        try:
            box, text, score = item[0], str(item[1]), float(item[2])
        except Exception:
            continue
        if not text.strip():
            continue
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        lines.append({"text": text.strip(), "score": score,
                      "y": sum(ys) / max(1, len(ys)),
                      "x": min(xs), "right": max(xs)})
    lines.sort(key=lambda r: r["y"])
    return lines


_VISION_PROMPT = (
    "这张图是抖音或小红书的评论区截图。请只提取**用户评论正文**，不要提取昵称、时间、点赞数、"
    "按钮文字（回复/赞/展开/关注）等界面元素。\n"
    "严格输出 JSON 数组，不要解释、不要代码块标记，每项形如："
    '{"content": "评论文本", "time_text": "图中显示的时间如 3天前/10-05，没有就留空", '
    '"platform": "抖音|小红书|未知"}\n'
    "如果图中没有评论，输出 []。"
)


def _ocr_api(blob, filename):
    """视觉模型兜底。走 OpenAI 兼容的 chat/completions 接口，换服务商只改环境变量。"""
    if not config.VISION_API_KEY:
        raise VisionError("未配置视觉模型", "需要 VISION_API_KEY")
    import requests

    mime = "image/png" if str(filename).lower().endswith(".png") else "image/jpeg"
    b64 = base64.b64encode(blob).decode("ascii")
    resp = requests.post(
        f"{config.VISION_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {config.VISION_API_KEY}",
                 "Content-Type": "application/json"},
        json={
            "model": config.VISION_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": _VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}],
            "temperature": 0,
            "max_tokens": 1500,
        },
        timeout=config.VISION_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise VisionError(f"视觉模型返回 HTTP {resp.status_code}", resp.text[:200])
    text = ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        items = json.loads(text)
    except Exception:
        raise VisionError("视觉模型返回的不是合法 JSON", text[:200])
    # API 通道没有逐字置信度，统一给 None：宁可在界面上显示"—"，
    # 也不要编一个 0.9 出来，那会让"置信度"这个字段整体失去意义。
    return [{"text": str(it.get("content") or "").strip(), "score": None,
             "time_text": str(it.get("time_text") or "").strip(),
             "platform": str(it.get("platform") or "").strip()}
            for it in items if isinstance(it, dict)]


class VisionError(Exception):
    def __init__(self, reason, detail=""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


# ============================ 行分类与解析 ============================
REGIONS = [
    "北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江",
    "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西", "海南", "四川",
    "贵州", "云南", "陕西", "甘肃", "青海", "内蒙古", "宁夏", "新疆", "西藏", "香港", "澳门",
    "台湾", "中国",
]

# 界面噪声：整行等于这些词时不可能是评论正文
_NOISE_EXACT = {
    "回复", "赞", "点赞", "分享", "收藏", "展开", "收起", "关注", "已关注", "作者",
    "置顶", "广告", "翻译", "更多", "查看", "展开更多", "说点什么", "说点什么...",
    "留下你的精彩评论", "全部评论", "暂无评论", "加载中", "发送", "搜索", "首页",
    "消息", "我", "发布", "评论", "回复 @", "作者赞过", "朋友", "推荐", "同城",
}
_NOISE_CONTAINS = ("IP属地", "抖音号", "小红书号", "条回复", "展开更多", "点击展开",
                   "作者赞过", "该评论已被", "复制链接", "保存图片")

_COUNT_RE = re.compile(r"^\d+(?:\.\d+)?\s*[万亿]?$")
_REL_TIME_RE = re.compile(r"^(\d+)\s*(秒|分钟|小时|天|周|个月|月|年)\s*前$")
_ABS_TIME_RE = re.compile(
    r"^(?:(\d{4})[-/.年])?(\d{1,2})[-/.月](\d{1,2})日?(?:\s+(\d{1,2})[:：](\d{2}))?$")
_TODAY_RE = re.compile(r"^(今天|昨天|前天)\s*(?:(\d{1,2})[:：](\d{2}))?$")


def _strip_seps(s):
    return re.sub(r"^[\s·•|｜\-—:：/]+|[\s·•|｜\-—:：/]+$", "", str(s or ""))


def parse_time_token(text, now=None):
    """从一行文字里解析发布时间。解析不出来返回 None（不猜）。

    支持：刚刚 / 3天前 / 2小时前 / 昨天 12:30 / 10-05 / 10-05 12:30 / 2026-05-01
    """
    s = _strip_seps(text)
    if not s:
        return None
    now = now or datetime.now()

    if s in ("刚刚", "此刻", "现在"):
        return now
    m = _REL_TIME_RE.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {
            "秒": timedelta(seconds=n), "分钟": timedelta(minutes=n), "小时": timedelta(hours=n),
            "天": timedelta(days=n), "周": timedelta(weeks=n), "个月": timedelta(days=30 * n),
            "月": timedelta(days=30 * n), "年": timedelta(days=365 * n),
        }.get(unit)
        return now - delta if delta else None

    m = _TODAY_RE.match(s)
    if m:
        base = {"今天": 0, "昨天": 1, "前天": 2}[m.group(1)]
        hh = int(m.group(2)) if m.group(2) else 12
        mm = int(m.group(3)) if m.group(3) else 0
        d = now - timedelta(days=base)
        return d.replace(hour=hh, minute=mm, second=0, microsecond=0)

    m = _ABS_TIME_RE.match(s)
    if m:
        year = int(m.group(1)) if m.group(1) else now.year
        month, day = int(m.group(2)), int(m.group(3))
        hh = int(m.group(4)) if m.group(4) else 12
        mm = int(m.group(5)) if m.group(5) else 0
        try:
            dt = datetime(year, month, day, hh, mm)
        except ValueError:
            return None
        # 只有 MM-DD 时按"不晚于今天"补年份：12 月的图在 1 月看到，
        # 应该算去年，而不是未来的今年 12 月。
        if not m.group(1) and dt > now + timedelta(days=1):
            dt = dt.replace(year=year - 1)
        return dt
    return None


def _has_region(s):
    s = _strip_seps(s)
    return any(r == s or s.endswith(" " + r) or s.endswith(r) for r in REGIONS)


def _time_from_line(text, now=None):
    """整行里找时间。真实截图常见 "3天前 · 广东" 这种时间+属地合一行，
    直接按整行匹配会失败，所以要拆开再试。"""
    s = _strip_seps(text)
    dt = parse_time_token(s, now)
    if dt:
        return dt
    for part in re.split(r"[·•｜|/]", s):
        dt = parse_time_token(part, now)
        if dt:
            return dt
    for r in REGIONS:
        if s.endswith(r):
            dt = parse_time_token(_strip_seps(s[:-len(r)]), now)
            if dt:
                return dt
    return None


def _is_meta_line(line):
    """整行只是"时间 · 地区"这类附带信息吗？"""
    s = _strip_seps(line)
    if not s:
        return False
    if _time_from_line(s) is not None and len(s) <= 24:
        return True
    # "广东" / "· 广东" 这种仅地区行
    if _has_region(s) and len(s) <= 12:
        return True
    return False


def _is_noise(line):
    s = _strip_seps(line)
    if not s:
        return True
    if s in _NOISE_EXACT:
        return True
    # "全部评论 126" / "共 38 条评论" 这类区块标题，OCR 常把数字和标题粘成一行
    if re.fullmatch(r"(全部评论|精彩评论|热门评论|最新评论|共\s*\d+\s*条评论)[\s\d.]*", s):
        return True
    if any(k in s for k in _NOISE_CONTAINS):
        return True
    if _COUNT_RE.match(s):
        return True
    if s.startswith("@"):
        return True
    # 纯英文/数字的导航文案（如 Explore / Home / 12.3万）
    if not re.search(r"[\u4e00-\u9fa5]", s) and len(s) <= 12:
        return True
    return False


def _ends_sentence(s):
    return bool(re.search(r"[。！？!?…~～,，；;：:]$", s))


def guess_platform(lines, filename=""):
    """从整图文字与文件名推断平台。推不出来就返回"未知"，不默认成抖音。"""
    joined = " ".join(l["text"] for l in lines) + " " + str(filename)
    low = joined.lower()
    dy = sum(joined.count(k) for k in ("抖音", "douyin", "@抖音", "抖音号"))
    xhs = sum(joined.count(k) for k in ("小红书", "xiaohongshu", "RED", "薯"))
    if dy > xhs and dy > 0:
        return "抖音"
    if xhs > dy and xhs > 0:
        return "小红书"
    if "douyin" in low:
        return "抖音"
    if "xhs" in low or "xiaohongshu" in low:
        return "小红书"
    return "未知"


def parse_lines(lines, platform="未知", now=None, page_right=None):
    """把 OCR 文本行解析成评论草稿。

    规则（刻意简单，因为每一步都要能被人工复核推翻）：
      1. 丢弃界面噪声（回复/赞/纯数字/展开条回复…）；
      2. 记录"时间/地区"行，用于给就近的正文行配发布时间；
      3. 折行合并：只有"上一行一直写到接近右边界"才认为是同一句被折断——
         昵称、区块标题都很短，不会顶到右边界，因此不会被吞进评论里；
      4. 每条正文行取其后 2 行内最近的时间行；取不到则标记 time_inferred。
    """
    now = now or datetime.now()
    rights = [l.get("right") for l in lines if l.get("right") is not None]
    edge = page_right if page_right is not None else (
        max(rights) if rights else None)
    wrap_tolerance = 40      # 距离右边界 40px 以内，视为"写满了这一行"
    rows = []
    pending = None          # 正在累积的正文（用于折行合并）

    def flush():
        nonlocal pending
        if pending:
            rows.append(pending)
            pending = None

    for i, item in enumerate(lines):
        text = _strip_seps(item.get("text"))
        if not text:
            continue
        if _is_meta_line(text) or _is_noise(text):
            # 时间/地区/按钮行：先收尾当前这条，再继续找下一条正文。
            # 这里必须 flush：否则"评论A + 时间行 + 评论B"会被当成 A 的折行合并，
            # 两条互不相干的吐槽粘成一句，复核的人根本看不出来原本是两条。
            flush()
            continue

        # 正文过短 / 过长：过短是噪声碎片，过长多半是整段说明文字被整块识别
        if len(text) < MIN_TEXT_LEN:
            continue

        prev_right = (pending or {}).get("_right")
        wraps = (pending is not None and edge is not None and prev_right is not None
                 and prev_right >= edge - wrap_tolerance)
        merge = (wraps and not _ends_sentence(pending["content"])
                 and len(pending["content"]) + len(text) <= MAX_TEXT_LEN)
        if merge:
            pending["content"] += text
            pending["_right"] = item.get("right")
            if item.get("score") is not None:
                pending["_scores"].append(float(item["score"]))
            continue

        flush()
        pending = {"content": text[:MAX_TEXT_LEN], "platform": platform,
                   "_scores": [float(item["score"])] if item.get("score") is not None else [],
                   "_idx": i, "_right": item.get("right"),
                   "time_text": "", "create_time": None,
                   "time_inferred": 0, "time_source": ""}
        # 就近取时间：往后看 2 行（时间行通常在正文下方）
        for j in range(i + 1, min(i + 3, len(lines))):
            t = _strip_seps(lines[j].get("text"))
            dt = _time_from_line(t, now) if t and len(t) <= 24 else None
            if dt:
                pending["create_time"] = dt
                pending["time_text"] = t
                pending["time_source"] = "截图内时间"
                break
        # 往后没找到，再看前一行（部分版式时间在上方）
        if pending["create_time"] is None and i > 0:
            t = _strip_seps(lines[i - 1].get("text"))
            dt = _time_from_line(t, now) if t and len(t) <= 24 else None
            if dt:
                pending["create_time"] = dt
                pending["time_text"] = t
                pending["time_source"] = "截图内时间"
    flush()

    out = []
    seen = set()
    for r in rows:
        content = str(r["content"]).strip()
        if len(content) < MIN_TEXT_LEN:
            continue
        key = re.sub(r"\s+", "", content)
        if key in seen:
            continue
        seen.add(key)
        scores = r.get("_scores") or []
        conf = round(sum(scores) / len(scores), 3) if scores else None
        infer = 0
        ct = r.get("create_time")
        if ct is None:
            ct = now
            infer = 1
        out.append({
            "content": content,
            "create_time": ct.strftime("%Y-%m-%d %H:%M:%S"),
            "time_text": r.get("time_text") or "",
            "time_source": r.get("time_source") or ("未包含发布时间，按导入日期计入" if infer else ""),
            "time_inferred": infer,
            "platform": r.get("platform") or platform,
            "confidence": conf,
            "needs_review": bool(infer or (conf is not None and conf < config.VISION_MIN_SCORE)),
        })
        if len(out) >= config.VISION_MAX_ROWS:
            break
    return out


# ============================ 对外入口 ============================
ALLOWED_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def recognize(files):
    """识别一组截图，返回草稿。files = [(filename, bytes)]。

    单张失败不影响其它张：一张图糊了，另外五张仍然可用——
    整批失败会让人误以为是功能坏了，而不是这张图的问题。
    """
    if not config.CP_VISION:
        raise VisionError("截图识别未启用", "CP_VISION=0")
    status = engine_status()
    if not status["active"]:
        raise VisionError(
            "没有可用的 OCR 引擎，截图无法识别",
            "本地 OCR 需要 rapidocr_onnxruntime（pip install rapidocr_onnxruntime）；"
            "或配置 VISION_API_KEY 使用视觉模型。")

    results, all_rows = [], []
    for filename, blob in files:
        name = os.path.basename(filename or "screenshot.png")
        started = time.time()
        try:
            if status["active"] == "rapidocr":
                lines = _ocr_local(blob)
                platform = guess_platform(lines, name)
                rows = parse_lines(lines, platform)
                engine = "rapidocr"
            else:
                items = _ocr_api(blob, name)
                lines = [{"text": it["text"], "score": it.get("score"),
                          "time_text": it.get("time_text")} for it in items]
                platform = items[0].get("platform") if items else ""
                if platform not in config.PLATFORMS:
                    platform = guess_platform(lines, name)
                rows = parse_lines(lines, platform)
                # 视觉模型直接给了结构化结果，时间优先用它给的 time_text。
                # 只在条数对得上时逐条覆盖：条数对不上说明模型多给/少给了项，
                # 硬按序号配对会把 A 的时间安到 B 头上。
                if len(items) == len(rows):
                    for row, it in zip(rows, items):
                        dt = parse_time_token(it.get("time_text") or "")
                        if dt:
                            row.update(create_time=dt.strftime("%Y-%m-%d %H:%M:%S"),
                                       time_text=it.get("time_text"), time_inferred=0,
                                       time_source="截图内时间")
                engine = "vision_api"
            for idx, r in enumerate(rows):
                r["engine"] = engine
                r["file"] = name
                # 稳定行号：人工复核时正文会被改错字，用"文件+正文"当键会失配，
                # 一改字就丢掉置信度与引擎信息。rid 不受编辑影响。
                r["rid"] = f"{name}#{idx}"
            all_rows.extend(rows)
            results.append({
                "file": name, "ok": True, "engine": engine, "platform": platform,
                "lines": len(lines), "rows": len(rows),
                "elapsed_ms": int((time.time() - started) * 1000),
                "note": "" if rows else "未识别到可用的评论正文（可能是纯界面截图或文字过短）",
            })
        except Exception as e:
            results.append({
                "file": name, "ok": False, "engine": status["active"], "rows": 0,
                "elapsed_ms": int((time.time() - started) * 1000),
                "error": getattr(e, "reason", str(e)),
                "detail": getattr(e, "detail", ""),
            })

    ok_count = sum(1 for r in results if r["ok"])
    stats = {
        "files": len(files), "ok_files": ok_count, "rows": len(all_rows),
        "inferred_time": sum(1 for r in all_rows if r["time_inferred"]),
        "low_confidence": sum(1 for r in all_rows
                              if r["confidence"] is not None
                              and r["confidence"] < config.VISION_MIN_SCORE),
        "engine": status["active"],
    }
    return {"engine": status["active"], "engine_label": _engine_label(status["active"]),
            "files": results, "rows": all_rows, "stats": stats}


def _engine_label(name):
    return {"rapidocr": "本地 OCR（离线）", "vision_api": f"视觉模型 · {config.VISION_MODEL}"}.get(
        name, name or "未知")


# ============================ 草稿 ============================
def _ensure_dir():
    os.makedirs(DRAFT_DIR, exist_ok=True)


def _purge_old_drafts():
    """清掉过期草稿。截图里是别人的原话，不该在磁盘上长期堆积。"""
    if not os.path.isdir(DRAFT_DIR):
        return
    cutoff = time.time() - DRAFT_TTL_HOURS * 3600
    for fn in os.listdir(DRAFT_DIR):
        p = os.path.join(DRAFT_DIR, fn)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass


def save_draft(payload):
    _ensure_dir()
    _purge_old_drafts()
    sid = time.strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
    payload = dict(payload, session_id=sid, created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    with open(os.path.join(DRAFT_DIR, sid + ".json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return sid


def load_draft(sid):
    sid = str(sid or "").strip()
    if not re.fullmatch(r"\d{14}_[0-9a-f]{6}", sid):
        raise VisionError("复核会话无效", "session_id 格式不正确")
    path = os.path.join(DRAFT_DIR, sid + ".json")
    if not os.path.isfile(path):
        raise VisionError("复核会话已过期", "草稿默认保留 24 小时，请重新上传截图")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def drop_draft(sid):
    try:
        os.remove(os.path.join(DRAFT_DIR, str(sid) + ".json"))
    except OSError:
        pass


def to_source_rows(draft_rows):
    """草稿 → ETL 能吃的原始记录。

    字段名沿用爬虫导出的 content / create_time / platform，让截图数据
    走**同一条**清洗管线；_source_type 等以下划线开头的字段由 ETL 透传到库，
    这样"来源"不会在清洗过程中丢失。
    """
    out = []
    for i, r in enumerate(draft_rows):
        content = str(r.get("content") or "").strip()
        if not content:
            continue
        out.append({
            "comment_id": f"shot_{i:03d}",
            "content": content,
            "create_time": r.get("create_time") or "",
            "platform": r.get("platform") if r.get("platform") in config.PLATFORMS else "未知",
            "_source_type": "screenshot",
            "_ocr_confidence": r.get("confidence"),
            "_ocr_engine": r.get("engine") or "",
            "_time_inferred": 1 if r.get("time_inferred") else 0,
            "_source_file": r.get("file") or "",
        })
    return out


def image_size(blob):
    """读图片尺寸，用于在复核界面上提示"这张图是不是太小了"。
    只做校验与提示，不改变识别结果（缩放由 OCR 引擎自己做）。"""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(blob)) as im:
            return im.size
    except Exception:
        return None
