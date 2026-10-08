#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_article.py —— 把 Obsidian 日报 MD 转成言序 create_article 参数 JSON。

背景：此前建稿需要手写约 10KB 的 chapters JSON（最易出错、最耗时的一环），
且 title/abstract 字数要手工跑 Python 校验。本脚本把这几步全部固化：
  1. 解析固定版式的日报 MD -> chapters（今日要点/主条目/速览/Builder/播客/免责）
  2. 自动校验 title<=30、abstract<=100（超限直接报错退出，不静默截断）
  3. 写出 create_article 参数 JSON + 待建稿状态文件（幂等，防同日重复建稿）

固定版式依赖（与 SKILL.md 一致）：
  frontmatter -> # AI日报 -> > 三源融合引用块 -> ## 今日要点 -> ## 主条目(### N.)
  -> ## 今日速览 -> ## Builder 观点 -> ## 播客 -> ## 来源与免责声明

用法：
  python build_article.py --md <日报.md> [--focus "焦点短语"] [--cover <封面URL>]
                          [--abstract "..."] [--intro "..."] [--out article.json]
退出码：0=成功；2=字数超限或解析失败（此时不写文件）
"""

import argparse
import json
import os
import re
import sys

TITLE_MAX = 30
ABSTRACT_MAX = 100

# markdown 链接 [text](url) -> "text url"（公众号块不渲染 markdown，保留明文链接）
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def strip_links(text: str) -> str:
    """把 [text](url) 转成 'text url'，并去掉 ** 加粗（公众号块不渲染 markdown）。"""
    text = LINK_RE.sub(r"\1 \2", text)
    return text.replace("**", "").strip()


def split_frontmatter(raw: str):
    """返回 (meta_dict, body)。无 frontmatter 时 meta 为空字典。"""
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            meta_block = raw[3:end]
            body = raw[end + 4:]
            meta = {}
            for line in meta_block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            return meta, body
    return {}, raw


def section_blocks(body: str, heading: str):
    """取出 '## heading' 到下一个 '## ' 之间的内容行。"""
    m = re.search(r"^##\s+" + re.escape(heading) + r".*$", body, re.M)
    if not m:
        return []
    rest = body[m.end():]
    nxt = re.search(r"^##\s+", rest, re.M)
    chunk = rest[: nxt.start()] if nxt else rest
    return [ln.rstrip() for ln in chunk.strip().splitlines()]


def parse_key_points(body: str):
    """今日要点 -> ordered list items。"""
    items = []
    for ln in section_blocks(body, "今日要点"):
        m = re.match(r"^\s*\d+\.\s+(.*)$", ln)
        if m:
            items.append(strip_links(m.group(1)))
    return items


def parse_main_entries(body: str):
    """主条目 -> [{title, blocks:[paragraph...]}]

    兼容两种版式：
      A（旧）：## 主条目  ->  ### N. 标题
      B（现行）：## 一、标题 / ## 二、标题（编号中文序号）
    扫描到下一个非数字二级标题时收尾。
    """
    entries, cur, in_sec = [], None, False
    for ln in body.splitlines():
        m = re.match(r"^##\s+(.*)$", ln)
        if m:
            heading = m.group(1).strip()
            num = re.match(r"^[一二三四五六七八九十]+\s*、\s*(.*)$", heading)
            if num:  # 版式 B：新的一条主条目
                if cur:
                    entries.append(cur)
                cur = {"title": num.group(1).strip(), "paras": []}
                in_sec = True
                continue
            if cur:
                entries.append(cur)
                cur = None
            in_sec = heading.startswith("主条目")
            continue
        if not in_sec:
            continue
        if cur is None:
            m3 = re.match(r"^###\s+(.*)$", ln)  # 版式 A 的子标题
            if m3:
                cur = {"title": strip_links(m3.group(1)).strip(), "paras": []}
            continue
        if not ln.strip() or ln.strip() == "---":
            continue
        # 去掉 **事件内容** / **值得关注的原因** 这类加粗小标题，正文照常并入
        ln = re.sub(r"^\*\*([^*：:]+)\*\*[：:]?\s*", "", ln.strip()).strip()
        if ln:
            cur["paras"].append(strip_links(ln))
    if cur:
        entries.append(cur)

    chapters = []
    for e in entries:
        blocks = [{"type": "paragraph", "content": p} for p in e["paras"] if p]
        if blocks:
            chapters.append({"title": e["title"], "type": "chapter", "blocks": blocks})
    return chapters


def parse_bullets(body: str, heading: str):
    """今日速览 -> unordered list items。"""
    items = []
    for ln in section_blocks(body, heading):
        m = re.match(r"^\s*[-*]\s+(.*)$", ln)
        if m:
            items.append(strip_links(m.group(1)))
    return items


def parse_prose(body: str, heading: str):
    """Builder 观点 / 播客 / 免责声明：每遇到 **加粗** 起的段落就切一块。"""
    lines = [ln for ln in section_blocks(body, heading) if ln.strip() != "---"]
    paras, buf = [], []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("**"):
            if buf:
                paras.append(strip_links(" ".join(buf)))
                buf = []
        buf.append(ln)
    if buf:
        paras.append(strip_links(" ".join(buf)))
    return [p for p in paras if p]


# Builder 观点按人分块：以 "**Name**（bio）" 起头，其下的 - 条目并入同一段，
# 输出形如「Name（bio）：观点...」，去掉 markdown 星号
def parse_builders(body: str):
    lines = [ln for ln in section_blocks(body, "Builder 观点") if ln.strip() != "---"]
    blocks, name, buf = [], None, []

    def flush():
        """把一个 builder 归档：姓名行自带正文时直接用该行，否则接上续写行。"""
        if not name:
            return
        entry = name if not buf else name + " " + " ".join(buf)
        blocks.append(strip_links(entry))

    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("**"):
            flush()          # 注意：不能要求 buf 非空——「**姓名**（bio）：正文」同行时 buf 为空
            name = ln
            buf = []
        else:
            buf.append(re.sub(r"^\s*[-*]\s+", "", ln))
    flush()
    return [b for b in blocks if b]


def quote_intro(body: str):
    """取 '> ...' 引用块作为缺省 intro。"""
    out = []
    for ln in body.splitlines():
        if ln.strip().startswith(">"):
            out.append(strip_links(ln.lstrip("> ").strip()))
        elif out:
            break
    return " ".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True, help="日报 Markdown 绝对路径")
    ap.add_argument("--focus", default="", help="标题焦点短语（<=18 字）")
    ap.add_argument("--cover", default="", help="封面公网直链，可空")
    ap.add_argument("--abstract", default="", help="摘要，<=100 字；缺省由要点自动拼接")
    ap.add_argument("--intro", default="", help="导语；缺省用 MD 的三源融合引用块")
    ap.add_argument("--out", default="", help="输出 JSON 路径，缺省 <MD同目录>/article_<date>.json")
    ap.add_argument("--pending", default="", help="待建稿状态文件路径，缺省不写")
    args = ap.parse_args()

    if not os.path.isfile(args.md):
        print("ERROR: 日报 MD 不存在:", args.md)
        return 2
    with open(args.md, "r", encoding="utf-8") as f:
        raw = f.read()

    meta, body = split_frontmatter(raw)
    date = meta.get("date") or re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(args.md)).group(1)
    m, d = int(date.split("-")[1]), int(date.split("-")[2])

    key_points = parse_key_points(body)
    main_ch = parse_main_entries(body)
    quick = parse_bullets(body, "今日速览")
    builders = parse_builders(body)
    podcast = parse_prose(body, "播客")
    disclaimer = parse_prose(body, "来源与免责声明")

    if not key_points or not main_ch:
        print("ERROR: 解析失败——未找到「今日要点」或「主条目」，请检查 MD 版式")
        return 2

    # 标题：AI日报 M.D丨焦点短语
    focus = args.focus.strip()
    if not focus and main_ch:
        focus = re.sub(r"^\d+\.\s*", "", main_ch[0]["title"])[:18]
    title = "AI日报 %d.%d丨%s" % (m, d, focus)

    # 摘要：缺省取前 3 条要点拼接
    abstract = args.abstract.strip()
    if not abstract:
        abstract = "；".join(re.sub(r"（[^）]*）", "", k) for k in key_points[:3])[:ABSTRACT_MAX]

    intro = args.intro.strip() or quote_intro(body) or abstract

    problems = []
    if len(title) > TITLE_MAX:
        problems.append("title 超长：%d > %d —— 请缩短 --focus（当前「%s」）" % (len(title), TITLE_MAX, focus))
    if len(abstract) > ABSTRACT_MAX:
        problems.append("abstract 超长：%d > %d —— 请用 --abstract 传入更短版本" % (len(abstract), ABSTRACT_MAX))
    if problems:
        print("ERROR: 字数校验未通过：")
        for p in problems:
            print("  -", p)
        print("  title =", title)
        print("  abstract =", abstract)
        return 2

    chapters = [{"title": "今日要点", "type": "chapter",
                 "blocks": [{"type": "list", "style": "ordered", "items": key_points}]}]
    chapters += main_ch
    if quick:
        chapters.append({"title": "今日速览", "type": "chapter",
                         "blocks": [{"type": "list", "style": "unordered", "items": quick}]})
    if builders:
        chapters.append({"title": "Builder 观点", "type": "chapter",
                         "blocks": [{"type": "paragraph", "content": p} for p in builders]})
    if podcast:
        chapters.append({"title": "播客", "type": "chapter",
                         "blocks": [{"type": "paragraph", "content": p} for p in podcast]})
    if disclaimer:
        chapters.append({"title": "来源与免责声明", "type": "chapter",
                         "blocks": [{"type": "paragraph", "content": p} for p in disclaimer]})

    payload = {"title": title, "abstract": abstract, "intro": intro,
               "cover_img": args.cover, "chapters": chapters}

    out = args.out or os.path.join(os.path.dirname(args.md), "article_%s.json" % date)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    if args.pending:
        state = {"date": date, "md": args.md, "cover": args.cover,
                 "article_json": out, "title": title, "abstract": abstract,
                 "status": "pending"}
        with open(args.pending, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print("PENDING", args.pending)

    print("OK", out)
    print("title    (%d) %s" % (len(title), title))
    print("abstract (%d) %s" % (len(abstract), abstract))
    print("chapters: %d 块 | 要点 %d / 主条目 %d / 速览 %d / Builder %d / 播客 %d / 免责 %d"
          % (len(chapters), len(key_points), len(main_ch), len(quick),
             len(builders), len(podcast), len(disclaimer)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
