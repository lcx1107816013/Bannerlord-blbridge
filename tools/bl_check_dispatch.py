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

判据（4 条 + 2 组对照，每条都能指出"哪种输入会红"）：
  C1 声明 ⇒ 派发：TOOLS 里每个名字都必须在 call_tool 里有分支。
                   缺了就是"看得见、调不动"（本脚本的立项理由）。
  C2 派发 ⇒ 声明：call_tool 里的每个分支名都必须在 TOOLS 里。
                   反过来那一半：一个没声明的分支永远不会被调到，是死代码（也是改名后
                   "旧名字还留着"的典型残留）。
  C3 声明 ⇒ 分组：TOOLS 里每个名字都必须落在 `TOOL_GROUPS` 的某一组里。
                   漏登记的工具，在设了 `BLBRIDGE_TOOLSET` 的环境里会**凭空消失**且极难察觉。
  C5 manifest 的 `_meta.blbridge.toolCount` == TOOLS 声明数（2026-10-07 新增）。
                   `build.ps1` **只同步 version、不同步 toolCount** ⇒ 它是手工维护的，
                   实测已漂移过（加第 58 个工具时仍写 57）。

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
    problems.extend(check_cli_entries(declared, src))
    problems.extend(check_manifest_toolcount(len(declared)))
    return problems


# ── C5：manifest.json 的 toolCount 必须等于真实工具数（2026-10-07 新增）──────
#
# ## 为什么加这条
#
# `module/mcp/manifest.json` 的 `_meta.blbridge.toolCount` 是**手工维护**的，
# 而 `build.ps1` **只同步 version、不同步 toolCount**（它自己写明"手维护必漂移"）。
# 实测证据（2026-10-07）：本仓加第 58 个工具（`bl_lexicon`）时，
# `toolCount` 仍停在 **57**，而 `bl_mcp.py` 的 `TOOLS` 已是 58 ——
# **没有任何既有闸门会发现**（C1/C2/C3 只比三个源，都不看 manifest）。
#
# ⇒ 危害是"对外声明与实现不符"：AI 读 manifest 得知 57 个工具，
#   于是不会去调第 58 个（`toolCount` 正是给"读完介绍即接入"的客户端看的）。
#
# ## 判据
#
# `toolCount == len(TOOLS 声明数)`。**哪种输入会红**：加/删工具却没同步 manifest；
# 或把 toolCount 写成别的数（打错字）。**抽取失败也算红**（ENV），不静默放行。
MANIFEST_PY = os.path.join(HERE, os.pardir, "module", "mcp", "manifest.json")


def read_manifest_toolcount(path=None):
    """读 manifest 的 _meta.blbridge.toolCount；取不到返回 (None, 原因)。"""
    p = os.path.abspath(path or MANIFEST_PY)
    if not os.path.isfile(p):
        return None, "manifest 不存在：%s" % p
    try:
        with io.open(p, "r", encoding="utf-8-sig") as fh:
            d = json.load(fh)
    except Exception as exc:                              # noqa: BLE001
        return None, "manifest 解析失败：%s" % exc
    try:
        v = d["_meta"]["blbridge"]["toolCount"]
    except Exception:                                     # noqa: BLE001
        return None, "manifest 里没有 _meta.blbridge.toolCount（键路径变了？）"
    return v, None


def check_manifest_toolcount(n_declared, path=None):
    """C5：manifest 的 toolCount 必须等于真实声明数。"""
    problems = []
    val, why = read_manifest_toolcount(path)
    if why:
        problems.append("ENV: C5 读不到 manifest 的 toolCount（%s）—— 抽取失败不算通过" % why)
        return problems
    if not isinstance(val, int):
        problems.append("C5 toolCount 不是整数：%r" % (val,))
        return problems
    if val != n_declared:
        problems.append(
            "C5 manifest 的 toolCount=%d 与 TOOLS 声明数=%d 不符"
            "（对外声明与实现不一致；build.ps1 只同步 version、不同步这项）"
            % (val, n_declared))
    return problems


def c5_selftest(declared):
    """C5 的对照组：注入「toolCount 写错」必须被抓到。"""
    import tempfile
    d = tempfile.mkdtemp(prefix="bl_c5_")
    try:
        cases = [
            ("正确值 %d" % len(declared), len(declared), False),
            ("写错 +1", len(declared) + 1, True),
            ("写错 -1", len(declared) - 1, True),
            ("写成字符串", str(len(declared)), True),
        ]
        for label, val, should_fail in cases:
            p = os.path.join(d, "m.json")
            obj = {"_meta": {"blbridge": {"toolCount": val}}}
            with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(obj))
            probs = check_manifest_toolcount(len(declared), path=p)
            got = bool(probs)
            ok = (got == should_fail)
            print("   [%s] C5 对照组 %-14s => %s"
                  % ("OK" if ok else "FAIL", label,
                     ("报: " + probs[0][:70]) if probs else "0 报错"))
            if not ok:
                return False
        # 缺键也要被抓到
        p = os.path.join(d, "bad.json")
        with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"_meta": {}}))
        probs = check_manifest_toolcount(len(declared), path=p)
        ok = bool(probs)
        print("   [%s] C5 对照组 %-14s => %s"
              % ("OK" if ok else "FAIL", "缺键", "报: " + probs[0][:70] if probs else "0 报错"))
        return ok
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


# ── C4：宿主侧工具模块必须有 CLI 入口（2026-10-06 新增）──────────────────
#
# ## 为什么加这条
#
# v0.8.47 新增的 3 个工具（`bl_json_health` / `bl_ipc_replay` / `bl_exception_detail`）
# 并入时**只写了库函数**（`scan()` / `build_report()`），**没有 `main()`、
# 没有 `__main__` 块、没有 `import sys`、没有 `sys.path` 处理**。
# ⇒ 直接 `python tools/bl_json_health.py` **静默 exit=0，什么都不做**。
#
# ★ 危害不只是「CLI 不能用」——它让**性能测量得出虚假结论**：
#   实测把 ~30ms（解释器启动）当成「全量解析 12991 个文件的耗时」，
#   而真实是 **~705ms**，差 **23 倍**。两个并行测量者都独立复现了这个坑。
#
# ★ 为什么既有自测没抓到：三个工具的自测都**直接 `import` 调函数**，
#   **完全绕过 CLI 路径** ⇒ 这个缺陷在自测里不可见。
#   ⇒ 所以加这条**静态**闸门（扫源码文本），而不是再加一条运行时断言。
#
# ## 判据
#
# ⚠️ **2026-10-06 第一次写这判据时是假阳性**，必须记下来：
#   初版只查文本里有没有 `sys.path` 字样，结果报了 `bl_crash.py`。
#   但实测 `python tools/bl_crash.py` **exit=0 正常** —— 因为它的 `import bl_common`
#   写在 **`main()` 函数体内**（不是模块级），且实跑时 cwd 恰为 `tools/`，
#   `import` 也能成功。⇒ **文本匹配抓不住"import 的层级"与"cwd 恰好可见"**。
#
# ⇒ 改成**以实测为准**：
#     1) **模块级** import 了同目录兄弟模块 ⇒ **必须**有 `sys.path` 处理
#        （因为模块级 import 在解释器启动后立刻执行，此时 cwd 不可依赖）；
#     2) 同时要求有 `def main(` 与 `__main__` 块（这两个是纯文本可判的，
#        且没有假阳性风险）。
#   不再要求"函数体内的 import"也配 `sys.path` —— 那会误报 `bl_crash.py`。

BACKEND_IMPORT_RE = re.compile(r"^\s*import\s+(bl_[a-z_]+)\s+as\s+", re.M)
# 模块级 import（行首无缩进）
MODULE_LEVEL_SIBLING_RE = re.compile(r"^import\s+(bl_[a-z_]+)", re.M)
CLI_EXEMPT = {"bl_rts"}          # 纯库，无 CLI 用途（历史如此）


def backend_modules(src=None):
    """从 bl_mcp.py 抽出被当作后端 import 的模块名（如 bl_crash / bl_json_health）。"""
    src = read_source() if src is None else src
    names = set()
    for m in BACKEND_IMPORT_RE.finditer(src):
        names.add(m.group(1))
    return names


def _cli_missing(mod, body):
    """返回该模块缺的 CLI 要素列表（共 3 项，见上文判据）。"""
    missing = []
    if re.search(r"^import\s+bl_[a-z_]+", body, re.M) and "sys.path" not in body:
        # 模块级 import 兄弟模块却没设 sys.path ⇒ CLI 直跑会 ModuleNotFoundError
        missing.append("sys.path 处理（模块级 import 了兄弟模块）")
    if not re.search(r"^def main\(", body, re.M):
        missing.append("def main()")
    if '__name__ == "__main__"' not in body and "__name__ == '__main__'" not in body:
        missing.append('__main__ 块')
    return missing


def check_cli_entries(declared, src=None):
    """C4：后端工具模块必须有 CLI 入口。返回问题清单。"""
    import os as _os
    src = read_source() if src is None else src
    problems = []
    here = _os.path.dirname(_os.path.abspath(__file__))
    checked = 0
    for mod in sorted(backend_modules(src)):
        if mod in CLI_EXEMPT:
            continue
        p = _os.path.join(here, mod + ".py")
        if not _os.path.isfile(p):
            problems.append("C4 后端模块文件不存在（%s）: %s.py" % (p, mod))
            continue
        # ⚠️ 这里**故意读原始文本**而不是 import —— 闸门要能在模块本身语法都坏时也报出来
        try:
            with io.open(p, "r", encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError as exc:
            problems.append("C4 读不到后端模块 %s.py: %s" % (mod, exc))
            continue
        checked += 1
        missing = _cli_missing(mod, body)
        if missing:
            problems.append(
                "C4 %s.py 缺 CLI 入口要素 %s —— 直接跑会**静默 exit=0 什么都不做**"
                "（危害：会被当成「跑得很快」=> 性能结论虚假）" % (mod, missing))
    if checked == 0:
        problems.append("ENV: C4 没检查到任何后端模块（抽取为空不算通过）")
    return problems


def c4_selftest():
    """C4 的对照组：注入「去掉 `__main__` 块」必须被抓到（证明判据不是恒真）。

    不做真实文件改动 —— 用**内存里的假文本**喂同一个判据函数，
    避免污染工作区（本项目纪律：验证不得污染被测对象）。
    """
    print()
    print("-- C4 对照组（CLI 入口） --")
    src = read_source()
    mods = backend_modules(src)
    print("   后端模块 %d 个: %s" % (len(mods), ", ".join(sorted(mods)) or "(空)"))
    if not mods:
        print("   [FAIL] 抽不到后端模块（判据失效）")
        return False

    # ① 真实源码必须零报错（对照组）
    p = audit(src)
    c4 = [x for x in p if x.startswith("C4")]
    if c4:
        print("   [FAIL] 真实源码 C4 报错 %d 条: %s" % (len(c4), c4[:3]))
        return False
    print("   [OK] 真实源码 C4 零报错（%d 个后端模块都合格）" % len(mods))

    # ② 注入故障 A：去掉 `__main__` 块
    probe = sorted(mods)[0]
    import os as _os
    here = _os.path.dirname(_os.path.abspath(__file__))
    with io.open(_os.path.join(here, probe + ".py"), "r", encoding="utf-8",
                 errors="replace") as fh:
        real = fh.read()
    broken = real.replace('if __name__ == "__main__":', '# REMOVED', 1)
    if broken == real:
        print("   [FAIL] 注入 A 失败：%s.py 找不到 `__main__` 块" % probe)
        return False
    miss = _cli_missing(probe, broken)
    okA = any("__main__" in m for m in miss)
    print("   [%s] 注入 A（%s.py 去掉 __main__）=> 判据报 %s"
          % ("OK" if okA else "FAIL", probe, miss))

    # ③ 注入故障 B：模块级 import 兄弟模块 + 去掉 sys.path
    broken2 = real.replace("sys.path.insert(0, HERE)", "# removed", 1)
    miss2 = _cli_missing(probe, broken2)
    has_modlevel = bool(re.search(r"^import\s+bl_[a-z_]+", broken2, re.M))
    okB = (not has_modlevel) or any("sys.path" in m for m in miss2)
    print("   [%s] 注入 B（%s.py 去掉 sys.path）=> 模块级兄弟 import=%s，判据报 %s"
          % ("OK" if okB else "FAIL", probe, has_modlevel, miss2))

    return okA and okB


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

    # ── C4 对照组（CLI 入口）：2026-10-06 新增 ──
    # 缺陷现场：v0.8.47 三个新工具只写库函数、漏了 CLI 四要素
    # ⇒ 直接跑静默 exit=0；而自测「直接 import 调函数」⇒ **绕过 CLI** ⇒ 抓不到。
    # 所以这条闸门是**静态**的（扫源码文本），并自带注入对照证明判据不是恒真。
    ok = c4_selftest() and ok

    # ── C5 对照组（manifest.toolCount）：2026-10-07 新增 ──
    # 缺陷现场：加第 58 个工具（bl_lexicon）时 manifest 的 toolCount 仍停在 57，
    # 而 C1/C2/C3 只比 bl_mcp.py 内部三个源、**都不看 manifest** ⇒ 无闸门可发现。
    print("-- C5 对照组（manifest toolCount） --")
    ok = c5_selftest(declared_names(src)) and ok

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
