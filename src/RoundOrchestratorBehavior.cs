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
    ///   4. 重生两队：给了 `*Squads` 的一方**按组**重生（每组按 `SquadSpec.Count`，第 i 组基点
    ///      沿 x 平移 `i * 12f`），没给的一方沿用旧单值路径（`Mission.SpawnAgent` + `AgentBuildData`）
    ///   5. 重申战术：给了 `*Squads` 的一方**按组**重申 movement（复用 `ScenarioProbe.ApplyOrders`），
    ///      没给的一方沿用旧 TacticCharge（对称化口径不变）
    ///
    /// ⚠️ 多轮模式下**不能挂 `AgentVictoryLogic`** —— 它会在"一方全灭"时结束 mission，
    /// 而多轮恰恰要在一方全灭后继续。见 `ScenarioRunner.CreateBehaviors`。
    ///
    /// ⚠️ **第 2 轮起重生的 agent 没有 `IAgentOriginBase`**：重生走 `Mission.SpawnAgent`，
    ///    不经 `IMissionTroopSupplier` ⇒ per-group combatant / 组级 supplier 只覆盖**第 1 轮**。
    ///    因此 `squad` / `spawned` 一类统计**必须从 `Team.ActiveAgents` 侧数**（T7 的口径，
    ///    见 `task-6-report.md` §2.1）。
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

        // ── T6：多轮按组重生 / 每轮按组重申（null = 旧单值路径，GC2）──────────────
        /// <summary>该方的组规格；null 或空 ⇒ 该方按单值 AttackerChar/AttackerCount 重生。</summary>
        internal static List<SquadSpec> AttackerSquads = null;
        internal static List<SquadSpec> DefenderSquads = null;
        /// <summary>与上面 Squads 下标一一对应的兵种（= Start() 校验阶段 Resolve 的结果）。</summary>
        internal static List<BasicCharacterObject> AttackerSquadTroops = null;
        internal static List<BasicCharacterObject> DefenderSquadTroops = null;

        internal static bool Enabled
        {
            get { return Rounds > 1; }
        }

        /// <summary>
        /// **轮内时钟**（秒）。每轮在 `Advance` 里换轮次时归零。
        ///
        /// 为什么必须是轮内而不是整场：`RoundLog` 写出的每条事件都带 `round` 字段，
        /// 语义上属于**该轮**；若 time 用整场累计值，则同一份日志（每轮一个文件）里
        /// `round_start` / `round_all_done` 会落在该文件遥测主时钟区间之外。
        /// 实测（battle_20260924_220146_854.jsonl）：遥测主时钟 0.01→155.70，
        /// 而 round_all_done 落在 300.45 —— 下游按 time 切窗口会静默算错。
        /// 这与 `TelemetryBehavior.BeginNewRound` 里的 `_elapsed = 0f` 保持同一语义。
        /// </summary>
        private float _elapsed;
        private float _sinceCheck;
        private int _round = 1;
        private int _cleanupDeaths;
        private bool _finished;

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
            // 轮内时钟归零：必须与下面 tb.BeginNewRound 同步（遥测侧在同一调用里
            // 把自己那份 _elapsed 归零）。两处共用一个原点，日志才对得上。
            // 放在 _round++ 之后、RoundLog("round_start") 之前 —— 让 round_start 的
            // time 从 0 起算，与该轮遥测的 time 同原点。
            _elapsed = 0f;
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
            // v0.8.10：可用性探针的**轮内时钟**也要在同一处归零 —— 它是仓库里第三个时钟，
            // v0.8.9 时漏了（第 2 轮起 probe 事件的 time 仍是整场累计值，与同文件其余事件不同源，
            // 外部审查 B2 用真实产物实测发现）。探针是临时的，取不到就跳过。
            try
            {
                ScoreHitProbeBehavior sp = m.GetMissionBehavior<ScoreHitProbeBehavior>();
                if (sp != null) sp.BeginNewRound();
            }
            catch
            {
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

            // T6：有组 ⇒ 逐组重生；无组 ⇒ 旧单值 Spawn（逐字节不变，GC2）。
            //     两组列表与 troop 列表随 swap 一起互换（与上面 atkCh/defCh 的互换语义一致）。
            List<SquadSpec> atkSquads = swapped ? DefenderSquads : AttackerSquads;
            List<BasicCharacterObject> atkTroops = swapped ? DefenderSquadTroops : AttackerSquadTroops;
            List<SquadSpec> defSquads = swapped ? AttackerSquads : DefenderSquads;
            List<BasicCharacterObject> defTroops = swapped ? AttackerSquadTroops : DefenderSquadTroops;
            // T7：逐组记录**实际重生成功数**，供 squad 事件（source="respawn"）。
            int[] atkSpawned = null;
            int[] defSpawned = null;
            if (HasSquads(atkSquads)) atkSpawned = SpawnGroups(m, m.AttackerTeam, atkSquads, atkTroops, atkPos, true);
            else Spawn(m, m.AttackerTeam, atkCh, atkN, atkPos, true);
            if (HasSquads(defSquads)) defSpawned = SpawnGroups(m, m.DefenderTeam, defSquads, defTroops, defPos, false);
            else Spawn(m, m.DefenderTeam, defCh, defN, defPos, false);

            // 4) 重设战术：与首轮同一口径。有组 ⇒ 按组重申 movement（与重生同组同 swap）；
            //    无组 ⇒ 旧 ApplyCharge 原样（GC2）。
            ApplyRoundOrders(m.AttackerTeam, atkSquads);
            ApplyRoundOrders(m.DefenderTeam, defSquads);

            // 5) T7：重生完成后写两侧的 squad 事件（source="respawn"，spawned = 实际成功次数）。
            //    无组的一方 specs 为 null ⇒ 该侧不写（GC2）。
            TelemetryBehavior.WriteSquadEvents(_round, "respawn",
                atkSquads, atkTroops, atkSpawned, defSquads, defTroops, defSpawned);
        }

        /// <summary>该方是否有可用的组规格（null / 空 ⇒ 走旧单值路径）。</summary>
        private static bool HasSquads(List<SquadSpec> specs)
        {
            return specs != null && specs.Count > 0;
        }

        /// <summary>
        /// T6：按组重生。第 i 组的基点 = 该方基点**沿 x 平移 i*12f**（y/z 不变；brief §3.2 定死的口径）。
        /// 基点无值 ⇒ 传 null（沿用引擎默认，与旧路径一致）。组内铺开仍由 Spawn() 自身完成。
        /// 防御分支：`specs[i]` / 对应 troop 缺失时**不静默** —— 写一条 `round_spawn_group_skipped`
        /// 事件（含 round / 第几组 / 原因）后跳过该组，绝不无痕少一组（GC3；Start() 已保证正常路径不可达）。
        /// </summary>
        private int[] SpawnGroups(Mission m, Team team, List<SquadSpec> specs,
            List<BasicCharacterObject> troops, Vec3? basePos, bool attackerSide)
        {
            if (!HasSquads(specs)) return null;
            // T7：逐组记录**实际 SpawnAgent 成功次数**，作为该轮 squad 事件的 spawned（source="respawn"）。
            int[] spawned = new int[specs.Count];
            for (int i = 0; i < specs.Count; i++)
            {
                SquadSpec s = specs[i];
                if (s == null)
                {
                    RoundLogGroupSkip(i + 1, "spec 为空");
                    continue;
                }
                BasicCharacterObject troop = (troops != null && i < troops.Count) ? troops[i] : null;
                if (troop == null)
                {
                    // troops 与 specs 本应一一对应（Start() 保证）；缺失 = 防御分支，必须留痕。
                    RoundLogGroupSkip(i + 1, "troop 缺失（Squads 与 SquadTroops 下标应一一对应）");
                    continue;
                }
                Vec3? pos = null;
                if (basePos.HasValue)
                {
                    Vec3 p = basePos.Value;
                    p.x = basePos.Value.x + (float)i * 12f;   // 只改 x：y/z 与基点相同
                    pos = p;
                }
                spawned[i] = Spawn(m, team, troop, s.Count, pos, attackerSide);
            }
            return spawned;
        }

        /// <summary>
        /// T6 · 修复轮 1 · minor3：按组重生"跳过一组"的可观测事件（绝不静默）。
        /// group1based 用 1 基编号（与错误消息"第 N 组"一致）。
        /// </summary>
        private void RoundLogGroupSkip(int group1based, string reason)
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"round_spawn_group_skipped\"");
                sb.Append(",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"round\":").Append(Jw.N(_round));
                sb.Append(",\"rounds\":").Append(Jw.N(Rounds));
                sb.Append(",\"group\":").Append(Jw.N(group1based));
                sb.Append(",\"reason\":").Append(Protocol.Q(reason));
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }

        /// <summary>
        /// T6：每轮重申命令。有组 ⇒ 复用 T5 的 ScenarioProbe.ApplyOrders（按"该组兵种的实际编队"
        /// 下发，内部已处理落点错误记录）；无组 ⇒ 旧 ApplyCharge 原样（GC2）。
        /// </summary>
        private static void ApplyRoundOrders(Team team, List<SquadSpec> specs)
        {
            if (team == null) return;
            if (HasSquads(specs))
            {
                ScenarioRunner.ScenarioProbe.ApplyOrders(team, specs);
                return;
            }
            ApplyCharge(team);
        }

        /// <summary>
        /// 单值 / 单组重生。⚠️ 走 `Mission.SpawnAgent` ⇒ 生成出的 agent **没有 `IAgentOriginBase`**：
        /// 它不在 `IMissionTroopSupplier` 的口径里（第 2 轮起尤其如此，见类注释与 `task-6-report.md` §2.1）。
        /// </summary>
        private static int Spawn(Mission m, Team team, BasicCharacterObject ch, int count, Vec3? pos, bool attackerSide)
        {
            if (m == null || team == null || ch == null || count <= 0) return 0;
            int spawned = 0;
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
                    Agent a = m.SpawnAgent(data);
                    if (a != null) spawned++;
                }
                catch
                {
                }
            }
            return spawned;
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
            AttackerSquads = null;
            DefenderSquads = null;
            AttackerSquadTroops = null;
            DefenderSquadTroops = null;
        }
    }
}
