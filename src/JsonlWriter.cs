using System;
using System.Globalization;
using System.IO;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// 极简 JSONL 写入器（不依赖 Newtonsoft，避免运行时程序集解析问题）。
    /// 每写一行可选立即 flush —— 游戏崩溃/强退也不丢已记录的命中数据。
    /// </summary>
    internal static class Jw
    {
        private static readonly object _lock = new object();
        private static StreamWriter _writer;
        private static string _currentFile;
        private static string _lastError;

        public static string CurrentFile
        {
            get { return _currentFile; }
        }

        /// <summary>最近一次打开/写入失败的原因（诊断用；为空表示一切正常）。</summary>
        public static string LastError
        {
            get { return _lastError; }
        }

        public static bool IsOpen
        {
            get { return _writer != null; }
        }

        /// <summary>
        /// 打开日志文件并返回**实际使用的路径**。
        /// v0.4.0：改用 FileMode.CreateNew + 同秒冲突自动加序号 ——
        /// 旧实现用 FileMode.Create，同一秒内重开战斗会**静默覆盖**上一场的数据。
        /// </summary>
        public static string Open(string preferredPath)
        {
            lock (_lock)
            {
                Close_NoLock();
                _lastError = null;
                string candidate = preferredPath;
                for (int attempt = 0; attempt < 20; attempt++)
                {
                    if (attempt > 0)
                    {
                        string dir = Path.GetDirectoryName(preferredPath);
                        string stem = Path.GetFileNameWithoutExtension(preferredPath);
                        string ext = Path.GetExtension(preferredPath);
                        candidate = Path.Combine(dir, stem + "_" + attempt + ext);
                    }
                    try
                    {
                        Directory.CreateDirectory(Path.GetDirectoryName(candidate));
                        FileStream fs = new FileStream(candidate, FileMode.CreateNew, FileAccess.Write, FileShare.ReadWrite);
                        _writer = new StreamWriter(fs, new UTF8Encoding(false));
                        _writer.AutoFlush = BridgeConfig.FlushEveryLine;
                        _currentFile = candidate;
                        return candidate;
                    }
                    catch (IOException)
                    {
                        // 文件已存在（同秒重开）→ 换下一个序号重试
                    }
                    catch (Exception ex)
                    {
                        _writer = null;
                        _lastError = "Open: " + ex.GetType().Name + ": " + ex.Message;
                        return preferredPath;
                    }
                }
                _writer = null;
                _lastError = "Open: 连续 20 个候选文件名都已存在";
                return preferredPath;
            }
        }

        public static void Write(string line)
        {
            lock (_lock)
            {
                if (_writer == null) return;
                try
                {
                    _writer.WriteLine(line);
                    if (!_writer.AutoFlush) _writer.Flush();
                }
                catch (Exception ex)
                {
                    _lastError = "Write: " + ex.GetType().Name + ": " + ex.Message;
                }
            }
        }

        public static void Close()
        {
            lock (_lock)
            {
                Close_NoLock();
            }
        }

        private static void Close_NoLock()
        {
            if (_writer == null) return;
            try
            {
                _writer.Flush();
                _writer.Dispose();
            }
            catch
            {
            }
            _writer = null;
        }

        private static long _nanCount;

        /// <summary>被丢弃的 NaN/Infinity 值个数（v0.4.0 起用于数据污染告警，会写进 end 事件）。</summary>
        public static long NanCount
        {
            get { return _nanCount; }
        }

        /// <summary>
        /// 浮点数按不变文化输出，避免逗号小数点。
        /// v0.4.0：改用 round-trip（"R"）保留精度（旧版 "0.###" 只留 3 位，累加对比会丢尾数）；
        /// NaN/Infinity 输出 JSON null 并计数 —— 旧版静默变 0，会把引擎异常值伪装成正常数据。
        /// </summary>
        public static string N(float v)
        {
            if (float.IsNaN(v) || float.IsInfinity(v))
            {
                System.Threading.Interlocked.Increment(ref _nanCount);
                return "null";
            }
            return v.ToString("R", CultureInfo.InvariantCulture);
        }

        public static string N(long v)
        {
            return v.ToString(CultureInfo.InvariantCulture);
        }

        public static string N(int v)
        {
            return v.ToString(CultureInfo.InvariantCulture);
        }

        public static string B(bool v)
        {
            return v ? "true" : "false";
        }

        /// <summary>JSON 字符串转义（含控制字符）。</summary>
        public static string Esc(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            StringBuilder sb = new StringBuilder(s.Length + 8);
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < ' ') sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        else sb.Append(c);
                        break;
                }
            }
            return sb.ToString();
        }

        /// <summary>ISO8601 UTC，秒级精度足够。</summary>
        public static string UtcNow()
        {
            return DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture);
        }

        // ─────────────────────────────────────────────────────────────────
        // 侧信道写手（v0.8.47）：**独立于战斗日志的常开文件**
        // ─────────────────────────────────────────────────────────────────
        //
        // 为什么需要它：主 `_writer` 是**战斗日志**，只在 mission 期间打开
        // （`Open()` 由遥测行为调用）。而异常**随时会发生** —— 主菜单、加载期、
        // 战役里都可能有 —— 那些时刻 `_writer == null`，`Write()` 会**静默丢弃**。
        //
        // ⇒ 异常记录必须有自己的写手：模块加载时开、卸载时关，**全程打开**。
        //   两者独立 ⇒ 谁也不会挤掉谁（战斗日志换场时不会关掉它）。

        private static readonly object _sideLock = new object();
        private static StreamWriter _sideWriter;
        private static string _sideFile;
        private static string _sideLastError;

        /// <summary>侧信道文件路径（未打开时为空）。</summary>
        public static string SideFile
        {
            get { lock (_sideLock) { return _sideFile; } }
        }

        /// <summary>侧信道最近一次错误（为空表示正常）。</summary>
        public static string SideLastError
        {
            get { lock (_sideLock) { return _sideLastError; } }
        }

        /// <summary>
        /// 打开侧信道（追加模式 —— 异常日志跨会话累积比"每次新建"更有用）。
        /// 返回实际路径；失败返回 null 并记 `SideLastError`（**不抛**）。
        /// </summary>
        public static string OpenSide(string path, bool append)
        {
            lock (_sideLock)
            {
                try
                {
                    string dir = Path.GetDirectoryName(path);
                    if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                    FileStream fs = new FileStream(path,
                        append ? FileMode.Append : FileMode.Create,
                        FileAccess.Write, FileShare.ReadWrite);
                    _sideWriter = new StreamWriter(fs, new UTF8Encoding(false));
                    // ★ 侧信道**强制逐行 flush**：崩溃就发生在我们想记录的那些时刻，
                    //   缓冲住就等于没记（这条不能跟随战斗日志的 FlushEveryLine 配置）。
                    _sideWriter.AutoFlush = true;
                    _sideFile = path;
                    _sideLastError = null;
                    return path;
                }
                catch (Exception ex)
                {
                    _sideWriter = null;
                    _sideFile = null;
                    _sideLastError = "OpenSide: " + ex.GetType().Name + ": " + ex.Message;
                    return null;
                }
            }
        }

        /// <summary>写侧信道一行（**无 writer 时静默返回**，与 `Write` 同语义）。</summary>
        public static void WriteSide(string line)
        {
            lock (_sideLock)
            {
                if (_sideWriter == null) return;
                try
                {
                    _sideWriter.WriteLine(line);
                }
                catch (Exception ex)
                {
                    _sideLastError = "WriteSide: " + ex.GetType().Name + ": " + ex.Message;
                }
            }
        }

        /// <summary>关侧信道（模块卸载）。</summary>
        public static void CloseSide()
        {
            lock (_sideLock)
            {
                if (_sideWriter == null) return;
                try
                {
                    _sideWriter.Flush();
                    _sideWriter.Dispose();
                }
                catch { }
                _sideWriter = null;
            }
        }

        // ─────────────────────────────────────────────────────────────────
        // 崩溃守卫账本（v0.8.49）：**第三个**独立写手
        // ─────────────────────────────────────────────────────────────────
        //
        // 为什么要独立第三个，而不是复用 `_sideWriter`：
        //   侧信道是 `ExceptionProbe`（FirstChance 观察者）的，**全程打开**；
        //   而守卫账本由 `CrashGuard` 在启用时开。两者**生命周期不同**，
        //   共用一个写手会互相把对方关掉（`OpenSide` 里第一件事就是关旧的）。
        //   ⇒ 各自持有自己的写手，互不干扰。
        //
        // ⚠️ 与侧信道一样**强制逐行 flush**：守卫记录的都是"即将出事的时刻"，
        //   缓冲住就等于没记。
        private static readonly object _guardLock = new object();
        private static StreamWriter _guardWriter;
        private static string _guardFile;
        private static string _guardLastError;

        /// <summary>守卫账本路径（未打开时为空）。</summary>
        public static string GuardFile
        {
            get { lock (_guardLock) { return _guardFile; } }
        }

        /// <summary>守卫账本最近一次错误（为空表示正常）。</summary>
        public static string GuardLastError
        {
            get { lock (_guardLock) { return _guardLastError; } }
        }

        /// <summary>打开守卫账本（追加模式）。返回实际路径；失败返回 null 并记错误（**不抛**）。</summary>
        public static string OpenGuardLog(string path, bool append)
        {
            lock (_guardLock)
            {
                try
                {
                    string dir = Path.GetDirectoryName(path);
                    if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                    FileStream fs = new FileStream(path,
                        append ? FileMode.Append : FileMode.Create,
                        FileAccess.Write, FileShare.ReadWrite);
                    _guardWriter = new StreamWriter(fs, new UTF8Encoding(false));
                    _guardWriter.AutoFlush = true;
                    _guardFile = path;
                    _guardLastError = null;
                    return path;
                }
                catch (Exception ex)
                {
                    _guardWriter = null;
                    _guardFile = null;
                    _guardLastError = "OpenGuardLog: " + ex.GetType().Name + ": " + ex.Message;
                    return null;
                }
            }
        }

        /// <summary>写守卫账本一行（**无 writer 时静默返回**）。</summary>
        public static void WriteGuardLog(string line)
        {
            lock (_guardLock)
            {
                if (_guardWriter == null) return;
                try
                {
                    _guardWriter.WriteLine(line);
                }
                catch (Exception ex)
                {
                    _guardLastError = "WriteGuardLog: " + ex.GetType().Name + ": " + ex.Message;
                }
            }
        }

        /// <summary>
        /// 写守卫账本一行，**并如实返回是否成功**。
        ///
        /// ★ 为什么需要它（v0.8.58）：`WriteGuardLog` 是 `void` 且**内部吞掉写失败**
        ///   ⇒ 调用方**无法区分**"写成功了"与"writer 为空/写盘失败"。
        ///   而 `CrashGuard` 的"立即落盘"路径需要一个**真实**的成功判据 ——
        ///   否则写失败时它会以为成功、**不再退回队列**，那条记录就真丢了
        ///   （恰好是这次要修的那个缺口）。
        ///   ⇒ 本方法把失败**显式暴露**出来，让调用方能安全回退。
        /// </summary>
        public static bool TryWriteGuardLog(string line)
        {
            lock (_guardLock)
            {
                if (_guardWriter == null) return false;
                try
                {
                    _guardWriter.WriteLine(line);
                    return true;            // AutoFlush=true ⇒ 到这里已经落盘
                }
                catch (Exception ex)
                {
                    _guardLastError = "TryWriteGuardLog: " + ex.GetType().Name + ": " + ex.Message;
                    return false;
                }
            }
        }

        /// <summary>关守卫账本（模块卸载）。</summary>
        public static void CloseGuardLog()
        {
            lock (_guardLock)
            {
                if (_guardWriter == null) return;
                try
                {
                    _guardWriter.Flush();
                    _guardWriter.Dispose();
                }
                catch { }
                _guardWriter = null;
            }
        }

        // ─────────────────────────────────────────────────────────────────
        // 战役事件账本（v0.8.50）：**第四个**独立写手
        // ─────────────────────────────────────────────────────────────────
        //
        // 为什么又要独立一个（而不是复用上面三个）：
        //   • 战斗日志 `_writer` 只在 mission 期间打开 —— 而战役事件发生在**地图上**，
        //     那一刻它是 null ⇒ 复用它会静默丢弃全部战役事件（正是侧信道当初的教训）；
        //   • 侧信道是异常探针的、守卫账本是 CrashGuard 的，两者生命周期各不相同；
        //     共用会互相 `Close` 掉对方（`Open*` 第一件事都是关旧的）。
        //   ⇒ 第四个，独立持有，生命周期 = 战役会话。
        //
        // 追加模式（跨会话累积比"每次新建"更有用：可以横跨多次读档看同一批 NPC 的
        // 行为漂移）；**强制逐行 flush**：观察者记录的常是"即将出事/正在涌现"的时刻。
        private static readonly object _obsLock = new object();
        private static StreamWriter _obsWriter;
        private static string _obsFile;
        private static string _obsLastError;

        /// <summary>战役事件账本路径（未打开时为空）。</summary>
        public static string ObserverFile
        {
            get { lock (_obsLock) { return _obsFile; } }
        }

        /// <summary>战役事件账本最近一次错误（为空表示正常）。</summary>
        public static string ObserverLastError
        {
            get { lock (_obsLock) { return _obsLastError; } }
        }

        /// <summary>账本是否已打开（区分"没事件"与"写手没开"—— 这两件事必须能分开）。</summary>
        public static bool ObserverOpen
        {
            get { lock (_obsLock) { return _obsWriter != null; } }
        }

        /// <summary>打开战役事件账本（追加模式）。返回实际路径；失败返回 null 并记错误（**不抛**）。</summary>
        public static string OpenObserver(string path, bool append)
        {
            lock (_obsLock)
            {
                try
                {
                    if (_obsWriter != null && _obsFile == path) return _obsFile;
                    string dir = Path.GetDirectoryName(path);
                    if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                    FileStream fs = new FileStream(path,
                        append ? FileMode.Append : FileMode.Create,
                        FileAccess.Write, FileShare.ReadWrite);
                    _obsWriter = new StreamWriter(fs, new UTF8Encoding(false));
                    _obsWriter.AutoFlush = true;
                    _obsFile = path;
                    _obsLastError = null;
                    return path;
                }
                catch (Exception ex)
                {
                    _obsWriter = null;
                    _obsFile = null;
                    _obsLastError = "OpenObserver: " + ex.GetType().Name + ": " + ex.Message;
                    return null;
                }
            }
        }

        /// <summary>写战役事件账本一行（**无 writer 时静默返回**，与 `Write` 同语义）。</summary>
        public static void WriteObserver(string line)
        {
            lock (_obsLock)
            {
                if (_obsWriter == null) return;
                try
                {
                    _obsWriter.WriteLine(line);
                }
                catch (Exception ex)
                {
                    _obsLastError = "WriteObserver: " + ex.GetType().Name + ": " + ex.Message;
                }
            }
        }

        /// <summary>关战役事件账本（模块卸载）。</summary>
        public static void CloseObserver()
        {
            lock (_obsLock)
            {
                if (_obsWriter == null) return;
                try
                {
                    _obsWriter.Flush();
                    _obsWriter.Dispose();
                }
                catch { }
                _obsWriter = null;
            }
        }
    }
}
