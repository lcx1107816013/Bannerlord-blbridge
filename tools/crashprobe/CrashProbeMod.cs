// ZzCrashProbe —— **故意崩的"第三方 mod"**，用于真机验收第三方崩溃定位。
//
// ## 它为什么存在
//
// `bl_source_map` 的第三方能力此前只有**离线**验证（真 DLL/PDB/栈，但不在游戏进程里）。
// 本模块把那次崩溃搬进**真实游戏进程**，从而验证：
//   ① 第三方 mod 在 tick 里抛异常，栈顶确实是**第三方方法**；
//   ② 该栈能被 ExceptionProbe 落盘、再被 bl_source_map 定位到 `文件:行号`；
//   ③ （附带）CrashGuard 能吞掉**真实的第三方异常**（不只是我们自己造的）。
//
// ## 设计要点
//
// - **只抛一次**：连续抛会触发 CrashGuard 的熔断（同签名 >20 次 ⇒ 停止吞 ⇒ 游戏崩），
//   那样测试就变成"熔断是否生效"了，偏离本探针的目的。
// - **等游戏就绪再抛**：默认在模块加载后 N 秒（可从旁边 `throw_after_seconds.txt` 改），
//   确保 BlBridge 已写好 status、ExceptionProbe/CrashGuard 已装好。
// - **异常消息带固定标记** `ZzCrashProbe`，便于在日志里精确定位本次事件。
// - **不改任何游戏逻辑**：只在自己的 tick 里抛异常。
using System;
using System.Globalization;
using System.IO;
using TaleWorlds.MountAndBlade;

namespace ZzCrashProbe
{
    public class SubModule : MBSubModuleBase
    {
        /// <summary>默认等多少秒后抛（够游戏加载完 + BlBridge 装好钩子）。</summary>
        private const double DefaultThrowAfterSeconds = 25.0;

        private static bool _thrown;
        private static double _elapsed;
        private static double _throwAfter = DefaultThrowAfterSeconds;
        private static bool _configRead;

        protected override void OnSubModuleLoad()
        {
            base.OnSubModuleLoad();
            ReadConfig();
        }

        /// <summary>
        /// 从模块目录旁的 `throw_after_seconds.txt` 读延时（**纯文件，不联网、不写盘**）。
        /// 读不到就用默认值 —— 不因为配置问题而改变行为。
        /// </summary>
        private static void ReadConfig()
        {
            if (_configRead) return;
            _configRead = true;
            try
            {
                string here = Path.GetDirectoryName(typeof(SubModule).Assembly.Location);
                if (string.IsNullOrEmpty(here)) return;
                string p = Path.Combine(here, "throw_after_seconds.txt");
                if (!File.Exists(p)) return;
                string txt = File.ReadAllText(p).Trim();
                double v;
                if (double.TryParse(txt, NumberStyles.Float, CultureInfo.InvariantCulture, out v)
                    && v >= 1.0 && v <= 600.0)
                {
                    _throwAfter = v;
                }
            }
            catch
            {
                // 配置读不到就用默认值 —— 绝不让配置问题变成"没崩"或"乱崩"
            }
        }

        /// <summary>
        /// 抛异常点。**这是本探针唯一的目的**：
        /// 让一个托管异常从**第三方 mod 自己的方法**里抛出，从而在栈里留下第三方帧。
        ///
        /// 为什么放在 tick 而不是 OnSubModuleLoad：
        ///   加载期抛会让游戏启动失败，我们拿不到"游戏已就绪"的上下文；
        ///   而 tick 里抛，栈会经过 `Module.OnApplicationTick → Managed.ApplicationTick`，
        ///   正是 CrashGuard 挂 Finalizer 的那条链 —— 于是能同时验证"吞掉"与"定位"。
        /// </summary>
        protected override void OnApplicationTick(float dt)
        {
            base.OnApplicationTick(dt);
            if (_thrown) return;
            _elapsed += dt;
            if (_elapsed < _throwAfter) return;
            _thrown = true;

            // ▼ 行号断言点：真机验收会把这个行号与 bl_source_map 的输出比对
            throw new InvalidOperationException(
                "ZzCrashProbe intentional third-party crash ("
                + _elapsed.ToString("F1", CultureInfo.InvariantCulture) + "s after load)");
        }
    }
}
