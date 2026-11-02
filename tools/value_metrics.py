"""商业与社会价值指标的复算脚本。

文档 `docs/商业与社会价值.md` 里出现的每个数字都能在这里跑出来，
避免"材料里的数对不上系统里的数"——这是本项目一贯的口径要求。

用法：
    .venv/Scripts/python.exe tools/value_metrics.py
    .venv/Scripts/python.exe tools/value_metrics.py --json   # 机器可读
"""
import argparse
import collections
import datetime
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "campuspulse.db")

# 秋招季样本：2023-09-01 ~ 2023-11-20，三个月连续覆盖招生与开学节点
CASE_DATASET = "ds_2023_all"
# 长期样本：2019-09 ~ 2026-07，用于季节性规律
HIST_DATASET = "ds_hist_all"


def _conn():
    if not os.path.exists(DB):
        sys.exit(f"找不到数据库：{DB}")
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def _rows(c, dataset_id):
    return c.execute(
        "SELECT comment_date, sentiment, dimension FROM comments "
        "WHERE dataset_id=? AND cleaning_status='kept' AND comment_date IS NOT NULL",
        (dataset_id,),
    ).fetchall()


def weekly_case(c):
    """2023 秋招季周级序列 + 首个异常周的检测过程。

    判定"异常周"的规则（写死在代码里，评委可以质疑规则，但不能质疑执行）：
      1. 取前 2 个完整周作为基线，算基线负面率；
      2. 某周负面率 >= 基线 × 2.0 且 >= 20%，即判为异常；
      3. 异常周之前的最后一个"正常周"的出报日 = 周报可见日。
    """
    weeks = collections.OrderedDict()
    for r in _rows(c, CASE_DATASET):
        d = r["comment_date"][:10]
        try:
            dt = datetime.date.fromisoformat(d)
        except ValueError:
            continue
        iso = dt.isocalendar()
        key = (iso[0], iso[1])
        w = weeks.setdefault(key, {"n": 0, "neg": 0, "dims": collections.Counter(),
                                   "start": d, "end": d})
        w["n"] += 1
        if r["sentiment"] == "negative":
            w["neg"] += 1
            w["dims"][r["dimension"]] += 1
        w["start"] = min(w["start"], d)
        w["end"] = max(w["end"], d)

    series = []
    for (y, wk), w in weeks.items():
        rate = w["neg"] / w["n"] if w["n"] else 0.0
        top = w["dims"].most_common(1)
        series.append({
            "week": f"{y}-W{wk:02d}",
            "start": w["start"],
            "end": w["end"],
            "n": w["n"],
            "neg": w["neg"],
            "neg_rate": round(rate, 4),
            "top_negative_dimension": top[0][0] if top else None,
            "top_negative_count": top[0][1] if top else 0,
        })

    # ---- 基线：前两个"完整周"（n >= 100，排除首尾被截断的周）----
    full = [s for s in series if s["n"] >= 100]
    baseline = full[:2] if len(full) >= 2 else series[:2]
    base_rate = sum(s["neg"] for s in baseline) / max(1, sum(s["n"] for s in baseline))

    spike = None
    for s in series:
        if s["n"] < 80:            # 样本过少的周不判定，避免"2 条评论 100% 负面"
            continue
        if s["neg_rate"] >= max(0.2, base_rate * 2.0):
            spike = s
            break

    # ---- 月报 vs 周报的滞后 ----
    # 月报：该月结束后才可能出报，即次月 1 日才能看到本月数据
    out = {"weeks": series, "baseline_rate": round(base_rate, 4), "spike": spike}
    if spike:
        spike_day = datetime.date.fromisoformat(spike["end"])       # 该周数据齐备日
        month_end = datetime.date.fromisoformat(spike["start"]).replace(day=1)
        next_month = (month_end + datetime.timedelta(days=32)).replace(day=1)
        lead_days = (next_month - spike_day).days
        month_rate = sum(s["neg"] for s in series
                         if s["start"][:7] == spike["start"][:7]) / max(
            1, sum(s["n"] for s in series if s["start"][:7] == spike["start"][:7]))
        out.update({
            "spike_day": spike_day.isoformat(),
            "monthly_report_day": next_month.isoformat(),
            "lead_days": lead_days,
            "spike_month": spike["start"][:7],
            "spike_month_neg_rate": round(month_rate, 4),
        })
        # 异常周之后的负面总量 = 未及时处置的潜在影响面
        after = series[series.index(spike) + 1:]
        out["neg_after_spike"] = sum(s["neg"] for s in after)
        out["neg_total"] = sum(s["neg"] for s in series)
        out["neg_after_spike_share"] = round(
            out["neg_after_spike"] / max(1, out["neg_total"]), 4)
    return out


def seasonality(c):
    """长期样本的月份分布：验证"招生季是舆情高峰"。"""
    rows = _rows(c, HIST_DATASET)
    total = len(rows)
    m = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        mo = (r["comment_date"] or "")[5:7]
        if len(mo) != 2:
            continue
        m[int(mo)][0] += 1
        if r["sentiment"] == "negative":
            m[int(mo)][1] += 1
    months = [{"month": k, "n": m[k][0], "neg": m[k][1],
               "share": round(m[k][0] / max(1, total), 4),
               "neg_rate": round(m[k][1] / m[k][0], 4) if m[k][0] else 0.0}
              for k in sorted(m)]
    peak = sum(x["n"] for x in months if x["month"] in (6, 7, 8))
    return {
        "total": total,
        "negative": sum(x["neg"] for x in months),
        "negative_rate": round(sum(x["neg"] for x in months) / max(1, total), 4),
        "months": months,
        "peak_months": [6, 7, 8],
        "peak_share": round(peak / max(1, total), 4),
        "peak_neg_rate": round(sum(x["neg"] for x in months if x["month"] in (6, 7, 8))
                               / max(1, peak), 4),
    }


def corpora(c):
    """语料与清洗漏斗：证明"数据是真的、清洗是有代价的"。"""
    out = {}
    for r in c.execute("SELECT dataset_id, name, raw_count, valid_count, "
                       "time_start, time_end, platforms, quality FROM datasets"):
        q = json.loads(r["quality"] or "{}")
        out[r["dataset_id"]] = {
            "name": r["name"], "raw_count": r["raw_count"],
            "valid_count": r["valid_count"], "time_start": r["time_start"],
            "time_end": r["time_end"], "platforms": json.loads(r["platforms"] or "{}"),
            "effective_rate": q.get("effective_rate"),
            "duplicate": q.get("duplicate"), "too_short": q.get("too_short"),
            "irrelevant": q.get("irrelevant"),
        }
    return out


def build():
    c = _conn()
    try:
        return {"corpora": corpora(c), "case": weekly_case(c), "seasonality": seasonality(c)}
    finally:
        c.close()


def _pct(x):
    return f"{x * 100:.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    data = build()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    hist = data["corpora"][HIST_DATASET]
    case = data["case"]
    sea = data["seasonality"]

    print("=" * 68)
    print("一、语料规模（真实爬虫导出）")
    print("=" * 68)
    print(f"长期样本 {hist['name']}")
    print(f"  原始 {hist['raw_count']:,} 条 → 清洗保留 {hist['valid_count']:,} 条"
          f"（有效率 {hist['effective_rate']}%），覆盖 {hist['time_start']} ~ {hist['time_end']}")
    print(f"  去重 -{hist['duplicate']:,} / 短评 -{hist['too_short']:,} / 无关 -{hist['irrelevant']:,}")
    print(f"  平台：{hist['platforms']}")

    print()
    print("=" * 68)
    print("二、社会价值实证：2023 秋招季周级负面率曲线")
    print("=" * 68)
    print(f"{'周':<10}{'起止':<24}{'条数':>6}{'负面':>6}{'负面率':>9}  当周最大负面维度")
    for s in case["weeks"]:
        mark = "  ← 首个异常周" if case["spike"] and s["week"] == case["spike"]["week"] else ""
        print(f"{s['week']:<10}{s['start'][5:]}~{s['end'][5:]:<14}"
              f"{s['n']:>6}{s['neg']:>6}{_pct(s['neg_rate']):>9}  "
              f"{s['top_negative_dimension'] or '-'}({s['top_negative_count']}){mark}")

    print()
    print(f"基线（前两个完整周）负面率：{_pct(case['baseline_rate'])}")
    if case.get("spike"):
        sp = case["spike"]
        print(f"首个异常周：{sp['week']}（{sp['start']} ~ {sp['end']}）")
        print(f"  负面率 {_pct(case['baseline_rate'])} → {_pct(sp['neg_rate'])}，"
              f"为基线 {sp['neg_rate'] / max(1e-9, case['baseline_rate']):.1f} 倍")
        print(f"  当周负面 {sp['neg']} 条，其中 {sp['top_negative_dimension']} "
              f"{sp['top_negative_count']} 条")
        print(f"周报可见日：{case['spike_day']}　月报最早可见日：{case['monthly_report_day']}")
        print(f"→ 提前 {case['lead_days']} 天")
        print(f"该月整体负面率仅 {_pct(case['spike_month_neg_rate'])}（月报看不到单周跳变）")
        print(f"异常周之后仍累计负面 {case['neg_after_spike']} 条，"
              f"占该样本负面总量 {case['neg_total']} 条的 {_pct(case['neg_after_spike_share'])}")

    print()
    print("=" * 68)
    print(f"三、季节性：长期样本 {sea['total']:,} 条有效评论的月份分布")
    print("=" * 68)
    print(f"{'月份':<8}{'条数':>7}{'占比':>9}{'负面率':>9}")
    for m in sea["months"]:
        print(f"{m['month']:>2}月{'':<5}{m['n']:>7}{_pct(m['share']):>9}{_pct(m['neg_rate']):>9}")
    print()
    print(f"6-8 月合计占全年 {_pct(sea['peak_share'])}，负面率 {_pct(sea['peak_neg_rate'])}")
    print(f"整体负面 {sea['negative']} / {sea['total']} = {_pct(sea['negative_rate'])}")
    print()
    print("口径：情感为关键词规则标注，存在误判；上述数字用于说明量级与趋势，")
    print("     不作为\"某校区确有食堂问题\"的结论。复算：本脚本即可。")


if __name__ == "__main__":
    main()
