#!/usr/bin/env python3
"""
多平台热点数据抓取脚本
支持三级降级策略：统一 API → 各平台接口 → WebSearch（由主Agent执行）
输出标准化 JSON 格式
"""

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ─── 配置 ───────────────────────────────────────────────────────────

UNIFIED_API_BASE = "https://60s.viki.moe/v2"

# 平台缩写映射
PLATFORM_MAP = {
    "weibo": {"name": "微博", "abbr": "wb"},
    "douyin": {"name": "抖音", "abbr": "dy"},
    "baidu": {"name": "百度", "abbr": "bd"},
    "zhihu": {"name": "知乎", "abbr": "zh"},
    "bilibili": {"name": "B站", "abbr": "bili"},
    "toutiao": {"name": "头条", "abbr": "tt"},
    "douban": {"name": "豆瓣", "abbr": "db"},
    "thepaper": {"name": "澎湃", "abbr": "pp"},
    "36kr": {"name": "36氪", "abbr": "36kr"},
    "ithome": {"name": "IT之家", "abbr": "ith"},
}

# 各平台独立接口（Level 2 备用）
PLATFORM_APIS = {
    "weibo": {
        "url": "https://weibo.com/ajax/side/hotSearch",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://weibo.com/",
        },
        "parse": "weibo",
    },
    "zhihu": {
        "url": "https://www.zhihu.com/api/v3/feed/topstory/hot-list-web",
        "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        "parse": "zhihu",
    },
    "baidu": {
        "url": "https://top.baidu.com/api/board?platform=wise&tab=realtime",
        "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        "parse": "baidu",
    },
    "bilibili": {
        "url": "https://api.bilibili.com/x/web-interface/popular?ps=20&pn=1",
        "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        "parse": "bilibili",
    },
    "toutiao": {
        "url": "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
        "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        "parse": "toutiao",
    },
    "douyin": {
        "url": "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.douyin.com/",
        },
        "parse": "douyin",
    },
}

REQUEST_TIMEOUT = 10  # 秒

# ─── 工具函数 ───────────────────────────────────────────────────────

CST = timezone(timedelta(hours=8))
now_iso = datetime.now(CST).isoformat()


def make_request(url, headers=None, timeout=REQUEST_TIMEOUT):
    """发起 HTTP GET 请求，返回 JSON 或 None"""
    try:
        req = Request(url, headers=headers or {})
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except (URLError, HTTPError, json.JSONDecodeError, Exception) as e:
        print(f"[WARN] 请求失败 {url}: {e}", file=sys.stderr)
        return None


def normalize_heat(raw_heat):
    """将各种格式的热度值标准化为数字"""
    if isinstance(raw_heat, (int, float)):
        return int(raw_heat)
    if isinstance(raw_heat, str):
        raw_heat = raw_heat.strip()
        multipliers = {"亿": 100000000, "万": 10000}
        for suffix, mult in multipliers.items():
            if suffix in raw_heat:
                try:
                    return int(float(raw_heat.replace(suffix, "")) * mult)
                except ValueError:
                    continue
        try:
            return int(float(raw_heat))
        except ValueError:
            return 0
    return 0


def format_heat(value):
    """将数字格式化为可读的热度文本"""
    if value >= 100000000:
        return f"{value / 100000000:.1f}亿"
    elif value >= 10000:
        return f"{value / 10000:.1f}万"
    else:
        return str(value)


# ─── Level 1: 统一 API ─────────────────────────────────────────────

def fetch_unified_api(platform):
    """通过统一 API 获取单个平台热榜"""
    url = f"{UNIFIED_API_BASE}/{platform}"
    data = make_request(url)
    if not data or "data" not in data:
        return None

    results = []
    abbr = PLATFORM_MAP.get(platform, {}).get("abbr", platform[:2])
    name = PLATFORM_MAP.get(platform, {}).get("name", platform)

    for idx, item in enumerate(data["data"], 1):
        heat_raw = item.get("hot", 0)
        heat_val = normalize_heat(heat_raw)
        results.append({
            "topic_id": f"{abbr}_{idx:03d}",
            "title": item.get("title", ""),
            "platform": platform,
            "platform_name": name,
            "heat_value": heat_val,
            "heat_display": format_heat(heat_val),
            "rank": idx,
            "category": item.get("category", ""),
            "url": item.get("url", ""),
            "fetch_time": now_iso,
            "data_source": "unified_api",
            "data_reliability": "high",
            "extra": {},
        })
    return results


# ─── Level 2: 各平台独立接口 ───────────────────────────────────────

def parse_weibo(data):
    """解析微博热搜接口返回数据"""
    results = []
    items = data.get("data", {}).get("realtime", [])
    for idx, item in enumerate(items, 1):
        heat_raw = item.get("num", 0)
        heat_val = normalize_heat(heat_raw)
        results.append({
            "topic_id": f"wb_{idx:03d}",
            "title": item.get("word", ""),
            "platform": "weibo",
            "platform_name": "微博",
            "heat_value": heat_val,
            "heat_display": format_heat(heat_val),
            "rank": idx,
            "category": item.get("label_name", ""),
            "url": f"https://s.weibo.com/weibo?q={item.get('word', '')}",
            "fetch_time": now_iso,
            "data_source": "platform_api",
            "data_reliability": "high",
            "extra": {"label": item.get("label_name", "")},
        })
    return results


def parse_zhihu(data):
    """解析知乎热榜接口返回数据"""
    results = []
    items = data.get("data", [])
    for idx, item in enumerate(items, 1):
        target = item.get("target", {})
        heat_text = item.get("detail_text", "")
        heat_val = normalize_heat(heat_text)
        results.append({
            "topic_id": f"zh_{idx:03d}",
            "title": target.get("title", ""),
            "platform": "zhihu",
            "platform_name": "知乎",
            "heat_value": heat_val,
            "heat_display": heat_text,
            "rank": idx,
            "category": "",
            "url": f"https://www.zhihu.com/question/{target.get('id', '')}",
            "fetch_time": now_iso,
            "data_source": "platform_api",
            "data_reliability": "high",
            "extra": {"excerpt": target.get("excerpt", "")[:100]},
        })
    return results


def parse_baidu(data):
    """解析百度热搜接口返回数据"""
    results = []
    items = data.get("data", {}).get("cards", [{}])
    if items:
        items = items[0].get("content", [])
    for idx, item in enumerate(items, 1):
        heat_text = item.get("desc", "")
        heat_val = normalize_heat(heat_text)
        results.append({
            "topic_id": f"bd_{idx:03d}",
            "title": item.get("word", ""),
            "platform": "baidu",
            "platform_name": "百度",
            "heat_value": heat_val,
            "heat_display": heat_text,
            "rank": idx,
            "category": item.get("tag", ""),
            "url": item.get("url", ""),
            "fetch_time": now_iso,
            "data_source": "platform_api",
            "data_reliability": "high",
            "extra": {"desc": heat_text},
        })
    return results


def parse_bilibili(data):
    """解析B站热门接口返回数据"""
    results = []
    items = data.get("data", {}).get("list", [])
    for idx, item in enumerate(items, 1):
        stat = item.get("stat", {})
        heat_val = stat.get("view", 0)
        results.append({
            "topic_id": f"bili_{idx:03d}",
            "title": item.get("title", ""),
            "platform": "bilibili",
            "platform_name": "B站",
            "heat_value": heat_val,
            "heat_display": format_heat(heat_val),
            "rank": idx,
            "category": item.get("tname", ""),
            "url": f"https://www.bilibili.com/video/{item.get('bvid', '')}",
            "fetch_time": now_iso,
            "data_source": "platform_api",
            "data_reliability": "high",
            "extra": {
                "play": stat.get("view", 0),
                "like": stat.get("like", 0),
                "reply": stat.get("reply", 0),
            },
        })
    return results


def parse_toutiao(data):
    """解析头条热榜接口返回数据"""
    results = []
    items = data.get("data", [])
    for idx, item in enumerate(items, 1):
        heat_val = normalize_heat(item.get("HotValue", 0))
        results.append({
            "topic_id": f"tt_{idx:03d}",
            "title": item.get("Title", ""),
            "platform": "toutiao",
            "platform_name": "头条",
            "heat_value": heat_val,
            "heat_display": format_heat(heat_val),
            "rank": idx,
            "category": item.get("Label", ""),
            "url": item.get("Url", ""),
            "fetch_time": now_iso,
            "data_source": "platform_api",
            "data_reliability": "high",
            "extra": {},
        })
    return results


def parse_douyin(data):
    """解析抖音热搜接口返回数据（iesdouyin billboard word）"""
    results = []
    items = data.get("word_list", [])
    for idx, item in enumerate(items, 1):
        heat_val = normalize_heat(item.get("hot_value", 0))
        word = item.get("word", "")
        results.append({
            "topic_id": f"dy_{idx:03d}",
            "title": word,
            "platform": "douyin",
            "platform_name": "抖音",
            "heat_value": heat_val,
            "heat_display": format_heat(heat_val),
            "rank": idx,
            "category": item.get("label", ""),
            "url": f"https://www.douyin.com/search/{word}",
            "fetch_time": now_iso,
            "data_source": "platform_api",
            "data_reliability": "high",
            "extra": {"label": item.get("label", "")},
        })
    return results


PARSERS = {
    "weibo": parse_weibo,
    "zhihu": parse_zhihu,
    "baidu": parse_baidu,
    "bilibili": parse_bilibili,
    "toutiao": parse_toutiao,
    "douyin": parse_douyin,
}


def fetch_platform_api(platform):
    """通过平台独立接口获取热榜"""
    config = PLATFORM_APIS.get(platform)
    if not config:
        return None

    data = make_request(config["url"], config.get("headers"))
    if not data:
        return None

    parser = PARSERS.get(config["parse"])
    if parser:
        return parser(data)
    return None


# ─── 主抓取函数（三级降级） ────────────────────────────────────────

def fetch_platform(platform):
    """
    抓取单个平台热榜数据，三级降级策略
    Level 1: 统一 API
    Level 2: 平台独立接口
    Level 3: 返回 None（由主Agent通过 WebSearch 降级）
    """
    # Level 1: 统一 API
    results = fetch_unified_api(platform)
    if results:
        print(f"[OK] {platform}: 统一API获取 {len(results)} 条", file=sys.stderr)
        return results

    # Level 2: 平台独立接口
    results = fetch_platform_api(platform)
    if results:
        print(f"[OK] {platform}: 平台接口获取 {len(results)} 条", file=sys.stderr)
        return results

    # Level 3: 标记需要 WebSearch 降级
    print(f"[FAIL] {platform}: API均失败，需要WebSearch降级", file=sys.stderr)
    return None


def fetch_all_platforms(platforms=None):
    """
    抓取多个平台热榜数据
    platforms: 平台列表，None 则抓取所有支持的平台
    """
    if platforms is None:
        platforms = list(PLATFORM_MAP.keys())

    all_results = {}
    failed_platforms = []

    for platform in platforms:
        results = fetch_platform(platform)
        if results:
            all_results[platform] = results
        else:
            failed_platforms.append(platform)

    output = {
        "fetch_time": now_iso,
        "success_platforms": list(all_results.keys()),
        "failed_platforms": failed_platforms,
        "total_topics": sum(len(v) for v in all_results.values()),
        "data": all_results,
    }

    return output


# ─── CLI 入口 ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="多平台热点数据抓取")
    parser.add_argument("--platforms", "-p", nargs="+", help="指定平台（如 weibo douyin zhihu）")
    parser.add_argument("--output", "-o", help="输出文件路径（默认 stdout）")
    args = parser.parse_args()

    result = fetch_all_platforms(args.platforms)
    output_json = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        print(f"结果已写入 {args.output}", file=sys.stderr)
    else:
        print(output_json)
