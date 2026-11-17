/**
 * CampusPulse 前端类型契约（TypeScript 声明文件，不参与构建）。
 *
 * 为什么是渐进式而不是重写为 TS：
 *   3,700 行原生 JS 在赛前重写的回归风险远大于收益（细则里这部分只有 4 分）。
 *   这里给"跨模块流动的数据"定契约——API 信封、全局状态、图表规格、工具步骤——
 *   因为这些地方出错会静默算错数字；至于 DOM 选择器，返回 any 更划算：
 *   90% 的调用点都清楚自己拿到的是 input 还是 div，给它们加断言只是噪音。
 *
 * 维护约定：`CP` 对象新增对外成员时，同步在这里补一行签名。
 */

/** 数据集卡片（/api/datasets 的一项） */
type CPDataset = {
    dataset_id: string;
    name: string;
    valid_count: number;
    raw_count: number;
    effective_rate?: number;
    time_start?: string;
    time_end?: string;
    analyzed_at?: string;
    source_label?: string;
    note?: string;
    is_demo?: number;
    data_version?: string;
    platforms?: Record<string, number>;
    quality?: Record<string, any>;
};

/** 全局筛选条件。空数组 = 不筛；空字符串 = 不限 */
type CPFilters = {
    platform: string[];
    dimension: string[];
    sentiment: string[];
    date_from: string;
    date_to: string;
    keyword: string;
};

/** 跨页面共享状态 */
type CPState = {
    dataset_id: string | null;
    datasets: CPDataset[];
    datasetMeta: CPDataset | null;
    filters: CPFilters;
    health: any;
};

/** 统一错误信封解析出来的错误对象 */
type CPError = Error & { detail?: any; status?: number };

/** 后端图表规格（charts.py 产出），前端只负责渲染，不参与计算 */
type CPChartSpec = {
    type: string;
    title?: string;
    unit?: string;
    horizontal?: boolean;
    categories?: string[];
    series?: Array<{ name?: string; data: any[] }>;
};

/** 后端工具调用步骤（agent._tool_steps_payload） */
type CPToolStep = {
    index: number;
    tool: string;
    label: string;
    args?: Record<string, any>;
    status: 'completed' | 'failed';
    elapsed_ms: number;
    summary?: string;
    empty?: boolean;
    /** 本次调用走的工具通道：local 直调 / mcp 协议 */
    channel?: 'local' | 'mcp';
    reason?: string | null;
};

/** 前端共享运行时（core.js 暴露到 window.CP） */
type CPCore = {
    state: CPState;
    /** DOM 选择器。刻意返回 any：调用点自己知道拿到的是什么元素 */
    $(sel: string, root?: ParentNode): any;
    $$(sel: string, root?: ParentNode): any[];
    esc(s: any): string;
    num(v: any): string;
    pct(v: any): string;
    dateShort(d: any): string;
    /** 工作小时：全站唯一的时长写法（SLA 单位是工作小时，不是自然小时）。
     *  业务文案里「48h」这种裸写法一律禁止，统一走这里 */
    workHours(v: any): string;
    /** 工作小时 → 工作天数。perDay 取 meta.sla.workday_hours，别写死 7 */
    workdays(v: any, perDay: any): string;
    /** 按时归档率的脚注：样本 < min 单只说分子分母，不换算百分比（研判页与工单页共用） */
    onTimeFoot(ot: any, min?: number): string;
    /** 同一份计算的拆开版：value 给大数字位、note 给脚注位。
     *  工单页那张「按时归档」KPI 用 value，研判页用 onTimeFoot，两页同源 */
    onTimeParts(ot: any, min?: number): { value: string, note: string };
    toast(msg: string, kind?: 'ok' | 'err'): void;
    /** 统一 API 信封处理；失败抛 CPError */
    api(path: string, params?: Record<string, any> | null, opts?: RequestInit): Promise<any>;
    qs(extra?: Record<string, any>): string;
    syncUrl(extra?: Record<string, any>): void;
    filterParams(extra?: Record<string, any>): Record<string, any>;
    setState(el: any, kind: 'loading' | 'empty' | 'error', msg?: string, onRetry?: () => void): void;
    chart(el: any): any;
    setOption(el: any, option: any): any;
    /** 图表就绪回调：惰性初始化下用于绑定下钻等交互 */
    onChart(el: any, cb: (inst: any) => void): void;
    /** 只 resize 已创建的图表，不触发新的 init */
    resizeCharts(): void;
    /** 元素进入视口附近时执行一次（用于"滚到才加载"的资源） */
    whenVisible(el: any, cb: () => void): void;
    renderSpec(el: any, spec: CPChartSpec): void;
    specToOption(spec: CPChartSpec): any;
    themeBase(): any;
    axisCommon(): any;
    renderFilterBar(container: any, opts?: any): { sync(): void; apply(): void };
    /** 页面脚本改完 state.filters 后同步筛选栏的可见状态（下钻/榜单点击用） */
    syncFilterBar(): void;
    /** 打开模态：焦点送进首个可见输入框，Tab 在内部循环，Esc 交给 onEsc */
    openModal(mask: any, opts?: { onEsc?: () => void; back?: any }): void;
    /** 关闭模态并把焦点还给打开它的元素 */
    closeModal(mask: any): void;
    /** 危险操作统一确认框：标题/说明/警示块，默认焦点在「取消」上，返回是否确认。
     *  需要 <b> 强调时传 subHtml / noteHtml（core.js 按富文本插入，调用方负责转义） */
    confirmModal(opt?: {
        title?: string; sub?: string; note?: string;
        subHtml?: string; noteHtml?: string;
        okLabel?: string; cancelLabel?: string; danger?: boolean;
    }): Promise<boolean>;
    /** 给图表容器一句人话（role=img + aria-label）；canvas 读屏读不到 */
    describeChart(el: any, text: string): void;
    /** 图表字号：与 app.css :root 的 9 档同源，图表 canvas 读不到 CSS 变量，下限 12px */
    CHART_FS: { micro: number; xs: number; sm: number; base: number };
    /** 负面段的 45° 细斜纹（色盲友好）：所有堆叠柱的负面系列共用同一个 */
    NEG_DECAL: Record<string, any>;
    /** 双 live region 播报；kind 省略为 polite */
    announce(msg: string, kind?: 'polite' | 'assertive'): void;
    /** 给图表容器写 aria-label（describeChart 的别名，页面脚本两个名字都在用） */
    labelChart(el: any, text: string): void;
    /** 当前**筛选**的人话串（不含数据集名），无筛选时为空串 */
    scopeText(): string;
    /** 当前数据集的人话名字，取不到返回空串 */
    datasetLabel(): string;
    /** 完整口径串：`数据集 X · 筛选`，两者都没有时返回「全部数据」 */
    scopeLine(): string;
    /** AI 卡依据行（已转义 HTML）。kind 决定分母口径；未传 / 未知值按 mixed。
     *  opts.state 为 'pending' / 'failed' 时保留口径行、只换第二行说明 */
    aiBasis(d?: any, kind?: 'ratio' | 'sum' | 'word-side' | 'mixed',
            opts?: { state?: 'pending' | 'failed' }): string;
    /** 打印、截图或「显示全部图表」时，把还没画的图一次性建出来 */
    flushAllCharts(): void;
    /** 标记某图表容器渲染真失败（is-failed），与纯懒加载区分 */
    markChartFailed(el: any): void;
    init(opts?: any): Promise<void>;
    loadHealth(): Promise<any>;
    loadTicketBadge(): Promise<any>;
    loadDatasets(): Promise<CPDataset[]>;
    renderDatasetSelect(): void;
    /** 切换数据集（全局唯一入口）：四个入口共用，避免各写一份漏清筛选 */
    switchDataset(id: string): Promise<{ dataset_id: string; name: string }>;
    themeColors(): { ink: string; ink2: string; ink3: string; ink4: string; line: string; line2: string; surface: string; dark: boolean };
    getTheme(): 'light' | 'dark';
    applyTheme(t: string, opts?: any): string;
    toggleTheme(): string;
    PALETTE: string[];
    POS: string;
    NEU: string;
    NEG: string;
    SENTIMENT_COLOR: Record<string, string>;
    DIMENSIONS: string[];
    PLATFORMS: string[];
};

declare const CP: CPCore;

/** ECharts 由本地 vendor 引入，类型交给官方 d.ts 会把体积撑大，这里按 any 处理 */
declare const echarts: any;

interface Window {
    CP: CPCore;
    /** Web Speech API：标准名与 Chrome 前缀名并存，两者都可能不存在 */
    SpeechRecognition?: any;
    webkitSpeechRecognition?: any;
    /** 语音输入模块（voice.js，可缺失） */
    CPVoice?: any;
    /** 测试/嵌入场景下关闭 core 的自动初始化 */
    __CP_NO_AUTOINIT?: boolean;
    /** 性能对比用：置 true 关闭图表惰性初始化（全局一次性开关） */
    __CP_NO_LAZY?: boolean;
    /** ECharts 由 vendor 脚本挂到 window 上 */
    echarts?: any;
    /** 词云插件已加载 */
    __CP_WORDCLOUD_READY?: boolean;
    /** 词云插件地址（默认 /static/vendor/…，测试可覆盖） */
    __CP_WORDCLOUD_URL?: string;
}
