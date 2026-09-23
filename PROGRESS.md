# BlBridge 进度列表

> **最后核实：2026-09-24 02:04**（本文件由 Reasonix 会话建立并维护；① 游戏内验证执行于 2026-09-24 01:40–01:41）
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

### [x] ② 分析器扩展 —— ✅ 已完成（2026-09-24，TDD 8 片红绿循环）
- **落点**：新建 `tools/bl_metrics.py`（8 个纯函数 seam + 薄渲染/CLI）与 `tools/bl_metrics_selftest.py`（74 项断言）。
  **未改动** `bl_analyze.py` / `bl_dummy_analyze.py` / `bl_mcp.py` ⇒ 零回归。
- **seam（已与你确认）**：`events: list[dict] -> dict/list` 的纯函数，可直接喂合成事件做**手算**断言：
  `shield_curves` / `shots_to_break` / `arrow_hits` / `speed_selfcheck` / `speed_vs_cap` / `reload_durations` / `ai_param_groups` / `death_arrow_stats`
- **验收**：`python tools/bl_metrics_selftest.py` → 74/74 通过（含真实日志 smoke）；
  对两场真实 0.7.9 日志 `python tools/bl_metrics.py <jsonl>` 均出报告。
- **实测结论（两场）**：
  - 挨箭分布：场 1 中位 11 箭/人、场 2 中位 4 箭/人（场 2 合计 153 箭，与独立解析一致）
  - 移速自洽：位置差分 vs 引擎 `speed`，平均误差 0.098~0.144、最大 1.5~2.1
    ⇒ **非零**：位置与速度两个字段并不完全自洽，值得追（可能是采样时刻与速度不同步）
  - 装弹：中位 **2.00 秒 = 恰好一个采样间隔** ⇒ 分辨率不足，该指标目前只能当**上界**
  - AI 参数：每兵种 30 个参数，两场各 2 兵种 × 20 agent
- **⚠️ 本轮纠错（我自己写错的，已修）**：初版 `speed_vs_cap` 用 `speed > maxSpeed` 判"超上限"，
  得出**"91% 超上限"的假结论**。源码取证：`maxSpeed` = `DrivenProperty.MaxSpeedMultiplier`、
  `combatSpeed` = `CombatMaxSpeedMultiplier`（**倍率**，`TelemetryBehavior.cs:476-477`），
  与世界单位速度量纲不同 ⇒ 该指标已删除，改为如实呈现并标注"两者不可直接比较"。

### [ ] ②-附 分析器暴露的新遥测缺口（做 ③ 前值得先补）
1. **真正的速度上限没采**：只有倍率，没有 max speed ⇒ "上限是否生效"判不了（源码 `:476`）。
2. **盾槽身份没采**：`shieldHp` 取自 `TryGetShield` 遍历到的**第一个盾槽**（`TelemetryBehavior.cs:253-278`）。
   场 1 有多个 agent 盾值**回升**（477 → 530）⇒ 盾槽/盾身份变过，此时"归零=破盾"不成立。
   分析器已把 `reversals` 当可疑信号报出，但遥测侧应补"盾槽索引 / 盾唯一 id"。
3. `attackType` 恒为 `Standard`（① 遗留）；`blowFlags` 需 split；弓的 `weaponSlot` 取到 `WeaponItemBeginSlot` 枚举边界值。
4. **`--freeze-dummies` 实测无效**（2026-09-24）：`dummy_meta.freeze=true` 已写入，但靶子**照样攻击**——
   `DummyRangeBehavior.cs:346-356` 用的是 `Agent.SetIsAIPaused(true)`，实测它挡不住攻击：
   弓手 20 人被"冻结"的靶子近战全灭（`Mace 137` / `OneHandedSword 83`），还出现友军互殴
   （legionary→legionary 36 次、fian_champion→fian_champion 49 次）。**修它要改 C# 并重部署重启**。
5. **引擎从不把盾耐久写成 0**（四场 0.7.9 日志实测：归零条数 = **0**，最小值 16 / 5 / 1 / 1）。
   破盾的真实表现是 `TryGetShield` 取不到盾 ⇒ 此后命中**不再带 `shieldHp` 字段**。
   ⇒ 源码注释里"从 shieldHp 序列归零即可得几箭破盾"的假设**不成立**；`bl_metrics` 已按新判据修正
   （`vanished` / `unshielded_after` / `vanish_time` / `zero_hp`，`broken = vanished or zero_hp`）。

### [ ] ③ 立项三个模型结论校验 —— 项目存在的理由
- **待校验结论**：
  1. "T6 挨 8 箭"
  2. "护甲回默认后 T4+ 中位 5 箭"
  3. "材质差异 7%"
- **🔴 至今一个都没测。**
- **依赖**：①②（**均已完成**）。**破盾箭数样本已拿到**（判据修正后才看得见）：
  场 `battle_20260924_015228_290.jsonl`（弓手 20 vs 靶子 10）→ **10/10 靶子盾被打掉**，
  **6~47 箭、中位 13.5 箭/人**，破盾时刻 121.6~239.5 游戏内秒。
- **▶ 完整对照结果（2026-09-24，20 场：T4 8 场 + T5/T6 12 场；攻击方固定 T6 弓手 40 人、靶子 5 人、battle_terrain_a）**
  复现：`python tools/bl_death_compare.py <这 20 个 jsonl>`

  | 档位 | 满血 | 样本 | **「扣血箭」中位区间** | 代表兵种（扣血箭中位） |
  |---|---|---|---|---|
  | **T4**（level 21） | 136 | 8 兵种 × 5 | **2~7（多数 3~5）** | falxman 5、sturgian_spearman 5、aserai_infantry 3、khuzait_spear_infantry 3 |
  | **T5**（level 26） | 146~151 | 6 兵种 × 5 | **7~8** | legionary 7、oathsworn 7、ulfhednar 7、wildling 8、menavliaton 9 |
  | **T6**（level 31） | 145~164 | 6 兵种 × 5 | **5~10（中位 7.5）** | banner_knight 5、druzhinnik 6、vanguard_faris 7、cataphract 8、khans_guard 9、fian_champion 10 |

  **对三条立项结论的回答**：
  1. **"T6 挨 8 箭" → 基本成立**：T6 六个兵种的「扣血箭」中位 = 5/6/7/8/9/10（**中位 7.5**）。
     ⚠️ 换成「总挨箭」口径就是 **8~15** ⇒ 口径不定死，同一批数据能得出相反结论。
  2. **"护甲回默认后 T4+ 中位 5 箭" → 对 T4 成立、对 T5/T6 偏低**：T4 实测多数 **3~5**（模型说 5 ✓），
     但 T5/T6 实测 **7~9**。模型那句话把三个档位混成了一句话。
  3. **"材质差异 7%" → 仍未测**：需要"同兵种不同材质"的对照（兵种固定、只换护甲材质）。
- **⚠️ 三个必须挂在结论上的限制**：
  1. **样本量**：每兵种 1 场 × 5 靶 = **5 样本**，撑不起"必然"（镜像局 σ 曾实测 2.6）。
  2. **死因偏差**：T4 骑兵与部分步兵的"死于箭"只有 40~80%（`imperial_heavy_horseman` **40%**、
     `vlandian_swordsman` 60%、`vlandian_knight` 80%）⇒ 这些行的「扣血箭」只统计了"恰好被箭射死"
     的子集，**与 100% 死于箭的行不可比**（选择偏差）。
  3. **暴露时长混淆**：「总挨箭」受靶子移动速度影响（骑兵 43~57 秒结束、步兵/弓手 86~166 秒）；
     只有「扣血箭」相对稳定 —— 这也是它更适合作为结论口径的原因。
- **▶ 下一步要补的**：① 每兵种 ≥3 场（把样本从 5 提到 15）；② 换边双跑（消除攻守/地形偏差）；
  ③ "材质差异"的同兵种对照；④ 死因偏差行的处理方法（如把"死于近战"单独归类而非混入中位数）。
- **工具**：`python tools/bl_death_compare.py --dir <battles>` 可复现上面这张表。

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

## 六、code-review（`7d3aadf..HEAD`，2026-09-24）—— 已修缺陷

双轴只读审查（Standards = 仓库既有惯例 + Fowler 坏味道基线；Spec = 本文件的 ②/③ 条目）发现并已修：

| # | 缺陷 | 性质 | 修法 |
|---|---|---|---|
| 1 | `bl_metrics.py` 的 `if __name__ == "__main__"` 块排在 `death_arrow_stats` **之前** | 结构错误（脚本模式下该函数永不定义） | 移回文件末尾 → 557 行 |
| 2 | **默认中文 Windows 控制台（GBK）下 CLI exit 1**：`render()` 输出里的 `⇒`/`⚠️` 抛 `UnicodeEncodeError` | 可用性回归（既有工具同条件不崩） | 新增 `_safe_streams()`：`reconfigure(errors="replace")`，中文保真、编不出的字符降级；补 GBK 回归测试 |
| 3 | README §二 未登记 3 个新工具 | 硬违规（仓库惯例：每个 `tools/*.py` 一行职责） | 补 3 行登记 |
| 4 | 文档漂移：`shots_to_break` 仍写"盾首次归零"（判据已改成"盾消失"）、`analyze_metrics` 称"6 个指标"、模块 docstring 只列 1 个函数 | 判断项 | 三处同步；并把 `death_arrow_stats` 接入 `analyze_metrics` |

**审查中未采纳/待议**（判断项，记录备查）：`load_events`/`_fmt` 与 `bl_analyze.py` 逐字重复（自包含 vs DRY 的取舍）、
四个指标函数重复"过滤→按 agent 分组→按 time 排序"样板、`bl_death_compare.py` 自建 unit 索引取 `side`（Feature Envy）、
`--json` 当前无消费者（Speculative Generality，且接线会违反"不改 bl_mcp.py"的裁定）。
**一条子代理误报**：其称 `render` 取 `meta.file` 恒空 —— 实测 `meta` 事件**带** `file` 字段，不成立。
**方法学限制**：两个审查子代理**没有 shell 工具**，无法实跑 `git diff`；其"未改动 bl_analyze.py/bl_mcp.py"等结论由主代理用 git 复核确认。
