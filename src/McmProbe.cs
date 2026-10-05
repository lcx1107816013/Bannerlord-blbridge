using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// MCM（Mod Configuration Menu / `Bannerlord.MBOptionScreen`）**只读**内省。
    ///
    /// ## 为什么需要它
    ///
    /// MCM 是**四前置级**的组件 —— 本机实测它在跑（`v5.12.3`），
    /// 且 Harmony 补丁表里 MCM 自己就打了 **22 个**补丁（`bl_patches` 实测）。
    /// **面板型 mod 的绝大多数"可调参数"最终都落成 MCM 设置项** ⇒
    /// 读得到 MCM 设置表，就等于拿到**全场 mod 的参数面**。
    ///
    /// 对三合一项目的直接价值：C8 要手搓 **172 个表格型控件**的菜单。
    /// 先看清"MCM 里已经有什么、长什么样"，才能判断**该复用还是该自建**。
    ///
    /// ## 与「零 MCM 依赖」的关系
    ///
    /// 与 <see cref="PatchProbe"/> 同款：**用反射**，不引用 MCM 的程序集
    /// ⇒ `BlBridge.dll` 对 MCM **零依赖**，MCM 未装时返回 `available=false`
    /// 而不是加载失败。而且**只读**：不注册设置、不写值、不碰它的 DI 容器。
    ///
    /// ## API 依据（反编译本机 `MCMv5.dll` v5.12.3，非推测）
    ///
    ///   `BaseSettingsProvider`（**命名空间 `MCM.Abstractions`**，:6881）
    ///       public static BaseSettingsProvider? Instance { get; internal set; }
    ///       public abstract IEnumerable&lt;SettingsDefinition&gt; SettingsDefinitions { get; }
    ///       public abstract BaseSettings? GetSettings(string id);
    ///   `SettingsDefinition`
    ///       string SettingsId / string DisplayName / List&lt;SettingsPropertyGroupDefinition&gt; SettingPropertyGroups
    ///   `SettingsPropertyDefinition`（:6471）
    ///       Id / PropertyReference(IRef) / SettingType / DisplayName / Order / RequireRestart
    ///       HintText / MaxValue / MinValue / EditableMinValue / EditableMaxValue
    ///       SelectedIndex / ValueFormat / GroupName / IsToggle / GroupOrder / Content
    ///   `IRef`（:994）
    ///       Type Type { get; }  object? Value { get; set; }
    ///       （`PropertyRef.Value` 直接 `PropertyInfo.GetValue(Instance)`）
    ///
    /// ⚠️ **`Instance` 的 setter 是 `internal`** ⇒ 必须**反射取属性**（`GetProperty` + `GetValue`），
    ///    不能直接编译期访问。这也是为什么本探针必须走反射。
    /// ⚠️ **类型全名不要靠推断**：`BaseSettingsProvider` 在 **`MCM.Abstractions`**；
    ///    我一度从 `MCMSubModule`（在 `MCM.Implementation`）的赋值语句**误推**到
    ///    `MCM.Implementation.BaseSettingsProvider` ⇒ 报 `no_mcm`（而 MCM 在正常跑）。
    ///    ⇒ 现在列多个候选全名并遍历程序集。
    /// </summary>
    internal static class McmProbe
    {
        private static Type _providerType;      // MCM.Implementation.BaseSettingsProvider
        private static PropertyInfo _instanceProp;
        private static bool _probed;

        private static void EnsureProbed()
        {
            if (_probed) return;
            _probed = true;
            try
            {
                // ⚠️ **类型全名踩过一次**：`BaseSettingsProvider` 在 **`MCM.Abstractions`**，
                //    不在 `MCM.Implementation` —— 我原先是从"`Instance` 被赋值的地方"
                //    （`MCMSubModule`，属于 `MCM.Implementation`）**误推**出来的。
                //    ⇒ 症状是 `no_mcm`（"好像没装 MCM"），而 MCM 其实在正常跑。
                //    修法：**列多个候选全名**（按版本可能漂移），并遍历所有已加载程序集。
                string[] candidates = {
                    "MCM.Abstractions.BaseSettingsProvider",
                    "MCM.Implementation.BaseSettingsProvider",
                    "MCM.BaseSettingsProvider",
                };
                foreach (Assembly asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    foreach (string full in candidates)
                    {
                        Type t = null;
                        try { t = asm.GetType(full, false); }
                        catch { }
                        if (t == null) continue;
                        // ⚠️ setter 是 internal ⇒ 用 NonPublic 把属性拿到手；
                        //    getter 是 public，GetValue 正常。
                        PropertyInfo pi = t.GetProperty("Instance",
                            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
                        if (pi == null) continue;
                        _providerType = t;
                        _instanceProp = pi;
                        return;
                    }
                }
            }
            catch { }
        }

        internal static bool Available
        {
            get
            {
                EnsureProbed();
                return _providerType != null && ProviderInstance() != null;
            }
        }

        /// <summary>取 `BaseSettingsProvider.Instance`（可能为 null：还没初始化）。</summary>
        private static object ProviderInstance()
        {
            if (_instanceProp == null) return null;
            try { return _instanceProp.GetValue(null, null); }
            catch { return null; }
        }

        private static string GetStr(object obj, string prop)
        {
            try
            {
                PropertyInfo p = obj.GetType().GetProperty(prop,
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                if (p == null) return null;
                object v = p.GetValue(obj, null);
                return v == null ? null : v.ToString();
            }
            catch { return null; }
        }

        private static object GetObj(object obj, string prop)
        {
            try
            {
                PropertyInfo p = obj.GetType().GetProperty(prop,
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                return p == null ? null : p.GetValue(obj, null);
            }
            catch { return null; }
        }

        private static bool GetBool(object obj, string prop, bool fallback)
        {
            object v = GetObj(obj, prop);
            return (v is bool b) ? b : fallback;
        }

        /// <summary>把值渲染成 JSON 片段（字符串带引号；数字/布尔裸写；其余 ToString）。</summary>
        private static string RenderValue(object v)
        {
            if (v == null) return "null";
            Type t = v.GetType();
            if (v is string s) return Protocol.Q(s);
            if (v is bool b) return b ? "true" : "false";
            if (v is int || v is long || v is short || v is byte) return v.ToString();
            if (v is float f) return f.ToString(System.Globalization.CultureInfo.InvariantCulture);
            if (v is double d) return d.ToString(System.Globalization.CultureInfo.InvariantCulture);
            if (v is decimal m) return m.ToString(System.Globalization.CultureInfo.InvariantCulture);
            if (t.IsEnum) return Protocol.Q(v.ToString());

            // ★ MCM 的 Dropdown 类**不是**原始值，而是 `Dropdown<T> : List<T>`
            //   ⇒ `ToString()` 得到的是**类型名**（`MCM.Common.Dropdown`1[System.String]`），
            //     完全不是用户看到的选中项。踩过一次。
            //   ⇒ 取它的 `SelectedValue`（反编译确认：`T SelectedValue => base[SelectedIndex]`）。
            //   判据用"名字里有 Dropdown"而不是硬编码类型，避免跨版本/泛型实例化差异。
            if (t.Name.StartsWith("Dropdown", StringComparison.Ordinal))
            {
                object sv = GetObj(v, "SelectedValue");
                if (sv != null) return RenderValue(sv);
            }

            // 其他集合/复杂对象：保守处理成字符串，不让它炸
            try { return Protocol.Q(v.ToString()); }
            catch { return "null"; }
        }

        /// <summary>
        /// 主入口。参数（Jmini 扁平读取）：
        ///   settingsId —— 只看某个设置块（如 `ButterLib` / `CharacterReload`）；不传则列全部块
        ///   summary    —— true 时只给每块的项数，不展开每一项（省 token）
        ///   limit      —— 每块最多几项，默认 200
        ///   withValues —— 是否读当前值（默认 true；false 只给元数据）
        ///
        /// ⚠️ 参数名**不得**与信封保留键重名（`protocolVersion`/`id`/`method`/`parameters`/`issuedUtc`）
        /// —— 这是本项目踩过两次的坑，见 AGENTS.md 与 `bl_patches_selftest.py` 的保留键不变式。
        /// </summary>
        internal static string HandleGetMcmSettings(string id, string raw)
        {
            EnsureProbed();
            if (_providerType == null)
            {
                return Protocol.Failure(id, "no_mcm",
                    "没找到 MCM（MCM.Implementation.BaseSettingsProvider 不在已加载程序集里）—— "
                    + "可能没装 Bannerlord.MBOptionScreen", false);
            }
            object provider = ProviderInstance();
            if (provider == null)
            {
                return Protocol.Failure(id, "mcm_not_ready",
                    "MCM 已加载但 BaseSettingsProvider.Instance 还是 null —— "
                    + "多半是模块加载/服务注册还没跑完（它在 OnServiceRegistration 之后才有值）", false);
            }

            try
            {
                string wantId = Jmini.Str(raw, "settingsId", null);
                bool summary = Jmini.Bool(raw, "summary", false);
                bool withValues = Jmini.Bool(raw, "withValues", true);
                int limit = Jmini.Int(raw, "limit", 200);
                if (limit <= 0) limit = 200;

                object defsObj = GetObj(provider, "SettingsDefinitions");
                IEnumerable defs = defsObj as IEnumerable;
                if (defs == null)
                {
                    return Protocol.Failure(id, "no_definitions",
                        "SettingsDefinitions 取不到或不是可枚举（MCM 版本变了？）", false);
                }

                List<string> blocks = new List<string>();
                int blockCount = 0;
                int propTotal = 0;
                int matchedId = 0;

                foreach (object def in defs)
                {
                    if (def == null) continue;
                    string sid = GetStr(def, "SettingsId");
                    string dname = GetStr(def, "DisplayName");
                    if (!string.IsNullOrEmpty(wantId)
                        && !string.Equals(sid, wantId, StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }
                    matchedId++;
                    blockCount++;

                    // 设置项：SettingPropertyGroups → 每组的 Properties
                    object groups = GetObj(def, "SettingPropertyGroups");
                    List<object> props = new List<object>();
                    IEnumerable gl = groups as IEnumerable;
                    if (gl != null)
                    {
                        foreach (object g in gl)
                        {
                            if (g == null) continue;
                            object pl = GetObj(g, "SettingProperties");
                            IEnumerable pls = pl as IEnumerable;
                            if (pls == null) continue;
                            foreach (object p in pls)
                            {
                                if (p != null) props.Add(p);
                            }
                        }
                    }
                    propTotal += props.Count;

                    StringBuilder b = new StringBuilder();
                    b.Append("{\"settingsId\":").Append(Protocol.Q(sid ?? ""));
                    b.Append(",\"displayName\":").Append(Protocol.Q(dname ?? ""));
                    b.Append(",\"groups\":").Append(CountOf(groups));
                    b.Append(",\"propertyCount\":").Append(props.Count);

                    if (!summary)
                    {
                        b.Append(",\"properties\":[");
                        int n = 0;
                        bool first = true;
                        foreach (object p in props)
                        {
                            if (n >= limit) break;
                            n++;
                            if (!first) b.Append(',');
                            first = false;
                            AppendProperty(b, p, withValues);
                        }
                        b.Append(']');
                        if (props.Count > limit) b.Append(",\"truncated\":true");
                    }
                    b.Append('}');
                    blocks.Add(b.ToString());
                }

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true,\"available\":true");
                sb.Append(",\"mcm\":").Append(Protocol.Q(_providerType.Assembly.GetName().Name ?? ""));
                sb.Append(",\"mcmVersion\":").Append(Protocol.Q(
                    _providerType.Assembly.GetName().Version == null
                        ? "" : _providerType.Assembly.GetName().Version.ToString()));
                sb.Append(",\"blockCount\":").Append(blockCount);
                sb.Append(",\"propertyTotal\":").Append(propTotal);
                if (!string.IsNullOrEmpty(wantId))
                {
                    sb.Append(",\"filter\":").Append(Protocol.Q(wantId));
                    if (matchedId == 0)
                    {
                        sb.Append(",\"warning\":").Append(Protocol.Q(
                            "没有任何设置块的 settingsId 匹配 '" + wantId + "'；"
                            + "去掉 settingsId 参数可列出全部块"));
                    }
                }
                sb.Append(",\"blocks\":[").Append(string.Join(",", blocks.ToArray())).Append(']');
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                Exception e = ex;
                if (ex is TargetInvocationException && ex.InnerException != null) e = ex.InnerException;
                return Protocol.Failure(id, "get_mcm_settings_failed",
                    e.GetType().Name + ": " + e.Message, false);
            }
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

        /// <summary>把一个 `SettingsPropertyDefinition` 摊成 JSON。</summary>
        private static void AppendProperty(StringBuilder b, object p, bool withValues)
        {
            b.Append('{');
            b.Append("\"id\":").Append(Protocol.Q(GetStr(p, "Id") ?? ""));
            b.Append(",\"name\":").Append(Protocol.Q(GetStr(p, "DisplayName") ?? ""));
            b.Append(",\"type\":").Append(Protocol.Q(GetStr(p, "SettingType") ?? ""));
            b.Append(",\"group\":").Append(Protocol.Q(GetStr(p, "GroupName") ?? ""));

            // 值域：MCM 用 decimal；⚠️ **只在真有值域时才输出**。
            //   踩过一次：Bool / Dropdown 的 MinValue/MaxValue 都是 0 ⇒ 输出 `"min":0,"max":0`
            //   纯属噪音，还会让读者误以为"这个开关的取值范围是 0..0"。
            //   判据：min < max 才算有值域（Bool/Dropdown 恒相等）。
            string stype = GetStr(p, "SettingType") ?? "";
            bool ranged = stype == "Integer" || stype == "Float" || stype == "FloatingInteger";
            if (ranged)
            {
                object mn = GetObj(p, "MinValue");
                object mx = GetObj(p, "MaxValue");
                if (mn != null) b.Append(",\"min\":").Append(RenderValue(mn));
                if (mx != null) b.Append(",\"max\":").Append(RenderValue(mx));
            }

            if (GetBool(p, "RequireRestart", false)) b.Append(",\"requireRestart\":true");
            if (GetBool(p, "IsToggle", false)) b.Append(",\"isToggle\":true");

            object sel = GetObj(p, "SelectedIndex");
            if (sel != null)
            {
                try
                {
                    int si = Convert.ToInt32(sel);
                    if (si >= 0) b.Append(",\"selectedIndex\":").Append(si);
                }
                catch { }
            }

            string hint = GetStr(p, "HintText");
            if (!string.IsNullOrEmpty(hint)) b.Append(",\"hint\":").Append(Protocol.Q(hint));

            if (withValues)
            {
                // ★ 当前值：走 IRef.Value（`PropertyRef.Value` = PropertyInfo.GetValue(Instance)）
                object iref = GetObj(p, "PropertyReference");
                if (iref != null)
                {
                    b.Append(",\"valueType\":").Append(Protocol.Q(
                        GetObj(iref, "Type") == null ? "" : GetObj(iref, "Type").ToString()));
                    object v = null;
                    bool ok = true;
                    try { v = GetObj(iref, "Value"); }
                    catch { ok = false; }
                    if (ok) b.Append(",\"value\":").Append(RenderValue(v));
                    else b.Append(",\"valueReadError\":true");
                }
            }
            b.Append('}');
        }
    }
}
