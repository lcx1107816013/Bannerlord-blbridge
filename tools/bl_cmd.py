#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BlBridge 控制通道命令行（不依赖 MCP，可直接驱动游戏）。

  python bl_cmd.py status
  python bl_cmd.py start --attacker imperial_legionary --defender battanian_fian_champion --a 20 --d 20
  python bl_cmd.py wait --state ended --timeout 180
  python bl_cmd.py abort
  python bl_cmd.py ping
  python bl_cmd.py list-ui                        # 有哪些游戏内入口（含官方 CustomBattle）
  python bl_cmd.py open-ui --ui-id CustomBattle   # 走官方正门进选兵界面（不需要人手动点）
  python bl_cmd.py open-ui                        # 或进 BlBridge 战场面板

前置：游戏在跑、BlBridge 模块已启用、并且**停在「自定义战斗」界面或 BlBridge 战场面板**
（start 需要其中之一 —— 真正的判据是"自定义战斗数据已加载"，v0.8.12 起两者等价申报）。
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
import bl_rts  # noqa: E402
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
    sub.add_parser("skip-video", help="跳过开场动画（判 VideoPlaybackState → OnVideoFinished，不模拟 ESC）")
    cm = sub.add_parser("cheat", help="开关作弊模式（写 NativeConfig.CheatMode + 回读）")
    cm.add_argument("mode", nargs="?", default="status", choices=("status", "on", "off", "toggle"),
                    help="默认 status（只查）；开了它，引擎相机的 Ctrl+↑/↓ 倍率热键与速度读数才可用")
    eb = sub.add_parser("enter-battle", help="启动第二步：等主菜单就绪 → ESC 跳过场 → 进自定义战斗（每 5s 确认）")
    eb.add_argument("--ui-id", "--id", dest="ui_id", default="CustomBattle",
                    help="要进的入口 id，默认 CustomBattle")
    eb.add_argument("--menu-timeout", type=float, default=120.0, help="等主菜单就绪的上限秒数")
    eb.add_argument("--entry-timeout", type=float, default=60.0, help="open_ui 后等状态落地的上限秒数")
    eb.add_argument("--no-esc", action="store_true", help="不按 ESC 跳过场动画")
    # v0.8.44：补上 MCP 侧早已存在的 auto_open（`bl_launch_game` 的 autoOpen）。
    # 为什么需要：不加它时 `enter-battle` **永远**返回 `await_confirm`（"主菜单就绪即返回，
    # 不自动 fire open_ui"）⇒ **无人值守跑批会在这一步断掉**，只能调用方自己轮询 + 自己 open-ui。
    # 默认 False 是**刻意保留的安全门**（2026-09-25 启动动画期间 fire open_ui → 21 s 后崩溃，
    # 见本文件 `_enter_custom_battle` 的注释），不是缺陷；本次只是把已存在的参数暴露到 CLI。
    eb.add_argument("--auto-open", action="store_true",
                    help="无人值守：主菜单就绪后自动 fire open_ui（默认关闭；开启会套 --min-startup-sec 安全下限）")
    eb.add_argument("--min-startup-sec", type=float, default=20.0,
                    help="--auto-open 的安全下限秒数（默认 20）；仅 --auto-open 路径生效")
    sub.add_parser("status", help="推演状态机")
    sub.add_parser("abort", help="中止当前推演")

    # ── 游戏内 UI 入口（v0.8.12 起 agent 正门；v0.8.14 自建面板删除后只剩官方界面）──
    lu = sub.add_parser("list-ui", help="列出可进入的游戏内入口 + 官方自定义战斗场景全表")
    lu.add_argument("--mode", choices=("all", "battle", "siege", "village", "lordsHall",
                                       "naval", "navalRaid"), default=None,
                    help="只列某模式的场景（默认 all；海战/海上掠夺来自 NavalDLC 的场景表）")
    lu.add_argument("--limit", type=int, default=None, help="最多列多少行场景（默认 0 = 全吐）")
    ou = sub.add_parser("open-ui", help="唤起一个游戏内界面（默认 = 官方自定义战斗界面）")
    ou.add_argument("--ui-id", "--id", dest="ui_id", default=None,
                    help="入口 id，默认 CustomBattle；全部可用 id 见 list-ui。"
                         "注意线上参数名是 uiId，不是 id —— 控制通道的 JSON 读取器是扁平的，"
                         "id 会被信封里的请求 id 顶掉（v0.8.12 真机踩过）")
    cu = sub.add_parser("close-ui", help="从官方自定义战斗界面回主菜单（官方 PopState 同路径）")
    cu.add_argument("--state", choices=("CustomBattleState",), default=None,
                    help="要离开的状态；白名单只有 CustomBattleState（不接受任意状态名 —— "
                         "那等于让调用方 pop 引擎状态栈）")

    # ── RTSCamera 配置（v0.8.15，B 方案：我们只当它的"参数管理员"，不碰它的代码）──
    rc = sub.add_parser("rts-config", help="回读 RTSCamera 配置（只读）")
    rc.add_argument("--key", action="append", dest="keys", default=None,
                    help="只读指定键（可重复），如 --key ElevatedHeightInSiege")
    ra = sub.add_parser("rts-apply", help="写 RTSCamera 配置（自动备份 + XML 校验 + 回读核对）")
    ra.add_argument("--preset", default=None,
                    help="预设：siege-god(攻城抬升10) / free-always / elevated-always / god-full")
    ra.add_argument("--set", action="append", dest="sets", default=None,
                    help="逐键写入：--set ElevatedHeightInSiege=10（可重复）")
    ra.add_argument("--dry-run", action="store_true", help="只预览不写入")

    # ── 幽灵/自由相机（v0.8.16，C 方案：引擎自带的开发者自由镜头）──
    gc = sub.add_parser("ghost", help="开关引擎自带的自由观察相机（幽灵模式）")
    gc.add_argument("mode", nargs="?", default="status",
                    choices=("status", "on", "off", "toggle"),
                    help="默认 status（只查）；任何一场战斗里都能切，包括你自己打的")

    # ── 相机移动速度（v0.8.17：三条腿，见 src/CameraSpeed.cs 的类注释）──
    cs = sub.add_parser("camera-speed", help="相机移动速度（shift / base / rts / boost / probe）")
    cs.add_argument("mode", nargs="?", default="status",
                    choices=("status", "shift", "base", "rts", "boost", "probe"),
                    help="shift=引擎自由相机的 Shift 倍率（官方控制台函数，**不用作弊模式**）；"
                         "base=引擎基础倍率（反射，⚠️ 引擎把速度分量 clamp 在 ±20）；"
                         "rts=RTSCamera 的 MovementSpeedFactor（最有效，上限随速度一起放大）；"
                         "boost=三条可用腿一次设成同一个值；"
                         "probe=测速探针（不带 --end 记起点，带 --end 记终点并算速度）；默认 status（只查）")
    cs.add_argument("value", nargs="?", type=float, default=None,
                    help="倍率：shift 需 ≥1 的整数，其余 0.1~1000")
    cs.add_argument("--end", action="store_true", dest="probe_end",
                    help="仅 mode=probe 用：结束测量并返回 位移/秒数/平均速度")

    # ── 接管士兵（v0.8.25：最小版 = 改 Mission.MainAgent + Controller=Player）──
    ca = sub.add_parser("control-agent", help="接管某个友方士兵（take / release / status）")
    ca.add_argument("mode", nargs="?", default="take", choices=("take", "release", "status"),
                    help="默认 take（接管）；release = 换回接管前记录的原始主角色；status = 只读现状")
    ca.add_argument("--agent-index", dest="agent_index", type=int, default=None,
                    help="引擎的 agent.Index（遥测/事件里的 agent 字段）；优先级最高")
    ca.add_argument("--troop", default=None, help="兵种 id（Character.StringId）：取该方第一个存活的")
    ca.add_argument("--formation", default=None,
                    help="Infantry/Ranged/Cavalry/HorseArcher/Skirmisher 或下标 0~4")
    ca.add_argument("--side", choices=("player", "attacker", "defender"), default="player",
                    help="从哪一方里挑目标，默认 player（但只接受玩家方的 agent）")

    # ── 战斗中途改令（v0.8.23：mission 内 SetMovementOrder + 当场回读）──
    od = sub.add_parser("order", help="战斗中途改令（movement / 指定点 / 指定目标 / 指定单位 / 阵列 / 射击纪律 / 上下马，均当场回读）")
    od.add_argument("movement", nargs="?", choices=("charge", "advance", "fallback", "stop", "retreat"),
                    help="要下发的 movement（hold 已移除：引擎层本就等同 stop）；"
                         "与 --position / --target / --target-agent 四者互斥、至少给一个"
                         "（只给 --arrangement / --firing / --riding 也行）")
    od.add_argument("--position", default=None,
                    help='指定点移动（v0.8.30）："x,y" 或 "x,y,z"（英文逗号，单位米；z 省略 = 0，'
                         "由引擎按地面补 Z）。与位置参数 movement / --target 互斥")
    od.add_argument("--target", default=None,
                    help="冲锋到指定**敌方编队**（v0.8.31）：Infantry/Ranged/Cavalry/HorseArcher/"
                         "Skirmisher 或下标 0~4（敌方 = 与 --side 相对的那一方）。"
                         "回读 targetAfter / targetDistance（后者是双方编队重心距离，行为判据）。"
                         "与位置参数 movement / --position 互斥")
    od.add_argument("--side", choices=("player", "attacker", "defender"), default="player",
                    help="哪一方，默认 player（mission 里玩家侧仍是攻/守之一）")
    od.add_argument("--formation", default=None,
                    help="Infantry/Ranged/Cavalry/HorseArcher/Skirmisher 或下标 0~4；不传 = 该方所有有兵编队")
    od.add_argument("--arrangement", default=None,
                    choices=("line", "shieldwall", "circle", "square", "skein", "column", "loose", "scatter"),
                    help="编队阵列（`ArrangementOrder`）；不传 = 不改")
    od.add_argument("--firing", default=None, choices=("fireAtWill", "holdFire"),
                    help="射击纪律（`FiringOrder`，引擎只有两档）；不传 = 不改")
    od.add_argument("--target-agent", dest="target_agent", type=int, default=None,
                    help="攻击指定**敌方单位**（v0.8.32）：agent 下标（`Agent.Index`，从遥测或 "
                         "control-agent status 的 candidates 取）。引擎侧是 "
                         "MovementOrderAttackEntity（surround=true）。回读 targetEntitySet / "
                         "targetAgentAlive / targetDistance（后者是编队重心到该单位的距离，行为判据）。"
                         "与 movement / --position / --target 四者互斥。⚠️ 目标会死：阵亡后重申会跳过并记 "
                         "order error，不静默改成冲锋")
    od.add_argument("--riding", default=None, choices=("free", "mount", "dismount"),
                    help="骑乘令（v0.8.32，`Formation.SetRidingOrder`，引擎三档：free 不干预 / "
                         "mount 上马 / dismount 下马）；与 movement order 正交（只管骑不骑、不管去哪），"
                         "所以**不参与** movement / --position / --target 的三者互斥；不传 = 不改")
    od.add_argument("--no-detach-ai", action="store_true",
                    help="不连带 SetControlledByAI(false,false)——用于对照实验（大概率被该方战术覆盖）")

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
    p.add_argument("--spectate", action="store_true",
                   help="兜底观战镜头（v0.8.14）：挂官方 ICameraModeLogic 走自由观察相机。"
                        "⚠️ 装了 RTSCamera 时野战会被它抢先（等于无效），优先用 --rts-preset；"
                        "这个开关留给没装 RTSCamera 的机器")
    p.add_argument("--rts-preset", dest="rts_preset", default=None,
                   help="开战前套用 RTSCamera 预设（siege-god / free-always / elevated-always / god-full）。"
                        "实测改配置无需重启、对本场立即生效；会先备份配置文件")
    p.add_argument("--allow-any-state", action="store_true",
                   help="跳过「必须停在自定义战斗界面」检查。⚠️ 不能用来从主菜单开战"
                        "（裸主菜单下兵种数据未加载 ⇒ unknown_troop）；也无需再用来绕 wrong_state"
                        "（v0.8.44 起带 NavalDLC 的 NavalCustomBattleState 已被正常接受）")
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
                        "—— 它们此时只用于回显；非法直接报错，不静默跳过。"
                        "⚠️ 第 3 字段 formation **不决定编队**（编队由兵种自身决定，该字段只回显）："
                        "写了不同族的名字会在 start 返回的 formationWarnings 里被点名，但不阻断开战）")
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
    # ── v0.8.41：战术档位 + 环境旋钮（零 Harmony）──────────────────────
    p.add_argument("--attacker-tactic-level", dest="attacker_tactic_level", type=int, default=None,
                   help=("-1（不覆盖，默认）或 0..100。引擎按 20/50 分档决定该方挂哪些 TacticOption；"
                         "⚠️ 默认 orders=charge 会清空战术菜单，要看效果要配 --orders default"))
    p.add_argument("--defender-tactic-level", dest="defender_tactic_level", type=int, default=None,
                   help="守方战术档位，语义同上")
    p.add_argument("--terrain", default=None,
                   help="地形覆盖（默认不设=引擎按场景决定）。可选：" + " | ".join(bl_common.TERRAINS))
    p.add_argument("--random-terrain-seed", dest="random_terrain_seed", type=int, default=None,
                   help="随机地形种子（设了会同时打开 NeedsRandomTerrain）")
    p.add_argument("--ai-friendly-fire", dest="ai_friendly_fire", type=float, default=None,
                   help=("设「打到玩家方身上」的伤害倍率 0..1（默认不设=引擎默认 1）。"
                         "**不是同队误伤开关** —— 引擎判据的「朋友」= 受害者属于玩家一方"
                         "（本靶场玩家方 = Attacker）。实测设 0 时该侧命中零伤害占比从 20 升到 72（个百分点），"
                         "显著但非全部归零"))
    p.add_argument("--keep-corpses", dest="keep_corpses", action="store_true",
                   help=("关掉尸体淡出（长跑/多轮时尸体量稳定）。"
                         "可用 sample 事件的 corpses 字段验证：对照会回落到 0，开了则从不回落"))
    p.add_argument("--scene-level", dest="scene_level", type=int, default=None,
                   help="攻城场景升级等级 1..3（默认 3 = 改动前的写死值）")
    p.add_argument("--time-of-day", dest="time_of_day", type=float, default=None,
                   help="攻城开战时刻 0..24 小时（默认 6 = 改动前的写死值）")
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
    # ── v0.8.42：动作账本（commands/actions.jsonl）────────────────────────
    ac = sub.add_parser("actions", help="读控制通道动作账本：每个请求一行（含被拒的）")
    ac.add_argument("--limit", type=int, default=30, help="只显示最后 N 条（0 = 全显示），默认 30")
    ac.add_argument("--fail-only", dest="fail_only", action="store_true",
                    help="只看**信封**失败项（ok=false，请求没被处理）")
    # v0.8.46：第二层口径 ——「信封成功、但**操作**失败」。实测这类占 2.00%、
    # 涉及 10 个方法（skip_video / order / control_agent / ghost_camera / camera_speed …），
    # 旧账本**完全看不出来**（因为信封就是 ok=true）。判据用 3 值：真 / 假 / 未知。
    ac.add_argument("--op-fail-only", dest="op_fail_only", action="store_true",
                    help="只看**操作**失败项（resultOk 明确为 false）；"
                         "历史行没有该字段 ⇒ 视为未知，**不计入**")
    ac.add_argument("--json", action="store_true", help="原样吐 JSON 行，便于二次处理")
    ac.add_argument("--path", default=None, help="直接指定 actions.jsonl 路径（默认按日志目录推导）")

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

    if args.cmd == "skip-video":
        return _print(*bl_mcp.send_command("skip_video", {}, timeout=10))

    if args.cmd == "cheat":
        params = {} if args.mode == "status" else {"mode": args.mode}
        return _print(*bl_mcp.send_command("cheat_mode", params, timeout=10))

    if args.cmd == "enter-battle":
        out = bl_mcp._enter_custom_battle(ui_id=args.ui_id, menu_timeout=args.menu_timeout,
                                          entry_timeout=args.entry_timeout,
                                          skip_intro=not args.no_esc,
                                          auto_open=args.auto_open,
                                          min_startup_sec=args.min_startup_sec)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if out.get("ok") else 1

    if args.cmd == "control-agent":
        params = {"mode": args.mode, "side": args.side}
        if args.agent_index is not None:
            params["agentIndex"] = args.agent_index
        if args.troop:
            params["troop"] = args.troop
        if args.formation:
            params["formation"] = args.formation
        return _print(*bl_mcp.send_command("control_agent", params, timeout=15))

    if args.cmd == "order":
        # ⚠️ 用 `not in (None, "")` 而不是 `if v`：agent 下标可以是 **0**，`0` 是假值，
        # 用 `if v` 会把 `--target-agent 0` 静默当成"没给"（这类静默正是本项目最忌讳的）。
        given = [k for k, v in (("movement", args.movement), ("--position", args.position),
                                ("--target", args.target),
                                ("--target-agent", args.target_agent)) if v not in (None, "")]
        if len(given) > 1:
            print("错误: %s 互斥 —— 一个编队只能有一个 movement order" % " / ".join(given))
            return 2
        if not (given or args.arrangement or args.firing or args.riding):
            print("错误: movement / --position / --target / --target-agent / --arrangement / "
                  "--firing / --riding 至少要给一个")
            return 2
        params = {"side": args.side}
        if args.movement:
            params["movement"] = args.movement
        if args.position:
            params["position"] = args.position
        if args.target:
            params["target"] = args.target
        if args.target_agent is not None:
            params["targetAgent"] = args.target_agent
        if args.formation:
            params["formation"] = args.formation
        if args.arrangement:
            params["arrangement"] = args.arrangement
        if args.firing:
            params["firing"] = args.firing
        if args.riding:
            params["riding"] = args.riding
        if args.no_detach_ai:
            params["detachAI"] = False
        return _print(*bl_mcp.send_command("order", params, timeout=10))

    if args.cmd == "status":
        return _print(*bl_mcp.send_command("status", {}, timeout=10))

    if args.cmd == "abort":
        return _print(*bl_mcp.send_command("abort", {}, timeout=20))

    if args.cmd == "ghost":
        params = {} if args.mode == "status" else {"mode": args.mode}
        return _print(*bl_mcp.send_command("ghost_camera", params, timeout=10))

    if args.cmd == "camera-speed":
        params = {} if args.mode == "status" else {"mode": args.mode}
        if args.value is not None:
            params["value"] = args.value
        if getattr(args, "probe_end", False):
            params["action"] = "end"
        return _print(*bl_mcp.send_command("camera_speed", params, timeout=10))

    if args.cmd == "rts-config":
        try:
            values = bl_rts.read(getattr(args, "keys", None))
        except Exception as e:  # noqa: BLE001
            print("错误: %s" % e)
            return 2
        print(json.dumps({"path": bl_rts.config_path(), "values": values},
                         ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "rts-apply":
        try:
            if getattr(args, "preset", None):
                out = bl_rts.apply_preset(args.preset,
                                          dry_run=bool(getattr(args, "dry_run", False)))
            else:
                edits = []
                for kv in (getattr(args, "sets", None) or []):
                    if "=" not in kv:
                        print("错误: --set 需要 KEY=VALUE 形式，实得 %r" % kv)
                        return 2
                    k, v = kv.split("=", 1)
                    edits.append({"key": k.strip(), "value": v.strip()})
                if not edits:
                    print("错误: 需要 --preset，或至少一个 --set KEY=VALUE")
                    return 2
                out = bl_rts.apply(edits, dry_run=bool(getattr(args, "dry_run", False)))
        except Exception as e:  # noqa: BLE001
            print("错误: %s" % e)
            return 2
        print(json.dumps(out, ensure_ascii=False, indent=1))
        if out.get("dryRun"):
            return 0
        return 0 if out.get("ok") else 1

    if args.cmd == "list-ui":
        params = {}
        if getattr(args, "mode", None):
            params["scenesMode"] = args.mode
        if getattr(args, "limit", None):
            params["sceneLimit"] = int(args.limit)
        return _print(*bl_mcp.send_command("list_ui", params, timeout=15))

    if args.cmd == "open-ui":
        params = {"uiId": args.ui_id} if args.ui_id else {}
        return _print(*bl_mcp.send_command("open_ui", params, timeout=20))

    if args.cmd == "close-ui":
        params = {"state": args.state} if args.state else {}
        return _print(*bl_mcp.send_command("close_ui", params, timeout=15))

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
        # v0.8.14：上帝视角 —— 只在开启时才加键，保持旧命令的请求字节逐字不变
        # （"请求面悄悄漂移"是这个项目最贵的一类坑：离线绿灯、真机才炸）
        if args.spectate:
            params["spectate"] = "true"
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
        # ── v0.8.41：战术档位 + 环境旋钮 ─────────────────────────────────
        # 数值参数发**裸数字**（C# 侧 Jmini.Num 只吃数字字符，字符串会被判成"读不到"）；
        # 枚举/范围在本地先校验（GC3）：游戏端读不到就是静默落 fallback ⇒ "参数没生效"。
        for attr, key in (("attacker_tactic_level", "attackerTacticLevel"),
                          ("defender_tactic_level", "defenderTacticLevel")):
            val = getattr(args, attr, None)
            if val is None:
                continue
            try:
                params[key] = bl_common.check_tactic_level(val, "--" + attr.replace("_", "-"))
            except ValueError as e:
                print("错误: %s" % e)
                return 2
        if getattr(args, "terrain", None):
            try:
                params["terrain"] = bl_common.parse_terrain(args.terrain)
            except ValueError as e:
                print("错误: %s" % e)
                return 2
        if getattr(args, "random_terrain_seed", None) is not None:
            params["randomTerrainSeed"] = int(args.random_terrain_seed)
        if getattr(args, "ai_friendly_fire", None) is not None:
            ff = float(args.ai_friendly_fire)
            if ff < 0.0 or ff > 1.0:
                print("错误: --ai-friendly-fire 只接受 0..1（引擎默认 1），收到 %s" % ff)
                return 2
            params["aiFriendlyFireMultiplier"] = ff
        if getattr(args, "keep_corpses", False):
            params["keepCorpses"] = "true"
        if getattr(args, "scene_level", None) is not None:
            lv = int(args.scene_level)
            if lv < 1 or lv > 3:
                print("错误: --scene-level 只接受 1..3（攻城场景升级等级），收到 %d" % lv)
                return 2
            params["sceneLevel"] = lv
        if getattr(args, "time_of_day", None) is not None:
            tod = float(args.time_of_day)
            if tod < 0.0 or tod > 24.0:
                print("错误: --time-of-day 只接受 0..24（小时），收到 %s" % tod)
                return 2
            params["timeOfDay"] = tod
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
        # v0.8.15：开战前套用 RTSCamera 预设（B 方案）。实测 RTSCamera 每场开始读一次配置，
        # 所以"改完立刻开战"就对本场生效；它事后会覆写，所以这一步不能提前太久。
        if getattr(args, "rts_preset", None):
            try:
                out = bl_rts.apply_preset(args.rts_preset)
            except Exception as e:  # noqa: BLE001
                print("错误: --rts-preset 套用失败：%s" % e)
                return 2
            print("rts-preset: %s" % json.dumps(out, ensure_ascii=False))
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

    if args.cmd == "actions":
        limit = None if int(args.limit) <= 0 else int(args.limit)
        entries, stats = bl_common.load_actions(args.path, limit=None,
                                                fail_only=bool(args.fail_only))
        # v0.8.46：第二层口径。**必须在 load_actions 之后自己筛**，而不是加参数进去 ——
        # 因为 3 值语义（true/false/None）用 `fail_only` 那种二值开关表达不了，
        # 硬塞会把"未知"当成"失败"（`not resultOk` 的经典错误）。
        if args.op_fail_only:
            entries = [e for e in entries if e.get("resultOk") is False]
        if limit is not None and limit >= 0:
            entries = entries[-limit:]
        if stats.get("missing"):
            print("账本还不存在：%s" % stats["path"])
            print("（v0.8.42 起，游戏每次处理一个请求就追加一行；先跑一条命令再看）")
            return 0
        if args.json:
            for ev in entries:
                print(json.dumps(ev, ensure_ascii=False))
        else:
            print("账本: %s" % stats["path"])
            print("非空行 %d ／ 解析失败 %d ／ 本次显示 %d 条 ／ 会话 %s"
                  % (stats["lines"], stats["bad"], len(entries),
                     ", ".join(stats["runs"]) if stats["runs"] else "-"))
            # ⚠️ 坏行**必须显式报出来**：load_events 那种"静默跳过"正是 v0.8.4 非法 JSON
            #    能活下来的原因（分析器不报错、只是少一段数据）。审计数据不接受这种静默。
            for s in stats["badSamples"]:
                print("  [坏行] %s" % s)
            # v0.8.46：多一列 `opOk` —— 两个 ok 各占一列，一眼看出"信封成功但操作失败"。
            # 三态显示：OK / FAIL / **?**（未知：历史行没有该字段，或信封失败/结果无 ok）。
            print("%-23s %-4s %-4s %-24s %-22s %7s  %s"
                  % ("t(UTC)", "ok", "opOk", "method", "code", "ms", "note"))
            for ev in entries:
                r = ev.get("resultOk")
                op = "?" if r is None else ("OK" if r else "FAIL")
                print("%-23s %-4s %-4s %-24s %-22s %7s  %s"
                      % (str(ev.get("t", ""))[:23], "OK" if ev.get("ok") else "FAIL", op,
                         str(ev.get("method", ""))[:24], str(ev.get("code", ""))[:22],
                         ev.get("ms", ""), str(ev.get("note", ""))[:60]))
        return 0

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
