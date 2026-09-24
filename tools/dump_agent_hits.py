#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把若干 agent 的命中序列原样打出来（含 hpAfter 变化），用于判定伤害字段语义。

当统计脚本给出自相矛盾的结果时，唯一可靠的办法是看原始序列：
每行 `时间 / dmg / absorbed / magnitude / hpAfter`，并在 hpAfter 发生变化处标记。

用法：python dump_agent_hits.py <battle_xxx.jsonl> [显示几个 agent=3]
"""
import io
import json
import os
import sys
from collections import defaultdict


def main():
    if len(sys.argv) < 2:
        print("usage: python dump_agent_hits.py <battle_xxx.jsonl> [agent_count=3]")
        return 1
    path = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    if not os.path.isfile(path):
        print("file not found: %s" % path)
        return 1

    hits = defaultdict(list)
    info = {}
    kills = {}
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
            if t == "unit":
                info[e["agent"]] = e
            elif t == "hit":
                hits[e["defender"]].append(e)
            elif t == "kill":
                kills[e["victim"]] = e

    # 优先挑"被打次数多且掉过血"的 agent，最能看出规律
    ranked = sorted(hits.items(), key=lambda kv: -len(kv[1]))
    picked = 0
    for agent, lst in ranked:
        hp_seq = {e["hpAfter"] for e in lst}
        if len(hp_seq) < 2 and picked >= limit:
            continue
        u = info.get(agent, {})
        print("\n--- agent %s (%s / %s / maxHp=%s): %d hits, %d distinct hpAfter values ---" % (
            agent, u.get("side"), u.get("troop"), u.get("maxHp"), len(lst), len(hp_seq)))
        print("   %-9s %6s %9s %10s %8s   %s" % ("time", "dmg", "absorbed", "magnitude", "hpAfter", "note"))
        prev = None
        for e in lst:
            note = ""
            if prev is not None and e["hpAfter"] != prev:
                note = "<- HP changed %d (dropped %d)" % (e["hpAfter"], prev - e["hpAfter"])
            print("   %-9.3f %6d %9d %10.2f %8.1f   %s" % (
                e["time"], e["dmg"], e.get("absorbedByArmor", 0),
                e.get("magnitude", 0.0), e["hpAfter"], note))
            prev = e["hpAfter"]
        k = kills.get(agent)
        if k:
            print("   -> died at t=%.3f (killer=%s, last dmg=%s / absorbed=%s)" % (
                k["time"], k.get("killer"), k.get("dmg"), k.get("absorbedByArmor", "?")))
        picked += 1
        if picked >= limit:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
