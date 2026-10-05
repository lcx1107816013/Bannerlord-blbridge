# 从 Bannerlord.GABS 抄来的经验（L3）

> 建立日期：2026-09-27
> 来源：[BUTR/Bannerlord.GABS](https://github.com/BUTR/Bannerlord.GABS)（MIT）的 `AGENTS.md` / `README.md` / 目录结构
> 性质：**读文档得到的经验，不是我们实测的结论**。每条都标了它对我们意味着什么，以及我们自己的对照事实。

**为什么单独记一份**：这些是别人用真机调试换来的坑。抄经验比抄代码便宜得多（零依赖、零构建改动），而漏掉它们要在真机上各踩一遍。
**取用纪律**：**脱壳抄** —— 抄实现体（对游戏 API 的调用序列、回调、判据），外壳换成我们自己的 `CommandPump` method + `Jmini`。它那边的工具是 `[Tool]` 特性 + 源生成器 + Lib.GAB 外壳，**直接拖 `Tools/*.cs` 会把它的框架一起拖进来**，那就不是抄、是换形态了。（我们已经有成功样本：`bl_skip_video` 就是脱壳抄它的 `core/skip_video`。）

---

## 一、直接可用（与我们的现状对得上）

### 1. 线程模型：所有游戏 API 调用必须回主线程

> 上游 AGENTS.md：「所有游戏 API 调用必须通过 `MainThreadDispatcher` 在主线程执行」；并给出两条前置检查：战役工具前查 `Campaign.Current != null`，战斗工具前查 `Mission.Current != null`。

**我们的对照**：`src/CommandPump.cs` 挂在 `SubModule.OnApplicationTick`（主线程每帧），所以这条天然满足 —— **不是因为我们做对了，而是因为文件 IPC 的读点就在主线程**。
但"前置检查"那半边我们有自己的硬证据，而且是更重的一侧：**在 mission 之外碰 `MovementOrder` 会抛 `TypeInitializationException` 并把该类型永久标记为不可用**（`BattleOrders` 的类注释）⇒ 只能重启游戏。所以我们把 `order` 在无 mission 时直接拒为 `no_mission`。
⇒ **结论：主线程 + 前置检查这一对是硬约束，不是风格**。将来 L2 加战役工具时，每个 method 都要在入口做「`Campaign.Current != null`」同形的检查，并且**失败要显式**、不能靠 catch。

### 2. 动作 / 等待器成对，禁止 sleep

> 上游 AGENTS.md：动作类工具立即返回，配套阻塞式等待器轮询到状态落定；调用方**永远不需要** `sleep` / `delay` / 手写轮询。中断原因由 `reason` 字段区分（`menu` / `conversation` / `incident` / `inquiry` / `scene_notification` / `screen_change` / `arrived`）。

**我们的对照**：`bl_wait_for_state` 已经是这个形状（`idle/loading/running/ended/error`），而且是 MCP 侧等待。命名也对齐了（见 `gabp-naming.md`）。
⇒ **可抄的那一半**：`reason` 的**多值归因**思路。我们现在超时归因已经拆成四值（`bridge_not_loaded` / `process_exited` / `probe_unknown` / `no_response`），是同一件事的另一个实例 —— 说明"超时不是一个值"这条我们本来就在做，他们的 `reason` 词表可以当参照扩充。

### 3. Gauntlet 点击链没有一条统一可靠的注入路径

> 上游 AGENTS.md：`ui/click_widget` 的 `HandleClick` 在某些界面（如库存）**不会触发 XAML 命令绑定**。

**我们的对照**：我们在 v0.8.12 踩的是**同一类坑的另一面** —— 合成鼠标注入**到不了我们自己的 Gauntlet 层**，根因是 `EventManager` 的事件命中顺序（`docs/prototype-ui-probe-2026-09-25.md`；官方 `ButtonWidget` 必须写 `DoNotPassEventsToChildren="true"` 才拿得到点击）。
两条合起来的结论：**"用鼠标点 Gauntlet"这条路无论从哪一侧走都不可靠**。这**外部印证**了我们 v0.8.14 的决策 —— 人走官方界面、AI 走端口（`open_ui` / `close_ui`），不去抢鼠标。

### 4. 别进 3D 任务场景

> 上游 AGENTS.md（硬约束）：定居点 NPC 交互**绝不进入 3D 任务场景**（领主大厅 / 竞技场 / 酒馆），改用 `conversation/start`。

**我们的对照**：这是"进场景 = 换 mission，成本高、失败面大"的另一个实例。我们自己的同形教训是 `open_ui` / `close_ui`：**进得去就要出得来** —— 官方 `ExecuteInitialStateOptionWithId` 只在主菜单可用（主菜单闸门），给了 `open_ui` 的门就必须给 `close_ui` 的出口，否则那个状态既出不来也再进不去。
⇒ **L2 加任何"进场景"的能力前，先把出口一起设计出来。**

---

## 二、抄 L2（战役层工具）时会用到的具体坑

| 上游说法 | 用在哪 |
|---|---|
| 玩家队伍追踪用 `SetMoveGoToPoint` **循环**，**不要**用 `SetMoveEngageParty` | `party/move_to_settlement` 类工具的实现体 |
| "安全旅行协议"：移动前先 `detect_threats { range: 20 }`，`canOutrun: false` 就不要出行 | 工具的参数/返回值设计；形状上与我们的**开战前 preflight**（构建四段哈希 + 场景存在性 + 兵种可解析）同构 |
| 定居点交互走 `conversation/start` + `conversation/wait_for_state` 的对话树导航 | `conversation/*` |
| `ui/get_viewmodel_property` 支持点号路径、数组下标、深层路径（`WeaponDesign.PieceLists[0].SelectedPiece.TierText`） | 通用 Gauntlet 反射读取 —— **这条对我们价值最高**，是"AI 自己读官方界面状态"的通用能力 |

⚠️ **一条必须自己复核的前置**：上游用 `supported-game-versions.txt` 声明支持的游戏版本，README/AGENTS 里出现的示例是 **v1.3.15**，**比本机的 v1.4.8 旧**。抄 L2 的实现体时，所有 API 都要拿我们的 `TaleWorlds.*` 引用程序集重核一遍签名，**不能照抄就编译**。

---

## 三、评估过、**不采用**的（记下来，免得以后重复讨论）

| 上游做法 | 为什么不采用 |
|---|---|
| **程序集名自动带游戏版本**（`{ModuleId}.{GameVersion}`，如 `Bannerlord.GABS.v1.3.15`） | 目的是同一模块多版本共存。我们是单版本 + `bl_build_check` 的四段哈希链（源码 / 构建产物 / 部署文件 / 进程内 DLL），已经能精确识别"进程里跑的是旧 DLL"。多版本共存对我们没有需求，反而会让 `SubModule.xml` 的 `DLLName` 不再是常量。 |
| **工具运行时注册 + 静态代理工具**（`games.tool_names` / `games.tool_detail` / `games.call_tool`） | 它这么做是因为客户端不处理 `notifications/tools/list_changed`，运行时注册的工具**不直接可见**。我们现在是**静态工具表**（32 个），没有这个问题，agent 直接调即可。⇒ 我们的静态表在这种场景下是**优势**，别为了"动态可扩展"去换形态。 |
| **要求 `Bannerlord.Harmony` / `ButterLib` / `MCM` 作为必需依赖** | 它需要 Harmony 拦截 inquiry/incident、用 ButterLib 的 DI 与生命周期钩子。BlBridge 的设计原则是**不 patch 任何方法、零第三方依赖**（`Jmini.cs` 就是为此写的），出问题删掉模块即完全回退。 |
| **`Beta_Debug` / `Stable_Debug` 构建配置 + `Bannerlord.ReferenceAssemblies` NuGet 包** | 我们是 `build.ps1` 直调 Roslyn `csc.exe`、显式列游戏 bin 里的引用程序集、无 NuGet。两套都行，但换构建体系会把"部署前查游戏进程 + 备份旧 DLL + SHA256 校验"这套已经验证过的链推倒重来 —— 不值。 |

---

## 四、抄录记账（MIT 署名）

| 来源 | 取用内容 | 取用日期 |
|---|---|---|
| [BUTR/Bannerlord.GABS](https://github.com/BUTR/Bannerlord.GABS)（MIT） | `AGENTS.md` 的线程模型与前置检查、"动作/等待器成对且禁止 sleep"约定、Gauntlet `click_widget` 的 XAML 绑定坑、"不进 3D 场景"硬约束、`SetMoveGoToPoint` / `detect_threats` / `conversation/*` / `ui/get_viewmodel_property` 的做法 | 2026-09-27 |
| 同上 | `Tools/InventoryTools.cs` 的 `inventory/get_inventory` 实现体（MainParty.ItemRoster + Hero.MainHero.Gold 的读法与 no_campaign/no_main_party 前置检查）→ `src/InventoryProbe.cs` | 2026-09-27（读源码本体；已落地 v0.8.36） |
| 同上 | `Tools/CoreTools.cs` / `Tools/MenuTools.cs` / `launch-bannerlord.ps1` 的等待器口径与启动脚本（读源码本体，未落地代码）→ 见 §五 | 2026-09-27 |

**本次抄录没有引入任何依赖，也没有改动任何游戏侧行为** —— 只新增了本文件与 `docs/gabp-naming.md`、`tools/gabp_names.json`、`tools/bl_check_gabp_names.py` 四个纯文本/纯标准库文件。


---

## 五、第二轮：为了"启动/进入慢"读的三个实现体（2026-09-27 晚）

> 起因：用户指出"BLSE 启动快、跳视频快，但等主菜单 → 进自定义战斗、以及开战部署的检测都慢"。
> 这轮读的不是文档，是 `Tools/CoreTools.cs`、`Tools/MenuTools.cs`、`launch-bannerlord.ps1` 三个文件本体
>（GitHub raw，MIT）。结论先行：**它没有"快速进游戏"的魔法，差距在我们自己的等待策略**。

### 1. `core/wait_for_state`：500ms 轮询 + 二次验证（已对齐）

- 轮询默认 **500ms**（参数 `pollIntervalMs`，钳 100–5000ms），上限 120s。
- 判据优先级：`Campaign.Current` → `Mission.Current`（mission）/ `NextGameMenuId`（game_menu）/ 否则 campaign_map；
  再退到 `GameStateManager.ActiveState` 类名；都没有 = unknown。子串大小写不敏感匹配。
- **即使 state 已匹配，还要查 `ScreenManager.TopScreen` 不含 "Loading"** —— 和我们 v0.8.20 抄的同一条。
- 归因不放在等待器里，另给 `core/check_blockers`（conversation_active / inquiry_active / mission_active:<scene> /
  menu_active:<id> / paused）。我们的超时归因是四值（bridge_not_loaded / process_exited / probe_unknown /
  no_response），同构；`check_blockers` 的词表将来做战役层时可以当参照。

### 2. 进入界面与我们是同一条路 ⇒ 状态落地慢是官方成本，抄不到解法

`core/new_game` 从主菜单开新战役：`Module.CurrentModule.GetInitialStateOptions()` 找选项 →
`IsDisabledAndReason()` 检查 → **`DoAction()`**。与我们 `open_ui`（`ExecuteInitialStateOptionWithId`）同形。
它也配 `core/skip_video` 跳开场动画。**没有**任何绕过/加速状态加载的手段 —— `CustomBattleState`
落地那 ~30s（真机实测，open_ui 后主线程忙到控制通道都不应答）是官方加载成本，谁都得等。

### 3. `core/save_game` 的死锁教训（将来要用的前置知识）

`SaveAs` 会**阻塞主线程**，若在 dispatcher 的 `ProcessQueue` 里调会**死锁**，所以它用
`MainThreadDispatcher.ScheduleSave` 延迟执行 + `core/wait_for_save`（判据 `MBSaveLoad.IsSaveGameFileExists`，
1s 轮询）确认完成。我们将来加任何"会长时间占主线程"的 method，都得是这个"触发即返回 + 等待器确认"的形状。

### 4. `launch-bannerlord.ps1`：一条值得记账的小技巧

启动前把 `Documents/.../engine_config.txt` 里的 `safely_exited = 0` 改回 `1`，
**从源头消掉 Safe Mode 弹窗**（我们现在是靠 bl_launch.ps1 自动应答弹窗，没出过事 —— 记账不抄，
哪天自动应答失手再启用）。其余没有等待逻辑：`/singleplayer` + `_MODULES_..._MODULES_` 串直接起。

### 5. `MenuTools`：战役 GameMenu 的读/选（战役层 L2 的样板）

`menu/get_current` 读 `Campaign.Current.CurrentMenuContext`（虚拟菜单选项含 isEnabled/isLeave/tooltip）；
`menu/select_option` 反射内部方法 `RunConsequenceOfVirtualMenuOption`（虚拟索引→物理索引），失败回退公开方法。
两条工程要点：**选项 id 会在 get 与 select 之间漂移 ⇒ 执行时刻用 id 重新定位**；"动作 + 等待器成对"。

### 6. 由此落到我们头上的改动（已做，真机 A/B 见 PROGRESS §三十 8）

| 我们原来的 | 改成 | 为什么 |
|---|---|---|
| await_confirm 等 45s 下限 | 菜单就绪即返回（下限只留给 auto_open） | 下限防的是"自动 fire"，这条路径不 fire |
| auto_open 下限 45s | 20s（可配） | 45s 定于硬判据出现之前；硬判据已在 |
| 轮询 2s / 5s，超时 15s | 1s，落地确认超时 6s | 上游 500ms 量级；15s 超时会在加载期吃掉全部轮询 |
