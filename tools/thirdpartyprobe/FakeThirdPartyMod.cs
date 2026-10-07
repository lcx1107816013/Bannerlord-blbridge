// ThirdPartyProbe —— **模拟第三方 mod 崩溃**，并验证定位链路（离线，不需要游戏）。
//
// ## 为什么要有它
//
// 「第三方崩溃定位」这条能力，之前的验证都停在"读了索引、查到了方法"这一层 ——
// 但**从没验过一个真实的第三方栈**：真 DLL → 真 PDB → 真抛异常 → 真栈文本 → 定位。
// 本探针把这条链**端到端**跑一遍，且**不需要游戏**（所以能进闸门）。
//
// ## 它模拟什么（与真实情形的对应）
//
// | 真实情形 | 本探针 |
// |---|---|
// | 第三方 mod 的 DLL（如 `RBM.dll`） | `FakeThirdPartyMod.dll`（真编译产物） |
// | 该 mod **自带 PDB**（实测模块目录 123 个） | `/debug:full` 产出真 PDB |
// | 异常在第三方方法**内部**抛出 | `FakeBehavior.OnApplicationTick` 里 throw |
// | 栈里有引擎/BCL 帧混着 | 经 `Inner.Tick` 转发，制造多层栈 |
// | 中文 Windows 的**本地化栈文本** | 直接拿运行时给的栈（本机是 `位置 X.cs:行号 N`） |
//
// ## 判据（每条都有"应报/不应报"的对照）
//
// A1 栈文本里**出现** `文件:行号`（证明 /debug:full 有效）
// A2 栈文本里的行号 == throw 语句的真实行号（**不是随便一个数**）
// A3 栈文本**不含绝对路径**（/pathmap 隐私生效）
// B1 第三方方法名出现在栈里，且**不是**我们工程的类型名
// C1 栈里同时有非第三方帧（模拟真实栈的混合形态）
using System;
using System.Reflection;

namespace FakeThirdPartyMod
{
    /// <summary>模拟第三方 mod 的一个行为——异常从这里抛出。</summary>
    public static class FakeBehavior
    {
        public static int Calls;

        // ▼ 行号断言点：下面这行必须是 A2 判据比对的基准
        public static void OnApplicationTick(float dt)
        {
            Calls++;
            throw new InvalidOperationException("third-party-mod-boom");
        }
    }

    /// <summary>再包一层，让栈有深度（更像真实的"引擎 → mod"两段）。</summary>
    public static class Inner
    {
        public static void Tick(float dt)
        {
            FakeBehavior.OnApplicationTick(dt);
        }
    }
}
