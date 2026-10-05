#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BlBridge MCP server（stdio, 仅标准库, 不需要任何 pip 包）。

把 BlBridge 遥测 Mod 的产物（JSONL 战斗日志 + bridge_status.json）
以及 Warbandlord 的 config.xml 暴露成 MCP 工具，供外部 Agent 调用：

  bl_status        模块/日志状态
  bl_list_battles  列出战斗日志
  bl_analyze       分析某场战斗（血量/伤害模型校验）
  bl_read_events   读取原始事件（可过滤/限量）
  bl_read_config   回读 Warbandlord 配置
  bl_apply_config  修改 Warbandlord 配置（自动备份 + XML 校验）

在 MCP 配置里这样接（示例）：
  "blbridge": {
    "command": "python",
    "args": ["c:\\\\Users\\\\LCGX\\\\CodeBuddy\\\\20260923171333\\\\BlBridge\\\\tools\\\\bl_mcp.py"]
  }

环境变量（可选）：
  BLBRIDGE_LOG_DIR   遥测日志目录（默认 我的文档\\Mount and Blade II Bannerlord\\BlBridge）
  BANNERLORD_DIR     游戏根目录（用于定位 Warbandlord/config.xml）
"""
import io
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_analyze  # noqa: E402
import bl_common  # noqa: E402
import bl_rts  # noqa: E402
import bl_sage  # noqa: E402

# bl_blockade 是**软依赖**：它自己只用标准库，但拓扑/通路文件可能在别的机器上没有。
# 导入失败不能让整个 MCP server 起不来 —— 所以失败时留 None，调用时如实报 unavailable。
try:
    import bl_blockade  # noqa: E402
except Exception as _e:                                  # pragma: no cover
    bl_blockade = None
    _BLOCKADE_IMPORT_ERR = "%s: %s" % (type(_e).__name__, _e)
else:
    _BLOCKADE_IMPORT_ERR = ""

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "blbridge"
SERVER_VERSION = "0.1.0"

DEFAULT_GAME_DIR = r"G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord"

# ── 控制通道（文件 IPC，协议 v1）────────────────────────────────────────
# 规范抄自 Bannerlord Coop 的 LiveTestProtocol：
#   信封 {protocolVersion,id,method,parameters} / {protocolVersion,id,ok,process,result,error}
#   不变式 ok==true ⇔ error==null；process 必填；版本不匹配直接拒绝；outcomeUncertain 时绝不盲目重试
# 注意：不要复用上面的 PROTOCOL_VERSION（那是 MCP 自身的协议版本）
BRIDGE_PROTOCOL_VERSION = 1


def _commands_root(log_dir=None):
    return os.path.join(log_dir or log_dir_default(), "commands")


def log_dir_default():
    return os.environ.get("BLBRIDGE_LOG_DIR") or bl_analyze.default_log_dir()


def _pending_dir(log_dir=None):
    return os.path.join(_commands_root(log_dir), "pending")


def _done_dir(log_dir=None):
    return os.path.join(_commands_root(log_dir), "done")


def _game_session(path=None):
    """读取游戏侧会话身份（runToken / pid / processStartedUtc），用于拒绝过期会话的响应。

    path 可显式指定（测试/多日志目录场景）；缺省用当前生效的 status_path()。
    """
    p = path or status_path()
    if not os.path.isfile(p):
        return None
    try:
        with io.open(p, "r", encoding="utf-8-sig", errors="replace") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def _pid_alive(pid):
    """判断进程是否存活。返回 True / False / None。

    None 表示**探测失败 = 未知**，绝不把未知当成"活着"或"死了"
    （照 Coop 的 ownership_probe_failed 语义：null tree observation is unknown, never confirmation）。
    """
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % n, "/NH", "/FO", "CSV"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=8).stdout.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    return ('"%d"' % n) in out


def _pid_image_name(pid):
    """取该 pid 的进程映像名（如 `Bannerlord.BLSE.Standalone.exe`）。

    查不到这个 pid、或输出解析不出名字 ⇒ 返回 None（**未知**，绝不猜）。
    """
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % n, "/NH", "/FO", "CSV"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=8).stdout.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    for line in out.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("INFO"):
            continue
        name = line.split(",")[0].strip().strip('"')
        # 只认"确实查到了这一行"：CSV 首列就是映像名，空行/提示行一律不算
        if name and name.lower() not in ("image name",):
            return name
    return None


# 进程名的判据：官方启动器、BLSE 三个入口、原生辅助进程都带 bannerlord / taleworlds：
#   Bannerlord.exe / Bannerlord.Native.exe / Bannerlord.BLSE.Launcher.exe /
#   Bannerlord.BLSE.Standalone.exe / TaleWorlds.MountAndBlade.Launcher.exe
GAME_IMAGE_RE = re.compile(r"bannerlord|mountandblade|taleworlds", re.I)


def _pid_is_game(pid):
    """判断"游戏进程在不在"。True / False / None（None = 未知）。

    与 `_pid_alive`（只问"这个 pid 上有进程吗"）的区别是**要验身份**：
    pid 会被系统回收再用 —— 2026-09-27 实测：状态文件里的 pid 6040 已经变成
    `MSI_Central_Service.exe`，只查存在性的话 `bl_status` 会报
    `verdict: running / 游戏进程存活`，而游戏根本没开 ⇒ 直接把调用方（AI）带偏。
    """
    exists = _pid_alive(pid)
    if exists is not True:
        return exists                       # False（pid 上没进程）/ None（探测失败）
    name = _pid_image_name(pid)
    if name is None:
        return None                         # 存在但认不出名字 —— 未知，不冒充"是游戏"
    return bool(GAME_IMAGE_RE.search(name))


def _drop_pending(path):
    """作废一个还没被执行的请求文件。

    这是"幽灵战斗"的防线：MCP 超时退出后，如果请求文件还留在 pending/，
    游戏**之后**启动会把旧请求执行掉（凭空冒出一场没要的战斗）。
    """
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception:  # noqa: BLE001
        pass


def _diag_no_response(timeout, log_dir=None):
    """超时归因：把「游戏没起来 / 桥没加载 / 进程退了 / 在跑但不响应」分开。

    照 Coop 的 `state: started ≠ ready` 与 `reached / process_exited / deadline_expired` 三值语义，
    避免把四种完全不同的故障塞进一句"等待超时"里。
    返回 (code, message)。
    """
    st = os.path.join(log_dir, "bridge_status.json") if log_dir else status_path()
    if not os.path.isfile(st):
        return "bridge_not_loaded", ("等待游戏响应超时（%.0fs）：未找到 %s —— 游戏没启动，或 BlBridge 模块未启用"
                                     % (timeout, st))
    sess = _game_session(st) or {}      # 与上面同一个路径，避免参数只生效一半
    pid = sess.get("pid")
    alive = _pid_is_game(pid)           # 验身份：pid 被复用时不能说"游戏还在"
    if alive is False:
        return "process_exited", ("等待游戏响应超时（%.0fs）：游戏进程（pid %s）已退出 —— 该请求不会被执行"
                                  % (timeout, pid))
    if alive is None:
        return "probe_unknown", ("等待游戏响应超时（%.0fs）：探测进程存活失败（未知），不做判断 —— 请人工确认游戏是否卡住"
                                 % timeout)
    return "no_response", ("等待游戏响应超时（%.0fs）：游戏进程（pid %s）仍在运行但未响应 —— 可能主线程卡住"
                           "（加载/存档中）、模块 Enabled=false，或运行的是未重启的旧版本模块" % (timeout, pid))


_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_TOOLS_DIR)

# ── C24：可选配置文件（加载即校验，优先级 环境变量 > 配置文件 > 默认值）─────
CONFIG_FILE = "blbridge.json"
_CONFIG_KEYS = ("logDir", "gameDir", "waitForStateTimeoutSec", "statusTimeoutSec", "maxRequestAgeSec")
_CONFIG_RANGES = {"waitForStateTimeoutSec": (1, 3600), "statusTimeoutSec": (1, 120),
                  "maxRequestAgeSec": (5, 3600)}
_config_cache = None


def config_path():
    return os.path.join(_PROJECT_DIR, CONFIG_FILE)


def load_config(path=None, force=False):
    """读项目根的可选 blbridge.json。返回 (cfg, problems)。

    设计取舍（照 Coop 的"配置即校验 + 来历可查"）：
      • 越界/类型错的项**逐项忽略并说明原因**，不让整份配置失效（和游戏端 BridgeConfigFile 一致）；
      • problems 里既有错误也有"用了默认值"的提示 —— 避免"我明明改了但没生效"这种无法定位的状态；
      • 读一次即缓存；测试可传 path/force。
    """
    global _config_cache
    p = path or config_path()
    if _config_cache is not None and not force and _config_cache.get("path") == p:
        return _config_cache["cfg"], _config_cache["problems"]

    cfg, problems = {}, []
    if not os.path.isfile(p):
        problems.append("未找到配置文件 %s —— 全部使用默认值（不影响使用）" % p)
        _config_cache = {"path": p, "cfg": cfg, "problems": problems}
        return cfg, problems

    try:
        with io.open(p, "r", encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        problems.append("解析失败（整份忽略）：%r" % exc)
        _config_cache = {"path": p, "cfg": cfg, "problems": problems}
        return cfg, problems

    if not isinstance(raw, dict):
        problems.append("配置根必须是 JSON 对象（整份忽略）")
        _config_cache = {"path": p, "cfg": cfg, "problems": problems}
        return cfg, problems

    for key in ("logDir", "gameDir"):
        v = raw.get(key)
        if v is None:
            continue
        if not isinstance(v, str) or not v.strip():
            problems.append("%s 必须是非空字符串，已忽略" % key)
            continue
        if not os.path.isabs(v):
            problems.append("%s 必须是绝对路径，已忽略：%s" % (key, v))
            continue
        if not os.path.isdir(v):
            problems.append("%s 指向的目录当前不存在（仍会采用）：%s" % (key, v))
        cfg[key] = v

    for key, (lo, hi) in _CONFIG_RANGES.items():
        v = raw.get(key)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            problems.append("%s 必须是数字，已忽略" % key)
            continue
        if not (lo <= v <= hi):
            problems.append("%s 越界（应在 %s~%s），已忽略" % (key, lo, hi))
            continue
        cfg[key] = v

    unknown = sorted(k for k in raw if k not in _CONFIG_KEYS)
    if unknown:
        problems.append("未知键被忽略：%s" % ", ".join(unknown))

    _config_cache = {"path": p, "cfg": cfg, "problems": problems}
    return cfg, problems


def _cfg(key, fallback):
    cfg, _ = load_config()
    v = cfg.get(key)
    return v if v is not None else fallback


_TERMINAL_STATES = ("idle", "loaded", "ended", "stopped", "exited")


def run_state_diagnosis(log_dir_path=None, pid_probe=None):
    """A9：判定"上次会话是正常结束、还活着，还是崩了/被强杀"。

    `pid_probe` 只给测试用（注入一个"是不是游戏进程"的判定），缺省用真的 `_pid_is_game`。

    依据（缺一不可，缺了就诚实说"不知道"）：
      • 进程是否存活且**确实是游戏**（tasklist + 映像名，探测失败 = 未知）；
      • 状态文件里的 `cleanExit`：只在引擎的 `OnSubModuleUnloaded` 里才会变成 true；
      • `missionInProgress` / 状态是否停在 battle：判断崩溃是否发生在战斗中（顺带指出战斗文件）。
    """
    st_path = os.path.join(log_dir_path, "bridge_status.json") if log_dir_path else status_path()
    out = {"verdict": "unknown", "detail": "", "statusPath": st_path}
    if not os.path.isfile(st_path):
        out["verdict"] = "no_session"
        out["detail"] = "没有状态文件 —— 本会话从未加载过模块"
        return out
    try:
        out["statusAgeSec"] = round(time.time() - os.path.getmtime(st_path), 1)
    except OSError:
        pass

    # 必须读**同一个**路径：只看 st_path 是否存在、却从全局路径读会话，
    # 会让这个函数的 log_dir_path 参数只生效一半（曾因此在测试里给出错误结论）
    sess = _game_session(st_path) or {}
    pid, state = sess.get("pid"), sess.get("state")
    alive = (pid_probe or _pid_is_game)(pid)   # 验身份：pid 被别的进程复用时不算游戏活着
    out.update({"pid": pid, "lastState": state, "alive": alive,
                "pidImageName": _pid_image_name(pid),
                "cleanExit": sess.get("cleanExit"),
                "missionInProgress": sess.get("missionInProgress"),
                "lastBattle": sess.get("lastBattle")})

    if alive is True:
        out["verdict"] = "running"
        out["detail"] = "游戏进程存活（%s，最后状态 %s）" % (out.get("pidImageName"), state)
        if (out.get("statusAgeSec") or 0) > 120:
            # 不新增 verdict（`running` 的消费方按字面判等），只在文案里把"卡住"的可能性说出来
            out["staleStatus"] = True
            out["detail"] += ("；但状态文件已 %.0f 秒没更新 —— 进程在，可能已不在主循环"
                              "（卡住/后台/崩溃后僵死）" % out["statusAgeSec"])
        return out
    if alive is None:
        out["verdict"] = "unknown"
        out["detail"] = "进程存活探测失败（未知），不做判断"
        return out
    if "cleanExit" not in sess:
        out["verdict"] = "undetermined"
        out["detail"] = ("状态文件来自旧版模块（没有 cleanExit 标记）—— 无法区分正常退出与崩溃；"
                         "重启游戏加载新版模块后本功能生效")
        return out
    if sess.get("cleanExit") is True:
        out["verdict"] = "clean_exit"
        out["detail"] = "进程已退出，且记录了正常卸载（最后状态 %s）" % state
        return out
    if sess.get("missionInProgress") is True or state == "battle":
        out["verdict"] = "crashed_in_battle"
        out["detail"] = ("进程已退出，但**没有正常卸载记录**且退出时正在战斗中 —— 疑似崩溃/被强杀。"
                         "崩溃现场战斗文件：%s" % (sess.get("lastBattle") or "(未记录)"))
        return out
    out["verdict"] = "crashed"
    out["detail"] = ("进程已退出，但没有正常卸载记录（最后状态 %s）—— 疑似崩溃或被强制关闭"
                     "（任务管理器/掉电）" % state)
    return out


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    try:
        with io.open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return ""


def source_dir():
    return os.path.join(_PROJECT_DIR, "src")


def module_dir():
    return os.path.join(game_dir(), "Modules", "BlBridge")


def deployed_dll_path():
    return os.path.join(module_dir(), "bin", "Win64_Shipping_Client", "BlBridge.dll")


def build_manifest_path():
    return os.path.join(module_dir(), "build_manifest.json")


def build_check(src_dir=None, mod_dir=None):
    """构建一致性判定：把「源码 → 构建产物 → 部署文件 → 进程内 DLL」四段串起来核验。

    （对照 Coop 的 preflight 读 AssemblyMetadata/MVID 判定构建一致性；我们用 SHA256 串全链条，
      因为我们的部署物就是 2 个文件，哈希比 MVID 更直接。）返回 dict，code 取值：

      ok                        四段一致
      no_deployed_dll           模块目录里没有 DLL
      manifest_missing          没有 build_manifest.json（旧版部署）
      manifest_invalid          清单读不了
      stale_deploy              模块里的 DLL ≠ 上次构建产物（构建了没部署）
      stale_source              源码变了但没重新构建
      game_not_restarted        进程内是更早的 DLL（磁盘已更新）→ 必须重启游戏
      game_running_other_build  进程内 DLL 与磁盘上的不同 → 部署路径存疑
      game_offline              游戏没在运行，无法核对进程内身份（文件链条可能仍然一致）

    v0.8.12：本函数从**部署副本**（<游戏根>\\Modules\\BlBridge\\mcp\\bl_mcp.py）跑时，
    旁边没有 src/，源码段会跳过并在 `sourceCheck` 里如实标注（不再报一个不存在的 srcDir）。
    """
    src = src_dir or source_dir()
    mod = mod_dir or module_dir()
    dll = os.path.join(mod, "bin", "Win64_Shipping_Client", "BlBridge.dll")
    mf = os.path.join(mod, "build_manifest.json")

    out = {"code": "ok", "detail": "", "moduleDir": mod,
           "deployedDll": dll, "manifestPath": mf}
    has_src = os.path.isdir(src)
    if has_src:
        out["srcDir"] = src
    else:
        # v0.8.12 双分包：部署副本（<游戏根>\Modules\BlBridge\mcp\bl_mcp.py）旁边没有 src/。
        # 如实标注并跳过源码段，而不是把 <module>\src 这个**不存在的目录**当成源码目录报出去
        # （报告里出现一个查无此处的路径，会让整个结论没法信 —— 见 AGENTS.md §四）。
        out["sourceCheck"] = "skipped_no_src_dir"
        out["sourceCheckNote"] = ("本副本旁边没有 src/（部署副本）：源码那一段跳过，"
                                  "只核对「构建产物 = 部署文件 = 进程内 DLL」；"
                                  "要核源码链请在仓库 tools\\ 下跑同一命令")

    if not os.path.isfile(dll):
        out["code"] = "no_deployed_dll"
        out["detail"] = "模块目录里没有 DLL：%s" % dll
        return out
    deployed = _sha256_file(dll)
    out["deployedSha256"] = deployed[:16]

    if not os.path.isfile(mf):
        out["code"] = "manifest_missing"
        out["detail"] = "模块目录里没有构建清单（可能是旧版部署）—— 重跑 build.ps1 -Deploy"
        return out
    try:
        with io.open(mf, "r", encoding="utf-8-sig") as fh:
            man = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        out["code"] = "manifest_invalid"
        out["detail"] = "构建清单解析失败：%r" % exc
        return out

    out["builtVersion"] = man.get("version")
    out["builtUtc"] = man.get("builtUtc")
    if (man.get("dllSha256") or "").lower() != deployed.lower():
        out["code"] = "stale_deploy"
        out["detail"] = ("模块里的 DLL 与上次构建产物不一致（磁盘 %s vs 清单 %s）—— 重跑 build.ps1 -Deploy"
                         % (deployed[:16], (man.get("dllSha256") or "")[:16]))
        return out

    if has_src:
        want = man.get("sources") or {}
        changed, have = [], set()
        for name in sorted(os.listdir(src)):
            if not name.endswith(".cs"):
                continue
            have.add(name)
            if (want.get(name) or "").lower() != _sha256_file(os.path.join(src, name)).lower():
                changed.append(name)
        missing = [n for n in want if n not in have]
        if changed or missing:
            out["code"] = "stale_source"
            out["detail"] = ("源码与上次构建不一致（改动 %d 个、缺失 %d 个）—— 需要重新构建后部署"
                             % (len(changed), len(missing)))
            out["changedSources"] = changed[:8]
            return out

    sess = _game_session() or {}
    b = sess.get("build") or {}
    if not b:
        out["code"] = "game_offline"
        out["detail"] = "游戏没在运行（无法核对进程内 DLL）—— 文件链条一致，启动游戏后再核对"
        return out

    out["loadedSha256"] = b.get("loadedSha256")
    out["loadedVersion"] = b.get("version")
    out["fileChangedSinceLoad"] = b.get("fileChangedSinceLoad")

    # 先确认这个状态文件属于**当前**会话：它可能是上一局留下的。
    # 进程已经退出时，"进程里的 DLL"这个说法不成立，硬比只会给出误导结论
    # （首次部署 0.7.0 后本函数就误报过一次 game_running_other_build）。
    alive = _pid_is_game(sess.get("pid"))
    loaded = (b.get("loadedSha256") or "").lower()
    mismatch = bool(b.get("fileChangedSinceLoad")) or (loaded and not deployed.lower().startswith(loaded))
    if alive is False:
        out["code"] = "game_offline"
        out["detail"] = ("游戏没在运行（状态文件是上次会话留下的）—— 文件链条一致；重启游戏后再核对进程内 DLL"
                         + ("；注意上次会话进程内是 %s（与当时的磁盘不一致）" % b.get("loadedSha256") if mismatch else ""))
        return out
    if alive is None:
        out["note"] = "进程存活探测失败（未知），下面的进程内比对仅供参考"

    if b.get("fileChangedSinceLoad"):
        out["code"] = "game_not_restarted"
        out["detail"] = ("游戏进程里加载的是更早的 DLL（加载时 %s，磁盘上 %s）—— 必须**完全重启游戏**"
                         % (b.get("loadedSha256"), deployed[:16]))
        return out
    if mismatch:
        out["code"] = "game_running_other_build"
        out["detail"] = "游戏进程里的 DLL 与磁盘上的不同（进程 %s）—— 请确认游戏读的是这个模块目录" % loaded
        return out
    out["detail"] = ("%s：源码 = 构建产物 = 部署文件 = 进程内 DLL（版本 %s）" % (
        "四段一致" if has_src else "三段一致（源码段跳过）", out.get("builtVersion")))
    return out


def send_command(method, parameters=None, timeout=30.0, log_dir=None, poll=0.15):
    """把请求写入 pending/，轮询 done/ 拿响应。返回 (response_dict, error_string)。"""
    import time as _time
    import uuid as _uuid

    root = _commands_root(log_dir)
    pend = _pending_dir(log_dir)
    done = _done_dir(log_dir)
    for d in (root, pend, done):
        if not os.path.isdir(d):
            os.makedirs(d)

    req_id = _uuid.uuid4().hex[:16]
    seq = "%019d" % _time.time_ns()
    req = {
        "protocolVersion": BRIDGE_PROTOCOL_VERSION,
        "id": req_id,
        "method": method,
        "parameters": parameters or {},
        "issuedUtc": _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime()) + "Z",
    }
    name = "%s-%s.json" % (seq, req_id)
    tmp = os.path.join(pend, name + ".tmp")
    final = os.path.join(pend, name)
    with io.open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(req, ensure_ascii=False))
    os.replace(tmp, final)

    resp_path = os.path.join(done, req_id + ".json")
    deadline = _time.time() + float(timeout)
    next_alive_check = _time.time() + 1.0
    while _time.time() < deadline:
        if os.path.isfile(resp_path):
            try:
                with io.open(resp_path, "r", encoding="utf-8-sig", errors="replace") as fh:
                    resp = json.load(fh)
            except Exception:  # noqa: BLE001
                _time.sleep(poll)
                continue
            # 会话身份校验：拒绝属于旧游戏进程的响应
            sess = _game_session()
            if sess and resp.get("process"):
                if resp["process"].get("runToken") and sess.get("runToken") and \
                        resp["process"]["runToken"] != sess["runToken"]:
                    return None, ("响应来自不同会话（runToken 不匹配：%s vs %s）—— 请确认游戏没有重启过"
                                  % (resp["process"].get("runToken"), sess.get("runToken")))
            if resp.get("protocolVersion") != BRIDGE_PROTOCOL_VERSION:
                return None, "响应协议版本不匹配: %s" % resp.get("protocolVersion")
            if resp.get("ok") is True and resp.get("error") is not None:
                return None, "响应违反不变式：ok=true 但 error 非空"
            if resp.get("ok") is False and not resp.get("error"):
                return None, "响应违反不变式：ok=false 但 error 为空"
            return resp, None
        # 进程死亡立即返回，不傻等到 deadline（照 Coop 的 process_exited 语义）
        if _time.time() >= next_alive_check:
            next_alive_check = _time.time() + 1.0
            pid = (_game_session() or {}).get("pid")
            if pid and _pid_is_game(pid) is False:
                _drop_pending(final)
                return None, "process_exited: 游戏进程（pid %s）在响应前退出，请求已作废" % pid
        _time.sleep(poll)
    # 超时：作废请求，避免它在游戏下次启动时被执行（幽灵战斗）
    _drop_pending(final)
    _code, _msg = _diag_no_response(timeout, log_dir)
    return None, "%s: %s" % (_code, _msg)


def game_dir():
    """优先级：环境变量 > blbridge.json > 内置默认值（bl_config 能看到来历）。"""
    d = os.environ.get("BANNERLORD_DIR")
    if d:
        return d
    return _cfg("gameDir", DEFAULT_GAME_DIR)


def log_dir():
    d = os.environ.get("BLBRIDGE_LOG_DIR")
    if d:
        return d
    return _cfg("logDir", bl_analyze.default_log_dir())


def status_path():
    return os.path.join(log_dir(), "bridge_status.json")


def warbandlord_config():
    return os.path.join(game_dir(), "Modules", "Warbandlord", "config.xml")


# ─────────────────────────────────────────────────────────────────────
# config.xml 读写（行级编辑，保留原文件其余内容与 BOM）
# ─────────────────────────────────────────────────────────────────────

_OPT_RE = re.compile(r'<Option\s+id="([^"]+)"\s+value="([^"]*)"\s*/?>')
_OPEN_RE = re.compile(r'<(Category|Group|SubGroup)\b([^>]*?)(/?)>')
_CLOSE_RE = re.compile(r'</\s*(Category|Group|SubGroup)\s*>')
_ID_RE = re.compile(r'id="([^"]+)"')


def _read_text(path):
    with io.open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def _scan(text):
    """按行扫描 config.xml，返回 [(行号, 完整路径, 值, OptionId)]。

    路径由 Category / Group / SubGroup 的 id 逐层拼接而成，
    例如 DamageCalc/ArmorEffect/ArmorBreakPoint。
    """
    out = []
    stack = []
    for idx, line in enumerate(text.splitlines()):
        cm = _CLOSE_RE.search(line)
        if cm:
            if stack and stack[-1][0] == cm.group(1):
                stack.pop()
            continue
        om = _OPEN_RE.search(line)
        if om:
            attrs = om.group(2) or ""
            ident = _ID_RE.search(attrs)
            self_closing = (om.group(3) == "/") or attrs.rstrip().endswith("/")
            if ident and not self_closing:
                stack.append((om.group(1), ident.group(1)))
            continue
        m = _OPT_RE.search(line)
        if m and stack:
            full = "/".join(x[1] for x in stack) + "/" + m.group(1)
            out.append((idx, full, m.group(2), m.group(1)))
    return out


def read_config(paths=None):
    path = warbandlord_config()
    if not os.path.isfile(path):
        raise IOError("找不到 Warbandlord 配置: %s" % path)
    text = _read_text(path)
    out = {}
    for _idx, full, val, _oid in _scan(text):
        out[full] = val
    if paths:
        return dict((p, out.get(p)) for p in paths)
    return out


def _is_number(s):
    """宽松数字判定（v0.8.10 F5：给 apply_config 的"旧值数字 → 新值非数字"提醒用）。"""
    try:
        float(str(s).strip())
        return True
    except (TypeError, ValueError):
        return False


def apply_config(edits, dry_run=False, allow_missing=False):
    """edits: [{"path": "DamageCalc/ArmorEffect/ArmorBreakPoint", "value": "45"}, ...]"""
    path = warbandlord_config()
    if not os.path.isfile(path):
        raise IOError("找不到 Warbandlord 配置: %s" % path)
    raw = io.open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = _read_text(path)
    lines = text.splitlines()

    # v0.8.10（F5）：入参形状与值的健全性检查。
    #
    # 原来直接 `e["path"]` / `e["value"]` —— 传错形状（如 {"Key": "0.3"} 而不是
    # [{"path": ..., "value": ...}]）会抛裸 `TypeError: string indices must be integers`，
    # 调用方看不出正确格式；值本身也不检查，`"abc"` 会被当成合法新值写进 XML
    # （虽有备份兜底，但越早拒绝越好）。真机回归实测踩到这两点。
    if not isinstance(edits, list) or not edits:
        raise ValueError('edits 必须是**非空数组**，形如 '
                         '[{"path": "DamageCalc/ArmorEffect/ArmorBreakPoint", "value": "45"}]')
    for i, e in enumerate(edits):
        if not isinstance(e, dict) or "path" not in e or "value" not in e:
            raise ValueError('edits[%d] 必须是 {"path": "...", "value": "..."} 形式的对象，实得：%r' % (i, e))
        v = e["value"]
        if isinstance(v, bool) or v is None or str(v).strip() == "":
            raise ValueError('edits[%d] 的 value 不能为空/布尔（path=%s）' % (i, e["path"]))
        if any(c in str(v) for c in "<>&\"'\n\r"):
            raise ValueError('edits[%d] 的 value 含 XML 特殊字符（< > & " \' 或换行），'
                             '会破坏配置文件（path=%s）' % (i, e["path"]))

    want = {}
    for e in edits:
        want[e["path"]] = str(e["value"])
    # 数值启发式（只提醒、不拒绝）：旧值是数字而新值不是 ⇒ 很可能写错了键或值类型。
    warnings = []

    info = {}
    for idx, full, val, oid in _scan(text):
        info[idx] = (full, val, oid)

    changed = {}
    missing = []
    new_lines = []
    for idx, line in enumerate(lines):
        if idx in info:
            full, oldval, oid = info[idx]
            if full in want:
                indent = line[:len(line) - len(line.lstrip())]
                new_lines.append(indent + '<Option id="%s" value="%s" />' % (oid, want[full]))
                changed[full] = {"old": oldval, "new": want[full]}
                if _is_number(oldval) and not _is_number(want[full]):
                    warnings.append("%s：旧值 %r 是数字、新值 %r 不是 —— 请确认该键允许非数值"
                                    % (full, oldval, want[full]))
                continue
        new_lines.append(line)

    for p in want:
        if p not in changed:
            missing.append(p)

    result = {"changed": changed, "missing": missing, "dryRun": bool(dry_run), "path": path}
    if warnings:
        result["warnings"] = warnings
    if dry_run:
        return result
    if missing and not allow_missing:
        return dict(result, ok=False, error="以下配置路径不存在，未写入任何修改: %s" % ", ".join(missing))

    # 备份
    import shutil
    import time
    backup = path + ".bak_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, backup)

    body = "\n".join(new_lines)
    if text.endswith("\n") and not body.endswith("\n"):
        body += "\n"
    data = body.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    tmp = path + ".tmp"
    with io.open(tmp, "wb") as fh:
        fh.write(data)

    # 写前校验：能否正常解析 + 值是否生效
    import xml.etree.ElementTree as ET
    try:
        ET.parse(tmp)
    except Exception as exc:  # noqa: BLE001
        os.remove(tmp)
        return dict(result, ok=False, error="XML 校验失败，已放弃写入: %r" % (exc,))
    os.replace(tmp, path)
    check = read_config(list(want.keys()))
    result.update({"ok": True, "backup": backup, "verified": check})
    return result


# ─────────────────────────────────────────────────────────────────────
# MCP 工具定义
# ─────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "bl_status",
        "description": ("读取模块状态（是否加载、日志目录、会话内场次、最近一场战斗）"
                        "+ 构建一致性（源码/构建/部署/进程内）+ 会话诊断（游戏是否真的在跑）。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_list_battles",
        "description": "列出已记录的战斗日志文件（名称/大小/时间）",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "最多返回多少条（默认 20，倒序）"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_analyze",
        "description": ("分析一场战斗日志，输出：血量模型校验（致死总伤害 vs 最大血量偏差）、"
                        "每击伤害分布（按兵种/伤害类型/部位/武器/远近战）、挨打成本。"
                        "不传 file 时分析最新一场。"),
        "inputSchema": {"type": "object", "properties": {
            "file": {"type": "string", "description": "战斗 JSONL 的绝对路径（可选）"},
            "format": {"type": "string", "enum": ["text", "json"], "description": "默认 text"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_read_events",
        "description": "读取原始事件行（可按类型过滤与限量），用于细节排查",
        "inputSchema": {"type": "object", "properties": {
            "file": {"type": "string"},
            "type": {"type": "string", "description": "meta/unit/hit/kill/flee/panic/sample/end，可选"},
            "limit": {"type": "integer", "description": "默认 50"},
            "offset": {"type": "integer", "description": "默认 0"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_read_config",
        "description": "回读 Warbandlord 的 config.xml 配置值（可不传 paths 取全部）",
        "inputSchema": {"type": "object", "properties": {
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "形如 DamageCalc/ArmorEffect/ArmorBreakPoint；不传则返回全部"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_apply_config",
        "description": ("修改 Warbandlord config.xml（自动备份 + 写后 XML 校验）。"
                        "注意：游戏需要重启后配置才生效。"),
        "inputSchema": {"type": "object", "properties": {
            "edits": {"type": "array", "items": {"type": "object", "properties": {
                "path": {"type": "string"}, "value": {"type": "string"}}, "required": ["path", "value"]}},
            "dry_run": {"type": "boolean", "description": "只预览不写入"}},
            "required": ["edits"], "additionalProperties": False},
    },
    {
        "name": "bl_rts_config",
        "description": ("回读 **RTSCamera** 的配置（Documents\\...\\Configs\\RTSCamera\\RTSCameraConfig.xml）。"
                        "只读。用来查'攻城相机高度/自由相机/抬升触发'这些开关现在是什么值。"
                        "我们不改它的代码，只当它的参数管理员（见 bl_apply_rts_config）。"),
        "inputSchema": {"type": "object", "properties": {
            "keys": {"type": "array", "items": {"type": "string"},
                     "description": "只读这些键（如 ElevatedHeightInSiege）；不传返回全部"},
            "presets": {"type": "boolean", "description": "true = 顺带回显可用预设名"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_apply_rts_config",
        "description": ("写 RTSCamera 配置（自动备份 + 写后 XML 校验 + 回读核对）。"
                        "**实测：改完无需重启，下一场战斗就生效**（RTSCamera 每场读一次配置）；"
                        "但它运行中会把自己的配置覆写回去 ⇒ 想要「永久」请在游戏内 MCM 改。"
                        "可传 preset（siege-god / free-always / elevated-always / god-full）或 edits。"),
        "inputSchema": {"type": "object", "properties": {
            "preset": {"type": "string",
                       "description": "预设名：siege-god(攻城抬升10) / free-always / elevated-always / god-full"},
            "edits": {"type": "array", "items": {"type": "object", "properties": {
                "key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]},
                "description": "逐键写入，如 [{\"key\":\"ElevatedHeightInSiege\",\"value\":\"10\"}]"},
            "dry_run": {"type": "boolean", "description": "只预览不写入"},
            "allow_missing": {"type": "boolean", "description": "允许写入表里不存在的键（默认 false）"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_ghost_camera",
        "description": ("开关**引擎自带**的自由观察相机（幽灵模式）。"
                        "运行时生效、**对任何一场战斗都有效**（包含你自己打的），不像 spectate 只对 AI 场次。"
                        "做法：设 `MissionScreen.IsCheatGhostMode` —— 相机模式变 Free，"
                        "且命令 UI 开着也保持自由（能观战 + 能下令）。"
                        "⚠️ 要能自己用 WASD 飞还需 engine_config.txt 的 cheat_mode=1（只读项，我们不改）。"
                        "装了 RTSCamera 的机器优先用它的配置（bl_apply_rts_config）。"),
        "inputSchema": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["status", "on", "off", "toggle"],
                     "description": "默认 status（只查）；on/off/toggle 切换"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_camera_speed",
        "description": ("相机移动速度分三条互不相干的腿，用 mode 指名："
                        "`shift` = 引擎自由相机的 Shift 倍率（**不需要作弊模式**，改完按住 Shift 飞就生效）；"
                        "`base` = 引擎自由相机的基础倍率（⚠️ 引擎把速度分量 clamp 在 ±20，"
                        "调到某个量之后可能不再变快）；"
                        "`rts` = RTSCamera 的相机速度系数（**最有效**：上限随基础速度一起放大，没有那个天花板）；"
                        "`boost` = 三条一次设成同一个值；`status` = 只读现状（默认）；`probe` = 测速探针。"
                        "每条腿都**写完回读**，失败会点名是哪条腿、为什么 —— 不静默。"
                        "装了 RTSCamera 时优先用 rts。"),
        "inputSchema": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["status", "shift", "base", "rts", "boost", "probe"],
                     "description": "默认 status（只查）；shift/base/rts/boost 需要 value；probe = 测速探针"},
            "value": {"type": "number",
                      "description": "倍率：shift 需 ≥1 的整数；base / rts / boost 为 0.1~1000"},
            "action": {"type": "string", "enum": ["end"],
                       "description": "仅 mode=probe 用：不带 = 记起点，带 end = 记终点并算速度（位移/墙钟秒数）"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_control_agent",
        "description": ("接管士兵（**最小版**）：把 `Mission.MainAgent` 换成友方某个 agent 并交给玩家控制器。"
                        "官方那条路（`CanTakeControlOfAgent`）**只在主角阵亡后**才允许、且**不改 MainAgent**；"
                        "本工具补上「随时接管」。"
                        "选目标：`agentIndex` > `troop` > `formation` > 该方第一个存活者；只允许玩家方（拒绝敌人）。"
                        "`mode=release` 换回接管前记录的那个主角色；`mode=status` 只看现状（不改任何东西）。"
                        "装了 RTSCamera 时会走它自己的**平滑推镜**，结果在 `cameraFollow`；"
                        "没装则退回只复位 screen。"
                        "⚠️ 与 RTSCamera 并存时它的 `ControlTroop` 键也会改 MainAgent，两边会互相覆盖。"
                        "未实现、传了就报 unsupported_param：agentId / slot / mount / weapon。"),
        "inputSchema": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["take", "release", "status"],
                     "description": "默认 take（接管）；release = 换回原始主角色；status = 只读现状"},
            "agentIndex": {"type": "integer",
                           "description": "引擎的 agent.Index（遥测/事件里的 agent 字段就是它）；优先级最高，"
                                          "但换一场就变"},
            "troop": {"type": "string", "description": "兵种 id（Character.StringId），取该方第一个存活的"},
            "formation": {"type": "string",
                          "description": "编队：Infantry/Ranged/Cavalry/HorseArcher/Skirmisher 或 0~4"},
            "side": {"type": "string", "enum": ["player", "attacker", "defender"],
                     "description": "从哪一方里挑目标，默认 player（但只接受玩家方的 agent）"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_order",
        "description": ("战斗中途改令：对**正在进行**的战斗里某一方的编队改 movement / 移动到指定点 / "
                        "冲锋到指定敌方编队 / 攻击指定敌方单位 / 阵列 / 射击纪律 / 骑乘令。"
                        "⚠️ 只在战斗内有意义：mission 之外碰 MovementOrder 会抛 TypeInitializationException"
                        "并把该类型永久标记为不可用，所以没战斗时直接拒（no_mission）。"
                        "⚠️ movement / position / target / targetAgent **四者互斥**（一个编队只能有一个 movement order）；"
                        "riding 与它们**正交**（只管骑不骑、不管去哪），不参与互斥。"
                        "默认 detachAI=true（连带 SetControlledByAI(false,false)）；否则 team 级战术会周期性把令覆盖回去"
                        "（实测守方 stop 组被覆盖 59/115 次、位移 154 m）。设 false 仅用于对照组。"
                        "判据是**当场回读**：三种 movement order 都回传 orderBefore/orderAfter；"
                        "position 另回传 moveTarget（引擎按导航网格算的落点，与请求值有偏差属引擎修正），"
                        "target 另回传 targetAfter + targetDistance。要确认没被覆盖，隔几秒再调一次看是否仍是上次的值。"
                        "⚠️ emptyFormations > 0 = 令写进去了但那个编队没人 ⇒ 没人执行"
                        "（编队按兵种自动分：弓手在 Ranged、近战步兵在 Infantry，先核对 formation 选对没）。"
                        "⚠️ targetAgent 的目标会死：阵亡后重申会跳过并记 order error（不静默改成冲锋）。"),
        "inputSchema": {"type": "object", "properties": {
            "side": {"type": "string", "enum": ["player", "attacker", "defender"],
                     "description": "哪一方，默认 player"},
            "formation": {"type": "string",
                          "description": "编队：Infantry/Ranged/Cavalry/HorseArcher/Skirmisher 或下标 0~4；"
                                         "不传 = 该方所有有兵的编队"},
            "movement": {"type": "string", "enum": ["charge", "advance", "fallback", "stop", "retreat"],
                         "description": "改 movement（hold 已移除：引擎层等同 stop）。"
                                        "会被组路径 0.5s 重申覆盖，本工具同步改「待重申的值」（回传 pendingSpecsUpdated）"},
            "position": {"type": "string",
                         "description": "移动到指定点：\"x,y\" 或 \"x,y,z\"（英文逗号，单位米，各分量 |值| ≤ 10000；"
                                        "z 省略 = 0）。本工具会标成「手动令优先」（回传 pendingSpecsUpdated），"
                                        "重申时照原样重申同一点，不会退回 charge"},
            "target": {"type": "string",
                       "description": "冲锋到指定**敌方编队**：Infantry/Ranged/Cavalry/HorseArcher/Skirmisher 或下标 0~4。"
                                      "目标编队为空/不存在时直接拒，不静默换成普通冲锋。回传 targetAfter / targetDistance"},
            "arrangement": {"type": "string",
                            "enum": ["line", "shieldwall", "circle", "square", "skein", "column",
                                     "loose", "scatter"],
                            "description": "改编队阵列：**不在**组路径重申范围内"},
            "firing": {"type": "string", "enum": ["fireAtWill", "holdFire"],
                       "description": "改射击纪律：引擎只有这两档"},
            "targetAgent": {"type": "integer",
                            "description": "攻击指定**敌方单位**：agent 下标（可从遥测或 bl_control_agent status 的 "
                                           "candidates 取）。回传 targetEntitySet / targetAgentAlive / targetDistance"},
            "riding": {"type": "string", "enum": ["free", "mount", "dismount"],
                       "description": "改骑乘令：free 不干预 / mount 上马 / dismount 下马。"
                                      "回传 ridingBefore/ridingAfter；只对**有坐骑**的单位有实际效果"},
            "detachAI": {"type": "boolean",
                         "description": "是否连带 SetControlledByAI(false,false)，默认 true；"
                                        "设 false 只用于「看它会不会被战术覆盖」的对照"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_skip_video",
        "description": ("跳过开场动画。**不模拟 ESC**，而是先问「当前活动状态是不是 `VideoPlaybackState`」，"
                        "是就直接调它的 `OnVideoFinished()`。判据硬、无副作用；"
                        "不是视频时如实报 `not_video` 并回传当前状态名。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_cheat_mode",
        "description": ("开关**作弊模式**（带回读：写 NativeConfig.CheatMode 再回读验证）。"
                        "**为什么需要它**：引擎自由相机的倍率热键（Ctrl+↑ ×1.5 / Ctrl+↓ ×2÷3 / Ctrl+中键重置）"
                        "与观察者 HUD 的「摄像机移动速度」读数都被 `Game.Current.CheatMode` 门控 ⇒ "
                        "这是「相机太慢」的另一条正解，不改我们的相机代码。"
                        "⚠️ 开了它 = 打开开发者/作弊通道（F2/F3/F4 杀敌杀友、Ctrl+K 幽灵相机、Ctrl+F5 换控制权等一并生效）；"
                        "本工具**不做任何自动开启**，只按显式请求执行。只作用于本次进程，重启回到 engine_config.txt 的设置。"),
        "inputSchema": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["status", "on", "off", "toggle"],
                     "description": "默认 status（只查）"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_battle_status",
        "description": "查询游戏内推演状态机（idle/loading/running/ended/error）、进度（双方存活数）与最近一次结果",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_start_battle",
        "description": ("⚡【Agent 调用纪律 · 必读】收到\"开一场战斗\"类需求时**直接调用本工具**，"
                        "禁止先去列目录 / 读 XML / 查索引确认兵种 id：troop id 由引擎侧 MBObjectManager "
                        "现场 Resolve，前置探索不产生任何新信息。缺参数就一次性问清（模式/人数/兵种/是否上帝视角）；"
                        "参数齐了立刻开。"
                        "第三方模组兵种（如 WarlordsBattlefield 的 empire_infantry5）不在官方索引里，"
                        "必须带 skipTroopCheck=true；attackerTroop/defenderTroop 必填（即使给了 Groups）。"
                        "攻城常用 scene=empire_town_c（Ortysia），要上帝视角带 rtsPreset=siege-god。"
                        "开一场**无玩家**的 AI 对 AI 战斗（10 倍速）；游戏需停在官方自定义战斗界面"
                        "（用 bl_open_ui 进入）。城池场景自动走攻城。返回 accepted 后用 bl_wait_for_state 等 ended。"
                        "v0.8.41 新增：attackerTacticLevel/defenderTacticLevel（战术档位，需配 orders=default "
                        "才看得出效果）、terrain/randomTerrainSeed（地形）、aiFriendlyFireMultiplier"
                        "（**打到「玩家方」身上的伤害**倍率 —— 不是同队误伤，见该参数的说明）、"
                        "keepCorpses、sceneLevel/timeOfDay（攻城）。"
                        "被忽略的参数会在响应的 envNotes 里报出 —— 绝不静默。"),
        "inputSchema": {"type": "object", "properties": {
            "attackerTroop": {"type": "string", "description": "攻方兵种 id，如 imperial_legionary"},
            "attackerCount": {"type": "integer", "description": "攻方人数，默认 20"},
            "defenderTroop": {"type": "string", "description": "守方兵种 id，如 battanian_fian_champion"},
            "defenderCount": {"type": "integer", "description": "守方人数，默认 20"},
            "scene": {"type": "string", "description": "场景名，默认 battle_terrain_a"},
            "durationCapSec": {"type": "integer", "description": "单场时长上限（游戏内秒），默认 600"},
            "orders": {"type": "string", "enum": ["charge", "default"],
                       "description": ("战术对称化。charge（默认）=双方都用 TacticCharge，消除攻守方向偏差"
                                       "（实测镜像对局 13:0 一边倒）；default=引擎默认战术，仅作 A/B 对照")},
            "playerSide": {"type": "string", "enum": ["attacker", "defender"],
                           "description": "谁被标记为玩家侧，仅排查该标记是否带来系统性偏差，默认 attacker"},
            "spectate": {"type": "boolean",
                         "description": ("兜底观战镜头（默认 false）。true = 挂官方 ICameraModeLogic 走自由观察相机。"
                                         "⚠️ 装了 RTSCamera 时野战会被它抢先（无效果），**优先用 rtsPreset**")},
            "rtsPreset": {"type": "string",
                          "description": ("开战前套用 RTSCamera 预设：siege-god=攻城也抬升视角；"
                                          "free-always / elevated-always / god-full。改完无需重启即对本场生效，会先备份")},
            "dummySide": {"type": "string", "enum": ["none", "attacker", "defender"],
                          "description": "不朽靶场：把该方设为永不倒下的靶子，默认 none"},
            "freezeDummies": {"type": "boolean",
                              "description": "冻结靶子 AI（不还手）。默认 false"},
            "unlimitedAmmo": {"type": "boolean",
                              "description": "给射手补满弹药（靶子的弹药不补 —— 它是被测对象），默认 false"},
            "dummyArmor": {"type": "string",
                           "description": ("靶子护甲数值覆盖，如 \"head=45,torso=35,legs=20,arms=25\""
                                           "（只作用于靶子；未知部位名/非数字直接报错）")},
            "dummyBodyItem": {"type": "string",
                              "description": ("把靶子**身甲**换成该物品 id（材质对照实验用，如 plated_leather_coat）。"
                                              "材质抗性只来自物品，数值可另用 dummyArmor 对齐")},
            "skipTroopCheck": {"type": "boolean",
                               "description": ("跳过兵种 id 校验（默认 false）。校验走 BannerlordSage 索引，"
                                               "只覆盖官方 XML；用第三方模组兵种时置 true")},
            "allowAnyState": {"type": "boolean",
                              "description": ("跳过「必须停在自定义战斗界面」检查（默认 false）。"
                                              "⚠️ 实测：**裸主菜单下兵种/文化数据未加载**，传任何兵种 id 都是 "
                                              "unknown_troop ⇒ 它**不能**用来『从主菜单开战』（该原始设计目标"
                                              "当年就已被排除，见 PROGRESS §四）。"
                                              "⚠️ 也**不要**再用它去绕 wrong_state —— "
                                              "带 NavalDLC 时官方入口落 NavalCustomBattleState，"
                                              "v0.8.44 起判据已改为后缀匹配 `*CustomBattleState`，"
                                              "正常走 open_ui 就不会被误报。")},
            "rounds": {"type": "integer",
                       "description": "多轮连续实验：同一 mission 内跑 N 轮（每轮独立日志），默认 1 = 关闭"},
            "roundEndAlive": {"type": "integer",
                              "description": "某方存活 ≤ 此值即判定本轮结束（默认 1）"},
            "roundSwap": {"type": "boolean", "description": "每轮交换攻守（第 2、4…轮把原守方放到攻方位置）"},
            "roundSpawnAttacker": {"type": "string",
                                   "description": "重生时攻方进场点，如 \"100,0,200\"（x,y,z 或 x,z）"},
            "roundSpawnDefender": {"type": "string", "description": "重生时守方进场点，语法同上"},
            "randomSeed": {"type": "integer",
                           "description": ("随机种子（-1 = 不设）。⚠️ 伤害公式的随机命中因子在 native 层掷，"
                                           "同种子不保证逐值复现")},
            "attackerGroups": {"type": "string",
                               "description": ("攻方多兵种/战术组：troop:count[:formation[:movement]]，多组用 | 分隔。"
                                               "给了它则 attackerTroop/attackerCount 被忽略；与 orders 互斥。"
                                               "⚠️ 第 3 字段 formation **不决定编队**（编队由兵种自身决定），只回显；"
                                               "写错族名会在 formationWarnings 里被点名（不阻断开战）")},
            "defenderGroups": {"type": "string", "description": "守方多兵种/战术组，语法同上"},
            # ── v0.8.41：战术档位 + 环境旋钮（零 Harmony；取证见 src/TacticsCombatant.cs / src/BattleEnv.cs）──
            "attackerTacticLevel": {
                "type": "integer",
                "description": ("攻方战术档位：-1（默认，不覆盖）或 0..100。"
                                "引擎用它**分 20 / 50 两档**决定给该方挂哪些 TacticOption"
                                "（<20 只有 TacticCharge；>=20 追加 TacticFullScaleAttack 等；"
                                ">=50 再追加 TacticFrontalCavalryCharge 等）。"
                                "⚠️ 默认 orders=charge 会 ClearTacticOptions() 只留 TacticCharge —— "
                                "想真的看到档位效果，要配 orders=default（保留引擎战术菜单）。"
                                "响应与 status 里的 aNative/dNative 是引擎原生值（= 参战兵种 max(Tactics)），"
                                "用来和你设的值对照")},
            "defenderTacticLevel": {"type": "integer", "description": "守方战术档位，语义同上"},
            "terrain": {
                "type": "string",
                # ⚠️ 这里**必须是纯字面量**：`bl_check_dispatch.py` 用 `ast.literal_eval(TOOLS)`
                #    静态核对"声明 ↔ 分派"，任何表达式（`+` / `.join()`）都会让它当场报
                #    "malformed node or string"（v0.8.41 实测踩到）。权威表是 bl_common.TERRAINS，
                #    与 src/BattleEnv.cs 的一致性由 bl_selftest ⑭ 双向对账。
                "description": ("地形覆盖（默认不设 = 引擎按场景决定）。可选："
                                "plain | desert | snow | forest | steppe | fording | mountain | "
                                "lake | water | river | canyon | ruralarea | swamp | dune | bridge | "
                                "coastalsea | opensea | beach | cliff | nonnavigableriver | "
                                "landrestriction | searestriction | underbridge。"
                                "⚠️ 只管野战路径；攻城走官方入口，传了会被显式忽略并在 envNotes 里报出。"
                                "⚠️ **在自定义战斗里它没有托管消费者**（2026-10-05 反证）：唯一已知消费点"
                                "`SandboxAgentStatCalculateModel` 只注册在战役模式；自定义战斗用的是 "
                                "`CustomBattleAgentStatCalculateModel`（全文 Terrain/Perk 命中 0）"
                                "⇒ 本靶场实测「默认 vs snow 无可测差异」（native 侧未排除）")},
            "randomTerrainSeed": {
                "type": "integer",
                "description": ("随机地形种子（默认不设）。设了会同时打开 NeedsRandomTerrain。"
                                "⚠️ 托管侧零读取消费者；实测把引擎的**植被指纹**（rgl 日志的 "
                                "`Placed tree/flora count`）当判据，种子 -1/-1/7/12345 四次场景加载**逐字相同**"
                                "⇒ 该判据下**无可测差异**（不代表参数无效：指纹看不到地形索引图/地表材质）")},
            "keepCorpses": {
                "type": "boolean",
                "description": ("关掉尸体淡出（默认 false）。长跑/多轮时尸体量稳定 ⇒ 性能与寻路不随轮次漂移。"
                                "v0.8.45 起可用 `sample` 事件的 **`corpses`** 字段验证行为效应："
                                "实测对照的尸体数 13→7→5→1→0（会淡出），而 keepCorpses=true 时单调升到 19 "
                                "并**从不回落**")},
            "sceneLevel": {
                "type": "integer",
                "description": "攻城场景升级等级 1..3（默认 3 = 改动前的写死值）。仅攻城路径生效"},
            "timeOfDay": {
                "type": "number",
                "description": "攻城开战时刻，小时 0..24（默认 6 = 改动前的写死值）。仅攻城路径生效"}},
            "required": ["attackerTroop", "defenderTroop"], "additionalProperties": False},
    },
    {
        "name": "bl_wait_for_state",
        "description": ("轮询等待推演状态（idle/loading/running/ended/error），到点返回当前状态与结果。"
                        "开战后的 `loading` 段是引擎加载场景（秒级~几十秒），本工具只是盯状态，"
                        "1s 一轮；running 但引擎停滞（stalled）会提前判无效并早退。"),
        "inputSchema": {"type": "object", "properties": {
            "state": {"type": "string", "description": "目标状态，默认 ended"},
            "timeoutSec": {"type": "integer", "description": "最长等待秒数，默认 180"},
            "pollSec": {"type": "number", "description": "轮询间隔秒，默认 1"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_abort",
        "description": "中止当前正在进行的推演（调用引擎的 Mission.EndMission，走官方结束路径）",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_list_ui",
        "description": ("列出游戏内所有可进入的入口（初始状态选项）+ 当前激活状态 + **官方自定义战斗场景全表**"
                        "（合并表 CustomBattleScenes，含模式：战斗/围攻/村庄/领主大厅/海战/海上掠夺，"
                        "以及地形、场景等级、目录是否真的存在）。只读。"
                        "用它替代\"靠记忆猜 id/场景名\"：id、名字与场景表都是引擎当场给的。"),
        "inputSchema": {"type": "object", "properties": {
            "scenesMode": {"type": "string",
                           "enum": ["all", "battle", "siege", "village", "lordsHall", "naval", "navalRaid"],
                           "description": "只返回某一模式的场景（默认 all）"},
            "sceneLimit": {"type": "integer",
                           "description": "最多返回多少行场景（默认 0 = 全吐；全表 314 行约 35 KB）"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_open_ui",
        "description": ("走官方正门唤起一个游戏内界面（默认 = 官方自定义战斗界面，uiId=CustomBattle）。"
                        "v0.8.14 起不再有自建面板 —— 官方界面本身就是完整入口"
                        "（战斗/围攻/村庄/海战/海上掠夺 + 玩家类型 + 选择攻守方 + 全套地图参数）。"
                        "⚠️ 入口动作是 fire-and-forget（跨帧加载）：返回 requested=true 只代表已触发，"
                        "请用 bl_list_ui 轮询 activeState 是否真的变了。"
                        "⚠️ CustomBattleState 从主菜单进**官方本身要 ~30s 才落地**（真机实测，"
                        "不是本工具慢）；本工具不代等，落地判据见 bl_launch_game 第二步。"
                        "⚠️ 只在**主菜单**可用：游戏已加载时执行它会让状态栈卡在 GameLoadingState（真机实测过）。"),
        "inputSchema": {"type": "object", "properties": {
            "uiId": {"type": "string",
                     "description": ("入口 id，默认 CustomBattle；全部可用 id 见 bl_list_ui。"
                                     "⚠️ 不接受也不要叫 `id`：控制通道的 JSON 读取器是扁平的，"
                                     "`id` 会被信封里那个请求 id 顶掉（v0.8.12 真机踩过）")}},
            "additionalProperties": False},
    },
    {
        "name": "bl_close_ui",
        "description": ("从官方自定义战斗界面回主菜单（与官方 CustomBattle 的「返回」同一路径：PopState）。"
                        "open_ui 进去了就用它出来。白名单**只有** CustomBattleState："
                        "别的状态一律返回 bad_state —— 不会去 pop 引擎的任意状态。"),
        "inputSchema": {"type": "object", "properties": {
            "state": {"type": "string", "enum": ["CustomBattleState"],
                      "description": "要离开的状态，默认且仅支持 CustomBattleState"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_get_screen",
        "description": ("只读：读当前界面的层 / 影片 / 可点按钮（文本 + 是否可用 + 状态 + id）—— 读界面，"
                        "不模拟鼠标（合成输入到不了官方界面）。"
                        "返回 screenType / layerCount / layers[]（name、isActive、movies[]：movieName、dataSource、"
                        "buttonCount、buttons[]）。地图装饰层默认跳过，layerFilter 只看某层。"
                        "⚠️ 按钮定位**优先用 text**：id 常常是空的、而且不唯一"
                        "（真机 2026-09-27：主菜单 11 个按钮只有 1 个有 id；自定义战斗界面 16 个里 AddTroopButton 占 8 个）。"
                        "前置：游戏在跑，且已部署含 get_screen 的 DLL（v0.8.34+；未部署会 no_response）。"),
        "inputSchema": {"type": "object", "properties": {
            "layerFilter": {"type": "string",
                            "description": "可选：只返回层名包含该子串的层（大小写不敏感），并展开其全部按钮"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_get_viewmodel_property",
        "description": ("只读：读当前界面某层 ViewModel 的一个属性；点号路径可穿透嵌套"
                        "（如 'Smelting.SmeltableItemList'）。列表属性返回 count + items + missingSubProperties，"
                        "subProperties 抽每项的子字段。layerName 从 bl_get_screen 的 layers[].name 取。"
                        "⚠️ 属性不存在 = `ok:false` / `property_not_found` 并给出候选属性名（不是 value:null）；"
                        "`value:null` 只表示‘属性存在、值就是空’。层名写错 = `no_data_source` 并点名那层。"
                        "前置同 bl_get_screen（游戏在跑 + DLL 已部署 v0.8.34+）。"),
        "inputSchema": {"type": "object", "properties": {
            "propertyName": {"type": "string",
                             "description": "ViewModel 上的属性路径，点号分隔，如 'PlayerGold' 或 'Smelting.SmeltableItemList'"},
            "layerName": {"type": "string",
                          "description": "层名（从 bl_get_screen 的 layers[].name 取；如 'CustomBattle'）"},
            "subProperties": {"type": "string",
                              "description": "可选：对列表属性的每项，逗号分隔抽取的子字段名，如 'ItemDescription,ItemCost'"}},
            "required": ["propertyName", "layerName"], "additionalProperties": False},
    },
    {
        "name": "bl_get_inventory",
        "description": ("只读：读玩家队伍的库存清单（主队伍 ItemRoster）+ 金币。"
                        "返回 gold / itemCount / totalElements / items[]（name、id、quantity、type、value、weight、tier）。"
                        "limit 控制最多返回几条（默认 50；截断时 totalElements 是真实总数，可对账）。"
                        "⚠️ 前置：**战役内**（Campaign 上下文）—— 主菜单 / 自定义战斗会如实报 no_campaign，不猜。"
                        "需要已部署含 get_inventory 的 DLL（v0.8.36+）。"),
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "最多返回的条目数，默认 50"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_list_saves",
        "description": ("只读：列出所有存档（MBSaveLoad.GetSaveFiles）。"
                        "返回 count / saves[]（name、isCorrupted、meta：存档元数据键值包，含模组/版本信息）。"
                        "name 是唯一标识，喂给 bl_load_save。前置：游戏在跑（v0.8.36+ DLL）。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_load_save",
        "description": ("**按名字直载存档**（MBSaveLoad.LoadSaveGameData + StartNewGame，"
                        "不经过存档选择界面）—— 无人值守换档的正门，也是复现读档期弹窗（模组不匹配）"
                        "的测试入口。异步加载：返回 started=true 后轮询 bl_list_ui 等 topScreen 变 *MapScreen"
                        "（自动应答器会点掉『模组不匹配』的『是』）。"
                        "⚠️ 前置：主菜单（战役进行中拒绝 in_campaign）；name 必须精确匹配 bl_list_saves 的 name"
                        "（不存在 → save_not_found 并列出可用档）。需要 v0.8.36+ DLL。"),
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "存档名（bl_list_saves 里的 name，精确匹配）"}},
            "required": ["name"], "additionalProperties": False},
    },
    {
        "name": "bl_campaign_time",
        "description": ("只读：战役时间/暂停状态诊断。"
                        "status（默认，也是唯一可用的 mode）回读 "
                        "timeControlMode / inMenuContext / campaignDays / pauseMenuOpen。"
                        "**pauseMenuOpen** = 地图上的暂停菜单（ESC 菜单）是否开着 —— 原版在失焦时若 "
                        "BannerlordConfig.StopGameOnFocusLost=true 会自动打开它，而它会向 GameStateManager "
                        "注册 ActiveStateDisableRequest ⇒ MapState 不再 Tick ⇒ 整个战役冻结"
                        "（档位却仍是 StoppablePlay）⇒ 判定「切窗口会不会被暂停」只能靠本字段。"
                        "**campaignDays** = 单调递增的战役天数（CampaignTime.Now.ToDays）："
                        "失焦前后各读一次，天数涨了才证明时间真的在走"
                        "（timeControlMode 不是 Stop 证明不了引擎没冻结战役推进）。"
                        "⚠️ v0.8.39：时间保活（mode=on/off）已移除 —— 用户明确不需要改游戏的时间暂停，"
                        "而且它看不见暂停菜单那种暂停、副作用还会顶掉手动暂停。"
                        "要「切窗口不弹暂停菜单」请用原版选项 BannerlordConfig.StopGameOnFocusLost=false"
                        "（PROGRESS §三十二）。"
                        "前置：战役加载后才有 timeControlMode / campaignDays 字段。v0.8.36+ 才有本工具。"),
        "inputSchema": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["status"],
                     "description": "只接受 status（默认）；on/off 已在 v0.8.39 移除，传了会显式报错"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_campaign_overview",
        "description": ("只读：战役全局概览（控制面 A 阶段）。"
                        "返回 inCampaign / clans / kingdoms / settlements / mobileParties 计数，"
                        "以及玩家 gold / influence / playerClan / playerKingdom / campaignDays / timeControlMode。"
                        "⚠️ 前置：战役内（Campaign 上下文）；主菜单 / 自定义战斗如实报 no_campaign，不猜。"
                        "需要 v0.8.40+ DLL（含 campaign_overview）。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_list_kingdoms",
        "description": ("只读：列出所有王国（Kingdom）。"
                        "返回 count / kingdoms[]（name、stringId、rulingClan、leader、clanCount、gold、isKingdom）。"
                        "limit 控制最多返回几条（默认全量）。前置：战役内。需要 v0.8.40+ DLL。"),
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "最多返回的条目数，默认全量"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_list_clans",
        "description": ("只读：列出所有家族（Clan）。"
                        "返回 count / clans[]（name、stringId、tier、gold、influence、kingdom、leader、"
                        "isMinor、isEliminated、fiefCount、partyCount）。"
                        "limit 控制最多返回几条（默认全量）。前置：战役内。需要 v0.8.40+ DLL。"),
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "最多返回的条目数，默认全量"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_list_settlements",
        "description": ("只读：列出所有封地（Settlement）。"
                        "返回 count / settlements[]（name、stringId、type=town/castle/village/hideout、"
                        "ownerClan、mapFaction，以及 town 的 prosperity/loyalty/security/foodStocks、"
                        "village 的 hearth）。limit 控制最多返回几条（默认全量）。前置：战役内。需要 v0.8.40+ DLL。"),
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "最多返回的条目数，默认全量"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_list_parties",
        "description": ("只读：列出地图上所有队伍（MobileParty）。"
                        "返回 count / parties[]（name、stringId、isMainParty/isLordParty/isCaravan/isGarrison、"
                        "leader、mapFaction、partyType、strength、morale、gold、size、aiBehavior、"
                        "targetSettlement、targetParty）。limit 控制最多返回几条（默认全量）。前置：战役内。需要 v0.8.40+ DLL。"),
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "最多返回的条目数，默认全量"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_campaign_log",
        "description": ("只读：战役日志快照（最近 N 条 LogEntry）。"
                        "返回 count / entries[]（text、time）。count 控制条数（默认 20，上限 200）。"
                        "注意：这是快照而非实时流；实时订阅后续版本提供。前置：战役内。需要 v0.8.40+ DLL。"),
        "inputSchema": {"type": "object", "properties": {
            "count": {"type": "integer", "description": "返回的日志条数，默认 20，上限 200"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_fast_forward",
        "description": ("开关战斗加速（10 倍速，引擎官方 Mission.IsFastForward 通道，由 BlBridge 每帧重申）。"
                        "对**你自己手打的战斗**同样生效（含自定义战斗），不依赖 RTSCamera 等第三方 mod。"
                        "注意：原版引擎没有战斗加速键，RTSCamera 的快进需要手动绑键且会互相独立。"),
        "inputSchema": {"type": "object", "properties": {
            "enabled": {"type": "boolean", "description": "true=开启加速，false=关闭"}},
            "required": ["enabled"], "additionalProperties": False},
    },
    {
        "name": "bl_patches",
        "description": ("**Harmony 补丁内省（只读）**：回答「**谁补了哪个方法**」。"
                        "为什么需要它：三合一 MOD（RBM + Warbandlord + RCM）的**核心风险就是"
                        "「双重叠加」** —— 两个 mod 补同一方法、Transpiler 撞 Transpiler "
                        "会不会生成非法 IL。此前只能靠猜（2026-10-06 做 C6 血量系统时，"
                        "我就是靠猜「我的补丁装上没有」「和 RBM 既有的撞没撞」）。"
                        "conflicts=true 只列被 ≥2 个 owner 补的方法；transpilerClash=true "
                        "是**最危险**的一类（改 IL，出错就是崩溃）。"
                        "⚠️ 它**只读** Harmony 的公开内省 API（不建实例、不 patch、不改 IL），"
                        "与 EngineProbe 同性质，不破坏本项目「删模块即完全回退」。"
                        "Harmony 未安装时返回 available=false（用反射，BlBridge 对 0Harmony 零依赖）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "只看补某个方法（简短类型名或全名，如 Mission / "
                                                             "DefaultCharacterStatsModel；也支持方法名子串）"},
                "owner": {"type": "string", "description": "只看某个 Harmony 实例 id（如 com.rbmcombat / "
                                                           "com.rbmmain / bannerlord.uiextender.ex）"},
                "conflicts": {"type": "boolean", "description": "只看被 ≥2 个 owner 补的方法（★ 核心判据）"},
                "detail": {"type": "boolean", "description": "是否给出每个补丁的明细行（默认 true；"
                                                             "false 只给计数，省 token）"},
                "limit": {"type": "integer", "description": "最多返回几个方法，默认 100"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_patch_failures",
        "description": ("**补丁失败清单**：把 Harmony 的 `HarmonyException` 从「现象」变成「点名」。"
                        "返回每条失败**想补的目标**（`targetClass` / `targetMethod`）与出现次数。"
                        "为什么需要它：`bl_patches` 只说「这个方法**没被补**」，"
                        "**不会说为什么**；而异常消息里直接写着原因"
                        "（如 `Ambiguous match for HarmonyMethod[(class=X, methodname=Y, …)]` "
                        "⇒ 方法有重载、Harmony 找不到唯一目标）。"
                        "★ **用法（两座桥交叉）**：拿这里的 `targetClass` 交给 `bl_patches` 按类型反查，"
                        "就能看到该类型上**谁在打补丁**（同类型的邻居方法上会露出 owner）"
                        "⇒ 从而定位到是哪个 mod 的补丁失败了。"
                        "⚠️ **边界（如实）**：发起补丁的 mod **不在异常里**"
                        "（HarmonyException 只带目标描述），所以本工具给的是**线索**而非归因结论。"
                        "⚠️ 只含**托管**异常；JIT 期失败与原生崩溃不在其中。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_exceptions",
        "description": ("**运行时异常统计（FirstChance 捕获的只读视图）**。"
                        "BlBridge 在模块加载时订阅 `AppDomain.FirstChanceException`（**.NET 原生事件，"
                        "零 Harmony、不改任何 IL**），把托管异常落进 `<日志目录>\\exceptions.jsonl`"
                        "（追加模式）；本工具返回统计：总共见到多少、多少种、队列积压、丢弃数、出现次数 Top。"
                        "为什么需要它：**ButterLib 的崩溃报告不落盘**（只弹 ImGui/WinForms 窗口，"
                        "实测没有自动写文件的开关），而 BlBridge 是无人值守的 ⇒ 弹窗会卡死流程。"
                        "★ 与 `bl_crash` **互补**：那个读 WER minidump 管「进程已死」，这个管「进程还活着」。"
                        "⚠️ **边界（如实）**：只含**托管**异常；**JIT 期失败**（非法 IL）与"
                        "**原生崩溃**（0xC0000005 等）抓不到 —— 那些不在任何方法体内，只能靠 dump。"
                        "⚠️ `dropped > 0` 表示异常风暴时队列满、**明细不全**（计数仍准确）。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_ui_extensions",
        "description": ("**UIExtenderEx 界面扩展（只读）**：列出每个模块通过 UIExtenderEx "
                        "**改了哪些官方界面**，以及每个界面上挂了哪些扩展类（含 ViewModel mixin 计数）。"
                        "为什么需要它：UIExtenderEx 是**界面扩展层**（本机在跑 v2.13.3，"
                        "自己就打了 19 个 Harmony 补丁），而**多个 mod 改同一个官方界面**时，"
                        "它是唯一能直接回答「谁改了什么」的地方。"
                        "对三合一项目的直接价值：C4（浮点输入框）手工做过 "
                        "`WidgetFactory._builtinTypes` / `WidgetInfo._widgetInfos` 注册，"
                        "而 UIExtenderEx 的 WidgetFactoryManager/WidgetPrefab 补丁**做的是同一件事** "
                        "⇒ 它的实现是本项目那份的成熟版对照物。"
                        "⚠️ 只读 + **零依赖**（反射，不引用其程序集）；未装时返回 available=false。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module": {"type": "string", "description": "只看某个模块的扩展（按 ModuleName 精确匹配，"
                                                            "如 RaiseYourBanner）；不传则列全部"},
                "moviesOnly": {"type": "boolean", "description": "只列界面名，不列每个界面上的扩展类（省 token）"},
                "limit": {"type": "integer", "description": "每个模块最多列几个界面，默认 200"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_mcm_settings",
        "description": ("**MCM 设置表（只读）**：列出 Mod Configuration Menu 里注册的全部设置块与设置项，"
                        "含每项的当前值 / 类型 / 值域 / 是否需重启 / 提示文本。"
                        "为什么需要它：MCM 是**四前置级**组件（本机在跑 v5.12.3，自己就打了 22 个 Harmony 补丁），"
                        "而**面板型 mod 的可调参数最终都落成 MCM 设置项** ⇒ 读得到它等于拿到全场 mod 的参数面。"
                        "对三合一项目的直接价值：C8 要手搓 172 个表格型控件，先看清 MCM 里已有什么，"
                        "才能判断该复用还是该自建。"
                        "⚠️ 只读 + **零依赖**（反射，不引用 MCM 程序集）：不注册设置、不写值、不碰它的 DI 容器；"
                        "MCM 未装时返回 available=false。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "settingsId": {"type": "string", "description": "只看某个设置块（如 ButterLib / CharacterReload）；"
                                                                "不传则列全部块"},
                "summary": {"type": "boolean", "description": "只给每块的项数，不展开每一项（省 token）"},
                "withValues": {"type": "boolean", "description": "是否读当前值，默认 true；false 只给元数据"},
                "limit": {"type": "integer", "description": "每块最多几项，默认 200"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_crash",
        "description": ("**崩溃取证（宿主侧，进程死后也能用）**：解析 Windows 自动落盘的 minidump，"
                        "给出「异常代码 + 崩溃模块+偏移 + 访问的目标地址」，并把它接到 BlBridge 的会话上"
                        "（pid 是否同一个进程 / 崩时在打哪一场 / 最后几行遥测）。"
                        "为什么需要它：`bl_status` 能判 `crashed` 但**不说崩在哪**；而翻 ModLogs 对原生崩溃"
                        "**无效**（RBM 的 Debug.Print 不落那份日志，且 ButterLib/BEW 的托管 Finalizer "
                        "结构上抓不到原生访问违规）。minidump 是公开格式，本工具**纯标准库解析、零依赖**。"
                        "deep=true 时再用真调试器（cdb）拿**符号化托管栈 + FAILURE_BUCKET_ID 崩溃指纹**"
                        "（判「是不是同一个 bug」最硬的判据；需 `winget install Microsoft.WinDbg`，"
                        "没装则优雅退化并给出安装命令，**不会**伪装成「没崩溃」）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "latest": {"type": "boolean", "description": "只看最新一份 dump（默认看最近 8 份）"},
                "pid": {"type": "integer", "description": "只看某个 pid 的 dump（填 bl_status 给的 pid）"},
                "limit": {"type": "integer", "description": "最多几份，默认 8"},
                "stack": {"type": "boolean", "description": "额外做启发式栈扫描（扫栈上落在已知模块内的指针，"
                                                            "回答「调用链里有没有某个 DLL」；不是精确帧）"},
                "deep": {"type": "boolean", "description": "用 cdb 拿符号化托管栈 + 崩溃指纹（每份约 5~30s；"
                                                           "首次拉 MS 符号较慢）。找不到 cdb 时会明确说明原因"},
                "cdb": {"type": "string", "description": "指定 cdb.exe 路径（默认自动探测，含 WinDbg Appx）"},
                "path": {"type": "string", "description": "直接指定某个 .dmp 路径"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_json_health",
        "description": ("**IPC 响应完整性体检（宿主侧，只读，不碰游戏）**：全量解析 "
                        "`<日志目录>\\commands\\done\\*.json`，找出**畸形 JSON** 并给出原始上下文与字节偏移。"
                        "为什么需要它：响应侧的 JSON 写坏了 ⇒ MCP 客户端**一直等到超时**，"
                        "而响应其实早就写好了 ⇒ 症状看起来像「游戏主线程卡死」，"
                        "根因却在 `Jw.Esc` **只转义、不加引号**（交接日志 §3.3 同一条；"
                        "`src/PatchProbe.cs:325-331` 有源码侧同坑注释）。"
                        "为什么单读一个文件发现不了：实测 12991 个响应里只有 5 个坏的（0.0385%），"
                        "按文件名看不出异常、单文件读一次基本撞不上 ⇒ 只有**全量解析**才看得见。"
                        "判据 `stillLive`：最新 20 个文件里还有畸形 ⇒ 缺陷**仍活着**（不是历史遗留），"
                        "此时请对照 `bl_build_check` 看进程内 DLL 与磁盘源码是否一致。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dir": {"type": "string", "description": "要扫描的目录（默认 BlBridge 的 commands\\done）"},
                "logDir": {"type": "string", "description": "日志目录（与 bl_status 的 logDir 同口径，用于推导 done 目录）"},
                "limit": {"type": "integer", "description": "最多返回几条坏文件明细，默认 20"},
            },
        },
    },
    {
        "name": "bl_ipc_replay",
        "description": ("**IPC 响应缓存检索（宿主侧，只读，不碰游戏）**：从 "
                        "`<日志目录>\\commands\\done\\` 的 **12991 份历史响应**里按条件捞回**完整响应体**。"
                        "为什么通用手段不行：**响应体里不含 method 名**（只有 process/result/error/id）"
                        "⇒ 想按工具名检索，**必须**先读动作账本 `commands/actions.jsonl` 拿 "
                        "`id -> method` 映射再来取（实测 2490 条账本 id **全部**能连上响应）。"
                        "本轮为了定位「`CryptographicException` 的 44 次在第几个响应里」，"
                        "手工 grep 了 12991 个文件 —— 本工具把它变成一次调用。"
                        "两种模式：`method=` 走账本（⚠️ 只覆盖有账本的那部分，"
                        "实测 2490/12991 ≈ 19%，工具会如实报 `ledgerCoverage`）；"
                        "`grep=` 在响应原文里搜（覆盖**全部**，但慢）。"
                        "典型用法：查某次失败调用的**完整错误消息**（账本只记 code，响应有全文）、"
                        "复盘上次会话某工具返回了什么。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {"type": "string",
                           "description": "按账本里的方法名过滤（如 get_patches / start_battle / get_exceptions）。"
                                          "只在有账本记录的响应里找；查不到时会提示改用 grep"},
                "grep": {"type": "string",
                         "description": "在响应原文里做子串匹配（覆盖全部响应，不依赖账本）"},
                "okOnly": {"type": "boolean",
                           "description": "true=只看成功 / false=只看失败（按信封 ok；不传=都看）"},
                "limit": {"type": "integer", "description": "最多返回几份响应明细，默认 5"},
                "full": {"type": "boolean", "description": "是否带完整响应体（默认只带前 400 字符）"},
                "newestFirst": {"type": "boolean", "description": "按文件时间倒序，默认 true"},
                "logDir": {"type": "string", "description": "日志目录（与 bl_status 的 logDir 同口径）"},
            },
        },
    },
    {
        "name": "bl_exception_detail",
        "description": ("**异常采集口径说明 + 记录读取（宿主侧，只读，不碰游戏）**："
                        "解释 `<日志目录>\\exceptions.jsonl` 的**记录口径**并读明细。"
                        "为什么必需——该文件的口径**多处反直觉，直接读会得出错误结论**："
                        "① **行数 != 出现次数**（去重键 =(类型|栈首帧)，且**只在首次出现时落盘**）"
                        "⇒ 实测 `CryptographicException` 只有 4 行而真实 44 次；"
                        "② `seq` 是**排空那刻的全局快照**、不是序号（**多条可共享同一 seq**）；"
                        "③ `stackHead` **只有 1 帧**，且常是 BCL **抛助记帧**（如 "
                        "`ThrowCryptographicException`，**与调用者无关**）⇒ 这类异常**来源不可判定**"
                        "（源码注释称'排空时取完整栈'，但**该代码不存在**）；"
                        "④ `t=session` 分段 = **游戏进程重启** ⇒ 跨段累加 `seen` 是错的；"
                        "⑤ 盲区：**只记托管异常**，JIT 期失败与原生崩溃抓不到（那两类用 `bl_crash`）。"
                        "频次请用 `bl_exceptions` 的 `top[].count`；历史响应见 `bl_ipc_replay`。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string",
                         "description": "只看这个异常类型（子串匹配，如 Cryptographic / HarmonyException）"},
                "limit": {"type": "integer", "description": "最多列几条明细，默认 20"},
                "explain": {"type": "boolean",
                            "description": "是否附口径说明（默认 true —— 本工具的主价值）"},
                "logDir": {"type": "string", "description": "日志目录（与 bl_status 的 logDir 同口径）"},
            },
        },
    },
    {
        "name": "bl_build_check",
        "description": ("核对「源码 → 构建产物 → 部署文件 → 进程内 DLL」四段是否一致。用于回答三件事："
                        "① 改了代码没重新构建（stale_source）；② 构建了但没部署（stale_deploy）；"
                        "③ 部署了但游戏没重启、进程里还是旧 DLL（game_not_restarted）。"
                        "只读，不改任何文件。跑批量测试前建议先跑它。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_config",
        "description": ("显示 MCP 侧的有效配置与来历（环境变量 / blbridge.json / 默认值分别给了什么），"
                        "并列出配置文件里的非法项（逐项忽略、附原因）。只读。"
                        "注意：游戏端另有 blbridge_game.json（采样间隔等），那个改完需要重启游戏。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_run_batch",
        "description": ("按计划文件批量跑 N 场 AI 对 AI 战斗（阶段 2④「一条命令跑 N 场」）。"
                        "支持换边双跑：plan 里两个 config 的 attacker/defender 对调即可。"
                        "dryRun=true 时只返回将执行的命令、不碰游戏。"
                        "需要游戏在跑且停在自定义战斗界面。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan": {"type": "string", "description": "跑批计划 JSON 路径"},
                "out": {"type": "string", "description": "runs.json 输出路径（供 bl_batch_report 用）"},
                "dryRun": {"type": "boolean", "description": "只打印计划，不执行"},
                "battlesDir": {"type": "string", "description": "战斗日志目录（默认 Documents 下）"},
            },
            "required": ["plan"],
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_batch_report",
        "description": ("A/B 对比报告（阶段 2④）。主指标 = **满编窗口**（第一例击杀之前的双方实际扣血占比，"
                        "此时双方严格等编）；全程口径会被幸存者偏差污染，仅作对照。"
                        "强制输出 均值 ± 标准差 + 95%CI，且样本少于 3 局时**拒绝下结论**。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "manifest": {"type": "string", "description": "bl_run_batch 产出的 runs.json"},
                "a": {"type": "string", "description": "A 组：文件 / 目录 / 逗号分隔"},
                "b": {"type": "string", "description": "B 组：同上"},
                "labelA": {"type": "string", "description": "A 组标签"},
                "labelB": {"type": "string", "description": "B 组标签"},
                "out": {"type": "string", "description": "报告输出路径（markdown）"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_lookup_troop",
        "description": ("查兵种：校验 id 是否存在、按 id 模糊搜索、按文化筛选。"
                        "数据来自 BannerlordSage 的索引库（只读，只索引官方 XML）。"
                        "没装 BannerlordSage 时返回 available=false —— 属软依赖，不影响其他工具。"
                        "注意：search 的 pattern 走 SQL LIKE（% 是通配符，如 imperial_%），"
                        "传裸词（如 legionary）会返回空结果 —— 那不是「没这个兵种」；"
                        "culture 区分大小写，需与库中一致（empire 或 Culture.empire）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "check": {"type": "array", "items": {"type": "string"},
                          "description": "要校验的兵种 id 列表，如 [\"imperial_legionary\"]"},
                "search": {"type": "string", "description": "按 id 模糊搜索，% 为通配符，如 imperial_%"},
                "culture": {"type": "string",
                            "description": "配合 search 按文化过滤，empire 或 Culture.empire 均可；"
                                           "区分大小写（EMPIRE 会得到空结果）"},
                "limit": {"type": "integer", "description": "search 返回条数，默认 20"},
                "status": {"type": "boolean", "description": "只做索引可用性自检"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_blockade",
        "description": ("关隘封锁候选生成（离线、只读，对应 tools/bl_blockade.py）。"
                        "给 MapBlockade（城池关隘 mod）算「哪座城该封哪几块地图面片」，"
                        "输出候选 + 有效性证据 + 冲突，**由你裁决**后再落盘 map_blockades.xml。"
                        "⚠️ 分工是刻意的：几何/图论由本工具算（确定性、可复现），"
                        "「该不该设关隘」由你按 isBorder/effect/type/冲突来判 —— "
                        "10408 个面片的坐标不该进上下文，你也判不动。"
                        "三层判据要一起看：mode=pass 表示**真的封得住**（BFS 实测封锁后邻城不可达），"
                        "mode=zoc 只是城门口控制区面片（作者人工数据里大多是这种，"
                        "墙长中位数才 4.7，根本封死不了通道）；isBorder=邻接别国，"
                        "是「该不该设」最强的单一信号（人工已标记城 88% 是边境城）。"
                        "⏱ 首次调用要 30~40s（解析拓扑 + 建邻接 + 全城扫描），结果按参数缓存，"
                        "后续同参数调用毫秒级；换参数或 refresh=true 才重算。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "candidates", "emit"],
                           "description": "status=只查输入可用性；candidates=出候选；emit=按裁决落盘。默认 candidates"},
                "city": {"type": "string", "description": "只看这一座城（id 或名字子串）"},
                "types": {"type": "string",
                          "description": "参与的节点类型，默认 castle,town（村庄不设关隘，是 MapBlockade 自己的规则）"},
                "mode": {"type": "string", "enum": ["pass", "zoc"],
                         "description": "只返回这一类的城（pass=真封得住 / zoc=只有控制区面片）"},
                "limit": {"type": "integer", "description": "最多返回几座，默认 40（按 pass 优先、关宽升序）"},
                "full": {"type": "boolean",
                         "description": "带上每块候选面片的明细（fi/地形/面积/通路数）。默认 false —— 全量明细会撑爆上下文"},
                "adopt": {"type": "object",
                          "description": ("action=emit 时的裁决结果：{settlement_id: {keep:bool, faces?:[fi]}}。"
                                          "faces 省略=采纳全部候选；keep=false=该城不设关隘。不给 adopt 则写全部候选。")},
                "out": {"type": "string", "description": "action=emit 的输出路径（map_blockades.xml）"},
                "refresh": {"type": "boolean", "description": "忽略缓存重算"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_launch_game",
        "description": ("无人值守启动 Bannerlord（走 BLSE）并**进到自定义战斗界面**，分两步："
                        "① 起游戏、自动应答两个模态弹窗（Safe Mode -> 否；Mod change detected -> 确定），"
                        "成功判据是**游戏窗口出现**；"
                        "② 轮询到**主菜单真的就绪**（1s 一轮；实测 ~10s）→ 期间跳过场动画 → "
                        "默认只截图即返回（await_confirm），你确认后再调 bl_open_ui → "
                        "之后 **1 秒**一轮等 activeState 变成 CustomBattleState（这一步官方状态本身要 ~30s 才落地）。"
                        "⚠️ 必须分两步：窗口出现时游戏还在加载/启动动画里，那时 open_ui 只会拿到 `wrong_state`。"
                        "返回里的 `enterCustomBattle.timeline` 是逐步时间线。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeoutSec": {"type": "integer", "description": "等待游戏窗口的上限秒数，默认 150"},
                "enterCustomBattle": {"type": "boolean",
                                      "description": "第二步：等主菜单 + ESC 跳过场 + 进自定义战斗（默认 true）。false = 只启动，自己再调 bl_open_ui"},
                "autoOpen": {"type": "boolean",
                             "description": ("第二步里是否**自动**执行 open_ui（默认 false）。"
                                             "⚠️ 启动期 fire open_ui 会崩游戏（2026-09-25 真机：cleanExit=false + "
                                             "rgl_log 崩溃栈），所以默认只截图并返回 await_confirm，由调用方看完再调 bl_open_ui。"
                                             "true 时会先套 minStartupSec 安全下限（默认 20s）再 fire")},
                "uiId": {"type": "string", "description": "第二步要进的入口 id，默认 CustomBattle"},
                "menuTimeoutSec": {"type": "integer", "description": "等主菜单就绪的上限秒数，默认 120"},
                "entryTimeoutSec": {"type": "integer", "description": "open_ui 后等 CustomBattleState 的上限秒数，默认 60"},
                "minStartupSec": {"type": "number",
                                  "description": ("autoOpen=true 的安全下限秒数（默认 20）。"
                                                  "默认路径（不自动 fire）**不受它约束**：主菜单就绪即返回"
                                                  "（实测 ~10s；2026-09-27 前固定等 45s，白等 35s）")},
                "skipModuleList": {"type": "boolean",
                                   "description": "调试用：不带 _MODULES_ 列表启动（结果是 no-mods 模式）"},
                "excludeModules": {"type": "array", "items": {"type": "string"},
                                   "description": ("启动时**排除**的模块名，用于 A/B 对照（同一次启动只差一个模块）。"
                                                   "名字必须与列表里完全一致，拼错会直接启动失败（不会静默忽略）。"
                                                   "例：[\"RTSCamera\",\"RTSCamera.CommandSystem\"] "
                                                   "= 关掉 RTSCamera，好让兜底相机（spectate）露出来")},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_desktop_windows",
        "description": ("列出当前可见的顶层窗口：标题 / 进程号 / **物理像素**坐标与尺寸（本机 3840x2160@150%，"
                        "已 DPI 校正）。用于判断游戏是否在跑、窗口是否在前台、拿 windowId 给其它工具用。"
                        "后端 = gridhand（外部 CLI，软依赖；未安装时返回 available=false 与安装提示）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "按标题子串过滤（大小写不敏感），如 bannerlord"},
                "limit": {"type": "integer", "description": "最多返回条数，默认 40"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_desktop_screenshot",
        "description": ("截屏（全屏或某个窗口），可叠加 16x9 的**带标签网格**（每格中心有十字准星），"
                        "也可放大到某一格看子网格。返回 PNG 路径 —— 用 view_image 读它即可看到画面。"
                        "为什么用网格：agent 从截图目测像素坐标误差极大（本机 150% 缩放时曾整体偏 1.5 倍，"
                        "把两次点击砸到主菜单的「退出游戏」上）。先截图读格名，再用 bl_desktop_click 点该格。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "windowId": {"type": "integer", "description": "窗口 id（bl_desktop_windows 拿）；不传则截全屏"},
                "grid": {"type": "boolean", "description": "叠加带标签网格，默认 true"},
                "cell": {"type": "string", "description": "放大到某格看子网格，如 C5；支持递归 B2.C1"},
                "out": {"type": "string", "description": "输出 PNG 路径；默认写到日志目录的 ui\\ 子目录"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_desktop_click",
        "description": ("按**格子名**点击（如 C5；跨格用 I5+I6；递归用 B2.C1），或按物理像素坐标点击。"
                        "一条命令完成「移动+点击」。可选先聚焦目标窗口（focus=true，默认 true）。"
                        "已知边界：合成输入能驱动官方界面（实测点主菜单 C5 一次即中），但**到不了我们自写"
                        "Gauntlet 层的按钮**（同一坐标手搓注入与 gridhand 都不响应）——"
                        "所以对自写面板，正解是文件 IPC（open_ui），不要指望模拟鼠标。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cell": {"type": "string", "description": "格子名，如 C5；跨格用 I5+I6；递归放大用 B2.C1"},
                "windowId": {"type": "integer", "description": "目标窗口 id（格子名需要它来换算；聚焦也用它）"},
                "button": {"type": "string", "description": "left / right，默认 left"},
                "focus": {"type": "boolean", "description": "点击前先把该窗口提到前台，默认 true"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_desktop_key",
        "description": ("按键或输入文本：keys 走组合键（enter / ctrl+a / alt+f4），text 走文本输入。"
                        "Bannerlord 与 BLSE 的对话框用得上（例如 Mod change detected 的按钮是中文「确定」，"
                        "WM_COMMAND IDOK 无效、回车有效）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "组合键，如 enter / ctrl+a / alt+f4"},
                "text": {"type": "string", "description": "要输入的文本"},
                "windowId": {"type": "integer", "description": "目标窗口 id（可选）"},
            },
            "additionalProperties": False,
        },
    },
]

# ── 工具集分组（BLBRIDGE_TOOLSET=core+config，默认全开）──────────────────
# 目的：减少常驻在模型上下文里的工具名数量。分组只决定 tools/list 暴露哪些工具，
# 不改变任何工具的行为。
TOOL_GROUPS = {
    "core": [
        "bl_status", "bl_battle_status", "bl_start_battle", "bl_wait_for_state", "bl_abort",
        "bl_order", "bl_control_agent", "bl_launch_game", "bl_skip_video",
        "bl_list_ui", "bl_open_ui", "bl_close_ui", "bl_fast_forward",
        "bl_get_screen", "bl_get_viewmodel_property", "bl_get_inventory",
        "bl_list_saves", "bl_load_save", "bl_campaign_time",
        "bl_campaign_overview", "bl_list_kingdoms", "bl_list_clans",
        "bl_list_settlements", "bl_list_parties", "bl_campaign_log",
    ],
    "config": [
        "bl_read_config", "bl_apply_config", "bl_rts_config", "bl_apply_rts_config",
        "bl_ghost_camera", "bl_camera_speed", "bl_cheat_mode",
    ],
    "lab": [
        "bl_list_battles", "bl_analyze", "bl_read_events", "bl_run_batch", "bl_batch_report",
        "bl_lookup_troop", "bl_blockade", "bl_build_check", "bl_config", "bl_crash", "bl_patches",
        "bl_json_health", "bl_ipc_replay", "bl_exception_detail",
        "bl_mcm_settings", "bl_ui_extensions", "bl_exceptions", "bl_patch_failures",
    ],
    "desktop": [
        "bl_desktop_windows", "bl_desktop_screenshot", "bl_desktop_click", "bl_desktop_key",
    ],
}


def _active_tool_names():
    """当前启用的工具名集合；None = 不过滤（全开）。

    未知组名不静默忽略：拼错会让整组工具凭空消失且极难察觉，所以显式抛错。
    """
    raw = (os.environ.get("BLBRIDGE_TOOLSET") or "").strip()
    if not raw or raw.lower() in ("all", "*"):
        return None
    wanted = [g.strip().lower() for g in raw.replace("+", ",").split(",") if g.strip()]
    unknown = [g for g in wanted if g not in TOOL_GROUPS]
    if unknown:
        raise ValueError(
            "BLBRIDGE_TOOLSET 含未知组名: %s（可用: %s）"
            % (", ".join(unknown), ", ".join(sorted(TOOL_GROUPS)))
        )
    names = set()
    for group in wanted:
        names.update(TOOL_GROUPS[group])
    return names


def active_tools():
    names = _active_tool_names()
    if names is None:
        return TOOLS
    return [t for t in TOOLS if t["name"] in names]


# ─────────────────────────────────────────────────────────────────────
# 关隘候选（后端 = tools/bl_blockade.py）
#
# 为什么必须缓存：全量扫描 120 座城要 30~40s（解析拓扑 + 建面片邻接 + 每城 BFS 实测
# 封锁效果）。MCP 是长驻进程，没缓存的话每次调工具都重扫一遍，根本没法交互。
# 缓存键 = (types, topology 路径+大小+mtime)，任一变化自动失效；
# city / mode / limit / full 只是**视图过滤**，不进缓存键（过滤是毫秒级的）。
# ─────────────────────────────────────────────────────────────────────
_BLOCKADE_CACHE = {"key": None, "payload": None}


def _blockade_payload(types=("castle", "town"), refresh=False):
    """拿全量候选（带缓存）。返回 dict；不可用时带 ok=False + reason。"""
    if bl_blockade is None:
        return {"ok": False, "reason": "bl_blockade 模块导入失败：%s" % _BLOCKADE_IMPORT_ERR}
    try:
        topo_path = bl_blockade.DEFAULT_TOPOLOGY
        paths_path = bl_blockade.DEFAULT_PATHS
        st = os.stat(topo_path)
        key = (tuple(sorted(types)), topo_path, paths_path,
               st.st_size, int(st.st_mtime), os.path.isfile(paths_path))
    except OSError as e:
        return {"ok": False, "reason": "拓扑/通路文件不可用：%s" % e}

    if not refresh and _BLOCKADE_CACHE["key"] == key and _BLOCKADE_CACHE["payload"]:
        return _BLOCKADE_CACHE["payload"]

    try:
        topo = bl_blockade.Topo.load(topo_path)
        paths = bl_blockade.load_paths(paths_path)
    except bl_blockade.TopoError as e:
        return {"ok": False, "reason": str(e)}

    t0 = time.time()
    gen = bl_blockade.Generator(topo, paths)
    results = []
    for nd in topo.nodes:
        if nd["type"] in types:
            results.append(gen.candidates(nd))
    ok = [r for r in results if r.get("ok")]
    payload = {
        "ok": True,
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsedSec": round(time.time() - t0, 1),
        "topology": topo_path,
        "paths": paths_path,
        "faceCount": len(topo.faces),
        "settlementCount": len(ok),
        "passCount": sum(1 for r in ok if r.get("mode") == "pass"),
        "skipped": [{"id": r["id"], "name": r["name"], "reason": r.get("reason")}
                    for r in results if not r.get("ok")],
        "conflicts": bl_blockade.find_conflicts(results),
        "results": ok,
    }
    _BLOCKADE_CACHE["key"] = key
    _BLOCKADE_CACHE["payload"] = payload
    return payload


def _blockade_view(r, full=False):
    """把一条结果压成**裁决所需的最小字段**。

    full=false 时丢掉 candidates 明细：120 座城 × 每座 ~10 块面片的明细会让一次
    tools/call 的返回大到没法读，而裁决根本用不到那些 —— 真要下钻时用 city 过滤再开 full。
    """
    v = {
        "id": r["id"], "name": r["name"], "type": r["type"],
        "kingdom": r.get("kingdom"), "mode": r.get("mode"),
        "passWidth": r.get("passWidth"),
        "isBorder": r.get("isBorder"),
        "foreignKingdoms": r.get("foreignKingdoms"),
        "neighborCount": r.get("neighborCount"),
        "effect": r.get("effect"),
        "cutCount": r.get("cutCount"),
        "chosenFaces": r.get("chosenFaces"),
        "outline": r.get("outline"),
        "reason": r.get("reason"),
    }
    if full:
        v["candidates"] = r.get("candidates")
        v["cutDestinations"] = r.get("cutDestinations")
        v["reachBefore"] = r.get("reachBefore")
        v["reachAfter"] = r.get("reachAfter")
        v["shrink"] = r.get("shrink")
    return v


# ─────────────────────────────────────────────────────────────────────
# 桌面 / 游戏 GUI 能力（后端 = tools/bl_launch.ps1 + gridhand 外部 CLI）
#
# 为什么不直接接一个 computer-use MCP（如 betrayzl/windows-computer-use-mcp）：
#   它的 Structured Mode 依赖 UI Automation，而游戏（Bannerlord 的 Gauntlet UI）不在
#   accessibility tree 里；Visual Mode 就是我们已有的「截图 + SendInput 模拟输入」，
#   实测同源注入到不了自写面板层。所以能力面借鉴、实现自持，避免 Node/Rust 依赖。
#   详见 docs/prototype-ui-probe-2026-09-25.md
# ─────────────────────────────────────────────────────────────────────

_GRIDHAND_CANDIDATES = [
    os.path.join(os.path.expanduser("~"), ".cargo", "bin", "gridhand.exe"),
    r"D:\Program Files\Rust\cargo\bin\gridhand.exe",
    "gridhand.exe",
    "gridhand",
]


def gridhand_path():
    """定位 gridhand 可执行文件；找不到返回 None（软依赖，不让其它工具受影响）。"""
    import shutil as _shutil
    for cand in _GRIDHAND_CANDIDATES:
        if os.path.isabs(cand):
            if os.path.isfile(cand):
                return cand
        else:
            found = _shutil.which(cand)
            if found:
                return found
    return None


def _ui_dir():
    d = os.path.join(log_dir(), "ui")
    if not os.path.isdir(d):
        try:
            os.makedirs(d)
        except OSError:
            pass
    return d


def _run_ps(script, extra_args=None, timeout=200.0):
    """跑一个 PowerShell 脚本（显式 UTF-8，见 AGENTS.md 编码规则）。"""
    import subprocess
    cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", script]
    if extra_args:
        cmd += list(extra_args)
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %.0fs" % timeout
    except OSError as exc:
        return 127, "", str(exc)


def _run_gridhand(sub_args, timeout=90.0):
    """跑 gridhand；返回 (rc, stdout, stderr)。未安装时 rc=127。"""
    import subprocess
    exe = gridhand_path()
    if not exe:
        return 127, "", "gridhand not found"
    cmd = [exe] + list(sub_args)
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %.0fs" % timeout
    except OSError as exc:
        return 127, "", str(exc)


def _gridhand_json(sub_args, timeout=90.0):
    """跑 gridhand 并把 stdout 解析成 JSON（去掉 ANSI 高亮码）。"""
    import re as _re
    rc, out, err = _run_gridhand(sub_args, timeout=timeout)
    clean = _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out).strip()
    data = None
    for line in clean.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                break
            except ValueError:
                continue
    if data is None:
        try:
            data = json.loads(clean)
        except ValueError:
            data = None
    return rc, data, clean, err


# ── 启动的第二步：等主菜单就绪 → 跳启动动画 → 进自定义战斗（v0.8.18）──────────
# 存在理由（2026-09-25 用户实测反馈）：
#   `bl_launch.ps1` 的 "LAUNCH OK" 只代表**游戏窗口出现** —— 那时还在加载/启动动画里，
#   紧接着调 open_ui 只会拿到 wrong_state，于是每次都要"等上一次失败"才轮到真正的主菜单。
#   这里把启动拆两步：① 起 BLSE（仍由 bl_launch.ps1 做）；② 轮询主菜单就绪、按 ESC 跳动画、
#   进自定义战斗，然后**每 5 秒**确认一次到底进去了没有。
# 判据（不猜、不静默）：
#   "主菜单就绪" = 控制通道能应答 且 `moduleLoaded=true` 且 `activeState` ∈ {"", "InitialState"}
#   且 `topScreen` 非空、不含 "Loading" 且 options 里有 `CustomBattle` 且 `disabled=false`
#   （加载/安装期间这些入口是禁用的）。
#
# ⚠️ v0.8.22：**两处必须同改**（C# 的 `ScenarioRunner.ActiveGameStateName()` 与本处）。
#   真机实测（2026-09-25 23:00，v0.8.21）：主菜单的活动状态名就是 **`InitialState`**；
#   而 C# 侧原先只看 `Game.Current.GameStateManager`，主菜单 `Game.Current` 是 null ⇒ 恒返回空串。
#   把 C# 换成"静态优先"后，主菜单会**开始返回 `InitialState`** —— 若这里仍把"非空"一律当"不在主菜单"，
#   就会把**真主菜单判成未就绪**，整个启动流程反被打死。所以两个名字都接受：
#     ""             = 拿不到状态名（启动初期/过渡期；这一档还叠着 `topScreen` 那道硬门）
#     "InitialState" = 主菜单（真机实测值）
_MAIN_MENU_ACTIVE_STATES = ("", "InitialState")
_INTRO_ESC_BUDGET_SEC = 40.0


def _send_key(keys, window_id=None):
    sub = ["key", "press", str(keys)]
    if window_id:
        sub += ["--window-id", str(int(window_id))]
    rc, data, clean, err = _gridhand_json(sub, timeout=30.0)
    return rc, clean


def _game_window_id():
    rc, data, clean, err = _gridhand_json(["windows", "list"], timeout=30.0)
    if rc != 0 or not data:
        return None
    wins = data.get("windows") or []
    for w in wins:
        if w.get("isGame"):
            return w.get("id")
    # ⚠️ gridhand 的 `windows list` **不给** isGame（那是 bl_desktop_windows 自己算的），
    # 所以这里按标题兜底认（真机 2026-09-25 踩过：只认 isGame ⇒ 恒返回 None）。
    for w in wins:
        t = w.get("title") or ""
        if "Mount and Blade II Bannerlord" in t or "Bannerlord - Singleplayer" in t:
            return w.get("id")
    return None


def _menu_state(ui):
    """→ (ready, why)：只看控制通道当场给的东西。"""
    if not ui.get("moduleLoaded"):
        return False, "BlBridge 模块还没挂上（moduleLoaded=false）"
    # ⭐ 关键硬判据（v0.8.20，学自 BUTR/Bannerlord.GABS 的 `wait_for_state` 注释）：
    # 「状态串匹配」不够 —— 那个注释的原话是 `Campaign.Current` 会在 GameLoadingScreen 过渡
    # **完成之前**就变成非 null，只看状态会**过早返回、后续操作踩空**；所以他们额外检查
    # `ScreenManager.TopScreen` 的类名里**不含 "Loading"**。我们同款：
    # 启动期 `activeState` 也是空串（就像主菜单），只凭它判断会让 open_ui 在启动动画里执行
    # 初始状态选项 —— 2026-09-25 真机把游戏打崩过（见 PROGRESS §二十九 9.6）。
    top_screen = ui.get("topScreen") or ""
    if "Loading" in top_screen:
        return False, "还在加载屏（topScreen=%s）" % top_screen
    # 真机实测（2026-09-25 22:51，v0.8.20）：启动后 ~2 s 时 `topScreen` 是**空串**，
    # 而那时 options 已经有 9 项、activeState 也是空串 ⇒ 光看那两项会误判"主菜单就绪"
    # （正是把游戏打崩的那次的条件）。真正的菜单有屏幕名：`GauntletInitialScreen`。
    if not top_screen:
        return False, "顶层还没有屏幕（topScreen 为空 = 启动初期，屏幕栈还没压上来）"
    st = ui.get("activeState") or ""
    if st not in _MAIN_MENU_ACTIVE_STATES:
        return False, ("不在主菜单（activeState=%s；本判据只接受 %s）"
                       % (st, " / ".join(repr(x) for x in _MAIN_MENU_ACTIVE_STATES)))
    cbs = [o for o in (ui.get("options") or []) if o.get("id") == "CustomBattle"]
    if not cbs:
        return False, "主菜单里还没有 CustomBattle 入口"
    if cbs[0].get("disabled"):
        return False, "CustomBattle 入口仍被禁用（%s）" % (cbs[0].get("disabledReason") or "无原因")
    return True, "主菜单就绪（topScreen=%s）" % top_screen


_ANSWER_PS1 = os.path.join(_TOOLS_DIR, "bl_launch.ps1")

# v0.8.44：进自定义战斗的**落地判据**用后缀匹配，别写死单一状态名。
# 依据（2026-10-05 真机）：装了 NavalDLC 时从主菜单 `open_ui(CustomBattle)` 落的是
# `NavalCustomBattleState`（官方把自定义战斗入口劫持到海战选兵界面），
# 而纯原版下是 `CustomBattleState`。旧实现 `if state == "CustomBattleState"` 只认后者
# ⇒ 带 NavalDLC 的机器上**永远等不到**。后缀匹配同时覆盖两者，且不会误认
# `MapState` / `CampaignState`（它们不以 CustomBattleState 结尾）。
_CUSTOM_BATTLE_STATE_SUFFIX = "CustomBattleState"
_last_answer_try = {"at": 0.0}


def _answer_late_dialogs(answer_sec=6, min_gap=15.0):
    """读档/加载期的迟到模态弹窗（如"模组不匹配"存档确认）应答器。

    背景（2026-09-27，用户真机指认）：bl_launch.ps1 的应答只覆盖**启动后**的 grace period，
    而 ContinueCampaign 的"模组不匹配"弹窗出现在读档阶段 —— 那时早没人守着，弹窗就挂在那
    等人点，bl_mcp 这边只看到 list_ui 一直 no_response（上一版把它误判成"加载慢 ~165s"）。

    做法：子进程跑 `bl_launch.ps1 -AnswerSec N`（独立应答模式，只扫弹窗不启游戏），
    点掉词表命中的按钮（OK/确定/是/Yes）。节流：min_gap 秒内只试一次。
    返回被点击的按钮日志（列表，可能为空）；None = 本次被节流或执行失败。
    """
    import time as _t
    now = _t.time()
    if now - _last_answer_try["at"] < min_gap:
        return None
    _last_answer_try["at"] = now
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", _ANSWER_PS1, "-AnswerSec", str(int(answer_sec))],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=answer_sec + 30)
        out = p.stdout.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - 应答器失败不影响等待主流程（下一轮可再试）
        return None
    return [l.strip() for l in out.splitlines() if ("BM_CLICK" in l or "VK_RETURN" in l)]


def _enter_custom_battle(ui_id="CustomBattle", menu_timeout=120.0, entry_timeout=60.0,
                         poll=1.0, skip_intro=True, auto_open=False, min_startup_sec=20.0):
    """启动第二步：等主菜单 → （必要时发 ESC 跳过场）→ open_ui → 每 poll 秒确认一次。

    时间账（2026-09-27 真机 + 对照上游 GABS 后收紧，原先是 poll=5.0 / 下限 45s）：
      • `min_startup_sec` 下限**只作用于 auto_open=True 的路径** —— 它防的是"自动 fire open_ui"，
        而 await_confirm 路径根本不发 open_ui（只截图），等下限纯属白等
        （实测：菜单 9.9s 就绪却拖到 45.0s 才返回，白白多等 35s）。
      • 下限本身 45 → 20s：45s 是 v0.8.18 定的，**早于** v0.8.20 的主菜单硬判据
        （topScreen=GauntletInitialScreen + CustomBattle 入口 enabled，就是为那次启动期
        open_ui 崩溃加的）；硬判据已在，下限退居"兜底"。上游 GABS 的 wait_for_state 用
        500ms 轮询、无任何下限（判据同为 topScreen 不含 Loading）。
      • 轮询 2s/5s → 1s：list_ui(status) 是轻量读（sceneLimit=1），不值得每 5s 看一眼。
      • **落地判据不能只认 `CustomBattleState`**（v0.8.44 真机修正）：
        装了 **NavalDLC** 时，从主菜单 `open_ui(CustomBattle)` 落的是
        **`NavalCustomBattleState`**（官方把自定义战斗入口劫持到海战选兵界面）。
        旧实现硬编码 `if state == "CustomBattleState"` ⇒ **永远等不到**、只能等到超时，
        于是 `--auto-open` 修好之后仍报 `wait_custom_battle`（2026-10-05 实测 140 s 超时）。
        ⇒ 改为**后缀匹配**（见 `_CUSTOM_BATTLE_STATE_SUFFIX`），同时容纳两者。
        这个缺陷原先被掩盖：手写脚本用 `-match 'CustomBattleState'`（子串）侥幸绕开。
    """
    import time as _time
    timeline = []
    t0 = _time.time()
    wid = _game_window_id()
    can_key = bool(gridhand_path())
    if skip_intro and not can_key:
        timeline.append("没装 gridhand ⇒ 这一轮不发 ESC（启动动画只能自己等人跳）")

    ready = False
    why = "超时"
    esc = 0
    video_skips = 0
    first_seen = None
    while _time.time() - t0 < menu_timeout:
        # sceneLimit=1：轮询只要状态位，不需要 314 行场景表（省掉每轮 ~35 KB）
        resp, err = send_command("list_ui", {"sceneLimit": 1}, timeout=15)
        if err or not resp:
            why = "控制通道还没应答：%s" % (err or "空响应")
        else:
            ui = resp.get("result") or {}
            if first_seen is None:
                first_seen = ("t=%.1fs list_ui: moduleLoaded=%s activeState=%r topScreen=%r options=%d"
                              % (_time.time() - t0, ui.get("moduleLoaded"),
                                 ui.get("activeState") or "", ui.get("topScreen") or "",
                                 len(ui.get("options") or [])))
                timeline.append(first_seen)
            ready, why = _menu_state(ui)
        if ready:
            timeline.append("t=%.1fs 主菜单就绪（skip_video %d 次 / ESC %d 次）"
                            % (_time.time() - t0, video_skips, esc))
            break
        # 没就绪：**优先**问"现在是不是开场动画"，是就直接结束它（判据硬、无副作用）；
        # 不是视频时才退回模拟 ESC。两者都只在启动后的时间窗内做。
        if skip_intro and (_time.time() - t0) < _INTRO_ESC_BUDGET_SEC:
            r, e = send_command("skip_video", {}, timeout=10)
            body = ((r or {}).get("result") or {}) if not e else {}
            if body.get("ok"):
                video_skips += 1
                timeline.append("t=%.1fs skip_video 成功（状态=%s）"
                                % (_time.time() - t0, body.get("activeState")))
            elif can_key:
                _send_key("esc", wid)
                esc += 1
        _time.sleep(1.0)

    if not ready:
        return {"ok": False, "phase": "wait_menu", "reason": why, "escSent": esc,
                "seconds": round(_time.time() - t0, 1), "timeline": timeline}

    # ── 安全门（真机血的教训，别删）──────────────────────────────────────
    # 2026-09-25 22:39:53：`open_ui` 在**启动动画期间**被接受并执行了
    # `ExecuteInitialStateOptionWithId(CustomBattle)`，21 秒后游戏崩溃
    # （`rgl_log_91600.txt` 22:40:14 有崩溃栈；`bridge_status.json` → `cleanExit:false`）。
    # ⇒ 只靠"activeState 为空"判主菜单**不够**（启动期同样是空串）。
    #   默认**不自动 fire**：先截图、把路径带回去，由调用方看一眼再决定；
    #   无人值守要显式传 autoOpen=True，且仍受 min_startup_sec 下限保护。
    #   （下限只在这一条路生效 —— await_confirm 不 fire open_ui，等下限没有保护对象。
    #     2026-09-27：45s → 20s，理由见函数 docstring；真机 A/B 见 PROGRESS §三十 8。）
    if auto_open:
        waited = _time.time() - t0
        if waited < min_startup_sec:
            _time.sleep(min_startup_sec - waited)
            timeline.append("t=%.1fs 已等足启动下限 %.0fs（auto_open 安全门）"
                            % (_time.time() - t0, min_startup_sec))
        t1 = _time.time()
        resp, err = send_command("open_ui", {"uiId": ui_id}, timeout=20)
    else:
        wid2 = wid or _game_window_id()
        shot_path = None
        if can_key and wid2:
            r = call_tool("bl_desktop_screenshot",
                          {"windowId": wid2, "grid": False,
                           "out": os.path.join(log_dir(), "ui", "enter_battle_confirm.png")})
            shot_path = r.get("path")
        return {"ok": False, "phase": "await_confirm", "uiId": ui_id,
                "reason": ("主菜单就绪即返回（不自动 fire open_ui）。看一眼截图确认是主菜单后，"
                           "再调 bl_open_ui；要无人值守就传 autoOpen=true（那才会套 %.0fs 安全下限）。"
                           % min_startup_sec),
                "screenshot": shot_path, "escSent": esc,
                "seconds": round(_time.time() - t0, 1), "timeline": timeline}

    if err:
        return {"ok": False, "phase": "open_ui", "reason": err, "escSent": esc,
                "seconds": round(_time.time() - t0, 1), "timeline": timeline}
    body = resp.get("result") or {}
    # 不静默：open_ui 失败时把 code/error 原样带出来（真机踩过：requested=None 时不知道原因）
    timeline.append("t=%.1fs open_ui(%s) → requested=%s%s"
                    % (_time.time() - t0, ui_id, body.get("requested"),
                       "" if body.get("requested") else " code=%s error=%s"
                       % (body.get("code"), body.get("error"))))
    if not body.get("requested"):
        menu_back = None
        r0, e0 = send_command("list_ui", {}, timeout=15)
        if not e0 and r0:
            menu_back = (r0.get("result") or {}).get("activeState")
        return {"ok": False, "phase": "open_ui", "uiId": ui_id, "state": menu_back,
                "reason": "open_ui 没有被接受：code=%s error=%s（activeState=%r）"
                          % (body.get("code"), body.get("error"), menu_back),
                "escSent": esc, "seconds": round(_time.time() - t0, 1), "timeline": timeline}

    state = None
    poll_errors = 0
    while _time.time() - t1 < entry_timeout:
        _time.sleep(poll)
        # timeout=6：真机实测（2026-09-27，B 组）open_ui 后 ~30s 里官方状态在主线程加载、
        # 控制通道**不应答** —— 轮询超时若是 15s，两次 no_response 就吃掉 30s，
        # "1s 轮询"名存实亡。短超时 + 失败小睡，让落地后最多 1s 就能发现。
        r2, e2 = send_command("list_ui", {"sceneLimit": 1}, timeout=6)
        if e2 or not r2:
            poll_errors += 1
            # 不静默：轮询失败也要记账（否则最后只看到 state=None，无从判断），
            # 但加载期会连着失败几十秒，只记第 1 条 + 每 10 条报一次数
            if poll_errors == 1 or poll_errors % 10 == 0:
                timeline.append("t=+%.1fs list_ui 失败×%d（多半是加载期主线程忙）：%s"
                                % (_time.time() - t1, poll_errors, e2 or "空响应"))
            # no_response 有两种：主线程真忙（加载），或者被一个没人应答的模态弹窗挡住
            # （2026-09-27：ContinueCampaign 的"模组不匹配"确认框）—— 后者等人点，等再久也没用，
            # 扫一轮把"是/OK"点掉（bl_launch.ps1 -AnswerSec 独立应答模式，节流 15s）。
            clicked = _answer_late_dialogs()
            if clicked:
                timeline.append("t=+%.1fs 应答了迟到的弹窗：%s"
                                % (_time.time() - t1, "; ".join(clicked)[:180]))
            _time.sleep(2.0)
            continue
        state = (r2.get("result") or {}).get("activeState") or ""
        timeline.append("t=+%.1fs activeState=%r" % (_time.time() - t1, state))
        if state.endswith(_CUSTOM_BATTLE_STATE_SUFFIX):
            return {"ok": True, "phase": "in_custom_battle", "uiId": ui_id, "state": state,
                    "escSent": esc, "seconds": round(_time.time() - t0, 1), "timeline": timeline}
    return {"ok": False, "phase": "wait_custom_battle", "uiId": ui_id, "state": state,
            "pollErrors": poll_errors,
            "reason": ("等了 %.0f 秒还没看到 *%s（轮询清单见 timeline；"
                       "带 NavalDLC 时官方入口会落 NavalCustomBattleState，判据已含它）"
                       % (entry_timeout, _CUSTOM_BATTLE_STATE_SUFFIX)),
            "escSent": esc, "seconds": round(_time.time() - t0, 1), "timeline": timeline}


def call_tool(name, args):
    args = args or {}
    if name == "bl_status":
        p = status_path()
        if not os.path.isfile(p):
            return {"loaded": False, "logDir": log_dir(),
                    "hint": "没有 bridge_status.json —— 遥测模块可能未安装/未启用，或本局还没进过战斗"}
        with io.open(p, "r", encoding="utf-8-sig", errors="replace") as fh:
            st = json.load(fh)
        st["loaded"] = True
        bl = bl_analyze.list_battles(log_dir())
        st["battleCount"] = len(bl)
        st["latestBattle"] = bl[-1] if bl else None
        st["buildCheck"] = build_check()
        st["sessionDiagnosis"] = run_state_diagnosis()
        return st

    if name == "bl_config":
        cfg, problems = load_config()
        return {"ok": True,
                "configPath": config_path(),
                "configExists": os.path.isfile(config_path()),
                "fileValues": cfg,
                "problems": problems,
                "effective": {
                    "logDir": log_dir(),
                    "gameDir": game_dir(),
                    "logDirSource": "env:BLBRIDGE_LOG_DIR" if os.environ.get("BLBRIDGE_LOG_DIR")
                                    else ("file:logDir" if cfg.get("logDir") else "default"),
                    "gameDirSource": "env:BANNERLORD_DIR" if os.environ.get("BANNERLORD_DIR")
                                     else ("file:gameDir" if cfg.get("gameDir") else "default"),
                    "waitForStateTimeoutSec": _cfg("waitForStateTimeoutSec", 180),
                    "statusTimeoutSec": _cfg("statusTimeoutSec", 10),
                },
                "hint": "改配置文件后无需重启游戏；但游戏端的 blbridge_game.json（采样间隔等）需要重启游戏"}

    if name == "bl_list_battles":
        items = bl_analyze.list_battles(log_dir())
        limit = int(args.get("limit") or 20)
        return {"logDir": log_dir(), "count": len(items), "battles": items[-limit:][::-1]}

    if name == "bl_analyze":
        f = args.get("file") or bl_analyze.latest_battle(log_dir())
        if not f or not os.path.isfile(f):
            return {"ok": False, "error": "没有可分析的战斗日志", "logDir": log_dir()}
        res = bl_analyze.analyze(f)
        if args.get("format") == "json":
            return {"ok": True, "result": res}
        return {"ok": True, "file": f, "report": bl_analyze.report(res)}

    if name == "bl_read_events":
        f = args.get("file") or bl_analyze.latest_battle(log_dir())
        if not f or not os.path.isfile(f):
            return {"ok": False, "error": "没有可读取的战斗日志"}
        want = args.get("type")
        limit = int(args.get("limit") or 50)
        offset = int(args.get("offset") or 0)
        ev = bl_analyze.load_events(f)
        if want:
            ev = [e for e in ev if e.get("t") == want]
        page = ev[offset:offset + limit]
        return {"ok": True, "file": f, "total": len(ev), "offset": offset,
                "count": len(page), "events": page}

    if name == "bl_read_config":
        return {"ok": True, "values": read_config(args.get("paths"))}

    if name == "bl_apply_config":
        edits = args.get("edits") or []
        if not edits:
            return {"ok": False, "error": "edits 不能为空"}
        return apply_config(edits, dry_run=bool(args.get("dry_run")))

    # ── v0.8.15：RTSCamera 配置（B 方案：只当它的参数管理员，不碰它的代码）──────
    if name == "bl_rts_config":
        try:
            values = bl_rts.read(args.get("keys"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "path": bl_rts.config_path()}
        out = {"ok": True, "path": bl_rts.config_path(), "values": values}
        if args.get("presets"):
            out["presets"] = dict(bl_rts.PRESETS)
        return out

    if name == "bl_apply_rts_config":
        try:
            if args.get("preset"):
                return bl_rts.apply_preset(str(args["preset"]), dry_run=bool(args.get("dry_run")))
            edits = args.get("edits") or []
            if not edits:
                return {"ok": False, "error": "需要 preset，或非空的 edits"}
            return bl_rts.apply(edits, dry_run=bool(args.get("dry_run")),
                                allow_missing=bool(args.get("allow_missing")))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "path": bl_rts.config_path()}

    if name == "bl_crash":
        # 宿主侧工具：解析 WER 自动落盘的 minidump。**不碰游戏、不需要游戏在跑**
        # （进程已经死了才有 dump —— 这正是它存在的意义）。
        # deep=true 时再用 cdb 拿符号化栈（可选；未装 cdb 会优雅退化并说明原因）。
        import bl_crash as _bc
        return _bc.build_report(
            limit=int(args.get("limit") or 8),
            latest=bool(args.get("latest")),
            pid=args.get("pid"),
            want_stack=bool(args.get("stack")),
            path=args.get("path"),
            deep=bool(args.get("deep")),
            cdb=args.get("cdb"),
        )

    if name == "bl_json_health":
        # 宿主侧工具：全量解析 commands\done\*.json，找畸形 JSON。
        # **不碰游戏、不需要游戏在跑**（与 bl_crash 同类）。
        import bl_json_health as _jh
        return _jh.build_report(
            directory=args.get("dir"),
            log_dir=args.get("logDir"),
            limit=int(args.get("limit") or 20),
        )

    if name == "bl_ipc_replay":
        # 宿主侧工具：检索 commands\done\ 的历史响应（+ actions.jsonl 连接）。
        # **不碰游戏、不需要游戏在跑**（与 bl_crash / bl_json_health 同类）。
        import bl_ipc_replay as _rp
        return _rp.build_report(
            method=args.get("method"),
            log_dir=args.get("logDir"),
            limit=int(args.get("limit") or 5),
            grep=args.get("grep"),
            ok_only=args.get("okOnly"),
            full=bool(args.get("full")),
            newest_first=bool(args.get("newestFirst", True)),
        )

    if name == "bl_exception_detail":
        # 宿主侧工具：解释 exceptions.jsonl 的口径并读明细。
        # **不碰游戏、不需要游戏在跑**（与 bl_crash / bl_json_health / bl_ipc_replay 同类）。
        import bl_exception_detail as _ed
        return _ed.build_report(
            log_dir=args.get("logDir"),
            type_filter=args.get("type"),
            limit=int(args.get("limit") or 20),
            explain=bool(args.get("explain", True)),
        )

    if name == "bl_build_check":
        return {"ok": True, "buildCheck": build_check()}

    if name == "bl_ghost_camera":
        mode = args.get("mode") or "status"
        params = {} if mode == "status" else {"mode": mode}
        resp, err = send_command("ghost_camera", params, timeout=10)
        if err:
            return {"ok": False, "error": err}
        body = resp.get("result") or {}
        hint = None
        if not body.get("ok") and body.get("code") == "not_in_mission":
            hint = "先开一场战斗再切幽灵相机（主菜单/界面里没有 MissionScreen 可改）"
        return {"ok": bool(body.get("ok")), "mode": mode,
                "ghostCamera": body.get("ghostCamera"), "inMission": body.get("inMission"),
                "error": body.get("error"), "note": body.get("note"), "hint": hint,
                "response": resp}

    if name == "bl_control_agent":
        mode = args.get("mode") or "take"
        if mode not in ("take", "release", "status"):
            return {"ok": False,
                    "error": "mode 只接受 take / release / status，收到 %r" % (mode,)}
        params = {"mode": mode}
        if args.get("side"):
            params["side"] = args["side"]
        if args.get("formation") not in (None, ""):
            params["formation"] = str(args["formation"])
        if args.get("troop"):
            params["troop"] = args["troop"]
        if args.get("agentIndex") is not None:
            params["agentIndex"] = int(args["agentIndex"])
        resp, err = send_command("control_agent", params, timeout=15)
        if err:
            return {"ok": False, "error": err}
        body = resp.get("result") or {}
        out = {"ok": bool(body.get("ok")), "mode": body.get("mode"), "how": body.get("how"),
               "target": body.get("target"), "mainAgentBefore": body.get("mainAgentBefore"),
               "mainAgentAfter": body.get("mainAgentAfter"), "restored": body.get("restored"),
               "oldHandedToAI": body.get("oldHandedToAI"),
               "previousHandedToAI": body.get("previousHandedToAI"),
               "screenReset": body.get("screenReset"),
               "cameraFollow": body.get("cameraFollow"),
               "playerControlError": body.get("playerControlError"),
               "originalIndex": body.get("originalIndex"),
               "playerTeam": body.get("playerTeam"),
               "aliveCandidatesOnPlayerTeam": body.get("aliveCandidatesOnPlayerTeam"),
               "code": body.get("code"), "error": body.get("error"), "note": body.get("note"),
               "response": resp}
        if not body.get("ok"):
            code = body.get("code")
            if code == "no_mission":
                out["hint"] = "先开一场战斗再接管（bl_start_battle / 游戏内自定义战斗界面）"
            elif code == "no_player_team":
                out["hint"] = "这一场没有玩家方（纯 AI 场次里用 playerSide 指定一方为玩家侧再开）"
            elif code == "not_player_team":
                out["hint"] = "只能接管玩家方的兵（RTSCamera 同样拒绝敌人）：换 side / 换目标"
            elif code == "no_target":
                out["hint"] = ("没找到存活目标：等士兵进场后再试，或用 mode=status 看"
                               " aliveCandidatesOnPlayerTeam 与 mainAgent")
            elif code in ("target_inactive", "original_inactive"):
                out["hint"] = "目标已阵亡，换一个（死亡瞬间的 agent 还在列表里但 IsActive()=false）"
            elif code == "already_main_agent":
                out["hint"] = "目标已经是主角色了，换个目标"
            elif code == "no_original":
                out["hint"] = "本次进程内还没 take 过，没有可换回的原始主角色；直接 take 别的即可"
            elif code == "unsupported_param":
                out["hint"] = "agentId / slot / mount / weapon 还没实现，别当成已生效"
        return out

    if name == "bl_order":
        # 四者互斥（都往同一个 Formation.SetMovementOrder 写）；做成"列全冲突项"而不是两两判断。
        # ⚠️ agent 下标可以是 **0** ⇒ 用 `not in (None, "")` 而不是真值判断（`0` 是假值，
        # 用真值判断会把 `targetAgent=0` 静默当成"没给"，正是本项目最忌讳的静默）。
        given = [k for k in ("movement", "position", "target", "targetAgent")
                 if args.get(k) not in (None, "")]
        if len(given) > 1:
            return {"ok": False,
                    "error": "movement / position / target / targetAgent 互斥"
                             "（一个编队只能有一个 movement order）：%s"
                             % " / ".join(str(args.get(k)) for k in given)}
        if not (given or args.get("arrangement") or args.get("firing") or args.get("riding")):
            return {"ok": False,
                    "error": "movement / position / target / targetAgent / arrangement / firing / riding "
                             "至少要给一个"}
        params = {}
        if args.get("movement"):
            params["movement"] = args["movement"]
        if args.get("position"):
            params["position"] = args["position"]
        if args.get("target"):
            params["target"] = args["target"]
        if args.get("targetAgent") is not None and args.get("targetAgent") != "":
            # 发**裸数字**（C# 侧 Jmini.Int 读它；字符串形式会被判非法）
            params["targetAgent"] = int(args["targetAgent"])
        if args.get("arrangement"):
            params["arrangement"] = args["arrangement"]
        if args.get("firing"):
            params["firing"] = args["firing"]
        if args.get("riding"):
            params["riding"] = args["riding"]
        if args.get("side"):
            params["side"] = args["side"]
        if args.get("formation") not in (None, ""):
            params["formation"] = str(args["formation"])
        if args.get("detachAI") is not None:
            params["detachAI"] = bool(args["detachAI"])
        resp, err = send_command("order", params, timeout=10)
        if err:
            return {"ok": False, "error": err}
        body = resp.get("result") or {}
        out = {"ok": bool(body.get("ok")), "movement": body.get("movement"),
               "position": body.get("position"), "target": body.get("target"),
               "targetAgent": body.get("targetAgent"),
               "targetAgentTroop": body.get("targetAgentTroop"),
               "targetSide": body.get("targetSide"), "side": body.get("side"),
               "team": body.get("team"), "detachAI": body.get("detachAI"),
               "arrangement": body.get("arrangement"), "firing": body.get("firing"),
               "riding": body.get("riding"),
               "appliedCount": body.get("appliedCount"), "totalUnits": body.get("totalUnits"),
               "emptyFormations": body.get("emptyFormations"),
               "pendingSpecsUpdated": body.get("pendingSpecsUpdated"),
               "applied": body.get("applied"), "code": body.get("code"),
               "error": body.get("error"), "note": body.get("note"), "response": resp}
        if not body.get("ok"):
            code = body.get("code")
            if code == "no_mission":
                out["hint"] = ("先开一场战斗再改令（bl_start_battle / 游戏内自定义战斗界面）；"
                               "mission 之外碰 MovementOrder 会永久污染该类型，所以这里直接拒。")
            elif code == "bad_movement":
                out["hint"] = "movement 只接受 charge/advance/fallback/stop/retreat（hold 已移除，改用 stop）"
            elif code == "bad_position":
                out["hint"] = ('position 写成 "x,y" 或 "x,y,z"（英文逗号、单位米、各分量 |值| ≤ 10000；'
                               "z 省略 = 0，引擎按地面补 Z）")
            elif code == "bad_formation":
                out["hint"] = ("formation 只接受 Infantry/Ranged/Cavalry/HorseArcher/Skirmisher"
                               " 或下标 0~4")
            elif code == "bad_riding":
                out["hint"] = "riding 只接受 free/mount/dismount（引擎 RidingOrder 只有这三档）"
            elif code in ("bad_target", "no_target_formation", "target_formation_empty", "no_enemy_team"):
                out["hint"] = ("target 是**敌方编队**（与 side 相对的那一方）：名字或下标 0~4；"
                               "该编队不存在或已空就拒（不静默换成普通冲锋）")
            elif code == "bad_target_agent":
                out["hint"] = "targetAgent 要传**敌方单位的 agent 下标**（非负整数；从遥测或 control-agent 取）"
            elif code in ("no_target_agent", "target_agent_inactive", "target_agent_not_enemy"):
                out["hint"] = ("targetAgent 找不到可用目标（下标不存在 / 已阵亡 / 不是敌方）："
                               "下标按场次重号，换一场要重新取；本通道只接受**敌方存活单位**")
            elif code == "target_agent_no_entity":
                out["hint"] = "该单位没有可用 GameEntity（AgentVisuals 为空）⇒ 无法绑定 AttackEntity 目标"
            elif code == "unsupported_param":
                out["hint"] = ("movement / position / target / targetAgent / arrangement / firing / riding "
                               "都已实现；本错误说明请求里带了尚未实现的参数，别当成已生效")
            elif code == "no_target":
                out["hint"] = "这一方当前没有有兵的编队（等士兵进场后再发）"
        return out

    if name == "bl_camera_speed":
        mode = args.get("mode") or "status"
        if mode not in ("status", "shift", "base", "rts", "boost", "probe"):
            return {"ok": False, "error": "mode 只接受 status/shift/base/rts/boost/probe，收到 %r" % (mode,)}
        if mode in ("shift", "base", "rts", "boost") and args.get("value") is None:
            return {"ok": False, "error": "mode=%s 需要 value（shift 为 ≥1 的整数，其余 0.1~1000）" % mode}
        if mode == "status":
            params = {}
        elif mode == "probe":
            params = {"mode": "probe"}
            if args.get("action"):
                params["action"] = args["action"]
        else:
            params = {"mode": mode, "value": args.get("value")}
        resp, err = send_command("camera_speed", params, timeout=10)
        if err:
            return {"ok": False, "error": err}
        body = resp.get("result") or {}
        out = {"ok": bool(body.get("ok")), "mode": mode, "response": resp}
        for k in ("legs", "leg", "value", "requested", "readBack", "changed", "note",
                  "code", "error", "succeeded", "inMission",
                  "phase", "seconds", "distance", "speed", "startPos", "endPos"):
            if k in body:
                out[k] = body[k]
        if not body.get("ok"):
            if body.get("code") == "not_in_mission":
                out["hint"] = "先开一场战斗（或停在部署界面）再改相机速度"
            elif body.get("code") == "leg_unavailable":
                out["hint"] = ("mode=rts 需要本机装了 RTSCamera，且正处在战斗里"
                               "（它只在 mission 内把相机控制器登记进 ACameraControllerManager）")
            elif body.get("code") == "bad_value":
                out["hint"] = "shift 只接受 ≥1 的整数；base/rts 接受 0.1~1000"
            elif body.get("code") == "no_probe_start":
                out["hint"] = "先调一次 mode=probe（不带 action）记起点，飞一段后再带 action=end"
        return out

    if name == "bl_skip_video":
        resp, err = send_command("skip_video", {}, timeout=10)
        if err:
            return {"ok": False, "error": err}
        body = resp.get("result") or {}
        out = {"ok": bool(body.get("ok")), "response": resp}
        for k in ("activeState", "via", "error", "code", "note"):
            if k in body:
                out[k] = body[k]
        if not body.get("ok"):
            out["hint"] = ("只有当活动状态是 VideoPlaybackState（开场动画）时才能跳；"
                           "其余时刻会明确报 not_video（附当前状态名 + via=状态机是从哪拿到的）；"
                           "拿不到状态机时报 no_state_manager")
        return out

    if name == "bl_cheat_mode":
        mode = args.get("mode") or "status"
        if mode not in ("status", "on", "off", "toggle"):
            return {"ok": False, "error": "mode 只接受 status/on/off/toggle，收到 %r" % (mode,)}
        params = {} if mode == "status" else {"mode": mode}
        resp, err = send_command("cheat_mode", params, timeout=10)
        if err:
            return {"ok": False, "error": err}
        body = resp.get("result") or {}
        out = {"ok": bool(body.get("ok")), "mode": mode, "response": resp}
        for k in ("cheatMode", "nativeConfig", "gameCurrent", "requested", "changed",
                  "note", "code", "error"):
            if k in body:
                out[k] = body[k]
        if not body.get("ok"):
            out["hint"] = ("写失败要点名：报 write_failed 说明游戏版本改了 "
                           "NativeConfig.CheatMode 的形态（setter 与后备字段都试过）")
        return out

    if name == "bl_battle_status":
        resp, err = send_command("status", {}, timeout=float(_cfg("statusTimeoutSec", 10)))
        if err:
            # 拿不到状态时，顺手给出"是不是崩了"的判定 —— 否则只剩一句超时
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        return {"ok": True, "response": resp}

    if name == "bl_start_battle":
        # preflight：构建链条不一致就别开打（照 Coop "incompatible build fails before an expensive launch"）
        bc = build_check()
        if bc.get("code") in ("no_deployed_dll", "manifest_missing", "manifest_invalid",
                              "stale_deploy", "game_not_restarted", "game_running_other_build"):
            return {"ok": False,
                    "error": "构建一致性检查未通过：" + (bc.get("detail") or bc.get("code")),
                    "buildCheck": bc,
                    "hint": "需要：完全退出游戏 → 跑 build.ps1 -Deploy → 重启游戏。细节看 bl_build_check"}
        params = {
            "attackerTroop": args.get("attackerTroop"),
            "attackerCount": int(args.get("attackerCount") or 20),
            "defenderTroop": args.get("defenderTroop"),
            "defenderCount": int(args.get("defenderCount") or 20),
            "scene": args.get("scene") or "battle_terrain_a",
            "durationCapSec": int(args.get("durationCapSec") or 600),
            # 对称化开关（v0.7.3）：默认双方对称冲锋，消除"攻方冲/守方站"的方向偏差
            "orders": args.get("orders") or "charge",
            "playerSide": args.get("playerSide") or "attacker",
        }
        # v0.8.14：上帝视角（只在开启时才写进请求 —— 保持旧调用的请求字节逐字不变，
        # 这个项目的"离线自测抓不到、真机才炸"的坑大多来自请求面悄悄漂移）。
        if args.get("spectate"):
            params["spectate"] = True
        # ── v0.8.10：补齐 CLI 侧早已支持的参数（真机回归 F7：MCP 的参数面窄于 CLI，
        #          导致多轮与多兵种组走不了 MCP）────────────────────────────────
        # 校验口径与 bl_cmd.py **逐字对齐**：组 DSL 在本地先校验，非法绝不透传
        # （游戏端也会拒，但先在本地说清楚能省一次往返，且消息带纠错提示）。
        for arg_key in ("attackerGroups", "defenderGroups"):
            raw = args.get(arg_key)
            if not raw:
                continue
            try:
                bl_common.parse_squad_groups(raw)
            except ValueError as e:
                return {"ok": False, "error": "%s 解析失败：%s" % (arg_key, e)}
            params[arg_key] = raw
        if args.get("rounds"):
            params["rounds"] = int(args["rounds"])
            if args.get("roundEndAlive") is not None:
                params["roundEndAlive"] = int(args["roundEndAlive"])
            if args.get("roundSwap") is True:
                params["roundSwap"] = "true"
            if args.get("roundSpawnAttacker"):
                params["roundSpawnAttacker"] = str(args["roundSpawnAttacker"])
            if args.get("roundSpawnDefender"):
                params["roundSpawnDefender"] = str(args["roundSpawnDefender"])
        if args.get("randomSeed") is not None:
            params["randomSeed"] = int(args["randomSeed"])
        if args.get("allowAnyState") is True:
            params["allowAnyState"] = "true"
        # 靶场参数（v0.8.0~v0.8.2），口径与 bl_cmd.py 完全一致：
        #   布尔走 Jmini.Str ⇒ 必须发字符串 "true"；护甲走 Jmini.Num ⇒ 必须发**数字**。
        # 解析失败一律拒绝，不静默跳过（静默丢弃曾让 9 场护甲实验整批作废）。
        if args.get("dummySide") and args.get("dummySide") != "none":
            params["dummySide"] = str(args.get("dummySide"))
        if args.get("freezeDummies") is True:
            params["freezeDummies"] = "true"
        if args.get("unlimitedAmmo") is True:
            params["unlimitedAmmo"] = "true"
        if args.get("dummyArmor"):
            try:
                params.update(bl_common.parse_dummy_armor(args.get("dummyArmor")))
            except ValueError as e:
                return {"ok": False, "error": "--dummy-armor 解析失败：%s" % e}
        if args.get("dummyBodyItem"):
            params["dummyBodyItem"] = str(args.get("dummyBodyItem"))
        # ── v0.8.41：战术档位 + 环境旋钮 ─────────────────────────────────
        # 数值参数发**裸数字**（C# 侧 Jmini.Num 只吃数字字符）；名字/枚举在本地先校验（GC3）。
        for arg_key in ("attackerTacticLevel", "defenderTacticLevel"):
            if args.get(arg_key) is not None:
                try:
                    params[arg_key] = bl_common.check_tactic_level(args[arg_key], arg_key)
                except ValueError as e:
                    return {"ok": False, "error": str(e)}
        if args.get("terrain"):
            try:
                params["terrain"] = bl_common.parse_terrain(args["terrain"])
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        if args.get("randomTerrainSeed") is not None:
            params["randomTerrainSeed"] = int(args["randomTerrainSeed"])
        if args.get("aiFriendlyFireMultiplier") is not None:
            ff = float(args["aiFriendlyFireMultiplier"])
            if ff < 0.0 or ff > 1.0:
                return {"ok": False, "error": "aiFriendlyFireMultiplier 只接受 0..1"}
            params["aiFriendlyFireMultiplier"] = ff
        if args.get("keepCorpses") is True:
            params["keepCorpses"] = "true"
        if args.get("sceneLevel") is not None:
            lv = int(args["sceneLevel"])
            if lv < 1 or lv > 3:
                return {"ok": False, "error": "sceneLevel 只接受 1..3"}
            params["sceneLevel"] = lv
        if args.get("timeOfDay") is not None:
            tod = float(args["timeOfDay"])
            if tod < 0.0 or tod > 24.0:
                return {"ok": False, "error": "timeOfDay 只接受 0..24"}
            params["timeOfDay"] = tod
        if not params["attackerTroop"] or not params["defenderTroop"]:
            return {"ok": False, "error": "必须提供 attackerTroop 与 defenderTroop"}

        # 兵种 id 前置校验。为什么放在发命令之前：id 打错时 C# 侧静默走 fallback，
        # 日志里看不出来，代价是白等一整场对局（10 分钟级）。
        # 为什么又留了 skipTroopCheck 出口：索引只覆盖官方 XML（xml_scope=official），
        # 第三方模组的兵种会被判成"不存在"，那种情况必须能跳过。
        troop_check = None
        if not args.get("skipTroopCheck"):
            # v0.8.10（F7）：组模式下单值的 attackerTroop/defenderTroop 会被游戏端**忽略**，
            # 拿它们去查索引是查错对象 —— 改成校验真正生效的那一组（与 bl_cmd.py 同口径）。
            ids = []
            ids += ([g["troop"] for g in bl_common.parse_squad_groups(args["attackerGroups"])]
                    if args.get("attackerGroups") else [params["attackerTroop"]])
            ids += ([g["troop"] for g in bl_common.parse_squad_groups(args["defenderGroups"])]
                    if args.get("defenderGroups") else [params["defenderTroop"]])
            troop_check = bl_sage.check_troops(ids)
            if troop_check.get("available") and troop_check.get("missing"):
                return {"ok": False,
                        "error": "兵种 id 在索引里不存在：%s" % ", ".join(troop_check["missing"]),
                        "missing": troop_check["missing"],
                        "suggestions": dict((m, bl_sage.suggest_troops(m)) for m in troop_check["missing"]),
                        "hint": ("索引只覆盖官方 XML；若该兵种确实来自第三方模组，"
                                 "用 skipTroopCheck=true 跳过校验。可用 bl_lookup_troop 查兵种")}

        # v0.8.15（B 方案）：开战前按需套用 RTSCamera 预设。必须在发请求**之前**做 ——
        # 实测 RTSCamera 每场战斗开始时读一次自己的配置，所以"改完立刻开战"就对本场生效
        # （也正因为它运行中会覆写配置，这一步不能提前太久）。
        rts_result = None
        if args.get("rtsPreset"):
            try:
                rts_result = bl_rts.apply_preset(str(args["rtsPreset"]))
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": "rtsPreset 套用失败：%s" % e}

        resp, err = send_command("start_battle", params, timeout=60)
        if err:
            return {"ok": False, "error": err, "rtsConfig": rts_result}
        body = resp.get("result") or {}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "start_battle 被拒绝",
                    "code": e.get("code"), "outcomeUncertain": e.get("outcomeUncertain"),
                    "rtsConfig": rts_result, "response": resp}
        # v0.8.32：开战 DSL 的 `formation` 是死字段（只回显、不决定编队）⇒ C# 侧开战前已比对
        # "请求值 vs 兵种实际编队"，不一致放进 formationWarnings（不阻断开战）。带上来，
        # 让调用方一眼看到"我写的 formation 没生效"，而不是等到发现兵不动才回头查。
        return {"ok": True, "state": body.get("state"), "accepted": body.get("accepted"),
                "buildCheck": bc,
                "troopCheck": troop_check,
                "rtsConfig": rts_result,
                "formationWarnings": body.get("formationWarnings") or [],
                "buildWarning": (bc.get("detail") if bc.get("code") == "stale_source" else None),
                "hint": "用 bl_wait_for_state(state=ended) 等它打完", "response": resp}

    if name == "bl_wait_for_state":
        import time as _t
        target = args.get("state") or "ended"
        timeout = float(args.get("timeoutSec") or _cfg("waitForStateTimeoutSec", 180))
        poll = float(args.get("pollSec") or 1)
        stall_tolerance = float(args.get("stallToleranceSec") or 15)
        deadline = _t.time() + timeout
        last = None
        stall_since = None
        while _t.time() < deadline:
            resp, err = send_command("status", {}, timeout=10)
            if err:
                return {"ok": False, "error": err}
            last = resp
            body = resp.get("result") or {}
            st = body.get("state")
            rd = body.get("readiness") or {}
            if st == target:
                return {"ok": True, "state": st, "readiness": rd, "response": resp}
            if st == "error":
                return {"ok": False, "state": st, "error": "推演进入 error 状态",
                        "readiness": rd, "response": resp}
            # 状态说 running、但引擎已停止推进（黑屏/加载卡住）：早退，别等到 deadline
            # 这种样本是"看起来正常"的无效样本 —— 混进 A/B 会让结论出错
            if st == "running" and rd.get("verdict") == "stalled":
                if stall_since is None:
                    stall_since = _t.time()
                elif (_t.time() - stall_since) > stall_tolerance:
                    return {"ok": False, "state": st, "readiness": rd,
                            "error": ("状态是 running，但引擎已停止推进 %.0fs（stalledSeconds=%s）"
                                      "—— 疑似黑屏/加载卡住，该样本无效" %
                                      (stall_tolerance, rd.get("stalledSeconds"))),
                            "response": resp}
            else:
                stall_since = None
            _t.sleep(poll)
        cur = (last or {}).get("result") or {}
        return {"ok": False, "error": "等待 %s 超时（%.0fs）" % (target, timeout),
                "state": cur.get("state"), "readiness": cur.get("readiness"), "lastResponse": last}

    if name == "bl_fast_forward":
        resp, err = send_command("fast_forward", {"enabled": bool(args.get("enabled"))}, timeout=15)
        if err:
            return {"ok": False, "error": err}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "fast_forward 失败", "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_abort":
        resp, err = send_command("abort", {}, timeout=20)
        if err:
            return {"ok": False, "error": err}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "abort 失败", "response": resp}
        return {"ok": True, "response": resp}

    # ── v0.8.12：游戏内 UI 入口（agent 正门）────────────────────────────
    # 设计依据：AI 不该靠"模拟鼠标"操作 UI（原型轮实测：同一套合成输入，官方主菜单可点、
    # 我们这层因 Gauntlet 事件命中顺序不可点）。正门 = Module.ExecuteInitialStateOptionWithId。
    if name == "bl_list_ui":
        ui_params = {}
        if args.get("scenesMode"):
            ui_params["scenesMode"] = str(args["scenesMode"])
        if args.get("sceneLimit"):
            ui_params["sceneLimit"] = int(args["sceneLimit"])
        resp, err = send_command("list_ui", ui_params, timeout=15)
        if err:
            return {"ok": False, "error": err}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "list_ui 失败", "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_open_ui":
        params = {}
        if args.get("uiId"):
            # 参数名必须是 uiId：控制通道的 Jmini 是扁平读取器，`id` 会被请求信封里的 id 顶掉
            params["uiId"] = str(args["uiId"])
        resp, err = send_command("open_ui", params, timeout=20)
        if err:
            return {"ok": False, "error": err}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "open_ui 被拒绝", "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp,
                "hint": ("入口动作已触发（fire-and-forget）。用 bl_list_ui 看 activeState 是否变成 "
                         "CustomBattleState（官方自定义战斗界面）；不在主菜单时本调用会被主菜单闸门拒绝")}

    if name == "bl_close_ui":
        params = {}
        if args.get("state"):
            params["state"] = str(args["state"])
        resp, err = send_command("close_ui", params, timeout=15)
        if err:
            return {"ok": False, "error": err}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "close_ui 失败", "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    # ── L2 #1：只读 UI 探测（脱壳抄 BUTR/Bannerlord.GABS 的 ui/get_screen / ui/get_viewmodel_property）──
    # 派发分支与 TOOLS 表是一对：只加 TOOLS 不加分支 ⇒ 工具在 tools/list 里看得见、一调就
    # `unknown tool`（2026-09-27 实测踩到）。防回归看 bl_check_dispatch.py。
    if name == "bl_get_screen":
        params = {}
        if args.get("layerFilter"):
            params["layerFilter"] = str(args["layerFilter"])
        resp, err = send_command("get_screen", params, timeout=15)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "get_screen 失败", "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_get_viewmodel_property":
        property_name = str(args.get("propertyName") or "").strip()
        layer_name = str(args.get("layerName") or "").strip()
        if not property_name or not layer_name:
            return {"ok": False,
                    "error": "propertyName 与 layerName 都必填（layerName 从 bl_get_screen 的 layers[].name 取）"}
        params = {"propertyName": property_name, "layerName": layer_name}
        if args.get("subProperties"):
            params["subProperties"] = str(args["subProperties"])
        resp, err = send_command("get_viewmodel_property", params, timeout=15)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "get_viewmodel_property 失败", "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_patch_failures":
        # 走文件 IPC（游戏侧 ExceptionProbe.PatchFailures()）。只读，无参数。
        resp, err = send_command("get_patch_failures", {}, timeout=15)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "get_patch_failures 失败",
                    "code": e.get("code"), "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_exceptions":
        # 走文件 IPC（游戏侧 ExceptionProbe.Summary()）。只读，无参数。
        resp, err = send_command("get_exceptions", {}, timeout=15)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "get_exceptions 失败",
                    "code": e.get("code"), "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_ui_extensions":
        # 走文件 IPC（游戏侧 UiExtendProbe 反射读 UIExtenderEx）。只读，无副作用。
        # ⚠️ 参数名避开信封保留键：`module`/`moviesOnly`/`limit` 都不撞
        #    （保留键 = protocolVersion/id/method/parameters/issuedUtc）。
        params = {}
        if args.get("module"):
            params["module"] = str(args["module"])
        if args.get("moviesOnly") is not None:
            params["moviesOnly"] = bool(args["moviesOnly"])
        if args.get("limit"):
            params["limit"] = int(args["limit"])
        resp, err = send_command("get_ui_extensions", params, timeout=25)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "get_ui_extensions 失败",
                    "code": e.get("code"), "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_mcm_settings":
        # 走文件 IPC（游戏侧 McmProbe 反射读 MCM）。只读，无副作用。
        #
        # ⚠️ **参数名必须避开信封保留键**（`protocolVersion`/`id`/`method`/`parameters`/`issuedUtc`）
        #    —— 本项目踩过两次（`open_ui` 的 id、`bl_patches` 的 method）。
        #    这里用的 `settingsId`/`summary`/`withValues`/`limit` 都不撞。
        #    `bl_patches_selftest.py` 的「保留键不变式」是这条的机器闸门。
        params = {}
        for k in ("settingsId",):
            if args.get(k):
                params[k] = str(args[k])
        if args.get("summary") is not None:
            params["summary"] = bool(args["summary"])
        if args.get("withValues") is not None:
            params["withValues"] = bool(args["withValues"])
        if args.get("limit"):
            params["limit"] = int(args["limit"])
        resp, err = send_command("get_mcm_settings", params, timeout=25)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "get_mcm_settings 失败",
                    "code": e.get("code"), "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_patches":
        # 走文件 IPC（游戏侧 PatchProbe 反射读 Harmony 内省）。只读，无副作用。
        #
        # ⚠️ **参数名坑（本项目第 3 次踩）**：请求信封自带 `"method":"get_patches"`，
        #    而 `Jmini` 是**扁平**读取器（按"第一个 "key""取值，不区分信封与 params）
        #    ⇒ 游戏侧若用 `Jmini.Str(raw, "method")` 会读到**信封里的方法名**，
        #      把过滤器设成 "get_patches" ⇒ 每个补丁都被跳过 ⇒
        #      静默输出 `scannedMethods=N / matchedMethods=0`（看起来像"没有补丁"）。
        #    ⇒ 所以这里发 `targetType`，游戏侧也读 `targetType`。同源前车之鉴：
        #      `open_ui` 的 `id` 撞请求号（AGENTS.md 已记）。
        params = {}
        if args.get("method"):
            params["targetType"] = str(args["method"])
        if args.get("owner"):
            params["owner"] = str(args["owner"])
        if args.get("conflicts") is not None:
            params["conflicts"] = bool(args["conflicts"])
        if args.get("detail") is not None:
            params["detail"] = bool(args["detail"])
        if args.get("limit"):
            params["limit"] = int(args["limit"])
        resp, err = send_command("get_patches", params, timeout=20)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "get_patches 失败",
                    "code": e.get("code"), "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_get_inventory":
        params = {}
        if args.get("limit"):
            params["limit"] = int(args["limit"])
        resp, err = send_command("get_inventory", params, timeout=15)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "get_inventory 失败",
                    "code": e.get("code"), "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_list_saves":
        resp, err = send_command("list_saves", {}, timeout=20)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "list_saves 失败", "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_load_save":
        name_ = str(args.get("name") or "").strip()
        if not name_:
            return {"ok": False,
                    "error": "name 必填（用 bl_list_saves 里的 name，精确匹配）"}
        resp, err = send_command("load_save", {"name": name_}, timeout=30)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "load_save 被拒绝",
                    "code": e.get("code"), "response": resp}
        # C# 侧的失败（save_not_found / load_failed / in_campaign）装在 result.ok=false 里，
        # 信封 ok 仍是 true —— 不看第二层就会把"没启动"报成"启动成功"（自测 2026-09-27 抓到）。
        body = resp.get("result") or {}
        if not body.get("ok"):
            return {"ok": False, "code": body.get("code"),
                    "error": body.get("error") or "load_save 被拒绝", "response": resp}
        return {"ok": True, "result": body, "response": resp,
                "hint": ("异步加载中：轮询 bl_list_ui 等 topScreen 变 *MapScreen"
                         "（读档期的模组不匹配确认框会被自动点『是』）")}

    if name == "bl_campaign_time":
        mode = str(args.get("mode") or "status")
        if mode in ("on", "off"):
            # 显式失败，不静默忽略：老脚本/老文档还在传 on/off，必须当场看见功能已撤。
            return {"ok": False, "code": "keep_awake_removed",
                    "error": ("时间保活（mode=on/off）已在 v0.8.39 移除：用户明确不需要改游戏的时间暂停，"
                              "而且它看不见「失焦自动打开的暂停菜单」那种暂停、还会顶掉手动暂停。"
                              "要「切窗口不弹暂停菜单」请用原版选项 "
                              "BannerlordConfig.StopGameOnFocusLost=false（见 PROGRESS §三十二）。")}
        if mode != "status":
            return {"ok": False, "error": "mode 只接受 status，收到 %r" % (mode,)}
        params = {}
        resp, err = send_command("campaign_time", params, timeout=15)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "campaign_time 失败", "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    # ── A 阶段：战役只读遥测（控制面，对应战场的 get_inventory / list_battles）──
    # C# 侧 no_campaign / *_failed 装在信封 ok=false 里（统一走这里判第二层）。
    if name == "bl_campaign_overview":
        resp, err = send_command("campaign_overview", {}, timeout=20)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "campaign_overview 失败",
                    "code": e.get("code"), "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_list_kingdoms":
        params = {}
        if args.get("limit"):
            params["limit"] = int(args["limit"])
        resp, err = send_command("list_kingdoms", params, timeout=25)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "list_kingdoms 失败",
                    "code": e.get("code"), "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_list_clans":
        params = {}
        if args.get("limit"):
            params["limit"] = int(args["limit"])
        resp, err = send_command("list_clans", params, timeout=25)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "list_clans 失败",
                    "code": e.get("code"), "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_list_settlements":
        params = {}
        if args.get("limit"):
            params["limit"] = int(args["limit"])
        resp, err = send_command("list_settlements", params, timeout=25)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "list_settlements 失败",
                    "code": e.get("code"), "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_list_parties":
        params = {}
        if args.get("limit"):
            params["limit"] = int(args["limit"])
        resp, err = send_command("list_parties", params, timeout=25)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "list_parties 失败",
                    "code": e.get("code"), "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_campaign_log":
        params = {}
        if args.get("count"):
            params["count"] = int(args["count"])
        resp, err = send_command("campaign_log", params, timeout=20)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "campaign_log 失败",
                    "code": e.get("code"), "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    # ── 阶段 2④：批量跑批 + A/B 对比报告 ────────────────────────────────
    # ⚠️ 必须捕获子进程输出：MCP 走 stdio，任何漏到 stdout 的东西都会破坏 JSON-RPC。
    if name == "bl_run_batch":
        plan = args.get("plan")
        if not plan or not os.path.isfile(plan):
            return {"ok": False, "error": "plan 文件不存在: %s" % plan}
        tools_dir = os.path.dirname(os.path.abspath(__file__))
        cmd = [sys.executable, os.path.join(tools_dir, "bl_batch.py"), "--plan", plan]
        if args.get("out"):
            cmd += ["--out", args["out"]]
        if args.get("dryRun"):
            cmd.append("--dry-run")
        if args.get("battlesDir"):
            cmd += ["--battles-dir", args["battlesDir"]]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return {"ok": p.returncode == 0, "exitCode": p.returncode,
                "out": args.get("out"),
                "stdout": (p.stdout or "")[-4000:],
                "stderr": (p.stderr or "")[-1000:]}

    if name == "bl_batch_report":
        tools_dir = os.path.dirname(os.path.abspath(__file__))
        cmd = [sys.executable, os.path.join(tools_dir, "bl_compare.py")]
        if args.get("manifest"):
            cmd += ["--manifest", args["manifest"]]
        if args.get("a"):
            cmd += ["--a", args["a"]]
        if args.get("b"):
            cmd += ["--b", args["b"]]
        if args.get("labelA"):
            cmd += ["--label-a", args["labelA"]]
        if args.get("labelB"):
            cmd += ["--label-b", args["labelB"]]
        if args.get("out"):
            cmd += ["--out", args["out"]]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return {"ok": p.returncode == 0, "exitCode": p.returncode,
                "out": args.get("out"),
                "report": (p.stdout or "")[-8000:],
                "stderr": (p.stderr or "")[-1000:]}

    if name == "bl_lookup_troop":
        if args.get("status"):
            return {"ok": True, "sage": bl_sage.status()}
        ids = args.get("check")
        if ids:
            if not isinstance(ids, list):
                return {"ok": False, "error": "check 必须是字符串数组"}
            chk = bl_sage.check_troops(ids)
            return {"ok": True, "available": chk.get("available"), "reason": chk.get("reason"),
                    "found": chk.get("found"), "missing": chk.get("missing")}
        if args.get("search"):
            st = bl_sage.status()
            rows = (bl_sage.search(args.get("search"), args.get("culture"),
                                   int(args.get("limit") or 20)) if st.get("available") else [])
            return {"ok": True, "available": st.get("available"), "reason": st.get("reason"),
                    "count": len(rows), "troops": rows}
        return {"ok": True, "sage": bl_sage.status()}

    if name == "bl_blockade":
        if bl_blockade is None:
            return {"ok": False, "error": "bl_blockade 不可用（导入失败）：%s" % _BLOCKADE_IMPORT_ERR}
        action = args.get("action") or "candidates"
        types = tuple(t.strip() for t in (args.get("types") or "castle,town").split(",") if t.strip())

        if action == "status":
            try:
                topo = bl_blockade.Topo.load(bl_blockade.DEFAULT_TOPOLOGY)
                paths = bl_blockade.load_paths(bl_blockade.DEFAULT_PATHS)
            except bl_blockade.TopoError as e:
                return {"ok": False, "error": str(e)}
            return {"ok": True, "topology": bl_blockade.DEFAULT_TOPOLOGY,
                    "faces": len(topo.faces), "vertices": len(topo.verts),
                    "nodes": len(topo.nodes), "edges": len(topo.edges),
                    "paths": len(paths),
                    "cached": _BLOCKADE_CACHE["payload"] is not None}

        payload = _blockade_payload(types, refresh=bool(args.get("refresh")))
        if not payload.get("ok"):
            return {"ok": False, "error": payload.get("reason")}

        if action == "emit":
            out = args.get("out")
            if not out:
                return {"ok": False, "error": "emit 必须给 out（输出 xml 路径）"}
            adopt = args.get("adopt")
            if adopt is not None and not isinstance(adopt, dict):
                return {"ok": False, "error": "adopt 必须是对象：{settlement_id:{keep,faces}}"}

            class _R(object):
                pass
            # emit_xml 需要能按裁决顺序遍历（先到先得），这里直接复用缓存里的 results
            topo = bl_blockade.Topo.load(payload["topology"])   # 已缓存过磁盘解析，代价可接受
            n_set, n_face, dropped = bl_blockade.emit_xml(
                out, topo, payload["results"], adopt,
                source_xml=os.path.basename(payload["topology"]))
            return {"ok": True, "out": out, "settlements": n_set, "faces": n_face,
                    "droppedDuplicates": [{"fi": f, "loser": l, "winner": w}
                                          for (f, l, w) in dropped[:50]],
                    "droppedCount": len(dropped),
                    "note": ("一块面片只能归一座城；重复归属已由写文件层强制剔除。"
                             "想改归属就调整 adopt 里的顺序或显式给 faces。")}

        # action == candidates
        rows = payload["results"]
        city = args.get("city")
        if city:
            rows = [r for r in rows if city == r["id"] or city in r["name"]]
            if not rows:
                return {"ok": False, "error": "没有匹配的城池：%s（类型过滤=%s）"
                        % (city, ",".join(types))}
        if args.get("mode"):
            rows = [r for r in rows if r.get("mode") == args.get("mode")]
        rows = sorted(rows, key=lambda r: (r.get("mode") != "pass",
                                           r.get("passWidth") or 999.0))
        limit = int(args.get("limit") or 40)
        full = bool(args.get("full"))
        shown = rows[:limit]
        return {
            "ok": True,
            "elapsedSec": payload["elapsedSec"],
            "cached": True,
            "settlementCount": payload["settlementCount"],
            "passCount": payload["passCount"],
            "returned": len(shown),
            "truncated": len(rows) - len(shown),
            "conflicts": payload["conflicts"],
            "skipped": payload["skipped"][:10],
            "results": [_blockade_view(r, full) for r in shown],
        }

    # ── 桌面 / 游戏 GUI 能力 ──────────────────────────────────────────

    if name == "bl_launch_game":
        import time as _time
        script = os.path.join(_TOOLS_DIR, "bl_launch.ps1")
        if not os.path.isfile(script):
            return {"ok": False, "error": "找不到 %s" % script}
        timeout_sec = int(args.get("timeoutSec") or 150)
        ps_args = ["-TimeoutSec", str(timeout_sec)]
        if args.get("skipModuleList"):
            ps_args.append("-SkipModuleList")
        excluded = args.get("excludeModules") or []
        if isinstance(excluded, str):
            excluded = [x.strip() for x in excluded.split(",") if x.strip()]
        if excluded:
            # PowerShell 会把 "A,B" 绑成 [string[]]；逐个 -ExcludeModules 也行但只会留最后一个。
            ps_args += ["-ExcludeModules", ",".join(excluded)]
        started = _time.time()
        rc, out, err = _run_ps(script, ps_args, timeout=timeout_sec + 90.0)
        res = {"ok": rc == 0, "exitCode": rc, "launchOk": "LAUNCH OK" in (out or ""),
               "seconds": round(_time.time() - started, 1),
               "log": (out or "")[-4000:], "stderr": (err or "")[-800:]}
        if rc != 0:
            res["hint"] = ("失败常见原因：① 游戏已在运行（先 bl_status 看 pid）；"
                           "② 没人答弹窗（本脚本会自动答 Safe Mode/Mod change）；"
                           "③ 模块列表与当前安装不符（改 tools/bl_launch.ps1 里的 $mods）")
        else:
            res["hint"] = "启动成功后 bl_status 会给出新的 pid（state=loaded）"
        # ── 第二步（v0.8.18，用户建议）：窗口出现 ≠ 主菜单可用 ──
        # LAUNCH OK 时游戏还在加载/启动动画里，此时 open_ui 必被判 wrong_state。
        # 所以第二步自己等：主菜单就绪 → 必要时 ESC 跳过场 → open_ui → 每 5s 确认。
        if res["ok"] and (args.get("enterCustomBattle") is not False):
            sec = _enter_custom_battle(
                ui_id=args.get("uiId") or "CustomBattle",
                menu_timeout=float(args.get("menuTimeoutSec") or 120),
                entry_timeout=float(args.get("entryTimeoutSec") or 60),
                auto_open=bool(args.get("autoOpen")),
                min_startup_sec=float(args.get("minStartupSec") or 20.0))
            res["enterCustomBattle"] = sec
            if sec.get("ok"):
                res["hint"] = "已进自定义战斗界面（activeState=%s）" % sec.get("state")
            elif sec.get("phase") == "await_confirm":
                res["hint"] = ("主菜单信号已就绪，但**默认不自动 open_ui**（启动期 fire 崩过游戏）。"
                               "看 enterCustomBattle.screenshot 确认是主菜单，再调 bl_open_ui；"
                               "要无人值守就传 autoOpen=true")
            else:
                res["hint"] = "启动成功但没进到自定义战斗：%s（见 enterCustomBattle.timeline）" % sec.get("reason")
        return res

    if name == "bl_desktop_windows":
        if not gridhand_path():
            return {"ok": False, "available": False, "error": "gridhand 未安装",
                    "hint": "cargo install gridhand（本机 Rust 在 D:\\Program Files\\Rust）"}
        rc, data, clean, err = _gridhand_json(["windows", "list"])
        if rc != 0 or not isinstance(data, dict):
            return {"ok": False, "exitCode": rc, "raw": clean[:1500], "stderr": err[:400]}
        wins = list(data.get("windows") or [])
        flt = (args.get("filter") or "").lower()
        if flt:
            wins = [w for w in wins if flt in (w.get("title") or "").lower()]
        limit = int(args.get("limit") or 40)
        wins = wins[:limit]
        for w in wins:
            w["isGame"] = "Mount and Blade" in (w.get("title") or "")
        return {"ok": True, "available": True, "count": len(wins), "windows": wins,
                "note": "坐标是物理像素（已 DPI 校正）；isGame=true 的那条就是游戏主窗口"}

    if name == "bl_desktop_screenshot":
        if not gridhand_path():
            return {"ok": False, "available": False, "error": "gridhand 未安装",
                    "hint": "cargo install gridhand"}
        import time as _time
        use_grid = args.get("grid")
        use_grid = True if use_grid is None else bool(use_grid)
        out = args.get("out") or os.path.join(
            _ui_dir(), "ui_%s.png" % _time.strftime("%Y%m%d_%H%M%S"))
        sub = ["screenshot"]
        wid = args.get("windowId")
        if wid:
            sub += ["--window-id", str(int(wid))]
        if use_grid:
            sub.append("--grid")
        cell = args.get("cell")
        if cell:
            sub += ["--cell", str(cell)]
        sub += ["--output", out]
        rc, data, clean, err = _gridhand_json(sub)
        if rc != 0:
            return {"ok": False, "exitCode": rc, "raw": clean[:800], "stderr": err[:400]}
        return {"ok": True, "path": out, "grid": (data or {}).get("grid"),
                "hint": "用 view_image 读 path 看画面；读格名（如 C5）后用 bl_desktop_click --cell 点它"}

    if name == "bl_desktop_click":
        if not gridhand_path():
            return {"ok": False, "available": False, "error": "gridhand 未安装",
                    "hint": "cargo install gridhand"}
        cell = args.get("cell")
        if not cell:
            return {"ok": False,
                    "error": "需要 cell（格子名，如 C5 / I5+I6 / B2.C1）",
                    "why": "gridhand 刻意不提供像素坐标点击；先用 bl_desktop_screenshot --grid 读格名"}
        wid = args.get("windowId")
        focus = args.get("focus")
        focus = True if focus is None else bool(focus)
        if focus and wid:
            _run_gridhand(["windows", "raise", str(int(wid))])
        sub = ["mouse", "click", "--cell", str(cell)]
        if (args.get("button") or "left") == "right":
            sub += ["--button", "right"]
        if wid:
            sub += ["--window-id", str(int(wid))]
        rc, data, clean, err = _gridhand_json(sub)
        return {"ok": rc == 0, "exitCode": rc, "result": data,
                "raw": clean[:400], "stderr": err[:300],
                "caveat": ("合成输入对官方界面有效（实测点主菜单 C5 一次即中），"
                           "但到不了 BlBridge 自写 Gauntlet 层的按钮 —— 那一块请走文件 IPC（open_ui）")}

    if name == "bl_desktop_key":
        if not gridhand_path():
            return {"ok": False, "available": False, "error": "gridhand 未安装",
                    "hint": "cargo install gridhand"}
        if args.get("text") is not None:
            sub = ["key", "type", str(args.get("text"))]
        elif args.get("keys"):
            sub = ["key", "press", str(args.get("keys"))]
        else:
            return {"ok": False, "error": "需要 keys（组合键）或 text（文本）之一"}
        wid = args.get("windowId")
        if wid:
            sub += ["--window-id", str(int(wid))]
        rc, data, clean, err = _gridhand_json(sub)
        return {"ok": rc == 0, "exitCode": rc, "result": data,
                "raw": clean[:400], "stderr": err[:300]}

    return {"ok": False, "error": "unknown tool: %s" % name}


# ─────────────────────────────────────────────────────────────────────
# MCP 协议（stdio, 换行分隔 JSON-RPC）
# ─────────────────────────────────────────────────────────────────────

def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(req_id, result):
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id, code, message):
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def handle(req):
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        _result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return
    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return
    if method == "ping":
        _result(req_id, {})
        return
    if method == "tools/list":
        try:
            _result(req_id, {"tools": active_tools()})
        except ValueError as exc:
            _error(req_id, -32602, str(exc))
        return
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            out = call_tool(name, args)
            text = out.get("report") if isinstance(out, dict) and "report" in out else None
            if text is None:
                text = json.dumps(out, ensure_ascii=False, indent=1)
            _result(req_id, {"content": [{"type": "text", "text": text}],
                             "isError": bool(isinstance(out, dict) and out.get("ok") is False)})
        except Exception as exc:  # noqa: BLE001
            # v0.8.10（F5）：参数类异常（ValueError/IOError）直接给消息，不要套一层 `ValueError('…')` 的
            # repr —— 调用方（人或 agent）要的是"哪儿错了、正确格式是什么"，不是 Python 类型名。
            if isinstance(exc, (ValueError, IOError, OSError)):
                text = str(exc)
            else:
                text = "工具执行失败: %r" % (exc,)
            _result(req_id, {"content": [{"type": "text", "text": text}], "isError": True})
        return
    _error(req_id, -32601, "method not found: %s" % method)


def _force_utf8_stdio():
    """把 stdin/stdout/stderr 钉成 UTF-8 —— MCP over stdio 的协议要求。

    不钉死时 Python 按 **locale 编码**（中文 Windows = GBK）写 stdout，而宿主按
    UTF-8 解码 ⇒ `tools/list` 里所有中文（工具描述）变成 U+FFFD 乱码。
    2026-09-24 实测复现：`tools/list` 的 7680 字节里有 **1489 个替换字符**，
    同一批字节按 GBK 解码则完全正常。

    ⚠️ 只在入口调用（`main()`），**不要**放在模块级：`bl_selftest.py` 会
    `import bl_mcp`，模块级改 stdio 会连带改掉调用方的编码。
    """
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    _force_utf8_stdio()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if isinstance(req, list):
            for r in req:
                handle(r)
        else:
            handle(req)
    return 0


if __name__ == "__main__":
    sys.exit(main())
