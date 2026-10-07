#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bl_save_diag.py 的离线自测（宿主侧，仅标准库，**不需要游戏、不需要真实存档**）。

## 为什么它不是摆设

按本仓库纪律：**没有对照组的验证不是验证，只是自证**。
每条判据都配"应报 / 不应报"的成对样本，且针对**真实会踩的坑**：

| # | 判据 | 哪种输入会红 |
|---|---|---|
| 1 | 缺框架级模组 ⇒ **critical** | 把"框架被禁用"按普通内容模组报成低风险 |
| 2 | 缺内容模组 ⇒ **warn** | 同上，反向 |
| 3 | 缺**游戏本体**模块（Native 等）⇒ **不报**（"不该报"对照） | 把随游戏版本变的基础模块报成"你漏装了" ⇒ 人人报错，结论失效 |
| 4 | 框架级**版本变化** ⇒ critical | 把框架大版本跳跃当无害 |
| 5 | 内容模组版本变化 ⇒ info | 反向：把无害的报成 critical（狼来了） |
| 6 | `isCorrupted` ⇒ critical | 漏掉游戏自己判的损坏标记 |
| 7 | 新增模组 ⇒ 只在 `addedList`，**不进** actionable | 把 20+ 个无害新增铺满报告，淹没真正的问题 |
| 8 | **计数与明细一致**（`added` == len(addedList)） | 数字写 24 而列表 23（实测踩到：base 模块被计入计数却跳过报告） |
| 9 | 只认 `IsSelected=true` 的模组 | 把"已取消勾选"当成"已启用" ⇒ 结论完全相反 |
| 10 | 没采集到存档清单时**明说原因**，不报"没有存档问题" | 把"没数据"读成"没问题" |
| 11 | ★ **反向对照**：完全一致的存档 ⇒ `worst == "ok"`、actionable 为空 | 判据恒真（把什么都报成 critical） |
| 12 | 只读声明存在且为 True | 悄悄变成会写存档的工具 |

用法：
    python tools/bl_save_diag_selftest.py [--verbose]
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

import bl_save_diag as sd                              # noqa: E402

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


def mk_save(name, mods, corrupted=False):
    meta = {}
    for k, v in mods.items():
        meta["Module_" + k] = v
    return {"name": name, "isCorrupted": corrupted, "meta": meta}


def kinds(d):
    return {i["kind"] for i in d["actionable"]}


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description="bl_save_diag 离线自测")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    VERBOSE = args.verbose
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                     # noqa: BLE001
        pass

    print("bl_save_diag 自测")
    print("=" * 74)

    tmp = tempfile.mkdtemp(prefix="bl_savediag_")
    try:
        # ── 1/2/3：缺模组按类别分级 ──
        print("\n[1][2][3] 缺模组按类别分级（框架=critical / 内容=warn / 本体=不报）")
        active = {
            "Bannerlord.Harmony": "v2.4.2.248",
            "Bannerlord.ButterLib": "v2.12.0.0",
            "Native": "v1.4.8.119303",
            "SandBoxCore": "v1.4.8.119303",
            "SomeContentMod": "v1.0.0.0",
        }
        saves = [mk_save("s1", {
            "Bannerlord.Harmony": "v2.4.2.248",
            "Bannerlord.ButterLib": "v2.12.0.0",
            "Bannerlord.UIExtenderEx": "v2.13.3.0",     # 框架级：缺 ⇒ critical
            "SomeContentMod": "v1.0.0.0",               # 两面都在
            "RemovedContentMod": "v1.2.0.0",            # 内容级：缺 ⇒ warn
            "Multiplayer": "v1.4.8.119303",             # 本体：缺 ⇒ **不该报**
        })]
        d1 = sd.diagnose(saves, active)[0]
        miss = {i["module"]: i for i in d1["actionable"] if i["kind"] == "missing_module"}
        check("1a 缺框架级模组判 critical",
              miss.get("Bannerlord.UIExtenderEx", {}).get("severity") == "critical",
              repr({k: v["severity"] for k, v in miss.items()}))
        check("2a 缺内容模组判 warn",
              miss.get("RemovedContentMod", {}).get("severity") == "warn",
              repr({k: v["severity"] for k, v in miss.items()}))
        check("3a 缺游戏本体模块**不报**（反向对照）",
              "Multiplayer" not in miss,
              "把随游戏版本变的基础模块报出来了 ⇒ 会人人报错")

        # ── 4/5：版本变化分级 ──
        print("\n[4][5] 版本变化分级（框架=critical / 内容=info）")
        saves2 = [mk_save("s2", {
            "Bannerlord.Harmony": "v2.0.0.0",           # 框架级版本变 ⇒ critical
            "Bannerlord.ButterLib": "v2.12.0.0",
            "SomeContentMod": "v0.9.0.0",               # 内容级版本变 ⇒ info
        })]
        d2 = sd.diagnose(saves2, active)[0]
        vd = {i["module"]: i for i in d2["items"] if i["kind"] == "version_changed"}
        check("4a 框架级版本变化判 critical",
              vd.get("Bannerlord.Harmony", {}).get("severity") == "critical",
              repr({k: v["severity"] for k, v in vd.items()}))
        check("5a 内容模组版本变化判 info（狼来了对照）",
              vd.get("SomeContentMod", {}).get("severity") == "info",
              repr({k: v["severity"] for k, v in vd.items()}))
        check("5b 内容版本变化**不进** actionable",
              not any(i["module"] == "SomeContentMod" for i in d2["actionable"]),
              repr([i["module"] for i in d2["actionable"]]))

        # ── 6：损坏标记 ──
        print("\n[6] isCorrupted ⇒ critical")
        d3 = sd.diagnose([mk_save("bad", {"Bannerlord.Harmony": "v2.4.2.248"}, corrupted=True)],
                         active)[0]
        check("6a 损坏标记进 actionable",
              any(i["kind"] == "corrupted_flag" for i in d3["actionable"]),
              repr(kinds(d3)))
        check("6b worst=critical", d3["worst"] == "critical", d3["worst"])

        # ── 7/8：新增模组折叠 + 计数一致 ──
        print("\n[7][8] 新增模组折叠展示 + 计数与明细一致")
        many = {"Bannerlord.Harmony": "v2.4.2.248", "Bannerlord.ButterLib": "v2.12.0.0"}
        for i in range(20):
            many["NewMod%02d" % i] = "v1.0.0.0"
        many["Native"] = "v1.4.8.119303"                 # 本体：不应计入新增
        d4 = sd.diagnose([mk_save("s4", {
            "Bannerlord.Harmony": "v2.4.2.248",
            "Bannerlord.ButterLib": "v2.12.0.0",
        })], many)[0]
        check("7a 新增模组**不进** actionable",
              all(i["kind"] != "added_module" for i in d4["actionable"]),
              repr(kinds(d4)))
        check("7b 新增模组进 addedList", len(d4["addedList"]) >= 20,
              "addedList=%d" % len(d4["addedList"]))
        check("8a added 计数 == addedList 长度（实测踩过：base 计入却不报告）",
              d4["added"] == len(d4["addedList"]),
              "added=%d len=%d" % (d4["added"], len(d4["addedList"])))
        check("8b items == actionable + addedList",
              len(d4["items"]) == len(d4["actionable"]) + len(d4["addedList"]),
              "items=%d act=%d add=%d" % (len(d4["items"]), len(d4["actionable"]),
                                          len(d4["addedList"])))
        check("8c 本体模块不计入新增",
              all("Native" != a["module"] for a in d4["addedList"]),
              repr([a["module"] for a in d4["addedList"]][:5]))

        # ── 9：只认 IsSelected=true ──
        print("\n[9] LauncherData 只认 IsSelected=true（不筛会结论相反）")
        lp = os.path.join(tmp, "LauncherData.xml")
        with io.open(lp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("""<?xml version="1.0" encoding="utf-8"?>
<UserData><SingleplayerData><ModDatas>
  <UserModData><Id>EnabledMod</Id><LastKnownVersion>v1.0.0.0</LastKnownVersion><IsSelected>true</IsSelected></UserModData>
  <UserModData><Id>DisabledMod</Id><LastKnownVersion>v2.0.0.0</LastKnownVersion><IsSelected>false</IsSelected></UserModData>
</ModDatas></SingleplayerData></UserData>
""")
        am = sd.read_active_modules(lp)
        check("9a 启用的被读到", "EnabledMod" in am["modules"], repr(am["modules"]))
        check("9b 取消勾选的**不**被当成启用", "DisabledMod" not in am["modules"],
              "把 IsSelected=false 当启用了 ⇒ 诊断结论完全相反")
        check("9c total 统计了全部项", am["total"] == 2, str(am["total"]))
        check("9d selected 只数启用的", am["selected"] == 1, str(am["selected"]))

        # ── 10：没有清单时明说原因 ──
        print("\n[10] 没采集到存档清单 ⇒ 明说原因，不报「没有存档问题」")
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        res10 = sd.build_report(log_dir=empty, launcher_path=lp)
        t10 = sd._render(res10)
        check("10a found=False", res10["saveListSource"]["found"] is False)
        check("10b 明说「不代表没有问题」", "不代表" in t10, t10[:300])
        check("10c 给出下一步（先跑 bl_list_saves）", "bl_list_saves" in t10, t10[:400])

        # ── 11：反向对照 —— 完全一致 ⇒ ok ──
        print("\n[11] 反向对照：完全一致的存档 ⇒ worst=ok、actionable 空")
        same = dict(active)
        d11 = sd.diagnose([mk_save("clean", same)], active)[0]
        check("11a worst=ok", d11["worst"] == "ok", d11["worst"])
        check("11b actionable 为空（判据不是恒真）", len(d11["actionable"]) == 0,
              repr([i["kind"] for i in d11["actionable"]]))
        check("11c items 也为空", len(d11["items"]) == 0, str(len(d11["items"])))

        # ── 12：只读声明 ──
        print("\n[12] 只读声明")
        r12 = sd.build_report(log_dir=empty, launcher_path=lp)
        check("12a readOnly=True", r12.get("readOnly") is True, repr(r12.get("readOnly")))
        check("12b semantics 里写明不写存档",
              "只读" in (r12["semantics"].get("readOnly") or ""), repr(r12["semantics"].get("readOnly")))

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
