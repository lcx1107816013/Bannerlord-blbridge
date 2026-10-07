using System;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace BlBridge
{
    /// <summary>
    /// B2：**自己落 minidump**，显式带 `MiniDumpWithFullMemoryInfo(0x800)`。
    ///
    /// ## 解决什么
    ///
    /// WER 落的 dump **缺 `MemoryInfoList`**（实测 **0/6 份**有）。
    /// 而 `MemoryInfoList` 提供的是**内存区间的页保护（r/w/x）** ——
    /// 区分"模块外那段内存"是 **JIT 生成的代码** 还是 **Harmony detour**
    /// 正是看页属性。这两个结论完全不同（前者引擎/CLR 问题，后者是我们改的 mod 问题）。
    ///
    /// 实测 WER 的 `flags = 0x200121`，**只差 `0x800` 那一位**；
    /// 本模块传 `0x201921` ⇒ `MemoryInfoList` 从 **0/6 → 5/5**（探针实测）。
    ///
    /// ## ★ 成本（实测，推翻了我最初的顾虑）
    ///
    /// 同一进程、同一时刻只差 flag 逐个量：
    ///
    ///   | flags | 大小 | 耗时 | 有 MemoryInfoList |
    ///   |---|---|---|---|
    ///   | 无 `0x800`（= WER 现状档） | 1.95 MB | 36 ms | ❌ |
    ///   | **加 `0x800`（本模块档）** | **1.97 MB** | **33 ms** | ✅ |
    ///   | 再加 `FullMemory(0x2)` | **49.17 MB** | 86 ms | ✅ |
    ///
    /// ⇒ **`0x800` 只多 0.02 MB / 0 ms**（`MemoryInfoList` 流本身只有 ~17 KB，
    ///   记的是**页保护元数据**，不是内存内容）；**真正贵的是 `FullMemory`，而本模块不要它**。
    ///   给一个已吃 600 MB 内存的进程写，仍是 **2.0 MB / 0.04 s**。
    ///
    /// ## 两条实测约束（决定了本类的形状）
    ///
    /// 1. ★★ **两个钩子缺一不可**。探针实测四条崩溃路径：
    ///
    ///      | 崩溃性质 | `AppDomain.UnhandledException` | `SetUnhandledExceptionFilter` |
    ///      |---|---|---|
    ///      | 托管异常 | ✅ | ✅ |
    ///      | AV（被 CLR 包成托管异常） | ✅ | ✅ |
    ///      | **纯 SEH**（`RaiseException`） | ❌ **不触发** | ✅ **触发** |
    ///      | `Environment.FailFast` | ❌ | ❌ |
    ///
    ///    ⇒ **只装托管钩子会漏掉纯 SEH 原生崩溃**（而那正是 6 份真实 dump 里的 3 份）。
    /// 2. **同一次崩溃会被两个钩子各调一次** ⇒ 必须**去重**（探针实测会落 2 份）。
    ///    去重手段：`Interlocked` 抢一个 0/1 标志。
    ///
    /// ## 边界（如实，不许宣称全覆盖）
    ///
    /// * **`Environment.FailFast`（`0xC0000409`）两条钩子都不触发** ——
    ///   而它占真实 dump 的 **3/6**（`48356` 那份就是 `ucrtbase!abort`）。
    ///   那类**继续走 WER，没有页保护**。报告里必须写明这一点。
    /// * `MiniDumpWriteDump` 成功时也可能留下**陈旧的上一次错误码**
    ///   （探针实测 `ok=True` 而 `GetLastError=6`）⇒ **判据只能看返回值**。
    /// * 崩溃回调里做 I/O 本身有风险（堆可能已损坏）。官方**推荐在独立进程里**做，
    ///   而我们只能进程内做 ⇒ 全部 try/catch 兜住，**绝不让它影响进程退出**。
    /// * 本模块**只写 dump，不做任何分析**（分析归宿主的 `bl_crash`）。
    ///
    /// ## 与 `ExceptionProbe` 的关系
    ///
    /// **不冲突**：那边订阅 `FirstChanceException`（每次抛出都触发，用于**统计**），
    /// 本类订阅 `UnhandledException` + Win32 顶层过滤器（**只在进程要死时**触发一次）。
    /// 两者事件不同、时机不同、落盘位置也不同。
    /// </summary>
    internal static class CrashDump
    {
        // ── MINIDUMP_TYPE 位（来自 dbghelp.h）─────────────────────────────
        private const uint MiniDumpNormal = 0x00000000;
        private const uint MiniDumpWithDataSegs = 0x00000001;
        private const uint MiniDumpWithFullMemory = 0x00000002;          // ★ 本模块**不用**（49 MB）
        private const uint MiniDumpWithUnloadedModules = 0x00000020;
        private const uint MiniDumpWithProcessThreadData = 0x00000100;
        private const uint MiniDumpWithFullMemoryInfo = 0x00000800;      // ★★ 目标位：页保护
        private const uint MiniDumpWithThreadInfo = 0x00001000;

        /// <summary>本模块固定使用的档位（实测 = `0x1921`）。</summary>
        internal const uint Flags = MiniDumpNormal
                                  | MiniDumpWithDataSegs
                                  | MiniDumpWithUnloadedModules
                                  | MiniDumpWithProcessThreadData
                                  | MiniDumpWithFullMemoryInfo     // ★ 有它才有 MemoryInfoList
                                  | MiniDumpWithThreadInfo;

        /// <summary>可选附加位：只有显式要求才加（体积 +25 倍）。</summary>
        internal const uint FlagsWithFullMemory = Flags | MiniDumpWithFullMemory;

        // ── P/Invoke ──────────────────────────────────────────────────────
        //
        // ⚠️ `dbghelp.dll` 刻意**不写死绝对路径**：让 Windows 按常规搜索顺序解析。
        //    本机有两份（游戏 bin 的 10.0.18362.1 / System32 的 10.0.26100.9549），
        //    实际加载哪份取决于搜索顺序 —— **这一条尚未定论**，已登记在交接文档里。
        [DllImport("dbghelp.dll", SetLastError = true)]
        private static extern bool MiniDumpWriteDump(
            IntPtr hProcess, uint processId, IntPtr hFile, uint dumpType,
            IntPtr exceptionParam, IntPtr userStreamParam, IntPtr callbackParam);

        [DllImport("kernel32.dll")]
        private static extern IntPtr GetCurrentProcess();

        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentProcessId();

        [DllImport("kernel32.dll")]
        private static extern IntPtr SetUnhandledExceptionFilter(IntPtr lpTopLevelExceptionFilter);

        [DllImport("kernel32.dll")]
        private static extern void RaiseException(uint dwExceptionCode, uint dwExceptionFlags,
            uint nNumberOfArguments, IntPtr lpArguments);

        /// <summary>Win32 顶层异常过滤器的签名（`LPTOP_LEVEL_EXCEPTION_FILTER`）。</summary>
        private delegate int TopLevelFilterDelegate(IntPtr exceptionPointers);

        // 委托必须**保活**，否则被 GC 回收后回调会跳到野地址
        private static TopLevelFilterDelegate _filterKeepAlive;

        // ── 状态 ──────────────────────────────────────────────────────────
        private static int _installed;          // 0/1：是否已安装
        private static int _dumpTaken;          // ★ 去重：0=未落，1=已落
        private static string _dir = "";        // dump 目录
        private static int _keepFiles = 10;     // 保留份数

        /// <summary>最近一次写 dump 的一句话结果（给 `status` 与报告用）。</summary>
        internal static string LastResult = "";
        internal static string LastPath = "";
        internal static int DumpCount;

        /// <summary>诊断块：让外部看清"装没装、落了几份、上次结果"。</summary>
        internal static string StatusJson()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"installed\":").Append(Jw.B(_installed == 1));
            sb.Append(",\"dir\":\"").Append(Jw.Esc(_dir)).Append('"');
            sb.Append(",\"keepFiles\":").Append(Jw.N(_keepFiles));
            sb.Append(",\"dumpsTaken\":").Append(Jw.N(DumpCount));
            sb.Append(",\"flags\":\"0x").Append(Flags.ToString("X", CultureInfo.InvariantCulture)).Append('"');
            sb.Append(",\"crashTestAllowed\":").Append(Jw.B(CrashTestAllowed));
            sb.Append(",\"lastPath\":\"").Append(Jw.Esc(LastPath)).Append('"');
            sb.Append(",\"lastResult\":\"").Append(Jw.Esc(LastResult)).Append('"');
            sb.Append(",\"note\":\"").Append(Jw.Esc(
                "只覆盖「托管异常」与「纯 SEH 原生崩溃」；" +
                "Environment.FailFast(0xC0000409) 两条钩子都不触发 ⇒ 那类继续走 WER（无页保护）")).Append('"');
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>
        /// 安装两个钩子。幂等；失败**不抛**（崩溃诊断不该拖垮游戏启动）。
        ///
        /// **两个都必须装**（见类注释的实测矩阵）。
        /// </summary>
        internal static void Install(string dir, int keepFiles)
        {
            if (Interlocked.CompareExchange(ref _installed, 1, 0) != 0)
            {
                return;                     // 已装过
            }
            try
            {
                _dir = dir ?? "";
                if (!string.IsNullOrEmpty(_dir))
                {
                    Directory.CreateDirectory(_dir);
                }
                if (keepFiles > 0)
                {
                    _keepFiles = keepFiles;
                }

                // ① 托管未处理异常（覆盖"托管异常"与"被 CLR 包装的原生异常"）
                AppDomain.CurrentDomain.UnhandledException += OnManagedUnhandled;

                // ② Win32 顶层异常过滤器（覆盖**纯 SEH** —— 托管钩子对它不触发）
                _filterKeepAlive = OnTopLevelFilter;
                SetUnhandledExceptionFilter(
                    Marshal.GetFunctionPointerForDelegate(_filterKeepAlive));

                LastResult = "installed";
            }
            catch (Exception ex)
            {
                // 安装失败要留痕，但**不能**抛（抛了就是模块加载失败 = 游戏起不来）
                LastResult = "install_failed: " + ex.GetType().Name + ": " + ex.Message;
            }
        }

        /// <summary>托管未处理异常（进程即将终止）。</summary>
        private static void OnManagedUnhandled(object sender, UnhandledExceptionEventArgs e)
        {
            // ⚠️ 这条路径**拿不到** Win32 的 `EXCEPTION_POINTERS` ⇒
            //    落出的 dump **没有 `Exception(6)` 流**（真机实测：于是
            //    `bl_crash` 只能说"无异常流 —— 无法归因"）。
            //    这是**托管钩子相对 Win32 钩子的固有劣势**，不是 bug；
            //    要它也能带 `Exception` 流，得在托管异常里手工构造
            //    `MINIDUMP_EXCEPTION_INFORMATION`（成本高、且托管异常本就有
            //    别的采集通道 —— `ExceptionProbe` 的 `exceptions.jsonl`）。
            //    ⇒ 取舍：**托管路径留空，原生路径带异常**（见 `OnTopLevelFilter`）。
            //
            // ⚠️ 注意：**异常队列的排空不在这里**，而在 `WriteOnce`（两个钩子的公共入口）。
            //    原因见那里的注释：真机实测发现这条托管路径**常常根本不被调用**
            //    （CLR 异常 `0xE0434352` 走的是 Win32 顶层过滤器），
            //    把排空挂在这里等于没挂。
            WriteOnce("managed", IntPtr.Zero);
        }

        /// <summary>Win32 顶层异常过滤器（原生崩溃）。返回 0 = EXCEPTION_EXECUTE_HANDLER。</summary>
        private static int OnTopLevelFilter(IntPtr exceptionPointers)
        {
            // ★ 关键：把 `exceptionPointers` **传下去**。
            //   `MiniDumpWriteDump` 的第 5 参（`ExceptionParam`）收的就是它 ——
            //   传了才有 `Exception(6)` 流（含异常码 / 故障地址 / 线程）。
            //   ⚠️ 这是真机验收发现的缺陷：第一版传 `IntPtr.Zero`，
            //      落出的 dump **没有 `Exception(6)`**，于是 `bl_crash` 对
            //      崩溃 dump 也只能报"无异常流 —— 无法归因"，
            //      等于**白丢了最关键的崩溃签名**（而 WER 的 dump 有它）。
            WriteOnce("native", exceptionPointers);
            return 0;
        }

        /// <summary>
        /// ★ 去重的唯一入口：两个钩子对同一次崩溃都会调到这里。
        ///
        /// 用 `Interlocked.CompareExchange` 抢标志（崩溃可能来自任意线程），
        /// 抢到的那个才写 dump；另一个直接返回。
        /// （探针实测不去重时同一次崩会落 **2 份**。）
        /// </summary>
        private static void WriteOnce(string trigger, IntPtr exceptionPointers)
        {
            if (Interlocked.CompareExchange(ref _dumpTaken, 1, 0) != 0)
            {
                return;                     // 另一个钩子已经在写了
            }
            // ★★ v0.8.51：**在写 dump 之前，先把异常队列同步排空**。
            //
            // 为什么放这里（真机两次实测，2026-10-07）：
            //   `ExceptionProbe` 是"FirstChance 入队 + **主线程 tick 排空**"两段式。
            //   托管异常无人 catch 时进程立刻终结，主线程再也不会 tick
            //   ⇒ 队列里最后那条（**真正致命的那条**）永远不落盘
            //   ⇒ 用户看到"游戏崩了，日志里什么都没有"，无法归因。
            //
            // ⚠️ **第一次我把排空挂在 `OnManagedUnhandled` 上，没用** ——
            //    真机证据：dump 文件名是 `blbridge-native-*`，且
            //    `exceptionCode=0xE0434352`（CLR 托管异常）由 **Win32 SEI 顶层过滤器**触发，
            //    那个钩子**根本没被调用**。⇒ 挂错钩子 = 修复无效，而且**看起来像已修**。
            //    `WriteOnce` 才是**两个钩子唯一的公共入口**、且由 Interlocked 保证只跑一次，
            //    所以排空放这里才覆盖全部路径。
            //
            // ⚠️ 只能同步写（此刻不能指望"下一帧再排"）；量给足（每帧上限是 64，这里 4096）。
            // ⚠️ 整体 try：排空失败绝不影响 dump（dump 是更重要的证据）。
            try
            {
                ExceptionProbe.Drain(4096);
            }
            catch
            {
            }
            try
            {
                if (trigger == "native" && exceptionPointers != IntPtr.Zero)
                {
                    WriteWithException(trigger, exceptionPointers);
                }
                else
                {
                    Write(trigger);
                }
            }
            catch (Exception ex)
            {
                try
                {
                    LastResult = "write_failed: " + ex.GetType().Name + ": " + ex.Message;
                }
                catch
                {
                }
            }
        }

        /// <summary>
        /// `MINIDUMP_EXCEPTION_INFORMATION`。
        ///
        /// ★★ **必须 `Pack = 4`** —— 这是本项目 2026-10-06 花了大半天的坑，
        ///    最后是**网上一条 issue** 一句话点破的（不是我们琢磨出来的）：
        ///    [Incorrect packing used for _MINIDUMP_EXCEPTION_INFORMATION · Leandros/WindowsHModular#33](https://github.com/Leandros/WindowsHModular/issues/33)
        ///
        ///    > MiniDump structs should be 4 bytes aligned (with eg. `#pragma pack(4)`)
        ///    > …This causes `MiniDumpWriteDump` to fail when passing it a "bad"
        ///    > `_MINIDUMP_EXCEPTION_INFORMATION`.
        ///
        ///    （同一 issue 指出 Odin 也踩过：[odin-lang/Odin#4407](https://github.com/odin-lang/Odin/issues/4407)。）
        ///
        /// ## 为什么默认对齐会炸
        ///
        ///   C 定义：`DWORD ThreadId; PEXCEPTION_POINTERS ExceptionPointers; BOOL ClientPointers;`
        ///
        ///   | 对齐 | `Marshal.SizeOf` | `ExceptionPointers` 偏移 | 结果 |
        ///   |---|---|---|---|
        ///   | **`Pack=4`（正确）** | **16** | **4** | ✅ 成功，且**带 `Exception(6)` 流** |
        ///   | 默认（8） | 24 | 8 | 💥 `dbghelp` **内部访问违规** |
        ///
        ///   默认对齐下指针在 offset 8，而 `dbghelp` 按 `pack(4)` 的约定**从 offset 4 读指针**
        ///   ⇒ 读到 `ThreadId` 之后那 4 字节填充 + 指针低半部拼出来的**垃圾指针**
        ///   ⇒ 它自己去解引用 ⇒ **访问违规**。
        ///   ⇒ 完美解释"传 `NULL` 就好、传真指针就崩"这个现象。
        ///
        /// ⚠️ **教训**：我先用 6 个探针**逐个排除了九种可能**
        ///   （flags / ClientPointers / ThreadId / 分配位置 / 指针有效性 /
        ///    参数畸形 / dbghelp 版本 / 崩溃来源）—— 全是白费功夫。
        ///   **网上搜一次就有答案**。⇒ 遇到这类"平台 API 行为诡异"，
        ///   **先搜再试**：几十亿人踩过的坑，不该由我们从零琢磨。
        /// </summary>
        [StructLayout(LayoutKind.Sequential, Pack = 4)]
        private struct MiniDumpExceptionInformation
        {
            public uint ThreadId;
            public IntPtr ExceptionPointers;
            public int ClientPointers;      // BOOL
        }

        /// <summary>
        /// 带**异常信息**写 dump ⇒ 产出 `Exception(6)` 流。**永不抛。**
        ///
        /// ★ 关键在于结构体必须 [`Pack = 4`](https://github.com/Leandros/WindowsHModular/issues/33)：
        ///   默认对齐（24 字节 / 指针 @8）会让 `dbghelp` **从 offset 4 读出垃圾指针**
        ///   然后自己去解引用 ⇒ **它内部访问违规** ⇒ dump 变 **0 字节**。
        ///   修成 `Pack=4`（16 字节 / 指针 @4）后：**成功，且 `Exception(6)` 流存在**
        ///   （真机 + 探针实测）。
        ///
        /// ⚠️ 结构体必须**固定在非托管内存**（`Marshal.AllocHGlobal`）——
        ///    GC 可能在 `MiniDumpWriteDump` 读它之前搬走托管对象。
        ///
        /// ★ 另外**仍写一份 sidecar**：`Exception(6)` 流与 sidecar 是**互为冗余**的两条路
        ///   （曾因结构体对齐问题完全拿不到 `Exception(6)`；sidecar 那条当时是唯一可行的）。
        ///   留着它能防"将来 dbghelp 行为又变"。
        /// </summary>
        private static void WriteWithException(string trigger, IntPtr exceptionPointers)
        {
            // 自己读一份异常信息（冗余通道；也是当年唯一的通道）
            string sidecar = ReadExceptionPointers(exceptionPointers);

            int size = Marshal.SizeOf(typeof(MiniDumpExceptionInformation));
            IntPtr buf = Marshal.AllocHGlobal(size);
            string path;
            try
            {
                MiniDumpExceptionInformation info;
                info.ThreadId = GetCurrentThreadId();
                info.ExceptionPointers = exceptionPointers;
                info.ClientPointers = 0;    // 0 = 指针属于**本进程**（不是跨进程 dump）
                Marshal.StructureToPtr(info, buf, false);

                // ★ 传真指针 ⇒ 有 Exception(6) 流（Pack=4 是关键，见结构体注释）
                path = Write(trigger, buf);

                // 若带异常信息反而失败/落空 ⇒ 退一步用 NULL 再试一次（别丢 dump）
                if (string.IsNullOrEmpty(path) || new FileInfo(path).Length < 32)
                {
                    LastResult += " retry_without_exception";
                    path = Write(trigger, IntPtr.Zero);
                }
            }
            finally
            {
                Marshal.FreeHGlobal(buf);
            }

            // 冗余落 sidecar（bl_crash 会在没有 Exception(6) 时回退读它）
            if (!string.IsNullOrEmpty(path) && !string.IsNullOrEmpty(sidecar))
            {
                try
                {
                    File.WriteAllText(path + ".exception.json", sidecar,
                        new UTF8Encoding(false));
                    LastResult += " sidecar=ok";
                }
                catch (Exception ex)
                {
                    LastResult += " sidecar_failed=" + ex.GetType().Name;
                }
            }
        }

        /// <summary>
        /// 自己解引用 `EXCEPTION_POINTERS`，返回 JSON（失败返回 null）。
        ///
        /// 布局（x64，来自 winnt.h）：
        ///   `EXCEPTION_POINTERS`  +0 = `PEXCEPTION_RECORD`，+8 = `PCONTEXT`
        ///   `EXCEPTION_RECORD`    +0 = `ExceptionCode`(DWORD)，+4 = `ExceptionFlags`(DWORD)，
        ///                        +16 = `ExceptionAddress`(PVOID)，+24 = `NumberParameters`(DWORD)，
        ///                        +32 = `ExceptionInformation[15]`
        ///   `CONTEXT`             `Rip` 在 +0xF8
        ///
        /// **永不抛**：崩溃回调里的任何异常都不能再放大。
        /// </summary>
        private static string ReadExceptionPointers(IntPtr excPtrs)
        {
            try
            {
                if (excPtrs == IntPtr.Zero) return null;

                IntPtr rec = Marshal.ReadIntPtr(excPtrs, 0);
                IntPtr ctx = Marshal.ReadIntPtr(excPtrs, 8);
                if (rec == IntPtr.Zero) return null;

                uint code = (uint)Marshal.ReadInt32(rec, 0);
                uint eflags = (uint)Marshal.ReadInt32(rec, 4);
                IntPtr addr = Marshal.ReadIntPtr(rec, 16);
                uint nparams = (uint)Marshal.ReadInt32(rec, 24);

                // Rip（CONTEXT + 0xF8）：能让 bl_crash 知道"当时在哪条指令"
                IntPtr rip = IntPtr.Zero;
                try { if (ctx != IntPtr.Zero) rip = Marshal.ReadIntPtr(ctx, 0xF8); }
                catch { }

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"exceptionCode\":\"0x")
                  .Append(code.ToString("X8", CultureInfo.InvariantCulture)).Append('"');
                sb.Append(",\"exceptionAddress\":\"0x")
                  .Append(addr.ToInt64().ToString("X", CultureInfo.InvariantCulture)).Append('"');
                sb.Append(",\"exceptionFlags\":\"0x")
                  .Append(eflags.ToString("X", CultureInfo.InvariantCulture)).Append('"');
                sb.Append(",\"numberParameters\":").Append(nparams.ToString(CultureInfo.InvariantCulture));
                if (rip != IntPtr.Zero)
                {
                    sb.Append(",\"contextRip\":\"0x")
                      .Append(rip.ToInt64().ToString("X", CultureInfo.InvariantCulture)).Append('"');
                }
                // 访问违规的两个参数：读/写 与 目标地址
                if (nparams >= 2)
                {
                    try
                    {
                        uint rw = (uint)Marshal.ReadInt32(rec, 32);
                        IntPtr tgt = Marshal.ReadIntPtr(rec, 32 + IntPtr.Size);
                        sb.Append(",\"accessType\":\"").Append(rw == 0 ? "read" : "write").Append('"');
                        sb.Append(",\"accessTarget\":\"0x")
                          .Append(tgt.ToInt64().ToString("X", CultureInfo.InvariantCulture)).Append('"');
                    }
                    catch { }
                }
                sb.Append(",\"source\":\"BlBridge.CrashDump.ReadExceptionPointers\"");
                sb.Append('}');
                return sb.ToString();
            }
            catch
            {
                return null;
            }
        }

        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentThreadId();

        /// <summary>真正写 dump。**永不抛**。返回落盘路径（失败返回空串）。</summary>
        private static string Write(string trigger)
        {
            return Write(trigger, IntPtr.Zero);
        }

        /// <summary>
        /// 真正写 dump。返回落盘路径（失败返回空串）。**永不抛。**
        ///
        /// `exceptionParam` = `MINIDUMP_EXCEPTION_INFORMATION*`（**必须 `Pack=4`**，
        /// 见该结构体的注释）⇒ 产出 `Exception(6)` 流；传 `IntPtr.Zero` ⇒ 没有该流。
        /// </summary>
        private static string Write(string trigger, IntPtr exceptionParam)
        {
            if (string.IsNullOrEmpty(_dir))
            {
                LastResult = "no_dir";
                return "";
            }

            string stamp = DateTime.UtcNow.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture);
            string name = string.Format(CultureInfo.InvariantCulture,
                "blbridge-{0}-pid{1}-{2}.dmp", trigger, Protocol.Pid, stamp);
            string path = Path.Combine(_dir, name);

            bool ok;
            int err;
            long size;
            long ms;
            try
            {
                using (FileStream fs = new FileStream(path, FileMode.Create,
                           FileAccess.Write, FileShare.None))
                {
                    DateTime t0 = DateTime.UtcNow;
                    // ⚠️ 直接传 SafeFileHandle —— **不要**用 C 运行时的 fn 号：
                    //    实测那样会失败（ok=False / 0 字节 / GetLastError=0），
                    //    因为 MiniDumpWriteDump 要的是真正的 Win32 HANDLE。
                    ok = MiniDumpWriteDump(GetCurrentProcess(), GetCurrentProcessId(),
                        fs.SafeFileHandle.DangerousGetHandle(), Flags,
                        exceptionParam, IntPtr.Zero, IntPtr.Zero);
                    err = Marshal.GetLastWin32Error();
                    ms = (long)(DateTime.UtcNow - t0).TotalMilliseconds;
                }
                size = new FileInfo(path).Length;
            }
            catch (Exception ex)
            {
                LastResult = "io_failed: " + ex.GetType().Name + ": " + ex.Message;
                return "";
            }

            DumpCount++;
            LastPath = path;
            // ★ 判据只有 `ok`：`GetLastError` 在成功时也可能留陈旧值（实测 6）。
            LastResult = string.Format(CultureInfo.InvariantCulture,
                "ok={0} trigger={1} bytes={2} ms={3} lastError={4}",
                ok, trigger, size, ms, err);

            Prune();
            return path;
        }

        /// <summary>只保留最近 N 份自己落的 dump（按写入时间）。失败静默。</summary>
        private static void Prune()
        {
            try
            {
                if (_keepFiles <= 0 || string.IsNullOrEmpty(_dir)) return;
                string[] files = Directory.GetFiles(_dir, "blbridge-*.dmp");
                if (files.Length <= _keepFiles) return;
                Array.Sort(files, delegate (string a, string b)
                {
                    return File.GetLastWriteTimeUtc(a).CompareTo(File.GetLastWriteTimeUtc(b));
                });
                int remove = files.Length - _keepFiles;
                for (int i = 0; i < remove; i++)
                {
                    try
                    {
                        File.Delete(files[i]);
                        // ★ 连带删同名 sidecar —— 否则留下"孤儿 json"，
                        //   而 `bl_crash` 会把它读成"有异常信息但 dump 没了"，
                        //   那是比没有 sidecar 更糟的状态（数据自相矛盾）。
                        string sc = files[i] + ".exception.json";
                        if (File.Exists(sc)) File.Delete(sc);
                    }
                    catch { }
                }
            }
            catch
            {
            }
        }

        /// <summary>
        /// **按需**写一份 dump（健康进程也能调）—— 不崩、零副作用。
        ///
        /// 为什么值得单独提供：实测**健康进程写出的 dump 同样带 `MemoryInfoList`**
        /// （2.0 MB / 36 ms，flags 与崩溃路径**完全相同**）⇒
        /// 它能覆盖 `bl_crash` 现在**结构上覆盖不到**的一类问题：
        /// **"卡住/僵死但进程还活着"**（那时既没有 WER dump，也还没崩）。
        ///
        /// 返回 null = 成功；非 null = 失败原因。**永不抛。**
        /// </summary>
        internal static string WriteNow(bool withFullMemory, out string path)
        {
            path = "";
            try
            {
                if (string.IsNullOrEmpty(_dir))
                {
                    return "dump 目录未设置（Install 未成功？）";
                }
                Directory.CreateDirectory(_dir);
                string stamp = DateTime.UtcNow.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture);
                string name = string.Format(CultureInfo.InvariantCulture,
                    "blbridge-ondemand-pid{0}-{1}.dmp", Protocol.Pid, stamp);
                path = Path.Combine(_dir, name);

                bool ok;
                int err;
                long ms;
                using (FileStream fs = new FileStream(path, FileMode.Create,
                           FileAccess.Write, FileShare.None))
                {
                    DateTime t0 = DateTime.UtcNow;
                    ok = MiniDumpWriteDump(GetCurrentProcess(), GetCurrentProcessId(),
                        fs.SafeFileHandle.DangerousGetHandle(),
                        withFullMemory ? FlagsWithFullMemory : Flags,
                        IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
                    err = Marshal.GetLastWin32Error();
                    ms = (long)(DateTime.UtcNow - t0).TotalMilliseconds;
                }
                long size = new FileInfo(path).Length;
                LastPath = path;
                LastResult = string.Format(CultureInfo.InvariantCulture,
                    "ondemand ok={0} bytes={1} ms={2} lastError={3} fullMemory={4}",
                    ok, size, ms, err, withFullMemory);
                if (!ok)
                {
                    return "MiniDumpWriteDump 返回 false（lastError=" + err + "）";
                }
                // 判据：文件必须非空且**真的**是 minidump（前 4 字节 'MDMP'）
                if (size < 32)
                {
                    return "写出的文件过小（" + size + " 字节），不是有效 dump";
                }
                using (FileStream fs2 = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    byte[] head = new byte[4];
                    int n = fs2.Read(head, 0, 4);
                    if (n != 4 || head[0] != (byte)'M' || head[1] != (byte)'D'
                        || head[2] != (byte)'M' || head[3] != (byte)'P')
                    {
                        return "写出的文件没有 MDMP 头，不是 minidump";
                    }
                }
                DumpCount++;
                Prune();
                return null;
            }
            catch (Exception ex)
            {
                return ex.GetType().Name + ": " + ex.Message;
            }
        }

        /// <summary>
        /// **受控崩溃**（验收用）：显式造一次**纯 SEH** 原生异常。
        ///
        /// ## 为什么用 `RaiseException` 而不是别的
        ///
        /// 探针实测四条路径的钩子触发矩阵：
        ///
        ///   | 造法 | 托管钩子 | Win32 钩子 |
        ///   |---|---|---|
        ///   | `RaiseException` | ❌ | ✅ |
        ///   | `throw` 托管异常 | ✅ | ✅ |
        ///   | `Environment.FailFast` | ❌ | ❌ |
        ///
        /// ⇒ **`RaiseException` 是最难的那条路**（托管钩子不触发）。
        ///   验它通过 ⇒ 更容易的托管路径自然也没问题。
        ///   而 `FailFast` 已知覆盖不到，验它没有意义。
        ///
        /// ## 安全闸门（缺一不可）
        ///
        ///   1. 必须由**控制通道 method 显式调用**（不接受默认触发）；
        ///   2. 必须设环境变量 `BLBRIDGE_ALLOW_CRASH_TEST=1`（**默认关**）。
        ///
        /// ⇒ 发布版**不可能**误崩。
        /// </summary>
        internal static bool CrashTestAllowed
        {
            get
            {
                try
                {
                    string v = Environment.GetEnvironmentVariable("BLBRIDGE_ALLOW_CRASH_TEST");
                    return !string.IsNullOrEmpty(v) && v.Trim() == "1";
                }
                catch
                {
                    return false;
                }
            }
        }

        /// <summary>造一次纯 SEH 崩溃（`0xC0000005`）。**调用后进程会死**，不返回。</summary>
        internal static void RaiseNativeCrash()
        {
            RaiseException(0xC0000005, 0, 0, IntPtr.Zero);
        }
    }
}
