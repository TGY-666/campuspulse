"""清理「全量历史样本」里重复采集的评论。

为什么需要单独跑一遍：ETL 的去重阶段是按 comment_id 判的，同一个评论在同一批
采集里换了 id 就会漏过去。结果 ds_hist_all 里有 20 条正文完全一样（其中一条
「好充实的校园生活 为你点赞」被采了 10 次），另有若干条只差句末语气词或标点
（「学费多少啊 / 学费多少呀」）——人工看就是同一句话。

处理方式：按 canon() 分组，每组只留**最早那条**，其余把 cleaning_status 改成
duplicate 并写明原因。不做物理删除，随时可以回滚。

    python tools/dedupe_hist.py            # 预演，只打印
    python tools/dedupe_hist.py --apply    # 执行
"""
import argparse
import json
import os
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from campuspulse import db                                # noqa: E402
from tools.gen_dataset_timeline import canon, dedupe_near  # noqa: E402

DATASET = "ds_hist_all"

# 归一化之后还有一类：同一句被打字打成了两个版本（「该开学了吧」/「开学了吧」、
# 「问的是」/「询问的是」）。canon 抹不掉这种差异，但它们读起来就是一句。
# 真实语料里不同人说出高度相似的话是正常的，所以这里的门槛必须高——
# 0.90 以上基本只剩"同一句话的两次录入"，不会误伤"两个人问了相似的问题"。
NEAR_SIM = 0.90


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dataset", default=DATASET)
    args = ap.parse_args()

    rows = db.query_all(
        "select comment_ref, content, comment_time from comments "
        "where dataset_id = ? and cleaning_status = 'kept' "
        "order by comment_time asc, comment_ref asc",
        (args.dataset,))
    print(f"{args.dataset} 当前有效 {len(rows)} 条")

    groups = defaultdict(list)
    for r in rows:
        groups[canon(r["content"] or "")].append(r)

    drop = []
    for key, items in groups.items():
        if len(items) < 2:
            continue
        keep = items[0]
        for r in items[1:]:
            drop.append((r, keep))

    print(f"归一化后重复组 {sum(1 for v in groups.values() if len(v) > 1)} 个，"
          f"需置为 duplicate {len(drop)} 条")

    # 第二道：归一化抹不掉的近似重复
    survivors = [items[0] for items in groups.values()]
    kept_texts = dedupe_near([r["content"] for r in survivors], threshold=NEAR_SIM)
    kept_set = set(kept_texts)
    by_text = {r["content"]: r for r in survivors}
    near_drop = [by_text[t] for t in by_text if t not in kept_set]
    drop.extend((r, None) for r in near_drop)
    print(f"近似重复（相似度≥{NEAR_SIM}）再置为 duplicate {len(near_drop)} 条")
    print("\n样例（保留 ← 置为重复）：")
    for r, keep in drop[:12]:
        left = keep["content"] if keep else "（近似重复，保留更完整的一条）"
        print(f"  {left[:30]!r}  ←  {r['content'][:30]!r}")

    if not args.apply:
        print("\n预演结束。加 --apply 执行。")
        return

    conn = db.get_conn()
    for r, _ in drop:
        conn.execute(
            "update comments set cleaning_status = 'duplicate', "
            "filter_reason = ? where comment_ref = ?",
            ("同一评论重复采集（正文归一化后相同）", r["comment_ref"]))

    # 数据集上的有效数与质量块要跟着改，不然数据质量面板会和列表对不上
    meta = db.query_one("select * from datasets where dataset_id = ?", (args.dataset,))
    quality = json.loads(meta["quality"] or "{}")
    valid = meta["valid_count"] - len(drop)
    quality["valid_count"] = valid
    quality["duplicate"] = quality.get("duplicate", 0) + len(drop)
    quality["effective_rate"] = round(valid / max(1, meta["raw_count"]) * 100, 2)
    conn.execute(
        "update datasets set valid_count = ?, quality = ? where dataset_id = ?",
        (valid, json.dumps(quality, ensure_ascii=False), args.dataset))
    conn.commit()
    print(f"\n已处理：有效 {meta['valid_count']} → {valid}，"
          f"有效率 {quality['effective_rate']}%")


if __name__ == "__main__":
    main()
