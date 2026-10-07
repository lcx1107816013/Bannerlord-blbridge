#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
崩溃报告导出（HTML + Markdown）—— 宿主侧，**只读**，仅标准库。

## 它做什么

把已有的三个数据源合成**一份自包含、可分享的报告**（单文件 HTML，内联 CSS，无外部依赖，
不需要联网、不引用任何 CDN）：

| 来源 | 内容 |
|---|---|
| `<LogDir>\\crashguard.jsonl` | 崩溃守卫账本（哪些异常被吞/被放行）—— 工具 `bl_crashguard` |
| `<LogDir>\\exceptions.jsonl` | 全量异常（FirstChance，含被 catch 的）—— 工具 `bl_exception_detail` |
| `<LogDir>\\bridge_status.json` | 会话身份（版本/pid/runToken/cleanExit/构建一致性） |

外加**可选**：本地崩溃词典给的人话解释与修复建议（`BLBRIDGE_LEXICON_DIR`，缺了如实标注）。

## ⚠️ 为什么自写模板而不用现成资源

本仓库**不含**任何第三方报告模板 —— 报告模板是**有版权的表达**（不是事实/接口）。
我们只借鉴"报告该有哪些栏目"这个**思路**，HTML/CSS 全部自己写，因此：
  · 无第三方模板 → 无许可义务、无 attribution 需求；
  · 体积小（单文件、内联样式，几十 KB），便于贴给他人。

## 输出去哪（默认**不覆盖已有文件**）

`<LogDir>\\reports\\crashreport-<UTC时间戳>.html`。
时间戳精确到秒并带序号兜底 ⇒ **同一秒内连导两份也不会互相覆盖**。
（照 `JsonlWriter.Open` 的既有教训：用 `CreateNew` + 序号重试，不用 `Create`。）

## 口径（读报告前必看）

- **报告只是"材料汇编"，不是结论**。"被吞掉" ≠ "已修复"；详见 `docs/crash-guard.md`。
- 报告里**不含**游戏存档内容、账号、token —— 只含日志与元数据。
- 导出**不修改任何源文件**。
"""
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bl_common                                      # noqa: E402

# 每个列表最多渲染多少条（防止一份日志把 HTML 撑到几十 MB）
MAX_GUARD_ROWS = 200
MAX_EXC_ROWS = 200


def _esc(s):
    """HTML 转义。**报告里所有来自日志的文本都必须过它** ——
    日志内容是不可信输入（可能含 `<script>`），直接拼进 HTML 会变成注入点。
    """
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _read_jsonl(path, limit=None):
    rows = []
    bad = 0
    if not os.path.isfile(path):
        return rows, bad, False
    with io.open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(o, dict):
                rows.append(o)
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    return rows, bad, True


def collect(log_dir=None):
    """汇总三源 + 词典（全部只读）。"""
    ld = log_dir or bl_common.default_log_dir()
    guard, g_bad, g_exists = _read_jsonl(os.path.join(ld, "crashguard.jsonl"), MAX_GUARD_ROWS)
    exc, e_bad, e_exists = _read_jsonl(os.path.join(ld, "exceptions.jsonl"), MAX_EXC_ROWS)

    status = None
    sp = os.path.join(ld, "bridge_status.json")
    if os.path.isfile(sp):
        try:
            with io.open(sp, "r", encoding="utf-8-sig", errors="replace") as fh:
                status = json.load(fh)
        except Exception:                                 # noqa: BLE001
            status = None

    # 词典：可选，缺了如实标注（不静默）
    lex_note = "未加载"
    lex_hits = []
    try:
        import bl_lexicon as _lx
        lex, st = _lx.load_lexicon()
        if lex is None:
            lex_note = "不可用：" + str(st.get("reason"))
        else:
            lex_note = "已加载"
            seen = set()
            for r in guard:
                if r.get("action") != "swallow":
                    continue
                t = r.get("type")
                if not t or t in seen:
                    continue
                seen.add(t)
                q = _lx.describe(lex, exc_type=t,
                                 text=" ".join(filter(None, [t, r.get("message"),
                                                             r.get("frames")])),
                                 lang="zh", limit=2)
                lex_hits.append({"type": t, "q": q})
    except Exception as exc2:                             # noqa: BLE001
        lex_note = "出错：%s: %s" % (type(exc2).__name__, exc2)

    return {
        "logDir": ld,
        "guard": guard, "guardBad": g_bad, "guardExists": g_exists,
        "exc": exc, "excBad": e_bad, "excExists": e_exists,
        "status": status, "lexNote": lex_note, "lexHits": lex_hits,
    }


CSS = """
:root{--bg:#111418;--fg:#e6e6e6;--dim:#9aa4b2;--card:#1a1f26;--line:#2a323d;
--crit:#ff5c5c;--warn:#ffb454;--info:#5cc8ff;--ok:#5cd97a}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--fg);
font:14px/1.6 "Segoe UI",Roboto,"Microsoft YaHei",sans-serif}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:16px;margin:28px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:14px;margin:16px 0 6px;color:var(--dim)}
.sub{color:var(--dim);font-size:12px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;
padding:12px 14px;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;
vertical-align:top;word-break:break-word}
th{color:var(--dim);font-weight:600}
code,pre{font-family:Consolas,Menlo,monospace;font-size:12px}
pre{background:#0d1117;border:1px solid var(--line);border-radius:4px;
padding:10px;overflow-x:auto;white-space:pre-wrap}
.sev{padding:1px 7px;border-radius:10px;font-size:11px;font-weight:700}
.sev-critical{background:rgba(255,92,92,.16);color:var(--crit)}
.sev-warn{background:rgba(255,180,84,.16);color:var(--warn)}
.sev-info{background:rgba(92,200,255,.16);color:var(--info)}
.sev-ok{background:rgba(92,217,122,.16);color:var(--ok)}
.warnbox{border-left:3px solid var(--warn);background:rgba(255,180,84,.08);
padding:10px 12px;border-radius:0 5px 5px 0;margin:12px 0}
.dim{color:var(--dim)}
.kv{display:grid;grid-template-columns:200px 1fr;gap:2px 10px;font-size:13px}
.kv dt{color:var(--dim)}
footer{margin-top:32px;color:var(--dim);font-size:12px;
border-top:1px solid var(--line);padding-top:12px}
"""


def _sev_of_guard(r):
    """账本记录 → 严重度（与 bl_crashguard 的 REASON_INFO 同口径）。"""
    reason = r.get("reason") or ""
    if reason in ("breaker_open", "quota_exhausted", "install_failed"):
        return "critical"
    if reason in ("fatal_passthrough", "swallowed", "unavailable", "target_failed"):
        return "warn"
    return "info"


def build_html(data, now=None):
    now = now or time.strftime("%Y-%m-%d %H:%M:%S")
    st = data.get("status") or {}
    g = data["guard"]
    e = data["exc"]

    sw = [r for r in g if r.get("action") == "swallow"]
    ps = [r for r in g if r.get("action") == "pass"]
    crit = [r for r in g if _sev_of_guard(r) == "critical"]

    L = []
    L.append("<!DOCTYPE html>")
    L.append('<html lang="zh-CN"><head><meta charset="utf-8">')
    L.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    L.append("<title>BlBridge 崩溃报告 %s</title>" % _esc(now))
    L.append("<style>%s</style></head><body>" % CSS)
    L.append("<h1>BlBridge 崩溃报告</h1>")
    L.append('<div class="sub">生成于 %s　·　数据目录 %s</div>'
             % (_esc(now), _esc(data["logDir"])))

    # ── 结论速览 ──
    L.append("<h2>一、速览</h2>")
    L.append('<div class="kv">')
    L.append("<dt>会话状态</dt><dd>%s</dd>" % _esc(st.get("state") or "(无状态文件)"))
    L.append("<dt>游戏版本 / 模块版本</dt><dd>%s / %s</dd>"
             % (_esc(st.get("gameVersion") or "?"), _esc(st.get("version") or "?")))
    L.append("<dt>pid / runToken</dt><dd>%s / %s</dd>"
             % (_esc(st.get("pid")), _esc(st.get("runToken"))))
    L.append("<dt>干净退出</dt><dd>%s</dd>"
             % _esc("是" if st.get("cleanExit") else ("否（崩溃/强杀）" if st else "?")))
    L.append("<dt>被吞异常 / 放行</dt><dd>%d / %d</dd>" % (len(sw), len(ps)))
    L.append("<dt>致命放行（游戏可能仍崩）</dt><dd>%d</dd>"
             % len([r for r in g if r.get("reason") == "fatal_passthrough"]))
    L.append("<dt>守卫已失效（熔断/配额）</dt><dd>%d</dd>" % len(crit))
    L.append("<dt>异常记录行数</dt><dd>%d</dd>" % len(e))
    L.append("<dt>词典</dt><dd>%s</dd>" % _esc(data["lexNote"]))
    L.append("</div>")

    # ── 关键警告 ──
    L.append('<div class="warnbox"><b>读报告前必看</b><ul>'
             "<li><b>「被吞掉」不等于「已修复」</b>：被吞的方法没做完它该做的事，"
             "调用方以为成功了 ⇒ 可能留下<b>不报错</b>的静默损坏"
             "（存档不一致 / AI 卡死 / 数值错乱）。</li>"
             "<li><b>熔断 / 配额触发是坏消息</b>：说明守卫已停止保护，游戏可能在带病运行。</li>"
             "<li><b>致命放行</b>（OOM / 栈溢出等）意味着游戏<b>很可能仍然崩了</b>"
             "—— 那类异常永不吞；请配合 minidump 分析。</li>"
             "<li>本报告是<b>材料汇编，不是结论</b>。</li></ul></div>")

    # ── 崩溃守卫账本 ──
    L.append("<h2>二、崩溃守卫账本</h2>")
    if not data["guardExists"]:
        L.append('<div class="card dim">账本不存在。这<b>不代表</b>没发生崩溃 —— '
                 "守卫默认关闭，或本次会话没有异常。</div>")
    else:
        if data["guardBad"]:
            L.append('<div class="card"><span class="sev sev-warn">坏行</span> '
                     "%d 行无法解析（已计数，未静默丢弃）</div>" % data["guardBad"])
        L.append("<table><tr><th>时间</th><th>动作</th><th>reason</th><th>异常类型</th>"
                 "<th>消息</th><th>目标方法</th></tr>")
        for r in sorted(g, key=lambda x: x.get("utc") or "")[-MAX_GUARD_ROWS:]:
            sev = _sev_of_guard(r)
            # ★ 必须带 `message` 列 —— 实测（2026-10-07 自测 3f）发现首版漏了它：
            #   而"被吞异常的消息"恰恰是最有诊断价值的一栏（没有它，
            #   `swallowed` 只告诉你"吞了个 NullReference"，不知道吞的是哪一处）。
            L.append("<tr><td>%s</td><td><span class='sev sev-%s'>%s</span></td>"
                     "<td><code>%s</code></td><td>%s</td><td>%s</td><td><code>%s</code></td></tr>"
                     % (_esc(r.get("utc")), sev, _esc(r.get("action")),
                        _esc(r.get("reason")), _esc(r.get("type") or "-"),
                        _esc((r.get("message") or "-")[:300]),
                        _esc(r.get("target") or "-")))
        L.append("</table>")

        # 栈帧（只给"被吞"和"熔断"的，那两类最需要定位）
        detail = [r for r in g if r.get("frames")
                  and r.get("reason") in ("swallowed", "breaker_open")]
        if detail:
            L.append("<h3>栈帧（被吞 / 熔断的条目）</h3>")
            for r in detail[:40]:
                L.append('<div class="card"><b>%s</b> '
                         "<span class='dim'>%s</span><pre>%s</pre></div>"
                         % (_esc(r.get("type")), _esc(r.get("target")),
                            _esc(r.get("frames"))))

    # ── 全量异常 ──
    L.append("<h2>三、全量异常（FirstChance，含被 catch 的）</h2>")
    if not data["excExists"]:
        L.append('<div class="card dim">无异常记录文件。</div>')
    else:
        L.append('<div class="card dim">口径提醒：行数 <b>不等于</b>出现次数 —— '
                 "去重键 =（类型 + 栈首帧），且只在首次出现时落盘。</div>")
        L.append("<table><tr><th>类型</th><th>消息</th><th>栈首帧</th></tr>")
        for r in e[-MAX_EXC_ROWS:]:
            if r.get("t") != "exception":
                continue
            L.append("<tr><td>%s</td><td>%s</td><td><code>%s</code></td></tr>"
                     % (_esc(r.get("type")), _esc((r.get("message") or "")[:200]),
                        _esc(r.get("stackHead") or "-")))
        L.append("</table>")

    # ── 修复建议（来自词典，可选）──
    if data["lexHits"]:
        L.append("<h2>四、按类型的修复建议（本地词条库）</h2>")
        for h in data["lexHits"]:
            q = h["q"]
            ex = q.get("exception") or {}
            L.append('<div class="card"><b>%s</b>' % _esc(h["type"]))
            if ex.get("displayName"):
                L.append("<div>%s</div>" % _esc(ex["displayName"]))
            if ex.get("description"):
                L.append('<div class="dim">%s</div>' % _esc(ex["description"]))
            for s in (ex.get("suggestions") or [])[:5]:
                L.append("<div>· %s</div>" % _esc(s.get("text")))
            for m in (q.get("matches") or [])[:2]:
                if m.get("action"):
                    L.append("<div>建议：%s</div>" % _esc(m["action"]))
            L.append("</div>")

    L.append("<footer>由 BlBridge 生成（只读汇总，未修改任何源文件）。"
             "报告内不含存档内容、账号或凭据。</footer>")
    L.append("</body></html>")
    return "\n".join(L)


def build_markdown(data, now=None):
    """同一份数据的 Markdown 版（便于贴进 issue / 聊天）。"""
    now = now or time.strftime("%Y-%m-%d %H:%M:%S")
    st = data.get("status") or {}
    g = data["guard"]
    sw = [r for r in g if r.get("action") == "swallow"]
    ps = [r for r in g if r.get("action") == "pass"]
    crit = [r for r in g if _sev_of_guard(r) == "critical"]

    L = []
    L.append("# BlBridge 崩溃报告")
    L.append("")
    L.append("> 生成于 %s　·　数据目录 `%s`" % (now, data["logDir"]))
    L.append("")
    L.append("## 速览")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 会话状态 | %s |" % (st.get("state") or "(无)"))
    L.append("| 模块版本 | %s |" % (st.get("version") or "?"))
    L.append("| pid / runToken | %s / %s |" % (st.get("pid"), st.get("runToken")))
    L.append("| 干净退出 | %s |" % ("是" if st.get("cleanExit") else "否"))
    L.append("| 被吞 / 放行 | %d / %d |" % (len(sw), len(ps)))
    L.append("| 守卫已失效（熔断/配额） | %d |" % len(crit))
    L.append("| 异常记录行数 | %d |" % len(data["exc"]))
    L.append("")
    L.append("> ⚠️ **「被吞掉」不等于「已修复」** —— 半完成的调用可能留下不报错的静默损坏。")
    L.append("")
    L.append("## 崩溃守卫账本")
    L.append("")
    if not data["guardExists"]:
        L.append("_账本不存在（守卫默认关闭，或不代表没崩溃）_")
    else:
        L.append("| 时间 | 动作 | reason | 类型 | 消息 | 目标 |")
        L.append("|---|---|---|---|---|---|")
        for r in g[-MAX_GUARD_ROWS:]:
            L.append("| %s | %s | `%s` | %s | %s | `%s` |"
                     % (r.get("utc"), r.get("action"), r.get("reason"),
                        r.get("type") or "-",
                        (r.get("message") or "-")[:160].replace("|", "\\|"),
                        r.get("target") or "-"))
    L.append("")
    L.append("## 全量异常")
    L.append("")
    if not data["excExists"]:
        L.append("_无异常记录文件_")
    else:
        L.append("> 行数不等于出现次数（去重键 = 类型+栈首帧，只在首次出现落盘）")
        L.append("")
        for r in data["exc"][-MAX_EXC_ROWS:]:
            if r.get("t") != "exception":
                continue
            L.append("- `%s` %s" % (r.get("type"), (r.get("message") or "")[:160]))
    L.append("")
    if data["lexHits"]:
        L.append("## 修复建议")
        L.append("")
        for h in data["lexHits"]:
            ex = (h["q"].get("exception") or {})
            L.append("### %s" % h["type"])
            if ex.get("displayName"):
                L.append("**%s**" % ex["displayName"])
            if ex.get("description"):
                L.append(ex["description"])
            for s in (ex.get("suggestions") or [])[:5]:
                L.append("- %s" % s.get("text"))
            L.append("")
    L.append("---")
    L.append("_由 BlBridge 生成（只读汇总）。不含存档内容、账号或凭据。_")
    return "\n".join(L)


def write_report(html, out_dir=None, log_dir=None, fmt="html", stamp=None):
    """写出报告。**不覆盖已有文件**（同秒冲突自动加序号）。返回实际路径。"""
    d = out_dir or os.path.join(log_dir or bl_common.default_log_dir(), "reports")
    os.makedirs(d, exist_ok=True)
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    ext = ".md" if fmt == "md" else ".html"
    base = os.path.join(d, "crashreport-%s" % stamp)
    for i in range(20):
        cand = base + (("_%d" % i) if i else "") + ext
        if not os.path.exists(cand):
            with io.open(cand, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(html)
            return cand
    raise RuntimeError("连续 20 个候选文件名都已存在：%s" % base)


def main(argv=None):
    import argparse
    try:
        bl_common.safe_streams()
    except Exception:                                     # noqa: BLE001
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="BlBridge 崩溃报告导出（HTML / Markdown）")
    ap.add_argument("--logDir", default=None, help="日志目录")
    ap.add_argument("--out", default=None, help="输出目录（默认 <LogDir>\\reports）")
    ap.add_argument("--format", default="html", choices=("html", "md"), help="默认 html")
    ap.add_argument("--stdout", action="store_true", help="打到标准输出，不写文件")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    data = collect(log_dir=args.logDir)
    text = build_html(data) if args.format == "html" else build_markdown(data)
    if args.stdout:
        print(text)
        return 0
    path = write_report(text, out_dir=args.out, log_dir=args.logDir, fmt=args.format)
    print("报告已写出：%s" % path)
    print("  （%d 字节；被吞 %d 条 / 放行 %d 条 / 异常 %d 行）"
          % (len(text),
             len([r for r in data["guard"] if r.get("action") == "swallow"]),
             len([r for r in data["guard"] if r.get("action") == "pass"]),
             len(data["exc"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
