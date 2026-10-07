using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Text;

using TaleWorlds.CampaignSystem;
using TaleWorlds.CampaignSystem.Party;
using TaleWorlds.CampaignSystem.Roster;

namespace BlBridge
{
    /// <summary>
    /// 坏数据扫描 —— **数据访问层**（碰 TaleWorlds），判据在 `BadDataSpec`（纯 BCL，可离线单测）。
    ///
    /// ## 分层理由（架构决定的）
    ///
    /// 「什么算坏数据」是本功能**最危险**的判断（误报 ⇒ 下游清理会**删好数据**），
    /// 所以判据必须**可离线对照**；而 `Campaign`/`MobileParty` 编不进离线单测。
    /// ⇒ 判据抽出、数据访问留在这里。与 `OrderSpec`/`SquadSpec` 同一模式。
    ///
    /// ## 本文件的两条硬纪律
    ///
    /// ① **只用反射实测过的成员**，不猜名字。
    ///    实测依据（2026-10-07，本机 1.4.8，见 `E:\Document\_scannerapi\` 的探针）：
    ///      · 容器真名：`Campaign.MobileParties` / `.Clans` / `.Kingdoms` / `.Settlements`
    ///        / **`Campaign.QuestManager.Quests`** / **`Kingdom.Armies`**
    ///      · ⚠️ **`Campaign.Armies` / `.Quests` / `.Issues` 都不存在**
    ///        （第一版猜错过 —— 照错的写法会得到"什么都扫不到却报没问题"的扫描器）
    ///      · `MobileParty.PartyComponent` / `IsGarrison` / `IsBandit` / `MemberRoster`
    ///        / `ActualClan` / `LeaderHero` / `IsMainParty` 全在
    ///      · `TroopRoster.TotalManCount`（成员数）
    ///      · `QuestBase.Title`（⚠️ **不是 `Name`**）+ `IsOngoing` + `QuestGiver` + `StringId`
    ///      · `MobileParty.ItemRoster`（直接可取）+ `ItemRoster.Count` / `GetItemAtIndex`
    ///
    /// ② **每个字段都单独取、单独降级**：任一成员缺失只让**那个字段**为 null，
    ///    不让整次扫描炸掉。理由：这是**只读诊断**，返回"部分数据 + 缺失说明"
    ///    远好过整个工具不可用（与 `CampaignReadProbe` 同口径）。
    ///
    /// ## ★ 与"清理"的边界
    ///
    /// 本文件**只读**：不写、不改、不删任何对象。调用方拿到的是一份**报告**。
    /// 清理必须另行实现，且**先备份 + 写新档不覆盖**（见 docs）。
    /// </summary>
    internal static class BadDataScanner
    {
        // ── 反射取值（属性优先、public 字段兜底；缺失/抛异常 ⇒ null）──────────────
        private static object P(object o, string name)
        {
            if (o == null) return null;
            Type t = o.GetType();
            PropertyInfo p = t.GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
            if (p != null)
            {
                try { return p.GetValue(o, null); } catch { }
            }
            FieldInfo f = t.GetField(name, BindingFlags.Public | BindingFlags.Instance);
            if (f != null)
            {
                try { return f.GetValue(o); } catch { }
            }
            return null;
        }

        private static bool B(object o, string name)
        {
            object v = P(o, name);
            return v is bool b && b;
        }

        private static int I(object o, string name)
        {
            object v = P(o, name);
            if (v == null) return 0;
            try { return Convert.ToInt32(v, CultureInfo.InvariantCulture); } catch { return 0; }
        }

        private static string S(object o, string name)
        {
            object v = P(o, name);
            return v == null ? null : v.ToString();
        }

        /// <summary>集合计数（`MBReadOnlyList&lt;T&gt;` / `TroopRoster` 等都实现 ICollection）。</summary>
        private static int CountOf(object o)
        {
            if (o == null) return 0;
            if (o is ICollection c) return c.Count;
            object n = P(o, "Count");
            if (n is int i) return i;
            return 0;
        }

        /// <summary>遍历任意可枚举对象（null / 非可枚举 ⇒ 空表，不抛）。</summary>
        private static IEnumerable<object> Each(object o)
        {
            if (o == null) yield break;
            IEnumerable en = o as IEnumerable;
            if (en == null) yield break;
            foreach (object x in en)
            {
                if (x != null) yield return x;
            }
        }

        /// <summary>把 `TroopRosterElement.Character.HeroObject` 取出来（同伴判定用）。</summary>
        private static object HeroOf(object troopRosterElement)
        {
            object ch = P(troopRosterElement, "Character");
            return P(ch, "HeroObject");
        }

        // ════════════════════════════════════════════════════════════════════
        // 扫描入口
        // ════════════════════════════════════════════════════════════════════
        internal static string HandleScan(string id, string raw)
        {
            if (Campaign.Current == null)
            {
                return Protocol.Failure(id, "no_campaign",
                    "坏数据扫描需要战役上下文 —— 主菜单 / 自定义战斗里没有战役数据（需进存档）", false);
            }

            // `only` 可限定扫哪几类（默认全扫）：不传就全扫
            string only = Jmini.Str(raw, "only", null);
            bool want(string k) { return string.IsNullOrEmpty(only) || only.IndexOf(k, StringComparison.OrdinalIgnoreCase) >= 0; }
            int limit = Jmini.Int(raw, "limit", 0);   // 每类最多入报告的条数（0 = 不限）

            DateTime t0 = DateTime.UtcNow;
            List<BadDataSpec.Finding> findings = new List<BadDataSpec.Finding>();

            int scannedParties = 0, scannedClans = 0, scannedKingdoms = 0;
            int scannedArmies = 0, scannedQuests = 0, scannedItems = 0;
            List<string> skipped = new List<string>();

            // ── 部队 ─────────────────────────────────────────────────────────
            if (want("party"))
            {
                if (Campaign.Current.MobileParties == null) { skipped.Add("MobileParties=null"); }
                else
                {
                    foreach (object p in Each(Campaign.Current.MobileParties))
                    {
                        scannedParties++;
                        object clan = P(p, "ActualClan");
                        BadDataSpec.PartySnapshot ps = new BadDataSpec.PartySnapshot
                        {
                            Name = S(p, "Name"),
                            IsActive = B(p, "IsActive"),
                            HasPartyComponent = P(p, "PartyComponent") != null,
                            IsMainParty = B(p, "IsMainParty"),
                            IsGarrison = B(p, "IsGarrison"),
                            IsBanditOrLooter = B(p, "IsBandit"),
                            MemberCount = I(P(p, "MemberRoster"), "TotalManCount"),
                            HasLeader = P(p, "LeaderHero") != null,
                            HasClan = clan != null,
                            ClanEliminated = B(clan, "IsEliminated"),
                        };
                        BadDataSpec.JudgeParty(ps, findings);
                    }
                }
            }

            // ── 家族 ─────────────────────────────────────────────────────────
            if (want("clan"))
            {
                if (Campaign.Current.Clans != null)
                {
                    foreach (object c in Each(Campaign.Current.Clans))
                    {
                        scannedClans++;
                        object culture = P(c, "Culture");
                        BadDataSpec.ClanSnapshot cs = new BadDataSpec.ClanSnapshot
                        {
                            Name = S(c, "Name"),
                            IsEliminated = B(c, "IsEliminated"),
                            HeroCount = CountOf(P(c, "Heroes")),
                            HasLeader = P(c, "Leader") != null,
                            HasKingdom = P(c, "Kingdom") != null,
                            SettlementCount = CountOf(P(c, "Settlements")),
                            PartyCount = CountPartyOfClan(c),
                            // ★ 真机实测补上：不排除匪帮/小家族 ⇒ 会误报 9 条
                            //   （劫匪/逃兵/海寇/山贼/… 本来就没有 Hero）。
                            //   两个成员都**反射实测存在**：Clan.IsMinorFaction、Culture.IsBandit。
                            IsMinorFaction = B(c, "IsMinorFaction"),
                            IsBandit = B(culture, "IsBandit"),
                        };
                        BadDataSpec.JudgeClan(cs, findings);
                    }
                }
                else { skipped.Add("Clans=null"); }
            }

            // ── 王国 + 军团（Army 挂在 Kingdom 上 —— 实测 `Campaign.Armies` 不存在）──
            if (want("kingdom") || want("army"))
            {
                if (Campaign.Current.Kingdoms != null)
                {
                    foreach (object k in Each(Campaign.Current.Kingdoms))
                    {
                        if (want("kingdom"))
                        {
                            scannedKingdoms++;
                            BadDataSpec.KingdomSnapshot ks = new BadDataSpec.KingdomSnapshot
                            {
                                Name = S(k, "Name"),
                                IsEliminated = B(k, "IsEliminated"),
                                ClanCount = CountOf(P(k, "Clans")),
                                HasLeader = P(k, "Leader") != null,
                                SettlementCount = CountOf(P(k, "Settlements")),
                            };
                            BadDataSpec.JudgeKingdom(ks, findings);
                        }
                        if (!want("army")) continue;
                        object armies = P(k, "Armies");
                        if (armies == null) { if (!skipped.Contains("Kingdom.Armies=null")) skipped.Add("Kingdom.Armies=null"); continue; }
                        foreach (object a in Each(armies))
                        {
                            scannedArmies++;
                            object leader = P(a, "LeaderParty");
                            object parties = P(a, "Parties");
                            bool leaderIn = false;
                            if (leader != null)
                            {
                                foreach (object lp in Each(parties))
                                {
                                    if (ReferenceEquals(lp, leader)) { leaderIn = true; break; }
                                }
                            }
                            BadDataSpec.ArmySnapshot asn = new BadDataSpec.ArmySnapshot
                            {
                                Name = ArmyLabel(a, leader),
                                PartyCount = CountOf(parties),
                                HasLeaderParty = leader != null,
                                LeaderPartyInParties = leaderIn,
                                HasKingdom = P(k, "Name") != null,
                            };
                            BadDataSpec.JudgeArmy(asn, findings);
                        }
                    }
                }
                else { skipped.Add("Kingdoms=null"); }
            }

            // ── 任务（真名：Campaign.QuestManager.Quests）────────────────────
            if (want("quest"))
            {
                object qm = P(Campaign.Current, "QuestManager");
                object quests = P(qm, "Quests");
                if (quests == null) { skipped.Add("QuestManager.Quests=null"); }
                else
                {
                    foreach (object q in Each(quests))
                    {
                        scannedQuests++;
                        BadDataSpec.QuestSnapshot qs = new BadDataSpec.QuestSnapshot
                        {
                            // ⚠️ `QuestBase.Name` **不存在**（实测），真名是 `Title`
                            Name = S(q, "Title") ?? S(q, "StringId"),
                            IsOngoing = B(q, "IsOngoing"),
                            HasQuestGiver = P(q, "QuestGiver") != null,
                            StillRegistered = true,   // 本就在 Quests 集合里遍历 ⇒ 恒真
                        };
                        BadDataSpec.JudgeQuest(qs, findings);
                    }
                }
            }

            // ── 物品（玩家队伍的 ItemRoster）──────────────────────────────────
            if (want("item"))
            {
                object main = Hero.MainHero;
                object mainParty = P(main, "PartyBelongedTo");
                object roster = P(mainParty, "ItemRoster");
                if (roster == null) { skipped.Add("MainParty.ItemRoster=null"); }
                else
                {
                    int n = CountOf(roster);
                    for (int i = 0; i < n; i++)
                    {
                        scannedItems++;
                        object item = null;
                        try { item = roster.GetType().GetMethod("GetItemAtIndex").Invoke(roster, new object[] { i }); }
                        catch { }
                        BadDataSpec.ItemSnapshot its = new BadDataSpec.ItemSnapshot
                        {
                            Name = item == null ? null : (S(item, "Name") ?? S(item, "StringId")),
                            // ⚠️ 真机实测修正（2026-10-07）：`GetItemNumber` 的签名是
                            //    **`GetItemNumber(ItemObject item)`** —— 收的是**物品对象**，
                            //    不是索引！第一版传了 index，`MethodInfo.Invoke` 找不到匹配
                            //    重载 ⇒ 抛异常 ⇒ 被 catch 成 0 ⇒ **60 条物品全被误报成"数量≤0"**。
                            //    ⇒ 必须传 `item` 本身。
                            Count = GetItemNumber(roster, item),
                            HasItemObject = item != null,
                        };
                        BadDataSpec.JudgeItem(its, findings);
                    }
                }
            }

            // ── 出报告 ───────────────────────────────────────────────────────
            double ms = (DateTime.UtcNow - t0).TotalMilliseconds;
            if (limit > 0 && findings.Count > limit)
            {
                // 只截断**报告**，不改扫描量（并在 note 里说清截断了，避免误读成"就这么多"）
                skipped.Add("findings 截断至 " + limit + "（实际 " + findings.Count + " 条）");
                findings = findings.GetRange(0, limit);
            }
            StringBuilder sb = new StringBuilder(BadDataSpec.BuildSummaryJson(
                findings, scannedParties, scannedClans, scannedKingdoms,
                scannedArmies, scannedQuests, scannedItems, ms));
            // 注入 skipped：**扫描不了的类别必须显式说**，
            // 否则"某一类异常导致整类 0 条"会被误读成"这一类很干净"。
            if (skipped.Count > 0)
            {
                List<string> qs2 = new List<string>();
                foreach (string s in skipped) qs2.Add(Protocol.Q(s));
                sb.Insert(sb.Length - 1, ",\"skipped\":[" + string.Join(",", qs2.ToArray()) + "]");
            }
            return Protocol.Success(id, sb.ToString());
        }

        /// <summary>家族的部队数（`Clan` 上没有现成计数 ⇒ 从全局部队里按其 ActualClan 数）。</summary>
        private static int CountPartyOfClan(object clan)
        {
            int n = 0;
            foreach (object p in Each(Campaign.Current.MobileParties))
            {
                object pc = P(p, "ActualClan");
                if (pc != null && ReferenceEquals(pc, clan)) n++;
            }
            return n;
        }

        /// <summary>军团的人类可读标签（Army 没有 Name ⇒ 用领袖名 + 成员数拼一个）。</summary>
        private static string ArmyLabel(object army, object leaderParty)
        {
            string ln = leaderParty == null ? "" : S(leaderParty, "Name");
            if (string.IsNullOrEmpty(ln)) ln = "(无领袖)";
            return "军团[" + ln + "]";
        }

        /// <summary>
        /// 取某物品在花名册里的数量。
        ///
        /// ⚠️ 反射实测（2026-10-07）：签名是 **`GetItemNumber(ItemObject item)`** ——
        /// 参数是**物品对象**，**不是索引**。第一版传 index 会因找不到匹配重载而抛异常、
        /// 被 catch 成 0，导致 60 条物品全被误报成"数量≤0"（真机抓到的第二个误报）。
        /// ⇒ 这里按 `ItemObject` 参数查找，且**读不到时返回 -1**（而不是 0）——
        /// 0 会让判据以为是"真实数量 0"。
        /// </summary>
        private static int GetItemNumber(object roster, object item)
        {
            if (roster == null || item == null) return -1;
            try
            {
                Type itemType = item.GetType();
                MethodInfo m = null;
                foreach (MethodInfo cand in roster.GetType().GetMethods())
                {
                    if (cand.Name != "GetItemNumber") continue;
                    ParameterInfo[] ps = cand.GetParameters();
                    if (ps.Length == 1 && ps[0].ParameterType.IsAssignableFrom(itemType))
                    {
                        m = cand;
                        break;
                    }
                }
                if (m == null) return -1;
                object v = m.Invoke(roster, new object[] { item });
                return v == null ? -1 : Convert.ToInt32(v, CultureInfo.InvariantCulture);
            }
            catch { return -1; }
        }
    }
}
