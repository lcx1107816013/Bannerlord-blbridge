using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using TaleWorlds.MountAndBlade;

namespace BlBridge.EditorProbe
{
    /// <summary>
    /// Entry point for the BannerlordSage.EditorProbe module.
    ///
    /// ─────────────────────────────────────────────────────────────────────────
    /// WHY THIS IS A SECOND MODULE AND NOT PART OF BlBridge
    ///
    /// BlBridge is the client-runtime bridge and is ZERO-DEPENDENCY by design: its
    /// SubModule.xml depends on Native/SandBoxCore/Sandbox/CustomBattle and nothing
    /// else, and it runs in bin/Win64_Shipping_Client.
    ///
    /// This file compiles only against bin/Win64_Shipping_wEditor, which ships with
    /// the Modding Kit. Merging it into BlBridge.dll would make the zero-dependency
    /// bridge depend on the Kit — risking the working tools to gain a single file.
    ///
    /// So: SAME REPO, TWO MODULES, two independent SubModule.xml files. If the Kit is
    /// missing or out of step, this module fails to load and BlBridge is unaffected.
    /// Bannerlord has no optional module dependency, so "callable by the editor" is
    /// expressed the way BlBridge already does elsewhere (tools/bl_sage.py): a soft
    /// dependency that reports presence and never blocks the host.
    /// </summary>
    public class EditorProbeSubModule : MBSubModuleBase
    {
        /// <summary>Chosen once; per-launch token lives in EditorProbeConfig.</summary>
        private const int ListenPort = 47321;

        private static bool _started;

        protected override void OnSubModuleLoad()
        {
            base.OnSubModuleLoad();
            try
            {
                string gameDir = ResolveGameDirectory();
                if (gameDir == null)
                {
                    // No usable game dir means no session file, which means the client
                    // reports invalid_session. Log the reason so it is not a mystery.
                    TryWriteStandaloneLog("无法定位游戏目录，编辑器探针未启动。");
                    return;
                }

                if (!EditorProbeConfig.Initialize(gameDir, ListenPort))
                {
                    TryWriteStandaloneLog("初始化失败：" + EditorProbeConfig.NotReadyReason);
                    return;
                }

                if (!EditorProbeServer.Start(EditorProbeConfig.Token, ListenPort))
                {
                    // The session file exists but nothing is listening. Say so instead
                    // of leaving the client to time out against a dead port.
                    EditorProbeConfig.TryLog("监听 " + ListenPort + " 失败（端口可能被占用），未启动服务。");
                    return;
                }

                _started = true;
                EditorProbeConfig.TryLog("EditorProbe 就绪：port=" + EditorProbeServer.BoundPort
                    + " token=" + EditorProbeConfig.Token.Substring(0, 8) + "…"
                    + " session=" + EditorProbeConfig.SessionPath);
            }
            catch (Exception ex)
            {
                TryWriteStandaloneLog("加载异常：" + ex);
            }
        }

        /// <summary>
        /// The frame pump. This is where `executedThread` becomes correct: the module
        /// tick is the engine's main thread, so draining the queue here is what makes
        /// the client's mainThread==executedThread check meaningful. Moving this to
        /// the socket thread would make every call report wrong_thread.
        ///
        /// Wrapped in try/catch because a throw here would propagate into the engine's
        /// tick and take the game down.
        /// </summary>
        protected override void OnApplicationTick(float dt)
        {
            base.OnApplicationTick(dt);
            if (!_started) return;
            try
            {
                EditorProbeServer.DrainOnMainThread();
            }
            catch (Exception ex)
            {
                EditorProbeConfig.TryLog("主线程排空异常：" + ex.Message);
            }
        }

        protected override void OnSubModuleUnloaded()
        {
            try
            {
                if (_started)
                {
                    EditorProbeServer.Stop();
                    EditorProbeConfig.TryLog("EditorProbe 已停止（模块卸载）。");
                }
            }
            catch
            {
            }
            _started = false;
            base.OnSubModuleUnloaded();
        }

        // ── game directory discovery ────────────────────────────────────────────

        /// <summary>
        /// Walks up from the assembly's own directory looking for the game root — the
        /// directory that holds both `bin` and `Modules`.
        ///
        /// Deriving it from our own location rather than from a hardcoded path means
        /// the probe follows the install it was actually loaded from, including a
        /// non-Steam layout or a second copy of the game. Returns null rather than
        /// guessing, because a wrong game dir would produce a session file that the
        /// client rejects with no explanation.
        /// </summary>
        internal static string ResolveGameDirectory()
        {
            try
            {
                string dir = AppDomain.CurrentDomain.BaseDirectory;
                if (string.IsNullOrEmpty(dir)) return null;

                DirectoryInfo current = new DirectoryInfo(dir.TrimEnd('\\', '/'));
                // Six levels is more than the depth of
                //   <game>/Modules/<module>/bin/<variant>/
                for (int depth = 0; current != null && depth < 8; depth++)
                {
                    if (Directory.Exists(Path.Combine(current.FullName, "Modules"))
                        && Directory.Exists(Path.Combine(current.FullName, "bin")))
                    {
                        return current.FullName;
                    }
                    current = current.Parent;
                }
            }
            catch
            {
            }
            return null;
        }

        /// <summary>
        /// Last-resort log used before the module dir is known, so a load-time failure
        /// is still discoverable on disk.
        /// </summary>
        private static void TryWriteStandaloneLog(string message)
        {
            try
            {
                string dir = AppDomain.CurrentDomain.BaseDirectory;
                File.AppendAllText(Path.Combine(dir, "editorprobe_load.log"),
                    DateTime.UtcNow.ToString("o") + " " + message + "\r\n", new UTF8Encoding(false));
            }
            catch
            {
            }
        }
    }
}
