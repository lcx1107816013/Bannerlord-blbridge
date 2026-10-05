using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Text;
using TaleWorlds.Engine.GauntletUI;
using TaleWorlds.GauntletUI.BaseTypes;
using TaleWorlds.ScreenSystem;

namespace BlBridge
{
    /// <summary>
    /// 只读 UI 探测（L2 短名单 #1，脱壳抄 BUTR/Bannerlord.GABS 的 `Tools/GauntletUITools.cs` 的
    /// `ui/get_screen` 与 `ui/get_viewmodel_property`）。
    ///
    /// 为什么值得抄（见 docs/l2-api-drift-1.4.8.md §2，CASE P17/P18/P19/P20/P21）：
    ///   - `ScreenManager.TopScreen` / `ScreenBase.Layers` / `GauntletLayer` / `Widget.Children` 在 1.4.8 上都可编过；
    ///   - `_movieIdentifiers` 字段（上游 v1.3.13+ 分支用）在 1.4.8 上**存在**；
    ///   - 这俩工具**只读**、零副作用，是"让 AI 读官方界面状态"最高杠杆的入口。
    ///
    /// 脱壳抄纪律（见 docs/lessons-from-bannerlord-gabs.md §一）：只取**实现体**，外壳换成我们自己的
    /// `CommandPump` method + `Jmini`/`Protocol`。上游的 `[Tool]`/`[ToolParameter]` 是 Lib.GAB 源生成器外壳，
    /// 不引入（我们有 `tools/l2probe/` 证明：直接拖它的 `Tools/*.cs` 会把 Lib.GAB + 源生成器一并拖进来）。
    ///
    /// 版本分叉：上游 `GetMovies` 有 `#if v1313 || v1315` 两条。本机 1.4.8 走反射 `_movieIdentifiers`
    /// 那条（已验证存在）；若将来游戏升级到该字段改名/移除，这里是唯一的断点（见 README 的"重写判据"）。
    ///
    /// 来源（MIT 署名，抄录记账见 docs/gabp-naming.md §五）：
    ///   BUTR/Bannerlord.GABS @ master，Tools/GauntletUITools.cs，2026-09-27 取用。
    /// </summary>
    internal static class UiInspector
    {
        // 反光字段缓存（同上游 MovieIdentifiersField 的静态缓存思路，但用 BCL 反射而非 AccessTools2）。
        private static readonly FieldInfo MovieIdentifiersField =
            typeof(GauntletLayer).GetField("_movieIdentifiers",
                BindingFlags.NonPublic | BindingFlags.Instance);

        // 地图装饰层：噪声大、按钮无意义；上游（get_screen）默认跳过，除非显式 filter。
        // 先放一份常见名，真机跑过后再按实际层名收敛。
        private static readonly HashSet<string> SkipButtonLayers = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "MapNotificationLayer", "MapYieldsLayer", "MapCursorLayer", "MapCameraLayer",
            "MapFactionBarLayer", "MapTimeControlLayer", "MapViewControlLayer", "MapQuestLayer",
        };

        internal static string HandleGetScreen(string id, string raw)
        {
            try
            {
                ScreenBase screen = ScreenManager.TopScreen;
                if (screen == null)
                    return Protocol.Failure(id, "no_active_screen", "当前没有活动界面", false);

                string layerFilter = Jmini.Str(raw, "layerFilter", "");
                bool hasFilter = layerFilter.Length > 0;

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"screenType\":").Append(Protocol.Q(screen.GetType().Name));
                sb.Append(",\"layerCount\":").Append(screen.Layers.Count);
                sb.Append(",\"layers\":[");
                bool firstLayer = true;
                foreach (ScreenLayer layer in screen.Layers)
                {
                    if (!firstLayer) sb.Append(',');
                    firstLayer = false;
                    sb.Append("{\"name\":").Append(Protocol.Q(layer.Name == null ? "" : layer.Name));
                    sb.Append(",\"isActive\":").Append(Jw.B(layer.IsActive));
                    sb.Append(",\"movies\":[");
                    GauntletLayer gl = layer as GauntletLayer;
                    if (gl != null)
                    {
                        bool skipButtons = !hasFilter && layer.Name != null &&
                                           SkipButtonLayers.Contains(layer.Name);
                        bool firstMovie = true;
                        foreach (MovieInfo mi in GetMovies(gl))
                        {
                            if (!firstMovie) sb.Append(',');
                            firstMovie = false;
                            sb.Append("{\"movieName\":").Append(Protocol.Q(mi.MovieName == null ? "" : mi.MovieName));
                            sb.Append(",\"dataSource\":").Append(
                                Protocol.Q(mi.DataSource == null ? "" : mi.DataSource.GetType().Name));
                            List<ButtonInfo> buttons = new List<ButtonInfo>();
                            if (!skipButtons && mi.RootWidget != null)
                                CollectButtons(mi.RootWidget, buttons, hasFilter ? 20 : 8, 0);
                            sb.Append(",\"buttonCount\":").Append(buttons.Count);
                            sb.Append(",\"buttons\":[");
                            for (int bi = 0; bi < buttons.Count; bi++)
                            {
                                if (bi > 0) sb.Append(',');
                                ButtonInfo b = buttons[bi];
                                sb.Append("{\"id\":").Append(Protocol.Q(b.Id == null ? "" : b.Id));
                                sb.Append(",\"text\":").Append(Protocol.Q(b.Text == null ? "" : b.Text));
                                sb.Append(",\"enabled\":").Append(Jw.B(b.Enabled));
                                sb.Append(",\"state\":").Append(Protocol.Q(b.State == null ? "" : b.State));
                                sb.Append('}');
                            }
                            sb.Append("]");
                            if (skipButtons)
                                sb.Append(",\"note\":").Append(Protocol.Q("skipped (map decoration layer)"));
                            sb.Append('}');
                        }
                    }
                    sb.Append("]}");
                }
                sb.Append("]}");
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "get_screen_failed", ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        internal static string HandleGetViewModelProperty(string id, string raw)
        {
            try
            {
                string propertyName = Jmini.Str(raw, "propertyName", "");
                string layerName = Jmini.Str(raw, "layerName", "");
                string subProperties = Jmini.Str(raw, "subProperties", "");
                if (propertyName.Length == 0 || layerName.Length == 0)
                    return Protocol.Failure(id, "bad_args", "propertyName 与 layerName 都必填", false);

                ScreenBase screen = ScreenManager.TopScreen;
                if (screen == null)
                    return Protocol.Failure(id, "no_active_screen", "当前没有活动界面", false);

                object dataSource = null;
                foreach (ScreenLayer layer in screen.Layers)
                {
                    GauntletLayer gl = layer as GauntletLayer;
                    if (gl != null && string.Equals(layer.Name, layerName, StringComparison.OrdinalIgnoreCase))
                    {
                        foreach (MovieInfo mi in GetMovies(gl))
                        {
                            if (mi.DataSource != null) { dataSource = mi.DataSource; break; }
                        }
                        if (dataSource != null) break;
                    }
                }
                if (dataSource == null)
                    return Protocol.Failure(id, "no_data_source",
                        "层 " + layerName + " 没有可读取的 ViewModel", false);

                object value;
                // 2026-09-27 真机：`propertyName` 写错（IsMultiplayer）与写对但值为 null，
                // 原来**都**返回 `ok:true / value:null` ⇒ 调用方分不清"没有这个属性"和"属性是空的"，
                // 只能靠猜。所以这里把"路径走不通"当成一次**明确的失败**（并给出可用属性名），
                // 把 null 留给"确实存在、值就是 null"那一种。
                bool found = TryTraversePropertyPath(dataSource, propertyName, out value);
                if (!found)
                {
                    return Protocol.Failure(id, "property_not_found",
                        "属性 " + propertyName + " 在 " + dataSource.GetType().Name + " 上不存在"
                        + SuggestProperties(dataSource.GetType(), propertyName), false);
                }

                List<string> missingSubs = new List<string>();
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"viewModel\":").Append(Protocol.Q(dataSource.GetType().Name));
                sb.Append(",\"property\":").Append(Protocol.Q(propertyName));
                if (value == null)
                {
                    sb.Append(",\"value\":null,\"count\":null,\"items\":null}");
                    return Protocol.Success(id, sb.ToString());
                }
                if (value is IEnumerable enumerable && !(value is string))
                {
                    // 子属性名写错时 `SerializeItem` 只会给 null，与主属性那头同一个"看不出错"的毛病。
                    // 这里按**第一项**的真实类型核一遍，把不存在的子属性名点出来。
                    if (!string.IsNullOrEmpty(subProperties))
                    {
                        Type itemType = null;
                        foreach (object probe in enumerable)
                        {
                            if (probe != null) { itemType = probe.GetType(); break; }
                        }
                        if (itemType != null)
                        {
                            foreach (string rawSub in subProperties.Split(','))
                            {
                                string p = rawSub.Trim();
                                if (p.Length == 0) continue;
                                PropertyInfo pi = itemType.GetProperty(p,
                                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                                if (pi == null && !missingSubs.Contains(p)) missingSubs.Add(p);
                            }
                        }
                    }
                    int count = 0;
                    StringBuilder items = new StringBuilder();
                    items.Append('[');
                    foreach (object item in enumerable)
                    {
                        if (count > 0) items.Append(',');
                        items.Append(SerializeItem(item, subProperties));
                        count++;
                    }
                    items.Append(']');
                    sb.Append(",\"value\":").Append(count.ToString());
                    sb.Append(",\"count\":").Append(count);
                    sb.Append(",\"items\":").Append(items.ToString());
                    // 没有子属性请求时也给这个字段（空数组），免得调用方要去区分"缺字段"和"没请求"
                    sb.Append(",\"missingSubProperties\":[");
                    for (int mi = 0; mi < missingSubs.Count; mi++)
                    {
                        if (mi > 0) sb.Append(',');
                        sb.Append(Protocol.Q(missingSubs[mi]));
                    }
                    sb.Append(']');
                }
                else
                {
                    sb.Append(",\"value\":").Append(Protocol.Q(value.ToString()));
                    sb.Append(",\"count\":null,\"items\":null");
                }
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                return Protocol.Failure(id, "get_viewmodel_property_failed",
                    ex.GetType().Name + ": " + ex.Message, false);
            }
        }

        // ── 脱壳抄自上游 GetMovies / CollectButtons / FindWidgetById / TraversePropertyPath ──

        private sealed class MovieInfo
        {
            public string MovieName;
            public object DataSource;
            public Widget RootWidget;
        }

        private sealed class ButtonInfo
        {
            public string Id;
            public string Text;
            public bool Enabled;
            public string State;
        }

        private static List<MovieInfo> GetMovies(GauntletLayer layer)
        {
            List<MovieInfo> result = new List<MovieInfo>();
            if (MovieIdentifiersField == null) return result;
            object fieldVal = MovieIdentifiersField.GetValue(layer);
            IEnumerable enumerable = fieldVal as IEnumerable;
            if (enumerable == null) return result;
            foreach (object item in enumerable)
            {
                GauntletMovieIdentifier id = item as GauntletMovieIdentifier;
                if (id == null) continue;
                MovieInfo mi = new MovieInfo();
                mi.MovieName = id.MovieName;
                mi.DataSource = id.DataSource;
                if (id.Movie != null) mi.RootWidget = id.Movie.RootWidget;
                result.Add(mi);
            }
            return result;
        }

        private static string FindTextInChildren(Widget root, int depth)
        {
            if (root == null || depth < 0) return null;
            TextWidget tw = root as TextWidget;
            if (tw != null) return tw.Text;
            for (int i = 0; i < root.ChildCount; i++)
            {
                string s = FindTextInChildren(root.GetChild(i), depth - 1);
                if (s != null) return s;
            }
            return null;
        }

        private static void CollectButtons(Widget widget, List<ButtonInfo> result, int maxDepth, int currentDepth)
        {
            if (widget == null || currentDepth > maxDepth) return;
            ButtonWidget bw = widget as ButtonWidget;
            if (bw != null && widget.IsVisible && !widget.IsHidden)
            {
                string text = FindTextInChildren(widget, 3);
                if (!string.IsNullOrEmpty(widget.Id) || !string.IsNullOrEmpty(text))
                {
                    ButtonInfo bi = new ButtonInfo();
                    bi.Id = widget.Id;
                    bi.Text = text;
                    bi.Enabled = widget.IsEnabled;
                    bi.State = widget.CurrentState;
                    result.Add(bi);
                }
            }
            for (int i = 0; i < widget.ChildCount; i++)
                CollectButtons(widget.GetChild(i), result, maxDepth, currentDepth + 1);
        }

        /// <summary>
        /// 逐段走属性路径。返回 false = **路径走不通**（中间某段不存在，或走到一半是 null），
        /// 与"走通了但值是 null"区分开 —— 后者返回 true + value=null。
        /// </summary>
        private static bool TryTraversePropertyPath(object obj, string path, out object value)
        {
            value = null;
            if (obj == null || path.Length == 0) return false;
            string[] segs = path.Split('.');
            object cur = obj;
            foreach (string seg in segs)
            {
                if (cur == null) return false;      // 中间是 null ⇒ 走不下去，不是"值就是 null"
                PropertyInfo p = cur.GetType().GetProperty(seg,
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                if (p == null) return false;        // 这一段压根不存在
                cur = p.GetValue(cur, null);
            }
            value = cur;
            return true;
        }

        /// <summary>
        /// 属性不存在时给出候选属性名（真的反射列一遍，最多 30 个；先给名字里含查询串的）。
        /// 目的：让"写错属性名"当场可自纠，而不是让人去猜 VM 的内部结构。
        /// </summary>
        private static string SuggestProperties(Type t, string wanted)
        {
            List<string> all = new List<string>();
            List<string> matched = new List<string>();
            foreach (PropertyInfo p in t.GetProperties(BindingFlags.Public | BindingFlags.Instance))
            {
                all.Add(p.Name);
                if (!string.IsNullOrEmpty(wanted)
                    && p.Name.IndexOf(wanted, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    matched.Add(p.Name);
                }
            }
            List<string> show = matched.Count > 0 ? matched : all;
            show.Sort(StringComparer.Ordinal);
            if (show.Count > 30) show = show.GetRange(0, 30);
            if (show.Count == 0) return "";
            return "（" + (matched.Count > 0 ? "名字相近的" : "可用的") + "属性："
                   + string.Join(", ", show.ToArray()) + "）";
        }

        private static string SerializeItem(object item, string subProps)
        {
            if (item == null) return "null";
            if (string.IsNullOrEmpty(subProps))
                return Protocol.Q(item.ToString());
            StringBuilder sb = new StringBuilder();
            sb.Append('{');
            bool first = true;
            foreach (string raw in subProps.Split(','))
            {
                string p = raw.Trim();
                if (p.Length == 0) continue;
                PropertyInfo pi = item.GetType().GetProperty(p,
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                object v = (pi == null) ? null : pi.GetValue(item, null);
                if (!first) sb.Append(',');
                first = false;
                sb.Append(Protocol.Q(p)).Append(':').Append(v == null ? "null" : Protocol.Q(v.ToString()));
            }
            sb.Append('}');
            return sb.ToString();
        }
    }
}
