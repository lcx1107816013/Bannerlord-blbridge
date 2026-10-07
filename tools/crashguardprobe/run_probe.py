#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finalizer 语义实验的执行入口（宿主侧，仅标准库，**不需要游戏**）。

## 为什么要有它

`src/CrashGuard.cs` 的全部行为押在两条 Harmony 语义假设上：

  ① Finalizer 参数名写成 `Exception __exception` 能否**绑定到被抛的异常**；
  ② Finalizer **返回 null** 是否真能**吞掉**异常；返回 `__exception` 是否真能**放行**。

这两条**不能靠记忆或文档断言**（本仓库纪律：没有对照组的验证不是验证，只是自证）。
`tools/crashguardprobe/FinalizerSemanticsProbe.cs` 用**真实 0Harmony.dll** 跑受控实验，
本脚本负责把"编译 → 凑齐依赖 → 运行 → 判定"这条链固化成**一条可复现的命令**。

## ★ 为什么必须"凑齐依赖"（实测踩到，不是理论顾虑）

首次运行时实验**直接崩掉**，Windows 只给一句
「由于 Exception.ToString() 失败，因此无法打印异常字符串。」—— **什么都学不到**。
逐层诊断后是两级依赖缺失：

  1. `0Harmony.dll` 本身（编译期引用 ≠ 运行期可加载）；
  2. `MonoMod.Backports` / `MonoMod.Core` / `MonoCecil...` —— Harmony **2.4.2 的新依赖**。

⇒ 所以本脚本把 `Modules/Bannerlord.Harmony/bin/Win64_Shipping_Client/*.dll`
   整份拷到工作目录，而不是只拷 `0Harmony.dll`。

## ⚠️ Vortex 陷阱（照 AGENTS.md 记过的同一条）

该模块目录下 `Get-Item.Length` **全部报 0**（Vortex 硬链接）。
判定"依赖是否真的拷过来了"必须用 `ReadAllBytes().Length`。
本脚本用后者。

## 用法

    python tools/crashguardprobe/run_probe.py
    python tools/crashguardprobe/run_probe.py --keep    # 保留工作目录以便人工查看

退出码：0 = 全部判据通过；1 = 有判据失败（含"编译失败"）；2 = 环境不足（找不到 csc / Harmony）。
"""
import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))

DEFAULT_GAME = r"G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord"
CSC_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe",
    r"C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\Roslyn\csc.exe",
]

PROBE_SRC = os.path.join(HERE, "FinalizerSemanticsProbe.cs")


def find_csc():
    for p in CSC_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def harmony_dir(game_dir):
    return os.path.join(game_dir, "Modules", "Bannerlord.Harmony", "bin", "Win64_Shipping_Client")


def real_size(path):
    """★ 用 ReadAllBytes 而不是 os.path.getsize —— 后者在 Vortex 硬链接下可能不可信。"""
    try:
        with io.open(path, "rb") as fh:
            return len(fh.read())
    except Exception:
        return -1


def main():
    ap = argparse.ArgumentParser(description="Finalizer 语义实验执行入口")
    ap.add_argument("--game", default=os.environ.get("BANNERLORD_DIR", DEFAULT_GAME))
    ap.add_argument("--keep", action="store_true", help="保留工作目录")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                       # noqa: BLE001
        pass

    print("CrashGuard Finalizer 语义实验")
    print("=" * 74)

    csc = find_csc()
    if not csc:
        print("[ENV] 找不到 Roslyn 编译器（csc.exe）")
        return 2
    hdir = harmony_dir(args.game)
    h0 = os.path.join(hdir, "0Harmony.dll")
    if not os.path.isfile(h0):
        print("[ENV] 找不到 %s" % h0)
        print("      （用 --game 指向游戏根目录，或设 BANNERLORD_DIR）")
        return 2
    if not os.path.isfile(PROBE_SRC):
        print("[ENV] 找不到实验源码 %s" % PROBE_SRC)
        return 2

    work = tempfile.mkdtemp(prefix="crashguard_probe_")
    try:
        # ── 1) 凑齐依赖（整份拷，不只拷 0Harmony）──
        copied = 0
        for fn in sorted(os.listdir(hdir)):
            if not fn.lower().endswith(".dll"):
                continue
            src = os.path.join(hdir, fn)
            if real_size(src) <= 0:                          # 占位/链接损坏，跳过并如实报
                print("  [skip] %s 实际字节数 <= 0（Vortex 占位？）" % fn)
                continue
            shutil.copy2(src, os.path.join(work, fn))
            copied += 1
        print("依赖 DLL 拷入 %d 个 -> %s" % (copied, work))
        if copied == 0:
            print("[ENV] 一个依赖都没拷到 —— 环境不足")
            return 2

        # ── 2) 编译 ──
        exe = os.path.join(work, "probe.exe")
        cmd = [csc, "/nologo", "/target:exe", "/out:" + exe,
               "/r:" + os.path.join(work, "0Harmony.dll"), PROBE_SRC]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0 or not os.path.isfile(exe):
            print("[FAIL] 编译失败：")
            print((r.stdout or "") + (r.stderr or ""))
            return 1
        print("编译 OK")

        # ── 3) 运行 ──
        print("-" * 74)
        r2 = subprocess.run([exe], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", cwd=work)
        out = (r2.stdout or "") + (r2.stderr or "")
        print(out.rstrip())
        print("-" * 74)

        # ── 4) 判定（既看退出码，也数判据行 —— 防"零输出假通过"）──
        n_ok = out.count("[OK]")
        n_fail = out.count("[FAIL]")
        if r2.returncode == 0 and n_ok > 0 and n_fail == 0:
            print("结果: 全部通过（%d 条判据）" % n_ok)
            return 0
        print("结果: 未通过（exit=%s, [OK]=%d, [FAIL]=%d）" % (r2.returncode, n_ok, n_fail))
        if n_ok == 0 and n_fail == 0:
            print("  [!] **一条判据都没跑出来** —— 这不是通过，是实验没生效（多半是依赖缺失）。")
        return 1
    finally:
        if args.keep:
            print("工作目录保留: %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
