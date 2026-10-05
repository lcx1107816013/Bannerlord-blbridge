#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""仓库编码体检：全仓库一律 **UTF-8 无 BOM + LF**（见 AGENTS.md 的「编码规则」）。

为什么要有这个脚本：编码规则只写在文档里，迟早会被改回去（2026-09-25 就走过两条弯路：
"只改 errors 不改 encoding" 的 safe_streams、"把中文译成英文来绕开乱码"）。规则必须**可执行**。

检查项（对 git 跟踪的 + 尚未跟踪但没被 ignore 的全部文本文件；后者是 2026-09-25 两次漏检的根因）：
  1. 必须是**有效 UTF-8**（非 UTF-8 的源文件一律报错）
  2. **不得带 BOM**（UTF-8 BOM 会让 `.gitignore` 首行、严格 `utf-8` 的 JSON 解析器等出错）
  3. 行尾必须是 **LF**（CRLF 会让 git 的 diff 隐身：工作区 CRLF / 索引 LF 时 `git status` 不报，
     直到有人改到那个文件才炸出一整份文件的差异）

用法：
    python tools/check_repo_encoding.py            # 体检；有违规 ⇒ 退出码 1
    python tools/check_repo_encoding.py --list     # 连带列出所有被检查的文件

退出码：0 = 全部合规；1 = 有违规（会在 stdout 逐条列出）；2 = 环境问题（不是 git 仓库等）。
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import bl_common  # noqa: E402

# 按扩展名判定"文本文件"（二进制文件不做编解码检查）。
TEXT_EXT = (".py", ".cs", ".md", ".json", ".xml", ".ps1", ".txt", ".cfg", ".ini", ".yml", ".yaml",
            ".rsp",     # Roslyn 响应文件（纯文本的编译参数表）—— tools/l2probe/_refs.rsp
            ".sh", ".bat", ".cmd", ".sql", ".tsv", ".csv")
TEXT_NAMES = (".gitignore", ".gitattributes", ".editorconfig")

# 明确**不是**文本、因而不做编码检查的后缀（二进制/产物）。
# 有了它，下面那条"覆盖面自检"才能区分「故意不查」与「忘了加」——
# 否则任何新后缀都会让自检变红，人会习惯性把它加进白名单，自检就废了。
BINARY_EXT = (".dll", ".exe", ".pdb", ".so", ".dylib", ".zip", ".7z", ".gz", ".tar",
              ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
              ".bin", ".dat", ".obj", ".lib", ".res", ".mdb", ".sacx", ".mmd",
              ".ivf", ".ogg", ".wav", ".mp3", ".ttf", ".otf", ".woff", ".woff2")


def is_text(path):
    base = os.path.basename(path)
    return base in TEXT_NAMES or path.endswith(TEXT_EXT)


def uncovered_tracked_files(files):
    """返回"既不在 TEXT_EXT/TEXT_NAMES、也不在 BINARY_EXT"的**已跟踪**文件。

    为什么要有这个自检（2026-10-05 实测踩到）：
      `tools/l2probe/_refs.rsp` 被 `git add` 进仓库后，体检**静默不检查它** ——
      因为 `.rsp` 当时不在 `TEXT_EXT` 里，`is_text()` 返回 False，`continue` 掉了。
      **它恰好是合规的（BOM=False / LF），所以谁也没发现门禁漏了它。**
      ⇒ 「没报错」在这里**不等于**「检查过」。加一个新后缀的文件时，体检会**安静地放过**，
      这正是本项目反复记录的失效模式：**不是坏掉，是坏掉而无人知**。

    判据刻意只覆盖**已跟踪**文件：未跟踪的临时产物（`out/` 等已被 .gitignore 排除）
    不该让体检变红。一旦有人 `git add` 了一个新后缀，本自检立刻点名。
    """
    out = []
    for f in files:
        if is_text(f):
            continue
        ext = os.path.splitext(f)[1].lower()
        if ext in BINARY_EXT:
            continue
        out.append(f)
    return out


def tracked_files():
    """git 跟踪的 **+ 尚未跟踪但没被 ignore 的** 文件列表（相对仓库根的 POSIX 路径）。

    为什么把"还没 add 的新文件"也算进来（2026-09-25 一天内两次踩到）：
      新文件刚写盘时最容易违反编码规则（编辑器/生成工具默认 CRLF），而它还没进 git
      ⇒ 只查 `git ls-files` 会**直接漏检**，直到 `git add` 之后体检才报出来。
      这两次都是 `bl_cmd.py buildcheck` 的 `stale_source` 先抓到的 —— 体检自己该早一步。
    """
    p = subprocess.run(["git", "-C", REPO, "ls-files"], capture_output=True)
    if p.returncode != 0:
        print("错误：这不是一个 git 仓库（git ls-files 失败）", file=sys.stderr)
        return None
    out = p.stdout.decode("utf-8", "replace")
    files = [f for f in out.split("\n") if f.strip()]
    seen = set(files)
    q = subprocess.run(["git", "-C", REPO, "ls-files", "--others", "--exclude-standard"],
                       capture_output=True)
    if q.returncode == 0:
        for f in q.stdout.decode("utf-8", "replace").split("\n"):
            f = f.strip()
            if f and f not in seen:
                files.append(f)
                seen.add(f)
    return files


def main(argv):
    bl_common.safe_streams()
    ap = argparse.ArgumentParser(description="仓库编码体检（UTF-8 无 BOM + LF）")
    ap.add_argument("--list", action="store_true", help="列出所有被检查的文本文件")
    args = ap.parse_args(argv)

    files = tracked_files()
    if files is None:
        return 2

    # ── 覆盖面自检（先跑）────────────────────────────────────────────────
    # 必须在逐文件检查**之前**：如果某个后缀根本没进覆盖集合，下面的循环会静默跳过它，
    # 于是"全部合规"是一句**没检查过它**的结论。这条自检让那种情况**当场变红**。
    uncovered = uncovered_tracked_files(files)
    if uncovered:
        print("发现 %d 个已跟踪文件的后缀**不在编码检查的覆盖集合内**："
              % len(uncovered))
        for f in uncovered:
            print("  %-46s （后缀 %r）" % (f, os.path.splitext(f)[1].lower() or "(无)"))
        print()
        print("这意味着体检**从未检查过**它们，而你会看到「全部合规」—— 那是假的。")
        print("修法（二选一，别默默放过）：")
        print("  · 若它是文本 ⇒ 把后缀加进本脚本的 `TEXT_EXT`；")
        print("  · 若它是二进制/产物 ⇒ 加进 `BINARY_EXT`（或写进 .gitignore 别入库）。")
        return 1

    problems = []
    checked = 0
    for f in files:
        if not is_text(f):
            continue
        checked += 1
        path = os.path.join(REPO, f.replace("/", os.sep))
        if args.list:
            print("  %s" % f)
        try:
            raw = open(path, "rb").read()
        except OSError as e:
            problems.append((f, "读不了：%s" % e))
            continue
        if raw[:3] == b"\xef\xbb\xbf":
            problems.append((f, "带 UTF-8 BOM（应为无 BOM）"))
        body = raw[3:] if raw[:3] == b"\xef\xbb\xbf" else raw
        try:
            body.decode("utf-8")
        except UnicodeDecodeError as e:
            problems.append((f, "不是有效 UTF-8：%s" % e))
            continue
        if b"\r\n" in body:
            problems.append((f, "含 CRLF 行尾（应为 LF），共 %d 处" % body.count(b"\r\n")))

    print("检查了 %d 个跟踪的文本文件（共 %d 个跟踪文件）" % (checked, len(files)))
    if problems:
        print("发现 %d 个不合规：" % len(problems))
        for f, why in problems:
            print("  %-46s %s" % (f, why))
        print("修法见 AGENTS.md「编码规则」：源文件转 UTF-8 无 BOM + LF；"
              "程序写出时别用 `Set-Content -Encoding UTF8`（PowerShell 5.1 会加 BOM）。")
        return 1
    print("全部合规：UTF-8 无 BOM + LF")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
