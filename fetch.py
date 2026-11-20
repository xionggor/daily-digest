#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch.py — 抓取 RSS 源，生成 data.json 与 index.html（数据内联）。

用法：
    python fetch.py                 # 使用 sources.json 中的默认配置
    python fetch.py --days 3        # 只保留最近 3 天
    python fetch.py --total 60      # 最多保留 60 条

只用标准库，无需 pip install，可直接在 GitHub Actions 中运行。
"""

import argparse
import gzip
import html as html_lib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.json"
TEMPLATE_FILE = ROOT / "template.html"
OUT_HTML = ROOT / "index.html"
OUT_JSON = ROOT / "data.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

TAG_RE = re.compile(r"<[^>]+>")
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
WS_RE = re.compile(r"\s+")
TAIL_RE = re.compile(
    r"\s*(Continue reading[^.]*\.?|Read more[^.]*\.?|The post .+? appeared first on .+?\.?|"
    r"Cet article .+?\.?|阅读全文[^。]*。?)\s*$",
    re.IGNORECASE,
)
TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "igshid",
}
NOISE_RE = re.compile(
    r"(Article URL|Comments URL|Discussion URL|Points|#\s*Comments)\s*:\s*\S+",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- 文本处理

def clean_text(value, limit=200, strip_tail=True):
    """去掉 HTML 标签与多余空白，并截断到 limit 个字符。"""
    if value is None:
        return ""
    text = html_lib.unescape(str(value))
    text = TAG_RE.sub(" ", text).replace("\u00a0", " ").replace("\u200b", "")
    text = NOISE_RE.sub(" ", text)
    text = WS_RE.sub(" ", text).strip()
    if strip_tail:
        text = TAIL_RE.sub("", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip(" ,.;，。、") + "…"
    return text


def normalize_url(url):
    """去掉跟踪参数，避免同一条内容因链接不同而重复。"""
    if not url:
        return ""
    url = url.strip()
    if "?" in url:
        base, _, query = url.partition("?")
        kept = [
            p for p in query.split("&")
            if p and p.split("=")[0] not in TRACKING_KEYS
        ]
        url = base + ("?" + "&".join(kept) if kept else "")
    return url.split("#")[0].rstrip("/")


def parse_date(value):
    if not value:
        return None
    raw = str(value).strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        iso = raw.replace("Z", "+00:00").replace("z", "+00:00")
        dt = datetime.fromisoformat(iso)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


# ---------------------------------------------------------------- 网络请求

def http_get(url, insecure=False, headers=None):
    context = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    req_headers = {
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=25, context=context) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
        return body


# ---------------------------------------------------------------- Feed 解析

def local_name(tag):
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def child_text(node, names):
    """按标签局部名，在直接子节点里找第一个文本。"""
    for child in list(node):
        if local_name(child.tag) in names:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def child_attr(node, names, attr):
    for child in list(node):
        if local_name(child.tag) in names and child.get(attr):
            return child.get(attr)
    return ""


def parse_feed(body, source):
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        # 清理非法控制字符后重试一次
        cleaned = CTRL_RE.sub("", "".join(
            ch for ch in body.decode("utf-8", "replace")
            if ch in "\t\n\r" or ord(ch) >= 32
        ))
        root = ET.fromstring(cleaned)

    entries = root.findall(".//item")
    is_atom = False
    if not entries:
        entries = [e for e in root.iter() if local_name(e.tag) == "entry"]
        is_atom = bool(entries)
    if not entries:
        tags = sorted({local_name(e.tag) for e in root.iter()} - {"channel", "feed", "rss", "rdf"})
        raise ValueError("no <item>/<entry> found, tags=%s" % ",".join(tags[:6]))

    items = []
    for entry in entries:
        title = child_text(entry, {"title"})
        if is_atom:
            link = ""
            for child in list(entry):
                if local_name(child.tag) == "link":
                    rel = (child.get("rel") or "alternate").lower()
                    if rel == "alternate" or not link:
                        link = child.get("href") or ""
        else:
            link = child_text(entry, {"link"}) or child_attr(entry, {"link"}, "href")
            if not link:
                guid = child_text(entry, {"guid", "id"})
                if guid.startswith("http"):
                    link = guid

        summary = child_text(
            entry,
            {"description", "summary", "encoded", "content", "abstract"},
        )
        published = parse_date(
            child_text(entry, {"pubdate", "published", "updated", "date", "issued", "created"})
        )

        title = clean_text(title, limit=0)
        # arXiv 的标题形如 "[2401.12345] 论文题目"
        title = re.sub(r"^\[[^\]]{3,40}\]\s*", "", title)
        if not title or not link:
            continue

        items.append({
            "title": title,
            "url": link.strip(),
            "source": source["name"],
            "category": source["category"],
            "summary": clean_text(summary, limit=180),
            "published": published.isoformat() if published else "",
        })
    return items


def fetch_source(source):
    """返回 (source, items, error)，遇限流时退避重试。"""
    last_error = None
    for attempt in range(3):
        try:
            body = http_get(source["url"], insecure=bool(source.get("insecure")))
            items = parse_feed(body, source)
            return source, items, None
        except Exception as exc:
            last_error = exc
            code = getattr(exc, "code", None)
            retryable = code in (419, 429, 500, 502, 503, 504) or isinstance(exc, TimeoutError)
            if retryable and attempt < 2:
                time.sleep(5 if attempt == 0 else 12)
                continue
            break
    return source, [], "%s: %s" % (type(last_error).__name__, str(last_error)[:120])


# ---------------------------------------------------------------- GitHub 精选

def fetch_github(cfg):
    """调用 GitHub Search API 取近期新晋热门仓库。无需 key，有 token 更好。"""
    if not cfg or cfg.get("enabled") is False:
        return []

    days = int(cfg.get("days", 14))
    min_stars = int(cfg.get("min_stars", 60))
    limit = int(cfg.get("limit", 9))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    params = urllib.parse.urlencode({
        "q": "created:>%s stars:>%d" % (since, min_stars),
        "sort": "stars",
        "order": "desc",
        "per_page": limit,
    })
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token

    payload = json.loads(http_get(
        "https://api.github.com/search/repositories?" + params,
        headers=headers,
    ).decode("utf-8"))

    repos = []
    for repo in payload.get("items", []):
        repos.append({
            "name": repo.get("full_name", ""),
            "url": repo.get("html_url", ""),
            "desc": clean_text(repo.get("description") or "", limit=110, strip_tail=False),
            "lang": repo.get("language") or "",
            "stars": int(repo.get("stargazers_count") or 0),
            "forks": int(repo.get("forks_count") or 0),
        })
    return repos


# ---------------------------------------------------------------- 行情

def fetch_quotes(cfg):
    """用 Yahoo Finance 的 chart 端点取指数/商品/加密的最新价与前收。"""
    if not cfg:
        return []

    def one(item):
        symbol = item["symbol"]
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=1d" % urllib.parse.quote(symbol, safe="")
        try:
            payload = json.loads(http_get(url, headers={"Accept": "application/json"}).decode("utf-8"))
            meta = payload["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            if price is None:
                return None
            change = pct = None
            if prev:
                change = float(price) - float(prev)
                pct = change / float(prev) * 100
            return {
                "symbol": symbol,
                "name": item.get("name") or symbol,
                "price": round(float(price), 2),
                "change": round(change, 2) if change is not None else None,
                "pct": round(pct, 2) if pct is not None else None,
                "currency": meta.get("currency") or "",
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        return [r for r in pool.map(one, cfg) if r]


# ---------------------------------------------------------------- 主流程

def build(args):
    config = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    site = config.get("site", {})
    categories = config.get("categories", [])
    sources = config.get("sources", [])

    now = datetime.now(timezone.utc)
    window = now - timedelta(days=args.days)
    total = args.total or int(site.get("max_items", 120))
    per_source = args.per_source or int(site.get("max_items_per_source", 6))

    print("抓取 %d 个源 …" % len(sources))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(fetch_source, sources))

    collected = []
    ok_sources = 0
    failed = []
    for source, items, error in results:
        if error:
            failed.append((source["name"], error))
            print("  [失败] %-16s %s" % (source["name"], error))
            continue
        ok_sources += 1
        fresh = [it for it in items if not it["published"] or parse_date(it["published"]) >= window]
        fresh.sort(key=lambda it: it["published"] or "", reverse=True)
        fresh = fresh[:per_source]
        collected.extend(fresh)
        print("  [成功] %-16s %2d 条" % (source["name"], len(fresh)))

    # 去重：规范化 URL 优先，其次标题
    seen, unique = set(), []
    for it in collected:
        key = normalize_url(it["url"]) or ("title:" + it["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    unique.sort(key=lambda it: it["published"] or "", reverse=True)
    unique = unique[:total]

    # 保护：抓取异常时不要把上一版内容覆盖成空白页
    if len(unique) < 10 and OUT_HTML.exists():
        print("\n只抓到 %d 条，低于安全阈值，保留上一版 index.html 不覆盖。" % len(unique))
        return 1

    repos = []
    try:
        repos = fetch_github(config.get("github"))
        print("GitHub 精选：%d 个仓库" % len(repos))
    except Exception as exc:
        print("GitHub 精选获取失败（不影响资讯）：%s: %s" % (type(exc).__name__, str(exc)[:100]))

    quotes = []
    try:
        quotes = fetch_quotes(config.get("quotes"))
        print("行情：%d/%d 个标的" % (len(quotes), len(config.get("quotes") or [])))
    except Exception as exc:
        print("行情获取失败（不影响资讯）：%s: %s" % (type(exc).__name__, str(exc)[:100]))

    data = {
        "site": {
            "title": site.get("title", "今日视界"),
            "subtitle": site.get("subtitle", ""),
        },
        "categories": categories,
        "generated_at": now.isoformat(),
        "source_count": ok_sources,
        "items": unique,
        "repos": repos,
        "quotes": quotes,
        "quote_at": now.isoformat(),
        "sources": [
            {"name": s.get("name", ""), "url": s.get("url", ""), "category": s.get("category", "")}
            for s in sources
        ],
    }

    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    OUT_HTML.write_text(template.replace("__DATA__", payload), encoding="utf-8")

    print("\n完成：%d 条，来源 %d/%d 可用 → %s" % (len(unique), ok_sources, len(sources), OUT_HTML.name))
    if failed:
        print("失败源：%s" % "、".join(name for name, _ in failed))
    return 0


def main():
    parser = argparse.ArgumentParser(description="聚合 RSS 生成每日简报")
    parser.add_argument("--days", type=int, default=7, help="时间窗口（天）")
    parser.add_argument("--total", type=int, default=0, help="总条数上限（默认取 sources.json 的 max_items）")
    parser.add_argument("--per-source", type=int, default=0, help="单源条数上限（默认取 sources.json）")
    parser.add_argument("--workers", type=int, default=10, help="并发数")
    args = parser.parse_args()
    return build(args)


if __name__ == "__main__":
    sys.exit(main())
