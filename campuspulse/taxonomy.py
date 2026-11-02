# -*- coding: utf-8 -*-
"""维度词典与归因规则的修订版。

为什么要重写（对应文档 2.2「开工前必须核对的问题」）：
原实现有两处会直接把结论带偏，且都能用数据证明：

1) 维度词典把情感词当主题词。
   「食堂餐饮」关键词里含"贵/便宜/性价比"，于是"电费比一个家庭还贵""贵族学校""学车贵吗"
   这类评论全部被算成食堂讨论。实测全量样本中 183 条食堂维度评论里有 116 条（63%）正文
   根本没提食堂/饭菜/外卖。修订后食堂维度降到 99 条，与正文一致。

2) 负面归因按词典顺序取"第一个命中"。
   「食堂物价高」排第一且含极宽的"贵"，导致学费、电费、住宿费吐槽全被归到食堂物价
   （原规则下食堂维度 56 条负面中 51 条被如此归因）。修订为"维度约束 + 关键词长度加权打分"。

修订后的口径变化会在数据集质量报告里如实展示，并在口径说明中列出，不做隐藏。
"""
from data_processor import DIMENSION_KEYWORDS as _LEGACY_DIMENSIONS

# ---------------- 维度词典（修订版）----------------
DIMENSION_KEYWORDS = {k: list(v) for k, v in _LEGACY_DIMENSIONS.items()}

# 1) 剔除情感词，避免"贵/便宜/性价比"把非食堂评论吸进食堂维度
for _w in ("贵", "便宜", "性价比"):
    if _w in DIMENSION_KEYWORDS["食堂餐饮"]:
        DIMENSION_KEYWORDS["食堂餐饮"].remove(_w)

# 2) 补齐宿舍的费用/设施类表述（原本只有"水电费"这种完整词，漏掉"电费""维修"）
DIMENSION_KEYWORDS["宿舍住宿"] += ["电费", "水费", "收费", "维修", "停水", "停电", "电梯"]

# 3) 补齐报考场景里的费用与院校口碑表述
DIMENSION_KEYWORDS["招生报考"] += ["学杂费", "费用", "贵族学校", "值不值"]

# 4) "不方便/偏远"是泛化情感词，会误伤任何提到不便的评论，交通维度只保留具象词
for _w in ("不方便", "偏远"):
    if _w in DIMENSION_KEYWORDS["交通出行"]:
        DIMENSION_KEYWORDS["交通出行"].remove(_w)

DIMENSION_REVISION_NOTES = [
    "食堂餐饮维度移除泛化词「贵 / 便宜 / 性价比」：这些是情感词而非主题词，"
    "会把学费、电费等非食堂评论误判为食堂讨论。",
    "宿舍住宿维度补充「电费 / 水费 / 收费 / 维修 / 停水 / 停电 / 电梯」。",
    "招生报考维度补充「学杂费 / 费用 / 贵族学校 / 值不值」。",
    "交通出行维度移除泛化词「不方便 / 偏远」，仅保留具象交通词。",
    "负面原因与正面卖点改为「维度约束 + 关键词长度加权」打分，取得分最高者，不再按词典顺序取首个命中。",
]


def matched_dimensions(text):
    """返回命中的维度列表（多标签）。"""
    return [d for d, keywords in DIMENSION_KEYWORDS.items() if any(k in text for k in keywords)]


def primary_dimension(text):
    """单标签主维度：命中关键词最多的维度，平票按配置顺序。"""
    best, best_hits = None, 0
    for dim, keywords in DIMENSION_KEYWORDS.items():
        hits = sum(1 for k in keywords if k in text)
        if hits > best_hits:
            best, best_hits = dim, hits
    return best


NEGATIVE_REASONS = {
    "食堂物价高": {
        "keywords": ["食堂贵", "饭菜贵", "饭贵", "菜贵", "食堂价格", "吃饭贵", "食堂消费", "物价高", "吃不起"],
        "scope": ["食堂餐饮"],
    },
    "饭菜口味差": {
        "keywords": ["难吃", "不好吃", "口味差", "菜难吃", "饭难吃", "太咸", "太油", "没味道", "难以下咽"],
        "scope": ["食堂餐饮"],
    },
    "学费与费用负担": {
        "keywords": ["学费", "学杂费", "住宿费", "电费", "水费", "水电费", "收费", "退费", "乱收费",
                     "贵族学校", "费用高", "太贵"],
        "scope": [],
    },
    "校园网卡顿": {
        "keywords": ["校园网", "网卡", "网慢", "网速", "断网", "网费", "没网"],
        "scope": ["宿舍住宿", "教学学业"],
    },
    "宿舍管理严": {
        "keywords": ["门禁", "断电", "断网", "查寝", "宿管", "管理严", "扣分", "限制", "不让"],
        "scope": ["宿舍住宿"],
    },
    "宿舍设施问题": {
        "keywords": ["空调", "热水", "洗衣机", "电梯", "停水", "停电", "维修", "漏水", "坏了", "老旧"],
        "scope": ["宿舍住宿", "校园环境"],
    },
    "小学期安排": {
        "keywords": ["小学期"],
        "scope": ["教学学业"],
    },
    "教学与课程问题": {
        "keywords": ["水课", "抢课", "选课", "挂科", "补考", "重修", "考勤", "作业多", "课程安排", "教学质量"],
        "scope": ["教学学业"],
    },
    "交通不便利": {
        "keywords": ["偏僻", "太远", "没地铁", "不方便", "进城难", "通勤", "打车", "公交", "校车"],
        "scope": ["交通出行", "校园环境"],
    },
    "地理位置偏": {
        "keywords": ["位置偏", "郊区", "乡下", "周边荒", "荒凉", "鸟不拉屎"],
        "scope": ["校园环境", "交通出行"],
    },
    "招生与报考疑虑": {
        "keywords": ["分数线", "录取", "投档", "能不能上", "稳不稳", "排位", "志愿", "通知书", "招生办"],
        "scope": ["招生报考"],
    },
}

POSITIVE_POINTS = {
    "上床下桌宿舍": {
        "keywords": ["上床下桌", "宿舍好", "宿舍大", "新宿舍", "宿舍条件", "独立卫浴", "独立卫生间", "空调"],
        "scope": ["宿舍住宿"],
    },
    "学费性价比": {
        "keywords": ["学费不贵", "性价比高", "学费合理", "不贵", "便宜", "亲民", "实惠"],
        "scope": [],
    },
    "校园环境好": {
        "keywords": ["环境好", "漂亮", "绿化好", "校园美", "风景好", "好看", "环境不错"],
        "scope": ["校园环境"],
    },
    "交通便利": {
        "keywords": ["交通方便", "近地铁", "出行方便", "有地铁", "地铁"],
        "scope": ["交通出行"],
    },
    "师资教学好": {
        "keywords": ["老师好", "教学好", "师资强", "课程好", "老师负责", "教得好"],
        "scope": ["教学学业"],
    },
    "校园活动多": {
        "keywords": ["活动多", "社团丰富", "活动好玩", "社团多"],
        "scope": ["校园活动"],
    },
    "食堂餐饮满意": {
        "keywords": ["好吃", "饭菜好", "食堂好", "食堂不错", "菜品丰富"],
        "scope": ["食堂餐饮"],
    },
    "招生口碑认可": {
        "keywords": ["推荐", "值得报", "不后悔", "选对了", "值了"],
        "scope": ["招生报考"],
    },
}


def classify(text, dimensions, table):
    """在给定维度下为文本选出唯一主因/主要卖点。

    返回 (label, score)。无合适规则时返回 ("", 0)。
    """
    dims = set(dimensions or [])
    best, best_score = "", 0
    for label, spec in table.items():
        scope = spec.get("scope") or []
        if scope and dims and not (set(scope) & dims):
            continue
        hits = [kw for kw in spec["keywords"] if kw in text]
        if not hits:
            continue
        # 关键词越长越具体，权重越高；命中越多分越高
        score = sum(len(kw) for kw in hits)
        if score > best_score:
            best, best_score = label, score
    return best, best_score
