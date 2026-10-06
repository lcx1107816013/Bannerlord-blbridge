#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BlBridge MCP 响应完整性体检（宿主侧，只读，仅标准库）。

## 为什么需要它（一手证据，2026-10-06 实测）

BlBridge 把每个请求的响应写到 `<logDir>\\commands\\done\\<id>.json`。
实测 **12991 个文件里有 5 个是畸形 JSON**（畸形率 0.0385%），三种表现、同一根源
（`Jw.Esc` **只转义、不加引号** -> 键/值边界丢失）：

    1eef012943094d74.json   {"Prefixes","type":"..."}     键后缺冒号
    2fbeff26dcab4fed.json   同上
    c60aec4c40004cd2.json   同上
    f029acf4ae79450b.json   {"Prefixes":"type":"..."}     键值错位
    3f10da861997480a.json   "type":System.Security...     值缺引号

## 为什么必须专门体检（症状极具误导性）

IPC 侧 json 解析失败 -> 客户端**一直等到超时**，而响应其实早就写好了
-> 症状看起来像「游戏主线程卡死」，根因却在响应侧的 JSON 写法。
（交接日志 §3.3 已记载同一条；`src/PatchProbe.cs:325-331` 有源码侧的同坑注释。）

## 为什么单读一个文件发现不了

- 只有**全量解析**才知道有几个坏的；
- 坏文件**混在 12991 个正常文件里**，按名字看不出任何异常；
- 单文件读一次基本撞不上（5/12991 ≈ 0.04%）。

## 实测结论（2026-10-06，本机）

    [key_value_swap] f029acf4ae79450b.json  <- 缺陷逐字等于 PatchProbe.cs:326 注释里记的那个
    坏文件范围: 2026-10-06 02:59:43 ~ 03:53:53
    最新文件  : 2026-10-06 04:06:25
    => 最新 20 个文件里仍有畸形 => **缺陷仍活着**（不是历史遗留）

源码侧 `PatchProbe.cs:325-331` **已修**，但进程内 DLL 是 0.8.46、磁盘源码是 0.8.47
=> 正是 `bl_build_check` / `BuildInfo` 要防的「进程内 vs 磁盘」错位。

## 边界（如实写明）

- **只读**：不修文件、不删文件、不改任何东西。
- 分类（`kind`）只是**线索**，每条同时给出原始上下文，读者可自行判断
  -- 本项目纪律：不给无法复核的结论。
- 它回答的是「**响应写对了没有**」，不回答「响应内容对不对」。
"""
import io
import json
import os
import sys
import time

# [!] 必须把本目录加进 sys.path —— 否则**直接跑 CLI**（`python tools/bl_json_health.py`）
#    时 `import bl_common` / `import bl_analyze` 会 ModuleNotFoundError
#    （被 import 成模块时不会有问题，所以只有 CLI 路径踩得到）。
#    这是本项目既有惯例，见 `bl_analyze.py:25-27`。
#    ★ 2026-10-06 补：本文件最初并入时**漏了这段 + 漏了 `import sys` + 漏了 `main()`**，
#      三缺叠加 -> 直接跑**静默 exit=0 什么都不做**（两个并行测量者都独立复现）。
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def default_done_dir(log_dir=None):
    """与 `bl_analyze.default_log_dir()` 同口径：<日志目录>\\commands\\done。"""
    if log_dir:
        root = log_dir
    else:
        import bl_analyze
        root = bl_analyze.default_log_dir()
    return os.path.join(root, "commands", "done")


def classify(err_msg):
    """把 json 解析错误归类。返回 (kind, hint)。

    ! 只是**线索**，不是结论 -- 判据是「python 的 json 解析器报了什么」，
    而 `kind` 是对这个报错的**猜测性命名**。调用方应连同 `before`/`after`
    的原始上下文一起呈现，让读者自己判断。
    """
    if "Expecting ':' delimiter" in err_msg:
        return ("missing_colon",
                "键后缺冒号 -- 疑似写完键名后漏了 \":\"（用了自带引号的 Q() 却只补冒号）")
    if "Expecting value" in err_msg:
        return ("unquoted_value",
                "值缺引号 -- 疑似 Jw.Esc 只转义、未加外层引号")
    if "Expecting ',' delimiter" in err_msg:
        return ("key_value_swap",
                "键值错位 -- 疑似只补了一个冒号、而非完整写出 \"name\": 键")
    return ("other", "未归类（请人工看上下文）")


def scan(directory):
    """全量解析目录下所有 *.json。返回结果字典（不抛异常）。"""
    try:
        names = sorted(n for n in os.listdir(directory) if n.lower().endswith(".json"))
    except OSError as exc:
        return {"ok": False, "error": "无法列目录 %s：%s" % (directory, exc)}

    bad = []
    ok_count = 0
    times = []
    for name in names:
        path = os.path.join(directory, name)
        try:
            times.append((os.path.getmtime(path), name))
        except OSError:
            pass
        try:
            with io.open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                raw = fh.read()
        except OSError as exc:
            bad.append({"file": name, "kind": "unreadable", "error": str(exc),
                        "pos": -1, "before": "", "after": "",
                        "hint": "读不到（IO 错误）", "mtime": None})
            continue
        try:
            json.loads(raw)
            ok_count += 1
        except ValueError as exc:
            pos = getattr(exc, "pos", 0) or 0
            kind, hint = classify(str(exc))
            mtime = None
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                pass
            bad.append({"file": name, "kind": kind, "error": str(exc),
                        "pos": pos, "len": len(raw), "mtime": mtime,
                        "before": raw[max(0, pos - 60):pos],
                        "after": raw[pos:pos + 60], "hint": hint})

    times.sort()
    bad_set = set(b["file"] for b in bad)
    recent_bad = [n for _t, n in times[-20:] if n in bad_set]
    kinds = {}
    for b in bad:
        kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1

    return {
        "ok": True,
        "dir": directory,
        "total": len(names),
        "parseable": ok_count,
        "malformed": len(bad),
        "malformedRatio": (float(len(bad)) / len(names)) if names else 0.0,
        "kinds": kinds,
        "bad": bad,
        "oldestUtc": _iso(times[0][0]) if times else None,
        "newestUtc": _iso(times[-1][0]) if times else None,
        "newestFile": times[-1][1] if times else None,
        "badRangeUtc": (_iso(min(b["mtime"] for b in bad if b["mtime"]))
                        if any(b["mtime"] for b in bad) else None),
        "badRangeEndUtc": (_iso(max(b["mtime"] for b in bad if b["mtime"]))
                           if any(b["mtime"] for b in bad) else None),
        "inRecent20": recent_bad,
        # 判据：最新 20 个文件里还有坏的 => 缺陷仍活着
        "stillLive": bool(recent_bad),
    }


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def build_report(directory=None, log_dir=None, limit=20):
    """给 MCP 工具用的入口：返回 dict（含人话 report）。"""
    target = directory or default_done_dir(log_dir)
    res = scan(target)
    if not res.get("ok"):
        return {"ok": False, "reason": "no_dir", "detail": res.get("error"),
                "dir": target}

    lines = []
    lines.append("IPC 响应完整性体检（%s）" % res["dir"])
    lines.append("")
    lines.append("文件总数   : %d" % res["total"])
    lines.append("可解析     : %d" % res["parseable"])
    lines.append("畸形 JSON  : %d" % res["malformed"])
    if res["total"]:
        lines.append("畸形率     : %.4f%%" % (100.0 * res["malformedRatio"]))
    if res["kinds"]:
        lines.append("形态分布   : %s" % ", ".join(
            "%s=%d" % (k, v) for k, v in sorted(res["kinds"].items())))
    lines.append("")
    lines.append("时间范围   : %s ~ %s" % (res["oldestUtc"], res["newestUtc"]))
    if res["badRangeUtc"]:
        lines.append("坏文件范围 : %s ~ %s" % (res["badRangeUtc"], res["badRangeEndUtc"]))
    if res["malformed"]:
        if res["stillLive"]:
            lines.append("")
            lines.append("[!] 最新 20 个文件里仍有畸形：%s" % ", ".join(res["inRecent20"][:5]))
            lines.append("    => 缺陷**仍活着**（不是历史遗留）")
            lines.append("    => 对照 bl_build_check：进程内 DLL 与磁盘源码是否一致？")
        else:
            lines.append("")
            lines.append("[OK] 最新 20 个文件全部可解析 => 这批坏文件是历史遗留；")
            lines.append("     但仍建议核对 bl_build_check（进程内 DLL vs 磁盘源码）。")
        lines.append("")
        lines.append("-" * 70)
        for b in res["bad"][:max(1, int(limit))]:
            lines.append("[%s] %s" % (b["kind"], b["file"]))
            lines.append("   错误   : %s" % b["error"])
            lines.append("   偏移   : %s%s" % (b["pos"], "  (len=%s)" % b.get("len")
                                              if b.get("len") is not None else ""))
            lines.append("   线索   : %s" % b["hint"])
            lines.append("   上下文 : ...%s<<HERE>>%s..." % (b["before"], b["after"]))
            lines.append("")
    else:
        lines.append("")
        lines.append("[OK] 全部可解析，未发现畸形 JSON。")

    # 只把前 limit 条明细放进返回体，避免响应过大
    out = dict(res)
    out["bad"] = res["bad"][:max(1, int(limit))]
    out["badTruncated"] = max(0, res["malformed"] - len(out["bad"]))
    out["report"] = "\n".join(lines)
    return out


def main(argv=None):
    """CLI 入口（与同目录 `bl_analyze.py` / `bl_crash.py` / `bl_blockade.py` 同惯例）。

    [!] **本函数是 2026-10-06 补的，补的是一个我自己引入的缺陷**：
    最初并入仓库时只写了 `scan()` / `build_report()` 两个**库函数**，
    **没有 `main()`、没有 `__main__` 块** -> 直接 `python tools/bl_json_health.py`
    会**什么都不做、静默 exit=0**（只是解释器启动开销，约 30ms）。

    危害不只是"CLI 不能用"：它会让**性能测量得出虚假结论** ——
    实测有人（包括我自己）把 30ms 当成"全量解析 12991 个文件的耗时"，
    而真实耗时约 **705ms**（冷缓存首次 1.17s），差 **23 倍**。
    -> 两个并行测量者都独立复现了这个坑（2026-10-06 A/B 双路）。
    同目录既有工具**全都有** `__main__`（`bl_crash` / `bl_blockade` / `bl_analyze`），
    所以这是**不一致**，不是设计选择。

    用法：
        python tools/bl_json_health.py                 # 默认扫 BlBridge 的 commands\\done
        python tools/bl_json_health.py --dir <path>
        python tools/bl_json_health.py --json
        python tools/bl_json_health.py --limit N
    退出码：发现畸形 JSON => 1（可做闸门）；全好 => 0；目录不存在 => 2。
    """
    import argparse
    import bl_common

    bl_common.safe_streams()          # 输出统一 UTF-8（见 safe_streams docstring）

    ap = argparse.ArgumentParser(description="BlBridge IPC 响应完整性体检")
    ap.add_argument("--dir", default=None,
                    help="要扫描的目录（默认 BlBridge 的 commands\\done）")
    ap.add_argument("--logDir", default=None, help="日志目录（用于推导 done 目录）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--limit", type=int, default=20, help="最多几条坏文件明细")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    res = build_report(directory=args.dir, log_dir=args.logDir, limit=args.limit)
    if not res.get("ok"):
        print(res.get("detail") or "扫描失败")
        return 2

    if args.json:
        out = dict(res)
        out.pop("report", None)
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(res["report"])
    return 1 if res.get("malformed") else 0


if __name__ == "__main__":
    sys.exit(main())
