using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;

namespace BlBridge
{
    /// <summary>
    /// 崩溃守卫（CrashGuard）：**在关键 tick 路径上装 Harmony Finalizer，吞掉本会杀掉进程的托管异常**，
    /// 让游戏继续跑；同时把每一次"吞掉了什么"记成结构化账本，交给外置 MCP 侧读。
    ///
    /// ## 为什么必须是 Finalizer（这是本类的存在理由）
    ///
    /// 三类钩子能力**不同**，不能混为一谈：
    ///
    /// | 钩子 | 能观察到异常 | **能阻止异常传播** |
    /// |---|---|---|
    /// | `AppDomain.FirstChanceException`（=<see cref="ExceptionProbe"/>） | ✅ 最早最全 | ❌ **不能**（它是通知，返回值被忽略） |
    /// | `AppDomain.UnhandledException` | ✅ | ❌ **不能**（此时进程已在终结途中） |
    /// | **Harmony Finalizer** | ✅ | ✅ **能**（返回 null = 吞掉异常，调用方看到"没抛过"） |
    ///
    /// ⇒ "跳过崩溃"这个能力**只能**由 Finalizer 提供。
    ///   前两个我们已经在用（`ExceptionProbe`），它们管**记录**，不管**存活**。
    ///
    /// ## ★ 仍然保持"零 Harmony 硬依赖"（关键设计，别改坏）
    ///
    /// 本类**不用 `using HarmonyLib`**，一律**反射**调 Harmony —— 与
    /// `PatchProbe`（读补丁表）/ `McmProbe` / `UiExtendProbe` 同一范式。
    /// 理由（照 `PatchProbe` 的原始取舍）：加编译期引用会让 BlBridge 在
    /// **Harmony 未安装时无法加载**，把"可选的自保能力"变成**硬依赖**，
    /// 违背本项目「删模块即完全回退 / 不增依赖」的性质。
    /// 反射的代价只是启动时一次类型查找，换来的是 **Harmony 不在就优雅退化成 no-op**。
    ///
    /// ## ⚠️ 安全边界（**必须如实传达，不许含糊**）
    ///
    /// **吞异常 ≠ 修好**。异常被吞掉意味着：
    ///   • 那个方法**没有完成它本该做的事**（半完成状态）；
    ///   • 调用方**以为成功了**，继续按"成功"的假设往下走；
    ///   • 于是可能产生**存档不一致、AI 卡死、数值错乱**等**静默** повреждение
    ///     —— 这些**不会崩溃、不会报错**，比崩溃更难查。
    ///
    /// ⇒ 所以本类**默认不启用**，且启用后有**三道硬闸门**（见 <see cref="BridgeConfig"/>）：
    ///   ① 只挂在**白名单方法**上（tick 类"每帧重来"的路径，吞掉顶多丢一帧）；
    ///   ② **配额**：整个会话吞掉的次数有上限，超了自动停手（防止"静默假活"无限延续）；
    ///   ③ **熔断**：同一签名短时间内反复出现 ⇒ 判定为"这条路径结构性坏了"，
    ///      停止吞它并**如实上报**（继续吞只是把崩溃换成卡顿+数据损坏）。
    ///
    /// **本类不修改任何游戏数据、不写存档、不联网**，只做"扔/不扔"这一个决定 + 记账。
    /// </summary>
    internal static class CrashGuard
    {
        // ─────────────────────────────────────────────────────────────────
        // 状态
        // ─────────────────────────────────────────────────────────────────

        /// <summary>已成功装上 Finalizer 的目标方法数。</summary>
        private static int _patchedCount;

        /// <summary>是否已尝试过安装（幂等闸门 —— 装不装只试一次）。</summary>
        private static bool _installAttempted;

        /// <summary>安装失败的原因（空 = 没失败）。给 `bl_status` / MCP 侧看。</summary>
        private static string _installError = "";

        private static string _harmonyId = "";

        /// <summary>成功挂上补丁的目标方法描述（人类可读）。</summary>
        private static readonly List<string> PatchedTargets = new List<string>();

        /// <summary>本会话**已吞掉**的异常次数（配额计数）。</summary>
        private static long _swallowed;

        /// <summary>本会话**已放行**（没吞，交给游戏原有逻辑）的次数。</summary>
        private static long _passedThrough;

        /// <summary>因配额耗尽而不再吞的次数（这些异常照原样抛出）。</summary>
        private static long _quotaBlocked;

        /// <summary>因熔断而不再吞的次数。</summary>
        private static long _breakerBlocked;

        /// <summary>配额是否已耗尽（耗尽后本类退化为"只记账、不吞"）。</summary>
        private static bool _quotaExhausted;

        // ── 待写账本（回调里只入队，主线程排空 —— 照 ExceptionProbe 的线程纪律）──
        private struct Event
        {
            internal bool Swallowed;
            internal string Reason;      // "swallowed" / "quota" / "breaker" / "unsafe"
            internal string TypeName;
            internal string Message;
            internal string TargetMethod;
            internal string StackFrames;
            internal long Count;
        }

        private static readonly ConcurrentQueue<Event> Pending = new ConcurrentQueue<Event>();
        private static int _queued;
        private const int MaxQueued = 2048;
        private static long _dropped;
        private static long _totalSeen;

        /// <summary>
        /// 签名 → 熔断状态。键 = 类型全名 + "|" + 栈首帧。
        ///
        /// ★ 为什么用签名而不是"同一目标方法"：同一个 tick 方法里可能有**多种**独立故障，
        ///   按目标方法熔断会让第一种故障把其它故障也一起"放行"，那是不必要的暴露。
        /// </summary>
        private static readonly ConcurrentDictionary<string, BreakerState> Breakers =
            new ConcurrentDictionary<string, BreakerState>();

        private sealed class BreakerState
        {
            internal int Count;
            internal bool Open;
        }

        /// <summary>
        /// 单个签名在**熔断前**允许被吞的次数。
        ///
        /// 为什么是 20：tick 类路径每秒 ~60 次调用，20 次约 0.3 秒就能攒到
        /// ⇒ "同一条异常连续 20 次"基本可以判定为**结构性故障**（不是偶发抖动），
        /// 继续吞只会把"立即崩溃"换成"静默卡死 + 数据损坏"。
        /// </summary>
        private const int BreakerThreshold = 20;

        /// <summary>**会话级**吞异常总配额。0 = 不限（不推荐，仅调试用）。</summary>
        private const int DefaultSessionQuota = 200;

        // ─────────────────────────────────────────────────────────────────
        // 反射句柄（一次解析，之后复用）
        // ─────────────────────────────────────────────────────────────────

        private static Type _harmonyType;
        private static ConstructorInfo _harmonyCtor;
        private static MethodInfo _patchMethod;
        private static ConstructorInfo _harmonyMethodCtor;
        private static object _harmonyInstance;
        private static bool _probed;

        internal static bool Installed { get { return _patchedCount > 0; } }
        internal static int PatchedCount { get { return _patchedCount; } }
        internal static string InstallError { get { return _installError; } }
        internal static long Swallowed { get { return Interlocked.Read(ref _swallowed); } }
        internal static long PassedThrough { get { return Interlocked.Read(ref _passedThrough); } }
        internal static long TotalSeen { get { return Interlocked.Read(ref _totalSeen); } }
        internal static long Dropped { get { return Interlocked.Read(ref _dropped); } }
        internal static bool QuotaExhausted { get { return _quotaExhausted; } }
        internal static int OpenBreakers
        {
            get
            {
                int n = 0;
                foreach (KeyValuePair<string, BreakerState> kv in Breakers)
                    if (kv.Value != null && kv.Value.Open) n++;
                return n;
            }
        }

        /// <summary>
        /// 可挂载的目标白名单：**只含"每帧重来"的 tick 路径**。
        ///
        /// ★ 为什么白名单这么窄（安全核心）：
        ///   这些方法的特点是**下一次 tick 会重新做一遍**，所以吞掉一次异常
        ///   最多损失**一帧**的行为，不会留下"半完成且永不重试"的状态。
        ///   反过来，像 `LoadSaveGame` / `ApplyResults` / `SaveAs` 这类**一次性语义**的方法
        ///   **绝不能**吞 —— 吞了就是"存档/战役状态半完成"，那比崩溃更糟。
        ///
        /// ⚠️ 签名全部**用反射实测过**（2026-10-07，游戏 1.4.8）：
        ///   实测探针输出见 `docs/crash-guard-targets.md`。
        ///   `MissionBehavior.OnTick` / `ScriptComponentBehavior.Tick` **不是**这样签名的，
        ///   故**不在此表**（宁缺勿滥：挂错方法的 Finalizer 可能吞掉不该吞的异常）。
        /// </summary>
        private static readonly string[,] Targets = new string[,]
        {
            // { 程序集名, 类型全名, 方法名 }
            { "TaleWorlds.DotNet",       "TaleWorlds.DotNet.Managed",              "ApplicationTick" },
            { "TaleWorlds.MountAndBlade","TaleWorlds.MountAndBlade.Mission",        "Tick" },
            { "TaleWorlds.ScreenSystem", "TaleWorlds.ScreenSystem.ScreenManager",   "Tick" },
        };

        // ─────────────────────────────────────────────────────────────────
        // 安装
        // ─────────────────────────────────────────────────────────────────

        private static void EnsureProbed()
        {
            if (_probed) return;
            _probed = true;
            try
            {
                // 遍历已加载程序集找 HarmonyLib.Harmony（不靠程序集名，改名也不怕）——
                // 与 PatchProbe.EnsureProbed 同一范式。
                foreach (Assembly asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    Type t = null;
                    try { t = asm.GetType("HarmonyLib.Harmony", false); }
                    catch { }
                    if (t == null) continue;

                    ConstructorInfo ctor = t.GetConstructor(new Type[] { typeof(string) });
                    MethodInfo patch = null;
                    foreach (MethodInfo m in t.GetMethods(BindingFlags.Public | BindingFlags.Instance))
                    {
                        if (m.Name != "Patch") continue;
                        ParameterInfo[] ps = m.GetParameters();
                        if (ps.Length == 5) { patch = m; break; }
                    }
                    Type hmType = null;
                    try { hmType = asm.GetType("HarmonyLib.HarmonyMethod", false); }
                    catch { }
                    if (hmType == null) continue;
                    ConstructorInfo hmCtor = hmType.GetConstructor(new Type[] { typeof(MethodInfo) });
                    if (ctor == null || patch == null || hmCtor == null) continue;

                    _harmonyType = t;
                    _harmonyCtor = ctor;
                    _patchMethod = patch;
                    _harmonyMethodCtor = hmCtor;
                    return;
                }
            }
            catch
            {
                _harmonyType = null;
            }
        }

        /// <summary>
        /// 装崩溃守卫。**必须由 SubModule 在模块加载时显式调用**（本类不自触发 ——
        /// "默认不启用"是安全设计，见类注释）。
        /// 幂等；任何失败都**不外抛**（装不上不该拖垮模块加载），但会如实记进 <see cref="InstallError"/>。
        /// </summary>
        internal static void Install()
        {
            if (_installAttempted) return;
            _installAttempted = true;

            try
            {
                // 开账本（追加模式：跨会话累积，与 exceptions.jsonl 同风格）
                string path = Path.Combine(BridgeConfig.LogDir, "crashguard.jsonl");
                Jw.OpenGuardLog(path, true);

                EnsureProbed();
                if (_harmonyType == null)
                {
                    _installError = "HarmonyLib.Harmony 未找到（Harmony 未安装或未加载）⇒ 优雅退化为 no-op";
                    WriteEvent(false, "unavailable", null, null, "CrashGuard.Install",
                                _installError, 1);
                    return;
                }

                _harmonyId = BridgeConfig.ModuleId + ".CrashGuard";
                _harmonyInstance = _harmonyCtor.Invoke(new object[] { _harmonyId });

                MethodInfo finalizer = typeof(CrashGuard).GetMethod(
                    "Finalizer", BindingFlags.Static | BindingFlags.NonPublic);
                if (finalizer == null)
                {
                    _installError = "找不到本类的 Finalizer 方法（构建异常？）";
                    return;
                }
                object harmonyMethod = _harmonyMethodCtor.Invoke(new object[] { finalizer });

                for (int i = 0; i < Targets.GetLength(0); i++)
                {
                    if (TryPatchOne(Targets[i, 0], Targets[i, 1], Targets[i, 2], harmonyMethod))
                        _patchedCount++;
                }

                WriteEvent(false, "installed", null, null, "CrashGuard.Install",
                            "已挂 " + _patchedCount + " 个目标：" + string.Join(", ", PatchedTargets.ToArray()),
                            1);
            }
            catch (Exception ex)
            {
                _installError = "安装异常：" + ex.GetType().Name + "：" + ex.Message;
                try { WriteEvent(false, "install_failed", ex.GetType().FullName, ex.Message,
                                  "CrashGuard.Install", _installError, 1); } catch { }
            }
        }

        private static bool TryPatchOne(string asmName, string typeName, string methodName, object harmonyMethod)
        {
            try
            {
                Assembly asm = null;
                foreach (Assembly a in AppDomain.CurrentDomain.GetAssemblies())
                {
                    try
                    {
                        if (string.Equals(a.GetName().Name, asmName, StringComparison.OrdinalIgnoreCase))
                        { asm = a; break; }
                    }
                    catch { }
                }
                if (asm == null) return false;

                Type type = asm.GetType(typeName, false);
                if (type == null) return false;

                // 目标方法签名在 1.4.8 上是 void X(float dt)；用参数个数定位而非硬编码，
                // 版本漂移时（比如加了参数）宁可不挂，也不挂错。
                MethodInfo target = null;
                foreach (MethodInfo m in type.GetMethods(BindingFlags.Public | BindingFlags.NonPublic
                                                        | BindingFlags.Instance | BindingFlags.Static))
                {
                    if (m.Name != methodName) continue;
                    ParameterInfo[] ps = m.GetParameters();
                    if (ps.Length != 1) continue;
                    if (ps[0].ParameterType != typeof(float)) continue;
                    target = m;
                    break;
                }
                if (target == null) return false;

                _patchMethod.Invoke(_harmonyInstance, new object[]
                {
                    target, null, null, null, harmonyMethod
                });
                PatchedTargets.Add(typeName + "." + methodName);
                return true;
            }
            catch (Exception ex)
            {
                // 单个目标失败不影响其它目标（如某版本方法签名变了）
                try
                {
                    WriteEvent(false, "target_failed", null, null, typeName + "." + methodName,
                               ex.GetType().Name + "：" + ex.Message, 1);
                }
                catch { }
                return false;
            }
        }

        // ─────────────────────────────────────────────────────────────────
        // ★ Finalizer 本体：**这是唯一"决定要不要吞"的地方**
        // ─────────────────────────────────────────────────────────────────

        /// <summary>
        /// Harmony Finalizer。返回 null = **吞掉异常**；返回 <paramref name="__exception"/> = 放行。
        ///
        /// ⚠️ 三条硬约束（违反任一条都会比崩溃更糟）：
        ///   ① **绝不抛异常** —— Finalizer 自己抛会把原异常替换成一个更难查的异常；
        ///   ② **绝不做 I/O / 不加锁等待** —— 它跑在游戏主线程的 tick 路径上；
        ///   ③ **拿不准就放行** —— "继续崩"是可接受的失败，"静默腐蚀存档"不是。
        /// </summary>
        private static Exception Finalizer(Exception __exception, MethodBase __originalMethod)
        {
            try
            {
                if (__exception == null) return null;

                Interlocked.Increment(ref _totalSeen);

                string target = Describe(__originalMethod);
                string typeName = null;
                string message = null;
                string frames = null;
                try
                {
                    typeName = __exception.GetType().FullName;
                    message = __exception.Message;
                    frames = HeadFrames(__exception);
                }
                catch { }

                // ── 判据 1：本质性异常**永不吞** ──
                // 这些异常代表"运行时/内存/栈已不可信"，吞掉它之后的执行**没有意义**，
                // 而且会把问题从"明确崩溃"变成"随机静默错误"。
                if (IsFatal(typeName))
                {
                    Interlocked.Increment(ref _passedThrough);
                    WriteEvent(false, "fatal_passthrough", typeName, message, target, frames, 1);
                    return __exception;
                }

                // ── 判据 2：熔断（同一签名反复出现 ⇒ 结构性故障）──
                string sig = Signature(typeName, frames);
                BreakerState bs = Breakers.GetOrAdd(sig, delegate { return new BreakerState(); });
                int seen;
                lock (bs) { seen = ++bs.Count; }
                if (seen > BreakerThreshold)
                {
                    bool justOpened = false;
                    lock (bs)
                    {
                        if (!bs.Open) { bs.Open = true; justOpened = true; }
                    }
                    Interlocked.Increment(ref _breakerBlocked);
                    Interlocked.Increment(ref _passedThrough);
                    if (justOpened)
                    {
                        // 只在"刚打开"时记一条，避免熔断本身刷屏
                        WriteEvent(false, "breaker_open", typeName, message, target,
                                   "同一签名已连续 " + seen + " 次 ⇒ 判定为结构性故障，**停止吞它**"
                                   + "（继续吞只会把崩溃换成卡死+数据损坏）。"
                                   + "要恢复：修好根因后重启游戏。", seen);
                    }
                    return __exception;
                }

                // ── 判据 3：配额（防"静默假活"无限延续）──
                int quota = BridgeConfig.CrashGuardSessionQuota;
                if (quota > 0)
                {
                    long n = Interlocked.Increment(ref _swallowed);
                    if (n > quota)
                    {
                        Interlocked.Decrement(ref _swallowed);
                        if (!_quotaExhausted)
                        {
                            _quotaExhausted = true;
                            WriteEvent(false, "quota_exhausted", typeName, message, target,
                                       "本会话吞异常已达配额 " + quota + " 次 ⇒ 之后一律放行。"
                                       + "配额的存在意义：**无限地假装没事比崩溃更危险**。", n);
                        }
                        Interlocked.Increment(ref _quotaBlocked);
                        Interlocked.Increment(ref _passedThrough);
                        return __exception;
                    }
                }
                else
                {
                    Interlocked.Increment(ref _swallowed);
                }

                // ── 通过全部闸门 ⇒ 吞掉 ──
                WriteEvent(true, "swallowed", typeName, message, target, frames, 1);
                return null;
            }
            catch
            {
                // ★ 判据 ①：Finalizer 自身绝不抛。拿不准时**放行原异常**（失败要安全）。
                return __exception;
            }
        }

        /// <summary>
        /// "永不可吞"的异常类型判定。
        ///
        /// 判据：这些异常表示**运行时基础设施已不可信** ——
        ///   • `OutOfMemoryException`：内存已经没了，继续跑只会到处失败；
        ///   • `StackOverflowException`：栈已爆（.NET 默认直接杀进程，这里只是防御性登记）；
        ///   • `ThreadAbortException` / `AccessViolationException`：线程/内存已受损；
        ///   • `BadImageFormatException` / `TypeInitializationException`：
        ///     程序集/静态构造坏了 ⇒ 后续每次调用都会失败，吞它等于无限循环。
        /// </summary>
        private static bool IsFatal(string typeName)
        {
            if (string.IsNullOrEmpty(typeName)) return true;   // 拿不准 ⇒ 放行
            return typeName == "System.OutOfMemoryException"
                || typeName == "System.StackOverflowException"
                || typeName == "System.Threading.ThreadAbortException"
                || typeName == "System.AccessViolationException"
                || typeName == "System.BadImageFormatException"
                || typeName == "System.TypeInitializationException"
                || typeName == "System.InsufficientExecutionStackException"
                || typeName == "System.Runtime.InteropServices.SEHException";
        }

        private static string Describe(MethodBase m)
        {
            try
            {
                if (m == null) return "(unknown)";
                return (m.DeclaringType == null ? "?" : m.DeclaringType.FullName) + "." + m.Name;
            }
            catch { return "(unknown)"; }
        }

        /// <summary>取前 4 帧（够定位"谁是调用者"，又不至于在 tick 路径上搬长栈）。</summary>
        private static string HeadFrames(Exception ex)
        {
            try
            {
                string st = ex.StackTrace;
                if (string.IsNullOrEmpty(st)) return null;
                StringBuilder sb = new StringBuilder();
                int start = 0, taken = 0;
                while (start < st.Length && taken < 4)
                {
                    int nl = st.IndexOf('\n', start);
                    int end = nl < 0 ? st.Length : nl;
                    string line = st.Substring(start, end - start).Trim();
                    if (line.Length > 0)
                    {
                        if (taken > 0) sb.Append('\n');
                        sb.Append(line);
                        taken++;
                    }
                    if (nl < 0) break;
                    start = nl + 1;
                }
                return sb.Length == 0 ? null : sb.ToString();
            }
            catch { return null; }
        }

        private static string Signature(string typeName, string frames)
        {
            string head = frames ?? "";
            int nl = head.IndexOf('\n');
            if (nl > 0) head = head.Substring(0, nl);
            return (typeName ?? "?") + "|" + head.Trim();
        }

        // ─────────────────────────────────────────────────────────────────
        // 记账（回调只入队；写盘在主线程 Drain）
        // ─────────────────────────────────────────────────────────────────

        private static void WriteEvent(bool swallowed, string reason, string typeName,
                                       string message, string target, string frames, long count)
        {
            if (Interlocked.Increment(ref _queued) > MaxQueued)
            {
                Interlocked.Decrement(ref _queued);
                Interlocked.Increment(ref _dropped);
                return;
            }
            Event e = new Event();
            e.Swallowed = swallowed;
            e.Reason = reason;
            e.TypeName = typeName;
            e.Message = message;
            e.TargetMethod = target;
            e.StackFrames = frames;
            e.Count = count;
            Pending.Enqueue(e);
        }

        /// <summary>
        /// 主线程排空（由 `SubModule.OnApplicationTick` 调用，与 `ExceptionProbe.Drain` 同处）。
        /// **I/O 只在这里做**。
        /// </summary>
        internal static void Drain(int max)
        {
            if (max <= 0) return;
            int n = 0;
            Event e;
            while (n < max && Pending.TryDequeue(out e))
            {
                Interlocked.Decrement(ref _queued);
                n++;
                try { Jw.WriteGuardLog(FormatEvent(e)); }
                catch { }
            }
        }

        private static string FormatEvent(Event e)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"t\":\"guard\"");
            sb.Append(",\"utc\":\"").Append(Jw.UtcNow()).Append('"');
            sb.Append(",\"runToken\":\"").Append(Jw.Esc(Protocol.RunToken)).Append('"');
            sb.Append(",\"pid\":").Append(Protocol.Pid);
            sb.Append(",\"action\":\"").Append(Jw.Esc(e.Swallowed ? "swallow" : "pass")).Append('"');
            sb.Append(",\"reason\":\"").Append(Jw.Esc(e.Reason)).Append('"');
            if (e.TypeName != null)
                sb.Append(",\"type\":\"").Append(Jw.Esc(e.TypeName)).Append('"');
            if (e.Message != null)
                sb.Append(",\"message\":\"").Append(Jw.Esc(Truncate(e.Message, 400))).Append('"');
            if (e.TargetMethod != null)
                sb.Append(",\"target\":\"").Append(Jw.Esc(e.TargetMethod)).Append('"');
            if (e.StackFrames != null)
                sb.Append(",\"frames\":\"").Append(Jw.Esc(Truncate(e.StackFrames, 1200))).Append('"');
            sb.Append(",\"count\":").Append(Jw.N(e.Count));
            sb.Append('}');
            return sb.ToString();
        }

        private static string Truncate(string s, int max)
        {
            if (string.IsNullOrEmpty(s)) return s;
            return s.Length <= max ? s : s.Substring(0, max) + "...";
        }

        /// <summary>会话汇总（给状态文件 / MCP 侧 `bl_crashguard` 一个便宜的概览）。</summary>
        internal static string SummaryJson()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"enabled\":").Append(Jw.B(BridgeConfig.CrashGuardEnabled));
            sb.Append(",\"installed\":").Append(Jw.B(Installed));
            sb.Append(",\"harmonyId\":\"").Append(Jw.Esc(_harmonyId)).Append('"');
            sb.Append(",\"patchedTargets\":").Append(PatchedTargets.Count);
            sb.Append(",\"targets\":[");
            for (int i = 0; i < PatchedTargets.Count; i++)
            {
                if (i > 0) sb.Append(',');
                sb.Append('"').Append(Jw.Esc(PatchedTargets[i])).Append('"');
            }
            sb.Append(']');
            sb.Append(",\"totalSeen\":").Append(Jw.N(TotalSeen));
            sb.Append(",\"swallowed\":").Append(Jw.N(Swallowed));
            sb.Append(",\"passedThrough\":").Append(Jw.N(PassedThrough));
            sb.Append(",\"quotaBlocked\":").Append(Jw.N(Interlocked.Read(ref _quotaBlocked)));
            sb.Append(",\"breakerBlocked\":").Append(Jw.N(Interlocked.Read(ref _breakerBlocked)));
            sb.Append(",\"quota\":").Append(BridgeConfig.CrashGuardSessionQuota);
            sb.Append(",\"quotaExhausted\":").Append(Jw.B(QuotaExhausted));
            sb.Append(",\"openBreakers\":").Append(OpenBreakers);
            sb.Append(",\"breakerThreshold\":").Append(BreakerThreshold);
            sb.Append(",\"dropped\":").Append(Jw.N(Dropped));
            sb.Append(",\"installError\":\"").Append(Jw.Esc(_installError)).Append('"');
            sb.Append(",\"boundary\":\"").Append(Jw.Esc(
                "吞异常≠修好：被吞的方法没做完它该做的事，调用方以为成功了。"
                + "可能产生**不崩溃、不报错的静默损坏**（存档不一致/AI 卡死/数值错乱）。"
                + "本功能默认关闭，且带配额+熔断+致命异常白名单三道闸门。")).Append('"');
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>卸载（模块卸载时）：撤掉补丁 —— "删模块即完全回退"的一部分。</summary>
        internal static void Uninstall()
        {
            if (!_installAttempted) return;
            try
            {
                if (_harmonyInstance != null && _harmonyType != null)
                {
                    MethodInfo unpatchAll = _harmonyType.GetMethod("UnpatchAll",
                        new Type[] { typeof(string) });
                    if (unpatchAll != null && !string.IsNullOrEmpty(_harmonyId))
                        unpatchAll.Invoke(_harmonyInstance, new object[] { _harmonyId });
                    else
                    {
                        MethodInfo noArg = _harmonyType.GetMethod("UnpatchAll", Type.EmptyTypes);
                        if (noArg != null) noArg.Invoke(_harmonyInstance, null);
                    }
                }
            }
            catch
            {
            }
            try { Jw.CloseGuardLog(); } catch { }
        }
    }
}
