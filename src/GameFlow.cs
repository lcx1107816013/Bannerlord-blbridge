using System;
using System.Reflection;
using TaleWorlds.Core;
using TaleWorlds.Engine;
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    /// <summary>
    /// 启动/流程控制类通道（v0.8.20）。两个方法都是从 **BUTR/Bannerlord.GABS** 的
    /// `Tools/CoreTools.cs` 学来的做法（它那份代码注释里写了原因，很有信息量；我们只抄做法，不抄代码：
    /// 它 MIT，但版本矩阵只有 v1.3.15 / v1.3.13 / v1.2.12，不支持本机 1.4.8）。
    ///
    /// 1) `skip_video` —— 跳过开场动画。
    ///    依据：`TaleWorlds.MountAndBlade.VideoPlaybackState`（`VideoPlaybackState.cs:6`）有
    ///    `public void OnVideoFinished()`（`:40`），引擎自己的视频屏就是这么收敛的
    ///    （`VideoPlaybackScreen.cs:36` `_videoPlaybackState.OnVideoFinished();`）。
    ///    ⇒ **不必靠模拟 ESC 键**（那要先提窗、还要赌输入进得去引擎）；先问"当前活动状态是不是
    ///    VideoPlaybackState"，是就直接结束它，判据硬、无副作用。
    ///
    /// 2) `cheat_mode` —— 开/关作弊模式（**带回读**）。
    ///    `TaleWorlds.Engine.NativeConfig.CheatMode` 是 `public static bool { get; private set; }`
    ///    ⇒ 读不需要反射，写要走**私有 setter**（`GetSetMethod(true)`）或后备字段
    ///    `<CheatMode>k__BackingField`。
    ///    为什么这条重要：引擎自由相机的倍率热键（Ctrl+↑ ×1.5 / Ctrl+↓ ×2÷3 / Ctrl+中键重置，
    ///    `MissionScreen.cs:1714` 那个 `if (Game.Current.CheatMode)` 块）以及"摄像机移动速度"读数
    ///    都被它门控 ⇒ 这是"相机太慢"的另一条正解，且不用改我们的相机代码。
    ///    ⚠️ 开了它等于打开开发者/作弊通道（F2/F3/F4 杀敌杀友、Ctrl+K 幽灵相机等一并生效），
    ///    所以本通道**不做任何自动开启**，只能由调用方显式请求。
    /// </summary>
    internal static class GameFlow
    {
        private const string NativeConfigTypeName = "TaleWorlds.Engine.NativeConfig";
        private const string CheatModeMemberName = "CheatMode";

        // ───────────────────────── skip_video ─────────────────────────

        internal static string HandleSkipVideo(string raw)
        {
            try
            {
                // v0.8.21 修正：**先试静态 `GameStateManager.Current`**（实现见 `ScenarioRunner.ActiveStateManager`）。
                // 真机证据（2026-09-25 23:00，启动窗口实测）：启动初期与主菜单 `Game.Current` 是 **null**，
                // ⇒ 旧实现（`Game.Current.GameStateManager.ActiveState`）在那两段直接报 not_in_game，
                // **开场动画那一段根本够不到**，等于这个功能在最需要它的时刻失效。
                // 静态那条路那时可用（`GameStateManager.cs:53`，public static）——与 GABS 的 `core/skip_video` 同路。
                GameStateManager mgr = ScenarioRunner.ActiveStateManager();
                if (mgr == null)
                {
                    return Fail("no_state_manager",
                        "拿不到 GameStateManager：静态 GameStateManager.Current 与 Game.Current 都不可用"
                        + " ⇒ 此刻没有状态机可查，也没有开场动画可跳");
                }
                string via;
                try
                {
                    via = ReferenceEquals(mgr, GameStateManager.Current)
                        ? "GameStateManager.Current(static)" : "Game.Current.GameStateManager";
                }
                catch
                {
                    via = "unknown";
                }

                GameState st = mgr.ActiveState;
                string name = st == null ? "" : st.GetType().Name;

                VideoPlaybackState video = st as VideoPlaybackState;
                if (video == null)
                {
                    return "{\"ok\":false,\"code\":" + Protocol.Q("not_video")
                           + ",\"via\":" + Protocol.Q(via)
                           + ",\"activeState\":" + Protocol.Q(name)
                           + ",\"error\":" + Protocol.Q("当前活动状态不是 VideoPlaybackState"
                                                        + " ⇒ 这一刻没有开场动画可跳")
                           + "}";
                }

                video.OnVideoFinished();
                return "{\"ok\":true,\"via\":" + Protocol.Q(via)
                       + ",\"activeState\":" + Protocol.Q(name)
                       + ",\"note\":" + Protocol.Q("已调用 VideoPlaybackState.OnVideoFinished()"
                                                   + "（等同玩家跳过开场动画）") + "}";
            }
            catch (Exception ex)
            {
                return Fail("skip_video_failed", ex.GetType().Name + ": " + ex.Message);
            }
        }

        // ───────────────────────── cheat_mode ─────────────────────────

        /// <summary>请求格式：`{"mode":"status|on|off|toggle"}`；空 = status。</summary>
        internal static string HandleCheatMode(string raw)
        {
            try
            {
                string mode = Jmini.Str(raw, "mode", "");
                if (string.IsNullOrEmpty(mode)) mode = "status";

                bool native = NativeConfig.CheatMode;
                bool game = Game.Current != null && Game.Current.CheatMode;

                if (mode == "status")
                {
                    return "{\"ok\":true,\"mode\":\"status\",\"cheatMode\":" + Jw.B(native)
                           + ",\"nativeConfig\":" + Jw.B(native)
                           + ",\"gameCurrent\":" + (Game.Current == null ? "null" : Jw.B(game))
                           + ",\"note\":" + Protocol.Q("读的是 NativeConfig.CheatMode（public static，无需反射）；"
                                                       + "Game.Current 为 null 时（主菜单）只报前者") + "}";
                }

                bool target;
                if (mode == "on" || mode == "true" || mode == "1") target = true;
                else if (mode == "off" || mode == "false" || mode == "0") target = false;
                else if (mode == "toggle") target = !native;
                else
                {
                    return Fail("bad_mode", "mode 只接受 status / on / off / toggle，收到 '" + mode + "'");
                }

                string why;
                if (!WriteCheatMode(target, out why)) return Fail("write_failed", why);

                bool afterNative = NativeConfig.CheatMode;
                string afterGame = Game.Current == null ? "null" : Jw.B(Game.Current.CheatMode);
                bool changed = afterNative == target;
                string note = "引擎自由相机的倍率热键（Ctrl+↑/↓、Ctrl+中键）与观察者 HUD 的"
                              + "'摄像机移动速度'读数现在" + (target ? "可用" : "不可用") + "；"
                              + "本改动只作用于本次进程，游戏重启后回到 engine_config.txt 的设置。";
                if (!changed)
                    note = "⚠️ 写入后回读不一致（native 仍是 " + (afterNative ? "true" : "false")
                           + "）：可能被 OnConfigChanged 从 native 配置刷回去了 —— 别当成成功。";

                return "{\"ok\":" + Jw.B(changed)
                       + ",\"mode\":" + Protocol.Q(mode)
                       + ",\"requested\":" + Jw.B(target)
                       + ",\"cheatMode\":" + Jw.B(afterNative)
                       + ",\"nativeConfig\":" + Jw.B(afterNative)
                       + ",\"gameCurrent\":" + afterGame
                       + ",\"changed\":" + Jw.B(changed)
                       + ",\"note\":" + Protocol.Q(note) + "}";
            }
            catch (Exception ex)
            {
                return Fail("cheat_mode_failed", ex.GetType().Name + ": " + ex.Message);
            }
        }

        /// <summary>写 `NativeConfig.CheatMode`：属性私有 setter → 后备字段，两条路都试，失败要点名。</summary>
        private static bool WriteCheatMode(bool value, out string why)
        {
            why = null;
            Type t = typeof(NativeConfig);
            try
            {
                PropertyInfo p = t.GetProperty(CheatModeMemberName,
                    BindingFlags.Public | BindingFlags.Static | BindingFlags.NonPublic);
                if (p != null)
                {
                    MethodInfo setter = p.GetSetMethod(true);   // private set 也要拿
                    if (setter != null)
                    {
                        setter.Invoke(null, new object[] { value });
                        return true;
                    }
                }
            }
            catch (Exception ex)
            {
                why = "调用私有 setter 失败：" + ex.GetType().Name + ": " + ex.Message;
            }

            try
            {
                FieldInfo f = t.GetField("<" + CheatModeMemberName + ">k__BackingField",
                    BindingFlags.NonPublic | BindingFlags.Static);
                if (f != null)
                {
                    f.SetValue(null, value);
                    why = null;
                    return true;
                }
            }
            catch (Exception ex)
            {
                why = (why == null ? "" : why + "；") + "写后备字段失败：" + ex.GetType().Name + ": " + ex.Message;
            }

            if (why == null)
                why = "在 " + NativeConfigTypeName + " 上既找不到可写的 " + CheatModeMemberName
                      + " setter，也找不到后备字段（游戏版本可能变了）";
            return false;
        }

        private static string Fail(string code, string message)
        {
            return "{\"ok\":false,\"code\":" + Protocol.Q(code) + ",\"error\":" + Protocol.Q(message) + "}";
        }
    }
}
