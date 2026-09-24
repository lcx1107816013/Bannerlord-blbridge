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
                sb.Append("\",\"mission\":\"").Append(Jw.Esc(_origin));
                // v0.8.4：随机种子（-1 = 未指定）。同种子两场逐值可复现 ⇒ 重放可行（见 PROGRESS §十）
                sb.Append("\",\"randomSeed\":").Append(Jw.N(SubModule.PendingRandomSeed));
                sb.Append("\",\"file\":\"").Append(Jw.Esc(name)).Append("\"}");
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

        public TelemetryBehavior()
        {
            _origin = SubModule.MissionOrigin;
            SubModule.MissionOrigin = "game";
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
    }
}
