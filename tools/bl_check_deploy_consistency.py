#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""部署产物 ↔ 源码 一致性机器判据（★ 含反向对照）。

## 为什么要有这个脚本（立项现场，2026-10-08）

本轮出现过一个**假阻塞**："部署的 `BlBridge.dll` 不含 `get_hero` ⇒ 必须先重新部署"。
它由**两个独立的错因**叠加而成，两个都是本项目反复防的那类：

**错因①：工具用法造成"唯一一份"的假象。**
  用 `Get-ChildItem -Filter 'BlBridge.dll'` 列目录时，该 filter **匹配不到**
  `BlBridge.dll.bak_*` ⇒ 备份文件被漏掉，于是一份 `.bak` 被当成了"唯一的那一份"。
  实测盘上是 **1 现役 + 3 备份**。

**错因②：只查一种编码的假阴性（`ab13ec9` 那条教训的复发）。**
  .NET 程序集有**两个**字符串堆：

    | 堆 | 编码 | 存什么 |
    |---|---|---|
    | `#Strings` | **UTF-8** | **类型名 / 字段名 / 方法名** |
    | `#US` | **UTF-16LE** | **字符串字面量**（`ldstr`） |

  只搜 UTF-8 ⇒ 对**字面量**必然假阴性；只搜 UTF-16 ⇒ 对**类型名**必然假阴性。
  `get_hero` 是**字面量**（在 `#US`），所以"ASCII 搜不到"**证明不了**它不在 DLL 里。
  实测：所谓"不含 `get_hero`"的那份（`706A591D`）其实 `get_hero=utf16` **在**，
  它真正缺的是 **`scan_bad_data`**。

⇒ 本脚本把那一轮**手验**过的东西做成**可重复跑的机器判据**，回答三件事：

  A. 部署 DLL 的 sha256 是否等于**与它同目录**的 `build_manifest.json` 的 `dllSha256`；
  B. 清单 `sources` 的每个哈希是否等于磁盘 `src/` 的对应文件（逐文件列出差异）；
  C. 部署 DLL 是否含**关键符号**（★ 两种编码取并集）。

另外如实报告**部署副本 vs 仓库 `tools/`** 的漂移，并点明"现场层走哪个"。

## ★ 为什么必须成对比较（DLL ↔ **同目录**清单）

不要拿仓库 `out/BlBridge.manifest.json` 当基准。实测（2026-10-08）：

    Modules\\BlBridge\\bin\\...\\BlBridge.dll  279552 B @00:45:23  sha16=4EC10B4F
    <仓库>\\out\\BlBridge.dll                  279552 B @00:51:23  sha16=6470FC1E
   两份清盘的 sources 47 个哈希**完全相同**，只有 builtUtc 不同（16:45:23Z vs 16:51:23Z）

⇒ **源码未变，同日重编也能得到不同字节**（编译器/时间戳等非确定性）。
所以 `dllSha256` 只对"**随这个 DLL 一起部署出去的那份清单**"有意义；
拿仓库 `out/` 去比会得到**假的 stale 结论**。

## ★★ 反向对照（本脚本的核心纪律）

只证明"现在 missing=0"**毫无价值** —— 一个恒返回 `[]` 的检查也永远通过。
所以 `--selftest` 会注入故障并逐条断言**必须被抓到**，同时断言**未注入时 0 报错**：

    1. 注入一个**不存在**的符号名     ⇒ 必须出现在 missing（判据真的在判定）
    2. 篡改清单的 dllSha256           ⇒ A 必须红
    3. 篡改清单里某个源文件的哈希      ⇒ B 必须红（且点名是哪个文件）
    4. 从清单里删掉一个源文件条目      ⇒ B 必须红
    5. ★ **双编码必要性对照**：对一个**字面量**，UTF-8-only **必须搜不到**，
       而并集**必须搜得到** —— 证明"并集"不是恒真、确实在干活（反之亦然：类型名对 UTF-16-only 假阴性）

用法：
    python tools/bl_check_deploy_consistency.py                 # 核验当前环境
    python tools/bl_check_deploy_consistency.py --selftest       # 注入故障自测（含反向对照）
    python tools/bl_check_deploy_consistency.py --json-out r.json
    python tools/bl_check_deploy_consistency.py --module-dir <path>   # 换部署目录

退出码：0 = 通过；1 = 有 FAIL；2 = 环境不足（DLL/清单缺失 —— **不算通过**）。
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
SRC_DIR = os.path.join(REPO_ROOT, "src")
TOOLS_DIR = HERE

try:
    import bl_common
except Exception:                                  # noqa: BLE001
    bl_common = None

DEFAULT_GAME_DIR = r"G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord"

# ── 关键符号表 ────────────────────────────────────────────────────────────
#
# 这些是"部署的 DLL 到底是不是当前源码编的"最直接的证据：
#   · get_hero / scan_bad_data / unsupported_in_campaign → **字符串字面量**（#US, UTF-16LE）
#   · IsCampaignActive                                   → **方法名**（#Strings, UTF-8）
# 刻意**两种都放**：这样"只查一种"的写法会在这里**立刻暴露**
# （若只查 UTF-8，`get_hero` 会假阴性；只查 UTF-16，`IsCampaignActive` 会假阴性）。
KEY_SYMBOLS = [
    ("get_hero", "literal"),
    ("scan_bad_data", "literal"),
    ("IsCampaignActive", "typename"),
    ("unsupported_in_campaign", "literal"),
]

# 一个**确定不存在**的符号名，用作反向对照（任何真实构建里都不该出现）。
CONTROL_ABSENT_SYMBOL = "zzz_not_a_real_symbol_qq_20261008"


def module_dir(explicit=None):
    """部署模块目录：<游戏根>/Modules/BlBridge。"""
    if explicit:
        return os.path.abspath(explicit)
    game = os.environ.get("BANNERLORD_DIR") or DEFAULT_GAME_DIR
    return os.path.join(game, "Modules", "BlBridge")


def deployed_dll_path(mod):
    return os.path.join(mod, "bin", "Win64_Shipping_Client", "BlBridge.dll")


def manifest_path(mod):
    return os.path.join(mod, "build_manifest.json")


# ── 两种字符串堆 ──────────────────────────────────────────────────────────
#
# ★ 刻意在**本脚本内**独立实现，而**不是** import bl_mcp 的同名函数。
#   理由：`Modules\BlBridge\mcp\bl_mcp.py`（部署副本）**缺 0020355/ab13ec9**，
#        它只有"只搜 UTF-16"的旧逻辑。若本脚本依赖它，那么**从部署副本跑就会继承那个洞**。
#        自带的实现让本脚本在两边跑都正确（并用 check_tools_drift 把那个漂移报出来）。

def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def has_utf8(blob, needle):
    """`#Strings` 堆（UTF-8）：类型名 / 字段名 / 方法名。"""
    return blob.find(needle.encode("utf-8")) >= 0


def has_utf16(blob, needle):
    """`#US` 堆（UTF-16LE）：字符串字面量。"""
    return blob.find(needle.encode("utf-16-le")) >= 0


def symbol_encoding(blob, needle):
    """返回 'utf8' / 'utf16' / 'both' / None。

    分开返回而不是只给 bool，是为了报告里能说清"是**哪一类**符号被找到了"
    —— 这直接决定"找不到"意味着什么（方法名没编进去 vs 字面量没编进去）。
    """
    u8, u16 = has_utf8(blob, needle), has_utf16(blob, needle)
    if u8 and u16:
        return "both"
    if u8:
        return "utf8"
    if u16:
        return "utf16"
    return None


def has_symbol(blob, needle):
    """★ 两种编码取并集 —— 任一命中即认为在 DLL 里。

    这是本脚本存在的核心理由：只查一种 ⇒ 对另一类**必然假阴性**。
    """
    return symbol_encoding(blob, needle) is not None


# ── A：DLL ↔ 同目录清单 ───────────────────────────────────────────────────

def check_dll_vs_manifest(mod, manifest_override=None):
    """A 段：部署 DLL 的 sha256 vs **与它同目录**的清单。返回 (problems, info)。"""
    problems = []
    info = {}
    dll = deployed_dll_path(mod)
    mf = manifest_override or manifest_path(mod)
    info["dll"] = dll
    info["manifest"] = mf

    if not os.path.isfile(dll):
        problems.append("ENV: 部署 DLL 不存在：%s" % dll)
        return problems, info
    if not os.path.isfile(mf):
        problems.append("ENV: 清单不存在：%s（旧版部署？）" % mf)
        return problems, info

    actual = sha256_file(dll)
    info["dllSha256"] = actual
    info["dllBytes"] = os.path.getsize(dll)

    try:
        with io.open(mf, "r", encoding="utf-8-sig") as fh:
            man = json.load(fh)
    except Exception as exc:                       # noqa: BLE001
        problems.append("A: 清单解析失败：%r" % exc)
        return problems, info

    want = (man.get("dllSha256") or "").lower()
    info["manifestDllSha256"] = want
    info["builtUtc"] = man.get("builtUtc")
    info["version"] = man.get("version")

    if not want:
        problems.append("A: 清单里没有 dllSha256（键路径变了？）—— 抽取失败不算通过")
    elif want != actual:
        problems.append("A: 部署 DLL 的 sha256=%s 与**同目录**清单的 %s 不一致"
                        "（磁盘 vs 清单）—— 不要拿仓库 out/ 的清单当基准，见文件头"
                        % (actual[:16], want[:16]))

    wb = man.get("dllBytes")
    if isinstance(wb, int) and wb != info["dllBytes"]:
        problems.append("A: 清单 dllBytes=%d 与实际字节 %d 不一致" % (wb, info["dllBytes"]))
    return problems, info


# ── B：清单 sources ↔ 磁盘 src/ ───────────────────────────────────────────

def check_sources_vs_disk(man, src_dir=None):
    """B 段：清单 sources 的每个哈希 vs 磁盘 src/。返回 (problems, info)。

    `src_dir` 不存在 ⇒ **如实标注跳过**（部署副本旁边没有 src/），
    而不是报一个查无此处的路径 —— 那会让整个结论没法信（AGENTS.md §四）。
    """
    problems = []
    info = {}
    src = src_dir or SRC_DIR
    info["srcDir"] = src
    if not os.path.isdir(src):
        info["skipped"] = "skipped_no_src_dir"
        info["note"] = ("本副本旁边没有 src/（部署副本）：源码段跳过，"
                        "只核「清单 ↔ 部署 DLL」与关键符号；要核源码链请在仓库 tools\\ 下跑")
        return problems, info

    want = man.get("sources") or {}
    if not want:
        problems.append("ENV: 清单里没有 sources（抽取为空不算通过）")
        return problems, info

    have = sorted(n for n in os.listdir(src) if n.endswith(".cs"))
    mismatched, missing_on_disk = [], []
    for name in sorted(want):
        p = os.path.join(src, name)
        if not os.path.isfile(p):
            missing_on_disk.append(name)
            continue
        if sha256_file(p).lower() != (want[name] or "").lower():
            mismatched.append(name)
    not_in_manifest = [n for n in have if n not in want]

    info.update({"manifestSourceCount": len(want), "diskSourceCount": len(have),
                 "mismatched": mismatched, "missingOnDisk": missing_on_disk,
                 "notInManifest": not_in_manifest})

    for name in mismatched:
        problems.append("B: 源文件哈希不一致（清单 vs 磁盘）：%s —— 改了源码但没重新构建/部署" % name)
    for name in missing_on_disk:
        problems.append("B: 清单里有、磁盘 src/ 上没有：%s" % name)
    for name in not_in_manifest:
        problems.append("B: 磁盘 src/ 上有、清单里没有（新文件没进构建清单？）：%s" % name)
    return problems, info


# ── C：关键符号（双编码并集）───────────────────────────────────────────────

def check_key_symbols(blob, key_symbols=None):
    """C 段：部署 DLL 是否含关键符号。返回 (problems, info)。"""
    problems = []
    symbols = key_symbols if key_symbols is not None else KEY_SYMBOLS
    detail, missing = {}, []
    for name, kind in symbols:
        enc = symbol_encoding(blob, name)
        detail[name] = {"kind": kind, "encoding": enc}
        if enc is None:
            missing.append("%s (%s)" % (name, kind))
    info = {"checked": len(symbols), "missing": missing, "detail": detail,
            "note": "两种编码取并集：类型名/字段名在 UTF-8(#Strings)，字面量在 UTF-16LE(#US)"}
    for m in missing:
        problems.append("C: 关键符号不在部署 DLL 里：%s —— 内容级判据（哈希链条抓不到这一层）" % m)
    return problems, info


# ── D：部署副本 vs 仓库 tools/ 的漂移 ─────────────────────────────────────

# ★ 覆盖范围：**自动发现**部署副本 `mcp/*.py`，逐个与仓库 `tools/` 对账。
#
# 为什么改成自动发现（2026-10-08，第一版只写死了 ["bl_mcp.py"]）：
#   RTS 那起事故暴露的就是"**我只给一个工具写了检查**"的思维 ——
#   当时 `bl_rts.py` 也漂移了、也带着缺陷（不转发 `path`），但**D 段根本没看它**。
#   写死名单必然漏：`build.ps1` 是"复制整个 tools/*.py"，名单却要人手维护 ⇒ 两者迟早不同步。
#   ⇒ 改成"以**部署副本里实际存在的 .py** 为准"，缺一即查一。
#   （`tools/` 里新增文件后，只要部署过就会被覆盖到，不需要改这个文件。）
TOOLS_DRIFT_FALLBACK = ["bl_mcp.py"]


def check_tools_drift(mod):
    """D 段：部署副本 `mcp/*.py` vs 仓库 `tools/*.py`。返回 (notes, info)。

    这一项**默认只作为 note 报告、不判 FAIL**：部署副本落后本身不影响现场
    （现场层走仓库 `tools/`），但它**有已知代价**（见下）——
    ⇒ 由 `--strict-drift` 显式开启时才升为 FAIL（发布前 / 交给别人之前强制核对）。

    ## ★ 已知代价（不是"无代价的 note"，是"有代价但被接受"）

    `Modules\\BlBridge\\mcp\\bl_mcp.py` 是**给"装了 mod 就用"的人**看的那一份。
    它落后时的真实后果（已实测）：
      · 若有人**从部署副本**跑 `bl_build_check`，用的是**旧的"只搜 UTF-16"**逻辑
        ⇒ **对类型名/字段名假阴性**（`ab13ec9` 修的就是这个洞）；
      · 若有人从部署副本跑 `bl_apply_config`，会**缺 `path` 参数**、并且带着
        "成功分支返回裸 dict ⇒ 派发 ValueError（而文件已写盘）"那个缺陷。
    ⇒ 所以它在"现场不阻塞"与"误导副本使用者"之间是个**折中**，
      默认不红是为了不阻塞当前工作，`--strict-drift` 是给"发布前必须干净"的场景用的。
    """
    notes = []
    info = {}
    # ★ 自动发现部署副本里的 .py（不再写死名单 —— 写死名单必然漏，见上面注释）。
    names = []
    mcp_dir = os.path.join(mod, "mcp")
    try:
        names = sorted(n for n in os.listdir(mcp_dir) if n.endswith(".py"))
    except OSError:
        names = []
    if not names:
        # 副本目录读不到 / 为空 ⇒ 退回最小集合，让"副本不存在"也能被报出来
        # （**抽取为空不算通过** —— 与 bl_check_dispatch 的 ENV 口径一致）
        names = list(TOOLS_DRIFT_FALLBACK)
    info["_count"] = len(names)
    for name in names:
        dep = os.path.join(mod, "mcp", name)
        rep = os.path.join(TOOLS_DIR, name)
        row = {}
        if not os.path.isfile(dep):
            row["note"] = "部署副本缺少 %s" % name
        else:
            row["deployedBytes"] = os.path.getsize(dep)
            row["deployedSha256"] = sha256_file(dep)
        if os.path.isfile(rep):
            row["repoBytes"] = os.path.getsize(rep)
            row["repoSha256"] = sha256_file(rep)
        else:
            # 部署有、仓库没有 ⇒ 也是漂移（多半是仓库删了文件但副本没刷新）
            row["note"] = row.get("note") or ("仓库 tools/ 里没有 %s（部署有、仓库无）" % name)
        if row.get("deployedSha256") and row.get("repoSha256"):
            row["identical"] = row["deployedSha256"] == row["repoSha256"]
            if not row["identical"]:
                text = io.open(dep, "r", encoding="utf-8", errors="replace").read()
                row["hasTwoHeapFix"] = "_dll_has_utf8" in text and "_dll_has_symbol" in text
                row["hasPathParam"] = '"path"' in text
                notes.append(
                    "D: 部署副本 %s 与仓库 tools/%s **不一致**（%s vs %s）%s"
                    % (name, name, row["deployedSha256"][:16], row["repoSha256"][:16],
                       "" if row["hasTwoHeapFix"] else
                       "；★ 且它**没有**双编码修正（`ab13ec9`）⇒ 从部署副本跑 build_check"
                       "只有 UTF-16 逻辑 ⇒ **对类型名/字段名假阴性**"))
        info[name] = row
    return notes, info


def drift_problems(d_tools):
    """把 D 段的不一致转成 **FAIL 条目**（只由 `--strict-drift` 调用）。

    单独成函数是为了让"默认 note / 严格 FAIL"两种口径**用同一份事实**，
    不会出现"note 说 A 不一致、FAIL 却判 B"这种两套判据。
    """
    probs = []
    for name, row in (d_tools or {}).items():
        if name.startswith("_"):
            continue          # `_count` 之类的元信息，不是文件行
        if row.get("deployedSha256") and row.get("repoSha256") and not row.get("identical"):
            probs.append("D(strict): 部署副本 mcp/%s 与仓库 tools/%s 不一致（%s vs %s）"
                         "—— 已知代价：会误导从 Modules\\ 跑工具的人"
                         % (name, name, row["deployedSha256"][:16], row["repoSha256"][:16]))
        elif row.get("note"):
            probs.append("D(strict): %s" % row["note"])
    return probs


# ── 主审计 ────────────────────────────────────────────────────────────────

def audit(mod=None, src_dir=None, key_symbols=None, manifest_override=None,
          strict_drift=False):
    """跑 A/B/C/D 四段。返回 dict（problems 为空 = 通过）。

    `strict_drift=True` 时把 D 段（部署副本 ↔ 仓库 tools/）的不一致**升为 FAIL**；
    默认只作 note（现场走仓库 `tools/`，漂移不阻塞当前工作）。
    """
    mod = module_dir(mod)
    out = {"moduleDir": mod, "problems": [], "notes": []}

    probs_a, info_a = check_dll_vs_manifest(mod, manifest_override=manifest_override)
    out["a_dllVsManifest"] = info_a
    out["problems"].extend(probs_a)

    dll = deployed_dll_path(mod)
    if os.path.isfile(dll):
        blob = read_bytes(dll)
        probs_c, info_c = check_key_symbols(blob, key_symbols=key_symbols)
        out["c_keySymbols"] = info_c
        out["problems"].extend(probs_c)
    else:
        out["c_keySymbols"] = {"skipped": "no_dll"}

    mf = manifest_override or manifest_path(mod)
    man = None
    if os.path.isfile(mf):
        try:
            with io.open(mf, "r", encoding="utf-8-sig") as fh:
                man = json.load(fh)
        except Exception:                          # noqa: BLE001
            man = None
    if man is not None:
        probs_b, info_b = check_sources_vs_disk(man, src_dir=src_dir)
        out["b_sourcesVsDisk"] = info_b
        out["problems"].extend(probs_b)
    else:
        out["b_sourcesVsDisk"] = {"skipped": "no_manifest"}

    notes_d, info_d = check_tools_drift(mod)
    out["d_toolsDrift"] = info_d
    out["notes"].extend(notes_d)
    # ★ 只有显式 `--strict-drift` 才把副本漂移升为 FAIL（默认不阻塞现场）。
    if strict_drift:
        out["problems"].extend(drift_problems(info_d))

    # 信息项：仓库 out/ 的清单（**只报告，不作基准**）
    out_mf = os.path.join(REPO_ROOT, "out", "BlBridge.manifest.json")
    if os.path.isfile(out_mf):
        try:
            with io.open(out_mf, "r", encoding="utf-8-sig") as fh:
                om = json.load(fh)
            dep = (info_a.get("manifestDllSha256") or "")
            rep = (om.get("dllSha256") or "").lower()
            out["repoOutManifest"] = {
                "path": out_mf, "dllSha256": rep, "builtUtc": om.get("builtUtc"),
                "sameSourcesAsDeployed": (om.get("sources") or {}) == (
                    (man or {}).get("sources") or {}),
                "dllSahaEqualsDeployed": rep == dep,
            }
            if rep and dep and rep != dep:
                out["notes"].append(
                    "N: 仓库 out/ 的清单 dllSha256=%s ≠ 部署清单 %s，但两者 sources 相同"
                    "⇒ **同日重编也会产出不同字节**（构建非确定性）"
                    "⇒ dllSha256 **只能**与随 DLL 一起部署的那份清单对账，"
                    "**不要**拿仓库 out/ 当基准" % (rep[:16], dep[:16]))
        except Exception:                          # noqa: BLE001
            pass
    return out


# ── 反向对照 ──────────────────────────────────────────────────────────────

def _status(ok):
    return "OK" if ok else "FAIL"


def selftest():
    """注入故障 + 双编码必要性对照。★ 只证 missing=0 毫无价值，必须证明它会红。"""
    mod = module_dir()
    dll = deployed_dll_path(mod)
    ok = True

    print("== A5 反向对照 ==")
    if not os.path.isfile(dll):
        print("[ENV] 找不到部署 DLL：%s —— 反向对照需要真实 DLL" % dll)
        return 2

    blob = read_bytes(dll)

    # ── 对照组 0：未注入时应当 0 报错（基线） ─────────────────────────────
    base = audit()
    good = (not base["problems"])
    print("   [%s] 基线（未注入）：真实环境 problems=%d %s"
          % (_status(good), len(base["problems"]),
             "" if good else "-> %s" % base["problems"][:2]))
    if not good:
        # 真实环境本来就不一致 ⇒ 基线不该"通过"。这不算脚本失败，如实报告并继续。
        print("        （注意：真实环境当前不一致，见上面的 problems；"
              "这仍能证明判据不是恒真）")

    # ── 对照组 1：注入一个**不存在**的符号 ⇒ 必须被报出来 ────────────────
    injected = KEY_SYMBOLS + [(CONTROL_ABSENT_SYMBOL, "literal")]
    probs, _info = check_key_symbols(blob, key_symbols=injected)
    hit = [p for p in probs if CONTROL_ABSENT_SYMBOL in p]
    ok1 = bool(hit)
    print("   [%s] 注入不存在的符号 %s => %s"
          % (_status(ok1), CONTROL_ABSENT_SYMBOL,
             ("被判据报出: " + hit[0][:70]) if hit else "**没报出来（判据恒真！）**"))
    ok = ok and ok1

    # ── 对照组 2/3/4：篡改清单 ⇒ A/B 必须红 ─────────────────────────────
    domod = tempfile.mkdtemp(prefix="bl_a5_")
    try:
        real_mf = manifest_path(mod)
        with io.open(real_mf, "r", encoding="utf-8-sig") as fh:
            man = json.load(fh)

        # 2: 篡改 dllSha256
        bad = json.loads(json.dumps(man))
        bad["dllSha256"] = "0" * 64
        p = os.path.join(domod, "m_sha.json")
        _write_json(p, bad)
        probs, _ = check_dll_vs_manifest(mod, manifest_override=p)
        got = [x for x in probs if x.startswith("A")]
        print("   [%s] 篡改 dllSha256 => %s"
              % (_status(bool(got)), (got[0][:70] if got else "**没报出来**")))
        ok = ok and bool(got)

        # 3: 篡改某个源文件哈希（点名文件）
        bad = json.loads(json.dumps(man))
        victim = sorted((bad.get("sources") or {}))[0] if bad.get("sources") else None
        if victim:
            bad["sources"][victim] = "1" * 64
            p = os.path.join(domod, "m_src.json")
            _write_json(p, bad)
            probs, _ = check_sources_vs_disk(bad)
            got = [x for x in probs if x.startswith("B") and victim in x]
            print("   [%s] 篡改源哈希（%s）=> %s"
                  % (_status(bool(got)), victim, (got[0][:70] if got else "**没报出来**")))
            ok = ok and bool(got)

        # 4: 从清单里删掉一个源文件条目
        bad = json.loads(json.dumps(man))
        if victim and victim in (bad.get("sources") or {}):
            del bad["sources"][victim]
            probs, info = check_sources_vs_disk(bad)
            got = [x for x in probs if x.startswith("B") and victim in x]
            print("   [%s] 清单漏一个源文件（%s）=> %s"
                  % (_status(bool(got)), victim,
                     (got[0][:70] if got else "**没报出来**")))
            ok = ok and bool(got)
    finally:
        shutil.rmtree(domod, ignore_errors=True)

    # ── 对照组 5：★ 双编码必要性（证明"取并集"不是恒真）─────────────────
    #
    # 这是最重要的一条：若本脚本内部退化成"只查一种编码"，
    # 下面两个断言会**立刻**变红。它证明并集**确实在干活**。
    lit = "get_hero"          # 字面量 → 只该在 UTF-16
    typ = "IsCampaignActive"  # 方法名 → 只该在 UTF-8

    ok_lit = (not has_utf8(blob, lit)) and has_utf16(blob, lit) and has_symbol(blob, lit)
    print("   [%s] 双编码必要性（字面量 %s）：UTF-8-only=%s（应 False）/"
          "UTF-16=%s（应 True）/ 并集=%s（应 True）"
          % (_status(ok_lit), lit, has_utf8(blob, lit), has_utf16(blob, lit), has_symbol(blob, lit)))
    ok = ok and ok_lit

    ok_typ = (not has_utf16(blob, typ)) and has_utf8(blob, typ) and has_symbol(blob, typ)
    print("   [%s] 双编码必要性（方法名 %s）：UTF-16-only=%s（应 False）/"
          "UTF-8=%s（应 True）/ 并集=%s（应 True）"
          % (_status(ok_typ), typ, has_utf16(blob, typ), has_utf8(blob, typ), has_symbol(blob, typ)))
    ok = ok and ok_typ

    # ── 对照组 6：不存在的符号对**两种**编码都该是 False（避免恒真） ────
    ok_abs = (not has_utf8(blob, CONTROL_ABSENT_SYMBOL)) and \
             (not has_utf16(blob, CONTROL_ABSENT_SYMBOL)) and \
             (not has_symbol(blob, CONTROL_ABSENT_SYMBOL))
    print("   [%s] 不存在符号两种编码均 False（有分辨力）" % _status(ok_abs))
    ok = ok and ok_abs

    print("结果: %s" % ("反向对照全部通过（判据真的在判定）" if ok else "★ 有对照未通过 —— 判据可疑"))
    return 0 if ok else 1


def _write_json(path, obj):
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=1))


# ── 打印 ──────────────────────────────────────────────────────────────────

def render(res):
    print("部署模块目录：%s" % res["moduleDir"])
    a = res.get("a_dllVsManifest") or {}
    print("\nA. 部署 DLL ↔ 同目录清单")
    print("   DLL      : %s" % a.get("dll"))
    print("   sha256   : %s%s" % ((a.get("dllSha256") or "?")[:16],
                                 "" if not a.get("dllBytes") else "  (%d B)" % a["dllBytes"]))
    print("   清单     : %s  dllSha256=%s  builtUtc=%s  version=%s"
          % (a.get("manifest"), (a.get("manifestDllSha256") or "?")[:16],
             a.get("builtUtc"), a.get("version")))
    print("   => %s" % ("一致 ✅" if a.get("dllSha256") == a.get("manifestDllSha256") else "不一致 ❌"))

    b = res.get("b_sourcesVsDisk") or {}
    print("\nB. 清单 sources ↔ 磁盘 src/")
    if b.get("skipped"):
        print("   %s（%s）" % (b["skipped"], b.get("note", "")))
    else:
        print("   清单 %s 项 / 磁盘 %s 项；不一致 %d，清单有磁盘无 %d，磁盘有清单无 %d"
              % (b.get("manifestSourceCount"), b.get("diskSourceCount"),
                 len(b.get("mismatched") or []), len(b.get("missingOnDisk") or []),
                 len(b.get("notInManifest") or [])))
        print("   => %s" % ("0 差异 ✅" if not (b.get("mismatched") or b.get("missingOnDisk")
                                              or b.get("notInManifest")) else "有差异 ❌"))

    c = res.get("c_keySymbols") or {}
    print("\nC. 关键符号（★ 两种编码取并集）")
    if c.get("skipped"):
        print("   %s" % c["skipped"])
    else:
        for name, d in (c.get("detail") or {}).items():
            print("   %-26s %-9s %s" % (name, d.get("kind"), d.get("encoding") or "**缺失**"))
        print("   => %s" % ("全部命中 ✅" if not c.get("missing") else "缺失：%s ❌" % c["missing"]))

    d = res.get("d_toolsDrift") or {}
    if d:
        print("\nD. 部署副本 ↔ 仓库 tools/ 漂移"
              + ("（★ --strict-drift：不一致判 FAIL）" if res.get("strictDrift") else
                 "（默认只作 note：现场走仓库 tools/，漂移不阻塞；`--strict-drift` 可强制核对）"))
        # ★ 只逐行列出**不一致**的：48 个副本全列会把信号淹掉
        #   （本项目的风格是"报告要让人一眼看出哪里不对"）。
        same_n, diff_n = 0, 0
        for name, row in d.items():
            if name.startswith("_"):
                continue      # 元信息（如 _count），不是文件行
            if row.get("identical"):
                same_n += 1
                continue
            diff_n += 1
            print("   ★不一致 %s: 部署 %s(%s B) vs 仓库 %s(%s B)"
                  % (name, (row.get("deployedSha256") or "?" )[:16], row.get("deployedBytes"),
                     (row.get("repoSha256") or "?")[:16], row.get("repoBytes")))
        print("   自动发现 %s 个副本 .py：一致 %d 个 / **不一致 %d 个**"
              "（不写死名单 —— 防『只检查一个工具』）"
              % (d.get("_count"), same_n, diff_n))

    ro = res.get("repoOutManifest")
    if ro:
        print("\nN. 参考（**不作基准**）：仓库 out/ 清单 dllSha256=%s builtUtc=%s；"
              "sources 与部署清单相同=%s"
              % ((ro.get("dllSha256") or "?")[:16], ro.get("builtUtc"),
                 ro.get("sameSourcesAsDeployed")))

    for n in res.get("notes") or []:
        print("\n[注意] %s" % n)
    for p in res.get("problems") or []:
        print("\n[FAIL] %s" % p)


def main(argv=None):
    if bl_common is not None:
        try:
            bl_common.safe_streams()
        except Exception:                          # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="部署产物 ↔ 源码 一致性判据（含反向对照）")
    ap.add_argument("--selftest", action="store_true", help="注入故障自测（含双编码必要性对照）")
    ap.add_argument("--module-dir", help="部署模块目录（默认 <BANNERLORD_DIR>/Modules/BlBridge）")
    ap.add_argument("--src-dir", help="源码目录（默认仓库 src/）")
    ap.add_argument("--json-out", help="把结果写成 JSON")
    ap.add_argument("--strict-drift", action="store_true",
                    help="把 D 段「部署副本 mcp/*.py ↔ 仓库 tools/*.py 不一致」**升为 FAIL**"
                         "（默认只作 note）。发布前 / 交付他人前建议开。"
                         "★ 已知代价：副本落后会误导『从 Modules\\ 跑工具』的人 —— "
                         "例如旧的副本跑 build_check 只有 UTF-16 逻辑，对类型名假阴性。")
    args = ap.parse_args(argv)

    if args.selftest:
        code = selftest()
        # 反向对照通过后，再跑一次真实环境审计并给出退出码（便于门禁直接用）
        res = audit(mod=args.module_dir, src_dir=args.src_dir,
                    strict_drift=args.strict_drift)
        res["strictDrift"] = bool(args.strict_drift)
        render(res)
        if args.json_out:
            _write_json(args.json_out, res)
        if code != 0:
            return code
        return 0 if not res["problems"] else 1

    res = audit(mod=args.module_dir, src_dir=args.src_dir, strict_drift=args.strict_drift)
    res["strictDrift"] = bool(args.strict_drift)
    render(res)
    if args.json_out:
        _write_json(args.json_out, res)

    has_dll = os.path.isfile(deployed_dll_path(res["moduleDir"]))
    if not has_dll:
        print("\n结果: 环境不足（没有部署 DLL）—— **不算通过**")
        return 2
    if res["problems"]:
        print("\n结果: 失败 %d 项" % len(res["problems"]))
        return 1
    print("\n结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
