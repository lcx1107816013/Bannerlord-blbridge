#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BlBridge → BannerlordSage 的**只读**数据通道（可选依赖，不是硬依赖）。

为什么存在
----------
``bl_start_battle`` 的兵种 id 来自人手敲或批量 plan。敲错 id 的代价不是报错，
而是**等一场 10 分钟的对局打完才发现那场根本没按预期开局**（C# 侧 troop 不存在时
静默走 fallback，日志里看不出来）。BannerlordSage 已经把全量兵种索引进了一个
SQLite 文件，直接只读连它，比 BlBridge 自己再解析一遍 ModuleData 划算得多。

设计取舍（每一条都对应一个真实的坑）
------------------------------------
1. **只读打开**（``mode=ro``）。BannerlordSage 是长驻进程、持有写连接并设了
   ``PRAGMA busy_timeout``；我们写会撞锁，也会污染它的索引。
2. **软依赖**。db 不存在 / schema 变了 / 根本没装 Sage ⇒ ``available=False`` +
   ``reason``，**绝不抛异常、绝不阻断 BlBridge 主流程**。理由：BlBridge 必须能
   在没有 BannerlordSage 的机器上独立使用。调用方拿到 ``available=False`` 时
   自行决定是降级警告还是硬失败。
3. **只依赖业务表** ``bannerlord_troops``，不碰 ``*_fts`` 全文索引虚拟表 ——
   那些是 FTS5 虚拟表，能否查询取决于 Python 的编译选项，不稳。
4. **每次查询开短连接**。索引只有 1981 行兵种，本地文件打开是微秒级；短连接
   比长驻连接更不容易和 Sage 的写操作互相牵连。
5. **schema 变更要能被看出来**：启动时校验表存在 + 必需列齐全，缺了就报
   "schema 变了"并原样返回实际列名，而不是抛个 KeyError 让人猜。

用法
----
    import bl_sage
    st = bl_sage.status()                       # 可用性 + 兵种总数
    one = bl_sage.lookup("imperial_legionary")  # 单个兵种 or None
    chk = bl_sage.check_troops(["imperial_legionary", "battanian_fian"])

命令行（便于自测，也便于 CLI 复用）::

    python tools/bl_sage.py --status
    python tools/bl_sage.py --check imperial_legionary battanian_fian_champion
    python tools/bl_sage.py --search imperial_% --culture empire --limit 10

数据库路径可用环境变量 ``BANNERSAGE_DB`` 覆盖（默认探 BannerlordSage 的 dist 目录）。
"""
import argparse
import difflib
import os
import re
import sqlite3
import sys

# BannerlordSage 的 src/utils/env.ts: dbPath = dist/games/<gameId>/<gameId>.db
DEFAULT_DB_PATH = os.path.join("F:\\", "Program Files", "BannerlordSage",
                               "dist", "games", "bannerlord", "bannerlord.db")

TROOPS_TABLE = "bannerlord_troops"
# 只用到这几列；缺任何一列就判定 schema 变了（新增列不影响向后兼容）
REQUIRED_TROOP_COLUMNS = ("characterId", "name", "level", "culture", "occupation")

# name 列是 "{=JGsUtBT3}Imperial Legionary" 形式：本地化键 + 英文原文
_LOCALIZED_RE = re.compile(r"^\{=[^}]*\}")


def db_path():
    """优先级：环境变量 BANNERSAGE_DB > 默认探测路径。"""
    return os.environ.get("BANNERSAGE_DB") or DEFAULT_DB_PATH


def clean_name(raw):
    """剥掉 '{=XXX}' 本地化键前缀，只留可读名（该列里存的就是英文原文）。"""
    if not raw:
        return None
    return _LOCALIZED_RE.sub("", str(raw))


def _open_readonly():
    """返回 (connection, error_message)，二选一非空。"""
    path = db_path()
    if not os.path.isfile(path):
        return None, ("未找到索引数据库：%s"
                      "（没装 BannerlordSage，或索引尚未构建 —— 不影响 BlBridge 使用）" % path)
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    except sqlite3.Error as exc:
        return None, "打开索引数据库失败：%r" % (exc,)
    return con, None


def _table_columns(con, table):
    try:
        return [row[1] for row in con.execute("PRAGMA table_info(%s)" % table)]
    except sqlite3.Error:
        return []


def _verify_schema(con):
    """表 + 必需列校验。返回 (ok, reason, columns)。"""
    cols = _table_columns(con, TROOPS_TABLE)
    if not cols:
        return False, ("索引库里没有表 %s —— BannerlordSage 版本可能变了，"
                       "或索引未构建" % TROOPS_TABLE), cols
    missing = [c for c in REQUIRED_TROOP_COLUMNS if c not in cols]
    if missing:
        return False, ("表 %s 缺少预期列 %s（schema 变了，请核对 BannerlordSage 版本）；"
                       "实际列：%s" % (TROOPS_TABLE, ", ".join(missing), ", ".join(cols))), cols
    return True, None, cols


def _row_to_troop(row, cols):
    d = dict(zip(cols, row))
    raw_level = d.get("level")
    try:
        level = int(str(raw_level).strip())
    except (TypeError, ValueError):
        level = None          # 部分 NPC（如 CustomBattle 的 commander_1）没有等级
    return {
        "id": d.get("characterId"),
        "name": clean_name(d.get("name")),
        "rawName": d.get("name"),
        "level": level,
        "culture": d.get("culture"),
        "occupation": d.get("occupation"),
        "isHero": str(d.get("isHero")) == "1",
        "filePath": d.get("filePath"),
    }


def status():
    """可用性自检。永远返回 dict，不抛异常。"""
    out = {
        "available": False,
        "path": db_path(),
        "pathSource": ("env:BANNERSAGE_DB" if os.environ.get("BANNERSAGE_DB") else "default"),
    }
    con, err = _open_readonly()
    if err:
        out["reason"] = err
        return out
    try:
        ok, reason, cols = _verify_schema(con)
        out["columns"] = cols
        if not ok:
            out["reason"] = reason
            return out
        out["troopCount"] = con.execute("SELECT COUNT(*) FROM %s" % TROOPS_TABLE).fetchone()[0]
        out["available"] = True
        out["reason"] = None
        return out
    except sqlite3.Error as exc:
        out["reason"] = "查询失败：%r" % (exc,)
        return out
    finally:
        con.close()


def lookup(character_id):
    """单个兵种，命中返回 dict，未命中/不可用返回 None。"""
    if not character_id:
        return None
    con, err = _open_readonly()
    if err:
        return None
    try:
        ok, _reason, cols = _verify_schema(con)
        if not ok:
            return None
        row = con.execute(
            "SELECT * FROM %s WHERE characterId = ?" % TROOPS_TABLE, (character_id,)).fetchone()
        return _row_to_troop(row, cols) if row else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def check_troops(ids):
    """批量校验兵种 id。**这是给 bl_start_battle 做前置校验用的**。

    返回 dict：
      available  索引是否可用（False 时调用方应降级而不是硬失败）
      reason     不可用的原因（可用时 None）
      found      [兵种 dict]，按传入顺序
      missing    [字符串]，库里查不到的 id
    """
    if isinstance(ids, str):
        ids = [ids]          # 传了单个字符串时别逐字符迭代
    out = {"available": False, "reason": None, "found": [], "missing": []}
    wanted = [i for i in (ids or []) if i]
    if not wanted:
        out["reason"] = "没有需要校验的兵种 id"
        return out

    con, err = _open_readonly()
    if err:
        out["reason"] = err
        return out
    try:
        ok, reason, cols = _verify_schema(con)
        if not ok:
            out["reason"] = reason
            return out

        found = {}
        # SQLite 参数上限远高于此，但一次几十个 id 分批更稳
        for i in range(0, len(wanted), 200):
            chunk = wanted[i:i + 200]
            ph = ",".join("?" * len(chunk))
            sql = "SELECT * FROM %s WHERE characterId IN (%s)" % (TROOPS_TABLE, ph)
            for row in con.execute(sql, chunk):
                t = _row_to_troop(row, cols)
                found[t["id"]] = t

        out["available"] = True
        out["reason"] = None
        out["found"] = [found[i] for i in wanted if i in found]
        out["missing"] = [i for i in wanted if i not in found]
        return out
    except sqlite3.Error as exc:
        out["reason"] = "查询失败：%r" % (exc,)
        return out
    finally:
        con.close()


def search(pattern=None, culture=None, limit=20):
    """按 id 模糊/文化筛兵种，用于「我忘记 id 叫什么」的场景。

    pattern 走 SQL LIKE（``%`` 通配）；culture 走等值匹配，允许省略 ``Culture.`` 前缀
    （库里存的是 ``Culture.empire``）。返回 list[dict]，不可用返回 []。

    ⚠️ culture 匹配**区分大小写**，必须与库中存的值一致 —— 例如 ``empire`` 或
    ``Culture.empire``；写成 ``EMPIRE`` 会得到空结果（不是「没这个文化」，是大小写不符）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    if limit <= 0:
        limit = 20             # limit<0 会被 SQLite 当成「无限制」返回全表
    con, err = _open_readonly()
    if err:
        return []
    try:
        ok, _reason, cols = _verify_schema(con)
        if not ok:
            return []
        where, args = [], []
        if pattern:
            where.append("characterId LIKE ?")
            args.append(str(pattern))
        if culture:
            want = str(culture)
            if not want.lower().startswith("culture."):
                want = "Culture." + want
            where.append("culture = ?")
            args.append(want)
        sql = "SELECT * FROM %s" % TROOPS_TABLE
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY characterId LIMIT ?"
        args.append(limit)
        # 同一兵种 id 在官方 XML 里可能被多个文件重复定义（SandBoxCore 与
        # CustomBattle 都写了 imperial_infantryman 之类）—— 按 id 去重取首条，
        # 否则建议列表/搜索结果里会出现同名条目。
        out, seen = [], set()
        for r in con.execute(sql, args):
            t = _row_to_troop(r, cols)
            if t["id"] in seen:
                continue
            seen.add(t["id"])
            out.append(t)
        return out
    except sqlite3.Error:
        return []
    finally:
        con.close()


def suggest_troops(bad_id, limit=5):
    """拼错 id 时给几个相近候选：取首段做前缀匹配，再按相似度排序。

    为什么不能只按字母序取前 N 个：实测 `imperial_legionarry` 的字母序前 5 里
    根本没有 `imperial_legionary`（它按字母序排在后面）—— 建议列表里偏偏缺了
    最该给的那个。用 difflib（标准库）按相似度重排，拼错一两个字母时正确 id
    会排到第一。只作提示，不参与判定 —— 索引只覆盖官方 XML，第三方模组兵种
    本就查不到，所以「给不出候选」不等于「这个 id 一定错」。

    放在 bl_sage 而不是 bl_mcp：bl_batch.py 按设计只依赖数据通道、不 import
    bl_mcp（见其文件头注释），而批量预检同样需要给建议。
    """
    if not bad_id or not isinstance(bad_id, str):
        return []
    head = bad_id.split("_")[0].strip()
    if not head:
        return []
    cands = [t["id"] for t in search(head + "%", None, 100)]
    if not cands:
        return []
    cands.sort(key=lambda c: difflib.SequenceMatcher(None, bad_id, c).ratio(), reverse=True)
    return cands[:limit]


def _main(argv=None):
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="BlBridge → BannerlordSage 只读数据通道")
    ap.add_argument("--status", action="store_true", help="可用性自检")
    ap.add_argument("--check", nargs="+", metavar="TROOP_ID", help="校验兵种 id 是否存在")
    ap.add_argument("--search", metavar="PATTERN", help="按 id 模糊搜索（%% 通配）")
    ap.add_argument("--culture", help="配合 --search，按文化过滤；区分大小写，需与库中一致（empire 或 Culture.empire）")
    ap.add_argument("--limit", type=int, default=20, help="--search 返回条数，默认 20")
    args = ap.parse_args(argv)

    if not (args.status or args.check or args.search):
        ap.print_help()
        return 1

    if args.status:
        st = status()
        print("available : %s" % st["available"])
        print("path      : %s  (source: %s)" % (st["path"], st["pathSource"]))
        if st["available"]:
            print("troops    : %s" % st.get("troopCount"))
        else:
            print("reason    : %s" % st["reason"])
        return 0 if st["available"] else 2

    if args.check:
        chk = check_troops(args.check)
        if not chk["available"]:
            print("索引不可用：%s" % chk["reason"])
            return 2
        for t in chk["found"]:
            print("OK    %-32s L%-4s %-18s %s" % (t["id"], t["level"], t["culture"], t["name"]))
        for m in chk["missing"]:
            print("MISS  %-32s （索引里没有这个兵种 id）" % m)
        return 0 if not chk["missing"] else 1

    rows = search(args.search, args.culture, args.limit)
    if not rows:
        print("（没有匹配，或索引不可用 —— 先用 --status 确认）")
        return 2
    for t in rows:
        print("%-34s L%-4s %-18s %s" % (t["id"], t["level"], t["culture"], t["name"]))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
