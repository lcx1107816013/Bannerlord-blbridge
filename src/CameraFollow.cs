using System;
using System.Reflection;
using TaleWorlds.MountAndBlade;
using TaleWorlds.MountAndBlade.View.Screens;
using TaleWorlds.ScreenSystem;

namespace BlBridge
{
    /// <summary>
    /// v0.8.32：RTSCamera 的**平滑推镜**（相机跟随）—— 接管 / 换回士兵时让镜头平滑跟过去，而不是硬切。
    ///
    /// 为什么需要它：`bl_control_agent` 到 v0.8.31 只做了"五步"里第 5 步的**一半** ——
    /// 把 `MissionScreen._isPlayerAgentAdded` 复位。那其实正是 RTSCamera
    /// `AfterSetMainAgent(should=false, …)` 的 **else 分支**；而 `should=true` 那一支会
    /// `SmoothMoveToAgent(...)`（把相机从当前位置**插值**推到新角色）。所以"接管后镜头是跳的"根因就是我们没调它。
    ///
    /// 上游那套是"**夹着赋值**调两个方法"（顺序不能反）：
    /// <code>
    ///   bool should = Utility.BeforeSetMainAgent(agent);
    ///   mission.MainAgent = agent;
    ///   Utility.AfterSetMainAgent(should, missionScreen, rotateCamera);
    /// </code>
    ///
    /// 依据（**上游真源码** + 本机反编译两份互证，不猜）：
    ///   * 上游 `lzh-mb-mod/RTSCamera` @ v5.4.16（MIT）：`MissionLibrary/src/Utilities/Utility.cs`
    ///     的 `BeforeSetMainAgent` / `AfterSetMainAgent` / `SmoothMoveToAgent`；
    ///   * 本机 `Modules\RTSCamera\bin\Win64_Shipping_Client\MissionLibrary.dll` 反编译
    ///     （`ilspycmd`，2026-09-26）：类型 `MissionSharedLibrary.Utilities.Utility`，
    ///     `BeforeSetMainAgent(Agent) : bool`（`ShouldSmoothMoveToAgent && GetMissionScreen().LastFollowedAgent != agent`
    ///     ⇒ 置 `ShouldSmoothMoveToAgent=false` 并返回 true，否则返回 false）；
    ///     `AfterSetMainAgent(bool, MissionScreen, bool rotateCamera = true)`：
    ///       `should=true` ⇒ `ShouldSmoothMoveToAgent = true; SmoothMoveToAgent(screen, forceMove:false, rotateCamera, …)`；
    ///       `should=false` ⇒ `SetIsPlayerAgentAdded(screen, false)`（**正是我们原本手写的那一步**）。
    ///
    /// 为什么**必须**反射：`MissionSharedLibrary.Utilities.Utility` 住在 RTSCamera 附带的
    /// `MissionLibrary.dll` 里，**不是**游戏 / 官方程序集 ⇒ 编译期引用它等于"没装 RTSCamera 就加载不了本模块"。
    /// 反射找不到时如实给出 `why`（与 `CameraSpeed` 的 `rts` 腿同策略），**不静默 no-op**，也不改变原有行为。
    ///
    /// 判据（**能回读**，不靠"我们调了方法"）：调用后读 `MissionScreen.LastFollowedAgent` ——
    /// RTSCamera 的 `SmoothMoveToAgent` 成功时会把它设成正在跟随的那个 agent
    /// （反编译 `:1150` `SetLastFollowedAgent.Invoke(missionScreen, AgentToFollow)`）。
    ///
    /// ⚠️ 诚实标注：**"平滑"本身是视觉判据**，本模块能证明的是"上游三方法被真的调到了 + 跟随目标被写进去了"，
    /// 至于插值过程好不好看，只能人眼看着说。
    /// </summary>
    internal static class CameraFollow
    {
        private const string UtilityTypeName = "MissionSharedLibrary.Utilities.Utility";

        /// <summary>上游的静态开关（`public static bool ShouldSmoothMoveToAgent`），仅供回读。</summary>
        private const string ShouldSmoothFieldName = "ShouldSmoothMoveToAgent";

        /// <summary>
        /// 在写 `Mission.MainAgent` **之前**调用（顺序是上游要求：原版就是夹着赋值调的）。
        ///
        /// 返回 `null` = 调用成功（`shouldSmooth` 见 out 参数：true = 接下来该平滑推镜，
        /// false = 相机本来就跟着这个 agent，上游会走"只复位 screen"那一支）；
        /// 返回非 null = **不可用 / 失败的原因**（调用方据此决定是否回退到原有的 `ResetPlayerAgentAdded`），
        /// 且**不抛**。反射找不到类型/方法、形参表漂移、被调方抛异常，都各给各的原因。
        /// </summary>
        internal static string Before(Agent agent, out bool shouldSmooth)
        {
            shouldSmooth = false;
            try
            {
                Type t = FindUtilityType();
                if (t == null)
                {
                    return "没找到类型 " + UtilityTypeName
                        + "（本机应该没装 RTSCamera，或它的 MissionLibrary.dll 未加载）";
                }
                MethodInfo m = t.GetMethod("BeforeSetMainAgent",
                    BindingFlags.Public | BindingFlags.Static, null,
                    new Type[] { typeof(Agent) }, null);
                if (m == null)
                {
                    return t.FullName + ".BeforeSetMainAgent(Agent) 不存在（RTSCamera 版本变了）";
                }
                object r = m.Invoke(null, new object[] { agent });
                shouldSmooth = r is bool && (bool)r;
                return null;
            }
            catch (TargetInvocationException ex)
            {
                return "BeforeSetMainAgent 抛异常：" + Describe(ex.InnerException == null ? ex : ex.InnerException);
            }
            catch (Exception ex)
            {
                return "BeforeSetMainAgent 调用失败：" + Describe(ex);
            }
        }

        /// <summary>
        /// 在写 `Mission.MainAgent`（以及把新主角色的 Controller 交给玩家）**之后**调用。
        ///
        /// 返回 `null` = 调用成功；`lastFollowed` 是调用后回读的 `MissionScreen.LastFollowedAgent`
        /// （`(none)` = 镜子没认任何 agent，`(unknown)` = 读失败；绝不为 nil 假装成功）。
        /// 返回非 null = **不可用 / 失败的原因**，且**不抛**。
        /// </summary>
        internal static string After(bool shouldSmooth, out string lastFollowed)
        {
            lastFollowed = "(n/a)";
            try
            {
                Type t = FindUtilityType();
                if (t == null)
                {
                    return "没找到类型 " + UtilityTypeName
                        + "（本机应该没装 RTSCamera，或它的 MissionLibrary.dll 未加载）";
                }
                MissionScreen screen = ScreenManager.TopScreen as MissionScreen;
                if (screen == null)
                {
                    return "拿不到 MissionScreen（ScreenManager.TopScreen 不是 MissionScreen）⇒ 平滑推镜无从下手";
                }
                MethodInfo m = t.GetMethod("AfterSetMainAgent", BindingFlags.Public | BindingFlags.Static);
                if (m == null)
                {
                    return t.FullName + ".AfterSetMainAgent 不存在（RTSCamera 版本变了）";
                }
                // 形参表断言：版本漂移时**宁可拒绝**，也不要把顺序传错（(bool, MissionScreen, bool)）
                ParameterInfo[] ps = m.GetParameters();
                if (ps.Length != 3 || ps[0].ParameterType != typeof(bool)
                    || ps[1].ParameterType != typeof(MissionScreen) || ps[2].ParameterType != typeof(bool))
                {
                    return t.FullName + ".AfterSetMainAgent 形参表变了（期望 (bool, MissionScreen, bool)）"
                        + "⇒ 拒绝调用，免得把参数传错";
                }
                // rotateCamera=false：与上游 `SetMainAgent()` 的默认路径一致
                // （`RTSCamera.decompiled.cs:12769` 传 rotateCamera: false）——
                // "让相机跟着角色转向"是 RTSCamera 自己的用户设置，我们不去覆盖它，只做**位置**上的平滑推镜。
                m.Invoke(null, new object[] { shouldSmooth, screen, false });
                lastFollowed = ReadLastFollowed(screen);
                return null;
            }
            catch (TargetInvocationException ex)
            {
                return "AfterSetMainAgent 抛异常：" + Describe(ex.InnerException == null ? ex : ex.InnerException);
            }
            catch (Exception ex)
            {
                return "AfterSetMainAgent 调用失败：" + Describe(ex);
            }
        }

        /// <summary>
        /// 回读 RTSCamera 自己的静态开关 `ShouldSmoothMoveToAgent`（string 形式：true/false/"(unknown)"）。
        /// 只用于排查（"上游认为下一次该不该平滑"），不参与 ok 判定。绝不抛。
        /// </summary>
        internal static string ShouldSmoothText()
        {
            try
            {
                Type t = FindUtilityType();
                if (t == null) return "(unavailable)";
                FieldInfo f = t.GetField(ShouldSmoothFieldName, BindingFlags.Public | BindingFlags.Static);
                if (f == null) return "(unknown)";
                object v = f.GetValue(null);
                return v is bool ? ((bool)v ? "true" : "false") : "(unknown)";
            }
            catch
            {
                return "(unknown)";
            }
        }

        // ── 内部工具 ────────────────────────────────────────────────────────

        /// <summary>
        /// 在所有**已加载**程序集里按全名找那个类型（不引用 RTSCamera 的程序集 ⇒ 没装它也能加载本模块）。
        /// 找不到 ⇒ null（调用方据此给"不静默"的原因）。绝不抛。
        /// </summary>
        private static Type FindUtilityType()
        {
            try
            {
                Assembly[] asms = AppDomain.CurrentDomain.GetAssemblies();
                for (int i = 0; i < asms.Length; i++)
                {
                    Assembly a = asms[i];
                    if (a == null) continue;
                    try
                    {
                        Type t = a.GetType(UtilityTypeName, false);
                        if (t != null) return t;
                    }
                    catch
                    {
                        // 个别程序集 GetType 会抛（动态程序集等），跳过即可
                    }
                }
            }
            catch
            {
            }
            return null;
        }

        /// <summary>回读 `MissionScreen.LastFollowedAgent`（RTSCamera 的 `SmoothMoveToAgent` 会写它）。绝不抛。</summary>
        private static string ReadLastFollowed(MissionScreen screen)
        {
            try
            {
                Agent a = screen.LastFollowedAgent;
                if (a == null) return "(none)";
                string id = null;
                try { id = a.Character == null ? null : a.Character.StringId; }
                catch { }
                if (string.IsNullOrEmpty(id)) id = "(unknown)";
                return id + "#" + a.Index;
            }
            catch
            {
                return "(unknown)";
            }
        }

        private static string Describe(Exception ex)
        {
            if (ex == null) return "(unknown)";
            return ex.GetType().Name + ": " + ex.Message;
        }
    }
}
