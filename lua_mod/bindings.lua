-- bannerlord-lua-bridge (blbridge) —— 宿主绑定示例
-- 作用：把 bridge.lua 里 Game 表的「桩方法」替换为真实游戏 API 调用。
-- 装载：bridge.lua 启动时会尝试 dofile(<MOD目录>/bindings.lua) 并合并其返回表；
--       也可由宿主脚本 require 后调用 bridge.bind(表) 显式注入。
-- 说明：不同游戏版本 / Lua 桥（BLSE 等）暴露的方法与属性名可能不同，
--       请对照你的宿主环境核对后再启用；本文件所有调用都处在 bridge 的 pcall 保护内，
--       单个方法报错只会返回 err，不会崩溃游戏。
-- 约定：返回表的方法名 == bridge.lua 中 OP2FN 的「值」（驼峰法名），如 getPlayer / listParties。

-- 无宿主（未注入 Campaign）时返回空表，bridge 继续使用桩实现，便于无游戏联调。
if type(Campaign) == "nil" or Campaign.Current == nil then return {} end

local B = {}

-- ---------- 工具函数 ----------
local function sid(o) return o and tostring(o.StringId) or nil end
local function nm(o) return o and tostring(o.Name) or nil end

local function each(list, fn)            -- 遍历 .NET 列表（0 基索引）
  if not list then return end
  for i = 0, list.Count - 1 do fn(list[i]) end
end

local function find(list, id)            -- 按 StringId 查找
  if not list or id == nil then return nil end
  id = tostring(id)
  for i = 0, list.Count - 1 do
    local o = list[i]
    if o and tostring(o.StringId) == id then return o end
  end
end

-- ---------- 主角 / 英雄 ----------
B.getPlayer = function()
  local h = Hero.MainHero
  return {
    heroId = sid(h), name = nm(h), level = h.Level, gold = h.Gold,
    clanId = sid(h.Clan), clanName = nm(h.Clan),
    kingdomId = h.Clan and sid(h.Clan.Kingdom) or nil,
    kingdomName = h.Clan and nm(h.Clan.Kingdom) or nil,
  }
end

B.setPlayerGold = function(c)
  Hero.MainHero.Gold = math.floor(tonumber(c.amount) or 0)
  return { ok = true, gold = Hero.MainHero.Gold }
end

B.addPlayerGold = function(c)
  Hero.MainHero.Gold = Hero.MainHero.Gold + math.floor(tonumber(c.amount) or 0)
  return { ok = true, gold = Hero.MainHero.Gold }
end

B.getHeroStats = function(c)
  local h = find(Hero.AllAliveHeroes, c.heroId) or Hero.MainHero
  return { heroId = sid(h), name = nm(h), level = h.Level, age = h.Age, hp = h.HitPoints }
end

B.listHeroes = function()
  local out = {}
  each(Hero.AllAliveHeroes, function(h)
    out[#out + 1] = { heroId = sid(h), name = nm(h), level = h.Level }
  end)
  return { count = #out, heroes = out }
end

-- ---------- 部队 party ----------
B.listParties = function(c)
  local out, want = {}, c.factionId and tostring(c.factionId) or nil
  each(MobileParty.All, function(p)
    if not want or sid(p.ActualClan) == want then
      out[#out + 1] = {
        partyId = sid(p), name = nm(p),
        members = p.MemberRoster.TotalManCount, clanId = sid(p.ActualClan),
      }
    end
  end)
  return { count = #out, parties = out }
end

B.getParty = function(c)
  local p = find(MobileParty.All, c.partyId)
  if not p then return { err = "party_not_found", partyId = c.partyId } end
  return {
    partyId = sid(p), name = nm(p), members = p.MemberRoster.TotalManCount,
    heroes = p.MemberRoster.TotalHeroes, morale = p.Morale,
  }
end

-- ---------- 定居点 ----------
B.listSettlements = function()
  local out = {}
  each(Settlement.All, function(s)
    out[#out + 1] = { settlementId = sid(s), name = nm(s), ownerClan = sid(s.OwnerClan) }
  end)
  return { count = #out, settlements = out }
end

B.getSettlement = function(c)
  local s = find(Settlement.All, c.settlementId)
  if not s then return { err = "settlement_not_found", settlementId = c.settlementId } end
  local garrison = s.Town and s.Town.GarrisonParty and s.Town.GarrisonParty.MemberRoster.TotalManCount or 0
  return {
    settlementId = sid(s), name = nm(s), ownerClan = sid(s.OwnerClan),
    prosperity = s.Prosperity, garrison = garrison,
  }
end

-- ---------- 王国 / 家族 / 外交 ----------
B.listKingdoms = function()
  local out = {}
  each(Kingdom.All, function(k)
    out[#out + 1] = { kingdomId = sid(k), name = nm(k), clans = k.Clans and k.Clans.Count or 0 }
  end)
  return { count = #out, kingdoms = out }
end

B.listClans = function()
  local out = {}
  each(Clan.All, function(cl)
    out[#out + 1] = { clanId = sid(cl), name = nm(cl), kingdomId = sid(cl.Kingdom) }
  end)
  return { count = #out, clans = out }
end

B.declareWar = function(c)
  local a, b = find(Kingdom.All, c.kingdomAId), find(Kingdom.All, c.kingdomBId)
  if not (a and b) then return { err = "kingdom_not_found" } end
  FactionManager.DeclareWar(a, b)
  return { ok = true, a = sid(a), b = sid(b) }
end

B.makePeace = function(c)
  local a, b = find(Kingdom.All, c.kingdomAId), find(Kingdom.All, c.kingdomBId)
  if not (a and b) then return { err = "kingdom_not_found" } end
  FactionManager.MakePeace(a, b)
  return { ok = true, a = sid(a), b = sid(b) }
end

-- ---------- 世界 / 时间 ----------
B.getCampaignTime = function()
  local t = CampaignTime.Now
  return {
    year = t.GetYear, season = t.GetSeasonOfYear, day = t.GetDayOfSeason,
    hour = t.GetHourOfDay, totalDays = Campaign.Current.CampaignStartTime and t.ElapsedDaysUntilNow or t.ToDays,
  }
end

B.getTimeScale = function() return { scale = Campaign.Current.TimeControlMode } end

B.setTimeScale = function(c)
  Campaign.Current.TimeControlMode = math.floor(tonumber(c.scale) or 1)
  return { ok = true, scale = Campaign.Current.TimeControlMode }
end

-- 快进：设置时间控制模式；真实天数推进由游戏主循环完成，调用后请用 get_campaign_time 轮询确认。
B.fastForward = function(c)
  Campaign.Current.TimeControlMode = 3
  return { ok = true, requested_days = tonumber(c.days) or 0, mode = 3 }
end

B.pauseGame = function() Campaign.Current.TimeControlMode = 0; return { ok = true } end
B.resumeGame = function() Campaign.Current.TimeControlMode = 1; return { ok = true } end

return B
