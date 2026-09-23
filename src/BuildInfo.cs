using System;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// 构建身份：回答"游戏进程里跑的到底是哪个 DLL"。
    ///
    /// 为什么需要它（对照 Coop 的 `LaunchPreflight` 读 AssemblyMetadata MVID 做构建一致性）：
    /// v0.3.0 时我就是靠人工对比 `bl_status` 里的 version 才发现"进程里是 0.1.0、磁盘上是 0.3.0"，
    /// 然后才让用户重启游戏 —— 这类"改了代码忘部署 / 部署了但没重启"的故障会让整晚的跑批
    /// 用的都是错的二进制，而且**看起来一切正常**。这里把它变成自动化判定：
    ///
    ///   LoadedSha256        模块加载那一刻，磁盘上这个 DLL 的 SHA256（进程身份的锚点）
    ///   CurrentFileSha256   现在磁盘上这个文件的 SHA256（带 10 秒缓存）
    ///   FileChangedSinceLoad  两者不等 ⇒ 有人在我们加载后又部署了一次 ⇒ 必须重启游戏
    /// </summary>
    internal static class BuildInfo
    {
        public static readonly string LoadedPath;
        public static readonly string LoadedSha256;
        public static readonly string LoadedAtUtc;
        public static readonly string Mvid;
        public static readonly string AssemblyVersion;

        private static string _cachedNowHash;
        private static double _cachedNowHashAt;

        static BuildInfo()
        {
            LoadedAtUtc = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ");
            try
            {
                Assembly asm = typeof(BuildInfo).Assembly;
                AssemblyVersion = asm.GetName().Version != null ? asm.GetName().Version.ToString() : "";
                Mvid = asm.ManifestModule.ModuleVersionId.ToString("N");
                LoadedPath = asm.Location;
                LoadedSha256 = HashFile(LoadedPath);
            }
            catch
            {
                LoadedSha256 = "";
            }
        }

        public static string CurrentFileSha256()
        {
            if (string.IsNullOrEmpty(LoadedPath)) return "";
            double now = (double)DateTime.UtcNow.Ticks / TimeSpan.TicksPerSecond;
            if (_cachedNowHash != null && (now - _cachedNowHashAt) < 10.0) return _cachedNowHash;
            _cachedNowHash = HashFile(LoadedPath);
            _cachedNowHashAt = now;
            return _cachedNowHash;
        }

        public static bool FileChangedSinceLoad
        {
            get
            {
                string cur = CurrentFileSha256();
                if (string.IsNullOrEmpty(cur) || string.IsNullOrEmpty(LoadedSha256)) return false;
                return !string.Equals(cur, LoadedSha256, StringComparison.OrdinalIgnoreCase);
            }
        }

        /// <summary>简短形式（16 字符），用于 process 身份块，避免响应体膨胀。</summary>
        public static string LoadedSha256Short
        {
            get { return Short(LoadedSha256); }
        }

        public static string HashFile(string path)
        {
            try
            {
                using (FileStream fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (SHA256 sha = SHA256.Create())
                {
                    byte[] hash = sha.ComputeHash(fs);
                    StringBuilder sb = new StringBuilder(hash.Length * 2);
                    for (int i = 0; i < hash.Length; i++) sb.Append(hash[i].ToString("x2"));
                    return sb.ToString();
                }
            }
            catch
            {
                return "";
            }
        }

        public static string Json()
        {
            string cur = CurrentFileSha256();
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"version\":\"").Append(Jw.Esc(BridgeConfig.Version)).Append('"');
            sb.Append(",\"assemblyVersion\":\"").Append(Jw.Esc(AssemblyVersion)).Append('"');
            sb.Append(",\"mvid\":\"").Append(Jw.Esc(Mvid)).Append('"');
            sb.Append(",\"loadedSha256\":\"").Append(Jw.Esc(Short(LoadedSha256))).Append('"');
            sb.Append(",\"currentFileSha256\":\"").Append(Jw.Esc(Short(cur))).Append('"');
            sb.Append(",\"fileChangedSinceLoad\":").Append(Jw.B(FileChangedSinceLoad));
            sb.Append(",\"loadedAtUtc\":\"").Append(Jw.Esc(LoadedAtUtc)).Append('"');
            sb.Append(",\"path\":\"").Append(Jw.Esc(LoadedPath)).Append('"');
            sb.Append('}');
            return sb.ToString();
        }

        private static string Short(string hash)
        {
            if (string.IsNullOrEmpty(hash)) return "";
            return hash.Length > 16 ? hash.Substring(0, 16) : hash;
        }
    }
}
