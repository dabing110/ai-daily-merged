#!/usr/bin/env python3
"""Fetch and compact upstream follow-builders feeds.

This script deliberately does not scrape source sites or use private API keys.
It reads only the public JSON feed artifacts produced by the upstream GitHub
Actions workflow in zarazhangrui/follow-builders.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UPSTREAM_REPO = "zarazhangrui/follow-builders"
OFFICIAL_RAW_BASE = f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/main"
# raw.githubusercontent.com 在部分网络环境会被直接重置连接（国内沙箱代理白名单外即如此）。
# 因此提供回退通道：可用环境变量 FOLLOW_BUILDERS_RAW_BASE 覆盖，否则官方失败后走 gh.idayer.com 镜像。
MIRROR_BASE = f"https://gh.idayer.com/https://raw.githubusercontent.com/{UPSTREAM_REPO}/main"
# api.github.com 的 Contents API 通常也在沙箱白名单内，且返回的是同一仓库文件。
# 镜像可能被 Cloudflare 防火墙规则拦截（error 1027，持续 429，重试无效），
# 因此把 API 作为最终兜底通道；用 raw media type 可直接拿到文件原文。
GITHUB_API_BASE = f"https://api.github.com/repos/{UPSTREAM_REPO}/contents"
RAW_BASE = os.environ.get("FOLLOW_BUILDERS_RAW_BASE") or OFFICIAL_RAW_BASE
RAW_BASE_CANDIDATES = [RAW_BASE]
if RAW_BASE == OFFICIAL_RAW_BASE:
    RAW_BASE_CANDIDATES.append(MIRROR_BASE)
if GITHUB_API_BASE not in RAW_BASE_CANDIDATES:
    RAW_BASE_CANDIDATES.append(GITHUB_API_BASE)
FEED_FILES = {
    "x": "feed-x.json",
    "podcasts": "feed-podcasts.json",
    "blogs": "feed-blogs.json",
}


def trim_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n\n[truncated to {limit} chars]"


def _fetch_one(url: str, timeout: int) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "follow-builders-lite/1.0",
    }
    if url.startswith(GITHUB_API_BASE):
        headers["Accept"] = "application/vnd.github.raw+json"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def fetch_json(filename: str, timeout: int) -> tuple[dict[str, Any], str]:
    """按候选通道依次尝试，返回 (数据, 实际可用的 base)。

    官方 raw 不可达时自动回退镜像，避免整源静默变空。
    """
    attempts: list[str] = []
    for base in RAW_BASE_CANDIDATES:
        url = f"{base}/{filename}"
        try:
            return _fetch_one(url, timeout), base
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            attempts.append(f"{base} -> {type(exc).__name__}: {exc}")
    raise OSError("all raw bases failed: " + " | ".join(attempts))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_feeds(local_dir: Path | None, timeout: int) -> tuple[dict[str, Any], list[str], str | None]:
    feeds: dict[str, Any] = {}
    errors: list[str] = []
    used_base: str | None = None

    for key, filename in FEED_FILES.items():
        try:
            if local_dir:
                feeds[key] = read_json(local_dir / filename)
                used_base = f"local:{local_dir}"
            else:
                feeds[key], used_base = fetch_json(filename, timeout)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            feeds[key] = {}
            errors.append(f"Could not load {filename}: {exc}")

    return feeds, errors, used_base


def compact_x(feed: dict[str, Any], max_field_chars: int) -> list[dict[str, Any]]:
    accounts = []
    for account in feed.get("x") or []:
        tweets = []
        for tweet in account.get("tweets") or []:
            tweets.append(
                {
                    "id": tweet.get("id"),
                    "text": trim_text(tweet.get("text"), min(max_field_chars, 1600)),
                    "createdAt": tweet.get("createdAt"),
                    "url": tweet.get("url"),
                    "likes": tweet.get("likes", 0),
                    "retweets": tweet.get("retweets", 0),
                    "replies": tweet.get("replies", 0),
                    "isQuote": bool(tweet.get("isQuote")),
                    "quotedTweetId": tweet.get("quotedTweetId"),
                }
            )
        if tweets:
            accounts.append(
                {
                    "source": "x",
                    "name": account.get("name"),
                    "handle": account.get("handle"),
                    "bio": trim_text(account.get("bio"), 500),
                    "tweets": tweets,
                }
            )
    return accounts


def compact_podcasts(feed: dict[str, Any], max_field_chars: int) -> list[dict[str, Any]]:
    podcasts = []
    for item in feed.get("podcasts") or []:
        podcasts.append(
            {
                "source": "podcast",
                "name": item.get("name"),
                "title": item.get("title"),
                "url": item.get("url"),
                "publishedAt": item.get("publishedAt"),
                "transcript": trim_text(item.get("transcript"), max_field_chars),
            }
        )
    return podcasts


def compact_blogs(feed: dict[str, Any], max_field_chars: int) -> list[dict[str, Any]]:
    blogs = []
    for item in feed.get("blogs") or []:
        blogs.append(
            {
                "source": "blog",
                "name": item.get("name"),
                "title": item.get("title"),
                "url": item.get("url"),
                "publishedAt": item.get("publishedAt"),
                "author": item.get("author"),
                "description": trim_text(item.get("description"), 800),
                "content": trim_text(item.get("content"), max_field_chars),
            }
        )
    return blogs


def feed_errors(feeds: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in FEED_FILES:
        for error in feeds.get(key, {}).get("errors") or []:
            errors.append(f"{key}: {error}")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=["en", "zh", "bilingual"], default="en")
    parser.add_argument("--max-field-chars", type=int, default=6000)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument(
        "--local-dir",
        type=Path,
        help="Read feed-x.json, feed-podcasts.json, and feed-blogs.json from a local clone.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    feeds, load_errors, used_base = load_feeds(args.local_dir, args.timeout)
    errors = load_errors + feed_errors(feeds)

    x = compact_x(feeds["x"], args.max_field_chars)
    podcasts = compact_podcasts(feeds["podcasts"], args.max_field_chars)
    blogs = compact_blogs(feeds["blogs"], args.max_field_chars)

    output = {
        "status": "ok" if not load_errors else "partial",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "upstream": {
            "repo": UPSTREAM_REPO,
            "feedBaseUrl": used_base or RAW_BASE,
            "feedBaseCandidates": RAW_BASE_CANDIDATES,
            "feedGeneratedAt": {
                "x": feeds["x"].get("generatedAt"),
                "podcasts": feeds["podcasts"].get("generatedAt"),
                "blogs": feeds["blogs"].get("generatedAt"),
            },
        },
        "config": {
            "language": args.language,
            "maxFieldChars": args.max_field_chars,
            "mode": "read-only static feed consumer",
        },
        "stats": {
            "xBuilders": len(x),
            "totalTweets": sum(len(account["tweets"]) for account in x),
            "podcastEpisodes": len(podcasts),
            "blogPosts": len(blogs),
        },
        "x": x,
        "podcasts": podcasts,
        "blogs": blogs,
        "errors": errors or None,
        "digestInstructions": [
            "Use only the content in this JSON.",
            "Prioritize original builder insights, product/research changes, and actionable takeaways.",
            "Include source links.",
            "Omit empty sections.",
        ],
    }

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if not load_errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
