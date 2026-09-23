#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BlBridge 各工具共享的 I/O 与格式化小函数（不含任何指标逻辑）。

存在的理由：``load_events`` / 「列出 battles/*.jsonl」/ 数字格式化曾在
bl_analyze.py、bl_metrics.py、bl_death_compare.py 里各写一份
（code-review 2026-09-24 指出的 Duplicated Code），收拢到这一处。

把本文件放同目录后 ``import bl_common`` 即可 —— Python 会把脚本所在目录
加入 sys.path，所以直接 ``python tools/xxx.py`` 也能导入。
"""
import io
import json
import os


def default_log_dir():
    return os.path.join(os.path.expanduser("~"), "Documents",
                        "Mount and Blade II Bannerlord", "BlBridge")


def battles_dir(log_dir=None):
    return os.path.join(log_dir or default_log_dir(), "battles")


def list_battle_files(log_dir=None):
    """目录下的 *.jsonl（按文件名升序；目录不存在 ⇒ 空列表）。"""
    d = battles_dir(log_dir)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, n) for n in os.listdir(d) if n.lower().endswith(".jsonl"))


def list_battles(log_dir=None):
    """[{file, name, size, mtime}]，按 mtime 升序（与 bl_analyze 既有语义一致）。"""
    out = []
    for p in list_battle_files(log_dir):
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"file": p, "name": os.path.basename(p), "size": st.st_size, "mtime": st.st_mtime})
    out.sort(key=lambda x: x["mtime"])
    return out


def latest_battle(log_dir=None):
    """按 mtime 最新的一份（无则 None）。"""
    rows = list_battles(log_dir)
    return rows[-1]["file"] if rows else None


def load_events(path):
    """读 JSONL 事件流；坏行静默跳过（既有工具一致的口径）。"""
    ev = []
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev.append(json.loads(line))
            except ValueError:
                continue
    return ev


def fmt(v, nd=1, dash="-"):
    """数字 / None 的紧凑格式化。"""
    if v is None:
        return dash
    if isinstance(v, float):
        return ("%." + str(nd) + "f") % v
    return str(v)