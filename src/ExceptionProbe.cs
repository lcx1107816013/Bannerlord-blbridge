using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Reflection;
using System.Text;
using System.Threading;

namespace BlBridge
{
    /// <summary>
    /// 运行时异常捕获（**FirstChance**）—— 与 ButterLib / BEW **等价的能力**，
    /// 但**零 Harmony、零依赖**，且输出是**结构化 JSONL** 而不是弹窗。
    ///
    /// ## 为什么是"等价实现"而不是"读 ButterLib"
    ///
    /// 本会话反编译 + 实测确认：**ButterLib 的崩溃报告不落盘** ——
    /// 它的 `ExceptionReporter.Show()` 直接走
    /// `CrashReportImGui.ShowAndWait(...)`（失败才退到 WinForms 弹窗），
    /// 没有任何"自动写文件"的开关（`config.json` 里也没有）。
    /// ⇒ **"读它的报告文件"这条路根本不通**（不是没找对路径，是它不写）。
    ///
    /// 而 BlBridge 是**无人值守**的（弹窗会卡死整个流程）⇒
    /// 唯一可行且合适的做法是：**自己订阅、自己落结构化日志**。
    ///
    /// ★ **而且这样做有个额外好处：不依赖 ButterLib 是否安装**。
    /// 与 `bl_crash`（读 minidump）互补：那一半管"进程已死"，这一半管"进程还活着但有人抛异常"。
    ///
    /// ## 为什么不需要 Harmony（关键）
    ///
    /// `AppDomain.CurrentDomain.FirstChanceException` 是 **.NET 原生事件** ——
    /// 它在**任何异常被抛出时**就触发（即使随后被 catch），
    /// 任何程序集都能订阅，**不需要 patch 任何方法、不需要改 IL**。
    /// ⇒ 与本项目"零 Harmony / 删模块即完全回退"的立场**完全兼容**。
    ///
    /// ## 与 ButterLib/BEW 的分工（诚实说明边界）
    ///
    /// | 场景 | 谁能抓到 |
    /// |---|---|
    /// | 托管异常（含**被 catch 掉**的） | ✅ 本探针（FirstChance 比它们的 Finalizer 更早、更全） |
    /// | **JIT 期**失败（非法 IL） | ❌ **谁都抓不到** —— 不在任何方法体内（`bl_crash` 的 dump 分析可事后定位） |
    /// | **原生**崩溃（`0xC0000005` 等） | ❌ 托管事件看不到 ⇒ 交给 `bl_crash` + WER minidump |
    ///
    /// ## ★ 线程与性能约束（照 AGENTS.md「主线程 tick 硬规则」）
    ///
    /// `FirstChanceException` 会在**任何线程、任何异常**上触发，**可能极其频繁**
    /// （正常控制流里也可能大量抛异常）。⇒ 回调里**绝不做 I/O**：
    ///   1. 只把**摘要**推进 `ConcurrentQueue`（无锁、无分配大对象）；
    ///   2. 真正的写盘在 `OnApplicationTick` 里**排空**（与 `ScenarioRunner.Watchdog()` 同款隔离）；
    ///   3. 加**环形上限**与**去重计数**，防止一次异常风暴把磁盘写满。
    /// </summary>
    internal static class ExceptionProbe
    {
        /// <summary>一条待写记录（回调里只造这个结构，不做 I/O）。</summary>
        private struct Entry
        {
            internal string TypeName;
            internal string Message;
            internal string StackHead;
            internal string ThreadName;
            internal bool HandledUnknown;
        }

        // ★ 环形上限：回调**永不阻塞**，满了就丢并计数（丢了多少要报出来，不静默）
        private const int MaxQueued = 4096;
        private static readonly ConcurrentQueue<Entry> Pending = new ConcurrentQueue<Entry>();
        private static int _queued;
        private static long _dropped;
        private static long _totalSeen;

        // ★ 去重：同一 (类型 + 栈首帧) 只详细记一次，其余只累加计数。
        //   理由：异常风暴里同一条异常会重复几万次，逐条写会淹没有效信息。
        private static readonly ConcurrentDictionary<string, int> SeenCounts =
            new ConcurrentDictionary<string, int>();
        private static readonly ConcurrentDictionary<string, long> SeenFirstTick =
            new ConcurrentDictionary<string, long>();

        private static bool _installed;
        private static long _tickCounter;

        internal static bool Installed { get { return _installed; } }
        internal static long TotalSeen { get { return Interlocked.Read(ref _totalSeen); } }
        internal static long Dropped { get { return Interlocked.Read(ref _dropped); } }
        internal static int DistinctCount { get { return SeenCounts.Count; } }

        /// <summary>
        /// 装订阅。**必须早**（模块加载时）—— 晚装会漏掉此前抛过的异常。
        /// 幂等；异常不外抛（装不上不该拖垮模块加载）。
        /// </summary>
        internal static void Install()
        {
            if (_installed) return;
            try
            {
                // ★ 先开侧信道再订阅 —— 否则订阅之后、开文件之前的异常会丢。
                //   侧信道是**追加**模式（异常日志跨会话累积更有用）。
                string path = System.IO.Path.Combine(BridgeConfig.LogDir, "exceptions.jsonl");
                Jw.OpenSide(path, true);

                AppDomain.CurrentDomain.FirstChanceException += OnFirstChance;
                _installed = true;

                // 开一条"会话开始"界碑：便于在跨会话累积的文件里切分本轮
                // ⚠️ 引号手写（`Jw.Esc` 不加引号 —— 见写 exception 处的注释）
                StringBuilder h = new StringBuilder();
                h.Append("{\"t\":\"session\"");
                h.Append(",\"utc\":\"").Append(Jw.Esc(Jw.UtcNow())).Append('"');
                h.Append(",\"note\":\"").Append(Jw.Esc(
                    "FirstChance 订阅已装（零 Harmony）；只记托管异常，"
                    + "JIT 期失败与原生崩溃抓不到 —— 那两类交给 bl_crash / WER minidump")).Append('"');
                h.Append('}');
                Jw.WriteSide(h.ToString());
            }
            catch
            {
                _installed = false;
            }
        }

        /// <summary>卸订阅（模块卸载时）。**这是"删模块即完全回退"的一部分** —— 订阅是我们的，撤得干净。</summary>
        internal static void Uninstall()
        {
            if (!_installed) return;
            try
            {
                AppDomain.CurrentDomain.FirstChanceException -= OnFirstChance;
            }
            catch { }
            _installed = false;
            try { Jw.CloseSide(); } catch { }
        }

        /// <summary>
        /// ★ **回调体：不做 I/O、不加锁、不抛异常**（三个"不"是硬要求）。
        /// 只造一个小结构入队。任何反射/格式化都放到主线程排出时做。
        /// </summary>
        private static void OnFirstChance(object sender, System.Runtime.ExceptionServices.FirstChanceExceptionEventArgs e)
        {
            try
            {
                Interlocked.Increment(ref _totalSeen);
                Exception ex = e == null ? null : e.Exception;
                if (ex == null) return;

                // 环形上限：满了就丢（并计数），**绝不阻塞**触发异常的线程
                if (Interlocked.Increment(ref _queued) > MaxQueued)
                {
                    Interlocked.Decrement(ref _queued);
                    Interlocked.Increment(ref _dropped);
                    return;
                }

                Entry en = new Entry();
                en.TypeName = ex.GetType().FullName;
                en.Message = ex.Message;
                // 栈只取**第一帧**（回调里不做大字符串操作；完整栈在主线程排空时按需再取）
                try
                {
                    string st = ex.StackTrace;
                    if (!string.IsNullOrEmpty(st))
                    {
                        int nl = st.IndexOf('\n');
                        en.StackHead = (nl > 0 ? st.Substring(0, nl) : st).Trim();
                    }
                }
                catch { en.StackHead = null; }
                try { en.ThreadName = Thread.CurrentThread.Name; }
                catch { en.ThreadName = null; }

                Pending.Enqueue(en);
            }
            catch
            {
                // ★ 回调里吞掉一切：**绝不能让诊断设施把游戏搞崩**
                //   （这与"诊断设施不得影响被测对象"的原则一致）。
            }
        }

        /// <summary>
        /// 主线程排空（由 `SubModule.OnApplicationTick` 调）。**I/O 只在这里发生**。
        /// 返回本次写出的行数（0 = 没东西）。
        /// </summary>
        internal static int Drain(int maxPerTick)
        {
            _tickCounter++;
            int written = 0;
            if (maxPerTick <= 0) maxPerTick = 64;

            Entry en;
            while (written < maxPerTick && Pending.TryDequeue(out en))
            {
                Interlocked.Decrement(ref _queued);
                try
                {
                    string key = (en.TypeName ?? "?") + "|" + (en.StackHead ?? "");
                    int count = SeenCounts.AddOrUpdate(key, 1, delegate(string k, int v) { return v + 1; });

                    // 首次见到：写完整一条；之后只累加（排空时若已达上限则留待下帧）
                    if (count == 1)
                    {
                        // ★ 若是 HarmonyException，**单独留一份 message** ——
                        //   目标类型/方法在 message 里（`Harmony.cs:6028` 的 `attr.Description()`），
                        //   而 SeenCounts 只存计数。`bl_patch_failures` 就靠这份定位"哪个补丁失败了"。
                        try
                        {
                            if (!string.IsNullOrEmpty(en.TypeName)
                                && en.TypeName.IndexOf("HarmonyException", StringComparison.Ordinal) >= 0)
                            {
                                _patchFailureMessages.TryAdd(key, en.Message ?? "");
                            }
                        }
                        catch { }
                        long firstTick;
                        SeenFirstTick.TryAdd(key, _tickCounter);
                        SeenFirstTick.TryGetValue(key, out firstTick);

                        StringBuilder sb = new StringBuilder();
                        sb.Append("{\"t\":\"exception\"");
                        sb.Append(",\"seq\":").Append(_totalSeen);
                        sb.Append(",\"tick\":").Append(firstTick);
                        // ⚠️ `Jw.Esc` **只转义、不加引号**（见 JsonlWriter.cs:160）。
                        //    踩过一次：漏了外层引号 ⇒ 输出 `"type":System.IO.IOException`
                        //    这种**畸形 JSON**（IPC 侧解析会失败）。
                        //    本项目的正确写法见 `TelemetryBehavior.cs:92`：引号手写。
                        sb.Append(",\"type\":\"").Append(Jw.Esc(en.TypeName ?? "")).Append('"');
                        sb.Append(",\"message\":\"").Append(Jw.Esc(Truncate(en.Message, 400) ?? "")).Append('"');
                        sb.Append(",\"stackHead\":\"").Append(Jw.Esc(Truncate(en.StackHead, 300) ?? "")).Append('"');
                        if (!string.IsNullOrEmpty(en.ThreadName))
                        {
                            sb.Append(",\"thread\":\"").Append(Jw.Esc(en.ThreadName)).Append('"');
                        }
                        sb.Append('}');
                        // ★ 写**侧信道**（常开），不是战斗日志（只在 mission 期间开）
                        //   —— 异常在主菜单/加载期也会发生，那时战斗日志是关的。
                        Jw.WriteSide(sb.ToString());
                        written++;
                    }
                }
                catch
                {
                    // 单条写失败不该中断排空（其余记录仍要出去）
                }
            }
            return written;
        }

        /// <summary>
        /// 统计快照（给 `bl_status` / 新工具用）。**如实报告"丢了多少"** —— 不静默。
        /// </summary>
        internal static string Summary()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"installed\":").Append(_installed ? "true" : "false");
            sb.Append(",\"seen\":").Append(Interlocked.Read(ref _totalSeen));
            sb.Append(",\"distinct\":").Append(SeenCounts.Count);
            // ⚠️ `_queued` 是 `int` ⇒ `Interlocked.Read` 只接受 `ref long`
            //    （编译错误 CS1503）。用 `CompareExchange(ref v, 0, 0)` 原子读 int 的惯用法。
            sb.Append(",\"queued\":").Append(Interlocked.CompareExchange(ref _queued, 0, 0));
            sb.Append(",\"dropped\":").Append(Interlocked.Read(ref _dropped));
            if (Interlocked.Read(ref _dropped) > 0)
            {
                sb.Append(",\"warning\":\"").Append(Jw.Esc(
                    "有 " + Interlocked.Read(ref _dropped) + " 条异常因队列满被丢弃（异常风暴）—— "
                    + "计数仍然准确，但**明细不全**")).Append('"');
            }
            // 出现次数 Top 10（帮"一眼看出哪个异常在刷屏"）
            try
            {
                List<KeyValuePair<string, int>> list = new List<KeyValuePair<string, int>>(SeenCounts);
                list.Sort(delegate(KeyValuePair<string, int> a, KeyValuePair<string, int> b)
                {
                    return b.Value.CompareTo(a.Value);
                });
                sb.Append(",\"top\":[");
                for (int i = 0; i < list.Count && i < 10; i++)
                {
                    if (i > 0) sb.Append(',');
                    string key = list[i].Key;
                    int bar = key.IndexOf('|');
                    string type = bar > 0 ? key.Substring(0, bar) : key;
                    string head = bar > 0 ? key.Substring(bar + 1) : "";
                    sb.Append("{\"type\":\"").Append(Jw.Esc(type)).Append('"');
                    sb.Append(",\"count\":").Append(list[i].Value);
                    if (!string.IsNullOrEmpty(head))
                    {
                        sb.Append(",\"stackHead\":\"").Append(Jw.Esc(Truncate(head, 200))).Append('"');
                    }
                    sb.Append('}');
                }
                sb.Append(']');
            }
            catch { }
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>复位统计（新一轮实验前清账；**不卸载订阅**）。</summary>
        internal static void Reset()
        {
            try
            {
                SeenCounts.Clear();
                SeenFirstTick.Clear();
                Entry dummy;
                while (Pending.TryDequeue(out dummy)) { }
                Interlocked.Exchange(ref _queued, 0);
                Interlocked.Exchange(ref _totalSeen, 0);
                Interlocked.Exchange(ref _dropped, 0);
            }
            catch { }
        }

        // ─────────────────────────────────────────────────────────────────
        // ★ 补丁失败分析（把 HarmonyException 从「现象」变成「点名」）
        // ─────────────────────────────────────────────────────────────────
        //
        // ## 为什么需要它
        //
        // 2026-10-06 实测抓到过：
        //   `HarmonyLib.HarmonyException: Ambiguous match for HarmonyMethod[(
        //      class=TaleWorlds.CampaignSystem.CharacterDevelopment.TraitLevelingHelper,
        //      methodname=OnIssueSolvedThroughQuest, type=Normal, args=undefined)]`
        //
        // 光有这条**还不够**——它只说"**有个补丁没打上**"，没说"**是谁**"。
        // 而 `bl_patches` 回答的是"**谁补了哪个方法**"，它**只会显示这个方法没被补**。
        //
        // ⇒ 两座桥**合起来**才能得出结论：
        //   ① 异常说「`TraitLevelingHelper.OnIssueSolvedThroughQuest` 重载歧义、补丁失败」；
        //   ② 把 `TraitLevelingHelper` 丢给 `bl_patches` ⇒ 看到
        //      `AddTraitXp ← mendo.governorsgonnagov / MendoMods.GovernorsGonnaGovern.Patches.TraitLevelingHelperPatch`
        //   ③ ⇒ **点名 `GovernorsGonnaGovern` 这个 mod 的一个补丁失败了**（同类的邻居方法在同一个补丁类里）。
        //
        // ## 消息格式（反编译确认，非推测）
        //
        //   `Harmony.cs:6028`  throw new HarmonyException("Ambiguous match for HarmonyMethod[" + attr.Description() + "]", ...)
        //   `attr.Description()` ⇒ `(class=<类型>, methodname=<方法>, type=Normal, args=<参数或不写>)`
        //
        // ⚠️ **本方法只做"提取与呈现"，不做"归因断言"**：
        //    message 里给的是**补丁想补的目标**，**不是**发起补丁的 mod（那个信息不在异常里）。
        //    所以 `suspectModules` 是**线索**（从同类型的其它方法反推），
        //    必须**显式标注为推测**，不能当成事实。

        /// <summary>解析一条 `HarmonyException` 的 message，提取目标类型/方法。解析不出来返回 null。</summary>
        private static void ParsePatchFailure(string message,
            out string targetClass, out string targetMethod)
        {
            // ★ 实现已抽到 `HarmonyMessage`（纯 BCL）—— 那样才能被 `tools/jsontest` **离线单测**。
            //   这里的薄包装只是为了让本文件的调用点读起来不变。
            HarmonyMessage.ParsePatchFailure(message, out targetClass, out targetMethod);
        }

        /// <summary>
        /// 补丁失败清单（从**已见到的**异常里筛 `HarmonyException` 并解析目标）。
        ///
        /// ★ 返回里对每条都带 `targetClass`/`targetMethod`（**异常直接给的**）
        ///   与 `note`（说明"谁发起的补丁**不在异常里**，要用 bl_patches 按 targetClass 反查"）。
        /// </summary>
        internal static string PatchFailures()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"ok\":true");
            sb.Append(",\"installed\":").Append(_installed ? "true" : "false");

            // ★★ 按 **(targetClass, targetMethod)** 归并 —— 这一步是必须的，不是美化。
            //
            // 实测（2026-10-06）踩到：**同一个补丁失败会被记成两条**，因为 Harmony
            //   ① 先在 `PatchTools.GetOriginalMethod` 抛一次；
            //   ② 然后 `PatchClassProcessor.ReportException` **包装后再抛一次**。
            // 而我们的去重键是 `(类型 + 栈首帧)` ⇒ 两条的栈首帧不同 ⇒ 各记一次，
            // 输出成 `failureCount: 2`、每条 `count: 1`
            // ⇒ **会让人误以为"有 2 个补丁失败了"**（其实只有 1 个）。
            // ⇒ 必须按**语义键**（目标类型+方法）归并，并把两个栈位置一并报出来。
            Dictionary<string, int> byTarget = new Dictionary<string, int>();
            Dictionary<string, string> firstMsg = new Dictionary<string, string>();
            Dictionary<string, List<string>> heads = new Dictionary<string, List<string>>();

            int rawRecords = 0;
            try
            {
                foreach (KeyValuePair<string, string> kv in _patchFailureMessages)
                {
                    string key = kv.Key;
                    string msg = kv.Value;
                    int cnt = 0;
                    SeenCounts.TryGetValue(key, out cnt);
                    rawRecords += cnt;

                    string cls, meth;
                    ParsePatchFailure(msg, out cls, out meth);
                    // 解析不出目标时退回用整条 message 当键（不丢信息）
                    string tkey = !string.IsNullOrEmpty(cls)
                        ? cls + "::" + (meth ?? "")
                        : msg;

                    int prev;
                    if (byTarget.TryGetValue(tkey, out prev)) byTarget[tkey] = prev + cnt;
                    else byTarget[tkey] = cnt;
                    if (!firstMsg.ContainsKey(tkey)) firstMsg[tkey] = msg;

                    // 该失败出现过的**栈首帧**（说明"抛了几次/在哪抛"，是给读者的细节）
                    int bar = key.IndexOf('|');
                    string head = bar >= 0 ? key.Substring(bar + 1) : "";
                    if (!string.IsNullOrEmpty(head))
                    {
                        List<string> list;
                        if (!heads.TryGetValue(tkey, out list)) { list = new List<string>(); heads[tkey] = list; }
                        if (!list.Contains(head)) list.Add(head);
                    }
                }
            }
            catch { }

            List<string> items = new List<string>();
            foreach (KeyValuePair<string, int> kv in byTarget)
            {
                string tkey = kv.Key;
                string msg = firstMsg[tkey];
                string cls = null, meth = null;
                ParsePatchFailure(msg, out cls, out meth);

                StringBuilder b = new StringBuilder();
                b.Append('{');
                if (!string.IsNullOrEmpty(cls))
                {
                    b.Append("\"targetClass\":\"").Append(Jw.Esc(cls)).Append('"');
                    b.Append(",\"targetMethod\":\"").Append(Jw.Esc(meth ?? "")).Append('"');
                }
                b.Append(",\"occurrences\":").Append(kv.Value);
                // 折叠了几个原始记录（>1 说明 Harmony 抛了不止一次 —— 正常，见上面注释）
                List<string> hl;
                if (heads.TryGetValue(tkey, out hl) && hl.Count > 1)
                {
                    b.Append(",\"throwSites\":[");
                    for (int i = 0; i < hl.Count; i++)
                    {
                        if (i > 0) b.Append(',');
                        b.Append('"').Append(Jw.Esc(Truncate(hl[i], 200))).Append('"');
                    }
                    b.Append(']');
                }
                b.Append(",\"message\":\"").Append(Jw.Esc(Truncate(msg, 500))).Append('"');
                b.Append('}');
                items.Add(b.ToString());
            }

            sb.Append(",\"failureCount\":").Append(items.Count);
            sb.Append(",\"rawRecords\":").Append(rawRecords);
            sb.Append(",\"note\":\"").Append(Jw.Esc(
                "targetClass/targetMethod 是**异常消息直接给出的「补丁想补的目标」**；"
                + "**发起该补丁的 mod 不在异常里**（Harmony 的 HarmonyException 只带目标描述）。"
                + "要定位到 mod，请把 targetClass 交给 bl_patches 按类型反查"
                + "（同类型的其它方法上会看到 owner）。"
                + "★ 结果已按 (targetClass, targetMethod) **归并** —— 同一个失败 Harmony 会抛两次"
                + "（GetOriginalMethod 一次、PatchClassProcessor 包装后再一次），"
                + "不归并会误报成「两个补丁失败」。")).Append('"');
            sb.Append(",\"failures\":[").Append(string.Join(",", items.ToArray())).Append(']');
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>
        /// 首见的补丁失败 message（键 = SeenCounts 的同一个 key）。
        /// ★ 为什么要单独留：`SeenCounts` 只存**计数**，为省内存没存 message；
        ///   而"谁失败了"必须靠 message（目标类型/方法在里面）。
        /// </summary>
        private static readonly ConcurrentDictionary<string, string> _patchFailureMessages =
            new ConcurrentDictionary<string, string>();

        private static string Truncate(string s, int max)
        {
            if (string.IsNullOrEmpty(s)) return s;
            if (s.Length <= max) return s;
            return s.Substring(0, max) + "…";
        }
    }
}
