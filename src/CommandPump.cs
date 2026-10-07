using System;
using System.IO;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// 文件 IPC 命令泵。
    ///
    /// 外部（MCP 侧）把请求写成 &lt;LogDir&gt;\commands\pending\&lt;seq&gt;-&lt;id&gt;.json，
    /// 游戏在**主线程**（MBSubModuleBase.OnApplicationTick）轮询并执行，再把响应写到
    /// &lt;LogDir&gt;\commands\done\&lt;id&gt;.json，最后删除请求文件。
    ///
    /// 为什么用文件而不是 HTTP/命名管道：本地单客户端场景下文件最稳 ——
    /// 没有 URL ACL 提权问题、没有端口冲突、进程崩溃后请求/响应都留痕可查。
    /// 延迟上限 = 轮询间隔（250ms），而单场战斗是 20~40 秒量级，完全可以忽略。
    ///
    /// v0.4.0 加固（对照 Coop 的字段级校验 + "过期请求作废"语义）：
    ///   1. **过期作废**：请求带 issuedUtc，超过 BridgeConfig.MaxRequestAgeSeconds 直接
    ///      回 request_expired 并丢弃 —— 否则"MCP 超时退出、游戏后启动"会把旧请求执行掉
    ///      （表现为凭空冒出一场没要的战斗）。Coop 靠命名管道断开天然规避，我们靠这条。
    ///   2. **id 白名单**：id 参与响应文件名拼接，必须是 16~32 位十六进制，否则拒绝
    ///      （防 id="..\..\evil" 的路径穿越）。
    ///   3. **大小上限**：请求文件超过 1 MiB 直接拒绝，不读进内存。
    /// </summary>
    internal static class CommandPump
    {
        private const string PendingFolder = "pending";
        private const string DoneFolder = "done";
        private static DateTime _nextPollUtc = DateTime.MinValue;
        private static readonly TimeSpan PollInterval = TimeSpan.FromMilliseconds(250);

        public static string CommandsRoot
        {
            get { return Path.Combine(BridgeConfig.LogDir, "commands"); }
        }

        public static string PendingDir
        {
            get { return Path.Combine(CommandsRoot, PendingFolder); }
        }

        public static string DoneDir
        {
            get { return Path.Combine(CommandsRoot, DoneFolder); }
        }

        public static void EnsureDirs()
        {
            try
            {
                if (!Directory.Exists(CommandsRoot)) Directory.CreateDirectory(CommandsRoot);
                if (!Directory.Exists(PendingDir)) Directory.CreateDirectory(PendingDir);
                if (!Directory.Exists(DoneDir)) Directory.CreateDirectory(DoneDir);
            }
            catch
            {
            }
        }

        /// <summary>
        /// B2：`crash_test` 的**延迟崩溃**标志。
        ///
        /// 为什么不在 `Dispatch` 里直接崩：那一刻响应还没落盘（`WriteResponse` 在
        /// `HandleOne` 末尾）、账本也还没记 ⇒ 外部会**永远等不到响应**，
        /// 无法区分"是我们主动崩的"还是"随机崩溃"，验收就没有对照。
        ///
        /// ⇒ 置标志 → `HandleOne` 照常写响应与账本 → **写完再崩**。
        /// </summary>
        private static bool _pendingCrash;

        /// <summary>必须在游戏主线程调用（引擎对象只能在主线程访问）。</summary>
        public static void Pump()
        {
            if (!BridgeConfig.Enabled) return;
            if (DateTime.UtcNow < _nextPollUtc) return;
            _nextPollUtc = DateTime.UtcNow + PollInterval;
            try
            {
                EnsureDirs();
                string[] files = Directory.GetFiles(PendingDir, "*.json");
                if (files.Length == 0) return;
                Array.Sort(files, StringComparer.Ordinal);
                for (int i = 0; i < files.Length; i++)
                {
                    HandleOne(files[i]);
                }
            }
            catch (Exception ex)
            {
                // v0.8.42：原来这里是**静默** catch。巡目录/建目录失败的后果是"泵整个不干活"，
                // 而外部看到的只是"命令没反应"——这类无法定位的状态必须有第二出口（RGL）。
                ActionLedger.ExceptionToRgl("CommandPump.Pump", ex);
            }
        }

        private static void HandleOne(string path)
        {
            // 文件名派生的安全 id：内容里的 id 伪造不了响应路径
            string id = RequestGuard.SafeFileName(Path.GetFileNameWithoutExtension(path));
            string raw = null;
            string response;
            // ── v0.8.42：动作账本的三个观测量 ────────────────────────────────
            // **只在采集侧加变量，一个判定分支都不改**（GC2：拒绝顺序与错误码逐字节不变）。
            // 账本要把 method 带出来，所以它从内层 else 里提到这里（原来是块内声明）。
            DateTime ledgerStartedUtc = DateTime.UtcNow;
            long ledgerBytes = -1;
            string ledgerMethod = "";

            try
            {
                long len = RequestGuard.FileLength(path);
                ledgerBytes = len;
                if (len > RequestGuard.MaxRequestBytes)
                {
                    response = Protocol.Failure(id, "request_too_large",
                        "请求文件过大：" + len + " 字节（上限 " + RequestGuard.MaxRequestBytes + "）", false);
                }
                else
                {
                    try
                    {
                        raw = File.ReadAllText(path, Encoding.UTF8);
                    }
                    catch
                    {
                    }

                    if (string.IsNullOrEmpty(raw))
                    {
                        response = Protocol.Failure(id, "invalid_request", "请求文件为空", false);
                    }
                    else
                    {
                        int ver = Jmini.Int(raw, "protocolVersion", 0);
                        string reqId = Jmini.Str(raw, "id", null);
                        ledgerMethod = Jmini.Str(raw, "method", "");

                        if (!RequestGuard.IsValidId(reqId))
                        {
                            response = Protocol.Failure(id, "invalid_request",
                                "请求 id 非法（须 16~32 位十六进制）：" +
                                (string.IsNullOrEmpty(reqId) ? "(缺失)" : reqId), false);
                        }
                        else
                        {
                            id = RequestGuard.SafeFileName(reqId);
                            if (ver != Protocol.Version)
                            {
                                response = Protocol.Failure(id, "unsupported_version",
                                    "协议版本不匹配：请求 " + ver + "，本模块 " + Protocol.Version, false);
                            }
                            else if (RequestGuard.IsExpired(Jmini.Str(raw, "issuedUtc", null),
                                         DateTime.UtcNow, BridgeConfig.MaxRequestAgeSeconds))
                            {
                                response = Protocol.Failure(id, "request_expired",
                                    "请求已过期（issuedUtc 距现在超过 " + BridgeConfig.MaxRequestAgeSeconds +
                                    " 秒，游戏当时可能没运行）—— 已作废，不会执行", false);
                            }
                            else
                            {
                                try
                                {
                                    response = Dispatch(id, ledgerMethod, raw);
                                }
                                catch (Exception ex)
                                {
                                    // 已进入方法执行阶段：可能已产生副作用（走了部分逻辑）
                                    response = Protocol.Failure(id, "handler_exception",
                                        ex.GetType().Name + ": " + ex.Message + "\n" + ex.StackTrace, true);
                                }
                            }
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                // v0.8.42：外层兜底异常**无条件**写游戏 RGL（照 GameMaster 的两级策略）。
                // 理由：走到这里说明连"读文件/判大小"都出错了，而那时 <LogDir> 很可能本身不可写
                // ⇒ 自定义账本写不进去，rgl_log 是唯一还能留痕的地方。
                ActionLedger.ExceptionToRgl("CommandPump.HandleOne", ex);
                response = Protocol.Failure(id, "invalid_request",
                    ex.GetType().Name + ": " + ex.Message, false);
            }

            RecordLedger(id, ledgerMethod, ledgerBytes, raw, response, ledgerStartedUtc);
            WriteResponse(id, response);
            SafeDelete(path);

            // ★ B2：受控崩溃的**真正触发点**。
            //
            // 放在这里（响应已落盘、账本已记）是刻意的：外部能先读到
            // `aboutToCrash:true` 的响应，据此确认"这次崩溃是我们主动造的"，
            // 而不是随机崩溃 —— 否则验收没有对照。
            //
            // ⚠️ 只有 `crash_test` 且环境变量闸门已开时才会走到这里（见 Dispatch）。
            if (_pendingCrash)
            {
                _pendingCrash = false;
                CrashDump.RaiseNativeCrash();      // 纯 SEH ⇒ 只有 Win32 钩子会触发
                // 正常情况下不会执行到这里（进程已死）；若返回说明 RaiseException 没生效，
                // 那么如实写 RGL，不留"以为崩了其实没崩"的状态。
                ActionLedger.ExceptionToRgl("CommandPump.crash_test",
                    new InvalidOperationException(
                        "crash_test 调了 RaiseException(0xC0000005) 但进程没崩 —— 需复查钩子"));
            }
        }

        /// <summary>
        /// v0.8.42：把这一次请求落进动作账本。**从已组装好的响应里反读** ok / code / note
        /// （`Jmini` 是只读器，读自己刚拼的 JSON 是安全的）—— 这样就不需要给
        /// `Protocol.Success/Failure` 加返回值，也就不会牵动任何既有判定分支。
        /// 整体 try/catch：账本绝不影响请求处理结果。
        /// </summary>
        private static void RecordLedger(string id, string method, long bytes, string raw,
            string response, DateTime startedUtc)
        {
            try
            {
                // v0.8.45：判定集中在 `Protocol.ReadResponseOutcome`（纯 BCL、可离线断言），
                // 让**出货代码**与**离线断言**调同一个方法 —— 不是各抄一份判定逻辑
                // （抄一份就是"验副本"，违反项目纪律）。
                // 修掉的缺口 B：原来 `Jmini.Bool(response,"ok",false)` 兜底是 false
                // ⇒ 成功的请求只要响应读不出来就被记成失败，且与真失败无法区分。
                bool readable, ok, uncertain;
                bool? resultOk;
                string code, note;
                Protocol.ReadResponseOutcome(response, out readable, out ok, out code,
                    out uncertain, out note, out resultOk);
                double ms = (DateTime.UtcNow - startedUtc).TotalMilliseconds;
                ActionLedger.Record(id, method, ok, code, ms, bytes, uncertain, note,
                    ActionLedger.ArgsSnippet(raw), resultOk);
            }
            catch
            {
            }
        }

        private static string Dispatch(string id, string method, string raw)
        {
            if (method == "ping")
            {
                return Protocol.Success(id, "{\"protocolVersion\":" + Protocol.Version +
                                            ",\"mod\":\"" + BridgeConfig.ModuleId +
                                            "\",\"version\":\"" + BridgeConfig.Version + "\"}");
            }
            if (method == "status")
            {
                return Protocol.Success(id, ScenarioRunner.StatusJson());
            }
            // ── B2：崩溃落盘（v0.8.48）────────────────────────────────────────
            // 三个方法刻意放在 `status` 旁边：它们都是**诊断**用途，且零游戏依赖
            // （不碰 TaleWorlds 任何 API ⇒ 在哪个状态下都能调）。
            if (method == "dump_status")
            {
                return Protocol.Success(id, CrashDump.StatusJson());
            }
            if (method == "dump_now")
            {
                // 按需写一份**当前进程**的 dump（健康进程也能调，不崩、零游戏副作用）。
                //
                // 为什么值得有：实测**健康进程写出的 dump 同样带 `MemoryInfoList`**
                // （2.0 MB / 36 ms，flags 与崩溃路径完全相同）⇒ 它覆盖 `bl_crash`
                // 现在**结构上覆盖不到**的一类问题：**"卡住/僵死但进程还活着"**
                // （那时既没有 WER dump，也还没崩）。
                //
                // 参数（可选）：fullMemory=true ⇒ 加 `MiniDumpWithFullMemory`
                //（**49 MB / 25 倍**，默认 false —— 它解决的是"要看内存内容"，与 B2 目标不同）
                bool fullMemory = Jmini.Bool(raw, "fullMemory", false);
                string path;
                string err = CrashDump.WriteNow(fullMemory, out path);
                if (err != null)
                {
                    return Protocol.Failure(id, "dump_failed", err, false);
                }
                return Protocol.Success(id, "{\"path\":" + Protocol.Q(path)
                    + ",\"fullMemory\":" + Jw.B(fullMemory)
                    + ",\"note\":" + Protocol.Q(
                        "本 dump 带 MemoryInfoList（页保护）；bl_crash 可直接读，"
                        + "或用 dump_streams.py 核对 stream 16 是否存在") + "}");
            }
            if (method == "crash_test")
            {
                // ★ **受控崩溃**（验收 B2 用）：显式造一次纯 SEH 原生异常。
                //
                // 为什么用 `RaiseException` 而不是抛托管异常：实测四条路径里
                //   `RaiseException` 是**最难的那条**（托管钩子不触发、只有 Win32 钩子触发）
                // ⇒ 验它通过，更容易的托管路径自然也没问题。
                //
                // 双重安全闸门（缺一不可，发布版**不可能**误崩）：
                //   ① 必须显式调本 method（没有默认触发路径）；
                //   ② 必须设环境变量 `BLBRIDGE_ALLOW_CRASH_TEST=1`（**默认关**）。
                if (!CrashDump.CrashTestAllowed)
                {
                    return Protocol.Failure(id, "crash_test_disabled",
                        "受控崩溃被闸门拦住：需显式设置环境变量 "
                        + "BLBRIDGE_ALLOW_CRASH_TEST=1（默认关，防误崩）", false);
                }
                // v0.8.49：`mode=managed` ⇒ 造一次**托管**异常，用于验收 CrashGuard。
                //
                // ★ 为什么必须另加这个模式（不能复用 native）：
                //   CrashGuard 靠 Harmony **Finalizer** 吞异常，而 Finalizer **只看得到托管异常**。
                //   原生 SEH 崩溃（native 模式）**结构上**不可能被它拦住 ——
                //   用 native 去验 CrashGuard 会得出"没吞掉 ⇒ 功能坏了"的**错误结论**。
                //   两者测的是**不同的东西**：native 验 dump 落盘，managed 验崩溃跳过。
                //
                // ⚠️ 同样受上面那道环境变量闸门约束（复用同一个，不另立一个新闸门
                //    让人记 —— 少一个"忘了设"的机会）。
                string mode = Jmini.Str(raw, "mode", "native");
                if (mode == "managed")
                {
                    // ★ 与 native 同样的两段式：这里只**置标志**（此刻响应还没落盘），
                    //   真正的抛出在 `SubModule.OnApplicationTick` 末尾 ——
                    //   那里在所有 `try` 隔离块**之外**，异常才能真的传播到
                    //   `Managed.ApplicationTick`（即 CrashGuard 挂载的那个方法）。
                    SubModule.PendingManagedThrow = true;
                    return Protocol.Success(id, "{\"aboutToThrow\":true"
                        + ",\"mode\":\"managed\""
                        + ",\"exceptionType\":\"System.InvalidOperationException\""
                        + ",\"patchTarget\":\"TaleWorlds.DotNet.Managed.ApplicationTick\""
                        + ",\"note\":" + Protocol.Q(
                            "本响应落盘后，下一次 ApplicationTick 会抛出受控托管异常。"
                            + "守卫**开**时：游戏应继续运行，crashguard.jsonl 出现 action=swallow；"
                            + "守卫**关**时：异常应传播出 Managed.ApplicationTick ⇒ 进程终止"
                            + "（这就是 A/B 的对照腿）。") + "}");
                }
                // ★ 先把"即将崩溃"写进响应（外部据此对账：收到 ok=true ⇒ 确认是本次
                //   主动崩的，不是随机崩溃），**再**崩。
                // ⚠️ 但**不能在这里崩** —— 此刻响应还没落盘（`WriteResponse` 在
                //   `HandleOne` 末尾）、账本也还没记。所以这里只**置标志**，
                //   真正的崩溃放在 `HandleOne` 写完响应与账本之后（见 `_pendingCrash`）。
                _pendingCrash = true;
                return Protocol.Success(id, "{\"aboutToCrash\":true"
                    + ",\"exceptionCode\":\"0xC0000005\""
                    + ",\"trigger\":\"RaiseException(SEH)\""
                    + ",\"dumpDir\":" + Protocol.Q(SubModule.DumpDir)
                    + ",\"note\":" + Protocol.Q(
                        "本响应落盘后进程立即崩溃；BlBridge 的 dump 应落在 dumpDir，"
                        + "文件名带 blbridge-native- 前缀，且**只有 1 份**（去重生效）") + "}");
            }
            if (method == "start_battle")
            {
                return ScenarioRunner.Start(id, raw);
            }
            if (method == "abort")
            {
                return ScenarioRunner.Abort(id);
            }
            if (method == "fast_forward")
            {
                return Protocol.Success(id, TimeControl.HandleCommand(raw));
            }
            if (method == "speed")
            {
                return Protocol.Success(id, TimeControl.SpeedJson());
            }
            // ── 游戏内 UI 入口（v0.8.12，agent 正门）────────────────────────────
            // 存在理由：AI 不该靠"模拟鼠标"操作游戏 UI（原型轮实测：同一套合成输入，
            // 官方主菜单可点、自写层不可点）。走官方正门 ExecuteInitialStateOptionWithId。
            // 三个方法都在主线程（Pump 由 OnApplicationTick 驱动），与引擎状态栈的约束一致。
            if (method == "list_ui")
            {
                return UiEntry.HandleListUi(id, raw);
            }
            if (method == "open_ui")
            {
                return UiEntry.HandleOpenUi(id, raw);
            }
            if (method == "close_ui")
            {
                return UiEntry.HandleCloseUi(id, raw);
            }
            // ── 幽灵/自由相机（v0.8.16，C 方案）──────────────────────────────
            // 用引擎自带的 IsCheatGhostMode（开发者自由镜头），运行时即可开关，
            // **对玩家自己打的战斗同样有效**（与 spectate 那条"只对 AI 场次"不同）。
            if (method == "ghost_camera")
            {
                return Protocol.Success(id, GhostCamera.HandleCommand(raw));
            }
            // ── 相机移动速度（v0.8.17）───────────────────────────────────────
            // 用户反馈"原版相机移动速度太慢了"。三条腿：官方控制台函数（Shift 倍率）/
            // 引擎基础倍率（反射）/ RTSCamera 的 MovementSpeedFactor（反射，最有效）。
            // 每条腿都**写完回读**，并且失败要点名 —— 见 CameraSpeed.cs 的类注释。
            if (method == "camera_speed")
            {
                return Protocol.Success(id, CameraSpeed.HandleCommand(raw));
            }
            // ── 启动/流程控制（v0.8.20）─────────────────────────────────────
            // 两者都是从 BUTR/Bannerlord.GABS 的做法学来的（见 src/GameFlow.cs 的类注释）：
            //   skip_video  = 判 VideoPlaybackState → OnVideoFinished()，替掉"盲按 ESC"
            //   cheat_mode  = 写 NativeConfig.CheatMode（私有 setter/后备字段）+ 回读，
            //                 解开引擎相机的倍率热键与速度读数
            if (method == "skip_video")
            {
                return Protocol.Success(id, GameFlow.HandleSkipVideo(raw));
            }
            if (method == "cheat_mode")
            {
                return Protocol.Success(id, GameFlow.HandleCheatMode(raw));
            }
            // ── 战斗中途改令（v0.8.23）───────────────────────────────────────
            // 与开战 DSL 同一条下发路径（Formation.SetMovementOrder + MapMovement），
            // 但**必须在 mission 内**：mission 之外碰 MovementOrder 会永久污染该类型。
            // 两条硬约束与判据见 src/BattleOrders.cs 的类注释。
            if (method == "order")
            {
                return Protocol.Success(id, BattleOrders.HandleCommand(raw));
            }
            // ── 接管士兵（v0.8.25，最小版：改 Mission.MainAgent + Controller = Player）──
            // 官方那条路（Mission.CanTakeControlOfAgent）只在主角阵亡后才允许、且**不改 MainAgent**；
            // 五步做法与逐条出处见 src/ControlAgent.cs 的类注释。
            if (method == "control_agent")
            {
                return Protocol.Success(id, ControlAgent.HandleCommand(raw));
            }
            // ── 只读 UI 探测（L2 #1，脱壳抄 BUTR/Bannerlord.GABS 的 Tools/GauntletUITools.cs）──
            // 只读取 ScreenManager.TopScreen 的层/影片/ViewModel 与一个属性值，**零副作用**，
            // 也不引入 Lib.GAB / TCP / Newtonsoft（见 docs/l2-api-drift-1.4.8.md §2 P17/P18/P19/P20/P21）。
            // 这是让 AI「读」官方界面状态、而不是去抢鼠标的最高杠杆入口。
            if (method == "get_screen")
            {
                return UiInspector.HandleGetScreen(id, raw);
            }
            if (method == "get_viewmodel_property")
            {
                return UiInspector.HandleGetViewModelProperty(id, raw);
            }
            // ── L2 #2：战役库存只读（脱壳抄上游 InventoryTools.cs 的 inventory/get_inventory）──
            // needs=campaign：主菜单 / 自定义战斗里 Campaign.Current 为空 ⇒ 如实报 no_campaign，不猜。
            if (method == "get_inventory")
            {
                return InventoryProbe.HandleGetInventory(id, raw);
            }
            // ── Harmony 补丁内省（只读）──
            // 回答「谁补了哪个方法」。三合一 MOD 的核心风险是「双重叠加」（两个 mod 补同一方法、
            // Transpiler 撞 Transpiler 生成非法 IL），此前只能靠猜。
            //
            // ⚠️ 它**不违反**本项目「零 Harmony 补丁」的原则：只调 Harmony 的**公开内省 API**，
            // 不创建 Harmony 实例、不 patch 任何方法、不改任何 IL —— 与 EngineProbe 同一性质。
            // 且**用反射**（而非 using HarmonyLib）⇒ BlBridge.dll 对 0Harmony.dll **零依赖**，
            // Harmony 没装时返回 available=false 而不是加载失败。
            // 无 needs：任何时候都能读（模块加载完就有补丁表）。
            if (method == "get_patches")
            {
                return PatchProbe.HandleGetPatches(id, raw);
            }
            // ── MCM（Mod Configuration Menu）设置表只读 ──
            // 面板型 mod 的可调参数最终都落成 MCM 设置项 ⇒ 读得到它 = 拿到全场 mod 的参数面。
            // 同样**只读 + 零依赖（反射）**：不注册设置、不写值、不碰它的 DI 容器。
            if (method == "get_mcm_settings")
            {
                return McmProbe.HandleGetMcmSettings(id, raw);
            }
            // ── UIExtenderEx 界面扩展只读（第三座桥）──
            // 回答「哪个 mod 改了哪个官方界面」。也是三合一 C4（浮点输入框）那套
            // 手工注册（WidgetFactory/_builtinTypes）的**成熟版对照物**。
            // 同样**只读 + 零依赖（反射）**。
            if (method == "get_ui_extensions")
            {
                return UiExtendProbe.HandleGetUiExtensions(id, raw);
            }
            // ── 运行时异常统计（FirstChance 捕获的只读视图）──
            // 与 `bl_crash`（读 WER minidump）互补：那个管"进程已死"，这个管"进程还活着"。
            // ⚠️ 边界：只含**托管**异常；JIT 期失败与原生崩溃不在其中（要靠 dump）。
            if (method == "get_exceptions")
            {
                return Protocol.Success(id, ExceptionProbe.Summary());
            }
            // ── 补丁失败清单（把 HarmonyException 从「现象」变成「点名」）──
            // 与 `bl_patches` **交叉使用**才完整：这里给"补丁**想补的目标**"，
            // 把 targetClass 交给 `bl_patches` 按类型反查就能看到 owner ⇒ 定位到 mod。
            // ⚠️ 发起补丁的 mod **不在异常里**（HarmonyException 只带目标描述），
            //    所以本接口的输出里显式标注了这一点，不假装它是归因结论。
            if (method == "get_patch_failures")
            {
                return Protocol.Success(id, ExceptionProbe.PatchFailures());
            }
            // ── 存档：列表 + 按名直载（脱壳抄上游 CoreTools.cs 的 core/list_saves / core/load_save）──
            // load_save 绕过存档选择界面 ⇒ 无人值守换档成为可能，也因此才能自主复现读档期弹窗。
            if (method == "list_saves")
            {
                return CampaignProbe.HandleListSaves(id, raw);
            }
            if (method == "load_save")
            {
                return CampaignProbe.HandleLoadSave(id, raw);
            }
            // ── 战役时间/暂停只读诊断：mode=status（保活已于 v0.8.39 移除，见 CampaignProbe 顶部注释）──
            if (method == "campaign_time")
            {
                return CampaignProbe.HandleCampaignTime(id, raw);
            }
            // ── A 阶段：战役只读遥测（控制面，见 src/CampaignReadProbe.cs）──
            // 全部 campaign 上下文；主菜单 / 自定义战斗如实报 no_campaign，不猜。
            if (method == "campaign_overview")
            {
                return CampaignReadProbe.HandleCampaignOverview(id, raw);
            }
            if (method == "list_kingdoms")
            {
                return CampaignReadProbe.HandleListKingdoms(id, raw);
            }
            if (method == "list_clans")
            {
                return CampaignReadProbe.HandleListClans(id, raw);
            }
            if (method == "list_settlements")
            {
                return CampaignReadProbe.HandleListSettlements(id, raw);
            }
            if (method == "list_parties")
            {
                return CampaignReadProbe.HandleListParties(id, raw);
            }
            if (method == "campaign_log")
            {
                return CampaignReadProbe.HandleCampaignLog(id, raw);
            }
            return Protocol.Failure(id, "unknown_method", "未知方法: " + method, false);
        }

        private static void WriteResponse(string id, string response)
        {
            try
            {
                EnsureDirs();
                string final = Path.Combine(DoneDir, RequestGuard.SafeFileName(id) + ".json");
                string tmp = final + ".tmp";
                File.WriteAllText(tmp, response, new UTF8Encoding(false));
                if (File.Exists(final)) File.Delete(final);
                File.Move(tmp, final);
            }
            catch
            {
            }
        }

        private static void SafeDelete(string path)
        {
            try
            {
                File.Delete(path);
            }
            catch
            {
            }
        }
    }
}
