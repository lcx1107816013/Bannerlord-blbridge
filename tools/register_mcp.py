#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 blbridge MCP 服务登记进 CodeBuddy 的 MCP 配置（幂等，可重复运行）。

  python register_mcp.py            # 登记/更新
  python register_mcp.py --remove   # 移除
  python register_mcp.py --show     # 只看当前配置

默认写入 C:\\Users\\<你>\\.codebuddy\\mcp.json，并在写入前备份为 mcp.json.bak_blbridge。

**自定位**（v0.8.12 起，双分包需要）：本脚本随 mod 一起部署到
`<游戏根>\\Modules\\BlBridge\\mcp\\register_mcp.py`，这时它从自身路径上溯三级得到游戏根目录，
因此**不需要用户手工填路径**；从仓库 `tools\\` 目录运行时则回退到内置默认值。
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
FALLBACK_GAME_DIR = r"G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord"


def _looks_like_game_root(path):
    """判据与引擎一致：游戏根目录里必须有 Modules\\ 与 bin\\Win64_Shipping_Client\\。

    两个条件都要（照 AGENTS.md §四 的纪律：判据要么两边都验，要么别宣称）：
    只查 Modules\\ 会把"仓库里某个叫 Modules 的目录"也认成游戏根。
    """
    if not path or not os.path.isdir(path):
        return False
    return (os.path.isdir(os.path.join(path, "Modules"))
            and os.path.isdir(os.path.join(path, "bin", "Win64_Shipping_Client")))


def derive_game_dir():
    """从自身位置推导游戏根目录；推导不出来返回 None。

    部署形态：<Game>\\Modules\\BlBridge\\mcp\\register_mcp.py
      dirname(HERE)      = <Game>\\Modules\\BlBridge
      dirname^2(HERE)    = <Game>\\Modules
      dirname^3(HERE)    = <Game>            <- 目标
    同时要求 <Game>\\Modules\\BlBridge 下确实有 SubModule.xml（证明确实是"我们被部署到了 mod 里"，
    而不是某个碰巧同名的目录结构）。
    """
    module_dir = os.path.dirname(HERE)
    modules_dir = os.path.dirname(module_dir)
    game_dir = os.path.dirname(modules_dir)
    if os.path.basename(HERE).lower() != "mcp":
        return None
    if os.path.basename(module_dir).lower() != "blbridge":
        return None
    if not os.path.isfile(os.path.join(module_dir, "SubModule.xml")):
        return None
    if not _looks_like_game_root(game_dir):
        return None
    return game_dir


def resolve_game_dir():
    """优先级：环境变量 > 自定位 > 内置默认值（与 bl_mcp 侧口径一致）。"""
    env = os.environ.get("BANNERLORD_DIR")
    if env:
        return env, "env:BANNERLORD_DIR"
    derived = derive_game_dir()
    if derived:
        return derived, "self:deployed-in-module"
    return FALLBACK_GAME_DIR, "fallback:builtin"


def build_entry(game_dir, game_dir_source):
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": [os.path.join(HERE, "bl_mcp.py")],
        "env": {"BANNERLORD_DIR": game_dir},
        "description": ("BlBridge - 骑砍2 战斗遥测与 AI 推演桥：战斗日志分析（血量/伤害模型校验）、"
                        "配置回读与改写、AI 对 AI 开战（含游戏内面板入口 open_ui）、无人值守启动游戏（BLSE）、"
                        "桌面/游戏 GUI 操作（列窗口 / 截图+网格 / 按格点击 / 按键）"),
        "_gameDirSource": game_dir_source,
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
    game_dir, source = resolve_game_dir()
    data = load(MCP_JSON)
    servers = data.setdefault("mcpServers", {})

    if "--show" in argv:
        entries = {"gameDir": game_dir, "gameDirSource": source,
                   "server": servers.get(SERVER_ENTRY, {})}
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0

    if "--remove" in argv:
        if SERVER_ENTRY in servers:
            del servers[SERVER_ENTRY]
            save(MCP_JSON, data)
            print("已移除 %s" % SERVER_ENTRY)
        else:
            print("%s 未登记，无需移除" % SERVER_ENTRY)
        return 0

    servers[SERVER_ENTRY] = build_entry(game_dir, source)
    save(MCP_JSON, data)
    print("已登记 %s -> %s" % (SERVER_ENTRY, MCP_JSON))
    print(json.dumps(build_entry(game_dir, source), ensure_ascii=False, indent=2))
    print()
    print("注意：MCP 服务列表通常在 IDE 启动时加载，需要重载窗口/重启 CodeBuddy 才会生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
