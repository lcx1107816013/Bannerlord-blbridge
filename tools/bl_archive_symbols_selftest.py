#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bl_archive_symbols 的自测 —— 重点验**判据**，不验「跑通了」。

## 为什么这个自测值得存在

本工具第一版有一个真实缺陷：Archive 用**普通文本**报「类型不存在」
（`csharp-type-reader.ts:48` 的 `notFoundText`，`isError` 根本不出现），
而第一版只检查了 `isError`。于是它把

    Type 'TaleWorlds.CampaignSystem.Hero' was not found in the index.

当成源码正文返回，标 `status: "ok"`，还把它计入 `resolved`。

**这是本项目反复出现的那一类缺陷**：检查看不见它要验的东西，报成功和报失败一样自信。
所以自测里最重要的一条不是「能查到 Hero」，而是
**「查不到时必须报 not_found，不许报 ok」**。

## 三层

1. **纯函数层**（不需要 Archive）：符号拆分、not-found 识别、假阳性防护。
2. **真机层**（需要 Archive，可跳过）：`Hero` 能查到、瞎编的类型报 not_found。
3. **反向对照**：把 not-found 判据拆掉，第 2 层必须变红。否则第 2 层是假绿。

    python bl_archive_symbols_selftest.py            # 全跑
    python bl_archive_symbols_selftest.py --offline  # 只跑第 1 层
"""
import argparse
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bl_archive_symbols as bas  # noqa: E402

FAILS = []
CHECKS = []


def check(name, condition, detail=""):
    CHECKS.append(name)
    if condition:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, detail))
        FAILS.append(name)


# ── 1. 纯函数层 ───────────────────────────────────────────────────────────

def test_symbol_to_type():
    print("\n[1] 符号拆分（不需要 Archive）")
    cases = [
        # (输入, 期望类型, 期望方法)
        ("TaleWorlds.CampaignSystem.Hero.AddSkillXp", "TaleWorlds.CampaignSystem.Hero", "AddSkillXp"),
        ("TaleWorlds.CampaignSystem.Hero", "TaleWorlds.CampaignSystem", "Hero"),
        ("Hero", "Hero", None),                       # 裸类型名是合法输入
        ("Ns.Type..ctor", "Ns.Type", None),           # 构造器
        ("Ns.Type..cctor", "Ns.Type", None),          # 静态构造器
        # ★ 泛型 arity 必须**剥掉**，而这曾是本文件里的一个错误期望。
        # 我先写成期望 `Ns.Type`1`，那是**推导**出来的，不是查出来的。
        # 实测（tools/_probe_generic_names.py，mode=ro 读 623MB 索引）：
        #     SELECT COUNT(*) FROM csharp_types WHERE typeName LIKE '%`%'  ->  0
        #     SELECT COUNT(*) FROM csharp_types                             ->  5203
        # 索引里**一个带反引号的类型名都没有**。带着反引号去查，真实类型也会报 not found，
        # 而 "not found" 读起来跟"这个类型不存在"一模一样。
        ("Ns.Type`1.Method", "Ns.Type", "Method"),    # 泛型 arity 剥掉
        ("Foo<Bar>.Baz", "Foo", "Baz"),               # 泛型实参
        ("BlBridge+0x1A2C", None, None),              # native 帧
        ("", None, None),
        (None, None, None),
    ]
    for raw, want_type, want_method in cases:
        got_type, got_method = bas.symbol_to_type(raw)
        check("symbol_to_type(%r) -> %r/%r" % (raw, want_type, want_method),
              (got_type, got_method) == (want_type, want_method),
              "got %r/%r" % (got_type, got_method))


def test_not_found_detection():
    print("\n[2] not-found 识别（这条是本工具的核心判据）")
    # 真实的 not-found 文本，逐字来自 csharp-type-reader.ts:48
    real = "Type 'TaleWorlds.CampaignSystem.Hero' was not found in the index."
    check("真实 not-found 文本被识别", bas._looks_like_not_found(real))

    # 真实的类型块：长、含 C# 结构，绝不能被误判成 not-found
    real_source = ("report_type: csharp_type_read\nquery_type_name: Hero\n"
                   "result_count: 1\n\n[result_1]\ntype_kind: class\n"
                   "public class Hero : CharacterObject\n{\n"
                   "    public void AddSkillXp(SkillObject skill, float xpAmount) { }\n}\n")
    check("真实源码块不被误判", not bas._looks_like_not_found(real_source))
    check("空文本不算 not-found（由 empty 分支处理）", not bas._looks_like_not_found(""))

    # ★ 假阴性防护：一句很长的、恰好含 not found 的源码注释，不该被判成没找到
    long_comment = ("public class Foo {\n  // this field was not found in the index of the game\n"
                    + "  public int Bar;\n" * 60 + "}\n")
    check("长源码含 'not found' 字样不被误判",
          not bas._looks_like_not_found(long_comment))


# ── 2. 真机层 ─────────────────────────────────────────────────────────────

def test_live():
    print("\n[3] 真机（起一个真实 Archive 进程）")
    out = bas.explain_symbols(["Hero"], max_source=300)
    if not out.get("ok"):
        check("Archive 可用", False, out.get("detail", "")[:200])
        return
    check("Archive 可用", True)
    row = out["rows"][0]
    check("Hero 报 ok", row.get("status") == "ok", "status=%r" % row.get("status"))
    check("Hero 带回了源码", (row.get("source_length") or 0) > 1000,
          "source_length=%r" % row.get("source_length"))
    check("Hero 不被当成 not-found", "was not found" not in (row.get("source") or "")[:200])

    print("\n[4] ★ 反向对照：瞎编的类型必须报 not_found")
    out = bas.explain_symbols(["TaleWorlds.Definitely.NotARealType"], max_source=100)
    row = out["rows"][0]
    check("不存在的类型报 not_found", row.get("status") == "not_found",
          "status=%r  ← 如果是 ok，说明 not-found 判据失效，"
          "用户会拿到一句 'not found' 当成源码" % row.get("status"))
    check("resolved 计数不把它算进去", out.get("resolved") == 0,
          "resolved=%r" % out.get("resolved"))

    print("\n[5] native 帧如实报 unresolved")
    out = bas.explain_symbols(["BlBridge+0x1A2C"], max_source=100)
    row = out["rows"][0]
    check("native 帧报 unresolved", row.get("status") == "unresolved",
          "status=%r" % row.get("status"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="只跑纯函数层")
    args = ap.parse_args()

    test_symbol_to_type()
    test_not_found_detection()
    if not args.offline:
        test_live()

    print("\n%d checks, %d failed" % (len(CHECKS), len(FAILS)))
    if FAILS:
        print("\nFAILED:")
        for f in FAILS:
            print("  - %s" % f)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    sys.exit(main())
