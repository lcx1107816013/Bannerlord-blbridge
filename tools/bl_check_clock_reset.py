#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多轮日志「时钟同源」校验：验证每份 jsonl 内所有事件的 time 共享同一原点。

为什么要有这个脚本：`round_*` 事件的 time 曾经误用**整场累计**值，而同文件其余事件用**轮内**值
⇒ 同一份日志里出现两条不衔接的时间轴（实测 round_all_done=300.45 vs 主时钟上界 155.70）。
下游按 time 切窗口会**静默**算错 —— 没有报错、没有异常，只是结论错。
修完之后，这条不变量不能只靠人眼看日志，必须**可执行**（与 check_repo_encoding.py 同一理由）。

判据（对应 PROGRESS.md §十一 判据 6）：
  1. `round_start.time`  ≈ 0                           —— 该轮起点
  2. 其余 round_* 事件   time 落在**轮内**区间内         —— 该轮的终点类事件
  3. 其余**每一个事件类型**的 time 起点不晚于锚时钟上界  —— 非 round_* 类型之间也必须同源
     锚时钟   = `state`（每轮必有，由 TelemetryBehavior 写出；缺失时退化为事件数最多的非 round_* 类型）
     轮内区间 = [0, 锚时钟上界 + OVER_TOL]

**为什么参考区间必须用锚时钟，而不是"除 round_* 外全体事件的 min/max"**（v0.8.9 首版的取法）：
  那个取法有**反单调性** —— 任何一条落在更大时间轴上的异源事件都会**抬高**区间上界，
  使 round_* 更容易落进区间、更容易通过。即「加入它本该发现的缺陷，反而让判定放宽」。
  实测 `battle_20260925_111509_578.jsonl`：首版打印的区间上界 310.42 **完全由 `probe` 这一个
  异源类型贡献**，剔除它之后轮内真实上界只有 91.43 —— 首版据此把该文件判成 PASS（**假通过**）。

**判据 3 是 v0.8.10 新增**：首版只把 round_* 与"其余全体"比，**从不检查其余类型之间是否同源**，
于是 `probe` 这类"非 round_* 的异源时钟"完全不可见。异源的特征是**整体平移**：轮 ≥2 时它的起点
至少是前一整轮的长度 ⇒ 用「该类型 time 的最小值 > 锚上界」即可捕获；而正常的轮内类型只可能
"早于首条锚事件"（如 unit 在轮初写 time=0，而首条 state 在 2.03 s），不会晚出区间，故不误报。

关于「哪几个事件要判」：**不硬编码**，从日志里自动发现所有以 `round_` 开头的事件类型。
理由：本脚本首版硬编码了 round_start / round_all_done 两个，漏掉 round_cleanup，
导致「只含 round_cleanup 的文件」被误报为无法判定（3 份）。
**教训：写「覆盖某类事件」的校验器时，先枚举该类事件的完整成员表。**

用法：
    python tools/bl_check_clock_reset.py                       # 全量：所有多轮日志
    python tools/bl_check_clock_reset.py --since 2026-09-25T11:10:00   # 只查新产物 + 自动对照组
    python tools/bl_check_clock_reset.py --json-out result.json        # 结果落盘

**关于 `--since` 与对照组（重要）**：
带 `--since` 时，脚本会在查完新产物之后**自动再扫一遍全量**，作为对照组 ——
因为「新产物 0 失败」单独拿出来**证明不了任何事**：一个恒返回 PASS 的脚本、
一个数据源接错的脚本、一个把所有事件都跳过的脚本，都会得到这个结果。
只有当**历史日志里的已知失败样本仍被报出 FAIL**，才说明这个校验器真的在判定。
所以这一步不做成「请用户手动再跑一次」，而是脚本自己完成（`--json-out-only` 可跳过）。

退出码：0 = 通过（新产物有 PASS 且无 FAIL）；1 = 有 FAIL 或无 PASS；2 = 环境问题（含**数据不足**：
目录不存在、没有 jsonl、--since 解析失败、以及 --since 下没有任何新的多轮文件 —— 后者 v0.8.9 曾误返回 1）。
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
# 锚时钟类型（按优先级）：state 每轮必有、由 TelemetryBehavior 每隔 StateIntervalSeconds 写出，
# 是"该轮轮内时钟"最可靠的载体。缺失时退化为事件数最多的非 round_* 类型（见 _pick_anchor）。
ANCHOR_TYPES = ("state",)
# 锚上界容差（秒）。为什么不是 1e-6：锚（state）是**周期采样**，最后一条 state 到轮末之间存在死区，
# 实测 end / round_cleanup 最多晚 1.95 s（如 battle_20260925_111509_578：末条 state=90.03、
# round_cleanup=91.43）。取 3.0 留余量；而异源时钟的平移量是**整轮时长**（数十~数百秒，
# 本仓库实测 12.2 s 起、最大 320 s），差一个数量级 ⇒ 区分力不受影响。
OVER_TOL = 3.0
# 时间/事件字段名
TIME_KEY = "time"
TYPE_KEY = "t"
# 受影响版本（仅用于报告标注，不参与判定）：
#   0.8.5–0.8.8 —— `round_*` 事件用了整场累计值（PROGRESS §十一 判据 6）
#   0.8.9       —— `round_*` 已修，但 `probe` 事件（ScoreHitProbeBehavior._elapsed）仍用整场累计值；
#                  该版本新增的校验器对这类异源无感、还会判成 PASS（外部审查 B1/B2 ⇒ v0.8.10 修）
AFFECTED_VERSIONS = {"0.8.5", "0.8.6", "0.8.7", "0.8.8", "0.8.9"}


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


def _type_clock(events):
    """按事件类型聚合带数值 time 的事件 ⇒ {type: [t, ...]}。

    为什么要按类型分组：本校验器要判的是**类型之间**是否同源（判据 3），
    而首版把除 round_* 外的所有事件揉成一个 min/max —— 那正是它漏掉 `probe` 的原因。
    """
    by_type = {}
    for e in events:
        name = e.get(TYPE_KEY)
        v = e.get(TIME_KEY)
        if name is None or not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        by_type.setdefault(str(name), []).append(float(v))
    return by_type


def _pick_anchor(by_type):
    """选锚时钟类型 ⇒ (name, values)；没有可用锚时 (None, None)。

    优先 ANCHOR_TYPES（`state`）：每轮必有、周期采样、覆盖全轮。
    缺失时退化为"带 time 事件数最多的非 round_* 类型"，让判据在缺 state 的文件上仍可运行
    （退化的锚不再有"每轮必有"的保证，故报告里会打出锚名与条数，便于识别）。
    """
    for name in ANCHOR_TYPES:
        if by_type.get(name):
            return name, by_type[name]
    best_name, best_vals = None, None
    for name, vals in by_type.items():
        if name.startswith(ROUND_PREFIX):
            continue
        if best_vals is None or len(vals) > len(best_vals):
            best_name, best_vals = name, vals
    return best_name, best_vals


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

    by_type = _type_clock(events)
    anchor_name, anchor_vals = _pick_anchor(by_type)
    if not anchor_vals:
        r["verdict"] = "INCONCLUSIVE"
        r["detail"] = "无任何带 time 的事件 —— 无法建立锚时钟"
        return r

    # 锚上界。**绝不能**改回"除 round_* 外全体事件的 max"：那种取法会把异源事件自己的
    # 大值算进上界，反而放行它（见文件头的反单调性说明）。
    hi = max(anchor_vals)
    r["anchor"] = {"type": anchor_name, "n": len(anchor_vals), "hi": round(hi, 2)}
    r["main_clock"] = [0.0, round(hi, 2)]   # 兼容旧字段名：自 v0.8.10 起它是**锚时钟区间**

    checks, msgs = [], []

    # 判据 1 / 2：round_* 事件必须落在该轮的**轮内**时间轴上
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
            ok = -1e-6 <= t <= hi + OVER_TOL
            msgs.append("%s.time=%.2f %s(轮内[0.00,%.2f])"
                        % (name, t, "OK" if ok else "FAIL", hi))
        checks.append(ok)

    # 判据 3（v0.8.10 新增）：**逐类型**检查起点 —— 抓"非 round_* 类型之间的异源"，
    # 也就是首版判据完全看不见的那一类（`probe` 用整场时钟当轮内值）。
    late = []
    for name in sorted(by_type):
        if name.startswith(ROUND_PREFIX) or name == anchor_name:
            continue
        mn, mx = min(by_type[name]), max(by_type[name])
        if mn > hi + OVER_TOL:
            late.append({"type": name, "min": round(mn, 2), "max": round(mx, 2)})
            checks.append(False)
            msgs.append("%s.time=[%.2f,%.2f] FAIL(起点晚于锚上界 %.2f —— 该类型与轮内时钟不同源)"
                        % (name, mn, mx, hi))
    r["late_types"] = late

    if not checks:
        r["verdict"] = "INCONCLUSIVE"
        r["detail"] = "%s* 事件都缺 time 字段，且无其它带 time 的事件类型" % ROUND_PREFIX
    elif all(checks):
        r["verdict"] = "PASS"
    else:
        r["verdict"] = "FAIL"
    r["detail"] = "; ".join(msgs) + "（锚=%s n=%d）" % (anchor_name, len(anchor_vals))
    return r


def print_control_group(files):
    """对照组：全量扫描，验证本校验器**真的在判定**（理由见文件头与 AGENTS.md §三）。

    只看"新产物 0 失败"等于没有对照：一个恒返回 PASS 的脚本、一个数据源接错的脚本、
    一个把所有事件都跳过的脚本 —— 都会「通过」。只有「已知失败样本仍报 FAIL」才能证明
    这个校验器在判定。所以这一步不做成「请用户手动再跑一次」，而是脚本自己完成。

    v0.8.10：抽成函数，好让「--since 下没有任何新的多轮文件」这条早退路径**也有对照组**
    （原实现在那里直接 return 1，操作者部署后第一次跑门只会看到"未通过"而看不到任何对照）。
    """
    print()
    print("=" * 72)
    print("对照组（全量扫描，验证本校验器有区分力）")
    print("=" * 72)
    all_res = [analyze(p) for p in files]
    all_multi = [r for r in all_res if r["has_round"]]
    all_fail = [r for r in all_multi if r["verdict"] == "FAIL"]
    all_pass = [r for r in all_multi if r["verdict"] == "PASS"]
    print("  全量多轮文件 %d 份：PASS %d / FAIL %d"
          % (len(all_multi), len(all_pass), len(all_fail)))
    for r in all_fail[:5]:
        print("    FAIL %-38s v%-7s %s" % (r["file"], r["version"] or "?", r["detail"]))
    if len(all_fail) > 5:
        print("    ... 共 %d 份" % len(all_fail))
    print()
    if all_fail:
        print("  ⇒ 区分力成立：新产物 0 失败 + 历史 %d 份仍报 FAIL —— **两侧都对，判定可信**。"
              % len(all_fail))
    else:
        print("  ⚠️ 对照组为空：全量扫描没有任何 FAIL。")
        print("     这不能证明新产物是对的 —— 只能说明这个校验器**从未报出过任何失败**。")
        print("     请用一个**已知含缺陷的样本**（如修复前的历史日志）验证它确实会报 FAIL。")
    print("=" * 72)


def main(argv):
    bl_common.safe_streams()
    ap = argparse.ArgumentParser(description="多轮日志时钟同源校验")
    ap.add_argument("battles_dir", nargs="?", default=DEFAULT_BATTLES_DIR,
                    help="日志目录（默认 %s）" % DEFAULT_BATTLES_DIR)
    ap.add_argument("--since", default=None,
                    help="只查 mtime 晚于该 ISO 时间（如 2026-09-25T11:10:00）的文件")
    ap.add_argument("--json-out", dest="json_out", default=None,
                    help="把完整结果写入该 JSON 文件")
    ap.add_argument("--json-out-only", dest="json_out_only", action="store_true",
                    help="配合 --since：跳过自动对照组（脚本化调用时用）")
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
        if args.since and not args.json_out_only:
            # v0.8.10：早退前**先给对照组**（原实现直接 return 1，操作者看不到任何对照），
            # 且退出码用 2（数据不足 = 环境问题），而不是 1（判定失败）—— 与文件头约定一致。
            print("   （下面给出现有日志的对照组，便于确认本校验器仍在判定。）")
            print_control_group(files)
        return 2

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
        if args.since and not args.json_out_only:
            print("      对照结果见下方（脚本已自动跑全量，验证本校验器有区分力）。")
        else:
            print("      ⚠️ 这只说明「这些文件没问题」，不能证明本校验器有判定能力。")
            print("         加 --since <部署时刻> 可让脚本自动带上对照组。")
    else:
        print("结论: 无法判定（全部 INCONCLUSIVE）。")
    print("=" * 72)

    # ── 对照组：带 --since 时，自动把「已知失败样本是否仍被报出」一并给出 ──
    if args.since and not args.json_out_only:
        print_control_group(files)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({"generated_from": args.battles_dir,
                       "since": args.since,
                       "results": results}, fh, ensure_ascii=False, indent=2)
        print("结果已写入: %s" % args.json_out)

    return 0 if (n_fail == 0 and n_pass > 0) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
