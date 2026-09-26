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
            catch
            {
            }
        }

        private static void HandleOne(string path)
        {
            // 文件名派生的安全 id：内容里的 id 伪造不了响应路径
            string id = RequestGuard.SafeFileName(Path.GetFileNameWithoutExtension(path));
            string raw = null;
            string response;

            try
            {
                long len = RequestGuard.FileLength(path);
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
                        string method = Jmini.Str(raw, "method", "");

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
                                    response = Dispatch(id, method, raw);
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
                response = Protocol.Failure(id, "invalid_request",
                    ex.GetType().Name + ": " + ex.Message, false);
            }

            WriteResponse(id, response);
            SafeDelete(path);
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
