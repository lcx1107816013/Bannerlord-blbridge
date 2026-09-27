-- bannerlord-lua-bridge (blbridge)
-- 职责：游戏内 tick 轮询 cmd.txt -> 执行 op -> 写 state.json（回写 seq + 支持嵌套结果）
-- 设计：省字符 + 全链路 pcall 异常捕获；cmd 经受限环境加载，禁止执行 IO/OS
local DIR = "C:/Program Files (x86)/Steam/steamapps/common/Mount & Blade II Bannerlord/Modules/BannerlordBlbridge/"
local CMD, STA = DIR .. "cmd.txt", DIR .. "state.json"
local last = ""             -- 已处理指令指纹，去重防止重复执行

-- op -> 游戏 API 方法名（真实环境由宿主 Lua 绑定覆盖 Game 表中的同名桩）
local OP2FN = {
  get_player = "getPlayer", set_player_gold = "setPlayerGold", add_player_gold = "addPlayerGold",
  get_player_inventory = "getPlayerInventory", add_item = "addItem",
  get_hero_stats = "getHeroStats", list_heroes = "listHeroes", set_hero_attr = "setHeroAttr",
  heal_hero = "healHero", wound_hero = "woundHero",
  spawn_troop = "spawnTroop", list_parties = "listParties", get_party = "getParty",
  add_troops = "addTroops", remove_troops = "removeTroops", disband_party = "disbandParty",
  teleport_party = "teleportParty", merge_parties = "mergeParties", start_battle = "startBattle",
  auto_resolve_battle = "autoResolveBattle", get_visible_parties = "getVisibleParties",
  get_map_entities = "getMapEntities",
  list_settlements = "listSettlements", get_settlement = "getSettlement", set_settlement_owner = "setSettlementOwner",
  list_kingdoms = "listKingdoms", list_clans = "listClans", declare_war = "declareWar",
  make_peace = "makePeace", get_relations = "getRelations",
  get_campaign_time = "getCampaignTime", get_time_scale = "getTimeScale", set_time_scale = "setTimeScale",
  fast_forward = "fastForward", pause_game = "pauseGame", resume_game = "resumeGame",
  get_weather = "getWeather", set_weather = "setWeather",
  list_quests = "listQuests", add_quest = "addQuest", complete_quest = "completeQuest",
  get_missions = "getMissions", get_campaign_log = "getCampaignLog",
  save_game = "saveGame", load_game = "loadGame", take_snapshot = "takeSnapshot",
}

-- 游戏 API：默认桩实现（宿主绑定后覆盖），保证无宿主时协议仍可端到端联调
local Game = {}
for _, fn in pairs(OP2FN) do Game[fn] = function(c) return { stub = true, op = c.op, args = c } end end
Game.spawnTroop = function(c) return { ok = true, partyId = c.partyId, spawned = c.count } end
Game.getHeroStats = function(c) return { heroId = c.heroId, level = math.random(1, 40), hp = math.random(50, 150) } end

-- 可选宿主绑定：若同目录存在 bindings.lua，则加载并覆盖上述桩方法（失败静默，保留桩）
pcall(function()
  local b = dofile(DIR .. "bindings.lua")
  if type(b) == "table" then for k, v in pairs(b) do Game[k] = v end end
end)

-- 极简 table->json（支持嵌套 table / string / number / boolean）
local function enc(v)
  local t = type(v)
  if t == "number" or t == "boolean" then return tostring(v) end
  if t == "string" then
    return '"' .. v:gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('\n', '\\n') .. '"'
  end
  if t == "table" then
    local p = {}
    for k, x in pairs(v) do p[#p + 1] = '"' .. tostring(k) .. '":' .. enc(x) end
    return "{" .. table.concat(p, ",") .. "}"
  end
  return "null"
end

local function rd(f)           -- 安全读文件，失败返回 nil
  local ok, s = pcall(function()
    local h = io.open(f, "r"); if not h then return nil end
    local v = h:read("*a"); h:close(); return v
  end)
  return ok and s or nil
end

local function wr(f, s)        -- 安全写文件
  return pcall(function()
    local h = io.open(f, "w"); if not h then return end
    h:write(s); h:close()
  end)
end

local function run(cmd)        -- op 分发（白名单，未知 op 返回 err）
  local fn = OP2FN[cmd.op]
  if not fn then return { err = "unknown_op", op = cmd.op } end
  local ok, res = pcall(Game[fn], cmd)
  return ok and (res or { err = "nil" }) or { err = tostring(res) }
end

local function tick()          -- 单次轮询
  local s = rd(CMD)
  if s and s ~= last then
    last = s
    -- 受限环境加载：阻断 os/io 等危险全局，仅允许基础类型构造
    local safe = { math = math, string = string, pairs = pairs, type = type, tostring = tostring, tonumber = tonumber }
    local ok, cmd = pcall(function() return load("return " .. s, "cmd", "t", safe)() end)
    if ok and type(cmd) == "table" then
      local res = run(cmd)
      if type(res) ~= "table" then res = { value = res } end
      res.seq = cmd.seq               -- 回写 seq，供外置端按序对齐回执
      wr(STA, enc(res))
    else
      wr(STA, enc({ err = "bad_cmd", seq = -1 }))
    end
  end
end

-- 由宿主环境按固定间隔调用（如每 0.5s 一次），务必在非主线程/协程中调用
local function loop()
  pcall(tick)                 -- 顶层再包一层，确保任何异常都不抛出到游戏主循环
end

local function bind(t)      -- 供宿主显式注入绑定：键为 OP2FN 的值（驼峰法名）
  if type(t) == "table" then for k, v in pairs(t) do Game[k] = v end end
  return Game
end

return { loop = loop, tick = tick, ops = OP2FN, bind = bind }
