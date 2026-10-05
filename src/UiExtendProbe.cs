using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// UIExtenderEx（`Bannerlord.UIExtenderEx`）**只读**内省 —— 第三座桥。
    ///
    /// ## 为什么需要它
    ///
    /// UIExtenderEx 是**界面扩展层**：它让多个 mod 往**官方界面的部件**上插东西。
    /// 本机实测在跑（`v2.13.3`），且 `bl_patches` 实测它自己打了 **19 个** Harmony 补丁
    /// （owner `bannerlord.uiextender.ex`）。
    ///
    /// 对三合一项目的直接价值：
    ///   - 项目 **C4（浮点输入框）** 手工做了 `WidgetFactory._builtinTypes` / `WidgetInfo._widgetInfos`
    ///     的注册 —— 而 UIExtenderEx 的 `WidgetFactoryManager.Patch` / `WidgetPrefabPatch`
    ///     **做的正是同一件事** ⇒ 它的实现可以与本项目那份**互证**；
    ///   - 想知道"**哪个 mod 改了哪个官方界面**"时，它是唯一能直接回答的地方。
    ///
    /// ## 与「零依赖」原则一致（同 `PatchProbe` / `McmProbe`）
    ///
    /// **用反射**，不引用 UIExtenderEx 的程序集 ⇒ BlBridge 对它是**零引用**，
    /// 未装时返回 `available=false` 而不是加载失败。
    /// **只读**：不注册扩展、不改界面、不碰它的实例表。
    ///
    /// ## API 依据（反编译本机 `Bannerlord.UIExtenderEx.dll` v2.13.3，非推测）
    ///
    ///   `UIExtender`（:362）
    ///       public  static UIExtender? GetUIExtenderFor(string moduleName)
    ///       internal static IReadOnlyList&lt;UIExtenderRuntime&gt; GetAllRuntimes()   ← ★ 入口（internal ⇒ 反射）
    ///       private static readonly Dictionary&lt;string, UIExtender&gt; Instances
    ///   `UIExtenderRuntime`（:539，**internal class**）
    ///       public readonly string ModuleName;
    ///       public readonly PrefabComponent PrefabComponent;
    ///       public readonly ViewModelComponent ViewModelComponent;
    ///   `PrefabComponent`（:2692，**internal class**）
    ///       internal readonly ConcurrentDictionary&lt;string, List&lt;PrefabPatch&gt;&gt; MoviePatches;
    ///           // key = 界面(movie)名；value = 该界面上的补丁列表
    ///       public IEnumerable&lt;string&gt; GetMoviesToPatch()                        （:2872）
    ///       private readonly ConcurrentDictionary&lt;Type, bool&gt; _enabledPatches;
    ///   `PrefabPatch`（:2694）
    ///       internal sealed record PrefabPatch(Type Type, Action&lt;XmlDocument&gt; Patcher);
    ///   `ViewModelComponent`（:3514）
    ///       public readonly ConcurrentDictionary&lt;Type, List&lt;Type&gt;&gt; Mixins;
    ///
    /// ⚠️ **踩坑记录（前两座桥的教训，这里预先规避）**：
    ///   1. **类型全名不靠推断** —— 本探针列多个候选全名并遍历程序集
    ///      （`McmProbe` 那次从"赋值处所在类"推全名，结果报 `no_mcm` 而 MCM 在正常跑）；
    ///   2. **`ToString()` 不假设是人话** —— `PrefabPatch` 是 **record**，`ToString()` 会给
    ///      `PrefabPatch { Type = ..., Patcher = ... }` 这种长串 ⇒ 这里**只取 `Type` 字段**
    ///      （要的是"哪个扩展类改了这个界面"，不是整个 record 的打印）；
    ///   3. **"没有"与"是 0"要分开** —— 界面补丁数为 0 与"读不到"必须能区分，
    ///      所以这里显式给 `movieCount` / `patchTotal`，读不到就报错而不是报 0。
    /// </summary>
    internal static class UiExtendProbe
    {
        private static Type _uiExtenderType;
        private static MethodInfo _getAllRuntimes;
        private static bool _probed;

        private static void EnsureProbed()
        {
            if (_probed) return;
            _probed = true;
            try
            {
                // ⚠️ 类型全名**列候选**、遍历程序集 —— 不靠推断（见类注释的坑 1）。
                string[] candidates = {
                    "Bannerlord.UIExtenderEx.UIExtender",
                    "Bannerlord.UIExtenderEx.Attributes.UIExtender",
                };
                foreach (Assembly asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    foreach (string full in candidates)
                    {
                        Type t = null;
                        try { t = asm.GetType(full, false); }
                        catch { }
                        if (t == null) continue;
                        // `GetAllRuntimes` 是 internal static ⇒ NonPublic 取
                        MethodInfo m = t.GetMethod("GetAllRuntimes",
                            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
                        if (m == null) continue;
                        _uiExtenderType = t;
                        _getAllRuntimes = m;
                        return;
                    }
                }
            }
            catch { }
        }

        internal static bool Available
        {
            get { EnsureProbed(); return _uiExtenderType != null; }
        }

        private static object GetObj(object obj, string name)
        {
            if (obj == null) return null;
            try
            {
                Type t = obj.GetType();
                FieldInfo f = t.GetField(name,
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                if (f != null) return f.GetValue(obj);
                PropertyInfo p = t.GetProperty(name,
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                return p == null ? null : p.GetValue(obj, null);
            }
            catch { return null; }
        }

        private static int CountOf(object coll)
        {
            if (coll == null) return 0;
            ICollection c = coll as ICollection;
            if (c != null) return c.Count;
            IEnumerable e = coll as IEnumerable;
            if (e == null) return 0;
            int n = 0;
            foreach (object _ in e) n++;
            return n;
        }

        /// <summary>
        /// 主入口。参数（Jmini 扁平读取；**不得与信封保留键重名**）：
        ///   module    —— 只看某个模块的扩展（如 `RaiseYourBanner`）
        ///   moviesOnly—— true 时只列界面名，不列每个界面的补丁类（省 token）
        ///   limit     —— 每个 runtime 最多列几个界面，默认 200
        /// </summary>
        internal static string HandleGetUiExtensions(string id, string raw)
        {
            EnsureProbed();
            if (_uiExtenderType == null)
            {
                return Protocol.Failure(id, "no_uiextenderex",
                    "没找到 UIExtenderEx（Bannerlord.UIExtenderEx.UIExtender 不在已加载程序集里）—— "
                    + "可能没装 Bannerlord.UIExtenderEx", false);
            }

            try
            {
                // ⚠️ **参数名不能叫 `module`?** —— `module` **不在**信封保留键
                //    （`protocolVersion`/`id`/`method`/`parameters`/`issuedUtc`）里，安全。
                string moduleFilter = Jmini.Str(raw, "module", null);
                bool moviesOnly = Jmini.Bool(raw, "moviesOnly", false);
                int limit = Jmini.Int(raw, "limit", 200);
                if (limit <= 0) limit = 200;

                object runtimesObj = _getAllRuntimes.Invoke(null, null);
                IEnumerable runtimes = runtimesObj as IEnumerable;
                if (runtimes == null)
                {
                    return Protocol.Failure(id, "no_runtimes",
                        "GetAllRuntimes() 返回 null/不可枚举（UIExtenderEx 版本变了？）", false);
                }

                List<string> items = new List<string>();
                int runtimeTotal = 0;
                int runtimeMatched = 0;
                int movieTotal = 0;
                int patchTotal = 0;
                int mixinTotal = 0;

                foreach (object rt in runtimes)
                {
                    if (rt == null) continue;
                    runtimeTotal++;
                    string modName = GetObj(rt, "ModuleName") as string;
                    if (!string.IsNullOrEmpty(moduleFilter)
                        && !string.Equals(modName, moduleFilter, StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }
                    runtimeMatched++;

                    // ── PrefabComponent.MoviePatches：界面 → 补丁列表 ──
                    object prefab = GetObj(rt, "PrefabComponent");
                    object moviePatches = GetObj(prefab, "MoviePatches");
                    List<string> movies = new List<string>();
                    int thisPatchCount = 0;
                    IDictionary dict = moviePatches as IDictionary;
                    if (dict != null)
                    {
                        int shown = 0;
                        foreach (DictionaryEntry de in dict)
                        {
                            string movie = de.Key as string;
                            int n = CountOf(de.Value);
                            thisPatchCount += n;
                            movieTotal++;
                            if (shown >= limit) continue;
                            shown++;
                            if (moviesOnly)
                            {
                                movies.Add(Protocol.Q(movie ?? ""));
                            }
                            else
                            {
                                // ★ 每个界面上的补丁类名（要的是"谁改了这个界面"）
                                StringBuilder mb = new StringBuilder();
                                mb.Append("{\"movie\":").Append(Protocol.Q(movie ?? ""));
                                mb.Append(",\"patchCount\":").Append(n);
                                mb.Append(",\"patchers\":[");
                                IEnumerable plist = de.Value as IEnumerable;
                                bool first = true;
                                int pn = 0;
                                if (plist != null)
                                {
                                    foreach (object p in plist)
                                    {
                                        if (p == null) continue;
                                        if (pn >= 20) { mb.Append(",\"...\""); break; }
                                        pn++;
                                        if (!first) mb.Append(',');
                                        first = false;
                                        // ⚠️ `PrefabPatch` 是 record ⇒ **不要 ToString()**（见类注释坑 2），
                                        //    只取它的 `Type` 字段（= 那个扩展类的类型）。
                                        object pt = GetObj(p, "Type");
                                        mb.Append(Protocol.Q(pt == null ? "" : pt.ToString()));
                                    }
                                }
                                mb.Append(']');
                                mb.Append('}');
                                movies.Add(mb.ToString());
                            }
                        }
                    }
                    patchTotal += thisPatchCount;

                    // ── ViewModelComponent.Mixins：ViewModel 类型 → mixin 类型 ──
                    object vmComp = GetObj(rt, "ViewModelComponent");
                    object mixins = GetObj(vmComp, "Mixins");
                    int thisMixins = CountOf(mixins);
                    mixinTotal += thisMixins;

                    StringBuilder b = new StringBuilder();
                    b.Append("{\"module\":").Append(Protocol.Q(modName ?? ""));
                    b.Append(",\"movieCount\":").Append(CountOf(moviePatches));
                    b.Append(",\"patchTotal\":").Append(thisPatchCount);
                    b.Append(",\"mixinCount\":").Append(thisMixins);
                    if (!moviesOnly)
                    {
                        b.Append(",\"movies\":[").Append(string.Join(",", movies.ToArray())).Append(']');
                    }
                    else if (movies.Count > 0)
                    {
                        b.Append(",\"movies\":[").Append(string.Join(",", movies.ToArray())).Append(']');
                    }
                    b.Append('}');
                    items.Add(b.ToString());
                }

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true,\"available\":true");
                sb.Append(",\"assembly\":").Append(Protocol.Q(_uiExtenderType.Assembly.GetName().Name ?? ""));
                sb.Append(",\"version\":").Append(Protocol.Q(
                    _uiExtenderType.Assembly.GetName().Version == null
                        ? "" : _uiExtenderType.Assembly.GetName().Version.ToString()));
                sb.Append(",\"runtimeCount\":").Append(runtimeTotal);
                sb.Append(",\"matchedCount\":").Append(runtimeMatched);
                sb.Append(",\"movieTotal\":").Append(movieTotal);
                sb.Append(",\"patchTotal\":").Append(patchTotal);
                sb.Append(",\"mixinTotal\":").Append(mixinTotal);
                if (!string.IsNullOrEmpty(moduleFilter) && runtimeMatched == 0)
                {
                    sb.Append(",\"warning\":").Append(Protocol.Q(
                        "没有任何 runtime 的 ModuleName 匹配 '" + moduleFilter + "'；"
                        + "去掉 module 参数可列出全部"));
                }
                // ★ 反静默：有 runtime 却一个界面都没有 ⇒ 几乎一定是读法问题（不是"没人扩展界面"）
                if (runtimeTotal > 0 && movieTotal == 0
                    && string.IsNullOrEmpty(moduleFilter))
                {
                    sb.Append(",\"warning\":").Append(Protocol.Q(
                        "有 " + runtimeTotal + " 个 runtime 但读不到任何界面 —— "
                        + "这几乎一定是本探针的读法问题（MoviePatches 的反射取法），而不是『没人扩展界面』"));
                }
                sb.Append(",\"runtimes\":[").Append(string.Join(",", items.ToArray())).Append(']');
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                Exception e = ex;
                if (ex is TargetInvocationException && ex.InnerException != null) e = ex.InnerException;
                return Protocol.Failure(id, "get_ui_extensions_failed",
                    e.GetType().Name + ": " + e.Message, false);
            }
        }
    }
}
