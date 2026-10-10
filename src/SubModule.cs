using System;
using System.IO;
using System.Text;
using TaleWorlds.CampaignSystem;        // Campaign / CampaignGameStarter（观察者挂载用）
using TaleWorlds.Core;                  // Game / IGameStarter
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
        /// v0.8.49：`crash_test mode=managed` 的**延迟抛异常**标志。
        ///
        /// ★ 为什么必须延迟到 `OnApplicationTick` **最末尾**：
        ///   本方法的每一段都自带 `try`（照 AGENTS.md「主线程 tick 硬规则」要求三段隔离）。
        ///   若在段内抛出，会被**自己的 try 吃掉** ⇒ 异常根本到不了
        ///   `Managed.ApplicationTick` ⇒ CrashGuard 的 Finalizer 看不到它 ⇒ 验收**假通过**。
        ///   所以抛出点必须在**所有 try 块之外**。
        /// </summary>
        internal static volatile bool PendingManagedThrow;

        /// <summary>本会话已完成的场次数（只读出口，给面板与状态文件共用）。</summary>
        internal static int MissionsThisSession
        {
            get { return _missionCount; }
        }

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
                // v0.8.14：**不再注册任何自建主菜单入口**。官方自定义战斗本身就是完整入口
                // （战斗 / 围攻 / 村庄 / 海战 / 海上掠夺 + 玩家类型 + 选择攻守方 + 全套地图参数），
                // 我们复刻它属重复建设 ⇒ 面板（BattleSetup*，v0.8.14 删）与原型探针（v0.8.13 删）
                // 都已退役。分工定格：**人用官方界面玩，AI 用端口调**（list_ui / open_ui / close_ui）。
                WriteStatus("loaded", null, 0);
                // v0.8.47：装运行时异常捕获（**FirstChance**，零 Harmony）。
                //
                // 为什么需要：ButterLib 的崩溃报告**不落盘**（只弹 ImGui/WinForms 窗口），
                // 而 BlBridge 是**无人值守**的 ⇒ 弹窗会卡死整个流程；实测也没有"自动写文件"的开关。
                // 所以自己订阅、落结构化 JSONL（`<LogDir>\exceptions.jsonl`，**追加**模式）。
                // ★ 与 `bl_crash`（读 WER minidump）互补：那一半管"进程已死"，这一半管
                //   "进程还活着但有人抛异常"。
                // ⚠️ 边界（如实）：只记**托管**异常；**JIT 期失败**与**原生崩溃**抓不到
                //   —— 那些不在任何方法体内，只能靠 dump（`bl_crash --deep`）。
                ExceptionProbe.Install();

                // v0.8.48（B2）：**自己落 minidump**，显式带 `MiniDumpWithFullMemoryInfo(0x800)`。
                //
                // 为什么需要：WER 落的 dump **缺 `MemoryInfoList`**（实测 **0/6 份**有），
                // 而它提供的**页保护（r/w/x）**正是区分"模块外那段内存"是
                // **JIT 代码** 还是 **Harmony detour** 的唯一依据 ——
                // 前者是引擎/CLR 问题，后者是**我们改的 mod** 的问题，结论完全不同。
                //
                // ★ 成本实测（推翻了我最初的顾虑）：只多 **0.02 MB / 0 ms**
                //   （`MemoryInfoList` 流仅 ~17 KB，记的是**页保护元数据**而非内存内容）；
                //   真正贵的是 `FullMemory`（49 MB / 25 倍），**本模块不要它**。
                //
                // ★ 两条实测约束（决定实现形状，见 `CrashDump.cs` 类注释）：
                //   ① **两个钩子缺一不可** —— 纯 SEH 原生崩溃**只有** Win32 顶层过滤器触发；
                //   ② 同一次崩溃会被两个钩子各调一次 ⇒ 内部 `Interlocked` **去重**。
                //
                // ⚠️ 边界（不许宣称全覆盖）：`Environment.FailFast`(0xC0000409)
                //   **两条钩子都不触发**，而它占真实 dump 的 3/6 ⇒ 那类继续走 WER。
                //
                // ★ 保留份数 = 3（**真机实测定的**，不是拍的）：
                //   真机一份 dump 是 **~86 MB**（不是我早前用小进程估的 2 MB ——
                //   主因是 `WithProcessThreadData`，**WER 也有这一位**）。
                //   86 MB × 10 = 860 MB 太多 ⇒ 降到 3（最坏 ~260 MB），
                //   而且**崩溃 dump 是最新落的那份**，历史份数价值低。
                CrashDump.Install(DumpDir, 3);

                // v0.8.49：崩溃守卫（在关键 tick 路径吞托管异常，让游戏继续跑）。
                //
                // ★ **默认关闭**（`BridgeConfig.CrashGuardEnabled = false`），要用必须在
                //   `blbridge_game.json` 里显式打开。这是**安全决定，不是保守**：
                //   吞异常 = 让"本该崩"的进程继续跑，而那个方法**没做完它该做的事**
                //   ⇒ 可能产生**不崩溃、不报错**的静默损坏（存档不一致 / AI 卡死 / 数值错乱），
                //   比崩溃更难查。需要它的场合：无人值守长跑、演示、定位。
                //
                // ★ 与 ExceptionProbe 的分工（**能力不同，不是重复建设**）：
                //   `ExceptionProbe`（FirstChance）能**观察**但不能**阻止** ——
                //   它是通知，返回值被忽略；
                //   `CrashGuard`（Harmony Finalizer）是唯一能**阻止传播**的钩子。
                //   两者互补：前者记录全集，后者记录"我们放过了哪些"。
                //
                // ★ 仍然零 Harmony 硬依赖：CrashGuard 用**反射**挂 Finalizer，
                //   Harmony 没装就退化成 no-op 并如实记 `installError`。
                if (BridgeConfig.CrashGuardEnabled)
                {
                    CrashGuard.Install();
                    // ★ 必须**重写一次状态** —— `WriteStatus` 在 `Install` 之前跑过，
                    //   那时 `CrashGuard.PatchedCount` 还是 0 ⇒ 状态文件会写
                    //   `installed:false / patchedTargets:0`，而账本里明明写着"已挂 3 个目标"。
                    //   实测踩到（2026-10-07 真机验收）：状态与账本**互相矛盾**，
                    //   而 `bl_status` 读的是状态文件 ⇒ 外部会误判成"守卫没装上"。
                    //   这类"两个产物说法不一致"比单纯漏写更难查，所以就地纠正顺序。
                    WriteStatus("loaded", null, 0);
                }
            }
            catch
            {
            }
        }

        /// <summary>
        /// v0.8.50：把**战役观察者**挂进战役（observer 层）。
        ///
        /// ## 为什么用 `InitializeGameStarter` 而不是 `OnGameStart`
        ///
        /// 官方自己的做法（`NavalDLCSubModule.cs:69-77`）就是在 `InitializeGameStarter` 里
        /// `if (game.GameType is Campaign)` 再 `gameStarterObject as CampaignGameStarter` 然后
        /// `AddBehavior(...)`。照抄它 = 走官方已验证的装配顺序，不自创时机。
        ///
        /// ## 为什么用 `AddBehavior` 而不是自己 tick
        ///
        /// `CampaignBehaviorBase` 的 `RegisterEvents()` 会被引擎在**战役装配期**调用，
        /// 那时 `CampaignEvents.Instance` 已就绪 ⇒ 订阅一定挂得上。
        /// 自己找时机订阅要处理"战役还没起来"的竞态，没有必要。
        ///
        /// ## 绝不参与的边界
        ///
        /// 观察者**只订阅、只读**：不 AddModel、不改任何 game model、
        /// 不 patch 任何方法 ⇒ 与本项目"零 Harmony / 删模块即完全回退"一致。
        /// 想关随时 `observer_config enabled=false`（运行时）或改配置。
        /// </summary>
        protected override void InitializeGameStarter(Game game, IGameStarter gameStarterObject)
        {
            base.InitializeGameStarter(game, gameStarterObject);
            try
            {
                if (!BridgeConfig.Enabled) return;
                if (!(game.GameType is Campaign)) return;
                CampaignGameStarter starter = gameStarterObject as CampaignGameStarter;
                if (starter == null) return;
                starter.AddBehavior(new CampaignObserver());
                UiEntry.Log("campaign observer: 已挂载（订阅 9 个 CampaignEvents，只读不干预）");
            }
            catch (Exception ex)
            {
                // 观察者挂不上绝不能影响战役启动 —— 它不是必需品。
                try
                {
                    ActionLedger.ExceptionToRgl("SubModule.InitializeGameStarter", ex);
                }
                catch
                {
                }
            }
        }

        /// <summary>
        /// B2 的 dump 落盘目录：`&lt;LogDir&gt;\crashes`。
        ///
        /// 为什么**另开目录**而不混进 `%LOCALAPPDATA%\CrashDumps`：
        /// 那里是 WER 的地盘，且我们要能一眼分清"哪份 dump 是 BlBridge 落的、带页保护"，
        /// 所以文件名一律带 **`blbridge-`** 前缀，且独立目录便于保留策略。
        /// </summary>
        internal static string DumpDir
        {
            get { return Path.Combine(BridgeConfig.LogDir, "crashes"); }
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
                    // v0.8.33：游戏内热键 —— 加速切换（默认 Home，10x ⇄ 1x）+ Tab 诊断探针。
                    // 挂在这里而不是 `ScenarioRunner.CreateBehaviors`，就是为了**玩家自己打的
                    // 战斗也生效**（CreateBehaviors 只服务于 AI 推演那条开战路径）。
                    // 详见 HotkeyBehavior 的类注释（含"为什么只能 1x/10x、做不出 2x/5x"）。
                    mission.AddMissionBehavior(new HotkeyBehavior());
                    // v0.8.14 上帝视角（AI 测试场次专用）：`start_battle` 传 spectate=true 时，
                    // 把镜头交给引擎自带的自由观察相机（实现见 SpectatorWatchBehavior）。
                    // **消费即清**：读完立刻复位，保证玩家自己打的战斗绝不会继承这个标志 ——
                    // 静态标志污染下一场是本项目踩过的坑（见 DummyRangeBehavior 的复位注释）。
                    if (ScenarioRunner.SpectateRequested)
                    {
                        ScenarioRunner.SpectateRequested = false;
                        // v0.8.15：**装了 RTSCamera 就让位**。两者都实现 ICameraModeLogic，
                        // 而 MissionScreen 是 FirstOrDefault —— 我实测过：野战里 RTSCamera 的
                        // FlyCameraMissionView 排在下标 30、我们的在 43，它先被选中（我们的只被
                        // 问了 8 次）；围城里则是我们被每帧问（8511 次）但它给的是"观察者镜头"，
                        // 不是用户要的抬升自由视角。两边都挂只会互相抢镜头/输入。
                        // ⇒ 我们退化成"**没装 RTSCamera 时的兜底**"；装了它就走配置管理
                        //    （bl_rts_config / bl_apply_rts_config / start_battle 的 rtsPreset）。
                        if (TaleWorlds.ModuleManager.ModuleHelper.IsModuleActive("RTSCamera"))
                        {
                            UiEntry.Log("spectate: 检测到 RTSCamera 已启用 ⇒ 不挂兜底相机"
                                        + "（改用它的配置：bl_apply_rts_config / rtsPreset）");
                        }
                        else
                        {
                            mission.AddMissionBehavior(new SpectatorWatchBehavior());
                            UiEntry.Log("spectate: SpectatorWatchBehavior 已挂载（兜底：本机未装 RTSCamera）");
                        }
                    }
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
                // v0.8.47：卸掉 FirstChance 订阅并关侧信道 ——
                // ★ 这是"**删模块即完全回退**"的一部分：订阅是我们自己挂的，撤得干净。
                //   与本项目"零 Harmony"的立场一致（我们没改任何别人的代码，
                //   只是订阅了一个 .NET 原生事件）。
                ExceptionProbe.Uninstall();
                // v0.8.49：撤掉崩溃守卫的 Finalizer —— 同样是"删模块即完全回退"的一部分。
                // ⚠️ 这里**必须撤**：Finalizer 是**改别人方法 IL**的补丁（与 ExceptionProbe
                //   只是订阅事件不同），不撤的话模块卸载后钩子仍指向我们的方法 ⇒ 悬空引用。
                CrashGuard.Uninstall();
                // v0.8.50：关掉战役观察者的事件账本并做最后一次排空 —— 同理，
                // ★ 订阅要撤得干净：观察者只 `AddNonSerializedListener`（订阅 .NET 事件），
                //   撤订阅由引擎在战役结束/卸载时按 owner 做（`ClearListeners`），
                //   这里只负责收尾写盘与关文件，不留半开的句柄。
                CampaignObserver.Shutdown();
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
            // v0.8.34：应用级真实帧时间 —— 2x/5x 挡靠它算 `Mission.FixedDeltaTime`。
            // **不能**用 mission tick 的 dt：在 `FixedDeltaTimeMode` 下那个 dt 已经被引擎
            // 换成 `FixedDeltaTime` 自己了，拿它乘 N 会正反馈爆炸（×2 → ×4 → ×8 …）。
            TimeControl.RealDt = dt;
            CommandPump.Pump();
            // v0.8.39：失焦"保活"（CampaignProbe.Tick 每帧改 Campaign.TimeControlMode）已按
            // 用户要求移除 —— 它看不见"失焦自动打开的暂停菜单"那种暂停，且会顶掉手动暂停。
            // 见 CampaignProbe 顶部注释与 PROGRESS §三十二/§三十三。
            // v0.8.10：看门狗必须挂在这里而不是 Mission tick —— mission tick 卡住时
            // 主线程仍在跑，这里能观察到「busy 但久无心跳」并强制收尾（见 ScenarioRunner.Watchdog）。
            ScenarioRunner.Watchdog();
            // v0.8.47：排空异常队列（**I/O 只在这里做**）。
            //
            // ★ 为什么不在 FirstChance 回调里直接写：那个回调在**任何线程、任何异常**上触发，
            //   可能极其频繁（正常控制流也会大量抛）。照 AGENTS.md「主线程 tick 硬规则」，
            //   回调里只入队，写盘放到主线程。
            // ★ 独立 try：**不能让它吃掉后面的东西**，也不该被前面某段抛异常而跳过
            //   （看门狗已经在前一段自带 try，这里同理自隔离）。
            // ★ 每帧限量：异常风暴时不让一帧写几千行把帧率拖垮（余下的下帧继续）。
            try
            {
                ExceptionProbe.Drain(64);
            }
            catch
            {
            }
            // v0.8.49：排空崩溃守卫账本（同为 I/O，同样只在主线程做）。
            // ★ 独立 try，理由同上：不让它被前一段的影响吃掉，也不让它影响后面的段。
            try
            {
                CrashGuard.Drain(64);
            }
            catch
            {
            }
            // v0.8.50：排空**战役观察者**事件账本（I/O 只在主线程做）。
            //
            // ★ 为什么必须在这里排空、而不能在事件处理器里直接写文件：
            //   事件处理器跑在**战役主线程**，而快进时 AI 决策每秒可触发数百次。
            //   在处理器里写盘会拖慢游戏本身 ⇒ **改变被测系统的行为**（观测者效应），
            //   而我们建这套东西的目的正是"如实观察"。⇒ 处理器只入队，这里批量写。
            // ★ 独立 try：观察者出问题绝不能影响命令泵/看门狗/异常探针。
            try
            {
                CampaignObserver.DrainPending();
            }
            catch
            {
            }
            // v0.8.54：推进战役刺激器的**跨帧待办**（目前是"存档完成后回主菜单"）。
            // ★ 为什么必须在每帧这里做、而不是在命令处理器里等：
            //   处理器跑在主线程，而存盘（`SaveTick`）与 `EndGame`（`async void`）
            //   都依赖后续帧才能推进 ⇒ 在处理器里等会**死锁**。
            // ★ 独立 try：待办失败绝不能影响其它每帧段。
            try
            {
                CampaignStimulus.Tick();
            }
            catch
            {
            }

            // ★★ v0.8.49：受控**托管**抛异常（仅 `crash_test mode=managed` 用）。
            //
            // ⚠️ 位置是**刻意**的 —— 必须在上面所有 `try` 隔离块**之外**：
            //   段内抛出会被本方法自己的 `try` 吃掉，异常就到不了
            //   `Managed.ApplicationTick`（CrashGuard 的挂载点）⇒ 验收会**假通过**
            //   （游戏当然没崩，但那是因为我们自己吞了，不是因为守卫生效）。
            //   放在末尾、try 之外，异常才能真的沿
            //   CoreManaged → Module.OnApplicationTick → Managed.ApplicationTick 传上去。
            //
            // 正常发布版永远不会走到这里：闸门在 CommandPump（需显式调 method +
            // 环境变量 `BLBRIDGE_ALLOW_CRASH_TEST=1`），标志恒为 false。
            if (PendingManagedThrow)
            {
                PendingManagedThrow = false;
                throw new InvalidOperationException(
                    "BlBridge 受控托管异常（crash_test mode=managed）—— "
                    + "用于验收 CrashGuard：守卫开则应被吞掉、游戏继续；守卫生效前应终止进程。");
            }
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
            // v0.8.49：崩溃守卫概览 —— 让外部（MCP 侧 / 人）一眼看到
            // "吞了多少、放行多少、配额/熔断有没有触发"。**不吞时这些是 0**，不是缺失。
            sb.Append(",\"crashGuard\":").Append(CrashGuard.SummaryJson());
            // v0.8.45：账本健康 —— 把"写失败静默"变成外部**一眼可见**。
            // 缺口的全部危害在于"少行而无人知"；有了这两个字段，bl_cmd/bl_mcp 就能报警。
            sb.Append(",\"ledger\":{");
            sb.Append("\"writeFailures\":").Append(ActionLedger.WriteFailureCount);
            sb.Append(",\"lastWriteFailure\":").Append(Protocol.Q(ActionLedger.LastWriteFailure));
            sb.Append('}');
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
