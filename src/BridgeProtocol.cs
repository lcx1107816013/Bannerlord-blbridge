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

        /// <summary>
        /// v0.8.45：把"从已组装好的响应里反读账本所需字段"这件事**集中到一处**。
        ///
        /// 为什么要有它（缺口 B 的修法，2026-10-05）：`CommandPump.RecordLedger` 原来直接
        /// `Jmini.Bool(response,"ok",false)` —— 兜底是 **false**，于是一个**成功**的请求只要
        /// 响应读不出来（截断/写坏/无 `ok` 键），就会被账本记成**失败**，且与真失败**无法区分**。
        ///
        /// 抽到这里还有个**测试上的理由**：`CommandPump` 依赖 TaleWorlds，**离线编不进来**，
        /// 所以离线断言若自己抄一份判定逻辑，就成了"验副本不验出货代码"（违反项目纪律）。
        /// 本类是**纯 BCL**、已被 `tools/jsontest` 编入 ⇒ 出货代码与断言**调同一个方法**。
        /// </summary>
        /// <param name="readable">响应里**存在** `ok` 键（= Jmini 反读的前提成立）</param>
        /// <param name="ok">`ok` 的值（`readable==false` 时无意义，恒 false）</param>
        /// <param name="code">失败码；`readable==false` 时是哨兵 `ledger_unreadable`</param>
        /// <param name="uncertain">`outcomeUncertain`（不可读时恒 false —— 不能凭空说"不确定"）</param>
        /// <param name="note">一句话摘要（不可读时说明"只表示读不出来"）</param>
        public static void ReadResponseOutcome(string response, out bool readable, out bool ok,
            out string code, out bool uncertain, out string note)
        {
            readable = Jmini.Has(response, "ok");
            ok = Jmini.Bool(response, "ok", false);
            if (!readable)
            {
                // 刻意**不**复用 ok=false 的语义：读不出来 ≠ 请求失败。给一个专属哨兵码，
                // 让下游能一眼把"账本读不到响应"与"真的失败了"分开。
                code = "ledger_unreadable";
                uncertain = false;
                note = "响应无法反读（长度 " + (response == null ? 0 : response.Length)
                       + "）；该行 ok=false 只表示「读不出来」，不代表请求失败";
                return;
            }
            code = ok ? "" : Jmini.Str(response, "code", "");
            uncertain = Jmini.Bool(response, "outcomeUncertain", false);
            note = ok
                ? ("state=" + Jmini.Str(response, "state", ""))
                : Jmini.Str(response, "message", "");
        }
    }
}
