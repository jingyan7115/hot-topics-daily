#!/usr/bin/env python3
"""
选题潜力评分脚本
输入：热点数据 + 用户配置
输出：带评分的选题排序列表（JSON）
"""

import json
import sys
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))


def load_json(filepath):
    """加载 JSON 文件"""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── 极简 YAML 解析器（无需 PyYAML）──────────────────────────────
# 仅支持本配置文件使用的子集：嵌套映射、列表(标量/映射)、注释、引号字符串、标量类型推断。
# 优先使用 PyYAML；不可用时回退到本解析器，保证沙箱环境一键跑通。

def _strip_comment(line):
    """去掉行内注释（不在引号内的 #，且前面有空格或为行首）"""
    in_q = None
    out = []
    for i, c in enumerate(line):
        if c in ('"', "'"):
            if in_q == c:
                in_q = None
            elif in_q is None:
                in_q = c
            out.append(c)
        elif c == '#' and in_q is None:
            if i == 0 or line[i - 1] in (' ', '\t'):
                break
            out.append(c)
        else:
            out.append(c)
    return ''.join(out)


def _split_kv(s):
    """在第一个不在引号内的 ': ' 处切分 key/value，返回 (key, value) 或 None"""
    in_q = None
    for i, c in enumerate(s):
        if c in ('"', "'"):
            if in_q == c:
                in_q = None
            elif in_q is None:
                in_q = c
        elif c == ':' and in_q is None:
            if i + 1 >= len(s) or s[i + 1] in (' ', '\t'):
                return s[:i].strip(), s[i + 1:].strip()
    return None


def _strip_quotes(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1]
    return v


def _coerce_scalar(v):
    v = _strip_quotes(v)
    if v == '':
        return None
    if v in ('true', 'True'):
        return True
    if v in ('false', 'False'):
        return False
    if v in ('null', '~', 'None'):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _parse_block(lines, pos, min_indent):
    """递归解析一个缩进块，返回 dict / list / None"""
    result = None
    while pos[0] < len(lines):
        indent, content = lines[pos[0]]
        if indent < min_indent:
            break
        if content.startswith('- '):
            if result is None:
                result = []
            item = content[2:].strip()
            pos[0] += 1
            kv = _split_kv(item)
            if kv is not None:
                m = {}
                key, val = kv
                if val == '':
                    m[key] = _parse_block(lines, pos, indent + 2)
                else:
                    m[key] = _coerce_scalar(val)
                # 继续读取本映射的其余键（缩进 = indent + 2）
                while pos[0] < len(lines):
                    ni, nc = lines[pos[0]]
                    if ni == indent + 2 and not nc.startswith('- '):
                        kv2 = _split_kv(nc)
                        if kv2:
                            k2, v2 = kv2
                            if v2 == '':
                                pos[0] += 1
                                m[k2] = _parse_block(lines, pos, ni + 2)
                            else:
                                m[k2] = _coerce_scalar(v2)
                                pos[0] += 1
                        else:
                            pos[0] += 1
                    else:
                        break
                result.append(m)
            else:
                result.append(_coerce_scalar(item))
        else:
            if result is None:
                result = {}
            kv = _split_kv(content)
            if kv is None:
                pos[0] += 1
                continue
            key, val = kv
            pos[0] += 1
            if val == '':
                child = _parse_block(lines, pos, indent + 2)
                result[key] = child if child is not None else {}
            else:
                result[key] = _coerce_scalar(val)
    return result


def parse_simple_yaml(text):
    """极简 YAML 解析（仅解析本配置使用的子集）"""
    lines = []
    for ln in text.split('\n'):
        s = _strip_comment(ln)
        if s.strip() == '':
            continue
        indent = len(s) - len(s.lstrip(' '))
        lines.append((indent, s.strip()))
    return _parse_block(lines, [0], 0)


def load_config(filepath):
    """加载配置：优先 JSON，其次 PyYAML，最后内置极简解析器"""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()
    if filepath.endswith('.json'):
        return json.loads(text)
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        return parse_simple_yaml(text)


# ─── 维度一：热度趋势 H（25%）─────────────────────────────────────

def score_heat_trend(topic, cross_platform_topics):
    """
    评分热度趋势
    - 搜索频率（用热度值近似）：0-10
    - 跨平台覆盖：0-10
    - 热度增速（启发式）：0-10
    """
    heat = topic.get("heat_value", 0)
    platform = topic.get("platform", "")
    title = topic.get("title", "")

    # 搜索频率分（用热度值近似）
    if heat >= 10000000:
        search_freq_score = 10
    elif heat >= 1000000:
        search_freq_score = 8
    elif heat >= 100000:
        search_freq_score = 6
    elif heat >= 10000:
        search_freq_score = 4
    elif heat >= 1000:
        search_freq_score = 2
    else:
        search_freq_score = 1

    # 跨平台覆盖分
    cross_count = 1  # 至少在当前平台
    for cp in cross_platform_topics:
        # 简单匹配：标题包含关键词或关键词包含标题中的词
        cp_kw = cp.get("keyword", "")
        if cp_kw and cp_kw in title:
            cross_count = max(cross_count, cp.get("platform_count", 1))
            break
        # 也检查 representative_title 是否相似
        rep_title = cp.get("representative_title", "")
        if rep_title and _similarity(title, rep_title) > 0.5:
            cross_count = max(cross_count, cp.get("platform_count", 1))
            break

    if cross_count >= 5:
        cross_score = 10
    elif cross_count >= 4:
        cross_score = 8
    elif cross_count >= 3:
        cross_score = 6
    elif cross_count >= 2:
        cross_score = 4
    else:
        cross_score = 2

    # 热度增速分（启发式，基于趋势信息）
    trend = topic.get("trend", "")
    if trend == "爆发期":
        speed_score = 10
    elif trend == "萌芽期/衰退期":
        speed_score = 5  # 不确定，给中间值
    elif trend == "稳定期":
        speed_score = 4
    else:
        speed_score = 5  # 默认中间值

    H = round((search_freq_score * 0.4 + cross_score * 0.3 + speed_score * 0.3) * 10)

    analysis_parts = []
    if search_freq_score >= 8:
        analysis_parts.append("热度极高")
    elif search_freq_score >= 6:
        analysis_parts.append("热度较高")
    else:
        analysis_parts.append("热度一般")

    if cross_score >= 6:
        analysis_parts.append(f"覆盖{cross_count}个平台")
    else:
        analysis_parts.append("单平台话题")

    if speed_score >= 8:
        analysis_parts.append("热度急速上升")
    elif speed_score >= 5:
        analysis_parts.append("热度稳步变化")

    return H, "；".join(analysis_parts)


# ─── 维度二：赛道匹配度 M（25%）───────────────────────────────────

def score_niche_match(topic, config):
    """
    评分赛道匹配度
    - 关键词命中：0-10
    - 粉丝画像契合：0-10
    - 内容风格适配：0-10
    """
    title = topic.get("title", "")
    account = config.get("account", {})
    niche = account.get("niche", "")
    sub_niches = account.get("sub_niches", [])
    tone = account.get("tone", "")
    audience = account.get("target_audience", {})
    preferences = config.get("content_preferences", {})
    formats = preferences.get("formats", [])
    avoid_topics = preferences.get("avoid_topics", [])

    # 检查回避话题
    for avoid in avoid_topics:
        if avoid in title:
            return 0, f"命中回避话题「{avoid}」"

    # 关键词命中分
    niche_words = _extract_niche_words(niche)
    niche_words.extend(_extract_niche_words(" ".join(sub_niches)))

    hit_count = sum(1 for w in niche_words if w in title)
    if hit_count >= 2:
        keyword_score = 10
    elif hit_count == 1:
        keyword_score = 6
    elif _has_indirect_relevance(title, niche_words):
        keyword_score = 3
    else:
        keyword_score = 0

    # 粉丝画像契合分（启发式）
    interests = audience.get("interests", [])
    pain_points = audience.get("pain_points", [])
    audience_words = _extract_niche_words(" ".join(interests + pain_points))

    audience_hit = sum(1 for w in audience_words if w in title)
    if audience_hit >= 2:
        audience_score = 10
    elif audience_hit == 1:
        audience_score = 6
    elif keyword_score >= 6:  # 赛道匹配高，画像也给中等分
        audience_score = 6
    else:
        audience_score = 3

    # 内容风格适配分
    if formats:
        # 根据话题特征判断适合的形式
        topic_type = _detect_topic_type(title)
        suitable_formats = {
            "教程": ["图文教程", "视频教程", "使用技巧"],
            "测评": ["工具对比", "测评", "开箱"],
            "观点": ["观点输出", "深度分析", "评论"],
            "资讯": ["资讯", "快讯", "解读"],
            "故事": ["故事", "案例", "经历分享"],
        }
        topic_formats = suitable_formats.get(topic_type, [])
        overlap = set(topic_formats) & set(formats)
        if overlap:
            style_score = 10
        elif formats:
            style_score = 7  # 有擅长形式，可以调整
        else:
            style_score = 5
    else:
        style_score = 7  # 未配置，给默认分

    M = round((keyword_score * 0.4 + audience_score * 0.35 + style_score * 0.25) * 10)

    analysis_parts = []
    if keyword_score >= 6:
        analysis_parts.append(f"命中赛道「{niche}」")
    elif keyword_score >= 3:
        analysis_parts.append("与赛道间接相关")
    else:
        analysis_parts.append("与赛道关联度低")

    if audience_score >= 6:
        analysis_parts.append("目标用户契合度高")
    if style_score >= 7:
        analysis_parts.append("内容形式适配")

    return M, "；".join(analysis_parts) if analysis_parts else "需人工判断适配度"


# ─── 维度三：竞争差异化 D（20%）───────────────────────────────────

def score_competition_diff(topic, config, all_topics):
    """
    评分竞争差异化
    - 同类内容密度：0-10
    - 差异化角度空间：0-10
    - 竞品覆盖度：0-10
    """
    title = topic.get("title", "")
    platform = topic.get("platform", "")
    competitors = config.get("competitors", [])

    # 同类内容密度（用同平台相似话题数量近似）
    similar_count = 0
    for t in all_topics:
        if t.get("platform") == platform and t.get("title") != title:
            if _similarity(title, t.get("title", "")) > 0.3:
                similar_count += 1

    if similar_count <= 2:
        density_score = 10  # 蓝海
    elif similar_count <= 5:
        density_score = 6  # 中等
    else:
        density_score = 3  # 红海

    # 差异化角度空间（启发式）
    topic_type = _detect_topic_type(title)
    if topic_type in ["观点", "故事"]:
        angle_score = 10  # 观点和故事类差异化空间大
    elif topic_type in ["测评", "教程"]:
        angle_score = 6  # 测评和教程角度相对固定
    else:
        angle_score = 7

    # 竞品覆盖度
    if not competitors:
        competitor_score = 7  # 无竞品信息，给默认分
    else:
        # 简单启发式：如果竞品优势和当前话题相关，认为可能已覆盖
        covered = 0
        for comp in competitors:
            strength = comp.get("strength", "")
            if any(w in title for w in _extract_niche_words(strength)):
                covered += 1
        if covered == 0:
            competitor_score = 10
        elif covered < len(competitors):
            competitor_score = 6
        else:
            competitor_score = 2

    D = round((density_score * 0.35 + angle_score * 0.35 + competitor_score * 0.30) * 10)

    analysis_parts = []
    if density_score >= 8:
        analysis_parts.append("蓝海话题，同类内容少")
    elif density_score >= 5:
        analysis_parts.append("同类内容中等密度")
    else:
        analysis_parts.append("红海话题，竞争激烈")

    if angle_score >= 8:
        analysis_parts.append("差异化角度空间大")

    return D, "；".join(analysis_parts)


# ─── 维度四：情绪激活度 E（15%）───────────────────────────────────

def score_emotion_activation(topic):
    """
    评分情绪激活度
    - 情绪强度：0-10
    - 共鸣广度：0-10
    - 争议性：0-10
    """
    title = topic.get("title", "")
    sentiment = topic.get("sentiment", "客观/中性")

    # 情绪强度
    high_emotion_types = ["焦虑/担忧", "愤怒/争议", "喜悦/共鸣"]
    medium_emotion_types = ["好奇/求知"]
    if sentiment in high_emotion_types:
        emotion_score = 10
    elif sentiment in medium_emotion_types:
        emotion_score = 6
    else:
        emotion_score = 3

    # 共鸣广度（启发式：与日常生活相关的共鸣更广）
    life_keywords = ["工资", "房价", "社保", "医保", "教育", "孩子", "父母",
                     "上班", "通勤", "外卖", "快递", "手机", "电费"]
    niche_keywords = ["AI", "算法", "模型", "芯片", "量子", "区块链", "元宇宙"]
    life_hit = sum(1 for w in life_keywords if w in title)
    niche_hit = sum(1 for w in niche_keywords if w in title)

    if life_hit >= 1:
        breadth_score = 10  # 全民共鸣
    elif niche_hit >= 1:
        breadth_score = 6  # 圈层共鸣
    else:
        breadth_score = 6  # 默认中等

    # 争议性
    controversy_keywords = ["该不该", "要不要", "到底", "VS", "vs", "对比",
                           "选择", "哪个好", "谁对谁错", "反转", "打脸",
                           "争议", "质疑", "反对", "支持"]
    controversy_hit = sum(1 for w in controversy_keywords if w in title)
    if controversy_hit >= 2:
        controversy_score = 10
    elif controversy_hit == 1:
        controversy_score = 7
    else:
        controversy_score = 4

    E = round((emotion_score * 0.40 + breadth_score * 0.30 + controversy_score * 0.30) * 10)

    analysis_parts = [f"情绪倾向：{sentiment}"]
    if breadth_score >= 8:
        analysis_parts.append("全民共鸣潜力")
    if controversy_score >= 7:
        analysis_parts.append("具有争议性，易引发讨论")

    return E, "；".join(analysis_parts)


# ─── 维度五：时效窗口 T（10%）────────────────────────────────────

def score_timeliness(topic):
    """
    评分时效窗口
    - 生命周期阶段：0-10
    - 剩余红利时间：0-10
    """
    trend = topic.get("trend", "")
    title = topic.get("title", "")

    # 生命周期阶段
    stage_scores = {
        "爆发期": 10,
        "稳定期": 6,
        "萌芽期/衰退期": 5,
    }
    stage_score = stage_scores.get(trend, 5)

    # 剩余红利时间（启发式）
    urgency_words = ["刚刚", "突发", "最新", "官宣", "确认", "通报", "回应", "辟谣"]
    evergreen_words = ["教程", "指南", "攻略", "合集", "盘点", "推荐", "测评"]

    if any(w in title for w in urgency_words):
        time_score = 4  # 时效性强但红利短
    elif any(w in title for w in evergreen_words):
        time_score = 10  # 常青话题，红利期长
    elif trend == "爆发期":
        time_score = 7
    elif trend == "稳定期":
        time_score = 5
    else:
        time_score = 5

    T = round((stage_score * 0.50 + time_score * 0.50) * 10)

    analysis_parts = [f"当前阶段：{trend}"]
    if time_score >= 8:
        analysis_parts.append("红利期较长")
    elif time_score <= 4:
        analysis_parts.append("红利期较短，需快速创作")

    return T, "；".join(analysis_parts)


# ─── 维度六：互动潜力 I（5%）─────────────────────────────────────

def score_interaction_potential(topic):
    """
    评分互动潜力
    - 评论讨论空间：0-10
    - UGC 引发可能：0-10
    """
    title = topic.get("title", "")

    # 评论讨论空间
    open_ended = ["你怎么看", "你觉得呢", "评论区", "大家", "你们",
                  "该不该", "选哪个", "哪个好", "值不值得"]
    if any(w in title for w in open_ended):
        comment_score = 10
    else:
        sentiment = topic.get("sentiment", "")
        if sentiment in ["愤怒/争议", "好奇/求知"]:
            comment_score = 8
        else:
            comment_score = 5

    # UGC 引发可能
    ugc_keywords = ["挑战", "测试", "清单", "盘点", "排行", "你的",
                    "晒一晒", "打卡", "跟风", "模仿"]
    ugc_hit = sum(1 for w in ugc_keywords if w in title)
    if ugc_hit >= 1:
        ugc_score = 10
    else:
        ugc_score = 5

    I = round((comment_score * 0.60 + ugc_score * 0.40) * 10)

    analysis_parts = []
    if comment_score >= 8:
        analysis_parts.append("话题开放性强，易引发评论")
    if ugc_score >= 8:
        analysis_parts.append("有UGC引发潜力")

    return I, "；".join(analysis_parts) if analysis_parts else "互动潜力中等"


# ─── 综合评分 ─────────────────────────────────────────────────────

def get_grade(score):
    """根据综合得分返回评级"""
    if score >= 90:
        return "S", "🏆 S"
    elif score >= 80:
        return "A", "⭐ A"
    elif score >= 70:
        return "B", "✅ B"
    elif score >= 60:
        return "C", "⚠️ C"
    else:
        return "D", "❌ D"


def score_all_topics(all_data, config, cross_platform_topics):
    """对所有话题进行评分"""
    all_topics = []
    for platform, topics in all_data.items():
        all_topics.extend(topics)

    scored_topics = []
    for topic in all_topics:
        H, H_analysis = score_heat_trend(topic, cross_platform_topics)
        M, M_analysis = score_niche_match(topic, config)
        D, D_analysis = score_competition_diff(topic, config, all_topics)
        E, E_analysis = score_emotion_activation(topic)
        T, T_analysis = score_timeliness(topic)
        I, I_analysis = score_interaction_potential(topic)

        total = round(H * 0.25 + M * 0.25 + D * 0.20 + E * 0.15 + T * 0.10 + I * 0.05)
        grade, grade_label = get_grade(total)

        scored_topics.append({
            "topic_id": topic.get("topic_id", ""),
            "title": topic.get("title", ""),
            "platform": topic.get("platform", ""),
            "platform_name": topic.get("platform_name", ""),
            "heat_value": topic.get("heat_value", 0),
            "heat_display": topic.get("heat_display", ""),
            "scores": {
                "H": {"score": H, "weight": "25%", "analysis": H_analysis},
                "M": {"score": M, "weight": "25%", "analysis": M_analysis},
                "D": {"score": D, "weight": "20%", "analysis": D_analysis},
                "E": {"score": E, "weight": "15%", "analysis": E_analysis},
                "T": {"score": T, "weight": "10%", "analysis": T_analysis},
                "I": {"score": I, "weight": "5%", "analysis": I_analysis},
            },
            "total_score": total,
            "grade": grade,
            "grade_label": grade_label,
            "sentiment": topic.get("sentiment", ""),
            "sentiment_icon": topic.get("sentiment_icon", "😐"),
            "trend": topic.get("trend", ""),
            "trend_icon": topic.get("trend_icon", "❓"),
            "url": topic.get("url", ""),
        })

    # 按综合得分排序
    scored_topics.sort(key=lambda x: x["total_score"], reverse=True)

    return scored_topics


# ─── 辅助函数 ─────────────────────────────────────────────────────

def _extract_niche_words(text):
    """从赛道描述中提取关键词"""
    import re
    words = re.split(r'[/、，,和与及]', text)
    return [w.strip() for w in words if len(w.strip()) >= 2]


def _has_indirect_relevance(title, niche_words):
    """判断标题是否与赛道间接相关"""
    # 简单的字符重叠检测
    for nw in niche_words:
        # 检查是否有共同字符（至少2个）
        common = set(nw) & set(title)
        if len(common) >= 2:
            return True
    return False


def _detect_topic_type(title):
    """检测话题类型"""
    type_keywords = {
        "教程": ["教程", "怎么", "如何", "方法", "步骤", "技巧", "指南", "攻略"],
        "测评": ["测评", "评测", "对比", "哪个好", "推荐", "排行", "榜单", "开箱"],
        "观点": ["认为", "应该", "不该", "必须", "千万别", "一定", "其实", "本质"],
        "资讯": ["发布", "宣布", "官宣", "最新", "突发", "确认", "消息", "报道"],
        "故事": ["经历", "故事", "从...到", "逆袭", "翻盘", "圆梦", "真实"],
    }
    for t, keywords in type_keywords.items():
        if any(kw in title for kw in keywords):
            return t
    return "资讯"  # 默认


def _similarity(s1, s2):
    """简单的字符串相似度（字符级 Jaccard）"""
    if not s1 or not s2:
        return 0.0
    set1 = set(s1)
    set2 = set(s2)
    intersection = set1 & set2
    union = set1 | set2
    return len(intersection) / len(union) if union else 0.0


# ─── CLI 入口 ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="选题潜力评分")
    parser.add_argument("--topics", "-t", required=True, help="热点数据 JSON 文件")
    parser.add_argument("--config", "-c", required=True, help="用户配置 YAML/JSON 文件")
    parser.add_argument("--cross-platform", "-x", help="跨平台话题 JSON 文件（可选）")
    parser.add_argument("--output", "-o", help="输出文件路径（默认 stdout）")
    parser.add_argument("--min-score", "-m", type=int, default=None, help="最低收录分数（None 表示用配置）")
    parser.add_argument("--max-topics", type=int, default=None, help="最大输出选题数（None 表示用配置）")
    args = parser.parse_args()

    # 加载配置（优先 PyYAML，失败用内置极简解析器，无需安装依赖）
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"[ERROR] 配置加载失败：{e}", file=sys.stderr)
        sys.exit(1)

    # 加载热点数据
    with open(args.topics, "r", encoding="utf-8") as f:
        fetch_result = json.load(f)
    all_data = fetch_result.get("data", {})

    # 加载跨平台话题
    cross_platform = []
    if args.cross_platform:
        with open(args.cross_platform, "r", encoding="utf-8") as f:
            cp_result = json.load(f)
        cross_platform = cp_result.get("cross_platform_topics", [])

    # 报告设置（用于阈值）
    rs = config.get("report_settings", {})

    # 解析账号列表（支持多账号 accounts，兼容单账号 account）
    accounts = config.get("accounts")
    if not accounts:
        single = config.get("account")
        accounts = [single] if single else []

    # 按账号分别评分
    scored_accounts = []
    for acc in accounts:
        per_cfg = dict(config)
        per_cfg["account"] = acc
        scored = score_all_topics(all_data, per_cfg, cross_platform)
        # 阈值：CLI 优先（显式传参，含 0），否则用配置
        min_score = args.min_score if args.min_score is not None else rs.get("min_viral_score", 0)
        max_topics = args.max_topics if args.max_topics is not None else rs.get("max_topics", 50)
        scored = [t for t in scored if t["total_score"] >= min_score]
        scored = scored[:max_topics]
        scored_accounts.append({
            "name": acc.get("name", ""),
            "niche": acc.get("niche", ""),
            "platform": acc.get("platform", ""),
            "douyin_id": acc.get("douyin_id", ""),
            "total_scored": len(scored),
            "topics": scored,
        })

    output = {
        "score_time": datetime.now(CST).isoformat(),
        "account_count": len(scored_accounts),
        "accounts": scored_accounts,
    }

    output_json = json.dumps(output, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        print(f"评分结果已写入 {args.output}", file=sys.stderr)
    else:
        print(output_json)
