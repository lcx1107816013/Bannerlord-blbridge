# BlBridge MCP 包（给 AI 的安装说明）

这个目录是 **BlBridge 骑砍2 mod 包的一部分**，位置固定为：

```
<游戏根>\Modules\BlBridge\mcp\
```

它不是给玩家看的，是给 **AI coding agent** 看的：读完这一页，你就能把 BlBridge 的**控制通道**接成 MCP 工具，
从而**既能让玩家用游戏内界面，也能自己驱动游戏**。

> 同一个 mod 包里的两个单元：
> **单元 A** = `Modules\BlBridge\`（DLL + 游戏内面板 + 本地化）——游戏与启动器读它；
> **单元 B** = 本目录（MCP 服务器 + 本说明 + manifest）——AI 读它。
> 两者共用**同一条后端**：面板上的「开始战斗」按钮与 MCP 的 `bl_start_battle` 走同一个入口，
> 所以不存在"界面上能做的、端口做不了"。

## 1. 先看这三件事

| 项 | 值 |
|---|---|
| 运行时要求 | **Python 3.8+**，**只用标准库**（不需要 pip install 任何东西） |
| 控制通道 | **文件 IPC**：`<我的文档>\Mount and Blade II Bannerlord\BlBridge\commands\{pending,done}`（无 HTTP、无端口、无 URL ACL 提权） |
| 什么时候需要游戏在跑 | 控制类工具（`bl_status` / `bl_start_battle` / `bl_open_ui` …）需要；纯日志分析类（`bl_analyze` / `bl_list_battles` / `bl_read_events`）**不需要** |

## 2. 注册到你的 MCP 宿主（三选一）

### 2.1 直接写配置（最通用）

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

CodeBuddy / CodeBuddy CN 的配置在 `%USERPROFILE%\.codebuddy\mcp.json`；
其它宿主见各自的 MCP 配置位置（本包对宿主没有特殊要求，stdio 即可）。

### 2.2 跑自带的注册脚本（幂等，会先备份）

```powershell
python "<游戏根>\Modules\BlBridge\mcp\register_mcp.py"          # 登记 / 更新
python "<游戏根>\Modules\BlBridge\mcp\register_mcp.py" --show    # 只看当前配置
python "<游戏根>\Modules\BlBridge\mcp\register_mcp.py" --remove  # 移除
```

脚本**自定位**：从自己的路径上溯三级得到游戏根目录，因此**不需要你手工填路径**。

### 2.3 `.mcpb` 一键安装（宿主支持时）

`manifest.json` 是 MCPB 规范 0.3 形态的清单。支持 MCPB 的宿主（Claude for macOS/Windows 等）
可以 `mcpb pack` 打成 `.mcpb` 后一键安装；**本包不依赖它** —— 2.1 / 2.2 永远可用。
注意官方限制：agent session 默认**不支持** bundle，所以别把"一键安装"当唯一路径。

## 3. 装完先自查这两条

```powershell
python "<游戏根>\Modules\BlBridge\mcp\register_mcp.py" --show    # ① 配置里能看到 blbridge
python "<游戏根>\Modules\BlBridge\mcp\bl_cmd.py" status          # ② 游戏在跑时能读到桥的状态
```

②返回 `state: idle|loading|running|ended` 就说明**控制通道通了**（不需要打开游戏内任何界面）。

## 4. 两个入口，一条管线

| 你想做的事 | 玩家侧（界面） | AI 侧（本 MCP 包） |
|---|---|---|
| 唤起 BlBridge 面板 | 主菜单点「BlBridge 战场面板」 | `bl_open_ui`（不传 id 即本面板） |
| 进官方自定义战斗选兵界面 | 主菜单点「Custom Battle」 | `bl_open_ui` + `uiId=CustomBattle` |
| 开一场 AI 对 AI 战斗 | 面板上调好参数点「开始战斗」 | `bl_start_battle` |
| 看有哪些入口 / 可用场景 | 面板上的场景轮选 | `bl_list_ui` |
| 离开面板回主菜单 | 面板上的「返回主菜单」 | `bl_close_ui` |
| 离开官方选兵界面（回到主菜单） | 官方界面上的「返回」 | `bl_close_ui` + `state=CustomBattleState` |

推荐的最小闭环（不需要玩家手动点任何东西）：

```powershell
python bl_cmd.py buildcheck                 # 先确认"跑的是不是我以为的那份 DLL"
python bl_cmd.py open-ui                    # 唤起 BlBridge 面板（也可 --id CustomBattle）
python bl_cmd.py start --attacker imperial_legionary --defender battanian_wildling --a 20 --d 20
python bl_cmd.py wait --state ended --timeout 300
python bl_cmd.py status
```

## 5. 已知边界（省得你当成 bug 排查）

1. **入口动作是 fire-and-forget**：`open_ui` 返回 `requested:true` 只代表已触发；面板约需 1~5 秒加载完。
   看 `bl_list_ui` 的 `activeState` 是否变成 `BattleSetupState`（面板）或 `CustomBattleState`（官方界面）。
2. **`open_ui` 的 `uiId` 必须存在**：不存在返回 `unknown_ui` 并附可用 id 清单（引擎侧 `ExecuteInitialStateOptionWithId`
   本身是**静默失败**，我们刻意把它变成显式错误）。
   ⚠️ 参数名是 **`uiId`**，**不是 `id`**：控制通道的 JSON 读取器（`src/Jmini.cs`）是扁平的，
   而请求信封自带 `id`（请求 id）⇒ 用 `id` 传参会被信封那个值顶掉（v0.8.12 真机踩过）。
3. **`start_battle` 的前提是"自定义战斗数据已加载"**：停在官方 `CustomBattleState` 或 BlBridge 面板都算；
   裸主菜单不算（那时所有兵种 id 都会报 `unknown_troop`）。
3b. **`open_ui` 只在主菜单可用**（返回码 `wrong_state_for_ui`）：`InitialStateOption` 的 action 基本都是
   `MBGameManager.StartNewGame(...)`，在已加载 Game 的状态下执行它会让状态栈**卡在 `GameLoadingState`**
   （2026-09-25 真机实测，只能重启游戏）。顺序永远是：`close_ui`（回主菜单）→ `open_ui`。
4b. **进得去就要出得来**：`open_ui uiId=CustomBattle` 之后用 `bl_close_ui state=CustomBattleState` 回主菜单
   （与官方界面的「返回」同一个 `PopState`）；白名单外返回 `bad_state`。
4. **写盘路径**：遥测日志在 `<我的文档>\Mount and Blade II Bannerlord\BlBridge\battles\`，
   **不在 mod 目录下** —— 别在 `Modules\BlBridge` 里找日志。
5. **`tools/` 与本目录同源**：仓库里 `tools\*.py` 是唯一真相源，`build.ps1 -Deploy` 会把它同步到这里；
   手改本目录的文件会在下次部署被覆盖。
