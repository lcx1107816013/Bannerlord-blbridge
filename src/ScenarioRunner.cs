using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using TaleWorlds.Core;
using TaleWorlds.Library;
using TaleWorlds.Localization;
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

        public static string Start(string id, string raw)
        {
            if (Busy)
            {
                return Protocol.Failure(id, "busy",
                    "已有一场推演在进行中（state=" + State + "），请先 abort 或等它结束", false);
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
            string roundSpawnA = Jmini.Str(raw, "roundSpawnAttacker", "");
            string roundSpawnD = Jmini.Str(raw, "roundSpawnDefender", "");

            // 1) 必须处于自定义战斗界面（官方 benchmark 同样要求 CustomBattleState）
            string stateName = "";
            try
            {
                if (Game.Current != null && Game.Current.GameStateManager != null &&
                    Game.Current.GameStateManager.ActiveState != null)
                {
                    stateName = Game.Current.GameStateManager.ActiveState.GetType().Name;
                }
            }
            catch
            {
            }
            if (!allowAnyState && stateName != "CustomBattleState")
            {
                return Protocol.Failure(id, "wrong_state",
                    "需要停留在「自定义战斗」界面（当前状态: " + (stateName.Length == 0 ? "未知" : stateName) +
                    "）。进游戏后点 Custom Battle，停在选兵界面即可；或传 allowAnyState=true 跳过本检查。", false);
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
                OpenMission(scene, attackerTroop, defenderTroop, aCount, dCount);
                State = RunStateLoading;
                LastHeartbeatUnix = NowUnix;

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"accepted\":true,\"state\":").Append(Protocol.Q(State));
                sb.Append(",\"attackerTroop\":").Append(Protocol.Q(attacker));
                sb.Append(",\"defenderTroop\":").Append(Protocol.Q(defender));
                sb.Append(",\"attackerCount\":").Append(Jw.N(aCount));
                sb.Append(",\"defenderCount\":").Append(Jw.N(dCount));
                sb.Append(",\"scene\":").Append(Protocol.Q(scene));
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
                Mission m = Mission.Current;
                if (m != null) m.EndMission();
                return Protocol.Success(id, "{\"aborted\":true,\"state\":" + Protocol.Q(State) + "}");
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "abort_failed", ex.GetType().Name + ": " + ex.Message, true);
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

        private static void OpenMission(string scene, BasicCharacterObject attackerTroop,
            BasicCharacterObject defenderTroop, int aCount, int dCount)
        {
            BasicCultureObject culture = MBObjectManager.Instance.GetObject<BasicCultureObject>("empire");

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

            MissionState.OpenNew("BlBridgeScenario", rec, CreateBehaviors, true, true);
        }

        private static CustomBattleCombatant _pendingAttacker;
        private static CustomBattleCombatant _pendingDefender;
        private static IMissionTroopSupplier[] _pendingSuppliers;
        private static string _pendingOrders = "charge";
        private static BattleSideEnum _pendingPlayerSide = BattleSideEnum.Attacker;

        private static IEnumerable<MissionBehavior> CreateBehaviors(Mission mission)
        {
            List<MissionBehavior> list = new List<MissionBehavior>();
            // 谁当"玩家侧"可切换：MissionCombatantsLogic 的参数顺序是
            //   (battleCombatants, playerBattleCombatant, defenderLeader, attackerLeader, teamAIType, isPlayerSergeant)
            // 官方 benchmark 传的是 (null, playerParty, enemyParty, playerParty, ...)，即 playerParty 同时是攻方首领。
            // 注意：3/4 号参数必须分别是**两个不同阵营**的首领，只有 playerBattleCombatant 跟着开关变。
            IBattleCombatant attackerLeader = _pendingAttacker;
            IBattleCombatant defenderLeader = _pendingDefender;
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

            public override void OnMissionTick(float dt)
            {
                base.OnMissionTick(dt);
                try
                {
                    if (_endCalled) return;
                    Mission m = this.Mission;
                    if (m == null) return;

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
                    // 官方 10 倍速通道（MissionState.MissionFastForwardSpeedMultiplier = 10）
                    m.SetFastForwardingFromUI(true);
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
    }
}
