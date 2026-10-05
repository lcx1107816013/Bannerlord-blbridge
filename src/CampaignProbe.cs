using System;
using System.Collections.Generic;
using System.Text;

using TaleWorlds.Core;              // MBSaveLoad
using TaleWorlds.CampaignSystem;    // Campaign（in_campaign 前置检查用）
using TaleWorlds.MountAndBlade;     // MBGameManager
using TaleWorlds.SaveSystem;        // SaveGameFileInfo
using TaleWorlds.SaveSystem.Load;   // LoadResult（注意在 Load 子命名空间）
using SandBox;                      // SandBoxGameManager（Sandbox 模块的 DLL，build.ps1 显式引用）

namespace BlBridge
{
    /// <summary>
    /// L2 #2 续：campaign/list_saves + campaign/load_save（脱壳抄 BUTR/Bannerlord.GABS 的
    /// CoreTools.cs core/list_saves + core/load_save —— LoadSaveGameData 直载 + StartNewGame，
    /// **不经过存档选择界面**，也不需要人坐在载入菜单前）。
    ///
    /// 为什么这条对项目结构重要（用户 2026-09-27 指出）：要复现"模组不匹配"这类读档期弹窗、
    /// 要在无人值守下进出战役，就必须能按名字换档 —— ContinueCampaign 只会读最新档。
    ///
    /// 前置检查（显式失败，不猜）：
    ///   • load_save 在战役进行中会拒绝（in_campaign）—— 往正在跑的战役上再 StartNewGame 的
    ///     行为未定义，不做。
    ///   • 名字必须精确匹配 MBSaveLoad.GetSaveFiles 给出的 Name（不模糊、不猜）。
    /// 加载是异步的：load_save 立即返回，调用方轮询 list_ui 等 topScreen 变成 *MapScreen。
    /// </summary>
    internal static class CampaignProbe
    {
        // ── 失焦"保活"已按用户要求移除（v0.8.39，2026-09-27 真机）──────────────────
        // 曾经的做法（§三十一）：开着时每帧把失焦造成的 TimeControlMode=Stop 恢复回原档位，
        // 挂在 SubModule.OnApplicationTick 上。三条删除理由：
        //   ① 用户 2026-09-27 明确"游戏时间暂停不用改"，他要的只是**切窗口不弹暂停菜单**——
        //      那件事的开关是原版 BannerlordConfig.StopGameOnFocusLost（已置 False，见 §三十二）；
        //   ② 它治不了真病：失焦弹出的暂停菜单走的是
        //      MapScreen.OnEscapeMenuToggled → GameStateManager.RegisterActiveStateDisableRequest
        //      ⇒ MapState 不再 Tick，而**TimeControlMode 始终是 StoppablePlay**，
        //      所以"看到 Stop 就恢复"这套判据在结构上看不见它（真机实测就是这么被误导的）；
        //   ③ 副作用：开着时选手动暂停也会被立刻顶掉。
        // 现在 bl_campaign_time 只剩只读诊断：档位 / 菜单上下文 / 战役天数 / 暂停菜单是否开着。

        /// <summary>
        /// v0.8.38：暂停菜单（地图上的 ESC 菜单）此刻是否开着。
        ///
        /// 为什么这是必须的判据：原版失焦 + BannerlordConfig.StopGameOnFocusLost 会**自动打开**
        /// 这个菜单（MapScreen.OnFocusChangeOnGameWindow → OnEscapeMenuToggled(true)），
        /// 而 OnEscapeMenuToggled(true) 会 GameStateManager.RegisterActiveStateDisableRequest(this)
        /// ⇒ MapState 不再 Tick ⇒ 整个战役冻结（用户看到的就是"游戏卡住 + 暂停菜单"）。
        /// 要命的是这条路径**不改 TimeControlMode**（一直是 StoppablePlay），所以保活那套
        /// "看到 Stop 就恢复"的判据完全看不见它 —— 2026-09-27 真机就是这么被误导的。
        /// 判据取 ScreenManager.TopScreen（战役地图上是 NavalMapScreen : MapScreen）的
        /// IsEscapeMenuOpened（public getter）。
        /// </summary>
        private static bool IsPauseMenuOpen()
        {
            try
            {
                var screen = TaleWorlds.ScreenSystem.ScreenManager.TopScreen
                             as SandBox.View.Map.MapScreen;
                return screen != null && screen.IsEscapeMenuOpened;
            }
            catch (Exception)
            {
                // 判据失败绝不能把命令炸了；当作"读不到"（false）并在文档里写清语义
                return false;
            }
        }

        internal static string HandleCampaignTime(string id, string raw)
        {
            try
            {
                string mode = Jmini.Str(raw, "mode", "status").Trim().ToLowerInvariant();
                if (mode != "status")
                {
                    // 显式失败，不静默忽略：老脚本还会传 mode=on/off，必须让它当场看见"功能已撤"。
                    return Protocol.Failure(id, "keep_awake_removed",
                        "时间保活（mode=on/off）已移除（v0.8.39）：用户明确不需要改游戏的时间暂停；"
                        + "而且它看不见失焦自动打开的暂停菜单（那条路径不改 TimeControlMode），"
                        + "副作用还会把手动暂停顶掉。要「切窗口不弹暂停菜单」请用原版选项 "
                        + "BannerlordConfig.StopGameOnFocusLost=False（见 PROGRESS §三十二）。",
                        false);
                }

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"inCampaign\":").Append(Campaign.Current != null ? "true" : "false");
                sb.Append(",\"pauseMenuOpen\":").Append(IsPauseMenuOpen() ? "true" : "false");
                if (Campaign.Current != null)
                {
                    sb.Append(",\"timeControlMode\":").Append(Protocol.Q(Campaign.Current.TimeControlMode.ToString()));
                    sb.Append(",\"inMenuContext\":").Append(Campaign.Current.CurrentMenuContext != null ? "true" : "false");
                    // v0.8.37：单调递增的战役天数（CampaignTime.Now.ToDays）。
                    // 为什么必须有它：TimeControlMode 只能证明"没有人显式把时间设成 Stop"，
                    // 证明不了引擎有没有把战役推进冻住（挂机时用户看到的就是"画面不动"）。
                    // 失焦前后各读一次，天数涨了才算真的没暂停。
                    sb.Append(",\"campaignDays\":").Append(
                        CampaignTime.Now.ToDays.ToString("F4", System.Globalization.CultureInfo.InvariantCulture));
                }
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "campaign_time_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        internal static string HandleListSaves(string id, string raw)
        {
            try
            {
                var saves = MBSaveLoad.GetSaveFiles(null);
                List<string> rows = new List<string>();
                foreach (var s in saves)
                {
                    StringBuilder one = new StringBuilder();
                    one.Append('{');
                    one.Append("\"name\":").Append(Protocol.Q(s.Name ?? ""));
                    one.Append(",\"isCorrupted\":").Append(s.IsCorrupted ? "true" : "false");
                    // MetaData 是键值对（键随版本变），整包带上但每值截断，避免调用方猜键名
                    StringBuilder meta = new StringBuilder();
                    if (s.MetaData != null)
                    {
                        bool first = true;
                        int used = 0;
                        foreach (string k in s.MetaData.Keys)
                        {
                            if (used >= 24) break;
                            string v = s.MetaData[k];
                            if (v == null) continue;
                            if (v.Length > 80) v = v.Substring(0, 80);
                            if (!first) meta.Append(',');
                            first = false;
                            meta.Append(Protocol.Q(k)).Append(':').Append(Protocol.Q(v));
                            used++;
                        }
                    }
                    one.Append(",\"meta\":{").Append(meta.ToString()).Append('}');
                    one.Append('}');
                    rows.Add(one.ToString());
                }
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"count\":").Append(rows.Count);
                sb.Append(",\"saves\":[").Append(string.Join(",", rows.ToArray())).Append(']');
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "list_saves_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        internal static string HandleLoadSave(string id, string raw)
        {
            try
            {
                string saveName = Jmini.Str(raw, "name", "").Trim();
                if (saveName.Length == 0)
                    return Protocol.Failure(id, "bad_args", "name 必填（用 list_saves 里的 name，精确匹配）", false);

                if (Campaign.Current != null)
                    return Protocol.Failure(id, "in_campaign",
                        "战役进行中不能换档 —— 回主菜单后再 load_save（防止往运行中的战役上叠加新战役）", false);

                // 先按名字精确找一遍：不存在就当场报错并列出可用档名（属性名写错的教训同款）
                bool found = false;
                foreach (var s in MBSaveLoad.GetSaveFiles(null))
                {
                    if (s != null && string.Equals(s.Name, saveName, StringComparison.OrdinalIgnoreCase))
                    {
                        found = true;
                        break;
                    }
                }
                if (!found)
                {
                    List<string> names = new List<string>();
                    foreach (var s in MBSaveLoad.GetSaveFiles(null))
                    {
                        if (s != null && !string.IsNullOrEmpty(s.Name)) names.Add(s.Name);
                        if (names.Count >= 30) break;
                    }
                    return Protocol.Failure(id, "save_not_found",
                        "存档 \"" + saveName + "\" 不存在（可用的存档：" + string.Join(", ", names.ToArray()) + "）",
                        false);
                }

                LoadResult result = MBSaveLoad.LoadSaveGameData(saveName);
                if (result == null)
                    return Protocol.Failure(id, "load_failed", "LoadSaveGameData 返回空（存档可能损坏）", false);

                MBGameManager.StartNewGame(new SandBoxGameManager(result));

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"started\":").Append(Protocol.Q(saveName));
                sb.Append(",\"note\":\"异步加载：轮询 list_ui 等 topScreen 变成 *MapScreen（首次读档可能弹模组不匹配确认框，自动化会点『是』）\"}");
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "load_save_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }
    }
}
