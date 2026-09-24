using System.Collections.Generic;
using System.Text;
using TaleWorlds.Core;
using TaleWorlds.Library;
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    /// <summary>
    /// v0.8.5 · **多轮连续实验**：在**同一个 mission 内**跑 N 轮对照，省掉每轮的场景加载/卸载。
    ///
    /// 动机（学自 OpenRA-RL v2 的"降低重置开销"）：现在每场都 `MissionState.OpenNew`
    /// ⇒ 每场约 3~6 秒花在加载/卸载上，而且**每轮的运行环境（地形加载、光照、初始站位、
    /// AI 状态）都是新的** —— 跨轮比较混着环境差异。同 mission 连跑把这一层消掉。
    ///
    /// 每轮结束（某方存活 ≤ `EndAlive`）后：
    ///   1. 写一条 `round_cleanup`（含清场人数）—— **补刀死的样本要在分析时排除**：
    ///      它们的死亡不是"自然战死"，与立项口径（到死挨几箭）不是同一个总体。
    ///      这跟刚修完的「死因偏差」是同一类坑，所以必须留标记、不靠猜。
    ///   2. 残兵就地死亡（`Agent.Die`），保持战场清洁
    ///   3. 递增轮次 → 通知遥测**换一个日志文件**（每轮独立、零污染）
    ///   4. 按配置重生两队（`Mission.SpawnAgent` + `AgentBuildData`，可指定进场位置）
    ///   5. 重设双方战术为 TacticCharge（与首轮对称化口径一致）
    ///
    /// ⚠️ 多轮模式下**不能挂 `AgentVictoryLogic`** —— 它会在"一方全灭"时结束 mission，
    /// 而多轮恰恰要在一方全灭后继续。见 `ScenarioRunner.CreateBehaviors`。
    ///
    /// 零 Harmony：全部走公开 API（`Mission.SpawnAgent` / `AgentBuildData` / `Agent.Die`）。
    /// </summary>
    public class RoundOrchestratorBehavior : MissionBehavior
    {
        public override MissionBehaviorType BehaviorType
        {
            get { return MissionBehaviorType.Other; }
        }

        // ── 由 ScenarioRunner 在开战前设置 ──────────────────────────────
        /// <summary>总轮数（1 = 不启用多轮，行为空转）。</summary>
        internal static int Rounds = 1;
        /// <summary>某方存活 ≤ 此值即判定本轮结束。</summary>
        internal static int EndAlive = 1;
        /// <summary>是否每轮交换攻守（第 2、4…轮把原守方放到攻方位置）。</summary>
        internal static bool SwapSides = false;
        /// <summary>重生时两队的进场点（null = 用引擎默认生成点）。位置**跟兵种走**，不动 team。</summary>
        internal static Vec3? SpawnAttacker = null;
        internal static Vec3? SpawnDefender = null;
        internal static BasicCharacterObject AttackerChar = null;
        internal static BasicCharacterObject DefenderChar = null;
        internal static int AttackerCount = 0;
        internal static int DefenderCount = 0;

        internal static bool Enabled
        {
            get { return Rounds > 1; }
        }

        private float _elapsed;
        private float _sinceCheck;
        private int _round = 1;
        private int _cleanupDeaths;
        private bool _finished;
        private bool _started;

        public override void OnMissionTick(float dt)
        {
            base.OnMissionTick(dt);
            if (!Enabled || _finished) return;
            _elapsed += dt;
            _sinceCheck += dt;
            if (_sinceCheck < 0.5f) return;       // 每 0.5 秒判一次，够用且不拖帧
            _sinceCheck = 0f;
            try
            {
                Evaluate();
            }
            catch
            {
            }
        }

        private void Evaluate()
        {
            Mission m = Mission.Current;
            if (m == null) return;
            int a = CountAlive(m.AttackerTeam);
            int d = CountAlive(m.DefenderTeam);
            if (a < 0 || d < 0) return;            // 队伍还没建好
            if (a + d == 0) return;                // 还没生成（首轮加载中）
            _started = true;
            if (a > EndAlive && d > EndAlive) return;

            if (_round >= Rounds)
            {
                Finish(m);
                return;
            }
            Advance(m);
        }

        private static int CountAlive(Team t)
        {
            if (t == null) return -1;
            try
            {
                int n = 0;
                foreach (Agent ag in t.ActiveAgents)
                {
                    if (ag != null && ag.IsActive()) n++;
                }
                return n;
            }
            catch
            {
                return -1;
            }
        }

        /// <summary>本轮结束、还有下一轮：清场 → 换轮次 → 重生 → 重设战术。</summary>
        private void Advance(Mission m)
        {
            // 1) 清场：残兵就地死亡。**先记标记**，分析时据此排除这批"补刀死"。
            RoundLog("round_cleanup", _round, -1);
            int killed = 0;
            foreach (Team t in new Team[] { m.AttackerTeam, m.DefenderTeam })
            {
                if (t == null) continue;
                try
                {
                    foreach (Agent ag in t.ActiveAgents)
                    {
                        if (ag == null || !ag.IsActive()) continue;
                        try
                        {
                            ag.Die(default(Blow));
                            killed++;
                        }
                        catch
                        {
                        }
                    }
                }
                catch
                {
                }
            }
            _cleanupDeaths += killed;

            // 2) 换轮次：通知遥测**换一个日志文件**（每轮独立）
            _round++;
            TelemetryBehavior tb = null;
            try
            {
                tb = m.GetMissionBehavior<TelemetryBehavior>();
            }
            catch
            {
            }
            if (tb != null)
            {
                try
                {
                    tb.BeginNewRound(_round);
                }
                catch
                {
                }
            }
            RoundLog("round_start", _round, killed);

            // 3) 重生两队（可交换攻守、可指定进场点）
            bool swapped = SwapSides && (_round % 2 == 0);
            BasicCharacterObject atkCh = swapped ? DefenderChar : AttackerChar;
            BasicCharacterObject defCh = swapped ? AttackerChar : DefenderChar;
            int atkN = swapped ? DefenderCount : AttackerCount;
            int defN = swapped ? AttackerCount : DefenderCount;
            Vec3? atkPos = swapped ? SpawnDefender : SpawnAttacker;
            Vec3? defPos = swapped ? SpawnAttacker : SpawnDefender;
            Spawn(m, m.AttackerTeam, atkCh, atkN, atkPos, true);
            Spawn(m, m.DefenderTeam, defCh, defN, defPos, false);

            // 4) 重设战术：与首轮同一口径（双方对称冲锋）
            ApplyCharge(m.AttackerTeam);
            ApplyCharge(m.DefenderTeam);
        }

        private static void Spawn(Mission m, Team team, BasicCharacterObject ch, int count, Vec3? pos, bool attackerSide)
        {
            if (m == null || team == null || ch == null || count <= 0) return;
            for (int i = 0; i < count; i++)
            {
                try
                {
                    AgentBuildData data = new AgentBuildData(ch)
                        .Team(team)
                        .Controller(AgentControllerType.AI);
                    if (pos.HasValue)
                    {
                        // 在指定点附近铺开，避免整队叠在同一个坐标上
                        Vec3 p = pos.Value;
                        p.x += (i % 5) * 1.5f - 3f;
                        p.y += (i / 5) * 1.5f;
                        data = data.InitialPosition(p)
                                   .InitialDirection(new Vec2(attackerSide ? 1f : -1f, 0f));
                    }
                    m.SpawnAgent(data);
                }
                catch
                {
                }
            }
        }

        private static void ApplyCharge(Team team)
        {
            if (team == null) return;
            try
            {
                team.ClearTacticOptions();
                team.AddTacticOption(new TacticCharge(team));
                foreach (Formation f in team.FormationsIncludingEmpty)
                {
                    if (f == null || f.CountOfUnits == 0) continue;
                    f.SetMovementOrder(MovementOrder.MovementOrderCharge);
                }
            }
            catch
            {
            }
        }

        /// <summary>全部轮次跑完：收尾并结束 mission。</summary>
        private void Finish(Mission m)
        {
            _finished = true;
            RoundLog("round_all_done", _round, _cleanupDeaths);
            try
            {
                m.EndMission();
            }
            catch
            {
            }
        }

        private void RoundLog(string type, int round, int killed)
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"").Append(type).Append('"');
                sb.Append(",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"round\":").Append(Jw.N(round));
                sb.Append(",\"rounds\":").Append(Jw.N(Rounds));
                if (killed >= 0) sb.Append(",\"cleanedUp\":").Append(Jw.N(killed));
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }

        /// <summary>mission 结束：复位全部静态字段（残留会污染玩家之后的手动战斗）。</summary>
        public override void OnRemoveBehavior()
        {
            base.OnRemoveBehavior();
            Rounds = 1;
            EndAlive = 1;
            SwapSides = false;
            SpawnAttacker = null;
            SpawnDefender = null;
            AttackerChar = null;
            DefenderChar = null;
            AttackerCount = 0;
            DefenderCount = 0;
        }
    }
}
