#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
`bl_json_health` 自测（宿主侧，离线：不需要游戏、不需要 BlBridge 仓库在跑）。

## 覆盖
  ① 分类器认三种**实测原文**形态（不是编的）
  ② 全好目录 => 退出码 0；有坏文件 => 退出码 1（可做闸门）
  ③ **编码不变式**：输出可按 GBK 编码
     -- 这条是**本工具自己踩过的坑**（2026-10-06 在 GBK 控制台打 emoji 崩掉），
        与 `AGENTS.md` 一「写入编码 ≠ 读取编码」同类，必须有回归闸门。
  ④ 坏文件混在正常文件里也能**全部**找出来（不许只报第一个）
  ⑤ `stillLive` 判据：最新若干文件里还有畸形 => True（决定「是不是历史遗留」）

## ! 环境坑（本机实测 2026-10-06，**所有 tools/ 下用 tempfile 的自测都中招**）

`tempfile.mkdtemp()` 之后**连单层 `mkdir` 都 WinError 5**（权限拒绝），
在 `%TEMP%` 与工作区**都一样**。已最小复现：
    tmp = tempfile.mkdtemp(); os.mkdir(tmp + "\\logs")   -> WinError 5
=> 本自测**完全不用 `tempfile`**，只在 `tools/` 下建**深度 1 的固定名目录**，
  跑完自删。（对照：`bl_selftest.py:1344`、`bl_patches_selftest.py` 都因这条跑不了，
  与本次改动无关 -- `git status` 可证。）
"""
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bl_json_health as H  # noqa: E402

# 深度 1、固定名（不用 tempfile、不做嵌套）
DIR_GOOD = os.path.join(HERE, "_bljh_case_good")
DIR_MIXED = os.path.join(HERE, "_bljh_case_mixed")

_fail = []


def check(cond, name, detail=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name
          + ("" if cond else "  <- " + str(detail)))
    if not cond:
        _fail.append(name)


# ① 三种形态的**实测原文**（取自 commands\done\ 的真实坏文件）
SAMPLES = [
    ("missing_colon", '{"a":[{"Prefixes","type":"X","count":0}]}'),
    ("key_value_swap", '{"a":[{"Prefixes":"type":"X","count":0}]}'),
    ("unquoted_value", '{"top":[{"type":System.Security.Cryptography.CryptographicException,"count":44}]}'),
]


def _fresh(path):
    """建/清一个**深度 1** 的目录（不用 tempfile）。"""
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    os.mkdir(path)
    return path


def _cleanup():
    for d in (DIR_GOOD, DIR_MIXED):
        shutil.rmtree(d, ignore_errors=True)


def test_classify():
    print("\n① 分类器认三种实测形态")
    for want, raw in SAMPLES:
        try:
            json.loads(raw)
            check(False, "样本应解析失败：%s" % want, "竟然解析成功")
            continue
        except ValueError as exc:
            kind, _hint = H.classify(str(exc))
            check(kind == want, "归类为 %s" % want, "实际 %s" % kind)


def test_scan_and_gate():
    print("\n② 退出码闸门 + ④ 全部坏文件都找到 + ⑤ stillLive")
    _fresh(DIR_GOOD)
    for i in range(3):
        with io.open(os.path.join(DIR_GOOD, "ok%d.json" % i), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ok": True, "i": i}))
    res = H.scan(DIR_GOOD)
    check(res["total"] == 3 and res["parseable"] == 3 and res["malformed"] == 0,
          "全好目录：3/3 可解析", res)
    check(res["stillLive"] is False, "全好目录 stillLive=False", res["stillLive"])

    _fresh(DIR_MIXED)
    for i in range(5):
        with io.open(os.path.join(DIR_MIXED, "ok%d.json" % i), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ok": True}))
    for i, (_w, raw) in enumerate(SAMPLES):
        with io.open(os.path.join(DIR_MIXED, "bad%d.json" % i), "w", encoding="utf-8") as fh:
            fh.write(raw)
    res = H.scan(DIR_MIXED)
    check(res["total"] == 8 and res["parseable"] == 5 and res["malformed"] == 3,
          "混合目录：5 好 / 3 坏，**三个都找到**（不许只报第一个）", res)
    check(len(set(b["kind"] for b in res["bad"])) == 3, "三种形态都被区分", res["kinds"])
    # 坏文件是最新的 3 个 => stillLive 必须为 True
    check(res["stillLive"] is True,
          "最新 20 个里有畸形 => stillLive=True（判「仍活着」）", res["inRecent20"])

    # 每条坏记录必须自带可复核上下文（本项目纪律：不给无法复核的结论）
    check(all(b["before"] or b["after"] for b in res["bad"]),
          "每条坏记录都带原始上下文（before/after）")
    check(all(not b["hint"].startswith("未归类") or b["kind"] == "other" for b in res["bad"]),
          "kind 与 hint 一致")


def test_report_and_gate():
    print("\n③ report 形状 + 编码不变式 + 退出码")
    _fresh(DIR_GOOD)
    with io.open(os.path.join(DIR_GOOD, "bad.json"), "w", encoding="utf-8") as fh:
        fh.write(SAMPLES[2][1])
    rep = H.build_report(directory=DIR_GOOD, limit=5)
    check(rep.get("ok") is True, "build_report ok", rep.get("ok"))
    check(rep.get("malformed") == 1, "build_report 认到 1 个畸形", rep.get("malformed"))
    check(bool(rep.get("report")), "report 文本非空")
    check("stillLive" in rep, "含 stillLive 判据")
    try:
        rep["report"].encode("gbk")
        check(True, "report 可按 GBK 编码（中文 Windows 控制台不崩）")
    except UnicodeEncodeError as exc:
        check(False, "report 可按 GBK 编码", exc)

    # limit 截断：badTruncated 必须如实计数
    _fresh(DIR_MIXED)
    for i, (_w, raw) in enumerate(SAMPLES):
        with io.open(os.path.join(DIR_MIXED, "bad%d.json" % i), "w", encoding="utf-8") as fh:
            fh.write(raw)
    rep2 = H.build_report(directory=DIR_MIXED, limit=1)
    check(len(rep2["bad"]) == 1 and rep2["badTruncated"] == 2,
          "limit=1 时明细截断为 1、badTruncated=2（不静默丢弃）",
          (len(rep2["bad"]), rep2.get("badTruncated")))

    # 目录不存在要如实报，不许假装 0 畸形
    rep3 = H.build_report(directory=os.path.join(HERE, "_no_such_dir_xyz"))
    check(rep3.get("ok") is False and rep3.get("reason") == "no_dir",
          "目录不存在 => ok=False / reason=no_dir（不伪装成 0 畸形）", rep3)


def test_source_hygiene():
    print("\n③b 源码卫生：不引入 emoji/箭头类符号（GBK 控制台会崩）")
    srcpath = os.path.join(HERE, "bl_json_health.py")
    with io.open(srcpath, "r", encoding="utf-8") as fh:
        src = fh.read()
    banned = ["\u26a0", "\u2705", "\u2713", "\u21d2", "\u2192"]
    hits = [hex(ord(b)) for b in banned if b in src]
    check(not hits, "bl_json_health.py 不含 emoji/箭头", hits)


def main():
    print("=" * 90)
    print("bl_json_health 自测（宿主侧离线）")
    print("=" * 90)
    try:
        test_classify()
        test_scan_and_gate()
        test_report_and_gate()
        test_source_hygiene()
    finally:
        _cleanup()
    print()
    print("=" * 90)
    if _fail:
        print("结果: 失败 %d 项" % len(_fail))
        for f in _fail:
            print("  - %s" % f)
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
