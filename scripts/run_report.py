#!/usr/bin/env python3
"""
热点选题一键报告流水线（纯标准库，无需 PyYAML）

流程：fetch_hot_topics.py → analyze_trends.py → score_topics.py → 生成 Markdown 报告
支持多账号（配置中 accounts 列表），每个账号独立生成选题方案。

用法：
    python run_report.py --workdir <工作目录> [--config hot-topics-config.yaml]
"""

import os
import re
import sys
import json
import time
import subprocess
import argparse
import urllib.parse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def run_script(script_name, args, workdir):
    """运行同目录下的子脚本，返回 returncode"""
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, script_name)] + args
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"
    try:
        r = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        print(f"[WARN] {script_name} 超时", file=sys.stderr)
        return 1
    if r.returncode != 0:
        print(f"[WARN] {script_name} 返回非零：{r.stderr[-600:]}", file=sys.stderr)
    elif r.stderr.strip():
        print(r.stderr.strip(), file=sys.stderr)
    return r.returncode


def load_json(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# 复用评分脚本的辅助函数（题型识别 / 关键词提取）
sys.path.insert(0, SCRIPT_DIR)
import score_topics as st  # noqa: E402


# ─── 选题方案生成（启发式）──────────────────────────────────────

def gen_titles(topic, account):
    t = topic.get("title", "")
    acc = account.get("name", "")
    niche0 = account.get("niche", "").split(" / ")[0]
    return [
        f"{acc}带你盘｜{t}爆了，这3个细节别错过",
        f"{t}为什么刷屏？{niche0}视角一次说清",
        f"别只当瓜吃！{t}里藏着黑龙江下一个爆款选题",
    ]


def gen_outline(topic, account):
    t = topic.get("title", "")
    ttype = st._detect_topic_type(t)
    if ttype == "教程":
        return [
            f"开头（钩子）：用「{t}」里可立即上手的价值点切入，2 秒留住用户",
            "核心内容1：分步骤拆解操作/方法，避免空泛",
            "核心内容2：列出最容易踩的坑，建立信任",
            "核心内容3：给一个进阶技巧，制造收藏价值",
            "结尾（CTA）：引导「收藏+关注」，承诺持续更新该系列",
        ]
    if ttype == "测评":
        return [
            f"开头（钩子）：抛出「{t}」的对比疑问，制造期待",
            "核心内容1：维度A实测（数据/画面说话）",
            "核心内容2：维度B实测，横向对比",
            "核心内容3：给出明确结论与适用人群",
            "结尾（CTA）：引导评论区「你选哪个」，拉升互动",
        ]
    if ttype == "观点":
        return [
            f"开头（钩子）：用反常识观点切入「{t}」",
            "核心内容1：论据1（事实/案例支撑）",
            "核心内容2：论据2，补充另一视角",
            "核心内容3：反转或升华，留下记忆点",
            "结尾（CTA）：引导用户「你认同吗」，引发讨论",
        ]
    # 资讯 / 默认
    return [
        f"开头（钩子）：一句话讲清「{t}」是什么",
        "核心内容1：关键事实/时间线，信息增量",
        "核心内容2：对本地用户的影响（结合黑龙江）",
        "核心内容3：后续进展/可关注的信号",
        "结尾（CTA）：引导关注，持续跟进",
    ]


def gen_suggestions(topic, account, config):
    cp = config.get("content_preferences", {})
    fmt = cp.get("formats", ["短视频"])[0] if cp.get("formats") else "短视频"
    platform = account.get("platform", "抖音")
    niche_words = st._extract_niche_words(account.get("niche", ""))
    topic_words = st._extract_niche_words(topic.get("title", ""))
    tags = []
    for w in (niche_words + topic_words):
        if w not in tags:
            tags.append(w)
        if len(tags) >= 5:
            break
    tags = ["#" + w for w in tags]
    avoid = cp.get("avoid_topics", [])
    notes = []
    ds = topic.get("data_source") or "web"
    notes.append(f"数据来源：{ds}，发布前请二次核实")
    if avoid:
        notes.append("回避话题：" + "、".join(avoid))
    notes.append("涉及政策/数据须官方核实后发布，避免误导")
    timing = "早7-9点 / 晚18-22点（抖音通勤与睡前高峰）" if platform == "抖音" else "按平台最佳时段发布"
    return {
        "platform": platform,
        "format": fmt,
        "timing": timing,
        "tags": " ".join(tags),
        "notes": "；".join(notes),
    }


def gen_competitor_diff(account, config):
    comps = config.get("competitors", [])
    if not comps:
        return None
    c0 = comps[0]
    return {
        "covered": c0.get("strength", "同质化内容"),
        "angle": f"从「{account.get('tone', '差异化视角')}」切入，补竞品短板：{c0.get('weakness', '缺乏个人观点')}",
    }


# ─── 报告结构 ────────────────────────────────────────────────────

def build_overview(topics_data, analysis):
    # TOP10 by heat
    all_topics = []
    for plat, items in topics_data.items():
        for it in items:
            all_topics.append(it)
    all_topics.sort(key=lambda x: x.get("heat_value", 0) or 0, reverse=True)
    cross = analysis.get("cross_platform_topics", [])

    def cross_count(title):
        cnt = 1
        for cp in cross:
            kw = cp.get("keyword", "")
            if kw and kw in title:
                cnt = max(cnt, cp.get("platform_count", 1))
        return cnt

    rows = []
    for it in all_topics[:10]:
        rows.append({
            "title": esc(it.get("title", "")),
            "heat": it.get("heat_display") or it.get("heat_value", "") or "—",
            "trend": (it.get("trend_icon", "") + (it.get("trend") or "—")) or "—",
            "sentiment": (it.get("sentiment_icon", "") + (it.get("sentiment") or "—")) or "—",
            "cross": f"{cross_count(it.get('title',''))}/8",
        })
    return rows


def build_platform_dist(topics, analysis):
    ps = analysis.get("platform_summary", {})
    failed = topics.get("failed_platforms", [])
    name_map = {
        "weibo": "微博", "douyin": "抖音", "baidu": "百度", "zhihu": "知乎",
        "bilibili": "B站", "toutiao": "头条", "douban": "豆瓣",
        "thepaper": "澎湃", "36kr": "36氪", "ithome": "IT之家",
    }
    rows = []
    for key, name in name_map.items():
        if key in ps:
            info = ps[key]
            rows.append(f"| {name} | {info.get('topic_count')} | {info.get('top_topic')} | 热度 {info.get('top_heat')} |")
        elif key in failed:
            rows.append(f"| {name} | 失败 | — | 接口未返回，建议补充抓取 |")
    if not rows:
        rows.append("| — | — | — | 本次未获取到平台数据 |")
    return rows


def stars(score):
    n = max(1, min(5, round(score / 20)))
    return "⭐" * n


def esc(s):
    """转义 Markdown 表格中的特殊字符（标题里的 | 会冲破单元格）"""
    return str(s).replace("|", "｜").replace("\n", " ").strip()


# ─── Markdown → HTML 转换（自包含，仅供本报告使用）─────────────

def _inline(text):
    """行内格式：先转义 HTML 实体，再处理 **粗体** / *斜体* / [文本](链接)"""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 行内链接 [text](url) —— 在转义后处理，避免把 url 里的 & 误伤（&amp; 在 href 中合法）
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                  r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    return text


def _table_to_html(rows):
    def cells(s):
        s = s.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    header = cells(rows[0])
    body = [cells(r) for r in rows[2:]]  # 跳过表头后的分隔行
    out = ["<table>", "<thead><tr>"]
    out.append("".join(f"<th>{_inline(c)}</th>" for c in header))
    out.append("</tr></thead><tbody>")
    for br in body:
        out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in br) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def markdown_to_html(md):
    """将本报告生成的 Markdown 转换为 HTML（覆盖标题/引用/表格/列表/分割线）"""
    lines = md.split("\n")
    html, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if line.strip() == "---":
            html.append("<hr>")
            i += 1
            continue
        if line.startswith("# "):
            html.append(f"<h1>{_inline(line[2:].strip())}</h1>"); i += 1; continue
        if line.startswith("## "):
            html.append(f"<h2>{_inline(line[3:].strip())}</h2>"); i += 1; continue
        if line.startswith("### "):
            html.append(f"<h3>{_inline(line[4:].strip())}</h3>"); i += 1; continue
        if line.startswith("#### "):
            html.append(f"<h4>{_inline(line[5:].strip())}</h4>"); i += 1; continue
        if line.startswith("> "):
            bq = []
            while i < n and lines[i].startswith("> "):
                bq.append(lines[i][2:].strip()); i += 1
            html.append("<blockquote>" + _inline(" ".join(bq)) + "</blockquote>")
            continue
        if line.startswith("|"):
            tbl = []
            while i < n and lines[i].startswith("|"):
                tbl.append(lines[i]); i += 1
            html.append(_table_to_html(tbl))
            continue
        if line.startswith("- "):
            items = []
            while i < n and lines[i].startswith("- "):
                items.append(lines[i][2:].strip()); i += 1
            html.append("<ul>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + "</ul>")
            continue
        if re.match(r"^\d+\.\s", line):
            items = []
            while i < n and re.match(r"^\d+\.\s", lines[i]):
                items.append(re.sub(r"^\d+\.\s", "", lines[i]).strip()); i += 1
            html.append("<ol>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + "</ol>")
            continue
        if not line.strip():
            i += 1; continue
        html.append("<p>" + _inline(line.strip()) + "</p>")
        i += 1
    return "\n".join(html)


def _slugify(text, used):
    s = re.sub(r"[^\w一-鿿]+", "-", text).strip("-")
    if not s:
        s = "sec"
    base, i = s, 1
    while s in used:
        s = f"{base}-{i}"
        i += 1
    used[s] = True
    return s


def add_toc(html):
    """为 h2/h3 添加 id 锚点、在标题后插入目录导航，便于长报告跳转"""
    used = {}

    def repl(m):
        level, inner = m.group(1), m.group(2)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        slug = _slugify(text, used)
        return f'<h{level} id="{slug}">{inner}</h{level}>'

    html2 = re.sub(r"<h([23])>(.*?)</h\1>", repl, html, flags=re.S)
    heads = list(re.finditer(r'<h([23]) id="([^"]+)">(.*?)</h\1>', html2, re.S))
    if not heads:
        return html2
    toc = ['<nav class="toc">', '<div class="toc-title">📑 目录</div>', "<ul>"]
    for m in heads:
        level, slug = m.group(1), m.group(2)
        text = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        cls = "sub" if level == "3" else ""
        toc.append(f'<li class="{cls}"><a href="#{slug}">{text}</a></li>')
    toc.append("</ul></nav>")
    idx = html2.find("</h1>")
    if idx != -1:
        html2 = html2[:idx + 5] + "\n" + "\n".join(toc) + html2[idx + 5:]
    return html2


HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>热点选题追踪报告</title>
<style>
:root{--bg:#ffffff;--fg:#1a1a1a;--muted:#666;--line:#e5e5e5;--brand:#ff2d55;--accent:#ff7a00;--card:#fafafa;}
@media (prefers-color-scheme: dark){:root{--bg:#0f1115;--fg:#e8eaed;--muted:#9aa0a6;--line:#2a2d33;--card:#171a21;}}
*{box-sizing:border-box;}
body{margin:0;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.7;-webkit-font-smoothing:antialiased;}
.container{max-width:860px;margin:0 auto;padding:20px 16px 60px;}
h1{font-size:1.6rem;margin:0 0 6px;}
h2{font-size:1.3rem;margin:30px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--line);}
h3{font-size:1.1rem;margin:22px 0 8px;color:var(--brand);}
h4{font-size:1.02rem;margin:16px 0 6px;}
blockquote{background:var(--card);border-left:4px solid var(--accent);margin:12px 0;padding:10px 14px;border-radius:0 8px 8px 0;color:var(--muted);font-size:.92rem;}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:.86rem;}
th,td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top;}
th{background:var(--card);font-weight:600;}
tbody tr:nth-child(even){background:rgba(127,127,127,.05);}
ul,ol{padding-left:22px;margin:10px 0;}
li{margin:4px 0;}
hr{border:none;border-top:1px solid var(--line);margin:24px 0;}
p{margin:10px 0;}
strong{color:var(--brand);}
em{color:var(--muted);}
.toc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:16px 0;font-size:.9rem;}
.toc-title{font-weight:600;margin-bottom:6px;color:var(--brand);}
.toc ul{list-style:none;padding-left:0;margin:0;}
.toc li{padding:3px 0;}
.toc li.sub{padding-left:18px;font-size:.85rem;color:var(--muted);}
.toc a{color:var(--fg);text-decoration:none;}
.toc a:hover{color:var(--brand);}
.totop{position:fixed;right:16px;bottom:20px;background:var(--brand);color:#fff;width:44px;height:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;text-decoration:none;font-size:1.2rem;box-shadow:0 2px 10px rgba(0,0,0,.3);z-index:50;}
.totop:hover{opacity:.9;}
</style>
</head>
<body>
<div class="container">
<a id="top"></a>
"""

HTML_TAIL = """
<a href="#top" class="totop" aria-label="返回顶部">↑</a>
</div>
</body>
</html>
"""


# ─── 微信推送（PushPlus / Server酱，纯标准库 urllib）────────────

def build_push_markdown(scored, config, html_path, workdir, minsheng=None):
    """构造推送到微信的精简 Markdown（每个账号 Top N 选题 + 民生最新内容）"""
    now = datetime.now(CST)
    top_n = config.get("push", {}).get("top_n", 3)
    lines = [f"## 🔥 热点选题日报 · {now.strftime('%Y-%m-%d')}", ""]
    rel = os.path.relpath(html_path, workdir)
    lines.append(f"> 完整报告（HTML，手机/电脑浏览器打开）：`{rel}`")
    # 环境变量 REPORT_PUBLIC_URL 优先（供 GitHub Actions 等云端环境覆盖配置文件）
    public_url = os.environ.get("REPORT_PUBLIC_URL") or config.get("report_settings", {}).get("public_url", "")
    if public_url:
        lines.append(f"> 🌐 在线完整报告（公网）：[点击查看]({public_url})")
    lines.append("")
    for acc in scored.get("accounts", []):
        name = acc.get("name", "")
        topics = acc.get("topics", [])
        lines.append(f"### 📱 {name}")
        if not topics:
            lines.append("> 今日无高匹配选题，可关注更多平台或放宽阈值。")
            continue
        for i, t in enumerate(topics[:top_n], 1):
            title = t.get("title", "")
            grade = t.get("grade_label", "")
            score = t.get("total_score", 0)
            lines.append(f"{i}. **{title}** — {grade}（{score}/100）")
            sug = gen_titles(t, acc)
            lines.append(f"   ↳ 标题参考：{sug[0]}")
        lines.append("")
    # 民生栏目最新内容（HotFlashNews 社会民生）
    if minsheng and minsheng.get("items"):
        lines.append("### 🏠 民生热点（HotFlashNews 社会民生）")
        for i, it in enumerate(minsheng["items"][:5], 1):
            t = it.get("title", "")
            u = it.get("url", "")
            src = it.get("source", "")
            tim = it.get("time", "")
            lines.append(f"{i}. {t} — {src} {tim}")
            if u:
                lines.append(f"   🔗 [原文链接]({u})")
        lines.append(f"   来源：{minsheng.get('source','')}（更新 {minsheng.get('update_time','')}）")
        lines.append("")
    lines.append("---")
    lines.append("*由「行业热点选题追踪」Skill 自动生成并推送*")
    return "\n".join(lines)


def _truncate_wecom(text, limit=4000):
    """企业微信 markdown 消息上限 4096 字节，超出按字节安全截断并提示。"""
    b = text.encode("utf-8")
    if len(b) <= limit:
        return text
    truncated = b[:limit].decode("utf-8", "ignore")
    note = "\n\n…（内容过长已截断，完整报告见公网链接）"
    return truncated + note


def _http_post(url, data, headers, timeout=30, retries=4, backoff=8):
    """带重试的 POST（应对 GitHub 运行器到国内 API 的偶发超时）。

    返回 (resp_dict, ok)。ok=False 时 resp_dict 含 {'__error__': '...'}。
    """
    last = None
    for attempt in range(1, retries + 1):
        req = Request(url, data=data, headers=headers)
        try:
            with urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "ignore")), True
        except (URLError, HTTPError) as e:
            last = e
            if attempt < retries:
                print(f"[RETRY] 推送第{attempt}次失败({repr(e)[:80]})，{backoff}s 后重试",
                      file=sys.stderr)
                time.sleep(backoff)
    return {"__error__": repr(last)[:200]}, False


def push_report(push_cfg, title, content):
    """按配置推送（支持 wecom / pushplus / serverchan）。
    content 已是最终格式：pushplus 传 HTML，wecom/serverchan 传 Markdown。
    失败仅告警，不影响主流程。"""
    ptype = (push_cfg.get("type") or "wecom").lower()
    if ptype == "wecom":
        # 环境变量 WECOM_WEBHOOK 优先（供 GitHub Actions 等云端环境通过 Secrets 注入，避免密钥入库）
        webhook = os.environ.get("WECOM_WEBHOOK") or push_cfg.get("webhook", "")
        if not webhook or "YOUR_WECOM_BOT_KEY" in webhook:
            print("[WARN] push.type=wecom 但未配置 webhook，跳过推送", file=sys.stderr)
            return
        payload = {"msgtype": "markdown", "markdown": {"content": _truncate_wecom(content)}}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8",
                   "User-Agent": "hot-topics-skill"}
        resp, ok = _http_post(webhook, data, headers, timeout=30, retries=4, backoff=8)
        if ok and resp.get("errcode") == 0:
            print("[PUSH] 企业微信机器人推送成功", file=sys.stderr)
        elif not ok:
            print(f"[WARN] 企业微信机器人推送最终失败（重试耗尽）：{resp.get('__error__')}",
                  file=sys.stderr)
        else:
            print(f"[WARN] 企业微信机器人推送返回错误：{resp}", file=sys.stderr)
    elif ptype == "pushplus":
        token = push_cfg.get("token", "")
        if not token:
            print("[WARN] push.type=pushplus 但未配置 token，跳过推送", file=sys.stderr)
            return
        url = "https://www.pushplus.plus/send"
        payload = {"token": token, "title": title, "content": content, "template": "html"}
        topic = push_cfg.get("topic")
        if topic:
            payload["topic"] = topic
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8",
                   "User-Agent": "hot-topics-skill"}
        resp, ok = _http_post(url, data, headers, timeout=30, retries=4, backoff=8)
        if ok and resp.get("code") == 200:
            print(f"[PUSH] PushPlus 推送成功：{resp.get('msg', '')}", file=sys.stderr)
        elif not ok:
            print(f"[WARN] PushPlus 推送最终失败：{resp.get('__error__')}", file=sys.stderr)
        else:
            print(f"[WARN] PushPlus 推送返回错误：{resp}", file=sys.stderr)
    elif ptype == "serverchan":
        sendkey = push_cfg.get("sendkey", "")
        if not sendkey:
            print("[WARN] push.type=serverchan 但未配置 sendkey，跳过推送", file=sys.stderr)
            return
        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        data = urllib.parse.urlencode({"title": title, "desp": content}).encode("utf-8")
        headers = {"User-Agent": "hot-topics-skill"}
        resp, ok = _http_post(url, data, headers, timeout=30, retries=4, backoff=8)
        if ok and resp.get("code") == 0:
            print(f"[PUSH] Server酱推送成功：{resp.get('message', '')}", file=sys.stderr)
        elif not ok:
            print(f"[WARN] Server酱推送最终失败：{resp.get('__error__')}", file=sys.stderr)
        else:
            print(f"[WARN] Server酱推送返回错误：{resp}", file=sys.stderr)
    else:
        print(f"[WARN] 不支持的 push.type={ptype}，跳过推送", file=sys.stderr)


def build_account_section(acc, config, analysis):
    name = acc.get("name", "")
    niche = acc.get("niche", "")
    platform = acc.get("platform", "")
    topics = acc.get("topics", [])
    lines = []
    lines.append(f"\n## 账号：{name}（{platform}）\n")
    lines.append(f"> 赛道：{niche}　|　抖音号：{acc.get('douyin_id','')}　|　本次入选选题：{acc.get('total_scored', len(topics))} 条\n")

    if not topics:
        lines.append("> 未找到高于阈值的高匹配度选题，可放宽 `min_viral_score` 或关注更多平台。\n")
        return "\n".join(lines)

    # 赛道筛选表
    lines.append("### 赛道相关热点筛选\n")
    lines.append("| 话题 | 赛道匹配 M | 综合得分 | 差异化 D | 推荐指数 |")
    lines.append("|------|:---------:|:--------:|:---------:|:--------:|")
    for t in topics:
        s = t.get("scores", {})
        m = s.get("M", {}).get("score", 0)
        d = s.get("D", {}).get("score", 0)
        lines.append(
            f"| {esc(t.get('title',''))} | {m} | {t.get('total_score')} | {d} | {stars(t.get('total_score',0))} |"
        )
    lines.append("")

    # 重点选题方案（最多 5 个）
    lines.append("### 重点选题方案\n")
    for i, t in enumerate(topics[:5], 1):
        title = t.get("title", "")
        grade = t.get("grade_label", "")
        score = t.get("total_score", 0)
        s = t.get("scores", {})
        lines.append(f"#### 📌 选题 {i}：{title}\n")
        lines.append(f"**爆款潜力评级**：{grade}（{score}/100）\n")
        lines.append("**六维度评分**：\n")
        lines.append("| 维度 | 权重 | 得分 | 分析 |")
        lines.append("|------|:----:|:----:|------|")
        for dim, label in [("H", "热度趋势"), ("M", "赛道匹配"), ("D", "竞争差异"),
                           ("E", "情绪激活"), ("T", "时效窗口"), ("I", "互动潜力")]:
            d = s.get(dim, {})
            lines.append(f"| {label} {dim} | {d.get('weight','')} | {d.get('score',0)}/100 | {d.get('analysis','')} |")
        total_w = "100%"
        lines.append(f"| **综合** | **{total_w}** | **{score}/100** | **{grade}** |")
        lines.append("")

        # 标题
        titles = gen_titles(t, acc)
        lines.append("**推荐标题（3选1）**：\n")
        lines.append(f"1. {titles[0]} — 数字+痛点型")
        lines.append(f"2. {titles[1]} — 悬念提问型")
        lines.append(f"3. {titles[2]} — 反常识/对比型\n")

        # 大纲
        lines.append("**结构化大纲**：\n")
        for b in gen_outline(t, acc):
            lines.append(f"- {b}")
        lines.append("")

        # 建议
        sug = gen_suggestions(t, acc, config)
        lines.append("**创作建议**：\n")
        lines.append("| 项目 | 建议 |")
        lines.append("|------|------|")
        lines.append(f"| 推荐平台 | {sug['platform']} |")
        lines.append(f"| 推荐形式 | {sug['format']} |")
        lines.append(f"| 发布时机 | {sug['timing']} |")
        lines.append(f"| 关键标签 | {sug['tags']} |")
        lines.append(f"| 注意事项 | {sug['notes']} |\n")

        # 竞品差异
        cd = gen_competitor_diff(acc, config)
        if cd:
            lines.append("**竞品差异化**：\n")
            lines.append(f"- 竞品已覆盖：{cd['covered']}")
            lines.append(f"- 建议切入角度：{cd['angle']}\n")

        lines.append("---\n")

    return "\n".join(lines)


def build_sentiment_trend(analysis):
    sent = analysis.get("sentiment_summary", {})
    total = sum(v.get("count", 0) for v in sent.values()) or 1
    lines = []
    lines.append("### 🎭 热点情绪分布\n")
    lines.append("| 情绪类型 | 占比 | 代表话题 |")
    lines.append("|:--------:|:----:|---------|")
    icon_map = {
        "好奇/求知": "🤔", "焦虑/担忧": "😰", "愤怒/争议": "😠",
        "喜悦/共鸣": "🥰", "客观/中性": "😐",
    }
    for k, v in sent.items():
        pct = round(v.get("count", 0) / total * 100)
        tops = "、".join(v.get("topics", [])[:2])
        lines.append(f"| {icon_map.get(k,'')} {k} | {pct}% | {tops} |")
    lines.append("")

    trend = analysis.get("trend_summary", {})
    lines.append("### 📈 趋势分布\n")
    for k, v in trend.items():
        lines.append(f"- {k}：{v.get('count',0)} 条（如：{('、'.join(v.get('topics',[])[:2]))}）")
    lines.append("")
    return "\n".join(lines)


def build_minsheng(minsheng, config):
    """民生选题分栏：来源 HotFlashNews 社会民生栏目最新内容"""
    if not minsheng or not minsheng.get("items"):
        err = minsheng.get("error") if minsheng else "无数据"
        return ("## 四、民生选题（来源：HotFlashNews 社会民生）\n\n"
                f"> 本次民生栏目抓取失败（{err or '空'}），可手动查看 {minsheng.get('source','https://hotflashnews.com/topic/society') if minsheng else 'https://hotflashnews.com/topic/society'}\n")

    lines = []
    lines.append("## 四、民生选题（来源：HotFlashNews 社会民生）\n")
    lines.append(f"> 栏目：{minsheng.get('topic_name','社会民生')}　|　更新时间：{minsheng.get('update_time','')}　|　抓取 {minsheng.get('count')} 条")
    lines.append(f"> 来源网址：{minsheng.get('source','')}（约每 3-5 分钟更新，聚合公共安全/教育/医疗/消费/出行/天气等民生热点）\n")

    # 最新民生热点表格（标题可点击跳转原文）
    lines.append("### 📰 最新民生热点\n")
    lines.append("| # | 民生热点 | 来源 | 时间 | 热度 |")
    lines.append("|:--:|------|------|:----:|:----:|")
    for i, it in enumerate(minsheng["items"][:15], 1):
        title = esc(it.get("title", ""))
        url = it.get("url", "")
        if url:
            title = f"[{title}]({url})"
        lines.append(f"| {i} | {title} | {it.get('source','')} | {it.get('time','')} | {it.get('hot','')} |")
    lines.append("")

    # 黑龙江账号借势角度（启发式，结合两个抖音账号定位）
    lines.append("### 💡 黑龙江账号借势角度\n")
    lines.append("- **公共安全 / 天气 / 出行**类：从黑龙江本地视角做「避险提醒 / 便民服务」短视频，契合政务民生定位；")
    lines.append("- **教育 / 医疗**类：延展「黑龙江本地政策 / 高校 / 医疗资源」解读，建立实用信息号人设；")
    lines.append("- **消费 / 出行**类：衔接「黑龙江文旅 / 特产」做种草或避坑，发布前务必核实官方信息，避免误导。")
    lines.append("")

    # 与两个账号赛道相关的重点提示（取前 3 条作为当日重点）
    lines.append("### 🎯 当日重点民生选题\n")
    for i, it in enumerate(minsheng["items"][:3], 1):
        lines.append(f"{i}. **{esc(it.get('title',''))}**（{it.get('source','')} {it.get('time','')}）")
        lines.append(f"   ↳ 切入：本地化解读 + 实用信息增量，标题可套用「黑龙江人注意 / 黑龙江版」角度")
    lines.append("")
    return "\n".join(lines)


def build_history(config):
    pvc = config.get("past_viral_content", [])
    if not pvc:
        return ""
    lines = []
    lines.append("## 五、历史爆款参考\n")
    lines.append("> 仅列出过往爆款作为选题方向校准（相似度需结合更多信号评估）。\n")
    lines.append("| 过往爆款 | 数据 | 赛道 | 为何爆 |")
    lines.append("|---------|------|------|--------|")
    for p in pvc:
        lines.append(f"| {esc(p.get('title',''))} | {p.get('views','')}/{p.get('likes','')} | {p.get('topic','')} | {esc(p.get('why_viral',''))} |")
    lines.append("")
    return "\n".join(lines)


def build_report(topics, analysis, scored, config, workdir, minsheng=None):
    now = datetime.now(CST)
    fetch_time = topics.get("fetch_time", "")
    meta = config.get("report_metadata", {})
    owner = meta.get("owner", "")
    overview = build_overview(topics.get("data", {}), analysis)
    plat_rows = build_platform_dist(topics, analysis)

    out = []
    out.append("# 🔥 热点选题追踪报告\n")
    out.append(f"> 生成时间：{now.strftime('%Y-%m-%d %H:%M')}　|　{owner}")
    out.append(f"> 数据来源：微博/百度/知乎/B站/头条/抖音等平台热榜 + HotFlashNews 民生栏目（豆瓣/澎湃/36氪/IT之家接口未返回，建议补充抓取）\n")
    out.append("---\n")

    out.append("## 一、全网热点概览\n")
    out.append("### 📊 热度 TOP 10\n")
    out.append("| 排名 | 话题 | 全网热度 | 趋势 | 情绪 | 跨平台 |")
    out.append("|:----:|------|:--------:|:----:|:----:|:------:|")
    for i, r in enumerate(overview, 1):
        out.append(f"| {i} | {r['title']} | {r['heat']} | {r['trend']} | {r['sentiment']} | {r['cross']} |")
    out.append("")
    out.append("### 📱 平台热度分布\n")
    out.append("| 平台 | 热点数 | TOP1 话题 | 内容特征 |")
    out.append("|:----:|:------:|----------|---------|")
    out.extend(plat_rows)
    out.append("")

    out.append("## 二、分账号选题方案\n")
    for acc in scored.get("accounts", []):
        out.append(build_account_section(acc, config, analysis))

    out.append("## 三、情绪与趋势分析\n")
    out.append(build_sentiment_trend(analysis))

    # 四、民生选题（来源 HotFlashNews 社会民生）
    out.append(build_minsheng(minsheng, config))

    hist = build_history(config)
    if hist:
        out.append(hist)

    next_update = (now + timedelta(hours=12)).strftime('%Y-%m-%d %H:%M')
    out.append(f"\n---\n")
    out.append("*报告由「行业热点选题追踪」Skill 自动生成（一键流水线 run_report.py）*")
    out.append(f"*数据抓取时间：{fetch_time}　|　报告生成时间：{now.isoformat()}*")
    out.append(f"*建议更新时间：{next_update}*")
    return "\n".join(out)


# ─── 主流程 ───────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="热点选题一键报告")
    ap.add_argument("--workdir", "-w", default=os.getcwd(), help="工作目录（配置与输出所在）")
    ap.add_argument("--config", "-c", default="hot-topics-config.yaml", help="配置文件名（位于 workdir）")
    args = ap.parse_args()

    workdir = os.path.abspath(args.workdir)
    config_path = os.path.join(workdir, args.config)
    # 回退：workdir 没有配置时，使用 skill 目录内的默认配置（用户级，跨对话可用）
    if not os.path.exists(config_path):
        # skill 根目录（与 SKILL.md 同级）或 scripts/ 子目录均可
        skill_cfg = os.path.join(os.path.dirname(SCRIPT_DIR), args.config)
        if not os.path.exists(skill_cfg):
            skill_cfg = os.path.join(SCRIPT_DIR, args.config)
        if os.path.exists(skill_cfg):
            print(f"[INFO] 工作目录无配置，回退使用 skill 内置配置：{skill_cfg}", file=sys.stderr)
            config_path = skill_cfg
        else:
            print(f"[ERROR] 配置文件不存在：{config_path}", file=sys.stderr)
            print("请先准备 hot-topics-config.yaml（可参考 SKILL.md 引导流程）。", file=sys.stderr)
            sys.exit(1)

    # 读取配置以拿到 output_dir 与账号信息
    cfg = st.load_config(config_path)
    out_dir = cfg.get("report_settings", {}).get("output_dir", "./hot-topics-reports")
    out_dir = os.path.join(workdir, out_dir.lstrip("./")) if out_dir.startswith("./") else out_dir
    os.makedirs(out_dir, exist_ok=True)

    print(f"[1/4] 抓取热点数据 ...", file=sys.stderr)
    topics_path = os.path.join(workdir, "topics.json")
    run_script("fetch_hot_topics.py", ["--output", topics_path], workdir)

    print(f"[2/4] 趋势与情绪分析 ...", file=sys.stderr)
    analysis_path = os.path.join(workdir, "analysis.json")
    run_script("analyze_trends.py", [topics_path, "--output", analysis_path], workdir)

    print(f"[3/4] 选题潜力评分（多账号）...", file=sys.stderr)
    scored_path = os.path.join(workdir, "scored.json")
    run_script("score_topics.py",
               ["--topics", topics_path, "--config", config_path,
                "--cross-platform", analysis_path, "--output", scored_path], workdir)

    print(f"[4/4] 生成 Markdown 报告 ...", file=sys.stderr)
    topics = load_json(topics_path)
    analysis = load_json(analysis_path)
    scored = load_json(scored_path)

    # 抓取民生栏目（HotFlashNews 社会民生），独立步骤，失败不影响主报告
    print(f"[+] 抓取民生栏目（HotFlashNews 社会民生）...", file=sys.stderr)
    minsheng_path = os.path.join(workdir, "minsheng.json")
    run_script("fetch_minsheng.py", ["--output", minsheng_path, "--limit", "15"], workdir)
    try:
        minsheng = load_json(minsheng_path)
    except Exception:
        minsheng = {"source": "https://hotflashnews.com/topic/society", "topic_name": "社会民生", "items": []}

    report = build_report(topics, analysis, scored, cfg, workdir, minsheng)

    ts = datetime.now(CST).strftime("%Y-%m-%d-%H-%M")
    report_path = os.path.join(out_dir, f"hot-topics-report-{ts}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"报告已生成：{report_path}", file=sys.stderr)

    # 同时生成自包含、响应式的 HTML 报告（手机/电脑均可直接打开）
    html = HTML_HEAD + add_toc(markdown_to_html(report)) + HTML_TAIL
    html_path = os.path.join(out_dir, f"hot-topics-report-{ts}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML 报告已生成：{html_path}", file=sys.stderr)

    # 同步写出稳定文件名 index.html（供 CloudStudio 等静态托管作为入口，每日覆盖刷新）
    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML 入口已生成：{index_path}", file=sys.stderr)

    # 微信推送（配置 push.enabled=true 时，把报告摘要推送出去）
    push_cfg = cfg.get("push", {})
    if push_cfg.get("enabled"):
        pm_md = build_push_markdown(scored, cfg, html_path, workdir, minsheng)
        ptype = (push_cfg.get("type") or "pushplus").lower()
        content = markdown_to_html(pm_md) if ptype == "pushplus" else pm_md
        push_report(push_cfg, f"🔥 热点选题日报 {datetime.now(CST).strftime('%Y-%m-%d')}", content)
    # 输出摘要到 stdout：TOP3 选题
    print("=== 报告摘要 ===")
    for acc in scored.get("accounts", []):
        print(f"\n【{acc.get('name')}】入选 {acc.get('total_scored')} 条")
        for t in acc.get("topics", [])[:3]:
            print(f"  {t.get('grade_label')} {t.get('total_score')} | {t.get('title')}")
    return html_path


if __name__ == "__main__":
    main()
