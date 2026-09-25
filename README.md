# BlBridge · 骑砍 2 战斗遥测 + AI 推演桥（版本以 `out/BlBridge.manifest.json` 为准）

把游戏里**每一次命中/阵亡**落成 JSONL，并让外部 Agent **自己开 AI 对 AI 战斗、读战果、改配置** —— 用来**验证**离线平衡模型（血量公式、伤害结算）到底对不对。

**设计原则**：只读游戏状态、不 patch 任何方法、不联网、不改游戏逻辑。出问题删掉模块即完全回退。

---

## 一、两个能力

| 能力 | 说明 | 是否需玩家参与 |
|---|---|---|
| **遥测** | 每场战斗的每次命中/阵亡/溃逃/10 秒快照 → JSONL | 正常打即可（也可由 AI 推演自动产生） |
| **AI 推演** | 由 MCP 触发，游戏自动开一场**无玩家**的战斗、10 倍速跑完、返回战果 | 不需要手打；只需停在自定义战斗界面 |

控制通道 = **本地文件 IPC**（`commands/pending/` ⇄ `commands/done/`），不是 HTTP：
无端口、无 URL ACL 提权、无防火墙问题，且崩溃后请求/响应都留痕。协议 v1 的信封、响应不变式、
会话身份（`runToken` + `processStartedUtc`）、版本硬校验、`outcomeUncertain` 不盲重试等规范，
抄自 Bannerlord Coop 团队的 `CoopMcpServer` / `LiveTestProtocol`（见 `阶段2-复用尽调报告.md`）。

---

## 二、目录

```
BlBridge/
  module/SubModule.xml            模块清单（<Id>=BlBridge，DLLName=BlBridge.dll）
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
  tools/bl_mcp.py                 MCP server（stdio，15 个工具）
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

## 六、MCP 工具（15 个）

| 工具 | 作用 | 需游戏在跑 |
|---|---|---|
| `bl_status` | 模块/日志状态 | 否 |
| `bl_list_battles` | 列出战斗日志 | 否 |
| `bl_analyze` | 分析某场（血量校验/伤害分布/挨打成本） | 否 |
| `bl_read_events` | 读原始事件（按类型过滤、分页） | 否 |
| `bl_read_config` | 回读 Warbandlord 配置 | 否 |
| `bl_apply_config` | 改 Warbandlord 配置（自动备份 + XML 校验） | 否（改完需重启游戏） |
| `bl_battle_status` | 推演状态机 + 双方存活数 + 战果 | 是 |
| `bl_start_battle` | 开一场 AI 对 AI 战斗（支持靶场参数：`dummySide` / `dummyArmor` / `dummyBodyItem` / `freezeDummies` / `unlimitedAmmo`） | 是 |
| `bl_wait_for_state` | 等状态（idle/loading/running/ended/error） | 是 |
| `bl_abort` | 中止当前推演 | 是 |
| `bl_fast_forward` | 开关战斗加速（10 倍速，**对自己手打的战斗也生效**） | 是 |
| `bl_build_check` | 核对 源码/构建产物/部署文件/进程内 DLL 是否一致 | 是 |
| `bl_config` | 显示有效配置与来历（env / 文件 / 默认），并列出配置文件里的非法项 | 是 |
| `bl_run_batch` | **按计划跑 N 场**（阶段 2④「一条命令跑 N 场」；支持换边双跑；`dryRun` 只回计划不碰游戏） | 是 |
| `bl_batch_report` | **A/B 对比报告**（主指标 = 满编窗口；强制 95%CI；样本 < 3 局时拒绝下结论） | 否 |

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
| 游戏端 | `<LogDir>\blbridge_game.json` | `enabled` / `sampleIntervalSeconds`(1~600) / `flushEveryLine` / `maxRequestAgeSeconds`(5~3600) | 模块加载时读一次，**改完要重启游戏** |
| MCP 端 | `BlBridge\blbridge.json` | `logDir` / `gameDir` / `waitForStateTimeoutSec` / `statusTimeoutSec` / `maxRequestAgeSec` | 读一次即缓存，改完不影响运行中 |

模板：`blbridge.example.json`、`blbridge_game.example.json`（每个键都写了范围与来历）。

两条设计原则（照 Coop 的"配置即校验 + 来历可查"）：

1. **加载即校验，坏值不拖累好值**：越界/类型错的项**逐项忽略并记录原因**（写进 `bridge_status.json` 的 `config.errors`），而不是让整份配置失效；
2. **来历可查**：`bl_config` 会说明每个值来自 环境变量 / 配置文件 / 内置默认值，避免"我明明改了但没生效"这种无从定位的状态。
   优先级：**环境变量 > 配置文件 > 默认值**。

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
| `meta` | schema / mod / version / startedUtc / file；**v0.8.3 起**另带 `mission`（`bridge`=BlBridge 自建靶场 / `game`=其它，含玩家在战役沙盒里的实战）；**v0.8.4 起**另带 `randomSeed`（-1 = 未指定）；**v0.8.5 起**另带 `round`（多轮连续实验的轮次） |
| `unit` | agent, side, troop, level, isHero, isMounted, maxHp；**v0.8.8 起**另带 `formation`（该 agent 的**实际**编队名，见下注） |
| `hit` | attacker, defender, aSide, dSide, aTroop, dTroop, weaponClass, isMissile, damageType, bodyPart, **dmg**, magnitude, absorbedByArmor, strikeType, hpAfter, hpMax, mounted；**v0.7.9 起**另带 `blocked`、**`damagedHp`**（引擎直给的实际扣血）、`hitDistance`、`shotDifficulty`、`attackDir`、`attackType`、`speedMod`、`atkStun`、`defStun`、`dmgPct`、`blowFlags`（逗号组合串）、`shieldHp`、`shieldMax`；**v0.8.1 起**另带 `bodyPartName`（部位直名）、`shieldSlot`、`shieldItem`（盾的槽位与物品 id，用于区分"换了盾"与"盾被修复"） |
| `shot` | **v0.7.9 起**：shooter, side, troop, weaponSlot, weaponClass, px/py/pz（位置）, vx/vy/vz（速度向量）, speed；**v0.8.1 起**另带 `weaponSlotName`（槽位直名） |
| `state` | **v0.7.9 起**：每 2 秒 × agent：agent, side, troop, px/py/pz, vx/vy, speed, maxSpeed, combatSpeed, armorEnc, weapEnc, morale, aiState, reloading, reloadPhase, reloadCount, ammo, ammoMax（末 5 项在取不到武器时会缺）。⚠️ **三者的量纲不同，不可直接比较**：`speed` 是 `MovementVelocity.Length`（**世界单位速度 m/s**），而 `maxSpeed` / `combatSpeed` 是 `DrivenProperty.MaxSpeedMultiplier` / `CombatMaxSpeedMultiplier`（**倍率**，基准 1.0）—— 见下方「速度上限」注 |
| `ai` | **v0.7.9 起**：每 agent 一条，30 个 AI / 精度参数（格挡能力、射击频率、瞄准误差、提前量误差…）；**v0.8.0 起**另带 `armorHead` / `armorTorso` / `armorLegs` / `armorArms`（四部位护甲值，用于验证护甲覆盖是否生效）；**v0.8.1 起**另带 `topSpeedReach`（加速到顶速所需时长；引擎不暴露世界单位速度上限，见下注） |
| `kill` | victim, killer, victimTroop, killerTroop, vSide, state, dmg, damageType, bodyPart, isMissile, weaponClass；**v0.8.1 起**另带 `bodyPartName` |
| `flee` / `panic` | agent, side, troop |
| `sample` | 每 10 秒：aAlive, dAlive, aHp, dHp |
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

---

## 八、回退

- 停止记录：启动器取消勾选 **BlBridge Telemetry**
- 完全移除：删除 `...\Modules\BlBridge\`
- MCP 解绑：`python tools\register_mcp.py --remove`
- 配置备份：`~\.codebuddy\mcp.json.bak_blbridge`

---

## 九、已验证 / 未验证

**已本地验证（无需游戏）**
- Roslyn 编译通过：**14 个源文件**（DLL 体积以 `out/BlBridge.manifest.json` 的 `dllBytes` 为准，不再手写数字避免漂移），已部署
- **离线单测 69 项**（`tools/jsontest/build_and_run.ps1`）：JSON 读取器（含"字符串值劫持键查找"）、请求闸门、探针判定规则、构建身份、配置校验
- **Python 自测 52 项**（`tools/bl_selftest.py`）：分析器（血量偏差 0.0%）、MCP 协议（15 工具）、控制通道、
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
