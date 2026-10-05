#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
`bl_patches` 自测（离线，仅标准库）。

## 为什么这个自测非做不可

`bl_patches` 是 2026-10-06 新增的**唯一没有自测**的工具 —— 而它当天正是
**因为没先做自测，连烧了 8 轮构建**才定位三个坑（见 PROGRESS §四十八 5.3）：

  1. `Patches.Prefixes` 等是 **public readonly 字段**（不是属性）⇒ 用 `GetProperty` 恒 null；
  2. ★★ **`Jmini` 扁平读取撞信封** —— 请求信封自带 `"method"`，
     而 `Jmini` 不区分信封与 `parameters` ⇒ 过滤器被设成方法名 ⇒ **每个补丁都被跳过**
     ⇒ 静默 `scannedMethods=N / matchedMethods=0`（**看起来完全像"没有补丁"**）；
  3. 改源码后没重建。

**本自测覆盖**：
  · ① 参数**透传**：`method=` 必须落成 **`targetType`**（坑 2 的回归测试）；
  · ② ★ **保留键不变式**：`bl_patches` 发出的 `parameters` 里
    **绝不能出现**信封保留键（`protocolVersion`/`id`/`method`/`parameters`/`issuedUtc`）
    —— 这是坑 2 的**通用防线**，将来任何人加参数都会被它拦住；
  · ③ 参数缺省语义：不传 `conflicts`/`detail` 时**不发送**这两个键（让游戏侧用默认值）；
  · ④ 布尔与整型的**类型**正确（`true`/`false` 不是字符串、`limit` 是 int）；
  · ⑤ 响应为 `ok:false` 时**如实透出 code/message**（不被吞成"成功"）；
  · ⑥ 未知参数**不冒充**已知参数（如传 `blah` 不应改变 `targetType`）。

⚠️ 它**不需要游戏在跑**：用 `bl_selftest.start_fake_game` 起的假游戏端即可
（`bl_patches` 走文件 IPC，而 IPC 是纯文件读写）。
"""
import io
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bl_mcp        # noqa: E402
import bl_selftest   # noqa: E402  复用它的假游戏端（避免第二套 IPC 模拟）

FAILED = []

# ★ 信封保留键：`send_command` 构造请求时写死的顶层字段。
#   任何工具的 `parameters` 里出现同名字段，都会被 `Jmini` 的**扁平读取**误取。
#   这一条是本自测的**核心不变式**。
ENVELOPE_KEYS = ("protocolVersion", "id", "method", "parameters", "issuedUtc")


def check(cond, label, detail=""):
    if cond:
        print("  [OK]   %s" % label)
    else:
        print("  [FAIL] %s %s" % (label, detail))
        FAILED.append(label)


def _run(logdir, args, method="get_patches"):
    """起一次假游戏端 → 调 bl_mcp.call_tool → 返回 (响应, 假游戏端收到的 request)。

    ⚠️ 假游戏端的 handler 由本函数临时**打补丁**到 `bl_selftest.start_fake_game` 的循环里 ——
    但那函数读的是它自己的分支表。所以这里改用更直接的做法：
    **自己起一个最小 pending 轮询**，把收到的 request 原样记下来并回一个合成响应。
    （不改 bl_selftest 的分支表，避免影响它自己的 15 组测试。）
    """
    import threading

    pend = os.path.join(logdir, "commands", "pending")
    done = os.path.join(logdir, "commands", "done")
    for d in (pend, done):
        if not os.path.isdir(d):
            os.makedirs(d)

    with io.open(os.path.join(logdir, "bridge_status.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"mod": "BlBridge", "version": "0.1.1", "protocolVersion": 1,
                             "runToken": "selftest-patches", "pid": os.getpid(),
                             "processStartedUtc": "2026-10-06T00:00:00.000Z",
                             "state": "loaded"}))

    seen = {}
    stop = {"v": False}

    def loop():
        while not stop["v"]:
            try:
                names = sorted(os.listdir(pend))
            except OSError:
                names = []
            for f in names:
                if not f.endswith(".json"):
                    continue
                p = os.path.join(pend, f)
                try:
                    with io.open(p, "r", encoding="utf-8") as fh:
                        req = json.load(fh)
                except Exception:      # noqa: BLE001
                    continue
                try:
                    os.remove(p)
                except OSError:
                    pass
                seen["req"] = req
                rid = req.get("id")
                # 回一个**最小但结构正确**的合成响应（形状照真实响应）
                resp = {
                    "protocolVersion": 1, "id": rid, "ok": True,
                    "process": {"pid": os.getpid(), "role": "game",
                                "runToken": "selftest-patches",
                                "assemblyVersion": "0.8.46.0",
                                "loadedSha256": "0" * 16},
                    "result": {"ok": True, "available": True, "harmony": "0Harmony",
                               "harmonyVersion": "2.4.2.0",
                               "scannedMethods": 1194, "matchedMethods": 2,
                               "conflictMethods": 1, "returned": 2,
                               "methods": [
                                   {"target": "Some.Type.A", "owners": ["o1", "o2"],
                                    "ownerCount": 2, "prefix": 1, "postfix": 1,
                                    "transpiler": 0, "finalizer": 0,
                                    "conflict": True, "transpilerClash": False},
                               ]},
                    "error": None,
                }
                with io.open(os.path.join(done, rid + ".json"), "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(resp, ensure_ascii=False))

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        resp, err = bl_mcp.send_command(method, args, timeout=8, log_dir=logdir)
    finally:
        stop["v"] = True
        time.sleep(0.15)
    return resp, err, seen.get("req")


def main():
    print("=" * 78)
    print("bl_patches 自测（离线：文件 IPC + 假游戏端）")
    print("=" * 78)

    tmp = tempfile.mkdtemp(prefix="blbridge_patches_")
    try:
        # ── ① 参数透传：method -> targetType（坑 2 的回归测试）──
        print("\n① 参数透传：MCP 的 `method` 必须落成游戏侧的 `targetType`")
        resp2 = _run_call_tool(tmp, "bl_patches", {"method": "Mission"})
        got = (resp2 or {}).get("_sentParams") or {}
        check("targetType" in got, "映射后含 targetType", repr(got))
        check("method" not in got, "★ 映射后**不含** method（否则撞信封）", repr(got))
        check(got.get("targetType") == "Mission", "targetType 值正确", repr(got.get("targetType")))

        # ── ② ★★ 保留键不变式（通用防线）──
        print("\n② ★ 保留键不变式：parameters 里绝不许出现信封保留键")
        # ★ 这张表是**所有走控制通道的工具**的参数样本 —— 新增工具时往这里加一条。
        #   它是 AGENTS.md「控制通道参数命名硬规则」的**机器闸门**
        #   （该规则 2026-09-25 写下，而 2026-10-06 又被踩了一次 ⇒ 约定不够，要靠闸门）。
        cases = [
            ("bl_patches", {"method": "X", "owner": "o", "conflicts": True, "detail": False, "limit": 7}),
            ("bl_patches", {"method": "Mission", "owner": "com.rbmcampaign"}),
            ("bl_patches", {"conflicts": True}),
            ("bl_patches", {}),
            ("bl_mcm_settings", {"settingsId": "ButterLib", "summary": True,
                                 "withValues": False, "limit": 5}),
            ("bl_mcm_settings", {}),
            ("bl_ui_extensions", {"module": "RaiseYourBanner", "moviesOnly": True, "limit": 10}),
            ("bl_ui_extensions", {}),
        ]
        for tool, c in cases:
            _r = _run_call_tool(tmp, tool, c)
            sent = (_r or {}).get("_sentParams") or {}
            bad = [k for k in sent if k in ENVELOPE_KEYS]
            check(not bad, "%s %s ⇒ 无保留键冲突" % (tool, c or "{}"),
                  "撞了: %s / sent=%s" % (bad, sent))

        # ── ③ 缺省语义：不传就不发（让游戏侧用默认值）──
        print("\n③ 缺省语义：未传的键**不应**出现在 parameters 里")
        _r = _run_call_tool(tmp, "bl_patches", {"method": "Mission"})
        sent = (_r or {}).get("_sentParams") or {}
        check("conflicts" not in sent, "未传 conflicts ⇒ 不发", repr(sent))
        check("detail" not in sent, "未传 detail ⇒ 不发", repr(sent))
        check("limit" not in sent, "未传 limit ⇒ 不发", repr(sent))
        check("owner" not in sent, "未传 owner ⇒ 不发", repr(sent))

        # ── ④ 类型正确 ──
        print("\n④ 类型：布尔是真布尔、整数是真整数（不是字符串）")
        _r = _run_call_tool(tmp, "bl_patches",
                                {"method": "M", "conflicts": True, "detail": False, "limit": 7})
        sent = (_r or {}).get("_sentParams") or {}
        check(sent.get("conflicts") is True, "conflicts 为真布尔", repr(sent.get("conflicts")))
        check(sent.get("detail") is False, "detail 为假布尔", repr(sent.get("detail")))
        check(isinstance(sent.get("limit"), int) and not isinstance(sent.get("limit"), bool),
              "limit 为 int", repr(sent.get("limit")))

        # ── ⑤ ok:false 如实透出 ──
        print("\n⑤ 响应 ok:false 时必须如实透出 code/message（不得吞成成功）")
        r = _run_call_tool_raw(tmp, "bl_patches", {"method": "M"},
                               override={"ok": False,
                                         "error": {"code": "no_harmony",
                                                   "message": "没找到 Harmony"}})
        check(r.get("ok") is False, "工具返回 ok=False", repr(r.get("ok")))
        check(r.get("code") == "no_harmony", "透出 code", repr(r.get("code")))
        check("Harmony" in (r.get("error") or ""), "透出 message", repr(r.get("error")))

        # ── ⑥ 未知参数不冒充 ──
        print("\n⑥ 未知参数不得冒充已知参数")
        _r = _run_call_tool(tmp, "bl_patches", {"blah": "zzz"})
        sent = (_r or {}).get("_sentParams") or {}
        check("targetType" not in sent, "未知参数不会变成 targetType", repr(sent))
        check("owner" not in sent, "未知参数不会变成 owner", repr(sent))

    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILED:
        print("自测失败 %d 项：%s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("自测全部通过。")
    return 0


# ─────────────────────────────────────────────────────────────────────
# 走**工具层**（call_tool）的辅助：这样才能测到"参数映射"那段逻辑。
# ─────────────────────────────────────────────────────────────────────
def _run_call_tool(logdir, name, args):
    """兼容包装（历史命名）；实际工作交给 `_run_call_tool_raw`。"""
    return _run_call_tool_raw(logdir, name, args)


def _run_call_tool_raw(logdir, name, args, override=None):
    """真调一次 call_tool；用假游戏端回应（可 override 响应体）。"""
    import threading

    pend = os.path.join(logdir, "commands", "pending")
    done = os.path.join(logdir, "commands", "done")
    for d in (pend, done):
        if not os.path.isdir(d):
            os.makedirs(d)
    with io.open(os.path.join(logdir, "bridge_status.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"mod": "BlBridge", "version": "0.1.1", "protocolVersion": 1,
                             "runToken": "selftest-patches", "pid": os.getpid(),
                             "processStartedUtc": "2026-10-06T00:00:00.000Z",
                             "state": "loaded"}))

    stop = {"v": False}

    def loop():
        while not stop["v"]:
            try:
                names = sorted(os.listdir(pend))
            except OSError:
                names = []
            for f in names:
                if not f.endswith(".json"):
                    continue
                p = os.path.join(pend, f)
                try:
                    with io.open(p, "r", encoding="utf-8") as fh:
                        req = json.load(fh)
                except Exception:      # noqa: BLE001
                    continue
                try:
                    os.remove(p)
                except OSError:
                    pass
                _LAST["req"] = req
                rid = req.get("id")
                if override is not None:
                    resp = {"protocolVersion": 1, "id": rid, "ok": override.get("ok", True),
                            "process": {"pid": os.getpid(), "role": "game",
                                        "runToken": "selftest-patches",
                                        "assemblyVersion": "0.8.46.0",
                                        "loadedSha256": "0" * 16},
                            "result": override.get("result"),
                            "error": override.get("error")}
                else:
                    resp = {"protocolVersion": 1, "id": rid, "ok": True,
                            "process": {"pid": os.getpid(), "role": "game",
                                        "runToken": "selftest-patches",
                                        "assemblyVersion": "0.8.46.0",
                                        "loadedSha256": "0" * 16},
                            "result": {"ok": True, "available": True, "harmony": "0Harmony",
                                       "harmonyVersion": "2.4.2.0",
                                       "scannedMethods": 1194, "matchedMethods": 1,
                                       "conflictMethods": 0, "returned": 1, "methods": []},
                            "error": None}
                with io.open(os.path.join(done, rid + ".json"), "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(resp, ensure_ascii=False))

    _LAST.clear()
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        # 把 log_dir 指到临时目录：send_command 支持 log_dir 参数，
        # 但 call_tool 内部不带它 ⇒ 用环境变量把默认日志目录改过去。
        old = os.environ.get("BLBRIDGE_LOG_DIR")
        os.environ["BLBRIDGE_LOG_DIR"] = logdir
        try:
            out = bl_mcp.call_tool(name, args)
        finally:
            if old is None:
                os.environ.pop("BLBRIDGE_LOG_DIR", None)
            else:
                os.environ["BLBRIDGE_LOG_DIR"] = old
    finally:
        stop["v"] = True
        time.sleep(0.15)

    if isinstance(out, dict):
        sent = (_LAST.get("req") or {}).get("parameters") or {}
        out = dict(out)
        out["_sentParams"] = sent
    return out


_LAST = {}


if __name__ == "__main__":
    sys.exit(main())
