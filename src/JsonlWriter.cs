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
    }
}
