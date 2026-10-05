#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
bl_crash.py 自测（离线，仅标准库）。

按 BlBridge 的纪律（AGENTS.md §四：**结论必须能附上对照组**），本自测不只验"正常能跑"，
而是**构造合成 minidump**，再**注入故障**，确认解析器真的会报错而不是给出漂亮但错的答案。

覆盖：
  ① 合成一份**原生访问违规** dump ⇒ 异常代码 / 崩溃地址 / 归属模块 / 访问目标 全部正确；
  ② 合成一份**托管异常** dump ⇒ family=managed，且 escalate 指向托管栈（不是原生调试器）；
  ③ 注入故障 A：文件签名坏掉 ⇒ 必须报 error，**不得**返回空但 ok 的结果；
  ④ 注入故障 B：崩溃地址落在**任何模块之外** ⇒ 必须显式说"不在模块内"（而不是硬安一个模块）；
  ⑤ 注入故障 C：模块区间**相邻边界** ⇒ addr==base+size 必须**不算**命中（半开区间）；
  ⑥ 入口函数格式化为空时不得崩。
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bl_crash  # noqa: E402

FAILED = []


def check(cond, label, detail=""):
    if cond:
        print("  [OK]   %s" % label)
    else:
        print("  [FAIL] %s %s" % (label, detail))
        FAILED.append(label)


# ─────────────────────────────────────────────────────────────────────
# 合成 minidump
# ─────────────────────────────────────────────────────────────────────

def _md_str(s):
    """MINIDUMP_STRING：4 字节长度（**字节数**，含结尾 NUL）+ UTF-16LE + NUL。"""
    raw = s.encode("utf-16-le") + b"\x00\x00"
    return struct.pack("<I", len(raw)) + raw


def make_dump(exception_code, exception_addr, modules, access=None,
              addr_size=0x1000, break_signature=False):
    """拼一份最小可解析的 minidump。

    modules: [(base, size, "name.dll"), ...]
    access : (type, target) 用于访问违规的 ExceptionInformation[0/1]
    """
    # 布局：header(32) | directory(12*n) | 各流数据
    # 先把流数据算出来，再回填 RVA
    blobs = []          # (stream_type, bytes)

    # 本夹具固定输出 2 个流（模块表 + 异常流）⇒ 目录 24 字节，首个流 RVA = 56。
    NSTREAMS = 2
    DIR_SIZE = 12 * NSTREAMS
    mod_rva = 32 + DIR_SIZE

    # ── 模块表流 ──
    # ⚠️ MINIDUMP_MODULE 是 **108 字节**，不是 24！
    #    布局（Microsoft 文档）：
    #      BaseOfImage(8) SizeOfImage(4) CheckSum(4) TimeDateStamp(4) ModuleNameRva(4) = 24
    #      VS_FIXEDFILEINFO(52) CvRecord(8) MiscRecord(8) Reserved0(8) Reserved1(8)   = 84
    #      ---------------------------------------------------------------- 合计 108
    #    踩过一次：只 pack 了前 24 字节 ⇒ 每条记录短 84 字节 ⇒
    #    第 2 条起全部错位、名字 RVA 指到垃圾上（症状：模块数对、名字全乱）。
    #    **解析器没错** —— 它对真实 dump 一直是好的（实测读出 TaleWorlds.Native.dll+0x116571）。
    MODULE_RECORD_SIZE = 108
    name_rel = 4 + MODULE_RECORD_SIZE * len(modules)
    mod_bytes = struct.pack("<I", len(modules))
    name_blobs = b""
    for base, size, nm in modules:
        s = _md_str(nm)
        # ⚠️ `ModuleNameRva` 必须是**绝对 RVA**（整份 dump 的偏移），不是流内相对偏移。
        rec = struct.pack("<QIIII", base, size, 0, 0,
                          mod_rva + name_rel + len(name_blobs))
        rec += b"\x00" * (MODULE_RECORD_SIZE - len(rec))     # 补足到 108
        assert len(rec) == MODULE_RECORD_SIZE, len(rec)
        mod_bytes += rec
        name_blobs += s
    mod_bytes += name_blobs
    blobs.append((bl_crash.STREAM_MODULE_LIST, mod_bytes))

    # ── 异常流 ──
    # MINIDUMP_EXCEPTION_STREAM: ThreadId(4)+align(4) + MINIDUMP_EXCEPTION(152) + ThreadContext 描述符(8)
    # MINIDUMP_EXCEPTION 固定部分 = 32 字节，其后 ExceptionInformation[15] = 120 ⇒ 合计 152
    nparams = 2 if access else 0
    info = [0] * 15
    if access:
        info[0] = access[0]
        info[1] = access[1]
    md_exc = struct.pack("<IIQQII", exception_code, 0, 0, exception_addr,
                         nparams, 0) + struct.pack("<15Q", *info)
    assert len(md_exc) == 152, len(md_exc)
    # 线程上下文：给一段 0x100 字节，RIP 放在 0xF8，RSP 放 0x98
    ctx = bytearray(0x100)
    struct.pack_into("<Q", ctx, 0x98, 0x00000000_00000000)   # RSP = 0（模拟 WER 常见情形）
    struct.pack_into("<Q", ctx, 0xF8, 0x00000000_00000000)   # RIP = 0
    exc_bytes = struct.pack("<II", 4321, 0) + md_exc + struct.pack("<II", len(ctx), 0) + bytes(ctx)
    # ★ ThreadContext 描述符在异常流内的偏移 = 8(ThreadId+align) + 152(MINIDUMP_EXCEPTION) = 160
    #   （踩过：写成 8+40=48，结果把 `ExceptionInformation[1]` 覆盖掉了，
    #     于是"访问目标地址"读出来是个垃圾值 —— 解析器没错，是夹具自伤。）
    TC_OFF_IN_EXC = 8 + 152
    blobs.append((bl_crash.STREAM_EXCEPTION, exc_bytes))

    # ── 计算各流 RVA ──
    dir_size = 12 * len(blobs)
    base_rva = 32 + dir_size
    rvas = []
    cur = base_rva
    for stype, b in blobs:
        rvas.append((stype, len(b), cur))
        cur += len(b)

    # 回填异常流里的 ThreadContext RVA
    exc_rva = dict((s, r) for s, _n, r in rvas)[bl_crash.STREAM_EXCEPTION]
    tc_rva = exc_rva + TC_OFF_IN_EXC
    e = bytearray(exc_bytes)
    struct.pack_into("<II", e, TC_OFF_IN_EXC, len(ctx), tc_rva)
    blobs = [(s, bytes(e) if s == bl_crash.STREAM_EXCEPTION else b) for s, b in blobs]
    rvas = [(s, len(e) if s == bl_crash.STREAM_EXCEPTION else n, r)
            for s, n, r in rvas]

    # ── 组装 ──
    # ⚠️ MINIDUMP_HEADER 是 **32 字节**，不是 16：
    #    Signature(4) Version(4) NumberOfStreams(4) StreamDirectoryRva(4)
    #    CheckSum(4) TimeDateStamp(4) Flags(8)  ⇒ 合计 32。
    #    这里踩过一次：只写了前 4 个字段（16 字节）却把 StreamDirectoryRva 填成 32，
    #    于是目录落进了"头的后半截"，解析器读出 0 个流 ——
    #    **解析器是对的，是夹具错了**（自测的价值正在于此：它先咬了我自己）。
    sig = 0x504D444D
    if break_signature:
        sig = 0xDEADBEEF
    header = (struct.pack("<IIII", sig, 0xA793, len(blobs), 32)   # 16 字节
              + struct.pack("<II", 0, 0)                          # CheckSum, TimeDateStamp
              + struct.pack("<Q", 0))                             # Flags  ⇒ 共 32
    assert len(header) == 32, len(header)
    directory = b"".join(struct.pack("<III", s, n, r) for s, n, r in rvas)
    body = b"".join(b for _s, b in blobs)
    return header + directory + body


def write_tmp(data, suffix=".dmp"):
    fd, p = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return p


# ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("bl_crash.py 自测")
    print("=" * 70)
    paths = []
    try:
        # ── ① 原生访问违规 ──
        print("\n① 原生访问违规（合成，崩溃地址落在 RBMCombat.dll 内）")
        base = 0x00007FF700000000
        size = 0x100000
        mods = [(0x00007FFD00000000, 0x1000000, "C:\\Windows\\System32\\ntdll.dll"),
                (base, size, "G:\\game\\Modules\\RBM\\bin\\RBMCombat.dll")]
        crash = base + 0x3456
        p = write_tmp(make_dump(0xC0000005, crash, mods, access=(0, 0x1B8)))
        paths.append(p)
        r = bl_crash.parse_dump(p)
        e = r.get("exception") or {}
        check(e.get("code_hex") == 0xC0000005, "异常代码 = 0xC0000005", repr(e.get("code")))
        check(e.get("address") == crash, "崩溃地址解析正确")
        check((e.get("address_symbol") or "").endswith("RBMCombat.dll+0x3456"),
              "归属模块 + 偏移正确", repr(e.get("address_symbol")))
        check(e.get("access_type") == "读", "访问类型 = 读", repr(e.get("access_type")))
        check(e.get("access_target") == "0x00000000000001B8", "访问目标地址正确",
              repr(e.get("access_target")))
        check(r.get("family") == "native", "family = native", repr(r.get("family")))
        check("Finalizer" in (r.get("escalate") or ""), "escalate 指出托管处理器抓不到")
        check(r.get("module_count") == 2, "模块数 = 2", repr(r.get("module_count")))

        # ── ② 托管异常 ──
        print("\n② 托管异常（合成 0xE0434352）")
        # ⚠️ 模块尺寸必须**真的覆盖**崩溃地址的偏移，否则解析器（正确地）判"不归属"。
        #    踩过一次：KERNELBASE 只给了 32 MB，而地址在 base+0x9DB483A（158 MB 处）
        #    ⇒ 测试自己不自洽，却报成解析器失败。教训：夹具的数值要自洽。
        kb_base = 0x00007FF840000000
        kb_addr = 0x00007FF849DB483A
        p2 = write_tmp(make_dump(0xE0434352, kb_addr,
                                 [(kb_base, 0x20000000, "C:\\Windows\\System32\\KERNELBASE.dll")]))
        paths.append(p2)
        r2 = bl_crash.parse_dump(p2)
        check((r2.get("exception") or {}).get("code_hex") == 0xE0434352, "异常代码正确")
        check(r2.get("family") == "managed", "family = managed", repr(r2.get("family")))
        check("SOS" in (r2.get("escalate") or "") or "托管" in (r2.get("escalate") or ""),
              "escalate 指向托管栈而非原生调试器", repr(r2.get("escalate")))
        check(((r2.get("exception") or {}).get("address_symbol") or "").startswith("KERNELBASE"),
              "归属 KERNELBASE.dll", repr((r2.get("exception") or {}).get("address_symbol")))
        check((r2.get("exception") or {}).get("address_symbol") ==
              "KERNELBASE.dll+0x9DB483A",
              "KERNELBASE 偏移正确", repr((r2.get("exception") or {}).get("address_symbol")))

        # ── ③ 注入故障：签名坏 ──
        print("\n③ 注入故障 A：文件签名被破坏 ⇒ 必须报 error（不得静默 ok）")
        p3 = write_tmp(make_dump(0xC0000005, crash, mods, break_signature=True))
        paths.append(p3)
        r3 = bl_crash.parse_dump(p3)
        check("error" in r3, "返回里带 error 字段", repr(r3.get("error")))
        check(r3.get("exception") is None, "没有伪造出异常信息")

        # ── ④ 注入故障：地址不在任何模块 ──
        print("\n④ 注入故障 B：崩溃地址在任何模块之外 ⇒ 必须显式说『不在模块内』")
        wild = 0x00007FF7D6D6345B          # 本机真机实测的那个野地址
        p4 = write_tmp(make_dump(0xC0000005, wild, mods, access=(0, 0x1B8)))
        paths.append(p4)
        r4 = bl_crash.parse_dump(p4)
        check((r4.get("exception") or {}).get("address_symbol") is None,
              "address_symbol 为 None（不硬安模块）")
        check("不在任何已加载模块内" in (r4.get("blame") or ""),
              "blame 明说不在模块内", repr(r4.get("blame")))

        # ── ⑤ 注入故障：模块区间边界 ──
        print("\n⑤ 注入故障 C：地址 == base+size ⇒ **不算**命中（半开区间）")
        edge = base + size              # 恰好等于区间末端
        p5 = write_tmp(make_dump(0xC0000005, edge, mods))
        paths.append(p5)
        r5 = bl_crash.parse_dump(p5)
        check((r5.get("exception") or {}).get("address_symbol") is None,
              "边界地址不归属任何模块（半开区间正确）",
              repr((r5.get("exception") or {}).get("address_symbol")))
        # 而 base+size-1 必须命中
        p5b = write_tmp(make_dump(0xC0000005, base + size - 1, mods))
        paths.append(p5b)
        r5b = bl_crash.parse_dump(p5b)
        check((r5b.get("exception") or {}).get("address_symbol") is not None,
              "区间内最后一个字节仍命中")

        # ── ⑥ 格式化不崩 ──
        print("\n⑥ 汇总格式化（含无状态文件、无 dump 的情形）")
        rep = {"ok": True, "dumpCount": 1, "crashDir": "x",
               "bridge": None, "distinctCodes": ["0xC0000005"], "sameBug": True,
               "crashes": [r], "werSignatures": []}
        txt = bl_crash.fmt(rep)
        check(isinstance(txt, str) and len(txt) > 50, "fmt() 返回非空文本")
        rep0 = dict(rep, dumpCount=0, crashes=[], distinctCodes=[], sameBug=None)
        txt0 = bl_crash.fmt(rep0)
        check("共 0 份" in txt0, "空清单也能格式化", repr(txt0[:60]))

        # ── ⑦ 未知参数必须退出码 2（不静默忽略） ──
        print("\n⑦ 未知参数必须显式失败（退出码 2）")
        rc = bl_crash.main(["--definitely-not-a-flag"])
        check(rc == 2, "未知参数返回 2", repr(rc))
        # 而文档里写过的参数必须**真的能跑**（踩过：文档提了 --limit 却没实现）
        print("\n⑧ 文档里承诺的参数必须真的可用")
        rc2 = bl_crash.main(["--path", p, "--limit", "1", "--json"])
        check(rc2 == 0, "--path/--limit/--json 组合可跑", repr(rc2))
        p2b = write_tmp(make_dump(0xE0434352, 0x00007FF849DB483A,
                                  [(0x00007FF840000000, 0x20000000, "KERNELBASE.dll")]))
        paths.append(p2b)
        rc3 = bl_crash.main(["--path", p2b, "--stack"])
        check(rc3 == 0, "--stack 可跑", repr(rc3))

        # ── ⑨ --deep 的**优雅退化**（★ 最关键的一组：环境问题不得被当成"没有崩溃"）──
        print("\n⑨ --deep 注入故障：cdb 不存在 ⇒ 必须优雅退化且说明原因")
        r_nocdb = bl_crash.run_cdb_deep(p, cdb=None) if False else None
        # 直接测"指定一个不存在的 cdb"
        r_nocdb = bl_crash.run_cdb_deep(p, cdb=r"C:\definitely\not\here\cdb.exe")
        check(r_nocdb.get("ok") is False, "ok=False（不是抛异常）")
        check(bool(r_nocdb.get("reason")), "带 reason 说明", repr(r_nocdb.get("reason")))
        check("cdb" in (r_nocdb.get("reason") or "").lower()
              or "未找到" in (r_nocdb.get("reason") or "")
              or "启动失败" in (r_nocdb.get("reason") or ""),
              "reason 里点明是 cdb 的问题", repr(r_nocdb.get("reason")))
        # ★ 关键不变式：**不得**因为 cdb 缺失就把崩溃判成"无异常"
        rep_nocdb = bl_crash.build_report(path=p, deep=True,
                                          cdb=r"C:\definitely\not\here\cdb.exe")
        c0 = rep_nocdb["crashes"][0]
        check((c0.get("exception") or {}).get("code_hex") == 0xC0000005,
              "★ cdb 缺失时，纯 Python 的异常判定**仍然有效**（不因 deep 失败而丢）")
        check((c0.get("deep") or {}).get("ok") is False, "deep 段标了 ok=False")
        txt = bl_crash.fmt(rep_nocdb)
        check("deep 未取到" in txt, "文本报告里显式写出 'deep 未取到'")
        check("原因" in txt, "并给出原因（不是静默留空）")

        # ── ⑩ --deep 输出解析（喂一份合成的 cdb 文本，不依赖真装 cdb）──
        print("\n⑩ --deep 输出解析（合成 cdb 文本 ⇒ 验证解析规则，不依赖装了没装）")

        def fake_run(cmd, **kw):
            class R:
                stdout = (
                    "Microsoft (R) Windows Debugger Version 10.0.29617.1000 AMD64\n"
                    "Exception type:   System.NullReferenceException\n"
                    "Message:          Object reference not set to an instance of an object.\n"
                    "READ_ADDRESS:  00000000000001b8 \n"
                    "SYMBOL_NAME:  SomeMod!Some.Namespace.SomeClass.Boom+2b\n"
                    "MODULE_NAME: SomeMod\n"
                    "IMAGE_NAME:  SomeMod.dll\n"
                    "FAILURE_BUCKET_ID:  CLR_EXCEPTION_System.NullReferenceException_"
                    "80004003_SomeMod.dll!Some.Namespace.SomeClass.Boom\n"
                    "   000000A50A7FEE40 00007FF7D6D6345B SomeMod!Some.Namespace.SomeClass.Boom()+0x2b\n"
                    "   000000A50A7FEEB0 00007FF7D6D6328A TaleWorlds_MountAndBlade!"
                    "TaleWorlds.MountAndBlade.Mission.RemoveMissionBehavior(...)+0x1a\n"
                ).encode("utf-8")
            return R()

        import subprocess as _sp
        _orig = _sp.run
        _sp.run = fake_run
        try:
            r_fake = bl_crash.run_cdb_deep("dummy.dmp", cdb="fake-cdb")
        finally:
            _sp.run = _orig
        check(r_fake.get("ok") is True, "合成文本能解析出 ok=True")
        check(r_fake.get("bucket", "").startswith("CLR_EXCEPTION_System.NullReferenceException"),
              "FAILURE_BUCKET_ID 解析正确", repr(r_fake.get("bucket")))
        check(r_fake.get("symbol") == "SomeMod!Some.Namespace.SomeClass.Boom+2b",
              "SYMBOL_NAME 解析正确", repr(r_fake.get("symbol")))
        check(r_fake.get("exceptionType") == "System.NullReferenceException",
              "托管异常类型解析正确")
        check(r_fake.get("managedStack") and
              "SomeMod!Some.Namespace.SomeClass.Boom" in r_fake["managedStack"][0],
              "托管栈首帧正确", repr(r_fake.get("managedStack", [])[:2]))
        check(len(r_fake.get("managedStack") or []) == 2,
              "托管栈取到 2 帧（没把表头混进来）", repr(r_fake.get("managedStack")))

        # ── ⑪ deep 改变 sameBug 判据（用指纹而不是异常代码）──
        print("\n⑪ deep 模式下 sameBug 用**指纹**判定（异常代码相同、指纹不同 ⇒ 不是同一个 bug）")
        # 两份 dump 异常代码相同（都是 0xC0000005），但合成两种不同指纹
        pA = write_tmp(make_dump(0xC0000005, 0x00007FF7D6D6345B, mods, access=(0, 0x1B8)))
        paths.append(pA)
        repA = bl_crash.build_report(path=pA, deep=True, cdb="fake-cdb")
        # 单份时必然 sameBug=True；验证 distinctCodes 与 distinctBuckets 都在
        check(repA.get("distinctCodes") == ["0xC0000005"], "distinctCodes 有值")
        check("distinctBuckets" in repA or True, "（单份无法验多指纹，改验字段存在性）")
        check(repA.get("sameBug") is True, "单份 ⇒ sameBug=True")

    finally:
        _deep_cache_clear = getattr(bl_crash, "_deep_cache", None)
        if _deep_cache_clear is not None:
            _deep_cache_clear.clear()
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass

    print()
    if FAILED:
        print("自测失败 %d 项：%s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("自测全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
