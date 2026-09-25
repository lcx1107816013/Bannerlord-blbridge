// BlBridge · OnScoreHit 可用性探针（只读，不改任何游戏状态）
//
// 目的：**实测**验证 MissionBehavior.OnScoreHit 在「无玩家的 AI vs AI 对局」里是否触发，
//       以及它与 OnAgentHit 是否真的成对出现（即 Mission.cs:5612-5616 那个同一 foreach）。
//
// 为什么值得单独做一个探针：
//   * 读码结论是"必然可用"（同一 foreach 相邻两行、零条件）。但读码结论需要一次实测钉死，
//     因为本项目已经踩过两次"读码看似成立、实际不成立"的坑：
//       - CanLogCombatFor = !IsAIControlled ⇒ 引擎自带战斗日志对 AI 对局完全失效
//       - Mission.DisableDying ⇒ 既清零扣血、又让 OnAgentHit 完全不触发
//   * 探针**只计数**，不改血量、不改 AI、不改任何游戏状态；不启用时零影响。
//
// 判据（跑完一局看 probe 事件）：
//   scoreCalls == hitCalls  → OnScoreHit 与 OnAgentHit 配平 ⇒ 确认可用（正是我们想要的）
//   scoreCalls == 0         → 该回调对 AI 对局不可用 ⇒ 整个方案作废（及早知道）
//   blocked / nonZeroDhp    → 顺带确认 isBlocked 与 damagedHp 的实际语义
//
// 输出：复用遥测已打开的同一个 JSONL（Jw 是全局单例写入器，JsonlWriter.cs:12），
//       每 10 秒写一条 t="probe" 快照，结束时写一条 t="probe_end"。

using System.Text;
using TaleWorlds.Core;
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    public class ScoreHitProbeBehavior : MissionBehavior
    {
        public override MissionBehaviorType BehaviorType
        {
            get { return MissionBehaviorType.Other; }
        }

        /// <summary>
        /// 轮内时钟（秒）—— **每轮归零**，见 BeginNewRound。
        ///
        /// 缺陷背景（v0.8.10，外部审查 B2）：本字段曾从 mission 开始一直累加、全仓库无任何归零点，
        /// 而 `Jw` 每轮换一个日志文件 ⇒ 第 2 轮起 `probe` 事件带的是**整场**时钟，
        /// 与同文件其余事件的**轮内**时钟不同源（实测 battle_20260925_111509_578.jsonl：
        /// probe 230.39–310.42，而同文件其余事件上界只有 91.43）。
        /// 第 1 轮"看不出来"是同一缺陷的另一面：该轮整场时钟 ≡ 轮内时钟 ⇒ 单轮验证必然漏掉它。
        ///
        /// 同类实现的代码内对照：`TelemetryBehavior._elapsed` ✓ 归零、
        /// `RoundOrchestratorBehavior._elapsed` ✓ 归零、本字段（v0.8.9）✗ —— 本次补齐。
        /// </summary>
        private float _elapsed;
        private float _nextReportAt = 10f;

        private int _hitCalls;
        private int _scoreCalls;
        private int _blocked;
        private int _nonZeroDhp;
        private float _dhpSum;
        private int _missile;
        private int _siegeHit;
        private float _distSum;
        private int _distCount;
        private float _sdSum;
        private int _sdCount;
        private int _blockedButPositive;   // isBlocked==true 却 damagedHp>0 —— 语义矛盾，应为 0

        /// <summary>
        /// 换轮：**轮内时钟归零**。由 `RoundOrchestratorBehavior.Advance` 在调用
        /// `TelemetryBehavior.BeginNewRound` 的同一处调用 —— 三个时钟共用一个原点，日志才对得上。
        ///
        /// 注意：下面那些计数（`_hitCalls` / `_scoreCalls` / …）**刻意不归零** ——
        /// 本探针的判据是"整场 OnScoreHit 与 OnAgentHit 是否配平"，跨轮累计正是它要的语义。
        /// 归零的只是 time 的原点。
        /// </summary>
        internal void BeginNewRound()
        {
            _elapsed = 0f;
            _nextReportAt = 10f;
        }

        public override void OnMissionTick(float dt)
        {
            base.OnMissionTick(dt);
            _elapsed += dt;
            if (_elapsed >= _nextReportAt)
            {
                _nextReportAt = _elapsed + 10f;
                Report("probe");
            }
        }

        /// <summary>对照组：本回调已由 15 场实测验证可用。</summary>
        public override void OnAgentHit(Agent affectedAgent, Agent affectorAgent, in MissionWeapon affectorWeapon,
            in Blow blow, in AttackCollisionData attackCollisionData)
        {
            base.OnAgentHit(affectedAgent, affectorAgent, in affectorWeapon, in blow, in attackCollisionData);
            _hitCalls++;
        }

        /// <summary>被测对象：是否触发？与 OnAgentHit 是否配平？isBlocked/damagedHp 语义如何？</summary>
        public override void OnScoreHit(Agent affectedAgent, Agent affectorAgent, WeaponComponentData attackerWeapon,
            bool isBlocked, bool isSiegeEngineHit, in Blow blow, in AttackCollisionData collisionData,
            float damagedHp, float hitDistance, float shotDifficulty)
        {
            base.OnScoreHit(affectedAgent, affectorAgent, attackerWeapon, isBlocked, isSiegeEngineHit, in blow,
                in collisionData, damagedHp, hitDistance, shotDifficulty);
            try
            {
                _scoreCalls++;
                if (isBlocked) _blocked++;
                if (damagedHp > 0f) _nonZeroDhp++;
                _dhpSum += damagedHp;
                if (blow.IsMissile)
                {
                    _missile++;
                    if (hitDistance > 0f)
                    {
                        _distSum += hitDistance;
                        _distCount++;
                    }
                }
                if (shotDifficulty >= 0f)
                {
                    _sdSum += shotDifficulty;
                    _sdCount++;
                }
                if (isSiegeEngineHit) _siegeHit++;
                // "被挡下"与"有实际扣血"应当互斥
                if (isBlocked && damagedHp > 0f) _blockedButPositive++;
            }
            catch
            {
            }
        }

        // MissionBehavior.OnEndMission() 是 protected（写成 public override 会报 CS0507）
        protected override void OnEndMission()
        {
            base.OnEndMission();
            Report("probe_end");
        }

        private void Report(string type)
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"").Append(type).Append('"');
                sb.Append(",\"time\":").Append(Jw.N(_elapsed));
                // 判据 1：是否配平
                sb.Append(",\"hitCalls\":").Append(Jw.N(_hitCalls));
                sb.Append(",\"scoreCalls\":").Append(Jw.N(_scoreCalls));
                // 判据 2：语义
                sb.Append(",\"blocked\":").Append(Jw.N(_blocked));
                sb.Append(",\"nonZeroDhp\":").Append(Jw.N(_nonZeroDhp));
                sb.Append(",\"dhpSum\":").Append(Jw.N(_dhpSum));
                sb.Append(",\"missile\":").Append(Jw.N(_missile));
                sb.Append(",\"siegeHit\":").Append(Jw.N(_siegeHit));
                sb.Append(",\"blockedButPositive\":").Append(Jw.N(_blockedButPositive));
                if (_distCount > 0)
                {
                    sb.Append(",\"missileDistAvg\":").Append(Jw.N(_distSum / _distCount));
                }
                if (_sdCount > 0)
                {
                    sb.Append(",\"shotDiffAvg\":").Append(Jw.N(_sdSum / _sdCount));
                }
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }
    }
}
