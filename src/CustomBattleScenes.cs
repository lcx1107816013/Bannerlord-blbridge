using System;
using System.Collections.Generic;
using System.IO;
using System.Xml;

namespace BlBridge
{
    /// <summary>官方自定义战斗场景表里的一行。</summary>
    internal sealed class SceneRow
    {
        internal string Id = "";
        internal string Name = "";
        internal string Terrain = "";
        internal bool IsSiege;
        internal bool IsVillage;
        internal bool IsNaval;
        internal bool IsNavalRaid;
        internal bool IsLordsHall;
        internal string SceneLevel = "";
        internal string ForestDensity = "";

        /// <summary>这一行是从哪个文件读来的（诊断用：一眼看出是官方还是某个 mod 加的场景）。</summary>
        internal string Source = "";

        /// <summary>场景目录真的存在（`Modules/*/SceneObj/&lt;id&gt;/scene.xscene`）。
        /// 见 ScenarioRunner.SceneExists 的说明：不存在的名字会让引擎在 native 层崩（0xC0000005）。
        /// **注意**：本字段由调用方按需填（只对真正要吐出去的行算），解析阶段一律 false。</summary>
        internal bool Exists;

        /// <summary>官方界面上的「游戏类型」这一行。**不是硬编码常量**，而是从这些 flag 推导的：
        /// 官方 `CustomBattleHelper` 只定义了 Battle/Siege/Village 三个常量，海上的两种
        /// （海战 / 海上掠夺）完全由 NavalDLC 的场景表 flag 带出来 —— 也就是说
        /// "多加一种模式" = "加一批带 flag 的场景"。</summary>
        internal string Mode
        {
            get
            {
                if (IsNavalRaid) return "navalRaid";
                if (IsNaval) return "naval";
                if (IsSiege) return "siege";
                if (IsVillage) return "village";
                if (IsLordsHall) return "lordsHall";
                return "battle";
            }
        }
    }

    /// <summary>
    /// 读官方自定义战斗的**场景表** `CustomBattleScenes`。
    ///
    /// ⚠️ v0.8.14 的实现变更（重要，别再改回去）：
    ///   旧实现用引擎的 `MBObjectManager.GetMergedXmlForManaged("CustomBattleScenes", …)`
    ///   —— 那确实是"引擎自己用的同一份表"（`CustomGame.cs:116`），但它是**引擎的**加载通道：
    ///   我们从一个主线程泵里调它，就可能插进"引擎正在切换状态 / 加载 CustomGame 数据"的窗口里。
    ///   2026-09-25 那次托管崩溃（`0xE0434352`，崩在 `loading managed_core_parameters.xml` 之后）
    ///   之后的复盘结论是：**明知道有窗口风险，就不该继续用带副作用/依赖全局状态的通道去拿只读数据**。
    ///   所以现在改为：
    ///     1) 扫每个**已激活模块**的 `SubModule.xml`，找 `&lt;XmlName id="CustomBattleScenes" path="…"/&gt;`；
    ///     2) 直接 XML 解析那些 `ModuleData/&lt;path&gt;.xml`（纯文件读，不碰 ObjectManager）；
    ///     3) 结果**缓存**（TTL 见 CacheTtlSeconds），合并语义与引擎一致：**同 id 后者覆盖前者**。
    ///   代价：如果某个 mod 在运行期动态注册这种表（不是靠 SubModule.xml 声明），我们看不到它 ——
    ///   官方与常见 mod 都是静态声明，这个代价可以接受（且会体现在 `files` 计数上，可核对）。
    ///
    /// 官方两张表（一手证据）：`custom_battle_scenes.xml`（CustomBattle，314 条）+
    /// `naval_custom_battle_scenes.xml`（NavalDLC，37 条，含 `is_naval_map` / `is_naval_raid_map`）。
    /// </summary>
    internal static class CustomBattleScenes
    {
        private const string TableId = "CustomBattleScenes";

        /// <summary>缓存有效期（秒）。表是静态数据，缓存只为省掉重复的磁盘 IO。</summary>
        private const double CacheTtlSeconds = 120.0;

        private static readonly object CacheGate = new object();
        private static List<SceneRow> _cache;
        private static DateTime _cacheUtc = DateTime.MinValue;
        private static string _cacheError = "";
        private static int _cacheFileCount;
        private static List<string> _cacheFiles = new List<string>();

        /// <summary>
        /// 读全表（带缓存）。
        /// `fromCache` 标明这次是不是直接给了缓存；`files` 是实际解析到的文件清单（诊断用）。
        /// 解析失败**不抛异常**：错误写进 `error`，返回已解析到的部分 —— 调用方（list_ui）
        /// 宁可吐半张表也不要因为某个 mod 的 XML 坏了就整个失败。
        /// </summary>
        internal static List<SceneRow> ReadAll(out string error, out bool fromCache, out List<string> files)
        {
            lock (CacheGate)
            {
                DateTime now = DateTime.UtcNow;
                if (_cache != null && (now - _cacheUtc).TotalSeconds < CacheTtlSeconds)
                {
                    error = _cacheError;
                    fromCache = true;
                    files = _cacheFiles;
                    return _cache;
                }

                List<string> scanned;
                string scanError;
                List<SceneRow> rows = Scan(out scanned, out scanError);
                _cache = rows;
                _cacheUtc = now;
                _cacheError = scanError;
                _cacheFileCount = scanned.Count;
                _cacheFiles = scanned;

                error = scanError;
                fromCache = false;
                files = scanned;
                return rows;
            }
        }

        internal static int CachedFileCount
        {
            get { lock (CacheGate) { return _cacheFileCount; } }
        }

        /// <summary>扫描 + 合并（无缓存；`ReadAll` 负责缓存）。</summary>
        private static List<SceneRow> Scan(out List<string> files, out string error)
        {
            error = "";
            files = new List<string>();
            Dictionary<string, SceneRow> byId = new Dictionary<string, SceneRow>(StringComparer.Ordinal);
            List<string> order = new List<string>();

            try
            {
                foreach (string folder in ScenarioRunner.ActiveModuleFolders())
                {
                    List<string> declared = DeclaredSceneTablePaths(folder);
                    for (int i = 0; i < declared.Count; i++)
                    {
                        string full = Path.Combine(folder, "ModuleData", declared[i] + ".xml");
                        if (!File.Exists(full)) continue;   // 声明了但文件不在：跳过，不报死
                        files.Add(full);
                        MergeFile(full, byId, order);
                    }
                }
            }
            catch (Exception ex)
            {
                error = ex.GetType().Name + ": " + ex.Message;
            }

            List<SceneRow> rows = new List<SceneRow>(order.Count);
            for (int i = 0; i < order.Count; i++) rows.Add(byId[order[i]]);
            if (rows.Count == 0 && error.Length == 0)
            {
                error = "没有任何已激活模块声明 " + TableId + "（或声明了但 ModuleData 文件不存在）";
            }
            return rows;
        }

        /// <summary>
        /// 从模块的 `SubModule.xml` 里取 `<XmlName id="CustomBattleScenes" path="…"/>` 的 path 列表。
        /// 用 `local-name()` 匹配，避免命名空间差异（官方与社区模块的 SubModule.xml 写法并不统一）。
        /// </summary>
        private static List<string> DeclaredSceneTablePaths(string moduleFolder)
        {
            List<string> paths = new List<string>();
            string subModule = Path.Combine(moduleFolder, "SubModule.xml");
            if (!File.Exists(subModule)) return paths;
            try
            {
                XmlDocument doc = new XmlDocument();
                doc.Load(subModule);
                XmlNodeList nodes = doc.SelectNodes("//*[local-name()='XmlName']");
                if (nodes == null) return paths;
                foreach (XmlNode node in nodes)
                {
                    if (node.Attributes == null) continue;
                    XmlAttribute id = node.Attributes["id"];
                    XmlAttribute path = node.Attributes["path"];
                    if (id == null || path == null) continue;
                    if (!string.Equals(id.Value, TableId, StringComparison.OrdinalIgnoreCase)) continue;
                    if (string.IsNullOrEmpty(path.Value)) continue;
                    paths.Add(path.Value);
                }
            }
            catch
            {
                // 单个模块的清单坏了不影响别的模块：跳过它（整体错误在 Scan 里汇总）
            }
            return paths;
        }

        /// <summary>解析一个场景表文件并并入结果集（同 id 后者覆盖，与引擎合并语义一致）。</summary>
        private static void MergeFile(string file, Dictionary<string, SceneRow> byId, List<string> order)
        {
            XmlDocument doc = new XmlDocument();
            doc.Load(file);
            XmlNode root = null;
            foreach (XmlNode child in doc.ChildNodes)
            {
                if (child.NodeType == XmlNodeType.Element) { root = child; break; }
            }
            if (root == null) return;
            if (!root.Name.Equals(TableId, StringComparison.OrdinalIgnoreCase)) return;   // 根节点不对：不是这张表

            string source = Path.GetFileName(file);
            foreach (XmlNode node in root.ChildNodes)
            {
                if (node.NodeType == XmlNodeType.Comment) continue;
                if (!node.Name.Equals("Scene", StringComparison.OrdinalIgnoreCase)) continue;
                if (node.Attributes == null) continue;

                string id = Attr(node, "id");
                if (id.Length == 0) continue;

                SceneRow row = new SceneRow();
                row.Id = id;
                row.Name = Attr(node, "name");
                row.Terrain = Attr(node, "terrain");
                row.IsSiege = Flag(node, "is_siege_map");
                row.IsVillage = Flag(node, "is_village_map");
                row.IsNaval = Flag(node, "is_naval_map");
                row.IsNavalRaid = Flag(node, "is_naval_raid_map");
                row.IsLordsHall = Flag(node, "is_lords_hall_map");
                row.SceneLevel = Attr(node, "forced_scene_level");
                row.ForestDensity = Attr(node, "forest_density");
                row.Source = source;

                if (!byId.ContainsKey(id)) order.Add(id);   // 覆盖时保留原位置，与"同 id 覆盖"语义一致
                byId[id] = row;
            }
        }

        /// <summary>`mode` 过滤器（list_ui 的 `scenesMode` 参数）：all / battle / siege / village / lordsHall / naval / navalRaid。</summary>
        internal static bool Matches(SceneRow row, string modeFilter)
        {
            if (string.IsNullOrEmpty(modeFilter) || modeFilter == "all") return true;
            return string.Equals(row.Mode, modeFilter, StringComparison.OrdinalIgnoreCase);
        }

        internal static bool IsKnownModeFilter(string modeFilter)
        {
            if (string.IsNullOrEmpty(modeFilter)) return true;
            switch (modeFilter)
            {
                case "all":
                case "battle":
                case "siege":
                case "village":
                case "lordsHall":
                case "naval":
                case "navalRaid":
                    return true;
                default:
                    return false;
            }
        }

        private static string Attr(XmlNode node, string name)
        {
            try
            {
                XmlAttribute a = node.Attributes[name];
                return a == null ? "" : a.Value;
            }
            catch
            {
                return "";
            }
        }

        /// <summary>官方表里这些 flag 一律写作 `="true"`（实测）；缺失即 false。
        /// 不按"存在即真"判 —— 免得将来出现 `is_siege_map="false"` 时行为翻转。</summary>
        private static bool Flag(XmlNode node, string name)
        {
            return string.Equals(Attr(node, name), "true", StringComparison.OrdinalIgnoreCase);
        }
    }
}
