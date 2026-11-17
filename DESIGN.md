---
name: CampusPulse 高校舆情智能洞察平台
description: 面向校办与归口部门的数据密集仪表盘；浅色白底 + 淡蓝辅助，圆角卡片，可复算的口径优先于装饰。
colors:
  primary: "#3358E8"
  primary-dark: "#2D4ED7"
  primary-soft: "#EEF2FF"
  primary-line: "#D4DDFC"
  ink: "#172033"
  ink-2: "#3E4C63"
  ink-3: "#5A6A83"
  ink-4: "#5F6E85"
  ink-faint: "#8C99AD"
  bg: "#F3F6FA"
  surface: "#FFFFFF"
  surface-2: "#F8FAFD"
  surface-3: "#F5F8FC"
  surface-sunken: "#F1F5F9"
  line: "#E3E8F0"
  line-2: "#EDF1F6"
  pos: "#15803D"
  neg: "#D92D20"
  warn: "#D97706"
  neu: "#94A3B8"
  pos-soft: "#ECFDF3"
  neg-soft: "#FEF3F2"
  warn-soft: "#FFFAEB"
  neu-soft: "#F1F5F9"
  sidebar-bg: "#111B30"
  sidebar-ink: "#E7ECF7"
  login-bg-top: "#070F1E"
  login-bg-mid: "#0A1830"
  login-bg-bottom: "#060B14"
  login-bg-node: "#9BBCFF"
  risk-magnitude: "#64748B"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, Hiragino Sans GB, Microsoft YaHei, Source Han Sans SC, sans-serif"
    fontSize: "30px"
    fontWeight: 720
    lineHeight: 1.15
  headline:
    fontSize: "26px"
    fontWeight: 720
    lineHeight: 1.2
  title:
    fontSize: "16px"
    fontWeight: 650
    lineHeight: 1.5
  body:
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  card: "16px"
  control: "10px"
spacing:
  card-padding: "20px"
  grid-gap: "16px"
components:
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "20px"
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "#FFFFFF"
    rounded: "{rounded.control}"
  badge:
    backgroundColor: "{colors.primary-soft}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.control}"
  input:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
---

# CampusPulse 设计系统

> 本文件由 `/impeccable document`（扫描模式）从 `static/cp/app.css`、`login.css`、`campuspulse/templates/*.html` 提取，2026-11-15。令牌是规范，正文是使用语境。**权威实现仍是 CSS 本身**；两者冲突时以 `:root` 声明为准并更新本文件。

## Overview

模式是 **Operate**（数据密集仪表盘，兼 Read）：使用者是校办值班负责人、归口部门处置员、分析员与评审老师，常在投影仪或 390px 手机竖屏上读。因此扫读性、口径可复算、无障碍优先于表达欲；品牌只落在精确细节里。产品自著的是**判断链**（风险指数四段构成 → 归口部门 → 部门响应 48 / 24 **工作小时** → 工单），不是外壳。**全站不写裸 `h`**：`sla_hours` 的单位是工作小时（每天 7 个），`48h` 会被读成两个自然日，差 4.8 倍且方向相反（第 11 轮定性、第 16 轮统一实现，见「第 16 轮同步」）。

一条贯穿全站的硬规则：**承载单位、分母、口径、时区、图例、控件名的说明必须可见**；只有纯旁白才允许用 `data-hint` 隐藏。判据是「删掉这句，用户会不会算错数或找错控件」。

## Colors

主色 `#3358E8`（白底 5.67:1、soft 底 5.07:1）。语义色成对出现：`--pos / --neg / --warn / --neu` 是图形与小字档，`*-soft` + `*-ink` 是徽章底与字。三档文字色按「最脏的底」定：浅底上留不住第四档灰度，所以 `--ink-3`/`--ink-4` 视觉接近，`--ink-faint` 只给图形（圆点、轨道），**任何文字对比度必须 ≥4.5:1**。

情感固定 **红＝负面 / 绿＝正面**（中国用户习惯），与 A 股涨跌配色无关。风险四段的「量级」用中性 `--risk-magnitude #64748B`，**不占用负面红**——一个分数里的四个成因不是四种情绪。

深色主题由 `[data-theme="dark"]` 覆盖表面 4 个变量实现，深浅各有一套语义色；评审与验收以浅色为准。

## Typography

只有 **9 档** `--fs-*`：micro 12 / xs 12 / sm 13 / base 14 / md 16 / lg 18 / xl 20 / 2xl 26 / display 30。**禁止裸 px 字号，也禁止 `clamp()` 自造档位**；新增字号先问落在哪一档。图表（canvas）读不到 CSS 变量，用 `static/cp/core.js` 的 `CP.CHART_FS`（同一套档位、下限 12px）；改 `:root` 必须同步它。

第 8 轮（2026-11-15）**未改 `:root` 的 9 档值**：档位集合与每个变量的值都没动。本轮「提字号」只是把承载口径的句子从 `--fs-xs` / `--fs-micro`（12px）**重新映射**到 `--fs-sm`（13px）/ `--fs-base`（14px），是改引用、不是改变量值；因此 `CP.CHART_FS` **无需改动**（core 组按契约第 3 节确认一致）。新增字号仍按上一段规则落档。第 9 轮（2026-11-15）**仍未改 `:root` 的 9 档值，`CP.CHART_FS` 同样无需改**（core 组按契约第 6 节确认与 `:root` 一致）；本轮所有字号调整依旧是改引用档位（如 `.ai-basis` 由 `--fs-sm` 提到 `--fs-base`、与 `.scope-line` 同档），不是改变量值。

micro 与 xs 同值（12px）是历史决定的：micro 承载「负面率 7.9%」「截至 07-07」「责任人 陈老师」，投影三米外最先糊掉的恰好是这些。保留两个名字是为了不动 200 多处引用。

字体栈是系统中文无衬线，不引入展示字体——这是工具界面，不是作品集。数字用 `font-variant-numeric: tabular-nums`。

**等宽族**：`ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`（`app.css` 的 `.ev-ref` / `.tk-code` / `.step-tool` / `.step-idx` 等，与 `login.css` 的 `.login-dev code`）。它只用于**真实代码、编号与数据列**——证据引用号、工单编号、步骤与时长数字——让等宽数字对齐可比，**不是**给「技术感」当装饰的 costume（craft-floor 的 *Monospace as a costume for "technical"* 指的正是不承载数据的那种用法）。这些等宽声明与 `app.css` / `login.css` 实际一致，**本轮不改代码**，仅在此登记其合理性。

## Layout

`.shell` 是 flex 根（侧栏 244px + 主区）。内容用 `.grid` 与 `.g-N` 栅格，所有子项 `min-width:0`，宽表交给 `.table-scroll` 自己横滑，绝不撑破视口。断点：≤1100 侧栏压成 72px 图标条；≤720 侧栏变抽屉（关闭态必须 `inert`）、`.topbar` 取消 sticky、筛选栏收成「筛选 · 已选 N 个条件」摘要、KPI 排 2+2+1（末项跨两列）。

筛选栏是唯一允许高密度控件的地方，但同一决策点不超过 4 个可见选项，其余进「更多筛选」。

## Elevation & Depth

投影必须带偏移与柔化：`--shadow`（`0 2px 5px` + `0 20px 42px -26px`）、`--shadow-sm`。**零偏移彩色 halo 是装饰，不是深度**——浅色视图不使用；深色主题里现存的品牌 glow 属已登记豁免（见 Do's and Don'ts）。表面靠 `--surface` / `--surface-2` / `--surface-3` / `--surface-sunken` 四层明度分层，不靠堆叠阴影。

## Shapes

卡片 `--radius: 16px`，控件 `--radius-sm: 10px`。卡片默认 `padding: 20px`。`.card.is-quiet` 是「没异常时把整卡压成两行」的统一写法，不要另造一套。`.note` 是 flex 容器，**文字必须包 `<span>`**，否则裸文本会变成匿名 flex 项，窄屏下缩成一字一行。芯片组在 ≤720 要「组间折、组内不折」。

## Components

- **KPI 卡**：`.kpi`、`.kpi-value`、`.kpi-unit`、`.kpi-foot`。顶部 `.kpi-bar` 用中性品牌阶梯，不承载情感语义。悬停只改描边与投影，**不位移**（五张卡同时上跳是最大的「可点错觉」）。
- **徽章**：`.badge` 通用；`.tk-prib.p0–p3` 是工单优先级四档语义色（红/橙/蓝/灰），只读视图同样套用。
- **图表**：ECharts，统一 `CP.CHART_FS`。`CP.setOption` 必须**先于** `CP.drillTo`（反写不报错但点击永远没反应）。容器不可见时 setOption 只入队，由 `flushLazy` 唤醒。空图表按容器自身状态分三类（`core.js` 的 `setChartState` / `refreshChartState`）：`is-lazy-wait`＝还在折线以下，中性「向下滚动以加载图表」；`is-stalled`＝已在视口但仍在加载，中性「图表仍在加载…」；`is-failed`＝真失败，警告色「图表未加载 · 刷新页面重试」。**已取消 8s / 10s 一刀切给所有 `.chart:empty` 加 stalled**：那会把「还没滚到」演成假故障，答辩截图里最刺眼。状态变化才 `CP.announce()` 一次，别反复打断读屏。文案不许写不存在的「点此重试」（没有绑定点击，不造假控件）。
- **模态**：手写 `role="dialog" aria-modal="true"`，`openModal/closeModal` 统一处理 Esc、Tab trap、背景锁滚动、焦点回归触发按钮。
- **确认与危险动作**：统一 `CP.confirm`，默认焦点在取消；删除、重算、归档要写清对象与后果。后端不支持撤销时**如实说不可撤销**，不做假撤销入口。
- **AI 卡**：`page-insight` / `chart-insights` 有「生成中」占位与序号守卫 `piSeq`（连点只认最后一次）。降级成规则版的结果**不进缓存**，否则接口恢复后页面仍写「不可用」。AI 文本里的数字必须由统计接口给出并带可复算口径、AI 不新增数字；「✓ 引用 n/n 已核验」是 Agent 页独有的能力，本页四个 AI 接口没有引用校验器，**不得套用**（见下方「第 8 轮新增组件」的 `.ai-basis`）。
- **空态**：区分首次使用 / 无结果 / 筛选过窄 / 无权限 / 加载失败，并给就地「清除筛选」入口。
- **术语表（glossaryModal）**：业务口径集中一处，共 11 条。关键区分：**比例类**数字以当前筛选条件圈定的评论为分母；**加总类**（总点赞量 / 平均赞，仅统计点赞、不含评论、转发与收藏）是加总与均值，另见卡片脚注。「超期 / SLA」按工作时间口径（工作日、扣午休与周末）、已过截止时间；「DeepSeek 生成」只撰写结论与解读，情感与主题仍由「关键词规则标注」，AI 不改数字。
- **KPI 彩条**：`.kpi-bar` 是占比进度条，走中性品牌阶梯，**仅表示占比、不代表情感**；KPI 区常驻一条可见小图例解释彩条含义。
- **面包屑**：二级页统一用 `<nav class="crumb" aria-label="当前位置">`，父页与错误页不加。

### 第 8 轮新增组件（2026-11-15）

- **`.chain`（判断链骨架，第 10 轮已降级为 `.cp-chain-note`）**：~~把判断链单行铺开~~ **第 10 轮定性后不再是导航**。四次修复都没改变它的本质：组内 0 个可聚焦元素、`aria-current="step"` 挂在不可导航的 `<span>` 上、且排在 `.crumb` 之前（实测 y=164 vs 217），一条不可点的流程条压过了「你在这里」。现在它是一句普通段落 `<p class="cp-chain-note">`：`研判到处置：风险指数 → <strong>归口部门</strong>（当前） → 部门响应 48 / 24 工作小时 → 处置工单`，只出现在真正走这条链的两页（`departments.html` 的 content 首行、`tickets.html` 的面包屑之后）。**不声明 `navigation` 地标、不写 `aria-current`、不做三段态**（`.is-done` 与未开始步实测同色 `rgb(62,76,99)/400`，写了等于没写）。`base.html` 的 `{% block chain %}` 保留为空块，只为兼容仍写空覆盖的子模板。历史约束依然成立：**不加 `<a>` 链接**——各环节可能指向尚不存在或当前角色无权限的页面，任何一条 404 都算页面故障。
- **`.ai-basis`（AI 依据行）**：AI 卡底部独立成行的口径说明，两条 `<span>` 可 wrap。**由 `CP.aiBasis(d, kind, opts)` 统一生成**（见下方「第 9 轮」「第 10 轮」条目）：第一行前缀是**「当前范围：」**，里面先写**数据集**（`数据集 2019-2026`）再写**筛选**（`平台 抖音 · 未筛选`），两者分开——第 10 轮之前把数据集名混进「当前筛选范围：」，而 `2019-2026` 是数据集名、筛选栏里根本没有这个值。第二行是「引擎 · 生成于 HH:MM（北京时间）· 数字由统计接口给出，AI 不新增数字」。**绝不写「✓ 引用 n/n 已核验」或任何引用数字**：`dashboard/insight`、`chart-insights`、`page-insight`、`word-insight` 这四个 AI 接口**没有引用核验器**，只有 Agent 页 `agent.js:203` 有真实校验；在没有校验器的卡上写核验数字就是编造能力。所有插值过 `CP.esc`，页面 JS 写入 `.ai-basis-slot`；**生成中 / 失败 / 无数据时保留口径行**（它只依赖 `state.filters`，与 AI 无关），只把第二行换成「结论生成中，口径与分母不受生成状态影响」/「结论未生成，口径行仍然有效」，不累积。
- **`.scope-line`（口径句）**：承载单位 / 分母 / 口径 / 时区 / 控件名 / 操作约束的说明，13–14px（`--fs-sm` / `--fs-base`）独立成行、`line-height:1.65`。把现有 `#scopeNote`、`#tkSlaNote`、`#sentSub` 等口径句容器挂上这个类，保证常驻可见，不用 `data-hint` 或 `title` 独占。实测 390 下三处均为 14px。
- **≤720 的 44px 点击区与两处例外**：`.tk-bulk` / `.tk-open` / `.theme-btn` / `.chip` / `#cpFilterToggle` / `.sent-key` 在 ≤720 补到 ≥44px。**`.tk-open` 的移动端规则必须写成与全局同选择器的 `.tk-item-meta > .tk-open`**：全局那条 `min-height:24px` 特异性是 0,2,1，写着 `.tk-open`（0,1,0）的媒体查询永远输——第 9 轮就是这样把 390 的 44px 打回 24px 的（实测 62×24）。`.tk-open` 之前是 `.tk-item-meta` 里唯一没有 `white-space:nowrap` 的 `<a>`，被挤成 12px 宽、「打开详情 →」逐字竖排；现在 meta 行允许 wrap、链接 `flex:0 0 auto` 并折到自己的一行。**`.sent-seg` 是唯一登记的例外**：它的宽度承载情感构成比例，强行拉到 44px 会把数据画错，因此只补高度，等价的 44px 可点目标由下方 `.sent-legend` 的 `.sent-key` 按钮提供。
- **AI 生成中占位**：`.ai-progress` 用 `Date.now()` + 每秒刷新显示「已等待 N 秒 · 模型生成通常需要 10–30 秒」，配合既有 `.skeleton` 撑住卡片最小高度（`#aiInsight` 96px，`#pageInsight` / `#wordInsight` 120px，`#chartInsights` 96px）；`finally` 必须 `clearInterval`。**不加「取消」按钮**——没有 AbortController 支持，不造假控件。失败文案写人话，原始 `e.message` 只进 `console.warn`。

### 第 9 轮新增 / 变更组件（2026-11-15）

- **`CP.aiBasis(d, kind, opts)` 四类分母（P0-1，第 10 轮再修）**：AI 依据行由 `core.js` 的 `CP.aiBasis(d, kind, opts)` 统一生成，返回**已转义** HTML（`<div class="ai-basis">` 两条 `<span>`）。第一行是「当前范围：**数据集 X · 筛选**」＋取得到才写的有效评论条数＋分母口径——**数据集与筛选必须分列**（见第 10 轮条目）。`kind` 决定 `denomText`，四类各不相同：`ratio`＝「分母＝当前筛选范围内已标注情感的有效评论」、`sum`＝「本卡是计数或均值，不换算百分比」、`word-side`＝「正面词云分母＝正面评论，负面词云分母＝负面评论」、`mixed`（默认 / 未传 / 未知值）＝「比例类分母＝当前筛选内的有效评论；计数类不换算百分比」。**「分母见卡片脚注」这句已删**——四张 AI 卡里从来没有脚注，它把人指向不存在的东西。**不许**再出现第 8 轮一刀切的「分母＝当前筛选圈定的评论」。`validNote`（` · 有效评论 N 条`）只在能从接口返回字段（`d.total` / `d.valid_count` / `d.sample_size` / `d.count`）或 `state.datasetMeta` 真实取到计数时追加，**取不到就整段省略、绝不编造**；`engine_label` 缺省「统计规则生成」，`generated_at` 只取 `HH:MM` 并标**(北京时间)**（与 `#tkSlaNote` 的时区口径一致）；`d` 可 `undefined`，所有插值过 `CP.esc`，不外露 `dataset_id` / `filters_text`。`opts.state` 取 `'pending'` / `'failed'` 时保留第一行、第二行换成生成中 / 未生成说明。**为什么词云卡必须独立分母**：`insights_sentiment.html` 的两朵云各用各的分母——正面词只从正面评论里数、负面词只从负面评论里数（源码注释明写），混在一起按总词频取前 30 会把负面侧的词被中性评论挤下去；因此 `wiBasis` 必须传 `'word-side'`。`dashboard.js` 直接传 `'mixed'`；`insights.js` 的 `basisKind(slotId)` **按页面**定 kind：情感页 `ratio`（正文就是构成比例）、主题页 `sum`（量与排行）、时段页与总览页 `mixed`，词云恒 `word-side`——第 9 轮那张 `KIND` 表只有 `sum / mixed / word-side`，写好的 `ratio` 分支没有任何调用方，于是情感页那张纯比例卡写着「本卡是计数或均值」，自相矛盾。
- **`.chain` 三段态（第 10 轮已废止）**：~~`chain_step` / `chain_index` 驱动三段态、唯一 current~~。实测 `.is-done` 与未开始步同为 `rgb(62,76,99)/400`——写了三段态、只有两段视觉；`aria-current="step"` 挂在 `<span>` 上也不产生任何导航语义。第 10 轮组件整体降级为 `<p class="cp-chain-note">`（见第 8 轮条目与「第 10 轮」段），**不要再恢复 `.chain` / `chain_step` / `chain_index`**。侧栏按当前组折叠的规则保留：`parent_on` 时给 `.nav-children` 追加 `is-open`，并由父项上的 `.nav-caret` 按钮用 `aria-expanded` 报告同一状态（第 10 轮 A2，`core.js: bindNavGroups`）。
- **`.cp-flat` / `.sr-only`**：`.cp-flat`（`background:transparent; border:0; box-shadow:none; border-radius:0`）用 CSS 去掉内层卡片的「卡片感」，**不删元素、不改 DOM 层级**；`.sr-only` 是标准视觉隐藏但读屏可读（`position:absolute; width:1px; height:1px; clip-path:inset(50%); overflow:hidden; white-space:nowrap`），承载 `data-hint` 的纯旁白改由它实现。但**承载单位 / 分母 / 口径 / 时区 / 控件名 / 操作约束的说明必须常驻可见**（挂 `.scope-line`），不许被 `.sr-only` 或 `data-hint` 独占隐藏。
- **`.topbar-icon-text` / `#cpShowAllCharts`**：无文字图标按钮（`#dsRename` / `#themeToggle` / `#glossaryBtn` / `.tk-badge` 等）补 `aria-label` 与可见动作名 `.topbar-icon-text`，≥1100 可见、≤1100 隐藏，避免撑破 topbar、文案与真实控件逐字一致。`#cpShowAllCharts`（文案「显示全部图表」）只在存在 `pendingOptions` 待渲染图表时出现，**位置是「第一张待渲染图表所在卡片之前」**（第 10 轮：以前 prepend 到 `.content` 顶部，390 实测落在 y=238、比面包屑还早，离它影响的图表两千多像素；卡片在栅格 / 弹性行里时退一步插到图表本体之前，避免多出一个格子），点击调 `CP.flushAllCharts()` 后自行隐藏并 `announce('已显示全部图表')`，点击区 ≥44px；它统一承担**就地恢复入口**，不在 `.chart:empty` 伪元素上造假「点此重试」。
- **`LAZY_MARGIN` 桌面放宽（D1）**：`core.js` 的 `LAZY_MARGIN` 由固定 160 改为**按初始视口算一次的常量**（`window.innerWidth >= 1440 ? 1200 : (>= 1100 ? 800 : 160)`），桌面 ≥1100 让首两屏直接建图；三处使用（视口判断 `nearViewport` 与两处 IntersectionObserver `rootMargin`）共用同一常量，**不改成运行时函数**（rootMargin 创建时固定）。`flushAllCharts` 保持已在导出对象里、不重复导出；三态状态机 `is-lazy-wait` / `is-stalled` / `is-failed`、`chartFailed` WeakMap、`CP.markChartFailed`、状态变化只 `announce()` 一次均保留；`beforeprint` 仍 `flushAllCharts() + renderPrintScope()`。
- **≤720 不横滑 / 不渐隐（D3）**：`app.css` 曾有「横滑 + 右缘渐隐」与「flex-wrap、不要渐隐」两段互相矛盾的注释，第 9 轮统一成与实现一致的写法——判断链在 390 一行 / 折两行排完（`scrollWidth − clientWidth = 0`），**不横向滚动、不右缘渐隐**；右缘渐隐只保留给 `.table-scroll` 宽表确有溢出时。第 10 轮起判断链已是 `<p class="cp-chain-note">`，**顿号折行交给普通文字流**，不再有 `.chain` 的 flex 折行规则。
- **9 档字号本轮仍未改**：第 9 轮**仍未改 `:root` 的 9 档值，`CP.CHART_FS` 无需改**（见 Typography）。所有「提字号」都是改引用档位，不是改变量值。

## Do's and Don'ts

**Do**：口径句常驻可见；承载单位 / 分母 / 口径 / 控件名 / 操作约束的说明必须可见，只有纯旁白才用 `data-hint`；一页最多一条常驻口径行，次要说明收进 `<details>`；术语表区分比例类（分母＝当前筛选圈定的评论）与加总类（总点赞量，另见卡片脚注）；单位与数字 `nowrap`；表格首列 sticky、宽表 `.table-scroll` 横滑时给右缘渐隐提示（**仅宽表**；`.cp-chain-note` / 内容区在 ≤720 折行、不横滑不渐隐）；危险动作写后果；桌面与 390 两个视口都要看渲染结果；改 CSS/JS 后靠 `?v=mtime` 指纹自动失效缓存。

**Don't**：不要暴露 `CP_AUTH`、`dataset_id`、原始文件名、Cookie/F12 步骤给业务用户（技术细节只进管理员折叠区或 dev 块）；不要承诺「查看原帖」却只给脱敏文本；不要把「建议优先建单 0 单」和「中高风险 1 个」并排而不写口径；不要用 `!important` 压死 JS 控制的状态类；不要新增裸 px 字号；不要加载外部 CDN 作为 vendor 回退（数据不出校）；不要用 `R2/R3/R4` 这类内部编号指代规则，写人话规则名；不要把「单文件 ≤20MB、一次最多 4 个」等操作约束藏进 `data-hint`；不要写「复制登录态」「下方输入框」这类不存在的控件，文案须与真实控件逐字一致；`aria-label` 不硬编码平台名；不许把 Agent 页独有的「引用 n/n 已核验」套到没有引用校验器的 AI 卡上（四个 AI 接口无引用校验器，写了就是伪造，见 `.ai-basis`）。

### Explicit exemptions（用户显式 pin，2026-11-15）

以下项目在 `/impeccable critique` 第 5 轮被确定性检测器标为风格问题，**项目所有者明确决定保留**，后续评审不再计入待修缺陷。

> **登记方式（第 9 轮：改为纯选择器登记）**：位置一律以**选择器 / 元素为准**（决定性定位，换工具、换轮次都查得到）。第 8 轮仍保留「辅助行号」，本轮并行编辑后这些行号**整体漂移 +2…+29**，再次证明行号不具权威性——因此本轮**删去表格里全部辅助行号，改为纯选择器 + 相对描述登记**；复核请用 Grep 搜选择器当前所在行（本轮已逐项核对，见下表）。此前版本只靠散文行号登记，导致 `app.css:974-977` 实为 `994-999`、`login.css:57-60` 实为 `52-60`、`login.css:141` 已变、深色 glow 的 `2196/2213/2247/2260` 整段漂移；第 8 轮逐项用 Grep 核对后改为「选择器为主、行号为辅」，第 9 轮进一步删去全部行号、改为纯选择器登记。本轮起，豁免同时在源码元素前以 `impeccable-disable` 内联注释落地（模板组 / 样式组按契约第 4 / 5 节负责），本表与其互为索引，不再只靠散文登记，避免换工具 / 轮次后重新报。**适用边界（如实记录）**：内联注释对**文件扫描**生效（本轮文件扫描的 pinned 项已归零）；对 **URL / 浏览器渲染扫描**不生效——检测器只读渲染后的 DOM，找不到源码注释，且 `config.json` 里按源码路径限定的 `ignoreValues` 对 route URL 不匹配（见 `live-browser-ignores.js` 的 known gap）。所以 `dark-glow` / `kicker-above-heading` / `codex-grid-background` / `side-tab(.evidence-item)` / `pulsing-dot` 在 URL 扫描里仍会报，属**已知且已豁免**，不要为清空计数去删品牌装饰，也不要用全局 `ignoreRules` 把真阳性一起吞掉。**第 9 轮起 `radial-spotlight-glow` 已从本列表移除**：`login.css` 两处 radial-gradient 按用户裁定修掉、`login.html:31` 与 `login.css:27` 的豁免注释一并删除，URL 扫描不再命中该项（详见下方「不在豁免表内」）。**配置侧 known gap（E6）**：内联豁免只对**文件扫描**生效、**URL / 浏览器渲染扫描仍报**这一 gap 靠本表如实登记，不靠全局忽略清计数——因此 `.impeccable/config.json` 第 9 轮**保持最小合法配置**（`detector.designSystem.enabled` 仍为 `true`，**不加**全局 `ignoreRules`），用全局忽略清计数会把真阳性一起吞掉。

| 项 | 位置（纯选择器登记，无辅助行号） | 判定 |
|---|---|---|
| 登录页两处全大写英文 kicker | 原 `login.html` 的 `.login-eyebrow` / `.login-kicker` | **已移除**（第 20 轮，2026-11-16：用户要求登录页简化，左右分栏与英文 kicker 一并删除） |
| 登录页 42px 双轴网格背景 | 原 `login.css` 的 `.login-brand::after` | **已移除**（第 20 轮随 `.login-brand` 删除） |
| 登录页品牌光圈 | 原 `login.css` 的 `.login-brand::before` | **已移除**（第 20 轮随 `.login-brand` 删除；`app.css` 的 `.brand-mark` 辉光仍保留） |
| 品牌彩色辉光 | `app.css` 的 `.brand-mark` `box-shadow` 及其 logo 变体（`.brand-mark svg`） | **仅 `app.css` `.brand-mark` `box-shadow`，不含 `login.css` 的 radial-gradient**（后者第 9 轮已按未 pin 修掉，见下）；保留 |
| 深色主题 glow | `app.css` 两个 `[data-theme="dark"]` 覆盖块中的 `box-shadow` glow（`[data-theme="dark"] .brand-mark` / `.modal` / `.toast` / `.btn-primary` 等；结构见下） | 保留；浅色视图不出现 |
| 证据行 3px 左侧情感色条 | `app.css` 的 `.evidence-item::before` 与其 `[data-sent]` 正负变体 | 保留，它是情感编码不是装饰 |
| 采集态脉冲点 | `app.css` 的 `.live-dot`、`.badge-run::before`、`@keyframes cp-pulse` | 保留为用户 pin 的采集态品牌样式；实例说明见下 |

**第 20 轮登录页简化（2026-11-16）**：用户要求登录页「简单点」。原左右分栏（深海军蓝品牌区 + 右侧表单）改为**单列居中卡片**：卡片内只有 logo / 「欢迎回来」/ 一句说明 / 账号密码 / 记住我 / 登录按钮 / 合规页脚；`login.css` 与 `login.html` 的 `.login-wrap`、`.login-brand`、`.login-hero`、`.login-features`、`.login-flow`、`.login-eyebrow`、`.login-kicker`、`.login-secure`、`.login-edition` 全部删除，上表三个登录页豁免项随之失效（元素已不存在，不是改判）。品牌色、字号九档、主题变量与 `app.css` 的 `.brand-mark` 辉光未动；表单结构与 `auth.js` 的选择器契约（`#loginForm` / `#username` / `#password` / `#remember` / `#loginBtn` / `#loginErr` / `#nextPath`）保持不变。登录页容器类名用 `.login-shell`，避免与采集页 `.login-card` 撞名。

**深色 glow 的结构（第 9 轮 Grep 核对）**：`app.css` 实有 **2 个** `[data-theme="dark"]` 根覆盖块（一处在基础层、一处在媒体合并层），深色 glow 的 `box-shadow` 分散在两块里；其中 `[data-theme="dark"] .brand-mark` 的 glow 声明**共 2 处**（每块各 1 处），另有 `.modal` / `.modal-mask` / `.toast` / `.btn-primary` 等。第 8 轮只登记了 `:1164-1168`、`:2573` 两个片段行号，本轮并行编辑后又整体漂移，故改为纯选择器登记、不再写行号；复核请 Grep `[data-theme="dark"]` 与 `[data-theme="dark"] .brand-mark` 当前所在行。

**采集态脉冲点 · 如实记录**：静态模板源码里确实没有 `.badge-run` / `.live-dot` 字面量，但**这不等于运行时实例为 0**——`.badge-run::before` 的 `cp-pulse` 呼吸点由运行时 JS 注入：采集页「采集中」徽标（`data_collect.js`）、Agent 页「运行中」徽标（`agent.js`），以及 `/tickets` 工单状态为「处理中」时（`tickets.js` 的 `STATUS_TONE.processing = 'run'` 拼出 `badge-run`）都会渲染出真实的 `.badge-run::before` 实例。原表「仅采集运行时由 JS 注入，静态页实例为 0」把「模板无字面量」误当成「DOM 无实例」，与 `/tickets` 实测不符，本轮更正。本项豁免的依据是**用户显式逐项 pin**，不是「实例为 0」；检测器按 CSS 规则命中，与 DOM 实例数量无关。

**不在豁免表内（未被 pin）**：`.wi-side.pos/.neg` 与 `#aiInsight` 的 3px 彩色 `border-left` **不是**上表被 pin 的 `.evidence-item::before`，不得借本表规避；由样式组在 audit / polish 中修掉（改 ≤1px，或改用 `.wi-dot` 圆点 + 标题色承担情感编码，不删正负区分），本表不收录。

**`login.css` 的 radial glow：已按用户裁定修掉，未重新 pin（第 9 轮，2026-11-15）**：第 8 轮它一度与「适用边界」的 waived 列表自相矛盾——一处写「未被 pin、须修」，一处又把它列为已豁免。用户本轮裁定「**按未 pin 修掉**」（不是重新 pin、不是保留矛盾）。样式组已删除 `login.css` 的两处 `radial-gradient`（`.login-body` / `.login-brand` 背景改纯 `--bg` / 纯深海军蓝底，可用 ≤1px 细线补层次但不再加 radial-gradient）与 `login.css:27` 的豁免注释，模板组删掉 `login.html:31` 的对应注释；`app.css` 的 `.brand-mark` 彩色辉光与 `[data-theme="dark"]` glow 属上表已 pin 项，**保留不动**。至此 radial glow 只剩一种说法：它从未被 pin、已修掉、未重新登记豁免、也不再出现在 waived 列表；`login.css` 的 dark-glow 注释与 `.login-brand::before`、42px 网格注释仍保留。

**已知冲突，如实记录（第 20 轮更新）**：Impeccable 的 craft-floor 把「标题上方的 kicker / eyebrow」列为 *ban*（"no brief earns it back"），也把「>1px 的彩色 border-left」与「无信息量的 01/02/03 章节编号」列为 refuse。登录页的两处 kicker 已在第 20 轮随简化删除，该冲突不再存在；上表只剩 3px 侧色条一项仍与 craft-floor 冲突，继续以用户显式 pin 为准。**不声称它们符合 craft-floor**。若未来要合规，需用户对每一条重新授权；不得在常规 polish / quieter 中顺手删除。

2026-11-15 第 7 轮同步
2026-11-15 第 8 轮同步（文档 / 配置 / 测试组）：逐项用 Grep 核对并更正 Explicit exemptions 的漂移行号与「静态页实例为 0」失真表述，改为「选择器为主、行号为辅」；补记 `.chain` / `.ai-basis` / `.scope-line` 三个新组件，以及「引用核验数字不得套用」「本轮未改 9 档档位值、`CHART_FS` 无需改」；新建 `.impeccable/config.json` 最小配置。
2026-11-15 第 9 轮同步（文档 / 配置 / 种子 / 测试组）：用户裁定 `login.css` 的 radial glow 按「未 pin」修掉，文档统一为单一说法、从 waived 列表移除；Explicit exemptions 改**纯选择器登记**（删去全部辅助行号）并补记 dark-glow 实有 2 个 `[data-theme="dark"]` 块 + 2 处 `.brand-mark`；补记等宽族、`CP.aiBasis(d, kind)` 四类分母与词云独立分母、`.chain` 三段态唯一 current、`.cp-flat` / `.sr-only` / `.topbar-icon-text` / `#cpShowAllCharts`、`LAZY_MARGIN` 桌面放宽、≤720 不横滑渐隐、9 档字号本轮未改；`config.json` 保持最小合法配置（不动 `enabled`、不加全局 `ignoreRules`）；`seed_demo_tickets.py` 文案 R2→人话规则名（**未重新导入**）；`verify_ui.py` 增 E7 断言。

**第 9 轮父代理集成补漏（如实记录，2026-11-15）**：
- `CP.aiBasis()` 的「有效评论 N 条」区分来源：接口返回的计数对应当前筛选，写作「有效评论 N 条」；回退到 `datasetMeta.valid_count` 的是**数据集全量**，只在无筛选时写「数据集有效评论 N 条」，带筛选时整段省略。否则「当前筛选范围：平台 抖音 … · 有效评论 1,969 条」会把全量数冒充筛选后数，与 P0-1 修掉的一刀切分母是同一类口径错误。
- A6 的 4 个可见动作名初次落地把 1280 视口撑出 **11px 横向溢出**（`.topbar-icon-text` 右缘到 1291）。改为 2–3 字短动作名（待办 / 重命名 / 主题 / 术语）+ `.topbar-right { min-width:0 }` 双保险；动作名仍常驻可见，完整描述保留在 `aria-label` / `title`。
- **`icon-tile-stack` 4 条 @390 属检测器误判，登记不改**：URL 扫描报 `h1.page-title` 前有 44×44 图标块，实测该元素是全站唯一的 `#navToggle`（44×44 功能性导航开关，DOM 顺序在 h1 之前），不是装饰性 icon tile；`.brand-mark` 40×40 位于 off-canvas 侧栏 `x=-256` 不可见。改布局会伤移动端导航功能，按契约「如实记录、不硬拆」处理。内联豁免对 URL 扫描不生效（已知 gap），不用全局 `ignoreRules` 清计数。
- E5 `nested-cards` 余下的精确选择器（`#aiInsight` / `#sentNote` / `#suggNote` / `.tk-item.on` / `.evidence-item` / `.tk-block .note`）由父代理按 URL 扫描结果补一段 CSS，只抹圆角与阴影、**保留背景与选中态**，不删元素、不改 DOM 层级；若检测器仍因结构命中，按契约如实记录。
- `cp.d.ts` 补齐 `CHART_FS` / `announce` / `labelChart` / `scopeText` / `aiBasis(d, kind)` / `flushAllCharts` / `markChartFailed` 的声明（`aiBasis` 新增第二参数）；桌面 `.tk-open` 补 `min-height:24px`，使非触屏视口也不低于 WCAG 2.5.8 的 24px 下限。
- **E5 收尾与最终计数**：`nested-cards` 从两视口合计 16 条降到 2 条（1280 8→1、390 8→1），`flat-type-hierarchy` 4→0、`side-tab` 开放项 5→0、`radial-spotlight-glow` 1→0。最后剩下的 `#aiInsight` 一条，即使把内层圆角 / 阴影 / 淡蓝实底 / padding 全部清掉仍被命中，判定来自 DOM 结构（`.card` 内的独立 `#aiInsight` div）而非视觉；**「视觉上已无套娃」这句第 10 轮实测证伪**——当时那条覆盖写的是 `.card .ai-summary`（0,2,0），而 `#aiInsight` 是 ID 选择器（1,0,0），特异性根本轮不到它赢，计算样式一直是 `rgb(238,242,255) / radius 11px / padding 15px 17px`。第 10 轮把 `#aiInsight.ai-summary` 写进选择器后才真正扁平化（见下）。最终 URL 扫描 1280 **14 条**（13 条已 pin 装饰 + 1 条 nested-cards）、390 **9 条**（4 条 waived + 4 条 `icon-tile-stack` 功能按钮误判 + 1 条 nested-cards）。文件扫描 17 条与第 8 / 9 轮基线逐字节相同，全为 Jinja `static_url()` 解析不出 CSS 的假阳性；CSS 扫描 187 条中 180 条是 `design-system-radius/color` 令牌一致性提示、7 条是已登记的等宽族。
- **验收**：`tools/verify_ui.py` 18/18（含本轮新增 4 条桌面回归断言）；14 页 × 双视口 0 console error、0 横向溢出；Jinja 16/16、全部 `node --check` 通过、CSS 花括号 1122/1122 与 80/80、0 裸 px 字号、`:root` 9 档值未动、`CHART_FS` 未改；桌面 1280 首屏两张主图 3.2s 内均有 canvas（0 stalled / 0 failed），长图导出不再是空占位；可靠对比度 nav-code 11.74 / `.page-sub` 8.68 / `.scope-line` 7.76 / `.ai-basis` 8.68，`low-contrast` URL 扫描 0 条。演示库 `data/campuspulse.db` 未被写入。

## 第 10 轮同步（2026-11-15，用户裁定「一次全跑」）

本轮起点是第 10 轮 critique 的 2 个 P0 + 5 个 P1 + 14 项清单。用户先裁定「先修两个 P0」，随后追加「`.chain` 退回面包屑」，范围定为 7 条优先问题 + 14 项清单 + 补 `verify_ui.py` 计算样式断言。

**两个 P0（界面在说假话）**
- **`/departments` 首屏 5.5 秒的「假无数据 + 假安全」**：`renderShell()` 在 `view.risk` 未到时写「当前数据集没有可用日期」，`renderKpi()` 用空数组算出 `0 个` + 绿色 tone。现在分三态：加载中 → 副标题「正在读取研判锚点…」+ `aria-busy` + KPI 骨架条；失败 → 「研判未完成，可点「重新研判」重试」+ 值写 `—`、tone 为空（不再有绿点）；成功才给数字与 tone。「当前数据集没有可用日期」只在**请求成功且确实没有 anchor** 时出现。`loadRisk()` 一进请求就刷一次 shell / KPI，`finally` 里再收口一次。
- **移动端「打开详情 →」什么都不会发生**：`≤720` 是单列，详情卡在列表之后（实测 390 点击后 `scrollY` 仍 1136、`#tkDetail.top` 1305、视口 844）。`openTicket()` 成功后调 `revealDetailOnNarrow()`：给 `.card-title` 加 `tabindex="-1"` 并聚焦，再按顶栏实际高度设 `scrollMarginTop` 后 `scrollIntoView({block:'start'})`。桌面主从同屏，**不做**这个跳转。

**五个 P1**
- **`.tk-open` 移动端 44px**：媒体查询里改成与全局同选择器 `.tk-item-meta > .tk-open`（见第 8 轮条目）。`verify_ui.py` 补 390 计算高度断言。
- **`#aiInsight` 扁平化**：覆盖选择器加 `#aiInsight.ai-summary`，去实底 / 圆角 / padding，保留 `border-left: 2px solid var(--brand-line)` 作为「这段由模型生成」的唯一视觉标记。
- **可复算口径**：`scopeText()` 只管筛选，`datasetLabel()` / `scopeLine()` 另说数据集；`.ai-basis` 前缀改「当前范围：数据集 X · 未筛选」；`生成于 HH:MM（北京时间）`；删掉指向不存在脚注的「分母见卡片脚注」；`basisKind()` 按页面定 kind（情感 `ratio` / 主题 `sum` / 时段与总览 `mixed` / 词云 `word-side`）；工单列表常驻「跨数据集汇总，不受顶栏数据集切换影响」，详情在 `dataset_id` 与当前不一致时插一条可见警示。
- **`.chain` 退回面包屑**：见第 8 轮条目，`.chain` 全部样式与三段态删除，改为 `.cp-chain-note` 段落，只留 `departments.html` / `tickets.html` 两页。
- **`/data`、`/data/reports` 390 表格逐字竖排**：`.table` 的移动端 `min-width` 由 520 提到 760，首列补 `min-width:180px`，让横向滚动接管纵向挤压（WCAG 1.4.10 Reflow）。

**14 项清单落地**
`A1` `.g-4` 与 `.g-3k` 一起豁免 1360 断点（4 张 KPI 不再两行把列表推出首屏）；`A2` 父项加 `.nav-caret` 按钮 + `aria-expanded` / `aria-controls`（`core.js: bindNavGroups`）；`A3` `#cpShowAllCharts` 挪到第一张待渲染图表卡之前、点击 `announce`；`A4` 390 顶栏改两行网格（标题行 / 控件行左对齐），`.ds-hint` 仍贴底；`A5` `.entry-desc` 放到 3 行 + `text-wrap: pretty`；`B1` 顶栏加可见「数据集」标签；`B2` 北京时间；`B3` 生成中 / 失败保留口径行（`aiBasis` 第三参数 `state`）；`B4` 重新生成保留旧结论（`.is-refreshing` 压到 0.55 + 卡头「更新中」）；`C1` `.composer textarea::placeholder` 用 `--ink-4`（4.99:1）；`C2` 390 `.crumb a` 与 `summary` 撑到 44px；`C3` 即 P1-4；`C4` `chart-insights` 四条子卡改用 `.pi-reason.ci-item`（去卡片四件套，只留 1px 分隔线）；`D1` `LAZY_MARGIN` 三级（≥1440 → 1200 / ≥1100 → 800 / 否则 160）；`D2` 全部 `impeccable-disable` 收窄为 `-next-line`；`D3` `verify_ui.py` 新增场景 D 四条计算样式断言；`D4` 本节与上面三处修正。

**本轮实测计数（对比第 9 轮基线）**：文件扫描仍然 **17 条**（1 anti-pattern + 16 advisory，与基线逐字节相同，全是 Jinja `static_url()` 解析不出 CSS 的假阳性）；CSS 扫描 **187 条**（7 等宽族 warning + 180 条 `design-system-radius/color` 提示，与基线相同——新增的 `.nav-caret` 一度用 8px 圆角多出 1 条，改回 `var(--radius-sm)` 后归零）；URL 扫描 **1280：14 → 12 条**（dark-glow 10 + nested-cards 1 + pulsing-dot 1）、**390：9 → 1 条**（只剩 pulsing-dot）。390 那 4 条 `icon-tile-stack` 误判随顶栏改两行网格自然消失：汉堡与 `h1` 现在同一行并排，不再是"图标块压在标题上方"的几何形态——它们是布局真的修好了，不是把计数藏起来。`#aiInsight` 的 `nested-cards` 仍在（判定来自 DOM 结构，不是视觉），按契约如实留档。

**豁免注释粒度（D2）**：`app.css` 8 条 `dark-glow` + 2 条 `pulsing-dot`，全部由文件级改为 `impeccable-disable-next-line`，逐条紧贴被豁免的规则；原 `login.css` 1 条 `dark-glow` + 1 条 `codex-grid-background` 与 `login.html` 3 条已随第 20 轮登录页简化删除（登录页当前无豁免注释）；今后新增品牌装饰必须用 `-next-line`，不得再写文件级。

## 第 11 轮同步（2026-11-15，用户裁定「一次跑完 1–5」）

起点是第 11 轮 critique 的 4 条 P1 + 1 条 P2。用户裁定：先清「界面在说假话」，承认「设计特异性目前靠文案自证」，本轮范围＝P1 四条 + P2 一条；视觉重构另立一轮（走 `/impeccable shape`），不混在缺陷修复里做。

**清「界面在说假话」**
- **建单备注弹窗不能再写 `{# … #}`**：`departments.js` 的 `askNote()` 用 JS 模板字面量拼 HTML，JS 不认识 Jinja 注释，于是那段「建单是有外部后果的动作…」原样显示在「确认建单」上方——用户把判断交出去的一刻看到源码。已删除，改成模板外的 `/* */`；`verify_ui.py` 新增断言「弹窗文本不得含 `{#` / `#}`」。
- **第三张 KPI 的标题只有一个值**：骨架分支只判 `scoped()`、真值分支还判 `!ticketsOn()`，实测 0.4s 写「建议优先建单」、9s 变「建议优先处置」。现在两处都走 `todoLabel()`；`view.meta` 未到（能力未知）时只给共同前缀「建议优先」，等确定再补全，不先摆一个当前做不到的动作名。
- **术语表不再指不存在的东西**：`base.html` 的「口径」条目删掉「另见卡片脚注」（四张 AI 卡从来没有脚注），改「加总类指标是加总与均值，不换算百分比」。

**三把 SLA 尺子分开说（clarify）**
- 判断链（`departments.html` / `tickets.html` 的 `.cp-chain-note`）由「48h / 24h 响应时限」改为 **「部门响应 48 / 24 工作小时」**：`sla_hours` 的单位是**工作小时**（每天 7 个，48 工作小时≈7 个工作日），写成裸 `h` 会被读成自然小时，与本项目唯一的 SLA 难点直接冲突。
- 建单确认由「48 工作小时内首次响应」改为 **「部门时限 48 工作小时 · 建单后在「处置工单」查看进度（首次响应按工单优先级计时）」**。原来这句话与建完单详情里的「首次响应要求 4 个工作小时」（P1＝4 工作小时）在同一个流程里互相矛盾。
- `#tkSlaNote` 增一句「三把尺子不要混着看」，并在 `tickets.js` 里**从 `view.meta.sla` 读出真实阶梯**追加「首次响应 / 归档时限按优先级：紧急 1 / 4、高 4 / 7、中 7 / 21、低 21 / 35 工作小时」——数值不写死在模板里，改配置后不会变假话。术语表的「超期 / SLA」同步拆成三把尺子。

**风险四段可复算（audit）**
- `.dp-risk-track` 由 `aria-hidden="true"` 改为 `role="img"` + `aria-label`，label 里念完四段各自的分值与口径（例：`四段构成：量级 34.9 分，负面 62 / 60 条封顶；负面率 6.8 分…；合计 57 分`）。子 `<i>` 不再逐个朗读，但四段信息完整进入无障碍树；视觉与 DOM 结构不变、不新增可聚焦元素（行本身 `tabindex=0`，回车展开的「风险指数拆解」在详情面板里）。
- `.dp-risk-num` 的 `title` 补「· 回车展开四段构成」；`.dp-lg-note` 补「点任意一行（或按回车）展开四项分值与权重」——把「有明细」这件事从"存在但没人知道"变成可见承诺。

**删除工单降权（polish）**
- 动作行由「下一张 / 返回列表 / 删除工单 / 关闭」改为「下一张 / 返回列表 / 关闭 ‖ 删除工单」：删除排到末尾、前面一条 `.tk-danger-sep` 竖线，并套 `.btn-danger`（红字 + 弱红边）。**颜色不是唯一线索**（位置与分组也变了），色盲用户同样分得开。`.card-tools` 仍是单行，390 实测 72×44。

**390 布局（adapt）**
- **工单卡主列 149 → 231px**：`.tk-item` 在 ≤720 `flex-wrap: wrap`，`.tk-item-sla` 折成整行（`flex: 1 1 100%`、左对齐、`b/span/i` 改 inline 一行读完），主列还回 231px。**`.tk-item-main` 必须 `flex: 1 1 0`**：`auto` 会拿标题的 max-content 当基准，在 wrap 容器里直接把自己挤到下一行——实测过，那样第一行只剩复选框、卡片从 74 涨到 207。
- **两处差零头的触控目标补到 44**：`summary` 43.1 → `min-height: 44px`（不再靠调 padding 追小数，line-height 一改又不对）；`.nav-child` 217×37.2 → `min-height: 44px`（全站唯一还没到 44 的可点链接）。
- **横滑表首列全站钉住**：原来只有 `.dp-risk-table` 做，`/data` 与 `/data/reports` 一横滑就丢行身份（实测 `scrollLeft=200` 时首列已滑到 x=-171）。改为 `.table-scroll .table:not(.dp-risk-table) th/td:first-child { position: sticky; left: 0 }`，自带不透明底与 1px 分隔线，并补 hover 变色；风险表在 ≤720 会改成卡片，显式排除避免两套规则打架。实测横滑 200px 后首列位移 0。

**本轮实测**：`verify_ui.py` **27/27**（新增场景 E 五条：弹窗无 Jinja / 四段 aria-label 完整 / 三把尺子有话说 / 删除已降权 / 390 主列与两处热区）；`tsc --noEmit` 0 条；`tests/acceptance.py` 78/78；12 路由 × 2 视口 0 console error、0 横向溢出。检测器：文件扫描 **17**（不变）、CSS **187**（7 anti-pattern + 180 advisory，与基线一致）、URL **1280 15 / 390 2**（与第 11 轮基线逐条相同，无新增——`nested-cards` 仍落在 `.note` 与 `/data` 的提示条上，判定假阳性：`.note` 无 border、无 box-shadow）。

## 第 12 轮同步（2026-11-15，视觉重构：判断链的时限有位置）

起点是第 11 轮留下的结论——**设计特异性靠文案自证**：归口、48/24 工作小时、四段构成只活在小字与弹窗里，删掉文字任意 SaaS 都能用。本轮走 `/impeccable shape` 先出方案再动手：新建 `PRODUCT.md`（此前只有本文件），裁定「演示与真实值班并重，按真实动作组织」；范围「全站六页统一 + 允许改行结构」。

### 选定方向：周带（seed:a578b40d12f4c492，7 个结构候选中的第 5 位，掷骰指定）

七个人物世界里的结构候选：督办单 / 台账 / 值班交接 / 会签栏 / **周带** / 挂图作战 / 叫号条；掷到第 5、4、2 位，用户锁周带。三张第一屏草图画在 `.impeccable/mocks/decision/{week-band,signoff,register}.html`，截图 `screenshots/r13/shape_*.png`。

**核心**：时限不是一个句子，而是事项落在一周里的哪一格。判断链四环全部有形状——风险＝分数＋四段条，归口＝部门名，**时限＝落点**，工单＝落点上留下的单号。

**三种点，三种来历，绝不混画**（这是本轮唯一不能妥协的地方）：

| 状态 | 画法 | 位置来源 |
|---|---|---|
| 已建单 | **实心**（品牌蓝点） | 工单真实的 `sla_due` |
| 已归口未建单 | **虚线框** | `tickets.add_work_hours(now, dept.sla_hours)` 的推算值 |
| 未归口 | **不进日期格**，落带尾「待归口」 | 没有时刻——时钟从归口开始 |

给未归口主题画一个 48 小时的日子，等于替学校许了一个没人认领的承诺。同理，「现在建单 + 部门时限」的落点必须画成虚线：它是推算，不是承诺。

### 新增组件 `.wk`（周带）

- **结构**：两行 × 五列（本周 / 下周 / 再下一周，按周差算标题，跨月也不会写成「本周」），列头 `MM-DD 周X`；今天那一列有品牌色上边线与浅底。上方是「已超期」横条（工单已过截止时刻），下方是「两周之后」与「待归口」横条。**横条不是垃圾桶**：落不进两周窗口的主题必须被承接，不能静默丢弃。
- **数据全部来自后端**：`/api/departments/risk` 的 `topics[].due`（`YYYY-MM-DDTHH:MM`）、`due_basis`（`ticket` / `projected`）、`sla_hours`，已建单的另有 `ticket_due`。前端**不自己加工作小时**——后端有工作日历（`config.WORK_HOURS`＋`config.HOLIDAYS`），前端只有周末概念。`_attach_ticket_codes` 的 SQL 因此扩了 `sla_due, priority` 两列，按 dimension 回填 `ticket_due`。
- **落点可复算**：48 工作小时＝7 个工作日，从「现在」起算而不是从研判锚点起算——锚点只说证据有多旧，时钟从现在走。周带脚注写明这一点。
- **交互**：pin 是 `<button>`，点一下等于点表格里那一行（`selectRow`），明细仍在表格行内展开；`aria-pressed` 同步。键盘用浏览器原生回车/空格，不另写分支。
- **≤720 降级**：五列格子读不了（一格比主题名还窄），折成按周分组的纵列，分组标题与桌面两行同源（同一套「下周 / 再下一周」，避免同一个日期两种说法），条目 ≥44px。两套结构同时存在于 DOM，靠 `display` 二选一，`display:none` 那套对读屏同样不可见。
- **隐藏条件**：`view.win !== 'live'` 或研判未回或没有主题时整块 `hidden`。自选时段讲的是过去某一段，把它钉到未来两周等于给历史加一个假 deadline。

### 全站装饰减法（六页统一）

- **侧栏 01–06 编号**：`base.html` 两处 `<em class="nav-code">{{ p.code[-2:] }}</em>` 删除，`app.css` 的基础规则、深色两档色、`041` 整段媒体查询、三处 `display` 列表里的 `.nav-code` 一并清掉。六页不是六个步骤，编号暗示了不存在的顺序，还占掉标题那列宽度。**同一条理由在第 16 轮执行到登录页**：三条特性前的 `01/02/03` 方块编号一起删掉（见「第 16 轮同步」）。
- **洞察入口 3.1 / 3.2 / 3.3**：`insights.html` 三处 `.entry-no` 与两段样式删除。同时消掉了第 9 轮那 4 条 `icon-tile-stack`（34×34 图标块）误判的来源——那个块就是 `.entry-no`。
- **浅色视图的淡蓝光斑**：`body` 与 `.main` 上**重复的两条** `radial-gradient(circle at 78% -12%, rgba(59,99,243,.045), ...)` 与 `.sidebar` 的 `rgba(89,119,255,.16)` 蓝色 bloom 删除。本文件「Elevation & Depth」早就写着「零偏移彩色 halo 是装饰，不是深度」，这三处正是它。已 pin 的 `.brand-mark` 辉光与 `[data-theme="dark"]` 的 `box-shadow` glow **未动**。

### 工单页的同一套语言

`.tk-item-sla` 增加第三行 `MM-DD 截止`（`.tk-due`，micro 档、等宽数字），把原本只活在 `title` 里、悬浮才看得到的截止日提到可见文本。**本轮没做**详情面板「三把尺子」的并排刻度化——那是下一轮的事，不要在本文件里写成已完成。

### 本轮实测

- `tools/verify_ui.py` **30/30**（新增场景 F 三条：① 周带落点＝后端 due 且未归口不进日期格；② 390 折成纵列且条目 ≥44px；③ 三类装饰已清除——`nav-code` / `entry-no` / `body|.main|.sidebar` 的 `radial-gradient`）。
- `tests/acceptance.py` **78/78**；`tsc -p jsconfig.json --noEmit` **0 条**。
- 检测器（CSS + JS + 三个模板）：**161 条 → 154 条**，全部是 `design-system-radius/color/font` 提示；**本轮新增段 0 条**（首版有 3 条：`3px` 圆角、`8px` 圆角、`#F0C7C2`；改成 `50%` 圆点、`var(--radius-sm)`、`rgba(217,45,32,.3)` 后归零）。
- 1280 / 390 双视口：`/departments`、`/tickets`、`/`、`/insights`、`/data` 0 console error、0 横向溢出。周带在 1280 高 460px、390 高 772px（8 条纵列）。
- `:root` 九档字号**未改**，`CP.CHART_FS` 无需同步。
- 演示库 `data/campuspulse.db` 未写入；验证用 `data/cp_r13.db` 副本，跑完即删。

### 已知未做 / 下一轮

1. `/tickets` 详情面板的三把尺子仍是三句话（判断链、`#tkSlaNote`、工单详情各一把），并排刻度化未做。
2. `.tk-bulk` 原生复选框仍被 `padding:11px` 撑成 44px 空方框（第 11 轮遗留，只改了对其方式）。
3. 周带窗口固定两周（10 个工作日），未做「之后」的展开。

## 第 13 轮同步（2026-11-15/16，收掉第 12 轮留下的三件）

第 12 轮结束时留了三条未做，这轮全收。三条都不是"再加点东西"，而是把已经在说谎或藏着话的地方改掉。

### 一、详情面板的三把尺子：两条刻度 + 一条参照

以前 `.tk-sla-box` 是一句话，三件事挤在一段里：部门期望（48/24 工作小时）、本单的首次响应、本单的归档时限。要读的人得自己拆句子才知道"还剩多少"，而看这页的人是来救火的。

现在是 `.tk-clocks` 三格：

| 格 | 内容 | 数据来源 |
|---|---|---|
| 首次响应 | 已用 / 时限 工作小时 + 填充条 | `clocks.first`（`work_minutes_between(created_at, first_response_at‖now)`） |
| 归档时限 | 同上 | `clocks.archive` |
| 部门期望响应 | **竖线分栏的第三格 + `1px dashed` outline 参照框**（无填充条、方角、透明底） | `clocks.department`（归口部门的 `sla_hours`） |

> **第 16 轮改写**：三格原来各自套一圈「底色＋边框＋圆角」，在 `.card` 里构成 `nested-cards`（检测器真阳性），且三格等权重的边框跟父卡抢视觉。现在改成**同一条尺子的三段**：`gap: 0` + `margin: 0 -14px` + 每格 `padding: 4px 14px` + `.tk-clock + .tk-clock { border-left: 1px solid var(--line) }`。参照格的虚线框改用 **`outline` 而非 `border`**（不占布局、也不构成「又一张卡片」），`outline-offset: -1px`、`margin: 0 4px` / `padding: 4px 10px` 让文字与另两栏对齐（10+4=14）。`.tk-clock.is-over` 也一并去掉 border / radius，只留铺满整栏的 `--neg-soft`，数字与轨道同时转红。

**第三格必须长得不像刻度。** 它不属于这张单：它讲的是"交给这个部门一般多久有人接"，从归口那一刻算起，和这张单的创建时刻无关。画成同一种刻度，就是又把"部门期望"和"本单计时"混着看了一次——那正是 `#tkSlaNote` 里花一整句在解释的事。既然一句话解释不清，就让形状来说。

未挂归口部门的单，第三格是 `—` + 「没挂归口部门，就没有部门时限」——与周带「待归口不许伪造 deadline」同一条规矩。

数值全部由后端算（`campuspulse.tickets.clocks()`），前端只算宽度比例。**前端不许自己加工作小时**：它只有周末概念，`config.HOLIDAYS` 里的节假日它不知道，节前节后会差出一两天，而这一格正是用来判断"还剩多少"的。

超时时边框、底色、填充条一起变红；填充条顶到 100% 后在右端加一个朝外的小三角——"刚好用满"和"还在涨"必须分得开。脚注用 `word-break: keep-all`：「尚未响应」不会被拆成「尚未响 / 应」（中文默认逐字可断）。

### 二、`.tk-bulk` 不再是 44px 空方框

第 11 轮的 44px 热区是 `content-box` 的 `padding:11px` 撑出来的：屏幕上只有一个 44px 的方框，勾选标记还是原生那枚指甲盖，看着像占位。而且那份尺寸写在行内 `style` 里，媒体查询压不动——和 `.tk-open` 的 44px 是同一类事故。

现在：`appearance:none`，盒子 44×44（热区），`::before` 画 18×18 的框（`--ink-3` 边框、`--surface` 底、5px 圆角），`::after` 画对勾，勾选后框填 `--brand`。负外边距（`-11px 0 -11px -12px`）把多出来的 26px 还回去，行距与左边缘与改动前一致；`≤720` 只改 `align-self`，尺寸不再分两套。

### 三、周带：窗口跟着数据走，「之后」不再是一个形容词

两个真问题：

1. **行与自然周不对齐**。原实现"从今天起数五个工作日当一行"，周三打开时那一行跨两个自然周，标题写「本周」却有两格是下周的日期。现在从**本周一**起铺，一行就是一个自然周，五列永远是周一~周五；本周已经过去的那几天照样占格（虚线顶边 + 淡日期），因为"这周一空着"本身是条信息。
2. **窗口固定两周，落不进去的主题被扔进「两周之后」且只有名字**。现在铺四周（20 个工作日）再取前三行：前两行恒在，第三行只在真有时限落进来时出现（否则就是五格空白，白白把人往下推一屏）；超出窗口的进「窗口之外」横条，**每个 pin 自带 MM-DD**，横条说明只说"时限在窗口之后"，不复述范围（那一列只有 108px，复述会把说明折成四行、把 pin 挤走）。

行标题按与本周的**周差**算（本周 / 下周 / 再下一周 / 三周后 / 四周后），不是按可见行序——周日在铺四周时第三行已经差到三周，写「再下一周」就错了一格。卡片标题也跟着第一行走：本周有活就是「本周要办结的事」，否则「接下来要办结的事」，副标题写明 `窗口 11-16~12-04，15 个工作日`。

日期补零（`12-04` 而不是 `12-4`），五列才有节奏。

### 本轮实测

- `tools/verify_ui.py` **34/34**（新增场景 G 四条：三把尺子＝两刻度＋一参照且数值等于后端 `clocks`、复选框 44px 热区／18px 视觉／无行内 style、周带每行是 5 格且星期为周一~周五、横条条目自带 MM-DD 而待归口仍写「未归口」）
- `tests/acceptance.py` **78/78**；`tsc -p jsconfig.json --noEmit` **0 条**
- 检测器：CSS 新增段 **0 条**（首版 `.tc-rail` 的 3px→999px 都报圆角越档，改成 `var(--radius-sm)`——5px 高的条会被钳到半高，视觉就是胶囊）；URL 扫描 `/departments` 1280 剩 2 条已 pin 的 dark-glow、**390 为 0**；`/tickets` 剩 4 条（2 dark-glow + 1 pulsing-dot + 1 nested-cards，均为既有）
- 1280/390 双视口：`/departments`、`/tickets` 0 console error、0 横向溢出；深色模式复查三处新组件（超时红卡、虚线参照框、复选框）均无浅底残留
- 跨零点实测：23:59 打开是「下周 11-16~11-20」，00:0x 重开自动变「本周 11-16~11-20 · 今天」——日期滚动不需要刷新也能自洽
- 演示库 `data/campuspulse.db` 未写入；验证用 `data/cp_r14.db` 副本，跑完即删

### 仍未做

- 周带只到四周；更远的主题靠「窗口之外」承接（带日期），不做横向翻页。
- 部门期望响应那一格没有历史（比如"该部门近 30 天平均首次响应 X 小时"），只有配置值。要做需要后端先有按部门聚合的响应时长。

## 第 16 轮同步（2026-11-16，用户裁定「全部做完」→「一次全跑」）

第 16 轮 `/impeccable critique` 打 **25/40**（第 11 轮 27）。**分数低 2 分不是改坏了**：第 12–13 轮把「时限」和「三把尺子」做成了形状（真收益），但这两个新形状第一次把「工作小时」和「小时」摆进同一屏——旧缺陷因此变得可读，才被扣分。评审判定「形状长出来了，刻度没有校准」：周带与三把尺子换成任何 BI 都长不出来，但**这两个形状最该守住的单位，界面自己把它填了回去**（判断链写「48 工作小时」，同一屏的部门徽章写「48h 内响应」）。

用户三项裁定：① **先统一口径**（唯一格式化器 + 验收断言，不靠每轮眼睛抓）；② 周带**保留两行，只补信息**（不重排结构）；③ 范围**全部做完**（2 个 P0 + 3 个 P1 + 9 条小观察），随后一句「一次全跑」授权六步一次做完。

### 一、P0-1：工作小时只能有一种写法

`sla_hours` 的单位是工作小时（每天 7 个）。`48h` 读作两个自然日，`48 工作小时`≈9.6 个自然日——**差 4.8 倍且方向相反**。这是 PRODUCT.md 写明的唯一 SLA 难点，第 11 轮专门为它改过判断链文案，第 16 轮把它变成**写不出别的写法**：

- **`core.js` 新增三个全站唯一格式化器**：`CP.workHours(n)` → `48 工作小时`（一位小数、`.0` 截掉、`null` → `—`）、`CP.workdays(n, perDay)` → `9.5 个工作日`（`perDay` 由接口给，**不写死 7**）、`CP.onTimeFoot(ot, min)` → 样本 ≥10 才换算百分比，不足则写「按时归档 2/2 · 样本不足 10 单，不换算百分比」。`cp.d.ts` 补三条签名并注明「业务文案里裸写 `48h` 一律禁止」。
- **四处裸 `h` 全部改掉**：`departments.js` 的部门卡徽章 `48h 内响应` → `响应 48 工作小时`（带 `title`）；`tickets.js` 的 `delta()` → `已超 66.6 工作小时`；`tickets.py` 的 `_human_minutes()` 小时位一律带「工作」，且**有小时就丢掉分钟位**（SLA 场景下宁可少说，也不给一个会被当成自然时间的四舍五入数）；`tc-num` 的数字与单位之间补空格。
- **口径由后端下发，前端不猜**：`tickets.sla_policy`、`/api/tickets/meta` 的 `sla`、`/api/departments` 顶层三处都带 `workday_hours = config.WORKDAY_HOURS`；周带脚注「每工作日 7 小时」改成读 `view.meta.workday_hours || 7`，改配置后不会变假话。
- **验收断言**：`verify_ui.py` 场景 H 的 `t_no_bare_hours` 扫渲染文本——数字后紧跟「小时」且不落在「工作小时」内即失败；「每工作日 7 小时」这类合法写法显式放过。

判据不只是文案：**同一个数在两页出现两种单位，比算错更伤可信度**。评审给的例子是全站唯一一处 `2/2`：`/departments` 写「样本不足 10 单，不换算百分比」、`/tickets` 写「= 100%」——本轮由 `CP.onTimeFoot()` 一处收口，两页同源。

### 二、P0-2：计时已停，但界面还在倒计时

`clockTile()` 原来只看 `used > budget` 判超期，不看「计时是否已停」。实测 TK-20261030-0002：`first.used = 0.0`、`first.at = 2026-10-30 17:23:59`，该格却渲染成 `0 / 4 工作小时` + **「还剩 4 小时」+ 空轨道 + 正常色**。看这页的人是来救火的，这一格正是他判断「要不要现在催」的依据——已经响应了却说还有 4 小时可用，是「界面不许说假话」被破在最关键的一格。

现在 `clockTile(name, src, used, budget, foot, done)` 增第 6 参，`done = !!cl.first.at`：

- 已停 → 脚注换成 `已响应 · 用时 0 工作小时 · 按时 · 10-30 17:23`，轨道按**实数**填，`.is-done` 给绿条、超时给红条；「还剩 N」分支在 `done` 时彻底不出现。
- 未停 → 保持原样（`已超 66.6 工作小时 · 尚未响应`）。

`.tk-clock.is-done .tc-foot` 降到 `--ink-3`：结论已经由条的颜色说了，脚注是补充。**「还没开始」和「已经量过了」必须长得不一样**——这是全站三态规则（骨架 / `—` / 数字）在单格上的同一条。

### 三、P1：390 首屏没有工单

实测 390×844：`#tkKpi` y=633 高 544，第一张 `.tk-item` **y=1455**——值班的人滑 1.7 屏才见第一张单，而第一屏给的是「规则建议 · 未命中」（把空结果当主角）。三处一起收：

1. **规则卡折叠**：`suggCard` 内部改 `<details class="sugg-fold" open>`，`summary` 就是原来的 `.card-head`。桌面默认展开（`open`），≤720 由 `cp:ready` 里 `fold.open = false` 收起——84px 替代 269px，`summary::after` 用 CSS `content` 写「展开 / 收起」，原生可聚焦、可键盘开关，不造假控件。
2. **KPI 2×2**：≤720 `#tkKpi { grid-template-columns: repeat(2, …) }`，544 → 305px。
3. **列表提到 KPI 之前**：新包一层 `.tk-flow`（suggCard + tk-layout）；`#tkKpi` 从 `tk-layout` 之外**移进** `tk-layout` 内部第一项，桌面 `grid-column: 1 / -1; order: -1`（仍在最前），390 `order: 2`（列表之后、详情之前）。

**踩过的坑**：只用 `order` 把 KPI 推到后面，它会跑到 y=3582（详情后面）。`order` 只在同一个 flex/grid 容器内生效，所以 DOM 必须先搬进 `tk-layout`。实测最终 390：sugg 350 / h84、list 461、first **726**、kpi 1612。

### 四、P1：周带 pin 的 57 没有单位，责任人不在带里

pin 原来只有 `<b>57</b>`，单位只在 `aria-label` / `title` 里；第二行只有部门 + 日期，「陈老师」只在下方表里——而这是全站唯一「一眼看出今天该催谁」的组件。

现在 `pinHtml()` 输出 `<b>57<span class="wk-unit">分</span></b>` + 三个 span：`.wk-dept`（可省略号）／`.wk-own`（负责人，无则回落部门）／`.wk-due`（`11-12`）。`aria` 串同步补负责人。实测：

```
57分 | 宿舍住宿 | 后勤保障处 | 陈老师 | 11-12
```

- `.wk-nm em` 由 `nowrap` 改 `flex-wrap: wrap` + `column-gap: 4px` + `row-gap: 1px`：**负责人不能丢，宁可折一行**（pin 高 43 / 59，无省略号、无重叠）。
- 390 可见条目（`display:none` 的桌面那套不算）实测 6 条皆 **44px 高、332px 宽**，未归口的两条只写「24分 交通出行 未归口」、**不给日期**——与第 12 轮「不许替没人认领的事伪造 deadline」同一条规矩。
- 副标题补「数字是**风险指数（0–100）**」，读者不必滚回页首找定义。

### 五、P1：点 pin 后焦点被留在屏外

`scrollY` 0→1017，`document.activeElement` 仍是那个已滚出屏的 `.wk-pin`（`top = -626`）——WCAG 2.4.7 直接失败，且这是进入风险表的**唯一键盘路径**。

- `focusDetail()`：给 `#dpDetail` 补 `tabindex="-1"` 并 `focus({preventScroll: true})`（先滚后聚焦，避免二次跳动）。实测点 pin 后 `activeElement = div.dpDetail`、`top = 322`、在视口内。
- `backToWeek()`：详情动作行在有周带时多一个 `data-backweek` 按钮「回到周带 ↑」，按 `topbar.offsetHeight + 10` 算偏移滚回，并把焦点交还对应 `data-row` 的 pin。实测回到 `.wk-pin`、`top = 290`、`scrollY = 108`。
- 有回路才叫「用户控制与自由」：第 10 轮的 `revealDetailOnNarrow()` 是同一套写法（滚动 + 交还焦点），不要另造一种。

### 六、小观察（9 条）

- **`.dp-scope-line` 替代 68px 示例配置黄条**：`.dp-notice` 三条规则整体替换为一句 `.scope-line`（`--ink-3`、`strong` 用 `--warn-ink`），**信息一条未删**，只是不再用整条黄底把答案推下去。`.dp-notice` 全站已无引用。
- **「更多分组 ▾」→「按归属」**：它筛的是单子归谁，不是换排序；`aria-label` 同步为「按归属筛选工单」。
- **登录页 01/02/03 删除**：三条特性前的方块编号去掉（第 12 轮删侧栏编号的同一条理由：它们不是步骤），`login.css` 的 `.login-features li > span` 规则一并删；注释写明「现在就是三行字，不留编号、也不补一个没有信息的圆点」。
- **登录页不再露服务器路径**：「初始管理员密码见 `data/initial_admin.txt`」→「见 `README` 的「首次启动」一节」（README 第 46 行写了同一件事）。投影时不该露出文件名。
- **`/data` 的危险动作退位**：「恢复内置数据」从 `.card-tools` 右上主位移进卡片底部 `.ds-maint <details>`，summary 自带后果说明「恢复内置数据会重跑清洗、覆盖数据版本」。行内「重算」都要先问，全局破坏性动作不该占最高可见位置。
- **代表证据按正文去重**：同一条评论被两个平台各采一次（`comment_ref` 不同、正文一字不差），五条证据里出现两句一模一样会削弱这块的可信度。`tickets.pick_evidence()` 与 `departments._samples()` 的 `LIMIT` 放宽到 `limit * 6` 后按 `"".join(text.split())` 判重再截取；`_evidence_items()` 在显示层二次判重（失联证据「（证据原文已不在当前数据集内）」不参与判重）。
- **三把尺子改成「一把尺子的三段」**：见第 13 轮表格下的「第 16 轮改写」——竖线分栏 + `outline` 虚线参照框，`nested-cards` 真阳性归零。
- **周带脚注措辞**：`含节假日跳过` → `逢节假日跳过`；`可在下方展开` → `可展开`。
- **「中高风险主题」卡的脚注给出四级分布**：`共研判 N 个主题 · 高 a / 中高 b / 中 c / 低 d`，与表格能当场对上——原来只写「中高风险 1 个」，旁边表里 3 行标着中高/中/中，读者只会觉得这个数算错。

### 新增 / 变更组件（登记进 Components）

| 选择器 / API | 形态与约束 |
|---|---|
| `CP.workHours` / `CP.workdays` / `CP.onTimeFoot` | 全站唯一的时间单位出口。业务文案禁止裸拼「小时」；`perDay` 必须由接口给；样本 <10 不换算百分比 |
| `.tk-clocks`（竖线分栏） | `gap: 0` + `margin: 0 -14px` + 每格 `padding: 4px 14px` + `.tk-clock + .tk-clock { border-left: 1px solid var(--line) }`；≤720 改纵排、去竖线、超期与参照补回内边距 |
| `.tk-clock.is-done` | 计时已停：轨道按实数填，绿条 / 超时红条，脚注降 `--ink-3`。与 `.is-over` 可叠加（`.is-done.is-over` 红条） |
| `.tk-clock.is-ref` | 参照格：`1px dashed` **outline**（非 border）+ 方角 + 透明底 + 无填充条；`outline-offset: -1px`，`margin: 0 4px` / `padding: 4px 10px` 与另两栏对齐 |
| `.sugg-fold` | 规则建议折叠：`summary` = 原 `.card-head`，桌面 `open`、≤720 关；`summary::after` 写「展开 / 收起」，`list-style: none` 去默认三角 |
| `.ds-maint` | 维护操作折叠：卡片底部 `border-top: 1px dashed`、summary 为 `.scope-line`，后果写在 summary 文本里 |
| `.dp-scope-line` | 页首口径行（替代 `.dp-notice`）：`--ink-3` + `strong` `--warn-ink`，不占卡片、不铺底色 |
| `.wk-unit` / `.wk-dept` / `.wk-own` / `.wk-due` | pin 主数字的单位（`--fs-micro` / `--ink-4`）与第二行三级信息：部门可省略号、负责人与日期 `flex: 0 0 auto; nowrap` |

### 本轮实测

- `tools/verify_ui.py` **39/39**（新增场景 H 五条：`t_no_bare_hours` / `t_stopped_clock` / `t_mobile_ticket_first_screen` / `t_pin_unit_and_owner` / `t_pin_focus_handoff`；场景 G 的参照框断言由 `borderTopStyle` 改为同时接受 `outlineStyle === 'dashed'`）。
- `tests/acceptance.py` **78/78**；`tsc -p jsconfig.json --noEmit` **0 条**。
- **16 路由 × 2 视口**（`/`、`/insights`、`/insights/topics|sentiment|timeline`、`/departments`、`/tickets`、`/data`）：0 console error、0 `pageerror`、`scrollWidth − clientWidth` 全为 **0**。
- 检测器：文件扫描 **17 条**（1 anti-pattern + 16 advisory，全是 Jinja `static_url()` 解析不出 CSS 的假阳性，与基线一致）；CSS 扫描 **180 条**（7 等宽族 + 173 令牌建议；评审时 185）；URL 扫描 `/tickets` 1280 **3** / 390 **1**、`/departments` 1280 **2** / 390 **0**——**`nested-cards` 从 4 条归零**，剩下的全是已 pin 的 `dark-glow` + `pulsing-dot` + 1 条 `body` 幽灵 `#8ca2ff` 误判（`getComputedStyle(body).boxShadow === "none"`）。
- CSS 花括号 `app.css` 1245/1245、`login.css` 79/79；**0 裸 px 字号**；`:root` 九档与 `CP.CHART_FS` **未改**。
- 演示库 `data/campuspulse.db` sha256 前 16 位仍 `eced9a5258b853e7`、mtime 未变（未写入）；验证全程用 `data/cp_r18.db` 副本，跑完即删。

### 仍未做（如实记录）

1. **详情内的动作顺序未动**：「提交复核」仍在 5 条证据 + 5 步时间线之后（约 1500px），本轮只补了「回到周带」的回路。评审把它算在 P1 的同一段里，实际只做了一半。
2. **表不能按「未归口 / 超期」筛**，选中行不写 URL（`selectRow` 不落 query），没法把「看宿舍住宿」发给同事。`/tickets` 写了 `?ticket=`，两页不一致。
3. **`/tickets` 第四张 KPI 仍是一卡两指标**：卡内「平均处置时长 N 工作小时」（`avg_handle_hours`，`work_minutes_between` 的均值，确为工作小时）+ 脚注「按时归档 2/2」。单位本轮已统一，但「一段时长」和「一个比率」共用一个卡位这件事没拆。
4. **顶栏三个低频管理动作**（重命名 / 主题 / 术语）仍占右上角，其中「重命名」紧挨数据集切换。第 9 轮为撑破 topbar 已经改过一次短文案，本轮未动结构。
5. **「点任意一个主题可在下方展开」的措辞**仍按桌面写（桌面下方 300px、390 下方 1100px）。
6. 三把尺子的 `--fs-micro` 脚注在浅红底上约 **4.76:1**，刚过 4.5 线；它是判断「超没超」的唯一文字，下一轮若要提档，落 `--fs-xs`。


## 第 17 轮同步（2026-11-16，收掉第 16 轮「仍未做」的 6 条）

第 16 轮末尾留了 6 条「仍未做」，这轮全部收掉。没有新增能力，改的都是**界面在说假话、藏着话、或顺序反了**的地方：一段时长和一个比率挤在一个卡位、动作按钮要滚 1500px 才出现、选中行发不出去、"下方"在两个视口指两个位置。

### 一、`/tickets` 详情：动作块提到证据与时间线之前

顺序由「三把尺子 → 建单口径 → 代表证据 → 处置时间线 → **可执行操作** → 追加评论」改为「三把尺子 → **可执行操作** → 建单口径 → 代表证据 → 处置时间线 → 追加评论」。

第 16 轮实测「提交复核」在 5 条证据 + 5 步时间线之后（390 约 1500px），而打开这一页的人是来救火的：他要先知道**现在能做什么**，五条评论是事后复查用的。块标题补一句 `em`「按当前状态与身份可用」——它回答的正是"为什么只有两个按钮"。

### 二、`/tickets` KPI：一段时长与一个比率拆成两张卡

第四张卡原来是大数字「平均处置时长 3.7 工作小时」+ 脚注「按时归档 2/2 = 100%」：**一段时长和一个比率共用一个卡位**，读者会把它读成同一个指标的两种说法。现在拆成第五张「按时归档」，数值走 `CP.onTimeParts`（与研判页「工单闭环」同源：样本 <10 单给 `—` + 「样本不足 10 单，不换算百分比」）。

栅格由 `.g-4` 换成 `.g-5`（auto-fit）：5 张卡放得下几列就几列，行尾不留空槽；`#tkKpi` 的 ≤720 规则仍是 2 列，`.g-5 > :last-child` 让第五张跨两列，正好 2+2+1。`CP.onTimeParts(ot)` 因此新增为公开 API（`cp.d.ts` 同步），`CP.onTimeFoot` 仍是它的字符串版。

### 三、`/departments`：表可按「未归口 / 已超期」筛，选中行写进 URL

- **三枚 chip**（`全部 / 未归口 / 已超期`，数量直接写在 chip 上）与表头图例同一组，代码在 `riskFilters()`；`view.filter` 只影响这张表——**周带不讲筛选**，它回答的是"全部主题落在未来哪一天"，筛掉一半就是一个不完整的答案，所以筛选行上写明「只筛这张表，周带上仍是全部主题」。
- **「已超期」只认真实工单的截止日**（`due_basis === 'ticket'` 且 `due` 早于今天），与周带「已超期」横条同一条规则；未建单的虚线点是推算，还没开始的时钟谈不上过期。
- **筛完再排**：`sortedTopics(pairs)` 改为接收已带原始索引的 `[{t,i}]`，排序只换 DOM 顺序、索引仍指回 `activeTopics()`，点开的行不会串到别的主题。筛空时给「看全部 N 个主题」的就地出口，不写"当前数据集没有可研判的主题"（那是另一件事）。
- **选中行写 URL**：`?dim=宿舍住宿`（`replaceState`，不塞历史栈）。带 `?dim=` 打开会还原选中并展开详情；那一行正被筛选挡着时先回「全部」，否则点开链接只看到"没有这一行"。换窗口 / 收起详情都会把参数删掉。至此 `/departments` 与 `/tickets` 的 `?ticket=` 是同一套做法。
- chip 重画会销毁焦点：`setFilter()` 渲染完把焦点还给同一颗 chip（否则键盘用户按一次就掉到 body，下一个 Tab 从页首开始）。

### 四、顶栏：三个低频管理动作收进「更多」

重命名 / 主题 / 术语一个月也点不到一次，却各占一个常驻图标位，其中「重命名」紧挨数据集切换，读起来像"切数据集"的第二个按钮。现在顶栏只留：待办、快照、数据集、**更多**、账户（`.topbar-right` 直接子项从 9 个减到 6 个）。

- 容器是原生 `<details class="top-more">`：`summary` 可聚焦、回车/空格开关，菜单项写**全名**（「重命名数据集 / 切换深色 · 浅色 / 术语说明」），不再是 2–3 字缩写。
- `core.js: bindTopMore()` 负责三件：点开关型菜单项（主题）后就收起；**收起时若焦点还在容器里，先交回 `summary`**（关掉的容器里的焦点看不见也 Tab 不到，WCAG 2.4.7）；Esc 收起并把焦点交回按钮。`dsRename` / `glossaryBtn` 会开弹窗、弹窗关掉时焦点要回到它们身上，所以点这两项时菜单**不**立即收起。
- ≤720：`summary` 与每一项都是 44px 热区，面板 `right:0` 锚在触发按钮上，390 实测左缘 16px、右缘 216px，不越界。

### 五、措辞按视口写：「在哪展开」

`.dp-lg-note` 原来只有一句「点任意一行（或按回车）展开四项分值与权重」——桌面详情插在被点那一行正下方，390 是单列、详情在整张表之后（实测 1100px 开外）。同一句"下方"在两个视口指的不是同一个位置，写死一句等于骗一半的人。现在按视口给两句：

- 桌面：「点任意一行（或按回车）**在这一行下方**展开四项分值与权重」
- ≤720：「点任意一行，**明细在整张表格下方**（页面会自动滚过去）」

周带脚注同步改为「点任意一个主题，明细在下方风险表里对应那一行」（两个视口都成立）。

### 六、三把尺子脚注：档位与字色

`.tc-foot` 从 `--fs-micro` 提到 `--fs-xs`。**如实记录：micro 与 xs 同为 12px**（见 Typography），所以这一条本身不改观感；4.76:1 的真正修法是颜色——超期那一格 `.tk-clock.is-over .tc-foot` 改用 `--neg-ink`（`#B42318`，浅红底 ≈6.2:1），不再沿用"次要文字"的 `--ink-4`。`verify_ui.py` 用 `getComputedStyle` 逐格算对比度，断言 ≥12px 且 ≥4.5:1（实测最差 5.18:1）。

### 新增 / 变更组件

| 选择器 / API | 形态与约束 |
|---|---|
| `.top-more` / `.top-more-pop` / `.top-more-item` | 顶栏「更多」：`summary` = `.theme-btn`（含 `.topbar-icon-text` 的「更多」，≤1100 自动隐去）；面板绝对定位锚右、`z-index:30`；项高 38（≤720 为 44）；菜单项图标类 `icon-sun` / `icon-moon` 需在 `.top-more-item` 作用域内重新声明一次显隐 |
| `.dp-risk-filters` / `.dp-rf-label` / `.dp-rf-count` | 风险表筛选项与计数；chip 复用 `.chip`（≤720 已 44px）；计数句必须写明"只筛这张表" |
| `CP.onTimeParts(ot, min)` | `{value, note}`：`value` 给 KPI 大数字位、`note` 给脚注位。与 `onTimeFoot` 同一份计算，两页不会各说一套 |
| `.tk-block-title em`（详情动作块） | 「按当前状态与身份可用」——解释"为什么只有这两个按钮"，字号 `--fs-micro`、色 `--ink-4` |

### 本轮实测

- `tools/verify_ui.py` **44/44**（新增场景 I 五条：动作块在证据之前 / KPI 一卡一指标 / 未归口筛选 + `?dim=` 深链 / 顶栏「更多」三项且 Esc 回焦 / 尺子脚注 ≥12px 且 ≥4.5:1）。场景 A 的两条断言随控件搬家改为"先开「更多」再点"，判据没变（仍验 `data-theme`、`localStorage`、`aria-pressed`）。
- `tests/acceptance.py` **78/78**；`tsc -p jsconfig.json --noEmit` **0 条**。
- **12 路由 × 2 视口**（12 页 × 1280 / 390）：0 console error、0 `pageerror`、`scrollWidth − clientWidth` 全为 **0**。
- 1280 实测：筛选 chip 为 `全部 8 / 未归口 4 / 已超期 1`，点「未归口」后 8 行 → 4 行且每行都是「未归口」；选中首行 URL 变 `?dim=宿舍住宿`，带该参数重开还原选中并展开详情。顶栏 `.topbar-right` 直接子项 `ticketBadge / snapshotBadge / ds-label / dsSelect / dsSelectHint / topMore`。
- 390 实测：chip 高 44px；KPI 2+2+1（`175px 175px`、5 张）；第一张工单 y=726（首屏 844）；「更多」面板 3 项均 44px 且不越界。
- 深色复查：`.top-more-pop` 底 `rgb(19,28,43)`、文字 `rgb(194,205,222)`，无亮底残留。
- **演示库写入如实记录**：页面验证（`verify_ui.py`）全程跑在 `data/cp_r19.db` 副本上，演示库没被它碰过；但 `tests/acceptance.py` 按既有行为会往演示库写历史报告（本轮 33 份 / 6 组口径），跑完已用 `tools/clean_devdata.py --apply` 清回（删 27 份重复，剩 6 组口径）。清理前后 sha256 **不再相同**（时间戳与页布局都变了），所以本轮不写"逐字节未变"这种话；内容层面恢复为演示态。
- `:root` 九档字号**未改**，`CP.CHART_FS` 无需同步。

### 仍未做（如实记录，第 17 轮之后剩下的）

1. **表头排序箭头被 `.dp-th-hint` 挤到第三行**：`.dp-th-hint` 是 `display:block`，`.sortable::after` 的「↕」落在它之后，于是「风险指数 / 截至 07-07 / ↕」三行。第 13 轮起就是这样，本轮未动（改它要动 `th` 的定位与箭头位置，与筛选无关）。
2. **表筛选状态不进 URL**：只有选中行进（`?dim=`）。筛选是当下浏览动作、不是"要看这条"，暂与 `?status_group=` 的取舍保持一致。
3. **部门期望响应那一格仍没有历史**（"该部门近 30 天平均首次响应 X 工作小时"），只有配置值；需要后端先有按部门聚合的响应时长。
4. **顶栏「更多」里三个入口没有权限区分**：只读账号也能看到"重命名数据集"，点了由后端 403 兜住。要收紧得先有前端能力探测。

## 第 18 轮同步（2026-11-16，critique 后一次修完）

这一轮不是新功能，是**评审**：两路独立取证（设计评审跑在隔离上下文里，检测器与浏览器取证另跑一路，互不可见），再把结论里的 P0 / P1 与全部"有硬证据"的项一次修掉。

- **设计评审（Nielsen 10 条）**：31 / 40。最强项是 #1 系统状态可见、#6 识别优于回忆、#10 帮助做进界面；扣分集中在 #2 贴近真实世界、#8 美学与极简——即"模型复杂度外溢到界面"。判断是"特异性是真的，但被信息架构埋了"：`风险指数 / 归口 / 工作小时` 在 `/`、`/agent`、`/insights`×3、`/evidence`、`/data`、`/data/reports` **九个页面出现 0 次**，只在第 5、6 个入口之后才出现。
- **机械取证**：12 路由 × 2 视口。横向溢出 0、console error 0、`requestfailed` 0；对比度不合格仅 3 条且全部是 `disabled` 按钮（2.75:1）；全站最小字号 12px（无 <12px）；390 小目标集中在 `/evidence`（34 个）、`/data/collect`（3）、`/departments`（1）。

### 一、驾驶舱：把产品的主张搬上第一屏（P0）

`dashboard.html` 首屏原来只有「待处置/超期」横幅 + 5 张声量情感 KPI + 一段 AI 长文——评委连点三页，看不到这个产品独有的任何东西。

新增 `.dp-today`「**今天要办的三件事**」，排在筛选栏之后、KPI 之前：

- 数据取 `/api/departments/risk?ai=0`（规则引擎、几十毫秒、不调模型），**排的是风险不是热度**；一条热搜如果没有人需要动，它不该出现。
- 一行四段对应四个问题：`57`（多急）· `宿舍住宿`（是什么）· `后勤保障处 · 陈老师`（归谁）· `11-12 截止`（多久之内）。行链接到 `/departments?dim=宿舍住宿`（第 17 轮做的深链），点开就是那一行的四段构成。
- 脚注写清三件事：共研判 N 个主题 / 高·中高 X 个 / **时限按工作小时计（不计周末与午休）**；并明说**不随上方筛选变化**（`pending` 与研判都是近 30 天全校口径）。
- 390 折成两列（分数占左、其余叠右），行高 64px。
- 无研判权限（只读角色 / 接口未开）时整块不出现——不摆一个空壳冒充"今天没事"。

### 二、工单横幅改口径：「待处置」→「未结」（P0 的一半）

`pending` 的口径是**所有未归档**（draft/triaged/assigned/processing/pending_review/reopened），而工单页的「待分派」只是其中一格。首页说「待处置 5 单」、点进去「待分派 0 · 处理中 3」，值守的人只会认为数字错了。

现在写「**未结 5 单** · 含已分派与处理中」，并在右侧补一句「工单数字不随上方筛选变化」（切平台后 KPI 会变、横幅不变，以前没说）。

### 三、三把尺子：主数字按状态换，两格不再一模一样（P1）

首次响应与归档时限都从**建单**起算，所以两张卡的大数字永远相同（实测 70.6 / 70.6），并排读起来像重复渲染。现在 `clockTile()` 按状态选主数字：

| 状态 | 主数字 | 小字 |
|---|---|---|
| 未超时 | 已用（`70.6`） | `时限 7 工作小时` |
| 已超时 | **已超**（`63.6`） | `已用 70.6 / 时限 7 工作小时` |
| 已响应/已归档 | 用时 | `时限 …` |

「已超多少」才是要动手的理由，而"已用 / 时限"这对数仍然留在小字里可复算。`verify_ui.py` 场景 G 的断言同步改为按这个规则重算（两格都超时且时限不同 → 主数字必须不同）。

### 四、跨数据集的单：把数字降级成快照，并给出切口径的动作（P0）

以前用三行 12px 黄字解释"为什么 71.8% 和 5.9% 打架"，读者记住的只是两个数在打架。现在：

- 该块整体降级为 `.tk-preview.is-snap`（灰底、无左边框、字色 `--ink-3`），前缀写「**建单时快照**」而不是「按此口径重算」；
- 块尾给一枚「**切到该单口径**」按钮（`data-ds` → `CP.switchDataset`），点了顶栏数据集跟着换——这是唯一能真正对上账的动作，以前只写在提示里让人自己去下拉里猜。

### 五、风险表头回到单行（P2，第 17 轮「仍未做」的第 1 条）

`.dp-th-hint` 是 `display:block`，`.sortable::after` 的排序箭头落在它之后，于是「风险指数 / 截至 07-07 / ↕」三行、`th` 高 76px。改成内联 + 箭头绝对定位后 **40px 单行**。

### 六、示例配置离开首屏（P1）

那句橙色警示原本压在周带上方——评委第一眼读到的是"交付未完成"。现在：

- 部门、负责人、联系方式是示例配置 → 写进「部门与职责」卡片的副标题（读者正好在看到真实姓名与电话的那一屏读到它）；
- 风险指数的定义与免责 → 写进「风险指数怎么来的」卡片副标题（`不是负面占比，也不代表对言论真实性的认定`）。**一个字没删**，只是换了位置与权重。`.dp-scope-line` 规则随之删除。

### 七、触控与对比度：按取证数值补齐

| 位置 | 原来 | 现在 |
|---|---|---|
| 证据页多选框 `.ev-bulk` | 13×13，无 label 包裹，全站最小目标 | `<label class="ev-pick">` 包一层：热区 32（≤720 为 44），视觉 16/18 |
| 证据页「展开全文」`.ev-more` | 48×19 | 高 30 / ≤720 为 44 |
| 证据页排行行 `.rank-row` | 332×40 | ≥40 / ≤720 为 44 |
| 研判页「展开看算法」`#dpWeightsTip` | 76×28 | ≤720 为 44 |
| 采集页分段 `#intakeSeg .seg button` | 80×30 | ≤720 为 44 |
| `.btn:disabled` | `opacity:.55` → 合成后 **2.75:1** | 去透明度，改显式 `--ink-4` + `--surface-2` → **4.95:1** |

顺带修掉：点复选框的 label 留白不再被当成"打开详情"（`closest('.ev-pick')`）。

### 八、报告页空态不再自相矛盾

`/data/reports` 上方写"还没有生成报告"、下方列着 3 份历史——其实是"本次会话还没打开"。改成「**还没有打开报告**」+ 「可以在下方历史报告里重开，重开不会重算数字」。

### 新增 / 变更组件

| 选择器 / API | 形态与约束 |
|---|---|
| `.dp-today` / `.dp-today-head` / `.dp-today-row` / `.dp-today-foot` | 驾驶舱三件事。行是 grid（56px / minmax(84px) / 1fr），**部门与截止日必须在同一格**（首版四列被 672px 空白拆成两件事）；≤720 折两列、行高 ≥44 |
| `.dpt-score` / `.dpt-dim` / `.dpt-who` / `.dpt-dept` / `.dpt-due` | 分数按等级取色（与 `/departments` 的 `LEVEL_CLS` 同值，同一档不许在两页两种颜色）；部门可省略号、日期 `tabular-nums` |
| `.tc-sub` / `.tc-lim` | 时钟主数字的单位后缀与「已用 / 时限」小字。**两格主数字相同时必须先怀疑这一层** |
| `.tk-preview.is-snap` / `.tk-snap-switch` | 跨数据集快照块与「切到该单口径」按钮 |
| `.ev-pick` | 复选框热区壳（32 / ≤720 44），视觉仍是原生 16/18px 方框 |
| `.dp-risk-table th.dp-col-risk` | 定了 `padding-right:26px` + 箭头绝对定位，表头恒单行 |

### 本轮实测

- `tools/verify_ui.py` **49/49**（新增场景 J 五条：驾驶舱正文出现风险指数/归口/工作小时且排在 KPI 之前 / 横幅改「未结」并说明不随筛选 / 表头单行 40px / 390 证据页有效目标 0 个 <44 / 禁用按钮 4.95:1）。场景 G 的时钟断言按新规则重写。
- `tests/acceptance.py` **78/78**（含 `tsc --noEmit` 0 条）。
- 12 路由 × 1280/390：横向溢出 0、console error 0、pageerror 0。
- 驾驶舱首屏：三件事 y=263、KPI y=570（1280）；390 三件事 y=418、行高 64、KPI 落到 y=839（首屏让给"要办的事"）。
- 检测器：**文件扫描 17 条**（16 条 `design-system-color` 全是 Jinja `static_url()` 读不到样式表的假阳性 + 1 条 `login.html` 的 `flat-type-hierarchy` 同因），与第 16/17 轮基线一致。**URL 扫描**：`/` 2、`/departments` 2、`/evidence` 2（均 `dark-glow`×2：`.brand-mark` 真实 + `body` 幽灵误判）、`/tickets` 3（+`pulsing-dot` 1，已 pin）、`/agent` `/insights` `/data` `/data/collect` 各 3（2 `dark-glow` + 1 `nested-cards`）。**`/` 与 `/tickets` 的 `nested-cards` 已归零**，本轮新增组件 0 条命中。
- 演示库：`acceptance.py` 写入的报告已用 `clean_devdata.py --apply` 清回；页面验证全程在 `data/cp_crit.db` 副本上，跑完删除。

### 未采纳 / 仍未做（如实记录，附理由）

1. **`nested-cards` 4 条不拆**：`/agent` 是聊天气泡 `.msg.bot`（气泡底承载"谁说的"），`/insights` `/data` 是 `.note.note-warn`（警示语义），`/data/collect` 是登录态面板。三者都是"内容块自带语义底"，不是卡片套卡片；硬清会抹掉状态色。与第 10 轮 `.cp-flat` 的处理口径一致。
2. **全站时长统一成一个单位（`CP.duration`）**：评审建议"永不并存 工作日 / 工作小时"。这要改 `sla.amount` 的生成、列表右格、详情脚注与 3 处断言，属独立一轮；本轮只解决了"两格主数字相同"这个更硬的误读。
3. **情感色盲可辨**（堆叠柱加斜纹 / 段内标百分比）：图表层改动，涉及 4 张图与 `CP.PALETTE`，且与"红＝负面"的 pin 冲突，需单独授权。
4. **深色侧栏焦点环**（`#3358E8` on `#111B30` ≈2.5:1）：属已 pin 的品牌色用法，改动要动深色令牌，留给下一轮。
5. **首页 IA 大改**（把 5 张 KPI 换成"待办 / 超期 / 中高风险"、AI 简述上收）：本轮只加了三件事条，没动 KPI 与 AI 卡的位置——那是产品决策，不是缺陷修复。
6. **证据页批量**（全选本页、按负面/风险排序、批量改状态）：交互增强，与"一次跑完修缺陷"不同性质。

## 第 19 轮同步（2026-11-16，critique 复评 → 一次修完 → 再评）

第二轮 critique（隔离设计评审 + 检测器/浏览器取证并行）给出 **27/40**，比第 18 轮的 31 分低——不是退步，是这轮审得更狠，且抓到一个真 bug。用户要求"不再留到下一轮"，所以这份清单里的每一条都在这轮落地。

### 0. 评审抓到的真 bug（先说这个）

`dashboard.js` 输出 `class="dpt-score lv-${LEVEL_CLS[t.level]}"`，而 CSS 写的是 `.dpt-score.high/.midhigh/.mid/.low`。**`lv-` 前缀让选择器永不匹配**，57 / 40 / 38 三行全是同一种黑色——第 18 轮我自己写的"同一档不许在两页两种颜色"当场被破坏，而验收只看"类名在不在"，没看计算值。

现在 `class="dpt-score ${LEVEL_CLS[...]}"`，并补两件事：
- **等级文字**（`.dpt-lv`，`中高 / 中 / 高 / 低`）与分数同色：只靠颜色传达严重度，对红绿色盲等于没传达；
- 分数从 20px 提到 **26px**（与 KPI 同级：产品自己的数字不该比通用 KPI 小），行内补 `aria-label="风险指数 57 分 · 中高 · 宿舍住宿 · 后勤保障处 · 已超 1.9 个工作日"`。
- `verify_ui.py` 场景 K 现在断言 `getComputedStyle(.dpt-score).color === .dpt-lv.color`、颜色 ≠ 正文色、`aria-label` 含"风险指数"与等级。

### 1. 「今天要办」名不副实 + 三个时间基准

标题改成「**最该先办的三件事**」，副标题写明「按风险指数取前 3 条，**不按到期日**」。截止不再写 `11-24 截止`（读者会问"今天为什么是 11-24"），改成「**已超 1.9 个工作日 / 还剩 6.9 个工作日**」：

- 数值由后端算（`departments.assess` 新增 `remaining_work_hours` + `remaining_text`），用同一本工作日历、同一个格式化器（`tickets._human_minutes`）。前端不再拿日期减今天——它没有节假日表，算出来会和工单页对不上。
- `work_minutes_between` 是**有方向的**：先判前后再反向计算，否则"已超"永远读成 0（本轮实测到的第二个 bug）。

### 2. 一个量只给一个单位（`_human_minutes`）

原来是「1 个工作日 6 工作小时」——两个单位粘在一起，86px 的列表格里还会被折成两行当成两个数。现在按量级选一档，**永不并存**：

| 量级 | 写法 |
|---|---|
| < 1 工作小时 | `34 分钟` |
| < 1 工作日 | `6.4 工作小时` |
| ≥ 1 工作日 | `1.9 个工作日` |

`slLines()` 随之退化成"一行"（旧正则会把 `1.9` 切成 `1.` 丢掉小数）。工单页 `超期 9.1 个工作日 · 11-02 截止` 现在是**一行**。

### 3. 工单页首屏：先给要办的事（P1）

- **规则未命中时桌面也收起**（原来只在 390 收）：桌面留着 215px 的"本次未命中 + 为什么不报警"说明书。摘要行改为「本次未命中 · 评估锚点 2026-07-07 · 展开看判定阈值（不会为了显得智能而报警）」，一句话说完。
- **动作条吸底**（`.tk-block-acts`，≥721）：详情有 2000px 高，900px 视口下「开始处理」原来在 y≈887、只剩 13px 露在折叠线外。sticky 挂在整块（父级 `#tkDetail` 够高才有行程），不是按钮本身。
- **KPI 用紧凑档**（≥721）：`.tk-layout > #tkKpi .kpi` 收到 11px 内边距、值 20px，`#tkKpi` 与 `.tk-filters` 的下边距各收 4px。第一张工单从 y=733 提到 **y≈696**（900 视口内标题、状态、时限全可见）。口径脚注一个字没删。

### 4. 今天条的三态（P1）

模板里先摆**三行骨架**（`.dp-today-skel`，与终态同高），JS 成功后替换、失败后换成 `.note note-warn` + 「重试」按钮、仅 403/401 才 `is-hidden`。第 18 轮实测"300ms 内从无到有把 KPI 顶下去 283px"，且失败与"今天没事"长得一样——两个问题一起解决。容器挂 `aria-busy`。

### 5. 顶栏角标按 scope 取数（P2）

`/api/tickets/badge` 新增 `scope`（`mine / dept / all`）。前端按 scope 取 `mine` 或 `pending`：管理员原来 `mine=0` 时整枚角标消失，**3 张超期单在顶栏上一个提示都没有**。现在显示「5 未结」，`aria-label` 动态写成「5 单未结工单，超期 3 单」，不再硬编码"待处置工单"。

### 6. Agent 空态右栏（P2）

空态右栏原来只有一句标题 + 一条折叠说明（1280 下右半屏约 40% 是白）。新增 `.ans-shape` 四格「结论一句话 / 支撑数字 / 原文证据 / 下一步」，讲清"会得到什么形状的答案"。**不给示例数字**——没有真实来源的数字不得上屏（PRODUCT.md）。工具卡与安全说明块同时去套娃（见 §8）。

### 7. 证据页：全选本页（P1，Alex）

1,969 条结果里逐条点 13px 复选框不现实，导出又只能从选中集合取。新增「全选本页」：只作用于当前页（不跨页累积），按钮同时报告状态（`全选本页 ⇄ 取消全选`），再点一次取消；批量条同步「已选 N 条」。理由：这个场景要导出的是"这几条拿去核对"，不是 1,969 行。

### 8. nested-cards 归零（P1，检测器唯一的结构性问题）

检测器的判定是"边框 + 圆角 + 不透明底色"三件套。第 18 轮我把 4 条登记为"不拆"，这轮全部拆掉：

| 位置 | 改法 |
|---|---|
| 驾驶舱 `#toneHint` / `#platTable` 警示块 | `.note-flat`：去底、去圆角，改 **1px** 左侧语义线（craft-floor 允许 ≤1px），颜色仍由 `currentColor` 承担警示 |
| `/insights` 可比性说明 | 同上 |
| `/agent` 工具卡 | `.tool-grid .tool-card`：去边框与实底，只留条目分隔线 |
| `/agent` 安全说明 `.note` | `.note-flat` |
| `/data` 定义说明 | `.note-flat` |
| `/data/collect` 登录态面板 | 内联 `border+radius+bg` → `.login-card`（无边框，靠底色分层） |
| `/agent` 聊天气泡 `.msg.bot` | `border: 0`（气泡靠底色与圆角，再加边框就是卡中卡） |

实测：`/`、`/agent`、`/data`、`/data/collect`、`/departments`、`/tickets`、`/evidence` 的 `nested-cards` 全部 **0**；`/insights` 也降到 0。URL 扫描现在每页只剩 2 条 `dark-glow`（`.brand-mark` 真实命中 + `body` 幽灵误判，均已登记），`/tickets` 多 1 条已 pin 的 `pulsing-dot`。

### 9. 删掉"需要自己解释自己"的装饰（P1）

- **KPI 顶部彩条**（`.kpi::before`）与那条「彩条表示指标类别，不是情感编码」的说明一起删。彩条长度固定 34px，没有任何可比基准，等于纯装饰 + 一句辩护。三页（驾驶舱 / 研判 / 工单）同时生效。
- **工单页三处声明合一**：「处置记录适用范围」折叠块并进页脚那条常驻口径行，页面上从三处降到两处（页面一条 + 详情卡一条后端下发的免责）。

### 10. 色盲可辨：负面段加第二编码

红与绿在红绿色盲眼里亮度接近，堆叠条只看颜色分不出哪段是负面。给**负面**段加 45° 细斜纹（正面/中性不加大面积纹理，图例会变成花布）：

- 图表（canvas）：`CP.NEG_DECAL`，作用于驾驶舱趋势、平台对比、洞察两处堆叠柱；
- HTML 情感条与图例点：`.sent-seg.is-neg { background-image: url("data:image/svg+xml…") }`。**不用 `repeating-linear-gradient`**——检测器把重复条纹读成装饰，这里它是语义；SVG 图案既避开误判，也保住了形状编码。

### 11. 其他

- 周带标题：`本周要办结的事` → `本周与下周要办结的事`（带宽实际铺两行，只写"本周"会让下周三的截止日看起来像写错了日期）。
- 深色侧栏焦点环：`.sidebar :focus-visible { outline-color: #8CA2FF }`（`--brand #3358E8` 压在 `#111B30` 上只有 ≈2.5:1，低于非文本 3:1）。
- 详情空态文案与块序同步（「三把尺子 → 可执行操作 → 建单口径 → 代表证据 → 时间线」）。

### 新增 / 变更组件

| 选择器 / API | 形态与约束 |
|---|---|
| `.dpt-score` / `.dpt-lv` | 26px 分数 + 等级文字，**两处必须同色**；类名直接取 `LEVEL_CLS` 的值，不要再加前缀 |
| `.dp-today-skel` | 三行骨架，与终态同高（CLS 的根因是"先无后有"） |
| `.tc-lim` | 「已用 X / 时限 Y 工作小时」小字；两张尺子的主数字不同时，可复算的那对放这里 |
| `.tk-block-acts` | 动作条吸底（≥721），`background: var(--surface)` + 上缘投影 |
| `.note-flat` | 去套娃用的提示块：无底、无圆角、1px 左侧语义线 |
| `.login-card` / `.tool-grid .tool-card` | 采集页登录面板 / Agent 工具清单：底色分层 + 分隔线，不构成卡片 |
| `.ans-shape` | Agent 空态的四格"答案形状"，不含任何数字 |
| `CP.NEG_DECAL` | 负面段 45° 斜纹（canvas）。HTML 侧用 `.is-neg` 的 SVG 图案 |
| `departments.assess` 新字段 | `remaining_work_hours`（可负）+ `remaining_text`（单一单位文案，与工单页同源） |
| `/api/tickets/badge` 新字段 | `scope`：`mine`（处置员）/ `dept` / `all` |

### 本轮实测

- `tools/verify_ui.py` **55/55**（新增场景 K 六条：等级颜色与文字 / 单一单位 / 工单页首屏与吸底 / 角标 scope / 全选本页 / 负面段纹理）。
- `tests/acceptance.py` **78/78**（含 `tsc --noEmit` 0 条；`CP.NEG_DECAL` 已补进 `cp.d.ts`）。
- 12 路由 × 1280/390：0 溢出、0 console error、0 pageerror。
- 检测器：文件扫描 17 条（16 条 Jinja 假阳性 + 1 条 `login.html` 同因）；URL 扫描 **每页 2 条**（`dark-glow`，已登记）×8 页 + `/tickets` 1 条 `pulsing-dot`（已 pin）；**`nested-cards` 与 `repeating-stripes-gradient` 均为 0**。
- 关键位置：驾驶舱三件事 y=263、KPI y=570；工单页第一张单 y≈696（900 视口）；动作条吸底、详情滚动时始终可见；证据页「全选本页」一次勾 20 条、按钮变「取消全选」。

### 仍未做（附理由，不再默认欠着）

1. **「处置记录适用范围」这类合规声明不再单独成块**，已并进页脚常驻口径；但详情卡底部的后端免责仍保留——那是每张单的数据，不是页面文案。
2. **洞察三页 AI 卡的「当前范围 / 生成于 / 数字由统计接口给出」四行没有折叠**：它与"讲口径的说明必须可见"的产品原则冲突，取口径。
3. **删除工单仍在详情头部**：它需要"随时可找到"（处置记录不留隐藏入口），靠二次确认 + 必填原因约束；吸底动作条已经把主操作提到首屏，两个动作不再抢同一位置。
4. **Agent 左栏仍 396px 固定**：拉宽会牺牲右栏的图表与证据，收益不明显，未改。

## 第 20 轮同步（2026-11-16，第二轮复评的 5 条）

第二轮复评 27/40（与第一轮同分，但失分位置换了：不再是"缺什么"，而是"同一个数在两页代表两个东西"）。这轮把 5 条全部改掉。

### 一、等级色只表示等级，状态只表示状态

复评实测：57 在首页与风险表是橙 `rgb(181,71,8)`，在周带「已超期」横条里被涂成红 `rgb(180,35,24)`；40/38 在首页是品牌蓝、在周带是墨黑。根因是 `.wk-strip.is-overdue .wk-pin b { color: var(--neg-ink) }` 直接覆盖了分数颜色。

现在立一条硬规则并写进 token 用法：

> **分数只表示等级（高/中高/中/低）；状态只用底色、边框、横条标题、`.dpt-due` 的文字色。**

- 删掉 `.wk-strip.is-overdue .wk-pin b` 的颜色覆盖（保留边框与淡红底）。
- 「中」档统一为 `--ink`（品牌蓝留给可交互/选中）：`.dpt-score.mid`、`.dpt-lv.mid`、`.dp-risk-num.mid` 三处同时改，首页 / 风险表 / 周带读同一种深色。
- 周带 pin 补等级文字 `.wk-lv`（`中高 / 中 / 低`），`aria-label` 同步 `风险 57 分（中高）`——周带是全站唯一没有等级文字的地方，而它恰好是色盲用户最依赖位置。
- `verify_ui.py` 新增断言：同一个主题在首页与周带的 `getComputedStyle(color)` 必须相等，且所有 pin 都有等级文字。

### 二、「最该先办」改为先看到期、再看风险

复评指出：食堂餐饮风险 9 分、`还剩 50 分钟`，按风险排第 8，首页给的是 6.9 个工作日后的主题——标题说"最该先办"，内容说"下周再说"。

排序键改为 `remaining_work_hours` 升序（已超为负，排最前），同值再比风险。副标题同步：「按『还剩多久』排：已超的在前，其次快到期的，同一时刻比风险指数」；脚注补「时钟从今天起算」。

实测首屏三行变成：`宿舍住宿 中高 已超 1.9 个工作日` → `食堂餐饮 低 还剩 50 分钟` → `招生报考 中 还剩 3.4 个工作日`。第二行带 `.dpt-due.is-soon`（警示色），第一行 `.is-over`（红）。

### 三、「已超期」两个口径各自说清单位

研判页数的是**主题**（1 个主题存在超期工单），工单页数的是**张数**（3 张单）。两个数并排时读者只会认为数据不一致。现在：

- 研判页 chip：`aria-label="看 1 个主题存在超期工单"`，`title="按主题数计（工单张数见「处置工单」）"`；
- 工单页 KPI 脚注：「按工单张数 · 工作时间口径判定，不计周末与午休」。

### 四、工单页：KPI 移到列表之后

复评第二次指出首屏被"说明书 + 5 张 KPI"占掉（第一张单 y=696）。这轮把 `#tkKpi` 整组 `order: 3` 排到列表与详情之后（桌面与手机一致，390 的"孤儿 KPI"问题一并消失）：

- 第一张工单从 y=696 提到 **y=544**（900 视口下能看两行）；
- KPI 变成这一页的"总量回顾"，不再挡在"要做的事"前面；
- 顶栏角标仍随时给出「未结 5」。

### 五、吸底动作条真的吸住 + 焦点框不再像坏了

`bottom: 0` 的 sticky 只对"还没滚到"的元素有效，滚过之后照样被推走（复评实测 `scrollY=900` 时 `top=-106`）。改成 `top: 62px`，并把动作块压成一行（标题 + 按钮右对齐），读证据与时间线时「开始处理」始终钉在顶栏下。

390 下程序化聚焦 `.card-title` 会画出 UA 的黑色 `auto` 外框，看起来像页面坏了。加 `[tabindex="-1"]:focus:not(:focus-visible) { outline: none }`：键盘 Tab 到它时仍有焦点环，鼠标点开时不画框。

### 本轮实测

- `tools/verify_ui.py` **56/56**（场景 K 增至 7 条：等级色与文字 / 跨页同色 / 单一单位 / 首屏与吸底 / 角标 scope / 全选本页 / 负面段纹理）。
- `tests/acceptance.py` **78/78**（含 `tsc --noEmit` 0 条）。
- 12 路由 × 1280/390：0 溢出、0 console error、0 pageerror。
- 检测器（URL 扫描）：`/` `/departments` `/agent` `/insights` 各 2 条、`/tickets` 3 条——全部是已登记的 `dark-glow`（2 条/页，含 `body` 幽灵误判）与 1 条已 pin 的 `pulsing-dot`；`nested-cards` 全 0。
- 关键位置：工单页第一张单 y=544、KPI y=1982；`scrollY=900` 时动作条 `top=62` 仍在视口内。

### 仍未做（如实记录）

1. **一个主题挂多张单时只显示一张**（研判页取较新的那张，最旧的 9.1 天单不出现）。要修需要 `/api/departments/risk` 回 `ticket_count` 与 `oldest_due`，并在行内写「2 张单 · 最早已超 9.1 天」。这是数据契约变更，留到下一版接口一起改，不在 CSS/JS 层糊。
2. **洞察三页 AI 卡的四行口径没有折叠**——与"讲口径的说明必须可见"冲突，取口径。
3. **删除工单仍在详情标题右侧**：需要"随时可找到"+ 二次确认 + 必填原因；动作条已把主操作提到首屏，两者不再抢同一位置。
4. **Agent 左栏 396px 固定**：拉宽会牺牲右栏图表，收益不明显。

## 第 21 轮同步（2026-11-16，第三轮复评的 6 条）

第三轮复评仍给 27/40，但抓到一个**硬约束违反**和两处"我上一轮修出来的新故障"。这轮全部改掉。

### 一、P0：智能体图表把「负面」画成青绿

`CP.renderSpec()` 一律按 series 下标取 `PALETTE`：`分平台情感占比` 的第二条是「负面占比」，却拿到 `PALETTE[1] = #14B8A6` 青绿——同一屏的环形图写着红＝负面。这违反已 pin 的情感色，属于"立场不可信"级别的错误。

改成**先认语义名，再回退序号**：系列名以「正面 / 中性 / 负面」开头时取 `SENTIMENT_COLOR`，其余仍走 `PALETTE`。`lineStyle` 与 `itemStyle` 两处同时改（折线与柱都算）。平台对比不再复用情感色。

### 二、吸顶动作条：跟着顶栏真实高度走

上一轮写死 `top: 62px`，而 1280 下 `.topbar` 有 **140px**（标题 + 副标题 + 控件两行），按钮整块滑进顶栏底下（z-index 15 vs 3），`elementFromPoint` 命中的是顶栏——等于"最需要的时候点不到"。手机端更彻底不吸，因为规则写在了 `min-width: 721px` 里。

- `core.js: watchTopbarHeight()` 把 `.topbar` 实际高度写进 `--topbar-h`（load / resize / 字体就绪三个时机）；
- CSS 用 `top: calc(var(--topbar-h, 96px) + 8px)`，`z-index: 4`，并去掉 721 的限制，手机同样吸（换行排布）；
- 实测 `scrollY=900`：动作条 `top=148`（140+8），命中测试落在动作条自身。

### 三、代表单取「最早到期」，并把依据写在行上

`_attach_ticket_codes` 原来按建单顺序取第一张，同一主题两张单时会把"已欠 9.1 天"的旧单藏起来。改为 `ORDER BY sla_due ASC`（空值排最后）。首页行的 `aria-label` / `title` 追加依据：「按工单截止」或「按部门时限推算」——这样两页时间不一致时，读者看到的是两条不同的依据，而不是两个矛盾的数字。

### 四、KPI 回到列表之上（紧凑档）

第 20 轮把 `#tkKpi` 整组排到页底，结果「超期 3 单」被推到 2000px 之后、桌面还留下近千像素空白——修掉一个问题，制造了两个。这轮折中：桌面 `order: -1` 回到列表之上（紧凑档 ≈126px，第一张单 y≈696），手机仍排在列表之后。

### 五、代表证据限定在研判窗口内

研判说的是 `2026-06-07~07-07`，证据却是 2023 年的两条——"每条都能回溯到原文"的追溯链当场断掉。`_samples()` 增加 `date_from/date_to`，按研判窗口（实时＝锚点往前 30 天；区间＝调用方给的起止）取负面样本；**窗口内一条都没有时**才回落全量，并在区块标题写明「窗口内没有负面样本，以下为『全量最高赞』，不属于本次统计」。两条路都留，是因为"窗口内 9 条负面却一条证据都不给"同样不可信。

### 六、两处状态回填

- 骨架态副标题还写着「按风险指数取前 3 条，不按到期日」——与终态排序（到期优先）矛盾，首屏会闪一句反话。改为「按『还剩多久』排：已超的在前、其次快到期的」。
- 390 的周带是另一套 DOM（`.wk-list` 分组），桌面 `.wk-strip.is-overdue` 的红底没有继承过来，手机上「已超期」只剩一个分组标题。补 `.wk-group.is-overdue`。

### 本轮实测

- `tools/verify_ui.py` **56/56**；`tests/acceptance.py` **78/78**（含 `tsc --noEmit` 0 条）。
- 12 路由 × 1280/390：0 溢出、0 console error、0 pageerror。
- 检测器（URL 扫描）：`/` `/agent` `/departments` 各 2 条、`/tickets` 3 条——全是已登记的 `dark-glow` 与 1 条已 pin 的 `pulsing-dot`；`nested-cards` 全 **0**。
- 关键实测：动作条 `top=148` 且命中测试通过；首页三行依序为「宿舍住宿 已超 1.9 个工作日」「食堂餐饮 还剩 50 分钟」「招生报考 还剩 3.4 个工作日（按部门时限推算）」；代表证据日期回到 2026-06/07（与锚点同窗口）。

### 仍未做（如实记录）

1. **首页 5 张 KPI 仍是「有效评论 / 正面占比 / 负面占比 / 讨论最多主题 / 总点赞量」**：其中两张正是产品主张要否定的口径。要换掉它们需要一次产品决策（换成"风险等级分布 / 未建单数 / 最该先办"），不是缺陷修复。
2. **风险指数等级阈值（30 / 50 / 70）全站没有一处写明**：`_level()` 里是硬编码分档，页面只有"高/中高/中/低"四个字。要写就得先决定这四个档次是"解释"还是"承诺"。
3. **洞察入口页三张卡与左侧导航子项重复**：属信息架构，与"六个一级入口"的产品定位绑定。
4. **`.skip-link` 聚焦后仍在视口外**、`.nav-caret` 的可访问名为空：两处小的无障碍缺陷，未在本轮处理。

## 第 22 轮同步（2026-11-16，第三轮复评的 4 条）

第三轮复评给出 **33/40**（27 → 33）。剩下的 4 条这轮修掉，其中两条是"上一轮的修复没落地"。

### 一、动作条吸顶：这次真的两端都吸

复评实测：390 下 `position: static`、`top=771`——因为整块规则被我写在了 `@media (min-width: 721px)` 里，手机上从未生效。桌面虽然吸住，但 `--topbar-h` 在 `/`、`/evidence`、`/agent` 读到 86px（实际 140），sticky 落点算错。

- 规则移出媒体查询，桌面与手机同一套；≤720 只调换行与 `top` 偏移。
- `--topbar-h` 改用 **ResizeObserver** 观察 `.topbar`，另在 600ms / 2000ms 各补一次（徽标异步出现会改高度）。
- 实测：1280 `--topbar-h=140px`、390 `=168px`；390 滚到 2000 时动作条 `top=172`、命中测试落在自身。

### 二、390 详情前置

原来 390 的顺序是 列表 → KPI → 详情，处置员点开一条单要先滑过 5 张统计卡（实测详情在 y=2050）。改为 **列表 → 详情 → KPI**，详情提到 y=1554，动作条就在详情顶部。

### 三、列表徽标写清是哪个钟

「超期 9.1 个工作日 · 11-02 截止」里，9.1 来自**归档**时限、11-02 是**归档**截止日，但同屏详情的 66.6 小时是**首次响应**——同一张单两个"超期"，读者以为对不上。徽标改成「超期 9.1 个工作日 · **归档** 11-02 截止」，与详情第一格（首次响应）明确区分。

### 四、智能体的承诺与行为对齐

空态原来写「每条结论都会附图表与评论原文」，而聚合统计类回答（"正面多少条"）本身没有原文可锚，答完还会亮一个橙色「结论未标注原文锚点」——两句话在同一屏互相打脸。承诺改成「数字附图表与分母；**涉及具体评论时会带原文编号**」，橙色核验徽标保留（它是真话，不能因为难看就藏起来）。

### 本轮实测

- `tools/verify_ui.py` **56/56**；`tests/acceptance.py` **78/78**（`tsc --noEmit` 0 条）。
- 12 路由 × 1280/390：0 溢出、0 console error、0 pageerror。
- 关键位置：1280 首单 y=696、KPI y=325、详情 y=477；390 首单 y=668、详情 y=1554、KPI y=3580（最后）；两端动作条 `position: sticky` 且命中测试通过。

### 仍未做（如实记录）

1. **聚合统计类回答不附原文锚点**：可以靠"统计意图强制检索 1 条正/负样本"补上，但那会让"正面 350 条"这种回答被两条个案绑架，需要产品先决定要不要。目前用文案把承诺降到"涉及具体评论才附原文"。
2. **`.chat-panel` 的 sticky 不生效**（`/agent` 滚动时对话头随内容滚走）：`.agent-shell` 是 `align-items: start`，栅格项只有自身高度、没有吸附行程；要修得给聊天列加一层包裹元素。
3. **390 的 KPI 排在页底**：与"总量回顾"的定位一致，但如果值班员在手机上也想知道"还超期几单"，需要顶栏角标之外再给一个入口。
4. **风险指数等级阈值（30/50/70）仍未在页面写明**；**洞察入口页与侧栏重复**；**`.skip-link` 焦点仍在视口外**：三条都需要产品/信息架构决策，不是单点修复。

## 第 23 轮同步（2026-11-16，AI 简述正文不再缩在左边）

### 现象

驾驶舱「AI 舆情简述」的正文被 `max-width: 74ch` 收成 638px 的一列，而卡片内宽是 918（1280）/ 1078（1440）——右侧空出三分之一，读起来像"没排完"；下方那条满宽的「当前范围 / 数据集 / 生成时间」口径行反而铺满，同一张卡里两种宽度叠在一起，更像排版事故。

### 改动

`#aiInsight` 的 `max-width: 74ch` → `min(100%, 75rem)`（只在 `#aiInsight` 上，`.ai-summary` 全站仅驾驶舱一处使用）。

- ≤1200 视口 `min()` 取 100%，正文用满卡片：1082 屏 900px、1280 屏 918px、1440 屏 1078px，行数 6 → 4 / 3。
- 超宽屏由 1200px 兜底：1920 屏卡片内宽 1576px，不设上限会变成 90+ 字/行，1.8 行高也救不回来。1920 实测正文 1200px，右侧留 376px 的 gutter，是有意的读数上限，不是没排完。
- `1.8` 行高与 `min-height: 96px` 未动，仍保留左侧 2px 品牌竖线（"这段是模型生成"的唯一标记）。

### 试过但没采用

- **两栏**（`columns: 2`，只在 ≥1700 生效）：1920 实测左栏 3 行、右栏 2 行，且断点落在"费用负担以28条居首"这种句子中间，读者要跨栏接句。段落短时（降级文案只有一两句）会拆成一栏一句，更难看。
- **整段放到 18px（`--fs-lg`）**：字号跟着 `ch` 一起变，宽度仍不足；且结论段已在 `.card-title` 同字号下与标题打架。

### 本轮实测

- `tools/verify_ui.py` **56/56**（`CP_AUTH=0 CP_TICKETS=1`，8031 端口临时库）。
- 1082 / 1100 / 1280 / 1440 / 1920 / 390 六档：`scrollWidth - clientWidth` 全为 0，无横向溢出；390 正文 332px、11 行，与改动前一致（`min()` 在窄屏取 100%）。
- 纯 CSS 一处改动，未动 DOM 与 JS。

## 第 24 轮同步（2026-11-16，`ds_hist_all` 的时间窗口收敛到 2022-01 起）

### 现象

`ds_hist_all` 从 2019-09 起算，名字叫「2019-2026」。但它 2022 年之前的有效评论只有 9 条（2019 年 4 条、2021 年 5 条）——82 个月的趋势图里前三分之一是 0 和 1，读者第一反应是"数据没加载全"，而不是"那几年本来就没采到东西"。数据集名里的 2019 承诺了一个它撑不起来的覆盖范围。

### 改动

1. **时间窗口变成数据集定义的一部分**：`etl.normalize_records/normalize_groups/build_dataset` 新增 `date_from` / `date_to`，在**读取之后、去重之前**生效——窗口外的记录不进质量链路，`raw_count` 也只统计窗口内。新增一档「时间范围」清洗阶段，把剔除量显式写出来，否则"读取 17,157 条"和"原始 23,724 条"之间的差额没人能对上。
2. **窗口只认评论发生时间**，不是采集时间；解析不出时间的记录**放行**，交给「时间解析」那一步计数——否则同一批文件只因为导入了哪一段，无效时间的条数就跟着变。
3. 窗口写在 `config.PRESET_DATASETS[].date_from`。`bootstrap_presets` 与 `/api/datasets/<id>/reanalyze` 都读同一个字段：漏传一处，「重新分析」就会把 2 年前的评论灌回来，而名字还写着 2022-2026。
4. 重建数据集（走 `etl.build_dataset`，不是 SQL 硬删）：`25,699 → 23,724`、`1,969 → 1,960`、有效率 `7.66% → 8.26%`、覆盖 `2022-01-13 ~ 2026-07-07`，平台 抖音 1,014 / 小红书 946。去重数同时从 11,437 降到 9,905，因为 2022 年前的重复对被一起截掉了——这是窗口前移的正常结果，不是丢数据。
5. 种子工单 `TK-20261111-0003` 有 2 条证据指向已剔除的评论（`CMT-3F43E9173C` / `CMT-D5A1D44485`），按"删主体先清链接"的老规矩删掉链接行，剩下 3 条证据仍可点开。

### 口径怎么讲

**25,699 / 1,823 仍然成立**，它们是完整批次口径，`tests/legacy_check.py` 一字未改；只是展示层不再包含 2022 年前的内容。`docs/口径说明.md` 加了一段「时间窗口收敛」，README 数据集表、`商业与社会价值.md` 的月度分布表（1,803 → 1,960 重算：6–8 月占 64.5%、负面率 5.5%，负面率最高变成 11 月 9.1% / 44 条与 9 月 8.1%）都按新窗口改过。

### 本轮实测

- 接口：`/api/datasets` 四个数据集名称与区间互不相同；`/api/analytics/trend?granularity=year` 首桶 `2022-01`、末桶 `2026-07`、合计 1,960、空桶 11；`/api/dashboard/summary` 分母 1,960；`/api/datasets/ds_hist_all/quality` 有效率 8.26%、`platform_raw` 15,260 / 8,464。
- 库内：`2022-01` 之前残留 0 行；`kept` 1,960；悬空证据链接 0 条。
- 缓存：`_data_stamp` 是 `(data_version, kept)`，两样都变了，词云/极性/报告缓存自动失效，**不需要重启服务**（实测运行中的 8000 端口直接返回新数字）。

## 第 25 轮同步（2026-11-16，顶栏恢复单行）

### 现象

1280 投影实测 `.topbar` 高 145px：标题在 y=15，控件组整排落到 y=88。原因不是控件太多，是 `.ds-hint` 与下拉并排——右侧控件组宽 803px，加上标题块 353px 与 18px 间距共 1174px，超过顶栏可用宽度 964px，`flex-wrap` 只能把整组换到下一行。长副标题页更差：`/insights`、`/departments` 的副标题 max-content 546px，控件组落到 y=109，顶栏 182px；`/evidence`、`/agent` 161px。

### 改动

1. **数据集收成一个组**：`base.html` 把「数据集」标签、`#dsSelect`、`#dsSelectHint` 包进 `.ds-group`，标签与下拉一行，说明在下拉正下方。`id`、`aria-describedby` 未动，说明仍常驻可见（口径说明必须可见的 pin 不变）。右侧控件组 803 → 573px。
2. **标题块按剩余空间定宽**：`.topbar > div:not(.topbar-right) { flex: 1 1 0; min-width: 0; }`。`flex-wrap` 先换行再收缩，长副标题页此前仍会把控件组挤下去；基数设 0 后副标题自己折行，控件始终与标题同行。
3. **≤720 不动**：093 的绝对定位仍在——说明贴顶栏底部独立一行，抽屉与控件行位置不变。

### 本轮实测

- 1280：`/` 顶栏 88px（原 145），首屏内容 y=169 → 112；`/insights`、`/departments` 182 → 107；`/evidence`、`/agent` 161 → 107。1440 同构：`/` 88、长副标题页 107。
- 1440 / 1280 / 1024 / 390 四档、四条路由：横向溢出 0；`.ds-hint` 均可见（桌面 253×18 在下拉下方，390 为 366×18 贴底）。
- 390 顶栏 168px 与改动前一致；抽屉开合、主题切换、「更多」菜单、`#dsSelect` 的 `aria-describedby="dsSelectHint"` 均正常。
- 纯 HTML/CSS 改动，未动 JS 与字号档位。

## 第 26 轮同步（2026-11-16，最终 Web 质量审计）

### 范围与工具

- 桌面 1280×800 是交付目标（传智杯演示/投影）；390 只做不回归抽查，不作为本轮优化目标。
- `web-quality-audit` 负责性能/无障碍证据，`impeccable critique` 负责设计评审。
- 评审为**单上下文降级版**：子代理工具不可用；Detector 因网络无法下载引擎，未运行。评分与结论以 Playwright 实测、截图与代码证据为准。

### 本轮改动

1. **首页 LCP**：「最该先办的三件事」的卡片头原先由 `dashboard.js` 在约 2.6 秒时整段重写，最宽的一行说明被重新绘制成新的 LCP 候选。现在头部由服务端一次渲染，JS 只替换 `#todayBody`。1280 LCP **2612 → 424ms**，CLS 0.004。
2. **CLS 占位**：`#filterBar`、`#ticketStrip`、`.dp-today`、`#riskCard`、`#rankList`、`#scopeKv`、`#dsTable`、`#qualityBody` 按终态高度预留；骨架被真实内容替换后 `:has(.skeleton)` 自动失效，不需要 JS 清理。1280 证据页 CLS **0.101 → 0.004**，数据页 0.09 → 0；390 首页 0.219 → 0.005（仅抽查）。
3. **顶栏宽度**：待办角标由模板按权限先渲染占位（数字后填，桌面 `min-width: 90px`），避免长标题页在数据回来时 88 → 107px 再换一行；`#dsSelect` 在 >1100 / 721–1100 分别先占 178 / 148px。
4. **安全响应头**：新增 `X-Content-Type-Options: nosniff`、`Referrer-Policy: same-origin`、`X-Frame-Options: DENY`。未加 CSP（页面有内联主题脚本与 ECharts），未改 wildcard CORS。
5. **横滑提示**：从表尾移到表前并服务端占位，长表在手机上先看到「左右滑动查看更多列」；≤720 的待办角标、用户头像补到 44px 触控。移动端只做不回归，不再扩展。
6. **证据页**：风险卡与排行/检索口径按终态高度占位，消除排行数据到达时的二次下推。

### 本轮实测（1280×800，`CP_AUTH=1`，analyst01）

| 页面 | LCP | CLS | TBT | 传输 |
|---|---:|---:|---:|---:|
| `/` | 424ms | 0.004 | 26ms | 454KB |
| `/insights` | 3504ms | 0.010 | 3ms | 451KB |
| `/evidence` | 112ms | 0.004 | 6ms | 121KB |
| `/data` | 184ms | 0 | 4ms | 109KB |
| `/agent` | 112ms | 0.006 | 1ms | 449KB |
| `/departments` | 204ms | 0 | 2ms | 163KB |

- 六页横向溢出 0、console/HTTP 错误 0；一页一个 h1、无重复 id；抽样对比度全部 ≥ 4.5:1。
- `/insights` 的 LCP 是 DeepSeek 返回的 AI 解读（页面导航与图表 FCP 104–144ms）。这是外部模型时延，未用缩小字号或隐藏文本的方式掩盖；现场讲解时先讲入口卡与筛选，等徽标从「生成中」变成「DeepSeek 生成」。

### 没改与边界

- 9 档字号、周带两行、红负绿正、44px 触控等 pin 未动。
- CORS 仍为 wildcard：应用同源使用且未开 credentials，改动超出页面范围。
- `static/vendor/tailwind.js` 未被当前模板引用，未删除（旧 `app.py` 可能还在用）。
- 尚未提交 git；本轮只改页面、静态资源与 `web.py` 的响应头/模板上下文。

## 第 27 轮同步（2026-11-17，登录页精修）

第 20 轮把登录页收成单列卡片后，本轮只做视觉精修，方向仍是「简单点」：没有恢复左右分栏、英文 kicker、42px 网格或品牌光圈。

- **品牌区**：`.login-logo` 直接使用共享 `.brand-mark`（40px，不覆盖尺寸/圆角），下方补 `CampusPulse / 高校舆情智能洞察平台` 两行；脉冲线用 `stroke-dashoffset` 画一次（`login-pulse`，`prefers-reduced-motion` 关闭）。
- **卡片**：420px，`var(--radius)` / `var(--shadow)`；背景改为 `--surface-2 → --bg → --surface-sunken` 的中性纵向渐层（不是浅色 radial glow，不违反第 12 轮的减法）；页脚加 `--line-2` 分隔线，版本号 `tabular-nums`。
- **表单**：输入框 48px / `--radius-sm`，补 `caret-color`、autofill 主题化与复选框 `:focus-visible` 环。
- **按钮**：浅色沿用系统 `.btn-primary` 的渐变；深色改为 `#2559C4 / #1E4CAB`（与 `[data-theme="dark"] .btn-primary` 同值，此前登录页漏了深色覆盖）；禁用态改显式 `--surface-sunken` + `--ink-4`，不使用透明度。
- **演示态**：原 `.hint` 段落改为 `.login-demo` 品牌软底状态条（7px 圆点 + 一句话），文案未改；dev 提示保留。
- **契约未动**：`auth.js` 的 `#loginForm` / `#username` / `#password` / `#remember` / `#loginBtn` / `#loginErr` / `#nextPath` 全部保留；九档字号、品牌色、红负绿正、深色主题与 `.brand-mark` 辉光 pin 未动。
- **范围边界（2026-11-17 用户声明）**：本项目不做移动端，后续修改不再新增移动端样式；本轮已撤掉登录页新增的 ≤480 适配，历史移动端规则保持原样、不再扩展。

**本轮实测**：Playwright 26/26（1280×800 桌面交付面；浅 / 深、演示 / 开发、空提交 / 错误密码 / 正确登录、键盘焦点、reduced-motion、无横向溢出）；检测器对两个改动文件 13 条 advisory（1 条 Jinja 假阳性、1 条既有等宽族、9 条沿用系统按钮渐变、2 条深色投影），无新增结构性命中。验证走 8123 / 8124 临时端口，未写演示库。

## 第 28 轮同步（2026-11-17，登录页背景节点图）

用户指定从 GitHub 选一个 UI / 背景库，给登录页加一张符合「高校舆情洞察」主题的静态或动态图。选择 **particles.js 2.0.0**（VincentGarreau/particles.js，MIT）的节点网络：它是本项目可直接使用的 vanilla JS 背景库，主题上对应「舆情传播与信息关联」，且构建文件只有 23KB。

- **本地化**：`static/vendor/particles/particles.min.js` + `LICENSE.md`，由 `static_url()` 本地加载；运行时不请求任何外部域名，满足「数据不出校」与无 CDN 约束。
- **背景层**：登录页新增 `#loginBg`（`aria-hidden="true"`、`pointer-events: none`），固定在视口内、`z-index: 0`；`.login-shell` 提到 `z-index: 1`，卡片仍是不透明 `--surface`，文字对比度不依赖背景。
- **构图**：径向遮罩将中心让给卡片，节点只在页边形成低对比的「传播网」；浅色节点/连线用 `--brand-text`（#3358E8）的 0.32 / 0.22，深色用 #5E93FF 的 0.34 / 0.26。遮罩里的 `#000` 是 alpha 极值，不是可见调色板颜色。
- **密度**：固定节点数按桌面视口面积取 44–72（1280×800 为 47，1920×1080 为 72），并关闭 particles.js 的 `density` 自动补点。上游 2.0.0 在 `move.enable=false` 且开启 `density` 时会在 `line_linked` 颜色缓存就绪前绘制并抛错，静态帧会空白；关闭后 reduced-motion 能稳定画出一帧静态网络。
- **动效边界**：仅在 `prefers-reduced-motion: no-preference` 时启用缓慢漂移（speed 0.45）；减少动效时 `move.enable=false`，不启动 RAF，保留静态构图。关闭 hover/click 交互，背景不拦截指针。
- **契约未动**：`auth.js` 的 `#loginForm` / `#username` / `#password` / `#remember` / `#loginBtn` / `#loginErr` / `#nextPath` 全部保留；九档字号、品牌色、红负绿正、深色主题、`.brand-mark` 辉光 pin 未动；未新增移动端样式。

**本轮实测（1280×800，`CP_AUTH=1`）**：

- 浅色 / 深色 / 1920 宽 / reduced-motion 四组：canvas 1280×800（1920 组 1920×1080），节点 47 / 47 / 72 / 47；减少动效组 `move=false`、无 `drawAnimFrame`；四组横向溢出 0。
- 外部请求 0，console / pageerror 0；页面背景层 `pointer-events: none`，卡片中心 `elementFromPoint` 命中 `.login-shell`。
- 空提交与错误密码均出现 `#loginErr`；正确账号登录跳转 `/`；演示模式（8124）仍渲染 `a.login-btn`，无表单、无报错；`autofocus` 仍在 `#username`。
- 设计检测器：1 条既有等宽族 failure（`SFMono-Regular`，非本轮引入）；13 条 advisory（含新增的遮罩 `#000`，以及既有 Jinja 假阳性、按钮渐变与深色投影），无新增结构性命中。
- 许可证：`static/vendor/particles/LICENSE.md`（MIT）；提交包若重新打包需包含 `static/vendor/particles/` 与 `static/cp/login-bg.js`。

## 第 29 轮同步（2026-11-17，登录页品牌深蓝底）

用户指出白底让动态节点图不明显，要求换一个符合主题的背景色。本轮只改登录入口面的底色与背景层对比，表单、卡片结构与工作台浅色主题不动。

- **底色**：浅色主题登录面改为 `linear-gradient(180deg, #0A1730 0%, #13316B 52%, #0B1220 100%)`；深色主题为 `#070F1E → #0A1830 → #060B14`。这是入口面的例外：工作台仍是浅色白底，不是全站换肤。
- **节点**：新增 `--login-bg-node`（浅色 #8FB4FF，深色 #9BBCFF），`login-bg.js` 从背景层读取；浅色点 / 线透明度 0.46 / 0.30，深色 0.50 / 0.32，节点与连线在深蓝底上清楚可见。
- **卡片**：仍是 `--surface` 不透明主体；浅色主题补深底专用投影（`0 30px 72px -36px rgba(2, 8, 23, .8)`），深色主题同步加深；边框仍用 `--line`，文字对比度不依赖背景。
- **浏览器色**：`theme-color` 改成深蓝端值（浅色 #0A1730 / 深色 #070F1E），避免浏览器 UI 与页面脱节。
- **未动**：`auth.js` 契约、九档字号、品牌色、红负绿正、`.brand-mark` 辉光、桌面-only 边界；仍是线性渐层，没有恢复第 12 轮删掉的彩色 radial 光斑。

**本轮实测（1280×800）**：浅色 / 深色 / reduced-motion / 1920 宽四组——canvas 正常、节点 47 / 47 / 47 / 72；减少动效组 `move=false`、无 RAF；四组横向溢出 0、外部请求 0、console/pageerror 0。空提交与错误密码均出现 `#loginErr`，正确账号登录跳转 `/`；演示模式仍渲染 `a.login-btn`，浅 / 深底色与状态条正常。页面级检测器 0 命中；文件级 1 条既有等宽族 failure + 20 条 advisory（新增的是本轮有意加入的深蓝底 / 节点 / 投影字面量，以及遮罩 `#000`），无新增结构性命中。验收截图：`bg_light_1280.png`、`bg_dark_1280.png`、`bg_reduce_1280.png`、`bg_demo_light_1280.png`、`bg_demo_dark_1280.png`。

## 第 30 轮同步（2026-11-17，登录页固定深色入口面）

用户给出深色登录页截图，要求默认就是该颜色。本轮把登录页从「跟随 `cp-theme` / 系统偏好」改为固定深色入口面；工作台主题逻辑不变。

- **固定深色**：`login.html` 的 `<html data-theme="dark">` 直接写死，删除登录页的主题脚本；首帧即深色，无 FOUC，不读 `cp-theme`，也不看 `prefers-color-scheme`。
- **底色**：登录页只保留深色渐变 `#070F1E → #0A1830 → #060B14`；节点 `#9BBCFF`（点 / 线透明度 0.5 / 0.32）；`theme-color` 统一为 `#070F1E`。
- **卡片**：继续使用深色 `--surface`；投影改为深底专用，浅色主题的登录底与登录专用投影已移除，避免留下不会再走到的分支。
- **工作台不受影响**：工作台仍按 `cp-theme > 系统偏好 > 浅色` 初始化；登录页不写 `cp-theme`，所以不会把工作台强制切深。
- **未动**：`auth.js` 契约、九档字号、品牌色、红负绿正、`.brand-mark` 辉光、桌面-only 边界；仍是线性渐层，没有新增 radial 彩色光斑。

**本轮实测（1280×800）**：OS 浅色 / OS 深色 / `cp-theme=light` / `cp-theme=dark` 四种组合下，登录页均为 `data-theme=dark`、卡片 `rgb(19, 28, 43)`、节点 `#9BBCFF`、`theme-color=#070F1E`。登录后工作台回到原逻辑：OS 浅色无记录 → light，OS 深色无记录 → dark，存 light → light，存 dark → dark。reduced-motion 组 `move=false`、无 RAF；演示模式仍是深色卡片 + `a.login-btn`；横向溢出 0、外部请求 0、console/pageerror 0。页面级检测器 0 命中；文件级 1 条既有等宽族 failure + 12 条 advisory（既有按钮渐变、遮罩 `#000`、深色投影），无新增结构性命中。

验收截图：`fixed_dark_os_light.png`、`fixed_dark_os_dark.png`、`fixed_dark_stored_light.png`、`fixed_dark_reduce.png`、`fixed_dark_demo_1280.png`。

## 第 31 轮同步（2026-11-17，登录页背景换成 Vanta NET）

用户在六套本地渲染候选中选择 C：Vanta NET。本轮只替换登录页背景层，卡片、表单、固定深色入口面与工作台主题均不动。

- **正式方案**：`static/vendor/vanta/three.min.js`（three r134，602 KB）+ `static/vendor/vanta/vanta.net.min.js`（Vanta 0.5.24，13 KB），均为 MIT；页面由 `static/cp/login-net.js` 初始化，参数 `color #9BBCFF`、`backgroundColor #070F1E`、`points 10`、`maxDistance 22`、`spacing 18`、`showDots true`，关闭鼠标 / 触摸 / 陀螺仪交互。
- **静态兜底**：`static/cp/login-net.jpg`（1920×1080，195 KB）是同一效果的一帧。仅在 `prefers-reduced-motion: reduce`、WebGL 不可用、Vanta 初始化失败或 `webglcontextlost` 时通过 `html.login-static` 加载；正常动效路径不请求这张图。
- **首屏与登录**：背景脚本改为 `defer`，`auth.js` 先执行，615 KB 的 3D 资源不阻塞登录按钮；CSS 底色仍是 `#070F1E → #0A1830 → #060B14`，Vanta canvas 未就绪时页面已是深色。
- **旧资源**：particles.js 与 `login-bg.js` 已移到 `.impeccable/archive/`，不再被模板引用，也不进入提交包。
- **未动**：`auth.js` 契约、演示模式、记住我、错误提示、九档字号、品牌色、红负绿正、`.brand-mark` 辉光、桌面-only 边界；背景层仍是 `pointer-events: none`，遮罩仍是中心透明、边缘可见。

**本轮实测（`CP_AUTH=1`，127.0.0.1:8123）**：

- 1280×800 / 1920×1080：canvas 分别为 1280×800 / 1920×1080，`#loginBg` 计算位置仍为 `fixed`，遮罩完整；卡片 420×547 居中，横向溢出 0；`THREE.REVISION=134`，`VANTA` 为 object。
- 正常路径外部请求 0、console / pageerror 0；只请求本地 `three.min.js`、`vanta.net.min.js`、`login-net.js`，不请求 `login-net.jpg`。
- reduced-motion：`html.login-static`、无 canvas，`login-net.jpg` 200；静态帧与动效画面构图一致。
- 模拟无 WebGL（`getContext` 对所有 webgl 类型返回 null）：`html.login-static`、无 canvas，`login-net.jpg` 200，仍无报错。
- 登录交互：错误账号提交后 `#loginErr` 可见，说明 `auth.js` 未被背景资源阻塞；`elementFromPoint` 卡片中心命中表单内容。

验收截图：`vanta_net_1280.png`、`vanta_net_1920.png`、`vanta_net_reduce_1280.png`、`vanta_net_nowebgl_1280.png`。

## 第 32 轮同步（2026-11-17，AI 卡片小字减法）

用户给出一张 AI 卡片截图，要求用 skill 减少前端「非必要、无用的小字说明」。按 `impeccable distill` 只做减法：事实、数字与可回溯证据全部保留，删掉重复口径、免责声明与自述式副标题。

- **依据行**：`CP.aiBasis()` 的可见部分只留「范围 · 有效评论条数 · 一句分母」，引擎与生成时间（HH:MM）有则接在后面；完整口径与「数字来自统计接口，AI 不新增数字」收进 `title`。去掉「当前范围：」前缀、「数字由统计接口给出」、逐卡「北京时间」与生成中/失败的长解释。
- **四类卡型**：`ratio` / `word-side` / `mixed` / `sum` 的可见分母仍各不相同；未知 kind 按 JSDoc 归到 `mixed`，不再误落到计数卡。
- **删副标题**：情感、主题、时间、洞察总览四张 AI 卡的 `.card-sub` 自述句全部移除；驾驶舱「AI 舆情简述」的「基于当前筛选范围的 KPI 与分布生成…」同删。
- **删重复注记**：洞察总览「平台指标对照」上方与表格下方各有一段相同的绝对数值不可比说明，保留 JS 用后端 `comparability_note` 渲染的那一段；情感总览卡删掉与卡头分母、AI 依据行重复的长公式注记（`#sentNote` 与对应渲染）。
- **口径行**：驾驶舱底部 `#scopeNote` 收敛为一句（关键词标注 / 多标签合计 / 分母随筛选 / 当前有效评论 N 条），不再重复「有效评论」定义与完整筛选标签；`有效评论 N 条` 保留，`verify_carry.py` 的跨页样本量比对不受影响。
- **等待态**：`insights.js` 只显示「已等待 N 秒 · 模型生成通常需要 10–30 秒」，删除进度尾巴。

**本轮实测（1280×900，`CP_AUTH=0 CP_DEV=0`）**：`/`、`/insights`、`/insights/sentiment`、`/insights/topics`、`/insights/timeline` 均无 AI 卡副标题、无长免责行、横向溢出 0、console / pageerror 0；情感页 `#sentNote` 已移除，卡头分母与词云依据行仍在；洞察总览平台口径只出现 1 次。`tools/verify_ui.py` 的 `.ai-basis 可见行精简（词云独立分母）` 与 `.ai-basis 位置在卡片标题之下` 均 PASS，全站控制台无报错。截图：`.impeccable/live/distill_overview.png`、`distill_dashboard.png`、`distill_sentiment.png`、`distill_topics.png`、`distill_timeline.png`。





