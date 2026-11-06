#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yanxu_publish.py —— 直连发布服务完成「上传封面 → 建稿 → 推公众号草稿箱」

与宿主平台的 MCP 工具注册完全解耦：用本机独立 OAuth 客户端（先跑 yanxu_auth.py 授权一次）
的 token 直连远程 MCP，因此无头自动化会话也能跑通（详见 yanxu_mcp.py 顶部说明）。

用法：
    python yanxu_publish.py --md <日报MD> --cover <封面PNG> --focus "<焦点短语>" \
        --abstract "<摘要≤100字>" --account-id "<公众号ID>" \
        [--intro "<导语>"] [--out article_YYYY-MM-DD.json] \
        [--pending 待建稿_YYYY-MM-DD.json] [--theme-id neon] [--dry-run]

    --dry-run   只做到「上传封面 + 生成 chapters JSON」，不建稿、不发布（安全验证用）

配置（环境变量）：
    YANXU_ACCOUNT_ID        公众号 ID（也可用 --account-id 传；必填，仓库里不预置任何账号）
    YANXU_THEME_ID          排版主题 id（部分账号 default_theme_id 为空，必须显式指定）
    YANXU_ISSUER / YANXU_HOME  见 yanxu_mcp.py
    AI_DAILY_BUILD_ARTICLE  build_article.py 的路径（默认取本脚本同目录，再退到技能目录）

幂等：若 --pending 指定的 JSON 里 status=done，则直接跳过（发布服务通常没有草稿删除接口，
避免同一天重复建稿）。**红线：本脚本只推草稿箱，绝不调用群发能力。**
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
import datetime as dt

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from yanxu_mcp import McpClient, unwrap, load_json, save_json  # noqa: E402

PYTHON = sys.executable
_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_build_article():
    cands = []
    env = os.environ.get("AI_DAILY_BUILD_ARTICLE", "").strip()
    if env:
        cands.append(env)
    cands.append(os.path.join(_HERE, "build_article.py"))
    cands.append(
        os.path.join(
            os.path.expanduser("~"), ".workbuddy", "skills", "ai-daily-merged",
            "scripts", "build_article.py",
        )
    )
    for c in cands:
        if os.path.isfile(c):
            return c
    return cands[0]


BUILD_ARTICLE = _find_build_article()


def upload_cover(cli, png_path):
    """用 get_upload_channel 拿一次性通道，multipart 上传封面，返回公网直链"""
    ch = unwrap(cli.call_tool("get_upload_channel", {"scene": "cover"}))
    if isinstance(ch, list):
        ch = ch[0] if ch else {}
    upload_url = (ch or {}).get("upload_url")
    if not upload_url:
        raise RuntimeError(f"未取得 upload_url：{ch}")

    with open(png_path, "rb") as f:
        data = f.read()
    filename = os.path.basename(png_path)
    boundary = "----yanxu" + secrets.token_hex(8)
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        upload_url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        res = json.loads(r.read().decode("utf-8"))
    url = (res.get("data") or {}).get("url")
    if not url:
        raise RuntimeError(f"封面上传返回异常：{res}")
    print(f"[cover] 上传成功 {url}（{(res.get('data') or {}).get('size')} bytes）")
    return url


def build_chapters(md_path, focus, abstract, cover_url, intro, out_json, pending_json):
    cmd = [
        PYTHON, BUILD_ARTICLE,
        "--md", md_path,
        "--focus", focus,
        "--abstract", abstract,
        "--cover", cover_url,
        "--out", out_json,
    ]
    if pending_json:
        cmd += ["--pending", pending_json]
    if intro:
        cmd += ["--intro", intro]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(p.stdout.strip())
    if p.returncode != 0:
        raise RuntimeError(f"build_article.py 失败（退出码 {p.returncode}）: {p.stderr.strip()}")
    return load_json(out_json)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md")
    ap.add_argument("--cover")
    ap.add_argument("--focus")
    ap.add_argument("--abstract")
    ap.add_argument("--intro")
    ap.add_argument("--out", default=None)
    ap.add_argument("--pending", default=None)
    ap.add_argument("--account-id", default=os.environ.get("YANXU_ACCOUNT_ID", "").strip())
    ap.add_argument("--theme-id", default=os.environ.get("YANXU_THEME_ID", "").strip())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 幂等保护
    if args.pending and os.path.exists(args.pending):
        prev = load_json(args.pending) or {}
        if prev.get("status") == "done":
            print(f"⏭  {args.pending} 已 status=done（info_id={prev.get('info_id')}），跳过，避免重复建稿。")
            return 0

    if not (args.md and args.cover and args.focus and args.abstract):
        print("缺少必填参数：--md --cover --focus --abstract")
        return 1

    cli = McpClient()
    cli.initialize()

    cover_url = upload_cover(cli, args.cover)
    out_json = args.out or os.path.join(
        os.path.dirname(os.path.abspath(args.md)),
        f"article_{dt.date.today().isoformat()}.json",
    )
    article = build_chapters(
        args.md, args.focus, args.abstract, cover_url, args.intro, out_json, args.pending
    )

    if args.dry_run:
        print("[dry-run] 已生成 chapters，未建稿、未发布。")
        print(f"  title    = {article['title']}")
        print(f"  abstract = {article['abstract']}")
        print(f"  chapters = {len(article['chapters'])} 块")
        return 0

    if not args.account_id:
        print("缺少 --account-id（或用环境变量 YANXU_ACCOUNT_ID 指定要推送的公众号）。")
        print("可用 `python yanxu_mcp.py --accounts` 查询已绑定账号。")
        return 1

    print("[create] 建稿中 ...")
    created = unwrap(
        cli.call_tool(
            "create_article",
            {
                "title": article["title"],
                "abstract": article["abstract"],
                "intro": article["intro"],
                "cover_img": article["cover_img"],
                "chapters": article["chapters"],
            },
        )
    )
    info_id = (created or {}).get("info_id") if isinstance(created, dict) else None
    if not info_id:
        raise RuntimeError(f"建稿返回异常：{created}")
    print(f"[create] info_id = {info_id}")

    print("[publish] 推送到公众号草稿箱 ...")
    pub_args = {"info_id": info_id, "account_id": args.account_id}
    if args.theme_id:
        pub_args["theme_id"] = args.theme_id
    else:
        print("⚠️ 未指定 --theme-id，交由服务端使用账号默认主题（部分账号默认为空会导致失败）。")
    pub = unwrap(cli.call_tool("publish_article", pub_args))
    print(f"[publish] {json.dumps(pub, ensure_ascii=False)}")

    if args.pending:
        rec = load_json(args.pending) or {}
        rec.update(
            {
                "status": "done",
                "info_id": info_id,
                "publish_id": (pub or {}).get("publish_id"),
                "media_id": (pub or {}).get("media_id"),
                "account_id": args.account_id,
                "theme_id": args.theme_id,
                "cover": cover_url,
            }
        )
        save_json(args.pending, rec)
        print(f"[done] 已更新 {args.pending}（status=done）")

    print("\n✅ 已进公众号草稿箱（未群发；群发请在公众号后台人工操作）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)
