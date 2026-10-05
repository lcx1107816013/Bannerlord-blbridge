using System;
using System.Text;
using TaleWorlds.InputSystem;
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    /// <summary>
    /// 游戏内热键（v0.8.34）。挂载点：`SubModule.OnBeforeMissionBehaviorInitialize`
    /// ⇒ **所有** mission 都有（含玩家自己打的战斗，不只是 AI 推演）。
    ///
    /// **PageUp = 升挡**：1x → 2x → 5x → 10x，到 10x 封顶（再按无任何变化）。
    /// **PageDown = 回原速**：不论当前几挡，一律回 1x，不做别的事。
    ///
    /// 键位选择依据（本机实测）：
    /// - `InputKey.PageUp` / `InputKey.PageDown` 在**全部 11 个按键配置文件里零绑定**
    ///   （含 RTSCamera / CommandSystem / 各 mod），且战斗内无游戏语义。
    /// - 被否掉的候选：`F` = 记分板加速键**但也是** `CombatHotKeyCategory.Action`（会干扰拾取）；
    ///   `Tab` = `Generic.Leave`（会直接离开战斗）；`L` = 被 RTSCamera 占用；`Home` 见下。
    /// - `Home` 曾作为 v0.8.33 默认键，但实测 `bl_desktop_key` 的后端（gridhand）**发不出这个键名**，
    ///   而 `PageUp/PageDown` 是用户指定、且与游戏本体零冲突 ⇒ v0.8.34 起改用 PgUp/PgDn。
    ///
    /// **为什么需要自建按键**：原版唯一的加速入口是记分板 VM 的 `ExecuteFastForwardAction`，
    /// 它前置要求 `IsMainCharacterDead`（"你死了以后快进看结局"），且记分板 View 只在官方
    /// mission 名（`"CustomBattle"`）下注册 —— BlBridge 自建 mission 名叫 `"BlBridgeScenario"`，
    /// **一个 view 都没注册**（`ViewCreatorManager.CollectMissionBehaviors` 按名查表 ⇒ 返回空）。
    /// ⇒ 这里直接读键，走 `TimeControl` 那条引擎官方通道。**原版行为没有被破坏**：
    /// `TimeControl.Apply` 在非用户干预状态下不碰 `IsFastForward` / `FixedDeltaTimeMode`。
    ///
    /// **Tab 探针**（保留）：记录按下 Tab 那一刻 mission 的现场，用于定位
    /// "为什么在 bridge mission 里按 Tab 会直接退出战斗"。
    /// v0.8.34 已用它定案：Tab 按下后 **78 毫秒** mission 就结束（`reason=engineEnded`，
    /// 且双方都还活着）；73 个 behavior 里**没有任何记分板 View**（名字含 score 的只有
    /// BlBridge 自己的 `ScoreHitProbeBehavior`）。对照：官方自定义战斗的 mission 名注册了整套 view，
    /// 长按 Tab 显示记分板、撤退是记分板上的按钮。
    /// </summary>
    internal class HotkeyBehavior : MissionBehavior
    {
        /// <summary>升挡键：1x → 2x → 5x → 10x（封顶）。</summary>
        public static InputKey UpKey = InputKey.PageUp;

        /// <summary>复位键：任意挡 → 1x。</summary>
        public static InputKey DownKey = InputKey.PageDown;

        /// <summary>挡位阶梯。必须升序，且末位 = 引擎能给的最高档。</summary>
        private static readonly int[] Ladder = new int[] { 1, 2, 5, 10 };

        private bool _prevUp;
        private bool _prevDown;
        private bool _prevTab;
        private static int _tabSeen;
        private static bool _tabPressedThisMission;
        private static float _tabPressTime = -1f;
        private static string _tabContext = "";

        public override MissionBehaviorType BehaviorType
        {
            get { return MissionBehaviorType.Other; }
        }

        public override void OnMissionTick(float dt)
        {
            try
            {
                Mission m = this.Mission;
                if (m == null) return;

                // ── PageUp：升一挡（封顶 10x）─────────────────────────────
                bool up = false;
                try { up = Input.IsKeyPressed(UpKey); }
                catch { }
                if (up && !_prevUp)
                {
                    int cur = TimeControl.ClampSpeed(TimeControl.TargetSpeed);
                    int next = cur;
                    foreach (int s in Ladder)
                    {
                        if (s > cur) { next = s; break; }
                    }
                    TimeControl.UserOverride = true;
                    if (next != cur)
                    {
                        TimeControl.TargetSpeed = next;
                        TimeControl.Apply(m);
                        UiEntry.Log("hotkey: " + UpKey + " 升挡 " + TimeControl.SpeedName(cur)
                            + " -> " + TimeControl.SpeedName(next)
                            + " (realDt=" + Jw.N(TimeControl.RealDt)
                            + " fixedDelta=" + Jw.N(TimeControl.FixedDelta(m)) + ")");
                    }
                    else
                    {
                        UiEntry.Log("hotkey: " + UpKey + " 已在最高挡 10x，无变化");
                    }
                }
                _prevUp = up;

                // ── PageDown：回原速（只做这一件事）───────────────────────
                bool down = false;
                try { down = Input.IsKeyPressed(DownKey); }
                catch { }
                if (down && !_prevDown)
                {
                    int cur = TimeControl.ClampSpeed(TimeControl.TargetSpeed);
                    TimeControl.UserOverride = true;
                    TimeControl.TargetSpeed = 1;
                    TimeControl.Restore(m);
                    UiEntry.Log("hotkey: " + DownKey + " 复位 " + TimeControl.SpeedName(cur) + " -> 1x");
                }
                _prevDown = down;

                // ── Tab 探针（保留）────────────────────────────────────────
                bool tab = false;
                try { tab = Input.IsKeyPressed(InputKey.Tab); }
                catch { }
                if (tab && !_prevTab && !_tabPressedThisMission)
                {
                    _tabSeen++;
                    _tabPressedThisMission = true;
                    try { _tabPressTime = m.CurrentTime; } catch { _tabPressTime = -1f; }
                    _tabContext = BuildTabContext(m);
                    UiEntry.Log("tab_probe: " + _tabContext);
                }
                _prevTab = tab;
            }
            catch (Exception ex)
            {
                UiEntry.Log("hotkey: 异常 " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        /// <summary>按下 Tab 那一刻的现场：挂了哪些 behavior、有没有记分板、是不是快进中。</summary>
        private static string BuildTabContext(Mission m)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"seen\":").Append(Jw.N(_tabSeen));
            try { sb.Append(",\"missionName\":").Append(Protocol.Q(MissionState.Current == null ? "?" : "hasState")); }
            catch (Exception ex) { sb.Append(",\"missionNameErr\":").Append(Protocol.Q(ex.GetType().Name)); }
            try { sb.Append(",\"missionTime\":").Append(Jw.N(m.CurrentTime)); } catch { }
            try { sb.Append(",\"mainAgent\":").Append(Protocol.Q(m.MainAgent == null ? "null" : "set")); } catch { }
            try { sb.Append(",\"isFastForward\":").Append(Jw.B(m.IsFastForward)); } catch { }
            try { sb.Append(",\"targetSpeed\":").Append(Jw.N(TimeControl.TargetSpeed)); } catch { }
            try
            {
                StringBuilder beh = new StringBuilder();
                int n = 0;
                bool scoreUi = false;
                if (m.MissionBehaviors != null)
                {
                    foreach (MissionBehavior b in m.MissionBehaviors)
                    {
                        if (b == null) continue;
                        string tn = b.GetType().Name;
                        // 只认"记分板 UI"这一类；注意别把自家 ScoreHitProbeBehavior 算进去
                        if ((tn.IndexOf("Scoreboard", StringComparison.OrdinalIgnoreCase) >= 0
                             || tn.IndexOf("BattleScore", StringComparison.OrdinalIgnoreCase) >= 0)
                            && tn.IndexOf("ScoreHitProbe", StringComparison.OrdinalIgnoreCase) < 0)
                        {
                            scoreUi = true;
                        }
                        if (n > 0) beh.Append(',');
                        beh.Append(Protocol.Q(tn));
                        n++;
                    }
                }
                sb.Append(",\"behaviorCount\":").Append(Jw.N(n));
                sb.Append(",\"hasScoreboardUi\":").Append(Jw.B(scoreUi));
                sb.Append(",\"behaviors\":[").Append(beh).Append(']');
            }
            catch (Exception ex) { sb.Append(",\"behaviorsErr\":").Append(Protocol.Q(ex.GetType().Name)); }
            sb.Append('}');
            return sb.ToString();
        }

        protected override void OnEndMission()
        {
            base.OnEndMission();
            try
            {
                if (_tabPressedThisMission)
                {
                    float now = -1f;
                    try { now = this.Mission != null ? this.Mission.CurrentTime : -1f; }
                    catch { }
                    float delta = (now >= 0f && _tabPressTime >= 0f) ? (now - _tabPressTime) : -1f;
                    UiEntry.Log("tab_probe_end: 本场按过 Tab；结束时刻距按 Tab "
                        + (delta >= 0f ? Jw.N(delta) + " 游戏秒" : "未知"));
                }

                // 每场复位：静态标志污染下一场是本项目踩过的坑。
                // 复位后：AI 推演回到"默认 10x"，玩家自己的战斗回到"默认不加速"。
                TimeControl.TargetSpeed = 1;
                TimeControl.UserOverride = false;
                _tabPressedThisMission = false;
                _tabPressTime = -1f;
                _tabContext = "";
                _prevUp = false;
                _prevDown = false;
                _prevTab = false;
            }
            catch
            {
            }
        }
    }
}
