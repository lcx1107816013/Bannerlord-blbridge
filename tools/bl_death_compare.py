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
import bl_common  # noqa: E402
import bl_metrics  # noqa: E402


def collect(files):
    agg = collections.defaultdict(list)
    for f in files:
        ev = bl_metrics.load_events(f)
        units = {e.get("agent"): e for e in ev if e.get("t") == "unit"}
        for a, s in bl_metrics.death_arrow_stats(ev).items():
            agg[(str(units.get(a, {}).get("side")), s["troop"])].append(s)
    return agg


#: 「死于箭」样本的样本量门槛 —— 与 bl_compare 的"样本 < 3 局拒绝下结论"同一标准。
MIN_ARROW_SAMPLES = 3


def summarize(rows):
    """汇总一种兵种的样本，**按死因拆开**。

    为什么必须拆（立项遗留第 ④ 项）：死于近战的样本里，「扣血箭」只统计了
    "恰好被箭射死"的那部分 —— 它与"100% 死于箭"的样本**不是同一个总体**，
    混在一起算中位数是选择偏差（实测：`legionary` 55%、`swordsman` 67%、
    `imperial_heavy_horseman` 67% 死于箭）。
    所以：主口径只取「死于箭」样本；样本不足门槛时**拒绝给数**（返回 None，不硬算）。
    """
    arrow = [x for x in rows if x.get("killed_by_missile")]
    melee = [x for x in rows if not x.get("killed_by_missile")]
    enough = len(arrow) >= MIN_ARROW_SAMPLES
    return {
        "n": len(rows),
        "n_arrow": len(arrow),
        "n_melee": len(melee),
        "arrow_rate_pct": (100.0 * len(arrow) / len(rows)) if rows else 0.0,
        "maxHp": rows[0].get("maxHp") if rows else None,
        # 干净口径：只取「死于箭」的样本
        "arrow_hits_median": (statistics.median([x["arrow_hits"] for x in arrow]) if enough else None),
        "arrow_damaging_median": (statistics.median([x["arrow_damaging"] for x in arrow])
                                  if enough else None),
        # 旧口径（全样本）：保留仅供对照，别用它下结论
        "all_damaging_median": (statistics.median([x["arrow_damaging"] for x in rows]) if rows else None),
    }


def main():
    bl_common.safe_streams()
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir", default=None)
    args = ap.parse_args()
    files = list(args.files)
    if args.dir:
        files += bl_common.list_battle_files(args.dir)
    if not files:
        files = bl_common.list_battle_files()
    if not files:
        print("没有 JSONL 可分析。")
        return 2
    agg = collect(files)
    print("源自 %d 个战斗文件" % len(files))
    rows = [(k, summarize(v)) for k, v in agg.items()]
    # 主排序口径 = 「死于箭」样本的扣血箭中位（拿不到数的排最后）
    rows.sort(key=lambda kv: (kv[1]["arrow_damaging_median"] is None,
                              kv[1]["arrow_damaging_median"] or 0.0))

    def _f(v):
        return "-" if v is None else "%.1f" % v

    print("%-28s%-8s%5s%9s%10s%7s%12s%12s%10s" % (
        "troop", "侧", "N", "死于箭%", "箭/近战", "满血", "扣血箭中位", "箭命中中位", "旧口径"))
    for (side, troop), s in rows:
        print("%-28s%-8s%5d%8.0f%%%10s%7.0f%12s%12s%10s" % (
            troop[:28], side[:7], s["n"], s["arrow_rate_pct"],
            "%d/%d" % (s["n_arrow"], s["n_melee"]), s["maxHp"] or 0.0,
            _f(s["arrow_damaging_median"]), _f(s["arrow_hits_median"]),
            _f(s["all_damaging_median"])))
    print("\n口径：**扣血箭中位只取「死于箭」的样本**（不足 %d 个则拒绝给数，显示 -）；"
          "「死于近战」的样本不计入主口径。" % MIN_ARROW_SAMPLES)
    print("      「旧口径」= 全样本中位，仅供对照，不要用它下结论（选择偏差）。")
    print("提示：每兵种样本量 = 场数 x 靶子数；1 场 5 靶不足以支撑「必然」结论。")
    return 0


if __name__ == "__main__":
    sys.exit(main())