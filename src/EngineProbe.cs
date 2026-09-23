using System;
using System.Text;
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    /// <summary>
    /// 引擎/任务推进探针（对照 Coop 的 `render-status`：engineFrame 推进 + rendererFps &gt; 0 +
    /// topScreen/activeState 两次相同 = "画面活着且稳定"）。
    ///
    /// 为什么必须有它：只看 `state == running` 无法区分"真的在打"和"卡在黑屏/加载界面"。
    /// 这种样本如果混进 A/B 对比，结论就是错的（而它看起来一切正常）。
    ///
    /// 我们用的三个信号（都是从引擎里读的真实值，不是自报）：
    ///   1. `Mission.CurrentTime`（Mission.cs:1185 `_cachedMissionTime`）—— 任务时间；
    ///      暂停、卡住、加载中时**停止推进**，这是最硬的判据；
    ///   2. 我们自己的 mission tick 计数（引擎每帧调 OnMissionTick → 主线程真的在转）；
    ///   3. 墙钟时间 + `MBCommon.IsPaused`（MBCommon.cs:43）。
    ///
    /// 判据：墙钟在走 + 任务时间在走 = advancing；
    ///       墙钟走而任务时间 2 秒没动 = stalled（该样本高度可疑）。
    /// </summary>
    internal static class EngineProbe
    {
        private static long _ticks;
        private static long _advances;
        private static double _startedWall;
        private static float _startMissionTime;
        private static float _lastMissionTime = -1f;
        private static double _lastAdvanceWall;
        private static long _maxStallMs;
        private static float _maxStallAtMissionTime = -1f;
        private static bool _hadMission;

        private static double WallNow()
        {
            return (double)DateTime.UtcNow.Ticks / TimeSpan.TicksPerSecond;
        }

        private static float MissionTimeOf(Mission m)
        {
            try
            {
                return m != null ? m.CurrentTime : 0f;
            }
            catch
            {
                return 0f;
            }
        }

        public static bool IsPaused
        {
            get
            {
                try
                {
                    return MBCommon.IsPaused;
                }
                catch
                {
                    return false;
                }
            }
        }

        /// <summary>必须在主线程、每个 mission tick 调一次（放在遥测开关判断之前）。</summary>
        public static void OnTick(Mission mission)
        {
            if (mission == null) return;
            try
            {
                _ticks++;
                float mt = MissionTimeOf(mission);
                double now = WallNow();

                if (!_hadMission)
                {
                    _hadMission = true;
                    _startedWall = now;
                    _startMissionTime = mt;
                    _lastMissionTime = mt;
                    _lastAdvanceWall = now;
                    return;
                }

                if (mt > _lastMissionTime + 0.0005f)
                {
                    _advances++;
                    _lastMissionTime = mt;
                    double stall = (now - _lastAdvanceWall) * 1000.0;
                    if (stall > _maxStallMs)
                    {
                        _maxStallMs = (long)stall;
                        _maxStallAtMissionTime = mt;
                    }
                    _lastAdvanceWall = now;
                }
            }
            catch
            {
            }
        }

        /// <summary>新 mission 开始时重置（否则上一场的 tk 计数会串场）。</summary>
        public static void Reset()
        {
            _ticks = 0;
            _advances = 0;
            _startedWall = 0;
            _startMissionTime = 0f;
            _lastMissionTime = -1f;
            _lastAdvanceWall = 0;
            _maxStallMs = 0;
            _maxStallAtMissionTime = -1f;
            _hadMission = false;
        }

        public static double WallSeconds
        {
            get { return _hadMission ? (WallNow() - _startedWall) : 0.0; }
        }

        public static double StalledSeconds
        {
            get { return _hadMission ? (WallNow() - _lastAdvanceWall) : 0.0; }
        }

        public static bool IsAdvancing
        {
            get { return _hadMission && ProbePolicy.IsAdvancing(StalledSeconds); }
        }

        public static long Ticks
        {
            get { return _ticks; }
        }

        public static long MaxStallMs
        {
            get { return _maxStallMs; }
        }

        /// <summary>给 status / speed 用的实时块。</summary>
        public static string Json(Mission mission)
        {
            bool active = mission != null;
            double wall = WallSeconds;
            double ticksPerSec = wall > 0.5 ? (_ticks / wall) : 0.0;
            string verdict = ProbePolicy.RealtimeVerdict(active, active && IsAdvancing);

            StringBuilder sb = new StringBuilder();
            sb.Append("{\"missionActive\":").Append(Jw.B(active));
            sb.Append(",\"verdict\":\"").Append(verdict).Append('"');
            sb.Append(",\"advancing\":").Append(Jw.B(active && IsAdvancing));
            sb.Append(",\"ticks\":").Append(Jw.N(_ticks));
            sb.Append(",\"ticksPerSecond\":").Append(Jw.N((float)ticksPerSec));
            sb.Append(",\"wallSeconds\":").Append(Jw.N((float)wall));
            sb.Append(",\"missionTime\":").Append(Jw.N(MissionTimeOf(mission)));
            sb.Append(",\"stalledSeconds\":").Append(Jw.N((float)StalledSeconds));
            sb.Append(",\"maxStallMs\":").Append(Jw.N(_maxStallMs));
            sb.Append(",\"paused\":").Append(Jw.B(IsPaused));
            if (active)
            {
                try
                {
                    sb.Append(",\"missionMode\":").Append(Jw.N((int)mission.Mode));
                    sb.Append(",\"missionState\":").Append(Jw.N((int)mission.CurrentState));
                }
                catch
                {
                }
            }
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>战斗结束时的样本有效性汇总（写进 end 事件，供分析器判定样本能不能用）。</summary>
        public static string EndValidityJson()
        {
            double wall = WallSeconds;
            double ticksPerSec = wall > 0.5 ? (_ticks / wall) : 0.0;
            string verdict = ProbePolicy.SampleVerdict(_hadMission, _ticks, ticksPerSec, _maxStallMs);

            StringBuilder sb = new StringBuilder();
            sb.Append("{\"verdict\":\"").Append(verdict).Append('"');
            sb.Append(",\"ticks\":").Append(Jw.N(_ticks));
            sb.Append(",\"ticksPerSecond\":").Append(Jw.N((float)ticksPerSec));
            sb.Append(",\"wallSeconds\":").Append(Jw.N((float)wall));
            sb.Append(",\"maxStallMs\":").Append(Jw.N(_maxStallMs));
            sb.Append(",\"maxStallAtMissionTime\":").Append(Jw.N(_maxStallAtMissionTime));
            sb.Append(",\"advances\":").Append(Jw.N(_advances));
            sb.Append('}');
            return sb.ToString();
        }
    }
}
