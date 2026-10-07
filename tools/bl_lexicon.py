#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BlBridge 崩溃词典（lexicon）读取器 —— 宿主侧，只读，仅标准库。

## 它解决什么

`bl_crash --deep` 与 `bl_exceptions` 能把崩溃**定位到符号**（模块 + 偏移 + 栈），
但**说不出「这对玩家意味着什么、该怎么办」**。本工具补的就是这一层：
按异常类型 / 崩溃文本来匹配一份**词条库**，返回人话描述、常见场景、修复建议。

## ★ 数据**不在本仓库**（这一点是刻意的，别当成缺陷）

词条数据派生自一个**没有任何许可声明**的上游作品（默认「保留所有权利」），
因此**不得随本 MIT 仓库再分发**。本仓库只放这个读取器，数据由使用者在本地自行准备。

数据目录通过 `BLBRIDGE_LEXICON_DIR` 指定（或放在 `tools/data/lexicon/`）。
**缺数据时本工具如实报 `installed: false` 并给出怎么装**——**绝不返回空结果冒充"没有匹配"**。

## 口径（读结论前先看）

1. **匹配是"短语子串"**，不是语义匹配：上游词条用的是 `MatchAny` / `MatchAll` / `ExcludeAny`
   三组**小写友好短语**。本读取器按同一口径做大小写不敏感的子串匹配。
2. **`MatchAll` 与 `MatchAny` 的语义**：`MatchAny` 命中任一即可，`MatchAll` 必须全部命中，
   `ExcludeAny` 命中任一即**排除**该词条。三者同时存在时按 合取 处理。
3. **`priority` 只用于排序**，高者先出；**它不代表置信度**。
4. **`zh` 缺失不等于没词条**：上游的 legacy 档（84 条里 43 条）本来就只有英文。
   本工具**如实标注** `langMissing`，不拿英文冒充中文。
5. **`sourceRepairAction` 只作信息**：那是**上游模组自己的修复按钮**（如 `smartRepair`），
   BlBridge **没有**对应动作 ⇒ 本工具**不把它当可执行动作**报出去。
6. 词条数不是"覆盖了多少种崩溃"，而是"上游作者写了多少条"——**匹配不到不等于没问题**。

## 边界

- **只读**：不写、不删、不改。
- **不发网络请求**：纯本地文件。
- 数据来源与原作者的隶属关系见使用者本地的 `PROVENANCE.md`；本仓库**不声明**对数据的所有权。
"""
import collections
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 数据目录的候选（按优先级）：显式参数 > 环境变量 > 仓库内 tools/data/lexicon
ENV_VAR = "BLBRIDGE_LEXICON_DIR"
DEFAULT_SUBDIR = os.path.join("tools", "data", "lexicon")

FILES = {
    "exceptions": "exceptions.json",
    "diagnosticRules": "diagnostic-rules.json",
    "knownIssues": "known-issues.json",
    "meta": "meta.json",
}

# 词条里所有"玩家可见文本"的键（用于多语言统计与渲染）
TEXT_KEYS = ("action", "reason", "evidence", "category", "severity", "confidence", "risk")


def lexicon_dir(path=None):
    """解析词条目录：显式 > 环境变量 > 仓库内默认。"""
    if path:
        return os.path.abspath(path)
    env = os.environ.get(ENV_VAR)
    if env:
        return os.path.abspath(env)
    # 仓库内默认：tools/data/lexicon（本文件在 tools/ 下）
    return os.path.join(HERE, "data", "lexicon")


def _load(path):
    with io.open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def load_lexicon(path=None, require=False):
    """读全部词条。

    返回 (lex, status)。**status 永远说明"为什么可用/不可用"**，
    不把"目录不存在"与"目录为空"混成一种。
    """
    d = lexicon_dir(path)
    status = {
        "dir": d,
        "envVar": ENV_VAR,
        "envSet": bool(os.environ.get(ENV_VAR)),
        "dirExists": os.path.isdir(d),
        "filesFound": [],
        "filesMissing": [],
        "installed": False,
        "reason": None,
    }
    if not status["dirExists"]:
        status["reason"] = ("词条目录不存在 —— 数据**不随本仓库分发**（上游无许可）。"
                            "请自行准备并把 %s 指向它。" % ENV_VAR)
        if require:
            raise RuntimeError(status["reason"])
        return None, status

    lex = {}
    for key, fn in FILES.items():
        p = os.path.join(d, fn)
        if os.path.isfile(p):
            status["filesFound"].append(fn)
            if key != "meta":
                lex[key] = _load(p)
            else:
                lex["meta"] = _load(p)
        else:
            status["filesMissing"].append(fn)

    core_missing = [FILES[k] for k in ("exceptions", "diagnosticRules", "knownIssues")
                    if FILES[k] in status["filesMissing"]]
    if core_missing:
        status["reason"] = ("目录存在但**缺少核心文件**：%s ⇒ 不按'没有匹配'处理。"
                            % ", ".join(core_missing))
        if require:
            raise RuntimeError(status["reason"])
        return None, status

    status["installed"] = True
    status["reason"] = "词条已加载"
    return lex, status


def _lang_missing(blk):
    """多语言块里 zh 缺失（但 en 有）⇒ 供如实标注，不是错误。"""
    if not isinstance(blk, dict):
        return False
    return bool(blk.get("en")) and not blk.get("zh")


def _text_of(blk, lang):
    """取某种语言的文本；缺失回退链：zh -> en（**并如实标注发生了回退**）。"""
    if not isinstance(blk, dict):
        return None, False
    if lang == "zh":
        if blk.get("zh"):
            return blk["zh"], False
        if blk.get("en"):
            return blk["en"], True          # 回退了，调用方须知情
        return None, False
    return blk.get(lang), False


def _haystack(text):
    return (text or "").lower()


def _matches(rule, text):
    """按上游口径匹配：MatchAll 全部命中 + MatchAny 任一命中 + ExcludeAny 全不命中。

    ⚠️ `MatchAny` **为空**时不当作"必须命中"——上游确有词条只有 `MatchAll`
       （legacy 档里很多），若把空集当空，会把它们全部误杀。
    """
    low = _haystack(text)
    any_ = [p.lower() for p in (rule.get("matchAny") or []) if p]
    all_ = [p.lower() for p in (rule.get("matchAll") or []) if p]
    exc = [p.lower() for p in (rule.get("excludeAny") or []) if p]

    if any(p in low for p in exc):
        return False
    if all_ and not all(p in low for p in all_):
        return False
    if any_ and not any(p in low for p in any_):
        return False
    return bool(any_ or all_)


def lookup_exception(lex, exc_type):
    """按异常类型查词条。

    匹配规则：**短名精确**（`NullReferenceException`）优先；再退回全名后缀匹配。
    ⚠️ 上游词条的 `type` 是.NET 全名（如 `System.NullReferenceException` 只写短名形态），
       故这里两侧都做**短名归一**后再比。
    """
    if not exc_type or not lex:
        return None
    want = exc_type.strip().rsplit(".", 1)[-1].lower()
    for e in lex.get("exceptions") or []:
        if (e.get("shortName") or "").lower() == want:
            return e
    for e in lex.get("exceptions") or []:
        if (e.get("type") or "").lower().rsplit(".", 1)[-1] == want:
            return e
    return None


def match_text(lex, text, limit=10):
    """按一段文本（崩溃报告 / 日志片段）匹配诊断词条与已知问题。

    返回按 priority 降序的命中列表（**不含**未命中的词条）。
    """
    hits = []
    for kind in ("diagnosticRules", "knownIssues"):
        for r in (lex.get(kind) or []):
            if _matches(r, text):
                hits.append((r.get("priority") if isinstance(r.get("priority"), int) else -1,
                             kind, r))
    hits.sort(key=lambda t: -t[0])
    return hits[:max(1, int(limit))]


def describe(lex, exc_type=None, text=None, lang="zh", limit=10):
    """组装报告（机器字段 + 可读文本）。"""
    if lex is None:
        return {"ok": False, "reason": "lexicon not loaded"}

    out = {
        "ok": True,
        "lang": lang,
        "exception": None,
        "matches": [],
        "langMissing": 0,
        "note": ("匹配是**短语子串**，不是语义匹配；匹配不到**不等于**没问题。"),
    }

    # ---- 异常类型词条 ----
    if exc_type:
        e = lookup_exception(lex, exc_type)
        if e:
            name, fell = _text_of(e.get("displayName"), lang)
            desc, _ = _text_of(e.get("description"), lang)
            # ⚠️ displayName / description 的缺失**也必须计入** langMissing。
            #    实测踩到（2026-10-07 自测 12a 抓到）：首版只在 scenarios/suggestions
            #    循环里累加，而 HarmonyException 这类**只有英文、且场景/建议为空**的词条
            #    ⇒ 计数恒为 0，"中文覆盖率"看起来是满的。**漏计比算错更危险**。
            for blk0 in (e.get("displayName"), e.get("description")):
                if _lang_missing(blk0):
                    out["langMissing"] += 1
            blk = {
                "type": e.get("type"),
                "displayName": name,
                "description": desc,
                "langFellBackToEn": fell,
                "scenarios": [],
                "suggestions": [],
            }
            for it in e.get("scenarios") or []:
                t, f = _text_of(it.get("text"), lang)
                blk["scenarios"].append({"text": t, "langFellBackToEn": f})
                if _lang_missing(it.get("text")):
                    out["langMissing"] += 1
            for it in e.get("suggestions") or []:
                t, f = _text_of(it.get("text"), lang)
                blk["suggestions"].append({"text": t, "langFellBackToEn": f})
                if _lang_missing(it.get("text")):
                    out["langMissing"] += 1
            out["exception"] = blk
        else:
            # 如实说"库里没有这个类型"，并给出库里有什么 —— 不静默
            out["exception"] = {
                "type": exc_type,
                "found": False,
                "note": "词典里没有这个异常类型的词条（不是失败，是覆盖范围所限）",
                "knownCount": len(lex.get("exceptions") or []),
            }

    # ---- 文本匹配 ----
    if text:
        for prio, kind, r in match_text(lex, text, limit=limit):
            item = {
                "id": r.get("id"),
                "kind": kind,
                "priority": prio if prio >= 0 else None,
                "sourceFile": (r.get("source") or {}).get("file"),
                "matchAny": r.get("matchAny"),
                "matchAll": r.get("matchAll"),
                # ⚠️ 只作信息：上游模组自己的修复按钮，BlBridge 无对应动作
                "sourceRepairAction": r.get("sourceRepairAction"),
                "backupRecommended": r.get("backupRecommended"),
            }
            for k in TEXT_KEYS:
                t, f = _text_of(r.get(k), lang)
                item[k] = t
                if k in ("action", "reason"):
                    item[k + "FellBackToEn"] = f
                if _lang_missing(r.get(k)):
                    out["langMissing"] += 1
            out["matches"].append(item)

    return out


def render(res):
    """把 describe() 的结果渲染成可读文本。"""
    L = []
    if not res.get("ok"):
        return "词条未加载：%s" % res.get("reason")
    L.append("BlBridge 崩溃词典（lexicon）")
    L.append("=" * 74)
    L.append("语言：%s   |   %s" % (res.get("lang"), res.get("note")))
    if res.get("langMissing"):
        L.append("[i] 有 %d 个文本块**没有中文**（上游 legacy 档只有英文）——已如实标注，未用英文冒充。"
                 % res["langMissing"])
    L.append("")

    e = res.get("exception")
    if e:
        L.append("-" * 74)
        if e.get("found") is False:
            L.append("异常类型：%s" % e.get("type"))
            L.append("[i] %s（库里共 %d 个类型）" % (e.get("note"), e.get("knownCount") or 0))
        else:
            L.append("异常类型：%s" % e.get("type"))
            L.append("名称    ：%s" % (e.get("displayName") or "(无)"))
            if e.get("langFellBackToEn"):
                L.append("          [!] 该词条无中文，以下为**英文原文**。")
            L.append("说明    ：%s" % (e.get("description") or "(无)"))
            if e.get("scenarios"):
                L.append("常见场景：")
                for i, s in enumerate(e["scenarios"], 1):
                    L.append("  %d) %s" % (i, s.get("text") or "(无)"))
            if e.get("suggestions"):
                L.append("修复建议：")
                for i, s in enumerate(e["suggestions"], 1):
                    L.append("  %d) %s" % (i, s.get("text") or "(无)"))
        L.append("")

    ms = res.get("matches") or []
    L.append("-" * 74)
    if not ms:
        L.append("文本匹配：**0 条命中**。")
        L.append("  [!] 这**不等于**没问题 —— 只说明这份词典（上游作者写的有限条数）里没有对得上的短语。")
    else:
        L.append("文本匹配：%d 条（按 priority 降序；priority **不是**置信度）" % len(ms))
        for m in ms:
            L.append("-" * 74)
            L.append("[%s] %s   (priority=%s)" % (m.get("kind"), m.get("id"), m.get("priority")))
            if m.get("sourceFile"):
                L.append("   来源文件：%s" % m["sourceFile"])
            if m.get("action"):
                fb = "  [!] 无中文，英文原文" if m.get("actionFellBackToEn") else ""
                L.append("   建议动作：%s%s" % (m["action"], fb))
            if m.get("reason"):
                fb = "  [!] 无中文，英文原文" if m.get("reasonFellBackToEn") else ""
                L.append("   原因    ：%s%s" % (m["reason"], fb))
            if m.get("severity"):
                L.append("   严重度  ：%s" % m["severity"])
            if m.get("sourceRepairAction"):
                L.append("   [!] 上游模组的修复按钮 `%s` —— BlBridge **没有**这个动作，仅作信息。"
                         % m["sourceRepairAction"])
    return "\n".join(L)


def main(argv=None):
    import argparse

    try:
        import bl_common
        bl_common.safe_streams()
    except Exception:                                   # noqa: BLE001
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="BlBridge 崩溃词典读取器（宿主侧，只读）")
    ap.add_argument("--type", default=None, help="异常类型（短名或全名，如 NullReferenceException）")
    ap.add_argument("--text", default=None, help="按一段崩溃文本/日志片段匹配词条")
    ap.add_argument("--lang", default="zh", choices=["zh", "en"], help="文本语言，默认 zh")
    ap.add_argument("--limit", type=int, default=10, help="最多几条匹配")
    ap.add_argument("--dir", default=None, help="词条目录（默认取 %s）" % ENV_VAR)
    ap.add_argument("--status", action="store_true", help="只报词典装载状态")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    lex, status = load_lexicon(path=args.dir)

    if args.status:
        # ⚠️ `--status` **不能走 render()** —— 那份 payload 没有 lang/note/matches 字段，
        #    实测（2026-10-07 端到端）会渲染出 `语言：None` 这种垃圾报告。
        #    本项只报"装没装上、为什么"，与 render() 的职责不同。
        payload = {"ok": status["installed"], "status": status}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        else:
            print("BlBridge 崩溃词典 —— 装载状态")
            print("=" * 74)
            print("词条目录   : %s" % status["dir"])
            print("目录存在   : %s" % status["dirExists"])
            print("来源       : %s" % ("环境变量 %s" % status["envVar"] if status["envSet"]
                                       else "仓库内默认"))
            print("已找到文件 : %s" % (", ".join(status["filesFound"]) or "(无)"))
            if status["filesMissing"]:
                print("缺少文件   : %s" % ", ".join(status["filesMissing"]))
            print("可用       : %s" % status["installed"])
            print("说明       : %s" % (status.get("reason") or ""))
            if not status["installed"]:
                print()
                print("  [!] 数据**不随本仓库分发**（上游无许可，默认保留所有权利）。")
                print("  → 用 --dir 或环境变量 %s 指向你本地的词条目录。" % ENV_VAR)
        return 0 if status["installed"] else 1

    if lex is None:
        payload = {"ok": False, "reason": status["reason"], "status": status}
    else:
        payload = describe(lex, exc_type=args.type, text=args.text,
                           lang=args.lang, limit=args.limit)
        payload["status"] = status

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        if not payload.get("ok"):
            print("词典不可用：%s" % payload.get("reason"))
            print("  → 数据**不随本仓库分发**（上游无许可）。")
            print("  → 用 %s 指向你本地准备好的词条目录，或 --status 看详情。" % ENV_VAR)
        else:
            print(render(payload))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
