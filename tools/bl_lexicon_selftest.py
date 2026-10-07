#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bl_lexicon.py 的离线自测（宿主侧，仅标准库，**不需要游戏、不需要真实词条数据**）。

## 为什么它不是摆设

`[OK] N/N 通过` 单独拿出来证明不了任何事 —— 一个恒返回 OK 的校验器、
一个正则写错把全部条目跳过去的校验器，都会得到这个结果。
所以本自测对**每一条判据都配一个"应报 / 不应报"的受控样本对**，
并额外注入故障（inject）逐条断言被抓到。

## 判据清单（每条都写明"哪种输入会红"）

| # | 判据 | 哪种输入会红 |
|---|---|---|
| 1 | **未装数据**时 `installed=False` 且退出码 1 | 把"目录不存在"当成"没有匹配"静默返回 0 |
| 2 | 目录在但**缺核心文件** ⇒ 不算已安装 | 缺文件仍报 installed=True（下游会拿到空结果） |
| 3 | 装载成功时条数正确 | 解析器把某个 XML/JSON 漏读 |
| 4 | 异常短名精确匹配 | `NullReferenceException` 查不到 `System.NullReferenceException` 形态的词条 |
| 5 | **库里没有该类型** ⇒ `found=False` 且给 knownCount | 查不到就返回 `None`/空，调用方以为"无异常" |
| 6 | `MatchAny` 语义 | 任一命中即中 |
| 7 | `MatchAll` 语义 | 缺一短语即不中 |
| 8 | `ExcludeAny` 语义 | 命中排除词仍中（**最易写反的一条**） |
| 9 | **`MatchAny` 为空、`MatchAll` 非空**仍能命中 | 把空 `MatchAny` 当"空集"⇒ 误杀全部 legacy-alone 词条 |
| 10 | priority 降序 | 排序写反（低优先级先出） |
| 11 | **zh 缺失时回退 en 且如实标注** | 静默用英文冒充中文（本工具立项时要防的就这条） |
| 12 | `langMissing` 计数 | 不数或数错，让"中文覆盖率"看起来是满的 |
| 13 | **zh 字段被英文污染** ⇒ 判为故障 | 上游数据/转换器把英文写进 zh（**实测真踩过**） |
| 14 | `sourceRepairAction` 只作信息、不冒充可执行动作 | 把上游模组的按钮报成 BlBridge 能做的事 |
| 15 | 真实数据（若本地存在）条数 = 57/84/43 | 数据漂移；**不存在时如实跳过，不算通过** |

用法：
    python tools/bl_lexicon_selftest.py             # 全部判据
    python tools/bl_lexicon_selftest.py --verbose

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

import bl_lexicon                                     # noqa: E402

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


def write_json(path, obj):
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=2))
        fh.write("\n")


def make_fixture(root, *, orphan_zh=False, missing_core=None):
    """造一份合成词条（形状与原数据一致：多语言块 {en,zh,zhHant}）。"""
    os.makedirs(root, exist_ok=True)

    def blk(en, zh=None):
        # orphan_zh=True 模拟"英文被写进 zh 字段"的污染
        return {"en": en, "zh": (en if (orphan_zh and zh is None) else zh), "zhHant": None}

    exceptions = [
        {
            "type": "NullReferenceException",
            "shortName": "NullReferenceException",
            "file": "01-core-data.xml",
            "displayName": blk("Null reference error", "空引用错误"),
            "description": blk("Reads missing data.", "读取了不存在的数据。"),
            "scenarios": [{"index": 1, "token": None, "text": blk("Before load.", "加载前。")}],
            "suggestions": [{"index": 1, "token": None, "text": blk("Check the mod set.", "检查模组集。")}],
        },
        {
            "type": "HarmonyException",
            "shortName": "HarmonyException",
            "file": "03.xml",
            "displayName": blk("Harmony patch failed"),          # 只有英文（legacy 形态）
            "description": blk("Patch target missing."),         # 只有英文
            "scenarios": [],
            "suggestions": [],
        },
    ]
    diagnostic = [
        {   # MatchAny + MatchAll
            "id": "gpu-lost", "source": {"file": "core.json", "kind": "diagnosticRules", "index": 0},
            "matchAny": ["device_removed", "0x887a0006"], "matchAll": ["dxgi"], "excludeAny": [],
            "category": blk("Graphics", "图形"), "severity": blk("High", "高"),
            "confidence": blk("High", "高"), "evidence": blk("Lost device", "设备丢失"),
            "priority": 70, "action": blk("Update driver", "更新驱动"),
            "risk": blk("Low", "低"), "reason": blk("Driver reset", "驱动重置"),
            "backupRecommended": False, "sourceRepairAction": "captureStatus",
        },
        {   # 只有 MatchAll（MatchAny 为空）—— 判据 9 的样本
            "id": "all-only", "source": {"file": "core.json", "kind": "diagnosticRules", "index": 1},
            "matchAny": [], "matchAll": ["loadsavegame"], "excludeAny": [],
            "category": None, "severity": None, "confidence": None, "evidence": None,
            "priority": 50, "action": blk("Load earlier save", "读取更早存档"),
            "risk": blk("Medium", "中"), "reason": blk("Save set changed", "存档模组集变了"),
            "backupRecommended": True, "sourceRepairAction": "saveDiagnosis",
        },
        {   # ExcludeAny —— 判据 8 的样本
            "id": "excl", "source": {"file": "core.json", "kind": "diagnosticRules", "index": 2},
            "matchAny": ["nullreferenceexception"], "matchAll": [], "excludeAny": ["is expected"],
            "category": None, "severity": None, "confidence": None, "evidence": None,
            "priority": 10, "action": blk("Investigate", "排查"),
            "risk": blk("Low", "低"), "reason": blk("Null deref", "空引用"),
            "backupRecommended": False, "sourceRepairAction": "captureStatus",
        },
    ]
    known = [
        {
            "id": "native-crash", "source": {"file": "ki.json", "kind": "knownIssues", "index": 0},
            "matchAny": ["access violation", "0xc0000005"], "matchAll": [], "excludeAny": [],
            "category": None, "severity": None, "confidence": None, "evidence": None,
            "priority": None, "action": blk("Review crash report", "查看崩溃报告"),
            "risk": blk("High", "高"), "reason": blk("Native crash", "原生崩溃"),
            "backupRecommended": False, "sourceRepairAction": None,
        },
    ]

    files = {
        "exceptions.json": exceptions,
        "diagnostic-rules.json": diagnostic,
        "known-issues.json": known,
        "meta.json": {"schema": "blbridge-lexicon/1", "provenance": {"privateOnly": True}},
    }
    for fn, obj in files.items():
        if missing_core and fn == missing_core:
            continue
        write_json(os.path.join(root, fn), obj)
    return root


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description="bl_lexicon.py 离线自测")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    VERBOSE = args.verbose

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                  # noqa: BLE001
        pass

    print("bl_lexicon 自测")
    print("=" * 74)

    tmp = tempfile.mkdtemp(prefix="bl_lexicon_selftest_")
    try:
        # ---------- 判据 1：未装数据 ----------
        print("\n[1] 未装数据（目录不存在）")
        missing = os.path.join(tmp, "nope")
        lex, st = bl_lexicon.load_lexicon(path=missing)
        check("1a installed=False", st["installed"] is False, repr(st))
        check("1b 返回 None（不是空词典）", lex is None, "返回了 %r" % (type(lex).__name__,))
        check("1c 有可读原因", bool(st.get("reason")), repr(st.get("reason")))
        check("1d 原因是'目录不存在'而非'没有匹配'",
              "不存在" in (st.get("reason") or ""), repr(st.get("reason")))

        # ---------- 判据 2：缺核心文件 ----------
        print("\n[2] 目录在但缺核心文件（diagnostic-rules.json）")
        d2 = make_fixture(os.path.join(tmp, "partial"), missing_core="diagnostic-rules.json")
        lex2, st2 = bl_lexicon.load_lexicon(path=d2)
        check("2a 不算已安装", st2["installed"] is False, repr(st2))
        check("2b 返回 None", lex2 is None, "返回了 %r" % (type(lex2).__name__,))
        check("2c 点名了缺的文件", "diagnostic-rules.json" in (st2.get("reason") or ""),
              repr(st2.get("reason")))

        # ---------- 判据 3：正常装载 ----------
        print("\n[3] 正常装载")
        d3 = make_fixture(os.path.join(tmp, "ok"))
        lex3, st3 = bl_lexicon.load_lexicon(path=d3)
        check("3a installed=True", st3["installed"] is True, repr(st3))
        check("3b exceptions=2", len(lex3["exceptions"]) == 2,
              str(len(lex3["exceptions"])))
        check("3c diagnosticRules=3", len(lex3["diagnosticRules"]) == 3,
              str(len(lex3["diagnosticRules"])))
        check("3d knownIssues=1", len(lex3["knownIssues"]) == 1,
              str(len(lex3["knownIssues"])))
        check("3e 无缺失文件", st3["filesMissing"] == [], repr(st3["filesMissing"]))

        # ---------- 判据 4/5：异常类型匹配 ----------
        print("\n[4][5] 异常类型匹配")
        e = bl_lexicon.lookup_exception(lex3, "NullReferenceException")
        check("4a 短名命中", e is not None and e["type"] == "NullReferenceException")
        e_full = bl_lexicon.lookup_exception(lex3, "System.NullReferenceException")
        check("4b 全名也命中（短名归一）",
              e_full is not None and e_full["type"] == "NullReferenceException",
              repr(e_full))
        res5 = bl_lexicon.describe(lex3, exc_type="TotallyUnknownException")
        check("5a 库里没有 ⇒ found=False", res5["exception"].get("found") is False,
              repr(res5["exception"]))
        check("5b 给出 knownCount", res5["exception"].get("knownCount") == 2,
              repr(res5["exception"]))

        # ---------- 判据 6/7/8/9：匹配语义 ----------
        print("\n[6][7][8][9] 匹配语义")
        m = bl_lexicon.match_text(lex3, "today we saw device_removed from dxgi")
        check("6a MatchAny 命中", any(x[2]["id"] == "gpu-lost" for x in m), repr([x[2]['id'] for x in m]))
        m_noall = bl_lexicon.match_text(lex3, "today we saw device_removed twice")
        check("7a MatchAll 缺一短语则不中",
              not any(x[2]["id"] == "gpu-lost" for x in m_noall),
              repr([x[2]['id'] for x in m_noall]))
        # ⚠️ 对照纪律（本项首版写错，自测反过来抓到了测试自己）：
        #    首版用的文本是 "device_removed only, no dxgi word" —— 它**真的含有子串 `dxgi`**，
        #    于是 MatchAll 命中是**正确行为**，却把判定写成了"应当不中"。
        #    教训：**子串匹配的样本必须逐字确认不含该子串**，不能凭"句意上否定"就算不含。
        check("7b 对照组自检：确认上面那份文本确实不含 'dxgi'",
              "dxgi" not in "today we saw device_removed twice",
              "测试样本写错会把正确行为判成失败")
        m_allonly = bl_lexicon.match_text(lex3, "error inside LoadSaveGameData call")
        check("9a MatchAny 为空 + MatchAll 命中 ⇒ 仍中",
              any(x[2]["id"] == "all-only" for x in m_allonly),
              repr([x[2]['id'] for x in m_allonly]))
        m_excl = bl_lexicon.match_text(lex3, "NullReferenceException: this is expected behavior")
        check("8a ExcludeAny 命中 ⇒ 排除",
              not any(x[2]["id"] == "excl" for x in m_excl),
              repr([x[2]['id'] for x in m_excl]))
        m_excl2 = bl_lexicon.match_text(lex3, "NullReferenceException: real problem")
        check("8b 未命中排除词 ⇒ 保留",
              any(x[2]["id"] == "excl" for x in m_excl2),
              repr([x[2]['id'] for x in m_excl2]))

        # ---------- 判据 10：priority 排序 ----------
        print("\n[10] priority 降序")
        m10 = bl_lexicon.match_text(lex3, "device_removed dxgi nullreferenceexception real problem")
        prios = [x[0] for x in m10]
        check("10a 降序排列", prios == sorted(prios, reverse=True), repr(prios))

        # ---------- 判据 10b：多代同名条目必须**合并**而非重复报告 ----------
        # ★ 立项理由（实测 2026-10-07）：真机上 `access violation 0xC0000005` 返回 **2 条**
        #   同名规则、`DXGI_ERROR_DEVICE_REMOVED` 返回 **3 条** —— 因为
        #   ① `diagnosticRules` 内部有 legacy/crashdoctor 两代同 id；
        #   ② `knownIssues` 43 条**完全是** `diagnosticRules` 的子集，而旧代码两处都遍历。
        #   后果：调用方以为有多个问题。**这里必须有判据钉住"同一条只报一次"**。
        print("\n[10b] 多代同名条目合并（不重复报告）")
        # 造两代同名条目：legacy 无元数据 + 新代有元数据，且匹配面不同
        d10 = make_fixture(os.path.join(tmp, "merged"))
        with io.open(os.path.join(d10, "diagnostic-rules.json"), "w",
                     encoding="utf-8", newline="\n") as fh:
            json.dump([
                {"id": "dup", "source": {"file": "crashdoctor.json", "kind": "diagnosticRules",
                                         "index": 0},
                 "matchAny": ["harmony.patchexception"], "matchAll": [], "excludeAny": [],
                 "category": {"en": "Cat", "zh": "类", "zhHant": None},
                 "severity": {"en": "High", "zh": "高", "zhHant": None},
                 "confidence": None, "evidence": None, "priority": 76,
                 "action": {"en": "Fix it", "zh": "修复", "zhHant": None},
                 "risk": None, "reason": None, "backupRecommended": False,
                 "sourceRepairAction": None},
                {"id": "dup", "source": {"file": "legacy.json", "kind": "diagnosticRules",
                                         "index": 0},
                 # ⚠️ legacy 的短语**更宽**（`harmony patch`）—— 合并必须保住它
                 "matchAny": ["harmony.patchexception", "harmony patch"], "matchAll": [],
                 "excludeAny": [], "category": None, "severity": None, "confidence": None,
                 "evidence": None, "priority": None,
                 "action": {"en": "Fix it", "zh": "修复", "zhHant": None},
                 "risk": None, "reason": None, "backupRecommended": False,
                 "sourceRepairAction": None},
            ], fh, ensure_ascii=False)
        lex10, _ = bl_lexicon.load_lexicon(path=d10)
        m_dup = bl_lexicon.match_text(lex10, "harmony.patchexception occurred")
        check("10b-1 同 id 只报一次", len(m_dup) == 1, "报道 %d 条" % len(m_dup))
        # 并集必须保住 legacy 独有的短语（丢了这个 = 匹配能力缩水）
        m_wide = bl_lexicon.match_text(lex10, "a harmony patch problem")
        check("10b-2 匹配面取并集（legacy 独有短语仍命中）",
              len(m_wide) == 1 and m_wide[0][2]["id"] == "dup",
              repr([x[2]["id"] for x in m_wide]))
        # 元数据必须取最全的那条（不能降级成 legacy 的空值）
        check("10b-3 元数据取最全（priority 保住）",
              m_dup[0][2].get("priority") == 76, repr(m_dup[0][2].get("priority")))
        check("10b-4 如实标出由多代合成", bool(m_dup[0][2].get("mergedFrom")),
              repr(m_dup[0][2].get("mergedFrom")))

        # ---------- 判据 11/12：语言回退与如实标注 ----------
        print("\n[11][12] 语言回退与 langMissing")
        resH = bl_lexicon.describe(lex3, exc_type="HarmonyException", lang="zh")
        check("11a zh 缺失时回退 en", resH["exception"]["displayName"] == "Harmony patch failed",
              repr(resH["exception"]["displayName"]))
        check("11b 如实标注回退了", resH["exception"].get("langFellBackToEn") is True,
              repr(resH["exception"].get("langFellBackToEn")))
        check("12a langMissing 计数 > 0", resH.get("langMissing", 0) > 0,
              repr(resH.get("langMissing")))
        resN = bl_lexicon.describe(lex3, exc_type="NullReferenceException", lang="zh")
        check("12b 有中文时不虚报 langMissing",
              resN["exception"]["displayName"] == "空引用错误" and resN["langMissing"] == 0,
              repr((resN["exception"]["displayName"], resN["langMissing"])))

        # ---------- 判据 13：zh 被英文污染 ----------
        print("\n[13] zh 字段被英文污染（对照判据）")
        d13 = make_fixture(os.path.join(tmp, "polluted"), orphan_zh=True)
        lex13, _ = bl_lexicon.load_lexicon(path=d13)
        # 污染下：zh 字段有值（英文），所以**不会**被标为 langMissing —— 这正是污染的危险之处。
        res13 = bl_lexicon.describe(lex13, exc_type="HarmonyException", lang="zh")
        got = res13["exception"]["displayName"]
        polluted = (got == "Harmony patch failed")
        check("13a 污染会被读出（zh 里是英文）", polluted, repr(got))
        check("13b 且**不会**被误标为 langMissing（故必须另有检测）",
              res13["langMissing"] == 0, repr(res13["langMissing"]))
        # 探测器：zh 无 CJK 且含 >=2 个英文单词 ⇒ 判污染
        import re
        CJK = re.compile(r"[\u4e00-\u9fff]")
        WORD = re.compile(r"[A-Za-z]{2,}")
        detector = lambda z: bool(z) and (not CJK.search(z)) and len(WORD.findall(z)) >= 2
        check("13c 外部探测器能抓住它",
              detector(got) and not detector("空引用错误"),
              repr((got, detector(got))))

        # ---------- 判据 14：sourceRepairAction 只作信息 ----------
        print("\n[14] sourceRepairAction 不当可执行动作")
        res14 = bl_lexicon.describe(lex3, text="device_removed dxgi")
        hit = [x for x in res14["matches"] if x["id"] == "gpu-lost"][0]
        check("14a 保留了来源信息", hit.get("sourceRepairAction") == "captureStatus",
              repr(hit.get("sourceRepairAction")))
        txt = bl_lexicon.render(res14)
        check("14b 渲染里明确标注 BlBridge 没有该动作",
              "没有" in txt and "captureStatus" in txt, txt[-200:])

        # ---------- 判据 1 的 CLI 侧（退出码） ----------
        print("\n[1-e] CLI 未装数据时退出码")
        rc = bl_lexicon.main(["--dir", missing, "--type", "X"])
        check("1e 退出码 1", rc == 1, "rc=%r" % (rc,))
        rc2 = bl_lexicon.main(["--dir", d3, "--type", "NullReferenceException"])
        check("1f 正常时退出码 0", rc2 == 0, "rc=%r" % (rc2,))

        # ---------- 判据 15：真实数据（存在才查，不存在如实跳过） ----------
        print("\n[15] 真实词条数据")
        # ⚠️ 必须走**同一个**目录解析器（`lexicon_dir()`），否则环境变量会被绕过 ——
        #    实测（2026-10-07 端到端）：首版硬拼 HERE/data/lexicon，于是设了
        #    BLBRIDGE_LEXICON_DIR 之后本项**仍然 skip**，环境变量形同虚设。
        real = bl_lexicon.lexicon_dir()
        if not os.path.isdir(real):
            print("  [skip] 真实词条目录不存在（公开仓不带数据，这是预期）")
            print("         解析到的路径：%s" % real)
            print("         让 %s 指向本地词条目录后再跑本项。" % bl_lexicon.ENV_VAR)
        else:
            lexr, str_ = bl_lexicon.load_lexicon()
            if lexr is None:
                check("15a 真实数据可装载", False, repr(str_.get("reason")))
            else:
                n = (len(lexr["exceptions"]), len(lexr["diagnosticRules"]),
                     len(lexr["knownIssues"]))
                check("15a 条数 = 57/84/43", n == (57, 84, 43), repr(n))
                # 真实数据上的**语言通道污染**检查（本仓立项时要防的那条）
                import re as _re
                CJK_ = _re.compile(r"[\u4e00-\u9fff]")
                WORD_ = _re.compile(r"[A-Za-z]{2,}")

                def bad(blk):
                    z = blk.get("zh") if isinstance(blk, dict) else None
                    return bool(z) and (not CJK_.search(z)) and len(WORD_.findall(z)) >= 2

                poll = 0
                for e0 in lexr["exceptions"]:
                    for b0 in (e0.get("displayName"), e0.get("description")):
                        poll += 1 if bad(b0) else 0
                    for it0 in (e0.get("scenarios") or []) + (e0.get("suggestions") or []):
                        poll += 1 if bad(it0.get("text")) else 0
                for k0 in ("diagnosticRules", "knownIssues"):
                    for r0 in lexr[k0]:
                        for kk in bl_lexicon.TEXT_KEYS:
                            poll += 1 if bad(r0.get(kk)) else 0
                check("15b 真实数据无 zh 英文污染", poll == 0, "%d 处" % poll)
                # 真实数据上按类型查询（真实类型名）
                rr = bl_lexicon.describe(lexr, exc_type="NullReferenceException", lang="zh")
                check("15c 真实类型可查到中文",
                      bool(rr.get("exception")) and rr["exception"].get("displayName") == "空引用错误",
                      repr(rr.get("exception")))
                # 真实数据上按文本匹配（用真实存在的短语）
                mm = bl_lexicon.match_text(lexr, "crash: access violation 0xC0000005 in engine")
                check("15d 真实短语能匹配到词条", len(mm) > 0,
                      repr([x[2]["id"] for x in mm][:5]))
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
