#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BlBridge 阶段 2④ · 跑批编排：一条命令跑 N 场。

实现方式：**子进程调用 BlBridge 自带的 CLI**（`tools/bl_cmd.py start|wait|status`）——
不 import 它的内部模块，只依赖稳定 CLI 接口，因此本文件可以放在任意目录。

换边协议（上一轮实测证明为强制，不是建议）：
    固定 playerSide=attacker，兵种靠**换边**抵消攻守与标记的双重效应：
    A 当攻方跑 N 局、B 当攻方跑 N 局，只比"同为攻方 + 玩家侧"位置上的战绩。
    所以 plan.json 里两个 config 的 attacker/defender 应当对调。

用法：
    python bl_batch.py --plan plan.json                 # 真跑（需游戏在跑 + 停在自定义战斗界面）
    python bl_batch.py --plan plan.json --dry-run       # 只打印计划，不碰游戏
    python bl_batch.py --plan plan.json --out runs.json # 产出给 bl_compare.py 的清单
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time

# 并入 BlBridge\tools\ 之后 bl_cmd.py 就在本脚本旁边 —— 用自身目录，
# 不再硬编码 CodeBuddy 的工作区路径。
DEFAULT_TOOLS = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BATTLES = os.path.join(
    os.path.expanduser("~"), "Documents", "Mount and Blade II Bannerlord", "BlBridge", "battles"
)


def run_cli(tools_dir, args, timeout):
    """调用 bl_cmd.py；返回 (returncode, stdout, stderr)。"""
    cmd = [sys.executable, os.path.join(tools_dir, "bl_cmd.py")] + args
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %ss" % timeout


def latest_battle(battles_dir, since_ts):
    """取 since_ts 之后最新的一场 JSONL（按 mtime）。"""
    best = None
    for p in glob.glob(os.path.join(battles_dir, "*.jsonl")):
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        if mt >= since_ts and (best is None or mt > best[0]):
            best = (mt, p)
    return best[1] if best else None


def preflight(plan, tools_dir, dry):
    """开跑前检查：桥是否就绪。

    注意：`bl_cmd.py status` 只报**推演状态机**（idle/loading/running/ended/error），
    不含游戏的 GameState 名。所以"是否停在自定义战斗界面"**不能**在这里判定 ——
    那由 `start_battle` 自己在游戏端检查 `CustomBattleState` 并回 `wrong_state`。
    （初版这里去 status 输出里找 "custombattle"，是个恒假的判据，实测暴露。）
    """
    if dry:
        print("[dry-run] 跳过 preflight")
        return True
    code, out, err = run_cli(tools_dir, ["status"], timeout=20)
    print("[preflight] bl_cmd.py status -> rc=%d" % code)
    if err.strip():
        print("  stderr: %s" % err.strip()[:300])
    if code != 0 or '"ok": true' not in out:
        print("  !! status 未成功 —— 游戏没在跑 / BlBridge 模块未启用？")
        print("     输出片段：%s" % out.strip()[:300])
        return False
    print("  OK：桥已就绪（界面名由 start 自己判定）")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="跑批计划 JSON")
    ap.add_argument("--tools-dir", default=DEFAULT_TOOLS, help="BlBridge tools 目录（含 bl_cmd.py）")
    ap.add_argument("--battles-dir", default=DEFAULT_BATTLES)
    ap.add_argument("--out", help="把 runs.json 写到该路径（供 bl_compare.py 用）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wait-timeout", type=float, default=300.0, help="单场等待上限（秒）")
    args = ap.parse_args()

    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)

    runs_per = int(plan.get("runsPerConfig", 3))
    scene = plan.get("scene", "battle_terrain_a")
    orders = plan.get("orders", "charge")
    player_side = plan.get("playerSide", "attacker")
    cap = int(plan.get("capSec", 600))
    configs = plan.get("configs", [])
    if not configs:
        print("plan 里没有 configs")
        return 1

    print("=" * 88)
    print("跑批计划：%s" % plan.get("label", "(未命名)"))
    print("  每个配置 %d 局；scene=%s orders=%s playerSide=%s cap=%ds" % (runs_per, scene, orders, player_side, cap))
    total = runs_per * len(configs)
    print("  合计 %d 局；10 倍速下 20v20 约 20~40s/局 ⇒ 预计 %.0f~%.0f 分钟"
          % (total, total * 20 / 60.0, total * 45 / 60.0))
    print("  换边协议：固定 playerSide=%s，用 attacker/defender 对调抵消攻守效应" % player_side)
    print("=" * 88)

    if not os.path.isfile(os.path.join(args.tools_dir, "bl_cmd.py")):
        print("!! 找不到 %s" % os.path.join(args.tools_dir, "bl_cmd.py"))
        return 2

    if not preflight(plan, args.tools_dir, args.dry_run):
        print("\n[abort] preflight 未通过。修好后重跑（记得 --dry-run 可先验证计划本身）。")
        return 1

    manifest = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "plan": plan.get("label", ""),
        "runsPerConfig": runs_per,
        "protocol": "固定 playerSide=%s；兵种换边双跑；每配置 >=3 局" % player_side,
        "configs": [],
    }

    for cfg in configs:
        label = cfg.get("label", "?")
        entry = {
            "label": label,
            "attacker": cfg.get("attacker"),
            "defender": cfg.get("defender"),
            "playerSide": player_side,
            "orders": orders,
            "runs": [],
        }
        print("\n---- 配置 %s: %s(%s) vs %s(%s) ----"
              % (label, cfg.get("attacker"), cfg.get("a", 20), cfg.get("defender"), cfg.get("d", 20)))

        for i in range(runs_per):
            start_args = [
                "start",
                "--attacker", str(cfg.get("attacker")),
                "--defender", str(cfg.get("defender")),
                "--a", str(cfg.get("a", 20)),
                "--d", str(cfg.get("d", 20)),
                "--scene", scene,
                "--cap", str(cap),
                "--orders", orders,
                "--player-side", player_side,
            ]
            wait_args = ["wait", "--state", "ended", "--timeout", str(args.wait_timeout)]

            if args.dry_run:
                print("  [%d/%d] would run: bl_cmd.py %s" % (i + 1, runs_per, " ".join(start_args)))
                print("        then:     bl_cmd.py %s" % " ".join(wait_args))
                entry["runs"].append({"file": None, "dryRun": True})
                continue

            t0 = time.time()
            code, out, err = run_cli(args.tools_dir, start_args, timeout=90)
            print("  [%d/%d] start rc=%d" % (i + 1, runs_per, code))
            if code != 0:
                print("        start 失败：%s%s" % (out.strip()[:200], err.strip()[:200]))
                print("        -> 跳过本局（不要盲重试；先看上面错误）")
                entry["runs"].append({"file": None, "error": "start_failed"})
                continue

            code, out, err = run_cli(args.tools_dir, wait_args, timeout=args.wait_timeout + 60)
            print("  [%d/%d] wait  rc=%d" % (i + 1, runs_per, code))
            if code != 0:
                print("        wait 非 0：%s%s" % (out.strip()[-300:], err.strip()[:200]))
                entry["runs"].append({"file": None, "error": "wait_failed"})
                continue

            f = latest_battle(args.battles_dir, t0)
            print("        战斗文件：%s" % (os.path.basename(f) if f else "(未找到)"))
            entry["runs"].append({"file": f, "dryRun": False})

        manifest["configs"].append(entry)

    out_path = args.out or os.path.join(os.path.dirname(os.path.abspath(args.plan)), "runs.json")
    if not args.dry_run:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)
        print("\n[written] %s" % out_path)
        print("下一步： python bl_compare.py --manifest \"%s\"" % out_path)
    else:
        print("\n[dry-run] 未执行任何命令、未写文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
