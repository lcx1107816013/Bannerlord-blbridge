# GABP 命名对齐（L1）

> 建立日期：2026-09-27　唯一真相源：`tools/gabp_names.json`　校验：`python tools/bl_check_gabp_names.py`
> 上游：[pardeike/GABP](https://github.com/pardeike/GABP)（协议）、[BUTR/Bannerlord.GABS](https://github.com/BUTR/Bannerlord.GABS)（同宿主的参考实现，MIT）

---

## 一、这个文档**不是**什么

**它不是一次传输层迁移。** BlBridge 的控制通道仍然是**文件 IPC**（`commands/pending/` ⇄ `commands/done/`），一个字节都不改。

它只回答一个问题：**如果**这些能力将来要以 GABP 工具的形式暴露出去（给 GABS 当 MCP 前端、或给别的 agent 客户端用），它们该叫什么名字。

这么做的理由是：命名是**将来接生态时唯一无法事后补的东西** —— 名字一旦对外用过就改不动了，而现在定下来是零成本（不改一行代码）。传输、依赖、线程模型这些都能以后再谈。

依据与取舍分析见 `GABP-GABS-Lib.GAB-适配评估-2026-09-27.md`（结论：**抄进来，不转换形态**）。

---

## 二、命名规则（5 条）

1. **形状固定为 `<category>/<snake_case>`**，全小写，只用 `[a-z0-9_]`。category 取自下面第三节的词表，**不得新造而不登记**。
2. **前缀不得落在 GABP 协议自留的名字里**：`session` / `tools` / `events` / `resources` / `attention` / `notifications` / `lifecycle` —— 这些是协议方法本身的命名空间（`session/hello`、`tools/list`、`events/subscribe`…），用了会撞。
3. **单一用途的动作 method 用动词**（`battle/start`、`ui/open`）；**同时承载读与写的 method 用名词短语**，读写靠参数区分（`camera/speed`、`camera/ghost_mode`、`core/cheat_mode`、`core/skip_video`、`desktop/press_key`）。理由：我们的 method 表里有 5 个是 `mode` 参数分档的，硬拆成 `get_*` / `set_*` 两个名字会与实际的 method 表对不上（一个 method 承载两向），不如用名词短语如实描述。
4. **读与写能分开的必须分开**：`battle/get_state` vs `battle/start`；`config/read_warbandlord` vs `config/write_warbandlord`。**不要**用一个 `battle/state` 同时承担读写 —— 那让"这个调用会不会改变游戏"变成要靠参数判断。
5. **动词尽量复用上游词表**：`ping` / `get_state` / `wait_for_state` / `start` / `abort` / `list` / `open` / `close`。上游已有的概念（`core/ping`、`core/skip_video`）**直接用同名**，不另起。

### 一条容易忽略的约定（抄自上游）

上游 Bannerlord.GABS 对"动作 / 等待器"的分工是**成对**的：动作类工具**立即返回**，游戏在后台跑，配套一个**阻塞式等待器**轮询到状态落定 —— 调用方**永远不需要 sleep / delay / 手写轮询**（它的 AGENTS.md 把这条写成硬约束）。

我们的 `bl_wait_for_state` 已经是这个形状，所以命名沿用同一个词 `wait_for_state`（上游有 `core/wait_for_state` / `party/wait_for_arrival` / `conversation/wait_for_state`）。**新增任何异步动作时，必须同时给出它的等待器**，命名 `<同一 category>/wait_for_<什么>`。

---

## 三、映射表（**由脚本生成，勿手改**）

重新生成：

```powershell
python tools\bl_check_gabp_names.py --markdown
```

<!-- BEGIN GENERATED -->
### 控制通道 method → GABP 名（共 15 条）

| GABP 名 | method | 读/写 | 前置 | 说明 |
|---|---|---|---|---|
| `battle/abort` | `abort` | write | any |  |
| `battle/control_agent` | `control_agent` | write | mission | mode = take/release/status；AI 场次里 controller 回读仍是 AI，工具如实报 ok=false |
| `battle/get_speed` | `speed` | read | any | 诊断读数：isFastForward / sceneTimeSpeed / missionMode |
| `battle/get_state` | `status` | read | any | 回的是推演状态机 + 双方存活数 + 战果 |
| `battle/order` | `order` | write | mission | movement / position / target / targetAgent 四者互斥 + arrangement / firing / riding |
| `battle/set_speed` | `fast_forward` | write | mission | 布尔开关（10 倍速）。与上游 core/set_time_speed 不同：那是战役时间，这是 mission 内快进 |
| `battle/start` | `start_battle` | write | CustomBattleState | 必须在官方自定义战斗界面；参数校验（兵种 / 场景 / 组 DSL）在开 mission 之前做 |
| `camera/ghost_mode` | `ghost_camera` | write | mission | mode = status/on/off/toggle ⇒ 名词短语命名，读写靠参数 |
| `camera/speed` | `camera_speed` | write | mission | mode = status/shift/base/rts/boost ⇒ 名词短语命名；三条腿各自写完回读 |
| `core/cheat_mode` | `cheat_mode` | write | any | 开关 + 回读；等于打开开发者通道，工具不做任何自动开启 |
| `core/ping` | `ping` | read | any | 与上游 Bannerlord.GABS 的 core/ping 同名同义，直接对齐 |
| `core/skip_video` | `skip_video` | write | any | 做法抄自上游 Bannerlord.GABS 的 core/skip_video（脱壳抄：不引 Lib.GAB） |
| `ui/close` | `close_ui` | write | CustomBattleState | 白名单只含官方自定义战斗界面 |
| `ui/list` | `list_ui` | read | any |  |
| `ui/open` | `open_ui` | write | main_menu | 走官方 ExecuteInitialStateOptionWithId；只在主菜单可用（有闸门） |

### MCP 工具 → GABP 名（共 32 条）

| GABP 名 | MCP 工具 | 说明 |
|---|---|---|
| `battle/abort` | `bl_abort` |  |
| `battle/control_agent` | `bl_control_agent` |  |
| `battle/get_state` | `bl_battle_status` |  |
| `battle/order` | `bl_order` |  |
| `battle/set_speed` | `bl_fast_forward` |  |
| `battle/start` | `bl_start_battle` |  |
| `battle/wait_for_state` | `bl_wait_for_state` | 上游同名词（core/wait_for_state / party/wait_for_arrival / conversation/wait_for_state）的同一约定：动作工具立即返回，等待器阻塞 |
| `bridge/check_build` | `bl_build_check` | 源码 / 构建产物 / 部署文件 / 进程内 DLL 四段哈希链 |
| `bridge/get_config` | `bl_config` | 有效配置 + 来历（env / 文件 / 默认） |
| `bridge/get_status` | `bl_status` | 模块 / 日志 / 会话身份，含崩溃判定 |
| `camera/ghost_mode` | `bl_ghost_camera` |  |
| `camera/speed` | `bl_camera_speed` |  |
| `config/read_rts` | `bl_rts_config` |  |
| `config/read_warbandlord` | `bl_read_config` |  |
| `config/write_rts` | `bl_apply_rts_config` | 含预设 siege-god / free-always / elevated-always / god-full |
| `config/write_warbandlord` | `bl_apply_config` | 自动备份 + XML 校验；改完需重启游戏才生效 |
| `core/cheat_mode` | `bl_cheat_mode` |  |
| `core/launch_game` | `bl_launch_game` | BLSE 无人值守启动 + 模态弹窗应答；excludeModules 是 A/B 对照的支点 |
| `core/skip_video` | `bl_skip_video` |  |
| `desktop/click` | `bl_desktop_click` |  |
| `desktop/list_windows` | `bl_desktop_windows` |  |
| `desktop/press_key` | `bl_desktop_key` | keys 走组合键、text 走文本输入，一个工具两用 ⇒ 名词短语 + 参数区分 |
| `desktop/screenshot` | `bl_desktop_screenshot` |  |
| `experiment/compare` | `bl_batch_report` | 本项目特有：A/B 对比，强制 95%CI，样本 < 3 局拒绝下结论 |
| `experiment/run_batch` | `bl_run_batch` | 本项目特有：按计划跑 N 场（支持换边双跑、dryRun） |
| `telemetry/analyze_battle` | `bl_analyze` | 血量校验 / 伤害分布 / 挨打成本 |
| `telemetry/list_battles` | `bl_list_battles` |  |
| `telemetry/read_events` | `bl_read_events` | 按类型过滤 + 分页 |
| `troop/lookup` | `bl_lookup_troop` |  |
| `ui/close` | `bl_close_ui` |  |
| `ui/list` | `bl_list_ui` |  |
| `ui/open` | `bl_open_ui` |  |

### category 词表（共 10 个）

| category | 含义 |
|---|---|
| `battle` | 战斗内动作与 AI 推演控制 |
| `bridge` | BlBridge 自己的基建（会话状态 / 构建身份 / 有效配置），不碰游戏 |
| `camera` | 相机与观战 |
| `config` | 读写模组配置文件 |
| `core` | 与上游同义：游戏进程生命周期与通用开关 |
| `desktop` | 桌面级窗口 / 输入自动化 |
| `experiment` | 跑批与 A/B 对比（本项目特有，上游没有对应物） |
| `telemetry` | 读我们自己的产物（战斗日志与逐击事件） |
| `troop` | 兵种查询（本项目特有） |
| `ui` | 进入 / 退出官方游戏界面 |

### 跨表同名（信息项，预期行为）

同一个能力在 MCP 侧与 GABP 侧共用一个名字，共 13 个：

`battle/abort` `battle/control_agent` `battle/get_state` `battle/order` `battle/set_speed` `battle/start` `camera/ghost_mode` `camera/speed` `core/cheat_mode` `core/skip_video` `ui/close` `ui/list` `ui/open`

<!-- END GENERATED -->

---

## 四、这条不变量怎么保证不过期

命名表最容易的失效方式**不是写错，而是悄悄过期**：我们加了第 16 个控制通道 method、或第 33 个 MCP 工具，表里没跟上，**谁也不会发现**。所以它必须可执行：

```powershell
python tools\bl_check_gabp_names.py            # 退出码 0 = 通过 / 1 = 有 FAIL / 2 = 环境或抽取失败
python tools\bl_check_gabp_names.py --selftest # 注入 8 类故障，必须全抓到
```

三个比对源、7 条判据：

| 源 | 谁是真相 |
|---|---|
| 控制通道 method | `src/CommandPump.cs` 的 `Dispatch` 里 `method == "..."` 分支（**源码**即真相） |
| MCP 工具 | `tools/bl_mcp.py` 的 `TOOLS = [...]` 表（**表**即真相） |
| GABP 命名表 | `tools/gabp_names.json`（本脚本校验它的覆盖度与内部合法性） |

| 判据 | 判什么 | 哪种输入会红 |
|---|---|---|
| **C1** 覆盖（双向） | 源码里的 method / 工具集与表**互为子集** | 加了一个 method 忘了登记；或表里留着一个已删的 method。**两个方向都查** —— 只查一个方向的话，"表里每条都有出处"会掩盖"源码里多了个没人命名的 method" |
| **C2** 同表内唯一 | `methods` 内部、`tools` 内部各自不重名（跨表重复是**预期**的，见上表） | 两条 method 都叫 `core/ping` |
| **C3** 形状 | `^[a-z][a-z0-9_]*/[a-z][a-z0-9_]*$` | `core/Ping`、`core.ping`、`ping` |
| **C4** category 已声明 | 前缀必须在本文件第三节的词表里 | `nowhere/ping` |
| **C5** 不撞协议自留前缀 | 见规则 2 | `tools/ping`、`events/subscribe` |
| **C6** `kind` / `needs` 合法 | `kind ∈ {read, write}`；`needs ∈ {any, mission, main_menu, CustomBattleState}` | 写了个新前置条件（如 `campaign`）却没在脚本的 `NEEDS_ALLOWED` 登记 —— **这是故意的**：逼着"想清楚它在哪个状态下可用"成为一次显式动作 |
| **C7** 信息项 | 跨表同名清单 | 不判失败，只报告 |

### 关于 `--selftest`（为什么它不是摆设）

`[OK] 7 条判据全过` 单独拿出来**证明不了任何事** —— 一个恒返回 OK 的校验器、一个正则写错把所有条目跳过的校验器，都会得到这个结果。所以：

- `--selftest` 在内存里**注入 8 类故障**（缺 method / 多 method / 多 tool / 名字重复 / category 未声明 / 撞自留前缀 / 形状非法 / `needs` 非法），逐条断言被抓到；
- 同时有**两个对照组**：① 未注入时 0 报错；② **跨表同名非空** —— 第二条是专门治"信息段静默失真"的。

> 对照组②不是凭空的：本脚本首版的跨表同名用错了集合（拿 `{gabp 名: 条目键}` 的 `.values()` 求交，等于拿 `method 名` 与 `bl_* 工具名` 求交），**恒为空**却毫无报错，报告永远打印"跨表同名 0 个"。同一个东西还在 `--markdown` 里另算了一遍（两处实现）。现在交叉链接只在 `check()` 里算一次，并被对照组②钉住。

---

## 五、抄录记账（MIT 署名）

| 来源 | 取用什么 | 取用日期 |
|---|---|---|
| [pardeike/GABP](https://github.com/pardeike/GABP) | `<category>/<verb>` 命名形状、`gabp/1` 的方法词表（`ping` / `get_state` / `wait_for_state` / `list` / `open`）、协议自留命名空间清单 | 2026-09-27 |
| [BUTR/Bannerlord.GABS](https://github.com/BUTR/Bannerlord.GABS)（MIT） | 同宿主（net472 骑砍模组）的分类词表（`core` / `battle` / `ui` …）、`core/ping` 与 `core/skip_video` 的同名对齐、动作/等待器成对的约定 | 2026-09-27 |

**没有引入任何依赖**：本文件与 `tools/gabp_names.json` + `tools/bl_check_gabp_names.py` 都是纯文本与纯 Python 标准库。
