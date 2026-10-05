using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using TaleWorlds.Core;
using TaleWorlds.Library;   // Vec3 / Mat3（射击事件要用）
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    /// <summary>
    /// 战斗遥测核心。只读游戏状态 + 写 JSONL，绝不修改任何游戏数据。
    ///
    /// 产出的一行行 JSON 事件类型：
    ///   meta   战斗开始（schema 版本）
    ///   unit   单位入场（兵种 id、阵营、等级、是否英雄、是否骑乘、最大血量）
    ///   hit    每次命中（攻击者/受击者兵种、武器类别、伤害类型、命中部位、
    ///          本次伤害、原始力度、被护甲吸收、受击后血量/满血）
    ///   kill   每次减员（Killed/Unconscious 等状态）
    ///   flee   单位开始溃逃    panic  单位恐慌
    ///   sample 每 N 秒快照（双方存活数、血量总和）
    ///   end    战斗结束汇总（含 ioError 诊断字段）
    ///
    /// V0.1.1 修复：文件改为**惰性打开**（EnsureOpen）。V0.1.0 依赖 OnBehaviorInitialize，
    /// 而该回调在挂载点太晚时不会被调用，导致"计数器在涨、文件是空的"。
    /// </summary>
    public class TelemetryBehavior : MissionBehavior
    {
        public override MissionBehaviorType BehaviorType
        {
            get { return MissionBehaviorType.Other; }
        }

        private float _elapsed;
        private float _nextSampleAt = BridgeConfig.SampleIntervalSeconds;
        private float _nextStateAt = BridgeConfig.StateIntervalSeconds;
        private int _stateSeq;
        private readonly HashSet<int> _aiDumped = new HashSet<int>();
        private readonly HashSet<int> _equipDumped = new HashSet<int>();
        private int _hitSeq;
        private int _killSeq;
        private int _shotSeq;
        private int _unitSeq;
        private int _fleeSeq;
        private bool _opened;
        private bool _closed;
        private string _ioError;
        private string _file;
        private int _initAttacker = -1;
        private int _initDefender = -1;
        private bool _probeReset;
        private bool _ffLast;
        private int _ffSeq;
        private bool _stallOpen;
        private double _stallOpenedWall;
        private float _stallOpenedMissionTime;

        // ── 事件：mission 生命周期 ────────────────────────────────────────

        public override void OnBehaviorInitialize()
        {
            base.OnBehaviorInitialize();
            EnsureOpen();
        }

        /// <summary>
        /// 惰性打开日志文件。任何事件都先调它 —— 这样"行为初始化回调有没有被调用"
        /// 不再是数据丢失的单点，挂载点顺序变化也不会再丢数据。
        /// </summary>
        private void EnsureOpen()
        {
            if (_opened || _closed) return;
            _opened = true;
            if (!BridgeConfig.Enabled) return;
            try
            {
                BridgeConfig.EnsureDirs();
                // 文件名精确到毫秒；Jw.Open 用 CreateNew，同秒重开也不会覆盖上一场数据
                string preferred = Path.Combine(BridgeConfig.BattlesDir,
                    "battle_" + DateTime.Now.ToString("yyyyMMdd_HHmmss_fff") + ".jsonl");
                _file = Jw.Open(preferred);
                string name = Path.GetFileName(_file);
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"meta\",\"schema\":1,\"mod\":\"").Append(BridgeConfig.ModuleId);
                sb.Append("\",\"version\":\"").Append(BridgeConfig.Version);
                sb.Append("\",\"startedUtc\":\"").Append(Jw.UtcNow());
                // v0.8.3：来源标记 —— BlBridge 自建靶场为 "bridge"，玩家在战役/沙盒里打的仗为 "game"。
                // 没有它，battles/ 目录里"靶场实验"与"玩家实战"混在一起，事后无法分辨。
                // ⚠️ 这一段原是"值不闭合、由下一行补引号"的拼接写法；**数字字段不能吃 `\"` 前缀** ——
                // v0.8.4~0.8.5 因此产出过非法 JSON（`"randomSeed":777"`），meta 被 load_events 静默跳过。
                // 现在每行自闭合，不再依赖下一行。
                sb.Append("\",\"mission\":\"").Append(Jw.Esc(_origin)).Append('"');
                sb.Append(",\"randomSeed\":").Append(Jw.N(SubModule.PendingRandomSeed));
                sb.Append(",\"round\":").Append(Jw.N(_round));
                // v0.8.41：本场的环境旋钮 + 战术档位。
                // 追加在 `round` 之后、`file` 之前 —— 既有键一个不删不改（老分析脚本按 key 取，不受影响）。
                // 未请求时分别是 `""` / `-1` / `-1`，与"从没设过"无法区分也没关系：
                // 分析时只关心"这一场是不是设了"，而 -1 在两端语义一致（不覆盖）。
                sb.Append(BattleEnv.MetaFragment());
                sb.Append(",\"aTactics\":").Append(Jw.N(ScenarioRunner.AttackerTacticsRequested));
                sb.Append(",\"dTactics\":").Append(Jw.N(ScenarioRunner.DefenderTacticsRequested));
                sb.Append(",\"aTacticsNative\":").Append(Jw.N(ScenarioRunner.AttackerTacticsNative));
                sb.Append(",\"dTacticsNative\":").Append(Jw.N(ScenarioRunner.DefenderTacticsNative));
                sb.Append(",\"file\":\"").Append(Jw.Esc(name)).Append("\"}");
                Jw.Write(sb.ToString());
            }
            catch (Exception ex)
            {
                _ioError = ex.GetType().Name + ": " + ex.Message;
            }
        }

        /// <summary>
        /// 本场 mission 的来源标记："bridge"（BlBridge 自建靶场）/ "game"（其它，含玩家实战）。
        /// 由 `ScenarioRunner.OpenMission` 在开战前置 "bridge"；构造时读一次并**立刻复位**，
        /// 所以标记只在它被设置的那一场有效，不存在残留污染。
        /// </summary>
        private readonly string _origin;

        /// <summary>v0.8.5：当前轮次（1 = 单轮，多轮时由 RoundOrchestratorBehavior 递增）。</summary>
        private int _round = 1;

        public TelemetryBehavior()
        {
            _origin = SubModule.MissionOrigin;
            SubModule.MissionOrigin = "game";
        }

        /// <summary>
        /// v0.8.5 多轮：切到新一轮 —— **换一个日志文件**、计数器归零、meta 带 round。
        /// 由 `RoundOrchestratorBehavior` 在清场之后调用。
        /// 每轮独立文件是刻意的：多轮挤进同一个文件，第 2 轮的箭会被算进第 1 轮的「到死挨箭数」。
        /// </summary>
        internal void BeginNewRound(int round)
        {
            if (_closed || !BridgeConfig.Enabled) return;
            _round = round;
            _elapsed = 0f;
            _nextSampleAt = BridgeConfig.SampleIntervalSeconds;
            _nextStateAt = BridgeConfig.StateIntervalSeconds;
            _hitSeq = _killSeq = _shotSeq = _unitSeq = _fleeSeq = _stateSeq = 0;
            _aiDumped.Clear();
            _equipDumped.Clear();
            _initAttacker = -1;
            _initDefender = -1;
            _opened = false;      // 让 EnsureOpen 重新开一个新文件（含新的 meta）
            EnsureOpen();
        }

        public override void OnAgentBuild(Agent agent, Banner banner)
        {
            base.OnAgentBuild(agent, banner);
            if (!BridgeConfig.Enabled || _closed) return;
            EnsureOpen();
            try
            {
                if (agent == null || !agent.IsHuman) return;
                _unitSeq++;
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"unit\",\"seq\":").Append(Jw.N(_unitSeq));
                sb.Append(",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"agent\":").Append(Jw.N(agent.Index));
                sb.Append(",\"side\":\"").Append(SideOf(agent)).Append('"');
                sb.Append(",\"troop\":\"").Append(Jw.Esc(TroopOf(agent))).Append('"');
                sb.Append(",\"level\":").Append(Jw.N(LevelOf(agent)));
                sb.Append(",\"isHero\":").Append(Jw.B(IsHero(agent)));
                sb.Append(",\"isMounted\":").Append(Jw.B(agent.MountAgent != null));
                sb.Append(",\"maxHp\":").Append(Jw.N(agent.HealthLimit));
                // T7：该 agent 的**实际编队**（GC4）。追加在末尾，既有字段一个不删不改（GC2）。
                sb.Append(",\"formation\":\"").Append(Jw.Esc(FormationOfActor(agent))).Append('"');
                sb.Append('}');
                Jw.Write(sb.ToString());
                CountInitial(SideOf(agent));
            }
            catch
            {
            }
        }

        // v0.7.6：hit 事件改用 OnScoreHit（不再用 OnAgentHit）。
        // 依据（读码 Mission.cs:5612-5616 + 本机实测 837:837 配平）：
        //   两者在同一个 foreach 里相邻调用、触发次数完全相同；但 OnScoreHit 多给
        //   isBlocked / damagedHp / hitDistance / shotDifficulty —— 于是"是否被挡下"与
        //   "本次实际扣血"从**推断**变成引擎**直给**，分析器不用再拿 hpAfter 做跨击差分
        //   （那个差分会被 Health 的 Ceiling 取整和单次超血量截断影响）。
        // 注意：**不能**同时 override 两者 —— 同一次命中会写两条 hit 事件。
        public override void OnScoreHit(Agent affectedAgent, Agent affectorAgent, WeaponComponentData attackerWeapon,
            bool isBlocked, bool isSiegeEngineHit, in Blow blow, in AttackCollisionData collisionData,
            float damagedHp, float hitDistance, float shotDifficulty)
        {
            base.OnScoreHit(affectedAgent, affectorAgent, attackerWeapon, isBlocked, isSiegeEngineHit, in blow,
                in collisionData, damagedHp, hitDistance, shotDifficulty);
            if (!BridgeConfig.Enabled || _closed) return;
            EnsureOpen();
            try
            {
                _hitSeq++;
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"hit\",\"seq\":").Append(Jw.N(_hitSeq));
                sb.Append(",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"attacker\":").Append(Jw.N(Idx(affectorAgent)));
                sb.Append(",\"defender\":").Append(Jw.N(Idx(affectedAgent)));
                sb.Append(",\"aSide\":\"").Append(SideOf(affectorAgent)).Append('"');
                sb.Append(",\"dSide\":\"").Append(SideOf(affectedAgent)).Append('"');
                sb.Append(",\"aTroop\":\"").Append(Jw.Esc(TroopOf(affectorAgent))).Append('"');
                sb.Append(",\"dTroop\":\"").Append(Jw.Esc(TroopOf(affectedAgent))).Append('"');
                sb.Append(",\"weaponClass\":\"").Append(blow.WeaponRecord.WeaponClass.ToString()).Append('"');
                sb.Append(",\"isMissile\":").Append(Jw.B(blow.IsMissile));
                sb.Append(",\"damageType\":\"").Append(blow.DamageType.ToString()).Append('"');
                sb.Append(",\"bodyPart\":\"").Append(blow.VictimBodyPart.ToString()).Append('"');
                // 直名（v0.8.1）：引擎 Head=0 与 CriticalBodyPartsBegin=0 同值，ToString 返回别名，
                // 直接读 bodyPart 会看到 "CriticalBodyPartsBegin"（占命中 43%~54%，2026-09-24 实测）。
                // 旧字段保留不动，避免破坏已有 60+ 场历史日志。
                sb.Append(",\"bodyPartName\":\"").Append(EnumNames.BodyPart(blow.VictimBodyPart)).Append('"');
                sb.Append(",\"dmg\":").Append(Jw.N(blow.InflictedDamage));
                sb.Append(",\"magnitude\":").Append(Jw.N(blow.BaseMagnitude));
                sb.Append(",\"absorbedByArmor\":").Append(Jw.N(blow.AbsorbedByArmor));
                sb.Append(",\"strikeType\":\"").Append(blow.StrikeType.ToString()).Append('"');
                sb.Append(",\"hpAfter\":").Append(Jw.N(HpOf(affectedAgent)));
                sb.Append(",\"hpMax\":").Append(Jw.N(MaxHpOf(affectedAgent)));
                sb.Append(",\"mounted\":").Append(Jw.B(affectedAgent != null && affectedAgent.MountAgent != null));
                // ↓ v0.7.6 新增：引擎直给，不再靠推断/差分
                sb.Append(",\"blocked\":").Append(Jw.B(isBlocked));
                sb.Append(",\"damagedHp\":").Append(Jw.N(damagedHp));
                sb.Append(",\"hitDistance\":").Append(Jw.N(hitDistance));
                sb.Append(",\"shotDifficulty\":").Append(Jw.N(shotDifficulty));
                sb.Append(",\"siegeHit\":").Append(Jw.B(isSiegeEngineHit));
                // ↓ v0.7.9 新增：把引擎对"这一次打击"知道的其余事也记全（全部来自 blow / collisionData）
                sb.Append(",\"attackDir\":\"").Append(collisionData.AttackDirection.ToString()).Append('"');
                sb.Append(",\"attackType\":\"").Append(blow.AttackType.ToString()).Append('"');
                sb.Append(",\"speedMod\":").Append(Jw.N(blow.MovementSpeedDamageModifier));
                sb.Append(",\"atkStun\":").Append(Jw.N(blow.AttackerStunPeriod));
                sb.Append(",\"defStun\":").Append(Jw.N(blow.DefenderStunPeriod));
                sb.Append(",\"dmgPct\":").Append(Jw.N(blow.DamagedPercentage));
                sb.Append(",\"blowFlags\":\"").Append(blow.BlowFlag.ToString()).Append('"');
                // 盾牌状态：用"盾自己的血量"区分"打在盾上"与"打在身体上"，
                // 不依赖任何瞄准推断 —— 打中盾 → 这个值下降；打中身体 → 它不变。
                // 分析器从 shieldHp 序列归零即可得出"几箭破盾"。
                // v0.8.1 补盾**身份**：旧版只取"遍历到的第一个盾槽"，盾槽/盾物品变过就会误判
                // （场 1 有 agent 盾值回升 477 → 530 ⇒ 盾身份变过）。
                short shHp, shMax;
                int shSlot;
                string shItem;
                if (TryGetShield(affectedAgent, out shHp, out shMax, out shSlot, out shItem))
                {
                    sb.Append(",\"shieldHp\":").Append(Jw.N((int)shHp));
                    sb.Append(",\"shieldMax\":").Append(Jw.N((int)shMax));
                    sb.Append(",\"shieldSlot\":").Append(Jw.N(shSlot));
                    sb.Append(",\"shieldItem\":\"").Append(Jw.Esc(shItem)).Append('"');
                }
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }

        /// <summary>
        /// 射击事件（v0.7.9）：每次射出投射物记一条。
        /// 有了它才能谈"命中率"和"要多少箭" —— 只看 hit 事件只有分子、没有分母。
        /// position + velocity 还能直接算**实际瞄准偏差**：
        ///   实际方向 = velocity 归一化；理想方向 = (目标位置 − position) 归一化；偏差 = 两者夹角。
        /// </summary>
        public override void OnAgentShootMissile(Agent shooterAgent, EquipmentIndex weaponIndex, Vec3 position,
            Vec3 velocity, Mat3 orientation, bool hasRigidBody, int forcedMissileIndex)
        {
            base.OnAgentShootMissile(shooterAgent, weaponIndex, position, velocity, orientation, hasRigidBody, forcedMissileIndex);
            if (!BridgeConfig.Enabled || _closed) return;
            EnsureOpen();
            try
            {
                _shotSeq++;
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"shot\",\"seq\":").Append(Jw.N(_shotSeq));
                sb.Append(",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"shooter\":").Append(Jw.N(Idx(shooterAgent)));
                sb.Append(",\"side\":\"").Append(SideOf(shooterAgent)).Append('"');
                sb.Append(",\"troop\":\"").Append(Jw.Esc(TroopOf(shooterAgent))).Append('"');
                sb.Append(",\"weaponSlot\":\"").Append(weaponIndex.ToString()).Append('"');
                // 直名（v0.8.1）：EquipmentIndex 的 WeaponItemBeginSlot(0) 与 Weapon0(0) 同值，
                // ToString 返回前者 ⇒ 字面 "Weapon0" 永不出现（与 bodyPart 同类）。
                sb.Append(",\"weaponSlotName\":\"").Append(EnumNames.EquipSlot(weaponIndex)).Append('"');
                sb.Append(",\"weaponClass\":\"").Append(Jw.Esc(WeaponClassOf(shooterAgent, weaponIndex))).Append('"');
                sb.Append(",\"px\":").Append(Jw.N(position.x));
                sb.Append(",\"py\":").Append(Jw.N(position.y));
                sb.Append(",\"pz\":").Append(Jw.N(position.z));
                sb.Append(",\"vx\":").Append(Jw.N(velocity.x));
                sb.Append(",\"vy\":").Append(Jw.N(velocity.y));
                sb.Append(",\"vz\":").Append(Jw.N(velocity.z));
                sb.Append(",\"speed\":").Append(Jw.N(velocity.Length));
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }

        /// <summary>取某个装备槽的武器类别（把"射出的东西"与后面命中的记录对上）。</summary>
        private static string WeaponClassOf(Agent a, EquipmentIndex slot)
        {
            try
            {
                if (a == null) return "";
                MissionWeapon w = a.Equipment[slot];
                if (w.Item == null) return "";
                WeaponComponentData u = w.CurrentUsageItem;
                return u != null ? u.WeaponClass.ToString() : "";
            }
            catch
            {
                return "";
            }
        }

        /// <summary>
        /// 取防守方身上的盾（若有）：当前耐久与上限。
        /// 槽位遍历照 Agent.cs:3008 的写法（WeaponItemBeginSlot → NumAllWeaponSlots）。
        /// 用途：用**盾自己的血量**区分"打在盾上"与"打在身体上"——不依赖任何瞄准推断：
        /// 打中盾 → 这个值下降；打中身体 → 它不变。分析器从 shieldHp 序列归零即可得"几箭破盾"。
        /// </summary>
        private static bool TryGetShield(Agent a, out short hp, out short max, out int slot, out string itemId)
        {
            hp = 0;
            max = 0;
            slot = -1;
            itemId = "";
            try
            {
                if (a == null) return false;
                // 注意：Agent.Equipment 是 MissionEquipment（索引器返回 MissionWeapon，才有耐久）；
                // 而 EquipmentElement 来自 Agent.SpawnEquipment（静态装备，无运行时耐久）。
                MissionEquipment eq = a.Equipment;
                for (EquipmentIndex i = EquipmentIndex.WeaponItemBeginSlot; i < EquipmentIndex.NumAllWeaponSlots; i++)
                {
                    MissionWeapon w = eq[i];
                    if (w.Item != null && w.IsShield())
                    {
                        hp = w.HitPoints;
                        max = w.ModifiedMaxHitPoints;
                        slot = (int)i;
                        itemId = w.Item.StringId ?? "";
                        return true;
                    }
                }
            }
            catch
            {
            }
            return false;
        }

        public override void OnAgentRemoved(Agent affectedAgent, Agent affectorAgent, AgentState agentState,
            KillingBlow blow)
        {
            base.OnAgentRemoved(affectedAgent, affectorAgent, agentState, blow);
            if (!BridgeConfig.Enabled || _closed) return;
            EnsureOpen();
            try
            {
                _killSeq++;
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"kill\",\"seq\":").Append(Jw.N(_killSeq));
                sb.Append(",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"victim\":").Append(Jw.N(Idx(affectedAgent)));
                sb.Append(",\"killer\":").Append(Jw.N(Idx(affectorAgent)));
                sb.Append(",\"victimTroop\":\"").Append(Jw.Esc(TroopOf(affectedAgent))).Append('"');
                sb.Append(",\"killerTroop\":\"").Append(Jw.Esc(TroopOf(affectorAgent))).Append('"');
                sb.Append(",\"vSide\":\"").Append(SideOf(affectedAgent)).Append('"');
                sb.Append(",\"state\":\"").Append(agentState.ToString()).Append('"');
                sb.Append(",\"dmg\":").Append(Jw.N(blow.InflictedDamage));
                sb.Append(",\"damageType\":\"").Append(blow.DamageType.ToString()).Append('"');
                sb.Append(",\"bodyPart\":\"").Append(blow.VictimBodyPart.ToString()).Append('"');
                sb.Append(",\"bodyPartName\":\"").Append(EnumNames.BodyPart(blow.VictimBodyPart)).Append('"');
                sb.Append(",\"isMissile\":").Append(Jw.B(blow.IsMissile));
                sb.Append(",\"weaponClass\":").Append(Jw.N(blow.WeaponClass));
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }

        public override void OnAgentFleeing(Agent affectedAgent)
        {
            base.OnAgentFleeing(affectedAgent);
            if (!BridgeConfig.Enabled || _closed) return;
            EnsureOpen();
            try
            {
                _fleeSeq++;
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"flee\",\"seq\":").Append(Jw.N(_fleeSeq));
                sb.Append(",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"agent\":").Append(Jw.N(Idx(affectedAgent)));
                sb.Append(",\"side\":\"").Append(SideOf(affectedAgent)).Append('"');
                sb.Append(",\"troop\":\"").Append(Jw.Esc(TroopOf(affectedAgent))).Append('"');
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }

        public override void OnAgentPanicked(Agent affectedAgent)
        {
            base.OnAgentPanicked(affectedAgent);
            if (!BridgeConfig.Enabled || _closed) return;
            EnsureOpen();
            try
            {
                _fleeSeq++;
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"panic\",\"seq\":").Append(Jw.N(_fleeSeq));
                sb.Append(",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"agent\":").Append(Jw.N(Idx(affectedAgent)));
                sb.Append(",\"side\":\"").Append(SideOf(affectedAgent)).Append('"');
                sb.Append(",\"troop\":\"").Append(Jw.Esc(TroopOf(affectedAgent))).Append('"');
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }

        public override void OnMissionTick(float dt)
        {
            base.OnMissionTick(dt);
            // 时间控制与推进探针都优先于遥测开关：即使关掉遥测，加速与"是否真的在打"也要能看
            TimeControl.Apply(this.Mission);
            if (!_probeReset)
            {
                _probeReset = true;      // 新任务开始：清掉上一场的计数，避免串场
                EngineProbe.Reset();
                EnsureOpen();            // 顺带让状态文件知道"正在战斗中"（崩溃现场定位用）
                SubModule.NotifyBattleStarted(_file);
            }
            EngineProbe.OnTick(this.Mission);
            LogFastForwardChanges();
            LogStallTransitions();
            if (!BridgeConfig.Enabled || _closed) return;
            EnsureOpen();
            try
            {
                _elapsed += dt;

                // v0.7.9：agent 状态采样，独立节奏（与 10 秒的 sample 解耦）。
                // ⚠️ 必须放在下面那个 `_elapsed < _nextSampleAt` 的 return **之前** ——
                //    否则状态采样会被 sample 的节奏挡掉（永远只在 sample 命中那一帧跑）。
                if (BridgeConfig.StateIntervalSeconds > 0f && _elapsed >= _nextStateAt)
                {
                    _nextStateAt += BridgeConfig.StateIntervalSeconds;
                    EmitStateSamples(this.Mission);
                }

                if (_elapsed < _nextSampleAt) return;
                _nextSampleAt += BridgeConfig.SampleIntervalSeconds;

                Team at = this.Mission != null ? this.Mission.AttackerTeam : null;
                Team df = this.Mission != null ? this.Mission.DefenderTeam : null;
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"sample\",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"aAlive\":").Append(Jw.N(TeamAlive(at)));
                sb.Append(",\"dAlive\":").Append(Jw.N(TeamAlive(df)));
                sb.Append(",\"aHp\":").Append(Jw.N(TeamHp(at)));
                sb.Append(",\"dHp\":").Append(Jw.N(TeamHp(df)));
                // v0.8.45：**尸体计数** —— 让 `keepCorpses`（`DisableCorpseFadeOut`）有观测量。
                // 动机（2026-10-05）：`DisableCorpseFadeOut` 在**托管侧零读取消费者**（定向复核确认），
                // 只能靠 native；要验它就必须有一个**残留尸体数**的时间序列。
                // 口径（见 Agent.IsAddedAsCorpse → MBAPI.IMBAgent.IsAddedAsCorpse）：数
                // `Mission.AllAgents` 里 `IsAddedAsCorpse()==true` 的。
                // ⚠️ 用 `AllAgents`（不是 `Agents`）：尸体在 `OnAgentDeleted` 之前仍在集合里，
                //    `Agents` 是"活跃 agent"，数不到尸体。整体 try/catch：绝不影响既有字段。
                sb.Append(",\"corpses\":").Append(Jw.N(CorpseCount(this.Mission)));
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }

        protected override void OnEndMission()
        {
            base.OnEndMission();
            if (_closed) return;
            EnsureOpen();
            try
            {
                Team at = this.Mission != null ? this.Mission.AttackerTeam : null;
                Team df = this.Mission != null ? this.Mission.DefenderTeam : null;
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"end\",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"aAlive\":").Append(Jw.N(TeamAlive(at)));
                sb.Append(",\"dAlive\":").Append(Jw.N(TeamAlive(df)));
                sb.Append(",\"aInitial\":").Append(Jw.N(_initAttacker));
                sb.Append(",\"dInitial\":").Append(Jw.N(_initDefender));
                sb.Append(",\"hits\":").Append(Jw.N(_hitSeq));
                sb.Append(",\"kills\":").Append(Jw.N(_killSeq));
                sb.Append(",\"flees\":").Append(Jw.N(_fleeSeq));
                sb.Append(",\"ioFailed\":").Append(Jw.B(!Jw.IsOpen));
                sb.Append(",\"ioError\":\"").Append(Jw.Esc(ErrText())).Append('"');
                sb.Append(",\"nanCount\":").Append(Jw.N(Jw.NanCount));
                // 样本有效性：卡住/黑屏/加载中的战斗会在这里被判 suspect，A/B 时应当剔除
                sb.Append(",\"validity\":").Append(EngineProbe.EndValidityJson());
                sb.Append('}');
                Jw.Write(sb.ToString());
                string f = Jw.CurrentFile;
                CloseFile();
                SubModule.NotifyBattleFinished(f, _hitSeq + _killSeq);
            }
            catch
            {
            }
        }

        /// <summary>
        /// v0.7.9：agent 状态快照。一个事件覆盖四类问题：
        ///   移速 —— Position / MovementVelocity / MaxSpeedMultiplier / CombatMaxSpeedMultiplier
        ///   负重 —— ArmorEncumbrance / WeaponsEncumbrance（移速公式的输入，可逐项对账）
        ///   装弹 —— IsReloading / ReloadPhase / ReloadPhaseCount / Amount / ModifiedMaxAmount
        ///   士气 —— GetMorale() / AIStateFlags
        /// 只采前 BridgeConfig.StateMaxAgents 个（按索引），防止大场面爆量。
        /// </summary>
        private void EmitStateSamples(Mission mission)
        {
            if (mission == null) return;
            int written = 0;
            foreach (Agent a in mission.Agents)
            {
                if (written >= BridgeConfig.StateMaxAgents) break;
                if (a == null || !a.IsActive() || !a.IsHuman) continue;
                try
                {
                    _stateSeq++;
                    written++;

                    // 首次见到这个 agent 时额外写一条 AI/精度快照（只写一次）
                    if (_aiDumped.Add(Idx(a))) EmitAiSnapshot(a);

                    // v0.8.41：同样"每个 agent 只写一次"的装备逐槽快照（见 EmitEquipSnapshot）。
                    // 与 `ai` 事件**分开**是有意的：`ai` 的字段集合已被既有分析脚本按 key 消费，
                    // 往里加 12 个槽位会把它撑成两种东西；新开一个事件类型，旧事件逐字节不变。
                    if (_equipDumped.Add(Idx(a))) EmitEquipSnapshot(a);

                    StringBuilder sb = new StringBuilder();
                    sb.Append("{\"t\":\"state\",\"seq\":").Append(Jw.N(_stateSeq));
                    sb.Append(",\"time\":").Append(Jw.N(_elapsed));
                    sb.Append(",\"agent\":").Append(Jw.N(Idx(a)));
                    sb.Append(",\"side\":\"").Append(SideOf(a)).Append('"');
                    sb.Append(",\"troop\":\"").Append(Jw.Esc(TroopOf(a))).Append('"');
                    Vec3 p = a.Position;
                    sb.Append(",\"px\":").Append(Jw.N(p.x));
                    sb.Append(",\"py\":").Append(Jw.N(p.y));
                    sb.Append(",\"pz\":").Append(Jw.N(p.z));
                    Vec2 v = a.MovementVelocity;
                    sb.Append(",\"vx\":").Append(Jw.N(v.x));
                    sb.Append(",\"vy\":").Append(Jw.N(v.y));
                    sb.Append(",\"speed\":").Append(Jw.N(v.Length));
                    sb.Append(",\"maxSpeed\":").Append(Jw.N(a.GetAgentDrivenPropertyValue(DrivenProperty.MaxSpeedMultiplier)));
                    sb.Append(",\"combatSpeed\":").Append(Jw.N(a.GetAgentDrivenPropertyValue(DrivenProperty.CombatMaxSpeedMultiplier)));
                    sb.Append(",\"armorEnc\":").Append(Jw.N(a.GetAgentDrivenPropertyValue(DrivenProperty.ArmorEncumbrance)));
                    sb.Append(",\"weapEnc\":").Append(Jw.N(a.GetAgentDrivenPropertyValue(DrivenProperty.WeaponsEncumbrance)));
                    sb.Append(",\"morale\":").Append(Jw.N(a.GetMorale()));
                    sb.Append(",\"aiState\":\"").Append(a.AIStateFlags.ToString()).Append('"');
                    EquipmentIndex prim = a.GetPrimaryWieldedItemIndex();
                    if (prim != EquipmentIndex.None)
                    {
                        MissionWeapon w = a.Equipment[prim];
                        sb.Append(",\"reloading\":").Append(Jw.B(w.IsReloading));
                        sb.Append(",\"reloadPhase\":").Append(Jw.N((int)w.ReloadPhase));
                        sb.Append(",\"reloadCount\":").Append(Jw.N((int)w.ReloadPhaseCount));
                        sb.Append(",\"ammo\":").Append(Jw.N((int)w.Amount));
                        sb.Append(",\"ammoMax\":").Append(Jw.N((int)w.ModifiedMaxAmount));
                    }
                    sb.Append('}');
                    Jw.Write(sb.ToString());
                }
                catch
                {
                }
            }
        }

        /// <summary>
        /// v0.7.9：AI / 精度参数的**一次性快照**（每个 agent 只写一条）。
        /// 这些值在战斗中基本不变，所以开头记一次即可 —— 用来回答"为什么"：
        ///   为什么野民打得快、军团兵打得重？为什么 AI 弓手那么准？
        /// 每项都来自 agent.GetAgentDrivenPropertyValue(DrivenProperty.X)，
        /// 也就是引擎自己算出来、并真正用于该 agent 行为的那份数值。
        /// </summary>
        private void EmitAiSnapshot(Agent a)
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"ai\",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"agent\":").Append(Jw.N(Idx(a)));
                sb.Append(",\"side\":\"").Append(SideOf(a)).Append('"');
                sb.Append(",\"troop\":\"").Append(Jw.Esc(TroopOf(a))).Append('"');
                sb.Append(",\"level\":").Append(Jw.N(LevelOf(a)));
                sb.Append(",\"defensiveness\":").Append(Jw.N(a.Defensiveness));
                sb.Append(",\"maxSpeed\":").Append(Jw.N(DP(a, DrivenProperty.MaxSpeedMultiplier)));
                sb.Append(",\"combatSpeed\":").Append(Jw.N(DP(a, DrivenProperty.CombatMaxSpeedMultiplier)));
                // v0.8.1：补"加速到顶速所需时长"。**引擎不通过公开 API 暴露世界单位的速度上限**
                // （AgentDrivenProperties 的 100+ 属性里只有 *Multiplier；AgentStatCalculateModel
                //  也没有 GetMaximumSpeed —— 2026-09-24 反编译核实过），所以"上限是否生效"只能退回到
                //  "实测速度峰值 ÷ 倍率"的统计口径间接判断；这里补的时长是加速模型的直接读数。
                sb.Append(",\"topSpeedReach\":").Append(Jw.N(DP(a, DrivenProperty.TopSpeedReachDuration)));
                sb.Append(",\"armorEnc\":").Append(Jw.N(DP(a, DrivenProperty.ArmorEncumbrance)));
            // 四部位护甲值：既是"护甲覆盖是否生效"的判据，也是护甲对照实验的自变量读数。
            // 部位名照引擎真名（ArmorTorso / ArmorLegs / ArmorArms，不是 Body / Arm / Leg）。
            sb.Append(",\"armorHead\":").Append(Jw.N(DP(a, DrivenProperty.ArmorHead)));
            sb.Append(",\"armorTorso\":").Append(Jw.N(DP(a, DrivenProperty.ArmorTorso)));
            sb.Append(",\"armorLegs\":").Append(Jw.N(DP(a, DrivenProperty.ArmorLegs)));
            sb.Append(",\"armorArms\":").Append(Jw.N(DP(a, DrivenProperty.ArmorArms)));
                sb.Append(",\"weapEnc\":").Append(Jw.N(DP(a, DrivenProperty.WeaponsEncumbrance)));
                // 攻击/格挡倾向（AI 战斗参数）
                sb.Append(",\"blockAbility\":").Append(Jw.N(DP(a, DrivenProperty.AIBlockOnDecideAbility)));
                sb.Append(",\"parryAbility\":").Append(Jw.N(DP(a, DrivenProperty.AIParryOnDecideAbility)));
                sb.Append(",\"decideOnAtk\":").Append(Jw.N(DP(a, DrivenProperty.AIDecideOnAttackChance)));
                sb.Append(",\"atkOnDecide\":").Append(Jw.N(DP(a, DrivenProperty.AIAttackOnDecideChance)));
                sb.Append(",\"swingSpeed\":").Append(Jw.N(DP(a, DrivenProperty.SwingSpeedMultiplier)));
                sb.Append(",\"thrustReady\":").Append(Jw.N(DP(a, DrivenProperty.ThrustOrRangedReadySpeedMultiplier)));
                // 远程：射速 / 精度 / 瞄准时间
                sb.Append(",\"shootFreq\":").Append(Jw.N(DP(a, DrivenProperty.AiShootFreq)));
                sb.Append(",\"shooterError\":").Append(Jw.N(DP(a, DrivenProperty.AiShooterError)));
                sb.Append(",\"weaponInaccuracy\":").Append(Jw.N(DP(a, DrivenProperty.WeaponInaccuracy)));
                // 注意：这两个的**属性名与枚举名不同名**（AgentDrivenProperties.cs:88 / :100）：
                //   WeaponMaxMovementAccuracyPenalty  → DrivenProperty.WeaponWorstMobileAccuracyPenalty
                //   WeaponMaxUnsteadyAccuracyPenalty  → DrivenProperty.WeaponWorstUnsteadyAccuracyPenalty
                sb.Append(",\"moveAccPenalty\":").Append(Jw.N(DP(a, DrivenProperty.WeaponWorstMobileAccuracyPenalty)));
                sb.Append(",\"unsteadyPenalty\":").Append(Jw.N(DP(a, DrivenProperty.WeaponWorstUnsteadyAccuracyPenalty)));
                sb.Append(",\"rotAccPenalty\":").Append(Jw.N(DP(a, DrivenProperty.WeaponRotationalAccuracyPenaltyInRadians)));
                sb.Append(",\"bestAccWaitTime\":").Append(Jw.N(DP(a, DrivenProperty.WeaponBestAccuracyWaitTime)));
                sb.Append(",\"unsteadyBegin\":").Append(Jw.N(DP(a, DrivenProperty.WeaponUnsteadyBeginTime)));
                sb.Append(",\"unsteadyEnd\":").Append(Jw.N(DP(a, DrivenProperty.WeaponUnsteadyEndTime)));
                // 提前量误差（AI 弹道预测）
                sb.Append(",\"leadErrMin\":").Append(Jw.N(DP(a, DrivenProperty.AiRangerLeadErrorMin)));
                sb.Append(",\"leadErrMax\":").Append(Jw.N(DP(a, DrivenProperty.AiRangerLeadErrorMax)));
                sb.Append(",\"vertErrMul\":").Append(Jw.N(DP(a, DrivenProperty.AiRangerVerticalErrorMultiplier)));
                sb.Append(",\"horzErrMul\":").Append(Jw.N(DP(a, DrivenProperty.AiRangerHorizontalErrorMultiplier)));
                // 装弹
                sb.Append(",\"reloadSpeed\":").Append(Jw.N(DP(a, DrivenProperty.ReloadSpeed)));
                sb.Append(",\"rangedReadyMul\":").Append(Jw.N(DP(a, DrivenProperty.BipedalRangedReadySpeedMultiplier)));
                sb.Append(",\"rangedReloadMul\":").Append(Jw.N(DP(a, DrivenProperty.BipedalRangedReloadSpeedMultiplier)));
                // 骑乘
                sb.Append(",\"riding\":").Append(Jw.N(DP(a, DrivenProperty.AttributeRiding)));
                sb.Append(",\"horseArchery\":").Append(Jw.N(DP(a, DrivenProperty.AttributeHorseArchery)));
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }

        /// <summary>
        /// v0.8.41：装备**逐槽**快照（每个 agent 只写一条，事件 `t="equip"`）。
        ///
        /// 为什么需要它（这是本项目一个**还没结账的**悬案）：
        ///   `PROGRESS.md` §十五 记着「`armorBody` 是确定性的（3/3 逐场一致）⇒ **不是随机 modifier**」。
        ///   这条推断**不成立**：`AgentBuildData.AgentEquipmentSeed` 来自 `IAgentOriginBase.Seed` /
        ///   `UniqueSeed`，种子若每场确定，则由它抽出的随机 modifier **同样每场一致** ——
        ///   "3/3 一致"分辨不了「没有 modifier」与「种子确定的 modifier」，而后者是
        ///   **随 agent 序号漂移的隐藏变量**，正好污染护甲/材质对照。
        ///
        /// 判别判据（拿到数据后一眼可判，不需要再改代码）：
        ///   * 同一 `troop`、同一场、不同 agent 的 `mod` 各不相同 ⇒ 随机 modifier（混杂源存在）；
        ///   * 全部为 `""` ⇒ 该路径确实不加 modifier，旧结论成立，换装路线可继续。
        ///
        /// `modArmor` 就是能解释"同一件 XML 甲、运行时护甲值不同"的那个数：
        ///   反编译 `ItemModifier.ModifyArmor(int armorValue) = Max(armorValue + Armor, 1)`
        ///   ⇒ **修饰符对护甲的贡献是纯加法**，`Armor` 即该槽位的护甲增量（可为负）。
        ///
        /// 为什么用 `SpawnEquipment` 而不是 `Equipment`：后者（`MissionEquipment`）带运行时状态
        /// （耐久、装弹），会随战斗变化；快照要的是**入场时的静态装备**，即 `SpawnEquipment`
        /// （与 `TryGetShield` 的注释同一口径）。
        ///
        /// 槽位数取 `EquipmentIndex.NumEquipmentSetSlots`（= 12，反编译取证）。
        /// 只写有物品的槽 ⇒ 空槽不进数组，体积可控（12 槽 × 每 agent 一条）。
        /// 整体 try/catch —— 遥测铁律：绝不因观测而抛。
        /// </summary>
        private void EmitEquipSnapshot(Agent a)
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"equip\",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"agent\":").Append(Jw.N(Idx(a)));
                sb.Append(",\"side\":\"").Append(SideOf(a)).Append('"');
                sb.Append(",\"troop\":\"").Append(Jw.Esc(TroopOf(a))).Append('"');
                // 这两个是"同兵种不同 agent 为什么会不一样"的另外两条通道，顺手一起记
                sb.Append(",\"isFemale\":").Append(Jw.B(a.IsFemale));
                sb.Append(",\"bodySeed\":").Append(Jw.N(a.BodyPropertiesSeed));
                sb.Append(",\"slots\":[");
                Equipment equipment = a.SpawnEquipment;
                bool first = true;
                if (equipment != null)
                {
                    for (EquipmentIndex i = EquipmentIndex.WeaponItemBeginSlot;
                        i < EquipmentIndex.NumEquipmentSetSlots; i++)
                    {
                        EquipmentElement element = equipment[i];
                        if (element.Item == null) continue;
                        if (!first) sb.Append(',');
                        first = false;
                        ItemModifier mod = element.ItemModifier;
                        sb.Append("{\"i\":").Append(Jw.N((int)i));
                        sb.Append(",\"slot\":\"").Append(EnumNames.EquipSlot(i)).Append('"');
                        sb.Append(",\"item\":\"").Append(Jw.Esc(ItemIdOf(element.Item))).Append('"');
                        sb.Append(",\"mod\":\"").Append(Jw.Esc(mod == null ? "" : mod.StringId)).Append('"');
                        if (mod != null)
                        {
                            sb.Append(",\"modName\":\"")
                              .Append(Jw.Esc(mod.Name == null ? "" : mod.Name.ToString())).Append('"');
                            // `Armor` 是**加法**增量（ModifyArmor 取证）；另三项同理
                            sb.Append(",\"modArmor\":").Append(Jw.N(mod.Armor));
                            sb.Append(",\"modDamage\":").Append(Jw.N(mod.Damage));
                            sb.Append(",\"modSpeed\":").Append(Jw.N(mod.Speed));
                            sb.Append(",\"modHitPoints\":").Append(Jw.N((int)mod.HitPoints));
                        }
                        sb.Append('}');
                    }
                }
                sb.Append("]}");
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }

        /// <summary>物品 id（取不到返回空串，绝不抛）。</summary>
        private static string ItemIdOf(ItemObject item)
        {
            try
            {
                if (item == null) return "";
                string id = item.StringId;
                return id == null ? "" : id;
            }
            catch
            {
                return "";
            }
        }

        /// <summary>读一个 agent 的 driven property（读不到就返回 0，绝不抛）。</summary>
        private static float DP(Agent a, DrivenProperty p)
        {
            try
            {
                return a.GetAgentDrivenPropertyValue(p);
            }
            catch
            {
                return 0f;
            }
        }

        public override void OnRemoveBehavior()
        {
            base.OnRemoveBehavior();
            CloseFile();
        }

        // ── 内部工具 ─────────────────────────────────────────────────────

        /// <summary>
        /// 记录 `Mission.IsFastForward` 的**每一次状态变化**（事件 t="ff"）。
        ///
        /// 为什么值得记：原版计分板快进按钮 / F 键 / 第三方 mod / 我们的命令通道，最终都落到这一个布尔量上。
        /// 于是"按钮点了没反应"这类问题可以一次定性：
        ///   • 点按钮后**没有 ff 事件** → 这个界面/模式下按钮压根没接线（不是被别的东西关掉）；
        ///   • 出现 true 后**立刻变 false** → 有别的代码在强制关它（mod 冲突）；
        ///   • true 且保持 → 功能其实是好的。
        /// 代价：每场战斗最多几条事件。
        /// </summary>
        private void LogFastForwardChanges()
        {
            try
            {
                bool now = TimeControl.IsFastForward(this.Mission);
                if (now == _ffLast) return;
                _ffLast = now;
                if (!BridgeConfig.Enabled || _closed) return;
                EnsureOpen();
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"ff\",\"seq\":").Append(Jw.N(++_ffSeq));
                sb.Append(",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"missionTime\":").Append(Jw.N(this.Mission != null ? this.Mission.CurrentTime : 0f));
                sb.Append(",\"value\":").Append(Jw.B(now));
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }

        /// <summary>
        /// 记录"任务时间停止推进"的起止（t="stall_start" / "stall_end"）。
        ///
        /// 为什么需要：第一场真实数据里出现 **21.4 秒**冻结，`end.validity` 因此判 `suspect`，
        /// 但从数据里分不清它是**部署阶段等待 / 战斗中被暂停 / 结算等待**中的哪一种。
        /// 记下起止与当时的任务时间，下一次就能直接读出结论（任务时间≈0 = 开局；≈总时长 = 结算）。
        /// </summary>
        private void LogStallTransitions()
        {
            try
            {
                double stalled = EngineProbe.StalledSeconds;
                if (!_stallOpen && stalled >= 3.0)
                {
                    _stallOpen = true;
                    _stallOpenedWall = (double)DateTime.UtcNow.Ticks / TimeSpan.TicksPerSecond;
                    _stallOpenedMissionTime = this.Mission != null ? this.Mission.CurrentTime : 0f;
                    WriteProbeEvent("stall_start", _stallOpenedMissionTime, stalled);
                }
                else if (_stallOpen && stalled < 1.0)
                {
                    double wall = (double)DateTime.UtcNow.Ticks / TimeSpan.TicksPerSecond;
                    _stallOpen = false;
                    WriteProbeEvent("stall_end", _stallOpenedMissionTime, wall - _stallOpenedWall);
                }
            }
            catch
            {
            }
        }

        private void WriteProbeEvent(string kind, float missionTime, double seconds)
        {
            if (!BridgeConfig.Enabled || _closed) return;
            EnsureOpen();
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"t\":\"").Append(kind).Append("\",\"time\":").Append(Jw.N(_elapsed));
            sb.Append(",\"missionTime\":").Append(Jw.N(missionTime));
            sb.Append(",\"seconds\":").Append(Jw.N((float)seconds));
            sb.Append('}');
            Jw.Write(sb.ToString());
        }

        private string ErrText()
        {
            if (_ioError != null) return _ioError;
            return Jw.LastError == null ? "" : Jw.LastError;
        }

        private void CloseFile()
        {
            if (_closed) return;
            _closed = true;
            try
            {
                Jw.Close();
            }
            catch
            {
            }
        }

        private static int Idx(Agent a)
        {
            try
            {
                return a == null ? -1 : a.Index;
            }
            catch
            {
                return -1;
            }
        }

        private static string SideOf(Agent a)
        {
            try
            {
                if (a == null || a.Team == null) return "None";
                return a.Team.Side.ToString();
            }
            catch
            {
                return "None";
            }
        }

        private static string TroopOf(Agent a)
        {
            try
            {
                if (a == null || a.Character == null) return "";
                string id = a.Character.StringId;
                return id == null ? "" : id;
            }
            catch
            {
                return "";
            }
        }

        private static int LevelOf(Agent a)
        {
            try
            {
                return (a == null || a.Character == null) ? 0 : a.Character.Level;
            }
            catch
            {
                return 0;
            }
        }

        private static bool IsHero(Agent a)
        {
            try
            {
                return a != null && a.Character != null && a.Character.IsHero;
            }
            catch
            {
                return false;
            }
        }

        private static float HpOf(Agent a)
        {
            try
            {
                return a == null ? 0f : a.Health;
            }
            catch
            {
                return 0f;
            }
        }

        private static float MaxHpOf(Agent a)
        {
            try
            {
                return a == null ? 0f : a.HealthLimit;
            }
            catch
            {
                return 0f;
            }
        }

        private static int TeamAlive(Team t)
        {
            try
            {
                return t == null ? -1 : t.ActiveAgents.Count;
            }
            catch
            {
                return -1;
            }
        }

        private static float TeamHp(Team t)
        {
            try
            {
                if (t == null) return -1f;
                float sum = 0f;
                for (int i = 0; i < t.ActiveAgents.Count; i++)
                {
                    Agent a = t.ActiveAgents[i];
                    if (a != null) sum += a.Health;
                }
                return sum;
            }
            catch
            {
                return -1f;
            }
        }

        /// <summary>
        /// v0.8.45：残留尸体数（= `Mission.AllAgents` 里 `IsAddedAsCorpse()==true` 的个数）。
        ///
        /// 为什么需要它：`keepCorpses` 写的是 `MissionInitializerRecord.DisableCorpseFadeOut`，
        /// 而该字段在**托管侧零读取消费者**（2026-10-05 定向复核）⇒ 想验它的**行为效应**，
        /// 唯一途径是找一个**能观测到"尸体还在不在"**的量。这就是那个量。
        ///
        /// 口径说明（照 `Agent.IsAddedAsCorpse` → native `IMBAgent.IsAddedAsCorpse`）：
        ///   • 必须遍历 `AllAgents`（`_allAgents`）而**不是** `Agents`（`_activeAgents`）——
        ///     后者是"活跃 agent"，尸体不在其中（`Mission.cs` 的 `OnAgentDeleted` 才从 AllAgents 移除）。
        ///   • 取不到一律返回 **-1**（与 `TeamAlive`/`TeamHp` 同一约定），绝不抛、绝不假装是 0。
        /// </summary>
        private static int CorpseCount(Mission m)
        {
            try
            {
                if (m == null) return -1;
                int n = 0;
                // 用 var：`AgentReadOnlyList` 在 `TaleWorlds.MountAndBlade.Missions` 命名空间里，
                // 而本文件按既有风格只 using 到 `TaleWorlds.MountAndBlade`（不为一行加 using）。
                var all = m.AllAgents;
                if (all == null) return -1;
                for (int i = 0; i < all.Count; i++)
                {
                    Agent a = all[i];
                    if (a == null) continue;
                    if (a.IsAddedAsCorpse()) n++;
                }
                return n;
            }
            catch
            {
                return -1;
            }
        }

        private void CountInitial(string side)
        {
            if (side == "Attacker")
            {
                if (_initAttacker < 0) _initAttacker = 0;
                _initAttacker++;
            }
            else if (side == "Defender")
            {
                if (_initDefender < 0) _initDefender = 0;
                _initDefender++;
            }
        }

        // ── T7：实际编队 + squad 事件 ──────────────────────────────────────

        /// <summary>
        /// T7：该 agent 的**实际编队**名（`EnumNames.Formation`）。
        /// 取证（2026-09-24 反射 TaleWorlds.MountAndBlade.dll）：
        ///   `Agent.Formation` : `TaleWorlds.MountAndBlade.Formation`（public）；
        ///   但本机 DLL 里 **`Formation` 没有 `FormationIndex` 属性** —— 真身是
        ///   `Formation.LogicalClass` / `PhysicalClass` / `RepresentativeClass`（皆 public `FormationClass`）。
        ///   `FormationIndex` 是旧版 API 名，`LogicalClass` 是其现名 ⇒ 用 `LogicalClass`。
        /// 回退链：`agent.Formation.LogicalClass` → `agent.Character.GetFormationClass()` → "Unset"。
        /// 异常安全：任一步取不到都返回 "Unset"，绝不抛（遥测铁律）。
        /// </summary>
        private static string FormationOfActor(Agent a)
        {
            try
            {
                if (a != null)
                {
                    Formation f = a.Formation;
                    if (f != null) return EnumNames.Formation(f.LogicalClass);
                    BasicCharacterObject ch = a.Character;
                    if (ch != null) return EnumNames.Formation(ch.GetFormationClass());
                }
                return "Unset";
            }
            catch
            {
                // 修复轮 1 · nit-1：外层 try 已覆盖 GetFormationClass() 回退，这里只需兜底常量。
                return "Unset";
            }
        }

        /// <summary>
        /// T7：写"每组一行"的 `squad` 事件（GC4）。两处触发点都调它 —— **只写一份格式化代码**：
        ///   1. 首轮入场完成（`ScenarioRunner.ScenarioProbe.OnMissionTick`，RunStateLoading→Running）：
        ///      source="supplier"，spawned = `SquadTroopSupplier.ProvidedCounts[group]`（交给引擎的 origin 数）；
        ///   2. 第 2 轮起重生完成（`RoundOrchestratorBehavior.Advance` 的 SpawnGroups 之后）：
        ///      source="respawn"，spawned = 该组 `Mission.SpawnAgent` 成功次数。
        /// spawned **不**按 troop 从 `Team.ActiveAgents` 统计：同一 troop 可出现在多个组里会算错，
        /// 且"有 origin / 无 origin"两条路径口径无法统一。
        /// 某侧 specs 为 null 或空 ⇒ 该侧不写任何 squad 事件（GC2：旧 plan 不产生新事件）。
        /// 内部整体 try/catch，风格照 RoundLog —— 绝不抛。
        /// </summary>
        internal static void WriteSquadEvents(int round, string source,
            List<SquadSpec> attackerSpecs, List<BasicCharacterObject> attackerTroops, int[] attackerSpawned,
            List<SquadSpec> defenderSpecs, List<BasicCharacterObject> defenderTroops, int[] defenderSpawned)
        {
            if (!BridgeConfig.Enabled) return;
            if (!Jw.IsOpen) return;
            // 修复轮 1 · minor-1：两侧各自 try/catch —— Attacker 侧异常不得连带丢掉 Defender 侧事件。
            try
            {
                WriteSquadSide("Attacker", round, source, attackerSpecs, attackerTroops, attackerSpawned);
            }
            catch
            {
            }
            try
            {
                WriteSquadSide("Defender", round, source, defenderSpecs, defenderTroops, defenderSpawned);
            }
            catch
            {
            }
        }

        private static void WriteSquadSide(string side, int round, string source,
            List<SquadSpec> specs, List<BasicCharacterObject> troops, int[] spawned)
        {
            if (specs == null) return;
            if (specs.Count == 0) return;
            for (int i = 0; i < specs.Count; i++)
            {
                SquadSpec s = specs[i];
                if (s == null) continue;
                int n = (spawned != null && i < spawned.Length) ? spawned[i] : 0;
                string mv = (s.Movement == null) ? "charge" : s.Movement;
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"squad\"");
                sb.Append(",\"round\":").Append(Jw.N(round));
                sb.Append(",\"side\":\"").Append(side).Append('"');
                sb.Append(",\"group\":").Append(Jw.N(i));
                sb.Append(",\"troop\":\"").Append(Jw.Esc(s.Troop)).Append('"');
                sb.Append(",\"count\":").Append(Jw.N(s.Count));
                sb.Append(",\"formation\":\"").Append(Jw.Esc(FormationNameOf(troops, i))).Append('"');
                sb.Append(",\"movement\":\"").Append(Jw.Esc(mv)).Append('"');
                sb.Append(",\"spawned\":").Append(Jw.N(n));
                sb.Append(",\"source\":\"").Append(source).Append('"');
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
        }

        /// <summary>某组兵种的实际编队名（缺 troop 对象 ⇒ "Unset"，绝不抛）。</summary>
        private static string FormationNameOf(List<BasicCharacterObject> troops, int i)
        {
            try
            {
                if (troops == null) return "Unset";
                if (i < 0) return "Unset";
                if (i >= troops.Count) return "Unset";
                BasicCharacterObject t = troops[i];
                if (t == null) return "Unset";
                return EnumNames.Formation(t.GetFormationClass());
            }
            catch
            {
                return "Unset";
            }
        }
    }
}
