#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
BlBridge 崩溃取证器（仅标准库）。

## 为什么需要它（真实痛点，2026-10-06）

一次崩溃的定位原本要这样走：启动 → 等 30s → 发现进程没了 → 翻 `ModLogs` → 猜 → 再启动。
单轮 ~2 分钟**且靠猜**。更糟的是这条路**经常根本不通**：

  - RBM 的 `Debug.Print` **不落** `ModLogs/default*.log`（本项目 2026-10-06 实证：该文件 `[RBM` 零命中）；
  - ButterLib 的托管异常处理器（`ExceptionHandler Enabled=true`）对**原生崩溃无效**
    —— 反编译确认它的 Finalizer 挂在托管 tick 上，抓不到原生访问违规；
  - 于是"翻日志"对**原生崩溃**这条最常见的路径是**无效的**。

但真正的原因其实一直都在，只是没人去看：**WER 早就把每次崩溃的 minidump 落盘了**。
本机实测 `%LOCALAPPDATA%\CrashDumps\` 下有 7 份 dump、合计 563 MB，
每份都能直接给出「异常代码 + 崩溃模块+偏移 + 访问目标地址」。

**minidump 是有公开文档的格式**（MINIDUMP_HEADER / _DIRECTORY / _EXCEPTION_STREAM /
_MODULE_LIST），纯 Python 就能解析 ⇒ **不需要安装 WinDbg / pybag / x64dbg**。

## 它补的是哪一格（与既有工具的分工）

  - `bl_status`：已经能判 `crashed` / `crashed_in_battle`（靠 `cleanExit` 标记），
    但它只说"崩了"，**不说崩在哪**；
  - `tools/bl_modules.py`：列**运行中**进程加载了哪些 native DLL，进程一死就用不了；
  - **本工具**：进程死后仍可用，且把三份信息**对接**起来 ——
    WER 的崩溃签名 × 状态文件里"当时在打哪一场" × 最后一场日志的尾部。

> ★ 这个对接是关键的增量：dump 只知道内存地址，**不知道那意味着什么**；
> 而状态文件知道 `lastBattle` / `missionsThisSession` / `pid`。
> 两边一拼，才能回答"**我第 3 场战斗，崩在 RBMCombat.dll 里**"。

## 与四件外部工具的关系（融合说明）

  - **BEW / ButterLib**：它们解决**托管**崩溃（Finalizer + 报告）。本工具解决**原生**崩溃，
    正是不在那两者覆盖范围内的那一类 —— 互补而非重复。
  - **mcp-windbg / x64dbg MCP**：能力更强（能看完整调用栈、能下断点），
    但需要装 Debugging Tools / x64dbg 且要**人机交互**。
    本工具是**零依赖、无人值守**的那一档：先把"崩在哪、是不是同一个 bug"答掉，
    只有需要深挖时才升级到调试器。升级路径见 `--dump` 参数与输出里的 `escalate` 字段。

## 用法

  python bl_crash.py                      # 汇总最近的崩溃（默认看 8 个）
  python bl_crash.py --latest             # 只看最新一次
  python bl_crash.py --pid 28540          # 只看某个 pid（配合 bl_status 给的 pid）
  python bl_crash.py --json               # 输出 JSON（给 MCP 用）
  python bl_crash.py --stack              # 额外做"穷人的栈回溯"（扫栈找自家模块的返回地址）
  python bl_crash.py --path <x.dmp>       # 指定 dump

  python bl_crash.py --deep               # ★ 用真调试器（cdb）拿符号化栈 + 崩溃指纹
  python bl_crash.py --deep --cdb <路径>  # 指定 cdb.exe

## --deep 那一档（可选升级）

默认（纯 Python）答「异常代码 + 崩溃模块+偏移」；`--deep` 再答两件事：

  - **托管栈** —— 真正的调用链（如 `RTSCamera.CommandSystem.Logic.CommandSystemLogic.OnRemoveBehavior`）；
  - **FAILURE_BUCKET_ID** —— Microsoft 的崩溃指纹，判「**是不是同一个 bug**」最硬的判据。

⚠️ **它比异常代码精确得多**：同样报 `0xC0000005`，`RTSCamera.OnRemoveBehavior`
和另一处 `ucrtbase!abort` 是**两个完全不同的 bug**；只看异常代码会混为一谈
（本项目 2026-10-06 实测踩到）。

需要完整 Debugging Tools（`cdb.exe`）：`winget install --id Microsoft.WinDbg`。
**没装也能用** —— `--deep` 会明确告诉你 `no_cdb` 与安装命令，**不会报错、也不会伪装成"没崩溃"**。
"""
import glob
import json
import os
import re
import struct
import sys

# ── minidump 流类型（Microsoft 公开文档）──────────────────────────────
STREAM_THREAD_LIST = 3
STREAM_MODULE_LIST = 4
STREAM_MEMORY_LIST = 5
STREAM_EXCEPTION = 6
STREAM_SYSTEM_INFO = 7
STREAM_MEMORY64_LIST = 9

# 异常代码 → 人话。重点是让判读**不必再去查表**。
EXC_NAMES = {
    0xC0000005: "EXCEPTION_ACCESS_VIOLATION（原生访问违规）",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN / FailFast（栈保护或主动终止）",
    0xE0434352: "CLR 托管异常（0xE0434352 = 'CCR'）",
    0xC00000FD: "STATUS_STACK_OVERFLOW（栈溢出）",
    0x80000003: "BREAKPOINT（断点，多为调试器/断言）",
    0xC000001D: "ILLEGAL_INSTRUCTION（非法指令）",
    0xC0000094: "INTEGER_DIVIDE_BY_ZERO（整数除零）",
    0xC0000096: "PRIVILEGED_INSTRUCTION（特权指令）",
    0xC0000374: "HEAP_CORRUPTION（堆损坏）",
    0xC0000135: "DLL_NOT_FOUND（缺 DLL）",
    0xC0000139: "ENTRYPOINT_NOT_FOUND（入口点找不到）",
    0xC0000006: "IN_PAGE_ERROR（页错误，常为磁盘/被杀软拦）",
}

# 这些异常代码**天然属于托管世界** ⇒ 若要深挖应走 SOS / 托管栈，而不是原生调试器。
MANAGED_CODES = {0xE0434352}
# 这些是"进程级终止"⇒ 托管异常处理器（BEW/ButterLib）**结构上抓不到**。
NATIVE_CODES = {0xC0000005, 0xC0000409, 0xC00000FD, 0xC000001D, 0xC0000374, 0xC0000096}


def _crash_dir():
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "CrashDumps")


def _wer_dirs():
    pd = os.environ.get("ProgramData", r"C:\ProgramData")
    return [os.path.join(pd, "Microsoft", "Windows", "WER", "ReportArchive"),
            os.path.join(pd, "Microsoft", "Windows", "WER", "ReportQueue")]


def list_dumps(name_filter="Bannerlord", log_dir=None):
    """列出可用 dump，新的在前。

    ⚠️ 两个来源都要看，它们**不是同一件事**（实测本机两者都有，且时间戳能对上）：
      - `%LOCALAPPDATA%\\CrashDumps\\<exe>.<pid>.dmp` —— ★ **文件名带 pid**，
        这是与 BlBridge 状态文件对接的关键（能回答"崩的是不是桥接的那个进程"）；
      - WER `ReportArchive\\AppCrash_*\\` —— 目录里有 `Report.wer`，
        含**已解析好的**崩溃签名（`Sig[3] 故障模块` / `Sig[6] 异常代码`），
        可用来**交叉验证**本工具的解析结果。

    返回 [{path, pid, exe, size, mtime, source}]，pid 可能为 None（WER 那份）。
    """
    out = []
    for p in glob.glob(os.path.join(_crash_dir(), "*.dmp")):
        if name_filter and name_filter.lower() not in os.path.basename(p).lower():
            continue
        base = os.path.basename(p)[:-4]                 # 去掉 .dmp
        pid = None
        exe = base
        if "." in base:
            head, tail = base.rsplit(".", 1)
            if tail.isdigit():
                pid = int(tail)
                exe = head
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"path": p, "pid": pid, "exe": exe, "size": st.st_size,
                    "mtime": st.st_mtime, "source": "CrashDumps"})
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


def read_wer_signature(pid=None):
    """从 WER 报告里读**已解析好的**签名，用于交叉验证。

    返回 [{code, module, offset, time, dir}]；`code` 已是 `0x...` 形式字符串。
    这些值由 Windows 自己算出来，所以拿它验证 minidump 解析器非常合适
    （本工具的自测就是这么做的，见 tools/bl_crash_selftest.py）。
    """
    res = []
    for base in _wer_dirs():
        if not os.path.isdir(base):
            continue
        for d in glob.glob(os.path.join(base, "AppCrash_*")):
            wer = os.path.join(d, "Report.wer")
            if not os.path.isfile(wer):
                continue
            rec = {"dir": d, "code": None, "module": None, "offset": None, "time": None}
            try:
                # Report.wer 是 UTF-16LE。
                #
                # ⚠️ **必须按 `Sig[i].Name` 的标签配对解析，不能按固定下标**。
                #    本项目实测（2026-10-06）踩到过：`Sig` 的**下标会在不同报告之间漂移** ——
                #    有的报告 `Sig[6]=异常代码 / Sig[7]=异常偏移`，
                #    另一份却是 `Sig[6]=偏移 / Sig[7]=异常代码`。
                #    按固定下标读会把"偏移 0xa527e"当成异常代码（本工具修好前就是这样，
                #    输出里出现了 `0xA527E`、`0x71A0D` 这种**不是异常代码**的值）。
                #    正确做法：先把 Name→Value 配对收集，再按**名字**取。
                names, values = {}, {}
                with open(wer, "r", encoding="utf-16", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if line.startswith("Sig[") and ".Name=" in line:
                            head, val = line.split("=", 1)
                            names[head.replace(".Name", "")] = val.strip()
                        elif line.startswith("Sig[") and ".Value=" in line:
                            head, val = line.split("=", 1)
                            values[head.replace(".Value", "")] = val.strip()
                by_name = {names[k]: v for k, v in values.items() if k in names}

                def pick(*wanted):
                    """按中文名取（WER 的 Name 是本机语言的，故给出多个候选）。"""
                    for w in wanted:
                        for nm, v in by_name.items():
                            if w in nm:
                                return v
                    return None

                rec["module"] = pick("故障模块名称", "Faulting module name")
                code = pick("异常代码", "Exception code")
                if code:
                    # 统一成 0xXXXXXXXX（WER 给的是不带 0x 的十六进制）
                    c = code.strip()
                    if c.lower().startswith("0x"):
                        c = c[2:]
                    try:
                        rec["code"] = "0x%08X" % int(c, 16)
                    except ValueError:
                        rec["code"] = code
                rec["offset"] = pick("异常偏移", "Fault offset")
                rec["app"] = pick("应用程序名", "Application name")
                rec["time"] = os.path.getmtime(wer)
            except OSError:
                continue
            res.append(rec)
    res.sort(key=lambda r: r["time"] or 0, reverse=True)
    return res


# ─────────────────────────────────────────────────────────────────────
# minidump 解析（核心）
# ─────────────────────────────────────────────────────────────────────

def _md_string(data, rva):
    """读 MINIDUMP_STRING。

    ⚠️ 长度字段是**字节数且包含结尾的 NUL 终止符**（Microsoft 文档：
    "LengthOfString: The size of the string in bytes, **including the null terminator**"）。
    ⇒ 必须把尾部 NUL 剥掉，否则模块名会带一个 `\\x00`，
      拼出 `RBMCombat.dll\\x00+0x3456` 这种话 —— 打印出来不易察觉，但会让下游字符串比较全部失效。

    本项由自测①抓到（拿真实 dump 走运时不一定暴露，因为显示上像空白）。
    """
    if not rva:
        return ""
    try:
        (length,) = struct.unpack_from("<I", data, rva)
        raw = data[rva + 4:rva + 4 + length]
        # ⚠️ `LengthOfString` 的**约定不统一**，两种都实测到了：
        #      - 合成/文档口径：长度**含**结尾 NUL；
        #      - **本机真实 WER dump：长度不含 NUL**（末尾就是最后一个字符，
        #        实测 `b'l\x00l\x00'` ⇒ "ntdll.dll"）。
        #    踩过两次：先 `split(b"\x00\x00")` 会在字符中间切错（'l'=6C 00），
        #    再盲目 `-2` 会把真实 dump 的最后一个字符吃掉 ⇒ 输出 `KERNELBASE.dl`、`ucrtbase.dl`
        #    （少一个 l —— 这种错**打印出来很不显眼**，但会让下游的模块名比较全部失配）。
        #    正解：**全解 + rstrip('\x00')** —— 含不含 NUL 都能得到正确结果。
        return raw.decode("utf-16-le", errors="replace").rstrip("\x00").strip()
    except (struct.error, IndexError):
        return ""


def parse_dump(path, want_stack=False):
    """解析一份 minidump，返回结构化结果。**不抛异常**：坏文件也返回带 error 的 dict。

    做三件事：
      1. 读异常流 ⇒ 异常代码 / 崩溃地址 / 访问目标；
      2. 读模块表 ⇒ 把地址映射成 `模块名+偏移`（★ 这是"崩在谁身上"的答案）；
      3. 可选：穷人的栈回溯 —— 扫 RSP 附近的指针，挑出落在已知模块内的值，
         按栈顺序给出"调用链上出现过哪些模块"。**不需要调试器也不需要 pdb**。
    """
    out = {"file": os.path.basename(path), "path": path}
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as e:
        out["error"] = "读不到文件：%s" % e
        return out
    out["size"] = len(data)

    try:
        sig, _ver, nstreams, dirrva = struct.unpack_from("<IIII", data, 0)
    except struct.error:
        out["error"] = "文件太小，不是 minidump"
        return out
    if sig != 0x504D444D:                     # 'MDMP'
        out["error"] = "不是 minidump（签名 0x%08X ≠ MDMP）" % sig
        return out
    out["streams"] = nstreams

    streams = {}
    for i in range(nstreams):
        try:
            stype, dsize, rva = struct.unpack_from("<III", data, dirrva + i * 12)
        except struct.error:
            break
        streams.setdefault(stype, []).append((dsize, rva))

    # ── 模块表 ────────────────────────────────────────────────────────
    modules = []
    if STREAM_MODULE_LIST in streams:
        _, rva = streams[STREAM_MODULE_LIST][0]
        try:
            (nmods,) = struct.unpack_from("<I", data, rva)
            off = rva + 4
            for _ in range(nmods):
                base, size, _csum, _tds, namerva = struct.unpack_from("<QIIII", data, off)
                modules.append((base, size, _md_string(data, namerva)))
                off += 108                     # sizeof(MINIDUMP_MODULE)
        except struct.error:
            pass
    out["module_count"] = len(modules)

    def addr_mod(addr):
        """地址 → '模块名+0x偏移'。★ 命中不了的地址本身就是重要信号（野跳转）。"""
        for base, size, name in modules:
            if base <= addr < base + size:
                return "%s+0x%X" % (os.path.basename(name), addr - base)
        return None

    # ── 异常流 ────────────────────────────────────────────────────────
    exc = None
    rsp = None
    if STREAM_EXCEPTION in streams:
        _, rva = streams[STREAM_EXCEPTION][0]
        try:
            tid, _align = struct.unpack_from("<II", data, rva)
            er = rva + 8
            code, flags = struct.unpack_from("<II", data, er)
            _rec, exc_addr = struct.unpack_from("<QQ", data, er + 8)
            nparams, _u = struct.unpack_from("<II", data, er + 24)
            params = list(struct.unpack_from("<15Q", data, er + 32))

            exc = {
                "thread_id": tid,
                "code": "0x%08X" % code,
                "code_hex": code,
                "name": EXC_NAMES.get(code, "(未收录的异常代码)"),
                "flags": flags,
                "address": exc_addr,
                "address_hex": "0x%016X" % exc_addr,
                "address_symbol": addr_mod(exc_addr),
                "nparams": nparams,
            }
            # 访问违规：参数是 [读/写, 目标地址] ⇒ 直接回答"碰了哪个地址"
            if code == 0xC0000005 and nparams >= 2:
                exc["access_type"] = "写" if params[0] == 1 else ("执行" if params[0] == 8 else "读")
                exc["access_target"] = "0x%016X" % params[1]
                exc["access_target_symbol"] = addr_mod(params[1]) or "未映射（野指针 / 空指针附近）"

            # 该线程的 CONTEXT（x64 固定偏移）——取寄存器，特别是 RSP（栈扫要用）
            tc_size, tc_rva = struct.unpack_from("<II", data, er + 32 + 15 * 8)
            if tc_rva and tc_size >= 0x100:
                regs = {}
                for nm, fo in (("Rax", 0x78), ("Rcx", 0x80), ("Rdx", 0x88), ("Rbx", 0x90),
                               ("Rsp", 0x98), ("Rbp", 0xA0), ("Rsi", 0xA8), ("Rdi", 0xB0),
                               ("R8", 0xB8), ("R9", 0xC0), ("R10", 0xC8), ("R11", 0xD0),
                               ("R12", 0xD8), ("R13", 0xE0), ("R14", 0xE8), ("R15", 0xF0),
                               ("Rip", 0xF8)):
                    try:
                        (v,) = struct.unpack_from("<Q", data, tc_rva + fo)
                        regs[nm] = v
                    except struct.error:
                        break
                if regs:
                    exc["registers"] = {k: "0x%016X" % v for k, v in regs.items()}
                    rsp = regs.get("Rsp")
        except struct.error:
            pass
    out["exception"] = exc

    # 归因：崩溃地址落在谁身上？落不到任何模块 = 跳到了非法地址（本身是强信号）
    if exc:
        out["blame"] = exc.get("address_symbol") or "不在任何已加载模块内（跳转到非法地址）"
        ch = exc["code_hex"]
        if ch in MANAGED_CODES:
            out["family"] = "managed"
            out["escalate"] = ("托管异常 ⇒ 深挖请用托管栈（SOS / dotnet-dump），"
                              "**不是**原生调试器；也可查 ButterLib/BEW 的报告")
        elif ch in NATIVE_CODES:
            out["family"] = "native"
            out["escalate"] = ("原生崩溃 ⇒ 托管异常处理器（BEW/ButterLib 的 Finalizer）"
                              "结构上抓不到。深挖用 mcp-windbg（load_dump）或 x64dbg MCP")
        else:
            out["family"] = "unknown"
            out["escalate"] = "异常代码未收录，建议用调试器看完整栈"
    else:
        out["blame"] = None
        out["family"] = None
        out["escalate"] = "无异常流（可能是手动 dump 或进程被强杀）⇒ 无崩溃签名可归因"

    # ── 穷人的栈回溯（可选）──────────────────────────────────────────
    # 思路：x64 栈上散布着返回地址。扫 RSP 起 8KB，把落在**已知模块**范围内的
    # 8 字节值挑出来，按出现顺序即"调用链上出现过哪些模块"。
    # ⚠️ 这是启发式（会把恰好长得像地址的数据也捞进来），所以输出里标了 `heuristic`。
    #    它的价值是回答"**调用链里有没有我的 DLL**"，而不是精确还原每一帧。
    if want_stack and rsp:
        stack_scan = []
        try:
            # 需要把 RVA 转成文件偏移：用 Memory64List 时 RVA 就是文件偏移；
            # 普通 MemoryList 要查描述符。这里只做**保守**尝试，失败就算了。
            span = 8192
            # 大多数 WER minidump 带 Memory64List：RVA 即文件偏移
            if STREAM_MEMORY64_LIST in streams:
                _, mrva = streams[STREAM_MEMORY64_LIST][0]
                (nranges,) = struct.unpack_from("<Q", data, mrva)
                base_rva = mrva + 8 + nranges * 16
                cur_file = base_rva
                ranges = []
                for i in range(nranges):
                    start, msize = struct.unpack_from("<QQ", data, mrva + 8 + i * 16)
                    ranges.append((start, msize, cur_file))
                    cur_file += msize
                for start, msize, foff in ranges:
                    if start <= rsp < start + msize:
                        delta = rsp - start
                        chunk = data[foff + delta: foff + delta + min(span, msize - delta)]
                        for k in range(0, len(chunk) - 8, 8):
                            (val,) = struct.unpack_from("<Q", chunk, k)
                            if val == 0:
                                continue
                            sym = addr_mod(val)
                            if sym:
                                stack_scan.append({"off": k, "addr": "0x%016X" % val, "symbol": sym})
                        break
        except (struct.error, IndexError):
            pass
        # 去重但保留顺序（同一模块连续重复只留第一个）
        seen = set()
        uniq = []
        for f in stack_scan:
            key = f["symbol"].split("+")[0]
            if key in seen:
                continue
            seen.add(key)
            uniq.append(f)
        out["stack"] = {"heuristic": True, "rsp": "0x%016X" % rsp,
                        "frames": uniq[:24],
                        "note": "启发式：栈上落在已知模块内的指针。用来判『调用链里有没有某个 DLL』，"
                                "不是精确帧。要精确栈请用 mcp-windbg / x64dbg MCP"}
    return out


# ─────────────────────────────────────────────────────────────────────
# --deep：用真调试器拿符号化的栈（可选升级档）
# ─────────────────────────────────────────────────────────────────────
#
# ## 为什么需要这一档（纯 Python 解析的边界）
#
# 上面的解析器能答「异常代码 + 崩溃模块+偏移」，但**答不了两件事**：
#   1. **托管栈** —— `0xC0000005` 只是一个内存违规；真正要看的是 .NET 侧的调用链
#      （实测本机那几次崩溃，答案在 `RTSCamera.CommandSystem.Logic.CommandSystemLogic
#      .OnRemoveBehavior` 和 `HarmonyLib.MethodCreatorTools.EmitCodes`，纯解析拿不到）；
#   2. **FAILURE_BUCKET_ID** —— Microsoft 的崩溃指纹，判「**是不是同一个 bug**」最硬的判据。
#
# ## 为什么不强制依赖
#
# 需要完整 Debugging Tools（`cdb.exe`）。实测本机：
#   - `winget install Microsoft.WinDbg` **一条命令可装**（装到 WindowsApps，约 100 MB）；
#   - ⚠️ 但 `pybag`（mcp-windbg 的后端）**用不了它**：`ctypes.LoadLibrary` 从
#     `WindowsApps` 读 DLL 会 **WinError 5 拒绝访问**（Appx 的 ACL）。
#     ⇒ 所以这里**不*走* pybag**，而是**直接跑 `cdb.exe` 子进程**（外部进程，无 ACL 问题，
#       也符合 BlBridge「宿主侧、无人值守」的架构）。
#   - `System32\\dbgeng.dll` 是**精简版**：能 import，但 `OpenDumpFile` 报 `E_NOTIMPL`
#     （实测），**不能**用来解 dump。别拿它充数。
#
# ## 实测成本
#
# 带符号缓存时**约 5 秒/份**；首次会从 MS 符号服务器拉符号，**明显更慢**（可能 30~60s）。
# ⇒ 默认给 120s 超时，并在超时时**明确报出来**而不是静默返回空。

CDB_TIMEOUT_SEC = 120

# 一次跑完，尽量少的往返。顺序有讲究：
#   .loadby sos clr —— 先加载 SOS，否则 !pe/!clrstack 不可用
#   .ecxr           —— 切到异常上下文（**必须**在任何栈命令之前）
#   !pe             —— 托管异常对象（类型 + Message + 托管栈）；对 0xE0434352 是主力
#   !clrstack       —— 托管调用栈；对原生访问违规也常有值（CLR 自己记着）
#   !analyze -v     —— 拿 FAILURE_BUCKET_ID / SYMBOL_NAME / MODULE_NAME
_cdb_script = (" .loadby sos clr; .ecxr; !pe; !clrstack; !analyze -v; q ")


def find_cdb():
    """找一个能用的 `cdb.exe`。找不到返回 None（调用方必须优雅退化）。

    搜索顺序（**WinDbg Appx 优先**，因为实测它能解 dump，而 System32 的 dbgeng 不能）：
      1. `BLBRIDGE_CDB` 环境变量（给用户强制指定的口子）；
      2. WinDbg Appx 包（`WindowsApps\\Microsoft.WinDbg_*\\amd64\\cdb.exe`）；
      3. 传统 Windows SDK 路径（`Windows Kits\\10\\Debuggers\\x64\\cdb.exe`）；
      4. `PATH` 上的 `cdb`。

    ⚠️ **Appx 这一档踩过一个坑**：直觉写法是 `os.listdir(r"C:\\Program Files\\WindowsApps")`
    再挑 `Microsoft.WinDbg_*` —— 但该目录**需要管理员权限才能列目录**（WinError 5），
    而**具体子路径是可以直接访问/执行的**（实测 `cdb.exe -version` 正常）。
    ⇒ 所以**绝不能用 listdir**：那会在普通权限下**静默**漏掉已安装的 WinDbg
    （本工具修好前就是这样，明明装了却报 `no_cdb`）。
    正确做法：问 Windows 自己（`Get-AppxPackage`），它能拿到 `InstallLocation` 而无需提权。
    """
    env = os.environ.get("BLBRIDGE_CDB")
    if env and os.path.isfile(env):
        return env

    cands = []

    # 2. WinDbg Appx —— 先问 PowerShell（可靠且不需提权）
    try:
        import subprocess
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-AppxPackage -Name '*WinDbg*' | Select-Object -First 1).InstallLocation"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        loc = (r.stdout or b"").decode("utf-8", errors="replace").strip()
        if loc and os.path.isdir(loc):
            cands.append(os.path.join(loc, "amd64", "cdb.exe"))
    except (OSError, subprocess.SubprocessError):
        pass

    # 2b. 退路：已知的 WindowsApps 目录**不能列**，但试几条常见版本名也无害。
    #     这里只做"试探具体路径"，不做 listdir。
    wa = r"C:\Program Files\WindowsApps"
    for d in ("Microsoft.WinDbg_1.2606.22001.0_x64__8wekyb3d8bbwe",
              "Microsoft.WinDbg_1.2600.0.0_x64__8wekyb3d8bbwe"):
        cands.append(os.path.join(wa, d, "amd64", "cdb.exe"))

    # 3. 传统 SDK
    for root in (r"C:\Program Files (x86)\Windows Kits\10\Debuggers",
                 r"C:\Program Files\Windows Kits\10\Debuggers"):
        cands.append(os.path.join(root, "x64", "cdb.exe"))

    for c in cands:
        if os.path.isfile(c):
            return c

    # 4. PATH
    from shutil import which
    return which("cdb")


def run_cdb_deep(path, cdb=None, timeout=CDB_TIMEOUT_SEC, symbol_cache=None):
    """跑 `cdb` 解一份 dump，返回结构化结果。**永不抛异常**。

    返回 {"ok": bool, "cdb": <路径>, "seconds": float,
          "bucket": str|None, "symbol": str|None, "module": str|None, "image": str|None,
          "exceptionType": str|None, "message": str|None,
          "managedStack": [str], "readAddress": str|None,
          "reason": str(为什么没结果), "rawTail": str}

    ⚠️ 三种"没结果"要分清，否则会把**环境问题**误读成**没有崩溃**：
      - 没有 cdb        ⇒ reason="no_cdb"（附安装命令）
      - cdb 超时        ⇒ reason="timeout"（首次拉符号很慢是正常的）
      - cdb 跑了但没输出 ⇒ reason="no_output"
    """
    import subprocess
    import time as _time

    out = {"ok": False, "cdb": None, "seconds": None, "bucket": None,
           "symbol": None, "module": None, "image": None, "exceptionType": None,
           "message": None, "managedStack": [], "readAddress": None,
           "reason": None, "rawTail": ""}

    cdb = cdb or find_cdb()
    if not cdb:
        out["reason"] = ("no_cdb —— 未找到 cdb.exe。安装："
                         "winget install --id Microsoft.WinDbg --accept-package-agreements "
                         "--accept-source-agreements")
        return out
    out["cdb"] = cdb

    sym = symbol_cache or os.path.join(os.environ.get("TEMP", "."), "blbridge_symbols")
    cmd = [cdb, "-z", path,
           "-y", "srv*%s*https://msdl.microsoft.com/download/symbols" % sym,
           "-c", _cdb_script]

    t0 = _time.time()
    try:
        # 文本输出；stderr 合并（cdb 把不少东西写在 stderr）
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout)
        text = (p.stdout or b"").decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        out["seconds"] = round(_time.time() - t0, 1)
        out["reason"] = ("timeout —— %ds 内没跑完（首次拉 MS 符号很慢是正常现象；"
                         "重跑一次通常就快了）" % timeout)
        return out
    except OSError as e:
        out["seconds"] = round(_time.time() - t0, 1)
        out["reason"] = "cdb 启动失败：%s" % e
        return out

    out["seconds"] = round(_time.time() - t0, 1)
    lines = text.splitlines()
    out["rawTail"] = "\n".join(lines[-12:])

    def pick(prefix):
        for ln in lines:
            s = ln.strip()
            if s.startswith(prefix):
                return s.split(":", 1)[1].strip()
        return None

    out["bucket"] = pick("FAILURE_BUCKET_ID:")
    out["symbol"] = pick("SYMBOL_NAME:")
    out["module"] = pick("MODULE_NAME:")
    out["image"] = pick("IMAGE_NAME:")
    out["readAddress"] = pick("READ_ADDRESS:")
    out["exceptionType"] = pick("Exception type:")
    out["message"] = pick("Message:")

    # 托管栈：`!clrstack` 的帧行。判据要**收紧**，否则会把别的行混进来。
    #
    # ★ 真实帧的形态是固定的：**两列 16 位十六进制地址 + 第三列才是符号**，例如
    #     `000000A50A7FEE40 00007FF7D6D6345B RTSCamera_CommandSystem!...OnRemoveBehavior()+0x2b`
    #   踩过一次：只判 `!` 且含 `+` ⇒ 把 `SYMBOL_NAME:  SomeMod!...Class.Boom+2b`
    #   这行（!analyze 的输出）也当成栈帧收了进去（自测⑩抓到）。
    #   ⇒ 正解：**要求前两列都是纯十六进制地址**，这才排得掉 `SYMBOL_NAME:` 那类行。
    _hex = re.compile(r"^[0-9A-Fa-f]{8,16}$")
    for ln in lines:
        s = ln.strip()
        if "!" not in s:
            continue
        parts = s.split(None, 2)
        if len(parts) == 3 and _hex.match(parts[0]) and _hex.match(parts[1]):
            out["managedStack"].append(parts[2].strip())
    # 去重保序，最多 20 帧
    seen, uniq = set(), []
    for f in out["managedStack"]:
        if f in seen:
            continue
        seen.add(f)
        uniq.append(f)
    out["managedStack"] = uniq[:20]

    out["ok"] = bool(out["bucket"] or out["symbol"] or out["managedStack"])
    if not out["ok"]:
        out["reason"] = ("no_output —— cdb 跑了 %ss 但没解析出崩溃点"
                         "（可能不是崩溃转储，或符号完全拿不到）" % out["seconds"])
    return out


_deep_cache = {}


def deep_for(path, cdb=None, timeout=CDB_TIMEOUT_SEC, use_cache=True):
    """带缓存地跑 deep（同一份 dump 在一次进程内只跑一次 —— cdb 要 5s，别重复付）。"""
    if use_cache and path in _deep_cache:
        return _deep_cache[path]
    r = run_cdb_deep(path, cdb=cdb, timeout=timeout)
    _deep_cache[path] = r
    return r


# ─────────────────────────────────────────────────────────────────────
# 与 BlBridge 状态对接（★ 本工具真正的增量）
# ─────────────────────────────────────────────────────────────────────

def bridge_status(log_dir=None):
    """读 BlBridge 状态文件。拿不到就返回 {}（本工具在游戏没跑过时也该能用）。"""
    if log_dir is None:
        try:
            from bl_common import default_log_dir
            log_dir = default_log_dir()
        except Exception:
            log_dir = os.path.join(
                os.path.expanduser("~"), "Documents",
                "Mount and Blade II Bannerlord", "BlBridge")
    p = os.path.join(log_dir, "bridge_status.json")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _battle_tail(path, n=3):
    """读战斗 JSONL 的**尾部** n 行 —— 崩溃前最后的遥测。仅标准库，不整文件载入。"""
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            back = min(size, 65536)
            fh.seek(size - back)
            chunk = fh.read().decode("utf-8", errors="replace")
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        return lines[-n:]
    except OSError:
        return []


def correlate(dump, status=None):
    """把一份 dump 接到 BlBridge 的会话上。**这就是四件外部工具都做不到的那一格。**

    对照三条：
      1. `pid` —— dump 文件名里的 pid 与状态文件的 pid 是否同一个进程
         （⇒ 这份 dump 是不是"我们桥接的那次运行"崩出来的）；
      2. `lastBattle` —— 崩的时候在打哪一场（⇒ 现场日志是哪个文件）；
      3. 战斗日志尾部 —— 最后一帧遥测（⇒ 崩在战斗的哪个阶段）。
    """
    status = status if status is not None else bridge_status()
    res = {"pid_match": None, "session": None, "battle": None, "battle_tail": []}
    if not status:
        res["note"] = "没有状态文件 ⇒ 只能给崩溃签名，无法关联到具体场次"
        return res

    spid = status.get("pid")
    if spid is not None and dump.get("pid") is not None:
        res["pid_match"] = (int(spid) == int(dump["pid"]))
    res["session"] = {
        "pid": spid,
        "state": status.get("state"),
        "cleanExit": status.get("cleanExit"),
        "missionInProgress": status.get("missionInProgress"),
        "missionsThisSession": status.get("missionsThisSession"),
        "lastBattle": status.get("lastBattle"),
        "processStartedUtc": status.get("processStartedUtc"),
    }
    lb = status.get("lastBattle")
    if lb:
        res["battle"] = lb
        res["battle_tail"] = _battle_tail(lb)
        res["battle_exists"] = os.path.isfile(lb)
    if res["pid_match"] is False:
        res["note"] = ("⚠ pid 不一致：dump 的 pid=%s，状态文件 pid=%s ⇒ "
                       "这份 dump 可能属于**另一次**运行，不可直接归因" % (dump.get("pid"), spid))
    return res


def build_report(limit=8, latest=False, pid=None, want_stack=False, path=None,
                 log_dir=None, deep=False, cdb=None):
    """汇总报告：崩溃清单 + 最近一次详情 + 与 BlBridge 会话的关联。

    deep=True 时额外跑 `cdb`（见 run_cdb_deep）拿托管栈与 FAILURE_BUCKET_ID。
    ⚠️ deep 是**可选升级**：找不到 cdb 时**不报错**，只在结果里给出 reason 与安装命令。
    """
    if path:
        dumps = [{"path": path, "pid": None, "exe": os.path.basename(path),
                  "size": os.path.getsize(path) if os.path.exists(path) else 0,
                  "mtime": os.path.getmtime(path) if os.path.exists(path) else 0,
                  "source": "explicit"}]
    else:
        dumps = list_dumps(log_dir=log_dir)
    if pid is not None:
        dumps = [d for d in dumps if d.get("pid") == int(pid)]
    if latest:
        dumps = dumps[:1]
    else:
        dumps = dumps[:limit]

    status = bridge_status(log_dir=log_dir)
    items = []
    for d in dumps:
        parsed = parse_dump(d["path"], want_stack=want_stack)
        parsed["mtime"] = d.get("mtime")
        parsed["pid"] = d.get("pid")
        parsed["correlate"] = correlate({**d, **parsed}, status)
        if deep:
            parsed["deep"] = deep_for(d["path"], cdb=cdb)
        items.append(parsed)

    out = {
        "ok": True,
        "dumpCount": len(items),
        "crashDir": _crash_dir(),
        "deep": bool(deep),
        "bridge": {
            "pid": status.get("pid"),
            "state": status.get("state"),
            "cleanExit": status.get("cleanExit"),
            "missionsThisSession": status.get("missionsThisSession"),
        } if status else None,
        "crashes": items,
        "werSignatures": read_wer_signature()[:limit] if not path else [],
    }
    # 一句话结论：给"这几次崩溃是不是同一个 bug"一个直接答案
    #
    # ★ deep 模式优先用 **FAILURE_BUCKET_ID**（Microsoft 的崩溃指纹）判定 ——
    #   它比异常代码精确得多：同样是 `0xC0000005`，`RTSCamera.OnRemoveBehavior`
    #   与 `0xC0000409 ucrtbase!abort` 是**完全不同的两个 bug**，
    #   而只看异常代码会把它们混为一谈（本项目 2026-10-06 实测踩到过）。
    if deep:
        buckets = [(c.get("deep") or {}).get("bucket") for c in items]
        buckets = sorted({b for b in buckets if b})
        if buckets:
            out["distinctBuckets"] = buckets
            out["sameBug"] = len(buckets) <= 1
    fams = [(c.get("exception") or {}).get("code") for c in items]
    out["distinctCodes"] = sorted({c for c in fams if c})
    if "sameBug" not in out:
        out["sameBug"] = (len(out["distinctCodes"]) <= 1) if out["distinctCodes"] else None
    return out


def fmt(report):
    L = []
    L.append("=" * 78)
    L.append("BlBridge 崩溃取证 · 共 %d 份 dump" % report["dumpCount"])
    if report.get("bridge"):
        b = report["bridge"]
        L.append("桥接会话: pid=%s state=%s cleanExit=%s 本会话场次=%s"
                 % (b.get("pid"), b.get("state"), b.get("cleanExit"),
                    b.get("missionsThisSession")))
    if report.get("distinctCodes"):
        L.append("异常代码: %s   ⇒ %s"
                 % (", ".join(report["distinctCodes"]),
                    "**同一个 bug**" if report.get("sameBug") else "**不止一个 bug**（需分别处理）"))
    if report.get("distinctBuckets"):
        L.append("崩溃指纹: %d 种" % len(report["distinctBuckets"]))
        for b in report["distinctBuckets"]:
            L.append("   · %s" % b)
    L.append("=" * 78)
    for c in report["crashes"]:
        e = c.get("exception")
        L.append("")
        L.append("■ %s  (%.1f MB)  pid=%s" % (c["file"], c.get("size", 0) / 1048576, c.get("pid")))
        if c.get("error"):
            L.append("   ✗ %s" % c["error"])
            continue
        if not e:
            L.append("   ⚠ 无异常流 —— 无法归因（手动 dump / 被强杀）")
        else:
            L.append("   异常 : %s  %s" % (e["code"], e["name"]))
            L.append("   归属 : %s" % (e.get("address_symbol") or "⚠ " + (c.get("blame") or "?")))
            L.append("   地址 : %s" % e["address_hex"])
            if e.get("access_type"):
                L.append("   访问 : %s %s  → %s" % (e["access_type"], e["access_target"],
                                                   e.get("access_target_symbol")))
            regs = e.get("registers") or {}
            if regs.get("Rip"):
                rip = regs["Rip"]
                L.append("   RIP  : %s   RSP: %s" % (rip, regs.get("Rsp")))
                # ⚠️ **RIP=0 而崩溃地址≠0 ⇒ 这份存储的线程上下文不是故障现场**，
                #    此时列出来的寄存器会误导判读（会说成"跳到空指针"，而真凶是别的地址）。
                #    实测本机 7 份 dump：只有 Launcher 那份带可用寄存器，其余 6 份 RIP 全 0。
                #    ⇒ 明确写出来，别让读者把"缺数据"当成"结论"。
                if rip == "0x0000000000000000" and e.get("address"):
                    L.append("          ⚠ RIP=0 但故障地址 %s ≠ 0 ⇒ **线程上下文不可用**，"
                             "上列寄存器与 RSP 不可用于判读；请以『地址/归属/访问』字段为准"
                             % e["address_hex"])
        co = c.get("correlate") or {}
        if co.get("session"):
            s = co["session"]
            mark = {True: "✅ 同一进程", False: "❌ 不是同一进程", None: "?"}.get(co.get("pid_match"))
            L.append("   会话 : %s（dump pid=%s vs 状态 pid=%s）" % (mark, c.get("pid"), s.get("pid")))
            if s.get("lastBattle"):
                L.append("   场次 : %s" % os.path.basename(s["lastBattle"]))
        for ln in (co.get("battle_tail") or [])[-2:]:
            L.append("   末帧 : %s" % ln[:150])
        if c.get("escalate"):
            L.append("   升级 : %s" % c["escalate"])
        # ── deep 结果（cdb）──
        dp = c.get("deep")
        if dp is not None:
            if dp.get("ok"):
                L.append("   ── deep（cdb %.1fs）──" % (dp.get("seconds") or 0))
                if dp.get("bucket"):
                    L.append("   指纹 : %s" % dp["bucket"])
                if dp.get("exceptionType"):
                    L.append("   托管 : %s  %s" % (dp["exceptionType"], dp.get("message") or ""))
                if dp.get("symbol"):
                    L.append("   符号 : %s" % dp["symbol"])
                if dp.get("readAddress"):
                    L.append("   读址 : %s" % dp["readAddress"])
                for f in (dp.get("managedStack") or [])[:6]:
                    L.append("     ↳ %s" % f)
            else:
                # ⚠️ **环境问题**与**没有崩溃**必须分清 —— 否则会把"没装调试器"读成"没崩"
                L.append("   ── deep 未取到 ──")
                L.append("   原因 : %s" % (dp.get("reason") or "?"))
                if dp.get("cdb"):
                    L.append("   cdb  : %s" % dp["cdb"])
        if c.get("stack"):
            fr = c["stack"]["frames"][:6]
            if fr:
                L.append("   栈(启发式): %s" % ", ".join(f["symbol"] for f in fr))
    if report.get("werSignatures"):
        L.append("")
        L.append("── WER 已解析签名（交叉验证用）──")
        for w in report["werSignatures"][:8]:
            L.append("   %s  %s" % (w.get("code"), w.get("module")))
    return "\n".join(L)


def main(argv=None):
    # 输出统一 UTF-8（见 bl_common.safe_streams 的 docstring：写入编码 ≠ 读取编码）。
    # ⚠️ 2026-10-06 补：本模块原先**没调它** ⇒ 作为 CLI 裸跑必挂：
    #    非 --json 分支走 `fmt(rep)`，而报告文本里含 `⇒`/`⚠`，
    #    Python 默认按 locale(cp936) 写 stdout，cp936 无这些码位
    #    ⇒ `UnicodeEncodeError` 崩在 `sys.stdout.write`（**看着像工具坏了，实为编码**）。
    #    实测：`python tools/bl_crash.py` 在中文 Windows 上 exit=1 且无任何输出。
    #    注意 MCP 路径不受影响（`bl_mcp.py` 入口自己钉了 UTF-8），只有 CLI 裸跑会踩。
    import bl_common
    bl_common.safe_streams()

    argv = list(sys.argv[1:] if argv is None else argv)
    kw = {"limit": 8, "latest": False, "want_stack": False, "pid": None,
          "path": None, "json": False, "deep": False, "cdb": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            kw["json"] = True
        elif a == "--latest":
            kw["latest"] = True
        elif a == "--limit":
            i += 1
            kw["limit"] = int(argv[i])
        elif a == "--stack":
            kw["want_stack"] = True
        elif a == "--deep":
            kw["deep"] = True
        elif a == "--cdb":
            i += 1
            kw["cdb"] = argv[i]
        elif a == "--pid":
            i += 1
            kw["pid"] = int(argv[i])
        elif a == "--path":
            i += 1
            kw["path"] = argv[i]
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            print("未知参数：%s\n" % a)
            print(__doc__)
            return 2
        i += 1

    rep = build_report(limit=kw["limit"], latest=kw["latest"], pid=kw["pid"],
                       want_stack=kw["want_stack"], path=kw["path"],
                       deep=kw["deep"], cdb=kw["cdb"])
    if kw["json"]:
        sys.stdout.write(json.dumps(rep, ensure_ascii=False, indent=1) + "\n")
    else:
        sys.stdout.write(fmt(rep) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
