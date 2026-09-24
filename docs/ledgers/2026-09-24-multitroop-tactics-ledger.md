# SDD ledger — plan: docs/superpowers/plans/2026-09-24-multitroop-tactics-plan.md

- **BASE（开工前 HEAD）**：`ab05caa2fc21a3de4eaa8c93b141854e69eabff7`
- **分支**：`feat/multitroop-tactics`（无远端，收尾时合回 main）
- **执行模式**：⚠️ 降级为「批量 + checkpoint」（本会话无 `task` 子代理工具）；保留 brief/report、
  ledger、每任务双阶段自审、GC1–GC6 对照；实现者＝主会话本人。

## 进度

| 任务 | 状态 | commit | 备注 |
|---|---|---|---|
| T1 | ✅ complete | `025b5f5` | `bl_common.parse_squad_groups` + 15 条断言 |
| T2 | ✅ complete | `6fc2850` | plan 支持 `attackerGroups`/`defenderGroups` |
| T3 | ✅ complete | `2715817` | `bl_cmd` 两个新参数 + 本地校验（断言总数 236） |
| T4 | ✅ complete | `8efabe9`+`e23c030` | `src/SquadSpec.cs` + jsontest ⑨ 节；含行尾修正 |
| T5 | 🔧 已裁决=路 A | — | 自定义 `IMissionTroopSupplier`；**计划已修订并入库** |
| T6 | pending | | 依赖 T5 的实现 |
| T7 | pending | | 遥测落点 + `EnumNames.Formation`（不依赖 T5） |
| T8 | pending | | 0.8.8 + 部署（需游戏关闭） |
| T9 | pending | | 游戏内 5 条判据 |
| T10 | pending | | 文档 + 示例 plan |

## Task 5: 裁决记录（2026-09-24 23:50 → 用户选路 A）

**发现**：计划 T5 的前提与实际不符 —— 两队由**引擎** `DefaultBattleMissionAgentSpawnLogic`
（`ScenarioRunner.cs:460`）生成，不是我们逐组 `SpawnAgent`。⇒ 记 BLOCKED 上报。

**用户裁决**：**路 A** —— 自定义 `IMissionTroopSupplier`（每方一个）按组轮转提供兵种/数量。

**裁决后取证（三接口完整签名）**：
- `IMissionTroopSupplier`（7）：`NumRemovedTroops` / `NumTroopsNotSupplied` / `AnyTroopRemainsToBeSupplied` /
  `SupplyTroops(int)→IEnumerable<IAgentOriginBase>` / `SupplyOneTroop()→IAgentOriginBase` /
  `GetAllTroops()` / `GetGeneralCharacter()` / `GetNumberOfPlayerControllableTroops()`
- `IBattleCombatant`（8）：`Name`/`Side`/`BasicCulture`/`General`/`PrimaryColorPair`/`Banner`/
  `GetTacticsSkillAmount()`/`GetNumberOfMissionReadyTroops()`/`IsUnderPlayersCommand(side)`
- `IAgentOriginBase`（18）：`Troop`/`Banner`/`Seed`/`UniqueSeed`/`HasShield`/`HasSpear`/`HasThrownWeapon`/
  `HasHeavyArmor`/`FactionColor(2)`/`BattleCombatant`/`SetWounded|Killed|Routed`/`OnAgentRemoved`/`OnScoreHit`
  ⇒ **没有任何 formation 成员**

**⇒ 能力边界（写进计划修订）**：
1. ✅ 多兵种混编**可做**（supplier 按组轮转返回不同 `Troop` 的 origin）
2. ✅ 编队**自动正确**（引擎按 `BasicCharacterObject.GetFormationClass()` 分配：弓手→Ranged、骑兵→Cavalry…）
3. ❌ **指定 formation 做不到** ⇒ GC1 的 `formation` **降级为"校验 + 记录实际编队"**（GC4 观测量不受影响）
   · 备选（未采用）：改 `CharacterObject.GetFormationClass()` 是 `virtual`，但那会污染共享的
     `CharacterObject`（全局副作用）⇒ 不可取；真要"指定编队"只能走**路 B**（自己 spawn，风险高）。

---

## Task 5: 执行前裁决与取证（2026-09-25，controller = Reasonix 新会话）

- **BASE（本会话开工前 HEAD）**：`37fe0c9920c56649447de41182c097ab6ef603ac`（分支 `feat/multitroop-tactics`，工作区干净）
- **计划内部矛盾（已处置，不静默）**：计划 §T5 的「前提修订」块（路 A + formation 不可下发）与紧随其后的旧「规格」段
  （仍写 `AgentBuildData.Formation` / `Mission.SpawnAgent` / 位置分段 / `SetArrangementOrder`）冲突。
  ⇒ 以「前提修订块 + 用户裁决」为准：旧规格三条作废（self-spawn / 位置分段 / 指定编队），保留「命令按组下发」；
  未给 movement 的组按 `charge`（取代旧规格的"保持引擎默认"）。详见 `task-5-brief.md` §3。
- **用户裁决 A（movement 落点）**：按"该组兵种的实际编队"下发；同一实际编队被多组以不同 movement 命中 ⇒ **报错中止**（GC3 风格）；
  groups 与 `orders != "charge"` 不得混用 ⇒ 报错。
- **用户裁决 B（本轮范围）**：做到 T7（代码 + 编译验证）+ T8（0.8.8 版本号 + 部署）；T9 交用户在游戏内验。
- **本轮取证（T5 实现依据，均为引擎侧实证）**：
  - `CustomBattleTroopSupplier` public、非 sealed，`SupplyTroops`/`SupplyOneTroop` 非 virtual ⇒ 只能组合，不能覆盖；
  - `CustomBattleAgentOrigin` 的 `troopSupplier` 参数类型是**具体类** `CustomBattleTroopSupplier` ⇒ 自建 supplier 必须转发组级实例；
  - `CustomBattleMissionSpawnHandler.AfterStart()` 用 `combatant.NumberOfHealthyMembers` 定 total/initial spawn ⇒ 整体 combatant 必须含全部组；
  - `IAgentOriginBase` 的 18 个成员无 formation、无 team ⇒ GC1 的 formation 降级成立。
- **执行模式**：本会话**有** `task` 子代理工具 ⇒ 按 sdd 标准模式（fresh 实现者 + 双阶段 review）。
## Task 5: complete (commit `37fe0c9..ab5c3a3`, review clean)

- **实现**（fresh 子代理）：`src/ScenarioRunner.cs` **+501 / -0**（0 deletions ⇒ GC2 旧路径逐字节不变）；
  `SquadTroopSupplier`（按组轮转，组级 `CustomBattleCombatant` + 组级 `CustomBattleTroopSupplier` 组合转发）、
  `Start()` 组校验（`bad_groups`/`unknown_troop`/`conflicting_movements`/`conflicting_orders`/`unsupported_rounds`）、
  `OpenMissionGrouped`、`ScenarioProbe.ApplyOrders`（按**实际编队**下发 movement）、`StatusJson` 新增 3 字段。
- **fix round 1/5**：M1（groups×`rounds>1` 静默退化 ⇒ 护栏 `unsupported_rounds`）、M2（冲突判定改用映射后落点，
  `hold`/`stop` 不再互判冲突）、`hold` 降级可观测、m1（首组未知也带组号）、m2（错误消息带逐字 segment 原文）、
  m3（删死参数）、m4（`TryLoadBaseObjects` 循环内最多一次）、nit×3。**fix round 2/5**：nit-1（catch 改追加）、
  nit-2（hold 提示移入新字段 `OrderNotes`，不再污染 `lastError`）、nit-3（`AppendOrderNote` 幂等去重）；
  nit-4 已知不修（消息已含逐字 segment，改动会扩大面）。
- **两轮 scoped re-review 均通过**（无 blocker/major）；controller **独立重跑**三条验证：
  `build.ps1` EXIT=0（73.5 KB，仅既有 `RoundOrchestratorBehavior.cs(62,22) CS0414` 警告）、
  `tools/jsontest/build_and_run.ps1` 全绿 EXIT=0、`python tools/bl_selftest.py` EXIT=0（236 OK / 0 FAIL）；
  `git diff --stat` 与 `--ignore-cr-at-eol --stat` 一致。
- **遗留（已上报，非静默）**：① DSL 的 `hold` 与 `stop` 在引擎层同落点 `MovementOrderStop`
  （`MovementOrder` 只暴露 Charge/Retreat/Stop/Advance/FallBack/Null，ctor 全 private）⇒ 建议保持"保留 DSL 六词 + 可观测降级"，
  待用户最终裁决；② 组级 combatant 作为 `origin.BattleCombatant` 的引擎用途待 **T9 游戏内**验证。
## Task 6: complete (commit `ab5c3a3..<HEAD>`, review clean)

- **实现**（fresh 子代理）：`src/RoundOrchestratorBehavior.cs` + `src/ScenarioRunner.cs`，
  合计 **148 insertions / 18 deletions**（删除项 = T5 的 7 行护栏 + 2 行签名重写 + 5 行旧路径语句被等价搬进 if/else，
  行为等价，GC2 成立——已由审查者逐行核对 `git diff -U0`）。
- 要点：按组重生（swap 时两组列表与 troop 列表一起互换；位置分段 `x = base.x + i*12m`，基点 null 交回引擎默认）；
  每轮重申复用 `ScenarioProbe.ApplyOrders`（无逻辑复制）；`round_spawn_group_skipped` 事件（跳过组不再静默）；
  `OnRemoveBehavior` 复位 4 个新字段；`AppendOrderError` 幂等；`Spawn()` doc 明写"第 2 轮起无 `IAgentOriginBase`"。
- **fix round 1/5**：minor1（口径写进代码注释）、minor2（合并重复 `<summary>`）、minor3（跳过组写 JSONL 事件）、
  nit4（删冗余赋值）、nit5（类头文案）、nit6（幂等）。
- **审查**：初轮无 blocker/major（3 minor + 3 nit）；scoped re-review 6/6 真修、无回归 ⇒ 可提交。
- **controller 独立重跑**：`build.ps1` EXIT=0（75 KB，仅既有 CS0414）、jsontest 全绿、`bl_selftest.py` EXIT=0；
  行尾 `--ignore-cr-at-eol` 一致。
- **并发事实（重要）**：工作区同时有**另一个任务**在改 `tools/bl_mcp.py`、`tools/bl_selftest.py`，并新增 `tools/bl_sage.py`
  ⇒ 本任务提交**只 add 两个 `.cs`**（严禁 `git add -A`）；外部改动未碰、未回退、未提交。
- **遗留**：`hold → MovementOrderStop`（待用户最终裁决）；组级 combatant 作 `origin.BattleCombatant` 的引擎用途待 T9；
  第 2 轮起无 origin ⇒ T7 的 `spawned` 口径必须从 `Team.ActiveAgents` 统计。
## Task 7: complete (commit `20e8d1e..8af3989`, review clean)

- **实现**：5 文件 **261 insertions / 14 deletions**（`EnumNames.Formation` 按真值写死；`unit.formation`；
  `squad` 事件两处触发；`bl_dummy_analyze --by formation`）。
- **fix round 1/5**：minor-1（`squad` 事件两侧各自 try/catch，一侧失败不影响另一侧）、nit-1（`FormationOfActor` catch 简化）、
  nit-3（删除死字段 `_started` ⇒ **编译警告归零**）、report 写死两条口径（缺字段兜底取既有 `(空)`；GC2 精确口径）。
- **如实记录未修**：minor-2（首轮 `spawned` 时机 = loading→running，可能早于 supplier 供完 origin ⇒ 交 T9 实测核对）、nit-2、nit-4。
- **审查**：初轮无 blocker/major（3 minor + 4 nit）；scoped re-review 三条真修、无回归 ⇒ 可提交。
- **controller 独立重跑**：`build.ps1` EXIT=0（**0 警告**）、jsontest 全绿、`bl_selftest.py` EXIT=0；行尾一致。
- **事实性修正**：brief 引用的 `Formation.FormationIndex` 在本机 DLL **不存在** ⇒ 实际用 `Formation.LogicalClass`（已反射取证）。

## Task 8: complete (commit `8af3989..d2c8623`；单行版本号 + 部署，无需双阶段审查)

- `src/BridgeConfig.cs` `Version` → `0.8.8`；`module/SubModule.xml` → `v0.8.8`。
- `build.ps1 -Deploy` EXIT=0（**部署前后各确认一次游戏未运行**）；`out\BlBridge.dll` 与
  `Modules\BlBridge\bin\Win64_Shipping_Client\BlBridge.dll` 的 sha256 **逐字一致**
  = `0EBB07D236007785D3F881F9AE63F7F72133F207ED0897877B6105A6D751A352`。
- `python tools/bl_cmd.py buildcheck` EXIT=0：`code=game_offline`、文件链条一致、`builtVersion=0.8.8`、
  `deployedSha256=0ebb07d236007785`、`fileChangedSinceLoad=False`。
- 待 T9：游戏内 `loadedSha256` / `loadedVersion` 应为本次 sha / 0.8.8。

## 本会话收尾状态（2026-09-25，controller = Reasonix 新会话）

- **已完成并提交**：**T5 / T6 / T7 / T8** = `ab5c3a3` / `20e8d1e` / `8af3989` / `d2c8623`；
  四个任务均经双阶段审查（T5/T6/T7 各含 1–2 轮修复 + scoped re-review），controller 每轮独立重跑三条验证。
- **未完成**：**T9**（游戏内 5 条判据，需游戏开着由用户跑）、**T10**（README/PROGRESS/示例 plan，依赖 T9 结果）。
- **分支终审未派独立子代理**：sdd 的终审属"全部任务完成后"的动作，而本分支 DoD 含 T9/T10 ⇒ 留待 T9/T10 之后。
- **计划文档已同步勘误**（§T5 规格段三处作废 + movement/hold 裁决），避免下个会话按旧规格实现。
- **并发事实（重要）**：本会话期间有**另一个任务**在并行改 `PROGRESS.md`、`tools/bl_batch.py`、`tools/bl_cmd.py`、
  `tools/bl_mcp.py`、`tools/bl_selftest.py` 并新增 `tools/bl_sage.py`；本会话**只 `git add` 自己的文件**，
  未碰/未回退/未提交外部改动（外部改动至今仍未提交，属那个任务的收尾范畴）。
- **遗留待用户最终裁决**：DSL 的 `hold` 是否保留（现落 `MovementOrderStop`，已在 `OrderNotes` 可读回）。
## Task 10: 部分完成 —— docs + 示例 plan（判据结果待 T9；commit `b775b08..7fc8157`）

- **产物**：新建 `tools/plan.multitroop.example.json`（多兵种/战术组示例：攻方 2 组 `hold`+`charge`、守方 1 组）；
  `README.md`（§二 工具清单 +1 行；§七 JSONL 事件格式补 `unit.formation` 与新增 `squad` 事件的字段/口径 ——
  逐条对照 `src/` 实现核对过）；**`PROGRESS.md` 的新增节改写入 `.sdd/.../task-10-progress-section.md`**
  （另一任务正在改 `PROGRESS.md`，避免连带提交别人的半成品；待其收工后由 controller 并入）。
- **验证**：`bl_batch.py --plan tools/plan.multitroop.example.json --dry-run` EXIT=0 且含
  `--attacker-groups` / `--defender-groups`（两组都在）；`bl_selftest.py` EXIT=0；行尾一致（README 未变 CRLF）。
- **审查**：文档与代码逐条对照，**无 blocker/major**；唯一 minor（草稿 §6 写 `12m`）已修，
  并顺手修掉 `src/RoundOrchestratorBehavior.cs` 注释里的同款笔误（2 处 `12m` → `12f`，纯注释、行为无关，
  单独一个 commit `0d00585`，改后 `build.ps1` 仍 EXIT=0）。
- **未完成（依赖 T9）**：T9 五条判据的实测结果（草稿里全部 `（待 T9）`，未编造数字）⇒ 并入 PROGRESS 时补齐。

## 会话收尾（第二轮，2026-09-25）

- 已提交：T5 `ab5c3a3` / T6 `20e8d1e` / T7 `8af3989` / T8 `d2c8623` / 计划勘误 `b775b08` /
  注释修正 `0d00585` / T10 docs `7fc8157`。
- **未完成**：**T9**（游戏内 5 条判据，需游戏开着由用户执行）、`PROGRESS.md` 的并入（待外部任务收工）。
- **分支终审与 `finish-branch` 均未做**：DoD 含 T9（游戏内 5 判据 + 旧 plan 回归）⇒ 分支尚不能算完成。
- **外部并发**：`PROGRESS.md`、`tools/bl_batch.py`、`tools/bl_cmd.py`、`tools/bl_mcp.py`、`tools/bl_selftest.py`
  与新增 `tools/bl_sage.py` 仍未提交（属另一任务，疑似 "BlBridge MCP ↔ bannerlordsage 联动"）；本会话只 `git add` 自己的文件。
- **待用户裁决（不阻塞）**：DSL 的 `hold` 是否保留（现落 `MovementOrderStop`、`OrderNotes` 可读回；默认保留）。
## 附加：BlBridge MCP ↔ BannerlordSage 联动（兵种 id 前置校验）—— 验证与收尾（2026-09-25）

- **来源**：另一个会话实现（未提交）；用户要求验证 ⇒ Reasonix 实测 + 入库前审查 + 修复 + 收尾提交 `6693653`。
- **实测（我，端到端）**：Sage 索引可用（`F:\Program Files\BannerlordSage\...\bannerlord.db`，634 MB，**1981 兵种**）；
  CLI `bl_sage --status/--check/--search` 正常；**MCP `bl_lookup_troop`** 的 status/check/search 正常；
  `bl_start_battle` 用错 id **在发命令之前**被拦（返回 missing + 近似候选 + hint，未发命令）；
  `bl_selftest.py` EXIT=0（新断言：`tools/list` = **16 工具**、`bl_lookup_troop` 已注册）。
- **入库前审查（独立子代理）**：无 blocker；**MAJOR 1** —— `bl_batch.py` 的预检把「被 `*Groups` 忽略的单值兵种」
  也校验 ⇒ **同侧双给时误拦整批**（我建的 `plan.multitroop.example.json` 正是该写法，只因单值恰好合法才未暴露）；
  已与 `bl_cmd.py` 语义对齐修复。另修 5 条 minor（死导入 `difflib`、`search` 的 `limit` 非法值钳位、
  `check_troops` 传 str 逐字符、空 ids 的误导性警告、culture 大小写写进文档）。
- **行尾**：新建 `tools/bl_sage.py` 原为 CRLF（仓库其余 Python 全 LF，`core.autocrlf=false`、无 `.gitattributes`）
  ⇒ 统一为 LF（内容逐字未变，已校验等价）。
- **边界复验（我）**：示例 plan `--dry-run` EXIT=0（含 `--attacker-groups` / `--defender-groups`）；
  双给 + 单值非法 ⇒ **EXIT=0 放行**；groups 内非法 ⇒ **EXIT=1 中止**；临时 plan 用完即删。
- **遗留 minor（不阻塞，已记录）**：① 工具文件顶层 `import bl_sage` 造成"对同仓文件的硬依赖"（风险低）；
  ② `culture` 仍大小写敏感（已文档化）；③ `bl_lookup_troop` 的 `search` 需 **LIKE 通配**（`imperial_%`），
  裸词返回空 ⇒ 待其作者决定是否自动包 `%…%`（我未擅自改行为）。

## PROGRESS.md 并入（commit `9cbb462`）

- T10 草稿并入为 **§十六**；修掉「另一任务插入的 §十四（Sage 校验）与既有 §十四（材质对照）撞号」
  ⇒ 后者顺延 **§十五**（只调编号，内容未改）。
- 至此**工作区干净**（`git status` 空），分支 `feat/multitroop-tactics` HEAD = `9cbb462`。
## Task 11: complete —— 从 DSL 移除 `hold`（用户 2026-09-25 批准；commit `<HEAD>`）

- **决策**：按编程规则（接口正交性 / Fail Fast=GC3 / 数据语义）删除 `hold`（与 `stop` 同落 `MovementOrderStop`）；
  发布前最后低成本窗口（v0.8.8 未发布、无历史 plan 依赖）。
- **实现**（fresh 子代理）：11 个文件 +71/-64；C#/Python 词表各收为 5 词 + 两套补位提示；
  **连带清理**：删 `OrderNotes`/`AppendOrderNote`/`StatusJson().orderNotes`（hold 降级是其唯一用户），`AppendOrderError` 保留。
- **fix round 1/5**：minor-1 **弱断言**（原用子串 `stop`，而通用错误消息本就列了 stop ⇒ 提示缺失也会假绿）
  ⇒ 改为断言 `已移除`，并**自证有效**（注入"无提示"⇒ Python/C# 两条断言都变红 ⇒ 恢复源文件）；
  minor-2（计划文档 GC1 示例行 `:17` 未同步 `hold`⇒`stop`）；minor-3 未改（无把握不改，仅措辞）。
- **审查**：无 blocker/major（3 minor）；实跑 `bl_selftest`/`GuardTest`/`bl_batch --dry-run` 全 EXIT=0。
- **controller 独立复验**：`build.ps1` EXIT=0、jsontest 全绿、`bl_selftest.py` EXIT=0、示例 dry-run EXIT=0（movement=stop）、
  **hold/HOLD/Hold 三变体都抛 ValueError 且消息含「已移除…请改用 stop」**；行尾 `--ignore-cr-at-eol` 一致。
- **历史规格边界**：计划文档 `:58`(T1)/`:116-117`(T4)/`:190`(T7) 保持不动（勘误块统管）；PROGRESS `b775b08` 历史描述保留。
- **待办**：T9（游戏内 5 条判据）仍未执行 ⇒ 判据 4 的观测对象从 `hold` 组改为 `stop` 组（行为预期不变：都不发冲锋）。
## T11 收尾：重新部署（2026-09-25）

- T11 改了 C#（`SquadSpec.cs` / `ScenarioRunner.cs` / `RoundOrchestratorBehavior.cs`）⇒ T8 部署的 DLL 已**过时**，
  在确认游戏未运行后重新 `build.ps1 -Deploy`：
  **新 sha256 = `531AAB84FA844C2AC7934989D13D68F42DCEB11B0FCE517733349352F9F02AD2`**
  （`out\BlBridge.dll` 与 `Modules\BlBridge\bin\Win64_Shipping_Client\BlBridge.dll` 逐字一致）。
- `python tools/bl_cmd.py buildcheck` EXIT=0：`code=game_offline`、`deployedSha256=531aab84fa844c2a`、
  `fileChangedSinceLoad=False`、`builtVersion=0.8.8`；`loadedSha256/loadedVersion` 仍是上次**进程内**记录
  （`3eb3b767183e04fb` / `0.8.7`）—— **T9 开游戏后应变为 `531aab84…` / `0.8.8`**。
- ⇒ **T9 判据 1 的期望值更新为 `531AAB84FA844C2AC7934989D13D68F42DCEB11B0FCE517733349352F9F02AD2` / `0.8.8`**。
- 分支 `feat/multitroop-tactics` 现有 11 个提交，HEAD = `706b88f`，工作区干净。
## 重新部署（用户要求，2026-09-25 00:07）

- 游戏未运行 ⇒ `build.ps1 -Deploy` EXIT=0；`out\BlBridge.dll` 与模块目录 DLL 的 sha256 逐字一致
  = `19F66AD1C637BC54BED14FA0E0CC05678B7552A355F0C10B0F564DF71AE62B89`。
- **新事实（重要）**：同一份**未改动**源码，两次 `-Deploy` 得到**不同** sha（上一轮 `531AAB84…` ⇒ 本次 `19F66AD1…`）
  —— Roslyn 命令行默认**非确定性**（嵌 MVID / PE 时间戳）。
  ⇒ 任何"把 dll sha 写进文档当判据"的做法都会在下次重编译时过期；**判据应改用 `bl_cmd.py buildcheck` 的
  `deployedSha256`**（它给当前部署值 + `fileChangedSinceLoad`）。已写进 PROGRESS §十六 的 T9 判据 1（commit 见下）。
- `buildcheck` EXIT=0：`deployedSha256=19f66ad1c637bc54`、`fileChangedSinceLoad=False`、`builtVersion=0.8.8`；
  `loadedSha256`/`loadedVersion` 仍是上次**进程内**记录（`3eb3b767…`/`0.8.7`）⇒ T9 开游戏后应变为本次 sha / 0.8.8。
## Task 12: complete —— 修 T9 游戏内实测暴露的 `TypeInitializationException`（commit 见下）

- **现象**：`bl_cmd.py start --attacker-groups …` ⇒ `handler_exception: TypeInitializationException:
  The type initializer for 'TaleWorlds.MountAndBlade.MovementOrder' threw an exception.`
  （`MapMovement` ← `ValidateGroupedSide` ← `Start()`）。
- **根因**：`MovementOrder` 是 struct，静态字段在**类型初始化**时构造实例，而 `Start()` 跑在 **mission 之外**
  ⇒ 访问即抛；且 .NET 把该类型**永久标记不可用** ⇒ 同进程内后续访问都抛（连旧路径 `ApplyCharge` 也坏）。
  T5 的 M2 修复（"按落点比较"提前到 Start）才首次踩到 ⇒ **离线编译/单测发现不了，只有 T9 能暴露**。
- **修法**：新增**纯字符串** `MovementKey(string)` 承担冲突校验的落点比较，彻底不碰 `MovementOrder`；
  错误码/消息/逐字回显/`resolvedTroops` 逐字未变；`MapMovement` doc 补"只允许 mission 内调用"。
  T11 移除 hold 后 5 词与 5 落点一一对应 ⇒ 字符串比较 ≡ 落点比较（语义零变化）。
- **验证**：`build.ps1` EXIT=0、jsontest 全绿、`bl_selftest.py` EXIT=0、行尾一致；
  全仓 `MovementOrder` 出现点逐条自查 ⇒ **已无 mission 之外的执行点**。
- **待办（下一步）**：**关游戏 → 重新部署 → 重开游戏**后重跑 T9 判据 2–5（本机无法离线确认）。
  注意：当前游戏进程的 `MovementOrder` 类型已被污染 ⇒ **必须重启游戏**，否则重跑仍会抛。
## T12 部署（2026-09-25 00:13，游戏已关闭后执行）

- `build.ps1 -Deploy` EXIT=0：**新 sha = `2183A94B94BBCF1175F2DDFAB21E3E01F1BA0D28B9E6C143FCA7C0CB5BE3B295`**
  （`out\BlBridge.dll` 与 `Modules\BlBridge\bin\Win64_Shipping_Client\BlBridge.dll` 逐字一致）。
- `bl_cmd.py buildcheck`：`code=game_offline`、`builtVersion=0.8.8`、`deployedSha256=2183a94b94bbcf11`、
  `loadedSha256=19f66ad1c637bc54`（上次进程内记录，游戏未运行）。
- ⚠️ 重编译又换了一次 sha（dll 非确定性，见上文）⇒ **T9 判据 1 仍以 `buildcheck` 的 `deployedSha256` 为准**（本次 = `2183a94b…`）。
- 下一步：等用户重开游戏（进「自定义战斗」界面）后重跑 **T9 判据 2–5**（判据 1 一并复核新 sha）。
## T9 判定：✅ 五条判据全部通过（2026-09-25 00:15–00:17，游戏内实测）

| # | 判据 | 结果与证据 |
|---|---|---|
| 1 | 进程内身份 | ✅ `buildcheck`: `code=ok`、`builtVersion=0.8.8`、`deployedSha256 = loadedSha256 = 2183a94b94bbcf11`、`loadedVersion=0.8.8`、`fileChangedSinceLoad=False` |
| 2 | 2 组 plan ⇒ 每组一行 `squad`、`spawned` 正确 | ✅ `battle_20260925_001508_814.jsonl` 共 **3 条** `squad`（攻方 2 组 + 守方 1 组）：`imperial_legionary` count=10 Infantry **stop spawned=10**、`khuzait_khans_guard` count=5 HorseArcher charge **spawned=5**、`battanian_wildling` count=15 charge spawned=15 |
| 3 | `unit.formation` 与 `squad` 一致 | ✅ `unit` 计数：`Attacker/Infantry=10`、`Attacker/HorseArcher=5`、`Defender/Infantry=15` |
| 4 | 行为可辨（stop 组不发冲锋） | ✅ 开局 10 mission 秒位移均值：**stop 组 0.00 m（max 0.00、均速 0.00）** vs charge 组 **86.01 m**（均速 8.55）；守方 charge 21.03 m |
| 5 | GC2 回归（旧路径不变） | ✅ 单兵种 20v20：`squad=0`、`bad lines=0`、`verdict=ok`、`nanCount=0`、`ioFailed=false`、`reason=defenderWiped`（正常结束） |

- ⚠️ 判据 2 的**首跑**（T12 修复前）抛 `TypeInitializationException` ⇒ 已修（`dba3d01`）并重新部署（`2183A94B…`）+ 重开游戏后全过。
- ⚠️ 判据 5 与计划原文的差异：计划写"`squad` 事件为单组"，**实测 0 条**（无 groups ⇒ 不产生新事件）—— 比原文更严格。
- **DoD 状态**：`bl_selftest.py` EXIT=0、`tools/jsontest` 全绿、`build.ps1 -Deploy` 成功、`buildcheck` 一致、**T9 五条判据全过** ⇒ **本分支 DoD 已闭环**。
- 下一步：`/finish-branch`（本仓无远端，合并即本地合回 `main`）。
## Task 13: 修「按组下发的 movement 对守方不生效」（commit 见下）—— 待游戏内复测

- **发现（游戏内同场对照，2026-09-25）**：同一场战斗、同配置，唯一差异是攻/守位 ⇒
  攻方 `imperial_legionary`(10, **stop**) **全程 0.00 m/s**（生效）；守方 `battanian_wildling`(10, **stop**) **全程 1.1–2.0 m/s、总位移 179 m**（不生效）。
  `first hit t=11.08` + `sample t=10` 双方满血 ⇒ 移动发生在**未接战期**（不是被打散）。
  另两场独立数据一致（官方 100v100 守方 stop 组自 t=2s 起 ~2 m/s；15v15 确诊场守方 stop 组全程 ~2.1 m/s、位移 164.8 m）。
  ⇒ **T9 判据 4 漏掉此缺陷**，因为它把 stop 组放在攻方。
- **根因假设**：`ApplyOrders` 只在 `AfterStart` 设一次 order，而 team 级 `TacticCharge` 会周期性重申 `Charge` 覆盖之。
- **修法（`src/ScenarioRunner.cs`，组路径专属 +150 行）**：每 0.5s 重申各组 order（`ReapplyGroupOrders`）+
  每 5s 写 `order` 观测事件（编队 + `MovementOrder.OrderEnum`）；旧路径无 groups 时两个钩子都立即返回（GC2 自证）。
- **API 取证**：`Formation.GetMovementOrder()` 不存在 ⇒ `GetReadonlyMovementOrderReference()`；
  `Team.TacticOptions`/`Tactic` 不存在 ⇒ `tactic` 字段恒 `"none"`（order 本身足以判断是否被覆盖，不值得为观测引入私有字段反射）。
- **验证**：`build.ps1` OK(79KB)、jsontest 全绿、`bl_selftest.py` EXIT=0、行尾一致。
- **待办**：关游戏 → 重新部署 → 重开游戏 → 复测（同场对照 + 100v100），并用 `order` 事件确认根因。

## 本轮（100v100 / MOD 兵种）的其它结论（2026-09-25）

- **官方兵种 100v100**：6 组（攻 40 advance/40 stop/20 charge、守 40 stop/40 advance/20 charge）⇒ `squad` 6 条、
  `unit` 计数逐一对应、`spawned` 40/40/20、`verdict=ok`、`nanCount=0`；攻方全灭（dAlive 81）。
- **MOD 兵种 100v100（首次打通 `--skip-troop-check` 实战）**：MOD = `WarlordsBattlefieldWarSailsEdition`
  （`ModuleData/major_army_*.xml` 等，1239 个 NPCCharacter 定义；`aserai_infantry5` / `aserai_archer4` /
  `aserai_cavalry4` / `battania_*` 系）⇒ 6 条 `squad`、`unit` 一致、`spawned` 精确、`verdict=ok`、`nanCount=0`。
  ⇒ **Sage 索引不含 MOD 兵种**（`empire_caravan_master1` 在 MOD XML 里却报 missing）⇒ `bl_lookup_troop` 的
  `--skip-troop-check` / `skipTroopCheck` 出口**实测有效**；游戏端能正常识别并生成 MOD 兵种。
- ⚠️ **可观测性限制**：`state`/`ai` 采样**不是全量** —— 100v100（200 agent）只覆盖约 69 个 agent
  （且集中在守方的 Infantry/Ranged），攻方与骑兵组无采样 ⇒ **大规模的"每组位移"无法用现值证明**；
  小规模（15v15 及以下）为全量覆盖 ⇒ 行为验证应在小规模做。
## Task 14: unknown_troop 一次报出所有坏兵种 id（commit `3a1e1b1` 前后见 git log）

- **动机（游戏内实测的教训）**：全兵种扫描（3200 个 id 分批）时靠"只报第一个坏 id"+ 反复二分 `start`/`abort` 找全，
  实测把引擎打进**异常状态**（下述）。
- **修法**：`ValidateGroupedSide` 收集所有 Resolve 失败条目（1 基组号 + 逐字 segment + id），
  失败组跳过 formation/冲突校验，循环后一次性报 `unknown_troop` 并中止（GC3 不变）。
  新消息：`<label>有 <n> 个兵种 id 不存在：第 N 组 'xxx:1'（xxx）、…`（>8 条列前 8 + " 等 n 个"）。
- **验证**：`build.ps1` OK(79KB)、jsontest 全绿、`bl_selftest.py` EXIT=0、行尾一致；函数依赖引擎 `Resolve` ⇒ 离线不可单测。

## 全兵种扫描（3200 个兵种：官方 1981 + MOD 1239）—— 进行中，本轮受阻

- **清单来源**：官方 = BannerlordSage 索引（`bannerlord_troops.characterId`，1981 条，**含大量非战斗 NPC**：
  lord/caravan_master/armorer/artisan/`*_template_*`/`tutorial_*`/`crazy_man_test*` 等）；
  MOD = `WarlordsBattlefieldWarSailsEdition` 的 `ModuleData/**.xml`（1239 个 NPCCharacter）。
  去重后 **3200**；分 11 批（每批每方 150 ⇒ 一场 300 人），plan 在 `.sdd/.../plan.alltroops-sweep.json`。
- **✅ 真发现（游戏端 `unknown_troop`，一次报第一个）**：`lord_A9_c`、`npc_gang_leader_equipment_aserai`、
  `spc_notable_vlandia_rural_2`、`tutorial_npc_basic_melee` ⇒ **Sage 索引有、游戏端不可 spawn** 的条目确实存在。
- **❌ 两次受阻**：
  1. `bl_cmd.py batch` **不认 plan 顶层 `skipTroopCheck`**（那是 `bl_batch.py` 的键）⇒ 11 批全部 `start_failed`；
     改用自带 runner 直跑 `start --skip-troop-check` 才行。
  2. 自写的**二分探测脚本有 bug**：把 `busy` 当成"兵种不存在"⇒ 好兵种（`imperial_legionary` 等）被误判为坏 id；
     且反复 `start`/`abort` **把引擎打进异常状态**：
     **mission 卡在 `state=loading`、`busy=true`，`abort` 返回 `aborted:true` 但状态 4 分钟以上纹丝不动** ⇒ 只能重启游戏。
     （该现象已记录为待查：abort 从 loading 拉不回来。）
- **下一步**：重启游戏（顺便部署 T13+T14）⇒ 用 T14 的"一次报全部坏 id"跑扫描，拿到完整坏 id 清单，
  再用干净清单跑覆盖扫描，统计 `unit` 事件缺口（真正"没 spawn 出来"的兵种）。
## 全兵种可用性判定（3200 个 id）—— 结论（2026-09-25）

- **方法纠正**：**不需要打 11 场战斗**。一次 `start` 校验就够（T14 修复后 `unknown_troop` 一次报全、失败秒回不建 mission）。
  把 3200 个 id 分 4 包（每包 800）分别校验。
- **结果：941 个游戏端不认 / 2259 个可用**：

  | chunk | 不认的 id 数 | 样本 |
  |---|---|---|
  | 0..800 | 403 | `anti_imperial_conspiracy_boss`、`betting_fraud_thug_female` |
  | 800..1600 | 518 | `lord_4_7`、`lord_5_1` |
  | 1600..2400 | 20 | `steppe_bandits_raider`、`storymode_imperial_mentor_arzagos` |
  | 2400..3200 | **0（全合法）** | — |

- **性质：全部是"非战斗 NPC"**（`lord_*` / `*_bandits_*` / `storymode_*` / `*_thug_*` / `*_conspiracy_*` /
  `*_contender*` / `*_template*` / `beggar_*` / `*_merchant*` / `*_caravan_*` / `armorer_*` / `artisan_*` …）
  ⇒ **不是"兵种 bug"，而是"清单来源问题"**：BannerlordSage 索引（1981 条）把非战斗 NPC 也算作 troop。
  ⇒ 给 MCP 联动任务的建议：`bl_sage` / `bl_lookup_troop` 的说明里应写明"**索引含非战斗条目，不等于可 spawn 的兵种**"。
- ⚠️ **复现问题（第二次）**：一次成功 `start` 之后的 `abort` **无法把 mission 从 `loading` 拉回来**
  （`state=loading` / `busy=true`，`abort` 报 `aborted:true` 但状态不变，只能重启）。本轮后续 probe 全被 `busy` 拒绝即由此而来。
  ⇒ 待查（可能与"超大编队 + 立即 abort"有关）。
- **剩余待做**：重启游戏后，用 2259 个**可用** id 跑覆盖扫描（按 `unit` 事件比对）⇒ 找"通过校验但没真正 spawn 出来"的兵种（那才是真正的兵种 bug）。