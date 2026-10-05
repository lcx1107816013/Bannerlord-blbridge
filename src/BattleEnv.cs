using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using TaleWorlds.Core;

namespace BlBridge
{
    /// <summary>
    /// 开战环境旋钮（v0.8.41）。全部是 `MissionInitializerRecord` 上的**已核字段**，
    /// 零 Harmony、零反射、零新类型暴露给引擎。
    ///
    /// 字段类型/默认值取证（2026-10-05，反编译 TaleWorlds.Core.MissionInitializerRecord）：
    ///   `public int TerrainType`                   默认 **-1**（无效 ⇒ 引擎按场景自己决定）
    ///   `public float DamageToFriendsMultiplier`    默认 1f
    ///   `public float DamageFromPlayerToFriendsMultiplier` 默认 1f
    ///   `public bool NeedsRandomTerrain`            默认 false
    ///   `public int RandomTerrainSeed`              默认 0
    ///   `public string SceneLevels`                 默认 ""
    ///   `public bool DisableCorpseFadeOut`          默认 false
    ///   `public AtmosphereInfo AtmosphereOnCampaign` 默认 `AtmosphereInfo.GetInvalidAtmosphereInfo()`
    ///
    /// **为什么默认 -1 而不是 1（Plain）**：BlBridge 至今从不设 TerrainType ⇒ 一直是 -1。
    /// 本模块的语义是"不传就不动"，所以未显式请求时**必须**保持 -1，
    /// 否则会把所有既有 baseline 的战场地形从"场景自带"改成"平原"，口径当场作废。
    ///
    /// **为什么没有时刻/天气/雾**（EBT 有，我们暂时不做）：`AtmosphereInfo` 是
    /// `TaleWorlds.Library` 里的 ValueType，含 SunInfo/RainInfo/SnowInfo/AmbientInfo/FogInfo/
    /// SkyInfo/NauticalInfo/TimeInfo/AreaInfo/PostProInfo 十个子结构 + `IsValid`。
    /// 手搓一份有效的 AtmosphereInfo 需要先把这十个子结构的字段与语义全部核清楚，
    /// 核错就是黑屏或原生崩溃。EBT 是自己写了 100 行的 `AtmosphereModel` 才做到的。
    /// ⇒ **本轮只做已核的标量字段**；要时刻/天气，先做 AtmosphereInfo 结构取证（见交接说明）。
    /// </summary>
    internal static class BattleEnv
    {
        /// <summary>"未请求"哨兵：TerrainType 用 -1（与引擎自己的"无效"同值）。</summary>
        internal const int TerrainUnset = -1;

        /// <summary>"未请求"哨兵：RandomTerrainSeed 用 int.MinValue（0 是引擎默认值，不能当哨兵）。</summary>
        internal const int SeedUnset = int.MinValue;

        internal static int TerrainTypeValue = TerrainUnset;

        /// <summary>请求的原始地形名（空 = 未请求），只用于回显/落盘。</summary>
        internal static string TerrainName = "";

        internal static int RandomTerrainSeedValue = SeedUnset;

        /// <summary>
        /// **打到「玩家方」身上**的伤害倍率（写进 `DamageToFriendsMultiplier`）；&lt; 0 = 未请求（保持引擎默认 1f）。
        ///
        /// ⚠️ **它不是"同队误伤"开关**（2026-10-05 真机实测更正，字段名与参数名都容易误导）：
        /// 引擎判据是 `victimAgent.IsFriendOf(Mission.Current.MainAgent)`
        /// （`DefaultMissionDifficultyModel.cs:18-22`）⇒ 语义是"**受害者属于玩家一方**"，
        /// 而**不是**"加害者与受害者同队"。本靶场玩家方 = Attacker（可用 `playerSide` 改）。
        /// 实测：设 0 时"打到玩家方命中的零伤害占比"从 20.2%±3.4(n=5) 升到 71.7%(n=4)，t=5.20 —— 显著但非全部归零。
        /// </summary>
        internal static float FriendlyFireMultiplier = -1f;

        /// <summary>关掉尸体淡出；false = 未请求（保持引擎默认）。</summary>
        internal static bool KeepCorpses = false;

        /// <summary>`TaleWorlds.Core.TerrainType` 全表（反编译取证，含数值）。
        /// 全部接受、大小写不敏感；未知名字显式报错并列候选（GC3：不静默兜底）。</summary>
        private static readonly KeyValuePair<string, int>[] TerrainTable = new KeyValuePair<string, int>[]
        {
            new KeyValuePair<string, int>("plain", (int)TerrainType.Plain),
            new KeyValuePair<string, int>("desert", (int)TerrainType.Desert),
            new KeyValuePair<string, int>("snow", (int)TerrainType.Snow),
            new KeyValuePair<string, int>("forest", (int)TerrainType.Forest),
            new KeyValuePair<string, int>("steppe", (int)TerrainType.Steppe),
            new KeyValuePair<string, int>("fording", (int)TerrainType.Fording),
            new KeyValuePair<string, int>("mountain", (int)TerrainType.Mountain),
            new KeyValuePair<string, int>("lake", (int)TerrainType.Lake),
            new KeyValuePair<string, int>("water", (int)TerrainType.Water),
            new KeyValuePair<string, int>("river", (int)TerrainType.River),
            new KeyValuePair<string, int>("canyon", (int)TerrainType.Canyon),
            new KeyValuePair<string, int>("ruralarea", (int)TerrainType.RuralArea),
            new KeyValuePair<string, int>("swamp", (int)TerrainType.Swamp),
            new KeyValuePair<string, int>("dune", (int)TerrainType.Dune),
            new KeyValuePair<string, int>("bridge", (int)TerrainType.Bridge),
            new KeyValuePair<string, int>("coastalsea", (int)TerrainType.CoastalSea),
            new KeyValuePair<string, int>("opensea", (int)TerrainType.OpenSea),
            new KeyValuePair<string, int>("beach", (int)TerrainType.Beach),
            new KeyValuePair<string, int>("cliff", (int)TerrainType.Cliff),
            new KeyValuePair<string, int>("nonnavigableriver", (int)TerrainType.NonNavigableRiver),
            new KeyValuePair<string, int>("landrestriction", (int)TerrainType.LandRestriction),
            new KeyValuePair<string, int>("searestriction", (int)TerrainType.SeaRestriction),
            new KeyValuePair<string, int>("underbridge", (int)TerrainType.UnderBridge)
        };

        /// <summary>接受的地形名清单（小写、字典序），供错误消息与文档用。</summary>
        internal static string TerrainNames()
        {
            List<string> names = new List<string>();
            for (int i = 0; i < TerrainTable.Length; i++) names.Add(TerrainTable[i].Key);
            names.Sort(StringComparer.Ordinal);
            return string.Join(" | ", names.ToArray());
        }

        /// <summary>解析地形名 → 枚举值。成功返回 true；失败给出候选清单（绝不静默兜底）。</summary>
        internal static bool TryParseTerrain(string text, out int value, out string error)
        {
            value = TerrainUnset;
            error = "";
            if (string.IsNullOrEmpty(text)) return true;   // 空 = 不请求
            string key = text.Trim().ToLowerInvariant();
            for (int i = 0; i < TerrainTable.Length; i++)
            {
                if (TerrainTable[i].Key == key)
                {
                    value = TerrainTable[i].Value;
                    return true;
                }
            }
            error = "未知地形名: " + text + "（可用：" + TerrainNames() + "）";
            return false;
        }

        /// <summary>
        /// 每场 start 前复位 —— 与 `_pendingOrders` 同一个模式：**不传就是"不覆盖"**，
        /// 绝不能把上一场的值泄漏到这一场（那会做成"我以为改了、其实没改"的静默污染）。
        /// </summary>
        internal static void Reset()
        {
            TerrainTypeValue = TerrainUnset;
            TerrainName = "";
            RandomTerrainSeedValue = SeedUnset;
            FriendlyFireMultiplier = -1f;
            KeepCorpses = false;
        }

        /// <summary>是否请求了任何一项（决定 status 里 env 块是 `null` 还是有内容）。</summary>
        internal static bool AnyRequested()
        {
            return TerrainTypeValue != TerrainUnset
                || RandomTerrainSeedValue != SeedUnset
                || FriendlyFireMultiplier >= 0f
                || KeepCorpses;
        }

        /// <summary>
        /// 把请求过的字段写进 `MissionInitializerRecord`。**只写请求过的** ——
        /// 未请求的字段一个都不碰，保证不传参数时与改动前逐字节等价（GC2）。
        /// `rec` 是 struct，必须 ref。
        /// </summary>
        internal static void Apply(ref MissionInitializerRecord rec)
        {
            if (TerrainTypeValue != TerrainUnset) rec.TerrainType = TerrainTypeValue;
            if (RandomTerrainSeedValue != SeedUnset)
            {
                rec.RandomTerrainSeed = RandomTerrainSeedValue;
                // ⚠️ `NeedsRandomTerrain` 与 `RandomTerrainSeed` 是**两个**字段：
                //    前者是"要不要随机地形"的开关，后者是种子。只设种子、不开开关，
                //    引擎可能整个忽略它。但反过来开开关会改变地形本身（不再是场景原样）
                //    ⇒ 这是个真实的选择，必须让调用方看得见，所以写成显式的 "randomTerrain"。
                //    这里选择：**主动开启**（否则这个参数等于没接线，属静默失效）。
                rec.NeedsRandomTerrain = true;
            }
            if (FriendlyFireMultiplier >= 0f) rec.DamageToFriendsMultiplier = FriendlyFireMultiplier;
            if (KeepCorpses) rec.DisableCorpseFadeOut = true;
        }

        /// <summary>status / meta 用的单行 JSON（未请求任何项 ⇒ `null`）。绝不抛。</summary>
        internal static string Json()
        {
            if (!AnyRequested()) return "null";
            StringBuilder sb = new StringBuilder();
            sb.Append('{');
            sb.Append("\"terrain\":");
            if (TerrainTypeValue == TerrainUnset) sb.Append("null");
            else sb.Append(Protocol.Q(TerrainName)).Append(",\"terrainId\":")
                     .Append(TerrainTypeValue.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\"randomTerrainSeed\":");
            sb.Append(RandomTerrainSeedValue == SeedUnset
                ? "null" : RandomTerrainSeedValue.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\"aiFriendlyFireMultiplier\":");
            sb.Append(FriendlyFireMultiplier < 0f
                ? "null" : FriendlyFireMultiplier.ToString("0.###", CultureInfo.InvariantCulture));
            sb.Append(",\"keepCorpses\":").Append(KeepCorpses ? "true" : "false");
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>meta 事件用的扁平片段（`,"terrain":"snow","terrainSeed":7,...`）。绝不抛。</summary>
        internal static string MetaFragment()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append(",\"terrain\":");
            sb.Append(TerrainTypeValue == TerrainUnset ? "\"\"" : Protocol.Q(TerrainName));
            sb.Append(",\"terrainSeed\":");
            sb.Append(RandomTerrainSeedValue == SeedUnset
                ? "-1" : RandomTerrainSeedValue.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\"friendlyFire\":");
            sb.Append(FriendlyFireMultiplier < 0f
                ? "-1" : FriendlyFireMultiplier.ToString("0.###", CultureInfo.InvariantCulture));
            return sb.ToString();
        }
    }
}
