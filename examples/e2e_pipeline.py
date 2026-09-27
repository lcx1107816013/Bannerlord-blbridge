#!/usr/bin/env python3
"""端到端自动化流水线 Demo（BlBridge 工具链联动）。

作用：用一个最小的 MCP stdio 客户端，把参与自动化的各 MCP 服务器
（**本项目两条通道** + 外部**资料库 / 汉化**）按顺序串成一条流水线跑一遍。

组成：
  * McpStdioClient —— 极简 MCP 客户端（initialize + tools/call，仅标准库）。
  * SERVERS       —— 服务器名 -> 启动命令（默认只配好本项目文本通道；其余见 servers.json）。
  * PIPELINE      —— 有序步骤表，每步标注 side_effect（是否改变状态/需要游戏）。

本项目 = BlBridge，含两条通道：
  * 文本通道（本仓库）：`mcp_server/server.py`，server 名 "project"。
  * DLL 通道（随游戏安装）：`<游戏根>/Modules/BlBridge/mcp/bl_mcp.py`，server 名 "blbridge"。

用法：
  python examples/e2e_pipeline.py                 # 默认 --only-safe：只跑无副作用步骤
  python examples/e2e_pipeline.py --all           # 跑全部步骤（会启动游戏/写文件，慎用）
  python examples/e2e_pipeline.py --servers path  # 指定 servers.json 配置
  python examples/e2e_pipeline.py --list          # 只打印流水线步骤

说明：未配置启动命令的服务器，其步骤会被「跳过」而非报错；因此开箱即用（只有本项目文本通道会被真正拉起）。
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_SERVERS = {
    # 本项目 · 文本通道（本仓库的 Python MCP）
    "project": [sys.executable, os.path.join(REPO, "mcp_server", "server.py")],
    # 本项目 · DLL 通道（BlBridge 自带 MCP 子包，随游戏安装；默认按 <游戏根> 定位，未配置则跳过）
    "blbridge": None,
    # 外部服务器（按你的实际挂载方式填写，见 examples/servers.example.json）
    "bannerlordsage": None,
    "bannerlordhelper": None,
}

# (服务器, 工具, 参数, 是否有副作用/是否依赖运行中的游戏)
# 说明：blbridge.* 是本项目「DLL 通道」的工具；project.* 是本项目「文本通道」的工具。
PIPELINE = [
    ("bannerlordsage", "search_bannerlord_knowledge", {"query": "MobileParty"}, False),
    ("bannerlordsage", "bannerlord_doctor", {}, False),
    ("bannerlordsage", "create_mod_workspace", {"workspaceRoot": REPO, "moduleId": "DemoMod"}, True),
    ("bannerlordhelper", "bh_list_local_modules", {}, False),
    ("bannerlordhelper", "bh_translate_module", {"module": "DemoMod", "to": "CNs"}, True),
    ("blbridge", "bl_status", {}, False),
    ("blbridge", "bl_launch_game", {"enterCustomBattle": False}, True),
    ("blbridge", "bl_get_screen", {}, False),
    ("project", "ping", {}, False),
    ("project", "list_ops", {}, False),
    ("project", "get_bridge_info", {}, False),
    ("project", "spawn_troop", {"partyId": "player", "troopId": "empire_infantry5", "count": 20}, True),
]


class McpStdioClient:
    """极简 MCP stdio 客户端：按行读写 JSON-RPC。"""

    def __init__(self, name, cmd):
        self.name = name
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, cwd=REPO,
        )
        self._id = 0
        self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "e2e-pipeline", "version": "1.0.0"},
        })

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError(f"[{self.name}] 服务进程已关闭")
        line = line.strip()
        return json.loads(line) if line else None

    def request(self, method, params=None):
        self._id += 1
        mid = self._id
        self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
        while True:
            msg = self._read()
            if msg is None:
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"[{self.name}] {method} 失败: {msg['error']}")
                return msg.get("result")

    def call(self, tool, args=None):
        res = self.request("tools/call", {"name": tool, "arguments": args or {}})
        content = res.get("content") or []
        text = content[0].get("text") if content else ""
        return {"isError": res.get("isError", False), "text": text}

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.terminate()
        except Exception:
            pass


def load_servers(path):
    servers = dict(DEFAULT_SERVERS)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in cfg.items():
            if k.startswith("_"):
                continue
            servers[k] = v
    return servers


def main():
    args = sys.argv[1:]
    if "--list" in args:
        for i, (srv, tool, a, se) in enumerate(PIPELINE, 1):
            flag = "副作用" if se else "只读"
            print(f"{i:2}. [{flag}] {srv}.{tool} {json.dumps(a, ensure_ascii=False)}")
        return 0

    only_safe = "--all" not in args
    servers_path = None
    if "--servers" in args:
        servers_path = args[args.index("--servers") + 1]
    elif os.path.exists(os.path.join(REPO, "examples", "servers.json")):
        servers_path = os.path.join(REPO, "examples", "servers.json")

    servers = load_servers(servers_path)
    print(f"模式: {'仅安全步骤(--only-safe)' if only_safe else '全部步骤(--all)'}")
    print(f"配置: {servers_path or '（默认，仅本项目文本通道已配置）'}\n")

    clients, needed, results = {}, set(), []
    for srv, _tool, _a, se in PIPELINE:
        if only_safe and se:
            continue
        needed.add(srv)
    for srv in sorted(needed):
        cmd = servers.get(srv)
        if not cmd:
            continue
        try:
            clients[srv] = McpStdioClient(srv, cmd)
            print(f"[启动] {srv}: {cmd}")
        except Exception as e:
            print(f"[启动失败] {srv}: {e}")
    print()

    for i, (srv, tool, a, se) in enumerate(PIPELINE, 1):
        if only_safe and se:
            results.append((i, srv, tool, "skip", "有副作用，安全模式跳过"))
            continue
        if not servers.get(srv):
            results.append((i, srv, tool, "skip", "未配置该服务器启动命令"))
            continue
        if srv not in clients:
            results.append((i, srv, tool, "skip", "服务器未启动"))
            continue
        try:
            r = clients[srv].call(tool, a)
            status = "error" if r["isError"] else "ok"
            results.append((i, srv, tool, status, (r["text"] or "")[:120]))
        except Exception as e:
            results.append((i, srv, tool, "error", str(e)[:120]))

    print("步骤结果：")
    for i, srv, tool, status, detail in results:
        mark = {"ok": "OK  ", "skip": "SKIP", "error": "ERR "}[status]
        print(f"  {i:2}. [{mark}] {srv}.{tool}")
        if detail:
            print(f"        {detail}")

    for c in clients.values():
        c.close()

    errors = [r for r in results if r[3] == "error"]
    print(f"\n完成：ok/skip/error = "
          f"{sum(1 for r in results if r[3]=='ok')}/"
          f"{sum(1 for r in results if r[3]=='skip')}/{len(errors)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
