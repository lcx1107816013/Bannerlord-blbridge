#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bl_source_map.py 的离线自测（宿主侧，仅标准库，**不需要游戏**）。

## 为什么它不是摆设

按本仓库纪律：**没有对照组的验证不是验证，只是自证**。
每条判据都配"应报 / 不应报"的成对样本，且针对**真实踩过的坑**：

| # | 判据 | 哪种输入会红 |
|---|---|---|
| 1 | **中文栈**那行号（`位置 /src/X.cs:行号 24`） | 只认英文 `:line` ⇒ 中文系统上"有行号"判成"没有"（**实测踩过**） |
| 2 | **英文栈**那行号（`in /src/X.cs:line 24`） | 反向：只顾中文，英文全瘫 |
| 3 | 与语言无关：**第三种形态**（无关键词，`/src/X.cs:24`） | 判据绑死在某个词上 |
| 4 | **无行号栈**不谎报（`hasLine=False`、line=None） | 凭空造一个行号（比"不知道"更坏） |
| 5 | 第三方帧 ⇒ `locatedBy=None` + 说明"第三方无源码" | 报成"索引未覆盖"（误导：让人以为加索引就能解决） |
| 6 | 自己的帧 ⇒ 走符号索引定位成功 | 有能力却定位不到 |
| 7 | 索引命中 ⇒ 能读出**真实源码片段**，且 `>>` 指向该行 | 给个行号但给不出上下文，agent 还是要瞎找 |
| 8 | 索引里没有的方法 ⇒ 如实说"未覆盖"，**不编造** | 返回一个看似合理的位置 |
| 9 | 索引缺失时**明说**要生成（给命令） | 静默返回空，调用方以为"没有可定位的帧" |
| 10 | ★ **反向对照**：纯第三方栈 ⇒ 全部"无法定位"，但**不报错**（正常结果） | 判据恒真 / 或把正常情况当失败 |
| 11 | 路径含 Windows 反斜杠也能解析 | 只处理 `/` |
| 12 | 只读：不修改任何源文件 | 定位顺手改了源码 |

用法：
    python tools/bl_source_map_selftest.py [--verbose]
退出码：0 = 全过；1 = 有失败。
"""
import argparse
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bl_source_map as sm                          # noqa: E402

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


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description="bl_source_map 离线自测")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    VERBOSE = args.verbose
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                     # noqa: BLE001
        pass

    print("bl_source_map 自测")
    print("=" * 74)

    tmp = tempfile.mkdtemp(prefix="bl_srcmap_")
    try:
        # ── 1/2/3：三种语言形态都要能解析出同一个行号 ──
        print("\n[1][2][3] 语言无关的行号提取（中/英/无关键词三种形态）")
        zh = "   在 Foo.Bar() 位置 /src/X.cs:行号 24"
        en = "   at Foo.Bar() in /src/X.cs:line 24"
        raw = "   at Foo.Bar() /src/X.cs:24"
        for label, line, expect in (("1a 中文形态", zh, 24), ("2a 英文形态", en, 24),
                                    ("3a 无关键词形态", raw, 24)):
            fr = sm.parse_frame(line)
            check(label, fr["line"] == expect,
                  "line=%r file=%r（若只认英文关键词，中文形态会失败）" % (fr["line"], fr["file"]))
        check("3b 三种形态给出同一个行号",
              len({sm.parse_frame(x)["line"] for x in (zh, en, raw)}) == 1,
              "不同形态解析结果不一致")
        check("3c 都能识别出方法名",
              all(sm.parse_frame(x)["method"] == "Foo.Bar" for x in (zh, en, raw)),
              repr([sm.parse_frame(x)["method"] for x in (zh, en, raw)]))

        # ── 4：无行号不谎报 ──
        print("\n[4] 无行号栈不谎报")
        fr4 = sm.parse_frame("   at Foo.Bar()")
        check("4a hasLine=False", fr4["hasLine"] is False, repr(fr4))
        check("4b line=None（不是 0，也不是编造的数字）", fr4["line"] is None, repr(fr4["line"]))
        check("4c 方法名仍识别", fr4["method"] == "Foo.Bar", repr(fr4["method"]))

        # ── 11：Windows 反斜杠 ──
        print("\n[11] Windows 路径（反斜杠）")
        fr11 = sm.parse_frame(r"   at Foo.Bar() in E:\repo\src\X.cs:line 77")
        check("11a 反斜杠路径可解析", fr11["line"] == 77, repr(fr11))
        check("11b 文件名取 basename", fr11["file"] == "X.cs", repr(fr11["file"]))

        # ── 5/6：第三方 vs 自己的帧 ──
        print("\n[5][6] 第三方帧 vs 自有帧（如实降级 / 精确定位）")
        stack = ("at HarmonyLib.PatchTools.GetOriginalMethod(HarmonyMethod attr)\n"
                 "at BlBridge.CrashGuard.Finalizer(System.Exception e, "
                 "System.Reflection.MethodBase m)\n"
                 "at TaleWorlds.MountAndBlade.Mission.Tick(Single dt)")
        res = sm.analyze(stack_text=stack)
        frames = [f for e in res["frames"] for f in e["located"]]
        check("5a 至少识别出 3 帧", len(frames) >= 3, "frames=%d" % len(frames))
        third = [f for f in frames if "HarmonyLib" in (f["method"] or "")
                 or "TaleWorlds" in (f["method"] or "")]
        check("5b 第三方帧标为 thirdParty", all(f["thirdParty"] for f in third),
              repr([(f["method"], f["thirdParty"]) for f in third]))
        # ⚠️ 这两条断言**已按新能力更新**（2026-10-07）：
        #    它们写于"只有我们自己索引"的时代，当时要求第三方帧一律无法定位。
        #    现在有了**第三方 PDB 索引**（新能力），能定位的**应该**给位置 ——
        #    断言若仍要求 None，反而会把改进判成失败。
        #    新的判据是：**要么如实定位（带程序集名），要么如实说无法定位**，
        #    **但绝不能给出我们自己的源码片段**（那才是错的方向）。
        check("5c 第三方帧要么定位要么如实说无法定位（不能沉默给假位置）",
              all(f["locatedBy"] in ("thirdparty", None) for f in third),
              repr([(f["method"], f["locatedBy"]) for f in third]))
        check("5d 第三方帧**绝不**附我们的源码片段",
              all(not f.get("snippet") for f in third),
              "★ 给了片段 ⇒ 极可能是我们的同名文件（如 SubModule.cs）")
        undecided = [f for f in third if f["locatedBy"] is None]
        check("5e 未定位的第三方帧都给了原因",
              all(bool(f.get("reason")) for f in undecided),
              repr([(f["method"], f.get("reason")) for f in undecided]))
        # 自有帧：若索引在，必须能定位
        own = [f for f in frames if "BlBridge.CrashGuard.Finalizer" in (f["method"] or "")]
        if res["indexAvailable"]:
            check("6a 自有帧定位成功（符号索引）",
                  own and own[0]["locatedBy"] == "symbols" and own[0].get("line"),
                  repr(own[0] if own else None))
            check("7a 给出源码片段", bool(own and own[0].get("snippet")),
                  "没给片段 ⇒ agent 拿不到上下文")
            if own and own[0].get("snippet"):
                txt = own[0]["snippet"]["text"]
                check("7b 片段里有 >> 标记且指向该行",
                      ">>" in txt and str(own[0]["line"]) in txt, txt[:200])
        else:
            print("  [skip] 无符号索引 ⇒ 自有帧的 6a/7a/7b 跳过（见判据 9）")

        # ── 8：索引里没有的方法 ⇒ 如实说"未覆盖" ──
        print("\n[8] 索引未覆盖的方法不编造位置")
        res8 = sm.analyze(stack_text="at BlBridge.NoSuchMethodXyz()")
        f8 = res8["frames"][0]["located"][0]
        check("8a locatedBy=None", f8["locatedBy"] is None, repr(f8["locatedBy"]))
        check("8b 没有 file/line 字段被填", not f8.get("file") and not f8.get("line"),
              repr((f8.get("file"), f8.get("line"))))
        check("8c 给了可读原因", bool(f8.get("reason")), repr(f8.get("reason")))

        # ── 9：索引缺失时明说 + 给命令 ──
        print("\n[9] 索引缺失时明说并给生成命令")
        idx_backup = None
        idxp = sm.INDEX_PATH
        try:
            if os.path.isfile(idxp):
                idx_backup = idxp + ".selftest_bak"
                shutil.move(idxp, idx_backup)
            res9 = sm.analyze(stack_text="at BlBridge.CrashGuard.Finalizer()")
            check("9a indexAvailable=False", res9["indexAvailable"] is False, repr(res9["indexAvailable"]))
            joined = " ".join(res9.get("notes") or [])
            check("9b 提示里给出生成命令", "bl_symbols.py" in joined, joined[:200])
            check("9c 明说要 PDB", "PDB" in joined, joined[:200])
            t9 = sm._render(res9)
            check("9d 渲染里出现'不可用'", "不可用" in t9, t9[:200])
        finally:
            if idx_backup:
                shutil.move(idx_backup, idxp)

        # ── 10：纯第三方栈 —— 反向对照 ──
        print("\n[10] 反向对照：纯第三方栈 ⇒ 不报错，且行为如实")
        res10 = sm.analyze(stack_text="at HarmonyLib.Harmony.PatchAll()\n"
                                      "at System.RuntimeMethodHandle.InvokeMethod()")
        check("10a ok=True（正常结果，不算失败）", res10["ok"] is True, repr(res10["ok"]))
        f10 = [f for e in res10["frames"] for f in e["located"]]
        check("10b 全部标为第三方", all(f["thirdParty"] for f in f10),
              repr([(f["method"], f["thirdParty"]) for f in f10]))
        # ⚠️ 同 5c 的理由：有了第三方 PDB 索引后，"能定位"是**改进**而非缺陷。
        #    真实判据是：要么如实定位（thirdparty），要么如实说无法定位（None + reason）。
        check("10c 每帧都有如实的处置（定位 或 带原因的无法定位）",
              all(f["locatedBy"] == "thirdparty"
                  or (f["locatedBy"] is None and f.get("reason")) for f in f10),
              repr([(f["method"], f["locatedBy"], f.get("reason")) for f in f10]))
        check("10d 过滤掉了 System.*（BCL 非我们工程）",
              all(f["thirdParty"] for f in f10 if "System." in (f["method"] or "")),
              "BCL 帧没被认成第三方")
        check("10e 第三方帧无我们的源码片段",
              all(not f.get("snippet") for f in f10), "给了片段")

        # ── 14：★ 第三方定位 —— 三条"假行号"缺陷的对照判据 ──
        print("\n[14] 第三方定位（含三条假行号缺陷的对照）")
        tp_idx = sm._thirdparty_indexes()
        if not tp_idx:
            print("  [skip] 无第三方索引 ⇒ 跑 `python tools/bl_symbols.py --third-party` 后再测")
        else:
            # 14a 归属判定：白名单，只有 BlBridge.* 才算我们的
            check("14a _is_our_frame 白名单（BlBridge 是，RBM 不是）",
                  sm._is_our_frame("BlBridge.SubModule.OnApplicationTick")
                  and not sm._is_our_frame("RBM.SubModule.OnSubModuleLoad"),
                  "黑名单式判定会把 RBM 当成我们的 ⇒ 读错源码")

            # 14b ★ 文件名撞车：第三方帧**绝不能**配上我们的源码片段
            #     实测：SubModule.cs 在 RBM/Bloodlust/BellumCivile 等**每个**第三方索引里都有，
            #     而我们的主入口同名 ⇒ 无条件按 basename 取就会给错代码。
            stack_tp = "at RBM.SubModule.OnSubModuleLoad() 位置 /src/SubModule.cs:行号 86"
            res_tp = sm.analyze(stack_text=stack_tp)
            f_tp = res_tp["frames"][0]["located"][0]
            check("14b-1 第三方帧不给我们的源码片段",
                  f_tp.get("snippet") is None,
                  "★ 给了片段 ⇒ 可能是我们的 src/SubModule.cs（错代码）")
            check("14b-2 第三方帧附说明为何无片段",
                  bool(f_tp.get("sourceNote")), repr(f_tp.get("sourceNote")))
            # 反向对照：我们自己的帧**必须**给片段
            stack_own = ("at BlBridge.SubModule.OnApplicationTick(Single dt) "
                         "位置 /src/SubModule.cs:行号 316")
            res_own = sm.analyze(stack_text=stack_own)
            f_own = res_own["frames"][0]["located"][0]
            check("14b-3 对照：自有帧仍给片段",
                  bool(f_own.get("snippet")), "自有帧也没片段 ⇒ 修过头了")

            # 14c ★ 同名方法的跨程序集碰撞：RBM 的 OnSubModuleLoad 不能被
            #     解析成 BlBridge.SubModule.OnSubModuleLoad
            e = sm.locate("RBM.SubModule.OnSubModuleLoad", sm.load_index())
            check("14c-1 查索引时不把 RBM 方法配到我们的同名方法（旧缺陷）",
                  e is None or not (e.get("method") or "").startswith("BlBridge."),
                  "配到了 %r ⇒ 会给我们的 SubModule.cs 行号" % (e,))
            tpm, cands = sm.locate_thirdparty("RBM.SubModule.OnSubModuleLoad")
            check("14c-2 走第三方索引能拿到 RBM 自己的位置",
                  tpm is not None and tpm.get("assembly") == "rbm",
                  repr(tpm))

            # 14d ★ 不可信行号（实测第三方 PDB 里有 16707566）必须被拦
            check("14d-1 _line_is_plausible 拒绝离谱行号",
                  (not sm._line_is_plausible(16707566)) and sm._line_is_plausible(86)
                  and (not sm._line_is_plausible(0)) and (not sm._line_is_plausible(None)),
                  "上界判据失灵 ⇒ 会把 16707566 当成位置报出去")
            stack_bad = ("at BellumCivile.Behaviors.FeudalTitleBehavior."
                         "TryFinalizeCrownPromotionHierarchy()")
            res_bad = sm.analyze(stack_text=stack_bad)
            f_bad = res_bad["frames"][0]["located"][0]
            if f_bad.get("corruptLine"):
                check("14d-2 损坏行号被如实说明且不报位置",
                      f_bad.get("locatedBy") is None and not f_bad.get("line")
                      and "不可信" in (f_bad.get("reason") or ""),
                      repr(f_bad))

            # 14e 版本歧义：不猜
            check("14e locate_thirdparty 返回 (best, candidates) 二元组",
                  isinstance(tpm, (dict, type(None))) and isinstance(cands, list),
                  "接口形状变了")

            # ── 14f ★ 游戏版本消歧（v0.8.51）：两种命名形态都要认 ──
            #
            # 立项理由（实测 2026-10-07）：一个 mod 目录里可能同时放 42 个版本的
            # dll+pdb，文件名里的版本号是**游戏版本**（供 ModuleLoader 按 LoaderFilter 挑），
            # 不是 mod 版本 —— 实测各版本 FileVersion 相同，但**同一方法行号不同**
            # （144 vs 149）。不消歧就会有大量真实方法以"歧义"被拒。
            print("\n[14f] 游戏版本消歧（两种命名形态）")
            try:
                import bl_symbols as sb
            except Exception:                             # noqa: BLE001
                sb = None
            if sb is None:
                print("  [skip] 取不到 bl_symbols")
            else:
                gv = sb.game_version()
                check("14f-1 读得到游戏版本（形如 1.4.8）",
                      bool(gv) and gv.count(".") == 2, repr(gv))
                # 人工造两种形态的索引名，验证都能被剥成同一个 stem
                # ⚠️ 夹具必须**含游戏版本对应的那一份**（否则消歧无从匹配 ——
                #    首版夹具只放了 v1.4.0/v1.5.1，却断言 gv=1.4.8 能选中 v1.4.8，
                #    那是不可能成立的；测试数据写错又会把正确实现判成失败）。
                fake = {"bannerlord.butterlib.implementation.1.4.8": {},
                        "bannerlord.butterlib.implementation.1.3.15": {},
                        "bannerlord.mboptionscreen.v1.4.8": {},
                        "bannerlord.mboptionscreen.v1.4.0": {},
                        "bannerlord.mboptionscreen.v1.5.1": {},
                        "rbm": {}}
                pref, _dropped = sb.preferred_index_names(fake, gv or "1.4.8")
                check("14f-2 点号形态被消歧（...implementation.1.4.8）",
                      pref.get("bannerlord.butterlib.implementation")
                      == "bannerlord.butterlib.implementation.1.4.8",
                      repr(pref))
                check("14f-3 ★ `v` 前缀形态也被消歧（...mboptionscreen.v1.4.8）",
                      pref.get("bannerlord.mboptionscreen") == "bannerlord.mboptionscreen.v1.4.8",
                      "首版只认纯数字尾段 ⇒ MCM 的 11 个版本一个都没消歧，"
                      "全部以'歧义'被拒（实测把覆盖率提升几乎全吃掉）")
                check("14f-4 不带版本号的索引不受影响（rbm）",
                      "rbm" not in pref, repr(pref))
                # _versioned_keys 也要认 v 前缀（否则 pool 收窄时会把它们漏掉）
                vk = sm._versioned_keys(fake)
                check("14f-5 _versioned_keys 认 v 前缀",
                      "bannerlord.mboptionscreen.v1.4.0" in vk and "rbm" not in vk,
                      repr(sorted(vk)))

        # ── 12：只读 ──
        print("\n[12] 只读（不修改源文件）")
        before = {}
        for fn in os.listdir(sm.SRC_DIR):
            if fn.endswith(".cs"):
                p = os.path.join(sm.SRC_DIR, fn)
                before[fn] = io.open(p, "rb").read()
        sm.analyze(stack_text="at BlBridge.CrashGuard.Finalizer()")
        sm.analyze(resolve="BlBridge.CrashGuard.Install")
        after = {}
        for fn in before:
            p = os.path.join(sm.SRC_DIR, fn)
            after[fn] = io.open(p, "rb").read()
        check("12a 源文件字节未变", before == after, "定位过程改了源码")

        # ── 补充：--resolve 命中/未命中 ──
        print("\n[13] --resolve 命中与未命中")
        r_ok = sm.analyze(resolve="BlBridge.CrashGuard.Install")
        if r_ok["indexAvailable"]:
            check("13a 命中的方法给出文件:行", bool(r_ok.get("resolved")), repr(r_ok.get("resolved")))
            check("13b 附源码片段", bool(r_ok.get("snippet")), "无片段")
        r_no = sm.analyze(resolve="Totally.Made.Up.Method")
        check("13c 未命中 ⇒ resolved=None", r_no.get("resolved") is None, repr(r_no.get("resolved")))
        check("13d 未命中时提示别编造位置",
              any("不要" in n and "编造" in n for n in (r_no.get("notes") or [])),
              repr(r_no.get("notes")))

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
