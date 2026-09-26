using System;
using System.Collections.Generic;
using System.Reflection;
using System.Text;
using TaleWorlds.Core;
using TaleWorlds.MountAndBlade;
using TaleWorlds.MountAndBlade.View.MissionViews;   // MissionMainAgentController（在 .View 程序集里）
using TaleWorlds.MountAndBlade.View.Screens;
using TaleWorlds.ScreenSystem;

namespace BlBridge
{
    /// <summary>
    /// **接管士兵**（v0.8.25）—— 最小可用版：把 `Mission.MainAgent` 换成友方某个 agent，并把它交给玩家控制器。
    ///
    /// 为什么"最小版"这样做（做法出处：RTSCamera / MissionLibrary 的源码，MIT，我们只抄做法不抄代码）：
    ///   `Mission` 自己那条官方路径（`Mission.CanTakeControlOfAgent` + `TakeControlOfAgent`）**只在主角已阵亡时**
    ///   才允许（要求 `MainAgent == null`），而且它**不改 `Mission.MainAgent`** —— 也就是说官方那条路只是把
    ///   某个 agent 的 `Controller` 交给玩家，镜头/主角色概念仍指着旧对象。想吃"随时接管"，就得自己补上
    ///   `Mission.MainAgent = 目标`（RTSCamera 的 `ControlTroopLogic.SetToMainAgent` 正是这么干的，`Mission.MainAgent`
    ///   有 public setter）。
    ///
    /// **接管五步**（缺一步就会出问题，逐条注明出处）：
    ///   1) **先把老主角色交回 AI**。RTSCamera 注释写明：同一编队里出现两个 `Controller == Player` 的 agent
    ///      会让编队逻辑**栈溢出** ⇒ 必须 `Controller = AI` + 重初始化 AI 组件（`CommonAIComponent` /
    ///      `HumanAIComponent`）+ 触发 `Formation.OnUnitAddedOrRemoved()` 刷新 UI 计数。
    ///   2) `Mission.MainAgent = 目标`。
    ///   3) 目标 `Controller = Player`、`AIStateFlags = None`、解除速度上限（跑动/坐骑）。
    ///   4) 摘掉 `VictoryComponent`（胜利动作组件；不摘会卡在庆祝动作里）。
    ///   5) 让 `MissionScreen` 重新认一次玩家 agent（私有字段 `_isPlayerAgentAdded`，反射）。
    ///      ⚠️ 这一步与第 4 步是**反射/组件级**细节：失败只降级（回传 `screenReset=false`），不假装成功。
    ///
    /// ⚠️ **真机实测的边界（2026-09-25，v0.8.26，AI 对 AI 自定义战斗）**：
    ///   `take` 能把 `Mission.MainAgent` 换成目标（回读确认），**但 `Controller` 当场回读仍是 `AI`**
    ///   —— 也就是说在没有真人玩家的场次里，这一步**没真正接管**。引擎侧能对上的两处：
    ///     * `Agent.Controller` 的 setter 会顺带 `Mission.MainAgent = this`（`Agent.cs:1199` 起）；
    ///     * `MissionMainAgentController.Mission_OnMainAgentChanged` 在任何主角色变更时把
    ///       `_isPlayerAgentAdded` 置 **true**（`MissionMainAgentController.cs:229`）。
    ///   ⇒ 结论：`ok` **由回读决定**（拿不到 `Controller == Player` 就报 `controller_not_verified`），
    ///   **不假装成功**；要真接管得在**真人场次**（玩家自己打的战斗）里验证。
    ///
    /// **已做 / 没做的**（如实列出，不静默）：
    ///   * ✅ **v0.8.32 起走 RTSCamera 自己的「平滑推镜」**（见 `CameraFollow`）：反射调
    ///     `Utility.BeforeSetMainAgent` → 写 `Mission.MainAgent` → `Utility.AfterSetMainAgent`，
    ///     并回读 `MissionScreen.LastFollowedAgent` 作为判据；没装 RTSCamera（或版本漂移）时
    ///     **退回**"只复位 `_isPlayerAgentAdded`"，行为与 v0.8.31 一致（不静默、也不假装成功）。
    ///     ⚠️ "平滑"本身是视觉判据：本模块能证明"上游那对方法被真的调到了 + 跟随目标写进去了"；
    ///   * 与 RTSCamera 并存时它自己也在管 `MainAgent`（它的 `ControlTroop` 键会覆盖我们）；
    ///   * 不能接管敌方 agent（RTSCamera 也拒绝：`agent.Team != Mission.PlayerTeam` 直接放弃 —— 敌人交给玩家
    ///     会让该方 AI/阵型失去主控，且引擎多处假设"玩家 agent 在自己队里"）。
    /// </summary>
    internal static class ControlAgent
    {
        /// <summary>已知但**尚未实现**的参数：传了就点名拒绝，不静默忽略。</summary>
        private static readonly string[] UnsupportedParams = new string[] {
            "agentId", "slot", "mount", "weapon"
        };

        /// <summary>
        /// 首次接管时记下的"原始主角色"（`release` 用它换回去）+ **它属于哪一场**。
        ///
        /// ⚠️ 为什么必须带场次（2026-09-25 真机发现）：agent 下标在不同 mission 之间会**重号**。
        /// 原先只存一个下标 ⇒ 上一场记的 `22` 到了这一场会指向**另一个兵**，`release` 就会把主角色
        /// 交给一个无关的人（这一场实测：进新场后 `originalIndex` 仍是旧场的 22）。
        /// 处理：换场即作废（不猜）。只持一个 Mission 引用，下一次 take/release 就替换掉。
        /// </summary>
        private static Mission _originalMission;
        private static int _originalMainAgentIndex = -1;

        /// <summary>记下"接管前的原始主角色"（同场只在第一次记）。</summary>
        private static void RememberOriginal(Mission mission, Agent main)
        {
            if (mission == null || main == null) return;
            if (!ReferenceEquals(_originalMission, mission))
            {
                _originalMission = mission;
                _originalMainAgentIndex = -1;
            }
            if (_originalMainAgentIndex < 0) _originalMainAgentIndex = main.Index;
        }

        /// <summary>取"接管前的原始主角色"；**跨场记录一律作废**（下标会重号，不能拿到新场里乱认）。</summary>
        private static Agent FindOriginal(Mission mission)
        {
            if (mission == null) return null;
            if (!ReferenceEquals(_originalMission, mission))
            {
                _originalMission = mission;
                _originalMainAgentIndex = -1;
                return null;
            }
            if (_originalMainAgentIndex < 0) return null;
            return FindByIndex(mission, _originalMainAgentIndex);
        }

        private static readonly FieldInfo IsPlayerAgentAddedField =
            typeof(MissionScreen).GetField("_isPlayerAgentAdded", BindingFlags.Instance | BindingFlags.NonPublic);

        internal static string HandleCommand(string raw)
        {
            try
            {
                for (int i = 0; i < UnsupportedParams.Length; i++)
                {
                    if (Jmini.Has(raw, UnsupportedParams[i]))
                    {
                        return Fail("unsupported_param", "参数 " + UnsupportedParams[i]
                            + " 还没实现（本通道目前只按 agentIndex / troop / formation 选目标；"
                            + "指定武器、上马、按槽位选都还没做）—— 不静默忽略。");
                    }
                }

                string mode = Jmini.Str(raw, "mode", "take").Trim().ToLowerInvariant();
                if (mode != "take" && mode != "release" && mode != "status")
                {
                    return Fail("bad_mode", "mode 只接受 take / release / status，收到: " + mode);
                }

                string side = Jmini.Str(raw, "side", "player").Trim().ToLowerInvariant();
                if (side != "attacker" && side != "defender" && side != "player")
                {
                    return Fail("bad_side", "side 只接受 attacker / defender / player，收到: " + side);
                }

                string formationArg = Jmini.Str(raw, "formation", "").Trim();
                int formationIndex = -1;
                if (formationArg.Length > 0 && !OrderSpec.TryFormationIndex(formationArg, out formationIndex))
                {
                    return Fail("bad_formation", "formation 只接受 " + OrderSpec.Join(OrderSpec.FormationNames)
                        + " 或下标 0~4，收到: " + formationArg);
                }
                string troop = Jmini.Str(raw, "troop", "").Trim();
                int agentIndex = Jmini.Int(raw, "agentIndex", -1);

                Mission mission = Mission.Current;
                if (mission == null)
                {
                    return Fail("no_mission", "当前没有战斗在跑（Mission.Current == null）⇒ 没有 agent 可接管。"
                        + "请先开一场战斗（start / 游戏内官方自定义战斗界面）。");
                }

                Team playerTeam = null;
                try { playerTeam = mission.PlayerTeam; }
                catch { }
                if (playerTeam == null)
                {
                    return Fail("no_player_team", "拿不到玩家方 Team（mission.PlayerTeam == null）"
                        + " ⇒ 无法判断谁是友方，接管会变成「把敌人交给玩家」，而引擎多处假设玩家 agent 在自己队里。");
                }

                if (mode == "status") return StatusJson(mission, playerTeam);

                Team sideTeam = TeamOf(mission, side);
                if (mode == "release") return Release(mission, playerTeam);

                return Take(mission, playerTeam, sideTeam, side, agentIndex, troop, formationIndex, formationArg);
            }
            catch (Exception ex)
            {
                return Fail("control_agent_failed", ex.GetType().Name + ": " + ex.Message);
            }
        }

        // ── take ────────────────────────────────────────────────────────────

        private static string Take(Mission mission, Team playerTeam, Team sideTeam, string side,
                                   int agentIndex, string troop, int formationIndex, string formationArg)
        {
            // 选目标。**默认跳过当前 MainAgent**（真机 2026-09-25 教训：自定义战斗里玩家方的
            // MainAgent 一直都在，第一个存活者往往就是它 ⇒ 默认选择器会稳定地撞上
            // `already_main_agent`，功能看着像坏的）。显式给了 agentIndex 就照给（撞上就如实报）。
            Agent mainNow = SafeMainAgent(mission);
            int excludeIndex = mainNow == null ? -1 : mainNow.Index;
            Agent target = null;
            string how;
            bool onlyMainMatched = false;
            if (agentIndex >= 0)
            {
                target = FindByIndex(mission, agentIndex);
                how = "agentIndex=" + agentIndex;
                if (target == null)
                {
                    return Fail("no_target", "按 agentIndex=" + agentIndex + " 找不到这个 agent"
                        + "（下标来自 `agent.Index`，可见于遥测/事件里的 agent 字段；换一场就变）");
                }
            }
            else
            {
                if (troop.Length > 0) how = "troop=" + troop;
                else if (formationIndex >= 0) how = "formation=" + EnumNames.Formation((FormationClass)formationIndex);
                else how = "firstAlive(" + side + "，跳过当前 MainAgent)";

                target = FirstAlive(mission, sideTeam, troop.Length > 0 ? troop : null,
                                    formationIndex, excludeIndex);
                if (target == null)
                {
                    // 再放宽一次（不排除主角色）：用来区分"真没有"与"符合条件的只有主角色本身"
                    Agent again = FirstAlive(mission, sideTeam, troop.Length > 0 ? troop : null,
                                             formationIndex, -1);
                    if (again != null)
                    {
                        target = again;
                        onlyMainMatched = true;
                    }
                }
            }
            if (target == null)
            {
                if (troop.Length > 0)
                {
                    return Fail("no_target", side + " 方没有存活的 " + troop
                        + "（活着才可能接管；阵亡/未进场都取不到）");
                }
                if (formationIndex >= 0)
                {
                    return Fail("no_target", side + " 方的 " + formationArg + " 编队里没有存活 agent");
                }
                return Fail("no_target", side + " 方找不到任何存活 agent"
                    + "（用 agentIndex / troop / formation 指定也行，见 bl_control_agent 的参数）");
            }
            if (onlyMainMatched)
            {
                return Fail("already_main_agent", "该条件下唯一存活的 agent 就是当前 MainAgent（index="
                    + target.Index + "）—— 换 troop / formation / agentIndex 指定别人。"
                    + "（默认选择器会跳过 MainAgent，所以正常不会撞到这条）");
            }

            if (target.Team != playerTeam)
            {
                return Fail("not_player_team", "目标不在玩家方（target.Team != mission.PlayerTeam）⇒ 拒绝。"
                    + "RTSCamera 同样拒绝：把敌人交给玩家会让该方 AI/阵型失去主控，"
                    + "而引擎多处假设玩家 agent 在自己队里。");
            }
            if (!target.IsActive())
            {
                return Fail("target_inactive", "目标已阵亡/未激活（IsActive() == false）⇒ 接管它没有意义");
            }
            if (mission.MainAgent == target)
            {
                return Fail("already_main_agent", "目标已经是 MainAgent（index=" + target.Index
                    + "）—— 不需要再接管一次");
            }

            Agent oldMain = null;
            try { oldMain = mission.MainAgent; }
            catch { }
            string oldJson = Describe(oldMain);
            bool oldHandedToAI = false;
            if (oldMain != null)
            {
                oldHandedToAI = HandToAI(mission, oldMain);
                RememberOriginal(mission, oldMain);
            }

            // v0.8.32：RTSCamera 的平滑推镜 —— 它在**写 MainAgent 之前**要问一句"这次该不该平滑"
            // （上游顺序是：`Before` → 赋值 → `After`，不能反）。不可用时 `camBeforeWhy` 给出原因，
            // 并回退到原有的 `ResetPlayerAgentAdded()`（= 上游 `AfterSetMainAgent(should=false, …)` 的等效动作）。
            bool camShouldSmooth = false;
            string camBeforeWhy = CameraFollow.Before(target, out camShouldSmooth);

            bool mainSet;
            try
            {
                mission.MainAgent = target;
                mainSet = true;
            }
            catch (Exception ex)
            {
                return Fail("set_main_agent_failed", "写 Mission.MainAgent 抛异常：" + ex.GetType().Name
                    + ": " + ex.Message + "（旧主角色已交回 AI；可以用 mode=release 或再 take 别的目标恢复）");
            }

            string playerControl = ApplyPlayerControl(target);
            bool screenReset = ResetPlayerAgentAdded();
            // v0.8.32：平滑推镜的第二步（After）。只有 Before 成功才调，结果照实回传：
            //   camAfterWhy == null ⇒ 已调用（lastFollowed 是回读判据）；
            //   非 null ⇒ 不可用 / 失败的原因（此时上面那条老路已做了最小动作，行为不退化）。
            string camLastFollowed = "(n/a)";
            string camAfterWhy = null;
            if (camBeforeWhy == null)
            {
                camAfterWhy = CameraFollow.After(camShouldSmooth, out camLastFollowed);
            }

            // ── 诚信判定（v0.8.26 真机教训）────────────────────────────────────
            // 真机（AI 对 AI 自定义战斗，无真人）实测：`Mission.MainAgent` 确实换成了目标，
            // 但 `Controller` **当场回读仍是 AI** —— 写进去了、回读不认 ⇒ 这个通道在这一刻**没达成目的**。
            // ⇒ 不许只看"我们调了 Setter"。`ok` 必须由回读决定；主角色换了但控制器没变，要单独点出来。
            Agent afterMain = SafeMainAgent(mission);
            bool mainAgentChanged = afterMain == target;
            string controllerAfter = ControllerName(target);
            bool controllerVerified = controllerAfter == "Player";
            bool ok = mainSet && mainAgentChanged && controllerVerified && playerControl == null;

            string note = controllerVerified
                ? "mainAgentAfter 与 target.controller 都是当场回读：主角色已换成目标且由玩家控制器接管。"
                : ("主角色已换成目标（mainAgentChanged=true），但 `Controller` 回读仍是 "
                   + controllerAfter + " —— 也就是说**这一刻并没有真正接管**。"
                   + "常见原因：这一场没有真人玩家（AI 对 AI 的自定义战斗里，引擎把玩家方主角色保持 AI 驱动；"
                   + "实测 MissionScreen 也不会 re-add 玩家 agent），因此要用**真人场次**验证。"
                   + "引擎侧依据：`Agent.Controller` 的 setter 会顺带 `Mission.MainAgent = this`，"
                   + "而 `MissionMainAgentController.Mission_OnMainAgentChanged` 又会把 _isPlayerAgentAdded 置 true。");

            if (!ok)
            {
                return "{\"ok\":false,\"code\":" +
                    Protocol.Q(controllerVerified ? "take_failed" : "controller_not_verified")
                    + ",\"mode\":\"take\",\"how\":" + Protocol.Q(how)
                    + ",\"target\":" + Describe(target)
                    + ",\"mainAgentBefore\":" + oldJson
                    + ",\"mainAgentAfter\":" + Describe(afterMain)
                    + ",\"mainAgentChanged\":" + Jw.B(mainAgentChanged)
                    + ",\"controllerAfter\":" + Protocol.Q(controllerAfter)
                    + ",\"oldHandedToAI\":" + Jw.B(oldHandedToAI)
                    + ",\"playerControlError\":" + (playerControl == null ? "null" : Protocol.Q(playerControl))
                    + ",\"screenReset\":" + Jw.B(screenReset)
                    + ",\"cameraFollow\":" + CameraFollowJson(camBeforeWhy, camShouldSmooth,
                                                             camAfterWhy, camLastFollowed)
                    + ",\"originalIndex\":" + Jw.N(_originalMainAgentIndex)
                    + ",\"note\":" + Protocol.Q(note)
                    + ",\"error\":" + Protocol.Q(
                        controllerVerified ? "接管未完成（见 note 与各回读字段）" : note) + "}";
            }

            return "{\"ok\":true"
                   + ",\"mode\":\"take\",\"how\":" + Protocol.Q(how)
                   + ",\"target\":" + Describe(target)
                   + ",\"mainAgentBefore\":" + oldJson
                   + ",\"mainAgentAfter\":" + Describe(afterMain)
                   + ",\"mainAgentChanged\":" + Jw.B(mainAgentChanged)
                   + ",\"controllerAfter\":" + Protocol.Q(controllerAfter)
                   + ",\"oldHandedToAI\":" + Jw.B(oldHandedToAI)
                   + ",\"playerControlError\":null"
                   + ",\"screenReset\":" + Jw.B(screenReset)
                   + ",\"cameraFollow\":" + CameraFollowJson(camBeforeWhy, camShouldSmooth,
                                                            camAfterWhy, camLastFollowed)
                   + ",\"originalIndex\":" + Jw.N(_originalMainAgentIndex)
                   + ",\"note\":" + Protocol.Q(note
                       + " 镜头：装了 RTSCamera 时走它自己的平滑推镜"
                       + "（`Utility.BeforeSetMainAgent` → 赋值 → `AfterSetMainAgent`，见 cameraFollow 的 "
                       + "applied/lastFollowed）；没装时退回原位（把 MissionScreen._isPlayerAgentAdded 复位）。"
                       + "mode=release 可换回原始主角色。") + "}";
        }

        // ── release ─────────────────────────────────────────────────────────

        private static string Release(Mission mission, Team playerTeam)
        {
            Agent current = SafeMainAgent(mission);
            Agent original = FindOriginal(mission);
            if (original == null)
            {
                return Fail("no_original", "没有可换回的「原始主角色」：**本场**还没 take 过"
                    + "（take 时会记下当时的 Mission.MainAgent），或那个 agent 已经不在了。"
                    + "跨场的记录一律作废（agent 下标在不同场次会重号，不能拿来乱认）。"
                    + "可以直接 take 别的 agent。");
            }
            if (!original.IsActive())
            {
                return Fail("original_inactive", "原始主角色已阵亡（IsActive() == false）⇒ 换回去没有意义");
            }
            if (current == original)
            {
                return Fail("already_original", "当前 MainAgent 已经是原始主角色（index=" + original.Index
                    + "），不需要 release");
            }

            bool currentHandedToAI = false;
            if (current != null) currentHandedToAI = HandToAI(mission, current);
            // v0.8.32：与 take 同一条腿 —— 换回原来那个角色时也走 RTSCamera 的平滑推镜
            // （Before → 赋值 → After）。
            bool camShouldSmooth = false;
            string camBeforeWhy = CameraFollow.Before(original, out camShouldSmooth);
            bool mainSet;
            try
            {
                mission.MainAgent = original;
                mainSet = true;
            }
            catch (Exception ex)
            {
                return Fail("set_main_agent_failed", "写 Mission.MainAgent 抛异常：" + ex.GetType().Name
                    + ": " + ex.Message);
            }
            string playerControl = ApplyPlayerControl(original);
            bool screenReset = ResetPlayerAgentAdded();
            string camLastFollowed = "(n/a)";
            string camAfterWhy = null;
            if (camBeforeWhy == null)
            {
                camAfterWhy = CameraFollow.After(camShouldSmooth, out camLastFollowed);
            }

            // 与 take 同一口径：ok 由回读决定（主角色是否真的换回 + 控制器回读）
            Agent afterMain = SafeMainAgent(mission);
            bool mainAgentChanged = afterMain == original;
            string controllerAfter = ControllerName(original);
            bool controllerVerified = controllerAfter == "Player";
            bool ok = mainSet && mainAgentChanged && controllerVerified && playerControl == null;

            string body = "{\"ok\":" + Jw.B(ok)
                + (ok ? "" : ",\"code\":" + Protocol.Q(controllerVerified ? "release_failed" : "controller_not_verified"))
                + ",\"mode\":\"release\""
                + ",\"restored\":" + Describe(original)
                + ",\"mainAgentAfter\":" + Describe(afterMain)
                + ",\"mainAgentChanged\":" + Jw.B(mainAgentChanged)
                + ",\"controllerAfter\":" + Protocol.Q(controllerAfter)
                + ",\"previousHandedToAI\":" + Jw.B(currentHandedToAI)
                + ",\"playerControlError\":" + (playerControl == null ? "null" : Protocol.Q(playerControl))
                + ",\"screenReset\":" + Jw.B(screenReset)
                + ",\"cameraFollow\":" + CameraFollowJson(camBeforeWhy, camShouldSmooth,
                                                         camAfterWhy, camLastFollowed)
                // v0.8.30：与 status / take 对齐，把"接管前那个 agent 的下标"也**结构化成字段**
                // 回传（旧实现只在 note 文案里出现 ⇒ 调用方要拿它做断言只能去抠字符串）。
                + ",\"originalIndex\":" + Jw.N(_originalMainAgentIndex)
                + ",\"note\":" + Protocol.Q("已把主角色换回接管前记录的那一个（原下标 "
                                            + _originalMainAgentIndex + "）；controllerAfter="
                                            + controllerAfter + " 是当场回读");
            if (!ok)
            {
                body += ",\"error\":" + Protocol.Q("换回未完成（见各回读字段；"
                    + "`controller_not_verified` 表示引擎回读的 Controller 不是 Player —— "
                    + "AI 对 AI 场次里常见，需真人场次验证）");
            }
            return body + "}";
        }

        // ── status ──────────────────────────────────────────────────────────

        private static string StatusJson(Mission mission, Team playerTeam)
        {
            Agent main = SafeMainAgent(mission);
            int candidates = 0;
            try
            {
                foreach (Formation f in playerTeam.FormationsIncludingEmpty)
                {
                    if (f == null) continue;
                    f.ApplyActionOnEachUnit(delegate(Agent a)
                    {
                        if (a != null && a.IsActive()) candidates++;
                    });
                }
            }
            catch
            {
            }
            string mainAgent = Describe(main);
            bool mainIsPlayer = false;
            try { mainIsPlayer = main != null && main.Controller == AgentControllerType.Player; }
            catch { }
            // 跨场记录一律作废（agent 下标会重号）
            Agent original = FindOriginal(mission);
            int originalIndex = original == null ? -1 : original.Index;

            return "{\"ok\":true,\"mode\":\"status\""
                   + ",\"inMission\":true"
                   + ",\"playerTeam\":" + Protocol.Q(TeamName(mission, playerTeam))
                   + ",\"mainAgent\":" + mainAgent
                   + ",\"mainAgentIsPlayerController\":" + Jw.B(mainIsPlayer)
                   + ",\"originalIndex\":" + Jw.N(originalIndex)
                   + ",\"aliveCandidatesOnPlayerTeam\":" + Jw.N(candidates)
                   + ",\"screenHasPlayerAgent\":" + Jw.B(ScreenHasPlayerAgent())
                   + ",\"note\":" + Protocol.Q(
                       "mainAgentIsPlayerController 用的就是引擎自己的口径"
                       + "（`Agent.IsMine => Controller == AgentControllerType.Player`，"
                       + "`Agent.cs:631`）⇒ 它为 false 时**没有任何 agent 在被玩家控制**："
                       + "常见于①自由相机/观战（RTSCamera 会把主角色交给 AI）②纯 AI 场次。"
                       + "candidates 是玩家方存活 agent 数。") + "}";
        }

        // ── 引擎操作（按 RTSCamera/MissionLibrary 的做法，见类注释）───────────────

        /// <summary>把某个 agent 交回 AI（老主角色 / release 时的当前主角色）。返回是否真的改了 Controller。</summary>
        private static bool HandToAI(Mission mission, Agent agent)
        {
            if (agent == null) return false;
            try
            {
                MissionMainAgentController ctrl = mission.GetMissionBehavior<MissionMainAgentController>();
                if (ctrl != null && ctrl.InteractionComponent != null) ctrl.InteractionComponent.ClearFocus();
            }
            catch
            {
            }
            bool changed = false;
            try
            {
                if (agent.Controller != AgentControllerType.AI)
                {
                    agent.Controller = AgentControllerType.AI;
                    changed = true;
                }
            }
            catch
            {
                return changed;
            }
            try
            {
                if (agent.CommonAIComponent != null) agent.CommonAIComponent.Initialize();
            }
            catch
            {
            }
            try
            {
                if (agent.HumanAIComponent != null) agent.HumanAIComponent.Initialize();
            }
            catch
            {
            }
            try
            {
                if (agent.Formation != null) agent.Formation.OnUnitAddedOrRemoved();
            }
            catch
            {
            }
            return changed;
        }

        /// <summary>把 agent 交给玩家控制器。成功返回 null，失败返回原因（**不静默**）。</summary>
        private static string ApplyPlayerControl(Agent agent)
        {
            try
            {
                agent.Controller = AgentControllerType.Player;
            }
            catch (Exception ex)
            {
                return "写 agent.Controller 失败: " + ex.GetType().Name + ": " + ex.Message;
            }
            try { agent.AIStateFlags = Agent.AIStateFlag.None; }
            catch { }
            try { if (agent.MountAgent != null) agent.MountAgent.SetMaximumSpeedLimit(-1f, false); }
            catch { }
            try { agent.SetMaximumSpeedLimit(-1f, false); }
            catch { }
            try
            {
                VictoryComponent vc = agent.GetComponent<VictoryComponent>();
                if (vc != null)
                {
                    agent.RemoveComponent(vc);
                    agent.SetActionChannel(1, ActionIndexCache.act_none, true);
                    agent.ClearTargetFrame();
                }
            }
            catch
            {
            }
            try
            {
                if (agent.Formation != null) agent.Formation.OnUnitAddedOrRemoved();
            }
            catch
            {
            }
            return null;
        }

        /// <summary>
        /// v0.8.32：把相机跟随的结果拼成 JSON 片段（`cameraFollow:{...}`）。**绝不抛**。
        ///
        /// 字段口径：`applied` = 上游那对方法**真的调到了**（两步都没报 why）；`shouldSmooth` = 上游
        /// `BeforeSetMainAgent` 的返回值（true = 它接下来会做平滑推镜）；`lastFollowed` = 调用后回读的
        /// `MissionScreen.LastFollowedAgent`（**这才是判据**，不是"我们调了方法"）；
        /// `why` = 不可用/失败的原因（没装 RTSCamera / 版本漂移 / 抛异常，各说各的）。
        /// </summary>
        private static string CameraFollowJson(string beforeWhy, bool shouldSmooth, string afterWhy,
                                              string lastFollowed)
        {
            bool applied = beforeWhy == null && afterWhy == null;
            return "{\"applied\":" + Jw.B(applied)
                + ",\"how\":" + Protocol.Q("reflection MissionSharedLibrary.Utilities.Utility"
                    + ".BeforeSetMainAgent / AfterSetMainAgent")
                + ",\"shouldSmooth\":" + Jw.B(shouldSmooth)
                + ",\"lastFollowed\":" + Protocol.Q(lastFollowed)
                + ",\"shouldSmoothFlagNext\":" + Protocol.Q(CameraFollow.ShouldSmoothText())
                + ",\"why\":" + Protocol.Q(beforeWhy == null ? (afterWhy == null ? "" : afterWhy) : beforeWhy)
                + "}";
        }

        /// <summary>
        /// 让 `MissionScreen` 重新认一次玩家 agent：把私有字段 `_isPlayerAgentAdded` 置 false。
        /// 这一步失败只降级（返回 false ⇒ 回传 `screenReset:false`），**不假装成功**。
        /// v0.8.32：装了 RTSCamera 时，它等价于上游 `AfterSetMainAgent(should=false, …)` 的 else 分支 ——
        /// 我们仍然保留它作为**兜底**（`CameraFollow` 不可用时行为与 v0.8.31 完全一致）。
        /// </summary>
        private static bool ResetPlayerAgentAdded()
        {
            try
            {
                MissionScreen screen = ScreenManager.TopScreen as MissionScreen;
                if (screen == null) return false;
                if (IsPlayerAgentAddedField == null) return false;
                IsPlayerAgentAddedField.SetValue(screen, false);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static bool ScreenHasPlayerAgent()
        {
            try
            {
                MissionScreen screen = ScreenManager.TopScreen as MissionScreen;
                if (screen == null || IsPlayerAgentAddedField == null) return false;
                object v = IsPlayerAgentAddedField.GetValue(screen);
                return v is bool && (bool)v;
            }
            catch
            {
                return false;
            }
        }

        // ── 小工具 ──────────────────────────────────────────────────────────

        private static Agent SafeMainAgent(Mission mission)
        {
            try { return mission.MainAgent; }
            catch { return null; }
        }

        private static Team TeamOf(Mission mission, string side)
        {
            try
            {
                if (side == "attacker") return mission.AttackerTeam;
                if (side == "defender") return mission.DefenderTeam;
                return mission.PlayerTeam;
            }
            catch
            {
                return null;
            }
        }

        private static string TeamName(Mission mission, Team team)
        {
            try
            {
                if (mission != null && ReferenceEquals(team, mission.PlayerTeam)) return "player";
                if (mission != null && ReferenceEquals(team, mission.AttackerTeam)) return "attacker";
                if (mission != null && ReferenceEquals(team, mission.DefenderTeam)) return "defender";
            }
            catch
            {
            }
            return "unknown";
        }

        private static Agent FindByIndex(Mission mission, int index)
        {
            try
            {
                foreach (Agent a in mission.Agents)
                {
                    if (a != null && a.Index == index) return a;
                }
            }
            catch
            {
            }
            return null;
        }

        /// <summary>
        /// 按 troop / formation 过滤取第一个存活 agent（两者都给时 troop 优先，均不给 = 任意）。
        /// `excludeIndex` = 要跳过的 agent 下标（传当前 MainAgent 的下标即可"换个兵接管"）。
        /// </summary>
        private static Agent FirstAlive(Mission mission, Team team, string troop, int formationIndex,
                                        int excludeIndex)
        {
            if (team == null) return null;
            try
            {
                foreach (Formation f in team.FormationsIncludingEmpty)
                {
                    if (f == null) continue;
                    if (formationIndex >= 0 && (int)f.LogicalClass != formationIndex) continue;
                    Agent found = null;
                    f.ApplyActionOnEachUnit(delegate(Agent a)
                    {
                        if (found != null || a == null) return;
                        if (!a.IsActive()) return;
                        if (excludeIndex >= 0 && a.Index == excludeIndex) return;
                        if (troop != null && troop.Length > 0)
                        {
                            string id = null;
                            try { id = a.Character == null ? null : a.Character.StringId; }
                            catch { }
                            if (!string.Equals(id, troop, StringComparison.OrdinalIgnoreCase)) return;
                        }
                        found = a;
                    });
                    if (found != null) return found;
                }
            }
            catch
            {
            }
            return null;
        }

        /// <summary>`Agent.Controller` 的当场回读（取不到返回 "(unknown)"）。判据用它，不用"我们调了 setter"。</summary>
        private static string ControllerName(Agent a)
        {
            if (a == null) return "(no-agent)";
            try
            {
                return a.Controller.ToString();
            }
            catch
            {
                return "(unknown)";
            }
        }

        private static string Describe(Agent a)
        {
            if (a == null) return "null";
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"index\":").Append(Jw.N(a.Index));
            string name = null, troop = null, formation = null, controller = null;
            try { name = a.Name; } catch { }
            try { troop = a.Character == null ? null : a.Character.StringId; } catch { }
            try { formation = a.Formation == null ? null : EnumNames.Formation(a.Formation.LogicalClass); } catch { }
            try { controller = a.Controller.ToString(); } catch { }
            bool isHero = false, isActive = false;
            float health = -1f;
            try { isHero = a.IsHero; } catch { }
            try { isActive = a.IsActive(); } catch { }
            try { health = a.Health; } catch { }
            sb.Append(",\"name\":").Append(Protocol.Q(name == null ? "" : name));
            sb.Append(",\"troop\":").Append(Protocol.Q(troop == null ? "" : troop));
            sb.Append(",\"formation\":").Append(Protocol.Q(formation == null ? "" : formation));
            sb.Append(",\"controller\":").Append(Protocol.Q(controller == null ? "" : controller));
            sb.Append(",\"isHero\":").Append(Jw.B(isHero));
            sb.Append(",\"isActive\":").Append(Jw.B(isActive));
            sb.Append(",\"health\":").Append(Jw.N(health));
            sb.Append('}');
            return sb.ToString();
        }

        private static string Fail(string code, string message)
        {
            return "{\"ok\":false,\"code\":" + Protocol.Q(code)
                   + ",\"error\":" + Protocol.Q(message) + "}";
        }
    }
}
