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
import sys


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

def safe_streams():
    """让 stdout/stderr 在 GBK 控制台下不因无法编码的字符而崩溃。

    默认中文 Windows 控制台（locale=gbk）下，输出里的 ⚠️ / ✅ / 箭头符号会抛
    UnicodeEncodeError，整个 CLI exit 1（2026-09-24 实测：bl_metrics.py 与
    bl_compare.py 都中过）。只改 errors 不改 encoding：中文照常可读，
    编不出的字符降级成问号。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


# ── 靶子护甲覆盖（v0.8.0）：部位名 → C# 侧参数名 ──────────────────────
# 单一真相：bl_cmd.py（--dummy-armor）与 bl_batch.py（plan）共用这一份，
# 避免两处清单各自漂移（那正是"写对了名字却静默无效"的温床）。
DUMMY_ARMOR_PARTS = {
    "head": "dummyArmorHead",
    "torso": "dummyArmorTorso",
    "legs": "dummyArmorLegs",
    "arms": "dummyArmorArms",
}


def parse_dummy_armor(text):
    """把 ``head=45,torso=35`` 解析成 ``{dummyArmorHead: 45.0, ...}``；**非法即抛 ValueError**。

    绝不静默跳过：2026-09-24 有 9 场实验因为参数被静默丢弃而整批作废
    （`Jmini.Num` 只吃数字字符，字符串值被判成"读不到"，没有任何报错）。
    未知部位名 / 非数字 / 缺 `=` / 缺值 / 对象形式 —— 全部报错，把问题挡在发命令之前。

    空串与纯空白 ⇒ 空 dict（= 不覆盖任何部位，与 CLI 默认一致）。
    """
    if isinstance(text, dict):
        raise ValueError('dummyArmor must be a string (e.g. "head=45,torso=35"), not an object')
    if text is None:
        return {}
    if not isinstance(text, str):
        raise ValueError("dummyArmor must be a string, got %s" % type(text).__name__)
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError("dummyArmor segment has no '=': %r (expected head=45,torso=35)" % part)
        k, v = part.split("=", 1)
        k = k.strip().lower()
        if not k:
            raise ValueError("dummyArmor segment has no part name: %r" % part)
        if k not in DUMMY_ARMOR_PARTS:
            raise ValueError("unknown armor part %r (available: %s)"
                             % (k, ", ".join(sorted(DUMMY_ARMOR_PARTS))))
        v = v.strip()
        try:
            out[DUMMY_ARMOR_PARTS[k]] = float(v)
        except ValueError:
            raise ValueError("armor part %s has a non-numeric value: %r" % (k, v))
    return out


# ── 多兵种 / 战术组（v0.8.8）：扁平字符串 DSL ─────────────────────────────
# 为什么是字符串而不是嵌套 JSON：C# 侧的自研解析器 `Jmini` 只读**裸值**——把字符串
# 传给它既不报错也不生效（静默用 fallback），嵌套结构更传不过去。这与
# `--dummy-armor head=45,torso=35` 是同一套思路（2026-09-24 的 9 场实验就是这么废掉的）。

SQUAD_FORMATIONS = ("Infantry", "Ranged", "Cavalry", "HorseArcher", "Skirmisher",
                    "HeavyInfantry", "LightCavalry", "HeavyCavalry", "General", "Bodyguard")
SQUAD_MOVEMENTS = ("charge", "advance", "fallback", "stop", "retreat")
# 已移除的 movement 及其替代（补位提示，GC3）：只放这一条，别顺手加别的。
REMOVED_MOVEMENTS = {"hold": "stop"}
_FORMATION_BY_LOWER = dict((n.lower(), n) for n in SQUAD_FORMATIONS)


def parse_squad_groups(text):
    """解析 ``troop:count[:formation[:movement]]``（多组用 ``|``）→ list[dict]。

    ``formation`` 大小写不敏感、回写为规范名（GC1）；两个可选字段缺省时是 None
    （默认值由调用方决定：C# 侧编队走引擎默认、movement 走 ``charge``）。
    **非法输入一律抛 ValueError，绝不静默丢弃** —— 理由同 parse_dummy_armor。
    """
    if text is None:
        return []
    if not isinstance(text, str):
        raise ValueError("group string must be a string, got %s" % type(text).__name__)
    if not text.strip():
        return []
    out = []
    for idx, part in enumerate(text.split("|"), 1):
        part = part.strip()
        if not part:
            raise ValueError("group %d is empty (expected troop:count[:formation[:movement]], "
                             "groups separated by |)" % idx)
        fields = [f.strip() for f in part.split(":")]
        if len(fields) not in (2, 3, 4):
            raise ValueError("group %d %r has %d fields; expected troop:count[:formation[:movement]]"
                             % (idx, part, len(fields)))
        troop, cnt = fields[0], fields[1]
        if not troop:
            raise ValueError("group %d %r has no troop id" % (idx, part))
        try:
            count = int(cnt)
        except ValueError:
            raise ValueError("group %d %r has a non-integer count: %r" % (idx, part, cnt))
        if count < 1:
            raise ValueError("group %d %r count must be >= 1, got %d" % (idx, part, count))
        formation = None
        if len(fields) >= 3 and fields[2]:
            formation = _FORMATION_BY_LOWER.get(fields[2].lower())
            if formation is None:
                raise ValueError("group %d %r has an unknown formation: %r (available: %s)"
                                 % (idx, part, fields[2], ", ".join(SQUAD_FORMATIONS)))
        movement = None
        if len(fields) == 4 and fields[3]:
            movement = fields[3].lower()
            if movement not in SQUAD_MOVEMENTS:
                hint = ""
                if movement in REMOVED_MOVEMENTS:
                    repl = REMOVED_MOVEMENTS[movement]
                    hint = " -- %s was removed (at the engine level it equals %s); use %s" % (
                        movement, repl, repl)
                raise ValueError("group %d %r has an unknown movement: %r (available: %s)%s"
                                 % (idx, part, fields[3], ", ".join(SQUAD_MOVEMENTS), hint))
        out.append({"troop": troop, "count": count, "formation": formation, "movement": movement})
    return out
