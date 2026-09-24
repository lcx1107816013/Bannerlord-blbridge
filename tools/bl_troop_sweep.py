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

输出编码（刻意为之，2026-09-25）：**面向人的输出一律 ASCII**。
  同目录其它工具输出中文，靠 `bl_common.safe_streams()`（只改 errors 不改 encoding）在
  chcp 936 的 GBK 控制台下可读；但那条口径一离开 GBK 终端就乱码——
  PowerShell 7 / Windows Terminal 的 `[Console]::OutputEncoding` 默认是 utf-8，
  重定向/CI/别的 agent 捕获时也一样（GBK 字节被按 UTF-8 解码 ⇒ 满屏 U+FFFD）。
  本工具不赌终端编码：**人类可读文本全 ASCII**，中文只留在注释与文档里，
  于是 `chcp` / `PYTHONIOENCODING` / 重定向都不影响可读性（id 与文件名本就是 ASCII）。
  仍保留 safe_streams() 兜底：日志目录路径里若出现非 ASCII 字符，也不会让整个 CLI 崩掉。
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
        raise SystemExit("missing %s: %s" % (what, path))
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise SystemExit("%s must be a JSON array of strings: %s" % (what, path))
    return data


def load_good(path):
    """probe 的产出（{"good": [...]}）或裸数组，两种都收。"""
    if not os.path.isfile(path):
        raise SystemExit("missing good list: %s" % path)
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        good = data.get("good")
        if not isinstance(good, list):
            raise SystemExit('good list has no "good" array: %s' % path)
        return [str(x) for x in good]
    if isinstance(data, list):
        return [str(x) for x in data]
    raise SystemExit('unrecognized good list format (want {"good": [...]} or [...]): %s' % path)


def dsl(ids):
    """id 列表 → 组 DSL（每 id 1 人）。"""
    return "|".join("%s:1" % t for t in ids)


def ping():
    """连通性/会话身份；游戏没在跑时给出可执行的提示，而不是让人对着超时猜。"""
    resp = bl_mcp.send_command("ping", {}, timeout=10)
    if not resp.get("ok"):
        raise SystemExit("ping failed (is the game running with BlBridge loaded?): %s"
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
    raise SystemExit("%stimeout waiting for state=%s (now %s). If it stays 'loading', that is the "
                     "known 'abort cannot pull back from loading' bug -- restart the game"
                     % (what, target, last))


def parse_unknown_troop(msg):
    """从 unknown_troop 消息里取出（坏 id 总数, 组号列表）。

    组号是**该请求内 1 基的组序号**（攻击方）。总数与组号数不等 ⇒ 消息格式变了或被截断，
    一律报错退出（GC3：宁可停下，也不能拿半个清单当全清单用）。
    """
    m = _RE_N.search(msg or "")
    if not m:
        raise SystemExit("cannot read the bad-id count from the unknown_troop message: %r"
                         % (msg or "")[:200])
    n = int(m.group(1))
    groups = [int(g) for g in _RE_GROUP.findall(msg)]
    if len(groups) != n:
        raise SystemExit("message says %d bad ids but only %d groups were parsed "
                         "(truncated message or changed format) -- refusing to degrade silently; "
                         "check that the game-side T14 message still lists them all" % (n, len(groups)))
    return n, groups


def write_json(path, obj):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("wrote %s" % path)


# ── ① 可用性判定 ───────────────────────────────────────────────────────
def cmd_probe(args):
    ids = _load_json_list(args.ids_file, "ids file")
    chunks = [ids[i:i + args.chunk] for i in range(0, len(ids), args.chunk)]
    print("ids=%d chunks=%d (chunk=%d) decoy=%s"
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
            print("  [%s] groups=%d (decoy included): %s..." % (label, len(troops), dsl(troops)[:80]))
            continue

        t0 = time.time()
        resp = bl_mcp.send_command("start", params, timeout=args.timeout)
        err = resp.get("error") or {}
        if resp.get("ok"):
            # 引信没生效 ⇒ 这条会话的判定前提不成立，且已经建了 mission。报错并停止，不硬跑下去。
            bl_mcp.send_command("abort", {}, timeout=20)
            raise SystemExit("[%s] start unexpectedly succeeded (decoy %s did not fire) -- abort sent; "
                             "check that the decoy is still an unknown id" % (label, args.decoy))
        if err.get("code") != "unknown_troop":
            raise SystemExit("[%s] unexpected error %s: %s"
                             % (label, err.get("code"), str(err.get("message"))[:200]))

        n, groups = parse_unknown_troop(err.get("message"))
        chunk_bad = []
        for g in groups:
            if g < 1 or g > len(troops):
                raise SystemExit("[%s] group index %d out of range (%d groups in this chunk)"
                                 % (label, g, len(troops)))
            chunk_bad.append(troops[g - 1])
        if args.decoy not in chunk_bad:
            raise SystemExit("[%s] decoy %s is missing from the bad list (group indices shifted?)"
                             % (label, args.decoy))
        chunk_bad = [x for x in chunk_bad if x != args.decoy]
        badset = set(chunk_bad)
        chunk_good = [t for t in chunk if t not in badset]

        good += chunk_good
        bad += chunk_bad
        records.append({"label": label, "size": len(chunk), "badCount": len(chunk_bad),
                        "bad": chunk_bad, "seconds": round(time.time() - t0, 2)})
        print("  [%s] ids=%d bad=%d good=%d (%.1fs)"
              % (label, len(chunk), len(chunk_bad), len(chunk_good), time.time() - t0))

    if args.dry_run:
        return 0
    print("TOTAL good=%d bad=%d (all=%d)" % (len(good), len(bad), len(good) + len(bad)))
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
    print("good=%d batches=%d (per-batch=%d; half per side, 1 man per id)"
          % (len(good), len(batches), args.per_batch))
    if not args.dry_run:
        ping()

    records, all_missing, all_extra = [], [], []
    for idx, batch in enumerate(batches, 1):
        label = "cover_%02d" % idx
        half = (len(batch) + 1) // 2
        atk, dfd = batch[:half], batch[half:]
        if not dfd:
            raise SystemExit("[%s] only 1 id in this batch: cannot split both sides "
                             "(--per-batch must be >= 2)" % label)
        params = {
            "attackerTroop": atk[0], "attackerCount": len(atk),
            "defenderTroop": dfd[0], "defenderCount": len(dfd),
            "scene": args.scene, "durationCapSec": args.cap, "orders": "charge",
            "playerSide": "attacker", "allowAnyState": "false",
            "attackerGroups": dsl(atk), "defenderGroups": dsl(dfd),
        }
        if args.dry_run:
            print("  [%s] attacker=%d defender=%d: %s..." % (label, len(atk), len(dfd), dsl(atk)[:60]))
            continue

        t0 = time.time()
        resp = bl_mcp.send_command("start", params, timeout=args.timeout)
        if not resp.get("ok"):
            err = resp.get("error") or {}
            records.append({"label": label, "ids": batch, "error": err.get("code"),
                            "message": str(err.get("message"))[:300]})
            print("  [%s] start failed: %s (%d ids left undecided -- the good list probably "
                  "still contains a bad id)" % (label, err.get("code"), len(batch)))
            continue

        wait_state("ended", args.wait_timeout, what="[%s] " % label)
        wait_state("idle", args.idle_timeout, what="[%s] " % label)

        path = battle_since(t0)
        if not path:
            raise SystemExit("[%s] battle ended but no new log found in %s"
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
        print("  [%s] expected=%d units=%d missing=%d extra=%d (%.1fs) %s"
              % (label, len(expected), len(troops), len(missing), len(extra),
                 time.time() - t0, os.path.basename(path)))

    if args.dry_run:
        return 0
    print("MISSING total=%d: %s" % (len(all_missing), ", ".join(all_missing[:20]) or "(none)"))
    write_json(args.out, {"goodFile": args.good_file, "perBatch": args.per_batch, "capSec": args.cap,
                          "scene": args.scene, "missing": all_missing, "extra": all_extra,
                          "batches": records})
    return 0


def main(argv):
    bl_common.safe_streams()
    ap = argparse.ArgumentParser(
        description="Troop sweep: availability probe + spawn coverage scan (output is ASCII on purpose)")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("probe", help="availability: chunked validation, yields the full bad-id list")
    p.add_argument("--ids-file", default=DEFAULT_IDS, help="ids JSON (array of strings)")
    p.add_argument("--chunk", type=int, default=800, help="ids per chunk (default 800, same as last round)")
    p.add_argument("--decoy", default=DECOY_DEFAULT, help="decoy id the game rejects (keeps start failing)")
    p.add_argument("--scene", default="battle_terrain_a")
    p.add_argument("--timeout", type=float, default=60.0, help="per-command timeout (sec)")
    p.add_argument("--out", default=DEFAULT_PROBE_OUT)
    p.add_argument("--dry-run", action="store_true", help="print the chunk plan only, do not touch the game")

    c = sub.add_parser("cover", help="coverage: run real battles, diff unit events for unspawned troops")
    c.add_argument("--good-file", default=DEFAULT_PROBE_OUT, help="probe output (or a bare id array)")
    c.add_argument("--per-batch", type=int, default=300, help="ids per battle (default 300 = 150 per side)")
    c.add_argument("--cap", type=int, default=20, help="battle time cap in game seconds (auto-end, no abort)")
    c.add_argument("--scene", default="battle_terrain_a")
    c.add_argument("--timeout", type=float, default=120.0, help="start command timeout (sec)")
    c.add_argument("--wait-timeout", type=float, default=180.0, help="timeout waiting for state=ended (sec)")
    c.add_argument("--idle-timeout", type=float, default=120.0, help="timeout waiting for state=idle (sec)")
    c.add_argument("--out", default=DEFAULT_COVER_OUT)
    c.add_argument("--dry-run", action="store_true", help="print the batch plan only, do not touch the game")

    args = ap.parse_args(argv)
    if args.cmd == "probe":
        return cmd_probe(args)
    if args.cmd == "cover":
        return cmd_cover(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
