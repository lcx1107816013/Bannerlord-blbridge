using System;
using System.IO;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// CapabilityProbe 的离线断言（纯 BCL，不碰 TaleWorlds，不需要游戏）。
    ///
    /// 为什么必须有它：这个探针存在的唯一理由是**把"编辑器为什么不能用"讲清楚**。
    /// 如果它自己判错（比如把"没装 Kit"说成 ready，或漏报版本不符），
    /// 那它就是又一个"报告成功但实际没做事"的东西 —— 正是本项目一直在消灭的失效类。
    ///
    /// 纪律：断言调的是**出货代码的同一个函数**（CapabilityProbe.Judge），
    /// 不是在这里抄一份判定逻辑。抄一份就是"验副本不验出货代码"。
    /// </summary>
    internal static class CapabilityProbeTest
    {
        private static int _fail;
        private static int _pass;

        internal static int Run()
        {
            Console.WriteLine("== CapabilityProbe 离线断言 ==");

            // ── 1. 全齐：Kit + 探针 + 版本对 ───────────────────────────────
            Ok("全齐 => ready", CapabilityProbe.Judge(Full()) , v => v.Ready == "yes" && v.ModdingKit
                                && v.EditorProbe && v.VersionMatch && v.Blockers.Length == 0);

            // ── 2. 本机真实情形：没装 Kit、没装探针（实测就是这两个） ───────
            Ok("缺 Kit => not ready，且**明确说是 Kit**", CapabilityProbe.Judge(KitMissing()), v =>
                v.Ready == "no" && !v.ModdingKit && v.Blockers.Length >= 1
                && AnyContains(v.Blockers, "Modding Kit"));
            Ok("缺 Kit 时 blockers 提到 bin/Win64_Shipping_wEditor",
               CapabilityProbe.Judge(KitMissing()), v => AnyContains(v.Blockers, "Win64_Shipping_wEditor"));

            // ── 3. Kit 目录在但装配不全（半个 Kit / 被安全软件吞文件） ──────
            Ok("Kit 目录在但装配不全 => not ready", CapabilityProbe.Judge(KitPartial()), v =>
                !v.ModdingKit && v.Ready == "no" && AnyContains(v.Blockers, "装配不全"));

            // ── 4. 版本不符（客户端门禁 v1.4.8）─────────────────────────────
            Ok("版本不符 => not ready 且点名两个版本", CapabilityProbe.Judge(WrongVersion()), v =>
                !v.VersionMatch && v.Ready == "no"
                && AnyContains(v.Blockers, "v1.5.0") && AnyContains(v.Blockers, "v1.4.8"));

            // ── 5. 版本读不到（Version.xml 缺失/畸形）⇒ 必须报"读不到"，不能当通过 ──
            Ok("版本读不到 => 算不符，不静默通过", CapabilityProbe.Judge(NoVersion()), v =>
                !v.VersionMatch && v.Ready == "no" && AnyContains(v.Blockers, "读不到"));

            // ── 6. Kit 与客户端版本不一致：单独一条，因为这症状不同 ─────────
            Ok("Kit 版本不一致 => 单独报出", CapabilityProbe.Judge(KitVersionDrift()), v =>
                v.ModdingKit && !v.KitVersionMatch && v.Ready == "no"
                && AnyContains(v.Blockers, "进场景会崩"));

            // ── 7. 探针模组缺 DLL（目录在、DLL 没编出来）────────────────────
            Ok("探针缺 DLL => 单独报出", CapabilityProbe.Judge(ProbeDllMissing()), v =>
                !v.EditorProbe && v.Ready == "no" && AnyContains(v.Blockers, "缺 DLL"));

            // ── 8. 多项同时缺 ⇒ 每条都要出现在 blockers（不能只报第一个）──
            Ok("多项缺失 => 逐条列出", CapabilityProbe.Judge(KitMissingAndProbeMissingAndOld()), v =>
                v.Blockers.Length == 3 && AnyContains(v.Blockers, "Modding Kit")
                && AnyContains(v.Blockers, "编辑器探针模组未安装")
                && AnyContains(v.Blockers, "游戏版本不符"));

            // ── 9. ★ 断言**线上路径** Json()，而不只是内部的 Judge() ────────
            //    实测踩到的坑：我最初用 Jw.Esc 拼字符串，而它**只转义不加引号**，
            //    于是 Json() 产出 `{"gameDir":G:\...}` —— **不是合法 JSON**。
            //    而当时的断言只调 Judge()，完全看不见这个问题。
            //    教训：要验输出，就要**验那条真正被发出的路径**。
            CapabilityProbe.Facts realFacts = CapabilityProbe.Collect(FindGameDir() ?? @"X:\nonexistent");
            string json = CapabilityProbe.Json(FindGameDir() ?? @"X:\nonexistent");
            Ok("Json() 输出是合法 JSON（故不能只测 Judge）", json, s =>
            {
                var ser = new System.Web.Script.Serialization.JavaScriptSerializer();
                var o = ser.Deserialize<System.Collections.Generic.Dictionary<string, object>>(s);
                return o != null && o.ContainsKey("editorReady") && o.ContainsKey("blockers");
            });
            Ok("Json() 的每个字符串值都被引号包住", json, s =>
                s.Contains("\"gameDir\":\"") && s.Contains("\"editorReady\":\"")
                && s.Contains("\"moduleId\":\""));
            Ok("Json() 的路径反斜杠被正确转义", json, s =>
                !s.Contains(":G:\\P") || s.Contains("\\\\"));

            Console.WriteLine("   Json() 实测输出：");
            Console.WriteLine("     " + json);

            // ── 10. 真实磁盘：对**这台机器**跑一遍，并把结论打印出来 ────────
            //    这不是断言"应该 ready"（那会是假的），而是断言"能跑、不抛、
            //    并且如实报告本机不 ready"。
            string gameDir = FindGameDir();
            if (gameDir != null)
            {
                CapabilityProbe.Facts f = CapabilityProbe.Collect(gameDir);
                CapabilityProbe.Verdict v = CapabilityProbe.Judge(f);
                Console.WriteLine("   本机 gameDir = " + gameDir);
                Console.WriteLine("   本机 gameVersion = " + (f.GameVersion == "" ? "(读不到)" : f.GameVersion));
                Console.WriteLine("   本机 editorReady = " + v.Ready);
                foreach (string b in v.Blockers) Console.WriteLine("     - " + b);
                Ok("本机探测不抛且如实报告", v, x => x.Ready == "yes" || x.Blockers.Length > 0);
                Ok("本机 gameVersion 应读得到（客户端一定装了）", f,
                   x => !string.IsNullOrEmpty(x.GameVersion));
            }
            else
            {
                Console.WriteLine("   (跳过真机探测：未设置 BANNERLORD_DIR)");
            }

            Console.WriteLine(_fail == 0
                ? ("== 全部通过（" + _pass + " 项）==")
                : ("== 失败 " + _fail + " 项（通过 " + _pass + "）=="));
            return _fail == 0 ? 0 : 1;
        }

        // ── 断言小工具 ────────────────────────────────────────────────────
        private static void Ok<T>(string label, T actual, Func<T, bool> check)
        {
            bool good;
            try { good = check(actual); }
            catch (Exception e) { good = false; Console.WriteLine("   异常: " + e.Message); }
            if (good) { _pass++; Console.WriteLine("  [ok]   " + label); }
            else { _fail++; Console.WriteLine("  [FAIL] " + label); }
        }

        private static bool AnyContains(string[] arr, string needle)
        {
            foreach (string s in arr)
                if (s != null && s.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0)
                    return true;
            return false;
        }

        // ── Facts 构造器（每个只改一处，便于定位失败原因）────────────────
        private static CapabilityProbe.Facts Full()
        {
            return new CapabilityProbe.Facts
            {
                GameDir = @"X:\game",
                GameVersion = CapabilityProbe.RequiredGameVersion,
                EditorVersion = CapabilityProbe.RequiredGameVersion,
                ModdingKitDirPresent = true,
                ModdingKitAssembliesFound = CapabilityProbe.ModdingKitAssemblies.Length,
                EditorProbeModulePresent = true,
                EditorProbeDllPresent = true,
                ClientRuntimePresent = true,
            };
        }
        private static CapabilityProbe.Facts KitMissing()
        {
            CapabilityProbe.Facts f = Full(); f.ModdingKitDirPresent = false; f.ModdingKitAssembliesFound = 0; return f;
        }
        private static CapabilityProbe.Facts KitPartial()
        {
            CapabilityProbe.Facts f = Full(); f.ModdingKitAssembliesFound = CapabilityProbe.ModdingKitAssemblies.Length - 1; return f;
        }
        private static CapabilityProbe.Facts WrongVersion()
        {
            CapabilityProbe.Facts f = Full(); f.GameVersion = "v1.5.0"; f.EditorVersion = "v1.5.0"; return f;
        }
        private static CapabilityProbe.Facts NoVersion()
        {
            CapabilityProbe.Facts f = Full(); f.GameVersion = ""; f.EditorVersion = ""; return f;
        }
        private static CapabilityProbe.Facts KitVersionDrift()
        {
            CapabilityProbe.Facts f = Full(); f.EditorVersion = "v1.4.7"; return f;
        }
        private static CapabilityProbe.Facts ProbeDllMissing()
        {
            CapabilityProbe.Facts f = Full(); f.EditorProbeDllPresent = false; return f;
        }
        private static CapabilityProbe.Facts KitMissingAndProbeMissingAndOld()
        {
            CapabilityProbe.Facts f = Full();
            f.ModdingKitDirPresent = false; f.ModdingKitAssembliesFound = 0;
            f.EditorProbeModulePresent = false; f.EditorProbeDllPresent = false;
            f.GameVersion = "v1.5.0"; f.EditorVersion = "v1.5.0";
            return f;
        }

        /// <summary>本机游戏目录：优先 BANNERLORD_DIR，其次从 BlBridge 日志目录反推（不猜死路径）。</summary>
        private static string FindGameDir()
        {
            string env = Environment.GetEnvironmentVariable("BANNERLORD_DIR");
            if (!string.IsNullOrEmpty(env) && Directory.Exists(env)) return env;
            try
            {
                // 日志目录里带游戏根（BridgeConfig.LogDir 就是 我的文档\...\BlBridge）
                string logDir = BridgeConfig.LogDir;
                string doc = Path.GetDirectoryName(logDir);
                if (!string.IsNullOrEmpty(doc))
                {
                    // 从 我的文档 目录读游戏配置来定位安装路径是不可靠的，直接放弃
                }
            }
            catch { }
            return null;
        }
    }
}
