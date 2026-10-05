using System;
using System.Collections.Generic;
using System.Reflection;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// Harmony 补丁内省（L1 只读）：回答「**谁补了哪个方法**」。
    ///
    /// ## 为什么需要它（本项目的真实痛点）
    ///
    /// 三合一 MOD（RBM + Warbandlord + RCM）的**核心风险就是「双重叠加」** ——
    /// 两个 mod 补同一个方法，谁覆盖谁、Transpiler 撞 Transpiler 会不会生成非法 IL。
    /// 这在知识库 `bannerlord-mod-mechanism-merge` 里一直是**纯理论**的；
    /// 有了本探针才能**实证**。
    ///
    /// 具体到 2026-10-06 的 C6（血量系统）：我**靠猜**"我的 `CharacterMaxHitpointsPatch`
    /// 装上没有""和 RBM 既有的补丁撞没撞同一个方法"。
    /// 有了它，那是**一条命令**。
    ///
    /// ## 与「零 Harmony」原则的关系（重要）
    ///
    /// BlBridge 刻意保持零 Harmony 补丁（见 `DummyRangeBehavior` 的说明：
    /// "不用 Harmony patch —— 那会破坏本项目「删模块即完全回退」的性质"）。
    ///
    /// ⚠️ **本文件不违反那条原则**：它**只读** Harmony 的公开内省 API，
    /// **不创建 Harmony 实例、不 patch 任何方法、不改任何 IL**。
    /// 它和 `EngineProbe` / `InventoryProbe` **是同一性质**：从引擎读真实值。
    /// ⇒ 删掉 BlBridge 模块，进程里不留任何痕迹。
    ///
    /// ## API 依据（反编译 0Harmony 2.4.2，非推测）
    ///
    ///   Harmony.cs:7111  public static IEnumerable&lt;MethodBase&gt; GetAllPatchedMethods()
    ///   Harmony.cs:7099  public static Patches GetPatchInfo(MethodBase method)
    ///   Harmony.cs:7116  public static MethodBase GetOriginalMethod(MethodInfo replacement)
    ///   Patch   : index / owner / priority / before / after / debug / PatchMethod
    ///   Patches : Prefixes / Postfixes / Transpilers / Finalizers / InnerPrefixes / InnerPostfixes / Owners
    /// </summary>
    internal static class PatchProbe
    {
        /// <summary>
        /// 反射拿 Harmony 的类型与方法。
        ///
        /// ⚠️ **为什么用反射而不是直接 `using HarmonyLib`**：
        /// BlBridge 的 `build.ps1` 目前**不引用 0Harmony.dll**，而加引用会让 BlBridge 在
        /// **Harmony 未安装时无法加载**（类型解析失败）—— 那会把一个"可选诊断"变成**硬依赖**，
        /// 违背本项目"删模块即回退 / 不增依赖"的性质。
        /// 反射让它**找不到就优雅退化**（返回 available=false），与 gridhand 的处理一致。
        /// </summary>
        private static Type _harmonyType;
        private static MethodInfo _getAll;
        private static MethodInfo _getInfo;
        private static bool _probed;

        private static void EnsureProbed()
        {
            if (_probed) return;
            _probed = true;
            try
            {
                // 遍历已加载程序集找 HarmonyLib.Harmony（不靠程序集名，改名也不怕）
                foreach (Assembly asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    Type t = null;
                    try { t = asm.GetType("HarmonyLib.Harmony", false); }
                    catch { }
                    if (t == null) continue;
                    MethodInfo all = t.GetMethod("GetAllPatchedMethods",
                        BindingFlags.Public | BindingFlags.Static);
                    MethodInfo info = t.GetMethod("GetPatchInfo",
                        BindingFlags.Public | BindingFlags.Static);
                    if (all == null || info == null) continue;
                    _harmonyType = t;
                    _getAll = all;
                    _getInfo = info;
                    break;
                }
            }
            catch { }
        }

        /// <summary>Harmony 是否可用（未安装 ⇒ available=false，不是错误）。</summary>
        internal static bool Available
        {
            get { EnsureProbed(); return _harmonyType != null; }
        }

        /// <summary>把手写的简短类型名（如 `Mission`/`MobileParty`）与真实类型对上。</summary>
        private static bool TypeMatches(Type t, string filter)
        {
            if (t == null) return false;
            if (string.IsNullOrEmpty(filter)) return true;
            string n = t.Name;
            string full = t.FullName ?? n;
            return n.Equals(filter, StringComparison.OrdinalIgnoreCase)
                || full.Equals(filter, StringComparison.OrdinalIgnoreCase)
                || full.EndsWith("." + filter, StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>按 `owner` 过滤（Harmony 的 owner 是 Harmony 实例的 Id，如 `com.rbmcombat`）。</summary>
        private static bool OwnerMatches(string owner, string filter)
        {
            if (string.IsNullOrEmpty(filter)) return true;
            return !string.IsNullOrEmpty(owner)
                && owner.IndexOf(filter, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        /// <summary>把 `Patch` 对象（反射拿到）摊成可读的一行。</summary>
        private static void AppendPatch(StringBuilder sb, object patch, string kind)
        {
            string owner = GetField<string>(patch, "owner") ?? "";
            object idx = GetField<object>(patch, "index");
            object prio = GetField<object>(patch, "priority");
            object pm = GetProperty<object>(patch, "PatchMethod");

            sb.Append("{\"kind\":").Append(Protocol.Q(kind));
            sb.Append(",\"owner\":").Append(Protocol.Q(owner));
            sb.Append(",\"index\":").Append(idx == null ? "0" : idx.ToString());
            sb.Append(",\"priority\":").Append(prio == null ? "0" : prio.ToString());

            MethodInfo mi = pm as MethodInfo;
            if (mi != null)
            {
                sb.Append(",\"patchMethod\":").Append(Protocol.Q(Describe(mi)));
                Type dt = mi.DeclaringType;
                sb.Append(",\"patchClass\":").Append(Protocol.Q(dt == null ? "" : dt.FullName));
                // ★ 补丁方法所在程序集 = "哪个 mod 干的" 的最直接证据
                sb.Append(",\"patchAssembly\":").Append(
                    Protocol.Q(dt == null ? "" : (dt.Assembly.GetName().Name ?? "")));
            }
            else
            {
                // PatchMethod 可能因模块已卸载而取不到（Harmony 用 moduleGUID+token 延迟解析）
                sb.Append(",\"patchMethod\":null,\"patchClass\":null,\"patchAssembly\":null");
            }
            sb.Append('}');
        }

        private static T GetField<T>(object obj, string name)
        {
            try
            {
                FieldInfo f = obj.GetType().GetField(name,
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                if (f == null) return default(T);
                object v = f.GetValue(obj);
                return v == null ? default(T) : (T)v;
            }
            catch { return default(T); }
        }

        private static T GetProperty<T>(object obj, string name)
        {
            try
            {
                PropertyInfo p = obj.GetType().GetProperty(name,
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                if (p == null) return default(T);
                object v = p.GetValue(obj, null);
                return v == null ? default(T) : (T)v;
            }
            catch { return default(T); }
        }

        private static string Describe(MethodBase m)
        {
            if (m == null) return "";
            StringBuilder sb = new StringBuilder();
            sb.Append(m.DeclaringType == null ? "?" : m.DeclaringType.Name).Append('.').Append(m.Name);
            sb.Append('(');
            ParameterInfo[] ps = m.GetParameters();
            for (int i = 0; i < ps.Length; i++)
            {
                if (i > 0) sb.Append(", ");
                sb.Append(ps[i].ParameterType == null ? "?" : ps[i].ParameterType.Name);
            }
            sb.Append(')');
            return sb.ToString();
        }

        private static string DescribeTarget(MethodBase m)
        {
            if (m == null) return "";
            string type = m.DeclaringType == null ? "?" : (m.DeclaringType.FullName ?? m.DeclaringType.Name);
            return type + "." + m.Name;
        }

        /// <summary>
        /// 从 `Patches` 对象里取某一类补丁的集合。
        ///
        /// ⚠️ **`Prefixes` / `Postfixes` / `Transpilers` / `Finalizers` 是 public readonly
        /// *字段*，不是属性**（反编译确认：`0Harmony.decompiled.cs:8082-8092`）。
        /// 踩过一次：本方法原先写成 `GetProperty(...)` ⇒ **恒返回 null** ⇒
        /// 每一条补丁都被跳过 ⇒ 输出 `scannedMethods=1194` 而 `matchedMethods=0`。
        /// **症状极具欺骗性**：扫描数（1194）看起来完全正常，只是"没有冲突"
        /// —— 差一点就被当成"真的没有冲突"这个结论。
        /// ⇒ 所以这里**字段优先，属性兜底**（版本变了也不至于静默失效）。
        /// </summary>
        private static System.Collections.IEnumerable GetPatchList(object patches, string name)
        {
            object v = GetField<object>(patches, name);
            if (v == null) v = GetProperty<object>(patches, name);
            return v as System.Collections.IEnumerable;
        }

        /// <summary>数一个集合的元素个数（null 记 0）。用 ICollection 快，否则退化为逐个数。</summary>
        private static int Count(System.Collections.IEnumerable seq)
        {
            if (seq == null) return 0;
            System.Collections.ICollection col = seq as System.Collections.ICollection;
            if (col != null) return col.Count;
            int n = 0;
            foreach (object _ in seq) n++;
            return n;
        }

        /// <summary>
        /// 主入口。
        ///
        /// 参数（Jmini 扁平读取）：
        ///   method  —— 只看补某个方法（简短类型名或全名，如 `Mission` / `DefaultCharacterStatsModel`）
        ///   owner   —— 只看某个 Harmony 实例（如 `com.rbmcombat`）
        ///   conflicts —— true 时**只列被 &gt;=2 个 owner 补的方法**（三合一项目的核心判据）
        ///   limit   —— 最多几个方法，默认 100
        ///   detail  —— false 时只给方法名与 owner 计数（省 token）；默认 true
        /// </summary>
        internal static string HandleGetPatches(string id, string raw)
        {
            if (!Available)
            {
                return Protocol.Failure(id, "no_harmony",
                    "没找到 Harmony（HarmonyLib.Harmony 不在已加载程序集里）—— "
                    + "可能没装 Bannerlord.Harmony，或本模块比它先加载", false);
            }

            try
            {
                // ⚠️⚠️ **参数名绝对不能叫 `method`** —— 本项目记过两次的 `Jmini` 扁平读取坑。
                //
                // 请求信封是：`{"protocolVersion":1,"id":"...","method":"get_patches","params":{...}}`
                // （见 tools/bl_mcp.py:523-525）。`Jmini` 是**扁平**读取器：取"文本里第一个 `"key"`"，
                // **不区分信封字段与 params**。
                // ⇒ `Jmini.Str(raw, "method", null)` 读到的是**信封里的方法名 `"get_patches"`**，
                //   过滤器被设成 `get_patches` ⇒ **每个方法都被 SKIP**
                //   ⇒ 输出 `scannedMethods=N / matchedMethods=0`，**看起来就像"一个补丁都没有"**。
                //
                // 实测（2026-10-06）为此连烧 8 轮构建才定位 —— 症状太像"反射读法坏了"。
                // 前车之鉴：`open_ui` 的 `id` 撞过信封请求号（AGENTS.md 记的同一类）。
                // ⇒ 规避与既有做法一致：**参数名不与信封字段重名**。
                string methodFilter = Jmini.Str(raw, "targetType", null);
                string ownerFilter = Jmini.Str(raw, "owner", null);
                bool conflictsOnly = Jmini.Bool(raw, "conflicts", false);
                bool detail = Jmini.Bool(raw, "detail", true);
                int limit = Jmini.Int(raw, "limit", 100);
                if (limit <= 0) limit = 100;
                // ★ 扫描上限（诊断用）：1194 个方法全扫时曾 IPC 超时。
                //   先给个可调的闸门，量清代价再定默认值。
                int scanCap = Jmini.Int(raw, "scanCap", 0);

                object all = _getAll.Invoke(null, null);
                System.Collections.IEnumerable methods = all as System.Collections.IEnumerable;
                if (methods == null)
                {
                    return Protocol.Failure(id, "no_patched_methods",
                        "Harmony.GetAllPatchedMethods() 返回空 —— 可能没有任何补丁，或加载顺序不对", false);
                }

                List<string> entries = new List<string>();
                int scanned = 0;
                int matched = 0;
                int conflictCount = 0;
                // ★ 诊断计数（定位 matched=0 的矛盾：diag 明明读出了 Transpilers=1）
                int diagNonNull = 0;      // GetPatchInfo 返回非 null 的方法数
                int diagTotal = 0;        // 所有方法读到的补丁条目总数
                List<string> diagFirst = new List<string>();   // 供 diag 用：前几个越界/异常样本
                int invokeErrors = 0;                          // GetPatchInfo 调用抛异常的次数

                // ── 主循环之前先**探一次**：反射到底读到了什么 ──
                // ⚠️ 必须只探一次。踩过一次：把这段放进 1194 次的方法循环里 ⇒
                //    每次都对 Patch/Patches 做全字段反射 ⇒ **主线程卡住、IPC 超时**
                //    （症状是 "等待游戏响应超时"，看起来像模块没生效，实际是探针自己太重）。
                string diag = null;
                string sbDiagError = null;
                try
                {
                    // ★ 探"第一个**真有补丁**的方法"，而不是第一个方法 ——
                    //   踩过：`GetAllPatchedMethods()` 的第一个可能是 `Program.Main` 这种
                    //   恰好零补丁的条目，于是 diag 全 0、看起来像"读法坏了"，
                    //   实际只是探错了对象。**诊断本身也要选对样本。**
                    MethodBase pm = null;
                    object pi = null;
                    int guard = 0;
                    foreach (object mo in methods)
                    {
                        if (++guard > 400) break;
                        MethodBase cand = mo as MethodBase;
                        if (cand == null) continue;
                        object p = _getInfo.Invoke(null, new object[] { cand });
                        if (p == null) continue;
                        System.Collections.IEnumerable pre = GetPatchList(p, "Prefixes");
                        System.Collections.IEnumerable pof = GetPatchList(p, "Postfixes");
                        System.Collections.IEnumerable tr = GetPatchList(p, "Transpilers");
                        System.Collections.IEnumerable fi = GetPatchList(p, "Finalizers");
                        int tot = Count(pre) + Count(pof) + Count(tr) + Count(fi);
                        if (tot > 0) { pm = cand; pi = p; break; }
                    }
                    if (pm != null)
                    {
                        StringBuilder dsb = new StringBuilder();
                        dsb.Append('{');
                        dsb.Append("\"probeTarget\":").Append(Protocol.Q(DescribeTarget(pm)));
                        dsb.Append(",\"patchesType\":").Append(
                            Protocol.Q(pi == null ? "null" : pi.GetType().FullName));
                        Type pt = pi.GetType();
                        StringBuilder fs = new StringBuilder();
                        bool f1 = true;
                        foreach (FieldInfo f in pt.GetFields(BindingFlags.Public | BindingFlags.Instance))
                        {
                            if (!f1) fs.Append(',');
                            f1 = false;
                            object v = null;
                            try { v = f.GetValue(pi); } catch { }
                            System.Collections.ICollection col = v as System.Collections.ICollection;
                            // ⚠️ `Protocol.Q(name)` **自带引号** ⇒ 必须显式写出 `"name":` 这个键，
                            //   不能只补一个冒号。踩过：得到 `{"Prefixes":"type":...}`
                            //   ⇒ **畸形 JSON** ⇒ IPC 侧解析失败、客户端**白等到超时**，
                            //   而响应其实早就写好了（症状极易误判为"游戏主线程卡死"）。
                            fs.Append('{').Append("\"name\":").Append(Protocol.Q(f.Name))
                              .Append(",\"type\":").Append(Protocol.Q(f.FieldType.Name))
                              .Append(",\"count\":").Append(col == null ? -1 : col.Count).Append('}');
                        }
                        dsb.Append(",\"fields\":[").Append(fs.ToString()).Append(']');
                        dsb.Append('}');
                        diag = dsb.ToString();
                    }
                    else
                    {
                        diag = "{\"note\":\"扫描前 400 个方法，没有任何一个读到补丁\"}";
                    }
                }
                catch (Exception dx)
                {
                    diag = null;
                    sbDiagError = dx.GetType().Name + ": " + dx.Message;
                }

                foreach (object mo in methods)
                {
                    if (scanCap > 0 && scanned >= scanCap) break;
                    MethodBase target = mo as MethodBase;
                    if (target == null) continue;
                    scanned++;

                    // ── 过滤：目标方法 ──
                    if (!string.IsNullOrEmpty(methodFilter))
                    {
                        if (!TypeMatches(target.DeclaringType, methodFilter)
                            && (target.Name == null ||
                                target.Name.IndexOf(methodFilter, StringComparison.OrdinalIgnoreCase) < 0))
                        {
                            continue;
                        }
                    }

                    object patches;
                    try
                    {
                        patches = _getInfo.Invoke(null, new object[] { target });
                    }
                    catch (Exception exInv)
                    {
                        // ★ 异常**必须显式记下**。此前这里的异常被外层 catch 吞掉，
                        //   表现为"静默 matched=0"，完全看不出发生过什么。
                        invokeErrors++;
                        if (diagFirst.Count < 5)
                        {
                            Exception inner = exInv is TargetInvocationException && exInv.InnerException != null
                                ? exInv.InnerException : exInv;
                            diagFirst.Add("EXC " + DescribeTarget(target) + " : "
                                + inner.GetType().Name + ": " + inner.Message);
                        }
                        continue;
                    }
                    if (patches == null) continue;
                    diagNonNull++;

                    // ── 统计 owner 与各类补丁 ──
                    HashSet<string> owners = new HashSet<string>();
                    int nPrefix = 0, nPostfix = 0, nTrans = 0, nFinal = 0;
                    StringBuilder body = new StringBuilder();

                    string[] kinds = { "Prefixes", "Postfixes", "Transpilers", "Finalizers" };
                    string[] kindNames = { "prefix", "postfix", "transpiler", "finalizer" };
                    int[] counters = new int[4];
                    bool first = true;

                    for (int k = 0; k < kinds.Length; k++)
                    {
                        System.Collections.IEnumerable list = GetPatchList(patches, kinds[k]);
                        if (list == null) continue;
                        foreach (object p in list)
                        {
                            if (p == null) continue;
                            string owner = GetField<string>(p, "owner") ?? "";
                            if (!OwnerMatches(owner, ownerFilter)) continue;
                            owners.Add(owner);
                            counters[k]++;
                            if (detail)
                            {
                                if (!first) body.Append(',');
                                first = false;
                                AppendPatch(body, p, kindNames[k]);
                            }
                        }
                    }

                    nPrefix = counters[0]; nPostfix = counters[1];
                    nTrans = counters[2]; nFinal = counters[3];
                    int totalPatches = nPrefix + nPostfix + nTrans + nFinal;
                    diagTotal += totalPatches;
                    if (totalPatches == 0) continue;   // owner 过滤后没有剩下的

                    bool isConflict = owners.Count >= 2;
                    if (isConflict) conflictCount++;
                    if (conflictsOnly && !isConflict) continue;

                    matched++;
                    if (matched > limit) continue;

                    // ── 组装这个方法的条目 ──
                    StringBuilder one = new StringBuilder();
                    one.Append("{\"target\":").Append(Protocol.Q(DescribeTarget(target)));
                    one.Append(",\"targetAssembly\":").Append(
                        Protocol.Q(target.DeclaringType == null || target.DeclaringType.Assembly == null
                            ? "" : (target.DeclaringType.Assembly.GetName().Name ?? "")));
                    one.Append(",\"owners\":[");
                    bool fo = true;
                    foreach (string o in owners)
                    {
                        if (!fo) one.Append(',');
                        fo = false;
                        one.Append(Protocol.Q(o));
                    }
                    one.Append(']');
                    one.Append(",\"ownerCount\":").Append(owners.Count);
                    one.Append(",\"prefix\":").Append(nPrefix);
                    one.Append(",\"postfix\":").Append(nPostfix);
                    one.Append(",\"transpiler\":").Append(nTrans);
                    one.Append(",\"finalizer\":").Append(nFinal);
                    one.Append(",\"conflict\":").Append(isConflict ? "true" : "false");
                    // ★ Transpiler 撞车是最危险的一类（改 IL，可能生成非法 IL ⇒ 崩溃）
                    one.Append(",\"transpilerClash\":").Append((isConflict && nTrans >= 2) ? "true" : "false");
                    if (detail)
                    {
                        one.Append(",\"patches\":[").Append(body.ToString()).Append(']');
                    }
                    one.Append('}');
                    entries.Add(one.ToString());
                }

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"available\":true");
                sb.Append(",\"harmony\":").Append(Protocol.Q(_harmonyType.Assembly.GetName().Name ?? ""));
                sb.Append(",\"harmonyVersion\":").Append(
                    Protocol.Q(_harmonyType.Assembly.GetName().Version == null
                        ? "" : _harmonyType.Assembly.GetName().Version.ToString()));
                sb.Append(",\"scannedMethods\":").Append(scanned);
                sb.Append(",\"matchedMethods\":").Append(matched);
                sb.Append(",\"conflictMethods\":").Append(conflictCount);
                sb.Append(",\"returned\":").Append(entries.Count);
                sb.Append(",\"nonNullInfo\":").Append(diagNonNull);
                sb.Append(",\"totalPatchesSeen\":").Append(diagTotal);
                sb.Append(",\"firstScanned\":[");
                for (int i = 0; i < diagFirst.Count; i++)
                {
                    if (i > 0) sb.Append(',');
                    sb.Append(Protocol.Q(diagFirst[i]));
                }
                sb.Append(']');
                sb.Append(",\"invokeErrors\":").Append(invokeErrors);
                // ★ 反静默断言：Harmony 报了 N 个被补方法，而我们**一个补丁条目都没读出来**
                //   ⇒ 几乎一定是"读法错了"（实测踩过：把 public readonly **字段**当**属性**读），
                //     **不是**"没有补丁"。显式标出来，免得被当成"真的没有冲突"这个结论
                //     —— 那次 `scannedMethods=1194 / matchedMethods=0` 就差一点被误读。
                if (scanned > 0 && matched == 0
                    && string.IsNullOrEmpty(ownerFilter)
                    && string.IsNullOrEmpty(methodFilter) && !conflictsOnly)
                {
                    sb.Append(",\"warning\":").Append(Protocol.Q(
                        "Harmony 报告 " + scanned + " 个被补方法，但一个补丁条目都没读出来 —— "
                        + "这几乎一定是本探针的读法问题（不是「没有补丁」）"));
                }
                if (diag != null) sb.Append(",\"diag\":").Append(diag);
                if (sbDiagError != null) sb.Append(",\"diagError\":").Append(Protocol.Q(sbDiagError));
                sb.Append(",\"methods\":[").Append(string.Join(",", entries.ToArray())).Append(']');
                sb.Append('}');
                return Protocol.Success(id, sb.ToString());
            }
            catch (Exception ex)
            {
                // 反射调用的异常常被包一层 TargetInvocationException，取内层更可读
                Exception e = ex;
                if (ex is TargetInvocationException && ex.InnerException != null) e = ex.InnerException;
                return Protocol.Failure(id, "get_patches_failed",
                    e.GetType().Name + ": " + e.Message, false);
            }
        }
    }
}
