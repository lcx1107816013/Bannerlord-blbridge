#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BlBridge 阶段 2④ · A/B 对比报告器（离线可用）。

为什么不能只报"均值"：本轮实测证明 20v20 镜像局从**均势开局**能走到 8:0 / 13:0 / 4:0，
单场方差极大。所以本报告强制输出 均值 ± 标准差 + 95% 置信区间 + 样本量，
并在样本不足时明确拒绝下结论（而不是给出一个看起来精确的假结论）。

为什么不能只用"全程口径"：同上——胜方活得更久，全程统计量必然更好（幸存者偏差）。
本报告的主指标是**满编窗口**（第一例击杀之前，双方严格等编），
全程统计只作对照。实测同一场里：全程 A 占 53.7%，满编期只有 43.8%。

用法：
    python bl_compare.py --a <文件|目录|逗号分隔> --b <同样> [--label-a A] [--label-b B]
    python bl_compare.py --manifest runs.json          # 由 bl_batch.py 产出
"""

import argparse
import glob
import json
import math
import os
import statistics
import sys

# 复用 2① 分析器的口径实现（同一目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bl_dummy_analyze import load  # noqa: E402

MIN_RUNS_FOR_VERDICT = 3  # 低于此局数不出结论（实测单场方差可让镜像局打出 8:0）


# ── 指标 ────────────────────────────────────────────────────────────────

def per_run_metrics(path):
    """单场指标：满编窗口 + 全程，各算双方实际扣血与命中。"""
    events = load(path)
    meta = next((e for e in events if e.get("t") == "meta"), {})
    end = next((e for e in events if e.get("t") == "end"), {})
    hits = [e for e in events if e.get("t") == "hit"]
    kills = [e for e in events if e.get("t") == "kill"]
    first_kill = min((k.get("time", 0.0) for k in kills), default=None)

    last_hp = {}
    rows = []
    for h in hits:
        d = h.get("defender")
        hp_max = float(h.get("hpMax", 0.0))
        hp_after = float(h.get("hpAfter", 0.0))
        prev = last_hp.get(d, hp_max)
        applied = prev - hp_after
        if applied < 0:
            applied = 0.0
        last_hp[d] = hp_after
        rows.append(
            {
                "time": float(h.get("time", 0.0)),
                "side": h.get("aSide", ""),
                "applied": applied,
                "dmg": float(h.get("dmg", 0.0)),
            }
        )

    def side_totals(rs):
        acc = {}
        for r in rs:
            a = acc.setdefault(r["side"], [0, 0.0])
            a[0] += 1
            a[1] += r["applied"]
        return acc

    full = side_totals(rows)
    early = side_totals([r for r in rows if first_kill is not None and r["time"] <= first_kill])

    def share(acc):
        at = acc.get("Attacker", [0, 0.0])[1]
        df = acc.get("Defender", [0, 0.0])[1]
        tot = at + df
        return (100.0 * at / tot) if tot > 0 else None

    return {
        "file": os.path.basename(path),
        "version": meta.get("version", "?"),
        "duration": end.get("time"),
        "aAlive": end.get("aAlive"),
        "dAlive": end.get("dAlive"),
        "aInitial": end.get("aInitial"),
        "dInitial": end.get("dInitial"),
        "validity": (end.get("validity") or {}).get("verdict", "-"),
        # 满编窗口：本报告的主指标（不受雪球影响）
        "early_hits_A": early.get("Attacker", [0, 0.0])[0],
        "early_hits_D": early.get("Defender", [0, 0.0])[0],
        "early_applied_A": early.get("Attacker", [0, 0.0])[1],
        "early_applied_D": early.get("Defender", [0, 0.0])[1],
        "early_share_A": share(early),
        # 全程：仅作对照，会被幸存者偏差污染
        "full_hits_A": full.get("Attacker", [0, 0.0])[0],
        "full_hits_D": full.get("Defender", [0, 0.0])[0],
        "full_applied_A": full.get("Attacker", [0, 0.0])[1],
        "full_applied_D": full.get("Defender", [0, 0.0])[1],
        "full_share_A": share(full),
        # 胜负（用于说明方差有多大）
        "winner": "A" if (end.get("dAlive") == 0 and (end.get("aAlive") or 0) > 0)
                  else ("D" if (end.get("aAlive") == 0 and (end.get("dAlive") or 0) > 0) else "?"),
    }


# ── 统计 ────────────────────────────────────────────────────────────────

def ci95(vals):
    """返回 (mean, sd, lo, hi)。n<2 时区间无意义，返回 None。"""
    n = len(vals)
    if n == 0:
        return (None, None, None, None)
    mean = statistics.fmean(vals)
    if n < 2:
        return (mean, 0.0, None, None)
    sd = statistics.stdev(vals)
    half = 1.96 * sd / math.sqrt(n)
    return (mean, sd, mean - half, mean + half)


def fmt(v, p=2):
    return "-" if v is None else ("{:.%df}" % p).format(v)


# ── 报告 ────────────────────────────────────────────────────────────────

def group_metrics(paths):
    runs = []
    for p in paths:
        try:
            runs.append(per_run_metrics(p))
        except Exception as exc:
            print("  !! %s: %s: %s" % (os.path.basename(p), type(exc).__name__, exc))
    return runs


def report_group(label, runs, out):
    out.append("### %s（%d 局）" % (label, len(runs)))
    out.append("")
    out.append("| 场次 | 版本 | 结果 A:D | 胜 | 满编A占比% | 全程A占比% | 时长s | validity |")
    out.append("|---|---|---|---|---|---|---|---|")
    for r in runs:
        out.append(
            "| %s | %s | %s:%s | %s | %s | %s | %s | %s |"
            % (
                r["file"], r["version"], r["aAlive"], r["dAlive"], r["winner"],
                fmt(r["early_share_A"], 1), fmt(r["full_share_A"], 1),
                fmt(r["duration"], 0), r["validity"],
            )
        )
    out.append("")

    early = [r["early_share_A"] for r in runs if r["early_share_A"] is not None]
    full = [r["full_share_A"] for r in runs if r["full_share_A"] is not None]
    m_e, sd_e, lo_e, hi_e = ci95(early)
    m_f, sd_f, lo_f, hi_f = ci95(full)
    out.append("**满编窗口（主指标）**：均值 %s%% ± SD %s  95%%CI [%s, %s]  n=%d"
               % (fmt(m_e, 1), fmt(sd_e, 1), fmt(lo_e, 1), fmt(hi_e, 1), len(early)))
    out.append("")
    out.append("**全程（对照，受雪球污染）**：均值 %s%% ± SD %s  95%%CI [%s, %s]  n=%d"
               % (fmt(m_f, 1), fmt(sd_f, 1), fmt(lo_f, 1), fmt(hi_f, 1), len(full)))
    out.append("")
    wins = {"A": 0, "D": 0, "?": 0}
    for r in runs:
        wins[r["winner"]] += 1
    out.append("胜负分布：A 胜 %d / D 胜 %d / 未分 %d —— **若镜像局出现一边倒，说明单场方差主导**"
               % (wins["A"], wins["D"], wins["?"]))
    out.append("")
    return early, full


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", help="A 组：文件 / 目录 / 逗号分隔")
    ap.add_argument("--b", help="B 组：同上")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--manifest", help="bl_batch.py 产出的 runs.json")
    ap.add_argument("--out", help="把 markdown 报告写到该文件")
    args = ap.parse_args()

    groups = []
    if args.manifest:
        man = json.load(open(args.manifest, encoding="utf-8"))
        for cfg in man.get("configs", []):
            paths = [r["file"] for r in cfg.get("runs", []) if r.get("file")]
            groups.append((cfg.get("label", "?"), paths))
    else:
        for spec, label in ((args.a, args.label_a), (args.b, args.label_b)):
            if not spec:
                continue
            paths = []
            for part in spec.split(","):
                part = part.strip().strip('"')
                if os.path.isdir(part):
                    paths += sorted(glob.glob(os.path.join(part, "*.jsonl")))
                elif os.path.isfile(part):
                    paths.append(part)
            groups.append((label, paths))

    if not groups:
        print("没有输入。用 --a/--b 或 --manifest。")
        return 1

    out = []
    out.append("# BlBridge A/B 对比报告")
    out.append("")
    out.append("> 主指标 = **满编窗口**（第一例击杀之前的双方实际扣血占比，此时双方严格等编）。")
    out.append("> 全程口径仅作对照：胜方活得更久，其统计量必然更好（幸存者偏差）。")
    out.append("")

    stats = []
    for label, paths in groups:
        if not paths:
            out.append("### %s：无输入" % label)
            out.append("")
            continue
        stats.append((label, report_group(label, group_metrics(paths), out)))

    # 两组对比
    if len(stats) == 2 and stats[0][1][0] and stats[1][1][0]:
        la, (ea, _) = stats[0]
        lb, (eb, _) = stats[1]
        out.append("## 对比：%s vs %s（满编窗口）" % (la, lb))
        out.append("")
        ma, sda, loa, hia = ci95(ea)
        mb, sdb, lob, hib = ci95(eb)
        out.append("| | %s | %s |" % (la, lb))
        out.append("|---|---|---|")
        out.append("| 均值 | %s%% | %s%% |" % (fmt(ma, 1), fmt(mb, 1)))
        out.append("| 标准差 | %s | %s |" % (fmt(sda, 1), fmt(sdb, 1)))
        out.append("| 95%%CI | [%s, %s] | [%s, %s] |" % (fmt(loa, 1), fmt(hia, 1), fmt(lob, 1), fmt(hib, 1)))
        out.append("| n | %d | %d |" % (len(ea), len(eb)))
        out.append("")
        diff = ma - mb
        out.append("差值 %s − %s = **%s 个百分点**" % (la, lb, fmt(diff, 1)))
        out.append("")
        n_min = min(len(ea), len(eb))
        if n_min < MIN_RUNS_FOR_VERDICT:
            out.append("> ⚠️ **拒绝下结论**：最少一组只有 %d 局（要求 ≥%d）。"
                       "本轮实测已证明镜像局能从均势走到 8:0，单场方差可淹没真实差异。"
                       % (n_min, MIN_RUNS_FOR_VERDICT))
        elif loa is not None and hib is not None and loa > hib:
            out.append("> ✅ 两组 95%% 置信区间**不重叠** —— 差异超出噪声。")
        elif hia is not None and lob is not None and hia < lob:
            out.append("> ✅ 两组 95%% 置信区间**不重叠**（%s 低于 %s）—— 差异超出噪声。" % (la, lb))
        else:
            out.append("> ⚠️ 两组 95%% 置信区间**重叠** —— 现有样本量下，差异与噪声不可区分。"
                       "**不要据此下结论**，应增加局数或改进实验设计。")
        out.append("")

    text = "\n".join(out)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("\n[written] %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
