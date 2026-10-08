#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 日报三源融合抓取器（确定性管道，仅标准库，无需 API key）。

数据源：
  1) 何夕2077 AI资讯日报  https://hex2077.dev/zh-CN/docs/YYYY-MM/YYYY-MM-DD/
  2) AIHOT 精选 RSS        https://aihot.news/feed.xml
  3) follow-builders-lite  https://github.com/zarazhangrui/follow-builders 上游 feed

输出：单个 JSON 到 stdout（可选 --out 写文件），供上层 Agent 汇总成中文日报。
失败不抛栈：单源失败会记录在 errors 中，其余源继续，保证"每天必有产出"。
"""

from __future__ import annotations

import argparse
import html as htmlmod
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
HEX_BASE = "https://hex2077.dev/zh-CN/docs"
AIHOT_FEED = "https://aihot.news/feed.xml"
def _find_lite_script() -> Path:
    """定位 follow-builders-lite 的取数脚本。

    优先级：环境变量 AI_DAILY_LITE_SCRIPT > WorkBuddy 用户技能目录 > 本仓库同级 skills/。
    """
    env = os.environ.get("AI_DAILY_LITE_SCRIPT", "").strip()
    cands = [Path(env)] if env else []
    cands.append(Path.home() / ".workbuddy" / "skills" / "follow-builders-lite" / "scripts" / "prepare_digest.py")
    cands.append(Path(__file__).resolve().parent.parent / "skills" / "follow-builders-lite" / "scripts" / "prepare_digest.py")
    for c in cands:
        if c.is_file():
            return c
    return cands[0]


LITE_SCRIPT = _find_lite_script()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) ai-daily-merged/1.0"


# ---------------------------------------------------------------- helpers
def http_get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        enc = resp.headers.get_content_charset() or "utf-8"
        return data.decode(enc, errors="replace")


def strip_tags(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<svg[\s\S]*?</svg>", " ", s, flags=re.I)
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.I)
    s = re.sub(r"<img[^>]*>", " ", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmlmod.unescape(s)
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def extract_links(s: str) -> list[dict]:
    out, seen = [], set()
    for m in re.finditer(r'<a\s[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', s, flags=re.I):
        url = htmlmod.unescape(m.group(1)).strip()
        if not url.startswith("http"):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append({"text": strip_tags(m.group(2)), "url": url})
    return out


# ---------------------------------------------------------------- source 1: hex2077
def parse_hex2077(html: str) -> dict:
    title = ""
    m = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", html)
    if m:
        title = strip_tags(m.group(1))

    summary = ""
    m = re.search(r'id="今日摘要"[\s\S]*?</h2>([\s\S]*?)(?=<h[23][^>]*id=)', html)
    if m:
        summary = strip_tags(m.group(1))

    sections = []
    h3s = list(re.finditer(r'<h3[^>]*id="([^"]+)"[^>]*>([\s\S]*?)</h3>', html))
    for idx, m3 in enumerate(h3s):
        name = strip_tags(m3.group(2)) or m3.group(1)
        start = m3.end()
        end = h3s[idx + 1].start() if idx + 1 < len(h3s) else len(html)
        seg = html[start:end]
        cut = re.search(r"<h2[^>]*id=", seg)
        if cut:
            seg = seg[: cut.start()]
        items = []
        for li in re.finditer(r"<li[^>]*>([\s\S]*?)</li>", seg):
            inner = li.group(1)
            st = re.search(r"<strong[^>]*>([\s\S]*?)</strong>", inner)
            it_title = strip_tags(st.group(1)) if st else ""
            body = strip_tags(re.sub(r"<ol[\s\S]*?</ol>", " ", inner))
            links = extract_links(inner)
            if not body:
                continue
            items.append({"title": it_title, "text": body, "links": links})
        if items:
            sections.append({"name": name, "items": items})

    return {"title": title, "summary": summary, "sections": sections}


def fetch_hex2077(target: datetime, lookback_days: int, timeout: int) -> dict:
    last_err = None
    for i in range(lookback_days + 1):
        d = (target - timedelta(days=i)).date()
        url = f"{HEX_BASE}/{d:%Y-%m}/{d:%Y-%m-%d}/"
        try:
            html = http_get(url, timeout)
        except urllib.error.HTTPError as e:
            last_err = f"{url} -> HTTP {e.code}"
            if e.code == 404:
                continue
            continue
        except Exception as e:  # noqa: BLE001
            last_err = f"{url} -> {e}"
            continue
        data = parse_hex2077(html)
        if not data["sections"]:
            last_err = f"{url} -> 页面无板块内容"
            continue
        data.update({"date": d.isoformat(), "url": url, "isFallback": i > 0, "daysBack": i})
        return data
    return {"error": last_err or "no page found", "sections": []}


# ---------------------------------------------------------------- source 2: AIHOT
def parse_aihot(xml_text: str, cutoff: datetime) -> dict:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        def g(tag: str) -> str:
            el = it.find(tag)
            return (el.text or "").strip() if el is not None else ""

        desc_html = g("description")
        pub_raw = g("pubDate")
        try:
            from email.utils import parsedate_to_datetime

            pub = parsedate_to_datetime(pub_raw)
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            pub = None

        author = g("author")
        source = ""
        am = re.search(r"\(([^)]*)\)", author)
        if am:
            source = am.group(1).strip()

        items.append(
            {
                "title": g("title"),
                "url": g("link"),
                "category": g("category"),
                "source": source,
                "publishedAt": pub.astimezone(CST).isoformat() if pub else pub_raw,
                "summary": strip_tags(desc_html),
                "links": [l for l in extract_links(desc_html) if "aihot.news/items/" not in l["url"]],
                "_pub": pub,
            }
        )

    # window filter
    keep = [i for i in items if i["_pub"] and i["_pub"] >= cutoff]
    fallback = False
    if not keep:
        keep = [i for i in items if i["_pub"]][:12]
        fallback = True

    cats: dict[str, int] = {}
    for i in keep:
        cats[i["category"] or "未分类"] = cats.get(i["category"] or "未分类", 0) + 1

    return {"count": len(keep), "isFallback": fallback, "categories": cats, "items": keep}


def fetch_aihot(target: datetime, window_hours: int, timeout: int) -> dict:
    try:
        xml_text = http_get(AIHOT_FEED, timeout)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{AIHOT_FEED} -> {e}", "items": []}
    cutoff = target - timedelta(hours=window_hours)
    data = parse_aihot(xml_text, cutoff)
    data["feedUrl"] = AIHOT_FEED
    return data


# ---------------------------------------------------------------- source 3: follow-builders-lite
def run_follow_builders(max_chars: int, timeout: int) -> dict:
    if not LITE_SCRIPT.exists():
        return {"error": f"lite script not found: {LITE_SCRIPT}"}
    try:
        p = subprocess.run(
            [sys.executable, str(LITE_SCRIPT), "--language", "zh", "--max-field-chars", str(max_chars)],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if not p.stdout.strip():
            return {"error": f"empty stdout (exit {p.returncode})", "stderr": (p.stderr or "")[-400:]}
        data = json.loads(p.stdout)
        # lite 内部取数失败时会返回 status=partial + errors[]（无 error 键）。
        # 若不冒泡，外层会误判为"上游无更新"，实际是网络取不到 —— 这里统一转成 error。
        if not data.get("error") and data.get("status") not in (None, "ok"):
            inner = data.get("errors") or []
            if inner:
                data["error"] = "; ".join(str(e) for e in inner)[:400]
        return data
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------- main
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="目标日期 YYYY-MM-DD（GMT+8），默认今天")
    ap.add_argument("--window-hours", type=int, default=28, help="过去多少小时内的 AIHOT 条目（默认28）")
    ap.add_argument("--hex-lookback-days", type=int, default=3, help="hex2077 日报回溯天数（默认3）")
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--max-chars", type=int, default=6000, help="follow-builders 长文本截断")
    ap.add_argument("--no-follow-builders", action="store_true", help="跳过 follow-builders 源")
    ap.add_argument("--out", help="把 JSON 写入该文件（否则只写 stdout）")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    now = datetime.now(CST)
    if args.date:
        d = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=CST)
    else:
        d = now
    day_end = d.replace(hour=23, minute=59, second=59, microsecond=0)
    # 参考时刻：今天用"现在"，历史日期用当天日终。窗口向前推 window_hours。
    ref = now if d.date() >= now.date() else day_end

    errors = []
    hex_data = fetch_hex2077(day_end, args.hex_lookback_days, args.timeout)
    if hex_data.get("error"):
        errors.append(f"hex2077: {hex_data['error']}")

    aihot_data = fetch_aihot(ref, args.window_hours, args.timeout)
    if aihot_data.get("error"):
        errors.append(f"aihot: {aihot_data['error']}")

    fb_data: dict = {}
    if not args.no_follow_builders:
        fb_data = run_follow_builders(args.max_chars, timeout=150)
        if fb_data.get("error"):
            errors.append(f"follow-builders: {fb_data['error']}")

    # sanitize internal fields
    for it in aihot_data.get("items", []):
        it.pop("_pub", None)

    result = {
        "status": "ok" if not errors else "partial",
        "generatedAt": datetime.now(CST).isoformat(),
        "targetDate": d.date().isoformat(),
        "windowHours": args.window_hours,
        "errors": errors or None,
        "sources": {
            "hex2077": hex_data,
            "aihot": aihot_data,
            "followBuilders": {
                "stats": fb_data.get("stats"),
                "upstream": fb_data.get("upstream"),
                "x": fb_data.get("x", []),
                "podcasts": fb_data.get("podcasts", []),
                "blogs": fb_data.get("blogs", []),
                "error": fb_data.get("error"),
            },
        },
        "instructions": [
            "仅使用本 JSON 中的内容，不得编造事实或访问外部链接。",
            "三源交叉去重：同一事件出现在多个来源时合并为一条，标注多来源。",
            "侧重 AI Coding / 具身智能 / 端侧 AI，但不要遗漏重大行业事件。",
            "每条保留原始链接；不确定的信息明确标注。",
        ],
    }

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
