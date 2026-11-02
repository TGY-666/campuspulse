# -*- coding: utf-8 -*-
"""演示预热：把"第一次点开很慢"的那几秒钟，挪到启动时。

现场演示最怕的不是功能不行，而是**第一次**很慢：评委看着转圈，你在解释。
真正慢的只有三处，且都是一次性的：

    ① jieba 词典加载 —— 首次分词要建前缀词典，约 0.5–1.0s；
    ② RAG 索引构建 —— 每个数据集要把 1,800 条评论切词、算 BM25 与 n-gram 向量（约 1s/数据集）；
    ③ 首次统计查询 —— SQLite 页缓存 + Pandas 冷启动。

预热只做**读操作**：不写库、不改数据版本、不生成报告，所以重复跑没有副作用，
跑完与不跑的结果完全一致（这条在验收里断言）。

两个刻意的决定：

- **放在 run.py，不放在 create_app()。**
  验收脚本与测试都调 create_app；如果预热藏在里面，测试会莫名其妙变慢，
  还会和"拦截耗时 < 500ms"这类断言抢 CPU。启动慢一点是服务器的责任，不是应用工厂的。
- **失败只记日志，不阻断启动。**
  预热是优化，不是依赖。词典加载失败也要能回答问题（只是第一问慢一点）。
"""
import time

from . import analytics, config, db, rag


def _timed(fn):
    started = time.time()
    try:
        return fn(), int((time.time() - started) * 1000), None
    except Exception as e:                     # 预热失败不影响主流程
        return None, int((time.time() - started) * 1000), str(e)[:160]


def run(dataset_ids=None, quiet=True):
    """执行预热，返回每一步的耗时。可重复调用（第二次基本全是缓存命中）。"""
    started = time.time()
    report = {"steps": [], "total_ms": 0}

    def step(name, fn):
        value, ms, err = _timed(fn)
        report["steps"].append({"step": name, "ms": ms, "ok": err is None, "error": err})
        if not quiet:
            print(f"  预热 {name}: {ms}ms" + (f"  失败 {err}" if err else ""))
        return value

    step("分词器词典", lambda: len(rag.tokenize("宿舍电费太贵了")))
    ids = dataset_ids or [d["dataset_id"] for d in db.list_datasets()]

    for ds_id in ids:
        step(f"检索索引 {ds_id}",
             lambda ds_id=ds_id: rag.retrieve("宿舍 电费", ds_id, top_k=3).get("candidates", 0))

    # 第二遍必须是缓存命中——这才是"预热完成"的真正判据，
    # 只看第一遍会分不清"建完了"和"建了个空索引"
    hits = 0
    for ds_id in ids:
        r = step(f"缓存校验 {ds_id}", lambda ds_id=ds_id: rag.is_cached(ds_id))
        if r:
            hits += 1

    for ds_id in ids[:1]:                       # 首页遇到的第一个统计查询
        f = analytics.build_filters(dataset_id=ds_id)
        step("统计总览", lambda f=f: analytics.stats_overview(f).get("total", 0))

    report["datasets"] = len(ids)
    report["cached_datasets"] = hits
    report["all_cached"] = bool(ids) and hits == len(ids)
    report["total_ms"] = int((time.time() - started) * 1000)
    report["engine"] = "deepseek" if config.DEEPSEEK_API_KEY else "rules"
    return report


def run_async(quiet=True):
    """在后台线程里预热，不阻塞服务启动（端口先起来，用户先能用）。

    注意：后台线程**没有** Flask 请求上下文，也就没有当前租户。
    不显式给一个身份的话，`db.list_datasets()` 会返回空列表，
    预热就会"成功执行、实际什么也没做"——而且日志里一切正常，最难排查。
    这里固定按默认租户预热：单机部署下它就是主租户。
    """
    import threading

    from . import tenancy

    box = {}

    def worker():
        token = tenancy.set_context(tenancy.system_context())
        try:
            box.update(run(quiet=quiet))
            if not quiet:
                r = box
                print(f"  预热完成: {r.get('total_ms')}ms · "
                      f"缓存命中 {r.get('cached_datasets')}/{r.get('datasets')} 个数据集"
                      f"{'' if r.get('all_cached') else '  ← 未全部命中，首问可能偏慢'}", flush=True)
        except Exception as e:
            # 以前这里是 pass：预热失败就彻底静默了，用户只会觉得"第一次问怎么这么慢"。
            # 预热是优化不是依赖，所以不阻断启动，但必须让人看见。
            import traceback
            print(f"  预热失败（不影响使用，仅首问变慢）: {e}", flush=True)
            traceback.print_exc()
        finally:
            tenancy.reset_context(token)
