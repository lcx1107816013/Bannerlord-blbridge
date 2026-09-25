using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using TaleWorlds.Core;
using TaleWorlds.Localization;
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    /// <summary>
    /// 游戏内 UI 的**正门**（agent 侧）：`list_ui` / `open_ui` / `close_ui`。
    ///
    /// v0.8.14 设计变更（用户裁定）：**不再自建界面**。
    /// 官方自定义战斗本身就是完整入口 —— 用户截图与官方数据表两处都能证实：
    ///   界面行：游戏类型 / 玩家类型（指挥官）/ 选择攻守方 / 地图 · 季节 · 时间 · 雨雪密度 · 雾密度 + 军团规模
    ///   模式集合：战斗 · 围攻 · 村庄 · 海战 · 海上掠夺（后两者由 NavalDLC 的场景表 flag 带出来）
    /// 我们复刻它是重复建设，且我们那份必然更窄（旧实现只列 `battle_*`）。
    /// ⇒ 分工定格：**人用官方界面玩，AI 用端口调**。本文件因此只留"进/出官方界面"与"读官方表"，
    ///   自建面板那套（主菜单入口注册 `EnsureRegistered`、自建 GameState/Screen/VM、prefab）已删。
    ///
    /// 一手 API 依据（bannerlordsage 索引，非推测）：
    ///   Module.cs:1550  public IEnumerable&lt;InitialStateOption&gt; GetInitialStateOptions()   // 按 OrderIndex 排序
    ///   Module.cs:1555  public InitialStateOption GetInitialStateOptionWithId(string id)     // 找不到返回 null
    ///   Module.cs:1567  public void ExecuteInitialStateOptionWithId(string id)
    ///                   { GetInitialStateOptionWithId(id)?.DoAction(); }   ← **静默失败**
    /// ⇒ 必须自己先判存在再执行，并把"找不到"变成显式错误（否则 AI 以为成功了）。
    ///
    /// ⚠️ 参数名避坑（v0.8.12 真机踩到）：`open_ui` 的参数叫 **`uiId`**，不能叫 `id` ——
    /// `Jmini` 是**扁平**读取器，它按"文本里第一个 `"key"`"取值，而请求信封本身自带
    /// `{"protocolVersion":1,"id":"&lt;16位hex&gt;",...}` ⇒ `Jmini.Str(raw,"id")` 恒返回信封请求号，
    /// 真机上出现过 `unknown_ui: 没有这个入口 id: 85a0d3d3fba54d4e`。离线自测抓不到这类碰撞。
    /// </summary>
    internal static class UiEntry
    {
        /// <summary>`open_ui` 的默认目标：官方自定义战斗界面。id 与显示名都由引擎给（见 `list_ui`）。</summary>
        internal const string DefaultOptionId = "CustomBattle";

        /// <summary>出口白名单里唯一的状态名。与 `ScenarioRunner` 的开战守卫共用同一常量，避免字符串漂移。</summary>
        internal const string CustomBattleStateName = "CustomBattleState";

        private static readonly object LogGate = new object();

        /// <summary>
        /// 「刚触发过界面切换」的时间窗（v0.8.14）：`open_ui` 成功后打开。
        ///
        /// 窗口内 `list_ui` 只吐缓存、**跳过逐行磁盘存在性检查** —— 因为那正是引擎在切状态 /
        /// 加载 CustomGame 数据表的时段。真机实测：这期间的请求会**无响应**（2026-09-25 第二轮
        /// 轮询 #1），说明那一刻主线程被引擎占着，我们不该在这时加码做重活。
        /// 降级而不是拒答：AI 正是在这个窗口里轮询 `activeState` 看自己有没有进去。
        /// </summary>
        private static DateTime TransitionUntilUtc = DateTime.MinValue;

        private static bool InTransitionWindow
        {
            get { return DateTime.UtcNow < TransitionUntilUtc; }
        }

        internal static string LogPath
        {
            get { return Path.Combine(BridgeConfig.LogDir, "ui.log"); }
        }

        internal static void Log(string message)
        {
            try
            {
                lock (LogGate)
                {
                    if (!Directory.Exists(BridgeConfig.LogDir)) Directory.CreateDirectory(BridgeConfig.LogDir);
                    File.AppendAllText(LogPath,
                        Jw.UtcNow() + " | " + message + "\n",
                        new UTF8Encoding(false));
                }
            }
            catch
            {
            }
        }

        // ── 控制端口侧（CommandPump.Dispatch 调用；全部在主线程）─────────────────

        /// <summary>
        /// `list_ui`：列官方入口（引擎当场给的 id/名字/禁用原因）+ **官方自定义战斗场景全表**
        /// （含模式与地形）。只读。
        ///
        /// 参数（都可选）：
        ///   `scenesMode` = all（默认）/ battle / siege / village / lordsHall / naval / navalRaid
        ///   `sceneLimit` = 最多吐多少行（默认 0 = 全吐；314 行的 JSON 约 35 KB）
        /// </summary>
        internal static string HandleListUi(string id, string raw)
        {
            try
            {
                string modeFilter = Jmini.Str(raw, "scenesMode", "all");
                if (!CustomBattleScenes.IsKnownModeFilter(modeFilter)) modeFilter = "all";
                int limit = Jmini.Int(raw, "sceneLimit", 0);
                if (limit < 0) limit = 0;

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"moduleLoaded\":").Append(Jw.B(Module.CurrentModule != null));
                sb.Append(",\"activeState\":").Append(Protocol.Q(ScenarioRunner.ActiveGameStateName()));
                sb.Append(",\"options\":").Append(OptionsJson());
                sb.Append(",\"scenes\":").Append(BattleScenesJson());
                sb.Append(",\"sceneTable\":").Append(SceneTableJson(modeFilter, limit));
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "list_ui_failed", ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        /// <summary>
        /// `open_ui`：走官方正门唤起某个入口（默认 = 官方自定义战斗界面 `CustomBattle`）。
        /// 参数：**`uiId`**（可选，缺省 = `CustomBattle`）。
        ///
        /// ⚠️ 本方法是 **fire-and-forget**：入口动作是 `MBGameManager.StartNewGame(...)` 式的跨帧加载或切状态，
        /// 所以**不谎报"已打开"** —— 返回 `requested` + 调用前后的 state，让调用方用 `list_ui` 观察落点。
        /// </summary>
        internal static string HandleOpenUi(string id, string raw)
        {
            try
            {
                Module module = Module.CurrentModule;
                if (module == null)
                {
                    return Protocol.Failure(id, "no_module",
                        "模块尚未加载（Module.CurrentModule 为 null）：这通常意味着 BlBridge 没被启动器激活", false);
                }

                string wantId = Jmini.Str(raw, "uiId", DefaultOptionId);
                if (string.IsNullOrEmpty(wantId)) wantId = DefaultOptionId;

                InitialStateOption option = module.GetInitialStateOptionWithId(wantId);
                if (option == null)
                {
                    return Protocol.Failure(id, "unknown_ui",
                        "没有这个入口 id: " + wantId + "。可用 id 见 list_ui（" + OptionIdList() + "）", false);
                }

                string stateBefore = ScenarioRunner.ActiveGameStateName();

                // ── 主菜单闸门（v0.8.12 真机踩到，F2）─────────────────────────
                // `InitialStateOption` 顾名思义只给"初始状态"用：它的 action 基本都是
                // `MBGameManager.StartNewGame(...)`，而 StartNewGame 做的是
                // `CleanAndPushState(GameLoadingState)` + 走一遍完整数据加载。
                // **在已经加载了 Game 的状态下执行它，状态机会卡在 `GameLoadingState` 不再推进**
                // （2026-09-25 真机：从 CustomBattleState 调 open_ui，90 秒 + 多次轮询仍是
                //  GameLoadingState、日志无异常、进程仍在响应 ⇒ 只能重启游戏收场）。
                // ⇒ 判据取"主菜单 = 没有激活的 Game 状态"（实测：主菜单与 PopState 回主菜单后
                //    `Game.Current.GameStateManager.ActiveState` 都取不到，名字为空串）。
                // Fail Fast：状态栈非空时一律拒绝，并告诉调用方怎么回到主菜单。
                if (stateBefore.Length != 0)
                {
                    return Protocol.Failure(id, "wrong_state_for_ui",
                        "初始状态选项只在**主菜单**可用（当前状态: " + stateBefore + "）。" +
                        "游戏已加载时执行它会让状态栈卡在 GameLoadingState（真机实测，只能重启游戏）。" +
                        "若停在官方自定义战斗界面，请先 close_ui（state=CustomBattleState）回到主菜单。", false);
                }

                bool hidden = false;
                try
                {
                    if (option.IsHidden != null) hidden = option.IsHidden();
                }
                catch (Exception ex)
                {
                    Log("open_ui: IsHidden 求值抛异常（按不可用处理）: " + ex.Message);
                    hidden = true;
                }
                if (hidden)
                {
                    return Protocol.Failure(id, "ui_hidden", "入口 " + wantId + " 当前被标记为隐藏（IsHidden = true）", false);
                }

                string disabledReason = null;
                try
                {
                    if (option.IsDisabledAndReason != null)
                    {
                        var verdict = option.IsDisabledAndReason();
                        if (verdict.Item1)
                        {
                            disabledReason = verdict.Item2 == null ? "(引擎未给原因)" : verdict.Item2.ToString();
                        }
                    }
                }
                catch (Exception ex)
                {
                    Log("open_ui: IsDisabledAndReason 求值抛异常（按不可用处理）: " + ex.Message);
                    disabledReason = "求值异常: " + ex.Message;
                }
                if (disabledReason != null)
                {
                    return Protocol.Failure(id, "ui_disabled",
                        "入口 " + wantId + " 当前不可用：" + disabledReason, false);
                }

                Log("open_ui: ExecuteInitialStateOptionWithId(" + wantId + ") stateBefore=" + stateBefore);
                module.ExecuteInitialStateOptionWithId(wantId);
                // 触发后开窗：窗口内 list_ui 走缓存并跳过磁盘检查（见 InTransitionWindow）
                TransitionUntilUtc = DateTime.UtcNow.AddSeconds(45);

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"requested\":true");
                sb.Append(",\"uiId\":").Append(Protocol.Q(wantId));
                sb.Append(",\"stateBefore\":").Append(Protocol.Q(stateBefore));
                sb.Append(",\"note\":").Append(Protocol.Q(
                    "入口动作已触发（fire-and-forget）。界面出现需要若干帧，请用 list_ui 观察 activeState"));
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "open_ui_failed", ex.GetType().Name + ": " + ex.Message, true);
            }
        }

        /// <summary>
        /// `close_ui`：从官方自定义战斗界面回主菜单（与官方 CustomBattle 的「返回」同一路径：
        /// `CustomBattleVM.ExecuteBack()` → `Game.Current.GameStateManager.PopState(0)`）。
        ///
        /// **白名单只有一个状态 `CustomBattleState`**，不接受任意状态名 —— 那等于把引擎状态栈交给调用方。
        /// 保留它的理由：`open_ui` 给了 agent `id=CustomBattle` 这扇门；没有出口，agent 进去就出不来
        /// （而它又不能从那里再 `open_ui` —— 见上面的主菜单闸门）。
        /// </summary>
        internal static string HandleCloseUi(string id, string raw)
        {
            string wantState = Jmini.Str(raw, "state", CustomBattleStateName);
            if (string.IsNullOrEmpty(wantState)) wantState = CustomBattleStateName;
            if (wantState != CustomBattleStateName)
            {
                return Protocol.Failure(id, "bad_state",
                    "close_ui 只接受 state=" + CustomBattleStateName + "，收到: " + wantState +
                    "。不接受任意状态名 —— 那等于让调用方 pop 引擎状态栈。", false);
            }

            string message;
            if (!RequestClose(wantState, out message))
            {
                return Protocol.Failure(id, "not_open", message, false);
            }
            return Protocol.Success(id, "{\"closeRequested\":true,\"state\":" + Protocol.Q(wantState) +
                ",\"note\":" + Protocol.Q("与官方 CustomBattle 的『返回』同一路径（PopState）；状态切换在下一帧生效") + "}");
        }

        internal static bool RequestClose(string wantState, out string message)
        {
            message = null;
            try
            {
                string state = ScenarioRunner.ActiveGameStateName();
                if (state != wantState)
                {
                    message = "当前不在 " + wantState + "（state=" +
                              (state.Length == 0 ? "主菜单/无激活状态" : state) + "），close_ui 只对白名单状态生效";
                    return false;
                }
                if (Mission.Current != null)
                {
                    message = "任务仍在进行（Mission.Current 非 null）：先等它结束或调用 abort，再返回主菜单";
                    return false;
                }
                if (Game.Current == null || Game.Current.GameStateManager == null)
                {
                    message = "Game.Current / GameStateManager 不可用";
                    return false;
                }
                Game.Current.GameStateManager.PopState(0);
                Log("close_ui: PopState(0) 已请求（state=" + wantState + "，官方 CustomBattleVM.ExecuteBack 同路径）");
                return true;
            }
            catch (Exception ex)
            {
                message = ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        // ── JSON 片段 ────────────────────────────────────────────────────────

        private static string OptionIdList()
        {
            try
            {
                List<string> ids = new List<string>();
                foreach (InitialStateOption option in Module.CurrentModule.GetInitialStateOptions())
                {
                    if (option != null && !string.IsNullOrEmpty(option.Id)) ids.Add(option.Id);
                }
                return string.Join(", ", ids.ToArray());
            }
            catch
            {
                return "(枚举失败)";
            }
        }

        private static string OptionsJson()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append('[');
            bool first = true;
            try
            {
                foreach (InitialStateOption option in Module.CurrentModule.GetInitialStateOptions())
                {
                    if (option == null) continue;
                    if (!first) sb.Append(',');
                    first = false;
                    sb.Append("{\"id\":").Append(Protocol.Q(option.Id));
                    sb.Append(",\"name\":").Append(Protocol.Q(option.Name == null ? "" : option.Name.ToString()));
                    sb.Append(",\"orderIndex\":").Append(Jw.N(option.OrderIndex));
                    bool hidden = false;
                    bool disabled = false;
                    string reason = "";
                    try
                    {
                        if (option.IsHidden != null) hidden = option.IsHidden();
                        if (option.IsDisabledAndReason != null)
                        {
                            var verdict = option.IsDisabledAndReason();
                            disabled = verdict.Item1;
                            reason = verdict.Item2 == null ? "" : verdict.Item2.ToString();
                        }
                    }
                    catch
                    {
                    }
                    sb.Append(",\"hidden\":").Append(Jw.B(hidden));
                    sb.Append(",\"disabled\":").Append(Jw.B(disabled));
                    sb.Append(",\"disabledReason\":").Append(Protocol.Q(reason));
                    sb.Append('}');
                }
            }
            catch (Exception ex)
            {
                sb.Append("{\"error\":").Append(Protocol.Q(ex.Message)).Append('}');
            }
            sb.Append(']');
            return sb.ToString();
        }

        /// <summary>野战场景（`battle_*`）—— 窄口径，与 `ScenarioRunner.ValidateScene` 的错误提示同源。</summary>
        private static string BattleScenesJson()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append('[');
            List<string> scenes = ScenarioRunner.AvailableBattleScenes();
            for (int i = 0; i < scenes.Count; i++)
            {
                if (i > 0) sb.Append(',');
                sb.Append(Protocol.Q(scenes[i]));
            }
            sb.Append(']');
            return sb.ToString();
        }

        /// <summary>
        /// 官方场景表全量（见 `CustomBattleScenes`：**纯文件读 + 缓存**，不碰 `MBObjectManager`）。
        /// `modes` 给的是**全表**计数（不受 filter 影响），`rows` 才是过滤后的。
        ///
        /// 逐行 `exists`（场景目录是否真在磁盘上 —— 不存在的名字会让引擎在 native 层崩 0xC0000005）
        /// **只对真正要吐出去的行算**，且在"刚触发过界面切换"的时间窗内**整段跳过**：
        /// 那正是引擎切状态/加载数据的时段（真机实测期间请求会无响应），不该在这时加码做磁盘扫描。
        /// 跳过时 `existsChecked=false`，且每行 `exists` 一律 false —— **不可信**，字段语义写在 `note` 里。
        /// </summary>
        private static string SceneTableJson(string modeFilter, int limit)
        {
            StringBuilder sb = new StringBuilder();
            string error;
            bool fromCache;
            List<string> files;
            List<SceneRow> all = CustomBattleScenes.ReadAll(out error, out fromCache, out files);

            bool checkExists = !InTransitionWindow;

            sb.Append("{\"count\":").Append(Jw.N(all.Count));
            sb.Append(",\"filter\":").Append(Protocol.Q(modeFilter));
            sb.Append(",\"fromCache\":").Append(Jw.B(fromCache));
            sb.Append(",\"sourceFiles\":").Append(Jw.N(files.Count));
            sb.Append(",\"existsChecked\":").Append(Jw.B(checkExists));
            if (!checkExists)
            {
                sb.Append(",\"note\":").Append(Protocol.Q(
                    "刚触发过界面切换（45 秒窗口内）⇒ 本次走缓存且跳过逐行磁盘检查；" +
                    "每行 exists 恒为 false 且不可信，场景清单本身仍可用"));
            }
            if (!string.IsNullOrEmpty(error))
            {
                sb.Append(",\"error\":").Append(Protocol.Q(error));
            }

            int battle = 0, siege = 0, village = 0, lordsHall = 0, naval = 0, navalRaid = 0;
            for (int i = 0; i < all.Count; i++)
            {
                switch (all[i].Mode)
                {
                    case "siege": siege++; break;
                    case "village": village++; break;
                    case "lordsHall": lordsHall++; break;
                    case "naval": naval++; break;
                    case "navalRaid": navalRaid++; break;
                    default: battle++; break;
                }
            }
            sb.Append(",\"modes\":{\"battle\":").Append(Jw.N(battle))
              .Append(",\"siege\":").Append(Jw.N(siege))
              .Append(",\"village\":").Append(Jw.N(village))
              .Append(",\"lordsHall\":").Append(Jw.N(lordsHall))
              .Append(",\"naval\":").Append(Jw.N(naval))
              .Append(",\"navalRaid\":").Append(Jw.N(navalRaid))
              .Append('}');

            sb.Append(",\"rows\":[");
            bool first = true;
            int emitted = 0;
            int missing = 0;
            for (int i = 0; i < all.Count; i++)
            {
                SceneRow row = all[i];
                if (!CustomBattleScenes.Matches(row, modeFilter)) continue;
                if (limit > 0 && emitted >= limit) break;
                if (!first) sb.Append(',');
                first = false;
                emitted++;

                row.Exists = checkExists && ScenarioRunner.SceneExists(row.Id);
                if (checkExists && !row.Exists) missing++;

                sb.Append("{\"id\":").Append(Protocol.Q(row.Id));
                sb.Append(",\"name\":").Append(Protocol.Q(row.Name));
                sb.Append(",\"mode\":").Append(Protocol.Q(row.Mode));
                sb.Append(",\"terrain\":").Append(Protocol.Q(row.Terrain));
                sb.Append(",\"exists\":").Append(Jw.B(row.Exists));
                if (row.SceneLevel.Length > 0)
                {
                    sb.Append(",\"sceneLevel\":").Append(Protocol.Q(row.SceneLevel));
                }
                if (row.Source.Length > 0)
                {
                    sb.Append(",\"source\":").Append(Protocol.Q(row.Source));
                }
                sb.Append('}');
            }
            sb.Append(']');
            sb.Append(",\"returned\":").Append(Jw.N(emitted));
            if (checkExists)
            {
                sb.Append(",\"missingInReturned\":").Append(Jw.N(missing));
            }
            sb.Append('}');
            return sb.ToString();
        }
    }
}
