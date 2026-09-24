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


def test_parse_squad_groups():
    """多兵种/战术组的 DSL 解析（v0.8.8）：合法要准，非法必须报错。

    为什么必须严格：C# 侧拿到的是**字符串**，一旦我们把非法输入"宽容"地透传过去，
    就又是一次"参数静默失效"（2026-09-24 有 9 场实验正是这么作废的）。
    """
    import bl_common
    g = bl_common.parse_squad_groups("imperial_legionary:10:Infantry:hold")
    check(g == [{"troop": "imperial_legionary", "count": 10, "formation": "Infantry",
                 "movement": "hold"}], "四字段组", g)
    g2 = bl_common.parse_squad_groups("khuzait_khans_guard:5")
    check(g2 == [{"troop": "khuzait_khans_guard", "count": 5, "formation": None,
                  "movement": None}], "两字段：formation/movement 缺省为 None", g2)
    g3 = bl_common.parse_squad_groups("a:1|b:2:HorseArcher")
    check(len(g3) == 2 and g3[1]["formation"] == "HorseArcher" and g3[0]["formation"] is None,
          "多组用 | 分隔、各组独立", g3)
    g4 = bl_common.parse_squad_groups("a:1:infantry:HOLD")
    check(g4[0]["formation"] == "Infantry" and g4[0]["movement"] == "hold",
          "formation 大小写不敏感→规范名；movement 归小写", g4)
    check(bl_common.parse_squad_groups(None) == [] and bl_common.parse_squad_groups("   ") == [],
          "None / 纯空白 ⇒ 空列表")
    for bad, why in (("a", "字段数 1"),
                     ("a:1:Infantry:hold:extra", "字段数 5"),
                     ("a:0", "count=0"),
                     ("a:abc", "count 非整数"),
                     ("a:1:Infantryy", "未知 formation"),
                     ("a:1:Infantry:jump", "未知 movement"),
                     ("a:1||b:2", "中间空组"),
                     (":1", "缺兵种 id")):
        try:
            bl_common.parse_squad_groups(bad)
            check(False, "非法组串必须报错（%s）: %r" % (why, bad))
        except ValueError as e:
            check(True, "非法组串报错（%s）" % why)
    try:
        bl_common.parse_squad_groups(123)
        check(False, "非字符串必须报错")
    except ValueError as e:
        check(True, "非字符串报错: %s" % e)


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

    A = bl_batch.build_start_args({}, dict(base, dummyBodyItem="plated_leather_coat"),
                                  scene, orders, ps, cap)
    check(A[A.index("--dummy-body-item") + 1] == "plated_leather_coat", "身甲物品透传（材质对照用）", A)
    check("--dummy-body-item" not in bl_batch.build_start_args({}, base, scene, orders, ps, cap),
          "未指定身甲物品时不追加开关")

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

    # ── 多轮参数（v0.8.5；③「换边双跑」用它）：plan 透传 + 非法即报错，绝不静默 ──
    R = bl_batch.build_start_args({"rounds": 2, "roundEndAlive": 1, "roundSwap": True},
                                  base, scene, orders, ps, cap)
    check(R[-1] == "--round-swap" and "--rounds" in R and "2" in R
          and "--round-end-alive" in R, "plan 的 rounds/roundEndAlive/roundSwap 进 CLI", R[-6:])
    # bl_cmd.py 只在给了 rounds 时才解析其余 round* 键 ⇒ 单独给会被静默忽略 ⇒ 必须报错
    for bad in ({"roundSwap": True}, {"roundEndAlive": 1}, {"roundSpawnAttacker": "1,2"}):
        try:
            bl_batch.build_start_args(bad, base, scene, orders, ps, cap)
            check(False, "只给 round* 不给 rounds 必须报错: %r" % (bad,))
        except ValueError as e:
            check("rounds" in str(e), "缺 rounds 时 round* 被拒", str(e)[:60])
    for bad in ({"rounds": "2"}, {"rounds": 1}, {"rounds": True},
                {"rounds": 2, "roundSwap": "true"}, {"rounds": 2, "roundEndAlive": -1}):
        try:
            bl_batch.build_start_args(bad, base, scene, orders, ps, cap)
            check(False, "非法多轮参数必须报错: %r" % (bad,))
        except ValueError as e:
            check(True, "非法多轮参数被拒: %r" % (bad,))


def test_squad_plan_args():
    """plan 的多兵种/战术组（v0.8.8）：组列表与 DSL 两条入口、非法一律报错、旧路径零影响。"""
    import bl_batch
    scene, orders, ps, cap = "battle_terrain_a", "charge", "attacker", 30
    base = {"attacker": "a", "defender": "b"}
    A = bl_batch.build_start_args({}, dict(base, attackerGroups=[
        {"troop": "imperial_legionary", "count": 10, "formation": "Infantry", "movement": "hold"},
        {"troop": "khuzait_khans_guard", "count": 5},
    ]), scene, orders, ps, cap)
    check("--attacker-groups" in A, "组列表 ⇒ CLI --attacker-groups", A[-2:])
    dsl = A[A.index("--attacker-groups") + 1]
    check(dsl == "imperial_legionary:10:Infantry:hold|khuzait_khans_guard:5",
          "组列表转 DSL（缺省字段省略）", dsl)
    check("--defender-groups" not in A, "只给攻方组时不产生守方参数")
    B = bl_batch.build_start_args({}, dict(base, defenderGroups="b:2:Ranged"),
                                  scene, orders, ps, cap)
    check(B[B.index("--defender-groups") + 1] == "b:2:Ranged", "DSL 字符串原样透传", B[-2:])
    # GC2：完全没有组字段 ⇒ 不产生新参数（"旧路径逐字节不变"由既有断言守）
    C = bl_batch.build_start_args({}, base, scene, orders, ps, cap)
    check("--attacker-groups" not in C and "--defender-groups" not in C,
          "GC2：旧字段路径不产生新参数", C)
    # 关键回归：**没给 rounds** 时组参数也必须出现（build_start_args 有一个 early-return 分支）
    D = bl_batch.build_start_args({}, dict(base, attackerGroups="a:1"), scene, orders, ps, cap)
    check("--attacker-groups" in D, "无 rounds 时组参数不被早退分支吃掉", D[-2:])
    for bad in ([{"troop": "a"}],
                [{"troop": "a", "count": 1, "movement": "hold"}],
                [{"troop": "a", "count": 1, "bogus": 2}],
                "a:0",
                "a:1:Infantry:jump"):
        try:
            bl_batch.build_start_args({}, dict(base, attackerGroups=bad), scene, orders, ps, cap)
            check(False, "非法组必须报错: %r" % (bad,))
        except ValueError:
            check(True, "非法组被拒: %r" % (bad,))


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


def test_bl_cmd_squad_strict():
    """bl_cmd.py 的多兵种/战术组参数：参数暴露 + 非法 DSL 在**发命令之前**就被拒。

    同 `--dummy-armor` 的理由：参数静默失效曾让实验整批作废（2026-09-24）。
    合法输入这里不测（那要真连游戏）；非法输入不会走到游戏。
    """
    import subprocess
    help_out = subprocess.run([sys.executable, os.path.join(HERE, "bl_cmd.py"), "start", "--help"],
                              capture_output=True, cwd=HERE)
    helptext = (help_out.stdout + help_out.stderr).decode("utf-8", "replace")
    check("--attacker-groups" in helptext and "--defender-groups" in helptext,
          "start 子命令暴露 --attacker-groups/--defender-groups")
    # ⚠️ 断言用 **ASCII 关键词**：子进程在未设 PYTHONIOENCODING 时按 locale(GBK) 写 stdout，
    #    这里若按 utf-8 解码，中文就是乱码 ⇒ 只查中文会**假失败**（本任务第一版正是这么栽的）。
    #    ASCII 字节在 GBK/UTF-8 下解码一致 ⇒ 用它做判据最稳（既有测试的 `or "hed"` 同一思路）。
    for bad, kw in (("a:0", "count"),
                    ("a:1:Infantryy", "formation"),
                    ("a:1:Infantry:jump", "movement")):
        r = subprocess.run([sys.executable, os.path.join(HERE, "bl_cmd.py"), "start",
                            "--attacker", "imperial_legionary", "--defender", "battanian_wildling",
                            "--attacker-groups", bad],
                           capture_output=True, cwd=HERE)
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        check(r.returncode != 0 and (kw in out or "解析失败" in out),
              "非法组串非 0 退出且指明原因（%s）" % kw, out.strip()[:160])


def test_death_compare_split():
    """bl_death_compare 必须把「死于箭」与「死于近战」**分开**——后者不可比。

    立项遗留第 ④ 项：`legionary` 只有 55%、`swordsman`/`heavy_horseman` 67% 死于箭，
    这些行的「扣血箭」只统计了"恰好被箭射死"的子集，与 100% 死于箭的行不是同一个总体；
    混在一起算中位数就是选择偏差。
    """
    import bl_death_compare as bdc
    rows = [
        {"troop": "t", "maxHp": 140, "arrow_hits": 10, "arrow_damaging": 5, "killed_by_missile": True},
        {"troop": "t", "maxHp": 140, "arrow_hits": 12, "arrow_damaging": 7, "killed_by_missile": True},
        {"troop": "t", "maxHp": 140, "arrow_hits": 30, "arrow_damaging": 2, "killed_by_missile": True},
        {"troop": "t", "maxHp": 140, "arrow_hits": 99, "arrow_damaging": 1, "killed_by_missile": False},
        {"troop": "t", "maxHp": 140, "arrow_hits": 99, "arrow_damaging": 1, "killed_by_missile": False},
    ]
    s = bdc.summarize(rows)
    check(s["n"] == 5 and s["n_arrow"] == 3 and s["n_melee"] == 2, "样本按死因拆开", s)
    check(abs(s["arrow_rate_pct"] - 60.0) < 0.01, "死于箭率 = 60%", s["arrow_rate_pct"])
    check(s["arrow_damaging_median"] == 5.0, "扣血箭中位只取「死于箭」的 3 个样本",
          s["arrow_damaging_median"])
    check(s["all_damaging_median"] == 2.0, "旧口径（全样本）保留供对照", s["all_damaging_median"])
    s2 = bdc.summarize(rows[3:])
    check(s2["n_arrow"] == 0 and s2["arrow_damaging_median"] is None,
          "「死于箭」样本不足 ⇒ 拒绝给中位（与 bl_compare 的样本量门槛一致）", s2)
    s3 = bdc.summarize([])
    check(s3["n"] == 0 and s3["arrow_damaging_median"] is None, "空输入不崩", s3)


def test_dummy_analyze_compare():
    """跨档对比（材质/护甲对照）的纯函数 + 接入层。

    为什么必须测：这是「两档差多少、显不显著」的唯一产出，而本项目最贵的教训
    正是「参数静默失效 / 换装没生效却照样出数」（2026-09-24 材质对照 Δ=0 即如此）。
    """
    import bl_dummy_analyze as bda

    # 1) Welch t 手算：a=[10,20,30]（mean 20, var 100）、b=[40,50,60]（mean 50, var 100）
    #    se = sqrt(100/3 + 100/3) = 8.16497 ⇒ t = (20-50)/8.16497 = -3.6742
    t = bda.welch_t([10, 20, 30], [40, 50, 60])
    check(t is not None and abs(t + 3.6742) < 0.001, "welch_t 手算 = -3.674", t)
    check(bda.welch_t([1.0], [2.0]) is None, "样本 <2 ⇒ t = None（不假装显著）")
    check(bda.welch_t([5, 5, 5], [5, 5, 5]) is None, "合并标准误 0 ⇒ t = None")

    # 2) 档名解析：没有 '=' 时用 basename
    spec = bda.parse_compare_specs(["A=x.jsonl", r"C:\d\b"])
    check(spec == [("A", "x.jsonl"), ("b", r"C:\d\b")], "parse_compare_specs：无 = 用 basename", spec)

    # 3) 换装生效判据：四态都要能区分（"未知"绝不等于"生效"）
    check(bda.swap_verdict({"swaps": [], "swapMissing": False}).startswith("未请求"),
          "无 dummy_swap ⇒ 未请求换装")
    check(bda.swap_verdict({"swaps": [{"item": "a"}], "swapMissing": True}).startswith("未知"),
          "缺 actualItem ⇒ 未知（不默认生效）")
    bad = bda.swap_verdict({"swaps": [{"item": "a", "actualItem": "b"}], "swapMissing": False})
    check(bad.startswith("!! 未生效"), "actualItem != item ⇒ 未生效", bad)
    ok = bda.swap_verdict({"swaps": [{"item": "a", "actualItem": "a", "material": "Cloth",
                                      "armorBody": 4, "agents": 10}], "swapMissing": False})
    check(ok.startswith("生效"), "actualItem == item ⇒ 生效", ok)
    # 同一档内 item 一致、但运行时 armorBody 逐场不同 ⇒ 必须报"抖动"（那是换装对照栽过的坑）
    jitter = bda.swap_verdict({"swaps": [{"item": "a", "actualItem": "a", "material": "Cloth",
                                          "armorBody": 25, "agents": 10},
                                         {"item": "a", "actualItem": "a", "material": "Cloth",
                                          "armorBody": 50, "agents": 10}], "swapMissing": False})
    check(jitter.startswith("!! 场间抖动"), "armorBody 逐场不同 ⇒ 报抖动，不当干净对照", jitter)
    same = bda.swap_verdict({"swaps": [{"item": "a", "actualItem": "a", "material": "Plate",
                                        "armorBody": 50, "agents": 10},
                                       {"item": "a", "actualItem": "a", "material": "Plate",
                                        "armorBody": 50, "agents": 10}], "swapMissing": False})
    check("逐场一致" in same, "armorBody/material 逐场一致 ⇒ 报一致", same)

    # 4) 逐击 rows：优先 bodyPartName，旧日志回退 bodyPart
    ev = [{"t": "dummy_hit", "applied": 10.0, "blocked": False, "isMissile": False,
           "bodyPartName": "Head", "bodyPart": "CriticalBodyPartsBegin"}]
    r1 = bda.applied_from_range(ev)
    check(r1[0]["bodypartname"] == "Head", "优先用 bodyPartName", r1[0]["bodypartname"])
    r2 = bda.applied_from_range([{k: v for k, v in ev[0].items() if k != "bodyPartName"}])
    check(r2[0]["bodypartname"] == "CriticalBodyPartsBegin",
          "旧日志（无 bodyPartName）回退 bodyPart", r2[0]["bodypartname"])

    # 5) 分组对比 + Δ% / t 手算：A=[10,20]（mean 15）、B=[20,30]（mean 25）⇒ +66.7%
    def tier(label, vals):
        return (label, {"files": [label], "versions": ["0.8.6"], "armor": None, "swaps": [],
                        "swapMissing": False, "hasBlockedField": True,
                        "rows": [dict(r1[0], applied=float(v)) for v in vals]})

    table = bda.compare_tiers([tier("A", (10, 20)), tier("B", (20, 30))],
                              "bodypartname", "applied", None, True)
    st = table["Head"]["B"]
    check(st["n"] == 2 and abs(st["mean"] - 25.0) < 1e-9, "B 组均值 = 25", st["mean"])
    check(abs(st["median"] - 25.0) < 1e-9, "B 组中位 = 25", st["median"])
    check(abs(st["deltaPct"] - 66.6667) < 0.01, "Δ% = +66.7", st["deltaPct"])
    check(table["Head"]["A"]["deltaPct"] == 0.0, "基准档 Δ% = 0")
    check(bda.compare_tiers([tier("A", (10, 20)), tier("B", (20, 30))],
                            "bodypartname", "applied", None, True)["Head"]["B"]["t"] is None
          or abs(bda.compare_tiers([tier("A", (10, 20)), tier("B", (20, 30))],
                                   "bodypartname", "applied", None, True)["Head"]["B"]["t"]) > 0,
          "两档 t 可算（n=2）")

    # 6) 筛选口径（§七 教训：箭伤不筛 blocked 会得出反向结论）
    rows = [{"applied": 1.0, "blocked": True, "missile": True, "bodypartname": "Head"},
            {"applied": 2.0, "blocked": False, "missile": True, "bodypartname": "Head"},
            {"applied": 3.0, "blocked": False, "missile": False, "bodypartname": "Head"}]
    check(len(bda.select_rows(rows, "bodypartname", True, True)) == 1, "箭伤筛掉被挡下 ⇒ 1 条")
    check(len(bda.select_rows(rows, "bodypartname", True, False)) == 2, "不筛 blocked ⇒ 2 条（对照口径）")
    check(len(bda.select_rows(rows, "bodypartname", None, False)) == 3, "全部口径 ⇒ 3 条")

    # 6b) 按伤害类型分组（第一轮 R 表实测暴露的缺口：R 是**按伤害类型**分档的，
    #     判据"Pierce 变 / Cut 不变"必须能把二者分开看，否则无法区分
    #     "R 没进伤害路径"与"R 进了但没效果"）
    ev_dt = [{"t": "dummy_hit", "applied": 5.0, "damageType": "Pierce", "blocked": False,
              "isMissile": True, "bodyPartName": "Chest"}]
    check(bda.applied_from_range(ev_dt)[0]["damagetype"] == "Pierce",
          "rows 带 damagetype 字段", bda.applied_from_range(ev_dt)[0].get("damagetype"))
    rows_dt = [dict(r1[0], damagetype="Pierce", applied=10.0),
               dict(r1[0], damagetype="Cut", applied=30.0),
               dict(r1[0], damagetype="Pierce", applied=20.0),
               dict(r1[0], damagetype="Cut", applied=40.0)]
    tier_dt = ("A", {"files": ["a"], "versions": ["0.8.7"], "armor": None, "swaps": [],
                     "swapMissing": False, "hasBlockedField": True, "rows": rows_dt})
    t_dt = bda.compare_tiers([tier_dt], "damagetype", "applied", None, True)
    check(t_dt["Pierce"]["A"]["n"] == 2 and abs(t_dt["Pierce"]["A"]["mean"] - 15.0) < 1e-9,
          "按 damagetype 分组：Pierce n=2 均值=15", t_dt["Pierce"]["A"])
    check(abs(t_dt["Cut"]["A"]["mean"] - 35.0) < 1e-9,
          "按 damagetype 分组：Cut 均值=35", t_dt["Cut"]["A"]["mean"])

    # 7) manifest 展开 + 接入层端到端（默认中文控制台 GBK 下也必须 exit 0）
    tmp = tempfile.mkdtemp(prefix="bda_compare_")
    try:
        def write_tier(path, applied):
            with io.open(path, "w", encoding="utf-8", newline="") as fh:
                for side in ("Head", "Chest"):
                    fh.write(json.dumps({"t": "dummy_hit", "applied": applied, "blocked": False,
                                         "isMissile": False, "bodyPartName": side},
                                        ensure_ascii=False) + "\n")

        fa, fb = os.path.join(tmp, "a.jsonl"), os.path.join(tmp, "b.jsonl")
        write_tier(fa, 10.0)
        write_tier(fb, 12.0)
        man = os.path.join(tmp, "runs.json")
        with io.open(man, "w", encoding="utf-8", newline="") as fh:
            json.dump({"configs": [{"label": "A_base", "runs": [{"file": fa}]},
                                   {"label": "B_alt", "runs": [{"file": fb}]}]}, fh)
        mt = bda.manifest_tiers(man)
        check(mt == [("A_base", [fa]), ("B_alt", [fb])], "manifest 展开成两个档", mt)
        check(bda.manifest_tiers(fa) is None, "非 manifest 的 jsonl ⇒ None（不误判）")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "gbk"      # 模拟默认中文控制台
        # 子进程按 gbk 写 ⇒ 必须按 gbk 读；用 utf-8 读会在线程里抛 UnicodeDecodeError
        # 且**被静默吞掉**，只留下 stdout=None（2026-09-24 的 MCP 段就栽在这里）。
        proc = subprocess.run([sys.executable, os.path.join(HERE, "bl_dummy_analyze.py"),
                               "--compare", "base=" + man, "--by", "bodypart"],
                              capture_output=True, text=True, encoding="gbk",
                              env=env, timeout=180)
        check(proc.stdout is not None, "子进程 stdout 可读（None = 编码失配被吞）",
              repr(proc.stdout)[:60])
        proc_stdout = proc.stdout or ""
        check(proc.returncode == 0, "跨档对比 CLI 在 GBK 控制台 exit 0", proc.returncode)
        check("跨档对比" in proc_stdout and "Δ%" in proc_stdout, "输出含对比表",
              proc_stdout[:90].replace("\n", " "))
        check("+20.0%" in proc_stdout, "Δ% 手算 = +20.0%（10 → 12）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    import bl_common
    bl_common.safe_streams()      # 默认中文控制台（GBK）下不因 ⇒/⚠️ 崩（同 bl_metrics/bl_compare 的修复）

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
    # 这里**故意不设 PYTHONIOENCODING**：bl_mcp.py 自己把 stdio 钉成 UTF-8
    # （`_force_utf8_stdio()`，MCP over stdio 的协议要求），而本段按 utf-8 读。
    # 不设环境变量，正好让"哪天 bl_mcp 又没钉编码"这种回归在**继承 GBK locale**
    # 的条件下被下面的乱码断言抓住（2026-09-24 宿主里工具描述全是问号就是这个 bug）。
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
    if out is None:
        # 注意：stdout=None 不是"空输出"，而是读取线程里的异常被静默吞掉了（见上面的编码说明）。
        # 这里显式报出来，否则症状会跑到下面变成 AttributeError，掩盖根因。
        check(False, "MCP 子进程 stdout 可读（编码须与 PYTHONIOENCODING 对齐）",
              "stdout=None; stderr=%r" % err[:200])
        out = ""
    # 编码回归：宿主按 UTF-8 解码响应，所以里面**不许**出现替换字符，中文必须可读。
    # （2026-09-24 实测：未钉 stdio 时 tools/list 的 7680 字节里有 1489 个 U+FFFD ——
    #   Reasonix 的 MCP 页面上就是"工具描述全是问号"。判据不绑具体文案，只看有无中文。）
    check("\ufffd" not in out, "MCP 响应无乱码（U+FFFD = 子进程按 locale 编码写了中文）")
    check(any("\u4e00" <= c <= "\u9fff" for c in out),
          "响应含可读中文（描述未被编码搞坏）")
    responses = [json.loads(l) for l in out.splitlines() if l.strip()]
    by_id = dict((r.get("id"), r) for r in responses)

    check(len(responses) >= 6, "收到 6 个响应", len(responses))
    init = by_id.get(1, {}).get("result", {})
    check(init.get("protocolVersion") == "2024-11-05", "initialize 返回协议版本", init.get("protocolVersion"))
    tools = by_id.get(2, {}).get("result", {}).get("tools", [])
    names = sorted(t["name"] for t in tools)
    check(len(tools) == 16, "tools/list 返回 16 个工具", names)
    check("bl_lookup_troop" in names, "bl_lookup_troop 已注册", names)
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
    test_parse_squad_groups()
    test_bl_batch_plan_args()
    test_squad_plan_args()
    test_bl_cmd_dummy_armor_strict()
    test_bl_cmd_squad_strict()
    test_death_compare_split()

    # ── ⑫ 跨档对比（材质/护甲对照：两档差多少 + 生效判据）──────────────
    print()
    print("=" * 90)
    print("⑫ bl_dummy_analyze --compare：跨档对比（材质/护甲对照）")
    print("=" * 90)
    test_dummy_analyze_compare()

    if FAIL:
        print("结果: 失败 %d 项 -> %s" % (len(FAIL), FAIL))
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
