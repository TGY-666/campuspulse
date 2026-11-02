import json
import re
import os
import numpy as np
import jieba
import pandas as pd
from collections import Counter

# ==================================================
# 🎯 配置区：只需要改这里的参数即可
# ==================================================
# 1. 数据文件路径（和脚本放同一个文件夹）
DATA_FILES = {
    "抖音": "douyin_comments.json",
    "小红书": "xhs_comments.json"
}

# 2. 7大分析维度关键词（和网站一一对应）
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

# 3. 情感关键词库
POSITIVE_WORDS = ["好", "棒", "优秀", "舒服", "满意", "推荐", "不错", "可以", "方便", "齐全", "优美", "干净", "划算",
                  "实惠", "喜欢", "爱了", "好评", "值得", "靠谱", "nice", "yyds", "完美", "贴心", "赞", "强", "nice",
                  "绝了"]
NEGATIVE_WORDS = ["差", "坑", "避雷", "烂", "贵", "吐槽", "不好", "垃圾", "劝退", "难吃", "吵", "远", "偏僻", "慢",
                  "卡", "无语", "后悔", "不值", "敷衍", "烂透", "恶心", "垃圾", "坑爹", "骗人", "差劲", "难受", "麻烦",
                  "严格"]

# 4. 负面细分原因（对应帕累托图）
NEGATIVE_REASONS = {
    "食堂物价高": ["贵", "物价高", "太贵", "不划算", "性价比低", "饭贵", "菜贵"],
    "交通不便利": ["偏僻", "远", "交通差", "不方便", "没地铁", "进城难", "偏远", "打车贵"],
    "宿舍管理严": ["门禁", "断电", "断网", "宿管严", "查寝", "管理严", "限制"],
    "地理位置偏": ["位置偏", "郊区", "乡下", "偏僻", "周边荒"],
    "饭菜口味差": ["难吃", "不好吃", "口味差", "菜难吃", "饭难吃"],
    "校园网卡顿": ["网卡", "网慢", "校园网差", "断网", "网速慢"],
}

# 5. 正面卖点细分（对应正面卖点图）
POSITIVE_POINTS = {
    "上床下桌宿舍": ["上床下桌", "宿舍好", "宿舍大", "新宿舍", "宿舍条件好"],
    "学费性价比": ["学费不贵", "性价比高", "学费合理", "不贵"],
    "校园环境好": ["环境好", "漂亮", "绿化好", "校园美", "风景好"],
    "交通便利": ["交通方便", "近地铁", "出行方便"],
    "师资教学好": ["老师好", "教学好", "师资强", "课程好"],
    "校园活动多": ["活动多", "社团丰富", "活动好玩"],
}

# 6. 停用词表（词云过滤用）
STOP_WORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "们", "在", "也", "都", "就", "还", "有", "没", "不", "很",
    "啊", "呀", "吧", "呢", "吗", "哦", "哈", "哈哈", "哈哈哈", "这个", "那个", "什么", "怎么", "为什么", "觉得",
    "感觉",
    "就是", "还是", "但是", "因为", "所以", "如果", "可以", "一个", "一下", "真的", "其实", "现在", "已经", "不是",
    "广东东软学院", "东软学院", "东软", "学校", "学院", "还有", "时候", "这样", "那样", "这么", "那么",
    "自己", "大家", "人家", "别人", "东西", "事情", "地方", "问题", "情况", "真的", "非常", "特别", "比较",
    "捂脸", "流泪", "微笑", "表情", "doge", "笑哭", "点赞", "评论", "回复", "楼主", "层主"
}

# 7. 评论最小有效字数
MIN_VALID_LEN = 5

# 8. 重点关键词（词云加权）
FOCUS_KEYWORD = "小学期"
FOCUS_WEIGHT = 2  # 词云加权倍数


# ==================================================
# 🔧 工具函数区
# ==================================================
def timestamp_to_datetime(ts):
    """时间戳转北京时间，兼容10位秒/13位毫秒"""
    try:
        ts = int(ts)
        ts_str = str(ts)
        if len(ts_str) == 13:
            ts = ts / 1000
        return pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Shanghai")
    except:
        return pd.NaT


def clean_text(text):
    """清洗文本：移除表情标签、空白字符、@用户"""
    text = str(text)
    text = re.sub(r"\[[^\]]+\]", "", text)  # 移除[表情]
    text = re.sub(r"@\S+", "", text)  # 移除@用户
    text = re.sub(r"\s+", "", text)  # 移除所有空白
    return text.strip()


def is_valid_comment(text):
    """判断是否为有效评论"""
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
    """简单规则情感判断：正面/中性/负面"""
    pos_count = sum(1 for w in POSITIVE_WORDS if w in text)
    neg_count = sum(1 for w in NEGATIVE_WORDS if w in text)
    if pos_count > neg_count:
        return "正面"
    elif neg_count > pos_count:
        return "负面"
    else:
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
    """从IP属地提取省份，没有则返回未知"""
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


# ==================================================
# 🚀 主流程
# ==================================================
def main():
    # 注册重点关键词到分词词典，保证不被拆分
    jieba.add_word(FOCUS_KEYWORD)

    print("=" * 70)
    print("📊 广东东软学院舆情数据一键清洗统计工具")
    print(f"   重点关注关键词：{FOCUS_KEYWORD}（词云已加权突出）")
    print("=" * 70)

    # ---------------------- 1. 读取原始数据 ----------------------
    print("\n【1/8 读取原始数据】")
    all_df_list = []
    has_ip = False
    for platform, filename in DATA_FILES.items():
        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
            df = pd.DataFrame(data)
            df["platform"] = platform
            print(f"  ✅ {platform}：读取到 {len(df)} 条原始评论")
            print(f"     包含字段：{list(df.columns)}")

            # 检查是否有IP字段
            ip_fields = ["ip_location", "ip_loc", "ip", "ip_address"]
            for f in ip_fields:
                if f in df.columns:
                    has_ip = True
                    break

            all_df_list.append(df)
        except FileNotFoundError:
            print(f"  ❌ 找不到文件：{filename}，已跳过")

    if not all_df_list:
        print("\n❌ 没有有效数据文件，请检查路径后重试")
        return

    # ---------------------- 2. 统一字段 + 合并 ----------------------
    print("\n【2/8 统一字段 & 合并数据】")
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
    print(f"  合并后原始数据共：{total_original} 条")

    # ---------------------- 3. 基础清洗：去重、去空 ----------------------
    print("\n【3/8 基础数据清洗】")
    # 去重
    if "comment_id" in all_data.columns:
        all_data = all_data.drop_duplicates(subset=["comment_id"], keep="first").reset_index(drop=True)
    else:
        all_data = all_data.drop_duplicates(subset=["content"], keep="first").reset_index(drop=True)
    print(f"  去重后剩余：{len(all_data)} 条")

    # 空值处理
    all_data["content"] = all_data["content"].astype(str).str.strip()
    all_data = all_data[all_data["content"].str.len() > 0].reset_index(drop=True)
    print(f"  去除空评论后剩余：{len(all_data)} 条")

    # 点赞数填充
    if "like_count" not in all_data.columns:
        all_data["like_count"] = 0
    all_data["like_count"] = pd.to_numeric(all_data["like_count"], errors="coerce").fillna(0).astype(int)

    # ---------------------- 4. 时间字段处理 ----------------------
    print("\n【4/8 时间字段转换】")
    if "create_time" in all_data.columns:
        all_data["publish_time"] = all_data["create_time"].apply(timestamp_to_datetime)
        # 去除无效时间
        valid_time_count = all_data["publish_time"].notna().sum()
        all_data = all_data[all_data["publish_time"].notna()].reset_index(drop=True)
        print(f"  有效时间转换：{valid_time_count} 条")

        # 提取时间维度
        all_data["month"] = all_data["publish_time"].dt.strftime("%Y-%m")
        all_data["hour"] = all_data["publish_time"].dt.hour
        all_data["weekday"] = all_data["publish_time"].dt.weekday  # 0=周一，6=周日
    else:
        print("  ⚠️  未检测到时间字段，跳过时间分析")
        all_data["month"] = "未知"
        all_data["hour"] = 0
        all_data["weekday"] = 0

    # ---------------------- 5. 无效评论过滤 + 主题筛选 ----------------------
    print("\n【5/8 有效评论筛选】")
    # 过滤水评论
    all_data["is_valid"] = all_data["content"].apply(is_valid_comment)
    valid_data = all_data[all_data["is_valid"]].reset_index(drop=True)
    print(f"  过滤水评论后剩余：{len(valid_data)} 条")

    # 计算评论长度
    valid_data["content_clean"] = valid_data["content"].apply(clean_text)
    valid_data["content_len"] = valid_data["content_clean"].str.len()

    # 维度匹配 + 相关性筛选
    valid_data["dimensions"] = valid_data["content_clean"].apply(get_hit_dimensions)
    valid_data["is_related"] = valid_data["dimensions"].apply(lambda x: len(x) > 0)

    # 最终有效数据
    final_data = valid_data[valid_data["is_related"]].reset_index(drop=True)
    print(f"  主题筛选后最终有效数据：{len(final_data)} 条")
    print(f"    - 抖音：{len(final_data[final_data['platform'] == '抖音'])} 条")
    print(f"    - 小红书：{len(final_data[final_data['platform'] == '小红书'])} 条")

    # ---------------------- 6. 衍生字段计算 ----------------------
    print("\n【6/8 衍生字段计算】")
    # 情感
    final_data["sentiment"] = final_data["content_clean"].apply(get_sentiment)
    print("  ✅ 情感倾向计算完成")

    # 负面原因 + 正面卖点
    final_data["negative_reasons"] = final_data["content_clean"].apply(get_negative_reasons)
    final_data["positive_points"] = final_data["content_clean"].apply(get_positive_points)
    print("  ✅ 细分原因匹配完成")

    # IP省份
    if has_ip and "ip_location" in final_data.columns:
        final_data["province"] = final_data["ip_location"].apply(extract_province)
        print("  ✅ IP属地提取完成")
    else:
        final_data["province"] = "未知"
        print("  ⚠️  无IP字段，跳过敏感地域分析")

    # ---------------------- 7. 全维度统计 ----------------------
    print("\n" + "=" * 70)
    print("📈 统计结果（对应网站模块，直接复制即可）")
    print("=" * 70)

    # ========== ① 顶部核心指标 ==========
    print("\n【① 顶部核心指标卡片】")
    total_final = len(final_data)
    pos_num = len(final_data[final_data["sentiment"] == "正面"])
    neg_num = len(final_data[final_data["sentiment"] == "负面"])
    neu_num = len(final_data[final_data["sentiment"] == "中性"])
    pos_rate = round(pos_num / total_final * 100, 1) if total_final else 0
    neg_rate = round(neg_num / total_final * 100, 1) if total_final else 0
    total_likes = final_data["like_count"].sum()
    dim_count = len(DIMENSION_KEYWORDS)
    platform_count = len(DATA_FILES)

    print(f"  有效评论总数：{total_final}")
    print(f"  正面评价占比：{pos_rate}%")
    print(f"  负面评价占比：{neg_rate}%")
    print(f"  核心维度数：{dim_count}")
    print(f"  覆盖平台数：{platform_count}")
    print(f"  总互动点赞数：{total_likes}")

    # ========== ② 平台分布饼图 ==========
    print("\n【② 平台分布饼图 data】")
    platform_counts = final_data["platform"].value_counts()
    for plat in ["抖音", "小红书"]:
        cnt = int(platform_counts.get(plat, 0))
        print(f"    {{ value: {cnt}, name: '{plat}' }},")

    # ========== ③ 评论长度分布 ==========
    print("\n【③ 评论长度分布直方图 data】")
    bins = [0, 10, 30, 50, 100, 9999]
    labels = ["1-10字", "11-30字", "31-50字", "51-100字", "100字以上"]
    len_cut = pd.cut(final_data["content_len"], bins=bins, labels=labels, right=True)
    len_count = len_cut.value_counts().reindex(labels).fillna(0).astype(int).tolist()
    print(f"  x轴标签：{labels}")
    print(f"  数量数据：{len_count}")

    # ========== ④ 点赞数箱线图 ==========
    print("\n【④ 点赞数箱线图 data】")

    def get_box_data(series):
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

    dy_likes = final_data[final_data["platform"] == "抖音"]["like_count"]
    xhs_likes = final_data[final_data["platform"] == "小红书"]["like_count"]
    dy_box = get_box_data(dy_likes)
    xhs_box = get_box_data(xhs_likes)
    print(f"  抖音箱线数据：{dy_box}")
    print(f"  小红书箱线数据：{xhs_box}")
    print(f"  抖音平均点赞：{round(dy_likes.mean(), 2) if len(dy_likes) else 0}")
    print(f"  小红书平均点赞：{round(xhs_likes.mean(), 2) if len(xhs_likes) else 0}")

    # ========== ⑤ 月度热度趋势 ==========
    print("\n【⑤ 月度评论热度趋势】")
    if "month" in final_data.columns and final_data["month"].iloc[0] != "未知":
        month_stat = final_data.groupby("month").agg(
            total=("comment_id", "count"),
            positive=("sentiment", lambda x: (x == "正面").sum()),
            negative=("sentiment", lambda x: (x == "负面").sum())
        ).sort_index()
        months = month_stat.index.tolist()
        total_month = month_stat['total'].tolist()
        pos_month = month_stat['positive'].tolist()
        neg_month = month_stat['negative'].tolist()
        peak_idx = total_month.index(max(total_month)) if total_month else 0
        peak_val = max(total_month) if total_month else 0
    else:
        months = []
        total_month = []
        pos_month = []
        neg_month = []
        peak_idx = 0
        peak_val = 0
    print(f"  x轴月份：{months}")
    print(f"  总评论量：{total_month}")
    print(f"  正面评论：{pos_month}")
    print(f"  负面评论：{neg_month}")

    # ========== ⑥ 24小时分布 ==========
    print("\n【⑥ 24小时用户活跃度】")
    hour_count = final_data.groupby("hour").size().reindex(range(24), fill_value=0).tolist()
    print(f"  x轴小时：{list(range(0, 24, 2))}")  # 每2小时一个刻度
    print(f"  评论数量：{hour_count[::2]}")

    # ========== ⑦ 周内分布 ==========
    print("\n【⑦ 周内评论量分布】")
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday_count = final_data.groupby("weekday").size().reindex(range(7), fill_value=0).tolist()
    print(f"  x轴星期：{weekday_names}")
    print(f"  评论数量：{weekday_count}")

    # ========== ⑧ 各维度讨论热度 ==========
    print("\n【⑧ 七大维度讨论热度排行】")
    dim_counts = {}
    for dim in DIMENSION_KEYWORDS.keys():
        cnt = final_data["dimensions"].apply(lambda x: dim in x).sum()
        dim_counts[dim] = int(cnt)
    # 按数量排序
    dim_sorted = sorted(dim_counts.items(), key=lambda x: x[1], reverse=True)
    dim_names_sorted = [x[0] for x in dim_sorted]
    dim_values_sorted = [x[1] for x in dim_sorted]
    print(f"  x轴维度：{dim_names_sorted}")
    print(f"  数量数据：{dim_values_sorted}")

    # ========== ⑨ 分维度情感满意度 ==========
    print("\n【⑨ 各维度情感满意度雷达图】")
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
    print(f"  维度列表：{list(DIMENSION_KEYWORDS.keys())}")
    print(f"  正面满意度：{dim_pos_rate}")
    print(f"  负面占比：{dim_neg_rate}")

    # ========== ⑩ 整体情感分布 ==========
    print("\n【⑩ 整体情感倾向饼图 data】")
    sent_count = final_data["sentiment"].value_counts()
    for s in ["正面", "中性", "负面"]:
        cnt = int(sent_count.get(s, 0))
        print(f"    {{ value: {cnt}, name: '{s}评价' }},")

    # ========== ⑪ 负面原因帕累托 ==========
    print("\n【⑪ 负面原因帕累托图 data】")
    neg_data = final_data[final_data["sentiment"] == "负面"]
    all_neg_reasons = []
    for reasons in neg_data["negative_reasons"]:
        all_neg_reasons.extend(reasons)
    reason_count = Counter(all_neg_reasons).most_common()
    # 补充其他
    other_count = len(neg_data) - sum(c for _, c in reason_count)
    if other_count > 0:
        reason_count.append(("其他", other_count))

    reason_names = [x[0] for x in reason_count]
    reason_nums = [x[1] for x in reason_count]
    # 计算累计占比
    cum_rate = []
    total_neg = sum(reason_nums)
    cum = 0
    for n in reason_nums:
        cum += n
        cum_rate.append(round(cum / total_neg * 100, 1))

    print(f"  原因名称：{reason_names}")
    print(f"  负面数量：{reason_nums}")
    print(f"  累计占比：{cum_rate}")

    # ========== ⑫ 正面卖点排行 ==========
    print("\n【⑫ 正面核心卖点条形图 data】")
    pos_data = final_data[final_data["sentiment"] == "正面"]
    all_pos_points = []
    for points in pos_data["positive_points"]:
        all_pos_points.extend(points)
    point_count = Counter(all_pos_points).most_common()
    # 补充其他
    other_pos = len(pos_data) - sum(c for _, c in point_count)
    if other_pos > 0:
        point_count.append(("其他", other_pos))

    point_names = [x[0] for x in point_count]
    point_nums = [x[1] for x in point_count]
    print(f"  卖点名称：{point_names}")
    print(f"  提及数量：{point_nums}")

    # ========== ⑬ 高频词云TOP30（重点词加权） ==========
    print("\n【⑬ 词云TOP30高频词 words】")
    all_text = "".join(final_data["content_clean"].tolist())
    words = jieba.lcut(all_text)
    valid_words = [w for w in words if len(w) > 1 and w not in STOP_WORDS and not w.isdigit()]
    top30 = Counter(valid_words).most_common(30)
    # 加权处理
    top30_dict = dict(top30)
    if FOCUS_KEYWORD in top30_dict:
        top30_dict[FOCUS_KEYWORD] = int(top30_dict[FOCUS_KEYWORD] * FOCUS_WEIGHT)
    else:
        # 如果没进前30，尝试单独统计
        focus_cnt = valid_words.count(FOCUS_KEYWORD)
        if focus_cnt > 0:
            top30_dict[FOCUS_KEYWORD] = int(focus_cnt * FOCUS_WEIGHT)
    # 重新排序取前30
    top30_weighted = sorted(top30_dict.items(), key=lambda x: x[1], reverse=True)[:30]
    for word, cnt in top30_weighted:
        print(f"    {{ name: '{word}', value: {cnt} }},")

    # ========== ⑭ 分平台各维度数量 ==========
    print("\n【⑭ 双平台各维度对比柱状图】")
    plat_dim_result = {}
    for plat in ["抖音", "小红书"]:
        plat_data = final_data[final_data["platform"] == plat]
        counts = []
        for dim in DIMENSION_KEYWORDS.keys():
            cnt = plat_data["dimensions"].apply(lambda x: dim in x).sum()
            counts.append(int(cnt))
        plat_dim_result[plat] = counts
    print(f"  维度列表：{list(DIMENSION_KEYWORDS.keys())}")
    print(f"  抖音数据：{plat_dim_result['抖音']}")
    print(f"  小红书数据：{plat_dim_result['小红书']}")

    # ========== ⑮ 不同情感平均点赞 ==========
    print("\n【⑮ 不同情感平均点赞数】")
    sent_like_mean = final_data.groupby("sentiment")["like_count"].mean()
    sent_likes = []
    for s in ["正面", "中性", "负面"]:
        v = round(sent_like_mean.get(s, 0), 2)
        sent_likes.append(v)
    print(f"  情感分类：['正面', '中性', '负面']")
    print(f"  平均点赞：{sent_likes}")

    # ========== ⑯ Top10高赞评论 ==========
    print("\n【⑯ Top10最高赞评论】")
    top_likes = final_data.sort_values("like_count", ascending=False).head(10)[["nickname", "content", "like_count"]]
    top_like_values = top_likes["like_count"].tolist()
    print("  点赞数排行：", top_like_values)
    print("  对应评论作者：", top_likes["nickname"].tolist())

    # ========== ⑰ Top10活跃用户 ==========
    print("\n【⑰ Top10最活跃用户】")
    top_users = final_data["nickname"].value_counts().head(10)
    active_user_values = top_users.values.tolist()
    print("  用户昵称：", list(top_users.index))
    print("  评论数量：", active_user_values)

    # ========== ⑱ IP地域分布（有数据才输出） ==========
    if has_ip:
        print("\n【⑱ 中国地图地域分布 mapData】")
        prov_count = final_data["province"].value_counts()
        for prov, cnt in prov_count.items():
            if prov not in ["未知", "其他"]:
                print(f"    {{ name: '{prov}', value: {cnt} }},")

    # ---------------------- 8. 导出结果 ----------------------
    print("\n" + "=" * 70)
    print("📁 导出结果文件")
    print("=" * 70)

    # ========== 导出前预处理，解决Excel兼容性问题 ==========
    export_df = final_data.copy()

    # 1. 时间列转字符串（去掉时区，彻底解决报错）
    if "publish_time" in export_df.columns:
        export_df["publish_time"] = export_df["publish_time"].dt.strftime("%Y-%m-%d %H:%M:%S")

    # 2. 列表类型列转成顿号分隔的字符串，Excel里正常显示
    list_columns = ["dimensions", "negative_reasons", "positive_points"]
    for col in list_columns:
        if col in export_df.columns:
            export_df[col] = export_df[col].apply(lambda x: "、".join(x) if len(x) > 0 else "")

    # 3. 导出完整清洗结果
    output_cols = ["platform", "nickname", "content", "content_clean", "content_len",
                   "publish_time", "like_count", "sentiment", "dimensions",
                   "negative_reasons", "positive_points", "province"]
    # 只保留实际存在的列
    output_cols = [c for c in output_cols if c in export_df.columns]
    export_df[output_cols].to_excel("清洗后_最终有效评论.xlsx", index=False)
    print("  ✅ 已导出：清洗后_最终有效评论.xlsx")

    # 4. 导出统计汇总表
    with pd.ExcelWriter("统计结果_全维度汇总.xlsx") as writer:
        # 核心指标
        pd.DataFrame({
            "指标": ["有效评论总数", "正面评价数", "中性评价数", "负面评价数", "正面占比", "负面占比", "总点赞数"],
            "数值": [
                total_final,
                pos_num,
                neu_num,
                neg_num,
                f"{pos_rate}%",
                f"{neg_rate}%",
                total_likes
            ]
        }).to_excel(writer, sheet_name="核心指标", index=False)

        # 维度统计
        pd.DataFrame({
            "维度": list(dim_counts.keys()),
            "评论数": list(dim_counts.values()),
            "占比": [f"{round(v / total_final * 100, 2)}%" for v in dim_counts.values()]
        }).to_excel(writer, sheet_name="维度统计", index=False)

        # 负面原因统计
        pd.DataFrame({
            "负面原因": reason_names,
            "提及次数": reason_nums,
            "累计占比": [f"{v}%" for v in cum_rate]
        }).to_excel(writer, sheet_name="负面原因统计", index=False)

        # 正面卖点统计
        pd.DataFrame({
            "正面卖点": point_names,
            "提及次数": point_nums
        }).to_excel(writer, sheet_name="正面卖点统计", index=False)

        # 月度趋势（month本身是字符串，无需额外处理）
        if months:
            month_stat.reset_index().to_excel(writer, sheet_name="月度趋势", index=False)

    print("  ✅ 已导出：统计结果_全维度汇总.xlsx")

    # ==================== 新增：导出前端 JSON ====================
    print("\n【导出前端 JSON】")
    os.makedirs("cleaned_data", exist_ok=True)  # 确保目录存在

    # ---- 组装 JSON ----
    # 平台饼图
    platform_pie = []
    for plat in ["抖音", "小红书"]:
        cnt = int(platform_counts.get(plat, 0))
        platform_pie.append({"value": cnt, "name": plat})

    # 长度分布（已计算）
    len_hist = len_count

    # 箱线图（已计算 dy_box, xhs_box）
    like_box = [dy_box, xhs_box]

    # 月度趋势（已计算）
    month_trend = {
        "total": total_month,
        "positive": pos_month,
        "negative": neg_month,
        "peakIndex": peak_idx,
        "peakValue": peak_val
    }

    # 24小时
    hour_trend = hour_count

    # 周内
    week_trend = weekday_count

    # 维度热度（按原始维度顺序，不排序）
    dim_order = list(DIMENSION_KEYWORDS.keys())
    dim_bar = [dim_counts[d] for d in dim_order]

    # 分维度情感雷达（按原始维度顺序）
    dim_radar_pos = dim_pos_rate
    dim_radar_neg = dim_neg_rate

    # 整体情感饼图
    sentiment_pie = []
    for s in ["正面", "中性", "负面"]:
        cnt = int(sent_count.get(s, 0))
        sentiment_pie.append({"value": cnt, "name": f"{s}评价"})

    # 负面帕累托
    neg_pareto_bar = reason_nums
    neg_pareto_line = cum_rate

    # 正面卖点
    pos_bar_values = point_nums

    # 词云
    wordcloud_data = [{"name": w, "value": c} for w, c in top30_weighted]

    # 主题分布（根据维度归并）
    topic_map = {
        "校园生活": ["宿舍住宿", "食堂餐饮", "校园活动"],
        "招生报考": ["招生报考"],
        "教学学业": ["教学学业"],
        "校园环境": ["校园环境", "交通出行"]
    }
    topic_pie = []
    for topic, dims in topic_map.items():
        val = sum(dim_counts.get(d, 0) for d in dims)
        topic_pie.append({"value": round(val / total_final * 100, 1) if total_final else 0, "name": topic})

    # 不同情感平均点赞
    sent_like_avg = sent_likes

    # Top10高赞（数值）
    top_like_values = top_likes["like_count"].tolist()

    # Top10活跃用户（数值）
    active_user_values = top_users.values.tolist()

    # ---- 双平台雷达（需要额外计算） ----
    # 定义指标：评论总量、正面占比、平均点赞、内容深度(平均字数)、传播速度(点赞/评论比)、用户活跃度(人均评论)
    def calc_platform_metrics(plat_df):
        if len(plat_df) == 0:
            return [0, 0, 0, 0, 0, 0]
        total = len(plat_df)
        pos_ratio = (plat_df["sentiment"] == "正面").sum() / total * 100
        avg_like = plat_df["like_count"].mean()
        avg_len = plat_df["content_len"].mean()
        # 传播速度：总点赞/总评论
        spread = plat_df["like_count"].sum() / total if total else 0
        # 用户活跃度：总评论 / 独立用户数
        active = total / plat_df["nickname"].nunique() if plat_df["nickname"].nunique() else 0
        # 归一化到0-100（取近似范围，根据实际情况调整）
        # 评论总量归一化（以总评论数最大值为100）
        max_total = max(len(final_data[final_data["platform"] == "抖音"]),
                        len(final_data[final_data["platform"] == "小红书"]))
        total_norm = (total / max_total) * 100 if max_total else 0
        # 正面占比直接使用
        pos_norm = pos_ratio
        # 平均点赞归一化（假设最大平均点赞为200）
        like_norm = min(avg_like / 200 * 100, 100)
        # 内容深度归一化（假设最大平均长度为50字）
        len_norm = min(avg_len / 50 * 100, 100)
        # 传播速度归一化（假设最大传播速度为20）
        spread_norm = min(spread / 20 * 100, 100)
        # 用户活跃度归一化（假设最大活跃度为10）
        active_norm = min(active / 10 * 100, 100)
        return [total_norm, pos_norm, like_norm, len_norm, spread_norm, active_norm]

    dy_df = final_data[final_data["platform"] == "抖音"]
    xhs_df = final_data[final_data["platform"] == "小红书"]
    plat_radar_douyin = calc_platform_metrics(dy_df)
    plat_radar_xhs = calc_platform_metrics(xhs_df)

    # 双平台分维度对比（按原始维度顺序）
    plat_dim_douyin = [plat_dim_result["抖音"][i] for i in range(len(dim_order))]
    plat_dim_xhs = [plat_dim_result["小红书"][i] for i in range(len(dim_order))]

    def convert_native(obj):
        """递归将 numpy 类型转换为 Python 原生类型"""
        if isinstance(obj, (np.int64, np.int32, np.int16, np.int8)):
            return int(obj)
        elif isinstance(obj, (np.float64, np.float32, np.float16)):
            return float(obj)
        elif isinstance(obj, (list, tuple)):
            return [convert_native(x) for x in obj]
        elif isinstance(obj, dict):
            return {k: convert_native(v) for k, v in obj.items()}
        else:
            return obj

    # ---- 组装最终 JSON ----
    final_json = {
        "overview": {
            "total": total_final,
            "positive_rate": pos_rate,
            "negative_rate": neg_rate,
            "dimensions": dim_count,
            "platforms": platform_count,
            "interactions": f"{total_likes // 10000}万" if total_likes >= 10000 else str(total_likes)
        },
        "platformPie": platform_pie,
        "lenHist": len_hist,
        "likeBox": like_box,
        "monthTrend": month_trend,
        "hourTrend": hour_trend,
        "weekTrend": week_trend,
        "dimBar": dim_bar,
        "dimRadar": {
            "positive": dim_radar_pos,
            "negative": dim_radar_neg
        },
        "sentimentPie": sentiment_pie,
        "negPareto": {
            "bar": neg_pareto_bar,
            "line": neg_pareto_line
        },
        "posBar": pos_bar_values,
        "wordCloud": wordcloud_data,
        "topicPie": topic_pie,
        "sentLike": sent_like_avg,
        "topLike": top_like_values,
        "activeUser": active_user_values,
        "platRadar": {
            "douyin": plat_radar_douyin,
            "xiaohongshu": plat_radar_xhs
        },
        "platDimBar": {
            "douyin": plat_dim_douyin,
            "xiaohongshu": plat_dim_xhs
        }
    }

    # 转换所有 numpy 类型为 Python 原生类型
    final_json = convert_native(final_json)

    # 写入文件
    json_path = "cleaned_data/dashboard_data.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(final_json, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 已生成前端所需 JSON：{json_path}")

    print("\n🎉 所有统计完成！")


if __name__ == "__main__":
    main()