# -*- coding: utf-8 -*-
"""前端性能对比探针（P1-4）。

用法：
    .venv/Scripts/python.exe tools/perf_probe.py                  # 默认对比洞察页
    .venv/Scripts/python.exe tools/perf_probe.py /insights /dashboard

它做什么：
    在**同一台机器、同一份数据、同一个浏览器**下跑两遍同一页：
      A：window.__CP_NO_LAZY = true  （关掉图表惰性初始化 = 改造前的行为）
      B：不设置                        （当前实现）
    采集首屏耗时、JS 传输字节、首屏已实例化的图表数量、是否请求了词云插件。

为什么不用 Lighthouse：
    Lighthouse 的分数受运行环境的 CPU/网络模拟影响很大，同一份代码两次能差 10 分。
    这个项目要说的是"同一环境下的前后差异"，那就把变量控制到只剩一个开关。
    如果机器上装了 Lighthouse，本文的数据可以再补一组对照，但不作为结论依据。
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("CP_BASE", "http://127.0.0.1:8123")
PAGES = sys.argv[1:] or ["/insights"]

# 采集一次页面加载的关键指标
_PROBE_JS = """
() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const res = performance.getEntriesByType('resource');
  let js = 0, jsN = 0, total = 0;
  res.forEach(r => {
    total += r.transferSize || 0;
    if (/\\.js(\\?|$)/.test(r.name)) { js += r.transferSize || 0; jsN++; }
  });
  let inited = 0, charts = 0;
  document.querySelectorAll('.chart').forEach(el => {
    charts++;
    if (window.echarts && window.echarts.getInstanceByDom && window.echarts.getInstanceByDom(el)) inited++;
  });
  const wordcloud = res.some(r => /wordcloud/.test(r.name));
  return {
    dcl: Math.round(nav.domContentLoadedEventEnd || 0),
    load: Math.round(nav.loadEventEnd || 0),
    fcp: Math.round((performance.getEntriesByName('first-contentful-paint')[0] || {}).startTime || 0),
    js_bytes: js, js_files: jsN, total_bytes: total,
    charts: charts, charts_inited: inited, wordcloud_loaded: wordcloud,
    requests: res.length
  };
}
"""


def measure(page, path, lazy_on):
    page.goto("about:blank")
    page.goto(BASE + path, wait_until="load")
    page.wait_for_timeout(1500)          # 等第一轮接口回来并画完
    data = page.evaluate(_PROBE_JS)
    data["lazy"] = lazy_on
    return data


def main():
    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        for path in PAGES:
            for lazy_on in (False, True):
                ctx = browser.new_context(viewport={"width": 1560, "height": 900})
                if not lazy_on:
                    ctx.add_init_script("window.__CP_NO_LAZY = true;")
                page = ctx.new_page()
                d = measure(page, path, lazy_on)
                d["page"] = path
                out.append(d)
                ctx.close()
        browser.close()

    print(f"{'页面':<12}{'惰性':<6}{'DCL(ms)':>9}{'Load(ms)':>10}{'FCP(ms)':>9}"
          f"{'JS(KB)':>9}{'图表/已建':>12}{'词云库':>8}{'请求数':>7}")
    print("-" * 84)
    for d in out:
        print(f"{d['page']:<12}{'开' if d['lazy'] else '关':<6}{d['dcl']:>9}{d['load']:>10}"
              f"{d['fcp']:>9}{d['js_bytes'] / 1024:>9.1f}"
              f"{str(d['charts_inited']) + '/' + str(d['charts']):>12}"
              f"{'加载' if d['wordcloud_loaded'] else '未加载':>8}{d['requests']:>7}")

    print("\nJSON:")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
