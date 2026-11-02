# -*- coding: utf-8 -*-
"""清理 data/uploads/ 下的孤儿目录。

孤儿 = 目录名（即 dataset_id）在 `datasets` 表里查不到。成因有两种：

1. **导入失败**：清洗后无有效评论，接口返回 400 但目录已经建好。
   这条路径已在 `web.py::import_dataset` 修掉（失败即清），本工具负责历史残留；
2. **数据集被删除**：删库记录时目录没跟着删（另一处待补的口子）。

默认 dry-run，只报告不动手；加 `--apply` 才删，且**只删空目录**。
非空目录一律只列出——里面可能是用户原始的爬虫文件，分不清就不删。

用法：
    .venv/Scripts/python.exe tools/clean_uploads.py
    .venv/Scripts/python.exe tools/clean_uploads.py --apply
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from campuspulse import config, db  # noqa: E402


def main():
    apply = "--apply" in sys.argv
    up = config.UPLOAD_DIR
    if not os.path.isdir(up):
        print("上传目录不存在，无需清理")
        return 0

    known = {r["dataset_id"] for r in db.query_all("SELECT dataset_id FROM datasets")}
    dirs = sorted(d for d in os.listdir(up) if os.path.isdir(os.path.join(up, d)))

    live, empty_orphan, full_orphan = [], [], []
    for d in dirs:
        full = os.path.join(up, d)
        if d in known:
            live.append(d)
            continue
        files = os.listdir(full)
        (empty_orphan if not files else full_orphan).append((d, files))

    print(f"上传目录：{up}")
    print(f"现有 {len(dirs)} 个子目录")
    print(f"  在用（库里有数据集）：{len(live)}")
    print(f"  孤儿·空目录：{len(empty_orphan)}  ← 可安全清理")
    print(f"  孤儿·非空目录：{len(full_orphan)}  ← 只报告，不动")

    if full_orphan:
        print("\n非空孤儿目录（请人工确认后再处理）：")
        for d, files in full_orphan[:15]:
            size = sum(os.path.getsize(os.path.join(up, d, f))
                       for f in files if os.path.isfile(os.path.join(up, d, f)))
            print(f"  {d}  {len(files)} 个文件 / {size / 1024:.1f} KB  {files[:3]}")
        if len(full_orphan) > 15:
            print(f"  …… 另有 {len(full_orphan) - 15} 个")

    if not empty_orphan:
        print("\n没有需要清理的空目录。")
        return 0

    if not apply:
        print(f"\n[dry-run] 将删除 {len(empty_orphan)} 个空目录。确认无误后加 --apply 执行：")
        for d, _ in empty_orphan[:10]:
            print(f"  - {d}")
        if len(empty_orphan) > 10:
            print(f"  …… 另有 {len(empty_orphan) - 10} 个")
        return 0

    removed = 0
    for d, _ in empty_orphan:
        full = os.path.join(up, d)
        try:
            shutil.rmtree(full)
        except Exception as e:
            # Windows 上 rmtree 删空目录偶发抛 SHFileOperationW 0x2（找不到），
            # 而目录其实已经被删掉了。判据改成"删完还在不在"，不看有没有抛异常。
            if os.path.exists(full):
                print(f"  删除失败 {d}：{e}")
                continue
        removed += 1
    print(f"\n已删除 {removed} 个空目录，剩余 {len(os.listdir(up))} 个子目录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
