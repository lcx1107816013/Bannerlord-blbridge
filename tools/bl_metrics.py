#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BlBridge 0.7.9 遥测指标分析器（纯计算层：无打印、无 I/O）。

seam（被测接缝）：每个函数都是「事件流 list[dict] -> 普通 dict/list」的纯函数，
可直接喂合成事件做手算断言（见 bl_metrics_selftest.py）。渲染与 CLI 不属于接缝。

指标一览：
  shield_curves(events)     每个被击中 agent 的盾 HP 轨迹与破盾点
  其它指标随 TDD 切片逐个加入本模块
"""


import io
import json
import math
import os
import statistics
import sys


def _hits(events):
    return [e for e in events if e.get("t") == "hit"]


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
      * ``reversals`` = 盾值回升次数（盾槽/盾身份变过的信号，见 TryGetShield 口径）。
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
            "vanish_time": None, "broken": False, "break_time": None,
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
        rec["vanished"] = rec["unshielded_after"] > 0
        rec["broken"] = bool(rec["vanished"] or rec["zero_hp"])
        rec["break_time"] = rec["vanish_time"] if rec["vanished"] else rec["_zero_time"]
        rec.pop("_last_shield_time", None)
        rec.pop("_zero_time", None)
        out[d] = rec
    return out

def shots_to_break(events):
    """破盾箭数：盾首次归零那一刻（含该次）之前，该 agent 承受的**箭类**命中次数。

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
    """移速对账之一：用同一 agent 相邻两条 ``state`` 的**位置差分**反算速度，
    与引擎直给的 ``state.speed`` 对账（验证位置与 speed 两个字段自洽）。

    返回 {agent_id: {"n": 对账点数, "max_abs_err": float, "mean_abs_err": float,
                    "worst": (time, v_from_position, v_from_field, err)}}。
    只含至少两个可用采样点的 agent。
    """
    by_agent = {}
    for e in events:
        if e.get("t") != "state":
            continue
        a = e.get("agent")
        if a is None:
            continue
        by_agent.setdefault(a, []).append(e)
    out = {}
    for a, evs in by_agent.items():
        evs = sorted(evs, key=lambda x: float(x.get("time") or 0.0))
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
            v_field = (float(p.get("speed") or 0.0) + float(q.get("speed") or 0.0)) / 2.0
            err = abs(v_pos - v_field)
            errs.append(err)
            if worst is None or err > worst[3]:
                worst = (float(q.get("time") or 0.0), v_pos, v_field, err)
        if errs:
            out[a] = {"n": len(errs), "max_abs_err": max(errs),
                      "mean_abs_err": sum(errs) / len(errs), "worst": worst}
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
    by_agent = {}
    for e in events:
        if e.get("t") != "state" or "reloading" not in e:
            continue
        a = e.get("agent")
        if a is None:
            continue
        by_agent.setdefault(a, []).append(e)
    out = {}
    for a, evs in by_agent.items():
        evs = sorted(evs, key=lambda x: float(x.get("time") or 0.0))
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

def load_events(path):
    """读 JSONL 事件流。"""
    ev = []
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev.append(json.loads(line))
            except ValueError:
                continue
    return ev


def default_battles_dir():
    return os.path.join(os.path.expanduser("~"), "Documents",
                        "Mount and Blade II Bannerlord", "BlBridge", "battles")


def analyze_metrics(events):
    """便利入口：6 个指标一次算齐（供渲染 / MCP 用）。

    ⚠️ 它**不是**被测接缝 —— 接缝是上面 6 个各自独立的纯函数，这里只做拼装。
    """
    return {
        "shields": shield_curves(events),
        "shots": shots_to_break(events),
        "arrows": arrow_hits(events),
        "speed_self": speed_selfcheck(events),
        "speed_cap": speed_vs_cap(events),
        "reload": reload_durations(events),
        "ai": ai_param_groups(events),
    }


def _fmt(v, nd=1):
    if v is None:
        return "-"
    if isinstance(v, float):
        return ("%." + str(nd) + "f") % v
    return str(v)


def _bar(count, peak, width=36):
    return "#" * int(round(count / peak * width)) if peak else ""


def render(events, top=8):
    """把指标渲染成文本（渲染层，不测）。"""
    res = analyze_metrics(events)
    meta = next((e for e in events if e.get("t") == "meta"), {})
    counts = {}
    for e in events:
        counts[e.get("t")] = counts.get(e.get("t"), 0) + 1
    L = ["BlBridge 指标报告 · v%s · %s" % (meta.get("version", "?"), meta.get("file", "")),
         "  事件：" + "  ".join("%s=%d" % kv for kv in sorted(counts.items(), key=lambda x: -x[1])), ""]

    sh = res["shields"]
    L.append("① 盾 HP 曲线：%d 个 agent 有盾记录" % len(sh))
    if sh:
        maxes = sorted({c["shield_max"] for c in sh.values() if c["shield_max"] is not None})
        L.append("   盾满值集合：%s" % ", ".join(_fmt(m, 0) for m in maxes))
        for a, c in sorted(sh.items(), key=lambda kv: -kv[1]["hits"])[:top]:
            L.append("   agent %-4s 被打中 %-3d 次   %s → %s%s" % (
                a, c["hits"], _fmt(c["first"]), _fmt(c["last"]),
                ("   ** 已破盾 @%.1fs **" % c["break_time"]) if c["broken"] else ""))
        rev = sorted(a for a, c in sh.items() if c["reversals"])
        if rev:
            L.append("   ⚠️ %d 个 agent 的盾值出现回升（盾槽/盾身份变过 ⇒ 「归零=破盾」口径对它们不成立）：%s"
                     % (len(rev), ", ".join("agent %s" % a for a in rev[:8])))
    L.append("")

    sb = res["shots"]
    broken = {a: v for a, v in sb.items() if v["shots"] is not None}
    L.append("② 破盾箭数：%d/%d 个 agent 破盾" % (len(broken), len(sb)))
    if broken:
        for a, v in sorted(broken.items(), key=lambda kv: kv[1]["shots"])[:top]:
            L.append("   agent %-4s %d 箭破盾（盾 %s，@%.1fs）" % (
                a, v["shots"], _fmt(v["shield_max"], 0), v["break_time"]))
    else:
        L.append("   本局无人破盾 ⇒ 无样本（需更长的战斗；见 PROGRESS.md ② 数据缺口）")
    L.append("")

    ah = res["arrows"]
    L.append("③ 挨箭分布：%d 个 agent 挨过箭" % len(ah))
    if ah:
        hist = {}
        for n in ah.values():
            hist[n] = hist.get(n, 0) + 1
        peak = max(hist.values())
        for k in sorted(hist):
            L.append("   挨 %-3d 箭的 agent：%-3d %s" % (k, hist[k], _bar(hist[k], peak)))
        vals = sorted(ah.values())
        L.append("   合计 %d 箭；中位 %s 箭/人；最多 %d 箭" % (
            sum(vals), statistics.median(vals), vals[-1]))
    L.append("")

    ss = res["speed_self"]
    L.append("④ 移速对账")
    if ss:
        worst = max(ss.items(), key=lambda kv: kv[1]["max_abs_err"])
        L.append("   位置差分 vs 引擎 speed：%d 个 agent、%d 个对账点；"
                 "平均误差 %.3f、最大误差 %.3f（最差 agent %s）" % (
                     len(ss), sum(v["n"] for v in ss.values()),
                     sum(v["mean_abs_err"] for v in ss.values()) / len(ss),
                     worst[1]["max_abs_err"], worst[0]))
    else:
        L.append("   位置差分 vs 引擎 speed：无样本")
    cap = res["speed_cap"]
    L.append("   速度与倍率（⚠️ speed 是世界单位速度；maxSpeed/combatSpeed 是 DrivenProperty **倍率**，两者不可直接比较）：")
    L.append("     %-27s %-7s %-9s %-12s %-14s" % (
        "兵种", "采样", "均速", "maxSpeed倍率", "combatSpeed倍率"))
    for troop, v in sorted(cap.items(), key=lambda kv: -kv[1]["n"]):
        L.append("     %-27s %-7d %-9.3f %-12.3f %-14.3f" % (
            troop[:27], v["n"], v["mean_speed"], v["mean_max_speed"],
            v["mean_combat_speed"]))
    L.append("")

    rl = res["reload"]
    eps = [d for v in rl.values() for d in v["durations"]]
    trunc = sum(v["truncated"] for v in rl.values())
    resolution = next(iter(rl.values()))["resolution"] if rl else 0.0
    L.append("⑤ 装弹时长：%d 个 agent、%d 段装弹；全体中位 %s 秒" % (
        len(rl), len(eps), _fmt(statistics.median(eps), 2) if eps else "-"))
    L.append("   ⚠️ 分辨率 ±%.1f 秒（state 采样间隔）⇒ 这是**上界**，不是精确值" % resolution)
    if trunc:
        L.append("   被丢弃的半段：%d 段（战斗在装弹中途结束）" % trunc)
    L.append("")

    ai = res["ai"]
    L.append("⑥ AI 参数分组：%d 个参数 × %d 个兵种" % (len(ai["params"]), len(ai["groups"])))
    for troop, g in sorted(ai["groups"].items(), key=lambda kv: -kv[1]["agents"]):
        L.append("   %-27s agents=%-4d 参数=%-3d sides=%s" % (
            troop[:27], g["agents"], len(g["params"]), ",".join(g["sides"])))
    return "\n".join(L)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    argv = [a for a in argv if not a.startswith("--")]
    path = argv[0] if argv else None
    if path is None:
        d = default_battles_dir()
        files = [os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".jsonl")] if os.path.isdir(d) else []
        if not files:
            print("没有战斗日志，也没给路径。")
            return 2
        path = max(files, key=os.path.getmtime)
    if os.path.isdir(path):
        files = sorted(os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith(".jsonl"))
    else:
        files = [path]
    for f in files:
        try:
            ev = load_events(f)
        except OSError as exc:
            print("读不了 %s: %s" % (f, exc))
            continue
        if as_json:
            out = {"file": f, "metrics": analyze_metrics(ev)}
            print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        else:
            print(render(ev))
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())