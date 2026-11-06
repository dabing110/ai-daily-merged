#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yanxu_mcp.py —— 用 JSON-RPC 直连远程 MCP 服务，绕过宿主的工具注册

背景：无头自动化会话里，宿主的 MCP 连接器常常不会注册远程工具（表现为"工具不存在"，
而不是报错）。根因通常是**连接在宿主网络未就绪时失败后就不再重连**，与 token 是否过期
不是同一件事。本脚本用「本机独立 OAuth 客户端」（先跑 yanxu_auth.py 授权一次）拿到的
token 直接以 JSON-RPC 调远程工具，因此无头环境也能稳定跑通。

配置（全部可用环境变量覆盖）：
    YANXU_ISSUER   服务根地址，默认 https://toolsvip.cn（MCP 端点为 <issuer>/mcp）
    YANXU_HOME     token / client 文件目录，默认 ~/.yanxu

用法：
    python yanxu_mcp.py --status                 # 查看 token 状态
    python yanxu_mcp.py --refresh                # 强制刷新 access token
    python yanxu_mcp.py --tools                  # 列出服务端工具
    python yanxu_mcp.py --call <tool> --args '{...}'
    python yanxu_mcp.py --accounts               # 快捷：列出已绑定公众号
    python yanxu_mcp.py --themes                 # 快捷：列出样式主题
"""
import json
import os
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
MCP_URL = ISSUER + "/mcp"
TOKEN_URL = ISSUER + "/token"
PROTOCOL_VERSION = "2025-06-18"

TOOLS_DIR = os.environ.get("YANXU_HOME") or os.path.join(os.path.expanduser("~"), ".yanxu")
TOKEN_FILE = os.path.join(TOOLS_DIR, ".yanxu_token.json")


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


# ---------- token 管理 ----------

def refresh_access_token(tok=None):
    tok = tok or load_json(TOKEN_FILE)
    if not tok or not tok.get("refresh_token"):
        raise RuntimeError("没有 refresh_token，请先运行 yanxu_auth.py --manual 完成授权")
    payload = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
            "client_id": tok["client_id"],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            new = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"刷新失败 HTTP {e.code}: {body}")
    expires_in = int(new.get("expires_in") or 3600)
    merged = {
        "client_id": tok["client_id"],
        "access_token": new["access_token"],
        "refresh_token": new.get("refresh_token") or tok["refresh_token"],
        "token_type": new.get("token_type", "Bearer"),
        "scope": new.get("scope", tok.get("scope", "mcp")),
        "expires_at_ms": int((time.time() + expires_in) * 1000),
        "obtained_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_json(TOKEN_FILE, merged)
    print(f"[token] 已刷新，有效期 {expires_in/60:.0f} 分钟")
    return merged


def get_access_token(force=False):
    tok = load_json(TOKEN_FILE)
    if not tok:
        raise RuntimeError("未找到 token 文件，请先运行 yanxu_auth.py 完成授权")
    if force or tok.get("expires_at_ms", 0) < (time.time() + 120) * 1000:
        tok = refresh_access_token(tok)
    return tok["access_token"]


# ---------- MCP JSON-RPC ----------

class McpClient:
    def __init__(self, token=None):
        self.token = token or get_access_token()
        self.session_id = None
        self._id = 0
        self.server_info = None

    def _post(self, payload, notify=False):
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(MCP_URL, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"MCP HTTP {e.code}: {body[:400]}")
        if notify:
            return None
        return self._parse(raw)

    @staticmethod
    def _parse(raw):
        raw = raw.strip()
        if not raw:
            return None
        if raw.startswith("{"):
            return json.loads(raw)
        # SSE: 取最后一个 data: 行
        last = None
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                last = line[5:].strip()
        if last:
            return json.loads(last)
        return {"raw": raw}

    def initialize(self):
        self._id += 1
        res = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "ai-daily-merged-direct", "version": "1.0"},
                },
            }
        )
        self.server_info = (res or {}).get("result", {}).get("serverInfo")
        # 按 MCP 规范发送 initialized 通知
        try:
            self._post(
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                notify=True,
            )
        except Exception:
            pass
        return res

    def list_tools(self):
        self._id += 1
        return self._post(
            {"jsonrpc": "2.0", "id": self._id, "method": "tools/list", "params": {}}
        )

    def call_tool(self, name, args=None):
        self._id += 1
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": self._id,
                "method": "tools/call",
                "params": {"name": name, "arguments": args or {}},
            }
        )


def unwrap(resp):
    """把 tools/call 返回里的 content 文本抽出来"""
    if not resp:
        return resp
    if "error" in resp:
        return resp
    result = resp.get("result", {})
    if isinstance(result, dict) and "content" in result:
        parts = []
        for c in result["content"]:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        if len(parts) == 1:
            try:
                return json.loads(parts[0])
            except Exception:
                return parts[0]
        return parts
    return result


def main():
    argv = sys.argv[1:]
    try:
        if "--status" in argv:
            tok = load_json(TOKEN_FILE)
            if not tok:
                print("未授权：先运行 yanxu_auth.py --manual")
                return 1
            remain = tok.get("expires_at_ms", 0) / 1000 - time.time()
            print(f"issuer    : {ISSUER}")
            print(f"client_id : {tok['client_id']}")
            print(f"签发时间  : {tok.get('obtained_at')}")
            print(f"剩余      : {remain/60:.0f} 分钟" if remain > 0 else f"已过期 {abs(remain)/60:.0f} 分钟")
            print(f"refresh   : {'有' if tok.get('refresh_token') else '无'}")
            return 0

        if "--refresh" in argv:
            refresh_access_token()
            return 0

        cli = McpClient()
        init = cli.initialize()
        print(f"[mcp] 已连接，server={cli.server_info}，session={cli.session_id}")

        if "--tools" in argv:
            res = cli.list_tools()
            tools = (res or {}).get("result", {}).get("tools", [])
            for t in tools:
                print(f"  - {t.get('name')}: {(t.get('description') or '')[:80]}")
            return 0

        if "--accounts" in argv:
            print(json.dumps(unwrap(cli.call_tool("list_wechat_accounts", {})), ensure_ascii=False, indent=2))
            return 0

        if "--themes" in argv:
            print(json.dumps(unwrap(cli.call_tool("list_theme_styles", {})), ensure_ascii=False, indent=2))
            return 0

        if "--call" in argv:
            i = argv.index("--call")
            if i + 1 >= len(argv):
                print("用法：--call <tool> [--args '{...}']")
                return 1
            tool = argv[i + 1]
            args = {}
            if "--args" in argv:
                j = argv.index("--args")
                if j + 1 < len(argv):
                    args = json.loads(argv[j + 1])
            resp = cli.call_tool(tool, args)
            print(json.dumps(unwrap(resp), ensure_ascii=False, indent=2))
            return 0

        print("用法：--status | --refresh | --tools | --accounts | --themes | --call <tool> --args '{...}'")
        return 0
    except Exception as e:
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
