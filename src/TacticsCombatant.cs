using System;
using TaleWorlds.Core;
using TaleWorlds.Localization;
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    /// <summary>
    /// 只读包装：把一方 combatant 的**战术档位**换成外部指定值，其它成员原样转发。
    ///
    /// 为什么是"包装"而不是"子类覆盖"（2026-10-05 取证）：
    ///   `CustomBattleCombatant` 是 public 非 sealed，但它的 `GetTacticsSkillAmount()`
    ///   在元数据里是 **virtual final**（= sealed override）⇒ **子类无法覆盖**。
    ///
    /// 为什么包装是安全的（同一批取证，两条独立证据）：
    ///   1. `MissionCombatantsLogic` 的 6 个 ctor 参数里，前 4 个**声明类型全是 `IBattleCombatant`**
    ///      （`IEnumerable&lt;IBattleCombatant&gt; battleCombatants` / `IBattleCombatant playerBattleCombatant`
    ///       / `... defenderLeaderBattleCombatant` / `... attackerLeaderBattleCombatant`），
    ///      全类**没有任何**向 `CustomBattleCombatant` 的下转型 ⇒ 传包装对象不会 InvalidCastException。
    ///   2. 引擎对 combatant 的用法只有 4 处，包装全部如实转发：
    ///      `Teams.Add(side, PrimaryColorPair.Item1, PrimaryColorPair.Item2, Banner)`（两处）、
    ///      `GetTacticsSkillAmount()`（`EarlyStart` 里 `Max` 出来决定加哪些 TacticOption）、
    ///      以及 `SupportsAllyTeamOnPlayerSide` 里的 `General` / `IsUnderPlayersCommand` /
    ///      `GetNumberOfMissionReadyTroops`。
    ///
    /// 为什么"战术档位"值得做成参数（这才是本文件存在的理由）：
    ///   `CustomBattleCombatant.GetTacticsSkillAmount()` 的实现是
    ///   `_characters.Max(h =&gt; h.GetSkillValue(DefaultSkills.Tactics))` ——
    ///   即**该方所有参战兵种里最高的战术技能值**。
    ///   而 `MissionCombatantsLogic.EarlyStart` 拿这个数**分档**决定给该方加哪些 TacticOption：
    ///     `&lt; 20`  → 只有 `TacticCharge`
    ///     `&gt;= 20` → 追加 `TacticFullScaleAttack`（守方另有 `TacticDefensiveEngagement` / `TacticDefensiveLine`；
    ///                 攻方另有 `TacticRangedHarrassmentOffensive`）
    ///     `&gt;= 50` → 再追加 `TacticFrontalCavalryCharge`（守方另有 `TacticDefensiveRing` / `TacticHoldChokePoint`；
    ///                 攻方另有 `TacticCoordinatedRetreat`）
    ///   ⇒ 这是 EBT README 里"战术等级 0-20 / 20-50 / 50+ 三档"的**引擎真身**。
    ///   而 `ScenarioProbe.ApplyCharge()` 会 `ClearTacticOptions()` 只留 `TacticCharge`
    ///   ⇒ 不覆盖时，整条"战术随战术技能分档"的维度是被抹掉的。
    ///   本包装让档位变成**可设自变量**，同时把引擎原生值写进遥测（见 meta 的 aTacticsNative）。
    ///
    /// 语义：`tactics &lt; 0` ⇒ 完全转发内层（构造方不包即可，这里只是防御性支持）。
    /// 异常安全：全部成员都是纯转发，不新增任何可抛点。
    /// </summary>
    internal sealed class TacticsCombatant : IBattleCombatant
    {
        private readonly IBattleCombatant _inner;
        private readonly int _tactics;

        internal TacticsCombatant(IBattleCombatant inner, int tactics)
        {
            if (inner == null) throw new ArgumentNullException("inner");
            _inner = inner;
            _tactics = tactics;
        }

        public TextObject Name
        {
            get { return _inner.Name; }
        }

        public BattleSideEnum Side
        {
            get { return _inner.Side; }
        }

        public BasicCultureObject BasicCulture
        {
            get { return _inner.BasicCulture; }
        }

        public BasicCharacterObject General
        {
            get { return _inner.General; }
        }

        public Tuple<uint, uint> PrimaryColorPair
        {
            get { return _inner.PrimaryColorPair; }
        }

        public Banner Banner
        {
            get { return _inner.Banner; }
        }

        public int GetTacticsSkillAmount()
        {
            return _tactics >= 0 ? _tactics : _inner.GetTacticsSkillAmount();
        }

        public int GetNumberOfMissionReadyTroops()
        {
            return _inner.GetNumberOfMissionReadyTroops();
        }

        public bool IsUnderPlayersCommand(BattleSideEnum playerSide)
        {
            return _inner.IsUnderPlayersCommand(playerSide);
        }
    }
}
