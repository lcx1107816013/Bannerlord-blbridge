using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using TaleWorlds.Core;
using TaleWorlds.Engine;
using TaleWorlds.Library;
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    /// <summary>
    /// **战斗中途改令**（v0.8.23）—— BlBridge 的第一个"运行时下令"通道。
    ///
    /// 与开战时 DSL（`troop:count[:formation[:movement]]`，见 `SquadSpec`）的关系：
    ///   DSL 只在 `ScenarioRunner.Start()` 时下发一次；本通道在**战斗进行中**随时改。
    ///   两者走**同一条**下发路径：`Formation.SetMovementOrder` + `ScenarioProbe.MapMovement`。
    ///
    /// ⚠️ **两条硬约束**（不遵守就会出现"看着调成功了其实没生效"或"污染整个进程"）：
    ///   1. **必须在 mission 内**。`MovementOrder` 是 struct，其静态字段在**类型初始化**时构造实例；
    ///      在 mission 之外碰它会抛 `TypeInitializationException`，且 .NET 会把该类型**永久标记为不可用**
    ///      —— 同进程内之后所有改令（含开战 DSL 那条路）一起坏掉。真机教训见
    ///      `ScenarioRunner.cs` 里 `MapMovement` 的注释。
    ///      ⇒ 没有 mission **直接拒**（`no_mission`），不做"先试试看"；名字表与校验器放在
    ///      **只依赖 BCL 的 `OrderSpec`** 里，保证"先校验、后触碰"这条边界能有离线对照断言。
    ///   2. **要连带 `SetControlledByAI(false, false)`**（`detachAI`，默认 **true**）。否则该方 team 级战术
    ///      会周期性把 movement 覆盖回去 —— 开战路径实测过：守方 stop 组被覆盖 59/115 次、位移 154 m。
    ///      官方 AI 对战基准（`CPUBenchmarkMissionLogic`）同样这么关。它只关**编队级 order 的 AI 驱动**，
    ///      不冻结士兵个人行为（那要另调 `SetIsAIPaused`）。
    ///
    /// 判据：每条都报 `orderBefore` / `orderAfter`（`MovementOrder.OrderEnum`）——
    ///   "生效没有"靠**当场回读**，不靠"我们调了 API"。要确认"会不会被覆盖"，隔几秒**再调一次**看
    ///   `orderBefore` 还是不是这次设的值（这就是 A/B 判据，不必另加只读模式）。
    ///
    /// **已支持**：`movement`（引擎 `MovementOrder`）/ `position`（指定点移动，v0.8.30：
    ///   `MovementOrder.MovementOrderMove(WorldPosition)`）/ `target`（冲锋到指定敌方编队，v0.8.31：
    ///   `MovementOrder.MovementOrderChargeToTarget(Formation)`）/ `targetAgent`（攻击指定敌方**单位**，
    ///   v0.8.32：`MovementOrder.MovementOrderAttackEntity(GameEntity, true)`）/ `riding`（上下马，v0.8.32：
    ///   `Formation.SetRidingOrder(RidingOrder)`，三档 Free/Mount/Dismount）/ `arrangement`（阵列：`ArrangementOrder`）
    ///   / `firing`（射击纪律：`FiringOrder`，引擎只有自由射击与停火两档）。七者**至少要给一个**。
    ///   movement / position / target / targetAgent 会被组路径那 0.5 秒一次的重申覆盖（所以必须同步改
    ///   "待重申的东西"；后三者没有"名字"可重申 ⇒ 走**手动令优先**标记，见 `SquadSpec.ManualKind`）；
    ///   arrangement / firing / riding **不在**重申范围内，但仍需隔几秒再读一次确认稳不稳。
    ///   ⚠️ `movement` / `position` / `target` / `targetAgent` **四者互斥**（一个编队只能有一个
    ///   movement order）；`riding` 与它们**正交**（管"骑不骑"，不管"去哪"），所以**不参与**那条互斥。
    /// 目前**没有**已知但未实现的参数（`UnsupportedParams` 保留为空表，供下一个"先占位、后实现"的参数）。
    ///   其余任意怪键由 MCP 工具那层挡
    ///   （`additionalProperties: false`；`Jmini` 是扁平读取器，无法枚举请求里的键）。
    /// </summary>
    internal static class BattleOrders
    {
        /// <summary>
        /// 已知但**尚未实现**的参数：传了就点名拒绝，绝不静默忽略。
        /// v0.8.32 起 `riding` 与 `targetAgent` 都已实现 ⇒ 本表**空了**（刻意保留空表结构，
        /// 给下一个"先占位、后实现"的参数用；空表的 for 循环不执行，行为零变化）。
        /// </summary>
        private static readonly string[] UnsupportedParams = new string[0];

        internal static string HandleCommand(string raw)
        {
            try
            {
                // ── 参数校验（GC3：非法输入绝不静默回落）──────────────────────────
                for (int i = 0; i < UnsupportedParams.Length; i++)
                {
                    if (Jmini.Has(raw, UnsupportedParams[i]))
                    {
                        return Fail("unsupported_param", "参数 " + UnsupportedParams[i]
                            + " 还没实现（本通道目前能做 movement / position 指定点移动 / "
                            + "target 冲锋到敌方编队 / targetAgent 攻击指定敌方单位 / "
                            + "arrangement / firing / riding 上下马） —— 不静默忽略。");
                    }
                }

                string side = Jmini.Str(raw, "side", "player").Trim().ToLowerInvariant();
                if (side != "attacker" && side != "defender" && side != "player")
                {
                    return Fail("bad_side", "side 只接受 attacker / defender / player，收到: " + side);
                }

                // 三个都可选，但**至少要给一个**（只给 detachAI 没有意义）。
                // 校验全部用 `OrderSpec`（只依赖 BCL）：在碰 `MovementOrder` / `ArrangementOrder` /
                // `FiringOrder` 这三个**静态字段会在 mission 之外把类型永久弄坏** 的 struct 之前完成。
                string movement = Jmini.Str(raw, "movement", "").Trim().ToLowerInvariant();
                string arrangement = Jmini.Str(raw, "arrangement", "").Trim().ToLowerInvariant();
                string firing = Jmini.Str(raw, "firing", "").Trim().ToLowerInvariant();
                // v0.8.32：骑乘令（`Formation.SetRidingOrder`）。三档 Free / Mount / Dismount，
                // 与 movement order **正交**（管"骑不骑"而非"去哪"）⇒ 不进三者互斥。
                string riding = Jmini.Str(raw, "riding", "").Trim().ToLowerInvariant();
                // v0.8.30：指定点移动。`position` 是 `"x,y"` 或 `"x,y,z"`（z 省略 = 0，
                // 引擎会按地面/导航网格补 Z）；解析同样放在 mission 门之前（`OrderSpec` 只依赖 BCL）。
                string positionRaw = Jmini.Str(raw, "position", "").Trim();
                float px = 0f, py = 0f, pz = 0f;
                bool wantPosition = positionRaw.Length > 0;
                if (wantPosition && !OrderSpec.TryParsePosition(positionRaw, out px, out py, out pz))
                {
                    return Fail("bad_position", "position 需要写成 \"x,y\" 或 \"x,y,z\"（英文逗号，"
                        + "单位米，各分量绝对值 ≤ " + OrderSpec.PositionLimit.ToString("0")
                        + "；z 省略 = 0，引擎会按地面/导航网格补 Z），收到: " + positionRaw);
                }
                // v0.8.31：`target` = "冲锋到某个**敌方编队**"（`MovementOrderChargeToTarget`）。
                // 口径说明：`ChargeToTarget` 绑的是 `Formation` 对象，回读有硬判据
                // （`MovementOrder.TargetFormation`）。
                string targetRaw = Jmini.Str(raw, "target", "").Trim();
                bool wantTarget = targetRaw.Length > 0;
                int targetIndex = -1;
                if (wantTarget && !OrderSpec.TryFormationIndex(targetRaw, out targetIndex))
                {
                    return Fail("bad_target", "target 只接受**敌方编队** " + OrderSpec.Join(OrderSpec.FormationNames)
                        + " 或下标 0~4，收到: " + targetRaw);
                }
                // v0.8.32：`targetAgent` = "攻击某个**敌方单位**"（`MovementOrderAttackEntity`，
                // 引擎侧 `MovementOrder.cs:417`；原版 `OrderController` 的 OrderType.AttackEntity 同款用法）。
                // 口径（本轮的决定，写给下一个会话）：**用 agent 下标**（`Agent.Index`）指定 ——
                //   调用方从遥测 / `bl_control_agent status`（同一套下标）里拿。
                //   理由：不自己"挑最像的敌人"。挑选口径一旦藏在工具里，实验就不可复现；
                //   而"这个下标是谁"调用方本来就能查到（回读里一并给 troop 名）。
                // ⚠️ 它和 target（编队目标）**同属 movement order**（都往 `Formation.SetMovementOrder` 写）
                //   ⇒ 参与 movement / position / target / targetAgent **四者互斥**。
                // ⚠️ 已知代价（如实标注）：目标**会死** —— 死后的重申会跳过并记 order error（不静默改令）。
                bool wantTargetAgentArg = Jmini.Has(raw, "targetAgent");
                int targetAgentIndex = Jmini.Int(raw, "targetAgent", -1);
                if (wantTargetAgentArg
                    && (targetAgentIndex < 0 || targetAgentIndex > OrderSpec.AgentIndexLimit))
                {
                    return Fail("bad_target_agent", "targetAgent 需要是**敌方单位的 agent 下标**"
                        + "（正整数，0 ~ " + OrderSpec.AgentIndexLimit.ToString(CultureInfo.InvariantCulture)
                        + "；可从遥测或 bl_control_agent status 的 candidates 里取），收到: "
                        + (targetAgentIndex < 0 ? "(非整数或负数)" : targetAgentIndex.ToString(CultureInfo.InvariantCulture)));
                }
                bool wantTargetAgent = wantTargetAgentArg && targetAgentIndex >= 0;
                bool wantMove = movement.Length > 0;
                bool wantArrange = arrangement.Length > 0;
                bool wantFire = firing.Length > 0;
                bool wantRiding = riding.Length > 0;
                // movement / position / target 都往同一个 `Formation.SetMovementOrder` 写 ⇒ 最多给一个。
                // 做成"数一遍再报哪几个冲突"（而不是三条两两判断）：以后再加一种 movement order 时
                // 不会漏配某一条组合（v0.8.30 就是三条两两判，加 target 时才发现会漏）。
                int orderKindCount = (wantMove ? 1 : 0) + (wantPosition ? 1 : 0)
                                   + (wantTarget ? 1 : 0) + (wantTargetAgent ? 1 : 0);
                if (orderKindCount > 1)
                {
                    StringBuilder given = new StringBuilder();
                    if (wantMove) given.Append("movement ");
                    if (wantPosition) given.Append("position ");
                    if (wantTarget) given.Append("target ");
                    if (wantTargetAgent) given.Append("targetAgent ");
                    return Fail("bad_request", "movement / position / target / targetAgent **四者互斥**"
                        + "（一个编队只能有一个 movement order），收到: " + given.ToString().Trim()
                        + "。movement: " + OrderSpec.Join(OrderSpec.MovementNames)
                        + "｜position: \"x,y[,z]\""
                        + "｜target: 敌方编队名或下标（" + OrderSpec.Join(OrderSpec.FormationNames) + "）"
                        + "｜targetAgent: 敌方单位的 agent 下标（整数）");
                }
                if (!wantMove && !wantArrange && !wantFire && !wantPosition && !wantTarget
                    && !wantTargetAgent && !wantRiding)
                {
                    return Fail("bad_request", "movement / position / target / targetAgent / arrangement / "
                        + "firing / riding 至少要给一个。"
                        + "movement: " + OrderSpec.Join(OrderSpec.MovementNames)
                        + "｜position: \"x,y[,z]\""
                        + "｜target: 敌方编队名或下标"
                        + "｜targetAgent: 敌方单位 agent 下标"
                        + "｜arrangement: " + OrderSpec.Join(OrderSpec.ArrangementNames)
                        + "｜firing: " + OrderSpec.Join(OrderSpec.FiringNames)
                        + "｜riding: " + OrderSpec.Join(OrderSpec.RidingNames));
                }
                if (wantMove && !OrderSpec.IsMovement(movement))
                {
                    return Fail("bad_movement", "movement 只接受 " + OrderSpec.Join(OrderSpec.MovementNames)
                        + "，收到: " + movement
                        + (movement == "hold"
                           ? "（hold 已在 v0.8.8 移除：引擎层它本就等同 stop，请改用 stop）" : ""));
                }
                if (wantArrange && !OrderSpec.IsArrangement(arrangement))
                {
                    return Fail("bad_arrangement", "arrangement 只接受 "
                        + OrderSpec.Join(OrderSpec.ArrangementNames) + "，收到: " + arrangement);
                }
                if (wantFire && !OrderSpec.IsFiring(firing))
                {
                    return Fail("bad_firing", "firing 只接受 " + OrderSpec.Join(OrderSpec.FiringNames)
                        + "（引擎只有这两档：自由射击 / 停火），收到: " + firing);
                }
                if (wantRiding && !OrderSpec.IsRiding(riding))
                {
                    return Fail("bad_riding", "riding 只接受 " + OrderSpec.Join(OrderSpec.RidingNames)
                        + "（引擎只有这三档：free 不干预 / mount 上马 / dismount 下马），收到: " + riding);
                }

                string formationArg = Jmini.Str(raw, "formation", "").Trim();
                bool detachAI = Jmini.Bool(raw, "detachAI", true);

                // formation 的**纯语法**校验也放在 mission 门之前：这样"没战斗"与"编队名拼错"不会
                // 互相遮蔽（真机教训：先判 mission 时，formation=Infantryy 会被报成 no_mission，
                // 调用方要修两次才知道第二个错误）。纯字符串判断，不碰任何引擎类型。
                int formationIndex = -1;
                if (formationArg.Length > 0 && !OrderSpec.TryFormationIndex(formationArg, out formationIndex))
                {
                    return Fail("bad_formation", "formation 只接受 "
                        + OrderSpec.Join(OrderSpec.FormationNames) + " 或下标 0~4，收到: " + formationArg);
                }

                // ── mission 硬门（先于任何 MovementOrder 访问）────────────────────
                Mission mission = Mission.Current;
                if (mission == null)
                {
                    return Fail("no_mission", "当前没有战斗在跑（Mission.Current == null）⇒ 拒绝改令。"
                        + "不是保守：MovementOrder 的静态字段在 mission 之外被碰会抛 TypeInitializationException，"
                        + "并把该类型永久标记为不可用（同进程内之后所有改令都坏）。"
                        + "请先开一场战斗（start / 游戏内官方自定义战斗界面）再改令。");
                }

                Team team;
                if (side == "attacker") team = mission.AttackerTeam;
                else if (side == "defender") team = mission.DefenderTeam;
                else team = mission.PlayerTeam;
                if (team == null)
                {
                    return Fail(side == "player" ? "no_player_team" : "no_team",
                        "拿不到 " + side + " 方的 Team（这一方不存在；player 要等 mission 起来后才有玩家侧）");
                }

                // 指定点移动要拿 Scene 才能构造 `WorldPosition`（`new WorldPosition(scene, vec3)`）。
                // 同样放在碰 `MovementOrder` **之前**判：少一个前置条件就多一条"看着像成功了"的路径。
                Scene scene = null;
                if (wantPosition)
                {
                    scene = mission.Scene;
                    if (scene == null)
                    {
                        return Fail("no_scene", "拿不到当前战斗的 Scene（Mission.Scene == null）"
                            + " ⇒ 没法构造 WorldPosition，指定点移动拒绝执行（不静默降级成别的令）。");
                    }
                }

                // v0.8.31：`target` 要先把**敌方**那个编队拿到手（同样是"先判前置条件、再碰引擎"）。
                // 三个拒绝理由分开报，别混成一个 —— 调用方要一眼看出是"没有敌方"还是"目标编队空着"。
                // v0.8.32：`targetAgent` 同样要敌方 Team ⇒ 两者共用这段解析（但目标校验各管各的）。
                Team enemyTeam = null;
                Formation targetFormation = null;
                if (wantTarget || wantTargetAgent)
                {
                    if (side == "attacker") enemyTeam = mission.DefenderTeam;
                    else if (side == "defender") enemyTeam = mission.AttackerTeam;
                    else
                    {
                        enemyTeam = ReferenceEquals(mission.PlayerTeam, mission.AttackerTeam)
                            ? mission.DefenderTeam : mission.AttackerTeam;
                    }
                    if (enemyTeam == null || ReferenceEquals(enemyTeam, team))
                    {
                        return Fail("no_enemy_team", "拿不到 " + side + " 方的敌方 Team"
                            + "（本场不是攻守两方，或 player 侧还没确定）⇒ "
                            + (wantTarget ? "target" : "targetAgent") + " 拒绝执行");
                    }
                }
                if (wantTarget)
                {
                    try
                    {
                        targetFormation = enemyTeam.GetFormation((FormationClass)targetIndex);
                    }
                    catch (Exception ex)
                    {
                        return Fail("bad_target", "取敌方编队 " + targetRaw + " 抛异常："
                            + ex.GetType().Name + ": " + ex.Message);
                    }
                    if (targetFormation == null)
                    {
                        return Fail("no_target_formation", "敌方（" + SideLabel(mission, enemyTeam)
                            + "）没有 " + targetRaw + " 这个编队（GetFormation 返回 null）");
                    }
                    int targetCount;
                    try { targetCount = targetFormation.CountOfUnits; }
                    catch { targetCount = -1; }
                    if (targetCount == 0)
                    {
                        return Fail("target_formation_empty", "敌方（" + SideLabel(mission, enemyTeam)
                            + "）的 " + targetRaw + " 编队是**空的**（CountOfUnits=0）⇒ 冲锋到空编队没有意义"
                            + "（多半是它已经被打光了），拒绝执行 —— 不静默换成普通冲锋。");
                    }
                }
                // v0.8.32：指定目标单位 —— 先按 `Agent.Index` 找到它，**并把"为什么不可用"分开报**
                // （没有这个下标 / 不是敌方 / 已阵亡），别混成一句"找不到"。
                Agent targetAgentObj = null;
                TaleWorlds.Engine.GameEntity targetAgentEntity = null;
                if (wantTargetAgent)
                {
                    string aCode, aWhy;
                    if (!TryResolveTargetAgent(mission, enemyTeam, targetAgentIndex,
                                               out targetAgentObj, out aCode, out aWhy))
                    {
                        return Fail(aCode, aWhy);
                    }
                    targetAgentEntity = AgentEntity(targetAgentObj);
                    if (targetAgentEntity == null)
                    {
                        return Fail("target_agent_no_entity", "目标单位（agent 下标 " + targetAgentIndex
                            + "）没有可用的 GameEntity（AgentVisuals 为空 / GetEntity() 回了 null）⇒ "
                            + "`MovementOrderAttackEntity` 没有实体可绑，拒绝执行 —— 不静默降级成别的令。");
                    }
                }

                // ── 目标编队 ────────────────────────────────────────────────────
                List<int> targets = new List<int>();
                if (formationIndex < 0)
                {
                    foreach (Formation f in team.FormationsIncludingEmpty)
                    {
                        if (f == null) continue;
                        int cnt;
                        try { cnt = f.CountOfUnits; }
                        catch { cnt = 0; }
                        if (cnt > 0) targets.Add((int)f.LogicalClass);
                    }
                    if (targets.Count == 0)
                    {
                        return Fail("no_target", side + " 方当前没有任何有兵的编队"
                            + "（FormationsIncludingEmpty 全空 ⇒ 没什么可改的）");
                    }
                }
                else
                {
                    targets.Add(formationIndex);
                }

                // 到这里才碰这三个 struct（mission 已确认存在）。
                // movement 走**同一个**映射器（`ScenarioRunner.ScenarioProbe.MapMovement`），
                // 不另写一份 switch —— 两份映射漂移会让"开战能写、中途改不了"。
                MovementOrder order = wantMove
                    ? ScenarioRunner.ScenarioProbe.MapMovement(movement)
                    : default(MovementOrder);
                // v0.8.30：指定点移动。引擎侧是 `MovementOrder.MovementOrderMove(WorldPosition)`，
                // 构造 WorldPosition 必须带当前 Scene（否则 `IsValid` 为 false、落点无意义）。
                MovementOrder moveToPos = wantPosition
                    ? MovementOrder.MovementOrderMove(new WorldPosition(scene, new Vec3(px, py, pz)))
                    : default(MovementOrder);
                // v0.8.31：指定目标 —— 引擎侧 `MovementOrder.MovementOrderChargeToTarget(Formation)`，
                // `OnApply` 会 `formation.SetTargetFormation(targetFormation)`（读码确认），
                // 所以回读有硬判据：`MovementOrder.TargetFormation`。
                MovementOrder chargeTarget = wantTarget
                    ? MovementOrder.MovementOrderChargeToTarget(targetFormation)
                    : default(MovementOrder);
                // v0.8.32：指定目标单位 —— 引擎侧 `MovementOrder.MovementOrderAttackEntity(GameEntity, bool)`
                // （`MovementOrder.cs:417`；`surroundEntity=true` = 包围它，原版对非城门实体也是 true）。
                // 回读判据：`OrderEnum == AttackEntity` + `TargetEntity != null`（`MovementOrder.cs:83` 是
                // public 字段）+ 行为判据（编队重心与目标单位的距离在缩小）。
                MovementOrder attackAgentOrder = wantTargetAgent
                    ? MovementOrder.MovementOrderAttackEntity(targetAgentEntity, true)
                    : default(MovementOrder);
                ArrangementOrder arrangeOrder = wantArrange
                    ? MapArrangement(arrangement) : default(ArrangementOrder);
                FiringOrder fireOrder = wantFire ? MapFiring(firing) : default(FiringOrder);
                // v0.8.32：骑乘令。与上面三个一样，映射放在 mission 门之后
                // （`RidingOrder` 的静态字段同样是"类型初始化时构造"的那种 struct，先校验后触碰这条纪律照旧）。
                RidingOrder ridingOrder = wantRiding ? MapRidingOrder(riding) : default(RidingOrder);

                StringBuilder applied = new StringBuilder();
                int okCount = 0;
                int totalUnits = 0;
                int pendingUpdated = 0;   // 同步改掉的"待重申 spec"个数（见下面的注释）
                int emptyFormations = 0;  // ok=true 但**该编队一个人都没有**的个数（见空编队那段注释）
                for (int i = 0; i < targets.Count; i++)
                {
                    int idx = targets[i];
                    string label = EnumNames.Formation((FormationClass)idx);
                    Formation f = null;
                    string before = "(n/a)";
                    string after = "(no-formation)";
                    string arrBefore = "(n/a)", arrAfter = "(n/a)";
                    string fireBefore = "(n/a)", fireAfter = "(n/a)";
                    string rideBefore = "(n/a)", rideAfter = "(n/a)";   // v0.8.32：骑乘令回读
                    string moveTarget = "(n/a)";   // v0.8.30：指定点移动下发后，从 order 里回读的目标点
                    string center = "(n/a)";       // v0.8.30：该编队当前重心（行为判据，见 CenterOf）
                    string targetAfter = "(n/a)";  // v0.8.31：指定目标下发后，从 order 里回读的目标编队
                    string targetDist = "(n/a)";   // v0.8.31：与目标编队的重心距离（行为判据）
                    string taEntitySet = "(n/a)";  // v0.8.32：order 里的 TargetEntity 是否还在（回读）
                    string taDist = "(n/a)";       // v0.8.32：编队重心 ↔ 目标单位的距离（行为判据）
                    string taAlive = "(n/a)";      // v0.8.32：目标单位此刻是否还活着
                    string taTroop = "(n/a)";      // v0.8.32：目标单位的兵种名（下标容易认错人，回显一下）
                    int count = -1;
                    bool appliedOk = false;
                    try
                    {
                        f = team.GetFormation((FormationClass)idx);
                    }
                    catch (Exception ex)
                    {
                        after = "(unavailable: " + ex.GetType().Name + ")";
                        f = null;
                    }
                    if (f != null)
                    {
                        before = OrderName(f);
                        try { arrBefore = f.ArrangementOrder.OrderEnum.ToString(); }
                        catch { arrBefore = "(unknown)"; }
                        try { fireBefore = f.FiringOrder.OrderEnum.ToString(); }
                        catch { fireBefore = "(unknown)"; }
                        try { rideBefore = f.RidingOrder.OrderEnum.ToString(); }
                        catch { rideBefore = "(unknown)"; }

                        if (wantMove) f.SetMovementOrder(order);
                        if (wantPosition) f.SetMovementOrder(moveToPos);
                        if (wantTarget) f.SetMovementOrder(chargeTarget);
                        if (wantTargetAgent) f.SetMovementOrder(attackAgentOrder);
                        // 连带关掉该编队的 order 级 AI 驱动（理由见类注释第 2 条）
                        if (detachAI) f.SetControlledByAI(false, false);
                        // 阵列 / 射击纪律：**不在**组路径那 0.5s 重申的范围内（重申只管 movement）
                        if (wantArrange)
                        {
                            f.SetArrangementOrder(arrangeOrder);
                            try { arrAfter = f.ArrangementOrder.OrderEnum.ToString(); }
                            catch { arrAfter = "(unknown)"; }
                        }
                        if (wantFire)
                        {
                            f.SetFiringOrder(fireOrder);
                            try { fireAfter = f.FiringOrder.OrderEnum.ToString(); }
                            catch { fireAfter = "(unknown)"; }
                        }
                        // v0.8.32：骑乘令。注意 `Formation.SetRidingOrder` 内部有 `if (RidingOrder != order)`
                        // 守卫（`Formation.cs:771-779`）——值本来就相同时它**什么也不做**，回读仍等于该值，
                        // 这不是"没生效"而是"已经是这个值"。所以判据只看回读相不相等，不看"有没有动"。
                        if (wantRiding)
                        {
                            f.SetRidingOrder(ridingOrder);
                            try { rideAfter = f.RidingOrder.OrderEnum.ToString(); }
                            catch { rideAfter = "(unknown)"; }
                        }

                        after = OrderName(f);
                        // 重心是 position / target / targetAgent 三条的**行为**判据（见 CenterOf 的 doc）。
                        if (wantPosition || wantTarget || wantTargetAgent) center = CenterOf(f);
                        if (wantPosition)
                        {
                            moveTarget = TargetPoint(f);
                        }
                        if (wantTarget)
                        {
                            targetAfter = TargetFormationName(f);
                            targetDist = DistanceBetween(f, targetFormation);
                        }
                        if (wantTargetAgent)
                        {
                            taEntitySet = TargetEntityPresent(f);
                            taDist = DistanceToAgent(f, targetAgentObj);
                            taAlive = AgentAlive(targetAgentObj);
                            taTroop = AgentLabel(targetAgentObj);
                        }
                        try { count = f.CountOfUnits; }
                        catch { count = -1; }
                        appliedOk = true;
                        // ⚠️ 关键一步（v0.8.23 真机踩到）：组路径每 0.5s 重申各组 movement，
                        // 只改 `Formation` 会在半秒内被**我们自己**改回去 ⇒ 必须把"待重申的值"一起改。
                        // v0.8.30/0.8.31：指定点 / 指定目标都没有"名字"可重申 ⇒ 走 `ManualKind` 标记那条路
                        // （重申时照原样重申同一个点）；两条路互斥，一个编队只会有其中一个。
                        try
                        {
                            if (wantMove)
                            {
                                pendingUpdated += ScenarioRunner.ScenarioProbe.OverridePendingMovement(
                                    team, idx, movement);
                            }
                            else if (wantPosition)
                            {
                                pendingUpdated += ScenarioRunner.ScenarioProbe.OverridePendingMoveToPosition(
                                    team, idx, px, py, pz);
                            }
                            else if (wantTarget)
                            {
                                pendingUpdated += ScenarioRunner.ScenarioProbe.OverridePendingChargeTarget(
                                    team, idx, targetIndex);
                            }
                            else if (wantTargetAgent)
                            {
                                pendingUpdated += ScenarioRunner.ScenarioProbe.OverridePendingAttackAgent(
                                    team, idx, targetAgentIndex);
                            }
                        }
                        catch
                        {
                        }
                    }

                    if (applied.Length > 0) applied.Append(',');
                    applied.Append("{\"formation\":").Append(Protocol.Q(label));
                    applied.Append(",\"index\":").Append(Jw.N(idx));
                    applied.Append(",\"count\":").Append(Jw.N(count));
                    applied.Append(",\"orderBefore\":").Append(Protocol.Q(before));
                    applied.Append(",\"orderAfter\":").Append(Protocol.Q(after));
                    if (wantArrange)
                    {
                        applied.Append(",\"arrangementBefore\":").Append(Protocol.Q(arrBefore));
                        applied.Append(",\"arrangementAfter\":").Append(Protocol.Q(arrAfter));
                    }
                    if (wantFire)
                    {
                        applied.Append(",\"firingBefore\":").Append(Protocol.Q(fireBefore));
                        applied.Append(",\"firingAfter\":").Append(Protocol.Q(fireAfter));
                    }
                    if (wantRiding)
                    {
                        applied.Append(",\"ridingBefore\":").Append(Protocol.Q(rideBefore));
                        applied.Append(",\"ridingAfter\":").Append(Protocol.Q(rideAfter));
                    }
                    if (wantPosition)
                    {
                        applied.Append(",\"moveTarget\":").Append(Protocol.Q(moveTarget));
                        applied.Append(",\"formationCenter\":").Append(Protocol.Q(center));
                    }
                    if (wantTarget)
                    {
                        applied.Append(",\"targetAfter\":").Append(Protocol.Q(targetAfter));
                        applied.Append(",\"formationCenter\":").Append(Protocol.Q(center));
                        applied.Append(",\"targetDistance\":").Append(Protocol.Q(targetDist));
                    }
                    if (wantTargetAgent)
                    {
                        applied.Append(",\"targetAgentIndex\":").Append(Jw.N(targetAgentIndex));
                        applied.Append(",\"targetAgentTroop\":").Append(Protocol.Q(taTroop));
                        applied.Append(",\"targetAgentAlive\":").Append(Protocol.Q(taAlive));
                        applied.Append(",\"targetEntitySet\":").Append(Protocol.Q(taEntitySet));
                        applied.Append(",\"formationCenter\":").Append(Protocol.Q(center));
                        applied.Append(",\"targetDistance\":").Append(Protocol.Q(taDist));
                    }
                    applied.Append(",\"aiDetachRequested\":").Append(Jw.B(detachAI));
                    // v0.8.31：空编队单独点名（真机踩到自己：把守方的 `formation=Infantry` 当成"那 12 个芬恩
                    // 勇士"，其实芬恩是弓手、在 Ranged，Infantry 是**空**的 —— 令照样写进去、`ok=true`、
                    // 但**没人执行**。这正是本项目最忌讳的"看着成功其实没生效"，所以给它一个显式信号。
                    if (appliedOk && count == 0) applied.Append(",\"emptyFormation\":true");
                    applied.Append('}');

                    if (appliedOk)
                    {
                        okCount++;
                        if (count > 0) totalUnits += count;
                        else emptyFormations++;
                    }
                }

                bool ok = okCount > 0;
                string note;
                if (!ok)
                {
                    note = "一个编队都没改成（见 applied 每条的原因）";
                }
                else if (pendingUpdated > 0)
                {
                    note = "已当场回读确认（" + ReadbackField(wantPosition, wantTarget, wantTargetAgent)
                        + "）；并同步改了 "
                        + pendingUpdated
                        + " 个待重申组，所以组路径那 0.5 秒一次的周期重申会跟着重申**这个新值**"
                        + "（真机教训：不同步改它，半秒内就会被我们自己改回原值）。"
                        + "要确认真的稳，隔几秒再调一次看 orderBefore 是否仍是这次设的值"
                        + (wantPosition ? "、moveTarget 是否还在那个点" : "")
                        + (wantTarget ? "、targetAfter 是否还是同一个编队" : "")
                        + (wantTargetAgent ? "、targetAgentAlive 是否还是 true" : "")
                        + (detachAI ? "" : "（detachAI=false ⇒ 更容易被 team 战术覆盖）") + "。";
                }
                else
                {
                    note = "已当场回读确认（" + ReadbackField(wantPosition, wantTarget, wantTargetAgent)
                        + "），但**没有**命中组路径的重申集合"
                        + "（该方不是按组开的战、或这个编队上没有组）⇒ 这个令可能被该方 team 级战术改回去；"
                        + "隔几秒再调一次看 orderBefore 就能判定，不猜。";
                }
                if (wantArrange || wantFire || wantRiding)
                {
                    note += " 阵列 / 射击纪律 / 骑乘令**不在**组路径那 0.5 秒一次的重申范围里"
                        + "（重申只管 movement）；要确认稳不稳，隔几秒再读一次看 "
                        + "arrangementBefore / firingBefore / ridingBefore。";
                }
                if (wantRiding)
                {
                    note += " 骑乘令的判据是**回读** ridingAfter（引擎 `RidingOrder.RidingOrderEnum`："
                        + "Free = 不干预 / Mount = 上马 / Dismount = 下马）；它只对**有坐骑**的单位有实际效果"
                        + "（纯步兵编队下发不会报错，但有没有行为变化本版未单独验证，别据此下结论）。";
                }
                if (wantPosition)
                {
                    note += " 指定点移动的落点来自当场回读（moveTarget = 下发的那个 order 里 `GetPosition` 的"
                        + "结果，引擎会把不在导航网格上的点挪到最近的合法位置）⇒ 与请求值有偏差是**引擎的修正**，"
                        + "不是我们没写进去；坐标单位米、z 省略时为 0（由引擎按地面补 Z）。";
                }
                if (ok && emptyFormations > 0)
                {
                    note += " ⚠️ 有 " + emptyFormations + " 个编队**当前一个人都没有**"
                        + "（applied 里带 `emptyFormation:true`、`count=0`、`formationCenter=(invalid)`）："
                        + "令确实写进去了，但**没人执行**。编队是按**兵种**自动分的（弓手/弩手在 Ranged、"
                        + "近战步兵在 Infantry、骑兵在 Cavalry…），先核对 formation 是不是选错了。";
                }
                if (wantTarget)
                {
                    note += " 指定目标走的是 `MovementOrderChargeToTarget`（引擎 `OnApply` 里会"
                        + "`SetTargetFormation`）⇒ orderAfter 是 `ChargeToTarget`、targetAfter 是回读到的目标编队；"
                        + "targetDistance 是**两个编队重心的距离**（行为判据：隔几秒再调一次，它应该在缩小）。"
                        + "目标编队被打光/变空之后重申会**跳过**（记一条 order error），"
                        + "不会偷偷退回成普通冲锋。";
                }
                if (wantTargetAgent)
                {
                    note += " 指定目标单位走的是 `MovementOrder.MovementOrderAttackEntity(GameEntity, true)`"
                        + "（`surroundEntity=true` = 包围它；`MovementOrder.cs:417`）⇒ orderAfter 是 "
                        + "`AttackEntity`、targetEntitySet 是回读 `MovementOrder.TargetEntity` 是否还记着那个实体；"
                        + "targetDistance 是**编队重心到那个单位**的距离（行为判据：隔几秒再调一次，它应在缩小）。"
                        + "⚠️ 目标**会死**（这正是当初「只做编队目标」的顾虑）：阵亡后重申会**跳过**并记一条 "
                        + "order error，不会偷偷退回成普通冲锋；要换目标就再发一次。";
                }

                return "{\"ok\":" + Jw.B(ok)
                       + ",\"side\":" + Protocol.Q(side)
                       + ",\"team\":" + Protocol.Q(TeamLabel(mission, team, side))
                       + ",\"movement\":" + Protocol.Q(movement)
                       + (wantPosition ? ",\"position\":{\"x\":" + Jw.N(px) + ",\"y\":" + Jw.N(py)
                                          + ",\"z\":" + Jw.N(pz) + "}" : "")
                       + (wantTarget ? ",\"target\":" + Protocol.Q(EnumNames.Formation((FormationClass)targetIndex))
                                          + ",\"targetSide\":" + Protocol.Q(SideLabel(mission, enemyTeam))
                                     : "")
                       + (wantTargetAgent ? ",\"targetAgent\":" + Jw.N(targetAgentIndex)
                                               + ",\"targetAgentTroop\":" + Protocol.Q(AgentLabel(targetAgentObj))
                                               + ",\"targetSide\":" + Protocol.Q(SideLabel(mission, enemyTeam))
                                          : "")
                       + (wantArrange ? ",\"arrangement\":" + Protocol.Q(arrangement) : "")
                       + (wantFire ? ",\"firing\":" + Protocol.Q(firing) : "")
                       + (wantRiding ? ",\"riding\":" + Protocol.Q(riding) : "")
                       + ",\"detachAI\":" + Jw.B(detachAI)
                       + ",\"appliedCount\":" + Jw.N(okCount)
                       + ",\"totalUnits\":" + Jw.N(totalUnits)
                       + ",\"emptyFormations\":" + Jw.N(emptyFormations)
                       + ",\"pendingSpecsUpdated\":" + Jw.N(pendingUpdated)
                       + ",\"applied\":[" + applied.ToString() + "]"
                       + ",\"note\":" + Protocol.Q(note) + "}";
            }
            catch (Exception ex)
            {
                return Fail("order_failed", ex.GetType().Name + ": " + ex.Message);
            }
        }

        /// <summary>
        /// 阵列名 → `ArrangementOrder`（mission 内才安全：与 `MovementOrder` 同样的静态字段类型初始化陷阱）。
        /// 名字表在 `OrderSpec.ArrangementNames`（校验在前，所以这里的 default 只是兜底）。
        /// </summary>
        internal static ArrangementOrder MapArrangement(string arrangement)
        {
            switch (arrangement)
            {
                case "shieldwall": return ArrangementOrder.ArrangementOrderShieldWall;
                case "circle": return ArrangementOrder.ArrangementOrderCircle;
                case "square": return ArrangementOrder.ArrangementOrderSquare;
                case "skein": return ArrangementOrder.ArrangementOrderSkein;
                case "column": return ArrangementOrder.ArrangementOrderColumn;
                case "loose": return ArrangementOrder.ArrangementOrderLoose;
                case "scatter": return ArrangementOrder.ArrangementOrderScatter;
                default: return ArrangementOrder.ArrangementOrderLine;   // line
            }
        }

        /// <summary>
        /// 射击纪律 → `FiringOrder`。引擎**只有两档**：FireAtWill / HoldYourFire
        /// （`FiringOrder.cs` 里的 `RangedWeaponUsageOrderEnum`）。
        /// </summary>
        internal static FiringOrder MapFiring(string firing)
        {
            // ⚠️ 真机踩到（2026-09-26）：入参在校验阶段被 `ToLowerInvariant()` 过一遍，
            // 这里若写成 `firing == "holdFire"` 就**永远不成立** ⇒ holdFire 被静默当成 fireAtWill
            // （回读 firingBefore/After 都是 FireAtWill，靠回读才抓出来）。必须忽略大小写。
            return string.Equals(firing, "holdFire", StringComparison.OrdinalIgnoreCase)
                ? FiringOrder.FiringOrderHoldYourFire
                : FiringOrder.FiringOrderFireAtWill;
        }

        /// <summary>
        /// v0.8.32：骑乘令 → `RidingOrder`。引擎**只有三档**
        /// （`RidingOrder.cs:5-10`：Free / Mount / Dismount；静态字段 `RidingOrderFree/Mount/Dismount`）。
        ///
        /// 与 `MovementOrder` 同样是"静态字段在类型初始化时构造"的 struct ⇒ 本方法**只允许在 mission 内调用**
        /// （mission 门在前）；名字表与校验在只依赖 BCL 的 `OrderSpec.IsRiding`，所以这里只是映射，不再校验。
        ///
        /// 大小写：入参在校验阶段已被 `ToLowerInvariant()`（与 `MapFiring` 同一个坑 —— 那里曾写成
        /// `firing == "holdFire"` 而永远不成立，靠回读才揪出来）⇒ 这里统一用 `OrdinalIgnoreCase` 比较。
        /// </summary>
        internal static RidingOrder MapRidingOrder(string riding)
        {
            if (string.Equals(riding, "mount", StringComparison.OrdinalIgnoreCase))
                return RidingOrder.RidingOrderMount;
            if (string.Equals(riding, "dismount", StringComparison.OrdinalIgnoreCase))
                return RidingOrder.RidingOrderDismount;
            return RidingOrder.RidingOrderFree;
        }

        /// <summary>给 note 用：这一次的"判据字段"到底叫什么（避免 note 里写死 orderAfter 而漏了别的回读）。</summary>
        private static string ReadbackField(bool wantPosition, bool wantTarget, bool wantTargetAgent)
        {
            if (wantPosition) return "orderAfter=Move + moveTarget";
            if (wantTarget) return "orderAfter=ChargeToTarget + targetAfter";
            if (wantTargetAgent) return "orderAfter=AttackEntity + targetEntitySet/targetAgentAlive";
            return "orderAfter";
        }

        /// <summary>
        /// v0.8.31：从**已下发**的 order 里回读目标编队名（`MovementOrder.TargetFormation`）。
        /// 与 `moveTarget` 同一口径：不靠"我们调了 API"，靠 order 自己记着的那个目标。
        /// 绝不抛。
        /// </summary>
        private static string TargetFormationName(Formation f)
        {
            try
            {
                MovementOrder mo = f.GetReadonlyMovementOrderReference();
                Formation t = mo.TargetFormation;
                if (t == null) return "(none)";
                return EnumNames.Formation(t.LogicalClass);
            }
            catch
            {
                return "(unknown)";
            }
        }

        /// <summary>
        /// v0.8.32：按 `Agent.Index` 找目标单位，并把"为什么不能当目标"**分开报**
        /// （没这个下标 / 不是敌方 / 已阵亡）—— 绝不混成一句"找不到"，否则调用方要猜着改。
        /// `enemyTeam` 由调用方先算好（为 null ⇒ 直接拒，不自己另算一个敌方）。
        /// 绝不抛。
        /// </summary>
        private static bool TryResolveTargetAgent(Mission mission, Team enemyTeam, int index,
                                                 out Agent agent, out string code, out string why)
        {
            agent = null;
            code = null;
            why = null;
            try
            {
                if (mission == null || enemyTeam == null)
                {
                    code = "no_enemy_team";
                    why = "拿不到敌方 Team ⇒ targetAgent 拒绝执行";
                    return false;
                }
                Agent sameIndex = null;
                foreach (Agent a in mission.Agents)
                {
                    if (a == null) continue;
                    if (a.Index != index) continue;
                    sameIndex = a;
                    break;
                }
                if (sameIndex == null)
                {
                    code = "no_target_agent";
                    why = "当前战斗里没有 agent 下标 " + index
                        + "（下标按场次重号，换一场要重新取）";
                    return false;
                }
                if (!ReferenceEquals(sameIndex.Team, enemyTeam))
                {
                    code = "target_agent_not_enemy";
                    why = "agent 下标 " + index + " 不在敌方（本通道只接受**敌方单位**当目标）";
                    return false;
                }
                if (!sameIndex.IsActive())
                {
                    code = "target_agent_inactive";
                    why = "agent 下标 " + index + " 已阵亡/未激活（IsActive() == false）⇒ 攻击它没有意义";
                    return false;
                }
                agent = sameIndex;
                return true;
            }
            catch (Exception ex)
            {
                code = "target_agent_failed";
                why = "找目标单位时抛异常：" + ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        /// <summary>
        /// v0.8.32：agent → `GameEntity`（`Agent.AgentVisuals.GetEntity()`，`MBAgentVisuals.cs:46`）。
        /// 没有渲染体 ⇒ null（调用方据此拒，不静默降级）。绝不抛。
        /// </summary>
        private static TaleWorlds.Engine.GameEntity AgentEntity(Agent agent)
        {
            try
            {
                if (agent == null) return null;
                MBAgentVisuals av = agent.AgentVisuals;
                if (av == null) return null;
                return av.GetEntity();
            }
            catch
            {
                return null;
            }
        }

        /// <summary>v0.8.32：目标单位的可读标签（`兵种 id#下标`）—— 下标很容易认错人，回显一下。绝不抛。</summary>
        private static string AgentLabel(Agent agent)
        {
            try
            {
                if (agent == null) return "(none)";
                string id = null;
                try { id = agent.Character == null ? null : agent.Character.StringId; }
                catch { }
                if (string.IsNullOrEmpty(id)) id = "(unknown)";
                return id + "#" + agent.Index;
            }
            catch
            {
                return "(unknown)";
            }
        }

        /// <summary>v0.8.32：目标单位此刻是否还活着。绝不抛。</summary>
        private static string AgentAlive(Agent agent)
        {
            try
            {
                if (agent == null) return "(none)";
                return agent.IsActive() ? "true" : "false";
            }
            catch
            {
                return "(unknown)";
            }
        }

        /// <summary>
        /// v0.8.32：回读**下发的那个 order 里**记着的目标实体是否还在（`MovementOrder.TargetEntity`，
        /// `MovementOrder.cs:83` 是 public 字段）。与 `orderAfter` 同一口径：不靠"我们调了 API"。
        /// 绝不抛。
        /// </summary>
        private static string TargetEntityPresent(Formation f)
        {
            try
            {
                MovementOrder mo = f.GetReadonlyMovementOrderReference();
                return mo.TargetEntity != null ? "true" : "false";
            }
            catch
            {
                return "(unknown)";
            }
        }

        /// <summary>
        /// v0.8.32：编队重心 ↔ 目标单位当前位置的距离（米，一位小数）。**行为判据** ——
        /// `orderAfter=AttackEntity` 只证明"令写进去了"，不证明"兵在往它那儿去"。
        /// 目标已阵亡 ⇒ "(dead)"（不再有意义的读数，不假装还能算）。绝不抛。
        /// </summary>
        private static string DistanceToAgent(Formation f, Agent agent)
        {
            try
            {
                if (f == null || agent == null) return "(none)";
                if (!agent.IsActive()) return "(dead)";
                Vec2 c = f.GetAveragePositionOfUnits(true, false);
                if (!c.IsValid) return "(invalid)";
                Vec3 p = agent.Position;
                double dx = c.x - p.x;
                double dy = c.y - p.y;
                double d = Math.Sqrt(dx * dx + dy * dy);
                return d.ToString("0.0", CultureInfo.InvariantCulture) + " m";
            }
            catch
            {
                return "(unknown)";
            }
        }

        /// <summary>
        /// v0.8.31：两个编队**重心**之间的距离（米，一位小数）。给 `target` 当行为判据 ——
        /// `orderAfter=ChargeToTarget` 只证明"令写进去了"，不证明"兵在冲过去"；
        /// 隔几秒再调一次，这个数应该在缩小。任一侧取不到重心 ⇒ "(unknown)"。绝不抛。
        /// </summary>
        private static string DistanceBetween(Formation a, Formation b)
        {
            try
            {
                if (a == null || b == null) return "(none)";
                Vec2 pa = a.GetAveragePositionOfUnits(true, false);
                Vec2 pb = b.GetAveragePositionOfUnits(true, false);
                if (!pa.IsValid || !pb.IsValid) return "(invalid)";
                double dx = pa.x - pb.x;
                double dy = pa.y - pb.y;
                double d = Math.Sqrt(dx * dx + dy * dy);
                return d.ToString("0.0", CultureInfo.InvariantCulture) + " m";
            }
            catch
            {
                return "(unknown)";
            }
        }

        /// <summary>
        /// v0.8.30：从**已下发**的 order 里回读目标点（`MovementOrder.GetPosition(f)`）。
        /// 这是"指定点移动到底写到哪了"的唯一硬判据 —— 与 `orderBefore`/`orderAfter` 同一口径：
        /// 不靠"我们调了 API"，靠引擎自己算出来的落点。
        /// 绝不抛：`GetPosition` 会走导航网格（可能失败）⇒ 失败回 "(unknown)"，不把整条命令拖崩。
        /// </summary>
        private static string TargetPoint(Formation f)
        {
            try
            {
                MovementOrder mo = f.GetReadonlyMovementOrderReference();
                Vec2 p = mo.GetPosition(f);
                if (!p.IsValid) return "(invalid)";
                return "(" + p.x.ToString("0.0", CultureInfo.InvariantCulture) + ","
                            + p.y.ToString("0.0", CultureInfo.InvariantCulture) + ")";
            }
            catch
            {
                return "(unknown)";
            }
        }

        /// <summary>
        /// v0.8.30：该编队当前**重心**（`Formation.GetAveragePositionOfUnits(true, false)` 的 x,y，米）。
        ///
        /// 加它的唯一理由：给"指定点移动到底动没动"一条**行为**判据。
        /// `orderAfter=Move` + `moveTarget` 只证明"令写进了那个 order 对象"（引擎认了落点），
        /// 不证明"兵在往那儿走"；本项目要求行为证据 ⇒ 隔几秒再调一次，
        /// 看重心是否朝目标点挪（或用两次调用的重心差算位移）。
        /// 绝不抛（编队空了 / 引擎不认时回 "(invalid)" / "(unknown)"，不把整条命令拖崩）。
        /// </summary>
        private static string CenterOf(Formation f)
        {
            try
            {
                Vec2 p = f.GetAveragePositionOfUnits(true, false);
                if (!p.IsValid) return "(invalid)";
                return "(" + p.x.ToString("0.0", CultureInfo.InvariantCulture) + ","
                            + p.y.ToString("0.0", CultureInfo.InvariantCulture) + ")";
            }
            catch
            {
                return "(unknown)";
            }
        }

        /// <summary>`orderBefore`/`orderAfter` 的来源：`MovementOrder.OrderEnum`（引擎自己的枚举）。绝不抛。</summary>
        private static string OrderName(Formation f)
        {
            try
            {
                MovementOrder mo = f.GetReadonlyMovementOrderReference();
                string name = mo.OrderEnum.ToString();
                return string.IsNullOrEmpty(name) ? "(unknown)" : name;
            }
            catch
            {
                return "(unknown)";
            }
        }

        /// <summary>给错误消息/回显用：这个 Team 是攻方还是守方（拿不到回 "?"）。绝不抛。</summary>
        private static string SideLabel(Mission mission, Team team)
        {
            try
            {
                if (mission != null && team != null)
                {
                    if (ReferenceEquals(team, mission.AttackerTeam)) return "attacker";
                    if (ReferenceEquals(team, mission.DefenderTeam)) return "defender";
                }
            }
            catch
            {
            }
            return "?";
        }

        private static string TeamLabel(Mission mission, Team team, string side)
        {
            try
            {
                if (mission != null && ReferenceEquals(team, mission.PlayerTeam)) return side + "(player side)";
            }
            catch
            {
            }
            return side;
        }

        private static string Fail(string code, string message)
        {
            return "{\"ok\":false,\"code\":" + Protocol.Q(code)
                   + ",\"error\":" + Protocol.Q(message) + "}";
        }
    }
}
