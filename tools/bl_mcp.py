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
    alive = _pid_alive(pid)
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


def run_state_diagnosis(log_dir_path=None):
    """A9：判定"上次会话是正常结束、还活着，还是崩了/被强杀"。

    依据（缺一不可，缺了就诚实说"不知道"）：
      • 进程是否存活（tasklist，探测失败 = 未知）；
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
    alive = _pid_alive(pid)
    out.update({"pid": pid, "lastState": state, "alive": alive,
                "cleanExit": sess.get("cleanExit"),
                "missionInProgress": sess.get("missionInProgress"),
                "lastBattle": sess.get("lastBattle")})

    if alive is True:
        out["verdict"] = "running"
        out["detail"] = "游戏进程存活（最后状态 %s）" % state
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
    """
    src = src_dir or source_dir()
    mod = mod_dir or module_dir()
    dll = os.path.join(mod, "bin", "Win64_Shipping_Client", "BlBridge.dll")
    mf = os.path.join(mod, "build_manifest.json")

    out = {"code": "ok", "detail": "", "srcDir": src, "moduleDir": mod,
           "deployedDll": dll, "manifestPath": mf}

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

    if os.path.isdir(src):
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
    alive = _pid_alive(sess.get("pid"))
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
    out["detail"] = "四段一致：源码 = 构建产物 = 部署文件 = 进程内 DLL（版本 %s）" % out.get("builtVersion")
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
            if pid and _pid_alive(pid) is False:
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


def apply_config(edits, dry_run=False, allow_missing=False):
    """edits: [{"path": "DamageCalc/ArmorEffect/ArmorBreakPoint", "value": "45"}, ...]"""
    path = warbandlord_config()
    if not os.path.isfile(path):
        raise IOError("找不到 Warbandlord 配置: %s" % path)
    raw = io.open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = _read_text(path)
    lines = text.splitlines()

    want = {}
    for e in edits:
        want[e["path"]] = str(e["value"])

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
                continue
        new_lines.append(line)

    for p in want:
        if p not in changed:
            missing.append(p)

    result = {"changed": changed, "missing": missing, "dryRun": bool(dry_run), "path": path}
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
        "description": "读取 BlBridge 遥测模块状态（模块是否加载、日志目录、会话内场次、最近一场战斗文件）",
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
        "name": "bl_battle_status",
        "description": "查询游戏内推演状态机（idle/loading/running/ended/error）、进度（双方存活数）与最近一次结果",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_start_battle",
        "description": ("让游戏开一场**无玩家**的 AI 对 AI 战斗（10 倍速）。"
                        "前提：游戏需停在「自定义战斗」界面。返回 accepted 后请用 bl_wait_for_state 等 ended。"),
        "inputSchema": {"type": "object", "properties": {
            "attackerTroop": {"type": "string", "description": "攻方兵种 id，如 imperial_legionary"},
            "attackerCount": {"type": "integer", "description": "攻方人数，默认 20"},
            "defenderTroop": {"type": "string", "description": "守方兵种 id，如 battanian_fian_champion"},
            "defenderCount": {"type": "integer", "description": "守方人数，默认 20"},
            "scene": {"type": "string", "description": "场景名，默认 battle_terrain_a"},
            "durationCapSec": {"type": "integer", "description": "单场时长上限（游戏内秒），默认 600"},
            "orders": {"type": "string", "enum": ["charge", "default"],
                       "description": ("战术对称化。charge（默认）=双方都用 TacticCharge，"
                                       "消除\"攻方进攻/守方原地防守\"带来的方向偏差（实测镜像对局会 13:0 一边倒）；"
                                       "default=引擎默认战术，仅用于 A/B 对照")},
            "playerSide": {"type": "string", "enum": ["attacker", "defender"],
                           "description": "谁被标记为玩家侧，仅用于排查该标记是否带来系统性偏差，默认 attacker"}},
            "required": ["attackerTroop", "defenderTroop"], "additionalProperties": False},
    },
    {
        "name": "bl_wait_for_state",
        "description": "轮询等待推演状态（idle/loading/running/ended/error），到点返回当前状态与结果",
        "inputSchema": {"type": "object", "properties": {
            "state": {"type": "string", "description": "目标状态，默认 ended"},
            "timeoutSec": {"type": "integer", "description": "最长等待秒数，默认 180"},
            "pollSec": {"type": "number", "description": "轮询间隔秒，默认 2"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_abort",
        "description": "中止当前正在进行的推演（调用引擎的 Mission.EndMission，走官方结束路径）",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
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
]


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

    if name == "bl_build_check":
        return {"ok": True, "buildCheck": build_check()}

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
        if not params["attackerTroop"] or not params["defenderTroop"]:
            return {"ok": False, "error": "必须提供 attackerTroop 与 defenderTroop"}
        resp, err = send_command("start_battle", params, timeout=60)
        if err:
            return {"ok": False, "error": err}
        body = resp.get("result") or {}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "start_battle 被拒绝",
                    "code": e.get("code"), "outcomeUncertain": e.get("outcomeUncertain"),
                    "response": resp}
        return {"ok": True, "state": body.get("state"), "accepted": body.get("accepted"),
                "buildCheck": bc,
                "buildWarning": (bc.get("detail") if bc.get("code") == "stale_source" else None),
                "hint": "用 bl_wait_for_state(state=ended) 等它打完", "response": resp}

    if name == "bl_wait_for_state":
        import time as _t
        target = args.get("state") or "ended"
        timeout = float(args.get("timeoutSec") or _cfg("waitForStateTimeoutSec", 180))
        poll = float(args.get("pollSec") or 2)
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
        _result(req_id, {"tools": TOOLS})
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
            _result(req_id, {"content": [{"type": "text", "text": "工具执行失败: %r" % (exc,)}],
                             "isError": True})
        return
    _error(req_id, -32601, "method not found: %s" % method)


def main():
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
