using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;

using TaleWorlds.CampaignSystem;
using TaleWorlds.CampaignSystem.Actions;
using TaleWorlds.CampaignSystem.Settlements;
using TaleWorlds.Core;                  // Game / MBSaveLoad
using TaleWorlds.Library;
using TaleWorlds.MountAndBlade;         // MBGameManager.EndGame()

namespace BlBridge
{
    /// <summary>
    /// 战役刺激器（stimulus）：**故意改变游戏状态**，用来把"等运气"变成"确定触发"。
    ///
    /// ## 为什么需要它（与 CampaignObserver 的分工）
    ///
    /// `CampaignObserver` 只**看**。但有些事件在平静的存档里**永远等不到** ——
    /// 最典型的是 `WarDeclared`：沙盒开局所有王国互相和平，不宣战就
    /// **不会有**围攻、不会有聚落易主、不会有大会战。
    /// ⇒ 那 4 个处理器（war_declared / make_peace / siege_started /
    ///   settlement_owner_changed）在平静档上**无法验证**，不是代码坏，是条件不存在。
    ///
    /// ⇒ 本类提供**最小刺激面**：造出那些条件。
    ///
    /// ## ⚠️ 与观察者的根本区别：这个会改存档
    ///
    /// `CampaignObserver` 是只读的（零副作用，随便开）；
    /// 本类是**写操作**，会真实改变战役状态。纪律：
    ///
    ///   1. **绝不隐式调用**：必须外部显式点名 method + 参数（不提供"顺手宣战"的路径）；
    ///   2. **回显真实结果**：宣战后**回读** `FactionManager.IsAtWarAgainstFaction`
    ///      确认真的开战了 —— 照本项目"写完回读"纪律，不假设 API 一定生效；
    ///   3. **幂等可查**：本来就在交战 ⇒ 明确报 `already_at_war`，不当成功；
    ///   4. **失败不猜**：找不到名字 ⇒ 列出可用名字（本项目的"属性名写错"教训同款）。
    ///
    /// ## 为什么用 `DeclareWarAction` 而不是直接改 `FactionManager`
    ///
    /// `DeclareWarAction.ApplyByDefault` 内部会：`FactionManager.DeclareWar` +
    /// 调整双方 `PoliticalStagnation` + 把可见聚落/队伍标记为 dirty（刷新地图显示）
    /// + **`CampaignEventDispatcher.Instance.OnWarDeclared(...)`**。
    /// 最后那条是本类存在的意义 —— 走官方路径才会**正常触发事件与后续 AI 行为**；
    /// 绕过它直接改关系表，就成了"数据变了但没人知道"，那对测 mod 毫无价值
    /// （甚至更糟：mod 会看到一个它从没被通知过的战争状态）。
    /// </summary>
    internal static class CampaignStimulus
    {
        // ══════════════════════════════════════════════════════════════
        // 回主菜单（可选：先存档再退）
        // ══════════════════════════════════════════════════════════════

        /// <summary>待办的"存档后回主菜单"（跨帧状态机；null = 无待办）。</summary>
        private static string _pendingExitAfterSaveName = null;
        private static DateTime _pendingExitIssuedUtc = DateTime.MinValue;
        private static int _pendingExitTicks;

        /// <summary>
        /// 回主菜单（卸载当前战役，**不关游戏进程**）。
        ///
        /// ## ★ 纠正一处我先前的错误结论（有源码证据）
        ///
        /// 我早前说"回主菜单会切断 MCP 通道、是自杀式操作"—— **那是错的**。证据：
        ///
        ///   `Module.OnApplicationTick`（`Module.cs:502-524`）**无条件**遍历所有 submodule：
        ///       `foreach (MBSubModuleBase item in CollectSubModules()) item.OnApplicationTick(dt);`
        ///   ⇒ 它不关心当前是战役还是主菜单，**命令泵照常每帧运行**。
        ///
        ///   而 `MBGameManager.EndGame()`（`Module.cs`/`MBGameManager.cs:182-215`）只做：
        ///       等 `IsLoaded` → `PopState()` 退 Mission → **`GameStateManager.CleanStates()`**
        ///   ⇒ **只清状态栈，不调 `OnSubModuleUnloaded`**（那个只在 `FinalizeSubModulesBases()`
        ///     里，即游戏关闭/重载模块时才走）。
        ///
        /// ⇒ 结论修正：**回主菜单后 `bl_status` / `bl_list_saves` / `bl_load_save` 都还能用**，
        ///   可以再次读档进来。这反而是有用的（换档、重置场景）。
        ///
        /// ## 为什么"先存档再退"要做成跨帧状态机
        ///
        /// 两个都是异步/分步的：
        ///   · 存盘：`SaveTick` 跨帧（`SaveHandler` 状态机）
        ///   · EndGame：`async void`，内部 `while(!IsLoaded) await Task.Delay(100)`
        /// 而本处理器**就在主线程**上（`OnApplicationTick` → `CommandPump.Pump`）
        /// ⇒ 在这里等任何一个都会**死锁**。
        /// ⇒ 所以 `saveFirst=true` 时只**登记待办**，由 `Tick()` 每帧检查
        ///   `IsSaving == false` 后再调 `EndGame()`。
        /// </summary>
        internal static string HandleReturnToMenu(string id, string raw)
        {
            try
            {
                if (Campaign.Current == null)
                    return Protocol.Failure(id, "no_campaign",
                        "当前不在战役里（可能已经在主菜单）—— 无需退回", false);

                bool saveFirst = Jmini.Bool(raw, "saveFirst", true);

                if (saveFirst)
                {
                    SaveHandler sh = Campaign.Current.SaveHandler;
                    if (sh == null)
                        return Protocol.Failure(id, "no_save_handler",
                            "`SaveHandler` 为空 ⇒ 不能先存档。若要强制退出请传 saveFirst=false", false);

                    if (sh.IsSaving)
                    {
                        StringBuilder bg = new StringBuilder();
                        bg.Append("{\"ok\":false,\"error\":\"save_in_progress\"");
                        bg.Append(",\"note\":").Append(Protocol.Q(
                            "引擎正在存盘 ⇒ 未登记退出待办。请等 `bl_save_status` 显示 "
                            + "`isSaving=false` 后重试（否则会与进行中的存盘抢状态机）。"));
                        bg.Append('}');
                        return Protocol.Success(id, bg.ToString());
                    }

                    string name = Jmini.Str(raw, "name", "").Trim();
                    if (name.Length > 0)
                    {
                        if (MBSaveLoad.IsSaveFileNameReserved(name))
                            return Protocol.Failure(id, "name_reserved",
                                "档名 \"" + name + "\" 是引擎保留名，不可用", false);
                        sh.SaveAs(name);
                    }
                    else
                    {
                        sh.QuickSaveCurrentGame();
                    }

                    // 登记待办：等 IsSaving 落下再退
                    _pendingExitAfterSaveName = name.Length > 0 ? name : "(quicksave)";
                    _pendingExitIssuedUtc = DateTime.UtcNow;
                    _pendingExitTicks = 0;

                    StringBuilder sb = new StringBuilder();
                    sb.Append("{\"ok\":").Append(Jw.B(sh.IsSaving));
                    sb.Append(",\"action\":\"return_to_menu\"");
                    sb.Append(",\"saveFirst\":true");
                    sb.Append(",\"savedAs\":").Append(Protocol.Q(_pendingExitAfterSaveName));
                    sb.Append(",\"saveQueued\":").Append(Jw.B(sh.IsSaving));
                    sb.Append(",\"issuedUtc\":").Append(Protocol.Q(
                        _pendingExitIssuedUtc.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture)));
                    sb.Append(",\"note\":").Append(Protocol.Q(
                        "已**登记待办**：引擎存盘完成（`IsSaving` 落下）后自动回主菜单。"
                        + "⚠️ 这是**跨帧**的 —— 本响应不代表已经退出了。"
                        + "请用 `bl_status` 轮询 `state` 变为非战役态（如 `idle`/`main_menu`）确认。"
                        + "★ 回主菜单**不会**切断 MCP 通道（`Module.OnApplicationTick` 无条件每帧跑），"
                        + "之后仍可 `bl_list_saves` / `bl_load_save` 再进来。"));
                    sb.Append('}');
                    return Protocol.Success(id, sb.ToString());
                }

                // 不存档，直接退
                MBGameManager.EndGame();
                StringBuilder sb2 = new StringBuilder();
                sb2.Append("{\"ok\":true");
                sb2.Append(",\"action\":\"return_to_menu\"");
                sb2.Append(",\"saveFirst\":false");
                sb2.Append(",\"issuedUtc\":").Append(Protocol.Q(
                    DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture)));
                sb2.Append(",\"note\":").Append(Protocol.Q(
                    "已调用 `MBGameManager.EndGame()`（与官方『保存并退出』的退出半侧同一入口）。"
                    + "它是 `async void`，内部要先等 `IsLoaded` 再 `CleanStates()` ⇒ **跨帧**，"
                    + "本响应不代表已经退出。请用 `bl_status`/`bl_campaign_time` 轮询 "
                    + "`inCampaign` 变 false 确认。★ 通道不会断。"));
                sb2.Append('}');
                return Protocol.Success(id, sb2.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "return_to_menu_failed",
                    ex.GetType().Name + ": " + ex.Message, true);
            }
        }

        /// <summary>
        /// 每帧推进待办动作（由 `SubModule.OnApplicationTick` 调用）。
        ///
        /// 目前只有一件：**存档完成后回主菜单**。
        /// 为什么不放在保存的回调里：`QuickSaveCurrentGame` 的回调签名只给
        /// `(SaveResult, string)`，拿不到我们需要的"退出意图"，而且回调时机
        /// 与 `IsSaving` 落下不完全同步 ⇒ 用 `IsSaving` 作判据更可靠。
        ///
        /// ⚠️ 超时保护：30 秒还没等到存盘落下就**放弃退出并如实报告**，
        /// 绝不无限等（否则会留下一个永远不退的状态，且没人知道为什么）。
        /// </summary>
        internal static void Tick()
        {
            if (_pendingExitAfterSaveName == null) return;
            try
            {
                _pendingExitTicks++;
                bool saving = Campaign.Current != null
                              && Campaign.Current.SaveHandler != null
                              && Campaign.Current.SaveHandler.IsSaving;

                if (!saving)
                {
                    string asName = _pendingExitAfterSaveName;
                    _pendingExitAfterSaveName = null;
                    UiEntry.Log("campaign_stimulus: 存档完成（" + asName + "）⇒ 调 EndGame() 回主菜单");
                    MBGameManager.EndGame();
                    return;
                }

                // 30 秒超时（按帧数粗算：60fps → 1800 帧）
                if (_pendingExitTicks > 1800)
                {
                    _pendingExitAfterSaveName = null;
                    UiEntry.Log("campaign_stimulus: ⚠️ 等存盘完成超时（30s）⇒ **放弃回主菜单**，"
                                + "战役保持原样（不假装成功）");
                }
            }
            catch (Exception ex)
            {
                _pendingExitAfterSaveName = null;
                try { ActionLedger.ExceptionToRgl("CampaignStimulus.Tick", ex); } catch { }
            }
        }

        // ══════════════════════════════════════════════════════════════
        // 遭遇 / 对话（读选项 + 选中）——**替玩家处理"给钱还是战斗"这类弹窗**
        // ══════════════════════════════════════════════════════════════

        /// <summary>
        /// 读当前对话的可选项。
        ///
        /// ## 为什么需要它（实机问题）
        ///
        /// 15x 快进时主角在路上**必然**撞到强盗/领主遭遇，游戏弹出
        /// "给钱 or 战斗"的二选一，并把 `TimeControlMode` 锁成 `Stop`
        /// ⇒ **无人值守的快进就此中断**（实测：`inMenuContext=True`、
        /// `MapConversation` 层 `isActive=true`、`CharacterNameIdParent="湖鼠资深勇士"`）。
        ///
        /// ## 为什么不用"模拟点坐标"
        ///
        /// `bl_get_screen` 里两个 `OptionButton` 的 `text` **都是空的**
        /// （文案在子控件里）⇒ 按坐标点 = **盲操作**，点错可能直接开战。
        /// ⇒ 走官方 API：`ConversationManager` 的选项列表 + `ProcessSentence()`。
        ///   这是**同一个入口**，玩家点选项时引擎走的就是它。
        ///
        /// ## 边界（本工具的纪律）
        ///
        /// **只读**：列出选项与其编号，**不替调用方选**。
        /// 选哪个是**玩家的决定**（掉钱 / 开战 / 损失兵力，后果不可逆且会污染测试基线）
        /// ⇒ 选择由 `bl_conversation_choose` 单独承担，且必须**显式给编号**。
        /// </summary>
        internal static string HandleConversation(string id, string raw)
        {
            try
            {
                if (Campaign.Current == null)
                    return Protocol.Failure(id, "no_campaign", "没有战役上下文", false);

                object cm = GetConversationManager();
                if (cm == null)
                    return Protocol.Failure(id, "no_conversation_manager",
                        "拿不到 `ConversationManager`（`Campaign.Current.ConversationManager` 为空）", false);

                // 是否在对话中：**判据用 `CurOptions.Count > 0 或 _isActive 或 CurrentSentenceText 非空`**
                // ⚠️ 实测踩点（v0.8.57）：只看 `_isActive` 会得到 **false**，
                //   而那时明明在对话里（`CurrentSentenceText` 有完整台词、`CurOptions` 有 2 项）
                //   ⇒ `_isActive` 不是可靠判据（它可能是别的语义，或本版本未维护）。
                //   ⇒ 三判据取并集，并且**把三条原始值都报出来**，便于下次判断该信哪个。
                object curOptsRaw = P(cm, "CurOptions");
                System.Collections.ICollection curOptsColl = curOptsRaw as System.Collections.ICollection;
                int curOptCount = curOptsColl == null ? 0 : curOptsColl.Count;
                object isActiveV = P(cm, "_isActive");
                bool isActiveFlag = isActiveV is bool && (bool)isActiveV;
                string curText = SafeStr(cm, "CurrentSentenceText");
                bool active = curOptCount > 0 || isActiveFlag || curText.Length > 0;

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"inConversation\":").Append(Jw.B(active));
                // 三条原始判据都报出来（透明：下次能看出该信哪个）
                sb.Append(",\"_isActiveRaw\":").Append(Jw.B(isActiveFlag));
                sb.Append(",\"curOptionsCount\":").Append(
                    curOptCount.ToString(CultureInfo.InvariantCulture));
                sb.Append(",\"currentSentence\":").Append(Protocol.Q(curText));

                // ── 列可选项 ────────────────────────────────────────────────
                // ⚠️ 正确成员是 **`CurOptions`**（public 属性，
                //    `ConversationManager.cs:120`：`public List<ConversationSentenceOption> CurOptions`），
                //    不是我第一版猜的 `_conversationSentenceOptions`（那个不存在 ⇒ 恒 0 项）。
                //    `ConversationSentenceOption` 是**结构体**，字段：
                //    `SentenceNo / Id / RepeatObject / Text / DebugInfo / IsClickable /
                //     HasPersuasion / SkillName / TraitName / IsSpecial / IsUsedOnce / HintText`。
                List<string> opts = new List<string>();
                System.Collections.IEnumerable en = curOptsRaw as System.Collections.IEnumerable;
                if (en != null)
                {
                    int i = 0;
                    foreach (object o in en)
                    {
                        if (o == null) continue;
                        // `Text` 是 `TextObject`（不是 string）⇒ 要 ToString 才有字
                        object textObj = P(o, "Text");
                        string t = textObj == null ? "" : textObj.ToString();
                        StringBuilder one = new StringBuilder();
                        one.Append("{\"index\":").Append(i.ToString(CultureInfo.InvariantCulture));
                        one.Append(",\"text\":").Append(Protocol.Q(t));
                        one.Append(",\"sentenceNo\":").Append(
                            JVal(P(o, "SentenceNo")));
                        one.Append(",\"id\":").Append(Protocol.Q(SafeStr(o, "Id")));
                        // 是否可点（disabled 选项要被指出来，否则"点了没反应"无法归因）
                        object clickable = P(o, "IsClickable");
                        if (clickable is bool) one.Append(",\"isClickable\":").Append(Jw.B((bool)clickable));
                        object special = P(o, "IsSpecial");
                        if (special is bool) one.Append(",\"isSpecial\":").Append(Jw.B((bool)special));
                        one.Append('}');
                        opts.Add(one.ToString());
                        i++;
                    }
                }
                sb.Append(",\"options\":[").Append(string.Join(",", opts.ToArray())).Append(']');
                sb.Append(",\"optionCount\":").Append(opts.Count.ToString(CultureInfo.InvariantCulture));
                sb.Append(",\"note\":").Append(Protocol.Q(
                    opts.Count > 0
                        ? "当前有 " + opts.Count + " 个可选项。选哪个是**玩家的决定**"
                          + "（掉钱/开战/损失兵力，不可逆）⇒ 本工具只列选项；"
                          + "要选请用 `bl_conversation_choose index=N`（显式给编号）。"
                        : "当前没有待处理的对话选项（不在遭遇里）。"));
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "conversation_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        /// <summary>
        /// 选中对话里的某个选项（**会改变游戏状态，不可逆**）。
        ///
        /// 参数 `index`：`bl_conversation` 给出的编号（从 0 起）。
        /// 走 `ConversationManager.ProcessSentence(option)` —— 与玩家点该选项**同一入口**。
        ///
        /// ⚠️ 纪律：**必须显式给 index**，不提供"自动挑一个"的默认行为 ——
        /// 因为选项后果不可逆（给钱会掉钱、战斗会开战/损兵），
        /// 而且**污染的测试基线不可恢复**（除非事先存过档）。
        /// </summary>
        internal static string HandleConversationChoose(string id, string raw)
        {
            try
            {
                if (Campaign.Current == null)
                    return Protocol.Failure(id, "no_campaign", "没有战役上下文", false);
                if (!Jmini.Has(raw, "index"))
                    return Protocol.Failure(id, "bad_args",
                        "必须显式给 `index`（先用 `bl_conversation` 看选项列表）—— "
                        + "本工具**刻意不提供**『自动挑一个』，因为选项后果不可逆。", false);

                object cm = GetConversationManager();
                if (cm == null)
                    return Protocol.Failure(id, "no_conversation_manager",
                        "拿不到 `ConversationManager`", false);

                int idx = Jmini.Int(raw, "index", -1);
                if (idx < 0)
                    return Protocol.Failure(id, "bad_args", "index 必须 >= 0", false);

                // 取选项列表里的第 idx 项（正确成员 = `CurOptions`，见 bl_conversation 注释）
                object optionsObj = P(cm, "CurOptions");
                System.Collections.IList list = optionsObj as System.Collections.IList;
                if (list == null)
                    return Protocol.Failure(id, "no_options",
                        "拿不到选项列表（`CurOptions`）—— 可能当前不在对话中", false);
                if (idx >= list.Count)
                    return Protocol.Failure(id, "index_out_of_range",
                        "index=" + idx + " 超出范围（当前 " + list.Count + " 个选项，编号 0.."
                        + (list.Count - 1) + "）", false);

                object opt = list[idx];
                object optTextObj = P(opt, "Text");
                string optText = optTextObj == null ? "" : optTextObj.ToString();

                // disabled 选项要拦（否则"选了没反应"会被误读成"操作成功"）
                object clickable = P(opt, "IsClickable");
                if (clickable is bool && !(bool)clickable)
                    return Protocol.Failure(id, "option_not_clickable",
                        "选项 " + idx + "（\"" + optText + "\"）当前**不可点击**（IsClickable=false）"
                        + " ⇒ 未执行任何操作。", false);

                MethodInfo mi = cm.GetType().GetMethod("ProcessSentence",
                    BindingFlags.Public | BindingFlags.Instance);
                if (mi == null)
                    return Protocol.Failure(id, "no_process_sentence",
                        "`ConversationManager` 上没有 `ProcessSentence` 方法（版本不符？）", false);
                mi.Invoke(cm, new object[] { opt });

                // ── ★ 关键第二步：`DoOptionContinue()` ──────────────────────
                //
                // 实机踩点（v0.8.57）：**只调 `ProcessSentence` 对话不会推进** ——
                // 实测 `currentSentence` 仍停在原句、`CurOptions` 一字未变。
                //
                // 源码依据（`ConversationManager.cs:839-846`）：选中一个选项是**两步**：
                //   ① `ProcessSentence(option)` —— 把 `_currentSentence` 指到该句并跑它的 consequence
                //   ② `DoOptionContinue()` —— 让 **NPC 回话**（`ProcessPartnerSentence()`）
                //      并在对话该结束时 `EndConversation()`
                // ```csharp
                // public void DoOptionContinue() {
                //     if (IsConversationEnded() && _sentences[_currentSentence].IsPlayer) { EndConversation(); return; }
                //     ProcessPartnerSentence();              // ← NPC 回应
                //     DoConversationContinuedCallback();
                // }
                // ```
                // ⇒ 玩家在 UI 上点选项时，Gauntlet 层正是"先 ProcessSentence、再 DoOptionContinue"。
                //   漏掉第二步 = **卡在原地不动**（看起来像"选了没反应"）。
                MethodInfo miCont = cm.GetType().GetMethod("DoOptionContinue",
                    BindingFlags.Public | BindingFlags.Instance);
                bool continued = false;
                if (miCont != null)
                {
                    miCont.Invoke(cm, null);
                    continued = true;
                }

                // 写完回读：选项是否变了 / 对话是否已结束
                string afterText = SafeStr(cm, "CurrentSentenceText");
                object afterOptions = P(cm, "CurOptions");
                System.Collections.ICollection aoc = afterOptions as System.Collections.ICollection;
                int afterCount = aoc == null ? 0 : aoc.Count;
                object afterActiveV = P(cm, "_isActive");
                bool stillActive = afterActiveV is bool ? (bool)afterActiveV : afterCount > 0;

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"action\":\"conversation_choose\"");
                sb.Append(",\"index\":").Append(idx.ToString(CultureInfo.InvariantCulture));
                sb.Append(",\"chosenText\":").Append(Protocol.Q(optText));
                sb.Append(",\"didContinue\":").Append(Jw.B(continued));
                sb.Append(",\"stillInConversation\":").Append(Jw.B(stillActive));
                sb.Append(",\"remainingOptions\":").Append(
                    afterCount.ToString(CultureInfo.InvariantCulture));
                sb.Append(",\"currentSentence\":").Append(Protocol.Q(afterText));
                sb.Append(",\"timeControlMode\":").Append(
                    Protocol.Q(Campaign.Current.TimeControlMode.ToString()));
                sb.Append(",\"inMenuContext\":").Append(
                    Jw.B(P(Campaign.Current, "CurrentMenuContext") != null));
                sb.Append(",\"note\":").Append(Protocol.Q(
                    stillActive
                        ? "已选中该选项，但**对话仍在继续**（可能是多步对话，剩 "
                          + afterCount + " 个选项）⇒ 再 `bl_conversation` 看后续。"
                        : "已选中该选项，对话已结束。若时间仍是 `Stop`，"
                          + "用 `bl_campaign_time_speed` 重新推起来。"
                          + "⚠️ 该选择的后果**已生效且不可逆**（掉钱/开战等）—— "
                          + "要回退请重新读档（前提是事先存过）。"));
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "conversation_choose_failed",
                    ex.GetType().Name + ": " + ex.Message, true);
            }
        }

        /// <summary>取 `Campaign.Current.ConversationManager`（反射，兼容版本差异）。</summary>
        private static object GetConversationManager()
        {
            try
            {
                return P(Campaign.Current, "ConversationManager");
            }
            catch
            {
                return null;
            }
        }

        /// <summary>
        /// **推进对话**（对应玩家在 NPC 台词上按"继续"）。
        ///
        /// ## 为什么需要它（实机踩点）
        ///
        /// 多步对话里有两种状态，**入口不同**：
        ///   ① **有选项**（`CurOptions.Count > 0`）⇒ 用 `conversation_choose index=N`
        ///      （走 `ProcessSentence` + `DoOptionContinue`）
        ///   ② **无选项、只有 NPC 台词**（`CurOptions.Count == 0`，但
        ///      `CurrentSentenceText` 有内容）⇒ 用**本工具**
        ///      （走 `ContinueConversation()`）
        ///
        /// 实测：选了"够胆就过来动手吧！"之后，对话推进到 NPC 回话
        /// （"你在挑衅吗？我们要把你的头盖骨当碗使！"），此时 **`CurOptions == 0`**
        /// ⇒ 若还用 `conversation_choose` 就会报"拿不到选项"，
        ///   而玩家在 UI 上这时是按**继续**键 ⇒ 正解是 `ContinueConversation()`。
        ///
        /// 源码依据（`ConversationManager.cs:848-870`）：
        /// ```csharp
        /// public void ContinueConversation() {
        ///     if (CurOptions.Count > 1) return;              // 有多个选项时它不干活
        ///     if (IsConversationEnded()) { EndConversation(); return; }
        ///     if (!ProcessPartnerSentence() && ListenerAgent.Character == Hero.MainHero.CharacterObject)
        ///         { EndConversation(); return; }
        ///     DoConversationContinuedCallback();
        ///     ...
        /// }
        /// ```
        /// ⚠️ 注意 `CurOptions.Count > 1` 时它**直接 return（静默不干活）** ⇒
        ///    本工具会把这种情况**显式报出来**（`ok:false` + `error:"has_options"`），
        ///    而不是静默返回成功 —— 否则调用方会以为"推进了"。
        /// </summary>
        internal static string HandleConversationContinue(string id, string raw)
        {
            try
            {
                if (Campaign.Current == null)
                    return Protocol.Failure(id, "no_campaign", "没有战役上下文", false);
                object cm = GetConversationManager();
                if (cm == null)
                    return Protocol.Failure(id, "no_conversation_manager",
                        "拿不到 `ConversationManager`", false);

                // 先看当前有几个选项
                object optsRaw = P(cm, "CurOptions");
                System.Collections.ICollection oc = optsRaw as System.Collections.ICollection;
                int n = oc == null ? 0 : oc.Count;

                string beforeText = SafeStr(cm, "CurrentSentenceText");

                // ⚠️ `CurOptions.Count > 1` 时 `ContinueConversation()` 会静默 return
                //    ⇒ 提前拦住，明确告诉调用方"该用 choose 而不是 continue"。
                if (n > 1)
                {
                    StringBuilder bg = new StringBuilder();
                    bg.Append("{\"ok\":false,\"error\":\"has_options\"");
                    bg.Append(",\"curOptionsCount\":").Append(n.ToString(CultureInfo.InvariantCulture));
                    bg.Append(",\"note\":").Append(Protocol.Q(
                        "当前有 " + n + " 个选项 ⇒ **不能用『继续』**，请用 "
                        + "`bl_conversation_choose index=N`（否则引擎的 "
                        + "`ContinueConversation()` 会因 `CurOptions.Count > 1` **静默不干活**）。"));
                    bg.Append('}');
                    return Protocol.Success(id, bg.ToString());
                }

                MethodInfo mi = cm.GetType().GetMethod("ContinueConversation",
                    BindingFlags.Public | BindingFlags.Instance);
                if (mi == null)
                    return Protocol.Failure(id, "no_continue_conversation",
                        "`ConversationManager` 上没有 `ContinueConversation` 方法（版本不符？）", false);
                mi.Invoke(cm, null);

                string afterText = SafeStr(cm, "CurrentSentenceText");
                object afterOpts = P(cm, "CurOptions");
                System.Collections.ICollection aoc = afterOpts as System.Collections.ICollection;
                int afterN = aoc == null ? 0 : aoc.Count;
                object act = P(cm, "_isActive");
                bool stillActive = (act is bool && (bool)act) || afterN > 0;

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"action\":\"conversation_continue\"");
                sb.Append(",\"beforeSentence\":").Append(Protocol.Q(beforeText));
                sb.Append(",\"afterSentence\":").Append(Protocol.Q(afterText));
                sb.Append(",\"progressed\":").Append(Jw.B(beforeText != afterText));
                sb.Append(",\"stillInConversation\":").Append(Jw.B(stillActive));
                sb.Append(",\"remainingOptions\":").Append(
                    afterN.ToString(CultureInfo.InvariantCulture));
                sb.Append(",\"timeControlMode\":").Append(
                    Protocol.Q(Campaign.Current.TimeControlMode.ToString()));
                sb.Append(",\"note\":").Append(Protocol.Q(
                    stillActive
                        ? (afterN > 0
                            ? "对话继续，现在有 " + afterN + " 个选项 ⇒ 用 `bl_conversation` 看、"
                              + "`bl_conversation_choose index=N` 选。"
                            : "对话继续，但仍无选项（还有 NPC 台词）⇒ 可再 `bl_conversation_continue`。")
                        : "对话已结束。若时间仍是 `Stop`，用 `bl_campaign_time_speed` 重新推起来；"
                          + "战斗/遭遇的后续事件可由 `bl_observer_events` 观察。"));
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "conversation_continue_failed",
                    ex.GetType().Name + ": " + ex.Message, true);
            }
        }

        // ══════════════════════════════════════════════════════════════
        // 遭遇 / 对话结束
        // ══════════════════════════════════════════════════════════════

        /// <summary>
        /// 保存当前战役（**只存不退**）。参数：`mode`（`quick` 默认 / `as`）、
        /// `name`（仅 mode=as 用，另存为的名字）。
        ///
        /// ## 为什么是"发起 + 轮询"而不是"一次调用就完成"（★ 关键）
        ///
        /// 引擎的存盘是**跨帧状态机**（`SaveHandler.SaveTick()`），由
        /// `MapState.OnTick` 每帧推进一步：
        /// ```
        /// PreSave → Saving → AwaitingCompletion → OnSaveCompleted 回调
        /// ```
        /// 而**本命令处理器就跑在那个主线程上**（`OnApplicationTick` → `CommandPump.Pump`）。
        /// ⇒ 如果在这里 `while(IsSaving) sleep()` **等**存盘完成，`SaveTick` 永远拿不到
        ///   主线程 ⇒ **死锁**，而且存档会停在半个中间态。
        /// ⇒ 所以正解是：**发起保存 → 立刻返回 → 调用方轮询** `bl_save_status`
        ///   （它读 `SaveHandler.IsSaving`，那是官方公开的判据）。
        ///
        /// ## 官方 API 依据
        /// | 用途 | 调用 |
        /// |---|---|
        /// | 快存（= F5 同一路径） | `Campaign.Current.SaveHandler.QuickSaveCurrentGame()` |
        /// | 另存为 | `Campaign.Current.SaveHandler.SaveAs(name)` |
        /// | **是否正在存**（公开判据） | `Campaign.Current.SaveHandler.IsSaving` |
        ///
        /// ⚠️ 两者都**只是入队**（`SetSaveArgs`），真正的落盘在后续帧里做。
        /// </summary>
        internal static string HandleSaveGame(string id, string raw)
        {
            try
            {
                if (Campaign.Current == null)
                    return Protocol.Failure(id, "no_campaign",
                        "没有战役上下文 —— 主菜单 / 自定义战斗里没有可存的战役档", false);

                SaveHandler sh = Campaign.Current.SaveHandler;
                if (sh == null)
                    return Protocol.Failure(id, "no_save_handler",
                        "`Campaign.Current.SaveHandler` 为空 —— 当前状态不可存盘", false);

                // ── 已在存 ⇒ 明确拒绝，不排队第二次 ──────────────────────
                // 为什么要拦：连排两次会让引擎的 `SaveArgsQueue` 里堆两项、
                // `_saveStep` 状态机被踩乱（它是单条状态机，不是并发安全的），
                // 结果可能是两次都不完整。⇒ 如实报"正在存"，让调用方等。
                if (sh.IsSaving)
                {
                    StringBuilder bg = new StringBuilder();
                    bg.Append("{\"ok\":false,\"error\":\"save_in_progress\"");
                    bg.Append(",\"isSaving\":true");
                    bg.Append(",\"note\":").Append(Protocol.Q(
                        "引擎**正在存盘**（`SaveHandler.IsSaving == true`）⇒ 本次请求没有被排队。"
                        + "存盘是跨帧状态机，请用 `bl_save_status` 轮询到 `isSaving=false` 再发起下一次。"));
                    bg.Append('}');
                    return Protocol.Success(id, bg.ToString());
                }

                string mode = Jmini.Str(raw, "mode", "quick").Trim().ToLowerInvariant();
                string saveName = Jmini.Str(raw, "name", "").Trim();

                DateTime t0 = DateTime.UtcNow;
                string action;
                if (mode == "quick" || mode == "quicksave" || mode == "f5")
                {
                    sh.QuickSaveCurrentGame();
                    action = "quick_save";
                }
                else if (mode == "as" || mode == "saveas")
                {
                    if (saveName.Length == 0)
                        return Protocol.Failure(id, "bad_args",
                            "mode=as 时必须给 `name`（另存为的档名）", false);
                    // 与引擎同判据：保留名不允许
                    if (MBSaveLoad.IsSaveFileNameReserved(saveName))
                        return Protocol.Failure(id, "name_reserved",
                            "档名 \"" + saveName + "\" 是引擎保留名，不可用", false);
                    sh.SaveAs(saveName);
                    action = "save_as";
                }
                else
                {
                    return Protocol.Failure(id, "bad_mode",
                        "mode 只接受 quick（默认，等价 F5）或 as（另存为，需配 name），收到 " + mode, false);
                }

                // ── 回读：确认"排队成功"（不是"存完了"）────────────────────
                bool queued = sh.IsSaving;

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":").Append(queued ? "true" : "false");
                sb.Append(",\"action\":").Append(Protocol.Q(action));
                sb.Append(",\"mode\":").Append(Protocol.Q(mode));
                if (saveName.Length > 0)
                    sb.Append(",\"name\":").Append(Protocol.Q(saveName));
                sb.Append(",\"queued\":").Append(Jw.B(queued));
                sb.Append(",\"isSaving\":").Append(Jw.B(queued));
                sb.Append(",\"issuedUtc\":").Append(Protocol.Q(
                    t0.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture)));
                sb.Append(",\"campaignDays\":").Append(
                    CampaignTime.Now.ToDays.ToString("F4", CultureInfo.InvariantCulture));
                if (!queued)
                {
                    sb.Append(",\"error\":\"verify_failed\"");
                    sb.Append(",\"note\":").Append(Protocol.Q(
                        "调了存档 API，但**回读 `IsSaving` 仍为 false** ⇒ 排队未生效，"
                        + "请当作失败（不要以为存上了）。可能原因：引擎处于不可存盘的状态。"));
                }
                else
                {
                    sb.Append(",\"note\":").Append(Protocol.Q(
                        "已**入队**（`IsSaving=true`）—— ⚠️ 这**不等于已经存完**！"
                        + "存盘是跨帧状态机，请用 `bl_save_status` 轮询到 `isSaving=false`，"
                        + "并核对 `newestSave` 的修改时间**晚于** 本次 `issuedUtc`，才算真的落盘。"));
                }
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                // 可能已入队 ⇒ outcomeUncertain=true（不可盲目重试，否则会重复排队）
                return Protocol.Failure(id, "save_game_failed",
                    ex.GetType().Name + ": " + ex.Message, true);
            }
        }

        /// <summary>
        /// 存档状态：**存盘是否还在进行 + 硬盘上最新那份档的时间戳**。
        ///
        /// 这是 `bl_save_game` 的**必要配对工具** —— 因为发起保存是异步的，
        /// 只有"`isSaving` 变回 false" **且** "`newestSave.mtime` 晚于发起时刻"
        /// 两条同时成立，才能说"真的存上了"。
        ///
        /// ⚠️ 只看 `isSaving` 不够：若发起时引擎恰好刚存完一帧，
        /// `isSaving` 可能瞬间就是 false（而我们那次还没落到盘）⇒ 必须核对 mtime。
        /// 这个"两个判据"的设计是为了杜绝本项目反复强调的
        /// "没报错但没生效 / 把没发生当成发生了"。
        /// </summary>
        internal static string HandleSaveStatus(string id, string raw)
        {
            try
            {
                bool inCampaign = Campaign.Current != null;
                bool isSaving = false;
                if (inCampaign && Campaign.Current.SaveHandler != null)
                    isSaving = Campaign.Current.SaveHandler.IsSaving;

                // 列存档文件（名称 + 大小 + mtime + sha 前缀可选项）
                string dir = SaveDir();
                List<string> rows = new List<string>();
                string newestName = "";
                string newestUtc = "";
                long newestBytes = 0;
                DateTime newestTime = DateTime.MinValue;
                if (dir != null && Directory.Exists(dir))
                {
                    string[] files = Directory.GetFiles(dir, "*.sav");
                    for (int i = 0; i < files.Length; i++)
                    {
                        FileInfo fi = new FileInfo(files[i]);
                        if (!fi.Exists) continue;
                        if (fi.LastWriteTimeUtc > newestTime)
                        {
                            newestTime = fi.LastWriteTimeUtc;
                            newestName = Path.GetFileNameWithoutExtension(fi.Name);
                            newestBytes = fi.Length;
                        }
                    }
                    // 全部列出（按时间倒序，最多 30）
                    Array.Sort(files, delegate (string a, string b)
                    {
                        return File.GetLastWriteTimeUtc(b).CompareTo(File.GetLastWriteTimeUtc(a));
                    });
                    int lim = files.Length > 30 ? 30 : files.Length;
                    for (int i = 0; i < lim; i++)
                    {
                        FileInfo fi2 = new FileInfo(files[i]);
                        StringBuilder one = new StringBuilder();
                        one.Append("{\"name\":").Append(Protocol.Q(
                            Path.GetFileNameWithoutExtension(fi2.Name)));
                        one.Append(",\"bytes\":").Append(fi2.Length.ToString(CultureInfo.InvariantCulture));
                        one.Append(",\"utc\":").Append(Protocol.Q(
                            fi2.LastWriteTimeUtc.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture)));
                        one.Append('}');
                        rows.Add(one.ToString());
                    }
                }
                if (newestTime != DateTime.MinValue)
                    newestUtc = newestTime.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture);

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"inCampaign\":").Append(Jw.B(inCampaign));
                // ★ 当前模式（v0.8.55）：沙盒 / 剧情 —— 见 CampaignReadProbe.HandleCampaignOverview
                //   里"为什么 CampaignGameMode 不够"的说明（它只有 None/Campaign/Tutorial，
                //   真正的区别在 `Campaign.Current` 的运行时类型）。
                if (inCampaign)
                {
                    string tn = Campaign.Current.GetType().FullName ?? "";
                    bool isStory = tn.IndexOf("CampaignStoryMode",
                        StringComparison.OrdinalIgnoreCase) >= 0;
                    sb.Append(",\"campaignType\":").Append(Protocol.Q(tn));
                    sb.Append(",\"isStoryMode\":").Append(Jw.B(isStory));
                    sb.Append(",\"isSandbox\":").Append(Jw.B(!isStory));
                }
                else
                {
                    sb.Append(",\"campaignType\":null,\"isStoryMode\":null,\"isSandbox\":null");
                }
                sb.Append(",\"isSaving\":").Append(Jw.B(isSaving));
                sb.Append(",\"saveDir\":").Append(Protocol.Q(dir ?? ""));
                sb.Append(",\"newestSave\":").Append(newestName.Length == 0 ? "null" : ("{\"name\":" + Protocol.Q(newestName)
                    + ",\"bytes\":" + newestBytes.ToString(CultureInfo.InvariantCulture)
                    + ",\"utc\":" + Protocol.Q(newestUtc) + "}"));
                sb.Append(",\"count\":").Append(rows.Count.ToString(CultureInfo.InvariantCulture));
                sb.Append(",\"saves\":[").Append(string.Join(",", rows.ToArray())).Append(']');
                sb.Append(",\"note\":").Append(Protocol.Q(
                    isSaving
                        ? "**正在存盘**（isSaving=true）⇒ 继续轮询，别重启游戏。"
                        : "当前没有存盘在进行。若刚 `bl_save_game`，请核对 `newestSave.utc` 是否晚于发起时刻。"));
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "save_status_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        /// <summary>
        /// 存档目录（`&lt;我的文档&gt;\Mount and Blade II Bannerlord\Game Saves`）。
        /// 与 `MBSaveLoad` 用的是同一个位置 —— 这是引擎的固定约定，不是我们编的。
        /// </summary>
        private static string SaveDir()
        {
            try
            {
                string docs = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
                if (string.IsNullOrEmpty(docs)) return null;
                return Path.Combine(Path.Combine(docs, "Mount and Blade II Bannerlord"),
                    "Game Saves");
            }
            catch
            {
                return null;
            }
        }

        // ══════════════════════════════════════════════════════════════
        // 时间流速（等价于玩家点屏幕中间那个「继续 / 倍速」键）
        // ══════════════════════════════════════════════════════════════

        /// <summary>
        /// 设置战役时间流速。**这是"让时间前进"的唯一正门**。
        ///
        /// ## 源码依据（`Campaign.cs:353-364` setter + `:839-875` TickMapTime）
        ///
        /// ```csharp
        /// public CampaignTimeControlMode TimeControlMode {
        ///     set { if (!TimeControlModeLock && value != _timeControlMode) _timeControlMode = value; }
        /// }
        /// ```
        /// ⇒ **只有一个守卫**：`TimeControlModeLock`。没有别的隐含条件，
        ///   所以代码可以直接设它 —— 与玩家按键走的是同一个属性。
        ///
        /// ## ★ 为什么 `Unstoppable*` 才是"必定流动"的那档（关键）
        ///
        /// `TickMapTime` 的分支：
        /// | 模式 | 时间推进条件 |
        /// |---|---|
        /// | `StoppablePlay` | **仅当 `!IsMainPartyWaiting`** 才推进 |
        /// | `UnstoppablePlay` | **无条件**推进 |
        /// | `StoppableFastForward` | 仅当 `!IsMainPartyWaiting` |
        /// | `UnstoppableFastForward` | **无条件** × `SpeedUpMultiplier`(默认4) |
        /// | `Stop` / `FastForwardStop` | 不推进 |
        ///
        /// 而 `IsMainPartyWaiting = MainParty.ComputeIsWaiting()` ——
        /// 它要求"主角停在原地/据点里"（`DefaultBehavior == Hold` 且无移动目标）。
        ///
        /// ⇒ **两条让时间流动的路径**（都实测过）：
        ///   ① `MainParty` 处于 `Hold`（站着不动） ⇒ 用 `Unstoppable*` 必流动；
        ///   ② 玩家在城镇里"等待一段时间" ⇒ `IsMainPartyWaiting` 为真。
        ///
        /// ⚠️ **若主角正在移动**（`DefaultBehavior != Hold`），`IsMainPartyWaiting` 为假，
        ///   此时 `Stoppable*` 档**仍会推进**（因为条件反过来成立了）——
        ///   这正是"走路时时间也在走"的原因。
        ///   ⇒ 真正会**冻住**时间的是 `Stop`/`FastForwardStop`，或
        ///     `Stoppable` 档 + 主角 Hold 之外的组合并不冻。
        ///
        /// ## 与 `bl_observer_*` 的关系（为什么它俩必须配对使用）
        ///
        /// 观察者只**记录**事件，但事件需要**时间推进**才会产生。
        /// 闲置的战役（读档后默认 `Stop`）里，AI 不决策、不打仗、不围城
        /// ⇒ 观察者只会记到 `observer_start` 一条。
        /// 本工具负责把时间推起来，两者合起来才是完整的"自动测试夹具"。
        /// </summary>
        internal static string HandleTimeSpeed(string id, string raw)
        {
            try
            {
                if (Campaign.Current == null)
                    return Protocol.Failure(id, "no_campaign",
                        "没有战役上下文 —— 主菜单 / 自定义战斗里没有战役时间（需进战役存档）", false);

                string want = Jmini.Str(raw, "mode", "").Trim();
                string speedStr = Jmini.Str(raw, "speed", "").Trim();

                // 可选的倍率（等价于控制台 `campaign.set_campaign_speed_multiplier N`）。
                //
                // ⚠️ 两种形态都收（照 `Jmini.Bool` 的既有约定：**"只认一种就是调用方猜谜"**）：
                //   · 裸数字 `"multiplier":15`     —— MCP 侧现在发的就是这种；
                //   · 带引号 `"multiplier":"15"` —— CLI 侧的既有约定（布尔参数一律发字符串）。
                //   实测踩点（v0.8.52）：MCP 侧曾发 `"multiplier":"15.0"`（字符串），
                //   而 `Jmini.Num` 只扫裸数字 ⇒ 读成 NaN ⇒ 报出的消息是
                //   "multiplier 不是数字：15.0"，**看着像自相矛盾**，极易误导排查者。
                //   ⇒ 这里显式兜住字符串形态，并且判据与官方命令一致（`> 0f`）。
                float? multiplier = null;
                if (Jmini.Has(raw, "multiplier"))
                {
                    double mv = Jmini.Num(raw, "multiplier", double.NaN);
                    if (double.IsNaN(mv))
                    {
                        // 退化：可能是带引号的形态 ⇒ 取字符串再 parse（不变文化）
                        string ms = Jmini.Str(raw, "multiplier", null);
                        double parsed;
                        if (ms != null && double.TryParse(ms.Trim(),
                                NumberStyles.Float, CultureInfo.InvariantCulture, out parsed))
                        {
                            mv = parsed;
                        }
                        else
                        {
                            return Protocol.Failure(id, "bad_multiplier",
                                "multiplier 不是数字：" + (ms ?? "(空)"), false);
                        }
                    }
                    multiplier = (float)mv;
                }

                CampaignTimeControlMode target;
                if (speedStr.Length > 0)
                {
                    int sp;
                    if (!int.TryParse(speedStr, out sp) || sp < 0 || sp > 3)
                        return Protocol.Failure(id, "bad_speed",
                            "speed 只接受 0..3（0=停 / 1=正常 / 2=加速 / 3=最快），收到 " + speedStr, false);
                    target = MapSpeedToMode(sp);
                }
                else if (want.Length > 0)
                {
                    if (!TryParseTimeMode(want, out target))
                        return Protocol.Failure(id, "bad_mode",
                            "mode 非法：" + want + "（可用：Stop / Play / FastForward / "
                            + "UnstoppablePlay / UnstoppableFastForward）", false);
                }
                else if (multiplier.HasValue)
                {
                    // 只给倍率 ⇒ 默认配到"倍率真正生效"的那档（UnstoppableFastForward）。
                    //
                    // ★ 为什么这样默认：`SpeedUpMultiplier` **只在 FastForward 两档被乘**，
                    //   若默认成 Play，调用方会以为"设了倍率=时间变快了"，
                    //   而实际倍率被忽略 —— 那是"没报错但没生效"的典型陷阱。
                    target = CampaignTimeControlMode.UnstoppableFastForward;
                }
                else
                {
                    return Protocol.Failure(id, "bad_args",
                        "要给 `speed`（0..3，推荐）或 `mode`（Stop/Play/FastForward/"
                        + "UnstoppablePlay/UnstoppableFastForward），或单独给 `multiplier`"
                        + "（会自动配 UnstoppableFastForward 档，因为倍率只在该档生效）", false);
                }

                if (Campaign.Current.TimeControlModeLock)
                    return Protocol.Failure(id, "time_control_locked",
                        "`TimeControlModeLock` 当前为 true ⇒ 设了也不会生效"
                        + "（通常是某个弹窗/菜单把时间锁住了，如决策弹窗/人物头像弹窗）。"
                        + "请先关掉那个界面。", false);

                CampaignTimeControlMode before = Campaign.Current.TimeControlMode;
                float multBefore = Campaign.Current.SpeedUpMultiplier;

                Campaign.Current.TimeControlMode = target;

                // ── 倍率（可选）：等价于控制台 `campaign.set_campaign_speed_multiplier N` ──
                //
                // ★ 为什么把它并进这个工具（v0.8.52）：
                //   控制台那条命令的**实现**（`CampaignCheats.cs:3126`）只做一件事 ——
                //       `Campaign.Current.SpeedUpMultiplier = N;`
                //   而 `SpeedUpMultiplier`（`Campaign.cs:370`，`{ get; set; } = 4f`，
                //   **无任何守卫**）**只在 `FastForward` 两档被乘**：
                //       UnstoppablePlay        : num = num2                ← 不吃倍率
                //       StoppableFastForward   : num = num2 * multiplier   ← 吃
                //       UnstoppableFastForward : num = num2 * multiplier   ← 吃
                //   ⇒ **单跑那条命令不会让时间流起来**（档位还是 Stop 就没用）。
                //     两件事（档位 + 倍率）必须一起设才达成"让时间按 N 倍前进"。
                //   ⇒ 所以并到这里：一次调用把两件事都做掉，且**不依赖开控制台**。
                //
                // ⚠️ 原版上限：非开发模式 15、开发模式 30（`CampaignCheats.cs:3135`）。
                //   我们**照抄这个上限**（超了钳到上限并如实说明），
                //   不自作主张放宽 —— 那是引擎定的平衡边界。
                bool multRequested = false;
                float multAfter = multBefore;
                bool multClamped = false;
                string multNote = "";
                if (multiplier.HasValue)
                {
                    multRequested = true;
                    float want2 = multiplier.Value;
                    float cap = (Game.Current != null && Game.Current.IsDevelopmentMode) ? 30f : 15f;
                    if (want2 <= 0f)
                        return Protocol.Failure(id, "bad_multiplier",
                            "multiplier 必须是正数（收到 " + want2.ToString("R", CultureInfo.InvariantCulture)
                            + "）—— 与官方命令同判据（`result2 > 0f`）。", false);
                    if (want2 > cap)
                    {
                        multClamped = true;
                        multNote = "请求 " + want2.ToString("R", CultureInfo.InvariantCulture)
                                 + " 超过上限 " + cap.ToString("R", CultureInfo.InvariantCulture)
                                 + " ⇒ 已钳到上限（照抄官方 `set_campaign_speed_multiplier` 的行为）。";
                        want2 = cap;
                    }
                    Campaign.Current.SpeedUpMultiplier = want2;
                    multAfter = Campaign.Current.SpeedUpMultiplier;
                }

                // ── 写完回读（本项目硬纪律）───────────────────────────────
                CampaignTimeControlMode after = Campaign.Current.TimeControlMode;
                bool effective = after == target;
                double d0 = CampaignTime.Now.ToDays;

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":").Append(effective ? "true" : "false");
                sb.Append(",\"action\":\"time_speed\"");
                sb.Append(",\"requested\":").Append(Protocol.Q(target.ToString()));
                sb.Append(",\"before\":").Append(Protocol.Q(before.ToString()));
                sb.Append(",\"after\":").Append(Protocol.Q(after.ToString()));
                sb.Append(",\"effective\":").Append(Jw.B(effective));
                sb.Append(",\"multiplierRequested\":").Append(Jw.B(multRequested));
                sb.Append(",\"multiplierBefore\":").Append(
                    multBefore.ToString("R", CultureInfo.InvariantCulture));
                sb.Append(",\"multiplierAfter\":").Append(
                    multAfter.ToString("R", CultureInfo.InvariantCulture));
                sb.Append(",\"multiplierClamped\":").Append(Jw.B(multClamped));
                sb.Append(",\"campaignDays\":").Append(
                    d0.ToString("F4", CultureInfo.InvariantCulture));
                sb.Append(",\"willAdvance\":").Append(Jw.B(AdvancesTime(after)));
                // 倍率是否**真的会被用上**：只在 FastForward 两档生效 —— 这个判据很关键，
                // 否则调用方会以为"设了倍率=时间变快了"，而实际档位是 Play 时倍率被忽略。
                sb.Append(",\"multiplierInEffect\":").Append(
                    Jw.B(UsesMultiplier(after)));

                // 说清"这档在什么条件下才真的推进" —— 避免调用方误读
                string why;
                switch (after)
                {
                    case CampaignTimeControlMode.UnstoppablePlay:
                        why = "无条件按正常速度推进（**不吃 SpeedUpMultiplier**）。";
                        break;
                    case CampaignTimeControlMode.UnstoppableFastForward:
                        why = "无条件按 SpeedUpMultiplier "
                              + multAfter.ToString("R", CultureInfo.InvariantCulture)
                              + "x 推进 —— 最快，且倍率生效。";
                        break;
                    case CampaignTimeControlMode.StoppablePlay:
                        why = "会在 `IsMainPartyWaiting`（主角 Hold 且在原地/据点）时推进；"
                              + "主角移动中同样推进（**不吃 SpeedUpMultiplier**）。";
                        break;
                    case CampaignTimeControlMode.StoppableFastForward:
                        why = "同 StoppablePlay，但快进（受 IsMainPartyWaiting 影响），"
                              + "倍率 " + multAfter.ToString("R", CultureInfo.InvariantCulture) + "x 生效。"
                              + "⚠️ 若目的是『无人值守稳定推进』，请用 UnstoppableFastForward。";
                        break;
                    default:
                        why = "**不推进时间**（停）。";
                        break;
                }
                if (multNote.Length > 0) why += " " + multNote;
                if (multRequested && !UsesMultiplier(after))
                    why += " ⚠️ 当前档位 `" + after + "` **不使用 SpeedUpMultiplier**"
                         + " ⇒ 倍率已写入但对本档无效；要让它生效请用 speed=2（UnstoppableFastForward）。";
                sb.Append(",\"note\":").Append(Protocol.Q(why));
                if (!effective)
                    sb.Append(",\"error\":\"verify_failed\"");
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "time_speed_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        /// <summary>
        /// 把 0..3 的档位映射到 `CampaignTimeControlMode`。
        ///
        /// ⚠️ 映射到 **`Unstoppable*`** 而不是 `Stoppable*` 是**刻意的**，与本文件
        /// 用途（自动测试夹具）直接相关：
        /// `Stoppable*` 会在 `IsMainPartyWaiting` 之外的条件下降速，
        /// 而无人值守时我们**希望时间确定性地流动**（否则测试窗口长度不可预期）。
        /// ⇒ 用 `Unstoppable*` 保证"设了就在跑"。
        /// </summary>
        private static CampaignTimeControlMode MapSpeedToMode(int speed)
        {
            switch (speed)
            {
                case 0: return CampaignTimeControlMode.Stop;
                case 1: return CampaignTimeControlMode.UnstoppablePlay;
                case 2: return CampaignTimeControlMode.UnstoppableFastForward;
                case 3: return CampaignTimeControlMode.UnstoppableFastForwardForPartyWaitTime;
                default: return CampaignTimeControlMode.UnstoppablePlay;
            }
        }

        private static bool AdvancesTime(CampaignTimeControlMode m)
        {
            return m != CampaignTimeControlMode.Stop
                && m != CampaignTimeControlMode.FastForwardStop;
        }

        /// <summary>
        /// 该档位**是否实际使用** `SpeedUpMultiplier`。
        ///
        /// 源码依据（`Campaign.cs:839-875` `TickMapTime`）—— 只有这两档乘倍率：
        ///   `StoppableFastForward` / `UnstoppableFastForward`（含 ForPartyWaitTime）。
        /// 其余档（`StoppablePlay` / `UnstoppablePlay`）用 `num2` 原值，**倍率被忽略**。
        ///
        /// ⇒ 这个判据必须回报给调用方：否则"设了倍率"会被误读成"时间变快了"，
        ///   而档位不对时倍率**静默无效**（无报错、无提示）——
        ///   属于本项目反复强调的"没报错但没生效"那类最难查的问题。
        /// </summary>
        private static bool UsesMultiplier(CampaignTimeControlMode m)
        {
            return m == CampaignTimeControlMode.StoppableFastForward
                || m == CampaignTimeControlMode.UnstoppableFastForward
                || m == CampaignTimeControlMode.UnstoppableFastForwardForPartyWaitTime;
        }

        private static bool TryParseTimeMode(string s, out CampaignTimeControlMode m)
        {
            switch ((s ?? "").Trim().ToLowerInvariant())
            {
                case "stop": m = CampaignTimeControlMode.Stop; return true;
                case "play": m = CampaignTimeControlMode.StoppablePlay; return true;
                case "fastforward":
                case "fast_forward":
                case "ff": m = CampaignTimeControlMode.StoppableFastForward; return true;
                case "unstoppableplay":
                case "unstoppable_play":
                case "uplay": m = CampaignTimeControlMode.UnstoppablePlay; return true;
                case "unstoppablefastforward":
                case "unstoppable_fast_forward":
                case "uff": m = CampaignTimeControlMode.UnstoppableFastForward; return true;
                case "fastforwardstop": m = CampaignTimeControlMode.FastForwardStop; return true;
                default: m = CampaignTimeControlMode.Stop; return false;
            }
        }

        // ══════════════════════════════════════════════════════════════
        // 宣战
        // ══════════════════════════════════════════════════════════════

        /// <summary>
        /// 让两个势力开战。参数：`faction1` / `faction2` 为 **StringId**
        /// （如 `empire_w` / `vlandia`），或名字包含匹配。
        /// 不传 faction2 ⇒ 自动挑一个"和 faction1 还没交战"的王国（省得调用方先查名字）。
        /// </summary>
        internal static string HandleDeclareWar(string id, string raw)
        {
            try
            {
                if (Campaign.Current == null)
                    return Protocol.Failure(id, "no_campaign",
                        "没有战役上下文 —— 主菜单 / 自定义战斗里不能宣战（需进战役存档）", false);

                string want1 = Jmini.Str(raw, "faction1", "").Trim();
                string want2 = Jmini.Str(raw, "faction2", "").Trim();

                IFaction f1 = ResolveFaction(want1);
                if (f1 == null)
                    return Protocol.Failure(id, "faction1_not_found",
                        "找不到 faction1=" + (want1.Length == 0 ? "(空)" : want1)
                        + "。可用：" + AvailableFactions(), false);

                // ── 不给 faction2 ⇒ 自动挑一个还没和 f1 交战的 ──────────────
                bool auto = want2.Length == 0;
                IFaction f2 = auto ? PickUnengagedEnemy(f1) : ResolveFaction(want2);
                if (f2 == null)
                {
                    if (auto)
                        return Protocol.Failure(id, "no_candidate",
                            f1.Name + " 已经和所有王国交战了（没有可自动挑的目标）—— "
                            + "请显式指定 faction2。可用：" + AvailableFactions(), false);
                    return Protocol.Failure(id, "faction2_not_found",
                        "找不到 faction2=" + want2 + "。可用：" + AvailableFactions(), false);
                }

                if (ReferenceEquals(f1, f2))
                    return Protocol.Failure(id, "same_faction",
                        "两个参数是同一个势力：" + SafeName(f1), false);

                // ── 幂等：本来就在交战 ⇒ 明确报，不当成功 ──────────────────
                if (FactionManager.IsAtWarAgainstFaction(f1, f2))
                {
                    StringBuilder ab = new StringBuilder();
                    ab.Append("{\"ok\":false,\"error\":\"already_at_war\"");
                    ab.Append(",\"faction1\":").Append(FactionJson(f1));
                    ab.Append(",\"faction2\":").Append(FactionJson(f2));
                    ab.Append(",\"note\":").Append(Protocol.Q(
                        "这两家**本来就在交战** —— 没有做任何改动（`WarDeclared` 事件不会再触发）。"
                        + "要造一场新的战争请换一对势力。"));
                    ab.Append(",\"atWarFactions\":").Append(AtWarList(f1));
                    ab.Append('}');
                    return Protocol.Success(id, ab.ToString());
                }

                // ── 宣战（走官方路径，会触发 OnWarDeclared）───────────────
                string detail = Jmini.Str(raw, "detail", "Default").Trim();
                DeclareWarAction.DeclareWarDetail d;
                if (!TryParseDetail(detail, out d))
                {
                    return Protocol.Failure(id, "bad_detail",
                        "detail 非法：" + detail + "（可用：Default / CausedByPlayerHostility / "
                        + "CausedByKingdomDecision / CausedByRebellion / CausedByCrimeRatingChange / "
                        + "CausedByKingdomCreation / CausedByClaimOnThrone / CausedByCallToWarAgreement）", false);
                }

                DeclareWarAction.ApplyByDefault(f1, f2);

                // ── ★ 写完回读：不假设 API 一定生效 ────────────────────────
                bool nowAtWar = FactionManager.IsAtWarAgainstFaction(f1, f2);

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":").Append(nowAtWar ? "true" : "false");
                sb.Append(",\"action\":\"declare_war\"");
                sb.Append(",\"detail\":").Append(Protocol.Q(d.ToString()));
                sb.Append(",\"autoPicked\":").Append(Jw.B(auto));
                sb.Append(",\"faction1\":").Append(FactionJson(f1));
                sb.Append(",\"faction2\":").Append(FactionJson(f2));
                sb.Append(",\"verifiedAtWar\":").Append(Jw.B(nowAtWar));
                if (!nowAtWar)
                {
                    sb.Append(",\"error\":\"verify_failed\"");
                    sb.Append(",\"note\":").Append(Protocol.Q(
                        "`DeclareWarAction.ApplyByDefault` 已调用，但**回读仍是未交战** —— "
                        + "请当作失败处理（不要假设宣战成功了）。"));
                }
                else
                {
                    sb.Append(",\"note\":").Append(Protocol.Q(
                        "已宣战并**回读确认**。`WarDeclared` 事件已触发（观察者应记到一条）"
                        + "；后续围攻 / 野战 / 聚落易主会在**游戏时间推进**后自然涌现 —— "
                        + "本工具只造这一下刺激，不会替你推进时间。"));
                }
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                // 宣战已产生副作用但后面读崩了 ⇒ outcomeUncertain=true（不可盲目重试）
                return Protocol.Failure(id, "declare_war_failed",
                    ex.GetType().Name + ": " + ex.Message, true);
            }
        }

        // ══════════════════════════════════════════════════════════════
        // 议和
        // ══════════════════════════════════════════════════════════════

        internal static string HandleMakePeace(string id, string raw)
        {
            try
            {
                if (Campaign.Current == null)
                    return Protocol.Failure(id, "no_campaign", "没有战役上下文 —— 需进战役存档", false);

                string want1 = Jmini.Str(raw, "faction1", "").Trim();
                string want2 = Jmini.Str(raw, "faction2", "").Trim();
                IFaction f1 = ResolveFaction(want1);
                if (f1 == null)
                    return Protocol.Failure(id, "faction1_not_found",
                        "找不到 faction1=" + want1 + "。可用：" + AvailableFactions(), false);

                bool auto = want2.Length == 0;
                IFaction f2 = auto ? PickCurrentEnemy(f1) : ResolveFaction(want2);
                if (f2 == null)
                {
                    if (auto)
                        return Protocol.Failure(id, "no_candidate",
                            f1.Name + " 当前没有交战对象（没什么可议和的）—— 请显式指定 faction2。", false);
                    return Protocol.Failure(id, "faction2_not_found",
                        "找不到 faction2=" + want2 + "。可用：" + AvailableFactions(), false);
                }
                if (ReferenceEquals(f1, f2))
                    return Protocol.Failure(id, "same_faction", "两个参数是同一个势力", false);

                if (!FactionManager.IsAtWarAgainstFaction(f1, f2))
                {
                    StringBuilder ab = new StringBuilder();
                    ab.Append("{\"ok\":false,\"error\":\"not_at_war\"");
                    ab.Append(",\"faction1\":").Append(FactionJson(f1));
                    ab.Append(",\"faction2\":").Append(FactionJson(f2));
                    ab.Append(",\"note\":").Append(Protocol.Q(
                        "这两家**本来就没在交战** —— 没有做任何改动（`MakePeace` 事件不会再触发）。"));
                    ab.Append('}');
                    return Protocol.Success(id, ab.ToString());
                }

                MakePeaceAction.Apply(f1, f2);

                bool nowPeace = !FactionManager.IsAtWarAgainstFaction(f1, f2);
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":").Append(nowPeace ? "true" : "false");
                sb.Append(",\"action\":\"make_peace\"");
                sb.Append(",\"autoPicked\":").Append(Jw.B(auto));
                sb.Append(",\"faction1\":").Append(FactionJson(f1));
                sb.Append(",\"faction2\":").Append(FactionJson(f2));
                sb.Append(",\"verifiedPeace\":").Append(Jw.B(nowPeace));
                // ── 失败时的**自解释诊断**（不让人猜）─────────────────────
                //
                // 实测：`MakePeaceAction.Apply` 调了但回读仍敌对，且 `OnMakePeace`
                // 事件也没派发。可能是三层原因之一，逐层报出来：
                //   ① `IsAtConstantWar` 为真 ⇒ 引擎**设计上**就议和不了
                //      （同文化的小阵营 vs 王国；或 `GetShallowDiplomaticStance` 返回 War）
                //   ② 某个 mod 覆盖了 `DiplomacyModel`，其 `GetShallowDiplomaticStance`
                //      对两个王国返回非 null ⇒ `SetNeutral` 的守卫 `!HasValue` 不成立 ⇒ 静默不动
                //   ③ `StanceType` 实际没被改写
                // ⇒ 这三条都读出来，调用方一眼能定性。
                if (!nowPeace)
                {
                    sb.Append(",\"error\":\"verify_failed\"");
                    sb.Append(",\"diagnosis\":").Append(PeaceDiagnosis(f1, f2));
                    sb.Append(",\"note\":").Append(Protocol.Q(
                        "已调用 MakePeaceAction.Apply，但**回读仍是交战** ⇒ 请当作失败。"
                        + "看 `diagnosis` 三条判据定位原因："
                        + "① `isAtConstantWar=true` ⇒ 引擎设计上就议和不了（同文化永久战），"
                        + "换一对**不同文化**的王国；"
                        + "② `diplomacyModelType` **不是** `...DefaultDiplomacyModel` ⇒ "
                        + "**有 mod 覆盖了外交模型**（实战实测：BellumCivile 的 "
                        + "CivilWarDiplomacyModel 会用 `BlockWhitePeacePatch` 之类拦下直接议和，"
                        + "议和要走它的内政/投票/白和平流程）—— 此时"
                        + "**本工具改不了和平，是那个 mod 的设计**，不是工具坏了；"
                        + "③ `shallowStance` 非 null ⇒ `FactionManager.SetNeutral` 的守卫"
                        + "（`!HasValue`）不成立 ⇒ 那种阵营组合天然改不了关系。"));
                }
                else
                {
                    sb.Append(",\"note\":").Append(Protocol.Q("已议和并回读确认；`MakePeace` 事件已触发。"));
                }
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "make_peace_failed",
                    ex.GetType().Name + ": " + ex.Message, true);
            }
        }

        /// <summary>
        /// 议和失败时的分层诊断（只读，不改任何东西）。
        ///
        /// 读三层，对应议和链上的三个可能断点：
        ///   ① `DiplomacyModel.IsAtConstantWar(f1,f2)` —— 引擎设计上的永久战
        ///   ② `DiplomacyModel.GetShallowDiplomaticStance(f1,f2)` —— 非 null 则
        ///      `SetNeutral` 的守卫不成立（静默不动）。**这正是"某个 mod 覆盖了
        ///      DiplomacyModel"的指纹**（原版对两个王国之间恒返回 null）。
        ///   ③ `f1.GetStanceWith(f2).StanceType` —— 底层关系类型实际值
        /// 全部走反射 + 兜底，读不到就报 "unknown"，**绝不猜**。
        /// </summary>
        private static string PeaceDiagnosis(IFaction f1, IFaction f2)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append('{');

            // ① IsAtConstantWar
            string cw = "unknown";
            try
            {
                object model = P(Campaign.Current, "Models");
                object dip = P(model, "DiplomacyModel");
                if (dip != null)
                {
                    MethodInfo mi = dip.GetType().GetMethod("IsAtConstantWar",
                        BindingFlags.Public | BindingFlags.Instance);
                    if (mi != null)
                    {
                        object v = mi.Invoke(dip, new object[] { f1, f2 });
                        cw = (v is bool && (bool)v) ? "true" : "false";
                    }
                }
            }
            catch
            {
            }
            sb.Append("\"isAtConstantWar\":").Append(cw);

            // ② GetShallowDiplomaticStance（mod 覆盖的指纹）
            string sh = "unknown";
            try
            {
                object model = P(Campaign.Current, "Models");
                object dip = P(model, "DiplomacyModel");
                if (dip != null)
                {
                    MethodInfo mi = dip.GetType().GetMethod("GetShallowDiplomaticStance",
                        BindingFlags.Public | BindingFlags.Instance);
                    if (mi != null)
                    {
                        object v = mi.Invoke(dip, new object[] { f1, f2 });
                        sh = (v == null) ? "null" : v.ToString();
                    }
                }
            }
            catch
            {
            }
            sb.Append(",\"shallowStance\":").Append(Protocol.Q(sh));

            // ③ 底层 StanceType
            //
            // ⚠️ `GetStanceWith` 声明在 **接口 `IFaction`** 上（`IFaction.cs:96`），
            //   具体类型 `Kingdom` / `Clan` 各自实现它。第一版只查具体类型
            //   ⇒ 实测拿到 "unknown"。这里两种都试：
            //     先具体类型，miss 再遍历接口。（与 CampaignObserver.P 同策略：
            //     本项目已两次栽在"显式接口实现/接口声明"上。）
            string st = "unknown";
            try
            {
                MethodInfo gs = f1.GetType().GetMethod("GetStanceWith",
                    BindingFlags.Public | BindingFlags.Instance);
                if (gs == null)
                {
                    Type[] ifs = f1.GetType().GetInterfaces();
                    for (int i = 0; i < ifs.Length && gs == null; i++)
                    {
                        gs = ifs[i].GetMethod("GetStanceWith",
                            BindingFlags.Public | BindingFlags.Instance);
                    }
                }
                if (gs != null)
                {
                    object stance = gs.Invoke(f1, new object[] { f2 });
                    if (stance == null)
                    {
                        st = "null";
                    }
                    else
                    {
                        // `StanceLink.StanceType` 可能是字段也可能是属性 —— 两条都试。
                        object t = P(stance, "StanceType");
                        if (t != null)
                            st = t.ToString();
                        else
                            st = "(StanceLink 无 StanceType: " + stance.GetType().Name + ")";
                        // 附带 IsAtWar（StanceLink 的公开布尔，通常比 StanceType 更直白）
                        object atWar = P(stance, "IsAtWar");
                        if (atWar is bool) st += "|IsAtWar=" + ((bool)atWar ? "true" : "false");
                    }
                }
            }
            catch
            {
            }
            sb.Append(",\"stanceType\":").Append(Protocol.Q(st));

            // 附：DiplomacyModel 的实际类型 —— 若不是 Default* 前缀，就是被 mod 覆盖了
            string modelType = "unknown";
            try
            {
                object dip = P(P(Campaign.Current, "Models"), "DiplomacyModel");
                if (dip != null) modelType = dip.GetType().FullName;
            }
            catch
            {
            }
            sb.Append(",\"diplomacyModelType\":").Append(Protocol.Q(modelType));
            sb.Append('}');
            return sb.ToString();
        }

        // ══════════════════════════════════════════════════════════════
        // 查询：现在是哪几家在交战（造刺激前先看，避免瞎猜）
        // ══════════════════════════════════════════════════════════════
        internal static string HandleWarStatus(string id, string raw)
        {
            try
            {
                if (Campaign.Current == null)
                    return Protocol.Failure(id, "no_campaign", "没有战役上下文 —— 需进战役存档", false);

                List<string> wars = new List<string>();
                Dictionary<string, int> count = new Dictionary<string, int>();
                var kingdoms = Campaign.Current.Kingdoms;
                for (int i = 0; i < kingdoms.Count; i++)
                {
                    for (int j = i + 1; j < kingdoms.Count; j++)
                    {
                        IFaction a = kingdoms[i];
                        IFaction b = kingdoms[j];
                        if (a == null || b == null) continue;
                        if (!FactionManager.IsAtWarAgainstFaction(a, b)) continue;
                        wars.Add("{\"a\":" + FactionJson(a) + ",\"b\":" + FactionJson(b) + "}");
                        string na = SafeName(a), nb = SafeName(b);
                        int c;
                        count.TryGetValue(na, out c); count[na] = c + 1;
                        count.TryGetValue(nb, out c); count[nb] = c + 1;
                    }
                }

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"kingdomCount\":").Append(kingdoms.Count);
                sb.Append(",\"warCount\":").Append(wars.Count);
                sb.Append(",\"wars\":[").Append(string.Join(",", wars.ToArray())).Append(']');
                sb.Append(",\"warCountByKingdom\":{");
                bool first = true;
                foreach (KeyValuePair<string, int> kv in count)
                {
                    if (!first) sb.Append(',');
                    first = false;
                    sb.Append(Protocol.Q(kv.Key)).Append(':').Append(kv.Value.ToString(CultureInfo.InvariantCulture));
                }
                sb.Append('}');
                sb.Append(",\"note\":").Append(Protocol.Q(
                    wars.Count == 0
                        ? "当前**没有任何王国交战** ⇒ 观察者收不到 war_declared / siege_started / "
                          + "settlement_owner_changed。要造条件用 `campaign_declare_war`（不传 faction2 会自动挑一对）。"
                        : "已有 " + wars.Count + " 对交战。"));
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "war_status_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        // ══════════════════════════════════════════════════════════════
        // 辅助
        // ══════════════════════════════════════════════════════════════

        /// <summary>
        /// 按 StringId 精确匹配，退化为名字包含匹配（大小写不敏感）。
        /// 与 `CampaignReadProbe.FindHeroByStringId` 同策略 —— 保持项目内一致。
        /// </summary>
        private static IFaction ResolveFaction(string want)
        {
            if (string.IsNullOrEmpty(want)) return null;
            want = want.Trim();
            var kingdoms = Campaign.Current.Kingdoms;

            // ① StringId 精确（最可靠：不受本地化影响）
            for (int i = 0; i < kingdoms.Count; i++)
            {
                IFaction k = kingdoms[i];
                if (k == null) continue;
                if (string.Equals(k.StringId, want, StringComparison.OrdinalIgnoreCase)) return k;
            }
            // ② 名字精确
            for (int i = 0; i < kingdoms.Count; i++)
            {
                IFaction k = kingdoms[i];
                if (k == null || k.Name == null) continue;
                if (string.Equals(k.Name.ToString(), want, StringComparison.OrdinalIgnoreCase)) return k;
            }
            // ③ 名字包含（退一步，便于手输）
            for (int i = 0; i < kingdoms.Count; i++)
            {
                IFaction k = kingdoms[i];
                if (k == null || k.Name == null) continue;
                if (k.Name.ToString().IndexOf(want, StringComparison.OrdinalIgnoreCase) >= 0) return k;
            }
            return null;
        }

        /// <summary>挑一个和 f1 还没交战的王国（优先有封地的，避免挑到名存实亡的）。</summary>
        private static IFaction PickUnengagedEnemy(IFaction f1)
        {
            IFaction best = null;
            int bestFiefs = -1;
            var kingdoms = Campaign.Current.Kingdoms;
            for (int i = 0; i < kingdoms.Count; i++)
            {
                IFaction k = kingdoms[i];
                if (k == null || ReferenceEquals(k, f1)) continue;
                if (FactionManager.IsAtWarAgainstFaction(f1, k)) continue;
                int fiefs = CountFiefs(k);
                if (fiefs > bestFiefs)
                {
                    bestFiefs = fiefs;
                    best = k;
                }
            }
            return best;
        }

        private static IFaction PickCurrentEnemy(IFaction f1)
        {
            var kingdoms = Campaign.Current.Kingdoms;
            for (int i = 0; i < kingdoms.Count; i++)
            {
                IFaction k = kingdoms[i];
                if (k == null || ReferenceEquals(k, f1)) continue;
                if (FactionManager.IsAtWarAgainstFaction(f1, k)) return k;
            }
            return null;
        }

        private static int CountFiefs(IFaction f)
        {
            try
            {
                object fiefs = P(f, "Fiefs");
                if (fiefs is System.Collections.ICollection c) return c.Count;
                object n = P(fiefs, "Count");
                if (n is int i) return i;
            }
            catch
            {
            }
            return 0;
        }

        private static bool TryParseDetail(string s, out DeclareWarAction.DeclareWarDetail d)
        {
            try
            {
                d = (DeclareWarAction.DeclareWarDetail)Enum.Parse(
                    typeof(DeclareWarAction.DeclareWarDetail), s, true);
                return true;
            }
            catch
            {
                d = DeclareWarAction.DeclareWarDetail.Default;
                return false;
            }
        }

        private static string SafeName(IFaction f)
        {
            try
            {
                if (f == null) return "";
                if (f.Name != null) return f.Name.ToString();
                return f.StringId ?? "";
            }
            catch
            {
                return "";
            }
        }

        private static string FactionJson(IFaction f)
        {
            if (f == null) return "null";
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"name\":").Append(Protocol.Q(SafeName(f)));
            sb.Append(",\"stringId\":").Append(Protocol.Q(SafeStringId(f)));
            sb.Append(",\"fiefs\":").Append(CountFiefs(f).ToString(CultureInfo.InvariantCulture));
            sb.Append('}');
            return sb.ToString();
        }

        private static string SafeStringId(IFaction f)
        {
            try
            {
                return f == null ? "" : (f.StringId ?? "");
            }
            catch
            {
                return "";
            }
        }

        private static string AvailableFactions()
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                var kingdoms = Campaign.Current.Kingdoms;
                for (int i = 0; i < kingdoms.Count; i++)
                {
                    IFaction k = kingdoms[i];
                    if (k == null) continue;
                    if (sb.Length > 0) sb.Append(" / ");
                    sb.Append(SafeStringId(k)).Append('(').Append(SafeName(k)).Append(')');
                }
                return sb.ToString();
            }
            catch
            {
                return "(读取失败)";
            }
        }

        private static string AtWarList(IFaction f1)
        {
            try
            {
                List<string> names = new List<string>();
                var kingdoms = Campaign.Current.Kingdoms;
                for (int i = 0; i < kingdoms.Count; i++)
                {
                    IFaction k = kingdoms[i];
                    if (k == null || ReferenceEquals(k, f1)) continue;
                    if (FactionManager.IsAtWarAgainstFaction(f1, k)) names.Add(SafeStringId(k));
                }
                StringBuilder sb = new StringBuilder("[");
                for (int i = 0; i < names.Count; i++)
                {
                    if (i > 0) sb.Append(',');
                    sb.Append(Protocol.Q(names[i]));
                }
                sb.Append(']');
                return sb.ToString();
            }
            catch
            {
                return "[]";
            }
        }

        private static object P(object o, string name)
        {
            if (o == null || string.IsNullOrEmpty(name)) return null;
            try
            {
                Type t = o.GetType();
                PropertyInfo pi = t.GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
                if (pi != null) { try { return pi.GetValue(o, null); } catch { } }
                // ★ 显式接口实现 / 结构体字段：去接口上再找一遍（本项目已多次栽在这）
                Type[] ifs = t.GetInterfaces();
                for (int i = 0; i < ifs.Length; i++)
                {
                    PropertyInfo ip = ifs[i].GetProperty(name,
                        BindingFlags.Public | BindingFlags.Instance);
                    if (ip == null) continue;
                    try
                    {
                        object v = ip.GetValue(o, null);
                        if (v != null) return v;
                    }
                    catch { }
                }
                FieldInfo fi = t.GetField(name, BindingFlags.Public | BindingFlags.Instance);
                if (fi != null) { try { return fi.GetValue(o); } catch { } }
            }
            catch
            {
            }
            return null;
        }

        /// <summary>把任意值安全转成 JSON 片段（供取字段用；与 CampaignReadProbe.JVal 同策略）。</summary>
        private static string JVal(object v)
        {
            if (v == null) return "null";
            if (v is bool b) return b ? "true" : "false";
            if (v is string s) return Protocol.Q(s);
            if (v is Enum) return Protocol.Q(v.ToString());
            if (v is IFormattable f)
                return f.ToString(null, CultureInfo.InvariantCulture);
            return Protocol.Q(v.ToString());
        }

        /// <summary>反射取一个成员并转字符串（读不到就空串，绝不抛）。</summary>
        private static string SafeStr(object o, string name)
        {
            try
            {
                object v = P(o, name);
                if (v == null) return "";
                // `TextObject` 等走 ToString
                return v.ToString();
            }
            catch
            {
                return "";
            }
        }
    }
}
