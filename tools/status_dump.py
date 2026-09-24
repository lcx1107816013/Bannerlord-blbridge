#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读诊断：把游戏侧状态文件与最近一场遥测的关键部分打出来。

用法：python status_dump.py [--all]
比 bl_cmd.py status 更适合离线排查：它不需要游戏在运行，直接读磁盘文件。
"""
import collections
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bl_analyze  # noqa: E402


def default_log_dir():
    home = os.path.expanduser("~")
    return os.path.join(home, "Documents", "Mount and Blade II Bannerlord", "BlBridge")


def main():
    logdir = os.environ.get("BLBRIDGE_LOG_DIR") or default_log_dir()
    print("log dir: %s\n" % logdir)

    status = os.path.join(logdir, "bridge_status.json")
    print("=" * 78)
    print("① bridge_status.json")
    print("=" * 78)
    if not os.path.isfile(status):
        print("  missing")
    else:
        with io.open(status, "r", encoding="utf-8-sig", errors="replace") as fh:
            st = json.load(fh)
        for k in ("mod", "version", "protocolVersion", "state", "pid", "runToken",
                  "processStartedUtc", "sessionStartedUtc", "statusWrittenUtc",
                  "missionsThisSession", "lastBattle", "lastBattleEvents",
                  "cleanExit", "missionInProgress", "enabled"):
            if k in st:
                print("  %-20s %s" % (k, st[k]))
        for block in ("build", "config", "readiness", "sessionDiagnosis"):
            if isinstance(st.get(block), dict):
                print("  %s:" % block)
                for k, v in st[block].items():
                    print("      %-22s %s" % (k, v))

    battles_dir = os.path.join(logdir, "battles")
    print()
    print("=" * 78)
    print("battles/ directory")
    print("=" * 78)
    if not os.path.isdir(battles_dir):
        print("  missing: %s" % battles_dir)
        return 0
    files = sorted(f for f in os.listdir(battles_dir) if f.endswith(".jsonl"))
    if not files:
        print("  empty (no battle log written yet in this session)")
        return 0
    for f in files[-8:]:
        p = os.path.join(battles_dir, f)
        print("  %-42s %8.1f KB" % (f, os.path.getsize(p) / 1024.0))

    latest = os.path.join(battles_dir, files[-1])
    print()
    print("=" * 78)
    print("latest battle: %s" % files[-1])
    print("=" * 78)
    events = bl_analyze.load_events(latest)
    kinds = collections.Counter(e.get("t") for e in events)
    print("  %d events, kind histogram: %s" % (len(events), dict(kinds)))
    for kind in ("meta", "end"):
        for e in events:
            if e.get("t") == kind:
                print("  %s: %s" % (kind, json.dumps(e, ensure_ascii=False)))
    for e in events[:3]:
        print("  first %s: %s" % (e.get("t"), json.dumps(e, ensure_ascii=False)[:220]))
    for e in events[-3:]:
        print("  last %s: %s" % (e.get("t"), json.dumps(e, ensure_ascii=False)[:220]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
