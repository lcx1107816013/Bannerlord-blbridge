using TaleWorlds.Core;
using TaleWorlds.MountAndBlade;

namespace BlBridge
{
    /// <summary>
    /// 引擎枚举 → **稳定可读名**。存在的理由：两个引擎枚举都有"别名"，
    /// `ToString()` 会返回别名而不是直观名，直接写进 JSONL 会被误读：
    ///
    ///   BoneBodyPartType : Head = 0 与 CriticalBodyPartsBegin = 0 **同值**
    ///                      ⇒ 头部命中全被写成 "CriticalBodyPartsBegin"，字面 "Head" 永不出现
    ///   EquipmentIndex   : WeaponItemBeginSlot = 0 与 Weapon0 = 0 **同值**
    ///                      ⇒ 第一个武器槽恒为 "WeaponItemBeginSlot"，字面 "Weapon0" 永不出现
    ///
    /// 2026-09-24 实测确认（0.8.0 日志：`Head` 出现 0 次、`CriticalBodyPartsBegin` 2277 次）。
    /// 这里按**枚举真值**给出稳定名，供分析器直接使用。
    /// 旧的 `bodyPart` / `weaponSlot` 字段**保持原样**（不改取值域），避免破坏已有 60+ 场历史日志。
    /// </summary>
    internal static class EnumNames
    {
        /// <summary>命中部位名（`BoneBodyPartType`，`BoneBodyPartType.cs:5-21`）。</summary>
        public static string BodyPart(BoneBodyPartType p)
        {
            switch ((int)p)
            {
                case -1: return "None";
                case 0: return "Head";          // 别名 CriticalBodyPartsBegin 的真身
                case 1: return "Neck";
                case 2: return "Chest";
                case 3: return "Abdomen";
                case 4: return "ShoulderLeft";
                case 5: return "ShoulderRight";
                case 6: return "ArmLeft";
                case 7: return "ArmRight";
                case 8: return "Legs";
                default: return p.ToString();
            }
        }

        /// <summary>装备槽名（`EquipmentIndex`，`EquipmentIndex.cs:3-26`）。</summary>
        public static string EquipSlot(EquipmentIndex i)
        {
            switch ((int)i)
            {
                case 0: return "Weapon0";       // 别名 WeaponItemBeginSlot 的真身
                case 1: return "Weapon1";
                case 2: return "Weapon2";
                case 3: return "Weapon3";
                case 4: return "ExtraWeaponSlot";
                case 5: return "Head";
                case 6: return "Body";
                case 7: return "Leg";
                case 8: return "Gloves";
                case 9: return "Cape";
                case 10: return "Horse";
                case 11: return "HorseHarness";
                default: return i.ToString();
            }
        }
    }
}
