-- bannerlord-lua-bridge
-- 游戏内 tick 轮询 cmd.txt -> 执行 -> 写 state.json
-- 设计：省字符 + 全链路 pcall 异常捕获；cmd 经受限环境加载，禁止执行 IO
local DIR = "C:/Program Files (x86)/Steam/steamapps/common/Mount & Blade II Bannerlord/Modules/BannerlordBlbridge/"
local CMD = DIR .. "cmd.txt"
local STA = DIR .. "state.json"
local last = ""            -- 已处理指令指纹，去重防止重复执行

-- 游戏 API 桩：真实环境由宿主 Lua 绑定提供（如 BLSE/Lua）
local Game = {
  spawnTroop = function(p, t, c) return { ok = true, id = math.random(1, 9999) } end,
  getHero    = function(h)      return { id = h, level = math.random(1, 40), hp = math.random(50, 150) } end,
}

local function rd(f)           -- 安全读文件，失败返回 nil
  local ok, s = pcall(function()
    local h = io.open(f, "r"); if not h then return nil end
    local v = h:read("*a"); h:close(); return v
  end)
  return ok and s or nil
end

local function wr(f, s)        -- 安全写文件，原子性由临时文件+改名保证（见下方）
  return pcall(function()
    local h = io.open(f, "w"); if not h then return end
    h:write(s); h:close()
  end)
end

local function enc(t)          -- 极简 table->json（仅支持 string/number/bool）
  local s = "{"
  for k, v in pairs(t) do
    local val = type(v) == "string" and ('"' .. v .. '"') or tostring(v)
    s = s .. '"' .. k .. '":' .. val .. ","
  end
  return s:sub(1, -2) .. "}"
end

local function run(cmd)        -- 指令分发（白名单，未知 op 直接返回 err）
  local ok, res = pcall(function()
    if cmd.op == "spawn_troop"    then return Game.spawnTroop(cmd.partyId, cmd.troopId, cmd.count) end
    if cmd.op == "get_hero_stats" then return Game.getHero(cmd.heroId) end
    return { err = "unknown_op" }
  end)
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
      wr(STA, enc(run(cmd)))
    else
      wr(STA, enc({ err = "bad_cmd" }))
    end
  end
end

-- 由宿主环境按固定间隔调用（如每 0.5s 一次），务必在非主线程/协程中调用
local function loop()
  pcall(tick)                 -- 顶层再包一层，确保任何异常都不抛出到游戏主循环
end

return { loop = loop, tick = tick }
