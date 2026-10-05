#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
`bl_exception_detail` 自测（宿主侧离线：不需要游戏、不需要真 BlBridge 日志）。

## 覆盖（重点是**口径**，不是"能读文件"）
  ① 分段：`t=session` 正确切段（每段 = 一个游戏进程）
  ② **行数 != 频次**：同一键在同一段里出现多次也**只有 1 行**，工具必须**明说**这点
  ③ `seq` 是全局快照 ⇒ **多条记录可共享同一 seq**（工具不许把它当序号）
  ④ `stackHeadIsThrower` 判据**有真值也有假值**（不许恒真/恒假）——
     抛助记帧 ⇒ True（来源不可判定）；真调用者帧 ⇒ False
  ⑤ `t=session` 分段后，**跨段累加 seen 是错的**（工具如实报段数）
  ⑥ 文件不存在时**如实说"不代表没发生异常"**，不伪装成"没异常"
  ⑦ 坏行计数并带原文（不静默）
  ⑧ 结构性盲区（JIT 期 / 原生崩溃抓不到）必须随结果报出

## ! 环境坑
同其它自测：沙箱里 `tempfile.mkdtemp()` 后嵌套建目录会 WinError 5
⇒ 只在 `tools/` 下建**深度 1 的固定名目录**，跑完自删。
"""
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bl_common              # noqa: E402
import bl_exception_detail as E  # noqa: E402

CASE = os.path.join(HERE, "_blexdetail_case")
_fail = []


def check(cond, name, detail=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name
          + ("" if cond else "  <- " + str(detail)))
    if not cond:
        _fail.append(name)


THROWER = ("at System.Security.Cryptography.CryptographicException."
           "ThrowCryptographicException(Int32 hr)")
REAL_CALLER = "at HarmonyLib.PatchTools.GetOriginalMethod(HarmonyMethod attr)"


def setup():
    """造一份**同真实结构**的 exceptions.jsonl：2 段 session + 若干异常行。"""
    if os.path.isdir(CASE):
        shutil.rmtree(CASE, ignore_errors=True)
    os.mkdir(CASE)
    p = os.path.join(CASE, "exceptions.jsonl")
    rows = [
        {"t": "session", "utc": "2026-10-05T19:00:00.000Z", "note": "FirstChance 订阅已装"},
        # 同一 (type|stackHead) 键，出现 3 次也只会有 1 行（这里模拟"只落 1 行"）
        {"t": "exception", "seq": 55, "tick": 4,
         "type": "System.Security.Cryptography.CryptographicException",
         "message": "未指定的错误\r\n", "stackHead": THROWER},
        {"t": "exception", "seq": 55, "tick": 4,
         "type": "System.Reflection.AmbiguousMatchException",
         "message": "Ambiguous match found.", "stackHead": "at System.RuntimeType.GetMethodImpl(...)"},
        {"t": "exception", "seq": 55, "tick": 4,
         "type": "HarmonyLib.HarmonyException",
         "message": "Ambiguous match for HarmonyMethod[(class=A, methodname=B)]",
         "stackHead": REAL_CALLER},
        {"t": "session", "utc": "2026-10-05T20:00:00.000Z", "note": "FirstChance 订阅已装"},
        {"t": "exception", "seq": 58, "tick": 4,
         "type": "System.Security.Cryptography.CryptographicException",
         "message": "未指定的错误\r\n", "stackHead": THROWER},
        "这不是合法 JSON 行（坏行）",
        "[1,2,3]",                       # 合法 JSON 但非对象 —— 也要算坏行
    ]
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(r if isinstance(r, str) else json.dumps(r, ensure_ascii=False))
            fh.write("\n")
    return p


def cleanup():
    shutil.rmtree(CASE, ignore_errors=True)


def test_segments():
    print("\n① 分段（t=session = 一个游戏进程）")
    r = E.build_report(log_dir=CASE, explain=False)
    check(r["sessions"] == 2, "识别出 2 个进程段", r["sessions"])
    check(len(r["segments"]) == 2, "segments 有 2 段", len(r["segments"]))
    check(r["segments"][0]["rows"] == 3, "段 0 有 3 行", r["segments"][0]["rows"])
    check(r["segments"][1]["rows"] == 1, "段 1 有 1 行", r["segments"][1]["rows"])
    # 跨段合计 4 行（不是频次）
    check(r["exceptionRows"] == 4, "异常行合计 4", r["exceptionRows"])


def test_rows_not_counts():
    print("\n② 行数 != 频次 —— 工具必须明说")
    r = E.build_report(log_dir=CASE)
    s = r["semantics"]
    check(s.get("rowsAreNotCounts") is True, "语义里标明 rowsAreNotCounts", s.get("rowsAreNotCounts"))
    check("只在 count==1 时落盘" in (s.get("why") or ""), "说明为什么", s.get("why"))
    check("bl_exceptions" in (s.get("freqSource") or ""),
          "指向真正的频次来源 bl_exceptions", s.get("freqSource"))
    check("行数 != 出现次数" in r["report"], "report 里显式警告行数!=次数")
    # 实测对照写进报告（Cryptographic 4 行 / 真实 44 次）
    check("44" in r["report"], "报告里带实测对照（44 次）")


def test_seq_is_snapshot():
    print("\n③ seq 是全局快照（可被多条共享），不是序号")
    r = E.build_report(log_dir=CASE, explain=False)
    seqs = [it["seq"] for it in r["items"]]
    check(len(set(seqs)) < len(seqs),
          "存在**共享同一 seq** 的多条记录（证明它不是序号）", seqs)
    r2 = E.build_report(log_dir=CASE)
    check("共享同一个 seq" in r2["report"], "report 说明 seq 可共享")
    check("不是序号" in r2["report"], "report 明确 seq 不是序号")


def test_thrower_detection_has_both_truth_values():
    print("\n④ stackHeadIsThrower 判据有真也有假（不许恒真/恒假）")
    r = E.build_report(log_dir=CASE, explain=False)
    vals = set(it["stackHeadIsThrower"] for it in r["items"])
    check(vals == {True, False},
          "同一份数据里既有 True 也有 False（判据有区分力）", vals)
    crypto = [it for it in r["items"] if "Cryptographic" in (it["type"] or "")]
    harm = [it for it in r["items"] if "HarmonyException" in (it["type"] or "")]
    check(crypto and all(it["stackHeadIsThrower"] for it in crypto),
          "Cryptographic（抛助记帧）=> True", [it["stackHeadIsThrower"] for it in crypto])
    check(harm and not any(it["stackHeadIsThrower"] for it in harm),
          "HarmonyException（真调用者帧）=> False", [it["stackHeadIsThrower"] for it in harm])

    # ⚠️ 这两条**必须用 explain=True**（2026-10-06 自测自己抓到的错）：
    #    本工具 `explain=False` 时 `report` 是**空串**（设计如此：调用方只要机器字段），
    #    最初这里传了 explain=False 却断言 report 内容 ⇒ 断言恒失败。
    #    结论：验"文本里说了什么"就必须开 explain。
    rr = E.build_report(log_dir=CASE)          # explain 默认 True
    check(bool(rr["report"]), "explain=True 时 report 非空", len(rr["report"]))
    check("来源不可判定" in rr["report"], "report 对 True 的条目明说「来源不可判定」")
    check("excludeModules" in rr["report"], "给出 A/B 归因法（bl_launch_game excludeModules）")
    # 尾部汇总：不过滤时也要点出"哪些条目不可判定"
    check("来源不可判定" in rr["report"].split("-" * 74)[-1],
          "结尾**汇总**段也点出不可判定（不只在明细行里）")


def test_blind_spots():
    print("\n⑤ 结构性盲区必须随结果报出")
    r = E.build_report(log_dir=CASE)
    s = r["semantics"]
    bs = " ".join(s.get("blindSpots") or [])
    check("JIT" in bs, "盲区含 JIT 期", bs[:80])
    check("原生崩溃" in bs, "盲区含原生崩溃", bs[:80])
    check("托管异常" in bs, "说明只记托管异常", bs[:80])
    check("bl_crash" in r["report"], "report 指向 bl_crash 补盲区")
    check(s.get("fullStackAvailable") is False, "声明完整栈不可得", s.get("fullStackAvailable"))
    check(s.get("stackHeadFrames") == 1, "声明只取 1 帧", s.get("stackHeadFrames"))


def test_bad_rows():
    print("\n⑥ 坏行计数并带原文（不静默）")
    r = E.build_report(log_dir=CASE, explain=False)
    check(r["badRows"] == 2, "2 条坏行（非法 JSON + 合法但非对象）", r["badRows"])
    check(len(r["badSamples"]) == 2, "带出坏行原文", r["badSamples"])
    rr = E.build_report(log_dir=CASE)
    check("坏行" in rr["report"], "report 里点明坏行")


def test_missing_file():
    print("\n⑦ 文件不存在时如实说「不代表没发生异常」")
    r = E.build_report(log_dir=os.path.join(HERE, "_no_such_logdir_xyz"))
    check(r["exists"] is False, "exists=False", r["exists"])
    check("不代表没发生异常" in r["report"],
          "明说「不代表没发生异常」（不伪装成没异常）", r["report"][:200])
    check("installed" in r["report"], "指向 installed 判据")


def test_type_filter():
    print("\n⑧ type_filter 过滤 + 截断如实报")
    r = E.build_report(log_dir=CASE, type_filter="Cryptographic", limit=1, explain=False)
    check(r["matched"] == 2, "Cryptographic 匹配 2 行", r["matched"])
    check(r["returned"] == 1, "limit=1 只回 1 条", r["returned"])
    check(all("Cryptographic" in (it["type"] or "") for it in r["items"]),
          "返回的都是匹配类型")


def main():
    bl_common.safe_streams()
    print("=" * 84)
    print("bl_exception_detail 自测（宿主侧离线）")
    print("=" * 84)
    try:
        setup()
        test_segments()
        test_rows_not_counts()
        test_seq_is_snapshot()
        test_thrower_detection_has_both_truth_values()
        test_blind_spots()
        test_bad_rows()
        test_missing_file()
        test_type_filter()
    finally:
        cleanup()
    print()
    print("=" * 84)
    if _fail:
        print("结果: 失败 %d 项" % len(_fail))
        for f in _fail:
            print("  - %s" % f)
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
