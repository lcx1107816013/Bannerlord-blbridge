#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""仓库编码体检：全仓库一律 **UTF-8 无 BOM + LF**（见 AGENTS.md 的「编码规则」）。

为什么要有这个脚本：编码规则只写在文档里，迟早会被改回去（2026-09-25 就走过两条弯路：
"只改 errors 不改 encoding" 的 safe_streams、"把中文译成英文来绕开乱码"）。规则必须**可执行**。

检查项（对 git 跟踪的全部文本文件）：
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
TEXT_EXT = (".py", ".cs", ".md", ".json", ".xml", ".ps1", ".txt", ".cfg", ".ini", ".yml", ".yaml")
TEXT_NAMES = (".gitignore", ".gitattributes", ".editorconfig")


def is_text(path):
    base = os.path.basename(path)
    return base in TEXT_NAMES or path.endswith(TEXT_EXT)


def tracked_files():
    """git 跟踪的文件列表（相对仓库根的 POSIX 路径）。"""
    p = subprocess.run(["git", "-C", REPO, "ls-files"], capture_output=True)
    if p.returncode != 0:
        print("错误：这不是一个 git 仓库（git ls-files 失败）", file=sys.stderr)
        return None
    out = p.stdout.decode("utf-8", "replace")
    return [f for f in out.split("\n") if f.strip()]


def main(argv):
    bl_common.safe_streams()
    ap = argparse.ArgumentParser(description="仓库编码体检（UTF-8 无 BOM + LF）")
    ap.add_argument("--list", action="store_true", help="列出所有被检查的文本文件")
    args = ap.parse_args(argv)

    files = tracked_files()
    if files is None:
        return 2
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
