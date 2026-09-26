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
        var q1 = SquadSpec.Parse("imperial_legionary:10:Infantry:stop");
        Check(q1.Count == 1 && q1[0].Troop == "imperial_legionary" && q1[0].Count == 10
              && q1[0].Formation == "Infantry" && q1[0].Movement == "stop",
              "四字段组", q1.Count > 0 ? q1[0].Formation + "/" + q1[0].Movement : "-");
        var q2 = SquadSpec.Parse("khuzait_khans_guard:5");
        Check(q2.Count == 1 && q2[0].Formation == null && q2[0].Movement == null,
              "两字段：两个可选字段为 null（缺省交给引擎/调用方）");
        var q3 = SquadSpec.Parse("a:1|b:2:HorseArcher");
        Check(q3.Count == 2 && q3[1].Formation == "HorseArcher" && q3[0].Formation == null,
              "多组用 | 分隔、各组独立");
        var q4 = SquadSpec.Parse("a:1:infantry:STOP");
        Check(q4[0].Formation == "Infantry" && q4[0].Movement == "stop",
              "大小写不敏感 → formation 回规范名、movement 归小写");
        Check(SquadSpec.Parse("").Count == 0 && SquadSpec.Parse(null).Count == 0,
              "空串 / null ⇒ 空列表");
        string[] badDsl = new string[] { "a", "a:1:Infantry:stop:extra", "a:0", "a:abc",
                                         "a:1:Infantryy", "a:1:Infantry:jump", "a:1||b:2", ":1" };
        foreach (string bad in badDsl)
        {
            bool threw = false;
            try { SquadSpec.Parse(bad); }
            catch (ArgumentException) { threw = true; }
            Check(threw, "非法组串抛 ArgumentException: " + bad);
        }

        // 移除 hold（2026-09-25 用户按编程规则批准）：写 hold 必须在解析期抛错并提示改用 stop。
        bool holdThrew = false;
        string holdMsg = "";
        try { SquadSpec.Parse("a:1:Infantry:hold"); }
        catch (ArgumentException ex) { holdThrew = true; holdMsg = ex.Message; }
        Check(holdThrew && holdMsg.IndexOf("已移除", StringComparison.Ordinal) >= 0,
              "hold 已移除：必须抛 ArgumentException 且提示改用 stop", holdMsg);

        // ── 手动令优先标记（v0.8.30/0.8.31，SquadSpec.ManualKind）─────────────────
        // 为什么值得有断言：组路径每 0.5s 重申movement，而这个三态决定了"重申什么"。
        // 最要命的一条是 **ManualNone == 0**：字段默认值就是它 ⇒ 开战 DSL 那条老路径
        // （按名字重申）行为**必须**不变（GC2）；万一有人把常量顺序改了，默认值就会变成
        // "重申某个点 (0,0,0)"，所有按组开的战都会莫名其妙往原点走。
        Check(SquadSpec.ManualNone == 0,
              "ManualNone 必须是 0（= 字段默认值 ⇒ 开战 DSL 那条路行为不变）",
              SquadSpec.ManualNone.ToString());
        Check(SquadSpec.ManualPosition != SquadSpec.ManualNone
              && SquadSpec.ManualChargeTarget != SquadSpec.ManualNone
              && SquadSpec.ManualAttackAgent != SquadSpec.ManualNone
              && SquadSpec.ManualPosition != SquadSpec.ManualChargeTarget
              && SquadSpec.ManualPosition != SquadSpec.ManualAttackAgent
              && SquadSpec.ManualChargeTarget != SquadSpec.ManualAttackAgent,
              "手动令四态两两不等（单值枚举式，不是一个编队能同时有两种手动令）");
        SquadSpec fresh = new SquadSpec();
        Check(fresh.ManualKind == SquadSpec.ManualNone && fresh.TargetFormationIndex == -1
              && fresh.TargetAgentIndex == -1,
              "新建 spec 默认 = 按名字重申、无目标编队、无目标单位",
              fresh.ManualKind + "/" + fresh.TargetFormationIndex + "/" + fresh.TargetAgentIndex);

        // ── v0.8.32：开战 DSL `formation` 死字段的一致性比对（SquadSpec.CollectFormationMismatches）──
        // 存在的唯一理由：`formation` **不决定编队**（引擎按 troop.GetFormationClass() 分），
        // 给弓手写 Infantry 会静默进 Ranged。它只依赖 BCL ⇒ 能在这里被锁住。
        var mmSpecs = SquadSpec.Parse("battanian_fian_champion:10:Infantry:stop");
        var mm = SquadSpec.CollectFormationMismatches(mmSpecs, new string[] { "Ranged" });
        Check(mm.Count == 1 && mm[0].Contains("battanian_fian_champion"),
              "formation 写 Infantry 而实际是 Ranged ⇒ 报 1 条（点名兵种）", mm.Count.ToString());
        Check(SquadSpec.CollectFormationMismatches(mmSpecs, new string[] { "Infantry" }).Count == 0,
              "formation 与实际同族 ⇒ 0 条（不打扰）");
        Check(SquadSpec.FormationFamily("HeavyInfantry") == "Infantry"
              && SquadSpec.FormationFamily("Skirmisher") == "Ranged"
              && SquadSpec.FormationFamily("LightCavalry") == "HorseArcher"
              && SquadSpec.FormationFamily("HeavyCavalry") == "Cavalry",
              "formation 家族折叠口径同引擎 FallbackClass："
              + "HeavyInfantry→Infantry / Skirmisher→Ranged / LightCavalry→HorseArcher / HeavyCavalry→Cavalry");
        Check(SquadSpec.CollectFormationMismatches(
                  SquadSpec.Parse("a:1:HeavyInfantry"), new string[] { "Infantry" }).Count == 0,
              "同族（HeavyInfantry vs Infantry）⇒ 不报（别把别名当不一致）");
        Check(SquadSpec.CollectFormationMismatches(
                  SquadSpec.Parse("a:1"), new string[] { "Ranged" }).Count == 0,
              "没写 formation（null）⇒ 不报（无意声明，不该打扰）");
        Check(SquadSpec.CollectFormationMismatches(mmSpecs, new string[] { null }).Count == 0
              && SquadSpec.CollectFormationMismatches(mmSpecs, new string[] { "" }).Count == 0,
              "实际编队取不到（null / 空）⇒ 跳过比对（不猜）");
        Check(SquadSpec.FormationFamily("Infantrys") == null && SquadSpec.FormationFamily(null) == null,
              "formation 家族：未知名字 / null 回 null（跳过比对，不猜）");
        Check(SquadSpec.CollectFormationMismatches(null, new string[] { "Ranged" }).Count == 0
              && SquadSpec.CollectFormationMismatches(mmSpecs, null).Count == 0,
              "空入参（null specs / null actual）⇒ 0 条，绝不抛");

        // ── 改令通道的名字表与校验器（v0.8.23，OrderSpec）──────────────────────
        // 这些校验必须在**碰 `MovementOrder` 之前**跑完（碰早了会抛 TypeInitializationException
        // 并把该类型永久标记为不可用），所以它们属于安全边界，必须有对照断言；
        // 同时与开战 DSL 的名字表（SquadSpec）**同集合**锁住，免得"开战能写、中途改不了"。
        int fi;
        Check(OrderSpec.TryFormationIndex("Infantry", out fi) && fi == 0, "formation 名 → 下标：Infantry=0");
        Check(OrderSpec.TryFormationIndex("skirmisher", out fi) && fi == 4,
              "formation 名大小写不敏感：skirmisher=4");
        Check(OrderSpec.TryFormationIndex("2", out fi) && fi == 2, "formation 下标字符串也接受：2 → 2");
        Check(!OrderSpec.TryFormationIndex("HeavyInfantry", out fi),
              "别名不收（HeavyInfantry 与 Infantry 同值，收两套名字会漂移）");
        Check(!OrderSpec.TryFormationIndex("Infantrys", out fi), "拼错必须拒，不回落默认编队");
        Check(!OrderSpec.TryFormationIndex("-1", out fi), "越界下标（-1）必须拒");
        Check(!OrderSpec.TryFormationIndex("5", out fi), "越界下标（5）必须拒");
        Check(!OrderSpec.TryFormationIndex("", out fi), "空串必须拒");

        Check(OrderSpec.IsMovement("stop") && OrderSpec.IsMovement("Charge"),
              "movement 白名单：大小写不敏感");
        Check(!OrderSpec.IsMovement("hold"), "movement 白名单：hold 已移除（必须拒）");
        Check(!OrderSpec.IsMovement("jump") && !OrderSpec.IsMovement(""), "movement 白名单：怪值/空串必须拒");

        Check(OrderSpec.MovementNames.Length == SquadSpec.Movements.Length,
              "改令与开战 DSL 的 movement 集合长度一致", OrderSpec.MovementNames.Length.ToString());
        for (int qi = 0; qi < OrderSpec.MovementNames.Length; qi++)
        {
            Check(OrderSpec.MovementNames[qi] == SquadSpec.Movements[qi],
                  "movement 名字表同集合（OrderSpec 与 SquadSpec）: " + OrderSpec.MovementNames[qi]);
        }
        for (int qi = 0; qi < OrderSpec.FormationNames.Length; qi++)
        {
            Check(OrderSpec.FormationNames[qi] == SquadSpec.Formations[qi],
                  "编队名字表前 5 项一致（OrderSpec 与 SquadSpec）: " + OrderSpec.FormationNames[qi]);
        }
        Check(OrderSpec.Join(OrderSpec.MovementNames).IndexOf("stop", StringComparison.Ordinal) > 0,
              "错误消息会带上可用值（OrderSpec.Join 生效）", OrderSpec.Join(OrderSpec.MovementNames));

        // v0.8.28：阵列 / 射击纪律的名字表（同样必须在碰 `ArrangementOrder` / `FiringOrder` 之前校验完）
        Check(OrderSpec.IsArrangement("shieldwall") && OrderSpec.IsArrangement("ShieldWall"),
              "阵列名白名单：大小写不敏感");
        Check(!OrderSpec.IsArrangement("wedge") && !OrderSpec.IsArrangement("") && !OrderSpec.IsArrangement(null),
              "阵列名白名单：怪值 / 空串 / null 必须拒");
        Check(OrderSpec.ArrangementNames.Length == 8,
              "阵列名共 8 个（引擎 ArrangementOrderEnum：Line/ShieldWall/Circle/Square/Skein/Column/Loose/Scatter）",
              OrderSpec.ArrangementNames.Length.ToString());
        Check(OrderSpec.IsFiring("holdFire") && OrderSpec.IsFiring("FireAtWill"),
              "射击纪律白名单：大小写不敏感");
        Check(!OrderSpec.IsFiring("holdFireUntilClose") && !OrderSpec.IsFiring("") && !OrderSpec.IsFiring(null),
              "射击纪律：引擎只有两档（FireAtWill / HoldYourFire），其余必须拒");
        Check(OrderSpec.FiringNames.Length == 2, "射击纪律共 2 档",
              OrderSpec.FiringNames.Length.ToString());

        // v0.8.32：骑乘令的名字表（`RidingOrder` 同样是"静态字段在类型初始化时构造"的 struct，
        // 所以校验也必须先于触碰它 ⇒ 名字表在这里、映射在 BattleOrders）。
        Check(OrderSpec.IsRiding("mount") && OrderSpec.IsRiding("Dismount") && OrderSpec.IsRiding("free"),
              "骑乘令白名单：三档都要认，且大小写不敏感");
        Check(!OrderSpec.IsRiding("ride") && !OrderSpec.IsRiding("") && !OrderSpec.IsRiding(null),
              "骑乘令：引擎只有 free / mount / dismount 三档，其余必须拒（ride/on/off 都不是引擎口径）");
        Check(OrderSpec.RidingNames.Length == 3, "骑乘令共 3 档",
              OrderSpec.RidingNames.Length.ToString());

        // v0.8.32：`targetAgent` 的下标上界（纯防御性 —— 明显是手滑的值要能被拒，而不是去海里捞）
        Check(OrderSpec.AgentIndexLimit > 10000 && OrderSpec.AgentIndexLimit <= 10000000,
              "targetAgent 下标上界在合理量级（覆盖真实战场规模，又挡得住地址/时间戳那类手滑值）",
              OrderSpec.AgentIndexLimit.ToString());

        // v0.8.30：指定点移动的坐标解析（同样必须在碰 `WorldPosition` / `MovementOrder` 之前跑完，
        // 所以它也得只依赖 BCL、也得有对照断言）。
        float cx, cy, cz;
        Check(OrderSpec.TryParsePosition("100,200", out cx, out cy, out cz)
              && cx == 100f && cy == 200f && cz == 0f,
              "position：\"x,y\" 两分量可解析，z 省略 = 0（由引擎按地面补 Z）",
              cx + "/" + cy + "/" + cz);
        Check(OrderSpec.TryParsePosition(" 12.5 , -3.25 , 8 ", out cx, out cy, out cz)
              && cx == 12.5f && cy == -3.25f && cz == 8f,
              "position：三分量 + 空格 + 负数 + 小数都可解析",
              cx + "/" + cy + "/" + cz);
        Check(!OrderSpec.TryParsePosition("100", out cx, out cy, out cz),
              "position：只有一个分量必须拒（不是\"x 给了 y z 当 0\"）");
        Check(!OrderSpec.TryParsePosition("1,2,3,4", out cx, out cy, out cz),
              "position：四个分量必须拒");
        Check(!OrderSpec.TryParsePosition("a,b", out cx, out cy, out cz),
              "position：非数字必须拒（绝不静默回落 0,0）");
        Check(!OrderSpec.TryParsePosition("1,", out cx, out cy, out cz),
              "position：空的 y 分量必须拒");
        Check(!OrderSpec.TryParsePosition("NaN,0", out cx, out cy, out cz)
              && !OrderSpec.TryParsePosition("Infinity,0", out cx, out cy, out cz),
              "position：NaN / Infinity 必须拒（否则会污染引擎的落点计算）");
        Check(!OrderSpec.TryParsePosition("1e9,0", out cx, out cy, out cz)
              && !OrderSpec.TryParsePosition("0,-99999", out cx, out cy, out cz),
              "position：超出 ±" + OrderSpec.PositionLimit.ToString("0") + " m 必须拒（多半是单位/小数点打错）",
              "limit=" + OrderSpec.PositionLimit);
        Check(OrderSpec.TryParsePosition("10000,-10000,0", out cx, out cy, out cz),
              "position：上界本身允许（边界值不误杀）");
        Check(!OrderSpec.TryParsePosition("", out cx, out cy, out cz)
              && !OrderSpec.TryParsePosition(null, out cx, out cy, out cz)
              && !OrderSpec.TryParsePosition("1;2", out cx, out cy, out cz),
              "position：空串 / null / 用分号当分隔符 必须拒");

        // ── 主菜单层面状态名（v0.8.22）────────────────────────────────────────
        // 与 Python 侧镜像 `bl_mcp._MAIN_MENU_ACTIVE_STATES` 必须同集合。
        // 存在理由（真机血证）：把"取状态机"改成静态优先后，主菜单的名字从空串变成 InitialState，
        // 而 open_ui 闸门当时写的是"空串 = 主菜单" ⇒ 真主菜单被判成"已加载"、open_ui 一律被拒
        // （真机 2026-09-25 23:05：requested=None / wrong_state_for_ui）。
        Check(MainMenuStates.Name == "InitialState", "主菜单状态名常量 = InitialState（真机实测值）");
        Check(MainMenuStates.IsMenuLevel(""), "主菜单层面：空串（取不到名字的启动期/过渡期）");
        Check(MainMenuStates.IsMenuLevel(null), "主菜单层面：null 也算（空 = 主菜单层面）");
        Check(MainMenuStates.IsMenuLevel("InitialState"), "主菜单层面：InitialState（主菜单本体）");
        foreach (string notMenu in new string[] { "VideoPlaybackState", "CustomBattleState",
                                                  "MapState", "CampaignState", "MissionState" })
        {
            Check(!MainMenuStates.IsMenuLevel(notMenu), "不在主菜单层面（必须拒）: " + notMenu);
        }

        Console.WriteLine();
        Console.WriteLine(_fail == 0 ? "结果: 全部通过" : "结果: 失败 " + _fail + " 项");
        return _fail == 0 ? 0 : 1;
    }
}
