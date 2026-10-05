using System;
using System.Text;
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    /// <summary>
    /// 战斗时间控制（v0.8.34 起支持四挡）。
    ///
    /// **四挡的实现通道**（引擎只给了两条，没有第三条）：
    ///
    /// | 档位 | 通道 | 说明 |
    /// |---|---|---|
    /// | 1x | 两条都关 | 原速 |
    /// | 2x / 5x | `Mission.FixedDeltaTimeMode` + `FixedDeltaTime` | 每帧把"模拟步长"设成**真实帧时间 × N**；<br>`Mission.cs:1279/1281` 两个属性都是 `public ... { get; set; }` |
    /// | 10x | `Mission.SetFastForwardingFromUI(bool)` | 引擎官方快进，倍率写死在 `MissionState`（=10，实测 ~9.3 倍） |
    ///
    /// ⚠️ **为什么 2x/5x 必须走 FixedDeltaTime**：`SetFastForwardingFromUI` 是**纯 bool**、倍率写死，
    /// 做不出中间档；另一条 `TimeRequest`（`Mission.AddTimeSpeedRequest`）只接受 ≤1，**只能做慢动作**。
    /// `MissionState.cs:155-158` 是 `num = FixedDeltaTimeMode ? FixedDeltaTime : 真实dt`，再 `num *= timeSpeed`
    /// ⇒ 设成真实 dt 的 N 倍就是 N 倍速（**换的是步长，不是加子步**，见下方风险）。
    ///
    /// ⚠️ **真实 dt 必须来自 `SubModule.OnApplicationTick`**：mission tick 收到的 dt 在
    /// `FixedDeltaTimeMode` 下已经被替换成 `FixedDeltaTime` 了，拿它去乘 N 会**正反馈爆炸**（×2→×4→×8…）。
    /// 所以由 `OnApplicationTick` 把应用级帧时间写进 <see cref="RealDt"/>。
    ///
    /// ⚠️ **风险（真机验证项）**：官方 10x 的做法是"每帧多跑 9 次 0.1s 的小子 tick"（步长仍小），
    /// 而 FixedDeltaTime 是"一次推进大 dt" ⇒ 物理/AI 步长变大。若真机上观察到穿透、判定异常或卡顿，
    /// 把 <see cref="UseFixedDelta"/> 置 false 即**自动降级为"只有 1x/10x"**（2x/5x 档退化成 10x），
    /// 不需要改别处。
    ///
    /// ⚠️ **不要**去写 `Mission.Scene.TimeSpeed`：它每帧被 `Mission.UpdateSceneTimeSpeed()` 覆盖成
    /// `min(1, 请求值)`，写 10 会被立刻打回 1。
    /// </summary>
    internal static class TimeControl
    {
        /// <summary>目标倍速：1 / 2 / 5 / 10（其它值一律按 1 处理）。由按键或命令设置。</summary>
        public static volatile int TargetSpeed = 1;

        /// <summary>
        /// 2x/5x 是否走 `FixedDeltaTime` 通道。置 false = 自动降级成只有 1x/10x
        /// （2x/5x 档会按 10x 执行），用于真机上发现大步长副作用时的一键回退。
        /// </summary>
        public static volatile bool UseFixedDelta = true;

        /// <summary>
        /// 应用级真实帧时间（秒），由 `SubModule.OnApplicationTick` 每帧写入。
        /// **不能用 mission tick 的 dt**（在 FixedDeltaTimeMode 下它已被替换，会正反馈）。
        /// </summary>
        public static volatile float RealDt;

        /// <summary>
        /// 本场是否被用户显式干预过（游戏内按键，或 `fast_forward` 命令）。
        ///
        /// 为什么需要它：AI 推演（bridge mission）默认"一律 10 倍速" ——
        /// `ScenarioProbe.ApplyFastForward` 每秒把档位重申为 10，那个重申会**覆盖**用户的减速动作。
        /// 所以：用户一旦干预过，本场就不再自动重申，以用户为准。
        ///
        /// 生命周期：每场结束由 `HotkeyBehavior.OnEndMission` 复位为 false
        /// （静态标志不跨场 —— 本项目踩过"标志污染下一场"的坑）。
        /// </summary>
        public static volatile bool UserOverride;

        private static int _appliedFrames;

        /// <summary>档位的中文/短名（日志与回读用）。</summary>
        public static string SpeedName(int sp)
        {
            return sp >= 10 ? "10x" : (sp == 5 ? "5x" : (sp == 2 ? "2x" : "1x"));
        }

        /// <summary>把档位规范化到 {1,2,5,10}。</summary>
        public static int ClampSpeed(int sp)
        {
            if (sp >= 10) return 10;
            if (sp >= 5) return 5;
            if (sp >= 2) return 2;
            return 1;
        }

        /// <summary>必须在主线程、且在战斗内调用（每帧一次，开销可忽略）。</summary>
        public static void Apply(Mission mission)
        {
            if (mission == null) return;
            try
            {
                int sp = ClampSpeed(TargetSpeed);

                if (sp >= 10)
                {
                    // 官方快进通道（bool，引擎写死 10x）
                    if (mission.FixedDeltaTimeMode) mission.FixedDeltaTimeMode = false;
                    if (!mission.IsFastForward) mission.SetFastForwardingFromUI(true);
                    _appliedFrames++;
                }
                else if (sp > 1)
                {
                    float rd = RealDt;
                    if (UseFixedDelta && rd > 0f)
                    {
                        // 2x / 5x：步长 = 真实帧时间 × N
                        if (mission.IsFastForward) mission.SetFastForwardingFromUI(false);
                        mission.FixedDeltaTime = rd * sp;
                        mission.FixedDeltaTimeMode = true;
                        _appliedFrames++;
                    }
                    else
                    {
                        // 降级：只有 1x/10x 可用时，2x/5x 按 10x 执行（绝不静默留个假档位）
                        if (mission.FixedDeltaTimeMode) mission.FixedDeltaTimeMode = false;
                        if (!mission.IsFastForward) mission.SetFastForwardingFromUI(true);
                        _appliedFrames++;
                    }
                }
                else if (UserOverride)
                {
                    // 1x 且在"用户接管"状态下才主动关 —— 未干预时绝不碰，
                    // 免得踩掉原版"主角阵亡后按 F"那条通道。
                    if (mission.FixedDeltaTimeMode) mission.FixedDeltaTimeMode = false;
                    if (mission.IsFastForward) mission.SetFastForwardingFromUI(false);
                }
            }
            catch
            {
            }
        }

        /// <summary>离开战斗/切档时做一次清理（Apply 只保证"开"，这里负责"关干净"）。</summary>
        public static void Restore(Mission m)
        {
            try
            {
                if (m == null) return;
                if (m.FixedDeltaTimeMode) m.FixedDeltaTimeMode = false;
                if (m.IsFastForward) m.SetFastForwardingFromUI(false);
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

        public static bool FixedDeltaOn(Mission m)
        {
            try
            {
                return m != null && m.FixedDeltaTimeMode;
            }
            catch
            {
                return false;
            }
        }

        public static float FixedDelta(Mission m)
        {
            try
            {
                return m != null ? m.FixedDeltaTime : -1f;
            }
            catch
            {
                return -1f;
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
            // v0.8.33：命令也算"用户显式干预"⇒ AI 推演的每秒重申不再把它覆盖回去。
            UserOverride = true;
            if (!enabled) _appliedFrames = 0;
            // v0.8.34：兼容老语义 —— enabled 只表示 1x / 10x 两态。
            TargetSpeed = enabled ? 10 : 1;

            Mission m = null;
            try
            {
                m = Mission.Current;
            }
            catch
            {
            }

            if (enabled)
            {
                Apply(m);
            }
            else
            {
                Restore(m);
            }

            StringBuilder sb = new StringBuilder();
            sb.Append("{\"targetSpeed\":").Append(Jw.N(TargetSpeed));
            sb.Append(",\"speedName\":").Append(Protocol.Q(SpeedName(TargetSpeed)));
            sb.Append(",\"userOverride\":").Append(Jw.B(UserOverride));
            sb.Append(",\"useFixedDelta\":").Append(Jw.B(UseFixedDelta));
            sb.Append(",\"missionActive\":").Append(Jw.B(m != null));
            sb.Append(",\"isFastForward\":").Append(Jw.B(IsFastForward(m)));
            sb.Append(",\"fixedDeltaMode\":").Append(Jw.B(FixedDeltaOn(m)));
            sb.Append(",\"fixedDelta\":").Append(Jw.N(FixedDelta(m)));
            sb.Append(",\"sceneTimeSpeed\":").Append(Jw.N(SceneTimeSpeed(m)));
            sb.Append(",\"missionMode\":").Append(Jw.N(MissionMode(m)));
            sb.Append(",\"appliedFrames\":").Append(Jw.N(_appliedFrames));
            if (!enabled)
            {
                sb.Append(",\"note\":\"已恢复原速（1x）\"");
            }
            else if (m == null)
            {
                sb.Append(",\"note\":\"已设为 10x；进入战斗后自动生效（每帧重申）\"");
            }
            else
            {
                sb.Append(",\"note\":\"已设为 10x，生效中\"");
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
            sb.Append("{\"targetSpeed\":").Append(Jw.N(TargetSpeed));
            sb.Append(",\"speedName\":").Append(Protocol.Q(SpeedName(TargetSpeed)));
            sb.Append(",\"userOverride\":").Append(Jw.B(UserOverride));
            sb.Append(",\"useFixedDelta\":").Append(Jw.B(UseFixedDelta));
            sb.Append(",\"realDt\":").Append(Jw.N(RealDt));
            sb.Append(",\"missionActive\":").Append(Jw.B(m != null));
            sb.Append(",\"isFastForward\":").Append(Jw.B(IsFastForward(m)));
            sb.Append(",\"fixedDeltaMode\":").Append(Jw.B(FixedDeltaOn(m)));
            sb.Append(",\"fixedDelta\":").Append(Jw.N(FixedDelta(m)));
            sb.Append(",\"sceneTimeSpeed\":").Append(Jw.N(SceneTimeSpeed(m)));
            sb.Append(",\"missionMode\":").Append(Jw.N(MissionMode(m)));
            sb.Append(",\"appliedFrames\":").Append(Jw.N(_appliedFrames));
            sb.Append('}');
            return sb.ToString();
        }
    }
}
