using System;
using TaleWorlds.MountAndBlade.View.Screens;
using TaleWorlds.ScreenSystem;

namespace BlBridge
{
    /// <summary>
    /// 「幽灵/自由相机」开关（v0.8.16，C 方案）：直接使用**引擎自带**的开发者镜头，
    /// 而不是自己写相机、也不是抄 RTSCamera 的代码。
    ///
    /// 依据（全部来自反编译源码，行号为检索返回）：
    ///   `MissionScreen.cs:224` `public bool IsCheatGhostMode { get; set; }` —— 公开可写；
    ///   `:3809-3811` 相机锁模式级联里 `if (IsCheatGhostMode) val5 = (SpectatorCameraTypes)0;`
    ///                （0 = `Free` 自由相机）—— 也就是说打开它，引擎自己就把镜头交给自由相机；
    ///   `:3840` `if ((val5 != 1 && val5 != 7 && Mission.Mode != 6) ||
    ///                (IsCheatGhostMode && !IsOrderMenuOpen && !IsTransferMenuOpen))`
    ///               —— 幽灵模式下**命令 UI 开着也保持自由**（这就是"能观战 + 能下令"）；
    ///   `:3298` `HandleUserInputCheatMode(dt)` —— 镜头的移动/缩放输入**由引擎自己处理**，
    ///               我们一行输入代码都不用写；
    ///   `:833-835` 官方自己就是这么开的：`if (ScreenManager.TopScreen is MissionScreen ms) ms.IsCheatGhostMode = true;`
    ///
    /// ⚠️ 两条诚实边界：
    ///   1) "能自己用 WASD 飞"还需要 `Game.Current.CheatMode`（`:2868` 把输入处理门控在它下面）。
    ///      它是**只读**的：`Game.CheatMode => GameManager.CheatMode => NativeConfig.CheatMode
    ///      => EngineApplicationInterface.IConfig.GetCheatMode()`，最终来自
    ///      `Documents\...\Configs\engine_config.txt` 的 `cheat_mode`（默认 0）。
    ///      所以我们只打开"自由观察 + 与命令 UI 共存"，**不**去改全局作弊开关。
    ///   2) 它是**开发者/作弊通道**，行为没有像 RTSCamera 那样被大量玩家验证过；与 RTSCamera
    ///      并存时两者都在动镜头 —— 装了 RTSCamera 的机器优先用它的配置（见 bl_apply_rts_config）。
    ///
    /// 与 `SpectatorWatchBehavior` 的分工：那个是"挂一个 ICameraModeLogic 返回 Free"（需要
    /// mission 起来之前挂、且会被 RTSCamera 抢先）；本类是"运行时直接改引擎的相机开关"，
    /// **任何时刻、任何一场**（包括玩家自己打的战斗）都能开关。
    /// </summary>
    internal static class GhostCamera
    {
        /// <summary>当前 mission 的屏幕对象；不在 mission 里时返回 null。</summary>
        private static MissionScreen CurrentScreen()
        {
            try
            {
                return ScreenManager.TopScreen as MissionScreen;
            }
            catch
            {
                return null;
            }
        }

        internal static bool IsOn()
        {
            MissionScreen ms = CurrentScreen();
            if (ms == null) return false;
            try
            {
                return ms.IsCheatGhostMode;
            }
            catch
            {
                return false;
            }
        }

        /// <summary>请求格式：`{"mode":"on|off|toggle"}` 或 `{"enabled":true|false}`；空 = 只查状态。</summary>
        internal static string HandleCommand(string raw)
        {
            string mode = Jmini.Str(raw, "mode", "");
            if (mode.Length == 0 && Jmini.Has(raw, "enabled"))
            {
                mode = Jmini.Bool(raw, "enabled", false) ? "on" : "off";
            }
            if (mode.Length == 0 || mode == "status") return StatusJson();

            bool target;
            switch (mode)
            {
                case "on":
                case "true":
                case "1":
                    target = true;
                    break;
                case "off":
                case "false":
                case "0":
                    target = false;
                    break;
                case "toggle":
                    target = !IsOn();
                    break;
                default:
                    return "{\"ok\":false,\"error\":" + Protocol.Q("mode 只接受 on / off / toggle / status")
                        + ",\"got\":" + Protocol.Q(mode) + "}";
            }

            MissionScreen ms = CurrentScreen();
            if (ms == null)
            {
                // 不在 mission 里（主菜单/界面）——明确报错，**不静默假装成功**
                // （"没报错但没生效"是本项目最贵的一类坑，见 PROGRESS §二十六）。
                return "{\"ok\":false,\"code\":" + Protocol.Q("not_in_mission")
                    + ",\"error\":" + Protocol.Q("当前不在 mission 里（ScreenManager.TopScreen 不是 MissionScreen）"
                                                 + "：请先开一场战斗，再切换幽灵相机")
                    + ",\"ghostCamera\":" + Jw.B(false) + "}";
            }
            try
            {
                ms.IsCheatGhostMode = target;
            }
            catch (Exception ex)
            {
                return "{\"ok\":false,\"code\":" + Protocol.Q("set_failed")
                    + ",\"error\":" + Protocol.Q(ex.GetType().Name + ": " + ex.Message) + "}";
            }

            string after;
            try
            {
                after = Jw.B(ms.IsCheatGhostMode);
            }
            catch
            {
                after = "null";
            }
            UiEntry.Log("ghost_camera: IsCheatGhostMode -> " + (target ? "true" : "false") + "（回读=" + after + "）");
            return "{\"ok\":true,\"requested\":" + Jw.B(target) + ",\"ghostCamera\":" + after
                + ",\"note\":" + Protocol.Q("引擎自带的自由观察相机；命令 UI 开着也保持自由（MissionScreen.cs:3840）。"
                                            + "要能自己用 WASD 飞还需 engine_config.txt 的 cheat_mode=1（只读项，我们不改）")
                + "}";
        }

        internal static string StatusJson()
        {
            MissionScreen ms = CurrentScreen();
            bool inMission = ms != null;
            bool on = false;
            if (inMission)
            {
                try
                {
                    on = ms.IsCheatGhostMode;
                }
                catch
                {
                }
            }
            return "{\"ok\":true,\"inMission\":" + Jw.B(inMission) + ",\"ghostCamera\":" + Jw.B(on) + "}";
        }
    }
}
