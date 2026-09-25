using System;
using System.IO;
using System.Text;
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    /// <summary>
    /// 模块入口。做三件事：
    ///   1) 模块加载时准备日志/命令目录，写 bridge_status.json（含协议版本与进程身份）
    ///   2) 每场 mission 挂上 TelemetryBehavior（记录命中/阵亡）
    ///   3) 每帧泵一次文件命令队列（AI 推演的启动/查询/中止）
    /// 不改任何游戏逻辑、不 patch 任何方法、不联网。
    /// </summary>
    public class SubModule : MBSubModuleBase
    {
        private static int _missionCount;

        /// <summary>
        /// v0.8.3：下一场 mission 的来源标记。默认 "game"；
        /// `ScenarioRunner.OpenMission` 在开战前置为 "bridge"，
        /// `TelemetryBehavior` 构造时读一次并复位 ⇒ 标记只对那一场有效。
        /// </summary>
        internal static string MissionOrigin = "game";

        /// <summary>
        /// v0.8.4：本场的随机种子（-1 = 未指定）。只用于**记录**（写进 meta）；
        /// 真正设种子由 `ScenarioRunner` 在开战前调 `MBRandom.SetSeed` 完成。
        /// </summary>
        internal static int PendingRandomSeed = -1;

        // A9：崩溃检测所需的三态标记
        //   cleanExit=false 写于加载时，true 写于 OnSubModuleUnloaded（引擎的模块卸载钩子）
        //   missionInProgress 标记"退出时正在战斗中"
        private static bool _cleanExit;
        private static bool _missionInProgress;
        private static string _currentBattleFile = "";
        private static int _lastBattleEventCount;

        protected override void OnSubModuleLoad()
        {
            base.OnSubModuleLoad();
            try
            {
                BridgeConfig.EnsureDirs();
                CommandPump.EnsureDirs();
                // 可选外部配置（C24）：只在模块加载时读一次，加载即校验，改完需重启游戏
                BridgeConfigFile.Apply(BridgeConfigFile.DefaultPath);
                // PROTOTYPE (branch prototype/ui-probe): register the main-menu entry.
                // Delete this line together with src/ProtoUi.cs when the prototype is archived.
                ProtoUi.Register();
                WriteStatus("loaded", null, 0);
            }
            catch
            {
            }
        }

        /// <summary>
        /// 挂载遥测行为的**正确**时机。
        ///
        /// 引擎顺序（反编译源码 Mission.cs 行号）：
        ///   3807  cachedSubModule.OnBeforeMissionBehaviorInitialize(this)   ← 必须挂在这里
        ///   3811  MissionBehaviors[i].OnBehaviorInitialize()                ← 行为初始化循环
        ///   3815  cachedSubModule2.OnMissionBehaviorInitialize(this)
        ///
        /// 若在 3815（OnMissionBehaviorInitialize）里 AddMissionBehavior，
        /// 行为被加进列表时 3811 的初始化循环已经跑完，它的 OnBehaviorInitialize()
        /// 永远不会被调用 —— 曾因此丢掉一整场 201 个事件的数据。
        /// V0.1.1 起同时保留惰性打开作为二次保险。
        /// </summary>
        public override void OnBeforeMissionBehaviorInitialize(Mission mission)
        {
            base.OnBeforeMissionBehaviorInitialize(mission);
            if (!BridgeConfig.Enabled) return;
            try
            {
                if (mission != null)
                {
                    mission.AddMissionBehavior(new TelemetryBehavior());
                    // 临时探针：只计数，用于验证 OnScoreHit 与 OnAgentHit 是否成对出现。
                    // 验证完成后可整行删掉（见 ScoreHitProbeBehavior.cs）。
                    mission.AddMissionBehavior(new ScoreHitProbeBehavior());
                    // 不朽靶场（阶段 2①）。**必须排在 TelemetryBehavior 之后**：
                    // Mission.cs:5612-5616 是同一个 foreach、按 behavior 顺序调用，
                    // 遥测先读到扣血后的 hpAfter，靶场随后才改血量 ——
                    // 这样遥测里的 hpMax - hpAfter 恒等于"本次真实伤害"。
                    mission.AddMissionBehavior(new DummyRangeBehavior());
                }
            }
            catch
            {
            }
        }

        /// <summary>
        /// 模块卸载（引擎 `MBSubModuleBase.cs:12`）。写 cleanExit=true ——
        /// 这是区分"正常关闭游戏"与"崩溃/被强杀"的唯一可靠信号：
        /// 进程消失后，若状态文件里 cleanExit 仍是 false，就说明它没能走到这里。
        /// </summary>
        protected override void OnSubModuleUnloaded()
        {
            base.OnSubModuleUnloaded();
            try
            {
                _cleanExit = true;
                _missionInProgress = false;
                // 传最近一场的真实事件数：第一版这里写死 0，把 lastBattleEvents 抹成了 0
                WriteStatus("exited", _currentBattleFile, _lastBattleEventCount);
            }
            catch
            {
            }
        }

        /// <summary>战斗开始：把状态切到 battle，并记下战斗文件（崩溃现场要用）。</summary>
        internal static void NotifyBattleStarted(string battleFile)
        {
            _missionInProgress = true;
            if (!string.IsNullOrEmpty(battleFile)) _currentBattleFile = battleFile;
            try
            {
                WriteStatus("battle", _currentBattleFile, _lastBattleEventCount);
            }
            catch
            {
            }
        }

        /// <summary>主线程每帧回调：命令泵在这里执行（引擎对象只能在主线程碰）。</summary>
        protected override void OnApplicationTick(float dt)
        {
            base.OnApplicationTick(dt);
            CommandPump.Pump();
            // v0.8.10：看门狗必须挂在这里而不是 Mission tick —— mission tick 卡住时
            // 主线程仍在跑，这里能观察到「busy 但久无心跳」并强制收尾（见 ScenarioRunner.Watchdog）。
            ScenarioRunner.Watchdog();
        }

        /// <summary>战斗结束时由 TelemetryBehavior 回调，刷新状态文件。</summary>
        internal static void NotifyBattleFinished(string battleFile, int events)
        {
            _missionCount++;
            _missionInProgress = false;
            _lastBattleEventCount = events;
            if (!string.IsNullOrEmpty(battleFile)) _currentBattleFile = battleFile;
            try
            {
                WriteStatus("idle", battleFile, events);
            }
            catch
            {
            }
        }

        /// <summary>取当前 Mission 的安全包装（状态文件也要能反映"是否真的在推进"）。</summary>
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

        private static void WriteStatus(string state, string lastBattle, int lastEvents)
        {
            BridgeConfig.EnsureDirs();
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"mod\":\"").Append(BridgeConfig.ModuleId).Append("\"");
            sb.Append(",\"version\":\"").Append(BridgeConfig.Version).Append("\"");
            sb.Append(",\"protocolVersion\":").Append(Protocol.Version);
            sb.Append(",\"pid\":").Append(Protocol.Pid);
            sb.Append(",\"runToken\":\"").Append(Protocol.RunToken).Append("\"");
            sb.Append(",\"processStartedUtc\":\"").Append(Protocol.ProcessStartedUtc).Append("\"");
            sb.Append(",\"state\":\"").Append(state).Append("\"");
            sb.Append(",\"sessionStartedUtc\":\"").Append(SessionStartedUtc).Append("\"");
            sb.Append(",\"statusWrittenUtc\":\"").Append(Jw.UtcNow()).Append("\"");
            sb.Append(",\"missionsThisSession\":").Append(_missionCount);
            sb.Append(",\"logDir\":\"").Append(Jw.Esc(BridgeConfig.LogDir)).Append("\"");
            sb.Append(",\"battlesDir\":\"").Append(Jw.Esc(BridgeConfig.BattlesDir)).Append("\"");
            sb.Append(",\"commandsDir\":\"").Append(Jw.Esc(CommandPump.CommandsRoot)).Append("\"");
            sb.Append(",\"lastBattle\":\"").Append(Jw.Esc(lastBattle == null ? "" : lastBattle)).Append("\"");
            sb.Append(",\"lastBattleEvents\":").Append(lastEvents);
            // 构建身份：外部一眼看出"进程里跑的是不是刚部署的 DLL"（fileChangedSinceLoad = 需要重启）
            sb.Append(",\"build\":").Append(BuildInfo.Json());
            // 推进就绪：区分"真的在打"与"卡在黑屏/加载界面"
            sb.Append(",\"readiness\":").Append(EngineProbe.Json(SafeMission()));
            sb.Append(",\"config\":").Append(BridgeConfigFile.EffectiveJson());
            // A9 崩溃检测：进程消失后靠这两个标记区分"正常退出"与"崩溃/强杀"
            sb.Append(",\"cleanExit\":").Append(Jw.B(_cleanExit));
            sb.Append(",\"missionInProgress\":").Append(Jw.B(_missionInProgress));
            sb.Append(",\"enabled\":").Append(Jw.B(true));
            sb.Append("}\n");

            // 原子写：先写临时文件再替换，避免外部读到半截内容
            string tmp = BridgeConfig.StatusPath + ".tmp";
            File.WriteAllText(tmp, sb.ToString(), new UTF8Encoding(false));
            if (File.Exists(BridgeConfig.StatusPath)) File.Delete(BridgeConfig.StatusPath);
            File.Move(tmp, BridgeConfig.StatusPath);
        }

        private static readonly string SessionStartedUtc = Jw.UtcNow();
    }
}
