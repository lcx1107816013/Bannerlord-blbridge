#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BlBridge 自测：不需要游戏，用合成数据验证 Python 侧（分析器 + MCP server）是否正确。

  python bl_selftest.py

会做四件事：
  1. 造一场合成战斗 JSONL（每单位的承伤累计恰好等于它的最大血量）
  2. 跑分析器，断言「致死总伤害 vs 最大血量」偏差 ≈ 0（这正是在游戏里校验血量模型用的指标）
  3. 以子进程方式启动 bl_mcp.py，走完 initialize / tools/list / tools/call 全流程
  4. 对真实的 Warbandlord config.xml 做只读回读 + dry-run 写入
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bl_analyze  # noqa: E402

FAIL = []


def check(cond, label, extra=""):
    print(("  [OK] " if cond else "  [FAIL] ") + label + (("  <- " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(label)


# ── 1. 合成一场战斗 ───────────────────────────────────────────────────

def make_battle(path):
    lines = []
    def w(o):
        lines.append(json.dumps(o, ensure_ascii=False))

    w({"t": "meta", "schema": 1, "mod": "BlBridge", "version": "0.1.0",
       "startedUtc": "2026-09-23T10:00:00.000Z", "file": os.path.basename(path)})

    archer_hp, archer_hits, archer_dmg = 112.0, 4, 28
    inf_hp, inf_hits, inf_dmg = 168.0, 6, 28
    archers = list(range(10))
    infantry = list(range(100, 110))

    for i in archers:
        w({"t": "unit", "seq": i + 1, "time": 0.0, "agent": i, "side": "Defender",
           "troop": "test_archer", "level": 26, "isHero": False, "isMounted": False, "maxHp": archer_hp})
    for i in infantry:
        w({"t": "unit", "seq": i - 90, "time": 0.0, "agent": i, "side": "Attacker",
           "troop": "test_infantry", "level": 31, "isHero": False, "isMounted": False, "maxHp": inf_hp})

    seq = 0
    t = 5.0
    for i in infantry:            # 步兵被弓手射杀：6 发 × 28 = 168 = 满血
        for k in range(inf_hits):
            seq += 1
            t += 1.5
            w({"t": "hit", "seq": seq, "time": round(t, 1), "attacker": archers[(i - 100) % 10],
               "defender": i, "aSide": "Defender", "dSide": "Attacker",
               "aTroop": "test_archer", "dTroop": "test_infantry", "weaponClass": "Bow",
               "isMissile": True, "damageType": "Pierce", "bodyPart": "Chest",
               "dmg": inf_dmg, "magnitude": 58.0, "absorbedByArmor": 30.0, "strikeType": "Normal",
               "hpAfter": round(inf_hp - inf_dmg * (k + 1), 1), "hpMax": inf_hp, "mounted": False})
        seq += 1
        w({"t": "kill", "seq": seq, "time": round(t, 1), "victim": i, "killer": archers[(i - 100) % 10],
           "victimTroop": "test_infantry", "killerTroop": "test_archer", "vSide": "Attacker",
           "state": "Killed", "dmg": inf_dmg, "damageType": "Pierce", "bodyPart": "Chest",
           "isMissile": True, "weaponClass": 25})

    # 近战砍击，用来检查分组统计。
    # 注意 hpAfter 必须随每次命中**递减**（实测语义：hpAfter 是扣血后的 HP，
    # 累计实际扣血 = 每次 dmg 之和 = 满血 —— 这正是校验血量模型用的口径）。
    for k in range(2):
        seq += 1
        w({"t": "hit", "seq": seq, "time": round(t + k, 1), "attacker": 100, "defender": 0,
           "aSide": "Attacker", "dSide": "Defender", "aTroop": "test_infantry", "dTroop": "test_archer",
           "weaponClass": "OneHandedSword", "isMissile": False, "damageType": "Cut", "bodyPart": "Arm",
           "dmg": 34, "magnitude": 52.0, "absorbedByArmor": 18.0, "strikeType": "Normal",
           "hpAfter": archer_hp - 34 * (k + 1), "hpMax": archer_hp, "mounted": False})
    for k in range(2):
        seq += 1
        w({"t": "hit", "seq": seq, "time": round(t + 3 + k, 1), "attacker": 101, "defender": 0,
           "aSide": "Attacker", "dSide": "Defender", "aTroop": "test_infantry", "dTroop": "test_archer",
           "weaponClass": "OneHandedSword", "isMissile": False, "damageType": "Cut", "bodyPart": "Arm",
           "dmg": 22, "magnitude": 52.0, "absorbedByArmor": 30.0, "strikeType": "Normal",
           "hpAfter": archer_hp - 34 * 2 - 22 * (k + 1), "hpMax": archer_hp, "mounted": False})
    seq += 1
    w({"t": "kill", "seq": seq, "time": 40.0, "victim": 0, "killer": 100, "victimTroop": "test_archer",
       "killerTroop": "test_infantry", "vSide": "Defender", "state": "Killed", "dmg": 22,
       "damageType": "Cut", "bodyPart": "Arm", "isMissile": False, "weaponClass": 2})

    w({"t": "sample", "time": 30.0, "aAlive": 10, "dAlive": 10, "aHp": 1680.0, "dHp": 1120.0})
    w({"t": "end", "time": 42.0, "aAlive": 10, "dAlive": 9, "aInitial": 10, "dInitial": 10,
       "hits": seq, "kills": 11, "flees": 0})

    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def start_fake_game(logdir, token):
    """假游戏端：轮询 commands/pending，把响应写进 commands/done。
    用来在不启动游戏的情况下验证控制通道（文件 IPC + 会话身份校验）。"""
    import threading
    import time as _t

    pend = os.path.join(logdir, "commands", "pending")
    done = os.path.join(logdir, "commands", "done")
    if not os.path.isdir(pend):
        os.makedirs(pend)
    if not os.path.isdir(done):
        os.makedirs(done)

    with io.open(os.path.join(logdir, "bridge_status.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"mod": "BlBridge", "version": "0.1.1", "protocolVersion": 1,
                             "runToken": token, "pid": os.getpid(),
                             "processStartedUtc": "2026-09-23T00:00:00.000Z", "state": "loaded"}))

    stop = {"v": False}

    def loop():
        while not stop["v"]:
            try:
                names = sorted(os.listdir(pend))
            except OSError:
                names = []
            for f in names:
                if not f.endswith(".json"):
                    continue
                p = os.path.join(pend, f)
                try:
                    with io.open(p, "r", encoding="utf-8") as fh:
                        req = json.load(fh)
                except Exception:  # noqa: BLE001
                    continue
                rid = req.get("id")
                method = req.get("method")
                if method == "status":
                    result = {"state": "running", "busy": True, "lastError": "",
                              "progress": {"aAlive": 18, "dAlive": 11, "aInitial": 20, "dInitial": 20},
                              "result": None}
                elif method == "ping":
                    result = {"protocolVersion": 1, "mod": "BlBridge", "version": "0.1.1"}
                elif method == "start_battle":
                    result = {"accepted": True, "state": "loading"}
                elif method == "abort":
                    result = {"aborted": True, "state": "running"}
                else:
                    result = {}
                resp = {"protocolVersion": 1, "id": rid, "ok": True,
                        "process": {"pid": 4242, "role": "game", "runToken": token,
                                    "processStartedUtc": "2026-09-23T00:00:00.000Z"},
                        "result": result, "error": None}
                out = os.path.join(done, rid + ".json")
                tmpf = out + ".tmp"
                with io.open(tmpf, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(resp))
                os.replace(tmpf, out)
                try:
                    os.remove(p)
                except OSError:
                    pass
            _t.sleep(0.05)

    th = threading.Thread(target=loop)
    th.daemon = True
    th.start()
    return stop


def test_mirror_cross_check():
    """换边双跑交叉验证（④）：位置效应与兵种差异必须可分离，非镜像不许硬算。"""
    import bl_compare
    cfg_a = {"label": "A_X_att", "attacker": "t_x", "defender": "t_y", "a": 20, "d": 20}
    cfg_b = {"label": "B_Y_att", "attacker": "t_y", "defender": "t_x", "a": 20, "d": 20}
    r = bl_compare.mirror_cross_check(cfg_a, cfg_b, [48.2, 48.2, 48.2], [49.6, 49.6, 49.6])
    check(r.get("is_mirror") is True, "识别出镜像双跑", r.get("reason"))
    check(abs(r.get("position_effect", -99) - (-1.1)) < 1e-9,
          "手算位置效应 =(48.2+49.6)/2-50 = -1.1", r.get("position_effect"))
    check(abs(r.get("troop_diff", -99) - (-1.4)) < 1e-9,
          "手算兵种差异 =48.2-49.6 = -1.4", r.get("troop_diff"))
    rows = dict((x["troop"], x) for x in r.get("rows", []))
    check(len(rows) == 2, "两个兵种各一行", list(rows))
    check(abs(rows.get("t_x", {}).get("as_attacker", -99) - 48.2) < 1e-9,
          "t_x 当攻方 = 组A 攻方占比", rows.get("t_x"))
    check(abs(rows.get("t_x", {}).get("as_defender", -99) - 50.4) < 1e-9,
          "t_x 当守方 = 100 - 组B 攻方占比 = 50.4", rows.get("t_x"))
    check(abs(rows.get("t_x", {}).get("diff", -99) - (-2.2)) < 1e-9,
          "t_x 攻-守 = -2.2（= 2x 位置效应）", rows.get("t_x"))
    check(abs(rows.get("t_y", {}).get("diff", -99) - (-2.2)) < 1e-9,
          "t_y 攻-守 必然与 t_x 相等（镜像设计的数学结果）", rows.get("t_y"))

    r2 = bl_compare.mirror_cross_check(
        {"label": "C", "attacker": "t_x", "defender": "t_y", "a": 20, "d": 20},
        {"label": "D", "attacker": "t_x", "defender": "t_z", "a": 20, "d": 20}, [50.0], [50.0])
    check(r2.get("is_mirror") is False, "非镜像被识别出来（旧报告正是在这里硬减）", r2.get("reason"))
    check(r2.get("position_effect") is None, "非镜像不给位置效应", r2.get("position_effect"))

    r3 = bl_compare.mirror_cross_check(
        cfg_a, {"label": "E", "attacker": "t_y", "defender": "t_x", "a": 20, "d": 5}, [50.0], [50.0])
    check(r3.get("is_mirror") is True and bool(r3.get("warning")),
          "兵种互换了但人数没换 ⇒ 仍识别为镜像但给警告", r3.get("warning"))

    r4 = bl_compare.mirror_cross_check({"label": "F"}, {"label": "G"}, [50.0], [50.0])
    check(r4.get("is_mirror") is False, "缺兵种信息 ⇒ 非镜像", r4.get("reason"))


def test_compare_manifest_runs():
    """bl_compare 的**接入层**必须跑得通，且在默认中文控制台（GBK）下也不能崩。

    2026-09-24 踩坑：只测纯函数 mirror_cross_check 远远不够 —— main() 里
    `stats` 结构改了（多了 cfg）却漏改一处解包，纯函数测试全绿而 CLI 直接崩。
    """
    import json
    import subprocess
    import tempfile
    bb = os.path.join(os.path.expanduser("~"), "Documents",
                      "Mount and Blade II Bannerlord", "BlBridge", "battles")
    real = os.path.join(bb, "battle_20260924_014004_626.jsonl")
    if not os.path.isfile(real):
        print("  [skip] 本机没有可用于对比的日志")
        return
    man = {"plan": "selftest-mirror", "runsPerConfig": 1, "configs": [
        {"label": "A_x_att", "attacker": "imperial_legionary", "defender": "battanian_wildling",
         "a": 20, "d": 20, "runs": [{"file": real}]},
        {"label": "B_y_att", "attacker": "battanian_wildling", "defender": "imperial_legionary",
         "a": 20, "d": 20, "runs": [{"file": real}]},
    ]}
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(man, fh, ensure_ascii=False)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "gbk"          # 默认中文 Windows 控制台
        # 子进程带着 PYTHONIOENCODING=gbk ⇒ 它的输出是 **GBK 字节**，必须按 gbk 解码；
        # 按 utf-8 解码会让中文全成乱码、断言假失败（2026-09-24 自己踩过）。
        r = subprocess.run([sys.executable, os.path.join(HERE, "bl_compare.py"), "--manifest", path],
                           capture_output=True, env=env, cwd=HERE)
        err = r.stderr.decode("gbk", "replace")[-200:]
        check(r.returncode == 0, "bl_compare --manifest 在 GBK 控制台下 exit 0", err)
        out = r.stdout.decode("gbk", "replace")
        check("换边双跑交叉验证" in out, "输出含交叉验证小节")
        check("识别为镜像双跑" in out, "两组互换攻守 ⇒ 识别为镜像双跑")
        check("位置效应" in out and "兵种差异" in out, "位置效应与兵种差异都被分离出来")
    finally:
        os.unlink(path)


def test_parse_dummy_armor():
    """bl_common.parse_dummy_armor：严格解析，绝不静默丢弃。

    2026-09-24 §七 教训：`--dummy-armor` 曾因参数类型写错被**静默丢弃**整整 9 场实验。
    所以解析层必须"不认识就报错"，而不是跳过 —— 本测试就是钉住这条性质。
    """
    import bl_common
    check(bl_common.parse_dummy_armor("head=45,torso=35") ==
          {"dummyArmorHead": 45.0, "dummyArmorTorso": 35.0}, "四部位名 → C# 参数名")
    check(bl_common.parse_dummy_armor(" head = 45 , arms=25.5 ") ==
          {"dummyArmorHead": 45.0, "dummyArmorArms": 25.5}, "容忍空白")
    check(bl_common.parse_dummy_armor("") == {}, "空串 → 空 dict（= 不覆盖）")
    check(bl_common.parse_dummy_armor("   ") == {}, "纯空白 → 空 dict")
    for bad, why in (("hed=45", "未知部位名"),
                     ("head=abc", "值非数字"),
                     ("head", "缺 = 号"),
                     ("head=", "缺值"),
                     ("=45", "缺部位名")):
        try:
            bl_common.parse_dummy_armor(bad)
            check(False, "非法输入必须报错（%s）: %s" % (why, bad))
        except ValueError as e:
            check(True, "非法输入报错（%s）: %s" % (why, e))
    try:
        bl_common.parse_dummy_armor({"head": 45})
        check(False, "对象形式必须报错并提示用字符串")
    except ValueError as e:
        check("字符串" in str(e), "对象形式给出可读提示", e)


def test_bl_batch_plan_args():
    """bl_batch 的 plan → CLI 参数组装（纯函数，不需要游戏）。

    覆盖：顶层默认 / 单配置覆盖 / none 不追加 / 护甲字符串透传 / 布尔开关 / 非法即报错。
    """
    import bl_batch
    base = {"attacker": "imperial_legionary", "defender": "battanian_wildling", "a": 20, "d": 10}
    scene, orders, ps, cap = "battle_terrain_a", "charge", "attacker", 30

    A = bl_batch.build_start_args({}, base, scene, orders, ps, cap)
    check(A[0] == "start" and A[1:5] == ["--attacker", "imperial_legionary",
                                        "--defender", "battanian_wildling"],
          "基础参数顺序不变", A[:6])
    check("--dummy-side" not in A and "--dummy-armor" not in A and
          "--unlimited-ammo" not in A and "--freeze-dummies" not in A,
          "未指定靶场参数时一个开关都不追加", A)

    A = bl_batch.build_start_args({"dummySide": "defender"}, base, scene, orders, ps, cap)
    check(A[A.index("--dummy-side") + 1] == "defender", "plan 顶层 dummySide 生效", A)

    A = bl_batch.build_start_args({"dummySide": "defender"}, dict(base, dummySide="attacker"),
                                  scene, orders, ps, cap)
    check(A[A.index("--dummy-side") + 1] == "attacker", "单配置覆盖顶层默认", A)

    A = bl_batch.build_start_args({}, dict(base, dummySide="none"), scene, orders, ps, cap)
    check("--dummy-side" not in A, "dummySide=none 不追加（与 CLI 默认一致）")

    A = bl_batch.build_start_args({}, dict(base, dummyArmor="head=45,torso=35"),
                                  scene, orders, ps, cap)
    check(A[A.index("--dummy-armor") + 1] == "head=45,torso=35", "护甲字符串原样透传（零翻译）", A)

    A = bl_batch.build_start_args({"unlimitedAmmo": True, "freezeDummies": True},
                                  base, scene, orders, ps, cap)
    check("--unlimited-ammo" in A and "--freeze-dummies" in A, "布尔靶场开关被追加", A)

    for bad, why in (("hed=45", "未知部位名"), ("head=abc", "值非数字"), ("head", "缺 = 号")):
        try:
            bl_batch.build_start_args({}, dict(base, dummyArmor=bad), scene, orders, ps, cap)
            check(False, "plan 里非法护甲必须报错（%s）" % why)
        except ValueError as e:
            check(True, "plan 里非法护甲报错（%s）: %s" % (why, e))

    # 非法 dummySide：CLI 的 choices 能拦，但跑批应在**发命令之前**中止（否则每局都要先连一次游戏）
    try:
        bl_batch.build_start_args({}, dict(base, dummySide="bogus"), scene, orders, ps, cap)
        check(False, "plan 里非法 dummySide 必须报错")
    except ValueError as e:
        check(True, "plan 里非法 dummySide 报错: %s" % e)

    # 字符串布尔是把开关写反的经典坑：`"false"` 在 Python 里是真值 ⇒ 必须拒绝，不能静默当真
    for key in ("unlimitedAmmo", "freezeDummies"):
        try:
            bl_batch.build_start_args({}, dict(base, **{key: "false"}), scene, orders, ps, cap)
            check(False, "plan 里 %s 用字符串必须报错" % key)
        except ValueError as e:
            check(True, "plan 里 %s 的字符串布尔被拒: %s" % (key, e))
    A = bl_batch.build_start_args({}, dict(base, unlimitedAmmo=False, freezeDummies=False),
                                  scene, orders, ps, cap)
    check("--unlimited-ammo" not in A and "--freeze-dummies" not in A, "显式 false 不追加开关", A)

    try:
        bl_batch.build_start_args({}, dict(base, dummyArmor={"head": 45}), scene, orders, ps, cap)
        check(False, "plan 里对象形式护甲必须报错")
    except ValueError as e:
        check("字符串" in str(e), "plan 里对象形式给出可读提示", e)


def test_bl_cmd_dummy_armor_strict():
    """bl_cmd.py 的 `--dummy-armor` 必须在**发命令之前**拒掉非法输入。

    否则就是又一次"静默失效"（9 场无效实验的根因）。合法输入这里不测（那要真连游戏）；
    非法输入不会走到游戏。
    """
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(HERE, "bl_cmd.py"), "start",
                        "--attacker", "imperial_legionary", "--defender", "battanian_wildling",
                        "--dummy-armor", "hed=45"],
                       capture_output=True, cwd=HERE)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    check(r.returncode != 0, "未知部位名 → 非 0 退出", r.returncode)
    check("未知部位" in out or "hed" in out, "报错信息指名道姓", out.strip()[:200])


def main():


    tmp = tempfile.mkdtemp(prefix="blbridge_selftest_")
    logdir = os.path.join(tmp, "logs")
    os.makedirs(os.path.join(logdir, "battles"))
    battle = os.path.join(logdir, "battles", "battle_selftest.jsonl")
    make_battle(battle)

    print("=" * 90)
    print("① 分析器：合成数据（每单位承伤累计恰好等于最大血量）")
    print("=" * 90)
    res = bl_analyze.analyze(battle)
    print(bl_analyze.report(res))
    print()
    by = dict((r["troop"], r) for r in res["troops"])
    inf = by.get("test_infantry")
    check(inf is not None, "步兵统计存在")
    if inf:
        check(abs(inf["max_hp_median"] - 168.0) < 0.01, "最大血量识别 = 168", inf["max_hp_median"])
        check(abs(inf["mean_lethal_damage"] - 168.0) < 0.01, "致死总伤害 = 168",
              inf["mean_lethal_damage"])
        check(abs(inf["lethal_dev_pct"]) < 0.5, "血量模型偏差 ≈ 0%", inf["lethal_dev_pct"])
        check(abs(inf["mean_dmg_per_hit"] - 28.0) < 0.01, "每击均伤 = 28", inf["mean_dmg_per_hit"])
        check(abs(inf["hits_per_kill"] - 6.0) < 0.01, "每击杀需 6 击", inf["hits_per_kill"])
    arc = by.get("test_archer")
    if arc:
        check(abs(arc["mean_lethal_damage"] - 112.0) < 0.01, "弓手致死总伤害 = 112（近战 34+22+22+34）",
              arc["mean_lethal_damage"])
    keys = [r["key"] for r in res["damage_by_range"]]
    check("missile" in keys and "melee" in keys, "远近战分组正确", keys)
    check(res["damage_by_type"][0]["key"] in ("Pierce", "Cut"), "伤害类型分组正确",
          [r["key"] for r in res["damage_by_type"]])

    print()
    print("=" * 90)
    print("② MCP server 全流程（子进程 + stdio）")
    print("=" * 90)
    env = dict(os.environ)
    env["BLBRIDGE_LOG_DIR"] = logdir
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "bl_mcp.py")],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, universal_newlines=True, encoding="utf-8")
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "selftest", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "bl_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "bl_analyze", "arguments": {"file": battle, "format": "json"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "bl_read_events", "arguments": {"file": battle, "type": "kill", "limit": 2}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "bl_list_battles", "arguments": {}}},
    ]
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reqs)
    out, err = proc.communicate(payload, timeout=60)
    responses = [json.loads(l) for l in out.splitlines() if l.strip()]
    by_id = dict((r.get("id"), r) for r in responses)

    check(len(responses) >= 6, "收到 6 个响应", len(responses))
    init = by_id.get(1, {}).get("result", {})
    check(init.get("protocolVersion") == "2024-11-05", "initialize 返回协议版本", init.get("protocolVersion"))
    tools = by_id.get(2, {}).get("result", {}).get("tools", [])
    names = sorted(t["name"] for t in tools)
    check(len(tools) == 15, "tools/list 返回 15 个工具", names)
    check("bl_apply_config" in names and "bl_analyze" in names, "关键工具存在", names)
    st = by_id.get(3, {}).get("result", {})
    check(st.get("content"), "bl_status 返回内容")
    an = by_id.get(4, {}).get("result", {})
    body = an.get("content", [{}])[0].get("text", "")
    check("lethal_dev_pct" in body or "致死" in body or "test_infantry" in body,
          "bl_analyze 返回分析结果", body[:120].replace("\n", " "))
    ev = by_id.get(5, {}).get("result", {})
    evt = ev.get("content", [{}])[0].get("text", "")
    check('"t": "kill"' in evt or "kill" in evt, "bl_read_events 过滤生效", evt[:120].replace("\n", " "))
    if err.strip():
        print("  (server stderr) " + err.strip()[:300])

    print()
    print("=" * 90)
    print("③ Warbandlord config.xml：回读 + dry-run 写入")
    print("=" * 90)
    try:
        vals = bl_analyze  # noqa
        import bl_mcp
        got = bl_mcp.read_config(["DamageCalc/ArmorEffect/ArmorBreakPoint",
                                 "Creature/MonsterModify/Human/HitPoints"])
        print("  回读: %s" % json.dumps(got, ensure_ascii=False))
        check(got.get("DamageCalc/ArmorEffect/ArmorBreakPoint") is not None,
              "能读到 ArmorBreakPoint", got)
        dry = bl_mcp.apply_config([{"path": "DamageCalc/ArmorEffect/ArmorBreakPoint", "value": "45"}],
                                  dry_run=True)
        check(dry.get("dryRun") is True and len(dry.get("changed") or {}) == 1,
              "dry-run 能定位并预览改动", dry)
        bad = bl_mcp.apply_config([{"path": "Not/Exist/Path", "value": "1"}], dry_run=True)
        check("Not/Exist/Path" in (bad.get("missing") or []), "无效路径会被报告", bad.get("missing"))
    except Exception as exc:  # noqa: BLE001
        check(False, "config 读写检查", repr(exc))

    print()
    print("=" * 90)
    print("④ 控制通道（文件 IPC + 会话身份校验 + 协议不变式）")
    print("=" * 90)
    os.environ["BLBRIDGE_LOG_DIR"] = logdir
    import bl_mcp
    stop = start_fake_game(logdir, "selftest-token-abc")

    r1, e1 = bl_mcp.send_command("ping", {}, timeout=6)
    check(e1 is None and bool(r1) and r1.get("ok") is True, "ping 往返成功", e1)

    r2, e2 = bl_mcp.send_command("status", {}, timeout=6)
    st = ((r2 or {}).get("result") or {}).get("state")
    check(e2 is None and st == "running", "status 往返并读到状态机", st or e2)

    r3, e3 = bl_mcp.send_command("start_battle", {"attackerTroop": "a", "defenderTroop": "b"}, timeout=6)
    check(e3 is None and ((r3 or {}).get("result") or {}).get("accepted") is True,
          "start_battle 往返成功", e3)

    # 会话身份（照 Coop 规范）：状态文件里的 runToken 变了，旧会话的响应必须被拒
    with io.open(os.path.join(logdir, "bridge_status.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"runToken": "another-session", "protocolVersion": 1}))
    r4, e4 = bl_mcp.send_command("ping", {}, timeout=6)
    check(r4 is None and e4 is not None and "runToken" in (e4 or ""), "旧会话响应被拒绝", e4)

    # ── ⑤ 请求作废 + 超时归因（进程死亡立即返回 / 不留幽灵请求）──────────
    print()
    print("⑤ 请求作废与超时归因（进程退出的早退 + pending 清理）")
    print("=" * 90)
    stop["v"] = True                      # 停掉假游戏端，制造"无人响应"
    time.sleep(0.3)

    pend_dir = os.path.join(logdir, "commands", "pending")
    status_file = os.path.join(logdir, "bridge_status.json")

    # 5.1 进程已退出：必须立即返回 process_exited，而不是傻等到 deadline
    with io.open(status_file, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"runToken": "selftest-token-abc", "pid": 999999, "state": "idle"}))
    t0 = time.time()
    r5, e5 = bl_mcp.send_command("ping", {}, timeout=10)
    dt = time.time() - t0
    check(r5 is None and (e5 or "").startswith("process_exited"), "进程已退出 → process_exited", e5)
    check(dt < 5.0, "进程死亡时立即返回（不傻等 10s）", "%.1fs" % dt)

    # 5.2 请求必须被作废：pending 里不能留东西，否则游戏下次启动会把它执行掉
    leftover = [f for f in os.listdir(pend_dir) if f.endswith(".json")] if os.path.isdir(pend_dir) else []
    check(len(leftover) == 0, "超时后 pending 已清空（不会变成幽灵战斗）", leftover)

    # 5.3 桥未加载：归因码要能区分
    os.remove(status_file)
    code, _msg = bl_mcp._diag_no_response(1.0)
    check(code == "bridge_not_loaded", "无状态文件 → bridge_not_loaded", code)

    # 5.4 存活探测：本进程 pid 必须被判为存活（否则上面的早退会误杀正常等待）
    check(bl_mcp._pid_alive(os.getpid()) is True, "存活探测对现行进程返回 True")
    check(bl_mcp._pid_alive(999999) is False, "存活探测对不存在的 pid 返回 False")
    check(bl_mcp._pid_alive(None) is None, "无法探测时返回 None（未知，不当作失败）")

    # ── ⑥ 构建一致性判定（源码 → 构建产物 → 部署文件 → 进程内 DLL）──────
    print()
    print("⑥ 构建一致性（四段哈希链：源码/构建/部署/进程内）")
    print("=" * 90)
    proj = os.path.join(tmp, "proj")
    src_dir = os.path.join(proj, "src")
    mod_dir = os.path.join(proj, "Modules", "BlBridge")
    bin_dir = os.path.join(mod_dir, "bin", "Win64_Shipping_Client")
    for _d in (src_dir, bin_dir):
        os.makedirs(_d)
    src_a = os.path.join(src_dir, "A.cs")
    with io.open(src_a, "w", encoding="utf-8") as fh:
        fh.write("class A {}\n")
    fake_dll = os.path.join(bin_dir, "BlBridge.dll")
    with io.open(fake_dll, "wb") as fh:
        fh.write(b"fake-dll-v1")
    man_path = os.path.join(mod_dir, "build_manifest.json")

    def write_man(dll_hash, src_hash):
        with io.open(man_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"version": "t", "dllSha256": dll_hash, "sources": {"A.cs": src_hash}}))

    dll_hash = bl_mcp._sha256_file(fake_dll)
    src_hash = bl_mcp._sha256_file(src_a)

    r = bl_mcp.build_check(src_dir, mod_dir)
    check(r["code"] == "manifest_missing", "缺构建清单 → manifest_missing", r["code"])

    write_man("0" * 64, src_hash)
    r = bl_mcp.build_check(src_dir, mod_dir)
    check(r["code"] == "stale_deploy", "DLL 与清单不符 → stale_deploy", r["code"])

    write_man(dll_hash, "0" * 64)
    r = bl_mcp.build_check(src_dir, mod_dir)
    check(r["code"] == "stale_source", "源码哈希不符 → stale_source", r["code"])

    write_man(dll_hash, src_hash)
    saved_log = os.environ.get("BLBRIDGE_LOG_DIR")
    os.environ["BLBRIDGE_LOG_DIR"] = os.path.join(tmp, "logs_empty")
    r = bl_mcp.build_check(src_dir, mod_dir)
    check(r["code"] == "game_offline", "游戏未运行 → game_offline", r["code"])
    os.environ["BLBRIDGE_LOG_DIR"] = saved_log

    # 上一局遗留的状态文件（进程已退出）：不得据此下"进程内是旧 DLL"的结论
    saved_log2 = os.environ.get("BLBRIDGE_LOG_DIR")
    with io.open(status_file, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"runToken": "x", "pid": 999999,
                             "build": {"version": "old", "loadedSha256": "deadbeefdeadbeef",
                                       "fileChangedSinceLoad": True}}))
    r = bl_mcp.build_check(src_dir, mod_dir)
    check(r["code"] == "game_offline", "进程已退出的遗留状态 → game_offline（不误报）", r["code"])
    check("上次会话" in (r.get("detail") or ""), "并在 detail 里说明那是上次会话", r.get("detail"))

    # 进程活着但加载的是旧 DLL：这是真正的 game_not_restarted
    with io.open(status_file, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"runToken": "x", "pid": os.getpid(),
                             "build": {"version": "old", "loadedSha256": "deadbeefdeadbeef",
                                       "fileChangedSinceLoad": True}}))
    r = bl_mcp.build_check(src_dir, mod_dir)
    check(r["code"] == "game_not_restarted", "进程活着 + 旧 DLL → game_not_restarted", r["code"])
    os.environ["BLBRIDGE_LOG_DIR"] = saved_log2 if saved_log2 is not None else logdir

    with io.open(status_file, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"runToken": "x", "pid": os.getpid(),
                             "build": {"version": "t", "loadedSha256": dll_hash[:16],
                                       "fileChangedSinceLoad": False}}))
    r = bl_mcp.build_check(src_dir, mod_dir)
    check(r["code"] == "ok", "四段一致 → ok", r["code"])

    # ── ⑦ 配置（C24）：加载即校验 + 优先级 ────────────────────────────
    print()
    print("⑦ 配置：加载即校验（坏值不拖累好值）+ 优先级")
    print("=" * 90)
    cfg_path = os.path.join(proj, "blbridge.json")
    for _d in (os.path.join(tmp, "logs_from_cfg"), os.path.join(tmp, "game_from_cfg")):
        if not os.path.isdir(_d):
            os.makedirs(_d)
    with io.open(cfg_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"logDir": os.path.join(tmp, "logs_from_cfg"),
                             "gameDir": os.path.join(tmp, "game_from_cfg"),
                             "waitForStateTimeoutSec": 42,
                             "statusTimeoutSec": 9999,
                             "bogusKey": 1}))
    cfg, probs = bl_mcp.load_config(cfg_path, force=True)
    check(cfg.get("waitForStateTimeoutSec") == 42, "合法键被采用", cfg)
    check("statusTimeoutSec" not in cfg, "越界键被拒", cfg)
    check(any("越界" in p for p in probs), "越界原因可见", probs)
    check(any("未知键" in p for p in probs), "未知键被提示", probs)
    check(not any("logDir" in p for p in probs), "合法路径不报错", probs)

    with io.open(cfg_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"logDir": "relative/path", "gameDir": 123}))
    cfg, probs = bl_mcp.load_config(cfg_path, force=True)
    check("logDir" not in cfg and "gameDir" not in cfg, "相对路径与非字符串都被拒", cfg)
    check(sum(1 for p in probs if "必须是" in p) == 2, "两条拒绝原因都在", probs)

    with io.open(cfg_path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    cfg, probs = bl_mcp.load_config(cfg_path, force=True)
    check(cfg == {} and any("解析失败" in p for p in probs), "坏 JSON：整份忽略且不崩", probs)

    check(bl_mcp.log_dir() == logdir, "环境变量优先于配置文件（log_dir 走 env）")

    # ── ⑧ 崩溃判定（A9）──────────────────────────────────────────────
    print()
    print("⑧ 崩溃判定：正常退出 / 崩溃 / 战斗中被杀 / 旧版状态文件")
    print("=" * 90)
    diag_logs = os.path.join(tmp, "diag_logs")
    os.makedirs(diag_logs)
    diag_status = os.path.join(diag_logs, "bridge_status.json")

    r = bl_mcp.run_state_diagnosis(diag_logs)
    check(r["verdict"] == "no_session", "无状态文件 → no_session", r["verdict"])

    def write_diag(payload):
        with io.open(diag_status, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload))

    write_diag({"pid": os.getpid(), "state": "battle", "cleanExit": False, "missionInProgress": True})
    check(bl_mcp.run_state_diagnosis(diag_logs)["verdict"] == "running", "进程存活 → running")

    write_diag({"pid": 999999, "state": "idle", "cleanExit": True, "missionInProgress": False})
    r = bl_mcp.run_state_diagnosis(diag_logs)
    check(r["verdict"] == "clean_exit", "进程退出 + 正常卸载 → clean_exit", r["detail"])

    write_diag({"pid": 999999, "state": "battle", "cleanExit": False, "missionInProgress": True,
                "lastBattle": "battle_x.jsonl"})
    r = bl_mcp.run_state_diagnosis(diag_logs)
    check(r["verdict"] == "crashed_in_battle", "战斗中被杀 → crashed_in_battle", r["detail"])
    check("battle_x.jsonl" in (r["detail"] or ""), "崩溃现场给出战斗文件")

    write_diag({"pid": 999999, "state": "loaded", "cleanExit": False})
    check(bl_mcp.run_state_diagnosis(diag_logs)["verdict"] == "crashed", "非战斗态被强杀 → crashed")

    write_diag({"pid": 999999, "state": "idle"})
    check(bl_mcp.run_state_diagnosis(diag_logs)["verdict"] == "undetermined",
          "旧版状态文件 → undetermined（不瞎猜）")

    stop["v"] = True

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    # ── ⑨ bl_metrics 指标断言（独立模块，纳入主回归链）──────────────
    # code-review 2026-09-24 指出：新指标不在主回归链，等于没人跑。
    print()
    print("=" * 90)
    print("⑨ bl_metrics 指标断言（合成事件手算 + 真实日志 smoke）")
    print("=" * 90)
    import bl_metrics_selftest
    if bl_metrics_selftest.main() != 0:
        FAIL.append("bl_metrics_selftest（详见上方输出）")

    # ── ⑩ 换边双跑交叉验证（④：位置效应 vs 兵种差异必须分离）──────────
    print()
    print("=" * 90)
    print("⑩ bl_compare：换边双跑交叉验证")
    print("=" * 90)
    test_mirror_cross_check()
    test_compare_manifest_runs()

    # ── ⑪ 跑批 plan → CLI 参数（⑤：靶场参数必须能进 plan，且非法输入不许静默）──
    print()
    print("=" * 90)
    print("⑪ bl_batch plan → CLI 参数 + bl_common 护甲解析（严格）")
    print("=" * 90)
    test_parse_dummy_armor()
    test_bl_batch_plan_args()
    test_bl_cmd_dummy_armor_strict()

    if FAIL:
        print("结果: 失败 %d 项 -> %s" % (len(FAIL), FAIL))
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
