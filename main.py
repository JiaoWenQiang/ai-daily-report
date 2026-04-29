#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI日报自动采集与过滤
功能：RSS抓取 + GitHub Release采集 + AI语义过滤 + HTML/Markdown生成
"""

import os
import sys
import re
import json
import hashlib
import datetime
import glob
from typing import List, Dict, Any, Optional

import yaml
import requests
import feedparser


# ============================================================
# 辅助函数
# ============================================================

def now_str() -> str:
    """返回当前时间字符串"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str):
    """带时间戳的打印"""
    print(f"[{now_str()}] {msg}", flush=True)


def md5_id(url: str) -> str:
    """基于URL生成8位MD5哈希ID"""
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:8]


def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    """加载配置文件"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def flatten_keywords(keywords_dict: Dict[str, List[str]]) -> List[str]:
    """将分类关键词展平为列表"""
    result = []
    for lst in keywords_dict.values():
        result.extend(lst)
    return result


# ============================================================
# A. 采集层
# ============================================================

class Article:
    """统一文章对象"""
    def __init__(self, title: str, url: str, source: str,
                 published: datetime.datetime, summary: str = ""):
        self.title = title
        self.url = url
        self.source = source
        self.published = published
        self.summary = summary
        self.id = md5_id(url)
        # AI过滤后填充
        self.score: Optional[int] = None
        self.tags: List[str] = []
        self.ai_summary: str = ""
        self.action: str = ""
        self.talk: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published": self.published.isoformat(),
            "summary": self.summary,
            "score": self.score,
            "tags": self.tags,
            "ai_summary": self.ai_summary,
            "action": self.action,
            "talk": self.talk,
        }


def fetch_rss_sources(sources: List[Dict[str, str]], cutoff: datetime.datetime) -> List[Article]:
    """抓取RSS源，返回48小时内的文章"""
    articles: List[Article] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    for src in sources:
        name = src["name"]
        url = src["url"]
        log(f"[RSS] 开始抓取: {name} -> {url}")
        try:
            parsed = feedparser.parse(url, request_headers=headers)
            if parsed.bozo and hasattr(parsed, 'bozo_exception'):
                log(f"[RSS] 警告 {name}: {parsed.bozo_exception}")

            for entry in parsed.entries:
                # 解析时间
                published = None
                for field in ("published_parsed", "updated_parsed", "created_parsed"):
                    if hasattr(entry, field) and getattr(entry, field):
                        t = getattr(entry, field)
                        published = datetime.datetime(*t[:6])
                        break

                if published is None:
                    # 尝试解析字符串时间
                    for field in ("published", "updated", "created"):
                        if hasattr(entry, field):
                            try:
                                published = datetime.datetime.strptime(
                                    getattr(entry, field), "%a, %d %b %Y %H:%M:%S %z"
                                )
                                break
                            except Exception:
                                pass

                if published is None:
                    published = datetime.datetime.now()

                # 只保留48小时内的
                if published < cutoff:
                    continue

                title = entry.get("title", "无标题")
                link = entry.get("link", "")
                # 摘要：优先description，其次summary
                summary = entry.get("summary", "") or entry.get("description", "")
                # 去除HTML标签的简单处理
                summary = re.sub(r"<[^>]+>", "", summary)
                summary = summary.strip()[:500]

                if not link:
                    continue

                articles.append(Article(
                    title=title,
                    url=link,
                    source=name,
                    published=published,
                    summary=summary,
                ))
            log(f"[RSS] {name} 抓取完成，有效文章数: {len([a for a in articles if a.source == name])}")
        except Exception as e:
            log(f"[RSS] 错误 {name}: {e}")

    return articles


def fetch_github_releases(sources: List[Dict[str, str]], cutoff: datetime.datetime) -> List[Article]:
    """抓取GitHub Release API，返回最近5条且48小时内的Release"""
    articles: List[Article] = []
    headers = {
        "User-Agent": "AI-Daily-News-Bot/1.0",
        "Accept": "application/vnd.github+json",
    }

    for src in sources:
        name = src["name"]
        url = src["url"]
        log(f"[GitHub] 开始抓取: {name} -> {url}")
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            releases = resp.json()

            if not isinstance(releases, list):
                log(f"[GitHub] 警告 {name}: 返回非列表数据")
                continue

            count = 0
            for rel in releases[:5]:
                published_str = rel.get("published_at", "")
                if not published_str:
                    continue
                try:
                    published = datetime.datetime.strptime(
                        published_str.replace("Z", "+00:00"), "%Y-%m-%dT%H:%M:%S%z"
                    )
                    # 转为naive用于比较（cutoff也是naive）
                    published = published.replace(tzinfo=None)
                except Exception:
                    continue

                if published < cutoff:
                    continue

                tag = rel.get("tag_name", "未知版本")
                title = f"[{name}] Release {tag}"
                link = rel.get("html_url", "")
                body = rel.get("body") or ""
                # Markdown转纯文本的简单处理
                body = re.sub(r"!\[.*?\]\(.*?\)", "", body)  # 去掉图片
                body = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", body)  # 去掉链接
                body = body.replace("*", "").replace("#", "").replace("`", "")
                summary = body.strip()[:500]

                if not link:
                    continue

                articles.append(Article(
                    title=title,
                    url=link,
                    source=f"GitHub-{name}",
                    published=published,
                    summary=summary,
                ))
                count += 1

            log(f"[GitHub] {name} 抓取完成，有效Release数: {count}")
        except Exception as e:
            log(f"[GitHub] 错误 {name}: {e}")

    return articles


# ============================================================
# B. 预过滤层
# ============================================================

def prefilter_articles(articles: List[Article], keywords: List[str]) -> List[Article]:
    """基于关键词预过滤，只保留匹配任意关键词的文章"""
    filtered: List[Article] = []
    keyword_set = [k.lower() for k in keywords]

    for art in articles:
        text = (art.title + " " + art.summary).lower()
        matched = any(k in text for k in keyword_set)
        if matched:
            filtered.append(art)
        else:
            log(f"[预过滤] 丢弃: {art.title[:60]}...")

    log(f"[预过滤] 保留 {len(filtered)} / {len(articles)} 篇")
    return filtered


# ============================================================
# C. AI过滤层
# ============================================================

VALID_TAGS = [
    "AIOps-日志", "AIOps-告警", "AIOps-变更", "AIOps-容量",
    "Agent-运维", "RAG-运维知识库", "算力-昇腾", "算力-国产",
    "办公-文档", "办公-PPT", "办公-会议", "办公-邮件",
    "办公-Excel", "办公-RPA", "办公-数字员工",
    "模型-国产", "模型-开源", "工程-部署", "安全-幻觉"
]

SYSTEM_PROMPT = f"""你是一名资深AI技术编辑，负责从运维和办公双场景角度评估技术资讯的价值。

请对用户提供的新闻标题和摘要进行评估，返回严格JSON格式：
{{
  "score": 0-5,
  "tags": ["标签1", "标签2"],
  "summary": "一句话技术总结，工程师语言，不浮夸",
  "action": "如果score>=3，给出2周内可验证的具体动作",
  "talk": "如果score>=4，给出30秒向技术型领导汇报的话术，包含技术价值和业务价值"
}}

评分标准：
- 5分：对运维/办公场景有颠覆性意义，可直接落地
- 4分：有明确技术价值，值得深入评估
- 3分：值得关注，有相关场景的同学可以看看
- 0-2分：关联度低或价值有限

tags必须从以下枚举中选择（最多3个）：{VALID_TAGS}
"""


def call_kimi_api(article: Article, api_key: str, model: str) -> Optional[Dict[str, Any]]:
    """调用Kimi API对单篇文章进行评分"""
    url = "https://api.moonshot.cn/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    user_content = f"标题：{article.title}\n来源：{article.source}\n摘要：{article.summary}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)
        return result
    except requests.exceptions.RequestException as e:
        log(f"[Kimi API] 请求异常: {e}")
    except (KeyError, json.JSONDecodeError) as e:
        log(f"[Kimi API] 解析异常: {e}")
    except Exception as e:
        log(f"[Kimi API] 未知异常: {e}")
    return None


def ai_filter_articles(articles: List[Article], api_key: str, model: str,
                       min_score: int, max_articles: int) -> List[Article]:
    """对预过滤后的文章进行AI评分，返回符合分数要求的文章"""
    scored: List[Article] = []

    for idx, art in enumerate(articles, 1):
        log(f"[AI过滤] ({idx}/{len(articles)}) 处理: {art.title[:60]}...")
        result = call_kimi_api(art, api_key, model)
        if result is None:
            log(f"[AI过滤] 跳过（API失败）: {art.title[:60]}...")
            continue

        try:
            art.score = int(result.get("score", 0))
        except (ValueError, TypeError):
            art.score = 0

        raw_tags = result.get("tags", [])
        # 只保留合法标签
        art.tags = [t for t in raw_tags if t in VALID_TAGS][:3]
        art.ai_summary = result.get("summary", "")
        art.action = result.get("action", "")
        art.talk = result.get("talk", "")

        log(f"[AI过滤] 评分: {art.score} | 标签: {art.tags}")

        if art.score >= min_score:
            scored.append(art)
        else:
            log(f"[AI过滤] 丢弃（分数不足）: {art.title[:60]}...")

        if len(scored) >= max_articles:
            log(f"[AI过滤] 已达到每日上限 {max_articles}，停止处理")
            break

    # 按分数降序排列
    scored.sort(key=lambda a: (a.score or 0), reverse=True)
    log(f"[AI过滤] 最终保留 {len(scored)} 篇")
    return scored


# ============================================================
# D. 输出层
# ============================================================

def generate_html(articles: List[Article], output_dir: str = "docs") -> str:
    """生成静态HTML日报"""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    high_priority = [a for a in articles if (a.score or 0) >= 4]
    normal = [a for a in articles if (a.score or 0) == 3]

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI日报 {today} | 运维+办公双场景</title>
<style>
  :root {{
    --bg: #f8f9fa;
    --card-bg: #ffffff;
    --text: #212529;
    --text-secondary: #6c757d;
    --border-blue: #0d6efd;
    --border-gray: #adb5bd;
    --score-5: #dc3545;
    --score-4: #fd7e14;
    --badge-bg: #e9ecef;
    --talk-bg: #fff3cd;
    --talk-border: #ffc107;
    --link: #0d6efd;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, "Noto Sans SC", sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    padding: 1rem;
  }}
  .container {{
    max-width: 960px;
    margin: 0 auto;
  }}
  header {{
    text-align: center;
    padding: 2rem 1rem;
    margin-bottom: 1.5rem;
  }}
  header h1 {{
    font-size: 1.75rem;
    font-weight: 700;
    margin-bottom: 0.5rem;
  }}
  header .subtitle {{
    color: var(--text-secondary);
    font-size: 0.95rem;
  }}
  .stats {{
    display: flex;
    justify-content: center;
    gap: 2rem;
    margin-bottom: 2rem;
    flex-wrap: wrap;
  }}
  .stat-item {{
    background: var(--card-bg);
    padding: 1rem 1.5rem;
    border-radius: 0.5rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    text-align: center;
    min-width: 120px;
  }}
  .stat-item .number {{
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--border-blue);
  }}
  .stat-item .label {{
    font-size: 0.8rem;
    color: var(--text-secondary);
    margin-top: 0.25rem;
  }}
  .section {{
    margin-bottom: 2rem;
  }}
  .section-title {{
    font-size: 1.1rem;
    font-weight: 600;
    margin-bottom: 1rem;
    padding-left: 0.75rem;
    border-left: 4px solid var(--border-blue);
  }}
  .section-title.normal {{
    border-left-color: var(--border-gray);
  }}
  .article {{
    background: var(--card-bg);
    border-radius: 0.5rem;
    padding: 1.25rem;
    margin-bottom: 1rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    border-left: 4px solid var(--border-blue);
  }}
  .article.normal {{
    border-left-color: var(--border-gray);
  }}
  .article-header {{
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
  }}
  .score {{
    font-weight: 700;
    font-size: 1.1rem;
  }}
  .score-5 {{ color: var(--score-5); }}
  .score-4 {{ color: var(--score-4); }}
  .article-title {{
    font-size: 1.05rem;
    font-weight: 600;
    color: var(--link);
    text-decoration: none;
  }}
  .article-title:hover {{
    text-decoration: underline;
  }}
  .meta {{
    font-size: 0.8rem;
    color: var(--text-secondary);
    margin-bottom: 0.5rem;
  }}
  .badges {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    margin-bottom: 0.5rem;
  }}
  .badge {{
    background: var(--badge-bg);
    color: var(--text-secondary);
    font-size: 0.75rem;
    padding: 0.15rem 0.5rem;
    border-radius: 0.25rem;
  }}
  .summary {{
    font-size: 0.9rem;
    margin-bottom: 0.5rem;
  }}
  .action {{
    font-size: 0.85rem;
    color: var(--text-secondary);
    margin-bottom: 0.5rem;
  }}
  .action strong {{
    color: var(--text);
  }}
  .talk {{
    background: var(--talk-bg);
    border: 1px solid var(--talk-border);
    border-radius: 0.35rem;
    padding: 0.6rem 0.8rem;
    font-size: 0.85rem;
    color: #664d03;
  }}
  .talk strong {{
    display: block;
    margin-bottom: 0.2rem;
    color: #856404;
  }}
  footer {{
    text-align: center;
    color: var(--text-secondary);
    font-size: 0.8rem;
    padding: 2rem 1rem;
    border-top: 1px solid #dee2e6;
    margin-top: 2rem;
  }}
  footer .sources {{
    margin-top: 0.5rem;
    line-height: 1.8;
  }}
  @media (max-width: 640px) {{
    header h1 {{ font-size: 1.4rem; }}
    .article {{ padding: 1rem; }}
    .stats {{ gap: 1rem; }}
  }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>🤖 AI日报 {today}</h1>
    <p class="subtitle">运维 + 办公双场景技术资讯过滤</p>
  </header>

  <div class="stats">
    <div class="stat-item">
      <div class="number">{len(articles)}</div>
      <div class="label">共筛选</div>
    </div>
    <div class="stat-item">
      <div class="number">{len(high_priority)}</div>
      <div class="label">高价值</div>
    </div>
  </div>
"""

    # 高优先级区块
    if high_priority:
        html += '  <div class="section">\n'
        html += '    <div class="section-title">🔥 高优先级（4-5分）</div>\n'
        for art in high_priority:
            html += render_article_html(art, is_high=True)
        html += '  </div>\n'

    # 值得关注区块
    if normal:
        html += '  <div class="section">\n'
        html += '    <div class="section-title normal">📌 值得关注（3分）</div>\n'
        for art in normal:
            html += render_article_html(art, is_high=False)
        html += '  </div>\n'

    # 无数据提示
    if not articles:
        html += '  <div class="section" style="text-align:center;color:var(--text-secondary);padding:3rem 1rem;">今日无符合条件的资讯</div>\n'

    sources_list = [
        "量子位 RSS", "机器之心 RSS", "InfoQ AI前线 RSS",
        "DeepSeek GitHub Releases", "通义千问 GitHub Releases",
        "Kimi GitHub Releases", "智谱AI GitHub Releases"
    ]

    html += f"""  <footer>
    <div>生成时间: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
    <div class="sources">信源: {' / '.join(sources_list)}</div>
  </footer>
</div>
</body>
</html>
"""

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"[HTML] 已生成: {path}")
    return path


def render_article_html(art: Article, is_high: bool) -> str:
    """渲染单篇文章HTML"""
    score_cls = ""
    if art.score == 5:
        score_cls = "score-5"
    elif art.score == 4:
        score_cls = "score-4"

    cls = "article" if is_high else "article normal"
    tags_html = "".join(f'<span class="badge">{t}</span>' for t in art.tags)
    action_html = f'<div class="action"><strong>落地建议:</strong> {escape_html(art.action)}</div>' if art.action else ""
    talk_html = (
        f'<div class="talk"><strong>💡 领导话术</strong>{escape_html(art.talk)}</div>'
        if art.talk else ""
    )

    return f"""    <div class="{cls}">
      <div class="article-header">
        <span class="score {score_cls}">[{art.score}分]</span>
        <a href="{art.url}" target="_blank" class="article-title">{escape_html(art.title)}</a>
      </div>
      <div class="meta">来源: {art.source} | ID: {art.id}</div>
      <div class="badges">{tags_html}</div>
      <div class="summary">{escape_html(art.ai_summary)}</div>
      {action_html}
      {talk_html}
    </div>
"""


def escape_html(text: str) -> str:
    """简单的HTML转义"""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def generate_markdown(articles: List[Article], output_dir: str = "docs") -> str:
    """生成Markdown归档，仅包含高优先级文章"""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    high_priority = [a for a in articles if (a.score or 0) >= 4]

    lines = [
        f"# AI日报 {today} — 高优先级归档",
        "",
        f"> 生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 共筛选 {len(articles)} 条，高价值 {len(high_priority)} 条",
        "",
    ]

    for art in high_priority:
        lines.append(f"## [{art.score}分] {art.title}")
        lines.append(f"- **来源**: {art.source}")
        lines.append(f"- **链接**: {art.url}")
        lines.append(f"- **标签**: {', '.join(art.tags)}")
        lines.append(f"- **总结**: {art.ai_summary}")
        if art.action:
            lines.append(f"- **落地建议**: {art.action}")
        if art.talk:
            lines.append(f"- **领导话术**: {art.talk}")
        lines.append("")

    if not high_priority:
        lines.append("今日无高优先级资讯。")
        lines.append("")

    content = "\n".join(lines)
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{today}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    log(f"[Markdown] 已生成: {path}")
    return path


def cleanup_old_markdown(output_dir: str = "docs", keep_days: int = 30):
    """保留最近N天的Markdown文件，删除旧文件"""
    cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
    pattern = os.path.join(output_dir, "*.md")
    removed = 0

    for path in glob.glob(pattern):
        basename = os.path.basename(path)
        # 尝试解析文件名中的日期
        try:
            date_str = basename.replace(".md", "")
            file_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            if file_date < cutoff:
                os.remove(path)
                removed += 1
                log(f"[清理] 删除旧文件: {basename}")
        except ValueError:
            pass  # 非日期命名的md文件跳过

    log(f"[清理] 共删除 {removed} 个旧Markdown文件")


# ============================================================
# 主流程
# ============================================================

def main():
    log("=" * 50)
    log("AI日报自动采集与过滤 开始运行")
    log("=" * 50)

    # 加载配置
    try:
        config = load_config()
    except Exception as e:
        log(f"[致命错误] 加载config.yaml失败: {e}")
        sys.exit(1)

    # 读取API Key
    api_key = os.environ.get("MOONSHOT_API_KEY", "")
    if not api_key:
        log("[致命错误] 环境变量 MOONSHOT_API_KEY 未设置")
        sys.exit(1)

    model = config.get("filter", {}).get("model", "moonshot-v1-8k")
    min_score = config.get("filter", {}).get("min_score", 3)
    max_articles = config.get("filter", {}).get("max_articles_per_day", 15)
    keywords = flatten_keywords(config.get("keywords", {}))
    output_dir = "docs"

    # 计算48小时 cutoff
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(hours=48)

    # A. 采集层
    all_articles: List[Article] = []

    rss_sources = config.get("sources", {}).get("rss", [])
    if rss_sources:
        articles = fetch_rss_sources(rss_sources, cutoff)
        all_articles.extend(articles)

    gh_sources = config.get("sources", {}).get("github_releases", [])
    if gh_sources:
        articles = fetch_github_releases(gh_sources, cutoff)
        all_articles.extend(articles)

    log(f"[采集] 总计获取 {len(all_articles)} 篇文章/Release")

    if not all_articles:
        log("[采集] 无数据，生成空日报")
        generate_html([], output_dir)
        generate_markdown([], output_dir)
        cleanup_old_markdown(output_dir)
        log("运行结束")
        return

    # B. 预过滤层
    filtered = prefilter_articles(all_articles, keywords)

    if not filtered:
        log("[预过滤] 无匹配文章，生成空日报")
        generate_html([], output_dir)
        generate_markdown([], output_dir)
        cleanup_old_markdown(output_dir)
        log("运行结束")
        return

    # C. AI过滤层
    scored = ai_filter_articles(filtered, api_key, model, min_score, max_articles)

    # D. 输出层
    generate_html(scored, output_dir)
    generate_markdown(scored, output_dir)
    cleanup_old_markdown(output_dir)

    log("=" * 50)
    log("AI日报自动采集与过滤 运行完成")
    log("=" * 50)


if __name__ == "__main__":
    main()
