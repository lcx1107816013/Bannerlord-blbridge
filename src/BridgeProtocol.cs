using System;
using System.Diagnostics;
using System.Globalization;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// BlBridge 控制通道协议（v1）。
    ///
    /// 设计规范抄自 Bannerlord Coop 团队的 CoopMcpServer / LiveTestProtocol（见尽调报告 §3）：
    ///   1. 信封：请求 {protocolVersion, id, method, parameters}；响应 {protocolVersion, id, ok, process, result, error}
    ///   2. 响应不变式：ok==true ⇔ error==null；process 必填
    ///   3. 进程身份：pid + runToken + processStartedUtc，用于识别"同一 PID 被复用前后"的不同会话
    ///   4. 版本硬校验：protocolVersion 不匹配直接拒绝（unsupported_version），不猜
    ///   5. 错误对象带 outcomeUncertain：为 true 时**绝不盲目重试**
    /// 传输：本地文件（pending/ → done/），无网络、无端口、无 URL ACL。
    /// </summary>
    internal static class Protocol
    {
        public const int Version = 1;

        public static readonly string ProcessStartedUtc = DateTime.UtcNow.ToString("o");

        public static readonly string RunToken = Guid.NewGuid().ToString("N").Substring(0, 12);

        private static int _pid = -2;

        public static int Pid
        {
            get
            {
                if (_pid == -2)
                {
                    try
                    {
                        _pid = Process.GetCurrentProcess().Id;
                    }
                    catch
                    {
                        _pid = -1;
                    }
                }
                return _pid;
            }
        }

        /// <summary>process 身份块（每个响应都要带）。</summary>
        public static string ProcessBlock()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"pid\":").Append(Pid.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\"role\":\"game\"");
            sb.Append(",\"runToken\":\"").Append(Jw.Esc(RunToken)).Append('"');
            sb.Append(",\"processStartedUtc\":\"").Append(Jw.Esc(ProcessStartedUtc)).Append('"');
            // 构建身份：让"进程里跑的是哪个 DLL"可被外部核验（照 Coop 在 process 块里带构建证据）
            sb.Append(",\"assemblyVersion\":\"").Append(Jw.Esc(BuildInfo.AssemblyVersion)).Append('"');
            sb.Append(",\"mvid\":\"").Append(Jw.Esc(BuildInfo.Mvid)).Append('"');
            sb.Append(",\"loadedSha256\":\"").Append(Jw.Esc(BuildInfo.LoadedSha256Short)).Append('"');
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>成功响应：error 必须为 null。</summary>
        public static string Success(string id, string resultJson)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"protocolVersion\":").Append(Version.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\"id\":\"").Append(Jw.Esc(id)).Append('"');
            sb.Append(",\"ok\":true");
            sb.Append(",\"process\":").Append(ProcessBlock());
            sb.Append(",\"result\":").Append(resultJson ?? "null");
            sb.Append(",\"error\":null}");
            return sb.ToString();
        }

        /// <summary>失败响应：ok==false 时 error 必须非 null。</summary>
        public static string Failure(string id, string code, string message, bool outcomeUncertain)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"protocolVersion\":").Append(Version.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\"id\":\"").Append(Jw.Esc(id)).Append('"');
            sb.Append(",\"ok\":false");
            sb.Append(",\"process\":").Append(ProcessBlock());
            sb.Append(",\"result\":null");
            sb.Append(",\"error\":{\"code\":\"").Append(Jw.Esc(code)).Append('"');
            sb.Append(",\"message\":\"").Append(Jw.Esc(message ?? "")).Append('"');
            sb.Append(",\"outcomeUncertain\":").Append(Jw.B(outcomeUncertain));
            sb.Append("}}");
            return sb.ToString();
        }

        /// <summary>把任意字符串安全地包成 JSON 字符串字面量（含引号）。</summary>
        public static string Q(string s)
        {
            return "\"" + Jw.Esc(s ?? "") + "\"";
        }
    }
}
