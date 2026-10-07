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


def locate(method, index=None):
    """用符号索引把**方法全名**映射到 文件:行号。返回 dict 或 None。"""
    if not method:
        return None
    idx = index if index is not None else load_index()
    if not idx:
        return None
    entries = idx.get("entries") or []
    # 精确 → 去括号后缀 → 短名匹配（索引里方法名是 `Type.Method`）
    want = method.strip()
    for e in entries:
        if e.get("method") == want:
            return e
    # 栈里可能带 `Type.Method(...)` 的参数（已被 parse_frame 去掉），这里再兜一层
    short = want.split("(")[0]
    for e in entries:
        if e.get("method") == short:
            return e
    # 只给"类型.方法"尾巴匹配（栈里常带命名空间全名）
    tail = short.split(".")[-1]
    cands = [e for e in entries if (e.get("name") or "") == tail]
    if len(cands) == 1:
        return cands[0]
    return None


def _is_third_party(method):
    m = (method or "").lower()
    return any(m.startswith(p) or ("." + p) in m for p in THIRD_PARTY_HINTS)


def read_source_snippet(file_name, line, before=4, after=6, src_dir=None):
    """读**我们的**源码片段（带行号）。找不到就如实返回 None。"""
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
    out = {
        "ok": True,
        "indexAvailable": index is not None,
        "indexMethods": (index or {}).get("methods"),
        "pdbAvailable": _pdb_available(),
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
                item["snippet"] = read_source_snippet(fr["file"], fr["line"], src_dir=src_dir)
            else:
                # 路径 B：查符号索引
                e = locate(fr["method"], index)
                if e:
                    item["file"] = e.get("file")
                    item["line"] = e.get("line")
                    item["endLine"] = e.get("endLine")
                    item["locatedBy"] = "symbols"
                    item["snippet"] = read_source_snippet(e.get("file"), e.get("line"),
                                                          src_dir=src_dir)
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
            tag = {"stack": "栈内行号", "symbols": "符号索引", None: "无法定位"}.get(
                it["locatedBy"], "?")
            L.append("  [%s] %s" % (tag, it["method"] or "(未识别方法)"))
            if it.get("file"):
                L.append("        %s:%s" % (it["file"], it.get("line")))
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
