#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按兵种对照「到死挨几箭」（③ 立项校验用）。

两个口径都报：``arrow_hits``（含被盾挡下）与 ``arrow_damaging``（真正扣血）。
用法：
    python bl_death_compare.py <battle.jsonl> [...]
    python bl_death_compare.py --dir <目录>      # 目录下全部 jsonl
"""
import argparse
import collections
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bl_metrics  # noqa: E402


def collect(files):
    agg = collections.defaultdict(list)
    for f in files:
        ev = bl_metrics.load_events(f)
        units = {e.get("agent"): e for e in ev if e.get("t") == "unit"}
        for a, s in bl_metrics.death_arrow_stats(ev).items():
            agg[(str(units.get(a, {}).get("side")), s["troop"])].append(s)
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir", default=None)
    args = ap.parse_args()
    files = list(args.files)
    if args.dir:
        files += [os.path.join(args.dir, n) for n in sorted(os.listdir(args.dir)) if n.endswith(".jsonl")]
    if not files:
        d = bl_metrics.default_battles_dir()
        files = [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(".jsonl")]
    if not files:
        print("没有 JSONL 可分析。")
        return 2
    agg = collect(files)
    print("源自 %d 个战斗文件" % len(files))
    print(f"{'troop':<29}{'侧':<10}{'N':>5}{'满血':>6}{'箭命中中位':>11}{'扣血箭中位':>11}{'死于箭%':>9}")
    for (side, troop), lst in sorted(agg.items(),
                                     key=lambda kv: statistics.median([x["arrow_damaging"] for x in kv[1]])):
        ah = [x["arrow_hits"] for x in lst]
        ad = [x["arrow_damaging"] for x in lst]
        km = 100.0 * sum(1 for x in lst if x["killed_by_missile"]) / len(lst)
        print(f"{troop[:29]:<29}{side[:9]:<10}{len(lst):>5}{lst[0]['maxHp']:>6.0f}"
              f"{statistics.median(ah):>11.1f}{statistics.median(ad):>11.1f}{km:>8.0f}%")
    print("\n提示：每兵种样本量 = 场数 x 靶子数；1 场 5 靶不足以支撑「必然」结论。")
    return 0


if __name__ == "__main__":
    sys.exit(main())