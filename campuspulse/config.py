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
DIMENSIONS = ["宿舍住宿", "食堂餐饮", "招生报考", "教学学业", "校园环境", "交通出行", "校园活动"]
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
ROLES = ["admin", "analyst", "handler", "viewer"]
ROLE_LABELS = {"admin": "管理员", "analyst": "分析员",
               "handler": "处置员", "viewer": "只读"}

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
# 与需求文档 3.5 的对照见 docs/P2能力扩展需求规格说明书.md（已按工作小时改写）。
SLA_FIRST_HOURS = {"P0": 1, "P1": 4, "P2": WORKDAY_HOURS, "P3": 3 * WORKDAY_HOURS}
SLA_ARCHIVE_HOURS = {"P0": 4, "P1": WORKDAY_HOURS, "P2": 3 * WORKDAY_HOURS,
                     "P3": 5 * WORKDAY_HOURS}
SLA_LABELS = {"P0": "紧急", "P1": "高", "P2": "中", "P3": "低"}
TICKET_PRIORITIES = ["P0", "P1", "P2", "P3"]
TICKET_REOPEN_DAYS = 7                    # 归档后多少天内允许重新打开
TICKET_MAX_EVIDENCE = 5                   # 建单自动带入的代表证据条数
TICKET_PAGE_SIZE = 20
TICKET_DUP_WINDOW_HOURS = 24              # 同规则同维度去重窗口

# ---------- 内置数据集（真实采集数据，不做任何人造补数）----------
# 全部来自项目内已有的爬虫导出文件，未经任何人工填充或改写。
PRESET_DATASETS = [
    {
        "dataset_id": "ds_hist_all",
        "name": "全量历史样本 · 2019-2026",
        "source_label": "抖音 + 小红书公开评论（爬虫导出，两批合并）",
        "files": [
            os.path.join("comparison_data", "first", "douyin_comments.json"),
            os.path.join("comparison_data", "first", "xhs_comments.json"),
        ],
        "note": "原始 25,699 条历史采集评论，按原规则清洗后为 1,823 条（抖音 909 / 小红书 914）；"
                "本系统修订维度词典后又剔除泛化情感词，实际保留 1,803 条有效记录。"
                "覆盖 2019-09 至 2026-07，是作品简介中 25,699 与 1,823 两个数字的真实来源，"
                "20 条差异的原因见 docs/口径说明.md。",
    },
    {
        "dataset_id": "ds_2023_all",
        "name": "2023 秋招季合并样本",
        "source_label": "抖音 + 小红书公开评论（爬虫导出，两批合并）",
        "files": [
            "douyin_comments.json", "xhs_comments.json",
            "douyin_comments2.json", "xhs_comments2.json",
        ],
        "note": "2023 年秋招季密集采集，共 2,830 条原始记录，去重后保留 2,573 条。",
    },
    {
        "dataset_id": "ds_2023_main",
        "name": "2023 秋招季主样本",
        "source_label": "抖音 + 小红书公开评论（爬虫导出）",
        "files": ["douyin_comments.json", "xhs_comments.json"],
        "note": "2023 年首批密集样本，抖音 941 条 + 小红书 889 条，三个月内连续覆盖招生与开学节点。",
    },
]
DEFAULT_DATASET_ID = "ds_hist_all"

# ---------- 统计口径文案（前端直接展示，避免各处解释不一致）----------
DEFINITIONS = {
    "raw_count": "原始评论数：本批次导入的原始记录总条数（含重复、无效、与校园话题无关的记录）。",
    "valid_count": "有效评论数：在同一批次内，依次完成字段映射、去重、时间解析、无效短评过滤、校园维度匹配后保留的记录数。",
    "effective_rate": "有效率 = 有效评论数 ÷ 原始评论数 × 100%，分子分母必须来自同一批次。",
    "negative_rate": "负面占比 = 负面记录数 ÷ 当前筛选范围内具有有效情感标签的记录数，分母随筛选条件变化。",
    "dimension": "维度为多标签计数：一条评论可同时命中多个维度，因此七维度合计大于评论总数，属正常现象。",
    "time": "时间口径按评论发布时间筛选，不使用数据导入时间。",
    "snapshot": "本系统展示的是历史采集快照，不是全网实时监测；数据更新以页面标注的分析时间为准。",
}
