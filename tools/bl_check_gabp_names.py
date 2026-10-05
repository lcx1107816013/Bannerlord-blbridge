#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GABP 命名对齐表（`tools/gabp_names.json`）与源码的三方一致性校验。

为什么要有这个脚本：`gabp_names.json` 是给"将来把能力暴露成 GABP 工具"用的命名约定。
命名表最容易的失效方式不是写错，而是**悄悄过期** —— 我们加了第 16 个控制通道 method、
或第 33 个 MCP 工具，表里没跟上，谁也不会发现。所以这条不变量必须**可执行**
（与 `check_repo_encoding.py` / `bl_check_clock_reset.py` 同一理由）。

三个比对源（谁是谁的唯一真相源）：
  1. **控制通道 method** ← `src/CommandPump.cs` 的 `Dispatch` 里的 `method == "..."` 分支（源码即真相）
  2. **MCP 工具**        ← `tools/bl_mcp.py` 的 `TOOLS = [...]` 表（表即真相）
  3. **GABP 命名表**     ← `tools/gabp_names.json`（本脚本校验它的**覆盖度**与**内部合法性**）

判据（7 条，逐条都能指出"哪种输入会红"）：
  C1 覆盖（双向）：C# 里每个 method 在表里都有一条；表里每条 method 都真实存在于 C#。
                   工具侧同理（py 表 ↔ json.tools）。**缺失与多余都要报** ——
                   只查一个方向的话，"表里每条都有出处"会掩盖"源码里多了个没人命名的 method"。
  C2 同表内唯一：methods 内部、tools 内部各自的 gabp 名不得重复。
                   （跨表重复是**允许**且预期的：同一个能力在 MCP 侧和 GABP 侧就是一个名字。）
  C3 形状：`^[a-z][a-z0-9_]*/[a-z][a-z0-9_]*$`（全小写、两段、下划线分段）。
  C4 前缀是已声明的 category。
  C5 前缀不在 reserved_prefixes 里（`session` / `tools` / `events` / `resources` / `attention` …
                   那些是 GABP 协议自留的，用了会撞）。
  C6 `kind` ∈ {read, write}；`needs` ∈ NEEDS_ALLOWED（新出现的前置条件必须显式登记）。
  C7 工具侧每条要么有 `note` 要么 `gabp` 段名可自解释 —— 不做主观判定，
                   改为：**每个 gabp 名必须是"表里唯一的"**（同 C2）+ 报告 `--markdown` 里两表交叉链接清单。

**关于 `--selftest`（重要）**：本脚本不能只"跑一遍说通过"。
一个恒返回 OK 的校验器、一个正则写错把所有条目都跳过的校验器，都会得到"通过"这个结果。
所以 `--selftest` 会在内存里**注入 7 类故障**，逐条断言被抓到；并同时断言**未注入时 0 报错**（对照组）。

用法：
    python tools/bl_check_gabp_names.py                 # 校验（退出码见下）
    python tools/bl_check_gabp_names.py --selftest      # 注入故障自测（7 类，必须全抓到）
    python tools/bl_check_gabp_names.py --markdown      # 输出 docs/gabp-naming.md 用的两张表
    python tools/bl_check_gabp_names.py --json-out r.json

退出码：0 = 通过；1 = 有 FAIL；2 = 环境问题（文件缺失 / 抽取不到内容 —— 抽取为空**不算通过**）。
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

try:
    import bl_common
except Exception:  # 允许从别处 import（如被 bl_selftest 引用）
    bl_common = None

NAMES_JSON = os.path.join(HERE, "gabp_names.json")
CSHARP = os.path.join(REPO, "src", "CommandPump.cs")
MCP_PY = os.path.join(HERE, "bl_mcp.py")

# C6：允许的前置条件。新出现一个（比如 campaign / settlement）必须在这里登记，
# 目的是让"命名时想清楚它在哪个状态下可用"变成一次显式动作，而不是随手写个字符串。
NEEDS_ALLOWED = ("any", "mission", "main_menu", "CustomBattleState", "campaign")
KINDS_ALLOWED = ("read", "write")

SHAPE_RE = re.compile(r"^[a-z][a-z0-9_]*/[a-z][a-z0-9_]*$")
METHOD_RE = re.compile(r'method\s*==\s*"([A-Za-z0-9_]+)"')
# TOOLS 表里的工具名。刻意只扫 `TOOLS = [` 到派发段之前 —— 派发段里有 `if name == "bl_x"`，
# 扫全文会把同一个名字数两遍（那就永远查不出"表里少一个工具"）。
TOOL_DECL_RE = re.compile(r'"name":\s*"(bl_[a-z0-9_]+)"')
TOOLS_TABLE_START_RE = re.compile(r"^TOOLS\s*=\s*\[")
DISPATCH_START_RE = re.compile(r'^\s{4}if name == "bl_')


# ───────────────────────────── 抽取 ─────────────────────────────

def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def methods_from_csharp(text):
    """控制通道 method 名（去重，保持首次出现顺序）。"""
    out = []
    for m in METHOD_RE.finditer(text):
        n = m.group(1)
        if n not in out:
            out.append(n)
    return out


def tools_from_mcp_table(text):
    """MCP 工具名，只取 TOOLS 表那一段。找不到表头 / 找不到派发段 ⇒ 抛错（不静默返回空）。"""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if TOOLS_TABLE_START_RE.match(ln):
            start = i
            break
    if start is None:
        raise ValueError("bl_mcp.py 里找不到 `TOOLS = [` —— 抽取规则已过期，别把它当成'通过'")
    end = None
    for j in range(start + 1, len(lines)):
        if DISPATCH_START_RE.match(lines[j]):
            end = j
            break
    if end is None:
        raise ValueError("bl_mcp.py 里找不到派发段 `    if name == \"bl_...\"` —— 抽取规则已过期")
    out = []
    for ln in lines[start:end]:
        for m in TOOL_DECL_RE.finditer(ln):
            n = m.group(1)
            if n not in out:
                out.append(n)
    if not out:
        raise ValueError("TOOLS 表段里一个工具名都没抽到 —— 抽取规则已过期")
    return out


# ───────────────────────────── 判据 ─────────────────────────────

def check(data, methods_in_src, tools_in_py):
    """返回 (issues, info)；issues 为空 ⇒ 通过。data 是 gabp_names.json 的 dict。"""
    issues = []
    info = {}

    md = data.get("methods") or {}
    td = data.get("tools") or {}
    cats = data.get("categories") or {}
    reserved = set(data.get("reserved_prefixes") or [])

    # C1 覆盖（双向）
    for m in methods_in_src:
        if m not in md:
            issues.append("C1 表里缺 method：`%s`（C# Dispatch 有它，gabp_names.json 没有）" % m)
    for m in md:
        if m not in methods_in_src:
            issues.append("C1 表里多出 method：`%s`（gabp_names.json 有它，C# Dispatch 没有）" % m)
    for t in tools_in_py:
        if t not in td:
            issues.append("C1 表里缺 tool：`%s`（bl_mcp.py 的 TOOLS 有它，gabp_names.json 没有）" % t)
    for t in td:
        if t not in tools_in_py:
            issues.append("C1 表里多出 tool：`%s`（gabp_names.json 有它，bl_mcp.py 的 TOOLS 没有）" % t)

    # C2 同表内唯一 + C3/C4/C5/C6
    def walk(entries, label, extra_rules):
        seen = {}
        for key, ent in entries.items():
            if not isinstance(ent, dict):
                issues.append("C3 %s `%s` 的条目不是对象" % (label, key))
                continue
            name = ent.get("gabp")
            if not name or not isinstance(name, str):
                issues.append("C3 %s `%s` 没有 gabp 名" % (label, key))
                continue
            if not SHAPE_RE.match(name):
                issues.append("C3 %s `%s` 的 gabp 名形状非法：`%s`（要 `<category>/<snake_case>`，全小写）"
                              % (label, key, name))
                continue
            cat = name.split("/", 1)[0]
            if cat in reserved:
                issues.append("C5 %s `%s` 用了 GABP 协议自留前缀：`%s`" % (label, key, cat))
            if cat not in cats:
                issues.append("C4 %s `%s` 的 category `%s` 未在 categories 里声明" % (label, key, cat))
            if name in seen:
                issues.append("C2 %s 内部 gabp 名重复：`%s`（%s 与 %s 撞了）"
                              % (label, name, seen[name], key))
            else:
                seen[name] = key
            extra_rules(key, ent, name)
        return seen

    def method_rules(key, ent, name):
        k = ent.get("kind")
        if k not in KINDS_ALLOWED:
            issues.append("C6 method `%s` 的 kind 非法：%r（只允许 %s）" % (key, k, "/".join(KINDS_ALLOWED)))
        n = ent.get("needs")
        if n not in NEEDS_ALLOWED:
            issues.append("C6 method `%s` 的 needs 非法：%r（只允许 %s；新前置条件请在 NEEDS_ALLOWED 登记）"
                          % (key, n, "/".join(NEEDS_ALLOWED)))

    m_names = walk(md, "methods", method_rules)
    t_names = walk(td, "tools", lambda k, e, n: None)

    # 交叉链接（**信息段，不判失败**）：同一个能力在 MCP 侧与 GABP 侧共用一个名字，是预期行为。
    # ⚠️ `walk` 返回的是 `{gabp 名: 条目键}`，所以取交集要用**键**（gabp 名），不是 `.values()`（条目键）。
    # 首版写成 `set(m_names.values()) & set(t_names.values())` ⇒ 拿 `method 名集合` 与 `bl_* 工具名集合`
    # 求交，恒为空 ⇒ 报告永远打印"跨表同名 0 个"。**静默失真**：不报错、只是信息段永远错。
    # 这类"两个地方各算一遍同一件事"的写法已被本文件的 selftest 加对照组钉住（见 selftest 里的 CROSS 检查）。
    info["cross_linked"] = sorted(set(m_names) & set(t_names))
    info["counts"] = {
        "methods_in_src": len(methods_in_src),
        "tools_in_py": len(tools_in_py),
        "methods_in_table": len(md),
        "tools_in_table": len(td),
        "categories": len(cats),
    }
    return issues, info


# ───────────────────────────── 自测 ─────────────────────────────

def selftest(data, methods_in_src, tools_in_py):
    """注入 7 类故障，断言每一类都被抓到；同时断言未注入时 0 报错（对照组）。"""
    import copy
    cases = []

    def mutate(fn):
        d = copy.deepcopy(data)
        fn(d)
        return d

    def del_method(d):
        d["methods"].pop(sorted(d["methods"])[0])

    def add_method(d):
        d["methods"]["__ghost_method__"] = {"gabp": "core/ghost", "kind": "read", "needs": "any"}

    def add_tool(d):
        d["tools"]["bl_ghost_tool"] = {"gabp": "bridge/ghost"}

    def dup_method_name(d):
        ks = sorted(d["methods"])
        d["methods"][ks[1]]["gabp"] = d["methods"][ks[0]]["gabp"]

    def bad_category(d):
        ks = sorted(d["methods"])
        d["methods"][ks[0]]["gabp"] = "nowhere/ping"

    def reserved(d):
        ks = sorted(d["methods"])
        d["methods"][ks[0]]["gabp"] = "tools/ping"

    def bad_shape(d):
        ks = sorted(d["methods"])
        d["methods"][ks[0]]["gabp"] = "core/Ping"

    def bad_needs(d):
        ks = sorted(d["methods"])
        d["methods"][ks[0]]["needs"] = "whenever"

    cases = [
        ("C1 缺 method", mutate(del_method), "C1 表里缺 method"),
        ("C1 多 method", mutate(add_method), "C1 表里多出 method"),
        ("C1 多 tool", mutate(add_tool), "C1 表里多出 tool"),
        ("C2 名字重复", mutate(dup_method_name), "C2"),
        ("C4 category 未声明", mutate(bad_category), "C4"),
        ("C5 撞协议自留前缀", mutate(reserved), "C5"),
        ("C3 形状非法", mutate(bad_shape), "C3"),
        ("C6 needs 非法", mutate(bad_needs), "C6"),
    ]

    # 对照组：未注入时必须是 0 报错
    ok_issues, ok_info = check(data, methods_in_src, tools_in_py)
    print("[%s] 对照组：未注入故障时 0 报错（实测 %d 条）"
          % ("OK" if not ok_issues else "FAIL", len(ok_issues)))
    if ok_issues:
        for i in ok_issues:
            print("        %s" % i)
    bad = 1 if ok_issues else 0

    # 对照组 2（专治信息段的静默失真）：跨表同名必须**非空** ——
    # method 侧的 battle/start / ui/open / core/skip_video … 与 tool 侧本就同名。
    # 首版用错集合求交，这里恒为 0 却没有任何报错；这条断言就是那种缺陷的对照组。
    n_cross = len(ok_info.get("cross_linked") or [])
    cross_ok = n_cross > 0
    if not cross_ok:
        bad += 1
    print("[%s] 对照组 2：跨表同名非空（实测 %d 个，期望 >0；为 0 说明取交集用错了集合）"
          % ("OK" if cross_ok else "FAIL", n_cross))

    caught_n = 0
    for label, d, expect in cases:
        issues, _ = check(d, methods_in_src, tools_in_py)
        caught = any(expect in i for i in issues)
        if caught:
            caught_n += 1
        else:
            bad += 1
        print("[%s] 注入「%s」→ 期望含 `%s`：实测 %d 条（%s）"
              % ("OK" if caught else "FAIL", label, expect, len(issues),
                 "抓到" if caught else "**漏报**"))
    print("")
    print("自测：%d 类故障，抓到 %d 类" % (len(cases), caught_n))
    return 0 if bad == 0 else 1


# ───────────────────────────── markdown ─────────────────────────────

def markdown(data, methods_in_src, tools_in_py):
    md = data.get("methods") or {}
    td = data.get("tools") or {}
    cats = data.get("categories") or {}
    # 交叉链接只从 check() 取一次 —— 不在两个地方各算一遍（首版就是这么错的，见 check() 里的注）
    _issues, _info = check(data, methods_in_src, tools_in_py)
    L = []
    L.append("### 控制通道 method → GABP 名（共 %d 条）\n" % len(md))
    L.append("| GABP 名 | method | 读/写 | 前置 | 说明 |")
    L.append("|---|---|---|---|---|")
    for m in sorted(md, key=lambda k: md[k].get("gabp", "")):
        e = md[m]
        L.append("| `%s` | `%s` | %s | %s | %s |"
                 % (e.get("gabp", "?"), m, e.get("kind", "?"), e.get("needs", "?"),
                    (e.get("note") or "").replace("|", "\\|")))
    L.append("")
    L.append("### MCP 工具 → GABP 名（共 %d 条）\n" % len(td))
    L.append("| GABP 名 | MCP 工具 | 说明 |")
    L.append("|---|---|---|")
    for t in sorted(td, key=lambda k: td[k].get("gabp", "")):
        e = td[t]
        L.append("| `%s` | `%s` | %s |"
                 % (e.get("gabp", "?"), t, (e.get("note") or "").replace("|", "\\|")))
    L.append("")
    L.append("### category 词表（共 %d 个）\n" % len(cats))
    L.append("| category | 含义 |")
    L.append("|---|---|")
    for c in sorted(cats):
        L.append("| `%s` | %s |" % (c, cats[c].replace("|", "\\|")))
    L.append("")
    linked = _info["cross_linked"]
    L.append("### 跨表同名（信息项，预期行为）\n")
    L.append("同一个能力在 MCP 侧与 GABP 侧共用一个名字，共 %d 个：" % len(linked))
    L.append("")
    L.append("`" + "` `".join(linked) + "`")
    L.append("")
    return "\n".join(L)


# ───────────────────────────── main ─────────────────────────────

def main():
    if bl_common is not None and hasattr(bl_common, "safe_streams"):
        bl_common.safe_streams()

    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="注入 7 类故障自测")
    ap.add_argument("--markdown", action="store_true", help="输出文档用的 markdown 表")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    missing = [p for p in (NAMES_JSON, CSHARP, MCP_PY) if not os.path.exists(p)]
    if missing:
        for p in missing:
            print("[ENV ] 找不到 %s" % p)
        return 2

    try:
        data = json.loads(read_text(NAMES_JSON))
        methods_in_src = methods_from_csharp(read_text(CSHARP))
        tools_in_py = tools_from_mcp_table(read_text(MCP_PY))
    except Exception as exc:
        print("[ENV ] 抽取失败：%s" % exc)
        return 2

    if not methods_in_src or not tools_in_py:
        print("[ENV ] 抽取结果为空（methods=%d tools=%d）—— 空结果不算通过"
              % (len(methods_in_src), len(tools_in_py)))
        return 2

    if args.selftest:
        return selftest(data, methods_in_src, tools_in_py)

    if args.markdown:
        sys.stdout.write(markdown(data, methods_in_src, tools_in_py) + "\n")
        return 0

    issues, info = check(data, methods_in_src, tools_in_py)
    print("源与表：C# method %d 个 / 表 %d 条；py tool %d 个 / 表 %d 条；category %d 个"
          % (info["counts"]["methods_in_src"], info["counts"]["methods_in_table"],
             info["counts"]["tools_in_py"], info["counts"]["tools_in_table"],
             info["counts"]["categories"]))
    print("跨表同名 %d 个（信息项）：%s"
          % (len(info["cross_linked"]), ", ".join(info["cross_linked"]) or "无"))
    print("")
    for i in issues:
        print("[FAIL] %s" % i)
    if issues:
        print("\n%d 条 FAIL" % len(issues))
    else:
        print("[OK] 7 条判据全过（C1 覆盖双向 / C2 唯一 / C3 形状 / C4 category / C5 自留前缀 / C6 kind+needs）")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"issues": issues, "info": info}, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
