# BlBridge · 骑砍 2 战斗遥测 + AI 推演桥（版本以 `out/BlBridge.manifest.json` 为准）

把游戏里**每一次命中/阵亡**落成 JSONL，并让外部 Agent **自己开 AI 对 AI 战斗、读战果、改配置** —— 用来**验证**离线平衡模型（血量公式、伤害结算）到底对不对。

**设计原则**：只读游戏状态、不 patch 任何方法、不联网、不改游戏逻辑。出问题删掉模块即完全回退。

---

## 一、两个能力

| 能力 | 说明 | 是否需玩家参与 |
|---|---|---|
| **遥测** | 每场战斗的每次命中/阵亡/溃逃/10 秒快照 → JSONL | 正常打即可（也可由 AI 推演自动产生） |
| **AI 推演** | 由 MCP 触发，游戏自动开一场**无玩家**的战斗、10 倍速跑完、返回战果 | 不需要手打；只需停在**官方自定义战斗界面**（v0.8.14 起只认它 —— 自建面板已删），AI 可用 `bl_open_ui` 自己走进去；加 `spectate=true` 让镜头进上帝视角观战 |

控制通道 = **本地文件 IPC**（`commands/pending/` ⇄ `commands/done/`），不是 HTTP：
无端口、无 URL ACL 提权、无防火墙问题，且崩溃后请求/响应都留痕。协议 v1 的信封、响应不变式、
会话身份（`runToken` + `processStartedUtc`）、版本硬校验、`outcomeUncertain` 不盲重试等规范，
抄自 Bannerlord Coop 团队的 `CoopMcpServer` / `LiveTestProtocol`（见 `阶段2-复用尽调报告.md`）。

**请求里的 `method` 有 17 个**（`src/CommandPump.cs`；名字**不是** CLI 子命令名 ——
直接手写请求时容易猜错，v0.8.10 真机回归实测踩到 `unknown_method: start`）。

> 这张表曾写"只有这 9 个"，而 v0.8.16–v0.8.32 期间又加进来 6 个（`ghost_camera` / `camera_speed` /
> `skip_video` / `cheat_mode` / `order` / `control_agent`）而这里没跟上 —— **文档漂移**。
> 现在这条不变量**可执行**：`python tools\bl_check_gabp_names.py` 双向核对「源码里的 method ↔ 命名表」，
> 缺失与多余都报错（并附带 `--selftest` 的注入故障对照组）。
> GABP 命名、分类词表与抄录记账见 **`docs/gabp-naming.md`**。

| method | 参数 | 等价入口 |
|---|---|---|
| `ping` | — | `bl_cmd.py ping` / MCP `bl_status` |
| `status` | — | `bl_cmd.py status` / MCP `bl_battle_status` |
| `start_battle` | `attackerTroop`/`defenderTroop`/`attackerCount`/`defenderCount`/`scene`/`durationCapSec`/`orders`/`playerSide`/`spectate`/`dummySide`/`dummyArmor*`/`dummyBodyItem`/`freezeDummies`/`unlimitedAmmo`/`allowAnyState`/`rounds`/`roundEndAlive`/`roundSwap`/`roundSpawnAttacker`/`roundSpawnDefender`/`randomSeed`/`attackerGroups`/`defenderGroups`；**v0.8.41 起**另带 `attackerTacticLevel`/`defenderTacticLevel`/`terrain`/`randomTerrainSeed`/`aiFriendlyFireMultiplier`/`keepCorpses`/`sceneLevel`/`timeOfDay` | `bl_cmd.py start` / MCP `bl_start_battle` |
| `abort` | — | `bl_cmd.py abort` / MCP `bl_abort` |
| `fast_forward` | `enabled`（`"true"`/`"false"`） | `bl_cmd.py fastforward` / MCP `bl_fast_forward` |
| `speed` | — | `bl_cmd.py speed` |
| `list_ui` | `scenesMode`（可选，all/battle/siege/village/lordsHall/naval/navalRaid）、`sceneLimit`（可选，0=全吐） | `bl_cmd.py list-ui [--mode …] [--limit N]` / MCP `bl_list_ui` |
| — | 场景表来源：**扫各模块 `SubModule.xml` 的 `<XmlName id="CustomBattleScenes">` 声明后直接读 XML**（纯文件读 + 缓存 + 切换窗口内降级），不再走引擎的 `GetMergedXmlForManaged`。见 `PROGRESS.md` §二十六 |
| `open_ui` | `uiId`（可选，缺省 = `CustomBattle` 官方自定义战斗界面） | `bl_cmd.py open-ui [--ui-id …]` / MCP `bl_open_ui` |
| `close_ui` | `state`（可选，白名单**只有** `CustomBattleState`） | `bl_cmd.py close-ui [--state …]` / MCP `bl_close_ui` |
| `ghost_camera` | `mode` = `status`/`on`/`off`/`toggle` | MCP `bl_ghost_camera` |
| `camera_speed` | `mode` = `status`/`shift`/`base`/`rts`/`boost`/`probe`（`value` 为倍率；`probe` 另有 `action=end`） | MCP `bl_camera_speed` |
| `skip_video` | — | MCP `bl_skip_video` |
| `cheat_mode` | `mode` = `status`/`on`/`off`/`toggle`（写完回读） | MCP `bl_cheat_mode` |
| `order` | `side`/`formation` + **至少一个动作**：`movement`/`position`/`target`/`targetAgent`（互斥）/`arrangement`/`firing`/`riding`（后三者可与前者并存） | MCP `bl_order` |
| `control_agent` | `mode` = `take`/`release`/`status`；目标 `agentIndex` > `troop` > `formation`，`side` 只接受玩家方 | MCP `bl_control_agent` |
| `get_screen` | `layerFilter`（可选，层名子串） | MCP `bl_get_screen` |
| `get_viewmodel_property` | `propertyName`（点号路径）/ `layerName` / `subProperties`（可选） | MCP `bl_get_viewmodel_property` |
| `get_inventory` | `limit`（可选，默认 50） | MCP `bl_get_inventory`（needs=campaign） |

> ⚠️ **参数名不能叫 `id`**：`src/Jmini.cs` 是**扁平** JSON 读取器（按"文本里第一个 `"key"`"取值），
> 而请求信封自带 `"id"`（16 位 hex 的请求 id）⇒ `Jmini.Str(raw,"id")` 恒读到信封那个值。
> v0.8.12 真机实测踩到：`open_ui` 不带参数时被解析成 `unknown_ui: 没有这个入口 id: 85a0d3d3fba54d4e`。
> **离线自测结构上抓不到**这类缺陷：测试用的假游戏端用真 JSON 解析（认嵌套），天然没有这个碰撞。

> `list_ui` / `open_ui` / `close_ui` 是 **v0.8.12 起的 agent 正门**：走官方
> `Module.CurrentModule.ExecuteInitialStateOptionWithId(id)` 唤起游戏内界面，而不是让 AI 去抢鼠标
> （原型轮实测：合成鼠标输入不可达我们自己的层，根因是 Gauntlet 事件命中顺序，见
> `docs/prototype-ui-probe-2026-09-25.md`）。引擎那个 API 本身**静默失败**（找不到 id 不报错、无返回值），
> 所以我们先 `GetInitialStateOptionWithId` 判存在，把"找不到"变成显式错误 `unknown_ui`。
>
> **v0.8.14 起不再自建界面**：官方自定义战斗本身就是完整入口（战斗 / 围攻 / 村庄 / 海战 / 海上掠夺
> 五种模式 + 玩家类型 + 选择攻守方 + 全套地图参数），复刻属重复建设 ⇒ 面板与主菜单入口注册已删。
> 分工定格：**人用官方界面玩，AI 用端口调**。`list_ui` 同时吐官方场景全表
> （合并表 `CustomBattleScenes`，含模式/地形/目录是否存在）。

> 布尔参数一律发**字符串** `"true"`/`"false"`（C# 侧走 `Jmini.Str`）；`dummyArmor*` 走数字（`Jmini.Num`）。

---

### 两个单元（同一个包，v0.8.12 起）

**一个 mod 包 = 两个单元**，玩家与 AI 各取所需、共用同一条后端：

| 单元 | 位置 | 谁读它 | 入口 |
|---|---|---|---|
| **A：mod 包** | `Modules\BlBridge\`（DLL + `ModuleData\` + `mcp\`） | 游戏与启动器 | **界面**：官方自定义战斗（游戏自带，玩家用）／**控制通道**：文件 IPC（AI） |
| **B：MCP 包** | `Modules\BlBridge\mcp\`（服务器 + `manifest.json` + `README.md`） | AI（读完介绍即可加载） | MCP 工具 **64** 个（不设 `BLBRIDGE_TOOLSET` 时全量暴露；常用的 `core+config+lab` 组合是 **60** 个）（`bl_open_ui` / `bl_start_battle` / `bl_order` / `bl_control_agent` / `bl_ghost_camera` / `bl_camera_speed` / `bl_skip_video` / `bl_cheat_mode` / `bl_get_screen` / `bl_get_viewmodel_property` / `bl_get_inventory` / …） |

关键约束：**界面就是官方那一个，我们只负责"进得去"的那扇门**。v0.8.14 之前我们自建过一套面板，
并宣称"界面与端口不是两套实现"；面板删掉后这句话更彻底地成立 —— 人走官方界面、AI 走端口，
两边最终落到同一条官方开战链。界面上能做而端口做不到的，只剩"用鼠标点选"这个动作本身。
（`custom_battle_scenes.xml` / `naval_custom_battle_scenes.xml` 是**官方**的表，我们只读不写。）

`tools\*.py` 是单元 B 的**唯一真相源**，`build.ps1 -Deploy` 把它整份复制进 `Modules\BlBridge\mcp\`；
`mcp\manifest.json` 的 `version` 与 `SubModule.xml` 一样从 `BridgeConfig.Version` 自动同步。

---

## 二、目录

```
BlBridge/
  module/SubModule.xml            模块清单（<Id>=BlBridge，DLLName=BlBridge.dll）
  module/mcp/README.md            单元 B 的入口文档（给 AI 的安装说明）
  module/mcp/manifest.json        MCPB 0.3 形态清单（version 由构建同步）
  src/UiEntry.cs                  游戏内 UI 入口 + 控制端口侧（list_ui / open_ui / close_ui）
  src/CustomBattleScenes.cs       读官方场景合并表（模式 / 地形 / 目录是否存在）—— list_ui 用它吐全表
  src/SpectatorWatch.cs           上帝视角：实现官方 ICameraModeLogic，返回 SpectatorCameraTypes.Free
  src/SubModule.cs                入口：目录/状态文件；挂遥测行为；每帧泵命令；卸载钩子（cleanExit）
  src/TelemetryBehavior.cs        命中/阵亡/溃逃/快照 → JSONL（惰性打开文件）
  src/ScenarioRunner.cs           AI 对 AI 开战 + 状态机探针 + 10 倍速（照官方 CPUBenchmark 模式）
  src/CommandPump.cs              文件命令泵（主线程执行；过期请求作废 + id 白名单 + 大小上限）
  src/BridgeProtocol.cs           协议信封 / 响应不变式 / 会话身份 + 构建身份
  src/RequestGuard.cs             请求校验闸门（过期/id/大小；纯 System，可离线单测）
  src/TimeControl.cs              战斗加速通道（IsFastForward，每帧重申）
  src/EngineProbe.cs              推进探针（任务时间 / tick / 墙钟 / 暂停）
  src/ProbePolicy.cs              探针判定规则（纯逻辑，可离线单测）
  src/BuildInfo.cs                构建身份（加载时 SHA256 / MVID / 版本）
  src/BridgeConfigFile.cs         游戏端可选配置 blbridge_game.json（加载即校验）
  src/Jmini.cs                    极简扁平 JSON 读取（零第三方依赖）
  src/JsonlWriter.cs / BridgeConfig.cs
  build.ps1                       一键编译 + 部署（查游戏进程 + 备份旧 DLL + SHA256 + 写构建清单）
  blbridge.example.json           MCP 侧配置模板
  blbridge_game.example.json      游戏端配置模板
  tools/bl_mcp.py                 MCP server（stdio，63 个工具；部署时整份复制进 Modules\BlBridge\mcp\）
  tools/bl_analyze.py             分析器（可独立命令行运行）
  tools/bl_dummy_analyze.py       伤害分布分析器（阶段 2① 靶场的读侧；range / battle 双口径；--compare 跨档对比：按部位给 Δ%/Welch t + 生效判据）
  tools/bl_batch.py               跑批编排：按 plan.json 跑 N 场（阶段 2④；plan 支持靶场参数 dummySide / freezeDummies / unlimitedAmmo / dummyArmor）
  tools/bl_compare.py             A/B 对比报告：主指标=满编窗口 + 95%CI + 样本量门槛 + 换边双跑交叉验证（阶段 2④）
  tools/bl_cmd.py                 命令行：status / start / wait / batch / compare / buildcheck / fastforward / speed
  tools/bl_troop_sweep.py         全兵种扫描：probe（可用性判定：引信 + 一次报全，永不建 mission）/ cover（覆盖扫描：跑真战斗比对 unit 事件，找「过了校验却没 spawn」的兵种）
  tools/bl_selftest.py            自测（合成数据 + MCP 协议 + 控制通道 + 假游戏端 + 构建链 + 配置 + 崩溃判定）
  tools/bl_metrics.py             0.7.9 遥测指标分析器（8 个纯函数 seam：盾 HP 曲线 / 破盾箭数 / 挨箭分布 / 移速自洽与倍率 / 装弹时长 / AI 参数分组 / 阵亡挨箭画像）
  tools/bl_metrics_selftest.py    上面那个的离线自测（合成事件手算期望 + 真实日志 smoke，75 项断言）
  tools/bl_death_compare.py       按兵种对照「到死挨几箭」（立项模型校验用；两个口径都报）
  tools/bl_check_clock_reset.py   多轮日志「时钟同源」校验（PROGRESS §十一 判据 6 的可执行版本）
  tools/gabp_names.json           GABP 命名对齐表（L1 唯一真相源；见 docs/gabp-naming.md）
  tools/bl_check_gabp_names.py    命名表 ↔ 源码 method ↔ MCP 工具 三方一致性校验（含 8 类注入故障自测）
  tools/bl_check_dispatch.py      MCP 工具「声明(TOOLS) ↔ 派发(call_tool) ↔ 分组(TOOL_GROUPS)」校验（含 3 类注入故障自测；防「列得出却调不动」）
  tools/l2probe/                  L2 API 漂移探针（用编译器判定上游调用点在 1.4.8 上能不能编过；见 docs/l2-api-drift-1.4.8.md）
  tools/plan.example.json         跑批计划示例（换边双跑）
  tools/plan.armor.example.json   跑批计划示例（靶子护甲对照：dummySide + dummyArmor）
  tools/plan.material.example.json 跑批计划示例（材质对照：dummyBodyItem 换身甲；两件甲护甲数值完全相同，只差材质）
  tools/plan.material_r.example.json 跑批计划示例（改 Warbandlord MaterialResistance 的对照：跑 baseline / 改后两趟，各自产出 manifest 再用 --compare）
  tools/plan.swap_probe.example.json 跑批计划示例（换装探针：同一件甲跑 3 场看运行时 armorBody 是否逐场一致）
  tools/plan.swap_sides.example.json 跑批计划示例（多轮攻守互换：rounds 2 + roundSwap，每轮一个文件）
  tools/plan.mirror.example.json   跑批计划示例（镜像双跑：两个 config 互换攻守，供 bl_compare 分解位置效应）
  tools/plan.mirror2.example.json  跑批计划示例（同上，第二对兵种：cataphract vs fian_champion）
  tools/plan.multitroop.example.json 跑批计划示例（多兵种/战术组：attackerGroups/defenderGroups DSL，含 stop 与 charge 两组）
  tools/runs.example.json         跑批清单示例（供 bl_compare --manifest）
  tools/jsontest/                 离线单测（Jmini/RequestGuard/ProbePolicy/BuildInfo/配置，69 项断言）
  tools/register_mcp.py           把 blbridge 登记进 CodeBuddy 的 mcp.json
  out/BlBridge.dll                编译产物
  out/BlBridge.manifest.json      构建清单（源文件哈希 + DLL 哈希 + 版本；随部署进模块目录）
```

日志/命令目录：`我的文档\Mount and Blade II Bannerlord\BlBridge\`
- `bridge_status.json` 状态 + 会话身份
- `battles\battle_YYYYmmdd_HHMMSS.jsonl` 每场一个文件
- `commands\pending\` / `commands\done\` 控制通道

---

## 三、安装（3 步，只需一次）

```powershell
cd c:\Users\LCGX\CodeBuddy\20260923171333\BlBridge
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Deploy   # 编译并复制进 Modules\BlBridge
python tools\register_mcp.py                                    # 登记 MCP（已做过）
```
然后：启动器勾选 **BlBridge Telemetry** → 启动游戏 → **重载一次 CodeBuddy 窗口**让 MCP 生效。

### ★ 建议加装「骑砍四前置」（**软前置**，不是必需，但能多三座桥）

BlBridge **本身不依赖**四前置 —— 它的 `SubModule.xml` 只声明官方四个
（`Native` / `SandBoxCore` / `Sandbox` / `CustomBattle`），`build.ps1` **也不引用**它们的程序集。
⇒ **不装四前置，BlBridge 的全部核心能力照常工作。**

**但装上它们，会多出三座「诊断桥」** —— 因为这三个组件恰好是
**90% 以上 mod 都会依赖**的基础设施，所以它们自己的状态就是重要的排查线索：

| 建议加装 | 模块 Id | 装了它能多什么 | 不装会怎样 |
|---|---|---|---|
| **Harmony** | `Bannerlord.Harmony` | `bl_patches` / `bl_patch_failures` —— 谁给哪个方法打了补丁、有没有**多 owner 双重叠加**、补丁为什么失败 | 报 `no_harmony`，其余照常 |
| **ButterLib** | `Bannerlord.ButterLib` | 其设置页会被 `bl_mcm_settings` 读到（它是"库也是界面"的典型） | 少一个可读的设置块 |
| **UIExtenderEx** | `Bannerlord.UIExtenderEx` | `bl_ui_extensions` —— 哪个 mod 改了哪个**官方界面**、Prefab 补丁与 ViewModel mixin 各多少 | 报 `no_uiextenderex`，其余照常 |
| **MCM** | `Bannerlord.MBOptionScreen` | `bl_mcm_settings` —— 全场 mod 的**参数面**（本机实测 29 块 / 1225 项） | 报 `no_mcm`，其余照常 |

#### ★ 为什么是「软前置」而不是硬依赖（这是刻意的设计）

| 判据 | 实测 |
|---|---|
| `SubModule.xml` 的 `DependedModules` | **只有官方四个**，不含四前置 |
| `build.ps1` 引用它们的程序集吗 | ❌ **不引用** |
| 那三座桥怎么读它们 | **运行时反射**（`PatchProbe.cs` 59 处 / `UiExtendProbe.cs` 25 处 / `McmProbe.cs` 17 处） |
| 没装时 | **优雅退化**：显式报 `no_harmony` / `no_mcm` / `no_uiextenderex`，**绝不崩、也不静默** |

★ **为什么刻意不做成硬依赖**：若在 `SubModule.xml` 里声明 `DependedModule`，
那"**可选诊断**"就变成了"**没装就不给启动**" —— 与"找不到就优雅退化"**自相矛盾**，
也让只想用 BlBridge 核心能力的人被迫多装四个前置。
⇒ 实测证明：**什么都没加，照样跑通**（三座桥在四前置缺席时如实报错码）。

> ⚠️ **加载顺序**：四前置在链条最前端且次序固定
> （`Harmony → ButterLib → UIExtenderEx → MBOptionScreen → Native → …`），
> 由引擎按 `DependedModules` 强制 —— **重排不解决问题**。
> 另见知识库 `butr-mod-stack`（若你有那份共享知识库）。

---

## 四、用法 A：手动打一场（验证遥测）

1. 自定义战斗打一场 → 退出
2. `python tools\bl_analyze.py`（或让我用 `bl_analyze`）
3. 看第一张表：**「致死总伤害均值」应 ≈「最大血量中位」**（±3% 内）——这是对血量模型的直接检验

## 五、用法 B：AI 推演（我来驱动）

1. **游戏里点 Custom Battle，停在选兵界面**（引擎要求当前状态是 `CustomBattleState`，官方 benchmark 同样要求）
2. 我调用 `bl_start_battle(attackerTroop, attackerCount, defenderTroop, defenderCount)`
3. 我调用 `bl_wait_for_state(state="ended")` 等它打完（10 倍速，20v20 约 20~40 秒）
4. 我调用 `bl_battle_status` 读战果、`bl_analyze` 读逐击数据

一次会话可反复跑：战斗结束后引擎会回到自定义战斗界面，直接发下一条 `start_battle` 即可。

---

## 六、MCP 工具（64 个）

| 工具 | 作用 | 需游戏在跑 |
|---|---|---|
| `bl_status` | 模块/日志状态 | 否 |
| `bl_list_battles` | 列出战斗日志 | 否 |
| `bl_analyze` | 分析某场（血量校验/伤害分布/挨打成本） | 否 |
| `bl_read_events` | 读原始事件（按类型过滤、分页） | 否 |
| `bl_read_config` | 回读 Warbandlord 配置 | 否 |
| `bl_apply_config` | 改 Warbandlord 配置（自动备份 + XML 校验） | 否（改完需重启游戏） |
| `bl_rts_config` | 回读 **RTSCamera** 配置（攻城相机高度 / 自由相机 / 抬升触发） | 否 |
| `bl_apply_rts_config` | 写 RTSCamera 配置（自动备份 + 校验 + 回读核对）；支持预设 `siege-god`/`free-always`/`elevated-always`/`god-full`。**实测改完下一场就生效**（它每场开始读一次配置；但它退出时会用内存值覆写文件 ⇒ 要"永久"得在游戏内 MCM 改） | 否 |
| `bl_ghost_camera` | **幽灵/自由相机**（`mode` = status/on/off/toggle）：设引擎自带的 `MissionScreen.IsCheatGhostMode` —— 相机走引擎的 Free 观察镜头，且**命令 UI 开着也保持自由**（能观战 + 能下令）。**运行时即可切、对任何一场战斗都有效**（含你自己打的）；`cheat_mode`（只读项）未开时不能手动飞，但观察者镜头与切换都可用 | 是 |
| `bl_camera_speed` | **相机移动速度**（v0.8.17；`mode` = status/shift/base/rts/boost）。三条腿：`shift`=引擎自由相机的 Shift 倍率（走官方控制台函数 `mission.set_shift_camera_speed`，**不需要作弊模式**，按住 Shift 生效）；`base`=引擎基础倍率（反射，⚠️ 引擎把速度分量 clamp 在 ±20）；`rts`=**RTSCamera** 的 `MovementSpeedFactor`（反射，最有效，上限随速度一起放大）；`boost`=三条可用腿一起设。每条腿**写完回读**，失败点名是哪条腿 | 是 |
| `bl_skip_video` | **跳过开场动画**（v0.8.20）：判当前活动状态是不是 `VideoPlaybackState`，是就直接调 `OnVideoFinished()` —— **不模拟 ESC**（判据硬、无副作用）。做法学自 BUTR/Bannerlord.GABS 的 `core/skip_video` | 是 |
| `bl_cheat_mode` | **开关作弊模式**（v0.8.20，带回读）：写 `NativeConfig.CheatMode`（私有 setter → 后备字段两条路）。开了它，**引擎自由相机的 `Ctrl+↑/↓`/`Ctrl+中键` 倍率热键与观察者 HUD 的「摄像机移动速度」读数才可用**（这是「相机太慢」的另一条正解）。⚠️ 等于打开开发者通道（F2/F3/F4 杀敌杀友等一并生效），工具**不做任何自动开启**；只作用于本次进程 | 是 |
| `bl_order` | **战斗中途改令**（v0.8.24；**v0.8.29 扩到阵列/射击纪律，v0.8.30 扩到指定点移动，v0.8.31 扩到指定目标，v0.8.32 扩到上下马与指定单位**）：对**进行中**的战斗里某方编队改 `movement` / `position` / `target` / `targetAgent` / `arrangement` / `firing` / `riding`（**至少给一个**；`side` = player/attacker/defender；`formation` = Infantry/Ranged/Cavalry/HorseArcher/Skirmisher 或 0~4，不传 = 该方所有有兵的编队；`movement` = charge/advance/fallback/stop/retreat；`position` = `"x,y"` 或 `"x,y,z"`（米，z 省略 = 0，引擎按地面/导航网格补 Z）；`target` = **敌方编队**名或下标 ⇒ `MovementOrderChargeToTarget`，敌方 = 与 `side` 相对的那一方；**`movement` / `position` / `target` / `targetAgent` 四者互斥**；`targetAgent` = **敌方单位**的 `Agent.Index`（整数，从遥测或 `bl_control_agent status` 取）⇒ `MovementOrderAttackEntity`；`riding` = free/mount/dismount ⇒ `Formation.SetRidingOrder`（与 movement order **正交**：管骑不骑、不管去哪，**不参与**那条互斥））；`arrangement` = line/shieldwall/circle/square/skein/column/loose/scatter；`firing` = fireAtWill/holdFire —— 引擎只有这两档）。与开战 DSL 走**同一条**下发路径，每条都回传 `orderBefore`/`orderAfter`（引擎 `MovementOrder.OrderEnum`）——**判据是当场回读**，不是"我们调了 API"。`position` 另回传 `moveTarget`（引擎按导航网格算出的落点，可能被夹到合法位置）+ `formationCenter`；`target` 另回传 `targetAfter`（回读到的目标编队）+ `targetDistance`（双方编队重心距离）；`targetAgent` 另回传 `targetAgentIndex`/`targetAgentTroop`/`targetEntitySet`（回读 `MovementOrder.TargetEntity`）/`targetAgentAlive` + `targetDistance`（编队重心↔该单位的距离）；`riding` 另回传 `ridingBefore`/`ridingAfter`（引擎 `RidingOrder` 三档）—— 这些是**行为**判据：隔几秒再调一次，重心应朝目标点挪 / 距离应缩小。⚠️ `emptyFormations` > 0（applied 里对应条目带 `emptyFormation:true`、`count:0`、`formationCenter:(invalid)`）= **令写进去了但那个编队一个人都没有**（编队按兵种自动分：弓手在 Ranged、近战步兵在 Infantry）⇒ 没人执行，先核对 `formation`。⚠️ 只在 mission 内有意义（mission 之外碰 `MovementOrder` 会抛 `TypeInitializationException` 并把该类型**永久**标记为不可用 ⇒ 没有战斗时直接拒 `no_mission`）。**关键**：会同步改掉组路径"待重申的值"（回传 `pendingSpecsUpdated`），否则开战 DSL 那 **0.5 秒一次的周期重申**会把你的令改回去（真机踩过：12 秒后复读 `orderBefore` 又变回 `Charge`）；`position` / `target` 没有"名字"可重申，所以走**手动令优先**标记（重申时照原样重申同一个点/目标，用 `movement` 覆盖时该标记会被清掉；目标编队被打空后重申会**跳过**并记一条 order error，不偷偷退回冲锋）。`detachAI` 默认 true（连带 `SetControlledByAI(false,false)`）。`targetAgent` 的目标**会死**：阵亡后重申**跳过**并记一条 order error，**不**退回 `s.Movement`（不静默改成冲锋）。目前**没有**未实现的参数（`UnsupportedParams` 已清空） | 是 |
| `bl_control_agent` | **接管某个友方士兵**（v0.8.26，**最小版**）：`mode` = take/release/status；目标用 `agentIndex` > `troop` > `formation`（不传 = 该方第一个存活者，**默认跳过当前 MainAgent**）。五步：老主角色交回 AI（否则同编队两个 `Player` 控制器会让编队逻辑栈溢出）→ `Mission.MainAgent = 目标` → 目标 `Controller = Player` + 清 `AIStateFlags` + 摘 `VictoryComponent` → 复位 `MissionScreen._isPlayerAgentAdded`（反射；**v0.8.32 起**：装了 RTSCamera 时改走它自己的**平滑推镜** —— 反射 `Utility.BeforeSetMainAgent` → 赋值 → `AfterSetMainAgent`，结果见返回的 `cameraFollow`：`applied`/`shouldSmooth`/`lastFollowed`（回读判据）/`why`；没装则退回这条老路，行为与旧版一致）→ **当场回读**。⚠️ **真机实测边界**：AI 对 AI（无真人）场次里主角色能换，但 `Controller` 回读仍是 `AI` ⇒ 工具如实报 `ok=false` / `controller_not_verified`，**不假装成功**；要真接管需**真人场次**。只允许玩家方（敌方 `not_player_team`）。未实现：`agentId`/`slot`/`mount`/`weapon`（传了报 `unsupported_param`） | 是 |
| `bl_get_screen` | **只读读当前界面**：层 / 影片 / 可点按钮（文本 + 是否可用 + 状态 + id）；地图装饰层默认跳过，`layerFilter` 只看某一层。读界面，不模拟鼠标（合成输入到不了官方界面）。脱壳抄自 BUTR/Bannerlord.GABS 的 `ui/get_screen`。⚠️ 按钮**优先用 text 定位**：id 常空且不唯一（真机：主菜单 11 个按钮只有 1 个有 id；自定义战斗界面 8 个都叫 `AddTroopButton`）。前置：游戏在跑 **且** 已部署含该 method 的 DLL（v0.8.34+）。真机判据见 `docs/mcp-tool-desc-audit-2026-09-27.md` §7.1 | 是 |
| `bl_get_viewmodel_property` | **只读读 ViewModel 属性**：`propertyName`（点号路径穿透嵌套，如 `PlayerGold` / `Smelting.SmeltableItemList`）+ `layerName`（从 `bl_get_screen` 的 `layers[].name` 取）；列表返回 `count` + `items` + `missingSubProperties`，`subProperties` 抽每项子字段。脱壳抄自上游 `ui/get_viewmodel_property`。⚠️ 属性不存在 = `ok:false` / `property_not_found` **并列出真实可用属性名**（v0.8.35 起；`value:null` 只表示"属性存在、值就是空"）；层名写错 = `no_data_source`。前置同 `bl_get_screen`。真机判据见同文档 §7.3/§7.4 | 是 |
| `bl_get_inventory` | **只读读战役库存**（v0.8.36，L2 #2）：主队伍 `ItemRoster` + 主英雄金币。返回 `gold` / `itemCount` / `totalElements` / `items[]`（name、id、quantity、type、value、weight、tier），`limit` 默认 50。脱壳抄自上游 `inventory/get_inventory`。⚠️ 前置 = **战役内**：主菜单 / 自定义战斗如实报 `no_campaign`（needs=campaign，命名表 C6 已登记） | 是 |
| `bl_list_saves` / `bl_load_save` | **存档列表 / 按名直载**（v0.8.36）：`MBSaveLoad.GetSaveFiles`（meta 含 `Module_*` 模组启停键值）+ `LoadSaveGameData + StartNewGame` 直载——**不经过存档选择界面**，无人值守换档的正门，也是复现读档期弹窗的测试入口。⚠️ `load_save` 前置 = 主菜单（战役中拒 `in_campaign`）；名字不存在 = `save_not_found` 并列出可用档；读档期"模组不匹配"确认框（引擎自绘，回车=『是』）会被自动应答。脱壳抄自上游 `core/list_saves` / `core/load_save` | 是 |
| `bl_campaign_time` | **只读：战役时间/暂停诊断**（v0.8.36 起，v0.8.39 收成只读）：`mode` 只接受 `status`，回读 `timeControlMode` / `inMenuContext` / `campaignDays` / `pauseMenuOpen`。`pauseMenuOpen` = 地图上的暂停菜单（ESC 菜单）是否开着——原版失焦 + `BannerlordConfig.StopGameOnFocusLost=true` 会自动打开它，而它会 `RegisterActiveStateDisableRequest` ⇒ MapState 不再 Tick ⇒ 战役冻结（档位却仍是 StoppablePlay）。`campaignDays` = `CampaignTime.Now.ToDays`，失焦前后各读一次才证明"时间真的在走"。⚠️ **时间保活（原 mode=on/off）已在 v0.8.39 移除**（用户明确不需要改游戏的时间暂停；且它看不见上面那种暂停、副作用会顶掉手动暂停）⇒ 传 on/off 会显式报 `keep_awake_removed`。真机 A/B 见 `PROGRESS.md` §三十二 | 是 |
| `bl_get_hero` | **只读：英雄运行时血量与状态**（v0.8.51，B7）：`hitPoints` / `maxHitPoints` / **`overflow`** / `isOverflow` / `isWounded` / `isDead` / `isAlive` / `clan` / `party`。不传参 ⇒ 列**玩家队伍**英雄（主角+同伴）；`heroId`（StringId 精确）/ `name`（包含）/ `all=true`（全战役）。★ 补一个**真实缺口**：C6-④「溢出修复」把"当前血量 > 最大血量"夹回上限，**其验收判据就是比较这两个值**，而此前无工具可读（`get_entity` 读的是**静态索引库**非运行时、`bl_list_parties` 无 hp、RBM 的 `Debug.Print` 不落盘）。**`overflow = hitPoints - maxHitPoints`**（>0 即要夹回的量）。⚠️ `hpReadable=false` 表示字段没读到 —— 此时 `hp/max` 的 0 **不是真值**，别据此判"没溢出"；⚠️ **不提供 `woundedLimit`**（`Hero.WoundedLimit` 在 1.4.8 **实测不存在**，宁缺勿造）。血量仅战役有意义（战斗里是 `Agent.Health`）| 是 |
| `bl_battle_status` | 推演状态机 + 双方存活数 + 战果 | 是 |
| `bl_start_battle` | 开一场 AI 对 AI 战斗（支持靶场参数：`dummySide` / `dummyArmor` / `dummyBodyItem` / `freezeDummies` / `unlimitedAmmo`；`spectate` = 兜底观战镜头（**本机装了 RTSCamera 时自动让位**，v0.8.16 起写明这条）；**`rtsPreset` = 开战前套用 RTSCamera 预设**；返回 `formationWarnings` = 开战 DSL 的 `formation` 只是**回显**，与兵种实际编队不一致时逐条点名，**不阻断开战**）；**v0.8.41**：`attackerTacticLevel`/`defenderTacticLevel`（战术档位，需配 `orders=default` 才看得出效果）、`terrain`/`randomTerrainSeed`、`aiFriendlyFireMultiplier`、`keepCorpses`、`sceneLevel`/`timeOfDay`（攻城）；返回另带 `tactics`（请求值 + 引擎原生值）、`env`、`envNotes`（被显式忽略的参数**逐条报出**） | 是 |
| `bl_wait_for_state` | 等状态（idle/loading/running/ended/error） | 是 |
| `bl_abort` | 中止当前推演 | 是 |
| `bl_list_ui` | **列出游戏内可进入的入口** + **官方自定义战斗场景全表**（模式 / 地形 / 是否存在；可按 `scenesMode` 过滤） | 是 |
| `bl_open_ui` | **唤起游戏内界面**（默认 = 官方自定义战斗；`uiId` 可换任意官方入口）—— agent 正门，不用抢鼠标 | 是 |
| `bl_close_ui` | 从官方自定义战斗界面返回主菜单（官方 `PopState` 同路径；白名单只有 `CustomBattleState`） | 是 |
| `bl_fast_forward` | 开关战斗加速（10 倍速，**对自己手打的战斗也生效**） | 是 |
| `bl_build_check` | 核对 源码/构建产物/部署文件/进程内 DLL 是否一致（从部署副本跑时如实标注"源码段跳过"） | 是 |
| `bl_config` | 显示有效配置与来历（env / 文件 / 默认），并列出配置文件里的非法项 | 是 |
| `bl_run_batch` | **按计划跑 N 场**（阶段 2④「一条命令跑 N 场」；支持换边双跑；`dryRun` 只回计划不碰游戏） | 是 |
| `bl_batch_report` | **A/B 对比报告**（主指标 = 满编窗口；强制 95%CI；样本 < 3 局时拒绝下结论） | 否 |
| `bl_lookup_troop` | 查兵种 id 是否存在（走 BannerlordSage 索引；第三方模组兵种不在索引里属正常） | 否 |
| `bl_launch_game` | 无人值守启动游戏（BLSE + 自动应答模态弹窗；宿主会回收本会话进程树，见原型结论文档）。`excludeModules` = 启动时**排除**某些模块（A/B 对照用：同一次启动只差一个模块；拼错即启动失败，不静默） | 否 |
| `bl_desktop_windows` | 列窗口（结构化 JSON，**物理**坐标，已 DPI-aware） | 否 |
| `bl_desktop_screenshot` | 截图（可叠带标签网格，按格定位） | 否 |
| `bl_desktop_click` | 按格点击（`gridhand` 后端） | 否 |
| `bl_desktop_key` | 发送按键 | 否 |
| `bl_lexicon` | **崩溃词典**（宿主侧，只读）：按异常类型或一段崩溃文本匹配**人话描述 / 常见场景 / 修复建议**，补 `bl_crash --deep` 与 `bl_exceptions` 只有符号栈、说不出"该怎么办"的那一层。⚠️ **词条数据不随本仓库分发**（上游无许可）⇒ 用 `BLBRIDGE_LEXICON_DIR` 指向本地目录；**缺数据时如实报 `installed=false`**，不返回空结果冒充"没有匹配"。匹配是**短语子串**（`MatchAny` 任一 / `MatchAll` 全部 / `ExcludeAny` 排除），**不是语义匹配** ⇒ 匹配不到不等于没问题；`priority` 只排序、不是置信度；`zh` 缺失时回退英文并如实标注。详见 [`tools/data/README.md`](tools/data/README.md) | 否 |
| `bl_crashguard` | **崩溃守卫账本 + 修复建议**（宿主侧，只读）：读 `crashguard.jsonl`，答「**哪些异常本来会杀掉游戏、被我们吞掉了**」与「**哪些我们不敢吞**」。与 `bl_exceptions` 的分工：那个答"发生过哪些异常"，本工具只答"守卫放过了什么"——FirstChance **只能观察不能阻止**，Harmony Finalizer 才是唯一能阻止传播的钩子。⚠️ **`action=swallow` 不等于已修复**：吞掉只保证游戏没死，被吞的方法**没做完它该做的事**，可能留下**不报错**的静默损坏。⚠️ `reason=breaker_open`/`quota_exhausted` 是**坏消息**（守卫已停止保护）会被判 critical 并置顶；`fatal_passthrough` 说明游戏**很可能仍崩了** ⇒ 用 `bl_crash` 看 dump。守卫**默认关闭** ⇒ 文件不存在**不等于**没崩溃，以 `enabled` 为准。详见 [`docs/crash-guard.md`](docs/crash-guard.md) | 否 |
| `bl_save_diag` | **存档诊断**（宿主侧，**只读**）：比对「存档记录的模组集」与「启动器当前启用的模组集」，找出会导致读档崩溃的不一致 —— ① 存档需要但当前**未启用**的模组；② **版本漂移**（框架级高风险）；③ `isCorrupted` 标记；④ 当前新加的模组（多数无害，**折叠展示**，不淹没前三条）。⚠️ **本工具只读**：不写、不改、不备份存档（存档修复是最难验证的一环，交回给人）。⚠️ **缺模组 ≠ 一定崩**（只说风险）；**版本不同 ≠ 不安全**（按 framework/content 分级）；游戏本体模块已降级，否则人人报错。存档清单取自**最近一次** `bl_list_saves` 响应 | 否 |
| `bl_report` | **崩溃报告导出**（宿主侧，只读）：把 `crashguard.jsonl` + `exceptions.jsonl` + `bridge_status.json` 合成一份**自包含**报告（**单文件 HTML**，内联 CSS、**无外部依赖、不联网**）或 Markdown，可选联动词典给修复建议。HTML/CSS **全部自写**（报告模板是有版权的表达，只借鉴栏目思路，不引第三方模板）。日志内容一律 HTML 转义（不可信输入）；**不含**存档内容/账号/凭据；**不修改**源文件；默认**不覆盖**已有文件（同秒自动加序号） | 否 |

### 战斗加速通道（v0.3.0 新增）

**原版这条通道只有 UI 入口，没有可编程触发**：`Mission.SetFastForwardingFromUI`（`Mission.cs:6775`）的
生产调用方是计分板 VM 的动作 `ExecuteFastForwardAction`（基础层 `TaleWorlds.MountAndBlade.ViewModelCollection.dll`，
同层还有 `CustomBattleScoreboardVM`），对应战斗计分板上的 `FastForwardButton`
（`SPScoreboard.xml:318`，`IsDisabled="@IsOver"`，`IsSelected="@IsFastForwarding"`；`:286/:322` 还有按键提示图标
`{FastForwardKey}`），默认键位是 `ScoreboardHotKeyCategory.ToggleFastForward`（**F**，见 `BannerlordGameKeys.xml`）。
也就是说：**原版加速需要"可见计分板 + 玩家按键/点击"**；桥要的是无 UI、无玩家的程序化触发，因此自建 ——
底层通道与 mod 共用 `Mission.IsFastForward`。

> **证据边界**（避免再次过读）：以上来自程序集符号检索 + 界面 XML + 键位文件，均为实测；
> 另有第三方 mod（RTSCamera、Bloodlust）也引用同一符号，走同一条底层通道。
>
> **链路已验证（2026-09-23 深夜，反编译真实 `TaleWorlds.MountAndBlade.ViewModelCollection.dll`）**：
> `ScoreboardHotKeyCategory` 默认把 `ToggleFastForward` 绑在 **F**（`ScoreboardHotKeyCategory.cs:32-35`，
> `new Key(InputKey.F)`）；消费方是 `CustomBattleScoreboardVM.ExecuteFastForwardAction()`
> （`ViewModelCollection.decompiled.cs:2158`），它调 `Mission.Current.SetFastForwardingFromUI(...)`
> **但前置条件是 `IsMainCharacterDead`** —— 即原版 F 键加速**只在主角阵亡后的记分板里可用**
> （"你死了以后快进看结局"），活着时按 F 无效。这解释了为什么活着的玩家只能靠 RTSCamera
> 这类 mod 或我们的命令通道。
>
> **修正记录（2026-09-23，外部审计 P1-1/P1-2）**：本段旧文案写的是"原版引擎没有任何战斗加速键 /
> `SetFastForwardingFromUI` 零调用"—— **那是错的**。错因：反编译源码树只覆盖 10 个核心程序集 + 6 个模块，
> **`*ViewModelCollection.dll` 一个都不在树里**，搜索对它静默返回 0 命中，而我把"空结果"读成了"零调用"。
> 现在改用**程序集级符号检索**复核（工具：`tools/symbol_search.py`）：该符号在 **13 个程序集**里命中。

BlBridge 自己实现了这条通道，**不依赖任何第三方 mod**：

```
python bl_cmd.py fastforward --on    # 开（10 倍速）
python bl_cmd.py fastforward --off   # 关
python bl_cmd.py speed               # 诊断：isFastForward / sceneTimeSpeed / missionMode
```

实现要点：`TelemetryBehavior.OnMissionTick` 每帧调用 `TimeControl.Apply()` 重申 `IsFastForward`；
引擎的 `MissionState` 因此每帧多跑 9 次 0.1s tick（`MissionFastForwardSpeedMultiplier = 10`）。

#### 与 RTSCamera 的关系：同一个开关（重要）

RTSCamera 的快进键、原版计分板按钮（若已接线）、我们的命令，**最终都落在同一个布尔量**
`Mission.IsFastForward` 上。所以：

- **不需要"调用 RTSCamera"** —— 我们设的就是它设的那个值，而且我们每帧重申，不会被别的逻辑关掉；
- 反过来，如果你**按了 RTSCamera 的快进键**而我们的强制开着，RTSCamera 会把该值翻转成 false，
  我们下一帧又重申 true → 你会看到"按了没反应"。**要关就用 `fastforward --off`，别去按它的键**；
- `bl_cmd.py fastforward`（不带参数）现在只**查看状态**，不会误关。

⚠️ **不要走 `Mission.Scene.TimeSpeed`**：`Mission.UpdateSceneTimeSpeed()` 每帧执行，
取 `min(1, 所有请求值)`（起始 1f、只接受更小值），写 10 会被下一帧打回 1 ——
这也是 `AddTimeSpeedRequest` 只能做慢动作的原因。

### 请求闸门（v0.4.0 新增）

对照 Coop 的字段级校验补上的三件事，都打在"文件 IPC 特有"的洞上：

| 问题 | 后果 | 修法 |
|---|---|---|
| **过期请求**：MCP 超时退出后请求文件还留在 `pending/` | 游戏**后**启动会把它执行掉 → 凭空冒出一场没要的战斗（幽灵战斗） | 游戏端读 `issuedUtc`，超 `BridgeConfig.MaxRequestAgeSeconds`（120s）回 `request_expired` 并丢弃；MCP 侧超时立即删除 pending |
| **id 直拼文件名** | `id="..\..\evil"` 路径穿越 | `RequestGuard.IsValidId`（16~32 位十六进制）+ `SafeFileName` 双保险 |
| **无大小上限** | 巨型请求文件拖死主线程 | 1 MiB 上限，超限回 `request_too_large` 且不读进内存 |

另外两项顺手修掉的数据污染源：

- **遥测文件名精确到毫秒 + `FileMode.CreateNew`**：旧版（秒级 + `Create`）在同一秒内重开战斗会**静默覆盖**上一场数据；
- **浮点改 round-trip（`"R"`）**：旧版 `"0.###"` 只留 3 位小数，A/B 累加对比会丢尾数；**NaN/Infinity 输出 `null` 并计数**（写进 `end` 事件的 `nanCount`），旧版静默变 0 会把引擎异常值伪装成正常数据。

超时归因也拆开了（不再是一句"等待超时"）：

```
bridge_not_loaded  没有 bridge_status.json —— 游戏没启动 / 模块未启用
process_exited     进程已退出（等待循环里每秒探测，立刻返回，不傻等 deadline）
probe_unknown      探测失败 = 未知，不做判断
no_response        进程在跑但不响应 —— 主线程卡住 / Enabled=false / 未重启的旧模块
```

### 离线单测（v0.4.0 新增，不需要启动游戏）

`Jmini`（JSON 读取器）与 `RequestGuard`（请求闸门）只依赖 System，所以能单独编译执行：

```
cd BlBridge\tools\jsontest
powershell -ExecutionPolicy Bypass -File .\build_and_run.ps1
```

44 项断言，覆盖：键查找不被字符串值劫持、嵌套键仍可读、`\uXXXX` 转义与代理对、id 白名单、文件名安全化、过期判定（含时钟偏差与解析失败不误杀）、浮点 round-trip 与 NaN 计数。

### 部署安全性（v0.4.0 新增）

`build.ps1 -Deploy` 现在会：**先确认游戏没在运行**（有进程就拒绝并列 PID，拷贝途中再确认一次）→ 备份旧 DLL（带时间戳）→ 拷贝 → **SHA256 比对校验**。
旧版直接 `Copy-Item -Force`，游戏运行时会失败或半途而废，而"看起来成功"的部署正是"跑了整晚才发现用的是旧 DLL"的根因。

> `build.ps1` 保持**全 ASCII**（PS 5.1 在无 UTF-8 BOM 时按 ANSI/GBK 读取 .ps1，中文会破坏语法 —— 本次就踩到一次）。

### 推进探针：区分"真的在打"和"卡在黑屏/加载界面"（v0.5.0 新增）

只看 `state == running` **无法**判断战斗是否真的在推进 —— 卡在加载界面时状态照样是 running，这种样本混进 A/B 对比会让结论出错，而且**看起来一切正常**。
（对照 Coop 的 `render-status`：engineFrame 推进 + rendererFps > 0 + topScreen/activeState 两次不变 = "活着且稳定"。）

我们读三个真实信号（不是自报）：

| 信号 | 来源 | 含义 |
|---|---|---|
| `missionTime` | `Mission.CurrentTime`（`Mission.cs:1185`，内部 `_cachedMissionTime`） | 暂停/卡住/加载中时**停止推进**——最硬的判据 |
| `ticks` | 我们自己的 mission tick 计数 | 主线程真的在转 |
| `paused` / 墙钟 | `MBCommon.IsPaused`（`MBCommon.cs:43`）+ `DateTime.UtcNow` | 区分"被暂停"与"卡死" |

判定规则抽在 `ProbePolicy`（纯逻辑，可离线单测）：

```
实时：  任务时间 2 秒没动 → stalled（否则 advancing）
样本：  帧数 < 30 或 帧率 < 10/s 或 单次卡顿 > 5s → suspect，否则 ok
```

产出两处：`status` 里的 `readiness` 块（实时），以及每场 `end` 事件里的 `validity` 块（该场能不能进 A/B）。
`bl_wait_for_state` 也用它：状态是 running 但引擎已停住 15 秒 → 直接早退并标注"该样本无效"，不再傻等到 180 秒超时。

### 构建身份：自动识别"进程里跑的是旧 DLL"（v0.5.0 新增）

v0.3.0 那次我是**人工**对比 `bl_status` 里的 version 才发现"进程里是 0.1.0、磁盘上是 0.3.0"，然后才让你重启游戏。
现在它变成自动判定：`build.ps1` 生成 `build_manifest.json`（每个源文件哈希 + DLL 哈希 + 版本），随部署一起进模块目录；
游戏端在**模块加载那一刻**记录自己这个文件的 SHA256、MVID、版本（`BuildInfo`），并写进 `bridge_status.json` 与每个响应的 `process` 块。

`bl_build_check` 把四段串起来核验：

```
ok                       源码 = 构建产物 = 部署文件 = 进程内 DLL
stale_source             源码改了但没重新构建
stale_deploy             构建了但没部署（或部署的是别的产物）
game_not_restarted       进程里是更早的 DLL（磁盘已更新）→ 必须完全重启游戏
game_running_other_build 进程内 DLL 与磁盘上的不同 → 部署路径存疑
game_offline             游戏没在运行（文件链条可能仍然一致）
manifest_missing         旧版部署，没有清单
```

`bl_start_battle` 现在**开打前先做这道 preflight**：`stale_deploy` / `game_not_restarted` 等直接拒绝（照 Coop 的"incompatible build fails before an expensive launch"），
`stale_source` 只警告并回带在结果里。CLI 也能单独跑：`python bl_cmd.py buildcheck`。

### 崩溃判定：区分"正常退出"和"崩溃/被强杀"（v0.6.0 新增）

`bl_status` 现在会给出 `sessionDiagnosis`。依据三个信号，缺哪个就诚实说"不知道"：

| 信号 | 来源 | 作用 |
|---|---|---|
| `cleanExit` | 只在引擎的 `OnSubModuleUnloaded()`（`MBSubModuleBase.cs:12`）里写 true | 进程消失后它还是 false ⇒ 它没能走到卸载钩子 |
| `missionInProgress` / `state == battle` | 战斗首帧写入（`SubModule.NotifyBattleStarted`） | 判断崩溃是否发生在战斗中，并指出战斗文件 |
| 进程存活探测 | `tasklist`（探测失败 = 未知，不下结论） | 区分"还活着"与"已经没了" |

结论取值：`running` / `clean_exit` / `crashed` / `crashed_in_battle`（附现场战斗文件）/ `no_session` / `undetermined`（旧版状态文件没有 `cleanExit` 标记时**不瞎猜**）/ `unknown`。

`bl_battle_status` 拿不到状态时也会附上这个判定 —— 否则失败信息只剩一句"超时"。

### 配置化：可选外部配置 + 加载即校验（v0.6.0 新增）

改采样间隔从"改代码 → 重新编译 → 重新部署 DLL → 重启游戏"变成"改一个 JSON → 重启游戏"。

| 侧 | 文件 | 可配 | 生效时机 |
|---|---|---|---|
| 游戏端 | `<LogDir>\blbridge_game.json` | `enabled` / `sampleIntervalSeconds`(1~600) / `flushEveryLine` / `maxRequestAgeSeconds`(5~3600) / **`maxActionLogBytes`(0=不轮转，默认；或 1024~1GiB)** / **`actionLogKeepFiles`(1~100，默认 5)** | 模块加载时读一次，**改完要重启游戏** |
| MCP 端 | `BlBridge\blbridge.json` | `logDir` / `gameDir` / `waitForStateTimeoutSec` / `statusTimeoutSec` / `maxRequestAgeSec` | 读一次即缓存，改完不影响运行中 |

模板：`blbridge.example.json`、`blbridge_game.example.json`（每个键都写了范围与来历）。

两条设计原则（照 Coop 的"配置即校验 + 来历可查"）：

1. **加载即校验，坏值不拖累好值**：越界/类型错的项**逐项忽略并记录原因**（写进 `bridge_status.json` 的 `config.errors`），而不是让整份配置失效；
2. **来历可查**：`bl_config` 会说明每个值来自 环境变量 / 配置文件 / 内置默认值，避免"我明明改了但没生效"这种无从定位的状态。
   优先级：**环境变量 > 配置文件 > 默认值**。

---

## 六点五、动作账本 `commands/actions.jsonl`（v0.8.42）

控制通道的**审计流水**：游戏每处理一个请求就追加一行，**永不重写、永不删除**（除非显式开了轮转）。

在此之前，`CommandPump.HandleOne` 把 id / method / 错误码 / 异常全算出来了，然后只写一个
**按 id 命名的响应文件** —— 外部消费完就没了，同 id 再来一次还会被覆盖
⇒ **"我到底下过什么命令"在磁盘上不留痕**。崩溃复盘时只能靠 battle 日志反推。

一行一个 JSON 对象（单行自闭合，字段集由自测 ⑮ 与 `src/ActionLedger.cs` 双向对账）：

| 字段 | 含义 |
|---|---|
| `t` | UTC ISO 时间戳 |
| `seq` | 进程内单调序号（判"有没有丢行/两个写者交错"，比时间戳可靠） |
| `runToken` | 游戏**进程会话**标识 ⇒ 账本能按会话分组（重启游戏即换 token） |
| `id` / `method` | 请求 id / 方法名（拒绝路径上 method 可能为空） |
| `ok` / `resultOk` | **两个不同的 ok** —— 见下面「两个 ok」；`ok` = 信封，`resultOk` = **操作** |
| `code` | 失败码（**信封**成功时为空） |
| `ms` / `bytes` | 处理耗时（毫秒）/ 请求文件字节数（读不到为 -1） |
| `uncertain` | 协议里的 `outcomeUncertain` —— 为 true 时**绝不盲目重试** |
| `note` | 成功时 `state=<状态>`；失败时错误消息（截断） |
| `args` | 请求参数片段（截断 600 字符；账本是**索引**不是副本） |

### ⚠️ 两个 `ok`（v0.8.46 起，务必分清）

响应里有**两层**成败，**它们不是一回事**：

| 字段 | 回答的问题 | 取值 |
|---|---|---|
| `ok` | "**请求有没有被游戏端处理**"（信封） | `true` / `false` |
| `resultOk` | "**这个操作成功了吗**"（`result` 内部） | `true` / `false` / **`null` = 未知** |

**为什么需要两个**：有 **10 个方法**返回的是**裸 body 字符串**、被成功信封包起来
（`skip_video` / `order` / `control_agent` / `ghost_camera` / `camera_speed` / `cheat_mode` …）。
典型例子：主菜单下跳开场动画 ⇒ 信封 `ok=true`（请求确实被处理了）
但操作失败（`code=not_video`）。**实测这类占全部响应的 2.00%**。
v0.8.46 之前，这类在账本里**完全看不出来**。

**`resultOk` 的 `null` 表示"未知"**，两种来源：
① **历史行没有这个字段**（v0.8.46 之前写的，确实没记录过这件事）；
② 信封失败、或 `result` 里没有 `ok` 键。

⇒ **筛"操作失败"要写 `resultOk is False`**，**不要**写 `not resultOk`
（后者会把"未知"也算成失败 —— 自测里有一条断言专门钉住这条）。

读它：`python tools\bl_cmd.py actions [--limit 30] [--fail-only] [--op-fail-only] [--json] [--path …]`

- `--fail-only`：只看**信封**失败（`ok=false`，请求没被处理）。**语义与 v0.8.42 一致，未变。**
- `--op-fail-only`：只看**操作**失败（`resultOk` 明确为 `false`）；未知的**不计入**。
- 表格第二列 `opOk` 显示三态：`OK` / `FAIL` / **`?`**（未知）。

- **每个请求都有且只有一行**，含全部拒绝路径：过大 / id 非法 / 版本不符 / 过期 / 未知方法 / 处理器异常。
- ⚠️ **轮转默认关闭**（`maxActionLogBytes` 默认 0）。理由：battle 日志是**实验数据**（`battles/` 从不自动删），
  而账本是**过程记录**、天生可截断 —— 但"默认删除用户磁盘上的文件"是另一回事，所以只在你显式配置后才删。
  开了之后只留最近 `actionLogKeepFiles` 个归档（对照：Bannerlord.GameMaster 的 `CommandLogger` 是**无条件**只留 5 个）。
- **坏行不静默**：`bl_common.load_actions()` 遇到解析失败或"合法 JSON 但非对象"的行会**计数并带出原文**，
  与 `load_events()` 的"静默跳过"**故意不同** —— v0.8.4 那个非法 JSON 的 bug 正是靠那种静默活下来的。
- 异常**无条件**写游戏 RGL 日志（引擎自己的 `rgl_log_<pid>.txt`），
  因为账本写不进去时（`<LogDir>` 不可写）**恰恰是最需要留痕的时候**。

---

## 七、JSONL 事件格式（schema 1）

**公共字段**：每条事件都带 `t`（事件类型）与 `time`（秒）。

> ⚠️ **`time` 的时钟原点 = 该文件所属那一轮的起点，不是整场起点。**
> 多轮模式下每轮是**独立文件**（`TelemetryBehavior.BeginNewRound` 换文件并把自身 `_elapsed` 归零），
> `RoundOrchestratorBehavior` 的轮内时钟在同一次换轮里一并归零。
> 因此**同一文件内所有事件的 `time` 共享同一原点**，可直接比较、可按时序切窗口。
> 需要整场耗时请跨文件求和或用 `meta.startedUtc`。
>
> **v0.8.9 修正**：v0.8.5–v0.8.8 期间 `round_start` / `round_cleanup` / `round_all_done`
> 的 `time` 误用了**整场累计**值（而同文件遥测用轮内值），导致同一文件內出现两条不衔接的时间轴。
> 实测样本 `battle_20260924_220146_854.jsonl`：遥测主时钟 0.01→155.70，
> 而 `round_all_done` 落在 300.45 —— 超出主时钟区间 144.75 秒。
> **分析旧日志时须注意**：v0.8.8 及更早的多轮日志里，`round_*` 三条事件的 `time` 不可与同文件其它事件直接比较。
>
> **v0.8.10 修正**：v0.8.5–v0.8.9 期间 `probe`（`ScoreHitProbeBehavior._elapsed`）同样一直用**整场累计**值，
> 且**第 1 轮看不出来**（该轮整场时钟 ≡ 轮内时钟，故单轮验证必然漏掉这一类）。
> 实测 `battle_20260925_111509_578.jsonl`（第 2 轮）：`probe` 落在 230.39–310.42，
> 而同文件其余事件的上界只有 91.43（超出 139 秒）。
> **分析旧日志时须注意**：v0.8.9 及更早的多轮日志里，`probe` 的 `time` 与同文件其它事件**不同源**（第 2 轮起必现）。

| t | 字段 |
|---|---|
| `meta` | schema / mod / version / startedUtc / file；**v0.8.3 起**另带 `mission`（`bridge`=BlBridge 自建靶场 / `game`=其它，含玩家在战役沙盒里的实战）；**v0.8.4 起**另带 `randomSeed`（-1 = 未指定）；**v0.8.5 起**另带 `round`（多轮连续实验的轮次）；**v0.8.41 起**另带 `terrain` / `terrainSeed` / `friendlyFire`（未设分别是 `""` / `-1` / `-1`）与 `aTactics` / `dTactics`（请求的战术档位，-1 = 不覆盖）/ `aTacticsNative` / `dTacticsNative`（**引擎原生**档位 = 该方参战兵种 `max(Tactics 技能)`） |
| `unit` | agent, side, troop, level, isHero, isMounted, maxHp；**v0.8.8 起**另带 `formation`（该 agent 的**实际**编队名，见下注） |
| `hit` | attacker, defender, aSide, dSide, aTroop, dTroop, weaponClass, isMissile, damageType, bodyPart, **dmg**, magnitude, absorbedByArmor, strikeType, hpAfter, hpMax, mounted；**v0.7.9 起**另带 `blocked`、**`damagedHp`**（引擎直给的实际扣血）、`hitDistance`、`shotDifficulty`、`attackDir`、`attackType`、`speedMod`、`atkStun`、`defStun`、`dmgPct`、`blowFlags`（逗号组合串）、`shieldHp`、`shieldMax`；**v0.8.1 起**另带 `bodyPartName`（部位直名）、`shieldSlot`、`shieldItem`（盾的槽位与物品 id，用于区分"换了盾"与"盾被修复"） |
| `shot` | **v0.7.9 起**：shooter, side, troop, weaponSlot, weaponClass, px/py/pz（位置）, vx/vy/vz（速度向量）, speed；**v0.8.1 起**另带 `weaponSlotName`（槽位直名） |
| `state` | **v0.7.9 起**：每 2 秒 × agent：agent, side, troop, px/py/pz, vx/vy, speed, maxSpeed, combatSpeed, armorEnc, weapEnc, morale, aiState, reloading, reloadPhase, reloadCount, ammo, ammoMax（末 5 项在取不到武器时会缺）。⚠️ **三者的量纲不同，不可直接比较**：`speed` 是 `MovementVelocity.Length`（**世界单位速度 m/s**），而 `maxSpeed` / `combatSpeed` 是 `DrivenProperty.MaxSpeedMultiplier` / `CombatMaxSpeedMultiplier`（**倍率**，基准 1.0）—— 见下方「速度上限」注 |
| `ai` | **v0.7.9 起**：每 agent 一条，30 个 AI / 精度参数（格挡能力、射击频率、瞄准误差、提前量误差…）；**v0.8.0 起**另带 `armorHead` / `armorTorso` / `armorLegs` / `armorArms`（四部位护甲值，用于验证护甲覆盖是否生效）；**v0.8.1 起**另带 `topSpeedReach`（加速到顶速所需时长；引擎不暴露世界单位速度上限，见下注） |
| `equip` | **v0.8.41**：每 agent 一条的**入场静态装备逐槽快照** —— agent, side, troop, isFemale, bodySeed, `slots[]`（只含有物品的槽），每槽 `{i, slot, item, mod, modName?, modArmor?, modDamage?, modSpeed?, modHitPoints?}`。`mod` = `ItemModifier.StringId`（**空串 = 该槽没有修饰符**）。用途见下方「装备修饰符」注 |
| `kill` | victim, killer, victimTroop, killerTroop, vSide, state, dmg, damageType, bodyPart, isMissile, weaponClass；**v0.8.1 起**另带 `bodyPartName` |
| `flee` / `panic` | agent, side, troop |
| `sample` | 每 10 秒：aAlive, dAlive, aHp, dHp；**v0.8.45 起**另带 `corpses`（残留尸体数 = `Mission.AllAgents` 里 `IsAddedAsCorpse()` 为真的个数；取不到 = -1）。用途：让 `keepCorpses`（`DisableCorpseFadeOut`）这个**托管侧零消费者**的旋钮有可观测的行为判据 |
| `squad` | **v0.8.8** 多兵种/战术组：每组一行，**仅当该方给了 `attackerGroups`/`defenderGroups` 时才出现**（旧 plan 不产生）。字段 `t / round / side / group / troop / count / formation / movement / spawned / source`；`round` 是 1 基轮次、`group` 是 **0 基**组下标、`formation` 是该组的**实际**编队（**不是** DSL 里写的那个）、`movement` 是 DSL 原值（缺省 `charge`）、`spawned` = 该组实际生成/提供的数量（`source="supplier"` 时 = 交给引擎的 origin 数；`source="respawn"` 时 = `SpawnAgent` 成功次数）|
| `end` | aAlive, dAlive, aInitial, dInitial, hits, kills, flees, **ioFailed, ioError**；**v0.7.9 起**另带 `nanCount` 与 `validity{verdict, ticks, ticksPerSecond, wallSeconds, maxStallMs, …}` |
| `dummy_meta` / `dummy_hit` / `dummy_end` | 靶场专用（阶段 2①）：dummySide, freeze, **armor**（v0.8.0 的护甲覆盖值）, applied, appliedByHp, blocked, hpAfter, hpMax, restored, leakedDeaths, hpMismatch；**v0.8.1 起** `dummy_hit` 另带 `bodyPartName`；**v0.8.2 起** `dummy_meta` 另带 `bodyItem`（请求替换的身甲物品 id） |
| `dummy_swap` | **v0.8.2**：靶子身甲被替换时的一条记录 —— item, **material**（实际生效的材质，可观测落点）, armorBody, agents；找不到物品时 `agents=0` 且带 `error` |
| `round_start` / `round_cleanup` / `round_all_done` | **v0.8.5** 多轮连续实验：round / rounds / cleanedUp（清场人数）；`round_cleanup` 用 `cleanedUp=-1` 标出分界。`time` 取**轮内**时钟（见上方公共字段说明）。<br>⚠️ **`round_cleanup` 与下一条 `round_start` 之间的阵亡是"补刀死"（不是自然战死），分析时须排除**——与「死因偏差」同一类坑 |

> **为什么有 `bodyPartName` / `weaponSlotName`（v0.8.1）**：引擎的两个枚举带**同值别名**，
> `ToString()` 返回别名而不是直观名 —— `BoneBodyPartType` 里 `Head = 0` 与 `CriticalBodyPartsBegin = 0` 同值
> （实测：`Head` 出现 **0** 次、`CriticalBodyPartsBegin` **2277** 次，占命中 43%~54%）；
> `EquipmentIndex` 里 `WeaponItemBeginSlot = 0` 与 `Weapon0 = 0` 同值（`Weapon0` 永不出现）。
> 旧字段**保持原样**（不破坏已有 60+ 场历史日志），分析请优先用 `*Name` 字段；映射见 `src/EnumNames.cs`。
>
> **速度上限（v0.8.1 的诚实标注）**：引擎**不通过公开 API 暴露世界单位的速度上限** ——
> `AgentDrivenProperties` 的 100+ 属性里只有 `MaxSpeedMultiplier` / `CombatMaxSpeedMultiplier`（**倍率**），
> `AgentStatCalculateModel` 也没有 `GetMaximumSpeed`（2026-09-24 反编译核实）。
> 所以"上限是否生效"只能用**统计口径**间接判断：同一兵种/状态下「实测速度峰值 ÷ 倍率」是否恒定；
> 新加的 `topSpeedReach` 是加速模型的直接读数。
>
> **`unit.formation` 与 `squad`（v0.8.8，多兵种混编 + 战术组）**：
> - `unit` 事件新增字段 **`formation`** = 该 agent 的**实际编队名**（`FormationClass` 真名；空编队写 `"Unset"`）。
>   这是**新增字段**：既有字段名/顺序/取值域不变；**旧日志没有该字段**，分析侧按既有“缺字段兜底”策略处理。
> - 新增事件 **`squad`**（**每组一行**，**仅当该方给了 `attackerGroups`/`defenderGroups` 时才出现**；旧 plan 不产生）：
>   字段 `t / round / side / group / troop / count / formation / movement / spawned / source`。
>   其中 `round` 是 1 基轮次、`group` 是 **0 基**组下标、`formation` 是该组的**实际**编队（**不是** DSL 里写的那个）、
>   `movement` 是 DSL 原值（缺省 `charge`）、`spawned` = 该组实际生成/提供的数量
>   （`source="supplier"` 时 = 交给引擎的 origin 数；`source="respawn"` 时 = `SpawnAgent` 成功次数）。
> - 原则：**schema 1 内的新增字段/事件，旧日志仍可解析**（GC2）。
>
> **`equip` 事件（v0.8.41）：装备修饰符到底有没有在动护甲**
> - 为什么加它：`PROGRESS.md` §十五 曾写下「`armorBody` 是确定性的（3/3 逐场一致）⇒ **不是随机 modifier**」。
>   **这条推断不成立** —— `AgentBuildData.AgentEquipmentSeed` 来自 `IAgentOriginBase.Seed` / `UniqueSeed`，
>   种子若每场确定，则由它抽出的随机 modifier **同样每场一致**："3/3 一致"分辨不了
>   「没有 modifier」与「种子确定的 modifier」，而后者是**随 agent 序号漂移的隐藏变量**，
>   正好污染护甲/材质对照。
> - 判别判据（拿到数据一眼可判，不用改代码）：同一 `troop`、同一场、**不同 agent 的 `mod` 是否各不相同** ——
>   不同 ⇒ 随机 modifier 成立（混杂源存在）；全 `""` ⇒ 该路径确实不加，旧结论可用。
> - `modArmor` 就是能解释「同一件 XML 甲、运行时护甲值不同」的那个数：反编译取证
>   `ItemModifier.ModifyArmor(int armorValue) => Math.Max(armorValue + Armor, 1)`
>   ⇒ **修饰符对护甲的贡献是纯加法**，`Armor` 即该槽的护甲增量。
> - 槽位上限取 `EquipmentIndex.NumEquipmentSetSlots`（= 12，反编译取证）；只写有物品的槽。
> - 数据源是 `Agent.SpawnEquipment`（**入场静态装备**），不是 `Equipment`（`MissionEquipment`，带耐久/装弹的运行时状态）。
>
> **战术档位（v0.8.41）：`attackerTacticLevel` / `defenderTacticLevel`**
> - 引擎真身：`CustomBattleCombatant.GetTacticsSkillAmount()` = **该方参战兵种的 `max(Tactics 技能)`**；
>   `MissionCombatantsLogic.EarlyStart` 拿它**分 20 / 50 两档**决定给该方挂哪些 `TacticOption`
>   （`<20` 只有 `TacticCharge`；`>=20` 追加 `TacticFullScaleAttack` 等；`>=50` 再追加 `TacticFrontalCavalryCharge` 等）。
>   ⇒ 这就是 EBT README 所说"战术等级 0-20 / 20-50 / 50+"的引擎真身。
> - 实现：`src/TacticsCombatant.cs` —— 一个**只读包装**（`IBattleCombatant`），只在请求了档位时才包上。
>   `CustomBattleCombatant` 非 sealed，但 `GetTacticsSkillAmount` 是 `virtual final`（子类无法覆盖），
>   而 `MissionCombatantsLogic` 的 4 个 ctor 参数**声明类型全是 `IBattleCombatant`、全类无下转型** ⇒ 包装安全。
> - ⚠️ **默认 `orders=charge` 会 `ClearTacticOptions()` 只留 `TacticCharge`**，档位看不出效果；
>   要看效果必须配 `orders=default`。`status` / 响应里的 `aNative` / `dNative` 是引擎原生值，用它与请求值对照。
>
> **环境旋钮（v0.8.41）：`terrain` / `randomTerrainSeed` / `aiFriendlyFireMultiplier` / `keepCorpses`**
> - 全部是 `MissionInitializerRecord` 上的**已核字段**，零 Harmony。**不传就不动**（未请求时与改动前逐字段一致）。
> - 地形名取自 `TaleWorlds.Core.TerrainType` 全表（23 项，小写）；非法名**显式报错**并列候选，不静默兜底成 plain。
>   `bl_common.TERRAINS` ↔ `src/BattleEnv.cs` 的 `TerrainTable` 由 `bl_selftest` ⑭ **双向对账**（防两表漂移）。
> - ⚠️ **`terrain` 在自定义战斗里没有托管消费者**（2026-10-05 反证）：它唯一已知的托管消费点
>   `SandboxAgentStatCalculateModel`（按地形给**队长**加 Tactics Perk）**注册在 `if (game.GameType is Campaign)` 里**
>   （`SandBoxSubModule.cs:37-41`），而自定义战斗注册的是 `CustomBattleAgentStatCalculateModel`（`CustomGame.cs:95`），
>   后者全文 `Terrain`/`Perk` 命中 **0**。⇒ 本靶场里 `terrain` **托管侧无影响路径**（native 侧未排除）。
>   实测"默认 vs snow 无可测差异"与此一致。
> - ⚠️ **`aiFriendlyFireMultiplier` 的作用面与名字不符**（2026-10-05 实测）：它写的是
>   `DamageToFriendsMultiplier`，而引擎判据是 `victimAgent.IsFriendOf(mainAgent)`
>   ⇒ 它管的是"**打到「玩家方」身上的伤害**倍率"（本靶场玩家方 = `Attacker`，可用 `playerSide` 改），
>   **不是"消掉同队误伤"**（同队误伤在引擎里本就近乎无害：418131 次命中里仅 1.39%，其中 99% 判定为 0 伤害）。
>   实测（匹配集交错 A/B，`fian_champion`20 vs `legionary`20）：设 0 时"打到玩家方的命中零伤害占比"
>   从 **20.2%±3.4（n=5）升到 71.7%（n=4）**，`t=5.20`、两组范围不重叠 ⇒ **显著**，但**不是"全部归零"**。
> - ⚠️ **攻城路径不支持这四项**（走官方 `OpenSiegeMissionWithDeployment`，它自建 record）⇒
>   传了会被**显式忽略**并出现在响应/`status` 的 `envNotes` 里，**不静默**。
>   攻城可设的是 `sceneLevel`（1..3，默认 3）与 `timeOfDay`（小时，默认 6）—— 这两个原本是写死值。
>   **2026-10-05 已真机取证**：`sceneLevel` 有硬判据（rgl 日志 `Opening new mission CustomSiegeBattle level_1 siege.`
>   逐字对上，且与默认 `level_3` 成对照）；`timeOfDay` 用截图判据（22 → 黑夜/月光/火把，12 → 正午）。
>   ⚠️ **`timeOfDay` 只有 `{6,12,15,18,22}` 这五个值有别**，其它值（如 14）引擎查表落空
>   ⇒ **回落到场景自带氛围**（看起来像"没生效"）。另注 `15 → TOD_04_00_SemiCloudy`（名叫"下午"、取的是凌晨 4 点）。
> - **时刻/天气/雾暂未做**：`AtmosphereInfo` 是 `TaleWorlds.Library` 里的 ValueType，含 10 个子结构与 `IsValid`；
>   手搓一份要先把子结构全部核清，核错就是黑屏或原生崩溃 ⇒ 留待专门取证，本轮不做。

---

## 八、回退

- 停止记录：启动器取消勾选 **BlBridge Telemetry**
- 完全移除：删除 `...\Modules\BlBridge\`
- MCP 解绑：`python tools\register_mcp.py --remove`
- 配置备份：`~\.codebuddy\mcp.json.bak_blbridge`

---

## 九、已验证 / 未验证

> ⚠️ **本节是历史记录**（写在 v0.1.0–v0.5.0 时代，下面的"尚未验证"清单早已过时）。
> **当前的验证状态以 `PROGRESS.md` 与 `docs/` 下的排查/结论文档为准**，本节只保留"当时验证了什么"这一层信息；
> 数字类计数不再手写（会漂移），改为给出可复算的出处。

**已本地验证（无需游戏）**
- Roslyn 编译通过：全部 `src\*.cs`（源文件数以 `out/BlBridge.manifest.json` 的 `sourceCount` 为准，DLL 体积以 `dllBytes` 为准 —— 不再手写数字避免漂移），已部署
- **离线单测 146 项**（`tools/jsontest/build_and_run.ps1`）：JSON 读取器（含"字符串值劫持键查找"）、请求闸门、探针判定规则、构建身份、配置校验、编队 DSL、主菜单层面状态名、改令名字表/校验器（含 v0.8.32 的骑乘令三档、`targetAgent` 下标上界、`formation` 死字段家族比对）
- **Python 自测 306 项断言**（`tools/bl_selftest.py`，2026-09-26 实测；`[OK]` 计数可复算）：分析器（血量偏差 0.0%）、MCP 协议（44 工具）、控制通道（含 `list_ui`/`open_ui`/`close_ui` 往返、`bl_order` 往返、`bl_control_agent` 往返）、
  **构建链四段判定**、**配置加载即校验**、**崩溃判定**、真实 config.xml 回读与 dry-run
- `bl_build_check` 在真实目录实跑：`builtVersion` 与 `deployedSha256` 与清单一致（游戏未启动时为 `game_offline`）

**已在游戏里验证**
- 只有 v0.1.0：模块成功加载（`bridge_status.json` state=loaded、missionsThisSession=1、记录到 201 个事件）

**尚未验证（等下一次启动游戏）**
- v0.1.1 的落盘修复（上一版因挂载点时机错误，文件没生成）
- v0.2 的 AI 推演：`start_battle` 能否真的开出 AI vs AI 战斗（`MissionState.OpenNew` 生命周期是唯一真未知数）
- v0.3.0 的加速通道（`fast_forward` / `speed` 是否显示 `isFastForward: true`）
- v0.4.0 全量：过期请求作废（`request_expired`）、id 白名单、遥测文件名毫秒级、浮点 round-trip
- v0.5.0 全量：`readiness` 是否 `advancing`、`end.validity` 是否 `ok`（**这条会直接检验我最不确定的假设：
  `Mission.CurrentTime` 在加载界面时是否真的停止推进**）、`build.fileChangedSinceLoad` 是否为 false
- v0.6.0 全量：`sessionDiagnosis`（正常退出应显示 `clean_exit`）、`config` 块、`blbridge_game.json` 是否被读取
- v0.7.0 新增探针：`t="ff"` 事件（判定"原版快进按钮是否接线"的关键证据）、`t="stall_start/stall_end"`
  （定位上一场那 21.4 秒冻结出现在哪个阶段）

> **首场实测已完成**（2026-09-23 22:00，v0.6.0）：800 单位 / 17400 事件 / 15344 命中（实测行数；`kill` 776 / `unit` 800 / `sample` 52）、`readiness=advancing`、
> `validity=suspect`（21.4 秒冻结）、`nanCount=0`。完整读数与对账见 `..\首场实测_20260923_220054.md`。

> 截至本文档核对时（2026-09-23 22:00 前后），`bridge_status.json` 仍停在 `version 0.1.0`（`statusWrittenUtc` 20:54），
> `battles\` 为空 —— **v0.1.1 之后的全部改动，一次都还没在游戏里跑过**。

---

## 十、已知修复记录

| 版本 | 问题 | 根因 | 修法 |
|---|---|---|---|
| v0.1.1 | 计数器涨到 201 但日志文件为空 | 引擎顺序：`Mission.cs:3807 OnBeforeMissionBehaviorInitialize` → `3811 OnBehaviorInitialize 循环` → `3815 OnMissionBehaviorInitialize`；我挂在 3815，行为初始化回调永不触发 | 改挂 `OnBeforeMissionBehaviorInitialize` + 文件惰性打开兜底 + `ioFailed/ioError` 诊断字段 |
| v0.2 | — | 新增 AI 推演与命令泵 | 见上 |
| v0.3.0 | 战斗"加速"不可用 | ~~原版无加速键~~（**此判断已被外部审计推翻，见 §六 加速通道的修正记录**）。修正后的结论：原版只有**计分板 UI 入口**（`FastForwardButton` + `ScoreboardHotKeyCategory.ToggleFastForward`，默认 **F**），需要可见计分板 + 玩家操作，无程序化触发；第三方 RTSCamera 亦提供快进，但其 `Fastforward` 键位注册为空列表（`RTSCameraGameKeyCategory.CreateCategory`，反编译见 `..\tools\rts_src\RTSCamera.decompiled.cs:15224`），用户配置 `RTSCameraGameKeyConfig.xml` 里同样为空 → **该键当前无绑定**（"曾经绑过"未验证） | 自建加速通道：`Mission.IsFastForward` + 每帧重申（无键、无 UI、可编程）；新增 `bl_fast_forward` 工具与 `bl_cmd.py fastforward/speed` |
| v0.4.0 | 幽灵请求 / 路径穿越 / 数据静默覆盖 / 部署静默失败 | 读 Coop 源码时照出我们自己 6 个缺陷（详见 `..\Coop源码对照_可复用清单.md` §1） | 新增 `RequestGuard`（过期作废 + id 白名单 + 大小上限）；`Jmini` 键查找改词法扫描；遥测文件名毫秒级 + `CreateNew`；浮点 round-trip + NaN 计数；超时三值归因 + 进程死亡早退；`build.ps1` 查进程 + 备份 + SHA256；新增 44 项离线单测 |
| v0.8.9 | **多轮日志内时钟不同源**：同一 jsonl 里遥测事件用轮内时钟，而 `round_start`/`round_cleanup`/`round_all_done` 用整场累计值 → 两条不衔接的时间轴（实测样本：遥测主时钟 0.01→155.70，`round_all_done`=300.45，超出 144.75 秒）。**静默失真**：不抛异常、不丢数据，仅让按 `time` 切窗口的下游分析算错 | `RoundLog` 写出的每条事件都带 `round`（语义属某轮），但其 `time` 复用了 `_elapsed`（整场累计）。而每轮是独立文件，`TelemetryBehavior.BeginNewRound` 已把遥测侧 `_elapsed` 归零 —— 两侧原点不一致 | `Advance` 里 `_round++` 之后、`RoundLog("round_start")` 之前补 `_elapsed = 0f;`，与 `tb.BeginNewRound` 同步（共用同一原点）。`_elapsed` 全部 4 处用法均只服务遥测 `time`，故直接改语义、不新增字段（15 行插入、0 行删除）。README §七 补「公共字段」说明，§十一 判据追加第 6 条 |
| v0.8.9 | **场景名不存在 → 引擎原生崩溃（`0xC0000005`，整个进程死）**：请求里的 `scene` 字符串被直接交给 `new MissionInitializerRecord(scene)`，而引擎的 `Scene.Read(sceneName)` 是纯原生调用（`EngineApplicationInterface.IScene.Read`，**无返回值、不抛托管异常**）⇒ 场景名写错时 `try/catch` 结构上拦不住，native 拿空指针直接访问违规。实测 11:40:31.868 `Loading xml file: SceneObj/bridge/scene.xscene` → 6 ms 后 `Unhandled Exception Code 0xC0000005`；对照：同一请求传 `battle_terrain_a` 则正常 | `Start` 的参数校验只管了「兵种 id 能解析」「组 DSL 合法」「必须在自定义战斗界面」，**独独没管场景名** —— 而它是唯一会触发进程级失败的那个 | 新增参数校验 **2c) 场景存在性检查**（在开 mission 之前）：遍历 `ModuleHelper.GetAllModules()` 的每个 `FolderPath`，探测 `SceneObj/<scene>/scene.xscene`（与引擎自身寻址一致，不需进 native）；拒绝含路径分隔符的名字；不存在则返回 `unknown_scene` + 可用野战场景清单（最多 12 个）；校验自身出错时**不静默放行**。受控样本对 7/7 通过（`bridge`/不存在名/空串/路径穿越 ⇒ 拒绝，`battle_terrain_a`/`_001` ⇒ 放行）。⚠️ **尚未做真机回归**；**v0.8.10 更正：那 7 项在本仓库找不到可复现入口，且与代码事实相反，应视作未验证（见 PROGRESS §十九 更正注、§二十）** |
| v0.8.10 | **`probe` 事件时钟未随轮次归零** ⇒ 第 2 轮起与同文件其它事件不同源（实测 578：`probe` 230.39–310.42 vs 同文件其余上界 91.43）。同时暴露**验证门假通过**：v0.8.9 新增的时钟校验器对此完全无感、还判它 PASS | `ScoreHitProbeBehavior._elapsed` 从 mission 开始累加、全仓库无归零点（另两个时钟 `TelemetryBehavior` / `RoundOrchestratorBehavior` 都有 —— 代码内对照 2:1）。而校验器的参考区间取"除 `round_*` 外全体 min/max"，**反单调**：异源事件的大值抬高区间、反而放宽判定 | 新增 `ScoreHitProbeBehavior.BeginNewRound()` 归零 `_elapsed` / `_nextReportAt`（**计数刻意不归零** —— 探针判据是整场配平），由 `RoundOrchestratorBehavior.Advance` 在 `tb.BeginNewRound` 同一处调用；校验器判据改为**锚时钟 `state` + 逐类型起点点名**，`OVER_TOL=3 s`。**已真机验证**（PROGRESS §二十 §5.2：3 轮文件 `probe` 与锚同源，第 2/3 轮由 230.39/320.44 起 → 10.03/10.04 起） |
| v0.8.10 | **场景守卫的路径拼接错 ⇒ 每一次 `start` 都被自己判成 `unknown_scene`**（该守卫 v0.8.9 提交后 **0 次真机运行**，故未暴露；§十九 当时记的「7/7 通过」不可复现且与事实相反） | 把"模块根"当成"带尾分隔符的目录前缀"：`mi.FolderPath + "SceneObj/" + …`，而 `FolderPath` **不含尾分隔符** —— 反编译 `ModuleInfo.LoadWithFullPath`（`FolderPath = fullPath;` → `FolderPath + "/SubModule.xml"`）与运行日志 `..\..\Modules\SandBoxCore/SubModule.xml` 逐字吻合（引擎自己的同类实现：`GameTextManager` / `MapScene` / `MBInitialScreenBase` / `Module` 四处全部用 `Path.Combine` 或显式补 `/`，新代码是唯一裸拼接） | 改 `Path.Combine(FolderPath, "SceneObj", scene, "scene.xscene")`（场景清单那处同步）；按引擎同款改用 `GetActiveModules()` + `IsActive`（原 `GetAllModules()` 会 fail-open）；新增 `IsSafeSceneName()` 显式拒绝 `.` / `..`（`SceneObj/../…` 会逃出 `SceneObj/`）。**已真机验证**（PROGRESS §二十 §5.1 样本对 6/6：`battle_terrain_a` 放行并打完 18.84 s 战斗、`bridge` / `..` / `../etc/passwd` / `a/b` / `battle_terrain_zzz` 全部 `unknown_scene` 且进程不崩） |
| v0.8.10 | **loading 卡死 ⇒ 状态机永久 `busy=true`**：mission tick 停住时（原生崩溃现场），`durationCapSec` 超时与 `abort` 的 `_endRequested` 判定都写在 tick 内 ⇒ 状态永久停在 `loading`，后续 `start` 全被拒（"已有一场推演在进行中"），只能重启游戏。`LastHeartbeatUnix` 当时是**只写不读**的死字段 | 熔断逻辑与它要监控的回调在同一条路径上（tick 内）—— 卡死时两者一起停 | 新增 `ScenarioRunner.Watchdog()`，挂 `SubModule.OnApplicationTick`（主线程每帧，实测卡死时仍在跑）；阈值 loading `120 s` / running `durationCap + 60 s`（不抢在正常收尾前误杀）；收尾**先**落状态（`Busy` 立即 false、`result` 带 `stuckState`/`stuckSec`）**后**尝试 `EndMission()`。**已真机验证**（PROGRESS §二十 §5.3 受控实验：阈值临时改 0.5 s，`state=error` / `busy=false` / `reason=watchdog_loading`（0.50 s 触发），且**随后 `start` 仍返回 `accepted`** —— 熔断后桥恢复可用） |

---

## 十一、编码约定（统一 UTF-8）

**全链路 UTF-8，没有例外**（2026-09-25 统一）：

| 环节 | 口径 |
|---|---|
| `tools/*.py` 的 stdout/stderr | `bl_common.safe_streams()` ⇒ `reconfigure(encoding="utf-8", errors="replace")`，**每个输出脚本入口显式调用** |
| `tools/*.py` 文件读写 | `open(..., encoding="utf-8")`；容忍 BOM 的输入用 `utf-8-sig`；二进制一律 `"rb"`/`"wb"` |
| 子进程 stdout 解码（离线自测） | 一律 `encoding="utf-8"`，且**不设** `PYTHONIOENCODING`（子进程自己钉），也别"设 gbk 再按 gbk 读" |
| C# 控制台（`tools/jsontest/GuardTest.cs`） | `Console.OutputEncoding = Encoding.UTF8` |
| C# 文件读写（日志 / 配置 / 命令通道） | `Encoding.UTF8` 或 `new UTF8Encoding(false)`（写文件不带 BOM） |
| 战斗日志 JSONL | C# 写 UTF-8 无 BOM ⇄ Python `io.open(..., encoding="utf-8")` 读 |

**为什么必须显式钉**：Windows 上 Python 的 stdio 默认按 **locale 编码**（简中 = cp936/GBK），
C# 的 `Console` 默认按**控制台代码页**。写入编码 ≠ 读取编码 ⇒ 中文整片变成 `U+FFFD`。
本项目的输出消费端是**调用这些工具的 AI / 管道**。**本机实测**：系统 `chcp 936` / ANSI `gb2312`，
宿主 PowerShell（5.1）的 `[Console]::OutputEncoding` 是 `utf-8`，而 Python 默认按 locale(cp936) 写
⇒ 写入/读取不一致就整片 `U+FFFD`。所以口径统一在 UTF-8，**不依赖控制台代码页**。

**别再走回去**（2026-09-25 走过的弯路）：旧版 `safe_streams()` 只 `reconfigure(errors="replace")`
不改 encoding，号称"GBK 控制台可读"—— 那只是把错配挪到另一边，一离开 GBK 就乱码；同类半吊子
还有 `bl_sage.py` 里一份内联的 `reconfigure` 副本（已删，收敛到 `bl_common.safe_streams()`）。
另一个反向的弯路是把中文**译制成英文**来"绕开乱码"—— 编码问题不该靠换语言解决，那些提交已 revert。
改任何输出前，先读 `bl_common.safe_streams()` 的 docstring。
| v0.5.0 | 无效样本无法识别 / 手动核对 DLL 版本 | 清单 §4 第 2 项（C20 引擎帧就绪判据 + A2 构建一致性） | 新增 `EngineProbe` + `ProbePolicy`（任务时间/tick/墙钟/暂停 → `readiness` 与 `end.validity`）；`BuildInfo` + `build_manifest.json` + `bl_build_check`（四段哈希链）；`bl_start_battle` 加构建 preflight；`bl_wait_for_state` 卡住早退；离线单测 44 → 57 项、Python 自测 36 项 |
| v0.7.0 | 首场实测暴露：21 秒冻结无法定位 / 程序集版本恒为 0.0.0.0 / 构建检查用上一局数据下结论 | 见 `首场实测_20260923_220054.md` §五 | 新增 `t="ff"` 事件（记录 `IsFastForward` 每次变化 → 可一次判定"按钮是否接线"）+ `t="stall_start/stall_end"` 与 `validity.maxStallAtMissionTime`（定位冻结阶段）；`build.ps1` 生成版本源文件（`assemblyVersion` 0.7.0.0）并**自动同步 `module/SubModule.xml` 版本**；`build_check` 先确认状态文件属于当前会话（修掉 `game_running_other_build` 误报）；`bl_cmd.py fastforward` 不带参数改为查看状态；Python 自测 54 项 |
| v0.7.3 | **镜像对局（同兵种 20v20）却一边倒**：攻方 13:0 / 6:0 全胜 | 引擎默认战术是「攻方进攻、守方原地防守」，冲锋方在混战中占优（实测攻方命中 407 次 vs 守方 285 次，且守方反而先命中）→ 该偏差足以掩盖真实兵种差异。官方 `CPUBenchmarkMissionLogic.AfterStart:160-175` 同样要对双方 `ClearTacticOptions()` | 场景默认改为**双方对称 `TacticCharge`**（+每个阵型 `MovementOrderCharge`）；新增 `--orders charge\|default` 与 `--player-side attacker\|defender` 两个实验开关；另外修正**伤害口径**：遥测 `dmg` 是实际扣血、`absorbedByArmor` 是另一路记账（被盾挡下的命中会带 267 而**一滴血不掉**），分析器改用 HP 真实变化计算，两局复核偏差均为 **0.0%**，新增"被挡下%"指标（盾臂 97% / 躯干 28%）；新增 `crashreport_probe.py`、`hit_semantics_probe.py`、`dump_agent_hits.py` 三个取证工具。**⚠️ 部署后 7 局实测的重要补充：战术对称化并没有消除偏差，真凶是 `playerSide` 标记**（——详见 §3.5）。
镜像局里**被标记为玩家侧的一方必胜且全歼**（5/5：攻标记→攻胜 3 次，守标记→守胜 2 次），偏差只跟标记走、不跟攻守方向走；
指纹是战斗 AI 有效性差异（玩家侧格挡 72.6% vs 敌方 58.5%，落地击数 162 vs 106）。
已排除出生时间、等级、装备、`DefaultMissionDifficultyModel`（其伤害倍率依赖 `MainAgent`，我们的 mission 无主角故全员 1.0）四条路径，
剩余嫌疑是 `Mission.GetAgentTeam` → `PlayerTeam`/`PlayerEnemyTeam` 的队级差异（具体行号待挖）。
混合兵种局则**守方两连胜**（守方接战节奏占优：两局守方都先手命中，87.5s vs 111.5s / 54.7s vs 68.3s）。
⇒ **A/B 协议由此从"建议"升级为强制**：固定 `playerSide=attacker`、兵种换边双跑、每配置 ≥3 局取均值、只认聚合统计。
**文档同步修正**（WorkBuddy 复核提出的 D1/N3 + 键位存疑）：① 标题不再携带版本号（D1 根治，D7 的自动同步只覆盖 `SubModule.xml`，标题改走"以 manifest 为准"原则）；② 首场命中数 15345 → **15344**（N3，实测行数：`hit` 15344 / `kill` 776 / `unit` 800 / `sample` 52）；③ 原版 F 键链路从"推断"升级为"已验证"（`CustomBattleScoreboardVM` 有 `IsMainCharacterDead` 门槛，即原版 F 键加速**只在主角阵亡后的记分板里可用**）；④ `InputKey 68 = F10`（非 F；反编译 `TaleWorlds.InputSystem.dll` 枚举 + 用户配置 `FreeCamera=F10` 交叉印证）。详见 `镜像对局_偏差与伤害口径_20260923.md` |
| v0.7.2 | **首次 AI 推演把游戏打崩**（`System.NullReferenceException` 在引擎 `Mission.OnTick` 内） | 我们的 mission **漏了一个行为**：`DefaultBattleMissionAgentSpawnLogic` 的 `_phases[]` 只在 `AddPhase`（`:488`）里被填充，而唯一入口是 spawn handler 在 `AfterStart` 调的 `InitWithSinglePhase`（`:195`）。没有 handler → `_phases` 为空 → `DefenderActivePhase`/`AttackerActivePhase`（`:80/:82`）为 null → `IsInitialSpawnOver`（`:88`，同一批属性里**唯一漏写 `?.`** 的那个）NRE | 补上引擎自带的 `CustomBattleMissionSpawnHandler(_pendingDefender, _pendingAttacker)`（`TaleWorlds.MountAndBlade.MissionSpawnHandlers`），行为列表与官方 `CPUBenchmarkMissionLogic.OpenCPUBenchmarkMission` 逐项等价（10 个）；新增 `tools/crashreport_probe.py` 解析 BUTR 崩溃报告 |
| v0.6.0 | 分不清"崩溃"与"正常退出" / 改采样间隔要重编译 | 清单 §4 第 3、5 项（A9 + C24） | `SubModule.OnSubModuleUnloaded` 写 `cleanExit` + 战斗首帧写 `missionInProgress` → `bl_status.sessionDiagnosis`（7 种结论）；`BridgeConfigFile`（游戏端 `blbridge_game.json`，加载即校验）+ MCP 端 `blbridge.json` + `bl_config`（来历可查、优先级 env > 文件 > 默认）；两份 example 模板；顺带修掉 `run_state_diagnosis/_diag_no_response` 的"参数只生效一半"bug；离线单测 69 项、Python 自测 52 项 |
