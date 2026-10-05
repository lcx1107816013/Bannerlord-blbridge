#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
`bl_ipc_replay` 自测（宿主侧离线：不需要游戏、不需要真 BlBridge 日志）。

## 覆盖
  ① 账本 -> done 的 **id 连接**：能给响应补上 method（这是本工具存在的理由：
     响应体里**不含 method 名**，不读账本根本没法按工具检索）
  ② `method=` 过滤 + `okOnly` 过滤
  ③ `grep=` 覆盖**全部**响应（不依赖账本）—— 且 `scannedFiles` 报的是**全量**，
     不是命中数（这条是实测踩过的口径 bug：第一版把"扫描 12991"显示成"搜索范围 3"）
  ④ **覆盖率边界如实报出**：账本只覆盖一部分响应，工具必须报 `ledgerCoverage`，
     且按 method 查不到时要提示"改用 grep"（**不许把"账本里没有"说成"没发生过"**）
  ⑤ 畸形响应**照样列出并标记**，不静默跳过
  ⑥ 目录不存在 / 账本缺失时如实报，不伪装成"0 命中"

## ! 环境坑
同 `bl_json_health_selftest`：沙箱里 `tempfile.mkdtemp()` 后嵌套建目录会
WinError 5 ⇒ 本自测只在 `tools/` 下建**深度 1 的固定名目录**，跑完自删。
"""
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bl_common      # noqa: E402
import bl_ipc_replay as R  # noqa: E402

CASE = os.path.join(HERE, "_blreplay_case")
_fail = []


def check(cond, name, detail=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name
          + ("" if cond else "  <- " + str(detail)))
    if not cond:
        _fail.append(name)


def _resp(req_id, ok=True, pid=1234, extra=None):
    """造一份**结构真实**的响应（含 process 块，与游戏端同形）。"""
    obj = {
        "protocolVersion": 1,
        "id": req_id,
        "ok": ok,
        "process": {"pid": pid, "role": "game", "runToken": "tok" + req_id[:4],
                    "assemblyVersion": "0.8.46.0"},
        "result": extra if extra is not None else {"ok": True},
        "error": None if ok else {"code": "some_code", "message": "boom",
                                  "outcomeUncertain": False},
    }
    return json.dumps(obj, ensure_ascii=False)


def setup():
    """建一个合成日志目录：commands/done/*.json + commands/actions.jsonl。

    三条响应的设计：
      A(id=aaaa...) 有账本，method=get_patches
      B(id=bbbb...) 有账本，method=start_battle 且 **ok=False**
      C(id=cccc...) **无账本**（模拟账本启用前的历史）—— 只有 grep 能找到
      D(id=dddd...) 畸形 JSON（无账本）
    """
    if os.path.isdir(CASE):
        shutil.rmtree(CASE, ignore_errors=True)
    os.mkdir(CASE)
    cmds = os.path.join(CASE, "commands")
    os.mkdir(cmds)
    done = os.path.join(cmds, "done")
    os.mkdir(done)

    with io.open(os.path.join(done, "aaaa111122223333.json"), "w", encoding="utf-8") as fh:
        fh.write(_resp("aaaa111122223333", True, 111, {"ok": True, "scannedMethods": 1194}))
    with io.open(os.path.join(done, "bbbb111122223333.json"), "w", encoding="utf-8") as fh:
        fh.write(_resp("bbbb111122223333", False, 222, None))
    with io.open(os.path.join(done, "cccc111122223333.json"), "w", encoding="utf-8") as fh:
        fh.write(_resp("cccc111122223333", True, 333, {"unique_marker_xyz": 1}))
    # 畸形：值缺引号（与真实现网缺陷同形）
    with io.open(os.path.join(done, "dddd111122223333.json"), "w", encoding="utf-8") as fh:
        fh.write('{"top":[{"type":System.Security.Cryptography.CryptographicException,"count":44}]}')

    ledger = os.path.join(cmds, "actions.jsonl")
    with io.open(ledger, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": "2026-10-05T20:00:00.000Z", "seq": 1,
                             "runToken": "tok1", "id": "aaaa111122223333",
                             "method": "get_patches", "ok": True, "code": "",
                             "ms": 11.0, "bytes": 100, "uncertain": False,
                             "note": "", "args": "{}"}, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"t": "2026-10-05T20:01:00.000Z", "seq": 2,
                             "runToken": "tok2", "id": "bbbb111122223333",
                             "method": "start_battle", "ok": False,
                             "code": "some_code", "ms": 1.0, "bytes": 50,
                             "uncertain": False, "note": "x", "args": "{}"},
                            ensure_ascii=False) + "\n")
    return CASE


def cleanup():
    shutil.rmtree(CASE, ignore_errors=True)


def test_join():
    print("\n① 账本 -> done 的 id 连接（本工具存在的理由）")
    r = R.build_report(log_dir=CASE, method="get_patches", limit=5)
    check(r["ok"] is True, "build_report ok", r.get("reason"))
    check(r["ledgerMatches"] == 1, "账本命中 1 条 get_patches", r["ledgerMatches"])
    check(len(r["items"]) == 1, "取到 1 份响应", len(r["items"]))
    if r["items"]:
        it = r["items"][0]
        check(it["ledger"] and it["ledger"]["method"] == "get_patches",
              "响应对上了 method（响应体自身不含 method，靠账本补）", it.get("ledger"))
        check(it["response"] and it["response"]["pid"] == 111,
              "响应的 pid 读出来了", it.get("response"))


def test_filters():
    print("\n② method= / okOnly 过滤")
    r = R.build_report(log_dir=CASE, method="start_battle", ok_only=False, limit=5)
    check(r["ledgerMatches"] == 1, "start_battle + ok=False 命中 1", r["ledgerMatches"])
    if r["items"]:
        check(r["items"][0]["response"]["ok"] is False, "响应确实 ok=false")
        check((r["items"][0]["response"].get("error") or {}).get("code") == "some_code",
              "错误码读出来", r["items"][0]["response"].get("error"))
    r2 = R.build_report(log_dir=CASE, method="get_patches", ok_only=False, limit=5)
    check(r2["ledgerMatches"] == 0, "get_patches + ok=False 命中 0（过滤真的生效）", r2["ledgerMatches"])


def test_grep_covers_all():
    print("\n③ grep 覆盖全部响应（不依赖账本）+ 扫描/命中口径分开")
    # cccc 无账本，只有 grep 能找到
    r = R.build_report(log_dir=CASE, grep="unique_marker_xyz", limit=5)
    check(r["matchedFiles"] == 1, "grep 找到无账本的那份（证明覆盖全量）", r["matchedFiles"])
    check(r["scannedFiles"] == 4, "scannedFiles = 全部 4 份（**不是命中数**）", r["scannedFiles"])
    check(r["scannedFiles"] != r["matchedFiles"], "扫描数 != 命中数（口径没混）",
          (r["scannedFiles"], r["matchedFiles"]))
    if r["items"]:
        check(r["items"][0]["ledger"] is None,
              "无账本的响应 ledger=None（如实报，不编一个 method）",
              r["items"][0].get("ledger"))


def test_coverage_boundary():
    print("\n④ 覆盖率边界如实报出 + 查不到时给下一步")
    r = R.build_report(log_dir=CASE, method="get_patches", limit=5)
    check("ledgerCoverage" in r, "报了 ledgerCoverage", r.keys())
    check(abs(r["ledgerCoverage"] - 0.5) < 1e-6,
          "覆盖率 = 2/4 = 0.5（账本只覆盖一部分）", r["ledgerCoverage"])
    # 按 method 找不到那个**无账本**的响应 ⇒ 必须提示改用 grep，而不是说"没有"
    r2 = R.build_report(log_dir=CASE, method="no_such_method_xyz", limit=5)
    check("grep" in r2["report"] and "账本" in r2["report"],
          "查不到时提示「按 method 只覆盖账本范围，请改用 grep」", r2["report"][-260:])


def test_malformed_listed():
    print("\n⑤ 畸形响应照样列出并标记（不静默跳过）")
    r = R.build_report(log_dir=CASE, grep="Cryptographic", limit=5)
    check(r["matchedFiles"] == 1, "grep 能命中畸形文件", r["matchedFiles"])
    if r["items"]:
        it = r["items"][0]
        check(it["malformed"] is True, "被标记为畸形", it.get("malformed"))
        check("畸形" in r["report"], "报告里点明畸形", "")
        check(bool(it.get("rawHead")), "仍带原文（可复核）")


def test_no_dir():
    print("\n⑥ 目录不存在时如实报，不伪装成 0 命中")
    r = R.build_report(log_dir=os.path.join(HERE, "_no_such_logdir_xyz"))
    check(r["ok"] is False and r["reason"] == "no_dir", "ok=False / reason=no_dir", r)


def main():
    # 输出统一 UTF-8（写入编码 != 读取编码，见 bl_common.safe_streams）
    bl_common.safe_streams()
    print("=" * 84)
    print("bl_ipc_replay 自测（宿主侧离线）")
    print("=" * 84)
    try:
        setup()
        test_join()
        test_filters()
        test_grep_covers_all()
        test_coverage_boundary()
        test_malformed_listed()
        test_no_dir()
    finally:
        cleanup()
    print()
    print("=" * 84)
    if _fail:
        print("结果: 失败 %d 项" % len(_fail))
        for f in _fail:
            print("  - %s" % f)
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
