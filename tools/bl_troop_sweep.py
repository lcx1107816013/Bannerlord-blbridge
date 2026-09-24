#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全兵种扫描：① 可用性判定（游戏端认不认这些兵种 id）② 覆盖扫描（认了却没生成出来）。

背景（2026-09-25，v0.8.8）：
  清单 = 官方 BannerlordSage 索引（1981 条）+ MOD WarlordsBattlefieldWarSailsEdition（1239 条），
  去重后 3200 个。上一轮把它们当战斗打（11 批 × 每方 150）是**错的**做法：
  游戏端在 start 的校验阶段就会因 `unknown_troop` 中止，根本不建 mission。
  本工具据此把两件事拆开：

  probe   把 id 分包（默认每包 800）+ 每包追加一个**已知坏 id 当引信**，
          让每次 start 必然以 `unknown_troop` 收场 ⇒ 永不建 mission
          ⇒ 绕开「成功 start 之后 abort 拉不回 loading、只能重启游戏」的引擎卡死（已复现 2 次）。
          游戏端一次报出**全部**坏 id（T14 的立意；2026-09-25 起不再截断到前 8 条），
          于是解析消息里的组号就能判定整包的 good / bad，不需要二分。
          消息里的总数 N 与解析出的组号数**必须相等**，否则直接报错退出 ——
          「把 busy 当成兵种不存在」正是上一版自写脚本的 bug，不能让同类静默误判再发生。

  cover   用 probe 得到的 good 清单分批跑真战斗（每场每方 ≤150 人、每个 id 1 人），
          等战斗**自然结束**（cap 秒；不主动 abort），再从该场 JSONL 的 `unit` 事件
          取 `troop` 集合与期望集合比对 ⇒ 差集就是「通过校验却没 spawn 出来」的兵种
          （那才是真正的兵种 bug；`unit` 在 OnAgentBuild 对每个 agent 写一行，不受采样限制）。

用法：
  python tools/bl_troop_sweep.py probe  [--ids-file IDS.json] [--chunk 800] [--out PROBE.json]
  python tools/bl_troop_sweep.py cover  [--good-file PROBE.json] [--per-batch 300] [--out COVER.json]
  两个子命令都支持 --dry-run（只打印计划，不碰游戏）。

前置：游戏在跑，且停在自定义战斗选兵界面（CustomBattleState）—— 与 bl_cmd.py 同一要求。

输出编码：与其它 tools 同一口径 —— 走 UTF-8（见 `bl_common.safe_streams`）。
  消费端是调用这些工具的 AI / 管道（实测 [Console]::OutputEncoding = utf-8），
  所以中文文案照常可读；`errors="replace"` 只兜底极端字符。
"""

import argparse
import json
import os
import re
import sys
import time

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TOOLS_DIR)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import bl_common   # noqa: E402
import bl_mcp      # noqa: E402

# 上一轮扫描的现场目录（.sdd/ 不入库，只在本地存在）。
DEFAULT_WORK = os.path.join(REPO_ROOT, ".sdd", "2026-09-24-multitroop-tactics-plan")
DEFAULT_IDS = os.path.join(DEFAULT_WORK, "sweep_expected_ids.json")
DEFAULT_PROBE_OUT = os.path.join(DEFAULT_WORK, "sweep_probe_full.json")
DEFAULT_COVER_OUT = os.path.join(DEFAULT_WORK, "sweep_coverage.json")

# 引信：实测游戏端不认（2026-09-25 记录在案），拿它保证 probe 的每次 start 都以校验失败收场。
# 注意它**只当引信**，判定完从结果里剔除，绝不混进 bad 清单。
DECOY_DEFAULT = "tutorial_npc_basic_melee"

_RE_N = re.compile(r"有\s*(\d+)\s*个兵种 id 不存在")
_RE_GROUP = re.compile(r"第\s*(\d+)\s*组")


def _load_json_list(path, what):
    if not os.path.isfile(path):
        raise SystemExit("找不到%s：%s" % (what, path))
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise SystemExit("%s必须是字符串数组：%s" % (what, path))
    return data


def load_good(path):
    """probe 的产出（{"good": [...]}）或裸数组，两种都收。"""
    if not os.path.isfile(path):
        raise SystemExit("找不到 good 清单：%s" % path)
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        good = data.get("good")
        if not isinstance(good, list):
            raise SystemExit('good 清单里没有 "good" 数组：%s' % path)
        return [str(x) for x in good]
    if isinstance(data, list):
        return [str(x) for x in data]
    raise SystemExit('good 清单格式无法识别（要 {"good": [...]} 或 [...]）：%s' % path)


def dsl(ids):
    """id 列表 → 组 DSL（每 id 1 人）。"""
    return "|".join("%s:1" % t for t in ids)


def ping():
    """连通性/会话身份；游戏没在跑时给出可执行的提示，而不是让人对着超时猜。"""
    resp = bl_mcp.send_command("ping", {}, timeout=10)
    if not resp.get("ok"):
        raise SystemExit("ping 失败（游戏在跑吗？BlBridge 模块加载了吗？）：%s"
                         % str(resp.get("error"))[:200])
    return resp


def wait_state(target, timeout, poll=2.0, what=""):
    """等 state == target。超时把**当前状态**带出来（loading 卡死一眼可辨）。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = bl_mcp.send_command("status", {}, timeout=10)
        res = resp.get("result") or {}
        last = res.get("state")
        if last == target:
            return last
        time.sleep(poll)
    raise SystemExit("%s等待 state=%s 超时（当前 %s）——若一直是 loading，"
                     "就是已知的「abort 拉不回 loading」现象，请重启游戏"
                     % (what, target, last))


def parse_unknown_troop(msg):
    """从 unknown_troop 消息里取出（坏 id 总数, 组号列表）。

    组号是**该请求内 1 基的组序号**（攻击方）。总数与组号数不等 ⇒ 消息格式变了或被截断，
    一律报错退出（GC3：宁可停下，也不能拿半个清单当全清单用）。
    """
    m = _RE_N.search(msg or "")
    if not m:
        raise SystemExit("unknown_troop 消息里读不到坏 id 总数：%r"
                         % (msg or "")[:200])
    n = int(m.group(1))
    groups = [int(g) for g in _RE_GROUP.findall(msg)]
    if len(groups) != n:
        raise SystemExit("消息说 %d 个坏 id，只解析出 %d 组（消息被截断或格式变了）"
                         "——不静默降级，请检查游戏端 T14 消息是否仍是「报全部」" % (n, len(groups)))
    return n, groups


def write_json(path, obj):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("已写出 %s" % path)


# ── ① 可用性判定 ───────────────────────────────────────────────────────
def cmd_probe(args):
    ids = _load_json_list(args.ids_file, "ids file")
    chunks = [ids[i:i + args.chunk] for i in range(0, len(ids), args.chunk)]
    print("id 清单 %d 个 → 分 %d 包（每包 %d）；引信 %s"
          % (len(ids), len(chunks), args.chunk, args.decoy))
    if not args.dry_run:
        ping()

    good, bad, records = [], [], []
    for idx, chunk in enumerate(chunks, 1):
        label = "probe_%02d" % idx
        troops = list(chunk) + [args.decoy]
        params = {
            "attackerTroop": args.decoy, "attackerCount": 1,
            "defenderTroop": args.decoy, "defenderCount": 1,
            "scene": args.scene, "durationCapSec": 20, "orders": "charge",
            "playerSide": "attacker", "allowAnyState": "false",
            "attackerGroups": dsl(troops),
        }
        if args.dry_run:
            print("  [%s] %d 组（含引信）：%s..." % (label, len(troops), dsl(troops)[:80]))
            continue

        t0 = time.time()
        resp = bl_mcp.send_command("start", params, timeout=args.timeout)
        err = resp.get("error") or {}
        if resp.get("ok"):
            # 引信没生效 ⇒ 这条会话的判定前提不成立，且已经建了 mission。报错并停止，不硬跑下去。
            bl_mcp.send_command("abort", {}, timeout=20)
            raise SystemExit("[%s] start 竟然成功（引信 %s 未生效）——已发 abort；"
                             "请确认引信仍是游戏端不认的 id" % (label, args.decoy))
        if err.get("code") != "unknown_troop":
            raise SystemExit("[%s] 返回意外错误 %s：%s"
                             % (label, err.get("code"), str(err.get("message"))[:200]))

        n, groups = parse_unknown_troop(err.get("message"))
        chunk_bad = []
        for g in groups:
            if g < 1 or g > len(troops):
                raise SystemExit("[%s] 组号 %d 越界（本包 %d 组）"
                                 % (label, g, len(troops)))
            chunk_bad.append(troops[g - 1])
        if args.decoy not in chunk_bad:
            raise SystemExit("[%s] 坏 id 列表里没有引信 %s（组号错位？）"
                             % (label, args.decoy))
        chunk_bad = [x for x in chunk_bad if x != args.decoy]
        badset = set(chunk_bad)
        chunk_good = [t for t in chunk if t not in badset]

        good += chunk_good
        bad += chunk_bad
        records.append({"label": label, "size": len(chunk), "badCount": len(chunk_bad),
                        "bad": chunk_bad, "seconds": round(time.time() - t0, 2)})
        print("  [%s] %d 个 id：坏 %d / 好 %d（%.1fs）"
              % (label, len(chunk), len(chunk_bad), len(chunk_good), time.time() - t0))

    if args.dry_run:
        return 0
    print("合计：好 %d / 坏 %d（总 %d）" % (len(good), len(bad), len(good) + len(bad)))
    write_json(args.out, {"idsFile": args.ids_file, "chunk": args.chunk, "decoy": args.decoy,
                          "good": good, "bad": bad, "chunks": records})
    return 0


# ── ② 覆盖扫描 ─────────────────────────────────────────────────────────
def battle_since(ts):
    """取 mtime >= ts 的最新一场 jsonl（start 之后新落盘的那份）。"""
    rows = [r for r in bl_common.list_battles() if r["mtime"] >= ts - 1]
    return rows[-1]["file"] if rows else None


def cmd_cover(args):
    good = load_good(args.good_file)
    batches = [good[i:i + args.per_batch] for i in range(0, len(good), args.per_batch)]
    print("good 清单 %d 个 → 分 %d 场（每场 %d 个 id，每方一半、每 id 1 人）"
          % (len(good), len(batches), args.per_batch))
    if not args.dry_run:
        ping()

    records, all_missing, all_extra = [], [], []
    for idx, batch in enumerate(batches, 1):
        label = "cover_%02d" % idx
        half = (len(batch) + 1) // 2
        atk, dfd = batch[:half], batch[half:]
        if not dfd:
            raise SystemExit("[%s] 本批只有 1 个 id：无法两侧分组"
                             "（--per-batch 至少 2）" % label)
        params = {
            "attackerTroop": atk[0], "attackerCount": len(atk),
            "defenderTroop": dfd[0], "defenderCount": len(dfd),
            "scene": args.scene, "durationCapSec": args.cap, "orders": "charge",
            "playerSide": "attacker", "allowAnyState": "false",
            "attackerGroups": dsl(atk), "defenderGroups": dsl(dfd),
        }
        if args.dry_run:
            print("  [%s] 攻 %d 组 / 守 %d 组：%s..." % (label, len(atk), len(dfd), dsl(atk)[:60]))
            continue

        t0 = time.time()
        resp = bl_mcp.send_command("start", params, timeout=args.timeout)
        if not resp.get("ok"):
            err = resp.get("error") or {}
            records.append({"label": label, "ids": batch, "error": err.get("code"),
                            "message": str(err.get("message"))[:300]})
            print("  [%s] start 失败：%s（本批 %d 个 id 未判定，多半是 good 清单里混进了坏 id）"
                  % (label, err.get("code"), len(batch)))
            continue

        wait_state("ended", args.wait_timeout, what="[%s] " % label)
        wait_state("idle", args.idle_timeout, what="[%s] " % label)

        path = battle_since(t0)
        if not path:
            raise SystemExit("[%s] 战斗结束但找不到新日志（%s）"
                             % (label, bl_common.battles_dir()))
        troops = set(e.get("troop") for e in bl_common.load_events(path) if e.get("t") == "unit")
        expected = set(batch)
        missing = sorted(expected - troops)
        extra = sorted(troops - expected)
        all_missing += missing
        all_extra += extra
        records.append({"label": label, "ids": batch, "file": path,
                        "expected": len(expected), "units": len(troops),
                        "missing": missing, "extra": extra,
                        "seconds": round(time.time() - t0, 1)})
        print("  [%s] 期望 %d / 实际 unit 兵种 %d：缺 %d、多 %d（%.1fs）%s"
              % (label, len(expected), len(troops), len(missing), len(extra),
                 time.time() - t0, os.path.basename(path)))

    if args.dry_run:
        return 0
    print("覆盖缺口合计 %d 个：%s" % (len(all_missing), ", ".join(all_missing[:20]) or "（无）"))
    write_json(args.out, {"goodFile": args.good_file, "perBatch": args.per_batch, "capSec": args.cap,
                          "scene": args.scene, "missing": all_missing, "extra": all_extra,
                          "batches": records})
    return 0


def main(argv):
    bl_common.safe_streams()
    ap = argparse.ArgumentParser(
        description="全兵种扫描（可用性判定 + 覆盖扫描）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("probe", help="可用性判定：分包校验，拿完整坏 id 清单")
    p.add_argument("--ids-file", default=DEFAULT_IDS, help="id 清单 JSON（字符串数组）")
    p.add_argument("--chunk", type=int, default=800, help="每包 id 数（默认 800，与上一轮分块一致）")
    p.add_argument("--decoy", default=DECOY_DEFAULT, help="引信 id（游戏端不认；保证 start 必失败）")
    p.add_argument("--scene", default="battle_terrain_a")
    p.add_argument("--timeout", type=float, default=60.0, help="单条命令超时（秒）")
    p.add_argument("--out", default=DEFAULT_PROBE_OUT)
    p.add_argument("--dry-run", action="store_true", help="只打印分包计划，不碰游戏")

    c = sub.add_parser("cover", help="覆盖扫描：跑真战斗，比对 unit 事件找没 spawn 的兵种")
    c.add_argument("--good-file", default=DEFAULT_PROBE_OUT, help="probe 的产出（或裸 id 数组）")
    c.add_argument("--per-batch", type=int, default=300, help="每场可容纳的 id 数（默认 300 = 每方 150）")
    c.add_argument("--cap", type=int, default=20, help="单场时长上限（游戏内秒；自动结束，不 abort）")
    c.add_argument("--scene", default="battle_terrain_a")
    c.add_argument("--timeout", type=float, default=120.0, help="start 命令超时（秒）")
    c.add_argument("--wait-timeout", type=float, default=180.0, help="等 state=ended 的超时（秒）")
    c.add_argument("--idle-timeout", type=float, default=120.0, help="等 state=idle 的超时（秒）")
    c.add_argument("--out", default=DEFAULT_COVER_OUT)
    c.add_argument("--dry-run", action="store_true", help="只打印分场计划，不碰游戏")

    args = ap.parse_args(argv)
    if args.cmd == "probe":
        return cmd_probe(args)
    if args.cmd == "cover":
        return cmd_cover(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
