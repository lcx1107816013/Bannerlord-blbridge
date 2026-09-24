using System;
using System.IO;
using BlBridge;

/// <summary>
/// BlBridge 离线单测：Jmini（JSON 读取器）与 RequestGuard（请求校验闸门）。
/// 这两个类只依赖 System，所以能用 csc 单独编译后直接跑，不需要启动游戏。
/// 编译：tools\jsontest\build_and_run.ps1
/// </summary>
internal static class GuardTest
{
    private static int _fail;

    private static void Check(bool cond, string label, string extra = "")
    {
        Console.WriteLine((cond ? "  [OK] " : "  [FAIL] ") + label + (extra.Length > 0 ? "   <- " + extra : ""));
        if (!cond) _fail++;
    }

    private static int Main()
    {
        try { Console.OutputEncoding = System.Text.Encoding.UTF8; }
        catch { }

        Console.WriteLine("① Jmini：键查找必须跳过字符串值（旧实现在这里会读错）");
        string hostile = "{\"method\":\"start_battle\",\"parameters\":{\"note\":\"\\\"method\\\":\\\"evil\\\"\"}}";
        Check(Jmini.Str(hostile, "method", "") == "start_battle",
            "字符串值里的 \"method\" 不能劫持键查找", Jmini.Str(hostile, "method", ""));

        string nested = "{\"method\":\"fast_forward\",\"parameters\":{\"enabled\":true,\"scene\":\"x\"}}";
        Check(Jmini.Bool(nested, "enabled", false), "嵌套（parameters 内）布尔仍可读");
        Check(Jmini.Str(nested, "scene", "") == "x", "嵌套字符串仍可读");
        Check(Jmini.Str(nested, "method", "") == "fast_forward", "顶层键仍可读");

        string idHostile = "{\"id\":\"abc123\",\"note\":\"\\\"id\\\":\\\"evil\\\"\"}";
        Check(Jmini.Str(idHostile, "id", "") == "abc123", "id 不被同名字符串值劫持", Jmini.Str(idHostile, "id", ""));

        Check(Jmini.Int("{\"protocolVersion\":1}", "protocolVersion", 0) == 1, "整数解析");
        Check(Math.Abs(Jmini.Num("{\"a\":-12.5e2}", "a", 0) + 1250.0) < 1e-9, "浮点/科学计数解析");
        Check(Jmini.Str("{\"s\":\"\\u4e2d\\u6587\"}", "s", "") == "中文", "\\uXXXX 转义");
        Check(Jmini.Str("{\"s\":\"esc\\\"q\\\\b\"}", "s", "") == "esc\"q\\b", "转义引号与反斜杠");
        Check(Jmini.Str("{\"s\":\"\\uD83D\\uDE00\"}", "s", "") == "\U0001F600", "合法代理对");
        string lone = Jmini.Str("{\"s\":\"\\uD83D x\"}", "s", "");
        Check(lone == "\uFFFD x", "孤立代理项降级为 U+FFFD", ((int)lone[0]).ToString("X4"));
        Check(Jmini.Str("{\"a\":1}", "missing", "d") == "d", "缺键回退默认值");
        Check(!Jmini.Has("{\"a\":1}", "b"), "Has 对不存在的键返回 false");

        Console.WriteLine();
        Console.WriteLine("② RequestGuard：id 白名单");
        Check(RequestGuard.IsValidId("0123456789abcdef"), "16 位小写十六进制合法");
        Check(RequestGuard.IsValidId("ABCDEF0123456789ABCDEF0123456789"), "32 位大写十六进制合法");
        Check(!RequestGuard.IsValidId("..\\..\\evil"), "路径穿越 id 被拒");
        Check(!RequestGuard.IsValidId("short"), "过短 id 被拒");
        Check(!RequestGuard.IsValidId("0123456789abcdeZ"), "非十六进制字符被拒");
        Check(!RequestGuard.IsValidId(null), "null 被拒");
        Check(!RequestGuard.IsValidId(""), "空串被拒");

        Console.WriteLine();
        Console.WriteLine("③ RequestGuard：文件名安全化");
        Check(RequestGuard.SafeFileName("..\\..\\evil") == "evil", "去掉路径分隔符", RequestGuard.SafeFileName("..\\..\\evil"));
        Check(RequestGuard.SafeFileName("") == RequestGuard.FallbackId, "空名回退为固定 id");
        Check(RequestGuard.SafeFileName("a/b:c*d") == "abcd", "非法字符被丢弃", RequestGuard.SafeFileName("a/b:c*d"));
        Check(RequestGuard.SafeFileName("a..b") == "ab", "点号被丢弃（无法构造 .. 段）", RequestGuard.SafeFileName("a..b"));
        Check(RequestGuard.SafeFileName(new string('x', 200)).Length == 64, "长度截断到 64");

        Console.WriteLine();
        Console.WriteLine("④ RequestGuard：过期判定（幽灵战斗防线）");
        DateTime now = new DateTime(2026, 9, 23, 12, 0, 0, DateTimeKind.Utc);
        Check(!RequestGuard.IsExpired("2026-09-23T11:59:30Z", now, 120), "30 秒前的请求不过期");
        Check(RequestGuard.IsExpired("2026-09-23T11:57:00Z", now, 120), "3 分钟前的请求过期");
        Check(!RequestGuard.IsExpired("2026-09-23T12:05:00Z", now, 120), "未来时间（时钟偏差）不过期");
        Check(!RequestGuard.IsExpired(null, now, 120), "缺 issuedUtc 不过期（向后兼容）");
        Check(!RequestGuard.IsExpired("垃圾时间戳", now, 120), "解析失败不过期（不误杀）");
        Check(!RequestGuard.IsExpired("2026-09-23T11:00:00Z", now, 0), "阈值 0 = 关闭检查");
        Check(RequestGuard.IsExpired("2026-09-23T11:57:00+00:00", now, 120), "带偏移量的 ISO 时间可解析");
        Check(RequestGuard.IsExpired("2026-09-23T11:57:00.500Z", now, 120), "带毫秒的时间可解析");

        Console.WriteLine();
        Console.WriteLine("⑤ Jw 浮点：round-trip 与 NaN 可见化");
        Check(Jw.N(1.5f) == "1.5", "普通浮点", Jw.N(1.5f));
        Check(Jw.N(0.1f) == "0.1", "0.1f 不丢精度", Jw.N(0.1f));
        float big = float.Parse(Jw.N(123456.78f), System.Globalization.CultureInfo.InvariantCulture);
        Check(big == 123456.78f, "大数 round-trip 无损", Jw.N(123456.78f) + " -> " + big);
        float parsed = float.Parse(Jw.N(0.3333333f), System.Globalization.CultureInfo.InvariantCulture);
        Check(parsed == 0.3333333f, "round-trip 无损（旧版 0.### 会丢尾数）", Jw.N(0.3333333f));
        long before = Jw.NanCount;
        Check(Jw.N(float.NaN) == "null", "NaN → null（旧版静默变 0）");
        Check(Jw.N(float.PositiveInfinity) == "null", "Infinity → null");
        Check(Jw.NanCount == before + 2, "NaN/Inf 被计数", Jw.NanCount.ToString());
        Check(Jw.N(42) == "42", "整数重载不受影响", Jw.N(42));

        Console.WriteLine();
        Console.WriteLine("⑥ ProbePolicy：样本有效性判定（决定这场能不能进 A/B 对比）");
        Check(ProbePolicy.SampleVerdict(true, 600, 60.0, 0) == "ok", "正常战斗 → ok");
        Check(ProbePolicy.SampleVerdict(true, 600, 60.0, 4000) == "ok", "4 秒卡顿仍在容忍内 → ok");
        Check(ProbePolicy.SampleVerdict(true, 10, 60.0, 0) == "suspect", "帧数不足（根本没打起来）→ suspect");
        Check(ProbePolicy.SampleVerdict(true, 600, 5.0, 0) == "suspect", "帧率过低 → suspect");
        Check(ProbePolicy.SampleVerdict(true, 600, 60.0, 6000) == "suspect", "单次卡顿超 5 秒 → suspect");
        Check(ProbePolicy.SampleVerdict(false, 0, 0, 0) == "no_probe", "没有探针数据 → no_probe");
        Check(ProbePolicy.IsAdvancing(1.9), "1.9 秒未推进仍算推进中");
        Check(!ProbePolicy.IsAdvancing(2.1), "2.1 秒未推进算停住");
        Check(ProbePolicy.RealtimeVerdict(false, false) == "no_mission", "无任务 → no_mission");
        Check(ProbePolicy.RealtimeVerdict(true, false) == "stalled", "有任务但停住 → stalled");
        Check(ProbePolicy.RealtimeVerdict(true, true) == "advancing", "有任务且在推进 → advancing");

        Console.WriteLine();
        Console.WriteLine("⑦ BuildInfo：构建身份（用来识别「进程里跑的是旧 DLL」）");
        Check(!string.IsNullOrEmpty(BuildInfo.LoadedSha256), "加载时哈希非空", BuildInfo.LoadedSha256Short);
        Check(BuildInfo.LoadedSha256.Length == 64, "哈希为 64 位十六进制", BuildInfo.LoadedSha256.Length.ToString());
        Check(BuildInfo.Mvid.Length == 32, "MVID 为 32 位十六进制", BuildInfo.Mvid);
        Check(BuildInfo.CurrentFileSha256() == BuildInfo.HashFile(typeof(GuardTest).Assembly.Location),
            "当前文件哈希与独立重算一致");
        Check(BuildInfo.Json().IndexOf("\"fileChangedSinceLoad\":false", System.StringComparison.Ordinal) >= 0,
            "未被改动时 fileChangedSinceLoad=false", BuildInfo.Json());

        Console.WriteLine();
        Console.WriteLine("⑧ BridgeConfigFile：外部配置（加载即校验，坏值不拖累好值）");
        string cfgDir = Path.Combine(Path.GetTempPath(), "blbridge_test_" + Guid.NewGuid().ToString("N").Substring(0, 8));
        Directory.CreateDirectory(cfgDir);
        string cfgFile = Path.Combine(cfgDir, "blbridge_game.json");
        bool savedEnabled = BridgeConfig.Enabled;
        float savedInterval = BridgeConfig.SampleIntervalSeconds;
        int savedAge = BridgeConfig.MaxRequestAgeSeconds;

        string errs = BridgeConfigFile.Apply(cfgFile);
        Check(errs == "" && !BridgeConfigFile.Loaded, "文件不存在：不改任何值、无错误", errs);

        File.WriteAllText(cfgFile, "{\"sampleIntervalSeconds\":3,\"maxRequestAgeSeconds\":300,\"flushEveryLine\":true}");
        errs = BridgeConfigFile.Apply(cfgFile);
        Check(BridgeConfigFile.Loaded && errs == "", "合法配置被应用且无错误", errs);
        Check(Math.Abs(BridgeConfig.SampleIntervalSeconds - 3f) < 0.001f, "sampleIntervalSeconds 生效",
            BridgeConfig.SampleIntervalSeconds.ToString());
        Check(BridgeConfig.MaxRequestAgeSeconds == 300, "maxRequestAgeSeconds 生效",
            BridgeConfig.MaxRequestAgeSeconds.ToString());

        BridgeConfig.SampleIntervalSeconds = savedInterval;
        BridgeConfig.MaxRequestAgeSeconds = savedAge;
        File.WriteAllText(cfgFile, "{\"sampleIntervalSeconds\":9999,\"maxRequestAgeSeconds\":10}");
        errs = BridgeConfigFile.Apply(cfgFile);
        Check(errs.IndexOf("sampleIntervalSeconds", StringComparison.Ordinal) >= 0, "越界项被拒并给出原因", errs);
        Check(Math.Abs(BridgeConfig.SampleIntervalSeconds - savedInterval) < 0.001f, "越界值没有生效");
        Check(BridgeConfig.MaxRequestAgeSeconds == 10, "同一文件里的合法键仍然生效");

        File.WriteAllText(cfgFile, "{\"sampleIntervalSeconds\":\"abc\"}");
        errs = BridgeConfigFile.Apply(cfgFile);
        Check(errs.IndexOf("sampleIntervalSeconds", StringComparison.Ordinal) >= 0, "非数字被拒并说明", errs);

        File.WriteAllText(cfgFile, "{\"enabled\":false,\"pad\":\"" + new string('x', 70000) + "\"}");
        errs = BridgeConfigFile.Apply(cfgFile);
        Check(errs.IndexOf("64 KiB", StringComparison.Ordinal) >= 0, "超过 64 KiB 整份忽略", errs);
        Check(BridgeConfig.Enabled, "超大文件没有把 enabled 改掉");

        File.WriteAllText(cfgFile, "{\"sampleIntervalSeconds\":5}");
        BridgeConfigFile.Apply(cfgFile);
        string eff = BridgeConfigFile.EffectiveJson();
        Check(eff.IndexOf("\"loaded\":true", StringComparison.Ordinal) >= 0, "来历块标记 loaded=true", eff);
        Check(eff.IndexOf("\"errors\":\"\"", StringComparison.Ordinal) >= 0, "无错误时 errors 为空串");

        BridgeConfig.Enabled = savedEnabled;
        BridgeConfig.SampleIntervalSeconds = savedInterval;
        BridgeConfig.MaxRequestAgeSeconds = savedAge;
        try { Directory.Delete(cfgDir, true); } catch { }

        Console.WriteLine();
        Console.WriteLine("⑨ SquadSpec：多兵种/战术组 DSL（非法必须抛，绝不静默回落）");
        var q1 = SquadSpec.Parse("imperial_legionary:10:Infantry:hold");
        Check(q1.Count == 1 && q1[0].Troop == "imperial_legionary" && q1[0].Count == 10
              && q1[0].Formation == "Infantry" && q1[0].Movement == "hold",
              "四字段组", q1.Count > 0 ? q1[0].Formation + "/" + q1[0].Movement : "-");
        var q2 = SquadSpec.Parse("khuzait_khans_guard:5");
        Check(q2.Count == 1 && q2[0].Formation == null && q2[0].Movement == null,
              "两字段：两个可选字段为 null（缺省交给引擎/调用方）");
        var q3 = SquadSpec.Parse("a:1|b:2:HorseArcher");
        Check(q3.Count == 2 && q3[1].Formation == "HorseArcher" && q3[0].Formation == null,
              "多组用 | 分隔、各组独立");
        var q4 = SquadSpec.Parse("a:1:infantry:HOLD");
        Check(q4[0].Formation == "Infantry" && q4[0].Movement == "hold",
              "大小写不敏感 → formation 回规范名、movement 归小写");
        Check(SquadSpec.Parse("").Count == 0 && SquadSpec.Parse(null).Count == 0,
              "空串 / null ⇒ 空列表");
        string[] badDsl = new string[] { "a", "a:1:Infantry:hold:extra", "a:0", "a:abc",
                                         "a:1:Infantryy", "a:1:Infantry:jump", "a:1||b:2", ":1" };
        foreach (string bad in badDsl)
        {
            bool threw = false;
            try { SquadSpec.Parse(bad); }
            catch (ArgumentException) { threw = true; }
            Check(threw, "非法组串抛 ArgumentException: " + bad);
        }

        Console.WriteLine();
        Console.WriteLine(_fail == 0 ? "结果: 全部通过" : "结果: 失败 " + _fail + " 项");
        return _fail == 0 ? 0 : 1;
    }
}
