using System;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;
using BlBridge;

/// <summary>
/// 响应信封形状的离线断言（2026-10-05 新增）。
///
/// 为什么要有它（缺口的取证，不是感觉）：
///   `BridgeProtocol.cs` 的注释里写着三条不变式 ——
///     ① `ok == true` ⇔ `error == null`
///     ② `process` 必填（且含 pid/runToken/… 六个键）
///     ③ `protocolVersion` 必须等于 `Protocol.Version`
///   —— 而**这三条一条测试都没有**：
///     · `tools/jsontest/build_and_run.ps1` 的编译清单里**没有 `BridgeProtocol.cs`**；
///     · `GuardTest.cs` 提到 `Protocol` 的次数 = 1，且那一次是 `Jmini.Int("{...}", "protocolVersion")`
///       的**字面量**，跟真信封无关；
///     · `bl_selftest.py` 的假游戏端自己手写响应 dict，**构造器根本没被调用过**。
///   实测现状是好的（`commands\done\` 里 10896/10896 个真实响应能 `json.loads`）——
///   但**正因为没人守，它坏了也没人知道**。本文件就是那条守卫。
///
/// ★ 方法论（本文件的核心设计，照 §三十九 的教训）：
///   **不能用 `Jmini` 去验 `Jmini` 造的 JSON** —— 那是自证，且这类"自己验自己"的断言
///   恰恰是最容易**恒绿**的（§6.10 那条写了四版才成立的断言就是栽在这里）。
///   所以这里引入一个**独立裁判**：`System.Web.Script.Serialization.JavaScriptSerializer`
///   （.NET 自带的**真** JSON 解析器，与 `Jmini` 无任何共同代码）。
///   判据是「真解析器能解析」+「真解析器的读数与 Jmini 一致」。
///
/// ★ 每条断言都必须能回答"我怎么证明它红了"：
///   所以本文件不写"直接断言成功"，而是先写一个**校验器** `Validate(json)`（返回违规清单），
///   再用两组输入喂它：
///     · 真 `Protocol.Success/Failure` 造的响应  ⇒ 期望 **0 条违规**（正例）
///     · 手工改坏的信封（缺 process / ok 与 error 矛盾 / 版本错 …）⇒ 期望 **逐条被抓**（注入对照组）
///   对照组是**恒绿检测器**：如果校验器写错了（恒返回空清单），第二组会全红。
/// </summary>
internal static class EnvelopeTest
{
    private static int _fail;

    private static void Check(bool cond, string label, string extra = "")
    {
        Console.WriteLine((cond ? "  [OK] " : "  [FAIL] ") + label + (extra.Length > 0 ? "   <- " + extra : ""));
        if (!cond) _fail++;
    }

    // ── 独立裁判：.NET 自带的真 JSON 解析器（不是 Jmini） ─────────────────────
    private static JavaScriptSerializer _json;

    private static object Parse(string text)
    {
        if (_json == null)
        {
            _json = new JavaScriptSerializer();
            // 长响应（如 list_ui 的 303 行场景表）远超默认 2 MB 上限
            _json.MaxJsonLength = int.MaxValue;
        }
        return _json.DeserializeObject(text);
    }

    /// <summary>真解析器读顶层键；读不到或类型不符返回 null。</summary>
    private static object Prop(string text, string key)
    {
        Dictionary<string, object> d = Parse(text) as Dictionary<string, object>;
        if (d == null) return null;
        object v;
        return d.TryGetValue(key, out v) ? v : null;
    }

    /// <summary>
    /// 信封校验器：返回违规清单（空 = 合法）。**纯判定，不抛**。
    /// 判据来源就是 `BridgeProtocol.cs` 顶部注释里的那三条不变式，加上"消费者实际要用的键"。
    /// </summary>
    private static List<string> Validate(string json)
    {
        List<string> v = new List<string>();
        if (string.IsNullOrEmpty(json)) { v.Add("响应为空"); return v; }

        object parsed;
        try { parsed = Parse(json); }
        catch (Exception ex) { v.Add("真解析器无法解析: " + ex.GetType().Name); return v; }

        Dictionary<string, object> d = parsed as Dictionary<string, object>;
        if (d == null) { v.Add("顶层不是对象: " + (parsed == null ? "null" : parsed.GetType().Name)); return v; }

        // ③ 协议版本
        object ver;
        if (!d.TryGetValue("protocolVersion", out ver)) v.Add("缺 protocolVersion");
        else if (!(ver is int) || (int)ver != Protocol.Version)
            v.Add("protocolVersion 不是 " + Protocol.Version + "（实为 " + ver + "）");

        // id 必须存在且非空
        object id;
        if (!d.TryGetValue("id", out id) || !(id is string) || ((string)id).Length == 0) v.Add("缺 id 或 id 为空");

        // ① ok 必填且必须是布尔；ok ⇔ error==null
        object ok, err;
        bool hasOk = d.TryGetValue("ok", out ok);
        if (!hasOk) v.Add("缺 ok");
        else if (!(ok is bool)) v.Add("ok 不是布尔（实为 " + (ok == null ? "null" : ok.GetType().Name) + "）");
        else
        {
            d.TryGetValue("error", out err);
            bool okv = (bool)ok;
            if (okv && err != null) v.Add("ok=true 但 error 非 null");
            if (!okv && err == null) v.Add("ok=false 但 error 为 null");
            if (okv && d.ContainsKey("error") == false) v.Add("缺 error 键（必须显式为 null）");
        }

        // 失败时 error 必须带 code / message / outcomeUncertain
        d.TryGetValue("error", out err);
        Dictionary<string, object> ed = err as Dictionary<string, object>;
        if (ed != null)
        {
            object code, msg, unc;
            if (!ed.TryGetValue("code", out code) || !(code is string) || ((string)code).Length == 0)
                v.Add("error 缺 code 或为空");
            if (!ed.TryGetValue("message", out msg) || !(msg is string))
                v.Add("error 缺 message");
            if (!ed.TryGetValue("outcomeUncertain", out unc) || !(unc is bool))
                v.Add("error 缺 outcomeUncertain（必须是布尔）");
        }

        // ② process 必填 + 六个键
        object proc;
        if (!d.TryGetValue("process", out proc)) v.Add("缺 process");
        else
        {
            Dictionary<string, object> pd = proc as Dictionary<string, object>;
            if (pd == null) v.Add("process 不是对象");
            else
            {
                foreach (string need in new string[]
                         { "pid", "role", "runToken", "processStartedUtc", "assemblyVersion", "mvid", "loadedSha256" })
                {
                    if (!pd.ContainsKey(need)) v.Add("process 缺 " + need);
                }
                object pid;
                if (pd.TryGetValue("pid", out pid) && (!(pid is int) || (int)pid <= 0))
                    v.Add("process.pid 不是正整数");
            }
        }

        // result 键必须存在（成功时是内容、失败时显式 null）
        if (!d.ContainsKey("result")) v.Add("缺 result 键");
        return v;
    }

    private static string Join(List<string> v)
    {
        return v.Count == 0 ? "(无)" : string.Join(" | ", v.ToArray());
    }

    /// <summary>正例：`Validate` 对合法信封必须返回 0 条违规。</summary>
    private static void ExpectValid(string json, string label)
    {
        List<string> v = Validate(json);
        Check(v.Count == 0, label, Join(v));
    }

    /// <summary>注入对照组：`Validate` 必须**至少**抓到这个特定违规（证明它不是恒绿）。</summary>
    private static void ExpectViolation(string json, string label, string mustMention)
    {
        List<string> v = Validate(json);
        bool hit = false;
        for (int i = 0; i < v.Count; i++)
        {
            if (v[i].IndexOf(mustMention, StringComparison.Ordinal) >= 0) { hit = true; break; }
        }
        Check(v.Count > 0 && hit, label, "违规=" + Join(v) + " 期望点名「" + mustMention + "」");
    }

    public static int Run()
    {
        _fail = 0;

        Console.WriteLine();
        Console.WriteLine("④ BridgeProtocol：响应信封形状（真解析器当独立裁判，不用 Jmini 自证）");

        // ── 正例：真构造器造出来的信封必须合法 ────────────────────────────────
        ExpectValid(Protocol.Success("0011223344556677", "{\"a\":1}"), "正例 1：Protocol.Success 的信封合法");
        ExpectValid(Protocol.Failure("0011223344556677", "unknown_scene", "场景不存在", false),
            "正例 2：Protocol.Failure 的信封合法");
        ExpectValid(Protocol.Success("0011223344556677", "null"), "正例 3：result 为 null 的成功信封合法");
        ExpectValid(Protocol.Failure("0011223344556677", "boom", "", true), "正例 4：空 message 的失败信封合法");

        // 语义正确性（不只是"长得对"）
        object okS = Prop(Protocol.Success("aa", "{}"), "ok");
        object errS = Prop(Protocol.Success("aa", "{}"), "error");
        object okF = Prop(Protocol.Failure("bb", "c", "m", false), "ok");
        Check(okS is bool && (bool)okS, "成功信封 ok=true", "ok=" + okS);
        Check(errS == null, "成功信封 error=null（真解析器读出来就是 null）", "error=" + (errS == null ? "null" : errS.GetType().Name));
        Check(okF is bool && !(bool)okF, "失败信封 ok=false", "ok=" + okF);

        object rid = Prop(Protocol.Success("deadbeefdeadbeef", "{}"), "id");
        Check(rid as string == "deadbeefdeadbeef", "id 原样回显", "id=" + rid);
        object pv = Prop(Protocol.Success("deadbeefdeadbeef", "{}"), "protocolVersion");
        Check(pv is int && (int)pv == Protocol.Version, "protocolVersion = Protocol.Version", "pv=" + pv);

        // ── 敌意样本表：内容再脏，信封也必须仍然合法 ──────────────────────────
        // 这些字符串会被 Jw.Esc 转义后放进 message；转义写错 ⇒ 真解析器当场炸。
        string[] hostile = new string[]
        {
            "引号 \" 半个转义",
            "反斜杠 \\ 与 \\\" 混合",
            "换行\n回车\r制表\t退格\b换页\f",
            "中文与表情 🙂 混合",
            "\"code\":\"FAKE\"",             // 试图劫持账本反读的 code
            "\"message\":\"FAKE2\"",
            "{\"ok\":true}",                 // 试图翻转 ok
            "outcomeUncertain\":true",       // 试图翻转 uncertain
            "{\"error\":null}",              // 试图伪造成功形状
            "\\u0000 控制字符与 \\uD83D 孤立代理项",
            new string('长', 5000),          // 超长
            "",
            "   ",
            "null",
            "{",
            "]",
            "\"",
            "\\",
        };
        int hostileOk = 0;
        for (int i = 0; i < hostile.Length; i++)
        {
            string r = Protocol.Failure("0011223344556677", "real_code", hostile[i], false);
            List<string> v = Validate(r);
            if (v.Count == 0) hostileOk++;
            else Check(false, "敌意样本 " + i + " 仍是合法信封", Join(v));
        }
        Check(hostileOk == hostile.Length,
            "敌意样本表 " + hostile.Length + " / " + hostile.Length + " 全部仍是合法信封（真解析器能解析）",
            hostileOk + "/" + hostile.Length);

        // ── ★ 独立裁判 vs Jmini：账本反读的**正确性**判据 ────────────────────────
        // `CommandPump.RecordLedger` 用 Jmini 从响应里反读 ok/code/message 再入账。
        // 若 Jmini 与真解析器读数不一致，账本就会**静默记错**（这正是交接 §8 记的第二个缺口）。
        // 这里是那条缺口的**守卫**：两个独立实现必须对同一份文本给出同一个答案。
        int agree = 0, total = 0;
        for (int i = 0; i < hostile.Length; i++)
        {
            for (int kind = 0; kind < 2; kind++)
            {
                string r = kind == 0
                    ? Protocol.Failure("0011223344556677", "real_code", hostile[i], false)
                    : Protocol.Success("0011223344556677", "{\"echo\":" + Protocol.Q(hostile[i]) + "}");
                total++;

                object realOk = Prop(r, "ok");
                bool jminiOk = Jmini.Bool(r, "ok", false);
                bool okAgree = (realOk is bool) && ((bool)realOk == jminiOk);

                object realCode = null;
                Dictionary<string, object> ed = Prop(r, "error") as Dictionary<string, object>;
                if (ed != null) { object cc; ed.TryGetValue("code", out cc); realCode = cc; }
                string jminiCode = Jmini.Str(r, "code", "");
                bool codeAgree = realCode == null || ((string)realCode) == jminiCode;

                if (okAgree && codeAgree) agree++;
                else Check(false, "Jmini 与真解析器读数一致（样本 " + i + "/kind" + kind + "）",
                    "ok real=" + realOk + " jmini=" + jminiOk + " ; code real=" + realCode + " jmini=" + jminiCode);
            }
        }
        Check(agree == total,
            "Jmini 与真解析器的 ok/code 读数在 " + total + " 个样本上完全一致（账本反读的守卫）",
            agree + "/" + total);

        // ── ★★ 注入对照组：证明上面的校验器**会红**（否则等于零守护） ──────────
        Console.WriteLine("   —— 注入对照组（每条都改坏一处，校验器必须点名）——");
        string good = Protocol.Success("0011223344556677", "{\"a\":1}");
        string goodFail = Protocol.Failure("0011223344556677", "boom", "msg", false);

        ExpectViolation(good.Replace(",\"process\":{", ",\"processX\":{"),
            "注入 1：删掉 process 键 ⇒ 必须点名「缺 process」", "缺 process");
        ExpectViolation(good.Replace("\"ok\":true", "\"ok\":false"),
            "注入 2：成功信封把 ok 改成 false ⇒ 必须点名「error 为 null」", "error 为 null");
        ExpectViolation(good.Replace("\"protocolVersion\":1", "\"protocolVersion\":2"),
            "注入 3：协议版本改错 ⇒ 必须点名「protocolVersion」", "protocolVersion");
        ExpectViolation(good.Replace("\"pid\":", "\"pidX\":"),
            "注入 4：process 里删掉 pid ⇒ 必须点名「process 缺 pid」", "process 缺 pid");
        ExpectViolation(good.Replace("\"loadedSha256\":", "\"loadedSha256X\":"),
            "注入 5：process 里删掉 loadedSha256 ⇒ 必须点名", "process 缺 loadedSha256");
        ExpectViolation(goodFail.Replace("\"outcomeUncertain\":false", "\"outcomeUncertainX\":false"),
            "注入 6：error 里删掉 outcomeUncertain ⇒ 必须点名", "outcomeUncertain");
        ExpectViolation(goodFail.Replace("\"code\":\"boom\"", "\"code\":\"\""),
            "注入 7：error.code 改成空串 ⇒ 必须点名「缺 code 或为空」", "缺 code 或为空");
        ExpectViolation("{\"protocolVersion\":1,\"ok\":true", "注入 8：截断的半截 JSON ⇒ 必须点名「无法解析」", "无法解析");
        ExpectViolation("", "注入 9：空串 ⇒ 必须点名「响应为空」", "响应为空");
        ExpectViolation("[]", "注入 10：顶层是数组 ⇒ 必须点名「顶层不是对象」", "顶层不是对象");
        ExpectViolation(Protocol.Success("aa", "{}").Replace(",\"result\":{}", ""),
            "注入 11：删掉 result 键 ⇒ 必须点名「缺 result 键」", "缺 result 键");

        // 反向对照：注入对照组用的样本，改坏**之前**必须是绿的 —— 证明断言不是恒红
        ExpectValid(good, "反向对照：注入前的那份必须合法（断言不是恒红）");
        ExpectValid(goodFail, "反向对照：注入前的那份失败信封必须合法（断言不是恒红）");

        return _fail;
    }
}
