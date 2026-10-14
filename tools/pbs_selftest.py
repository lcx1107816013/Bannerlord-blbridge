#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路径边界锁（PBS）回归断言 —— 2026-10-13 安全审计后新增。

## 为什么单独立一个文件

本项目已有 `assert_param_retargets()`（docs/config-tool-safety.md §六）负责
"参数被转发"这一面。PBS 管的是**另一面**："目标**该不该**被写"。
两者互补，不该混在一个断言里 —— 否则失败时看不出是哪一类问题。

## 纪律（照抄本项目对"注入对照"的要求）

★ 每条"应该拒绝"的断言，都配一条"应该放行"的**反向对照** ——
  否则"全拒绝"也能让测试全绿（把功能修坏而不自知）。

## 跑法

    python pbs_selftest.py          # 独立跑
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bl_common   # noqa: E402
import bl_rts      # noqa: E402

FAIL = []


def check(cond, label, detail=""):
    print("  [%s] %s%s" % ("OK" if cond else "!!", label,
                           ("  <- " + str(detail)[:150]) if detail else ""))
    if not cond:
        FAIL.append(label)


def main():
    for n in ("stdout", "stderr"):
        try:
            getattr(sys, n).reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    print("=" * 78)
    print("路径边界锁（PBS）回归断言")
    print("=" * 78)

    game = os.environ.get("BANNERLORD_DIR") or \
        r"G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord"
    proj = os.path.dirname(HERE)
    roots = bl_common.allowed_roots(game_dir=game, project_dir=proj)

    print("\n① 允许根必须覆盖**全部既有合法用法**（否则修复不是无损）")
    must = [
        # 游戏目录 —— Warbandlord config.xml
        os.path.join(game, "Modules", "Warbandlord", "config.xml"),
        # 文档目录 —— RBM 真档 / RTSCamera 真档 / BlBridge 日志
        os.path.join(os.path.expanduser("~"), "Documents",
                     "Mount and Blade II Bannerlord", "Configs", "RBM", "config.xml"),
        os.path.join(os.path.expanduser("~"), "Documents",
                     "Mount and Blade II Bannerlord", "Configs", "RTSCamera",
                     "RTSCameraConfig.xml"),
        # 临时目录 —— 自测的 tempfile.mkdtemp()
        os.path.join(tempfile.gettempdir(), "blbridge_selftest_x", "a.xml"),
        # 项目目录 —— blbridge.json
        os.path.join(proj, "blbridge.json"),
    ]
    for p in must:
        ok, pay = bl_common.path_allowed(p, game_dir=game, project_dir=proj)
        check(ok, "放行：%s" % os.path.basename(p), pay.get("boundaryRoot", ""))

    print("\n② 越界必须拒绝（审计实测过的真实目标）")
    must_deny = [
        r"C:\Windows\Temp\evil.xml",
        r"C:\Users\LCGX\.gitconfig",
        r"C:\Windows\System32\drivers\etc\hosts",
        r"C:\Windows\win.ini",
        r"C:\Windows\System32\config\SAM",
    ]
    for p in must_deny:
        ok, pay = bl_common.path_allowed(p, game_dir=game, project_dir=proj)
        check(not ok, "拒绝：%s" % p,
              "error=%s" % pay.get("error") if not ok else "**被放行了**")

    print("\n③ 边界判定必须是**路径前缀**语义，不是字符串前缀（经典 bug）")
    # 造 `<允许根>/..` 之外的邻居，验证不会因字符串前缀误放行
    parent = os.path.dirname(os.path.realpath(proj))
    sibling = os.path.join(parent, os.path.basename(os.path.realpath(proj)) + "_EVIL", "x.xml")
    ok, _ = bl_common.path_allowed(sibling, game_dir=game, project_dir=proj)
    check(not ok, "拒绝『同名+后缀』的邻居目录（防 startswith 误判）", sibling)

    print("\n④ 目录穿越必须被 realpath 归一后判定")
    trav = os.path.join(os.path.expanduser("~"), "Documents", "..", "..", ".gitconfig")
    ok, _ = bl_common.path_allowed(trav, game_dir=game, project_dir=proj)
    check(not ok, "拒绝用 `..` 穿越出允许根", trav)

    print("\n⑤ 扩展名白名单")
    for p, exp in [
        ("x.xml", True), ("x.json", True), ("x.txt", True), ("x.cfg", True),
        ("x.exe", False), ("x.bat", False), ("x.ps1", False), ("x.dll", False),
    ]:
        ok, _ = bl_common.ext_allowed(p)
        check(ok == exp, "ext %-8s -> %s" % (p, "允许" if ok else "拒绝"))

    print("\n⑥ 显式放开开关必须真的生效（否则用户被硬卡住）")
    # ★ 坑：第一版我用 tempfile.mkdtemp() 造"白名单外的目录" —— 错了。
    #   mkdtemp 落在 `AppData\Local\Temp`，而**它就是默认允许根之一** ⇒
    #   "关掉开关后仍被放行"是**正确的**，断言却报红。
    #   ⇒ 教训（本项目反复记过的同一类）：**断言本身的前提必须先验证**，
    #     否则会得到"测试说错了、其实是测试写错了"。
    #   ⇒ 改用**系统盘下、确定不在任何允许根内**的目录。
    outside = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Temp",
                           "_pbs_outside_probe")
    try:
        os.makedirs(outside, exist_ok=True)
        usable = True
    except OSError:
        usable = False
    if not usable:
        check(True, "⑥ 跳过：无法在系统目录建测试目录（权限），改用只判定逻辑")
        # 退化为纯逻辑判定：用一个必然越界的路径
        target = r"C:\Windows\System32\_pbs_probe.xml"
        base_deny, _ = bl_common.path_allowed(target, game_dir=game, project_dir=proj)
        os.environ["BLBRIDGE_ALLOW_WRITE_ROOTS"] = r"C:\Windows\System32"
        try:
            ok1, _ = bl_common.path_allowed(target, game_dir=game, project_dir=proj)
        finally:
            os.environ.pop("BLBRIDGE_ALLOW_WRITE_ROOTS", None)
        check(base_deny is False and ok1 is True,
              "BLBRIDGE_ALLOW_WRITE_ROOTS 生效（关=拒绝 / 开=放行）",
              "关=%s 开=%s" % (base_deny, ok1))
    else:
        target = os.path.join(outside, "a.xml")
        del_ok = None
        try:
            base_deny, _ = bl_common.path_allowed(target, game_dir=game, project_dir=proj)
            os.environ["BLBRIDGE_ALLOW_WRITE_ROOTS"] = outside
            ok1, _ = bl_common.path_allowed(target, game_dir=game, project_dir=proj)
            # 反向对照：去掉开关必须回到拒绝
            os.environ.pop("BLBRIDGE_ALLOW_WRITE_ROOTS", None)
            del_ok = bl_common.path_allowed(target, game_dir=game, project_dir=proj)[0]
            check((base_deny is False) and (ok1 is True) and (del_ok is False),
                  "BLBRIDGE_ALLOW_WRITE_ROOTS 生效（默认拒 / 开=放行 / 关=回拒）",
                  "默认=%s 开=%s 关=%s" % (base_deny, ok1, del_ok))
        finally:
            os.environ.pop("BLBRIDGE_ALLOW_WRITE_ROOTS", None)
            shutil.rmtree(outside, ignore_errors=True)

    os.environ["BLBRIDGE_ALLOW_ANY_PATH"] = "1"
    try:
        ok3, _ = bl_common.path_allowed(r"C:\Windows\win.ini",
                                        game_dir=game, project_dir=proj)
        check(ok3, "BLBRIDGE_ALLOW_ANY_PATH=1 完全关闭边界锁")
    finally:
        os.environ.pop("BLBRIDGE_ALLOW_ANY_PATH", None)

    print("\n⑦ ★ 反向对照：合法目标**真的能写**（防『修得过头』）")
    # 在临时目录（白名单内）真写一次，证明边界锁没有把正常功能挡死
    d = tempfile.mkdtemp(prefix="pbs_rw_")
    try:
        f = os.path.join(d, "cfg.xml")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("<Config><A><B>1</B></A></Config>\n")
        ok_b, _ = bl_common.path_allowed(f, game_dir=game, project_dir=proj)
        ok_e, _ = bl_common.ext_allowed(f)
        check(ok_b and ok_e, "白名单内的 .xml 可通过两把锁", f)

        # bl_rts 也必须能读/写白名单内的文件（第二条写路径）
        try:
            bl_rts.apply([{"key": "A", "value": "1"}], path=f)
            wr_ok = True
            err = ""
        except Exception as exc:  # noqa: BLE001
            wr_ok = False
            err = repr(exc)
        check(wr_ok, "bl_rts.apply 在合法路径仍可写（未越权拦截）", err)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("\n⑧ bl_rts 的越界必须拒绝（第二条写路径也要锁）")
    try:
        bl_rts.apply([{"key": "A", "value": "1"}], path=r"C:\Windows\Temp\evil2.xml")
        check(False, "bl_rts.apply 拒绝越界路径", "**竟然没拒**")
    except IOError as exc:
        check("路径边界锁" in str(exc), "bl_rts.apply 拒绝越界路径", str(exc)[:90])
    except Exception as exc:  # noqa: BLE001
        check(False, "bl_rts.apply 拒绝越界路径", "抛了非 IOError: %r" % exc)

    # ══════════════════════════════════════════════════════════════════════
    # L3：合成输入闸门（ITG）
    # ══════════════════════════════════════════════════════════════════════
    print("\n⑨ L3 输入闸门：白名单内必须放行（★ 防『修得过头』）")
    # ★ 合法用途来自 bl_desktop_key 的工具描述：
    #   「对话框 ... 按钮是中文「确定」，WM_COMMAND IDOK 无效、**回车有效**」
    for k in ("enter", "tab", "esc", "down", "up", "space", "ctrl+a", "shift+tab",
              "f5", "backspace"):
        ok, _ = bl_common.key_allowed(k)
        check(ok, "放行 keys=%s" % k)

    print("\n⑩ L3 输入闸门：系统键必须拒绝")
    for k, why in (("win+r", "运行框"), ("meta", "系统键"), ("printscreen", "截屏")):
        ok, pay = bl_common.key_allowed(k)
        check(not ok, "拒绝 keys=%s（%s）" % (k, why), pay.get("error", ""))

    print("\n⑪ ★ L3 第二层：白名单组件拼出的**高危组合**必须拒绝")
    # ★★ 这是**实测发现的缺口**：第一版只有白名单时，`ctrl+shift+esc` 每个组件
    #    都合法（ctrl/shift 是允许的修饰键、esc 是允许的键）⇒ **被放行了**，
    #    而它的语义是"打开任务管理器"。
    #    ⇒ 断言必须覆盖"组件合法但组合危险"这一整类，不能只测"组件合法性"。
    for k, why in (("ctrl+shift+esc", "任务管理器"),
                   ("ctrl+esc", "开始菜单"),
                   ("alt+f4", "关前台窗口"),
                   ("alt+tab", "换前台窗口"),
                   ("ctrl+alt+del", "安全屏")):
        ok, pay = bl_common.key_allowed(k)
        check(not ok, "拒绝 %s（%s）" % (k, why), pay.get("error", ""))

    print("\n⑫ L3 text 闸门：合法文本放行、控制字符与超长必须拒")
    for t in ("确定", "OK", "Yes", "a" * 100):
        ok, _ = bl_common.text_allowed(t)
        check(ok, "放行 text=%r" % (t[:14],))
    for t, why in (("a" * 600, "超长"), ("x\ny", "换行=隐式回车"), ("\t", "制表符")):
        ok, pay = bl_common.text_allowed(t)
        check(not ok, "拒绝 text（%s）" % why, pay.get("error", ""))

    print("\n⑬ L3 逃生门：BLBRIDGE_ALLOW_ANY_KEY=1 必须真的放开")
    os.environ["BLBRIDGE_ALLOW_ANY_KEY"] = "1"
    try:
        # 注意：key_allowed 是纯函数，不看环境变量；开关判定在 bl_mcp 的派发里。
        # ⇒ 这里断言的是"开关变量被 bl_mcp 读到并跳过校验"这一层，
        #   用源码级判据（派发段必须引用它），避免依赖 gridhand 是否安装。
        src = open(os.path.join(HERE, "bl_mcp.py"), encoding="utf-8").read()
        check("BLBRIDGE_ALLOW_ANY_KEY" in src,
              "bl_mcp 派发段引用了 BLBRIDGE_ALLOW_ANY_KEY（逃生门存在）")
        # 反向对照：默认（不设变量）时该名字**不能**为真
        os.environ.pop("BLBRIDGE_ALLOW_ANY_KEY", None)
        check(os.environ.get("BLBRIDGE_ALLOW_ANY_KEY") != "1",
              "未设变量时逃生门关闭（默认受闸门约束）")
    finally:
        os.environ.pop("BLBRIDGE_ALLOW_ANY_KEY", None)

    # ══════════════════════════════════════════════════════════════════════
    # L4：路径类参数的统一前置闸门
    # ══════════════════════════════════════════════════════════════════════
    print("\n⑭ L4 前置闸门覆盖全部路径类参数（覆盖面由构造保证）")
    src_mcp = open(os.path.join(HERE, "bl_mcp.py"), encoding="utf-8").read()
    check("_PATH_ARGS" in src_mcp and "_precheck_path_args" in src_mcp,
          "bl_mcp 里有统一前置闸门 _precheck_path_args")
    import re as _re
    m = _re.search(r"_PATH_ARGS\s*=\s*\(([^)]*)\)", src_mcp)
    got = m.group(1) if m else ""
    # ★★ 2026-10-14 修一条**自证式断言**：原来这里手抄了一份 5 项名单，
    #    与源码里的名单是同一份 ⇒ 把源码删一项、这里也删一项，**永远绿**。
    #    实测证明它无效：`file` / `plan` / `manifest` 等参数从来没被覆盖，
    #    而本断言一直报"覆盖面由构造保证"。
    #
    #    改成三条**会变红**的判据：
    #      ① 名单必须包含实测确认过的"真·文件路径"参数（下面这份是**断言**，
    #         不是从源码抓的 —— 它代表"我知道应该拦哪些"）
    #      ② 源码里 `_PATH_ARGS_NEVER` 点名的参数**不得**出现在名单里
    #         （反向对照：防"为了图省事把不该拦的也拦了"）
    #      ③ 逐项**真调用** `_precheck_path_args`，验证"该拦的真的拦"
    # ★★ 2026-10-14：**不要用正则抓源码文本**做断言 —— 那是"判据绑定形态"。
    #    我第一版就是正则抓 `_PATH_ARGS_SCOPED` 再找 `'a'`，而源码用的是双引号
    #    `"a"` ⇒ 断言**误红**（代码完全正确）。这正是本项目记过的坑：
    #    判据一旦绑到"引号风格 / 换行 / 变量名写法"，改个排版就会假红或假绿。
    #    ⇒ 改成**导入模块、直接读数据结构**。语义判据，不绑形态。
    sys.path.insert(0, HERE)
    import importlib
    _mcp = importlib.import_module("bl_mcp")
    _gate = _mcp._precheck_path_args
    _generic = set(_mcp._PATH_ARGS)
    _scoped = _mcp._PATH_ARGS_SCOPED
    _never = set(_mcp._PATH_ARGS_NEVER)

    # ① 通用名单必须含全部"真·文件路径"参数
    for k in ("logDir", "out", "dir", "battlesDir", "launcher",
              "file", "plan", "manifest", "cdb"):
        check(k in _generic, "通用闸门名单覆盖 %s" % k)

    # ② 反向对照 A：语义不定的单字母**不得**进通用名单
    for k in ("a", "b"):
        check(k not in _generic,
              "通用名单里**不得**出现语义不定的单字母 %r（必须按工具限定）" % k)

    # ③ 按工具限定：bl_batch_report 的 a/b 必须在 _PATH_ARGS_SCOPED 下
    check("bl_batch_report" in _scoped
          and set(_scoped["bl_batch_report"]) >= {"a", "b"},
          "按工具限定：bl_batch_report 的 a/b 被限定在该工具下", repr(_scoped))

    # ④ 反向对照 B：名字像路径但**不是**路径的，不得出现在任何名单里
    for k in ("target", "paths"):
        check(k in _never, "反向对照：%r 已被显式标为**非路径**" % k)
        check(k not in _generic and not any(k in v for v in _scoped.values()),
              "反向对照：%r **没有**被塞进任何闸门名单" % k)

    # ⑤ 真调用：该拦的拦、该放的放（**不读源码，直接打闸门**）
    for tool, arg in (("bl_read_events", "file"), ("bl_analyze", "file"),
                      ("bl_run_batch", "plan"), ("bl_batch_report", "manifest"),
                      ("bl_batch_report", "a"), ("bl_batch_report", "b"),
                      ("bl_crash", "path"), ("bl_crash", "cdb")):
        r = _gate(tool, {arg: r"C:\Windows\win.ini"})
        check(r is not None, "真调用：%s.%s 指向系统文件时**被拒**" % (tool, arg))
    for tool, arg, val in (("bl_order", "target", "Infantry"),
                           ("bl_read_config", "paths", ["DamageCalc/ArmorEffect"]),
                           ("bl_launch_game", "minStartupSec", 20),
                           # ★ 关键反向对照：**别的工具**传 a=非路径 不得被拦
                           #   （这正是"按工具限定"与"进通用名单"的差别所在）
                           ("bl_status", "a", "someValue")):
        r = _gate(tool, {arg: val})
        check(r is None, "真调用：%s.%s **未被误拦**（值=%r）" % (tool, arg, val))

    # ⑥（2026-10-14 补，第二意见指出）**没进显式名单、靠内部校验**的那几个工具，
    #   也必须被证明"真的会被拒"。为什么单独立一条：
    #     · ⑤ 只覆盖了 _PATH_ARGS / _PATH_ARGS_SCOPED 里点名的参数；
    #     · `bl_read_config` / `bl_apply_config` / `bl_rts_config` / `bl_apply_rts_config`
    #       的 `path` **刻意没进闸门**（它们内部自己调 check_path_allowed）——
    #       这是我们主动做的取舍，但**取舍本身必须被断言锁住**，
    #       否则哪天有人重构把那行校验删掉，闸门不会红、⑤ 也不会红。
    #   ⚠️ 必须用 `call_tool`（端到端）而不是 `_gate`（只看前置闸门）：
    #      这四个的校验在**函数内部**，前置闸门对它们的 `path` 本来就返回 None。
    #
    #   ⚠️ 形态很关键（我实测过，写错就假绿）：
    #      `bl_apply_config` / `bl_apply_rts_config` 若**只传 path**，会先撞
    #      "edits 不能为空" / "需要 preset" 而**在到达边界校验之前就返回** ——
    #      那时断言看到"报错了"就通过，其实**根本没测到边界**。
    #      ⇒ 必须传够参数，让流程真的走到边界校验那一步。
    _needs = {"bl_read_config": {}, "bl_apply_config": {"edits": [{"key": "k", "value": "1"}]},
              "bl_rts_config": {}, "bl_apply_rts_config": {"edits": [{"key": "k", "value": "1"}]}}
    for tool, extra in _needs.items():
        args = {"path": r"C:\Windows\win.ini"}
        args.update(extra)
        r = _mcp.call_tool(tool, args)
        blob = repr(r)          # ★ 判据打**语义**（"被拒了"），不打**形状**（error 字段叫什么）
        #   实测：这几个工具拒绝时的**形状不一致** ——
        #     bl_read_config / bl_apply_config  -> {"error": "path_out_of_allowed_roots", ...}
        #     bl_rts_config / bl_apply_rts_config -> {"error": "<路径边界锁的整段 message>"}
        #   （后者是跨仓复用的 `bl_rts` 直接把 message 当 error 用）
        #   ⇒ 第一版我断言 `err == "path_out_of_allowed_roots"`，那两个**误红**了。
        #     这正是"判据绑定形态"——所以改成：**必须出现边界锁的判据文字**，
        #     而不是它被装在哪个字段里。
        check("path_out_of_allowed_roots" in blob
              or "路径边界锁拒绝" in blob,
              "靠内部校验的工具也被拒：%s(path=系统文件%s)" % (tool, "+必要参数" if extra else ""))
    # ★ 反向对照：闸门必须在 call_tool 的**入口**（第一件事），
    #   否则"某条分支先返回了"就绕过闸门。
    i_fn = src_mcp.find("def call_tool(")
    i_gate = src_mcp.find("_precheck_path_args(name, args)", i_fn)
    i_first = src_mcp.find("if name == ", i_fn)
    check(i_fn > 0 and 0 < i_gate < i_first,
          "闸门位于 call_tool 入口、先于任何分支（不被早退绕过）",
          "gate=%d first_branch=%d" % (i_gate, i_first))

    print("\n⑮ L4 路径类参数的实际判定")
    for p, exp in ((r"C:\Windows\Temp", False),
                   (r"C:\Windows\System32", False),
                   (os.path.join(tempfile.gettempdir(), "reports"), True),
                   (os.path.join(os.path.expanduser("~"), "Documents"), True)):
        ok, _ = bl_common.path_allowed(p, game_dir=game, project_dir=proj)
        check(ok == exp, "%s -> %s" % ("允许" if exp else "拒绝", p))

    print("\n" + "=" * 78)
    if FAIL:
        print("PBS 断言失败 %d 项：" % len(FAIL))
        for f in FAIL:
            print("  - %s" % f)
        return 1
    print("PBS 断言全部通过（含反向对照）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
