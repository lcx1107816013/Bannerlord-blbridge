#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真机验证：游戏在跑、BlBridge 已加载、Bridge→Archive 直连可用。

## 为什么需要这个脚本

项目里此前所有 Bridge 验证都是**离线**的：`bl_selftest.py` 用桩 CLI、`bl_check_*.py`
读文件、MCP 工具面用 `bc_status` 问进程。它们能证明"代码路径对"，但**证明不了
"游戏真的在响应"** —— 那是交接日志里唯一一条长期标着 `未验证` 的项。

而"未验证"和"已验证"的区别不是形式上的：本轮就是因为游戏起不来，
一个**真实的部署缺陷**（BLSE 被 Vortex 换成符号链接）被藏了一整天。

## 三件事，按依赖顺序

1. **游戏进程活着** —— 否则后面全是空的。
2. **BlBridge 真的加载了** —— 进程活着不等于 mod 加载了；前者是 Windows 的事实，
   后者是游戏内的事实。用 `list-ui` 的 `moduleLoaded` 判定，那是**游戏端自己报的**。
3. **Bridge→Archive 直连在真机下可用** —— 这是本轮新增的那条边。
   它要的不只是 Archive 起得来，还要**在游戏占着 CPU 的时候**起得来。

## 判据纪律

- 每一层都**单独报 PASS/FAIL**，不因为前一层过了就跳过后一层。
- 游戏不在跑时**报 SKIP 而不是 PASS** —— "没测"和"通过"必须分开，
  这正是本项目反复踩的那个坑。
- 退出码：0 = 全过，1 = 有 FAIL，2 = SKIP（游戏没跑）。

用法：
    python bl_live_verify.py
    python bl_live_verify.py --skip-archive   # 只验前两层
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILS = []
CHECKS = []


def check(label, ok, detail=""):
    CHECKS.append(label)
    if ok:
        print("  PASS  %s%s" % (label, ("  <- " + detail) if detail else ""))
    else:
        print("  FAIL  %s%s" % (label, ("  <- " + detail) if detail else ""))
        FAILS.append(label)
    return ok


def run_cmd(*args, timeout=180):
    """调 bl_cmd.py，返回 (ok, parsed_json, raw_text)。"""
    p = subprocess.run(
        [sys.executable, os.path.join(HERE, "bl_cmd.py")] + list(args),
        capture_output=True, timeout=timeout, cwd=HERE,
    )
    out = p.stdout.decode("utf-8", "replace")
    try:
        return p.returncode == 0, json.loads(out), out
    except ValueError:
        return False, None, out


# ── 1. 游戏进程 ──────────────────────────────────────────────────────────
def layer1():
    print("\n[1] 游戏进程")
    try:
        p = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Bannerlord*", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=60,
        )
        text = p.stdout.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return check("游戏进程可查", False, "tasklist 失败：%s" % exc)

    alive = "Bannerlord" in text and "No tasks" not in text
    if not check("游戏进程在跑", alive, text.strip().splitlines()[0][:80] if text.strip() else ""):
        return False
    return True


# ── 2. BlBridge 已加载（游戏端自报）──────────────────────────────────────
def layer2():
    print("\n[2] BlBridge 已加载（游戏端自报，不是看进程名）")
    ok, doc, raw = run_cmd("list-ui", timeout=180)
    if not check("list-ui 有响应", ok and doc is not None, raw[:160]):
        return False

    result = doc.get("result") or {}
    process = doc.get("process") or {}
    check("响应来自游戏进程", process.get("role") == "game",
          "pid=%s role=%s" % (process.get("pid"), process.get("role")))
    check("moduleLoaded = true", result.get("moduleLoaded") is True,
          "这是游戏端自己报的，不是我们推断的")
    check("协议版本匹配", doc.get("protocolVersion") == 1,
          "protocolVersion=%s" % doc.get("protocolVersion"))
    check("DLL 版本与仓库一致", process.get("assemblyVersion", "").startswith("0.8."),
          "assemblyVersion=%s" % process.get("assemblyVersion"))
    options = result.get("options") or []
    check("能读到真实 UI 入口", len(options) > 0, "%d 个选项" % len(options))
    print("        状态: activeState=%s topScreen=%s"
          % (result.get("activeState"), result.get("topScreen")))
    return ok


# ── 3. Bridge→Archive 直连（真机负载下）────────────────────────────────
def layer3():
    print("\n[3] Bridge→Archive 直连（游戏正在跑）")
    try:
        import bl_archive_symbols as bas
    except Exception as exc:  # noqa: BLE001
        return check("bl_archive_symbols 可导入", False, str(exc))

    # 一个确定存在于索引里的类型。用裸类型名，顺便验证「裸类型名也是合法输入」。
    out = bas.explain_symbols(["Hero"], max_source=120)
    if not check("Archive 在游戏运行时仍可拉起", out.get("ok") is True,
                 str(out.get("detail"))[:160]):
        return False

    row = (out.get("rows") or [{}])[0]
    check("Hero 查得到", row.get("status") == "ok", "status=%s" % row.get("status"))
    check("拿回了真实源码长度", (row.get("source_length") or 0) > 1000,
          "source_length=%s" % row.get("source_length"))

    # 反向对照：编造的类型必须报 not_found，不许报 ok
    out2 = bas.explain_symbols(["TaleWorlds.No.Such.TypeHere"], max_source=50)
    row2 = (out2.get("rows") or [{}])[0]
    check("编造的类型报 not_found（不是 ok）", row2.get("status") == "not_found",
          "status=%s  <- 若为 ok，说明 not-found 判据在真机下失效" % row2.get("status"))
    return True


def main():
    ap = argparse.ArgumentParser(description="Bridge 真机验证")
    ap.add_argument("--skip-archive", action="store_true", help="跳过第 3 层")
    args = ap.parse_args()

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    print("BlBridge 真机验证")
    print("=" * 62)

    if not layer1():
        print("\nSKIP: 游戏不在跑。")
        print("     这不是「通过」—— 是「这次没验证」。")
        print("     确认 BLSE 的文件是真文件而不是符号链接：")
        print("       Get-ChildItem \"<game>\\bin\\Win64_Shipping_Client\" -Filter 'Bannerlord.BLSE*' -Force |")
        print("         Select Name, Attributes   # ReparsePoint 表示是链接，BLSE 会推错游戏根目录")
        return 2

    layer2()
    if not args.skip_archive:
        layer3()

    print("\n" + "=" * 62)
    print("%d checks, %d failed" % (len(CHECKS), len(FAILS)))
    if FAILS:
        print("\nFAILED:")
        for f in FAILS:
            print("  - %s" % f)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
