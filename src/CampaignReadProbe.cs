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
    }
}
