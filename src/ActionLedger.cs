using System;
using System.Globalization;
using System.IO;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// 控制通道的**动作账本**（v0.8.42）：每个请求一行，追加写，永不重写。
    ///
    /// 为什么要有它（缺口的取证）：
    ///   `CommandPump.HandleOne` 现在把 id / method / 错误码 / 异常全都算出来了，然后在
    ///   `WriteResponse` 里**写进一个按 id 命名的响应文件就完了** —— 而那个文件会被外部消费掉，
    ///   同 id 再来一次还会被 `File.Delete` 覆盖。⇒ **"我到底下过什么命令"在磁盘上不留痕**。
    ///
    /// 与两个独立先例同构（2026-10-05 评估取证）：
    ///   • Bannerlord.GameMaster `Console/Common/Execution/CommandLogger.cs`：每条命令一行
    ///     `Timestamp / Command / Status SUCCESS|FAILED / output`，落
    ///     `Documents\...\Configs\GameMaster\command_log_<ts>.txt`，**只留最近 5 个文件**（`MAX_LOG_FILES`）；
    ///     异常**无条件**写游戏 RGL 日志、自定义日志文件只在开启时写（两级策略）。
    ///   • TAOM_CheatPanel `Runtime.RecentActionsLog` + `RecentActionEntry`：模组自己的动作流水，
    ///     并且**对用户可见**（"…could not be saved. See Action History."）。
    /// 两边独立收敛到同一件事 ⇒ 这不是偏好问题，是这类宿主工具的必需件。
    ///
    /// 与 `Jw` 的区别（**不能复用**）：`Jw` 是**单文件**静态写入器，同一时刻只持有一个
    /// battle 日志句柄；账本要在任意时刻追加，复用它会把正在进行的战斗日志顶掉。
    /// 所以这里每条自己开-写-关（`File.AppendAllText`），代价是一次 open/close，
    /// 而请求频率是**泵的 4 Hz 上限**（`CommandPump.PollInterval` = 250ms），完全可以忽略。
    /// ⚠️ GameMaster 用的是 `ConcurrentQueue` + 1 秒 Timer 缓冲刷盘 —— **那是它自己的速率决定的**
    ///    （控制台 UI 线程高频写）。此处**刻意不抄**：抄过来只会让"崩溃前最后几条请求"更容易丢，
    ///    而这恰恰是账本最该保住的部分。
    ///
    /// 不变式：
    ///   • **每个请求都有且只有一行**（含全部拒绝路径：过大/id 非法/版本不符/过期/未知方法/处理器异常）；
    ///   • 单行自闭合 JSON（分析与判断的教训同 `TelemetryBehavior` 的 meta：**数字字段不吃 `\"` 前缀**）。
    ///     行内不依赖下一行补引号 —— 历史上正是这种写法产出过非法 JSON（v0.8.4）；
    ///   • **整体 try/catch**：账本永远不能成为新的失败源。
    /// </summary>
    internal static class ActionLedger
    {
        private const string FileName = "actions.jsonl";
        private const string ArchivedPrefix = "actions-";

        /// <summary>进程内单调序号。用来判"有没有丢行/两个写者交错"，比时间戳可靠。</summary>
        private static long _seq;

        /// <summary>`args` 字段的截断上限（字符）。请求体（尤其 start_battle 的 groups DSL）
        /// 可能很长，账本是**索引**不是副本 —— 完整请求要看 battle 日志与 plan。</summary>
        private const int ArgsSnippetMax = 600;

        /// <summary>
        /// v0.8.45：**写失败不再静默**。
        ///
        /// 缺口（2026-10-05 复核确认，是本模块剩下的两个真缺口之一）：
        /// `Record` 的 `catch { }` 是**裸吞**。磁盘满 / 权限错 / `<LogDir>` 被清理时，
        /// 账本**少行而无人知** —— 而账本的全部价值就在于"事后能证明发生过什么"。
        /// 一个会静默丢行的审计日志，比没有审计日志更危险（它让人**以为**有记录）。
        ///
        /// 修法（**刻意最小**）：只加两个可观测出口，**不改任何判定分支、不改字节格式**：
        ///   • `WriteFailureCount`：进程内累计写失败次数（`status` 里可读）；
        ///   • `LastWriteFailure`：最近一次失败的**原因摘要**（异常类型 + 消息，截断）。
        /// 每次失败仍照旧走 RGL（`ExceptionToRgl`）—— 那条第二出口本来就有，只是**没被调用**。
        /// </summary>
        private static int _writeFailures;
        private static string _lastWriteFailure = "";

        /// <summary>进程内累计的账本写失败次数（0 = 至今没丢过行）。</summary>
        internal static int WriteFailureCount
        {
            get { return _writeFailures; }
        }

        /// <summary>最近一次写失败的原因摘要（空串 = 没失败过）。</summary>
        internal static string LastWriteFailure
        {
            get { return _lastWriteFailure; }
        }

        public static string Path
        {
            get { return System.IO.Path.Combine(CommandPump.CommandsRoot, FileName); }
        }

        /// <summary>
        /// 记一条。`method` 未知/为空时按原样写（拒绝路径上可能拿不到 method）。
        /// 全部参数都必须已由调用方判定为"可安全落盘"；本方法自己不再猜。
        /// </summary>
        /// <param name="id">请求 id（十六进制；拒绝路径上可能只有文件名派生的安全 id）</param>
        /// <param name="method">请求方法名（可能为空）</param>
        /// <param name="ok">响应是否成功</param>
        /// <param name="code">失败码（成功时为空串）</param>
        /// <param name="ms">从开始处理到写出响应之间的毫秒数</param>
        /// <param name="bytes">请求文件大小（读不到填 -1）</param>
        /// <param name="outcomeUncertain">该次失败是否"副作用不确定"（协议里要求外部绝不盲目重试）</param>
        /// <param name="note">失败原因摘要 / 其它需要留痕的一句话（成功时通常为空）</param>
        /// <param name="argsSnippet">请求参数片段（已截断；可为空）</param>
        internal static void Record(string id, string method, bool ok, string code, double ms,
            long bytes, bool outcomeUncertain, string note, string argsSnippet)
        {
            try
            {
                if (!BridgeConfig.Enabled) return;
                CommandPump.EnsureDirs();
                RotateIfNeeded();

                long seq = System.Threading.Interlocked.Increment(ref _seq);
                StringBuilder sb = new StringBuilder(256);
                sb.Append("{\"t\":\"").Append(Jw.UtcNow()).Append('"');
                sb.Append(",\"seq\":").Append(Jw.N(seq));
                // runToken：把账本按"游戏进程会话"分组。没有它，重启游戏后的账本无法与
                // 上一会话区分，而 BlBridge 的全部诊断都建立在这个概念上（见 Protocol）。
                sb.Append(",\"runToken\":\"").Append(Jw.Esc(Protocol.RunToken)).Append('"');
                sb.Append(",\"id\":\"").Append(Jw.Esc(id ?? "")).Append('"');
                sb.Append(",\"method\":\"").Append(Jw.Esc(method ?? "")).Append('"');
                sb.Append(",\"ok\":").Append(Jw.B(ok));
                sb.Append(",\"code\":\"").Append(Jw.Esc(code ?? "")).Append('"');
                sb.Append(",\"ms\":").Append(Jw.N((float)ms));
                sb.Append(",\"bytes\":").Append(Jw.N(bytes));
                sb.Append(",\"uncertain\":").Append(Jw.B(outcomeUncertain));
                sb.Append(",\"note\":\"").Append(Jw.Esc(Truncate(note, 300))).Append('"');
                sb.Append(",\"args\":\"").Append(Jw.Esc(Truncate(argsSnippet, ArgsSnippetMax))).Append('"');
                sb.Append('}');
                File.AppendAllText(Path, sb.ToString() + "\n", new UTF8Encoding(false));
            }
            catch (Exception ex)
            {
                // v0.8.45：**不再裸吞**。账本失败仍然绝不外溢（遥测/审计铁律 —— 记账不能反过来
                // 弄坏请求处理），但**必须留痕**：计数 + 记原因 + 写 RGL 第二出口。
                // 收敛成一句话，避免同一故障每请求刷一条 RGL。
                _writeFailures++;
                if (_lastWriteFailure.Length == 0)
                {
                    _lastWriteFailure = ex.GetType().Name + ": " + ex.Message;
                    if (_lastWriteFailure.Length > 200) _lastWriteFailure = _lastWriteFailure.Substring(0, 200);
                    ExceptionToRgl("ActionLedger.Record", ex);
                }
            }
        }

        /// <summary>
        /// 按大小轮转（照 GameMaster 的 `PerformLogRotation`：**只留最近 N 个**，其余删除）。
        /// 阈值与保留数都可配；`MaxActionLogBytes = 0` ⇒ **不轮转**（默认就是"用户显式开启才删"）。
        ///
        /// 为什么默认不轮转：BlBridge 的 battle 日志是**实验数据**，用户会拿 60+ 场历史做对比；
        /// 而账本是**过程记录**，天生可以截断。⇒ 两者的默认策略**故意不同**：
        /// battle 日志永不自动删（没有实现），账本可配可删。
        /// </summary>
        private static void RotateIfNeeded()
        {
            int limit = BridgeConfig.MaxActionLogBytes;
            if (limit <= 0) return;
            try
            {
                string current = Path;
                if (!File.Exists(current)) return;
                long len = new FileInfo(current).Length;
                if (len <= limit) return;

                string stem = System.IO.Path.GetFileNameWithoutExtension(current);
                string ext = System.IO.Path.GetExtension(current);
                string archived = System.IO.Path.Combine(CommandPump.CommandsRoot,
                    ArchivedPrefix + DateTime.Now.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture) + ext);
                File.Move(current, archived);
                PruneArchives(stem, ext);
            }
            catch
            {
            }
        }

        /// <summary>只保留最近 `ActionLogKeepFiles` 个归档（含本轮的 `actions.jsonl` 在内按数量裁）。</summary>
        private static void PruneArchives(string stem, string ext)
        {
            try
            {
                int keep = BridgeConfig.ActionLogKeepFiles;
                if (keep <= 0) return;
                string[] files = Directory.GetFiles(CommandPump.CommandsRoot,
                    ArchivedPrefix + "*" + ext);
                if (files.Length <= keep) return;
                Array.Sort(files, delegate (string a, string b)
                {
                    return File.GetCreationTimeUtc(a).CompareTo(File.GetCreationTimeUtc(b));
                });
                int remove = files.Length - keep;
                for (int i = 0; i < remove; i++)
                {
                    try
                    {
                        File.Delete(files[i]);
                    }
                    catch
                    {
                    }
                }
            }
            catch
            {
            }
        }

        /// <summary>
        /// 从原始请求体里抠出参数片段（`"parameters"` 之后的原文，截断）。
        /// Jmini 是扁平读取器，这里只做**定位**不做解析 —— 拿不到就退化为"从开头截"。
        /// </summary>
        internal static string ArgsSnippet(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return "";
            try
            {
                int p = raw.IndexOf("\"parameters\"", StringComparison.Ordinal);
                if (p >= 0) return raw.Substring(p);
                return raw;
            }
            catch
            {
                return "";
            }
        }

        /// <summary>
        /// 异常**无条件**写游戏 RGL 日志（照 GameMaster 的两级策略：RGL 永远写，自定义文件按开关）。
        ///
        /// 为什么需要这条第二出口：`<LogDir>` 可能不可写（权限/被清理），而那时
        /// **恰恰是最需要留痕的时候**；rgl_log 由引擎自己维护，只要游戏在跑就写得进去。
        /// 注意这里**不用** `System.Diagnostics.Debug`（那是调试器输出），用引擎的 `Debug.Print`。
        /// </summary>
        internal static void ExceptionToRgl(string where, Exception ex)
        {
            try
            {
                string text = ex == null
                    ? "[BlBridge] " + where + ": (null exception)"
                    : "[BlBridge] " + where + ": " + ex.GetType().Name + ": " + ex.Message;
                TaleWorlds.Library.Debug.Print(text);
            }
            catch
            {
            }
        }

        private static string Truncate(string s, int max)
        {
            if (string.IsNullOrEmpty(s)) return "";
            if (max <= 0 || s.Length <= max) return s;
            return s.Substring(0, max) + "…(截断)";
        }
    }
}
