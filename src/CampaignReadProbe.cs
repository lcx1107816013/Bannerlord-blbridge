using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Text;

using TaleWorlds.CampaignSystem;
using TaleWorlds.CampaignSystem.Party;
using TaleWorlds.CampaignSystem.Settlements;

namespace BlBridge
{
    /// <summary>
    /// A 阶段 · 战役只读遥测（控制面，对应战场的 get_inventory / list_battles）。
    ///
    /// 设计取舍（与 InventoryProbe 一致，并强化）：
    ///   • 全部只读、零副作用；任何字段缺失都不会炸游戏 —— 用反射取值，
    ///     属性名随游戏版本漂移时优雅降级为 null（"多返回 null 好过少一个编译点"，
    ///     见 AGENTS.md 的版本容错原则）。
    ///   • 只依赖高度稳定的集合根（Campaign.Current.Clans / Kingdoms / Settlements /
    ///     MobileParties）+ 静态根（Hero.MainHero / Clan.PlayerClan / CampaignTime.Now）；
    ///     实体字段一律经 P()/PS() 反射读取。
    ///   • 前置检查：Campaign.Current == null ⇒ no_campaign（主菜单 / 自定义战斗如实报，不猜）。
    ///
    /// 覆盖：campaign_overview / list_kingdoms / list_clans / list_settlements /
    ///       list_parties / campaign_log。
    /// </summary>
    internal static class CampaignReadProbe
    {
        // ── 反射取值（属性优先、public 字段兜底；缺失 ⇒ null；读取抛异常 ⇒ null）─────
        // 关键教训（2026-09-28 真机）：Settlement.Town / Village / Hideout 在 1.4.8 里是
        // **public 字段**而非属性 —— 只查 GetProperty 会全部 miss，城镇经济字段因此整体缺失。
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

        // 多候选名（不同版本字段名不同）：返回第一个命中的非空值
        private static object PAny(object o, params string[] names)
        {
            if (o == null) return null;
            foreach (string n in names)
            {
                object v = P(o, n);
                if (v != null) return v;
            }
            return null;
        }

        // 把任意值安全转成 JSON 片段（null / bool / 数字 / 枚举 / 字符串）
        private static string JVal(object v)
        {
            if (v == null) return "null";
            if (v is bool b) return b ? "true" : "false";
            if (v is string s) return Protocol.Q(s);
            if (v is Enum) return Protocol.Q(v.ToString());
            if (v is IFormattable f) return f.ToString(null, CultureInfo.InvariantCulture);
            return Protocol.Q(v.ToString());
        }

        // 数字统一走 Convert：拆箱必须精确匹配类型（boxed float 直接 (double) 会 InvalidCastException）
        private static double Num(object v)
        {
            if (v == null) return 0;
            try { return Convert.ToDouble(v, CultureInfo.InvariantCulture); }
            catch { return 0; }
        }

        // ICollection 计数（MBReadOnlyList<T> / TroopRoster 等都实现）
        private static int CountOf(object o)
        {
            if (o == null) return 0;
            if (o is ICollection c) return c.Count;
            object n = P(o, "Count");
            if (n is int i) return i;
            return 0;
        }

        private static string Guard(string id, out bool inCampaign)
        {
            inCampaign = Campaign.Current != null;
            if (!inCampaign)
                return Protocol.Failure(id, "no_campaign",
                    "没有战役上下文 —— 主菜单 / 自定义战斗里读不了战役数据（需要进战役存档）", false);
            return null;
        }

        // ── 全局概览 ───────────────────────────────────────────────────────────
        internal static string HandleCampaignOverview(string id, string raw)
        {
            bool ok;
            string g = Guard(id, out ok);
            if (g != null) return g;
            try
            {
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"inCampaign\":true");
                sb.Append(",\"clans\":").Append(Campaign.Current.Clans.Count);
                sb.Append(",\"kingdoms\":").Append(Campaign.Current.Kingdoms.Count);
                sb.Append(",\"settlements\":").Append(Campaign.Current.Settlements.Count);
                sb.Append(",\"mobileParties\":").Append(Campaign.Current.MobileParties.Count);
                sb.Append(",\"campaignDays\":").Append(
                    CampaignTime.Now.ToDays.ToString("F4", CultureInfo.InvariantCulture));
                sb.Append(",\"timeControlMode\":").Append(
                    Protocol.Q(Campaign.Current.TimeControlMode.ToString()));
                Hero hero = Hero.MainHero;
                sb.Append(",\"gold\":").Append(hero == null ? "0" : Num(P(hero, "Gold")).ToString("0", CultureInfo.InvariantCulture));
                Clan pc = Clan.PlayerClan;
                sb.Append(",\"influence\":").Append(pc == null ? "0" : Num(P(pc, "Influence")).ToString("0.##", CultureInfo.InvariantCulture));
                sb.Append(",\"playerClan\":").Append(Protocol.Q(pc == null ? "" : (pc.Name == null ? "" : pc.Name.ToString())));
                object pk = P(pc, "Kingdom");
                sb.Append(",\"playerKingdom\":").Append(
                    Protocol.Q(pk == null ? "" : (P(pk, "Name") == null ? "" : P(pk, "Name").ToString())));
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "campaign_overview_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        // ── 王国列表 ───────────────────────────────────────────────────────────
        internal static string HandleListKingdoms(string id, string raw)
        {
            bool ok;
            string g = Guard(id, out ok);
            if (g != null) return g;
            try
            {
                int limit = Jmini.Int(raw, "limit", 0);
                List<string> rows = new List<string>();
                int i = 0;
                foreach (var k in Campaign.Current.Kingdoms)
                {
                    if (limit > 0 && i >= limit) break;
                    i++;
                    object clans = P(k, "Clans");
                    object ruling = P(k, "RulingClan");
                    object leader = P(ruling, "Leader");
                    StringBuilder one = new StringBuilder();
                    one.Append('{');
                    one.Append("\"name\":").Append(Protocol.Q(k.Name == null ? "" : k.Name.ToString()));
                    one.Append(",\"stringId\":").Append(Protocol.Q(k.StringId ?? ""));
                    one.Append(",\"rulingClan\":").Append(Protocol.Q(ruling == null ? "" : (P(ruling, "Name") == null ? "" : P(ruling, "Name").ToString())));
                    one.Append(",\"leader\":").Append(Protocol.Q(leader == null ? "" : (P(leader, "Name") == null ? "" : P(leader, "Name").ToString())));
                    one.Append(",\"clanCount\":").Append(CountOf(clans));
                    one.Append(",\"gold\":").Append(JVal(P(k, "Gold")));
                    one.Append(",\"isKingdom\":").Append(JVal(PAny(k, "IsKingdomFaction", "IsKingdom")));
                    one.Append('}');
                    rows.Add(one.ToString());
                }
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"count\":").Append(rows.Count);
                sb.Append(",\"kingdoms\":[").Append(string.Join(",", rows.ToArray())).Append(']');
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "list_kingdoms_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        // ── 家族列表 ───────────────────────────────────────────────────────────
        internal static string HandleListClans(string id, string raw)
        {
            bool ok;
            string g = Guard(id, out ok);
            if (g != null) return g;
            try
            {
                int limit = Jmini.Int(raw, "limit", 0);
                List<string> rows = new List<string>();
                int i = 0;
                foreach (var c in Campaign.Current.Clans)
                {
                    if (limit > 0 && i >= limit) break;
                    i++;
                    object king = P(c, "Kingdom");
                    object leader = P(c, "Leader");
                    StringBuilder one = new StringBuilder();
                    one.Append('{');
                    one.Append("\"name\":").Append(Protocol.Q(c.Name == null ? "" : c.Name.ToString()));
                    one.Append(",\"stringId\":").Append(Protocol.Q(c.StringId ?? ""));
                    one.Append(",\"tier\":").Append(JVal(P(c, "Tier")));
                    one.Append(",\"gold\":").Append(JVal(P(c, "Gold")));
                    one.Append(",\"influence\":").Append(JVal(P(c, "Influence")));
                    one.Append(",\"kingdom\":").Append(Protocol.Q(king == null ? "" : (P(king, "Name") == null ? "" : P(king, "Name").ToString())));
                    one.Append(",\"leader\":").Append(Protocol.Q(leader == null ? "" : (P(leader, "Name") == null ? "" : P(leader, "Name").ToString())));
                    one.Append(",\"isMinor\":").Append(JVal(P(c, "IsMinorFaction")));
                    one.Append(",\"isEliminated\":").Append(JVal(P(c, "IsEliminated")));
                    one.Append(",\"fiefCount\":").Append(CountOf(P(c, "Fiefs")));
                    one.Append(",\"partyCount\":").Append(CountOf(PAny(c, "Parties", "WarPartyComponents")));
                    one.Append('}');
                    rows.Add(one.ToString());
                }
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"count\":").Append(rows.Count);
                sb.Append(",\"clans\":[").Append(string.Join(",", rows.ToArray())).Append(']');
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "list_clans_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        // ── 封地列表 ───────────────────────────────────────────────────────────
        internal static string HandleListSettlements(string id, string raw)
        {
            bool ok;
            string g = Guard(id, out ok);
            if (g != null) return g;
            try
            {
                int limit = Jmini.Int(raw, "limit", 0);
                List<string> rows = new List<string>();
                int i = 0;
                foreach (var s in Campaign.Current.Settlements)
                {
                    if (limit > 0 && i >= limit) break;
                    i++;
                    string type = "other";
                    if (s.IsTown) type = "town";
                    else if (s.IsCastle) type = "castle";
                    else if (s.IsVillage) type = "village";
                    else if (s.IsHideout) type = "hideout";
                    object owner = P(s, "OwnerClan");
                    object faction = P(s, "MapFaction");
                    object town = P(s, "Town");
                    object village = P(s, "Village");
                    StringBuilder one = new StringBuilder();
                    one.Append('{');
                    one.Append("\"name\":").Append(Protocol.Q(s.Name == null ? "" : s.Name.ToString()));
                    one.Append(",\"stringId\":").Append(Protocol.Q(s.StringId ?? ""));
                    one.Append(",\"type\":").Append(Protocol.Q(type));
                    one.Append(",\"ownerClan\":").Append(Protocol.Q(owner == null ? "" : (P(owner, "Name") == null ? "" : P(owner, "Name").ToString())));
                    one.Append(",\"mapFaction\":").Append(Protocol.Q(faction == null ? "" : (P(faction, "Name") == null ? "" : P(faction, "Name").ToString())));
                    if (town != null)
                    {
                        one.Append(",\"prosperity\":").Append(JVal(P(town, "Prosperity")));
                        one.Append(",\"loyalty\":").Append(JVal(P(town, "Loyalty")));
                        one.Append(",\"security\":").Append(JVal(P(town, "Security")));
                        one.Append(",\"foodStocks\":").Append(JVal(P(town, "FoodStocks")));
                        object garrison = P(town, "GarrisonParty");
                        if (garrison != null)
                            one.Append(",\"garrisonSize\":").Append(CountOf(P(garrison, "MemberRoster")));
                    }
                    if (village != null)
                    {
                        one.Append(",\"hearth\":").Append(JVal(P(village, "Hearth")));
                    }
                    one.Append('}');
                    rows.Add(one.ToString());
                }
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"count\":").Append(rows.Count);
                sb.Append(",\"settlements\":[").Append(string.Join(",", rows.ToArray())).Append(']');
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "list_settlements_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        // ── 队伍列表（地图上的 MobileParty）──────────────────────────────────────
        internal static string HandleListParties(string id, string raw)
        {
            bool ok;
            string g = Guard(id, out ok);
            if (g != null) return g;
            try
            {
                int limit = Jmini.Int(raw, "limit", 0);
                List<string> rows = new List<string>();
                int i = 0;
                foreach (var p in Campaign.Current.MobileParties)
                {
                    if (limit > 0 && i >= limit) break;
                    i++;
                    object leader = P(p, "LeaderHero");
                    object faction = P(p, "MapFaction");
                    object roster = P(p, "MemberRoster");
                    // 1.4.8 反编译确认：当前 AI 行为 = ShortTermBehavior（枚举 AiBehavior）
                    object ai = PAny(p, "ShortTermBehavior", "DefaultBehavior", "CurrentAiBehavior", "AiBehavior");
                    object tgtSettle = PAny(p, "TargetSettlement", "ShortTermTargetSettlement");
                    object tgtParty = PAny(p, "TargetParty", "ShortTermTargetParty", "MoveTargetParty");
                    // 1.4.8 没有 Strength/PartyType/Gold 属性：战力在 Party.Strength，金币是 PartyTradeGold，
                    // 类型从组件对象推（WarPartyComponent / VillagerPartyComponent / ... 均为属性）
                    object partyBase = P(p, "Party");
                    object strength = PAny(p, "Strength") ?? P(partyBase, "Strength");
                    string kind = "other";
                    if (P(p, "WarPartyComponent") != null) kind = "war";
                    else if (P(p, "VillagerPartyComponent") != null) kind = "villager";
                    else if (P(p, "CaravanPartyComponent") != null) kind = "caravan";
                    else if (P(p, "BanditPartyComponent") != null) kind = "bandit";
                    else if (P(p, "GarrisonPartyComponent") != null) kind = "garrison";
                    else if (P(p, "PatrolPartyComponent") != null) kind = "patrol";
                    else if (JVal(P(p, "IsMilitia")) == "true") kind = "militia";
                    else if (JVal(P(p, "IsCustomParty")) == "true") kind = "custom";
                    object pos = P(p, "GetPosition2D");
                    StringBuilder one = new StringBuilder();
                    one.Append('{');
                    one.Append("\"name\":").Append(Protocol.Q(p.Name == null ? "" : p.Name.ToString()));
                    one.Append(",\"stringId\":").Append(Protocol.Q(p.StringId ?? ""));
                    one.Append(",\"kind\":").Append(Protocol.Q(kind));
                    one.Append(",\"isMainParty\":").Append(JVal(P(p, "IsMainParty")));
                    one.Append(",\"isLordParty\":").Append(JVal(P(p, "IsLordParty")));
                    one.Append(",\"isCaravan\":").Append(JVal(P(p, "IsCaravan")));
                    one.Append(",\"isGarrison\":").Append(JVal(P(p, "IsGarrison")));
                    one.Append(",\"leader\":").Append(Protocol.Q(leader == null ? "" : (P(leader, "Name") == null ? "" : P(leader, "Name").ToString())));
                    one.Append(",\"mapFaction\":").Append(Protocol.Q(faction == null ? "" : (P(faction, "Name") == null ? "" : P(faction, "Name").ToString())));
                    one.Append(",\"strength\":").Append(JVal(strength));
                    one.Append(",\"morale\":").Append(JVal(P(p, "Morale")));
                    one.Append(",\"gold\":").Append(JVal(PAny(p, "PartyTradeGold")));
                    one.Append(",\"size\":").Append(CountOf(roster));
                    one.Append(",\"aiBehavior\":").Append(JVal(ai));
                    one.Append(",\"targetSettlement\":").Append(Protocol.Q(tgtSettle == null ? "" : (P(tgtSettle, "Name") == null ? "" : P(tgtSettle, "Name").ToString())));
                    one.Append(",\"targetParty\":").Append(Protocol.Q(tgtParty == null ? "" : (P(tgtParty, "Name") == null ? "" : P(tgtParty, "Name").ToString())));
                    if (pos != null)
                    {
                        one.Append(",\"x\":").Append(JVal(P(pos, "X")));
                        one.Append(",\"y\":").Append(JVal(P(pos, "Y")));
                    }
                    one.Append('}');
                    rows.Add(one.ToString());
                }
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"count\":").Append(rows.Count);
                sb.Append(",\"parties\":[").Append(string.Join(",", rows.ToArray())).Append(']');
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "list_parties_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        // ── 战役日志（最近 N 条 LogEntry 快照；实时订阅留待后续）──────────────────
        internal static string HandleCampaignLog(string id, string raw)
        {
            bool ok;
            string g = Guard(id, out ok);
            if (g != null) return g;
            try
            {
                int count = Jmini.Int(raw, "count", 20);
                if (count <= 0 || count > 200) count = 20;
                object hist = P(Campaign.Current, "LogEntryHistory");
                // 1.4.8 反编译确认：公开属性叫 GameActionLogs（内部才是 _logs/Entries）
                object entries = hist == null ? null : PAny(hist, "GameActionLogs", "Entries");
                List<object> list = new List<object>();
                if (entries is IList il)
                {
                    for (int j = il.Count - 1; j >= 0 && list.Count < count; j--)
                        list.Add(il[j]);
                }
                List<string> rows = new List<string>();
                foreach (object e in list)
                {
                    StringBuilder one = new StringBuilder();
                    one.Append('{');
                    one.Append("\"text\":").Append(Protocol.Q(e == null ? "" : e.ToString()));
                    one.Append(",\"time\":").Append(JVal(P(e, "CreationTime")));
                    one.Append('}');
                    rows.Add(one.ToString());
                }
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"count\":").Append(rows.Count);
                sb.Append(",\"note\":\"最近 ").Append(count).Append(" 条日志的快照（非实时流）；实时订阅后续版本提供\"");
                sb.Append(",\"entries\":[").Append(string.Join(",", rows.ToArray())).Append(']');
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "campaign_log_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        // ── B7（2026-10-07，隔壁项目需求）：读英雄**当前血量** ─────────────────
        //
        // ## 为什么需要它
        //
        // 三合一 MOD 的 C6-④「溢出修复」把"当前血量 > 最大血量"的英雄夹回上限；
        // **它的验收判据天然就是"读当前血量与上限并比较"** —— 而此前**没有任何工具能读**：
        //   · `get_entity kind=hero` 读的是**静态索引库**（预计算投影），非运行时状态；
        //   · `bl_list_parties` 只有 morale/gold/size/位置，无 hp；
        //   · `bl_campaign_overview` 只有 gold/influence/counts；
        //   · RBM 的 `Debug.Print` **不落** default*.log。
        // ⇒ 一个改存档数据的修复项**无法做行为验证**。本 handler 补的就是这个缺口。
        //
        // ## API 依据（反射实测 2026-10-07，本机 1.4.8；**不是猜的**）
        //
        //   `Hero.HitPoints` / `MaxHitPoints` : ✅ Int32
        //   `Hero.IsWounded` / `IsDead` / `IsAlive` / `IsFugitive` : ✅ Boolean
        //   `Hero.Name`(TextObject) / `StringId` / `PartyBelongedTo` / `Clan` / `Occupation` : ✅
        //   `Hero.WoundedLimit` : ❌ **不存在**（隔壁建议里提到的字段名不对）
        //
        // ⚠️ 所以**不输出 `woundedLimit`** —— 宁可少一个字段，也不凭空造一个
        //    （造了调用方会以为读到了真实阈值）。重伤状态用实测存在的 `isWounded`/`isAlive` 表达。
        //
        // ## 口径（读结论前必看）
        //
        // · **只读**：不写、不改、不治疗。
        // · `overflow = hitPoints - maxHitPoints`（>0 即溢出）—— 正是 C6-④ 要夹回的量。
        // · **`hpReadable=false` 时 hp/max 的 0 不是真值**，别据此下结论（防"读不到 → 报没溢出"）。
        // · 血量只在**战役**里有意义（战斗里是 `Agent.Health`，另一套）⇒ 无战役上下文报 `no_campaign`。
        // · 不给 id 时默认列**玩家队伍的**英雄（主角 + 同伴）—— 覆盖最常见的问法。
        internal static string HandleGetHero(string id, string raw)
        {
            string g = Guard(id, out _);
            if (g != null) return g;
            try
            {
                string want = Jmini.Str(raw, "heroId", null);
                if (string.IsNullOrEmpty(want)) want = Jmini.Str(raw, "name", null);
                int limit = Jmini.Int(raw, "limit", 0);
                bool all = Jmini.Str(raw, "all", "false") == "true";

                List<object> heroes = new List<object>();
                if (!string.IsNullOrEmpty(want))
                {
                    object hit = FindHeroByStringId(want);
                    if (hit == null) hit = FindHeroByName(want);
                    if (hit == null)
                    {
                        return Protocol.Failure(id, "hero_not_found",
                            "找不到英雄：" + want + "（按 StringId 精确匹配，或名字包含匹配）。"
                            + "注意这与 get_entity 的**静态索引库**不同 —— 那个读预计算表，本工具读运行时状态。",
                            false);
                    }
                    heroes.Add(hit);
                }
                else if (all)
                {
                    heroes.AddRange(AllHeroes());
                }
                else
                {
                    object main = Hero.MainHero;
                    if (main != null) heroes.Add(main);
                    // ★ 真机实测修正（2026-10-07）：**不要**遍历 `MemberRoster.data`
                    //   去找同伴 —— 第一版那么写，实测**静默漏报**：
                    //   存档里玩家部队有 4 个英雄（主角 + 3 个：Wanderer/Lord/Lord），
                    //   默认路径只给出 1 个。**看起来成功、实际少给数据**，是最危险的一类
                    //   （不报错，调用方无从察觉）。
                    //
                    // ⇒ 改用**已验证可靠**的路径：遍历全部英雄，按
                    //   `PartyBelongedTo` 与主角的**同一性**（ReferenceEquals）筛选。
                    //   判据可独立对照：这与 `all=true` 后按 party 名筛出的集合**必然同集**
                    //   （见 tools 里的 `_b7_diag.py` 对照）。
                    //
                    //   为什么这样更稳：`MemberRoster.data` 是**内部字段**，
                    //   名字/结构随版本可能变；而 `Hero.PartyBelongedTo` 是**公开属性**、
                    //   且是本工具已经在用的同一个访问器。
                    object mainParty = P(main, "PartyBelongedTo");
                    if (mainParty != null)
                    {
                        foreach (object h in AllHeroes())
                        {
                            if (ReferenceEquals(h, main)) continue;
                            object hp2 = P(h, "PartyBelongedTo");
                            if (hp2 != null && ReferenceEquals(hp2, mainParty))
                            {
                                heroes.Add(h);
                            }
                        }
                    }
                }

                List<string> rows = new List<string>();
                int i = 0;
                int overflow = 0;
                foreach (object h in heroes)
                {
                    if (limit > 0 && i >= limit) break;
                    i++;
                    object hpO = P(h, "HitPoints");
                    object maxO = P(h, "MaxHitPoints");
                    int hp = (int)Num(hpO);
                    int max = (int)Num(maxO);
                    // ★ 只在两个值**都读到了**时才判溢出：
                    //   读不到时 hp/max 都是 0，照判会得出"没溢出"的**假结论**。
                    bool hasHp = hpO != null && maxO != null;
                    bool isOverflow = hasHp && max > 0 && hp > max;
                    if (isOverflow) overflow++;
                    object clan = P(h, "Clan");
                    object party = P(h, "PartyBelongedTo");
                    object nameO = P(h, "Name");
                    StringBuilder one = new StringBuilder();
                    one.Append('{');
                    // ⚠️ 真机实测修正（2026-10-07）：`JVal` **自带** JSON 引号
                    //    （它是"把任意值安全转成 JSON 片段"），第一版又包了层 `Protocol.Q`
                    //    ⇒ 输出成 `"stringId":""main_hero""`（**双重引号**），
                    //    直接把它回传当 `heroId` 就会匹配失败。
                    //    ⇒ 属性值只用 `JVal`；**只有**需要手动取字段（name/clan/party）时
                    //      才先 `.ToString()` 再 `Protocol.Q`（此时是纯字符串，无引号）。
                    one.Append("\"stringId\":").Append(JVal(P(h, "StringId")));
                    one.Append(",\"name\":").Append(Protocol.Q(nameO == null ? "" : nameO.ToString()));
                    one.Append(",\"hitPoints\":").Append(hpO == null ? "null" : hp.ToString());
                    one.Append(",\"maxHitPoints\":").Append(maxO == null ? "null" : max.ToString());
                    one.Append(",\"hpReadable\":").Append(hasHp ? "true" : "false");
                    // 溢出量由工具算好（也避免调用方把符号方向算反）
                    one.Append(",\"overflow\":").Append(hasHp && max > 0
                        ? (hp > max ? (hp - max).ToString() : "0") : "null");
                    one.Append(",\"isOverflow\":").Append(isOverflow ? "true" : "false");
                    one.Append(",\"isWounded\":").Append(JVal(P(h, "IsWounded")));
                    one.Append(",\"isDead\":").Append(JVal(P(h, "IsDead")));
                    one.Append(",\"isAlive\":").Append(JVal(P(h, "IsAlive")));
                    one.Append(",\"isFugitive\":").Append(JVal(P(h, "IsFugitive")));
                    one.Append(",\"occupation\":").Append(JVal(P(h, "Occupation")));
                    one.Append(",\"clan\":").Append(Protocol.Q(clan == null ? "" : (P(clan, "Name") == null ? "" : P(clan, "Name").ToString())));
                    one.Append(",\"party\":").Append(Protocol.Q(party == null ? "" : (P(party, "Name") == null ? "" : P(party, "Name").ToString())));
                    one.Append('}');
                    rows.Add(one.ToString());
                }

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"count\":").Append(rows.Count);
                sb.Append(",\"overflowCount\":").Append(overflow);
                sb.Append(",\"heroes\":[").Append(string.Join(",", rows.ToArray())).Append(']');
                sb.Append(",\"note\":").Append(Protocol.Q(
                    "只读运行时血量。overflow = hitPoints - maxHitPoints（>0 即溢出，正是 C6-④ 要夹回的量）；"
                    + "isOverflow 是同一判断的布尔形式。hpReadable=false 表示该字段没读到"
                    + "（**此时 hp/max 的 0 不是真值**，别据此下结论）。"
                    + "⚠️ 本工具**不提供 woundedLimit** —— `Hero.WoundedLimit` 在 1.4.8 上"
                    + "**实测不存在**，宁缺勿造；重伤状态请看 isWounded/isAlive。"));
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "get_hero_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        // ⚠️ 这里曾有 `MainPartyCompanions()`（遍历 `MemberRoster.data` 找同伴）——
        //    **已删除**。真机实测它**静默漏报**（部队里 4 个英雄只给出 1 个），
        //    见 `HandleGetHero` 里的注释：改用 `Hero.PartyBelongedTo` + ReferenceEquals 筛选。

        /// <summary>按 StringId 精确找英雄（只读）。</summary>
        private static object FindHeroByStringId(string sid)
        {
            foreach (object h in AllHeroes())
            {
                object idv = P(h, "StringId");
                if (idv != null && string.Equals(idv.ToString(), sid, StringComparison.Ordinal))
                {
                    return h;
                }
            }
            return null;
        }

        /// <summary>按名字**包含**找英雄（大小写不敏感；命中多个时返回第一个）。</summary>
        private static object FindHeroByName(string nameFragment)
        {
            foreach (object h in AllHeroes())
            {
                object n = P(h, "Name");
                if (n != null
                    && n.ToString().IndexOf(nameFragment, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    return h;
                }
            }
            return null;
        }

        /// <summary>全部英雄（`AllAliveHeroes` 只含活着，故把 `DeadOrDisabledHeroes` 也并进来）。</summary>
        private static List<object> AllHeroes()
        {
            List<object> outList = new List<object>();
            foreach (string prop in new string[] { "AllAliveHeroes", "DeadOrDisabledHeroes" })
            {
                try
                {
                    PropertyInfo pi = typeof(Hero).GetProperty(prop,
                        BindingFlags.Public | BindingFlags.Static);
                    if (pi == null) continue;
                    IEnumerable en = pi.GetValue(null, null) as IEnumerable;
                    if (en == null) continue;
                    foreach (object h in en)
                    {
                        if (h != null) outList.Add(h);
                    }
                }
                catch
                {
                }
            }
            return outList;
        }
    }
}
