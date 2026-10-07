#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bl_report.py 的离线自测（宿主侧，仅标准库，**不需要游戏**）。

## 为什么它不是摆设

按本仓库纪律：**没有对照组的验证不是验证，只是自证**。
每条判据都配"应报 / 不应报"的成对样本，且针对**真实会踩的坑**：

| # | 判据 | 哪种输入会红 |
|---|---|---|
| 1 | 报告**自包含**：无外部 URL、无 `<script>` | 引了 CDN ⇒ 离线打不开；引了脚本 ⇒ 多一个注入面 |
| 2 | HTML 结构闭合（DOCTYPE / `</html>` / 内联 `<style>`） | 半截 HTML，浏览器渲染成空白 |
| 3 | ★ **注入防护**：日志里的 `<script>` / `"` / `&` 被转义 | **这是安全属性** —— 日志是不可信输入，直拼进 HTML 就是注入点 |
| 4 | 中文正常渲染（非乱码） | 编码写错 ⇒ 整份报告变 U+FFFD |
| 5 | 缺账本时**明说"不代表没崩溃"** | 把"没数据"读成"没问题" |
| 6 | 口径声明在场（"被吞掉"≠"已修复"） | 报告被当成结论用 |
| 7 | **不覆盖已有文件**（同秒连导两份） | 用 `Create` 而非 `CreateNew` ⇒ 静默覆盖上一份 |
| 8 | 只读：**不修改**任何源文件 | 导出顺手改了日志 |
| 9 | Markdown 版含同样口径 | 两种格式说法不一致 |
| 10 | ★ **反向对照**：空数据也能生成**结构完整**的报告 | 判据/渲染恒依赖数据存在，空场景直接崩 |
| 11 | 坏行计数（JSONL 有畸形行） | 静默跳过坏行 |

用法：
    python tools/bl_report_selftest.py [--verbose]
退出码：0 = 全过；1 = 有失败。
"""
import argparse
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bl_report as rp                               # noqa: E402

FAILS = []
CHECKS = [0]
VERBOSE = False

# 恶意样本：日志内容是不可信输入，报告必须把它**当文本**渲染
XSS = '<script>alert("xss")</script> & <img src=x onerror=alert(1)>'


def check(name, cond, detail=""):
    CHECKS[0] += 1
    if cond:
        if VERBOSE:
            print("  [ok] %s" % name)
    else:
        FAILS.append("%s%s" % (name, (" —— " + detail) if detail else ""))
        print("  [FAIL] %s%s" % (name, (" —— " + detail) if detail else ""))


def fixture(log_dir, *, with_guard=True, with_exc=True, with_status=True,
            xss=False, bad_rows=0):
    os.makedirs(log_dir, exist_ok=True)
    if with_guard:
        rows = [
            {"t": "guard", "utc": "2026-10-07T10:00:00Z", "runToken": "rt1", "pid": 42,
             "action": "pass", "reason": "installed", "target": "CrashGuard.Install",
             "count": 1},
            {"t": "guard", "utc": "2026-10-07T10:01:00Z", "runToken": "rt1", "pid": 42,
             "action": "swallow", "reason": "swallowed",
             "type": "System.NullReferenceException",
             "message": XSS if xss else "Object reference not set",
             "target": "TaleWorlds.MountAndBlade.Mission.Tick",
             "frames": XSS if xss else "at Foo.Bar()", "count": 1},
            {"t": "guard", "utc": "2026-10-07T10:02:00Z", "runToken": "rt1", "pid": 42,
             "action": "pass", "reason": "breaker_open",
             "type": "System.InvalidOperationException",
             "target": "TaleWorlds.MountAndBlade.Mission.Tick", "count": 21},
        ]
        with io.open(os.path.join(log_dir, "crashguard.jsonl"), "w",
                     encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            for i in range(bad_rows):
                fh.write("{bad json %d\n" % i)
    if with_exc:
        rows = [
            {"t": "session", "utc": "2026-10-07T10:00:00Z", "note": "start"},
            {"t": "exception", "type": "System.IO.IOException",
             "message": XSS if xss else "file busy",
             "stackHead": "at System.IO.FileStream..ctor()", "seq": 3, "tick": 10},
        ]
        with io.open(os.path.join(log_dir, "exceptions.jsonl"), "w",
                     encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    if with_status:
        with io.open(os.path.join(log_dir, "bridge_status.json"), "w",
                     encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"state": "loaded", "version": "0.8.47", "pid": 42,
                                 "runToken": "rt1", "cleanExit": False,
                                 "gameVersion": "v1.4.8"}, ensure_ascii=False))


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description="bl_report 离线自测")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    VERBOSE = args.verbose
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                     # noqa: BLE001
        pass

    print("bl_report 自测")
    print("=" * 74)

    tmp = tempfile.mkdtemp(prefix="bl_report_")
    try:
        # ── 1/2：自包含 + 结构完整 ──
        print("\n[1][2] 自包含（无外部 URL / 无 script）+ 结构完整")
        d1 = os.path.join(tmp, "full")
        fixture(d1)
        html = rp.build_html(rp.collect(log_dir=d1))
        check("1a 无外部 URL", "http://" not in html and "https://" not in html,
              "引了外部资源 ⇒ 离线/断网时打不开")
        check("1b 无 <script> 标签", "<script" not in html.lower(),
              "报告不该含脚本（多一个注入面）")
        check("2a 有 DOCTYPE", html.lstrip().startswith("<!DOCTYPE html>"))
        check("2b 有 </html>", html.rstrip().endswith("</html>"))
        check("2c 有内联 <style>", "<style>" in html)
        check("2d 三个主栏目都在",
              all(k in html for k in ("一、速览", "二、崩溃守卫账本", "三、全量异常")),
              "缺栏目")

        # ── 3：注入防护（安全属性，必测）──
        print("\n[3] 注入防护：日志里的 HTML 必须被转义")
        d2 = os.path.join(tmp, "xss")
        fixture(d2, xss=True)
        h2 = rp.build_html(rp.collect(log_dir=d2))
        check("3a 原始 <script> 未出现", "<script>alert" not in h2,
              "★ 日志内容被当 HTML 执行了（注入）")
        check("3b 尖括号被转义", "&lt;script&gt;" in h2, "未转义 < >")
        check("3c 引号被转义", "&quot;" in h2, "未转义引号")
        check("3d & 被转义", "&amp;" in h2, "未转义 &")
        check("3e onerror 未形成可执行属性",
              "onerror=alert(1)>" not in h2, "★ 属性注入未拦住")
        # 反向对照：干净输入不应被过度转义成乱码
        d3 = os.path.join(tmp, "clean")
        fixture(d3)
        h3 = rp.build_html(rp.collect(log_dir=d3))
        check("3f 对照：正常文本未被破坏",
              "Object reference not set" in h3, "把正常文本也转义坏了")

        # ── 4：中文 ──
        print("\n[4] 中文渲染")
        check("4a 报告含中文", any("\u4e00" <= c <= "\u9fff" for c in h3), "无中文")
        check("4b 无 U+FFFD 乱码", "\ufffd" not in h3, "出现替换字符 ⇒ 编码写错")

        # ── 5/6：缺数据时的口径 ──
        print("\n[5][6] 缺账本时的口径")
        d5 = os.path.join(tmp, "noguard")
        fixture(d5, with_guard=False)
        h5 = rp.build_html(rp.collect(log_dir=d5))
        check("5a 明说账本不存在", "账本不存在" in h5, h5[:200])
        check("5b 明说'不代表没崩溃'", "不代表" in h5, "把没数据读成没问题")
        check("6a 口径声明在场", "不等于" in h5 and "已修复" in h5,
              "缺「被吞≠已修复」声明")

        # ── 7：不覆盖已有文件 ──
        print("\n[7] 不覆盖已有文件（同秒连导两份）")
        out = os.path.join(tmp, "out")
        p1 = rp.write_report("A", out_dir=out, stamp="20261007-120000")
        p2 = rp.write_report("B", out_dir=out, stamp="20261007-120000")
        check("7a 两次路径不同", p1 != p2, "%s vs %s" % (p1, p2))
        check("7b 第一份内容未被覆盖",
              io.open(p1, encoding="utf-8").read() == "A", "第一份被覆盖了")
        check("7c 第二份内容正确",
              io.open(p2, encoding="utf-8").read() == "B")

        # ── 8：只读 ──
        print("\n[8] 只读（不修改源文件）")
        before = {f: os.path.getmtime(os.path.join(d3, f))
                  for f in os.listdir(d3)}
        before_h = {f: io.open(os.path.join(d3, f), "rb").read()
                    for f in before}
        rp.collect(log_dir=d3)
        rp.build_html(rp.collect(log_dir=d3))
        after = {f: io.open(os.path.join(d3, f), "rb").read() for f in before}
        check("8a 源文件字节未变", before_h == after, "导出改了源文件")

        # ── 9：Markdown 版口径一致 ──
        print("\n[9] Markdown 版含同样口径")
        md = rp.build_markdown(rp.collect(log_dir=d1))
        check("9a 含「被吞掉」≠「已修复」", "不等于" in md and "已修复" in md, md[:300])
        check("9b 含速览表", "## 速览" in md)

        # ── 10：反向对照 —— 空数据也能生成完整报告 ──
        print("\n[10] 反向对照：全空数据也能生成结构完整的报告")
        d10 = os.path.join(tmp, "empty")
        os.makedirs(d10)
        h10 = rp.build_html(rp.collect(log_dir=d10))
        check("10a 空数据不抛异常且有结构", h10.lstrip().startswith("<!DOCTYPE html>")
              and h10.rstrip().endswith("</html>"), "空场景崩了或半截")
        check("10b 空数据仍给口径提示", "不代表" in h10 or "账本不存在" in h10)
        md10 = rp.build_markdown(rp.collect(log_dir=d10))
        check("10c Markdown 版同样不崩", "# BlBridge 崩溃报告" in md10)

        # ── 11：坏行计数 ──
        print("\n[11] 坏行计数（JSONL 畸形行）")
        d11 = os.path.join(tmp, "badrow")
        fixture(d11, bad_rows=3)
        data11 = rp.collect(log_dir=d11)
        check("11a 坏行被计数", data11["guardBad"] == 3,
              "guardBad=%d" % data11["guardBad"])
        h11 = rp.build_html(data11)
        check("11b 报告里点出坏行", "坏行" in h11, "坏行没进报告")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 74)
    if FAILS:
        print("失败 %d / %d：" % (len(FAILS), CHECKS[0]))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("全部通过：%d / %d" % (CHECKS[0], CHECKS[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
