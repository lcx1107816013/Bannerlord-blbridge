using System;
using System.Collections.Generic;

namespace BlBridge
{
    /// <summary>
    /// v0.8.8 · **多兵种 / 战术组**规格：解析扁平字符串 DSL
    /// `troop:count[:formation[:movement]]`（多组用 `|` 分隔）。
    ///
    /// 为什么手写解析、不用 Jmini：`Jmini` 只读**裸值**、也没有对象树；更要命的是它对
    /// "读不到"一律**静默**返回 fallback（2026-09-24 有 9 场实验因此整批作废）。这里的铁律相反：
    /// **任何非法都显式抛 ArgumentException，绝不回落到默认值**（GC3）。
    ///
    /// 本类**只依赖 BCL**（不碰 TaleWorlds 类型）⇒ 可以进 `tools/jsontest` 离线单测；
    /// 把字符串映射到 `FormationClass` / `MovementOrder` 的工作放在 ScenarioRunner 一侧。
    ///
    /// formation（`TaleWorlds.Core.FormationClass` 的名字，大小写不敏感）：
    ///   Infantry / Ranged / Cavalry / HorseArcher / Skirmisher /
    ///   HeavyInfantry / LightCavalry / HeavyCavalry / General / Bodyguard
    /// movement：charge / advance / fallback / stop / retreat
    /// 缺省：`Formation = null`（引擎按兵种决定编队）、`Movement = null`（调用方按 charge 处理）。
    /// </summary>
    internal sealed class SquadSpec
    {
        internal string Troop;
        internal int Count;
        internal string Formation;
        internal string Movement;

        /// <summary>
        /// v0.8.30：**手动令优先**标记 —— 这个组所在的编队被"运行中改令"（`BattleOrders`）亲手改过
        /// movement order，于是组路径每 0.5 s 的周期重申（`ScenarioProbe.ReapplySideOrders`）必须让它路。
        ///
        /// 存在理由（真机踩出来的，不是设计偏好）：重申只认**名字**（`s.Movement`），而
        /// 指定点移动 / 指定目标编队**没有名字可重申** ⇒ 不给它让路，半秒内就会被我们自己重申回
        /// `charge`（真机表现：`Formation` 上当场看是 `Move`，12 秒后 `orderBefore` 又变回 `Stop`）。
        ///
        /// 三态（而不是两个 bool）：一个编队只能有**一个** movement order，
        /// 用两个独立 bool 就可能同时为真（自相矛盾的状态），所以做成单值枚举式常量。
        /// `movement` 通道下发时置回 `ManualNone`（否则改回 movement 后旧目标点会被反复重申）。
        ///
        /// 为什么存数值而不存引擎对象（`WorldPosition` / `Formation`）：本类**只依赖 BCL**
        /// （要进离线单测），且重申发生在下一帧，届时引擎句柄可能已失效，存数值/下标最稳。
        /// </summary>
        internal const int ManualNone = 0;          // 按 Movement 名字重申（默认，开战 DSL 那条路）
        internal const int ManualPosition = 1;      // 重申"指定点"（MoveToPosition）
        internal const int ManualChargeTarget = 2;  // 重申"冲锋到某个敌方编队"（ChargeToTarget）

        internal int ManualKind = ManualNone;
        internal float MoveX;
        internal float MoveY;
        internal float MoveZ;
        /// <summary>`ManualChargeTarget` 用：目标**敌方**编队的下标（0~4）。</summary>
        internal int TargetFormationIndex = -1;

        // internal（而非 private）：`tools/jsontest` 的 GuardTest 要用它锁住"运行时改令（OrderSpec）
        // 与开战 DSL 认同一套拼写"——两份名字表漂移会让"开战能写、中途改不了"这种 bug 很难查。
        internal static readonly string[] Formations = new string[] {
            "Infantry", "Ranged", "Cavalry", "HorseArcher", "Skirmisher",
            "HeavyInfantry", "LightCavalry", "HeavyCavalry", "General", "Bodyguard"
        };
        internal static readonly string[] Movements = new string[] {
            "charge", "advance", "fallback", "stop", "retreat"
        };
        // 已移除的 movement 及其替代（补位提示，GC3）：只放这一条，别顺手加别的。
        private static readonly Dictionary<string, string> RemovedMovements = new Dictionary<string, string> {
            { "hold", "stop" }
        };

        internal static List<SquadSpec> Parse(string dsl)
        {
            List<SquadSpec> list = new List<SquadSpec>();
            if (string.IsNullOrEmpty(dsl)) return list;
            string[] parts = dsl.Split('|');
            for (int i = 0; i < parts.Length; i++)
            {
                int n = i + 1;
                string part = parts[i].Trim();
                if (part.Length == 0)
                    throw new ArgumentException("第 " + n
                        + " 组为空（应为 troop:count[:formation[:movement]]，多组用 | 分隔）");
                string[] f = part.Split(':');
                if (f.Length < 2 || f.Length > 4)
                    throw new ArgumentException("第 " + n + " 组 '" + part + "' 的字段数 = " + f.Length
                                                + "，应为 troop:count[:formation[:movement]]");
                SquadSpec s = new SquadSpec();
                s.Troop = f[0].Trim();
                if (s.Troop.Length == 0)
                    throw new ArgumentException("第 " + n + " 组 '" + part + "' 缺少兵种 id");
                int cnt;
                string cntRaw = f[1].Trim();
                if (!int.TryParse(cntRaw, out cnt))
                    throw new ArgumentException("第 " + n + " 组 '" + part + "' 的 count 不是整数：'"
                                                + cntRaw + "'");
                if (cnt < 1)
                    throw new ArgumentException("第 " + n + " 组 '" + part + "' 的 count 必须 ≥1，收到 " + cnt);
                s.Count = cnt;
                if (f.Length >= 3 && f[2].Trim().Length > 0)
                    s.Formation = MatchOne(Formations, f[2].Trim(), "formation", n, part);
                if (f.Length == 4 && f[3].Trim().Length > 0)
                    s.Movement = MatchOne(Movements, f[3].Trim().ToLowerInvariant(), "movement", n, part);
                list.Add(s);
            }
            return list;
        }

        private static string MatchOne(string[] names, string raw, string what, int n, string part)
        {
            for (int i = 0; i < names.Length; i++)
                if (string.Equals(names[i], raw, StringComparison.OrdinalIgnoreCase))
                    return names[i];
            string hint = "";
            if (what == "movement")
            {
                string replacement;
                if (RemovedMovements.TryGetValue(raw.ToLowerInvariant(), out replacement))
                    hint = "—— " + raw.ToLowerInvariant() + " 已移除（引擎层它本就等同 " + replacement
                           + "），请改用 " + replacement;
            }
            throw new ArgumentException("第 " + n + " 组 '" + part + "' 的 " + what + " 未知：'" + raw
                                        + "'（可用：" + string.Join(", ", names) + "）" + hint);
        }
    }
}
