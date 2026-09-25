# BlBridge 项目规则（给 AI agent / 协作者）

## 一、编码规则（硬规则，2026-09-25 定）

**全仓库一律 UTF-8 无 BOM + LF 行尾**。理由与两条走过的弯路见 `README.md` §十一。

| 位置 | 规则 |
|---|---|
| 源文件（`.py` / `.cs` / `.md` / `.json` / `.xml` / `.ps1` / `.txt`） | UTF-8 **无 BOM**，LF 换行 |
| Python 的 stdout/stderr | 每个输出脚本入口调用 `bl_common.safe_streams()`（= `reconfigure(encoding="utf-8")`） |
| Python 读写文件 | 显式 `encoding="utf-8"`；要容忍 BOM 的输入用 `utf-8-sig`；二进制一律 `"rb"`/`"wb"` |
| Python 读子进程输出 | 显式 `encoding="utf-8"`；**不要**依赖 `PYTHONIOENCODING` / locale |
| C# 控制台 | `Console.OutputEncoding = Encoding.UTF8` |
| C# 读写文件 | `Encoding.UTF8` 或 `new UTF8Encoding(false)`（写文件**不带 BOM**） |
| PowerShell 写文本文件 | **别用** `Set-Content -Encoding UTF8`（PS 5.1 会加 BOM）⇒ `[System.IO.File]::WriteAllText($p, $s, (New-Object System.Text.UTF8Encoding $false))` |

**核心判据**：乱码的根因是**写入编码 ≠ 读取编码**，不是文案语言。
本项目的输出消费端是调用这些工具的 AI / 管道。**本机实测**：系统 `chcp 936` / ANSI `gb2312`，
而宿主 PowerShell（5.1）的 `[Console]::OutputEncoding` 是 `utf-8`，Python 却默认按 locale(cp936) 写
⇒ 写入/读取不一致就整片 `U+FFFD`。所以口径钉死在 UTF-8，**不依赖控制台代码页**。

> 改任何输出前，先读 `bl_common.safe_streams()` 的 docstring。

### Gauntlet prefab 硬规则（2026-09-25 增，真机确证）

- **`ButtonWidget`（或任何"自己处理点击"的 widget）只要内含子 widget，就必须写 `DoNotPassEventsToChildren="true"`。**
  一手源码理由：`EventManager.CollectEnableWidgetsAt` 遍历时**子 widget 先进候选表**，而
  `Widget.OnPreviewMousePressed/OnPreviewMouseReleased` 基类默认返回 `true`，
  `GetWidgetAtPositionForEvent` 取**候选表里第一个**接受事件的 widget ⇒ 不写这个属性时，事件被内层
  `TextWidget` 吃掉，`ButtonWidget.HandleClick()` 永不执行 ⇒ `Command.Click` 静默不触发
  （**真人点与合成点一样没反应**，极易误诊成"输入不达"）。
  官方 `SandBoxCore/GUI/Prefabs/CustomBattle/CustomBattleScreen.xml` 与 EBT 的所有按钮都带它。
- **`GUI/Prefabs/**/*.xml` 是 XML**：注释里**不得出现 `--`**（`<!-- ... -- ... -->` 会让解析器报错），
  别拿 `--` 当破折号，用逗号或冒号代替。

### 控制通道参数命名硬规则（2026-09-25 增，v0.8.12 真机踩到）

- **请求参数名不得与信封键同名**：`src/Jmini.cs` 是**扁平**读取器（按"文本里第一个 `"key"`"取值），
  而请求信封是 `{"protocolVersion":…,"id":…,"method":…,"parameters":{…},"issuedUtc":…}`
  ⇒ 参数叫 `id` / `method` / `parameters` / `protocolVersion` / `issuedUtc` 时，**读到的是信封的值**。
  实例：`open_ui` 原本用 `id` 传入口名，真机上恒被解析成请求 id（`unknown_ui: 没有这个入口 id: 85a0d3d3fba54d4e`），
  已改名为 **`uiId`**。
- **离线自测抓不到这一类缺陷**：`tools/bl_selftest.py` 的假游戏端用真 JSON 解析（认嵌套），
  天然没有这个碰撞。⇒ 新增控制通道参数**必须**在真机上过一遍（或至少确认参数名不在信封键集合里）。
- 对照纪律仍然适用：新增/改名参数时，要能指出"哪种输入会红"（例：`uiId=NoSuchThing` 必须报 `unknown_ui`）。

### InitialStateOption 硬规则（2026-09-25 增，v0.8.12 真机踩到）

- **`Module.ExecuteInitialStateOptionWithId(id)` 只在主菜单可执行。** 它的 action 基本都是
  `MBGameManager.StartNewGame(...)`，而后者做的是 `CleanAndPushState(GameLoadingState)` + 重跑一遍数据加载。
  **在已经加载了 Game 的状态下执行它，状态机会卡在 `GameLoadingState` 不再推进**
  （真机：从 `CustomBattleState` 调 `open_ui`，90 s+ 仍是 `GameLoadingState`、日志无异常、进程仍响应 ⇒ 只能重启游戏）。
  ⇒ `UiEntry.HandleOpenUi` 有主菜单闸门（`ActiveGameStateName() != ""` → `wrong_state_for_ui`）。
  上界判据：主菜单与从面板 `PopState` 回主菜单后，`Game.Current.GameStateManager.ActiveState` 都取不到（名字为空串）。
- **进得去就要出得来**：给 agent 开了"能进官方 `CustomBattleState`"的门，就必须有对应出口
  （`close_ui` 的 `state` 白名单含它）；否则它既出不来、又不能在那里再 `open_ui`（被上一条闸门挡住）。

### 双分包硬规则（2026-09-25 增，v0.8.12）

- **一个 mod 包 = 两个单元**：`Modules\BlBridge\`（游戏与启动器读：DLL / `GUI\` / `ModuleData\`）
  + `Modules\BlBridge\mcp\`（AI 读：MCP 服务器 + `manifest.json` + `README.md`）。玩家用界面，AI 用控制通道，**共用同一条后端**。
- `Modules\BlBridge\mcp\*.py` 是**构建产物**：唯一真相源是仓库 `tools\*.py`，`build.ps1 -Deploy` 整份复制。
  **手改部署副本会在下次部署被覆盖**（`mcp\README.md` 把这条写给了 AI，别再让它踩）。
- `module\mcp\manifest.json` 的 `version` 与 `module\SubModule.xml` 一样**由构建从 `BridgeConfig.Version` 同步**，
  禁止手维护（手维护必漂移：清单说 0.8.9、DLL 说 0.8.12）。
- **界面与端口必须共用同一个入口**：面板按钮构造与 MCP 同形的请求并调用同一个
  `ScenarioRunner.Start`；返回主菜单走 `UiEntry.RequestClose`（官方 `CustomBattleVM.ExecuteBack` 的 `PopState` 同路径）。
  禁止为 UI 另写一份开战/返回逻辑 —— 那样"界面上能做的、端口做不了"就会在结构上成立。
- 从**部署副本**（`Modules\BlBridge\mcp\`）跑 `bl_build_check` 时，它旁边没有 `src/`：源码段会跳过并如实标注
  `sourceCheck: skipped_no_src_dir`，结论降级为"三段一致"。**不要**把那个不存在目录当成源码目录报出去。

## 二、遇到「不支持 UTF-8 的代码」怎么处理

**不是**把文案改成英文，**也不是**加"编码兼容"补丁（那只是把错配挪到另一边），而是
**把那段代码改成显式 UTF-8**：

| 症状 | 改法 |
|---|---|
| `open(p)` / `open(p, "w")` 没写编码（走 locale） | 补 `encoding="utf-8"` |
| `decode("gbk")`、`encode("gbk")`、依赖 `locale.getpreferredencoding()` | 换 `utf-8` |
| 子进程输出按 locale 解 | `subprocess.run(..., text=True, encoding="utf-8")` |
| `Set-Content ... -Encoding UTF8`（PS 5.1 加 BOM） | 用 `[System.IO.File]::WriteAllText(..., UTF8Encoding($false))` |
| JSON/配置按严格 `utf-8` 读却带了 BOM | 写侧去 BOM；读侧容忍 BOM 用 `utf-8-sig` |
| 文件带 CRLF | 转 LF（见「怎么验证」里的体检脚本会报出来） |

## 三、改完必跑（验证）

```powershell
python tools\check_repo_encoding.py                  # 编码体检：UTF-8 无 BOM + LF，有违规则退出码 1
python tools\bl_selftest.py                          # 离线自测（合成数据 + MCP 协议 + 控制通道 + 构建链）
python tools\bl_metrics_selftest.py                  # 指标模块自测
powershell -ExecutionPolicy Bypass -File tools\jsontest\build_and_run.ps1   # C# 离线单测（Jmini/RequestGuard/SquadSpec…）
python tools\bl_check_clock_reset.py                 # 多轮日志「时钟同源」校验（见下）
```

**第 5 项（时钟同源校验）是正式必跑项**，理由不是「它很重要」，而是**它的判据本身就是对照实验**：

```
python tools\bl_check_clock_reset.py --since <部署时刻 ISO>   # 新产物：期望 PASS
python tools\bl_check_clock_reset.py                          # 全量：历史已知失败样本**必须仍报 FAIL**
```

**一次跑必须同时给出两边结果**：
- 新产物 **0 失败** —— 说明修复生效；
- 历史日志里的已知失败样本**仍被报出** —— 说明这个校验器**真的在判定**，而不是永远返回 PASS。

**判据长什么样（v0.8.10 起，三条）**：锚时钟 = `state`（每轮必有，由 `TelemetryBehavior` 写出），
轮内区间 = `[0, 锚上界 + 3.0 s]`；
① `round_start.time ≈ 0`；② 其余 `round_*` 落在轮内区间；③ **每个非 `round_*` 类型的 `time` 起点**
不晚于锚上界（越界的类型逐一点名）。
⚠️ **不要**把参考区间改回"除 `round_*` 外全体事件的 min/max" —— 那种取法**反单调**：
异源事件自己的大值会抬高区间、反而让判定放宽（v0.8.9 首版因此把 2 份异源文件判成 PASS，见 PROGRESS §二十）。
退出码：`0` 通过 / `1` 有 FAIL 或无 PASS / **`2` 环境或数据不足**（含"`--since` 下没有任何新的多轮文件"）。

只有新产物那半边，等于没有对照组：一个恒返回 PASS 的脚本、一个数据源接错的脚本、
一个正则写错把所有事件都跳过的脚本 —— 都会「通过」。**这也是整个项目所有验证的通用纪律**：
`bl_metrics` 的合成事件手算期望、`bl_compare` 的换边双跑、jsontest 的「应报 / 不应报」样本对，
本质都是同一件事 —— **没有对照组的验证不是验证，只是自证**。

细节见修复记录与 PROGRESS §十一 判据 6：`round_*` 的 time 曾用整场累计值当轮内值
（v0.8.5–v0.8.8），**症状是静默错** —— 无异常、无报错，只是下游按 time 切窗口时算错。

## 四、发现与结论的纪律：必须能附上对照组

**任何"发现了问题"的结论，severity 都必须可追溯到一个具体对照组。写不出来的，降级或撤回。**

对照组的合法形式（按强度，优先用靠前的）：

| 类型 | 形态 | 本仓库实例 |
|---|---|---|
| **受控实验** | 只改一个变量，两种取值对照 | `FlushEveryLine` True/False 各自实测「未 Close 前可见字节」 |
| **代码内对照** | 同类实现一个有防护、一个没有 | 三个 orders 循环里两个有 `if (s == null) continue;`，`ApplyOrders` 没有 |
| **数据对照** | 实测产物里「应存在 / 应缺席」 | 106 份日志：`source="respawn"` 零命中 ⇒ 该路径从未执行 |
| **受控样本对** | 「应报 / 不应报」成对 | jsontest 77 项断言 |

**关键是「事实层」与「后果层」要各自有对照**：

- 事实层：代码确实如此（读代码 / 跑实验）。
- 后果层：**这个后果真的可达吗**（该场景在真实数据/调用路径里是否出现过）。

**最容易失手的是后果层。** 实测教训：某条「0 基 / 1 基 group 编号混用」的发现，
事实层成立（两个事件确实一个 0 基一个 1 基），但后果层写的「同批日志里关联会整体错位一组」
**不可达** —— 那两个事件在全部日志里从未共存（其中一个零命中），该条由 medium **降为 low**。

**操作检查法**：给后果层也指定对照 = 「找一份能触发该后果的真实数据或调用」。
找不到 ⇒ 后果不可达 ⇒ severity 必须下调。这一步能拦住绝大多数**严重度膨胀**
（把推演出的后果当成已发生的问题写进「影响」段）。

**另一条同样硬的纪律：「验证」必须留下可复现入口。**

一条写着「验证通过 / N/N 通过」的结论，如果**说不清怎么复现**（没有命令、没有入口、没有样本文件），
就应当**视作未验证**。实测教训（v0.8.10，2026-09-25）：PROGRESS §十九 记过场景守卫的
「受控样本对验证（7/7 通过）」，但全仓库找不到任何能跑出这 7 项的入口，且**它与代码事实相反** ——
守卫当时写的是裸拼接 `FolderPath + "SceneObj/"`，而 `FolderPath` 不含尾分隔符 ⇒ 每次 `start`
都会被判成 `unknown_scene`，「放行」在真机上不可能发生（已就地更正）。
⇒ 操作检查法：**写下"验证通过"之前，先写出那一条能复现它的命令**；写不出来，就不要写"通过"。

另外两条与编码相关的纪律（踩过坑）：

- PowerShell 5.1 读**无 BOM 的 UTF-8 `.ps1`** 会按 ANSI 解 ⇒ `build.ps1` 刻意只用 ASCII；
  往脚本里写中文前先确认编码，或保持 ASCII。

## 五、托管/原生边界：请求参数是「不可信输入」，不是「本地配置」

**凡把请求里带来的字符串直接喂给引擎原生 API 的地方，都必须先自己校验。**

理由是结构性的，不是「小心一点」就能解决：

- 引擎的原生边界**不做防御**。反编译 `TaleWorlds.Engine.dll` 可见 `Scene.Read(string sceneName)`
  的整个实现体就是一次 `EngineApplicationInterface.IScene.Read(...)` —— **无返回值、不抛托管异常**。
- 因此托管侧的 `try/catch` 在这种调用点上**结构性无效**：它拦得住托管异常，拦不住 native 访问违规。
  失败后果是**进程级**（`0xC0000005`），不是「这个请求失败」。

⇒ **判据**：一个调用点若同时满足「跨托管/原生边界」+「失败后果是进程级」，
则**必须在调用前自己校验**，不能指望 catch、不能指望引擎报错。

实测案例（v0.8.9，2026-09-25）：`Start` 把请求里的 `scene` 字符串直接交给
`new MissionInitializerRecord(scene)`。传入不存在的 `bridge` ⇒ 6 ms 后
`Loading xml file: SceneObj/bridge/scene.xscene` → `0xC0000005`，整个游戏进程死。
对照：同一请求传 `battle_terrain_a`（存在于 `SandBoxCore/SceneObj/`）则完全正常。

修复方式值得照抄：**绕开原生边界去校验** —— 引擎自己就是扫所有模块的 `SceneObj/`，
所以托管侧遍历 `ModuleHelper.GetActiveModules()` 的 `FolderPath`、用 `Path.Combine` 探测
`SceneObj/<scene>/scene.xscene`，判定结果与引擎一致，且**不需要进 native**。

**v0.8.10 补充：这道守卫自己曾有两处缺陷（外部审查 B3/B5，已修）**
① 拼接写成裸 `FolderPath + "SceneObj/"`，而 `FolderPath` **不含尾分隔符**
（反编译 `ModuleInfo.LoadWithFullPath`：`FolderPath = fullPath;` 紧接着 `FolderPath + "/SubModule.xml"`），
于是探测路径恒不存在 ⇒ **每一次 `start` 都被判成 `unknown_scene`**（fail-closed：不崩，但桥不可用）。
该守卫提交后 **0 次真机运行**，所以一直没暴露；而 PROGRESS §十九 当时却记着「7/7 通过」（见 §四 新增的纪律）。
② 遍历用 `GetAllModules()` 而非引擎同款 `GetActiveModules()` + `IsActive` —— 未启用模块里存在同名场景时
会 fail-open（放行一个引擎解析不到的场景）。
⇒ 结论：**思路（绕开原生边界自己校验）没问题，但校验器自己也要有对照组。**

顺带记一条操作教训：**`meta.mission` 不是场景名。** 本次崩溃的触发方式是从旧日志里读了
`meta.mission = "bridge"` 当成 `scene` 传进去 —— 那是遥测记录的 mission 标识
（`SubModule.MissionOrigin`）。**日志字段的语义要从写它的代码确认，不能从名字猜。**
- 脚本里 `print` 非 ASCII 时不要靠控制台编码兜底 —— 见第一节的 `safe_streams()`。
