using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace BlBridge.EditorProbe
{
    /// <summary>
    /// Runtime configuration and the session handshake file.
    ///
    /// The archive's editor client refuses to talk to anything unless it can read
    /// a session file whose shape it validates field by field:
    ///
    ///   { "gameDir": "...", "outputDir": "...", "sceneDir": "...",
    ///     "token": ">= 8 chars", "port": 1..65535 }
    ///
    /// and then, separately, it checks on disk that
    ///   - gameDir / outputDir / sceneDir all resolve
    ///   - sceneDir is  <gameDir>/Modules/BannerlordSage.EditorProbe/SceneObj/<sceneName>
    ///   - <sceneName> matches ^[a-zA-Z][a-zA-Z0-9_]{0,79}$
    ///   - BOTH bin/Win64_Shipping_Client/Version.xml and
    ///     bin/Win64_Shipping_wEditor/Version.xml report v1.4.8
    ///
    /// Those are the client's gates, so the probe must SATISFY them, not merely
    /// intend to. Each is asserted in WriteSession() below: a session file that
    /// fails any gate produces the client's generic `invalid_session` with no hint
    /// about which condition failed, so the failure is made loud here instead.
    /// </summary>
    internal static class EditorProbeConfig
    {
        /// <summary>
        /// The module id the CLIENT hardcodes. It resolves
        /// Modules/BannerlordSage.EditorProbe and compares it against the scene's
        /// owning module, so this string cannot be chosen freely.
        /// </summary>
        internal const string ModuleId = "BannerlordSage.EditorProbe";

        /// <summary>The single game version the client validates, in both bin variants.</summary>
        internal const string RequiredGameVersion = "v1.4.8";

        /// <summary>Matches the client's 80-character scene name limit.</summary>
        private const int MaxSceneNameLength = 80;

        internal static string Token { get; private set; }
        internal static string GameDir { get; private set; }
        internal static string ModuleDir { get; private set; }
        internal static string OutputDir { get; private set; }
        internal static string SceneDir { get; private set; }
        internal static string SceneName { get; private set; }
        internal static int Port { get; private set; }

        internal static bool Ready { get; private set; }

        /// <summary>Human-readable reason when Ready is false. Never null once probed.</summary>
        internal static string NotReadyReason { get; private set; }

        /// <summary>Per-frame throttled log so the installer can see progress happen.</summary>
        internal static string LogPath
        {
            get { return Path.Combine(ModuleDir ?? ".", "editor_probe.log"); }
        }

        private static readonly object InitGate = new object();
        private static bool _logged;

        /// <summary>
        /// Resolves every path, generates a session token, and writes the handshake
        /// file. Returns false with NotReadyReason set rather than throwing: the
        /// module must survive a misconfigured install so the game still runs.
        /// </summary>
        internal static bool Initialize(string gameDir, int port)
        {
            lock (InitGate)
            {
                Ready = false;
                NotReadyReason = null;

                if (string.IsNullOrEmpty(gameDir) || !Directory.Exists(gameDir))
                {
                    NotReadyReason = "游戏目录不可用：" + (gameDir ?? "(null)");
                    return false;
                }

                GameDir = gameDir;
                ModuleDir = Path.Combine(gameDir, "Modules", ModuleId);
                SceneName = "ProbeScene";

                if (!Directory.Exists(ModuleDir))
                {
                    NotReadyReason = "探针模组目录不存在：" + ModuleDir;
                    return false;
                }

                // scene.xscene must be a FILE inside SceneObj/<sceneName>, because the
                // client resolves the scene directory and then requires its basename
                // to equal the session's scene name.
                string sceneObj = Path.Combine(ModuleDir, "SceneObj");
                SceneDir = Path.Combine(sceneObj, SceneName);
                OutputDir = Path.Combine(ModuleDir, "output");

                try
                {
                    Directory.CreateDirectory(SceneDir);
                    Directory.CreateDirectory(OutputDir);
                }
                catch (Exception ex)
                {
                    NotReadyReason = "无法创建场景/输出目录：" + ex.Message;
                    return false;
                }

                string sceneFile = Path.Combine(SceneDir, "scene.xscene");
                if (!File.Exists(sceneFile))
                {
                    // An empty-but-valid scene so the editor can open something. The
                    // engine rewrites this on save; creating it here means a fresh
                    // install does not depend on the user authoring a scene first.
                    try
                    {
                        File.WriteAllText(sceneFile,
                            "<?xml version=\"1.0\" encoding=\"utf-8\"?>\r\n<scene>\r\n</scene>\r\n",
                            new UTF8Encoding(false));
                    }
                    catch (Exception ex)
                    {
                        NotReadyReason = "无法创建 scene.xscene：" + ex.Message;
                        return false;
                    }
                }

                if (!ValidateSceneName(SceneName))
                {
                    NotReadyReason = "场景名不符合客户端规则：" + SceneName;
                    return false;
                }

                if (!ValidateGameVersions())
                {
                    NotReadyReason = "游戏版本不是 " + RequiredGameVersion
                        + "（客户端要求 Win64_Shipping_Client 与 Win64_Shipping_wEditor 同时匹配）";
                    return false;
                }

                Token = NewToken();
                Port = port;

                string failure;
                if (!WriteSession(out failure))
                {
                    NotReadyReason = failure;
                    return false;
                }

                TryLog("initialized: gameDir=" + GameDir + " sceneDir=" + SceneDir + " port=" + Port);
                Ready = true;
                return true;
            }
        }

        private static bool ValidateSceneName(string name)
        {
            if (string.IsNullOrEmpty(name) || name.Length > MaxSceneNameLength) return false;
            char first = name[0];
            if (!((first >= 'a' && first <= 'z') || (first >= 'A' && first <= 'Z'))) return false;
            for (int i = 1; i < name.Length; i++)
            {
                char c = name[i];
                bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                          || (c >= '0' && c <= '9') || c == '_';
                if (!ok) return false;
            }
            return true;
        }

        /// <summary>
        /// Both bin variants must report the pinned version, exactly as the client
        /// requires. Missing Version.xml counts as a mismatch — the client treats an
        /// unreadable version the same way, and guessing "probably fine" here would
        /// produce an unexplainable invalid_session later.
        /// </summary>
        private static bool ValidateGameVersions()
        {
            string client = ReadSingleplayerVersion(Path.Combine(GameDir, "bin", "Win64_Shipping_Client", "Version.xml"));
            string editor = ReadSingleplayerVersion(Path.Combine(GameDir, "bin", "Win64_Shipping_wEditor", "Version.xml"));
            return client == RequiredGameVersion && editor == RequiredGameVersion;
        }

        internal static string ReadSingleplayerVersion(string versionXmlPath)
        {
            try
            {
                if (!File.Exists(versionXmlPath)) return null;
                string xml = File.ReadAllText(versionXmlPath, Encoding.UTF8);
                const string marker = "Singleplayer";
                int at = xml.IndexOf(marker, StringComparison.Ordinal);
                if (at < 0) return null;
                int value = xml.IndexOf("Value", at, StringComparison.Ordinal);
                if (value < 0) return null;
                int open = xml.IndexOf('"', value);
                if (open < 0) return null;
                int close = xml.IndexOf('"', open + 1);
                if (close < 0) return null;
                return xml.Substring(open + 1, close - open - 1).Trim();
            }
            catch
            {
                return null;
            }
        }

        /// <summary>
        /// Writes the session file the client reads. Paths are written as absolute
        /// and unescaped-by-hand (Escape does the JSON escaping) so the client's
        /// `localAbsolutePath` check passes on Windows.
        /// </summary>
        internal static bool WriteSession(out string failure)
        {
            failure = null;
            string path = SessionPath;
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                StringBuilder sb = new StringBuilder();
                sb.Append("{\n");
                sb.Append("  \"gameDir\": ").Append(EditorProbeJson.Quote(GameDir)).Append(",\n");
                sb.Append("  \"outputDir\": ").Append(EditorProbeJson.Quote(OutputDir)).Append(",\n");
                sb.Append("  \"sceneDir\": ").Append(EditorProbeJson.Quote(SceneDir)).Append(",\n");
                sb.Append("  \"token\": ").Append(EditorProbeJson.Quote(Token)).Append(",\n");
                sb.Append("  \"port\": ").Append(Port).Append('\n');
                sb.Append("}\n");

                // Write then move, so a client reading concurrently never sees a
                // half-written session file (which it would reject as malformed).
                string tmp = path + ".tmp";
                File.WriteAllText(tmp, sb.ToString(), new UTF8Encoding(false));
                if (File.Exists(path)) File.Delete(path);
                File.Move(tmp, path);

                string readBack = File.ReadAllText(path, Encoding.UTF8);
                Dictionary<string, object> parsed;
                try
                {
                    parsed = EditorProbeJson.ParseObject(readBack);
                }
                catch (Exception ex)
                {
                    failure = "刚写出的 session 无法解析：" + ex.Message;
                    return false;
                }
                if (EditorProbeJson.GetString(parsed, "token") != Token
                    || EditorProbeJson.GetString(parsed, "sceneDir") != SceneDir)
                {
                    failure = "session 回读不一致";
                    return false;
                }
                return true;
            }
            catch (Exception ex)
            {
                failure = "写 session 失败：" + ex.Message;
                return false;
            }
        }

        /// <summary>
        /// Where the session file lives. Inside the module so the client's
        /// "outside the dedicated module scope" check cannot trip over it, and so
        /// uninstalling the module removes the handshake with it.
        /// </summary>
        internal static string SessionPath
        {
            get { return Path.Combine(ModuleDir ?? ".", "editor_probe_session.json"); }
        }

        /// <summary>
        /// A fresh token per game launch. Not a secret to be transmitted anywhere —
        /// its job is to stop a stray local process from driving the editor by
        /// accident, so per-launch randomness is the right lifetime.
        /// </summary>
        private static string NewToken()
        {
            byte[] raw = new byte[24];
            using (RNGCryptoServiceProvider rng = new RNGCryptoServiceProvider())
            {
                rng.GetBytes(raw);
            }
            StringBuilder sb = new StringBuilder(raw.Length * 2);
            for (int i = 0; i < raw.Length; i++) sb.Append(raw[i].ToString("x2"));
            return sb.ToString();
        }

        internal static void TryLog(string message)
        {
            try
            {
                string line = DateTime.UtcNow.ToString("o") + " " + message + "\r\n";
                File.AppendAllText(LogPath, line, new UTF8Encoding(false));
                _logged = true;
            }
            catch
            {
                // Logging must never break the probe.
            }
        }

        internal static bool HasLogged { get { return _logged; } }
    }
}
