#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI日报自动采集与过滤
- RSS抓取 + GitHub Release采集
- Kimi Coding API (Anthropic兼容格式) 语义过滤
- 生成静态HTML日报并发布到GitHub Pages
"""

import os
import sys
import re
import json
import hashlib
import datetime
import glob
import traceback
from typing import List, Dict, Any, Optional

import yaml
import requests
import feedparser


def now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str):
    print(f"[{now_str()}] {msg}", flush=True)


def md5_id(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def flatten_keywords(cfg_keywords: Dict[str, List[str]]) -> List[str]:
    result = []
    for lst in cfg_keywords.values():
        result.extend(lst)
    return result


class Article:
    def __init__(self, title: str, url: str, source: str,
                 published: datetime.datetime, summary: str = "",
                 source_type: str = "rss", raw_body: str = ""):
        self.title = title
        self.url = url
        self.source = source
        self.published = published
        self.summary = summary
        self.id = md5_id(url)
        self.score: Optional[int] = None
        self.tags: List[str] = []
        self.ai_summary: str = ""
        self.action: str = ""
        self.talk: str = ""
        self.source_type = source_type
        self.raw_body = raw_body


def fetch_rss(url: str, name: str) -> List[Article]:
    articles: List[Article] = []
    cutoff = datetime.datetime.now() - datetime.timedelta(days=7)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    log(f"[RSS] 开始抓取: {name} -> {url}")
    try:
        parsed = feedparser.parse(url, request_headers=headers)
        if parsed.bozo and hasattr(parsed, "bozo_exception"):
            log(f"[RSS] 警告 {name}: {parsed.bozo_exception}")

        for entry in getattr(parsed, "entries", []):
            published = None
            for field in ("published_parsed", "updated_parsed", "created_parsed"):
                if hasattr(entry, field):
                    t = getattr(entry, field)
                    if t:
                        published = datetime.datetime(*t[:6])
                        break

            if published is None:
                for field in ("published", "updated", "created"):
                    if hasattr(entry, field):
                        val = getattr(entry, field)
                        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
                            try:
                                published = datetime.datetime.strptime(val, fmt)
                                break
                            except Exception:
                                pass
                        if published:
                            break

            if published is None:
                published = datetime.datetime.now()

            if published.tzinfo:
                published = published.replace(tzinfo=None)

            if published < cutoff:
                continue

            title = entry.get("title", "无标题")
            link = entry.get("link", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            summary = re.sub(r"<[^>]+>", "", summary)
            summary = summary.strip()[:800]

            if not link:
                continue

            articles.append(Article(title=title, url=link, source=name,
                                     published=published, summary=summary,
                                     source_type="rss"))

        log(f"[RSS] {name} 完成，有效 {len(articles)} 篇")
    except Exception as e:
        log(f"[RSS] 错误 {name}: {e}")
    return articles


def fetch_github_releases(url: str, name: str, max_count: int = 2) -> List[Article]:
    articles: List[Article] = []
    cutoff = datetime.datetime.now() - datetime.timedelta(days=7)
    headers = {
        "User-Agent": "AI-Daily-News-Bot/1.0",
        "Accept": "application/vnd.github.v3+json",
    }
    log(f"[GitHub] 开始抓取: {name} -> {url}")
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 403:
            log(f"[GitHub] 警告 {name}: 403 限流，跳过")
            return articles
        resp.raise_for_status()
        releases = resp.json()

        if not isinstance(releases, list):
            log(f"[GitHub] 警告 {name}: 返回非列表数据: {str(releases)[:200]}")
            return articles

        count = 0
        for rel in releases[:max_count]:
            published_str = rel.get("published_at", "")
            if not published_str:
                continue
            try:
                published = datetime.datetime.strptime(
                    published_str.replace("Z", "+00:00"), "%Y-%m-%dT%H:%M:%S%z"
                ).replace(tzinfo=None)
            except Exception:
                continue

            if published < cutoff:
                continue

            tag = rel.get("tag_name", "未知版本")
            title = f"[{name}] Release {tag}"
            link = rel.get("html_url", "")
            body_raw = rel.get("body") or ""
            body = body_raw
            body = re.sub(r"!\[.*?\]\(.*?\)", "", body)
            body = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", body)
            for ch in "#*`>_-":
                body = body.replace(ch, "")
            summary = body.strip()[:800]

            if not link:
                continue

            articles.append(Article(title=title, url=link, source=f"GitHub-{name}",
                                     published=published, summary=summary,
                                     source_type="github_release", raw_body=body_raw))
            count += 1

        log(f"[GitHub] {name} 完成，有效 {count} 篇")
    except requests.exceptions.RequestException as e:
        log(f"[GitHub] 请求错误 {name}: {e}")
    except Exception as e:
        log(f"[GitHub] 错误 {name}: {e}")
    return articles


def prefilter(articles: List[Article], cfg: Dict[str, Any]) -> List[Article]:
    keywords = flatten_keywords(cfg.get("keywords", {}))
    filtered: List[Article] = []
    for art in articles:
        text = (art.title + " " + art.summary).lower()
        matched = [k for k in keywords if k.lower() in text]
        if matched:
            log(f"[预过滤] 保留: {art.title[:40]}... | 匹配: {matched}")
            filtered.append(art)
        else:
            log(f"[预过滤] 丢弃: {art.title[:40]}...")
    log(f"[预过滤] 统计: 保留 {len(filtered)} / {len(articles)} 篇")
    return filtered


VALID_TAGS = [
    "AIOps-日志", "AIOps-告警", "AIOps-变更", "AIOps-容量",
    "Agent-运维", "RAG-运维知识库", "算力-昇腾", "算力-国产",
    "办公-文档", "办公-PPT", "办公-会议", "办公-邮件",
    "办公-Excel", "办公-RPA", "办公-数字员工",
    "模型-国产", "模型-开源", "工程-部署", "安全-幻觉"
]

FILTER_PROMPT_TEMPLATE = """你是一名国企AI技术顾问。请分析以下文章，判断与"数据中心运维"或"国企办公智能化"的相关度。

【文章】
标题：{title}
摘要：{summary}

【评分指导】
你是为国企数据中心运维工程师筛选资讯。请放宽标准：
- 涉及开源模型、国产大模型（千问/DeepSeek/Kimi/智谱）、AI Agent框架、RAG工具、国产算力适配的文章，即使不直接讲运维，也可能对我们的技术选型有影响，请给至少2分。
- 涉及办公自动化（文档/PPT/邮件/Excel/RPA）、数字员工、智能会议的文章，给至少2分。
- 只有纯娱乐、纯消费级硬件、纯学术理论（无落地价值）的文章才给0-1分。

【输出要求】
请严格只输出以下JSON格式，不要任何其他文字，不要markdown代码块：

{{
  "score": 0-5,
  "tags": ["标签1", "标签2"],
  "summary": "一句话技术总结（工程师语言，不浮夸）",
  "action": "如果score>=3，给出2周内可验证的具体动作",
  "talk": "如果score>=4，给出30秒向技术型领导汇报的话术，包含技术价值和业务价值"
}}

标签必须从以下枚举中选择（最多3个）：
AIOps-日志、AIOps-告警、AIOps-变更、AIOps-容量、Agent-运维、RAG-运维知识库、算力-昇腾、算力-国产、办公-文档、办公-PPT、办公-会议、办公-邮件、办公-Excel、办公-RPA、办公-数字员工、模型-国产、模型-开源、工程-部署、安全-幻觉"""


def ai_filter(article: Article, cfg: Dict[str, Any]) -> Optional[Article]:
    api_key = os.environ.get("MOONSHOT_API_KEY", "")
    if not api_key:
        log("[AI过滤] 错误: 环境变量 MOONSHOT_API_KEY 未设置")
        return None
    model = cfg.get("filter", {}).get("model", "kimi-for-coding")
    prompt = FILTER_PROMPT_TEMPLATE.format(title=article.title, summary=article.summary)
    url = "https://api.kimi.com/coding/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 800,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=90)
        resp.raise_for_status()
        data = resp.json()
        raw_text = data["content"][0]["text"]
        log(f"[AI过滤] 原始响应: {raw_text[:200]}")
        cleaned = raw_text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        result = json.loads(cleaned)
        try:
            article.score = int(result.get("score", 0))
        except (ValueError, TypeError):
            article.score = 0
        raw_tags = result.get("tags", [])
        article.tags = [t for t in raw_tags if t in VALID_TAGS][:3]
        article.ai_summary = result.get("summary", "")
        article.action = result.get("action", "")
        article.talk = result.get("talk", "")
        return article
    except requests.exceptions.HTTPError as e:
        log(f"[AI过滤] HTTP错误: {e.response.status_code} | {e.response.text[:200]}")
    except json.JSONDecodeError as e:
        log(f"[AI过滤] JSON解析错误: {e} | 原始文本: {raw_text[:200]}")
    except (KeyError, IndexError) as e:
        log(f"[AI过滤] 响应结构错误: {e}")
    except Exception:
        log(f"[AI过滤] 未知异常: {traceback.format_exc()}")
    return None


RELEASE_TAG_MAP = {
    "LangChain": ["办公-RPA", "Agent-运维"],
    "Ollama": ["工程-部署", "模型-开源"],
    "vLLM": ["工程-部署", "算力-国产"],
    "Dify": ["办公-数字员工", "Agent-运维"],
    "FastGPT": ["RAG-运维知识库", "办公-数字员工"],
    "RAGFlow": ["RAG-运维知识库", "工程-部署"],
    "MaxKB": ["RAG-运维知识库", "办公-文档"],
    "QAnything": ["RAG-运维知识库", "模型-国产"],
}

SECURITY_KEYWORDS = ["security", "fix", "bug", "cve", "critical", "breaking"]


def auto_score_release(article: Article) -> Article:
    project = article.source.replace("GitHub-", "")

    body_lower = article.raw_body.lower()
    if any(k in body_lower for k in SECURITY_KEYWORDS):
        article.score = 4
    else:
        article.score = 3

    article.tags = RELEASE_TAG_MAP.get(project, ["模型-开源", "工程-部署"])[:2]

    first_line = ""
    for line in article.raw_body.splitlines():
        stripped = line.strip()
        if stripped:
            stripped = re.sub(r"!\[.*?\]\(.*?\)", "", stripped)
            stripped = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", stripped)
            for ch in "#*`>_-":
                stripped = stripped.replace(ch, "")
            first_line = stripped.strip()
            break

    if first_line:
        article.ai_summary = first_line[:120]
    else:
        tag = article.title.split("Release ")[-1] if "Release " in article.title else "新版本"
        article.ai_summary = f"发布新版本 {tag}"

    article.action = "评估该版本是否涉及安全修复或性能提升，决定是否在内网测试环境升级验证"
    article.talk = "该工具是我们技术栈的重要组件，此版本更新可能影响现有部署方案，建议安排评估"

    return article


def generate_html(rss_articles: List[Article], release_articles: List[Article]) -> str:
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    high = [a for a in rss_articles if (a.score or 0) >= 3]
    normal = [a for a in rss_articles if (a.score or 0) == 2]

    def esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def render_high(art: Article) -> str:
        score_cls = "score-5" if art.score == 5 else "score-4"
        badges = "".join(f'<span class="badge">{esc(t)}</span>' for t in art.tags)
        action_html = f'<div class="action"><strong>落地建议：</strong>{esc(art.action)}</div>' if art.action else ""
        talk_html = f'<div class="talk"><strong>💡 领导话术</strong>{esc(art.talk)}</div>' if art.talk else ""
        lines = [
            '    <div class="article">',
            '      <div class="article-header">',
            f'        <span class="score {score_cls}">[{art.score}分]</span>',
            f'        <a href="{art.url}" target="_blank" class="article-title">{esc(art.title)}</a>',
            '      </div>',
            f'      <div class="meta">来源：{esc(art.source)} | ID：{art.id}</div>',
            f'      <div class="badges">{badges}</div>',
            f'      <div class="summary">{esc(art.ai_summary)}</div>',
            f'      {action_html}',
            f'      {talk_html}',
            '    </div>',
            '',
        ]
        return "\n".join(lines)

    def render_normal(art: Article) -> str:
        badges = "".join(f'<span class="badge">{esc(t)}</span>' for t in art.tags)
        lines = [
            '    <div class="article normal">',
            '      <div class="article-header">',
            f'        <span class="score">[{art.score}分]</span>',
            f'        <a href="{art.url}" target="_blank" class="article-title">{esc(art.title)}</a>',
            '      </div>',
            f'      <div class="meta">来源：{esc(art.source)} | ID：{art.id}</div>',
            f'      <div class="badges">{badges}</div>',
            f'      <div class="summary">{esc(art.ai_summary)}</div>',
            '    </div>',
            '',
        ]
        return "\n".join(lines)

    def render_release(art: Article) -> str:
        if art.score == 4:
            type_label = "🔴 安全/重要"
            type_cls = "release-security"
        else:
            type_label = "🔵 常规更新"
            type_cls = "release-normal"

        version = art.title.split("Release ")[-1] if "Release " in art.title else ""
        project = art.source.replace("GitHub-", "")

        badges = "".join(f'<span class="badge">{esc(t)}</span>' for t in art.tags)

        lines = [
            '    <div class="article release">',
            '      <div class="article-header">',
            f'        <span class="release-type {type_cls}">{type_label}</span>',
            f'        <a href="{art.url}" target="_blank" class="article-title">{esc(project)} {esc(version)}</a>',
            '      </div>',
            f'      <div class="meta">来源：{esc(art.source)}</div>',
            f'      <div class="badges">{badges}</div>',
            f'      <div class="summary">{esc(art.ai_summary)}</div>',
            '    </div>',
            '',
        ]
        return "\n".join(lines)

    total_count = len(rss_articles) + len(release_articles)
    high_value_count = len([a for a in rss_articles if (a.score or 0) >= 4])

    html_parts = [
        '<!DOCTYPE html>',
        '<html lang="zh-CN">',
        '<head>',
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f'<title>AI日报 {today} | 运维+办公双场景</title>',
        '<style>',
        '  :root {',
        '    --bg: #f8f9fa;',
        '    --card-bg: #ffffff;',
        '    --text: #212529;',
        '    --text-secondary: #6c757d;',
        '    --border-blue: #0d6efd;',
        '    --border-gray: #adb5bd;',
        '    --score-5: #dc3545;',
        '    --score-4: #fd7e14;',
        '    --badge-bg: #e9ecef;',
        '    --talk-bg: #fff3cd;',
        '    --talk-border: #ffc107;',
        '    --link: #0d6efd;',
        '  }',
        '  * { box-sizing: border-box; margin: 0; padding: 0; }',
        '  body {',
        '    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,',
        '                 "Helvetica Neue", Arial, "Noto Sans SC", sans-serif;',
        '    background: var(--bg);',
        '    color: var(--text);',
        '    line-height: 1.6;',
        '    padding: 1rem;',
        '  }',
        '  .container { max-width: 960px; margin: 0 auto; }',
        '  header { text-align: center; padding: 2rem 1rem; margin-bottom: 1.5rem; }',
        '  header h1 { font-size: 1.75rem; font-weight: 700; margin-bottom: 0.5rem; }',
        '  header .subtitle { color: var(--text-secondary); font-size: 0.95rem; }',
        '  .stats {',
        '    display: flex; justify-content: center; gap: 2rem;',
        '    margin-bottom: 2rem; flex-wrap: wrap;',
        '  }',
        '  .stat-item {',
        '    background: var(--card-bg); padding: 1rem 1.5rem;',
        '    border-radius: 0.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08);',
        '    text-align: center; min-width: 120px;',
        '  }',
        '  .stat-item .number { font-size: 1.5rem; font-weight: 700; color: var(--border-blue); }',
        '  .stat-item .label { font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.25rem; }',
        '  .section { margin-bottom: 2rem; }',
        '  .section-title {',
        '    font-size: 1.1rem; font-weight: 600; margin-bottom: 1rem;',
        '    padding-left: 0.75rem; border-left: 4px solid var(--border-blue);',
        '  }',
        '  .section-title.normal { border-left-color: var(--border-gray); }',
        '  .section-title.release { border-left-color: #20c997; }',
        '  .article {',
        '    background: var(--card-bg); border-radius: 0.5rem; padding: 1.25rem;',
        '    margin-bottom: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08);',
        '    border-left: 4px solid var(--border-blue);',
        '  }',
        '  .article.normal { border-left-color: var(--border-gray); }',
        '  .article.release { border-left-color: #20c997; }',
        '  .article-header {',
        '    display: flex; flex-wrap: wrap; align-items: baseline;',
        '    gap: 0.5rem; margin-bottom: 0.5rem;',
        '  }',
        '  .score { font-weight: 700; font-size: 1.1rem; }',
        '  .score-5 { color: var(--score-5); }',
        '  .score-4 { color: var(--score-4); }',
        '  .article-title {',
        '    font-size: 1.05rem; font-weight: 600; color: var(--link); text-decoration: none;',
        '  }',
        '  .article-title:hover { text-decoration: underline; }',
        '  .meta { font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 0.5rem; }',
        '  .badges { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-bottom: 0.5rem; }',
        '  .badge {',
        '    background: var(--badge-bg); color: var(--text-secondary);',
        '    font-size: 0.75rem; padding: 0.15rem 0.5rem; border-radius: 0.25rem;',
        '  }',
        '  .summary { font-size: 0.9rem; margin-bottom: 0.5rem; }',
        '  .action { font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.5rem; }',
        '  .action strong { color: var(--text); }',
        '  .talk {',
        '    background: var(--talk-bg); border: 1px solid var(--talk-border);',
        '    border-radius: 0.35rem; padding: 0.6rem 0.8rem;',
        '    font-size: 0.85rem; color: #664d03;',
        '  }',
        '  .talk strong { display: block; margin-bottom: 0.2rem; color: #856404; }',
        '  .release-type {',
        '    font-size: 0.8rem; font-weight: 600; padding: 0.15rem 0.5rem;',
        '    border-radius: 0.25rem; margin-right: 0.3rem;',
        '  }',
        '  .release-type.release-security { background: #f8d7da; color: #721c24; }',
        '  .release-type.release-normal { background: #d1ecf1; color: #0c5460; }',
        '  footer {',
        '    text-align: center; color: var(--text-secondary); font-size: 0.8rem;',
        '    padding: 2rem 1rem; border-top: 1px solid #dee2e6; margin-top: 2rem;',
        '  }',
        '  footer .sources { margin-top: 0.5rem; line-height: 1.8; }',
        '  @media (max-width: 640px) {',
        '    header h1 { font-size: 1.4rem; }',
        '    .article { padding: 1rem; }',
        '    .stats { gap: 1rem; }',
        '  }',
        '</style>',
        '</head>',
        '<body>',
        '<div class="container">',
        '  <header>',
        f'    <h1>🤖 AI日报 {today}</h1>',
        '    <p class="subtitle">运维 + 办公双场景技术资讯过滤</p>',
        '  </header>',
        '  <div class="stats">',
        f'    <div class="stat-item"><div class="number">{total_count}</div><div class="label">共筛选</div></div>',
        f'    <div class="stat-item"><div class="number">{high_value_count}</div><div class="label">高价值</div></div>',
        f'    <div class="stat-item"><div class="number">{len(rss_articles)}</div><div class="label">RSS资讯</div></div>',
        f'    <div class="stat-item"><div class="number">{len(release_articles)}</div><div class="label">工具更新</div></div>',
        '  </div>',
    ]

    if high:
        html_parts.append('  <div class="section">')
        html_parts.append('    <div class="section-title">🔥 高优先级（3-5分）</div>')
        for art in high:
            html_parts.append(render_high(art))
        html_parts.append('  </div>')

    if normal:
        html_parts.append('  <div class="section">')
        html_parts.append('    <div class="section-title normal">📌 值得关注（2分）</div>')
        for art in normal:
            html_parts.append(render_normal(art))
        html_parts.append('  </div>')

    if release_articles:
        html_parts.append('  <div class="section">')
        html_parts.append('    <div class="section-title release">🛠 开源基础设施更新</div>')
        for art in release_articles:
            html_parts.append(render_release(art))
        html_parts.append('  </div>')

    if not rss_articles and not release_articles:
        html_parts.append('  <div class="section" style="text-align:center;color:var(--text-secondary);padding:3rem 1rem;">今日无符合条件的资讯</div>')

    sources_list = [
        "量子位 RSS", "机器之心 RSS", "InfoQ AI前线 RSS",
        "DeepSeek / 通义千问 / Kimi / 智谱AI GitHub Releases",
        "LangChain / Ollama / vLLM / Dify GitHub Releases",
        "FastGPT / RAGFlow / MaxKB / QAnything GitHub Releases",
    ]
    html_parts.extend([
        '  <footer>',
        f'    <div>生成时间：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>',
        f'    <div class="sources">信源：{" / ".join(sources_list)}</div>',
        '  </footer>',
        '</div>',
        '</body>',
        '</html>',
    ])

    html = "\n".join(html_parts)
    os.makedirs("docs", exist_ok=True)
    path = "docs/index.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"[HTML] 已生成: {path}")
    return path


def generate_md(results: List[Article]) -> str:
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    high = [a for a in results if (a.score or 0) >= 3]
    lines = [
        f"# AI日报 {today} — 高优先级归档",
        "",
        f"> 生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 共筛选 {len(results)} 条，高价值 {len(high)} 条",
        "",
    ]
    for art in high:
        lines.append(f"## [{art.score}分] {art.title}")
        lines.append(f"- **来源**：{art.source}")
        lines.append(f"- **链接**：{art.url}")
        lines.append(f"- **标签**：{', '.join(art.tags)}")
        lines.append(f"- **总结**：{art.ai_summary}")
        if art.action:
            lines.append(f"- **落地建议**：{art.action}")
        if art.talk:
            lines.append(f"- **领导话术**：{art.talk}")
        lines.append("")
    if not high:
        lines.append("今日无高优先级资讯。")
        lines.append("")
    os.makedirs("docs", exist_ok=True)
    path = f"docs/{today}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"[Markdown] 已生成: {path}")
    return path


def cleanup_old_md():
    cutoff = datetime.datetime.now() - datetime.timedelta(days=30)
    removed = 0
    for path in glob.glob("docs/*.md"):
        basename = os.path.basename(path)
        try:
            date_str = basename.replace(".md", "")
            file_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            if file_date < cutoff:
                os.remove(path)
                removed += 1
                log(f"[清理] 删除旧文件: {basename}")
        except ValueError:
            pass
    log(f"[清理] 共删除 {removed} 个旧Markdown文件")


def main():
    log("=" * 60)
    log("AI日报自动采集与过滤 开始运行")
    log("=" * 60)
    try:
        cfg = load_config()
    except Exception as e:
        log(f"[致命错误] 加载config.yaml失败: {e}")
        sys.exit(1)
    api_key = os.environ.get("MOONSHOT_API_KEY", "")
    if not api_key:
        log("[致命错误] 环境变量 MOONSHOT_API_KEY 未设置")
        sys.exit(1)

    min_score = cfg.get("filter", {}).get("min_score", 2)
    max_articles = cfg.get("filter", {}).get("max_articles_per_day", 20)
    gh_cfg = cfg.get("filter", {}).get("github_releases", {})
    gh_max_per_source = gh_cfg.get("max_per_source", 2)

    all_articles: List[Article] = []
    for src in cfg.get("sources", {}).get("rss", []):
        all_articles.extend(fetch_rss(src["url"], src["name"]))
    for src in cfg.get("sources", {}).get("github_releases", []):
        all_articles.extend(fetch_github_releases(src["url"], src["name"], gh_max_per_source))

    log(f"[采集] 总计 {len(all_articles)} 篇")

    rss_articles = [a for a in all_articles if a.source_type == "rss"]
    release_articles = [a for a in all_articles if a.source_type == "github_release"]
    log(f"[采集] RSS {len(rss_articles)} 篇, Release {len(release_articles)} 篇")

    filtered_rss = prefilter(rss_articles, cfg)

    scored_releases: List[Article] = []
    for art in release_articles:
        auto_score_release(art)
        scored_releases.append(art)
        log(f"[Release自动评分] {art.title[:50]}... | score={art.score} | tags={art.tags}")

    scored_rss: List[Article] = []
    if filtered_rss:
        ai_limit = 30
        for idx, art in enumerate(filtered_rss[:ai_limit], 1):
            log(f"[AI过滤] ({idx}/{min(len(filtered_rss), ai_limit)}) 分析: {art.title[:40]}...")
            result = ai_filter(art, cfg)
            if result is None:
                log(f"[AI过滤] 失败，跳过: {art.title[:40]}...")
                continue
            log(f"[AI过滤] 评分: {result.score} | 标签: {result.tags}")
            if result.score >= min_score:
                scored_rss.append(result)
            else:
                log(f"[AI过滤] 丢弃（分数不足）: {art.title[:40]}...")
        log(f"[AI过滤] 保留 >= {min_score} 分的RSS文章: {len(scored_rss)} 篇")
    else:
        log("[预过滤] 无匹配RSS文章")

    seen_rss: set = set()
    deduped_rss: List[Article] = []
    for art in scored_rss:
        if art.id not in seen_rss:
            seen_rss.add(art.id)
            deduped_rss.append(art)
    if len(deduped_rss) < len(scored_rss):
        log(f"[去重] RSS去除 {len(scored_rss) - len(deduped_rss)} 篇重复，剩余 {len(deduped_rss)} 篇")

    seen_rel: set = set()
    deduped_releases: List[Article] = []
    for art in scored_releases:
        if art.id not in seen_rel:
            seen_rel.add(art.id)
            deduped_releases.append(art)
    if len(deduped_releases) < len(scored_releases):
        log(f"[去重] Release去除 {len(scored_releases) - len(deduped_releases)} 篇重复，剩余 {len(deduped_releases)} 篇")

    final = deduped_releases + deduped_rss
    final = final[:max_articles]

    final_releases = [a for a in final if a.source_type == "github_release"]
    final_rss = [a for a in final if a.source_type == "rss"]

    log(f"[截断] 最终输出 RSS {len(final_rss)} 篇, Release {len(final_releases)} 篇（上限 {max_articles}）")

    generate_html(final_rss, final_releases)
    generate_md(final)
    cleanup_old_md()
    log("=" * 60)
    log("AI日报自动采集与过滤 运行完成")
    log("=" * 60)


if __name__ == "__main__":
    main()
