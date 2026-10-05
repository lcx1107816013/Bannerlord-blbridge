# MCP 工具「描述 / 诊断输出」误导性审计（2026-09-27，v0.8.34）

> 起因：用户让 AI 换用**新的 MCP 调用方式**（先看 `tools/list` → 取该工具的描述 → 真的 call 一次），
> 顺带检验"这套方式会不会把 AI 带偏"，发现不准就改描述。
> 结论：**当场抓到 2 个会误导调用方的问题**（都不是描述文字本身的问题，而是"名不副实"）。

---

## 1. 结论（先说）

| # | 现象（AI 视角） | 真实情况 | 处置 |
|---|---|---|---|
| D1 | `bl_get_screen` / `bl_get_viewmodel_property` 在 `tools/list` 里有名有描述，一调就 `unknown tool` | 只加了 `TOOLS` 表与命名表，**漏了 `call_tool()` 的派发分支** | 补派发 + 加防回归（§3） |
| D2 | `bl_status` 报 `verdict: running` / "游戏进程存活（最后状态 exited）" | **pid 被系统回收再用**：pid 6040 现在是 `MSI_Central_Service.exe`，游戏根本没开 | 存活判定改成**验进程身份**（§4） |

两个都是"输出看着正常、实际把人带偏"，而且**当时的离线自测全绿**——
因为它只断言"tools/list 返回 34 个工具"，从不真的 call 一次。这是本轮最该记住的一条。

---

## 2. 是怎么抓到的（可复现）

```
① mcp_get_tool_description(bl_get_screen)   → 有描述，参数清楚
② mcp_call_tool(bl_get_screen, {})          → {"ok": false, "error": "unknown tool: bl_get_screen"}
③ tasklist /FI "PID eq 6040"                → MSI_Central_Service.exe（不是 Bannerlord）
```

`②` 这一步就是新调用方式的价值：**只列不调，永远不会发现 D1。**

---

## 3. D1 的修复与防回归

### 3.1 修复

`tools/bl_mcp.py` 的 `call_tool()` 补两条分支（紧跟 `bl_close_ui`，与 `TOOLS` 表相邻）：

- `bl_get_screen` → `send_command("get_screen", {layerFilter?})`
- `bl_get_viewmodel_property` → `send_command("get_viewmodel_property", {propertyName, layerName, subProperties?})`，
  `propertyName` / `layerName` 缺失时**本地就拒**（不发空请求去撞 C# 的 `bad_args`）

### 3.2 防回归（三层，逐层都有判据）

1. **新脚本 `tools/bl_check_dispatch.py`**（必跑项已写入 AGENTS.md §三）
   - C1 声明(`TOOLS`) ⇒ 派发(`call_tool`)；C2 派发 ⇒ 声明（抓死分支/改名残留）；C3 声明 ⇒ 分组(`TOOL_GROUPS`)
     （漏分组的后果：设了 `BLBRIDGE_TOOLSET` 的环境里这个工具会**凭空消失**）
   - `--selftest` 注入 3 类故障，逐条断言被抓到；并断言真实源码 0 报错（对照组）
   - 实测：`声明 34 / 派发 34 / 分组 34`，3 类故障全抓到，EXIT=0
2. **`tools/bl_selftest.py` ④**：假游戏端加 `get_screen` / `get_viewmodel_property` 应答，
   真的 `call_tool` 一次并断言参数透传；配**不存在工具名**作对照组（仍须报 `unknown tool`）。
3. **负向实验**（已实跑）：把 `if name == "bl_get_screen":` 改名为 `..._DISABLED` →
   自测立刻变红（2 条 FAIL，EXIT=1），还原后回绿。**证明这条判据不是恒绿。**

### 3.3 规则升格（AGENTS.md）

新增工具从"同步三处"改为**同步四处**：C# `Dispatch` 分支 / `TOOLS` 表 / **`call_tool()` 派发分支** / `gabp_names.json`。

---

## 4. D2 的修复：存活判定必须验身份

`_pid_alive(pid)` 只回答"这个 pid 上有进程吗"。pid 会被系统回收再用，所以新增：

```python
_pid_image_name(pid)            # tasklist 取映像名；取不到 = None（未知）
_pid_is_game(pid)               # True/False/None —— 存在 **且** 映像名命中 bannerlord|mountandblade|taleworlds
```

四处"判断游戏在不在"的调用点（`_diag_no_response` / `run_state_diagnosis` / `build_check` / `send_command` 的等待循环）
全部改为 `_pid_is_game`；`run_state_diagnosis` 另加 `pid_probe` 参数供测试注入（不是放宽判据）。

**真机判据（本机实测，非离线）**：

```
_pid_alive(6040)      = True     ← 旧行为：据此报 running（误）
_pid_is_game(6040)    = False    ← 新行为
pid 6040 实际映像名    = MSI_Central_Service.exe
run_state_diagnosis() = {"verdict": "clean_exit", "detail": "进程已退出，且记录了正常卸载（最后状态 exited）",
                         "pidImageName": "MSI_Central_Service.exe", "alive": false}
```

顺带：`running` 分支在状态文件超过 120 s 未更新时追加一句"进程在，可能已不在主循环"（不新增 verdict，
`running` 的消费方按字面判等）。

---

## 5. 描述改法（本轮定的规矩）

- **写清前置条件**：`bl_get_screen` / `bl_get_viewmodel_property` 明确"游戏在跑 **且** 已部署含该 method 的 DLL
  （v0.8.34+；未部署会 `no_response`）"。工具可用 ≠ 能力可用。
- **出处/署名搬出描述**："脱壳抄自 BUTR/Bannerlord.GABS 的 `ui/get_screen`" 这类信息挪到 `docs/`，
  描述留给调用方的判据。
- `bl_status` 描述补上它其实还返回**构建一致性**与**会话诊断**。
- 已写进 AGENTS.md §三「工具描述与诊断输出的硬规则」：描述要让调用方一眼判断"现在能不能用"；
  "有没有"不等于"是不是"；探测不出 ⇒ 返回未知，不冒充结论。

---

## 6. 验收（本轮实跑）

| 项 | 命令 | 结果 |
|---|---|---|
| Python 自测 | `python tools\bl_selftest.py` | **316 项断言全绿**（306 → 316），EXIT=0 |
| 派发一致性 | `python tools\bl_check_dispatch.py` | 34/34/34 全过，EXIT=0 |
| 派发一致性（对照组） | `python tools\bl_check_dispatch.py --selftest` | 3 类故障全抓到，EXIT=0 |
| 命名表一致性 | `python tools\bl_check_gabp_names.py [--selftest]` | 17 method / 34 tool 双向对齐；8 类故障全抓到 |
| 编码体检 | `python tools\check_repo_encoding.py` | 96 个跟踪文本文件全合规（UTF-8 无 BOM + LF） |
| 编译 | `powershell -File build.ps1` | OK → `out\BlBridge.dll`（0.8.34，169 KB，33 源文件） |
| 指标自测 | `python tools\bl_metrics_selftest.py` | 全部通过 |

---

## 7. 真机判据（2026-09-27 20:33–20:47，部署 v0.8.35，进程内 sha `720e350db84b76e4`）

> `build.ps1 -Deploy` → 关游戏 → 重开（pid 40848）→ 主菜单与自定义战斗界面各抓一次。
> `bl_status.buildCheck = 四段一致（源码 = 构建 = 部署 = 进程内，0.8.35）`。

### 7.1 读界面（`bl_get_screen`）

| 场景 | screenType | 层 | dataSource | 按钮 |
|---|---|---|---|---|
| 主菜单 | `GauntletInitialScreen` | `MainMenu` | `InitialMenuVM` | 11 个：载入游戏 / 继续战役 / 新战役 / 沙盒模式 / **自定义战斗** / Mod 选项 / 选项 / 战团式霸主选项 / 制作人员表 / 退出游戏 / (空 id 的 AnnouncementButton，disabled) |
| 自定义战斗界面（`open_ui` 后） | `CustomBattleScreen` | `CustomBattle` | `CustomBattleVM` | 16 个：含 **开始 / 返回 / 随机 / 取消 / 完成 / 切换**，另有 DropdownButton ×2、AddTroopButton ×8 |

`layerFilter="Main"` 生效（只回 `MainMenu` 层）。

### 7.2 真机暴露的第 3 个描述问题：按钮 id 不能当主键

- 主菜单 11 个按钮里 **只有 1 个有 id**（`AnnouncementButton`），其余 10 个 id 是空字符串；
- 自定义战斗界面 16 个按钮里 id **大量重复**（`AddTroopButton` 出现 8 次、`DropdownButton` 2 次）。

⇒ 描述已从"id + 文本"改成"**优先用 text 定位**，id 常空且不唯一"（并写明实测数字）。
这是 Gauntlet 按钮由列表/模板生成、XML 里没写 Id 导致的，不是我们的 bug。

### 7.3 读 ViewModel（`bl_get_viewmodel_property`）

| 用例 | 结果 |
|---|---|
| `CurrentLanguageString` @ MainMenu | `value = "简体中文"`（字符串路径通） |
| `MenuOptions` + `subProperties=NameText,IsDisabled` | `count=10`，10 项（载入游戏…退出游戏），`IsDisabled` 全 `False` |
| 层名写错 `NoSuchLayer` | `ok:false` / `no_data_source`：层 NoSuchLayer 没有可读取的 ViewModel |

### 7.4 真机暴露的第 4 个问题（会误导，已改行为）：属性不存在 = 值为 null

**改前**：`IsMultiplayer`（不存在）与"属性存在但为 null" **都**返回 `ok:true / value:null`
⇒ 调用方分不清"我写错了属性名"和"这个值本来就是空"。

**改后（v0.8.35）**：
- 属性不存在 ⇒ `ok:false` / `property_not_found`，并**反射列出真实可用属性名**：
  `属性 IsMultiplayer 在 InitialMenuVM 上不存在（可用的属性：Announcement, CurrentLanguageString,
  DownloadingText, IsDownloadingContent, IsNavalDLCEnabled, IsProfileSelectionEnabled, MenuOptions,
  ProfileName, SelectProfileText）`
- `value:null` 现在只表示"属性存在、值就是空"。
- 子属性名写错同理：`missingSubProperties: ["NotAField"]`（实测 `subProperties=NameText,NotAField`）。

改法：`TraversePropertyPath` → `TryTraversePropertyPath`（区分"路径走不通"与"值是 null"）+ `SuggestProperties`。
判据是上面的**真机输出**（C# 侧没有离线单测可用 —— `UiInspector` 依赖引擎类型，离线跑不起来）。

### 7.5 pid 身份校验的对照（同一台机器、同一个字段）

| 时刻 | pid | `pidImageName` | `verdict` |
|---|---|---|---|
| 审计前（游戏没开，pid 已被复用） | 6040 | `MSI_Central_Service.exe` | `clean_exit` ✅（改前是 running ❌） |
| 真机（游戏真的在跑） | 40848 | `Bannerlord.BLSE.Standalone.exe` | `running` ✅ |

## 8. 仍待办（诚实留档）

1. 短名单 #2（`inventory` / `get_inventory`）未开工。
2. `UiInspector` 的 C# 行为**只有真机判据，没有离线单测**（它依赖引擎类型，离线跑不起来）
   ⇒ 以后改这块必须重跑 §7 那几步，不能只看 Python 自测全绿。
3. 按钮 id 空/重复是引擎侧形态（列表生成、XML 里没写 Id），**没有**改成"合成一个稳定 id"——
   那会偏离上游 GABS 的行为，需要时再议。
4. 游戏当前**留在主菜单**（pid 40848，v0.8.35 进程内）；本轮为部署 DLL 强制关过一次游戏。
4. `~/.codebuddy/mcp.json` 里 blbridge 的 description 还写着"（20 工具）"（实际 core+config 组 22 个）——
   属用户本机配置，本轮未改（建议改成不写数字，避免再次过期）。
