using System;

namespace BlBridge
{
    /// <summary>
    /// `HarmonyMessage.ParsePatchFailure` 的离线单测（v0.8.47）。
    ///
    /// ## 为什么要测它
    ///
    /// `bl_patch_failures` 的价值全在"**能不能把目标解析出来**" ——
    /// 解析错了会给出**错误的 targetClass**，而下游 `bl_patches` 按它反查，
    /// 就会**指到一个无关的类型上**（比不解析更糟）。
    ///
    /// 它是**纯字符串处理**（`HarmonyMessage.cs` 无 TaleWorlds 依赖）
    /// ⇒ 完全没理由不做离线覆盖。本文件被 `tools/jsontest/build_and_run.ps1` 编译运行。
    ///
    /// ## 断言
    ///
    /// 1. **实测样例**（2026-10-06 真机抓到的那条）必须正确解析；
    /// 2. **没有 `args=`** 时也要能解析（描述里 args 是可选的）；
    /// 3. **空/垃圾输入**必须给 null（不抛、不返回空串）；
    /// 4. ★ **`args` 里带逗号**（多参数）不得影响 `class`/`methodname` 的解析；
    /// 5. **注入故障对照**：把 key 拼错必须**解析不到**（证明断言不是恒真）。
    /// </summary>
    internal static class HarmonyMessageTest
    {
        private static int _fail;

        private static void Check(bool cond, string label, string detail)
        {
            if (cond)
            {
                Console.WriteLine("  [OK]   " + label);
            }
            else
            {
                Console.WriteLine("  [FAIL] " + label + "  " + detail);
                _fail++;
            }
        }

        public static int Run()
        {
            Console.WriteLine("================================================================================");
            Console.WriteLine("⑯ HarmonyMessage.ParsePatchFailure（bl_patch_failures 的解析核心）");
            Console.WriteLine("================================================================================");

            string cls, meth;

            // ── ① 实测样例（真机抓到的原文）──
            HarmonyMessage.ParsePatchFailure(
                "Ambiguous match for HarmonyMethod[(class=TaleWorlds.CampaignSystem.CharacterDevelopment.TraitLevelingHelper, "
                + "methodname=OnIssueSolvedThroughQuest, type=Normal, args=undefined)]",
                out cls, out meth);
            Check(cls == "TaleWorlds.CampaignSystem.CharacterDevelopment.TraitLevelingHelper",
                "① 实测样例：class 解析正确", "得到 " + (cls ?? "null"));
            Check(meth == "OnIssueSolvedThroughQuest",
                "① 实测样例：methodname 解析正确", "得到 " + (meth ?? "null"));

            // ── ② 没有 args= 的形态 ──
            HarmonyMessage.ParsePatchFailure(
                "Ambiguous match for HarmonyMethod[(class=A.B.C, methodname=Foo, type=Normal)]",
                out cls, out meth);
            Check(cls == "A.B.C" && meth == "Foo",
                "② 无 args= 时仍能解析", "得到 " + (cls ?? "null") + " / " + (meth ?? "null"));

            // ── ③ 空/垃圾输入 ⇒ null（不抛）──
            HarmonyMessage.ParsePatchFailure(null, out cls, out meth);
            Check(cls == null && meth == null, "③ null 输入 ⇒ 两个 null", cls + "/" + meth);
            HarmonyMessage.ParsePatchFailure("", out cls, out meth);
            Check(cls == null && meth == null, "③ 空串 ⇒ 两个 null", cls + "/" + meth);
            HarmonyMessage.ParsePatchFailure("完全不是 Harmony 的消息", out cls, out meth);
            Check(cls == null && meth == null, "③ 无关文本 ⇒ 两个 null（不猜）", cls + "/" + meth);

            // ── ④ ★ args 里带逗号（多参数）──
            //    这一条最容易写错：若用 Split(',') 取第 2 段，class 就会被截断。
            HarmonyMessage.ParsePatchFailure(
                "Ambiguous match for HarmonyMethod[(class=X.Y, methodname=Bar, type=Normal, "
                + "args=System.Int32, System.String)]",
                out cls, out meth);
            Check(cls == "X.Y" && meth == "Bar",
                "④ args 带逗号不影响 class/methodname", "得到 " + (cls ?? "null") + " / " + (meth ?? "null"));

            // ── ⑤ ★ 注入故障对照：key 拼错必须解析不到 ──
            //    若这一条"通过"了（即仍能解析出东西），说明解析器在**猜**，断言就是恒真的。
            HarmonyMessage.ParsePatchFailure(
                "Ambiguous match for HarmonyMethod[(klass=X.Y, methodname=Bar)]",
                out cls, out meth);
            Check(cls == null, "⑤ 注入故障：class= 拼错 ⇒ 解析不到 class（证明①-④不是恒真）",
                "得到 " + (cls ?? "null"));
            Check(meth == "Bar", "⑤ 但 methodname 仍应解析到（两字段独立）", "得到 " + (meth ?? "null"));

            Console.WriteLine();
            if (_fail > 0)
            {
                Console.WriteLine("HarmonyMessage 单测失败 " + _fail + " 项");
                return 1;
            }
            Console.WriteLine("HarmonyMessage 单测全部通过。");
            return 0;
        }
    }
}
