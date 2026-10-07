# BlBridge · 给 AI 的完整教程（装 MCP → 加载进游戏 → 用能力干活）

> 你现在读的这个文件位于 `<游戏根>\Modules\BlBridge\mcp\AI-TUTORIAL.md`。
> 它是**这个 mod 包内部**的自带说明书 —— 只要用户装了 BlBridge，你就能从这里把整条链路接起来，
> **不需要用户再单独给你一个 MCP 包**。
>
> 读完这一页你应该能独立完成三件事：
> ① 把 `mcp\bl_mcp.py` 登记成 MCP 工具；② 确认 mod 真的被游戏加载了；③ 用这些工具做战斗实验、改配置、读界面、跑批。

---

## 0. 先认清一件事：一个 mod 包 = 两个单元

| 单元 | 位置 | 谁读它 | 入口 |
|---|---|---|---|
| **A：mod 本体** | `<游戏根>\Modules\BlBridge\`（`SubModule.xml` + `bin\Win64_Shipping_Client\BlBridge.dll`） | 游戏与启动器 | 官方自定义战斗界面（玩家用） |
| **B：MCP 子包** | `<游戏根>\Modules\BlBridge\mcp\`（**本目录**） | **AI（你）** | MCP 工具（**63 个**；不设 `BLBRIDGE_TOOLSET` 时全量暴露，常用的 `core+config+lab` 组合是 59 个） |

两个单元**共用同一条后端**：玩家在界面上的操作与 MCP 的工具最终落到同一条官方开战链。
所以谱系上不存在"界面能做、端口做不了"——唯一例外是"用鼠标点选"这个动作本身。

**关键约束**：

- `mcp\*.py` 是**构建产物**，唯一真相源是开发仓库的 `tools\*.py`。**手改本目录会在下次部署被整份覆盖** —— 要长期改，改仓库然后重新部署。
- 控制通道是**本地文件 IPC**，不是 HTTP：`<我的文档>\Mount and Blade II Bannerlord\BlBridge\commands\{pending,done}`。无端口、无 URL ACL 提权、无防火墙问题，崩溃后请求/响应都留痕。
- 遥测日志在 `<我的文档>\Mount and Blade II Bannerlord\BlBridge\battles\*.jsonl`，**不在 mod 目录下**。别在 `Modules\BlBridge` 里找日志。

---

## 1. 第一步：把 MCP 装上

### 1.1 前提

| 项 | 要求 |
|---|---|
| Python | **3.8+**，**只用标准库**（不需要 `pip install` 任何东西） |
| PowerShell | `bl_launch.ps1` 需要它（无人值守启动游戏用）。5.1 与 7 都可以 |
| 游戏 | 不装游戏也能用**只读日志分析类**工具（见 §4.0） |

### 1.2 三种登记方式（任一即可）

**方式 A：跑自带注册脚本（推荐，幂等 + 自动备份）**

```powershell
python "<游戏根>\Modules\BlBridge\mcp\register_mcp.py"          # 登记 / 更新
python "<游戏根>\Modules\BlBridge\mcp\register_mcp.py" --show    # 只看当前配置
python "<游戏根>\Modules\BlBridge\mcp\register_mcp.py" --remove  # 移除
```

脚本**自定位**：从自己的路径上溯三级得到游戏根，因此**不需要用户手工填路径**。
（判据：上溯结果里必须同时存在 `Modules\` 与 `bin\Win64_Shipping_Client\`，否则回退到内置默认值。）

**方式 B：手写配置**（最通用）

CodeBuddy / CodeBuddy CN 的配置在 `%USERPROFILE%\.codebuddy\mcp.json`；
其它宿主按各自的 MCP 配置位置（本包只要求 stdio）。

```json
{
  "mcpServers": {
    "blbridge": {
      "type": "stdio",
      "command": "python",
      "args": ["<游戏根>\\Modules\\BlBridge\\mcp\\bl_mcp.py"],
      "env": { "BANNERLORD_DIR": "<游戏根>" }
    }
  }
}
```

**方式 C：`.mcpb` 一键安装**（宿主支持时）
`mcp\manifest.json` 是 MCPB 规范 0.3 形态的清单，支持 MCPB 的宿主可以 `mcpb pack` 后一键装。
**本包不依赖它** —— A / B 永远可用。（注意：agent session 默认不支持 bundle，别把"一键安装"当唯一路径。）

### 1.3 环境变量（全部可选，都有内置默认值）

| 变量 | 作用 | 默认 |
|---|---|---|
| `BANNERLORD_DIR` | 游戏根。写配置 / 启动游戏 / 查构建一致性都用它 | 内置默认路径 |
| `BLBRIDGE_TOOLSET` | 工具分组过滤，`+` 或 `,` 分隔。`all` / `*` = 全开 | 全开 |
| `BANNERSAGE_DB` | 覆盖 BannerlordSage 的 sqlite 路径（只读连它做兵种校验） | 探测 BannerlordSage 的 dist 目录 |

分组表：

| 组 | 工具 |
|---|---|
| `core` | `bl_status` `bl_battle_status` `bl_start_battle` `bl_wait_for_state` `bl_abort` `bl_order` `bl_control_agent` `bl_launch_game` `bl_skip_video` `bl_list_ui` `bl_open_ui` `bl_close_ui` `bl_fast_forward` `bl_get_screen` `bl_get_viewmodel_property` `bl_get_inventory` `bl_list_saves` `bl_load_save` `bl_campaign_time` |
| `config` | `bl_read_config` `bl_apply_config` `bl_rts_config` `bl_apply_rts_config` `bl_ghost_camera` `bl_camera_speed` `bl_cheat_mode` |
| `lab` | `bl_list_battles` `bl_analyze` `bl_read_events` `bl_run_batch` `bl_batch_report` `bl_lookup_troop` `bl_build_check` `bl_config` |
| `desktop` | `bl_desktop_windows` `bl_desktop_screenshot` `bl_desktop_click` `bl_desktop_key` |

> 组名**拼错会直接抛错**（不会静默少给工具）。如果某个工具"列不出来"，先查 `BLBRIDGE_TOOLSET`。

### 1.4 装完自查（两条，缺一条就是没通）

```powershell
python "<游戏根>\Modules\BlBridge\mcp\register_mcp.py" --show   # ① 配置里能看到 blbridge
python "<游戏根>\Modules\BlBridge\mcp\bl_cmd.py" status          # ② 游戏在跑时能读到桥状态
```

②返回 `state: idle|loading|running|ended` ⇒ 控制通道通了（**不需要打开游戏内任何界面**）。

> ⚠️ MCP server 列表通常在 **IDE 启动时**加载一次 —— 登记完要**重载 IDE 窗口**工具才出现。

---

## 2. 第二步：把 BlBridge 加载进游戏

### 2.1 部署（安装包形态：文件已经在位）

本包已经就位。若你是从 zip 分发来的，确认目录长这样：

```
<游戏根>\Modules\BlBridge\
  SubModule.xml                          <Id>=BlBridge, <Name>=BlBridge Telemetry, DLLName=BlBridge.dll
  build_manifest.json                    构建清单（四段一致的判据来源）
  bin\Win64_Shipping_Client\BlBridge.dll
  mcp\                                   本目录（MCP 服务器 + 本文档）
```

### 2.2 源码形态的部署（开发时）

```powershell
cd <仓库>\BlBridge
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Deploy
```

`build.ps1` 会：确认 **DLL 没被锁**（游戏在跑就会被拒）→ 备份旧 DLL（带时间戳，只留最近 3 份）→ 同步 `SubModule.xml` / `manifest.json` 的版本 → 拷贝 → **SHA256 比对校验**。
（过程名只用来警告，**锁才是硬判据**：以前残留的 `Bannerlord.BLSE.Launcher` 进程会让部署白白被拒。）

### 2.3 让游戏加载它

1. **启动器里勾选 `BlBridge Telemetry`**（`SubModule.xml` 的 `<Name>`，也是启动器排序界面上显示的名字）。
   启动器数据在 `%USERPROFILE%\Documents\Mount and Blade II Bannerlord\Configs\LauncherData.xml`（`UserModData/Id/IsSelected`）。
2. **通过 BLSE 启动游戏**：`bin\Win64_Shipping_Client\Bannerlord.BLSE.Standalone.exe`。
3. 若用命令行起（无人值守），**模块列表不能省**：`Bannerlord.BLSE.Standalone.exe` 后面必须带
   `_MODULES_*...*_MODULES_`，否则会以"无 mod"模式启动 —— `SubModule.xml` 照样被扫，但**没有任何社区模块被激活**，BlBridge 永远不会加载。
   现成的列表在 `mcp\bl_launch.ps1` 的 `$mods` 变量里（**保持顺序**，Harmony/ButterLib/UIExtenderEx/MCM 必须在前）。

### 2.4 判定"真的加载了"（唯一硬判据）

```powershell
python "<游戏根>\Modules\BlBridge\mcp\bl_cmd.py" status
python "<游戏根>\Modules\BlBridge\mcp\bl_cmd.py" buildcheck
```

| 看什么 | 期望 |
|---|---|
| `bridge_status.json` 的 `state` | `loaded` |
| `build.loadedSha256` | **等于**你刚部署的那个 sha256 |
| `bl_build_check` 的 `code` | `ok`（源码 = 构建产物 = 部署文件 = 进程内 DLL） |
| `sessionDiagnosis.verdict` | 通常 `running`；`clean_exit` = 正常退出过；`crashed*` = 崩过 |

状态文件位置：`<我的文档>\Mount and Blade II Bannerlord\BlBridge\bridge_status.json`

**别信 `LAUNCH OK`**：`bl_launch.ps1` 的 `LAUNCH OK` 只代表**窗口出现**，那时游戏还在加载/启动动画里。
`LAUNCH OK` → **不等于** → 模块已加载。

### 2.5 两个高频踩坑

| 症状 | 真因 | 修法 |
|---|---|---|
| `bl_build_check` 报 `game_not_restarted` | 改了代码/DLL，但进程里跑的还是旧的那份 | **完全退出游戏**再启动。重载界面不够 |
| `bl_build_check` 报 `stale_deploy` / `stale_source` | 编译了没部署 / 改了源码没编译 | `build.ps1 -Deploy` 然后重启游戏 |

- `stale_deploy` / `game_not_restarted` 会让 `bl_start_battle` 的 **preflight 直接拒绝**开战（照 Coop 的 "incompatible build fails before an expensive launch"）。
- `stale_source` 只警告并回带在结果里。

### 2.6 回退（三层，都很干净）

| 粒度 | 做法 |
|---|---|
| 只停记录 | 启动器里取消勾选 **BlBridge Telemetry** |
| 完全移除 | 删 `<游戏根>\Modules\BlBridge\` |
| 解绑 MCP | `python register_mcp.py --remove`（备份在 `mcp.json.bak_blbridge`） |
| 恢复配置 | 模板见仓库 `blbridge.example.json`（MCP 侧）/ `blbridge_game.example.json`（游戏侧） |

> 设计原则：**只读游戏状态、不 patch 任何方法、不联网、不改游戏逻辑**。出问题删掉模块即完全回退。

---

## 3. 架构速览（知道这些才看得懂报错）

```
        ┌──────────── AI（你）────────────┐
        │  MCP 工具 (stdio)               │
        └───────────────┬─────────────────┘
                        │ 写请求文件
                        ▼
   <文档>\...\BlBridge\commands\pending\<id>.json
                        │  游戏主线程每帧泵一次（CommandPump）
                        ▼
        ┌──────────── 游戏进程内的 BlBridge.SubModule ─────────┐
        │  UiEntry / ScenarioRunner / TelemetryBehavior        │
        │  TimeControl / EngineProbe / RequestGuard            │
        └───────────────┬──────────────────────────────────────┘
                        │ 写响应 + 遥测
                        ▼
   commands\done\<id>.json      battles\battle_YYYYmmdd_HHMMSS_mmm.jsonl
```

要点：

- **请求有闸门**（`RequestGuard`）：`issuedUtc` 超过 `MaxRequestAgeSeconds`（默认 120 秒）的请求会被回 `request_expired` 并丢弃（防"MCP 超时退出后，游戏后启动把它执行掉"的**幽灵战斗**）；id 必须是 16~32 位十六进制（防路径穿越）；请求文件上限 1 MiB。
- **推进探针**（`EngineProbe` + `ProbePolicy`）：区分"**真的在打**"和"卡在黑屏/加载界面"。读三个真实信号：`Mission.CurrentTime` / 自己的 mission tick 计数 / `MBCommon.IsPaused`。
- **看门狗**（`ScenarioRunner.Watchdog`）：挂在 `OnApplicationTick`，loading 超 120 秒 / running 超过 `durationCap+60` 秒就熔断，落 `state=error` 并**恢复可用**（不会永久卡在 busy）。
- **加速通道**：BlBridge 自己实现，直接写 `Mission.IsFastForward` 并**每帧重申**（10 倍速），不依赖任何第三方 mod。
  ⚠️ RTSCamera 的快进键、原版计分板按钮（只在主角阵亡后可用）、本工具**最终都落在同一个布尔量**上 ⇒ 要用 `bl_fast_forward` 开/关，**别去按它们的键**（按了会被我们下一帧重申回来，看起来"没反应"）。
  ⚠️ **不要走** `Mission.Scene.TimeSpeed`（引擎每帧取 `min(1, 所有请求值)`，写 10 会被打回 1）。
- **配置化**：游戏侧 `<文档>\...\BlBridge\blbridge_game.json`（`enabled` / `sampleIntervalSeconds` / `flushEveryLine` / `maxRequestAgeSeconds`，**模块加载时读一次 ⇒ 改完要重启游戏**）；MCP 侧 `blbridge.json`（`logDir` / `gameDir` / 超时项）。
  坏值**逐项忽略并记录原因**（写进 `bridge_status.json` 的 `config.errors`），不会让整份配置失效。`bl_config` 会告诉你每个值来自 env / 文件 / 默认值。

---

## 4. 第三步：用这些能力干活

每个工具的描述里都写了**前置条件**（要不要游戏在跑、要不要已部署的某版本 DLL、在哪个状态下可用），
**调之前先读描述**，别靠猜。

### 4.0 先记住这条分界线

| 类别 | 需要游戏在跑？ | 工具 |
|---|---|---|
| **纯日志/文件分析** | ❌ 不需要 | `bl_list_battles` `bl_analyze` `bl_read_events` `bl_batch_report` `bl_lookup_troop` `bl_build_check` `bl_config` |
| **控制类** | ✅ 需要 | `bl_status` `bl_start_battle` `bl_open_ui` `bl_order` `bl_get_screen` `bl_get_inventory` … |

控制类工具拿不到状态时，会附上**超时归因**（不是一句干巴巴的"等待超时"）：

```
bridge_not_loaded   没有 bridge_status.json —— 游戏没启动 / 模块未启用
process_exited      进程已退出（等待循环里每秒探测，立刻返回，不傻等）
probe_unknown       探测失败 = 未知，不做判断
no_response         进程在跑但不响应 —— 主线程卡住 / Enabled=false / 未重启的旧模块
```

### 4.1 技能：诊断与环境

| 想干什么 | 工具 |
|---|---|
| 模块在不在、构建一致不一致、会话是不是崩过 | `bl_status` |
| 跑的是不是我改的那份 DLL | `bl_build_check` |
| 配置从哪来、有没有非法项 | `bl_config` |

```
bl_status → 看 build.fileChangedSinceLoad（false 才对）、buildCheck.code、sessionDiagnosis.verdict
bl_build_check → ok / stale_source / stale_deploy / game_not_restarted / game_offline / manifest_missing
```

### 4.2 技能：无人值守启动游戏

```
bl_launch_game
  excludeModules=["<模块名>", ...]   # A/B 对照：同一次启动只差一个模块；名字拼错即启动失败，不静默
```

它会走 BLSE、并自动应答启动期的模态弹窗（Safe Mode → 点「否」；模组变更 → 回车；读档期的"模组不匹配"确认框 → 自动应答）。

**已知边界（务必知道）**：
- 从 agent 自己的会话里直接起进程，宿主会在会话结束时**回收进程树**，游戏 0.3~0.7 秒后就死（引擎日志停在 `Selected graphics adapter`，无异常、无崩溃报告）。
  唯一可靠形态是**让 Windows 任务计划程序去起**（可用代码在 `mcp\bl_launch.ps1` 顶部注释里）。
- `LAUNCH OK` **不等于**模块已加载。落地判据：轮询 `bridge_status.json` 直到 `state=loaded` + 你刚部署的 sha256。

### 4.3 技能：进入游戏内界面（"agent 正门"）

```
bl_list_ui                       # 列出可进入的官方入口 + 官方自定义战斗场景全表（模式/地形/目录是否存在）
bl_list_ui(scenesMode="siege")   # 只看攻城场景
bl_open_ui                       # 默认进入官方自定义战斗界面（uiId=CustomBattle）
bl_open_ui(uiId="<其它官方入口>")
bl_close_ui(state="CustomBattleState")   # 回主菜单
```

**走的是官方 `Module.CurrentModule.ExecuteInitialStateOptionWithId(id)`**，不是抢鼠标
（合成鼠标输入到不了官方界面层，根因见仓库 `docs/prototype-ui-probe-2026-09-25.md`）。引擎那个 API 是**静默失败**的（找不到 id 不报错、无返回值），本工具先判存在，把"找不到"变成显式的 `unknown_ui`。

三条硬约束：

1. **`open_ui` 只在主菜单可用**（否则 `wrong_state_for_ui`）。`InitialStateOption` 的 action 基本都是 `MBGameManager.StartNewGame(...)`，在已加载 Game 的状态下执行它会让状态栈**卡在 `GameLoadingState`**（只能重启游戏）。顺序永远是：`close_ui` → `open_ui`。
2. **进得去就要出得来**：`open_ui uiId=CustomBattle` 之后用 `bl_close_ui state=CustomBattleState` 回主菜单（与官方界面「返回」同一个 `PopState`）。白名单外的 state 回 `bad_state`。
3. **参数名是 `uiId`，不是 `id`**：控制通道的 JSON 读取器是**扁平**的，而请求信封自带 `id`（请求 id）⇒ 用 `id` 传参会被信封那个值顶掉（真机踩过：`unknown_ui: 没有这个入口 id: 85a0d3d3fba54d4e`）。

### 4.4 技能：开一场 AI 对 AI 战斗（核心能力）

**调用纪律**：收到"开一场战斗"类需求时**直接调用**，**禁止**先去列目录 / 读 XML / 查索引确认兵种 id ——
troop id 由引擎侧 `MBObjectManager` 现场 Resolve，前置探索不产生任何新信息。

```
bl_open_ui                                  # ① 先进官方自定义战斗界面（引擎要求当前状态是 CustomBattleState）
bl_start_battle(
    attackerTroop="imperial_legionary",     # 必填（即使给了 Groups）
    defenderTroop="battanian_wildling",     # 必填
    attackerCount=50, defenderCount=50,
    scene="battle_terrain_a",               # 攻城常用 empire_town_c（Ortysia）
    orders="charge",                        # 默认 charge：双方都用 TacticCharge，消除攻守方向偏差
    playerSide="attacker",                  # 实验时必须固定成 attacker（见下方"偏差"）
    skipTroopCheck=true                     # 第三方模组兵种必带
)
bl_wait_for_state(state="ended")            # ② 等打完（10 倍速，20v20 约 20~40 秒）
bl_battle_status                            # ③ 读双方存活 + 战果 + readiness
bl_analyze                                  # ④ 读逐击数据
```

一次会话可反复跑：战斗结束后引擎会回到自定义战斗界面，**直接发下一条 `start_battle` 即可**。

**参数分组速查**：

| 组 | 参数 |
|---|---|
| 基本 | `attackerTroop` `defenderTroop` `attackerCount` `defenderCount` `scene` `durationCapSec` |
| 实验对称化 | `orders`（charge / default）`playerSide`（attacker / defender）`randomSeed` |
| 多兵种/战术组 | `attackerGroups` / `defenderGroups`，语法 `troop:count[:formation[:movement]]`，多组用 `\|` 分隔。给了它则 `attackerTroop/Count` 被忽略，且与 `orders` 互斥 |
| 靶场 | `dummySide` `freezeDummies` `unlimitedAmmo` `dummyArmor` `dummyBodyItem` |
| 多轮 | `rounds` `roundEndAlive` `roundSwap` `roundSpawnAttacker` `roundSpawnDefender` |
| 观战镜头 | `spectate`（兜底）`rtsPreset`（装了 RTSCamera 时优先用它：`siege-god` / `free-always` / `elevated-always` / `god-full`） |
| 校验 | `skipTroopCheck` `allowAnyState` |

**两个必知事实**：

1. **镜像局一边倒的真凶是 `playerSide` 标记**，不是攻守方向。被标记为玩家侧的一方在镜像局里 5/5 全胜且全歼（指纹：玩家侧格挡 72.6% vs 敌方 58.5%）。
   ⇒ **A/B 协议**：固定 `playerSide=attacker` + 兵种换边双跑 + 每配置 ≥3 局取均值 + 只认聚合统计。
2. **布尔参数一律发字符串** `"true"` / `"false"`（C# 侧走扁平 JSON 读取）；`dummyArmor*` 走数字。
   ⚠️ **参数名不能叫 `id`**（同 §4.3 的扁平读取器碰撞）。

### 4.5 技能：战斗中途改令 / 接管士兵

```
bl_order(side="player", formation="Cavalry", movement="charge")
bl_order(side="attacker", formation="Infantry", position="100,0,200")   # 引擎按导航网格算落点
bl_order(side="attacker", formation="Ranged", target="Infantry")        # 打敌方某个编队
bl_order(side="attacker", formation="Cavalry", targetAgent=1234)        # 打某个敌方单位（Agent.Index）
bl_order(side="player", formation="Infantry", arrangement="shieldwall") # 阵列
bl_order(side="player", formation="Ranged", firing="holdFire")          # 射击纪律
bl_order(side="player", formation="Cavalry", riding="dismount")         # 上下马（与 movement 正交）
```

- `side` = `player` / `attacker` / `defender`；`formation` = 编队名或 `0~4`，不传 = 该方所有有兵的编队。
- **`movement` / `position` / `target` / `targetAgent` 四者互斥**（至少给一个）；`arrangement` / `firing` / `riding` 可与前者并存。
- **判据是当场回读**，不是"我们调了 API"：返回带 `orderBefore` / `orderAfter`（引擎 `MovementOrder.OrderEnum`）。
  `position` 另回 `moveTarget` + `formationCenter`；`target` 另回 `targetAfter` + `targetDistance`；`riding` 另回 `ridingBefore/After`。
  **行为判据**：隔几秒再调一次，编队重心应朝目标点挪 / 距离应缩小。
- ⚠️ `emptyFormations > 0` = **令写进去了但那个编队一个人都没有**（编队按兵种自动分：弓手在 Ranged、近战步兵在 Infantry）⇒ 先核对 `formation`。
- ⚠️ 只在 mission 内有意义。mission 之外碰 `MovementOrder` 会抛 `TypeInitializationException` 并把该类型**永久**标记为不可用 ⇒ 没有战斗时直接拒 `no_mission`。

```
bl_control_agent(mode="take", agentIndex=123)    # 接管某个友方士兵
bl_control_agent(mode="status")
bl_control_agent(mode="release")
```

⚠️ **真机边界**：AI 对 AI（无真人）的场次里主角色能换，但 `Controller` 回读仍是 `AI` ⇒ 工具**如实报** `ok=false` / `controller_not_verified`，**不假装成功**。要真接管需要**真人场次**。只允许玩家方。

### 4.6 技能：观战镜头 / 加速 / 界面读取

| 工具 | 说明 |
|---|---|
| `bl_ghost_camera(mode="on")` | 幽灵/自由相机：设引擎自带 `MissionScreen.IsCheatGhostMode`，走引擎的 Free 观察镜头，**命令 UI 开着也保持自由**（能观战 + 能下令）。运行时即可切，对任何一场战斗有效 |
| `bl_camera_speed(mode="boost")` | 相机移动速度。三条腿：`shift`（官方控制台函数，**不需作弊模式**，按住 Shift 生效）/ `base`（反射，引擎把速度 clamp 在 ±20）/ `rts`（RTSCamera 的 `MovementSpeedFactor`，最有效）。每条腿**写完回读**，失败点名是哪条腿 |
| `bl_fast_forward(enabled="true")` | 10 倍速，**对自己手打的战斗也生效** |
| `bl_skip_video` | 跳过开场动画：判当前活动状态是不是 `VideoPlaybackState`，是就直接调 `OnVideoFinished()` —— **不模拟 ESC** |
| `bl_cheat_mode(mode="on")` | 开关作弊模式（写 `NativeConfig.CheatMode`，带回读）。开了它，引擎自由相机的 `Ctrl+↑/↓` 倍率热键与观察者 HUD 的速度读数才可用。⚠️ 等于打开开发者通道，工具**不做任何自动开启**，只作用于本次进程 |

```
bl_get_screen(layerFilter="...")                 # 只读读当前界面：层 / 影片 / 可点按钮（文本+可用+状态+id）
bl_get_viewmodel_property(propertyName="PlayerGold", layerName="...")
bl_get_viewmodel_property(propertyName="Smelting.SmeltableItemList", subProperties=["Name","Count"])
```

- ⚠️ **按钮优先用 text 定位**：id 常空且不唯一（真机：主菜单 11 个按钮只有 1 个有 id；自定义战斗界面 8 个都叫 `AddTroopButton`）。
- 属性不存在 = `ok:false` / `property_not_found` **并列出真实可用属性名**；层名写错 = `no_data_source`。
- 这两个工具属于 **v0.8.34+** 才有的 method ⇒ 前置条件是**已部署含该版本 DLL 且游戏已重启**。

### 4.7 技能：读战役数据 / 存档

| 工具 | 前置 | 说明 |
|---|---|---|
| `bl_get_inventory` | **战役内** | 主队伍 `ItemRoster` + 主英雄金币。`limit` 默认 50。主菜单/自定义战斗里如实报 `no_campaign` |
| `bl_list_saves` | 游戏在跑 | 存档列表（meta 含 `Module_*` 模组启停键值） |
| `bl_load_save(name=...)` | **主菜单** | 按名直载，**不经过存档选择界面** —— 无人值守换档的正门。名字不存在 = `save_not_found` 并列出可用档。读档期的"模组不匹配"确认框会被自动应答 |
| `bl_campaign_time(mode="status")` | 战役内 | 只读诊断：`timeControlMode` / `campaignDays` / `pauseMenuOpen`。`campaignDays` 前后各读一次才证明"时间真的在走" |

> `pauseMenuOpen` = 地图上的暂停菜单（ESC 菜单）是否开着。原版失焦 + `BannerlordConfig.StopGameOnFocusLost=true` 会自动打开它，
> 而它会 `RegisterActiveStateDisableRequest` ⇒ MapState 不再 Tick ⇒ **战役冻结**（但档位仍显示 `StopablePlay`，所以只看 `timeControlMode` 看不见）。
> ⚠️ 时间保活（旧的 `mode=on/off`）已在 v0.8.39 移除，传 `on/off` 会显式报 `keep_awake_removed`。

### 4.8 技能：改配置

| 工具 | 作用 | 生效时机 |
|---|---|---|
| `bl_read_config` / `bl_apply_config` | 读/改 **Warbandlord** 配置（自动备份 + XML 校验） | 改完需重启游戏 |
| `bl_rts_config` / `bl_apply_rts_config` | 读/改 **RTSCamera** 配置；支持预设 `siege-god` / `free-always` / `elevated-always` / `god-full` | 实测改完**下一场就生效**（它每场开始读一次）；但**退出时会用内存值覆写文件** ⇒ 要"永久"得在游戏内 MCM 改 |

`bl_start_battle` 的 `rtsPreset` 参数就是"开战前套一次 `bl_apply_rts_config`"，改完无需重启即对本场生效，会先备份。

### 4.9 技能：跑批与 A/B 报告

```
bl_run_batch(plan=..., dryRun=true)     # 先只回计划，不碰游戏
bl_run_batch(plan=...)                  # 按 plan 跑 N 场（支持换边双跑、多轮、靶场参数）
bl_batch_report(...)                    # A/B 报告：主指标=满编窗口，强制 95%CI，样本<3 局拒绝下结论
```

Plan 示例在仓库 `tools\plan.*.example.json`（换边双跑 / 靶子护甲对照 / 材质对照 / 换装探针 / 攻守互换 / 镜像双跑 / 多兵种战术组）。
`plan.example.json` 与 `plan.armor.example.json` 是最小起点。

### 4.10 技能：只读分析（游戏不用开）

```powershell
python "<游戏根>\Modules\BlBridge\mcp\bl_cmd.py" status                 # 状态
python "<游戏根>\Modules\BlBridge\mcp\bl_analyze.py"                    # 分析最近一场
python "<游戏根>\Modules\BlBridge\mcp\bl_analyze.py" <battle.jsonl>     # 分析指定一场
python "<游戏根>\Modules\BlBridge\mcp\bl_dummy_analyze.py" --compare ...  # 靶场跨档对比（按部位给 Δ% + Welch t）
python "<游戏根>\Modules\BlBridge\mcp\bl_compare.py" --manifest runs.json
python "<游戏根>\Modules\BlBridge\mcp\bl_troop_sweep.py" probe          # 全兵种可用性判定（不建 mission）
```

**血量模型的第一张判据表**：「致死总伤害均值」应 ≈「最大血量中位」（±3% 内）。这是对伤害结算模型的直接检验。

---

## 5. 读战斗日志：JSONL 事件格式（schema 1）

每场一个文件：`battles\battle_YYYYmmdd_HHMMSS_mmm.jsonl`。每行一个 JSON 事件，都带 `t`（类型）与 `time`（秒）。

| `t` | 关键字段 |
|---|---|
| `meta` | schema / mod / version / startedUtc / file / `mission`（`bridge`=BlBridge 自建靶场 / `game`=其它）/ `randomSeed` / `round` |
| `unit` | agent, side, troop, level, isHero, isMounted, maxHp, **formation**（实际编队名） |
| `hit` | attacker, defender, aTroop, dTroop, weaponClass, isMissile, bodyPart, `dmg`, **`damagedHp`**（引擎直给的实际扣血）, absorbedByArmor, blocked, hpAfter, hpMax, shieldHp, `bodyPartName`, `shieldSlot`, `shieldItem` … |
| `shot` | shooter, side, troop, weaponSlot/Name, weaponClass, px/py/pz, vx/vy/vz, speed |
| `state` | 每 2 秒 × agent：位置/速度/`speed`/`maxSpeed`/`combatSpeed`/护甲/士气/`aiState`/装弹/弹药 |
| `ai` | 每 agent 一条：30 个 AI 与精度参数 + `armorHead/Torso/Legs/Arms` + `topSpeedReach` |
| `kill` | victim, killer, vSide, dmg, damageType, bodyPart, isMissile, weaponClass |
| `flee` / `panic` | agent, side, troop |
| `sample` | 每 10 秒：aAlive, dAlive, aHp, dHp |
| `squad` | 多兵种战术组，每组一行（**仅当给了 Groups 才出现**）：side, group, troop, count, formation, movement, spawned, source |
| `end` | aAlive, dAlive, aInitial, dInitial, hits, kills, flees, ioFailed, **nanCount**, **`validity`** |
| `dummy_*` | 靶场专用：dummySide, freeze, armor, applied, blocked, hpAfter, restored, leakedDeaths, hpMismatch |
| `round_*` | 多轮连续实验：round / rounds / cleanedUp |

**四条分析纪律**：

1. **`time` 的时钟原点 = 该文件所属那一轮的起点**（每轮独立文件）。需要整场耗时请跨文件求和或用 `meta.startedUtc`。
2. ⚠️ **`round_cleanup` 与下一条 `round_start` 之间的阵亡是"补刀死"**（不是自然战死），分析时必须排除。
3. ⚠️ **`speed` 与 `maxSpeed` 量纲不同**：`speed` 是世界单位速度（m/s），`maxSpeed` 是**倍率**（基准 1.0）。**不可直接比较**。
   引擎不通过公开 API 暴露世界单位速度上限 ⇒ "上限是否生效"只能用统计口径间接判断。
4. ⚠️ **旧日志的已知缺陷**（分析 v0.8.9 及更早的多轮日志时注意）：
   - v0.8.5–v0.8.8：`round_*` 三条事件的 `time` 用整场累计值，与同文件其它事件**不同源**。
   - v0.8.5–v0.8.9：`probe` 同样用整场累计值，**第 2 轮起**与同文件其它事件不同源（第 1 轮看不出来）。
   - 校验命令：`python mcp\bl_check_clock_reset.py`。

---

## 6. 常见失败与归因（照着查，别猜）

| 症状 | 归因 | 修法 |
|---|---|---|
| 工具列不出来 | `BLBRIDGE_TOOLSET` 过滤掉了 / MCP 没重载 | 改 env 或重载 IDE 窗口 |
| `bridge_not_loaded` | 游戏没启动 / 模块没勾选 / SubModule 没加载 | 查启动器 `IsSelected`、重跑 `bl_status` |
| `no_response` | 进程在跑但主线程卡住 / `Enabled=false` / 进程里是旧模块 | `bl_status.sessionDiagnosis`；必要时重启游戏 |
| `request_expired` | 请求超过 `MaxRequestAgeSeconds`（默认 120 秒） | 正常保护，重发即可 |
| `unknown_troop` | 兵种 id 不存在 / 当前还没加载自定义战斗数据 | 主菜单不算已加载；第三方模组兵种带 `skipTroopCheck=true` |
| `unknown_scene` | 场景名不存在（返回可用野战场景清单） | 用 `bl_list_ui` 吐的场景表核对。**场景名写错会让引擎原生崩溃**，所以这里是显式拒绝 |
| `unknown_ui` | `uiId` 不存在（附可用 id 清单） | 用 `bl_list_ui` 核对 |
| `wrong_state_for_ui` | 不在主菜单就调 `open_ui` | 先 `bl_close_ui` 回主菜单 |
| `bad_state` | `close_ui` 的 state 不在白名单（只有 `CustomBattleState`） | 用官方界面的返回，或核对 state 名 |
| `no_mission` | mission 之外调 `bl_order` / `bl_control_agent` | 只在战斗进行中调 |
| `no_campaign` | 不在战役里调 `bl_get_inventory` | 先 `bl_load_save` 进战役 |
| `in_campaign` | 战役里调 `bl_load_save` | 先回主菜单 |
| `keep_awake_removed` | 传了 `bl_campaign_time(mode="on"/"off")` | 该模式已移除，只用 `status` |
| `unsupported_param` | `bl_control_agent` 传了 `agentId`/`slot`/`mount`/`weapon` | 这些未实现 |
| 开战被拒 | `bl_build_check` 不是 ok | `build.ps1 -Deploy` + **完全重启游戏** |
| 日志目录空 | 找错地方了 | 在 `<我的文档>\Mount and Blade II Bannerlord\BlBridge\battles\`，不在 mod 目录 |

---

## 7. 最短上手闭环（复制粘贴级）

```powershell
# ① 登记 MCP（一次）
python "<游戏根>\Modules\BlBridge\mcp\register_mcp.py"

# ② 重载 IDE 窗口，然后：
#    调 bl_status           → state=loaded
#    调 bl_build_check      → code=ok

# ③ 进界面 → 开战 → 等结束 → 读数据
#    调 bl_open_ui
#    调 bl_start_battle(attackerTroop="imperial_legionary", defenderTroop="battanian_wildling",
#                       attackerCount=50, defenderCount=50, playerSide="attacker")
#    调 bl_wait_for_state(state="ended")
#    调 bl_battle_status
#    调 bl_analyze
```

命令行等价物（不依赖 MCP，用于自查）：

```powershell
cd "<游戏根>\Modules\BlBridge\mcp"
python bl_cmd.py buildcheck
python bl_cmd.py open-ui
python bl_cmd.py start --attacker imperial_legionary --defender battanian_wildling --a 20 --d 20
python bl_cmd.py wait --state ended --timeout 300
python bl_cmd.py status
```
