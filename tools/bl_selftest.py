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
                    # v0.8.14：回显 spectate（God view）—— 断言"参数被透传到端口"
                    result = {"accepted": True, "state": "loading",
                              "spectate": bool((req.get("parameters") or {}).get("spectate"))}
                elif method == "abort":
                    result = {"aborted": True, "state": "running"}
                elif method == "control_agent":
                    # v0.8.25：回显 mode + 一份假接管回读（断言"目标与 before/after 被带回"）
                    op = req.get("parameters") or {}
                    result = {"ok": True, "mode": op.get("mode", "take"), "how": "agentIndex=7",
                              "target": {"index": 7, "troop": "imperial_legionary",
                                         "formation": "Infantry", "controller": "Player",
                                         "isHero": False, "isActive": True},
                              "mainAgentBefore": {"index": 3, "controller": "Player"},
                              "mainAgentAfter": {"index": 7, "controller": "Player"},
                              "oldHandedToAI": True, "screenReset": True, "originalIndex": 3,
                              "note": "fake"}
                elif method == "order":
                    # v0.8.23/0.8.28：回显改令参数 + 一条假回读（断言"参数确实被透传到端口"与"回读被带回"）
                    op = req.get("parameters") or {}
                    detach = op.get("detachAI", True)
                    ap = {"formation": "Infantry", "index": 0, "count": 7,
                          "orderBefore": "Charge", "orderAfter": "Stop",
                          "aiDetachRequested": detach}
                    if op.get("arrangement"):
                        ap["arrangementBefore"] = "Line"
                        ap["arrangementAfter"] = op["arrangement"].capitalize()
                    if op.get("firing"):
                        ap["firingBefore"] = "FireAtWill"
                        ap["firingAfter"] = op["firing"].capitalize()
                    pos = None
                    pos_bad = False
                    if op.get("position"):
                        # v0.8.30：指定点移动 —— 回读 `orderAfter=Move` + 引擎算出的落点。
                        # 假端也照 C# 的口径判一下语法（2~3 个分量、都是数字）；真机的唯一真相
                        # 仍在 `OrderSpec.TryParsePosition`（离线单测锁它）。
                        try:
                            nums = [float(v) for v in str(op["position"]).split(",")]
                            if len(nums) not in (2, 3):
                                raise ValueError("分量数不是 2 或 3")
                            while len(nums) < 3:
                                nums.append(0.0)
                            pos = {"x": nums[0], "y": nums[1], "z": nums[2]}
                        except ValueError:
                            pos_bad = True
                    tgt = None
                    tgt_bad = False
                    if op.get("target"):
                        # v0.8.31：指定目标 —— 回读 `orderAfter=ChargeToTarget` + 目标编队名 + 双方重心距离。
                        # 假端按 C# 的口径认"编队名（大小写不敏感）或下标 0~4"，非法回 bad_target
                        # （真机的唯一真相仍在 `OrderSpec.TryFormationIndex`，离线单测锁它）。
                        _F = ["Infantry", "Ranged", "Cavalry", "HorseArcher", "Skirmisher"]
                        t = str(op["target"]).strip()
                        if t.isdigit() and 0 <= int(t) < len(_F):
                            tgt = _F[int(t)]
                        elif t.lower() in [f.lower() for f in _F]:
                            tgt = [f for f in _F if f.lower() == t.lower()][0]
                        else:
                            tgt_bad = True
                    if pos_bad:
                        result = {"ok": False, "code": "bad_position",
                                  "error": "position 需要写成 \"x,y\" 或 \"x,y,z\"（fake 端判的）"}
                    elif tgt_bad:
                        result = {"ok": False, "code": "bad_target",
                                  "error": "target 只接受敌方编队名或下标 0~4（fake 端判的）"}
                    else:
                        if pos is not None:
                            ap["orderAfter"] = "Move"
                            ap["moveTarget"] = "(123.0,456.0)"
                            ap["formationCenter"] = "(1.0,2.0)"
                        if tgt is not None:
                            ap["orderAfter"] = "ChargeToTarget"
                            ap["targetAfter"] = tgt
                            ap["formationCenter"] = "(1.0,2.0)"
                            ap["targetDistance"] = "123.4 m"
                        # v0.8.31：`formation=Skirmisher` 当"空编队"的代表（真机口径：count=0 ⇒
                        # 令写进去了但没人执行）⇒ 断言这条信号能透传出去。
                        empty = (op.get("formation") == "Skirmisher")
                        if empty:
                            ap["count"] = 0
                            ap["emptyFormation"] = True
                        result = {"ok": True, "side": op.get("side", "player"),
                                  "movement": op.get("movement"), "detachAI": detach,
                                  "position": pos, "target": tgt,
                                  "targetSide": "defender" if tgt is not None else None,
                                  "arrangement": op.get("arrangement"), "firing": op.get("firing"),
                                  "appliedCount": 1, "totalUnits": 0 if empty else 7,
                                  "emptyFormations": 1 if empty else 0, "applied": [ap],
                                  "note": "fake"}
                elif method == "list_ui":
                    # v0.8.14：入口清单 + 官方场景全表（这里只放三行代表三种模式）
                    # 状态名用**真机实测值** `InitialState`（v0.8.22 更正：以前这里写的是虚构的 "MainMenu"，
                    # 与真机不符 ⇒ 假端比真机"更好说话"，会掩盖 Python 侧的状态名假设）。
                    result = {"moduleLoaded": True, "activeState": "InitialState",
                              "options": [{"id": "CustomBattle", "name": "自定义战斗", "orderIndex": 5000,
                                           "hidden": False, "disabled": False, "disabledReason": ""}],
                              "scenes": ["battle_terrain_a"],
                              "sceneTable": {
                                  "count": 3, "filter": "all",
                                  "fromCache": False, "sourceFiles": 2, "existsChecked": True,
                                  "modes": {"battle": 1, "siege": 1, "village": 0, "lordsHall": 0,
                                            "naval": 1, "navalRaid": 0},
                                  "rows": [{"id": "battle_terrain_a", "name": "Vladiv Forest (Plain)",
                                            "mode": "battle", "terrain": "Plain", "exists": True,
                                            "source": "custom_battle_scenes.xml"},
                                           {"id": "battania_castle_b", "name": "Llanoc Hen Castle",
                                            "mode": "siege", "terrain": "Plain", "exists": True,
                                            "source": "custom_battle_scenes.xml"},
                                           {"id": "battle_terrain_opensea_northern",
                                            "name": "Northern Open Sea", "mode": "naval",
                                            "terrain": "OpenSea", "exists": True,
                                            "source": "naval_custom_battle_scenes.xml"}],
                                  "returned": 3, "missingInReturned": 0}}
                elif method == "open_ui":
                    # 假游戏端用真 JSON 解析（认嵌套）⇒ 抓不到"参数名与信封键同名"那类碰撞
                    # （例如 v0.8.12 真机踩到的 id vs 信封 id）。真机判据见 PROGRESS §二十三 §3。
                    result = {"requested": True,
                              "uiId": (req.get("parameters") or {}).get("uiId") or "CustomBattle",
                              "stateBefore": "MainMenu", "note": "fire-and-forget"}
                elif method == "close_ui":
                    result = {"closeRequested": True,
                              "state": (req.get("parameters") or {}).get("state") or "CustomBattleState",
                              "note": "PopState"}
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
    """bl_compare 的**接入层**必须跑得通，且在不设编码环境变量时也不能崩（子进程自己钉 UTF-8）。

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
        env.pop("PYTHONIOENCODING", None)        # 不设编码环境变量：子进程自己钉 UTF-8
        # 子进程由 bl_common.safe_streams() 固定按 UTF-8 写 ⇒ 这里必须按 utf-8 解码
        # （旧口径是"设 gbk 环境变量 + 按 gbk 读"；统一 UTF-8 后方向正好反过来）。
        r = subprocess.run([sys.executable, os.path.join(HERE, "bl_compare.py"), "--manifest", path],
                           capture_output=True, env=env, cwd=HERE)
        err = r.stderr.decode("utf-8", "replace")[-200:]
        check(r.returncode == 0, "bl_compare --manifest exit 0", err)
        out = r.stdout.decode("utf-8", "replace")
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
    g = bl_common.parse_squad_groups("imperial_legionary:10:Infantry:stop")
    check(g == [{"troop": "imperial_legionary", "count": 10, "formation": "Infantry",
                 "movement": "stop"}], "四字段组", g)
    g2 = bl_common.parse_squad_groups("khuzait_khans_guard:5")
    check(g2 == [{"troop": "khuzait_khans_guard", "count": 5, "formation": None,
                  "movement": None}], "两字段：formation/movement 缺省为 None", g2)
    g3 = bl_common.parse_squad_groups("a:1|b:2:HorseArcher")
    check(len(g3) == 2 and g3[1]["formation"] == "HorseArcher" and g3[0]["formation"] is None,
          "多组用 | 分隔、各组独立", g3)
    g4 = bl_common.parse_squad_groups("a:1:infantry:STOP")
    check(g4[0]["formation"] == "Infantry" and g4[0]["movement"] == "stop",
          "formation 大小写不敏感→规范名；movement 归小写", g4)
    check(bl_common.parse_squad_groups(None) == [] and bl_common.parse_squad_groups("   ") == [],
          "None / 纯空白 ⇒ 空列表")
    # 移除 hold（2026-09-25 用户按编程规则批准）：写 hold 必须在解析期报错，且提示改用 stop。
    try:
        bl_common.parse_squad_groups("a:1:Infantry:hold")
        check(False, "hold 必须被拒（已移除）")
    except ValueError as e:
        check("已移除" in str(e), "hold 报错信息必须含补位提示（已移除）", e)
    for bad, why in (("a", "字段数 1"),
                     ("a:1:Infantry:stop:extra", "字段数 5"),
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
        {"troop": "imperial_legionary", "count": 10, "formation": "Infantry", "movement": "stop"},
        {"troop": "khuzait_khans_guard", "count": 5},
    ]), scene, orders, ps, cap)
    check("--attacker-groups" in A, "组列表 ⇒ CLI --attacker-groups", A[-2:])
    dsl = A[A.index("--attacker-groups") + 1]
    check(dsl == "imperial_legionary:10:Infantry:stop|khuzait_khans_guard:5",
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
                [{"troop": "a", "count": 1, "movement": "stop"}],
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
    # 断言用 ASCII 关键词：tools/ 的输出统一走 UTF-8（bl_common.safe_streams），直查中文
    # 关键词也稳；保留 ASCII 关键词只是让断言在任何解码口径下都不误报（`or "hed"` 同一思路）。
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

    # 7) manifest 展开 + 接入层端到端（不设编码环境变量时也必须 exit 0）
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
        env.pop("PYTHONIOENCODING", None)    # 不设编码环境变量：子进程自己钉 UTF-8
        # 子进程按 UTF-8 写 ⇒ 必须按 utf-8 读；读错编码会在线程里抛 UnicodeDecodeError
        # 且**被静默吞掉**，只留下 stdout=None（2026-09-24 的 MCP 段就栽在这里）。
        proc = subprocess.run([sys.executable, os.path.join(HERE, "bl_dummy_analyze.py"),
                               "--compare", "base=" + man, "--by", "bodypart"],
                              capture_output=True, text=True, encoding="utf-8",
                              env=env, timeout=180)
        check(proc.stdout is not None, "子进程 stdout 可读（None = 编码失配被吞）",
              repr(proc.stdout)[:60])
        proc_stdout = proc.stdout or ""
        check(proc.returncode == 0, "跨档对比 CLI exit 0", proc.returncode)
        check("跨档对比" in proc_stdout and "Δ%" in proc_stdout, "输出含对比表",
              proc_stdout[:90].replace("\n", " "))
        check("+20.0%" in proc_stdout, "Δ% 手算 = +20.0%（10 → 12）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_menu_state_gate():
    """⑬ 启动门 `bl_mcp._menu_state`：主菜单层面必须**同时**认 "" 与 "InitialState"。

    存在理由（v0.8.22，真机依据 2026-09-25 23:00）：C# 的 `ScenarioRunner.ActiveStateName()` 改成
    "静态 GameStateManager 优先"后，主菜单会**开始返回 `InitialState`**（真机实测值）。
    若 Python 侧仍旧把"activeState 非空"一律当成"不在主菜单"，就会把**真主菜单判成未就绪**，
    整个 enter-battle 流程反被打死 ⇒ 两处必须一起改，所以这里必须有对照断言（不许只靠真机临场发现）。
    """
    import bl_mcp

    def ui(**kw):
        base = {"moduleLoaded": True, "activeState": "", "topScreen": "GauntletInitialScreen",
                "options": [{"id": "CustomBattle", "disabled": False}]}
        base.update(kw)
        return base

    ready, why = bl_mcp._menu_state(ui())
    check(ready, "activeState=''（取不到状态名）+ 真菜单屏 ⇒ 判主菜单就绪", why)

    ready, why = bl_mcp._menu_state(ui(activeState="InitialState"))
    check(ready, "activeState='InitialState'（真机实测的主菜单名）⇒ 仍判主菜单就绪", why)

    for other in ("VideoPlaybackState", "CustomBattleState", "MapState", "CampaignState"):
        ready, why = bl_mcp._menu_state(ui(activeState=other))
        check(not ready and other in (why or ""),
              "activeState=%s ⇒ 判未就绪，且把状态名写进原因" % other, why)

    ready, why = bl_mcp._menu_state(ui(topScreen=""))
    check(not ready and "topScreen" in (why or ""), "顶层还没有屏幕 ⇒ 判未就绪（启动初期硬门）", why)

    ready, why = bl_mcp._menu_state(ui(topScreen="GameLoadingScreen"))
    check(not ready and "Loading" in (why or ""), "topScreen 含 Loading ⇒ 判未就绪（GABS 的二次校验）", why)

    ready, why = bl_mcp._menu_state(ui(moduleLoaded=False))
    check(not ready, "moduleLoaded=false ⇒ 判未就绪", why)

    ready, why = bl_mcp._menu_state(ui(options=[]))
    check(not ready and "CustomBattle" in (why or ""), "没有 CustomBattle 入口 ⇒ 判未就绪", why)

    ready, why = bl_mcp._menu_state(ui(options=[{"id": "CustomBattle", "disabled": True,
                                                 "disabledReason": "加载中"}]))
    check(not ready and "禁用" in (why or ""), "CustomBattle 入口被禁用 ⇒ 判未就绪", why)

    # ── 跨语言同集合：C# 的唯一实现与 Python 镜像必须写同样的名字 ────────────────
    # 「改一处忘另一处」在这件事上已经有真机代价（open_ui 被误拒），所以这条断言是必须的：
    root = os.path.dirname(HERE)
    check(bl_mcp._MAIN_MENU_ACTIVE_STATES == ("", "InitialState"),
          "Python 侧集合 = ('', 'InitialState')", repr(bl_mcp._MAIN_MENU_ACTIVE_STATES))

    cs = os.path.join(root, "src", "MainMenuStates.cs")
    check(os.path.isfile(cs), "C# 的 MainMenuStates.cs 存在（跨语言对照物）", cs)
    if os.path.isfile(cs):
        src = io.open(cs, encoding="utf-8").read()
        check('Name = "InitialState"' in src, "C# 侧常量写作 InitialState", "")
        check("IsNullOrEmpty" in src, "C# 侧也接受空串（与 Python 的 '' 同集合）", "")

    uientry = os.path.join(root, "src", "UiEntry.cs")
    if os.path.isfile(uientry):
        u = io.open(uientry, encoding="utf-8").read()
        check("MainMenuStates.IsMenuLevel(stateBefore)" in u,
              "open_ui 闸门走 MainMenuStates（不再自己写\"空串 = 主菜单\"）", "")


def main():
    import bl_common
    bl_common.safe_streams()      # 输出统一 UTF-8（见其 docstring：消费端是 UTF-8 管道）

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
    # 不设环境变量，正好让"哪天 bl_mcp 又没钉编码"这种回归被下面的乱码断言抓住
    # （2026-09-24 宿主里工具描述全是问号就是这个 bug）。
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
    check(len(tools) == 32, "tools/list 返回 32 个工具", names)
    check("bl_lookup_troop" in names, "bl_lookup_troop 已注册", names)
    check("bl_order" in names, "bl_order（战斗中途改令）已注册", names)
    check("bl_control_agent" in names, "bl_control_agent（接管士兵）已注册", names)
    check("bl_launch_game" in names and "bl_desktop_click" in names,
          "桌面/游戏 GUI 工具已注册", names)
    check("bl_apply_config" in names and "bl_analyze" in names, "关键工具存在", names)
    check("bl_list_ui" in names and "bl_open_ui" in names and "bl_close_ui" in names,
          "游戏内 UI 入口工具已注册（v0.8.12）", names)
    check("bl_rts_config" in names and "bl_apply_rts_config" in names,
          "RTSCamera 配置工具已注册（v0.8.15，B 方案）", names)
    check("bl_ghost_camera" in names, "幽灵相机工具已注册（v0.8.16，C 方案）", names)
    check("bl_camera_speed" in names, "相机速度工具已注册（v0.8.17）", names)
    _cs = next((t for t in tools if t.get("name") == "bl_camera_speed"), None)
    check(_cs is not None
          and sorted((_cs.get("inputSchema") or {}).get("properties", {}).get("mode", {}).get("enum") or [])
          == ["base", "boost", "probe", "rts", "shift", "status"],
          "bl_camera_speed 的 mode 枚举齐全（status/shift/base/rts/boost/probe）",
          (_cs or {}).get("inputSchema"))
    check("bl_skip_video" in names and "bl_cheat_mode" in names,
          "启动/流程控制工具已注册（v0.8.20：skip_video / cheat_mode）", names)
    _cm = next((t for t in tools if t.get("name") == "bl_cheat_mode"), None)
    check(_cm is not None
          and sorted(((_cm.get("inputSchema") or {}).get("properties", {}).get("mode", {}) or {}).get("enum") or [])
          == ["off", "on", "status", "toggle"],
          "bl_cheat_mode 的 mode 枚举齐全（status/on/off/toggle）", (_cm or {}).get("inputSchema"))

    # ── v0.8.15：RTSCamera 配置读写（离线：全在临时文件上跑，绝不碰真配置）──
    import bl_rts
    rts_dir = tempfile.mkdtemp(prefix="bda_rts_")
    rts_cfg = os.path.join(rts_dir, "RTSCameraConfig.xml")
    with io.open(rts_cfg, "w", encoding="utf-8-sig") as fh:
        fh.write('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<RTSCameraConfig>\n'
                 '  <DefaultToFreeCamera>DeploymentStage</DefaultToFreeCamera>\n'
                 '  <ElevatedHeight>10</ElevatedHeight>\n'
                 '  <ElevatedHeightInSiege>0</ElevatedHeightInSiege>\n'
                 '</RTSCameraConfig>\n')
    check(bl_rts.read(["ElevatedHeightInSiege"], path=rts_cfg).get("ElevatedHeightInSiege") == "0",
          "rts: 读配置（指定键）", bl_rts.read(path=rts_cfg))
    dry = bl_rts.apply([{"key": "ElevatedHeightInSiege", "value": "10"}], dry_run=True, path=rts_cfg)
    check(dry.get("changed", {}).get("ElevatedHeightInSiege", {}).get("new") == "10"
          and bl_rts.read(["ElevatedHeightInSiege"], path=rts_cfg)["ElevatedHeightInSiege"] == "0",
          "rts: dry-run 只预览不落盘", dry)
    out = bl_rts.apply_preset("siege-god", path=rts_cfg)
    check(out.get("ok") is True
          and (out.get("verified") or {}).get("ElevatedHeightInSiege") == "10"
          and os.path.isfile(out.get("backup") or ""),
          "rts: 套用预设 + 自动备份 + 回读核对", out)
    bad = bl_rts.apply([{"key": "NoSuchKey", "value": "1"}], path=rts_cfg)
    check(bad.get("ok") is False and "NoSuchKey" in (bad.get("error") or ""),
          "rts: 未知键默认拒绝写入", bad)
    thrown = False
    try:
        bl_rts.apply([{"key": "ElevatedHeight", "value": "1<2"}], path=rts_cfg)
    except ValueError:
        thrown = True
    check(thrown, "rts: 含 XML 特殊字符的值被拒绝")
    preset_bad = False
    try:
        bl_rts.apply_preset("no-such-preset", path=rts_cfg)
    except ValueError:
        preset_bad = True
    check(preset_bad, "rts: 未知预设名被拒绝")
    check(bl_rts.read(["DefaultToFreeCamera"], path=rts_cfg)["DefaultToFreeCamera"] == "DeploymentStage",
          "rts: 未被指定的键保持原值")
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

    # ── v0.8.23：战斗中途改令 `bl_order`（断言参数透传 + 回读带回 + 本地缺参就拒）──
    ro1 = bl_mcp.call_tool("bl_order", {"movement": "stop", "side": "defender", "formation": "Infantry"})
    ap1 = (ro1.get("applied") or [{}])[0]
    check(ro1.get("ok") is True and ro1.get("movement") == "stop" and ro1.get("side") == "defender"
          and ap1.get("orderBefore") == "Charge" and ap1.get("orderAfter") == "Stop",
          "bl_order 往返成功，且带回 orderBefore/orderAfter（参数透传到端口）", ro1)

    ro2 = bl_mcp.call_tool("bl_order", {"movement": "stop", "detachAI": False})
    check(ro2.get("ok") is True and ro2.get("detachAI") is False
          and (ro2.get("applied") or [{}])[0].get("aiDetachRequested") is False,
          "bl_order 的 detachAI=false 能透传到端口（对照实验用）", ro2)

    ro3 = bl_mcp.call_tool("bl_order", {})
    check(ro3.get("ok") is False and "movement" in (ro3.get("error") or ""),
          "bl_order 三者都不给时**本地**就拒（不发出请求）", ro3)

    # ── v0.8.28：阵列 / 射击纪律（透传 + before/after 带回）──
    ro4 = bl_mcp.call_tool("bl_order", {"movement": "stop", "arrangement": "shieldwall",
                                        "firing": "holdFire"})
    ap4 = (ro4.get("applied") or [{}])[0]
    check(ro4.get("ok") is True and ro4.get("arrangement") == "shieldwall"
          and ro4.get("firing") == "holdFire"
          and ap4.get("arrangementAfter") == "Shieldwall" and ap4.get("firingAfter") == "Holdfire"
          and ap4.get("arrangementBefore") == "Line" and ap4.get("firingBefore") == "FireAtWill",
          "bl_order 的 arrangement/firing 透传，且带回 arrangement/firing 的 before→after", ro4)

    ro5 = bl_mcp.call_tool("bl_order", {"arrangement": "shieldwall"})
    check(ro5.get("ok") is True and ro5.get("movement") is None,
          "只给 arrangement 也能发令（movement 不再是必填）", ro5)

    # ── v0.8.30：指定点移动 `position`（透传 + 落点回读 + 与 movement 互斥）──
    ro6 = bl_mcp.call_tool("bl_order", {"position": "100,200", "side": "defender",
                                        "formation": "Infantry"})
    ap6 = (ro6.get("applied") or [{}])[0]
    check(ro6.get("ok") is True and (ro6.get("position") or {}).get("x") == 100
          and (ro6.get("position") or {}).get("z") == 0
          and ap6.get("orderAfter") == "Move" and ap6.get("moveTarget") == "(123.0,456.0)"
          and ap6.get("formationCenter") == "(1.0,2.0)",
          "bl_order 的 position 透传到端口，且带回 orderAfter=Move + moveTarget + formationCenter",
          ro6)

    ro7 = bl_mcp.call_tool("bl_order", {"movement": "stop", "position": "1,2"})
    check(ro7.get("ok") is False and "互斥" in (ro7.get("error") or ""),
          "movement 与 position 同时给 ⇒ **本地**就拒（互斥，一个编队只能有一个 movement order）", ro7)

    ro8 = bl_mcp.call_tool("bl_order", {"position": "a,b"})
    check(ro8.get("ok") is False and ro8.get("code") == "bad_position",
          "position 语法非法时带回 bad_position（真机由 C# OrderSpec 判，Python 侧只透传）", ro8)

    # ── v0.8.31：指定目标 `target`（透传 + 目标回读 + 从"未实现"里移出来）──
    ro9 = bl_mcp.call_tool("bl_order", {"target": "Infantry", "side": "defender"})
    ap9 = (ro9.get("applied") or [{}])[0]
    check(ro9.get("ok") is True and ro9.get("target") == "Infantry"
          and ro9.get("targetSide") == "defender"
          and ap9.get("orderAfter") == "ChargeToTarget" and ap9.get("targetAfter") == "Infantry"
          and ap9.get("targetDistance") == "123.4 m" and ap9.get("formationCenter") == "(1.0,2.0)",
          "bl_order 的 target 透传到端口，且带回 orderAfter=ChargeToTarget + targetAfter "
          "+ targetDistance + formationCenter", ro9)

    ro10 = bl_mcp.call_tool("bl_order", {"movement": "stop", "target": "Infantry"})
    check(ro10.get("ok") is False and "互斥" in (ro10.get("error") or ""),
          "movement 与 target 同时给 ⇒ **本地**就拒（互斥）", ro10)

    ro11 = bl_mcp.call_tool("bl_order", {"position": "1,2", "target": "Infantry"})
    check(ro11.get("ok") is False and "互斥" in (ro11.get("error") or ""),
          "position 与 target 同时给 ⇒ **本地**就拒（互斥；三者互斥要一次判全，别两两漏配）", ro11)

    ro12 = bl_mcp.call_tool("bl_order", {"target": "bogus"})
    check(ro12.get("ok") is False and ro12.get("code") == "bad_target",
          "target 非法时带回 bad_target（真机由 C# OrderSpec 判，Python 侧只透传）", ro12)

    ro13 = bl_mcp.call_tool("bl_order", {"movement": "stop", "formation": "Skirmisher"})
    ap13 = (ro13.get("applied") or [{}])[0]
    check(ro13.get("ok") is True and ro13.get("emptyFormations") == 1
          and ap13.get("emptyFormation") is True and ap13.get("count") == 0,
          "空编队信号（emptyFormations / emptyFormation）能透传：ok=true 但没人执行", ro13)

    # ── v0.8.25：接管士兵 `bl_control_agent`（目标 / before-after 回读 / 降级字段要被带回）──
    rc1 = bl_mcp.call_tool("bl_control_agent", {"agentIndex": 7})
    check(rc1.get("ok") is True and (rc1.get("target") or {}).get("index") == 7
          and (rc1.get("mainAgentAfter") or {}).get("index") == 7
          and rc1.get("oldHandedToAI") is True and rc1.get("screenReset") is True,
          "bl_control_agent take 往返成功（带回 target / mainAgentAfter / oldHandedToAI / screenReset）", rc1)

    rc2 = bl_mcp.call_tool("bl_control_agent", {"mode": "release"})
    check(rc2.get("ok") is True and rc2.get("mode") == "release" and rc2.get("how") == "agentIndex=7"
          and rc2.get("originalIndex") == 3,
          "bl_control_agent release 往返成功（参数透传到端口，v0.8.30 起 originalIndex 是结构化字段）",
          rc2)

    rc3 = bl_mcp.call_tool("bl_control_agent", {"mode": "nonsense"})
    check(rc3.get("ok") is False and "mode" in (rc3.get("error") or ""),
          "bl_control_agent mode 非法时**本地**就拒（不发出请求）", rc3)

    # ── v0.8.12 / v0.8.14：UI 入口三个方法的往返（含"带参数"与"不带参数"两种形态）──
    rui1, eui1 = bl_mcp.send_command("list_ui", {}, timeout=6)
    ui1 = (rui1 or {}).get("result") or {}
    check(eui1 is None and ui1.get("activeState") == "InitialState" and bool(ui1.get("options")),
          "list_ui 往返成功且带回入口清单", eui1 or ui1)
    st1 = ui1.get("sceneTable") or {}
    check(len(st1.get("rows") or []) == 3 and (st1.get("modes") or {}).get("naval") == 1,
          "list_ui 带回官方场景全表（含模式计数与海战行）", ui1.get("sceneTable"))
    check(st1.get("existsChecked") is True and st1.get("sourceFiles") == 2,
          "场景表标注了 存在性是否已检查 / 实际解析到几个表文件", ui1.get("sceneTable"))

    rui2, eui2 = bl_mcp.send_command("open_ui", {}, timeout=6)
    ui2 = (rui2 or {}).get("result") or {}
    check(eui2 is None and ui2.get("requested") is True and ui2.get("uiId") == "CustomBattle",
          "open_ui 不带 uiId 时默认指向官方自定义战斗界面", eui2 or ui2)

    rui3, eui3 = bl_mcp.send_command("open_ui", {"uiId": "CampaignResumeGame"}, timeout=6)
    ui3 = (rui3 or {}).get("result") or {}
    check(eui3 is None and ui3.get("uiId") == "CampaignResumeGame",
          "open_ui 的 uiId 会被透传（任意官方入口）", eui3 or ui3)

    rui4, eui4 = bl_mcp.send_command("close_ui", {}, timeout=6)
    ui4 = (rui4 or {}).get("result") or {}
    check(eui4 is None and ui4.get("closeRequested") is True and ui4.get("state") == "CustomBattleState",
          "close_ui 往返成功且默认官方状态", eui4 or ui4)

    rui5, eui5 = bl_mcp.send_command("close_ui", {"state": "CustomBattleState"}, timeout=6)
    ui5 = (rui5 or {}).get("result") or {}
    check(eui5 is None and ui5.get("state") == "CustomBattleState",
          "close_ui 可显式指定官方状态（进得去就要出得来）", eui5 or ui5)

    # v0.8.14：上帝视角参数从 MCP 层透传到端口层（模拟层只能测透传，真机行为另验）
    r6, e6 = bl_mcp.send_command("start_battle",
                                {"attackerTroop": "a", "defenderTroop": "b", "spectate": True}, timeout=6)
    check(e6 is None and ((r6 or {}).get("result") or {}).get("spectate") is True,
          "start_battle 的 spectate（上帝视角）会被透传", e6 or r6)

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

    # ── ⑬ 启动门 `_menu_state`（主菜单层面："" 与 "InitialState" 都算）──────────────
    print()
    print("=" * 90)
    print("⑬ 启动门 bl_mcp._menu_state（C# 静态优先后主菜单名 = InitialState）")
    print("=" * 90)
    test_menu_state_gate()

    if FAIL:
        print("结果: 失败 %d 项 -> %s" % (len(FAIL), FAIL))
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
