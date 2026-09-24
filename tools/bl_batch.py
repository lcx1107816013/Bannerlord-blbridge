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

plan 字段（顶层给默认值，单个 config 可覆盖）：
    label / runsPerConfig / scene / orders / playerSide / capSec
    configs[] = { label, attacker, a, defender, d, ...靶场参数 }

靶场参数（v0.8.0，可选；键名与 `bl_cmd.py start` 的开关一一对应）：
    dummySide       none|attacker|defender —— 把该方设为"永不倒下"的靶子
    dummyArmor      "head=45,torso=35"（字符串；只作用于靶子，每帧重申）
    freezeDummies   true/false
    unlimitedAmmo   true/false
    —— 以上任一非法（未知部位名 / 非数字 / 字符串布尔 / 非法阵营名）都会**直接报错中止**
    整批跑批，绝不静默跳过（静默丢弃曾让 9 场护甲实验整批作废，2026-09-24）。
    示例见同目录 plan.armor.example.json。
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
if DEFAULT_TOOLS not in sys.path:
    sys.path.insert(0, DEFAULT_TOOLS)
import bl_common  # noqa: E402
import bl_sage  # noqa: E402

DEFAULT_BATTLES = os.path.join(
    os.path.expanduser("~"), "Documents", "Mount and Blade II Bannerlord", "BlBridge", "battles"
)

# ── 靶场参数（v0.8.0）：plan 顶层给默认值，单配置可覆盖 ────────────────
# 键名与 `bl_cmd.py start` 的开关一一对应，值直接透传给 CLI（零翻译层）；
# 护甲的部位名清单只有一份，在 bl_common.DUMMY_ARMOR_PARTS。
DUMMY_PLAN_KEYS = ("dummySide", "freezeDummies", "unlimitedAmmo", "dummyArmor", "dummyBodyItem")

# ── 多轮参数（v0.8.5）：同样 plan 顶层默认 + 单配置覆盖 ────────────────
# ③「换边双跑」靠它：`rounds: 2` + `roundSwap: true` ⇒ 同一 mission 内第 1 轮被测兵种当守方、
# 第 2 轮攻守互换后它当攻方 —— 两轮环境完全一致，比跑两批独立 mission 干净（2026-09-24）。
ROUND_PLAN_KEYS = ("rounds", "roundEndAlive", "roundSwap",
                   "roundSpawnAttacker", "roundSpawnDefender")

# ── 多兵种 / 战术组（v0.8.8）：plan 顶层默认 + 单配置覆盖 ─────────────
# 值可以是 DSL 字符串，也可以是组列表（每项 dict：troop/count/formation/movement）——
# 列表会先转成 DSL，保证**只有一条解析路径**（bl_common.parse_squad_groups）。
SQUAD_PLAN_KEYS = ("attackerGroups", "defenderGroups")
_SQUAD_FIELDS = ("troop", "count", "formation", "movement")


def squads_to_dsl(groups):
    """组列表 → DSL 字符串（缺省字段省略）；已是字符串则原样返回（交给 parse 校验）。

    非法一律 ValueError（GC3）。注意 movement 是第 4 字段、不能跳过 formation：
    给了 movement 却没给 formation 会拼出 `a:10:charge`，那是会被当成 formation 的。
    """
    if isinstance(groups, str):
        return groups
    if not isinstance(groups, list):
        raise ValueError("组必须是 DSL 字符串或组列表，收到 %s" % type(groups).__name__)
    parts = []
    for i, g in enumerate(groups, 1):
        if not isinstance(g, dict):
            raise ValueError("第 %d 组不是对象：%r" % (i, g))
        unknown = [k for k in g if k not in _SQUAD_FIELDS]
        if unknown:
            raise ValueError("第 %d 组含未知字段 %s（可用：%s）"
                             % (i, unknown, ", ".join(_SQUAD_FIELDS)))
        if g.get("movement") and not g.get("formation"):
            raise ValueError("第 %d 组给了 movement 却没给 formation"
                             "（DSL 里 movement 是第 4 字段，不能跳过第 3 个）" % i)
        parts.append(":".join(str(g[k]) for k in _SQUAD_FIELDS if g.get(k) is not None))
    return "|".join(parts)


def resolve_dummy_params(plan, cfg, keys=DUMMY_PLAN_KEYS):
    """plan 顶层默认 + 单配置覆盖 ⇒ 只保留真正给了值的键。"""
    out = {}
    for k in keys:
        if k in cfg:
            out[k] = cfg[k]
        elif k in plan:
            out[k] = plan[k]
    return out


def build_start_args(plan, cfg, scene, orders, player_side, cap):
    """组装 `bl_cmd.py start` 的 CLI 参数（纯函数，故可在 bl_selftest 里手算断言）。

    靶场参数非法（未知部位名 / 非数字 / 对象形式）⇒ **抛 ValueError**，由调用方给出可读报错。
    **绝不静默丢弃** —— 那正是 9 场护甲实验整批作废的根因（2026-09-24）。
    """
    dummy = resolve_dummy_params(plan, cfg)
    out = [
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
    # plan 顶层 skipTroopCheck=true 必须**同时**透传给 CLI：bl_batch 只跳过了它自己的预检，
    # 而 `bl_cmd.py start` 里还有一道本地索引预检（bl_sage.check_troops），第三方模组兵种
    # 不在索引里 ⇒ 会被它拦下、整批 start_failed（2026-09-24 实跑踩过，当时只能绕开 batch）。
    if plan.get("skipTroopCheck"):
        out.append("--skip-troop-check")
    side = dummy.get("dummySide")
    if side is not None and side not in ("none", "attacker", "defender"):
        raise ValueError("dummySide 只能是 none/attacker/defender，收到 %r" % (side,))
    if side and side != "none":
        out += ["--dummy-side", str(side)]
    # 布尔必须真是 JSON 布尔：`"false"` 在 Python 里是**真值**，静默当成 true 会把开关写反
    for key, flag in (("freezeDummies", "--freeze-dummies"),
                      ("unlimitedAmmo", "--unlimited-ammo")):
        val = dummy.get(key)
        if val is not None and not isinstance(val, bool):
            raise ValueError("%s 必须是 true/false（JSON 布尔），收到 %r" % (key, val))
        if val is True:
            out += [flag]
    armor = dummy.get("dummyArmor")
    if armor:
        bl_common.parse_dummy_armor(armor)   # 只校验；值原样透传给 CLI
        out += ["--dummy-armor", armor]
    body_item = dummy.get("dummyBodyItem")
    if body_item:
        # 物品 id 不做本地校验：不认识的 id 由游戏端报 item_not_found 并写进 dummy_swap
        out += ["--dummy-body-item", str(body_item)]
    # ── 多兵种/战术组（v0.8.8）：组列表或 DSL ⇒ CLI 字符串 ─────────────
    # ⚠️ 必须放在下面那个 `if n is None: return out` **之前** —— 否则"没给 rounds"时会整段被跳过
    #    （这是本任务第一版差点踩到的坑：早退分支会吃掉后面的参数）。
    squads = resolve_dummy_params(plan, cfg, SQUAD_PLAN_KEYS)
    for key, flag in (("attackerGroups", "--attacker-groups"),
                      ("defenderGroups", "--defender-groups")):
        val = squads.get(key)
        if val is None:
            continue
        dsl = squads_to_dsl(val)
        bl_common.parse_squad_groups(dsl)   # GC3：非法在这里就报错，不透传给游戏端
        out += [flag, dsl]
    # ── 多轮参数（③ 换边双跑）：同样"非法即报错、绝不静默丢弃" ────────
    rounds = resolve_dummy_params(plan, cfg, ROUND_PLAN_KEYS)
    n = rounds.get("rounds")
    if n is None:
        for k in ROUND_PLAN_KEYS[1:]:
            if rounds.get(k) is not None:
                # bl_cmd.py 只在给了 rounds 时才解析这些键 ⇒ 单独给会被**静默忽略**
                raise ValueError("%s 需要同时给 rounds（多轮开关），否则会被 CLI 忽略" % (k,))
        return out
    if isinstance(n, bool) or not isinstance(n, int) or n < 2:
        raise ValueError("rounds 必须是 ≥2 的整数（1 = 不启用多轮），收到 %r" % (n,))
    out += ["--rounds", str(n)]
    end_alive = rounds.get("roundEndAlive")
    if end_alive is not None:
        if isinstance(end_alive, bool) or not isinstance(end_alive, int) or end_alive < 0:
            raise ValueError("roundEndAlive 必须是 ≥0 的整数，收到 %r" % (end_alive,))
        out += ["--round-end-alive", str(end_alive)]
    swap = rounds.get("roundSwap")
    if swap is not None and not isinstance(swap, bool):
        raise ValueError("roundSwap 必须是 true/false（JSON 布尔），收到 %r" % (swap,))
    if swap is True:
        out += ["--round-swap"]
    for key, flag in (("roundSpawnAttacker", "--round-spawn-attacker"),
                      ("roundSpawnDefender", "--round-spawn-defender")):
        val = rounds.get(key)
        if val:
            out += [flag, str(val)]
    return out


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
    # 必须在第一次 print 之前调：输出里有 ⇒/✅ 等符号，GBK 控制台下直接
    # UnicodeEncodeError 崩掉（2026-09-24 实测：--dry-run 都跑不完；bl_metrics /
    # bl_compare 中过同一招，见 PROGRESS 十三节）。
    bl_common.safe_streams()
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

    # 兵种 id 预检：跑批一开就是 N 局，任何 id 打错 ⇒ 整批全是无效样本。
    # dry-run 也做 —— 它本来就是"验证计划本身"的入口。索引不可用 ⇒ 警告放行
    # （软依赖）；plan 顶层 skipTroopCheck=true 可跳过（第三方模组兵种不在索引里，
    # 与 CLI --skip-troop-check / MCP skipTroopCheck 是同一个出口）。
    if not plan.get("skipTroopCheck"):
        # 与 bl_cmd.py 的校验语义对齐：某一侧给了 *Groups 时，同侧的单值兵种会被
        # CLI/游戏端**忽略**（见 bl_cmd.py start 的 id 收集），所以这里也只收集
        # 真正生效的那组 —— 否则被忽略的单值 id 会被误判 missing ⇒ 整批无谓中止。
        ids = set()
        for cfg in configs:
            for single_key, group_key in (("attacker", "attackerGroups"),
                                          ("defender", "defenderGroups")):
                val = cfg.get(group_key, plan.get(group_key))
                if val:
                    try:
                        dsl = val if isinstance(val, str) else squads_to_dsl(val)
                        for g in bl_common.parse_squad_groups(dsl):
                            ids.add(g["troop"])
                    except ValueError:
                        pass  # 语法错误由 build_start_args 正式报错，这里只负责收集 id
                elif cfg.get(single_key):
                    ids.add(str(cfg[single_key]))
        # 没有需要校验的 id 时不要调 check_troops([])，否则会打出「未预检：没有需要
        # 校验的兵种 id」这种看起来像出错的误导性警告（软依赖下本该静默放行）。
        if ids:
            chk = bl_sage.check_troops(sorted(ids))
            if chk.get("available") and chk.get("missing"):
                print("!! 兵种 id 在索引里不存在 —— 整批中止（否则 N 局全是无效样本）：")
                for m in chk["missing"]:
                    s = bl_sage.suggest_troops(m)
                    print("   %s%s" % (m, ("  -> 近似候选: " + ", ".join(s)) if s else ""))
                print("   （索引只覆盖官方 XML；第三方模组兵种在 plan 顶层加 \"skipTroopCheck\": true）")
                return 1
            if not chk.get("available"):
                print("[warn] 兵种 id 未预检：%s" % (chk.get("reason") or "索引不可用"))

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
        try:
            start_args = build_start_args(plan, cfg, scene, orders, player_side, cap)
        except ValueError as e:
            print("!! 配置 %s 的靶场参数非法：%s" % (label, e))
            print("   -> 中止跑批（靶场参数绝不静默跳过，否则整批数据作废）")
            return 1
        dummy = resolve_dummy_params(plan, cfg)
        entry = {
            "label": label,
            "attacker": cfg.get("attacker"),
            "defender": cfg.get("defender"),
            "playerSide": player_side,
            "orders": orders,
            "runs": [],
        }
        if dummy:
            entry["dummy"] = dummy
        print("\n---- 配置 %s: %s(%s) vs %s(%s) ----"
              % (label, cfg.get("attacker"), cfg.get("a", 20), cfg.get("defender"), cfg.get("d", 20)))
        if dummy:
            print("     靶场参数：%s" % json.dumps(dummy, ensure_ascii=False))

        for i in range(runs_per):
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
