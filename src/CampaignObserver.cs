using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;

using TaleWorlds.CampaignSystem;
using TaleWorlds.CampaignSystem.Actions;
using TaleWorlds.CampaignSystem.MapEvents;
using TaleWorlds.CampaignSystem.Party;
using TaleWorlds.CampaignSystem.Settlements;
using TaleWorlds.CampaignSystem.Siege;
using TaleWorlds.Core;
using TaleWorlds.SaveSystem;

namespace BlBridge
{
    /// <summary>
    /// 战役观察者（observer 层）：**只订阅引擎事件，不驱动任何东西**。
    ///
    /// ## 它解决什么问题
    ///
    /// 此前 BlBridge 的战役能力**全是轮询快照**（`CampaignReadProbe` 的
    /// overview / list_parties / campaign_log 等）：要看"N 天里发生了什么"，只能
    /// "快进 → 采样 → 快进 → 采样"，**两次采样之间的所有事件都丢了**。
    /// 而 mod 测试真正要回答的问题是：
    ///
    ///   • 某位领主"下令"之后，他的 `DefaultBehavior` **到底变没变**？（命令是否生效）
    ///   • 哪两支队伍打起来了、在哪、什么类型？（战役层涌现的事件）
    ///   • 聚落什么时候易主、围攻什么时候开始/结束？
    ///
    /// 这些**引擎本来就会发事件**（`CampaignEvents.*`，引擎自己订阅了 83 处），
    /// 我们只是**接上一根线**。
    ///
    /// ## 与项目纪律的一致性
    ///
    /// - **零 Harmony**：只 `AddNonSerializedListener`（订阅 .NET 原生事件），
    ///   不 patch 任何方法 ⇒ 与 `ExceptionProbe` 同性质，**删模块即完全回退**。
    /// - **绝不干预**：任何处理器都**不调用**会改变游戏状态的 API（不 SetMove / 不 Apply）。
    /// - **绝不抛**：每个处理器整体 try/catch。事件处理器抛异常会顺着引擎的
    ///   `CampaignEventDispatcher` 往外冒，可能**打断战役 tick** —— 那是不可接受的。
    /// - **诚实记录丢失**：环形缓冲满了会丢最旧的，但 `dropped` 计数**如实上报**，
    ///   绝不让外部把"丢了"误读成"没发生"（本项目踩过的坑：静默失败无法与"真没问题"区分）。
    ///
    /// ## ★ 信噪比：为什么 AiHourlyTick 只记"变化"
    ///
    /// `AiHourlyTickEvent` 会为**每支队伍、每 1/3/6 小时**触发一次（真机一个战役日
    /// 可达数千次）。全记会把缓冲瞬间冲爆、且淹没真正有用的信号。
    /// ⇒ 默认只记 **`DefaultBehavior` 发生变化**的那些（这正是"领主下令有没有生效"
    /// 的判据）；要看全部候选打分请开 `verboseAi`。
    ///
    /// ## 为什么用内存环形缓冲 + 延迟落盘（照 ExceptionProbe 的两段式）
    ///
    /// 事件处理器在**战役主线程**里跑。若在处理器内直接写文件，快进时每秒数百次
    /// 文件 I/O 会**拖慢游戏本身**，从而改变被测系统的行为（观测者效应）。
    /// ⇒ 处理器只做"渲染成一行 JSON 并入队"（纯内存），真正的写盘由
    /// `DrainPending()` 在 `OnApplicationTick` 里**批量**完成。
    /// </summary>
    internal class CampaignObserver : CampaignBehaviorBase
    {
        // ── 总开关与容量 ────────────────────────────────────────────────
        //
        // 默认 **开**：它零副作用、只读、且是诊断的入口 —— 关着反而会让
        // "为什么没记录到"变成新的疑团。要关随时 observer_config enabled=false。

        /// <summary>是否记录（默认开）。</summary>
        internal static bool Enabled = true;

        /// <summary>是否连"未发生变化"的 AI 决策也记（默认关 —— 信噪比，见类注释）。</summary>
        internal static bool VerboseAi = false;

        /// <summary>内存环形缓冲容量（保留最近 N 条供查询）。</summary>
        internal static int MaxEvents = 2000;

        /// <summary>单次排空最多写多少行（防快进时单帧写太久）。</summary>
        private const int MaxDrainPerTick = 400;

        // ── 内存环形缓冲 ────────────────────────────────────────────────
        private static readonly object _lock = new object();
        private static readonly List<string> _events = new List<string>(1024);
        private static readonly Queue<string> _pending = new Queue<string>();
        private static long _seq;
        private static long _dropped;
        private static readonly Dictionary<string, long> _byType = new Dictionary<string, long>();
        private static bool _writerOpened;
        private static string _writerPath;

        /// <summary>各 party 上次见到的 DefaultBehavior（"变化"判据的基线）。</summary>
        private static readonly Dictionary<MobileParty, string> _lastBehavior =
            new Dictionary<MobileParty, string>();

        internal static string ObserverLogPath
        {
            get { return _writerPath; }
        }

        // ══════════════════════════════════════════════════════════════
        // 事件注册 —— 全部是引擎既有事件，我们只订阅
        // ══════════════════════════════════════════════════════════════

        public override void RegisterEvents()
        {
            try
            {
                // 战斗（战役层"涌现事件"的主力）
                CampaignEvents.MapEventStarted.AddNonSerializedListener(this, OnMapEventStarted);
                CampaignEvents.MapEventEnded.AddNonSerializedListener(this, OnMapEventEnded);
                // 围攻
                CampaignEvents.OnSiegeEventStartedEvent.AddNonSerializedListener(this, OnSiegeStarted);
                // 聚落易主（mod 改经济/政治时最常被影响）
                CampaignEvents.OnSettlementOwnerChangedEvent.AddNonSerializedListener(this, OnSettlementOwnerChanged);
                // 外交
                CampaignEvents.WarDeclared.AddNonSerializedListener(this, OnWarDeclared);
                CampaignEvents.MakePeace.AddNonSerializedListener(this, OnMakePeace);
                // 队伍消亡
                CampaignEvents.MobilePartyDestroyed.AddNonSerializedListener(this, OnPartyDestroyed);
                // AI 决策（默认只记变化，见类注释）
                CampaignEvents.AiHourlyTickEvent.AddNonSerializedListener(this, OnAiHourlyTick);

                // 读档时建立 writer 与清空基线
                CampaignEvents.OnGameLoadedEvent.AddNonSerializedListener(this, OnGameLoaded);
            }
            catch (Exception ex)
            {
                ActionLedger.ExceptionToRgl("CampaignObserver.RegisterEvents", ex);
            }
        }

        public override void SyncData(IDataStore dataStore)
        {
            // 刻意不持久化：观察数据是**会话级**诊断产物，写进存档只会污染玩家的档。
        }

        private void OnGameLoaded(CampaignGameStarter starter)
        {
            try
            {
                lock (_lock)
                {
                    _lastBehavior.Clear();
                }
                EnsureWriter();
                Record("observer_start", "\"note\":" + Protocol.Q("读档完成，观察者已就绪"));
            }
            catch (Exception ex)
            {
                ActionLedger.ExceptionToRgl("CampaignObserver.OnGameLoaded", ex);
            }
        }

        // ══════════════════════════════════════════════════════════════
        // 处理器 —— 每一个都整体 try/catch，绝不把异常放回引擎
        // ══════════════════════════════════════════════════════════════

        private void OnMapEventStarted(MapEvent mapEvent, PartyBase attackerParty, PartyBase defenderParty)
        {
            try
            {
                if (mapEvent == null) return;
                StringBuilder sb = new StringBuilder();
                sb.Append("\"settlement\":").Append(NameOfSettlement(mapEvent.MapEventSettlement));
                sb.Append(",\"attacker\":").Append(PartyBrief(attackerParty));
                sb.Append(",\"defender\":").Append(PartyBrief(defenderParty));
                sb.Append(",\"attackerStrength\":").Append(NumOf(attackerParty, "EstimatedStrength"));
                sb.Append(",\"defenderStrength\":").Append(NumOf(defenderParty, "EstimatedStrength"));
                sb.Append(",\"fieldBattle\":").Append(Jw.B(BoolP(mapEvent, "IsFieldBattle")));
                sb.Append(",\"siegeAssault\":").Append(Jw.B(BoolP(mapEvent, "IsSiegeAssault")));
                sb.Append(",\"raid\":").Append(Jw.B(BoolP(mapEvent, "IsRaid")));
                sb.Append(",\"blockade\":").Append(Jw.B(BoolP(mapEvent, "IsBlockade")));
                sb.Append(",\"sallyOut\":").Append(Jw.B(BoolP(mapEvent, "IsSallyOut")));
                sb.Append(",\"naval\":").Append(Jw.B(BoolP(mapEvent, "IsNavalMapEvent")));
                sb.Append(",\"battleTypes\":").Append(Protocol.Q(SafeStr(mapEvent, "BattleTypes")));
                sb.Append(",\"posX\":").Append(NumP(mapEvent, "Position", "X"));
                sb.Append(",\"posY\":").Append(NumP(mapEvent, "Position", "Y"));
                sb.Append(",\"playerInvolved\":").Append(Jw.B(PlayerInvolved(mapEvent)));
                Record("map_event_started", sb.ToString());
            }
            catch (Exception ex)
            {
                NoteInternalError("OnMapEventStarted", ex);
            }
        }

        private void OnMapEventEnded(MapEvent mapEvent)
        {
            try
            {
                if (mapEvent == null) return;
                StringBuilder sb = new StringBuilder();
                sb.Append("\"settlement\":").Append(NameOfSettlement(mapEvent.MapEventSettlement));
                sb.Append(",\"battleState\":").Append(Protocol.Q(SafeStr(mapEvent, "BattleState")));
                sb.Append(",\"retreatingSide\":").Append(Protocol.Q(SafeStr(mapEvent, "RetreatingSide")));
                // 结束时尽量读双方（可能已被清空 —— 读到 null 是**正常**，不是错误）
                sb.Append(",\"attacker\":").Append(PartyBrief(mapEvent.AttackerSide == null ? null : mapEvent.AttackerSide.LeaderParty));
                sb.Append(",\"defender\":").Append(PartyBrief(mapEvent.DefenderSide == null ? null : mapEvent.DefenderSide.LeaderParty));
                sb.Append(",\"attackerTroops\":").Append(IntOf(mapEvent.AttackerSide, "TroopCount"));
                sb.Append(",\"defenderTroops\":").Append(IntOf(mapEvent.DefenderSide, "TroopCount"));
                sb.Append(",\"playerInvolved\":").Append(Jw.B(PlayerInvolved(mapEvent)));
                Record("map_event_ended", sb.ToString());
            }
            catch (Exception ex)
            {
                NoteInternalError("OnMapEventEnded", ex);
            }
        }

        private void OnSiegeStarted(SiegeEvent siegeEvent)
        {
            try
            {
                if (siegeEvent == null) return;
                StringBuilder sb = new StringBuilder();
                sb.Append("\"settlement\":").Append(NameOfSettlement(siegeEvent.BesiegedSettlement));
                sb.Append(",\"targetOwner\":").Append(Protocol.Q(SettlementOwner(siegeEvent.BesiegedSettlement)));

                // ⚠️ 两处真机抓出来的错（v0.8.51 修；改前这两个字段**恒为 null**）：
                //
                //   ① `BesiegerCamp.LeaderParty` 是 **MobileParty**，不是 PartyBase。
                //      原先写 `P(camp,"LeaderParty") as PartyBase` ⇒ 永远 null
                //      （MobileParty 不继承 PartyBase；要 PartyBase 得取 `.Party`）。
                //      `PartyBrief` 本身能直接吃 MobileParty ⇒ 直接传，别转。
                //   ② `BesiegerCamp` 上**没有** `NumberOfParties` 成员 ⇒ 反射取不到，
                //      回的是 "null" —— 看着像"没有围攻者"，实际是**字段名猜错了**。
                //      正确入口是 `ISiegeEventSide.GetInvolvedPartiesForEventType()`。
                //
                // ⇒ 教训（写进本项目纪律）：反射取值**必须用真机数据回读验证**。
                //   这类错编译得过、跑得动、不抛异常，只是值恒 null ——
                //   若不看真实输出，永远发现不了。
                object camp = siegeEvent.BesiegerCamp;
                object lead = P(camp, "LeaderParty");
                sb.Append(",\"besiegerParties\":").Append(CountInvolvedParties(camp));
                sb.Append(",\"besiegerLead\":").Append(PartyBrief(lead));
                sb.Append(",\"besiegerFaction\":").Append(
                    Protocol.Q(SafeStr(P(lead, "MapFaction"), "Name")));
                object besieged = siegeEvent.BesiegedSettlement;
                sb.Append(",\"defenderParties\":").Append(CountInvolvedParties(besieged));
                sb.Append(",\"besiegedFaction\":").Append(
                    Protocol.Q(SafeStr(P(besieged, "MapFaction"), "Name")));
                sb.Append(",\"settlementType\":").Append(Protocol.Q(SettlementType(besieged)));
                sb.Append(",\"playerIsBesieger\":").Append(Jw.B(
                    lead != null && BoolP(lead, "IsMainParty")));
                Record("siege_started", sb.ToString());
            }
            catch (Exception ex)
            {
                NoteInternalError("OnSiegeStarted", ex);
            }
        }

        /// <summary>
        /// 数一个 `ISiegeEventSide`（`BesiegerCamp` / `Settlement`）上的参战方数量。
        ///
        /// ⚠️ 不能用无参反射找 `GetInvolvedPartiesForEventType()` —— 它声明为
        /// `(MapEvent.BattleTypes mapEventType = MapEvent.BattleTypes.Siege)`，
        /// **默认参数在反射里仍计为一个参数** ⇒ `GetMethod(name, Type.EmptyTypes)` 找不到。
        /// ⇒ 这里按 `ParameterInfo.HasDefaultValue` 通用填默认值再调，
        ///   避免把枚举类型硬编码进来（那会让本文件多依赖一个命名空间）。
        /// </summary>
        private static string CountInvolvedParties(object side)
        {
            try
            {
                if (side == null) return "null";
                MethodInfo mi = side.GetType().GetMethod("GetInvolvedPartiesForEventType",
                    BindingFlags.Public | BindingFlags.Instance);
                if (mi == null) return "null";

                ParameterInfo[] ps = mi.GetParameters();
                object[] argv = new object[ps.Length];
                for (int i = 0; i < ps.Length; i++)
                {
                    if (ps[i].HasDefaultValue) argv[i] = ps[i].DefaultValue;
                    else if (ps[i].ParameterType.IsValueType)
                        argv[i] = Activator.CreateInstance(ps[i].ParameterType);
                    else argv[i] = null;
                }

                object res = mi.Invoke(side, argv);
                if (res == null) return "null";
                System.Collections.ICollection col = res as System.Collections.ICollection;
                if (col != null) return col.Count.ToString(CultureInfo.InvariantCulture);
                System.Collections.IEnumerable en = res as System.Collections.IEnumerable;
                if (en != null)
                {
                    int n = 0;
                    foreach (object _ in en) n++;
                    return n.ToString(CultureInfo.InvariantCulture);
                }
                return "null";
            }
            catch
            {
                return "null";
            }
        }

        private void OnSettlementOwnerChanged(Settlement settlement, bool openToClaim, Hero newOwner,
            Hero oldOwner, Hero capturerHero, ChangeOwnerOfSettlementAction.ChangeOwnerOfSettlementDetail detail)
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                sb.Append("\"settlement\":").Append(NameOfSettlement(settlement));
                sb.Append(",\"newOwner\":").Append(HeroBrief(newOwner));
                sb.Append(",\"oldOwner\":").Append(HeroBrief(oldOwner));
                sb.Append(",\"capturer\":").Append(HeroBrief(capturerHero));
                sb.Append(",\"detail\":").Append(Protocol.Q(detail.ToString()));
                // ⚠️ 键名**必须**叫 `settlementType` 而不是 `type`（v0.8.56 修的真 bug）：
                //   `Record()` 已在最外层写了 `"type":"settlement_owner_changed"`，
                //   这里再写一个 `"type"` ⇒ **同一对象里两个同名键**。
                //   JSON 规范下后者覆盖前者，Python `json.loads` 取最后一个 ⇒
                //   事件类型被静默改写成 `castle`/`town`，按 type 过滤/统计**全部失效**
                //   （实测：落盘 59856 行里 101 行被改写，统计里冒出了 `castle:35, town:66`）。
                //   ⇒ 教训：**扁平 JSON 拼装时，键名要与外层保留字避让**。
                sb.Append(",\"settlementType\":").Append(Protocol.Q(SettlementType(settlement)));
                Record("settlement_owner_changed", sb.ToString());
            }
            catch (Exception ex)
            {
                NoteInternalError("OnSettlementOwnerChanged", ex);
            }
        }

        private void OnWarDeclared(IFaction faction1, IFaction faction2, DeclareWarAction.DeclareWarDetail detail)
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                sb.Append("\"faction1\":").Append(FactionBrief(faction1));
                sb.Append(",\"faction2\":").Append(FactionBrief(faction2));
                sb.Append(",\"detail\":").Append(Protocol.Q(detail.ToString()));
                Record("war_declared", sb.ToString());
            }
            catch (Exception ex)
            {
                NoteInternalError("OnWarDeclared", ex);
            }
        }

        private void OnMakePeace(IFaction faction1, IFaction faction2, MakePeaceAction.MakePeaceDetail detail)
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                sb.Append("\"faction1\":").Append(FactionBrief(faction1));
                sb.Append(",\"faction2\":").Append(FactionBrief(faction2));
                sb.Append(",\"detail\":").Append(Protocol.Q(detail.ToString()));
                Record("make_peace", sb.ToString());
            }
            catch (Exception ex)
            {
                NoteInternalError("OnMakePeace", ex);
            }
        }

        private void OnPartyDestroyed(MobileParty party, PartyBase destroyer)
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                sb.Append("\"party\":").Append(PartyBrief(party));
                sb.Append(",\"destroyer\":").Append(PartyBrief(destroyer));
                sb.Append(",\"isLordParty\":").Append(Jw.B(BoolP(party, "IsLordParty")));
                sb.Append(",\"isBandit\":").Append(Jw.B(BoolP(party, "IsBandit")));
                sb.Append(",\"isCaravan\":").Append(Jw.B(BoolP(party, "IsCaravan")));
                sb.Append(",\"clan\":").Append(Protocol.Q(SafeStr(P(party, "ActualClan"), "Name")));
                Record("party_destroyed", sb.ToString());
            }
            catch (Exception ex)
            {
                NoteInternalError("OnPartyDestroyed", ex);
            }
        }

        /// <summary>
        /// AI 决策。**默认只在 `DefaultBehavior` 变化时记录** —— 判据取自
        /// `MobilePartyAi`：`AiPartyThinkBehavior` 在 dispatch **之后**才写
        /// `DefaultBehavior`，所以"变化"会在该队伍的下一次 tick 上被我们看见。
        /// 这就是"某位领主下令之后，命令到底生效了没有 / 变成什么了"的直接读数。
        /// </summary>
        private void OnAiHourlyTick(MobileParty party, PartyThinkParams p)
        {
            try
            {
                if (party == null || party == MobileParty.MainParty) return;

                string now = SafeStr(party, "DefaultBehavior");
                string shortTerm = SafeStr(party, "ShortTermBehavior");
                string prev = null;
                bool hasPrev;
                lock (_lock)
                {
                    hasPrev = _lastBehavior.TryGetValue(party, out prev);
                    _lastBehavior[party] = now;
                }
                bool changed = hasPrev && !string.Equals(prev, now, StringComparison.Ordinal);
                if (!changed && !VerboseAi) return;

                StringBuilder sb = new StringBuilder();
                sb.Append("\"party\":").Append(PartyBrief(party));
                sb.Append(",\"changed\":").Append(Jw.B(changed));
                sb.Append(",\"was\":").Append(Protocol.Q(prev ?? ""));
                sb.Append(",\"now\":").Append(Protocol.Q(now));
                sb.Append(",\"shortTerm\":").Append(Protocol.Q(shortTerm));
                sb.Append(",\"targetParty\":").Append(Protocol.Q(SafeStr(P(party, "ShortTermTargetParty"), "Name")));
                sb.Append(",\"targetSettlement\":").Append(NameOfSettlement(P(party, "TargetSettlement") as Settlement));
                sb.Append(",\"objectiveValue\":").Append(p == null ? "null" : Jw.N(p.CurrentObjectiveValue));
                sb.Append(",\"willGatherArmy\":").Append(Jw.B(p != null && p.WillGatherAnArmy));
                sb.Append(",\"doNotChange\":").Append(Jw.B(p != null && p.DoNotChangeBehavior));
                sb.Append(",\"inArmy\":").Append(Jw.B(BoolP(party, "Army") || P(party, "Army") != null));
                Record(changed ? "ai_behavior_changed" : "ai_tick", sb.ToString());
            }
            catch (Exception ex)
            {
                NoteInternalError("OnAiHourlyTick", ex);
            }
        }

        // ══════════════════════════════════════════════════════════════
        // 记录与排空
        // ══════════════════════════════════════════════════════════════

        /// <summary>
        /// 组装一行事件并入队（**纯内存，不做 I/O** —— 见类注释的观测者效应）。
        ///
        /// ## ★ 同名键守卫（v0.8.56 加，因为真踩过一次）
        ///
        /// 本函数在最外层写 `"seq"/"utc"/"type"/"day"` 四个键，而 `body` 由各处理器拼装。
        /// 若某个处理器也写了同名键（实测：`OnSettlementOwnerChanged` 曾把聚落类型
        /// 写成 `"type"`），就会产出**同一对象里两个 `"type"`** 的 JSON。
        /// 危害是**静默**的：JSON 后者覆盖前者，`json.loads` 取最后一个 ⇒
        /// 事件类型被改写成 `town`/`castle`，按 type 过滤/统计**全部失效**
        /// （实测落盘 59856 行里 101 行中招，统计里冒出 `castle:35, town:66`）。
        ///
        /// ⇒ 这里做一次**廉价自检**：扫 body 里是否出现 `"seq":`/`"utc":`/`"type":`/`"day":`
        ///   这四个**顶层键名**。命中就改写行的 `"type"` 为 `"observer_bug_duplicate_key"`
        ///   并附上原始类型 —— **宁可让事件类型显眼地错，也不让它静默地错**
        ///   （这正是本项目"绝不静默失败"纪律的直接应用）。
        /// </summary>
        private static void Record(string type, string body)
        {
            if (!Enabled) return;
            try
            {
                // ── 同名键自检（见上面注释）──────────────────────────────
                if (!string.IsNullOrEmpty(body))
                {
                    string[] reserved = { "\"seq\":", "\"utc\":", "\"type\":", "\"day\":" };
                    for (int i = 0; i < reserved.Length; i++)
                    {
                        if (body.IndexOf(reserved[i], StringComparison.Ordinal) >= 0)
                        {
                            // 把原始类型塞进 body 以便定位，然后换成显眼的哨兵类型
                            string warned = type;
                            type = "observer_bug_duplicate_key";
                            body = "\"originalType\":" + Protocol.Q(warned)
                                   + ",\"duplicateKey\":" + Protocol.Q(reserved[i])
                                   + "," + body;
                            break;
                        }
                    }
                }

                long seq;
                lock (_lock)
                {
                    _seq++;
                    seq = _seq;
                    long c;
                    _byType.TryGetValue(type, out c);
                    _byType[type] = c + 1;
                }

                StringBuilder sb = new StringBuilder(256);
                sb.Append("{\"seq\":").Append(seq.ToString(CultureInfo.InvariantCulture));
                sb.Append(",\"utc\":\"").Append(Jw.UtcNow()).Append('"');
                sb.Append(",\"type\":").Append(Protocol.Q(type));
                sb.Append(",\"day\":").Append(
                    Campaign.Current != null
                        ? CampaignTime.Now.ToDays.ToString("F4", CultureInfo.InvariantCulture)
                        : "null");
                if (!string.IsNullOrEmpty(body)) sb.Append(',').Append(body);
                sb.Append('}');
                string line = sb.ToString();

                lock (_lock)
                {
                    _pending.Enqueue(line);
                    _events.Add(line);
                    if (_events.Count > MaxEvents)
                    {
                        // 丢最旧的，并**如实计数** —— 绝不让外部把"丢了"当成"没发生"
                        int over = _events.Count - MaxEvents;
                        _events.RemoveRange(0, over);
                        _dropped += over;
                    }
                }
            }
            catch (Exception ex)
            {
                NoteInternalError("Record", ex);
            }
        }

        /// <summary>内部故障记录（不递归进 Record，直接走 RGL+SIDECAR，防自激）。</summary>
        private static void NoteInternalError(string where, Exception ex)
        {
            try
            {
                ActionLedger.ExceptionToRgl("CampaignObserver." + where, ex);
            }
            catch
            {
            }
        }

        /// <summary>
        /// 把待写队列排空到 observer 日志（**由 `OnApplicationTick` 调用**）。
        /// 单帧限量，避免快进时写盘拖慢游戏。
        /// </summary>
        internal static void DrainPending()
        {
            try
            {
                if (!Enabled) return;
                EnsureWriter();
                if (!Jw.ObserverOpen) return;
                int n = 0;
                while (n < MaxDrainPerTick)
                {
                    string line;
                    lock (_lock)
                    {
                        if (_pending.Count == 0) break;
                        line = _pending.Dequeue();
                    }
                    Jw.WriteObserver(line);
                    n++;
                }
            }
            catch (Exception ex)
            {
                NoteInternalError("DrainPending", ex);
            }
        }

        private static void EnsureWriter()
        {
            try
            {
                if (_writerOpened && Jw.ObserverOpen) return;
                string dir = BridgeConfig.LogDir;
                if (string.IsNullOrEmpty(dir)) return;
                string path = Path.Combine(dir, "campaign_events.jsonl");
                string got = Jw.OpenObserver(path, true);
                _writerOpened = true;
                _writerPath = got;
            }
            catch (Exception ex)
            {
                NoteInternalError("EnsureWriter", ex);
            }
        }

        // ══════════════════════════════════════════════════════════════
        // 查询接口（供 CommandPump 调用）
        // ══════════════════════════════════════════════════════════════

        internal static string StatusJson()
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                lock (_lock)
                {
                    sb.Append("{\"ok\":true");
                    sb.Append(",\"enabled\":").Append(Jw.B(Enabled));
                    sb.Append(",\"verboseAi\":").Append(Jw.B(VerboseAi));
                    sb.Append(",\"maxEvents\":").Append(MaxEvents.ToString(CultureInfo.InvariantCulture));
                    sb.Append(",\"recorded\":").Append(_seq.ToString(CultureInfo.InvariantCulture));
                    sb.Append(",\"inBuffer\":").Append(_events.Count.ToString(CultureInfo.InvariantCulture));
                    sb.Append(",\"droppedFromBuffer\":").Append(_dropped.ToString(CultureInfo.InvariantCulture));
                    sb.Append(",\"pendingWrite\":").Append(_pending.Count.ToString(CultureInfo.InvariantCulture));
                    sb.Append(",\"writerOpen\":").Append(Jw.B(Jw.ObserverOpen));
                    sb.Append(",\"writerPath\":").Append(Protocol.Q(_writerPath ?? ""));
                    sb.Append(",\"writerError\":").Append(Protocol.Q(Jw.ObserverLastError ?? ""));
                    sb.Append(",\"baselineParties\":").Append(_lastBehavior.Count.ToString(CultureInfo.InvariantCulture));

                    sb.Append(",\"counts\":{");
                    bool first = true;
                    foreach (KeyValuePair<string, long> kv in _byType)
                    {
                        if (!first) sb.Append(',');
                        first = false;
                        sb.Append(Protocol.Q(kv.Key)).Append(':').Append(kv.Value.ToString(CultureInfo.InvariantCulture));
                    }
                    sb.Append('}');

                    sb.Append(",\"subscribed\":[");
                    string[] subs = {
                        "MapEventStarted", "MapEventEnded", "OnSiegeEventStartedEvent",
                        "OnSettlementOwnerChangedEvent", "WarDeclared", "MakePeace",
                        "MobilePartyDestroyed", "AiHourlyTickEvent", "OnGameLoadedEvent"
                    };
                    for (int i = 0; i < subs.Length; i++)
                    {
                        if (i > 0) sb.Append(',');
                        sb.Append(Protocol.Q(subs[i]));
                    }
                    sb.Append(']');
                    sb.Append('}');
                }
                return sb.ToString();
            }
            catch (Exception ex)
            {
                return "{\"ok\":false,\"error\":" + Protocol.Q(ex.GetType().Name + ": " + ex.Message) + "}";
            }
        }

        /// <summary>
        /// 取最近事件。**返回原始 JSON 行**（不转述、不裁剪字段）—— 照本项目
        /// "每项必须给工具原始返回"的取证纪律。
        /// 过滤：type（可选，逗号分隔多个）、limit（默认 50，上限 500）、
        /// sinceSeq（只看 seq &gt; 它的）。
        /// </summary>
        internal static string EventsJson(string typeFilter, int limit, long sinceSeq)
        {
            try
            {
                if (limit <= 0) limit = 50;
                if (limit > 500) limit = 500;
                HashSet<string> want = null;
                if (!string.IsNullOrEmpty(typeFilter))
                {
                    want = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                    string[] parts = typeFilter.Split(',');
                    for (int i = 0; i < parts.Length; i++)
                    {
                        string t = parts[i].Trim();
                        if (t.Length > 0) want.Add(t);
                    }
                    if (want.Count == 0) want = null;
                }

                List<string> picked = new List<string>();
                long dropped;
                long seq;
                int inBuffer;
                lock (_lock)
                {
                    dropped = _dropped;
                    seq = _seq;
                    inBuffer = _events.Count;
                    // 从后往前找最近的 limit 条（"最近"才是诊断要看的那一端）
                    for (int i = _events.Count - 1; i >= 0 && picked.Count < limit; i--)
                    {
                        string line = _events[i];
                        if (sinceSeq > 0 && SeqOf(line) <= sinceSeq) break;
                        if (want != null && !want.Contains(TypeOf(line))) continue;
                        picked.Add(line);
                    }
                }
                picked.Reverse();       // 输出按时间正序，便于人读

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"ok\":true");
                sb.Append(",\"returned\":").Append(picked.Count.ToString(CultureInfo.InvariantCulture));
                sb.Append(",\"recorded\":").Append(seq.ToString(CultureInfo.InvariantCulture));
                sb.Append(",\"inBuffer\":").Append(inBuffer.ToString(CultureInfo.InvariantCulture));
                sb.Append(",\"droppedFromBuffer\":").Append(dropped.ToString(CultureInfo.InvariantCulture));
                sb.Append(",\"filter\":").Append(Protocol.Q(typeFilter ?? ""));
                sb.Append(",\"events\":[");
                for (int i = 0; i < picked.Count; i++)
                {
                    if (i > 0) sb.Append(',');
                    sb.Append(picked[i]);
                }
                sb.Append("]}");
                return sb.ToString();
            }
            catch (Exception ex)
            {
                return "{\"ok\":false,\"error\":" + Protocol.Q(ex.GetType().Name + ": " + ex.Message) + "}";
            }
        }

        internal static string Clear()
        {
            try
            {
                long had;
                lock (_lock)
                {
                    had = _events.Count;
                    _events.Clear();
                    _pending.Clear();
                    _lastBehavior.Clear();
                }
                return "{\"ok\":true,\"cleared\":" + had.ToString(CultureInfo.InvariantCulture)
                       + ",\"note\":" + Protocol.Q("缓冲已清空；累计计数与 dropped 保留（它们是本次会话的事实）") + "}";
            }
            catch (Exception ex)
            {
                return "{\"ok\":false,\"error\":" + Protocol.Q(ex.GetType().Name + ": " + ex.Message) + "}";
            }
        }

        internal static string Configure(bool? enabled, bool? verboseAi, int? maxEvents)
        {
            try
            {
                if (enabled.HasValue) Enabled = enabled.Value;
                if (verboseAi.HasValue) VerboseAi = verboseAi.Value;
                if (maxEvents.HasValue && maxEvents.Value > 0) MaxEvents = maxEvents.Value;
                if (Enabled) EnsureWriter();
                return "{\"ok\":true,\"enabled\":" + Jw.B(Enabled)
                       + ",\"verboseAi\":" + Jw.B(VerboseAi)
                       + ",\"maxEvents\":" + MaxEvents.ToString(CultureInfo.InvariantCulture) + "}";
            }
            catch (Exception ex)
            {
                return "{\"ok\":false,\"error\":" + Protocol.Q(ex.GetType().Name + ": " + ex.Message) + "}";
            }
        }

        /// <summary>模块卸载：关掉 observer 写手（"删模块即完全回退"的一部分）。</summary>
        internal static void Shutdown()
        {
            try
            {
                DrainPending();
                Jw.CloseObserver();
            }
            catch
            {
            }
        }

        // ══════════════════════════════════════════════════════════════
        // 反射取值工具（照 CampaignReadProbe 的既有做法）
        // ══════════════════════════════════════════════════════════════
        //
        // 为什么用反射而不是直接调属性：事件里拿到的对象可能是**已部分销毁**的
        // （尤其 MapEventEnded 之后），直接访问成员属性有抛异常的风险。
        // 反射 + 兜底 null ⇒ 读不到就是 null，**绝不让观测把游戏搞崩**。
        //
        // ⚠️ 真机抓到的第三个坑（v0.8.51）：**显式接口实现（explicit interface
        //   implementation）用 GetProperty 在具体类型上永远找不到**。
        //
        //   实证：`Kingdom` 里写的是
        //       `bool IFaction.IsKingdomFaction => true;`   （Kingdom.cs:188）
        //   —— 它**不是** public 实例属性，而是显式实现。于是
        //   `typeof(Kingdom).GetProperty("IsKingdomFaction", Public|Instance)`
        //   **返回 null** ⇒ 事件里 `isKingdom` 恒为 `false`（看着像"不是王国"，
        //   其实是读不到）。
        //
        //   ⇒ 判据：`GetProperty` miss 时，还要在**接口**上找一遍
        //     （先 `GetInterfaces()`，再逐接口 `GetProperty`），找到后 `Invoke`。
        //     这条对所有"显式实现"的成员都适用（IFaction 里还有若干同类）。

        private static object P(object o, string name)
        {
            if (o == null || string.IsNullOrEmpty(name)) return null;
            try
            {
                Type t = o.GetType();
                PropertyInfo pi = t.GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
                if (pi != null) { try { return pi.GetValue(o, null); } catch { } }

                // ★ 显式接口实现：`bool IFaction.IsKingdomFaction => true;`
                //   在具体类型上 GetProperty 找不到 ⇒ 去接口上找。
                Type[] ifaces = t.GetInterfaces();
                for (int i = 0; i < ifaces.Length; i++)
                {
                    PropertyInfo ip = ifaces[i].GetProperty(name,
                        BindingFlags.Public | BindingFlags.Instance);
                    if (ip == null) continue;
                    try
                    {
                        object v = ip.GetValue(o, null);
                        if (v != null) return v;
                    }
                    catch
                    {
                    }
                }

                FieldInfo fi = t.GetField(name, BindingFlags.Public | BindingFlags.Instance);
                if (fi != null) { try { return fi.GetValue(o); } catch { } }
            }
            catch
            {
            }
            return null;
        }

        private static string SafeStr(object o, string name)
        {
            try
            {
                object v = P(o, name);
                if (v == null) return "";
                return v.ToString();
            }
            catch
            {
                return "";
            }
        }

        private static bool BoolP(object o, string name)
        {
            try
            {
                object v = P(o, name);
                return v is bool && (bool)v;
            }
            catch
            {
                return false;
            }
        }

        /// <summary>读嵌套数字：NumP(obj, "Position", "X") ⇒ obj.Position.X</summary>
        private static string NumP(object o, string outer, string inner)
        {
            try
            {
                object a = P(o, outer);
                object b = P(a, inner);
                if (b == null) return "null";
                return Convert.ToDouble(b, CultureInfo.InvariantCulture)
                    .ToString("R", CultureInfo.InvariantCulture);
            }
            catch
            {
                return "null";
            }
        }

        private static string NumOf(object o, string name)
        {
            try
            {
                object v = P(o, name);
                if (v == null) return "null";
                return Convert.ToDouble(v, CultureInfo.InvariantCulture)
                    .ToString("R", CultureInfo.InvariantCulture);
            }
            catch
            {
                return "null";
            }
        }

        private static string IntOf(object o, string name)
        {
            try
            {
                object v = P(o, name);
                if (v == null) return "null";
                return Convert.ToInt64(v, CultureInfo.InvariantCulture)
                    .ToString(CultureInfo.InvariantCulture);
            }
            catch
            {
                return "null";
            }
        }

        private static string NameOfSettlement(object settlement)
        {
            try
            {
                if (settlement == null) return "null";
                string n = SafeStr(settlement, "Name");
                if (string.IsNullOrEmpty(n)) n = SafeStr(settlement, "StringId");
                return Protocol.Q(n);
            }
            catch
            {
                return "null";
            }
        }

        private static string SettlementOwner(object settlement)
        {
            try
            {
                if (settlement == null) return "";
                object owner = P(settlement, "OwnerClan");
                if (owner == null) owner = P(settlement, "MapFaction");
                string n = SafeStr(owner, "Name");
                if (string.IsNullOrEmpty(n)) n = SafeStr(owner, "StringId");
                return n;
            }
            catch
            {
                return "";
            }
        }

        private static string SettlementType(object settlement)
        {
            try
            {
                if (settlement == null) return "";
                if (BoolP(settlement, "IsTown")) return "town";
                if (BoolP(settlement, "IsCastle")) return "castle";
                if (BoolP(settlement, "IsVillage")) return "village";
                if (BoolP(settlement, "IsHideout")) return "hideout";
                return "";
            }
            catch
            {
                return "";
            }
        }

        /// <summary>队伍简报：名字 + 类型 + 阵营 —— 用于"是哪支队伍"的辨认。</summary>
        private static string PartyBrief(object party)
        {
            try
            {
                if (party == null) return "null";
                // PartyBase 与 MobileParty 都要能处理
                object mp = party;
                string name = SafeStr(party, "Name");
                if (string.IsNullOrEmpty(name))
                {
                    object m = P(party, "MobileParty");
                    if (m != null) { mp = m; name = SafeStr(m, "Name"); }
                }
                if (string.IsNullOrEmpty(name)) name = SafeStr(party, "StringId");

                string kind = "";
                if (BoolP(mp, "IsMainParty")) kind = "player";
                else if (BoolP(mp, "IsLordParty")) kind = "lord";
                else if (BoolP(mp, "IsBandit") || BoolP(mp, "IsBanditBossParty")) kind = "bandit";
                else if (BoolP(mp, "IsCaravan")) kind = "caravan";
                else if (BoolP(mp, "IsVillager")) kind = "villager";
                else if (BoolP(mp, "IsGarrison")) kind = "garrison";
                else if (BoolP(mp, "IsMilitia")) kind = "militia";
                else if (BoolP(mp, "IsPatrolParty")) kind = "patrol";

                string faction = "";
                object f = P(mp, "MapFaction");
                if (f == null) f = P(party, "MapFaction");
                if (f != null)
                {
                    faction = SafeStr(f, "Name");
                    if (string.IsNullOrEmpty(faction)) faction = SafeStr(f, "StringId");
                }

                StringBuilder sb = new StringBuilder();
                sb.Append("{\"name\":").Append(Protocol.Q(name));
                sb.Append(",\"kind\":").Append(Protocol.Q(kind));
                sb.Append(",\"faction\":").Append(Protocol.Q(faction));
                object leader = P(mp, "LeaderHero");
                sb.Append(",\"leader\":").Append(Protocol.Q(SafeStr(leader, "Name")));
                sb.Append('}');
                return sb.ToString();
            }
            catch
            {
                return "null";
            }
        }

        private static string HeroBrief(object hero)
        {
            try
            {
                if (hero == null) return "null";
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"name\":").Append(Protocol.Q(SafeStr(hero, "Name")));
                sb.Append(",\"clan\":").Append(Protocol.Q(SafeStr(P(hero, "Clan"), "Name")));
                sb.Append(",\"kingdom\":").Append(Protocol.Q(SafeStr(P(hero, "Kingdom"), "Name")));
                sb.Append('}');
                return sb.ToString();
            }
            catch
            {
                return "null";
            }
        }

        private static string FactionBrief(object faction)
        {
            try
            {
                if (faction == null) return "null";
                StringBuilder sb = new StringBuilder();
                sb.Append("{\"name\":").Append(Protocol.Q(SafeStr(faction, "Name")));
                sb.Append(",\"isKingdom\":").Append(Jw.B(BoolP(faction, "IsKingdomFaction")));
                sb.Append('}');
                return sb.ToString();
            }
            catch
            {
                return "null";
            }
        }

        /// <summary>玩家是否卷入这场 MapEvent（用于过滤"和我有关的事件"）。</summary>
        private static bool PlayerInvolved(MapEvent mapEvent)
        {
            try
            {
                if (mapEvent == null) return false;
                // ⚠️ `PartyBase` **没有** `IsMainParty` 实例属性（只有静态 `PartyBase.MainParty`）；
                //    `IsMainParty` 是 `MobileParty` 的。两者不能混用 —— 编译期就会拦下
                //    （本文件第一版就是这么挂的）。
                PartyBase main = PartyBase.MainParty;
                if (main == null) return false;
                if (mapEvent.AttackerSide != null && mapEvent.AttackerSide.LeaderParty == main) return true;
                if (mapEvent.DefenderSide != null && mapEvent.DefenderSide.LeaderParty == main) return true;

                // 也检查是否作为**附属**加入（玩家的军团成员参战）
                object parties = P(mapEvent, "InvolvedParties");
                System.Collections.IEnumerable en = parties as System.Collections.IEnumerable;
                if (en != null)
                {
                    foreach (object item in en)
                    {
                        if (item == null) continue;
                        if (ReferenceEquals(item, main)) return true;
                        object m = P(item, "MobileParty");
                        if (m != null && BoolP(m, "IsMainParty")) return true;
                    }
                }
                return false;
            }
            catch
            {
                return false;
            }
        }

        /// <summary>从已渲染的 JSON 行里取 seq（避免为过滤再解析整个对象）。</summary>
        private static long SeqOf(string line)
        {
            try
            {
                // Jmini 只有 Num（无 Long）⇒ 用 Num 再转。seq 远小于 2^53，精度无忧。
                return (long)Jmini.Num(line, "seq", 0);
            }
            catch
            {
                return 0;
            }
        }

        /// <summary>从已渲染的 JSON 行里取 type。</summary>
        private static string TypeOf(string line)
        {
            try
            {
                return Jmini.Str(line, "type", "");
            }
            catch
            {
                return "";
            }
        }
    }
}
