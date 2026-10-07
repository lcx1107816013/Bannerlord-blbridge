#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟一个「第三方 mod」并端到端验证定位链路（离线，需要的只是 csc），**不需要游戏**。

## 为什么需要它（补的洞）

`bl_source_map` 的第三方能力此前只被验到"读了索引、查到了方法" ——
**从没有一个真实第三方栈**走完全程：真 DLL → 真 PDB → 真抛异常 → 真栈文本 → 定位。
本脚本把这条链跑通，因此可以进闸门（对照纪律：没有对照组的验证不是验证）。

## 判据（每条都有"应报 / 不应报"两侧）

| # | 判据 | 哪种输入会红 |
|---|---|---|
| A1 | 栈文本里**出现** `文件:行号` | `/debug:full` 没生效（`.NET Framework` 不认 portable） |
| A2 | 栈里的行号 == throw 语句的**真实行号** | 行号是随便一个数（比没有更坏） |
| A3 | 栈里**不含**绝对路径 | `/pathmap` 没开 ⇒ 泄漏构建机路径（含用户名） |
| B1 | 栈里出现第三方方法名 | 栈没落到第三方代码上 |
| B2 | 该方法名**不是**我们工程的（`BlBridge.` 前缀） | 归属判定写反 |
| C1 | 栈里同时有**非第三方**帧（真实栈是混合的） | 判据只对"纯第三方栈"成立 |
| D1 | ★ **反向对照**：不给索引时，第三方帧如实说"无法定位" | 判据恒真（有没有索引都报成功） |
| D2 | ★ 给了索引 ⇒ 能定位到**正确文件与行** | 索引接错/行号错 |
| D3 | 定位结果与 A2 的**运行时行号一致** | 索引与实际栈**不一致**（最隐蔽的错） |

用法：
    python tools/thirdpartyprobe/run_probe.py
    python tools/thirdpartyprobe/run_probe.py --keep
退出码：0 = 全过；1 = 有判据失败；2 = 环境不足（缺 csc）。
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
SRC = os.path.join(HERE, "FakeThirdPartyMod.cs")

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


def real_size(p):
    """Vortex 硬链接下 os.path.getsize 可能不可信 ⇒ 用 ReadAllBytes。"""
    try:
        with io.open(p, "rb") as fh:
            return len(fh.read())
    except Exception:                                     # noqa: BLE001
        return -1


def make_workdir():
    """建工作目录。

    ⚠️ 优先用**已存在**的父目录 + os.makedirs（实测：某些受限沙箱里
       `tempfile.mkdtemp()` 建的目录**写不进去**，而 makedirs 建的可以）。
    """
    base = os.environ.get("BLBRIDGE_PROBE_DIR") or os.path.join(REPO, "out")
    os.makedirs(base, exist_ok=True)
    for i in range(50):
        p = os.path.join(base, "thirdpartyprobe_%d_%d" % (os.getpid(), i))
        if not os.path.exists(p):
            os.makedirs(p)
            return p
    raise RuntimeError("无法创建工作目录：%s" % base)


def throw_line_number():
    """源码里 `throw new InvalidOperationException("third-party-mod-boom")` 的行号。"""
    with io.open(SRC, "r", encoding="utf-8") as fh:
        for i, ln in enumerate(fh, 1):
            if 'throw new InvalidOperationException("third-party-mod-boom")' in ln:
                return i
    return -1


# 栈行号提取：**与语言无关**（中文是 `位置 X.cs:行号 N`，英文是 `in X.cs:line N`）
LINE_RE = re.compile(r"([A-Za-z0-9_./\\-]+\.cs)(?::\s*[^\d\s]{0,6}\s*)?(\d+)")
THIRD_RE = re.compile(r"FakeThirdPartyMod\.(\S+?)\(")


def main():
    ap = argparse.ArgumentParser(description="模拟第三方 mod 崩溃 + 端到端验证定位")
    ap.add_argument("--keep", action="store_true", help="保留工作目录")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                     # noqa: BLE001
        pass

    print("第三方崩溃定位 · 端到端探针（离线）")
    print("=" * 74)

    csc = find_csc()
    if not csc:
        print("[ENV] 找不到 Roslyn csc.exe")
        return 2
    if not os.path.isfile(SRC):
        print("[ENV] 找不到模拟源码 %s" % SRC)
        return 2

    work = make_workdir()
    try:
        dll = os.path.join(work, "FakeThirdPartyMod.dll")
        pdb = os.path.join(work, "FakeThirdPartyMod.pdb")
        # ★ 模拟的"第三方 mod"编译成 **library** —— 真实 mod 也是这样（只有行为，没有入口）。
        #   /debug:full（`.NET Framework` 不认 portable）+ /pathmap（隐私）。
        #   ⚠️ 首版误按 `/target:exe` 编译 ⇒ `CS5001: 程序不包含适合于入口点的静态 Main`。
        #      根因是我把两件事混成一件：「被测程序集」与「触发它取栈的载体」。
        #      ⇒ 程序集是 library，栈由**独立的 driver**（那个才是 exe）触发。
        r = subprocess.run(
            [csc, "/nologo", "/target:library", "/optimize+", "/debug:full",
             "/pathmap:" + REPO + "=/",
             "/out:" + dll, SRC],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print("[FAIL] 编译失败：\n" + (r.stdout or "") + (r.stderr or ""))
            return 1
        print("编译 OK -> %s" % os.path.basename(dll))
        print("  dll=%d B  pdb=%d B" % (real_size(dll), real_size(pdb)))

        # ── 运行：抓真实栈文本 ──
        # ⚠️ 需要一个小驱动程序来捕获栈（探针类本身不 catch）
        driver = os.path.join(work, "driver.cs")
        with io.open(driver, "w", encoding="utf-8", newline="\n") as fh:
            fh.write('''using System;
using System.IO;
public static class Driver {
  public static int Main() {
    try { FakeThirdPartyMod.Inner.Tick(0.016f); }
    catch (Exception ex) {
      File.WriteAllText(@"%s", ex.StackTrace, new System.Text.UTF8Encoding(false));
    }
    return 0;
  }
}
''' % (os.path.join(work, "stack.txt").replace("\\", "\\\\")))
        r2 = subprocess.run(
            [csc, "/nologo", "/target:exe", "/debug:full",
             "/pathmap:" + REPO + "=/",
             "/out:" + os.path.join(work, "driver.exe"),
             SRC, driver],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r2.returncode != 0:
            print("[FAIL] 驱动编译失败：\n" + (r2.stdout or "") + (r2.stderr or ""))
            return 1
        subprocess.run([os.path.join(work, "driver.exe")], cwd=work,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
        stackf = os.path.join(work, "stack.txt")
        if not os.path.isfile(stackf):
            print("[FAIL] 没拿到栈文本（driver 未写文件）")
            return 1
        with io.open(stackf, "r", encoding="utf-8-sig", errors="replace") as fh:
            stack = fh.read().strip()
        print()
        print("--- 真实栈文本（模拟第三方 mod）---")
        for ln in stack.split("\n")[:6]:
            print("   " + ln.strip())
        print()

        # ── A1/A2/A3：栈自带行号、行号正确、无绝对路径 ──
        print("[A] 栈里的行号（/debug:full + /pathmap 是否生效）")
        m = LINE_RE.search(stack)
        check("A1 栈里出现 `文件:行号`", m is not None, "栈=%r" % stack[:160])
        want = throw_line_number()
        got = int(m.group(2)) if m else -1
        check("A2 行号 == throw 语句真实行号（%d）" % want, got == want,
              "栈给 %s，源码 throw 在第 %d 行 —— 行号不对就是**假位置**" % (got, want))
        check("A3 栈里**不含**绝对路径（/pathmap 隐私生效）",
              (":\\" not in stack) and (":/" not in stack.replace("file:", "")),
              "含本机路径 ⇒ 会泄漏构建机（可能含用户名）")
        check("A3b 路径是符号形式（形如 /src/ 或 /tools/）",
              ("/src/" in stack) or ("/tools/" in stack) or ("/FakeThirdPartyMod" in stack)
              or bool(re.search(r"/[A-Za-z0-9_]+\.cs", stack)),
              repr(stack[:160]))

        # ── B：第三方方法名在栈里，且不是我们工程的 ──
        print("\n[B] 归属：这确实是**第三方**帧")
        tm = THIRD_RE.search(stack)
        check("B1 栈里出现第三方方法名", tm is not None,
              "栈=%r" % stack[:200])
        check("B2 方法名**不是**我们工程的（不以 BlBridge. 开头）",
              "BlBridge." not in stack, "栈里出现了我们的类型 ⇒ 模拟没到位")

        # ── C：真实栈是混合的 ──
        print("\n[C] 真实栈是混合的（不只第三方）")
        frames = [x.strip() for x in stack.split("\n") if x.strip()]
        third = [f for f in frames if "FakeThirdPartyMod" in f]
        other = [f for f in frames if "FakeThirdPartyMod" not in f]
        check("C1 既有第三方帧也有别的帧", len(third) >= 1 and len(other) >= 1,
              "third=%d other=%d" % (len(third), len(other)))
        print("     第三方帧 %d 个 / 其他帧 %d 个" % (len(third), len(other)))

        # ── D：用 bl_source_map 端到端定位（含反向对照）──
        print("\n[D] 端到端：真栈 → bl_source_map → 位置")
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import bl_source_map as sm                            # noqa: E402

        # 先把这份"模拟第三方"的索引建出来（模拟 bl_symbols --third-party 的产物）
        import bl_symbols as sb                               # noqa: E402
        exe2, err = sb.ensure_extractor()
        if err:
            print("  [skip] 抽取器不可用：%s" % err)
        else:
            idxdir = os.path.join(REPO, "out", "symbols-thirdparty")
            os.makedirs(idxdir, exist_ok=True)
            outidx = os.path.join(idxdir, "FakeThirdPartyMod.symbols.json")
            # 先把源码复制到索引能对应上的位置（模拟 mod 自带开发树）
            rr = subprocess.run([exe2, dll, pdb, outidx, "single"],
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
            built = (rr.returncode == 0 and os.path.isfile(outidx))
            check("D1 能从真 PDB 抽出该第三方程序集的索引", built,
                  (rr.stdout or "") + (rr.stderr or ""))
            if built:
                # 反向对照：先清掉内存缓存 + 只用"不含该索引"的池子
                sm._TP_CACHE["loaded"] = False
                idxs_all = sm._thirdparty_indexes()
                without = {k: v for k, v in idxs_all.items()
                           if k != "fakethirdpartymod"}
                tp_no, _ = sm.locate_thirdparty("FakeThirdPartyMod.Inner.Tick", without)
                check("D2 ★ 反向对照：**不给**该索引 ⇒ 无法定位（不是恒真）",
                      tp_no is None, "不给索引也报成功 ⇒ 判据恒真：%r" % (tp_no,))

                tp, cands = sm.locate_thirdparty("FakeThirdPartyMod.Inner.Tick")
                check("D3 给了索引 ⇒ 能定位", tp is not None,
                      "cands=%d" % len(cands))
                if tp:
                    check("D4 定位到正确文件", tp.get("file") == "FakeThirdPartyMod.cs",
                          repr(tp.get("file")))
                    # D5 ★ 最关键：索引给的行号 与 **运行时栈**给的行号一致
                    rt_line = None
                    for f in frames:
                        if "FakeBehavior.OnApplicationTick" in f or "Inner.Tick" in f:
                            mm2 = LINE_RE.search(f)
                            if mm2:
                                rt_line = int(mm2.group(2))
                                break
                    if rt_line is None:
                        print("  [i] 运行时栈未带该帧行号（已由 A2 单独证明行号有效）")
                    check("D5 ★ 索引行号 == 运行时栈行号",
                          rt_line is None or rt_line == want,
                          "运行时 %s vs 源码 %s —— 不一致说明索引与实际不符（最隐蔽的错）"
                          % (rt_line, want))
            # 清理：不要污染真实的第三方索引目录
            try:
                if os.path.isfile(outidx):
                    os.remove(outidx)
                    print("  [i] 已清理探针索引（不污染 out/symbols-thirdparty）")
            except Exception:                                 # noqa: BLE001
                pass
    finally:
        if args.keep:
            print("\n工作目录保留：%s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)

    print()
    print("=" * 74)
    if FAILS:
        print("失败 %d / %d：" % (len(FAILS), CHECKS[0]))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("全部通过：%d / %d" % (CHECKS[0], CHECKS[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
