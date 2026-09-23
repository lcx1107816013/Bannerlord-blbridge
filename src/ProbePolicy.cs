namespace BlBridge
{
    /// <summary>
    /// 推进探针的判定规则（纯逻辑、只依赖 System，因此能离线单测）。
    ///
    /// 与 EngineProbe 分离的原因：**判定规则才是最容易写错、也最需要被测的部分**，
    /// 而 EngineProbe 依赖 TaleWorlds（Mission/MBCommon），没法脱离游戏编译。
    /// 阈值都写在这里，且每个都有来历：
    ///   StallThresholdSeconds = 2.0   任务时间 2 秒没动就算停住（正常战斗每帧都动）
    ///   MinHealthyTicksPerSecond = 10 每秒不到 10 帧 ⇒ 这场基本没在跑（10x 加速下远高于此值）
    ///   MaxHealthyStallMs = 5000      全场最长一次卡顿超过 5 秒 ⇒ 样本可疑
    ///   MinHealthyTicks = 30          总共不到 30 帧 ⇒ 根本没打起来
    /// </summary>
    internal static class ProbePolicy
    {
        public const double StallThresholdSeconds = 2.0;
        public const double MinHealthyTicksPerSecond = 10.0;
        public const long MaxHealthyStallMs = 5000;
        public const long MinHealthyTicks = 30;

        /// <summary>任务时间是否仍在推进（实时判定）。</summary>
        public static bool IsAdvancing(double stalledSeconds)
        {
            return stalledSeconds < StallThresholdSeconds;
        }

        /// <summary>实时结论（写进 status/speed）。</summary>
        public static string RealtimeVerdict(bool missionActive, bool advancing)
        {
            if (!missionActive) return "no_mission";
            return advancing ? "advancing" : "stalled";
        }

        /// <summary>样本有效性结论（写进 end 事件，决定该场能不能进 A/B 对比）。</summary>
        public static string SampleVerdict(bool hadMission, long ticks, double ticksPerSecond, long maxStallMs)
        {
            if (!hadMission) return "no_probe";
            if (ticks < MinHealthyTicks) return "suspect";
            if (ticksPerSecond < MinHealthyTicksPerSecond) return "suspect";
            if (maxStallMs > MaxHealthyStallMs) return "suspect";
            return "ok";
        }
    }
}
