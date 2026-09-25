using TaleWorlds.Core;
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    /// <summary>
    /// 「兜底观战镜头」：实现官方的 <see cref="ICameraModeLogic"/>，把镜头交给引擎自带的自由观察相机
    /// （`SpectatorCameraTypes.Free`）。
    ///
    /// ⚠️ v0.8.15 起它的定位是**兜底**，不是主方案。原因（2026-09-25 真机插桩取证，见 PROGRESS §二十六/§二十七）：
    ///   1) `MissionScreen.cs:396` 从所有 mission behavior 里 `FirstOrDefault(b =&gt; b is ICameraModeLogic)`
    ///      —— **下标最小者胜出**；
    ///   2) 用户装了 **RTSCamera**（`FlyCameraMissionView`，来自 `RTSCamera.dll`），它在野战里排在下标 30、
    ///      我们的在 43 ⇒ **野战里根本轮不到我们**（我们的方法只被问了 8 次 = 初始化期探测）；
    ///   3) 围城里轮到我们（8511 次/场），但给出的是**观察者镜头**（锁 agent、带"上一个/下一个角色"），
    ///      并不是用户想要的抬升自由视角 —— 那件事由 RTSCamera 的配置决定
    ///      （`ElevatedHeightInSiege` 默认 0 = 关闭）。
    /// ⇒ 结论：**不要跟 RTSCamera 抢相机**。装了它就走它的配置（我们只当参数管理员），
    ///   本类只在"没装 RTSCamera 的机器"上启用（见 SubModule 里的 IsModuleActive 判断）。
    ///
    /// 官方依据：`ICameraModeLogic.GetMissionCameraLockMode(bool lockedToMainPlayer)`（单方法接口，TaleWorlds.Core）；
    /// `SpectatorCameraTypes.Free = 0`；官方先例 `TournamentBehavior.cs:81-87`。
    /// </summary>
    internal class SpectatorWatchBehavior : MissionLogic, ICameraModeLogic
    {
        /// <summary>本场被询问的次数（只在收尾时记一条日志，便于判断到底谁在管镜头）。</summary>
        private int _queries;

        public SpectatorCameraTypes GetMissionCameraLockMode(bool lockedToMainPlayer)
        {
            _queries++;
            return SpectatorCameraTypes.Free;
        }

        public override void OnRemoveBehavior()
        {
            try
            {
                UiEntry.Log("SpectatorWatch(兜底): 结束，共被询问 " + _queries + " 次"
                            + "（被问=本类确实是引擎选中的那个 ICameraModeLogic）");
            }
            catch
            {
            }
            base.OnRemoveBehavior();
        }
    }
}
