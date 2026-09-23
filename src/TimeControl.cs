using System;
using System.Text;
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    /// <summary>
    /// 战斗时间控制。
    ///
    /// 为什么需要它：原版的加速入口**只有 UI，没有可编程触发** ——
    /// `Mission.SetFastForwardingFromUI`（Mission.cs:6775）的生产调用方是计分板 VM 的动作
    /// `CustomBattleScoreboardVM.ExecuteFastForwardAction()`，而它前置要求 `IsMainCharacterDead`：
    /// 也就是说**原版 F 键加速只在主角阵亡后的记分板里可用**（活着时按 F 无效）。
    /// Ctrl+F9 那类慢动作还要 CheatMode，自定义战斗把作弊 UI 整个禁用。
    /// 玩家用的"快进"来自第三方 mod（例如 RTSCamera），走的是同一条通道：`Mission.IsFastForward`。
    ///
    /// ⚠️ 本类早期注释写的是"原版没有任何战斗加速键、`SetFastForwardingFromUI` 零调用"——**那是错的**。
    /// 错因：反编译源码树不含 `*ViewModelCollection.dll`，对它的搜索**静默返回 0 命中**，
    /// 而"空结果"被读成了"零调用"。现改用程序集级符号检索复核（该符号在 13 个程序集命中）。
    /// 完整修正记录见 README §六「加速通道」的修正记录。
    ///
    /// 本类提供一条**不依赖任何第三方 mod** 的加速通道：
    ///   TelemetryBehavior.OnMissionTick 每帧调用 Apply()，把 IsFastForward 重申为 true。
    ///   引擎的 MissionState 会因此每帧多跑 9 次 0.1s 的 tick（常量 MissionFastForwardSpeedMultiplier = 10）。
    ///
    /// 重要：**不要**去写 `Mission.Scene.TimeSpeed`。它每帧都被 `Mission.UpdateSceneTimeSpeed()`
    /// 覆盖成 `min(1, 请求值)`（起始值 1f，只接受更小的请求），所以直接写 10 会被立刻打回 1。
    /// </summary>
    internal static class TimeControl
    {
        /// <summary>由命令通道设置；主线程读取（volatile 保证可见性）。</summary>
        public static volatile bool ForceFastForward;

        private static int _appliedFrames;

        /// <summary>必须在主线程、且在战斗内调用（每帧一次，开销可忽略）。</summary>
        public static void Apply(Mission mission)
        {
            if (!ForceFastForward || mission == null) return;
            try
            {
                if (!mission.IsFastForward)
                {
                    mission.SetFastForwardingFromUI(true);
                }
                _appliedFrames++;
            }
            catch
            {
            }
        }

        public static bool IsFastForward(Mission m)
        {
            try
            {
                return m != null && m.IsFastForward;
            }
            catch
            {
                return false;
            }
        }

        public static float SceneTimeSpeed(Mission m)
        {
            try
            {
                return (m != null && m.Scene != null) ? m.Scene.TimeSpeed : -1f;
            }
            catch
            {
                return -1f;
            }
        }

        public static int MissionMode(Mission m)
        {
            try
            {
                return m != null ? (int)m.Mode : -1;
            }
            catch
            {
                return -1;
            }
        }

        /// <summary>处理 fast_forward 命令。raw 是原始请求 JSON（扁平取 enabled 字段）。</summary>
        public static string HandleCommand(string raw)
        {
            bool enabled = Jmini.Bool(raw, "enabled", false);
            ForceFastForward = enabled;
            if (!enabled) _appliedFrames = 0;

            Mission m = null;
            try
            {
                m = Mission.Current;
            }
            catch
            {
            }

            StringBuilder sb = new StringBuilder();
            sb.Append("{\"forced\":").Append(Jw.B(ForceFastForward));
            sb.Append(",\"missionActive\":").Append(Jw.B(m != null));
            sb.Append(",\"isFastForward\":").Append(Jw.B(IsFastForward(m)));
            sb.Append(",\"sceneTimeSpeed\":").Append(Jw.N(SceneTimeSpeed(m)));
            sb.Append(",\"missionMode\":").Append(Jw.N(MissionMode(m)));
            sb.Append(",\"appliedFrames\":").Append(Jw.N(_appliedFrames));
            if (!enabled)
            {
                try
                {
                    if (m != null && m.IsFastForward) m.SetFastForwardingFromUI(false);
                }
                catch
                {
                }
                sb.Append(",\"note\":\"已关闭强制加速\"");
            }
            else if (m == null)
            {
                sb.Append(",\"note\":\"已开启；进入战斗后会自动生效（每帧重申）\"");
            }
            else
            {
                sb.Append(",\"note\":\"已开启，生效中\"");
            }
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>只读的速度诊断（供 bl_battle_status / status 使用）。</summary>
        public static string SpeedJson()
        {
            Mission m = null;
            try
            {
                m = Mission.Current;
            }
            catch
            {
            }
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"forced\":").Append(Jw.B(ForceFastForward));
            sb.Append(",\"missionActive\":").Append(Jw.B(m != null));
            sb.Append(",\"isFastForward\":").Append(Jw.B(IsFastForward(m)));
            sb.Append(",\"sceneTimeSpeed\":").Append(Jw.N(SceneTimeSpeed(m)));
            sb.Append(",\"missionMode\":").Append(Jw.N(MissionMode(m)));
            sb.Append(",\"appliedFrames\":").Append(Jw.N(_appliedFrames));
            sb.Append('}');
            return sb.ToString();
        }
    }
}
