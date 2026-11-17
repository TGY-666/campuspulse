# -*- coding: utf-8 -*-
"""区间风险研判 · 命令行入口。

定时器已经内置在服务进程里（每小时自己检查一次），这个脚本是它的手动版：
给外部计划任务用，也用来在演示前把某个时间段补跑一次。

**这里只出研判结论，不建单。** 建单在页面上点（`/departments` 那张表的
「建单」列）。理由是开不开单取决于"这几天有没有人在盯、别的渠道是不是也在
说同一件事"，这两件事系统都不知道，所以命令行也不替人做这个决定。

用法：
    python tools/monthly_assess.py                      # 等价于定时器那一跳：检查上个月
    python tools/monthly_assess.py --from 2026-09-15 --to 2026-10-14   # 任意时间段
    python tools/monthly_assess.py --period 2026-10     # 整个自然月
    python tools/monthly_assess.py --from 2023-10-01 --to 2023-11-20 --dataset ds_2023_all
    python tools/monthly_assess.py --from ... --to ... --force         # 同区间重跑并覆盖
    python tools/monthly_assess.py --list               # 看已有研判记录

注意：脚本以"系统任务"身份运行（归属默认租户），研判记录里的触发方是
`manual`、操作人不是某个真人——定期研判本来就该是系统发起的。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campuspulse import db, monthly, tenancy          # noqa: E402


def _print_run(r):
    flag = "（定时任务）" if r.get("trigger") == "schedule" else ""
    print(f"  区间 {r['date_from']} ~ {r['date_to']}（标签 {r['period']}）{flag}")
    print(f"  数据集 {r['dataset_id']} · 评论 {r['sample']} 条（负面 {r['negative']}）"
          f" · 引擎 {r['engine']}")
    linked = len([t for t in (r.get("topics") or []) if t.get("ticket_code")])
    print(f"  风险主题 {len(r.get('topics') or [])} 个，其中 {linked} 个这段已有工单"
          + ("： " + ", ".join(r["ticket_codes"]) if r.get("ticket_codes") else ""))
    for t in (r.get("topics") or []):
        mark = f" → {t['ticket_code']}" if t.get("ticket_code") else ""
        print(f"    {t['risk']:>3}% {t['level']:<2} {t['priority']} "
              f"{t['dimension']:<6} 负面 {t['negative']}/{t['total']}"
              f"{mark}{('（' + t['ticket_note'] + '）') if t.get('ticket_note') else ''}")
    print("  要不要建单在页面上点（工具不替人决定）。")


def main():
    ap = argparse.ArgumentParser(description="区间风险研判")
    ap.add_argument("--from", dest="date_from", help="开始日期 YYYY-MM-DD（需与 --to 成对）")
    ap.add_argument("--to", dest="date_to", help="结束日期 YYYY-MM-DD")
    ap.add_argument("--period", help="YYYY-MM，整个自然月（与 --from/--to 二选一）")
    ap.add_argument("--dataset", help="数据集 ID，缺省=config.MONTHLY_DATASET")
    ap.add_argument("--force", action="store_true", help="同区间重跑并覆盖快照")
    ap.add_argument("--no-ai", action="store_true", help="不用模型，只跑统计规则")
    ap.add_argument("--list", action="store_true", help="列出已有研判记录")
    args = ap.parse_args()

    db.init_db()
    token = tenancy.set_context(tenancy.system_context())
    try:
        if args.list:
            rows = monthly.list_runs(limit=24)
            if not rows:
                print("还没有研判记录。")
                return 0
            print(f"{'区间':<24}{'数据集':<14}{'状态':<8}{'评论':>5}{'负面':>5}{'已建单':>6}  "
                  f"触发 / 完成时间")
            for r in rows:
                span = f"{r['date_from']}~{r['date_to']}"
                print(f"{span:<24}{r['dataset_id']:<14}{r['status']:<8}"
                      f"{r['sample']:>5}{r['negative']:>5}{r['tickets']:>6}  "
                      f"{r['trigger']} / {r['finished_at'] or '-'}")
            return 0

        has_range = bool(args.date_from or args.date_to)
        if has_range and args.period:
            print("--period 与 --from/--to 只能给一种")
            return 2
        if not has_range and not args.period and not args.dataset:
            msg = monthly.tick_once()
            print(f"[定时检查] {msg}")
            return 0

        out = monthly.run(period=args.period, date_from=args.date_from, date_to=args.date_to,
                          dataset_id=args.dataset, trigger="manual", force=args.force,
                          use_ai=not args.no_ai)
        if out.get("skipped"):
            print(f"跳过：{out['skip_reason']}（加 --force 可覆盖重跑）")
        _print_run(out)
        return 0
    finally:
        tenancy.reset_context(token)


if __name__ == "__main__":
    sys.exit(main())
