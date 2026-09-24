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
    /// movement：charge / advance / hold / fallback / stop / retreat
    /// 缺省：`Formation = null`（引擎按兵种决定编队）、`Movement = null`（调用方按 charge 处理）。
    /// </summary>
    internal sealed class SquadSpec
    {
        internal string Troop;
        internal int Count;
        internal string Formation;
        internal string Movement;

        private static readonly string[] Formations = new string[] {
            "Infantry", "Ranged", "Cavalry", "HorseArcher", "Skirmisher",
            "HeavyInfantry", "LightCavalry", "HeavyCavalry", "General", "Bodyguard"
        };
        private static readonly string[] Movements = new string[] {
            "charge", "advance", "hold", "fallback", "stop", "retreat"
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
            throw new ArgumentException("第 " + n + " 组 '" + part + "' 的 " + what + " 未知：'" + raw
                                        + "'（可用：" + string.Join(", ", names) + "）");
        }
    }
}
