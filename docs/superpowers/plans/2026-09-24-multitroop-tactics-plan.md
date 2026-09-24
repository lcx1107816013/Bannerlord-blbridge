# BlBridge 多兵种混编 + 战术命令 · 实施计划（2026-09-24）

> 设计已由用户批准（2026-09-24 晚间对话）。本文件是执行期**唯一需求来源**：任务 brief 从它抽取，审查对照它进行。
> **不许在本计划里夹带新设计决策**；发现设计漏洞 ⇒ 回到设计讨论，不得自行改设计。

## Global Constraints（硬约束 —— 后续每个任务与审查都以此为准）

- **GC1 DSL 格式（定死）**：`<troop>:<count>[:<formation>[:<movement>]]`，多组用 `|` 分隔。
  - `troop`：兵种 id（**不做本地校验**，由游戏端报 `unknown_troop`，与 `--dummy-body-item` 同策略）
  - `count`：整数 ≥ 1
  - `formation`：`FormationClass` 的引擎名，**大小写不敏感**，取值只允许：
    `Infantry | Ranged | Cavalry | HorseArcher | Skirmisher | HeavyInfantry | LightCavalry | HeavyCavalry | General | Bodyguard`
    ⚠️ **修订（2026-09-24 23:5x，用户批准路 A）**：`IAgentOriginBase` 无 formation 成员、
    编队由 `BasicCharacterObject.GetFormationClass()` 按兵种决定 ⇒ 该字段**不能下发**，
    语义降级为"**解析时校验 + 日志里记录实际编队**"（GC4 的观测量不受影响）。
  - `movement`：`charge | advance | hold | fallback | stop | retreat`（小写；映射见 T4）
  - 示例：`imperial_legionary:10:Infantry:hold|khuzait_khans_guard:5:HorseArcher:charge`
  - 缺省：省略 `formation` ⇒ 引擎默认编队；省略 `movement` ⇒ `charge`
- **GC2 向后兼容**：旧 plan 字段 `attacker`/`a`/`defender`/`d` 的行为**逐字节不变**
  （等价于单组：`formation` 缺省、`movement=charge`）。旧 plan 必须仍能跑出与今天一致的结果。
- **GC3 非法即报错、绝不静默**：DSL 字段数 ∉ {2,3,4}、`count` 非正整数、未知 `formation`/`movement`
  ⇒ **抛错并中止跑批**，错误信息里带上出错的那一组原文。这是本项目最贵教训（参数静默丢弃曾让 9 场实验作废）。
- **GC4 可观测落点（每个新参数都要能被读回）**：
  1. `unit` 事件新增 **`formation`** 字段（`FormationClass` 的**真名**，见 T7 别名坑）；
  2. 新增 **`squad`** 事件，每组一行（字段见 T7）；
  3. 分析侧必须能按 `formation` 分组（`bl_dummy_analyze --by` 加一项）。
- **GC5 工程约束**：零 Harmony；`python tools/bl_selftest.py` 必须 **EXIT=0**；改后须
  `build.ps1 -Deploy`（**游戏必须关闭**）且 `bl_cmd.py buildcheck` 文件链条一致。
- **GC6 版本**：`src/BridgeConfig.cs` 的 `Version` 与 `module/SubModule.xml` → **0.8.8**（build.ps1 会自动同步后者）。

## 任务清单（按序执行）

| # | 任务 | 文件 | 依赖 |
|---|---|---|---|
| T1 | Python 侧 DSL 解析纯函数 + 断言 | `tools/bl_common.py`、`tools/bl_selftest.py` | — |
| T2 | `bl_batch` plan 支持 `attackerGroups`/`defenderGroups` | `tools/bl_batch.py`、`tools/bl_selftest.py` | T1 |
| T3 | `bl_cmd.py` 新增 `--attacker-groups`/`--defender-groups` | `tools/bl_cmd.py`、`tools/bl_selftest.py` | T1 |
| T4 | C# 侧 `SquadSpec` 解析 + 离线单测 | `src/SquadSpec.cs`、`tools/jsontest/` | — |
| T5 | `ScenarioRunner` 按组建队/编队/下命令 | `src/ScenarioRunner.cs` | T4 |
| T6 | `RoundOrchestratorBehavior` 重生与命令重申按组 | `src/RoundOrchestratorBehavior.cs` | T4 |
| T7 | 遥测落点：`unit.formation` + `squad` 事件 + 别名映射 | `src/TelemetryBehavior.cs`、`src/EnumNames.cs` | T5、T6 |
| T8 | 版本 0.8.8 + 编译部署 + buildcheck | `src/BridgeConfig.cs` | T7 |
| T9 | 游戏内验证（判据见下） | （无） | T8 |
| T10 | 文档与示例 plan | `README.md`、`PROGRESS.md`、`tools/plan.multitroop.example.json` | T9 |

---

## T1 · Python 侧 DSL 解析纯函数 + 断言

**文件**：`tools/bl_common.py`（新增函数）、`tools/bl_selftest.py`（新增断言）

**规格**：

```python
# bl_common.py
SQUAD_FORMATIONS = ("Infantry", "Ranged", "Cavalry", "HorseArcher", "Skirmisher",
                    "HeavyInfantry", "LightCavalry", "HeavyCavalry", "General", "Bodyguard")
SQUAD_MOVEMENTS = ("charge", "advance", "hold", "fallback", "stop", "retreat")

def parse_squad_groups(text):
    """把 DSL 拆成 [{"troop","count","formation","movement"}]。
    非法输入一律 raise ValueError（GC3：绝不静默丢弃）。
    formation/movement 缺省时为 None（由 C# 侧/CLI 侧再填默认）。"""
```

- 规则：按 `|` 切组；每组按 `:` 切字段，长度 2~4；`troop` 非空；`count` 必须能 `int()` 且 ≥1；
  `formation` 大小写不敏感地匹配 `SQUAD_FORMATIONS`（回写为**规范名**）；`movement` 小写匹配 `SQUAD_MOVEMENTS`。
- 报错示例（照抄这个风格）：`ValueError("attackerGroups 解析失败：第 2 组 %r 的 count 不是正整数（应为 troop:count[:formation[:movement]]）" % part)`

**验证**：`python tools/bl_selftest.py` ⇒ 新增断言至少覆盖：
① 合法四字段组；② 合法两字段（缺省）组；③ 多组 `|`；④ `formation` 大小写不敏感（`infantry` → `Infantry`）；
⑤ 非法：字段数 1 与 5、`count=0`、`count=abc`、未知 formation、未知 movement —— 每种都必须 `ValueError`。
期望：`EXIT=0`。

## T2 · `bl_batch` plan 支持 `attackerGroups`/`defenderGroups`

**文件**：`tools/bl_batch.py`、`tools/bl_selftest.py`

**规格**：
- `DUMMY_PLAN_KEYS` 之外新增 `SQUAD_PLAN_KEYS = ("attackerGroups", "defenderGroups")`（复用 `resolve_dummy_params(plan, cfg, keys)`）。
- 值必须是**字符串**（DSL）或**组列表**（每项 dict 含 `troop`/`count`/`formation`/`movement`）；
  是列表时先转成 DSL 字符串再走 T1 校验（保证只有一条解析路径）。
- 生成 CLI 参数：`--attacker-groups <dsl>` / `--defender-groups <dsl>`。
- **GC2**：若 `cfg` 既没有 `attackerGroups` 也没有 plan 级默认 ⇒ `build_start_args` 的输出
  **必须与改造前逐字符相同**（现有断言即为回归网）。

**验证**：`bl_selftest.py` 新增断言：① 组列表 → DSL 字符串正确；② 非法 DSL ⇒ `ValueError`；
③ **旧字段回归**：用今天 `tools/plan.mirror.example.json` 的 config 产出，断言与既有期望一致（现有 8 条断言不许改）。
期望：`EXIT=0`。

## T3 · `bl_cmd.py` 新增 `--attacker-groups`/`--defender-groups`

**文件**：`tools/bl_cmd.py`、`tools/bl_selftest.py`

**规格**：新增两个字符串参数；给了就写进 `params["attackerGroups"]` / `params["defenderGroups"]`（**原样字符串**，
不做二次翻译）；给了 `--attacker-groups` 时**允许**同时给 `--attacker`/`--a`（前者优先，且 `--attacker` 的值被忽略——
在 docstring 里写明）。解析失败在 CLI 层就 `raise`（非 0 退出）。

**验证**：`bl_selftest.py` 断言：① 参数进 `params`；② 非法 DSL ⇒ 非 0 退出且 stderr/stdout 含可读原因。
期望：`EXIT=0`。

## T4 · C# 侧 `SquadSpec` 解析 + 离线单测

**文件**：新建 `src/SquadSpec.cs`；单测加到 `tools/jsontest/`（与 Jmini 等同一套离线断言）

**规格**：
```csharp
internal sealed class SquadSpec {
    public string Troop; public int Count; public string Formation; public string Movement;
    // 解析 attackerGroups/defenderGroups 的字符串（GC1 语法），非法抛 ArgumentException
    public static List<SquadSpec> Parse(string dsl);
}
```
- 解析用 `string.Split('|')` + `Split(':')`（**不要**用 Jmini —— 它只读裸值，GC1 已说明）。
- `Formation` 存**规范名**（与 T1 同一张表，大小写不敏感）；`Movement` 存小写。
- 映射表（**写死**）：`charge/advance/hold/fallback/stop/retreat`
  → `MovementOrder.MovementOrderCharge / MovementOrderAdvance / MovementOrderHold / MovementOrderFallBack / MovementOrderStop / MovementOrderRetreat`
  （若某个静态实例名不存在 ⇒ 用 `new MovementOrder(MovementOrder.MovementOrderEnum.X)`；**编译期即验证**）。
- 非法输入（字段数、count、未知 formation/movement）⇒ `throw new ArgumentException("...")`，
  且**不得吞异常**（与 GC3 一致）。

**验证**：`tools/jsontest/` 新增断言（合法/非法各若干，含大小写不敏感）；编译通过即证明枚举名正确。
期望：jsontest 全绿 + `build.ps1`（不部署）成功。

## T5 · `ScenarioRunner` 按组建队/编队/下命令（**2026-09-24 23:5x 修订：改走自定义 troop supplier**）

> ⚠️ **前提修订**：原规格假设"我们逐组 `Mission.SpawnAgent` + `AgentBuildData.Formation`"，
> 但取证表明两队是**引擎生成**的（`ScenarioRunner.cs:438` `MissionState.OpenNew` → `:458`
> `MissionCombatantsLogic` → **`:460` `DefaultBattleMissionAgentSpawnLogic(_pendingSuppliers, …)`**；
> 注释 `:462-467` 说明 `InitWithSinglePhase` 才是唯一创建 spawn phase 的地方）。
> ⇒ 改为**用户已批准的路 A**：自定义 `IMissionTroopSupplier`（每方一个），按组轮转提供兵种/数量。
>
> ⚠️ **能力边界（同批取证）**：`IAgentOriginBase` 的 18 个成员里**没有 formation**
> ⇒ **无法指定编队**（编队由 `BasicCharacterObject.GetFormationClass()` 按兵种决定，那是引擎 virtual，
> 改它会污染共享 `CharacterObject`）⇒ GC1 的 `formation` 已降级为"校验 + 记录实际编队"。
>
> ⚠️ **GC2 提醒**：原规格末尾"未给命令的组保持引擎默认"仍成立，但 `ApplyCharge(team)` 的既有行为
> **必须原样保留**给"没有 groups"的旧路径。


**文件**：`src/ScenarioRunner.cs`

> ⚠️ **勘误（2026-09-25，本节「规格」段有三处已作废）**：下面的「规格」写于改用路 A **之前**，与上方「前提修订」块冲突。
> 实际执行以 `task-5-brief.md`（sdd 执行期 brief，在 `.sdd/2026-09-24-multitroop-tactics-plan/`）+ 用户裁决为准：
> ① ~~`AgentBuildData.Formation(f)` + 逐组 `Mission.SpawnAgent`~~ **作废** —— 两队由**引擎**经 `IMissionTroopSupplier` 生成；
> ② ~~按组顺序分段分配 spawn 位置、把偏移量写进实现~~ **作废** —— 位置由引擎 deployment plan 决定（多轮重生才有位置分段，见 §T6 的 `x = base.x + i*12m`）；
> ③ ~~`SetArrangementOrder` / 指定编队~~ **作废** —— `IAgentOriginBase` 无 formation 成员，编队由 `BasicCharacterObject.GetFormationClass()` 决定。
> **movement 落点（用户裁决）**：按「该组兵种的**实际编队**」下发；同一实际编队被多组以不同 movement 命中 ⇒ **报错中止**；
> `groups` 与 `orders != "charge"` 不得混用 ⇒ 报错。
> **`hold` 落点（引擎能力边界）**：`MovementOrder` 只暴露 Charge/Retreat/Stop/Advance/FallBack/Null，且 ctor 全 private
> ⇒ `hold` 落 `MovementOrderStop`（等价 `stop`），并在 `OrderNotes` 里留可读回提示。
> **是否保留 DSL 的 `hold` 一词仍待用户最终裁决**（保留 = 现状、零改动；移除 = 需同步改 GC1 + T1/T4 与 Python 断言）。
> 执行记录（T5–T8 提交 `ab5c3a3` / `20e8d1e` / `8af3989` / `d2c8623`）见 `.sdd/2026-09-24-multitroop-tactics-plan/progress.md`。

**规格**（⚠️ 以下三条已作废，见上方勘误）：
- 新增静态字段 `AttackerGroups`/`DefenderGroups`（`List<SquadSpec>`，缺省 null ⇒ 走旧路径，GC2）。
- 建队时：为每个 spec 取目标 `Formation` —— 用 `team.GetFormation(FormationClass.X)`
  （`FormationClass` 在 `TaleWorlds.Core`，需 `using TaleWorlds.Core;`）；
  `Formation` 缺省时用引擎默认（`AgentBuildData.SpawnsUsingOwnTroopClass(true)` 的既有行为）。
- spawn 循环：`new AgentBuildData(character).Team(team).Formation(f).InitialPosition(pos)` ⇒ `Mission.SpawnAgent`。
  位置分配：沿既有 spawn 点的排布规则，按组**顺序分段**（第 g 组的起点 = 起点 + g × 组偏移；**确切偏移量写在实现里并记进 PROGRESS**）。
- 命令：把现有 `ApplyCharge(team)` 泛化为 `ApplyOrder(team, specs)` —— 先 `team.ClearTacticOptions()`，
  再对每个 spec 的 formation 依次 `SetArrangementOrder`（若有）与 `SetMovementOrder`；
  **未给命令的组保持引擎默认**（不要默认下 Charge —— 那会改变旧行为）。

**验证**：`build.ps1`（不部署）成功；写一个 2 组 plan 由 T8 之后的游戏内验证覆盖。

## T6 · `RoundOrchestratorBehavior` 重生与命令重申按组

**文件**：`src/RoundOrchestratorBehavior.cs`

**规格**：`Respawn`（现约 160-210 行）与 `ApplyCharge`（现约 213-224 行）改为按 `SquadSpec` 列表：
按组重生（同样的 formation/位置规则）并在每轮开始时重申各组的 arrangement/movement；
`--round-swap` 时**两组列表互换**（与现有 `SwapSides` 语义一致，GC2 不变）。

**验证**：`build.ps1` 成功；多轮行为由 T9 覆盖（判据：`squad` 事件在每轮都出现一次）。

## T7 · 遥测落点：`unit.formation` + `squad` 事件 + 别名映射

**文件**：`src/TelemetryBehavior.cs`、`src/EnumNames.cs`

**规格**：
- `EnumNames.cs` 新增 `Formation(FormationClass)` 映射：**不要用 `ToString()`** ——
  `FormationClass` 有同值别名（`NumberOfDefaultFormations = 4 = Skirmisher`、`NumberOfRegularFormations = 8 = General`、
  `NumberOfAllFormations = 10 = Unset`）⇒ `ToString()` 会返回边界名（与 §七/§八 的 `CriticalBodyPartsBegin`、
  `WeaponItemBeginSlot` 同类坑）。映射表照 `FormationClass.cs` 逐项写死。
- `unit` 事件新增 `"formation"`（用上面的映射；空编队写 `"Unset"`）。
- 新增 `squad` 事件（每组一行，**在 spawn 完成后写**）：
  `{"t":"squad","side":"Attacker|Defender","group":0,"troop":"imperial_legionary","count":10,"formation":"Infantry","movement":"hold","spawned":10}`
  其中 `spawned` = 该组实际成功的 `SpawnAgent` 次数（**验证"是否真生成了"**）。
- `bl_dummy_analyze.py` 的 `--by` 增加 `formation`（读 `unit.formation` 关联到 `dummy_hit.victim`）；
  若实现成本高，降级为"在 `--compare` 的判据区打印 `squad` 事件汇总"，但**必须**能在分析里看到 formation。

**验证**：`build.ps1` 成功；游戏内判据见 T9。

## T8 · 版本 0.8.8 + 编译部署

**文件**：`src/BridgeConfig.cs`（`Version = "0.8.8"`）

**验证**：游戏关闭时执行 `powershell -ExecutionPolicy Bypass -File .\build.ps1 -Deploy`
⇒ 输出含 `module/SubModule.xml version -> v0.8.8` 与 `dll sha256 = ...`；
`python tools/bl_cmd.py buildcheck` ⇒ 文件链条一致。

## T9 · 游戏内验证（判据）

**判据（全部要有）**：
1. `bridge_status.json` 的 `version = 0.8.8`、`loadedSha256` = 部署 sha、`fileChangedSinceLoad = false`；
2. 跑一个 **2 组**的 plan（例：攻方 `imperial_legionary:10:Infantry:hold` + `khuzait_khans_guard:5:HorseArcher:charge`）
   ⇒ 日志出现 **2 条 `squad`**，`spawned` 分别 = 10 与 5；
3. `unit` 事件的 `formation` 与 `squad` 一致（10 个 Infantry、5 个 HorseArcher）；
4. **行为可辨**：`hold` 那组在开局不发冲锋（`state` 的前若干秒位移 < 阈值）—— 与 `charge` 组对比；
5. **GC2 回归**：用**旧 plan**（`tools/plan.mirror.example.json`）跑 1 场 ⇒ `squad` 事件为单组 + `movement=charge`，
   且 `end.validity.verdict = ok`、`nanCount = 0`、坏行 0。

## T10 · 文档与示例 plan

**文件**：`README.md`（工具清单加 1 行 `plan.multitroop.example.json`）、
`PROGRESS.md`（新增一节：DSL 语法、GC1-GC6、判据结果、位置偏移量的确切值）、
新建 `tools/plan.multitroop.example.json`

**验证**：`bl_batch.py --plan tools/plan.multitroop.example.json --dry-run` 输出含
`--attacker-groups` 与 `--defender-groups`，且 `attackerGroups` 里两组都在。

---

## 完成定义（DoD）

- `python tools/bl_selftest.py` ⇒ **EXIT=0**（含新增断言，且**既有断言一条未改**）；
- `tools/jsontest/` 全绿；`build.ps1 -Deploy` 成功；`bl_cmd.py buildcheck` 一致；
- T9 的 5 条判据全部通过（**游戏内**，逐条贴证据到 PROGRESS）；
- 旧 plan 行为不变（GC2）——这是本次改造的**硬底线**。
