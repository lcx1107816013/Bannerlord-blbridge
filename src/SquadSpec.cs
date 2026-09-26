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
        /// <summary>
        /// DSL 第 3 字段（`troop:count:formation:movement`）。
        ///
        /// ⚠️ **它不决定编队**（v0.8.32 明确标注）：真正决定编队的是 `troop.GetFormationClass()`
        /// （`ApplyOrders` / `ReapplySideOrders` 都用后者）—— 这个字段全仓库**没有任何地方读它**，
        /// 属"仅回显"。给弓手写 `Infantry` 会**静默**进 `Ranged`。
        /// v0.8.32 起由 `CollectFormationMismatches` 在开战前把"请求值 vs 实际值"不一致**显式报出来**
        /// （start 响应的 `formationWarnings`），不再静默。
        /// </summary>
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
        /// 多态（单值枚举式，而不是几个独立 bool）：一个编队只能有**一个** movement order，
        /// 用多个 bool 就可能同时为真（自相矛盾的状态），所以做成单值枚举式常量
        /// （v0.8.32 起四态：按名字 / 指定点 / 指定目标编队 / 指定目标单位）。
        /// `movement` 通道下发时置回 `ManualNone`（否则改回 movement 后旧目标点会被反复重申）。
        ///
        /// 为什么存数值而不存引擎对象（`WorldPosition` / `Formation`）：本类**只依赖 BCL**
        /// （要进离线单测），且重申发生在下一帧，届时引擎句柄可能已失效，存数值/下标最稳。
        /// </summary>
        internal const int ManualNone = 0;          // 按 Movement 名字重申（默认，开战 DSL 那条路）
        internal const int ManualPosition = 1;      // 重申"指定点"（MoveToPosition）
        internal const int ManualChargeTarget = 2;  // 重申"冲锋到某个敌方编队"（ChargeToTarget）
        internal const int ManualAttackAgent = 3;   // v0.8.32：重申"攻击某个敌方单位"（AttackEntity）

        internal int ManualKind = ManualNone;
        internal float MoveX;
        internal float MoveY;
        internal float MoveZ;
        /// <summary>`ManualChargeTarget` 用：目标**敌方**编队的下标（0~4）。</summary>
        internal int TargetFormationIndex = -1;
        /// <summary>
        /// `ManualAttackAgent` 用：目标**敌方** agent 的下标（`Agent.Index`）。
        /// 存下标而不是 agent 对象：本类只依赖 BCL，且重申发生在下一帧 —— 届时目标可能已阵亡，
        /// 重申时按下标**重新解析**（解不到就跳过重申并记一条 order error，见 `ReapplySideOrders`）。
        /// </summary>
        internal int TargetAgentIndex = -1;

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

        /// <summary>
        /// v0.8.32：`formation` 字段是**死字段**（只回显、不决定编队）—— 这里给它补一个
        /// "请求值 vs 实际值"的一致性比对，把**静默**变成**显式信号**。
        ///
        /// 背景（真机踩到，2026-09-26）：真正决定编队的是 `troop.GetFormationClass()`
        /// （`ApplyOrders` / `ReapplySideOrders` 都用它），DSL 里那个 `formation` 全仓库无人读取
        /// ⇒ 给弓手写 `Infantry` 会**静默**进 `Ranged`。`order` 那条路已有 `emptyFormations` 兜底，
        /// 开战 DSL 这条路此前**没有任何信号**。
        ///
        /// 比对口径 = 引擎的 `FormationClassExtensions.FallbackClass()`（`FormationClass.cs:96-107`）：
        /// 把请求名与实际名都折叠到**默认编队家族**再比 ——
        /// `Ranged/Skirmisher → Ranged`、`Cavalry/HeavyCavalry → Cavalry`、
        /// `HorseArcher/LightCavalry → HorseArcher`、其余（含 `HeavyInfantry/General/Bodyguard`）→ `Infantry`。
        /// 这样 `HeavyInfantry` vs 实际 `Infantry` **不算**不一致（同族），而弓手写 `Infantry` 会被点名。
        ///
        /// 为什么放在这里：本类**只依赖 BCL**（要进离线单测），而实际值是调用方从
        /// `FormationClass` 取好后**以名字形式**喂进来（`EnumNames.Formation`），
        /// 于是这条判据能在 `tools/jsontest` 里被锁住。
        ///
        /// 注意：本方法**只收集、不抛**（它不改变开战结果，只是把不一致报出来）——
        /// 与"非法输入直接拒"（GC3）不冲突：`formation` 写个不存在的名字仍会被 `MatchOne` 拒，
        /// 这里说的是"名字合法但与兵种实际编队不同族"。绝不抛。
        /// </summary>
        internal static List<string> CollectFormationMismatches(List<SquadSpec> specs, string[] actualFormationNames)
        {
            List<string> mismatches = new List<string>();
            if (specs == null || actualFormationNames == null) return mismatches;
            int n = specs.Count < actualFormationNames.Length ? specs.Count : actualFormationNames.Length;
            for (int i = 0; i < n; i++)
            {
                SquadSpec s = specs[i];
                if (s == null || s.Formation == null) continue;      // 没写 formation ⇒ 无意声明，不报
                string actual = actualFormationNames[i];
                if (string.IsNullOrEmpty(actual)) continue;          // 取不到实际编队（调用方给了空）⇒ 不猜
                string want = FormationFamily(s.Formation);
                string got = FormationFamily(actual);
                if (want == null || got == null) continue;           // 未知名字（应为死代码：名字表已校验）⇒ 不猜
                if (want == got) continue;
                mismatches.Add("第 " + (i + 1) + " 组（" + s.Troop + "）写了 formation=" + s.Formation
                    + "，但该兵种的实际编队是 " + actual + " ⇒ 引擎按实际编队处理"
                    + "（DSL 的 formation 字段不决定编队，只回显与做这条校验）");
            }
            return mismatches;
        }

        /// <summary>
        /// 编队名 → **默认编队家族名**（口径同引擎 `FormationClassExtensions.FallbackClass()`，
        /// `FormationClass.cs:96-107`）。未知名字回 null（调用方据此跳过比对，不猜）。
        /// </summary>
        internal static string FormationFamily(string name)
        {
            if (string.IsNullOrEmpty(name)) return null;
            switch (name.Trim())
            {
                case "Infantry":
                case "HeavyInfantry":
                case "General":
                case "Bodyguard":
                    return "Infantry";
                case "Ranged":
                case "Skirmisher":
                    return "Ranged";
                case "Cavalry":
                case "HeavyCavalry":
                    return "Cavalry";
                case "HorseArcher":
                case "LightCavalry":
                    return "HorseArcher";
                default:
                    return null;
            }
        }

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
