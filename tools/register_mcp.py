#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 blbridge MCP 服务登记进 CodeBuddy 的 MCP 配置（幂等，可重复运行）。

  python register_mcp.py            # 登记/更新
  python register_mcp.py --remove   # 移除
  python register_mcp.py --show     # 只看当前配置

默认写入 C:\\Users\\<你>\\.codebuddy\\mcp.json，并在写入前备份为 mcp.json.bak_blbridge。
"""
import io
import json
import os
import shutil
import sys

MCP_JSON = os.path.join(os.path.expanduser("~"), ".codebuddy", "mcp.json")
HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_ENTRY = "blbridge"
DEFAULT_GAME_DIR = r"G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord"


def build_entry():
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": [os.path.join(HERE, "bl_mcp.py")],
        "env": {"BANNERLORD_DIR": DEFAULT_GAME_DIR},
        "description": "BlBridge - 骑砍2 战斗遥测：分析战斗日志(血量/伤害模型校验)、回读/修改 Warbandlord 配置（6 个工具）",
    }


def load(path):
    if not os.path.isfile(path):
        return {"mcpServers": {}}
    with io.open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def save(path, data):
    if os.path.isfile(path):
        shutil.copy2(path, path + ".bak_blbridge")
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main(argv):
    data = load(MCP_JSON)
    servers = data.setdefault("mcpServers", {})

    if "--show" in argv:
        print(json.dumps(servers.get(SERVER_ENTRY, {}), ensure_ascii=False, indent=2))
        return 0

    if "--remove" in argv:
        if SERVER_ENTRY in servers:
            del servers[SERVER_ENTRY]
            save(MCP_JSON, data)
            print("removed %s" % SERVER_ENTRY)
        else:
            print("%s is not registered, nothing to remove" % SERVER_ENTRY)
        return 0

    servers[SERVER_ENTRY] = build_entry()
    save(MCP_JSON, data)
    print("registered %s -> %s" % (SERVER_ENTRY, MCP_JSON))
    print(json.dumps(build_entry(), ensure_ascii=False, indent=2))
    print()
    print("note: the MCP server list is normally loaded at IDE startup -- reload the window / restart CodeBuddy for it to take effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
