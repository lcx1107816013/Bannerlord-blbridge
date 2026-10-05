#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BlBridge 异常采集口径说明 + 记录读取（宿主侧，只读，仅标准库）。

## 为什么需要它（一手证据，2026-10-06 实测）

`<日志目录>\\exceptions.jsonl` 是 `ExceptionProbe`（零 Harmony 的 FirstChance 订阅器）
落盘的**侧信道**。**它的记录口径有多处反直觉**，直接读文件会得出**错误结论** ——
本工具就是为了不让人踩这些坑而存在的。

### 坑 1：文件里的条数 != 出现次数（差一个数量级）

实测：`CryptographicException` 在 jsonl 里只有 **4 行**，
而真实频次是 **44 次**（`bl_exceptions` 的 `top[].count`）。

**原因**（`src/ExceptionProbe.cs`）：
  * 去重键 = `(类型全名 + "|" + 栈首帧)`            —— `:194`
  * **只在 `count == 1` 时落盘**，之后只累加内存计数 —— `:198`
  * ⇒ 同一键不论出现多少次，jsonl 里**永远只有 1 行**

⇒ **jsonl 是"首次出现"明细通道，不是频率表。**
   要频率必须读 `bl_exceptions` 的 `top[]`（内存态，进程内累积），
   或读 `commands/done/*.json` 里历史响应的 `count` 字段（见 `bl_ipc_replay`）。

### 坑 2：`seq` 不是序号，`tick` 不是时间

  * `seq`  = **排空那一刻的全局 `_totalSeen` 快照**（`:218`）——
            所以**多条记录可以共享同一个 seq**（实测 7 条共享 seq=55）。
            它**不是**"第 55 个异常"，也**不是**该异常的计数。
  * `tick` = 该键**首次排空**时的 `_tickCounter`（`:213,219`），**不是**发生时刻。
  * 两个字段**都不能**用来排序或算频率。

### 坑 3：`stackHead` 只有**一帧**，而且常常是"抛异常的助手方法"

实测 `CryptographicException` 的 `stackHead` =
`at System.Security.Cryptography.CryptographicException.ThrowCryptographicException(Int32 hr)`
—— **这是 BCL 的抛助记帧，与调用者无关**。

**原因**：回调里只取栈的**第一帧**（`:155-162`，"回调里不做大字符串操作"）。
⚠️ 而 `:155` 的注释写"完整栈在主线程排空时按需再取" ——
**但全文件 `StackTrace` 只出现一次**，排空路径里没有那段代码。
⇒ **调用者信息在采集那一刻就被丢弃，事后无法恢复。**

⇒ **推论**：若某异常的 `stackHead` 落在 BCL 抛助记帧上（`ThrowXxxException`），
   **它的来源在当前数据里不可判定** —— 任何进一步归因都是编造。

### 坑 4：`t=session` 分段 = 游戏进程重启

每个游戏进程启动会追加一条 `{"t":"session", ...}`，**并把内存计数清零**。
⇒ 跨段累加 `seen` 是**错的**（那是多个进程各自的计数）。
   实测：4 段 / 4 个 pid。

### 坑 5：采集有结构性盲区（不是"没抓到＝没发生"）

`ExceptionProbe.cs:39` 自己写明：
  * **只记托管异常**（FirstChance，含**被 catch 掉的**）；
  * **JIT 期失败**与**原生崩溃**结构上抓不到 ⇒ 那两类必须用 `bl_crash`（minidump）。

### 坑 6：`dropped > 0` 时"计数仍准、明细不全"

环形队列上限 4096，满了丢弃并计数（`:144-149`），
`Summary()` 会在 `dropped > 0` 时显式给 `warning`（`:259-264`）—— **不静默**。

## 边界

- **只读**：不写、不删、不改。
- 本工具**只解释口径 + 读明细**；频率看 `bl_exceptions`，
  历史响应的 `count` 看 `bl_ipc_replay`，**不重复造**。
- 无法判定的来源**明确说"不可判定"**，不给编造的归因。
"""
import collections
import io
import json
import os
import time

import bl_common

# 硬编码的采集边界（源码 ExceptionProbe.cs 已核实，改源码时这里要同步）
BLIND_SPOTS = (
    "只记**托管异常**（FirstChance，含被 catch 掉的）",
    "**JIT 期失败**抓不到（不在任何被 patch 的方法体内）",
    "**原生崩溃**抓不到（托管处理器结构上不可见）⇒ 那两类用 bl_crash / WER minidump",
)
MAX_QUEUED = 4096          # ExceptionProbe 的环形上限
STACKHEAD_FRAMES = 1       # 只取第一帧（ExceptionProbe.cs:155-162）

# "抛助记帧"的判据：栈首帧落在 BCL 的 ThrowXxxException 上 ⇒ 与调用者无关
_THROWER_HINTS = ("ThrowCryptographicException", "ThrowHelper",
                  "ThrowArgumentException", "ThrowIOException")


def exceptions_path(log_dir=None):
    """`<日志目录>\\exceptions.jsonl`。"""
    return os.path.join(log_dir or bl_common.default_log_dir(), "exceptions.jsonl")


def read_records(path=None, log_dir=None):
    """读 jsonl 全部记录（不解析失败即报错，不静默）。返回 (records, stats)。"""
    p = path or exceptions_path(log_dir)
    stats = {"path": p, "exists": os.path.isfile(p), "lines": 0,
             "bad": 0, "badSamples": [], "sessions": 0, "exceptions": 0}
    if not stats["exists"]:
        return [], stats
    records = []
    with io.open(p, "r", encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            stats["lines"] += 1
            try:
                o = json.loads(line)
            except ValueError as exc:
                stats["bad"] += 1
                if len(stats["badSamples"]) < 3:
                    stats["badSamples"].append(line[:160])
                continue
            if not isinstance(o, dict):
                stats["bad"] += 1
                if len(stats["badSamples"]) < 3:
                    stats["badSamples"].append(line[:160])
                continue
            records.append(o)
            if o.get("t") == "session":
                stats["sessions"] += 1
            elif o.get("t") == "exception":
                stats["exceptions"] += 1
    return records, stats


def _is_thrower_frame(head):
    """判断 stackHead 是否落在 BCL 抛助记帧上（⇒ 来源不可判定）。"""
    if not head:
        return False
    return any(h in head for h in _THROWER_HINTS)


def build_report(log_dir=None, path=None, type_filter=None, limit=20, explain=True):
    """读取并解释异常记录。

    参数：
        type_filter  只列这个类型（子串匹配，如 "Cryptographic"）。
        limit        最多列几条明细。
        explain      是否附口径说明（默认 True —— 本工具的主价值就在这）。
                     ⚠️ `explain=False` 时 **`report` 是空串**（调用方只要机器字段）。
                     所以**断言"文本里说了什么"必须开 explain**
                     —— 本工具自测 2026-10-06 踩过：用 `explain=False` 却断言 report
                     内容 ⇒ 断言恒失败，且看着像功能缺失。
    """
    records, stats = read_records(path=path, log_dir=log_dir)

    # 分段：每个 t=session 开一段
    segments = []
    cur = None
    for r in records:
        if r.get("t") == "session":
            cur = {"sessionUtc": r.get("utc"), "note": r.get("note"), "events": []}
            segments.append(cur)
        else:
            if cur is None:                 # 段前记录（少见）也要留
                cur = {"sessionUtc": None, "note": "(段前记录)", "events": []}
                segments.append(cur)
            cur["events"].append(r)

    # 按类型的**行数**统计（⚠️ 这不是频次！）
    by_type = collections.Counter(e.get("type") for s in segments for e in s["events"])
    # 按 (type, stackHead) 组合
    by_key = collections.Counter(
        "%s|%s" % (e.get("type"), e.get("stackHead")) for s in segments for e in s["events"])

    # 选中要展示的
    shown = []
    for si, s in enumerate(segments):
        for e in s["events"]:
            if type_filter and type_filter.lower() not in (e.get("type") or "").lower():
                continue
            shown.append({
                "segment": si,
                "sessionUtc": s.get("sessionUtc"),
                "type": e.get("type"),
                "message": e.get("message"),
                "stackHead": e.get("stackHead"),
                "seq": e.get("seq"),
                "tick": e.get("tick"),
                "stackHeadIsThrower": _is_thrower_frame(e.get("stackHead")),
            })
    total_shown = len(shown)
    shown = shown[:max(1, int(limit))]

    # 每段内的"行数"（用于解释「为什么 N 段 * 1 行」）
    per_seg = []
    for si, s in enumerate(segments):
        types = collections.Counter(e.get("type") for e in s["events"])
        per_seg.append({"segment": si, "sessionUtc": s.get("sessionUtc"),
                        "rows": len(s["events"]), "byType": dict(types)})

    report = _render(stats, segments, by_type, by_key, shown, total_shown,
                     per_seg, type_filter, explain) if explain else ""

    return {
        "ok": True,
        "path": stats["path"],
        "exists": stats["exists"],
        "rows": stats["lines"],
        "badRows": stats["bad"],
        "badSamples": stats["badSamples"],
        "sessions": stats["sessions"],
        "exceptionRows": stats["exceptions"],
        "segments": per_seg,
        "rowsByType": dict(by_type),
        "rowsByKey": dict(by_key),
        "matched": total_shown,
        "returned": len(shown),
        "items": shown,
        # 口径元数据（给机器消费者，不必解析 report 文本）
        "semantics": {
            "rowsAreNotCounts": True,
            "why": "去重键 =(类型|栈首帧)，只在 count==1 时落盘 ⇒ 同键永远只有 1 行",
            "seqMeans": "排空那刻的全局 _totalSeen 快照，不是序号、不是该异常计数",
            "tickMeans": "该键首次排空时的 tickCounter，不是发生时刻",
            "stackHeadFrames": STACKHEAD_FRAMES,
            "fullStackAvailable": False,
            "maxQueued": MAX_QUEUED,
            "blindSpots": list(BLIND_SPOTS),
            "freqSource": "bl_exceptions 的 top[].count（内存态）；历史响应见 bl_ipc_replay",
        },
        "report": report,
    }


def _render(stats, segments, by_type, by_key, shown, total_shown, per_seg,
            type_filter, explain):
    L = []
    L.append("BlBridge 异常采集（口径说明 + 明细）")
    L.append("=" * 74)
    if not stats["exists"]:
        L.append("[!] 文件不存在：%s" % stats["path"])
        L.append("    => 这**不代表没发生异常**。可能：游戏从未加载 BlBridge、"
                 "或采集器未安装（看 bl_exceptions 的 installed）。")
        return "\n".join(L)

    L.append("文件       : %s" % stats["path"])
    L.append("总行数     : %d" % stats["lines"])
    L.append("进程段数   : %d  <- 每个游戏进程一段（t=session）" % stats["sessions"])
    L.append("异常行数   : %d" % stats["exceptions"])
    if stats["bad"]:
        L.append("[!] 坏行     : %d" % stats["bad"])
        for s in stats["badSamples"]:
            L.append("      %s" % s)
    L.append("")

    L.append("-" * 74)
    L.append("★ 口径（读之前必须先看，否则会得出错误结论）")
    L.append("-" * 74)
    L.append("1) **行数 != 出现次数**：去重键 =(类型|栈首帧)，且**只在首次出现时落盘**")
    L.append("   => 同一异常出现 44 次，这里也只有 1 行。实测：Cryptographic 4 行 / 真实 44 次。")
    L.append("   => 要**频次**请看 bl_exceptions 的 top[].count，或 bl_ipc_replay 取历史响应。")
    L.append("2) `seq` = 排空那刻的**全局 _totalSeen 快照**（不是序号、不是该异常计数）")
    L.append("   => 多条记录**可以共享同一个 seq**。不能用它排序或计数。")
    L.append("3) `tick` = 该键**首次排空**时的 tick（不是发生时刻）。")
    L.append("4) `stackHead` **只有 %d 帧**，且常常是 BCL 的**抛助记帧**（与调用者无关）"
             % STACKHEAD_FRAMES)
    L.append("   => 若某条的 stackHead 落在 `ThrowXxxException` 上，它的**来源在当前数据里不可判定**。")
    L.append("   => 源码注释称'排空时按需取完整栈'，但**该代码不存在**（全文件 StackTrace 仅 1 处）。")
    L.append("5) `t=session` 分段 = **游戏进程重启**（内存计数随之清零）⇒ **跨段累加 seen 是错的**。")
    L.append("6) 采集有结构性盲区：")
    for b in BLIND_SPOTS:
        L.append("   - %s" % b)
    L.append("7) 环形上限 %d；`dropped > 0` 时**计数仍准、明细不全**（Summary 会显式 warning）。"
             % MAX_QUEUED)
    L.append("")

    L.append("-" * 74)
    L.append("每段的异常类型分布（行数，**不是频次**）")
    L.append("-" * 74)
    for s in per_seg:
        if not s["byType"]:
            continue
        L.append("段 %d  (%s)  行数=%d" % (s["segment"], s["sessionUtc"], s["rows"]))
        for t, c in sorted(s["byType"].items(), key=lambda kv: -kv[1]):
            L.append("    %-56s %d 行" % (t, c))
    L.append("")

    if total_shown == 0:
        L.append("[!] 没有匹配 `%s` 的记录行。" % type_filter if type_filter
                 else "[i] 没有异常记录行。")
        return "\n".join(L)

    L.append("-" * 74)
    if type_filter:
        L.append("匹配 `%s` 的记录：%d 行（显示前 %d）" % (type_filter, total_shown, len(shown)))
    else:
        L.append("异常记录：%d 行（显示前 %d）" % (total_shown, len(shown)))
    L.append("-" * 74)
    for it in shown:
        L.append("[段 %d @ %s] %s" % (it["segment"], it["sessionUtc"], it["type"]))
        L.append("   message : %s" % (it["message"] or "").strip()[:200])
        L.append("   seq/tick: %s / %s   <- seq 是全局快照，不是序号" % (it["seq"], it["tick"]))
        L.append("   stackHead: %s" % (it["stackHead"] or "(无)"))
        if it["stackHeadIsThrower"]:
            L.append("   [!] 该栈首帧是**抛助记帧** => 调用者不在记录里，**来源不可判定**。")
            L.append("       要归因需补采集（多帧栈）或做 A/B（如 bl_launch_game 的 excludeModules）。")
        L.append("")

    # ★ 尾部汇总：即使调用方**没按类型过滤**，也要把"哪些条目的来源不可判定"点出来。
    #   实测踩过（2026-10-06，本工具自测 ④ 抓到）：最初只在**明细行内**给提示，
    #   而不过滤时读者更容易把整份列表当成"都是可归因的" ⇒ 必须在结尾再汇总一次。
    undecidable = [it for it in shown if it["stackHeadIsThrower"]]
    if undecidable:
        types = sorted(set(it["type"] for it in undecidable))
        L.append("-" * 74)
        L.append("[!] 本次列出的 %d 条里，有 **%d 条来源不可判定**（栈首帧是抛助记帧）："
                 % (len(shown), len(undecidable)))
        for t in types:
            L.append("      - %s" % t)
        L.append("    这些类型**不能**从当前记录归因到发起者。要归因请：")
        L.append("      · 补采集多帧栈（改 ExceptionProbe），或")
        L.append("      · 做 A/B：bl_launch_game 的 excludeModules（同一次启动只差一个模块）。")
    return "\n".join(L)
