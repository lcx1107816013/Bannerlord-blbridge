#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用实测数据反推伤害字段语义 + 分侧统计（镜像对局偏差分析）。

为什么需要它：
  遥测里 hit 事件同时有 dmg(blow.InflictedDamage) / absorbedByArmor(blow.AbsorbedByArmor) /
  magnitude(blow.BaseMagnitude) / hpAfter。但这三个字段里哪个是"真正扣掉的血"不能靠猜 ——
  挂钩点可能在扣血之前（hpAfter 是"扣血前的血"）。本脚本用"同一 agent 相邻两次 hpAfter 的差"
  作为**实测基准**，逐个假设去比对，谁的匹配率接近 100% 谁就是真相。

同时输出分侧（aSide/dSide）聚合，用于判断镜像对局是否存在系统性方向偏差。

用法：python hit_semantics_probe.py <battle_xxx.jsonl>
"""
import io
import json
import os
import statistics
import sys
from collections import defaultdict


def main():
    if len(sys.argv) < 2:
        print("用法: python hit_semantics_probe.py <battle_xxx.jsonl>")
        return 1
    path = sys.argv[1]
    if not os.path.isfile(path):
        print("找不到文件：%s" % path)
        return 1

    hits = defaultdict(list)          # agent -> [ {..} ]
    kill_time = {}                    # agent -> time
    side_of = {}                      # agent -> side
    troop_of = {}
    meta = None
    end = None
    t0 = None

    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get("t")
            if t == "meta":
                meta = e
            elif t == "unit":
                side_of[e["agent"]] = e.get("side")
                troop_of[e["agent"]] = e.get("troop")
            elif t == "hit":
                if t0 is None:
                    t0 = e["time"]
                hits[e["defender"]].append(e)
            elif t == "kill":
                kill_time[e["victim"]] = e["time"]
            elif t == "end":
                end = e

    print("文件: %s" % os.path.basename(path))
    if end:
        print("end: %s" % json.dumps(end, ensure_ascii=False))

    # ── 一、反推字段语义 ────────────────────────────────────────────
    # 对每个 agent 的命中序列，用相邻 hpAfter 差（= 期间真实掉血）去比对假设。
    # H_next：hpAfter 是"扣血后"的值 → 本次生效伤害 = hpAfter[i-1] - hpAfter[i]
    # H_same：hpAfter 是"扣血前"的值 → 本次生效伤害 = hpAfter[i] - hpAfter[i+1]
    cand = {
        "dmg": lambda e: e["dmg"],
        "dmg-absorbed": lambda e: e["dmg"] - e.get("absorbedByArmor", 0),
        "absorbed": lambda e: e.get("absorbedByArmor", 0),
    }
    stat = {h: {c: [0, 0] for c in cand} for h in ("H_next", "H_same")}
    for agent, lst in hits.items():
        if len(lst) < 2:
            continue
        for i in range(len(lst)):
            e = lst[i]
            for hyp in ("H_next", "H_same"):
                if hyp == "H_next":
                    if i == 0:
                        continue
                    observed = lst[i - 1]["hpAfter"] - e["hpAfter"]
                else:
                    if i + 1 >= len(lst):
                        continue
                    observed = e["hpAfter"] - lst[i + 1]["hpAfter"]
                if observed < 0:
                    continue
                for cname, fn in cand.items():
                    got = fn(e)
                    if got < 0:
                        continue
                    stat[hyp][cname][0] += 1
                    if abs(got - observed) <= 1:
                        stat[hyp][cname][1] += 1

    print("\n① 伤害字段语义（用相邻 hpAfter 差作为实测基准；命中率越高越可信）")
    print("   假设                                 候选公式            样本     吻合率")
    for hyp in ("H_next", "H_same"):
        for cname in cand:
            n, ok = stat[hyp][cname]
            if n == 0:
                continue
            print("   %-34s %-18s %6d   %6.1f%%" % (
                hyp + ("（hpAfter=扣血后）" if hyp == "H_next" else "（hpAfter=扣血前）"),
                cname, n, 100.0 * ok / n))

    # ── 二、分侧聚合（镜像局方向偏差）────────────────────────────────
    print("\n② 分侧统计（aSide=命中方阵营 / dSide=被命中方阵营）")
    by_side_hitout = defaultdict(lambda: {"hits": 0, "dmg": 0, "absorbed": 0, "eff": 0})
    by_side_takenin = defaultdict(lambda: {"hits": 0, "dmg": 0, "absorbed": 0, "eff": 0})
    first_hit = {}
    for agent, lst in hits.items():
        for e in lst:
            a = e.get("aSide")
            d = e.get("dSide")
            eff = max(0, e["dmg"] - e.get("absorbedByArmor", 0))
            by_side_hitout[a]["hits"] += 1
            by_side_hitout[a]["dmg"] += e["dmg"]
            by_side_hitout[a]["absorbed"] += e.get("absorbedByArmor", 0)
            by_side_hitout[a]["eff"] += eff
            by_side_takenin[d]["hits"] += 1
            by_side_takenin[d]["dmg"] += e["dmg"]
            by_side_takenin[d]["absorbed"] += e.get("absorbedByArmor", 0)
            by_side_takenin[d]["eff"] += eff
            if a not in first_hit or e["time"] < first_hit[a]:
                first_hit[a] = e["time"]

    print("   阵营        打出命中   打出原始伤   被护甲吃掉   打出有效伤   首次命中(任务秒)")
    for s in sorted(by_side_hitout):
        v = by_side_hitout[s]
        print("   %-10s %8d %12d %12d %12d   %8.2f" % (
            s, v["hits"], v["dmg"], v["absorbed"], v["eff"], first_hit.get(s, -1)))

    print("\n③ 阵亡分布")
    dead = defaultdict(int)
    for agent, tm in kill_time.items():
        dead[side_of.get(agent, "?")] += 1
    for s in sorted(dead):
        print("   %-10s 阵亡 %d" % (s, dead[s]))

    # 有效伤害（用反向推断出的公式：dmg - absorbed）分侧时间线
    if t0 is not None:
        print("\n④ 每 30 秒窗口的有效伤害（左右对称性检查）")
        win = defaultdict(lambda: defaultdict(int))
        for agent, lst in hits.items():
            for e in lst:
                w = int((e["time"] - t0) // 30) * 30
                win[e.get("aSide")][w] += max(0, e["dmg"] - e.get("absorbedByArmor", 0))
        sides = sorted({e.get("aSide") for lst in hits.values() for e in lst})
        print("   窗口(秒)  " + "".join("%14s" % s for s in sides))
        for w in sorted({w for d in win.values() for w in d}):
            print("   %02d-%02d     " % (w, w + 30) + "".join(
                "%14d" % win[s].get(w, 0) for s in sides))
    return 0


if __name__ == "__main__":
    sys.exit(main())
