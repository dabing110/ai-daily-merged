#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yanxu_auth.py —— 为远程 OAuth MCP 服务建立一条"本机独立客户端"授权通道

背景：远程 OAuth MCP 服务的 access token 有效期通常只有 1 小时，且宿主的无头自动化会话
**不会触发凭据读取/刷新**（只在有人工交互的会话启动时才刷新），于是自动化时段里工具
检索不到。本脚本用「动态客户端注册 + PKCE 授权码流程」建立一条属于本机的独立通道，
token 存本地文件，之后由 yanxu_mcp.py 自行刷新并直连，全程不依赖宿主连接器。

配置（全部可用环境变量覆盖）：
    YANXU_ISSUER   服务根地址，默认 https://toolsvip.cn
    YANXU_HOME     token / client / pkce 文件目录，默认 ~/.yanxu

三种用法：
    python yanxu_auth.py                 # 启动本地回环（127.0.0.1:8765）等待回调，最长 15 分钟
    python yanxu_auth.py --manual        # 只打印授权链接并保存 PKCE，不等待（推荐：随时点，回头把回调 URL 给脚本）
    python yanxu_auth.py --exchange "<回调URL或裸code>"   # 用 --manual 保存的 PKCE 换取 token
    python yanxu_auth.py --status        # 查看 client / token 状态

产出（目录由 YANXU_HOME 决定，默认 ~/.yanxu）：
    .yanxu_client.json   # client_id 等注册信息（非敏感）
    .yanxu_pkce.json     # 待完成的 PKCE（verifier/state）
    .yanxu_token.json    # access/refresh token（敏感，勿外传，已在 .gitignore 中排除）
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import socketserver
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import datetime as dt

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

ISSUER = os.environ.get("YANXU_ISSUER", "https://toolsvip.cn").rstrip("/")
REGISTER_URL = f"{ISSUER}/register"
AUTHORIZE_URL = f"{ISSUER}/authorize"
TOKEN_URL = f"{ISSUER}/token"
REDIRECT_URI = "http://127.0.0.1:8765/callback"
SCOPE = "mcp"
WAIT_SECONDS = 900  # 默认回环等待窗口（秒）

TOOLS_DIR = os.environ.get("YANXU_HOME") or os.path.join(os.path.expanduser("~"), ".yanxu")
CLIENT_FILE = os.path.join(TOOLS_DIR, ".yanxu_client.json")
PKCE_FILE = os.path.join(TOOLS_DIR, ".yanxu_pkce.json")
TOKEN_FILE = os.path.join(TOOLS_DIR, ".yanxu_token.json")


# ---------- 基础 IO ----------

def http_post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def http_post_form(url, payload):
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}


def load_json(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


# ---------- 客户端注册 / PKCE ----------

def get_or_register_client():
    info = load_json(CLIENT_FILE)
    now = int(time.time())
    if info and info.get("client_id") and info.get("client_id_expires_at", 0) > now + 86400:
        print(f"[client] 复用已注册客户端 {info['client_id']}")
        return info
    print("[client] 注册新的动态客户端 ...")
    info = http_post_json(
        REGISTER_URL,
        {
            "client_name": "ai-daily-merged",
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": SCOPE,
        },
    )
    save_json(CLIENT_FILE, info)
    print(f"[client] 注册成功 client_id={info['client_id']}")
    return info


def pkce_pair():
    verifier = secrets.token_urlsafe(64)[:64]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_auth_url(client_id, challenge, state):
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )


def save_token(client_id, tok):
    expires_in = int(tok.get("expires_in") or 3600)
    prev = load_json(TOKEN_FILE) or {}
    refresh = tok.get("refresh_token") or prev.get("refresh_token")
    save_json(
        TOKEN_FILE,
        {
            "client_id": client_id,
            "access_token": tok["access_token"],
            "refresh_token": refresh,
            "token_type": tok.get("token_type", "Bearer"),
            "scope": tok.get("scope", SCOPE),
            "expires_at_ms": int((time.time() + expires_in) * 1000),
            "obtained_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


# ---------- 回调服务器 ----------

class _Handler(http.server.BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        _Handler.result = {k: v[0] for k, v in qs.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in _Handler.result
        self.wfile.write(
            (
                "<html><body style='font-family:sans-serif;padding:40px'>"
                f"<h2>{'授权成功 ✅' if ok else '授权失败 ❌'}</h2>"
                "<p>可以关闭本页面了。</p>"
                "</body></html>"
            ).encode("utf-8")
        )

    def log_message(self, *a, **kw):
        pass


def serve_once(port=8765, timeout=WAIT_SECONDS):
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), _Handler)
    httpd.timeout = 1
    deadline = time.time() + timeout
    while time.time() < deadline and not _Handler.result:
        httpd.handle_request()
    httpd.server_close()
    return _Handler.result


# ---------- 各模式 ----------

def cmd_status():
    tok = load_json(TOKEN_FILE)
    cli = load_json(CLIENT_FILE)
    print("=== 独立客户端状态 ===")
    print(f"issuer         : {ISSUER}")
    print(f"home           : {TOOLS_DIR}")
    if cli:
        exp = cli.get("client_id_expires_at", 0)
        print(f"client_id      : {cli.get('client_id')}")
        print(f"client 到期    : {dt.datetime.fromtimestamp(exp) if exp else '未知'}")
    else:
        print("client_id      : 未注册")
    if tok:
        exp_ms = tok.get("expires_at_ms", 0)
        exp = dt.datetime.fromtimestamp(exp_ms / 1000) if exp_ms else None
        remain = (exp_ms / 1000 - time.time()) if exp_ms else 0
        print(f"token 签发     : {tok.get('obtained_at')}")
        print(f"token 到期     : {exp}")
        if remain > 0:
            print(f"剩余有效期     : {remain/60:.0f} 分钟")
        else:
            print(f"状态           : 已过期 {abs(remain)/60:.0f} 分钟")
        print(f"refresh_token  : {'有' if tok.get('refresh_token') else '无'}")
    else:
        print("token          : 未获取")
    return 0


def cmd_manual():
    client = get_or_register_client()
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    save_json(
        PKCE_FILE,
        {
            "verifier": verifier,
            "state": state,
            "client_id": client["client_id"],
            "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    url = build_auth_url(client["client_id"], challenge, state)
    print("请打开下面的链接并点击「同意/授权」：")
    print("")
    print(url)
    print("")
    print("授权后浏览器会跳到 http://127.0.0.1:8765/callback?code=...")
    print("（页面可能显示「无法访问」，这不影响）请把地址栏里的完整 URL 复制回来。")
    return 0


def cmd_exchange(raw):
    pkce = load_json(PKCE_FILE)
    if not pkce:
        print("❌ 找不到 PKCE 记录，请先运行 --manual 生成授权链接。")
        return 1
    code = raw.strip()
    if "code=" in code:
        code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query).get("code", [""])[0]
    if not code:
        print("❌ 未能从输入中解析出 code。")
        return 1
    status, tok = http_post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": pkce["client_id"],
            "code_verifier": pkce["verifier"],
        },
    )
    if status != 200 or "access_token" not in tok:
        print(f"❌ 换取 token 失败 HTTP {status}: {tok}")
        return 1
    save_token(pkce["client_id"], tok)
    try:
        os.remove(PKCE_FILE)
    except Exception:
        pass
    print(f"✅ token 已保存到 {TOKEN_FILE}")
    print(f"   refresh_token: {'已获取' if tok.get('refresh_token') else '⚠️ 未返回'}")
    return 0


def cmd_wait():
    client = get_or_register_client()
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = build_auth_url(client["client_id"], challenge, state)
    print("\n" + "=" * 72)
    print("请在浏览器中打开下面的链接并点击「同意/授权」：")
    print(url)
    print("=" * 72)
    print(f"\n等待回调（最长 {WAIT_SECONDS//60} 分钟）...")
    result = serve_once(8765, timeout=WAIT_SECONDS)
    if not result:
        print("❌ 超时未收到回调。可改用 --manual 模式（不受时间窗限制）。")
        return 1
    if result.get("state") != state:
        print(f"⚠️ state 不匹配（got={result.get('state')}），已中止。")
        return 1
    code = result.get("code")
    if not code:
        print(f"❌ 回调未带 code：{result}")
        return 1
    print(f"[auth] 收到授权码（{code[:8]}…）")
    status, tok = http_post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client["client_id"],
            "code_verifier": verifier,
        },
    )
    if status != 200 or "access_token" not in tok:
        print(f"❌ 换取 token 失败 HTTP {status}: {tok}")
        return 1
    save_token(client["client_id"], tok)
    expires_in = int(tok.get("expires_in") or 3600)
    print(f"✅ token 已保存到 {TOKEN_FILE}（有效期 {expires_in/60:.0f} 分钟）")
    return 0


def main():
    argv = sys.argv[1:]
    if "--status" in argv:
        return cmd_status()
    if "--manual" in argv:
        return cmd_manual()
    if "--exchange" in argv:
        i = argv.index("--exchange")
        if i + 1 >= len(argv):
            print("用法：--exchange \"<回调URL或裸code>\"")
            return 1
        return cmd_exchange(argv[i + 1])
    return cmd_wait()


if __name__ == "__main__":
    sys.exit(main())
