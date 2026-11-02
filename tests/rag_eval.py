# -*- coding: utf-8 -*-
"""RAG 检索质量评估 —— 让"检索得好不好"变成可复算的数字，而不是口头形容词。

为什么需要这个脚本
------------------
评委问"你凭什么说混合检索比关键词检索好"，回答"感觉更准"是没有说服力的。
这里给出两种客观指标，并且把 4 种检索策略放在同一套标注题上跑：

    Precision@5  前 5 条里有多少条真的相关（相关性由标注词判定，见 CASES）
    MRR@10       第一条相关结果排在第几位的倒数（越接近 1 越靠前）

对照组：
    ① LIKE 基线      —— 旧实现的做法：按字面包含关系过滤后按点赞排序
    ② BM25 单路      —— 只做词项检索
    ③ 向量单路       —— 只做字符 n-gram 哈希向量
    ④ 混合（线上）   —— BM25 + 同义词扩展 + 向量，加权融合
    ⑤ 混合 RRF       —— 同样的三路，改用倒数排名融合（用于证明选型不是随手拍的）

标注方法（可复算、不掺主观）：
    每个问题给出"必须命中的词面"（must），片段文本包含其中任一词即判为相关。
    这套标注偏向字面召回，对纯语义泛化（"寝室"↔"宿舍"）不公平，
    所以额外单列一题专门测同义词扩展——那一题在 must 里只写"寝室"，看谁能召回。

运行：
    .venv/Scripts/python.exe tests/rag_eval.py
    .venv/Scripts/python.exe tests/rag_eval.py --dataset ds_2023_all
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campuspulse import config, db, rag  # noqa: E402

K = 5            # Precision@K
M = 10           # MRR@M

# 标注题：q=检索语句，must=判相关的词面，note=说明这题在测什么
CASES = [
    {"q": "宿舍住几个人", "must": ["人间", "几个人", "几人"], "note": "数字型细节"},
    {"q": "宿舍有没有空调", "must": ["空调", "制冷"], "note": "设施询问"},
    {"q": "宿舍能不能洗澡 有没有热水", "must": ["热水", "洗澡", "淋浴", "水温"], "note": "同义改写"},
    {"q": "食堂饭菜贵不贵", "must": ["饭菜", "食堂", "饭堂", "价格", "消费"], "note": "口语化提问"},
    {"q": "食堂好吃吗", "must": ["好吃", "难吃", "口味", "饭菜"], "note": "评价型"},
    {"q": "校园网网速怎么样", "must": ["校园网", "网速", "断网", "网络"], "note": "专有名词"},
    {"q": "军训累不累", "must": ["军训"], "note": "话题检索"},
    {"q": "专升本难不难", "must": ["专升本"], "note": "招生相关"},
    {"q": "学费多少钱一年", "must": ["学费", "学杂费", "住宿费"], "note": "费用询问"},
    {"q": "快递点在哪里", "must": ["快递", "驿站", "取件"], "note": "生活服务"},
    {"q": "有没有独立卫浴", "must": ["独立卫浴", "独立卫生间", "卫浴"], "note": "领域词不被切碎"},
    {"q": "寝室条件如何", "must": ["寝室"], "note": "纯同义词：只写寝室，考扩展能力"},
]


def _is_relevant(doc, must):
    text = doc["text"] or ""
    return any(w in text for w in must)


def _like_baseline(idx, q, mask, top_k=K):
    """复刻"没有 RAG"时的检索能力：分词后按字面包含过滤 + 点赞排序。

    这就是旧实现 search_comments 的行为——不排相关度，只按点赞把命中的评论排出来。
    """
    terms = rag.tokenize(q, query=True) or rag.tokenize(q) or [q]
    hits = []
    for i, d in enumerate(idx.docs):
        if not mask[i]:
            continue
        if any(t in (d["text"] or "") for t in terms):
            hits.append(i)
    hits.sort(key=lambda i: -(idx.docs[i]["like_count"] or 0))
    return hits[:M]


def _rank_from_result(result):
    return [it["rank"] and idx_index(result, it) for it in result["items"]]


def idx_index(result, it):
    return it.get("_doc_index")


def evaluate(dataset_id=None, k=K, m=M, verbose=False):
    dataset_id = dataset_id or config.DEFAULT_DATASET_ID
    idx = rag.get_index(dataset_id)
    mask = rag._mask(idx, {})

    strategies = {
        "LIKE 基线（旧实现）": None,
        "BM25 单路": dict(retrievers=("bm25",)),
        "向量单路": dict(retrievers=("vector",)),
        "混合·加权（线上）": dict(),
        "混合·RRF": dict(fusion="rrf"),
    }
    rows = {name: {"p": 0.0, "mrr": 0.0, "hit": 0} for name in strategies}

    for case in CASES:
        # 为每种策略取回 top-M，用于同时算 P@K 与 MRR@M
        for name, opts in strategies.items():
            if opts is None:
                order = _like_baseline(idx, case["q"], mask)
                docs = [idx.docs[i] for i in order[:m]]
            else:
                res = rag.retrieve(case["q"], dataset_id, top_k=m, **opts)
                docs = [{"text": it["text"]} for it in res["items"]]
            rel = [_is_relevant(d, case["must"]) for d in docs]
            p_at_k = sum(rel[:k]) / float(k)
            first = next((i + 1 for i, r in enumerate(rel) if r), None)
            mrr = 1.0 / first if first else 0.0
            rows[name]["p"] += p_at_k
            rows[name]["mrr"] += mrr
            rows[name]["hit"] += 1 if first else 0
            if verbose:
                print(f"    {name:<18} P@{k}={p_at_k:.2f} RR={mrr:.2f}  「{case['q']}」")

    n = len(CASES)
    out = []
    for name, v in rows.items():
        out.append({
            "strategy": name,
            "precision_at_k": round(v["p"] / n, 4),
            "mrr_at_m": round(v["mrr"] / n, 4),
            "hit_rate": round(v["hit"] / n, 4),
            "k": k, "m": m, "cases": n,
        })
    out.sort(key=lambda r: -r["precision_at_k"])
    return {"dataset_id": dataset_id, "index": idx.stats(), "rows": out}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=config.DEFAULT_DATASET_ID)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    db.init_db()
    res = evaluate(args.dataset, verbose=args.verbose)
    print(f"\n数据集：{res['dataset_id']}｜语料 {res['index']['docs']} 条｜"
          f"词表 {res['index']['vocab']}｜索引构建 {res['index']['build_ms']}ms")
    print(f"{'策略':<22}{'P@5':>8}{'MRR@10':>10}{'命中率':>10}")
    print("-" * 52)
    for r in res["rows"]:
        print(f"{r['strategy']:<22}{r['precision_at_k']:>8.3f}{r['mrr_at_m']:>10.3f}{r['hit_rate']:>10.3f}")

    base = next(r for r in res["rows"] if r["strategy"].startswith("LIKE"))
    best = res["rows"][0]
    gain = (best["precision_at_k"] - base["precision_at_k"]) / base["precision_at_k"] * 100 \
        if base["precision_at_k"] else 0
    print(f"\n最优策略：{best['strategy']}　P@5 相对 LIKE 基线提升 {gain:+.1f}%")
    return res


if __name__ == "__main__":
    main()
