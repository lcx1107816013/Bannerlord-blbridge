#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""列出 Bannerlord 进程**实际加载了哪些 native DLL** —— 佐证"跑起来的到底是哪份代码"。

⚠️ **本工具的能力边界（2026-10-04 真机实测，别对它期望过高）**

`EnumProcessModules` 枚举的是 **Win32 模块表**，也就是走 `LoadLibrary` 加载的 **native DLL**。
而 mod 自己的代码（`BlBridge.dll` / `AnimusForge.dll` / `AIInfluence.dll` / …）是 **.NET 程序集**，
由 CLR 的 `Assembly.Load` 加载，**不会出现在 Win32 模块表里**。

实测证据：带 44 个 mod 的游戏进程，本工具只能列出 178 个模块，全是系统/游戏/驱动 DLL，
**一个 mod 的程序集都列不出来** —— 而此时 BlBridge 的桥明明在正常响应请求。

所以：
- **能用它证明**：某个 native 依赖真的进了进程。最有价值的一条是
  `AnimusForge\bin\Win64_Shipping_Client\onnxruntime.DLL` —— 它出现 = RAG 推理栈真的起来了。
- **不能用它判断**：某个 mod「有没有生效」。早期版本会输出一份
  「声明了但进程里没有」的清单，**那是误导性的**（几乎所有 mod 都会被列进去），已删除。
  要判断 mod 生效与否，去看 mod 自己的日志（`ModuleData`/`Logs` 下）和 BlBridge 的
  `bl_build_check` / `bl_status.loadedSha256`。


为什么需要它
------------
静态反编译只能证明"磁盘上有这份代码"，证明不了"进程真的加载了它"。
而在本机这个 mod 组合下，这个问题特别尖锐：

1. **AnimusForge 按 API 线挑实现**：`versions/1.3/AnimusForge.dll` 与
   `versions/1.4/AnimusForge.dll` 是两份 11.4MB 的实现，`Bootstrap` 运行时探测后
   只加载其中一份。看目录看不出加载了哪份。
2. **部署副本 vs 仓库源码**：`Modules\X\bin\...\X.dll` 可能与仓库里刚编出来的不同
   （BlBridge 自己就有 `build_check` 管这个，但那只对 BlBridge 一个模块）。
3. **同名 DLL 谁先被解析**：Harmony / ButterLib / UIExtenderEx 这些前置可能被多个
   mod 各自带一份，进程里真正生效的是哪一份，只有枚举模块才知道。

本工具用 `EnumProcessModules` + `GetModuleFileNameEx` 直接问进程，不给猜测留余地。

设计取舍
--------
1. **只读**。不写游戏进程内存、不注入、不挂调试器 —— 只是读进程句柄的模块表。
2. **不需要管理员**。同用户级别的进程就能枚举（`PROCESS_QUERY_INFORMATION |
   PROCESS_VM_READ`）；提权失败时如实报，不静默退化成"看起来没加载"。
3. **pid 不给就自己找**：用 `bridge_status.json` 里记的 pid，找不到再按进程名扫。
4. **输出 UTF-8**（`bl_common.safe_streams()`），与项目其它工具同一口径。
5. **不依赖第三方库**，纯 ctypes + 标准库 —— 它要在"刚装完 mod、环境还不确定的时候"跑，
   那时候恰恰最不该有依赖问题。

用法
----
    python tools/bl_modules.py                      # 全部已加载模块
    python tools/bl_modules.py --modules-only       # 只看 Modules\ 下的（mod 自己的 DLL）
    python tools/bl_modules.py --filter Animus      # 名字过滤
    python tools/bl_modules.py --json out.json      # 机读
    python tools/bl_modules.py --sha                # 额外算 SHA256（慢，但能对齐 build 清单）

返回码：0 正常 / 2 找不到进程或打不开 / 1 有模块读不出路径。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
import bl_common  # noqa: E402

GAME_EXE_NAMES = ("Bannerlord.BLSE.Standalone.exe", "Bannerlord.exe",
                  "MountAndBladeBannerlord.exe")

STATUS_CANDIDATES = [
    os.path.join(os.path.expanduser("~"), "Documents",
                 "Mount and Blade II Bannerlord", "BlBridge", "bridge_status.json"),
]

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


def _status_pid():
    """从 BlBridge 的状态文件里读 pid（它记录了当前桥接的是哪个游戏进程）。"""
    for p in STATUS_CANDIDATES:
        if not os.path.isfile(p):
            continue
        try:
            d = json.loads(open(p, "r", encoding="utf-8").read())
        except (ValueError, OSError):
            continue
        pid = d.get("pid")
        if isinstance(pid, int) and pid > 0:
            return pid
    return None


def _pids_alive():
    """当前存活的 {pid: 进程名}。用来在打开句柄前先确认目标还活着 ——
    否则会拿到一个 5 分钟前写进状态文件的 pid，然后 OpenProcess 报个莫名其妙的 299。"""
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, errors="replace")
    alive = {}
    if out.returncode != 0:
        return alive
    for line in (out.stdout or "").splitlines():
        parts = [x.strip('"') for x in line.split(",")]
        if len(parts) >= 2:
            try:
                alive[int(parts[1])] = parts[0]
            except ValueError:
                pass
    return alive


def _find_pid_by_name():
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, errors="replace")
    if out.returncode != 0:
        return None
    for line in (out.stdout or "").splitlines():
        parts = [x.strip('"') for x in line.split(",")]
        if len(parts) >= 2 and parts[0] in GAME_EXE_NAMES:
            try:
                return int(parts[1])
            except ValueError:
                continue
    return None


def _load_win32():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    k32.OpenProcess.restype = wintypes.HANDLE
    psapi.EnumProcessModules.restype = wintypes.BOOL
    psapi.GetModuleFileNameExW.restype = wintypes.DWORD
    psapi.GetModuleFileNameExA.restype = wintypes.DWORD
    return ctypes, k32, psapi


def enumerate_modules(pid):
    """返回 [(path, size)]。打不开进程抛 RuntimeError。"""
    ctypes, k32, psapi = _load_win32()
    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        raise RuntimeError("打不开进程 %d（错误 %d）—— 可能需要同用户级别的权限，"
                           "或进程已退出" % (pid, ctypes.GetLastError()))
    try:
        buf = (ctypes.c_ulonglong * 4096)()
        needed = ctypes.c_ulong()
        if not psapi.EnumProcessModules(handle, ctypes.byref(buf), ctypes.sizeof(buf),
                                        ctypes.byref(needed)):
            raise RuntimeError("EnumProcessModules 失败（错误 %d）" % ctypes.GetLastError())
        count = min(needed.value // ctypes.sizeof(ctypes.c_ulonglong), 4096)
        out = []
        for i in range(count):
            h = buf[i]
            if not h:
                continue
            name = ctypes.create_unicode_buffer(4096)
            n = psapi.GetModuleFileNameExW(handle, ctypes.c_ulonglong(h),
                                           name, 4096)
            path = name.value if n else ""
            if not path:
                nb = ctypes.create_string_buffer(4096)
                n2 = psapi.GetModuleFileNameExA(handle, ctypes.c_ulonglong(h),
                                                nb, 4096)
                path = nb.value.decode("utf-8", "replace") if n2 else ""
            size = 0
            try:
                size = os.path.getsize(path) if path and os.path.isfile(path) else 0
            except OSError:
                size = 0
            out.append((path, size))
        return out
    finally:
        k32.CloseHandle(handle)


def _deployed_modules(game_dir):
    """扫 Modules\\*\\SubModule.xml 得到 {模块名: 目录}，用来标注 DLL 属于哪个 mod。"""
    root = os.path.join(game_dir, "Modules")
    out = {}
    if not os.path.isdir(root):
        return out
    for name in os.listdir(root):
        d = os.path.join(root, name)
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "SubModule.xml")):
            out[name] = d
    return out


def _submodule_dlls(game_dir):
    """{模块名: [SubModule.xml 里声明的 DLL 名]} —— 用来标出"声明了但没加载"的。"""
    root = os.path.join(game_dir, "Modules")
    out = {}
    if not os.path.isdir(root):
        return out
    for name in os.listdir(root):
        sm = os.path.join(root, name, "SubModule.xml")
        if not os.path.isfile(sm):
            continue
        try:
            r = ET.parse(sm).getroot()
        except ET.ParseError:
            continue
        out[name] = [a.get("value") for a in r.findall(".//Assemblies/Assembly")
                     if a.get("value")] + \
                    [d.get("value") for d in r.findall(".//SubModules/SubModule/DLLName")
                     if d.get("value")]
    return out


def _main(argv=None):
    bl_common.safe_streams()
    ap = argparse.ArgumentParser(
        description="列出游戏进程实际加载的 DLL（回答'跑起来的到底是哪份代码'）")
    ap.add_argument("--pid", type=int, help="游戏进程 pid；不给就从 bridge_status.json / 按进程名找")
    ap.add_argument("--game-dir",
                    default=r"G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord")
    ap.add_argument("--modules-only", action="store_true",
                    help="只显示 Modules\\ 下的 DLL（各 mod 自己的代码）")
    ap.add_argument("--filter", help="路径/文件名包含该子串才显示")
    ap.add_argument("--sha", action="store_true", help="额外计算 SHA256（慢）")
    ap.add_argument("--json", help="写 JSON")
    args = ap.parse_args(argv)

    pid = args.pid or _status_pid() or _find_pid_by_name()
    if not pid:
        print("找不到游戏进程：bridge_status.json 里没有，tasklist 里也没有 %s"
              % " / ".join(GAME_EXE_NAMES))
        return 2
    alive = _pids_alive()
    if pid not in alive:
        print("pid %d 已经不在了（bridge_status.json 是上次会话留下的？）—— 先启动游戏再跑本工具"
              % pid)
        return 2
    print("目标进程：pid %d  %s" % (pid, alive[pid]))
    try:
        mods = enumerate_modules(pid)
    except RuntimeError as e:
        print(str(e))
        return 2

    deployed = _deployed_modules(args.game_dir)
    declared = _submodule_dlls(args.game_dir)

    rows = []
    missing_path = 0
    for path, size in mods:
        if not path:
            missing_path += 1
            continue
        owner = ""
        low = path.lower()
        for mname, mdir in deployed.items():
            if low.startswith(mdir.lower()):
                owner = mname
                break
        row = {"path": path, "size": size, "module": owner}
        if args.sha and os.path.isfile(path):
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            row["sha256"] = h.hexdigest()[:16]
        rows.append(row)

    shown = rows
    if args.modules_only:
        shown = [r for r in shown if r["module"]]
    if args.filter:
        f = args.filter.lower()
        shown = [r for r in shown if f in r["path"].lower()]

    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"pid": pid, "modules": shown},
                                ensure_ascii=False, indent=2))
        print("→ %s" % args.json)

    print("pid %d  已加载模块 %d 个（Modules\\ 下的 %d 个）"
          % (pid, len(rows), sum(1 for r in rows if r["module"])))
    print("%-22s %10s  %s" % ("归属模块", "大小", "路径"))
    for r in sorted(shown, key=lambda r: (r["module"], r["path"])):
        print("%-22s %10s  %s%s"
              % (r["module"] or "—",
                 ("%.1fM" % (r["size"] / 1048576.0)) if r["size"] else "—",
                 r["path"],
                 ("  sha=%s" % r["sha256"]) if "sha256" in r else ""))

    return 1 if missing_path else 0


if __name__ == "__main__":
    sys.exit(_main())
