# BlBridge 进度列表

> **最后核实：2026-09-24 19:40**（本文件由 Reasonix 会话建立并维护；最近一次游戏内验证 = §八 v0.8.1 遥测补齐，2026-09-24 19:37）
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
- **落点**：新建 `tools/bl_metrics.py`（8 个纯函数 seam + 薄渲染/CLI）与 `tools/bl_metrics_selftest.py`（75 项断言）。
  **未改动** `bl_analyze.py` / `bl_dummy_analyze.py` / `bl_mcp.py` ⇒ 零回归。
- **seam（已与你确认）**：`events: list[dict] -> dict/list` 的纯函数，可直接喂合成事件做**手算**断言：
  `shield_curves` / `shots_to_break` / `arrow_hits` / `speed_selfcheck` / `speed_vs_cap` / `reload_durations` / `ai_param_groups` / `death_arrow_stats`
- **验收**：`python tools/bl_metrics_selftest.py` → 75/75 通过（含真实日志 smoke）；
  对两场真实 0.7.9 日志 `python tools/bl_metrics.py <jsonl>` 均出报告。
- **实测结论（两场）**：
  - 挨箭分布：场 1 中位 11 箭/人、场 2 中位 4 箭/人（场 2 合计 153 箭，与独立解析一致）
  - 移速自洽（口径已修正，见 §六）：位置差分 vs 引擎 `speed` 的**区间外距离** ——
    最近 20 场 / 869 个 agent 中 **69.9% 完全自洽（区间外距离 = 0）**，中位 0.0000、最大 2.93。
    ⇒ 结论由旧口径的"普遍不同步"更正为"**大多数自洽，约 30% 的 agent 有区间外偏离**"
    （大概是采样跨过了速度突变时刻）；旧数字 0.098~0.144 建立在一个有偏的近似上，已废。
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

### [x] ④ 阶段 2④ 报告器补「换边双跑」交叉验证块 —— ✅ 已完成（2026-09-24）
- **落点**：`tools/bl_compare.py`（`bl_batch.py` 只产 manifest、本身没有报告段）。
- **新增纯函数 seam `mirror_cross_check(cfg_a, cfg_b, shares_a, shares_b)`**（有手算断言）：
  识别两组是否**互换攻守的镜像**，并把两组各自的"攻方满编占比"拆成
  **位置效应**（= 两组攻方占比均值之和的一半 − 50）与 **兵种差异**（= 两组攻方占比相减）。
  镜像下两个兵种的「攻 − 守」**必然相等**（= 2 × 位置效应）—— 报告用它做数据自洽核对。
- **旧行为的毛病**：只在恰好两组时直接相减，既不识别镜像、也不分离位置效应；
  非镜像时那个差值**没有可比性**（这正是语义误导的来源）。现在非镜像会明确输出
  「不构成换边双跑」并说明该差值为什么不能当真。
- **顺带修的同源缺陷**：`bl_compare.py` / `bl_death_compare.py` 输出里的 ⚠️ / ✅ 在默认中文控制台
  （GBK）下同样会 `UnicodeEncodeError` 崩溃 ⇒ `safe_streams()` 收拢进 `bl_common`，三个工具统一调用；
  `bl_compare` 原先从 `bl_dummy_analyze` 取的 `load` 也改为复用 `bl_common.load_events`。
- **补测试覆盖缺口**：原先只测纯函数，`main()` 接入层改了 `stats` 结构却漏改一处解包
  （纯函数测试全绿、CLI 直接崩）⇒ 新增端到端测试（subprocess + GBK 环境 + 临时 manifest）。
- **▶ 真实数值（2026-09-24，6 场换边双跑：`imperial_legionary` vs `battanian_wildling` 各 20 人 × 3 局）**
  复现：`python tools/bl_batch.py --plan tools/plan.example.json --out runs.json`
  → `python tools/bl_compare.py --manifest runs.json`

  | 兵种 | 当攻方（满编占比） | 当守方 | 攻 − 守 |
  |---|---|---|---|
  | `imperial_legionary` | 25.3% | 28.7% | **−3.4** |
  | `battanian_wildling` | 71.3% | 74.7% | **−3.4** |

  - **位置效应 = −1.7 个百分点**（极小）⇒ v0.7.3 的"对称化"确实把攻守位偏差压到了接近零 ✓
  - **兵种差异 = −46.0 个百分点**（巨大）⇒ 满编窗口里 `wildling` 的输出效率约为 `legionary` 的 2.8 倍
  - 对称性核对：两组攻方占比之和 96.6%（偏离 −3.4 = 2 × 位置效应）✓ 数学自洽
  - **⚠️ 反直觉、且对实验设计重要**：`legionary` 满编输出效率低得多，**但最终 5 胜 1 负**
    （`wildling` 1 胜 5 负）⇒ **满编窗口占比是"输出效率"，不是"胜负预测器"**。
    另外两组的「满编 → 全程」占比**方向完全相反**（25.3%→49.8% 与 71.3%→39.0%），
    这正是"全程口径被幸存者偏差污染"的对称证据（赢的一方活更久 ⇒ 全程占比被抬高）。

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

**② 后续「全部处理」（2026-09-24，同一轮）** —— 把审查中留作判断项的 5 条也做了：

| # | 项 | 处理 |
|---|---|---|
| 5 | `speed_selfcheck` 用"与区间均值的差"当误差，会把**真实加减速**误报成字段不同步 | 改为**区间外距离**（v_pos 落在两端 speed 之间即 0）；结论数字随之更正（见 ② 段） |
| 6 | 指标函数重复「过滤 → 按 agent 分组 → 按 time 排序」样板 | 抽出 `_by_agent()` 供 `speed_selfcheck` / `reload_durations` 共用 |
| 7 | `load_events` / `_fmt` / 「列 battles」在三个文件里逐字重复 | 新建 `tools/bl_common.py` 收拢；`bl_analyze.py` / `bl_metrics.py` / `bl_death_compare.py` 共用（`bl_analyze` 的公开名保留 ⇒ `bl_mcp.py` 无需改，契约未动） |
| 8 | `--json` 当前无消费者（Speculative Generality） | 删掉；等真有消费者再加，避免"一接线就得违反不改 bl_mcp.py 的裁定" |
| 9 | 新指标不在主回归链（改坏了也无人发现） | `bl_selftest.py` 新增 ⑨ 分节调用 `bl_metrics_selftest.main()`，失败并入其 FAIL 汇总 |

顺带修掉两处小问题：`bl_death_compare.py --dir` 原先直接 `os.listdir`（无 isdir 保护）→ 改用 `bl_common.list_battle_files()`；
`bl_metrics.py` 去掉已无人使用的 `io` / `json` 导入。

**⚠️ 本轮踩坑（记以免再犯）**：用 pwsh 的 `Get-Content -Raw` + `Set-Content` 去改 `bl_analyze.py`，
把文件里的中文**写坏**（`SyntaxError: unterminated string literal` + 满屏乱码），只能 `git checkout` 还原。
**教训：改含非 ASCII 的源文件不要走 pwsh 的文本读写，用 Python（显式 `encoding="utf-8"` + `newline=""`）。**

## 七、靶子护甲数值覆盖（v0.8.0，**未游戏内验证**）

**目的**：回答立项里的旋钮问题（"护甲 ×1.15"这类改动到底值不值），并给"材质/护甲对照"打第一层地基。

**实现（零 Harmony）**：`DummyRangeBehavior` 新增 `ArmorHead` / `ArmorTorso` / `ArmorLegs` / `ArmorArms`
（`-1` = 该部位不覆盖），在 `OnMissionTick` 里**每帧重申**到靶子；
CLI：`python tools/bl_cmd.py start ... --dummy-armor head=45,torso=35,legs=20,arms=25`。

- **为什么不用 Harmony patch**：第三方编辑器 `KunKunEditor`（已反编译对照）走的是
  `[HarmonyPatch(typeof(SandboxAgentStatCalculateModel), "UpdateHumanStats")]` + 直接改 `AgentDrivenProperties`。
  本项目"删掉模块即完全回退"的性质要求**零 patch**，所以改用每帧重申 —— 与既有 `ApplyDummyToughness`
  同因：`AgentDrivenProperties` 是引擎每次重算属性时**重写的对象**，只设一次会被覆盖。
- **部位名照引擎真名**：`ArmorHead` / **`ArmorTorso`** / **`ArmorLegs`** / **`ArmorArms`** ——
  不是控制台命令里那种 `set_body_armor` / `set_arm_armor` 的 Body / Arm 命名。
  **编译器已替我们验证**这四个 `DrivenProperty` 项存在（编译通过即证据）。
- **新增遥测（可验证性的前提）**：`ai` 事件补 `armorHead` / `armorTorso` / `armorLegs` / `armorArms`
  —— 没有它就**无法证明覆盖生效**。
- **顺手修的既有缺陷**：`OnRemoveBehavior` 原先只复位 `DummySide` / `FreezeDummies`，漏了
  `UnlimitedAmmoForShooters` 与新的护甲字段（static 残留会污染玩家之后的手动战斗），已补齐。

**验证状态**：✅ **已部署 + 已游戏内验证**（2026-09-24）。

- **部署**：`build.ps1 -Deploy` 于 **02:36** 完成（本文件 02:04 那版写的"尚未部署"已过期）；
  18:3x 核对 `bl_cmd.py buildcheck` = `ok`（源码 = 构建产物 = 部署文件 = 进程内 DLL，`e47ae4f6…`，版本 0.8.0）。
- **参数修复确认**：`ebf0ea3`（`--dummy-armor` 改传数字）生效 —— `dummy_meta.armor` **不再是 `-1`**。
- **游戏内验证**：2026-09-24 18:37–18:45，**12 场四档对照**
  （攻方固定 `battanian_fian_champion` 20 人 vs 不朽靶 `imperial_legionary` 10 人，`battle_terrain_a`，
  `orders=charge`，`cap=30` 真实秒，`--unlimited-ammo`，每档 3 场）。

  **判据① 通过（12/12 场）**：`dummy_meta.armor` 与 `ai` 事件中靶子的四部位护甲**逐场等于指定值**；
  攻方护甲始终是自己的 `(50,59,31,42)` ⇒ 覆盖只作用于靶子 ✓

  **判据② 通过**：伤害随护甲**单调**变化，跨度约 **4 倍**

  | 档 | 靶子护甲 head/torso/legs/arms | 近战命中靶子 n | `damagedHp` 均值 | 中位 | p25 | p75 |
  |---|---|---|---|---|---|---|
  | D 不覆盖（默认） | (50,75,85,90) | 4892 | 21.92 | 19 | 9 | 34 |
  | L 低 | (10,10,10,10) | 5258 | **67.62** | 74 | 47 | 97 |
  | M 中 | (45,35,20,25) | 5611 | **32.22** | 38 | 17 | 49 |
  | H 高 | (80,70,50,60) | 5871 | **16.85** | 19 | 7 | 26 |

  箭伤（`isMissile=true`，`dSide=Defender`）同向且更剧烈：L 4.64 / M 2.93 / D 1.56 / H 0.69
  （**中位恒为 0** —— 靶子带盾，多数箭被挡下 ⇒ 盾是护甲实验的混淆项）。

  **对旋钮问题的初步回答**：护甲对每击伤害的影响**很大**（本例低↔高相差 4 倍），
  远大于"×1.15"这种量级 ⇒ 15% 的护甲微调**可测**：每档近战样本 ≈5600、均值标准误 ≈0.4，
  足以分辨 5% 级别的均值差异。**但必须固定命中部位/武器**才能干净归因（本次是混合口径，含头/躯/腿/臂）。

**本轮新发现的缺口（并入 ②-附 同源清单）**：
  1. `bl_batch.py` 的 plan **不支持** `dummySide` / `dummyArmor*` ⇒ 跑批做护甲对照只能逐场走 CLI（本次 12 场即如此）；
  2. `hit.bodyPart` 出现**枚举边界值** `CriticalBodyPartsBegin`（本批 2640~3326 条/档）——
     与"弓的 `weaponSlot` 取到 `WeaponItemBeginSlot`"**同一类缺陷**（枚举边界被当成真实值）；
  3. `hit` 事件里 `dSide=Attacker`（约 1084 条/档，靶子还手打攻方）与 `dSide=Defender` 混在一起
     ⇒ 分析必须**显式按 `dSide` 过滤**（本次口径：`dSide=Defender` 才是"靶子挨打"）。

**另一条已勘察的路线（未实施）**：真"换装备/材质" —— 参照 `CharacterReload`（本机，Vortex 部署）的姿势：
`Equipment.Clone(false)` + 槽位赋值 + `CalculateEquipmentCode()`，或 `Equipment.AddEquipmentToSlotWithoutAgent`；
作用于**开战前**的靶子 `CharacterObject`，同样零 Harmony。它才是"材质差异 7%"要的路。

### 首次实测（2026-09-24）暴露的坑：参数被**静默丢弃**

9 场护甲对照**全部无效**，且**没有任何报错**：`dummy_meta.armor` 恒为 `-1`，三档的每击扣血无差异
（25.13 / 24.96 / 25.31）。根因不在 C#，而在**参数类型**：

- `bl_cmd.py` 把 `--dummy-armor head=25` 解析成**字符串** `"25"` 发出去；
- C# 侧 `Jmini.Num`（`Jmini.cs:146-164`）从值的位置**只接受数字字符**（digit / `-` / `+` / `.` / `e` / `E`），
  碰到字符串的引号首字符 ⇒ `p == start` ⇒ **直接返回 fallback** ⇒ 参数被静默丢弃。
- **修法**：`bl_cmd.py` 改传 `float`。

**教训（值得记住）**：`Jmini` 的 `Str` 读字符串，而 `Num` / `Int` / `Bool` 只读**裸值** ——
给它们传字符串**不会报错，只会静默用默认值**。这类"静默失效"正是本项目一直在猎杀的东西，
所以**每个新参数都必须有可观测的落点**（本次就是靠 `dummy_meta.armor` 才发现的）。

**顺带澄清一条既有文档偏差**：`--cap` 传的是 int ⇒ 生效，但它的单位是**真实秒**
（`--cap 120` ⇒ 游戏内约 1200 秒，正好 10 倍速），与 `--help` 文案写的"游戏内秒"不符。

### 护甲微调实验：×1.15 到底值不值（2026-09-24 18:48–18:53，9 场）

**问题**（§七 立项目的）：护甲 ×1.15 这类小改动到底值不值？

**设计**：攻方固定 `battanian_fian_champion` 20 人 vs 不朽靶 `imperial_legionary` 10 人，
`battle_terrain_a` / `orders=charge` / `cap=30` 真实秒 / `--unlimited-ammo`，
**只改靶子四部位护甲**，每档 3 场。

| 档 | 靶子护甲 (头/躯/腿/臂) | 近战命中 n | `damagedHp` 均值 | 95%CI | 中位 | Δ vs ×1.00 | Welch t |
|---|---|---|---|---|---|---|---|
| A ×1.00 | 45/35/20/25 | 6192 | 32.145 | [31.63, 32.66] | 37 | — | — |
| B ×1.15 | 52/40/23/29 | 5940 | 28.036 | [27.57, 28.51] | 32 | **−12.78%** | **11.52** |
| C ×1.30 | 59/46/26/33 | 5740 | 24.989 | [24.56, 25.42] | 28 | **−22.26%** | **20.88** |

**批次再现性**：上一轮 M 档（护甲同为 45/35/20/25，18:42–18:43 三场）vs 本轮 A 档：
Δ = **+0.23%**、Welch t = **−0.20** ⇒ 无系统性偏差，实验可复现 ✓

**结论（对"值不值"的回答）**：护甲 ×1.15 ⇒ **每击伤害 −12.8%**（等价于生存击数 **+14.7%**）；
×1.30 ⇒ −22.3%（近似线性、略递减）。检测灵敏度：近战均值 SE/mean ≈ **0.8%/档**
⇒ 可分辨约 1.6% 的伤害差异，**12.8% 远超噪声** ⇒ **这是个强烈可感的旋钮，值得调**。

**⚠️ 本轮口径教训（新增，重要）**：箭伤**必须筛 `blocked=false`**。
不筛时（`dSide=Defender` 的全部箭命中）会得到"**护甲越高、箭伤越高**"的假象（2.45 → 2.81 → 3.05，Welch t 仅 −0.68 / −1.12）；
真因是**盾的阻挡率在变**（93% → 91% → 89%），而每发有效伤害在降：
只取未被挡下的箭 → 均值 **35.86 → 31.70 → 28.37**（−11.6% / −20.9%，与近战同向）✓

**日志**（`battles/`，2026-09-24）：
A `battle_20260924_184835_525` / `184910_771` / `184946_307`；
B `185021_312` / `185056_925` / `185132_481`；
C `185207_876` / `185243_058` / `185318_280`

### 单部位护甲细分：改哪个部位最值（2026-09-24 19:09–19:16，12 场）

**设计**：其余同上一节；4 档 × 3 场 = `BASE`(45/35/20/25) / `HEAD`(52/35/20/25) / `TORSO`(45/40/20/25) / `ARMS`(45/35/20/29)。

**🔎 先修正一条既有记录（重要）**：`hit.bodyPart` 里的 `CriticalBodyPartsBegin` **不是"坏值"，而是 `Head` 的枚举别名**。
取证（`bannerlordsage` 反编译）：`TaleWorlds.MountAndBlade.BoneBodyPartType`（`bin/.../BoneBodyPartType.cs:5-21`）

```csharp
None = -1, Head = 0, Neck = 1, Chest = 2, Abdomen = 3, ShoulderLeft = 4, ShoulderRight = 5,
ArmLeft = 6, ArmRight = 7, Legs = 8, NumOfBodyPartTypes = 9,
CriticalBodyPartsBegin = 0,   // Head 的别名
CriticalBodyPartsEnd   = 6    // ArmLeft 的别名
```

`ToString()` 遇到同值别名时返回了边界名 ⇒ **字面 `Head` 永不出现**，`CriticalBodyPartsBegin` 即头部命中
（实测 `Head` 0 次、`CriticalBodyPartsBegin` 2277 次，与 `Head=0` 完全吻合）。
⇒ **修正 ②-附 3**："枚举边界值"的说法不成立——**数据没错，是命名歧义**，分析时须把 `CriticalBodyPartsBegin` 当 `Head`。
（`weaponSlot` 取到 `WeaponItemBeginSlot` 属同类现象的显示问题，仍待修。）

**命中部位份额（BASE，n=4546 近战命中靶子）**：

| 部位 | 份额 | 3 场均值 |
|---|---|---|
| Head（=`CriticalBodyPartsBegin`） | **50.1%** | 33.47 |
| ShoulderRight + ShoulderLeft | 28.6% | 35.41 / 35.67 |
| ArmRight + ArmLeft | 14.1% | 17.55 / 5.32 |
| Chest / Neck / Abdomen / Legs | 3.6% / 3.1% / 0.4% / ~0% | 32.79 / 30.58 / 13.95 / — |

**稳健结果（全量 与 time≤120 窗 两套口径一致）**：

| 档 | 对应部位 | 伤害变化 | Welch t |
|---|---|---|---|
| `HEAD`（头 45→52） | Head | **−11.92%**（窗内 −12.36%） | 8.1（窗内 4.2） |
| `ARMS`（臂 25→29） | ArmRight | **−25.28%**（窗内 −31.10%） | 3.3（窗内 2.3） |
| `TORSO`（躯干 35→40） | Chest 等 | 不显著（Chest n≈160，t=−0.5） | — |

⇒ 只改头护甲只降头部伤害、只改臂护甲只降臂伤害，**归因干净** ✓

**⚠️ 方法学限制（本轮新发现，与 ③「暴露时长混淆」同类）**：
1. **改护甲会改变整场战斗的演化**：四档近战命中总量 4546 / 6421 / 4991 / 5686（**相差 41%**），
   部位构成随之变化（Head 命中 2277 → 3317）⇒ 跨档比"同部位均值"仍被构成变化污染；
   做时间窗（time≤120）标准化后各档总量才接近（1516~1896）。
2. **小样本部位测不出**：Chest(3.6%)、Neck(3.1%)、Legs(~0%) 每档样本 <200 ⇒ 3 场规模无法检验。
3. 若干"意外上升"（ShoulderRight 在 `HEAD` 档 +14.7%、Chest 在 `HEAD` 档 +20.5%）**不视为机制**——
   大概率是构成变化的产物；本轮**未从引擎源码证实"命中部位 → 护甲字段"的映射**，故不据此下结论。

**结论（部位权重）**：头部承受 **~50%** 近战命中，且头部护甲确实直接压低头部伤害 ⇒
**"改头护甲最值"**，其次肩(28.6%)、臂(14.1%)；躯干与腿受击份额太小，靠这类实验测不准。

**日志**（`battles/`，2026-09-24）：BASE `battle_20260924_190945_274` / `191017_341` / `191053_122`；
HEAD `191128_383` / `191203_290` / `191238_552`；TORSO `191313_527` / `191349_090` / `191424_415`；
ARMS `191459_538` / `191534_790` / `191610_398`

## 八、v0.8.1 遥测补齐（2026-09-24，**已编译部署、待游戏内验证**）

**动机**：②-附 列的 3 个遥测缺口。**先取证再改**（全部反编译核实，不猜）：

| # | 缺口 | 取证结论 |
|---|---|---|
| 1 | 真速度上限 | **引擎不通过公开 API 暴露**：`AgentDrivenProperties` 的 100+ 属性里只有 `MaxSpeedMultiplier` / `CombatMaxSpeedMultiplier`（**倍率**）；`AgentStatCalculateModel` 无 `GetMaximumSpeed`；`Agent` 也没有 `MaxSpeed`（唯一同名项是 `SiegeWeaponMovementComponent.MaxSpeed`，围城器械的）⇒ **原记录"没采"应更正为"引擎不提供"** |
| 2 | 盾槽身份 | `TryGetShield` 只回传 HP，且取遍历到的**第一个** `IsShield()` 槽 ⇒ 盾槽/盾物品变过即误判（场 1 有盾值回升 477→530） |
| 3 | 部位/槽位名 | `BoneBodyPartType`：`Head=0` 与 `CriticalBodyPartsBegin=0` **同值**；`EquipmentIndex`：`WeaponItemBeginSlot=0` 与 `Weapon0=0` **同值** ⇒ `ToString()` 返回别名，`Head` / `Weapon0` 字面**永不出现** |

**改动**（一律**新增**字段，旧字段不动，避免破坏已有 60+ 场历史日志）：

| 事件 | 新增字段 | 来源 |
|---|---|---|
| `hit` | `bodyPartName` | 新 `EnumNames.BodyPart`（枚举真值 → 稳定名） |
| `hit` | `shieldSlot` / `shieldItem` | `TryGetShield` 增回传槽索引与 `Item.StringId` |
| `shot` | `weaponSlotName` | 新 `EnumNames.EquipSlot` |
| `kill` / `dummy_hit` | `bodyPartName` | 同上 |
| `ai` | `topSpeedReach` | `DrivenProperty.TopSpeedReachDuration`（加速到顶速时长） |

- 新增 `src/EnumNames.cs`（manifest 17 sources）；`BridgeConfig.Version` 与 `module/SubModule.xml` → **0.8.1**
- 构建：`build.ps1 -Deploy` 成功（59 KB，`dll sha256 = 7CB21A49287892E0…`，旧 dll 备份 `BlBridge.dll.bak_20260924_193505`）

**▶ 游戏内验证（2026-09-24 19:37，1 场靶场：`battle_20260924_193725_850.jsonl`）—— 判据全过**

| 判据 | 结果 |
|---|---|
| 1. `version 0.8.1` + `fileChangedSinceLoad: false` + `buildCheck ok`（四段一致） | ✅ |
| 2. `hit.bodyPartName` = `Head` **1040** 次；`hit.bodyPart` 仍是 `CriticalBodyPartsBegin` **1040** 次 | ✅ 新旧并存，其余 8 个部位**逐值一致**（ArmLeft 267 / ArmRight 378 / ShoulderRight 299 / ShoulderLeft 309 / Chest 80 / Neck 59 / Abdomen 16 / Legs 5） |
| 3. `shot.weaponSlotName` = `Weapon0`；`weaponSlot` 仍是 `WeaponItemBeginSlot` | ✅（另 `Weapon2` 无别名冲突，两列同名） |
| 4. `hit.shieldSlot` = `{1: 437}`、`hit.shieldItem` = `stronger_reinforced_kite_shield`（437 条） | ✅ 字段有值 |
| 5. `ai.topSpeedReach` 30/30 条，2.590~3.031 | ✅ |
| 附：`dummy_hit.bodyPartName` 同样正确（`Head` 959 次） | ✅ |

**⚠️ 判据 4 的"正面证据"本场未取到**：10 个持盾靶共 437 条带盾命中，
**盾 HP 回升 0 次、换盾 0 次**（盾物品恒为 `stronger_reinforced_kite_shield`、槽恒为 1）
⇒ "回升时能否区分『换了盾』与『盾被修复』"**仍需一场出现盾值回升的战斗**才能验证
（②-附 2 记录的 477→530 回升出自 0.7.9 的一场，本场未复现）。
