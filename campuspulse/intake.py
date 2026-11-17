# -*- coding: utf-8 -*-
"""导入批次：文件导入 / 截图导入共用的「清洗 → 逐条核对 → 确认入库」会话。

三条入口最后都汇到同一条清洗链路（`etl.normalize_records`），区别只在记录从哪来。
但以前只有实时采集把"清洗结果摆出来让人点头"这一步做了：
文件导入和截图导入是选完文件就直接入库、还顺手把当前数据集切过去——
用户既看不到清洗剔了谁，也没机会说"这批我不要"。

这里把两个入口补成和采集一样的三步：

    1. 预览：文件落进 `data/uploads/_pending/<sid>/`，清洗但**不写库**；
    2. 核对：逐条看通过 / 剔除及原因（`records_view`）；
    3. 入库 / 放弃：只有 `commit` 才建数据集，`discard` 直接删掉暂存目录。

预览期不建 `ds_*` 目录、不写 datasets 行，所以中途关页面或服务重启都不会
留下"看着像 87 次成功导入"的空壳（那是这个项目踩过的坑）。
"""
import json
import logging
import os
import secrets
import shutil
import threading
import time

from . import config, db, etl

logger = logging.getLogger(__name__)

MAX_FILES = config.MAX_UPLOAD_FILES
MAX_BYTES = config.MAX_UPLOAD_BYTES
TTL = 2 * 3600              # 预览会话两小时过期：文件还压在磁盘上，不能一直挂着
MAX_SESSIONS = 6

_PENDING = os.path.join(config.UPLOAD_DIR, "_pending")
_LOCK = threading.Lock()
SESSIONS = {}


class IntakeError(RuntimeError):
    def __init__(self, message, code="bad_request", detail=None):
        super().__init__(message)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------- 基础
def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _drop(sid):
    s = SESSIONS.pop(sid, None)
    if s:
        shutil.rmtree(s["dir"], ignore_errors=True)
    return bool(s)


def _gc():
    """过期会话连暂存目录一起清掉。顺带扫一遍磁盘上的孤儿目录：
    进程重启后内存里的会话没了，但 `_pending` 下的文件还在。"""
    now = time.time()
    for sid, s in list(SESSIONS.items()):
        if now - s["created_ts"] > TTL and not s["committed"]:
            _drop(sid)
    try:
        for name in os.listdir(_PENDING):
            path = os.path.join(_PENDING, name)
            if not os.path.isdir(path) or name in SESSIONS:
                continue
            if now - os.path.getmtime(path) > TTL:
                shutil.rmtree(path, ignore_errors=True)
    except FileNotFoundError:
        pass


def _get(sid):
    s = SESSIONS.get(str(sid or ""))
    if not s:
        raise IntakeError("这批导入已经失效（可能超过两小时或服务重启过），请重新选择文件",
                          "not_found")
    return s


def _trim():
    """会话数封顶：预览是"人正在看"的临时状态，攒多了只会白占磁盘。"""
    while len(SESSIONS) > MAX_SESSIONS:
        oldest = min(SESSIONS.values(), key=lambda x: x["created_ts"])
        if oldest["committed"]:
            SESSIONS.pop(oldest["sid"], None)
        else:
            _drop(oldest["sid"])


# ---------------------------------------------------------------- 建批次
def _stage(kind, dataset_id, source_label, note, name_hint):
    sid = "in_" + secrets.token_hex(8)
    folder = os.path.join(_PENDING, sid)
    os.makedirs(folder, exist_ok=True)
    sess = {
        "sid": sid, "kind": kind, "dataset_id": dataset_id,
        "source_label": source_label, "note": note, "name_hint": name_hint,
        "dir": folder, "files": [], "rejected": [],
        "records": [], "quality": {}, "cleaned_at": None,
        "created_ts": time.time(), "committed": None,
    }
    SESSIONS[sid] = sess
    _trim()
    return sess


def _clean(sess):
    """跑清洗链路。文件只负责"把一组记录交出来"，规则全在 etl 里——
    两条路各写一遍清洗，同一句话在导入页和采集页就会得出两种结论。"""
    paths = [os.path.join(sess["dir"], f) for f in sess["files"]]
    records, quality = etl.normalize_records(sess["dataset_id"], paths)
    sess["records"] = records
    sess["quality"] = quality
    sess["cleaned_at"] = _now()
    return sess


def create_files(uploaded):
    """文件导入第一步：校验 + 存暂存目录 + 清洗。不建数据集。

    uploaded: [(文件名, 二进制内容)]
    """
    _gc()
    uploaded = [(os.path.basename(n), b) for n, b in uploaded if n and b]
    if not uploaded:
        raise IntakeError("未接收到文件，请选择抖音或小红书的公开平台导出 JSON", "invalid_file")
    if len(uploaded) > MAX_FILES:
        raise IntakeError(f"一次最多上传 {MAX_FILES} 个文件", "invalid_file")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    dataset_id = f"ds_up_{stamp}_{secrets.token_hex(2)}"
    sess = _stage(
        "file", dataset_id,
        source_label="用户上传的公开平台导出文件",
        note="",                       # 入库前按实际条数补写，见 commit()
        name_hint=f"文件导入 · {time.strftime('%m-%d %H:%M')}",
    )

    rejected = []
    for name, blob in uploaded:
        if not name.lower().endswith(".json"):
            rejected.append({"file": name, "reason": "只支持 .json 格式"})
            continue
        if len(blob) > MAX_BYTES:
            rejected.append({"file": name, "reason": f"文件超过 {MAX_BYTES // 1024 // 1024}MB 限制"})
            continue
        try:
            json.loads(blob.decode("utf-8-sig"))
        except Exception as e:
            rejected.append({"file": name, "reason": f"JSON 解析失败：{e}"})
            continue
        with open(os.path.join(sess["dir"], name), "wb") as out:
            out.write(blob)
        sess["files"].append(name)

    sess["rejected"] = rejected
    if not sess["files"]:
        _drop(sess["sid"])
        raise IntakeError("没有可用的数据文件", "invalid_file", detail=rejected)

    _clean(sess)
    if not sess["quality"].get("valid_count"):
        # 清洗后一条都不剩：直接把批次作废，别让用户对着空表点"确认导入"
        stages = sess["quality"].get("stages")
        _drop(sess["sid"])
        raise IntakeError(
            "清洗后没有有效评论：请确认评论内容为校园相关文本，且 create_time 可解析。",
            "empty_dataset", detail=stages)
    return summary(sess)


def create_records(source_rows, source_label, note, name_hint):
    """截图导入第一步：把人工复核后的行写成 json 再走同一条清洗链路。

    不直接拿字典去 normalize，是因为"导入"的输入本来就该是一份可复现的源文件——
    commit 之后 `{dataset_id}/screenshots.json` 就是重新分析时要用的那一份。
    """
    _gc()
    if not source_rows:
        raise IntakeError("没有可导入的记录：请至少保留一条复核通过的评论", "empty_dataset")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    dataset_id = f"ds_shot_{stamp}_{secrets.token_hex(2)}"
    sess = _stage("vision", dataset_id, source_label=source_label, note=note,
                  name_hint=name_hint)

    with open(os.path.join(sess["dir"], "screenshots.json"), "w", encoding="utf-8") as f:
        json.dump(source_rows, f, ensure_ascii=False, indent=2)
    sess["files"].append("screenshots.json")
    _clean(sess)
    if not sess["quality"].get("valid_count"):
        stages = sess["quality"].get("stages")
        _drop(sess["sid"])
        raise IntakeError("识别出的内容清洗后没有有效评论：多为界面噪声或未命中校园主题。",
                          "empty_dataset", detail=stages)
    return summary(sess)


# ---------------------------------------------------------------- 读
def summary(sess):
    q = sess["quality"] or {}
    return {
        "session_id": sess["sid"],
        "kind": sess["kind"],
        "dataset_id": sess["dataset_id"],
        "suggest_name": sess["name_hint"],
        "raw_count": q.get("raw_count", 0),
        "valid_count": q.get("valid_count", 0),
        "effective_rate": q.get("effective_rate", 0),
        "platform_valid": q.get("platform_valid", {}),
        "stages": q.get("stages", []),
        "too_short": q.get("too_short", 0),
        "missing_like": q.get("missing_like", 0),
        "screenshot_count": q.get("screenshot_count", 0),
        "time_inferred": q.get("time_inferred", 0),
        "cleaned_at": sess["cleaned_at"],
        "files": list(sess["files"]),
        "rejected": list(sess["rejected"]),
    }


def records_view(sid, status="all", offset=0, limit=50):
    """逐条核对用的分页视图，形状与 `/api/crawl/<sid>/rows` 完全一致——
    前端两处共用同一个表格渲染函数，不必写第二套。"""
    sess = _get(sid)
    if not sess["records"]:
        raise IntakeError("还没有执行清洗", "not_cleaned")
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


# ---------------------------------------------------------------- 入库 / 放弃
def _merge_quality(old, new, dup, kept_total):
    """把两批的口径合成一份。

    合并后的漏斗要能被读者顺着减下去算出有效数，所以：
    同名阶段相加、跨批次去重单独一行、有效数与有效率一律以**库里实际条数**为准
    （62+48 相加得 110，可库里其实是 108，因为撞了 2 条重复——这种矛盾最伤人）。
    """
    q = dict(old or {})
    for k in ("raw_count", "duplicate", "empty_content", "invalid_time", "too_short",
              "irrelevant", "missing_like", "screenshot_count", "time_inferred"):
        q[k] = int(q.get(k) or 0) + int(new.get(k) or 0)

    stages, index = [], {}
    for s in (q.get("stages") or []) + (new.get("stages") or []):
        cur = index.get(s["stage"])
        if cur:
            cur["count"] += s["count"]
        else:
            item = {"stage": s["stage"], "note": s.get("note", ""), "count": s["count"]}
            index[s["stage"]] = item
            stages.append(item)
    if dup:
        stages.append({"stage": "并入时去重", "note": "与目标数据集已有评论重复", "count": -dup})
    q["stages"] = stages

    pv = dict(q.get("platform_valid") or {})
    for k, v in (new.get("platform_valid") or {}).items():
        pv[k] = pv.get(k, 0) + v
    q["platform_valid"] = pv
    q["valid_count"] = kept_total
    q["effective_rate"] = round(100 * kept_total / q["raw_count"], 2) if q["raw_count"] else 0
    return q


def _archive(archive_dir, target_id, batch_tag):
    """把这一批的源文件留档到目标数据集目录，重新分析时还能读到。

    目标目录不存在（内置数据集走的是预设源文件）就不建目录——
    宁可留一句 note，也不要凭空造一个没人会读的空目录。
    """
    final = os.path.join(config.UPLOAD_DIR, target_id)
    if not archive_dir or not os.path.isdir(final):
        return False
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(batch_tag))[:40]
    for name in os.listdir(archive_dir):
        src = os.path.join(archive_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(final, f"{safe}__{name}"))
    return True


def select_records(records, quality, select_mode="all", refs=None):
    """按人工勾选裁掉清洗通过的评论，并把质量口径改成这一批的真实构成。

    select_mode="all" 表示默认全选，refs 是要排除的 comment_ref；
    select_mode="none" 表示默认不选，refs 是要保留的 comment_ref。
    被清洗剔除的记录照旧保留（带原因），它们本来就不计入有效数；
    真正从数据集里消失的只有"清洗通过、但用户没勾"的评论。
    """
    kept = [r for r in records if r["cleaning_status"] == "kept"]
    if isinstance(refs, (str, bytes)):
        refs = [refs]
    wanted = {str(x) for x in (refs or [])}
    if select_mode == "none":
        chosen = [r for r in kept if r["comment_ref"] in wanted]
    else:
        chosen = [r for r in kept if r["comment_ref"] not in wanted]
    if not chosen:
        raise IntakeError("请至少勾选一条有效评论再入库", "empty_selection")

    chosen_refs = {r["comment_ref"] for r in chosen}
    kept_records = [r for r in records
                    if r["cleaning_status"] != "kept" or r["comment_ref"] in chosen_refs]
    dropped = len(kept) - len(chosen)

    q = dict(quality or {})
    stages = list(q.get("stages") or [])
    if dropped:
        stages.append({
            "stage": "人工取消勾选",
            "note": "清洗通过但用户未选入的评论",
            "count": -dropped,
        })
    q["stages"] = stages
    q["valid_count"] = len(chosen)
    q["platform_valid"] = {}
    for r in chosen:
        q["platform_valid"][r["platform"]] = q["platform_valid"].get(r["platform"], 0) + 1
    q["missing_like"] = sum(1 for r in chosen if r.get("like_count") is None)
    raw = int(q.get("raw_count") or 0)
    q["effective_rate"] = round(100 * len(chosen) / raw, 2) if raw else 0.0
    return kept_records, q


def save_batch(records, quality, *, new_dataset_id, name, source_label, note,
               target_dataset_id=None, archive_dir=None, batch_tag=""):
    """一批清洗结果落库：`target_dataset_id` 为空则新建，否则并入。

    三条入口（实时采集 / 文件 / 截图）共用这一个函数——
    "并入已有数据集"如果各写一份，迟早出现两条路对重复、对计数口径不一致。
    """
    if target_dataset_id:
        return _append(target_dataset_id, records, quality,
                       batch_tag=batch_tag, archive_dir=archive_dir, note=note)

    meta, q = etl.build_dataset_from_records(
        new_dataset_id, (name or "")[:60], records, quality,
        source_label=source_label, note=note, is_demo=False)
    return {
        "dataset_id": meta["dataset_id"], "name": meta["name"], "merged": False,
        "raw_count": meta["raw_count"], "valid_count": meta["valid_count"],
        "effective_rate": q["effective_rate"],
        "time_span": [meta["time_start"], meta["time_end"]],
        "platforms": meta["platforms"],
        "added": meta["valid_count"], "skipped": 0,
        "screenshot_count": q.get("screenshot_count", 0),
        "time_inferred": q.get("time_inferred", 0),
    }


def _append(target_id, records, quality, batch_tag="", archive_dir=None, note=""):
    ds = db.get_dataset(target_id)
    if not ds:
        raise IntakeError("目标数据集不存在或已被删除", "not_found")

    kept = [r for r in records if r["cleaning_status"] == "kept"]
    if not kept:
        raise IntakeError("清洗后没有有效评论，不能并入数据集", "empty_dataset")

    # 跨批次去重按（平台, 正文）：两批数据可能来自同一条评论的不同导出，
    # comment_id 未必一致，正文相同就当同一条。重复的不入，但要计数——
    # 否则"这批 60 条，为什么只多了 48 条"没人解释得清。
    rows = db.query_all(
        "SELECT platform, content FROM comments WHERE dataset_id = ? AND tenant_id = ?",
        (target_id, db.tid()))
    seen = {(r["platform"], (r["content"] or "").strip()) for r in rows}
    add, dup = [], 0
    for r in kept:
        key = (r["platform"], (r["content"] or "").strip())
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        item = dict(r)
        item["dataset_id"] = target_id
        # comment_ref 里编了数据集 id：并库后必须重算，否则会和源批次撞引用
        item["comment_ref"] = etl.make_comment_ref(
            target_id, item["platform"],
            f"{batch_tag or 'batch'}|{item.get('source_record') or item['comment_ref']}")
        add.append(item)

    if add:
        db.insert_comments(add)

    tid = db.tid()
    kept_total = db.scalar(
        "SELECT COUNT(*) FROM comments WHERE dataset_id = ? AND tenant_id = ? "
        "AND cleaning_status = 'kept'", (target_id, tid)) or 0
    span = db.query_one(
        "SELECT MIN(comment_date) a, MAX(comment_date) b FROM comments "
        "WHERE dataset_id = ? AND tenant_id = ? AND cleaning_status = 'kept' "
        "AND comment_date IS NOT NULL", (target_id, tid)) or {}
    plat = db.query_all(
        "SELECT platform, COUNT(*) n FROM comments WHERE dataset_id = ? AND tenant_id = ? "
        "AND cleaning_status = 'kept' GROUP BY platform", (target_id, tid))

    q = _merge_quality(ds.get("quality") or {}, quality, dup, kept_total)
    archived = _archive(archive_dir, target_id, batch_tag)
    stamp = db.now_str()
    line = (f"[{stamp[:16]}] 并入「{batch_tag or '一个批次'}」：新增 {len(add)} 条，"
            f"跳过重复 {dup} 条"
            + ("；源文件已留档，可重新分析" if archived else "")
            + ("；这是内置数据集，重新分析/恢复内置会回到原始数据" if ds.get("is_demo") else ""))
    meta = dict(ds)
    meta.update({
        "note": ((ds.get("note") or "").rstrip() + "\n" + line).strip(),
        "raw_count": (ds.get("raw_count") or 0) + (quality.get("raw_count") or 0),
        "valid_count": kept_total,
        "status": "ready" if kept_total else "empty",
        "time_start": span.get("a") or ds.get("time_start"),
        "time_end": span.get("b") or ds.get("time_end"),
        "platforms": json.dumps({r["platform"]: r["n"] for r in plat}, ensure_ascii=False),
        "quality": json.dumps(q, ensure_ascii=False),
        "data_version": stamp,
        "analyzed_at": stamp,
    })
    db.upsert_dataset(meta)
    return {
        "dataset_id": target_id, "name": meta["name"], "merged": True,
        "raw_count": meta["raw_count"], "valid_count": kept_total,
        "effective_rate": q["effective_rate"],
        "time_span": [meta["time_start"], meta["time_end"]],
        "platforms": json.loads(meta["platforms"]),
        "added": len(add), "skipped": dup, "archived": archived,
        "screenshot_count": quality.get("screenshot_count", 0),
        "time_inferred": quality.get("time_inferred", 0),
    }


def _dataset_dir(dataset_id):
    """只返回 uploads 内的真实目录，防止拼出越界的路径。"""
    root = os.path.realpath(config.UPLOAD_DIR)
    path = os.path.realpath(os.path.join(root, str(dataset_id or "")))
    return path if path != root and path.startswith(root + os.sep) else ""


def _remove_dataset_dir(dataset_id):
    path = _dataset_dir(dataset_id)
    if path and os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
        return True
    return False


def _archive_dataset(source_id, target_id):
    """把源数据集的源文件复制进目标目录，让目标「重算」后仍包含并入的数据。

    目标没有源文件目录（内置数据集 / 实时采集直建的数据集）时跳过：
    那类数据集本来就不支持重跑，凭空建个空目录只会让人以为它能重算。
    """
    src_dir = _dataset_dir(source_id)
    dst_dir = _dataset_dir(target_id)
    if not (src_dir and dst_dir and os.path.isdir(src_dir) and os.path.isdir(dst_dir)):
        return False
    copied = 0
    for name in os.listdir(src_dir):
        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            continue
        try:
            shutil.copy2(src, os.path.join(dst_dir, f"merged_{source_id}__{name}"))
            copied += 1
        except OSError:
            # 留档失败不该阻断合并：评论已经写进目标库，
            # 代价只是目标以后「重算」时少了这一份源文件。
            logger.exception("并入数据集时源文件留档失败：%s", src)
    return copied > 0


def merge_datasets(source_id, target_id):
    """把一个数据集整体并入另一个，随后删除源数据集。

    搬的是评论记录本身（含被清洗剔除的，保留追溯），不是两份统计数字相加；
    重复按（平台, 正文）跳过并计数，与采集/导入的跨批次去重同一口径。
    """
    source_id = str(source_id or "").strip()
    target_id = str(target_id or "").strip()
    if not target_id:
        raise IntakeError("请选择要并入的目标数据集", "bad_target")
    if source_id == target_id:
        raise IntakeError("不能把数据集并入它自己", "bad_target")
    src = db.get_dataset(source_id)
    if not src:
        raise IntakeError("源数据集不存在或已被删除", "not_found")
    dst = db.get_dataset(target_id)
    if not dst:
        raise IntakeError("目标数据集不存在或已被删除", "not_found")

    t = db.tid()
    rows = db.query_all(
        "SELECT * FROM comments WHERE dataset_id = ? AND tenant_id = ?",
        (source_id, t))
    if not rows:
        raise IntakeError("源数据集没有评论记录，无法并入", "empty_dataset")

    existing = db.query_all(
        "SELECT platform, content FROM comments WHERE dataset_id = ? AND tenant_id = ?",
        (target_id, t))
    seen = {(r["platform"], (r["content"] or "").strip()) for r in existing}
    add, dup = [], 0
    for r in rows:
        item = dict(r)
        if r.get("cleaning_status") == "kept":
            key = (r["platform"], (r["content"] or "").strip())
            if key in seen:
                dup += 1
                continue
            seen.add(key)
        item["dataset_id"] = target_id
        # comment_ref 编了数据集 id，并入后必须重算，否则会和目标已有引用撞键
        item["comment_ref"] = etl.make_comment_ref(
            target_id, item["platform"], f"merge|{source_id}|{r['comment_ref']}")
        add.append(item)

    if add:
        db.insert_comments(add)

    kept_total = db.scalar(
        "SELECT COUNT(*) FROM comments WHERE dataset_id = ? AND tenant_id = ? "
        "AND cleaning_status = 'kept'", (target_id, t)) or 0
    span = db.query_one(
        "SELECT MIN(comment_date) a, MAX(comment_date) b FROM comments "
        "WHERE dataset_id = ? AND tenant_id = ? AND cleaning_status = 'kept' "
        "AND comment_date IS NOT NULL", (target_id, t)) or {}
    plat = db.query_all(
        "SELECT platform, COUNT(*) n FROM comments WHERE dataset_id = ? AND tenant_id = ? "
        "AND cleaning_status = 'kept' GROUP BY platform", (target_id, t))

    q = _merge_quality(dst.get("quality") or {}, src.get("quality") or {}, dup, kept_total)
    # 平台分布与点赞缺失以库里实际留下的评论为准：上面是两份口径相加，
    # 去重掉的那几条还留在加数里，照抄会出现"有效 10 条、平台却合计 12 条"。
    q["platform_valid"] = {r["platform"]: r["n"] for r in plat}
    q["missing_like"] = db.scalar(
        "SELECT COUNT(*) FROM comments WHERE dataset_id = ? AND tenant_id = ? "
        "AND cleaning_status = 'kept' AND like_count IS NULL", (target_id, t)) or 0

    archived = _archive_dataset(source_id, target_id)
    stamp = db.now_str()
    line = (f"[{stamp[:16]}] 并入「{src['name']}」：新增 {len(add)} 条，"
            f"跳过重复 {dup} 条"
            + ("；源文件已留档，可重新分析" if archived else "")
            + ("；目标为内置数据集，重新分析/恢复内置会回到原始数据"
               if dst.get("is_demo") else ""))
    meta = dict(dst)
    meta.update({
        "note": ((dst.get("note") or "").rstrip() + "\n" + line).strip(),
        "raw_count": (dst.get("raw_count") or 0) + (src.get("raw_count") or 0),
        "valid_count": kept_total,
        "status": "ready" if kept_total else "empty",
        "time_start": span.get("a") or dst.get("time_start"),
        "time_end": span.get("b") or dst.get("time_end"),
        "platforms": json.dumps({r["platform"]: r["n"] for r in plat}, ensure_ascii=False),
        "quality": json.dumps(q, ensure_ascii=False),
        "data_version": stamp,
        "analyzed_at": stamp,
    })
    db.upsert_dataset(meta)
    db.delete_dataset(source_id)
    _remove_dataset_dir(source_id)
    return {
        "dataset_id": target_id, "name": meta["name"], "merged": True,
        "source_dataset_id": source_id, "source_name": src["name"],
        "added": len(add), "skipped": dup, "archived": archived,
        "raw_count": meta["raw_count"], "valid_count": kept_total,
        "effective_rate": q["effective_rate"],
        "time_span": [meta["time_start"], meta["time_end"]],
        "platforms": json.loads(meta["platforms"]),
    }


def delete_dataset(dataset_id):
    """删除数据集。至少保留一个，否则全站页面会指向一份不存在的数据。"""
    ds = db.get_dataset(dataset_id)
    if not ds:
        raise IntakeError("数据集不存在或已被删除", "not_found")
    remaining = [d for d in db.list_datasets() if d["dataset_id"] != dataset_id]
    if not remaining:
        raise IntakeError(
            "这是最后一个数据集，删除后全站没有数据可看。请先采集或导入一份新数据。",
            "last_dataset")
    db.delete_dataset(dataset_id)
    _remove_dataset_dir(dataset_id)
    return {
        "deleted": True, "dataset_id": dataset_id, "name": ds["name"],
        "fallback_dataset_id": remaining[0]["dataset_id"],
        "fallback_name": remaining[0]["name"],
    }


def commit(sid, name=None, target_dataset_id=None, select_mode="all", refs=None):
    """人点了"确认导入"才写库。

    预览阶段数据集 id 就已经生成，但库里没有行、目录还叫 `_pending/<sid>`；
    确认时才写库，新建时把目录改名成 `uploads/<dataset_id>`——
    改名这一步是为了让"重新分析"能像以前一样从源文件重跑。
    选择并入已有数据集时，暂存文件会被拷进目标目录留档（见 _archive）。
    勾选状态只影响清洗通过的评论；被剔除的记录仍留作质量追溯。
    """
    sess = _get(sid)
    if sess["committed"]:
        raise IntakeError("这批导入已经入库过，请不要重复提交", "already_committed")
    kept = [r for r in sess["records"] if r["cleaning_status"] == "kept"]
    if not kept:
        raise IntakeError("清洗后没有有效评论，不入库（否则会多一个空数据集）", "empty_dataset")

    records, q = select_records(sess["records"], sess["quality"] or {},
                                select_mode, refs)
    label = (str(name or "").strip() or sess["name_hint"])[:60]
    note = sess["note"] or (
        f"通过数据与报告中心导入 {len(sess['files'])} 个文件："
        f"原始 {q.get('raw_count', 0)} 条 → 有效 {q.get('valid_count', 0)} 条"
        + (f"（清洗通过 {len(kept)} 条，人工勾选 {q.get('valid_count', 0)} 条）"
           if q.get("valid_count", 0) != len(kept) else "")
        + "。清洗规则与实时采集完全一致，被剔除的记录也保留在数据集里并标注原因。")
    batch_tag = f"{'截图' if sess['kind'] == 'vision' else '文件'}导入 {sess['sid'][-6:]}"

    if target_dataset_id:
        result = save_batch(records, q, new_dataset_id=sess["dataset_id"], name=label,
                            source_label=sess["source_label"], note=note,
                            target_dataset_id=target_dataset_id,
                            archive_dir=sess["dir"], batch_tag=batch_tag)
        dirpath = sess["dir"]
        sess["dir"] = ""            # 文件已拷进目标数据集，暂存目录随会话一起清掉
        shutil.rmtree(dirpath, ignore_errors=True)
        sess["committed"] = {"dataset_id": result["dataset_id"],
                             "name": result["name"], "at": _now()}
        return result

    result = save_batch(records, q, new_dataset_id=sess["dataset_id"], name=label,
                        source_label=sess["source_label"], note=note)
    final = os.path.join(config.UPLOAD_DIR, sess["dataset_id"])
    try:
        shutil.rmtree(final, ignore_errors=True)
        os.replace(sess["dir"], final)
    except OSError:
        # 改名失败不影响已经写好的数据集，但要留痕：重新分析会用到这个目录
        logger.exception("导入批次的源文件暂存目录未能转正：%s", sess["sid"])

    sess["committed"] = {"dataset_id": result["dataset_id"], "name": result["name"], "at": _now()}
    return result


def discard(sid):
    sess = _get(sid)
    if sess["committed"]:
        raise IntakeError("这批已经入库，不能放弃", "already_committed")
    _drop(sid)
    return {"discarded": True}
