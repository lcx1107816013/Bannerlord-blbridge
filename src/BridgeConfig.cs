using System;
using System.IO;

namespace BlBridge
{
    /// <summary>
    /// 全局设置：日志目录、开关。全部走文件 IPC，不联网。
    /// 日志写到「我的文档\Mount and Blade II Bannerlord\BlBridge\」，
    /// 便于外部 MCP/分析器直接读取，且不污染游戏 Modules 目录。
    /// </summary>
    internal static class BridgeConfig
    {
        public const string Version = "0.8.46";
        public const string ModuleId = "BlBridge";

        /// <summary>
        /// 过期请求阈值（秒）：请求里的 issuedUtc 超过这个年龄就直接作废、不执行。
        /// 依据：MCP 侧命令超时最长 60s（start_battle），留 2 倍余量。
        /// 离开这个值会导致"MCP 超时退出、游戏后启动时把旧请求执行掉"（幽灵战斗）。
        /// 可被 &lt;LogDir&gt;\blbridge_game.json 覆盖（见 BridgeConfigFile）。
        /// </summary>
        public static int MaxRequestAgeSeconds = 120;

        /// <summary>总开关。设为 false 时本模块什么都不记录。</summary>
        public static bool Enabled = true;

        /// <summary>每次命中/阵亡都落盘并 flush（崩溃也不丢数据）。</summary>
        public static bool FlushEveryLine = true;

        /// <summary>战斗内每隔多少秒写一条 sample（存活人数/血量）。可被 blbridge_game.json 覆盖。</summary>
        public static float SampleIntervalSeconds = 10f;

        /// <summary>
        /// v0.7.9：**agent 状态采样**间隔（秒）。0 = 关闭。
        /// 采样内容：位置 / 速度 / 移速倍率 / 负重 / 士气 / AI 状态 / 装弹 / 弹药。
        /// 一个事件同时回答"移速、装弹、士气、弹药"四类问题。
        /// </summary>
        public static float StateIntervalSeconds = 2f;

        /// <summary>
        /// v0.7.9：每次状态采样最多采几个 agent（按索引取前 N）。
        /// 防止 400v400 那种规模把日志撑爆（默认 40 ≈ 20v20 全覆盖）。
        /// </summary>
        public static int StateMaxAgents = 40;

        /// <summary>
        /// v0.8.42：动作账本（`commands/actions.jsonl`）的轮转阈值（字节）。**0 = 不轮转（默认）**。
        ///
        /// 默认 0 是刻意的：账本是过程记录，但**默认删除用户磁盘上的文件是另一回事**。
        /// 想让它自动瘦身，在 `blbridge_game.json` 里显式写 `maxActionLogBytes`。
        /// 对照：Bannerlord.GameMaster 的 `CommandLogger` 是**无条件**只留 5 个日志文件 ——
        /// 那是它的产品语境（控制台日志），不是我们的（实验数据）。
        /// </summary>
        public static int MaxActionLogBytes = 0;

        /// <summary>v0.8.42：轮转时保留的归档个数（只在 `MaxActionLogBytes &gt; 0` 时生效）。</summary>
        public static int ActionLogKeepFiles = 5;

        private static string _logDir;

        public static string LogDir
        {
            get
            {
                if (_logDir == null)
                {
                    string docs = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
                    _logDir = Path.Combine(Path.Combine(docs, "Mount and Blade II Bannerlord"), ModuleId);
                }
                return _logDir;
            }
        }

        public static string BattlesDir
        {
            get { return Path.Combine(LogDir, "battles"); }
        }

        public static string StatusPath
        {
            get { return Path.Combine(LogDir, "bridge_status.json"); }
        }

        public static void EnsureDirs()
        {
            try
            {
                if (!Directory.Exists(LogDir)) Directory.CreateDirectory(LogDir);
                if (!Directory.Exists(BattlesDir)) Directory.CreateDirectory(BattlesDir);
            }
            catch
            {
            }
        }
    }
}
