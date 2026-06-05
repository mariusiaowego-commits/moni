#!/usr/bin/env python3
"""
source_monitor.py - 同业信源自动抓取与增量检测脚本

为 Hermes Agent cron job 提供数据预处理：
- 抓取多个信源（36氪RSS、移动支付网、量子位、微信公众号RSS）
- 关键词过滤（银行/微信/AI 三组）
- 增量检测（对比 state.json）
- JSON 输出到 stdout
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ============================================================
# 配置
# ============================================================

# 脚本所在目录（state.json 存放在同一目录）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "state.json")

# 加载 .env（极简实现，兼容 Python 3.9+，不依赖 python-dotenv）
def _load_env(path: str) -> None:
    """Mini .env loader: KEY=VALUE per line, ignores blanks/comments/quoted values."""
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # 已存在的环境变量优先（便于 cron / 容器注入覆盖本地 .env）
            os.environ.setdefault(key, value)


_load_env(os.path.join(SCRIPT_DIR, ".env"))

# 网络请求超时（秒）
REQUEST_TIMEOUT = 10

# 请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 北京时间
CST = timezone(timedelta(hours=8))

# WeChatRSS 配置（token 来自 .env / 环境变量，绝不入库）
WECHAT_RSS_BASE_URL = "https://wechatrss.waytomaster.com/api/rss/all"
_WECHAT_RSS_TOKEN = os.environ.get("WECHAT_RSS_TOKEN", "").strip()
if not _WECHAT_RSS_TOKEN:
    sys.stderr.write(
        "ERROR: WECHAT_RSS_TOKEN 未设置。请在 .env 中填入或设置环境变量。\n"
        "申请地址: https://wechatrss.waytomaster.com/\n"
    )
    sys.exit(2)
WECHAT_RSS_URL = f"{WECHAT_RSS_BASE_URL}?token={_WECHAT_RSS_TOKEN}"

# ============================================================
# 关键词定义（三组）
# ============================================================

KEYWORDS = {
    "banking": [
        "银行", "零售银行", "获客", "活客", "财富管理", "客户经营",
        "私域", "数字化", "金融科技", "手机银行", "小程序",
        "信用卡", "贷款", "理财", "基金",
    ],
    "wechat": [
        "微信", "小程序", "公众号", "视频号", "企微",
        "企业微信", "微信生态", "微信小店",
    ],
    "ai": [
        "大模型", "AI", "智能体", "智能客服", "LLM",
        "人工智能", "GPT", "DeepSeek", "Agent",
    ],
}


# ============================================================
# 网络请求
# ============================================================

def fetch_url(url: str) -> str:
    """
    抓取URL内容，返回文本。
    自动检测编码，超时10秒。
    """
    req = Request(url, headers=HEADERS)
    try:
        resp = urlopen(req, timeout=REQUEST_TIMEOUT)
        raw = resp.read()
        # 尝试从 Content-Type 获取编码
        content_type = resp.headers.get("Content-Type", "")
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].strip().split(";")[0]
        return raw.decode(charset, errors="replace")
    except (URLError, HTTPError) as e:
        print(f"[警告] 抓取失败 {url}: {e}", file=sys.stderr)
        return ""


# ============================================================
# 信源解析器
# ============================================================

def parse_36kr_rss() -> list[dict]:
    """
    解析36氪 RSS Feed，提取文章标题、链接、日期。
    """
    url = "https://36kr.com/feed"
    xml_text = fetch_url(url)
    if not xml_text:
        return []

    articles = []
    try:
        root = ET.fromstring(xml_text)
        # RSS 2.0: root > channel > item
        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            pub_date_el = item.find("pubDate")

            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            link = link_el.text.strip() if link_el is not None and link_el.text else ""
            pub_date_str = pub_date_el.text.strip() if pub_date_el is not None and pub_date_el.text else ""

            # 解析日期: "2026-06-04 14:06:37  +0800"
            date = ""
            if pub_date_str:
                try:
                    # 去除多余空格
                    cleaned = re.sub(r'\s+', ' ', pub_date_str).strip()
                    dt = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S %z")
                    date = dt.strftime("%Y-%m-%d")
                except ValueError:
                    # 尝试 RFC 2822 格式
                    try:
                        dt = datetime.strptime(pub_date_str[:25], "%a, %d %b %Y %H:%M:%S")
                        date = dt.strftime("%Y-%m-%d")
                    except ValueError:
                        date = pub_date_str[:10]

            if title and link:
                articles.append({
                    "title": title,
                    "url": link,
                    "date": date,
                })
    except ET.ParseError as e:
        print(f"[警告] 36氪 RSS 解析失败: {e}", file=sys.stderr)

    return articles


def parse_mpaypass() -> list[dict]:
    """
    解析移动支付网首页 HTML，提取最新文章标题、链接、日期。
    """
    url = "https://mpaypass.com.cn/"
    html = fetch_url(url)
    if not html:
        return []

    articles = []
    # 文章列表在 <div class="newslist"> 块中
    # 标题和链接: <div id="title"><a href="URL" ...>TITLE</a></div>
    # 日期: <div id="time">... YYYY-M-D HH:MM</div>
    
    # 使用正则匹配每个 newslist 块
    newslist_blocks = re.findall(
        r'<div class="newslist">(.*?)</div></div></div>',
        html, re.DOTALL
    )

    for block in newslist_blocks:
        # 提取标题和链接
        title_match = re.search(
            r'<div id="title"><a\s+href="([^"]+)"[^>]*>([^<]+)</a></div>',
            block
        )
        if not title_match:
            continue

        link = title_match.group(1).strip()
        title = title_match.group(2).strip()

        # 提取日期
        date = ""
        time_match = re.search(
            r'(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}:\d{2})',
            block
        )
        if time_match:
            y, m, d, _ = time_match.groups()
            date = f"{y}-{int(m):02d}-{int(d):02d}"

        if title and link:
            articles.append({
                "title": title,
                "url": link,
                "date": date,
            })

    return articles


def parse_qbitai() -> list[dict]:
    """
    解析量子位首页 HTML，提取最新文章标题、链接、日期。
    """
    url = "https://www.qbitai.com/"
    html = fetch_url(url)
    if not html:
        return []

    articles = []
    
    # 方法1: 解析文章列表（picture_text 块）
    # <div class="picture_text">...<h4><a href="URL">TITLE</a></h4>...<span class="time">TIME</span>
    article_blocks = re.findall(
        r'<div class="picture_text">(.*?)</div>\s*</div>\s*</div>',
        html, re.DOTALL
    )

    for block in article_blocks:
        # 提取标题和链接
        title_match = re.search(
            r'<h4><a\s+href="([^"]+)"[^>]*>([^<]+)</a></h4>',
            block
        )
        if not title_match:
            continue

        link = title_match.group(1).strip()
        title = title_match.group(2).strip()

        # 提取时间（相对时间如"刚刚"、"1小时前"、"5小时前"）
        date = ""
        time_match = re.search(r'<span class="time">(.*?)</span>', block)
        if time_match:
            time_text = time_match.group(1).strip()
            today = datetime.now(CST).strftime("%Y-%m-%d")
            # 将相对时间转换为日期
            if "刚刚" in time_text or "分钟前" in time_text or "小时前" in time_text:
                date = today
            elif "天前" in time_text:
                try:
                    days_ago = int(re.search(r'(\d+)', time_text).group(1))
                    dt = datetime.now(CST) - timedelta(days=days_ago)
                    date = dt.strftime("%Y-%m-%d")
                except (ValueError, AttributeError):
                    date = today
            else:
                date = today

        if title and link:
            articles.append({
                "title": title,
                "url": link,
                "date": date,
            })

    # 方法2: 解析轮播区文章（swiper-slide）
    carousel_matches = re.findall(
        r'<div class="swiper-slide"><a\s+href="([^"]+)"[^>]*>.*?<h3>([^<]+)</h3>',
        html, re.DOTALL
    )
    today = datetime.now(CST).strftime("%Y-%m-%d")
    for link, title in carousel_matches:
        link = link.strip()
        title = title.strip()
        if title and link:
            articles.append({
                "title": title,
                "url": link,
                "date": today,
            })

    # 方法3: 解析焦点区大图文章（pic_box）
    picbox_match = re.findall(
        r'<div class="pic_box[^"]*">\s*<a\s+href="([^"]+)"[^>]*>.*?<h4><span>([^<]+)</span></h4>',
        html, re.DOTALL
    )
    for link, title in picbox_match:
        link = link.strip()
        title = title.strip()
        if title and link:
            articles.append({
                "title": title,
                "url": link,
                "date": today,
            })

    return articles


def parse_wechat_rss() -> list[dict]:
    """
    解析 WeChatRSS 聚合 RSS Feed，提取公众号文章。
    标题格式：[公众号名] 文章标题
    """
    xml_text = fetch_url(WECHAT_RSS_URL)
    if not xml_text:
        return []

    articles = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.findall(".//item"):
            title_el = item.find("title")
            link_el = item.find("link")
            pub_date_el = item.find("pubDate")

            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            link = link_el.text.strip() if link_el is not None and link_el.text else ""
            pub_date_str = pub_date_el.text.strip() if pub_date_el is not None and pub_date_el.text else ""

            # 解析日期: "Thu, 04 Jun 2026 02:00:00 +0000"
            date = ""
            if pub_date_str:
                try:
                    dt = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %z")
                    date = dt.strftime("%Y-%m-%d")
                except ValueError:
                    date = pub_date_str[:10]

            if title and link:
                articles.append({
                    "title": title,
                    "url": link,
                    "date": date,
                })
    except ET.ParseError as e:
        print(f"[警告] WeChatRSS 解析失败: {e}", file=sys.stderr)

    return articles


# ============================================================
# 关键词过滤
# ============================================================

def match_keywords(title: str) -> dict:
    """
    对文章标题进行关键词匹配。
    返回: {"matched": [命中的关键词列表], "categories": [命中的分类集合], "is_cross": bool}
    """
    matched_keywords = []
    matched_categories = set()

    for category, keywords in KEYWORDS.items():
        for kw in keywords:
            # 大小写不敏感匹配英文关键词
            if kw.isascii():
                if re.search(re.escape(kw), title, re.IGNORECASE):
                    matched_keywords.append(kw)
                    matched_categories.add(category)
            else:
                if kw in title:
                    matched_keywords.append(kw)
                    matched_categories.add(category)

    # 去重关键词
    matched_keywords = list(dict.fromkeys(matched_keywords))

    return {
        "matched": matched_keywords,
        "categories": matched_categories,
        "is_cross": len(matched_categories) >= 2,
    }


def filter_articles(articles: list[dict]) -> list[dict]:
    """
    过滤文章：保留命中任意关键词的文章。
    交叉命中（>=2个分类）的文章优先排列。
    """
    filtered = []
    for article in articles:
        result = match_keywords(article["title"])
        if result["matched"]:
            article["keywords_matched"] = result["matched"]
            article["_categories"] = result["categories"]
            article["_is_cross"] = result["is_cross"]
            filtered.append(article)

    # 交叉命中的排在前面
    filtered.sort(key=lambda a: (not a["_is_cross"], a["title"]))
    return filtered


# ============================================================
# 增量检测
# ============================================================

def load_state() -> dict:
    """加载上次的状态文件"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[警告] 读取 state.json 失败: {e}", file=sys.stderr)
    return {"seen_urls": [], "last_run": ""}


def save_state(state: dict):
    """保存当前状态"""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"[警告] 保存 state.json 失败: {e}", file=sys.stderr)


def mark_new_articles(articles: list[dict], seen_urls: set) -> list[dict]:
    """
    标记新文章：URL 不在 seen_urls 中的为新文章。
    """
    for article in articles:
        article["is_new"] = article["url"] not in seen_urls
    return articles


# ============================================================
# 主逻辑
# ============================================================

def build_output(sources_data: list[dict]) -> dict:
    """构建最终 JSON 输出"""
    now = datetime.now(CST)
    run_time = now.isoformat()

    total_articles = 0
    filtered_articles = 0
    new_articles = 0

    summary = {"banking": 0, "wechat": 0, "ai": 0, "cross": 0}

    sources_output = []
    for source in sources_data:
        source_total = source["total"]
        source_articles = source["articles"]
        source_filtered = len(source_articles)
        source_new = sum(1 for a in source_articles if a.get("is_new", False))

        total_articles += source_total
        filtered_articles += source_filtered
        new_articles += source_new

        # 统计分类
        for article in source_articles:
            cats = article.get("_categories", set())
            if "banking" in cats:
                summary["banking"] += 1
            if "wechat" in cats:
                summary["wechat"] += 1
            if "ai" in cats:
                summary["ai"] += 1
            if article.get("_is_cross", False):
                summary["cross"] += 1

        # 清理内部字段，构建输出文章列表
        clean_articles = []
        for article in source_articles:
            clean_articles.append({
                "title": article["title"],
                "url": article["url"],
                "date": article.get("date", ""),
                "keywords_matched": article.get("keywords_matched", []),
                "is_new": article.get("is_new", False),
            })

        sources_output.append({
            "name": source["name"],
            "url": source["url"],
            "total": source_total,
            "filtered": source_filtered,
            "new": source_new,
            "articles": clean_articles,
        })

    return {
        "run_time": run_time,
        "total_articles": total_articles,
        "filtered_articles": filtered_articles,
        "new_articles": new_articles,
        "sources": sources_output,
        "summary": summary,
    }


def main():
    """主入口"""
    # 加载上次状态
    state = load_state()
    seen_urls = set(state.get("seen_urls", []))

    # 定义信源
    sources = [
        {
            "name": "36氪",
            "url": "https://36kr.com/feed",
            "parser": parse_36kr_rss,
        },
        {
            "name": "移动支付网",
            "url": "https://mpaypass.com.cn/",
            "parser": parse_mpaypass,
        },
        {
            "name": "量子位",
            "url": "https://www.qbitai.com/",
            "parser": parse_qbitai,
        },
        {
            "name": "微信公众号",
            "url": WECHAT_RSS_URL,
            "parser": parse_wechat_rss,
        },
    ]

    # 抓取并处理每个信源
    sources_data = []
    all_new_urls = []

    for source in sources:
        print(f"[信息] 正在抓取: {source['name']}...", file=sys.stderr)
        try:
            raw_articles = source["parser"]()
        except Exception as e:
            print(f"[错误] {source['name']} 抓取异常: {e}", file=sys.stderr)
            raw_articles = []

        total = len(raw_articles)
        print(f"[信息] {source['name']} 获取到 {total} 篇文章", file=sys.stderr)

        # 关键词过滤
        filtered = filter_articles(raw_articles)

        # 增量检测
        filtered = mark_new_articles(filtered, seen_urls)

        # 收集新 URL
        for article in filtered:
            if article.get("is_new", False):
                all_new_urls.append(article["url"])

        sources_data.append({
            "name": source["name"],
            "url": source["url"],
            "total": total,
            "articles": filtered,
        })

    # 构建输出
    output = build_output(sources_data)

    # 更新状态文件
    # 合并旧的和新的 URL（保留最近的，避免无限增长）
    updated_urls = list(seen_urls | set(all_new_urls))
    # 限制状态文件大小，最多保留 2000 条 URL
    if len(updated_urls) > 2000:
        updated_urls = updated_urls[-2000:]

    save_state({
        "seen_urls": updated_urls,
        "last_run": datetime.now(CST).isoformat(),
    })

    # 输出 JSON 到 stdout
    print(json.dumps(output, ensure_ascii=False, indent=2))

    print(f"[信息] 完成: 共 {output['total_articles']} 篇, "
          f"过滤后 {output['filtered_articles']} 篇, "
          f"新增 {output['new_articles']} 篇", file=sys.stderr)


if __name__ == "__main__":
    main()
