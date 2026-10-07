#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
符号索引生成器（方法 → 文件:行号）—— 宿主侧，只读输入，仅标准库。

## 它解决什么

崩溃栈**只有方法名**，没有 `.cs` 文件与行号：

    at System.Security.Cryptography.CryptographicException.ThrowCryptographicException(Int32 hr)
    at HarmonyLib.PatchTools.GetOriginalMethod(HarmonyMethod attr)

⇒ agent 拿到崩溃后**无法跳到源码那一行**，「看完数据再改代码」这条路就断在第一步。

## 为什么从 PDB 取，而不是正则扫源码

C# 里"这一行属于哪个方法"**靠源码文本判不准**：

  · 嵌套类 / 局部函数 / lambda 会让行号区间重叠；
  · `async` 方法被编译器拆成状态机（`<Foo>d__0.MoveNext`），方法名与源码对不上；
  · 表达式体成员（`=> ...`）可能跨多行；
  · `#region` / 条件编译会让同一文件产生**不同版本**的行号。

而 **PDB 是编译器自己写的映射**，权威且精确。
⇒ 本工具读 PDB 的 sequence points，产出 `方法全名 → 文件:行号` 索引。

## ★ 实测依据（2026-10-07，受控实验，非推断）

| 腿 | 编译参数 | 栈里有行号？ | 行号正确？ |
|---|---|---|---|
| A | `/debug-`（**本项目现状**） | ❌ 没有 | — |
| B | `/debug:portable` | ❌ 没有 | — |
| C | **`/debug:full`** | ✅ 有 | ✅ 24（= throw 语句真实行号） |

两条**必须记住**的坑：

1. **`.NET Framework` 不认 `portable` PDB** —— 加 `/debug:portable` 白加，栈里照样没有行号。
   必须 `/debug:full`（Windows PDB）。
2. **栈文本会被本地化**：中文 Windows 是 `位置 <file>:行号 24`，
   英文才是 `in <file>:line 24`。首版判据只认英文 `":line "` ⇒ 中文系统上
   **明明有行号却判成没有**，差点把"方案不可行"写进设计。
   ⇒ 解析必须**与语言无关**（看"文件 + 冒号 + 数字"的形状，不认关键词）。

## 隐私（重要）

PDB 与栈文本都会带**编译机的绝对路径**（实测：`E:\\Document\\...`；若构建机在
`C:\\Users\\<名字>\\` 下就会泄漏用户名）。
⇒ 用 **`/pathmap:<绝对路径>=/`** 把它改写成符号路径 `/<文件名>`：
   实测行号仍准确（24），且 `HAS_ABS_PATH=False`。

## 用法

    python tools/bl_symbols.py --build            # 用 Cecil 从 out/BlBridge.pdb 抽取
    python tools/bl_symbols.py --dll <x.dll> --pdb <x.pdb>
    python tools/bl_symbols.py --check            # 只看索引是否存在/是否过期
"""
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

REPO = os.path.abspath(os.path.join(HERE, os.pardir))
OUT_DIR = os.path.join(REPO, "out")
DEFAULT_DLL = os.path.join(OUT_DIR, "BlBridge.dll")
DEFAULT_PDB = os.path.join(OUT_DIR, "BlBridge.pdb")
INDEX_NAME = "BlBridge.symbols.json"

# Cecil 所在（Bannerlord.Harmony 模块带；也可用环境变量覆盖）
HARMONY_BIN_CANDIDATES = [
    os.path.join("G:\\Program Files (x86)\\Steam\\steamapps\\common\\Mount & Blade II Bannerlord",
                 "Modules", "Bannerlord.Harmony", "bin", "Win64_Shipping_Client"),
]


def _cecil_dir():
    for p in HARMONY_BIN_CANDIDATES:
        if os.path.isfile(os.path.join(p, "Mono.Cecil.dll")):
            return p
    env = os.environ.get("BLBRIDGE_CECIL_DIR")
    if env and os.path.isfile(os.path.join(env, "Mono.Cecil.dll")):
        return env
    return None


def _csc():
    for p in (r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe",
              r"C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\Roslyn\csc.exe"):
        if os.path.isfile(p):
            return p
    return None


# 内嵌的抽取器源码：编译一次，之后直接跑（避免依赖任何 pip 包）
EXTRACTOR_CS = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Mono.Cecil;
using Mono.Cecil.Cil;

public static class Extractor
{
    // 参数: dll pdb out [mode]
    //   mode = "single"（默认，失败即退出）
    //   mode = "multi"  —— **第三方模式**：把 dll 目录下**所有** .pdb 逐个试，
    //                     只采用**校验通过**的那个（Cecil 抛 SymbolsNotMatchingException 即错配）。
    //
    // ★ 为什么必须逐个试（实测 2026-10-07）：一个目录里可能有**多个版本**的 PDB ——
    //   实测 `Bloodlust` 目录同时有 v1.4.5/v1.4.6/v1.4.7/v1.4.8/v1.5.1/v1.5.2 六套 dll+pdb。
    //   "取旁边那个 .pdb" 很可能取的**不是**当前 DLL 的 ⇒ 会给**错行号**。
    //   Cecil **自带 GUID/age 校验**（实测抛 `SymbolsNotMatchingException`），
    //   所以"逐个试 + 只收匹配的"是可靠的；但**绝不能跳过校验**。
    public static int Main(string[] args)
    {
        if (args.Length < 3) { Console.Error.WriteLine("need dll pdb out [mode]"); return 2; }
        string dll = args[0];
        string pdb = args[1];
        string outp = args[2];
        string mode = args.Length > 3 ? args[3] : "single";

        var cands = new List<string>();
        if (mode == "multi")
        {
            try
            {
                string dir = Path.GetDirectoryName(Path.GetFullPath(dll));
                foreach (string p in Directory.GetFiles(dir, "*.pdb"))
                    cands.Add(p);
                // 同名优先（最可能），其余按名字排序保证可复现
                string same = Path.ChangeExtension(dll, ".pdb");
                cands.Sort(delegate(string a, string b)
                {
                    bool sa = string.Equals(a, same, StringComparison.OrdinalIgnoreCase);
                    bool sb = string.Equals(b, same, StringComparison.OrdinalIgnoreCase);
                    if (sa != sb) return sa ? -1 : 1;
                    return string.CompareOrdinal(a, b);
                });
            }
            catch (Exception e) { Console.Error.WriteLine("scan: " + e.Message); }
        }
        if (cands.Count == 0) cands.Add(pdb);

        var tried = new List<string>();
        foreach (string cand in cands)
        {
            if (!File.Exists(cand)) { tried.Add(Path.GetFileName(cand) + "=missing"); continue; }
            try
            {
                var rp = new ReaderParameters();
                rp.ReadSymbols = true;
                rp.SymbolReaderProvider = new Mono.Cecil.Pdb.PdbReaderProvider();
                rp.SymbolStream = File.OpenRead(cand);
                ModuleDefinition mod = ModuleDefinition.ReadModule(dll, rp);
                int rc = Write(mod, outp, dll, cand);
                if (rc == 0)
                {
                    Console.WriteLine("MATCHED_PDB=" + cand);
                    return 0;
                }
                tried.Add(Path.GetFileName(cand) + "=noPoints");
            }
            catch (Exception e)
            {
                // 错配（SymbolsNotMatchingException）或坏文件 —— 都属"这个候选不能用"，继续试下一个
                tried.Add(Path.GetFileName(cand) + "=" + e.GetType().Name);
            }
        }
        Console.Error.WriteLine("no matching pdb; tried: " + string.Join(", ", tried.ToArray()));
        return 5;
    }

    private static int Write(ModuleDefinition mod, string outp, string dll, string pdb)
    {
        var rows = new List<string>();
        int methods = 0, withPts = 0;
        foreach (TypeDefinition t in mod.GetTypes())
        {
            foreach (MethodDefinition m in t.Methods)
            {
                methods++;
                if (!m.HasBody || m.DebugInformation == null || !m.DebugInformation.HasSequencePoints)
                    continue;
                withPts++;
                var pts = m.DebugInformation.SequencePoints;
                var first = pts.First();
                var last = pts.Last();
                string full = t.FullName + "." + m.Name;
                // ★ 同时保留 `file`（basename，兼容既有消费者）与 `docUrl`（PDB 原始路径）。
                //
                // 为什么留 docUrl（实测 2026-10-07）：PDB 里其实**记着完整源码路径** ——
                // 实测 RBM.pdb 内含
                //   `G:\...\Modules\RBMDev\RBM\SubModule.cs`
                // 而首版用 `Path.GetFileName` 把它截成 basename ⇒ **目录信息永久丢失**
                // ⇒ 无法按"程序集 → 源码根"定位，只能靠文件名猜（那正是撞车 bug 的根因）。
                // ⚠️ 该路径来自第三方 PDB，是**不可信输入** —— 消费侧必须做前缀校验
                //     （只允许落在游戏目录内），见 bl_source_map 的 foreign_source_root。
                rows.Add(string.Format(
                    "{{\"method\":{0},\"type\":{1},\"name\":{2},\"file\":{3},\"line\":{4},\"endLine\":{5},\"points\":{6},\"docUrl\":{7}}}",
                    J(full), J(t.FullName), J(m.Name),
                    J(Path.GetFileName(first.Document.Url)), first.StartLine, last.EndLine, pts.Count,
                    J(first.Document.Url)));
            }
        }
        if (withPts == 0) return 1;
        string asmName = Path.GetFileNameWithoutExtension(dll);
        var sb = new System.Text.StringBuilder();
        sb.Append("{\n  \"assembly\": ").Append(J(asmName))
          .Append(",\n  \"dll\": ").Append(J(Path.GetFileName(dll)))
          .Append(",\n  \"pdb\": ").Append(J(Path.GetFileName(pdb)))
          .Append(",\n  \"methods\": ").Append(methods)
          .Append(",\n  \"withSequencePoints\": ").Append(withPts)
          .Append(",\n  \"entries\": [\n");
        for (int i = 0; i < rows.Count; i++)
        {
            sb.Append("    ").Append(rows[i]);
            if (i < rows.Count - 1) sb.Append(',');
            sb.Append('\n');
        }
        sb.Append("  ]\n}\n");
        File.WriteAllText(outp, sb.ToString(), new System.Text.UTF8Encoding(false));
        Console.WriteLine("methods=" + methods + " withPoints=" + withPts);
        return 0;
    }

    private static string J(string s)
    {
        if (s == null) return "null";
        var sb = new System.Text.StringBuilder("\"");
        foreach (char c in s)
        {
            if (c == '"' || c == '\\') sb.Append('\\').Append(c);
            else if (c == '\n') sb.Append("\\n");
            else if (c == '\r') sb.Append("\\r");
            else if (c == '\t') sb.Append("\\t");
            else sb.Append(c);
        }
        return sb.Append('"').ToString();
    }
}
'''


def ensure_extractor():
    """把内嵌抽取器编译成 exe（缓存在 out/ 下）。返回 (exe, 错误)。

    ⚠️ **必须把 Cecil 的 DLL 拷到 exe 旁边**（实测踩到，2026-10-07）：
       编译期 `/r:` 引用 ≠ 运行期能加载。只给 `/r:` 的话，运行抽取器会抛
       `FileNotFoundException: Mono.Cecil ...`，而外层只看到 Windows 那句
       「由于 Exception.ToString() 失败，因此无法打印异常字符串」——**几乎无法诊断**。
       这与 `crashguardprobe` 踩的是同一个坑（那里缺的是 MonoMod 一族）。
    """
    cecil = _cecil_dir()
    if not cecil:
        return None, ("找不到 Mono.Cecil.dll（找过 %s；可用环境变量 BLBRIDGE_CECIL_DIR 指定）"
                      % HARMONY_BIN_CANDIDATES[0])
    csc = _csc()
    if not csc:
        return None, "找不到 Roslyn csc.exe"
    os.makedirs(OUT_DIR, exist_ok=True)
    src = os.path.join(OUT_DIR, "symbols_extractor.cs")
    exe = os.path.join(OUT_DIR, "symbols_extractor.exe")
    with io.open(src, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(EXTRACTOR_CS)

    # ★ 运行期依赖：拷到 exe 同目录（且只拷实际用到的）
    copied = 0
    for name in ("Mono.Cecil.dll", "Mono.Cecil.Pdb.dll", "Mono.Cecil.Rocks.dll",
                 "Mono.Cecil.Mdb.dll"):
        sp = os.path.join(cecil, name)
        if os.path.isfile(sp):
            try:
                with io.open(sp, "rb") as f1, io.open(os.path.join(OUT_DIR, name), "wb") as f2:
                    f2.write(f1.read())
                copied += 1
            except Exception:                             # noqa: BLE001
                pass
    if copied == 0:
        return None, "一个 Cecil 依赖都没拷到（%s）" % cecil

    r = subprocess.run([csc, "/nologo", "/target:exe", "/out:" + exe,
                        "/r:" + os.path.join(OUT_DIR, "Mono.Cecil.dll"),
                        "/r:" + os.path.join(OUT_DIR, "Mono.Cecil.Pdb.dll"),
                        src],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not os.path.isfile(exe):
        return None, "编译抽取器失败：\n" + (r.stdout or "") + (r.stderr or "")
    return exe, None


def build_index(dll=None, pdb=None, out=None):
    """从 PDB 抽索引。返回 (indexPath or None, info)。"""
    dll = dll or DEFAULT_DLL
    pdb = pdb or DEFAULT_PDB
    if not os.path.isfile(dll):
        return None, {"ok": False, "reason": "DLL 不存在：%s" % dll}
    if not os.path.isfile(pdb):
        return None, {"ok": False,
                      "reason": ("PDB 不存在：%s ⇒ 说明这次构建**没产 PDB**。"
                                 "请确认 build.ps1 用的是 `/debug:full`（.NET Framework "
                                 "不认 portable）" % pdb)}
    exe, err = ensure_extractor()
    if err:
        return None, {"ok": False, "reason": err}
    outp = out or os.path.join(os.path.dirname(dll), INDEX_NAME)
    r = subprocess.run([exe, dll, pdb, outp],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None, {"ok": False, "reason": "抽取失败：%s%s" % (r.stdout or "", r.stderr or "")}
    return outp, {"ok": True, "path": outp, "stats": (r.stdout or "").strip()}


def load_index(path=None):
    p = path or os.path.join(OUT_DIR, INDEX_NAME)
    if not os.path.isfile(p):
        return None
    try:
        with io.open(p, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:                                     # noqa: BLE001
        return None


# ── 第三方程序集索引（v0.8.51）────────────────────────────────────────────
#
# ## 为什么需要它（实测边界，2026-10-07）
#
# 统计真机 171 条异常 / 194 个栈帧：**只有 2 帧（1.0%）属我们工程，99% 是第三方**。
# ⇒ 「源码定位」只覆盖自己的代码是**不够的**：真实崩溃基本都发生在别的 mod 里。
#
# 好消息：**第三方 mod 常自带 PDB**（实测模块目录下 123 个，且 123/123 都有同名 DLL）。
#
# ## ★ 最大的风险：PDB 与 DLL **错配**
#
# 错配会给出**属于另一个版本的行号** —— 比"没有行号"坏得多：agent 照着错行改代码，
# 而且**看起来完全正常**（有文件、有行号）。实测发现两种真实风险：
#   ① 一个目录可能有**多个版本**的 dll+pdb 共存（`Bloodlust` 同时有 v1.4.5~v1.5.2 六套）；
#   ② 有 pdb 但没有**同名** dll（版本化命名）。
#
# ⇒ 对策：**逐个候选 PDB 试，只收校验通过的那个**。
#   实测 Mono.Cecil **自带 GUID/age 校验**，错配时抛
#   `SymbolsNotMatchingException: Symbols were found but are not matching the assembly`
#   ⇒ 由 Cecil 做**权威校验**，我们绝不"取旁边那个 pdb"。

# 第三方索引目录（放在 out/ 下，已被 .gitignore 挡掉 —— 里面含第三方路径信息）
THIRD_PARTY_DIR = os.path.join(OUT_DIR, "symbols-thirdparty")

# 单个索引的体积上限（防止某个巨型程序集把索引撑爆）
MAX_INDEX_BYTES = 40 * 1024 * 1024


def module_dirs(game_dir=None):
    """枚举游戏模块的 bin 目录（第三方 DLL/PDB 所在地）。"""
    g = game_dir or os.environ.get("BANNERLORD_DIR") or (
        r"G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord")
    mods = os.path.join(g, "Modules")
    out = []
    if not os.path.isdir(mods):
        return out
    for name in sorted(os.listdir(mods)):
        d = os.path.join(mods, name, "bin", "Win64_Shipping_Client")
        if os.path.isdir(d):
            out.append(d)
    # 游戏本体的 bin 也放进来（TaleWorlds.* 在那里，但一般不带 PDB）
    gb = os.path.join(g, "bin", "Win64_Shipping_Client")
    if os.path.isdir(gb):
        out.append(gb)
    return out


def build_thirdparty_index(game_dir=None, out_dir=None, only=None, verbose=True):
    """为**所有带 PDB 的第三方 DLL** 建索引。返回 (结果列表, 统计)。

    每个候选：逐个试该目录下的 PDB，**只收 Cecil 校验通过的**。
    """
    exe, err = ensure_extractor()
    if err:
        return [], {"ok": False, "reason": err}
    d = out_dir or THIRD_PARTY_DIR
    os.makedirs(d, exist_ok=True)

    results = []
    stats = {"dirs": 0, "dllsWithPdb": 0, "indexed": 0, "noMatch": 0, "failed": 0}

    for md in module_dirs(game_dir):
        stats["dirs"] += 1
        try:
            files = os.listdir(md)
        except OSError:
            continue
        pdbs = [f for f in files if f.lower().endswith(".pdb")]
        if not pdbs:
            continue
        dll_by_stem = {}
        for f in files:
            if f.lower().endswith(".dll"):
                dll_by_stem[os.path.splitext(f)[0]] = f

        # ① 同名配对（最常见）
        pairs = []
        for p in pdbs:
            stem = os.path.splitext(p)[0]
            if stem in dll_by_stem:
                pairs.append((dll_by_stem[stem], p))
        # ② 目录里只有一个 dll 或多个 pdb 但只有一个 dll：也试
        if not pairs and len(dll_by_stem) == 1:
            one = list(dll_by_stem.values())[0]
            for p in pdbs:
                pairs.append((one, p))
        # ③ 有 pdb 但完全没有同名 dll：只能靠"逐个 dll 试"补
        if not pairs and dll_by_stem and pdbs:
            for dll_name in dll_by_stem.values():
                pairs.append((dll_name, pdbs[0]))

        if only:
            pairs = [x for x in pairs if only.lower() in x[0].lower()]
        if not pairs:
            continue

        for dll_name, pdb_name in pairs:
            dll_path = os.path.join(md, dll_name)
            # ★ 体积守卫：用 ReadAllBytes 而不是 getsize（Vortex 硬链接下 getsize 可能不可信）
            try:
                with io.open(dll_path, "rb") as fh:
                    size = len(fh.read())
            except Exception:                             # noqa: BLE001
                size = -1
            if size <= 0:
                stats["failed"] += 1
                results.append({"dll": dll_name, "dir": md, "status": "empty-or-unreadable"})
                continue
            stats["dllsWithPdb"] += 1
            outp = os.path.join(d, os.path.splitext(dll_name)[0] + ".symbols.json")
            r = subprocess.run([exe, dll_path, os.path.join(md, pdb_name), outp, "multi"],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode == 0 and os.path.isfile(outp):
                try:
                    with io.open(outp, "rb") as fh:
                        if len(fh.read()) > MAX_INDEX_BYTES:
                            os.remove(outp)
                            stats["failed"] += 1
                            results.append({"dll": dll_name, "status": "too-large"})
                            continue
                except Exception:                         # noqa: BLE001
                    pass
                stats["indexed"] += 1
                matched = ""
                for ln in (r.stdout or "").split("\n"):
                    if ln.startswith("MATCHED_PDB="):
                        matched = os.path.basename(ln.split("=", 1)[1].strip())
                results.append({"dll": dll_name, "dir": md, "status": "ok",
                                "pdb": matched, "stats": (r.stdout or "").strip().split("\n")[0]})
                if verbose:
                    print("  [ok] %-46s <- %s" % (dll_name[:46], matched))
            else:
                stats["noMatch"] += 1
                why = "no-matching-pdb"
                if "SymbolsNotMatching" in (r.stderr or "") or "matching" in (r.stderr or ""):
                    why = "no-matching-pdb"
                results.append({"dll": dll_name, "dir": md, "status": why,
                                "detail": (r.stderr or "").strip()[:200]})
                if verbose:
                    print("  [--] %-46s %s" % (dll_name[:46], why))
    return results, stats


def load_thirdparty_indexes(dir_path=None):
    """读全部第三方索引 → {程序集名: 索引}。"""
    d = dir_path or THIRD_PARTY_DIR
    out = {}
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".symbols.json"):
            continue
        idx = load_index(os.path.join(d, fn))
        if not idx:
            continue
        asm = idx.get("assembly") or os.path.splitext(fn)[0]
        out[asm.lower()] = idx
    return out


# ── 游戏版本消歧（v0.8.51）──────────────────────────────────────────────
#
# ## 为什么需要它（实测依据，2026-10-07）
#
# 很多 mod 的 bin 目录里**同时放了 42 个版本**的 dll+pdb。实测确认那串版本号是
# **游戏版本**（供 `Bannerlord.ModuleLoader` 按 `LoaderFilter` 挑），**不是** mod 版本：
#   · `Bannerlord.ButterLib.Implementation.1.4.8.dll` 等各版本的 `FileVersion`
#     **全都是同一个 mod 版本**（如 5.12.3.0）；
#   · 同一个方法在不同版本索引里**行号不同**（实测 144 vs 149、160 vs 165）。
#
# ⇒ 若把所有版本平铺成候选，会得出"歧义"而**拒绝定位** —— 实测 **18.3%** 的真实方法
#   就是这样被拒掉的。但真实栈帧只可能来自**被加载的那一个版本**（= 本机游戏版本）。
# ⇒ 用游戏版本**消歧**：把"不确定"变成"可确定"。
#
# ⚠️ 边界（不许夸大）：本机游戏版本只能**缩小**候选，不能证明"模块加载器一定选了它"。
#    所以消歧只在**候选确实收窄**时生效；仍多于一个时照旧**不猜**。


def _norm_game_version(v):
    """`v1.4.8.119303` / `1.4.8` → `1.4.8`（只保留前三段）。"""
    s = (v or "").strip().lstrip("vV")
    parts = [x for x in s.split(".") if x.isdigit()]
    return ".".join(parts[:3]) if parts else None


def game_version(game_dir=None):
    """读本机游戏版本（形如 `1.4.8`）。取不到返回 None（**不猜**）。

    来源优先级：
      ① `<game>/Modules/Native/SubModule.xml` 的 `<Version value="..."/>`；
      ② `LauncherData.xml` 里 `Native` 的 `LastKnownVersion`。
    """
    g = game_dir or os.environ.get("BANNERLORD_DIR") or (
        r"G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord")
    # ① Native 模块
    try:
        import xml.etree.ElementTree as ET
        p = os.path.join(g, "Modules", "Native", "SubModule.xml")
        if os.path.isfile(p):
            root = ET.parse(p).getroot()
            for el in root.iter("Version"):
                v = (el.get("value") or el.text or "").strip()
                if v:
                    nv = _norm_game_version(v)
                    if nv:
                        return nv
    except Exception:                                     # noqa: BLE001
        pass
    # ② LauncherData.xml 的 Native 条目
    try:
        import re as _re
        docs = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"),
                            "Documents", "Mount and Blade II Bannerlord")
        p = os.path.join(docs, "Configs", "LauncherData.xml")
        if os.path.isfile(p):
            with io.open(p, "r", encoding="utf-8-sig", errors="replace") as fh:
                txt = fh.read()
            m = _re.search(r"<Id>\s*Native\s*</Id>\s*<LastKnownVersion>\s*([^<]+?)\s*"
                           r"</LastKnownVersion>", txt)
            if m:
                return _norm_game_version(m.group(1))
    except Exception:                                     # noqa: BLE001
        pass
    return None


def preferred_index_names(indexes, gv=None):
    """按游戏版本挑"首选索引名" → {程序集 stem: 索引名}。

    ## ★ 实测两种命名形态都要支持（2026-10-07）

    同一个仓库里，不同 mod 的版本化命名**不一致**：

    | 实际文件名 | 形态 |
    |---|---|
    | `Bannerlord.ButterLib.Implementation.1.4.8.dll` | 点号 + `1.4.8` |
    | `Bannerlord.MBOptionScreen.v1.4.0.dll` | **`v` 前缀** + `1.4.0` |

    首版只认前者 ⇒ MCM 那 11 个版本**一个都没消歧**，仍以"多版本歧义"被拒
    （实测：76.6% 覆盖率的提升几乎全被这一条吃掉）。

    ⇒ stem 的算法要**同时剥掉 `v` 前缀**：`...mboptionscreen.v1.4.0` → stem `...mboptionscreen`，
    这样它才能与 `...mboptionscreen.v1.4.8`（同 stem 的另一版本）落到同一个键上。
    """
    gv = gv if gv is not None else game_version()
    if not gv:
        return {}, {}
    # 备选后缀：`.1.4.8`（点号）与 `.v1.4.8`（v 前缀）
    suffixes = ["." + gv, ".v" + gv]
    pref = {}
    for asm in indexes:
        for suf in suffixes:
            if asm.endswith(suf):
                stem = asm[: -len(suf)]
                pref[stem] = asm
                break
    dropped = {}
    for asm in indexes:
        head, _dot, tail = asm.rpartition(".")
        # 形如 `xxx.<数字>` 或 `xxx.v<数字>` 的其他版本 → 记为已排除
        t = tail[1:] if tail.startswith("v") else tail
        if t[:1].isdigit() and head in pref:
            dropped[asm] = pref[head]
    return pref, dropped


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

    ap = argparse.ArgumentParser(description="符号索引生成（方法 → 文件:行号，从 PDB 取）")
    ap.add_argument("--dll", default=None)
    ap.add_argument("--pdb", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--build", action="store_true",
                    help="从 out/BlBridge.dll + .pdb 抽索引（等价于不带 --check 的默认行为）")
    ap.add_argument("--third-party", action="store_true",
                    help="为所有带 PDB 的第三方 DLL 建索引（out/symbols-thirdparty/）")
    ap.add_argument("--only", default=None, help="只处理名字含该子串的 DLL")
    ap.add_argument("--game", default=None, help="游戏根目录（默认自动/环境变量）")
    ap.add_argument("--check", action="store_true", help="只报索引状态")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.third_party:
        print("为第三方程序集建符号索引（逐个候选 PDB 试，只收校验通过的）")
        print("=" * 74)
        res, st = build_thirdparty_index(game_dir=args.game, only=args.only)
        print()
        print("目录 %d 个 | 带 PDB 的 DLL %d 个 | 成功 %d | 无匹配 PDB %d | 失败 %d"
              % (st["dirs"], st["dllsWithPdb"], st["indexed"], st["noMatch"], st["failed"]))
        print("索引目录：%s" % THIRD_PARTY_DIR)
        return 0 if st["indexed"] > 0 else 1

    if args.check:
        idx = load_index()
        d = os.path.isfile(DEFAULT_DLL)
        p = os.path.isfile(DEFAULT_PDB)
        print("DLL 存在      : %s" % d)
        print("PDB 存在      : %s" % p)
        print("索引存在      : %s" % (idx is not None))
        if idx:
            print("方法数        : %s" % idx.get("methods"))
            print("有序列点的    : %s" % idx.get("withSequencePoints"))
        tp = load_thirdparty_indexes()
        print("第三方索引    : %d 个程序集" % len(tp))
        return 0 if (d and p and idx) else 1

    path, info = build_index(dll=args.dll, pdb=args.pdb, out=args.out)
    if not info.get("ok"):
        print("失败：%s" % info.get("reason"))
        return 1
    print("索引已写出：%s" % path)
    print("  %s" % info.get("stats"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
