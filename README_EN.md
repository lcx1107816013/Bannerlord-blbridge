# Bannerlord blbridge

> Let an AI Agent remotely control *Mount & Blade II: Bannerlord* over the MCP protocol, for automated MOD testing.
> 中文版：见 [README.md](./README.md)。

## Project Introduction

Bannerlord blbridge splits "game control" into two ends:

- **MOD side (in-game)**: A small Lua script that polls a local `cmd.txt` on every game tick, parses the command, calls the game API, then writes the result to `state.json`.
- **External MCP Server (Python)**: A stdio MCP service that handles MCP protocol parsing and bridges files between the Agent and the game MOD (writes `cmd.txt` / reads `state.json`).

The two ends communicate purely through local text files. **The MOD side embeds no MCP protocol at all**, so the MOD is minimal and stable.

**What it can do**
- Lets an AI Agent (e.g. CodeBuddy) drive the game through standard MCP tools — spawn troops, read hero stats, etc.
- Turns repetitive manual testing into orchestrated, automated scripts, boosting MOD dev efficiency.
- Provides a clear file-based protocol that is easy to extend and debug.

## Architecture

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

Data flow: Agent → MCP tool call → Python writes `cmd.txt` → Lua polls & reads → executes → writes `state.json` → Python reads → returns to Agent.

## Quick Deployment

1. **Get the MOD package**: Download `BannerlordBlbridge.zip` from this repo's `Actions`, unzip into Bannerlord's `Modules/` directory (the folder name must match `<Id>` in `lua_mod/SubModule.xml`, i.e. `BannerlordBlbridge`).
2. **Enable the MOD**: Tick `BannerlordBlbridge` in the launcher and start the game; confirm `cmd.txt` and `state.json` can be read/written in the MOD directory (path is at the top of `lua_mod/bridge.lua` as `DIR`).
3. **Install Python deps**: `pip install -r mcp_server/requirements.txt` (the service uses only the standard library, so this file may stay empty).
4. **Configure the MCP client**: Add a stdio server in an MCP-capable client (e.g. CodeBuddy) and point `BL_BRIDGE_DIR` at the MOD directory from step 2.
5. **Restart the client**: So the MCP tools load; you can then call `spawn_troop` / `get_hero_stats` etc. in chat.

MCP client config (put under `mcp.servers` in your client's `settings.json`):

```json
{
  "mcp": {
    "servers": {
      "bannerlord-remote": {
        "command": "python",
        "args": ["mcp_server/server.py"],
        "env": { "BL_BRIDGE_DIR": "C:/path/to/game/Modules/BannerlordBlbridge" }
      }
    }
  }
}
```

> `BL_BRIDGE_DIR` must point at the directory containing `cmd.txt` / `state.json`, and the MOD side and Python side must point at the same directory.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BL_BRIDGE_DIR` | Yes | Directory of `cmd.txt` / `state.json` (must be identical on MOD and Python sides). |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | No (only needed if you use `github-mcp-server` for repo management) | Auth token for the official `github-mcp-server`; this project does not depend on it at runtime. Inject it via a system environment variable — never write it to a file or commit it. |

## Tool List (57 tools)

**Local tools (11, handled in Python)**:
`ping`, `get_bridge_info`, `list_ops`, `get_protocol`, `read_state`, `wait_for_state`, `clear_state`, `assert_state`, `run_ops`, `send_raw`, `sleep`

**Game tools (46, forwarded to the in-game MOD)**, by category:

| Category | Tools |
|----------|-------|
| Player / Hero | `get_player`, `set_player_gold`, `add_player_gold`, `get_player_inventory`, `add_item`, `get_hero_stats`, `list_heroes`, `set_hero_attr`, `heal_hero`, `wound_hero` |
| Party | `spawn_troop`, `list_parties`, `get_party`, `add_troops`, `remove_troops`, `disband_party`, `teleport_party`, `merge_parties`, `start_battle`, `auto_resolve_battle`, `get_visible_parties`, `get_map_entities` |
| Settlement | `list_settlements`, `get_settlement`, `set_settlement_owner` |
| Kingdom / Clan / Diplomacy | `list_kingdoms`, `list_clans`, `declare_war`, `make_peace`, `get_relations` |
| World / Time | `get_campaign_time`, `get_time_scale`, `set_time_scale`, `fast_forward`, `pause_game`, `resume_game`, `get_weather`, `set_weather` |
| Quest / Log / Save | `list_quests`, `add_quest`, `complete_quest`, `get_missions`, `get_campaign_log`, `save_game`, `load_game` |
| Test helpers | `take_snapshot` |

> Defined in `ENTRIES` of `mcp_server/server.py`; to add a tool, register it both in `ENTRIES` and in `OP2FN` of `lua_mod/bridge.lua`. At runtime, use `list_ops` / `get_protocol` to introspect.

## Advanced Tips

- **Tune latency vs. stability**: Adjust the `loop` poll interval in `lua_mod/bridge.lua` and `POLL_TIMEOUT` in `mcp_server/server.py` to trade off responsiveness and stability.
- **Extend tools**: Register in `ENTRIES` of `server.py` and in `OP2FN` of `lua_mod/bridge.lua` (`local=True` tools live only in Python).
- **Bind real game APIs**: `lua_mod/bindings.lua` is a host-binding sample; `bridge.lua` auto-loads it from the same directory to override the stubs. You can also inject via `bridge.bind(table)` from your host script. Verify method/property names against your Lua host before enabling.
- **Multi-command queue**: The current model is "single command overwrite". For sequential commands, build an in-memory queue on the Python side, dispatch one at a time and wait for the receipt to avoid `cmd.txt` races.
- **Automated test loop**: Use `read_state` to poll game state and make assertions, turning tests into repeatable scripts.
- **Remote / cross-machine control**: As long as `BL_BRIDGE_DIR` points to a network-shared folder, the Agent and the game need not be on the same machine.
- **Troubleshooting**: The `err` field in `state.json` quickly distinguishes `bad_cmd` (malformed command) / `timeout` / `unknown_op` (unknown operation).

## Ecosystem Integration (Bannerlord Toolchain)

This project is a *lightweight scripting bridge* covering only the orchestration layer. **MOD authoring/editing, localization, and game-client / BLSE debugging & launching** are provided by dedicated MCP servers and orchestrated by the AI Agent:

| Stage | MCP server | Representative tools |
|-------|-----------|----------------------|
| Knowledge / API / install diagnostics | `bannerlordsage` | `search_bannerlord_knowledge`, `search_bannerlord_api_docs`, `bannerlord_doctor` |
| MOD authoring / editing / patches | `bannerlordsage` | `create_mod_workspace`, `generate_harmony_patch`, `generate_xslt_patch`, `index_mod_source` |
| Localization / translation | `bannerlordhelper` | `bh_list_local_modules`, `bh_translate_module`, `bh_create_external_translation` |
| Launch / client control / runtime debug | `blbridge` | `bl_launch_game` (via BLSE), `bl_status`, `bl_get_screen`, `bl_load_save`, `bl_apply_config` |
| Scripting / automated assertions | **this project** | 57 tools + Lua text bridge (`run_ops` / `assert_state`, etc.) |

> `blbridge` is a C#-DLL direct link (powerful, needs deployment); this project is a Lua text bridge (lightweight, portable, easy to extend). They complement each other.
> See [docs/工具链联动.md](./docs/工具链联动.md) for the end-to-end pipeline and caveats.

## Acknowledgements

- The *Mount & Blade II: Bannerlord* MOD development ecosystem and community, including BLSE/Lua injection tooling.
- The MCP (Model Context Protocol) and the official GitHub `github-mcp-server`.
- The initiator and maintainer of this project: **lcx1107816013**.

## Disclaimer

- This project is intended **only for single-player MOD automated testing and research** of *Mount & Blade II: Bannerlord*.
- Do not use it for multiplayer cheating or any behavior that undermines fair play; all consequences are borne solely by the user.
- This project is open-sourced under the MIT License and is provided **without any express or implied warranty**; use it at your own risk.
- MOD development and usage must comply with TaleWorlds' MOD policy and the relevant EULA.

## Known Limitations & Security Notes

- File polling introduces latency (depends on the MOD tick interval); not suitable for sub-second real-time control.
- `cmd.txt` holds only one pending command at a time (queueing is covered in `docs/开发文档.md`).
- Only local/shared-folder communication is supported — no network exposure; the Agent must share the folder with the game.
- The Lua bridge depends on an in-game Lua runtime (e.g. BLSE/Lua); without it the MOD side won't activate.
- **Never do blocking IO on the game's main thread**: `cmd.txt` / `state.json` reads/writes must run in async / coroutine / separate tick, or the game will freeze (see `docs/开发文档.md`).
- Always add **queue protection** and length validation before writing a command, to avoid half-written files / races.
- Never write tokens, saves, or account info into `state.json` or commit them (see `.gitignore`).
- This repo embeds no MCP auth; the MCP Server is a local file bridge only — never expose its listening port to the public internet.

---

For the directory layout, communication protocol and risk notes, see [docs/开发文档.md](./docs/开发文档.md).
