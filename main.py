#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI办公日报自动采集与过滤
- RSS办公资讯抓取
- GitHub Trending本周热门开源项目抓取
- Kimi Coding API 语义分析与办公价值评估
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


class NewsItem:
    def __init__(self, title: str, url: str, source: str,
                 published: datetime.datetime, summary: str = ""):
        self.title = title
        self.url = url
        self.source = source
        self.published = published
        self.summary = summary
        self.id = md5_id(url)
        self.score: Optional[int] = None
        self.core_summary: str = ""
        self.office_advice: str = ""
        self.scenarios: List[str] = []
        self.thinking: str = ""


class RepoItem:
    def __init__(self, name: str, url: str, description: str,
                 language: str, stars: int):
        self.name = name
        self.url = url
        self.description = description
        self.language = language
        self.stars = stars
        self.id = md5_id(url)
        self.score: Optional[int] = None
        self.core_summary: str = ""
        self.office_value: str = ""
        self.combo_usage: str = ""
        self.thinking: str = ""


def fetch_rss(url: str, name: str) -> List[NewsItem]:
    items: List[NewsItem] = []
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

            items.append(NewsItem(title=title, url=link, source=name,
                                  published=published, summary=summary))

        log(f"[RSS] {name} 完成，有效 {len(items)} 篇")
    except Exception as e:
        log(f"[RSS] 错误 {name}: {e}")
    return items


def fetch_github_trending() -> List[RepoItem]:
    repos: List[RepoItem] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html",
    }
    url = "https://github.com/trending?since=weekly"
    log(f"[GitHub] 开始抓取 Trending: {url}")
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        html = resp.text

        blocks = re.findall(
            r'<article[^>]*class="[^"]*Box-row[^"]*"[^>]*>(.*?)</article>',
            html, re.DOTALL
        )
        for block in blocks[:20]:
            name_match = re.search(
                r'<h2[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?</h2>',
                block, re.DOTALL
            )
            if not name_match:
                continue
            repo_path = name_match.group(1).strip()
            repo_name = re.sub(r'<[^>]+>', '', name_match.group(2)).strip()
            repo_name = ' '.join(repo_name.split()).replace(' / ', '/')
            if not repo_path.startswith('/'):
                repo_path = '/' + repo_path
            repo_url = f"https://github.com{repo_path}"

            desc_match = re.search(
                r'<p[^>]*class="[^"]*color-fg-muted[^"]*"[^>]*>(.*?)</p>',
                block, re.DOTALL
            )
            description = ""
            if desc_match:
                description = re.sub(r'<[^>]+>', '', desc_match.group(1)).strip()

            lang_match = re.search(
                r'<span[^>]*itemprop="programmingLanguage"[^>]*>(.*?)</span>',
                block, re.DOTALL
            )
            language = re.sub(r'<[^>]+>', '', lang_match.group(1)).strip() if lang_match else "未知"

            star_match = re.search(
                r'(\d[\d,]*)\s*stars?\s*(?:today|this\s*week)',
                block, re.IGNORECASE
            )
            stars = 0
            if star_match:
                try:
                    stars = int(star_match.group(1).replace(',', ''))
                except ValueError:
                    stars = 0

            repos.append(RepoItem(
                name=repo_name, url=repo_url, description=description,
                language=language, stars=stars
            ))

        if repos:
            log(f"[GitHub] Trending 完成，有效 {len(repos)} 个")
            return repos
    except Exception as e:
        log(f"[GitHub] Trending 页面抓取失败: {e}")

    log("[GitHub] Fallback 到 Search API")
    try:
        week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        search_url = (
            f"https://api.github.com/search/repositories"
            f"?q=pushed:>{week_ago}&sort=stars&order=desc&per_page=20"
        )
        api_headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "AI-Daily-News-Bot/1.0",
        }
        token = os.environ.get("GITHUB_TOKEN", "")
        if token:
            api_headers["Authorization"] = f"Bearer {token}"

        resp = requests.get(search_url, headers=api_headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("items", [])[:20]:
            repos.append(RepoItem(
                name=item.get("full_name", ""),
                url=item.get("html_url", ""),
                description=item.get("description") or "",
                language=item.get("language") or "未知",
                stars=item.get("stargazers_count", 0),
            ))
        log(f"[GitHub] Search API 完成，有效 {len(repos)} 个")
    except Exception as e:
        log(f"[GitHub] Search API 也失败: {e}")
    return repos


def prefilter_news(items: List[NewsItem], cfg: Dict[str, Any]) -> List[NewsItem]:
    keywords = flatten_keywords(cfg.get("keywords", {}))
    filtered: List[NewsItem] = []
    for it in items:
        text = (it.title + " " + it.summary).lower()
        matched = [k for k in keywords if k.lower() in text]
        if matched:
            log(f"[预过滤] 保留: {it.title[:40]}... | 匹配: {matched}")
            filtered.append(it)
        else:
            log(f"[预过滤] 丢弃: {it.title[:40]}...")
    log(f"[预过滤] 统计: 保留 {len(filtered)} / {len(items)} 篇")
    return filtered


NEWS_PROMPT_TEMPLATE = """你是一名企业办公效率顾问。请分析以下资讯，判断它对【办公场景】的实际价值。

【文章】
标题：{title}
摘要：{summary}

【输出要求】
请严格只输出以下JSON格式，不要任何其他文字，不要markdown代码块：

{{
  "score": 1-5,
  "core_summary": "核心内容简介（中文，通俗易懂，50字内）",
  "office_advice": "办公使用建议（怎么用、能解决什么问题，80字内）",
  "scenarios": ["场景1：...", "场景2：..."],
  "thinking": "发散思考（延伸用法、组合用法、优化方向，80字内）"
}}

评分标准（1-5分）：
5分 = 可直接改变办公方式，有明确落地路径
4分 = 办公价值高，短期可尝试
3分 = 有一定办公参考价值
2分 = 间接相关，可了解
1分 = 几乎无办公价值

注意：纯软件版本号更新、框架小版本迭代、无实际办公价值的纯技术更新，请给1分。"""


REPO_PROMPT_TEMPLATE = """你是一名企业办公效率顾问。请分析以下开源项目，评估它在【办公场景】的实用价值。

【项目】
名称：{name}
描述：{description}
开发语言：{language}
星标数：{stars}

【输出要求】
请严格只输出以下JSON格式，不要任何其他文字，不要markdown代码块：

{{
  "score": 1-5,
  "core_summary": "核心功能介绍（中文总结，50字内）",
  "office_value": "AI分析：这个工具可以用来做什么、办公场景价值（80字内）",
  "combo_usage": "组合用法：可以和哪些工具/AI/现有工作流结合使用（80字内）",
  "thinking": "发散思考：拓展用法、落地优化方向、风险提示（80字内）"
}}

评分标准（1-5分）：
5分 = 可直接用于办公提效，开箱即用
4分 = 办公价值高，配置后可使用
3分 = 有办公参考或定制价值
2分 = 偏技术，办公价值有限
1分 = 几乎无办公价值"""


def call_ai_api(prompt: str, cfg: Dict[str, Any]) -> Optional[str]:
    api_key = os.environ.get("MOONSHOT_API_KEY", "")
    if not api_key:
        log("[AI] 错误: 环境变量 MOONSHOT_API_KEY 未设置")
        return None
    model = cfg.get("filter", {}).get("model", "kimi-for-coding")
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
        log(f"[AI] 原始响应: {raw_text[:200]}")
        return raw_text
    except requests.exceptions.HTTPError as e:
        log(f"[AI] HTTP错误: {e.response.status_code} | {e.response.text[:200]}")
    except (KeyError, IndexError) as e:
        log(f"[AI] 响应结构错误: {e}")
    except Exception:
        log(f"[AI] 未知异常: {traceback.format_exc()}")
    return None


def parse_json_from_ai(raw_text: str) -> Optional[Dict[str, Any]]:
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        log(f"[AI] JSON解析错误: {e} | 原始文本: {raw_text[:200]}")
    return None


def ai_analyze_news(item: NewsItem, cfg: Dict[str, Any]) -> Optional[NewsItem]:
    prompt = NEWS_PROMPT_TEMPLATE.format(title=item.title, summary=item.summary)
    raw_text = call_ai_api(prompt, cfg)
    result = parse_json_from_ai(raw_text)
    if result is None:
        return None
    try:
        item.score = int(result.get("score", 0))
    except (ValueError, TypeError):
        item.score = 0
    item.core_summary = result.get("core_summary", "")
    item.office_advice = result.get("office_advice", "")
    raw_scenarios = result.get("scenarios", [])
    if isinstance(raw_scenarios, list):
        item.scenarios = [str(s) for s in raw_scenarios][:3]
    else:
        item.scenarios = []
    item.thinking = result.get("thinking", "")
    return item


def ai_analyze_repo(item: RepoItem, cfg: Dict[str, Any]) -> Optional[RepoItem]:
    prompt = REPO_PROMPT_TEMPLATE.format(
        name=item.name, description=item.description,
        language=item.language, stars=item.stars
    )
    raw_text = call_ai_api(prompt, cfg)
    result = parse_json_from_ai(raw_text)
    if result is None:
        return None
    try:
        item.score = int(result.get("score", 0))
    except (ValueError, TypeError):
        item.score = 0
    item.core_summary = result.get("core_summary", "")
    item.office_value = result.get("office_value", "")
    item.combo_usage = result.get("combo_usage", "")
    item.thinking = result.get("thinking", "")
    return item


def generate_html(news_items: List[NewsItem], repo_items: List[RepoItem]) -> str:
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    def esc(text: str) -> str:
        if not text:
            return ""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def score_class(score: Optional[int]) -> str:
        if score == 5:
            return "score-5"
        if score == 4:
            return "score-4"
        if score == 3:
            return "score-3"
        if score == 2:
            return "score-2"
        return "score-1"

    def score_label(score: Optional[int]) -> str:
        mapping = {5: "极高", 4: "高", 3: "中", 2: "一般", 1: "低", None: "未评"}
        return mapping.get(score, "未评")

    def render_news_card(it: NewsItem) -> str:
        scls = score_class(it.score)
        slbl = score_label(it.score)
        scenarios_html = ""
        if it.scenarios:
            scenarios_html = '<ul class="scenario-list">' + "".join(
                f'<li>{esc(s)}</li>' for s in it.scenarios
            ) + '</ul>'
        lines = [
            '    <div class="card news-card">',
            '      <div class="card-header">',
            f'        <span class="score-badge {scls}">{it.score}分 · {slbl}</span>',
            f'        <a href="{it.url}" target="_blank" class="card-title">{esc(it.title)}</a>',
            f'        <span class="source-tag">{esc(it.source)}</span>',
            '      </div>',
            '      <div class="card-body">',
            '        <div class="field">',
            '          <span class="field-label">📌 核心内容</span>',
            f'          <p>{esc(it.core_summary)}</p>',
            '        </div>',
            '        <div class="field">',
            '          <span class="field-label">💡 办公建议</span>',
            f'          <p>{esc(it.office_advice)}</p>',
            '        </div>',
            '        <div class="field">',
            '          <span class="field-label">🎯 落地场景</span>',
            f'          {scenarios_html}',
            '        </div>',
            '        <div class="field">',
            '          <span class="field-label">🔭 发散思考</span>',
            f'          <p>{esc(it.thinking)}</p>',
            '        </div>',
            '      </div>',
            '    </div>',
            '',
        ]
        return "\n".join(lines)

    def render_repo_card(it: RepoItem) -> str:
        scls = score_class(it.score)
        slbl = score_label(it.score)
        stars_str = f"{it.stars:,}" if it.stars else "0"
        lines = [
            '    <div class="card repo-card">',
            '      <div class="card-header">',
            f'        <span class="score-badge {scls}">{it.score}分 · {slbl}</span>',
            f'        <a href="{it.url}" target="_blank" class="card-title">{esc(it.name)}</a>',
            f'        <span class="repo-meta">⭐ {stars_str} | {esc(it.language)}</span>',
            '      </div>',
            '      <div class="card-body">',
            '        <div class="field">',
            '          <span class="field-label">📌 核心功能</span>',
            f'          <p>{esc(it.core_summary)}</p>',
            '        </div>',
            '        <div class="field">',
            '          <span class="field-label">💼 办公价值</span>',
            f'          <p>{esc(it.office_value)}</p>',
            '        </div>',
            '        <div class="field">',
            '          <span class="field-label">🔗 组合用法</span>',
            f'          <p>{esc(it.combo_usage)}</p>',
            '        </div>',
            '        <div class="field">',
            '          <span class="field-label">🔭 发散思考</span>',
            f'          <p>{esc(it.thinking)}</p>',
            '        </div>',
            '      </div>',
            '    </div>',
            '',
        ]
        return "\n".join(lines)

    total_news = len(news_items)
    total_repos = len(repo_items)
    high_news = len([n for n in news_items if (n.score or 0) >= 4])
    high_repos = len([r for r in repo_items if (r.score or 0) >= 4])

    html_parts = [
        '<!DOCTYPE html>',
        '<html lang="zh-CN">',
        '<head>',
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f'<title>AI办公日报 {today}</title>',
        '<style>',
        '  :root {',
        '    --bg: #f5f7fa;',
        '    --card-bg: #ffffff;',
        '    --text: #1f2937;',
        '    --text-secondary: #6b7280;',
        '    --border: #e5e7eb;',
        '    --link: #2563eb;',
        '    --news-accent: #2563eb;',
        '    --repo-accent: #059669;',
        '    --score-5: #dc2626;',
        '    --score-4: #ea580c;',
        '    --score-3: #d97706;',
        '    --score-2: #6b7280;',
        '    --score-1: #9ca3af;',
        '  }',
        '  * { box-sizing: border-box; margin: 0; padding: 0; }',
        '  body {',
        '    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,',
        '                 "Helvetica Neue", Arial, "Noto Sans SC", "PingFang SC", sans-serif;',
        '    background: var(--bg);',
        '    color: var(--text);',
        '    line-height: 1.6;',
        '    padding: 1rem;',
        '  }',
        '  .container { max-width: 1100px; margin: 0 auto; }',
        '  header { text-align: center; padding: 2.5rem 1rem 2rem; }',
        '  header h1 { font-size: 2rem; font-weight: 800; margin-bottom: 0.5rem; letter-spacing: -0.02em; }',
        '  header .subtitle { color: var(--text-secondary); font-size: 1rem; }',
        '  .stats {',
        '    display: flex; justify-content: center; gap: 1.5rem;',
        '    margin-bottom: 2.5rem; flex-wrap: wrap;',
        '  }',
        '  .stat-item {',
        '    background: var(--card-bg); padding: 1rem 1.25rem;',
        '    border-radius: 0.75rem; box-shadow: 0 1px 2px rgba(0,0,0,0.05);',
        '    text-align: center; min-width: 100px; border: 1px solid var(--border);',
        '  }',
        '  .stat-item .number { font-size: 1.5rem; font-weight: 700; color: var(--link); }',
        '  .stat-item .label { font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem; }',
        '  .module { margin-bottom: 3rem; }',
        '  .module-header {',
        '    display: flex; align-items: center; gap: 0.75rem;',
        '    margin-bottom: 1.25rem; padding-bottom: 0.75rem;',
        '    border-bottom: 2px solid var(--border);',
        '  }',
        '  .module-icon { font-size: 1.5rem; }',
        '  .module-title { font-size: 1.25rem; font-weight: 700; }',
        '  .module-subtitle { color: var(--text-secondary); font-size: 0.875rem; margin-left: auto; }',
        '  .card {',
        '    background: var(--card-bg); border-radius: 0.75rem;',
        '    margin-bottom: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.06);',
        '    border: 1px solid var(--border);',
        '    overflow: hidden;',
        '  }',
        '  .news-card { border-left: 4px solid var(--news-accent); }',
        '  .repo-card { border-left: 4px solid var(--repo-accent); }',
        '  .card-header {',
        '    display: flex; flex-wrap: wrap; align-items: center;',
        '    gap: 0.6rem; padding: 1rem 1.25rem;',
        '    background: #fafafa; border-bottom: 1px solid var(--border);',
        '  }',
        '  .score-badge {',
        '    font-size: 0.75rem; font-weight: 600; padding: 0.2rem 0.6rem;',
        '    border-radius: 999px; color: #fff; white-space: nowrap;',
        '  }',
        '  .score-5 { background: var(--score-5); }',
        '  .score-4 { background: var(--score-4); }',
        '  .score-3 { background: var(--score-3); }',
        '  .score-2 { background: var(--score-2); }',
        '  .score-1 { background: var(--score-1); }',
        '  .card-title {',
        '    font-size: 1.05rem; font-weight: 600; color: var(--link); text-decoration: none;',
        '  }',
        '  .card-title:hover { text-decoration: underline; }',
        '  .source-tag { font-size: 0.75rem; color: var(--text-secondary); margin-left: auto; }',
        '  .repo-meta { font-size: 0.8rem; color: var(--text-secondary); margin-left: auto; font-weight: 500; }',
        '  .card-body { padding: 1rem 1.25rem; }',
        '  .field { margin-bottom: 0.75rem; }',
        '  .field:last-child { margin-bottom: 0; }',
        '  .field-label {',
        '    display: inline-block; font-size: 0.8rem; font-weight: 600;',
        '    color: #374151; margin-bottom: 0.25rem;',
        '  }',
        '  .field p { font-size: 0.9rem; color: #4b5563; }',
        '  .scenario-list { margin: 0.25rem 0 0 1.25rem; font-size: 0.9rem; color: #4b5563; }',
        '  .scenario-list li { margin-bottom: 0.15rem; }',
        '  .empty-tip { text-align:center; color: var(--text-secondary); padding: 3rem 1rem; }',
        '  footer {',
        '    text-align: center; color: var(--text-secondary); font-size: 0.8rem;',
        '    padding: 2rem 1rem; border-top: 1px solid var(--border); margin-top: 1rem;',
        '  }',
        '  @media (max-width: 640px) {',
        '    header h1 { font-size: 1.5rem; }',
        '    .card-header { padding: 0.875rem 1rem; }',
        '    .card-body { padding: 0.875rem 1rem; }',
        '    .stats { gap: 1rem; }',
        '    .module-subtitle { display: none; }',
        '  }',
        '</style>',
        '</head>',
        '<body>',
        '<div class="container">',
        '  <header>',
        f'    <h1>🤖 AI办公日报 {today}</h1>',
        '    <p class="subtitle">聚焦办公场景 · 资讯精选 + 开源工具发现</p>',
        '  </header>',
        '  <div class="stats">',
        f'    <div class="stat-item"><div class="number">{total_news}</div><div class="label">办公资讯</div></div>',
        f'    <div class="stat-item"><div class="number">{total_repos}</div><div class="label">热门项目</div></div>',
        f'    <div class="stat-item"><div class="number">{high_news}</div><div class="label">高价值资讯</div></div>',
        f'    <div class="stat-item"><div class="number">{high_repos}</div><div class="label">高价值项目</div></div>',
        '  </div>',
    ]

    # 办公资讯模块
    html_parts.append('  <div class="module">')
    html_parts.append('    <div class="module-header">')
    html_parts.append('      <span class="module-icon">📰</span>')
    html_parts.append('      <span class="module-title">办公资讯精选</span>')
    html_parts.append(f'      <span class="module-subtitle">共 {total_news} 条，按办公价值排序</span>')
    html_parts.append('    </div>')
    if news_items:
        for it in news_items:
            html_parts.append(render_news_card(it))
    else:
        html_parts.append('    <div class="empty-tip">本周暂无符合条件的办公资讯</div>')
    html_parts.append('  </div>')

    # GitHub Trending 模块
    html_parts.append('  <div class="module">')
    html_parts.append('    <div class="module-header">')
    html_parts.append('      <span class="module-icon">🔥</span>')
    html_parts.append('      <span class="module-title">GitHub 本周热门项目</span>')
    html_parts.append(f'      <span class="module-subtitle">共 {total_repos} 个，按办公实用价值排序</span>')
    html_parts.append('    </div>')
    if repo_items:
        for it in repo_items:
            html_parts.append(render_repo_card(it))
    else:
        html_parts.append('    <div class="empty-tip">本周暂无热门项目数据</div>')
    html_parts.append('  </div>')

    html_parts.extend([
        '  <footer>',
        f'    <div>生成时间：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>',
        '    <div style="margin-top:0.5rem;">信源：量子位 / 机器之心 / InfoQ AI前线 / GitHub Trending</div>',
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


def generate_md(news_items: List[NewsItem], repo_items: List[RepoItem]) -> str:
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# AI办公日报 {today}",
        "",
        f"> 生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 办公资讯 {len(news_items)} 条 | 热门项目 {len(repo_items)} 个",
        "",
    ]

    lines.append("## 📰 办公资讯精选")
    lines.append("")
    if news_items:
        for it in news_items:
            lines.append(f"### [{it.score}分] {it.title}")
            lines.append(f"- **来源**：{it.source}")
            lines.append(f"- **链接**：{it.url}")
            lines.append(f"- **核心内容**：{it.core_summary}")
            lines.append(f"- **办公建议**：{it.office_advice}")
            if it.scenarios:
                lines.append(f"- **落地场景**：{' / '.join(it.scenarios)}")
            lines.append(f"- **发散思考**：{it.thinking}")
            lines.append("")
    else:
        lines.append("本周暂无符合条件的办公资讯。")
        lines.append("")

    lines.append("## 🔥 GitHub 本周热门项目")
    lines.append("")
    if repo_items:
        for it in repo_items:
            lines.append(f"### [{it.score}分] {it.name}")
            lines.append(f"- **链接**：{it.url}")
            lines.append(f"- **星标**：{it.stars:,} | **语言**：{it.language}")
            lines.append(f"- **核心功能**：{it.core_summary}")
            lines.append(f"- **办公价值**：{it.office_value}")
            lines.append(f"- **组合用法**：{it.combo_usage}")
            lines.append(f"- **发散思考**：{it.thinking}")
            lines.append("")
    else:
        lines.append("本周暂无热门项目数据。")
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
    log("AI办公日报自动采集与过滤 开始运行")
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
    max_news_ai = cfg.get("filter", {}).get("max_news_ai", 12)
    max_repo_ai = cfg.get("filter", {}).get("max_repo_ai", 10)

    # 1. 采集
    all_news: List[NewsItem] = []
    for src in cfg.get("sources", {}).get("rss", []):
        all_news.extend(fetch_rss(src["url"], src["name"]))

    all_repos: List[RepoItem] = fetch_github_trending()

    log(f"[采集] RSS {len(all_news)} 篇, Trending {len(all_repos)} 个")

    # 2. 预过滤 RSS
    filtered_news = prefilter_news(all_news, cfg)

    # 3. AI 分析 RSS
    analyzed_news: List[NewsItem] = []
    if filtered_news:
        for idx, it in enumerate(filtered_news[:max_news_ai], 1):
            log(f"[AI资讯] ({idx}/{min(len(filtered_news), max_news_ai)}) 分析: {it.title[:40]}...")
            result = ai_analyze_news(it, cfg)
            if result is None:
                log(f"[AI资讯] 失败，跳过: {it.title[:40]}...")
                continue
            log(f"[AI资讯] 评分: {result.score}")
            if result.score >= min_score:
                analyzed_news.append(result)
            else:
                log(f"[AI资讯] 丢弃（分数不足）: {it.title[:40]}...")
        log(f"[AI资讯] 保留 >= {min_score} 分: {len(analyzed_news)} 篇")
    else:
        log("[预过滤] 无匹配RSS文章")

    # 4. AI 分析 Trending
    analyzed_repos: List[RepoItem] = []
    if all_repos:
        for idx, it in enumerate(all_repos[:max_repo_ai], 1):
            log(f"[AI项目] ({idx}/{min(len(all_repos), max_repo_ai)}) 分析: {it.name[:40]}...")
            result = ai_analyze_repo(it, cfg)
            if result is None:
                log(f"[AI项目] 失败，跳过: {it.name[:40]}...")
                continue
            log(f"[AI项目] 评分: {result.score}")
            if result.score >= min_score:
                analyzed_repos.append(result)
            else:
                log(f"[AI项目] 丢弃（分数不足）: {it.name[:40]}...")
        log(f"[AI项目] 保留 >= {min_score} 分: {len(analyzed_repos)} 个")
    else:
        log("[GitHub] 无Trending数据")

    # 5. 分别按办公价值降序排序
    analyzed_news.sort(key=lambda x: x.score or 0, reverse=True)
    analyzed_repos.sort(key=lambda x: x.score or 0, reverse=True)

    # 6. 去重
    seen_news: set = set()
    deduped_news: List[NewsItem] = []
    for it in analyzed_news:
        if it.id not in seen_news:
            seen_news.add(it.id)
            deduped_news.append(it)

    seen_repos: set = set()
    deduped_repos: List[RepoItem] = []
    for it in analyzed_repos:
        if it.id not in seen_repos:
            seen_repos.add(it.id)
            deduped_repos.append(it)

    # 7. 截断（各自有上限，互不占用）
    final_news = deduped_news[:max_articles]
    final_repos = deduped_repos[:max_articles]

    log(f"[截断] 最终输出 资讯 {len(final_news)} 篇, 项目 {len(final_repos)} 个")

    generate_html(final_news, final_repos)
    generate_md(final_news, final_repos)
    cleanup_old_md()
    log("=" * 60)
    log("AI办公日报自动采集与过滤 运行完成")
    log("=" * 60)


if __name__ == "__main__":
    main()
