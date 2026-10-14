using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace BlBridge.EditorProbe
{
    /// <summary>
    /// Editor bridge server: a loopback-only, token-authenticated, single-request
    /// JSON-lines endpoint for the archive's editor MCP client.
    ///
    /// ─────────────────────────────────────────────────────────────────────────
    /// WHY THIS IS A SEPARATE FILE FROM BlBridge.CommandPump
    ///
    /// BlBridge talks over FILES (commands/pending -> commands/done) because that
    /// is all a client-side-only module can rely on. The editor client speaks
    /// TCP on 127.0.0.1, and the contract is already fixed (see
    /// BannerlordSage.EditorProbe expectations in the archive repo):
    ///
    ///   request : {"token":"<session token>","action":"<name>","args":{...}}\n
    ///   reply   : {"ok":true|false,"result":{...},"mainThread":N,"executedThread":M}\n
    ///
    /// A MESSAGE MAY BE ONE LINE, and is one line in both directions. The client
    /// rejects anything it cannot split on a single '\n', so a reply is written and
    /// flushed as ONE buffer — never as several Write() calls that could interleave
    /// with another request's reply.
    ///
    /// TWO THREADS, ONE RULE
    ///
    ///   - The accept/socket thread (this file) does I/O only. It never touches
    ///     TaleWorlds types, because the engine is not thread-safe and calls from a
    ///     foreign thread corrupt state or crash outright.
    ///   - Game state is touched only on the main thread, by EditorProbeMainThread,
    ///     which the module's OnApplicationTick drives.
    ///
    /// The reply reports `mainThread` (the OS thread id the engine ticks on) and
    /// `executedThread` (the thread that actually ran the action). The client
    /// compares them and raises `wrong_thread` if they differ, so these fields are
    /// a contract, not diagnostics: getting them wrong makes working calls look
    /// broken.
    /// </summary>
    internal static class EditorProbeServer
    {
        /// <summary>Loopback only. Binding anything else would expose the editor to the network.</summary>
        private const string BindAddress = "127.0.0.1";

        /// <summary>Matches the client's cap so an oversized request is refused here too, not just there.</summary>
        private const int MaxRequestBytes = 16384;

        /// <summary>Enough for any reply this probe produces; the client rejects oversized replies.</summary>
        private const int MaxReplyBytes = 1024 * 1024;

        private static TcpListener _listener;
        private static Thread _acceptThread;
        private static volatile bool _running;
        private static int _port;

        /// <summary>The OS thread the engine ticks on. Captured on first main-thread entry.</summary>
        private static int _mainThreadId;

        private static readonly object Gate = new object();

        /// <summary>
        /// Requests waiting to be executed on the main thread. Only ever touched
        /// under <see cref="Gate"/>: written by the socket thread, drained by the
        /// main thread.
        /// </summary>
        private static readonly Queue<EditorProbeRequest> Queue = new Queue<EditorProbeRequest>();

        internal static int Port { get { return _port; } }

        internal static bool Running { get { return _running; } }

        /// <summary>Port actually bound, or 0 when not running.</summary>
        internal static int BoundPort { get; private set; }

        /// <summary>
        /// Called from the main thread once per frame so the probe can learn which
        /// thread id the engine owns. Cheap: a volatile read after the first frame.
        /// </summary>
        internal static void ObserveMainThread()
        {
            if (_mainThreadId == 0)
                _mainThreadId = Thread.CurrentThread.ManagedThreadId;
        }

        internal static int MainThreadId { get { return _mainThreadId; } }

        /// <summary>
        /// Start listening. Safe to call repeatedly; a second call is a no-op.
        /// Returns false when the port is taken or the socket cannot be created —
        /// the caller surfaces that as `disconnected` rather than pretending.
        /// </summary>
        internal static bool Start(string token, int port)
        {
            lock (Gate)
            {
                if (_running) return true;
                if (string.IsNullOrEmpty(token) || token.Length < 8) return false;
                try
                {
                    _listener = new TcpListener(IPAddress.Parse(BindAddress), port);
                    _listener.Start();
                    _port = port;
                    BoundPort = ((IPEndPoint)_listener.LocalEndpoint).Port;
                    _running = true;
                    _acceptThread = new Thread(AcceptLoop);
                    _acceptThread.IsBackground = true;   // must never keep the process alive
                    _acceptThread.Name = "EditorProbe.Accept";
                    _acceptThread.Start();
                    return true;
                }
                catch
                {
                    _listener = null;
                    _port = 0;
                    BoundPort = 0;
                    _running = false;
                    return false;
                }
            }
        }

        internal static void Stop()
        {
            lock (Gate)
            {
                _running = false;
                try
                {
                    if (_listener != null) _listener.Stop();
                }
                catch
                {
                }
                _listener = null;
                _acceptThread = null;
                Queue.Clear();
            }
        }

        private static void AcceptLoop()
        {
            while (_running)
            {
                TcpClient client = null;
                try
                {
                    TcpListener listener = _listener;
                    if (listener == null) break;
                    client = listener.AcceptTcpClient();
                }
                catch
                {
                    // Stop() closes the listener, which lands here. Loop condition
                    // then ends the thread. Any other failure also just ends it.
                    if (!_running) break;
                    continue;
                }

                // Handled inline rather than in a thread pool: the client sends one
                // request and waits, so concurrency here buys nothing and costs the
                // certainty of a single writer per socket.
                try
                {
                    ServeOne(client);
                }
                catch
                {
                    // A single bad connection must never take the listener down.
                }
                finally
                {
                    try { client.Close(); } catch { }
                }
            }
        }

        private static void ServeOne(TcpClient client)
        {
            client.ReceiveTimeout = 15000;
            client.SendTimeout = 15000;

            using (NetworkStream stream = client.GetStream())
            {
                string line = ReadLine(stream, MaxRequestBytes);
                string reply = line == null
                    ? Reply(false, null, "invalid_request", "请求为空或超过上限")
                    : DispatchLine(line);
                byte[] bytes = Encoding.UTF8.GetBytes(reply + "\n");
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush();
            }
        }

        /// <summary>
        /// Reads up to and excluding the first '\n'. Returns null when the peer
        /// closed first or the cap is exceeded, so the caller answers with a single
        /// shape instead of guessing.
        /// </summary>
        private static string ReadLine(NetworkStream stream, int maxBytes)
        {
            MemoryStream buffer = new MemoryStream();
            byte[] chunk = new byte[4096];
            while (true)
            {
                int read;
                try
                {
                    read = stream.Read(chunk, 0, chunk.Length);
                }
                catch
                {
                    return null;
                }
                if (read <= 0) return null;

                for (int i = 0; i < read; i++)
                {
                    if (chunk[i] == (byte)'\n')
                    {
                        return Encoding.UTF8.GetString(buffer.ToArray());
                    }
                    buffer.WriteByte(chunk[i]);
                }
                if (buffer.Length > maxBytes) return null;
            }
        }

        /// <summary>
        /// Validates the envelope, then blocks the socket thread until the main
        /// thread has run the action. Blocking here is the point: the client sent
        /// one request and is waiting for one reply, and the client's own timeout
        /// (50s) is the deadline.
        /// </summary>
        private static string DispatchLine(string line)
        {
            Dictionary<string, object> envelope;
            try
            {
                envelope = EditorProbeJson.ParseObject(line);
            }
            catch
            {
                return Reply(false, null, "invalid_request", "请求不是合法 JSON 对象");
            }
            if (envelope == null)
                return Reply(false, null, "invalid_request", "请求不是合法 JSON 对象");

            string token = EditorProbeJson.GetString(envelope, "token");
            string action = EditorProbeJson.GetString(envelope, "action");
            object rawArgs = EditorProbeJson.Get(envelope, "args");

            if (string.IsNullOrEmpty(token) || token != EditorProbeConfig.Token)
                return Reply(false, null, "invalid_request", "token 不匹配");

            if (string.IsNullOrEmpty(action) || !EditorProbeActions.IsKnown(action))
                return Reply(false, null, "invalid_request", "未知 action：" + action);

            EditorProbeRequest request = new EditorProbeRequest
            {
                Action = action,
                Args = rawArgs as Dictionary<string, object>,
                Done = new ManualResetEvent(false),
            };

            lock (Gate)
            {
                if (!_running)
                    return Reply(false, null, "disconnected", "编辑器探针未运行");
                Queue.Enqueue(request);
            }

            // 50s == the client's EDITOR_REQUEST_TIMEOUT_MS. Waiting a little less
            // means we answer with `timeout` instead of the client giving up first,
            // which turns a silent hang into a diagnosable reply.
            if (!request.Done.WaitOne(45000))
                return Reply(false, null, "timeout", "主线程未在期限内执行该操作（可能仍在上一次操作中）");

            if (request.Exception != null)
                return Reply(false, null, "bridge_error", request.Exception.Message);

            return Reply(true, request.ResultJson, null, null, request.ExecutedThreadId);
        }

        /// <summary>
        /// Called by the main thread each frame. Drains and executes the queue.
        /// Never throws: one failing action must not stop the frame pump.
        /// </summary>
        internal static void DrainOnMainThread()
        {
            ObserveMainThread();

            while (true)
            {
                EditorProbeRequest request;
                lock (Gate)
                {
                    if (Queue.Count == 0) return;
                    request = Queue.Dequeue();
                }

                try
                {
                    request.ExecutedThreadId = Thread.CurrentThread.ManagedThreadId;
                    request.ResultJson = EditorProbeMainThread.Execute(request.Action, request.Args);
                }
                catch (Exception ex)
                {
                    request.Exception = ex;
                }
                finally
                {
                    try { request.Done.Set(); } catch { }
                }
            }
        }

        /// <summary>
        /// Builds the reply envelope. `mainThread` and `executedThread` are always
        /// present on success because the client treats their absence as a protocol
        /// violation (it compares them and reports wrong_thread when they differ).
        /// </summary>
        private static string Reply(bool ok, string resultJson, string code, string error, int executedThreadId = 0)
        {
            StringBuilder sb = new StringBuilder(256);
            sb.Append("{\"ok\":").Append(ok ? "true" : "false");
            if (ok)
            {
                sb.Append(",\"result\":").Append(resultJson ?? "{}");
                // executedThreadId is passed in by the caller that drained the
                // queue. Falling back to the current thread keeps error paths sane,
                // but the success path always has the real executor.
                int executed = executedThreadId != 0 ? executedThreadId : Thread.CurrentThread.ManagedThreadId;
                sb.Append(",\"mainThread\":").Append(_mainThreadId.ToString(CultureInfo.InvariantCulture));
                sb.Append(",\"executedThread\":").Append(executed.ToString(CultureInfo.InvariantCulture));
                // Set only when the action really did not run on the engine thread,
                // which the client treats as wrong_thread. Reporting this honestly
                // matters: a false positive would make working calls look broken.
                if (_mainThreadId != 0 && executed != _mainThreadId)
                {
                    sb.Append(",\"offMainThread\":true");
                }
            }
            else
            {
                sb.Append(",\"code\":\"").Append(EditorProbeJson.Escape(code ?? "bridge_error")).Append("\"");
                sb.Append(",\"error\":\"").Append(EditorProbeJson.Escape(error ?? "未知错误")).Append("\"");
            }
            sb.Append('}');
            return sb.ToString();
        }
    }

    /// <summary>One queued action, plus the gate its reply waits on.</summary>
    internal sealed class EditorProbeRequest
    {
        internal string Action;
        internal Dictionary<string, object> Args;
        internal ManualResetEvent Done;
        internal string ResultJson;
        internal Exception Exception;

        /// <summary>
        /// The thread that actually ran the action. Recorded here because the
        /// reply is assembled on the SOCKET thread after the work is done —
        /// reading Thread.CurrentThread there would report the socket thread and
        /// make every successful call look like it ran off the main thread.
        /// </summary>
        internal int ExecutedThreadId;
    }
}
