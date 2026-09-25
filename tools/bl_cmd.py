#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BlBridge 控制通道命令行（不依赖 MCP，可直接驱动游戏）。

  python bl_cmd.py status
  python bl_cmd.py start --attacker imperial_legionary --defender battanian_fian_champion --a 20 --d 20
  python bl_cmd.py wait --state ended --timeout 180
  python bl_cmd.py abort
  python bl_cmd.py ping

前置：游戏在跑、BlBridge 模块已启用、并且**停在「自定义战斗」界面**（start 需要 CustomBattleState）。
"""
import argparse
import io
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bl_common  # noqa: E402
import bl_mcp  # noqa: E402
import bl_sage  # noqa: E402


def _print(resp, err):
    if err:
        print("错误: %s" % err)
        return 2
    print(json.dumps(resp, ensure_ascii=False, indent=1))
    return 0 if resp.get("ok") else 1


def main(argv):
    bl_common.safe_streams()
    ap = argparse.ArgumentParser(description="BlBridge 控制通道 CLI")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("ping", help="连通性/版本")
    sub.add_parser("status", help="推演状态机")
    sub.add_parser("abort", help="中止当前推演")

    p = sub.add_parser("start", help="开一场 AI 对 AI 战斗")
    p.add_argument("--attacker", required=True)
    p.add_argument("--defender", required=True)
    p.add_argument("--a", type=int, default=20, help="攻方人数")
    p.add_argument("--d", type=int, default=20, help="守方人数")
    p.add_argument("--scene", default="battle_terrain_a")
    p.add_argument("--cap", type=int, default=600, help="单场时长上限（游戏内秒）")
    p.add_argument("--orders", choices=("charge", "default"), default="charge",
                   help="charge=双方都对称冲锋（默认，消除攻守战术偏差）；default=引擎默认战术")
    p.add_argument("--player-side", choices=("attacker", "defender"), default="attacker",
                   help="谁被标记为玩家侧（仅用于排查该标记是否带来系统性偏差）")
    p.add_argument("--allow-any-state", action="store_true",
                   help="跳过「必须停在自定义战斗界面」检查（用于从主菜单直接开战）")
    p.add_argument("--dummy-side", choices=("none", "attacker", "defender"), default="none",
                   help="不朽靶场：把该方设为永不倒下的靶子（阶段 2①）")
    p.add_argument("--freeze-dummies", action="store_true",
                   help="靶场模式下属实冻结靶子的 AI（不还手；默认关闭，冻结会改变 AI 行为）")
    p.add_argument("--unlimited-ammo", action="store_true",
                   help="给射手补满弹药（长测不中断；靶子的弹药不补，它是被测对象）")
    p.add_argument("--dummy-armor", default=None,
                   help="靶子护甲数值覆盖，如 head=45,torso=35,legs=20,arms=25"
                        "（只作用于靶子；未写的部位不动；每帧重申，零 Harmony；"
                        "未知部位名/非数字会直接报错，不静默跳过）")
    p.add_argument("--dummy-body-item", dest="dummy_body_item", default=None,
                   help="把靶子的身甲换成该物品 id（材质对照实验用；空=不换。"
                        "材质抗性只来自物品，数值仍由 --dummy-armor 对齐）")
    p.add_argument("--attacker-groups", dest="attacker_groups", default=None,
                   help="攻方多兵种/战术组：troop:count[:formation[:movement]]，多组用 | 分隔，"
                        '如 "imperial_legionary:10:Infantry:stop|khuzait_khans_guard:5:HorseArcher:charge"'
                        "（给了它则组内兵力取代 --attacker/--a，但 --attacker/--defender 仍须照常提供"
                        "—— 它们此时只用于回显；非法直接报错，不静默跳过）")
    p.add_argument("--defender-groups", dest="defender_groups", default=None,
                   help="守方多兵种/战术组，语法同 --attacker-groups（组内兵力取代 --defender/--d，"
                        "但 --attacker/--defender 仍须照常提供 —— 它们此时只用于回显）")
    p.add_argument("--rounds", type=int, default=None,
                   help="多轮连续实验：同一 mission 内跑 N 轮（每轮一个日志文件；默认 1 = 关闭）")
    p.add_argument("--round-end-alive", dest="round_end_alive", type=int, default=None,
                   help="某方存活 ≤ 此值即判定本轮结束（默认 1）")
    p.add_argument("--round-swap", action="store_true",
                   help="每轮交换攻守（第 2、4…轮把原守方放到攻方位置）")
    p.add_argument("--round-spawn-attacker", dest="round_spawn_attacker", default=None,
                   help='重生时攻方进场点，如 "100,0,200"（x,y,z 或 x,z；不给则用引擎默认）')
    p.add_argument("--round-spawn-defender", dest="round_spawn_defender", default=None,
                   help="重生时守方进场点")
    p.add_argument("--random-seed", dest="random_seed", type=int, default=None,
                   help="随机种子（同种子两次跑可逐值复现；不给就用引擎默认随机）")
    p.add_argument("--skip-troop-check", dest="skip_troop_check", action="store_true",
                   help="跳过兵种 id 校验（索引只覆盖官方 XML；用第三方模组兵种时加它）")
    p.add_argument("--timeout", type=float, default=60.0)

    w = sub.add_parser("wait", help="等待状态")
    w.add_argument("--state", default="ended")
    w.add_argument("--timeout", type=float, default=180.0)
    w.add_argument("--poll", type=float, default=2.0)

    ff = sub.add_parser("fastforward", help="开关战斗加速（10 倍速，对你自己手打的战斗也生效）")
    ff.add_argument("--on", action="store_true", help="开启")
    ff.add_argument("--off", action="store_true", help="关闭")
    sub.add_parser("speed", help="查看加速状态（是否生效/Scene.TimeSpeed/Mission.Mode）")
    sub.add_parser("buildcheck", help="核对 源码/构建产物/部署文件/进程内 DLL 是否一致")

    # ── 阶段 2④：批量跑批 + A/B 对比报告 ────────────────────────────────
    b = sub.add_parser("batch", help="按计划批量跑 N 场（一条命令跑 N 场）")
    b.add_argument("--plan", required=True, help="跑批计划 JSON")
    b.add_argument("--out", help="runs.json 输出路径（供 compare 用）")
    b.add_argument("--dry-run", action="store_true", help="只打印计划，不碰游戏")
    b.add_argument("--battles-dir", dest="battles_dir", help="战斗日志目录")

    c = sub.add_parser("compare", help="A/B 对比报告（主指标 = 满编窗口）")
    c.add_argument("--manifest", help="bl_batch 产出的 runs.json")
    c.add_argument("--a", help="A 组：文件 / 目录 / 逗号分隔")
    c.add_argument("--b", help="B 组：同上")
    c.add_argument("--label-a", dest="label_a", default="A")
    c.add_argument("--label-b", dest="label_b", default="B")
    c.add_argument("--out", help="把 markdown 报告写到该文件")

    args = ap.parse_args(argv[1:])
    if not args.cmd:
        ap.print_help()
        return 0

    if args.cmd == "ping":
        return _print(*bl_mcp.send_command("ping", {}, timeout=10))

    if args.cmd == "status":
        return _print(*bl_mcp.send_command("status", {}, timeout=10))

    if args.cmd == "abort":
        return _print(*bl_mcp.send_command("abort", {}, timeout=20))

    if args.cmd == "start":
        params = {
            "attackerTroop": args.attacker, "attackerCount": args.a,
            "defenderTroop": args.defender, "defenderCount": args.d,
            "scene": args.scene, "durationCapSec": args.cap,
            "orders": args.orders, "playerSide": args.player_side,
            "allowAnyState": "true" if args.allow_any_state else "false",
            "dummySide": args.dummy_side,
            "freezeDummies": "true" if args.freeze_dummies else "false",
            "unlimitedAmmo": "true" if args.unlimited_ammo else "false",
            }
        if getattr(args, "dummy_armor", None):
            try:
                # 必须传**数字**：C# 侧 Jmini.Num 只吃数字字符，字符串值会被判成"读不到"。
                # 解析失败一律报错退出 —— 静默跳过曾让 9 场实验整批作废（2026-09-24）。
                params.update(bl_common.parse_dummy_armor(args.dummy_armor))
            except ValueError as e:
                print("错误: --dummy-armor 解析失败：%s" % e)
                return 2
        if getattr(args, "dummy_body_item", None):
            params["dummyBodyItem"] = args.dummy_body_item
        if getattr(args, "random_seed", None) is not None:
            params["randomSeed"] = int(args.random_seed)
        for attr, key in (("attacker_groups", "attackerGroups"),
                          ("defender_groups", "defenderGroups")):
            raw = getattr(args, attr, None)
            if not raw:
                continue
            try:
                # 在**本地**先校验（GC3）：非法绝不透传给游戏端 —— 那边只会静默用默认值。
                bl_common.parse_squad_groups(raw)
            except ValueError as e:
                print("错误: --%s 解析失败：%s" % (attr.replace("_", "-"), e))
                return 2
            params[key] = raw
        # 兵种 id 前置校验（与 MCP bl_start_battle 同一语义）：CLI 打错 id 同样会被
        # 游戏端静默 fallback、白等一整场对局。给了 --attacker/--defender-groups 时
        # 对应的单兵种参数会被游戏端忽略，所以只校验真正生效的那组。
        # 索引不可用 ⇒ 警告但放行（软依赖：BlBridge 不因没装 BannerlordSage 而不能用）。
        ids = []
        if getattr(args, "attacker_groups", None):
            ids += [g["troop"] for g in bl_common.parse_squad_groups(args.attacker_groups)]
        else:
            ids.append(args.attacker)
        if getattr(args, "defender_groups", None):
            ids += [g["troop"] for g in bl_common.parse_squad_groups(args.defender_groups)]
        else:
            ids.append(args.defender)
        chk = bl_sage.check_troops(ids)
        if chk.get("available") and chk.get("missing"):
            if getattr(args, "skip_troop_check", False):
                print("警告: 兵种 id 不在索引里（--skip-troop-check 放行）：%s"
                      % ", ".join(chk["missing"]))
            else:
                print("错误: 兵种 id 在索引里不存在：%s" % ", ".join(chk["missing"]))
                for m in chk["missing"]:
                    s = bl_sage.suggest_troops(m)
                    if s:
                        print("  近似候选：%s -> %s" % (m, ", ".join(s)))
                print("  （索引只覆盖官方 XML；第三方模组兵种请加 --skip-troop-check）")
                return 2
        elif not chk.get("available"):
            print("警告: 兵种 id 未校验：%s" % (chk.get("reason") or "索引不可用"))
        if getattr(args, "rounds", None):
            params["rounds"] = int(args.rounds)
            if getattr(args, "round_end_alive", None) is not None:
                params["roundEndAlive"] = int(args.round_end_alive)
            if getattr(args, "round_swap", False):
                params["roundSwap"] = "true"
            if getattr(args, "round_spawn_attacker", None):
                params["roundSpawnAttacker"] = args.round_spawn_attacker
            if getattr(args, "round_spawn_defender", None):
                params["roundSpawnDefender"] = args.round_spawn_defender
        return _print(*bl_mcp.send_command("start_battle", params, timeout=args.timeout))

    if args.cmd == "wait":
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            resp, err = bl_mcp.send_command("status", {}, timeout=10)
            if err:
                print("错误: %s" % err)
                return 2
            st = (resp.get("result") or {}).get("state")
            prog = (resp.get("result") or {}).get("progress") or {}
            print("  state=%s  aAlive=%s dAlive=%s" % (st, prog.get("aAlive"), prog.get("dAlive")))
            if st == args.state:
                print(json.dumps((resp.get("result") or {}).get("result"), ensure_ascii=False, indent=1))
                return 0
            if st == "error":
                print("推演进入 error: %s" % (resp.get("result") or {}).get("lastError"))
                return 1
            time.sleep(args.poll)
        print("等待 %s 超时" % args.state)
        return 1

    if args.cmd == "fastforward":
        if not args.on and not args.off:
            # 不带参数 = 查看状态，绝不"默认关掉"（避免误触把加速关了还不知道）
            return _print(*bl_mcp.send_command("speed", {}, timeout=10))
        enabled = bool(args.on)
        return _print(*bl_mcp.send_command("fast_forward", {"enabled": enabled}, timeout=15))

    if args.cmd == "speed":
        return _print(*bl_mcp.send_command("speed", {}, timeout=10))

    if args.cmd == "buildcheck":
        r = bl_mcp.build_check()
        print("code   : %s" % r.get("code"))
        print("detail : %s" % r.get("detail"))
        for k in ("builtVersion", "builtUtc", "deployedSha256", "loadedSha256",
                  "loadedVersion", "fileChangedSinceLoad", "changedSources", "srcDir", "moduleDir"):
            if r.get(k) not in (None, "", []):
                print("%-7s: %s" % (k, r.get(k)))
        # game_offline 不是失败（游戏本来就可能没开）：文件链条已核对通过，只差进程内身份
        return 0 if r.get("code") in ("ok", "game_offline") else 1

    if args.cmd in ("batch", "compare"):
        # 转发到对应脚本的子进程：两者本来就是独立可执行的 CLI，
        # 而 bl_cmd.py 的职责是"控制通道"，不把跑批/报告的实现塞进来。
        script = "bl_batch.py" if args.cmd == "batch" else "bl_compare.py"
        cmd = [sys.executable, os.path.join(HERE, script)]
        if args.cmd == "batch":
            cmd += ["--plan", args.plan]
            if args.out:
                cmd += ["--out", args.out]
            if args.dry_run:
                cmd.append("--dry-run")
            if args.battles_dir:
                cmd += ["--battles-dir", args.battles_dir]
        else:
            if args.manifest:
                cmd += ["--manifest", args.manifest]
            if args.a:
                cmd += ["--a", args.a]
            if args.b:
                cmd += ["--b", args.b]
            cmd += ["--label-a", args.label_a, "--label-b", args.label_b]
            if args.out:
                cmd += ["--out", args.out]
        return subprocess.call(cmd)

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
