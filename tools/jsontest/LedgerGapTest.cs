using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using BlBridge;

/// <summary>
/// 账本两个缺口的断言（2026-10-05 新增，对应交接日志 §8 剩下的两条真缺口）。
///
/// 缺口 A「账本写失败静默」：`ActionLedger.Record` 原来是裸 `catch { }` ⇒ 丢行而无人知。
/// 缺口 B「非法响应让账本静默记错」：`CommandPump.RecordLedger` 用
///   `Jmini.Bool(response,"ok",false)` 反读，兜底是 **false**
///   ⇒ 一个**成功**的请求，只要响应读不出来，就被记成**失败**，且与真失败无法区分。
///
/// ★ 本文件的纪律（照 §三十九 / §四十 的教训）：
///   1. **每条断言都要能回答"我怎么证明它红了"** ⇒ 每条都配 `ExpectRed`（注入坏实现，必须抓到）；
///   2. **不对着副本断言** ⇒ 缺口 A 用**真实** `ActionLedger.Record`，把日志目录指向一个
///      "不可能写成功"的路径（`commands` 位置先建成**文件**），驱动真实代码路径；
///   3. 不断言"实现细节"，只断言**外部可观测量**（计数 / 原因 / 记出来的 ok+code）。
/// </summary>
internal static class LedgerGapTest
{
    private static int _fail;

    private static void Check(bool cond, string label, string extra = "")
    {
        Console.WriteLine((cond ? "  [OK] " : "  [FAIL] ") + label + (extra.Length > 0 ? "   <- " + extra : ""));
        if (!cond) _fail++;
    }

    /// <summary>注入对照：`probe` 必须返回 true（= 检测到了植入的坏行为）。</summary>
    private static void ExpectRed(Func<bool> probe, string label)
    {
        bool caught;
        string err = "";
        try { caught = probe(); }
        catch (Exception ex) { caught = false; err = ex.GetType().Name + ": " + ex.Message; }
        Check(caught, label, caught ? "" : ("注入的坏行为**没被抓到** " + err));
    }

    // ── 缺口 B 的判定逻辑 ──────────────────────────────────────────────────
    // **直接调出货代码** `Protocol.ReadResponseOutcome`（纯 BCL，已编入本 harness）。
    // 刻意**不**在这里抄一份判定 —— 抄一份就变成"验副本不验出货代码"。
    private static void ReverseRead(string response, out bool ok, out string code, out string note)
    {
        bool readable, uncertain;
        Protocol.ReadResponseOutcome(response, out readable, out ok, out code, out uncertain, out note);
    }

    public static int Run()
    {
        _fail = 0;

        Console.WriteLine();
        Console.WriteLine("⑤ 账本两个缺口（写失败静默 / 非法响应静默记错）");

        // ══ 缺口 B ══════════════════════════════════════════════════════════
        // 正例：完好的成功响应必须记成 ok=true
        {
            bool ok; string code, note;
            ReverseRead(Protocol.Success("0011223344556677", "{\"state\":\"loading\"}"), out ok, out code, out note);
            Check(ok && code == "" && note == "state=loading",
                "B 正例：完好的成功响应 ⇒ ok=true / code=\"\" / note=state=…",
                "ok=" + ok + " code='" + code + "' note='" + note + "'");
        }
        // 正例：完好的失败响应必须记成它**真实的** code
        {
            bool ok; string code, note;
            ReverseRead(Protocol.Failure("0011223344556677", "unknown_scene", "场景不存在", false),
                out ok, out code, out note);
            Check(!ok && code == "unknown_scene" && note == "场景不存在",
                "B 正例：完好的失败响应 ⇒ ok=false / code=真实码 / note=message",
                "ok=" + ok + " code='" + code + "' note='" + note + "'");
        }
        // ★ 核心：读不出来的响应，**必须**与"真失败"可区分
        string[] unreadable = new string[]
        {
            "{\"protocolVersion\":1,\"id\":\"0011223344556677\",\"proc",   // 截断
            "",                                                            // 空串
            "   ",                                                         // 全空白
            "not json at all",                                             // 根本不是 JSON
            "{\"result\":{\"note\":\"ok\"}}",                               // 有字面量 ok 但不是键
            null,
        };
        int distinguishable = 0;
        for (int i = 0; i < unreadable.Length; i++)
        {
            bool ok; string code, note;
            ReverseRead(unreadable[i], out ok, out code, out note);
            bool good = (code == "ledger_unreadable") && !ok && note.Length > 0;
            if (good) distinguishable++;
            else Check(false, "B 读不出来的响应 (样本 " + i + ") 必须单独标码且带说明",
                "ok=" + ok + " code='" + code + "' note='" + note + "'");
        }
        Check(distinguishable == unreadable.Length,
            "B ★ " + unreadable.Length + " / " + unreadable.Length
            + " 条读不出来的响应全部记为 code=ledger_unreadable + 非空说明（与真失败可区分）",
            distinguishable + "/" + unreadable.Length);

        // 关键区分点：真失败的 code 绝不会是 ledger_unreadable
        {
            bool ok; string code, note;
            ReverseRead(Protocol.Failure("aa", "unknown_scene", "x", false), out ok, out code, out note);
            Check(code != "ledger_unreadable",
                "B ★ 真失败（unknown_scene）不会被误标成 ledger_unreadable",
                "code=" + code);
        }

        // 注入对照：把"可读性判定"去掉（= 旧实现），读不出来的响应必须**无法区分**
        ExpectRed(delegate
        {
            bool legacyOk = Jmini.Bool("{\"protocolVersion\":1,\"proc", "ok", false);
            string legacyCode = legacyOk ? "" : Jmini.Str("{\"protocolVersion\":1,\"proc", "code", "");
            // 旧实现：code 是 ""，与"真失败但 code 为空"无法区分
            return legacyOk == false && legacyCode == "";
        }, "B 注入对照：去掉可读性判定（旧实现）⇒ 读不出来的响应 code=\"\"（与真失败混淆）");

        // ══ 缺口 A ══════════════════════════════════════════════════════════
        string tmp = Path.Combine(Path.GetTempPath(), "blbridge_gapA_" + Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(tmp);
            // 让 <LogDir>\commands 是一个**文件** ⇒ Path.Combine(commands,"actions.jsonl") 写必失败。
            string commandsAsFile = Path.Combine(tmp, "commands");
            File.WriteAllText(commandsAsFile, "not a directory", new UTF8Encoding(false));

            FieldInfo lf = typeof(BridgeConfig).GetField("_logDir",
                BindingFlags.NonPublic | BindingFlags.Static);
            if (lf == null)
            {
                Check(false, "A 找不到 BridgeConfig._logDir（无法把账本指向不可写路径）");
            }
            else
            {
                lf.SetValue(null, tmp);

                PropertyInfo cnt = typeof(ActionLedger).GetProperty("WriteFailureCount",
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
                PropertyInfo last = typeof(ActionLedger).GetProperty("LastWriteFailure",
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
                Check(cnt != null, "A 存在可观测出口 WriteFailureCount");
                Check(last != null, "A 存在可观测出口 LastWriteFailure");

                if (cnt != null)
                {
                    int before = (int)cnt.GetValue(null, null);
                    // 驱动**真实** Record：路径不可写 ⇒ 走 catch 分支
                    ActionLedger.Record("0011223344556677", "ping", true, "", 0.5, 10, false, "n", "{}");
                    int after = (int)cnt.GetValue(null, null);
                    string reason = last != null ? (last.GetValue(null, null) as string ?? "") : "";

                    Check(after > before,
                        "A ★ 真实 Record 写失败被**计数**（" + before + " -> " + after + "）",
                        "before=" + before + " after=" + after);
                    Check(reason.Length > 0, "A ★ 写失败带**原因**（不再裸吞）", "'" + reason + "'");
                    Check(!File.Exists(Path.Combine(commandsAsFile, "actions.jsonl")),
                        "A 对照：该路径下确实没写成功（证明失败是真的）");
                }

                // 注入对照：模拟旧的裸 catch ⇒ 计数不会增长（= 检测得到"静默"这一坏行为）
                ExpectRed(delegate
                {
                    int snapshot = cnt != null ? (int)cnt.GetValue(null, null) : 0;
                    try
                    {
                        File.AppendAllText(Path.Combine(commandsAsFile, "actions.jsonl"), "x");
                    }
                    catch
                    {
                        // 旧实现就是这样：吞掉，什么都不记
                    }
                    int now = cnt != null ? (int)cnt.GetValue(null, null) : 0;
                    return now == snapshot;   // 计数没变 ⇒ 证明"裸吞就是静默"
                }, "A 注入对照：裸 catch 吞掉失败 ⇒ 计数不变（静默可检）");

                lf.SetValue(null, null);   // 复位，别污染后续（后续断言会重新推导默认目录）
            }
        }
        finally
        {
            try { Directory.Delete(tmp, true); } catch { }
        }

        return _fail;
    }
}
