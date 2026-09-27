# Bannerlord blbridge

> 通过 MCP 协议让 AI Agent 远程操控《骑马与砍杀2：霸主》，用于 MOD 的自动化测试。
> English version: see [README_EN.md](./README_EN.md).

## 项目简介

Bannerlord blbridge 把"游戏操控"拆成两端：

- **MOD 端（游戏内）**：少量 Lua 脚本，随游戏 tick 轮询本地 `cmd.txt`，解析指令后调用游戏 API，再把结果写入 `state.json`。
- **外置 MCP Server（Python）**：一个 stdio 型 MCP 服务，负责 MCP 协议解析，并在 Agent 与游戏 MOD 之间做文件桥接（写 `cmd.txt` / 读 `state.json`）。

两端纯靠本地文本文件通信，**MOD 端不内置任何 MCP 协议**，因此 MOD 体积最小、最稳定。

**它能做什么**
- 让 AI Agent（如 CodeBuddy）通过标准 MCP 工具，在游戏中生成部队、读取英雄属性等。
- 把重复的手工测试变成可编排的自动化脚本，提升 MOD 开发效率。
- 提供清晰的文件通信协议，便于二次开发与调试。

## 架构原理图

```
┌──────────┐  MCP(JSON-RPC,stdio)  ┌────────────────┐   读写文件   ┌──────────────┐  文件轮询  ┌──────────────┐
│ AI Agent │ ───────────────────▶ │ Python MCP    │ ──────────▶ │ cmd.txt /    │ ─────────▶ │ 游戏内 Lua  │
│          │ ◀─────────────────── │ Server        │ ◀────────── │ state.json   │ ◀──────── │ 桥脚本      │
└──────────┘                      └────────────────┘             └──────────────┘            └──────┬───────┘
                                                                                                  │ 调用
                                                                                                  ▼
                                                                                         ┌──────────────┐
                                                                                         │ 骑砍2 游戏API │
                                                                                         └──────────────┘
```

数据流：Agent → MCP tool call → Python 写 `cmd.txt` → Lua 轮询读到 → 执行 → 写 `state.json` → Python 读 → 返回 Agent。

## 快速部署

1. **获取 MOD 包**：从本仓库 `Actions` 下载 `BannerlordBlbridge.zip`，解压到 Bannerlord 的 `Modules/` 目录（目录名需与 `lua_mod/SubModule.xml` 的 `<Id>` 一致，即 `BannerlordBlbridge`）。
2. **启用 MOD**：启动器勾选 `BannerlordBlbridge` 并启动游戏；确认 MOD 目录下可正常读写 `cmd.txt` 与 `state.json`（路径见 `lua_mod/bridge.lua` 顶部 `DIR`）。
3. **安装 Python 依赖**：`pip install -r mcp_server/requirements.txt`（本服务仅用标准库，该文件可留空）。
4. **配置 MCP 客户端**：在支持 MCP 的客户端（如 CodeBuddy）添加 stdio 服务器，并把 `BL_BRIDGE_DIR` 指向第 2 步的 MOD 目录。
5. **重启客户端**：使 MCP 工具加载，即可在对话中调用 `spawn_troop` / `get_hero_stats` 等工具。

MCP 客户端配置（写入对应客户端的 `settings.json` 的 `mcp.servers`）：

```json
{
  "mcp": {
    "servers": {
      "bannerlord-remote": {
        "command": "python",
        "args": ["mcp_server/server.py"],
        "env": { "BL_BRIDGE_DIR": "C:/路径/到/游戏/Modules/BannerlordBlbridge" }
      }
    }
  }
}
```

> `BL_BRIDGE_DIR` 必须指向 `cmd.txt` / `state.json` 所在目录，且 MOD 端与 Python 端指向同一目录。

## 环境变量说明

| 变量 | 必填 | 说明 |
|------|------|------|
| `BL_BRIDGE_DIR` | 是 | `cmd.txt` / `state.json` 所在目录（MOD 端与 Python 端必须一致）。 |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | 否（仅用 `github-mcp-server` 管理仓库时需要） | 官方 `github-mcp-server` 的鉴权变量；本项目运行本身不依赖它。请通过系统环境变量注入，勿写入任何文件或提交到仓库。 |

## 工具列表（共 57 个）

**本地工具（11 个，Python 直接处理）**：
`ping`、`get_bridge_info`、`list_ops`、`get_protocol`、`read_state`、`wait_for_state`、`clear_state`、`assert_state`、`run_ops`、`send_raw`、`sleep`

**游戏工具（46 个，转发给游戏内 MOD 执行）**，按类别：

| 类别 | 工具 |
|------|------|
| 主角 / 英雄 | `get_player`、`set_player_gold`、`add_player_gold`、`get_player_inventory`、`add_item`、`get_hero_stats`、`list_heroes`、`set_hero_attr`、`heal_hero`、`wound_hero` |
| 部队 | `spawn_troop`、`list_parties`、`get_party`、`add_troops`、`remove_troops`、`disband_party`、`teleport_party`、`merge_parties`、`start_battle`、`auto_resolve_battle`、`get_visible_parties`、`get_map_entities` |
| 定居点 | `list_settlements`、`get_settlement`、`set_settlement_owner` |
| 王国 / 家族 / 外交 | `list_kingdoms`、`list_clans`、`declare_war`、`make_peace`、`get_relations` |
| 世界 / 时间 | `get_campaign_time`、`get_time_scale`、`set_time_scale`、`fast_forward`、`pause_game`、`resume_game`、`get_weather`、`set_weather` |
| 任务 / 日志 / 存档 | `list_quests`、`add_quest`、`complete_quest`、`get_missions`、`get_campaign_log`、`save_game`、`load_game` |
| 测试辅助 | `take_snapshot` |

> 定义在 `mcp_server/server.py` 的 `ENTRIES` 中；新增工具需在 `ENTRIES` 与 `lua_mod/bridge.lua` 的 `OP2FN` 两处同步登记。运行时可用 `list_ops` / `get_protocol` 自省。

## 高级实用技巧

- **调节延迟与稳定**：调整 `lua_mod/bridge.lua` 中 `loop` 的调用频率（轮询间隔），以及在 `mcp_server/server.py` 中调整 `POLL_TIMEOUT`，在响应速度与稳定性之间取舍。
- **扩展工具**：在 `server.py` 的 `ENTRIES` 与 `lua_mod/bridge.lua` 的 `OP2FN` 两处同步登记即可（`local=True` 的工具只在 Python 侧实现）。
- **接入真实游戏 API**：`lua_mod/bindings.lua` 是宿主绑定示例，`bridge.lua` 启动时会自动加载同目录的它来覆盖桩方法；也可由宿主 `require` 后调用 `bridge.bind(表)` 显式注入。方法/属性名需按你的 Lua 宿主环境核对。
- **多指令队列**：当前为"单指令覆盖"模型；如需连续下发多条指令，在 Python 端自建内存队列，逐条下发并等待回执，避免 `cmd.txt` 竞态覆盖。
- **自动化测试闭环**：用 `read_state` 轮询游戏状态做断言，把测试脚本化、可重复执行。
- **跨机/远程操控**：只要 `BL_BRIDGE_DIR` 指向网络共享目录，Agent 与游戏不必同机。
- **排查问题**：`state.json` 的 `err` 字段可快速区分 `bad_cmd`（指令格式错误）/ `timeout`（超时）/ `unknown_op`（未知操作）。

## 特别鸣谢

- 《骑马与砍杀2：霸主》MOD 开发生态与社区，以及 BLSE/Lua 注入等基础设施。
- MCP（Model Context Protocol）协议与 GitHub 官方 `github-mcp-server`。
- 本项目的发起与维护者：**lcx1107816013**。

## 免责声明

- 本项目仅供《骑马与砍杀2：霸主》**单机 MOD 自动化测试与研究**使用。
- 严禁用于多人联机作弊或任何破坏游戏公平性的行为；由此产生的一切后果由使用者自行承担。
- 本项目以 MIT 许可开源，**不提供任何明示或暗示担保**；使用风险自负。
- MOD 的开发与使用须遵守 TaleWorlds 的 MOD 政策与相关 EULA。

## 已知限制与安全提醒

- 文件轮询存在延迟（取决于 MOD 端 tick 间隔），不适合亚秒级实时控制。
- `cmd.txt` 同一时刻只承载一条待执行指令（队列机制见 `docs/开发文档.md`）。
- 仅支持本机/共享目录，不提供任何网络暴露；Agent 必须与游戏同机或共享该目录。
- Lua 桥依赖游戏内 Lua 运行环境（如 BLSE/Lua 注入）；若游戏无 Lua 宿主，MOD 端不会生效。
- **严禁在游戏主线程做阻塞 IO**：`cmd.txt` / `state.json` 读写必须在异步/协程或独立 tick 中完成，否则会卡死游戏（详见 `docs/开发文档.md`）。
- 指令写入前必须做**队列保护**与长度校验，防止文件半写/竞态。
- token、游戏存档、账号信息**绝不能**写入 `state.json` 或提交到仓库（见 `.gitignore`）。
- 本仓库不内置任何 MCP 鉴权；MCP Server 仅做本地文件桥接，切勿将其监听端口暴露到公网。

---

项目内容、目录结构与通信协议详见 [docs/开发文档.md](./docs/开发文档.md)。
