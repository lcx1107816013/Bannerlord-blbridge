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

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import bl_common  # noqa: E402

MCP_JSON = os.path.join(os.path.expanduser("~"), ".codebuddy", "mcp.json")
SERVER_ENTRY = "blbridge"
DEFAULT_GAME_DIR = r"G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord"


def build_entry():
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": [os.path.join(HERE, "bl_mcp.py")],
        "env": {"BANNERLORD_DIR": DEFAULT_GAME_DIR},
        "description": ("BlBridge - 骑砍2 战斗遥测与 AI 推演桥：战斗日志分析（血量/伤害模型校验）、"
                        "配置回读与改写、AI 对 AI 开战、无人值守启动游戏（BLSE）、"
                        "桌面/游戏 GUI 操作（列窗口 / 截图+网格 / 按格点击 / 按键）"),
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
    bl_common.safe_streams()
    data = load(MCP_JSON)
    servers = data.setdefault("mcpServers", {})

    if "--show" in argv:
        print(json.dumps(servers.get(SERVER_ENTRY, {}), ensure_ascii=False, indent=2))
        return 0

    if "--remove" in argv:
        if SERVER_ENTRY in servers:
            del servers[SERVER_ENTRY]
            save(MCP_JSON, data)
            print("已移除 %s" % SERVER_ENTRY)
        else:
            print("%s 未登记，无需移除" % SERVER_ENTRY)
        return 0

    servers[SERVER_ENTRY] = build_entry()
    save(MCP_JSON, data)
    print("已登记 %s -> %s" % (SERVER_ENTRY, MCP_JSON))
    print(json.dumps(build_entry(), ensure_ascii=False, indent=2))
    print()
    print("注意：MCP 服务列表通常在 IDE 启动时加载，需要重载窗口/重启 CodeBuddy 才会生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
