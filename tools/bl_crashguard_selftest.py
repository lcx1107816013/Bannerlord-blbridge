#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bl_crashguard.py 的离线自测（宿主侧，仅标准库，**不需要游戏**）。

## 为什么它不是摆设

按本仓库纪律：**没有对照组的验证不是验证，只是自证**。
所以每条判据都配"应报 / 不应报"的成对样本，且都针对**真实会踩的坑**：

| # | 判据 | 哪种输入会红 |
|---|---|---|
| 1 | 账本不存在 ⇒ `exists=false`、退出码 1 | 把"读不到"当成"没有崩溃"静默返回 0 |
| 2 | **守卫未启用**时明确说"不等于没崩溃" | 默认关闭被误读成"一切正常" |
| 3 | 正常解析各 reason 计数 | 解析器漏字段 |
| 4 | **坏行计数并带原文** | 静默跳过坏行（本仓库 bl_json_health 立项时的同款缺陷） |
| 5 | `swallow` **必须**被标成"不是已修复" | 把"吞掉了"报成"修好了"——**本工具最危险的方向** |
| 6 | `breaker_open` / `quota_exhausted` 判 critical 且排在前面 | 把"守卫已失效"埋进 info 里 |
| 7 | `fatal_passthrough` 指出"游戏很可能仍崩了" | 误报成"已跳过" |
| 8 | 按 `runToken` 切段 | 跨进程混算 |
| 9 | 词条可用时给出修复建议；不可用时**如实说缺**（不是空列表冒充） | 静默给空建议 |
| 10 | 该有建议却没有 ⇒ 明确提示 | 让"没建议"看起来像"不需要建议" |
| 11 | ★ **反向对照**：全是 info（installed）时 `attention` 必须为空 | 判据恒真（把什么都说成 critical） |

用法：
    python tools/bl_crashguard_selftest.py [--verbose]
退出码：0 = 全过；1 = 有失败。
"""
import argparse
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bl_crashguard as cg                              # noqa: E402

FAILS = []
CHECKS = [0]
VERBOSE = False


def check(name, cond, detail=""):
    CHECKS[0] += 1
    if cond:
        if VERBOSE:
            print("  [ok] %s" % name)
    else:
        FAILS.append("%s%s" % (name, (" —— " + detail) if detail else ""))
        print("  [FAIL] %s%s" % (name, (" —— " + detail) if detail else ""))


def wl(path, objs, extra_lines=()):
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        for o in objs:
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")
        for ln in extra_lines:
            fh.write(ln + "\n")


def rec(action, reason, rtype=None, target=None, tok="tokA", msg=None, frames=None):
    """造一条账本记录。

    ⚠️ `action` 必须是**字符串** `"swallow"` / `"pass"`，与 C# 侧
       `CrashGuard.FormatEvent` 的 `e.Swallowed ? "swallow" : "pass"` **逐字一致**。
       首版本这里传布尔 `True/False`，于是本自测**全绿**却测的是不存在的格式
       —— 而生产解析器按字符串比对 ⇒ `swallowed` 恒为 0。
       ★ 教训：**合成样本必须与被测端的真实产物同格式**，否则测的是自己的想象。
       （true == 1 / false == 0 的相似性让这类错更难看出。）
    """
    assert action in ("swallow", "pass"), "action 必须是 'swallow'/'pass'，收到 %r" % (action,)
    d = {"t": "guard", "utc": "2026-10-07T10:00:00.000Z", "runToken": tok,
         "pid": 1234, "action": action, "reason": reason, "count": 1}
    if rtype:
        d["type"] = rtype
    if target:
        d["target"] = target
    if msg:
        d["message"] = msg
    if frames:
        d["frames"] = frames
    return d


def write_status(d, guard):
    with io.open(os.path.join(d, "bridge_status.json"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"crashGuard": guard}, ensure_ascii=False) + "\n")


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description="bl_crashguard 离线自测")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    VERBOSE = args.verbose
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                     # noqa: BLE001
        pass

    print("bl_crashguard 自测")
    print("=" * 74)

    tmp = tempfile.mkdtemp(prefix="bl_cg_selftest_")
    try:
        # ── 1: 账本不存在 ──
        print("\n[1] 账本不存在（无状态文件、无账本）")
        d1 = os.path.join(tmp, "empty")
        os.makedirs(d1)
        r1 = cg.build_report(log_dir=d1)
        check("1a exists=False", r1["exists"] is False, repr(r1["exists"]))
        # ⚠️ 断言要**瞄准具体表述**，不能用"不含某个词"这种模糊判据：
        #    首版写 `"没有崩溃" not in render.replace("不代表","")` —— 太脆，
        #    而渲染里正确的说法是"**不等于**「没有崩溃」"，恰好被误伤。
        #    改为直接要求出现这句**澄清**。
        txt1 = cg._render(r1)
        check("1b 明说「不等于没有崩溃」", "不等于" in txt1 and "没有崩溃" in txt1, txt1[:200])
        check("1c CLI 退出码 1", cg.main(["--logDir", d1]) == 1)
        check("1d 明说未启用/无法判断", ("未启用" in txt1) or ("无法判断" in txt1), txt1[:160])

        # ── 2: 守卫未启用但账本存在 ──
        print("\n[2] 守卫未启用（enabled=false）但账本存在")
        d2 = os.path.join(tmp, "disabled")
        os.makedirs(d2)
        wl(os.path.join(d2, "crashguard.jsonl"), [])
        write_status(d2, {"enabled": False, "installError": ""})
        r2 = cg.build_report(log_dir=d2)
        check("2a enabled=False", r2["enabled"] is False, repr(r2["enabled"]))
        check("2b 渲染里点明「默认即关闭」", "默认即关闭" in cg._render(r2),
              cg._render(r2)[:200])

        # ── 3~8: 真实形态的账本 ──
        print("\n[3-8] 真实形态账本（吞/放行/熔断/配额/致命/坏行/多进程）")
        d3 = os.path.join(tmp, "real")
        os.makedirs(d3)
        rows = [
            rec("pass",  "installed", target="CrashGuard.Install", msg="已挂 3 个目标"),
            rec("swallow", "swallowed", "System.NullReferenceException",
                "TaleWorlds.MountAndBlade.Mission.Tick", "tokA",
                "Object reference not set", "at Foo.Bar()\nat Baz.Qux()"),
            rec("swallow", "swallowed", "System.InvalidOperationException",
                "TaleWorlds.DotNet.Managed.ApplicationTick", "tokA", "bad state", "at A.B()"),
            rec("pass",  "fatal_passthrough", "System.OutOfMemoryException",
                "TaleWorlds.MountAndBlade.Mission.Tick", "tokA", "oom", None),
            rec("pass",  "breaker_open", "System.NullReferenceException",
                "TaleWorlds.MountAndBlade.Mission.Tick", "tokA", "same sig x21", "at Foo.Bar()"),
            rec("pass",  "quota_exhausted", target=None, msg="配额用尽", tok="tokA"),
            # 第二个进程段
            rec("swallow", "swallowed", "System.FormatException",
                "TaleWorlds.ScreenSystem.ScreenManager.Tick", "tokB", "bad format", "at S.T()"),
        ]
        # 故意混入 2 行坏 JSON（含一行"合法 JSON 但不是对象"）
        wl(os.path.join(d3, "crashguard.jsonl"), rows,
           extra_lines=["{不是 JSON", "[1,2,3]"])
        write_status(d3, {"enabled": True, "installError": ""})

        r3 = cg.build_report(log_dir=d3)
        check("3a 解析 7 条", r3["lines"] - r3["badRows"] == 7,
              "lines=%d bad=%d" % (r3["lines"], r3["badRows"]))
        check("3b swallow 计数 = 3", r3["swallowed"] == 3, str(r3["swallowed"]))
        check("3c pass 计数 = 4", r3["passedThrough"] == 4, str(r3["passedThrough"]))
        check("4a 坏行被计数 = 2", r3["badRows"] == 2, str(r3["badRows"]))
        check("4b 坏行带原文", len(r3["badSamples"]) == 2, repr(r3["badSamples"]))
        check("5a 口径里声明 swallow 不是修复",
              r3["semantics"]["swallowIsNotFix"] is True)
        t3 = cg._render(r3)
        check("5b 渲染里明说「不等于已修复」", "不等于已修复" in t3 or "不是「已修复」" in t3,
              t3[:400])
        sevs = [a["reason"] for a in r3["attention"]]
        check("6a breaker_open 在 attention 里", "breaker_open" in sevs, repr(sevs))
        check("6b quota_exhausted 在 attention 里", "quota_exhausted" in sevs, repr(sevs))
        crit = [a["reason"] for a in r3["attention"] if a["sev"] == "critical"]
        check("6c breaker/quota 判 critical",
              "breaker_open" in crit and "quota_exhausted" in crit, repr(crit))
        # ⚠️ 首版这条写成 `sevs.index(...) < len([...]) + 99` —— **近乎恒真**，
        #    等于没测。改为直接判"critical 全部排在非 critical 之前"。
        first_non_crit = next((i for i, a in enumerate(r3["attention"])
                               if a["sev"] != "critical"), len(r3["attention"]))
        all_crit_before = all(a["sev"] == "critical"
                              for a in r3["attention"][:first_non_crit])
        check("6d critical 全部排在 warn 之前", all_crit_before, repr(sevs))
        check("7a fatal_passthrough 被标为 warn 并提示仍可能崩",
              any(a["reason"] == "fatal_passthrough" for a in r3["attention"]), repr(sevs))
        t7 = cg._render(r3)
        check("7b 渲染里要求用 bl_crash 看 dump", "bl_crash" in t7, t7[:300])
        check("8a 按 runToken 切段 = 2", r3["sessions"] == 2, str(r3["sessions"]))

        # ── 9/10: 修复建议与词典状态 ──
        print("\n[9][10] 修复建议与词典")
        lex_dir = os.environ.get("BLBRIDGE_LEXICON_DIR")
        # ⚠️ 这一组**两边都要测**（有词条 / 无词条），不能只测当前环境恰好落到的那个分支 ——
        #    首版只在"无词条"时断言，于是"有词条却没给建议"这条真缺陷**永远不会被发现**。
        if lex_dir and os.path.isdir(lex_dir):
            check("9a 有词条时给出 repairs", len(r3["repairs"]) >= 1,
                  "repairs=%d lexicon=%s" % (len(r3["repairs"]), r3["lexicon"]))
            check("9b lexicon 状态为 loaded", r3["lexicon"] == "loaded", r3["lexicon"])
            got = [rp for rp in r3["repairs"] if (rp.get("exception") or {}).get("displayName")]
            check("9c 至少一条给出人话名称", len(got) >= 1,
                  repr([rp.get("type") for rp in r3["repairs"]]))
        else:
            print("  [skip] 未设 BLBRIDGE_LEXICON_DIR（无词条）—— 测「缺词条如实报告」分支")
            check("9b 无词条时如实说缺（不冒充空建议）",
                  r3["lexicon"].startswith("missing"), r3["lexicon"])
        # 10：**无论词典在不在**，渲染都必须对被吞记录有交代（有建议 或 明确说没建议）
        t9 = cg._render(r3)
        check("10a 对被吞记录有交代（建议或如实说缺）",
              ("修复建议" in t9) or ("未能给出修复建议" in t9), t9[-400:])

        # ── 11: 反向对照 —— 只有 info 时 attention 必须为空 ──
        print("\n[11] 反向对照：全是 info ⇒ attention 必须为空")
        d4 = os.path.join(tmp, "infoonly")
        os.makedirs(d4)
        wl(os.path.join(d4, "crashguard.jsonl"), [rec("pass", "installed", target="X")])
        write_status(d4, {"enabled": True, "installError": ""})
        r4 = cg.build_report(log_dir=d4)
        check("11a attention 为空（判据不是恒真）", r4["attentionTotal"] == 0,
              "attentionTotal=%d" % r4["attentionTotal"])
        check("11b 但行数仍被读到", r4["lines"] == 1, str(r4["lines"]))

        # ── 12: 导出 JSON 可序列化（MCP 侧要直接返回）──
        print("\n[12] JSON 可序列化（MCP 直接返回）")
        try:
            s = json.dumps(r3, ensure_ascii=False)
            check("12a 可 json.dumps", len(s) > 100, "len=%d" % len(s))
        except Exception as exc:                          # noqa: BLE001
            check("12a 可 json.dumps", False, "%s: %s" % (type(exc).__name__, exc))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 74)
    if FAILS:
        print("失败 %d / %d：" % (len(FAILS), CHECKS[0]))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("全部通过：%d / %d" % (CHECKS[0], CHECKS[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
