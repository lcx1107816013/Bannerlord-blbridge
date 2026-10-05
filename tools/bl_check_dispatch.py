#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MCP 工具「声明 ↔ 派发 ↔ 分组」三方一致性校验（只查 bl_mcp.py 一个文件）。

为什么要有这个脚本（2026-09-27 实测踩出来的坑）：
上一轮新增 `bl_get_screen` / `bl_get_viewmodel_property` 时，只往 `TOOLS = [...]` 表和
`gabp_names.json` 里加了条目，**漏了 `call_tool()` 里的派发分支**。后果是：

    tools/list 看得见这个工具（还有一大段描述），一调就返回 `unknown tool: bl_get_screen`

而当时的离线自测是**全绿**的 —— 因为它只断言「tools/list 返回 34 个工具」，从不真的 call 一次。
`bl_selftest.py` ④ 现在补了「真派发 + 对照组」的端到端判据，但它只盯那两个工具；
本脚本把同一条不变量**推广到全部工具**：任何一个"列得出却调不动"的工具都会被点名。

三个比对源（都在 `tools/bl_mcp.py` 里）：
  1. **声明** ← `TOOLS = [...]` 里 `"name": "bl_x"`（决定 tools/list 暴露什么）
  2. **派发** ← `call_tool()` 里的 `if name == "bl_x":`（决定调不调得动）
  3. **分组** ← `TOOL_GROUPS`（`BLBRIDGE_TOOLSET=core+config` 时只暴露这两组）

判据（3 条，每条都能指出"哪种输入会红"）：
  C1 声明 ⇒ 派发：TOOLS 里每个名字都必须在 call_tool 里有分支。
                   缺了就是"看得见、调不动"（本脚本的立项理由）。
  C2 派发 ⇒ 声明：call_tool 里的每个分支名都必须在 TOOLS 里。
                   反过来那一半：一个没声明的分支永远不会被调到，是死代码（也是改名后
                   "旧名字还留着"的典型残留）。
  C3 声明 ⇒ 分组：TOOLS 里每个名字都必须落在 `TOOL_GROUPS` 的某一组里。
                   漏登记的工具，在设了 `BLBRIDGE_TOOLSET` 的环境里会**凭空消失**且极难察觉。

**关于 `--selftest`**：一个恒返回 OK 的校验器、一个正则写错把所有名字都跳过来的校验器，
都会得到"通过"。所以 `--selftest` 会在内存里注入 3 类故障，逐条断言被抓到；
同时断言**未注入时 0 报错**（对照组）。

用法：
    python tools/bl_check_dispatch.py                 # 校验
    python tools/bl_check_dispatch.py --selftest      # 注入故障自测（3 类，必须全抓到）
    python tools/bl_check_dispatch.py --json-out r.json

退出码：0 = 通过；1 = 有 FAIL；2 = 环境问题（文件缺失 / 抽取为空 —— 抽取为空**不算通过**）。
"""
import argparse
import ast
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MCP_PY = os.path.join(HERE, "bl_mcp.py")

try:
    import bl_common
except Exception:  # noqa: BLE001 - 允许从别处 import（如被 bl_selftest 引用）
    bl_common = None

DISPATCH_RE = re.compile(r'if name == "(bl_[a-z0-9_]+)"')


def read_source(path=None):
    with io.open(path or MCP_PY, "r", encoding="utf-8") as fh:
        return fh.read()


def declared_names(src):
    """TOOLS 表里声明的工具名（= tools/list 会暴露的）。"""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "TOOLS" for t in node.targets):
            return [d["name"] for d in ast.literal_eval(node.value)]
    return []


def grouped_names(src):
    """TOOL_GROUPS 里登记过的工具名集合。"""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "TOOL_GROUPS" for t in node.targets):
            groups = ast.literal_eval(node.value)
            out = set()
            for members in groups.values():
                out.update(members)
            return out
    return set()


def dispatched_names(src):
    """call_tool() 里真的有分支的工具名（= 调得动的）。

    只取 `call_tool` 函数体：别处若有同形比较（如 `if name == ...`）会被误算进来，
    那会让 C1 永远绿。
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "call_tool":
            try:
                body = ast.get_source_segment(src, node) or ""
            except Exception:  # noqa: BLE001
                body = ""
            return set(DISPATCH_RE.findall(body))
    return set()


def audit(src=None, path=None):
    """返回问题清单（空列表 = 通过）。"""
    src = read_source(path) if src is None else src
    problems = []
    declared = declared_names(src)
    dispatched = dispatched_names(src)
    grouped = grouped_names(src)

    if not declared:
        problems.append("ENV: 从 bl_mcp.py 里没抽到 TOOLS 声明（抽取为空不算通过）")
    if not dispatched:
        problems.append("ENV: 从 bl_mcp.py 里没抽到 call_tool 的派发分支（抽取为空不算通过）")
    if problems:
        return problems

    for n in declared:
        if n not in dispatched:
            problems.append("C1 声明了却没有派发分支（tools/list 看得见、一调就 unknown tool）: %s" % n)
    for n in sorted(dispatched):
        if n not in declared:
            problems.append("C2 有派发分支却没声明（死分支；多半是改名后的旧名残留）: %s" % n)
    for n in declared:
        if n not in grouped:
            problems.append("C3 不在任何 TOOL_GROUPS 组里（设了 BLBRIDGE_TOOLSET 时会凭空消失）: %s" % n)
    return problems


def selftest():
    """注入 3 类故障，逐条断言被抓到；并断言未注入时 0 报错（对照组）。"""
    src = read_source()
    base = audit(src)
    if base:
        print("[FAIL] 对照组：真实源码应当 0 报错，实际 %d 条 -> %s" % (len(base), base))
        return 1
    print("[OK] 对照组：真实源码 0 报错")

    ok = True

    # 故障 1：删掉一条派发分支（= 立项时踩的那个坑）
    bad1 = src.replace('if name == "bl_status":', 'if name == "bl_status_removed":', 1)
    if bad1 == src:
        print("[FAIL] 故障注入失败：找不到 bl_status 的派发分支（脚本自身失效）")
        ok = False
    else:
        p = audit(bad1)
        hit = [x for x in p if x.startswith("C1") and "bl_status" in x]
        print(("[OK] 故障1（删派发分支）被抓到: %s" % hit[0]) if hit
              else ("[FAIL] 故障1（删派发分支）没被抓到 -> %s" % p))
        ok = ok and bool(hit)

    # 故障 2：加一条没声明的派发分支
    bad2 = src.replace('    if name == "bl_status":',
                       '    if name == "bl_orphan_tool":\n        return {}\n    if name == "bl_status":', 1)
    if bad2 == src:
        print("[FAIL] 故障注入失败：无法插入孤儿分支（脚本自身失效）")
        ok = False
    else:
        p = audit(bad2)
        hit = [x for x in p if x.startswith("C2") and "bl_orphan_tool" in x]
        print(("[OK] 故障2（孤儿派发分支）被抓到: %s" % hit[0]) if hit
              else ("[FAIL] 故障2（孤儿派发分支）没被抓到 -> %s" % p))
        ok = ok and bool(hit)

    # 故障 3：把一个工具从 TOOL_GROUPS 里摘掉
    bad3 = src.replace('"bl_get_screen", "bl_get_viewmodel_property",', '', 1)
    if bad3 == src:
        print("[FAIL] 故障注入失败：找不到 bl_get_screen 的分组条目（脚本自身失效）")
        ok = False
    else:
        p = audit(bad3)
        hit = [x for x in p if x.startswith("C3") and "bl_get_screen" in x]
        print(("[OK] 故障3（漏分组）被抓到: %s" % hit[0]) if hit
              else ("[FAIL] 故障3（漏分组）没被抓到 -> %s" % p))
        ok = ok and bool(hit)

    print("结果: %s" % ("全部通过" if ok else "有故障没被抓到"))
    return 0 if ok else 1


def main(argv=None):
    if bl_common is not None:
        bl_common.safe_streams()
    ap = argparse.ArgumentParser(description="MCP 工具 声明/派发/分组 三方一致性校验")
    ap.add_argument("--selftest", action="store_true", help="注入 3 类故障自测")
    ap.add_argument("--json-out", help="把结果写成 JSON")
    args = ap.parse_args(argv)

    if not os.path.isfile(MCP_PY):
        print("ENV: 找不到 %s" % MCP_PY)
        return 2

    if args.selftest:
        return selftest()

    problems = audit()
    declared = declared_names(read_source())
    dispatched = dispatched_names(read_source())
    print("声明(TOOLS) %d 个 / 派发(call_tool) %d 个 / 分组(TOOL_GROUPS) %d 个"
          % (len(declared), len(dispatched), len(grouped_names(read_source()))))
    if problems:
        for p in problems:
            print("[FAIL] %s" % p)
        print("结果: 失败 %d 项" % len(problems))
    else:
        print("[OK] 每个工具都：声明了 ⇒ 派发得到 ⇒ 分了组")
        print("结果: 全部通过")

    if args.json_out:
        with io.open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"declared": declared, "dispatched": sorted(dispatched),
                                 "problems": problems}, ensure_ascii=False, indent=1))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
