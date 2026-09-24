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
        print("error: %s" % err)
        return 2
    print(json.dumps(resp, ensure_ascii=False, indent=1))
    return 0 if resp.get("ok") else 1


def main(argv):
    ap = argparse.ArgumentParser(description="BlBridge control-channel CLI")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("ping", help="connectivity / version")
    sub.add_parser("status", help="scenario state machine")
    sub.add_parser("abort", help="abort the current scenario")

    p = sub.add_parser("start", help="run one AI-vs-AI battle")
    p.add_argument("--attacker", required=True)
    p.add_argument("--defender", required=True)
    p.add_argument("--a", type=int, default=20, help="attacker count")
    p.add_argument("--d", type=int, default=20, help="defender count")
    p.add_argument("--scene", default="battle_terrain_a")
    p.add_argument("--cap", type=int, default=600, help="per-battle time cap (in-game seconds)")
    p.add_argument("--orders", choices=("charge", "default"), default="charge",
                   help="charge=symmetric charge for both sides (default; removes attack/defense "
                        "bias); default=engine tactics")
    p.add_argument("--player-side", choices=("attacker", "defender"), default="attacker",
                   help="which side is flagged as the player (only to test whether that flag "
                        "biases results)")
    p.add_argument("--allow-any-state", action="store_true",
                   help="skip the 'must be on the custom-battle screen' check "
                        "(to start straight from the main menu)")
    p.add_argument("--dummy-side", choices=("none", "attacker", "defender"), default="none",
                   help="immortal dummy range: make this side never fall (phase 2-1)")
    p.add_argument("--freeze-dummies", action="store_true",
                   help="in dummy-range mode, freeze the dummy AI (no fighting back; off by "
                        "default -- freezing changes AI behavior)")
    p.add_argument("--unlimited-ammo", action="store_true",
                   help="refill the archers' ammo (for long runs; the dummy's ammo is NOT "
                        "refilled -- it is the subject under test)")
    p.add_argument("--dummy-armor", default=None,
                   help="override the dummy's armor values, e.g. head=45,torso=35,legs=20,arms=25"
                        " (dummy only; unlisted parts untouched; re-applied every frame, no "
                        "Harmony; an unknown part name or a non-numeric value errors out "
                        "instead of being skipped silently)")
    p.add_argument("--dummy-body-item", dest="dummy_body_item", default=None,
                   help="swap the dummy's body armor to this item id (material A/B; empty = no "
                        "swap. Material resistance comes only from the item; values are still "
                        "aligned by --dummy-armor)")
    p.add_argument("--attacker-groups", dest="attacker_groups", default=None,
                   help="attacker multi-troop / tactic groups: troop:count[:formation[:movement]], "
                        "groups separated by |, e.g. "
                        '"imperial_legionary:10:Infantry:stop|khuzait_khans_guard:5:HorseArcher:charge"'
                        " (when given, --attacker/--a are ignored; invalid input errors out "
                        "instead of being skipped silently)")
    p.add_argument("--defender-groups", dest="defender_groups", default=None,
                   help="defender multi-troop / tactic groups, same syntax as --attacker-groups "
                        "(when given, --defender/--d are ignored)")
    p.add_argument("--rounds", type=int, default=None,
                   help="multi-round run: N rounds inside one mission (one log file per round; "
                        "default 1 = off)")
    p.add_argument("--round-end-alive", dest="round_end_alive", type=int, default=None,
                   help="a side left with <= this many alive ends the round (default 1)")
    p.add_argument("--round-swap", action="store_true",
                   help="swap sides every round (rounds 2, 4, ... put the former defender on the "
                        "attacker side)")
    p.add_argument("--round-spawn-attacker", dest="round_spawn_attacker", default=None,
                   help='attacker spawn point on respawn, e.g. "100,0,200" (x,y,z or x,z; '
                        "engine default when omitted)")
    p.add_argument("--round-spawn-defender", dest="round_spawn_defender", default=None,
                   help="defender spawn point on respawn")
    p.add_argument("--random-seed", dest="random_seed", type=int, default=None,
                   help="random seed (the same seed reproduces runs value-by-value; engine "
                        "default when omitted)")
    p.add_argument("--skip-troop-check", dest="skip_troop_check", action="store_true",
                   help="skip troop-id validation (the index only covers official XML; add this "
                        "for third-party mod troops)")
    p.add_argument("--timeout", type=float, default=60.0)

    w = sub.add_parser("wait", help="wait for a state")
    w.add_argument("--state", default="ended")
    w.add_argument("--timeout", type=float, default=180.0)
    w.add_argument("--poll", type=float, default=2.0)

    ff = sub.add_parser("fastforward", help="toggle battle fast-forward (10x; also affects "
                                           "battles you fight yourself)")
    ff.add_argument("--on", action="store_true", help="turn on")
    ff.add_argument("--off", action="store_true", help="turn off")
    sub.add_parser("speed", help="show fast-forward state (active / Scene.TimeSpeed / Mission.Mode)")
    sub.add_parser("buildcheck", help="compare source / build output / deployed file / in-process DLL")

    # ── 阶段 2④：批量跑批 + A/B 对比报告 ────────────────────────────────
    b = sub.add_parser("batch", help="run N battles from a plan (one command for a whole batch)")
    b.add_argument("--plan", required=True, help="batch plan JSON")
    b.add_argument("--out", help="runs.json output path (for compare)")
    b.add_argument("--dry-run", action="store_true", help="print the plan only, do not touch the game")
    b.add_argument("--battles-dir", dest="battles_dir", help="battle log directory")

    c = sub.add_parser("compare", help="A/B report (main metric = full-strength window)")
    c.add_argument("--manifest", help="runs.json produced by bl_batch")
    c.add_argument("--a", help="group A: file / dir / comma-separated")
    c.add_argument("--b", help="group B: same as A")
    c.add_argument("--label-a", dest="label_a", default="A")
    c.add_argument("--label-b", dest="label_b", default="B")
    c.add_argument("--out", help="write the markdown report to this file")

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
                print("error: --dummy-armor parse failed: %s" % e)
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
                print("error: --%s parse failed: %s" % (attr.replace("_", "-"), e))
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
                print("warning: troop id(s) not in the index (allowed by --skip-troop-check): %s"
                      % ", ".join(chk["missing"]))
            else:
                print("error: troop id(s) not in the index: %s" % ", ".join(chk["missing"]))
                for m in chk["missing"]:
                    s = bl_sage.suggest_troops(m)
                    if s:
                        print("  similar candidates: %s -> %s" % (m, ", ".join(s)))
                print("  (the index covers official XML only; for third-party mod troops add "
                      "--skip-troop-check)")
                return 2
        elif not chk.get("available"):
            print("warning: troop ids not validated: %s"
                  % (chk.get("reason") or "index unavailable"))
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
                print("error: %s" % err)
                return 2
            st = (resp.get("result") or {}).get("state")
            prog = (resp.get("result") or {}).get("progress") or {}
            print("  state=%s  aAlive=%s dAlive=%s" % (st, prog.get("aAlive"), prog.get("dAlive")))
            if st == args.state:
                print(json.dumps((resp.get("result") or {}).get("result"), ensure_ascii=False, indent=1))
                return 0
            if st == "error":
                print("scenario entered error: %s" % (resp.get("result") or {}).get("lastError"))
                return 1
            time.sleep(args.poll)
        print("timeout waiting for %s" % args.state)
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
