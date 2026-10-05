# L4：线程模型对照 —— 上游 `MainThreadDispatcher` vs 我们的 `CommandPump`

> 日期：2026-09-27　上游来源：[BUTR/Bannerlord.GABS](https://github.com/BUTR/Bannerlord.GABS)（MIT）
> 对照的四个文件：上游 `MainThreadDispatcher.cs` / `SubModule.cs`，我们 `src/CommandPump.cs` / `src/SubModule.cs`
> 纪律：每条都标「**事实层**」（代码确实如此）与「**后果层**」（这个后果真的可达吗）。看不出来后一层的，只记事实、不下结论。

---

## 0. 一句话

**不需要抄它的 dispatcher —— 我们根本没有那个问题；要抄的是它用真机换来的 2 条边界条件。**
而且我们这边有一条它没有的东西（**请求方的超时归因**），这正是"形态决定能力"的又一例。

---

## 1. 结构性差异：它需要 dispatcher，是因为它的调用方在别的线程上

| | 上游 | 我们 |
|---|---|---|
| 请求从哪里来 | GABP **TCP 后台线程**（Lib.GAB 的服务端线程） | **另一个进程**（MCP 通过 `commands/pending/*.json`） |
| 怎么回到主线程 | `ConcurrentQueue<Action>` + `EnqueueAsync<T>()` → `TaskCompletionSource`，在 `OnApplicationTick` 里 `ProcessQueue()` 排空 | 不需要 marshal：唯一的入口点 `CommandPump.Pump()` **本来就在主线程**（`OnApplicationTick`） |
| 每帧做什么 | `ProcessQueue()` → `ProcessPendingSave()` | `TimeControl.RealDt = dt` → `CommandPump.Pump()` → `ScenarioRunner.Watchdog()` |

**⇒ 结论（也是本轮最有价值的一条）**：`MainThreadDispatcher` 存在的理由是"工具处理器跑在后台线程上"。
文件 IPC 把整个问题类消掉了 —— **没有后台线程，就没有跨线程 marshal**。
这不是"我们写得好"，是**形态差异**。所以 L4 里能抄的**不是** dispatcher 本身，而是它在真机上撞出来的那几条规矩。

---

## 2. 逐条对照

### L4-1 「阻塞式操作不能放在泵循环里」——它踩过，我们也踩过（**同一条纪律的两个实例**）

- **上游事实层**：`MainThreadDispatcher.cs:71-78` 专门为存档开了一个**队列之外**的逃生口：
  > *"Schedule a save to run on the main thread OUTSIDE the dispatcher queue.
  > SaveAs blocks the main thread and deadlocks if called inside ProcessQueue."*
  `ScheduleSave()` 只登记名字，真正的 `SaveHandler.SaveAs` 在 `ProcessQueue()` **之后**由 `ProcessPendingSave()` 执行。
- **我们的同类事实**：v0.8.10 的看门狗根因就是"熔断逻辑与它要监控的回调在同一条路径上（tick 内）——
  卡死时两者一起停"。解法是 `ScenarioRunner.Watchdog()` 挂在 `OnApplicationTick`，但**独立于 `Pump()` 的循环**。
- **⇒ 可行动（零风险，纯文档）**：把这条从"注释里的经验"升级成**成文规则**：

  > 任何会**长时间阻塞主线程**的操作（存档、换场景、跑加载）**不得**在 `CommandPump.HandleOne` 内直接执行；
  > 必须"登记 + 在 `Pump()` 之后执行"（同 `ScheduleSave` / `Watchdog` 的形状）。
  > 动机不是洁癖：这类操作一卡，`Pump()` 的同帧后续动作、看门狗、下一次轮询会**一起停**。

  目前 15 个 method 里**没有**这种操作（`open_ui` / `start_battle` 是"发起"而不是"同步阻塞"），
  所以这是**预防性规则**，不是修 bug。**不要**把它当成已发生的问题写进任何报告。

- **附带一条该记住的边界（理论，未实测）**：`CommandPump.Pump()` 的 `try` 从 `EnsureDirs()` 开始，
  而 `if (!BridgeConfig.Enabled) return;` 与 `_nextPollUtc` 赋值在 `try` **之外**。
  理论上静态构造失败会抛 `TypeInitializationException`（与 `MovementOrder` 那个"类型永久不可用"同类），
  从而**跳过同一帧的 `Watchdog()`**。**没有实测、也几乎不可达**（`BridgeConfig` 是纯常量类，且加载期已写过状态文件）；
  记在这里只是"知道即可"。要消掉它只需把这几个语句也放进 `try` —— 但那要动 DLL（需重启游戏验证），
  值不值由下一轮顺手决定。

### L4-2 「异常必须传得回去」——两家都做到了，但留痕的地方不同

- **上游事实层**：`ProcessQueue()` 内层 `catch (Exception) { }` 明确吞掉，注释说明理由：
  *"Individual TaskCompletionSource callers will receive their exceptions."* ⇒ 异常经 TCS 回到调用方。
- **我们的事实层**：`CommandPump.HandleOne` 把 `Dispatch(...)` 包在 try 里，异常变成
  `Protocol.Failure(id, "handler_exception", ..., outcomeUncertain: true)` —— 并且**落盘成响应文件**。
- **⇒ 差异（对我们有利）**：同一类失败，上游只活在那次 TCP 会话里；我们**在磁盘上留了痕**，
  崩溃之后还能看到"哪次请求、什么异常"。这与文件 IPC 的立项理由（*崩溃后请求/响应都留痕可查*）是同一条。
  **形态决定能力**的又一例。

### L4-3 排空策略：两家都是"无上限"，且都一样

- 上游：`while (ExecutionQueue.TryDequeue(out var action))`。
- 我们：`Directory.GetFiles(PendingDir, "*.json")` + `Array.Sort(Ordinal)` + 逐个 `HandleOne`。
- **事实层**：两边都**不设每帧上限**。**后果层：未验证** —— 我们没有"一帧里来 N 个请求"的真实数据，
  更没有测过每个请求的最坏耗时。**按本项目的纪律，没有对照就不下结论**：这不是缺陷，只是"没测过"。
  真要测，做法是把请求批量灌进 `pending/`，量一帧的墙钟耗时（现有遥测里有 `stall_start/stall_end`，可以直接看出）。

- 顺带**核掉一个假想缺陷**：`Array.Sort(..., StringComparer.Ordinal)` 用序号排序，
  若文件名不是定长的会乱序 —— 但 MCP 侧是 `seq = "%019d" % time.time_ns()`（19 位零填充，`bl_mcp.py:452`），
  序数与字典序一致。**已核对，不是问题**（记录在此，免得以后有人重新怀疑一遍）。

### L4-4 超时归属：这一项我们更强，且是结构性的

- **上游**：`EnqueueAsync` 返回的 `Task` **没有超时**。主线程如果一直不 tick（加载界面卡住），
  task 永不完成；超时只能由**传输层**（GABP/GABS 的 `timeouts` 配置）在外部兜。调用方**分不清**
  "游戏没启动 / 进程死了 / 主线程卡住"。
- **我们**：超时归因在**请求方**，而且是四值（`bridge_not_loaded` / `process_exited` / `probe_unknown` / `no_response`），
  外加 `bl_wait_for_state` 的"卡住早退"、`sessionDiagnosis` 的崩溃/正常退出判定、`outcomeUncertain` 的不盲重试。
- **⇒ 这是"不转换形态"决策的一条硬证据**：把传输换成 GABP/TCP，这套归因**不会自动继承**，
  得在 TCP 上重做一遍。而它是我们从多次真机事故里长出来的。

### L4-5 一处纯代码观察（上游）：`TaskCompletionSource` 没有 `RunContinuationsAsynchronously`

- **事实层**：`MainThreadDispatcher.cs:33` / `:53` 的 `new TaskCompletionSource<T>()` 未传
  `TaskCreationOptions.RunContinuationsAsynchronously`。因此 `ProcessQueue()` 在主线程上 `SetResult(...)` 时，
  调用方注册的**续体可能内联在主线程上跑**（.NET 4.6+ 起官方建议加这个选项）。
- **后果层：未验证，不声称它出过问题。** 这里只留一条给未来：**如果**我们哪天真的引入一个跨线程 dispatcher，
  这一条是要避开的坑（否则续体在主线程跑，等于把"回到主线程"变成"继续占着主线程"）。
- **它对"抄实现体、不抄框架"的支撑**：这类问题只有在你**自己写** dispatcher 时才归你管；
  我们不引入 dispatcher，它就不存在。

---

## 3. 结论与行动

| # | 结论 | 行动 |
|---|---|---|
| 1 | 文件 IPC 把"跨线程 marshal"整类消掉 ⇒ **不抄 dispatcher** | 无（记录在案） |
| 2 | "阻塞操作不许放泵循环内"是两家共同的规矩，我们已有实例（watchdog） | **写成规则**（零风险，本次已写进 `AGENTS.md`） |
| 3 | 异常留痕：我们落盘、上游只活在那次会话里 | 无（已是我们的优势，记录在案） |
| 4 | 每帧排空无上限：两家同形，**未验证**是否有害 | 记入"未验证"，需要时用 `stall_start/stall_end` 测 |
| 5 | 请求方超时四值归因是我们独有 | 无（是"不转换形态"的硬证据） |
| 6 | 上游 TCS 少了 `RunContinuationsAsynchronously` | 无（只作为"将来若要 dispatcher 时的坑"记录） |
