using System;
using System.Collections.Generic;
using System.Text;

using TaleWorlds.CampaignSystem;
using TaleWorlds.CampaignSystem.Party;

namespace BlBridge
{
    /// <summary>
    /// L2 #2：inventory/get_inventory（脱壳抄 BUTR/Bannerlord.GABS 的 Tools/InventoryTools.cs 的
    /// inventory/get_inventory —— 只取实现体：MainParty.ItemRoster + Hero.MainHero.Gold 的读法，
    /// 外壳换成我们自己的 CommandPump method + Jmini/Protocol，不引 Lib.GAB）。
    ///
    /// 与上游同款的两个前置检查（缺了就显式失败，不猜）：
    ///   • Campaign.Current == null  → no_campaign（主菜单 / 自定义战斗里没有战役上下文）
    ///   • MobileParty.MainParty == null → no_main_party
    /// 只读、零副作用；只读队伍库存，不碰角色装备栏（上游同款边界，将来单独做）。
    /// </summary>
    internal static class InventoryProbe
    {
        internal static string HandleGetInventory(string id, string raw)
        {
            try
            {
                int limit = Jmini.Int(raw, "limit", 50);
                if (limit <= 0) limit = 50;

                if (Campaign.Current == null)
                {
                    return Protocol.Failure(id, "no_campaign",
                        "没有战役上下文 —— 主菜单 / 自定义战斗里读不了库存（需要进战役存档）", false);
                }
                MobileParty party = MobileParty.MainParty;
                if (party == null)
                {
                    return Protocol.Failure(id, "no_main_party", "战役里没有主队伍（MainParty 为空）", false);
                }
                Hero hero = Hero.MainHero;

                // ItemRoster / ItemRosterElement 的类型随版本换过命名空间，这里一律 var + 索引访问，
                // 不写类型名（少一个会随游戏升级断的编译点；字段读取与上游一致：
                // IsEmpty / EquipmentElement.Item / Amount → Name / StringId / ItemType / Value / Weight / Tier）
                var roster = party.ItemRoster;
                List<string> items = new List<string>();
                int total = 0;
                for (int i = 0; i < roster.Count; i++)
                {
                    var element = roster[i];
                    if (element.IsEmpty || element.EquipmentElement.Item == null) continue;
                    total++;
                    if (items.Count >= limit) continue;
                    var item = element.EquipmentElement.Item;
                    StringBuilder one = new StringBuilder();
                    one.Append('{');
                    one.Append("\"name\":").Append(Protocol.Q(item.Name == null ? "" : item.Name.ToString()));
                    one.Append(",\"id\":").Append(Protocol.Q(item.StringId == null ? "" : item.StringId));
                    one.Append(",\"quantity\":").Append(element.Amount);
                    one.Append(",\"type\":").Append(Protocol.Q(item.ItemType.ToString()));
                    one.Append(",\"value\":").Append(item.Value);
                    one.Append(",\"weight\":").Append(
                        Math.Round(item.Weight, 2).ToString(System.Globalization.CultureInfo.InvariantCulture));
                    one.Append(",\"tier\":").Append(Protocol.Q(item.Tier.ToString()));
                    one.Append('}');
                    items.Add(one.ToString());
                }

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"gold\":").Append(hero == null ? 0 : hero.Gold);
                sb.Append(",\"itemCount\":").Append(items.Count);
                sb.Append(",\"totalElements\":").Append(total);
                sb.Append(",\"limit\":").Append(limit);
                sb.Append(",\"items\":[").Append(string.Join(",", items.ToArray())).Append(']');
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "get_inventory_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }
    }
}
