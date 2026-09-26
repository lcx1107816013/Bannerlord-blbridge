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
    ///   `MovementOrder.MovementOrderChargeToTarget(Formation)`）/ `arrangement`（阵列：`ArrangementOrder`）
    ///   / `firing`（射击纪律：`FiringOrder`，引擎只有自由射击与停火两档）。五者**至少要给一个**。
    ///   movement / position / target 会被组路径那 0.5 秒一次的重申覆盖（所以必须同步改"待重申的东西"；
    ///   position / target 没有"名字"可重申 ⇒ 走**手动令优先**标记，见 `SquadSpec.ManualKind`）；
    ///   arrangement / firing **不在**重申范围内，但仍需隔几秒再读一次确认稳不稳。
    ///   ⚠️ `movement` / `position` / `target` **三者互斥**（一个编队只能有一个 movement order）。
    /// 未实现、且**不静默忽略**：`riding`（上下马）；**指定 agent/实体**为目标（`AttackEntity` /
    ///   `Follow`）也没做 —— 本轮只做"编队目标"，理由见 `HandleCommand` 里 `target` 那一段。
    ///   ⇒ 传了就报 `unsupported_param`。其余任意怪键由 MCP 工具那层挡
    ///   （`additionalProperties: false`；`Jmini` 是扁平读取器，无法枚举请求里的键）。
    /// </summary>
    internal static class BattleOrders
    {
        /// <summary>已知但**尚未实现**的参数：传了就点名拒绝，绝不静默忽略。</summary>
        private static readonly string[] UnsupportedParams = new string[] {
            "riding"
        };

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
                            + "target 冲锋到敌方编队 / arrangement / firing；上下马还没做）"
                            + " —— 不静默忽略。");
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
                // 口径说明（本轮的决定，写给下一个会话）：**只做编队目标，不做 agent/实体目标**。
                //   理由：`ChargeToTarget` 绑的是 `Formation` 对象，回读有硬判据
                //   （`MovementOrder.TargetFormation`）；而 `AttackEntity`/`Follow` 绑的是具体 agent，
                //   一死人目标就失效，还得再定"怎么挑敌方单位"的口径（那一套 `bl_control_agent` 有，
                //   但只认玩家方）⇒ 先做能验的那个，agent 目标明确报 `unsupported_param` 而不是半成品。
                string targetRaw = Jmini.Str(raw, "target", "").Trim();
                bool wantTarget = targetRaw.Length > 0;
                int targetIndex = -1;
                if (wantTarget && !OrderSpec.TryFormationIndex(targetRaw, out targetIndex))
                {
                    return Fail("bad_target", "target 只接受**敌方编队** " + OrderSpec.Join(OrderSpec.FormationNames)
                        + " 或下标 0~4，收到: " + targetRaw
                        + "（本通道只做编队目标；指定某个兵的 agent 目标还没做）");
                }
                bool wantMove = movement.Length > 0;
                bool wantArrange = arrangement.Length > 0;
                bool wantFire = firing.Length > 0;
                // movement / position / target 都往同一个 `Formation.SetMovementOrder` 写 ⇒ 最多给一个。
                // 做成"数一遍再报哪几个冲突"（而不是三条两两判断）：以后再加一种 movement order 时
                // 不会漏配某一条组合（v0.8.30 就是三条两两判，加 target 时才发现会漏）。
                int orderKindCount = (wantMove ? 1 : 0) + (wantPosition ? 1 : 0) + (wantTarget ? 1 : 0);
                if (orderKindCount > 1)
                {
                    StringBuilder given = new StringBuilder();
                    if (wantMove) given.Append("movement ");
                    if (wantPosition) given.Append("position ");
                    if (wantTarget) given.Append("target ");
                    return Fail("bad_request", "movement / position / target **三者互斥**"
                        + "（一个编队只能有一个 movement order），收到: " + given.ToString().Trim()
                        + "。movement: " + OrderSpec.Join(OrderSpec.MovementNames)
                        + "｜position: \"x,y[,z]\""
                        + "｜target: 敌方编队名或下标（" + OrderSpec.Join(OrderSpec.FormationNames) + "）");
                }
                if (!wantMove && !wantArrange && !wantFire && !wantPosition && !wantTarget)
                {
                    return Fail("bad_request", "movement / position / target / arrangement / firing 至少要给一个。"
                        + "movement: " + OrderSpec.Join(OrderSpec.MovementNames)
                        + "｜position: \"x,y[,z]\""
                        + "｜target: 敌方编队名或下标"
                        + "｜arrangement: " + OrderSpec.Join(OrderSpec.ArrangementNames)
                        + "｜firing: " + OrderSpec.Join(OrderSpec.FiringNames));
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
                Team enemyTeam = null;
                Formation targetFormation = null;
                if (wantTarget)
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
                            + "（本场不是攻守两方，或 player 侧还没确定）⇒ target 拒绝执行");
                    }
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
                ArrangementOrder arrangeOrder = wantArrange
                    ? MapArrangement(arrangement) : default(ArrangementOrder);
                FiringOrder fireOrder = wantFire ? MapFiring(firing) : default(FiringOrder);

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
                    string moveTarget = "(n/a)";   // v0.8.30：指定点移动下发后，从 order 里回读的目标点
                    string center = "(n/a)";       // v0.8.30：该编队当前重心（行为判据，见 CenterOf）
                    string targetAfter = "(n/a)";  // v0.8.31：指定目标下发后，从 order 里回读的目标编队
                    string targetDist = "(n/a)";   // v0.8.31：与目标编队的重心距离（行为判据）
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

                        if (wantMove) f.SetMovementOrder(order);
                        if (wantPosition) f.SetMovementOrder(moveToPos);
                        if (wantTarget) f.SetMovementOrder(chargeTarget);
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

                        after = OrderName(f);
                        // 重心是 position / target 两条的**行为**判据（见 CenterOf 的 doc），所以两者都要采。
                        if (wantPosition || wantTarget) center = CenterOf(f);
                        if (wantPosition)
                        {
                            moveTarget = TargetPoint(f);
                        }
                        if (wantTarget)
                        {
                            targetAfter = TargetFormationName(f);
                            targetDist = DistanceBetween(f, targetFormation);
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
                    note = "已当场回读确认（" + ReadbackField(wantPosition, wantTarget) + "）；并同步改了 "
                        + pendingUpdated
                        + " 个待重申组，所以组路径那 0.5 秒一次的周期重申会跟着重申**这个新值**"
                        + "（真机教训：不同步改它，半秒内就会被我们自己改回原值）。"
                        + "要确认真的稳，隔几秒再调一次看 orderBefore 是否仍是这次设的值"
                        + (wantPosition ? "、moveTarget 是否还在那个点" : "")
                        + (wantTarget ? "、targetAfter 是否还是同一个编队" : "")
                        + (detachAI ? "" : "（detachAI=false ⇒ 更容易被 team 战术覆盖）") + "。";
                }
                else
                {
                    note = "已当场回读确认（" + ReadbackField(wantPosition, wantTarget)
                        + "），但**没有**命中组路径的重申集合"
                        + "（该方不是按组开的战、或这个编队上没有组）⇒ 这个令可能被该方 team 级战术改回去；"
                        + "隔几秒再调一次看 orderBefore 就能判定，不猜。";
                }
                if (wantArrange || wantFire)
                {
                    note += " 阵列/射击纪律**不在**组路径那 0.5 秒一次的重申范围里（重申只管 movement）；"
                        + "要确认稳不稳，隔几秒再读一次看 arrangementBefore / firingBefore。";
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

                return "{\"ok\":" + Jw.B(ok)
                       + ",\"side\":" + Protocol.Q(side)
                       + ",\"team\":" + Protocol.Q(TeamLabel(mission, team, side))
                       + ",\"movement\":" + Protocol.Q(movement)
                       + (wantPosition ? ",\"position\":{\"x\":" + Jw.N(px) + ",\"y\":" + Jw.N(py)
                                          + ",\"z\":" + Jw.N(pz) + "}" : "")
                       + (wantTarget ? ",\"target\":" + Protocol.Q(EnumNames.Formation((FormationClass)targetIndex))
                                          + ",\"targetSide\":" + Protocol.Q(SideLabel(mission, enemyTeam))
                                     : "")
                       + (wantArrange ? ",\"arrangement\":" + Protocol.Q(arrangement) : "")
                       + (wantFire ? ",\"firing\":" + Protocol.Q(firing) : "")
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

        /// <summary>给 note 用：这一次的"判据字段"到底叫什么（避免 note 里写死 orderAfter 而漏了别的回读）。</summary>
        private static string ReadbackField(bool wantPosition, bool wantTarget)
        {
            if (wantPosition) return "orderAfter=Move + moveTarget";
            if (wantTarget) return "orderAfter=ChargeToTarget + targetAfter";
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
