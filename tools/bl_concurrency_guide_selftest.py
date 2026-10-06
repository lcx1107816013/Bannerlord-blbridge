#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
`bl_concurrency_guide` 自测（离线：不需要游戏、不需要真账本）。

## 覆盖
  ① **闭包边界**：`bl_launch_game` 必须含 `_enter_custom_battle` 里的通道方法，
     **但不能**因 `_enter_custom_battle` 回调 `call_tool` 而把全部工具的方法收进来。
     —— 这一条同时锁住我踩过的**两个相反方向的错**：
        初版只看直接调用 ⇒ 误判 HOST（太浅）；
        加闭包不设边界 ⇒ 列出全部 31 个方法（太深）。
  ② 宿主侧工具必须**不含任何通道方法**（launch_game 不能混进去）。
  ③ `UNKNOWN`（走通道但无实测）与 `HOST`（不碰通道）**必须分开** ——
     前者有争用风险、后者零风险，混作一谈会给出错误的"可无限并发"建议。
  ④ **分档用 p90 而非中位数**：构造一个"双峰"方法（p50 极小、p90 极大），
     必须被判成 HEAVY（真实缺陷现场：`skip_video` p50=1ms / p90=459ms）。
  ⑤ `budget` 判据：`N × p90 ≤ 250ms`（撑不破轮询周期）。
  ⑥ 目录不存在 / 账本缺失时**如实报**，不伪装成"没有重工具"。
"""
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bl_common                  # noqa: E402
import bl_concurrency_guide as G  # noqa: E402

CASE = os.path.join(HERE, "_blcg_case")
_fail = []


def check(cond, name, detail=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name
          + ("" if cond else "  <- " + str(detail)))
    if not cond:
        _fail.append(name)


def test_closure_boundary():
    print("\n① 闭包边界（两个相反方向的错都要锁住）")
    tmap = G.tool_method_map()
    check(tmap is not None, "能解析出工具→方法映射", tmap)
    if not tmap:
        return

    lg = tmap.get("bl_launch_game")
    check(lg is not None, "bl_launch_game 在映射里", list(tmap)[:5])
    if lg is None:
        return
    # 太浅的错：它必须**不是**空（它经 _enter_custom_battle 走通道）
    check(bool(lg), "bl_launch_game **不是**宿主侧（闭包追到了间接调用）", lg)
    # 太深的错：它**不能**含"只有别的工具才用"的方法
    for forbidden in ("start_battle", "get_patches", "load_save", "campaign_overview"):
        check(forbidden not in lg,
              "bl_launch_game **不含** %s（未跨 call_tool 派发边界放大）" % forbidden, lg)
    # 它应该有的那几个
    for want in ("list_ui", "open_ui"):
        check(want in lg, "bl_launch_game 含 %s（经 _enter_custom_battle）" % want, lg)

    # ② 宿主侧不含任何通道方法
    host = [t for t, ms in tmap.items() if not ms]
    check(len(host) >= 15, "宿主侧数量合理（实测 22）", len(host))
    for t in ("bl_status", "bl_crash", "bl_json_health", "bl_analyze"):
        check(t in host, "%s 是宿主侧" % t, tmap.get(t))
    check("bl_launch_game" not in host, "bl_launch_game **不在**宿主侧（曾误判）")


def _write_case(rows):
    """造一个合成日志目录：commands/actions.jsonl。"""
    if os.path.isdir(CASE):
        shutil.rmtree(CASE, ignore_errors=True)
    os.makedirs(os.path.join(CASE, "commands"))
    p = os.path.join(CASE, "commands", "actions.jsonl")
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return CASE


def test_p90_not_median():
    print("\n④ 分档用 p90 而非中位数（锁住 skip_video 双峰的真实缺陷）")
    # 造一个双峰方法：48 次 1ms、12 次 459ms ⇒ p50≈1ms 但 p90≈459ms
    rows = []
    for i in range(48):
        rows.append({"method": "bimodal", "ms": 1.0, "ok": True})
    for i in range(12):
        rows.append({"method": "bimodal", "ms": 459.0, "ok": True})
    _write_case(rows)
    stats, meta = G.method_stats(CASE)
    check(meta["exists"] is True, "读到合成账本", meta)
    bim = stats.get("bimodal")
    check(bim is not None, "统计到 bimodal", list(stats))
    if not bim:
        return
    print("       n=%d p50=%.1f p90=%.1f max=%.1f" % (bim["n"], bim["p50"], bim["p90"], bim["max"]))
    check(bim["p50"] < 20, "中位数确实很小（%.1f）—— 只看它会误判成轻工具" % bim["p50"])
    check(bim["p90"] >= 100, "p90 很大（%.1f）" % bim["p90"])
    check(G.classify(bim["p90"], bim["n"]) == "HEAVY",
          "按 p90 判成 HEAVY（若用中位数会误判成 LIGHT）", G.classify(bim["p90"], bim["n"]))


def test_budget():
    print("\n⑤ budget 判据 N × p90 <= 250ms")
    check(G.concurrency_budget(1.0) == 64, "p90=1ms => 上限 64（亚毫秒级不受限）",
          G.concurrency_budget(1.0))
    check(G.concurrency_budget(250.0) == 1, "p90=250ms => 上限 1", G.concurrency_budget(250.0))
    b = G.concurrency_budget(25.0)
    check(b == 8, "p90=25ms => 上限 8（8×25=200 ≤ 200=250×0.8）", b)
    check(G.concurrency_budget(0.0) == 64, "p90=0 => 64（除零保护）", G.concurrency_budget(0.0))
    # 判据本身：N×p90 不得超过 250
    for p90 in (5.0, 12.5, 30.0, 60.0):
        n = G.concurrency_budget(p90)
        check(n * p90 <= G.POLL_INTERVAL_MS,
              "budget(%s)=%d => %d×%.1f=%.1f <= 250" % (p90, n, n, p90, n * p90))


def test_unknown_vs_host():
    print("\n③ UNKNOWN（走通道无实测）与 HOST（不碰通道）必须分开")
    # 合成账本里只有 bimodal，其余通道方法都没数据
    _write_case([{"method": "bimodal", "ms": 5.0, "ok": True}])
    res = G.build_report(log_dir=CASE)
    check(res["ok"] is True, "build_report ok", res.get("reason"))
    kinds = {}
    for t in res["tools"]:
        kinds[t["tool"]] = t["kind"]
    # bl_launch_game 走通道（list_ui/open_ui/skip_video），但合成账本里没有它们的耗时
    check(kinds.get("bl_launch_game") == "UNKNOWN",
          "bl_launch_game 无实测 => UNKNOWN（**不是 HOST**）", kinds.get("bl_launch_game"))
    check(kinds.get("bl_status") == "HOST", "bl_status => HOST", kinds.get("bl_status"))
    # budget 显示：HOST 给 ∞，UNKNOWN **不给** ∞
    rep = res["report"]
    check("∞" in rep, "报告里 HOST 显示 ∞")
    check("<=4?" in rep, "报告里 UNKNOWN 显示 <=4?（保守，**不给 ∞**）")
    check("走通道但**无实测数据**" in rep or "无实测" in rep,
          "报告里点明 UNKNOWN 的原因")


def test_missing_ledger():
    print("\n⑥ 账本缺失时如实报，不伪装成「没有重工具」")
    nodir = os.path.join(HERE, "_blcg_nodir")
    if os.path.isdir(nodir):
        shutil.rmtree(nodir, ignore_errors=True)
    stats, meta = G.method_stats(nodir)
    check(meta["exists"] is False, "meta.exists=False", meta)
    check(stats == {}, "无数据 => 空统计（不伪造）", stats)
    res = G.build_report(log_dir=nodir)
    check(res["ok"] is True, "仍能出报告（映射来自源码，不依赖账本）", res.get("ok"))
    # 所有通道工具都应变 UNKNOWN，而不是 HOST
    ch = [t for t in res["tools"] if t["kind"] == "UNKNOWN"]
    check(len(ch) >= 20, "通道工具在无账本时全为 UNKNOWN（%d 个）" % len(ch), len(ch))
    check("账本行数  : 0" in res["report"] or "账本行数  : None" in res["report"],
          "报告如实报账本为空")


def main():
    bl_common.safe_streams()
    print("=" * 88)
    print("bl_concurrency_guide 自测（离线）")
    print("=" * 88)
    try:
        test_closure_boundary()
        test_p90_not_median()
        test_budget()
        test_unknown_vs_host()
        test_missing_ledger()
    finally:
        shutil.rmtree(CASE, ignore_errors=True)
        shutil.rmtree(os.path.join(HERE, "_blcg_nodir"), ignore_errors=True)
    print()
    print("=" * 88)
    if _fail:
        print("结果: 失败 %d 项" % len(_fail))
        for f in _fail:
            print("  - %s" % f)
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
