#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
存档诊断（Save Diagnosis）—— 宿主侧，**只读**，仅标准库。

## 它做什么

比对「存档里记录的模组集」与「当前启动器启用的模组集」，找出**会导致读档崩溃的不一致**：

  ① 存档需要、但当前**没启用**的模组（最常见：卸载 MOD 后读旧档 ⇒ NullReference）
  ② 存档需要、但**版本变了**的模组（框架级 mod 换版本常破坏序列化）
  ③ 存档没有、但当前**新加了**的模组（通常无害，但战役类 mod 可能注入数据）
  ④ 存档自身的 `isCorrupted` 标记
  ⑤ 存档文件本体是否可读（大小/头部）

## ★★ 本工具**只读**，绝不写存档

这是刻意的设计红线，不是"暂时没做"：

- 写存档的代价是**不可逆的**（覆盖即丢失原档）；
- 「存档修复」是上游 mod 里**最重、最难验证**的部分（它要改存档内部对象图）；
- 而我们**没有**独立验证"修复后存档仍语义正确"的能力 ——
  能读到"文件能打开"**不等于**"战役数据没被改坏"。
⇒ 所以本工具只给**判断与建议**，把"改不改、怎么改"交回给人。
  要备份时它**只提示**，不代替你操作。

## 数据来源

| 输入 | 从哪来 | 怎么拿 |
|---|---|---|
| 存档清单 + `isCorrupted` + `Module_*` 元数据 | 游戏内 `MBSaveLoad.GetSaveFiles` | 控制通道 `list_saves`（已有） |
| 当前启用的模组集 | `Configs/LauncherData.xml` | 直接读该 XML（纯文件，不需要游戏在跑） |

⇒ 两者都是**只读**：前者是游戏已有的只读接口，后者是一个配置文件。

## 口径（读结论前必看）

1. **"存档缺模组"不等于"一定崩"** —— 只说明**风险**：
   那个 mod 的数据还在存档里，但代码不在 ⇒ 读到它时才炸（往往在进入战役/战斗后）。
2. **"版本不同"也不等于"一定不安全"** —— 框架级（Harmony/ButterLib/MCM/UIExtenderEx）
   换版本风险高；纯资源包换版本通常无害。本工具**按类别标风险**，不搞一刀切。
3. **本工具不判断存档内容是否"坏了"** —— 只看元数据与文件可读性。
   真正的存档内部损坏需要读档时才知道（那属于 `bl_crash` / 异常账本的领域）。
"""
import io
import json
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bl_common                                      # noqa: E402

# 框架级模组：换版本**高风险**（它们参与序列化/补丁/UI，版本漂移常破坏存档兼容）
FRAMEWORK_PREFIXES = (
    "bannerlord.harmony", "bannerlord.butterlib", "bannerlord.uiextenderex",
    "bannerlord.mboptionscreen", "bannerlord.mcm",
)

# 游戏本体模块：存档里必有，且**随游戏版本**变化 ⇒ 不算"不一致"（否则人人报错）
BASE_MODULES = {
    "native", "sandboxcore", "sandbox", "custombattle", "storymode",
    "birthanddeath", "multiplayer", "navaldlc", "fastmode",
    # ⚠️ **不要把 Bannerlord.Harmony 放进这里**（实测踩到，2026-10-07）：
    #    它同时在 FRAMEWORK_PREFIXES 里，而本函数按"先 base 后 framework"判 ⇒
    #    base 胜出 ⇒ 框架级版本漂移被降级成 info，**漏报真实高风险**。
    #    它虽然常随游戏一起分发，但**换版本会破坏存档兼容**，必须按框架级处理。
    #    （自测判据 4a 抓到了这条。）
}


def launcher_data_path(docs=None):
    """`Configs/LauncherData.xml`。"""
    d = docs or os.path.join(
        os.environ.get("USERPROFILE") or os.path.expanduser("~"),
        "Documents", "Mount and Blade II Bannerlord")
    return os.path.join(d, "Configs", "LauncherData.xml")


def read_active_modules(path=None):
    """读当前**已启用**的模组 → {Id: LastKnownVersion}。

    ⚠️ 只认 `IsSelected` 为 true 的项 —— LauncherData.xml 里**保留了全部**曾出现过的模组
       （含已取消勾选的），不筛就会把"已禁用"当成"已启用"，那诊断结论会完全相反。
       实测（2026-10-07）：该文件里 `<UserModData>` 有多组，`IsSelected` 才是开关。
    """
    p = path or launcher_data_path()
    res = {"path": p, "exists": os.path.isfile(p), "modules": {}, "total": 0, "selected": 0}
    if not res["exists"]:
        return res
    try:
        root = ET.parse(p).getroot()
    except Exception:                                     # noqa: BLE001
        return res
    for um in root.iter("UserModData"):
        mid = None
        ver = None
        sel = None
        for ch in um:
            t = ch.tag
            if t == "Id":
                mid = (ch.text or "").strip()
            elif t == "LastKnownVersion":
                ver = (ch.text or "").strip()
            elif t == "IsSelected":
                sel = (ch.text or "").strip().lower() == "true"
        if mid is None:
            continue
        res["total"] += 1
        if sel:
            res["selected"] += 1
            res["modules"][mid] = ver or ""
    return res


def load_saves_from_log(log_dir=None):
    """从控制通道历史响应里取最近一次 `list_saves` 的结果（只读）。

    ★ 为什么不能"扫最新 N 个文件"（实测踩到，2026-10-07）：
      `<LogDir>\\commands\\done\\` 里有 **13622 个**响应文件，而含 `isCorrupted` 的
      只有 **13 个**、且**都不在最新 400 个里**。首版按 mtime 取最新 400 个 ⇒
      一条都找不到，却报告"没有存档清单" —— 一个**看起来像环境不足的实现缺陷**。
      （`done/*.json` 的内容与它上面那层目录的时间都不保证与"调用顺序"相关。）

    ⇒ 改成走**账本** `commands/actions.jsonl`：它每行一次调用（含 `method`），
      本来就是为"检索历史响应"设计的（`bl_ipc_replay` 同源）。
      从账本里筛 `list_saves` 的 id，再去 `done/<id>.json` 取响应。
      账本不在时才退回有限扫描（并如实说明覆盖范围）。
    """
    import bl_common as _bc
    done = os.path.join(_bc.commands_dir(log_dir), "done")
    out = {"found": False, "dir": done, "saves": [], "responseFile": None,
           "source": None, "scanned": 0}
    if not os.path.isdir(done):
        return out

    ids = []
    # ① 首选：动作账本（精确、按时间序、便宜）
    ledger = _bc.actions_path(log_dir)
    if os.path.isfile(ledger):
        try:
            recs, _ = _bc.load_actions(ledger)
            for r in reversed(recs):                      # 从最近往回找
                if (r or {}).get("method") == "list_saves" and r.get("ok"):
                    rid = r.get("id")
                    if rid:
                        ids.append(rid)
                    if len(ids) >= 8:
                        break
            out["source"] = "actions.jsonl"
        except Exception:                                 # noqa: BLE001
            pass

    # ② 退回：扫 done（覆盖面有限，如实标注）
    if not ids:
        try:
            files = sorted(os.listdir(done))
        except Exception:                                 # noqa: BLE001
            files = []
        out["source"] = "done-scan"
        out["scanned"] = len(files)
        # 响应文件名 = <id>.json；这里只能靠"内容里有没有 isCorrupted"来认，
        # 所以必须**全量**解析才算可靠（13622 个文件约 1 秒级，可接受）
        for fn in files:
            if not fn.endswith(".json"):
                continue
            rid = fn[:-5]
            ids.append(rid)

    for rid in ids[:4000]:
        fp = os.path.join(done, rid + ".json")
        if not os.path.isfile(fp):
            continue
        try:
            with io.open(fp, "r", encoding="utf-8-sig", errors="replace") as fh:
                o = json.load(fh)
        except Exception:                                 # noqa: BLE001
            continue
        res = o.get("result") if isinstance(o, dict) and "result" in o else o
        if not isinstance(res, dict):
            continue
        saves = res.get("saves")
        if isinstance(saves, list) and saves and isinstance(saves[0], dict) \
                and "isCorrupted" in saves[0]:
            out["found"] = True
            out["saves"] = saves
            out["responseFile"] = rid + ".json"
            return out
    return out


def _mods_from_save(save):
    """把存档 meta 里的 `Module_*` 抽成 {Id: Version}。"""
    meta = save.get("meta") or {}
    mods = {}
    for k, v in meta.items():
        if k.startswith("Module_"):
            mods[k[len("Module_"):]] = v or ""
    return mods


def _norm(s):
    return (s or "").strip().lower()


def _classify(mid):
    """把模组 id 分类成 base / framework / content。

    ★ **判定顺序是 framework 优先**（实测教训，2026-10-07）：
      `Bannerlord.Harmony` 既属本体（随游戏分发）又属框架（参与补丁/序列化）。
      若先判 base，它就被降成"随游戏版本变、忽略" ⇒ **框架级版本漂移被漏报**。
      自测判据 4a 抓到过这条 ⇒ 顺序不能反。
    """
    n = _norm(mid)
    if any(n.startswith(p) for p in FRAMEWORK_PREFIXES):
        return "framework"
    if n in BASE_MODULES:
        return "base"
    return "content"


def diagnose(saves, active, limit=None):
    """对每个存档给出诊断条目（风险分级）。"""
    results = []
    active_l = {_norm(k): (k, v) for k, v in active.items()}

    for s in saves:
        name = s.get("name") or "(无名)"
        save_mods = _mods_from_save(s)
        items = []

        # ① 损坏标记
        if s.get("isCorrupted"):
            items.append({
                "severity": "critical", "kind": "corrupted_flag",
                "text": "存档被标记为**已损坏**（游戏自己判的）",
                "advice": "不要覆盖它；先备份，再尝试读档看具体报错（配 bl_crash / bl_crashguard 归因）。",
            })

        save_l = {_norm(k): (k, v) for k, v in save_mods.items()}
        missing = [k for k in save_l if k not in active_l]
        version_diff = [k for k in save_l if k in active_l
                        and save_l[k][1] and active_l[k][1]
                        and save_l[k][1] != active_l[k][1]]
        added = [k for k in active_l if k not in save_l]

        # ② 存档需要但当前没启用（最危险）
        for k in missing:
            mid, ver = save_l[k]
            kind = _classify(mid)
            sev = "base" if kind == "base" else ("critical" if kind == "framework" else "warn")
            if kind == "base":
                advice = ("游戏本体模块（随游戏版本变化，通常不算不一致）—— "
                          "但若你**降级/升级过游戏版本**，存档可能确实不兼容。")
            elif kind == "framework":
                advice = ("框架级前置被禁用 ⇒ **高风险**：它的数据还在存档里，"
                          "而代码不在 ⇒ 常在进入战役/战斗后才炸。"
                          "要么重新启用它，要么读更早的、创建于禁用之前的存档。")
            else:
                advice = ("该模组存档里有、当前没启用 ⇒ 它保存的对象可能读不出来。"
                          "若读档报 NullReference / KeyNotFound，优先重新启用它，"
                          "或用创建于卸载之前的存档。")
            items.append({
                "severity": sev, "kind": "missing_module",
                "module": mid, "versionInSave": ver, "class": kind,
                "text": "存档需要但当前**未启用**：%s (%s)" % (mid, ver or "版本未知"),
                "advice": advice,
            })

        # ③ 版本不一致
        for k in version_diff:
            mid, v_save = save_l[k]
            v_now = active_l[k][1]
            kind = _classify(mid)
            sev = "critical" if kind == "framework" else "info"
            if kind == "framework":
                advice = ("框架级模组版本变了 ⇒ **高风险**（它参与序列化/补丁）："
                          "换回原版本，或用改版之前的存档。")
            else:
                advice = "内容模组换版本通常无害；若读档报错再考虑回退版本。"
            items.append({
                "severity": sev, "kind": "version_changed",
                "module": mid, "versionInSave": v_save, "versionNow": v_now, "class": kind,
                "text": "版本不一致：%s 存档=%s / 当前=%s" % (mid, v_save, v_now),
                "advice": advice,
            })

        # ④ 当前新加、存档里没有（通常无害）
        n_added_reported = 0
        for k in added:
            mid, v_now = active_l[k]
            kind = _classify(mid)
            if kind == "base":
                # 游戏本体模块随游戏版本变化，不算"你新增的模组" ⇒ 不计入、不报告。
                # ⚠️ 这里必须**跳过计数**：早先 `added` 用原始列表长度，于是
                #    "新增 24"与列表里实际 23 条不符（差的那个就是 base 模块）——
                #    数字与明细对不上会让读者怀疑整份报告。
                continue
            n_added_reported += 1
            items.append({
                "severity": "info", "kind": "added_module",
                "module": mid, "versionNow": v_now, "class": kind,
                "text": "当前启用了、但存档创建时没有：%s (%s)" % (mid, v_now or "版本未知"),
                "advice": ("多数情况无害（新模组只是没被存档记录）。"
                           "但注入战役数据的 mod 可能改变读档后的世界状态 —— "
                           "若读档后行为异常，优先怀疑它。"),
            })

        order = {"critical": 0, "warn": 1, "info": 2}
        items.sort(key=lambda x: order.get(x["severity"], 3))
        results.append({
            "name": name,
            "isCorrupted": bool(s.get("isCorrupted")),
            "modulesInSave": len(save_mods),
            "missing": len(missing),
            "versionChanged": len(version_diff),
            # ★ 用**实际报告条数**，不用原始列表长度 —— 保证数字与明细一致
            "added": n_added_reported,
            "worst": ("critical" if any(i["severity"] == "critical" for i in items)
                      else ("warn" if any(i["severity"] == "warn" for i in items)
                            else ("info" if items else "ok"))),
            # ★ `actionable` = 需要**动手**的条目（critical/warn）。
            #   `added_module` 全是 info，单独归到 `addedList` 里**折叠展示** ——
            #   实测踩到：24 个"新增模组"逐条铺开会把真正的 critical 淹掉，
            #   而它们绝大多数无害（`advice` 也这么说）。信息设计缺陷，不是数据缺陷。
            "actionable": [i for i in items if i["severity"] in ("critical", "warn")],
            "addedList": [i for i in items if i["kind"] == "added_module"],
            "items": items,
        })

    results.sort(key=lambda r: {"critical": 0, "warn": 1, "info": 2, "ok": 3}.get(r["worst"], 4))
    return results[:limit] if limit else results


def build_report(log_dir=None, launcher_path=None, limit=None):
    active = read_active_modules(launcher_path)
    savesrc = load_saves_from_log(log_dir)
    diag = diagnose(savesrc["saves"], active["modules"], limit=limit)
    return {
        "ok": True,
        "readOnly": True,
        "activeModules": {
            "path": active["path"], "exists": active["exists"],
            "total": active["total"], "selected": active["selected"],
            "count": len(active["modules"]),
        },
        "saveListSource": {
            "found": savesrc["found"], "dir": savesrc["dir"],
            "responseFile": savesrc["responseFile"], "count": len(savesrc["saves"]),
        },
        "diagnoses": diag,
        "semantics": {
            "readOnly": ("本工具**只读**：不写、不改、不备份存档。"
                         "存档修复是最难验证的一环，交回给人。"),
            "missingMeansRisk": ("缺模组**不等于**一定崩，只说明风险："
                                 "那个 mod 的数据还在存档里、代码不在 ⇒ 读到它时才炸。"),
            "versionNotAlwaysBad": ("版本不同不等于不安全 —— 按 framework/content 分类标风险，"
                                    "不搞一刀切。"),
            "baseModulesExcluded": ("游戏本体模块（Native/SandBox 等）随游戏版本变，"
                                    "已从'缺模组'里降级，否则人人报错。"),
            "saveListFrom": ("存档清单来自**最近一次** `list_saves` 的控制通道响应；"
                             "要刷新请先调 `bl_list_saves`。"),
        },
    }


def _render(res):
    L = []
    L.append("BlBridge 存档诊断（**只读** —— 不写、不改、不备份存档）")
    L.append("=" * 74)
    a = res["activeModules"]
    L.append("当前启用模组 : %d 个（读自 LauncherData.xml，共 %d 项）"
             % (a["count"], a["total"]))
    if not a["exists"]:
        L.append("  [!] 读不到 LauncherData.xml：%s" % a["path"])
        L.append("      ⇒ 无法比对'当前启用'，只能给存档侧信息。")
    ss = res["saveListSource"]
    L.append("存档清单来源 : %s" % ("控制通道响应 " + str(ss["responseFile"]) if ss["found"]
                                 else "**没有**（需先跑一次 bl_list_saves）"))
    L.append("存档数       : %d" % ss["count"])
    L.append("")

    diags = res["diagnoses"]
    if not diags:
        L.append("没有可诊断的存档。")
        if not ss["found"]:
            L.append("  [!] 原因：**没有** list_saves 的历史响应 ⇒ 存档清单是空的。")
            L.append("      这**不代表**没有存档问题，只说明还没采集到清单。")
            L.append("      → 先跑 `bl_list_saves`（或 `python bl_cmd.py list-saves`）再回来。")
        return "\n".join(L)

    # 汇总
    crit = sum(1 for d in diags if d["worst"] == "critical")
    warn = sum(1 for d in diags if d["worst"] == "warn")
    L.append("风险汇总     : critical=%d  warn=%d  其余=%d" % (crit, warn, len(diags) - crit - warn))
    L.append("")

    for d in diags:
        L.append("-" * 74)
        mark = {"critical": "!! CRITICAL", "warn": "!  WARN", "info": "i  INFO", "ok": "ok"}.get(d["worst"], "?")
        L.append("[%s] %s   存档内模组=%d" % (mark, d["name"], d["modulesInSave"]))
        L.append("         缺 %d / 版本变 %d / 新增 %d"
                 % (d["missing"], d["versionChanged"], d["added"]))
        # ★ 需要动手的先列全（这些才是结论）
        for it in d["actionable"]:
            L.append("   · %s" % it["text"])
            L.append("     → %s" % it["advice"])
        # 新增模组**折叠成一行**（无害占多数，逐条铺开会淹没上面那些）
        adds = d["addedList"]
        if adds:
            L.append("   · 新增模组 %d 个（多数无害，仅在读档后行为异常时才需怀疑）：%s"
                     % (len(adds), ", ".join(a["module"] for a in adds[:12])
                        + (" …" if len(adds) > 12 else "")))
        L.append("")

    L.append("-" * 74)
    L.append("★ 口径（读结论前必看）")
    L.append("-" * 74)
    for k in ("readOnly", "missingMeansRisk", "versionNotAlwaysBad",
              "baseModulesExcluded", "saveListFrom"):
        L.append("· %s" % res["semantics"][k])
    return "\n".join(L)


def main(argv=None):
    import argparse
    try:
        bl_common.safe_streams()
    except Exception:                                     # noqa: BLE001
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="BlBridge 存档诊断（只读）")
    ap.add_argument("--logDir", default=None, help="日志目录")
    ap.add_argument("--launcher", default=None, help="LauncherData.xml 路径")
    ap.add_argument("--limit", type=int, default=None, help="最多几个存档")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    res = build_report(log_dir=args.logDir, launcher_path=args.launcher, limit=args.limit)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(_render(res))
    # 退出码：有 critical => 1（便于 CI/agent 快速判）；否则 0
    worst = any(d["worst"] == "critical" for d in res["diagnoses"])
    return 1 if worst else 0


if __name__ == "__main__":
    sys.exit(main())
