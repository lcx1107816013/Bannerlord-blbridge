#!/usr/bin/env python3
"""Bannerlord Lua 桥接遥控 MOD —— 外置 MCP Server (stdio)。

职责：
  1. 解析 MCP(JSON-RPC) 协议（initialize / tools/list / tools/call）。
  2. 在 AI Agent 与游戏 MOD 之间做文件桥接：
     写 cmd.txt（下发指令）/ 读 state.json（回读状态）。
本服务仅依赖 Python 标准库，零第三方依赖。
"""
import json
import os
import sys
import time

BRIDGE_DIR = os.environ.get("BL_BRIDGE_DIR", ".")
CMD_PATH = os.path.join(BRIDGE_DIR, "cmd.txt")
STA_PATH = os.path.join(BRIDGE_DIR, "state.json")
POLL_TIMEOUT = 10.0     # 等待 MOD 回写 state.json 的最长秒数
POLL_INTERVAL = 0.1     # 轮询间隔


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _read_state():
    try:
        with open(STA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _send_command(cmd):
    """写 cmd.txt 并等待 state.json 更新，返回结果 dict。"""
    try:
        with open(CMD_PATH, "w", encoding="utf-8") as f:
            f.write(json.dumps(cmd, ensure_ascii=False))
    except Exception as e:
        return {"error": f"write cmd failed: {e}"}
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        st = _read_state()
        if st is not None:
            return st
        time.sleep(POLL_INTERVAL)
    return {"error": "timeout waiting for state.json"}


TOOLS = [
    {
        "name": "spawn_troop",
        "description": "在场景中生成一支部队",
        "inputSchema": {
            "type": "object",
            "properties": {
                "partyId": {"type": "string"},
                "troopId": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["partyId", "troopId", "count"],
        },
    },
    {
        "name": "get_hero_stats",
        "description": "读取某英雄的属性/技能",
        "inputSchema": {
            "type": "object",
            "properties": {"heroId": {"type": "string"}},
            "required": ["heroId"],
        },
    },
    {
        "name": "read_state",
        "description": "读取最近一次 state.json",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _dispatch(name, args):
    if name == "spawn_troop":
        return _send_command({"op": "spawn_troop", **args})
    if name == "get_hero_stats":
        return _send_command({"op": "get_hero_stats", **args})
    if name == "read_state":
        st = _read_state()
        return st if st is not None else {"error": "no state yet"}
    return {"error": f"unknown tool: {name}"}


def _handle(msg):
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "bannerlord-lua-mcp", "version": "0.1.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params", {})
        res = _dispatch(params.get("name", ""), params.get("arguments", {}))
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}],
                "isError": "error" in res,
            },
        }
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip().replace("\ufeff", "")  # 去掉可能的 UTF-8 BOM
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        out = _handle(msg)
        if out is not None:
            _send(out)


if __name__ == "__main__":
    main()
