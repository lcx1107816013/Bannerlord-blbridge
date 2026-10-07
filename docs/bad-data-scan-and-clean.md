# 坏数据扫描 / 清理 —— 分层与边界（2026-10-07）

> **一句话**：**扫描在 BlBridge（只读），清理在 MOD（写）**。
> 本文记这个分工的**技术理由**、已实测的 API 事实、以及清理器**必须遵守的硬约束**。

---

## 一、分工（用户裁定，2026-10-07）

| 组件 | 位置 | 权限 | 状态 |
|---|---|---|---|
| **坏数据扫描** | **BlBridge**（`bl_scan_bad_data`） | **只读** | ✅ 已实现 + 真机验证 13/13 |
| **数据清理** | **三合一 MOD** | 写（存档） | ⏳ 待实现 |

### 为什么清理必须放 MOD 侧（四条，都不是偏好问题）

1. **BlBridge 的前提是「删模块 = 完全回退」**
   它是纯只读工具（`bl_patches` / `EngineProbe` 均自述"不建实例、不 patch、不改 IL/数据"）。
   **写存档会破坏这个可回退性** —— 删掉模块后，被改过的存档仍然是被改过的。

2. **写存档需要 Harmony / Finalizer 级兜底**
   清理过程中若抛异常，必须靠 **`CrashGuard`**（MOD 侧能力）吞掉并记账，
   否则一次半完成的清理 = 存档半损坏。

3. **存档语义只有 MOD 知道**
   三合一 MOD 才清楚"哪些对象是它自己造的、清掉是否安全"。
   BlBridge 不引入任何 mod 语义（那是它保持通用的原因）。

4. **与上游一致**
   Crash Doctor / Save Cleaner 都以 **mod 形式**运行（MCM 菜单 / 自动），
   **没有**作为外部进程改存档的成品 —— 这个形态差异本身是信号。

⇒ 因此：**BlBridge 只做扫描 + 出报告**（它的报告就是 MOD 清理的判据）。

---

## 二、已实测的 API 事实（清理器实现前必读）

反射实测（2026-10-07，本机 1.4.8；探针留在 `E:\Document\_cleanerapi\`）。

### 2.1 ★★ `MBReadOnlyList<T>` 的 `Add`/`Remove` **不能信**

```
类型: TaleWorlds.Library.MBReadOnlyList`1
接口: IList<T>, ICollection<T>, IEnumerable<T>, IList, ICollection, IReadOnlyList<T>, ...

Add      public=True   explicit=False  IL字节=74   含throw指令=0
Add      public=False  explicit=True   IL字节=50   含throw指令=0
Remove   public=True   explicit=False  IL字节=23   含throw指令=0
Remove   public=False  explicit=True   IL字节=22   含throw指令=0
Insert / Clear / RemoveAt  同样是 public=True, 含throw指令=0
内部承载字段: **无**（一个都没扫到）
```

**⚠️ 结论（重要）**：类型名是 `ReadOnly`，但 `Add`/`Remove` **报告存在、且 IL 里没有 `throw`**。
这意味着**不能**从"方法存在"推断"调用会抛 `NotSupportedException`"。
**两种可能的坏结局，都不可接受**：
- 要么调用后**抛异常**（至少能看见）；
- 要么**静默无效** —— 报"清理成功"但数据没变（**更危险**，因为看起来成功了）。

⇒ **清理器实现前必须先用受控实验判定它到底是哪种**（见 §四）。
**在此之前，任何"调用 `MobileParties.Remove(...)` 就完事"的写法都是猜测。**

> ★ **2026-10-08 订正（读本节前必看）**：上一条"必须"是**针对"直删集合"这条路线**说的，
> 而该路线**本身已被否定** —— 见 §2.3：五类对象**都有** `*Action` 正规入口，
> 清理**一律走 `*Action` 类，不碰 `MobileParties`/`Clans` 集合**。
> ⇒ 本节的"不能猜"**依然成立**，但它约束的是**不要走直删路线**，而不是"先做实验才能清理"。

### 2.2 集合都是只读视图

`Campaign.MobileParties` / `.Clans` / `.Kingdoms` / `.Settlements` 全是 `MBReadOnlyList<T>`。
`Campaign` 上没有公开的 `Remove*`/`Destroy*`（扫到的只有 `OnDestroy()` / `RemoveEntityComponent`，
后者是 EntityComponent 体系、与部队无关）。

### 2.3 各类对象的"正规销毁入口"（★ 已订正，2026-10-08）

> **订正声明（重要）**：本节原先的结论是"**部队/家族/军团没有 `Destroy`/`Remove`/`Disband`
> ⇒ 只能硬改对象图**"。**该结论错误，已作废。**
>
> **错在哪**：只在**实体类本身**（`MobileParty` / `Clan` / `Army`）上找销毁方法。
> 而 Bannerlord 的引擎语义是 **"实体类上没有方法 ≠ 没有正规入口"** ——
> 销毁一律走 `TaleWorlds.CampaignSystem.Actions` 命名空间下的 **`*Action` 类**
> （这是引擎唯一被设计出来、会派发事件 + 维护所有关联容器的路径）。
>
> 订正依据：`read_csharp_type` 反编译实测
> （`TaleWorlds.CampaignSystem/TaleWorlds.CampaignSystem.Actions/*.cs`），
> 以及本项目**自己已在用**该模式的既有代码 `RBM/SubModule.cs:396-415`。

| 对象 | **正规入口（`*Action` 类）** | 内部做什么（实测） |
|---|---|---|
| **部队** `MobileParty` | `DestroyPartyAction.Apply(PartyBase destroyerParty, MobileParty destroyedParty)`<br>`DestroyPartyAction.ApplyForDisbanding(MobileParty, Settlement)` | 派发 `OnMobilePartyDestroyed` + `OnMapInteractableDestroyed` → **`destroyedParty.RemoveParty()`**；`ApplyForDisbanding` 先 `LeaveSettlementAction.ApplyForParty` 再派发 `OnPartyDisbanded` |
| **家族** `Clan` | `DestroyClanAction.Apply(Clan)`<br>`ApplyByFailedRebellion(Clan)` / `ApplyByClanLeaderDeath(Clan)` | 派发 `OnClanDestroyed` → **`DeactivateClan()`** → 逐队伍 `DestroyPartyAction.Apply` → 处理英雄（`KillCharacterAction.ApplyByRemove`）→ 聚落改属（`ChangeOwnerOfSettlementAction.ApplyByDestroyClan`）→ `FactionManager.RemoveFactionsFromCampaignWars` → 退王国（`ChangeKingdomAction`）|
| **军团** `Army` | `DisbandArmyAction` 共 **12 个** `ApplyBy*`（逐条列举：`ApplyByReleasedByPlayerAfterBattle` / `ApplyByArmyLeaderIsDead` / `ApplyByNotEnoughParty` / `ApplyByObjectiveFinished` / `ApplyByPlayerTakenPrisoner` / `ApplyByFoodProblem` / `ApplyByInactivity` / `ApplyByCohesionDepleted` / `ApplyByNoActiveWar` / `ApplyByUnknownReason` / `ApplyByLeaderPartyRemoved` / `ApplyByNoShip`）| **`army.DisperseInternal(reason)`**（按成因选 reason；`DismissalRequestedWithInfluence` 还会扣影响力/关系）|
| **物品** `ItemRoster` | `RemoveIf(Func<ItemRosterElement,int>)` → `IEnumerable<ItemRosterElement>`<br>`Remove(ItemRosterElement)` / `AddToCounts(ItemObject, int)`<br>★ 读取一律 **`GetItemNumber(ItemObject)`**（不是 `GetElementNumber(int index)`）| vanilla 自带 `RemoveZeroCountsFromRoster` / `ReplaceInvalidItemsWithTrash`（均 `private`，由 `OnLoadStarted`/`LoadInitializationCallback` 调用）|
| **任务** `QuestBase` | `CompleteQuestWithCancel(TextObject)` / `CompleteQuestWithFail(TextObject)` | （原有结论，**正确**，保留）|
| `QuestManager` | `OnQuestCompleted(QuestBase, QuestCompleteDetails)` | （原有结论，**正确**，保留）|

#### 两条必须一起看的补充（都来自实测反编译，不是推测）

1. **`CampaignObjectManager.RemoveClan` 不是通用入口，别无条件调。**
   `DestroyClanAction.ApplyInternal` **只在 `destroyedClan.IsRebelClan` 为真时**才自己调
   `Campaign.Current.CampaignObjectManager.RemoveClan(...)`。
   ⇒ 对**非叛乱**家族直接 `RemoveClan` 会绕过同一批次里的英雄/聚落/战争清理。
   （本项目 `RBM/SubModule.cs:396-415` 是"`DestroyClanAction.Apply` **之后**再补 `RemoveClan`"，
   针对的是 `Culture == null` 的**坏家族**这一特殊场景，注释明写"只 `Clans.Remove` 会漏 `Factions`"。）
2. **`ApplyInternal` 是 `private`**，外部只能调那些 `Apply*` 公开重载 —— 这正是"入口存在但必须选对语义"的原因
   （例如军团清理要按成因挑 `ApplyByInactivity` 等，而不是自己造一个 reason）。

#### ★ 保留的硬约束（**理由仍然成立，不要因本节订正而忽略**）

**绝不**用 `Campaign.MobileParties.Remove(...)` / `Campaign.Clans.Remove(...)`
这类**集合直删**去"清理"对象 —— **即使 §2.1 的 `MBReadOnlyList` 行为未知，这条也成立**：

- `Campaign.MobileParties` / `.Clans` 是 **`MBReadOnlyList<T>`**（§2.2），而引擎另有
  **`MobilePartyLocator`** 等索引结构（按位置/会话维护的查找表）；
- **集合直删只动一个容器，索引与关联对象都留在原处** ⇒ 得到一个"查得到、但已不在册"的**不一致状态**，
  **比不清理更糟**（后续引擎遍历索引时触雷）。
- `*Action` 类的价值正是**替你把"所有该动的地方"按引擎顺序动完**（派发事件 + 清理关联 + 更新索引）。
  ⇒ **一律走 `*Action` 类**，这是本节的唯一正确结论。

### 2.4 存档相关 API

```
MBSaveLoad.SaveAsCurrentGame(CampaignSaveMetaDataArgs, String saveName, Action<...>) -> Void
MBSaveLoad.QuickSaveCurrentGame(...) / AutoSaveCurrentGame(...)
MBSaveLoad.DeleteSaveGame(String saveName) -> Boolean
SaveManager.Save(Object target, MetaData metaData, String saveName, ISaveDriver driver) -> SaveOutput
MBSaveLoad.GetSaveFiles(Func<SaveGameFileInfo,Boolean>) -> SaveGameFileInfo[]
MBSaveLoad.GetSaveFileWithName(String) -> SaveGameFileInfo
```

- 写新档：**`SaveAsCurrentGame(meta, saveName, cb)`**（可指定**新名字** ⇒ 天然满足"不覆盖原档"）；
- 但**备份原档**：本工具链目前**没有**直接拿 `.sav` 绝对路径的 API
  ⇒ 备份要么用文件系统（`Game Saves\*.sav`，已验证存在），要么先查 `SaveGameFileInfo` 是否含路径。

---

## 三、清理器**必须**遵守的硬约束（先定，再写代码）

1. **先备份**：动任何数据前，把原 `.sav` **复制**一份（带时间戳），并**验证副本可读**（大小/哈希）。
2. **写新档，不覆盖原档**：用 `SaveAsCurrentGame(meta, "<原档名>_cleaned_<时间>")`。
   （Crash Doctor 的做法：`CrashDoctor_cleaned_<date>.sav`，`The original .sav file is never overwritten`。）
3. **只清 `severity=broken`**：`suspect` **绝不自动清**
   （它含大量"游戏正常的过渡状态"，如刚被灭的家族）。
4. **清理后必须验证"能读档"**：写完后重新 `MBSaveLoad.LoadSaveGameData(新档名)` 一次，
   失败 ⇒ **明确报错并保留原档**（不能说"清理成功"）。
5. **逐项可回滚**：报告里逐条列出"清掉了什么"（对象标识 + 判据），便于人工核对。
6. **不许批量猜**：清理项必须来自**扫描报告的具体条目**，不做"扫描器没报但我觉得该清"的推断。

---

## 四、关于 `MBReadOnlyList` 的受控实验（★ 已降级：可选，不再是清理的前提）

> **2026-10-08 订正**：本节原先写作"清理器实现前的**第一步**（受控实验，别跳）"。
> 自 §2.3 订正后（五类**都有** `*Action` 正规入口），**清理不需要碰这两个只读集合**
> ⇒ 本实验**不再是清理路径的前置条件**。
> 保留它只有一个理由：它回答"**直删到底会不会静默无效**"—— 这仍是一个值得知道的引擎事实
> （§2.1 那条"`Add`/`Remove` 存在、IL 里无 `throw`"的**观测**有效，只是**不该用它给清理定性**）。

在**动真档之前**，若仍想回答 `MBReadOnlyList.Add/Remove` 被调用时到底怎样，可写一个**只读探测**：

```
判据（必须成对）：
  A 调 Add(一个假对象) ⇒ 抛异常 / 静默无效 / 真的加进去了？
  B 调 Remove(一个真实对象) ⇒ 同上
  C 若静默无效 ⇒ **在临时存档**上试（绝不碰玩家真档）
```

★ 原先写的"只有在 A/B 都是'抛异常'或'确实生效且可回滚'时才继续设计清理"**已作废** ——
清理走 `*Action` 类（§2.3），**不依赖本实验的结果**。
本实验的结论无论哪种，都**不改变"一律走 `*Action` 类"**这条路线。

---

## 五、当前状态

| 项 | 状态 |
|---|---|
| 扫描（BlBridge，只读） | ✅ `bl_scan_bad_data`，真机 13/13，修掉 2 处误报 |
| 清理（MOD，写） | ⏳ **未开始**；先做 §四 的受控实验 |
| **各类对象的正规销毁入口** | ✅ **已订正（2026-10-08）**：**五类全都有** `*Action` 正规入口（见 §2.3），**不需要硬改对象图** |
| `MBReadOnlyList` 写行为 | ⚠️ **未知**（Add/Remove 存在且无 throw ⇒ 不能猜）；★ 但**这条已不再是清理的必经之路** —— 见 §2.3 的"一律走 `*Action` 类" |
| 存档备份 API | ⚠️ **未找到**直接路径 API，需用文件系统或继续查 |

> ★ **订正带来的实质变化**：原先"部队/家族/军团没有正规入口 ⇒ 清理 = 硬改对象图 = 高风险"
> 这个**阻塞理由已消失**。§四 的 `MBReadOnlyList` 受控实验**仍值得做**（它回答"直删会不会静默无效"），
> 但**不再是清理路径的前提** —— 清理一律走 `*Action`，不碰这两个集合。

### 一条纪律（写给实现者）

**扫描器已经用真机数据纠正过我两处误报**（匪帮 9 条 / 物品 60 条）。
清理比扫描危险得多：**误报会变成"删掉正常数据"**。
⇒ 清理器的每条判据都要有"**这种输入不许清**"的对照组，
且**先在临时存档上验证**，再碰真档。
