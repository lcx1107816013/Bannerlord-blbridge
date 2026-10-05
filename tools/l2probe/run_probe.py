# -*- coding: utf-8 -*-
"""L2 API 漂移探针的执行器。

做什么：收集本机 v1.4.8 的全部游戏引用程序集，用 Roslyn `csc` 编译 `L2ApiProbe.cs`，
        把编译器输出按 `// ───────── CASE Pxx` 注释**归到每个 CASE**，打印一张漂移表。

为什么用「编译」当判据：上游 Bannerlord.GABS 声明支持 v1.3.15 / v1.3.13 / v1.2.12，本机是 v1.4.8。
        探针里的每一段都是上游的**真实调用点**（带 file:line 出处）。编译通过 = 该 API 在 1.4.8
        存在且签名兼容；编译不过 = 漂移，**错误信息本身就是判据**（会指出缺哪个成员 / 参数不对）。
        这样不需要启动游戏、不需要装 Lib.GAB、不需要跑起来。

覆盖范围的诚实标注：只覆盖探针文件里列出的 CASE，**不是**上游代码的全量验证。
        另有一类漂移本探针**测不到**：上游用 `AccessTools2.StaticFieldRefAccess<string>(typeof(X), "字段名")`
        这种**字符串**反射（Tools_CoreTools.cs:27-28、Tools_GauntletUITools.cs:25）—— 字段名错了编译照样过。
        那一类要运行期验，见本目录 README 的「第二层」。

退出码：0 = 全部 CASE 编译通过；1 = 有 CASE 漂移；2 = 环境问题（找不到 csc / 游戏 / 源文件）。
"""
import io
import os
import re
import subprocess
import sys

def safe_streams():
    """把 stdout/stderr 钉成 UTF-8 —— 与仓库 `tools/bl_common.safe_streams()` 同一口径。

    为什么必须有：Windows 上 Python 的 stdio 默认按 **locale** 编码（简中 = cp936）。
    本脚本的输出里既有中文又有 `✓`，一旦 stdout 被管道接走（`| Select-Object`），
    Python 就不再走控制台编码而退回 cp936 ⇒ `UnicodeEncodeError: 'gbk' codec can't encode …`
    （本轮实测踩到）。口径钉死 UTF-8，**不依赖控制台代码页**，也不靠"少写点非 ASCII"绕开。
    """
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.environ.get("BANNERLORD_DIR") or r"G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord"
SRC = os.path.join(HERE, "L2ApiProbe.cs")
OUT = os.path.join(HERE, "L2ApiProbe.dll")
RSP = os.path.join(HERE, "_refs.rsp")

CSC_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe",
    r"C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\Roslyn\csc.exe",
    r"C:\Program Files\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\Roslyn\csc.exe",
    r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise\MSBuild\Current\Bin\Roslyn\csc.exe",
]


def find_csc():
    for p in CSC_CANDIDATES:
        if os.path.exists(p):
            return p, False
    # 回退：dotnet SDK 里的 Roslyn
    sdk_root = r"C:\Program Files\dotnet\sdk"
    if os.path.isdir(sdk_root):
        for d in sorted(os.listdir(sdk_root), reverse=True):
            p = os.path.join(sdk_root, d, "Roslyn", "bincore", "csc.dll")
            if os.path.exists(p):
                return p, True
    return None, False


def collect_refs():
    """游戏程序集：bin 优先；再补各模块 bin 里 bin 没有的（按文件名去重）。"""
    seen = {}
    bins = [os.path.join(GAME, "bin", "Win64_Shipping_Client")]
    mods = os.path.join(GAME, "Modules")
    if os.path.isdir(mods):
        for m in sorted(os.listdir(mods)):
            b = os.path.join(mods, m, "bin", "Win64_Shipping_Client")
            if os.path.isdir(b):
                bins.append(b)
    skipped = []
    for b in bins:
        for n in sorted(os.listdir(b)):
            if not n.lower().endswith(".dll"):
                continue
            if not (n.startswith("TaleWorlds") or n.startswith("SandBox") or n == "Newtonsoft.Json.dll"):
                continue
            if n in seen:
                continue
            p = os.path.join(b, n)
            if not is_managed(p):
                skipped.append(n)
                continue
            seen[n] = p
    collect_refs.skipped = skipped
    # 框架引用程序集 + netstandard 门面（与 build.ps1 同款）
    ref_dir = r"C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8"
    if not os.path.exists(os.path.join(ref_dir, "mscorlib.dll")):
        ref_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                               "Microsoft.NET", "Framework64", "v4.0.30319")
    for n in ("mscorlib.dll", "System.dll", "System.Core.dll", "System.Xml.dll"):
        p = os.path.join(ref_dir, n)
        if os.path.exists(p):
            seen["@" + n] = p
    for c in (os.path.join(ref_dir, "Facades", "netstandard.dll"),
              os.path.join(ref_dir, "netstandard.dll"),
              os.path.join(GAME, "bin", "Win64_Shipping_Client", "netstandard.dll")):
        if os.path.exists(c):
            seen["@netstandard"] = c
            break
    return list(seen.values())


CASE_RE = re.compile(r"CASE\s+(P\d+[a-z]?)")

# 预期**编译不过**的 CASE —— 负向对照。
# P13 = 上游 `#else` 分支的 `party.Ai.SetMoveGoToPoint(new Vec2(x,y))`（v1.3.13 之前的写法）：
#       1.4.8 的 MobilePartyAi 上没有这个方法（实测 CS1061）。它**必须红**：
#       · 它红了 ⇒ 本探针确实在按编译器判定，不是恒绿；
#       · 它绿了   ⇒ 要么探针失效了，要么游戏把老 API 加回来了 —— 两种情况都该有人看一眼。
EXPECT_FAIL = {"P13"}
ERR_RE = re.compile(r"^(?P<file>[^(]+)\((?P<line>\d+),(?P<col>\d+)\):\s+error\s+(?P<code>[A-Z]+\d+):\s+(?P<msg>.*)$")
ANY_ERR_RE = re.compile(r"\berror\s+(?P<code>[A-Z]+\d+):")


def is_managed(path):
    """PE 映像里有没有 CLI 数据目录（= 是不是托管程序集）。

    为什么必须自己判断：`/reference:` 传一个**原生** DLL 会给 `error CS0009 无法打开元数据文件`，
    而那不是源码错误 ⇒ 会被"按 CASE 归纳"那一步漏掉，脚本照样打印「全部通过」。
    这正是本项目反复强调的"数据源接错的脚本也会通过"（见 AGENTS.md §四）。
    首版就是这样：把 TaleWorlds.Native.dll 当引用传进去，CS0009 落进了"其它输出"，
    退出码仍是 0。修法两道：① 引用前逐个判托管性；② 任何没归到 CASE 的 `error CS` 一律算**装置错误**（退出码 2）。
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(0x400)
        if len(head) < 0x40 or head[:2] != b"MZ":
            return False
        e_lfanew = int.from_bytes(head[0x3C:0x40], "little")
        with open(path, "rb") as fh:
            fh.seek(e_lfanew)
            sig = fh.read(4)
            if sig != b"PE\0\0":
                return False
            coff = fh.read(20)
            opt_start = fh.tell()
            opt = fh.read(256)
        magic = int.from_bytes(opt[0:2], "little")
        if magic == 0x10B:      # PE32
            dd = opt_start + 96
        elif magic == 0x20B:    # PE32+
            dd = opt_start + 112
        else:
            return False
        cli_off = dd + 14 * 8   # DataDirectory[14] = COM descriptor（CLI 头）
        with open(path, "rb") as fh:
            fh.seek(cli_off)
            raw = fh.read(8)
        if len(raw) < 8:
            return False
        size = int.from_bytes(raw[4:8], "little")
        return size != 0
    except Exception:
        return False


def case_map():
    """行号 -> CASE 标签（每个 CASE 的声明点起、到下一个 CASE 之前）。"""
    lines = io.open(SRC, "r", encoding="utf-8").read().splitlines()
    m = {}
    cur = "(文件头)"
    for i, ln in enumerate(lines, start=1):
        hit = CASE_RE.search(ln)
        if hit:
            cur = hit.group(1)
        m[i] = cur
    return m, lines


def main():
    safe_streams()
    if not os.path.exists(SRC):
        print("[ENV ] 找不到探针源码：%s" % SRC)
        return 2
    if not os.path.isdir(GAME):
        print("[ENV ] 找不到游戏目录：%s（可用 BANNERLORD_DIR 覆盖）" % GAME)
        return 2
    csc, use_dotnet = find_csc()
    if not csc:
        print("[ENV ] 找不到 Roslyn csc（VS Build Tools 或 dotnet SDK）")
        return 2

    refs = collect_refs()
    if not any(r.endswith("TaleWorlds.CampaignSystem.dll") for r in refs):
        print("[ENV ] 没收集到 TaleWorlds.CampaignSystem.dll —— 游戏目录不对？")
        return 2

    with io.open(RSP, "w", encoding="utf-8", newline="\n") as fh:
        for r in refs:
            fh.write('/reference:"%s"\n' % r)

    cmd = (["dotnet", csc] if use_dotnet else [csc]) + [
        "/nologo", "/nostdlib+", "/target:library", "/langversion:7.3",
        "/nowarn:0169,0649,0414", "/out:" + OUT, "@" + RSP, SRC]
    print("编译器 : %s" % (" ".join(cmd[:3])))
    print("引用数 : %d 个程序集（含 %s）" % (len(refs), os.path.basename(
        [r for r in refs if r.endswith("TaleWorlds.CampaignSystem.dll")][0])))
    skipped = getattr(collect_refs, "skipped", [])
    if skipped:
        print("已跳过 : %d 个非托管 DLL（无 CLI 头，传进来会 CS0009）：%s"
              % (len(skipped), ", ".join(skipped)))
    print("")

    pr = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = pr.stdout.decode("utf-8", errors="replace")

    cmap, lines = case_map()
    errs = {}
    others = []
    device = []
    for ln in text.splitlines():
        m = ERR_RE.match(ln.strip())
        if m and m.group("file").endswith("L2ApiProbe.cs"):
            case = cmap.get(int(m.group("line")), "?")
            errs.setdefault(case, []).append(
                (int(m.group("line")), m.group("code"), m.group("msg")))
            continue
        if ANY_ERR_RE.search(ln) and "error CS" in ln:
            # 装置错误：编译器报的错没归到任何 CASE（引用坏了 / 输出写不进去 …）
            device.append(ln.strip())
            continue
        if ln.strip():
            others.append(ln.strip())

    all_cases = []
    for ln in lines:
        h = CASE_RE.search(ln)
        if h:
            all_cases.append(h.group(1))

    print("按 CASE 汇总（%d 个；✓ = 与预期一致）" % len(all_cases))
    print("-" * 78)
    ok = 0
    wrong = 0
    for c in all_cases:
        e = errs.get(c)
        should_fail = c in EXPECT_FAIL
        good = (bool(e) == should_fail)
        if good:
            ok += 1
        else:
            wrong += 1
        tag = "OK  " if good else "FAIL"
        want = "预期**不该**编译过" if should_fail else "预期编译过"
        print("  [%s] %-6s %s  %s" % (tag, c, "编译失败" if e else "编译通过", want))
        for (lno, code, msg) in (e or [])[:3]:
            print("           L%-4d %s %s" % (lno, code, msg))
    print("-" * 78)
    print("与预期一致 %d / %d    （csc 退出码 %d）" % (ok, len(all_cases), pr.returncode))
    print("负向对照 %s：上游 v1.3.13 **之前**的写法，在 1.4.8 上本就不该存在 —— 它必须红；"
          % ", ".join(sorted(EXPECT_FAIL)))
    print("            它要是变绿了，说明本探针已失效（或游戏把老 API 加回来了），两种情况都该有人看一眼")
    if device:
        print("\n[ENV ] 装置错误：%d 条编译器错误**没有**归到任何 CASE ⇒ 本轮结论无效（不让它冒充通过）" % len(device))
        for o in device[:10]:
            print("  " + o)
    if others:
        print("\n编译器其它输出：")
        for o in others[:20]:
            print("  " + o)
    if device:
        return 2
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
