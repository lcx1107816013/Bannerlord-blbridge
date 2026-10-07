using System;
using System.Collections.Generic;
using BlBridge;

/// <summary>
/// 坏数据判据的**离线对照测试**（`BadDataSpec` 是纯 BCL ⇒ 能编进这里）。
///
/// ## 为什么每条判据都必须**成对**（必红 / 不应红）
///
/// 判据的两种失效方向**代价极不对称**：
///   · **误报**（好数据判成坏）⇒ 下游清理会**删好数据** —— **不可逆**；
///   · **漏报**（坏数据没判出）⇒ 功能没用，但至少不伤数据。
///
/// ⇒ 所以每条判据都要同时证明两件事：
///   ① 该红的**必红**（否则等于没判据）；
///   ② 相邻的正常情形**不应红**（否则清理器会误伤）。
///   只证①会得到"什么都报坏"的判据；只证②会得到"从不报警"的判据。
///
/// ## 与"不能自证"的关系
///
/// 这里用的是**合成快照**（不是从游戏读的），所以它证明的是"判据逻辑正确"，
/// **不**证明"数据访问层取对了字段"。后者由真机扫描的 `scanned` 计数证明
/// （见 `docs/bad-data-scan.md` §验证：扫描量非零 + 有真样本才叫有效）。
/// 两层证据缺一不可，别混为一谈。
public static class BadDataSpecTest
{
    private static int _checks;
    private static readonly List<string> _fails = new List<string>();

    public static int Main()
    {
        Console.WriteLine("=== 坏数据判据对照测试（BadDataSpec，纯 BCL）===");
        Console.WriteLine();

        // ════════════════════════════════════════════════════════════════
        Console.WriteLine("--- party ---");
        Party(must: true, "活跃但无 PartyComponent ⇒ broken",
              new BadDataSpec.PartySnapshot { Name = "幽灵", IsActive = true, HasPartyComponent = false },
              "party_no_component", BadDataSpec.SeverityBroken);
        // 对照：同样的"没有 component"，但**不活跃** ⇒ 不报这条（避免与"已失活"重复报）
        Party(must: false, "不活跃 + 无 component ⇒ 不报 no_component（另有条目管它）",
              new BadDataSpec.PartySnapshot { Name = "正常", IsActive = false, HasPartyComponent = false },
              "party_no_component");

        Party(must: true, "非主力失活但仍在集合 ⇒ broken（幽灵部队）",
              new BadDataSpec.PartySnapshot { Name = "幽灵2", IsActive = false, IsMainParty = false },
              "party_inactive_referenced", BadDataSpec.SeverityBroken);
        // 对照：**主角队伍**失活是允许的（某些阶段会临时如此）⇒ 不报
        Party(must: false, "主角队伍失活 ⇒ 不报（正常阶段）",
              new BadDataSpec.PartySnapshot { Name = "主角", IsActive = false, IsMainParty = true },
              "party_inactive_referenced");

        Party(must: true, "挂已灭家族 ⇒ broken",
              new BadDataSpec.PartySnapshot { Name = "孤儿", IsActive = true, HasClan = true, ClanEliminated = true },
              "party_orphan_clan", BadDataSpec.SeverityBroken);
        // 对照：家族**未被灭** ⇒ 不报
        Party(must: false, "家族健在 ⇒ 不报孤儿",
              new BadDataSpec.PartySnapshot { Name = "正常", IsActive = true, HasClan = true, ClanEliminated = false },
              "party_orphan_clan");

        Party(must: true, "非驻军空部队 ⇒ suspect（**不是 broken**）",
              new BadDataSpec.PartySnapshot { Name = "空", IsActive = true, MemberCount = 0, IsGarrison = false },
              "party_empty_roster", BadDataSpec.SeveritySuspect);
        // 对照：**驻军为空是合法的**（实测：驻军本来就可能没人）⇒ 不报
        Party(must: false, "驻军为空 ⇒ 不报（合法状态）",
              new BadDataSpec.PartySnapshot { Name = "驻军", IsActive = true, MemberCount = 0, IsGarrison = true },
              "party_empty_roster");
        // 对照：主角队伍为空 ⇒ 不报（换兵瞬间可能如此，且是主角）
        Party(must: false, "主角队伍为空 ⇒ 不报",
              new BadDataSpec.PartySnapshot { Name = "主角", IsActive = true, MemberCount = 0, IsMainParty = true },
              "party_empty_roster");

        // ════════════════════════════════════════════════════════════════
        Console.WriteLine();
        Console.WriteLine("--- clan ---");
        Clan(must: true, "未消灭但零 Hero ⇒ broken（空家族）",
             new BadDataSpec.ClanSnapshot { Name = "空家族", IsEliminated = false, HeroCount = 0 },
             "clan_empty_heroes", BadDataSpec.SeverityBroken);
        // ★ 这条是**最关键的反向对照**：已消灭的家族 Hero=0 是**正常**的
        //   （这正是"不能把 suspect 当 broken 去清理"的理由）
        Clan(must: false, "★ 已消灭 + 零 Hero ⇒ **不报**（这是正常状态，不是坏数据）",
             new BadDataSpec.ClanSnapshot { Name = "已灭家族", IsEliminated = true, HeroCount = 0 },
             "clan_empty_heroes");
        Clan(must: false, "有 Hero ⇒ 不报空家族",
             new BadDataSpec.ClanSnapshot { Name = "正常", IsEliminated = false, HeroCount = 5 },
             "clan_empty_heroes");

        // ★★ 真机实测补的关键对照（2026-10-07）：**匪帮/小家族 Hero=0 是正常的**。
        //   第一版没有这个排除 ⇒ 真机报了 9 条误报，全是
        //   劫匪/逃兵/海寇/山贼/绿林强盗/沙漠强盗/响马/海盗。
        //   ⇒ 这是"误报会误导清理器删正常数据"的直接实例，故必须有成对判据守住。
        Clan(must: false, "★ 匪帮（IsMinorFaction）+ 零 Hero ⇒ **不报 broken**（误报防线）",
             new BadDataSpec.ClanSnapshot { Name = "劫匪", IsEliminated = false, HeroCount = 0,
                                            IsMinorFaction = true },
             "clan_empty_heroes");
        Clan(must: false, "★ 匪帮（Culture.IsBandit）+ 零 Hero ⇒ **不报 broken**（另一条判据）",
             new BadDataSpec.ClanSnapshot { Name = "海寇", IsEliminated = false, HeroCount = 0,
                                            IsBandit = true },
             "clan_empty_heroes");
        Clan(must: false, "★ 匪帮无首领 ⇒ 不报 clan_no_leader（同一批误报的另一条）",
             new BadDataSpec.ClanSnapshot { Name = "山贼", IsEliminated = false, HeroCount = 0,
                                            IsBandit = true, HasLeader = false },
             "clan_no_leader");
        Clan(must: false, "★ 匪帮无资产 ⇒ 不报 clan_no_assets（匪帮本来就没聚落）",
             new BadDataSpec.ClanSnapshot { Name = "响马", IsEliminated = false, HeroCount = 0,
                                            IsMinorFaction = true, HasLeader = true,
                                            PartyCount = 0, SettlementCount = 0 },
             "clan_no_assets");
        // 但匪帮仍会**低噪列出**一条 suspect（可追溯，且**不作清理依据**）
        Clan(must: true, "匪帮零 Hero ⇒ 仍列 suspect（可追溯，但不作清理依据）",
             new BadDataSpec.ClanSnapshot { Name = "逃兵", IsEliminated = false, HeroCount = 0,
                                            IsMinorFaction = true },
             "clan_bandit_no_heroes", BadDataSpec.SeveritySuspect);

        Clan(must: true, "未消灭但无首领 ⇒ suspect",
             new BadDataSpec.ClanSnapshot { Name = "无首领", IsEliminated = false, HeroCount = 3, HasLeader = false },
             "clan_no_leader", BadDataSpec.SeveritySuspect);
        Clan(must: false, "有首领 ⇒ 不报",
             new BadDataSpec.ClanSnapshot { Name = "正常", IsEliminated = false, HeroCount = 3, HasLeader = true },
             "clan_no_leader");

        // ════════════════════════════════════════════════════════════════
        Console.WriteLine();
        Console.WriteLine("--- kingdom ---");
        Kingdom(must: true, "未消灭但零家族 ⇒ broken",
                new BadDataSpec.KingdomSnapshot { Name = "空王国", IsEliminated = false, ClanCount = 0 },
                "kingdom_empty_clans", BadDataSpec.SeverityBroken);
        Kingdom(must: false, "已消灭 + 零家族 ⇒ 不报（正常）",
                new BadDataSpec.KingdomSnapshot { Name = "已灭王国", IsEliminated = true, ClanCount = 0 },
                "kingdom_empty_clans");
        Kingdom(must: false, "有家族 ⇒ 不报",
                new BadDataSpec.KingdomSnapshot { Name = "正常", IsEliminated = false, ClanCount = 7 },
                "kingdom_empty_clans");

        // ════════════════════════════════════════════════════════════════
        Console.WriteLine();
        Console.WriteLine("--- army ---");
        Army(must: true, "零成员 ⇒ broken（空军团）",
             new BadDataSpec.ArmySnapshot { Name = "空", PartyCount = 0, HasLeaderParty = true, LeaderPartyInParties = true },
             "army_no_parties", BadDataSpec.SeverityBroken);
        Army(must: false, "有成员 ⇒ 不报空军团",
             new BadDataSpec.ArmySnapshot { Name = "正常", PartyCount = 4, HasLeaderParty = true, LeaderPartyInParties = true },
             "army_no_parties");

        Army(must: true, "无领袖 ⇒ broken",
             new BadDataSpec.ArmySnapshot { Name = "无领袖", PartyCount = 3, HasLeaderParty = false },
             "army_no_leader", BadDataSpec.SeverityBroken);
        // ★ 结构矛盾：领袖**不在自己的成员表里** —— 引擎遍历会漏掉它
        Army(must: true, "领袖不在自己的 Parties 里 ⇒ broken（结构矛盾）",
             new BadDataSpec.ArmySnapshot { Name = "矛盾", PartyCount = 3, HasLeaderParty = true, LeaderPartyInParties = false },
             "army_leader_not_in_parties", BadDataSpec.SeverityBroken);
        // 对照：领袖在成员表里 ⇒ 不报这条
        Army(must: false, "领袖在成员表里 ⇒ 不报矛盾",
             new BadDataSpec.ArmySnapshot { Name = "正常", PartyCount = 3, HasLeaderParty = true, LeaderPartyInParties = true },
             "army_leader_not_in_parties");

        // ════════════════════════════════════════════════════════════════
        Console.WriteLine();
        Console.WriteLine("--- quest ---");
        Quest(must: true, "已结束却仍登记 ⇒ broken（卡死/残留任务）",
              new BadDataSpec.QuestSnapshot { Name = "残留", IsOngoing = false, StillRegistered = true },
              "quest_finished_still_registered", BadDataSpec.SeverityBroken);
        // 对照：**正在进行**且已登记 = 完全正常 ⇒ 不报
        Quest(must: false, "进行中 + 已登记 ⇒ 不报（正常）",
              new BadDataSpec.QuestSnapshot { Name = "正常", IsOngoing = true, StillRegistered = true },
              "quest_finished_still_registered");
        // 对照：已结束 + **未登记** = 已清理干净 ⇒ 不报
        Quest(must: false, "已结束 + 未登记 ⇒ 不报（已清理）",
              new BadDataSpec.QuestSnapshot { Name = "已清", IsOngoing = false, StillRegistered = false },
              "quest_finished_still_registered");

        Quest(must: true, "进行中但无委托人 ⇒ suspect",
              new BadDataSpec.QuestSnapshot { Name = "无主", IsOngoing = true, HasQuestGiver = false },
              "quest_no_giver", BadDataSpec.SeveritySuspect);

        // ════════════════════════════════════════════════════════════════
        Console.WriteLine();
        Console.WriteLine("--- item ---");
        Item(must: true, "物品对象为空 ⇒ broken（无效物品实例）",
             new BadDataSpec.ItemSnapshot { Name = "坏物品", HasItemObject = false },
             "item_null_object", BadDataSpec.SeverityBroken);
        Item(must: false, "物品对象在 ⇒ 不报",
             new BadDataSpec.ItemSnapshot { Name = "正常", HasItemObject = true, Count = 5 },
             "item_null_object");
        Item(must: false, "数量=0 且对象在 ⇒ **只报 suspect，不报 broken**",
             new BadDataSpec.ItemSnapshot { Name = "零数量", HasItemObject = true, Count = 0 },
             "item_null_object");
        Item(must: true, "数量=0 ⇒ suspect",
             new BadDataSpec.ItemSnapshot { Name = "零数量", HasItemObject = true, Count = 0 },
             "item_nonpositive_count", BadDataSpec.SeveritySuspect);
        // ★ 真机实测补的对照（2026-10-07）：**"没读到" ≠ "数量是 0"**。
        //   第一版混为一谈 ⇒ 真机 60 条物品全被误报（根因是我调用签名用错）。
        Item(must: false, "★ 数量读不到（Count=-1）⇒ **不报** nonpositive_count（防把读不到当 0）",
             new BadDataSpec.ItemSnapshot { Name = "读不到", HasItemObject = true, Count = -1 },
             "item_nonpositive_count");
        Item(must: true, "★ 数量读不到 ⇒ 单独报 item_count_unreadable（不静默）",
             new BadDataSpec.ItemSnapshot { Name = "读不到", HasItemObject = true, Count = -1 },
             "item_count_unreadable", BadDataSpec.SeveritySuspect);

        // ════════════════════════════════════════════════════════════════
        Console.WriteLine();
        Console.WriteLine("--- 汇总 JSON ---");
        List<BadDataSpec.Finding> fs = new List<BadDataSpec.Finding>();
        // ⚠️ 这两个 fixture 是**精心构造的**：只触发各自那一条 broken，
        //    不连带触发 suspect（第一版我随手复用了别的 fixture，
        //    结果 MemberCount=0 / HasLeader=false 额外产生 3 条 suspect，
        //    断言 suspect:0 就假失败了 —— **是测试写错，不是代码错**）。
        //    ⇒ 断言计数时必须让 fixture **只产生想数的那几条**，否则计数断言没有意义。
        BadDataSpec.JudgeParty(new BadDataSpec.PartySnapshot {
            Name = "幽灵", IsActive = true, HasPartyComponent = false,
            MemberCount = 5,            // 非空 ⇒ 不触发 party_empty_roster
            HasClan = false,            // 无家族 ⇒ 不触发 party_no_leader
        }, fs);
        BadDataSpec.JudgeClan(new BadDataSpec.ClanSnapshot {
            Name = "空家族", IsEliminated = false, HeroCount = 0,
            HasLeader = true,           // 有首领 ⇒ 不触发 clan_no_leader
            PartyCount = 1, SettlementCount = 1,   // 有资产 ⇒ 不触发 clan_no_assets
        }, fs);
        string js = BadDataSpec.BuildSummaryJson(fs, 10, 20, 3, 1, 2, 4, 12.5);
        check("汇总 JSON 含 readOnly:true（口径：只读）", js.Contains("\"readOnly\":true"), js.Substring(0, 120));
        check("汇总 JSON broken 计数正确（2）", js.Contains("\"broken\":2"), js);
        check("汇总 JSON suspect 计数正确（0）", js.Contains("\"suspect\":0"), js);
        check("汇总 JSON 带 scanned 明细", js.Contains("\"scanned\":{"), js.Substring(0, 200));
        check("汇总 JSON 带 byCode（便于一眼看哪类最多）", js.Contains("\"byCode\":{"), js);
        check("汇总 JSON 带 scope 说明（不是授权）", js.Contains("\"scope\":") && js.Contains("只读扫描"),
              js.Substring(js.Length - 150));
        check("汇总 JSON 每条 finding 都带 evidence（便于人工复核）",
              js.Contains("\"evidence\":\"isActive=true"), js);
        check("finding 是合法 JSON（能被独立解析，而非自证）", ParseOk(js), js.Substring(0, 160));

        // ════════════════════════════════════════════════════════════════
        Console.WriteLine();
        Console.WriteLine("==================================================");
        if (_fails.Count > 0)
        {
            Console.WriteLine("失败 " + _fails.Count + " / " + _checks);
            foreach (string f in _fails) Console.WriteLine("  - " + f);
            return 1;
        }
        Console.WriteLine("全部通过：" + _checks + " / " + _checks);
        return 0;
    }

    /// <summary>用 BCL 自己的解析器验（**不是**用 Jmini 验 Jmini —— 那是自证）。</summary>
    private static bool ParseOk(string s)
    {
        try
        {
            var ser = new System.Web.Script.Serialization.JavaScriptSerializer();
            object o = ser.DeserializeObject(s);
            return o is System.Collections.Generic.Dictionary<string, object>;
        }
        catch
        {
            return false;
        }
    }

    // ── 断言小工具 ────────────────────────────────────────────────────
    private static void Fire(string kind, string label, List<BadDataSpec.Finding> fs,
                             string code, string severity)
    {
        _checks++;
        BadDataSpec.Finding hit = null;
        foreach (BadDataSpec.Finding f in fs)
        {
            if (f.Code == code) { hit = f; break; }
        }
        bool ok = hit != null && (severity == null || hit.Severity == severity);
        Console.WriteLine("  [" + (ok ? "OK" : "FAIL") + "]   " + label);
        if (!ok)
        {
            _fails.Add(label + "  (期望 code=" + code + " severity=" + severity + ")");
        }
    }

    private static void NoFire(string label, List<BadDataSpec.Finding> fs, string code)
    {
        _checks++;
        bool fired = false;
        foreach (BadDataSpec.Finding f in fs)
        {
            if (f.Code == code) { fired = true; break; }
        }
        Console.WriteLine("  [" + (fired ? "FAIL" : "OK") + "]   " + label);
        if (fired) _fails.Add(label + "  (不该报 code=" + code + " 却报了)");
    }

    private static void Party(bool must, string label, BadDataSpec.PartySnapshot p,
                              string code, string severity = null)
    {
        List<BadDataSpec.Finding> fs = new List<BadDataSpec.Finding>();
        BadDataSpec.JudgeParty(p, fs);
        if (must) Fire("party", label, fs, code, severity); else NoFire(label, fs, code);
    }

    private static void Clan(bool must, string label, BadDataSpec.ClanSnapshot c,
                             string code, string severity = null)
    {
        List<BadDataSpec.Finding> fs = new List<BadDataSpec.Finding>();
        BadDataSpec.JudgeClan(c, fs);
        if (must) Fire("clan", label, fs, code, severity); else NoFire(label, fs, code);
    }

    private static void Kingdom(bool must, string label, BadDataSpec.KingdomSnapshot k,
                                string code, string severity = null)
    {
        List<BadDataSpec.Finding> fs = new List<BadDataSpec.Finding>();
        BadDataSpec.JudgeKingdom(k, fs);
        if (must) Fire("kingdom", label, fs, code, severity); else NoFire(label, fs, code);
    }

    private static void Army(bool must, string label, BadDataSpec.ArmySnapshot a,
                             string code, string severity = null)
    {
        List<BadDataSpec.Finding> fs = new List<BadDataSpec.Finding>();
        BadDataSpec.JudgeArmy(a, fs);
        if (must) Fire("army", label, fs, code, severity); else NoFire(label, fs, code);
    }

    private static void Quest(bool must, string label, BadDataSpec.QuestSnapshot q,
                              string code, string severity = null)
    {
        List<BadDataSpec.Finding> fs = new List<BadDataSpec.Finding>();
        BadDataSpec.JudgeQuest(q, fs);
        if (must) Fire("quest", label, fs, code, severity); else NoFire(label, fs, code);
    }

    private static void Item(bool must, string label, BadDataSpec.ItemSnapshot it,
                             string code, string severity = null)
    {
        List<BadDataSpec.Finding> fs = new List<BadDataSpec.Finding>();
        BadDataSpec.JudgeItem(it, fs);
        if (must) Fire("item", label, fs, code, severity); else NoFire(label, fs, code);
    }

    private static void check(string name, bool cond, string detail = "")
    {
        _checks++;
        Console.WriteLine("  [" + (cond ? "OK" : "FAIL") + "]   " + name
                          + (cond ? "" : "  -- " + detail));
        if (!cond) _fails.Add(name + "  -- " + detail);
    }
}
