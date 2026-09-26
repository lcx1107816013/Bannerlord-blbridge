namespace BlBridge
{
    /// <summary>
    /// 「主菜单层面」的活动状态名 —— **唯一实现**，C# 侧判据与**离线单测**共用。
    ///
    /// 为什么单独放一个只依赖 BCL 的文件：`tools/jsontest` 的离线单测要把它编译进去做对照断言
    /// （`GuardTest.cs` 的「主菜单层面」段），而 `src/ScenarioRunner.cs` 碰 TaleWorlds、编不进离线测试。
    ///
    /// 集合（与 `tools/bl_mcp.py` 的 `_MAIN_MENU_ACTIVE_STATES` **必须一致**，两侧各有断言）：
    ///   ""             —— 取不到状态名（启动初期/过渡期）
    ///   "InitialState" —— 主菜单本体（真机实测 2026-09-25 23:00，v0.8.21）
    ///
    /// 为什么这行代码值得单开文件（真机血证）：v0.8.22 把"取状态机"改成静态优先
    /// （`ScenarioRunner.ActiveStateManager()`）后，主菜单的名字从**空串**变成 **`InitialState`**，
    /// 而 `UiEntry` 的 open_ui 闸门当时写的是 `stateBefore.Length != 0`（"空串 = 主菜单"）
    /// ⇒ **真主菜单被判成"已加载 Game"**，`open_ui` 一律被拒
    /// （真机 2026-09-25 23:05 实测：`requested=None` / `wrong_state_for_ui`（state=InitialState））。
    /// </summary>
    internal static class MainMenuStates
    {
        internal const string Name = "InitialState";

        /// <summary>空串（取不到名字）与 `InitialState`（主菜单本体）都算「主菜单层面」。</summary>
        internal static bool IsMenuLevel(string stateName)
        {
            return string.IsNullOrEmpty(stateName) || stateName == Name;
        }
    }
}
