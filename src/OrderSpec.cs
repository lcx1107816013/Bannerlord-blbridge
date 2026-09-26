using System;
using System.Globalization;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// 改令通道（`BattleOrders`）的名字表与校验器 —— **只依赖 BCL**，所以能进 `tools/jsontest` 离线单测。
    ///
    /// 为什么值得单独一个文件：这些校验**必须在碰 `MovementOrder` 之前**跑完。
    /// `MovementOrder` 是 struct，其静态字段在类型初始化时构造；在 mission 之外被碰会抛
    /// `TypeInitializationException` 并让该类型在同进程内**永久**不可用（真机教训见
    /// `ScenarioRunner.cs` 的 `MapMovement` 注释）。⇒ "先校验、后触碰"是安全边界的一部分，
    /// 而 `BattleOrders.cs` 碰 TaleWorlds 类型、编不进离线单测，所以把边界挪到这里，让它有对照断言。
    ///
    /// 与 `SquadSpec`（开战 DSL 那份名字表）的关系：**同集合**，`GuardTest` 有断言锁住
    /// （DSL 与运行时改令必须认同一套 movement 拼写，否则"开战能写、中途改不了"这种漂移会很难查）。
    /// </summary>
    internal static class OrderSpec
    {
        /// <summary>
        /// 可指定的编队：`FormationClass` 的 5 个**默认**编队（0~4）。
        /// 别名（HeavyInfantry / LightCavalry / …）与这几个**同值**，所以不收 —— 两套名字反而会漂移。
        /// </summary>
        internal static readonly string[] FormationNames = new string[] {
            "Infantry", "Ranged", "Cavalry", "HorseArcher", "Skirmisher"
        };

        /// <summary>
        /// 坐标分量绝对值上限（米）。官方战场/攻城场景一般在 ±1000 m 内，给 10000 已经很宽；
        /// 超出 ⇒ 几乎肯定是少打一个小数点或单位搞错 ⇒ **拒**，不静默夹取。
        /// </summary>
        internal const float PositionLimit = 10000f;

        /// <summary>允许的 movement（与 `SquadSpec.Movements` 同集合，见类注释）。</summary>
        internal static readonly string[] MovementNames = new string[] {
            "charge", "advance", "fallback", "stop", "retreat"
        };

        /// <summary>
        /// 可下发的编队阵列（对应引擎 `ArrangementOrder.ArrangementOrderEnum`，全小写）。
        /// 引擎那套名字是 Line/ShieldWall/Circle/Square/Skein/Column/Loose/Scatter（见 `ArrangementOrder.cs` 的静态字段）。
        /// </summary>
        internal static readonly string[] ArrangementNames = new string[] {
            "line", "shieldwall", "circle", "square", "skein", "column", "loose", "scatter"
        };

        /// <summary>
        /// 可下发的射击纪律 —— 引擎**只有两个**（`FiringOrder.FiringOrderFireAtWill` / `...HoldYourFire`），
        /// 不存在"近距离才开火"这一档（旧版的 FireAtWill/HoldFire/HoldFireUntilClose 已只留这两个）。
        /// </summary>
        internal static readonly string[] FiringNames = new string[] {
            "fireAtWill", "holdFire"
        };

        /// <summary>
        /// v0.8.32：可下发的**骑乘令**（`Formation.SetRidingOrder`，引擎 `RidingOrder.RidingOrderEnum`）。
        /// 引擎**只有三档**（`RidingOrder.cs:5-10`）：`Free` / `Mount` / `Dismount`
        /// （`Free` = 不干预，由 AI/兵种自己决定上下马）。
        ///
        /// 与 movement order（`MovementOrder`）**正交**：那条管"去哪"，这条管"骑不骑"，
        /// 所以它**不参与** movement / position / target 的三者互斥。
        /// </summary>
        internal static readonly string[] RidingNames = new string[] {
            "free", "mount", "dismount"
        };

        /// <summary>
        /// v0.8.32：`targetAgent`（把某个**敌方 agent** 当目标）的下标上限 —— 纯防御性上界。
        /// 一场战斗的 agent 数远小于它（400v400 也只有约 800 个），给 100 万是为了"明显是手滑"的值
        /// （如把内存地址/时间戳填进来）能被拒，而不是静默去海里捞。
        /// </summary>
        internal const int AgentIndexLimit = 1000000;

        /// <summary>`FormationClass` 名或下标 → 0~4。大小写不敏感；非法返回 false（**不回落默认值**）。</summary>
        internal static bool TryFormationIndex(string raw, out int index)
        {
            index = -1;
            if (string.IsNullOrEmpty(raw)) return false;
            string s = raw.Trim();
            int parsed;
            if (int.TryParse(s, out parsed))
            {
                if (parsed >= 0 && parsed < FormationNames.Length)
                {
                    index = parsed;
                    return true;
                }
                return false;
            }
            for (int i = 0; i < FormationNames.Length; i++)
            {
                if (string.Equals(FormationNames[i], s, StringComparison.OrdinalIgnoreCase))
                {
                    index = i;
                    return true;
                }
            }
            return false;
        }

        /// <summary>大小写不敏感地判断 movement 是否在白名单内。</summary>
        internal static bool IsMovement(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return false;
            string s = raw.Trim();
            for (int i = 0; i < MovementNames.Length; i++)
                if (string.Equals(MovementNames[i], s, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }

        /// <summary>大小写不敏感地判断阵列名是否在白名单内（**碰 `ArrangementOrder` 之前**用）。</summary>
        internal static bool IsArrangement(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return false;
            string s = raw.Trim();
            for (int i = 0; i < ArrangementNames.Length; i++)
                if (string.Equals(ArrangementNames[i], s, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }

        /// <summary>大小写不敏感地判断射击纪律是否在白名单内（**碰 `FiringOrder` 之前**用）。</summary>
        internal static bool IsFiring(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return false;
            string s = raw.Trim();
            for (int i = 0; i < FiringNames.Length; i++)
                if (string.Equals(FiringNames[i], s, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }

        /// <summary>大小写不敏感地判断骑乘令是否在白名单内（**碰 `RidingOrder` 之前**用）。</summary>
        internal static bool IsRiding(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return false;
            string s = raw.Trim();
            for (int i = 0; i < RidingNames.Length; i++)
                if (string.Equals(RidingNames[i], s, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }

        /// <summary>
        /// v0.8.30：指定点移动（`MovementOrder.MovementOrderMove(WorldPosition)`）的坐标解析。
        /// 接受 `"x,y"` 或 `"x,y,z"`（英文逗号，允许空格；z 省略 = 0）。
        ///
        /// 为什么**必须**放在这里：坐标校验与名字表校验同属"碰 `MovementOrder` / `WorldPosition`
        /// **之前**要跑完的那一段"（碰早了会抛 `TypeInitializationException` 并把类型永久弄坏），
        /// 所以它也得只依赖 BCL，才能进离线单测。
        ///
        /// z 省略取 0 的依据：`new WorldPosition(scene, vec3)` 的 Z 有效性初始是 `Invalid`，
        /// `MovementOrder.GetPosition` 内部会 `ValidateZ`（按地面/导航网格补 Z）⇒ 只给 x,y 也能用；
        /// 但仍允许显式给 z（想让兵站到某层楼上时用）。
        ///
        /// 绝不静默回落：非数字 / NaN / Infinity / 越界 / 分量数不对 ⇒ false。
        /// </summary>
        internal static bool TryParsePosition(string raw, out float x, out float y, out float z)
        {
            x = 0f; y = 0f; z = 0f;
            if (string.IsNullOrEmpty(raw)) return false;
            string[] parts = raw.Split(',');
            if (parts.Length < 2 || parts.Length > 3) return false;
            float[] v = new float[3];
            for (int i = 0; i < parts.Length; i++)
            {
                string s = parts[i].Trim();
                float f;
                if (!float.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out f)) return false;
                if (float.IsNaN(f) || float.IsInfinity(f)) return false;
                if (Math.Abs(f) > PositionLimit) return false;
                v[i] = f;
            }
            x = v[0]; y = v[1]; z = v[2];
            return true;
        }

        /// <summary>给错误消息用：把名字表拼成 `a / b / c`。</summary>
        internal static string Join(string[] names)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < names.Length; i++)
            {
                if (i > 0) sb.Append(" / ");
                sb.Append(names[i]);
            }
            return sb.ToString();
        }
    }
}
