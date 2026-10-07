#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
崩溃守卫账本（crashguard.jsonl）读取 + 修复建议 —— 宿主侧，只读，仅标准库。

## 它补的是什么

三段合起来才叫"跳过崩溃 + 检测 + 提修复"：

| 段 | 产物 | 谁做 |
|---|---|---|
| MOD 侧：**阻止崩溃**（吞异常） | `<LogDir>\\crashguard.jsonl` | `src/CrashGuard.cs`（Harmony Finalizer） |
| MOD 侧：**观察全部异常** | `<LogDir>\\exceptions.jsonl` | `src/ExceptionProbe.cs`（FirstChance） |
| **MCP 侧：读账本 + 提修复** | 本工具 | ← 你在这里 |

★ 关键区分：`ExceptionProbe` **能观察但不能阻止**（FirstChance 是通知，返回值被忽略）；
`CrashGuard` 是**唯一能阻止传播**的钩子。所以这个账本是"**我们放过了什么**"的唯一记录 ——
它回答的问题与 `bl_exceptions` 不同：

  • `bl_exceptions` 答："游戏里发生过哪些异常"（含被 catch 的，全集）
  • `bl_crashguard` 答："**哪些异常本来会杀掉游戏、被我们吞掉了**"（以及**哪些我们不敢吞**）

## ⚠️ 读结论前必须知道的口径（否则会误判）

1. **`action: "swallow"` 不等于"问题已解决"**。吞掉只意味着"游戏没死"，
   那个方法**没做完它该做的事** ⇒ 可能已经留下静默损坏（存档不一致/AI 卡死/数值错乱）。
   本工具的每条 `swallow` 都据此标注，**不会**把它报成"已修复"。
2. **`reason` 是判定依据，不是装饰**：
   | reason | 含义 |
   |---|---|
   | `swallowed` | 真的吞了（在三道闸门都通过的前提下） |
   | `fatal_passthrough` | **致命异常，故意放行**（OOM/栈溢出等 —— 吞它之后的执行没有意义） |
   | `breaker_open` | **熔断**：同一签名反复出现 ⇒ 判定结构性故障，**主动停止吞** |
   | `quota_exhausted` | **配额用尽** ⇒ 之后一律放行 |
   | `unavailable` / `install_failed` / `target_failed` | 守卫**没装上**（如 Harmony 缺失） |
   | `installed` | 装载成功（信息行，不是异常） |
3. **熔断与配额触发 = 坏消息，不是好消息**。它意味着守卫**已经退化成"看着游戏崩"**。
   本工具会把它们放在报告**最前面**并明确要求人工介入。
4. **`dropped > 0`**：账本队列满而丢弃过记录 ⇒ **计数不全**（如实告警，不静默）。
5. **没装守卫时文件不存在** ⇒ 返回 `enabled: false`，**不**报"没有崩溃"。
   "没记录"与"没发生"是两件事。
"""
import collections
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bl_common                                      # noqa: E402

GUARD_FILE = "crashguard.jsonl"

# reason → 人话 + 严重度。**顺序即报告顺序**（越靠前越要紧）。
#
# ⚠️ severity 的判据（不是拍的）：
#   • critical = 守卫已失去保护能力，或进程级问题被放过 ⇒ **必须人工介入**
#   • warn     = 有损坏风险，但游戏仍在受保护状态
#   • info     = 装载/状态信息
REASON_INFO = {
    "breaker_open": {
        "sev": "critical",
        "text": "熔断已触发：同一异常签名反复出现，守卫**主动停止吞它**",
        "advice": ("这通常意味着那条路径**结构性坏了**（不是偶发）。继续吞只会把"
                   "「立即崩溃」换成「卡死 + 静默数据损坏」，所以守卫选择放行。"
                   "请用 frames 里的调用者定位根因；恢复需**修好根因后重启游戏**。"),
    },
    "quota_exhausted": {
        "sev": "critical",
        "text": "会话吞异常配额已用尽，之后一律放行",
        "advice": ("配额是防「无限假装没事」的闸门。它触发说明本次会话异常**很多**，"
                   "游戏可能已在带病运行 ⇒ 建议尽快存档并重启，再按 type/frames 归因。"),
    },
    "install_failed": {
        "sev": "critical",
        "text": "崩溃守卫**安装失败**（异常）",
        "advice": "见 message。守卫未生效 ⇒ 本会话没有崩溃保护。",
    },
    "unavailable": {
        "sev": "warn",
        "text": "崩溃守卫**未生效**（Harmony 不可用）",
        "advice": ("BlBridge 用反射挂 Finalizer，Harmony 缺失就优雅退化 —— "
                   "这是刻意设计（不把可选能力变成硬依赖）。装 Harmony 后重启即可。"),
    },
    "target_failed": {
        "sev": "warn",
        "text": "某个挂载目标失败（该路径无保护）",
        "advice": "多为游戏版本导致方法签名变化。见 message。",
    },
    "fatal_passthrough": {
        "sev": "warn",
        "text": "**致命异常被故意放行**（OOM / 栈溢出 / 类型初始化失败等）",
        "advice": ("这类异常表示运行时已不可信，吞掉它之后的执行没有意义，"
                   "而且会把明确崩溃变成随机静默错误 ⇒ 守卫**永不吞**它们。"
                   "看到这条说明游戏很可能仍然崩了 —— 请用 `bl_crash` 看 minidump。"),
    },
    "swallowed": {
        "sev": "warn",
        "text": "异常被吞（游戏没死，但那个方法**没做完它该做的事**）",
        "advice": ("⚠️ 这不是「已修复」。半完成的调用可能已造成静默损坏"
                   "（存档不一致 / AI 卡死 / 数值错乱），而且**不会报错**。"
                   "请按 target/frames 归因，并检查相关功能是否表现异常。"),
    },
    "installed": {
        "sev": "info",
        "text": "守卫装载成功",
        "advice": "无。",
    },
}


def guard_path(log_dir=None):
    return os.path.join(log_dir or bl_common.default_log_dir(), GUARD_FILE)


def read_records(path=None, log_dir=None):
    """读账本全部记录。返回 (records, stats)。坏行**计数并带原文**，不静默跳过。"""
    p = path or guard_path(log_dir)
    stats = {"path": p, "exists": os.path.isfile(p), "lines": 0, "bad": 0,
             "badSamples": [], "sessions": 0}
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
            except ValueError:
                stats["bad"] += 1
                if len(stats["badSamples"]) < 3:
                    stats["badSamples"].append(line[:160])
                continue
            if not isinstance(o, dict):
                # ⚠️ 非对象的合法 JSON（如 `[1,2,3]`、`"str"`）**同样要带原文** ——
                #    它与解析失败是两种坏行，但都属"读不懂这行"。
                #    实测对齐既有先例（`bl_selftest` 的账本判据）：
                #    两种坏行都应进 badSamples，否则只报数字、看不到是哪种坏法。
                stats["bad"] += 1
                if len(stats["badSamples"]) < 3:
                    stats["badSamples"].append(line[:160])
                continue
            records.append(o)
    return records, stats


def _session_split(records):
    """按 runToken 切段（每次游戏进程一个 runToken）。"""
    segs = collections.OrderedDict()
    for r in records:
        tok = r.get("runToken") or "(未知)"
        segs.setdefault(tok, []).append(r)
    return segs


def build_report(log_dir=None, path=None, limit=20, force=None):
    """核心：读账本 → 分组 → 给修复建议。

    force=None 时自动判断守卫是否启用（看状态文件与账本是否存在）。
    """
    records, stats = read_records(path=path, log_dir=log_dir)

    # 守卫到底启没启用？三重来源，避免"文件不存在"被误读成"没崩溃"
    enabled = None
    install_err = None
    try:
        st = bl_common.status_path(log_dir) if hasattr(bl_common, "status_path") else None
        if st is None:
            st = os.path.join(log_dir or bl_common.default_log_dir(), "bridge_status.json")
        if os.path.isfile(st):
            with io.open(st, "r", encoding="utf-8-sig", errors="replace") as fh:
                status = json.load(fh)
            cg = status.get("crashGuard") or {}
            if isinstance(cg, dict) and cg:
                enabled = bool(cg.get("enabled"))
                install_err = cg.get("installError") or None
    except Exception:                                     # noqa: BLE001
        pass
    if force is not None:
        enabled = force

    by_reason = collections.Counter(r.get("reason") for r in records)
    by_type = collections.Counter(r.get("type") for r in records if r.get("type"))
    by_target = collections.Counter(r.get("target") for r in records if r.get("target"))

    swallowed = [r for r in records if r.get("action") == "swallow"]
    passed = [r for r in records if r.get("action") == "pass"]

    # 需要人看的（critical 优先，其次 warn），按 reason 分桶
    attention = []
    for r in records:
        info = REASON_INFO.get(r.get("reason") or "")
        if not info or info["sev"] == "info":
            continue
        attention.append({"sev": info["sev"], "reason": r.get("reason"),
                          "text": info["text"], "advice": info["advice"],
                          "type": r.get("type"), "message": r.get("message"),
                          "target": r.get("target"), "frames": r.get("frames"),
                          "utc": r.get("utc"), "runToken": r.get("runToken")})
    order = {"critical": 0, "warn": 1, "info": 2}
    attention.sort(key=lambda a: order.get(a["sev"], 3))

    # 每条"吞掉的"都附一条修复建议（复用词典，若可用）
    repairs = []
    try:
        import bl_lexicon as _lx
        lex, lst = _lx.load_lexicon()
        if lex is not None:
            for r in swallowed:
                q = _lx.describe(lex, exc_type=r.get("type"),
                                 text=" ".join(filter(None, [r.get("type"), r.get("message"),
                                                             r.get("frames")])),
                                 lang="zh", limit=3)
                repairs.append({
                    "type": r.get("type"),
                    "target": r.get("target"),
                    "exception": (q.get("exception") or {}),
                    "matches": q.get("matches") or [],
                })
            lexicon_state = "loaded"
        else:
            lexicon_state = "missing: " + (lst.get("reason") or "")
    except Exception as exc:                              # noqa: BLE001
        lexicon_state = "error: %s: %s" % (type(exc).__name__, exc)

    segs = _session_split(records)

    return {
        "ok": True,
        "path": stats["path"],
        "exists": stats["exists"],
        "enabled": enabled,
        "installError": install_err,
        "lines": stats["lines"],
        "badRows": stats["bad"],
        "badSamples": stats["badSamples"],
        "sessions": len(segs),
        "swallowed": len(swallowed),
        "passedThrough": len(passed),
        "byReason": dict(by_reason),
        "byType": dict(by_type),
        "byTarget": dict(by_target),
        "attention": attention[:max(1, int(limit))],
        "attentionTotal": len(attention),
        "repairs": repairs[:max(1, int(limit))],
        "lexicon": lexicon_state,
        "semantics": {
            "swallowIsNotFix": True,
            "why": ("吞掉只保证「游戏没死」；被吞的方法没做完它该做的事，"
                    "调用方以为成功了 ⇒ 可能已留下不报错的静默损坏。"),
            "reasonMeans": dict((k, v["text"]) for k, v in REASON_INFO.items()),
            "emptyVsDisabled": ("文件不存在**不等于**没有崩溃 —— 可能是守卫没启用"
                                "（默认就是关闭的）。以 enabled 字段为准。"),
            "droppedMeans": "队列满而丢弃过记录 ⇒ 计数不全。",
        },
    }


def _render(res):
    L = []
    L.append("BlBridge 崩溃守卫账本（跳过崩溃的记账 + 修复建议）")
    L.append("=" * 74)
    if res.get("enabled") is False:
        L.append("[i] 崩溃守卫**未启用**（默认即关闭）。")
        L.append("    开关：`blbridge_game.json` 里 `crashGuardEnabled: true`，改完**重启游戏**。")
        if res.get("installError"):
            L.append("    安装问题：" + str(res["installError"]))
        L.append("")
        if not res.get("exists"):
            L.append("    ⇒ 因此**不存在**账本文件。这**不代表**没发生崩溃。")
            return "\n".join(L)
    elif res.get("enabled") is None and not res.get("exists"):
        L.append("[!] 既没有状态文件也没有账本 —— 无法判断守卫是否启用。")
        L.append("    这**不等于**「没有崩溃」；请先确认 BlBridge 是否加载（`bl_status`）。")
        return "\n".join(L)

    L.append("账本        : %s" % res["path"])
    L.append("行数        : %d（进程段 %d）" % (res["lines"], res["sessions"]))
    if res["badRows"]:
        L.append("[!] 坏行    : %d（已计数并带原文，未静默跳过）" % res["badRows"])
        for s in res["badSamples"]:
            L.append("      %s" % s)
    L.append("被吞（swallow）: %d    放行（pass）: %d" % (res["swallowed"], res["passedThrough"]))
    if res["byReason"]:
        L.append("按 reason   : %s" % ", ".join("%s=%d" % kv for kv in sorted(res["byReason"].items())))
    L.append("")

    if res["attentionTotal"]:
        L.append("-" * 74)
        L.append("★ 需要人看的 %d 条（critical 在前）" % res["attentionTotal"])
        L.append("-" * 74)
        for a in res["attention"]:
            L.append("[%s] %s" % (a["sev"].upper(), a["text"]))
            if a.get("type"):
                L.append("      异常  : %s" % a["type"])
            if a.get("message"):
                L.append("      消息  : %s" % (a["message"] or "").strip()[:200])
            if a.get("target"):
                L.append("      目标  : %s" % a["target"])
            if a.get("frames"):
                L.append("      栈前几帧:")
                for ln in (a["frames"] or "").split("\n")[:4]:
                    L.append("        %s" % ln.strip())
            L.append("      → 建议: %s" % a["advice"])
            L.append("")

    if res["repairs"]:
        L.append("-" * 74)
        L.append("★ 按类型给出的修复建议（词条：%s）" % res["lexicon"])
        L.append("-" * 74)
        for rp in res["repairs"]:
            e = rp.get("exception") or {}
            L.append("异常类型: %s" % (rp.get("type") or "(未知)"))
            if rp.get("target"):
                L.append("  出现于 : %s" % rp["target"])
            if e.get("displayName") or e.get("description"):
                L.append("  这是什么: %s" % (e.get("displayName") or ""))
                L.append("  说明    : %s" % (e.get("description") or ""))
                for s in (e.get("suggestions") or [])[:4]:
                    L.append("    · %s" % (s.get("text") or ""))
            elif e.get("found") is False:
                L.append("  [i] 词典里没有这个异常类型的词条（覆盖范围所限，不是失败）")
            for m in (rp.get("matches") or [])[:2]:
                if m.get("action"):
                    L.append("  诊断建议: %s" % m["action"])
            L.append("")
    else:
        L.append("-" * 74)
        if res["swallowed"] == 0:
            L.append("没有「被吞掉的异常」记录。")
            L.append("  [!] 注意：这**不等于**「游戏没出问题」，只说明守卫没吞过东西")
            L.append("      （可能守卫刚装、或本次会话确实没触发）。")
        else:
            L.append("有 %d 条被吞记录，但**未能给出修复建议**（词条不可用：%s）。"
                     % (res["swallowed"], res["lexicon"]))
        L.append("")

    L.append("-" * 74)
    L.append("★ 口径（读结论前必看）")
    L.append("-" * 74)
    L.append("1) action=swallow **不等于已修复** —— 游戏没死，但那个方法没做完它该做的事，")
    L.append("   可能已留下**不报错**的静默损坏（存档不一致 / AI 卡死 / 数值错乱）。")
    L.append("2) reason=breaker_open / quota_exhausted 是**坏消息**：守卫已停止保护。")
    L.append("3) reason=fatal_passthrough 说明游戏**很可能仍然崩了** ⇒ 用 bl_crash 看 dump。")
    L.append("4) 「没有记录」与「没有发生」是两件事：守卫**默认关闭**，以 enabled 为准。")
    return "\n".join(L)


def main(argv=None):
    import argparse
    try:
        bl_common.safe_streams()
    except Exception:                                     # noqa: BLE001
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="BlBridge 崩溃守卫账本读取 + 修复建议")
    ap.add_argument("--limit", type=int, default=20, help="最多列几条（默认 20）")
    ap.add_argument("--path", default=None, help="直接指定 crashguard.jsonl")
    ap.add_argument("--logDir", default=None, help="日志目录")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    res = build_report(log_dir=args.logDir, path=args.path, limit=args.limit)
    if args.json:
        out = dict(res)
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(_render(res))
    # 退出码：文件不存在 => 1（"读不到"，与"读到且为空"区分开）
    return 0 if res.get("exists") else 1


if __name__ == "__main__":
    sys.exit(main())
