#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BlBridge 各工具共享的 I/O 与格式化小函数（不含任何指标逻辑）。

存在的理由：``load_events`` / 「列出 battles/*.jsonl」/ 数字格式化曾在
bl_analyze.py、bl_metrics.py、bl_death_compare.py 里各写一份
（code-review 2026-09-24 指出的 Duplicated Code），收拢到这一处。

把本文件放同目录后 ``import bl_common`` 即可 —— Python 会把脚本所在目录
加入 sys.path，所以直接 ``python tools/xxx.py`` 也能导入。
"""
import io
import json
import os
import re
import sys
import tempfile


# ══════════════════════════════════════════════════════════════════════════
# 路径边界锁（PBS, Path Boundary Seal）—— 2026-10-13 安全审计后新增
# ══════════════════════════════════════════════════════════════════════════
#
# ## 为什么放在 bl_common
#
# 写路径有**两条独立入口**：`bl_mcp.apply_config`（Warbandlord / RBM）与
# `bl_rts.apply`（RTSCamera）。两者都要边界锁。
# 放 `bl_mcp` 会让 `bl_rts` 反向依赖它（且 bl_mcp 已 import bl_rts ⇒ 循环）；
# 而 `bl_common` 只依赖标准库、两边都已依赖 ⇒ 放这里，**单一实现**。
#
# ## 为什么需要（实测，非推测）
#
# 审计实测 `bl_apply_config(path=...)` 能改**任意绝对路径**的文件：
#     C:\Windows\Temp\*.xml   → 被改（SHA256 变化）
#     C:\Users\LCGX\*.xml     → 被改
#     notes.txt（非 .xml）     → 被改
# 且 `expandvars` 会把 `%WINDIR%\win.ini` 展开成真路径。
#
# ## 为什么这是"设计权衡的边界"而非"忘了校验"
#
# `bl_mcp` 的注释写明：A4 的**有意设计**是"显式给 path 就跳过 Warbandlord 闸门"。
# 问题不在跳闸门，而在**跳完之后没有补上"路径边界"这一层** ——
# 正好命中本项目已确立的教训（CONCURRENCY.md B8 段）：
#     **加参数/放宽闸门，等于打开一条以前锁着的路 —— 要检查那条路上有没有旧洞。**
#
# ## 设计纪律（与 docs/config-tool-safety.md 对齐）
#
# 1. ★ **默认放行 = 全部既有合法用法**（游戏目录 / 文档目录 / 临时目录 / 项目目录）
#    ⇒ 正常使用**一字不变**；只有"越界"这一**新情形**被拒。
# 2. ★ **拒绝方式结构化**（返回 `{"ok": False, "error": ...}`），
#    与本项目既有错误同一形状 —— 对齐"派发路径必须给出可读错误"的纪律。
# 3. ★ **可显式放开**：`BLBRIDGE_ALLOW_WRITE_ROOTS`（os.pathsep 分隔）追加；
#    `BLBRIDGE_ALLOW_ANY_PATH=1` 完全关闭（需显式，便于审计"谁关的")。
# 4. ★ **错误信息回显全部允许根** —— 让调用方知道"该往哪儿写"，而不是只被拒。

ALLOWED_WRITE_EXTS = (".xml", ".json", ".jsonl", ".txt", ".cfg", ".ini", ".log")


def allowed_roots(game_dir=None, project_dir=None):
    """允许写入/读取的根目录（realpath 后）。

    ★ 默认集合是**实测得出**的，覆盖本项目全部既有合法目标：
         · 游戏目录        —— Warbandlord config.xml
         · 文档目录        —— RBM config.xml / RTSCameraConfig.xml / BlBridge 日志
         · 系统临时目录    —— 自测的 tempfile.mkdtemp()
         · 项目目录        —— blbridge.json
    """
    roots = []
    cands = [
        game_dir or os.environ.get("BANNERLORD_DIR"),
    ]
    # 文档目录：与 default_log_dir 同口径（Personal/Mount and Blade II Bannerlord）
    cands.append(os.path.join(os.path.expanduser("~"), "Documents"))
    cands.append(tempfile.gettempdir())
    if project_dir:
        cands.append(project_dir)
    for v in cands:
        if not v:
            continue
        try:
            roots.append(os.path.realpath(os.path.abspath(v)))
        except Exception:  # noqa: BLE001
            pass
    raw = (os.environ.get("BLBRIDGE_ALLOW_WRITE_ROOTS") or "").strip()
    if raw:
        for x in raw.split(os.pathsep):
            x = x.strip()
            if not x:
                continue
            try:
                roots.append(os.path.realpath(
                    os.path.abspath(os.path.expanduser(os.path.expandvars(x)))))
            except Exception:  # noqa: BLE001
                pass
    seen, out = set(), []
    for r in roots:
        k = os.path.normcase(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def within(root, path):
    """path 是否位于 root 之内（两者 realpath；符号链接也被挡住）。

    ★ 用 os.path.commonpath 而非字符串 startswith ——
      否则 `C:\\a` 会误判 `C:\\ab` 为"在其内"（经典边界 bug）。
    """
    try:
        r, p = os.path.realpath(root), os.path.realpath(path)
        return os.path.commonpath([r, p]) == r
    except ValueError:
        return False          # 跨盘符 ⇒ 必然不在其内


def path_allowed(path, verb="写入", game_dir=None, project_dir=None):
    """路径边界锁。返回 (ok, payload)；ok=False 时 payload 是**结构化错误**。

    ★ 这是**唯一的边界判定入口** —— 所有写/读路径都过它，
      这样"哪条路开了边界"在代码里一眼可查（而非散落的 startswith）。
    """
    if os.environ.get("BLBRIDGE_ALLOW_ANY_PATH") == "1":
        return True, {"path": path, "boundaryLock": "disabled_by_env"}
    roots = allowed_roots(game_dir=game_dir, project_dir=project_dir)
    for r in roots:
        if within(r, path):
            return True, {"path": path, "boundaryRoot": r,
                          "boundaryLock": "within_allowed_roots"}
    return False, {
        "ok": False,
        "error": "path_out_of_allowed_roots",
        "path": path,
        "verb": verb,
        "allowedRoots": roots,
        "message": (
            "%s被路径边界锁拒绝：%s\n"
            "允许的根：%s\n"
            "如需放开，设 BLBRIDGE_ALLOW_WRITE_ROOTS（%s 分隔）追加目录；"
            "或 BLBRIDGE_ALLOW_ANY_PATH=1 完全关闭边界锁（不推荐）。"
            % (verb, path, "; ".join(roots), os.pathsep)),
    }


def ext_allowed(path):
    """扩展名白名单（写入用）。返回 (ok, payload)。

    ★ 单独一条的理由：审计实测"`.txt` 被改"能成功，
      而"改配置文件"的语义落到 `.txt` 上是**明确越界**。
    """
    ext = os.path.splitext(str(path))[1].lower()
    if not ext or ext in ALLOWED_WRITE_EXTS:
        return True, {"ext": ext or "(无)"}
    return False, {
        "ok": False,
        "error": "ext_not_allowed",
        "path": path,
        "ext": ext,
        "allowedExts": list(ALLOWED_WRITE_EXTS),
        "message": ("拒绝写入该扩展名：%s（白名单：%s）"
                    % (ext, ", ".join(ALLOWED_WRITE_EXTS))),
    }


# ══════════════════════════════════════════════════════════════════════════
# L3：合成输入的输入闸门（ITG, Input Triage Gate）—— 2026-10-13 安全审计后新增
# ══════════════════════════════════════════════════════════════════════════
#
# ## 为什么需要
#
# `bl_desktop_key` 把 `keys` / `text` 原样交给外部 `gridhand` 做**合成输入**，
# 而 gridhand 会把它发到**当前活动窗口**。参数以列表传（**无 shell 注入**），
# 但那不等于无风险 —— 它是**完整键盘权限**：
#   · `keys="win+r"` ⇒ 打开运行框 ⇒ 可接着输入任意命令
#   · `keys="ctrl+shift+esc"` ⇒ 任务管理器
#   · `keys="alt+f4"` ⇒ 关掉**任意**前台窗口（不一定是游戏）
#   · `text` 无长度上限 ⇒ 可灌入任意长文本
#
# ## 设计纪律：**白名单为主 + 高危组合否决**（双层，缺一不可）
#
# ### 第一层：白名单（拦"没记录过的键"）
#
# ★ 为什么不只用黑名单：攻击面是"全部组合键"，枚举拦不完
#   （`win+x`、`win+l`、`ctrl+alt+del`… 列不尽）。
#   本项目一贯纪律是"**宁可明确拒绝并给出可行路径，也不要假装知道**"
#   （见 ENUM_HINTS 的注释）⇒ 用白名单，只放行**在代码/文档里真实出现过**的用途。
#
# ### 第二层：高危组合否决（拦"白名单组件拼出来的危险组合"）
#
# ★★ 为什么必须有第二层 —— 这是**实测发现的**，不是推测：
#   第一版只有白名单，而 `ctrl+shift+esc` 的每个组件都合法
#   （`ctrl`/`shift` 是允许的修饰键、`esc` 是允许的键）⇒ **放行了**。
#   可它的语义是**打开任务管理器** —— 与"点掉对话框"毫无关系。
#   ⇒ 教训：**白名单只保证"组件是被认识的"，不保证"组合是要的那个意思"。**
#   同理 `alt+f4`（关前台窗口）、`alt+tab`（换焦点到别的应用再注入）
#   也能用合法组件拼出来。
#
# ⇒ 两层叠加：**先挡不认识的，再挡认识但危险的**。

# 允许按下的单键（小写；与 gridhand 的按键名口径一致）
ALLOWED_KEYS = frozenset([
    "enter", "return", "space", "tab", "esc", "escape",
    "up", "down", "left", "right",
    "home", "end", "pageup", "pagedown",
    "backspace", "delete", "insert",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
    "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
])

# 允许的修饰键（**只有 ctrl / shift / alt** —— 刻意不含 win/meta/cmd）
ALLOWED_MODIFIERS = frozenset(["ctrl", "control", "shift", "alt"])

# 明确点名的高危键（拒绝时给出**可读原因**，而不是笼统"不允许"）
DANGEROUS_KEYS = {
    "win": "系统键（可打开运行框/开始菜单，等效于拿到任意命令入口）",
    "meta": "系统键（同 win）",
    "cmd": "系统键（同 win）",
    "super": "系统键（同 win）",
    "lwin": "系统键（左 Win）",
    "rwin": "系统键（右 Win）",
    "printscreen": "截屏键（可外带屏幕内容）",
    "prtsc": "截屏键",
}

# ── 第二层：高危组合（键 = (修饰键 frozenset, 主键)，值 = 为什么危险）──
#   键里只写规范化后的 "ctrl"（不写 "control"）
_DENY_COMBOS = {
    (frozenset(["ctrl", "shift"]), "esc"): "打开任务管理器",
    (frozenset(["ctrl"]), "esc"):          "打开开始菜单",
    (frozenset(["alt"]), "f4"):            "关闭**当前前台窗口**（不一定是游戏；想关对话框请用 esc）",
    (frozenset(["alt"]), "tab"):           "切换前台窗口（可把焦点移到别的应用再注入）",
    (frozenset(["alt"]), "esc"):           "窗口/菜单循环切换",
    (frozenset(["ctrl", "alt"]), "del"):   "安全屏",
    (frozenset(["ctrl", "shift"]), "enter"): "以管理员身份运行",
    (frozenset(["ctrl", "alt"]), "delete"): "安全屏",
}

MAX_KEY_SPEC_LEN = 64          # 单个组合键串的总长上限
MAX_TEXT_LEN = 512             # 合成文本长度上限（够输入中文按钮/短串，挡灌入）


def _norm_mods(parts):
    """把修饰键规范化：control -> ctrl，去重，返回 frozenset。"""
    out = set()
    for p in parts:
        out.add("ctrl" if p == "control" else p)
    return frozenset(out)


def key_allowed(spec):
    """校验 `keys` 组合键串。返回 (ok, payload)。"""
    if not isinstance(spec, str) or not spec.strip():
        return False, {"ok": False, "error": "keys_empty",
                       "message": "keys 不能为空"}
    s = spec.strip()
    if len(s) > MAX_KEY_SPEC_LEN:
        return False, {"ok": False, "error": "keys_too_long", "len": len(s),
                       "maxLen": MAX_KEY_SPEC_LEN,
                       "message": "keys 过长（%d > %d）" % (len(s), MAX_KEY_SPEC_LEN)}

    # 允许 "ctrl+a" 这种一式多键（+ 分隔）；逐段校验
    parts = [p.strip().lower() for p in re.split(r"\s*\+\s*", s) if p.strip()]
    if not parts:
        return False, {"ok": False, "error": "keys_empty", "message": "keys 解析后为空"}

    # ── 第一层 ①：修饰键必须在白名单 ──
    bad_mods = []
    for p in parts[:-1]:                      # 除最后一段外都应是修饰键
        if p not in ALLOWED_MODIFIERS:
            bad_mods.append(p)
    if bad_mods:
        why = []
        for p in bad_mods:
            if p in DANGEROUS_KEYS:
                why.append("%s（%s）" % (p, DANGEROUS_KEYS[p]))
            else:
                why.append(p)
        return False, {
            "ok": False,
            "error": "modifier_not_allowed",
            "keys": s,
            "offending": bad_mods,
            "allowedModifiers": sorted(ALLOWED_MODIFIERS),
            "message": ("修饰键不在白名单：%s\n"
                        "只允许 ctrl / shift / alt（**刻意不含 win/meta** —— "
                        "它们等效于拿到任意命令入口）。\n"
                        "如确需放开，设 BLBRIDGE_ALLOW_ANY_KEY=1。"
                        % "; ".join(why)),
        }

    # ── 第一层 ②：主键必须在白名单 ──
    last = parts[-1]
    if last in DANGEROUS_KEYS:
        return False, {
            "ok": False,
            "error": "key_not_allowed",
            "keys": s,
            "offending": last,
            "message": ("该键被拒：%s（%s）\n如确需放开，设 BLBRIDGE_ALLOW_ANY_KEY=1。"
                        % (last, DANGEROUS_KEYS[last])),
        }
    if last not in ALLOWED_KEYS:
        return False, {
            "ok": False,
            "error": "key_not_allowed",
            "keys": s,
            "offending": last,
            "message": ("按键不在白名单：%r\n"
                        "白名单是**本项目记录的合法用途**那批（enter/tab/esc/方向键/字母数字等）。\n"
                        "如确需放开，设 BLBRIDGE_ALLOW_ANY_KEY=1。" % (last,)),
        }

    # ── ★ 第二层：高危组合否决（组件全合法、但语义危险）──
    mods = _norm_mods(parts[:-1])
    key_norm = "esc" if last == "escape" else ("enter" if last == "return" else last)
    why = _DENY_COMBOS.get((mods, key_norm))
    if why:
        return False, {
            "ok": False,
            "error": "dangerous_combo",
            "keys": s,
            "combo": "+".join(sorted(mods) + [key_norm]),
            "reason": why,
            "message": ("组合键被高危否决：%s ⇒ %s\n"
                        "（它的每个组件都在白名单里，但组合起来的语义与本工具的用途无关。）\n"
                        "如确需放开，设 BLBRIDGE_ALLOW_ANY_KEY=1。" % (s, why)),
        }

    return True, {"keys": s, "parts": parts}


def text_allowed(s):
    """校验 `text` 合成输入。返回 (ok, payload)。

    ★ 拦三类：
      ① 超长（`MAX_TEXT_LEN`）—— 挡"灌入任意长文本"；
      ② 控制字符（`\\x00-\\x1f`，含 `\\r`/`\\n`/`\\t`）—— 换行在对话框上等效于"按下回车"，
         会是**隐式按键**（调用方以为是"输文本"，结果触发了按钮）；
      ③ 非字符串类型。
    """
    if not isinstance(s, str):
        return False, {"ok": False, "error": "text_not_string",
                       "message": "text 必须是字符串，收到 %s" % type(s).__name__}
    if len(s) > MAX_TEXT_LEN:
        return False, {"ok": False, "error": "text_too_long", "len": len(s),
                       "maxLen": MAX_TEXT_LEN,
                       "message": ("text 过长（%d > %d）—— 合成输入不该用来灌长文本。"
                                   "如确需放开，设 BLBRIDGE_ALLOW_ANY_KEY=1。"
                                   % (len(s), MAX_TEXT_LEN))}
    ctrl = [(i, c) for i, c in enumerate(s)
            if ord(c) < 0x20 or ord(c) == 0x7f]
    if ctrl:
        i, c = ctrl[0]
        return False, {
            "ok": False,
            "error": "text_has_control_char",
            "index": i,
            "codepoint": "U+%04X" % ord(c),
            "message": ("text 含控制字符 U+%04X（第 %d 个字符）—— "
                        "换行/回车在对话框上等效于**按下回车**，会隐式触发按钮。\n"
                        "想按键请用 keys 参数（显式、在白名单内）。" % (ord(c), i)),
        }
    return True, {"text": s, "len": len(s)}


def default_log_dir():
    """日志目录。**先看 `BLBRIDGE_LOG_DIR`**，再退到 我的文档。

    为什么加环境变量覆盖（v0.8.42）：`bl_mcp.py` 早就在读这个变量（`:69`），
    而 `bl_common` 不读 —— 于是"设了变量跑自测/MCP"与"跑 CLI 子命令"会看到**两个不同目录**。
    自测里本来就有几处 `os.environ["BLBRIDGE_LOG_DIR"] = ...`（如合成战斗那段），
    在 `bl_common` 不读它的时候，那些设置其实只对 MCP 生效、对分析器无效。
    统一到一处口径，与 `bl_mcp.py` 逐字一致。
    """
    return (os.environ.get("BLBRIDGE_LOG_DIR")
            or os.path.join(os.path.expanduser("~"), "Documents",
                            "Mount and Blade II Bannerlord", "BlBridge"))


def battles_dir(log_dir=None):
    return os.path.join(log_dir or default_log_dir(), "battles")


def list_battle_files(log_dir=None):
    """目录下的 *.jsonl（按文件名升序；目录不存在 ⇒ 空列表）。"""
    d = battles_dir(log_dir)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, n) for n in os.listdir(d) if n.lower().endswith(".jsonl"))


def list_battles(log_dir=None):
    """[{file, name, size, mtime}]，按 mtime 升序（与 bl_analyze 既有语义一致）。"""
    out = []
    for p in list_battle_files(log_dir):
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"file": p, "name": os.path.basename(p), "size": st.st_size, "mtime": st.st_mtime})
    out.sort(key=lambda x: x["mtime"])
    return out


def latest_battle(log_dir=None):
    """按 mtime 最新的一份（无则 None）。"""
    rows = list_battles(log_dir)
    return rows[-1]["file"] if rows else None


def load_events(path):
    """读 JSONL 事件流；坏行静默跳过（既有工具一致的口径）。"""
    ev = []
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev.append(json.loads(line))
            except ValueError:
                continue
    return ev


# ── v0.8.42：动作账本（commands/actions.jsonl，GameMaster 的 CommandLogger 同款）──

def commands_dir(log_dir=None):
    """控制通道目录（pending / done / actions.jsonl 都在这里）。"""
    return os.path.join(log_dir or default_log_dir(), "commands")


def actions_path(log_dir=None):
    """动作账本文件路径。"""
    return os.path.join(commands_dir(log_dir), "actions.jsonl")


def load_actions(path=None, limit=None, fail_only=False):
    """读动作账本 → ``(entries, stats)``。

    **与 ``load_events`` 的口径故意不同**：``load_events`` 遇到坏行**静默跳过**，
    而 v0.8.4 那个"meta 产出非法 JSON"的 bug 正是靠这种静默活下来的（分析器不报错、
    只是少了一段数据）。账本是**审计**用途，"少了一行"本身就是结论，所以这里
    **坏行计入 `stats["bad"]` 并把前几条原文带出来**，绝不静默。

    ``stats`` 键：``path`` / ``lines``（非空行总数）/ ``bad``（解析失败行数）/
    ``badSamples``（前 3 条坏行原文，截断 160 字符）/ ``runs``（出现过的 runToken 列表）。
    ``limit`` 取**过滤后的最后 N 条**（要的是"最近发生了什么"）。

    **两个 ``ok``（v0.8.46 起，务必分清）**：每行有两个布尔语义 ——

    * ``ok``：**信封**是否成功（"请求有没有被游戏端处理"）。**语义与 v0.8.42 逐字不变。**
    * ``resultOk``：``result`` **内部**的 ``ok``（"**这个操作成功了吗**"）。
      ``true``=操作成功 / ``false``=操作失败 / **``null``=未知**。

    ⚠️ **历史行没有 ``resultOk`` 键** ⇒ ``ev.get("resultOk")`` 返回 ``None``，
    与"值是 null"**同形**，两者都表示**未知**（老数据确实没记录过这件事）。
    ⇒ 想筛"操作失败"请用 ``resultOk is False``（**不要**写 ``not resultOk``，
    那会把"未知"也算成失败）。``fail_only`` 仍按**信封** ``ok`` 过滤（既有语义不变）；
    要按操作成败筛，用本函数返回的条目自行按 ``resultOk is False`` 过滤。
    """
    p = path or actions_path()
    entries = []
    bad_samples = []
    lines = 0
    bad = 0
    if not os.path.isfile(p):
        return [], {"path": p, "lines": 0, "bad": 0, "badSamples": [], "runs": [],
                    "missing": True}
    with io.open(p, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            lines += 1
            try:
                ev = json.loads(line)
            except ValueError:
                bad += 1
                if len(bad_samples) < 3:
                    bad_samples.append(line[:160])
                continue
            # ⚠️ 合法 JSON 但**不是对象**（如 `[1,2,3]`）同样要挡掉：
            #    否则后面的 `.get()` 会抛 AttributeError，把一个"读审计日志"的动作
            #    变成崩溃 —— 这条是自测 ⑮ 用合成的 `[1,2,3]` 行当场抓到的。
            if not isinstance(ev, dict):
                bad += 1
                if len(bad_samples) < 3:
                    bad_samples.append(line[:160])
                continue
            if fail_only and ev.get("ok") is not False:
                continue
            entries.append(ev)
    if limit is not None and limit >= 0:
        entries = entries[-limit:]
    runs = []
    for ev in entries:
        tok = ev.get("runToken")
        if tok and tok not in runs:
            runs.append(tok)
    return entries, {"path": p, "lines": lines, "bad": bad, "badSamples": bad_samples,
                     "runs": runs, "missing": False}


def fmt(v, nd=1, dash="-"):
    """数字 / None 的紧凑格式化。"""
    if v is None:
        return dash
    if isinstance(v, float):
        return ("%." + str(nd) + "f") % v
    return str(v)

def safe_streams():
    """把 stdout/stderr 固定为 **UTF-8 输出**（errors="replace" 只兜底极端字符）。

    为什么必须显式设：Windows 上 Python 的 stdout 默认用 **locale 编码**（简中是 cp936），
    于是脚本写出的是 GBK 字节。而本项目的输出消费端是**调用这些工具的 AI / 管道**
    （**本机实测**：系统 `chcp 936` / ANSI `gb2312`，而宿主 PowerShell 5.1 的
    `[Console]::OutputEncoding` 是 `utf-8`，Python 却默认按 locale(cp936) 写 ⇒ 写入/读取不一致；
    重定向、CI、别的 agent 捕获同理）—— **写入编码 ≠ 读取编码，中文就整片变成 U+FFFD**。
    所以这里统一按 UTF-8 写，与 `bl_mcp.py` 的入口口径一致（那里一开始就是 utf-8）。

    历史（别再走回去）：旧版只 `reconfigure(errors="replace")`、宣称"中文照常可读"——
    那只在 GBK 控制台成立，一离开就乱码（2026-09-25 实测：GBK 字节被按 UTF-8 解 ⇒ 满屏 U+FFFD）。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ── 靶子护甲覆盖（v0.8.0）：部位名 → C# 侧参数名 ──────────────────────
# 单一真相：bl_cmd.py（--dummy-armor）与 bl_batch.py（plan）共用这一份，
# 避免两处清单各自漂移（那正是"写对了名字却静默无效"的温床）。
DUMMY_ARMOR_PARTS = {
    "head": "dummyArmorHead",
    "torso": "dummyArmorTorso",
    "legs": "dummyArmorLegs",
    "arms": "dummyArmorArms",
}


def parse_dummy_armor(text):
    """把 ``head=45,torso=35`` 解析成 ``{dummyArmorHead: 45.0, ...}``；**非法即抛 ValueError**。

    绝不静默跳过：2026-09-24 有 9 场实验因为参数被静默丢弃而整批作废
    （`Jmini.Num` 只吃数字字符，字符串值被判成"读不到"，没有任何报错）。
    未知部位名 / 非数字 / 缺 `=` / 缺值 / 对象形式 —— 全部报错，把问题挡在发命令之前。

    空串与纯空白 ⇒ 空 dict（= 不覆盖任何部位，与 CLI 默认一致）。
    """
    if isinstance(text, dict):
        raise ValueError('dummyArmor 请用字符串形式（如 "head=45,torso=35"），不要用对象')
    if text is None:
        return {}
    if not isinstance(text, str):
        raise ValueError("dummyArmor 必须是字符串，收到 %s" % type(text).__name__)
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError("dummyArmor 片段缺少 '='：%r（应为 head=45,torso=35 形式）" % part)
        k, v = part.split("=", 1)
        k = k.strip().lower()
        if not k:
            raise ValueError("dummyArmor 片段缺少部位名：%r" % part)
        if k not in DUMMY_ARMOR_PARTS:
            raise ValueError("未知部位名 %r（可用：%s）"
                             % (k, ", ".join(sorted(DUMMY_ARMOR_PARTS))))
        v = v.strip()
        try:
            out[DUMMY_ARMOR_PARTS[k]] = float(v)
        except ValueError:
            raise ValueError("部位 %s 的值不是数字：%r" % (k, v))
    return out


# ── 多兵种 / 战术组（v0.8.8）：扁平字符串 DSL ─────────────────────────────
# 为什么是字符串而不是嵌套 JSON：C# 侧的自研解析器 `Jmini` 只读**裸值**——把字符串
# 传给它既不报错也不生效（静默用 fallback），嵌套结构更传不过去。这与
# `--dummy-armor head=45,torso=35` 是同一套思路（2026-09-24 的 9 场实验就是这么废掉的）。

SQUAD_FORMATIONS = ("Infantry", "Ranged", "Cavalry", "HorseArcher", "Skirmisher",
                    "HeavyInfantry", "LightCavalry", "HeavyCavalry", "General", "Bodyguard")
SQUAD_MOVEMENTS = ("charge", "advance", "fallback", "stop", "retreat")
# 已移除的 movement 及其替代（补位提示，GC3）：只放这一条，别顺手加别的。
REMOVED_MOVEMENTS = {"hold": "stop"}
_FORMATION_BY_LOWER = dict((n.lower(), n) for n in SQUAD_FORMATIONS)


def parse_squad_groups(text):
    """解析 ``troop:count[:formation[:movement]]``（多组用 ``|``）→ list[dict]。

    ``formation`` 大小写不敏感、回写为规范名（GC1）；两个可选字段缺省时是 None
    （默认值由调用方决定：C# 侧编队走引擎默认、movement 走 ``charge``）。
    **非法输入一律抛 ValueError，绝不静默丢弃** —— 理由同 parse_dummy_armor。
    """
    if text is None:
        return []
    if not isinstance(text, str):
        raise ValueError("组串必须是字符串，收到 %s" % type(text).__name__)
    if not text.strip():
        return []
    out = []
    for idx, part in enumerate(text.split("|"), 1):
        part = part.strip()
        if not part:
            raise ValueError("第 %d 组为空（应为 troop:count[:formation[:movement]]，多组用 | 分隔）" % idx)
        fields = [f.strip() for f in part.split(":")]
        if len(fields) not in (2, 3, 4):
            raise ValueError("第 %d 组 %r 的字段数 = %d，应为 troop:count[:formation[:movement]]"
                             % (idx, part, len(fields)))
        troop, cnt = fields[0], fields[1]
        if not troop:
            raise ValueError("第 %d 组 %r 缺少兵种 id" % (idx, part))
        try:
            count = int(cnt)
        except ValueError:
            raise ValueError("第 %d 组 %r 的 count 不是整数：%r" % (idx, part, cnt))
        if count < 1:
            raise ValueError("第 %d 组 %r 的 count 必须 ≥1，收到 %d" % (idx, part, count))
        formation = None
        if len(fields) >= 3 and fields[2]:
            formation = _FORMATION_BY_LOWER.get(fields[2].lower())
            if formation is None:
                raise ValueError("第 %d 组 %r 的 formation 未知：%r（可用：%s）"
                                 % (idx, part, fields[2], ", ".join(SQUAD_FORMATIONS)))
        movement = None
        if len(fields) == 4 and fields[3]:
            movement = fields[3].lower()
            if movement not in SQUAD_MOVEMENTS:
                hint = ""
                if movement in REMOVED_MOVEMENTS:
                    repl = REMOVED_MOVEMENTS[movement]
                    hint = "—— %s 已移除（引擎层它本就等同 %s），请改用 %s" % (movement, repl, repl)
                raise ValueError("第 %d 组 %r 的 movement 未知：%r（可用：%s）%s"
                                 % (idx, part, fields[3], ", ".join(SQUAD_MOVEMENTS), hint))
        out.append({"troop": troop, "count": count, "formation": formation, "movement": movement})
    return out


# ── v0.8.41：环境旋钮与战术档位（与 src/BattleEnv.cs / src/TacticsCombatant.cs 一一对应）──
#
# 为什么在这里也做一遍校验（GC3 同款理由）：游戏端 `Jmini` 是**扁平文本读**器，
# 名字错了/类型错了它读不到就落 fallback ⇒ "请求 accepted、参数没生效"。
# 这类静默在 2026-09-24 废掉过一整批 9 场实验，所以**本地先拒**。

# `TaleWorlds.Core.TerrainType` 全表（反编译取证）。全小写，与 C# 侧表逐项一致。
TERRAINS = ("plain", "desert", "snow", "forest", "steppe", "fording", "mountain",
            "lake", "water", "river", "canyon", "ruralarea", "swamp", "dune",
            "bridge", "coastalsea", "opensea", "beach", "cliff",
            "nonnavigableriver", "landrestriction", "searestriction", "underbridge")

# 战术档位合法域：-1（不覆盖）或 0..100。引擎自己的分档线是 20 / 50。
TACTIC_LEVEL_MIN = -1
TACTIC_LEVEL_MAX = 100


def parse_terrain(text):
    """校验地形名（大小写不敏感）→ 规范小写名；空串/None → ""（= 不覆盖）。

    非法名一律抛 ValueError（附候选清单），绝不静默兜底成 plain。
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ValueError("terrain 必须是字符串，收到 %s" % type(text).__name__)
    key = text.strip().lower()
    if not key:
        return ""
    if key not in TERRAINS:
        raise ValueError("terrain 未知：%r（可用：%s）" % (text, " | ".join(TERRAINS)))
    return key


def check_tactic_level(value, label):
    """校验战术档位。返回 int；非法抛 ValueError。

    接受 ``-1``（不覆盖）或 ``0..100``。``None`` → -1。
    """
    if value is None:
        return -1
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError("%s 必须是整数，收到 %r" % (label, value))
    if v != -1 and not (TACTIC_LEVEL_MIN <= v <= TACTIC_LEVEL_MAX):
        raise ValueError("%s 只接受 -1（不覆盖）或 0..%d，收到 %d"
                         % (label, TACTIC_LEVEL_MAX, v))
    return v


def end_metrics(events, end=None):
    """结束类指标（v0.8.10 F6）：时长与双方入场人数，缺 `end` 事件时按多轮口径回退。

    背景：多轮连续实验**每轮一个文件**，而 `end` 事件只在 mission 真正结束时写 ⇒ 只有最后一轮
    有 `end`。只认 `end` 的分析器会把中间轮的时长/人数显示成 `-`/None（真机回归实测：
    `bl_analyze` 报"时长 -""攻方 None→None"、`bl_compare` 报"A:D = None:None"）。

    回退口径（**只在前者为空时启用**，不覆盖 `end` 里的真值）：
      - duration   ：最后一个 `round_*` 事件的 `time`（即该轮末）；
      - a/d 初始数：`unit` 事件按 `side` 统计（每轮一个文件 ⇒ 就是该轮入场数）。
    """
    if end is None:
        end = next((e for e in events if e.get("t") == "end"), {})
    duration = end.get("time")
    a_init = end.get("aInitial")
    d_init = end.get("dInitial")

    if duration is None:
        rounds = [e for e in events if str(e.get("t", "")).startswith("round_")]
        if rounds:
            duration = rounds[-1].get("time")

    if a_init is None or d_init is None:
        units = [e for e in events if e.get("t") == "unit"]
        a_n = sum(1 for e in units if str(e.get("side")) == "Attacker")
        d_n = sum(1 for e in units if str(e.get("side")) == "Defender")
        if a_n or d_n:
            if a_init is None:
                a_init = a_n
            if d_init is None:
                d_init = d_n
    return duration, a_init, d_init
