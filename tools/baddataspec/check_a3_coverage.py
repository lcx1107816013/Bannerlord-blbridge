#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A3 真机判据：`bl_scan_bad_data` 的 `coverage` 到底有没有"真的扫到了多队伍/多聚落"。

## 为什么需要它（而不是"人眼看一眼返回"）

A3 的交付是**扫描范围**从"只扫玩家队伍"扩到**全队伍 + 全聚落**（含 `Settlement.Stash`）。
这种"范围类"改动**最容易的失效方式**是**恒返回一个看起来合理的数字**：
容器名写错（实测 `Campaign.Armies` 就不存在）、字段名写错、或只扫了玩家队伍 ——
**三种情况都报 `ok:true`**，而 `scanned.items` 还可能是**非 0**（玩家自己那一个 roster 就有条目）。
⇒ 只看"有没有扫到东西"**证明不了**范围扩大了。必须看**覆盖口径**（`coverage`）。

## 判据（三值，缺一不可；★ "物品 0 条"**不是**失败）

1. **覆盖口径存在**：`coverage` 里含 `item:` 那一条（04:04:52 版起叫 `coverage`；更早的窗口叫 `itemScope`）。
2. ★ **N ≥ 3 且 ≥ 2 个非 `[玩家]` 的 roster** —— 这是 A3 的**核心判据**：
   只有玩家队伍时 N=1（或 2：玩家 + 玩家聚落）；跨过 ≥3 且含非玩家条目，
   才证明"全队伍/全聚落"真的生效。
3. **范围扩大的直接证据**：`coverage` 的 roster 名清单里出现**非玩家**的 `party:`，
   或 `settlement:` / `stash:` 前缀。
   （★ 刻意**不**做"`scanned.items` 必须大于某条基线"那条判据：玩家队伍自己那一个 roster
    就可能有很多条目，而"只扫玩家"与"扫了全部"在**数量**上并不单调可分 —— 见
    `AGENTS.md` §四"数量类结论必须给可复算的分段分解"。所以判据落在**名册清单**上，
    它是范围问题的**直接**证据，不依赖任何基线数字。）

★ **不得**把 `found` / `broken` = 0 当作失败 —— 干净存档本来就该 0 条（这正是
   `docs/bad-data-scan-and-clean.md` 里那条"别把 0 条读成很干净"的反面）。
★ **不得**把 `skipped` 非空当作失败 —— `skipped` 是"某类没扫成"的**异常**通道，
   与 `coverage`（正常覆盖）**语义不同**；两者混读是 A3 第一版犯过的错。

## 判据自带对照（★ 本项目硬纪律：没有对照组的验证不是验证）

`★4a/4b/4c` 用**自造样本**校准判据本身（已知为真的样本必绿、抽掉输入必红、
"只扫玩家"的样本必红）—— 且**与本次返回无关**，因此无论本机返回什么都能判定。
（第一版把对照写成"把**本次**返回的 coverage 抽掉再看"，那是错的：当本次返回本来就没有
 coverage 时，"抽掉后不存在"与"抽掉前也不存在"无法区分 ⇒ 对照自己被输入污染。）

## 用法

    python tools\baddataspec\check_a3_coverage.py            # 游戏须在跑（走整条 MCP 链）
    python tools\baddataspec\check_a3_coverage.py --json <path>   # 或直接吃一份已存下的返回

退出码：0 = 判据全过；1 = 有 FAIL；2 = 环境不足（游戏没跑 / 拿不到返回 —— **不算通过**）。
"""
import argparse
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))

FAILS = []
CHECKS = [0]


def check(cond, label, extra=""):
    """`extra` 只在**失败**时打印 —— 它写的是"为什么可能是错的"，成功时显示会误导读者
    （第一版就把它无条件打出来，于是 `[OK]` 行后面跟着"没有 coverage…"这样的反话）。"""
    CHECKS[0] += 1
    print(("  [OK]   " if cond else "  [FAIL] ") + label
          + (("  <- " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        FAILS.append(label)


def extract_coverage(payload):
    """从返回里取覆盖条目列表。兼容字段名 `coverage`（现役）与 `itemScope`（旧窗口）。"""
    for key in ("coverage", "itemScope"):
        v = payload.get(key)
        if isinstance(v, list) and v:
            return key, [str(x) for x in v]
    return None, []


def parse_roster_count(cov_entries):
    """从 `item: 共扫 N 个 roster —— ...` 里取 N，并取 roster 名清单。"""
    total = None
    names = []
    for e in cov_entries:
        m = re.search(r"共扫\s*(\d+)\s*个\s*roster", e)
        if m:
            total = int(m.group(1))
        # '——' 之后是 roster 名清单（`party:xxx(3); settlement:yyy(12); stash:zzz(5)`）
        for part in re.split(r"[;；]", e.split("——")[-1] if "——" in e else ""):
            part = part.strip()
            if re.match(r"^(party|settlement|stash|clan|army|quest)[:\[]", part):
                names.append(part)
    return total, names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="直接读一份已保存的 bl_scan_bad_data 返回（不连游戏）")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")     # 见 AGENTS.md §一：不靠控制台代码页
    except Exception:                                 # noqa: BLE001
        pass

    if args.json:
        try:
            payload = json.loads(io.open(args.json, encoding="utf-8-sig").read())
        except Exception as exc:                      # noqa: BLE001
            print("[ENV] 读不了 %s：%r" % (args.json, exc))
            return 2
        print("输入：%s" % args.json)
    else:
        sys.path.insert(0, os.path.join(REPO, "tools"))
        try:
            import bl_mcp
        except Exception as exc:                      # noqa: BLE001
            print("[ENV] 无法 import bl_mcp：%r" % (exc,))
            return 2
        print("输入：真机（走 bl_mcp → 文件 IPC）")
        try:
            ret, err = bl_mcp.send_command("scan_bad_data", {"limit": 0}, timeout=args.timeout)
        except Exception as exc:                      # noqa: BLE001
            # ★ 契约是"环境不足 ⇒ 2，**不算通过**"，所以这里必须**兜住**异常：
            #   第一版没兜，受限环境（日志目录不可写 PermissionError、游戏侧目录只读）会抛栈，
            #   退出码变成 1 —— 那会被读成"判据失败"，而实际是**根本没跑起来**。
            #   这正是本项目反复防的"归因错位"。
            print("[ENV] 发送请求时就失败了：%r" % (exc,))
            print("      （日志/命令目录不可写、桥没加载 —— 环境不足，不是判据失败）")
            return 2
        if ret is None:
            print("[ENV] 没拿到响应：%s" % err)
            print("      （游戏没在跑 / 桥没加载 / 请求被作废 —— 不是判据失败，是环境不足）")
            return 2
        payload = ret

    print()
    print("返回摘要：")
    for k in ("ok", "scanned", "found", "skipped"):
        if k in payload:
            print("  %-9s = %s" % (k, json.dumps(payload.get(k), ensure_ascii=False)[:160]))
    key, cov = extract_coverage(payload)
    print("  coverage 键名 = %s（%d 条）" % (key, len(cov)))
    for e in cov:
        print("      - %s" % e)
    print()

    # ── 判据 1：覆盖口径存在 ─────────────────────────────────────────────
    check(key is not None, "★1 覆盖口径存在（`coverage`；旧窗口名 `itemScope`）",
          "没有 coverage/itemScope —— 可能跑的是 04:04:52 之前的旧 DLL")

    total, names = parse_roster_count(cov)
    check(total is not None, "★1b 覆盖条目里有可复算的 roster 计数（`共扫 N 个 roster`）",
          "coverage=%r" % (cov[:1],))

    non_player = [n for n in names if "[玩家]" not in n]

    # ── 判据 2：N ≥ 3 且 ≥ 2 个非玩家 roster（A3 核心）──────────────────
    check(total is not None and total >= 3,
          "★★2 roster 总数 N ≥ 3（只扫玩家队伍时 N=1）", "N=%r" % (total,))
    check(len(non_player) >= 2,
          "★★2b 至少 2 个**非玩家** roster（证明'全队伍 + 全聚落'真的生效，不是只扫玩家）",
          "非玩家 roster=%d，例：%s" % (len(non_player), non_player[:3]))

    # ── 判据 3：范围扩大的**直接证据**（名册前缀，不依赖任何基线数字）──────
    scanned = payload.get("scanned") or {}
    items = scanned.get("items") if isinstance(scanned, dict) else None
    has_party_or_settle = any(("party:" in n and "[玩家]" not in n) or "settlement:" in n or "stash:" in n
                              for n in names)
    check(has_party_or_settle,
          "★3 覆盖清单里出现非玩家 `party:` 或 `settlement:` / `stash:` 前缀（范围扩大的直接证据）",
          "names=%s" % (names[:4],))
    print("      （参考）scanned.items = %s —— ★ '物品 0 条'**不是**失败，干净存档本该 0" % (items,))

    # ── 反向对照：判据必须真的在判定（用**自造样本**，不依赖本次返回）────────
    # ★ 第一版我写成"把本次返回的 coverage 抽掉再看判据1"，那是错的：
    #   当本次返回**本来就没有** coverage（旧 DLL）时，"抽掉后不存在"这个条件
    #   与"抽掉前也不存在"无法区分 ⇒ 反向对照自己被输入污染（成了恒红/恒绿）。
    #   ⇒ 正确做法：拿一份**已知覆盖良好**的合成样本，抽掉覆盖口径，断言判据**变红**。
    #     这样这条对照与本机的实际返回无关，永远可判定。
    print()
    print("反向对照（证明判据不是恒绿 —— 用自造样本，与本次返回无关）：")
    _syn = {"ok": True, "scanned": {"items": 1918},
            "coverage": ["item: 共扫 1918 个 roster —— party:[玩家]主力(120); party:阿尔法(88); "
                         "settlement:奥齐城(340); stash:奥齐城(210)"]}
    _k_ok, _c_ok = extract_coverage(_syn)
    _t_ok, _n_ok = parse_roster_count(_c_ok)
    check(_k_ok == "coverage" and _t_ok == 1918 and len([n for n in _n_ok if "[玩家]" not in n]) == 3,
          "★4a 正向校准：自造样本（含 3 个非玩家 roster）被判为**通过**（已知为真的样本必绿）",
          "k=%r N=%r" % (_k_ok, _t_ok))
    _stripped = {k: v for k, v in _syn.items() if k != "coverage"}
    _k_no, _c_no = extract_coverage(_stripped)
    _t_no, _ = parse_roster_count(_c_no)
    check(_k_no is None and _t_no is None,
          "★4b 反向：抽掉 coverage 后，判据1/判据2 所依赖的输入**确实消失**（证明它们不是恒绿）",
          "k=%r total=%r" % (_k_no, _t_no))
    _player_only = {"coverage": ["item: 共扫 1 个 roster —— party:[玩家]主力(120)"]}
    _kp, _cp = extract_coverage(_player_only)
    _tp, _np = parse_roster_count(_cp)
    check(_tp == 1 and len([n for n in _np if "[玩家]" not in n]) == 0,
          "★4c 灵敏度：'只扫玩家队伍'的样本 → N=1 且 0 个非玩家 roster（★2/★2b 会红）",
          "N=%r" % (_tp,))

    print()
    print("=" * 78)
    if FAILS:
        print("结论: **失败 %d 项 / 共 %d 项** -> %s" % (len(FAILS), CHECKS[0], FAILS))
        return 1
    print("结论: 全部通过（%d 项）—— A3 的扫描范围在真机上确实扩大到了全队伍 + 全聚落" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
