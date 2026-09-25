#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多轮日志「时钟同源」校验：验证每份 jsonl 内所有事件的 time 共享同一原点。

为什么要有这个脚本：`round_*` 事件的 time 曾经误用**整场累计**值，而同文件其余事件用**轮内**值
⇒ 同一份日志里出现两条不衔接的时间轴（实测 round_all_done=300.45 vs 主时钟上界 155.70）。
下游按 time 切窗口会**静默**算错 —— 没有报错、没有异常，只是结论错。
修完之后，这条不变量不能只靠人眼看日志，必须**可执行**（与 check_repo_encoding.py 同一理由）。

判据（对应 PROGRESS.md §十一 判据 6）：
  1. `round_start.time`  ≈ 0                     —— 该轮起点
  2. 其余 round_* 事件   time 落在主时钟区间内    —— 该轮的终点类事件
     主时钟区间 = 除 round_* 外所有带 time 事件的最小值~最大值

关于「哪几个事件要判」：**不硬编码**，从日志里自动发现所有以 `round_` 开头的事件类型。
理由：本脚本首版硬编码了 round_start / round_all_done 两个，漏掉 round_cleanup，
导致「只含 round_cleanup 的文件」被误报为无法判定（3 份）。
**教训：写「覆盖某类事件」的校验器时，先枚举该类事件的完整成员表。**

用法：
    python tools/bl_check_clock_reset.py                       # 扫默认 battles 目录
    python tools/bl_check_clock_reset.py <日志目录>
    python tools/bl_check_clock_reset.py --since 2026-09-25T11:10:00   # 只查该时刻之后的文件
    python tools/bl_check_clock_reset.py --json-out result.json        # 结果落盘

退出码：0 = 通过（有 PASS 且无 FAIL）；1 = 有 FAIL 或无 PASS；2 = 环境问题。

注意：`--since` 是本脚本的关键用法。回归时历史日志里存着**修复前**的失败样本，
不筛掉它们会淹没新结果；但同时**必须**在历史日志上再跑一次，
确认脚本能抓出那些已知失败 —— 只报 PASS 的脚本可能是永远返回 PASS。
"""

import argparse
import datetime
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import bl_common  # noqa: E402

# 默认日志目录（与 bl_common / bl_batch 的口径一致）
DEFAULT_BATTLES_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "Mount and Blade II Bannerlord",
    "BlBridge", "battles")

# 轮次事件的前缀
ROUND_PREFIX = "round_"
# round_start.time 的容差（秒）
START_TOL = 0.5
# 时间/事件字段名
TIME_KEY = "time"
TYPE_KEY = "t"
# 受影响版本（仅用于报告标注，不参与判定）
AFFECTED_VERSIONS = {"0.8.5", "0.8.6", "0.8.7", "0.8.8"}


def load_events(path):
    """逐行读 jsonl；返回 (events, meta, bad_line_count)。坏行跳过并计数。"""
    events, meta, bad = [], None, 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                bad += 1
                continue
            if obj.get(TYPE_KEY) == "meta":
                meta = obj
            events.append(obj)
    return events, meta, bad


def analyze(path):
    """对单份文件做判据评估。"""
    events, meta, bad = load_events(path)
    r = {
        "file": os.path.basename(path),
        "version": (meta or {}).get("version"),
        "bad_lines": bad,
        "n_events": len(events),
        "has_round": False,
        "verdict": None,
        "detail": "",
    }

    round_evs = [e for e in events
                 if str(e.get(TYPE_KEY, "")).startswith(ROUND_PREFIX)]
    if not round_evs:
        r["verdict"] = "N/A"
        r["detail"] = "单轮文件（无 %s* 事件），判据不适用" % ROUND_PREFIX
        return r
    r["has_round"] = True

    # 主时钟 = 除 round_* 外，所有带 time 的事件的取值范围
    main = [e[TIME_KEY] for e in events
            if TIME_KEY in e and isinstance(e[TIME_KEY], (int, float))
            and not str(e.get(TYPE_KEY, "")).startswith(ROUND_PREFIX)]
    if not main:
        r["verdict"] = "INCONCLUSIVE"
        r["detail"] = "无主时钟事件"
        return r

    lo, hi = min(main), max(main)
    r["main_clock"] = [round(lo, 2), round(hi, 2)]

    checks, msgs = [], []
    for ev in round_evs:
        name = ev.get(TYPE_KEY)
        if TIME_KEY not in ev or not isinstance(ev[TIME_KEY], (int, float)):
            continue
        t = float(ev[TIME_KEY])
        if name == "round_start":
            ok = abs(t) <= START_TOL
            msgs.append("round_start.time=%.2f %s(应≈0)" % (t, "OK" if ok else "FAIL"))
        else:
            # 其余 round_* （round_cleanup / round_all_done / 未来新增的）都在该轮内
            ok = lo - 1e-6 <= t <= hi + 1e-6
            msgs.append("%s.time=%.2f %s(主时钟[%.2f,%.2f])"
                        % (name, t, "OK" if ok else "FAIL", lo, hi))
        checks.append(ok)

    if not checks:
        r["verdict"] = "INCONCLUSIVE"
        r["detail"] = "%s* 事件都缺 time 字段" % ROUND_PREFIX
    elif all(checks):
        r["verdict"] = "PASS"
    else:
        r["verdict"] = "FAIL"
    r["detail"] = "; ".join(msgs)
    return r


def main(argv):
    bl_common.safe_streams()
    ap = argparse.ArgumentParser(description="多轮日志时钟同源校验")
    ap.add_argument("battles_dir", nargs="?", default=DEFAULT_BATTLES_DIR,
                    help="日志目录（默认 %s）" % DEFAULT_BATTLES_DIR)
    ap.add_argument("--since", default=None,
                    help="只查 mtime 晚于该 ISO 时间（如 2026-09-25T11:10:00）的文件")
    ap.add_argument("--json-out", dest="json_out", default=None,
                    help="把完整结果写入该 JSON 文件")
    args = ap.parse_args(argv[1:])

    if not os.path.isdir(args.battles_dir):
        print("错误: 目录不存在: %s" % args.battles_dir)
        return 2

    files = sorted(glob.glob(os.path.join(args.battles_dir, "*.jsonl")))
    if not files:
        print("错误: 目录下没有 *.jsonl: %s" % args.battles_dir)
        return 2

    since_ts = None
    if args.since:
        try:
            since_ts = datetime.datetime.fromisoformat(args.since).timestamp()
        except Exception as e:
            print("错误: --since 解析失败: %s" % e)
            return 2

    results = []
    for p in files:
        if since_ts is not None and os.path.getmtime(p) < since_ts:
            continue
        results.append(analyze(p))

    multi = [r for r in results if r["has_round"]]
    print("=" * 72)
    print("扫描 %d 份文件；其中多轮文件 %d 份" % (len(results), len(multi)))
    print("=" * 72)

    if not multi:
        print()
        print("!! 未发现任何多轮日志。")
        print("   本判据需要 Rounds>1 的战斗才能验证 —— 请先跑一场 --rounds 3 的战斗。")
        print("   单轮战斗**不会**触发该类缺陷，跑它只会得到假阳性通过。")
        return 1

    by_verdict = {}
    for r in multi:
        by_verdict.setdefault(r["verdict"], []).append(r)

    for v in ("FAIL", "PASS", "INCONCLUSIVE"):
        group = by_verdict.get(v, [])
        if not group:
            continue
        print()
        print("--- %s (%d 份) ---" % (v, len(group)))
        for r in group:
            tag = "  [受影响版本]" if r["version"] in AFFECTED_VERSIONS else ""
            print("  %-42s v%-7s %s%s"
                  % (r["file"], r["version"] or "?", r["detail"], tag))

    n_fail = len(by_verdict.get("FAIL", []))
    n_pass = len(by_verdict.get("PASS", []))

    print()
    print("=" * 72)
    if n_fail:
        print("结论: **未通过** —— %d 份文件时钟不同源。" % n_fail)
        print("      （先看 mtime：早于部署时刻的是修复前历史产物，不构成本轮回归失败）")
    elif n_pass:
        print("结论: **通过** —— %d 份多轮文件时钟同源，0 份失败。" % n_pass)
        print("      ⚠️ 仅此不足以定论：还须在历史日志上跑一次，确认脚本仍能抓出已知失败样本。")
    else:
        print("结论: 无法判定（全部 INCONCLUSIVE）。")
    print("=" * 72)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({"generated_from": args.battles_dir,
                       "since": args.since,
                       "results": results}, fh, ensure_ascii=False, indent=2)
        print("结果已写入: %s" % args.json_out)

    return 0 if (n_fail == 0 and n_pass > 0) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
