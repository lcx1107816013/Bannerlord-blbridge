using System;
using System.IO;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// 游戏端能力探测（Game-side capability probe）。
    ///
    /// 为什么需要它
    /// ------------
    /// 「编辑器能不能用」在本项目里一直是**猜**的：探针的 C# 引用的是
    /// `bin/Win64_Shipping_wEditor/*`（**Modding Kit** 的装配），客户端还硬门禁
    /// `v1.4.8`；而 Kit 没装时，`.csproj` 会漏引用、MCP 侧只报一个笼统的
    /// `version_mismatch` / `invalid_session`。于是使用者看到的是失败，
    /// 看不到**为什么**失败，也看不到"差什么就能跑"。
    ///
    /// 这个探针把那些理由变成可读事实：Kit 在不在、探针装没装、游戏版本是不是
    /// 客户端要求的那个、场景根目录在不在。它只读，不写、不改、不加载任何编辑器装配。
    ///
    /// 设计纪律（与 EngineProbe / PatchProbe / InventoryProbe 同一性质）
    /// ---------------------------------------------------------------
    /// · **纯 BCL + 纯读文件** —— 不解析任何 TaleWorlds 类型 ⇒ 未装 Kit 也能跑，
    ///   而"未装 Kit"恰恰是它最需要能跑的时候。
    /// · **路径全部可注入**（RootDir），便于离线断言，不必靠真机游戏目录。
    /// · **永不抛** —— 每一项独立 try，读不到就是 absent，而不是让整个探测失败。
    /// · **判定规则与 IO 分离**（参考 ProbePolicy 的先例：规则最该被测，
    ///   而 IO 依赖真实目录）。规则在 <see cref="Judge"/>，测试可脱离磁盘调用。
    /// </summary>
    internal static class CapabilityProbe
    {
        /// <summary>客户端（Archive 的编辑器 MCP）硬门禁的游戏版本。</summary>
        internal const string RequiredGameVersion = "v1.4.8";

        /// <summary>编辑器探针模块的目录名。</summary>
        internal const string EditorProbeModuleId = "BannerlordSage.EditorProbe";

        /// <summary>Modding Kit 的装配目录名。</summary>
        internal const string ModdingKitBinDir = "Win64_Shipping_wEditor";

        /// <summary>客户端运行时装配目录名。</summary>
        internal const string ClientBinDir = "Win64_Shipping_Client";

        /// <summary>Modding Kit 存在时必须能找到的装配（取自 EditorProbe.csproj 的引用表）。</summary>
        internal static readonly string[] ModdingKitAssemblies =
        {
            "TaleWorlds.MountAndBlade.dll",
            "TaleWorlds.Engine.dll",
            "TaleWorlds.Library.dll",
            "TaleWorlds.Core.dll",
            "TaleWorlds.DotNet.dll",
            "TaleWorlds.Localization.dll",
            "TaleWorlds.ScreenSystem.dll",
        };

        /// <summary>探测结果（纯数据，便于离线断言）。</summary>
        internal sealed class Facts
        {
            public string GameDir = "";
            public string GameVersion = "";              // 客户端 Version.xml 的 Singleplayer 值
            public string EditorVersion = "";            // Kit 侧 Version.xml 的值
            public bool ModdingKitDirPresent;            // bin/Win64_Shipping_wEditor 在不在
            public int ModdingKitAssembliesFound;        // 其中找到几个必需装配
            public bool EditorProbeModulePresent;        // Modules/<EditorProbe> 在不在
            public bool EditorProbeDllPresent;           // 模块的 DLL 在不在
            public bool ClientRuntimePresent;            // bin/Win64_Shipping_Client 在不在
        }

        /// <summary>判定结果：能不能跑编辑器，以及差什么。</summary>
        internal sealed class Verdict
        {
            public bool ModdingKit;
            public bool EditorProbe;
            public bool VersionMatch;                    // 客户端版本 == 门禁版本
            public bool KitVersionMatch;                 // Kit 版本 == 客户端版本（两边一致）
            public string Ready;                         // "yes" / "no"
            public string[] Blockers = new string[0];    // 逐条"差什么"
        }

        /// <summary>
        /// 纯判定：只吃 Facts，不碰磁盘 —— 这样离线就能把所有分支测完。
        /// </summary>
        internal static Verdict Judge(Facts f)
        {
            var blockers = new System.Collections.Generic.List<string>();

            bool kitAssembliesComplete =
                f.ModdingKitDirPresent && f.ModdingKitAssembliesFound >= ModdingKitAssemblies.Length;
            if (!f.ModdingKitDirPresent)
            {
                blockers.Add("Modding Kit 未安装：缺少 bin/" + ModdingKitBinDir +
                             "（Steam 里勾选 Bannerlord Modding Kit）");
            }
            else if (!kitAssembliesComplete)
            {
                blockers.Add("Modding Kit 装配不全：bin/" + ModdingKitBinDir + " 只找到 " +
                             f.ModdingKitAssembliesFound + "/" + ModdingKitAssemblies.Length +
                             " 个必需装配");
            }

            if (!f.EditorProbeModulePresent)
            {
                blockers.Add("编辑器探针模组未安装：Modules/" + EditorProbeModuleId + " 不存在");
            }
            else if (!f.EditorProbeDllPresent)
            {
                blockers.Add("编辑器探针模组缺 DLL：Modules/" + EditorProbeModuleId +
                             "/bin/" + ClientBinDir + "/" + EditorProbeModuleId + ".dll 不存在");
            }

            bool versionMatch = string.Equals(f.GameVersion, RequiredGameVersion,
                                              StringComparison.OrdinalIgnoreCase);
            if (!versionMatch)
            {
                blockers.Add("游戏版本不符：客户端是 " +
                             (string.IsNullOrEmpty(f.GameVersion) ? "(读不到)" : f.GameVersion) +
                             "，编辑器客户端硬门禁 " + RequiredGameVersion);
            }

            // Kit 与客户端版本不一致时，编辑器装配可能和运行时对不上 ——
            // 独立报出来，因为它的症状（进场景崩）和"没装 Kit"完全不同。
            bool kitVersionMatch = string.IsNullOrEmpty(f.EditorVersion)
                                   || string.Equals(f.EditorVersion, f.GameVersion,
                                                    StringComparison.OrdinalIgnoreCase);
            if (kitAssembliesComplete && !kitVersionMatch)
            {
                blockers.Add("Modding Kit 版本与客户端不一致：Kit=" + f.EditorVersion +
                             " 客户端=" + f.GameVersion +
                             " —— 编辑器装配可能与运行时对不上，进场景会崩");
            }

            return new Verdict
            {
                ModdingKit = kitAssembliesComplete,
                EditorProbe = f.EditorProbeModulePresent && f.EditorProbeDllPresent,
                VersionMatch = versionMatch,
                KitVersionMatch = kitVersionMatch,
                Ready = (blockers.Count == 0) ? "yes" : "no",
                Blockers = blockers.ToArray(),
            };
        }

        /// <summary>读磁盘得到 Facts。任何一项读不到都记为 absent，绝不抛。</summary>
        internal static Facts Collect(string gameDir)
        {
            var f = new Facts { GameDir = gameDir ?? "" };
            if (string.IsNullOrEmpty(gameDir)) return f;

            try
            {
                string client = Path.Combine(gameDir, "bin", ClientBinDir);
                f.ClientRuntimePresent = Directory.Exists(client);
                f.GameVersion = ReadSingleplayerVersion(Path.Combine(client, "Version.xml"));

                string kit = Path.Combine(gameDir, "bin", ModdingKitBinDir);
                f.ModdingKitDirPresent = Directory.Exists(kit);
                f.EditorVersion = ReadSingleplayerVersion(Path.Combine(kit, "Version.xml"));
                if (f.ModdingKitDirPresent)
                {
                    int n = 0;
                    foreach (string asm in ModdingKitAssemblies)
                    {
                        try { if (File.Exists(Path.Combine(kit, asm))) n++; }
                        catch { }
                    }
                    f.ModdingKitAssembliesFound = n;
                }

                string mod = Path.Combine(gameDir, "Modules", EditorProbeModuleId);
                f.EditorProbeModulePresent = Directory.Exists(mod);
                if (f.EditorProbeModulePresent)
                {
                    string dll = Path.Combine(mod, "bin", ClientBinDir, EditorProbeModuleId + ".dll");
                    f.EditorProbeDllPresent = File.Exists(dll);
                }
            }
            catch
            {
                // 探测工具绝不该因为探测失败而失败
            }
            return f;
        }

        /// <summary>
        /// 从 Version.xml 里取 Singleplayer 的值。用正则而不是 XML 解析：
        /// 这个文件是引擎写的、结构可能变，而这里只要一个属性值；
        /// 解析失败就返回空串，由判定层报"读不到"。
        /// </summary>
        internal static string ReadSingleplayerVersion(string versionXmlPath)
        {
            try
            {
                if (!File.Exists(versionXmlPath)) return "";
                string text = File.ReadAllText(versionXmlPath, Encoding.UTF8);
                var m = System.Text.RegularExpressions.Regex.Match(
                    text, @"\bSingleplayer\s+Value\s*=\s*[""']([^""']+)[""']",
                    System.Text.RegularExpressions.RegexOptions.IgnoreCase);
                return m.Success ? m.Groups[1].Value : "";
            }
            catch
            {
                return "";
            }
        }

        /// <summary>JSON 输出（手写，与 BridgeProtocol 的 Jw/Jmini 同一做法：零依赖）。</summary>
        internal static string Json(string gameDir)
        {
            Facts f = Collect(gameDir);
            Verdict v = Judge(f);
            StringBuilder sb = new StringBuilder();
            sb.Append("{");
            sb.Append("\"gameDir\":").Append(Quo(f.GameDir));
            sb.Append(",\"clientRuntimePresent\":").Append(Jw.B(f.ClientRuntimePresent));
            sb.Append(",\"gameVersion\":").Append(Str(f.GameVersion));
            sb.Append(",\"requiredGameVersion\":").Append(Str(RequiredGameVersion));
            sb.Append(",\"moddingKit\":{");
            sb.Append("\"present\":").Append(Jw.B(v.ModdingKit));
            sb.Append(",\"dirPresent\":").Append(Jw.B(f.ModdingKitDirPresent));
            sb.Append(",\"assembliesFound\":").Append(f.ModdingKitAssembliesFound);
            sb.Append(",\"assembliesRequired\":").Append(ModdingKitAssemblies.Length);
            sb.Append(",\"version\":").Append(Str(f.EditorVersion));
            sb.Append(",\"versionMatchesClient\":").Append(Jw.B(v.KitVersionMatch));
            sb.Append("}");
            sb.Append(",\"editorProbe\":{");
            sb.Append("\"installed\":").Append(Jw.B(v.EditorProbe));
            sb.Append(",\"moduleId\":").Append(Str(EditorProbeModuleId));
            sb.Append(",\"moduleExists\":").Append(Jw.B(f.EditorProbeModulePresent));
            sb.Append(",\"dllExists\":").Append(Jw.B(f.EditorProbeDllPresent));
            sb.Append("}");
            sb.Append(",\"versionMatch\":").Append(Jw.B(v.VersionMatch));
            sb.Append(",\"editorReady\":").Append(Str(v.Ready));
            sb.Append(",\"blockers\":[");
            for (int i = 0; i < v.Blockers.Length; i++)
            {
                if (i > 0) sb.Append(",");
                sb.Append(Quo(v.Blockers[i]));
            }
            sb.Append("]");
            sb.Append("}");
            return sb.ToString();
        }

        private static string Str(string s)
        {
            return Quo(s);
        }

        /// <summary>
        /// 加引号 + 转义。**必须用这个，不能用 Jw.Esc**。
        ///
        /// 实测踩到的坑（2026-10-14）：`Jw.Esc` 只转义、**不加引号**
        /// （加引号的是 `Protocol.Q`）。我最初照它写，产出的是
        ///   {"gameDir":G:\Program Files\...}
        /// —— **不是合法 JSON**，解析器直接失败。
        /// 而离线断言当时只调了 `Judge()`（判定逻辑），**没调 `Json()`**（输出），
        /// 所以这个问题在断言里完全看不见。教训：**要断言线上那条路径**（此处即 Json），
        /// 而不仅是它内部的一个纯函数。
        /// </summary>
        private static string Quo(string s)
        {
            return "\"" + Jw.Esc(s ?? "") + "\"";
        }
    }
}
