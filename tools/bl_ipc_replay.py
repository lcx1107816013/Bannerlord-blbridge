#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BlBridge IPC 响应缓存检索（宿主侧，只读，仅标准库）。

## 为什么需要它（一手证据，2026-10-06 实测）

`<日志目录>\\commands\\done\\` 里躺着 **12991 个历史响应**（每个请求一份
`<id>.json`），而 `commands\\actions.jsonl` 是**动作账本**（2490 行），
两者用 **`id` 精确连接**（实测：账本有 id 的 2490 条**全部**能在 done 里找到，
`账本有但 done 无 = 0`）。

⇒ 于是可以回答一类**此前只能靠手工 grep 才能回答**的问题：

    · 上个会话那次 `get_patches` 的**完整响应**到底是什么？
    · 那次 `start_battle` 失败时游戏端回了什么 `code`？（账本只记 code，响应有全文）
    · 某次响应为什么解析不出来？（对照 `bl_json_health`）

## 为什么通用手段不行（这才是它存在的理由）

**响应体里不含 method 名**：`done/<id>.json` 只有 `process` / `result` / `error` /
`protocolVersion` / `id`，**没有"我是哪个工具"**。想按工具名检索，
**必须**先读账本拿到 `id -> method` 的映射，再去取响应。
⇒ 手工得写两段脚本；只用 `read`/`grep` 根本做不到。

实测代价：本轮为了找「`CryptographicException` 的 44 次到底在第几个响应里」，
**手工 grep 了 12991 个文件**才定位到。

## ⚠️ 覆盖率边界（必须随结果一起报出）

账本只覆盖 **2490 / 12991 ≈ 19%** 的响应（其余 10501 个是账本启用前的历史，
或游戏端未记账本的请求）。所以：

    · 按 `method` 检索 ⇒ **只能在有账本的那 2490 个里找**
    · 按 `id` / `file` / 内容检索 ⇒ 覆盖**全部** 12991 个

本工具**如实报出** `covered` 与 `total`，**不把"账本里没找到"说成"没发生过"**。

## 边界

- **只读**：不写、不删、不改任何东西。
- 不猜：查不到就回 `found: 0` 并说明**搜索范围**（而不是"没有"）。
- 坏响应（畸形 JSON）：**照样列出**并标明，不静默跳过（那正是 `bl_json_health` 的活）。
"""
import io
import json
import os
import time

import bl_common


def done_dir(log_dir=None):
    """`<日志目录>\\commands\\done`。"""
    return os.path.join(bl_common.commands_dir(log_dir), "done")


def _load_ledger(log_dir=None, method=None, ok_only=None, fail_only=False):
    """读账本 → (entries, stats)，并可选按 method / 成败过滤。

    复用 `bl_common.load_actions`（它已处理坏行计数、非对象 JSON、`limit` 语义、
    `resultOk` 三态）—— **不重写解析器**，避免两套口径漂移。

    ⚠️ `load_actions(path=..., limit=..., fail_only=...)` **没有 `log_dir` 参数**
    —— 它只吃完整的账本**文件路径**（用 `bl_common.actions_path(log_dir)` 求）。
    这里实测踩过：直接传 `log_dir=` 会 `TypeError`。
    """
    entries, stats = bl_common.load_actions(
        path=bl_common.actions_path(log_dir), fail_only=fail_only)
    if method:
        want = method.strip().lower()
        entries = [e for e in entries if (e.get("method") or "").lower() == want]
    if ok_only is True:
        entries = [e for e in entries if e.get("ok") is True]
    elif ok_only is False:
        entries = [e for e in entries if e.get("ok") is False]
    return entries, stats


def _read_response(path):
    """读一份响应。返回 (obj_or_None, raw, err)。**畸形也返回原文**，不静默丢。"""
    try:
        raw = io.open(path, "r", encoding="utf-8-sig", errors="replace").read()
    except OSError as exc:
        return None, "", "读不到：%s" % exc
    try:
        return json.loads(raw), raw, None
    except ValueError as exc:
        return None, raw, "畸形 JSON：%s" % exc


def build_report(method=None, log_dir=None, limit=5, grep=None,
                 ok_only=None, fail_only=False, full=False,
                 newest_first=True, include_missing=False):
    """检索 IPC 响应缓存。

    参数：
        method         按账本里的方法名过滤（如 "get_patches"）。
                       ⚠️ 只在**有账本**的响应里找（见模块 docstring 的覆盖率边界）。
        grep           在**响应原文**里做子串匹配（覆盖全部 12991 个，不依赖账本）。
        ok_only        True=只看成功 / False=只看失败 / None=都看（按**信封** ok）。
        fail_only      复用 load_actions 的 fail_only（语义与 CLI `--fail-only` 一致）。
        limit          最多返回几份响应明细。
        full           是否带完整响应体（默认只带摘要 + 前若干字符）。
        newest_first   按文件 mtime 倒序（默认 True）。
        include_missing 账本有、done 里缺失的 id 是否也列出（默认 False）。
    """
    d = done_dir(log_dir)
    if not os.path.isdir(d):
        return {"ok": False, "reason": "no_dir", "detail": "目录不存在：%s" % d, "dir": d}

    all_files = [n for n in os.listdir(d) if n.lower().endswith(".json")]
    total_responses = len(all_files)

    # ── 账本侧 ──────────────────────────────────────────────
    entries, stats = _load_ledger(log_dir=log_dir, method=method,
                                  ok_only=ok_only, fail_only=fail_only)
    ledger_by_id = {}
    for e in entries:
        rid = e.get("id")
        if rid:
            ledger_by_id[rid] = e

    # 账本总量（未过滤）—— 用来如实报覆盖率
    _all_entries, all_stats = bl_common.load_actions(path=bl_common.actions_path(log_dir))
    ledger_total = len(_all_entries)

    # ── 决定候选集 ──────────────────────────────────────────
    # 有 method/node 过滤 ⇒ 从账本出发（但只覆盖有账本的部分）
    # 只有 grep        ⇒ 从磁盘全量出发（覆盖 100%）
    from_ledger = bool(method) or ok_only is not None or fail_only
    if from_ledger:
        candidates = [(rid, os.path.join(d, rid + ".json")) for rid in ledger_by_id]
    else:
        candidates = [(n[:-5], os.path.join(d, n)) for n in all_files]

    # grep：在响应原文里找子串（覆盖全部）
    # ⚠️ `scannedFiles` 与 `matchedFiles` 必须分开报（2026-10-06 实测踩过）：
    #    第一版只报"搜索范围 = len(candidates)"，那是 **grep 过滤之后**的数量，
    #    于是 `grep='"count":44'` 显示成"搜索范围: 3"——看着像只搜了 3 个文件，
    #    实际扫了全部 12991 个、命中 3 个。**口径混淆会把"扫全量"说成"只搜3个"。**
    scanned_files = len(candidates)
    if grep:
        needle = grep
        kept = []
        for rid, path in candidates:
            try:
                raw = io.open(path, "r", encoding="utf-8-sig", errors="replace").read()
            except OSError:
                continue
            if needle in raw:
                kept.append((rid, path))
        candidates = kept
    matched_files = len(candidates)

    # ── 排序（按 mtime） ────────────────────────────────────
    def _mt(item):
        try:
            return os.path.getmtime(item[1])
        except OSError:
            return 0.0

    candidates.sort(key=_mt, reverse=bool(newest_first))

    # ── 取明细 ──────────────────────────────────────────────
    missing = []
    items = []
    for rid, path in candidates:
        if not os.path.isfile(path):
            missing.append(rid)
            continue
        if len(items) >= max(0, int(limit)):
            continue
        obj, raw, err = _read_response(path)
        led = ledger_by_id.get(rid) or {}
        item = {
            "id": rid,
            "file": os.path.basename(path),
            "mtimeUtc": _iso(_mt((rid, path))),
            "bytes": len(raw),
            "malformed": bool(err),
            "readError": err,
            # 账本侧（可能为空 —— 覆盖率边界，如实报）
            "ledger": {
                "method": led.get("method"),
                "ok": led.get("ok"),
                "code": led.get("code"),
                "ms": led.get("ms"),
                "t": led.get("t"),
                "runToken": led.get("runToken"),
                "resultOk": led.get("resultOk", None),
            } if led else None,
            # 响应侧
            "response": {
                "ok": (obj or {}).get("ok"),
                "pid": ((obj or {}).get("process") or {}).get("pid"),
                "error": (obj or {}).get("error"),
                "resultKeys": sorted(((obj or {}).get("result") or {}).keys())
                if isinstance((obj or {}).get("result"), dict) else None,
            } if obj is not None else None,
        }
        if full:
            item["raw"] = raw
        else:
            item["rawHead"] = raw[:400]
        items.append(item)

    return {
        "ok": True,
        "dir": d,
        "totalResponses": total_responses,
        "ledgerTotal": ledger_total,
        "ledgerMatches": len(ledger_by_id),
        "ledgerCoverage": (float(ledger_total) / total_responses) if total_responses else 0.0,
        "searched": scanned_files,
        "scannedFiles": scanned_files,
        "matched": matched_files,
        "matchedFiles": matched_files,
        "returned": len(items),
        "missingResponses": missing[:20],
        "missingCount": len(missing),
        "ledgerStats": stats,
        "filters": {"method": method, "grep": grep, "okOnly": ok_only,
                    "failOnly": fail_only, "newestFirst": newest_first},
        "items": items,
        "report": _render(method, grep, ok_only, fail_only, items,
                          total_responses, ledger_total, len(ledger_by_id),
                          scanned_files, matched_files, missing, stats, full),
    }


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else None


def _render(method, grep, ok_only, fail_only, items, total, ledger_total,
            ledger_matches, scanned_files, matched_files, missing, stats, full):
    L = []
    L.append("IPC 响应缓存检索")
    L.append("")
    L.append("文件总数   : %d 份响应（commands\\done）" % total)
    L.append("账本总量   : %d 条（actions.jsonl）" % ledger_total)
    cov = (100.0 * ledger_total / total) if total else 0.0
    L.append("账本覆盖率 : %.1f%%  <- 按 method 检索只在这个范围内有效" % cov)
    L.append("")
    f = []
    if method:
        f.append("method=%s" % method)
    if grep:
        f.append("grep=%r" % grep)
    if ok_only is not None:
        f.append("ok=%s" % ok_only)
    if fail_only:
        f.append("fail_only")
    L.append("过滤条件   : %s" % (", ".join(f) if f else "(无)"))
    L.append("账本命中   : %d" % ledger_matches)
    L.append("扫描文件   : %d" % scanned_files)
    L.append("命中文件   : %d" % matched_files)
    if stats.get("bad"):
        L.append("账本坏行   : %d  <- 见 bl_common.load_actions 的 badSamples" % stats["bad"])
    if missing:
        L.append("响应缺失   : %d 个 id 在账本里有、done 里没有（前几个：%s）"
                 % (len(missing), ", ".join(missing[:3])))
    L.append("")

    if not items:
        L.append("[!] 未找到匹配的响应。")
        if method:
            L.append("    注意：按 method 检索**只能覆盖有账本记录的响应**"
                     "（实测 2490/%d）。若你确定那次调用发生过，" % total)
            L.append("    可能是它早于账本启用 ⇒ 请改用 grep= 在响应原文里搜（覆盖全部）。")
        return "\n".join(L)

    L.append("-" * 74)
    for it in items:
        led = it.get("ledger") or {}
        resp = it.get("response") or {}
        L.append("%s  (%s, %d 字节)" % (it["file"], it["mtimeUtc"], it["bytes"]))
        if it.get("malformed"):
            L.append("   [!] 畸形 JSON：%s" % it["readError"])
            L.append("       => 这个响应**客户机会一直等到超时**（见 bl_json_health）")
        if led:
            L.append("   账本 : method=%s ok=%s code=%r ms=%s resultOk=%s"
                     % (led.get("method"), led.get("ok"), led.get("code"),
                        led.get("ms"), led.get("resultOk")))
        else:
            L.append("   账本 : (无记录 —— 该响应在账本覆盖范围之外，method 未知)")
        if resp:
            L.append("   响应 : envelopeOk=%s pid=%s" % (resp.get("ok"), resp.get("pid")))
            if resp.get("error"):
                L.append("   错误 : %s" % json.dumps(resp["error"], ensure_ascii=False)[:200])
            if resp.get("resultKeys"):
                L.append("   result 键: %s" % ", ".join(resp["resultKeys"][:14]))
        if full:
            L.append("   原文 :")
            for line in it["raw"].splitlines()[:40]:
                L.append("      " + line[:200])
        else:
            L.append("   原文头: %s" % it["rawHead"].replace("\n", " ")[:240])
        L.append("")
    return "\n".join(L)
