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
    public static int Main(string[] args)
    {
        if (args.Length < 2) { Console.Error.WriteLine("need dll pdb out"); return 2; }
        string dll = args[0], pdb = args[1], outp = args[2];
        var rp = new ReaderParameters();
        rp.ReadSymbols = true;
        rp.SymbolReaderProvider = new Mono.Cecil.Pdb.PdbReaderProvider();
        try { rp.SymbolStream = File.OpenRead(pdb); }
        catch (Exception e) { Console.Error.WriteLine("open pdb: " + e.Message); return 3; }

        ModuleDefinition mod;
        try { mod = ModuleDefinition.ReadModule(dll, rp); }
        catch (Exception e) { Console.Error.WriteLine("read: " + e.GetType().Name + ": " + e.Message); return 4; }

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
                rows.Add(string.Format(
                    "{{\"method\":{0},\"type\":{1},\"name\":{2},\"file\":{3},\"line\":{4},\"endLine\":{5},\"points\":{6}}}",
                    J(full), J(t.FullName), J(m.Name),
                    J(Path.GetFileName(first.Document.Url)), first.StartLine, last.EndLine, pts.Count));
            }
        }
        var sb = new System.Text.StringBuilder();
        sb.Append("{\n  \"methods\": ").Append(methods)
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
    ap.add_argument("--check", action="store_true", help="只报索引状态")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

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
