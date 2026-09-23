#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bl_metrics.py 的离线自测。

纪律：合成事件流 + 手算期望值（独立真相源），不依赖真实日志、不跑游戏。
用法：python bl_metrics_selftest.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bl_metrics  # noqa: E402

FAIL = []


def check(cond, label, extra=""):
    print(("  [OK] " if cond else "  [FAIL] ") + label + (("  <- " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(label)


# ── 合成事件：agent 7 带盾（shieldMax 100），挨 3 箭 + 1 近战后破盾；agent 9 无盾 ──
def shield_sample():
    return [
        {"t": "meta", "version": "0.7.9"},
        {"t": "hit", "time": 1.0, "attacker": 1, "defender": 7, "isMissile": True, "shieldHp": 80.0, "shieldMax": 100.0},
        {"t": "hit", "time": 2.0, "attacker": 1, "defender": 7, "isMissile": False, "shieldHp": 60.0, "shieldMax": 100.0},
        {"t": "hit", "time": 3.0, "attacker": 1, "defender": 7, "isMissile": True, "shieldHp": 20.0, "shieldMax": 100.0},
        {"t": "hit", "time": 4.0, "attacker": 1, "defender": 7, "isMissile": True, "shieldHp": 0.0, "shieldMax": 100.0},
        {"t": "hit", "time": 5.0, "attacker": 2, "defender": 9, "isMissile": True, "dmg": 30},
    ]


def test_shield_curves():
    cur = bl_metrics.shield_curves(shield_sample())
    check(7 in cur, "有盾记录的 agent 出现在结果里", sorted(cur))
    c = cur.get(7, {})
    check(c.get("shield_max") == 100.0, "shield_max 取自事件的 shieldMax", c.get("shield_max"))
    check([p[1] for p in c.get("points", [])] == [80.0, 60.0, 20.0, 0.0], "盾 HP 轨迹按时间序", c.get("points"))
    check(c.get("hits") == 4, "hits = 盾被击中的次数", c.get("hits"))
    check(c.get("last") == 0.0, "last = 末次盾值", c.get("last"))
    check(c.get("broken") is True, "broken = 出现盾值 <= 0", c.get("broken"))
    check(c.get("break_time") == 4.0, "break_time = 首次归零的时间", c.get("break_time"))
    check(9 not in cur, "无 shieldHp 的命中不进曲线", sorted(cur))


def unbroken_sample():
    return [
        {"t": "hit", "time": 1.0, "defender": 3, "isMissile": True, "shieldHp": 50.0, "shieldMax": 100.0},
        {"t": "hit", "time": 2.0, "defender": 3, "isMissile": True, "shieldHp": 30.0, "shieldMax": 100.0},
    ]


def test_shots_to_break():
    r = bl_metrics.shots_to_break(shield_sample())
    check(7 in r, "破盾箭数含 agent 7", sorted(r))
    check(r.get(7, {}).get("shots") == 3, "破盾前承受 3 箭（那一击近战不计）", r.get(7))
    check(r.get(7, {}).get("shield_max") == 100.0, "带出 shield_max 便于报「几箭 / 多少盾」", r.get(7))
    check(r.get(7, {}).get("break_time") == 4.0, "带出 break_time", r.get(7))
    check(9 not in r, "无盾记录的 agent 不进结果", sorted(r))
    u = bl_metrics.shots_to_break(unbroken_sample())
    check(u.get(3, {}).get("shots") is None, "未破盾的 agent 保留在结果里、shots=None", u.get(3))
    check(3 in u, "未破盾 agent 不被丢掉", sorted(u))


def melee_only_sample():
    return [{"t": "hit", "time": 1.0, "defender": 5, "isMissile": False, "dmg": 20}]


def test_arrow_hits():
    r = bl_metrics.arrow_hits(shield_sample())
    check(r.get(7) == 3, "agent 7 挨了 3 箭（近战那一击不计）", r)
    check(r.get(9) == 1, "agent 9 挨了 1 箭", r)
    check(len(r) == 2, "只含有箭命中的 agent", sorted(r))
    check(bl_metrics.arrow_hits(melee_only_sample()) == {}, "纯近战样本 ⇒ 空结果（不是 0 值条目）",
          bl_metrics.arrow_hits(melee_only_sample()))


def speed_sample():
    """agent 1 匀速直线：0s@x=0, 1s@x=2, 2s@x=5（相邻两段位置差分 2.0 与 3.0）。"""
    return [
        {"t": "state", "time": 0.0, "agent": 1, "px": 0.0, "py": 0.0, "pz": 0.0, "speed": 2.0,
         "maxSpeed": 3.0, "combatSpeed": 2.5, "troop": "t_a", "armorEnc": 10.0, "weapEnc": 20.0},
        {"t": "state", "time": 1.0, "agent": 1, "px": 2.0, "py": 0.0, "pz": 0.0, "speed": 2.5,
         "maxSpeed": 3.0, "combatSpeed": 2.5, "troop": "t_a", "armorEnc": 10.0, "weapEnc": 20.0},
        {"t": "state", "time": 2.0, "agent": 1, "px": 5.0, "py": 0.0, "pz": 0.0, "speed": 3.0,
         "maxSpeed": 3.0, "combatSpeed": 2.5, "troop": "t_a", "armorEnc": 10.0, "weapEnc": 20.0},
    ]


def test_speed_selfcheck():
    r = bl_metrics.speed_selfcheck(speed_sample())
    check(1 in r, "agent 1 出现", sorted(r))
    check(r.get(1, {}).get("n") == 2, "两段相邻采样 = 2 个对账点", r.get(1))
    check(abs(r.get(1, {}).get("max_abs_err", -1) - 0.25) < 1e-9,
          "手算：段1 |2.0-(2.0+2.5)/2|=0.25；段2 |3.0-(2.5+3.0)/2|=0.25", r.get(1))
    one = bl_metrics.speed_selfcheck([{"t": "state", "time": 0.0, "agent": 2, "px": 0.0, "speed": 1.0}])
    check(one == {}, "只有单个采样点的 agent 不算（无从差分）", one)


def cap_sample():
    """t_a 三条（speed 2.0/2.5/3.0，上限 3.0）；t_b 一条 speed=4.0 超上限（3.0）。"""
    return speed_sample() + [
        {"t": "state", "time": 0.0, "agent": 9, "px": 0.0, "py": 0.0, "pz": 0.0, "speed": 4.0,
         "maxSpeed": 3.0, "combatSpeed": 3.0, "troop": "t_b", "armorEnc": 40.0, "weapEnc": 10.0},
    ]


def test_speed_vs_cap():
    r = bl_metrics.speed_vs_cap(cap_sample())
    check(r.get("t_a", {}).get("n") == 3, "t_a 三条采样", r.get("t_a"))
    check(abs(r.get("t_a", {}).get("mean_speed", -1) - 2.5) < 1e-9, "手算 t_a 均速 =(2.0+2.5+3.0)/3=2.5", r.get("t_a"))
    check("over_max_pct" not in r.get("t_a", {}),
          "无 over_max_pct（maxSpeed 是 DrivenProperty **倍率**，不是速度上限；TelemetryBehavior.cs:476）",
          sorted(r.get("t_a", {})))
    check(abs(r.get("t_b", {}).get("mean_speed", -1) - 4.0) < 1e-9, "t_b 均速 4.0", r.get("t_b"))
    check(abs(r.get("t_b", {}).get("mean_max_speed", -1) - 3.0) < 1e-9,
          "保留倍率均值供按兵种对比（speed 1.751 vs 倍率 0.548 这种对比以前被误读成「超上限」）",
          r.get("t_b"))
    check(abs(r.get("t_a", {}).get("mean_armor_enc", -1) - 10.0) < 1e-9, "带出负重均值 armorEnc", r.get("t_a"))


def reload_sample():
    """agent 1：装弹区间 2→6（观测 4.0 秒）与 8→10（2.0 秒）；agent 2：开场就在装弹、没有结束边沿。"""
    return [
        {"t": "state", "time": 0.0, "agent": 1, "reloading": False, "reloadPhase": 1},
        {"t": "state", "time": 2.0, "agent": 1, "reloading": True, "reloadPhase": 0},
        {"t": "state", "time": 4.0, "agent": 1, "reloading": True, "reloadPhase": 0},
        {"t": "state", "time": 6.0, "agent": 1, "reloading": False, "reloadPhase": 1},
        {"t": "state", "time": 8.0, "agent": 1, "reloading": True, "reloadPhase": 0},
        {"t": "state", "time": 10.0, "agent": 1, "reloading": False, "reloadPhase": 1},
        {"t": "state", "time": 0.0, "agent": 2, "reloading": True, "reloadPhase": 0},
    ]


def test_reload_durations():
    r = bl_metrics.reload_durations(reload_sample())
    check(r.get(1, {}).get("episodes") == 2, "两段装弹", r.get(1))
    check(r.get(1, {}).get("durations") == [4.0, 2.0], "手算：(2→6)=4.0、(8→10)=2.0", r.get(1))
    check(r.get(1, {}).get("median") == 3.0, "中位数 3.0", r.get(1))
    check(r.get(1, {}).get("resolution") == 2.0, "带出采样分辨率（±2 秒，必须让读者看到）", r.get(1))
    check(r.get(2, {}).get("truncated") == 1, "段尾没有 false ⇒ 整段丢弃并计入 truncated", r.get(2))
    check(r.get(2, {}).get("episodes") == 0, "truncated 段不计入 episodes", r.get(2))


def ai_sample():
    """t_a 两个 agent（blockAbility 0.8 / 0.9，其余同值）；t_b 一个 agent。"""
    return [
        {"t": "ai", "time": 0.0, "agent": 0, "side": "Attacker", "troop": "t_a", "level": 26,
         "blockAbility": 0.8, "shootFreq": 0.5},
        {"t": "ai", "time": 0.0, "agent": 1, "side": "Attacker", "troop": "t_a", "level": 26,
         "blockAbility": 0.9, "shootFreq": 0.5},
        {"t": "ai", "time": 0.0, "agent": 2, "side": "Defender", "troop": "t_b", "level": 31,
         "blockAbility": 0.7, "shootFreq": 0.6},
    ]


def test_ai_param_groups():
    r = bl_metrics.ai_param_groups(ai_sample())
    check(r.get("params") == ["blockAbility", "level", "shootFreq"],
          "参数名升序，且元数据（t/time/agent/side/troop）不算参数", r.get("params"))
    g = r.get("groups", {})
    check(g.get("t_a", {}).get("agents") == 2, "t_a 两个 agent", g.get("t_a"))
    check(g.get("t_a", {}).get("params", {}).get("blockAbility") == [0.8, 0.9],
          "同兵种内的不同值保留成集合（不抹平成均值）", g.get("t_a", {}).get("params", {}).get("blockAbility"))
    check(g.get("t_a", {}).get("params", {}).get("shootFreq") == [0.5], "同值的参数收敛成一个值", None)
    check(g.get("t_b", {}).get("params", {}).get("level") == [31], "t_b 的 level", g.get("t_b", {}).get("params"))
    check(bl_metrics.ai_param_groups([{"t": "hit"}]).get("params") == [], "无 ai 事件 ⇒ 空参数表",
          bl_metrics.ai_param_groups([{"t": "hit"}]))


def test_real_log_smoke():
    """对真实 0.7.9 日志的集成 smoke。

    期望值全部来自**早先独立解析**得到的外部事实（不是用 bl_metrics 重算）：
    场 2 = battle_20260924_014105_968.jsonl（弓手 20 vs 军团兵靶 20）。
    本机没有该日志时跳过 —— 不能因为本地没跑过游戏就让自测变红。
    """
    path = os.path.join(os.path.expanduser("~"), "Documents", "Mount and Blade II Bannerlord",
                        "BlBridge", "battles", "battle_20260924_014105_968.jsonl")
    if not os.path.isfile(path):
        print("  [skip] 真实日志不在本机：%s" % os.path.basename(path))
        return
    ev = bl_metrics.load_events(path)
    cur = bl_metrics.shield_curves(ev)
    check(len(cur) == 20, "场2：20 个靶子都有盾记录（外部事实）", len(cur))
    check(max(c["shield_max"] for c in cur.values() if c["shield_max"] is not None) == 530.0,
          "场2：靶子盾满值 530（外部事实）", max(c["shield_max"] for c in cur.values()))
    st = bl_metrics.shots_to_break(ev)
    broken2 = sorted(a for a, v in st.items() if v["shots"] is not None)
    check(len(st) == 20, "场2：20 个 agent 有盾记录（外部事实）", len(st))
    check(len(broken2) == 4,
          "场2：4 个 agent 的盾被打掉（独立统计：盾记录结束后仍被命中；旧「归零」判据漏报）", broken2)
    check(sum(bl_metrics.arrow_hits(ev).values()) == 153,
          "场2：箭类命中合计 153 次（145 Arrow 打靶 + 8 Javelin 打弓手，外部事实）",
          sum(bl_metrics.arrow_hits(ev).values()))
    check(len(bl_metrics.arrow_hits(ev)) == 27,
          "场2：27 个 agent 挨过箭（含被标枪打中的弓手，外部事实）",
          len(bl_metrics.arrow_hits(ev)))
    check(len(bl_metrics.speed_selfcheck(ev)) == 40, "场2：40 个 agent（= unit/ai 事件数）都有移速对账样本（外部事实）",
          len(bl_metrics.speed_selfcheck(ev)))
    ai = bl_metrics.ai_param_groups(ev)
    check(sorted(ai["groups"]) == ["battanian_fian_champion", "imperial_legionary"],
          "场2：两个兵种（外部事实）", sorted(ai["groups"]))
    check(len(bl_metrics.reload_durations(ev)) == 40, "场2：40 个 agent 有装弹观测（外部事实）",
          len(bl_metrics.reload_durations(ev)))


def reversal_sample():
    """盾 100 → 50 → 90：中间出现回升 ⇒ 该 agent 的盾槽/盾身份变过。"""
    return [
        {"t": "hit", "time": 1.0, "defender": 1, "isMissile": True, "shieldHp": 100.0, "shieldMax": 100.0},
        {"t": "hit", "time": 2.0, "defender": 1, "isMissile": True, "shieldHp": 50.0, "shieldMax": 100.0},
        {"t": "hit", "time": 3.0, "defender": 1, "isMissile": True, "shieldHp": 90.0, "shieldMax": 100.0},
    ]


def test_shield_curves_reversals():
    c = bl_metrics.shield_curves(reversal_sample())[1]
    check(c.get("reversals") == 1, "盾值回升被计数（口径可疑的信号）", c.get("reversals"))
    check(bl_metrics.shield_curves(shield_sample())[7].get("reversals") == 0,
          "单调下降 ⇒ reversals 为 0", bl_metrics.shield_curves(shield_sample())[7].get("reversals"))


def vanish_sample():
    """盾 100 → 60 → 10 后被打破：此后命中不再带 shieldHp（引擎移除盾，而不是写 0）。"""
    return [
        {"t": "hit", "time": 1.0, "defender": 1, "isMissile": True, "shieldHp": 100.0, "shieldMax": 100.0},
        {"t": "hit", "time": 2.0, "defender": 1, "isMissile": False, "shieldHp": 60.0, "shieldMax": 100.0},
        {"t": "hit", "time": 3.0, "defender": 1, "isMissile": True, "shieldHp": 10.0, "shieldMax": 100.0},
        {"t": "hit", "time": 4.0, "defender": 1, "isMissile": True, "dmg": 40.0},
        {"t": "hit", "time": 5.0, "defender": 1, "isMissile": False, "dmg": 30.0},
    ]


def test_shield_vanished():
    c = bl_metrics.shield_curves(vanish_sample())[1]
    check(c.get("vanished") is True, "vanished = 盾记录结束后仍被命中（实测的破盾表现）", c.get("vanished"))
    check(c.get("unshielded_after") == 2, "盾消失后仍有 2 次命中", c.get("unshielded_after"))
    check(c.get("vanish_time") == 4.0, "vanish_time = 第一条不带 shieldHp 的命中时刻", c.get("vanish_time"))
    check(c.get("broken") is True, "broken 成立", c.get("broken"))
    check(c.get("break_time") == 4.0, "break_time 指向盾消失时刻", c.get("break_time"))
    check(c.get("zero_hp") is False, "zero_hp=False（引擎从不写 0）", c.get("zero_hp"))
    old = bl_metrics.shield_curves(shield_sample())[7]
    check(old.get("broken") is True and old.get("zero_hp") is True, "旧样例仍算破盾（靠 zero_hp 分支）", old.get("broken"))
    check(old.get("vanished") is False, "旧样例没有「消失」证据 ⇒ vanished=False", old.get("vanished"))
    check(old.get("break_time") == 4.0, "旧样例 break_time 仍指向归零时刻（向后兼容）", old.get("break_time"))


def test_shots_to_break_after_vanish():
    r = bl_metrics.shots_to_break(vanish_sample())
    check(r.get(1, {}).get("shots") == 3,
          "破盾箭数按「到盾消失时刻（含）」数：t1/t3/t4 共 3 箭（t2 近战不计）", r.get(1))
    check(r.get(1, {}).get("break_time") == 4.0, "break_time 随新判据", r.get(1))


def block_sample():
    """盾 100 → 100（打身体，不变）→ 60（打中盾）→ 60：只有 1 次真的被盾挡下。"""
    return [
        {"t": "hit", "time": 1.0, "defender": 1, "isMissile": True, "shieldHp": 100.0, "shieldMax": 100.0},
        {"t": "hit", "time": 2.0, "defender": 1, "isMissile": True, "shieldHp": 100.0, "shieldMax": 100.0},
        {"t": "hit", "time": 3.0, "defender": 1, "isMissile": True, "shieldHp": 60.0, "shieldMax": 100.0},
        {"t": "hit", "time": 4.0, "defender": 1, "isMissile": True, "shieldHp": 60.0, "shieldMax": 100.0},
    ]


def test_shield_blocks():
    c = bl_metrics.shield_curves(block_sample())[1]
    check(c.get("hits") == 4, "hits = 有盾时的命中次数（字段存在口径）", c.get("hits"))
    check(c.get("blocks") == 1,
          "blocks = 盾耐久真正下降的次数（打中身体不降）—— 这才是「打中盾」", c.get("blocks"))


def death_sample():
    """agent 1（maxHp 100）挨 2 箭（其中 1 箭被盾挡下）+ 1 次近战，最后被近战砍死。"""
    return [
        {"t": "unit", "agent": 1, "troop": "t_x", "maxHp": 100.0},
        {"t": "hit", "time": 1.0, "defender": 1, "isMissile": True, "damagedHp": 30.0, "weaponClass": "Arrow"},
        {"t": "hit", "time": 2.0, "defender": 1, "isMissile": True, "damagedHp": 0.0, "blocked": True,
         "weaponClass": "Arrow", "shieldHp": 60.0},
        {"t": "hit", "time": 3.0, "defender": 1, "isMissile": False, "damagedHp": 20.0, "weaponClass": "OneHandedSword"},
        {"t": "kill", "time": 4.0, "victim": 1, "killer": 9, "state": "Killed",
         "victimTroop": "t_x", "weaponClass": "OneHandedSword", "isMissile": False},
    ]


def test_death_arrow_stats():
    r = bl_metrics.death_arrow_stats(death_sample())
    d = r.get(1, {})
    check(d.get("arrow_hits") == 2, "arrow_hits = 全部箭命中（含被挡下的）", d.get("arrow_hits"))
    check(d.get("arrow_damaging") == 1, "arrow_damaging = 真正扣血的箭只有 1 支", d.get("arrow_damaging"))
    check(d.get("arrow_damage") == 30.0, "arrow_damage = 30.0", d.get("arrow_damage"))
    check(d.get("arrow_blocked") == 1, "arrow_blocked = 1", d.get("arrow_blocked"))
    check(d.get("melee_hits") == 1, "melee_hits = 1", d.get("melee_hits"))
    check(d.get("troop") == "t_x" and d.get("maxHp") == 100.0, "带出兵种与满血", (d.get("troop"), d.get("maxHp")))
    check(d.get("killed_by_missile") is False, "killed_by_missile=False ⇒ 死于近战", d.get("killed_by_missile"))
    check(bl_metrics.death_arrow_stats([{"t": "meta"}]) == {}, "没有 kill 事件 ⇒ 空结果",
          bl_metrics.death_arrow_stats([{"t": "meta"}]))


def test_cli_survives_gbk_console():
    """默认中文 Windows 控制台（locale=gbk）下，CLI 不能因输出字符而崩溃。

    2026-09-24 复现：不设 PYTHONIOENCODING 时 Python 用 gbk 写 stdout，
    render() 输出里的 ``⇒``（U+21D2）抛 UnicodeEncodeError ⇒ 整个 CLI exit 1。
    既有工具如 bl_dummy_analyze.py 在同样条件下 exit 0，所以这是本模块引入的回归。
    """
    import json
    import subprocess
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            for e in shield_sample():
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "gbk"
        r = subprocess.run([sys.executable, os.path.join(HERE, "bl_metrics.py"), tmp],
                           capture_output=True, env=env, cwd=HERE)
        check(r.returncode == 0, "GBK stdout 下 CLI exit 0（默认中文 Windows 控制台）",
              r.stderr.decode("utf-8", "replace")[-150:])
    finally:
        os.unlink(tmp)


def main():
    print("=" * 88)
    print("bl_metrics 自测（合成事件 + 手算期望）")
    test_shield_curves()
    test_shots_to_break()
    test_arrow_hits()
    test_speed_selfcheck()
    test_speed_vs_cap()
    test_reload_durations()
    test_ai_param_groups()
    test_real_log_smoke()
    test_shield_curves_reversals()
    test_shield_vanished()
    test_shots_to_break_after_vanish()
    test_shield_blocks()
    test_death_arrow_stats()
    test_cli_survives_gbk_console()
    print("-" * 88)
    if FAIL:
        print("结果: %d 项失败" % len(FAIL))
        for f in FAIL:
            print("  - " + f)
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())