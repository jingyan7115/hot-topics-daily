#!/usr/bin/env python3
"""
抓取 HotFlashNews 民生（社会民生）栏目最新内容，输出 JSON。

数据源：https://hotflashnews.com/api/topic/society
（民生栏目前端路由 /topic/society，配置 id = society）
该站点约每 3-5 分钟更新，聚合微博/百度/知乎/抖音/B站等平台的民生热搜。

用法：
    python fetch_minsheng.py --output minsheng.json [--topic society] [--limit 15]
"""

import os
import sys
import json
import argparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
API_BASE = "https://hotflashnews.com/api/topic/"
TOPIC_NAME = {
    "society": "社会民生",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Referer": "https://hotflashnews.com/topic/society",
    "Accept": "application/json",
}


def fetch_topic(topic_id, limit):
    """抓取指定栏目的 JSON，返回归一化结构"""
    url = API_BASE + topic_id
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8", "ignore"))

    items = []
    for it in data.get("items", [])[:limit]:
        items.append({
            "title": (it.get("title") or "").strip(),
            "url": it.get("url", ""),
            "source": it.get("source", ""),
            "time": it.get("time") or it.get("created_at", ""),
            "hot": it.get("hot", ""),
            "rank": it.get("rank", ""),
            "full_text": it.get("full_text", ""),
        })

    return {
        "source": f"https://hotflashnews.com/topic/{topic_id}",
        "topic_id": topic_id,
        "topic_name": data.get("topic", {}).get("name") or TOPIC_NAME.get(topic_id, "社会民生"),
        "topic_desc": data.get("topic", {}).get("description", ""),
        "update_time": data.get("update_time", ""),
        "fetch_time": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "total": data.get("total", len(items)),
        "count": len(items),
        "items": items,
    }


def main():
    ap = argparse.ArgumentParser(description="抓取 HotFlashNews 民生栏目")
    ap.add_argument("--output", "-o", required=True, help="输出 JSON 路径")
    ap.add_argument("--topic", "-t", default="society", help="栏目 id（民生=society）")
    ap.add_argument("--limit", "-l", type=int, default=15, help="抓取条数（默认 15）")
    args = ap.parse_args()

    try:
        result = fetch_topic(args.topic, args.limit)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[OK] 民生栏目抓取到 {result['count']} 条，更新时间 {result['update_time']}", file=sys.stderr)
        print(f"[OK] 已写入 {args.output}", file=sys.stderr)
    except (URLError, HTTPError) as e:
        print(f"[WARN] 民生栏目抓取失败（网络）：{repr(e)[:200]}", file=sys.stderr)
        _dump_empty(args.output, args.topic, repr(e)[:200])
        sys.exit(1)
    except Exception as e:
        print(f"[WARN] 民生栏目抓取失败：{repr(e)[:200]}", file=sys.stderr)
        _dump_empty(args.output, args.topic, repr(e)[:200])
        sys.exit(1)


def _dump_empty(path, topic_id, err):
    empty = {
        "source": f"https://hotflashnews.com/topic/{topic_id}",
        "topic_id": topic_id,
        "topic_name": TOPIC_NAME.get(topic_id, "社会民生"),
        "items": [],
        "error": err,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(empty, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
