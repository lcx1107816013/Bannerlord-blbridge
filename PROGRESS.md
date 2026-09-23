# BlBridge 进度列表

> **最后核实：2026-09-24 01:42**（本文件由 Reasonix 会话建立并维护；① 游戏内验证执行于 2026-09-24 01:40–01:41）
> 项目权威页（共享知识库）：`E:\ObsidianDocument\entities\blbridge.md`
> 上次交接快照：`E:\ObsidianDocument\raw\transcripts\reasonix-handoff-blbridge-2026-09-24.md`
> 立项理由（别忘）：**"工具把决策依据从『猜』换成了『数据』，但数据还没取"**

## 一、现状核实（本次实测，非引用旧结论）

| 项 | 值 |
|---|---|
| 版本 | **v0.7.9** |
| 是否已部署 | ✅ `G:\...\Mount & Blade II Bannerlord\Modules\BlBridge\bin\Win64_Shipping_Client\BlBridge.dll`，58368 B，sha256 `70111C2D5F4A529F…`，与项目 `out\BlBridge.dll` **逐字节一致**（2026-09-24 01:09:12） |
| 是否在游戏内跑过 | ✅ **已跑**（2026-09-24 01:40 / 01:41，0.7.9 进程）：`bridge_status.json` → `version: 0.7.9`、`state: idle`、`missionsThisSession: 1 → 2`。证据见 ① |
| 版本控制 | ✅ 本轮建立：git 基线 `7d3aadf`（分支 `main`，**无远端**，`core.autocrlf=false`） |
| 源码 ↔ 部署 一致性 | ✅ `bl_cmd.py buildcheck` 判 **文件链条一致**（`builtVersion 0.7.9`、`builtUtc 01:09:12` 本地、`deployedSha256 70111c2d…`）⇒ 游戏里的 0.7.9 **确实由当前 src 构建**，① 的验证对象有效 |
| 离线自测 | ✅ `bl_selftest.py` **全部通过**（清理残留后回归） |
| 部署目录 `BlBridge.dll.bak_*` | 保留（`build.ps1:190-207` 的**有意机制**：每次部署留备份、只保留最近 3 个）—— 不是残留，勿清 |

## 二、进度列表

状态图例：`[ ]` 未做 · `[~]` 进行中 · `[x]` 完成 · `[!]` 被阻塞

### [x] ① 0.7.9 六组遥测的游戏内验证 —— ✅ 已完成（2026-09-24 01:40 / 01:41 两场）
- **为什么**：0.7.9 的六组遥测只做过"编译通过 + 反编译静态核对"，**没有跑过真实战斗**。不验证就等于所有新数据不可信。
- **做什么**：启动游戏 → 跑一场靶场/批量 → 核对落盘。
- **验收标准**：
  1. `bridge_status.json` 的 `version` 变为 `0.7.9`（当前为 `0.7.8`），`build.fileChangedSinceLoad = false`；
  2. 新事件类型 `shot` / `state` / `ai` 均出现在 JSONL 中，且关键字段非空（`shot.velocity`、`state.MaxSpeedMultiplier`、`ai` 的 26 个参数）；
  3. `hit` 新字段（`attackDir` / `speedMod` / `atkStun`·`defStun` / `shieldHp`·`shieldMax`）有非默认值出现；
  4. 无异常/崩溃，战斗正常结束（`dummy_end` / `end` 事件存在）。
- **▶ 验证结果（2026-09-24，游戏内两场，进程 0.7.9）**

  日志：`battle_20260924_014004_626.jsonl`（2.4 MB，军团兵 20 vs 野民靶 20）与
  `battle_20260924_014105_968.jsonl`（2.1 MB，巴旦尼亚勇士弓手 20 vs 军团兵靶 20）。

  | 验收 | 结果 |
  |---|---|
  | 1. `bridge_status.json` → 0.7.9 | ✅ `version: 0.7.9`、`fileChangedSinceLoad: False` |
  | 2. `shot`/`state`/`ai` 落盘 | ✅ 359 / 4586 / 40（场 1）；弓 `Bow` 178 发（场 2） |
  | 3. `hit` 新字段有非默认值 | ✅ `speedMod` 214 种值、`atkStun` 215、`defStun` 166、`attackDir` 5、`blowFlags` 7、`shieldHp` 140 |
  | 4. 无异常 | ✅ `end.validity.verdict: ok`、`nanCount: 0`、坏行 0、`ioFailed: false` |
  | 附加：盾 HP 机制 | ✅ **20/20 靶子有盾曲线**（`shieldMax 530`，522/523 → 5…406，504 条记录，无人破盾）⇒ "用盾自身血量判定是否打中盾"**游戏内成立** |

- **▶ 本次暴露的问题 / 仍未验证（诚实标注）**
  1. `attackType` 两场恒为 `Standard` —— 本场无踢击/盾击（`AgentAttackType.Kick/Bash`），**无法区分"字段未填充"与"本场无触发"**，需一场含踢击的战斗才能定论；
  2. `attackDir` 有 34%（290/843）为 `None`，语义未确认；
  3. `blowFlags` 是**逗号组合串**（如 `"ShrugOff, NonTipThrust"`，85% 为 `None`）⇒ 分析器必须 split，别当单值枚举；
  4. 弓箭 `shot` 的 `weaponSlot` = `WeaponItemBeginSlot`（疑似取了枚举边界值，槽名可能不准）—— 待查；
  5. `state` 有 17 条缺 `reloading`/`reloadPhase`/`reloadCount`/`ammo`/`ammoMax`（取不到武器的状态）；`reloadCount` 恒为 1（语义可疑）；
  6. **`--unlimited-ammo` 本次未构成有效验证**：场 2 只射 178 发，未达携弹上界 ⇒ 没碰到补弹逻辑；
  7. `probe`(23) / `sample`(24) 事件与新遥测冗余，是否退役未定。

- **📌 磁盘预算**：单场 2.1–2.4 MB（上轮 0.48 MB，×4–5），大头是 `state`（每 2 秒 × 40 agent ≈ 4586 条/场）⇒ 批量跑前先算容量。

### [ ] ② 分析器扩展（纯 Python，无需游戏）
- **做什么**（6 项）：盾 HP 曲线 / 破盾箭数 / 挨箭分布 / 移速对账 / 装弹时长 / AI 参数分组。
- **落点**：`tools/bl_analyze.py`、`tools/bl_dummy_analyze.py`（必要时新增）。
- **依赖**：形态上不依赖 ①，但**真实验证**依赖 ① （现有样本不含新事件）。
- **可立即动工**：✅

### [ ] ③ 立项三个模型结论校验 —— 项目存在的理由
- **待校验结论**：
  1. "T6 挨 8 箭"
  2. "护甲回默认后 T4+ 中位 5 箭"
  3. "材质差异 7%"
- **🔴 至今一个都没测。**
- **依赖**：①②（要新遥测 + 要分析器）。

### [ ] ④ 阶段 2④ 报告器补「换边双跑」交叉验证块
- **问题**：当前报告会把两个**不同兵种**的"A 侧"直接相减，语义误导。
- **落点**：`tools/bl_compare.py` / `bl_batch.py` 的报告段。
- **可立即动工**：✅

### [x] ⑤ 卫生：git 基线 + 清残留
- `7d3aadf` 基线（41 文件；**刻意包含** 4 个 `*.bak_probe` 以便删除可回退）
- `d92798e` 清理 `src/*.bak_probe`(4) + `tools/__pycache__`（已核实：不被 `build.ps1` 编译、全项目 0 引用）
- `.gitignore`：`/out/`、`__pycache__/`、`*.pyc`、`*.bak_probe`、本机运行时配置
- **未处置**：项目仍**无远端仓库**（如需备份/多机协作，需你决定远端方案）

## 三、构建与一致性判据（2026-09-24 实测，易踩）

- **构建不是字节可复现的**：源码一字未改（git 工作区干净）的情况下重建 ``out\BlBridge.dll``，
  sha256 从部署副本的 ``70111C2D…`` 变成 ``43ADB178…``。
  ⇒ **不要用 dll sha256 跨次比对**来判断"跑的是不是我以为的版本"。
- **一致性的真正判据**是 ``bl_build_check`` 的口径（``tools/bl_mcp.py:331-375``）：
  1. 拿**模块目录内**的 ``build_manifest.json`` 与**同目录**的 dll 比对 sha256；
  2. 再拿清单里的 ``sources{}`` 逐文件 sha256 与 ``src\*.cs`` 比对。
  它**不读 ``out\``** —— 所以只要 ``build.ps1 -Deploy`` 把 dll 与 manifest **成对**拷贝，
  检查就自洽；手工只拷 dll 会误报 ``stale_deploy``。
- 另有 ``bridge_status.json`` 的进程内检查（``loadedSha256`` vs ``currentFileSha256``，同一个文件），
  用于发现"运行中被换掉的 DLL"。

## 四、已排除的路线（别再重走，详见交接快照 §6）

主菜单直接开战 · `Bannerlord.BLSE.Standalone.exe` 无人值守 · 引擎自带 `CombatLogManager`（AI 对局不产出）· 给靶子无限盾耐久（那是被测对象本身）。

## 五、关键路径

| 内容 | 路径 |
|---|---|
| 项目本体 | `C:\Users\LCGX\CodeBuddy\20260923171333\BlBridge\` |
| 构建 | `powershell -ExecutionPolicy Bypass -File .\build.ps1 [-Deploy]` |
| 战斗日志 | `%USERPROFILE%\Documents\Mount and Blade II Bannerlord\BlBridge\battles\` |
| 运行时状态 | 同上目录 `bridge_status.json` |
| 靶场工作区原型 | `%APPDATA%\reasonix\global-workspace\blbridge-dummy-range\` |
