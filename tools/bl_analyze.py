#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BlBridge 遥测分析器（仅标准库）。

用途：读取 BlBridge 遥测 Mod 写出的战斗 JSONL，回答两类问题：
  1) 血量模型对不对：每个阵亡单位「从入场到阵亡累计承受的伤害」应当 ≈ 它的最大血量。
  2) 伤害模型对不对：每击伤害的分布（按兵种/伤害类型/命中部位/武器），可与离线公式模型对比。

用法：
  python bl_analyze.py                      # 分析日志目录里最新的一场
  python bl_analyze.py <battle.jsonl>       # 分析指定文件
  python bl_analyze.py --all                # 分析目录里全部战斗，汇总
  python bl_analyze.py --json               # 输出 JSON（给 MCP 用）
"""
import json
import os
import statistics
import sys


# I/O 与目录扫描收拢在 bl_common（code-review 2026-09-24：三处重复合并成一处）。
# 这里保留同名的公开入口 —— bl_mcp.py 与 bl_selftest.py 一直在用它们。
from bl_common import battles_dir, default_log_dir, latest_battle, list_battles, load_events  # noqa: E402,F401
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import bl_common  # noqa: E402


def _pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def analyze(path):
    ev = load_events(path)
    meta = next((e for e in ev if e.get("t") == "meta"), {})
    end = next((e for e in reversed(ev) if e.get("t") == "end"), {})
    # v0.8.10（F6）：多轮模式下 `end` 只在**最后一轮**存在 ⇒ 时长/入场人数回退
    # （见 bl_common.end_metrics：round_* 取时长、unit 按 side 取人数）
    duration_sec, a_initial, d_initial = bl_common.end_metrics(ev, end)
    units = [e for e in ev if e.get("t") == "unit"]
    hits = [e for e in ev if e.get("t") == "hit"]
    kills = [e for e in ev if e.get("t") == "kill"]
    flees = [e for e in ev if e.get("t") in ("flee", "panic")]
    samples = [e for e in ev if e.get("t") == "sample"]

    # ── 单位索引：agent id -> 单位信息 ────────────────────────────────
    unit_by_id = {}
    for u in units:
        uid = u.get("agent", -1)
        if uid is not None and uid >= 0:
            unit_by_id[uid] = u
    max_hp = {}
    for uid, u in unit_by_id.items():
        max_hp[uid] = float(u.get("maxHp") or 0.0)

    # ── 命中伤害语义（v0.7.4 修正；经 battle_20260923_222557_756 逐击实测确认）──────
    # 旧版直接累加遥测里的 dmg(blow.InflictedDamage)。实测（tools/dump_agent_hits.py 的原始序列）：
    #   dmg=16  / absorbed=37  → HP 正好跌 16
    #   dmg=267 / absorbed=267 → HP **完全不变**（投掷物被盾挡下）
    # 结论：dmg 就是"实际扣血"，absorbedByArmor 是**另一路**记账（护甲/盾吸收的名义量），
    # 两者不能相减；被挡下的命中会带 267 这种大值。旧版把它当真实伤害累加，才出现
    # "致死总伤害 307 vs 满血 146（+110%）"的假象。
    # 所以这里改用 hpAfter 的真实变化给每次命中打 applied（实际扣血），并标记 blocked。
    _hp_now = {}
    for u in units:
        uid = u.get("agent")
        if uid is not None and uid >= 0:
            _hp_now[uid] = float(u.get("maxHp") or 0.0)
    for h in hits:
        d = h.get("defender")
        before = _hp_now.get(d)
        if before is None:
            before = float(h.get("hpMax") or 0.0)
        after = h.get("hpAfter")
        after = before if after is None else float(after)
        applied = before - after
        h["applied"] = applied if applied > 0 else 0.0
        h["blocked"] = h["applied"] <= 0.0
        _hp_now[d] = after

    # ── 逐单位承伤累计（按实际扣血 applied；raw=名义伤害仅作对照）────────
    dmg_by_unit = {}
    raw_by_unit = {}
    blocked_by_unit = {}
    hits_by_unit = {}
    last_hp = {}
    for h in hits:
        d = h.get("defender", -1)
        if d is None or d < 0:
            continue
        dmg_by_unit[d] = dmg_by_unit.get(d, 0) + h["applied"]
        raw_by_unit[d] = raw_by_unit.get(d, 0) + float(h.get("dmg") or 0)
        blocked_by_unit[d] = blocked_by_unit.get(d, 0) + (1 if h["blocked"] else 0)
        hits_by_unit[d] = hits_by_unit.get(d, 0) + 1
        last_hp[d] = h.get("hpAfter")

    died = {}
    for k in kills:
        v = k.get("victim", -1)
        if v is not None and v >= 0:
            died[v] = k.get("state", "?")

    # ── 按兵种汇总 ───────────────────────────────────────────────────
    by_troop = {}
    for uid, u in unit_by_id.items():
        troop = u.get("troop") or "(unknown)"
        rec = by_troop.setdefault(troop, {
            "units": 0, "killed": 0, "lethal_damage": [], "max_hp": [],
            "hits": 0, "damage": 0.0, "levels": [], "sides": set(), "mounted": 0,
            "raw_damage": 0.0, "blocked": 0,
        })
        rec["units"] += 1
        rec["levels"].append(u.get("level") or 0)
        rec["sides"].add(u.get("side"))
        if u.get("isMounted"):
            rec["mounted"] += 1
        mh = float(u.get("maxHp") or 0.0)
        rec["max_hp"].append(mh)
        dmg = dmg_by_unit.get(uid, 0)
        rec["damage"] += dmg
        rec["raw_damage"] += raw_by_unit.get(uid, 0)
        rec["blocked"] += blocked_by_unit.get(uid, 0)
        rec["hits"] += hits_by_unit.get(uid, 0)
        if uid in died:
            rec["killed"] += 1
            rec["lethal_damage"].append(dmg)

    troop_rows = []
    for troop, r in sorted(by_troop.items(), key=lambda kv: -kv[1]["killed"]):
        # 致死总伤害 vs 最大血量的偏差 —— 这是对血量模型最直接的检验
        dev = None
        if r["lethal_damage"] and r["max_hp"]:
            mean_lethal = sum(r["lethal_damage"]) / len(r["lethal_damage"])
            # 同兵种 maxHp 基本一致，用中位数更稳
            mh = statistics.median(r["max_hp"])
            if mh > 0:
                dev = (mean_lethal / mh - 1.0) * 100.0
        troop_rows.append({
            "troop": troop,
            "units": r["units"],
            "killed": r["killed"],
            "killed_pct": (r["killed"] / r["units"] * 100.0) if r["units"] else 0.0,
            "max_hp_median": statistics.median(r["max_hp"]) if r["max_hp"] else 0.0,
            "mean_lethal_damage": (sum(r["lethal_damage"]) / len(r["lethal_damage"])) if r["lethal_damage"] else None,
            "lethal_dev_pct": dev,
            "hits_taken": r["hits"],
            "damage_taken": r["damage"],
            "mean_dmg_per_hit": (r["damage"] / r["hits"]) if r["hits"] else None,
            "mean_raw_per_hit": (r["raw_damage"] / r["hits"]) if r["hits"] else None,
            "blocked_hits": r["blocked"],
            "blocked_pct": (r["blocked"] / r["hits"] * 100.0) if r["hits"] else 0.0,
            "hits_per_kill": (r["hits"] / r["killed"]) if r["killed"] else None,
            "level_median": statistics.median(r["levels"]) if r["levels"] else 0,
            "mounted": r["mounted"],
            "sides": sorted(x for x in r["sides"] if x),
        })

    # ── 每击伤害分布（全局 + 分类） ───────────────────────────────────
    def dist(group_key):
        buckets = {}
        rawb = {}
        for h in hits:
            key = group_key(h)
            if key is None:
                continue
            buckets.setdefault(key, []).append(h["applied"])
            rawb.setdefault(key, []).append(float(h.get("dmg") or 0))
        rows = []
        for k, vals in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            rv = rawb[k]
            rows.append({
                "key": k, "n": len(vals),
                "mean": sum(vals) / len(vals),
                "mean_raw": sum(rv) / len(rv),
                "p10": _pct(vals, 0.10), "p50": _pct(vals, 0.50), "p90": _pct(vals, 0.90),
                "max": max(vals), "zero_pct": sum(1 for v in vals if v <= 0) / len(vals) * 100.0,
            })
        return rows

    result = {
        "file": path,
        "meta": meta,
        "summary": {
            "duration_sec": duration_sec,
            "a_initial": a_initial, "d_initial": d_initial,
            "a_alive": end.get("aAlive"), "d_alive": end.get("dAlive"),
            "hits": len(hits), "kills": len(kills), "flees": len(flees),
            "units_seen": len(units), "samples": len(samples),
        },
        "troops": troop_rows,
        "damage_by_type": dist(lambda h: h.get("damageType")),
        "damage_by_bodypart": dist(lambda h: h.get("bodyPart")),
        "damage_by_weapon": dist(lambda h: h.get("weaponClass")),
        "damage_by_range": dist(lambda h: "missile" if h.get("isMissile") else "melee"),
        "hit_events": len(hits),
    }
    return result


# ── 文本报告 ─────────────────────────────────────────────────────────

def _f(v, nd=1, dash="-"):
    if v is None:
        return dash
    if isinstance(v, float):
        return ("%." + str(nd) + "f") % v
    return str(v)


def report(res):
    L = []
    s = res["summary"]
    L.append("战斗文件: %s" % os.path.basename(res["file"]))
    L.append("  时长 %s 秒 | 单位入场 %s | 命中 %s 次 | 阵亡 %s | 溃逃/恐慌 %s 次" % (
        _f(s.get("duration_sec"), 0), s.get("units_seen"), s.get("hits"), s.get("kills"), s.get("flees")))
    L.append("  攻方 %s→%s 人 | 守方 %s→%s 人" % (
        s.get("a_initial"), s.get("a_alive"), s.get("d_initial"), s.get("d_alive")))
    L.append("")
    L.append("① 血量模型校验（累计**实际扣血** 应≈ 最大血量；偏差大说明血量或伤害记账有问题）")
    L.append("   %-34s %-6s %-7s %-12s %-12s %-9s" % ("兵种", "入场", "阵亡%", "最大血量中位", "累计实际扣血", "偏差%"))
    for r in res["troops"]:
        if not r["killed"]:
            continue
        L.append("   %-34s %-6d %-7s %-12s %-12s %-9s" % (
            r["troop"][:34], r["units"], _f(r["killed_pct"], 0),
            _f(r["max_hp_median"], 1), _f(r["mean_lethal_damage"], 1), _f(r["lethal_dev_pct"], 1)))
    L.append("")
    L.append("② 挨打成本（实际=按 HP 真实变化；名义=含被盾/甲吸收，仅对照）")
    L.append("   %-30s %-8s %-10s %-10s %-9s %-10s %-8s" % (
        "兵种", "被命中", "每击实际", "每击名义", "被挡下%", "每击杀需击数", "阵亡数"))
    for r in sorted(res["troops"], key=lambda x: -x["hits_taken"])[:20]:
        L.append("   %-30s %-8d %-10s %-10s %-9s %-10s %-8d" % (
            r["troop"][:30], r["hits_taken"], _f(r["mean_dmg_per_hit"], 2),
            _f(r["mean_raw_per_hit"], 2), _f(r["blocked_pct"], 1),
            _f(r["hits_per_kill"], 2), r["killed"]))
    for title, key in (("③ 按伤害类型", "damage_by_type"), ("④ 按命中部位", "damage_by_bodypart"),
                       ("⑤ 按武器类别", "damage_by_weapon"), ("⑥ 远程/近战", "damage_by_range")):
        L.append("")
        L.append(title)
        L.append("   %-20s %-7s %-9s %-8s %-8s %-8s %-8s %-8s %-8s" % (
            "分组", "次数", "均伤实际", "均伤名义", "被挡%", "p10", "p50", "p90", "最大"))
        for r in res[key]:
            L.append("   %-20s %-7d %-9s %-8s %-8s %-8s %-8s %-8s %-8s" % (
                str(r["key"])[:20], r["n"], _f(r["mean"], 2), _f(r.get("mean_raw"), 2),
                _f(r["zero_pct"], 1), _f(r["p10"], 1), _f(r["p50"], 1),
                _f(r["p90"], 1), _f(r["max"], 1)))
    return "\n".join(L)


def main(argv):
    bl_common.safe_streams()
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    if "--all" in argv:
        files = [b["file"] for b in list_battles()]
    elif len(argv) > 1:
        files = [argv[1]]
    else:
        f = latest_battle()
        if not f:
            print("没有找到战斗日志。日志目录: %s" % battles_dir())
            return 2
        files = [f]
    if not files:
        print("没有战斗日志可分析。")
        return 2

    if as_json:
        out = [analyze(f) for f in files]
        print(json.dumps(out if len(out) > 1 else out[0], ensure_ascii=False, indent=1))
        return 0

    for f in files:
        try:
            print(report(analyze(f)))
        except Exception as exc:  # noqa: BLE001
            print("分析失败 %s: %r" % (f, exc))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
