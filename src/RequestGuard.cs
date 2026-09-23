using System;
using System.Globalization;
using System.IO;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// 请求校验闸门（纯 System，不依赖游戏程序集 —— 因此可离线单测）。
    ///
    /// 新增原因（对照 Coop 的 `LiveTestProtocol.cs:82-107` 字段级校验）：
    ///   1. 请求 id 会参与响应文件名拼接（`CommandPump.WriteResponse`），
    ///      不校验就是路径穿越面（id = "..\..\evil"）；
    ///   2. 请求文件大小无上限，一个误生成的巨型文件会拖死主线程；
    ///   3. 过期请求（游戏当时没运行、MCP 超时退出）会在游戏**后**启动时被
    ///      执行掉，表现为"凭空冒出一场战斗"（幽灵战斗）。
    /// </summary>
    internal static class RequestGuard
    {
        /// <summary>id 允许的字符集：只认十六进制（我们 MCP 用 uuid4().hex[:16]）。</summary>
        public const int MinIdLength = 16;
        public const int MaxIdLength = 32;

        /// <summary>请求文件大小上限（对照 Coop 的 1 MiB 消息上限）。</summary>
        public const long MaxRequestBytes = 1024L * 1024L;

        /// <summary>响应文件名安全化后的最大长度。</summary>
        private const int MaxSafeNameLength = 64;

        public const string FallbackId = "invalid_request_id";

        /// <summary>id 必须是 16~32 位十六进制。</summary>
        public static bool IsValidId(string id)
        {
            if (id == null) return false;
            if (id.Length < MinIdLength || id.Length > MaxIdLength) return false;
            for (int i = 0; i < id.Length; i++)
            {
                char c = id[i];
                bool hex = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
                if (!hex) return false;
            }
            return true;
        }

        /// <summary>
        /// 把任意字符串安全化成文件名片段：只保留 [A-Za-z0-9_-]，
        /// 其余字符丢弃，长度截断。空结果回退为 FallbackId。
        /// </summary>
        public static string SafeFileName(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return FallbackId;
            StringBuilder sb = new StringBuilder(raw.Length);
            for (int i = 0; i < raw.Length && sb.Length < MaxSafeNameLength; i++)
            {
                char c = raw[i];
                bool ok = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                          c == '_' || c == '-';
                if (ok) sb.Append(c);
            }
            return sb.Length == 0 ? FallbackId : sb.ToString();
        }

        /// <summary>
        /// 请求是否过期。解析失败**视为不过期**（向后兼容旧版请求，不误杀）。
        /// 时钟偏差导致"未来时间"时也不算过期。
        /// </summary>
        public static bool IsExpired(string issuedUtc, DateTime utcNow, int maxAgeSeconds)
        {
            if (string.IsNullOrEmpty(issuedUtc)) return false;
            if (maxAgeSeconds <= 0) return false;
            DateTime issued;
            if (!TryParseUtc(issuedUtc, out issued)) return false;
            double age = (utcNow - issued).TotalSeconds;
            if (age < 0) age = 0;
            return age > maxAgeSeconds;
        }

        /// <summary>兼容 MCP 侧 `strftime("%Y-%m-%dT%H:%M:%S") + "Z"` 与 .NET "o" 两种口径。</summary>
        public static bool TryParseUtc(string s, out DateTime utc)
        {
            utc = DateTime.MinValue;
            if (string.IsNullOrEmpty(s)) return false;
            string[] formats = { "yyyy-MM-ddTHH:mm:ssZ", "yyyy-MM-dd'T'HH:mm:ss.fffZ", "yyyy-MM-ddTHH:mm:ss.fffffffZ" };
            if (DateTime.TryParseExact(s, formats, CultureInfo.InvariantCulture,
                    DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out utc))
            {
                return true;
            }
            return DateTime.TryParse(s, CultureInfo.InvariantCulture,
                DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out utc);
        }

        /// <summary>读取请求文件长度；读不到返回 -1。</summary>
        public static long FileLength(string path)
        {
            try
            {
                return new FileInfo(path).Length;
            }
            catch
            {
                return -1;
            }
        }
    }
}
