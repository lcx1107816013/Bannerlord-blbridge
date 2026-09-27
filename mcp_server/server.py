#!/usr/bin/env python3
"""Bannerlord blbridge —— 外置 MCP Server (stdio)。

职责：
  1. 解析 MCP(JSON-RPC) 协议：initialize / ping / tools/list / tools/call。
  2. 在 AI Agent 与游戏内 Lua 桥之间做文件桥接：
     原子写 cmd.txt（下发指令，带自增 seq）/ 轮询读 state.json（按 seq 对齐回执）。

设计要点：
  * 零第三方依赖（仅标准库）；stdout 只用于 JSON-RPC，日志一律走 stderr，
    避免污染 stdio 协议流。
  * cmd.txt 采用「临时文件 + os.replace」原子写，MOD 端不会读到半写内容。
  * 每条指令带自增 seq + 时间戳；Python 只接受 state.json 中 seq 匹配的回执，
    从根本上规避「旧回执 / 竞态 / 重复执行」误判；超时返回结构化错误。
  * 工具分两类：
      - local=True ：Python 本地直接处理（读状态、诊断、编排）。
      - local=False：转发为游戏 op，写入 cmd.txt 等待 MOD 端执行。
"""
import json
import os
import sys
import time

# --------------------------------------------------------------------------
# 配置（均可通过环境变量覆盖）
# --------------------------------------------------------------------------
BRIDGE_DIR = os.environ.get("BL_BRIDGE_DIR", ".")
CMD_PATH = os.path.join(BRIDGE_DIR, "cmd.txt")
STA_PATH = os.path.join(BRIDGE_DIR, "state.json")
POLL_TIMEOUT = float(os.environ.get("BL_POLL_TIMEOUT", "10"))    # 单条指令最长等待秒数
POLL_INTERVAL = float(os.environ.get("BL_POLL_INTERVAL", "0.1"))  # 轮询间隔
SERVER_NAME = "bannerlord-blbridge"
SERVER_VERSION = "0.2.0"
PROTOCOL_VERSION = "2024-11-05"

_seq = 0


def log(*a):
    """日志走 stderr，绝不占用 stdout 的 JSON-RPC 通道。"""
    print("[blbridge]", *a, file=sys.stderr, flush=True)


def _next_seq():
    global _seq
    _seq += 1
    return _seq


# --------------------------------------------------------------------------
# 文件桥
# --------------------------------------------------------------------------
def _atomic_write(path, text):
    """原子写：先写 .tmp 再 os.replace，避免读到半写文件。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_state():
    try:
        with open(STA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _send_command(op, args, timeout=None):
    """下发一条 op 到 cmd.txt，并等待 seq 匹配的回执。"""
    seq = _next_seq()
    cmd = {"op": op, "seq": seq, "ts": round(time.time(), 3)}
    if isinstance(args, dict):
        cmd.update(args)
    try:
        _atomic_write(CMD_PATH, json.dumps(cmd, ensure_ascii=False))
    except Exception as e:
        return {"error": "write_cmd_failed", "detail": str(e), "cmd_path": CMD_PATH}

    deadline = time.time() + (timeout or POLL_TIMEOUT)
    while time.time() < deadline:
        st = _read_state()
        if st is not None and st.get("seq") == seq:
            return st
        time.sleep(POLL_INTERVAL)
    return {
        "error": "timeout",
        "seq": seq,
        "op": op,
        "hint": "未收到匹配 seq 的回执：确认游戏在运行、MOD 已启用、BL_BRIDGE_DIR 指向正确目录。",
    }


def _file_info(path):
    try:
        s = os.stat(path)
        return {"exists": True, "size": s.st_size, "mtime": round(s.st_mtime, 3)}
    except Exception:
        return {"exists": False}


# --------------------------------------------------------------------------
# 工具注册表：(name, description, params, required, timeout, local)
#   params  : {参数名: JSON 类型}
#   required: 必填参数名列表
#   timeout : 覆盖默认 POLL_TIMEOUT（秒），仅游戏工具有效
#   local   : True 表示 Python 本地处理
# --------------------------------------------------------------------------
ENTRIES = [
    # ---------------- 本地工具：诊断 / 编排 ----------------
    ("ping", "健康检查：返回桥接端版本、桥接目录与文件时间戳。", None, None, None, True),
    ("get_bridge_info", "获取桥接配置与状态文件信息（路径、更新时间、最近回执）。", None, None, None, True),
    ("list_ops", "列出 MOD 端支持的全部游戏操作 op（协议白名单）。", None, None, None, True),
    ("get_protocol", "获取 cmd.txt / state.json 文件通信协议说明。", None, None, None, True),
    ("read_state", "读取最近一次 state.json（不等待）。", None, None, None, True),
    ("wait_for_state", "等待新的回执：可按 seq 精确等待，或等待任意更晚的回执。",
     {"seq": "integer", "timeout": "number"}, None, None, True),
    ("clear_state", "删除 state.json，用于重置回执（不影响游戏）。", None, None, None, True),
    ("assert_state", "断言最近回执的字段值，用于自动化测试。",
     {"key": "string", "equals": "string"}, ["key"], None, True),
    ("run_ops", "批量顺序执行多个游戏 op（每步等待回执），返回逐步结果。",
     {"ops": "array"}, ["ops"], None, True),
    ("send_raw", "高级：直接下发原始 op（绕过工具封装，供协议调试）。",
     {"op": "string", "args": "object"}, ["op"], None, True),
    ("sleep", "客户端本地休眠指定秒数，用于编排测试节奏。",
     {"seconds": "number"}, ["seconds"], None, True),

    # ---------------- 游戏工具：主角 / 英雄 ----------------
    ("get_player", "读取主角信息（等级、金币、家族、王国等）。", None, None, None, False),
    ("set_player_gold", "设置主角金币（测试用，会改变存档）。",
     {"amount": "integer"}, ["amount"], None, False),
    ("add_player_gold", "增减主角金币（可负数）。",
     {"amount": "integer"}, ["amount"], None, False),
    ("get_player_inventory", "读取指定英雄的背包物品列表。",
     {"heroId": "string"}, ["heroId"], None, False),
    ("add_item", "向指定英雄背包添加物品。",
     {"heroId": "string", "itemId": "string", "count": "integer"}, ["heroId", "itemId", "count"], None, False),
    ("get_hero_stats", "读取某英雄的属性/技能。", {"heroId": "string"}, ["heroId"], None, False),
    ("list_heroes", "列出当前世界中已知英雄。", None, None, None, False),
    ("set_hero_attr", "设置英雄属性/技能值（测试用）。",
     {"heroId": "string", "attr": "string", "value": "number"}, ["heroId", "attr", "value"], None, False),
    ("heal_hero", "治愈英雄的全部伤势。", {"heroId": "string"}, ["heroId"], None, False),
    ("wound_hero", "使英雄受伤（测试用）。", {"heroId": "string"}, ["heroId"], None, False),

    # ---------------- 游戏工具：部队 party ----------------
    ("spawn_troop", "在场景中生成一支部队。",
     {"partyId": "string", "troopId": "string", "count": "integer"}, ["partyId", "troopId", "count"], None, False),
    ("list_parties", "列出地图上的部队（可按阵营过滤）。",
     {"factionId": "string"}, None, None, False),
    ("get_party", "读取指定部队的详情（成员、位置、士气等）。",
     {"partyId": "string"}, ["partyId"], None, False),
    ("add_troops", "向部队添加士兵。",
     {"partyId": "string", "troopId": "string", "count": "integer"}, ["partyId", "troopId", "count"], None, False),
    ("remove_troops", "从部队移除士兵。",
     {"partyId": "string", "troopId": "string", "count": "integer"}, ["partyId", "troopId", "count"], None, False),
    ("disband_party", "解散指定部队。", {"partyId": "string"}, ["partyId"], None, False),
    ("teleport_party", "把部队移动到指定定居点。",
     {"partyId": "string", "settlementId": "string"}, ["partyId", "settlementId"], None, False),
    ("merge_parties", "把源部队并入目标部队。",
     {"srcPartyId": "string", "dstPartyId": "string"}, ["srcPartyId", "dstPartyId"], None, False),
    ("start_battle", "让两支部队开战。",
     {"attackerPartyId": "string", "defenderPartyId": "string"}, ["attackerPartyId", "defenderPartyId"], None, False),
    ("auto_resolve_battle", "自动结算当前战斗（避免进入场景）。",
     {"partyId": "string"}, ["partyId"], None, False),
    ("get_visible_parties", "列出当前可见的敌方/中立部队。", None, None, None, False),
    ("get_map_entities", "读取地图实体（部队/定居点/据点），可限定半径。",
     {"radius": "number"}, None, None, False),

    # ---------------- 游戏工具：定居点 ----------------
    ("list_settlements", "列出所有定居点（城镇/城堡/村庄）。", None, None, None, False),
    ("get_settlement", "读取指定定居点详情（归属、繁荣度、驻军等）。",
     {"settlementId": "string"}, ["settlementId"], None, False),
    ("set_settlement_owner", "设置定居点归属家族（测试用）。",
     {"settlementId": "string", "clanId": "string"}, ["settlementId", "clanId"], None, False),

    # ---------------- 游戏工具：王国 / 家族 / 外交 ----------------
    ("list_kingdoms", "列出所有王国及其势力信息。", None, None, None, False),
    ("list_clans", "列出所有家族及其从属王国。", None, None, None, False),
    ("declare_war", "宣布两个王国进入战争。",
     {"kingdomAId": "string", "kingdomBId": "string"}, ["kingdomAId", "kingdomBId"], None, False),
    ("make_peace", "促成两个王国停战议和。",
     {"kingdomAId": "string", "kingdomBId": "string"}, ["kingdomAId", "kingdomBId"], None, False),
    ("get_relations", "读取两个阵营之间的关系值。",
     {"factionAId": "string", "factionBId": "string"}, ["factionAId", "factionBId"], None, False),

    # ---------------- 游戏工具：世界 / 时间 ----------------
    ("get_campaign_time", "读取战役内时间（年/季/日/时）。", None, None, None, False),
    ("get_time_scale", "读取当前游戏时间倍速。", None, None, None, False),
    ("set_time_scale", "设置游戏时间倍速。", {"scale": "number"}, ["scale"], None, False),
    ("fast_forward", "快进指定天数（测试常用）。", {"days": "number"}, ["days"], None, False),
    ("pause_game", "暂停游戏主循环。", None, None, None, False),
    ("resume_game", "恢复游戏主循环。", None, None, None, False),
    ("get_weather", "读取当前战场/地图天气。", None, None, None, False),
    ("set_weather", "设置天气（测试用）。", {"weather": "string"}, ["weather"], None, False),

    # ---------------- 游戏工具：任务 / 日志 / 存档 ----------------
    ("list_quests", "列出当前进行中的任务。", None, None, None, False),
    ("add_quest", "为指定英雄添加一个任务。",
     {"questId": "string", "giverHeroId": "string"}, ["questId", "giverHeroId"], None, False),
    ("complete_quest", "完成指定任务。", {"questId": "string"}, ["questId"], None, False),
    ("get_missions", "读取当前进行中的战斗/场景任务（mission）。", None, None, None, False),
    ("get_campaign_log", "读取战役日志（最近事件）。",
     {"limit": "integer"}, None, None, False),
    ("save_game", "保存当前游戏存档（会写入存档文件）。", {"name": "string"}, ["name"], None, False),
    ("load_game", "读取指定存档（危险：会中断当前进度）。", {"name": "string"}, ["name"], None, False),

    # ---------------- 游戏工具：测试辅助 ----------------
    ("take_snapshot", "生成当前世界状态的快照，用于测试断言。",
     {"tags": "array"}, None, None, False),
]


def _make_tool(name, desc, params, required):
    props = {k: {"type": v} for k, v in (params or {}).items()}
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return {"name": name, "description": desc, "inputSchema": schema}


TOOLS = [_make_tool(n, d, p, r) for (n, d, p, r, _t, _l) in ENTRIES]
TOOL_INDEX = {n: {"desc": d, "params": p or {}, "required": r or [], "timeout": t, "local": l}
              for (n, d, p, r, t, l) in ENTRIES}
GAME_OPS = [n for (n, d, p, r, t, l) in ENTRIES if not l]


def _protocol_doc():
    return (
        "cmd.txt   : 单行 JSON，一条指令 = 一次下发。字段：\n"
        "            op(操作名) / seq(自增序号) / ts(时间戳) / 其余为参数。\n"
        "state.json: 单行 JSON，MOD 端执行后的回执。字段：\n"
        "            seq(与指令一致) / 其余为结果或 err(错误码)。\n"
        "对齐规则  : Python 只接受 seq 与本次下发一致的 state.json；\n"
        "            故需保证 MOD 端把 cmd.seq 原样回写到 state.json。\n"
        "错误码    : bad_cmd(指令无法解析) / unknown_op(未知操作) / timeout(超时)。\n"
        "安全约束  : cmd.txt 原子写；MOD 端禁止在主线程做阻塞 IO；\n"
        "            指令经受限环境加载，禁止执行 IO/OS 调用。"
    )


# --------------------------------------------------------------------------
# 本地工具实现
# --------------------------------------------------------------------------
def _local(tool, args):
    if tool == "ping":
        return {"ok": True, "server": SERVER_NAME, "version": SERVER_VERSION, "bridge_dir": BRIDGE_DIR}
    if tool == "get_bridge_info":
        return {"bridge_dir": BRIDGE_DIR, "cmd": _file_info(CMD_PATH),
                "state": _file_info(STA_PATH), "last_state": _read_state(),
                "poll_timeout": POLL_TIMEOUT, "poll_interval": POLL_INTERVAL}
    if tool == "list_ops":
        return {"count": len(GAME_OPS), "ops": GAME_OPS}
    if tool == "get_protocol":
        return {"protocol": _protocol_doc()}
    if tool == "read_state":
        st = _read_state()
        return st if st is not None else {"error": "no_state", "state_path": STA_PATH}
    if tool == "wait_for_state":
        target = args.get("seq")
        timeout = args.get("timeout") or POLL_TIMEOUT
        last_seq = (_read_state() or {}).get("seq", 0)
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = _read_state()
            if st is not None:
                if target is not None:
                    if st.get("seq") == target:
                        return st
                elif st.get("seq", 0) > last_seq:
                    return st
            time.sleep(POLL_INTERVAL)
        return {"error": "timeout", "waited_for": target, "timeout": timeout}
    if tool == "clear_state":
        try:
            os.remove(STA_PATH)
            return {"ok": True, "removed": STA_PATH}
        except FileNotFoundError:
            return {"ok": True, "note": "state.json 不存在，无需删除"}
        except Exception as e:
            return {"error": "remove_failed", "detail": str(e)}
    if tool == "assert_state":
        st = _read_state()
        if st is None:
            return {"ok": False, "reason": "no_state"}
        actual = st.get(args["key"])
        expected = args.get("equals")
        ok = str(actual) == str(expected) if expected is not None else args["key"] in st
        return {"ok": ok, "key": args["key"], "expected": expected, "actual": actual}
    if tool == "run_ops":
        results = []
        for item in args.get("ops", []) or []:
            name = item.get("name") if isinstance(item, dict) else None
            iargs = (item.get("arguments") if isinstance(item, dict) else None) or {}
            if not name or name not in TOOL_INDEX:
                results.append({"name": name, "error": "unknown_tool"})
                continue
            meta = TOOL_INDEX[name]
            if meta["local"]:
                results.append({"name": name, "result": _local(name, iargs)})
            else:
                results.append({"name": name, "result": _send_command(name, iargs, meta["timeout"])})
        return {"count": len(results), "results": results}
    if tool == "send_raw":
        return _send_command(args["op"], args.get("args") or {})
    if tool == "sleep":
        secs = float(args.get("seconds") or 0)
        time.sleep(max(0.0, secs))
        return {"ok": True, "slept": secs}
    return {"error": "not_local_tool"}


def _dispatch(name, args):
    meta = TOOL_INDEX.get(name)
    if meta is None:
        return {"error": "unknown_tool", "tool": name}

    # 必填参数校验
    missing = [k for k in meta["required"] if k not in (args or {})]
    if missing:
        return {"error": "missing_params", "missing": missing, "tool": name}

    if meta["local"]:
        return _local(name, args or {})
    return _send_command(name, args or {}, meta["timeout"])


# --------------------------------------------------------------------------
# MCP / JSON-RPC 处理
# --------------------------------------------------------------------------
def _rpc_result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _rpc_error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _handle(msg):
    if not isinstance(msg, dict):
        return _rpc_error(None, -32600, "invalid request")
    mid = msg.get("id")
    method = msg.get("method")
    is_notification = "id" not in msg and method is not None

    if method == "initialize":
        return _rpc_result(mid, {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _rpc_result(mid, {})
    if method == "tools/list":
        return _rpc_result(mid, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params", {}) or {}
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        res = _dispatch(name, args)
        return _rpc_result(mid, {
            "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}],
            "isError": isinstance(res, dict) and "error" in res,
        })
    if is_notification:
        return None
    return _rpc_error(mid, -32601, f"method not found: {method}")


def main():
    log(f"started {SERVER_NAME} v{SERVER_VERSION}; bridge_dir={BRIDGE_DIR}")
    for line in sys.stdin:
        line = line.strip().replace("\ufeff", "")
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            _send_error_parse()
            continue
        try:
            out = _handle(msg)
        except Exception as e:
            out = _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32603, f"internal error: {e}")
        if out is not None:
            _send(out)
    log("stdin closed, exit")


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _send_error_parse():
    _send(_rpc_error(None, -32700, "parse error"))


if __name__ == "__main__":
    main()
