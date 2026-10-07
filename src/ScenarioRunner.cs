using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using TaleWorlds.Core;
using TaleWorlds.CampaignSystem;      // B2: Campaign（判定"是否战役模式"）
using TaleWorlds.Library;
using TaleWorlds.Localization;
using TaleWorlds.ModuleManager;
using TaleWorlds.MountAndBlade;
using TaleWorlds.MountAndBlade.MissionSpawnHandlers;
using TaleWorlds.ObjectSystem;

namespace BlBridge
{
    /// <summary>
    /// AI 对 AI 自动推演。模式完全照抄 TaleWorlds 官方的 CPUBenchmarkMissionLogic：
    /// 用 MissionCombatantsLogic + DefaultBattleMissionAgentSpawnLogic + AgentHumanAILogic +
    /// AgentVictoryLogic 组一场**没有玩家**的战斗，用 MissionState.OpenNew 打开并推到状态栈上。
    ///
    /// 状态机（照 Coop 规范）：
    ///   idle → loading（已请求开战，等 agent 生成）→ running（双方已入场）
    ///        → ended（有 result）/ error
    /// 结束路径：由 ScenarioProbe 判定胜负或超时后调用 public Mission.EndMission()，
    /// 引擎的 MissionState 检测到结束后自行 PopState 回到上一个界面（官方路径，不硬拔）。
    /// </summary>
    internal static class ScenarioRunner
    {
        public const string RunStateIdle = "idle";
        public const string RunStateLoading = "loading";
        public const string RunStateRunning = "running";
        public const string RunStateEnded = "ended";
        public const string RunStateError = "error";

        // ── 当前运行态 ───────────────────────────────────────────────────
        internal static string State = RunStateIdle;
        internal static string LastError = "";
        internal static string StartedUtc = "";
        internal static string AttackerTroop = "";
        internal static string DefenderTroop = "";
        internal static int AttackerCount;
        internal static int DefenderCount;
        internal static int AttackerInitial = -1;
        internal static int DefenderInitial = -1;
        internal static float DurationCapSeconds = 600f;
        internal static double StartedAtUnix;
        internal static double LastHeartbeatUnix;

        /// <summary>
        /// v0.8.10：看门狗在 loading 态的判定阈值（秒）—— 自开战请求起超过这么久
        /// 仍无任何 mission tick 推进 ⇒ 判定卡死。running 态不用这个值，用 DurationCapSeconds + 60（见 Watchdog）。
        ///
        /// v0.8.10 修订（真机回归 §二十一）：120 → **300**。全兵种分批测试每场有 400 个兵种组
        /// （攻 200 + 守 200），loading 明显慢于常规场次（20~200 组）；120 s 会把"慢"误判成"卡死"
        /// 并强行收尾。300 s 给慢加载留余量 —— 代价是"真卡死"要等更久才兜住（阈值越大越保守，这是权衡）。
        /// </summary>
        internal static float WatchdogLoadingSeconds = 300f;

        // 结果
        internal static string ResultJson = "null";

        // 正在进行的一场的上下文（仅主线程使用）
        private static string _currentRequestId = "";
        private static bool _endRequested;
        private static string _endReason = "";
        private static double _missionStartUnix;

        public static bool Busy
        {
            get { return State == RunStateLoading || State == RunStateRunning; }
        }

        public static double NowUnix
        {
            get { return (DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds; }
        }

        // ── 对外接口（由 CommandPump 在主线程调用）────────────────────────

        public static string StatusJson()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"state\":").Append(Protocol.Q(State));
            sb.Append(",\"busy\":").Append(Jw.B(Busy));
            sb.Append(",\"lastError\":").Append(Protocol.Q(LastError));
            sb.Append(",\"requestId\":").Append(Protocol.Q(_currentRequestId));
            sb.Append(",\"startedUtc\":").Append(Protocol.Q(StartedUtc));
            sb.Append(",\"elapsedSec\":").Append(Jw.N((float)ElapsedSeconds()));
            sb.Append(",\"scenario\":{");
            sb.Append("\"attackerTroop\":").Append(Protocol.Q(AttackerTroop));
            sb.Append(",\"attackerCount\":").Append(Jw.N(AttackerCount));
            sb.Append(",\"defenderTroop\":").Append(Protocol.Q(DefenderTroop));
            sb.Append(",\"defenderCount\":").Append(Jw.N(DefenderCount));
            sb.Append(",\"attackerGroups\":").Append(Protocol.Q(AttackerGroupsDsl));
            sb.Append(",\"defenderGroups\":").Append(Protocol.Q(DefenderGroupsDsl));
            // v0.8.32：`formation` 死字段的一致性告警（每场 start 时重算 ⇒ 天然按场次隔离）
            sb.Append(",\"formationWarnings\":").Append(FormationWarningsJson());
            // v0.8.41：环境旋钮与被显式忽略的原因（见 BattleEnv.cs）；无 ⇒ null / []
            sb.Append(",\"env\":").Append(BattleEnv.Json());
            sb.Append(",\"envNotes\":").Append(EnvNotesJson());
            // v0.8.41：战术档位（请求值 + 引擎原生值；见 TacticsCombatant.cs）
            sb.Append(",\"tactics\":").Append(TacticsJson());
            sb.Append(",\"durationCapSec\":").Append(Jw.N((int)DurationCapSeconds));
            sb.Append('}');
            sb.Append(",\"progress\":").Append(ProgressJson());
            sb.Append(",\"speed\":").Append(TimeControl.SpeedJson());
            sb.Append(",\"readiness\":").Append(EngineProbe.Json(SafeMission()));
            sb.Append(",\"build\":").Append(BuildInfo.Json());
            sb.Append(",\"result\":").Append(ResultJson);
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>取当前 Mission 的安全包装（mission 可能在任意时刻被销毁）。</summary>
        private static Mission SafeMission()
        {
            try
            {
                return Mission.Current;
            }
            catch
            {
                return null;
            }
        }

        private static double ElapsedSeconds()
        {
            if (State == RunStateIdle || StartedAtUnix <= 0) return 0;
            return NowUnix - StartedAtUnix;
        }

        private static string ProgressJson()
        {
            int aAlive = -1;
            int dAlive = -1;
            try
            {
                Mission m = Mission.Current;
                if (m != null)
                {
                    if (m.AttackerTeam != null) aAlive = m.AttackerTeam.ActiveAgents.Count;
                    if (m.DefenderTeam != null) dAlive = m.DefenderTeam.ActiveAgents.Count;
                }
            }
            catch
            {
            }
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"aAlive\":").Append(Jw.N(aAlive));
            sb.Append(",\"dAlive\":").Append(Jw.N(dAlive));
            sb.Append(",\"aInitial\":").Append(Jw.N(AttackerInitial));
            sb.Append(",\"dInitial\":").Append(Jw.N(DefenderInitial));
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>
        /// 取「当前状态机」：**优先静态** `GameStateManager.Current`，取不到再退回 `Game.Current.GameStateManager`。
        ///
        /// 为什么优先静态（真机 2026-09-25 23:00，v0.8.21 实测）：
        ///   启动初期与主菜单 **`Game.Current` 是 null**，而 `GameStateManager.Current` 那时**可用**
        ///   （实测其 `ActiveState.GetType().Name == "InitialState"`）
        ///   ⇒ 旧写法（只看 `Game.Current`）在这两个阶段恒返回"没有状态"，等于问不到。
        /// `TaleWorlds.Core.GameStateManager.Current` 是 **public static** 属性（`GameStateManager.cs:53`），
        /// 与 BUTR/Bannerlord.GABS 的 `core/skip_video` / `wait_for_state` 走同一条路。
        ///
        /// **只此一处实现**：`skip_video`、`activeState`、`close_ui` 全走这里，免得"两处各自算状态"漂移
        /// （v0.8.10 的 B3/B5 就是这类"判定器自己没对照"的坑）。已兜异常，取不到返回 null。
        /// </summary>
        internal static GameStateManager ActiveStateManager()
        {
            try
            {
                GameStateManager mgr = GameStateManager.Current;
                if (mgr != null) return mgr;
            }
            catch
            {
            }
            try
            {
                Game game = Game.Current;
                if (game != null) return game.GameStateManager;
            }
            catch
            {
            }
            return null;
        }

        /// <summary>
        /// 当前激活的 GameState 类型名（取不到返回空串）。只读、已兜异常。
        /// 主菜单实测是 **`InitialState`**（真机 2026-09-25 23:00）—— `Game.Current` 为 null 的时期也取得到。
        /// `UiEntry` 的 list_ui/open_ui/close_ui 与这里的开战守卫**共用这一个判据**，
        /// 免得"两处各自算状态名"漂移（v0.8.10 的 B3/B5 就是这类"判定器自己没对照"的坑）。
        /// </summary>
        /// <summary>
        /// 当前是否处于**战役模式**（B2 判据）。
        ///
        /// ## 为什么用 `Game.Current.GameType is Campaign`
        ///
        /// 反射核实（2026-10-07，本机 1.4.8）：
        ///   · `TaleWorlds.CampaignSystem.Campaign` 的基类是 `TaleWorlds.Core.GameType`
        ///     ⇒ 它**就是**一个 GameType，用 `is` 判定语义精确；
        ///   · `Game.Current` 与 `Game.GameType` 都存在。
        ///
        /// ⚠️ 为什么不直接看 `Campaign.Current != null`：那个在**读档过程中**也会为 null，
        ///    而 `GameType` 在 `Game` 建好后就确定了 —— 判"这一局是哪套 Game"，它更准。
        ///    两个都兜一层（`Game.Current` 为 null 时退回看 `Campaign.Current`），
        ///    任一路径为真即认定战役，**fail-closed**（宁可不放行，也不放行后才崩）。
        /// </summary>
        internal static bool IsCampaignActive()
        {
            try
            {
                Game game = Game.Current;
                if (game != null && game.GameType is Campaign)
                {
                    return true;
                }
            }
            catch
            {
            }
            try
            {
                return Campaign.Current != null;
            }
            catch
            {
                // 取不到 ⇒ 保守返回 false（不误拒"非战役"的合法调用）
                return false;
            }
        }

        /// <summary>
        /// 当前活动 GameState 的**类型名**（空串 = 取不到）。
        ///
        /// ⚠️ 它只是**判据的一半**：`MissionState` 在战役与自定义战斗里同名，
        ///    所以判"能不能开战"还要看 <see cref="IsCampaignActive"/>。
        /// </summary>
        internal static string ActiveGameStateName()
        {
            try
            {
                GameStateManager mgr = ActiveStateManager();
                GameState st = mgr == null ? null : mgr.ActiveState;
                if (st != null) return st.GetType().Name;
            }
            catch
            {
            }
            return "";
        }

        /// <summary>
        /// 「可以在这里开战」的状态：**官方自定义战斗界面**。
        ///
        /// 沿革（别把这段读成"一直如此"）：
        ///   v0.8.11 及之前 —— 同样是单值 `CustomBattleState`；
        ///   v0.8.12        —— 放宽成白名单，多放行一个自建面板态 `BattleSetupState`；
        ///   v0.8.14        —— 面板删除（官方自带完整界面：战斗/围攻/村庄/海战/海上掠夺 + 玩家类型
        ///                     + 选择攻守方 + 全套地图参数，复刻属重复建设）⇒ 收回为单值；
        ///   v0.8.44        —— **再放宽成"同一族状态"**：装了 **NavalDLC** 时，从主菜单
        ///                     `open_ui(CustomBattle)` 落的是 **`NavalCustomBattleState`**
        ///                     （官方把自定义战斗入口劫持到海战选兵界面）。
        ///                     旧实现写死单值 ⇒ 带 NavalDLC 的机器上：
        ///                       ① `bl_start_battle` 直接报 `wrong_state`（要调用方手动传
        ///                          `allowAnyState=true` 才绕过 —— 那是**把缺陷推给调用方**）；
        ///                       ② `close_ui` 报 `not_open`（**进去了出不来**，而进去正是本模块给的门）。
        ///                     实测见工作区报告 2026-10-05（`activeState=NavalCustomBattleState`
        ///                     时 `close_ui` → `code=not_open`）。
        ///
        /// 判据是**后缀匹配** `*CustomBattleState`（同时容纳 `CustomBattleState` 与
        /// `NavalCustomBattleState`），而不是"前缀"或"包含"：
        /// 这样 `MapState` / `CampaignState` / `InitialState` / `VideoPlaybackState`
        /// **都不匹配**（它们不以 `CustomBattleState` 结尾）—— 集合外一律拒绝，
        /// 不做更宽的匹配、不靠 catch 兜底。
        /// 判据可离线对照：假造一个集合外状态名必须仍报 wrong_state（见 tools\bl_selftest.py 的断言）。
        /// </summary>
        internal static bool IsBattleSetupState(string stateName)
        {
            if (string.IsNullOrEmpty(stateName)) return false;
            // 后缀必须严格是 CustomBattleState（大小写敏感，与引擎给的名字逐字一致）
            return stateName.EndsWith(UiEntry.CustomBattleStateName, StringComparison.Ordinal);
        }

        /// <summary>
        /// v0.8.41：战术档位的合法域 = `-1`（不覆盖）或 `0..100`。
        /// 上限 100 不是因为引擎需要它（引擎只在 20 / 50 分档），而是为了给一个**有限域**，
        /// 好让打错的值当场报错 —— 静默接受 999 会做出一场"看起来设了、其实等于 100"的实验。
        /// </summary>
        private static bool InTacticLevelRange(int value)
        {
            return value == -1 || (value >= 0 && value <= 100);
        }

        /// <summary>
        /// v0.8.41：整数参数 —— **同时接受**裸数字与带引号的字符串。
        /// 理由与 `Jmini.Bool` 收两种形态同源（v0.8.14 真机踩到 `spectate` 被静默丢弃：
        /// CLI 发字符串、MCP 发裸字面量，只认一种就是"调用方猜谜"）。不动 Jmini 本体，
        /// 只给本版新增的参数用，避免影响既有参数的解析路径（GC2）。
        /// </summary>
        private static int IntOrQuoted(string raw, string key, int fallback)
        {
            if (!Jmini.Has(raw, key)) return fallback;
            string quoted = Jmini.Str(raw, key, null);
            if (quoted != null)
            {
                int parsed;
                if (int.TryParse(quoted.Trim(), NumberStyles.Integer,
                        CultureInfo.InvariantCulture, out parsed))
                {
                    return parsed;
                }
                return fallback;
            }
            return Jmini.Int(raw, key, fallback);
        }

        public static string Start(string id, string raw)
        {
            if (Busy)
            {
                return Protocol.Failure(id, "busy",
                    "已有一场推演在进行中（state=" + State + "），请先 abort 或等它结束", false);
            }

            // v0.8.32 修复（**真机抓到的**）：`formation` 死字段的一致性告警必须**每次 start 无条件清空**。
            // 原先把它写在 `if (attackerGrouped || defenderGrouped)` 块内 ⇒ **不带 groups 的 start 不会重置**，
            // 上一场（带 groups）的告警就被原样带进这一场的响应里。真机复现（2026-09-26）：
            //   A 场：带 groups 但场景名写错 ⇒ start 失败（告警已写入）；
            //   B 场：不带 groups ⇒ 响应里出现了 A 场那条告警 ⇒ 残留坐实。
            // 位置：放在 Busy 短路**之后**（正在跑的那一场的告警不该被一个被拒的请求清掉）。
            _pendingFormationWarnings = new List<string>();

            // v0.8.41：环境旋钮 + 战术档位，**每场无条件复位**（理由同上一段）。
            // 不传 = "不覆盖"：既不能沿用上一场的值，也不能悄悄改成引擎默认之外的别的值。
            BattleEnv.Reset();
            _pendingEnvNotes = new List<string>();
            AttackerTacticsRequested = -1;
            DefenderTacticsRequested = -1;
            AttackerTacticsNative = -1;
            DefenderTacticsNative = -1;

            // 战术档位（0..100；-1 = 不覆盖）。阈值 20 / 50 是引擎自己的分档线
            // （反编译 MissionCombatantsLogic.EarlyStart 取证，见 TacticsCombatant.cs）。
            int atkTactics = IntOrQuoted(raw, "attackerTacticLevel", -1);
            int defTactics = IntOrQuoted(raw, "defenderTacticLevel", -1);
            if (!InTacticLevelRange(atkTactics))
            {
                return Protocol.Failure(id, "bad_tactic_level",
                    "attackerTacticLevel 只接受 -1（不覆盖）或 0..100，收到 " + atkTactics, false);
            }
            if (!InTacticLevelRange(defTactics))
            {
                return Protocol.Failure(id, "bad_tactic_level",
                    "defenderTacticLevel 只接受 -1（不覆盖）或 0..100，收到 " + defTactics, false);
            }

            // 地形（空 = 不覆盖）。名字非法**显式报错**，绝不静默兜底成 Plain。
            string terrainArg = Jmini.Str(raw, "terrain", "");
            int terrainValue;
            string terrainError;
            if (!BattleEnv.TryParseTerrain(terrainArg, out terrainValue, out terrainError))
            {
                return Protocol.Failure(id, "bad_terrain", terrainError, false);
            }
            BattleEnv.TerrainTypeValue = terrainValue;
            BattleEnv.TerrainName = string.IsNullOrEmpty(terrainArg)
                ? "" : terrainArg.Trim().ToLowerInvariant();

            // 随机地形种子（不给 = 不覆盖 ⇒ record 保持引擎默认 0 且不开 NeedsRandomTerrain）
            if (Jmini.Has(raw, "randomTerrainSeed"))
            {
                BattleEnv.RandomTerrainSeedValue = IntOrQuoted(raw, "randomTerrainSeed", 0);
            }

            // 「打到玩家方身上的伤害」倍率（不给 = 不覆盖 ⇒ 保持引擎默认 1f）。
            // ⚠️ 名字里的 "FriendlyFire" 容易误导：见 BattleEnv.FriendlyFireMultiplier 的注释 ——
            //    引擎比的是"受害者是否属于玩家一方"，不是"加害者与受害者同队"。
            if (Jmini.Has(raw, "aiFriendlyFireMultiplier"))
            {
                double ff = Jmini.Num(raw, "aiFriendlyFireMultiplier", 1.0);
                if (ff < 0.0 || ff > 1.0)
                {
                    return Protocol.Failure(id, "bad_env",
                        "aiFriendlyFireMultiplier 只接受 0..1（引擎默认 1），收到 " + ff, false);
                }
                BattleEnv.FriendlyFireMultiplier = (float)ff;
            }

            // 关尸体淡出（长跑时尸体量稳定；不给 = 不覆盖）
            BattleEnv.KeepCorpses = Jmini.Bool(raw, "keepCorpses", false);

            // 攻城专用：场景等级（1..3）与开战时刻（小时）。范围外**显式报错**。
            SiegeSceneLevel = 3;
            SiegeTimeOfDay = 6f;
            if (Jmini.Has(raw, "sceneLevel"))
            {
                int lv = IntOrQuoted(raw, "sceneLevel", 3);
                if (lv < 1 || lv > 3)
                {
                    return Protocol.Failure(id, "bad_env",
                        "sceneLevel 只接受 1..3（攻城场景升级等级），收到 " + lv, false);
                }
                SiegeSceneLevel = lv;
            }
            if (Jmini.Has(raw, "timeOfDay"))
            {
                double tod = Jmini.Num(raw, "timeOfDay", 6.0);
                if (tod < 0.0 || tod > 24.0)
                {
                    return Protocol.Failure(id, "bad_env",
                        "timeOfDay 只接受 0..24（小时），收到 " + tod, false);
                }
                SiegeTimeOfDay = (float)tod;
            }

            string attacker = Jmini.Str(raw, "attackerTroop", "");
            string defender = Jmini.Str(raw, "defenderTroop", "");
            int aCount = Jmini.Int(raw, "attackerCount", 0);
            int dCount = Jmini.Int(raw, "defenderCount", 0);
            string scene = Jmini.Str(raw, "scene", "battle_terrain_a");
            int capSec = Jmini.Int(raw, "durationCapSec", 600);
            if (aCount <= 0) aCount = 20;
            if (dCount <= 0) dCount = 20;
            if (capSec <= 0) capSec = 600;

            // 对称化开关（v0.7.3 新增，用于消除/定位镜像对局的方向偏差）：
            //   orders=charge（默认）→ 双方都上 TacticCharge，消除"攻方冲锋、守方原地"的战术不对称
            //   orders=default       → 保留引擎默认战术（攻方进攻/守方防守），用于 A/B 对照
            //   playerSide=attacker|defender → 谁被标记为"玩家侧"，用于验证该标记是否带来系统性偏差
            string orders = Jmini.Str(raw, "orders", "charge");
            string playerSideArg = Jmini.Str(raw, "playerSide", "attacker");
            if (orders != "charge" && orders != "default") orders = "charge";
            if (playerSideArg != "defender") playerSideArg = "attacker";

            // allowAnyState=true → 不再要求停在「自定义战斗」界面。
            // 依据（读码 + 实测）：MissionState.OpenNew 只做 GameStateManager.PushState（MissionState.cs:312），
            // 引擎没有"必须处于 CustomBattleState"的要求。但**实测发现另一道墙**：主菜单时
            // MBObjectManager 里还没有角色数据（所有兵种 id 都是 unknown_troop），
            // 所以这个开关本身不足以在裸主菜单开战——真正的依赖是"数据已加载"。
            //
            // ⚠️ v0.8.44 复核结论（**保留此开关，但用途已被掏空**，勿再照抄旧文档）：
            //   • 原始设计目标「从主菜单直接开战」**当年就已排除**（PROGRESS §四"已排除的路线"）；
            //   • 后来被实际使用的唯一场景是"绕过带 NavalDLC 时的 `wrong_state`" ——
            //     而那**本来是我们的判据写死单值造成的假报**（见 IsBattleSetupState 的 v0.8.44 注释），
            //     v0.8.44 改为后缀匹配后**该用途自动消失**。
            //   ⇒ 现在它只剩"从数据已加载的其他状态开战"这个**未经实测**的理论用途。
            //     保留而不删的理由：删参数会让仍在传它的调用方直接报错（破坏性），
            //     而留着一个不用的开关成本近乎为零。**别再把"绕过 wrong_state"写成它的用途。**
            bool allowAnyState = Jmini.Str(raw, "allowAnyState", "false") == "true";

            // 不朽靶场（阶段 2①）：dummySide=attacker|defender 把该方设为"永不倒下"的靶子。
            // 原理见 DummyRangeBehavior.cs —— 保持 Mortal + 在 OnScoreHit 内回血，
            // 而不是用 SetMortalityState（那三个"不死"开关都会毁掉伤害数据）。
            string dummyArg = Jmini.Str(raw, "dummySide", "none");
            if (dummyArg != "attacker" && dummyArg != "defender") dummyArg = "none";
            bool freezeDummies = Jmini.Str(raw, "freezeDummies", "false") == "true";
            // v0.7.9：给射手补满弹药（靶子的弹药不补 —— 它是被测对象）
            bool unlimitedAmmo = Jmini.Str(raw, "unlimitedAmmo", "false") == "true";
            // 靶子护甲数值覆盖（-1 = 该部位不动）；只作用于靶子，每帧重申（零 Harmony）
            double armorHead = Jmini.Num(raw, "dummyArmorHead", -1.0);
            double armorTorso = Jmini.Num(raw, "dummyArmorTorso", -1.0);
            double armorLegs = Jmini.Num(raw, "dummyArmorLegs", -1.0);
            double armorArms = Jmini.Num(raw, "dummyArmorArms", -1.0);
            // v0.8.2：把靶子的**身甲**换成指定物品 id（空 = 不换）。
            // 用于"同兵种、同护甲数值、只换材质"的对照：材质抗性 R 只来自物品，
            // 数值仍由上面四个覆盖值对齐。见 DummyRangeBehavior.BodyItemId。
            string bodyItem = Jmini.Str(raw, "dummyBodyItem", "");
            // v0.8.4：随机种子（-1 = 不设）。见下方设置处的说明。
            int randomSeed = Jmini.Int(raw, "randomSeed", -1);
            // v0.8.5：多轮连续实验（同一 mission 内跑 N 轮，省掉每轮的场景加载）
            int rounds = Jmini.Int(raw, "rounds", 1);
            int roundEndAlive = Jmini.Int(raw, "roundEndAlive", 1);
            bool roundSwap = Jmini.Str(raw, "roundSwap", "false") == "true";
            // v0.8.14：上帝视角观战（默认 false = 与以往完全一致）。
            // 为什么要它：AI 测试场次里人只想看，不想被绑在一个身体上操作（官方自定义战斗的
            // 「玩家类型」只有 Commander / Sergeant，点不出观察者）。实现见 SpectatorWatchBehavior。
            bool spectate = Jmini.Bool(raw, "spectate", false);
            string roundSpawnA = Jmini.Str(raw, "roundSpawnAttacker", "");
            string roundSpawnD = Jmini.Str(raw, "roundSpawnDefender", "");

            // 1) 必须处于官方自定义战斗界面（官方 benchmark 同样要求该界面）。
            //    v0.8.12 曾放宽为白名单（含自建面板态 `BattleSetupState`）；v0.8.14 面板删除
            //    （官方自带完整界面，复刻属重复建设）⇒ 收回；
            //    **v0.8.44 再放宽为"同一族"**：判据是**后缀匹配** `*CustomBattleState`，
            //    因为装了 NavalDLC 时官方入口落 `NavalCustomBattleState`（详见 IsBattleSetupState
            //    的注释：那个写死单值造成过三条连带缺陷）。判据本身仍然唯一（不复刻）。
            //    真正的前提其实是"MBObjectManager 里的兵种/文化已加载"（裸主菜单下所有兵种 id
            //    都报 unknown_troop），而官方自定义战斗正是加载它们的正门 —— 所以就用它当判据。
            string stateName = ActiveGameStateName();

            // ★★ B2 修复（2026-10-07，由隔壁项目的测试表实测发现）：
            //    **战役模式下 `allowAnyState=true` 必崩**，所以在此**先拒绝、再谈其它**。
            //
            // 实测证据（`bl_crash --deep`，pid 2756）：
            //   bucket : CLR_EXCEPTION_System.InvalidCastException_80004002_
            //            SandBox.dll!SandboxAgentStatCalculateModel.InitializeMissionEquipment
            //   message: 无法将类型为 `CustomBattleCombatant` 的对象
            //            强制转换为类型 `PartyBase`
            //   stack  : SandboxAgentStatCalculateModel.InitializeMissionEquipment
            //            ← BloodlustAgentStatCalculateModel... ← Agent.Build ← Mission.SpawnAgent
            //            ← Mission.SpawnTroop ← DefaultBattleMissionAgentSpawnLogic.CheckDeployment
            //
            // 根因（结构性，不是配置问题）：
            //   战役模式下引擎注册的是 `SandboxAgentStatCalculateModel`（按 `PartyBase` 取队伍），
            //   而我们造的是 `CustomBattleCombatant`（自定义战斗用的类型）⇒ spawn 第一个 agent 时崩。
            //   `allowAnyState` 绕得过"必须停在自定义战斗界面"，**绕不过"战役用哪套 Model"**。
            //
            // ⚠️ 为什么必须在**这里**拒绝（而不是让它崩）：
            //   崩溃发生在 spawn 第一个 agent 时 —— 此前我们已经返回 `accepted:true, state:loading`
            //   ⇒ 调用方**看起来开战成功了**，30 秒后进程却没了（且 cleanExit=false 污染会话）。
            //   这正是本项目最防的那类"先报成功、后失败"。
            if (allowAnyState && IsCampaignActive())
            {
                return Protocol.Failure(id, "unsupported_in_campaign",
                    "战役模式下 **不支持** allowAnyState —— 它会**必然崩溃**，故在此拒绝（不再先报成功）。"
                    + "根因：战役模式注册的是 SandboxAgentStatCalculateModel（按 PartyBase 取队伍），"
                    + "而桥造的是 CustomBattleCombatant，两套 Model 期望的队伍类型不同 ⇒ "
                    + "在 spawn 第一个 agent 时抛 InvalidCastException。"
                    + "allowAnyState 绕得过『必须停在自定义战斗界面』，绕不过『战役用哪套 Model』。"
                    + "正确做法：回主菜单后用 open_ui（uiId=CustomBattle）进官方自定义战斗界面再开战；"
                    + "若你需要在战役内验证战斗，那需要另做一条走 Campaign 队伍类型的通道（尚未实现）。",
                    false);
            }

            if (!allowAnyState && !IsBattleSetupState(stateName))
            {
                return Protocol.Failure(id, "wrong_state",
                    "需要停留在「自定义战斗」界面（当前状态: " + (stateName.Length == 0 ? "未知" : stateName) +
                    "）。" + StuckMissionHint(stateName) +
                    "进游戏后点 Custom Battle 停在选兵界面，或用 open_ui（uiId=CustomBattle，主菜单下可用）走进去；" +
                    "也可以传 allowAnyState=true 跳过本检查（但那时兵种 id 可能还解析不了，见该参数的说明）。", false);
            }

            // 2) T5 多兵种/战术组参数：`troop:count[:formation[:movement]]`，多组用 | 分隔。
            //    空串 ⇒ 空列表 ⇒ 该方走旧路径（GC2）；任何非法一律显式报 bad_groups（GC3）。
            string attackerGroupsDslRaw = Jmini.Str(raw, "attackerGroups", "");
            string defenderGroupsDslRaw = Jmini.Str(raw, "defenderGroups", "");
            List<SquadSpec> attackerGroups;
            List<SquadSpec> defenderGroups;
            try
            {
                attackerGroups = SquadSpec.Parse(attackerGroupsDslRaw);
            }
            catch (ArgumentException ex)
            {
                return Protocol.Failure(id, "bad_groups", "attackerGroups 解析失败：" + ex.Message, false);
            }
            try
            {
                defenderGroups = SquadSpec.Parse(defenderGroupsDslRaw);
            }
            catch (ArgumentException ex)
            {
                return Protocol.Failure(id, "bad_groups", "defenderGroups 解析失败：" + ex.Message, false);
            }
            bool attackerGrouped = attackerGroups.Count > 0;
            bool defenderGrouped = defenderGroups.Count > 0;
            // DSL 按 | 切分的原始片段（与 specs 下标一一对应），用于逐字回显错误消息（修复轮 1 · m2）
            string[] attackerGroupRawSegments = attackerGrouped ? attackerGroupsDslRaw.Split('|') : null;
            string[] defenderGroupRawSegments = defenderGrouped ? defenderGroupsDslRaw.Split('|') : null;
            // 组模式下把"单值"指向第一组，并把 count 换成各组之和：旧脚本读到的单值字段因此非空，
            // 且 try 块与 OpenMission 的既有赋值/调用无需改动（无 groups 时这两行不执行 ⇒ 逐字节等价）。
            if (attackerGrouped)
            {
                attacker = attackerGroups[0].Troop;
                aCount = SumCounts(attackerGroups);
            }
            if (defenderGrouped)
            {
                defender = defenderGroups[0].Troop;
                dCount = SumCounts(defenderGroups);
            }

            // 2b) 组模式：逐组校验兵种 + movement 冲突校验（§3 用户裁决 A）+ 多轮拦截。
            //     必须在下面的单值检查**之前**跑：组模式下该方第一组若兵种未知，这里就能报出
            //     "带组号 + 逐字原文"的错误，而不会被单值检查报成"无组号"（修复轮 1 · m1）。
            // T6：组信息还要交给多轮编排器（按组重生 / 每轮按组重申）⇒ 把校验阶段
            //     **已 Resolve 过的**兵种对象带出来复用，不重复解析（brief §3.1）。
            //     无 groups 的一方保持 null（旧路径判据用 null，不是空列表）。
            List<BasicCharacterObject> attackerGroupChars = null;
            List<BasicCharacterObject> defenderGroupChars = null;
            if (attackerGrouped || defenderGrouped)
            {
                if (attackerGrouped)
                {
                    string gcode;
                    string gmsg;
                    if (!ValidateGroupedSide(attackerGroups, attackerGroupRawSegments, "攻方",
                            out gcode, out gmsg, out attackerGroupChars))
                    {
                        return Protocol.Failure(id, gcode, gmsg, false);
                    }
                }
                if (defenderGrouped)
                {
                    string gcode;
                    string gmsg;
                    if (!ValidateGroupedSide(defenderGroups, defenderGroupRawSegments, "守方",
                            out gcode, out gmsg, out defenderGroupChars))
                    {
                        return Protocol.Failure(id, gcode, gmsg, false);
                    }
                }
                // groups 与整体 orders 不能混用（两套命令机制）
                if (orders != "charge")
                {
                    return Protocol.Failure(id, "conflicting_orders",
                        "attackerGroups/defenderGroups 与 orders=" + orders
                        + " 不能混用：groups 走按组命令，orders 走整体命令", false);
                }
                // v0.8.32：`formation` 是**死字段**（只回显、不决定编队）⇒ 开战前把"请求值 vs 兵种
                // 实际编队"的不一致**显式收集**（见 SquadSpec.CollectFormationMismatches）。
                // 不改变开战结果（名字非法仍由 SquadSpec.Parse 拒），只是不再静默：
                // 真机踩到过 —— 给弓手写 Infantry 照样开战、无人报，兵却进了 Ranged。
                // ⚠️ 清空**不在这里**：见 `Start()` 开头（以前写在这一块里 ⇒ 不带 groups 的 start 会残留上一场的告警）。
                if (attackerGrouped) AppendFormationWarnings("攻方", attackerGroups, attackerGroupChars);
                if (defenderGrouped) AppendFormationWarnings("守方", defenderGroups, defenderGroupChars);
            }

            // 2) 兵种 id 必须能解析。
            //    失败时先怀疑"游戏内数据还没加载" —— 实测：停在主菜单（或任何还没进过游戏内
            //    界面的状态）时，**所有**兵种 id 都报 unknown_troop，因为 MBObjectManager
            //    尚未填充角色数据。此时主动 LoadXML 一次再重试。
            BasicCharacterObject attackerTroop = Resolve(attacker);
            BasicCharacterObject defenderTroop = Resolve(defender);
            if (attackerTroop == null || defenderTroop == null)
            {
                if (TryLoadBaseObjects())
                {
                    attackerTroop = Resolve(attacker);
                    defenderTroop = Resolve(defender);
                }
            }
            if (attackerTroop == null)
            {
                return Protocol.Failure(id, "unknown_troop", "攻方兵种 id 不存在: " + attacker, false);
            }
            if (defenderTroop == null)
            {
                return Protocol.Failure(id, "unknown_troop", "守方兵种 id 不存在: " + defender, false);
            }

            // 2c) 场景名必须真实存在。**这是唯一一道能拦住原生崩溃的检查** ——
            //     见下方 SceneExists 的说明：引擎的 Scene.Read(sceneName) 是纯原生调用
            //     （EngineApplicationInterface.IScene.Read），场景名不存在时托管侧拿不到任何
            //     可判的返回值，直接进 native 访问违规（0xC0000005）**整个进程死掉**。
            //     实测：v0.8.9 部署后一次 start 传 scene=bridge（该名字在全部 Modules 的
            //     SceneObj/ 下都不存在）→ 6 ms 后崩溃。故必须在开 mission 之前先用文件系统查。
            string sceneCode;
            string sceneMsg;
            if (!ValidateScene(scene, out sceneCode, out sceneMsg))
            {
                return Protocol.Failure(id, sceneCode, sceneMsg, false);
            }

            _pendingAttackerGroups = attackerGrouped ? attackerGroups : null;
            _pendingDefenderGroups = defenderGrouped ? defenderGroups : null;
            AttackerGroupsDsl = attackerGrouped ? attackerGroupsDslRaw : "";
            DefenderGroupsDsl = defenderGrouped ? defenderGroupsDslRaw : "";

            // 3) 组军并开战
            try
            {
                AttackerTroop = attacker;
                DefenderTroop = defender;
                AttackerCount = aCount;
                DefenderCount = dCount;
                DurationCapSeconds = capSec;
                LastError = "";
                ResultJson = "null";
                AttackerInitial = -1;
                DefenderInitial = -1;
                _currentRequestId = id;
                _endRequested = false;
                _endReason = "";
                _missionStartUnix = 0;
                StartedUtc = Jw.UtcNow();
                StartedAtUnix = NowUnix;
                _pendingOrders = orders;
                _pendingPlayerSide = playerSideArg == "defender"
                    ? BattleSideEnum.Defender : BattleSideEnum.Attacker;

                // 靶场开关必须在建 mission **之前**写进静态字段（与 _pendingOrders 同一个模式）；
                // 战斗结束后由 SetIdle() 复位，避免污染下一场。
                DummyRangeBehavior.DummySide = dummyArg == "attacker" ? BattleSideEnum.Attacker
                                            : dummyArg == "defender" ? BattleSideEnum.Defender
                                            : BattleSideEnum.None;
                DummyRangeBehavior.FreezeDummies = freezeDummies;
                DummyRangeBehavior.UnlimitedAmmoForShooters = unlimitedAmmo;
                DummyRangeBehavior.ArmorHead = (float)armorHead;
                DummyRangeBehavior.ArmorTorso = (float)armorTorso;
                DummyRangeBehavior.ArmorLegs = (float)armorLegs;
                DummyRangeBehavior.ArmorArms = (float)armorArms;
                DummyRangeBehavior.BodyItemId = bodyItem;

                // v0.8.3：标记本场来源，供 meta 事件区分靶场实验与玩家实战
                SubModule.MissionOrigin = "bridge";
                // v0.8.4：随机种子（-1 = 不设）。MBRandom 是 C# 侧唯一随机门面
                // （RandomFloat/RandomInt/ChooseWeighted/RoundRandomized 全走它），
                // 设它即可钉住 C# 侧的随机流 ⇒ 为"同种子重放"提供可能。
                // ⚠️ 伤害公式里的随机命中因子 PRF 在 native 层掷（Agent.ApplyDamage 是引擎 C++），
                //    能否被它管住**只能实测**——同种子跑两场比对逐值一致性即可判定。
                if (randomSeed >= 0)
                {
                    SubModule.PendingRandomSeed = randomSeed;
                    MBRandom.SetSeed((uint)randomSeed, (uint)(randomSeed ^ 0x9E3779B9));
                }
                else
                {
                    SubModule.PendingRandomSeed = -1;
                }
                // v0.8.5：多轮连续实验的配置（见 RoundOrchestratorBehavior）
                RoundOrchestratorBehavior.Rounds = rounds < 1 ? 1 : rounds;
                RoundOrchestratorBehavior.EndAlive = roundEndAlive < 0 ? 0 : roundEndAlive;
                RoundOrchestratorBehavior.SwapSides = roundSwap;
                RoundOrchestratorBehavior.SpawnAttacker = ParseVec3(roundSpawnA);
                RoundOrchestratorBehavior.SpawnDefender = ParseVec3(roundSpawnD);
                RoundOrchestratorBehavior.AttackerChar = attackerTroop;
                RoundOrchestratorBehavior.DefenderChar = defenderTroop;
                RoundOrchestratorBehavior.AttackerCount = aCount;
                RoundOrchestratorBehavior.DefenderCount = dCount;
                // T6：把组信息交给多轮编排器（无 groups ⇒ 四个字段皆 null ⇒ 旧单值路径，GC2）。
                //     Squads 与 SquadTroops 下标一一对应（校验阶段按同一顺序 Resolve，见 §3.1）。
                RoundOrchestratorBehavior.AttackerSquads = _pendingAttackerGroups;
                RoundOrchestratorBehavior.DefenderSquads = _pendingDefenderGroups;
                RoundOrchestratorBehavior.AttackerSquadTroops = attackerGroupChars;
                RoundOrchestratorBehavior.DefenderSquadTroops = defenderGroupChars;
                // v0.8.14：上帝视角标志必须与 SiegePending 同一时刻写入（都在开 mission 之前），
                // 由 SubModule.OnBeforeMissionBehaviorInitialize 消费一次（挂 SpectatorWatchBehavior）。
                SpectateRequested = spectate;
                // v0.8.41：战术档位请求值必须在 CreateBehaviors 之前写进静态字段
                //（CreateBehaviors 里据此决定要不要包 TacticsCombatant）。
                AttackerTacticsRequested = atkTactics;
                DefenderTacticsRequested = defTactics;
                // v0.8.11：城池场景必须走 siege mission（理由见 IsSiegeScene 的实测说明）
                SiegePending = IsSiegeScene(scene);
                if (SiegePending)
                {
                    OpenSiegeMission(scene, attackerTroop, defenderTroop, aCount, dCount);
                    // 攻城走官方 OpenSiegeMissionWithDeployment（内部自建 record + 自建 combatant）
                    // ⇒ 环境字段无处可落、战术档位也不可控。显式记，不静默。
                    AttackerTacticsNative = -1;
                    DefenderTacticsNative = -1;
                    if (BattleEnv.AnyRequested())
                    {
                        _pendingEnvNotes.Add(
                            "env: 攻城路径不支持 terrain / randomTerrainSeed / aiFriendlyFireMultiplier"
                            + " / keepCorpses（走官方 OpenSiegeMissionWithDeployment，它自建"
                            + " MissionInitializerRecord）⇒ 本场这些参数已被忽略");
                        // v0.8.43 修复：**meta 不能声称一个没被应用的参数**。
                        // 实测（2026-10-05）：攻城场次的 meta 里 `terrain:"snow"`，而同一场的
                        // envNotes 明说"已被忽略" —— 两处对同一事实给了相反的说法，下游按 meta
                        // 做分组/对照就会把这场当成 snow 场。既然攻城路径的 record 是官方自建的，
                        // 这些旋钮**确实没生效**，故在此把它们清回"未请求"哨兵，
                        // 让 meta 如实写 `terrain:"" / terrainSeed:-1 / friendlyFire:-1`
                        // （与"野战且没传这些参数"完全同形）。
                        // envNotes 仍然照写，调用方依旧知道"你传了、但没生效"——两条口径自此一致。
                        BattleEnv.Reset();
                    }
                }
                else
                {
                    OpenMission(scene, attackerTroop, defenderTroop, aCount, dCount);
                    // 引擎**原生**战术档位：该方 combatant 的 GetTacticsSkillAmount()
                    //  = 参战兵种里 max(GetSkillValue(Tactics))。留着它才能回答
                    //  "我设了 60，但这场本来是多少" —— 否则覆盖值无法与原生值对照。
                    AttackerTacticsNative = _pendingAttacker != null
                        ? _pendingAttacker.GetTacticsSkillAmount() : -1;
                    DefenderTacticsNative = _pendingDefender != null
                        ? _pendingDefender.GetTacticsSkillAmount() : -1;
                }
                State = RunStateLoading;
                LastHeartbeatUnix = NowUnix;

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"accepted\":true,\"state\":").Append(Protocol.Q(State));
                sb.Append(",\"attackerTroop\":").Append(Protocol.Q(attacker));
                sb.Append(",\"defenderTroop\":").Append(Protocol.Q(defender));
                sb.Append(",\"attackerCount\":").Append(Jw.N(aCount));
                sb.Append(",\"defenderCount\":").Append(Jw.N(dCount));
                sb.Append(",\"scene\":").Append(Protocol.Q(scene));
                sb.Append(",\"spectate\":").Append(Jw.B(spectate));
                // v0.8.32：开战 DSL `formation` 与实际编队不一致的告警（空数组 = 无；不阻断开战）
                sb.Append(",\"formationWarnings\":").Append(FormationWarningsJson());
                // v0.8.41：战术档位（请求 + 引擎原生）与环境旋钮（含"被显式忽略"的原因）
                sb.Append(",\"tactics\":").Append(TacticsJson());
                sb.Append(",\"env\":").Append(BattleEnv.Json());
                sb.Append(",\"envNotes\":").Append(EnvNotesJson());
                sb.Append(",\"note\":\"战斗无玩家参与，10 倍速运行；用 bl_wait_for_state 等 ended\"}");
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                State = RunStateError;
                LastError = ex.GetType().Name + ": " + ex.Message;
                return Protocol.Failure(id, "open_failed", LastError, true);
            }
        }

        /// <summary>
        /// 卡死残留提示（v0.8.10 真机回归发现，见 PROGRESS §二十一）。只在"停在 MissionState
        /// 且引擎里还有 mission、但本状态机不 Busy"时给出**可执行**的指引。
        ///
        /// 为什么需要它：`abort` 在 loading 态会让 mission 卡住（EndMission 在 loading 中无效），
        /// 看门狗能收回本状态机（Busy=false），但**收不回引擎的 GameState 栈** —— MissionState
        /// 仍留在栈顶。此时用户按原来的文案去"点 Custom Battle"是做不到的（界面已在 mission 里），
        /// 会以为桥坏了。实测（2026-09-25 13:09，100v100 第 3 场）：`wrong_state（当前状态: MissionState）`。
        /// </summary>
        private static string StuckMissionHint(string stateName)
        {
            try
            {
                if (stateName != "MissionState") return "";
                if (Busy) return "";
                if (SafeMission() == null) return "";
                return "【引擎里残留了一个不再推进的 mission（本状态机已被看门狗收尾: " + State + "）—— "
                    + "请按 ESC 退出该战斗 / 退回主菜单后再点 Custom Battle；界面无响应则需重启游戏。】";
            }
            catch
            {
                return "";
            }
        }

        public static string Abort(string id)
        {
            if (!Busy)
            {
                return Protocol.Success(id, "{\"aborted\":false,\"state\":" + Protocol.Q(State) + "}");
            }
            try
            {
                _endReason = "aborted";
                _endRequested = true;
                // v0.8.10：**loading 态不要直接 EndMission**。
                //
                // 实测（真机回归 2026-09-25 13:09，100v100 第 3 场：start 后立刻 abort）：
                // loading 中调 EndMission 会让 mission 卡在中间状态、mission tick 停住
                // （`readiness.ticks` 不再增长、`missionTime=0`），只能等看门狗 120 s 兜底；
                // 而且引擎的 GameState 栈会残留 MissionState ⇒ 之后每次 start 都被判 wrong_state，
                // 用户必须手动退出/重启游戏（PROGRESS §二十一 F1/F2）。
                //
                // loading 态只置标志：ScenarioProbe 进入 running 后会消费 `_endRequested` 并
                // Finish("aborted") 正常收尾；若它始终进不了 running（真卡死），看门狗负责兜底。
                if (State != RunStateLoading)
                {
                    Mission m = Mission.Current;
                    if (m != null) m.EndMission();
                }
                return Protocol.Success(id, "{\"aborted\":true,\"state\":" + Protocol.Q(State) + "}");
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "abort_failed", ex.GetType().Name + ": " + ex.Message, true);
            }
        }

        /// <summary>
        /// v0.8.10 看门狗：**必须**挂在 SubModule.OnApplicationTick（主线程每帧），
        /// **不能**挂在 Mission.OnMissionTick —— 后者恰恰是"卡死时会停掉"的那个回调
        /// （v0.8.9 实测：tick 已停，但主线程仍在正常处理命令）。
        ///
        /// 缺陷背景：场景加载失败 / 引擎异常时 mission tick 不再推进，而
        /// durationCapSec 超时与 abort 的 _endRequested 判定都写在 tick 内
        /// ⇒ 状态机永久停在 loading、busy=true，后续 start_battle 全被拒，只能重启游戏。
        ///
        /// 判据：自 LastHeartbeatUnix 起超过阈值仍无 tick ⇒ 判定卡死，状态侧强制收尾。
        /// 收尾顺序：**先**落状态（Busy 立刻转 false，调用方能拿到失败原因并继续发请求），
        /// **后**尝试 EndMission（引擎已异常时大概率无效，历史实测"abort 无效"但不新增崩溃）。
        ///
        /// 实测补记（v0.8.10 真机受控实验，见 PROGRESS §二十 §5.3）：`EndMission()` 在
        /// "mission 正常加载、只是状态被判卡死"时会**真的生效**，其 `OnEndMission()` 会**重写 ResultJson**
        /// ⇒ 下面写的 `stuckState` / `stuckSec` 会被覆盖（`reason` 因两者共用 `_endReason` 而保留）。
        /// 真卡死时 EndMission 无效，本函数写的 result 才原样保留 ⇒ 两条路径都能拿到
        /// `reason="watchdog_loading"`，但**诊断字段只在真卡死路径可见**（`LastError` 两路都有）。
        /// </summary>
        public static void Watchdog()
        {
            try
            {
                if (!Busy) return;
                if (LastHeartbeatUnix <= 0) return;
                double since = NowUnix - LastHeartbeatUnix;
                double limit = State == RunStateLoading
                    ? (double)WatchdogLoadingSeconds
                    : (double)DurationCapSeconds + 60.0;
                if (since < limit) return;

                string stuck = State;
                _endReason = "watchdog_" + stuck;
                LastError = "看门狗：状态 " + stuck + " 已 " + Jw.N((float)since) +
                            " 秒无 mission tick 推进（阈值 " + Jw.N((float)limit) + "），判定卡死并强制收尾";
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"reason\":").Append(Protocol.Q(_endReason));
                sb.Append(",\"stuckState\":").Append(Protocol.Q(stuck));
                sb.Append(",\"stuckSec\":").Append(Jw.N((float)since));
                sb.Append(",\"durationSec\":").Append(Jw.N((float)ElapsedSeconds()));
                sb.Append(",\"aAlive\":-1,\"dAlive\":-1");
                sb.Append('}');
                ResultJson = sb.ToString();
                _endRequested = false;
                State = RunStateError;
                try
                {
                    Mission m = SafeMission();
                    if (m != null) m.EndMission();
                }
                catch
                {
                }
            }
            catch
            {
            }
        }

        // ── 内部实现 ─────────────────────────────────────────────────────

        /// <summary>解析 "x,z" 或 "x,y,z" → Vec3?（空串/格式错 = null，表示用引擎默认生成点）。</summary>
        private static Vec3? ParseVec3(string s)
        {
            try
            {
                if (string.IsNullOrEmpty(s)) return null;
                string[] p = s.Split(',');
                if (p.Length < 2 || p.Length > 3) return null;
                float x = float.Parse(p[0].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                float y = p.Length == 3
                    ? float.Parse(p[1].Trim(), System.Globalization.CultureInfo.InvariantCulture) : 0f;
                float z = float.Parse(p[p.Length - 1].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                return new Vec3(x, y, z);
            }
            catch
            {
                return null;
            }
        }

        private static BasicCharacterObject Resolve(string troopId)
        {
            try
            {
                if (string.IsNullOrEmpty(troopId)) return null;
                return MBObjectManager.Instance.GetObject<BasicCharacterObject>(troopId);
            }
            catch
            {
                return null;
            }
        }

        /// <summary>
        /// 场景存在性校验。**必须在 OpenMission 之前调用**，因为引擎侧没有可判的失败路径：
        ///
        ///   源码依据（反编译 TaleWorlds.Engine.dll）：
        ///     Scene.Read(string sceneName) → EngineApplicationInterface.IScene.Read(...)
        ///   `Scene.Read` 的整个实现体就是一次原生调用，**没有返回值、不抛托管异常**。
        ///   场景名不存在时 native 侧拿到空指针，直接 0xC0000005（访问违规）终止进程 ——
        ///   try/catch 在结构上拦不住（崩在 C++ 层，不是托管异常）。
        ///
        ///   实测依据（v0.8.9，2026-09-25 11:40）：
        ///     11:40:31.868  Loading xml file: SceneObj/bridge/scene.xscene
        ///     11:40:31.874  Unhandled Exception Code 0xC0000005   ← 6 ms 后
        ///   对照：同一请求传 battle_terrain_a（存在于 SandBoxCore/SceneObj/）则正常。
        ///
        /// 检查方式：遍历**全部已激活模块**的 `SceneObj/&lt;scene&gt;/scene.xscene`（见 SceneSearchModules）。
        ///   引擎自己的场景寻址就是"扫所有模块的 SceneObj"（$BASE/Modules/&lt;id&gt;/SceneObj/...），
        ///   所以文件系统查与引擎查一致，且不需要进 native。
        ///   ⚠️ 只认 `scene.xscene` 这一必需文件：`Modules/SandBoxCore/SceneObj/battle_terrain_a/`
        ///      下还有 atmosphere.xml / terrain.bin / navmesh.bin，但只有 scene.xscene 是每个
        ///      场景都必有的入口文件（实测该目录与其他场景目录均有）。
        /// </summary>
        /// <remarks>v0.8.14 起为 internal：`UiEntry` 的官方场景表（`CustomBattleScenes`）要逐行标
        /// `exists` —— 一样的判据，不能让两处各写一份（那类"两个判定器各自漂移"本项目吃过亏）。</remarks>
        internal static bool SceneExists(string scene)
        {
            if (string.IsNullOrEmpty(scene)) return false;
            try
            {
                if (!IsSafeSceneName(scene)) return false;

                foreach (ModuleInfo mi in SceneSearchModulesInternal())
                {
                    string probe = Path.Combine(mi.FolderPath, "SceneObj", scene, "scene.xscene");
                    if (File.Exists(probe)) return true;
                }
                return false;
            }
            catch
            {
                // 校验本身失败时**不静默放行**：调用方按"不存在"处理并报错，
                // 宁可拒绝一次合法请求，也不放一次会崩进程的请求过去。
                return false;
            }
        }

        /// <summary>
        /// 场景名必须是**单个目录段**：这个名字会被拼进文件系统路径。
        ///
        /// - 含路径分隔符 ⇒ 等于允许请求越权指向任意文件（v0.8.9 已拦）。
        /// - `.` / `..` **不含分隔符却不被拦**，而 `SceneObj/../scene.xscene` 会逃出 SceneObj/
        ///   （v0.8.10 审查发现）。按 AGENTS.md §五「请求参数是不可信输入」，
        ///   判据取"归一化后仍须在 &lt;module&gt;/SceneObj/ 之下"，故显式拒绝这两个名字。
        /// </summary>
        private static bool IsSafeSceneName(string scene)
        {
            if (string.IsNullOrEmpty(scene)) return false;
            if (scene.IndexOf('/') >= 0 || scene.IndexOf('\\') >= 0) return false;
            if (scene == "." || scene == "..") return false;
            return true;
        }

        /// <summary>
        /// 场景搜寻用的模块清单：**只认已激活模块**。
        ///
        /// 判据来自引擎自己的同类实现（"这个场景存在吗"）：
        /// `ModuleHelper.GetActiveModules()` + `activeModule.IsActive` + `Path.Combine(FolderPath, ...)`。
        /// 用 `GetAllModules()` 会把**未启用**模块里同名 `SceneObj/` 也算存在 ⇒ 放行一个引擎解析不到的场景
        /// （fail-open；后果层未观测到实例，按 AGENTS.md §四 属 low，但改法与引擎一致、零成本）。
        ///
        /// ⚠️ FolderPath 的形态（v0.8.10 反编译确证，此前只能是高置信推断）：
        ///   `ModuleInfo.LoadWithFullPath`：`FolderPath = fullPath;` 紧接着 `FolderPath + "/SubModule.xml"`，
        ///   而运行日志同一处渲染为 `..\..\Modules\SandBoxCore/SubModule.xml`
        ///   ⇒ **FolderPath 不含尾分隔符**，必须用 `Path.Combine` 拼接。
        ///   v0.8.9 写的 `mi.FolderPath + "SceneObj/"` 因此得到 `...SandBoxCoreSceneObj/...`（恒不存在）
        ///   ⇒ 该守卫会把**每一次** start 都判成 unknown_scene。它提交后 0 次真机运行，故未被发现。
        /// </summary>
        private static List<ModuleInfo> SceneSearchModulesInternal()
        {
            List<ModuleInfo> modules = new List<ModuleInfo>();
            foreach (ModuleInfo mi in ModuleHelper.GetActiveModules())
            {
                if (mi == null || !mi.IsActive) continue;
                if (string.IsNullOrEmpty(mi.FolderPath)) continue;
                modules.Add(mi);
            }
            return modules;
        }

        /// <summary>
        /// 已激活模块的根目录（**不含**尾分隔符，理由见 SceneSearchModules 的 FolderPath 说明）。
        ///
        /// v0.8.14：给 `CustomBattleScenes` 用 —— 它按各模块 `SubModule.xml` 里的
        /// `&lt;XmlName id="CustomBattleScenes" path="…"/&gt;` 声明去找 `ModuleData/&lt;path&gt;.xml`，
        /// 属**纯文件读**，不碰 `MBObjectManager`。
        /// 为什么不复用引擎的 `GetMergedXmlForManaged`：见 PROGRESS §二十六 —— 那条依赖会
        /// 在"引擎自己正在合并/加载 XML"的窗口里被我们从主线程插进去，风险不可控；
        /// 只读的数据用只读的办法拿。
        /// </summary>
        internal static List<string> ActiveModuleFolders()
        {
            List<string> folders = new List<string>();
            foreach (ModuleInfo mi in SceneSearchModulesInternal()) folders.Add(mi.FolderPath);
            return folders;
        }

        /// <summary>
        /// 可用野战场景（`battle_*` 前缀），排序后返回。**与 `ValidateScene` 同一判据**：
        /// 同一个 `SceneSearchModules()`（只认已激活模块）+ 同样只收 `battle_` 开头的目录。
        /// 一物两用：① 上面错误消息里的清单；② `list_ui` 的 `scenes` 字段与面板的场景轮选。
        /// （面板不再硬编码场景名 —— 硬编码的名字一旦不存在，玩家第一次点「开始战斗」就吃 unknown_scene。）
        /// </summary>
        internal static List<string> AvailableBattleScenes()
        {
            List<string> scenes = new List<string>();
            try
            {
                foreach (ModuleInfo mi in SceneSearchModulesInternal())
                {
                    string sceneObj = Path.Combine(mi.FolderPath, "SceneObj");
                    if (!Directory.Exists(sceneObj)) continue;
                    foreach (string dir in Directory.GetDirectories(sceneObj))
                    {
                        string name = Path.GetFileName(dir);
                        if (!name.StartsWith("battle_", StringComparison.Ordinal)) continue;
                        if (scenes.IndexOf(name) >= 0) continue;
                        scenes.Add(name);
                    }
                }
                scenes.Sort(StringComparer.Ordinal);
            }
            catch
            {
            }
            return scenes;
        }

        /// <summary>
        /// 场景校验 + 可读错误消息（含可用场景清单，避免调用方反复试错）。
        /// 返回 false 时 code/message 已填好。
        /// </summary>
        private static bool ValidateScene(string scene, out string code, out string message)
        {
            code = null;
            message = null;
            if (SceneExists(scene)) return true;

            code = "unknown_scene";
            StringBuilder sb = new StringBuilder();
            sb.Append("场景不存在: ").Append(scene);
            sb.Append("。引擎侧无法安全失败（Scene.Read 崩在原生层，0xC0000005），故在此提前拦截。");

            // 附上可用场景清单：只列野战战场（battle_*），避免把几百个城镇/城堡场景全刷出来。
            List<string> battleScenes = AvailableBattleScenes();
            if (battleScenes.Count > 0)
            {
                sb.Append(" 可用野战场景（最多列 12 个，默认 battle_terrain_a）: ");
                sb.Append(string.Join(", ", battleScenes.GetRange(
                    0, Math.Min(12, battleScenes.Count)).ToArray()));
                if (battleScenes.Count > 12) sb.Append(" …");
            }
            message = sb.ToString();
            return false;
        }

        /// <summary>
        /// 按需加载基础对象（文化 + 角色），用于"游戏内数据还没填充"的场合。
        /// 实测依据：停在主菜单时**所有**兵种 id 都报 unknown_troop。
        /// 引擎自己就是这么加载的（反编译所见）：
        ///   EditorGame.cs:58  ObjectManager.LoadXML("SPCultures");
        ///   EditorGame.cs:57  ObjectManager.LoadXML("NPCCharacters");
        ///   SandBox/MultiplayerItemTestMissionController.cs:33  Game.Current.ObjectManager.LoadXML("MPCharacters");
        /// 只在兵种解析失败后调用，避免无谓的重复加载。
        /// </summary>
        private static bool TryLoadBaseObjects()
        {
            try
            {
                MBObjectManager om = MBObjectManager.Instance;
                if (om == null) return false;
                om.LoadXML("SPCultures");     // 文化先于角色（角色会引用文化）
                om.LoadXML("NPCCharacters");  // 兵种
                return true;
            }
            catch (Exception ex)
            {
                LastError = "loadxml: " + ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        private static readonly string[] Banners = new string[]
        {
            "11.4.124.4345.4345.768.768.1.0.0.163.0.5.512.512.769.764.1.0.0",
            "11.45.126.4345.4345.768.768.1.0.0.462.0.13.512.512.769.764.1.0.0"
        };

        // ── v0.8.11：攻城场景走官方 siege mission ────────────────────────────────

        /// <summary>
        /// 本场是否为 siege（攻城）模式。`Start` 每次开战前设置，供 `ScenarioProbe` 读。
        /// </summary>
        internal static bool SiegePending;

        /// <summary>
        /// 下一场是否要「上帝视角」观战（v0.8.14，`start_battle` 的 `spectate` 参数）。
        ///
        /// **消费即清**（SubModule.OnBeforeMissionBehaviorInitialize 里读完立刻置 false）：
        /// 这样即使收尾复位漏了，也绝不会把自由镜头带到玩家自己打的战斗里 ——
        /// 这个项目在"静态标志污染下一场"上踩过坑（见 DummyRangeBehavior 的复位注释）。
        ///
        /// 它只控制"挂不挂 `SpectatorWatchBehavior`"（镜头），**不碰指挥权** ——
        /// 指挥权仍由 `isPlayerGeneral=false` 那条既有修复决定（防止攻方停下来等玩家下令）。
        /// </summary>
        internal static bool SpectateRequested;

        /// <summary>
        /// 场景名 → 是否攻城场景（启发式）。
        ///
        /// 为什么必须区分（2026-09-25 真机受控对照）：城池场景（SandBox/SandBoxCore 的
        /// `*_castle_*` / `*_town_*`）在 `MissionTeamAITypeEnum.FieldBattle` 装配下加载**必崩**
        /// （native `0xc0000005`，进程死，只来得及写 meta 行）；同一请求换 `battle_terrain_a`
        /// 则完全正常（2.24 MB 落盘）。城池场景必须由 siege mission 初始化。
        ///
        /// ⚠️ 这是**启发式**（按名字），不是引擎判据：排除 `*_keep_*` / `*_interior`
        /// （领主大厅等室内场景，名字里也含 `_castle_`）。后续应改为显式请求参数。
        /// </summary>
        internal static bool IsSiegeScene(string scene)
        {
            if (string.IsNullOrEmpty(scene)) return false;
            string s = scene.ToLowerInvariant();
            if (s.IndexOf("_keep_", System.StringComparison.Ordinal) >= 0) return false;
            if (s.IndexOf("interior", System.StringComparison.Ordinal) >= 0) return false;
            if (s.IndexOf("_castle_", System.StringComparison.Ordinal) >= 0) return true;
            if (s.IndexOf("_town_", System.StringComparison.Ordinal) >= 0) return true;
            return false;
        }

        /// <summary>
        /// 攻城开战：照官方自定义战斗（`CustomBattleHelper.StartGame` 的 "Siege" 分支）与
        /// 官方 CPU benchmark（`CPUBenchmarkMissionLogic` 的 siege 分支）的用法。
        ///
        /// 与野战路径的两处关键差异：
        ///   1) 入口是 `BannerlordMissions.OpenSiegeMissionWithDeployment` —— 城墙/攻城器械/攻城 AI/
        ///      部署流程全部由引擎装配，BlBridge 的 `CreateBehaviors` 不参与；
        ///   2) 它**返回 Mission** ⇒ 官方同款做法是随后 `AddMissionBehavior` 挂自己的行为
        ///      （`CPUBenchmarkMissionLogic.cs:1341`）。此处挂 `ScenarioProbe`：
        ///      它的 `OnBehaviorInitialize` 不会跑，但状态机全在 `OnMissionTick` 里，照常工作。
        /// </summary>
        private static void OpenSiegeMission(string scene, BasicCharacterObject attackerTroop,
            BasicCharacterObject defenderTroop, int aCount, int dCount)
        {
            SiegeTrace("enter scene=" + scene + " a=" + aCount + " d=" + dCount);

            BasicCultureObject culture = MBObjectManager.Instance.GetObject<BasicCultureObject>("empire");

            CustomBattleCombatant attacker = new CustomBattleCombatant(
                new TextObject("{=!}BlBridge Attacker"), culture, new Banner(Banners[0]));
            attacker.Side = BattleSideEnum.Attacker;
            attacker.AddCharacter(attackerTroop, aCount);

            CustomBattleCombatant defender = new CustomBattleCombatant(
                new TextObject("{=!}BlBridge Defender"), culture, new Banner(Banners[1]));
            defender.Side = BattleSideEnum.Defender;
            defender.AddCharacter(defenderTroop, dCount);

            // 墙段血量：官方 `CustomBattleHelper.GetWallHitpointPercentages(0)` 就是 {1f, 1f}（两段完好）
            float[] wallHitPointPercentages = new float[2] { 1f, 1f };

            // 器械清单：id 与数量照 CPUBenchmarkMissionLogic 的 siege 分支
            // （攻方 = 4 远程 + 1 攻城塔 + 1 撞车；守方 = 4 远程）
            List<MissionSiegeWeapon> attackerMachines = BuildSiegeMachines(new string[]
            {
                "fire_ballista", "fire_ballista", "trebuchet", "trebuchet", "siege_tower_level2", "ram"
            });
            List<MissionSiegeWeapon> defenderMachines = BuildSiegeMachines(new string[]
            {
                "fire_ballista", "fire_ballista", "fire_ballista", "fire_ballista"
            });
            SiegeTrace("machines atk=" + attackerMachines.Count + " def=" + defenderMachines.Count);

            bool isPlayerAttacker = _pendingPlayerSide == BattleSideEnum.Attacker;
            CustomBattleCombatant playerParty = isPlayerAttacker ? attacker : defender;
            CustomBattleCombatant enemyParty = isPlayerAttacker ? defender : attacker;

            // 官方入口的第一个参数是"玩家角色"：无玩家也必须有（`CustomBattleHelper.StartGame`
            // 会先写 `Game.Current.PlayerTroop`，CPUBenchmark 传的是 commander_1）。
            BasicCharacterObject playerCharacter =
                MBObjectManager.Instance.GetObject<BasicCharacterObject>("commander_1");
            if (playerCharacter == null) playerCharacter = attackerTroop;
            try { Game.Current.PlayerTroop = playerCharacter; }
            catch { }
            // 照 CPUBenchmarkMissionLogic：玩家角色**必须在该方的 combatant 里**
            // （它把 commander_1 也 AddCharacter 进 playerParty）。代价：该方人数 +1 —— 记着这个口径偏移。
            try { playerParty.AddCharacter(playerCharacter, 1); }
            catch (Exception ex) { AppendSiegeError("add playerCharacter: " + ex.GetType().Name); }

            SiegeTrace("calling OpenSiegeMissionWithDeployment playerChar="
                + (playerCharacter == null ? "null" : playerCharacter.StringId)
                + " isPlayerAttacker=" + isPlayerAttacker
                + " atk=" + attacker.NumberOfHealthyMembers + " def=" + defender.NumberOfHealthyMembers);

            Mission mission = BannerlordMissions.OpenSiegeMissionWithDeployment(
                scene,
                playerCharacter,
                playerParty,
                enemyParty,
                false,          // isPlayerGeneral：**改为 false** ——
                                //   官方自定义战斗里玩家是真人、会下命令；传 true 时攻方（玩家侧）
                                //   疑似在等玩家指挥，实测推到墙下后全体停摆（aiState 只剩 AlarmStateMask）。
                                //   commander_1 仍留在 party 里（不留在 party 的话首次尝试崩过）。
                wallHitPointPercentages,
                true,           // hasAnySiegeTower：与清单里的 siege_tower_level2 对应
                attackerMachines,
                defenderMachines,
                isPlayerAttacker,
                SiegeSceneLevel,   // sceneUpgradeLevel（默认 3 = CPUBenchmark 同值；v0.8.41 起可设）
                "",             // seasonString
                false,          // isSallyOut
                false,          // isReliefForceAttack
                SiegeTimeOfDay);   // timeOfDay（默认 6f = 改动前的写死值；v0.8.41 起可设）

            SiegeTrace("returned mission=" + (mission == null ? "null" : "ok"));

            if (mission != null)
            {
                mission.AddMissionBehavior(new ScenarioProbe());
                SiegeTrace("probe attached");
                // 关键诊断：确认"攻城语义"真的开着（`IsSiegeBattle == MissionTeamAIType == Siege`）。
                // 若它是 false，则引擎的攻城 AI 与 SiegeAIFix 的攻城逻辑**整段不执行** ⇒ 攻方必然停摆。
                try
                {
                    SiegeTrace("mission flags: IsSiegeBattle=" + mission.IsSiegeBattle
                        + " TeamAIType=" + mission.MissionTeamAIType
                        + " IsFieldBattle=" + mission.IsFieldBattle
                        + " IsSallyOut=" + mission.IsSallyOutBattle);
                }
                catch (Exception ex)
                {
                    SiegeTrace("flags read failed: " + ex.GetType().Name);
                }
            }
        }

        /// <summary>
        /// 按 id 批量取 `SiegeEngineType` 并包成 `MissionSiegeWeapon`。
        /// 取不到 / 工厂返回 null 一律**跳过**（宁可少一件器械，也不让 null 进引擎），并记账到 LastError。
        /// </summary>
        private static List<MissionSiegeWeapon> BuildSiegeMachines(string[] engineTypeIds)
        {
            List<MissionSiegeWeapon> machines = new List<MissionSiegeWeapon>();
            MBObjectManager om = MBObjectManager.Instance;
            if (om == null) return machines;
            for (int i = 0; i < engineTypeIds.Length; i++)
            {
                SiegeEngineType type = om.GetObject<SiegeEngineType>(engineTypeIds[i]);
                if (type == null)
                {
                    AppendSiegeError("siege engine type 不存在: " + engineTypeIds[i]);
                    continue;
                }
                MissionSiegeWeapon weapon = MissionSiegeWeapon.CreateDefaultWeapon(type);
                if (weapon == null)
                {
                    AppendSiegeError("CreateDefaultWeapon 返回 null: " + engineTypeIds[i]);
                    continue;
                }
                machines.Add(weapon);
            }
            return machines;
        }

        private static void AppendSiegeError(string detail)
        {
            string entry = "siege: " + detail;
            if (LastError.Contains(entry)) return;
            LastError = string.IsNullOrEmpty(LastError) ? entry : LastError + " | " + entry;
        }

        /// <summary>
        /// 攻城路径的定向插桩：把装配节点追加写到 `&lt;LogDir&gt;\siege_debug.log`。
        ///
        /// 为什么需要：真机失败是**进程级**的（BUTR CrashReport + 进程消失），日志里拿不到托管栈。
        /// 最后写成功的那一行就是"界碑"—— 它能区分「崩在我们自己的代码里」与「崩在引擎内部
        /// （`OpenSiegeMissionWithDeployment` 返回之前）」，而这正是当前最需要知道的事。
        /// 整体 try/catch：插桩绝不能成为新的崩溃源。
        /// </summary>
        private static void SiegeTrace(string message)
        {
            try
            {
                string dir = BridgeConfig.LogDir;
                if (string.IsNullOrEmpty(dir)) return;
                Directory.CreateDirectory(dir);
                File.AppendAllText(Path.Combine(dir, "siege_debug.log"),
                    Jw.UtcNow() + " " + message + "\n", new UTF8Encoding(false));
            }
            catch
            {
            }
        }

        private static void OpenMission(string scene, BasicCharacterObject attackerTroop,
            BasicCharacterObject defenderTroop, int aCount, int dCount)
        {
            BasicCultureObject culture = MBObjectManager.Instance.GetObject<BasicCultureObject>("empire");

            // ── T5：任一方给了 groups ⇒ 走"每方一个自定义 troop supplier"的组路径；
            //         否则原样走旧路径（下面的代码逐字节不变）。──
            bool attGrouped = _pendingAttackerGroups != null && _pendingAttackerGroups.Count > 0;
            bool defGrouped = _pendingDefenderGroups != null && _pendingDefenderGroups.Count > 0;
            if (attGrouped || defGrouped)
            {
                OpenMissionGrouped(scene, culture, attackerTroop, defenderTroop, aCount, dCount,
                    attGrouped, defGrouped);
                return;
            }

            CustomBattleCombatant attacker = new CustomBattleCombatant(
                new TextObject("{=!}BlBridge Attacker"), culture, new Banner(Banners[0]));
            attacker.Side = BattleSideEnum.Attacker;
            attacker.AddCharacter(attackerTroop, aCount);

            CustomBattleCombatant defender = new CustomBattleCombatant(
                new TextObject("{=!}BlBridge Defender"), culture, new Banner(Banners[1]));
            defender.Side = BattleSideEnum.Defender;
            defender.AddCharacter(defenderTroop, dCount);

            IMissionTroopSupplier[] suppliers = new IMissionTroopSupplier[2];
            suppliers[(int)attacker.Side] = new CustomBattleTroopSupplier(attacker, false, false, false, null);
            suppliers[(int)defender.Side] = new CustomBattleTroopSupplier(defender, false, false, false, null);

            _pendingAttacker = attacker;
            _pendingDefender = defender;
            _pendingSuppliers = suppliers;

            MissionInitializerRecord rec = new MissionInitializerRecord(scene);
            rec.DoNotUseLoadingScreen = false;
            rec.PlayingInCampaignMode = false;
            rec.DecalAtlasGroup = 2;
            // v0.8.41：环境旋钮（只写调用方显式请求过的字段；不请求 ⇒ record 逐字段与改动前一致）
            BattleEnv.Apply(ref rec);

            MissionState.OpenNew("BlBridgeScenario", rec, CreateBehaviors, true, true);
        }

        /// <summary>
        /// T5 组路径：整体 combatant（含全部组，供 leader / spawn handler / 总人数）+ 组级 combatant。
        /// ⚠️ CustomBattleMissionSpawnHandler.AfterStart() 用整体 combatant 的 NumberOfHealthyMembers
        ///    当 total 与 initial spawn 数 ⇒ 整体 combatant 必须是全组之和。
        /// 每方各自判断：一方有 groups、另一方没有也应支持（无 groups 的一侧仍用单个 supplier）。
        /// </summary>
        private static void OpenMissionGrouped(string scene, BasicCultureObject culture,
            BasicCharacterObject attackerTroop, BasicCharacterObject defenderTroop,
            int aCount, int dCount, bool attackerGrouped, bool defenderGrouped)
        {
            CustomBattleCombatant attacker = new CustomBattleCombatant(
                new TextObject("{=!}BlBridge Attacker"), culture, new Banner(Banners[0]));
            attacker.Side = BattleSideEnum.Attacker;
            CustomBattleCombatant defender = new CustomBattleCombatant(
                new TextObject("{=!}BlBridge Defender"), culture, new Banner(Banners[1]));
            defender.Side = BattleSideEnum.Defender;

            IMissionTroopSupplier attackerSupplier = BuildSideSupplier(attacker, attackerGrouped,
                _pendingAttackerGroups, attackerTroop, aCount, BattleSideEnum.Attacker, culture,
                Banners[0], "Attacker");
            IMissionTroopSupplier defenderSupplier = BuildSideSupplier(defender, defenderGrouped,
                _pendingDefenderGroups, defenderTroop, dCount, BattleSideEnum.Defender, culture,
                Banners[1], "Defender");

            IMissionTroopSupplier[] suppliers = new IMissionTroopSupplier[2];
            suppliers[(int)attacker.Side] = attackerSupplier;
            suppliers[(int)defender.Side] = defenderSupplier;

            _pendingAttacker = attacker;
            _pendingDefender = defender;
            _pendingSuppliers = suppliers;

            MissionInitializerRecord rec = new MissionInitializerRecord(scene);
            rec.DoNotUseLoadingScreen = false;
            rec.PlayingInCampaignMode = false;
            rec.DecalAtlasGroup = 2;
            // v0.8.41：环境旋钮（与单值路径同一个调用点语义：只写显式请求过的字段）
            BattleEnv.Apply(ref rec);

            MissionState.OpenNew("BlBridgeScenario", rec, CreateBehaviors, true, true);
        }

        /// <summary>
        /// 构造一方的 supplier：无 groups ⇒ 沿用单个 CustomBattleTroopSupplier（与旧路径同参）；
        /// 有 groups ⇒ 每组一个组级 combatant + 组级 supplier，外层包 SquadTroopSupplier 做顺序编排。
        /// </summary>
        private static IMissionTroopSupplier BuildSideSupplier(CustomBattleCombatant whole, bool grouped,
            List<SquadSpec> specs, BasicCharacterObject singleTroop, int singleCount,
            BattleSideEnum side, BasicCultureObject culture, string banner, string label)
        {
            if (!grouped)
            {
                whole.AddCharacter(singleTroop, singleCount);
                return new CustomBattleTroopSupplier(whole, false, false, false, null);
            }

            CustomBattleTroopSupplier[] groupSuppliers = new CustomBattleTroopSupplier[specs.Count];
            for (int i = 0; i < specs.Count; i++)
            {
                SquadSpec s = specs[i];
                BasicCharacterObject troop = Resolve(s.Troop);
                // 整体 combatant 必须含全部组（spawn handler 拿它当 total / initial spawn）
                whole.AddCharacter(troop, s.Count);
                // 组级 combatant：名字带组号便于排查；Side/culture/banner 与整体一致
                CustomBattleCombatant group = new CustomBattleCombatant(
                    new TextObject("{=!}BlBridge " + label + " Group " + (i + 1)), culture,
                    new Banner(banner));
                group.Side = side;
                group.AddCharacter(troop, s.Count);
                groupSuppliers[i] = new CustomBattleTroopSupplier(group, false, false, false, null);
            }
            return new SquadTroopSupplier(specs, groupSuppliers, whole);
        }

        private static int SumCounts(List<SquadSpec> specs)
        {
            int total = 0;
            if (specs != null)
            {
                for (int i = 0; i < specs.Count; i++) total += specs[i].Count;
            }
            return total;
        }

        /// <summary>
        /// v0.8.32：把某一方 `formation` 字段与**兵种实际编队**的不一致收集进 `_pendingFormationWarnings`
        /// （加"攻方/守方"前缀），最终出现在 start 响应的 `formationWarnings` 里。
        ///
        /// 实际值从校验阶段**已 Resolve 的兵种对象**取（`troop.GetFormationClass()`，
        /// 与 `ApplyOrders` / `ReapplySideOrders` 同一个来源），再经 `EnumNames.Formation` 换成稳定名
        /// （`FormationClass` 有同值别名，`ToString()` 会给出边界常量名）。
        /// 比对本身在**只依赖 BCL** 的 `SquadSpec.CollectFormationMismatches`（离线单测锁它）。
        /// 绝不抛：这条信号不该把开战流程拖崩。
        /// </summary>
        private static void AppendFormationWarnings(string label, List<SquadSpec> specs,
                                                   List<BasicCharacterObject> resolvedTroops)
        {
            try
            {
                if (specs == null || resolvedTroops == null) return;
                string[] actual = new string[resolvedTroops.Count];
                for (int i = 0; i < resolvedTroops.Count; i++)
                {
                    BasicCharacterObject c = resolvedTroops[i];
                    actual[i] = (c == null) ? null : EnumNames.Formation(c.GetFormationClass());
                }
                List<string> ms = SquadSpec.CollectFormationMismatches(specs, actual);
                for (int i = 0; i < ms.Count; i++) _pendingFormationWarnings.Add(label + "：" + ms[i]);
            }
            catch
            {
            }
        }

        /// <summary>v0.8.41：把 `_pendingEnvNotes` 渲染成 JSON 数组（无 ⇒ `[]`）。绝不抛。</summary>
        private static string EnvNotesJson()
        {
            try
            {
                if (_pendingEnvNotes == null || _pendingEnvNotes.Count == 0) return "[]";
                StringBuilder sb = new StringBuilder("[");
                for (int i = 0; i < _pendingEnvNotes.Count; i++)
                {
                    if (i > 0) sb.Append(',');
                    sb.Append(Protocol.Q(_pendingEnvNotes[i]));
                }
                sb.Append(']');
                return sb.ToString();
            }
            catch
            {
                return "[]";
            }
        }

        /// <summary>v0.8.41：战术档位的单行 JSON（请求值 + 引擎原生值；都未参与 ⇒ `null`）。绝不抛。</summary>
        private static string TacticsJson()
        {
            try
            {
                if (AttackerTacticsRequested < 0 && DefenderTacticsRequested < 0
                    && AttackerTacticsNative < 0 && DefenderTacticsNative < 0) return "null";
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"aRequested\":").Append(Jw.N(AttackerTacticsRequested));
                sb.Append(",\"dRequested\":").Append(Jw.N(DefenderTacticsRequested));
                sb.Append(",\"aNative\":").Append(Jw.N(AttackerTacticsNative));
                sb.Append(",\"dNative\":").Append(Jw.N(DefenderTacticsNative));
                sb.Append('}');
                return sb.ToString();
            }
            catch
            {
                return "null";
            }
        }

        /// <summary>v0.8.32：把 `_pendingFormationWarnings` 渲染成 JSON 数组（无警告 ⇒ `[]`）。绝不抛。</summary>
        private static string FormationWarningsJson()
        {
            try
            {
                if (_pendingFormationWarnings == null || _pendingFormationWarnings.Count == 0) return "[]";
                StringBuilder sb = new StringBuilder("[");
                for (int i = 0; i < _pendingFormationWarnings.Count; i++)
                {
                    if (i > 0) sb.Append(',');
                    sb.Append(Protocol.Q(_pendingFormationWarnings[i]));
                }
                sb.Append(']');
                return sb.ToString();
            }
            catch
            {
                return "[]";
            }
        }

        /// <summary>
        /// 逐组校验：兵种 id 可解析（否则 unknown_troop），以及"同一实际编队被多组以不同 movement
        /// **落点**命中"⇒ conflicting_movements（§3 用户裁决 A）。落点编队 = Resolve(troop).GetFormationClass()，
        /// 不是 DSL 里写的 formation 字段。
        /// 冲突比较用 `MovementKey` 的**落点名**比较（纯字符串：绝不能在此处碰 `MovementOrder`，
        /// 见 §1 与 `MovementKey` 的 doc）—— 不同 movement 若落同一点不算冲突（修复轮 1 · M2）。
        /// rawSegments：该方 DSL 按 | 切分的原始片段（与 specs 下标一致），用于逐字回显（修复轮 1 · m2）。
        /// T6：resolvedTroops 带出逐组 Resolve 的兵种对象（下标与 specs 一一对应），
        /// 供多轮编排器按组重生复用，避免重复解析。
        /// T14：未知兵种 id **一次报全部**（收集后统一返回），避免"只报第一个"逼得扫描方反复
        /// start/abort 二分（实测会把引擎打进异常状态）；语义不变——仍报错并中止整场（GC3）。
        /// </summary>
        private static bool ValidateGroupedSide(List<SquadSpec> specs, string[] rawSegments, string label,
            out string code, out string message, out List<BasicCharacterObject> resolvedTroops)
        {
            code = null;
            message = null;
            resolvedTroops = null;
            List<BasicCharacterObject> troops = new List<BasicCharacterObject>();
            // T14：收集**全部** Resolve 失败的条目（1 基组号 + 逐字 segment 原文 + 兵种 id），
            // 循环结束后一次性报出，避免"只报第一个"逼得扫描方反复 start/abort 二分（实测会把引擎
            // 打进异常状态：mission 卡 loading/busy，abort 无效）。失败的组跳过后续 formation/冲突校验。
            List<string> unknownEntries = new List<string>();
            bool triedLoad = false;
            Dictionary<FormationClass, string> orderByFormation =
                new Dictionary<FormationClass, string>();
            Dictionary<FormationClass, string> movementByFormation = new Dictionary<FormationClass, string>();
            Dictionary<FormationClass, string> rawByFormation = new Dictionary<FormationClass, string>();
            for (int i = 0; i < specs.Count; i++)
            {
                SquadSpec s = specs[i];
                BasicCharacterObject troop = Resolve(s.Troop);
                if (troop == null && !triedLoad)
                {
                    // 修复轮 1 · m4：整个循环最多补加载一次（原先每个未知组各加载一次）
                    triedLoad = true;
                    if (TryLoadBaseObjects()) troop = Resolve(s.Troop);
                }
                if (troop == null)
                {
                    unknownEntries.Add("第 " + (i + 1) + " 组 '" + RawSegment(rawSegments, i, s.Troop)
                        + "'（" + s.Troop + "）");
                    continue;
                }
                troops.Add(troop);
                FormationClass fc = troop.GetFormationClass();
                string mv = s.Movement == null ? "charge" : s.Movement;
                string ord = ScenarioProbe.MovementKey(mv);
                string raw = RawSegment(rawSegments, i, s.Troop);
                string prevOrd;
                if (orderByFormation.TryGetValue(fc, out prevOrd))
                {
                    if (prevOrd != ord)
                    {
                        code = "conflicting_movements";
                        message = label + "的编队 " + fc + " 被多组以不同 movement 命中: "
                            + rawByFormation[fc] + "(" + movementByFormation[fc] + ") 与 " + raw + "(" + mv + ")";
                        return false;
                    }
                }
                else
                {
                    orderByFormation[fc] = ord;
                    movementByFormation[fc] = mv;
                    rawByFormation[fc] = raw;
                }
            }
            if (unknownEntries.Count > 0)
            {
                // 语义不变（GC3：非法即报错、绝不静默）：仍报错并中止整场，只是从"第一个"换成"全部"。
                // 2026-09-25：不再截断为前 8 条 —— 全兵种扫描需要一次拿到完整坏 id 清单
                // （普通误输入通常只有几条，消息长度不是问题）。
                code = "unknown_troop";
                message = label + "有 " + unknownEntries.Count + " 个兵种 id 不存在："
                    + string.Join("、", unknownEntries.ToArray());
                return false;
            }
            resolvedTroops = troops;
            return true;
        }

        /// <summary>取该组的逐字原文片段；越界时回退到 spec 重建（防御性）。</summary>
        private static string RawSegment(string[] rawSegments, int i, string fallback)
        {
            if (rawSegments != null && i >= 0 && i < rawSegments.Length) return rawSegments[i];
            return fallback;
        }

        // ── T7：首轮 squad 事件（source="supplier"）─────────────────────────
        /// <summary>
        /// 首轮入场完成时写两侧的 squad 事件（GC4 触发点 1，由 ScenarioProbe.OnMissionTick 调用）。
        /// spawned 口径 = `SquadTroopSupplier.ProvidedCounts[group]`（**交给引擎的 origin 数**，
        /// 不是实际 spawn 的 agent 数 —— 首轮这部分由引擎经 supplier 生成，我们只知道自己供了多少）。
        /// round 恒为 1（首轮）。无 groups 的一方 specs 为 null ⇒ 该侧不写（GC2）。
        /// </summary>
        private static void WriteFirstRoundSquadEvents()
        {
            try
            {
                List<SquadSpec> aSpecs = _pendingAttackerGroups;
                List<SquadSpec> dSpecs = _pendingDefenderGroups;
                bool aHas = (aSpecs != null) ? (aSpecs.Count > 0) : false;
                bool dHas = (dSpecs != null) ? (dSpecs.Count > 0) : false;
                if (!aHas)
                {
                    if (!dHas) return;
                }
                TelemetryBehavior.WriteSquadEvents(1, "supplier",
                    aSpecs, ResolveAll(aSpecs), ProvidedOf(BattleSideEnum.Attacker),
                    dSpecs, ResolveAll(dSpecs), ProvidedOf(BattleSideEnum.Defender));
            }
            catch
            {
            }
        }

        /// <summary>取某方 supplier 的逐组"已交给引擎的 origin 数"；非组路径/取不到 ⇒ null。</summary>
        private static int[] ProvidedOf(BattleSideEnum side)
        {
            try
            {
                IMissionTroopSupplier[] sup = _pendingSuppliers;
                if (sup == null) return null;
                int i = (int)side;
                if (i < 0) return null;
                if (i >= sup.Length) return null;
                SquadTroopSupplier s = sup[i] as SquadTroopSupplier;
                if (s == null) return null;
                IReadOnlyList<int> pc = s.ProvidedCounts;
                if (pc == null) return null;
                int[] arr = new int[pc.Count];
                for (int k = 0; k < pc.Count; k++) arr[k] = pc[k];
                return arr;
            }
            catch
            {
                return null;
            }
        }

        /// <summary>
        /// 逐组 Resolve 兵种（下标与 specs 一一对应）；缺 ⇒ 该位为 null
        /// （squad 事件的 formation 随之落 "Unset"）。仅供 squad 事件的 formation 取值。
        /// </summary>
        private static List<BasicCharacterObject> ResolveAll(List<SquadSpec> specs)
        {
            if (specs == null) return null;
            List<BasicCharacterObject> list = new List<BasicCharacterObject>(specs.Count);
            for (int i = 0; i < specs.Count; i++)
                list.Add(specs[i] == null ? null : Resolve(specs[i].Troop));
            return list;
        }

        private static CustomBattleCombatant _pendingAttacker;
        private static CustomBattleCombatant _pendingDefender;
        private static IMissionTroopSupplier[] _pendingSuppliers;
        private static string _pendingOrders = "charge";
        private static BattleSideEnum _pendingPlayerSide = BattleSideEnum.Attacker;

        // ── T5：每方多兵种/战术组（null 或空 ⇒ 该方走旧路径，GC2）───────────────────
        private static List<SquadSpec> _pendingAttackerGroups;
        private static List<SquadSpec> _pendingDefenderGroups;

        // 原样回显 groups DSL（供 StatusJson 排查）。空串 = 该方无 groups。
        internal static string AttackerGroupsDsl = "";
        internal static string DefenderGroupsDsl = "";

        // ── v0.8.41：战术档位 + 环境旋钮 ─────────────────────────────────
        // 取证与动机见 `src/TacticsCombatant.cs`（战术档位）与 `src/BattleEnv.cs`（环境）。
        // -1 = 不覆盖 ⇒ 该方照旧走引擎原生路径（GC2）。

        /// <summary>请求的战术档位（&lt; 0 = 不覆盖）。引擎用它分 20/50 两档决定加哪些 TacticOption。</summary>
        internal static int AttackerTacticsRequested = -1;

        /// <summary>请求的战术档位（&lt; 0 = 不覆盖）。</summary>
        internal static int DefenderTacticsRequested = -1;

        /// <summary>
        /// 引擎**原生**战术档位 = `combatant.GetTacticsSkillAmount()` = 该方所有参战兵种的
        /// `GetSkillValue(DefaultSkills.Tactics)` 最大值（反编译 CustomBattleCombatant 取证）。
        /// 开战时取一次即可（参战名单在开战后不再变）。攻城路径由官方入口内部自建 combatant ⇒ 保持 -1。
        /// </summary>
        internal static int AttackerTacticsNative = -1;

        /// <summary>引擎原生战术档位（守方）。</summary>
        internal static int DefenderTacticsNative = -1;

        /// <summary>
        /// 环境旋钮被**显式忽略**的原因（如"攻城路径不支持 terrain"）。
        /// 与 `formationWarnings` 同一模式：不阻断开战，但绝不静默 —— 静默是这类
        /// "参数写了、没接线"最贵的坑。空列表 ⇒ 无。
        /// </summary>
        private static List<string> _pendingEnvNotes = new List<string>();

        // ── v0.8.41：攻城专用旋钮 ────────────────────────────────────────
        // 攻城路径走官方 `BannerlordMissions.OpenSiegeMissionWithDeployment(..., sceneUpgradeLevel,
        // seasonString, isSallyOut, isReliefForceAttack, timeOfDay)` —— 这两个值一直是**写死**的
        // （3 与 6f，照 CPUBenchmark）。官方入口内部自建 record ⇒ terrain/seed 那些字段落不进去，
        // 但这两个是入口的显式参数，可以开出来。默认值 = 改动前的写死值 ⇒ 不传就是原样（GC2）。

        /// <summary>攻城场景升级等级（1..3；官方入口参数）。默认 3 = 改动前的写死值。</summary>
        internal static int SiegeSceneLevel = 3;

        /// <summary>攻城开战时刻（小时，0..24；官方入口参数）。默认 6f = 改动前的写死值。</summary>
        internal static float SiegeTimeOfDay = 6f;

        // v0.8.32：开战 DSL `formation` 死字段的一致性告警（已渲染好的 JSON 数组元素，空 = 无不一致）。
        // 见 `SquadSpec.CollectFormationMismatches` —— 把"写 Infantry 的弓手静默进 Ranged"变成显式信号。
        private static List<string> _pendingFormationWarnings = new List<string>();

        private static IEnumerable<MissionBehavior> CreateBehaviors(Mission mission)
        {
            List<MissionBehavior> list = new List<MissionBehavior>();
            // 谁当"玩家侧"可切换：MissionCombatantsLogic 的参数顺序是
            //   (battleCombatants, playerBattleCombatant, defenderLeader, attackerLeader, teamAIType, isPlayerSergeant)
            // 官方 benchmark 传的是 (null, playerParty, enemyParty, playerParty, ...)，即 playerParty 同时是攻方首领。
            // 注意：3/4 号参数必须分别是**两个不同阵营**的首领，只有 playerBattleCombatant 跟着开关变。
            IBattleCombatant attackerLeader = _pendingAttacker;
            IBattleCombatant defenderLeader = _pendingDefender;
            // v0.8.41：只在**请求了档位**时才包一层 TacticsCombatant（不传 ⇒ 逐字节走原路径，GC2）。
            // 包装的安全性取证（无下转型、引擎只读 4 处成员）见 `TacticsCombatant.cs`。
            // ⚠️ `CustomBattleMissionSpawnHandler` 仍拿**未包装**的 `_pendingDefender/_pendingAttacker`：
            //    它的参数类型是具体类 `CustomBattleCombatant`，本来也塞不进接口对象。
            if (attackerLeader != null && AttackerTacticsRequested >= 0)
            {
                attackerLeader = new TacticsCombatant(attackerLeader, AttackerTacticsRequested);
            }
            if (defenderLeader != null && DefenderTacticsRequested >= 0)
            {
                defenderLeader = new TacticsCombatant(defenderLeader, DefenderTacticsRequested);
            }
            IBattleCombatant playerParty = _pendingPlayerSide == BattleSideEnum.Attacker
                ? attackerLeader : defenderLeader;
            list.Add(new MissionCombatantsLogic(null, playerParty, defenderLeader, attackerLeader,
                Mission.MissionTeamAITypeEnum.FieldBattle, false));
            list.Add(new DefaultBattleMissionAgentSpawnLogic(_pendingSuppliers, _pendingPlayerSide,
                Mission.BattleSizeType.Battle));
            // ★ 必须有：spawn handler 在 AfterStart 里调 DefaultBattleMissionAgentSpawnLogic.InitWithSinglePhase(...)，
            //   那才是**唯一**创建 spawn phase 的地方（_phases[].Add → DefaultBattleMissionAgentSpawnLogic.cs:488）。
            //   少了它 _phases 是空列表 → DefenderActivePhase/AttackerActivePhase 为 null →
            //   IsInitialSpawnOver（`:88`，同一批属性里唯一漏写 ?. 的那个）NullReferenceException，
            //   而且是在引擎的 Mission.OnTick 里崩，表现为"任务刚创建就卡死/崩溃"。
            //   引擎自带这个 handler，就在我们已引用的 TaleWorlds.MountAndBlade.dll 里（注意 ctor 参数顺序是 守方, 攻方）。
            list.Add(new CustomBattleMissionSpawnHandler(_pendingDefender, _pendingAttacker));
            list.Add(new BattlePowerCalculationLogic());
            list.Add(new AgentHumanAILogic());
            // v0.8.5：多轮模式下**不能挂 AgentVictoryLogic** —— 它会在"一方全灭"时结束 mission，
            // 而多轮恰恰要在一方全灭之后继续（见 RoundOrchestratorBehavior 的说明）。
            if (RoundOrchestratorBehavior.Enabled)
            {
                list.Add(new RoundOrchestratorBehavior());
            }
            else
            {
                list.Add(new AgentVictoryLogic());
            }
            list.Add(new MissionHardBorderPlacer());
            list.Add(new MissionBoundaryPlacer());
            list.Add(new MissionBoundaryCrossingHandler(10f));
            list.Add(new ScenarioProbe());
            return list;
        }

        // ── 探针：判定入场、加速、结束 ────────────────────────────────────

        internal class ScenarioProbe : MissionBehavior
        {
            public override MissionBehaviorType BehaviorType
            {
                get { return MissionBehaviorType.Other; }
            }

            private float _sinceSpeedCheck;
            private bool _endCalled;

            // ── T13：组路径周期性重申 order + 编队 order 观测 ────────────────
            private float _sinceOrderRefresh;   // 累计秒（重申间隔）
            private float _sinceOrderSample;    // 累计秒（观测间隔）
            private const float OrderRefreshSeconds = 0.5f; // 组路径每 0.5s 重申各组的编队 movement order
            private const float OrderSampleSeconds = 5f;    // 每 5s 写一条 order 观测事件

            /// <summary>
            /// 对称化（v0.7.3）：把双方都改成 TacticCharge。
            /// 动机来自**实测**：20v20 同兵种镜像对局连打两局，攻方 13:0 / 6:0 全胜，
            /// 且攻方打出 407 次命中 vs 守方 285 次 —— 引擎默认战术是"攻方进攻、守方原地防守"，
            /// 冲锋方天然占优，这个偏差足以掩盖真实兵种差异。
            /// 官方 benchmark 也做同类处理：CPUBenchmarkMissionLogic.AfterStart 里对双方
            /// ClearTacticOptions() 后加 TacticStop（我们改用 TacticCharge，保证双方必然接战）。
            /// </summary>
            public override void AfterStart()
            {
                base.AfterStart();
                if (TryApplyGroupOrders()) return;
                if (_pendingOrders != "charge") return;
                try
                {
                    Mission m = this.Mission;
                    if (m == null) return;
                    ApplyCharge(m.AttackerTeam);
                    ApplyCharge(m.DefenderTeam);
                }
                catch (Exception ex)
                {
                    LastError = "orders: " + ex.GetType().Name + ": " + ex.Message;
                }
            }

            private static void ApplyCharge(Team team)
            {
                if (team == null) return;
                team.ClearTacticOptions();
                team.AddTacticOption(new TacticCharge(team));
                foreach (Formation f in team.FormationsIncludingEmpty)
                {
                    if (f == null || f.CountOfUnits == 0) continue;
                    f.SetMovementOrder(MovementOrder.MovementOrderCharge);
                }
            }

            /// <summary>
            /// T5 组路径：按"每组兵种的实际编队"下发 movement。
            /// 编队由 BasicCharacterObject.GetFormationClass() 决定（GC1 修订：formation 字段不可下发）。
            /// 冲突已在 Start() 拦掉（conflicting_movements）；这里只做安全下发，
            /// f==null 记 LastError 并跳过，绝不抛未捕获异常。
            /// （修复轮 1 · m3：删掉从未使用的 BattleSideEnum side 参数。）
            /// </summary>
            internal static void ApplyOrders(Team team, List<SquadSpec> specs)
            {
                if (team == null || specs == null) return;
                team.ClearTacticOptions();
                team.AddTacticOption(new TacticCharge(team));
                for (int i = 0; i < specs.Count; i++)
                {
                    SquadSpec s = specs[i];
                    BasicCharacterObject troop = Resolve(s.Troop);
                    if (troop == null)
                    {
                        AppendOrderError("第 " + (i + 1) + " 组兵种 id 不存在: " + s.Troop);
                        continue;
                    }
                    Formation f = team.GetFormation(troop.GetFormationClass());
                    if (f == null)
                    {
                        AppendOrderError("编队不可用 " + troop.GetFormationClass() + "（组 " + s.Troop + "）");
                        continue;
                    }
                    string mv = s.Movement == null ? "charge" : s.Movement;
                    f.SetMovementOrder(MapMovement(mv));
                    // T13：让该编队脱离 AI 战术控制。否则 team 级 TacticCharge 会周期性把 movement
                    // 覆盖成 ChargeToTarget（游戏内实测：守方 stop 组被覆盖 59/115 次 ⇒ 位移 154 m，
                    // 双方 order 每 1-2 秒拉锯）。官方同款做法见 CPUBenchmarkMissionLogic（AI 对战基准）：
                    // `SetControlledByAI(false, false)`。它只关掉**编队级 order 的 AI 驱动**，
                    // 不会冻结士兵个人行为（那要另调 SetIsAIPaused）。
                    f.SetControlledByAI(false, false);
                }
            }

            /// <summary>
            /// 按**追加**方式记录命令下发问题（不覆盖先前信息；与 LastError 同风格）。仅用于真错误。
            /// 幂等（T6 修复轮 1 · nit6）：同一条整场只追加一次 —— 多轮重申会重复命中同一问题，
            /// 否则 LastError 会累积重复串。追加语义不变，只是重复项被丢弃。
            /// </summary>
            private static void AppendOrderError(string detail)
            {
                string entry = "orders: " + detail;
                if (LastError.Contains(entry)) return;
                LastError = string.IsNullOrEmpty(LastError) ? entry : LastError + " | " + entry;
            }

            /// <summary>
            /// DSL movement → 规范化的「落点名」（纯字符串）。缺省 charge。
            ///
            /// ⚠️ 为什么不能用 `MovementOrder`：`MovementOrder` 是 struct，其静态字段
            /// （`MovementOrderCharge` / `MovementOrderAdvance` / …）在**类型初始化**时构造实例；该初始化在
            /// **mission 之外**（`Start()` 阶段，游戏停在「自定义战斗界面」）会抛 `TypeInitializationException`，
            /// 且 .NET 会把该类型**永久标记为不可用** ⇒ 同一游戏进程内后续任何 `MovementOrder` 访问
            /// （连旧路径 `ApplyCharge` 的 `MovementOrder.MovementOrderCharge`）也一起坏掉。
            /// `ValidateGroupedSide` 在 `Start()` 阶段（mission 之外）判落点，**只能**用本方法。
            ///
            /// 与落点一一对应（charge→Charge、advance→Advance、fallback→FallBack、stop→Stop、retreat→Retreat，
            /// T11 已移除 hold）⇒ 按此规范化字符串比较 ≡ 按落点比较，语义零变化。
            /// </summary>
            internal static string MovementKey(string movement)
            {
                switch (movement)
                {
                    case "advance": return "Advance";
                    case "fallback": return "FallBack";
                    case "stop": return "Stop";
                    case "retreat": return "Retreat";
                    case "charge":
                    default: return "Charge";
                }
            }

            /// <summary>
            /// DSL movement → 引擎 MovementOrder。缺省 charge。
            /// ⚠️ 只允许在 **mission 内**调用（`ApplyOrders` / `AfterStart` 路径）；mission 之外需要判落点时用
            /// `MovementKey`（见其 doc 与 §1：mission 外碰 `MovementOrder` 会抛并永久污染整个进程内的该类型）。
            /// </summary>
            internal static MovementOrder MapMovement(string movement)
            {
                switch (movement)
                {
                    case "advance": return MovementOrder.MovementOrderAdvance;
                    case "fallback": return MovementOrder.MovementOrderFallBack;
                    case "stop": return MovementOrder.MovementOrderStop;
                    case "retreat": return MovementOrder.MovementOrderRetreat;
                    case "charge":
                    default: return MovementOrder.MovementOrderCharge;
                }
            }

            /// <summary>
            /// T5：组路径下按组下发命令；无 groups 时返回 false，让旧 AfterStart 逻辑原样继续（GC2）。
            /// 一方有 groups、另一方没有也能工作（无 groups 的一侧仍走 ApplyCharge）。
            /// </summary>
            private bool TryApplyGroupOrders()
            {
                bool aGrouped = _pendingAttackerGroups != null && _pendingAttackerGroups.Count > 0;
                bool dGrouped = _pendingDefenderGroups != null && _pendingDefenderGroups.Count > 0;
                if (!aGrouped && !dGrouped) return false;
                try
                {
                    Mission m = this.Mission;
                    if (m == null) return true;
                    if (aGrouped) ApplyOrders(m.AttackerTeam, _pendingAttackerGroups);
                    else ApplyCharge(m.AttackerTeam);
                    if (dGrouped) ApplyOrders(m.DefenderTeam, _pendingDefenderGroups);
                    else ApplyCharge(m.DefenderTeam);
                }
                catch (Exception ex)
                {
                    // 修复轮 2 · nit-1：改用追加，避免吞掉 ApplyOrders 已写入的提示/错误
                    AppendOrderError(ex.GetType().Name + ": " + ex.Message);
                }
                return true;
            }

            // ── T13：组路径周期性重申各组的编队 movement order ──────────────────
            /// <summary>
            /// 组路径专用：每 `OrderRefreshSeconds` 重申一次各组的编队 movement order，
            /// 以压过 team 级 `TacticCharge` 周期性把编队 order 重设回 `Charge` 的行为
            /// （缺陷：按组下发的 movement 对「守方」不生效 —— 见 task-13-brief §0/§1）。
            /// **双方都无 groups ⇒ 立即返回**（旧路径绝不受影响，GC2）。
            /// **不**在这里 `ClearTacticOptions()`/`AddTacticOption()`：那只在 `AfterStart` 的
            /// `ApplyOrders`/`ApplyCharge` 里做一次，避免每隔 0.5s 重置 tactic 造成 AI 抖动。
            /// 整体 try/catch —— 遥测/命令通道绝不抛。
            /// </summary>
            private static void ReapplyGroupOrders(Mission m)
            {
                try
                {
                    bool aGrouped = _pendingAttackerGroups != null && _pendingAttackerGroups.Count > 0;
                    bool dGrouped = _pendingDefenderGroups != null && _pendingDefenderGroups.Count > 0;
                    if (!aGrouped && !dGrouped) return; // 旧路径：什么都不做（GC2）
                    if (m == null) return;
                    if (aGrouped) ReapplySideOrders(m.AttackerTeam, _pendingAttackerGroups);
                    if (dGrouped) ReapplySideOrders(m.DefenderTeam, _pendingDefenderGroups);
                    // 无 groups 的一方**什么都不做**：它仍由既有 ApplyCharge/TacticCharge 管（GC2）。
                }
                catch
                {
                }
            }

            /// <summary>
            /// 把某一方 pending 组里**落在指定编队**上的 spec 的 `Movement` 改掉，返回改了几个。
            ///
            /// 为什么必须有它（v0.8.23 真机实测，`order` 通道的坑）：
            /// 组路径每 `OrderRefreshSeconds`(0.5s) 重申一次各组的 movement（`ReapplyGroupOrders`），
            /// 所以"只用 `order` 通道手改一次 `Formation.SetMovementOrder`"会在**半秒内被我们自己**
            /// 重申回原值 —— 真机表现：t1 设 stop 回读 `Charge→Stop` ✅，t2（12 秒后）复读
            /// `orderBefore` 又是 `Charge`。⇒ 必须把"待重申的那个值"一起改掉（改 `spec.Movement`），
            /// 否则本通道在 BlBridge 自己开的场次里等于失效。
            ///
            /// 返回 0 的两种含义（调用方要如实报告，别当成成功）：
            ///   * 该方没走组路径（旧单值路径）⇒ 另行由 `ApplyCharge`/`TacticCharge` 管；
            ///   * 目标编队上没有组（刷新不会碰它，但也可能被 team 战术改）。
            /// 绝不抛。
            /// </summary>
            internal static int OverridePendingMovement(Team team, int formationIndex, string movement)
            {
                try
                {
                    Mission m = Mission.Current;
                    if (m == null || team == null) return 0;
                    List<SquadSpec> specs;
                    if (ReferenceEquals(team, m.AttackerTeam)) specs = _pendingAttackerGroups;
                    else if (ReferenceEquals(team, m.DefenderTeam)) specs = _pendingDefenderGroups;
                    else specs = null;
                    if (specs == null || specs.Count == 0) return 0;

                    int changed = 0;
                    for (int i = 0; i < specs.Count; i++)
                    {
                        SquadSpec s = specs[i];
                        if (s == null) continue;
                        BasicCharacterObject troop = Resolve(s.Troop);
                        if (troop == null) continue;
                        if ((int)troop.GetFormationClass() != formationIndex) continue;
                        s.Movement = movement;
                        // v0.8.30：改回"按名字重申" ⇒ 手动令标记必须一并清掉（三态归零），
                        // 否则 `ReapplySideOrders` 会继续重申那个旧的点/旧的目标，movement 通道等于失效。
                        s.ManualKind = SquadSpec.ManualNone;
                        s.TargetFormationIndex = -1;
                        s.TargetAgentIndex = -1;
                        changed++;
                    }
                    return changed;
                }
                catch
                {
                    return 0;
                }
            }

            /// <summary>
            /// v0.8.30：`OverridePendingMovement` 的**指定点**版本 —— 把某一方 pending 组里落在
            /// 指定编队上的 spec 标记为"手动指定点移动"，并记下目标点；返回改了几个。
            ///
            /// 为什么必须有它（与 v0.8.23 那条 movement 同步是同一个坑，只是分量多了）：
            /// 组路径每 0.5 s 重申一次的只有 `s.Movement`（名字），指定点移动没有名字可重申
            /// ⇒ 不改"待重申的值"的话，半秒内就会被我们自己重申回 charge，
            /// 而 `Formation` 上当下看起来是 `Move` ⇒ 只看"我调了 API"会把它算成功。
            ///
            /// 返回 0 的含义与 `OverridePendingMovement` 相同（该方没走组路径 / 该编队上没有组）。
            /// 绝不抛。
            /// </summary>
            internal static int OverridePendingMoveToPosition(Team team, int formationIndex,
                                                            float x, float y, float z)
            {
                try
                {
                    Mission m = Mission.Current;
                    if (m == null || team == null) return 0;
                    List<SquadSpec> specs;
                    if (ReferenceEquals(team, m.AttackerTeam)) specs = _pendingAttackerGroups;
                    else if (ReferenceEquals(team, m.DefenderTeam)) specs = _pendingDefenderGroups;
                    else specs = null;
                    if (specs == null || specs.Count == 0) return 0;

                    int changed = 0;
                    for (int i = 0; i < specs.Count; i++)
                    {
                        SquadSpec s = specs[i];
                        if (s == null) continue;
                        BasicCharacterObject troop = Resolve(s.Troop);
                        if (troop == null) continue;
                        if ((int)troop.GetFormationClass() != formationIndex) continue;
                        s.ManualKind = SquadSpec.ManualPosition;
                        s.TargetFormationIndex = -1;
                        s.TargetAgentIndex = -1;
                        s.MoveX = x; s.MoveY = y; s.MoveZ = z;
                        changed++;
                    }
                    return changed;
                }
                catch
                {
                    return 0;
                }
            }

            /// <summary>
            /// v0.8.31：`OverridePendingMovement` 的**指定目标编队**版本 —— 把某一方 pending 组里落在
            /// 指定编队上的 spec 标记为"手动冲锋到敌方某编队"，并记下那个**敌方编队下标**；返回改了几个。
            ///
            /// 与指定点那条同理：`ChargeToTarget` 也**不是**按名字重申的东西（它绑的是 `Formation` 对象），
            /// 不mark 的话半秒内会被组路径重申回 `s.Movement`（而 `Formation` 上当场看是 `ChargeToTarget`）。
            /// 返回 0 的含义同其它两个 Override。绝不抛。
            /// </summary>
            internal static int OverridePendingChargeTarget(Team team, int formationIndex,
                                                           int targetFormationIndex)
            {
                try
                {
                    Mission m = Mission.Current;
                    if (m == null || team == null) return 0;
                    List<SquadSpec> specs;
                    if (ReferenceEquals(team, m.AttackerTeam)) specs = _pendingAttackerGroups;
                    else if (ReferenceEquals(team, m.DefenderTeam)) specs = _pendingDefenderGroups;
                    else specs = null;
                    if (specs == null || specs.Count == 0) return 0;

                    int changed = 0;
                    for (int i = 0; i < specs.Count; i++)
                    {
                        SquadSpec s = specs[i];
                        if (s == null) continue;
                        BasicCharacterObject troop = Resolve(s.Troop);
                        if (troop == null) continue;
                        if ((int)troop.GetFormationClass() != formationIndex) continue;
                        s.ManualKind = SquadSpec.ManualChargeTarget;
                        s.TargetFormationIndex = targetFormationIndex;
                        s.TargetAgentIndex = -1;
                        changed++;
                    }
                    return changed;
                }
                catch
                {
                    return 0;
                }
            }

            /// <summary>
            /// v0.8.32：`OverridePendingMovement` 的**指定目标单位**版本 —— 把某一方 pending 组里落在
            /// 指定编队上的 spec 标记为"手动攻击某个敌方 agent"，并记下那个**agent 下标**；返回改了几个。
            ///
            /// 同理：`AttackEntity` 也不是按名字重申的东西（它绑的是那个 `GameEntity`），
            /// 不打标记的话半秒内会被组路径重申回 `s.Movement`（而 `Formation` 上当场看是 `AttackEntity`）。
            /// 存**下标**而不是对象：重申在下一帧，届时对象可能已失效；解不到就跳过重申（见 `ReapplySideOrders`）。
            /// 返回 0 的含义同其它几个 Override。绝不抛。
            /// </summary>
            internal static int OverridePendingAttackAgent(Team team, int formationIndex,
                                                          int targetAgentIndex)
            {
                try
                {
                    Mission m = Mission.Current;
                    if (m == null || team == null) return 0;
                    List<SquadSpec> specs;
                    if (ReferenceEquals(team, m.AttackerTeam)) specs = _pendingAttackerGroups;
                    else if (ReferenceEquals(team, m.DefenderTeam)) specs = _pendingDefenderGroups;
                    else specs = null;
                    if (specs == null || specs.Count == 0) return 0;

                    int changed = 0;
                    for (int i = 0; i < specs.Count; i++)
                    {
                        SquadSpec s = specs[i];
                        if (s == null) continue;
                        BasicCharacterObject troop = Resolve(s.Troop);
                        if (troop == null) continue;
                        if ((int)troop.GetFormationClass() != formationIndex) continue;
                        s.ManualKind = SquadSpec.ManualAttackAgent;
                        s.TargetAgentIndex = targetAgentIndex;
                        s.TargetFormationIndex = -1;
                        changed++;
                    }
                    return changed;
                }
                catch
                {
                    return 0;
                }
            }

            /// <summary>
            /// `team` 的**敌方** Team（mission 只有攻/守两方）。拿不到（单方场次/不是攻守任一方）⇒ null。
            /// 绝不抛。
            /// </summary>
            private static Team EnemyTeamOf(Team team)
            {
                try
                {
                    Mission m = Mission.Current;
                    if (m == null || team == null) return null;
                    Team enemy;
                    if (ReferenceEquals(team, m.AttackerTeam)) enemy = m.DefenderTeam;
                    else if (ReferenceEquals(team, m.DefenderTeam)) enemy = m.AttackerTeam;
                    else return null;
                    if (enemy == null || ReferenceEquals(enemy, team)) return null;
                    return enemy;
                }
                catch
                {
                    return null;
                }
            }

            /// <summary>
            /// 敌方某个编队（按 `FormationClass` 下标）；编队不存在 / 已经**空**了 ⇒ null。
            /// 空编队不算"可用目标"（`ChargeToTarget` 指向空编队没有意义，而且那多半是它已经被打光了）。
            /// 绝不抛。
            /// </summary>
            private static Formation EnemyFormation(Team team, int formationIndex)
            {
                try
                {
                    if (formationIndex < 0) return null;
                    Team enemy = EnemyTeamOf(team);
                    if (enemy == null) return null;
                    Formation f = enemy.GetFormation((FormationClass)formationIndex);
                    if (f == null) return null;
                    if (f.CountOfUnits == 0) return null;
                    return f;
                }
                catch
                {
                    return null;
                }
            }

            /// <summary>
            /// v0.8.32：按 `Agent.Index` 在**敌方**（相对 `team` 而言）里找那个 agent，**且要求存活**。
            /// 找不到 / 不是敌方 / 已阵亡 ⇒ null（调用方据此跳过重申或拒绝改令，绝不换人）。
            /// 与 `EnemyFormation` 同一套"目标可用性"口径：不可用就是不可用，不静默替换。
            /// 绝不抛。
            /// </summary>
            private static Agent EnemyAgentByIndex(Team team, int agentIndex)
            {
                try
                {
                    if (agentIndex < 0) return null;
                    Team enemy = EnemyTeamOf(team);
                    if (enemy == null) return null;
                    Mission m = Mission.Current;
                    if (m == null) return null;
                    foreach (Agent a in m.Agents)
                    {
                        if (a == null) continue;
                        if (a.Index != agentIndex) continue;
                        // 找到了同号的下标但不是敌方 ⇒ 当场判不可用（**不**继续找别人）
                        if (!ReferenceEquals(a.Team, enemy)) return null;
                        if (!a.IsActive()) return null;
                        return a;
                    }
                    return null;
                }
                catch
                {
                    return null;
                }
            }

            /// <summary>
            /// v0.8.32：agent → `GameEntity`（走 `Agent.AgentVisuals.GetEntity()`，
            /// `MBAgentVisuals.cs:46`；`MovementOrderAttackEntity` 要的是 `GameEntity`）。
            /// 没渲染体（AgentVisuals == null）⇒ null。⚠️ 这里**不能** `using TaleWorlds.Engine;`
            /// （它带进来的 `Path` 与 `System.IO.Path` 撞名，本文件两个都在用）⇒ 写全名。绝不抛。
            /// </summary>
            private static TaleWorlds.Engine.GameEntity EntityOf(Agent agent)
            {
                try
                {
                    if (agent == null) return null;
                    MBAgentVisuals av = agent.AgentVisuals;
                    if (av == null) return null;
                    return av.GetEntity();
                }
                catch
                {
                    return null;
                }
            }

            /// <summary>
            /// 重申某一方（有 groups 的方）每个 spec 的编队 movement order。
            /// 解析/取编队失败 ⇒ `AppendOrderError`（幂等），绝不抛。
            /// </summary>
            private static void ReapplySideOrders(Team team, List<SquadSpec> specs)
            {
                if (team == null || specs == null) return;
                for (int i = 0; i < specs.Count; i++)
                {
                    SquadSpec s = specs[i];
                    if (s == null) continue;
                    BasicCharacterObject troop = Resolve(s.Troop);
                    if (troop == null)
                    {
                        AppendOrderError("第 " + (i + 1) + " 组兵种 id 不存在: " + s.Troop);
                        continue;
                    }
                    Formation f = team.GetFormation(troop.GetFormationClass());
                    if (f == null)
                    {
                        AppendOrderError("编队不可用 " + troop.GetFormationClass() + "（组 " + s.Troop + "）");
                        continue;
                    }
                    if (s.ManualKind == SquadSpec.ManualPosition)
                    {
                        // v0.8.30：这个编队被 `order --position` 手动指定过目标点 ⇒ 重申时
                        // 照原样重申**那个点**，绝不退回 `s.Movement`（那会把它改回 charge）。
                        // ⚠️ 这里**不能** `using TaleWorlds.Engine;`：它带进来的 `Path` 会与
                        // `System.IO.Path` 撞名（本文件两个都在用，实测 CS0104）⇒ 用全名。
                        Mission cur = Mission.Current;
                        TaleWorlds.Engine.Scene scene = cur == null ? null : cur.Scene;
                        if (scene != null)
                        {
                            f.SetMovementOrder(MovementOrder.MovementOrderMove(
                                new TaleWorlds.Engine.WorldPosition(
                                    scene, new Vec3(s.MoveX, s.MoveY, s.MoveZ))));
                        }
                    }
                    else if (s.ManualKind == SquadSpec.ManualChargeTarget)
                    {
                        // v0.8.31：`order --target` 手动指定过"冲锋到某个敌方编队" ⇒ 重申同一件事。
                        // 目标编队可能已经**空了/没了**（被打光、合并）⇒ 那种情况下**跳过重申**
                        // 并记一条错误（AppendOrderError 幂等），**不**退回 `s.Movement`
                        // —— 退回等于偷偷把一个"冲这个编队"的令换成"冲锋"，是静默改令。
                        Formation enemy = EnemyFormation(team, s.TargetFormationIndex);
                        if (enemy == null)
                        {
                            AppendOrderError("指定目标的编队已不可用（组 " + s.Troop
                                + " / 目标下标 " + s.TargetFormationIndex + "）⇒ 跳过重申，不改令");
                        }
                        else
                        {
                            f.SetMovementOrder(MovementOrder.MovementOrderChargeToTarget(enemy));
                        }
                    }
                    else if (s.ManualKind == SquadSpec.ManualAttackAgent)
                    {
                        // v0.8.32：`order --target-agent` 手动指定过"攻击某个敌方单位" ⇒ 重申同一件事。
                        // 目标**会死**（这正是当初"只做编队目标"的理由）⇒ 解不到 / 已阵亡时**跳过重申**
                        // 并记一条 order error（AppendOrderError 幂等），**不**退回 `s.Movement`
                        // —— 退回等于偷偷把"打这个兵"换成"冲锋"，是静默改令。
                        Agent enemyAgent = EnemyAgentByIndex(team, s.TargetAgentIndex);
                        if (enemyAgent == null)
                        {
                            AppendOrderError("指定的目标单位已不可用（组 " + s.Troop
                                + " / 目标 agent 下标 " + s.TargetAgentIndex + "）⇒ 跳过重申，不改令");
                        }
                        else
                        {
                            TaleWorlds.Engine.GameEntity ent = EntityOf(enemyAgent);
                            if (ent == null)
                            {
                                AppendOrderError("指定的目标单位拿不到 GameEntity（组 " + s.Troop
                                    + " / 目标 agent 下标 " + s.TargetAgentIndex + "）⇒ 跳过重申，不改令");
                            }
                            else
                            {
                                f.SetMovementOrder(MovementOrder.MovementOrderAttackEntity(ent, true));
                            }
                        }
                    }
                    else
                    {
                        string mv = s.Movement == null ? "charge" : s.Movement;
                        f.SetMovementOrder(MapMovement(mv));
                    }
                    // 重申时也钉一次（短路成本≈0），防引擎或其它逻辑中途把控制权还回去。
                    f.SetControlledByAI(false, false);
                }
            }

            // ── T13：编队 order 观测事件（每 5s，每方每编队一行）─────────────────
            /// <summary>
            /// 每 5s 写一条 `order` 观测事件（用于确诊「tactic 是否覆盖 order」+ 以后当回归网）。
            /// **只在任一方有 groups 时写**（旧路径不产生新事件 ⇒ GC2）。字段形状见 brief §2.2，写死。
            /// 整体 try/catch，绝不抛。
            /// </summary>
            private static void WriteOrderEvents(Mission m)
            {
                try
                {
                    bool aGrouped = _pendingAttackerGroups != null && _pendingAttackerGroups.Count > 0;
                    bool dGrouped = _pendingDefenderGroups != null && _pendingDefenderGroups.Count > 0;
                    if (!aGrouped && !dGrouped) return; // 旧路径不产生新事件（GC2）
                    if (!Jw.IsOpen) return;
                    if (m == null) return;
                    float time = (float)DurationNow();
                    WriteOrderSide("Attacker", m.AttackerTeam, time);
                    WriteOrderSide("Defender", m.DefenderTeam, time);
                }
                catch
                {
                }
            }

            // ── v0.8.43：战术档位观测事件（`t`=`tactics`）──────────────────────────────
            /// <summary>
            /// 每 5s 写两行 `tactics`（每方一行），**只在请求过战术档位时**写。
            ///
            /// 为什么需要它（这是 T3② 之前测不出来的第二重原因，2026-10-05 真机验证定位）：
            ///   要做"档位 60 vs 不覆盖"的 A/B，必须用 `orders=default`（默认的 `orders=charge`
            ///   会 `ClearTacticOptions()` 只留 `TacticCharge`，档位必然看不出差别）。而
            ///   `orders=default` 且**不带 groups** 时，`order` 事件一条都不写
            ///   （`WriteOrderEvents` 开头就 return）⇒ 整场没有任何战术观测出口。
            ///   `MissionCombatantsLogic.EarlyStart` 依 `GetTacticsSkillAmount()` 分 20/50 两档
            ///   挂 `TacticOption`，而档位正是通过 `TacticsCombatant` 覆盖那个值的。
            ///
            /// **GC2 论证**：触发条件是 `AttackerTacticsRequested >= 0 || DefenderTacticsRequested >= 0`，
            ///   而这两个字段是 v0.8.41 才有的新参数、默认 -1 ⇒ 任何 v0.8.41 之前的 plan /
            ///   不传档位的请求都**不会**产生本事件，旧路径逐字节不变。
            /// 整体 try/catch，绝不抛。
            /// </summary>
            private static void WriteTacticsEvents(Mission m)
            {
                try
                {
                    if (AttackerTacticsRequested < 0 && DefenderTacticsRequested < 0) return; // GC2
                    if (!Jw.IsOpen) return;
                    if (m == null) return;
                    float time = (float)DurationNow();
                    WriteTacticsSide("Attacker", m.AttackerTeam, time, AttackerTacticsRequested);
                    WriteTacticsSide("Defender", m.DefenderTeam, time, DefenderTacticsRequested);
                }
                catch
                {
                }
            }

            /// <summary>写一方的一行 `tactics` 事件（请求档位 + 引擎真实战术）。绝不抛。</summary>
            private static void WriteTacticsSide(string side, Team team, float time, int requested)
            {
                if (team == null) return;
                try
                {
                    string names = ReadTacticNames(team);
                    string state;
                    if (names == null) state = "(unknown)";        // 反射读不到
                    else if (names.Length == 0) state = "(no-teamai)"; // 读到了：该队没挂 TeamAI
                    else state = names;                            // `可用集/当前生效`

                    // v0.8.43：把"档位是否真的生效"一并落盘（只读、不干预）。
                    // 只在**可用集**段上判 —— `/` 之后是"当前生效的那个"，两者同源。
                    string availOnly = names == null ? "" : names;
                    int slash = availOnly.IndexOf('/');
                    if (slash >= 0) availOnly = availOnly.Substring(0, slash);
                    string overriddenBy = DetectOverriddenBy(requested, side, availOnly);

                    StringBuilder sb = new StringBuilder();
                    sb.Append("{\"t\":\"tactics\"");
                    sb.Append(",\"time\":").Append(Jw.N(time));
                    sb.Append(",\"side\":\"").Append(side).Append('"');
                    sb.Append(",\"requested\":").Append(Jw.N(requested));
                    sb.Append(",\"actual\":\"").Append(Jw.Esc(state)).Append('"');
                    // 空串 = 未检测到覆盖者。**字段恒在**（不省略），便于下游稳定取用。
                    sb.Append(",\"overriddenBy\":\"").Append(Jw.Esc(overriddenBy)).Append('"');
                    sb.Append('}');
                    Jw.Write(sb.ToString());
                }
                catch
                {
                }
            }

            /// <summary>写该方每个编队一行 `order` 事件（`count==0` 的也写，便于看「编队是否存在」）。绝不抛。</summary>
            private static void WriteOrderSide(string side, Team team, float time)
            {
                if (team == null) return;
                // v0.8.43：`tactic` 改为**真实读取**。
                // 缺陷背景（2026-10-05 真机验证发现，**既有缺陷、非本轮引入**）：本方法原先写的是
                //   `string tactic = "none";` —— 该变量此后**从未被重新赋值**，落盘时原样写出，
                //   于是**所有** `order` 事件的 `tactic` 恒为 `"none"`，是个占位字段。
                //   ⇒ 交接文档里"看 order 的 tactic 名判断档位是否分岔"那条判据**永远测不出东西**。
                // 读法用反射：`TeamAIComponent` 的 `_availableTactics`（可用集）与 `_currentTactic`
                //   （当前生效的那个）都是 private。本仓库既有反射先例见 CameraFollow / CameraSpeed /
                //   ControlAgent / UiInspector。
                // ⚠️ 三态必须可分辨（本项目反复吃过亏，同 UiInspector 的"属性不存在 vs 值为 null"）：
                //     null       = 反射读不到（字段名漂了/异常）→ 写 "(unknown)"
                //     ""         = 读到了，但该队没有 TeamAI / 一个战术都没有 → 写 "(none)"
                //     非空       = 真实战术名（多个用 + 连）
                string tactic = ReadTacticNames(team);
                if (tactic == null) tactic = "(unknown)";
                else if (tactic.Length == 0) tactic = "(none)";
                foreach (Formation f in team.FormationsIncludingEmpty)
                {
                    string formation; // JSON 片段：带引号的名字，或裸 `null`
                    int count;
                    string order;
                    if (f == null)
                    {
                        formation = "null";
                        count = 0;
                        order = "(no-formation)";
                    }
                    else
                    {
                        string name = "(unknown)";
                        try { name = EnumNames.Formation(f.LogicalClass); }
                        catch { name = "(unknown)"; }
                        if (name == null) name = "(unknown)";
                        formation = "\"" + Jw.Esc(name) + "\"";
                        try { count = f.CountOfUnits; }
                        catch { count = 0; }
                        try
                        {
                            MovementOrder mo = f.GetReadonlyMovementOrderReference();
                            order = mo.OrderEnum.ToString();
                            if (string.IsNullOrEmpty(order)) order = "(unknown)";
                        }
                        catch
                        {
                            order = "(unknown)";
                        }
                    }
                    StringBuilder sb = new StringBuilder();
                    sb.Append("{\"t\":\"order\"");
                    sb.Append(",\"time\":").Append(Jw.N(time));
                    sb.Append(",\"side\":\"").Append(side).Append('"');
                    sb.Append(",\"formation\":").Append(formation);
                    sb.Append(",\"count\":").Append(Jw.N(count));
                    sb.Append(",\"order\":\"").Append(Jw.Esc(order)).Append('"');
                    sb.Append(",\"tactic\":\"").Append(Jw.Esc(tactic)).Append('"');
                    sb.Append('}');
                    Jw.Write(sb.ToString());
                }
            }

            // ── v0.8.43：真实战术名读取（反射，解决 order.tactic 恒为 "none" 的既有缺陷）────
            /// <summary>`TeamAIComponent` 的两个 private 字段，只解析一次。</summary>
            private static FieldInfo _availableTacticsField;
            private static FieldInfo _currentTacticField;
            private static bool _tacticFieldsResolved;

            /// <summary>
            /// 读该队**真实**的战术名，返回 `可用集/当前生效` 两段（形如 `TacticCharge/TacticFullScaleAttack`）。
            /// 返回值语义（**三态必须可分辨**，见调用点的说明）：
            ///   `null` = 反射读不到（字段名漂了 / 抛异常）；`""` = 读到了但没有任何战术；非空 = 真实名。
            /// 整体 try/catch，绝不抛。
            /// </summary>
            private static string ReadTacticNames(Team team)
            {
                try
                {
                    TeamAIComponent ai = team.TeamAI;
                    if (ai == null) return "";   // 读到了：该队没挂 TeamAI

                    if (!_tacticFieldsResolved)
                    {
                        const BindingFlags F = BindingFlags.Instance | BindingFlags.NonPublic;
                        Type t = typeof(TeamAIComponent);
                        _availableTacticsField = t.GetField("_availableTactics", F);
                        _currentTacticField = t.GetField("_currentTactic", F);
                        _tacticFieldsResolved = true;
                    }
                    if (_availableTacticsField == null || _currentTacticField == null) return null; // 字段名漂了

                    string available = JoinTacticTypes(_availableTacticsField.GetValue(ai) as System.Collections.IEnumerable);
                    TacticComponent current = _currentTacticField.GetValue(ai) as TacticComponent;
                    string curName = current == null ? "" : current.GetType().Name;
                    return available + "/" + curName;
                }
                catch
                {
                    return null;
                }
            }

            /// <summary>把战术集合拼成 `A+B`（空集合 ⇒ `""`）。绝不抛。</summary>
            private static string JoinTacticTypes(System.Collections.IEnumerable items)
            {
                if (items == null) return "";
                try
                {
                    StringBuilder sb = new StringBuilder();
                    foreach (object o in items)
                    {
                        TacticComponent tc = o as TacticComponent;
                        if (tc == null) continue;
                        if (sb.Length > 0) sb.Append('+');
                        sb.Append(tc.GetType().Name);
                    }
                    return sb.ToString();
                }
                catch
                {
                    return "";
                }
            }

            // ── v0.8.43：战术覆盖者检测（谁改写了档位挂上的战术集）──────────────────
            /// <summary>
            /// 判断"请求的档位是否真的产生了它应有的战术集"，并在没有时给出**归因**。
            /// **只读、不 patch、不干预。**
            ///
            /// 为什么要它（2026-10-05 真机验证，见工作区报告 §8 / §14）：
            ///   纯原版下 `attackerTacticLevel`/`defenderTacticLevel` **确实生效**（换边自对照证实：
            ///   攻方 60→0 时战术集从 5 个塌成 1 个）。但**装了 RBM 时档位是空操作** ——
            ///   RBM 给 `MissionCombatantsLogic.EarlyStart` 挂了**纯 Postfix**，它**无条件
            ///   `ClearTacticOptions()`** 再按 `BasicCulture` + `Side` 重建，且全上游 grep
            ///   `GetTacticsSkillAmount` **零命中** ⇒ 档位没有任何路径会被读到。
            ///   ⇒ 后果：调用方拿到 `accepted:true` 却**看不出"档位没接上"**，只能靠统计猜
            ///   （§三十七 之前正是这么误判成"样本不足"的）。本方法让它**一眼可读**。
            ///
            /// 判据（**不依赖文化知识，且比"类名含 RBMTactic"强**）：
            ///   按引擎 `MissionCombatantsLogic.EarlyStart` 的 FieldBattle 三档逻辑算出**应有集**，
            ///   与**观测到的可用集**比对；不等即为"被覆盖"。理由：
            ///   `&lt; 20` 只给 `TacticCharge`；`&gt;= 20` 追加 `FullScaleAttack`（攻方另加
            ///   `RangedHarrassmentOffensive`，守方另加 `DefensiveEngagement`/`DefensiveLine`）；
            ///   `&gt;= 50` 追加 `FrontalCavalryCharge`（攻方另加 `CoordinatedRetreat`，守方另加
            ///   `DefensiveRing`/`HoldChokePoint`）。
            ///   ⚠️ **为什么不用"含 `RBMTactic*`"**：那会**漏报** —— 实测守方 req=0 时 RBM 已覆盖
            ///   （应只有 `Charge`，实际 4 个），但它那支没有 `RBMTactic*` 类名（RBM 的守卫分支按文化
            ///   条件追加，未命中）⇒ 只看类名会报"无覆盖"。见工作区报告 §18。
            ///
            /// 归因：判为覆盖后，RBM 模块激活则报 `"RBM"`，否则报 `"(unknown)"` ——
            ///   **模块身份与战术集异常是两条独立证据**，能对上才写成因，不替调用方编因果。
            /// 整体 try/catch，绝不抛。
            /// </summary>
            /// <param name="requested">该方请求的档位（`&lt; 0` = 未覆盖 ⇒ 无法判定，返回空串）。</param>
            /// <param name="side">`"Attacker"` / `"Defender"`（决定 20/50 档的侧向成员）。</param>
            /// <param name="availableTacticNames">`A+B+C` 形式的**可用集**（`/` 之前那段）。</param>
            private static string DetectOverriddenBy(int requested, string side, string availableTacticNames)
            {
                try
                {
                    if (requested < 0) return "";                       // 没覆盖档位 ⇒ 无从判定
                    if (string.IsNullOrEmpty(availableTacticNames)) return "";

                    bool isAttacker = side == "Attacker";

                    // 引擎应有集（FieldBattle 分支，反编译逐条对齐）
                    List<string> expect = new List<string>();
                    expect.Add("TacticCharge");
                    if (requested >= 20)
                    {
                        expect.Add("TacticFullScaleAttack");
                        if (isAttacker) expect.Add("TacticRangedHarrassmentOffensive");
                        else { expect.Add("TacticDefensiveEngagement"); expect.Add("TacticDefensiveLine"); }
                        if (requested >= 50)
                        {
                            expect.Add("TacticFrontalCavalryCharge");
                            if (isAttacker) expect.Add("TacticCoordinatedRetreat");
                            else { expect.Add("TacticDefensiveRing"); expect.Add("TacticHoldChokePoint"); }
                        }
                    }

                    // 观测集（`A+B` → 排序去重）
                    List<string> got = new List<string>();
                    string[] parts = availableTacticNames.Split('+');
                    for (int i = 0; i < parts.Length; i++)
                    {
                        string s = parts[i].Trim();
                        if (s.Length > 0 && !got.Contains(s)) got.Add(s);
                    }

                    // 集合相等（顺序无关）⇒ 档位生效，未被覆盖
                    if (got.Count == expect.Count)
                    {
                        bool same = true;
                        for (int i = 0; i < expect.Count; i++)
                        {
                            if (!got.Contains(expect[i])) { same = false; break; }
                        }
                        if (same) return "";
                    }

                    bool rbmActive = false;
                    try { rbmActive = ModuleHelper.IsModuleActive("RBM"); }
                    catch { rbmActive = false; }
                    return rbmActive ? "RBM" : "(unknown)";
                }
                catch
                {
                    return "";
                }
            }

            // ── v0.8.11：攻城路径跳过「部署阶段」────────────────────────────────────
            // ⚠️ 必须是**实例**字段（不是 `static`）：`ScenarioProbe` 每场新建一个实例，
            //    写成 static 会让"已结束部署"跨场保留 ⇒ 第二场一进来就 return、部署永远结束不了。
            //    2026-09-25 真机踩到：第二次跑攻城卡在部署界面，插桩里连一行都没有。
            private bool _siegeDeploymentFinished;

            /// <summary>
            /// 无玩家时结束攻城部署阶段。照官方 CPU benchmark 的两步
            /// （`CPUBenchmarkMissionLogic.cs:376-381`）：
            ///   1) 给引擎一个"玩家代理"：`Mission.Current.MainAgent = AttackerTeam.ActiveAgents[0]`
            ///      —— 官方 benchmark 是无玩家自动跑的，它也是这么干的；
            ///   2) `DeploymentHandler.FinishDeployment()` 结束部署。
            /// 只做一次；handler/agent 还没就绪就下帧再试（那不是错误）。整体 try/catch，绝不抛。
            /// ⚠️ 尚未真机验证：若部署仍不结束，下一步改成官方那种 `Utilities.ConstructMainThreadJob(...)` 排队调用。
            /// </summary>
            private bool _siegeWaitLogged;
            private bool _flagsLogged;

            private void TryFinishSiegeDeployment(Mission m)
            {
                if (_siegeDeploymentFinished) return;
                try
                {
                    DeploymentHandler handler = null;
                    foreach (MissionBehavior behavior in m.MissionBehaviors)
                    {
                        DeploymentHandler candidate = behavior as DeploymentHandler;
                        if (candidate != null) { handler = candidate; break; }
                    }
                    if (handler == null) return;

                    // 官方同款第一步：无玩家也得有个 MainAgent（CPUBenchmarkMissionLogic.cs:379）。
                    // ★ 没有 agent 就**必须等** —— 在 MainAgent=null 时调 FinishDeployment 会抛异常，
                    //   而且部署阶段会永远结束不了（2026-09-25 真机实测）。
                    if (m.MainAgent == null)
                    {
                        if (m.AttackerTeam != null)
                        {
                            foreach (Agent agent in m.AttackerTeam.ActiveAgents)
                            {
                                m.MainAgent = agent;
                                break;
                            }
                        }
                        if (m.MainAgent == null)
                        {
                            if (!_siegeWaitLogged)
                            {
                                _siegeWaitLogged = true;
                                SiegeTrace("waiting for attacker agents; atkActive=" + AgentCount(m.AttackerTeam)
                                    + " defActive=" + AgentCount(m.DefenderTeam) + " runState=" + State);
                            }
                            return;
                        }
                        SiegeTrace("mainAgent set; atkActive=" + AgentCount(m.AttackerTeam)
                            + " defActive=" + AgentCount(m.DefenderTeam) + " runState=" + State);
                    }

                    handler.FinishDeployment();
                    _siegeDeploymentFinished = true;
                    SiegeTrace("FinishDeployment OK; runState=" + State);
                }
                catch (Exception ex)
                {
                    // 不每帧重试（异常 + 刷日志成本高）：把**异常原文**落盘，下次真机就能据它定位
                    _siegeDeploymentFinished = true;
                    SiegeTrace("FinishDeployment FAILED: " + ex.GetType().Name + ": " + ex.Message);
                    LastError = "siege_deploy: " + ex.GetType().Name + ": " + ex.Message;
                }
            }

            /// <summary>数该方 active agent（诊断用；取不到一律 -1）。</summary>
            private static int AgentCount(Team team)
            {
                if (team == null) return -1;
                try
                {
                    int n = 0;
                    foreach (Agent agent in team.ActiveAgents) n++;
                    return n;
                }
                catch
                {
                    return -1;
                }
            }

            public override void OnMissionTick(float dt)
            {
                base.OnMissionTick(dt);
                try
                {
                    if (_endCalled) return;
                    Mission m = this.Mission;
                    if (m == null) return;

                    // v0.8.11：攻城路径的部署收尾**必须在 State 判断之前**无条件尝试 ——
                    // 真机实测（2026-09-25）：agent 生成晚于最初几帧，而 Loading 分支一旦因
                    // CountAlive>0 切到 Running 就再也不进 ⇒ 部署永远结束不了 ⇒ 主线程死锁
                    // （症状：游戏卡在部署界面、命令通道 no_response、进程仍在但 CPU 灌满）。
                    if (SiegePending) TryFinishSiegeDeployment(m);

                    // v0.8.11：攻城语义/TeamAI 只能在 **tick 阶段**读 —— `MissionCombatantsLogic`
                    // 是在 `EarlyStart()` 里设 `Mission.MissionTeamAIType` 并挂
                    // `TeamAISiegeAttacker`/`TeamAISiegeDefender` 的，而 `OpenSiegeMissionWithDeployment`
                    // 返回时 `EarlyStart` 还没跑（实测在返回点读到的是初始值 `NoTeamAI`，属误判）。
                    // 每场只读一次，并直接读每队的 TeamAI 类型（没挂上 = 没有攻城战术）。
                    if (SiegePending && !_flagsLogged)
                    {
                        _flagsLogged = true;
                        try
                        {
                            string attAI = "?";
                            string defAI = "?";
                            try
                            {
                                attAI = m.AttackerTeam == null ? "nullTeam"
                                    : (m.AttackerTeam.TeamAI == null ? "null" : m.AttackerTeam.TeamAI.GetType().Name);
                            }
                            catch (Exception ex) { attAI = "err:" + ex.GetType().Name; }
                            try
                            {
                                defAI = m.DefenderTeam == null ? "nullTeam"
                                    : (m.DefenderTeam.TeamAI == null ? "null" : m.DefenderTeam.TeamAI.GetType().Name);
                            }
                            catch (Exception ex) { defAI = "err:" + ex.GetType().Name; }
                            SiegeTrace("flags@tick runState=" + State
                                + " IsSiegeBattle=" + m.IsSiegeBattle
                                + " TeamAIType=" + m.MissionTeamAIType
                                + " attackerTeamAI=" + attAI
                                + " defenderTeamAI=" + defAI);
                        }
                        catch (Exception ex)
                        {
                            SiegeTrace("flags@tick failed: " + ex.GetType().Name + ": " + ex.Message);
                        }

                        // 若"攻城语义/TeamAI"没挂上 ⇒ 按官方 `MissionCombatantsLogic.EarlyStart` 的
                        // 代码补齐：设 `MissionTeamAIType = Siege`，并给每队挂
                        // `TeamAISiegeAttacker` / `TeamAISiegeDefender`（那是攻城战术的真身）。
                        // **只在缺失时补**（`team.TeamAI != null` 就完全不动）⇒ 不干扰引擎自己的行为。
                        try
                        {
                            if (m.MissionTeamAIType != Mission.MissionTeamAITypeEnum.Siege)
                            {
                                m.MissionTeamAIType = Mission.MissionTeamAITypeEnum.Siege;
                                SiegeTrace("fixup: MissionTeamAIType -> Siege");
                            }
                            foreach (Team team in m.Teams)
                            {
                                if (team == null || team.TeamAI != null) continue;
                                if (team.Side == BattleSideEnum.Attacker)
                                {
                                    team.AddTeamAI(new TeamAISiegeAttacker(m, team, 5f, 1f));
                                    SiegeTrace("fixup: added TeamAISiegeAttacker");
                                }
                                else if (team.Side == BattleSideEnum.Defender)
                                {
                                    team.AddTeamAI(new TeamAISiegeDefender(m, team, 5f, 1f));
                                    SiegeTrace("fixup: added TeamAISiegeDefender");
                                }
                            }
                        }
                        catch (Exception ex)
                        {
                            SiegeTrace("fixup failed: " + ex.GetType().Name + ": " + ex.Message);
                        }
                    }

                    if (State == RunStateLoading)
                    {
                        int a = CountAlive(m.AttackerTeam);
                        int d = CountAlive(m.DefenderTeam);
                        if (a >= 0 && d >= 0 && (a + d) > 0)
                        {
                            if (AttackerInitial < 0) AttackerInitial = a;
                            if (DefenderInitial < 0) DefenderInitial = d;
                            _missionStartUnix = NowUnix;
                            State = RunStateRunning;
                            ApplyFastForward(m);
                            // T7：首轮入场完成 ⇒ 写两侧的 squad 事件（source="supplier"）。
                            // 此刻 _pendingSuppliers 仍指向本场的 SquadTroopSupplier（首轮 provided 已定）。
                            WriteFirstRoundSquadEvents();
                        }
                        return;
                    }

                    if (State != RunStateRunning) return;

                    LastHeartbeatUnix = NowUnix;

                    _sinceSpeedCheck += dt;
                    if (_sinceSpeedCheck >= 1f)
                    {
                        _sinceSpeedCheck = 0f;
                        ApplyFastForward(m);
                    }

                    // ── T13：组路径周期性重申 order + 编队 order 观测 ──────────────
                    // 只在 running 状态累计（本处已在 `State != RunStateRunning` 的 return 之后）。
                    // 无 groups 时 ReapplyGroupOrders/WriteOrderEvents 立即返回 ⇒ 旧路径逐字未变（GC2）。
                    _sinceOrderRefresh += dt;
                    if (_sinceOrderRefresh >= OrderRefreshSeconds)
                    {
                        _sinceOrderRefresh = 0f;
                        ReapplyGroupOrders(m);
                    }
                    _sinceOrderSample += dt;
                    if (_sinceOrderSample >= OrderSampleSeconds)
                    {
                        _sinceOrderSample = 0f;
                        WriteOrderEvents(m);
                        WriteTacticsEvents(m);   // v0.8.43：档位观测（仅请求过档位时才写 ⇒ GC2）
                    }

                    int aAlive = CountAlive(m.AttackerTeam);
                    int dAlive = CountAlive(m.DefenderTeam);

                    if (_endRequested)
                    {
                        Finish(m, _endReason.Length > 0 ? _endReason : "aborted");
                        return;
                    }
                    if (aAlive == 0 && dAlive == 0)
                    {
                        Finish(m, "mutual");
                        return;
                    }
                    if (aAlive == 0)
                    {
                        Finish(m, "attackerWiped");
                        return;
                    }
                    if (dAlive == 0)
                    {
                        Finish(m, "defenderWiped");
                        return;
                    }
                    double elapsed = NowUnix - _missionStartUnix;
                    if (elapsed >= DurationCapSeconds)
                    {
                        Finish(m, "timeout");
                        return;
                    }
                }
                catch (Exception ex)
                {
                    LastError = "probe: " + ex.GetType().Name + ": " + ex.Message;
                }
            }

            protected override void OnEndMission()
            {
                base.OnEndMission();
                try
                {
                    _endCalled = true;
                    Mission m = this.Mission;
                    StringBuilder sb = new StringBuilder();
                    sb.Append("{\"reason\":").Append(Protocol.Q(_endReason.Length > 0 ? _endReason : "engineEnded"));
                    sb.Append(",\"durationSec\":").Append(Jw.N((float)DurationNow()));
                    sb.Append(",\"aAlive\":").Append(Jw.N(m != null ? CountAlive(m.AttackerTeam) : -1));
                    sb.Append(",\"dAlive\":").Append(Jw.N(m != null ? CountAlive(m.DefenderTeam) : -1));
                    sb.Append(",\"aInitial\":").Append(Jw.N(AttackerInitial));
                    sb.Append(",\"dInitial\":").Append(Jw.N(DefenderInitial));
                    sb.Append(",\"attackerTroop\":").Append(Protocol.Q(AttackerTroop));
                    sb.Append(",\"defenderTroop\":").Append(Protocol.Q(DefenderTroop));
                    sb.Append(",\"finishedUtc\":").Append(Protocol.Q(Jw.UtcNow()));
                    sb.Append('}');
                    ResultJson = sb.ToString();
                    if (State != RunStateError) State = RunStateEnded;
                }
                catch (Exception ex)
                {
                    LastError = "onEnd: " + ex.GetType().Name + ": " + ex.Message;
                    State = RunStateError;
                }
            }

            private static double DurationNow()
            {
                if (_missionStartUnix <= 0) return 0;
                return NowUnix - _missionStartUnix;
            }

            private static void ApplyFastForward(Mission m)
            {
                try
                {
                    // v0.8.33：先尊重"用户显式干预"。
                    // 在此之前这里是**无条件** `SetFastForwardingFromUI(true)` ⇒ 推演场次里
                    // 用户按热键关掉加速后，1 秒后就被这里重申回 true（关不住）。
                    // 现在：未干预时保持历史行为（推演一律 10 倍速），干预过就以用户为准。
                    if (!TimeControl.UserOverride)
                    {
                        TimeControl.TargetSpeed = 10;
                    }
                    TimeControl.Apply(m);
                }
                catch
                {
                }
            }

            private static int CountAlive(Team team)
            {
                try
                {
                    return team == null ? -1 : team.ActiveAgents.Count;
                }
                catch
                {
                    return -1;
                }
            }

            private void Finish(Mission m, string reason)
            {
                if (_endCalled) return;
                _endCalled = true;
                _endReason = reason;
                try
                {
                    m.EndMission();
                }
                catch (Exception ex)
                {
                    LastError = "endMission: " + ex.GetType().Name + ": " + ex.Message;
                    State = RunStateError;
                }
            }
        }

        /// <summary>
        /// T5：每方一个的"按组轮转"troop supplier。
        /// 它不自己造 origin —— CustomBattleAgentOrigin 的 troopSupplier 参数类型是**具体类**
        /// CustomBattleTroopSupplier（不是接口，也无法覆盖它的非 virtual 方法），
        /// 所以每个 origin 都由对应的**组级** custom supplier 产生，这里只做顺序编排与汇总。
        ///
        /// 组顺序：组 0 取完才进组 1，不交错。SupplyTroops(n) 允许在**同一次调用里跨组补齐**
        /// （理由见 task-5-report.md §1：引擎 initial spawn 会一次性请求该方全部兵力，
        ///  不补齐则第一组之后的组永远不会被 spawn）。
        /// </summary>
        internal sealed class SquadTroopSupplier : IMissionTroopSupplier
        {
            private readonly List<SquadSpec> _specs;
            private readonly CustomBattleTroopSupplier[] _groups;
            private readonly CustomBattleCombatant _whole;
            private readonly int[] _provided;
            private int _groupIndex;

            internal SquadTroopSupplier(List<SquadSpec> specs, CustomBattleTroopSupplier[] groups,
                CustomBattleCombatant whole)
            {
                if (specs == null || groups == null || specs.Count != groups.Length)
                {
                    throw new ArgumentException("SquadTroopSupplier: specs 与 groups 数量必须一致（传入 "
                        + (specs == null ? "null" : specs.Count.ToString(CultureInfo.InvariantCulture))
                        + " vs "
                        + (groups == null ? "null" : groups.Length.ToString(CultureInfo.InvariantCulture)) + "）");
                }
                _specs = specs;
                _groups = groups;
                _whole = whole;
                _provided = new int[groups.Length];
                _groupIndex = 0;
            }

            /// <summary>只读暴露本方的组规格（供 T7 的分组归因）。</summary>
            internal IReadOnlyList<SquadSpec> Specs { get { return _specs; } }

            /// <summary>
            /// 每组已"交给引擎"的 origin 数（供 T7 的 squad 事件）。
            /// 注意：这是 supplied 计数，**不等于**实际 spawn 的 agent 数 —— T7 需从 team 侧 Agent 统计。
            /// 返回副本，避免外部改动内部数组（修复轮 1 · nit）。
            /// </summary>
            internal IReadOnlyList<int> ProvidedCounts
            {
                get
                {
                    int[] copy = new int[_provided.Length];
                    Array.Copy(_provided, copy, _provided.Length);
                    return copy;
                }
            }

            public int NumRemovedTroops
            {
                get
                {
                    int sum = 0;
                    for (int i = 0; i < _groups.Length; i++) sum += _groups[i].NumRemovedTroops;
                    return sum;
                }
            }

            public int NumTroopsNotSupplied
            {
                get
                {
                    int sum = 0;
                    for (int i = 0; i < _groups.Length; i++) sum += _groups[i].NumTroopsNotSupplied;
                    return sum;
                }
            }

            /// <summary>任一组还有兵就为 true（按各组"剩余量"算，不依赖组级 supplier 的内部标志）。</summary>
            public bool AnyTroopRemainsToBeSupplied
            {
                get
                {
                    for (int i = 0; i < _specs.Count; i++)
                    {
                        if (_specs[i].Count - _provided[i] > 0) return true;
                    }
                    return false;
                }
            }

            public IEnumerable<IAgentOriginBase> SupplyTroops(int numberToAllocate)
            {
                List<IAgentOriginBase> result = new List<IAgentOriginBase>();
                int need = numberToAllocate;
                while (need > 0 && _groupIndex < _groups.Length)
                {
                    int remaining = _specs[_groupIndex].Count - _provided[_groupIndex];
                    if (remaining <= 0)
                    {
                        _groupIndex++;
                        continue;
                    }
                    int take = need < remaining ? need : remaining;
                    List<IAgentOriginBase> batch = new List<IAgentOriginBase>(_groups[_groupIndex].SupplyTroops(take));
                    for (int i = 0; i < batch.Count; i++)
                    {
                        result.Add(batch[i]);
                        _provided[_groupIndex]++;
                    }
                    need -= batch.Count;
                    if (batch.Count < take) _groupIndex++;
                }
                return result;
            }

            public IAgentOriginBase SupplyOneTroop()
            {
                while (_groupIndex < _groups.Length
                       && _specs[_groupIndex].Count - _provided[_groupIndex] <= 0)
                {
                    _groupIndex++;
                }
                if (_groupIndex >= _groups.Length) return null;
                IAgentOriginBase origin = _groups[_groupIndex].SupplyOneTroop();
                if (origin != null) _provided[_groupIndex]++;
                return origin;
            }

            public IEnumerable<IAgentOriginBase> GetAllTroops()
            {
                List<IAgentOriginBase> all = new List<IAgentOriginBase>();
                for (int i = 0; i < _groups.Length; i++) all.AddRange(_groups[i].GetAllTroops());
                return all;
            }

            public BasicCharacterObject GetGeneralCharacter()
            {
                return _whole.General;
            }

            public int GetNumberOfPlayerControllableTroops()
            {
                return _whole.CountOfCharacters;
            }
        }
    }
}
