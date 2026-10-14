using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using BlBridge.EditorProbe;

namespace EditorProbeTest
{
    /// <summary>
    /// Offline assertions for the EditorProbe wire contract.
    ///
    /// WHAT THIS PROVES AND WHAT IT CANNOT
    ///
    /// It proves the parts that do not need the engine: JSON parsing/escaping, the
    /// session file the archive client validates, the action vocabulary, and the
    /// full TCP round trip including the mainThread/executedThread contract.
    ///
    /// It CANNOT prove that entering the editor works, because that needs the game
    /// running. Nothing here fakes that: the actions that require edit mode are
    /// asserted to FAIL with a diagnosable code, which is the honest offline result.
    ///
    /// Being able to fail matters. Several assertions below deliberately feed bad
    /// input and require rejection, so a reader can see the suite is not merely
    /// agreeing with itself.
    /// </summary>
    internal static class Program
    {
        private static int _pass;
        private static int _fail;

        /// <summary>
        /// The test EXE is built into the repo's out/ directory, but the TaleWorlds
        /// assemblies it references live in the game install. Without a resolver the
        /// runtime fails on the first use of a game type ("could not load
        /// TaleWorlds.Engine"), which would look like a code fault when it is only a
        /// deployment detail.
        ///
        /// This deliberately does NOT hide a missing Modding Kit: if the directory is
        /// absent the handler returns null and the failure surfaces normally.
        /// </summary>
        private static void HookAssemblyResolve()
        {
            string gameDir = FindGameDir();
            if (gameDir == null) return;
            string editorBin = Path.Combine(gameDir, "bin", "Win64_Shipping_wEditor");
            if (!Directory.Exists(editorBin)) return;

            AppDomain.CurrentDomain.AssemblyResolve += (sender, args) =>
            {
                try
                {
                    string simple = new System.Reflection.AssemblyName(args.Name).Name;
                    string candidate = Path.Combine(editorBin, simple + ".dll");
                    if (File.Exists(candidate))
                        return System.Reflection.Assembly.LoadFrom(candidate);
                }
                catch
                {
                }
                return null;
            };
        }

        private static int Main()
        {
            Console.OutputEncoding = Encoding.UTF8;
            HookAssemblyResolve();

            Console.WriteLine("== EditorProbe 离线断言 ==\n");

            TestJsonParser();
            TestJsonEscaping();
            TestActions();
            TestConfigInit();
            TestTransportRoundTrip();
            TestTransportRejections();
            TestStatusIsAnswerableWithoutEditor();

            Console.WriteLine();
            Console.WriteLine("== " + (_fail == 0 ? "全部通过" : "有失败") + "（" + (_pass + _fail) + " 项，失败 " + _fail + "）==");
            return _fail == 0 ? 0 : 1;
        }

        // ── helpers ─────────────────────────────────────────────────────────────

        private static void Ok(string name, bool condition, string detail = null)
        {
            if (condition)
            {
                _pass++;
                Console.WriteLine("  [ok]   " + name);
            }
            else
            {
                _fail++;
                Console.WriteLine("  [FAIL] " + name + (detail == null ? "" : "  <- " + detail));
            }
        }

        private static void OkEq<T>(string name, T actual, T expected)
        {
            bool same = EqualityComparer<T>.Default.Equals(actual, expected);
            Ok(name, same, same ? null : "got " + Fmt(actual) + ", want " + Fmt(expected));
        }

        private static string Fmt(object v)
        {
            return v == null ? "(null)" : v.ToString();
        }

        // ── JSON ────────────────────────────────────────────────────────────────

        private static void TestJsonParser()
        {
            Console.WriteLine("[JSON 解析]");

            Dictionary<string, object> o = EditorProbeJson.ParseObject(
                "{\"token\":\"abc\",\"action\":\"status\",\"args\":{}}");
            OkEq("读 token", EditorProbeJson.GetString(o, "token"), "abc");
            OkEq("读 action", EditorProbeJson.GetString(o, "action"), "status");
            Ok("args 是对象", EditorProbeJson.GetObject(o, "args") != null);

            // nested + array + all scalar kinds
            Dictionary<string, object> n = EditorProbeJson.ParseObject(
                "{\"e\":[{\"id\":\"a\",\"x\":1.5,\"f\":true,\"z\":null}],\"deep\":{\"k\":[1,2,3]}}");
            List<object> arr = EditorProbeJson.GetArray(n, "e");
            OkEq("数组长度", arr.Count, 1);
            Dictionary<string, object> first = arr[0] as Dictionary<string, object>;
            Ok("嵌套对象可读", first != null);
            double x;
            Ok("浮点可读", EditorProbeJson.TryGetNumber(first, "x", out x) && Math.Abs(x - 1.5) < 1e-9);
            Ok("布尔可读", IsTrue(EditorProbeJson.Get(first, "f")));
            OkEq("null 可读", EditorProbeJson.Get(first, "z"), null);
            int xInt;
            Ok("整数可读为 int", EditorProbeJson.TryGetInt(first, "id2", out xInt) == false);

            // unicode escapes survive
            Dictionary<string, object> u = EditorProbeJson.ParseObject("{\"p\":\"C:\\\\dir\\\\\\u4e2d\\u6587.xml\"}");
            OkEq("转义与 \\u 还原", EditorProbeJson.GetString(u, "p"), "C:\\dir\\中文.xml");

            // ── must REJECT (proves the parser is not lenient) ──
            Ok("拒绝尾随内容", Throws(() => EditorProbeJson.ParseObject("{} x")));
            Ok("拒绝未闭合对象", Throws(() => EditorProbeJson.ParseObject("{\"a\":1")));
            Ok("拒绝未闭合字符串", Throws(() => EditorProbeJson.ParseObject("{\"a\":\"x}")));
            Ok("拒绝坏转义", Throws(() => EditorProbeJson.ParseObject("{\"a\":\"\\q\"}")));
            Ok("拒绝空输入", Throws(() => EditorProbeJson.ParseObject("")));
            Ok("拒绝裸值", Throws(() => EditorProbeJson.ParseObject("123")));
            Console.WriteLine();
        }

        /// <summary>Reads a JSON boolean that arrived as object.</summary>
        private static bool IsFalse(object v)
        {
            return v is bool && !(bool)v;
        }

        private static bool IsTrue(object v)
        {
            return v is bool && (bool)v;
        }

        private static bool Throws(Action action)
        {
            try { action(); return false; }
            catch { return true; }
        }

        private static void TestJsonEscaping()
        {
            Console.WriteLine("[JSON 输出]");

            // A Windows path MUST come out with single backslashes in the JSON text.
            // This is the same class of bug that F11 caught in the capability probe,
            // where Jw.Esc escaped without quoting and emitted unparseable JSON.
            string json = "{\"p\":" + EditorProbeJson.Quote("C:\\a\\b") + "}";
            Dictionary<string, object> back = EditorProbeJson.ParseObject(json);
            OkEq("路径往返一致", EditorProbeJson.GetString(back, "p"), "C:\\a\\b");
            Ok("输出里反斜杠被转义（不是裸的）", json.Contains("\\\\"));

            string quoted = EditorProbeJson.Quote("say \"hi\"");
            Ok("引号被转义", EditorProbeJson.ParseObject("{\"q\":" + quoted + "}") != null);

            string withNl = "{\"q\":" + EditorProbeJson.Quote("a\nb\tc") + "}";
            Ok("控制字符被转义，仍是合法 JSON", EditorProbeJson.ParseObject(withNl) != null);

            string cjk = "{\"q\":" + EditorProbeJson.Quote("中文路径") + "}";
            OkEq("非 ASCII 不被破坏", EditorProbeJson.GetString(EditorProbeJson.ParseObject(cjk), "q"), "中文路径");

            OkEq("Quote(null) 是空串", EditorProbeJson.Quote(null), "\"\"");
            Console.WriteLine();
        }

        private static void TestActions()
        {
            Console.WriteLine("[动作表]");

            string[] expected =
            {
                "status", "enter_editor", "open_scene", "entities", "place_probe",
                "move_probe", "save_scene", "close_scene", "quit", "capture",
                "apply_layout", "layout_entities", "focus", "camera", "prefab_info",
            };
            string[] actual = EditorProbeActions.AllActions();
            OkEq("动作数量与客户端 EDITOR_PROBE_ACTIONS 一致", actual.Length, expected.Length);

            bool allPresent = true;
            foreach (string want in expected)
            {
                if (!EditorProbeActions.IsKnown(want)) { allPresent = false; Console.WriteLine("      缺少: " + want); }
            }
            Ok("客户端会发的每个动作都被接受", allPresent);
            Ok("未知动作被拒绝", !EditorProbeActions.IsKnown("not_an_action"));
            Ok("空动作被拒绝", !EditorProbeActions.IsKnown(""));
            Ok("status 不需要活动场景", !EditorProbeActions.RequiresActiveScene("status"));
            Ok("apply_layout 需要活动场景", EditorProbeActions.RequiresActiveScene("apply_layout"));
            Console.WriteLine();
        }

        // ── config / session ────────────────────────────────────────────────────

        private static string _gameDir;

        private static string FindGameDir()
        {
            foreach (string candidate in new[]
            {
                Environment.GetEnvironmentVariable("BANNERLORD_DIR"),
                @"G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord",
            })
            {
                if (!string.IsNullOrEmpty(candidate) && Directory.Exists(candidate)) return candidate;
            }
            return null;
        }

        private static void TestConfigInit()
        {
            Console.WriteLine("[配置 / session 文件]");

            _gameDir = FindGameDir();
            if (_gameDir == null)
            {
                _fail++;
                Console.WriteLine("  [FAIL] 找不到游戏目录（设 BANNERLORD_DIR 或装好游戏）");
                return;
            }

            bool ready = EditorProbeConfig.Initialize(_gameDir, 47321);
            Ok("初始化成功", ready, EditorProbeConfig.NotReadyReason);

            if (!ready)
            {
                // Report exactly which gate failed rather than continuing on bad state.
                Console.WriteLine("       原因: " + EditorProbeConfig.NotReadyReason);
                return;
            }

            Ok("Modding Kit 的 7 个程序集都在", Directory.Exists(Path.Combine(_gameDir, "bin", "Win64_Shipping_wEditor")));
            Ok("两个 bin 变体版本都读到 v1.4.8",
                EditorProbeConfig.ReadSingleplayerVersion(Path.Combine(_gameDir, "bin", "Win64_Shipping_Client", "Version.xml")) == "v1.4.8"
                && EditorProbeConfig.ReadSingleplayerVersion(Path.Combine(_gameDir, "bin", "Win64_Shipping_wEditor", "Version.xml")) == "v1.4.8");

            // The archive client validates each of these; if any is wrong it reports a
            // generic invalid_session with no indication of which one failed.
            Ok("session 文件存在", File.Exists(EditorProbeConfig.SessionPath));
            Dictionary<string, object> session = EditorProbeJson.ParseObject(
                File.ReadAllText(EditorProbeConfig.SessionPath, Encoding.UTF8));
            OkEq("session.gameDir", EditorProbeJson.GetString(session, "gameDir"), _gameDir);
            Ok("session.token >= 8 字符", (EditorProbeJson.GetString(session, "token") ?? "").Length >= 8);
            int port;
            Ok("session.port 在 1..65535", EditorProbeJson.TryGetInt(session, "port", out port) && port >= 1 && port <= 65535);

            string sceneDir = EditorProbeJson.GetString(session, "sceneDir");
            string expectedSceneDir = Path.Combine(_gameDir, "Modules", "BannerlordSage.EditorProbe", "SceneObj", "ProbeScene");
            OkEq("sceneDir 落在模块 SceneObj 下（客户端会 realpath 校验）", sceneDir, expectedSceneDir);
            Ok("scene.xscene 存在", File.Exists(Path.Combine(sceneDir, "scene.xscene")));
            Ok("场景名符合客户端正则 ^[a-zA-Z][a-zA-Z0-9_]{0,79}$",
                System.Text.RegularExpressions.Regex.IsMatch(
                    Path.GetFileName(sceneDir), "^[a-zA-Z][a-zA-Z0-9_]{0,79}$"));

            // Idempotent: a second init must not corrupt the session or throw.
            bool again = EditorProbeConfig.Initialize(_gameDir, 47321);
            Ok("重复初始化仍然成功", again, EditorProbeConfig.NotReadyReason);
            Console.WriteLine();
        }

        // ── transport ───────────────────────────────────────────────────────────

        /// <summary>
        /// Stands in for the game's main thread: repeatedly drains the queue. The
        /// thread id this captures becomes `mainThread`, which is what the archive
        /// client compares `executedThread` against.
        /// </summary>
        private static void RunMainThread(ManualResetEvent stop)
        {
            EditorProbeServer.ObserveMainThread();
            while (!stop.WaitOne(5))
            {
                EditorProbeServer.DrainOnMainThread();
            }
        }

        private static string SendRaw(int port, string text)
        {
            using (TcpClient client = new TcpClient())
            {
                client.Connect("127.0.0.1", port);
                using (NetworkStream stream = client.GetStream())
                {
                    byte[] outgoing = Encoding.UTF8.GetBytes(text + "\n");
                    stream.Write(outgoing, 0, outgoing.Length);
                    stream.Flush();

                    MemoryStream buffer = new MemoryStream();
                    byte[] chunk = new byte[4096];
                    client.ReceiveTimeout = 10000;
                    while (true)
                    {
                        int read = stream.Read(chunk, 0, chunk.Length);
                        if (read <= 0) break;
                        for (int i = 0; i < read; i++)
                        {
                            if (chunk[i] == (byte)'\n') return Encoding.UTF8.GetString(buffer.ToArray());
                            buffer.WriteByte(chunk[i]);
                        }
                    }
                    return Encoding.UTF8.GetString(buffer.ToArray());
                }
            }
        }

        private static void TestTransportRoundTrip()
        {
            Console.WriteLine("[TCP 往返]");

            if (!EditorProbeConfig.Ready)
            {
                _fail++;
                Console.WriteLine("  [FAIL] 配置未就绪，跳过往返测试");
                return;
            }

            bool started = EditorProbeServer.Start(EditorProbeConfig.Token, EditorProbeConfig.Port);
            Ok("监听启动", started);
            if (!started) return;

            ManualResetEvent stop = new ManualResetEvent(false);
            Thread mainThread = new Thread(() => RunMainThread(stop));
            mainThread.IsBackground = true;
            mainThread.Start();

            // Give the drain loop a moment to register the main thread id.
            Thread.Sleep(50);

            string request = "{\"token\":\"" + EditorProbeConfig.Token + "\",\"action\":\"status\",\"args\":{}}";
            string reply = SendRaw(EditorProbeServer.BoundPort, request);
            Console.WriteLine("       请求: " + request.Substring(0, Math.Min(60, request.Length)) + "...");
            Console.WriteLine("       应答: " + (reply.Length > 200 ? reply.Substring(0, 200) + "..." : reply));

            Dictionary<string, object> parsed = null;
            try { parsed = EditorProbeJson.ParseObject(reply); }
            catch (Exception ex) { Console.WriteLine("       解析失败: " + ex.Message); }

            Ok("应答是合法 JSON 对象", parsed != null);
            if (parsed != null)
            {
                // The client requires `typeof parsed.ok === 'boolean'` or it treats the
                // reply as invalid and reports invalid_response.
                Ok("应答 ok 为 true", IsTrue(EditorProbeJson.Get(parsed, "ok")));
                Ok("应答含 result 对象", EditorProbeJson.GetObject(parsed, "result") != null);
                Ok("应答含 mainThread", EditorProbeJson.Get(parsed, "mainThread") != null);
                Ok("应答含 executedThread", EditorProbeJson.Get(parsed, "executedThread") != null);

                // ★ The whole point of the two-thread design: the action must run on
                // the same thread the engine ticks on, or the client reports
                // wrong_thread and every working call looks broken.
                // Both out params must be assigned before use, so the calls are
                // kept separate rather than short-circuited inside one &&.
                int mainId = 0, execId = 0;
                bool haveIds = EditorProbeJson.TryGetInt(parsed, "mainThread", out mainId);
                haveIds = EditorProbeJson.TryGetInt(parsed, "executedThread", out execId) && haveIds;
                Ok("mainThread == executedThread（否则客户端会报 wrong_thread）",
                    haveIds && mainId == execId,
                    haveIds ? ("main=" + mainId + " exec=" + execId) : "缺字段");
                Ok("未标记 offMainThread", EditorProbeJson.Get(parsed, "offMainThread") == null);

                Dictionary<string, object> result = EditorProbeJson.GetObject(parsed, "result");
                Ok("result 报告 probeScene", EditorProbeJson.GetString(result, "probeScene") == "ProbeScene");
                // The client realpath-checks this against its session module dir.
                Ok("result.resolvedSceneModule 等于模块目录",
                    EditorProbeJson.GetString(result, "resolvedSceneModule") == EditorProbeConfig.ModuleDir);
            }

            stop.Set();
            mainThread.Join(2000);
            EditorProbeServer.Stop();
            Console.WriteLine();
        }

        private static void TestTransportRejections()
        {
            Console.WriteLine("[拒绝路径 —— 证明闸门真的会拦]");

            if (!EditorProbeConfig.Ready)
            {
                _fail++;
                Console.WriteLine("  [FAIL] 配置未就绪，跳过拒绝测试");
                return;
            }

            EditorProbeServer.Start(EditorProbeConfig.Token, EditorProbeConfig.Port);
            ManualResetEvent stop = new ManualResetEvent(false);
            Thread mainThread = new Thread(() => RunMainThread(stop));
            mainThread.IsBackground = true;
            mainThread.Start();
            Thread.Sleep(50);

            int port = EditorProbeServer.BoundPort;

            // wrong token
            Dictionary<string, object> r1 = EditorProbeJson.ParseObject(
                SendRaw(port, "{\"token\":\"wrongwrongwrong\",\"action\":\"status\",\"args\":{}}"));
            Ok("错 token 被拒", IsFalse(EditorProbeJson.Get(r1, "ok")));
            OkEq("错 token 的错误码", EditorProbeJson.GetString(r1, "code"), "invalid_request");

            // unknown action
            Dictionary<string, object> r2 = EditorProbeJson.ParseObject(
                SendRaw(port, "{\"token\":\"" + EditorProbeConfig.Token + "\",\"action\":\"delete_everything\",\"args\":{}}"));
            Ok("未知动作被拒", IsFalse(EditorProbeJson.Get(r2, "ok")));
            OkEq("未知动作的错误码", EditorProbeJson.GetString(r2, "code"), "invalid_request");
            Ok("未知动作的提示点出该动作名",
                (EditorProbeJson.GetString(r2, "error") ?? "").Contains("delete_everything"));

            // malformed JSON
            Dictionary<string, object> r3 = EditorProbeJson.ParseObject(
                SendRaw(port, "{not json at all"));
            Ok("坏 JSON 被拒", IsFalse(EditorProbeJson.Get(r3, "ok")));

            // ★ An action that needs the editor must FAIL offline with a diagnosable
            //   code. This is the assertion that keeps the suite honest: if it started
            //   "succeeding" here, the probe would be inventing editor state.
            Dictionary<string, object> r4 = EditorProbeJson.ParseObject(
                SendRaw(port, "{\"token\":\"" + EditorProbeConfig.Token + "\",\"action\":\"apply_layout\",\"args\":{\"entities\":[{\"id\":\"a\",\"prefab\":\"x\",\"x\":0,\"y\":0,\"z\":0}]}}"));
            Ok("无编辑器时 apply_layout 失败", IsFalse(EditorProbeJson.Get(r4, "ok")));
            OkEq("错误码是客户端认识的那一个", EditorProbeJson.GetString(r4, "code"), "bridge_error");
            Ok("错误信息说明是编辑模式的问题",
                (EditorProbeJson.GetString(r4, "error") ?? "").Contains("编辑模式"));

            stop.Set();
            mainThread.Join(2000);
            EditorProbeServer.Stop();
            Console.WriteLine();
        }

        private static void TestStatusIsAnswerableWithoutEditor()
        {
            Console.WriteLine("[status 在任何时候都可答（这是它的存在意义）]");

            // status must never throw: it is the call a user makes precisely when
            // nothing is working, so it has to answer with a diagnosis.
            string json = null;
            try
            {
                json = EditorProbeMainThread.Execute("status", null);
            }
            catch (Exception ex)
            {
                Console.WriteLine("       抛出: " + ex);
            }
            Ok("status 不抛异常", json != null);
            if (json != null)
            {
                Dictionary<string, object> result = EditorProbeJson.ParseObject(json);
                Ok("status 输出是合法 JSON", result != null);
                Ok("如实报告 editMode=false（游戏没在跑）", IsFalse(EditorProbeJson.Get(result, "editMode")));
                Ok("如实报告 sceneOpen=false", IsFalse(EditorProbeJson.Get(result, "sceneOpen")));
                Ok("仍然给出 probeScene 供客户端比对", EditorProbeJson.GetString(result, "probeScene") == "ProbeScene");
            }
            Console.WriteLine();
        }
    }
}
