#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""坏数据判据的**离线对照测试**（`BadDataSpec` 是纯 BCL ⇒ 可编进 jsontest）。

## 为什么必须有这个文件

坏数据扫描器**最容易的失效方式是"恒返回空 = 没问题"**：
- 它可能因为容器名写错（`Campaign.Armies` 实测不存在！）而**扫不到任何东西**；
- 也可能因为判据写反了而**从不报警**；
- 这两种情况下工具都会**报 ok:true、found:0**，看起来"存档很干净"。

⇒ 所以判据必须能用**合成样本**证明：**这种输入必红、那种输入不应红**。
本测试就是那组"必红 / 不应红"的对照（每条都成对，缺一不可）。

## 与 jsontest 的关系

本文件由 `tools/jsontest/BadDataSpecTest.cs` 调用（那边是 C# 入口，因为判据是 C#）。
这里只放**用例表**，方便改断言时不碰 C#。
"""
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
CSC_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe",
    r"C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\Roslyn\csc.exe",
]

FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    if cond:
        print("  [OK]   %s" % name)
    else:
        FAILS.append("%s%s" % (name, (" -- " + str(detail)) if detail else ""))
        print("  [FAIL] %s%s" % (name, (" -- " + str(detail)) if detail else ""))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                     # noqa: BLE001
        pass

    csc = None
    for c in CSC_CANDIDATES:
        if os.path.isfile(c):
            csc = c
            break
    if not csc:
        print("[ENV] 找不到 csc.exe")
        return 2

    out_dir = os.path.join(REPO, "out")
    os.makedirs(out_dir, exist_ok=True)
    exe = os.path.join(out_dir, "baddataspec_test.exe")
    srcs = [
        os.path.join(HERE, "BadDataSpecTest.cs"),
        os.path.join(REPO, "src", "BadDataSpec.cs"),
        os.path.join(REPO, "src", "Jmini.cs"),
        os.path.join(REPO, "src", "JsonlWriter.cs"),     # 提供 Jw.N（BadDataSpec 用它出数字）
        os.path.join(REPO, "src", "BuildInfo.cs"),       # JsonlWriter 依赖它
        os.path.join(REPO, "src", "BridgeConfig.cs"),    # JsonlWriter/BuildInfo 读配置
        os.path.join(REPO, "src", "BridgeProtocol.cs"),  # 提供 Protocol.Q / Protocol.Success
    ]
    cmd = [csc, "/nologo", "/target:exe", "/r:System.Web.Extensions.dll",
           "/out:" + exe] + srcs
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("[FAIL] 编译失败：\n" + (r.stdout or "") + (r.stderr or ""))
        return 1
    print("[1/2] 编译 OK -> %s" % exe)
    print("[2/2] 跑判据对照")
    run = subprocess.run([exe], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    print(run.stdout or "")
    if run.stderr:
        print("stderr: " + run.stderr[:800])
    return run.returncode


if __name__ == "__main__":
    sys.exit(main())
