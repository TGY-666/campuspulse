# -*- coding: utf-8 -*-
"""
广东东软学院多平台社交舆情分析 —— 数据清洗 + 统计 + DeepSeek AI 分析
===========================================================
职责：
1. 读取 raw_data 下的抖音 / 小红书原始评论 JSON
2. 清洗、去重、主题筛选、情感/维度/负面原因/正面卖点标注
3. 产出前端所需的全部图表数据（overview + charts）
4. 调用 DeepSeek 生成两类 AI 分析：
   - insights：每张图表的简短辅助分析（14 项）
   - report：综合研判报告（结论 / 决策建议 / 深度讨论 / 平台策略等）
5. 计算舆情风险等级
6. 导出 cleaned_data/data.json 与 output_excel 下的统计 Excel
"""
import os
import re
import sys
import json
import requests
import numpy as np
import pandas as pd
from collections import Counter

import jieba

# 自定义词典：只补充默认会拆错的领域词（其余保持 jieba 默认切分，与历史词云口径一致）
for _w in ["小学期", "上床下桌"]:
    jieba.add_word(_w)


def log(*args, **kwargs):
    """安全日志输出。

    Windows 下以后台方式、重定向方式或 GUI 方式启动时，标准输出句柄可能失效，
    直接 print 会抛 OSError: [Errno 22] Invalid argument，从而中断整个分析流程。
    这里统一兜住，保证日志写不出去也不影响主流程。
    """
    try:
        print(*args, **kwargs)
        sys.stdout.flush()
    except Exception:
        pass

# ====================== DeepSeek API 配置 ======================
# key 的读取优先级：系统环境变量  >  项目根目录的 .env 文件  >  空（走本地兜底文案）
# 推荐用 .env 文件管理：在项目根目录新建 .env，写一行
#   DEEPSEEK_API_KEY=sk-xxxxxxxx
# 这样换 key 不用改代码，也不会把 key 写死进源码。
def _load_dotenv():
    """轻量读取项目根目录 .env（不引入 python-dotenv 依赖）。

    只补充「尚未存在」的环境变量，不覆盖系统里已配置的值，
    因此 set DEEPSEEK_API_KEY=... 的方式依然优先。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(path):
        return
    try:
        # utf-8-sig 兼容记事本保存时带的 BOM
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v and not os.environ.get(k):
                    os.environ[k] = v
    except Exception:
        pass


_load_dotenv()

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
USE_DEEPSEEK_AI = True  # True=调用 DeepSeek 生成分析；False=仅使用本地模板
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE_DIR, "raw_data")
OUTPUT_EXCEL_DIR = os.path.join(BASE_DIR, "output_excel")
CLEANED_DIR = os.path.join(BASE_DIR, "cleaned_data")

os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(OUTPUT_EXCEL_DIR, exist_ok=True)
os.makedirs(CLEANED_DIR, exist_ok=True)

# ============================================================
# 平台识别：raw_data 下每个 .json 按文件名/字段自动判断所属平台
# 不再写死「抖音 / 小红书」两个文件，支持任意平台数据上传
# ============================================================
PLATFORM_KEYWORDS = [
    ("抖音", ["douyin", "抖音", "aweme"]),
    ("小红书", ["xhs", "xiaohongshu", "redbook", "小红书"]),
    ("微博", ["weibo", "微博", "sina"]),
    ("B站", ["bilibili", "b站", "bili", "哔哩"]),
    ("快手", ["kuaishou", "快手"]),
    ("知乎", ["zhihu", "知乎"]),
    ("贴吧", ["tieba", "贴吧"]),
    ("视频号", ["shipinhao", "视频号", "channels"]),
    ("Twitter", ["twitter"]),
    ("YouTube", ["youtube", "ytb"]),
]

PLATFORM_SLUG = {
    "抖音": "douyin", "小红书": "xhs", "微博": "weibo", "B站": "bilibili",
    "快手": "kuaishou", "知乎": "zhihu", "贴吧": "tieba", "视频号": "shipinhao",
    "Twitter": "twitter", "YouTube": "youtube",
}


def platform_slug(name):
    """平台名 → 安全的英文文件名前缀"""
    if name in PLATFORM_SLUG:
        return PLATFORM_SLUG[name]
    cleaned = re.sub(r"[^\w\u4e00-\u9fa5]+", "_", str(name)).strip("_")
    return cleaned or "other"


def _extract_rows(data):
    """兼容多种爬虫导出结构：纯列表 / {data:[...]} / {comments:[...]} 等"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "comments", "list", "items", "results", "notes"):
            val = data.get(key)
            if isinstance(val, list):
                return val
        return []
    return []


def detect_platform(filename, rows=None):
    """识别一份数据所属平台

    优先顺序：数据里自带的 platform 字段 → 文件名关键词 → 文件名主干
    """
    if rows:
        first = rows[0] if isinstance(rows[0], dict) else {}
        raw_plat = str(first.get("platform") or first.get("source") or "").strip()
        if raw_plat:
            low = raw_plat.lower()
            for plat, keys in PLATFORM_KEYWORDS:
                if any(k in low for k in keys):
                    return plat
            return raw_plat

    name_low = str(filename).lower()
    for plat, keys in PLATFORM_KEYWORDS:
        if any(k in name_low for k in keys):
            return plat

    stem = os.path.splitext(os.path.basename(str(filename)))[0]
    stem = re.sub(r"(_?comments?|_?data|_?export)$", "", stem, flags=re.I)
    return stem or "其他"


def scan_raw_files(only_files=None):
    """扫描 raw_data 下的 json，返回 [(平台, 文件路径)]

    only_files: 只扫描这些文件名（即「本次上传的数据集」）。
                传入空列表/None 时扫描全部（兼容旧调用）。
                限定文件名的意义：上传新数据后只分析新数据，
                不会把 raw_data 里遗留的历史文件一起算进去。
    """
    result = []
    if not os.path.isdir(RAW_DIR):
        return result
    allow = None
    if only_files:
        allow = {os.path.basename(str(x)) for x in only_files}
    for fn in sorted(os.listdir(RAW_DIR)):
        if not fn.lower().endswith(".json"):
            continue
        if allow is not None and fn not in allow:
            log(f"  ⏭️ 跳过非本次上传文件：{fn}")
            continue
        path = os.path.join(RAW_DIR, fn)
        rows = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                rows = _extract_rows(json.load(f))
        except Exception:
            rows = None
        result.append((detect_platform(fn, rows), path))
    return result

DIMENSION_KEYWORDS = {
    "宿舍住宿": ["宿舍", "寝室", "上床下桌", "空调", "独立卫浴", "洗衣机", "宿管", "门禁", "断电", "水电费", "网费",
                 "校园网", "热水", "水电", "宿舍条件", "住宿费"],
    "食堂餐饮": ["食堂", "饭堂", "餐厅", "饭菜", "菜品", "伙食", "外卖", "奶茶", "超市", "商业街", "物价", "贵", "便宜",
                 "性价比", "干饭", "好吃", "难吃", "卫生"],
    "招生报考": ["分数", "分数线", "录取", "投档线", "排位", "省排", "招生", "报考", "志愿", "专业组", "学校代码",
                 "学费", "奖学金", "助学金", "助学贷款", "新生", "开学", "军训", "转专业", "专升本", "专插本",
                 "能不能上", "稳不稳", "冲不冲", "保底", "录取分"],
    "校园环境": ["环境", "地理位置", "位置", "周边", "佛山", "南海", "狮山", "偏僻", "进城", "校外", "校内", "绿化",
                 "湖", "校园", "校区", "校门", "风景", "漂亮"],
    "教学学业": ["专业", "选课", "抢课", "学分", "绩点", "综测", "补考", "重修", "考试", "期末", "期中", "作业",
                 "实验报告", "实训", "实习", "毕业设计", "论文", "答辩", "辅导员", "导师", "教授", "老师", "教学质量",
                 "课程", "水课", "考勤", "挂科", "四六级", "计算机二级", "考研", "考公", "考编", "师资", "产教融合",
                 "小学期"],
    "交通出行": ["交通", "出行", "地铁", "佛山西站", "狮山站", "通勤", "公交", "校巴", "摆渡车", "快递", "菜鸟驿站",
                 "打车", "不方便", "偏远"],
    "校园活动": ["社团", "学生会", "团委", "社团招新", "百团大战", "校园活动", "校庆", "校运会", "运动会", "迎新晚会",
                 "校园歌手", "辩论赛", "志愿", "义工", "表白墙", "社团活动", "活动"]
}

POSITIVE_WORDS = ["好", "棒", "优秀", "舒服", "满意", "推荐", "不错", "可以", "方便", "齐全", "优美", "干净", "划算",
                  "实惠", "喜欢", "爱了", "好评", "值得", "靠谱", "nice", "yyds", "完美", "贴心", "赞", "强", "nice",
                  "绝了"]
NEGATIVE_WORDS = ["差", "坑", "避雷", "烂", "贵", "吐槽", "不好", "垃圾", "劝退", "难吃", "吵", "远", "偏僻", "慢",
                  "卡", "无语", "后悔", "不值", "敷衍", "烂透", "恶心", "垃圾", "坑爹", "骗人", "差劲", "难受", "麻烦",
                  "严格"]

NEGATIVE_REASONS = {
    "食堂物价高": ["贵", "物价高", "太贵", "不划算", "性价比低", "饭贵", "菜贵"],
    "饭菜口味差": ["难吃", "不好吃", "口味差", "菜难吃", "饭难吃"],
    "校园网卡顿": ["校园网", "网卡", "网慢", "校园网差", "网速慢", "网费"],
    "小学期安排": ["小学期"],
    "交通不便利": ["偏僻", "远", "交通差", "不方便", "没地铁", "进城难", "偏远", "打车贵"],
    "宿舍管理严": ["门禁", "断电", "断网", "宿管严", "查寝", "管理严", "限制"],
    "地理位置偏": ["位置偏", "郊区", "乡下", "偏僻", "周边荒"],
}

POSITIVE_POINTS = {
    "上床下桌宿舍": ["上床下桌", "宿舍好", "宿舍大", "新宿舍", "宿舍条件好"],
    "学费性价比": ["学费不贵", "性价比高", "学费合理", "不贵"],
    "校园环境好": ["环境好", "漂亮", "绿化好", "校园美", "风景好"],
    "交通便利": ["交通方便", "近地铁", "出行方便"],
    "师资教学好": ["老师好", "教学好", "师资强", "课程好"],
    "校园活动多": ["活动多", "社团丰富", "活动好玩"],
}

STOP_WORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "们", "在", "也", "都", "就", "还", "有", "没", "不", "很",
    "啊", "呀", "吧", "呢", "吗", "哦", "哈", "哈哈", "哈哈哈", "这个", "那个", "什么", "怎么", "为什么", "觉得",
    "感觉", "就是", "还是", "但是", "因为", "所以", "如果", "可以", "一个", "一下", "真的", "其实", "现在", "已经",
    "不是", "广东东软学院", "东软学院", "东软", "学校", "学院", "还有", "时候", "这样", "那样", "这么", "那么",
    "自己", "大家", "人家", "别人", "东西", "事情", "地方", "问题", "情况", "真的", "非常", "特别", "比较",
    "捂脸", "流泪", "微笑", "表情", "doge", "笑哭", "点赞", "评论", "回复", "楼主", "层主"
}
MIN_VALID_LEN = 5


def timestamp_to_datetime(ts):
    """解析各平台五花八门的时间格式，失败返回 NaT

    兼容：
      - 10 位秒级 / 13 位毫秒级时间戳（数字或纯数字字符串）
      - "2026-10-15 12:00:00" / "2026/10/15 12:00" / "2026.10.15"
      - "2026年10月15日 12:00"
      - "2026-10-15T12:00:00+08:00"（ISO8601）
      - "10-15 12:00"（缺年份，按当前年补）
    """
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return pd.NaT
    if isinstance(ts, (pd.Timestamp,)):
        t = pd.Timestamp(ts)
        return t.tz_localize("Asia/Shanghai") if t.tzinfo is None else t.tz_convert("Asia/Shanghai")

    s = str(ts).strip()
    if not s or s.lower() in ("nan", "none", "null", "0"):
        return pd.NaT

    # 1) 纯数字 → 时间戳（10 位秒 / 13 位毫秒）
    if re.fullmatch(r"\d{10}|\d{13}", s):
        try:
            v = int(s)
            if len(s) == 13:
                v = v / 1000
            return pd.to_datetime(v, unit="s", utc=True).tz_convert("Asia/Shanghai")
        except Exception:
            return pd.NaT

    # 2) 中文字符串归一化为常见格式
    s2 = (s.replace("年", "-").replace("月", "-").replace("日", " ")
           .replace("/", "-").replace(".", "-").replace("T", " "))
    s2 = re.sub(r"\s+", " ", s2).strip()
    # 去掉尾部的时区描述：(CST) / +0800 / GMT+8 ...
    s2 = re.sub(r"(\(.*?\)|\s*[+-]\d{4}|\s*GMT[+-]?\d*)\s*$", "", s2).strip()
    try:
        t = pd.to_datetime(s2, errors="raise")
        if t.tzinfo is None:
            return t.tz_localize("Asia/Shanghai")
        return t.tz_convert("Asia/Shanghai")
    except Exception:
        pass

    # 3) 只有「月-日 [时:分]」，按当前年份补全
    m = re.match(r"^(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$", s2)
    if m:
        from datetime import datetime
        try:
            dt = datetime(datetime.now().year, int(m.group(1)), int(m.group(2)),
                          int(m.group(3) or 0), int(m.group(4) or 0), int(m.group(5) or 0))
            return pd.Timestamp(dt).tz_localize("Asia/Shanghai")
        except Exception:
            return pd.NaT

    return pd.NaT


def clean_text(text):
    """清洗文本：移除表情标签、空白字符、@用户"""
    text = str(text)
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"@\S+", "", text)
    text = re.sub(r"\s+", "", text)
    return text.strip()


def parse_count(v):
    """解析点赞数等可能带中文单位的数值，如 "2.4万"、"1.2千"、80、"3,000" """
    if v is None:
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        return int(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    if not s:
        return 0
    try:
        if s.endswith("万"):
            return int(float(s[:-1]) * 10000)
        if s.endswith("千"):
            return int(float(s[:-1]) * 1000)
        return int(float(s))
    except Exception:
        return 0


def is_valid_comment(text):
    """判断是否为有效评论（长度足够且包含有效字符）"""
    clean = clean_text(text)
    if len(clean) < MIN_VALID_LEN:
        return False
    if re.fullmatch(r"[^a-zA-Z0-9\u4e00-\u9fa5]+", clean):
        return False
    return True


def get_hit_dimensions(text):
    """获取评论命中的所有维度"""
    hit = []
    for dim, keywords in DIMENSION_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                hit.append(dim)
                break
    return hit


def get_sentiment(text):
    """简单规则情感判断：正面 / 中性 / 负面"""
    pos_count = sum(1 for w in POSITIVE_WORDS if w in text)
    neg_count = sum(1 for w in NEGATIVE_WORDS if w in text)
    if pos_count > neg_count:
        return "正面"
    elif neg_count > pos_count:
        return "负面"
    return "中性"


def get_negative_reasons(text):
    """获取负面评论的具体原因"""
    hit = []
    for reason, keywords in NEGATIVE_REASONS.items():
        for kw in keywords:
            if kw in text:
                hit.append(reason)
                break
    return hit


def get_positive_points(text):
    """获取正面评论的具体卖点"""
    hit = []
    for point, keywords in POSITIVE_POINTS.items():
        for kw in keywords:
            if kw in text:
                hit.append(point)
                break
    return hit


def extract_province(ip_text):
    """从 IP 属地提取省份，没有则返回未知"""
    if pd.isna(ip_text) or not str(ip_text).strip():
        return "未知"
    ip_text = str(ip_text).strip()
    provinces = [
        "广东", "广西", "湖南", "湖北", "河南", "河北", "山东", "山西",
        "江苏", "浙江", "福建", "江西", "安徽", "四川", "贵州", "云南",
        "陕西", "甘肃", "青海", "辽宁", "吉林", "黑龙江", "海南",
        "北京", "上海", "天津", "重庆", "内蒙古", "新疆", "西藏", "宁夏", "香港", "澳门", "台湾"
    ]
    for p in provinces:
        if p in ip_text:
            return p
    return "其他"


def get_box_data(series):
    """箱线图五数概括 [下边缘, Q1, 中位数, Q3, 上边缘]"""
    if len(series) == 0:
        return [0, 0, 0, 0, 0]
    s = series.sort_values().reset_index(drop=True)
    q1 = s.quantile(0.25)
    median = s.quantile(0.5)
    q3 = s.quantile(0.75)
    iqr = q3 - q1
    upper = min(q3 + 1.5 * iqr, s.max())
    lower = max(q1 - 1.5 * iqr, s.min())
    return [round(lower, 1), round(q1, 1), round(median, 1), round(q3, 1), round(upper, 1)]


def json_default(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError


def norm(a, b):
    m = max(a, b)
    if m == 0:
        return [0, 0]
    return [round(a / m * 100, 1), round(b / m * 100, 1)]


# ---------------- DeepSeek 调用 ----------------
def _deepseek_chat(prompt, temperature=0.3, timeout=90):
    """通用 DeepSeek 调用，返回解析后的 JSON 对象；失败返回 None"""
    if not DEEPSEEK_API_KEY or not DEEPSEEK_API_KEY.strip():
        return None
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "response_format": {"type": "json_object"}
    }
    try:
        resp = requests.post(DEEPSEEK_URL, json=payload, headers=headers, timeout=timeout)
        resp_json = resp.json()
        if "choices" not in resp_json or not resp_json["choices"]:
            log(f"DeepSeek 返回异常：{json.dumps(resp_json, ensure_ascii=False)[:300]}")
            return None
        content = resp_json["choices"][0]["message"]["content"]
        start = content.find("{")
        end = content.rfind("}") + 1
        if start == -1 or end <= start:
            log(f"DeepSeek 返回非 JSON：{content[:200]}")
            return None
        return json.loads(content[start:end])
    except Exception as e:
        log(f"DeepSeek 调用异常：{str(e)}")
        return None


# ---------------- DeepSeek AI 分析函数 ----------------
def generate_ai_insight_by_deepseek(stat_data):
    """为每张图表生成简短 AI 辅助分析说明（14 项）"""
    prompt = f"""
你是校园社交舆情分析专家，根据下面的统计数据，为每一张图表生成简短的AI辅助分析说明。
要求：每段文字简短，1-3句话，面向学校舆情工作人员；不要markdown，不要标题，只输出JSON对象。
注意：平台名称必须以统计数据里「平台分布」字段中出现过的平台为准，不要凭空添加未见过的平台。

统计数据：
{json.dumps(stat_data, ensure_ascii=False, indent=2)}

严格只返回如下JSON对象，字段名固定，不要输出任何额外解释文字：
{{
  "platform":"平台分布饼图分析",
  "length":"评论长度分布分析",
  "like":"点赞箱线图分析",
  "month":"月度热度趋势分析",
  "hour":"24小时活跃度分析",
  "week":"周分布分析",
  "dimensionBar":"七大维度热度柱状图分析",
  "dimensionRadar":"维度情感雷达图分析",
  "sentimentPie":"整体情感饼图分析",
  "pareto":"负面帕累托图分析",
  "posBar":"正面卖点条形图分析",
  "wordCloud":"词云分析",
  "sentLike":"不同情感平均点赞分析",
  "platformCompare":"平台对比雷达分析"
}}
"""
    return _deepseek_chat(prompt, temperature=0.3)


def generate_ai_report_by_deepseek(stat_data):
    """生成综合研判报告（结论 / 决策建议 / 深度讨论 / 平台策略等）"""
    prompt = f"""
你是高校舆情分析专家。根据下面的统计数据，生成一份完整的舆情研判报告（面向学校舆情与招生宣传工作人员）。
要求：
1. 语言专业、客观、可落地，每段内容1-3句话，避免空话套话；
2. 不要markdown，不要额外标题，不要编号，只输出JSON对象；
3. conclusions 固定输出5条结论，suggestions 固定输出4条建议。

统计数据：
{json.dumps(stat_data, ensure_ascii=False, indent=2)}

严格按如下字段输出JSON（字段名固定，不可更改）：
{{
  "timeDiscussion": [
    {{"title":"周期规律成因","content":"..."}},
    {{"title":"时段传播策略","content":"..."}},
    {{"title":"风险窗口期","content":"..."}}
  ],
  "dimensionInsight": [
    {{"title":"口碑长板","content":"..."}},
    {{"title":"舆情短板","content":"..."}},
    {{"title":"提升空间","content":"..."}}
  ],
  "keywordInterpretation": [
    {{"title":"生活类","content":"..."}},
    {{"title":"报考类","content":"..."}},
    {{"title":"区位类","content":"..."}},
    {{"title":"学业类","content":"..."}}
  ],
  "userInsight": [
    {{"title":"传播规律","content":"..."}},
    {{"title":"意见领袖","content":"..."}},
    {{"title":"风险提示","content":"..."}}
  ],
  "platformStrategy": {{
    "douyin": {{"feature":"...","advantage":"...","risk":"...","strategy":"..."}},
    "xiaohongshu": {{"feature":"...","advantage":"...","risk":"...","strategy":"..."}}
  }},
  "conclusions": ["结论1","结论2","结论3","结论4","结论5"],
  "suggestions": [
    {{"category":"招生宣传","content":"..."}},
    {{"category":"运营优化","content":"..."}},
    {{"category":"舆情管理","content":"..."}},
    {{"category":"用户运营","content":"..."}}
  ]
}}
"""
    return _deepseek_chat(prompt, temperature=0.5)


def _top_key(mapping, default=""):
    """取字典里数值最大的键"""
    try:
        return max(mapping.items(), key=lambda kv: kv[1])[0]
    except Exception:
        return default


def _fmt_plat(plat_count):
    """平台分布 → 文字"""
    if not plat_count:
        return "暂无平台数据"
    return "、".join(f"{k} {v} 条" for k, v in plat_count.items())


def _first_real(pairs):
    """取第一个「有意义」的项，跳过「其他」这类兜底分类"""
    pairs = list(pairs or [])
    for name, cnt in pairs:
        if str(name).strip() not in ("其他", "其它", "未知", ""):
            return name, cnt
    return pairs[0] if pairs else ("无", 0)


def get_fallback_insight(ctx=None):
    """本地 AI 兜底：用本次分析的**真实统计**生成图表解读

    重点：文案由数据算出，换一批数据结论就会跟着变，
    不会出现「数据换了、分析还是一模一样」的情况。
    """
    c = ctx or {}
    if not c:
        # 极端兜底（理论上不会走到）
        return {
            "platform": "暂无平台数据。", "length": "暂无评论长度数据。", "like": "暂无点赞数据。",
            "month": "暂无月度数据。", "hour": "暂无时段数据。", "week": "暂无周分布数据。",
            "dimensionBar": "暂无维度数据。", "dimensionRadar": "暂无维度情感数据。",
            "sentimentPie": "暂无情感数据。", "pareto": "暂无负面归因数据。",
            "posBar": "暂无正向卖点数据。", "wordCloud": "暂无高频词数据。",
            "sentLike": "暂无点赞与情感关联数据。", "platformCompare": "暂无平台对比数据。"
        }

    total = int(c.get("total") or 0)
    pos_rate = float(c.get("pos_rate") or 0)
    neg_rate = float(c.get("neg_rate") or 0)
    neu_rate = round(max(0.0, 100 - pos_rate - neg_rate), 1)
    plat_count = c.get("plat_count") or {}
    plat_pos = c.get("plat_pos") or {}

    # 评论长度
    len_labels = c.get("len_labels") or []
    len_count = c.get("len_count") or []
    if len_labels and len_count:
        i = max(range(len(len_count)), key=lambda k: len_count[k])
        top_len_label, top_len_cnt = len_labels[i], int(len_count[i])
        top_len_rate = round(top_len_cnt / total * 100, 1) if total else 0
    else:
        top_len_label, top_len_cnt, top_len_rate = "未知", 0, 0

    # 点赞
    like_mean = float(c.get("like_mean") or 0)
    like_median = float(c.get("like_median") or 0)
    like_top_share = float(c.get("like_top_share") or 0)

    # 时间
    month_x = c.get("month_x") or []
    month_total = c.get("month_total") or []
    if month_x and month_total:
        mi = max(range(len(month_total)), key=lambda k: month_total[k])
        peak_month, peak_month_cnt = month_x[mi], int(month_total[mi])
        low_month = month_x[min(range(len(month_total)), key=lambda k: month_total[k])]
    else:
        peak_month, peak_month_cnt, low_month = "无", 0, "无"

    hour_count = c.get("hour_count") or []
    if hour_count:
        h_sorted = sorted(range(len(hour_count)), key=lambda k: -hour_count[k])
        peak_hours = [h_sorted[0]] + ([h_sorted[1]] if len(h_sorted) > 1 else [])
        peak_hour_cnt = int(hour_count[h_sorted[0]])
    else:
        peak_hours, peak_hour_cnt = [0], 0

    weekday_names = c.get("weekday_names") or []
    weekday_count = c.get("weekday_count") or []
    if weekday_names and weekday_count:
        wi = max(range(len(weekday_count)), key=lambda k: weekday_count[k])
        peak_week, peak_week_cnt = weekday_names[wi], int(weekday_count[wi])
    else:
        peak_week, peak_week_cnt = "无", 0

    # 维度
    dim_pairs = c.get("dim_pairs") or []
    dim_pos = c.get("dim_pos") or {}
    dim_neg = c.get("dim_neg") or {}
    if dim_pairs:
        top_dim, top_dim_cnt = dim_pairs[0][0], int(dim_pairs[0][1])
        top3 = "、".join(f"{n}（{int(v)} 条）" for n, v in dim_pairs[:3])
    else:
        top_dim, top_dim_cnt, top3 = "无", 0, "无"
    hi_neg_dim = _top_key(dim_neg, "-")
    hi_pos_dim = _top_key(dim_pos, "-")
    hi_neg_rate = dim_neg.get(hi_neg_dim, 0) if hi_neg_dim != "-" else 0
    hi_pos_rate = dim_pos.get(hi_pos_dim, 0) if hi_pos_dim != "-" else 0

    # 负面归因 / 正面卖点
    neg_pairs = c.get("neg_pairs") or []
    pos_pairs = c.get("pos_pairs") or []
    if neg_pairs:
        neg_top, _neg_c = _first_real(neg_pairs)
        neg_top_cnt = int(_neg_c)
        neg_top_share = round(neg_top_cnt / max(1, c.get("neg_total") or 1) * 100, 1)
        neg_first2 = int(sum(v for _, v in neg_pairs[:2]))
        neg_first2_rate = round(neg_first2 / max(1, c.get("neg_total") or 1) * 100, 1)
    else:
        neg_top, neg_top_cnt, neg_top_share, neg_first2_rate = "无", 0, 0, 0
    if pos_pairs:
        pos_top, _pos_c = _first_real(pos_pairs)
        pos_top_cnt = int(_pos_c)
    else:
        pos_top, pos_top_cnt = "无", 0

    # 词云
    words = c.get("words") or []
    word_text = "、".join(f"{w['name']}({w['value']})" for w in words[:6]) if words else "无"

    # 情感与点赞
    sent_like = c.get("sent_like") or {}
    best_sent = _top_key(sent_like, "-")
    best_sent_like = sent_like.get(best_sent, 0) if best_sent != "-" else 0

    # 情感大盘判断
    if pos_rate >= max(40.0, neg_rate * 2.5):
        mood = "整体口碑偏正面"
    elif neg_rate > pos_rate:
        mood = "负面声量已高于正面，需要重点干预"
    else:
        mood = "整体以中性讨论为主，情绪尚未明显分化"

    # 平台
    plat_names = list(plat_count.keys())
    if len(plat_names) >= 2:
        p0, p1 = plat_names[0], plat_names[1]
        r0, r1 = plat_pos.get(p0, 0), plat_pos.get(p1, 0)
        better = p0 if r0 >= r1 else p1
        worse = p1 if better == p0 else p0
        platform_text = (
            f"本次分析共 {total} 条有效评论：{_fmt_plat(plat_count)}。"
            f"{p0} 评论量最多，是本次舆情的主要阵地；正面占比方面 {better} 为 "
            f"{max(r0, r1)}%，高于 {worse} 的 {min(r0, r1)}%，正向内容可优先在 {better} 放大。"
        )
        compare_text = (
            f"{p0}正面占比 {r0}%、{p1}正面占比 {r1}%；"
            f"评论量更大的 {p0} 应承担主要监测与响应压力，{better} 更适合做口碑沉淀与种草。"
        )
    elif len(plat_names) == 1:
        p0 = plat_names[0]
        platform_text = (
            f"本次分析仅包含「{p0}」单个平台共 {total} 条有效评论，为单平台视角；"
            f"如需平台对比结论，建议补传另一平台数据后重新分析。"
        )
        compare_text = (
            f"当前只有「{p0}」一个平台的数据，对比图表仅作该平台的指标画像使用，"
            f"涉及平台差异的结论需在补齐数据后再判断。"
        )
    else:
        platform_text, compare_text = "暂无平台数据。", "暂无平台对比数据。"

    return {
        "platform": platform_text,
        "length": (
            f"评论以「{top_len_label}」为主，共 {top_len_cnt} 条，占 {top_len_rate}%；"
            + ("短评占比高，情绪表达偏碎片化，需结合高赞评论判断真实诉求。"
               if top_len_label in ("1-10字", "11-30字")
               else "长评论占比较高，用户愿意展开描述，适合做深度就读体验挖掘。")
        ),
        "like": (
            f"平均点赞 {like_mean:.1f}、中位数 {like_median:.0f}；"
            f"点赞前 10% 的评论贡献了全部点赞的 {like_top_share:.1f}%，"
            + ("头部效应显著，重点盯住高赞内容即可覆盖主要舆论影响面。"
               if like_top_share >= 60 else "点赞分布相对分散，需要按话题而非单条内容做监测。")
        ),
        "month": (
            f"评论量最高的是 {peak_month}（{peak_month_cnt} 条），最低的是 {low_month}；"
            f"共覆盖 {len(month_x)} 个月份，热度随校历与招生周期波动，招生季与开学季应提前布置监测。"
        ),
        "hour": (
            f"活跃高峰出现在 {'、'.join(str(h) + '点' for h in peak_hours)}，高峰时段评论量 {peak_hour_cnt} 条；"
            f"官方回应与宣传投放建议安排在高峰前 1-2 小时，才能在扩散前完成引导。"
        ),
        "week": (
            f"{peak_week}评论量最高（{peak_week_cnt} 条），发言主体以在校工作日的学生群体为主，"
            f"周末活跃度相对回落，适合做长内容沉淀与素材准备。"
        ),
        "dimensionBar": (
            f"讨论热度最高的是「{top_dim}」（{top_dim_cnt} 条），前三为 {top3}；"
            f"热度高不等于口碑好，需结合情感雷达判断是卖点还是痛点。"
        ),
        "dimensionRadar": (
            f"负面占比最高的是「{hi_neg_dim}」（{hi_neg_rate}%），正面占比最高的是「{hi_pos_dim}」（{hi_pos_rate}%）；"
            f"前者是优先整改的痛点，后者可作为招生宣传的核心卖点。"
        ),
        "sentimentPie": (
            f"有效评论 {total} 条：正面 {pos_rate}%、负面 {neg_rate}%、中性 {neu_rate}%，{mood}；"
            f"负面共 {int(c.get('neg_total') or 0)} 条，是舆情响应的主要观测对象。"
        ),
        "pareto": (
            f"排名第一的负面诱因是「{neg_top}」（{neg_top_cnt} 次，占负面评论 {neg_top_share}%），"
            f"前两项合计占 {neg_first2_rate}%；先处理这两项即可覆盖大部分负面声量。"
        ),
        "posBar": (
            f"提及最多的正向卖点是「{pos_top}」（{pos_top_cnt} 次），"
            f"可作为招生宣传与内容投放的核心素材反复使用。"
        ),
        "wordCloud": (
            f"高频词依次为 {word_text}；词语分布与维度排行相互印证，可直接作为内容运营的关键词库。"
        ),
        "sentLike": (
            f"平均点赞最高的是「{best_sent}」类评论（{best_sent_like:.1f} 赞）；"
            f"说明该类表达最容易被传播，官方内容可参考其语气与信息密度。"
        ),
        "platformCompare": compare_text
    }


def get_fallback_report(ctx=None):
    """综合报告本地兜底：同样由本次真实统计数据生成"""
    c = ctx or {}
    if not c:
        return {"timeDiscussion": [], "dimensionInsight": [], "keywordInterpretation": [],
                "userInsight": [], "platformStrategy": {}, "conclusions": [], "suggestions": []}

    total = int(c.get("total") or 0)
    pos_rate = float(c.get("pos_rate") or 0)
    neg_rate = float(c.get("neg_rate") or 0)
    plat_count = c.get("plat_count") or {}
    dim_pairs = c.get("dim_pairs") or []
    dim_pos = c.get("dim_pos") or {}
    dim_neg = c.get("dim_neg") or {}
    neg_pairs = c.get("neg_pairs") or []
    pos_pairs = c.get("pos_pairs") or []
    month_x = c.get("month_x") or []
    month_total = c.get("month_total") or []
    hour_count = c.get("hour_count") or []
    weekday_names = c.get("weekday_names") or []
    weekday_count = c.get("weekday_count") or []
    words = c.get("words") or []
    like_top_share = float(c.get("like_top_share") or 0)
    active_share = float(c.get("active_user_share") or 0)
    max_neg_like = int(c.get("max_neg_like") or 0)

    top_dim = dim_pairs[0][0] if dim_pairs else "无"
    top_dim_cnt = int(dim_pairs[0][1]) if dim_pairs else 0
    neg_dim = _top_key(dim_neg, "无")
    pos_dim = _top_key(dim_pos, "无")
    neg_top, _neg_c = _first_real(neg_pairs)
    neg_top_cnt = int(_neg_c)
    pos_top, _pos_c = _first_real(pos_pairs)
    pos_top_cnt = int(_pos_c)
    peak_hours = []
    if hour_count:
        hs = sorted(range(len(hour_count)), key=lambda k: -hour_count[k])
        peak_hours = hs[:2]
    peak_week = weekday_names[max(range(len(weekday_count)), key=lambda k: weekday_count[k])] if weekday_count else "无"
    peak_month = month_x[max(range(len(month_total)), key=lambda k: month_total[k])] if month_total else "无"
    word_text = "、".join(w["name"] for w in words[:8]) if words else "无"

    # 平台策略：只输出本次真实存在的平台
    platform_strategy = {}
    for plat, cnt in plat_count.items():
        if plat == "抖音":
            platform_strategy["douyin"] = {
                "feature": f"本次抖音有效评论 {cnt} 条，传播速度快、覆盖人群广、家长与考生占比高",
                "advantage": f"评论量级大（{cnt} 条），招生政策科普与校园宣传触达面广，爆款效应强",
                "risk": f"负面内容扩散速度快，本次负面占比 {neg_rate}%，高赞负面需第一时间响应",
                "strategy": f"重点投放招生与校园生活短视频，围绕「{pos_top}」做正面内容，建立高赞负面快速响应机制"
            }
        elif plat == "小红书":
            platform_strategy["xiaohongshu"] = {
                "feature": f"本次小红书有效评论 {cnt} 条，种草属性强、用户以考生与在校生为主、长尾效应明显",
                "advantage": f"就读体验、宿舍环境、校园生活类内容转化率高，关于「{pos_top}」的正面内容口碑沉淀久",
                "risk": f"负面体验帖会长期留存并影响报考决策，本次负面占比 {neg_rate}%，需定期维护",
                "strategy": f"引导在校生产出真实体验内容，针对「{neg_top}」类吐槽主动回应，打造校园生活种草矩阵"
            }

    # 建议里的平台措辞跟着本次数据里实际存在的平台走
    if len(plat_count) >= 2:
        platform_tip = "抖音做政策科普、小红书做生活种草"
    elif "抖音" in plat_count:
        platform_tip = "在抖音集中做政策科普与校园生活短视频"
    elif "小红书" in plat_count:
        platform_tip = "在小红书集中做生活体验种草"
    else:
        platform_tip = "按平台特性做差异化投放"

    return {
        "timeDiscussion": [
            {"title": "周期规律成因",
             "content": f"本次数据覆盖 {len(month_x)} 个月份，{peak_month}评论量最高。舆情热度与招生、校历周期高度相关，"
                        f"志愿填报与开学季是流量高峰，考生、家长与在校生是核心参与群体。"},
            {"title": "时段传播策略",
             "content": f"活跃高峰集中在 {'、'.join(str(h) + '点' for h in peak_hours) or '暂无数据'}，"
                        f"{peak_week}的讨论量最高；建议宣传内容在高峰前投放，答疑类内容放在午间可获更高曝光。"},
            {"title": "风险窗口期",
             "content": f"当前风险等级为「{c.get('risk', '未知')}」，单日负面峰值 {c.get('max_daily_neg', 0)} 条、"
                        f"单条负面最高点赞 {max_neg_like}；开学季前后校园服务问题易集中爆发，需提前准备回应话术。"}
        ],
        "dimensionInsight": [
            {"title": "口碑长板",
             "content": f"「{pos_dim}」正面占比最高（{dim_pos.get(pos_dim, 0)}%），用户认可度集中，"
                        f"适合作为招生宣传的核心抓手，配合「{pos_top}」类卖点放大传播。"},
            {"title": "舆情短板",
             "content": f"「{neg_dim}」负面占比相对最高（{dim_neg.get(neg_dim, 0)}%），"
                        f"主要吐槽点集中在「{neg_top}」（{neg_top_cnt} 次），是需优先优化的痛点。"},
            {"title": "提升空间",
             "content": f"讨论热度最高的「{top_dim}」（{top_dim_cnt} 条）若正面占比不高，说明关注度未转化为口碑，"
                        f"可通过强化就业优势、产教融合等特色内容提升正向感知。"}
        ],
        "keywordInterpretation": [
            {"title": "高频词",
             "content": f"本次高频词为 {word_text}，是用户最直接的关注点，可作为内容选题与标题的关键词来源。"},
            {"title": "话题集中度",
             "content": f"讨论最集中的维度是「{top_dim}」，单维度就有 {top_dim_cnt} 条提及，"
                        f"占全部有效评论的 {(round(top_dim_cnt / total * 100, 1) if total else 0)}%，说明议题相对聚焦。"},
            {"title": "负面指向",
             "content": f"负面提及最多的原因是「{neg_top}」，共 {neg_top_cnt} 次，可作为整改与回应清单的第一条。"},
            {"title": "表达特征",
             "content": f"词云以短词、口语化表达为主，说明用户更习惯用生活化语言讨论学校；"
                        f"{'长评占比不高，深度反馈需要通过问卷或访谈补充。' if total else ''}"}
        ],
        "userInsight": [
            {"title": "传播规律",
             "content": f"点赞前 10% 的评论贡献了全部点赞的 {like_top_share:.1f}%，"
                        f"高赞内容对舆论的影响远大于普通评论，监测重心应放在高赞内容上。"},
            {"title": "意见领袖",
             "content": f"评论量前 8 的活跃用户贡献了 {active_share:.1f}% 的评论，"
                        f"对话题走向有引导作用，可重点维护正向活跃用户、跟进吐槽用户诉求。"},
            {"title": "风险提示",
             "content": f"当前单条负面最高点赞 {max_neg_like}，负面内容一旦进入高赞序列扩散极快，"
                        f"需建立常态化的高赞内容巡检机制。"}
        ],
        "platformStrategy": platform_strategy,
        "conclusions": [
            f"本次共分析 {total} 条有效评论（{_fmt_plat(plat_count)}），正面 {pos_rate}%、负面 {neg_rate}%，"
            f"{'舆情整体平稳' if neg_rate < pos_rate else '负面声量偏高，需重点干预'}。",
            f"讨论热度最高的是「{top_dim}」，正面感知最强的是「{pos_dim}」，这是学校最值得放大的口碑长板。",
            f"「{neg_dim}」是负面占比最高的维度，核心诱因是「{neg_top}」，优先整改可显著降低负面舆情。",
            f"{peak_month}是这段时间的热度高点，{peak_week}与{'、'.join(str(h) + '点' for h in peak_hours)}是用户活跃窗口，"
            f"宣传与回应应卡在这两个节奏上。",
            f"点赞头部效应明显（前 10% 评论占 {like_top_share:.1f}% 点赞），"
            f"少数高赞内容与活跃用户主导舆情走向，是监测与引导的关键节点。"
        ],
        "suggestions": [
            {"category": "招生宣传",
             "content": f"把「{pos_top}」作为核心卖点，在 {peak_month if peak_month != '无' else '招生季'}集中投放；{platform_tip}。"},
            {"category": "运营优化",
             "content": f"优先处理「{neg_top}」相关诉求（当前 {neg_top_cnt} 次提及），"
                        f"给出可感知的整改动作并公开反馈进度。"},
            {"category": "舆情管理",
             "content": f"以「{neg_dim}」为监测重点，对点赞量高的负面内容设置预警，"
                        f"在活跃高峰前完成回应。"},
            {"category": "用户运营",
             "content": f"培育正向校园 KOC，引导在校生产出真实体验内容；"
                        f"对贡献评论量前 8 的活跃用户跟进诉求，避免情绪累积。"}
        ]
    }


def generate_hot_content_analysis(hot_data):
    """生成「高赞评论 + 活跃用户」的内容分析总结（面向舆情工作人员）"""
    prompt = f"""
你是校园舆情分析专家。根据下面的数据，生成两段简短的舆情解读总结。

要求：
1. topLikeSummary：总结「点赞量大的评论」主要在讲什么——它们反映了用户哪些核心关注点、诉求或情绪；
2. activeUserSummary：总结「评论最多的用户」大概在讲什么——他们的身份特征（如考生/家长/在校生）、主要关注方向与情绪倾向；
3. 每段1-3句话，客观具体、有信息量；不要markdown、不要额外文字、不要编号，只输出JSON对象。

数据：
{json.dumps(hot_data, ensure_ascii=False, indent=2)}

严格只返回如下JSON对象（字段名固定）：
{{
  "topLikeSummary": "...",
  "activeUserSummary": "..."
}}
"""
    return _deepseek_chat(prompt, temperature=0.4)


def get_fallback_hot_content(ctx=None):
    """高赞评论 + 活跃用户内容分析本地兜底（同样基于本次真实数据）"""
    c = ctx or {}
    comments = c.get("top_like_comments") or []
    users = c.get("active_users") or []

    if comments:
        sent_cnt = {}
        like_sum = 0
        for item in comments:
            s = item.get("sentiment") or "中性"
            sent_cnt[s] = sent_cnt.get(s, 0) + 1
            try:
                like_sum += int(item.get("like", 0) or 0)
            except Exception:
                pass
        main_sent = max(sent_cnt.items(), key=lambda kv: kv[1])[0]
        avg_like = round(like_sum / max(1, len(comments)))
        top_like_summary = (
            f"点赞量最高的 {len(comments)} 条评论中，{main_sent}类占 {sent_cnt.get(main_sent, 0)} 条，"
            f"平均点赞 {avg_like}；高赞内容以「{main_sent}」表达为主，是舆论影响面最大的部分，"
            f"负面高赞一旦出现需第一时间跟进。"
        )
    else:
        top_like_summary = "本次数据中没有可用的高赞评论样本。"

    if users:
        plat_cnt = {}
        sent_cnt2 = {}
        for u in users:
            p = u.get("platform") or "未知平台"
            plat_cnt[p] = plat_cnt.get(p, 0) + 1
            s = u.get("sentiment") or "中性"
            sent_cnt2[s] = sent_cnt2.get(s, 0) + 1
        main_plat = max(plat_cnt.items(), key=lambda kv: kv[1])[0]
        main_sent2 = max(sent_cnt2.items(), key=lambda kv: kv[1])[0]
        active_user_summary = (
            f"评论最活跃的 {len(users)} 位用户主要来自「{main_plat}」，情绪以「{main_sent2}」为主；"
            f"他们贡献了本次有效评论的 {c.get('active_user_share', 0)}%，是话题扩散的关键节点，"
            f"可优先维护正向用户、跟进吐槽用户诉求。"
        )
    else:
        active_user_summary = "本次数据中没有识别出稳定活跃的评论用户。"

    return {"topLikeSummary": top_like_summary, "activeUserSummary": active_user_summary}


def _merge_report(fallback, ai):
    """以 fallback 为基底，用 AI 结果覆盖，保证字段完整"""
    result = dict(fallback)
    if not isinstance(ai, dict):
        return result
    for key in ["timeDiscussion", "dimensionInsight", "keywordInterpretation", "userInsight", "conclusions", "suggestions"]:
        if isinstance(ai.get(key), list) and len(ai[key]) > 0:
            result[key] = ai[key]
    if isinstance(ai.get("platformStrategy"), dict):
        result["platformStrategy"] = ai["platformStrategy"]
    return result


def run_analysis(only_files=None):
    try:
        log("====开始执行舆情分析任务====")
        if only_files:
            log(f"本次分析范围：{len(only_files)} 个文件 → {', '.join(only_files)}")
        all_df_list = []
        has_ip = False
        for platform, filename in scan_raw_files(only_files):
            if not os.path.exists(filename):
                log(f"跳过缺失文件：{filename}")
                continue
            with open(filename, "r", encoding="utf-8") as f:
                data = _extract_rows(json.load(f))
            if not data:
                log(f"跳过空文件：{os.path.basename(filename)}")
                continue
            df = pd.DataFrame(data)
            if df.empty:
                log(f"跳过无有效行文件：{os.path.basename(filename)}")
                continue
            df["platform"] = platform
            log(f"  ✅ {platform}：读取到 {len(df)} 条原始评论")
            ip_fields = ["ip_location", "ip_loc", "ip", "ip_address"]
            for f in ip_fields:
                if f in df.columns:
                    has_ip = True
                    break
            all_df_list.append(df)

        if not all_df_list:
            return {"ok": False, "msg": "没有找到原始评论json，请检查raw_data目录！"}

        field_map = {
            "comment_id": "comment_id",
            "content": "content",
            "create_time": "create_time",
            "nickname": "nickname",
            "like_count": "like_count",
            "ip_loc": "ip_location",
            "ip_location": "ip_location",
            "ip": "ip_location"
        }
        cleaned_dfs = []
        for df in all_df_list:
            rename_dict = {k: v for k, v in field_map.items() if k in df.columns}
            df = df.rename(columns=rename_dict)
            keep_cols = [c for c in
                         ["comment_id", "content", "create_time", "nickname", "like_count", "ip_location", "platform"] if
                         c in df.columns]
            df = df[keep_cols].copy()
            cleaned_dfs.append(df)
        all_data = pd.concat(cleaned_dfs, ignore_index=True)
        total_original = len(all_data)
        log(f"合并后原始数据共：{total_original} 条")

        if "comment_id" in all_data.columns:
            all_data = all_data.drop_duplicates(subset=["comment_id"], keep="first").reset_index(drop=True)
        else:
            all_data = all_data.drop_duplicates(subset=["content"], keep="first").reset_index(drop=True)
        log(f"去重后剩余：{len(all_data)} 条")

        all_data["content"] = all_data["content"].astype(str).str.strip()
        all_data = all_data[all_data["content"].str.len() > 0].reset_index(drop=True)
        log(f"去除空评论后剩余：{len(all_data)} 条")

        # 点赞数统一解析（兼容 "2.4万" / "1.2千" / 纯数字）
        if "like_count" not in all_data.columns:
            all_data["like_count"] = 0
        all_data["like_count"] = all_data["like_count"].apply(parse_count)

        if "create_time" in all_data.columns:
            all_data["publish_time"] = all_data["create_time"].apply(timestamp_to_datetime)
            valid_time_count = all_data["publish_time"].notna().sum()
            all_data = all_data[all_data["publish_time"].notna()].reset_index(drop=True)
            log(f"有效时间转换：{valid_time_count} 条")
            all_data["month"] = all_data["publish_time"].dt.strftime("%Y-%m")
            all_data["hour"] = all_data["publish_time"].dt.hour
            all_data["weekday"] = all_data["publish_time"].dt.weekday
        else:
            log("⚠️ 未检测时间字段")
            all_data["month"] = "未知"
            all_data["hour"] = 0
            all_data["weekday"] = 0

        all_data["is_valid"] = all_data["content"].apply(is_valid_comment)
        valid_data = all_data[all_data["is_valid"]].reset_index(drop=True)
        log(f"过滤水评论后剩余：{len(valid_data)} 条")

        valid_data["content_clean"] = valid_data["content"].apply(clean_text)
        valid_data["content_len"] = valid_data["content_clean"].str.len()
        valid_data["dimensions"] = valid_data["content_clean"].apply(get_hit_dimensions)
        valid_data["is_related"] = valid_data["dimensions"].apply(lambda x: len(x) > 0)
        final_data = valid_data[valid_data["is_related"]].reset_index(drop=True)
        log(f"主题筛选后最终有效数据：{len(final_data)} 条")
        for _p, _c in final_data["platform"].value_counts().items():
            log(f"    - {_p}：{int(_c)} 条")

        if len(final_data) == 0:
            return {
                "ok": False,
                "msg": "数据清洗后没有有效评论。请检查两点：①评论内容是否为校园相关文本"
                       "（需命中宿舍/食堂/招生/环境/教学/交通/活动等主题词）；"
                       "②create_time 是否为可解析的时间（支持时间戳或 2026-10-15 12:00:00 这类格式）。"
            }

        final_data["sentiment"] = final_data["content_clean"].apply(get_sentiment)
        final_data["negative_reasons"] = final_data["content_clean"].apply(get_negative_reasons)
        final_data["positive_points"] = final_data["content_clean"].apply(get_positive_points)
        if has_ip and "ip_location" in final_data.columns:
            final_data["province"] = final_data["ip_location"].apply(extract_province)
            log("✅ IP属地提取完成")
        else:
            final_data["province"] = "未知"
            log("⚠️ 无IP字段")

        total_final = len(final_data)
        pos_num = len(final_data[final_data["sentiment"] == "正面"])
        neg_num = len(final_data[final_data["sentiment"] == "负面"])
        neu_num = len(final_data[final_data["sentiment"] == "中性"])
        pos_rate = round(pos_num / total_final * 100, 1) if total_final else 0
        neg_rate = round(neg_num / total_final * 100, 1) if total_final else 0
        total_likes = int(final_data["like_count"].sum())

        plat_count_series = final_data["platform"].value_counts()
        plat_count = {k: int(v) for k, v in plat_count_series.to_dict().items()}
        # 平台分布饼图：按实际出现的平台动态生成，不再写死两个平台
        platform_pie = [{"value": int(cnt), "name": plat}
                        for plat, cnt in plat_count_series.items()]

        bins = [0, 10, 30, 50, 100, 9999]
        len_labels = ["1-10字", "11-30字", "31-50字", "51-100字", "100字以上"]
        len_cut = pd.cut(final_data["content_len"], bins=bins, labels=len_labels, right=True)
        len_count = len_cut.value_counts().reindex(len_labels).fillna(0).astype(int).tolist()

        # 平台顺序（按有效评论量降序），对比类图表取前两个平台
        plat_order = list(plat_count_series.index)
        main_plat = plat_order[0] if plat_order else ""
        sub_plat = plat_order[1] if len(plat_order) > 1 else ""
        plat_compare_names = [p for p in (main_plat, sub_plat) if p]

        dy_df = final_data[final_data["platform"] == main_plat] if main_plat else final_data.iloc[0:0]
        xhs_df = final_data[final_data["platform"] == sub_plat] if sub_plat else final_data.iloc[0:0]
        dy_likes = dy_df["like_count"]
        xhs_likes = xhs_df["like_count"]
        # 点赞分布箱线图：按平台数量动态生成（最多 4 个，避免横轴拥挤）
        box_plats = plat_order[:4]
        like_box_data = [get_box_data(final_data[final_data["platform"] == p]["like_count"]) for p in box_plats]
        like_box_x = box_plats

        month_stat = final_data.groupby("month").agg(
            total=("comment_id", "count"),
            positive=("sentiment", lambda x: (x == "正面").sum()),
            negative=("sentiment", lambda x: (x == "负面").sum())
        ).sort_index()
        month_x = [m.split('-')[1].lstrip('0') + '月' for m in month_stat.index]
        month_total = [int(v) for v in month_stat['total']]
        month_positive = [int(v) for v in month_stat['positive']]
        month_negative = [int(v) for v in month_stat['negative']]

        hour_count = final_data.groupby("hour").size().reindex(range(24), fill_value=0).tolist()
        hour_x = [f"{i}点" for i in range(0, 24, 2)]
        hour_trend = hour_count[::2]

        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday_count = final_data.groupby("weekday").size().reindex(range(7), fill_value=0).tolist()

        dim_count_dict = {}
        for dim in DIMENSION_KEYWORDS.keys():
            cnt = final_data["dimensions"].apply(lambda x: dim in x).sum()
            dim_count_dict[dim] = int(cnt)
        dim_sorted = sorted(dim_count_dict.items(), key=lambda x: x[1], reverse=True)
        dim_x = [x[0] for x in dim_sorted]
        dim_bar = [x[1] for x in dim_sorted]

        dim_pos_rate = []
        dim_neg_rate = []
        for dim in DIMENSION_KEYWORDS.keys():
            dim_data = final_data[final_data["dimensions"].apply(lambda x: dim in x)]
            if len(dim_data) == 0:
                dim_pos_rate.append(0)
                dim_neg_rate.append(0)
                continue
            pos_r = round((dim_data["sentiment"] == "正面").sum() / len(dim_data) * 100, 1)
            neg_r = round((dim_data["sentiment"] == "负面").sum() / len(dim_data) * 100, 1)
            dim_pos_rate.append(pos_r)
            dim_neg_rate.append(neg_r)

        sentiment_pie = [
            {"value": pos_num, "name": "正面评价"},
            {"value": neu_num, "name": "中性评价"},
            {"value": neg_num, "name": "负面评价"}
        ]

        neg_data = final_data[final_data["sentiment"] == "负面"]
        all_neg_reasons = []
        for reasons in neg_data["negative_reasons"]:
            all_neg_reasons.extend(reasons)
        reason_count = Counter(all_neg_reasons).most_common()
        other_count = len(neg_data) - sum(c for _, c in reason_count)
        if other_count > 0:
            reason_count.append(("其他", other_count))
        neg_pareto_x = [x[0] for x in reason_count]
        neg_pareto_bar = [x[1] for x in reason_count]
        cum_rate = []
        total_neg = sum(neg_pareto_bar)
        cum = 0
        for n in neg_pareto_bar:
            cum += n
            cum_rate.append(round(cum / total_neg * 100, 1) if total_neg else 0)

        pos_data = final_data[final_data["sentiment"] == "正面"]
        all_pos_points = []
        for points in pos_data["positive_points"]:
            all_pos_points.extend(points)
        point_count = Counter(all_pos_points).most_common()
        other_pos = len(pos_data) - sum(c for _, c in point_count)
        if other_pos > 0:
            point_count.append(("其他", other_pos))
        pos_bar_y = [x[0] for x in point_count][::-1]
        pos_bar = [x[1] for x in point_count][::-1]

        all_text = "".join(final_data["content_clean"].tolist())
        words = jieba.lcut(all_text)
        valid_words = [w for w in words if len(w) > 1 and w not in STOP_WORDS and not w.isdigit()]
        top30 = Counter(valid_words).most_common(30)
        word_cloud = [{"name": word, "value": cnt} for word, cnt in top30]

        campus_life = dim_count_dict["宿舍住宿"] + dim_count_dict["食堂餐饮"] + dim_count_dict["校园活动"]
        topic_pie = [
            {"value": round(campus_life / total_final * 100, 1) if total_final else 0, "name": "校园生活"},
            {"value": round(dim_count_dict["招生报考"] / total_final * 100, 1) if total_final else 0, "name": "招生报考"},
            {"value": round(dim_count_dict["教学学业"] / total_final * 100, 1) if total_final else 0, "name": "教学学业"},
            {"value": round(dim_count_dict["校园环境"] / total_final * 100, 1) if total_final else 0, "name": "校园环境"}
        ]

        sent_likes_mean = final_data.groupby("sentiment")["like_count"].mean()
        sent_likes = []
        for s in ["正面", "中性", "负面"]:
            v = round(sent_likes_mean.get(s, 0), 2)
            sent_likes.append(v)
        sent_like_x = ['正面', '中性', '负面']

        top_likes = final_data.sort_values("like_count", ascending=False).head(10)
        top_like_list = top_likes["like_count"].tolist()[::-1]
        top_like_y = [f'第{i}名' for i in range(10, 0, -1)]

        # 高赞评论内容详情（TOP10，含原文 / 点赞数 / 情感 / 平台 / 昵称）
        top_like_comments = []
        for _, row in top_likes.iterrows():
            nickname = row.get("nickname")
            top_like_comments.append({
                "content": str(row["content_clean"]),
                "like": int(row["like_count"]),
                "sentiment": str(row["sentiment"]),
                "platform": str(row["platform"]),
                "nickname": str(nickname) if pd.notna(nickname) else ""
            })

        top_users = final_data["nickname"].value_counts().head(10)
        active_user = list(top_users.values)[::-1]
        active_user_y = [f'用户{i}' for i in range(10, 0, -1)]

        # 评论最活跃用户详情（TOP8，含昵称 / 评论数 / 情感倾向 / 主要平台 / 代表评论）
        active_users_detail = []
        if "nickname" in final_data.columns and len(top_users) > 0:
            for name in top_users.index[:8]:
                user_df = final_data[final_data["nickname"] == name]
                if len(user_df) == 0:
                    continue
                rep_comments = user_df.sort_values("like_count", ascending=False)["content_clean"].head(3).tolist()
                sent_count = user_df["sentiment"].value_counts()
                main_sent = sent_count.idxmax() if len(sent_count) else "中性"
                plat_count_u = user_df["platform"].value_counts()
                main_plat = plat_count_u.idxmax() if len(plat_count_u) else ""
                active_users_detail.append({
                    "nickname": str(name),
                    "count": int(len(user_df)),
                    "sentiment": str(main_sent),
                    "platform": str(main_plat),
                    "comments": [str(c) for c in rep_comments]
                })

        plat_dim_result = {}
        for plat in plat_compare_names:
            plat_data = final_data[final_data["platform"] == plat]
            counts = []
            for dim in DIMENSION_KEYWORDS.keys():
                cnt = plat_data["dimensions"].apply(lambda x: dim in x).sum()
                counts.append(int(cnt))
            plat_dim_result[plat] = counts
        plat_dim_bar_x = list(DIMENSION_KEYWORDS.keys())
        _zero_counts = [0] * len(plat_dim_bar_x)
        plat_dim_bar_dy = plat_dim_result.get(main_plat, _zero_counts) if main_plat else _zero_counts
        plat_dim_bar_xhs = plat_dim_result.get(sub_plat, _zero_counts) if sub_plat else _zero_counts

        dy_total = plat_count.get(main_plat, 1) or 1
        xhs_total = plat_count.get(sub_plat, 1) or 1
        dy_pos_rate = round(
            final_data[(final_data["platform"] == main_plat) & (final_data["sentiment"] == "正面")].shape[0] / dy_total * 100,
            1
        )
        xhs_pos_rate = round(
            final_data[(final_data["platform"] == sub_plat) & (final_data["sentiment"] == "正面")].shape[0] / xhs_total * 100,
            1
        )
        dy_avg_like = dy_likes.mean() if len(dy_likes) else 0
        xhs_avg_like = xhs_likes.mean() if len(xhs_likes) else 0
        dy_avg_len = dy_df["content_len"].mean() if len(dy_df) else 0
        xhs_avg_len = xhs_df["content_len"].mean() if len(xhs_df) else 0

        n_total = norm(dy_total, xhs_total)
        n_pos = norm(dy_pos_rate, xhs_pos_rate)
        n_like = norm(dy_avg_like, xhs_avg_like)
        n_depth = norm(dy_avg_len, xhs_avg_len)

        dy_speed = dy_avg_like / dy_total if dy_total > 0 else 0
        xhs_speed = xhs_avg_like / xhs_total if xhs_total > 0 else 0
        n_speed = norm(dy_speed, xhs_speed)

        dy_user_cnt = dy_df["nickname"].nunique() if "nickname" in dy_df.columns else 0
        xhs_user_cnt = xhs_df["nickname"].nunique() if "nickname" in xhs_df.columns else 0
        dy_active = dy_total / dy_user_cnt if dy_user_cnt > 0 else 0
        xhs_active = xhs_total / xhs_user_cnt if xhs_user_cnt > 0 else 0
        n_active = norm(dy_active, xhs_active)

        plat_radar_dy = [n_total[0], n_pos[0], n_like[0], n_depth[0], n_speed[0], n_active[0]]
        plat_radar_xhs = [n_total[1], n_pos[1], n_like[1], n_depth[1], n_speed[1], n_active[1]]
        plat_radar_indicator = [
            {"name": "评论总量", "max": 100},
            {"name": "正面占比", "max": 100},
            {"name": "平均点赞", "max": 100},
            {"name": "内容深度", "max": 100},
            {"name": "传播速度", "max": 100},
            {"name": "用户活跃度", "max": 100}
        ]

        # --------------------- 舆情风险等级计算 ---------------------
        max_neg_like = int(neg_data["like_count"].max()) if len(neg_data) else 0
        if "publish_time" in final_data.columns:
            daily_neg = neg_data.groupby(neg_data["publish_time"].dt.date).size()
            max_daily_neg = int(daily_neg.max()) if len(daily_neg) else 0
        else:
            max_daily_neg = 0
        if max_daily_neg > 20 or max_neg_like >= 1000:
            risk_level, risk_name = 3, "高风险"
        elif max_daily_neg >= 5 or max_neg_like >= 100:
            risk_level, risk_name = 2, "中风险"
        else:
            risk_level, risk_name = 1, "低风险"
        risk_info = {
            "level": risk_level,
            "levelName": risk_name,
            "maxDailyNegative": max_daily_neg,
            "maxNegativeLike": max_neg_like,
            "negativeTotal": neg_num
        }

        # --------------------- 组装传给 DeepSeek 的统计数据 ----------------
        top_comments_text = [
            {"content": row["content_clean"], "like": int(row["like_count"]), "sentiment": row["sentiment"]}
            for _, row in top_likes.head(5).iterrows()
        ]
        stat_for_ai = {
            "总有效评论数": total_final,
            "正面占比": f"{pos_rate}%",
            "负面占比": f"{neg_rate}%",
            "平台分布": plat_count,
            "评论长度分布": {k: int(v) for k, v in zip(len_labels, len_count)},
            "维度热度排行": {k: int(v) for k, v in zip(dim_x, dim_bar)},
            "各维度正面满意度": {k: v for k, v in zip(list(DIMENSION_KEYWORDS.keys()), dim_pos_rate)},
            "各维度负面占比": {k: v for k, v in zip(list(DIMENSION_KEYWORDS.keys()), dim_neg_rate)},
            "负面原因及提及次数": {k: int(v) for k, v in zip(neg_pareto_x, neg_pareto_bar)},
            "负面原因累计占比": {k: v for k, v in zip(neg_pareto_x, cum_rate)},
            "正面卖点及提及次数": {k: int(v) for k, v in zip(pos_bar_y, pos_bar)},
            "不同情感平均点赞": {k: v for k, v in zip(sent_like_x, sent_likes)},
            "月度总评论": {k: v for k, v in zip(month_x, month_total)},
            "24小时评论量分布": {f"{h}点": v for h, v in enumerate(hour_count)},
            "周内评论量分布": {k: v for k, v in zip(weekday_names, weekday_count)},
            "高频词Top20": [w["name"] for w in word_cloud[:20]],
            "最高赞评论示例": top_comments_text,
            "风险等级": risk_name
        }

        ai_ins = None
        ai_report = None
        ai_hot = None
        if USE_DEEPSEEK_AI:
            ai_ins = generate_ai_insight_by_deepseek(stat_for_ai)
            ai_report = generate_ai_report_by_deepseek(stat_for_ai)
            ai_hot = generate_hot_content_analysis({
                "高赞评论Top10": top_like_comments,
                "评论最活跃用户及其代表评论": active_users_detail
            })

        # ---------- 本地兜底分析所需的统计上下文（全部来自本次数据） ----------
        _likes = final_data["like_count"].sort_values(ascending=False)
        _like_sum = float(_likes.sum()) or 1.0
        _top_k = max(1, int(len(_likes) * 0.1))
        like_top_share = round(float(_likes.head(_top_k).sum()) / _like_sum * 100, 1)
        _top8_cnt = sum(u["count"] for u in active_users_detail[:8])
        active_user_share = round(_top8_cnt / max(1, total_final) * 100, 1)

        ai_ctx = {
            "total": total_final,
            "pos_rate": pos_rate,
            "neg_rate": neg_rate,
            "neg_total": neg_num,
            "plat_count": plat_count,
            "plat_pos": {k: v for k, v in ((main_plat, dy_pos_rate), (sub_plat, xhs_pos_rate)) if k},
            "len_labels": len_labels,
            "len_count": len_count,
            "like_mean": float(final_data["like_count"].mean()),
            "like_median": float(final_data["like_count"].median()),
            "like_top_share": like_top_share,
            "active_user_share": active_user_share,
            "month_x": month_x,
            "month_total": month_total,
            "hour_count": hour_count,
            "weekday_names": weekday_names,
            "weekday_count": weekday_count,
            "dim_pairs": dim_sorted,
            "dim_pos": dict(zip(list(DIMENSION_KEYWORDS.keys()), dim_pos_rate)),
            "dim_neg": dict(zip(list(DIMENSION_KEYWORDS.keys()), dim_neg_rate)),
            "neg_pairs": list(zip(neg_pareto_x, neg_pareto_bar)),
            "pos_pairs": list(zip(pos_bar_y, pos_bar)),
            "words": word_cloud[:10],
            "sent_like": dict(zip(sent_like_x, sent_likes)),
            "risk": risk_name,
            "max_daily_neg": max_daily_neg,
            "max_neg_like": max_neg_like,
            "top_like_comments": top_like_comments,
            "active_users": active_users_detail
        }

        insights = ai_ins if ai_ins is not None else get_fallback_insight(ai_ctx)
        report = _merge_report(get_fallback_report(ai_ctx), ai_report)

        # 高赞评论 + 活跃用户内容分析（含 AI 总结 + 兜底）
        hot_fallback = get_fallback_hot_content(ai_ctx)
        hot_content = {
            "topLikeComments": top_like_comments,
            "activeUsers": active_users_detail,
            "topLikeSummary": hot_fallback["topLikeSummary"],
            "activeUserSummary": hot_fallback["activeUserSummary"]
        }
        if isinstance(ai_hot, dict):
            for k in ["topLikeSummary", "activeUserSummary"]:
                if ai_hot.get(k):
                    hot_content[k] = ai_hot[k]
        # -----------------------------------------------------------

        report_data = {
            "overview": {
                "totalComment": total_final,
                "positiveRate": f"{pos_rate}%",
                "negativeRate": f"{neg_rate}%",
                "dimensions": len(DIMENSION_KEYWORDS),
                "platforms": plat_count,
                "totalLikes": f"{total_likes / 10000:.1f}万"
            },
            "charts": {
                "platformPie": platform_pie,
                "lenHist": len_count,
                "lenHistX": len_labels,
                "likeBox": like_box_data,
                "likeBoxX": like_box_x,
                "monthTotal": month_total,
                "monthPositive": month_positive,
                "monthNegative": month_negative,
                "monthX": month_x,
                "hourTrend": hour_trend,
                "hourX": hour_x,
                "weekTrend": weekday_count,
                "weekX": weekday_names,
                "dimBar": dim_bar,
                "dimX": dim_x,
                "dimRadarPositive": dim_pos_rate,
                "dimRadarNegative": dim_neg_rate,
                "sentimentPie": sentiment_pie,
                "negParetoBar": neg_pareto_bar,
                "negParetoLine": cum_rate,
                "negParetoX": neg_pareto_x,
                "posBar": pos_bar,
                "posBarY": pos_bar_y,
                "wordCloud": word_cloud,
                "topicPie": topic_pie,
                "sentLike": sent_likes,
                "sentLikeX": sent_like_x,
                "topLike": top_like_list,
                "topLikeY": top_like_y,
                "activeUser": active_user,
                "activeUserY": active_user_y,
                "platRadarDy": plat_radar_dy,
                "platRadarXhs": plat_radar_xhs,
                "platRadarIndicator": plat_radar_indicator,
                "platCompareNames": plat_compare_names,
                "platDimBarDy": plat_dim_bar_dy,
                "platDimBarXhs": plat_dim_bar_xhs,
                "platDimBarX": plat_dim_bar_x
            },
            "insights": insights,
            "report": report,
            "risk": risk_info,
            "hotContent": hot_content,
            # 本次分析用的 AI 引擎：deepseek=在线大模型，local=本地统计模板
            "aiEngine": "deepseek" if ai_ins is not None else "local"
        }

        json_out_path = os.path.join(CLEANED_DIR, "data.json")
        with open(json_out_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2, default=json_default)

        export_df = final_data.copy()
        if "publish_time" in export_df.columns:
            export_df["publish_time"] = export_df["publish_time"].dt.strftime("%Y-%m-%d %H:%M:%S")
        list_columns = ["dimensions", "negative_reasons", "positive_points"]
        for col in list_columns:
            if col in export_df.columns:
                export_df[col] = export_df[col].apply(lambda x: "、".join(x) if len(x) > 0 else "")
        output_cols = ["platform", "nickname", "content", "content_clean", "content_len",
                       "publish_time", "like_count", "sentiment", "dimensions",
                       "negative_reasons", "positive_points", "province"]
        output_cols = [c for c in output_cols if c in export_df.columns]
        export_df[output_cols].to_excel(os.path.join(OUTPUT_EXCEL_DIR, "清洗后_最终有效评论.xlsx"), index=False)

        with pd.ExcelWriter(os.path.join(OUTPUT_EXCEL_DIR, "统计结果_全维度汇总.xlsx")) as writer:
            pd.DataFrame({
                "指标": ["有效评论总数", "正面评价数", "中性评价数", "负面评价数", "正面占比", "负面占比", "总点赞数"],
                "数值": [
                    total_final, pos_num, neu_num, neg_num,
                    f"{pos_rate}%", f"{neg_rate}%", total_likes
                ]
            }).to_excel(writer, sheet_name="核心指标", index=False)
            pd.DataFrame({
                "维度": list(dim_count_dict.keys()),
                "评论数": list(dim_count_dict.values()),
                "占比": [f"{round(v / total_final * 100, 2) if total_final else 0}%" for v in dim_count_dict.values()]
            }).to_excel(writer, sheet_name="维度统计", index=False)
            pd.DataFrame({
                "负面原因": neg_pareto_x,
                "提及次数": neg_pareto_bar,
                "累计占比": [f"{v}%" for v in cum_rate]
            }).to_excel(writer, sheet_name="负面原因统计", index=False)
            pd.DataFrame({
                "正面卖点": pos_bar_y,
                "提及次数": pos_bar
            }).to_excel(writer, sheet_name="正面卖点统计", index=False)
            if not month_stat.empty:
                month_stat.reset_index().to_excel(writer, sheet_name="月度趋势", index=False)

        log("====舆情分析任务执行完成====")
        return {
            "ok": True,
            "msg": "分析完成，数据已更新",
            "output_json": json_out_path,
            # 数据摘要：告诉前端这次到底用了哪份原始数据的多少条
            "stats": {
                "totalRaw": total_original,
                "totalValid": total_final,
                "platforms": plat_count
            }
        }
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        log(f"分析异常：{err_msg}")
        return {"ok": False, "msg": f"执行失败：{str(e)}"}


if __name__ == "__main__":
    res = run_analysis()
    log(json.dumps(res, ensure_ascii=False, indent=2))
