// BlBridge 阶段 2① · 不朽靶场行为（原型，待并入 BlBridge\src\）
//
// ── 为什么不用 SetMortalityState（逐行核对引擎的结论）──────────────────────
//
//   MortalityState.Invulnerable  → 伤害在结算前就被取消（Mission.cs:6073 直接 return）
//                                   ⇒ 连命中都收不到
//   MortalityState.Immortal      → 伤害正常算，但实际扣血被强制清零
//                                   Agent.cs:5444-5448  num = min(InflictedDamage, health)
//                                                        if (Immortal || ...) num = 0f;
//                                   ⇒ 有命中事件，但 damagedHp 恒为 0
//   Mission.DisableDying         → 同上清零，且 OnAgentHit/OnScoreHit 根本不触发
//                                   Agent.cs:5466  if (!Mission.DisableDying && ...) Mission.OnAgentHit(...)
//
// 三个"不死"开关都会毁掉伤害数据。正确做法是**保持 Mortal**：
// 让伤害正常结算（拿得到真实扣血），然后在同一回调内把血量复原，使随后的死亡判定不成立。
//
// ── 为什么用 OnScoreHit 而不是 OnAgentHit ──────────────────────────────────
//
// Mission.cs:5612-5616 里两者是**同一个 foreach 中的相邻两行**，零额外条件：
//
//   foreach (MissionBehavior mb in MissionBehaviors)
//   {
//       mb.OnAgentHit(affectedAgent, affectorAgent, in affectorWeapon, in b, in collisionData);
//       mb.OnScoreHit(affectedAgent, affectorAgent, affectorWeapon.CurrentUsageItem, isBlocked,
//                     isSiegeEngineHit, in b, in collisionData, damagedHp, hitDistance, shotDifficulty);
//   }
//
// ⇒ 凡是 OnAgentHit 触发处 OnScoreHit 必然触发（前者已由 15 场实测验证）。
//   而 OnScoreHit 多给 5 个字段，直接消掉了旧方案里的两处推断：
//     isBlocked   —— "被挡下"从推断变成事实（OnAgentHitBlocked → isBlocked:true, damagedHp:0）
//     damagedHp   —— 引擎给的实际扣血，不再需要 hpAfter 差分（也没有取整/截断问题）
//     hitDistance / shotDifficulty / attackerWeapon —— 新增分析维度
//
// ── 挂载顺序 ────────────────────────────────────────────────────────────
//
// 必须挂在 TelemetryBehavior **之后**：同一 foreach 按 behavior 顺序遍历，
// 遥测先读到扣血后的 hpAfter，本行为随后才回血，于是遥测里的 hpMax - hpAfter
// 恒等于"本次真实伤害"。且 Jw 是全局单例写入器（JsonlWriter.cs:12），两者须共用同一文件。

using System.Text;
using TaleWorlds.Core;
using TaleWorlds.MountAndBlade;
using TaleWorlds.ObjectSystem;

namespace BlBridge
{
    /// <summary>
    /// 不朽靶场：让指定一方持续挨打但永不倒下，从而采到**完整**的伤害分布。
    /// 只改血量、不改任何战斗参数；关掉开关即完全恢复原状。
    /// </summary>
    public class DummyRangeBehavior : MissionBehavior
    {
        public override MissionBehaviorType BehaviorType
        {
            get { return MissionBehaviorType.Other; }
        }

        // ── 由 ScenarioRunner 在开战前设置 ──────────────────────────────
        /// <summary>哪一方当靶子；None = 靶场未启用（行为空转）。</summary>
        internal static BattleSideEnum DummySide = BattleSideEnum.None;
        /// <summary>是否冻结靶子的 AI 行为（不还手、不移动）。默认 false —— 冻结会改变 AI 行为，反而失真。</summary>
        internal static bool FreezeDummies = false;
        /// <summary>
        /// v0.7.9：给**射手侧**补满弹药（每帧重申），让长测不会因为"箭射完了"中断。
        /// 依据 = 作弊 mod BannerWand 的 UnlimitedAmmoTarget 思路（它把弹药设成 999），
        /// 但我们**不用 Harmony patch**（那会破坏本项目"删模块即完全回退"的性质），
        /// 而是每帧调 Agent.SetWeaponAmountInSlot 把弹药写回上限。
        /// ⚠️ 只补给射手，**不补给靶子** —— 靶子的弹药与耐久都是被测对象。
        /// </summary>
        internal static bool UnlimitedAmmoForShooters = false;

        /// <summary>
        /// v0.7.9+：靶子护甲数值覆盖（-1 = 不覆盖），每帧重申。
        /// 不用 Harmony patch SandboxAgentStatCalculateModel.UpdateHumanStats（第三方编辑器
        /// KunKunEditor 的做法）：本项目"删掉模块即完全回退"的性质要求**零 patch**；
        /// 每帧重申与 ApplyDummyToughness 同因 —— AgentDrivenProperties 是引擎每次重算
        /// 属性时重写的对象，只设一次会被覆盖。
        /// 部位名照引擎真名（不是 Body / Arm）：Head / Torso / Legs / Arms。
        /// </summary>
        internal static float ArmorHead = -1f;
        internal static float ArmorTorso = -1f;
        internal static float ArmorLegs = -1f;
        internal static float ArmorArms = -1f;

        /// <summary>
        /// v0.8.2：把靶子的**身甲**换成指定物品 id（空 = 不换）。
        /// 动机：Warbandlord 的护甲公式里材质抗性 R 参与伤害
        /// （阈值 = R·A_eff·0.6·PRF…；PR = 0.125^(R·A_eff·0.0215+0.09)），
        /// 而 R 只来自**物品**的 MaterialType、数值来自 AgentDrivenProperties
        /// ⇒ 两者来源不同：换物品即换材质，数值仍可由 ArmorHead/Torso/Legs/Arms 对齐。
        /// 这是"同兵种同数值、只换材质"对照实验的全部机理。
        /// 只作用于靶子；每个 agent 只换一次（FillFrom 会重置整份装备，每帧重建会打断战斗状态）。
        /// </summary>
        internal static string BodyItemId = "";

        private float _elapsed;
        private int _seq;
        private int _restored;
        private int _leakedDeaths;
        private float _appliedSum;
        private int _blockedCount;
        private int _maxApplied;
        private int _mismatchCount;
        private bool _frozen;
        private bool _wroteMeta;
        /// <summary>已换过身甲的靶子 agent（避免每帧重建整份装备）。</summary>
        private readonly System.Collections.Generic.HashSet<int> _swapped = new System.Collections.Generic.HashSet<int>();
        /// <summary>换装报告只写一条（记录实际生效的物品与材质）。</summary>
        private string _swapReported;

        private static bool Enabled
        {
            get { return DummySide != BattleSideEnum.None; }
        }

        // ── 生命周期 ────────────────────────────────────────────────────

        public override void OnMissionTick(float dt)
        {
            base.OnMissionTick(dt);
            if (!Enabled && !UnlimitedAmmoForShooters) return;
            _elapsed += dt;
            try
            {
                if (Enabled)
                {
                    WriteMetaOnce();
                    ApplyDummyToughness();
                    ApplyArmorOverride();
                    ApplyBodyItem();
                    if (FreezeDummies && !_frozen)
                    {
                        FreezeAll();
                        _frozen = true;
                    }
                }
                // 无限弹药独立于靶场开关（可以单独给射手用）
                if (UnlimitedAmmoForShooters) ApplyUnlimitedAmmo();
            }
            catch
            {
            }
        }

        /// <summary>
        /// 靶场核心。时序（Agent.ApplyDamage）：
        ///   5454  Health = num2;                         ← 伤害已应用
        ///   5468  Mission.OnAgentHit(..., damagedHp)      ← 本回调在此内部
        ///   5470  if (Health < 1f) Die(b, ...);           ← 死亡判定在回调**之后**
        /// 所以在这里复原血量即可挡住 Die()。
        /// </summary>
        public override void OnScoreHit(Agent affectedAgent, Agent affectorAgent, WeaponComponentData attackerWeapon,
            bool isBlocked, bool isSiegeEngineHit, in Blow blow, in AttackCollisionData collisionData,
            float damagedHp, float hitDistance, float shotDifficulty)
        {
            base.OnScoreHit(affectedAgent, affectorAgent, attackerWeapon, isBlocked, isSiegeEngineHit, in blow,
                in collisionData, damagedHp, hitDistance, shotDifficulty);
            if (!Enabled || affectedAgent == null || !affectedAgent.IsActive()) return;
            if (!IsDummy(affectedAgent)) return;

            try
            {
                float limit = affectedAgent.HealthLimit;
                float hp = affectedAgent.Health;

                // 引擎给的实际扣血就是权威值；同时用 HP 差分做交叉校验
                // （被挡下时 damagedHp == 0 且 HP 未变，两者应当一致）
                float applied = damagedHp;
                if (applied < 0f) applied = 0f;
                float byHp = limit - hp;
                if (byHp < 0f) byHp = 0f;
                if (System.Math.Abs(applied - byHp) > 1.5f) _mismatchCount++;

                _seq++;
                _appliedSum += applied;
                if (isBlocked) _blockedCount++;
                if ((int)applied > _maxApplied) _maxApplied = (int)applied;

                Record(applied, byHp, hp, limit, isBlocked, isSiegeEngineHit, hitDistance, shotDifficulty,
                    affectedAgent, affectorAgent, attackerWeapon, blow);

                // 这里**不再**回血 —— 改由 OnMissionTick 的 ApplyDummyToughness() 保证靶子打不死。
                // 原因：把 HealthLimit 拉高 9999 之后，60 秒内根本掉不到 0，
                // 于是"必须抢在 Die() 之前回血"这个时机假设就不再是必需品了。
                // （原方案依赖 Agent.cs:5470 的 Die() 判定在回调之后，能work但更脆弱。）
            }
            catch
            {
            }
        }

        /// <summary>兜底：靶子仍然死了（说明回血没赶上）。这不是正常路径，必须留下证据。</summary>
        public override void OnAgentRemoved(Agent affectedAgent, Agent affectorAgent, AgentState agentState,
            KillingBlow blow)
        {
            base.OnAgentRemoved(affectedAgent, affectorAgent, agentState, blow);
            if (!Enabled || affectedAgent == null) return;
            if (!IsDummy(affectedAgent)) return;
            try
            {
                _leakedDeaths++;
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"dummy_leak\",\"seq\":").Append(Jw.N(_seq));
                sb.Append(",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"agent\":").Append(Jw.N(affectedAgent.Index));
                sb.Append(",\"side\":\"").Append(SideName(affectedAgent)).Append('"');
                sb.Append(",\"state\":\"").Append(agentState.ToString()).Append('"');
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }

        /// <summary>
        /// 写汇总 + **无条件复位**静态开关。
        ///
        /// v0.7.9：从 OnEndMission 改为 OnRemoveBehavior。实测依据 —— 那场靶场战斗里
        /// dummy_meta / dummy_hit 都正常，但 **dummy_end 根本没出现**，说明
        /// OnEndMission 在该场景下没被调用；而 TelemetryBehavior 的 end 事件走的是
        /// OnRemoveBehavior，那条一直都是有的。
        /// </summary>
        public override void OnRemoveBehavior()
        {
            base.OnRemoveBehavior();
            try
            {
                if (Enabled)
                {
                    StringBuilder sb = new StringBuilder();
                    sb.Append("{\"t\":\"dummy_end\",\"time\":").Append(Jw.N(_elapsed));
                    sb.Append(",\"dummySide\":\"").Append(DummySide.ToString()).Append('"');
                    sb.Append(",\"hits\":").Append(Jw.N(_seq));
                    sb.Append(",\"restored\":").Append(Jw.N(_restored));
                    sb.Append(",\"leakedDeaths\":").Append(Jw.N(_leakedDeaths));
                    sb.Append(",\"appliedSum\":").Append(Jw.N(_appliedSum));
                    sb.Append(",\"blocked\":").Append(Jw.N(_blockedCount));
                    sb.Append(",\"maxApplied\":").Append(Jw.N(_maxApplied));
                    // 交叉校验：damagedHp 与 HP 差分不一致的次数（正常应为 0 或极少）
                    sb.Append(",\"hpMismatch\":").Append(Jw.N(_mismatchCount));
                    sb.Append('}');
                    Jw.Write(sb.ToString());
                }
            }
            catch
            {
            }
            finally
            {
                // ★ 必须复位（无条件）：这是 static 字段，若残留，
                //   玩家之后**手动**打一场也会变成"靶子永不倒下"——
                //   而手动战斗根本不走 start_battle，没有别的地方会清它。
                DummySide = BattleSideEnum.None;
                FreezeDummies = false;
                // 顺手补上原先漏掉的两项（static 字段残留会污染玩家之后的手动战斗）
                UnlimitedAmmoForShooters = false;
                ArmorHead = ArmorTorso = ArmorLegs = ArmorArms = -1f;
                BodyItemId = "";
            }
        }

        // ── 内部 ────────────────────────────────────────────────────────

        private void WriteMetaOnce()
        {
            if (_wroteMeta) return;
            _wroteMeta = true;
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"t\":\"dummy_meta\",\"dummySide\":\"").Append(DummySide.ToString()).Append('"');
            sb.Append(",\"freeze\":").Append(Jw.B(FreezeDummies));
            sb.Append(",\"armor\":{\"head\":").Append(Jw.N(ArmorHead))
              .Append(",\"torso\":").Append(Jw.N(ArmorTorso))
              .Append(",\"legs\":").Append(Jw.N(ArmorLegs))
              .Append(",\"arms\":").Append(Jw.N(ArmorArms)).Append('}');
            sb.Append(",\"bodyItem\":\"").Append(Jw.Esc(BodyItemId)).Append('"');
            sb.Append(",\"note\":\"Mortal + OnScoreHit 内回血；applied 取自引擎 damagedHp\"}");
            Jw.Write(sb.ToString());
        }

        private void Record(float applied, float byHp, float hp, float limit, bool isBlocked, bool isSiegeEngineHit,
            float hitDistance, float shotDifficulty, Agent victim, Agent attacker, WeaponComponentData weapon, Blow blow)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"t\":\"dummy_hit\",\"seq\":").Append(Jw.N(_seq));
            sb.Append(",\"time\":").Append(Jw.N(_elapsed));
            sb.Append(",\"attacker\":").Append(Jw.N(attacker == null ? -1 : attacker.Index));
            sb.Append(",\"victim\":").Append(Jw.N(victim.Index));
            sb.Append(",\"aTroop\":\"").Append(Jw.Esc(TroopOf(attacker))).Append('"');
            sb.Append(",\"aSide\":\"").Append(SideName(attacker)).Append('"');
            // 主指标：引擎给的实际扣血
            sb.Append(",\"applied\":").Append(Jw.N(applied));
            // 交叉校验值（HP 差分）
            sb.Append(",\"appliedByHp\":").Append(Jw.N(byHp));
            // 被挡下：事实，不再是推断
            sb.Append(",\"blocked\":").Append(Jw.B(isBlocked));
            sb.Append(",\"hpAfter\":").Append(Jw.N(hp));
            sb.Append(",\"hpMax\":").Append(Jw.N(limit));
            sb.Append(",\"dmg\":").Append(Jw.N(blow.InflictedDamage));
            sb.Append(",\"absorbedByArmor\":").Append(Jw.N(blow.AbsorbedByArmor));
            sb.Append(",\"isMissile\":").Append(Jw.B(blow.IsMissile));
            sb.Append(",\"damageType\":\"").Append(blow.DamageType.ToString()).Append('"');
            sb.Append(",\"bodyPart\":\"").Append(blow.VictimBodyPart.ToString()).Append('"');
            // 直名（v0.8.1）：引擎 Head=0 与 CriticalBodyPartsBegin=0 同值，ToString 返回别名
            sb.Append(",\"bodyPartName\":\"").Append(EnumNames.BodyPart(blow.VictimBodyPart)).Append('"');
            sb.Append(",\"weaponClass\":\"").Append(blow.WeaponRecord.WeaponClass.ToString()).Append('"');
            // OnScoreHit 独有
            if (weapon != null)
            {
                sb.Append(",\"weaponItemUsage\":\"").Append(Jw.Esc(weapon.WeaponClass.ToString())).Append('"');
            }
            sb.Append(",\"hitDistance\":").Append(Jw.N(hitDistance));
            sb.Append(",\"shotDifficulty\":").Append(Jw.N(shotDifficulty));
            sb.Append(",\"siegeHit\":").Append(Jw.B(isSiegeEngineHit));
            sb.Append('}');
            Jw.Write(sb.ToString());
        }

        /// <summary>靶子加厚量。取自 BannerWand 的伪无敌常量（9999）。</summary>
        private const float ToughnessBonus = 9999f;

        /// <summary>
        /// 把靶子变"硬"：HealthLimit = BaseHealthLimit + ToughnessBonus，并回满。
        /// 依据 = 本机作弊 mod BannerWand 的伪无敌实现
        /// （Modules\BannerWand\...\BannerWand.dll → Behaviors\Handlers\HealthCheatHandler.cs:126）：
        ///     agent.HealthLimit = agent.BaseHealthLimit + 9999f;
        /// 它另外还调了 ToggleInvulnerable()（即 MortalityState.Invulnerable），
        /// **我们有意不那样做** —— 那会把本次实际扣血强制清零（Agent.cs:5444-5448），
        /// 正是"靶子不死但测不到伤害"的坑。我们的目标是"不死 **且** 伤害可测"。
        /// 每 tick 重申而非只设一次：上限可能被引擎的 stats 重算覆盖
        /// （BannerWand 同样是在回调里反复应用）。
        /// </summary>
        private void ApplyDummyToughness()
        {
            Mission m = Mission.Current;
            if (m == null) return;
            foreach (Agent a in m.Agents)
            {
                if (a == null || !a.IsActive() || !a.IsHuman) continue;
                if (!IsDummy(a)) continue;
                float want = a.BaseHealthLimit + ToughnessBonus;
                if (a.HealthLimit < want)
                {
                    a.HealthLimit = want;
                    a.Health = want;
                    _restored++;
                }
                else if (a.Health < a.HealthLimit)
                {
                    a.Health = a.HealthLimit;
                    _restored++;
                }
            }
        }

        /// <summary>
        /// 把靶子的四个部位护甲设成指定值（每帧重申）。零 Harmony —— 见静态字段处的说明。
        /// </summary>
        private void ApplyArmorOverride()
        {
            if (ArmorHead < 0f & ArmorTorso < 0f & ArmorLegs < 0f & ArmorArms < 0f) return;
            Mission m = Mission.Current;
            if (m == null) return;
            foreach (Agent a in m.Agents)
            {
                if (a == null) continue;
                if (!a.IsActive()) continue;
                if (!a.IsHuman) continue;
                if (!IsDummy(a)) continue;
                try
                {
                    AgentDrivenProperties props = a.AgentDrivenProperties;
                    if (props == null) continue;
                    if (ArmorHead >= 0f) props.ArmorHead = ArmorHead;
                    if (ArmorTorso >= 0f) props.ArmorTorso = ArmorTorso;
                    if (ArmorLegs >= 0f) props.ArmorLegs = ArmorLegs;
                    if (ArmorArms >= 0f) props.ArmorArms = ArmorArms;
                }
                catch
                {
                }
            }
        }

        /// <summary>
        /// 把靶子的身甲换成 BodyItemId（每个 agent 只换一次）。
        ///
        /// 为什么走 `SpawnEquipment.Clone` + `MissionEquipment.FillFrom`：
        /// `MissionEquipment` 的索引器**只有 getter**（反编译核实），运行时无法单槽赋值；
        /// 而 `Equipment.AddEquipmentToSlotWithoutAgent` 能改 `Equipment`，
        /// `FillFrom(Equipment, Banner)` 能把整份装备灌进 agent（零 Harmony）。
        /// 换完发一条 `dummy_swap` 记录**实际生效的物品与材质**——这是本参数的可观测落点
        /// （否则又是一个"写了参数但没生效"的静默失效）。
        /// </summary>
        private void ApplyBodyItem()
        {
            if (string.IsNullOrEmpty(BodyItemId)) return;
            Mission m = Mission.Current;
            if (m == null) return;
            ItemObject item = null;
            try
            {
                MBObjectManager om = MBObjectManager.Instance;
                if (om != null) item = om.GetObject<ItemObject>(BodyItemId);
            }
            catch
            {
            }
            if (item == null)
            {
                if (_swapReported == null)
                {
                    _swapReported = "missing";
                    WriteSwap(-1, "", 0, "item_not_found");
                }
                return;
            }
            int n = 0;
            foreach (Agent a in m.Agents)
            {
                if (a == null || !a.IsActive() || !a.IsHuman) continue;
                if (!IsDummy(a)) continue;
                if (_swapped.Contains(a.Index)) continue;
                try
                {
                    Equipment eq = a.SpawnEquipment.Clone(false);
                    eq.AddEquipmentToSlotWithoutAgent(EquipmentIndex.Body, new EquipmentElement(item));
                    // 注意：Agent.Banner 是 ItemObject（旗子物品），不是 Banner ——
                    // FillFrom 要的是 Banner，从阵型/队伍取（编译期实测踩到过）。
                    Banner banner = null;
                    try
                    {
                        if (a.Formation != null) banner = a.Formation.Banner;
                        else if (a.Team != null) banner = a.Team.Banner;
                    }
                    catch
                    {
                    }
                    a.Equipment.FillFrom(eq, banner);
                    _swapped.Add(a.Index);
                    n++;
                }
                catch
                {
                }
            }
            if (n > 0 && _swapReported == null)
            {
                _swapReported = BodyItemId;
                int bodyArmor = -1;
                string mat = "";
                try
                {
                    ArmorComponent ac = item.ArmorComponent;
                    if (ac != null)
                    {
                        bodyArmor = ac.BodyArmor;
                        mat = ac.MaterialType.ToString();
                    }
                }
                catch
                {
                }
                WriteSwap(bodyArmor, mat, n, "");
            }
        }

        private void WriteSwap(int bodyArmor, string material, int agents, string error)
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"t\":\"dummy_swap\",\"time\":").Append(Jw.N(_elapsed));
                sb.Append(",\"item\":\"").Append(Jw.Esc(BodyItemId)).Append('"');
                sb.Append(",\"material\":\"").Append(Jw.Esc(material)).Append('"');
                sb.Append(",\"armorBody\":").Append(Jw.N(bodyArmor));
                sb.Append(",\"agents\":").Append(Jw.N(agents));
                if (!string.IsNullOrEmpty(error))
                {
                    sb.Append(",\"error\":\"").Append(Jw.Esc(error)).Append('"');
                }
                sb.Append('}');
                Jw.Write(sb.ToString());
            }
            catch
            {
            }
        }

        /// <summary>
        /// 把射手侧的弹药补满（每帧重申）。
        /// MissionWeapon 是**值类型** —— 所以不能写 `agent.Equipment[slot].Amount = x`（改的是副本），
        /// 必须走 `Agent.SetWeaponAmountInSlot`（Agent.cs:2258）。只在弹药不满时才写。
        /// </summary>
        private void ApplyUnlimitedAmmo()
        {
            Mission m = Mission.Current;
            if (m == null) return;
            foreach (Agent a in m.Agents)
            {
                if (a == null || !a.IsActive() || !a.IsHuman) continue;
                if (IsDummy(a)) continue;          // ★ 不补给靶子：它的弹药同样是被测对象
                try
                {
                    MissionEquipment eq = a.Equipment;
                    for (EquipmentIndex i = EquipmentIndex.WeaponItemBeginSlot; i < EquipmentIndex.NumAllWeaponSlots; i++)
                    {
                        MissionWeapon w = eq[i];
                        if (w.Item == null || !w.IsAnyAmmo()) continue;
                        if (w.Amount < w.ModifiedMaxAmount)
                        {
                            a.SetWeaponAmountInSlot(i, w.ModifiedMaxAmount, false);
                        }
                    }
                }
                catch
                {
                }
            }
        }

        private void FreezeAll()
        {
            Mission m = Mission.Current;
            if (m == null) return;
            foreach (Agent a in m.Agents)
            {
                if (a == null || !a.IsActive() || !a.IsHuman) continue;
                if (!IsDummy(a)) continue;
                a.SetIsAIPaused(true);
            }
        }

        private static bool IsDummy(Agent a)
        {
            if (a == null || a.Team == null) return false;
            return a.Team.Side == DummySide;
        }

        private static string SideName(Agent a)
        {
            if (a == null || a.Team == null) return "None";
            return a.Team.Side.ToString();
        }

        private static string TroopOf(Agent a)
        {
            try
            {
                if (a == null || a.Character == null) return "";
                return a.Character.StringId ?? "";
            }
            catch
            {
                return "";
            }
        }
    }
}
