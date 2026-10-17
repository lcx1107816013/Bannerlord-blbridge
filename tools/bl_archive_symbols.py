#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BlBridge → Archive 直连：把崩溃栈里的托管符号，交给 Archive 的 C# 索引解释。

## 它解决什么（这是本项目最早被提出、一直没接上的那条边）

`bl_crash --deep` 与 `bl_exceptions` 能给出符号化的栈，例如：

    RTSCamera.CommandSystem.Logic.CommandSystemLogic.OnRemoveBehavior
    HarmonyLib.MethodCreatorTools.EmitCodes

但这两个名字**对解决问题没有帮助** —— 崩在 `OnRemoveBehavior` 里，
问题可能是「它期望一个已经 dispose 的对象」。缺少的是**那个方法的代码**。

这份代码在 Archive 的索引里（`csharp_methods` 45,687 条 / `csharp_types` 5,203 条，
`bannerlord.db` 623 MB）。Archive 把它暴露为 `ba_read_csharp_type` ——
一个**本模块无法调用**的工具，因为 Bridge 是 Python、Archive 是 TypeScript，
两者之间只有 stdio MCP。

所以本工具做的事情就一件事：**按 MCP 协议起一个 Archive 进程，问它一个类型，把答案带回来。**

## ★ 为什么不是「再写一份索引」

写一份 SQLite 直读也能出结果，但那会让 Bridge 依赖 Archive 的**内部 schema**。
Archive 换语言、换表结构、换字段名，Bridge 就会静默地读错。
走 MCP 只依赖**工具名和入参名**，那是公开契约。

这与 Core 的立场一致：Core 不 import 任何模块的代码，只说 MCP。

## 口径（读结论前先看）

1. **符号 → 类型名是推导，不是精确映射。** C# 栈帧形如
   `Namespace.Type.Method(...)`，本工具按「最后一个 `.` 之前」取类型段；
   泛型 ``Foo`1``、嵌套 `Outer+Inner`、构造器 `.ctor` 都按 Archive 的命名口径归一。
   推不出来就**如实报 unresolved**，不猜一个近似类型名 —— 猜错的代码比没有更贵。
2. **Archive 不在跑不代表没答案。** 本工具会**自己拉起** Archive（几十毫秒到几秒）。
   起不来时如实报 `archive_unavailable` 和启动错误，**不返回空结果冒充「没有这个类型」**。
3. **只读。** 只调 `ba_read_csharp_type`（`readOnlyHint: true`），不碰任何写工具。

## 用法

    python bl_archive_symbols.py --symbol "RTSCamera.CommandSystem.Logic.CommandSystemLogic.OnRemoveBehavior"
    python bl_archive_symbols.py --symbol A --symbol B --max-source 4000
    python bl_archive_symbols.py --from-crash          # 读最近一份崩溃的栈，逐帧解释
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# C# 的泛型 arity 后缀：`List`1` / `Dictionary`2`。反引号 + 数字整体去掉。
# 索引里 typeName 不含它（实测 0/5203 带反引号），所以它是**显示层**的记号，
# 不能进查询。
_ARITY = re.compile(r"`\d+")

# Archive 的启动方式：优先环境变量，其次仓库默认位置。
# 刻意**不**把 bun 的绝对路径写死在这里 —— 本机那个 bun 曾经是 0 字节的 WinGet 假文件，
# 硬编码路径会把「路径对但文件是假的」伪装成「Archive 坏了」。
ARCHIVE_CANDIDATES = [
    os.environ.get("BLBRIDGE_ARCHIVE_ENTRY"),
    r"D:\GitHub\BannerlordSage-variant\src\entrypoints\bannerlord-full-stdio.ts",
]
ARCHIVE_CWD = os.environ.get("BLBRIDGE_ARCHIVE_CWD", r"D:\GitHub\BannerlordSage-variant")

# MCP 的 stdio 传输用「一行一个 JSON」（不是 Content-Length 分帧）。
# 这一点必须写死在代码里：SDK 默认就是换行分帧，而 hand-rolled 客户端
# 最常见的错误就是按 HTTP 那套写 Content-Length，然后读到 EOF。
def _frame(obj):
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def _find_bun():
    """找到**真实**的 bun。

    PATH 上的 `bun` 在本机是 0 字节 WinGet 假文件：执行它无输出、无报错，
    看起来像挂起或「测试通过但什么都没跑」。所以按**文件大小**筛，不按存在性。
    """
    candidates = [
        os.environ.get("BLBRIDGE_BUN"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"
                           r"\Oven-sh.Bun_Microsoft.Winget.Source_8wekyb3d8bbwe"
                           r"\bun-windows-x64\bun.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            try:
                if os.path.getsize(c) >= 1024 * 1024:
                    return c
            except OSError:
                continue
    return None


class ArchiveUnavailable(Exception):
    """Archive 起不来 —— 与「这个类型不存在」是两件事，绝不能混报。"""


class ArchiveClient:
    """极简 MCP 客户端：只做 initialize / tools/call / 读一行响应。

    刻意不引入官方 SDK：Bridge 是「仅标准库」的项目（见各工具文件头），
    引入一个 Python MCP 包会让整个仓库多一个运行期依赖。
    """

    def __init__(self, timeout=90):
        self.timeout = timeout
        self.proc = None
        self._buf = b""
        self._next_id = 0

    def __enter__(self):
        entry = None
        for c in ARCHIVE_CANDIDATES:
            if c and os.path.isfile(c):
                entry = c
                break
        if entry is None:
            raise ArchiveUnavailable(
                "找不到 Archive 入口。设置 BLBRIDGE_ARCHIVE_ENTRY 指向 "
                "bannerlord-full-stdio.ts")
        bun = _find_bun()
        if bun is None:
            raise ArchiveUnavailable(
                "找不到真实的 bun（按大小 >=1MB 筛过；PATH 上那个可能是 0 字节假文件）。"
                "设置 BLBRIDGE_BUN 指向真实 bun.exe")

        env = dict(os.environ)
        env.setdefault("BANNERSAGE_EULA_ACCEPTED", "true")
        try:
            self.proc = subprocess.Popen(
                [bun, "run", entry],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=ARCHIVE_CWD, bufsize=0, env=env,
            )
        except OSError as exc:
            raise ArchiveUnavailable("无法启动 Archive：%s" % exc) from exc

        self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "bl-archive-symbols", "version": "1"},
        })
        # notifications/initialized 没有 id，也不需要响应
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def __exit__(self, *exc):
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        return False

    def _write(self, obj):
        if self.proc is None or self.proc.stdin is None:
            raise ArchiveUnavailable("Archive 进程不在")
        try:
            self.proc.stdin.write(_frame(obj))
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ArchiveUnavailable("写入 Archive 失败（进程已退出？）：%s" % exc) from exc

    def _read_line(self):
        while b"\n" not in self._buf:
            chunk = self.proc.stdout.read(1)
            if not chunk:
                err = b""
                try:
                    err = self.proc.stderr.read() or b""
                except Exception:  # noqa: BLE001
                    pass
                raise ArchiveUnavailable(
                    "Archive 未响应就退出：%s" % err.decode("utf-8", "replace")[:400])
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line.decode("utf-8", "replace"))

    def _request(self, method, params):
        self._next_id += 1
        my_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": my_id, "method": method, "params": params})
        # 只接受 id 匹配的那一行；通知和别的响应丢掉
        for _ in range(50):
            msg = self._read_line()
            if msg.get("id") == my_id:
                if "error" in msg:
                    raise ArchiveUnavailable(
                        "%s 返回错误：%s" % (method, json.dumps(msg["error"], ensure_ascii=False)))
                return msg.get("result") or {}
        raise ArchiveUnavailable("%s 没有在 50 帧内返回" % method)

    def call(self, name, arguments):
        return self._request("tools/call", {"name": name, "arguments": arguments})


def _text_of(result):
    """把 MCP 的 content 数组拼成纯文本。"""
    if not result:
        return ""
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text") or "")
    return "\n".join(parts)


# Archive 用**普通文本**报「类型不存在」，而不是 isError。
# 这个模板来自 `src/utils/csharp-type-reader.ts:48`（notFoundText 的默认值）。
# 用子串而不是完整匹配：模板里嵌了类型名，而且将来可能加前后缀。
_NOT_FOUND_MARKERS = (
    "was not found in the index",
    "was not found",
    "not found in the index",
)


def _looks_like_not_found(text):
    """这条响应是不是「没找到」而不是「这是你要的代码」。

    宁可多判一次 not_found 也不能漏 —— 漏判的结果是把一句 "not found"
    当成源码正文交给读代码的人，而他会以为那个类型真的长这样。
    """
    low = text.strip().lower()
    if not low:
        return False
    # 真正的源码块很长且有 C# 结构；not-found 是一句短话
    if len(low) > 400:
        return False
    return any(marker in low for marker in _NOT_FOUND_MARKERS)


# ── 符号 → 类型名 ────────────────────────────────────────────────────────

# C# 栈帧里的这几段不是类型/方法，是编译器生成的成员名。碰到就换算。
_MEMBER_SUFFIXES = (".ctor", ".cctor", ".MoveNext", ".Dispose")


def symbol_to_type(symbol):
    """`Ns.Type.Method` -> (Ns.Type, Method)。推不出来就返回 (None, None)。

    ★ 这里是**推导**，所以宁可返回 None 也不猜。
    猜错一个类型名，Archive 会返回一个别的类型的代码，而读代码的人不会怀疑它。

    两种情况要分清：
      * `Ns.Type.Method` —— 栈帧形态，拆出类型 + 方法。
      * `Type` / `Ns.Type` —— **类型名本身**，没有方法段。这是合法输入：
        调用者常常是先拿到一个类型名想问「这玩意儿是什么」。
        所以只按「段数」判断是不够的 —— 单段名（`Hero`）也是类型，
        而 `Hero` 正是知识库里验证过能查到的那个例子。
    """
    if not symbol or not isinstance(symbol, str):
        return None, None
    s = symbol.strip()
    if not s:
        return None, None
    # 去掉 Harmony / 泛型实参这类外壳：`Foo<T>.Bar` -> `Foo.Bar`
    while "<" in s:
        start = s.index("<")
        depth = 0
        end = -1
        for i in range(start, len(s)):
            if s[i] == "<":
                depth += 1
            elif s[i] == ">":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            break
        s = s[:start] + s[end + 1:]

    # native 帧形如 `BlBridge+0x1A2C`，没有类型信息 —— 但它是「能看出来是什么」的，
    # 所以要和「完全不像符号」分开报。
    if "+0x" in s.lower() or "!" in s:
        return None, None

    # 反引号泛型：`List`1` / `Foo`2` -> 去掉 arity 后缀。
    # 不剥的话 `Ns.Type`1.Method` 会带着反引号去查索引，而索引里的类型名没有它。
    # ★ 必须连同反引号后面的**数字**一起剥：只删反引号会得到 `Type1`，
    # 那是个看起来合理、实际上不存在的类型名 —— 最坏的一种错。
    s = _ARITY.sub("", s)

    parts = s.split(".")
    # 构造器在栈帧里是 `Ns.Type..ctor`（两个点），split 后会多出一个空段。
    # 只按部分数判断会切出一个尾点类型名 `Ns.Type.`，那不是任何真实类型。
    parts = [p for p in parts if p != ""]
    if not parts:
        return None, None

    if len(parts) == 1:
        # 裸类型名：整段就是类型，没有方法
        return parts[0], None

    method = parts[-1]
    type_name = ".".join(parts[:-1])
    if method in ("ctor", "cctor") or not method:
        return type_name, None
    return type_name, method


def explain_symbols(symbols, max_source=3000, include_source=True):
    """把一组符号交给 Archive，返回逐条结论。

    一次只起一个 Archive 进程处理全部符号 —— 起进程的成本远大于一次调用。
    """
    rows = []
    try:
        with ArchiveClient() as client:
            for sym in symbols:
                type_name, method = symbol_to_type(sym)
                row = {"symbol": sym, "type": type_name, "method": method}
                if type_name is None:
                    row["status"] = "unresolved"
                    row["reason"] = ("这个字符串不像 `Namespace.Type.Member` 形式的托管符号。"
                                     "native 帧（如 `BlBridge+0x1A2C`）没有类型信息，"
                                     "用 bl_crash --deep 拿符号化的栈。")
                    rows.append(row)
                    continue
                result = client.call("ba_read_csharp_type", {"typeName": type_name})
                text = _text_of(result)
                is_error = bool(result.get("isError"))
                if is_error:
                    row["status"] = "not_indexed"
                    row["detail"] = text[:400]
                    rows.append(row)
                    continue
                if not text.strip():
                    row["status"] = "empty"
                    rows.append(row)
                    continue

                # ★ ARCHIVE REPORTS "NOT FOUND" AS ORDINARY TEXT, WITH isError ABSENT.
                #
                # `csharp-type-reader.ts` returns
                #     { content: [{ type: 'text', text: "Type 'X' was not found in the index." }] }
                # — a 200-shaped success carrying a failure sentence. The first version
                # of this client checked only `isError`, so it reported
                #     status: "ok", source: "Type '...' was not found in the index."
                # and counted the miss as a resolution. That is the exact defect this
                # project keeps finding: the check cannot see what it is meant to verify,
                # and it reports success while nothing was found.
                #
                # The guard is the sentence itself, and it is checked BEFORE `ok` can
                # be set, because the real not-found text is the only signal Archive gives.
                if _looks_like_not_found(text):
                    row["status"] = "not_found"
                    row["detail"] = text.strip()[:200]
                    rows.append(row)
                    continue

                row["status"] = "ok"
                row["source_length"] = len(text)
                if include_source:
                    row["source"] = text[:max_source]
                    if len(text) > max_source:
                        row["truncated"] = True
                # 在源码里标出目标方法，免得让人自己在几千行里找
                if method:
                    row["method_mentioned"] = method in text
                rows.append(row)
    except ArchiveUnavailable as exc:
        return {
            "ok": False,
            "reason": "archive_unavailable",
            "detail": str(exc),
            "note": ("Archive 起不来 —— 这不代表这些类型不存在。"
                     "**不要**把它读成「没有这个类型」。"),
            "symbols": [{"symbol": s, "status": "unknown"} for s in symbols],
        }
    return {
        "ok": True,
        "count": len(rows),
        "resolved": sum(1 for r in rows if r.get("status") == "ok"),
        "rows": rows,
    }


def symbols_from_latest_crash():
    """从最近一份崩溃报告里取栈符号。失败就返回空列表（调用方会如实报）。"""
    try:
        import bl_crash  # noqa: E402
    except Exception:  # noqa: BLE001
        return [], "bl_crash 不可导入"
    try:
        report = bl_crash.build_report()
    except Exception as exc:  # noqa: BLE001
        return [], "bl_crash 报价失败：%s" % exc
    frames = ((report or {}).get("stack") or {}).get("frames") or []
    syms = [f.get("symbol") for f in frames if f.get("symbol")]
    return syms, None if syms else "最近一份崩溃报告里没有符号化的帧"


def main():
    # 中文 Windows 的控制台默认 GBK，直接 print 中文会变成乱码 —— 而乱码的诊断信息
    # 比没有信息更糟（看起来像编码 bug，其实是显示层）。钉成 UTF-8。
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    ap = argparse.ArgumentParser(description="把崩溃栈符号交给 Archive 的 C# 索引解释")
    ap.add_argument("--symbol", action="append", default=[], help="托管符号，可重复")
    ap.add_argument("--from-crash", action="store_true", help="从最近一份崩溃读取栈符号")
    ap.add_argument("--max-source", type=int, default=3000, help="每个类型最多返回多少字符")
    ap.add_argument("--no-source", action="store_true", help="只要结论，不要源码正文")
    ap.add_argument("--out", help="把 JSON 写到这里")
    args = ap.parse_args()

    symbols = list(args.symbol)
    note = None
    if args.from_crash:
        more, note = symbols_from_latest_crash()
        symbols.extend(more)
    if not symbols:
        out = {"ok": False, "reason": "no_symbols",
               "detail": note or "没有提供 --symbol，也没有可读的崩溃栈"}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2

    out = explain_symbols(symbols, max_source=args.max_source,
                          include_source=not args.no_source)
    if note:
        out.setdefault("notes", []).append(note)

    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        with io.open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("written: %s" % args.out)
    else:
        print(text)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
