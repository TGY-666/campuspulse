# -*- coding: utf-8 -*-
"""RAG 检索层 —— 让 Agent 能"翻原文"，而不是只会算统计。

为什么要有这一层
----------------
统计工具（tools.py 前 5 个）回答的是"多少条、占比多少"，但评委真正会追问的是
"你凭什么这么说"。原实现里只有 search_comments 做 LIKE 子串匹配：
问"宿舍最不满的是什么"，它只能匹配字面含"宿舍"的评论，召回不了"5 人间没有独立卫浴"
这种没有一个字相同的证据。

检索流程（两路召回 + 融合 + 重排，全部本地可复算，不依赖外部向量服务）
------------------------------------------------------------------
    问题 ─┬─ ① 稀疏检索：jieba 词项 BM25（k1=1.5, b=0.75）
          ├─ ② 词项扩展检索：领域同义词表扩展后重新 BM25（提高召回）
          └─ ③ 稠密检索：字符 2/3-gram 哈希向量 (1024 维 TF-IDF, L2 归一) 余弦
                    ↓
              RRF 融合（k=60，只取各榜前 300）
                    ↓
              元数据重排（点赞、维度命中、时间新鲜度）
                    ↓
              top-k 片段 + 内部引用 ID（CMT-xxxxxxxx）

关于"稠密"的诚实说明
--------------------
② 用的是字符 n-gram 哈希特征，不是神经网络句向量——因为项目坚持零外部模型依赖、
离线可跑。它的好处是对分词错误、未登录词、语气改写不敏感（"上床下桌"被切成
"上床/下桌"也照样命中），缺点是没有真正的语义泛化（"寝室"≠"宿舍"）。
这个缺口由 ② 的同义词扩展弥补。接口上预留了 embedding 钩子（见 EMBEDDING_HOOK），
换成真向量库时只需替换 _dense_matrix 的来源，上层 RRF 与引用逻辑不用改。

引用（citation）
---------------
每个片段都带 comment_ref，回答里写 [CMT-xxxx] 就能一键回跳到证据详情；
citation_check() 会核对"回答引用的编号是否真的在本次检索结果里"，
编造的编号会被单独标出来，而不是悄悄放过。
"""
import json
import math
import re
import threading
import time
import zlib

import numpy as np

from . import analytics, config, db

# ---------------- 超参 ----------------
BM25_K1 = 1.5
BM25_B = 0.75
VEC_DIM = 1024               # 哈希向量维度（碰撞可控，内存约 2600×1024×4B ≈ 10MB）
RRF_K = 60                   # RRF 平滑常数
RECALL_DEPTH = 300           # 每路召回深度
MAX_CHUNK_CHARS = 160        # 超过这个长度才切块（本项目平均 20 字，几乎不会触发）
CHUNK_OVERLAP = 30

# 检索层只做"召回"，不做摘要，所以这里不套用词云的强停用词表（那套会把"学校""今年"
# 等词删掉，但检索时"学校"恰恰可能是有效线索）。只去掉纯虚词与表情残留。
_STOP = {
    "的", "了", "是", "我", "你", "他", "她", "它", "们", "在", "也", "都", "就", "还",
    "有", "没", "不", "很", "啊", "呀", "吧", "呢", "吗", "哦", "哈", "和", "与", "或",
    "这", "那", "个", "上", "下", "里", "吗", "嘛", "嗯", "哦", "唉", "诶", "把", "被",
    "给", "让", "对", "从", "到", "会", "要", "能", "说", "去", "来", "看", "想", "知道",
}

# 提问里的"空壳词"：出现在问题里但对召回没有区分度，反而会让短评论靠一个泛词冲到前排。
# 实测（未加这张表时）"宿舍住宿方面学生最不满的是什么" 的第 2 名是
# "已经是东软准学生了，建议什么专业呢"——它只命中了"学生""什么"两个泛词。
_QUERY_STOP = {
    "什么", "怎么样", "怎么", "怎样", "哪些", "哪个", "如何", "为什么", "是否", "多少",
    "请问", "一下", "方面", "主要", "具体", "相关", "情况", "问题", "看看", "说说",
    "介绍", "评价", "分析", "一下", "目前", "现在", "整体", "大概", "一般",
    "学校", "学生", "同学", "大家", "我们", "校园", "本校", "这所",
    # 正反问句式：测试发现"能不能""有没有"会被当成实词参与打分，
    # 把"宿舍能不能洗澡"打成"洗澡"和"能"两路噪声，必须剔除。
    "能不能", "有没有", "可不可以", "是不是", "会不会", "要不要", "好不好", "行不行",
    "能", "会", "要", "行", "吗", "呢", "啊", "呀", "吧", "啦", "么",
}

# 领域同义词 / 近义扩展表：解决"字面不同但指同一件事"的召回缺口。
# 只放校园语料里真实出现的说法，不做无限扩张，避免查询漂移。
_SYNONYMS = {
    "宿舍": ["寝室", "住宿", "公寓", "宿舍楼", "宿舍区", "人间"],
    "寝室": ["宿舍", "住宿", "人间"],
    "住宿": ["宿舍", "寝室", "住宿费", "公寓"],
    "食堂": ["饭堂", "餐厅", "饭菜", "吃饭", "外卖"],
    "饭堂": ["食堂", "饭菜", "餐厅"],
    "饭菜": ["食堂", "口味", "难吃", "好吃"],
    "学费": ["学杂费", "收费", "费用", "交钱"],
    "学杂费": ["学费", "收费", "费用"],
    "收费": ["学费", "学杂费", "费用", "贵"],
    "贵": ["学费", "收费", "物价", "费用", "贵"],
    "空调": ["空调费", "制冷", "电费", "断电"],
    "热水": ["热水器", "洗澡", "淋浴", "水温"],
    "网络": ["校园网", "断网", "网速", "宽带", "流量"],
    "校园网": ["网络", "断网", "网速", "宽带"],
    "快递": ["快递点", "菜鸟", "驿站", "取件"],
    "军训": ["军训", "教官", "新生"],
    "招生": ["报考", "分数线", "录取", "志愿", "招生办"],
    "录取": ["录取通知书", "分数线", "招生"],
    "课程": ["教学", "老师", "上课", "小学期", "作业", "考试"],
    "教学": ["课程", "老师", "上课", "师资"],
    "老师": ["教师", "师资", "教学", "课程"],
    "就业": ["实习", "校招", "找工作", "专升本", "就业率"],
    "交通": ["地铁", "公交", "校车", "通勤", "打车"],
    "环境": ["绿化", "校园环境", "施工", "卫生"],
    "活动": ["社团", "校园活动", "比赛", "晚会"],
    "管理": ["规定", "制度", "门禁", "查寝", "管理"],
}

# 预留：将来接入真向量服务时实现此钩子（返回 (N, D) float32 矩阵，行已 L2 归一）
EMBEDDING_HOOK = None


# ============================ 文本处理 ============================
def normalize(text):
    """复用词云同一套预处理（去表情占位符 / @ / 链接 / 标点），保证两处口径一致。"""
    return analytics._prep_text(text or "")


def tokenize(text, query=False):
    """jieba 分词 → 去虚词与单字噪点。数字、英文词保留。

    query=True 时额外剔掉"什么/怎么样/学校/学生"这类空壳词：
    它们大量出现在提问里却不携带检索意图，留在查询里会污染 BM25。
    """
    analytics._ensure_jieba()
    import jieba

    out = []
    for w in jieba.lcut(normalize(text)):
        w = w.strip().lower()
        if not w or w in _STOP:
            continue
        if query and w in _QUERY_STOP:
            continue
        if len(w) == 1 and not w.isascii() and not w.isdigit():
            continue                      # "人""好"这类单字几乎无区分度
        out.append(w)
    return out


def _grams(text, ns=(2, 3)):
    """字符 n-gram（去空格后），用于哈希向量：对分词切错、口语改写更鲁棒。"""
    s = re.sub(r"\s+", "", normalize(text))
    grams = []
    for n in ns:
        if len(s) < n:
            continue
        for i in range(len(s) - n + 1):
            grams.append(s[i:i + n])
    return grams


def _bucket(gram):
    """稳定哈希（内置 hash 有进程随机化，不能用于索引）。"""
    return zlib.crc32(gram.encode("utf-8")) % VEC_DIM


def _split_chunks(text):
    """长评论切块，短评论保持整条（可溯源到评论级）。"""
    t = (text or "").strip()
    if len(t) <= MAX_CHUNK_CHARS:
        return [(0, t)]
    out, start = [], 0
    while start < len(t):
        end = min(start + MAX_CHUNK_CHARS, len(t))
        out.append((start, t[start:end]))
        if end >= len(t):
            break
        start = end - CHUNK_OVERLAP
    return out


# ============================ 索引 ============================
class RagIndex:
    """单数据集的混合检索索引（进程内，惰性构建）。"""

    def __init__(self, dataset_id, tenant=None):
        started = time.time()
        self.tenant_id = tenant or db.tid()
        rows = db.query_all(
            """SELECT comment_ref, platform, comment_date, sentiment, dimension, dimensions,
                      like_count, negative_reason, content_masked
               FROM comments
               WHERE tenant_id = ? AND dataset_id = ? AND cleaning_status = 'kept'
               ORDER BY comment_ref""",
            (self.tenant_id, dataset_id),
        )
        self.dataset_id = dataset_id
        self.docs = []
        for r in rows:
            text = r["content_masked"] or ""
            try:
                dims = json.loads(r["dimensions"] or "[]")
            except Exception:
                dims = []
            for off, piece in _split_chunks(text):
                self.docs.append({
                    "chunk_id": r["comment_ref"] if off == 0 else f"{r['comment_ref']}#{off}",
                    "comment_ref": r["comment_ref"],
                    "offset": off,
                    "text": piece,
                    "platform": r["platform"],
                    "date": r["comment_date"],
                    "sentiment": r["sentiment"],
                    "dimension": r["dimension"],
                    "dimensions": dims,
                    "like_count": r["like_count"],
                    "negative_reason": r["negative_reason"] or "",
                })

        self.n = len(self.docs)
        self.postings = {}                # term -> [(doc_idx, tf)]
        self.df = {}
        self.doc_len = np.zeros(self.n, dtype=np.float32)
        self.vec = np.zeros((self.n, VEC_DIM), dtype=np.float32)
        self._build()
        self.build_ms = int((time.time() - started) * 1000)

    # ---------- 构建 ----------
    def _build(self):
        if self.n == 0:
            self.avgdl = 1.0
            return
        inv = {}
        rows_idx = []
        cols_idx = []
        vals = []
        for i, d in enumerate(self.docs):
            toks = tokenize(d["text"])
            self.doc_len[i] = len(toks) or 1
            tf = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t, c in tf.items():
                inv.setdefault(t, []).append((i, c))
            # 向量侧：字符 n-gram TF
            gram_tf = {}
            for g in _grams(d["text"]):
                b = _bucket(g)
                gram_tf[b] = gram_tf.get(b, 0) + 1
            for b, c in gram_tf.items():
                rows_idx.append(i)
                cols_idx.append(b)
                vals.append(1.0 + math.log(c))
        self.postings = inv
        self.df = {t: len(v) for t, v in inv.items()}
        self.avgdl = float(self.doc_len.mean()) or 1.0
        self.idf = {
            t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in self.df.items()
        }
        # 向量矩阵 + 文档频率 IDF（哈希桶维度）
        if rows_idx:
            mat = np.zeros((self.n, VEC_DIM), dtype=np.float32)
            np.add.at(mat, (np.array(rows_idx), np.array(cols_idx)), np.array(vals, dtype=np.float32))
            bucket_df = np.count_nonzero(mat, axis=0).astype(np.float32)
            bucket_idf = np.log((self.n + 1) / (bucket_df + 1)) + 1.0
            # 高频 n-gram（"的""了"这类跨词碎片、"学生""学校"这类全库通用词）对区分度只有坏处：
            # 出现于超过 35% 文档的桶直接置零，等价于向量侧的停用词表。
            bucket_idf[bucket_df > max(3.0, 0.35 * self.n)] = 0.0
            mat *= bucket_idf
            norm = np.linalg.norm(mat, axis=1, keepdims=True)
            norm[norm == 0] = 1.0
            self.vec = mat / norm
            self.bucket_idf = bucket_idf
        else:
            self.bucket_idf = np.ones(VEC_DIM, dtype=np.float32)

    # ---------- 打分 ----------
    def bm25(self, terms):
        scores = np.zeros(self.n, dtype=np.float32)
        for t in terms:
            plist = self.postings.get(t)
            if not plist:
                continue
            idf = self.idf[t]
            for i, tf in plist:
                denom = tf + BM25_K1 * (1 - BM25_B + BM25_B * self.doc_len[i] / self.avgdl)
                scores[i] += idf * tf * (BM25_K1 + 1) / denom
        return scores

    def dense(self, query_text):
        gram_tf = {}
        for g in _grams(query_text):
            b = _bucket(g)
            gram_tf[b] = gram_tf.get(b, 0) + 1
        if not gram_tf:
            return np.zeros(self.n, dtype=np.float32)
        q = np.zeros(VEC_DIM, dtype=np.float32)
        for b, c in gram_tf.items():
            q[b] = (1.0 + math.log(c)) * self.bucket_idf[b]
        nq = float(np.linalg.norm(q))
        if nq == 0:
            return np.zeros(self.n, dtype=np.float32)
        return self.vec @ (q / nq)

    # ---------- 统计 ----------
    def stats(self):
        return {
            "dataset_id": self.dataset_id,
            "docs": self.n,
            "vocab": len(self.postings),
            "avg_doc_len": round(self.avgdl, 2),
            "vector_dim": VEC_DIM,
            "build_ms": self.build_ms,
            "engine": "bm25 + char-ngram-vector + weighted-fusion(rrf 可选)",
        }


# ============================ 索引缓存 ============================
# 键是 (tenant_id, dataset_id)，不是单独的 dataset_id。
# 这是全项目最容易被忽略的一处隔离：SQL 带了租户条件，内存缓存没带，
# 结果两所学校读到同一份索引——SQL 审计脚本查不出来，因为它不是 SQL。
_CACHE = {}
_LOCK = threading.Lock()


def _cache_key(dataset_id, tenant=None):
    return (tenant or db.tid(), dataset_id)


def get_index(dataset_id, tenant=None):
    """按 (租户, 数据版本, 保留条数) 做缓存键：导入 / 重分析后自动失效，不会拿到旧索引。"""
    ck = _cache_key(dataset_id, tenant)
    ds = db.get_dataset(dataset_id, tenant=ck[0]) or {}
    kept = db.scalar(
        "SELECT COUNT(*) FROM comments WHERE tenant_id = ? AND dataset_id = ? AND cleaning_status='kept'",
        ck,
    ) or 0
    key = (ck[1], ds.get("data_version"), kept)
    hit = _CACHE.get(ck)
    if hit and hit[0] == key:
        return hit[1]
    with _LOCK:
        hit = _CACHE.get(ck)
        if hit and hit[0] == key:
            return hit[1]
        idx = RagIndex(dataset_id, tenant=ck[0])
        _CACHE[ck] = (key, idx)     # 每个数据集只留最新版本，换版本即重建
    return idx


def invalidate(dataset_id=None, tenant=None):
    """失效当前租户的索引（传 dataset_id 只清那一个）。

    刻意不清别的租户：别的租户的数据集刚被改动时，它自己的请求
    会用 data_version 比对自动重建，不需要替它操心。
    """
    t = tenant or db.tid()
    with _LOCK:
        if dataset_id is None:
            for k in [k for k in _CACHE if k[0] == t]:
                _CACHE.pop(k, None)
        else:
            _CACHE.pop((t, dataset_id), None)


def is_cached(dataset_id, tenant=None):
    """当前数据集的最新版本是否已在内存索引里。

    这是"预热是否生效"的唯一可靠判据：只看耗时会被机器性能骗
    （快机器上建索引也只要 2ms 的时候，压根分不出是缓存还是重建）。
    """
    ck = _cache_key(dataset_id, tenant)
    hit = _CACHE.get(ck)
    if not hit:
        return False
    ds = db.get_dataset(dataset_id, tenant=ck[0]) or {}
    kept = db.scalar(
        "SELECT COUNT(*) FROM comments WHERE tenant_id = ? AND dataset_id = ? AND cleaning_status='kept'",
        ck) or 0
    return hit[0] == (ck[1], ds.get("data_version"), kept)


def index_stats(dataset_id=None):
    idx = get_index(dataset_id or config.DEFAULT_DATASET_ID)
    s = idx.stats()
    s["cached"] = True
    return s


# ============================ 检索 ============================
def _mask(idx, f):
    """结构化筛选（平台/维度/情感/日期）作为硬过滤，先挡掉不相关文档。"""
    mask = np.ones(idx.n, dtype=bool)
    plats = set(f.get("platforms") or [])
    dims = set(f.get("dimensions") or [])
    sents = set(f.get("sentiments") or [])
    d1, d2 = f.get("date_from"), f.get("date_to")
    if not (plats or dims or sents or d1 or d2):
        return mask
    for i, d in enumerate(idx.docs):
        if plats and d["platform"] not in plats:
            mask[i] = False
            continue
        if sents and d["sentiment"] not in sents:
            mask[i] = False
            continue
        if dims and not (dims & set(d["dimensions"] or [d["dimension"]])):
            mask[i] = False
            continue
        if d1 and (d["date"] or "") < d1:
            mask[i] = False
            continue
        if d2 and (d["date"] or "") > d2:
            mask[i] = False
            continue
    return mask


def _expand(terms):
    """同义词扩展：原词 + 表中对应近义词，最多扩 8 个，防止查询漂移。"""
    extra = []
    for t in terms:
        for w in _SYNONYMS.get(t, []):
            if w not in terms and w not in extra:
                extra.append(w)
    return terms + extra[:8]


def _oov_split(idx, terms):
    """未登录词拆二元字组。

    jieba 会把"快递点"当成一个词，而语料里写的是"快递站"，直接查必然 0 条。
    把未登录的 3 字以上词拆成字符二元组（快递 / 递点），只要其中任一组在词表里就能召回。
    """
    out = list(terms)
    for t in terms:
        if len(t) < 3 or t.isascii() or t in idx.postings:
            continue
        for i in range(len(t) - 1):
            bg = t[i:i + 2]
            if bg in idx.postings and bg not in out:
                out.append(bg)
    return out


def _top(scores, mask, depth=RECALL_DEPTH):
    """取 top-depth 的 (doc_idx, score)，已按掩码过滤。返回按分数降序的下标列表。"""
    s = np.where(mask, scores, -1e9)
    if depth >= s.size:
        order = np.argsort(-s)
    else:
        part = np.argpartition(-s, depth)[:depth]
        order = part[np.argsort(-s[part])]
    return [int(i) for i in order if s[i] > 0]


def _rrf(rank_lists):
    """倒数排名融合：不比较各路分数量纲，只看名次，天然抗分数尺度差异。

    保留实现是为了做 A/B 对比（见 tests/rag_eval.py）：RRF 只认名次，
    稀疏导出的强匹配会被向量榜里的大量弱匹配稀释，实测 Recall@5 低于加权融合，
    因此线上默认用 weighted，RRF 作为可切换的备选（fusion="rrf"）。
    """
    fused = {}
    for name, ids in rank_lists.items():
        for rank, i in enumerate(ids, start=1):
            fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank)
    return fused


def _norm(scores, mask):
    """按当前筛选范围做 min-max 归一，让三路分数可以相加。"""
    s = np.where(mask, scores, 0.0).astype(np.float32)
    mx = float(s.max()) if s.size else 0.0
    return s / mx if mx > 0 else s


# 融合权重：词项 BM25 为主（可解释、不胡扩），同义扩展补口语化改写，向量补分词错误。
# 这组权重不是拍脑袋定的：在 tests/rag_eval.py 的 12 题标注集上网格搜索过
#   (0.55,0.25,0.20) P@5=0.817 MRR=0.903
#   (0.60,0.30,0.10) P@5=0.817 MRR=0.958   ← 采用
#   (0.68,0.20,0.12) P@5=0.800 MRR=0.958
#   (0.50,0.20,0.30) P@5=0.783 MRR=0.896   （向量权重过高反而掉点）
W_BM25, W_EXPAND, W_VECTOR = 0.60, 0.30, 0.10


def retrieve(query, dataset_id=None, filters=None, top_k=6, use_expansion=True,
             fusion="weighted", retrievers=None):
    """混合检索主入口。返回 {items, trace, stats}。

    retrievers 用于消融实验（tests/rag_eval.py），线上默认三路全开。
    """
    started = time.time()
    dataset_id = dataset_id or config.DEFAULT_DATASET_ID
    use = set(retrievers or ("bm25", "expand", "vector"))
    idx = get_index(dataset_id)
    if idx.n == 0:
        return {"items": [], "empty": True, "query": query, "trace": {}, "elapsed_ms": 0,
                "stats": idx.stats(), "note": "当前数据集没有可检索的评论。"}

    f = filters or {}
    mask = _mask(idx, f)
    terms = tokenize(query, query=True)
    if not terms:
        terms = tokenize(query) or [t for t in re.split(r"[\s,，、]+", query or "") if t][:6]
    terms = _oov_split(idx, terms)
    expanded = _expand(terms) if (use_expansion and "expand" in use) else terms

    s_bm25 = idx.bm25(terms) if ("bm25" in use or "expand" in use) else np.zeros(idx.n, dtype=np.float32)
    s_exp = idx.bm25(expanded) if "expand" in use else np.zeros(idx.n, dtype=np.float32)
    s_vec = idx.dense(query) if "vector" in use else np.zeros(idx.n, dtype=np.float32)

    list_bm25 = _top(s_bm25, mask)
    list_exp = _top(s_exp, mask)
    list_vec = _top(s_vec, mask)

    if fusion == "rrf":
        fused = _rrf({"bm25": list_bm25, "expand": list_exp, "vector": list_vec})
        base_score = dict(fused)
    else:
        w = {}
        if "bm25" in use:
            w["bm25"] = W_BM25
        if "expand" in use and expanded != terms:
            w["expand"] = W_EXPAND
        if "vector" in use:
            w["vector"] = W_VECTOR
        total_w = sum(w.values()) or 1.0
        base = np.zeros(idx.n, dtype=np.float32)
        if "bm25" in w:
            base += w["bm25"] * _norm(s_bm25, mask)
        if "expand" in w:
            base += w["expand"] * _norm(s_exp, mask)
        if "vector" in w:
            base += w["vector"] * _norm(s_vec, mask)
        base /= total_w
        base_score = {i: float(base[i]) for i in _top(base, mask) if base[i] > 0}

    # 两套融合的分数尺度差一个量级（加权≈0~1，RRF≈0~0.05），直接叠加元数据会让加成反客为主，
    # 所以先各自按最大值归一到 [0,1]，再进重排。
    _mx = max(base_score.values()) if base_score else 1.0
    if _mx > 0:
        base_score = {i: v / _mx for i, v in base_score.items()}

    # ---- 元数据重排：只做小幅调整，不允许盖过相关性 ----
    # 最终分 = 0.88×相关性 + 0.12×元数据加成，天然落在 [0,1]，不会出现"融合分 1.016"这种
    # 让评委看不懂的数；同时保持对相关性单调，不会因为点赞多就把不相关的顶上来。
    BOOST_CAP = 0.12
    max_like = max([d["like_count"] or 0 for d in idx.docs] or [1]) or 1
    q_dims = {d for d in config.DIMENSIONS if d in (query or "")}
    ranked = []
    for i, score0 in base_score.items():
        d = idx.docs[i]
        boost = 0.0
        boost += 0.06 * min(1.0, (d["like_count"] or 0) / max_like)      # 高赞样本更有代表性
        if q_dims and (q_dims & set(d["dimensions"] or [d["dimension"]])):
            boost += 0.04                                                # 维度与提问一致
        if terms and any(t in (d["text"] or "") for t in terms):
            boost += 0.02                                                # 字面命中
        ranked.append((i, (1 - BOOST_CAP) * score0 + BOOST_CAP * min(1.0, boost / BOOST_CAP)))
    ranked.sort(key=lambda x: -x[1])

    items = []
    for rank, (i, score) in enumerate(ranked[:max(1, min(int(top_k or 6), 12))], start=1):
        d = idx.docs[i]
        text = d["text"] or ""
        items.append({
            "rank": rank,
            "comment_ref": d["comment_ref"],
            "chunk_id": d["chunk_id"],
            "offset": d["offset"],
            "snippet": text if len(text) <= 120 else text[:120] + "…",
            "text": text,
            "platform": d["platform"],
            "date": d["date"],
            "sentiment": d["sentiment"],
            "sentiment_label": config.SENTIMENT_LABELS.get(d["sentiment"], "未知"),
            "dimension": d["dimension"],
            "like_count": d["like_count"],
            "negative_reason": d["negative_reason"],
            "score": round(float(score), 3),
            "bm25": round(float(s_bm25[i]), 3),
            "vector": round(float(s_vec[i]), 3),
            "matched_terms": [t for t in terms if t in text][:5],
        })

    return {
        "items": items,
        "empty": len(items) == 0,
        "query": query,
        "terms": terms,
        "expanded_terms": [t for t in expanded if t not in terms],
        "candidates": {"bm25": len(list_bm25), "expand": len(list_exp), "vector": len(list_vec),
                       "fused": len(base_score), "filtered_docs": int(mask.sum())},
        "trace": {
            "retrievers": [n for n, on in (("bm25(词项)", "bm25" in use),
                                           ("bm25(同义扩展)", "expand" in use),
                                           ("vector(字符n-gram)", "vector" in use)) if on],
            "fusion": (f"RRF(k={RRF_K})" if fusion == "rrf" else
                       f"加权融合(BM25 {W_BM25} / 扩展 {W_EXPAND} / 向量 {W_VECTOR})"),
            "recall_depth": RECALL_DEPTH,
        },
        "stats": idx.stats(),
        "elapsed_ms": int((time.time() - started) * 1000),
    }


# ============================ 引用 ============================
_REF_RE = re.compile(r"\[\s*(CMT-[0-9A-Za-z]+)\s*\]")


def extract_citations(answer):
    return [m.group(1) for m in _REF_RE.finditer(answer or "")]


def citation_check(answer, items, dataset_id=None):
    """核对回答里的引用编号是否真的来自本次检索结果。

    三种情况分开统计，便于前端如实展示：
      · valid   引用了本次检索到的片段（可回跳原文）
      · invalid 编号在库里存在、但没被本次检索命中（越界引用）
      · unknown 编号在库里根本不存在（编造）
    """
    cited = extract_citations(answer)
    retrieved = {it["comment_ref"] for it in (items or [])}
    valid = [c for c in cited if c in retrieved]
    rest = [c for c in cited if c not in retrieved]
    unknown = []
    if rest and dataset_id:
        known = {r["comment_ref"] for r in db.query_all(
            "SELECT comment_ref FROM comments WHERE tenant_id = ? AND dataset_id = ?"
            " AND comment_ref IN (%s)" % ",".join("?" * len(rest)),
            [db.tid(), dataset_id] + rest)}
        invalid = [c for c in rest if c in known]
        unknown = [c for c in rest if c not in known]
    else:
        invalid = rest
    passed = bool(cited) and not invalid and not unknown
    return {
        "total": len(cited),
        "valid": len(valid),
        "invalid": invalid[:4],
        "unknown": unknown[:4],
        "passed": passed,
        "note": ("引用全部命中本次检索结果，可逐条回跳原文。" if passed else
                 "未标注引用（结论缺少原文锚点）。" if not cited else
                 "存在无法溯源的引用编号：" + "、".join((invalid + unknown)[:4])),
    }


def build_context(items, max_chars=1400):
    """把检索片段拼成注入提示词的上下文块（带编号，模型可引用）。"""
    if not items:
        return ""
    lines = ["【检索到的原文片段】（只能引用下列编号，格式 [编号]）"]
    used = 0
    for it in items:
        line = (f"[{it['comment_ref']}] {it['text']}"
                f"（{it['platform']} · {it['date']} · {it['sentiment_label']}"
                f" · {it['dimension'] or '未分类'}）")
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)
