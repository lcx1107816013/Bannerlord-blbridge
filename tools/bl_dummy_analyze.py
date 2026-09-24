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
    python bl_dummy_analyze.py <battle.jsonl> [--mode auto|range|battle] [--by troop|weapon|bodypart|formation]
    python bl_dummy_analyze.py <dir>            # 目录下全部 *.jsonl 汇总
    python bl_dummy_analyze.py --compare A=<...> B=<...>   # 跨档对比（材质/护甲对照）

跨档对比（`--compare`）的 PATH 可以是 `.jsonl` / 目录 / `bl_batch.py` 产出的 `runs.json`
（manifest 会按其 `configs[].label` 展开成多个档）。**第一档为基准**，每档先打印
"生效判据"（`meta.version` / `dummy_meta.armor` / `dummy_swap` 的 `item` vs `actualItem`），
再按部位给 n / 均值 / 中位 / Δ% / Welch t —— 并按「近战」「箭伤（筛被挡下）」分开报，
因为 §七 实测过：箭伤不筛 `blocked` 会得出与护甲反向的假结论。
"""

import argparse
import glob
import json
import math
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
    units = unit_table(events)
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
                # 稳定部位名（v0.8.1 起）。旧日志没有 bodyPartName ⇒ 回退到 bodyPart
                # （那里面 Head 会显示成别名 CriticalBodyPartsBegin，见 PROGRESS §七）。
                "bodypartname": e.get("bodyPartName") or e.get("bodyPart", ""),
                # 伤害类型（Cut / Pierce / Blunt）：材质抗性 R 是**按伤害类型**分档的
                # （config 里每个材质各有 Cut/Pierce/BluntResistance 三项）⇒ 判据必须能按它
                # 分组/筛选，否则"R 变了但伤害没变"与"R 根本没进这条路径"无法区分。
                "damagetype": e.get("damageType", ""),
                "missile": bool(e.get("isMissile", False)),
                "distance": float(e.get("hitDistance", 0.0)),
                # 受击者的实际编队（--by formation）。旧日志缺该字段 ⇒ "" ⇒ group_by 兜底 "(空)"。
                "formation": victim_formation(e, units),
            }
        )
    return rows


def applied_from_battle(events):
    """普通战斗口径：同一 defender 的相邻 hpAfter 差分。

    若遥测未来也改用 OnScoreHit（事件里会带 blocked / damagedHp），则优先用那些字段。
    """
    units = unit_table(events)
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
                "bodypartname": e.get("bodyPartName") or e.get("bodyPart", ""),
                "damagetype": e.get("damageType", ""),
                "missile": bool(e.get("isMissile", False)),
                "distance": 0.0,
                # 受击者的实际编队（--by formation）。旧日志缺该字段 ⇒ "" ⇒ group_by 兜底 "(空)"。
                "formation": victim_formation(e, units),
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


def unit_table(events):
    """agent 下标 → 该 agent 的 unit 事件（含 troop / formation）。

    `--by formation` 用它把**受击者**映射回其 unit 属性；与既有 troop 分组走同一条
    「事件字段 → group_by」路径，不另起一套解析。旧日志的 unit 事件没有 formation
    字段 ⇒ 取值得到 ""，随后落入 group_by 的既有 "(空)" 兜底（与 troop 缺字段同一策略）。
    """
    table = {}
    for e in events:
        if e.get("t") == "unit":
            a = e.get("agent")
            if a is not None:
                table[a] = e
    return table


def victim_formation(e, units):
    """受击者的 unit.formation。hit 事件的受击者字段是 `defender`，dummy_hit 是 `victim`，二者都取。"""
    key = e.get("victim", e.get("defender"))
    u = units.get(key)
    if not u:
        return ""
    return u.get("formation", "") or ""


# ── 跨档对比（材质 / 护甲对照；2026-09-24 新增）─────────────────────────
# 为什么需要它：单文件口径看得见"这一档的分布"，看不见"两档的差与显著性"，
# 而立项问题（材质差异 7% / 护甲 ×1.15 值不值）问的正是后者。
# 为什么每档先打"生效判据"：本项目最贵的教训是"参数被静默丢弃 / 换装没生效"
# 却照样出数 —— 2026-09-24 的材质对照 Δ=0 就是这么来的。

def welch_t(a, b):
    """Welch t 统计量（不等方差）。任一组 < 2 或合并标准误为 0 ⇒ None（不假装显著）。"""
    if len(a) < 2 or len(b) < 2:
        return None
    se2 = statistics.variance(a) / len(a) + statistics.variance(b) / len(b)
    if se2 <= 0:
        return None
    return (statistics.fmean(a) - statistics.fmean(b)) / math.sqrt(se2)


def parse_compare_specs(items):
    """['A=path', ...] → [(tier, path), ...]；没有 '=' 时整体当路径、档名取 basename。"""
    out = []
    for it in items:
        if "=" in it:
            tier, path = it.split("=", 1)
            out.append((tier.strip() or "tier", path.strip()))
        else:
            out.append((os.path.basename(it.rstrip("\\/")) or "tier", it))
    return out


def manifest_tiers(path):
    """path 是 bl_batch 产出的 manifest ⇒ [(label, [file, ...]), ...]；否则 None。"""
    if not path.lower().endswith(".json"):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            man = json.load(fh)
    except Exception:
        return None
    if not isinstance(man, dict) or not isinstance(man.get("configs"), list):
        return None
    out = []
    for cfg in man["configs"]:
        files = [r.get("file") for r in (cfg.get("runs") or []) if r.get("file")]
        if files:
            out.append((cfg.get("label") or "(未命名)", files))
    return out or None


def explode_tiers(specs):
    """[(tier, path)] → [(tier, [jsonl, ...])]。path 可为 jsonl / 目录 / manifest。"""
    out = []
    for tier, path in specs:
        mt = manifest_tiers(path)
        if mt:
            for label, files in mt:
                out.append((("%s/%s" % (tier, label)) if len(mt) > 1 else tier, files))
            continue
        if os.path.isdir(path):
            files = sorted(glob.glob(os.path.join(path, "*.jsonl")))
        elif glob.has_magic(path):
            files = sorted(glob.glob(path))       # 支持通配符，方便按批次圈文件
        else:
            files = [path]
        out.append((tier, [f for f in files if os.path.isfile(f)]))
    return out


def load_tier(files):
    """一个档 = 若干 jsonl。返回逐击 rows（口径自动）+ 该档元信息与生效判据。"""
    rows = []
    versions, armor, swaps, swap_missing = set(), None, [], False
    for f in files:
        ev = load(f)
        has_range = any(e.get("t") == "dummy_hit" for e in ev)
        rows.extend(applied_from_range(ev) if has_range else applied_from_battle(ev))
        for e in ev:
            t = e.get("t")
            if t == "meta" and e.get("version"):
                versions.add(e["version"])
            elif t == "dummy_meta" and e.get("armor") is not None:
                armor = e["armor"]
            elif t == "dummy_swap":
                # actualItem 是 v0.8.6 才有的：缺它就判不了"换装是否生效"，
                # 必须如实报"未知"，不能默认它生效。
                if "actualItem" not in e:
                    swap_missing = True
                swaps.append(e)
    has_blocked = bool(rows) and all(r.get("blocked") is not None for r in rows)
    return {"files": files, "rows": rows, "versions": sorted(versions), "armor": armor,
            "swaps": swaps, "swapMissing": swap_missing, "hasBlockedField": has_blocked}


def swap_verdict(tier):
    """换装生效判据：请求的 item vs 从 agent 读回的 actualItem（v0.8.6 起可判）。"""
    if not tier["swaps"]:
        return "未请求换装（本轮不改装备）"
    if tier["swapMissing"]:
        return "未知（该版本 dummy_swap 无 actualItem 字段）"
    bad = [s for s in tier["swaps"] if (s.get("actualItem") or "") != (s.get("item") or "")]
    if bad:
        return "!! 未生效：%d/%d 场 actualItem != item（例 %r → %r）" % (
            len(bad), len(tier["swaps"]), bad[0].get("item"), bad[0].get("actualItem"))
    s = tier["swaps"][0]
    bodies = sorted(set(str(x.get("armorBody")) for x in tier["swaps"]))
    mats = sorted(set(str(x.get("material")) for x in tier["swaps"]))
    note = "%d 场" % len(tier["swaps"])
    if len(bodies) > 1 or len(mats) > 1:
        # 同一档里 item 相同、但**运行时的材质/护甲值逐场不同** ⇒ 随机 modifier（或别的每场抖动）。
        # 这类抖动会直接污染"按护甲值/材质配对"的对照实验 —— 2026-09-24 的换装对照就栽在这上面，
        # 所以必须当场报出来，不能只显示第一场的值（那会把抖动掩盖成"一致"）。
        return ("!! 场间抖动：item=%s 但 material=%s armorBody=%s（%s）⇒ 该批不能当干净对照"
                % (s.get("item"), "/".join(mats), "/".join(bodies), note))
    return "生效：item=%s material=%s armorBody=%s agents=%s（%s，逐场一致）" % (
        s.get("item"), mats[0], bodies[0], s.get("agents"), note)


def _group_of(row, key):
    return (row.get(key) or "(空)") if key else "(全部)"


def select_rows(rows, key="bodypartname", missile=None, exclude_blocked=True):
    """按部位/远近战/是否被挡下筛样本。缺 blocked 字段的行不会被当成"被挡下"。"""
    out = []
    for r in rows:
        if missile is not None and bool(r.get("missile")) != missile:
            continue
        if exclude_blocked and r.get("blocked"):
            continue
        out.append(r)
    return out


def _stats(vals):
    if not vals:
        return None
    return {"n": len(vals), "mean": statistics.fmean(vals), "median": statistics.median(vals)}


def compare_tiers(tiers, key="bodypartname", metric="applied", missile=None,
                  exclude_blocked=True):
    """{部位: {档: {n, mean, median, deltaPct, t}}}；第一档为基准，Δ% / t 都相对它。"""
    base_label = tiers[0][0]
    vals = {}
    for tier, data in tiers:
        for r in select_rows(data["rows"], key, missile, exclude_blocked):
            vals.setdefault((_group_of(r, key), tier), []).append(float(r.get(metric, 0.0)))
    table = {}
    for (g, tier), v in vals.items():
        st = _stats(v)
        if st is None:
            continue
        b = _stats(vals.get((g, base_label), []))
        st["deltaPct"] = (None if (not b or not b.get("mean"))
                          else 100.0 * (st["mean"] - b["mean"]) / b["mean"])
        st["t"] = (None if (tier == base_label or not b)
                   else welch_t(v, vals[(g, base_label)]))
        table.setdefault(g, {})[tier] = st
    return table


def render_compare(specs, by="bodypart", metric="applied", top=10):
    key = {"bodypart": "bodypartname", "troop": "troop", "damagetype": "damagetype",
           "formation": "formation", "none": None}.get(by, "bodypartname")
    tiers = [(t, load_tier(f)) for t, f in explode_tiers(specs)]
    tiers = [(t, d) for t, d in tiers if d["files"]]
    print("=" * 100)
    print("跨档对比  档数=%d  metric=%s  分组=%s  基准档=%s"
          % (len(tiers), metric, by, tiers[0][0] if tiers else "-"))
    print("=" * 100)
    if len(tiers) < 2:
        print("!! 跨档对比至少需要两个有数据的档 —— 检查路径/文件是否存在")
        return 1
    for tier, data in tiers:
        print("  [%s]  文件 %d 个  逐击样本 %d 条" % (tier, len(data["files"]), len(data["rows"])))
        print("       version     : %s" % (", ".join(data["versions"]) or "?"))
        if data["armor"] is not None:
            print("       dummy_armor : %s" % json.dumps(data["armor"], ensure_ascii=False))
        print("       dummy_swap  : %s" % swap_verdict(data))
    base = tiers[0][0]
    for title, missile, excl in (("全部命中", None, True),
                                 ("近战（!isMissile）", False, True),
                                 ("箭伤（isMissile 且未被挡下）", True, True),
                                 ("箭伤（含被挡下，仅作对照）", True, False)):
        table = compare_tiers(tiers, key, metric, missile, excl)
        print()
        print("── %s ──" % title)
        rows = [(g, d) for g, d in table.items() if d.get(base)]
        if not rows:
            print("   (无样本)")
            continue
        rows.sort(key=lambda kv: -kv[1][base]["n"])
        print("   %-14s %-14s %7s %9s %8s %9s %9s"
              % ("部位", "档", "n", "均值", "中位", "Δ%", "Welch t"))
        for g, d in rows[:top]:
            for tier, _ in tiers:
                st = d.get(tier)
                if not st:
                    continue
                print("   %-14s %-14s %7d %9.2f %8.1f %9s %9s"
                      % (g[:14] if tier == base else "", tier[:14], st["n"], st["mean"],
                         st["median"],
                         "—" if st["deltaPct"] is None else "%+.1f%%" % st["deltaPct"],
                         "—" if st["t"] is None else "%.2f" % st["t"]))
    print()
    print("── 被挡下率（混淆检查：§七 教训是箭伤不筛它就会得出反向结论）──")
    for tier, data in tiers:
        arrow = select_rows(data["rows"], key, missile=True, exclude_blocked=False)
        if not arrow:
            continue
        nb = sum(1 for r in arrow if r.get("blocked"))
        print("   %-14s %5.1f%%  (n=%d)%s"
              % (tier[:14], 100.0 * nb / len(arrow), len(arrow),
                 "" if data["hasBlockedField"] else "   <- 该档无 blocked 字段，此值为推断"))
    return 0


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
        key = {"troop": "troop", "weapon": "weapon", "bodypart": "bodypart",
               "damagetype": "damagetype", "formation": "formation"}[by]
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
    import bl_common
    bl_common.safe_streams()      # 默认中文控制台（GBK）下不因 Δ / — 等字符崩

    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="JSONL 文件或目录")
    ap.add_argument("--mode", default="auto", choices=["auto", "range", "battle"])
    ap.add_argument("--by", default="troop",
                    choices=["troop", "weapon", "bodypart", "damagetype", "formation", "none"])
    ap.add_argument("--compare", nargs="+", metavar="LABEL=PATH",
                    help="跨档对比（材质/护甲对照）：LABEL=PATH，PATH 可为 jsonl / 目录 / "
                         "bl_batch 的 manifest.json；第一档为基准")
    ap.add_argument("--metric", default="applied", choices=["applied", "dmg"],
                    help="跨档对比的逐击量（默认 applied = 引擎实际扣血）")
    ap.add_argument("--top", type=int, default=10, help="每个表最多列几个部位")
    args = ap.parse_args()

    if args.compare:
        return render_compare(parse_compare_specs(args.compare), args.by, args.metric,
                              max(1, args.top))
    if not args.path:
        ap.error("需要 path，或者用 --compare LABEL=PATH ...")

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
