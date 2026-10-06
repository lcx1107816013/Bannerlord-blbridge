#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
`bl_concurrency_guide` —— **并发纪律（数据驱动，非手写文档）**

## 为什么要有它（2026-10-06 实测）

BlBridge 的义务：**所有走游戏通道的工具共用同一条串行泵**
（`commands/pending/` → 游戏主线程 `GetFiles` + `Array.Sort` + 逐个 `HandleOne`）。

实测结论（三路独立测量：本 agent + 子代理 A + 子代理 B）：

  · **并发提交可行**，`pending/` 深度实测 **max=8**；
  · **执行仍是串行**——`seq` 单调、**游戏执行序 == 文件名序**（FIFO）；
  · **但代价不会立刻线性外溢**：游戏端**一次轮询把整批捞走**，
    单次成本 ≪ 250ms 轮询周期时，额外串行开销**藏进周期里**。

★ 子代理 A 给出的**外溢判据**（最精确的一条）：

    N × 单次耗时 ≥ 250ms（轮询周期）时才开始外溢

  实测佐证：`list_ui`(~15ms) N=4 → 跨度 ~51ms(≈4×) / N=8 → ~112ms(≈8×)，
  但**总墙钟**在 N<8 时几乎不动（0.24s，比值 1.06）。

## 为什么纪律要"从数据生成"而不是手写

手写的"哪些工具重"**必然腐化**：① 工具会改名；② 耗时随机器/场景变。
所以本模块**两次派发都从现场取数**：
  ① 工具 → 通道方法：**解析 `bl_mcp.py` 的 `call_tool`**（不手抄映射）；
  ② 方法 → 耗时分位：**读 `commands/actions.jsonl` 的实测 `ms`**（不凭印象）。
⇒ 数据变了，纪律自动跟着变。

★ 用 **p90 而非中位数**判定"重"：实测 `skip_video` 是**双峰**
（p50 = 1ms 但 p90 = **459ms**）——只看中位数会**把它误判成轻工具**。

## 判定口径

  🔴 重（HEAVY）  p90 ≥ 100ms   ⇒ **一次就吃掉大半个轮询周期**，不要并发
  🟡 中（MEDIUM） p90 ≥  20ms   ⇒ 可并发，但**上限 ~4**（4×25ms=100ms 仍 < 250ms）
  🟢 轻（LIGHT）  p90 <  20ms   ⇒ **可自由并发**（N 受 250ms 预算约束）
  宿主侧（HOST）  不走通道     ⇒ **零争用，可无限并发**

## 边界（如实写明）

  · 分位数来自**历史 `actions.jsonl`**，是**该机器的实测**，不保证跨机器/跨场景成立；
  · 样本少的工具（n<10）分位数**不可靠**，本模块会标 `lowSample`；
  · 本模块**只给建议，不强制**——它不拦截调用（拦截需要游戏端配合，见 `note`）。
"""
import collections
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 阈值（依据见模块 docstring：250ms 轮询周期）
HEAVY_P90_MS = 100.0
MEDIUM_P90_MS = 20.0
LOW_SAMPLE_N = 10
POLL_INTERVAL_MS = 250.0        # CommandPump.PollInterval

# 这些方法**不是**工具直接发起的（内部/条件性），标注出来避免读者困惑
INTERNAL_METHODS = {"ping"}


def _mcp_path():
    return os.path.join(HERE, "bl_mcp.py")


def _func_bodies(src):
    """切出模块内每个顶层函数的源码（用于闭包分析）。"""
    out = {}
    for m in re.finditer(r"^def ([A-Za-z_]\w*)\(.*?(?=^def |\Z)", src, re.S | re.M):
        out[m.group(1)] = m.group(0)
    return out


def _channel_methods_deep(seg, bodies, seen=None):
    """**闭包**解析：seg 里直接或经模块内函数间接调用的通道方法。

    ⚠️ **2026-10-06 修（两处，都是自己踩的）**：

    ① 初版只扫分支里的**直接** `send_command(` ⇒ 把 `bl_launch_game` 判成宿主侧 —— **错**。
       它直接分支没有，但调用 `_enter_custom_battle`，那里面有 5 处。

    ② 加闭包后**反过来过头了**：`bl_launch_game` 一度列出**全部 31 个方法**。
       根因：`_enter_custom_battle` **会回调 `call_tool` 自身**
       （它要复用工具层逻辑）⇒ 闭包追进 `call_tool` ⇒ 把每个工具的方法都收进来。
       ⇒ **必须把 `call_tool` 本身排除在闭包之外**（它是"派发器"，不是"某工具的依赖"）。

    ⇒ 这两条放在一起说明：**闭包深度既不能太浅也不能太深**，
       边界就是「工具自己的实现 + 它调用的辅助函数」，**不跨越派发边界**。
    """
    if seen is None:
        seen = set()
    found = []
    for c in re.findall(r'send_command\(\s*["\']([A-Za-z_][\w]*)["\']', seg):
        if c not in found:
            found.append(c)
    # 追本模块内的被调函数（**但不追 call_tool 本身** —— 那是派发器，会造成循环放大）
    for callee in set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", seg)):
        if callee in seen or callee not in bodies:
            continue
        if callee == "call_tool":          # ★ 关键：不跨派发边界
            continue
        seen.add(callee)
        for c in _channel_methods_deep(bodies[callee], bodies, seen):
            if c not in found:
                found.append(c)
    return found


def tool_method_map(path=None):
    """解析 `bl_mcp.py` 的 `call_tool`，返回 {工具名: [通道方法...] 或 []（纯宿主侧）}。

    ⚠️ 用 AST 分块 + **闭包**解析 —— 手抄映射会随改名腐化，
    而只看直接调用会漏掉"经内部函数间接走通道"的工具。
    """
    p = path or _mcp_path()
    src = io.open(p, "r", encoding="utf-8", errors="replace").read()
    m = re.search(r"\ndef call_tool\(.*?\n(?=\ndef )", src, re.S)
    if not m:
        return None
    body = m.group(0)
    bodies = _func_bodies(src)
    out = {}
    for part in re.split(r'\n    if name == "', body)[1:]:
        tool = part.split('"')[0]
        out[tool] = _channel_methods_deep(part, bodies)
    return out


def method_stats(log_dir=None):
    """读 `actions.jsonl` 的实测 `ms`，按通道方法统计分位数。"""
    import bl_common
    p = bl_common.actions_path(log_dir)
    stats = {}
    if not os.path.isfile(p):
        return stats, {"path": p, "exists": False, "rows": 0}
    rows = 0
    acc = collections.defaultdict(list)
    with io.open(p, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            rows += 1
            ms = o.get("ms")
            if isinstance(ms, (int, float)):
                acc[o.get("method")].append(float(ms))
    for k, v in acc.items():
        s = sorted(v)
        n = len(s)
        stats[k] = {
            "n": n,
            "p50": s[n // 2],
            "p90": s[min(n - 1, int(n * 0.9))],
            "max": s[-1],
            "lowSample": n < LOW_SAMPLE_N,
        }
    return stats, {"path": p, "exists": True, "rows": rows}


def classify(p90, n):
    """按 p90 分档（口径见模块 docstring）。"""
    if p90 is None:
        return "UNKNOWN"
    if p90 >= HEAVY_P90_MS:
        return "HEAVY"
    if p90 >= MEDIUM_P90_MS:
        return "MEDIUM"
    return "LIGHT"


def concurrency_budget(p90):
    """在"不撑破 250ms 轮询周期"的前提下，建议的最大并发数。

    判据：N × p90 ≤ 250ms（留一点余量用 0.8 系数）。
    """
    if not p90 or p90 <= 0:
        return 64          # 亚毫秒级：实际不受限
    n = int((POLL_INTERVAL_MS * 0.8) / p90)
    return max(1, min(64, n))


def build_report(log_dir=None, tool=None):
    """组装并发纪律报告。"""
    tmap = tool_method_map()
    if tmap is None:
        return {"ok": False, "reason": "cannot_parse_call_tool",
                "detail": "解析不出 bl_mcp.py 的 call_tool（纪律数据源失效）"}
    stats, meta = method_stats(log_dir)

    rows = []
    for t, methods in sorted(tmap.items()):
        if not methods:
            rows.append({"tool": t, "kind": "HOST", "methods": [],
                         "p90": None, "max": None, "n": 0, "budget": None,
                         "note": "宿主侧：不碰通道，零争用"})
            continue
        # 一个工具可能发多个方法（如 bl_launch_game -> list_ui/skip_video/open_ui）
        worst_p90 = None
        worst_max = None
        tot_n = 0
        measured = False
        for meth in methods:
            st = stats.get(meth)
            if st:
                measured = True
                tot_n += st["n"]
                if worst_p90 is None or st["p90"] > worst_p90:
                    worst_p90 = st["p90"]
                if worst_max is None or st["max"] > worst_max:
                    worst_max = st["max"]
        # ⚠️ 走通道但**账本里没有实测**（游戏从没调过它）⇒ UNKNOWN，
        #    与 HOST（不碰通道）**必须分开**：前者有争用风险、后者零风险。
        kind = "UNKNOWN" if not measured else classify(worst_p90, tot_n)
        rows.append({
            "tool": t, "kind": kind, "methods": methods,
            "p90": worst_p90, "max": worst_max, "n": tot_n,
            "lowSample": (not measured) or tot_n < LOW_SAMPLE_N,
            "budget": concurrency_budget(worst_p90) if worst_p90 is not None else None,
            "note": ("走通道但**无实测数据**（游戏未调用过）—— 保守起见按 MEDIUM 对待"
                     if not measured else ""),
        })

    # 未映射到任何工具的方法（内部方法）也报出来，避免"数据里有但纪律没提"
    used = set()
    for ms in tmap.values():
        used.update(ms)
    orphan = sorted(k for k in stats if k not in used)

    if tool:
        rows = [r for r in rows if r["tool"] == tool]
        if not rows:
            return {"ok": False, "reason": "unknown_tool",
                    "detail": "没有这个工具：%s（共 %d 个）" % (tool, len(tmap))}

    report = _render(rows, stats, meta, orphan, tool)
    return {
        "ok": True,
        "ledger": meta,
        "pollIntervalMs": POLL_INTERVAL_MS,
        "thresholds": {"heavyP90": HEAVY_P90_MS, "mediumP90": MEDIUM_P90_MS,
                       "lowSampleN": LOW_SAMPLE_N},
        "counts": dict(collections.Counter(r["kind"] for r in rows)),
        "tools": rows,
        "orphanMethods": orphan,
        "methodStats": stats,
        "report": report,
    }


def _render(rows, stats, meta, orphan, tool_filter):
    L = []
    L.append("BlBridge 并发纪律（数据驱动：工具映射来自源码，耗时来自实测账本）")
    L.append("=" * 78)
    L.append("账本      : %s" % meta.get("path"))
    L.append("账本行数  : %s" % meta.get("rows"))
    L.append("轮询周期  : %.0f ms（CommandPump.PollInterval）" % POLL_INTERVAL_MS)
    L.append("")
    L.append("★ 核心事实（三路实测一致）")
    L.append("  1) 所有通道工具**共用同一条串行泵**（pending/ → 主线程 Array.Sort 后逐个执行）")
    L.append("  2) 执行**严格串行**：seq 单调、**游戏执行序 == 文件名序**（FIFO）")
    L.append("  3) 但代价**不会立刻线性外溢**：游戏端一次轮询把整批捞走，")
    L.append("     单次成本 << 250ms 时，额外串行开销藏进周期里")
    L.append("  ★ 外溢判据（子代理 A 实测）：**N × 单次耗时 >= 250ms** 才开始外溢")
    L.append("")
    L.append("★ 分档口径（用 **p90** 而非中位数 —— 实测 skip_video 双峰：")
    L.append("   p50=1ms 但 p90=459ms，只看中位数会把它误判成轻工具）")
    L.append("   🔴 HEAVY  p90 >= %-5.0fms  => 一次就吃掉大半个周期，**不要并发**" % HEAVY_P90_MS)
    L.append("   🟡 MEDIUM p90 >= %-5.0fms  => 可并发，上限见 budget 列" % MEDIUM_P90_MS)
    L.append("   🟢 LIGHT  p90 <  %-5.0fms  => 可自由并发" % MEDIUM_P90_MS)
    L.append("   ⚪ HOST   不走通道          => **零争用，可无限并发**")
    L.append("")
    L.append("★ 建议 `budget` = 在不撑破 250ms 周期的前提下可并发的路数")
    L.append("   （判据 N × p90 <= 250ms × 0.8）")
    L.append("")

    order = {"HOST": 0, "LIGHT": 1, "MEDIUM": 2, "HEAVY": 3, "UNKNOWN": 4}
    rows = sorted(rows, key=lambda r: (-(r["p90"] or -1), r["tool"]))

    L.append("-" * 78)
    L.append("%-30s %-8s %9s %9s %6s %7s" % ("工具", "档", "p90(ms)", "max(ms)", "n", "budget"))
    L.append("-" * 78)
    for r in rows:
        p90 = "%.2f" % r["p90"] if r["p90"] is not None else "-"
        mx = "%.2f" % r["max"] if r["max"] is not None else "-"
        if r["kind"] == "HOST":
            bud = "∞"                      # 真·零争用
        elif r["kind"] == "UNKNOWN":
            bud = "<=4?"                   # 无实测 ⇒ 保守，**不给 ∞**
        else:
            bud = str(r["budget"])
        flag = " *低样本" if r.get("lowSample") else ""
        L.append("%-30s %-8s %9s %9s %6d %7s%s"
                 % (r["tool"], r["kind"], p90, mx, r["n"], bud, flag))
    # ⚠️ 2026-10-06 修：`note` 原先是**设了却没在报告里渲染** ——
    #    自测 ③ 当场抓到（"报告里点明 UNKNOWN 的原因" FAIL）。
    #    这正是本项目反复出现的同一类问题：**字段填了 ≠ 读者看得到**。
    notes = [r for r in rows if r.get("note")]
    if notes:
        L.append("")
        L.append("★ 逐条说明")
        for r in notes:
            L.append("  · %s：%s" % (r["tool"], r["note"]))
    L.append("")
    if orphan:
        L.append("账本里出现但**不属于任何工具**的方法（内部/条件性调用）: %s" % ", ".join(orphan))
        L.append("")
    L.append("★ 纪律（三条，从上面数据直接推出）")
    L.append("  1. **🔴 HEAVY 不要并发** —— 一个就吃掉大半个轮询周期，")
    L.append("     它自己在 `start_battle` 这类路径上还有 `wait_for_*` 配套调用，")
    L.append("     并发会让配套调用的时序不可预测。")
    L.append("  2. **🟡 MEDIUM 并发上限 = budget 列** —— 超过就逼近 250ms 外溢阈值。")
    L.append("  3. **🟢 LIGHT / ⚪ HOST 随便并发** —— 实测 8 路轻工具总墙钟仍 0.24s。")
    L.append("")
    L.append("★ 边界（如实）")
    L.append("  · 分位数来自**历史账本**，是**本机实测**，不保证跨机器/跨场景成立；")
    L.append("  · 标记 `*低样本` 的（n < %d）分位数**不可靠**，仅供参考；" % LOW_SAMPLE_N)
    L.append("  · 本报告**只给建议、不拦截** —— 拦截需游戏端配合（它看不到客户端身份）。")
    return "\n".join(L)


def main(argv=None):
    """CLI 入口（与同目录既有工具同惯例）。"""
    import argparse
    import bl_common

    bl_common.safe_streams()

    ap = argparse.ArgumentParser(description="BlBridge 并发纪律（数据驱动）")
    ap.add_argument("--tool", default=None, help="只看某个工具")
    ap.add_argument("--logDir", default=None, help="日志目录")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    res = build_report(log_dir=args.logDir, tool=args.tool)
    if not res.get("ok"):
        print(res.get("detail") or res.get("reason"))
        return 2
    if args.json:
        out = dict(res)
        out.pop("methodStats", None)
        out.pop("report", None)
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(res["report"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
