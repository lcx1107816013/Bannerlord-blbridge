#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
源码定位（栈帧 → 文件:行号）—— 宿主侧，**只读**，仅标准库。

## 它解决什么

崩溃栈原本**只有方法名**：

    at HarmonyLib.PatchTools.GetOriginalMethod(HarmonyMethod attr)

⇒ agent 无法跳到源码，「看完数据再改代码」断在第一步。
本工具把栈帧映射到**源码文件与行号**，并给出该处的**真实源码片段**，
让 agent 能直接读上下文、动手改。

## 两条数据路径（各有边界，如实标注）

| 路径 | 来源 | 覆盖 | 前提 |
|---|---|---|---|
| **A. 行号直取** | 栈文本里的 `文件:行号` | 只有**带 PDB 编译**的程序集 | 该 DLL 用 `/debug:full` 编译过 |
| **B. 符号索引** | `out/BlBridge.symbols.json` | **我们自己的** DLL（方法名→文件:行号） | 需要 PDB 生成过索引 |

★ **两者不能互相替代**，也不该假装能覆盖全部：

- 第三方 mod 的栈帧（如 `HarmonyLib.*`、`RBM*`）：它们的 DLL **不带 PDB**
  ⇒ 路径 A 拿不到行号、路径 B 也没有它们的索引
  ⇒ 此时**如实说"无法定位到行"**，只给方法名 + 它属于哪个程序集。
  **绝不编造一个行号。**
- 我们自己的栈帧：两条路径都能用，优先 A（栈里直接带着，最权威）。

## ★ 实测依据（2026-10-07，受控实验）

| 编译参数 | 栈里有行号 |
|---|---|
| `/debug-`（旧设置） | ❌ |
| `/debug:portable` | ❌ **`.NET Framework` 不认 portable PDB** |
| **`/debug:full`** | ✅ 形如 `位置 C:\\...\\X.cs:行号 24` |

**两个必须记住的坑**：

1. **栈文本被本地化**：中文 Windows 是 `位置 <file>:行号 24`，英文是 `in <file>:line 24`。
   ⇒ 解析必须**与语言无关**：认"路径样 + 冒号 + 数字"这个形状，**不认英文关键词**。
   （首版只认 `":line "`，在中文系统上把"有行号"判成"没有"，差点毁掉整个方案。）
2. **绝对路径泄漏**：栈与 PDB 都带编译机路径（`C:\\Users\\<名字>\\...`）。
   ⇒ 用 `/pathmap` 改写成 `/src/X.cs` 这种符号形式（实测行号仍准确）。

## 用法

    python tools/bl_source_map.py --stack "at Foo.Bar() 位置 /src/X.cs:行号 12"
    python tools/bl_source_map.py --from-exceptions     # 读 exceptions.jsonl 逐条定位
    python tools/bl_source_map.py --resolve BlBridge.CrashGuard.Finalizer
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

REPO = os.path.abspath(os.path.join(HERE, os.pardir))
OUT_DIR = os.path.join(REPO, "out")
INDEX_PATH = os.path.join(OUT_DIR, "BlBridge.symbols.json")
SRC_DIR = os.path.join(REPO, "src")

# 第三方符号索引（由 bl_symbols.py --third-party 生成）
import bl_symbols as _symbols                            # noqa: E402

tp_load = _symbols.load_thirdparty_indexes

# 已声明的“已知 BCL/框架”前缀：这些**本来就不该有源码**（不在我们仓库里）
THIRD_PARTY_HINTS = (
    "system.", "microsoft.", "harmony", "newtonsoft", "mono.",
    "taleworlds.", "sandbox.", "rbm", "butterlib", "mcm",
)


def _pdb_available():
    return os.path.isfile(os.path.join(OUT_DIR, "BlBridge.pdb"))


def load_index(path=None):
    p = path or INDEX_PATH
    if not os.path.isfile(p):
        return None
    try:
        with io.open(p, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:                                     # noqa: BLE001
        return None


# ── 栈文本解析：**与语言无关**（核心判据）────────────────────────────────
#
# 实测的三种栈行形态：
#   en: `   at Foo.Bar() in /src/X.cs:line 24`
#   zh: `   在 Foo.Bar() 位置 /src/X.cs:行号 24`     ← 中文 Windows 实测
#   无行号: `   at Foo.Bar()`
#
# ⇒ 判据：**先找 ".cs:" 或 ".cs:行号 " 这种形状**，再用正则取数字；
#   **不依赖** "line"/"in"/"位置" 任何一个词（它们都会随语言变）。
_METHOD_RE = re.compile(r"(?:at|在)\s+([^\s(]+)\s*\(")


def parse_frame(line):
    """解析单个栈帧 → dict。

    返回 {method, file, line, hasLine, raw}。
    `file`/`line` 为 None 时**表示"这条栈里没有行号信息"**（不是解析失败）。
    """
    raw = (line or "").strip()
    out = {"raw": raw, "method": None, "file": None, "line": None, "hasLine": False}
    if not raw:
        return out

    m = _METHOD_RE.search(raw)
    if m:
        out["method"] = m.group(1)

    # ★ 语言无关的行号提取：找 `xxx.cs` 后面跟的 `: <可选本地化词> <数字>`
    #   支持 `.cs` / `.cs` 之外的常见源后缀，便于将来别的语言
    for fm in re.finditer(r"([A-Za-z0-9_./\\-]+\.(?:cs|vb|fs|py))(?::\s*[^\d\s]{0,6}\s*)?(\d+)",
                          raw):
        cand_file = fm.group(1)
        # 只接受"看起来是源文件"的（去掉纯数字误配）
        out["file"] = os.path.basename(cand_file.replace("\\", "/"))
        out["fullPath"] = cand_file.replace("\\", "/")
        out["line"] = int(fm.group(2))
        out["hasLine"] = True
        break
    return out


def parse_stack(text):
    """解析整段栈文本（多行）→ 帧列表。"""
    if not text:
        return []
    frames = []
    for line in str(text).split("\n"):
        s = line.strip()
        if not s:
            continue
        fr = parse_frame(s)
        if fr["method"] or fr["hasLine"]:
            frames.append(fr)
    return frames


def locate(method, index=None, allow_shortname_fallback=True):
    """用**我们自己的**符号索引把方法全名映射到 文件:行号。返回 dict 或 None。

    ★★ 内置白名单守卫（实测缺陷，2026-10-07）：
    首版靠**调用方记得先判归属**来避免把 `RBM.SubModule.OnSubModuleLoad`
    配到 `BlBridge.SubModule.OnSubModuleLoad` —— 但那是**靠约定**，
    `locate()` 被单独调用时依然会配错（自测 14c-1 正是这么抓到的）。
    根因在这里：短名兜底用 `name == OnSubModuleLoad` 做尾匹配，
    而**同名方法在所有 mod 里都存在**（`OnSubModuleLoad` 是通用入口名）。

    ⇒ 把守卫**放进函数本体**：不是 `BlBridge.*` 的方法**直接返回 None**，
      不依赖任何调用方顺序。
    """
    if not method:
        return None
    if not _is_our_frame(method):
        return None
    idx = index if index is not None else load_index()
    if not idx:
        return None
    entries = idx.get("entries") or []
    # 精确 → 去括号后缀
    want = method.strip()
    for e in entries:
        if e.get("method") == want:
            return e
    short = want.split("(")[0]
    for e in entries:
        if e.get("method") == short:
            return e
    if not allow_shortname_fallback:
        return None
    # ⚠️ 尾匹配**风险最高**（同名方法多）—— 因此只在"类型名也对得上"时才接受。
    #    例如栈里 `BlBridge.SubModule.OnSubModuleLoad` 与索引条目
    #    `BlBridge.SubModule.OnSubModuleLoad` 已在上面的精确分支命中；
    #    走到这里说明类型名有差异（如外部命名空间），此时**要求类型全名以栈里的为准**。
    tail = short.split(".")[-1]
    type_part = short[:-(len(tail) + 1)] if short.endswith("." + tail) else ""
    cands = []
    for e in entries:
        if (e.get("name") or "") != tail:
            continue
        if type_part and not (e.get("type") or "").endswith(type_part):
            continue
        cands.append(e)
    if len(cands) == 1:
        return cands[0]
    return None


# ── 第三方程序集定位（v0.8.51）────────────────────────────────────────────
#
# ## 关键设计：**按程序集分别查，且处理同名的多版本歧义**
#
# 实测：`Bannerlord.MBOptionScreen` 一个模块里就有 **11 个版本**的 dll+pdb
# （v1.4.0~v1.5.1），`Bloodlust` 有 6 个。若只按"类型名"全局查，
# 会命中**另一个版本**的索引 ⇒ **给出错行号**。
#
# ⇒ 对策（两道）：
#   ① 先用**栈帧里的程序集线索**（`Type.FullName` 的命名空间前缀）挑候选索引；
#   ② 候选**多于一个**时，**不猜** —— 全部列出来并标注"版本不确定"，
#      让调用方（或人）知道这里有歧义。
#      **宁可说"不确定"，也不给一个可能是错的行号。**

_TP_CACHE = {"loaded": False, "indexes": None, "gv": None, "pref": None}


def _thirdparty_indexes():
    if not _TP_CACHE["loaded"]:
        idxs = tp_load() or {}
        _TP_CACHE["indexes"] = idxs
        # ★ 游戏版本消歧（v0.8.51）：把"同名多版本"收窄到**对应本机游戏版本**那个。
        #   实测动机：`ButterLib.Implementation` 有 42 个版本共存，同一个方法在不同版本里
        #   行号不同（144 vs 149）⇒ 不消歧会有 18.3% 的真实方法被"歧义"拒掉。
        try:
            gv = _symbols.game_version()
            pref, _dropped = _symbols.preferred_index_names(idxs, gv)
        except Exception:                                 # noqa: BLE001
            gv, pref = None, {}
        _TP_CACHE["gv"] = gv
        _TP_CACHE["pref"] = pref or {}
        _TP_CACHE["loaded"] = True
    return _TP_CACHE["indexes"] or {}


def _versioned_keys(idxs):
    """索引名里带"数字尾段"的（形如 `xxx.1.4.8` 或 `xxx.v1.4.0`）—— 多版本共存的那批。

    ★ `v` 前缀必须一起认（实测 2026-10-07）：同一个仓库里两种命名形态并存 ——
      `Bannerlord.ButterLib.Implementation.1.4.8`（点号）与
      `Bannerlord.MBOptionScreen.v1.4.0`（**v 前缀**）。
      首版只认纯数字尾段 ⇒ MCM 的 11 个版本一个都没被消歧，
      仍以"多版本歧义"被拒（把覆盖率提升几乎全吃掉）。
    """
    out = set()
    for asm in idxs:
        _head, _dot, tail = asm.rpartition(".")
        t = tail[1:] if tail.startswith("v") else tail
        if t[:1].isdigit():
            out.add(asm)
    return out


def locate_thirdparty(method, indexes=None):
    """在第三方索引里定位一个方法。返回 (best, candidates)。

    **先在"游戏版本对应的索引"里找**；找不到才退回全部索引。

    `best` 为 None 时表示**不确定或没有** —— 调用方应如实说"无法确定"，
    **不要**退回"取第一个"（那正是本功能要防的错行号）。
    """
    idxs = indexes if indexes is not None else _thirdparty_indexes()
    if not idxs or not method:
        return None, []
    want = (method or "").strip().split("(")[0]

    # ★ 候选池：优先"游戏版本对应"的那批（消歧）；其余版本不参与，避免假歧义。
    pref = _TP_CACHE.get("pref") or {}
    vkeys = _versioned_keys(idxs)
    pool = idxs
    if pref:
        narrowed = {}
        for asm, idx in idxs.items():
            # 保留：① 该 stem 的"首选版本"；② 不带版本号尾段的普通索引
            if asm in pref.values() or asm not in vkeys:
                narrowed[asm] = idx
        if narrowed:
            pool = narrowed

    def scan(pred, source=None):
        hits = []
        for asm, idx in (source if source is not None else pool).items():
            for e in (idx.get("entries") or []):
                if pred(e):
                    hits.append({"assembly": asm, "indexPdb": idx.get("pdb"), **e})
        return hits

    # ① 精确方法全名
    hits = scan(lambda e: e.get("method") == want)
    # ② 后缀匹配（栈里可能带/不带命名空间）
    if not hits:
        hits = scan(lambda e: (e.get("method") or "").endswith("." + want) or
                              (e.get("method") or "") == want)
    # ③ 类型名也必须对得上（只靠方法名一律不认）
    #
    # ★★ 这里曾是**假行号**的来源（实测 2026-10-07）：
    #   `TaleWorlds.MountAndBlade.Mission.Tick` 被尾匹配到**任意**名叫 `Tick` 的方法
    #   （共 10 个候选）；`HarmonyLib.PatchTools.GetOriginalMethod` 更被配到
    #   `BUTR.CrashReport.Bannerlord.HarmonyProvider.GetOriginalMethod`
    #   —— **类型不同、只是方法同名**，却报得"很确定"。
    if not hits:
        short = want.split(".")[-1]
        type_part = want[:-(len(short) + 1)] if want.endswith("." + short) else ""
        if type_part:
            hits = scan(lambda e: (e.get("name") or "") == short
                                  and (e.get("type") or "").endswith(type_part))
        if not hits:
            # 消歧池里没有 ⇒ 再给**全量池**一次机会（某些 mod 不做版本化命名）
            if pool is not idxs:
                hits = scan(lambda e: e.get("method") == want, idxs)
    if not hits:
        return None, []

    # ★ 过滤掉**行号不可信**的条目（实测：第三方 PDB 里有 16707566 这种损坏行号）。
    good = [h for h in hits if _line_is_plausible(h.get("line"))]
    if not good:
        return None, hits          # 有命中但行号都不可信 ⇒ 不猜

    # 去重到"程序集 + 方法 + 行"
    uniq = {}
    for h in good:
        key = (h["assembly"], h.get("method"), h.get("file"), h.get("line"))
        uniq[key] = h
    hits = list(uniq.values())

    # ★ 行号不同 ⇒ 真歧义 ⇒ **不猜**（消歧成功时通常只剩一组）
    lines = {(h.get("file"), h.get("line")) for h in hits}
    if len(lines) > 1:
        return None, hits
    # 行号一致 ⇒ 可以给（任取一个代表，并带上是哪个 dll）
    best = dict(hits[0])
    best["alsoIn"] = sorted({h["assembly"] for h in hits})
    best["ambiguous"] = False
    if pref:
        best["disambiguatedBy"] = "gameVersion=%s" % _TP_CACHE.get("gv")
    return best, hits


# 行号的**合理性上界**。
#
# ★ 为什么必须有它（实测发现，2026-10-07）：第三方 PDB 里出现了**离谱的行号** ——
#   `BellumCivile...TryFinalizeCrownPromotionHierarchy` 的 `line` 是 **16707566**
#   （一千六百万）。那是 PDB 数据损坏/错位的表现，**不是**真实行号。
#   若不拦，agent 会拿着"第 16707566 行"去改代码（或以为源码有 1600 万行）。
#   ⇒ 超过这个上界的行号一律**判为不可信**并如实说明，绝不当成位置报出去。
MAX_PLAUSIBLE_LINE = 2_000_000


def _line_is_plausible(line):
    try:
        v = int(line)
    except (TypeError, ValueError):
        return False
    return 1 <= v <= MAX_PLAUSIBLE_LINE


def _is_third_party(method):
    m = (method or "").lower()
    return any(m.startswith(p) or ("." + p) in m for p in THIRD_PARTY_HINTS)


def _is_our_frame(method):
    """这一帧是否属于**我们工程**（`BlBridge.*`）。

    ★ **白名单**而不是黑名单（实测教训，2026-10-07）：
      黑名单（"不是第三方就是我们的"）会把 `RBM.*` 这类**看起来不像第三方**的
      mod 类型名当成我们的 ⇒ 进而去 `REPO/src/` 找**同名文件**
      （第三方 PDB 经 pathmap 后也是 `/src/X.cs` 形状）⇒ 给出**错误源码片段**。
      实测 `SubModule.cs` 在 RBM / Bloodlust / BellumCivile / StrategicCampaignAI
      **每一个**第三方索引里都存在，而我们的主入口同名 ⇒ 这个碰撞是**必然**发生的。
    ⇒ 只有明确以 `BlBridge.` 开头的类型才算我们的；其余一律不给片段。
    """
    m = (method or "").strip()
    return m.startswith("BlBridge.") or m.startswith("BlBridge+")


def read_source_snippet(file_name, line, before=4, after=6, src_dir=None,
                        allow_foreign=False):
    """读**我们的**源码片段（带行号）。找不到就如实返回 None。

    ★★ 安全红线：只允许读**我们自己**的源码（`REPO/src/`）。

    为什么必须卡死（实测缺陷，2026-10-07）：第三方索引里的文件名会**与我们的撞车** ——
    实测 `SubModule.cs` 同时存在于 RBM / Bloodlust / BellumCivile / StrategicCampaignAI
    等**每一个**第三方程序集的索引里，而我们的主入口也叫 `SubModule.cs`。
    首版只按 basename 找 ⇒ 一条 **RBM** 的帧（`RBM.SubModule.OnSubModuleLoad`）
    会配上**我们自己的** `src/SubModule.cs` 的源码片段，行号还"看起来合理"
    ⇒ **给 agent 看错代码**，而且它无从察觉。

    ⇒ 判据：
      ① 由调用方显式声明来源（我们自己的帧才给 `allow_foreign=False`）；
      ② 这里再兜一层：只从 `REPO/src/` 取，绝不按 basename 去别处找；
      ③ 第三方帧**一律不给片段**（我们本来就没有它的源码）。
    """
    if allow_foreign:
        # 预留口子：将来若真把某个第三方源码树纳入本地，必须显式指定目录，
        # 而不是靠 basename 撞运气。
        return None
    if not file_name or not line:
        return None
    d = src_dir or SRC_DIR
    p = os.path.join(d, os.path.basename(file_name))
    if not os.path.isfile(p):
        return None
    try:
        lines = io.open(p, "r", encoding="utf-8", errors="replace").read().split("\n")
    except Exception:                                     # noqa: BLE001
        return None
    # 行号越界 ⇒ 说明这份源码**不是**那个程序集的（文件同名但内容不同）
    # ⇒ 如实返回 None，绝不截断给出一个错位置的片段。
    if int(line) > len(lines):
        return None
    lo = max(1, int(line) - before)
    hi = min(len(lines), int(line) + after)
    out = []
    for i in range(lo, hi + 1):
        mark = ">>" if i == int(line) else "  "
        out.append("%s %5d | %s" % (mark, i, lines[i - 1].rstrip("\r")))
    return {"path": p, "startLine": lo, "endLine": hi, "text": "\n".join(out)}


def analyze(stack_text=None, records=None, resolve=None, src_dir=None):
    """主入口：解析栈 → 逐帧定位 → 附源码片段。"""
    index = load_index()
    tp = _thirdparty_indexes()
    out = {
        "ok": True,
        "indexAvailable": index is not None,
        "indexMethods": (index or {}).get("methods"),
        "pdbAvailable": _pdb_available(),
        "thirdPartyIndexes": len(tp),
        "thirdPartyAssemblies": sorted(tp.keys())[:200],
        "frames": [],
        "notes": [],
    }

    if resolve:
        e = locate(resolve, index)
        if e:
            out["resolved"] = e
            out["snippet"] = read_source_snippet(e.get("file"), e.get("line"), src_dir=src_dir)
        else:
            out["resolved"] = None
            out["notes"].append(
                "符号索引里没有 `%s` ⇒ 无法定位。**不要**据此编造位置；"
                "可能是索引过期（改完源码要重新 build）或该方法来自第三方程序集。" % resolve)
        return out

    stacks = []
    if stack_text:
        stacks.append({"source": "(参数传入)", "text": stack_text})
    for r in (records or []):
        if r.get("t") != "exception":
            continue
        txt = r.get("stackFrames") or r.get("stackHead")
        if txt:
            stacks.append({"source": r.get("type") or "(未知类型)",
                           "utc": r.get("utc"), "text": txt})

    for st in stacks:
        entry = {"source": st["source"], "utc": st.get("utc"), "located": []}
        for fr in parse_stack(st["text"]):
            item = {
                "method": fr["method"],
                "fromStack": ({"file": fr["file"], "line": fr["line"]}
                              if fr["hasLine"] else None),
                "hasLineInStack": fr["hasLine"],
                "thirdParty": _is_third_party(fr["method"]),
            }
            if fr["hasLine"]:
                # 路径 A：栈里直接带着（最权威）
                item["file"] = fr["file"]
                item["line"] = fr["line"]
                item["locatedBy"] = "stack"
                # ★★ 只有在"这帧确实属于我们工程"时才去读我们的源码片段。
                #
                # 为什么（实测缺陷，2026-10-07）：第三方 PDB 经 `/pathmap` 后路径也是
                # `/src/SubModule.cs` 这种形状，而**我们的主入口也叫 SubModule.cs**。
                # 首版无条件去 `REPO/src/` 找同名文件 ⇒ 一条 **RBM** 的帧
                # （`RBM.SubModule.OnSubModuleLoad`）配上了**我们自己的**源码片段，
                # 行号还"看起来合理" ⇒ 给 agent 看错代码且无从察觉。
                # ⇒ 用**类型名所属程序集**判定：`RBM.*` 不是我们的 ⇒ 不给片段。
                item["snippet"] = (read_source_snippet(fr["file"], fr["line"], src_dir=src_dir)
                                   if _is_our_frame(fr["method"]) else None)
                if item["snippet"] is None and not _is_our_frame(fr["method"]):
                    item["sourceNote"] = ("第三方程序集 —— 行号来自其自带 PDB；"
                                          "本仓库没有它的源码，故无片段。")
            else:
                # 路径 B / C：**先判这一帧属于谁，再查对应的索引**。
                #
                # ★ 顺序是硬的（实测缺陷，2026-10-07）：
                #   若先查我们自己的索引，它的"短名兜底"会把
                #   `RBM.SubModule.OnSubModuleLoad` 匹配到
                #   `BlBridge.SubModule.OnSubModuleLoad`（**同名方法**）
                #   ⇒ 一条 RBM 的帧配上我们的 `SubModule.cs:59`，还带着我们的源码片段。
                #   这是**必然**碰撞（`OnSubModuleLoad` 是所有 mod 的入口名）。
                #   ⇒ 白名单判定优先：只有 `BlBridge.*` 才查我们的索引。
                if _is_our_frame(fr["method"]):
                    e = locate(fr["method"], index)
                else:
                    e = None
                if e:
                    item["file"] = e.get("file")
                    item["line"] = e.get("line")
                    item["endLine"] = e.get("endLine")
                    item["locatedBy"] = "symbols"
                    item["snippet"] = read_source_snippet(e.get("file"), e.get("line"),
                                                          src_dir=src_dir)
                else:
                    # 路径 C：第三方符号索引（v0.8.51）
                    #
                    # ★ 实测动机：真机 194 个栈帧里 **192 个（99%）是第三方帧** ——
                    #   只覆盖自己的代码等于没覆盖真实崩溃。第三方 mod 常自带 PDB
                    #   （实测 123 个），所以这条路是可行的。
                    tp, cands = locate_thirdparty(fr["method"])
                    if tp is not None and _line_is_plausible(tp.get("line")):
                        item["file"] = tp.get("file")
                        item["line"] = tp.get("line")
                        item["endLine"] = tp.get("endLine")
                        item["locatedBy"] = "thirdparty"
                        item["assembly"] = tp.get("assembly")
                        item["alsoIn"] = tp.get("alsoIn")
                        # 第三方源码**不在我们仓库** ⇒ 没有源码片段可给
                        # （如实标注，不要让调用方以为"片段缺失 = 工具坏了"）
                        item["snippet"] = None
                        item["sourceNote"] = ("第三方程序集 —— 行号来自其自带 PDB；"
                                              "本仓库没有它的源码，故无片段。")
                    elif cands and not any(_line_is_plausible(c.get("line"))
                                           for c in cands):
                        # ★ 有命中但行号**不可信**（实测 16707566 那种 PDB 损坏）
                        #   ⇒ 如实说"数据不可信"，**不报位置**。
                        item["locatedBy"] = None
                        item["corruptLine"] = True
                        item["reason"] = ("该程序集的 PDB 里有**不可信的行号**"
                                          "（实测出现 16707566 这类值）⇒ 不报位置。"
                                          "典型原因是 PDB 与其 DLL 并非同一版本编译产物。")
                        item["candidates"] = [
                            {"assembly": c.get("assembly"), "file": c.get("file"),
                             "line": c.get("line"), "pdb": c.get("indexPdb")}
                            for c in cands[:5]
                        ]
                    elif cands:
                        # ★ 有候选但**版本/位置不一致** ⇒ 不猜，如实报歧义
                        item["locatedBy"] = None
                        item["ambiguous"] = True
                        item["candidates"] = [
                            {"assembly": c.get("assembly"), "file": c.get("file"),
                             "line": c.get("line"), "pdb": c.get("indexPdb")}
                            for c in cands[:8]
                        ]
                        item["reason"] = ("同名方法在**多个版本**的程序集索引里位置不同"
                                          " ⇒ **不猜**（猜错会给错行号，比不给更坏）")
                    else:
                        item["locatedBy"] = None
                        # ★ 如实区分"第三方本来就没源码"与"我们有源码但索引没覆盖"
                        item["reason"] = ("第三方程序集（本来就无我们的源码）"
                                          if item["thirdParty"]
                                          else "本地符号索引未覆盖该方法（索引可能过期或未生成）")
            entry["located"].append(item)
        out["frames"].append(entry)

    if not out["indexAvailable"]:
        out["notes"].append(
            "符号索引不存在（out/BlBridge.symbols.json）⇒ 只能依赖栈里自带的行号。"
            "生成：`python tools/bl_symbols.py --build`（需 build.ps1 出 PDB）。")
    return out


def _render(res):
    L = []
    L.append("BlBridge 源码定位（栈帧 → 文件:行号）")
    L.append("=" * 74)
    L.append("符号索引 : %s（方法 %s）"
             % ("可用" if res["indexAvailable"] else "**不可用**", res.get("indexMethods")))
    L.append("第三方索引: %d 个程序集" % res.get("thirdPartyIndexes", 0))
    L.append("PDB      : %s" % ("在" if res["pdbAvailable"] else "**不在**（栈里不会有行号）"))
    for n in res.get("notes") or []:
        L.append("[i] %s" % n)
    L.append("")

    if res.get("resolved") is not None:
        e = res["resolved"]
        L.append("解析 `%s` → %s:%s-%s" % (e.get("method"), e.get("file"),
                                          e.get("line"), e.get("endLine")))
        if res.get("snippet"):
            L.append("")
            L.append(res["snippet"]["text"])
        return "\n".join(L)

    for entry in res["frames"]:
        L.append("-" * 74)
        L.append("栈来源：%s%s" % (entry["source"],
                                 ("  @ " + str(entry["utc"])) if entry.get("utc") else ""))
        for it in entry["located"]:
            tag = {"stack": "栈内行号", "symbols": "符号索引",
                   "thirdparty": "第三方PDB", None: "无法定位"}.get(
                it["locatedBy"], "?")
            L.append("  [%s] %s" % (tag, it["method"] or "(未识别方法)"))
            if it.get("file"):
                extra = ""
                if it.get("assembly"):
                    extra = "   (程序集 %s)" % it["assembly"]
                L.append("        %s:%s%s" % (it["file"], it.get("line"), extra))
                if it.get("sourceNote"):
                    L.append("        [i] %s" % it["sourceNote"])
            elif it.get("ambiguous"):
                L.append("        ⚠ %s" % it.get("reason"))
                for c in (it.get("candidates") or [])[:5]:
                    L.append("          候选: %s  %s:%s  (pdb %s)"
                             % (c.get("assembly"), c.get("file"), c.get("line"),
                                c.get("pdb")))
            else:
                L.append("        ⚠ %s" % it.get("reason", "无可定位信息"))
            if it.get("snippet"):
                for ln in it["snippet"]["text"].split("\n"):
                    L.append("     " + ln)
        L.append("")
    return "\n".join(L)


def main(argv=None):
    import argparse
    try:
        import bl_common
        bl_common.safe_streams()
    except Exception:                                     # noqa: BLE001
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="栈帧 → 源码定位（只读）")
    ap.add_argument("--stack", default=None, help="直接给一段栈文本")
    ap.add_argument("--from-exceptions", action="store_true",
                    help="读 <LogDir>\\exceptions.jsonl 逐条定位")
    ap.add_argument("--resolve", default=None, help="解析一个方法全名")
    ap.add_argument("--logDir", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    records = None
    if args.from_exceptions:
        import bl_common
        p = os.path.join(args.logDir or bl_common.default_log_dir(), "exceptions.jsonl")
        records = []
        if os.path.isfile(p):
            for line in io.open(p, "r", encoding="utf-8-sig", errors="replace"):
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if isinstance(o, dict):
                    records.append(o)

    res = analyze(stack_text=args.stack, records=records, resolve=args.resolve)
    if args.json:
        out = dict(res)
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(_render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
