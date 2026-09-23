#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BlBridge 阶段 2① · 伤害分布分析器（不朽靶场的读侧）。

两种口径，自动判别：

  range  靶场数据（存在 dummy_hit 事件）。
         每击后靶子立即回血，所以「本次真实扣血」就存在事件的 applied 字段里。

  battle 普通战斗数据（只有 hit 事件）。
         同一 defender 的相邻 hpAfter 之差 = 该次命中的实际扣血。
         ⚠️ 不能直接用 hpMax - hpAfter：那是"累积扣血"，不是单次。
         ⚠️ 也不能用 dmg（= blow.InflictedDamage，名义值）：被挡下时它仍是大数
            （实测 dmg=203 而 hpAfter == hpMax），会造出 +110% 的假偏差。

用法：
    python bl_dummy_analyze.py <battle.jsonl> [--mode auto|range|battle] [--by troop|weapon|bodypart]
    python bl_dummy_analyze.py <dir>            # 目录下全部 *.jsonl 汇总
"""

import argparse
import glob
import json
import os
import statistics
import sys


def load(path):
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                pass
    return events


def applied_from_range(events):
    """靶场口径：applied 是逐击的绝对量（引擎 OnScoreHit 的 damagedHp），直接可用。"""
    rows = []
    for e in events:
        if e.get("t") != "dummy_hit":
            continue
        rows.append(
            {
                "applied": float(e.get("applied", 0.0)),
                # 交叉校验值：HP 差分（正常应与 applied 一致）
                "appliedByHp": float(e.get("appliedByHp", e.get("applied", 0.0))),
                # 该字段只有遥测改用 OnScoreHit 之后才存在。缺字段时留 None，
                # 以便 describe 区分「事实为 False」与「根本没有这个字段」——否则
                # 旧数据会被显示成"被挡下 0%"，是彻底的误导。
                "blocked": (bool(e["blocked"]) if "blocked" in e else None),
                "dmg": float(e.get("dmg", 0.0)),
                "absorbed": float(e.get("absorbedByArmor", 0.0)),
                "troop": e.get("aTroop", ""),
                "weapon": e.get("weaponClass", ""),
                "bodypart": e.get("bodyPart", ""),
                "missile": bool(e.get("isMissile", False)),
                "distance": float(e.get("hitDistance", 0.0)),
            }
        )
    return rows


def applied_from_battle(events):
    """普通战斗口径：同一 defender 的相邻 hpAfter 差分。

    若遥测未来也改用 OnScoreHit（事件里会带 blocked / damagedHp），则优先用那些字段。
    """
    last_hp = {}
    rows = []
    for e in events:
        if e.get("t") != "hit":
            continue
        d = e.get("defender")
        hp_max = float(e.get("hpMax", 0.0))
        hp_after = float(e.get("hpAfter", 0.0))
        prev = last_hp.get(d, hp_max)
        applied = prev - hp_after
        if applied < 0:
            applied = 0.0
        last_hp[d] = hp_after
        rows.append(
            {
                "applied": applied,
                "appliedByHp": applied,
                "blocked": (bool(e["blocked"]) if "blocked" in e else None),
                "dmg": float(e.get("dmg", 0.0)),
                "absorbed": float(e.get("absorbedByArmor", 0.0)),
                "troop": e.get("aTroop", ""),
                "weapon": e.get("weaponClass", ""),
                "bodypart": e.get("bodyPart", ""),
                "missile": bool(e.get("isMissile", False)),
                "distance": 0.0,
            }
        )
    return rows


def quantile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    idx = q * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def histogram(vals, buckets=20, width=48):
    if not vals:
        return ["(无样本)"]
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return ["  全部样本 = %.2f (N=%d)" % (lo, len(vals))]
    step = (hi - lo) / buckets
    counts = [0] * buckets
    for v in vals:
        k = int((v - lo) / step)
        if k >= buckets:
            k = buckets - 1
        counts[k] += 1
    peak = max(counts)
    out = []
    for i, c in enumerate(counts):
        bar = "#" * int(round(c / peak * width)) if peak else ""
        out.append("  %8.1f ~ %8.1f | %-*s %d" % (lo + i * step, lo + (i + 1) * step, width, bar, c))
    return out


def describe(label, vals, blocked=None):
    if not vals:
        print("  %s: 无样本" % label)
        return
    s = sorted(vals)
    print("  %s" % label)
    print("    样本 N        = %d" % len(s))
    print("    均值 / 中位数  = %.3f / %.3f" % (statistics.fmean(s), statistics.median(s)))
    print("    最小 / 最大    = %.1f / %.1f" % (s[0], s[-1]))
    if len(s) > 1:
        print("    标准差        = %.3f" % statistics.pstdev(s))
    print(
        "    分位数 p10/p25/p50/p75/p90/p99 = %.1f / %.1f / %.1f / %.1f / %.1f / %.1f"
        % tuple(quantile(s, q) for q in (0.10, 0.25, 0.50, 0.75, 0.90, 0.99))
    )
    zero = sum(1 for v in s if v <= 0.0)
    # 只有全部样本都真的带 blocked 字段，才算"引擎直给的事实"；
    # 混合或缺失时一律回退到推断口径（宁可说"推断"，也不要把缺失说成 0%）。
    has_blocked = (blocked is not None and len(blocked) == len(s)
                   and all(b is not None for b in blocked))
    if has_blocked:
        nb = sum(1 for b in blocked if b)
        print("    被挡下(isBlocked)  = %d (%.1f%%)   <- 引擎直给的事实" % (nb, 100.0 * nb / len(s)))
        print("    零伤害(applied<=0) = %d (%.1f%%)" % (zero, 100.0 * zero / len(s)))
    else:
        print("    被挡下(applied<=0) = %d (%.1f%%)   <- 推断（该批数据无 blocked 字段）"
              % (zero, 100.0 * zero / len(s)))


def group_by(rows, key):
    groups = {}
    for r in rows:
        groups.setdefault(r.get(key, "") or "(空)", []).append(r)
    return groups


def analyze(path, mode, by):
    events = load(path)
    has_range = any(e.get("t") == "dummy_hit" for e in events)
    if mode == "auto":
        mode = "range" if has_range else "battle"
    if mode == "range" and not has_range:
        print("!! %s: --mode range 但文件里没有 dummy_hit 事件" % os.path.basename(path))
        return

    meta = next((e for e in events if e.get("t") == "meta"), {})
    end = next((e for e in events if e.get("t") == "end"), {})
    dummy_end = next((e for e in events if e.get("t") == "dummy_end"), {})
    rows = applied_from_range(events) if mode == "range" else applied_from_battle(events)

    print("=" * 92)
    print("%s   v%s   口径=%s" % (os.path.basename(path), meta.get("version", "?"), mode))
    if end:
        print(
            "  战斗结果 aAlive=%s dAlive=%s hits=%s kills=%s validity=%s"
            % (
                end.get("aAlive"),
                end.get("dAlive"),
                end.get("hits"),
                end.get("kills"),
                (end.get("validity") or {}).get("verdict", "-"),
            )
        )
    if dummy_end:
        print(
            "  靶场汇总 hits=%s restored=%s leakedDeaths=%s blocked=%s maxApplied=%s hpMismatch=%s"
            % (
                dummy_end.get("hits"),
                dummy_end.get("restored"),
                dummy_end.get("leakedDeaths"),
                dummy_end.get("blocked", dummy_end.get("zeroApplied")),
                dummy_end.get("maxApplied"),
                dummy_end.get("hpMismatch"),
            )
        )

    applied = [r["applied"] for r in rows]
    nominal = [r["dmg"] for r in rows]
    blocked = [r["blocked"] for r in rows]
    describe("实际扣血 applied 分布", applied, blocked)

    # 交叉校验：applied（引擎 OnScoreHit.damagedHp） vs appliedByHp（hpAfter 差分）
    if mode == "range" and rows:
        byhp = [r["appliedByHp"] for r in rows]
        mism = sum(1 for a, b in zip(applied, byhp) if abs(a - b) > 1.5)
        print("  交叉校验 applied vs appliedByHp：不一致 %d/%d (%.1f%%)  <- 应为 0"
              % (mism, len(rows), 100.0 * mism / len(rows)))
    if nominal:
        nz = [v for v in nominal if v > 0]
        if nz:
            print("  名义伤害 dmg：均值 %.2f（仅交叉校验用；被挡下时它仍是大数）" % statistics.fmean(nz))

    print("  直方图（applied）")
    for line in histogram(applied):
        print(line)

    if by != "none":
        key = {"troop": "troop", "weapon": "weapon", "bodypart": "bodypart"}[by]
        print("  分组（%s）" % by)
        groups = group_by(rows, key)
        for name in sorted(groups, key=lambda k: -len(groups[k])):
            vals = [r["applied"] for r in groups[name]]
            nonzero = [v for v in vals if v > 0]
            mean = statistics.fmean(nonzero) if nonzero else 0.0
            print(
                "    %-28s N=%-6d 有效均值=%-8.2f 被挡下=%5.1f%%"
                % (name[:28], len(vals), mean, 100.0 * (len(vals) - len(nonzero)) / max(1, len(vals)))
            )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="JSONL 文件或目录")
    ap.add_argument("--mode", default="auto", choices=["auto", "range", "battle"])
    ap.add_argument("--by", default="troop", choices=["troop", "weapon", "bodypart", "none"])
    args = ap.parse_args()

    targets = []
    if os.path.isdir(args.path):
        targets = sorted(glob.glob(os.path.join(args.path, "*.jsonl")))
    else:
        targets = [args.path]
    if not targets:
        print("没有找到 JSONL")
        return 1

    for t in targets:
        try:
            analyze(t, args.mode, args.by)
        except Exception as exc:
            print("!! %s: %s: %s" % (os.path.basename(t), type(exc).__name__, exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
