# 实施计划：游戏内 UI 入口 + 双分包（mod 包 / 内嵌 MCP 包）

> ⚠️ **本计划的自建面板部分（W3/W4/W5）已作废（保留作历史记录）**：写完后用户裁定
> "官方自定义战斗本身就是完整入口（战斗 / 围攻 / 村庄 / 海战 / 海上掠夺 + 玩家类型 + 选择攻守方 +
> 全套地图参数），我们复刻属重复建设"，于是 **面板整套被删除**（v0.8.14，见 `PROGRESS.md` §二十五）。
> 仍然有效并被保留的是：**控制通道**（`list_ui` / `open_ui` / `close_ui` + CLI/MCP）、
> **官方场景表读取**（`src/CustomBattleScenes.cs`）、**遥测与批量实验**能力、**双分包**（mod 包 + `module/mcp/`）。
> 另：本文件第 3 节那句"结论已回填到 PROGRESS.md 的对应章节与后续实施计划"当时**不属实**
> （PROGRESS 里没有对应章节），已在 v0.8.15 的 `PROGRESS.md` 补记并纠正。

> 分支 `prototype/ui-probe`（本文件所在分支）。上游结论文档：`docs/prototype-ui-probe-2026-09-25.md`。
> 纪律依据：`AGENTS.md`（§三 改完必跑、§四 结论必须能附对照组、§五 托管/原生边界）。
> 本计划的每一项都写成「改动 + 验收判据 + 对照组」，**写不出对照组的项降级为待办**。

## 0. 本轮目标（用户裁定，2026-09-25）

1. 把原型的四个"猜不出来"的答案**并进正式实施计划**（本文件）。
2. 实现 **agent 正门**：让 AI 不抢鼠标也能唤起/驱动游戏内面板。
3. **产品化 UI**：面板不再是 5 行探针文本，而是玩家真正可用的自定义战场入口。
4. **最终形态拆成两个单元、打在一个包里**：
   - **单元 A（mod 包）**：`Modules\BlBridge\`，游戏/启动器读取；玩家用界面，AI 用控制端口。
   - **单元 B（MCP 包，放在 mod 包内）**：`Modules\BlBridge\mcp\`，AI 读完它的介绍即可加载并把 mod 的控制端口接成 MCP 工具。

## 1. 现状核实（本会话实测，非引用旧结论）

| 项 | 值 |
|---|---|
| 仓库 / 分支 | `C:\Users\LCGX\CodeBuddy\20260923171333\BlBridge`，`prototype/ui-probe` @ `543fe57`，**工作区干净** |
| 对照分支 | `main` @ `657b982`（攻城轮，无 UI 相关代码） |
| 原型 5 问 | 全部通过（第 5 问根因 = prefab 的 `ButtonWidget` 缺 `DoNotPassEventsToChildren="true"`；真机证据 `ExecutePing: clicks=1..24`） |
| 记载的三项未完成 | ⓐ `open_ui` 未实现 ⓑ `wrong_state` 守卫未放宽 ⓒ 原型临时代码未删、分支去留未定 |
| **一处记载不实（本轮发现并修正）** | `docs/prototype-ui-probe-2026-09-25.md:6` 称"结论已回填到 `PROGRESS.md` 的对应章节与后续实施计划"，实测 `PROGRESS.md` 无 UI 章节、`docs/superpowers/plans/` 无 UI 计划 ⇒ 本文件即补齐该缺口，并在 PROGRESS 补 §二十三 |

### 1.1 一手 API 取证（`bannerlordsage` 索引，非推测）

| 事实 | 出处 |
|---|---|
| `public IEnumerable<InitialStateOption> GetInitialStateOptions()`（按 `OrderIndex` 排序） | `Module.cs:1550-1553` |
| `public InitialStateOption GetInitialStateOptionWithId(string id)`（找不到返回 `null`） | `Module.cs:1555-1565` |
| `public void ExecuteInitialStateOptionWithId(string id)` = `GetInitialStateOptionWithId(id)?.DoAction();` ⇒ **静默失败**（找不到不报错、无返回值） | `Module.cs:1567-1570` |
| `InitialStateOption` 公开成员：`Id` / `Name` / `OrderIndex` / `IsHidden` / `IsDisabledAndReason` / `EnabledHint` / `DoAction()` | `InitialStateOption.cs:6-38` |
| `ClearStateOptions()` 会清空列表（`Module` 上有此方法） | `Module.cs:1528-1531` |

**官方注册的入口 id（一手源码）**：`CustomBattle`(5000) · `Multiplayer`(9997) · `Options`(9998) · `Credits`(9999) · `Exit`(10000) · `Editor`(-1)；
另有 `SandBoxNewGame`（NavalDLC 在调 `ExecuteInitialStateOptionWithId("SandBoxNewGame")`，注册点在 SandBox 侧）。

> **这条事实直接取消一个既有前提**：`ScenarioRunner.cs:224` 的报文要求"玩家手动点进 Custom Battle 停在选兵界面"。
> 有了 `open_ui`，agent 自己就能走 `open_ui id=CustomBattle` 走到那个界面 ⇒ 该前置条件从"必须人做"变成"agent 可做"。

### 1.2 MCP 侧取证（为双分包设计）

| 事实 | 依据 |
|---|---|
| `tools/bl_mcp.py` **纯标准库**（`io/json/os/re/subprocess/sys/time`）+ 本地模块（`bl_analyze` / `bl_common` / `bl_sage`） | 实测 import 列表 |
| 日志/战斗/命令目录来自 `~Documents\Mount and Blade II Bannerlord\BlBridge`，**与 mod 安装位置无关** | `bl_common.default_log_dir()` |
| 只有 Warbandlord 配置读写需要游戏根目录 | `bl_mcp.py:492` 读 `BANNERLORD_DIR` |
| ⇒ 装在 `Modules\BlBridge\mcp\` 的 MCP **可以零配置自定位**：自身路径上溯 3 级 = 游戏根 | 设计推论（验收见 W6） |

**`.mcpb` 打包规范（联网核实）**：`.mcpb`（原 DXT）= 一个 ZIP，最小形态只需 `manifest.json`；
CLI 为 `mcpb init` / `mcpb pack`；Claude for macOS/Windows 打开即可一键安装，微软侧（MSIX + 包标识）可在应用安装时注册 MCP。
两条限制要记住：① 官方**推荐 Node.js**（宿主自带运行时），Python 走 `server.type: "python"|"uv"`，宿主需有 Python；
② **agent session 不支持 bundle**（默认需开发者模式）⇒ 不能把"一键安装"当成唯一分发路径。

**同类先例（都能搜到，形态可借鉴）**：Minecraft MCP Mod（Java 客户端 mod 暴露 JSON-RPC 端点）、MC-MCP（Fabric mod + HTTP）、
MoLing-Minecraft、网易 ModPC（mod 进游戏才开对接端口 + 本地 `server.py` 起 `http://127.0.0.1:3002/mcp` + AI 配地址）、
rimcp-v2（RimWorld 代码索引 MCP）、unity-mcp（把 Editor 暴露给 AI）。
⇒ 结论：**"mod 负责世界、MCP 负责翻译"**是已被反复验证的形态；我们的差异点是**控制端口用文件 IPC 而不是 HTTP**（避开 URL ACL 提权，见 `CommandPump.cs` 注释）。

## 2. 目标形态：两个单元、一个包、一条管线

```
G:\...\Mount & Blade II Bannerlord\Modules\BlBridge\        ← 一个 Nexus 包，装完就是两个单元
├── SubModule.xml                 (启动器读)
├── bin\Win64_Shipping_Client\BlBridge.dll
├── build_manifest.json
├── GUI\Prefabs\BlBridge\...      ← 单元 A 的界面
├── ModuleData\...                ← 单元 A 的本地化
└── mcp\                          ← 单元 B：MCP 包（AI 读这里的介绍后加载）
    ├── README.md                 ← 「给 AI 的安装说明」（入口文档）
    ├── manifest.json             ← mcpb 形态元数据
    ├── register_mcp.py           ← 把本目录登记进宿主 MCP 配置（幂等、自定位）
    ├── bl_mcp.py + bl_*.py       ← 服务器本体（与 tools\ 同源，构建时复制）
    └── BlBridge.mcpb             ← 可选：一键安装包（宿主支持时用）
```

**一条管线（本计划最重要的一条设计约束）**：

```
玩家 → 游戏内面板「开始战斗」┐
                            ├→ 同一个请求 JSON → ScenarioRunner.Start(id, raw) → mission
AI   → MCP 工具 start_battle ┘
```

UI **不重实现**开战逻辑，只构造与 MCP 同形的请求并交给同一入口 ⇒ 「界面上能做的事，端口一定能做」是结构保证，而不是靠人工同步两份代码。
（验收：同一请求经两条路径得到同形的 `accepted` 响应 —— 见 W4。）

**控制端口 = 文件 IPC**（`commands\pending|done`）。本轮**不引入** TCP 监听：它是第二条代码路径 + 新的安全面，
且对 agent 而言文件 IPC 的语义（请求留痕、过期作废、协议版本）已经够用。TCP 列为未决（§6）。

## 3. 工作项

| # | 工作项 | 改动落点 | 验收判据 | 对照组 |
|---|---|---|---|---|
| **W1** | `open_ui`（参数 **`uiId`**）/ `list_ui` / `close_ui`（参数 `state`）三个协议方法 | `src/CommandPump.cs`（`Dispatch`）+ `src/UiEntry.cs` | ① `list_ui` 返回官方 id 表（含 `CustomBattle`）；② `open_ui` 返回 `requested:true` + `stateBefore`（**fire-and-forget，不谎报已打开**）；③ **入口 id 不存在时返回 `unknown_ui` 而不是静默成功**；④ 非主菜单执行时返回 `wrong_state_for_ui`（✎ 见 §7 F2） | 用 `ExecuteInitialStateOptionWithId` 的静默语义做对照：`unknown_ui` 这条断言必须能红（先调官方静默路径，再调我们的） |
| **W2** | `wrong_state` 守卫放宽为**允许集合** | `src/ScenarioRunner.cs:224` | 停在自有面板态时 `start_battle` 返回 `accepted`（不再 `wrong_state`）；集合外的态仍 `wrong_state` | 同一份代码、两个取值：`CustomBattleState`（旧行为，必须仍通过）与自造字符串（必须仍拒绝） |
| **W3** | 产品化面板：`BattleSetupState` + `BattleSetupScreen` + `BattleSetupVM` + `module/GUI/Prefabs/BlBridge/BattleSetupScreen.xml` | 新增 4 个文件 | 真机：面板显示桥状态与可选参数，「开始战斗」触发一场普通战斗；按钮**必须带** `DoNotPassEventsToChildren="true"`（`AGENTS.md` §一 硬规则） | 与原型探针屏同时存在 ⇒ 同一次启动里新旧两块屏可对照 |
| **W4** | 面板与端口共用后端 | `src/BattleSetupVM.cs` 调用 `ScenarioRunner.Start`（构造同形请求） | 同一个请求（兵种/人数/场景）经 UI 与经 MCP 各跑一次，两次响应字段同形、都 `accepted` | 两条路径的响应做字段级 diff |
| **W5** | 暴露到 CLI / MCP / README | `tools/bl_cmd.py`（`open-ui` / `list-ui`）、`tools/bl_mcp.py`（2 个工具）、`README.md` 协议 method 表、`tools/bl_selftest.py` 断言 | `bl_selftest` 断言数随之更新且全绿；MCP 工具数 21 → 23 | 自测里"工具清单"断言本身就是对照（少一个就红） |
| **W6** | 双分包部署 + MCP 包元数据 | `build.ps1 -Deploy` 复制 `mcp\`；新增 `module/mcp/`（README + manifest.json + register_mcp.py 模板） | 部署后 `Modules\BlBridge\mcp\bl_mcp.py` 可**零配置**跑通 `ping`（不需要 `BANNERLORD_DIR`） | 删掉环境变量跑一次；再从 `tools\` 跑一次（必须仍能跑）——两条路径都要通 |
| **W7** | 原型退役 | 删 `src/ProtoUi.cs`、`module/GUI/Prefabs/BlBridge/ProtoUiScreen.xml`、`SubModule.cs:51` 一行；`build.ps1` 的 Modules 程序集引用 / `System.ValueTuple` / `GUI`+`ModuleData` 部署**保留** | 删除后 `build.ps1 -Deploy` + 真机仍进得去新面板 | W3 真机通过**之后**才执行（顺序本身是对照：先证新屏可跑，再删旧的） |
| **W8** | 真机验收 | 计划任务启动（宿主会回收本会话进程树，见结论文档 §七） | 见 §4 清单 | 每条判据都要有"应报 / 不应报"两侧 |
| **W9** | 文档同步 | `PROGRESS.md` §二十三；`README.md`；`AGENTS.md`（若产生新硬规则） | 三份文件一致，无"已回填"这类无法复现的表述 | 与 §1 表格里那条不实记载对照 |

## 4. 验收清单

### 4.1 离线（不需要游戏）

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Deploy     # 编译 + 部署（含 mcp\ 子包）
python tools\bl_selftest.py                                      # 合成数据 + MCP 协议 + 控制通道 + 构建链
python tools\bl_metrics_selftest.py
python tools\check_repo_encoding.py                              # UTF-8 无 BOM + LF
powershell -ExecutionPolicy Bypass -File tools\jsontest\build_and_run.ps1
python tools\bl_check_clock_reset.py --since <部署时刻 ISO>       # 新产物
python tools\bl_check_clock_reset.py                             # 历史已知失败样本必须仍 FAIL
```

### 4.2 真机

| # | 步骤 | 期望 | 不应出现 |
|---|---|---|---|
| 1 | `list_ui` | 含 `CustomBattle` / `BlBridgeBattleSetup` | 空表 |
| 2 | 主菜单时 `open_ui uiId=CustomBattle` | 进入官方选兵界面（`stateName=CustomBattleState`） | 静默无事发生（那说明又踩了 `?.DoAction()` 的静默语义） |
| 3 | 回主菜单，`open_ui`（默认 id = 我们的面板） | 面板出现且可交互 | 卡在读取界面（漏 `LoadingWindow.DisableGlobalLoadingWindow()`）、按钮点不动（漏 `DoNotPassEventsToChildren`） |
| 4 | 面板停在最前时 `start_battle` | `accepted`，战斗正常打完 | `wrong_state` |
| 5 | 面板上真人点「开始战斗」 | 与第 4 步同形响应 | 只有 AI 能用、真人用不了（那等于 UI 是装饰） |
| 6 | 战斗结束后面板仍可返回/重开 | 状态回到 `idle` 后可再次 `start_battle` | 残留 `MissionState` |

## 5. 风险与回退

| 风险 | 处置 |
|---|---|
| 新面板真机不通 | W7 刻意排在 W3 之后 ⇒ 原型屏仍在，可立刻回到已知可用形态 |
| `ClearStateOptions()` 被调用（如切模块/回主菜单）导致我们的入口消失 | `open_ui` 每次先 `GetInitialStateOptionWithId` 判存在，不存在就**重新注册**（而不是报错） |
| `wrong_state` 放宽放宽过头 | 允许集合写成常量表 + 注释指向本条判据；集合外一律拒绝（对照组见 W2） |
| 双分包让 `build.ps1 -Deploy` 变成"复制目录树" | 部署后打印每个子目录的文件数（沿用现有 `deployed N/` 输出），不静默 |
| 编码/行尾 | 所有新文件 UTF-8 无 BOM + LF，收尾跑 `check_repo_encoding.py` |

## 6. 未决（需用户裁定，本轮不擅自决定）

1. **控制端口形态**：维持文件 IPC（本计划的选择）／另加 localhost TCP 监听（更接近"端口"，但多一条代码路径 + 安全面）。
2. **`.mcpb` 是否作为强制分发物**：宿主支持时一键装很好，但 agent session 不支持 bundle ⇒ 注册脚本仍是主路径。
3. **面板要暴露哪些能力**：本轮计划只做「兵种/人数/场景/战术组 + 开始战斗 + 桥状态」；
   是否把遥测报告、批量跑批、护甲旋钮也搬进面板，属于下一轮的范围裁定。
4. **分支去留**：本分支（`prototype/ui-probe`）在 W7 之后是否改名为 `feat/ui-entry` 并合回 `main`。

## 7. 真机修订（2026-09-25 晚，实测）

W1–W8 已在真机上跑完（7 条正向判据 + 2 条反向判据，结果表与证据见 `PROGRESS.md` §3）。
计划本身被真机改了**三处** —— 都是"离线推理得不出、只有跑起来才知道"的：

| 修订 | 原计划 | 真机事实 | 定案 |
|---|---|---|---|
| **F1** 参数名 | `open_ui` 的参数叫 `id` | `src/Jmini.cs` 是**扁平**读取器，信封自带 `"id"` ⇒ 恒读到请求 id（真机报 `unknown_ui: 没有这个入口 id: 85a0d3d3fba54d4e`） | 改名 **`uiId`**；写进 `AGENTS.md`。**离线自测用真 JSON 解析 ⇒ 结构上抓不到这类碰撞** |
| **F2** 执行时机 | "唤起入口"随时可调 | 非主菜单执行会让状态机**卡死在 `GameLoadingState`**（90 s+ 不推进、只能重启游戏） | 加**主菜单闸门** `wrong_state_for_ui`；并给 `close_ui` 补 `state` 白名单（含官方 `CustomBattleState`）作为出口 |
| **F3** 默认值 | 面板从可用场景列表取首项 | 首项是字典序第一的 `battle_terrain_001`，与 CLI/MCP 的默认 `battle_terrain_a` **悄悄分叉** | 显式优先 `battle_terrain_a`（"同一条管线"要求默认值也一致） |

另外两条实测口径要记下来（避免下轮重踩）：

- **主菜单的 `activeState` 是空串**（`Game.Current` 为 null 或状态栈为空），不是 `MainMenuState`；
  自测假游戏端的 `activeState: "MainMenu"` 只是假数据，别拿它当判据。
- **入口加载是跨帧的**：`open_ui` 之后 `StartNewGame` 走完整数据加载，真机实测 **约 30 s** 才 `OnLoadFinished`
  ⇒ 判据必须轮询（`list_ui` 的 `activeState`），不能 `open_ui` 完立刻断言（我第一轮就是这样误判了一次）。
