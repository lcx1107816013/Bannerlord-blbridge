# L2 可行性实测：上游 API 在 1.4.8 上到底能不能用

> 日期：2026-09-27
> 上游：[BUTR/Bannerlord.GABS](https://github.com/BUTR/Bannerlord.GABS)（MIT），它自己声明的支持版本是
> **`v1.3.15` / `v1.3.13` / `v1.2.12`**（`src/Bannerlord.GABS/supported-game-versions.txt`），
> **本机是 v1.4.8 —— 从没验证过**。
> 探针：`tools/l2probe/`（`L2ApiProbe.cs` + `run_probe.py`，`python tools\l2probe\run_probe.py`）

---

## 0. 结论

| # | 结论 | 依据 |
|---|---|---|
| 1 | **L2 可以做**：22 个上游真实调用点里 **21 个**在 1.4.8 上编译通过 | 编译器判定（见 §2） |
| 2 | 唯一不可用的是上游的**老分支**：`party.Ai.SetMoveGoToPoint(...)`（v1.3.13 之前）。它的**新分支** `party.SetMoveGoToPoint(CampaignVec2, NavigationType)` 在 1.4.8 上可用 | P13 红 / P12 绿 |
| 3 | ⇒ **抄 PartyTools 必须走 v1.3.13+ 那条分支**。而且上游 13 处 `#if` 里 **10 处在 PartyTools** ⇒ 它是漂移面最大的文件，**排最后做** | 见 §4 |
| 4 | 3 个**字符串反射**目标里 2 个名字对得上；`MBSaveLoad.ActiveSaveSlotName` 在 1.4.8 **从字段变成了属性** ⇒ 抄的时候必须改写法（而且它现在是 `public static`，**直接读就行、不用反射**） | 反编译核对（见 §3） |
| 5 | 另外两件"抄录注意事项"（不是漂移）：`ConversationManager.GetPersuasion*` 返回 **float**；`GameMenuManager` / `MenuContext` 在 1.4.8 位于 `…CampaignSystem.GameMenus` / `…GameState` 两个子命名空间（上游一路用 `var`，所以它的 `using` 清单看不出来） | 见 §2 备注 |

**一句话**：编译期层面基本没漂移（比预期的好），真正的坑在**字符串反射**和**命名空间**这两类编译期看不见的地方。

---

## 1. 方法：为什么用"编译"当判据

- 上游的每个调用点都是**真机跑过的代码**，把它们原样搬到 1.4.8 的引用程序集上编译：
  **过 = 该 API 在 1.4.8 存在且签名兼容；不过 = 漂移，错误信息本身就是判据**（会说清缺哪个成员 / 参数不对）。
- 不需要启动游戏、不需要装 Lib.GAB、不需要跑起来 ⇒ 可以在改任何东西之前就把可行性钉死。
- **参考程序集**：`bin\Win64_Shipping_Client` + 各模块 `bin` 里 `bin` 没有的（共 76 个），
  框架引用 + `netstandard` 门面与 `build.ps1` 同款。

**这个判据测不到什么（诚实标注）**：字符串反射（`AccessTools2.…(typeof(X), "字段名")`）—— 字段名写错编译照样过。
那一类要走**第二层**（§3，用反编译器核成员名）。至于"行为对不对"，两层都测不了，仍须真机判据。

---

## 2. 第一层：编译结果（22 个 CASE）

`python tools\l2probe\run_probe.py`（P13 是**负向对照**，期望就是红）：

| CASE | 上游出处 | 在 1.4.8 上 | 覆盖的 API |
|---|---|---|---|
| P01 | BarterTools.cs:8 | ✅ | 命名空间 `TaleWorlds.CampaignSystem.BarterSystem` |
| P02 | CoreTools.cs:134 / ConversationTools.cs:48,299-300 | ✅ | `Campaign.Current.ConversationManager.IsConversationInProgress`、`ConversationManager.GetPersuasionIsActive/Progress/GoalValue` |
| P03 | CoreTools.cs:81 | ✅ | `Campaign.Current.GameMenuManager.NextGameMenuId` |
| P04 | CoreTools.cs:278,286 | ✅ | `CampaignTime.Now` / `.GetSeasonOfYear` / `.ToString()` |
| P05 | CoreTools.cs:308-338 | ✅ | `Campaign.Current.TimeControlMode` 读写 + 四个 `CampaignTimeControlMode` 值 |
| P06 | CoreTools.cs:390 | ✅ | `Game.Current.CheatMode` |
| P07 | InventoryTools.cs:30,37 | ✅ | `MobileParty.MainParty.ItemRoster` 遍历 + `ItemRosterElement` |
| P08 | InventoryTools.cs:68 | ✅ | `Hero.MainHero.Gold` |
| P09 | InventoryTools.cs:186 | ✅ | `Settlement.Town.MarketData.GetPrice(EquipmentElement)` |
| P10 | InventoryTools.cs:167 | ✅ | `Settlement.ItemRoster` |
| P11 | PartyTools.cs:122,253 | ✅ | `MobileParty.All` / `Settlement.All` |
| P12 | PartyTools.cs:290-291（`#if v1313`） | ✅ | `CampaignVec2` + `party.SetMoveGoToPoint(target, MobileParty.NavigationType.Default)` |
| **P13** | PartyTools.cs:293（`#else`） | ❌ **CS1061** | `party.Ai.SetMoveGoToPoint(Vec2)` —— **1.4.8 上不存在** |
| P14 | BarterTools.cs:118 | ✅ | `BarterManager.Instance` |
| P15 | SubModule.cs:111 + BarterTools.cs:20 | ✅ | `Campaign.Current.BarterManager.BarterBegin += (BarterData)` |
| P16 | MainThreadDispatcher.cs:111 | ✅ | `Campaign.Current.SaveHandler.SaveAs(string)` |
| P17 | CoreTools.cs:140 | ✅ | `ScreenManager.TopScreen` |
| P18 | GauntletUITools.cs:65 | ✅ | `TaleWorlds.Engine.GauntletUI.GauntletLayer` |
| P19 | GauntletUITools.cs:63-67 | ✅ | `ScreenBase.Layers` + `is GauntletLayer` |
| P20 | GauntletUITools.cs:647-655 | ✅ | `Widget.Children` 递归遍历（Gauntlet 控件树） |
| P21 | GauntletUITools.cs:25 | ✅ | `typeof(GauntletLayer).GetField("_movieIdentifiers", NonPublic\|Instance)` 这行本身可编 |
| P22 | MenuTools.cs:32,45 / CoreTools.cs:212 | ✅ | `GameMenuManager` 写类型名 + `GetVirtualGameMenuOption(MenuContext, int)` + `GameMenuOption.IdString` |

### 两条"不是漂移、但抄录时会撞上"的备注

- **P02 的 float**：`ConversationManager.GetPersuasionProgress()` / `GetPersuasionGoalValue()` 在 1.4.8 返回 **float**。
  上游用 `var` 接，所以它不受影响；**我们抄的时候写成 `int` 会 CS0266**。
  （这一条差点被记成"漂移" —— 见 §5 的自查记录。）
- **P22 的命名空间**：1.4.8 里 `GameMenuManager` 在 `TaleWorlds.CampaignSystem.GameMenus`、
  `MenuContext` 在 `TaleWorlds.CampaignSystem.GameState`。上游 `MenuTools.cs` 的 `using` 清单只有
  `TaleWorlds.CampaignSystem` 一条，因为它**从不写出类型名**（一路 `var` / 属性模式 `menuContext is { GameMenu: not null }`）。
  ⇒ 我们抄的时候只要显式声明类型，就得自己补这两个 `using`。

---

## 3. 第二层：字符串反射目标的成员名核对

上游只有 3 处 `AccessTools2.*(typeof(X), "成员名")` —— 编译期全都测不到，必须用反编译器核：

| 上游写法 | 1.4.8 实际 | 判定 |
|---|---|---|
| `StaticFieldRefAccess<string>(typeof(MBSaveLoad), "ActiveSaveSlotName")`（CoreTools.cs:27） | `TaleWorlds.Core.MBSaveLoad`：**`public static string ActiveSaveSlotName { get; private set; }`** —— 是**属性**，不是字段 | ⚠️ **写法要改**。1.4.8 上它是 `public static` ⇒ **直接读，不需要反射**（真要用反射应走属性访问） |
| `StaticFieldRefAccess<IDictionary>(typeof(CommandLineFunctionality), "AllFunctions")`（CoreTools.cs:28） | `TaleWorlds.Library.CommandLineFunctionality`：`private static Dictionary<string, CommandLineFunction> AllFunctions` | ✅ 名字与形状都对得上 |
| `FieldRefAccess<IEnumerable>(typeof(GauntletLayer), "_movieIdentifiers")`（GauntletUITools.cs:25） | `TaleWorlds.Engine.GauntletUI.GauntletLayer`：`private readonly MBList<GauntletMovieIdentifier> _movieIdentifiers` | ✅ 名字与形状都对得上（只读，上游只遍历） |

> 关于第一条的**后果层：未验证**。我只确认了"名字存在但形状从字段变成了属性"，
> **没有**去跑上游代码看它失败后的行为（它大概有 null 检查、降级而不是崩，但这是推测，不写进结论）。

---

## 4. L2 短名单（按"落地顺序"排，不是按价值）

排序依据：**API 稳不稳**（本报告）+ **是否只读**（副作用面）+ **会不会碰 3D/换场景**（我们已有的硬约束）。

| 顺位 | 做什么 | 为什么排这个位置 |
|---|---|---|
| **1** | `ui/get_screen` + `ui/get_viewmodel_property`（GauntletUITools 的**只读面**） | **只读、无副作用、API 全部验过**（P17/P18/P19/P20/P21）；价值最高 —— 让 AI 能**读**官方界面的状态，而不是去抢鼠标（我们 v0.8.12 已证明抢鼠标不可达） | **已落地**：2026-09-27 脱壳抄进 `src/UiInspector.cs`（method 名 `get_screen` / `get_viewmodel_property`，MCP `bl_get_screen` / `bl_get_viewmodel_property`），`build.ps1` 编译通过；**真机判据待游戏运行** |
| **2** | `inventory/get_inventory` | 只读；`ItemRoster` / `MarketData` / `Hero.Gold` 全验过（P07/P08/P09/P10） |
| **3** | `menu/get_current` + `menu/select_option` | P03/P22 验过；但要补 `GameMenus` / `GameState` 两个 `using`。`select_option` 有副作用 ⇒ 要有闸门 |
| **4** | `barter/*` | P01/P14/P15 验过；`BarterBegin` 事件那条要挂 `OnGameStart`（上游 SubModule.cs:109-113 的做法） |
| **5** | `conversation/*` | P02 验过（注意 float）；**边界**：只在同一定居点、不换 3D 场景（上游硬约束，见 `lessons-from-bannerlord-gabs.md`） |
| **6** | `inventory/buy_item` / `sell_item` | 依赖 2 的读数面先稳；**会改游戏状态** ⇒ 必须有显式确认与回滚口径 |
| **最后** | `party/*`（移动 / 招募 / 追踪） | **13 处 `#if` 里它占 10 处**、且老分支在 1.4.8 上直接不存在（P13）⇒ 漂移面最大。做之前先把它的分支矩阵逐条过一遍 |

**每个工具落地时仍然要真机判据**：本报告只证明"API 签名对得上"，证明不了"行为对"。

---

## 5. 自查记录：两个探针缺陷 + 一次**假通过**

**探针缺陷（不是漂移，差点被写成漂移）**：

1. 首版 P02 把 `GetPersuasion*` 接成 `int` ⇒ CS0266。**这是我的探针写错**，不是 API 变了。
2. 首版 P22 只 `using TaleWorlds.CampaignSystem` 却写出类型名 `GameMenuManager` ⇒ CS0246。同样是探针写错。

⇒ 如果直接照第一版结果写报告，会**凭空造出 2 个"漂移"**。所以本报告里每一条 FAIL 都回读了上游原文与 1.4.8 的真实定义才定性。

**一次假通过（更有价值的那条）**：探针首跑打印 **「通过 22 / 22，退出码 0」**，而实际上是错的：

- 引用收集把 **`TaleWorlds.Native.dll`（原生 DLL）** 也当托管程序集传给了 `/reference:`；
- csc 报 `error CS0009: 无法打开元数据文件 …（PE 映像不包含任何托管元数据）` 后**中止编译**，源码错误一个都没报出来；
- 而驱动的统计只挑"归到某个 CASE 的错误"，CS0009 落在"其它输出"里 ⇒ **照样打印全部通过、退出码 0**。

这正是本项目反复写的那句话的又一个实例：**没有对照组的验证不是验证** —— 一个数据源接错的脚本会得到"通过"这个结果。
现在两道防线：

1. **引用前逐个判托管性**（读 PE 的 CLI 数据目录 `DataDirectory[14]`），非托管直接跳过并打印；
2. **任何没归到 CASE 的 `error CS` 一律算「装置错误」**，打印出来并让退出码变 **2**（不让它冒充通过）。

修完之后的第一次真实结果就是 §2 那张表（20 个应当通过 + P13 应当红，**与预期一致 22/22**）。
顺带修掉的两个驱动小毛病：`✓` 在 stdout 被管道接走时触发 `UnicodeEncodeError('gbk')`
（现在入口钉 `safe_streams()`，与仓库 `tools/bl_common.safe_streams()` 同口径）。

---

## 6. 复现入口

```powershell
cd C:\Users\LCGX\CodeBuddy\20260923171333\BlBridge
python tools\l2probe\run_probe.py
# 退出码：0 = 与预期一致（含 P13 必须红）；1 = 有 CASE 与预期不符；2 = 装置问题（引用/路径/csc）
```

游戏目录可用环境变量覆盖：`$env:BANNERLORD_DIR = '<游戏根>'`。
**这份探针不在"改完必跑"清单里**：它依赖具体的游戏版本，只在"游戏升级了 / 要抄上游某个文件"时跑。
