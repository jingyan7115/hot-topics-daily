#!/usr/bin/env python3
"""
热度趋势分析脚本
输入：标准化热点数据（JSON）
输出：跨平台话题聚合、趋势判断、情绪分析
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))


def load_data(filepath_or_stdin):
    """加载 JSON 数据"""
    if filepath_or_stdin and filepath_or_stdin != "-":
        with open(filepath_or_stdin, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        return json.load(sys.stdin)


# ─── 跨平台话题聚合 ────────────────────────────────────────────────

def extract_keywords(title):
    """
    从标题中提取关键词（简化版）
    实际场景中可接入 NLP 分词，这里用基础规则
    """
    # 去除常见停用词和标点
    stop_words = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都",
        "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你",
        "会", "着", "没有", "看", "好", "自己", "这", "他", "她", "吗",
        "什么", "怎么", "如何", "为什么", "哪", "哪些", "多少", "几",
        "被", "把", "让", "给", "从", "向", "对", "与", "而", "但",
        "或", "如果", "因为", "所以", "虽然", "但是", "然而", "不过",
        "可以", "已经", "正在", "将", "能", "该", "这个", "那个",
        "今日", "最新", "突发", "刚刚", "官方", "回应", "通报",
    }
    # 简单分词：按标点和空格切分，过滤短词和停用词
    import re
    words = re.split(r'[，。！？、；：""\'\'（）[\]【】\s]+', title)
    keywords = []
    for w in words:
        w = w.strip()
        if len(w) >= 2 and w not in stop_words:
            keywords.append(w)
    return keywords


def aggregate_cross_platform(all_data):
    """
    跨平台话题聚合
    识别在多个平台同时出现的话题
    """
    # 按关键词聚合
    keyword_topics = defaultdict(lambda: {
        "titles": set(),
        "platforms": set(),
        "total_heat": 0,
        "max_heat": 0,
        "entries": [],
    })

    for platform, topics in all_data.items():
        for topic in topics:
            title = topic.get("title", "")
            heat = topic.get("heat_value", 0)
            keywords = extract_keywords(title)

            for kw in keywords:
                entry = keyword_topics[kw]
                entry["titles"].add(title)
                entry["platforms"].add(platform)
                entry["total_heat"] += heat
                entry["max_heat"] = max(entry["max_heat"], heat)
                entry["entries"].append(topic)

    # 筛选跨平台话题（出现在2+平台）
    cross_platform = []
    for kw, info in keyword_topics.items():
        if len(info["platforms"]) >= 2:
            cross_platform.append({
                "keyword": kw,
                "representative_title": max(info["titles"], key=len),
                "platforms": list(info["platforms"]),
                "platform_count": len(info["platforms"]),
                "total_heat": info["total_heat"],
                "max_heat": info["max_heat"],
                "topic_count": len(info["entries"]),
            })

    # 按跨平台数量和热度排序
    cross_platform.sort(key=lambda x: (x["platform_count"], x["total_heat"]), reverse=True)

    return cross_platform[:20]  # 返回 TOP 20 跨平台话题


# ─── 趋势判断 ─────────────────────────────────────────────────────

def judge_trend(topic):
    """
    判断单个话题的趋势
    由于单次抓取无法获取历史数据，这里基于话题特征进行启发式判断

    实际使用中，建议对比两次抓取结果来判断真实趋势
    """
    title = topic.get("title", "")
    rank = topic.get("rank", 50)
    heat = topic.get("heat_value", 0)
    platform = topic.get("platform", "")

    # 启发式规则
    signals = []

    # 排名靠前且热度极高 → 可能处于爆发期
    if rank <= 3 and heat > 1000000:
        signals.append("爆发")
    # 排名靠前 → 至少是稳定期
    elif rank <= 10:
        signals.append("稳定")
    # 排名靠后 → 可能是上升期或衰退期
    elif rank <= 30:
        signals.append("观察")

    # 标题中的时效性信号
    urgency_words = ["刚刚", "突发", "最新", "官宣", "确认", "通报", "回应", "辟谣"]
    if any(w in title for w in urgency_words):
        signals.append("时效性强")

    # 综合判断
    if "爆发" in signals:
        trend = "爆发期"
        trend_icon = "🔥"
        prediction = "热度将持续上升，建议立即创作"
    elif "稳定" in signals:
        if "时效性强" in signals:
            trend = "爆发期"
            trend_icon = "🔥"
            prediction = "时效性强，红利期约1-2天"
        else:
            trend = "稳定期"
            trend_icon = "⚖️"
            prediction = "热度稳定，适合差异化角度切入"
    elif "观察" in signals:
        trend = "萌芽期/衰退期"
        trend_icon = "🌱"
        prediction = "需进一步观察热度变化"
    else:
        trend = "未知"
        trend_icon = "❓"
        prediction = "数据不足，建议持续关注"

    return {
        "trend": trend,
        "trend_icon": trend_icon,
        "prediction": prediction,
        "signals": signals,
    }


def analyze_trends(all_data):
    """对所有话题进行趋势分析"""
    trend_summary = defaultdict(lambda: {"count": 0, "topics": []})

    for platform, topics in all_data.items():
        for topic in topics:
            trend_info = judge_trend(topic)
            topic["trend"] = trend_info["trend"]
            topic["trend_icon"] = trend_info["trend_icon"]
            topic["trend_prediction"] = trend_info["prediction"]

            key = trend_info["trend"]
            trend_summary[key]["count"] += 1
            if len(trend_summary[key]["topics"]) < 5:
                trend_summary[key]["topics"].append(topic["title"])

    return dict(trend_summary)


# ─── 情绪倾向分析 ─────────────────────────────────────────────────

# 情绪关键词库
EMOTION_KEYWORDS = {
    "好奇/求知": [
        "揭秘", "真相", "背后", "原因", "为什么", "怎么回事", "竟然",
        "居然", "没想到", "原来", "内幕", "真相是", "你知道吗",
        "发现", "研究", "科学", "数据", "报告", "调查",
    ],
    "焦虑/担忧": [
        "暴跌", "崩盘", "危机", "风险", "裁员", "失业", "倒闭",
        "涨价", "缺货", "断供", "逾期", "爆雷", "亏损", "下降",
        "衰退", "紧张", "担忧", "恐慌", "不安",
    ],
    "愤怒/争议": [
        "怒", "骂", "投诉", "举报", "曝光", "黑幕", "不公",
        "歧视", "欺负", "欺骗", "造假", "违规", "处罚", "封禁",
        "争议", "对立", "抵制", "谴责", "批评",
    ],
    "喜悦/共鸣": [
        "恭喜", "成功", "突破", "冠军", "夺冠", "获奖", "升级",
        "好消息", "暖心", "感动", "泪目", "致敬", "加油", "点赞",
        "逆袭", "翻盘", "圆梦", "幸福", "美好",
    ],
    "客观/中性": [
        "发布", "公布", "通知", "公告", "政策", "规定", "方案",
        "计划", "报告", "数据", "统计", "排名", "榜单", "指数",
        "会议", "签约", "合作", "启动", "完成",
    ],
}


def analyze_sentiment(title):
    """分析单个话题的情绪倾向"""
    scores = {}
    for emotion, keywords in EMOTION_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in title)
        if score > 0:
            scores[emotion] = score

    if not scores:
        return {
            "sentiment": "客观/中性",
            "sentiment_icon": "😐",
            "confidence": "low",
        }

    # 取得分最高的情绪
    top_emotion = max(scores, key=scores.get)
    confidence = "high" if scores[top_emotion] >= 2 else "medium"

    icons = {
        "好奇/求知": "🤔",
        "焦虑/担忧": "😰",
        "愤怒/争议": "😠",
        "喜悦/共鸣": "🥰",
        "客观/中性": "😐",
    }

    return {
        "sentiment": top_emotion,
        "sentiment_icon": icons.get(top_emotion, "😐"),
        "confidence": confidence,
        "scores": scores,
    }


def analyze_all_sentiments(all_data):
    """分析所有话题的情绪分布"""
    sentiment_summary = defaultdict(lambda: {"count": 0, "topics": []})

    for platform, topics in all_data.items():
        for topic in topics:
            sentiment_info = analyze_sentiment(topic["title"])
            topic["sentiment"] = sentiment_info["sentiment"]
            topic["sentiment_icon"] = sentiment_info["sentiment_icon"]

            key = sentiment_info["sentiment"]
            sentiment_summary[key]["count"] += 1
            if len(sentiment_summary[key]["topics"]) < 5:
                sentiment_summary[key]["topics"].append(topic["title"])

    return dict(sentiment_summary)


# ─── 主分析函数 ────────────────────────────────────────────────────

def analyze(fetch_result):
    """
    完整分析流程
    输入：fetch_hot_topics.py 的输出
    输出：分析结果
    """
    all_data = fetch_result.get("data", {})

    # 1. 跨平台话题聚合
    cross_platform = aggregate_cross_platform(all_data)

    # 2. 趋势分析
    trend_summary = analyze_trends(all_data)

    # 3. 情绪分析
    sentiment_summary = analyze_all_sentiments(all_data)

    # 4. 平台热度分布
    platform_summary = {}
    for platform, topics in all_data.items():
        if topics:
            platform_summary[platform] = {
                "topic_count": len(topics),
                "top_topic": topics[0]["title"],
                "top_heat": topics[0]["heat_display"],
                "avg_heat": sum(t["heat_value"] for t in topics) // len(topics),
            }

    return {
        "analysis_time": datetime.now(CST).isoformat(),
        "cross_platform_topics": cross_platform,
        "trend_summary": trend_summary,
        "sentiment_summary": sentiment_summary,
        "platform_summary": platform_summary,
        "total_topics_analyzed": sum(len(v) for v in all_data.values()),
    }


# ─── CLI 入口 ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="热度趋势分析")
    parser.add_argument("input", help="输入 JSON 文件路径（fetch_hot_topics.py 的输出）")
    parser.add_argument("--output", "-o", help="输出文件路径（默认 stdout）")
    args = parser.parse_args()

    fetch_result = load_data(args.input)
    analysis_result = analyze(fetch_result)

    output_json = json.dumps(analysis_result, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        print(f"分析结果已写入 {args.output}", file=sys.stderr)
    else:
        print(output_json)
