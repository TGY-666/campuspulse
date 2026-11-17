# -*- coding: utf-8 -*-
"""CampusPulse 全局配置。

所有路径、限额、枚举值集中在这里，避免散落在各模块造成口径不一致。
"""
import os
import secrets

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
LOG_DIR = os.path.join(DATA_DIR, "logs")

# 允许把库指到别处（CP_DB_PATH）：验证脚本用它跑在副本上。
# 为什么值得单独开一个口子：工单的 ticket_events 是 append-only（库里挂了触发器），
# 测试产生的记录**删不掉**——这是特性不是缺陷。既然如此，就别让测试数据
# 落进演示用的那个库，否则一跑验证，驾驶舱的"今日建单"就多出十几条假数据。
DB_PATH = os.environ.get("CP_DB_PATH") or os.path.join(DATA_DIR, "campuspulse.db")
if not os.path.isabs(DB_PATH):
    DB_PATH = os.path.join(BASE_DIR, DB_PATH)

for _d in (DATA_DIR, UPLOAD_DIR, LOG_DIR):
    os.makedirs(_d, exist_ok=True)


def load_env_file():
    """读取项目根目录 .env，只补充尚未存在的环境变量，不覆盖系统配置。"""
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and not os.environ.get(k):
                    os.environ[k] = v
    except Exception:
        pass


load_env_file()

# ---------- DeepSeek ----------
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "45"))

# ---------- 枚举 ----------
PLATFORMS = ["抖音", "小红书"]
PLATFORM_SLUGS = {"抖音": "douyin", "小红书": "xiaohongshu"}
DIMENSIONS = ["宿舍住宿", "食堂餐饮", "招生报考", "教学学业", "实习就业",
              "校园环境", "交通出行", "校园活动"]
SENTIMENTS = ["positive", "neutral", "negative"]
SENTIMENT_LABELS = {"positive": "正面", "neutral": "中性", "negative": "负面", "unknown": "未知"}

# ---------- 限额 ----------
MAX_UPLOAD_BYTES = 20 * 1024 * 1024      # 单文件 20MB
MAX_UPLOAD_FILES = 4
MAX_COMMENT_LIMIT = 50                   # 证据检索单次最多返回条数
DEFAULT_COMMENT_LIMIT = 10
MAX_TREND_DAYS = 400
AGENT_MAX_TOOL_CALLS = 6                 # 单轮对话最多工具调用次数
AGENT_MAX_SECONDS = 90                   # 单轮对话总耗时上限
AGENT_MAX_QUESTION_CHARS = 200           # 单条提问字数上限（输入侧第一道闸）
AGENT_RATE_LIMIT_PER_MIN = 20            # 同一来源每分钟最多提问次数（输入侧频次闸）
AGENT_MAX_ROUNDS = 20                    # 单会话建议轮次上限（前端计数，超限提示新开会话）

# 开发／演示模式：只有 CP_DEV=1 时才开放模板热重载与限流器重置
# （限流器重置在生产环境等于把闸门开关交给调用方，所以必须显式关闭）
CP_DEV = os.environ.get("CP_DEV") == "1"
DEFAULT_TREND_TOP_N = 10
DEFAULT_TOPIC_TOP_N = 10


# ---------- P2 能力开关（全部默认关闭 = 行为与 v1.0 完全一致）----------
def _flag(name):
    """只认 "1"，其它一律当关闭。写成 "true"/"yes" 容易在 bat 里漏引号，不迁就。"""
    return (os.environ.get(name, "") or "").strip() == "1"


CP_AUTH = _flag("CP_AUTH")                        # 登录与轻量多租户
# 工单默认**跟随 CP_AUTH**：没有角色就没有派单对象，工单在无登录模式下
# 只能演"自己给自己派单"，演示出来是假的。显式 CP_TICKETS=0 可单独关掉。
CP_TICKETS = (os.environ.get("CP_TICKETS", "1" if CP_AUTH else "0").strip() == "1")
# 引用核验是 P2 里**唯一默认开启**的开关，理由：它只做减法。
# 不改统计口径、不改图表数值，最坏情况是回答少一句话；而关掉它，
# "系统能拦住 AI 编造的引用"这句话在默认配置下就不成立了。
# 出问题时置 0 即可回到 v1.0 路径（只记录、不拦截、不重答）。
CP_CITATION_GUARD = (os.environ.get("CP_CITATION_GUARD", "1").strip() == "1")

# ---------- 实时采集（P3）----------
# 真实平台直连：Playwright 开真浏览器、带登录态进搜索页与详情页，
# 拦平台的评论接口取正文。默认开启，但**没有登录态就跑不出数据**——
# 这不是开关问题，是抖音/小红书对未登录访问做了接口级拦截（实测匿名搜索返回空）。
# 关掉（CP_CRAWL=0）时采集入口整体隐藏，其余功能不受影响。
CP_CRAWL = (os.environ.get("CP_CRAWL", "1").strip() == "1")
CRAWL_MAX_TARGET = 300          # 单次最多采多少条：再多用户等不住，也没必要
# 采集什么时候结束：**采满目标条数**或**用户点停止**。不按固定秒数硬停——
# 现场演示时 150 秒经常刚好卡在翻页中间，人还在看屏幕，任务已经被掐了。
# 但完全不管也不行：用户关掉页面就走了，后台会留一个浏览器进程一直跑。
# 所以留一个"安全阀"（默认 30 分钟），正常操作永远碰不到它。
CRAWL_MAX_SECONDS = int(os.environ.get("CP_CRAWL_MAX_SECONDS", "1800") or 1800)
CRAWL_MAX_PAGES = 30            # 单次最多打开多少个视频/笔记详情页
# 单个详情页最多滚多少次。评论是按页懒加载的，抖音一页 20 条——
# 只滚 4 次大约拿 20–40 条，那不叫"这个视频的评论"，只是头几屏。
# 真正的停止条件是"连续两轮没有新增"（翻到底了），这个数字只是上限。
CRAWL_SCROLL_ROUNDS = 15
# 搜索页滚几次：搜索结果本身也是懒加载的，只取首屏等于只采前几个视频。
CRAWL_SEARCH_SCROLLS = 4
# 连续多少个详情页一条都没新增，就认定这个平台采不出来（未登录/被限流/时间段太老）。
# 10 而不是 5：搜索结果的排序里夹着不少评论很少的冷门视频，
# 阈值太低会在"其实还能采"的时候提前收工，用户只会看到条数莫名停住。
CRAWL_EMPTY_STREAK = 10
# 连续多少个详情页"有评论但都在时间段外"，就认定这个关键词翻不到那么早。
# 和 EMPTY_STREAK 分开：那个判"平台没给东西"，这个判"给了但都不在区间"。
CRAWL_RANGE_STREAK = 6
CRAWL_AUTH_PATH = os.path.join(DATA_DIR, "crawl_auth.json")
CRAWL_CHROME = os.environ.get("CP_CRAWL_CHROME", "").strip()
CRAWL_HEADFUL = _flag("CP_CRAWL_HEADFUL")   # 有头模式：反爬更宽松，演示时也更有说服力
# 扫码登录的等待上限。抖音的扫码+手机确认常常要 30–60 秒，
# 给 180 秒；到点自动关窗，不留一个没人管的浏览器进程。
CRAWL_LOGIN_TIMEOUT = int(os.environ.get("CP_CRAWL_LOGIN_TIMEOUT", "180") or 180)

# ---------- P1 能力开关 ----------
# 截图评论导入（多模态）。默认**开启**：入口只出现在数据页，没有 OCR 环境时
# 接口会明确报错并给出安装指引，而不是静默降级成"上传成功但没有数据"。
CP_VISION = (os.environ.get("CP_VISION", "1").strip() == "1")
VISION_MAX_FILES = 6                       # 一次最多 6 张截图（一张约 1 秒，再多用户等不住）
VISION_MAX_BYTES = 8 * 1024 * 1024         # 单张 8MB；手机截图通常 0.3–2MB
VISION_MAX_ROWS = 80                       # 单张图最多解析 80 条，防止一张长图刷爆复核表
VISION_MIN_SCORE = float(os.environ.get("VISION_MIN_SCORE", "0.55") or 0.55)
# 视觉模型兜底（OpenAI 兼容接口）。不配置也能用：本地 OCR 才是首选路径，
# 只有在没装 onnxruntime 的机器上才需要它。
VISION_API_KEY = os.environ.get("VISION_API_KEY", "").strip()
VISION_BASE_URL = os.environ.get(
    "VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
VISION_MODEL = os.environ.get("VISION_MODEL", "qwen-vl-plus")
VISION_TIMEOUT = int(os.environ.get("VISION_TIMEOUT", "60"))

# Agent 工具通道：local = 进程内直调；mcp = 走 MCP stdio 客户端。
# 两条通道调用的是**同一份 TOOL_SPECS 与同一个 execute()**，切换只影响传输层，
# 参数校验、白名单、脱敏、口径披露完全一致——这正是"工具层与界面解耦"的证据。
TOOL_CHANNEL = (os.environ.get("CP_TOOL_CHANNEL", "local").strip() or "local").lower()

# 租户隔离的"闸门"与"体检"分开：
#   CP_TENANT_ENFORCE=1 -> 漏写 tenant_id 直接抛异常
#   CP_TENANT_ENFORCE=0 -> 只累计计数，不阻断
#
# 默认值**跟随 CP_AUTH**：只要开了多租户就强制隔离。
# 这个默认值是有意为之的——S1 把 60 处 SQL 全改完之后，
# "开了登录却忘了开强制"就成了纯风险、零收益的状态：
# 隔离看起来在工作（登录页有、权限有），实际漏写一处就能跨租户读数据，
# 而且没有任何报错。显式设 CP_TENANT_ENFORCE=0 仍可关闭，仅用于排查对比。
CP_TENANT_ENFORCE = os.environ.get(
    "CP_TENANT_ENFORCE", "1" if CP_AUTH else "0").strip() == "1"

DEFAULT_TENANT_ID = os.environ.get("CP_DEFAULT_TENANT", "t_default").strip() or "t_default"


def _secret_key():
    """会话签名密钥。优先环境变量，其次本地文件，最后生成并落盘。

    必须持久化：每次重启换密钥 = 所有人被踢下线，演示时会很难看。
    """
    env = (os.environ.get("CP_SECRET_KEY") or "").strip()
    if len(env) >= 32:
        return env
    path = os.path.join(DATA_DIR, ".session_key")
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                v = f.read().strip()
            if len(v) >= 32:
                return v
    except OSError:
        pass
    v = secrets.token_urlsafe(48)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(v)
        os.chmod(path, 0o600)
    except OSError:
        pass
    return v


SECRET_KEY = _secret_key()

# ---------- 认证与会话 ----------
SESSION_HOURS = int(os.environ.get("CP_SESSION_HOURS", "8"))
REMEMBER_DAYS = int(os.environ.get("CP_REMEMBER_DAYS", "30"))
LOGIN_IP_LIMIT = (5, 300)       # 同 IP：5 次 / 5 分钟
LOGIN_USER_LIMIT = (10, 3600)   # 同账号：10 次 / 1 小时
# dept = 部门账号：绑定到某一个归口部门，只看得到、只办得了本部门的工单。
# 它不是"更小的管理员"，也不是"带部门的处置员"——处置员按人名分工（owner_id），
# 部门账号按归口分工（department_id），两者的可见范围口径不同，不能合成一个角色。
ROLES = ["admin", "analyst", "handler", "viewer", "dept"]
ROLE_LABELS = {"admin": "管理员", "analyst": "分析员",
               "handler": "处置员", "viewer": "只读", "dept": "部门账号"}

# ---------- M3 处置工单：SLA 与工时日历 ----------
# **单位统一为「工作小时」**，这是本项目 SLA 唯一的难点，也是答辩最容易被追问的点。
#
# 为什么不用自然小时：工单周五 17:00 建、要求 24 小时归档，如果按自然小时算，
# 周六下午就"超期"了，而这两天根本没人上班——这类"假超期"会让指标彻底不可信。
# 所以只累计工作时间：
WORK_HOURS = [(9, 12), (14, 18)]          # 上午 9-12、下午 14-18，每天 7 个工作小时
WORKDAY_HOURS = sum(b - a for a, b in WORK_HOURS)

# 节假日：默认**空**。不猜、不内置中国法定节假日表——
# 猜错一次就会让"按时率"整体偏移，而这种偏移没人会去核对。需要时用环境变量补：
#     CP_HOLIDAYS=2026-10-01,2026-10-02
HOLIDAYS = {d.strip() for d in (os.environ.get("CP_HOLIDAYS") or "").split(",") if d.strip()}

# 数值含义：P0 半天、P1 一个工作日、P2 三个工作日、P3 五个工作日。
# 工作小时 SLA 的计算口径见 tickets.py；与需求文档 3.5 的对照已按工作小时改写。
SLA_FIRST_HOURS = {"P0": 1, "P1": 4, "P2": WORKDAY_HOURS, "P3": 3 * WORKDAY_HOURS}
SLA_ARCHIVE_HOURS = {"P0": 4, "P1": WORKDAY_HOURS, "P2": 3 * WORKDAY_HOURS,
                     "P3": 5 * WORKDAY_HOURS}
SLA_LABELS = {"P0": "紧急", "P1": "高", "P2": "中", "P3": "低"}
TICKET_PRIORITIES = ["P0", "P1", "P2", "P3"]
TICKET_REOPEN_DAYS = 7                    # 归档后多少天内允许重新打开
TICKET_MAX_EVIDENCE = 5                   # 建单自动带入的代表证据条数
TICKET_PAGE_SIZE = 20
TICKET_DUP_WINDOW_HOURS = 24              # 同规则同主题去重窗口

# ---------- 内置数据集（真实采集数据，不做任何人造补数）----------
# 全部来自项目内已有的爬虫导出文件，未经任何人工填充或改写。
PRESET_DATASETS = [
    {
        "dataset_id": "ds_hist_all",
        "name": "2022-2026",
        "source_label": "抖音 + 小红书公开评论（公开平台采集 · 两批合并）",
        "files": [
            os.path.join("datasets", "2022-2026", "douyin_comments.json"),
            os.path.join("datasets", "2022-2026", "xhs_comments.json"),
        ],
        # 时间窗口只按**评论发生时间**截：这是"数据集讲哪段时间"的定义，
        # 不是把旧评论藏起来。截口前的记录不进质量链路，原始条数也只算窗口内的，
        # 所以"原始 23,724 → 有效 1,960"与名字里的 2022-2026 是同一件事。
        "date_from": "2022-01-01",
        "note": "原始两批采集共 25,699 条（抖音 17,157 + 小红书 8,542），按原规则清洗后 1,823 条"
                "——这两个数字出自完整批次，是作品简介的口径来源。"
                "本数据集只保留评论时间 2022-01 之后的部分：23,724 条原始记录，"
                "清洗后有效 1,960 条（抖音 1,014 / 小红书 946），有效率 8.26%，覆盖 2022-01 至 2026-07。"
                "2022 年之前的 1,975 条（其中有效 9 条）已按时间窗口剔除，"
                "剔除量与两套口径的关系见 README 的「数据集说明」。",
    },
    {
        "dataset_id": "ds_2023_all",
        "name": "2023（合并）",
        "source_label": "抖音 + 小红书公开评论（公开平台采集 · 两批合并）",
        "files": [
            os.path.join("datasets", "2023", "douyin_comments.json"),
            os.path.join("datasets", "2023", "xhs_comments.json"),
            os.path.join("datasets", "2023", "douyin_comments2.json"),
            os.path.join("datasets", "2023", "xhs_comments2.json"),
        ],
        "note": "2023 年秋招季密集采集，共 2,830 条原始记录，去重后保留 2,567 条。",
    },
    {
        "dataset_id": "ds_2023_main",
        "name": "2023",
        "source_label": "抖音 + 小红书公开评论（公开平台采集）",
        "files": [
            os.path.join("datasets", "2023", "douyin_comments.json"),
            os.path.join("datasets", "2023", "xhs_comments.json"),
        ],
        "note": "2023 年首批密集样本，抖音 941 条 + 小红书 889 条，三个月内连续覆盖招生与开学节点。",
    },
    {
        # 这份不是采集原件，是按全量历史样本的形态生成的演示数据。
        # 单独放一份的理由：它是唯一覆盖「实习就业」主题的数据集，
        # 换台机器首次启动就能直接演示八主题，不必先跑生成脚本。
        # note 里如实写明"生成"，不伪装成采集结果。
        # 名字只用时间：这份数据回答的是"哪段时间的讨论"，不是"几个主题"。
        "dataset_id": "ds_2020_2026",
        "name": "2020-2026",
        "source_label": "抖音 + 小红书公开评论（按真实样本特征生成 · 公开平台口径）",
        "files": [
            os.path.join("datasets", "2020-2026-generated", "douyin_comments.json"),
            os.path.join("datasets", "2020-2026-generated", "xiaohongshu_comments.json"),
        ],
        "note": "正文取自全量历史样本的真实评论与同题材补充语料，时间节律、长度、"
                "互动量按历史样本的实测分布重采样，覆盖 2020-2026 每年 9 月下旬至 11 月上旬。"
                "原始 7,386 条含重复/空内容/短评/无关讨论，清洗后有效 1,979 条，"
                "有效率 26.79%；正/中/负 = 26.3% / 66.0% / 7.7%，"
                "小红书 998 + 抖音 981。生成脚本 tools/gen_dataset_timeline.py，"
                "固定种子可复现。",
    },
]
DEFAULT_DATASET_ID = "ds_hist_all"

# ---------- 定期风险研判（PAGE-06 的定期部分）----------
# 按**自选时间段**把评论单独跑一遍研判，结论落库；定时任务每月初跑「上个月」。
# 与页面上的实时研判只有两点不同：统计窗口是一段明确的日期（不是滚动 30 天），
# 结果会落库成一条可追溯的研判记录（risk_runs），而不是算完就丢。
#
# **这里不设门槛、也不自动建单。** 哪一条该开单，取决于"这几天有没有人在盯、
# 别的渠道是不是也在说同一件事"——这两件事系统都不知道。研判只负责把
# 该谁管、多急、凭什么摆出来，开不开单由人在页面上点。
# （早先有过"风险≥70 自动建单 + 单次上限 3 张"的版本，正是因为它得替人
#  猜这两件事，才显得多余：门槛调低了刷废单，调高了漏真事。）
#
# 为什么默认跟着 CP_AUTH 走：免登录模式（CP_AUTH=0）跑验收脚本时不该
# 凭空多出一批研判记录，那会让截图与文档里的历史记录对不上。
# 要单独开就用 CP_MONTHLY=1。
CP_MONTHLY = (os.environ.get("CP_MONTHLY", "1" if CP_AUTH else "0").strip() == "1")

# 定时任务没有浏览器，只能固定一个数据集：默认就是内置主数据集。
# 页面上手动点「立即研判」时会用当前选中的数据集，两者可以不同。
MONTHLY_DATASET = os.environ.get("CP_MONTHLY_DATASET", "").strip() or DEFAULT_DATASET_ID

# 后台检查间隔（分钟）。每轮只问一句"上个月的研判跑了没"，
# 便宜到没有理由做成精确到秒的 cron。
MONTHLY_CHECK_MINUTES = int(os.environ.get("CP_MONTHLY_CHECK_MINUTES", "60") or 60)

# ---------- 对外术语 ----------
# 校园八类业务在界面上统一叫「主题」，数据库字段仍是 dimension（改名要动表与全部查询）。
# 这句话必须塞进每一个提示词：模型会照抄提示词和事实键名里的措辞，只要有一处没写死，
# 它答完排行就会自己补一句"各维度中负面集中度较高"——界面上的主题，AI 嘴里的维度，
# 当场穿帮（驾驶舱「AI 舆情简述」就是这么露的）。
TERM_RULE = "术语：涉及校园这几类业务分类时，一律写「主题」，不要使用「维度」这个词。"

# ---------- 统计口径文案（前端直接展示，避免各处解释不一致）----------
DEFINITIONS = {
    "raw_count": "原始评论数：本批次导入的原始记录总条数（含重复、无效、与校园话题无关的记录）。",
    "valid_count": "有效评论数：在同一批次内，依次完成字段映射、去重、时间解析、无效短评过滤、校园主题匹配后保留的记录数。",
    "effective_rate": "有效率 = 有效评论数 ÷ 原始评论数 × 100%，分子分母必须来自同一批次。",
    "negative_rate": "负面占比 = 负面记录数 ÷ 当前筛选范围内具有有效情感标签的记录数，分母随筛选条件变化。",
    "dimension": "主题为多标签计数：一条评论可同时命中多个主题，因此八主题合计大于评论总数，属正常现象。",
    "time": "时间口径按评论发布时间筛选，不使用数据导入时间。",
    "snapshot": "本系统展示的是历史采集快照，不是全网实时监测；数据更新以页面标注的分析时间为准。",
    "screenshot": "截图识别：由 OCR 从上传截图提取的评论文本，置信度由逐字识别分数得出；"
                  "识别结果须人工复核后才计入分析，未包含发布时间的记录按导入日期计入并单独标注。",
}
