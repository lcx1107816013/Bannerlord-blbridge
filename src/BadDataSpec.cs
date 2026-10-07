using System;
using System.Collections.Generic;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// 坏数据判据（**纯 BCL，不碰 TaleWorlds**）—— 所以能进 `tools/jsontest` 离线单测。
    ///
    /// ## 为什么把判据单独抽出来（架构决定的，不是洁癖）
    ///
    /// 「什么算坏数据」是本功能**最危险**的一处判断：
    ///
    ///   · **误报**（把正常数据判成坏）⇒ 下游"清理"会**删掉好数据** —— 不可逆；
    ///   · **漏报**（坏数据没判出来）⇒ 功能没用，但至少不伤数据。
    ///
    /// 所以判据必须具备两个性质：
    ///   ① **可离线对照**——能用合成样本证明"这种输入必红、那种输入不应红"；
    ///   ② **保守**——只在**明确错**时才报，含糊的一律降级为"可疑"，不报成"坏"。
    ///
    /// 而 `Campaign` / `MobileParty` 这些类型编不进离线单测（碰 TaleWorlds 就起不来），
    /// ⇒ 把判据抽到本文件（**只依赖 BCL**），数据访问留在 `BadDataScanner.cs`。
    /// 与 `OrderSpec` / `SquadSpec` 同一模式（见它们的类注释）。
    ///
    /// ## 判据来源（如实）
    ///
    /// 判据是**我们自己的**：从"这个字段为 null 意味着什么"反推，不抄任何上游实现。
    /// 上游项目（AltSaveSystemFix / retinues / Crash Doctor）只用于**确认方向可行**，
    /// 未取用其代码（详见 `docs/bad-data-scan.md` 的"参考与边界"）。
    /// </summary>
    internal static class BadDataSpec
    {
        // ────────────────────────────────────────────────────────────────
        // 严重度：只有两档，刻意不设第三档
        //
        // `Broken`  = **明确错**（结构上不可能正常）⇒ 可作为清理依据
        // `Suspect` = **可疑**（可能正常、也可能坏）⇒ **只报给人看，不作为自动清理依据**
        //
        // ★ 为什么必须分开：`Suspect` 里大量是"游戏本来就会出现"的合法状态
        //   （例如刚被灭的家族 `IsEliminated=true` 但 Hero 还没清空）。
        //   若把它们也当"坏"，清理器就会**删正常数据**。
        // ────────────────────────────────────────────────────────────────
        internal const string SeverityBroken = "broken";
        internal const string SeveritySuspect = "suspect";

        /// <summary>一条发现。字段刻意做成扁平字符串，便于直接落 JSONL。</summary>
        internal sealed class Finding
        {
            internal string Kind;        // party / clan / kingdom / army / quest / item
            internal string Severity;    // broken / suspect
            internal string Code;        // 机器可判的短码，如 party_no_component
            internal string Subject;     // 主体标识（名字或 id）
            internal string Detail;      // 人话说明
            internal string Evidence;    // **判据用到的事实**（便于人工复核，防误删）

            internal Finding(string kind, string severity, string code,
                             string subject, string detail, string evidence)
            {
                Kind = kind;
                Severity = severity;
                Code = code;
                Subject = subject;
                Detail = detail;
                Evidence = evidence;
            }
        }

        // ────────────────────────────────────────────────────────────────
        // 快照：把"活对象"压成**纯数据**，判据只认它
        //
        // ★ 为什么用 bool 而不是可空引用：快照由 Scanner 填，
        //   而 Scanner 是碰 TaleWorlds 的那一半 —— 它必须自己处理 null，
        //   不能把 null 传进来让判据猜。⇒ 用 Has* 布尔把"有没有"讲清楚。
        // ────────────────────────────────────────────────────────────────
        internal struct PartySnapshot
        {
            internal string Name;
            internal bool IsActive;
            internal bool HasPartyComponent;
            internal bool IsMainParty;
            internal bool IsGarrison;        // 驻军：**允许**成员为 0（是合法状态）
            internal bool IsBanditOrLooter;  // 匪徒/劫掠者：可能没有正式 clan
            internal int MemberCount;
            internal bool HasLeader;
            internal bool HasClan;
            internal bool ClanEliminated;    // 仅当 HasClan 时有意义
        }

        internal struct ClanSnapshot
        {
            internal string Name;
            internal bool IsEliminated;
            internal int HeroCount;
            internal bool HasLeader;
            internal bool HasKingdom;
            internal int SettlementCount;
            internal int PartyCount;
            // ★ 真机实测补上的两个字段（2026-10-07）：第一版没有它们 ⇒ **误报 9 条**。
            //   真机数据：`clan_empty_heroes` 报出的 9 条全是
            //   **劫匪 / 逃兵 / 海寇 / 山贼 / 绿林强盗 / 沙漠强盗 / 响马 / 海盗**
            //   —— 它们是游戏里的**匪帮/小家族**，`Hero = 0` 是**完全正常**的。
            //   ⇒ 不排除它们，清理器就会去删**正常数据**（不可逆）——正是本类注释里
            //     反复强调的"误报比漏报危险"。
            internal bool IsMinorFaction;   // `Clan.IsMinorFaction`（反射实测存在）
            internal bool IsBandit;         // `Clan.Culture.IsBandit`（反射实测存在）
        }

        internal struct KingdomSnapshot
        {
            internal string Name;
            internal bool IsEliminated;
            internal int ClanCount;
            internal bool HasLeader;
            internal int SettlementCount;
        }

        internal struct ArmySnapshot
        {
            internal string Name;
            internal int PartyCount;
            internal bool HasLeaderParty;
            internal bool LeaderPartyInParties;
            internal bool HasKingdom;
        }

        internal struct QuestSnapshot
        {
            internal string Name;
            internal bool IsOngoing;
            internal bool HasQuestGiver;
            internal bool StillRegistered;   // 是否仍挂在 QuestManager.Quests 里
        }

        internal struct ItemSnapshot
        {
            internal string Name;
            internal int Count;
            internal bool HasItemObject;     // ItemRoster 条目指向的 ItemObject 是否存在
        }

        // ────────────────────────────────────────────────────────────────
        // 判据：部队
        // ────────────────────────────────────────────────────────────────
        internal static void JudgeParty(PartySnapshot p, List<Finding> into)
        {
            string who = Safe(p.Name);

            // ① 活跃但**没有 PartyComponent** —— 结构上不可能正常：
            //    引擎所有队伍行为都通过 PartyComponent 挂载，没有它 = 悬空。
            if (p.IsActive && !p.HasPartyComponent)
            {
                into.Add(new Finding("party", SeverityBroken, "party_no_component", who,
                    "活跃部队但没有 PartyComponent（结构上不可能正常）",
                    "isActive=true, hasPartyComponent=false"));
            }

            // ② 已失活却仍在集合里 —— 幽灵部队。
            //    ⚠️ **非主力**才报：主角队伍在某些阶段会被临时置为失活，那是正常的。
            if (!p.IsActive && !p.IsMainParty)
            {
                into.Add(new Finding("party", SeverityBroken, "party_inactive_referenced", who,
                    "已失活（IsActive=false）但仍留在部队集合里 —— 幽灵部队",
                    "isActive=false, isMainParty=false"));
            }

            // ③ 有 clan 但 clan 已被消灭 —— 孤儿归属。
            if (p.IsActive && p.HasClan && p.ClanEliminated)
            {
                into.Add(new Finding("party", SeverityBroken, "party_orphan_clan", who,
                    "部队的家族已被消灭，但它仍活跃且挂着该家族",
                    "hasClan=true, clanEliminated=true, isActive=true"));
            }

            // ④ 空部队 —— **可疑而非坏**（见判据说明）。
            //    驻军允许空；主角队伍不该空。
            if (p.IsActive && p.MemberCount <= 0 && !p.IsGarrison && !p.IsMainParty)
            {
                into.Add(new Finding("party", SeveritySuspect, "party_empty_roster", who,
                    "活跃部队但成员数为 0（驻军允许为空，此处不是驻军）",
                    "isActive=true, memberCount=0, isGarrison=false"));
            }

            // ⑤ 领主队伍没有领袖 —— 可疑（换帅瞬间可能短暂如此）。
            if (p.IsActive && p.HasClan && !p.HasLeader && !p.IsGarrison
                && !p.IsBanditOrLooter)
            {
                into.Add(new Finding("party", SeveritySuspect, "party_no_leader", who,
                    "有家族的活跃部队没有领袖 Hero",
                    "isActive=true, hasClan=true, hasLeader=false"));
            }
        }

        // ────────────────────────────────────────────────────────────────
        // 判据：家族
        // ────────────────────────────────────────────────────────────────
        internal static void JudgeClan(ClanSnapshot c, List<Finding> into)
        {
            string who = Safe(c.Name);

            // ★★ 真机实测修正（2026-10-07）：**匪帮/小家族必须排除**。
            //
            // 第一版直接判"未消灭 + Hero=0 ⇒ 空家族(broken)"，
            // 真机一跑就报了 **9 条**，而这 9 条全是：
            //   劫匪 / 逃兵 / 海寇 / 山贼 / 绿林强盗 / 沙漠强盗 / 响马 / 海盗
            // —— 它们是游戏内置的**匪帮/小家族**（靠刷兵维持，本来就没有 Hero）。
            //
            // ⇒ 这是**误报**，而且方向最危险：下游清理会据此**删掉正常数据**（不可逆）。
            //   判据"必红"必须成对配一个"不应红"，这里补的正是那个"不应红"。
            //
            // 排除依据（反射实测）：`Clan.IsMinorFaction` / `Clan.Culture.IsBandit`。
            bool isBanditLike = c.IsMinorFaction || c.IsBandit;

            // ① 未消灭但**一个 Hero 都没有** —— 空家族（用户要的那类）。
            if (!c.IsEliminated && c.HeroCount <= 0 && !isBanditLike)
            {
                into.Add(new Finding("clan", SeverityBroken, "clan_empty_heroes", who,
                    "家族未被判定消灭，但没有任何 Hero —— 空家族",
                    "isEliminated=false, heroCount=0, isMinorFaction=false, isBandit=false"));
            }
            // ①b 匪帮类 Hero=0 是正常的 ⇒ 降级为 suspect 且**注明为何不报 broken**
            //    （保留一条低噪提示：万一有个匪帮真该有 Hero，人能看到它、但不作为清理依据）
            else if (!c.IsEliminated && c.HeroCount <= 0 && isBanditLike)
            {
                into.Add(new Finding("clan", SeveritySuspect, "clan_bandit_no_heroes", who,
                    "匪帮/小家族没有 Hero —— **这是正常状态**，不是坏数据（列出仅为可追溯）",
                    "isEliminated=false, heroCount=0, isMinorFaction=" + c.IsMinorFaction
                    + ", isBandit=" + c.IsBandit + " ⇒ 排除"));
            }

            // ② 未消灭但没有首领 —— 可疑（首领可能刚阵亡、尚未重算）。
            //    ⚠️ 匪帮同样没有"首领 Hero"，故一并排除（否则又是同一批误报）。
            if (!c.IsEliminated && !c.HasLeader && !isBanditLike)
            {
                into.Add(new Finding("clan", SeveritySuspect, "clan_no_leader", who,
                    "家族未被消灭但没有首领",
                    "isEliminated=false, hasLeader=false, isMinorFaction=false, isBandit=false"));
            }

            // ③ 仍在但既无部队也无聚落 —— 可疑（可能刚被灭、数据未收敛）。
            //    ⚠️ 匪帮没有聚落是正常的 ⇒ 同样排除。
            if (!c.IsEliminated && c.PartyCount <= 0 && c.SettlementCount <= 0 && !isBanditLike)
            {
                into.Add(new Finding("clan", SeveritySuspect, "clan_no_assets", who,
                    "家族仍在但既无部队也无聚落（可能刚被灭、数据未收敛）",
                    "isEliminated=false, partyCount=0, settlementCount=0, isMinorFaction=false, isBandit=false"));
            }
        }

        // ────────────────────────────────────────────────────────────────
        // 判据：王国
        // ────────────────────────────────────────────────────────────────
        internal static void JudgeKingdom(KingdomSnapshot k, List<Finding> into)
        {
            string who = Safe(k.Name);

            if (!k.IsEliminated && k.ClanCount <= 0)
            {
                into.Add(new Finding("kingdom", SeverityBroken, "kingdom_empty_clans", who,
                    "王国未被判定消灭，但没有任何家族",
                    "isEliminated=false, clanCount=0"));
            }
            if (!k.IsEliminated && !k.HasLeader)
            {
                into.Add(new Finding("kingdom", SeveritySuspect, "kingdom_no_leader", who,
                    "王国未被消灭但没有领袖",
                    "isEliminated=false, hasLeader=false"));
            }
        }

        // ────────────────────────────────────────────────────────────────
        // 判据：军团（Army）
        //
        // ⚠️ 容器是 `Kingdom.Armies`，**不是** `Campaign.Armies`
        //    —— 后者**实测不存在**（见 docs 的容器真名表）。数据访问在 Scanner 侧。
        // ────────────────────────────────────────────────────────────────
        internal static void JudgeArmy(ArmySnapshot a, List<Finding> into)
        {
            string who = Safe(a.Name);

            if (a.PartyCount <= 0)
            {
                into.Add(new Finding("army", SeverityBroken, "army_no_parties", who,
                    "军团没有任何成员部队 —— 空军团",
                    "partyCount=0"));
            }
            if (!a.HasLeaderParty)
            {
                into.Add(new Finding("army", SeverityBroken, "army_no_leader", who,
                    "军团没有领袖部队",
                    "hasLeaderParty=false"));
            }
            else if (!a.LeaderPartyInParties)
            {
                // ★ 结构矛盾：领袖**不在**自己的成员表里 ⇒ 引擎遍历时会漏掉它。
                into.Add(new Finding("army", SeverityBroken, "army_leader_not_in_parties", who,
                    "军团的领袖部队不在其成员列表中（结构矛盾）",
                    "hasLeaderParty=true, leaderPartyInParties=false"));
            }
            if (!a.HasKingdom)
            {
                into.Add(new Finding("army", SeveritySuspect, "army_no_kingdom", who,
                    "军团没有所属王国",
                    "hasKingdom=false"));
            }
        }

        // ────────────────────────────────────────────────────────────────
        // 判据：任务
        // ────────────────────────────────────────────────────────────────
        internal static void JudgeQuest(QuestSnapshot q, List<Finding> into)
        {
            string who = Safe(q.Name);

            // ① 已不进行却仍挂在 QuestManager 里 —— **卡死/残留任务**（用户要的那类）。
            if (!q.IsOngoing && q.StillRegistered)
            {
                into.Add(new Finding("quest", SeverityBroken, "quest_finished_still_registered",
                    who, "任务已不再进行，但仍登记在 QuestManager 中 —— 残留任务",
                    "isOngoing=false, stillRegistered=true"));
            }
            // ② 委托人不在了 —— 可疑（任务可能仍能完成）。
            if (q.IsOngoing && !q.HasQuestGiver)
            {
                into.Add(new Finding("quest", SeveritySuspect, "quest_no_giver", who,
                    "任务仍在进行但没有委托人",
                    "isOngoing=true, hasQuestGiver=false"));
            }
        }

        // ────────────────────────────────────────────────────────────────
        // 判据：物品
        // ────────────────────────────────────────────────────────────────
        internal static void JudgeItem(ItemSnapshot it, List<Finding> into)
        {
            string who = Safe(it.Name);

            if (!it.HasItemObject)
            {
                into.Add(new Finding("item", SeverityBroken, "item_null_object", who,
                    "物品栏条目指向的物品对象不存在 —— 无效物品实例",
                    "hasItemObject=false"));
            }
            // ★ 真机实测修正（2026-10-07）：**"读不到数量"必须与"数量真的是 0"区分开**。
            //   第一版把两者混为一谈（读不到时 `Count=0`）⇒ 真机报了 60 条
            //   "item_nonpositive_count"，而根因是**我调用签名用错了**
            //   （`GetItemNumber` 收的是 `ItemObject` 不是索引），**不是数据坏了**。
            //   ⇒ 约定：`Count < 0` 表示**没读到**，此时**不下任何结论**（只记 unreadable）。
            else if (it.Count < 0)
            {
                into.Add(new Finding("item", SeveritySuspect, "item_count_unreadable", who,
                    "物品数量**没读到** —— 不下结论（不代表数量是 0，也不代表数据坏）",
                    "count=<unreadable>"));
            }
            else if (it.Count == 0)
            {
                into.Add(new Finding("item", SeveritySuspect, "item_nonpositive_count", who,
                    "物品数量为 0（通常应是已移除；**不是 broken**）",
                    "count=0"));
            }
        }

        // ────────────────────────────────────────────────────────────────
        // 汇总（**报告本体**）
        // ────────────────────────────────────────────────────────────────
        internal static string BuildSummaryJson(List<Finding> findings, int scannedParties,
                                                int scannedClans, int scannedKingdoms,
                                                int scannedArmies, int scannedQuests,
                                                int scannedItems, double elapsedMs)
        {
            int broken = 0, suspect = 0;
            Dictionary<string, int> byKind = new Dictionary<string, int>(StringComparer.Ordinal);
            Dictionary<string, int> byCode = new Dictionary<string, int>(StringComparer.Ordinal);
            for (int i = 0; i < findings.Count; i++)
            {
                Finding f = findings[i];
                if (f.Severity == SeverityBroken) broken++; else suspect++;
                int n;
                byKind.TryGetValue(f.Kind, out n);
                byKind[f.Kind] = n + 1;
                byCode.TryGetValue(f.Code, out n);
                byCode[f.Code] = n + 1;
            }

            StringBuilder sb = new StringBuilder();
            sb.Append("{\"ok\":true");
            sb.Append(",\"readOnly\":true");
            sb.Append(",\"elapsedMs\":").Append(Jw.N((float)elapsedMs));
            sb.Append(",\"scanned\":{\"parties\":").Append(scannedParties)
              .Append(",\"clans\":").Append(scannedClans)
              .Append(",\"kingdoms\":").Append(scannedKingdoms)
              .Append(",\"armies\":").Append(scannedArmies)
              .Append(",\"quests\":").Append(scannedQuests)
              .Append(",\"items\":").Append(scannedItems).Append('}');
            sb.Append(",\"found\":").Append(findings.Count);
            sb.Append(",\"broken\":").Append(broken);
            sb.Append(",\"suspect\":").Append(suspect);
            sb.Append(",\"byKind\":").Append(DictJson(byKind));
            sb.Append(",\"byCode\":").Append(DictJson(byCode));
            sb.Append(",\"findings\":[");
            for (int i = 0; i < findings.Count; i++)
            {
                if (i > 0) sb.Append(',');
                Finding f = findings[i];
                sb.Append("{\"kind\":").Append(Protocol.Q(f.Kind))
                  .Append(",\"severity\":").Append(Protocol.Q(f.Severity))
                  .Append(",\"code\":").Append(Protocol.Q(f.Code))
                  .Append(",\"subject\":").Append(Protocol.Q(f.Subject))
                  .Append(",\"detail\":").Append(Protocol.Q(f.Detail))
                  .Append(",\"evidence\":").Append(Protocol.Q(f.Evidence))
                  .Append('}');
            }
            sb.Append(']');
            // ★ 口径：把"这份报告不是什么"写进结果，避免下游把它当授权。
            sb.Append(",\"scope\":").Append(Protocol.Q(
                "只读扫描。severity=broken 是明确错；severity=suspect 是可疑，"
                + "可能只是游戏正常的过渡状态（如刚被灭的家族）。"
                + "本扫描不改任何数据；清理必须另行实现且先备份。"));
            sb.Append('}');
            return sb.ToString();
        }

        private static string DictJson(Dictionary<string, int> d)
        {
            StringBuilder sb = new StringBuilder("{");
            bool first = true;
            foreach (KeyValuePair<string, int> kv in d)
            {
                if (!first) sb.Append(',');
                first = false;
                sb.Append(Protocol.Q(kv.Key)).Append(':').Append(kv.Value);
            }
            return sb.Append('}').ToString();
        }

        private static string Safe(string s)
        {
            if (string.IsNullOrEmpty(s)) return "(无名)";
            // 防超长名字把报告撑爆（第三方 mod 可能塞长串）
            return s.Length <= 80 ? s : s.Substring(0, 80) + "...";
        }
    }
}
