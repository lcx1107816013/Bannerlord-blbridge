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


def _tasklist_query(pid):
    """向 `tasklist` 查一个 pid，返回 `(returncode, text)`；起不来进程时返回 `(None, "")`。

    ★★ 刻意抽成单独一层（2026-10-08，B11）：把「**能不能真的调到 tasklist**」与
    「**拿到输出后怎么判**」分开 —— 后者因此可以在任何环境里被**确定性地测**
    （见 `bl_selftest.py` 的 B11 组：注入 `(rc, text)` 三态）。
    否则这两件事被 subprocess 绑死，测试就只能依赖"本机 tasklist 恰好可用"，
    在受限环境（安全策略拦 `Access denied`、PATH 里没有、精简镜像）里整片判据失灵 ——
    而**失灵的样子**恰好是"冒充已死"，正是 B11 要防的。
    """
    try:
        proc = subprocess.run(["tasklist", "/FI", "PID eq %d" % int(pid), "/NH", "/FO", "CSV"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=8)
    except Exception:  # noqa: BLE001
        return None, ""
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


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
    # ★★ 必须看 returncode（2026-10-08 修，B11）：tasklist **非 0 退出**时 stdout 是**空的**，
    #   而被安全策略/EDR 拦、不在 PATH、`Access denied` 都是这种形态。
    #   只看 stdout 会把"探不到"读成"这个 pid 上没有进程" ⇒ 返回 False（=已死），
    #   而消费点（等待循环）见到 False 就**立刻 `_drop_pending()` 并报 `process_exited`**
    #   ⇒ **游戏明明在跑，控制通道却把每个请求当"进程已退出"作废**。
    #   ⇒ "没有输出" ≠ "没有这个进程"：非 0 一律按**未知**回 None，让归因码走 `probe_unknown`。
    rc, out = _tasklist_query(n)
    if rc is None or rc != 0:
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
    # 与 `_pid_alive` 同一个洞：非 0 退出 ⇒ 探测失败（未知），不是"没有名字"。
    # 若这里不判，`_pid_is_game` 会把"探测失败"读成"名字认不出" ⇒ 同样落进 None，
    # 但更危险的是它让"探测不可用"看起来像"这不是游戏"（两句语义完全不同）。
    rc, out = _tasklist_query(n)
    if rc is None or rc != 0:
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


def _iso_utc(epoch_seconds):
    """epoch 秒 → `YYYY-MM-DDTHH:MM:SSZ`（UTC）。失败返回 None（不抛）。

    B6 用：给 `changedSources` 附 mtime，让调用方能判断"这个改动是不是我做的"
    —— 本仓库被**两个会话**共用，没有 mtime 就只能靠人去问。
    """
    try:
        # ⚠️ 用**模块级**的 `time`（顶部 import time）。`_time` 只是 `send_command`
        #    里的**函数局部**别名，在这里不可见 —— 我第一版就写错成 `_time`，
        #    被自己的实测抓出来（NameError）。这正是"改完必须真跑一遍"的价值。
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_seconds))
    except (TypeError, ValueError, OSError):
        return None


def _dll_has_utf8(path, needle):
    """DLL 的 **`#Strings` 堆**里是否含该名字（**UTF-8**）。

    ⚠️ 这个函数是 2026-10-07 补的 —— 补的是我**自己**上一版判据的洞。

    ## 为什么必须区分两种编码（ECMA-335 的两个字符串堆）

    | 堆 | 编码 | 存什么 |
    |---|---|---|
    | **`#Strings`** | **UTF-8** | **类型名 / 字段名 / 方法名**（元数据表用它）|
    | **`#US`** | **UTF-16LE** | **字符串字面量**（`ldstr` 用它）|

    我上一版只搜 UTF-16 ⇒ 对**类型名/字段名**必然**假阴性**。
    当时没暴露，是因为提取器只抓"小写下划线字面量"（那些确实在 `#US`）——
    **歪打正着**，不是设计对。

    ## 实测证据（`E:\\Document\\_encoding_evidence.py`，含反向对照）

    ```
    RBM.dll:
      SaveRosterRepairBehavior   UTF8=True   UTF16=False   ← 类型名只在 UTF-8
      BadDataCleanupBehavior     UTF8=True   UTF16=False   ← 同上
      get_hero / scan_bad_data   UTF8=False  UTF16=True    ← 字面量只在 UTF-16
      RBM BadDataCleanup         UTF8=False  UTF16=True
      未启用                      UTF8=False  UTF16=True
      zzz_not_a_real_symbol_xyz  UTF8=False  UTF16=False   ← 反向对照（有分辨力）
    ```
    """
    try:
        with io.open(path, "rb") as fh:
            blob = fh.read()
        return blob.find(needle.encode("utf-8")) >= 0
    except (IOError, OSError, UnicodeEncodeError):
        return False


def _dll_symbol_encoding(path, needle):
    """该符号在 DLL 里以**哪种编码**存在。返回 `"utf8"` / `"utf16"` / `"both"` / `None`。

    分开返回而不是只给 bool，是为了报告里能说清"**是哪一类**符号被找到了"——
    这直接决定"找不到"意味着什么（类型名没编进去 vs 字面量没编进去）。
    """
    u8 = _dll_has_utf8(path, needle)
    u16 = _dll_has_utf16(path, needle)
    if u8 and u16:
        return "both"
    if u8:
        return "utf8"
    if u16:
        return "utf16"
    return None


def _dll_has_symbol(path, needle):
    """**两种编码取并集**：任一命中即认为该符号在 DLL 里。

    ★ 为什么必须取并集（这是本函数存在的全部理由）：
      类型名/字段名只在 `#Strings`(UTF-8)，字面量只在 `#US`(UTF-16)。
      只查一种 ⇒ 对另一类**必然假阴性** ⇒ 会误报"部署落后于源码"。
    """
    return _dll_symbol_encoding(path, needle) is not None


def _dll_has_utf16(path, needle):
    """DLL 里是否含该字符串（按 **UTF-16LE** 找）。

    ## ⚠️ 为什么按 UTF-16 找（血泪教训，2026-10-07）

    .NET 的**字符串字面量**在程序集里以 **UTF-16LE** 存储（ECMA-335 的 `#US` 流）。
    用 ASCII / UTF-8 搜**必然搜不到** —— 于是会得出**假阴性**：
    "这个符号不在 DLL 里 ⇒ 部署落后了"。

    真事：隔壁会话据此报"部署落后于源码（DLL 不含 `get_hero`）"，
    而我**自己也用带中文的 `.ps1` 复现了同一个假阴性**
    （PS 5.1 按 cp936 解码 UTF-8 无 BOM 脚本 ⇒ 比对错位）。
    Python 字节级按 UTF-16LE 搜 ⇒ 一次性证明符号**都在**。

    ⚠️ 但**只查 UTF-16 也不够** —— 见 `_dll_has_utf8` / `_dll_has_symbol`：
    类型名与字段名在 **UTF-8** 的 `#Strings` 堆里。
    """
    try:
        with io.open(path, "rb") as fh:
            blob = fh.read()
        return blob.find(needle.encode("utf-16-le")) >= 0
    except (IOError, OSError, UnicodeEncodeError):
        return False


def dll_source_symbol_check(src_dir=None, dll_path=None):
    """**内容级**核验：源码里新加的「可判定符号」是否真的进了 DLL。

    ## 为什么单靠 SHA256 不够（这条判据补的就是那个洞）

    `build_check` 的四段链条用的是**哈希**：源码哈希 vs 清单哈希 vs 磁盘哈希。
    它能抓"改了没构建"，但**抓不到一类更阴的情况**：
    清单/哈希都对上了，而 DLL 内容**缺**某个新符号
    （例如构建缓存、把旧 DLL 复制过去、或清单与产物不同步）。

    ⇒ 这里做**内容级**独立核验，**两类符号都查**（2026-10-07 修正）：

      · **字符串字面量**（形如 `x_y_z` 的小写下划线串，如 `clan_bandit_no_heroes`）
        → 存于 **`#US` 堆（UTF-16LE）**；
      · **本工程自己的类型名 / 字段名**（形如 `class Xxx…` / `static bool xxxYyy`）
        → 存于 **`#Strings` 堆（UTF-8）**。

    ★ 为什么必须两类都查（这是我上一版的**真实漏洞**，不是假想）：
      上一版只抓字面量 ⇒ **完全不检查类型名** ⇒ 于是**抓不到当天真实发生的那类故障**：
      我新增了 `BadDataCleanup.cs`（RBM 侧）但**忘了加进 `csproj` 的显式 `<Compile>` 清单**
      ⇒ 编译直接报 `CS0246 找不到类型`。
      那次是编译器替我发现的 —— 但**同类问题在"清单漏加某个文件、而该文件恰好只含字面量"时
      编译器不会报**，只有内容级核验能抓到。

    ⚠️ 边界（如实）：
      · 只做**字节级子串搜索**，**不解析 IL / 元数据表** ⇒ 证明的是
        "这些名字/字面量的字节出现在 DLL 里"，**不是**"逻辑正确";
      · 编译器可能**合并/驻留**字符串 ⇒ "找不到"是**强信号**（几乎肯定是没编进去），
        但"找得到"**不能**证明版本一致（旧 DLL 里可能仍留着已删符号 ⇒ **有假阳性**）
        ⇒ 与哈希链条**互补**，**不替代**。
    """
    out = {"checked": 0, "found": 0, "missing": [], "dll": dll_path, "note": ""}
    if not dll_path:
        dll_path = deployed_dll_path()
        out["dll"] = dll_path
    if not os.path.isfile(dll_path):
        out["note"] = "DLL 不存在，跳过内容核验"
        return out
    src = src_dir or source_dir()
    if not os.path.isdir(src):
        out["note"] = "没有 src/（部署副本），跳过内容核验"
        return out

    # ── 提取器一：字符串字面量（形如 "clan_bandit_no_heroes"）──────────
    #    形状是本工程自己的约定（坏数据 code、协议 method 名）；
    #    要求 ≥2 个下划线 + 长度≥8 ⇒ 误抓风险低（中文注释/路径不会是这个形状）。
    literal_re = re.compile(r'"([a-z][a-z0-9]*(?:_[a-z0-9]+){2,})"')

    # ── 提取器二：本工程自己的类型名 / 字段名（2026-10-07 新增）─────────
    #    ⚠️ 这个提取器补的是上一版的**真实漏洞**：只抓字面量 ⇒ 类型名从不被检查。
    #    刻意**限定本工程前缀**（`BlBridge` / `RBM` / `BadData` …），
    #    否则会把 `System.` / `TaleWorlds.` 等**外部**名字也拿去搜，
    #    那些本该 "找不到"（它们不在我们的 DLL 里）⇒ 会造成大量**假阳性**。
    #    ⇒ 只认我们自己声明的东西：`class X` / `static ... Y =` 且名字含工程前缀。
    decl_re = re.compile(
        r'\b(?:internal|public|private|protected)\s+'
        r'(?:static\s+|sealed\s+|abstract\s+|partial\s+)*'
        r'(?:class|struct|enum)\s+([A-Z][A-Za-z0-9_]{3,})')
    field_re = re.compile(
        r'\b(?:internal|public|private)\s+static\s+(?:readonly\s+)?'
        r'[A-Za-z_][A-Za-z0-9_<>,\.\[\]]*\s+([a-zA-Z][A-Za-z0-9_]{5,})\s*[=;]')

    # ★★ 必须先剥掉注释（2026-10-07，第一版就栽在这）
    #    没有剥注释 ⇒ 正则匹配到了**注释里引用的外部类名**（真例：
    #    `PatchProbe.cs` 的注释写着 `6159: public class HarmonyAttribute : Attribute`，
    #    那是**反编译源码的摘录**，`HarmonyAttribute` **不是我们声明的**）
    #    ⇒ 检查报"源码里有、DLL 里没有"⇒ **假阳性**，会误导人以为部署落后。
    #    ⇒ 静默失效（漏报）与该假阳性都是本判据最不能出的错，故两者都要防。
    def _strip_comments(text):
        # 顺序要紧：先块注释，再行注释（行注释里的 `//` 可能出现在字符串里，
        # 但这只影响"是否误剥"，不会造成漏报 —— 保守优先）。
        text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
        out = []
        for line in text.split("\n"):
            # 只剥**行首即为注释**或 `//` 之前无引号的行；含 `"http://"` 这类
            # 字符串的极少数情况会误剥，代价是漏一个符号（可接受，不制造假阳性）。
            idx = line.find("//")
            if idx >= 0 and line.count('"', 0, idx) % 2 == 0:
                line = line[:idx]
            out.append(line)
        return "\n".join(out)

    seen = []           # [(符号, 类别)] —— 类别用于报告里说清"缺的是哪一类"
    src_files = sorted(n for n in os.listdir(src) if n.endswith(".cs"))
    try:
        for name in src_files:
            try:
                text = io.open(os.path.join(src, name), encoding="utf-8",
                               errors="replace").read()
            except (IOError, OSError):
                continue
            code = _strip_comments(text)
            for m in literal_re.finditer(code):
                item = (m.group(1), "literal")
                if item not in seen:
                    seen.append(item)
            for m in decl_re.finditer(code):
                item = (m.group(1), "typename")
                if item not in seen:
                    seen.append(item)
            for m in field_re.finditer(code):
                item = (m.group(1), "fieldname")
                if item not in seen:
                    seen.append(item)
    except (IOError, OSError) as exc:
        out["note"] = "读源码失败：%r" % exc
        return out

    missing = []
    by_kind = {"literal": 0, "typename": 0, "fieldname": 0}
    for sym, kind in seen:
        by_kind[kind] = by_kind.get(kind, 0) + 1
        # ★ **两种编码取并集**（类型名在 UTF-8、字面量在 UTF-16）
        if not _dll_has_symbol(dll_path, sym):
            missing.append(sym + " (" + kind + ")")
    out["checked"] = len(seen)
    out["found"] = len(seen) - len(missing)
    out["missing"] = missing[:20]
    out["totalMissing"] = len(missing)
    out["byKind"] = by_kind
    out["encodings"] = ("查了两种编码：类型名/字段名在 UTF-8(#Strings)，"
                        "字面量在 UTF-16LE(#US) —— 只查一种会对另一类假阴性")
    if missing:
        out["note"] = ("⚠️ 有 %d 个源码里的符号**不在 DLL 里** ⇒ 部署很可能落后于源码"
                       "（内容级判据；与哈希链条互补）。缺的按类别：%s"
                       % (len(missing), "、".join(missing[:8])))
    else:
        out["note"] = ("源码里的 %d 个符号（字面量 %d / 类型名 %d / 字段名 %d）"
                       "都在 DLL 里（两种编码取并集，内容级一致）"
                       % (len(seen), by_kind.get("literal", 0),
                          by_kind.get("typename", 0), by_kind.get("fieldname", 0)))
    return out


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
                                ★ 若同时带 `probeUnknown: true`，含义是**探测失败 = 未知**
                                （不是"已退出"）—— 例如 `tasklist` 被安全策略拦截。
                                两种情形都**不**核对进程内 DLL：不知道进程在不在，
                                就无法确立"进程内是哪份 DLL"这一环（见 B11）。

    ★ 判据必须**三值**：True / False / **None（未知）**。把"未知"并入任何一侧都会产出
    与事实相反的结论（`None` 曾被当成"可以继续比" ⇒ 对没在跑的游戏报"进程内是别的构建"）。

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

        # ★ 内容级核验（2026-10-07，用户要求补上）：哈希链条**抓不到**"DLL 内容缺新符号"。
        #   实测背景：隔壁会话用 ASCII 搜 DLL 报"部署落后（缺 get_hero）"——
        #   那是**假阴性**（.NET 字符串是 UTF-16）；但"哈希都对、内容却缺符号"这个洞是真的。
        #   ⇒ 无论哈希结果如何，都独立做一次**内容级**核验并把结果放进返回里。
        out["symbolCheck"] = dll_source_symbol_check(src_dir=src, dll_path=dll)

        if changed or missing:
            out["code"] = "stale_source"
            # ── B6 修复（2026-10-07，隔壁项目测试表发现）──────────────────────
            #
            # 现象：报 `stale_source` 但战斗照常执行；而 `changedSources` 里出现
            # **调用方自己没改过**的文件（本例：另一个会话在写的 `BadDataSpec.cs`）
            # ⇒ 调用方无法判断"这个警告与我当前操作有关吗"，只能靠人去问。
            #
            # ★ 关键澄清（B6 的分析里漏了一点）：`stale_source` 说的**永远是 BlBridge 自己**
            #   （`src/*.cs` ↔ 部署的 DLL），**与三合一 MOD 的 DLL 无关**。
            #   所以对"测 RBM 行为"无影响；但**若你在改 BlBridge 本身，这个警告必须当真**
            #   （否则测的是旧 DLL）。两种情形都取决于"那个改动的文件是不是你的" ——
            #   而**判断依据就是 mtime**。
            #
            # ⇒ 故：给每个改动文件附 mtime（B6 自己的建议），并显式提示并发写入的可能。
            det = []
            for n in changed[:8]:
                p = os.path.join(src, n)
                try:
                    mt = os.path.getmtime(p)
                    det.append({"name": n,
                                "mtimeUtc": _iso_utc(mt),
                                "sizeBytes": os.path.getsize(p)})
                except OSError:
                    det.append({"name": n, "mtimeUtc": None, "sizeBytes": None})
            for n in missing[:8]:
                det.append({"name": n, "missing": True})
            out["changedSources"] = changed[:8]
            out["changedSourcesDetail"] = det
            out["detail"] = ("源码与上次构建不一致（改动 %d 个、缺失 %d 个）—— 需要重新构建后部署。"
                             "⚠️ 见 changedSourcesDetail 的 mtime：**若有你没改过的文件**，"
                             "说明**另一个会话/进程正在同一源码树里写**（本仓库被两个会话共用）"
                             "⇒ 先确认再决定是否重编，别把别人的中间态部署进游戏。"
                             % (len(changed), len(missing)))
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
    # ★★ `None` 必须与 `False` 一样**早退**（2026-10-08 修，B11 的连带面）。
    #
    # 这里曾写成：False ⇒ game_offline 早退；**None ⇒ 只加一条 note 然后继续往下比**。
    # 当时 `None` 近乎不可达（`tasklist` 几乎总能返回 0），所以没暴露。而 `_pid_alive`
    # 修好"探测失败 ⇒ None"之后，这一支**第一次真的可达** —— 于是"探测不可用"会一路走到
    # 下面的 `mismatch` 判定，对一个**根本没在跑**的游戏报出 `game_running_other_build`
    # （"进程内 DLL 与磁盘不同"）⇒ 又是一条**与事实相反**的结论。
    # 这正是本项目记录过的那条教训：**放宽/修正一处闸门 = 打开一条以前锁着的路 ⇒
    # 必须回头看那条路上有没有旧洞**（B8 的归因）。
    #
    # 语义上两者本就该同级：**"进程不在"与"不知道进程在不在"，都无法确立"进程内 DLL"这一环**
    # ⇒ 一律 game_offline（文件链条照常给出；不硬比、不臆断"必须重启"）。
    if alive is False or alive is None:
        out["code"] = "game_offline"
        out["detail"] = ("游戏没在运行（状态文件是上次会话留下的）—— 文件链条一致；重启游戏后再核对进程内 DLL"
                         + ("；注意上次会话进程内是 %s（与当时的磁盘不一致）" % b.get("loadedSha256") if mismatch else ""))
        if alive is None:
            out["detail"] = ("**无法确认游戏是否在运行**（进程存活探测失败 = 未知，例如 tasklist 被安全策略拦截）"
                             "—— 文件链条一致，但『进程内 DLL』这一环无法核对；"
                             "启动游戏后重跑，或修好取样环境再核")
            out["probeUnknown"] = True
        return out

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
# B1 修复（2026-10-07，由隔壁项目的测试表发现）：目标模块**可能已被软卸载**
#
# ## 症状（实测，非推测）
#
# `bl_read_config` / `bl_apply_config` 硬编码读
# `<game>\Modules\Warbandlord\config.xml`，而本机上该目录：
#   · **没有 `SubModule.xml`**、**0 个 DLL** —— 模块已软卸载；
#   · 只剩一个 31,721 B 的残留 `config.xml` ⇒ **没有任何消费者**。
# 而项目（三合一 MOD）真正读的是 `Configs\RBM\config.xml`。
#
# ⇒ 后果是**最坏的一类**：拿这两个工具改配置会"改了但没生效"，
#   然后把**工具问题误判成 MOD 有问题**（本仓库纪律里明确要分开归因的两件事）。
#   实证：两处同一项的值本就不一致（残留档 vs 真档），一眼可辨不是同一个文件。
#
# ## 修法（三件事，对应测试表给的三条建议）
#
#   ① **检测模块是否真的装好**：要求同目录存在 `SubModule.xml` **或** 至少一个 DLL；
#   ② 不满足时**明确报错**（而不是静默读写一个死人文件）；
#   ③ 返回值里**说清操作的是哪个模块**（`module` / `path` / `consumer` 字段）。
#
# ⚠️ 边界（如实）：**只有 `SubModule.xml`/DLL 的存在性**能判，判不了
#    "该模块是否在本次启动的模块列表里"（那要看 LauncherData，且本工具不碰游戏）。
#    ⇒ 所以措辞用"看起来已卸载"，并在返回里给出判据，让人能一眼复核。
# ─────────────────────────────────────────────────────────────────────

def warbandlord_module_state():
    """看 `Modules\\Warbandlord` 是否像个**真的装好的模块**。

    返回 dict：{exists, hasSubModuleXml, dllCount, hasConfig, looksUninstalled, why}
    """
    mod_dir = os.path.join(game_dir(), "Modules", "Warbandlord")
    st = {"module": "Warbandlord", "dir": mod_dir, "exists": os.path.isdir(mod_dir),
          "hasSubModuleXml": False, "dllCount": 0, "hasConfig": False,
          "looksUninstalled": False, "why": ""}
    if not st["exists"]:
        st["looksUninstalled"] = True
        st["why"] = "模块目录不存在"
        return st
    st["hasSubModuleXml"] = os.path.isfile(os.path.join(mod_dir, "SubModule.xml"))
    st["hasConfig"] = os.path.isfile(os.path.join(mod_dir, "config.xml"))
    try:
        for root, _dirs, files in os.walk(mod_dir):
            st["dllCount"] += sum(1 for f in files if f.lower().endswith(".dll"))
    except Exception:                                     # noqa: BLE001
        pass
    # ⇒ 引擎靠 SubModule.xml 认识模块；没有它 + 一个 DLL 都没有 ⇒ 不可能被加载
    if not st["hasSubModuleXml"] and st["dllCount"] == 0:
        st["looksUninstalled"] = True
        st["why"] = ("没有 SubModule.xml 且 0 个 DLL ⇒ 引擎不会加载它"
                     "（残留目录只有一个没有消费者的 config.xml）")
    elif not st["hasSubModuleXml"]:
        st["why"] = "没有 SubModule.xml（有 DLL，但引擎靠 SubModule.xml 识模块）"
    return st


def _warbandlord_guard():
    """读/写 Warbandlord 配置前的闸门。返回 (ok, payload)。

    不 ok 时 payload 已经是可直接返回给调用方的**说明性错误**（含判据与替代方案）。
    """
    st = warbandlord_module_state()
    if st["looksUninstalled"]:
        return False, {
            "ok": False,
            "error": "target_module_not_installed",
            "module": "Warbandlord",
            "path": os.path.join(st["dir"], "config.xml"),
            "pathExists": st["hasConfig"],
            "message": ("`bl_read_config` / `bl_apply_config` 操作的是 **Warbandlord** 模块的"
                        "config.xml，但该模块看起来**已被卸载**：" + st["why"]),
            "evidence": {"hasSubModuleXml": st["hasSubModuleXml"],
                         "dllCount": st["dllCount"], "hasConfig": st["hasConfig"]},
            "note": ("⚠️ 这个文件**没有消费者** —— 改它不会影响任何在跑的模块。"
                     "若你要改的是别的模组（例如三合一里的 RBM），"
                     "它读的是 `<我的文档>\\Mount and Blade II Bannerlord\\Configs\\RBM\\config.xml`，"
                     "请直接编辑那个文件（或等本工具支持 `path` 参数）。"),
        }
    return True, st



# ─────────────────────────────────────────────────────────────────────
# config.xml 读写（行级编辑，保留原文件其余内容与 BOM）
# ─────────────────────────────────────────────────────────────────────

_OPT_RE = re.compile(r'<Option\s+id="([^"]+)"\s+value="([^"]*)"\s*/?>')
_OPEN_RE = re.compile(r'<(Category|Group|SubGroup)\b([^>]*?)(/?)>')
_CLOSE_RE = re.compile(r'</\s*(Category|Group|SubGroup)\s*>')
_ID_RE = re.compile(r'id="([^"]+)"')

# ── 形式①（裸标签）：RBM 自己的 config.xml 用它 ────────────────────────────
#   `<Tag>value</Tag>` 单行、无 id/value 属性。
#   ⚠️ 与形式②（`<Option id=".." value=".." />`）**在同一个文件里并存**：
#      实测 `Configs\RBM\config.xml` 顶层有 7 个段，`<Warbandlord>` 是形式②、
#      其余（`RBMAI` / `RBMCombat` / `RBMCampaign` / `RBMTournament` /
#      `DeveloperMode` / `LastSeenChangelogVersion`）是形式①。
_BARE_RE = re.compile(r'^\s*<([A-Za-z_][A-Za-z0-9_]*)(?:\s[^>]*)?>([^<]*)</\1>\s*$')
_TAG_NAME_RE = re.compile(r'^\s*</?([A-Za-z_][A-Za-z0-9_]*)')
_SELF_CLOSING_RE = re.compile(r'/>\s*$')

# ★ 形式②的保留标签名。**必须显式排除**，否则它们会被当成形式①的裸标签：
#   `<Category id="X">` / `<Option id=".." value=".." />` 都能匹配"通用标签"形状，
#   一旦被塞进形式①的祖先栈，就会污染路径、甚至让 `Option` 记录整个失效
#   （`<Option ... />` 会被误当成"祖先标签"而不是"配置项"）。
_RESERVED_TAGS = ("Category", "Group", "SubGroup", "Option")

# 这些"标签"不是配置段，进栈会污染路径（XML 声明 / 注释）。
_SKIP_LINE_PREFIX = ("<?", "<!")


def _read_text(path):
    with io.open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def _scan(text):
    """按行扫描 config.xml，返回 [(行号, 完整路径, 值, 键名, 形式)]。

    **两种形式并存**（实测本机 `Configs\\RBM\\config.xml` 两者同时存在）：

      形式② `<Option id="X" value="V" />`（Warbandlord 遗产格式）
          ⇒ 路径由 `Category/Group/SubGroup` 的 **id** 逐层拼接，
            例如 `DamageCalc/ArmorEffect/ArmorBreakPoint`。
      形式① `<Tag>V</Tag>`（RBM 自己的格式）
          ⇒ 路径由**祖先标签名**逐层拼接（顶层 `Config` 不计），
            例如 `RBMCampaign/BadDataCleanupEnabled`、`RBMCombat/UseWarbandlordPerkValues`。

    ★ 为什么形式①必须用**祖先标签名**而不是光用标签名：
      `Enabled` 在 `RBMTournament` / `RBMAI` / `RBMCombat` / `RBMCampaign`
      **四个段里都出现**（Lead 实测）⇒ 光用标签名会**四路撞车**、后写覆盖前写。
      带祖先路径 ⇒ `RBMCampaign/Enabled` 唯一。
      （实测该文件 215 个裸标签**拼接后无重复路径**，见自测里的断言。）

    ★ 两种形式的路径**天然不撞车**：形式①用"标签名"、形式②用"id 值"。
    """
    out = []
    stack = []        # 形式②：[(标签名, id)]
    bare_stack = []   # 形式①：祖先标签名
    for idx, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith(_SKIP_LINE_PREFIX):
            continue

        name_m = _TAG_NAME_RE.match(line)
        tag_name = name_m.group(1) if name_m else None
        is_reserved = tag_name in _RESERVED_TAGS
        self_closing = bool(_SELF_CLOSING_RE.search(line))

        # ── 形式①：裸标签成对 `<Tag>v</Tag>`（无属性、单行）──────────────
        #   保留名（Category/Group/SubGroup/Option）**不走这里**。
        if not is_reserved:
            bm = _BARE_RE.match(line)
            if bm:
                tag, val = bm.group(1), bm.group(2)
                # 顶层容器（`Config`）不进路径 ⇒ 路径从它的子段开始。
                ancestors = [t for t in bare_stack if t != "Config"]
                full = ("/".join(ancestors) + "/" + tag) if ancestors else tag
                out.append((idx, full, val, tag, "bare"))
                continue

            # 形式①的**容器**开/闭（如 `<RBMCampaign>` / `</RBMCampaign>`）。
            # ⚠️ 自闭合行（`<X ... />`）不进出栈 —— 它没有配对的闭合标签。
            if self_closing:
                continue
            if stripped.startswith("</"):
                if bare_stack and bare_stack[-1] == tag_name:
                    bare_stack.pop()
                continue
            if stripped.startswith("<"):
                bare_stack.append(tag_name)
                continue
            continue

        # ── 形式②：Category/Group/SubGroup + Option ──────────────────────
        cm = _CLOSE_RE.search(line)
        if cm:
            if stack and stack[-1][0] == cm.group(1):
                stack.pop()
            continue
        om = _OPEN_RE.search(line)
        if om:
            attrs = om.group(2) or ""
            ident = _ID_RE.search(attrs)
            sc = (om.group(3) == "/") or attrs.rstrip().endswith("/")
            if ident and not sc:
                stack.append((om.group(1), ident.group(1)))
            continue
        m = _OPT_RE.search(line)
        if m and stack:
            full = "/".join(x[1] for x in stack) + "/" + m.group(1)
            out.append((idx, full, m.group(2), m.group(1), "option"))
    return out


# ─────────────────────────────────────────────────────────────────────
# A4（2026-10-08）：给 config 工具加**显式 `path`** —— 让调用方能指定配置文件
#
# ## 为什么要（B1 建议②的落地）
#
# B1 修完后，不传 `path` 时工具会**明确拒绝**已卸载的 Warbandlord；
# 而项目真正要改的是 RBM 的 `Configs\RBM\config.xml`（`bl_apply_config` 现在够不着它），
# 于是"脚本化改 RBM 开关做自动化 A/B"做不了 —— 调用方只能手工编辑文件。
# ⇒ 加 `path`：**显式指定就按指定文件操作**，目标是什么由调用方自己负责。
#
# ## ★★ 安全纪律（本节的每一条都是"文件目标不可信"这个教训的直接产物）
#
# 1. **`path` 一旦给了，就跳过 Warbandlord 闸门。**
#    闸门的语义是"你没说目标 ⇒ 我按默认目标替你判它好不好"。
#    调用方**明确**指定了目标 ⇒ 再拿 Warbandlord 的存活性去拒它就是错判。
# 2. ★ **返回值里必须回显实际操作的绝对路径**，且**读与写都要回显**。
#    理由：B1 的事故是"操作了**错的文件**"（改 Warbandlord 残留档却以为在改 RBM）；
#    另一类同源事故是"以为操作的是另一个文件"（用改了路径的副本往返、
#    却被底层无条件写回了真实配置）。
#    两者都只能靠"**把目标绝对路径回显出来**"被人/脚本核对 ⇒ 这是本工具的第一道防线。
# 3. ★ **读必须真只读。** 本文件的 `read_config` 走 `_read_text()` +
#    `_scan()`（纯文本/正则解析），**从不实例化任何 mod 的 Config 类**。
#    ⚠️ 这不是可有可无的洁癖 —— 实测（2026-10-08，B 线真实事故）：
#    `RBMConfig.parseXmlConfig()` **不是只读函数**，它的末尾无条件调 `saveXmlConfig()`
#    → `document.Save(Utilities.GetConfigFilePath())`（**永远写真实配置路径**）。
#    有人用它做"只读往返验证"，结果**用副本覆写了玩家的真实配置**。
#    ⇒ 结论钉死：**任何"读配置"的实现都不得调用 mod 自己的 parse/load 函数**，
#      只能用纯文本/XML 解析。本工具满足这条。
# 4. `path` 给错（不存在/不是文件）⇒ **明确报错**，不回退到默认目标。
#    静默回退会让"我指定了 A 却改了 B"，正是第 2 条要防的。
# ─────────────────────────────────────────────────────────────────────

def _resolve_config_path(path):
    """把调用方给的 `path` 规范化成**绝对路径**（用于回显与操作）。

    ⚠️ 刻意**不做**"不存在就回退默认"：回退会造成"指定了 A 却动了 B"。
    存在问题一律由调用方（read_config / apply_config）显式报错。
    """
    if path is None:
        return None
    p = str(path).strip()
    if not p:
        return None
    # expandvars/expanduser 后 abspath：让返回里的路径**一定是绝对的**，
    # 这样"我到底动了哪个文件"不依赖 cwd 的隐含语义。
    return os.path.abspath(os.path.expanduser(os.path.expandvars(p)))


def read_config(paths=None, _structured=False, path=None):
    """读配置（默认 Warbandlord；给了 `path` 则读指定文件）。

    ★ **本函数只读** —— 纯文本解析，**绝不调用任何 mod 的 parse/load 函数**
    （原因见上面 A4 注释第 3 条：`RBMConfig.parseXmlConfig()` 会静默写真实配置）。

    B1 修复：**未给 `path`** 时，目标模块若已软卸载 ⇒ **明确拒绝**
    （不再读一个没有消费者的死人文件）。
    `_structured=True` 时返回 `(ok, payload)` 而不抛异常（供 MCP 派发直接用）。
    """
    explicit = _resolve_config_path(path)
    if explicit is not None:
        # 显式指定目标 ⇒ 跳过 Warbandlord 闸门（闸门只管"没说目标"的情形）。
        if not os.path.isfile(explicit):
            err = {"ok": False, "error": "config_not_found",
                   "path": explicit, "pathSource": "explicit",
                   "message": ("指定的配置文件不存在或不是文件：%s"
                               "（**不会**回退到默认目标 —— 静默回退会造成"
                               "『指定了 A 却动了 B』）" % explicit)}
            if _structured:
                return False, err
            raise IOError(err["message"])
        path_use, module, source = explicit, None, "explicit"
    else:
        ok, st = _warbandlord_guard()
        if not ok:
            if _structured:
                return False, st
            raise IOError(st["message"] + " ｜ 判据: " + str(st["evidence"]))
        path_use, module, source = warbandlord_config(), "Warbandlord", "default"

    if not os.path.isfile(path_use):
        err = {"ok": False, "error": "config_not_found", "path": path_use,
               "pathSource": source,
               "message": "找不到配置: %s" % path_use}
        if _structured:
            return False, err
        raise IOError(err["message"])
    text = _read_text(path_use)
    out = {}
    kinds = {}
    for _idx, full, val, _key, kind in _scan(text):
        out[full] = val
        kinds[full] = kind
    result = dict((p, out.get(p)) for p in paths) if paths else out
    if _structured:
        # 口径：让调用方看得出这个文件里**两种形式各有多少** ——
        # 否则"读到 468 项"会被误以为"读到全部"（实测形式①另有 215 项）。
        n_bare = sum(1 for k in kinds.values() if k == "bare")
        n_opt = sum(1 for k in kinds.values() if k == "option")
        payload = {"values": result, "path": path_use, "pathSource": source,
                   "readOnly": True,
                   "keyCount": {"total": len(kinds), "option": n_opt, "bare": n_bare},
                   "formats": ("支持两种写法：`<Option id=\"..\" value=\"..\" />`（option，"
                               "Warbandlord 遗产格式）与 `<Tag>value</Tag>`（bare，RBM 自己的格式）。"
                               "★ 路径口径：option 用 `Category/Group/SubGroup id` 拼接；"
                               "bare 用**祖先标签名**拼接（如 `RBMCampaign/BadDataCleanupEnabled`）"
                               "—— 因为像 `Enabled` 这种标签名在多个段里重复，"
                               "只用标签名会撞车。"),
                   "note": ("只读：纯文本解析，**未**调用任何 mod 的 parse/load 函数"
                            "（那些函数可能静默写回真实配置）。"
                            "配置文件的大小/时间已一并返回，便于确认读的是哪个文件。")}
        if module:
            payload["module"] = module
        try:
            stt = os.stat(path_use)
            payload["sizeBytes"] = stt.st_size
            payload["mtimeUtc"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(stt.st_mtime))
        except OSError:
            pass
        return True, payload
    return result


def _is_number(s):
    """宽松数字判定（v0.8.10 F5：给 apply_config 的"旧值数字 → 新值非数字"提醒用）。"""
    try:
        float(str(s).strip())
        return True
    except (TypeError, ValueError):
        return False


def apply_config(edits, dry_run=False, allow_missing=False, _structured=False, path=None):
    """edits: [{"path": "DamageCalc/ArmorEffect/ArmorBreakPoint", "value": "45"}, ...]

    默认目标 = Warbandlord；给了 `path` 则改指定文件（A4，2026-10-08）。

    B1 修复：**未给 `path`** 时，写入前先过模块存在性闸门 —— 目标模块若已软卸载，
    改这个文件的唯一后果是"看起来改了、实际没人读"，进而把工具问题误判成 MOD 问题。

    ★ A4 安全纪律（见上面那节长注释）：
      · 返回值里**回显实际操作的绝对路径**（`path`）+ `pathSource`；
      · **写前备份**（`backup` 字段给出备份的绝对路径）；
      · **写后回读校验**（`verified`），且回读走**同一份文本解析**（不碰 mod 的 parse 函数）；
      · 返回里写清"**改完需重启游戏才生效**"。

    ★★ 返回约定（**必须严格遵守，2026-10-08 因违反它出过一次严重缺陷**）：

        _structured=False  ⇒ 返回 **dict**（payload 本体）
        _structured=True   ⇒ 返回 **(ok: bool, payload: dict)** 元组 —— **所有**分支，含**成功**分支

    为什么把这条写死在 docstring 里：MCP 派发点（`call_tool`）写的是
    `ok, payload = apply_config(..., _structured=True)`。若某个分支偷偷返回裸 dict，
    解包会把 **dict 的键**当成 `(ok, payload)` ⇒ `ValueError: too many values to unpack`。
    ★ 而**真写路径**的顺序是"备份 → 写盘 → os.replace → 回读 → return"
    ⇒ 异常发生在**返回之后**（调用方解包时）⇒ **文件已经改完落盘，但调用方只看到 ValueError**
    ⇒ 调用方会以为"改失败了"而**实际上配置已被改** —— 正是本项目最防的"报告与事实相反"，
    而且发生在**写路径**上。

    ⚠️ 该缺陷**在 HEAD 里就已存在**（成功分支一直返回裸 dict），但长期**不可达**：
    没有 `path` 参数时永远先过 B1 闸门，而目标模块（Warbandlord）已卸载 ⇒ 总是走
    `(False, st)` 错误分支 ⇒ 成功分支没人走。**A4 加了 `path=` 之后它才第一次可达。**
    ⇒ 教训：**加参数/放宽闸门，等于打开一条以前锁着的路 —— 要检查那条路上有没有旧洞。**
    """
    explicit = _resolve_config_path(path)
    if explicit is not None:
        # 显式指定目标 ⇒ 跳过 Warbandlord 闸门（同 read_config 的理由）。
        if not os.path.isfile(explicit):
            err = {"ok": False, "error": "config_not_found",
                   "path": explicit, "pathSource": "explicit",
                   "message": ("指定的配置文件不存在或不是文件：%s"
                               "（**不会**回退到默认目标）" % explicit)}
            if _structured:
                return False, err
            raise IOError(err["message"])
        path_use, source = explicit, "explicit"
    else:
        ok, st = _warbandlord_guard()
        if not ok:
            if _structured:
                return False, st
            raise IOError(st["message"] + " ｜ 判据: " + str(st["evidence"]))
        path_use, source = warbandlord_config(), "default"

    path = path_use
    if not os.path.isfile(path):
        raise IOError("找不到配置: %s" % path)

    # ★ 统一的"结构化/非结构化"返回出口 —— 保证**所有**分支遵守上面的约定。
    #   刻意做成局部函数而不是在每个 return 前手写三元式：
    #   手写 4 处就有 4 次漏掉的机会，而这里漏一次就是"改完报失败"。
    def _ret(payload, ok=True):
        return (ok, payload) if _structured else payload
    raw = io.open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = _read_text(path)
    lines = text.splitlines()

    # ── ★★ 行尾风格：必须**保留原样**（2026-10-08 修，Lead 实测发现）──────────
    #
    # ## 缺陷现场（实测字节账）
    #
    # 原文件 CRLF=1007；调一次 `apply_config` 改 1 个键之后：
    #   48505 B / CRLF=1007  →  47498 B / CRLF=0（全变 LF-only）
    #   47498 = 48133 - 1007 + 372 ⇒ **1007 个 `\r` 被静默吃掉**（6 个新节点的增量另算）。
    #
    # ⇒ 后果是**报告与事实不符**：返回里 `changed` 只说"改了 1 个键"，
    #   而实际上**整个文件的每一行行尾都被重写了**。
    #   这正是本项目反复强调的"**写路径必须报告真实发生了什么**"
    #   （与"改完报失败""读不到当 0"同一类：报告 ≠ 事实）。
    #
    # ## 修法
    #
    # 从**原始字节**判定该文件用的是哪种行尾，写回时**按原样**拼。
    # 三态而非二态：CRLF / LF / **混用**（混用时按"以 CRLF 为主"处理，
    # 并仍以**计数**如实回报，不假装它是单一风格）。
    _n_crlf = raw.count(b"\r\n")
    _n_lf = raw.count(b"\n") - _n_crlf
    if _n_crlf > 0 and _n_lf == 0:
        line_ending, newline = "CRLF", "\r\n"
    elif _n_crlf == 0 and _n_lf > 0:
        line_ending, newline = "LF", "\n"
    elif _n_crlf == 0 and _n_lf == 0:
        line_ending, newline = "none", "\n"          # 单行/无换行文件
    else:
        line_ending = "mixed"
        newline = "\r\n" if _n_crlf >= _n_lf else "\n"

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
    for idx, full, val, key, kind in _scan(text):
        info[idx] = (full, val, key, kind)

    changed = {}
    missing = []
    new_lines = []
    for idx, line in enumerate(lines):
        if idx in info:
            full, oldval, key, kind = info[idx]
            if full in want:
                indent = line[:len(line) - len(line.lstrip())]
                if kind == "bare":
                    # ★ 形式①必须**保持原格式**：写回 `<Tag>value</Tag>`，
                    #   只替换值、保留原缩进与标签名。
                    #   理由：形式①是 **RBM 自己的 loader** 在读；
                    #   若把它改写成 `<Option id=.. value=.. />`，RBM 就读不到了
                    #   ⇒ 又是"改了没生效"，正是本项目最防的那类静默失败。
                    #   （这也是刻意**不**统一成一种格式的原因。）
                    new_lines.append(indent + '<%s>%s</%s>' % (key, want[full], key))
                else:
                    new_lines.append(indent + '<Option id="%s" value="%s" />' % (key, want[full]))
                changed[full] = {"old": oldval, "new": want[full], "format": kind}
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
        return _ret(result)
    if missing and not allow_missing:
        return _ret(dict(result, ok=False,
                         error="以下配置路径不存在，未写入任何修改: %s" % ", ".join(missing)),
                    ok=False)

    # 备份
    import shutil
    import time
    backup = path + ".bak_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, backup)

    # ★ 按**原文件的行尾风格**拼回（不是硬编码 "\n"）—— 见上面 line_ending 的注释。
    body = newline.join(new_lines)
    if raw.endswith(b"\n"):
        body += newline
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
        return _ret(dict(result, ok=False, error="XML 校验失败，已放弃写入: %r" % (exc,)),
                    ok=False)
    os.replace(tmp, path)
    # ★ 回读校验走**同一份文本解析**（`read_config` 指定 path）——
    #   绝不用 mod 自己的 parse 函数（那些会写盘，正是 A4 注释第 3 条记的事故）。
    check = read_config(list(want.keys()), path=path)
    # ★ 写后**字节级**事实（不靠推断）：行尾风格是否守住、BOM 是否守住。
    #   为什么要回报这些：Lead 实测发现过"只改 1 个键，却把整文件 CRLF 重写成 LF"，
    #   而返回里只说了 changed 的条数 ⇒ **报告与事实不符**。
    #   ⇒ 凡是"这次写入动了什么"，都要能从返回里**核对**，而不是靠调用方自己 diff。
    after_raw = io.open(path, "rb").read()
    after_crlf = after_raw.count(b"\r\n")
    after_lf = after_raw.count(b"\n") - after_crlf
    if after_crlf > 0 and after_lf == 0:
        after_ending = "CRLF"
    elif after_crlf == 0 and after_lf > 0:
        after_ending = "LF"
    elif after_crlf == 0 and after_lf == 0:
        after_ending = "none"
    else:
        after_ending = "mixed"
    bytes_delta = len(after_raw) - len(raw)
    result.update({
        "ok": True,
        "path": path,                  # ★ 实际写入的**绝对路径**（第一道防线）
        "pathSource": source,          # explicit（调用方指定） / default（Warbandlord 默认）
        "backup": backup,              # ★ 备份的绝对路径
        "verified": check,             # ★ 写后回读（与写入同一解析口径）
        "restartRequired": True,
        "restartNote": ("⚠️ **改完必须重启游戏才生效** —— 本类配置（如 RBM）"
                        "**没有热重载**，游戏只在启动/读档时读一次配置文件。"
                        "改完不重启 ⇒ 表现为『改了但没生效』，"
                        "**不要把这种情况误判成 MOD 有 bug**。"),
        # ★ 文件格式不变量（写前后对照，**如实回报**，不假设"肯定没变"）
        "fileFacts": {
            "lineEndingBefore": line_ending,
            "lineEndingAfter": after_ending,
            "lineEndingPreserved": (line_ending == after_ending),
            "bomBefore": bool(bom),
            "bomAfter": after_raw.startswith(b"\xef\xbb\xbf"),
            "bytesBefore": len(raw),
            "bytesAfter": len(after_raw),
            "bytesDelta": bytes_delta,
            "crlfBefore": _n_crlf,
            "crlfAfter": after_crlf,
            "note": ("`changed` 只说**键值**改了什么；本字段说**文件本身**改了什么。"
                     "两者**必须一起看** —— 只报 changed 条数会掩盖『整文件行尾被重写』这类事实。"),
        },
    })
    if not result["fileFacts"]["lineEndingPreserved"]:
        result["warnings"] = (result.get("warnings") or []) + [
            "⚠️ 行尾风格**未保持**（%s → %s）—— 请核对这是否是你想要的"
            % (line_ending, after_ending)]
    return _ret(result)


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
        "description": ("回读 config.xml 配置值（可不传 paths 取全部）。"
                        "**默认目标 = Warbandlord 模块的 config.xml**；"
                        "★ 给了 `path` 则读**指定文件**（例如 RBM 的 "
                        "`<我的文档>\\Mount and Blade II Bannerlord\\Configs\\RBM\\config.xml`）"
                        "—— 目的：让 MOD 侧能脚本化核对/改 RBM 开关做自动化 A/B。"
                        "★ **本工具真只读**：纯文本解析，**不调用任何 mod 的 parse/load 函数**"
                        "（实测 `RBMConfig.parseXmlConfig()` 会在末尾无条件写回**真实**配置路径，"
                        "用副本做『只读往返』会覆写玩家真档 —— 已发生过一次事故）。"
                        "⚠️ 返回里的 `path` 是**实际读取的绝对路径**，请照它核对；"
                        "`pathSource=explicit|default` 说明目标是你指定还是默认。"
                        "★ **支持两种写法**（实测 RBM 的 `Configs\\RBM\\config.xml` **两者并存**）："
                        "`<Option id=\"..\" value=\"..\" />`（Warbandlord 遗产格式，路径=`Category/Group/SubGroup` id 拼接）"
                        "与 `<Tag>value</Tag>`（RBM 自己的格式，路径=**祖先标签名**拼接，"
                        "如 `RBMCampaign/BadDataCleanupEnabled`）。"
                        "`keyCount` 分别给出两种形式各多少（RBM 真档实测：option=468 + bare=215 = 683）。"
                        "⚠️ 像 `Enabled` 这种标签名在 4 个段里重复 ⇒ 只给标签名会撞车，必须用带祖先的完整路径。"
                        "⚠️ 不给 `path` 时保留 B1 闸门：目标模块看起来已卸载 ⇒ "
                        "明确拒绝 `target_module_not_installed`（读一个没有消费者的死人文件没有意义）。"),
        "inputSchema": {"type": "object", "properties": {
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "形如 DamageCalc/ArmorEffect/ArmorBreakPoint；不传则返回全部"},
            "path": {"type": "string",
                     "description": "要读的配置文件**绝对路径**（可选）。给了就跳过 Warbandlord 闸门"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_apply_config",
        "description": ("修改 config.xml（自动备份 + 写后 XML 校验 + 回读核对）。"
                        "**默认目标 = Warbandlord 模块的 config.xml**；"
                        "★ 给了 `path` 则改**指定文件**（例如 RBM 的 "
                        "`<我的文档>\\Mount and Blade II Bannerlord\\Configs\\RBM\\config.xml`）。"
                        "★ 返回值一定回显**实际写入的绝对路径** `path`、`pathSource`、"
                        "备份路径 `backup`、回读结果 `verified` —— 请照 `path` 核对"
                        "（本项目的教训是『文件目标不可信』：既会『操作了错的文件』，"
                        "也会『以为操作的是另一个文件』）。"
                        "★ **写回保持原格式**：`<Option id=\"..\" value=\"..\" />` 仍写成 Option，"
                        "`<Tag>v</Tag>` 仍写成裸标签（**不会**被统一成一种）——"
                        "因为 RBM 自己的 loader 只认裸标签，改写格式 ⇒ RBM 读不到 ⇒ 又是『改了没生效』。"
                        "⚠️ **RBM **没有热重载**：改完必须重启游戏才生效**，"
                        "否则表现为『改了但没生效』，别误判成 MOD 有 bug。"),
        "inputSchema": {"type": "object", "properties": {
            "edits": {"type": "array", "items": {"type": "object", "properties": {
                "path": {"type": "string"}, "value": {"type": "string"}}, "required": ["path", "value"]}},
            "dry_run": {"type": "boolean", "description": "只预览不写入"},
            "path": {"type": "string",
                     "description": "要改的配置文件**绝对路径**（可选）。给了就跳过 Warbandlord 闸门"}},
            "required": ["edits"], "additionalProperties": False},
    },
    {
        "name": "bl_rts_config",
        "description": ("回读 **RTSCamera** 的配置（Documents\\...\\Configs\\RTSCamera\\RTSCameraConfig.xml）。"
                        "只读。用来查'攻城相机高度/自由相机/抬升触发'这些开关现在是什么值。"
                        "我们不改它的代码，只当它的参数管理员（见 bl_apply_rts_config）。"
                        "★ 可选 `path` 指定别的文件（副本）；返回里回显**实际读取的绝对路径** + `pathSource`。"),
        "inputSchema": {"type": "object", "properties": {
            "keys": {"type": "array", "items": {"type": "string"},
                     "description": "只读这些键（如 ElevatedHeightInSiege）；不传返回全部"},
            "presets": {"type": "boolean", "description": "true = 顺带回显可用预设名"},
            "path": {"type": "string",
                     "description": "要读的配置文件**绝对路径**（可选；不传=真实 RTSCamera 配置）"}},
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
            "allow_missing": {"type": "boolean", "description": "允许写入表里不存在的键（默认 false）"},
            "path": {"type": "string",
                     "description": "要写的配置文件**绝对路径**（可选；不传=真实 RTSCamera 配置）。"
                                    "★ 自测/试写**请务必传副本路径**"}},
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
        "name": "bl_get_hero",
        "description": ("**读取英雄的运行时血量与状态（只读，需战役上下文）**："
                        "返回 `hitPoints` / `maxHitPoints` / `overflow` / `isOverflow` / "
                        "`isWounded` / `isDead` / `isAlive` / `clan` / `party` 等。"
                        "★ 它补的是一个**真实缺口**：三合一 MOD 的 C6-④「溢出修复」把"
                        "『当前血量 > 最大血量』的英雄夹回上限，**其验收判据就是比较这两个值**，"
                        "而此前**没有任何工具能读运行时血量** —— "
                        "`get_entity` 读的是**静态索引库**（预计算表，非运行时），"
                        "`bl_list_parties` 无 hp 字段，RBM 的 Debug.Print 也不落盘。"
                        "用法：不传参 ⇒ 列**玩家队伍**的英雄（主角+同伴）；"
                        "`heroId`（StringId 精确）或 `name`（名字包含）指名查；`all=true` 列全战役英雄。"
                        "⚠️ **`overflow = hitPoints - maxHitPoints`**（>0 即溢出，正是要夹回的量）；"
                        "⚠️ `hpReadable=false` 表示该字段**没读到** —— 此时 `hp/max` 的 0 **不是真值**，"
                        "别据此得出「没溢出」；"
                        "⚠️ **不提供 `woundedLimit`** —— `Hero.WoundedLimit` 在 1.4.8 上**实测不存在**，"
                        "宁缺勿造（重伤状态请看 `isWounded`/`isAlive`）。"
                        "⚠️ 血量只在**战役**里有意义（战斗里是 `Agent.Health`，另一套）⇒ "
                        "主菜单/自定义战斗下报 `no_campaign`。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "heroId": {"type": "string",
                           "description": "英雄 StringId（**精确**匹配）"},
                "name": {"type": "string",
                         "description": "英雄名字（**包含**匹配，大小写不敏感）"},
                "all": {"type": "boolean",
                        "description": "true = 列全战役英雄（默认只列玩家队伍）"},
                "limit": {"type": "integer", "description": "最多几个，默认不限"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_scan_bad_data",
        "description": ("**坏数据扫描（只读，需战役上下文）**：找存档里的坏数据 —— "
                        "死部队 / 空家族 / 无效物品 / 卡死任务 / 损坏军团。"
                        "★ **本工具只扫描、不改任何数据**；清理必须另行实现且先备份"
                        "（写新档、不覆盖原档）。"
                        "返回按 `kind`（party/clan/kingdom/army/quest/item）分组的 findings，"
                        "每条带 `severity` / `code` / `subject` / `detail` / **`evidence`**（判据用到的事实，便于人工复核）。"
                        "⚠️ **严重度两档，意义完全不同**："
                        "`broken` = **结构上不可能正常**（如活跃部队却没有 PartyComponent、"
                        "任务已结束却仍登记在 QuestManager 里）；"
                        "`suspect` = **可疑**，可能只是游戏正常的过渡状态"
                        "（如刚被灭的家族、换帅瞬间的部队）⇒ **不可作为自动清理依据**。"
                        "⚠️ **`skipped` 字段必须看**：若某类扫描不了（成员缺失），它会显式列出 —— "
                        "否则「某类 0 条」会被误读成「这一类很干净」。"
                        "★ **`coverage` 是另一回事**：它是**正常**的覆盖口径（例如物品扫描"
                        "**共扫了哪些 roster** —— 全部队伍 + 聚落 + `Settlement.Stash`）——"
                        "`skipped`=异常（没扫成）/ `coverage`=正常（扫了什么），**两者别混读**；"
                        "看 `coverage` 才能判断「物品 0 条」到底是扫遍了还是只扫了一处。"
                        "⚠️ 实测（1.4.8）：军团在 `Kingdom.Armies` 上，"
                        "**`Campaign.Armies` 不存在**（任务同理走 `Campaign.QuestManager.Quests`）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "only": {"type": "string",
                         "description": "只扫某几类（逗号分隔）：party,clan,kingdom,army,quest,item。不传=全扫"},
                "limit": {"type": "integer",
                          "description": "报告最多返回几条 finding（0=不限；仅截断报告，不影响扫描量）"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_get_perk",
        "description": ("**读取 Perk 的运行时生效值（只读，需战役上下文）**：返回 C7 会改写的那 6 个 "
                        "Perk 的 `primaryBonus` / `secondaryBonus`（各带 `...Readable` 标志），"
                        "以及 `primaryIncrementType` / `requiredSkillValue` / `skill`。"
                        "★ 它补的是一个**真实缺口**：三合一 C7 会改写 6 个 Perk 的 bonus，"
                        "而验『值真的被改写了』此前**没有任何工具**能读运行时 Perk 值 ⇒ "
                        "V2 只能退化成『看游戏内 Perk tooltip』（需真人、不精确）。"
                        "★ **Primary 与 Secondary 分开返回，必须按『每个字段各自的预期』核对** —— "
                        "反编译实测原版基线：这 6 个 Perk 的 Primary **全是 0.002**、Secondary **全是 0.005**；"
                        "而目标值对 Primary/Secondary 是同一个数（0.001 或 0.002）⇒ "
                        "对 `_bowDeadshot` / `_crossbowMightyPull` / `_throwingUnstoppableForce` 而言"
                        "**Primary 本来就不该变**（目标 0.002 == 原版 0.002）。"
                        "⚠️ 所以**不要**把判据写成『开关前后两次读数必须不同』—— "
                        "那会把『正确』误判成『工具无效』。"
                        "⚠️ **本工具不返回调用方的目标值**（BlBridge 刻意不引入 mod 语义）——"
                        "目标值请在三合一侧对照。"
                        "⚠️ `xxxReadable=false` 表示该属性**没读到**（此时 `null` **不是 0**）。"
                        "⚠️ `DefaultPerks` 是 `Campaign` 的属性 ⇒ **主菜单/自定义战斗下为 null** ⇒ "
                        "报 `no_campaign`，**不返回 0**（防『读到 0 当成映射成 0』）。"
                        "用法：不传参 ⇒ 列那 6 个；`perk=_bowDeadshot`（私有字段名，可省 `_`）"
                        "或 `perk=BowDeadshot`（StringId）指名查；`all=true` 列全部 Perk。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "perk": {"type": "string",
                         "description": "Perk 字段名（如 `_bowDeadshot`，`_` 可省）或 StringId（如 `BowDeadshot`）"},
                "all": {"type": "boolean",
                        "description": "true = 列出全部 Perk（默认只列 C7 相关的那 6 个）"},
            },
            "additionalProperties": False,
        },
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
                        "★ **`isSandbox` / `isStoryMode` / `campaignType`（v0.8.55）**："
                        "**一眼区分这是沙盒还是剧情战役** —— 此前没有这个字段，"
                        "只能靠翻存档元数据猜，极易搞错档。"
                        "判据是 `Campaign.Current` 的**运行时类型**"
                        "（沙盒 = `TaleWorlds.CampaignSystem.Campaign`；"
                        "剧情 = 子类 `StoryMode.CampaignStoryMode`），"
                        "**不是** `CampaignGameMode`（那个枚举只有 None/Campaign/Tutorial，不区分二者）。"
                        "另附 `activeSaveSlot`（当前活动存档位）。"
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
    # ── 战役观察者（v0.8.50，observer 层）────────────────────────────────
    #
    # ★ 这四个补的是**结构性缺口**：此前所有 bl_campaign_* / bl_list_* 都是**轮询快照**，
    #   "快进 5 天 → 看一眼"两次采样之间的**所有事件都丢了**。而 mod 测试要回答的
    #   "某位领主下令之后命令生效了没有"、"哪两支队伍打起来了"恰恰是**事件**。
    #   ⇒ C# 侧订阅 9 个 CampaignEvents，写 campaign_events.jsonl；这里读它。
    #
    # ⚠️ 需要 v0.8.50+ DLL（含 CampaignObserver）。老 DLL 调它会得到 unknown_method。
    {
        "name": "bl_observer_status",
        "description": ("观察者状态：是否启用 / 已记录条数 / 缓冲占用 / **丢弃计数** / "
                        "写手路径与错误 / 各事件类型计数 / **已订阅的事件名清单**。"
                        "★ `droppedFromBuffer` 必须看：它 >0 表示环形缓冲丢过数据，"
                        "此时 `bl_observer_events` 返回的**不是全量** —— 别把『丢了』读成『没发生』。"
                        "★ `writerOpen=false` 且 recorded=0 说明**写手没开**（而非没事件）—— 两者必须分清。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_observer_events",
        "description": ("读最近战役事件（**事件驱动，不是快照**）。返回原始 JSON 行，不转述、不裁剪字段。\n"
                        "事件类型：`map_event_started`（哪两支队伍开打/什么类型/双方实力）、"
                        "`map_event_ended`（结果/退却方/双方兵力）、`siege_started`、"
                        "`settlement_owner_changed`（聚落易主）、`war_declared` / `make_peace`（外交）、"
                        "`party_destroyed`、**`ai_behavior_changed`**（★ 领主意图变了 —— "
                        "`was`→`now` 就是『命令是否生效』的直接读数）、`ai_tick`（仅 verboseAi=true）、"
                        "`observer_start`。\n"
                        "每条都带 `seq` / `utc` / `day`（战役天数）⇒ 可与 `bl_campaign_time` 对账。\n"
                        "增量拉取：传 `sinceSeq` 只看更新的（配 `bl_observer_status` 的 recorded）。"),
        "inputSchema": {"type": "object", "properties": {
            "type": {"type": "string",
                     "description": "按类型过滤，逗号分隔多个（如 'map_event_started,ai_behavior_changed'）；不传=全部"},
            "limit": {"type": "integer", "description": "最多返回多少条（默认 50，上限 500）"},
            "sinceSeq": {"type": "integer", "description": "只看 seq 大于它的（增量拉取用）"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_observer_config",
        "description": ("运行时调观察者：`enabled`（总开关，默认 true）/ `verboseAi`"
                        "（是否连『未变化』的 AI 决策也记 —— 默认 false，因为 AI tick 每支队伍每 "
                        "1/3/6 小时触发一次，全记会淹没信号并冲爆缓冲）/ `maxEvents`（环形缓冲容量，默认 2000）。"
                        "三个字段都可选，**只改给了的那些**（缺省=保持现状，不会猜成 false）。"),
        "inputSchema": {"type": "object", "properties": {
            "enabled": {"type": "boolean"},
            "verboseAi": {"type": "boolean"},
            "maxEvents": {"type": "integer"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_observer_clear",
        "description": ("清空观察者内存缓冲（**累计计数与 dropped 保留** —— 它们是本次会话的事实，"
                        "不能因为清屏而消失）。用于『跑一段 → 清空 → 再跑一段』的干净对照。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    # ── 战役刺激器（v0.8.50）：**会改存档**，与上面的只读 observer 相反 ──
    #
    # ★ 为什么必须有：`war_declared` / `make_peace` / `siege_started` /
    #   `settlement_owner_changed` 这 4 个观察者处理器，在**平静存档里永远等不到** ——
    #   沙盒开局所有王国互相和平，不宣战就不会有围攻、不会有聚落易主。
    #   ⇒ 那不是代码坏，是**条件不存在**。这三个工具负责**造出条件**。
    #
    # ⚠️ 这三个是**写操作**（与 bl_observer_* 的只读性质相反）：
    #   走官方 `DeclareWarAction` / `MakePeaceAction` ⇒ 事件正常派发。
    #   全部"写完回读"确认，verify 为 false 时**当作失败**处理。
    {
        "name": "bl_war_status",
        "description": ("**造刺激前先看现状**：列出现在哪几对王国在交战（`wars[]` 带 name/stringId/fiefs）"
                        "+ 每个王国的交战数 `warCountByKingdom`。\n"
                        "为什么需要它：`bl_declare_war` 若挑到一对**本来就在交战**的势力，"
                        "会返回 `already_at_war` 而**不触发** `WarDeclared` 事件 —— 先查这个能避免白跑。"
                        "`warCount == 0` 说明这是平静档，战斗类事件观察者收不到。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_declare_war",
        "description": ("★ **让两个王国开战**（会真实改存档）。用来**确定性地**造出 `WarDeclared` 事件，"
                        "从而验证观察者与后续连锁（围攻 / 野战 / 聚落易主）。\n"
                        "走官方 `DeclareWarAction.ApplyByDefault` ⇒ 关系表 + PoliticalStagnation + "
                        "可见实体刷新 + **`OnWarDeclared` 事件**全部按官方路径发生。\n"
                        "参数 `faction1` / `faction2` 用 **StringId**（如 `empire_w` / `vlandia`，"
                        "不受本地化影响）或名字（含中文名）匹配；**不传 `faction2` 会自动挑一个"
                        "还没和 faction1 交战、封地最多的王国**（省得先查名字）。\n"
                        "★ **写完回读**：返回 `verifiedAtWar` —— 为 false 时**当作失败**处理，"
                        "别假设宣战成功了。\n"
                        "⚠️ 本来就在交战 ⇒ 返回 `already_at_war` 且**不做改动**（事件不会再触发）。"),
        "inputSchema": {"type": "object", "properties": {
            "faction1": {"type": "string", "description": "StringId（如 empire_w）或名字；必填"},
            "faction2": {"type": "string",
                         "description": "StringId 或名字；**不传则自动挑一个还没交战的王国**"},
            "detail": {"type": "string",
                       "description": ("宣战原因（默认 Default）。可选：CausedByPlayerHostility / "
                                       "CausedByKingdomDecision / CausedByRebellion / "
                                       "CausedByCrimeRatingChange / CausedByKingdomCreation / "
                                       "CausedByClaimOnThrone / CausedByCallToWarAgreement")}},
            "required": ["faction1"], "additionalProperties": False},
    },
    {
        "name": "bl_make_peace",
        "description": ("让两个交战的王国**议和**（会真实改存档）。走官方 `MakePeaceAction.Apply` ⇒ "
                        "`MakePeace` 事件正常派发。\n"
                        "用途：造「一打一和」的对照（同一对势力，先看开战事件、再看议和事件）。\n"
                        "不传 `faction2` 会自动挑一个**当前正与 faction1 交战**的王国。\n"
                        "★ 写完回读：`verifiedPeace` 为 false 时**当作失败**。\n"
                        "⚠️ 本来就没交战 ⇒ 返回 `not_at_war` 且不做改动。\n"
                        "⚠️ **同文化永久战议和不了**（引擎的 `IsAtConstantWar`：帝国三分之间、"
                        "以及同文化的小阵营 vs 王国），此时 `verifiedPeace=false` 属**预期行为**，"
                        "不是工具坏了 —— 换一对不同文化的王国测。"),
        "inputSchema": {"type": "object", "properties": {
            "faction1": {"type": "string", "description": "StringId 或名字；必填"},
            "faction2": {"type": "string", "description": "StringId 或名字；不传则自动挑当前交战方"}},
            "required": ["faction1"], "additionalProperties": False},
    },
    {
        "name": "bl_campaign_time_speed",
        "description": ("★ **让战役时间前进**（等价于点屏幕中间那个「继续 / 倍速」键）。\n"
                        "**这是整套观察者的前提** —— 读档后战役默认处于 `Stop`，不推时间就"
                        "一条 AI / 战斗 / 围攻事件都不会产生（观察者只会记到 `observer_start`）。\n"
                        "参数 `speed`：`0`=停 / `1`=正常 / `2`=加速 / `3`=最快。"
                        "**推荐用 2 或 3** —— 它们映射到 `Unstoppable*` 档，**无条件推进时间**；"
                        "`1` 也映射到 Unstoppable（保证无人值守时确定性流动）。\n"
                        "也可用 `mode` 直接指定：Stop / Play / FastForward / UnstoppablePlay / "
                        "UnstoppableFastForward。\n"
                        "★ `multiplier`（可选，正数，原版上限非开发 15 / 开发 30）：设置"
                        "`SpeedUpMultiplier` —— 等价于控制台 `campaign.set_campaign_speed_multiplier N`，"
                        "**不依赖开控制台**。⚠️ 倍率**只在 FastForward 两档生效**；"
                        "只给 multiplier 不给 speed/mode 时会自动配 `UnstoppableFastForward` 档。"
                        "返回里 `multiplierInEffect` 说明倍率本档是否真的生效。\n"
                        "★ **写完回读**：返回 `before` / `after` / `effective` / `multiplierBefore` / "
                        "`multiplierAfter` —— `effective=false` 说明没设上（多半是 `TimeControlModeLock`）。\n"
                        "⚠️ 与 `bl_fast_forward` 的区别：那个是**战斗内**的 10x（Mission 通道，"
                        "对大地图无效）；这个是**战役大地图**的时间流速。两者互不相干。"),
        "inputSchema": {"type": "object", "properties": {
            "speed": {"type": "integer", "enum": [0, 1, 2, 3],
                      "description": "0=停 / 1=正常 / 2=加速 / 3=最快（推荐 2 或 3）"},
            "mode": {"type": "string",
                     "description": "直接指定模式（与 speed 二选一）：Stop/Play/FastForward/UnstoppablePlay/UnstoppableFastForward"},
            "multiplier": {"type": "number",
                           "description": ("可选：设 SpeedUpMultiplier（正数；上限 15，开发模式 30）。"
                                           "只在 FastForward 档生效；只给它会自动配 UnstoppableFastForward")}},
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
    # ── 存档（v0.8.53）：**只存不退** ─────────────────────────────────────
    #
    # ★ 为什么不提供"保存并退出到主菜单"：
    #   官方那条路径是 `QuickSave` + `MBGameManager.EndGame()`（`Module.cs:529`）。
    #   但 `EndGame()` 会**卸载整个战役** ⇒ 我们的观察者与命令泵一并关停
    #   ⇒ **MCP 通道本身断了**，后续任何测试都要先重启游戏。
    #   对测试夹具来说那是自杀式操作 ⇒ 只做"存"，把"退"留给用户/进程层。
    #
    # ★ 异步纪律：引擎存盘是**跨帧状态机**（`SaveHandler.SaveTick`：
    #   PreSave → Saving → AwaitingCompletion → 完成），发起后必须**轮询**。
    #   `bl_save_game` 返回的 `queued=true` **只代表入队，不代表存完**。
    {
        "name": "bl_save_game",
        "description": ("**保存当前战役（只存不退）**。`mode=quick`（默认，等价按 F5）或 "
                        "`mode=as` + `name`（另存为新档）。\n"
                        "★ **这是异步的**：引擎存盘是跨帧状态机，本工具返回的 `queued=true` "
                        "**只表示已入队，不表示存完了**。请接着用 `bl_save_status` 轮询到 "
                        "`isSaving=false`，并核对 `newestSave.utc` **晚于** 本工具返回的 "
                        "`issuedUtc` —— 两条同时成立才算真落盘。\n"
                        "⚠️ 正在存盘时再调会被拒绝（`save_in_progress`）—— 引擎的状态机是单条的，"
                        "重复排队会把两次都搞坏。\n"
                        "⚠️ **不提供『保存并退出』**：官方 `EndGame()` 会卸载战役、连带切断 MCP 通道，"
                        "对测试流程是自杀式操作。要退出请用进程层（或让它正常关闭）。"),
        "inputSchema": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["quick", "as"],
                     "description": "quick=快存（等价 F5，默认）/ as=另存为（需配 name）"},
            "name": {"type": "string", "description": "mode=as 时的档名（不能用引擎保留名）"}},
            "additionalProperties": False},
    },
    {
        "name": "bl_save_status",
        "description": ("存档状态：**是否正在存盘**（`isSaving`）+ 硬盘上最新那份档的时间戳与大小 "
                        "（`newestSave`）+ 全部存档清单（按时间倒序）。\n"
                        "★ 与 `bl_save_game` 配对使用：只有 `isSaving=false` **且** "
                        "`newestSave.utc` 晚于发起时刻，才能判定『真的存上了』。\n"
                        "⚠️ 只看 `isSaving` 不够 —— 若发起时引擎恰好刚存完一帧，它可能瞬间就是 false。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_return_to_menu",
        "description": ("**回主菜单**（卸载当前战役，**不关游戏进程**）—— 对应游戏里那个"
                        "『保存并退出到主菜单』。\n"
                        "默认 `saveFirst=true`：**先存档、等存完再退**（跨帧待办，见下）。\n"
                        "★ **通道不会断**（实测 + 源码证据）：`Module.OnApplicationTick` 每帧"
                        "**无条件**遍历所有 submodule，而 `MBGameManager.EndGame()` 只 "
                        "`CleanStates()`、不调 `OnSubModuleUnloaded` ⇒ 退回主菜单后 "
                        "`bl_status` / `bl_list_saves` / `bl_load_save` **仍然可用**，可以换档再进。\n"
                        "⚠️ **这是跨帧操作**：返回 `ok` 只表示『已发起/已登记待办』，"
                        "**不代表已经退出**。请轮询 `bl_campaign_time` 的 `inCampaign` 变 false 确认。\n"
                        "⚠️ `saveFirst=true` 时若存盘进行中会被拒绝（`save_in_progress`）——"
                        "存盘状态机是单条的，抢它会坏档。\n"
                        "可选 `name`：给了则**另存为**该档名后再退（不给则用快存位）。"),
        "inputSchema": {"type": "object", "properties": {
            "saveFirst": {"type": "boolean",
                          "description": "true（默认）=先存档再退；false=直接退（不保存）"},
            "name": {"type": "string",
                     "description": "可选：另存为的档名（不给则走快存位）"}},
            "additionalProperties": False},
    },
    # ── 遭遇/对话（v0.8.57）──────────────────────────────────────────────
    #
    # ★ 为什么需要：15x 快进时主角**必然**撞到强盗/领主遭遇，游戏弹
    #   "给钱 or 战斗"二选一并把时间锁成 `Stop` ⇒ **无人值守快进就此中断**
    #   （实测现场：`inMenuContext=True`、`MapConversation` 层 `isActive=true`、
    #     `CharacterNameIdParent="湖鼠资深勇士"`、两个 `OptionButton`）。
    #
    # ★ 为什么不用 `bl_desktop_click` 点坐标：实测那两个 `OptionButton` 的 `text`
    #   都是**空的**（文案在子控件里）⇒ 按坐标点 = **盲操作**，点错可能直接开战。
    #   走官方 `ConversationManager.CurOptions` + `ProcessSentence()` ——
    #   与玩家点该选项**同一入口**。
    {
        "name": "bl_conversation",
        "description": ("**读当前遭遇/对话的可选项（只读）**。用于 15x 快进被遭遇弹窗中断时"
                        "看清『现在卡在什么选择上』。\n"
                        "返回 `inConversation` / `currentSentence` / "
                        "`options[]`（每项带 `index` / `text` / `sentenceNo` / `isClickable` / `isSpecial`）。\n"
                        "★ **本工具只列选项，不替你选** —— 选哪个是**玩家的决定**"
                        "（掉钱 / 开战 / 损失兵力，**不可逆**且会污染测试基线）。\n"
                        "要选请用 `bl_conversation_choose index=N`（**必须显式给编号**）。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "bl_conversation_choose",
        "description": ("**选中遭遇/对话里的某个选项**（⚠️ **会改变游戏状态，不可逆**）。\n"
                        "走官方 `ConversationManager.ProcessSentence(option)` —— 与玩家点该选项"
                        "**同一入口**。\n"
                        "★ 必须显式给 `index`（先用 `bl_conversation` 看列表）："
                        "**刻意不提供『自动挑一个』**，因为选项后果不可逆"
                        "（给钱会掉钱、战斗会开战/损兵），而且**污染的基线不可恢复**"
                        "（除非事先存过档）。\n"
                        "返回 `stillInConversation` / `remainingOptions`（多步对话要接着选）"
                        "与 `timeControlMode` —— 对话结束后时间常仍是 `Stop`，"
                        "需用 `bl_campaign_time_speed` 重新推起来。\n"
                        "⚠️ `isClickable=false` 的选项会被**拒绝执行**（否则『点了没反应』"
                        "会被误读成『操作成功』）。"),
        "inputSchema": {"type": "object", "properties": {
            "index": {"type": "integer",
                      "description": "选项编号（从 0 起），来自 bl_conversation 的 options[].index"}},
            "required": ["index"], "additionalProperties": False},
    },
    {
        "name": "bl_conversation_continue",
        "description": ("**推进对话**（对应玩家在 NPC 台词上按『继续』）。\n"
                        "★ 多步对话有**两个不同入口**，必须交替用：\n"
                        "  · **有选项**（`optionCount > 0`）⇒ `bl_conversation_choose index=N`\n"
                        "  · **无选项、只有 NPC 台词**（`optionCount == 0`）⇒ **本工具**\n"
                        "实机踩点：选了『够胆就过来动手吧！』后，对话推进到 NPC 回话"
                        "（『你在挑衅吗？…』），此时 `CurOptions == 0` —— 玩家在 UI 上这时"
                        "按的是**继续**键，而不是选选项。\n"
                        "返回 `progressed`（句子是否真的变了）与 `stillInConversation` / "
                        "`remainingOptions` ⇒ 依此决定下一步调哪个。\n"
                        "⚠️ 当前**有 >1 个选项**时会返回 `error:\"has_options\"` 而**不执行** ——"
                        "因为引擎的 `ContinueConversation()` 在 `CurOptions.Count > 1` 时"
                        "**静默不干活**（直接 return），静默返回成功会让人误以为推进了。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
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
        "name": "bl_crashguard",
        "description": ("**崩溃守卫账本（跳过崩溃的记账 + 修复建议）**："
                        "读 `<日志目录>\\crashguard.jsonl`，回答「**哪些异常本来会杀掉游戏、"
                        "被我们吞掉了**」以及「**哪些我们不敢吞**」，并**按异常类型给出修复建议**。"
                        "与 `bl_exceptions` 的分工：那个答「发生过哪些异常」（FirstChance，全集），"
                        "本工具只答「守卫放过了什么」——"
                        "FirstChance **只能观察不能阻止**，Harmony Finalizer 才是唯一能阻止传播的钩子。"
                        "⚠️ **`action=swallow` 不等于已修复**：吞掉只保证游戏没死，"
                        "被吞的方法**没做完它该做的事**，可能留下**不报错**的静默损坏"
                        "（存档不一致 / AI 卡死 / 数值错乱）—— 本工具据此标注，不报成「已修复」。"
                        "⚠️ `reason=breaker_open`/`quota_exhausted` 是**坏消息**（守卫已停止保护），"
                        "会被判 critical 并排在报告最前；`fatal_passthrough` 说明游戏**很可能仍崩了**"
                        "（那类异常永不吞）⇒ 用 `bl_crash` 看 minidump。"
                        "守卫**默认关闭**（`blbridge_game.json` 的 `crashGuardEnabled`）"
                        "⇒ 文件不存在**不等于**没崩溃，以 `enabled` 字段为准。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "最多列几条，默认 20"},
                "logDir": {"type": "string", "description": "日志目录（与 bl_status 同口径）"},
                "path": {"type": "string", "description": "直接指定 crashguard.jsonl 路径"},
            },
        },
    },
    {
        "name": "bl_source_map",
        "description": ("**源码定位（栈帧 → 文件:行号）**：把崩溃栈帧映射到源码位置并附"
                        "**真实源码片段**，补「栈只有方法名、agent 无法跳到那一行」的缺口。"
                        "三条路径：① 栈里自带行号（该 DLL 需用 `/debug:full` 编译 —— "
                        "**`.NET Framework` 不认 portable PDB**）；② 我们自己的符号索引 "
                        "（`out/BlBridge.symbols.json`）；③ **第三方 PDB 索引** "
                        "（`out/symbols-thirdparty/`，由各 mod 自带 PDB 抽出）。"
                        "⚠️ **解析与语言无关**：中文 Windows 的栈是 `位置 X.cs:行号 24`、"
                        "英文是 `in X.cs:line 24` —— 只认英文关键词的判据会在中文系统上"
                        "把「有行号」判成「没有」。"
                        "⚠️ **拿不准就不给**：第三方帧若同名方法散在多个程序集**且行号不同**"
                        "（实测 `HarmonyExtensions.cs` 89 vs 90：BUTR 共享库被复制进每个 mod），"
                        "或 PDB 里有**不可信行号**（实测出现 16707566），一律如实说"
                        "「无法确定」—— **绝不编造行号**（错行号比没有更坏）。"
                        "⚠️ 第三方**没有源码片段**（它们的源码不在本仓库）。"
                        "⚠️ 实测覆盖：真实方法名抽样 333 个 → **约 75% 可定位**"
                        "（对比改造前只有 1%，因为当时只覆盖我们自己的代码）；"
                        "其余因真歧义或 PDB 损坏被如实拒绝。"
                        "生成索引：`python tools/bl_symbols.py --build` / `--third-party`。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "resolve": {"type": "string",
                            "description": "解析一个方法全名（如 BlBridge.CrashGuard.Finalizer）"},
                "stack": {"type": "string", "description": "直接给一段栈文本"},
                "fromExceptions": {"type": "boolean",
                                   "description": "读 exceptions.jsonl 逐条定位"},
                "logDir": {"type": "string", "description": "日志目录（与 bl_status 同口径）"},
            },
        },
    },
    {
        "name": "bl_save_diag",
        "description": ("**存档诊断（宿主侧，只读）**：比对「存档里记录的模组集」与"
                        "「启动器当前启用的模组集」，找出**会导致读档崩溃的不一致**："
                        "① 存档需要但当前**未启用**的模组；② **版本漂移**（框架级高风险）；"
                        "③ 存档自身的 `isCorrupted` 标记；④ 当前新加的模组（多数无害）。"
                        "⚠️ **本工具只读**：不写、不改、不备份存档 —— 存档修复是最难验证的一环"
                        "（能读到'文件能打开'≠'战役数据没被改坏'），所以只给判断与建议。"
                        "⚠️ **缺模组 ≠ 一定崩**，只说明风险：那个 mod 的数据还在存档里、代码不在。"
                        "⚠️ **版本不同 ≠ 不安全**：按 framework/content 分级标风险，不搞一刀切。"
                        "游戏本体模块（Native/SandBox 等）随游戏版本变，已降级处理，否则人人报错。"
                        "存档清单取自**最近一次** `bl_list_saves` 的响应；没采集过会如实说明。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "最多诊断几个存档"},
                "logDir": {"type": "string", "description": "日志目录（与 bl_status 同口径）"},
                "launcher": {"type": "string", "description": "LauncherData.xml 路径（默认自动定位）"},
            },
        },
    },
    {
        "name": "bl_report",
        "description": ("**崩溃报告导出（宿主侧，只读）**：把三个数据源合成一份"
                        "**自包含**报告 —— `crashguard.jsonl`（守卫账本）+ `exceptions.jsonl`"
                        "（全量异常）+ `bridge_status.json`（会话身份），并可选联动词典给出"
                        "修复建议。输出**单文件 HTML**（内联 CSS、**无外部依赖、不联网**）"
                        "或 Markdown。"
                        "⚠️ 报告口径与 `bl_crashguard` 一致：**「被吞掉」不等于「已修复」**。"
                        "报告内**不含**存档内容、账号或凭据；导出**不修改**任何源文件；"
                        "默认**不覆盖**已有文件（同秒自动加序号）。"
                        "日志内容一律 HTML 转义（日志是不可信输入）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": ["html", "md"],
                           "description": "默认 html"},
                "out": {"type": "string", "description": "输出目录（默认 <LogDir>\\reports）"},
                "stdout": {"type": "boolean", "description": "直接返回文本而不写文件"},
                "logDir": {"type": "string", "description": "日志目录（与 bl_status 同口径）"},
            },
        },
    },
    {
        "name": "bl_lexicon",
        "description": ("**崩溃词典（人话解释 + 修复建议）**：按异常类型或一段崩溃文本匹配词条，"
                        "返回描述、常见场景、修复建议。补的是 `bl_crash --deep` / `bl_exceptions` "
                        "**只有符号栈、说不出'该怎么办'** 的那一层。"
                        "⚠️ **词条数据不随本仓库分发**（上游作品无许可声明 ⇒ 默认保留所有权利）"
                        "⇒ 需用 `BLBRIDGE_LEXICON_DIR` 指向本地词条目录；**缺数据时如实报 installed=false**，"
                        "不会返回空结果冒充'没有匹配'。"
                        "口径：匹配是**短语子串**（MatchAny 任一 / MatchAll 全部 / ExcludeAny 排除），"
                        "**不是语义匹配** ⇒ 匹配不到**不等于**没问题。"
                        "`priority` 只用于排序，**不是置信度**。"
                        "词条 `zh` 缺失时会回退英文并**如实标注** `langFellBackToEn`（上游 legacy 档只有英文）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string",
                         "description": "异常类型（短名或全名，如 NullReferenceException）"},
                "text": {"type": "string",
                         "description": "按一段崩溃报告/日志片段做短语匹配"},
                "lang": {"type": "string", "enum": ["zh", "en"],
                         "description": "文本语言，默认 zh"},
                "limit": {"type": "integer", "description": "最多几条匹配，默认 10"},
                "dir": {"type": "string",
                        "description": "词条目录（默认取环境变量 BLBRIDGE_LEXICON_DIR）"},
                "statusOnly": {"type": "boolean",
                               "description": "只报词典装载状态（装没装上、为什么）"},
            },
        },
    },
    {
        "name": "bl_concurrency_guide",
        "description": ("**并发纪律（宿主侧，只读，不碰游戏）**：告诉你**哪些工具能并发、最多几路**。"
                        "为什么需要：**所有走游戏通道的工具共用同一条串行泵**"
                        "（`commands/pending/` → 游戏主线程 `Array.Sort` 后逐个 `HandleOne`）。"
                        "三路实测结论：① 并发提交**可行**（`pending/` 深度实测 max=8）；"
                        "② 执行**严格串行**（`seq` 单调、**游戏执行序 == 文件名序**）；"
                        "③ 但代价**不会立刻线性外溢**——游戏端一次轮询把整批捞走，"
                        "单次成本远小于 250ms 周期时开销藏进周期里；"
                        "★ 外溢判据：**N × 单次耗时 >= 250ms** 才开始外溢。"
                        "本工具**两次派发都从现场取数**（工具→方法来自解析 `bl_mcp.py` 的闭包；"
                        "耗时来自实测账本的分位数），所以**不会随工具改名/负载变化而腐化**。"
                        "★ 用 **p90 而非中位数**判重（实测 `skip_video` 双峰：p50=1ms 但 p90=459ms，"
                        "只看中位数会误判成轻工具）。"
                        "分档：HEAVY(p90>=100ms，**不要并发**) / MEDIUM(>=20ms，上限见 budget) / "
                        "LIGHT(<20ms，可自由并发) / HOST(不走通道，**零争用**) / "
                        "UNKNOWN(走通道但无实测 ⇒ 保守按 MEDIUM)。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "description": "只看某个工具（如 bl_start_battle）"},
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
        "name": "bl_dump",
        "description": ("按需让**游戏进程自己**写一份 minidump（B2 / v0.8.48）。"
                        "与 WER 的 dump 的关键差别：**显式带 MiniDumpWithFullMemoryInfo(0x800)** "
                        "⇒ 有 MemoryInfoList（页保护 r/w/x）—— 那是区分「模块外那段内存」"
                        "是 JIT 代码还是 Harmony detour 的**唯一**依据。"
                        "实测：WER 的 6 份 dump **全都没有**这一位；BlBridge 落的都有。"
                        "★ 不崩也能调（实测健康进程写出的 dump 同样带页保护），"
                        "所以它能覆盖 bl_crash 结构上覆盖不到的「卡住/僵死但进程还活着」。"
                        "成本实测：只多 0.02 MB / 0 ms（MemoryInfoList 流仅 ~17 KB）；"
                        "fullMemory=true 才加 FullMemory（**49 MB / 25 倍**，默认 false）。"
                        "先跑 bl_status 确认游戏在跑；落盘目录见返回的 path。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fullMemory": {"type": "boolean", "default": False,
                               "description": "是否附带 FullMemory（49 MB / 25 倍，默认 false）"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "bl_crash_test",
        "description": ("**受控崩溃**：让游戏进程显式崩一次（纯 SEH 0xC0000005），"
                        "用于验收 B2 的崩溃落盘路径。**游戏会真的崩掉**。"
                        "★ 双重安全闸门（缺一不可）：① 必须显式调本工具；"
                        "② 必须设环境变量 BLBRIDGE_ALLOW_CRASH_TEST=1（**默认关**）⇒ 发布版不可能误崩。"
                        "为什么用 RaiseException 而不是抛托管异常：实测四条崩溃路径里它**最难覆盖**"
                        "（托管钩子不触发、只有 Win32 顶层过滤器触发）；验它通过，更容易的路径自然没问题。"
                        "调用后：先返回 aboutToCrash=true 的响应，**随后**进程崩溃 ⇒ "
                        "用那个响应可区分「主动崩」与「随机崩」。"
                        "⚠️ 已知覆盖不到 Environment.FailFast(0xC0000409)，那类继续走 WER。"),
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
        "bl_get_hero", "bl_scan_bad_data", "bl_get_perk",
        # v0.8.50：战役观察者（事件流，非快照）。放 core 的理由：它是"读战役正在发生什么"
        # 的**首选入口**，而 core 正是"只读读数据/记录日志/分析现状"那一档的落点。
        "bl_observer_status", "bl_observer_events",
        "bl_observer_config", "bl_observer_clear",
    ],
    # ── v0.8.50：战役刺激器（**写操作**）────────────────────────────────
    # 放 config 而不是 core：这一组的定位就是"改游戏状态"（config 现有成员
    # bl_apply_config / bl_apply_rts_config / bl_cheat_mode 同类）。
    # ⚠️ 尤其 `bl_declare_war` 会**真实改变外交关系**——归到只读的 core 组会误导。
    "stimulus": [
        "bl_war_status", "bl_declare_war", "bl_make_peace", "bl_campaign_time_speed",
        # v0.8.53：存档（只存不退）。归 stimulus 的理由：它和上面同属
        # "改游戏状态/持久化"那一档；**不是**只读的观察类工具。
        "bl_save_game", "bl_save_status",
        # v0.8.54：回主菜单（卸载战役，不关进程）。同属"改状态"档。
        "bl_return_to_menu",
        # v0.8.57：遭遇/对话（读选项 + 显式选择）。拦住 15x 快进的那个"必经弹窗"。
        "bl_conversation", "bl_conversation_choose", "bl_conversation_continue",
    ],
    "config": [
        "bl_read_config", "bl_apply_config", "bl_rts_config", "bl_apply_rts_config",
        "bl_ghost_camera", "bl_camera_speed", "bl_cheat_mode",
    ],
    "lab": [
        "bl_list_battles", "bl_analyze", "bl_read_events", "bl_run_batch", "bl_batch_report",
        "bl_lookup_troop", "bl_blockade", "bl_build_check", "bl_config", "bl_crash", "bl_patches",
        "bl_json_health", "bl_ipc_replay", "bl_exception_detail", "bl_concurrency_guide",
        "bl_lexicon",
        "bl_crashguard",
        "bl_report", "bl_save_diag", "bl_source_map",
        "bl_mcm_settings", "bl_ui_extensions", "bl_exceptions", "bl_patch_failures",
        # B2（v0.8.48）：崩溃落盘 —— 诊断族，与 bl_crash 同类
        "bl_dump", "bl_crash_test",
    ],
    "desktop": [
        "bl_desktop_windows", "bl_desktop_screenshot", "bl_desktop_click", "bl_desktop_key",
    ],
    # ── ★ v0.8.47：`4b` —— 为小模型（4B 级）准备的窄工具面 ──────────────
    #
    # ## 为什么单独成组
    #
    # 全量 55 个工具的 `tools/list` = **59,973 字节 ≈ 17,428 token**（实测）。
    # 4B 级模型上下文预算小、且**工具选择能力弱** ⇒ 塞全量基本不可用。
    #
    # 本组按**真实用途**挑选（用户 2026-10-06 明确：mod 测试 / 查 bug / 只读分析现状），
    # 实测 **10 个工具 = 5,048 字节 ≈ 1,420 token（占全量 8%）**。
    #
    # ## 选材依据（不是拍脑袋，见 `bl_concurrency_guide` 的实测分档）
    #
    #   ① **零争用优先**：宿主侧工具不碰 `pending/`，8 路并发实测总墙钟 0.24s
    #      ⇒ `bl_crash` / `bl_status` / `bl_analyze` / `bl_list_battles` / `bl_read_events`
    #   ② **诊断核心**：查 bug 的四件套（崩溃 / 异常 / 补丁失败）
    #   ③ **开战必须有**：用户明确"开战测试属于 mod 测试"
    #      ⚠️ 但 `bl_start_battle` **单工具就 8,278 字节（2,400 tok）**——
    #         32 个属性里 **24 个是实验/调试专用**（说明合计 4,469 字节）。
    #         本组**只做工具过滤、不改 schema**（用户裁定："先不砍"）
    #         ⇒ 强模型看全量时行为**一个字节不变**。
    #   ④ **统计**：`bl_concurrency_guide` 判 `bl_start_battle` 为 **HEAVY（p90 605ms）**
    #      ⇒ 4B 调它时**不要并发**（纪律见该工具）。
    #
    # ## 边界（如实）
    #
    #   · 本组**只影响 `tools/list` 暴露面**，不改变任何工具的行为；
    #   · **不默认启用** —— 用 `BLBRIDGE_TOOLSET=4b` 显式切换；
    #   · `bl_wait_for_state` 必须与 `bl_start_battle` 同组（开战后的配套等待）。
    "4b": [
        # ① 零争用（宿主侧，不占串行泵）
        "bl_status", "bl_crash", "bl_exceptions", "bl_patch_failures",
        "bl_list_battles", "bl_analyze", "bl_read_events",
        # ② 游戏内只读（轻通道，p90 ≤ 1ms）
        "bl_battle_status",
        # ③ 开战 + 配套等待（HEAVY，勿并发）
        "bl_start_battle", "bl_wait_for_state",
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


def _observer_dll_hint(code):
    """观察者工具专用的错误补注。

    ★ 为什么必须有它：老 DLL（< v0.8.50）收到 `observer_*` 会回 `unknown_method`。
    若只把这句话原样透传，调用方极容易把它读成**"观察者跑了，只是没有事件"** ——
    而真相是**工具本身不存在**。这两件事的后果完全不同：
      · "没有事件" ⇒ 结论"这段时间战役很平静"（**可能完全错误**）；
      · "工具不存在" ⇒ 结论"我什么都没测到，得先部署"。
    ⇒ 本项目一贯纪律：**宁可显式说"没测到"，也不让失败伪装成"没问题"**。
    """
    if code == "unknown_method":
        return {"hint": ("当前部署的 BlBridge.dll **不含观察者**（需 v0.8.50+）。"
                         "这不是「没有事件」，而是工具本身不存在 —— "
                         "请先 `build.ps1 -Deploy` 并重启游戏。")}
    return {}


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
        # ── B5（2026-10-07，隔壁项目测试表发现）────────────────────────────
        #
        # 现象：同一响应里 `sessionDiagnosis.verdict` 报 `crashed_in_battle`，
        # 而 `buildCheck.code` 报 `ok`（"四段一致"）—— 并列出现易被读成**自相矛盾**。
        #
        # ★ 复核结论：**两者说的不是同一件事，所以都"对"**：
        #   · `sessionDiagnosis.verdict` —— 上次会话**是否正常退出**（运行时/行为面）；
        #   · `buildCheck.code`         —— 「源码→产物→部署→进程内 DLL」**文件链条是否一致**
        #                                 （静态/构建面）。
        #   ⇒ 崩溃 + 文件链条一致，是**完全正常**的组合（崩了不等于文件坏了）。
        #
        # ⇒ 所以不是"改文案"，而是**把两者的作用域写明**，让并列不再被误读。
        #   ⚠️ 刻意**不加**"互斥提示" —— 那会暗示两者应该有因果关系，反而是错的。
        st["scopes"] = {
            "sessionDiagnosis": ("**运行时/行为面**：上次会话是正常结束、还活着，还是崩了/被强杀。"
                                 "回答『游戏**跑得**怎么样』。"),
            "buildCheck": ("**静态/构建面**：源码 → 构建产物 → 部署文件 → 进程内 DLL "
                           "**四段是否一致**。回答『文件**对不对**』。"),
            "note": ("两者**互不蕴含**：崩溃而文件链一致是正常组合（崩了 ≠ 文件坏了）；"
                     "文件链不一致也不代表上次崩过。"
                     "⇒ 判『上次怎么结束的』看 `sessionDiagnosis.verdict`；"
                     "判『现在这份 DLL 是不是源码构建的』看 `buildCheck.code`。"),
        }
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
        # B1：未给 path 且目标模块已软卸载 ⇒ 结构化错误（含判据与替代方案），不抛异常。
        # A4：给了 path ⇒ 跳过闸门、读指定文件，并在返回里回显绝对路径。
        ok, payload = read_config(args.get("paths"), _structured=True,
                                  path=args.get("path"))
        return payload if not ok else {"ok": True, **payload}

    if name == "bl_apply_config":
        edits = args.get("edits") or []
        if not edits:
            return {"ok": False, "error": "edits 不能为空"}
        # ★ 防御性形状检查（2026-10-08）：`apply_config(...,_structured=True)` 的契约是
        #   返回 `(ok, payload)` **元组**。历史缺陷：成功分支返回裸 dict ⇒ 解包炸在**返回之后**，
        #   而那时文件**已经改完落盘** ⇒ 调用方以为失败、实际已改（最防的"报告与事实相反"）。
        #   ⇒ 这里显式断言形状：契约再被破坏时立刻报**可读**错误，而不是 `too many values to unpack`。
        _ret_shape = apply_config(edits, dry_run=bool(args.get("dry_run")),
                                  _structured=True, path=args.get("path"))
        if not (isinstance(_ret_shape, tuple) and len(_ret_shape) == 2):
            return {"ok": False, "error": "internal_return_contract_violation",
                    "detail": ("apply_config(_structured=True) 必须返回 (ok, payload) 元组，"
                               "实得 %r —— 这是**实现缺陷**（不是你的调用问题）。"
                               "⚠️ 若本次是**真写**，配置文件**可能已经被改**："
                               "请用 bl_read_config 回读确认，并检查同目录是否有 .bak_ 备份。"
                               % type(_ret_shape).__name__),
                    "path": args.get("path")}
        ok, payload = _ret_shape
        return payload if not ok else {"ok": True, **payload}

    # ── v0.8.15：RTSCamera 配置（B 方案：只当它的参数管理员，不碰它的代码）──────
    if name == "bl_rts_config":
        try:
            values = bl_rts.read(args.get("keys"), path=args.get("path"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e),
                    "path": args.get("path") or bl_rts.config_path()}
        # ★ 回显**实际读取的绝对路径**（与 A4 同口径）。
        _p = args.get("path") or bl_rts.config_path()
        out = {"ok": True, "path": os.path.abspath(os.path.expanduser(str(_p))),
               "pathSource": "explicit" if args.get("path") else "default",
               "values": values}
        if args.get("presets"):
            out["presets"] = dict(bl_rts.PRESETS)
        return out

    if name == "bl_apply_rts_config":
        try:
            # ★★ `path` 必须**转发**下去（2026-10-08 修，实测事故）：
            #   旧版这两个 RTS 工具的派发**既不声明也不转发 `path`** ⇒
            #   调用方传 `path=<副本>` 会被**静默丢弃**，实际写的是**真实**配置
            #   ⇒ 我的自测本想写临时副本，结果**改到了玩家的真档**（已按备份 sha 逐字节还原）。
            #   ⇒ 教训：**"参数没被转发" = 静默改错文件**，与 B1（改了没有消费者的死文件）
            #     是同一类"文件目标不可信"，但方向相反（这次是"以为在写副本，其实写了真档"）。
            if args.get("preset"):
                return bl_rts.apply_preset(str(args["preset"]),
                                           dry_run=bool(args.get("dry_run")),
                                           path=args.get("path"))
            edits = args.get("edits") or []
            if not edits:
                return {"ok": False, "error": "需要 preset，或非空的 edits"}
            return bl_rts.apply(edits, dry_run=bool(args.get("dry_run")),
                                allow_missing=bool(args.get("allow_missing")),
                                path=args.get("path"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e),
                    "path": args.get("path") or bl_rts.config_path()}

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

    if name == "bl_dump":
        # B2（v0.8.48）：让**游戏进程自己**写一份带页保护的 minidump。
        resp = send_command("dump_now", {"fullMemory": bool(args.get("fullMemory"))})
        return resp

    if name == "bl_crash_test":
        # B2（v0.8.48）：受控崩溃（验收用）。**游戏会真的崩掉。**
        # 双重闸门在 C# 侧（Dispatch 里判 CrashDump.CrashTestAllowed）——
        # ⚠️ 环境变量是**游戏进程**的（由启动脚本设），不是本 MCP 进程的，
        #    所以这里**不重复判**，只如实转发 C# 的拒绝理由。
        return send_command("crash_test", {})

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

    if name == "bl_source_map":
        # 宿主侧工具：栈帧 → 源码 文件:行号（+ 源码片段）。**不碰游戏、不需要游戏在跑**。
        import bl_source_map as _sm
        records = None
        if args.get("fromExceptions"):
            import bl_common as _bc
            records = []
            p = os.path.join(args.get("logDir") or _bc.default_log_dir(), "exceptions.jsonl")
            if os.path.isfile(p):
                with io.open(p, "r", encoding="utf-8-sig", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            o = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(o, dict):
                            records.append(o)
        return _sm.analyze(stack_text=args.get("stack"), records=records,
                           resolve=args.get("resolve"))

    if name == "bl_save_diag":
        # 宿主侧工具：存档诊断（**只读** —— 不写/不改/不备份存档）。
        # **不碰游戏、不需要游戏在跑**（LauncherData.xml 是纯文件；存档清单取自历史响应）。
        import bl_save_diag as _sd
        return _sd.build_report(
            log_dir=args.get("logDir"),
            launcher_path=args.get("launcher"),
            limit=(int(args["limit"]) if args.get("limit") else None),
        )

    if name == "bl_report":
        # 宿主侧工具：把三源合成自包含报告（HTML/Markdown）。**不碰游戏**。
        import bl_report as _rp
        data = _rp.collect(log_dir=args.get("logDir"))
        fmt = args.get("format") or "html"
        text = _rp.build_html(data) if fmt == "html" else _rp.build_markdown(data)
        if args.get("stdout"):
            return {"ok": True, "format": fmt, "text": text}
        path = _rp.write_report(text, out_dir=args.get("out"),
                                log_dir=args.get("logDir"), fmt=fmt)
        return {"ok": True, "format": fmt, "path": path,
                "bytes": len(text.encode("utf-8")),
                "guardRows": len(data["guard"]), "exceptionRows": len(data["exc"]),
                "note": "报告只读汇总，未修改任何源文件"}

    if name == "bl_crashguard":
        # 宿主侧工具：读崩溃守卫账本（谁被吞了/谁没敢吞）+ 给修复建议。
        # **不碰游戏、不需要游戏在跑**（与 bl_crash / bl_exception_detail 同类）。
        import bl_crashguard as _cg
        return _cg.build_report(
            log_dir=args.get("logDir"),
            path=args.get("path"),
            limit=int(args.get("limit") or 20),
        )

    if name == "bl_lexicon":
        # 宿主侧工具：崩溃词典（人话 + 修复建议）。**词条数据不随本仓库分发**
        # （上游无许可）⇒ 缺数据时如实报 installed=false，不返回空结果冒充"没有匹配"。
        # **不碰游戏、不需要游戏在跑**（与 bl_crash / bl_exception_detail 同类）。
        import bl_lexicon as _lx
        if args.get("statusOnly"):
            _lex, _st = _lx.load_lexicon(path=args.get("dir"))
            return {"ok": _st["installed"], "status": _st}
        _lex, _st = _lx.load_lexicon(path=args.get("dir"))
        if _lex is None:
            return {"ok": False, "reason": _st.get("reason"), "status": _st}
        res = _lx.describe(_lex, exc_type=args.get("type"), text=args.get("text"),
                           lang=args.get("lang") or "zh",
                           limit=int(args.get("limit") or 10))
        res["status"] = _st
        return res

    if name == "bl_concurrency_guide":
        # 宿主侧工具：从源码闭包推导「工具→通道方法」，从实测账本取耗时分位。
        # **不碰游戏、不需要游戏在跑**（与 bl_build_check / bl_json_health 同类）。
        import bl_concurrency_guide as _cg
        return _cg.build_report(log_dir=args.get("logDir"), tool=args.get("tool"))

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
        # ── B4（2026-10-07，隔壁项目测试表发现）：把**边界**写进返回 ──────────
        #
        # `bl_patches` 是只读内省，回答的是「**谁补了哪个方法**」= **注册在册**，
        # 它**看不到"补丁有没有生效"**。
        #
        # ★ 这个边界本身是**正确**的（不是 bug）—— 而且它是个**利器**：
        #   隔壁项目实测（C6 的 ③）补丁因 `Prepare()` 返回 false **静默不装**，
        #   正是 `bl_patches` 报 `matchedMethods: 0` 把它抓了出来。
        #
        # ⇒ 但调用方容易误读成"在册 = 生效"（或反之"没在册 = 没跑"），所以补一句口径。
        #   ⚠️ 注意 `Prepare()` 返回 false 的补丁**不会出现在这里** ——
        #     所以"这里没有"**不能**推断"没装过"。
        result = resp.get("result")
        return {"ok": True, "result": result,
                "scope": ("**注册在册 ≠ 生效**。本工具只读 Harmony 的公开内省 API，"
                          "回答「谁补了哪个方法」；判「是否真的生效」需要**行为证据**"
                          "（例如值/伤害真的变了）。"
                          "⚠️ `Prepare()` 返回 `false` 的补丁**不会出现在这里** ⇒ "
                          "「这里没有」**不能**推断「没装过」；"
                          "但反过来，`matchedMethods: 0` 是**静默失效的强信号**"
                          "（隔壁项目正是靠它抓到 C6-③ 的静默不装的）。"),
                "response": resp}

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

    if name == "bl_scan_bad_data":
        # 坏数据扫描（只读）。**只报不改** —— 清理另行实现且先备份。
        params = {}
        if args.get("only"):
            params["only"] = str(args["only"])
        if args.get("limit"):
            params["limit"] = int(args["limit"])
        resp, err = send_command("scan_bad_data", params, timeout=60)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "scan_bad_data 失败",
                    "code": e.get("code"), "response": resp}
        result = resp.get("result")
        # 把口径再钉一遍（调用方常只看 result，不看 tool description）
        return {"ok": True, "result": result,
                "scope": ("**只读扫描，不改任何数据。** severity=broken 是**明确错**"
                          "（结构上不可能正常）；severity=suspect 是**可疑**，"
                          "可能只是游戏正常的过渡状态 ⇒ **不可作为自动清理依据**。"
                          "⚠️ `skipped` 非空表示**某类没扫成** —— 别把「0 条」读成「很干净」。"
                          "清理必须另行实现，且**先备份 + 写新档不覆盖原档**。"),
                "response": resp}

    if name == "bl_get_hero":
        # B7（2026-10-07，隔壁项目需求）：读英雄运行时血量（只读，需战役上下文）。
        params = {}
        if args.get("heroId"):
            params["heroId"] = str(args["heroId"])
        if args.get("name"):
            params["name"] = str(args["name"])
        if args.get("all"):
            params["all"] = "true"
        if args.get("limit"):
            params["limit"] = int(args["limit"])
        resp, err = send_command("get_hero", params, timeout=15)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "get_hero 失败",
                    "code": e.get("code"), "response": resp}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_get_perk":
        # R1（2026-10-08，隔壁项目需求）：读 Perk 运行时生效值（只读，需战役上下文）。
        params = {}
        if args.get("perk"):
            params["perk"] = str(args["perk"])
        if args.get("all"):
            params["all"] = "true"
        resp, err = send_command("get_perk", params, timeout=15)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "get_perk 失败",
                    "code": e.get("code"), "response": resp}
        result = resp.get("result")
        # ★ 把"判据口径"再钉一遍（调用方常只看 result，不看 tool description）——
        #   这条**必须**跟着返回走，否则 V2 的验收很容易写成"两次必须不同"而误判。
        return {"ok": True, "result": result,
                "scope": ("**只读运行时 Perk 值。** ★ `primaryBonus` 与 `secondaryBonus` "
                          "**必须按各自预期**核对：原版基线 6 个 Perk 的 Primary 全 0.002、"
                          "Secondary 全 0.005，而目标值对 Primary/Secondary 是同一个数 ⇒ "
                          "`_bowDeadshot`/`_crossbowMightyPull`/`_throwingUnstoppableForce` 的 "
                          "**Primary 本来就不该变**。⚠️ 别写成『开关前后两次读数必须不同』—— "
                          "那会把正确判成工具无效。"
                          "⚠️ `xxxReadable=false` ⇒ 该值**没读到**，`null` **不是 0**。"
                          "⚠️ 本工具**不返回调用方目标值**（BlBridge 不引入 mod 语义）。"),
                "response": resp}

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

    # ── 战役观察者（v0.8.50，observer 层）──────────────────────────────
    #
    # ★ 这四个与上面所有 bl_campaign_* 的**本质区别**：上面是轮询快照，
    #   这四个读的是 C# 侧**事件订阅**攒下来的流。
    # ⚠️ 老 DLL（< v0.8.50）会回 unknown_method —— 此时如实转述该错误码，
    #   并明确提示"需要新 DLL"，**不要**伪装成"没有事件"（那是最坏的一种误导）。
    if name == "bl_observer_status":
        resp, err = send_command("observer_status", {}, timeout=20)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "observer_status 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_observer_events":
        params = {}
        if args.get("type"):
            params["type"] = str(args["type"])
        if args.get("limit") is not None:
            params["limit"] = int(args["limit"])
        if args.get("sinceSeq") is not None:
            params["sinceSeq"] = int(args["sinceSeq"])
        resp, err = send_command("observer_events", params, timeout=20)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "observer_events 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_observer_config":
        params = {}
        # ★ 只透传**显式给了**的字段（`is not None` 判据）：
        #   若把缺省 False 也发过去，会把 verboseAi/enabled 意外关掉 ——
        #   "只改我要改的"是配置类工具的硬纪律。
        for k in ("enabled", "verboseAi", "maxEvents"):
            if args.get(k) is not None:
                params[k] = args[k]
        if not params:
            return {"ok": False,
                    "error": "至少给一个字段：enabled / verboseAi / maxEvents"}
        resp, err = send_command("observer_config", params, timeout=20)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "observer_config 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_observer_clear":
        resp, err = send_command("observer_clear", {}, timeout=20)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "observer_clear 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    # ── 战役刺激器（v0.8.50）：**会改存档**，与上面的只读 observer 相反 ──
    #
    # ⚠️ 这三个的返回体里 `result.ok` 是**业务结论**（例如 already_at_war 时
    #   result.ok=false 但**信封 ok=true**）—— 这是刻意区分的（参见 Protocol.ReadResponseOutcome
    #   对 `resultOk` 的说明）：请求被正常处理 ≠ 操作达成目的。
    if name == "bl_war_status":
        resp, err = send_command("war_status", {}, timeout=20)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "war_status 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_declare_war":
        params = {}
        if args.get("faction1") is not None:
            params["faction1"] = str(args["faction1"])
        if args.get("faction2") is not None:
            params["faction2"] = str(args["faction2"])
        if args.get("detail") is not None:
            params["detail"] = str(args["detail"])
        if not params.get("faction1"):
            return {"ok": False, "error": "faction1 必填（StringId 如 empire_w，或名字）"}
        resp, err = send_command("campaign_declare_war", params, timeout=30)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "declare_war 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_make_peace":
        params = {}
        if args.get("faction1") is not None:
            params["faction1"] = str(args["faction1"])
        if args.get("faction2") is not None:
            params["faction2"] = str(args["faction2"])
        if not params.get("faction1"):
            return {"ok": False, "error": "faction1 必填（StringId 如 empire_w，或名字）"}
        resp, err = send_command("campaign_make_peace", params, timeout=30)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "make_peace 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_campaign_time_speed":
        params = {}
        if args.get("speed") is not None:
            params["speed"] = str(int(args["speed"]))
        if args.get("mode") is not None:
            params["mode"] = str(args["mode"])
        if args.get("multiplier") is not None:
            # ★ 必须发**裸数字**，不能发字符串。
            #   踩过（v0.8.52 实测）：写成 `repr(float(x))` 会得到 '15.0' 这个**字符串**，
            #   序列化后是 `"multiplier":"15.0"`（带引号），而 C# 侧 `Jmini.Num` 只扫
            #   **裸数字** ⇒ 读成 NaN ⇒ 报 "multiplier 不是数字：15.0"（消息本身看着
            #   像"值是 15.0 却说不是数字"，极具误导性）。这里改成 float 类型。
            params["multiplier"] = float(args["multiplier"])
        if not params:
            return {"ok": False,
                    "error": ("要给 speed（0..3，推荐 2/3）或 mode（如 UnstoppablePlay），"
                              "或 multiplier（正数，会自动配 UnstoppableFastForward）")}
        resp, err = send_command("campaign_time_speed", params, timeout=20)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "campaign_time_speed 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    # ── 存档（v0.8.53）：只存不退 + 状态轮询 ────────────────────────────
    if name == "bl_save_game":
        params = {}
        if args.get("mode") is not None:
            params["mode"] = str(args["mode"])
        if args.get("name") is not None:
            params["name"] = str(args["name"])
        # ⚠️ 超时给足：发起本身很快，但引擎可能在同帧忙（如正在存档）。
        resp, err = send_command("save_game", params, timeout=30)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "save_game 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_save_status":
        resp, err = send_command("save_status", {}, timeout=20)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "save_status 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_return_to_menu":
        params = {}
        if args.get("saveFirst") is not None:
            params["saveFirst"] = "true" if args["saveFirst"] else "false"
        if args.get("name") is not None:
            params["name"] = str(args["name"])
        resp, err = send_command("return_to_menu", params, timeout=30)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "return_to_menu 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    # ── 遭遇/对话（v0.8.57）──────────────────────────────────────────
    if name == "bl_conversation":
        resp, err = send_command("conversation", {}, timeout=20)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "conversation 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_conversation_choose":
        if args.get("index") is None:
            return {"ok": False,
                    "error": ("必须显式给 index（先用 bl_conversation 看选项）—— "
                              "本工具刻意不提供『自动挑一个』，因为选项后果不可逆")}
        # ⚠️ 必须发**裸数字**，不能发字符串 —— 与 `multiplier` 同一类坑（v0.8.52 踩过）：
        #   `Jmini.Num` 只扫裸数字；发成 `"index":"0"` 会读成 NaN ⇒ 报
        #   "index 必须 >= 0"（消息看着像"我明明给了 0"，极具误导性）。
        params = {"index": int(args["index"])}
        resp, err = send_command("conversation_choose", params, timeout=30)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "conversation_choose 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
        return {"ok": True, "result": resp.get("result"), "response": resp}

    if name == "bl_conversation_continue":
        resp, err = send_command("conversation_continue", {}, timeout=20)
        if err:
            return {"ok": False, "error": err, "sessionDiagnosis": run_state_diagnosis()}
        if not resp.get("ok"):
            e = resp.get("error") or {}
            return {"ok": False, "error": e.get("message") or "conversation_continue 失败",
                    "code": e.get("code"), "response": resp,
                    **(_observer_dll_hint(e.get("code")))}
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
