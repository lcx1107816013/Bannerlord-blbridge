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
            sb.Append(",\"attackerGroups\":").Append(Protocol.Q(AttackerGroupsDsl));
            sb.Append(",\"defenderGroups\":").Append(Protocol.Q(DefenderGroupsDsl));
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
                int n = unknownEntries.Count;
                int shown = n > 8 ? 8 : n;
                string joined = string.Join("、", unknownEntries.GetRange(0, shown).ToArray());
                code = "unknown_troop";
                message = label + "有 " + n + " 个兵种 id 不存在：" + joined
                    + (n > 8 ? " 等 " + n + " 个" : "");
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
                    string mv = s.Movement == null ? "charge" : s.Movement;
                    f.SetMovementOrder(MapMovement(mv));
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

            /// <summary>写该方每个编队一行 `order` 事件（`count==0` 的也写，便于看「编队是否存在」）。绝不抛。</summary>
            private static void WriteOrderSide(string side, Team team, float time)
            {
                if (team == null) return;
                string tactic = "none";
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
