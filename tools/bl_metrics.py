#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BlBridge 0.7.9 遥测指标分析器（纯计算层：无打印、无 I/O）。

seam（被测接缝）：每个函数都是「事件流 list[dict] -> 普通 dict/list」的纯函数，
可直接喂合成事件做手算断言（见 bl_metrics_selftest.py）。渲染与 CLI 不属于接缝。

被测接缝共 8 个（前 6 项对应「分析器扩展」的 6 项需求，其中「移速对账」按要求拆成两个函数）：

  shield_curves(events)      每个被击中 agent 的盾 HP 轨迹与破盾判定
  shots_to_break(events)     破盾箭数（到盾消失为止的箭类命中数）
  arrow_hits(events)         挨箭分布（每个 agent 被箭命中次数）
  speed_selfcheck(events)    移速自洽核验（位置差分 vs 引擎 speed）
  speed_vs_cap(events)       速度与倍率按兵种分组（不做上限判定，见函数文档）
  reload_durations(events)   装弹时长（受 state 采样间隔限制，是上界）
  ai_param_groups(events)    AI / 精度参数按兵种分组
  death_arrow_stats(events)  阵亡者的挨箭画像（两个口径都报 + 死因）

渲染与 CLI（analyze_metrics / render / main）不属于被测接缝。
"""


import math
import os

import bl_common
import statistics
import sys


def _hits(events):
    return [e for e in events if e.get("t") == "hit"]


def _by_agent(events, kind="state", require=None):
    """把某类事件按 agent 分组，每组内按 ``time`` 升序。

    code-review（2026-09-24）指出 speed_selfcheck 与 reload_durations 各写了一份
    同样的「过滤 → 按 agent 分组 → 排序」样板，这里收拢成一处。``require`` 用于
    只收带某字段的事件（如 reload_durations 只关心带 ``reloading`` 的采样）。
    """
    groups = {}
    for e in events:
        if e.get("t") != kind:
            continue
        if require and require not in e:
            continue
        a = e.get("agent")
        if a is None:
            continue
        groups.setdefault(a, []).append(e)
    for evs in groups.values():
        evs.sort(key=lambda x: float(x.get("time") or 0.0))
    return groups


def shield_curves(events):
    """盾 HP 轨迹 + 破盾判定。

    返回 {agent_id: {"shield_max", "points": [(time, hp), ...], "hits", "first", "last",
                    "reversals", "zero_hp", "vanished", "unshielded_after",
                    "vanish_time", "broken", "break_time"}}。

    **破盾怎么判（2026-09-24 实测修正）**：源码注释原本设想"从 shieldHp 序列归零即可得
    几箭破盾"，但四场 0.7.9 日志实测 ``shieldHp`` **归零条数为 0**（最小值 16/5/1/1）——
    引擎在盾耐久耗尽时是把盾**移除**，而不是留下一条 0 记录。真正的破盾表现是
    ``TryGetShield`` 再也取不到盾 ⇒ 此后的命中事件**不再带 shieldHp 字段**。因此：

      * ``vanished`` = 该 agent 的盾记录结束之后，它**仍被命中**（且不再带 shieldHp）。
        这是实测站得住的破盾判据（要求"之后还有命中"，避免把"战斗刚结束"误判成破盾）；
      * ``zero_hp`` = 是否见过 ``shieldHp <= 0``（实测恒为 False，保留作对照）；
      * ``broken`` = ``vanished or zero_hp``；
      * ``reversals`` = 盾值回升次数（盾槽/盾身份变过的信号，见 TryGetShield 口径）；
      * ``hits`` / ``blocks`` —— **`shieldHp` 字段存在只代表"该次命中时防守方有盾"**（`TryGetShield`
        成功即写字段），**不代表这一箭打中了盾**！打中盾的判据是**盾耐久下降**（源码：
        "打中盾 → 这个值下降；打中身体 → 它不变"，`TelemetryBehavior.cs:250-251`）。
        所以 ``hits`` = 有盾时的命中次数，``blocks`` = 盾真正挡下的次数。
        实测（弓手 40 vs 军团兵 5）：到死挨 15~35 箭，其中 ``blocks`` 占绝大多数、
        **真正扣血的只有 0~6 箭** ⇒ 只报 ``hits`` 会严重高估"挨了几箭"。
    """
    state = {}
    for e in _hits(events):
        d = e.get("defender")
        if d is None:
            continue
        rec = state.setdefault(d, {
            "shield_max": None, "points": [], "hits": 0,
            "first": None, "last": None, "reversals": 0,
            "zero_hp": False, "vanished": False, "unshielded_after": 0,
            "vanish_time": None, "broken": False, "break_time": None, "blocks": 0,
            "_last_shield_time": None, "_zero_time": None,
        })
        if "shieldHp" in e:
            hp = float(e.get("shieldHp") or 0.0)
            t = float(e.get("time") or 0.0)
            if rec["shield_max"] is None and "shieldMax" in e:
                rec["shield_max"] = float(e.get("shieldMax") or 0.0)
            rec["points"].append((t, hp))
            rec["hits"] += 1
            if rec["first"] is None:
                rec["first"] = hp
            rec["last"] = hp
            rec["_last_shield_time"] = t
            if hp <= 0.0:
                if not rec["zero_hp"]:
                    rec["_zero_time"] = t
                rec["zero_hp"] = True
        elif rec["_last_shield_time"] is not None:
            rec["unshielded_after"] += 1
            if rec["vanish_time"] is None:
                rec["vanish_time"] = float(e.get("time") or 0.0)
    out = {}
    for d, rec in state.items():
        if rec["hits"] == 0:
            continue          # 从来没有过盾记录的 agent 不输出
        rec["points"].sort(key=lambda p: p[0])
        rec["reversals"] = sum(1 for i in range(1, len(rec["points"]))
                               if rec["points"][i][1] > rec["points"][i - 1][1])
        # 「打中盾」= 盾耐久**下降**（源码注释原意）。字段存在只代表"该次命中时防守方有盾"。
        rec["blocks"] = sum(1 for i in range(1, len(rec["points"]))
                            if rec["points"][i][1] < rec["points"][i - 1][1])
        rec["vanished"] = rec["unshielded_after"] > 0
        rec["broken"] = bool(rec["vanished"] or rec["zero_hp"])
        rec["break_time"] = rec["vanish_time"] if rec["vanished"] else rec["_zero_time"]
        rec.pop("_last_shield_time", None)
        rec.pop("_zero_time", None)
        out[d] = rec
    return out

def shots_to_break(events):
    """破盾箭数：盾**消失**那一刻（含该次）之前，该 agent 承受的**箭类**命中次数。

    判据见 shield_curves —— 实测引擎从不把盾耐久写成 0，破盾表现为 shieldHp
    字段消失（``vanished``），所以这里是"到盾消失为止"，不是"到归零为止"。

    返回 {agent_id: {"shots": int|None, "shield_max": float|None, "break_time": float|None}}。
    未破盾的 agent 仍保留在结果里（``shots`` = None）—— 报告要能说「未破盾」，
    不能因为没破盾就让它凭空消失。完全没有盾记录的 agent 不出现。
    「箭」用 ``isMissile`` 判定，近战命中不计。
    """
    curves = shield_curves(events)
    out = {}
    for agent, rec in curves.items():
        info = {"shots": None, "shield_max": rec.get("shield_max"),
                "break_time": rec.get("break_time")}
        if rec.get("broken"):
            bt = float(rec.get("break_time") or 0.0)
            info["shots"] = sum(
                1 for e in _hits(events)
                if e.get("defender") == agent and e.get("isMissile")
                and float(e.get("time") or 0.0) <= bt
            )
        out[agent] = info
    return out

def arrow_hits(events):
    """挨箭分布（计数口径）：{agent_id: 被箭命中次数}。

    只数 ``isMissile`` 的 hit（近战不计），只含至少挨过一箭的 agent。
    直方图 / 分位数属于渲染层（本函数只给事实，避免把可视化口径焊进接缝）。
    """
    out = {}
    for e in _hits(events):
        if not e.get("isMissile"):
            continue
        d = e.get("defender")
        if d is None:
            continue
        out[d] = out.get(d, 0) + 1
    return out

def speed_selfcheck(events, min_dt=1e-6):
    """移速对账之一：位置差分速度必须落在两端引擎 ``state.speed`` 之间。

    返回 {agent_id: {"n", "max_outside", "mean_outside",
                    "worst": (time, v_from_position, lo, hi, outside)}}。
    只含至少两个可用采样点的 agent。

    **为什么是"区间外距离"而不是"与均值的差"**：相邻两个采样点之间引擎可能真的在加减速，
    用 ``(speed_p + speed_q) / 2`` 当期望值，会把**真实加减速**误报成"字段不同步"
    （2026-09-24 code-review 指出）。改为：``v_pos`` 落在 ``[min(speed_p, speed_q),
    max(speed_p, speed_q)]`` 内记 0，落到区间外才记差多少 —— 这样非零值才真的是
    位置与速度两个字段互相矛盾。
    """
    out = {}
    for a, evs in _by_agent(events).items():
        errs = []
        worst = None
        for p, q in zip(evs, evs[1:]):
            dt = float(q.get("time") or 0.0) - float(p.get("time") or 0.0)
            if dt <= min_dt:
                continue
            dx = float(q.get("px") or 0.0) - float(p.get("px") or 0.0)
            dy = float(q.get("py") or 0.0) - float(p.get("py") or 0.0)
            dz = float(q.get("pz") or 0.0) - float(p.get("pz") or 0.0)
            v_pos = math.sqrt(dx * dx + dy * dy + dz * dz) / dt
            sp = float(p.get("speed") or 0.0)
            sq = float(q.get("speed") or 0.0)
            lo, hi = min(sp, sq), max(sp, sq)
            # 落在端点速度区间内 ⇒ 0（可由真实加减速解释）；落在外才记距离。
            outside = 0.0 if lo <= v_pos <= hi else min(abs(v_pos - lo), abs(v_pos - hi))
            errs.append(outside)
            if worst is None or outside > worst[4]:
                worst = (float(q.get("time") or 0.0), v_pos, lo, hi, outside)
        if errs:
            out[a] = {"n": len(errs), "max_outside": max(errs),
                      "mean_outside": sum(errs) / len(errs), "worst": worst}
    return out

def speed_vs_cap(events):
    """移速对账之二：实测速度与速度上限的关系（按兵种分组，带负重均值）。

    返回 {troop: {"n", "mean_speed", "mean_max_speed", "mean_combat_speed",
                  "mean_armor_enc", "mean_weap_enc"}}。

    ⚠️ **不做「是否超上限」判定**：``maxSpeed`` / ``combatSpeed`` 实测是
    ``DrivenProperty.MaxSpeedMultiplier`` / ``CombatMaxSpeedMultiplier``（**倍率**，
    源码 TelemetryBehavior.cs:476-477），而 ``speed`` 是世界单位速度 ⇒ 量纲不同。
    早期版本把它当上限做过比较，得出过「91% 超上限」的假结论（2026-09-24 实测纠错）。
    真正的速度上限本遥测**没有采**，要判定须另加字段。
    负重用 armorEnc / weapEnc 的均值带出（不引入分箱规则）。
    """
    groups = {}
    for e in events:
        if e.get("t") != "state":
            continue
        troop = e.get("troop") or "(unknown)"
        groups.setdefault(troop, []).append(e)
    out = {}
    for troop, evs in groups.items():
        n = len(evs)
        speeds = [float(x.get("speed") or 0.0) for x in evs]
        maxes = [float(x.get("maxSpeed") or 0.0) for x in evs]
        combats = [float(x.get("combatSpeed") or 0.0) for x in evs]

        out[troop] = {
            "n": n,
            "mean_speed": sum(speeds) / n,
            "mean_max_speed": sum(maxes) / n,
            "mean_combat_speed": sum(combats) / n,

            "mean_armor_enc": sum(float(x.get("armorEnc") or 0.0) for x in evs) / n,
            "mean_weap_enc": sum(float(x.get("weapEnc") or 0.0) for x in evs) / n,
        }
    return out

def reload_durations(events, sample_interval=2.0):
    """装弹时长（**观测值，上界**）。

    ⚠️ 分辨率 = ``sample_interval``（0.7.9 的 ``state`` 是每 2 秒一行）⇒ 真实装弹时长
    ≤ 本函数给出的观测值，误差最大可达一个采样间隔。要精确测需要把 state 采样调密
    （改 C# + 重部署），见 PROGRESS.md ② 的数据缺口记录。

    口径：start = 首个 ``reloading=true`` 的采样时刻，end = 其后首个 ``reloading=false``
    的时刻，该次时长 = end - start。段尾没有 ``false``（战斗在装弹中途结束）时整段丢弃，
    只计入 ``truncated``，不污染时长分布。

    返回 {agent_id: {"episodes", "durations", "median", "truncated", "resolution"}}。
    """
    out = {}
    for a, evs in _by_agent(events, require="reloading").items():
        durations = []
        truncated = 0
        i, n = 0, len(evs)
        while i < n:
            if not evs[i].get("reloading"):
                i += 1
                continue
            start = float(evs[i].get("time") or 0.0)
            j = i + 1
            while j < n and evs[j].get("reloading"):
                j += 1
            if j < n:
                durations.append(float(evs[j].get("time") or 0.0) - start)
            else:
                truncated += 1
            i = j
        out[a] = {
            "episodes": len(durations),
            "durations": durations,
            "median": statistics.median(durations) if durations else None,
            "truncated": truncated,
            "resolution": float(sample_interval),
        }
    return out

_AI_META = ("t", "time", "agent", "side", "troop")


def ai_param_groups(events):
    """AI / 精度参数按**兵种**分组。

    返回 {"params": [参数名升序], "groups": {troop: {"agents": int, "sides": [...],
          "params": {name: [值集合, 升序]}}}}。

    元数据字段（t / time / agent / side / troop）不算参数。同一兵种内不同 agent 的
    同名参数**可能不同值**（实测：``maxSpeed`` 受负重影响，同兵种内有 30 种取值）
    ⇒ 这里保留**值集合**而不是均值，避免把真实差异抹平。
    """
    groups = {}
    params = set()
    for e in events:
        if e.get("t") != "ai":
            continue
        troop = e.get("troop") or "(unknown)"
        g = groups.setdefault(troop, {"agents": 0, "params": {}, "sides": set()})
        g["agents"] += 1
        if e.get("side"):
            g["sides"].add(e["side"])
        for k, v in e.items():
            if k in _AI_META:
                continue
            params.add(k)
            g["params"].setdefault(k, set()).add(v)
    out_groups = {}
    for troop, g in groups.items():
        out_groups[troop] = {
            "agents": g["agents"],
            "sides": sorted(g["sides"]),
            "params": {k: sorted(v, key=str) for k, v in sorted(g["params"].items())},
        }
    return {"params": sorted(params), "groups": out_groups}

# ── I/O 与渲染层（不属于被测接缝）─────────────────────────────────────

# I/O 与格式化收拢在 bl_common（code-review 2026-09-24：消除 Duplicated Code）。
# 用别名而不是 import from，是为了保住既有公开名（bl_death_compare / 自测都在用）。
load_events = bl_common.load_events


def analyze_metrics(events):
    """便利入口：所有指标一次算齐（供渲染 / 下游消费者用）。

    它**不是**被测接缝 —— 接缝是上面 8 个各自独立的纯函数，这里只做拼装。
    """
    return {
        "shields": shield_curves(events),
        "shots": shots_to_break(events),
        "arrows": arrow_hits(events),
        "speed_self": speed_selfcheck(events),
        "speed_cap": speed_vs_cap(events),
        "reload": reload_durations(events),
        "ai": ai_param_groups(events),
        "deaths": death_arrow_stats(events),
    }


_fmt = bl_common.fmt


def _bar(count, peak, width=36):
    return "#" * int(round(count / peak * width)) if peak else ""


def render(events, top=8):
    """把指标渲染成文本（渲染层，不测）。"""
    res = analyze_metrics(events)
    meta = next((e for e in events if e.get("t") == "meta"), {})
    counts = {}
    for e in events:
        counts[e.get("t")] = counts.get(e.get("t"), 0) + 1
    L = ["BlBridge metrics report - v%s - %s" % (meta.get("version", "?"), meta.get("file", "")),
         "  events: " + "  ".join("%s=%d" % kv for kv in sorted(counts.items(), key=lambda x: -x[1])), ""]

    sh = res["shields"]
    L.append("1) shield HP curve: %d agents have shield records" % len(sh))
    if sh:
        maxes = sorted({c["shield_max"] for c in sh.values() if c["shield_max"] is not None})
        L.append("   shield max values: %s" % ", ".join(_fmt(m, 0) for m in maxes))
        for a, c in sorted(sh.items(), key=lambda kv: -kv[1]["hits"])[:top]:
            L.append("   agent %-4s hit %-3d times   %s -> %s%s" % (
                a, c["hits"], _fmt(c["first"]), _fmt(c["last"]),
                ("   ** shield broken @%.1fs **" % c["break_time"]) if c["broken"] else ""))
        rev = sorted(a for a, c in sh.items() if c["reversals"])
        if rev:
            L.append("   [!] %d agents had shield values rise again (shield slot/identity changed => the 'zero = broken' rule does not hold for them): %s"
                     % (len(rev), ", ".join("agent %s" % a for a in rev[:8])))
    L.append("")

    sb = res["shots"]
    broken = {a: v for a, v in sb.items() if v["shots"] is not None}
    L.append("2) arrows to break: %d/%d agents broke their shield" % (len(broken), len(sb)))
    if broken:
        for a, v in sorted(broken.items(), key=lambda kv: kv[1]["shots"])[:top]:
            L.append("   agent %-4s broke after %d arrows (shield %s, @%.1fs)" % (
                a, v["shots"], _fmt(v["shield_max"], 0), v["break_time"]))
    else:
        L.append("   nobody broke a shield this run => no samples (needs a longer battle; see PROGRESS.md section 2, data gap)")
    L.append("")

    ah = res["arrows"]
    L.append("3) arrows-taken distribution: %d agents took arrows" % len(ah))
    if ah:
        hist = {}
        for n in ah.values():
            hist[n] = hist.get(n, 0) + 1
        peak = max(hist.values())
        for k in sorted(hist):
            L.append("   agents with %-3d arrows: %-3d %s" % (k, hist[k], _bar(hist[k], peak)))
        vals = sorted(ah.values())
        L.append("   total %d arrows; median %s arrows/agent; max %d arrows" % (
            sum(vals), statistics.median(vals), vals[-1]))
    L.append("")

    ss = res["speed_self"]
    L.append("4) movement reconciliation")
    if ss:
        worst = max(ss.items(), key=lambda kv: kv[1]["max_outside"])
        L.append("   position delta vs engine speed (out-of-window distance, 0 = self-consistent): %d agents, %d checkpoints; "
                 "mean %.3f, max %.3f (worst agent %s)" % (
                     len(ss), sum(v["n"] for v in ss.values()),
                     sum(v["mean_outside"] for v in ss.values()) / len(ss),
                     worst[1]["max_outside"], worst[0]))
    else:
        L.append("   position delta vs engine speed: no samples")
    cap = res["speed_cap"]
    L.append("   speed and multipliers ([!] speed is world-unit speed; maxSpeed/combatSpeed are DrivenProperty **multipliers** -- they are not directly comparable):")
    L.append("     %-27s %-7s %-9s %-12s %-14s" % (
        "troop", "samples", "mean_speed", "maxSpeed_mult", "combatSpeed_mult"))
    for troop, v in sorted(cap.items(), key=lambda kv: -kv[1]["n"]):
        L.append("     %-27s %-7d %-9.3f %-12.3f %-14.3f" % (
            troop[:27], v["n"], v["mean_speed"], v["mean_max_speed"],
            v["mean_combat_speed"]))
    L.append("")

    rl = res["reload"]
    eps = [d for v in rl.values() for d in v["durations"]]
    trunc = sum(v["truncated"] for v in rl.values())
    resolution = next(iter(rl.values()))["resolution"] if rl else 0.0
    L.append("5) reload duration: %d agents, %d reload segments; overall median %s s" % (
        len(rl), len(eps), _fmt(statistics.median(eps), 2) if eps else "-"))
    L.append("   [!] resolution +/-%.1f s (state sampling interval) => this is an **upper bound**, not an exact value" % resolution)
    if trunc:
        L.append("   dropped half-segments: %d (battle ended mid-reload)" % trunc)
    L.append("")

    ai = res["ai"]
    L.append("6) AI parameters: %d params x %d troops" % (len(ai["params"]), len(ai["groups"])))
    for troop, g in sorted(ai["groups"].items(), key=lambda kv: -kv[1]["agents"]):
        L.append("   %-27s agents=%-4d params=%-3d sides=%s" % (
            troop[:27], g["agents"], len(g["params"]), ",".join(g["sides"])))
    return "\n".join(L)


def main(argv=None):
    """用法：python bl_metrics.py [battle.jsonl | 目录]（缺省分析最新一场）。"""
    bl_common.safe_streams()
    argv = [a for a in (sys.argv[1:] if argv is None else argv) if not a.startswith("--")]
    path = argv[0] if argv else None
    if path is None:
        path = bl_common.latest_battle()
        if path is None:
            print("no battle logs and no path given.")
            return 2
    if os.path.isdir(path):
        files = bl_common.list_battle_files(path)
    else:
        files = [path]
    for f in files:
        try:
            ev = load_events(f)
        except OSError as exc:
            print("cannot read %s: %s" % (f, exc))
            continue
        print(render(ev))
        print()
    return 0


def death_arrow_stats(events):
    """每个**阵亡者**的挨箭画像 —— 回答「挨几箭才死」。

    返回 {agent_id: {"troop", "maxHp", "death_time", "arrow_hits", "arrow_damaging",
                    "arrow_damage", "arrow_blocked", "melee_hits",
                    "killed_by_weapon", "killed_by_missile"}}。

    **两个口径都报**（实测差异极大，只报一个会得出相反结论）：
      * ``arrow_hits`` = 到死为止承受的**全部**箭类命中（含被盾挡下的）；
      * ``arrow_damaging`` = 其中**真正扣血**（``damagedHp > 0``）的箭数；``arrow_damage`` = 累计扣血。
    实测（T6 弓手 40 vs T5 军团兵 5）：``arrow_hits`` 15~35，而 ``arrow_damaging`` 只有 0~6；
    其中一个挨了 35 箭、累计扣血 **0** 却阵亡 ⇒ 死因是近战（``killed_by_missile`` = False）。
    不看死因，就会把它算成"被箭射死"。
    """
    units = {}
    kills = {}
    for e in events:
        t = e.get("t")
        if t == "unit":
            units[e.get("agent")] = e
        elif t == "kill":
            kills[e.get("victim")] = e
    out = {}
    for a, k in kills.items():
        u = units.get(a, {})
        out[a] = {
            "troop": u.get("troop") or k.get("victimTroop") or "",
            "maxHp": float(u.get("maxHp") or 0.0),
            "death_time": float(k.get("time") or 0.0),
            "arrow_hits": 0, "arrow_damaging": 0, "arrow_damage": 0.0, "arrow_blocked": 0,
            "melee_hits": 0,
            "killed_by_weapon": k.get("weaponClass") or "",
            "killed_by_missile": bool(k.get("isMissile")),
        }
    for e in events:
        if e.get("t") != "hit":
            continue
        a = e.get("defender")
        rec = out.get(a)
        if rec is None:
            continue
        t = float(e.get("time") or 0.0)
        if rec["death_time"] > 0.0 and t > rec["death_time"]:
            continue          # 死亡之后的命中不计（异常数据保护）
        if e.get("isMissile"):
            rec["arrow_hits"] += 1
            dh = float(e.get("damagedHp") or 0.0)
            if dh > 0.0:
                rec["arrow_damaging"] += 1
                rec["arrow_damage"] += dh
            if e.get("blocked"):
                rec["arrow_blocked"] += 1
        else:
            rec["melee_hits"] += 1
    return out

if __name__ == "__main__":
    sys.exit(main())