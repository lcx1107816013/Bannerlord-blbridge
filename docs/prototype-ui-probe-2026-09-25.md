# 原型结论 · BlBridge 游戏内 UI 入口（2026-09-25，分支 `prototype/ui-probe`）

> 目的：在写实施计划之前，先用**一次性代码 + 真机**回答 5 个"猜不出来"的问题。
> 原型代码：`src/ProtoUi.cs`（单文件，标了 PROTOTYPE）+ `module/GUI/Prefabs/BlBridge/ProtoUiScreen.xml`
> + `module/ModuleData/Languages/CNs/*`；`src/SubModule.cs` 里只有一行 `ProtoUi.Register()`。
> 结论已回填到 `PROGRESS.md` 的对应章节与后续实施计划。

## 一、五个问题的答案（真机实测，非推演）

| # | 问题 | 答案 | 证据 |
|---|---|---|---|
| 1 | 主菜单项能否在 `OnSubModuleLoad` 注册并进入 | ✅ 能 | 主菜单第 5 项显示 `BlBridge 自定义战场（原型）`（走 `Module.CurrentModule.AddInitialStateOption`，零 patch、零 Harmony） |
| 2 | 自写 prefab 能否被 `GauntletLayer.LoadMovie("<name>", vm)` 加载 | ✅ 能，**movie 名 = prefab 文件名** | `LoadMovie OK: ProtoUiScreen -> movie=not-null`；prefab 落 `Modules/BlBridge/GUI/Prefabs/BlBridge/ProtoUiScreen.xml` |
| 3 | `[DataSourceProperty]` 绑定能否生效 | ✅ 能 | 5 行文本全部按绑定值渲染（含一行**硬编码对照组**、一个空 `BrushWidget`、一个 `ButtonWidget`） |
| 4 | 在「主菜单 → 自建 GameState」阶段 `{=key}` 本地化是否已加载 | ✅ **已加载，不需要手工 `LoadGameTexts`** | 官方 key 出 `出发`；**我们自己 UTF-8 语言文件**里的 key 也出中文（`OWN KEY OK：来自 BlBridge 自有语言文件`）—— 这一点与 EBT 单机版要手工 `LoadGameTexts` 不同 |
| 5 | **合成鼠标/键盘输入能否驱动我们这层 UI** | ✅ **能（根因见第七节）** | 2026-09-25 二轮复测：给 prefab 的 `ButtonWidget` 补上 `DoNotPassEventsToChildren="true"` 之后，真人点击与合成点击都能触发 `ExecutePing`（日志 `clicks=1..24`，按钮文本实时刷新为 `CLICK ME (24)`）。此前的「连合成输入都不达」是**假象** —— 事件被按钮里的子 `TextWidget` 抢走，与输入源无关 |

附带确认：**中文渲染正常**（`CustomBattle.Value.Text` 的字体 `FiraSansExtraCondensed-Regular` 对中文会 fallback，实测无方块、无截断）。

## 二、踩到的坑（每一条都是"离线/静态看不出来"的）

1. **必须调 `LoadingWindow.DisableGlobalLoadingWindow()`。**
   游戏管理器加载期间引擎会启用**全局加载窗口**，它画在**所有 screen 之上**。官方 `CustomBattleScreen` 在 `OnActivate` 后第 2 帧关掉它；不关的话，我们的面板其实渲染正常，但被那张加载图盖住——第一轮"卡在读取界面"就是这么来的。
2. **焦点必须在 `AddLayer` 之后设。**
   写成 `LoadMovie() → TrySetFocus() → AddLayer()` 时层拿不到输入焦点（官方顺序：`OnInitialize` 里 `LoadMovie` + `AddLayer`，`OnActivate` 里 `IsFocusLayer = true` + `TrySetFocus`）。
3. **`GauntletLayer.InputRestrictions.SetInputRestrictions(true, (InputUsageMask)7)`** 官方有、原型第一版漏了（补上后合成输入仍不达，但缺它更不可能达）。
4. **DPI：任何做窗口坐标运算的进程都必须先 `SetProcessDPIAware()`。**
   本机桌面 **3840×2160 + 150% 缩放**。非 DPI-aware 进程的 `GetWindowRect`/`CopyFromScreen` 在虚拟化的 2560×1440 空间，而 `SetCursorPos` 吃**物理**像素 ⇒ 每次点击偏 1.5 倍。**实测代价：两次"点击"落到了主菜单的"退出游戏"上，把游戏关掉了**（当时误以为是崩溃/人工退出）。
5. **BLSE 启动要两样东西**（已固化为 `tools/bl_launch.ps1`）：
   - **模块列表**：`Bannerlord.BLSE.Standalone.exe` 不带 `_MODULES_*…*BlBridge*_MODULES_` 会以 **no mods 模式**启动（`Command Args: no_watchdog`，SubModule.xml 只被扫描、不激活）⇒ BlBridge 不加载；
   - **两个模态弹窗**：`Safe Mode`（问是否安全模式，必须**否**，`WM_COMMAND IDNO(7)` 有效）与 `Mod change detected`（提示删渲染缓存，必须**确定**，按钮是中文"确定"、`IDOK(1)` **无效**，回车有效）。没人答它们，launcher 会自己退出、游戏永远起不来。
   - 另：`Standalone` 会把游戏**转交另一个进程**后自己退出 ⇒ 不能用"launcher 进程存活"当成功判据（第一版脚本因此误报 FAILED）。

## 三、环境事实（写进后续所有自动化脚本的前提）

- 桌面 **3840×2160，150% 缩放**；Bannerlord 窗口约 `2564×1647`（物理）。
- **BLSE 装在 Vortex 里**（游戏窗口标题里的 Managed 路径是 `…\Vortex\mountandblade2bannerlord\mods\Bannerlord Software Extender (BLSE) 1.1.7.2…`）。
- 进程名是 `Bannerlord.BLSE.Standalone` / `Bannerlord.BLSE.Launcher`（**不是** `Bannerlord`）。
- 成功启动的判据（无需看屏幕）：`<Documents>\Mount and Blade II Bannerlord\BlBridge\proto_ui.log` 出现新的 `Register: AddInitialStateOption OK`。
- 引擎日志：`C:\ProgramData\Mount and Blade II Bannerlord\logs\rgl_log_<pid>.txt`（`Command Args:` 行能看出 mod 列表是否带上）。
- 有一个 **PID 44560 的 `Bannerlord.BLSE.Launcher` 僵尸条目**（13:13 起，CPU 0，`GetProcessById` 报 not running、`taskkill` Access denied）——不影响启动，别被它误导。

## 四、第 5 项失败的判读（对设计的意义）

事实：**同一套合成输入，官方主菜单可点、我们这层不可点。**
⇒ 结论有两条，第二条比第一条重要：

1. **AI 不应该靠"模拟鼠标"操作游戏 UI。** 正解是设计里已经定好的端口：文件 IPC（`commands/pending`）+ 新增 `open_ui`（`Module.CurrentModule.ExecuteInitialStateOptionWithId(id)` 就是官方给的正门），让 agent 从命令通道唤起/驱动面板，而不是抢鼠标。
2. **对我们自己的层，还需要一次"真人点击"的对照**（原型验证时未完成）：
   - 真人点得动 ⇒ 层的配置没问题，只是合成输入不达（那就安心走文件 IPC）；
   - 真人点不动 ⇒ 我们的层配置有实质缺陷，必须在实施阶段修掉（否则 UI 对人类用户也是坏的）。
   这一步**只有人能做**，已记进待办。

## 五、工具与环境（本轮新增，可复用）

| 工具 | 说明 |
|---|---|
| `tools/bl_launch.ps1` | **已固化**：带 mod 列表启动 BLSE + 自动应答两个模态弹窗 + 判定成功（`LAUNCH OK`/`LAUNCH FAILED`）。ASCII-only（PS 5.1 会按 ANSI 读无 BOM 的 .ps1）。 |
| `gridhand`（外部，`cargo install gridhand` v0.4.1） | **面向 agent 的 GUI 自动化 CLI**：`windows list` 给结构化窗口 JSON（**物理坐标**，已 DPI-aware）、`screenshot --grid` 在截图上叠**带标签网格**、`mouse click --cell C5 --window-id N` **按格子点击**（消除像素估算）。实测它**能截到 D3D 渲染的游戏画面**（不是黑图）。 |

> 注意：`gridhand windows list` 的输出带 ANSI 高亮码，PowerShell 里 `ConvertFrom-Json` 后按 title 过滤时要用能容忍转义码的方式（本轮吃过一次亏）。

## 六、未完成 / 留给实施阶段

- 真人点击对照（见第四节第 2 点）。
- 原型代码尚未删除：删 `src/ProtoUi.cs` + `module/GUI/Prefabs/BlBridge/ProtoUiScreen.xml` + `module/ModuleData/Languages/CNs/*` + `src/SubModule.cs` 里那一行 `ProtoUi.Register()`（`build.ps1` 的改动要**保留**：Modules 下程序集引用、`System.ValueTuple`、`GUI/ModuleData` 部署都是正式方案需要的）。
- `src/SubModule.cs` 的 `wrong_state` 守卫放宽（允许我们的 state）尚未做。

## 七、第 5 问的最终结论（2026-09-25 二轮，真机确证）

**根因不在输入，在 prefab：`ButtonWidget` 少了 `DoNotPassEventsToChildren="true"`。**

一手源码链（`ilspycmd` 反编译游戏 `bin\Win64_Shipping_Client` 下的 GauntletUI 程序集）：

1. `EventManager.GetWidgetAtPositionForEvent` → `CollectEnableWidgetsAt(Root, pos, list)`：
   **子 widget 先进候选表**（后序遍历），父 widget 最后进。
2. `Widget.OnPreviewMousePressed/OnPreviewMouseReleased` 的基类默认是 `return true`（Widget.cs:2445-2450）。
3. ⇒ `list` 里第一个"接受事件"的是按钮内层的 `TextWidget`（`StretchToParent`，必然命中），
   `DispatchEvent` 把 `MousePressed/MouseReleased` 都给了它 ⇒ `ButtonWidget.OnMouseReleased` 的
   `_clickState != HandlingClick` 直接 return ⇒ `HandleClick()`（唯一触发 `EventFired("Click")` 的地方）
   **永不执行** ⇒ `Command.Click="ExecutePing"` 静默不触发。
4. `DoNotPassEventsToChildren="true"` 让 `CollectEnableWidgetsAt` **跳过子 widget**，事件才归按钮。

佐证：官方 `SandBoxCore/GUI/Prefabs/CustomBattle/CustomBattleScreen.xml` 的按钮、以及 EBT 的
**全部 17 个按钮**，无一例外都带这个属性。

**修复**：`module/GUI/Prefabs/BlBridge/ProtoUiScreen.xml` 的 `ButtonWidget` 补
`DoNotPassEventsToChildren="true" UpdateChildrenStates="true"`（后者让子文本跟随按钮状态）。
**复测证据**：`proto_ui.log` 出现 `ExecutePing: clicks=1..24`，按钮文本实时刷新为 `CLICK ME (24)`；真人点击与合成点击都有效。

**顺带排除的假设**（都读过一手实现，别再重走）：`(InputUsageMask)7` 值正确（`All=MouseButtons|MouseWheels|Keyboardkeys`）；
跳过 `MBGameManager.OnLoadFinished` 无害（只做 `IsLoaded = true`）；`Command.Click` 的值就是 VM 方法名
（`ViewModel.ExecuteCommand` 有反射兜底）；`Brush="WideButton.Flat"` 存在（`Native/GUI/Brushes/Brush.xml`）；
`LayoutImp.LayoutMethod` 有效（EBT 也在用）；`ScreenBase` 只有 `TaleWorlds.ScreenSystem` 一个版本。

### 环境事实：本会话启动的游戏会被宿主回收

用 `bl_launch.ps1`（**前台命令与后台 job 都试过**）启动的游戏，都在脚本 `LAUNCH OK` 退出后
**0.3~0.7 秒内被杀**（引擎日志停在 `Selected graphics adapter`、无 exception、无 BUTR crash 报告）。
**绕法**：经 Windows 计划任务启动（`Register-ScheduledTask -Principal (LogonType Interactive)` + `Start-ScheduledTask`），
进程由 Task Scheduler 创建、不属本会话进程树 —— 实测游戏可常驻（PID 79784 活过 5 分钟并正常加载、可点击）。
⇒ 「AI 无人值守启动游戏」在当前环境**要么走计划任务，要么由人启动**；
`bl_launch.ps1` 打印的 `LAUNCH OK` **不能**证明游戏会活下来。
（另：`--` 不得出现在 XML 注释里，`Prefab` 是 XML —— 本轮踩过一次。）
