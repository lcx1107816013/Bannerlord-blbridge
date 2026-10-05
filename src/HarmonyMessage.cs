using System;

namespace BlBridge
{
    /// <summary>
    /// 从 Harmony 的 `HarmonyException` 消息里解析「**补丁想补的目标**」。
    ///
    /// ## 为什么单独成一个文件（纯 BCL，无 TaleWorlds 依赖）
    ///
    /// 这样它就能被 `tools/jsontest/build_and_run.ps1` **离线单测**
    /// （那个测试架只编"纯 BCL"的源文件，见 `SquadSpec.cs` / `OrderSpec.cs` 的先例）。
    /// 把解析逻辑留在 `ExceptionProbe` 里的话它就只能真机验 —— 而这是**纯字符串处理**，
    /// 完全没理由不做离线覆盖。
    ///
    /// ## 消息格式（反编译 `0Harmony` 2.4.2 确认，非推测）
    ///
    /// ```csharp
    /// // Harmony.cs:6028
    /// throw new HarmonyException("Ambiguous match for HarmonyMethod[" + attr.Description() + "]", ...);
    /// ```
    ///
    /// `attr.Description()` 的形状：
    /// ```
    /// (class=<类型全名>, methodname=<方法名>, type=Normal, args=<参数或不写>)
    /// ```
    ///
    /// 实测样例（2026-10-06）：
    /// ```
    /// Ambiguous match for HarmonyMethod[(class=TaleWorlds.CampaignSystem.CharacterDevelopment.TraitLevelingHelper,
    ///   methodname=OnIssueSolvedThroughQuest, type=Normal, args=undefined)]
    /// ```
    ///
    /// ## ⚠️ 边界（很重要）
    ///
    /// 解析出的只有**补丁的目标**。**发起补丁的 mod 不在这条消息里** ——
    /// 要定位到 mod，必须把 `targetClass` 交给 `bl_patches` 反查。
    /// </summary>
    internal static class HarmonyMessage
    {
        /// <summary>
        /// 解析一条 `HarmonyException` 消息。解析不出来时**两个 out 都是 null**
        /// （不抛异常、不返回空串 —— 让调用方能区分"没解析到"与"解析到空"）。
        /// </summary>
        public static void ParsePatchFailure(string message,
            out string targetClass, out string targetMethod)
        {
            targetClass = null;
            targetMethod = null;
            if (string.IsNullOrEmpty(message)) return;

            try
            {
                targetClass = ExtractField(message, "class=");
                targetMethod = ExtractField(message, "methodname=");
            }
            catch
            {
                // 解析失败不该抛（调用方在异常处理的路径上）
                targetClass = null;
                targetMethod = null;
            }
        }

        /// <summary>
        /// 取 `key=value` 里的 value（到 `,` 或 `)` 为止）。
        ///
        /// ⚠️ 用 `IndexOfAny(new[]{',', ')'})` 而不是 `Split(',')`：
        ///    `args=` 里可能带逗号（多参数），而我们要的是**前一个字段**；
        ///    且**没有 `args=`** 时字段以 `)` 结尾（描述里 `args` 是可选的）。
        /// </summary>
        private static string ExtractField(string message, string key)
        {
            int k = message.IndexOf(key, StringComparison.Ordinal);
            if (k < 0) return null;
            int start = k + key.Length;
            if (start >= message.Length) return "";
            int end = message.IndexOfAny(new char[] { ',', ')' }, start);
            string v = (end > start) ? message.Substring(start, end - start) : message.Substring(start);
            return v.Trim();
        }
    }
}
