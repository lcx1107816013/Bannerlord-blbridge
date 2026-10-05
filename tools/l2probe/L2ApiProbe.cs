// L2 API 漂移探针
// ============================================================================
// 目的：上游 Bannerlord.GABS 声明支持的游戏版本是 v1.3.15 / v1.3.13 / v1.2.12，
//       本机是 v1.4.8 —— **从没验证过**。本探针把上游的**真实调用点**（注释里标了出处
//       仓库文件:行）搬到 1.4.8 的引用程序集上编译：
//         编译通过      = 该 API 在 1.4.8 上存在且签名兼容 ⇒ 可抄
//         编译失败      = 漂移，错误信息就是判据 ⇒ 抄之前必须先解决
//       **编译器就是判据**：不需要跑游戏，不需要装 Lib.GAB。
//
// 覆盖范围（诚实标注）：只覆盖下面的 CASE 清单，**不是**对上游代码的全量验证。
//       上游 PartyTools.cs 里有 10 处 `#if v1313`、SubModule/CoreTools/GauntletUITools 各 1 处，
//       共 13 处版本分叉 —— 每处都是一个潜在漂移点，本探针只挑了每处的主干写法。
//
// 怎么跑：tools\..  见同目录 run_probe.py（自动收集游戏引用程序集 + 定位 Roslyn csc）
// ============================================================================

using System;
using System.Collections.Generic;
using System.Linq;

// ── 上游用到的命名空间（Tools_GauntletUITools.cs:14 / Tools_InventoryTools.cs:9,11 /
//    Tools_PartyTools.cs:13 / Tools_ConversationTools.cs:14 / Tools_BarterTools.cs:8）──
using TaleWorlds.CampaignSystem;
using TaleWorlds.CampaignSystem.Actions;
using TaleWorlds.CampaignSystem.BarterSystem;
using TaleWorlds.CampaignSystem.Conversation;
using TaleWorlds.CampaignSystem.GameMenus;
using TaleWorlds.CampaignSystem.GameState;
using TaleWorlds.CampaignSystem.Party;
using TaleWorlds.CampaignSystem.Settlements;
using TaleWorlds.Core;
using TaleWorlds.Engine.GauntletUI;
using TaleWorlds.GauntletUI.BaseTypes;
using TaleWorlds.Library;
using TaleWorlds.MountAndBlade;
using TaleWorlds.ScreenSystem;

namespace BlBridge.L2Probe
{
    public static class L2ApiProbe
    {
        // ───────── CASE P01 命名空间 TaleWorlds.CampaignSystem.BarterSystem ─────────
        // 出处：Tools_BarterTools.cs:8 `using TaleWorlds.CampaignSystem.BarterSystem;`
        // 判据：上方 using 能编过 ⇒ 该命名空间在 1.4.8 存在
        public static Type P01()
        {
            return typeof(BarterData);
        }

        // ───────── CASE P02 ConversationManager 的属性与静态方法 ─────────
        // 出处：Tools_CoreTools.cs:134 `Campaign.Current?.ConversationManager?.IsConversationInProgress == true`
        //       Tools_ConversationTools.cs:48 `ConversationManager.GetPersuasionIsActive()`
        //       Tools_ConversationTools.cs:299-300 `ConversationManager.GetPersuasionProgress()` / `GetPersuasionGoalValue()`
        // ⚠️ 这两个 GetPersuasion* 在 1.4.8 返回 **float**（上游用 `var` 接，所以不受影响）；
        //    抄的时候写成 int 会 CS0266 —— 这是"抄录注意事项"，不是 API 漂移。
        public static object P02()
        {
            ConversationManager cm = Campaign.Current.ConversationManager;
            bool busy = cm.IsConversationInProgress;
            bool persuasion = ConversationManager.GetPersuasionIsActive();
            float progress = ConversationManager.GetPersuasionProgress();
            float goal = ConversationManager.GetPersuasionGoalValue();
            return new { cm, busy, persuasion, progress, goal };
        }

        // ───────── CASE P03 GameMenuManager.NextGameMenuId ─────────
        // 出处：Tools_CoreTools.cs:81 `Campaign.Current.GameMenuManager?.NextGameMenuId != null`
        public static string P03()
        {
            return Campaign.Current.GameMenuManager.NextGameMenuId;
        }

        // ───────── CASE P04 CampaignTime ─────────
        // 出处：Tools_CoreTools.cs:278 `CampaignTime.Now`；:286 `CampaignTime.Now.GetSeasonOfYear`
        public static object P04()
        {
            CampaignTime now = CampaignTime.Now;
            return new { now = now.ToString(), season = now.GetSeasonOfYear.ToString() };
        }

        // ───────── CASE P05 战役时间控制（读写 + 四个枚举值）─────────
        // 出处：Tools_CoreTools.cs:308-338 `Campaign.Current.TimeControlMode == CampaignTimeControlMode.Stop`
        //       以及 UnstoppableFastForward / StoppableFastForward / FastForwardStop / UnstoppablePlay
        public static void P05(bool fast)
        {
            bool paused = Campaign.Current.TimeControlMode == CampaignTimeControlMode.Stop;
            bool speedUp = Campaign.Current.TimeControlMode == CampaignTimeControlMode.UnstoppableFastForward
                        || Campaign.Current.TimeControlMode == CampaignTimeControlMode.StoppableFastForward
                        || Campaign.Current.TimeControlMode == CampaignTimeControlMode.FastForwardStop
                        || Campaign.Current.TimeControlMode == CampaignTimeControlMode.UnstoppablePlay;
            Campaign.Current.TimeControlMode = fast
                ? CampaignTimeControlMode.UnstoppableFastForward
                : CampaignTimeControlMode.Stop;
            if (paused && speedUp) { }
        }

        // ───────── CASE P06 Game.Current.CheatMode ─────────
        // 出处：Tools_CoreTools.cs:390 `cheatMode = Game.Current.CheatMode`
        public static bool P06()
        {
            return Game.Current.CheatMode;
        }

        // ───────── CASE P07 队伍背包（遍历 ItemRoster + ItemRosterElement）─────────
        // 出处：Tools_InventoryTools.cs:30 `var party = MobileParty.MainParty;`
        //       :37 `foreach (var element in party.ItemRoster)`
        public static int P07()
        {
            MobileParty party = MobileParty.MainParty;
            int n = 0;
            foreach (ItemRosterElement element in party.ItemRoster)
            {
                n += element.Amount;
            }
            return n;
        }

        // ───────── CASE P08 Hero.MainHero.Gold ─────────
        // 出处：Tools_InventoryTools.cs:68 `gold = Hero.MainHero?.Gold ?? 0`
        public static int P08()
        {
            return Hero.MainHero.Gold;
        }

        // ───────── CASE P09 定居点市价 MarketData.GetPrice ─────────
        // 出处：Tools_InventoryTools.cs:186 `settlement.Town.MarketData?.GetPrice(itemElement.EquipmentElement) ?? 0`
        public static int P09(ItemRosterElement itemElement)
        {
            Settlement settlement = Settlement.CurrentSettlement;
            return settlement.Town.MarketData.GetPrice(itemElement.EquipmentElement);
        }

        // ───────── CASE P10 定居点背包 Settlements 命名空间 + ItemRoster ─────────
        // 出处：Tools_InventoryTools.cs:167 `foreach (var element in settlement.ItemRoster)`
        public static int P10()
        {
            Settlement settlement = Settlement.CurrentSettlement;
            int n = 0;
            foreach (ItemRosterElement e in settlement.ItemRoster)
            {
                n++;
            }
            return n;
        }

        // ───────── CASE P11 全量集合 MobileParty.All / Settlement.All ─────────
        // 出处：Tools_PartyTools.cs:122 `MobileParty.All.FirstOrDefault(p => p.StringId == nameOrId)`
        //       :253 `Settlement.All.FirstOrDefault(s => s.StringId == settlementNameOrId)`
        public static object P11(string id)
        {
            MobileParty p = MobileParty.All.FirstOrDefault(x => x.StringId == id);
            Settlement s = Settlement.All.FirstOrDefault(x => x.StringId == id);
            return new { p, s };
        }

        // ───────── CASE P12 MobileParty.NavigationType + SetMoveGoToPoint（v1.3.13 分支）─────────
        // 出处：Tools_PartyTools.cs:290-291（`#if v1313 || v1315` 分支）
        //       `var target = new CampaignVec2(new Vec2(x, y), true);`
        //       `party.SetMoveGoToPoint(target, MobileParty.NavigationType.Default);`
        public static void P12(float x, float y)
        {
            MobileParty party = MobileParty.MainParty;
            CampaignVec2 target = new CampaignVec2(new Vec2(x, y), true);
            party.SetMoveGoToPoint(target, MobileParty.NavigationType.Default);
        }

        // ───────── CASE P13 party.Ai.SetMoveGoToPoint(Vec2)（v1.3.13 之前的兜底分支）─────────
        // 出处：Tools_PartyTools.cs:293（`#else` 分支）
        public static void P13(float x, float y)
        {
            MobileParty party = MobileParty.MainParty;
            party.Ai.SetMoveGoToPoint(new Vec2(x, y));
        }

        // ───────── CASE P14 BarterManager.Instance ─────────
        // 出处：Tools_BarterTools.cs:118 `var manager = BarterManager.Instance;`
        public static object P14()
        {
            return BarterManager.Instance;
        }

        // ───────── CASE P15 交易开始事件（订阅 + BarterData 参数）─────────
        // 出处：SubModule.cs:111 `Campaign.Current.BarterManager.BarterBegin += Tools.BarterTools.OnBarterBegin;`
        //       Tools_BarterTools.cs:20 `public static void OnBarterBegin(BarterData args)`
        public static void P15()
        {
            Campaign.Current.BarterManager.BarterBegin += (BarterData args) => { _last = args; };
        }
        private static BarterData _last;

        // ───────── CASE P16 存档 SaveHandler.SaveAs ─────────
        // 出处：MainThreadDispatcher.cs:111 `Campaign.Current?.SaveHandler.SaveAs(saveName);`
        public static void P16(string saveName)
        {
            Campaign.Current.SaveHandler.SaveAs(saveName);
        }

        // ───────── CASE P17 ScreenManager.TopScreen ─────────
        // 出处：Tools_CoreTools.cs:140 `var screen = TaleWorlds.ScreenSystem.ScreenManager.TopScreen;`
        //       Tools_GauntletUITools.cs:48 `var screen = ScreenManager.TopScreen;`
        public static string P17()
        {
            ScreenBase screen = ScreenManager.TopScreen;
            return screen.GetType().Name;
        }

        // ───────── CASE P18 GauntletLayer（TaleWorlds.Engine.GauntletUI）─────────
        // 出处：Tools_GauntletUITools.cs:65 `if (layer is GauntletLayer gauntletLayer)`
        public static Type P18()
        {
            return typeof(GauntletLayer);
        }

        // ───────── CASE P19 ScreenBase.Layers 枚举（上游遍历 layers 找 GauntletLayer）─────────
        // 出处：Tools_GauntletUITools.cs:63-67 附近 `foreach (var layer in screen.Layers) { if (layer is GauntletLayer ...) }`
        public static int P19()
        {
            ScreenBase screen = ScreenManager.TopScreen;
            int n = 0;
            foreach (ScreenLayer layer in screen.Layers)
            {
                if (layer is GauntletLayer) { n++; }
            }
            return n;
        }

        // ───────── CASE P20 Gauntlet 控件树（Widget 的 Children 遍历）─────────
        // 出处：Tools_GauntletUITools.cs:647-655 `FindWidgetById(Widget root, string id)` 递归遍历 Children
        public static Widget P20(Widget root, string id)
        {
            if (root == null) { return null; }
            foreach (Widget child in root.Children)
            {
                Widget hit = P20(child, id);
                if (hit != null) { return hit; }
            }
            return null;
        }
        // 出处：Tools_GauntletUITools.cs:238 `found = FindWidgetById(rootWidget, searchText);`
        public static Widget P20b(Widget root, string id) { return P20(root, id); }

        // ───────── CASE P21 读取 ViewModel 的字符串属性（上游 ui/get_viewmodel_property 的落点）─────────
        // 出处：Tools_GauntletUITools.cs 里对 GauntletLayer 的 _movieIdentifiers 反射（:25）
        //       与 ui/get_viewmodel_property 的实现（点号路径 + 数组下标）
        // 本 CASE 只验"类型存在 + 能反射到非公开字段的 API 可用"，字段名是否存在要靠运行期（见 run_probe.py 的第二层）
        public static object P21(GauntletLayer layer)
        {
            var field = typeof(GauntletLayer).GetField("_movieIdentifiers",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            return field;
        }

        // ───────── CASE P22 菜单管理器（GameMenuManager 的菜单状态 + 写类型名）─────────
        // 出处：Tools_MenuTools.cs:32 `var menuManager = Campaign.Current.GameMenuManager;`
        //       :45 `menuManager.GetVirtualGameMenuOption(menuContext, i)?.IdString`
        //       Tools_CoreTools.cs:212 `Campaign.Current.GameMenuManager?.NextGameMenuId != null`
        // ⚠️ 1.4.8 里 GameMenuManager 在 `…CampaignSystem.GameMenus`、
        //    MenuContext 在 `…CampaignSystem.GameState` —— **写类型名要把这两个 using 补上**。
        //    上游一路用 `var` / 属性模式（`menuContext is { GameMenu: not null }`）回避了这个问题，
        //    所以它的 `using` 清单只有 TaleWorlds.CampaignSystem 一条。
        public static object P22(MenuContext ctx)
        {
            GameMenuManager m = Campaign.Current.GameMenuManager;
            string next = m.NextGameMenuId;
            GameMenuOption opt = m.GetVirtualGameMenuOption(ctx, 0);
            return new { next = next, firstOption = opt.IdString };
        }

        // ───────── 汇总入口（避免"未使用方法"警告，同时保证所有 CASE 都被编译）─────────
        public static void CompileAll()
        {
            P01(); P03(); P04(); P06(); P07(); P08(); P10(); P11("x"); P14(); P17(); P18();
            P22(null);
            P05(false); P12(0, 0); P13(0, 0); P15(); P16("probe"); P19(); P20(null, "x");
            P02(); P09(default(ItemRosterElement)); P21(null);
        }
    }
}
