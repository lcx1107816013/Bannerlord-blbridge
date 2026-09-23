using System;
using System.Globalization;
using System.IO;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// 可选的外部配置文件：&lt;LogDir&gt;\blbridge_game.json（C24）。
    ///
    /// 解决的问题（"配置漂移"）：v0.4.0 之前 `Enabled` / `SampleIntervalSeconds` / `FlushEveryLine`
    /// 全是 C# 常量 —— 想把采样间隔从 10 秒改成 3 秒，得改代码、重新编译、重新部署 DLL、
    /// 重启游戏。而"重新编译部署"本身又是一次引入不一致的机会。
    ///
    /// 设计取舍（照 Coop 的 "配置即校验 + 显式重启" 语义）：
    ///   • **加载即校验**：越界/类型不对的项直接忽略并记录原因，不静默接受、也不让整份配置失效；
    ///   • **不热重载**：只在模块加载时读一次；改完重启游戏（与游戏读 config.xml 的语义一致）；
    ///   • **来历可查**：`EffectiveJson()` 会说明每个值是被配置文件覆盖的、还是没有配置文件时的默认值，
    ///     避免"我明明改了但没生效"这种无法定位的状态。
    /// </summary>
    internal static class BridgeConfigFile
    {
        public const string FileName = "blbridge_game.json";
        public const long MaxBytes = 64 * 1024;

        public static bool Loaded;
        public static string Errors = "";
        public static string FilePath = "";

        public static string DefaultPath
        {
            get { return Path.Combine(BridgeConfig.LogDir, FileName); }
        }

        /// <summary>读取并应用配置；返回错误/提示文本（空串 = 文件不存在或全部正常）。</summary>
        public static string Apply(string path)
        {
            Loaded = false;
            Errors = "";
            FilePath = path;
            try
            {
                if (string.IsNullOrEmpty(path) || !File.Exists(path)) return "";
                FileInfo fi = new FileInfo(path);
                if (fi.Length > MaxBytes)
                {
                    Errors = "配置文件超过 64 KiB，整份忽略";
                    return Errors;
                }

                string raw = File.ReadAllText(path, Encoding.UTF8);
                StringBuilder errs = new StringBuilder();
                bool any = false;

                if (Jmini.Has(raw, "enabled"))
                {
                    BridgeConfig.Enabled = Jmini.Bool(raw, "enabled", BridgeConfig.Enabled);
                    any = true;
                }

                if (Jmini.Has(raw, "flushEveryLine"))
                {
                    BridgeConfig.FlushEveryLine = Jmini.Bool(raw, "flushEveryLine", BridgeConfig.FlushEveryLine);
                    any = true;
                }

                if (Jmini.Has(raw, "sampleIntervalSeconds"))
                {
                    double v = Jmini.Num(raw, "sampleIntervalSeconds", double.NaN);
                    if (double.IsNaN(v) || v < 1.0 || v > 600.0)
                    {
                        errs.Append("sampleIntervalSeconds 必须在 1~600 秒之间，已忽略；");
                    }
                    else
                    {
                        BridgeConfig.SampleIntervalSeconds = (float)v;
                        any = true;
                    }
                }

                if (Jmini.Has(raw, "maxRequestAgeSeconds"))
                {
                    double v = Jmini.Num(raw, "maxRequestAgeSeconds", double.NaN);
                    if (double.IsNaN(v) || v < 5.0 || v > 3600.0)
                    {
                        errs.Append("maxRequestAgeSeconds 必须在 5~3600 秒之间，已忽略；");
                    }
                    else
                    {
                        BridgeConfig.MaxRequestAgeSeconds = (int)v;
                        any = true;
                    }
                }

                Loaded = any;
                Errors = errs.ToString();
                return Errors;
            }
            catch (Exception ex)
            {
                Errors = "读取失败：" + ex.GetType().Name + "：" + ex.Message;
                return Errors;
            }
        }

        /// <summary>状态文件里的 config 块：让外部看清"每个值是谁给的"。</summary>
        public static string EffectiveJson()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"file\":\"").Append(Jw.Esc(FilePath ?? DefaultPath)).Append('"');
            sb.Append(",\"loaded\":").Append(Jw.B(Loaded));
            sb.Append(",\"enabled\":").Append(Jw.B(BridgeConfig.Enabled));
            sb.Append(",\"flushEveryLine\":").Append(Jw.B(BridgeConfig.FlushEveryLine));
            sb.Append(",\"sampleIntervalSeconds\":").Append(Jw.N(BridgeConfig.SampleIntervalSeconds));
            sb.Append(",\"maxRequestAgeSeconds\":").Append(Jw.N(BridgeConfig.MaxRequestAgeSeconds));
            sb.Append(",\"errors\":\"").Append(Jw.Esc(Errors)).Append('"');
            sb.Append(",\"note\":\"").Append(Jw.Esc(Loaded
                ? "以上值已被配置文件覆盖（改完需重启游戏生效）"
                : "未找到配置文件或其中没有可识别的键，当前为内置默认值")).Append('"');
            sb.Append('}');
            return sb.ToString();
        }
    }
}
