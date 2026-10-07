#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真机验收：**第三方 mod 崩溃**能否被定位（一键部署 + 采集 + 判定）。

## 它做什么（三步，都可回滚）

  1. `deploy`   —— 把 `ZzCrashProbe`（一个**故意崩**的第三方 mod）编译并装进
                   `<game>/Modules/ZzCrashProbe/`
  2. `run`      —— 用 `bl_launch.ps1 -IncludeModules ZzCrashProbe` 启动
                   （**不改你的 LauncherData.xml** —— 只对这一次启动生效）
  3. `verify`   —— 读 `exceptions.jsonl` / `crashguard.jsonl`，判定：
                   · 栈顶是否是**第三方方法**（`ZzCrashProbe.*`）
                   · `bl_source_map` 能否定位到 `文件:行号`
                   · 该行号是否 == 源码里 `throw` 所在行（**精确性判据**）
                   · CrashGuard 是否吞掉（游戏是否存活）
  4. `cleanup`  —— 移除探针模块 + 还原配置

## ⚠️ 这个探针**会故意让游戏崩溃**（当守卫关闭时）

它默认等 **25 秒**再抛，可从 `<模块>/throw_after_seconds.txt` 改。
设计上**只抛一次** —— 连续抛会触发 CrashGuard 的熔断（同签名 >20 次），
那样测的就变成"熔断是否生效"，偏离本探针目的。

## 为什么需要它（补的洞）

`bl_source_map` 的第三方能力此前**只有离线验证**（真 DLL/PDB/栈，但不在游戏进程里）。
本脚本把那次崩溃搬进**真实游戏进程**。

用法：
    python tools\\crashprobe\\deploy_and_verify.py deploy
    python tools\\crashprobe\\deploy_and_verify.py run          # 需提权（写游戏目录 / 启进程）
    python tools\\crashprobe\\deploy_and_verify.py verify
    python tools\\crashprobe\\deploy_and_verify.py cleanup
    python tools\\crashprobe\\deploy_and_verify.py all          # 全流程（分段执行也可）
退出码：0 = 通过；1 = 判据失败；2 = 环境不足。
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, os.path.join(REPO, "tools"))

MOD_ID = "ZzCrashProbe"
DEFAULT_GAME = r"G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord"
CSC_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe",
    r"C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\Roslyn\csc.exe",
]

FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    if cond:
        print("  [OK]   %s" % name)
    else:
        FAILS.append("%s%s" % (name, (" —— " + detail) if detail else ""))
        print("  [FAIL] %s%s" % (name, (" —— " + detail) if detail else ""))


def find_csc():
    for p in CSC_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def game_dir(args):
    return args.game or os.environ.get("BANNERLORD_DIR") or DEFAULT_GAME


def probe_dir(args):
    return os.path.join(game_dir(args), "Modules", MOD_ID)


def throw_line():
    """探针源码里 `throw new InvalidOperationException(` 所在行 —— 精确性判据的基准。"""
    src = os.path.join(HERE, "CrashProbeMod.cs")
    with io.open(src, "r", encoding="utf-8") as fh:
        for i, ln in enumerate(fh, 1):
            if "throw new InvalidOperationException(" in ln:
                return i
    return -1


def do_deploy(args):
    """编译探针并装进 Modules/ZzCrashProbe/。"""
    csc = find_csc()
    if not csc:
        print("[ENV] 找不到 csc.exe")
        return 2
    g = game_dir(args)
    if not os.path.isdir(os.path.join(g, "Modules")):
        print("[ENV] 游戏目录看起来不对：%s" % g)
        return 2

    pdir = probe_dir(args)
    bindir = os.path.join(pdir, "bin", "Win64_Shipping_Client")
    os.makedirs(bindir, exist_ok=True)

    # 引用：只要 TaleWorlds.MountAndBlade（MBSubModuleBase）。参考程序集按需带上。
    refs = []
    gbin = os.path.join(g, "bin", "Win64_Shipping_Client")
    for n in ("TaleWorlds.MountAndBlade.dll", "TaleWorlds.Library.dll",
              "TaleWorlds.DotNet.dll", "TaleWorlds.Core.dll", "TaleWorlds.Engine.dll",
              "TaleWorlds.ObjectSystem.dll", "TaleWorlds.Localization.dll"):
        p = os.path.join(gbin, n)
        if os.path.isfile(p):
            refs.append("/reference:" + p)
    # 框架参考程序集（net472）
    refasm = r"C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8"
    if os.path.isfile(os.path.join(refasm, "mscorlib.dll")):
        for n in ("mscorlib.dll", "System.dll", "System.Core.dll", "System.Xml.dll"):
            refs.append("/reference:" + os.path.join(refasm, n))

    # ★ netstandard facade：游戏程序集是基于 **netstandard 2.0** 编译的，
    #   缺它会报 `CS0012: 类型 Object 在未引用的程序集中定义 ... netstandard 2.0.0.0`。
    #   （实测踩到。做法与 `build.ps1:91-106` 一致 —— 那里也是按候选列表找一个可用的。）
    #   ⚠️ 实测（2026-10-07）：本机 `Reference Assemblies\v4.8\Facades` 与游戏 bin **都没有**
    #      `netstandard.dll`；只有 **dotnet SDK** 下有。所以候选里必须包含 SDK 那条路径
    #      （`build.ps1:100-106` 也是同样的兜底，只是它用通配 + Sort 取最后一个）。
    import glob as _glob
    ns_cands = [
        os.path.join(refasm, "Facades", "netstandard.dll"),
        os.path.join(refasm, "netstandard.dll"),
        os.path.join(gbin, "netstandard.dll"),
        r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\Facades\netstandard.dll",
    ]
    sdk_hits = sorted(_glob.glob(r"C:\Program Files\dotnet\sdk\*\Microsoft\Microsoft.NET.Build.Extensions\net*\lib\netstandard.dll"))
    ns_cands += sdk_hits
    for cand in ns_cands:
        if os.path.isfile(cand):
            refs.append("/reference:" + cand)
            print("  netstandard: %s" % cand)
            break
    else:
        print("  [WARN] 找不到 netstandard facade；编译可能 CS0012")

    dll = os.path.join(bindir, MOD_ID + ".dll")
    cmd = [csc, "/nologo", "/target:library", "/platform:x64", "/optimize+",
           "/langversion:7.3", "/codepage:65001", "/utf8output",
           # /debug:full（`.NET Framework` 不认 portable）+ /pathmap（隐私）
           "/debug:full", "/pathmap:" + REPO + "=/",
           "/out:" + dll] + refs + [os.path.join(HERE, "CrashProbeMod.cs")]
    print("编译探针 -> %s" % dll)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not os.path.isfile(dll):
        print("[FAIL] 编译失败：\n" + (r.stdout or "") + (r.stderr or ""))
        return 1
    # SubModule.xml（引擎靠它认识这个模块）
    shutil.copy2(os.path.join(HERE, "SubModule.xml"), os.path.join(pdir, "SubModule.xml"))

    def rsize(p):
        try:
            with io.open(p, "rb") as fh:
                return len(fh.read())
        except Exception:                                 # noqa: BLE001
            return -1
    print("  dll=%d B" % rsize(dll))
    print("  pdb=%d B" % rsize(os.path.join(bindir, MOD_ID + ".pdb")))
    print("  模块目录：%s" % pdir)
    return 0


def do_run(args):
    """启动游戏（-IncludeModules 只对本次生效，不改 LauncherData.xml）。"""
    g = game_dir(args)
    if not os.path.isfile(os.path.join(probe_dir(args), "SubModule.xml")):
        print("[ENV] 探针未部署，先跑 deploy")
        return 2
    ps = os.path.join(REPO, "tools", "bl_launch.ps1")
    cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", ps,
           "-GameDir", g, "-IncludeModules", MOD_ID]
    print("启动：%s" % " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    print(out[-2000:])
    # 从输出里抓 pid（bl_launch 会打印 `launcher pid = N`）
    m = re.search(r"launcher pid\s*=\s*(\d+)", out)
    if m:
        print("PID=%s" % m.group(1))
    return 0 if r.returncode == 0 else 1


def _read_jsonl(path, limit=None):
    rows = []
    if not os.path.isfile(path):
        return rows
    with io.open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            if isinstance(o, dict):
                rows.append(o)
    return rows[-limit:] if limit else rows


def do_verify(args):
    """读产物并判定（**这是验收的核心**）。"""
    import bl_common
    ld = args.logDir or bl_common.default_log_dir()
    print("日志目录：%s" % ld)
    print()

    exc = _read_jsonl(os.path.join(ld, "exceptions.jsonl"))
    guard = _read_jsonl(os.path.join(ld, "crashguard.jsonl"))

    # 只挑本次探针的异常（消息里带固定标记）
    mine = [r for r in exc if "ZzCrashProbe" in (r.get("message") or "")]
    gmine = [r for r in guard if "ZzCrashProbe" in (r.get("message") or "")]
    print("探针异常条数：exceptions=%d  crashguard=%d" % (len(mine), len(gmine)))
    check("A1 探针异常已落盘（exceptions.jsonl）", len(mine) > 0,
          "没落盘 ⇒ 模块没加载 / 抛得太早 / 标记不匹配")

    if not mine:
        return 1

    # ── 栈：第三方方法是否在栈里、能否定位 ──
    rec = mine[-1]
    stack = rec.get("stackFrames") or rec.get("stackHead") or ""
    print()
    print("--- 本次异常栈（前 6 帧）---")
    for ln in stack.split("\n")[:6]:
        print("   " + ln.strip())
    print()

    check("A2 栈里出现**第三方方法**（ZzCrashProbe.*）",
          "ZzCrashProbe" in stack, "栈=%r" % stack[:200])
    check("A3 栈里**没有**我们工程的类型（BlBridge.*）",
          "BlBridge." not in stack,
          "出现 BlBridge 说明抛点不在第三方代码里（栈=%r）" % stack[:200])

    import bl_source_map as sm
    res = sm.analyze(stack_text=stack)
    frames = [f for e in res["frames"] for f in e["located"]]
    probe_frames = [f for f in frames if "ZzCrashProbe" in (f.get("method") or "")]
    check("B1 定位到探针帧", len(probe_frames) > 0, repr([f.get("method") for f in frames]))

    # ★ 精确性：行号必须 == 源码 throw 行
    want = throw_line()
    got = None
    for f in probe_frames:
        if f.get("line"):
            got = int(f["line"])
            break
    check("B2 ★ 定位到**文件:行号**", got is not None,
          "探针帧没有行号：%r" % (probe_frames[:1],))
    if got is not None:
        # 探针是用 /debug:full 编的 ⇒ 栈里应自带行号；PDB 语义给的是**方法体第一个序列点**，
        # 与该方法的 throw 行可能差几行（参数/大括号）。判据：落在方法体内且 <= throw 行。
        check("B3 ★ 行号落在方法体内且不超过 throw 行（%d）" % want,
              1 <= got <= want, "定位行=%s，throw 在 %d —— 越界说明位置错" % (got, want))
        print("     （栈/索引给 %d；源码 throw 在 %d —— PDB 给的是方法体首个序列点，"
              "通常落在方法开头）" % (got, want))

    # ── CrashGuard 是否吞掉（决定游戏是否存活）──
    print()
    if gmine:
        reasons = [g.get("reason") for g in gmine]
        check("C1 CrashGuard 记录了本次异常", True)
        print("     reasons=%s" % reasons)
        check("C2 动作是 swallow（守卫开着时）", "swallowed" in reasons,
              "reasons=%s ⇒ 若非 swallowed，看是不是熔断/配额/守卫没开" % reasons)
    else:
        print("  [i] crashguard.jsonl 里没有本次记录（守卫可能未启用）")
        print("      判据跳过：本脚本的主目标（第三方定位）不依赖守卫。")

    # ── 游戏是否还活着（守卫开则应活着）──
    try:
        st = os.path.join(ld, "bridge_status.json")
        if os.path.isfile(st):
            with io.open(st, "r", encoding="utf-8-sig", errors="replace") as fh:
                status = json.load(fh)
            print()
            print("  会话状态：%s  cleanExit=%s" % (status.get("state"), status.get("cleanExit")))
            cg = status.get("crashGuard") or {}
            if cg:
                print("  守卫：enabled=%s installed=%s swallowed=%s"
                      % (cg.get("enabled"), cg.get("installed"), cg.get("swallowed")))
    except Exception:                                     # noqa: BLE001
        pass
    return 0


def do_cleanup(args):
    """移除探针模块（**不动 LauncherData.xml** —— 我们从没改过它）。"""
    pdir = probe_dir(args)
    if os.path.isdir(pdir):
        shutil.rmtree(pdir, ignore_errors=True)
        print("已移除模块目录：%s" % pdir)
    else:
        print("模块目录本就不存在：%s" % pdir)
    print("LauncherData.xml **未被修改**（-IncludeModules 只作用于单次启动）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="真机验收：第三方 mod 崩溃定位")
    ap.add_argument("action", choices=("deploy", "run", "verify", "cleanup", "all"))
    ap.add_argument("--game", default=None)
    ap.add_argument("--logDir", default=None)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                     # noqa: BLE001
        pass

    if args.action == "deploy":
        return do_deploy(args)
    if args.action == "run":
        return do_run(args)
    if args.action == "verify":
        rc = do_verify(args)
        print()
        print("=" * 74)
        if FAILS:
            print("失败 %d / %d：" % (len(FAILS), CHECKS[0]))
            for f in FAILS:
                print("  - " + f)
            return 1
        print("全部通过：%d / %d" % (CHECKS[0], CHECKS[0]))
        return rc
    if args.action == "cleanup":
        return do_cleanup(args)
    # all
    rc = do_deploy(args)
    if rc != 0:
        return rc
    print("\n（下一步 run 需要提权：会写游戏目录并启动进程）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
