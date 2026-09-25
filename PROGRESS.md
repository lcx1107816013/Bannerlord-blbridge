# BlBridge 进度列表

> **最后核实：2026-09-25（v0.8.10 修复轮，见 §二十；**最近一次游戏内验证 = §二十 §5**，2026-09-25 12:50–12:57）**（本文件由 Reasonix 会话建立并维护）
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
  3. **"材质差异 7%" → 语义已查明、仍待测**：出自 **Warbandlord 的护甲公式模型**（离线反编译重建），
     指"同一护甲值下布甲 vs 板甲的实伤差 7%"（刺伤 A=40 时 39.3 vs 36.5）。
     机理 = mod 引入的**材质抗性 R**（`config.xml` 的 `MaterialResistance` 组：Cloth 刺 0.55 / Plate 刺 0.65）；
     公式：阈值 = R·A_eff·0.6·PRF，PR = 0.125^(R·A_eff·0.0215+0.09)。
     ⚠️ **原版引擎不读材质**（只进音效 `GetSoundParameterForArmorType`），是 mod 用 Harmony 改写的
     ⇒ 查证时别只看 `TaleWorlds.*.dll`（我踩过这个坑，差点得出反向结论）。
     验证路线见 §九（`--dummy-body-item` 换装）。
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

## 九、③ 数据补强 + v0.8.2 靶子换装（材质对照的实现）

### ③ 数据补强（2026-09-24 19:40–19:48，40 场）
配置同 §③（`fian_champion` 40 人 vs 被测兵种 5 人），每兵种样本 **5 → 15**（5 靶 × 3 场）：

| 档 | 兵种数 | 「扣血箭」中位区间 | 旧（5 样本） |
|---|---|---|---|
| T4 | 8 | **3~6** | 2~7（多数 3~5） |
| T5 | 6 | **5~8** | 7~8 |
| T6 | 6 | **5~10（中位 8）** | 5~10（中位 7.5） |

⇒ **「T6 挨 8 箭」在 15 样本下仍成立**（T6 六兵种中位 = 8）。
**死因偏差仍在**（未修）：`imperial_legionary` 55%、`vlandian_swordsman` 67%、`imperial_heavy_horseman` 67%、
`vlandian_knight`/`vlandian_banner_knight` 87% 死于箭 —— 这些行的「扣血箭」只统计"恰好被箭射死"的子集。
复现：筛出 `40 fian_champion vs 5 被测` 的 71 个文件 → `bl_death_compare.collect()`。

### v0.8.2 靶子换装（**已编译，部署待游戏关闭**）
- **动机**：验「材质差异 7%」（§三 第 3 条的更正）——需要"同兵种、同护甲数值、**只换材质**"
- **实现**：`--dummy-body-item <item_id>` 把靶子身甲换成该物品。
  `SpawnEquipment.Clone(false)` → `AddEquipmentToSlotWithoutAgent(Body, …)` → `MissionEquipment.FillFrom(eq, banner)`
  （零 Harmony；`MissionEquipment` 索引器**只有 getter**，所以必须整份灌回；每个 agent 只换一次）
- **新事件 `dummy_swap`**：item / material / armorBody / agents —— 本参数的**可观测落点**（防静默失效）
- **实验设计**：靶子 `imperial_legionary` 10 人 + 攻方 `fian_champion` 20 人，
  `--dummy-armor` 对齐四部位数值，只换身甲（Cloth `nordic_tunic` vs Plate `plated_leather_coat`），每档 3 场，
  分析按 `bodyPartName` 分组（换身甲只影响躯干/肩类部位的材质）
- **编译期踩坑**：`Agent.Banner` 是 `ItemObject`（旗子物品）**不是** `Banner` ——
  `FillFrom` 要的 Banner 得从 `Formation`/`Team` 取；`MBObjectManager` 需 `using TaleWorlds.ObjectSystem;`

### ③ 死因偏差处理（立项遗留 ④，2026-09-24）

`bl_death_compare.py` 新增纯函数 `summarize()`：**主口径只取「死于箭」的样本**
（不足 `MIN_ARROW_SAMPLES=3` 拒绝给数，显示 `-`）；「死于近战」只计数、不计入；「旧口径」（全样本）保留供对照。

- **实测差异**：`imperial_legionary` 旧口径 **5.0 → 新口径 7.0** —— 它 20 个样本里 9 个死于近战，
  混算把中位拉低了 2 箭；100% 死于箭的兵种新旧口径一致 ✓
- 自测新增 6 项断言（`test_death_compare_split`），全量回归 `EXIT=0`

### ③ 暴露时长混淆分离（2026-09-24，71 场 / 20 兵种）

| 关系 | Pearson r |
|---|---|
| 存活时长 ↔ **总挨箭** | **+0.493** |
| 存活时长 ↔ **扣血箭** | +0.235 |
| 总挨箭 ↔ 扣血箭 | **−0.414** |

- 存活时长确实污染「总挨箭」（骑兵存活中位 35~48 秒 vs 步兵 75~113 秒）
- 「扣血箭」几乎不受时长影响 ⇒ **§③ 选它当主口径是对的**（首次给出量化依据）
- **意外发现**：总挨箭与扣血箭**负相关** ⇒ 「总挨箭 − 扣血箭」= **被盾挡下的箭**。
  带盾兵种差得极大（`vlandian_knight` 16 vs 4、`khuzait_spear_infantry` 31 vs 3），
  无盾的 `battanian_fian_champion` 则是 10 vs 10 完全相等
  ⇒ **「总挨箭」实际度量的是"箭压力"而非"受伤"**，里面混着盾的阻挡率。

### v0.8.3：来源标记 + 工具收尾（2026-09-24）

- `meta` 事件新增 `mission`：`"bridge"`（BlBridge 自建靶场）/ `"game"`（其它，含玩家在战役/沙盒里打的实战）
  —— 解决"`battles/` 里靶场实验与实战混在一起、事后分不清"。
  实现：`SubModule.MissionOrigin` 静态标记（`ScenarioRunner` 开战前置 `"bridge"`），
  `TelemetryBehavior` 构造时读一次并**立刻复位** ⇒ 标记只对那一场有效、无残留。
- `bl_mcp.py` 的 `bl_start_battle` 补齐靶场 schema（`dummySide`/`dummyArmor`/`dummyBodyItem`/`freezeDummies`/`unlimitedAmmo`），
  口径与 `bl_cmd.py` 完全一致（布尔发字符串 `"true"`、护甲发**数字**）
- 新增 `tools/plan.material.example.json`（材质对照示例）
- 编译部署 **0.8.3**（`dll sha256 = 822AC5409555470A…`）

## 十、随机种子与「同种子重放」（v0.8.4）

**动机**（学自 OpenRA-RL 的 `.orarep` 重放）：BlBridge 只记录**结果**、不记录**输入种子**
⇒ 同一配置两次跑结果不同，只能靠多跑取统计；这也是"改护甲会改变整场战斗演化"那类混淆的根源。

**取证**（反编译核实，`TaleWorlds.Core.MBRandom`）：
- `public static void SetSeed(uint seed, uint seed2)` —— **公开可用**；
  `MBRandom.Random` 在游戏内取 `Game.Current.RandomGenerator` ⇒ C# 侧随机流可被钉住
- `MBRandom` 是 C# 侧**唯一**随机门面（`RandomFloat` / `RandomInt` / `ChooseWeighted` / `RoundRandomized` 全走它）
- `MissionInitializerRecord.RandomTerrainSeed` 只管**地形**生成，不是战斗随机
- ⚠️ **未知项**：伤害公式的随机命中因子 `PRF` 在 **native 层**掷（`Agent.ApplyDamage` 是引擎 C++），
  C# 设种子**未必**管得住它 —— 只能实测判定

**实现**：`--random-seed N` → 开战前 `MBRandom.SetSeed((uint)N, (uint)(N ^ 0x9E3779B9))`；
`meta` 事件记录实际用的 `randomSeed`（-1 = 未指定，即引擎默认行为不变）。

**待游戏内验证（3 场，判据）**：
- A / B **同种子** ⇒ `hit.damagedHp` 序列、`kill` 时刻、`unit` 初始位置应**逐值一致**
- C 换种子 ⇒ 应与 A 不同
- 若 A/B 也一致不了，说明剩余随机源在 native 层（则该路只到"部分可复现"）

## 十一、多轮连续实验（v0.8.5，**已编译部署，待游戏内验证**）

**动机**（学自 OpenRA-RL v2 的"降低重置开销"＋用户构想）：现在每场都 `MissionState.OpenNew`
⇒ 每场约 3~6 秒花在加载/卸载上，更关键的是**每轮的运行环境都是新的**（地形加载、光照、初始站位、AI 状态），
跨轮比较混着环境差异。同一 mission 连跑就把这一层消掉。

**实现**（零 Harmony，全部公开 API）：
- 新 `src/RoundOrchestratorBehavior.cs`：每 0.5 秒查双方存活，某方 ≤ `EndAlive` ⇒ 本轮结束
- 结束处理：写 `round_cleanup` → 残兵 `Agent.Die(default(Blow))` 清场 → 通知遥测**换一个新文件** →
  `Mission.SpawnAgent(AgentBuildData.Team/InitialPosition/InitialDirection)` 重生 → 重设 `TacticCharge`
- ⚠️ **多轮模式不挂 `AgentVictoryLogic`** —— 它会在"一方全灭"时结束 mission，与多轮直接冲突
- **每轮一个独立日志文件**（零污染），`meta.round` 标记轮次
- CLI：`--rounds N` / `--round-end-alive N` / `--round-swap` / `--round-spawn-attacker|defender "x,z"`

**判据（待游戏内验证）**：
1. `--rounds 3` ⇒ 产出 **3 个 jsonl**，`meta.round` = 1/2/3；
2. 每轮交界有 `round_cleanup`（带 `cleanedUp` 人数）+ `round_start`；
3. 末轮结束有 `round_all_done`，mission 正常结束（不卡死）；
4. `--round-swap` ⇒ 第 2 轮攻守兵种与第 1 轮**对调**；
5. **补刀死的样本**落在 `round_cleanup` 之后 —— 分析必须排除（与「死因偏差」同类）
6. **【v0.8.9 追加】时钟同源**：每份 jsonl 内所有事件的 `time` 共享同一原点（该轮起点）。
   `round_start.time ≈ 0`；`round_all_done.time` 应落在同文件遥测主时钟区间内（不超出）。
   ⚠️ v0.8.5–v0.8.8 违反本条（`round_*` 用了整场累计值，实测 round_all_done=300.45 vs 主时钟上限 155.70）——
   **分析这段版本的旧日志时，`round_*` 三条事件的 `time` 不可与同文件其它事件直接比较。**
6b. **【v0.8.10 追加】同源的范围 = 文件内的每一个事件类型，不只 `round_*`**。
   v0.8.9 只修了 `round_*`，而 `probe`（`ScoreHitProbeBehavior._elapsed`）同样从未归零
   ⇒ 每轮文件里仍有两条时间轴（实测 `battle_20260925_111509_578.jsonl`：`probe` 230.39–310.42，
   同文件其余事件上界 91.43）。**该缺陷在 v0.8.9 的校验器下被判成 PASS（假通过）**——
   因为它的参考区间取的是"除 `round_*` 外全体事件的 min/max"，异源事件自己的大值会把区间抬高。
   现行判据（`tools/bl_check_clock_reset.py`，v0.8.10）改为：**锚时钟 = `state`**（每轮必有），
   轮内区间 = `[0, 锚上界 + 3 s]`，并要求**每个类型**的 `time` 起点不晚于锚上界（超出的类型逐一点名）。
   ⚠️ 分析 v0.8.5–v0.8.9 的多轮日志时，`probe` 的 `time` 与同文件其它事件**不同源**（第 2 轮起必现）。

## 十二、首批实测结果（2026-09-24 20:20–20:27，10 场）

### ① 随机种子 / 同种子重放 —— ❌ **不成立**

| 对比 | hit 序列逐值相同率 |
|---|---|
| A(seed 11111) vs B(seed 11111) | **0.3%** |
| A(seed 11111) vs C(seed 22222) | 0.3% |

同种子与异种子的相同率**一样低** ⇒ 种子没能约束战斗演化。原始证据（同为 seed 11111）：
```
A: time=2.0206  px=586.9586  py=762.0095  vy=2.1136
B: time=2.0027  px=586.6436  py=762.2758  vy=1.9847
```
**连开局部署位置都不同**。根因（反编译核实）：
- `MBRandom.SetSeed` 本身有效、也没被别处覆盖（全项目只有一处实现；`Game.RandomGenerator` 只在构造时设），
  **但随机源不止它** —— `Formation` 里有**独立 RNG**
  （`MBFastRandomSelector<Agent>(1024)` / `MBFastRandomSelector<IFormationUnit>(1024)`，`MountAndBlade:140842`），
  再加 native 层的伤害掷点与 AI ⇒ **"设一个全局种子"管不住全局**。
- ⇒ **重放路线放弃**，回到统计路线（多跑 + 控制变量）；§十一 的多轮功能提供了"同一环境"的折中。

### ② 材质差异 7%（换身甲）—— ⚠️ **未验到，且暴露了我自己的观测缺陷**

`dummy_swap` 显示材质请求生效（Cloth/armorBody=4 vs Plate/armorBody=60，各 10 个靶子），
但**躯干类部位（身甲覆盖）的近战扣血中位：CLOTH 43.0 vs PLATE 43.0，Δ = 0.0** ✗

- **我写错的地方**：`dummy_swap` 记的是**请求的物品**材质，不是 agent 身上**实际穿的**——
  换装若被拒绝 / `FillFrom` 未生效，只看请求值就会得到"以为换了"的假象（本轮的假象很可能正是它）。
  v0.8.6 已补 `actualItem`（从 agent 读回），**换装是否真生效待重测**。
- 另一层可能（设计）：Warbandlord 的护甲是 **5 层串行**（头→披风→手套→腿→身甲，每层重跑③~⑦），
  只换**身甲**一层时效应会被稀释。

### ③ 多轮连续实验 —— ✅ **成功**（判据 1/2/3/4 全过）

`--rounds 3 --round-swap --random-seed 777` 产出 **3 个独立文件**，各带 `meta.round` = 1/2/3；
交界处 `round_cleanup`(cleanedUp=2) + `round_start`(cleanedUp=2)；末轮 `round_all_done`；
**第 2 轮出现 `side:"Attacker", troop:"imperial_legionary"`** ⇒ 攻守互换生效 ✓

### ④ 多轮日志「时钟不同源」修复（v0.8.9，2026-09-25 外部评审发现）

**发现来源**：WorkBuddy 侧的静态分析链路（自研 Roslyn 规则 `BLB001`）在 v0.8.8 代码库上命中，
经源码复核 + 106 份真实日志取证确认。

**缺陷**：`RoundLog` 写出的事件都带 `round`（语义属某轮），但 `time` 用的是 `_elapsed`（**整场累计**）。
每轮是独立文件、遥测侧 `TelemetryBehavior.BeginNewRound` 已归零自己那份 `_elapsed` ⇒ **两侧原点不一致**。

**实测取证**（`battle_20260924_220146_854.jsonl`，4435 事件，`meta.round=2`）：
遥测主时钟覆盖 **0.01 → 155.70**，而 `round_all_done` 落在 **300.45** —— 超出主时钟区间 **144.75 秒**。
**静默失真**：不抛异常、不丢数据，只让按 `time` 切窗口的下游分析算错。

**修法**：`Advance` 里 `_round++` 之后、`RoundLog("round_start")` 之前补 `_elapsed = 0f;`，
与 `tb.BeginNewRound` 同步。`_elapsed` 全部 4 处用法（声明 / `+= dt` / 两处遥测写入）
均只服务遥测 `time`，故**直接改语义、不新增字段**（15 行插入、0 行删除）。

**验证**：影子工程编译 0 错误；`BLB001` 自检命中数 **1 → 0**；
`tools/check_repo_encoding.py` 64 文件全合规；`bl_selftest.py` / `bl_metrics_selftest.py` 全部通过 EXIT=0。
**未做真机回归**（见 §十一 判据 6 待验）。

### 顺带修掉的两个真 bug（v0.8.6）

1. **`meta` 产出非法 JSON**（v0.8.4 引入）：拼接式组装里"数字字段不能吃 `\"` 前缀"，
   产出 `"randomSeed":777"` ⇒ meta 被 `load_events` **静默跳过**。已改为每行自闭合。
2. **`dummy_swap` 只记请求值**（见 ②）：已补 `actualItem`（从 agent 读回实际身甲）。

## 十三、自测脚本的编码脆弱点（2026-09-24 晚间续，交接之后）

**症状**：`python tools/bl_selftest.py` 在**纯净 shell**（未设 `PYTHONIOENCODING`）下 **EXIT=1**：

```
File "tools/bl_selftest.py", line 462, in main
    responses = [json.loads(l) for l in out.splitlines() if l.strip()]
AttributeError: 'NoneType' object has no attribute 'splitlines'
```

**这是"基线健康"判据本身失效**：交接要求 `bl_selftest.py` 必须 `EXIT=0`，而它只在"调用者环境恰好设了
`PYTHONIOENCODING=utf-8`"时通过（上一轮会话即在那种环境里跑，所以没暴露）。

**根因（插桩 + 读 `D:\Program Files\Python312\Lib\subprocess.py` 核实）**：

1. 子进程 `bl_mcp.py` 的 stdout 被重定向到管道时，Python 按 **locale 编码**（中文 Windows = GBK）写出，
   而 `bl_selftest.py` 用 `encoding="utf-8"` 读 ⇒ `_readerthread` 抛 `UnicodeDecodeError`；
2. 该异常发生在**读取线程内部**，**被静默吞掉**（`subprocess.py:1598-1600` 的 `_readerthread` 无异常处理）；
3. Windows 版 `_communicate`（`subprocess.py:1603-1651`）最后是
   `stdout = stdout[0] if stdout else None` —— 缓冲区空 ⇒ **`communicate()` 返回 `stdout=None`**；
4. ⇒ 下游 `out.splitlines()` 才炸，**症状与根因（编码）毫无关系**。

**定位方法（可复用）**：给 `subprocess.Popen._communicate` 打包装打印入参/返回值，立刻看见
`stdout=TextIOWrapper → 返回 (None, '')`；再用最小探针（只发 ASCII 的 `initialize`）成功、发中文请求失败，
即锁定"中文 + 编码失配"。**注意**：`out is None` 不等于"空输出"。

**修法（`tools/bl_selftest.py`，两处）**：

1. MCP 子进程的 env 里注入 `PYTHONIOENCODING = "utf-8"`（与父进程读的编码对齐；不依赖调用者环境）；
2. `out is None` 时**显式 `check(False, …)` 并打印 stderr**，让根因可见，不再让症状跑到 `AttributeError`。

## 十四、兵种 id 前置校验 —— 接通 BannerlordSage 索引（2026-09-24）

**要解决的问题**：`bl_start_battle` 的兵种 id 敲错时，C# 侧**静默走 fallback**，日志里看不出来，
代价是白等一整场对局才发现那场根本没按预期开局。

**为什么不再写一遍 XML 解析**：BannerlordSage 已经把全量兵种索引进了一个 SQLite 文件 ——
`F:\Program Files\BannerlordSage\dist\games\bannerlord\bannerlord.db`（见其 `src/utils/env.ts` 的 `dbPath`），
1981 条兵种；实测 Python 标准库 `sqlite3` **只读直连**即可查询，无需启动 bun、无需走 MCP 套娃。

**新增 `tools/bl_sage.py`（只读通道，软依赖）**：

- `status()` / `lookup()` / `check_troops()` / `search()`，另带 CLI（`--status` / `--check` / `--search`）；
- **只读**打开（`mode=ro`）：BannerlordSage 是长驻进程、持有写连接并设了 `busy_timeout`，我们写会撞锁；
- **软依赖**：db 缺失 / schema 变了 ⇒ `available=False` + `reason`，**不抛异常、不阻断主流程**
  （BlBridge 必须能在没装 BannerlordSage 的机器上独立使用）；
- 只依赖业务表 `bannerlord_troops`，**不碰 `*_fts` 虚拟表**（FTS5 能否查询取决于 Python 编译选项，不稳）；
- 启动时校验表 + 必需列齐全，schema 变了就报并把实际列名带回来，而不是抛 `KeyError` 让人猜。

**`bl_mcp.py` 两处接入**：

1. 新工具 `bl_lookup_troop`：校验 id / 按 id 模糊搜索 / 按文化筛选 / 索引可用性自检；
2. `bl_start_battle` 在**发命令之前**校验双方 id，缺失即返回错误 + 近似候选（取首段前缀猜）；
   新增 `skipTroopCheck` 出口 —— 索引只覆盖**官方 XML**（`xml_scope=official`），
   第三方模组的兵种会被判成"不存在"，那种情况必须能跳过，否则等于误伤。

**判据（均已实测）**：

- `--check imperial_legionary battanian_fian_champion imperial_legionarry`
  ⇒ 前两个 OK（L26 / L31），拼错的那个判 MISS；
- `bl_start_battle` 传错 id ⇒ `ok=False`、`missing=['imperial_legionarry']`、给出 5 个 `imperial_*` 候选；
- db 指向不存在的路径 ⇒ `available=False`、`missing=[]`（**不误报**），`bl_start_battle` 不被拦截；
- `tools/bl_selftest.py` 全绿（工具数断言 15 → 16，并新增"bl_lookup_troop 已注册"一项）。

**润色（同日，实测暴露后已修）**：

1. 建议列表原按字母序取前 5 —— `imperial_legionarry` 的建议里**反而没有** `imperial_legionary`
   （它按字母序排在后面）。改用 `difflib.SequenceMatcher`（标准库）按相似度重排，正确 id 排第一；
2. 同一兵种 id 在官方 XML 里被多个文件重复定义（SandBoxCore 与 CustomBattle 都有
   `imperial_infantryman`），`bl_sage.search` 已按 id 去重取首条，否则建议/搜索结果出现同名条目。

**CLI / 跑批接入（同日续）**：

- 读码发现 `bl_cmd.py start` 走 `send_command` **直通通道**、绕过 MCP 工具层校验
  ⇒ CLI 与批量原先对错 id 完全裸奔；
- `suggest_troops` 下沉到 `bl_sage.py`：`bl_batch.py` 按其文件头设计**不 import bl_mcp**、
  只依赖数据通道，而批量预检同样需要给近似候选；
- `bl_cmd.py start`：加同一语义校验（给了 `--attacker/--defender-groups` 时只校验组里的
  troop —— 单兵种参数那时会被游戏端忽略）+ `--skip-troop-check` 出口；索引不可用 ⇒ 警告放行；
- `bl_batch.py`：跑批前**一次性预检全部配置的兵种 id**（含 squad 组 DSL 展开），错一个**整批中止**
  —— N 局全是无效样本比单场失败严重得多；**dry-run 也预检**（它本来就是"验证计划本身"的入口）；
  plan 顶层 `"skipTroopCheck": true` 与 CLI / MCP 是同一个出口；
- **顺带修掉一个既有 bug**：`bl_batch.py` 没调 `bl_common.safe_streams()`，纯净 shell 下打印 `⇒`
  直接 `UnicodeEncodeError`（连 `--dry-run` 都跑不完）—— 与十三节里 bl_metrics / bl_compare
  中过的是同一招；
- 判据（均已实测）：`bl_cmd` 错 id ⇒ rc=2 + 候选（legionary 排第一）；加 `--skip-troop-check`
  ⇒ 警告放行并到达发送阶段；`bl_batch` 坏 plan ⇒ 整批中止 rc=1、好 plan ⇒ dry-run 走完 rc=0；
  `bl_selftest.py` 全绿。

**注意**：MCP 工具列表是**连接时枚举**的 —— 改完 `bl_mcp.py` 后要**重启 blbridge MCP**，
否则 IDE 侧看不到 `bl_lookup_troop`（改完首次验证时就是这个现象）。

**连带修掉的同源缺陷**：`bl_selftest.py` 自己**没有** `bl_common.safe_streams()`（§六 #2 给
`bl_metrics` / `bl_compare` / `bl_death_compare` 修过的那条）⇒ 默认中文控制台下，本文件里含 `⇒` 的
`check()` 标签（⑩⑪ 段）会抛 `UnicodeEncodeError`。已在 `main()` 开头补上 —— 修①之后立刻暴露的就是它。

**判据（两条，均须通过）**：
- 纯净 shell（`PYTHONIOENCODING` 未设）跑 `python tools/bl_selftest.py` ⇒ **EXIT=0**
- 故意 `PYTHONIOENCODING=gbk` 再跑 ⇒ **EXIT=0**（证明修法自足，不靠调用者施舍）

**教训（本项目"静默失效"家族新成员）**：凡是"父进程按 A 编码读子进程输出"，**两端编码都要钉死**，
且读取路径上的异常必须能被看见 —— 线程里的 `UnicodeDecodeError` 就是一个把"编码问题"伪装成
"`NoneType` 没有 `splitlines`"的完美烟幕。

## 十五、材质对照：机理取证 + 两轮实测准备（2026-09-24 晚间续）

**起因**：§九/§十二 的材质对照（`--dummy-body-item` 换身甲）`Δ 中位 = 0.0`，当时的猜测是
"Warbandlord 的 5 层串行把单层效应稀释了"。**反编译取证推翻了它** —— 真因是**改错了装备副本**。

### 取证结论（`ilspycmd` 反编译 Warbandlord.dll → `%TEMP%\wb_decomp`）

| 结论 | 证据（行号） |
|---|---|
| Warbandlord 的护甲/材质公式读 **`victim.SpawnEquipment`** | `Warbandlord.decompiled.cs:7311` `spawnEquipment.GetSpawnEquipmentArmorAmount(...)`；`:7372-7376`；`:11055` `TryGetFirstWarbandlordArmorEffect(this Equipment spawnEquipment, …)` |
| 原版 `Agent.SpawnEquipment { get; private set; }`（返回对象引用） | `Agent.cs:863` |
| §九 的换装写的是 `agent.Equipment`（= MissionEquipment）⇒ **公式根本看不见** | 旧 `DummyRangeBehavior.cs:439` `a.Equipment.FillFrom(eq, banner)` |
| 取**第一个覆盖该部位的层**（Body(5)→Gloves(9)→Leg(8)→Head(7)→Cape(6)），**不是串行叠加** | `Warbandlord.decompiled.cs:5173-5180`、`:11055-11068` |
| 公式：`Threshold = R×A_eff×0.6×随机×mult`、`PR = 0.125^(R·A_eff·0.0215+0.09)` | `:5268-5276`；`EffectiveArmorDefense = CalcScaledArmorDefense(layer.ArmorDefense×multiplier + extra)`（`:12811-12814`）；`multiplier = 引擎护甲 ÷ GetSpawnEquipmentArmorAmount(部位)`（`:7374-7376`） |
| R 表（`config.xml`，路径 `DamageCalc/MaterialResistance/<材质>/PierceResistance`） | Cloth **0.55** / Leather 0.55 / Chainmail 0.6 / Plate **0.65** |

### 干净对照的材料（都已查实）

- **物品对**（护甲数值**完全相同**、只差材质）：
  Cloth `leather_strips_over_padded_robe` vs Plate `aserai_scale_armor_on_cloth` —— 两件都是
  `(body 36, leg 6, arm 7, head 0)`。
  为什么必须数值全同：`armorEffectMultiplier` 的分母 `GetSpawnEquipmentArmorAmount(部位)` 读**物品自身**护甲，
  数值差一点就会同时改动"护甲量"与"材质"两个变量（旧的 `nordic_tunic` 4 vs `plated_leather_coat` 60 就是 15 倍差）。
- **靶子 `imperial_legionary`**：3 套 `EquipmentRoster` **完全相同且全部 Plate**（Body `imperial_lamellar` 等）
  ⇒ 改 Plate 的 R 能 100% 影响它，没有"每场随机选一套 roster"的问题。
- **攻方 `battanian_fian_champion` 20 人 vs 靶子 10 人**：靶子受击 5743 条/3 场，其中
  **近战 Pierce 未被挡 1446 条**、近战 Cut 未挡 2719 条 ⇒ Pierce 样本主要在**近战**（箭大多被盾挡下，仅 47 条）。

### 两轮实测设计（每轮只差一个变量）

**第一轮（零 C# 改动：改 config 的 R 表）** —— `DamageCalc/MaterialResistance/Plate/PierceResistance`：0.65 → 0.85

- 判据①：`bl_read_config` 回读 = `0.85`（`bl_apply_config` 的 dry-run 已预演，`missing: []`）
- 判据②：**近战 Pierce（未挡）**的 `applied` 下降（方向：R↑ ⇒ 阈值↑ ⇒ 伤害↓）
- 判据③：**Cut 不变** —— 阴性对照（本轮只动 Pierce 的 R；若 Cut 也变，说明有其它机制在动）
- 判据④：箭伤（筛 `blocked=false`）方向一致（样本仅 ~47/档 ⇒ 只作辅助）
- ⚠️ **改 config 必须重启游戏**：`ConfigManager` 在 SubModule 构造时读文件，`OnGameInitializationFinished`
  只是把已读入的字典应用回去（工具描述里也写了这句）

**第二轮（v0.8.7 的换装）** —— 用上面那对物品

- 判据①：`dummy_swap.actualItem` == 请求的 item（v0.8.7 起从 **SpawnEquipment** 读回，与公式同源）
- 判据②：**躯干类部位的 Δ ≠ 0，且头/腿/臂不变**（Body 甲只覆盖躯干 ⇒ 归因干净的正面证据）
- 判据③：Cut 与 Pierce 分开看（两件甲 Cut R 都是 0.8、Pierce 0.55 vs 0.65 ⇒ 差异应只出现在 Pierce）

### 本轮改动

1. **`src/DummyRangeBehavior.cs`（v0.8.7）**：换装改走引擎公开 API
   `Agent.UpdateSpawnEquipmentAndRefreshVisuals(Equipment)`（`Agent.cs:3685` 起）——
   它一次做完 SpawnEquipment 赋值 + MissionEquipment 同步（内部 `FillFrom`）+ 驱动属性重算 + 视觉刷新；
   传进去的是自己 Clone 的副本 ⇒ 不污染兵种模板。`dummy_swap.actualItem` 改为**从 SpawnEquipment 读回**。
   顺带删掉手写 `FillFrom` 与为它绕出来的 `Banner` 查找（那段注释里记的坑一并消失）。
2. **`tools/bl_dummy_analyze.py`**：新增 `--compare LABEL=PATH …` 跨档对比。每档先打**生效判据**
   （`meta.version` / `dummy_meta.armor` / `dummy_swap` 的 `item` vs `actualItem` 四态），
   再按部位给 n / 均值 / 中位 / Δ% / Welch t，并把**近战 / 箭伤（筛 blocked）/ 箭伤（不筛，对照）**
   分开报 + 被挡下率。PATH 可为 jsonl / 目录 / 通配符 / `bl_batch` 的 manifest。
   纯函数 seam：`welch_t` / `parse_compare_specs` / `manifest_tiers` / `explode_tiers` / `load_tier` /
   `swap_verdict` / `select_rows` / `compare_tiers` / `render_compare`。
3. **`tools/bl_selftest.py`**：新增 ⑫ 段 `test_dummy_analyze_compare`（welch_t 手算、Δ% 手算、
   生效判据四态、筛选口径、manifest 展开、GBK 控制台端到端）⇒ 全量回归 **EXIT=0**。
4. **plan**：`tools/plan.material_r.example.json`（第一轮）与 `tools/plan.material.example.json`
   （第二轮，已换成上面的干净对）。
5. **部署 v0.8.7**：`dll sha256 = 3EB3B767183E04FB…`，`bl_cmd.py buildcheck` = 文件链条一致
   （源码 = out 产物 = 部署文件）；进程内 DLL 待下次开游戏核对（`bridge_status.json` 里应出现 `3eb3b767…`）。

### 复现命令（下次开游戏用）

```powershell
cd C:\Users\LCGX\CodeBuddy\20260923171333\BlBridge
# 第一轮：baseline（R=0.65，v0.8.7 进程）
python tools/bl_batch.py --plan tools/plan.material_r.example.json --out runs_r065.json
#   → 关游戏 → 改 config（MCP bl_apply_config：DamageCalc/MaterialResistance/Plate/PierceResistance = 0.85）→ 重开
python tools/bl_batch.py --plan tools/plan.material_r.example.json --out runs_r085.json
python tools/bl_dummy_analyze.py --compare base=runs_r065.json r085=runs_r085.json --by bodypart
# 第二轮：换装（同一轮开游戏里可一起跑）
python tools/bl_batch.py --plan tools/plan.material.example.json --out runs_swap.json
python tools/bl_dummy_analyze.py --compare swap=runs_swap.json --by bodypart   # manifest 自动展开成两档
```

### 踩坑（同源第 3 次，已升级教训）

写新测试时又用 `encoding="utf-8"` 读一个带 `PYTHONIOENCODING=gbk` 的子进程 ⇒ 同一个
"读取线程吞异常 ⇒ `stdout=None`" 的坑（正是 §十三 刚修的）。仓库里 `test_compare_manifest_runs`
早有正确示范（子进程 gbk ⇒ 按 gbk 读）。**规则升级：只要父进程读子进程输出，两端编码都显式对齐。**

### 待验证（下次开游戏）

- v0.8.7 的进程内 DLL 一致（`loadedSha256` 应 = `3eb3b767…`）
- 第一轮 baseline / 改后各 3 场（中间必须重启游戏）；第二轮换装 2 档 × 3 场
- 仍**未做**：③ 的换边双跑（立项遗留）；战役层（`meta.mission=game` 未实测）

### 追加修复（同源第 4 次）：MCP 工具描述在宿主里全是问号

**用户报**：Reasonix 的 MCP 页面上 blbridge 的 15 个工具描述全是 "?"。

**根因**（与 §十三 同源，但这次是**用户可见**的）：MCP over stdio 要求 UTF-8，
而 `bl_mcp.py` 没有钉编码 ⇒ Python 按 locale（中文 Windows = GBK）写 stdout、
宿主按 UTF-8 解码 ⇒ `tools/list` 的 7680 字节里 **1489 个 U+FFFD**；
把同一批字节按 GBK 解码则完全正常（探针实测，红绿两态都有数据）。

**修法**：
- 新增 `_force_utf8_stdio()`：`stdin/stdout/stderr` 一律 `reconfigure(encoding="utf-8")`。
  **只在 `main()` 入口调用，不放模块级** —— `bl_selftest.py` 会 `import bl_mcp`，
  模块级改 stdio 会连带污染调用方。
- `bl_selftest` 的 MCP 段**去掉**原先的 `PYTHONIOENCODING=utf-8` 注入（改成"故意不设"），
  并新增两条断言（响应不许出现 `U+FFFD` / 必须含可读中文）⇒ 在**继承 GBK locale** 的条件下
  也能抓住这个回归（原先那层环境变量注入恰好会掩盖它）。

**证据**：修复前 1489 个 U+FFFD → 修复后 **0**；全量自测 `EXIT=0`。
⚠️ **用户侧要"重新连接"该 MCP（或重启 Reasonix）才会生效。**

**教训升级**：本项目"父进程读子进程输出"的编码坑已出现 4 次（§十三 的 MCP 段、
§十四 的测试、以及这次的**真实宿主**）。规则固定为：**凡是跨进程边界传文本，
两端编码都显式钉成 UTF-8，绝不依赖 locale 与调用者环境。**

### 日志归档（2026-09-24，按用户决定执行）

**背景**：`battles/` 已到 **147 场 / 635 MB** 且只增不减 —— 最字面的"数据堆叠"（列目录、
全目录分析都在变慢）。用户决定：**先把剩余小项测完，全面整理留到实测之后**；
日志归档属于实测的"提速项"，现在就做。

**做法（可逆：先压缩 → 校验 → 才删除）**

- **保留** = *最近 20 场* ∪ *`PROGRESS.md` / `README.md` 里被引用过的场次* ⇒ **39 场 / 110.2 MB**
  - ⚠️ 引用识别必须认**两种写法**：完整名 `battle_20260924_184835_525` 与短名 `184910_771`
    （§七 的部位实验就是短名引用的 —— 只认完整名会**漏掉 13 个判据证据**）
- **归档** 其余 **108 场 / 525.2 MB** → `…\BlBridge\battles_archive\battles_20260923-20260924.zip`
  （**42.4 MB**，12.4×。校验：zip 内 108 条目与移出数一致 + `testzip()` 通过 +
  首/中/末 3 个文件 sha256 与磁盘逐一相同，**通过后才删原件**）
- 归档目录自带 `README.md`（策略 + `Expand-Archive` 还原命令），不依赖本文件记忆

**结果**：`battles/` 147 → **39 个文件**，635 → **110 MB**。
⚠️ 归档在项目 git **之外**（游戏日志目录旁），git 历史里看不到 —— 后来人若发现 PROGRESS
引用的场次不在 `battles/`，先看 `battles_archive/README.md`。

**第二次归档（2026-09-24 22:22，当天收尾）**：当天又跑了 ~57 场（**96 文件 / 248 MB**）⇒ 再归档 26 个
（41.7 MB → 3.4 MB）。**策略升级**：保留集合里新增"**被 `runs/*.json` manifest 引用过**"这一维度 ——
今天的批次大多只写进 manifest、没有逐场写进 PROGRESS，只认文本短名会把它们误归档（这正是第一版
"只认两种文本写法"的盲区）。结果：96 → **70 个文件 / 207 MB**
（保留 = 最近 20 场 ∪ 文本引用 26 ∪ manifest 引用 48）。

### 第一轮实测：只改 Warbandlord 的 R 表（2026-09-24 21:27，进行中）

**设计**：唯一变量 = `DamageCalc/MaterialResistance/Plate/PierceResistance`（靶子 legionary 全是 Plate 装备；
本批**不覆盖** `--dummy-armor`，所以护甲值两批都由兵种装备决定、完全相同）

| 批 | 场次 | R（Plate 刺） |
|---|---|---|
| baseline | `battle_20260924_211728_844` / `211801_170` / `211832_808`（v0.8.7，`dummy_meta.armor` 全 -1） | 0.65 |
| 改后 | 待跑 | **0.85** |

**判据① 已通过**：
- `bl_apply_config` dry-run 预演（`missing: []`）→ 实写 `0.65 → 0.85`，工具自带 `verified: 0.85`
- **独立回读**（`bl_mcp.read_config`）：`Plate/Pierce = 0.85`，其余 5 键（Cut 0.8 / Blunt 0.5 /
  Cloth 0.55 / Chainmail 0.6 / Leather 0.55）**逐一与 baseline 相同** ⇒ 无误伤
- 备份 `config.xml.bak_20260924_212718`

**判据②③④ 全过（改后 3 场：`battle_20260924_213328_507` / `213400_879` / `213432_792`；
进程 21:32 启动 ⇒ 读到的就是改后的 config）**：

| 段 | 伤害类型 | base（R=0.65）n / 均值 | r085（R=0.85）n / 均值 | Δ% | Welch t |
|---|---|---|---|---|---|
| **近战** | **Pierce** | 1208 / 32.94 | 1684 / 26.36 | **−20.0%** | **−11.04** |
| 近战 | **Cut（阴性对照）** | 2405 / 24.12 | 3319 / 24.41 | **+1.2%** | 0.82 |
| 近战 | Blunt（样本太少） | 31 / 2.16 | 35 / 3.94 | +82.4% | 1.80 |
| 箭伤（筛被挡） | Pierce | 29 / 19.72 | 26 / 13.15 | −33.3% | −2.29 |

- 被挡下率 92.0% → 94.8%（方向一致：更难穿透 ⇒ 更多被挡）
- ⚠️ 两批总命中数 4773 vs 6586（差 38%）⇒ 战斗演化不同（§七 已记）。但**按伤害类型分组**已控制这一点，
  而 **Cut 几乎不动（+1.2%，t=0.82）** 正是"构成差异没造成系统性偏移"的强证据。

**结论**：**材质抗性 R 确实参与伤害计算** —— R +30.8%（0.65→0.85）⇒ 近战刺伤 **−20.0%（t=−11.04）**，
未被改动的 Cut 纹丝不动 ⇒ 单变量归因成立。**立项第 3 条"材质差异 7%"的机理层面在游戏内得到验证。**
⚠️ 但**"7%"这个具体数字还没对上**：它指的是"同一护甲值下 R=0.55（Cloth）vs 0.65（Plate）"，
而我们现在只有 **0.65 与 0.85 两个点**，落在公式的非线性区（`CalcScaledArmorDefense` 饱和 +
`PR = 0.125^x`）⇒ **必须再补一档（R=0.75）定出曲线才能内插**，单点外推不可靠。

**▶ 三点曲线（R = 0.65 / 0.75 / 0.85，各 3 场；`--by damagetype`；2026-09-24 21:40–21:42）**

| 伤害类型 | R=0.65 | R=0.75 | R=0.85 | Δ%(0.75) | Δ%(0.85) |
|---|---|---|---|---|---|
| **Pierce** | 32.63（n=1237） | 29.25（n=1712） | 26.16（n=1710） | **−10.4%（t=−5.56）** | **−19.8%（t=−10.95）** |
| **Cut**（阴性对照） | 24.12（n=2405） | 24.28（n=3214） | 24.41（n=3319） | +0.7%（t=0.46） | +1.2%（t=0.82） |
| Blunt（样本 <40，忽略） | 2.16（n=31） | 3.33（n=39） | 3.94（n=35） | — | — |

改后 3 场：`battle_20260924_214021_375` / `214053_217` / `214125_369`
（进程 21:37:53 启动、**晚于** 21:37:14 的 config 改动 ⇒ 读到的确实是 0.75；回读也确认了）

- **Pierce 近似线性**：每 +0.10 的 R ⇒ 约 **−10%**（两档增量 −10.4% / −9.8%）
- **Cut 三档纹丝不动** ⇒ 阴性对照在三档上一致成立 ⇒ 改 R 没有牵动别的机制
- **对"材质差异 7%"的回答**：
  - 实测在 **ΔR = 0.10** 下的近战刺伤差 = **−10.4%（t=−5.56，n≈1200/1700）**
  - 外推到低区间（**R 0.55 → 0.65**，即 Cloth vs Plate）：三点近似线性 ⇒ 约 **−10%**
  - ⚠️ 但这**与模型预测的"7%"不是同一个场景**：那 7% 出自"刺伤 A=40、单次命中"的离线公式重建，
    而这里是"fian_champion 打 legionary、混合命中部位、30 秒窗口"⇒ **数量级一致（~10%），不等于同一个数**
  - 要严格对照模型，需把本次的 `A_eff` 反推出来再代入 Warbandlord 公式（未做，列为可选）

**config 已恢复原值**：`Plate/PierceResistance` 0.75 → **0.65**（下次重启游戏生效，不必专门重启）。
备份链可回溯：`config.xml.bak_20260924_212718`（0.65→0.85）、`_213714`（0.85→0.75）、`_214219`（0.75→0.65）。

**顺带确认（换装实验）**：v0.8.7 的换装**在游戏内真的生效了** —— `dummy_swap.actualItem` 与请求值
**6/6 场一致**（Cloth `leather_strips_over_padded_robe` ×3、Plate `aserai_scale_armor_on_cloth` ×3，
`agents=10`）。部位归因也对：**Head 完全不动**（+0.3%，t=0.28），躯干/肩弱显著（`ShoulderLeft` +7.5%，t=3.71）。
⚠️ **但该批对照不干净**：运行时 `armorBody` 是 **25 vs 50**（两件甲的 XML 都写 36 —— 说明加载期有东西
改写了物品护甲，可能是 Warbandlord/平衡 mod 的覆写），所以同时改了"护甲值 2 倍差"与"材质"两个变量
⇒ **只能当"机制生效 + 部位归因"的定性证据，不能当材质效应的定量结论**。要干净对照需按**运行时**
`armorBody` 配对（用 `dummy_swap.armorBody` 做一次"试穿探针"建表）。

### 第二轮（换装）结案 + 换装探针（2026-09-24 21:46–21:50）

**探针设计**：同一件甲跑 3 场（看运行时 `armorBody` 是否波动，即"随机 modifier"假设）+ 建表。
9 场：`battle_20260924_214617_459` … `215029_891`（`runs/runs_probe.json`）

| 档 | item | 材质 | **运行时 armorBody** | XML body_armor | 逐场一致？ |
|---|---|---|---|---|---|
| probe_cloth_36 | `leather_strips_over_padded_robe` | Cloth | **25** | 36 | ✅ 3/3 |
| probe_plate_36 | `aserai_scale_armor_on_cloth` | Plate | **50** | 36 | ✅ 3/3 |
| probe_default_plate48 | `imperial_lamellar`（靶子自带甲） | Plate | **60** | 33 | ✅ 3/3 |

**两条硬结论**：
1. **`armorBody` 是确定性的**（3/3 逐场一致）⇒ **不是随机 modifier** ⇒ "试穿建表"可行（每件跑 1 场就读回真实值）
2. **XML 值不可信、也不能用来配对**：同一个 XML `body_armor=36` 得到 **25** 与 **50**；
   `imperial_lamellar` 在 `body_armors.xml` 里出现两次（33 / 48），运行时却是 **60**
   ⇒ 有 mod 覆写了物品数据（很可能是 Warbandlord 的护甲大修）⇒ **只能信运行时值**。

**② 的判定：换装路线不能用于护甲/材质对照（实测判定）**

| 档 | 运行时 armorBody | 近战 Pierce 均值 | Δ% vs 第一档 | Welch t |
|---|---|---|---|---|
| Cloth 甲 | 25 | 29.35（n=1244） | — | — |
| Plate 甲 | 50 | 28.44（n=1418） | −3.1% | −1.54 |
| 靶子自带甲 | 60 | 28.82（n=1649） | −1.8% | −0.94 |

护甲值 25 → 60（**>2 倍**）而伤害**无显著变化** ⇒ 换装**没有进 Warbandlord 的护甲计算路径**
（对照：§七 改**驱动属性**时护甲 ×1.15 就有 −12.8% ⇒ 那条路径才通）。

**⚠️ 未解（诚实标注，不猜）**：已取证 `ArmorCollection.Create()` 按**全部物品**建 `_armorDict[StringId]`、
`TryGetWarbandlordArmorArea` 是**按 item 查表**（换装后本该查到新甲的 ArmorArea）、
且两件甲都 `covers_body="true"` ⇒ **理论上应当生效**。剩下未查的环节：
`ArmorEffect.CreateArmorEffect` 里 `armorArea.ArmorDefense` 的实际取值、`AttackInformation.ArmorAmountFloat` 的语义。
⇒ **不再深挖** —— 材质/护甲对照已由第一轮那条路（改 R 表 / 覆盖驱动属性）干净回答。

**② 收尾结论**：护甲与材质的对照**一律走第一轮那条路**；换装路线的价值仅剩
"验证换装机制本身可用"（已达成：`dummy_swap.actualItem` 6/6 一致 + `armorBody` 逐场一致）。

### ③ 换边双跑（第一版，2026-09-24 22:00–22:04）：方法跑通，指标设计要改

**做法**：`--rounds 2 --round-swap --round-end-alive 1` + **无 `dummySide`**
（先逐字段核对了立项批 `battle_20260924_194641_881` 那组：`dummySide=None`、Defender=被测 5 人、
Attacker=fian_champion 40 人 —— 与 §③/§九 的口径一致才动手），6 兵种各 1 场。

**跑通的部分** ✅：多轮生效（`meta.round` = 1/2、每轮独立文件），攻守确实互换
（round 2 的 `Attacker` 变成被测兵种 5 人、`Defender` 变成 fian_champion 40 人）。

**round 1（被测兵种当守方）—— 复现立项表** ✅（6 场、每兵种 5 靶）：

| 兵种 | 本次「扣血箭」中位 | §三 立项表 |
|---|---|---|
| aserai_infantry | 4.0 | 3 |
| battanian_falxman | 5.0 | 5 |
| battanian_wildling | 6.0 | 8 |
| imperial_cataphract | 6.0 | 8 |
| khuzait_khans_guard | 10.0 | 9 |
| imperial_legionary | `-`（只 40% 死于箭，样本 <3 ⇒ 拒绝给数） | 7（§九 记 55%） |

**round 2（攻守互换）—— 指标无区分度** ❌：4 场全部得到「fian_champion 扣血箭中位 = **9**」
（4 个完全不同的攻方兵种数值相同）。原因：`--round-swap` 只交换**兵种与位置**，**人数跟着兵种走**
⇒ round 2 变成"被测兵种 **5 人** vs fian_champion **40 人**"；而这个指标是"**每个** fian_champion
个体到死挨几箭"，**与攻方人数无关** ⇒ 只要各攻方的远程单发伤害相近（标枪/投石/弓 ≈18），结果就都是 9。
⇒ **下一版必须改对称配置**（例如 20 vs 20），互换后才仍对称，才能把"位置效应"与"兵种差异"分开。

**本次暴露的两个坑（都值得记）**：

1. **`dummySide: defender` + 多轮 = 第 2 轮永不触发**：不朽靶子 ⇒ 第 1 轮既不会全灭、也到不了
   `EndAlive` ⇒ 只能等 `cap` 超时 ⇒ 只产出 round 1。我第一次按这个配置跑了 6 场，**全部只拿到
   round=1**（5 个文件），整批作废重跑。⇒ **多轮实验不要配不朽靶子**。
2. **2 场缺 round 2**（`aserai_infantry` / `imperial_cataphract`）—— 原因未查（可能第 1 轮未达结束条件，
   或重生失败）。⇒ 若日后要用多轮做正式实验，应先查清这一点。

**结论**：③ 的**方法**已验证可行（多轮 + 攻守互换 + 每轮独立文件 + `unit` 事件可精确归属），
但**指标设计需重做**（对称人数）。

### ③ 换边双跑（第二版，对称 20v20，2026-09-24 22:10–22:12）

**改动**：对称 20 vs 20（6 兵种各 1 场 × 2 轮）。意图：两轮里两个兵种都可能阵亡 ⇒ 比较"同兵种跨轮"。

**跑通** ✅：6 场全成功、11/12 个文件（**`khuzait_khans_guard` 又缺 round 2**）。

**数据**（`arrow_damaging_median` = 扣血箭中位）：

| 兵种 | round 1（fian 攻 / 被测 守） | round 2（被测 攻 / fian 守） |
|---|---|---|
| aserai_infantry | 被测 20 死（35% 死于箭）中位 **3**；fian 死 1 | fian 19 死（100% 箭）中位 **8**；**被测 0 死** |
| battanian_falxman | 被测 20 死（95%）中位 **5**；fian 死 1 | fian 19 死 中位 **9**；被测 0 死 |
| imperial_legionary | 被测 20 死（**0% 死于箭**＝全近战）；fian 死 2 | fian 19 死 中位 **8**；被测 0 死 |
| battanian_wildling | 被测 20 死（95%）中位 **5**；fian 死 3 | fian 死 5 中位 **9**；被测 0 死 |
| imperial_cataphract | **fian 20 全死**（0% 箭＝被骑兵近战砍死）；被测死 3 | 被测 19 全死于近战；fian 无阵亡样本 |
| khuzait_khans_guard | fian 17 死（100% 箭）中位 9；被测 4 死 中位 8.5 | ❌ 缺 |

**结论：位置效应与兵种属性混杂，「到死挨几箭」这个指标在原理上分离不了它** ❌

- 「被测兵种当守方」⇒ 大量死于箭（aserai 3 / falxman 5 / wildling 5）
- 「被测兵种当攻方」⇒ **一个都没死**（fian_champion 弓手被近身压制）
- ⇒ 差异来自**兵种组合本身**（弓手 vs 近战），不是"攻/守位置"；位置互换改变不了"谁强"。
- ⇒ **要分离位置效应只能用 §④ 的镜像口径**（同兵种互换 + 胜负份额 = `mirror_cross_check`）；
  "挨几箭"是**单方属性**，做不了这种分解。
- ✅ 副产品有价值：`imperial_cataphract` 当攻方时**把 20 个 fian_champion 全砍死**（0% 死于箭），
  当守方时却被射死 ⇒ 直观量出"重骑兵冲弓阵"的不对称。

**新发现的稳定性问题**：多轮的 **round 2 会丢** —— 累计 **3 次**（第一版 2 场 + 本版 1 场，
约 1/6）。⇒ 若日后要用多轮做正式实验，**必须先查清"round 2 偶尔不产出"**（疑与 `cap` 超时或重生失败有关）。

### ③ strict 版：镜像双跑（2026-09-24 22:14–22:19，12 场）——**答案有了**

**方法**：改用 §④ 已实现的**镜像口径**（`bl_compare --manifest`；两个 config 互为攻守互换 +
固定 `playerSide`）⇒ 它把"满编窗口占比"分解成**位置效应**与**兵种差异**两块，并有对称性自洽核对。

| 兵种对（每方向 3 场） | **位置效应** | **兵种差异** | 对称性核对 |
|---|---|---|---|
| `imperial_legionary` vs `battanian_wildling` | **+5.8** | **−45.7** | 和 111.6% = 100 + 2×5.8 ✓ |
| `imperial_cataphract` vs `battanian_fian_champion` | **−14.5** | **−71.0** | 和 71.0% = 100 + 2×(−14.5) ✓ |

**可复现性**：`legionary vs wildling` 的**兵种差异**与 §④ 早先那次几乎完全相同（**−45.7 vs −46.0**）✓
而**位置效应**从 −1.7 变成 +5.8（**连符号都变了**）⇒ 它是小量且不稳，要更多场次才测得准。

**⇒ ③ 立项问题的回答（这是立项遗留项要的那个答案）**：

1. **兵种差异是强且可复现的信号** ✓（两次差 0.3 个百分点）
2. **位置效应不是常数，随兵种组合变**（legionary/wildling ≈ 0；cataphract/fian = −14.5）
   ⇒ 「攻守位偏差」**无法用统一修正消掉** ⇒ **镜像双跑不是可选项、是必需品**（每个组合单独测）。
   ⇒ §④ 那句"对称化把攻守位偏差压到接近零"只对**特定组合**成立，**不能当通则** —— 这是本次的主要修正。
3. 副产品：`cataphract` 当攻方时**满编窗口输出份额 = 0.0%（SD 0.0）** —— 骑兵还在冲刺时弓手已开火，
   第一例击杀之前骑兵**一点伤害都没造成** ⇒ 直观量出"冲锋延迟 × 远程压制"。

⚠️ **更正我早先的一处口误**：上一版我写"cataphract 当攻方把 20 个 fian_champion 全砍死" —— 看错了列：
那张表里的"fian 20 全死"属于 **round 1（fian 当攻方）**；round 2 里是 **cataphract 19 死**。
真相是"**fian_champion 当守方赢、当攻方输**"，这也正是本版位置效应为负（−14.5）的来源。

**产出文件**：`runs/runs_mirror1.json`（6 场）、`runs/runs_mirror2.json`（6 场）；
报告可直接复现：`python tools/bl_compare.py --manifest <上述 manifest>`。

### 规模梯度实验（2026-09-24 22:22–22:25，新跑 6 场 + 复用 3 场）

**动机**（用户提出）：20v20 在地图上太密 ⇒ 可能"人人打到人"、命中被高估；50v50 里后排可能够不到敌人。

**做法**：同兵种对（`imperial_legionary` 攻 vs `battanian_wildling` 守）、`cap 30` 等其余参数**完全一致**，
只变规模：20v20 复用今天镜像批的 3 场；新跑 50v50 与 100v100 各 3 场（`tools/plan.scale.example.json`）。

| 档 | 参战 agent | 零命中% | 每单位命中 | 攻方占比（满编） | 时长 | 自洽 |
|---|---|---|---|---|---|---|
| 20v20 | 40 | **0.0** | 21.8 | 33.0 | 268s | ok |
| 50v50 | 100 | 0.3 | 19.8 | 18.9 | 301s | ok |
| 100v100 | 200 | **5.2** | **17.1** | 23.6 | 301s | ok |

**结论**：

1. **规模确实有影响**（用户方向对）：每单位命中**单调下降** 21.8 → 19.8 → 17.1（−22%）；
   100v100 有 **5.2% 的人一次都没打中**。
2. **记录在大规模下仍然可信**：入场人数**精确**（40/100/200 ⇒ **未触发原版"增援分批"**）、
   `verdict=ok`、`nanCount=0`、坏行 0 —— 三档全过。⇒ "100v100 读不准"**目前未出现**
   （真风险仍是**未设防的 `Agent.Index` 复用**：大规模死亡时才可能暴露）。
3. ⚠️ **更正我上一轮的说法**：我曾用**混合批次**算出"20v20 有 32% 零命中"；**控制变量后**（同兵种对、
   同 cap）20v20 是 **0.0%** ⇒ 零命中率主要是**兵种组合与战斗时长**的函数，规模只是次要因素。
   这正是"控制变量"的价值 —— 差点用一个跨批次对比误导结论。
4. **可外推性**：满编窗口占比 33.0 → 18.9 → 23.6 ⇒ **方向三档一致，但绝对值随规模摆动 ~±7 个百分点**
   ⇒ 20v20 的**绝对值不能直接外推**到 100v100（且 n=3 偏少，要压准需加场次）。

### 候选扩展（已评估，**暂不做** —— 2026-09-24，用户裁定先收尾实测）

用户提议借鉴两个 mod 扩大可测的实验类型。**已查：本机均未安装**（`Modules/` 里只有内置 `CustomBattle`）。

- **Enhanced Battle Test**（[Nexus mods/2](https://www.nexusmods.com/mountandblade2bannerlord/mods/2?tab=docs)、
  [GitHub](https://github.com/lzh-mb-mod/EnhancedBattleTest)）：每方 **8 组部队 × 8 阵型**（多兵种混编）、
  可导入战役部队、**装备修饰符 Random / Average（去随机，"同装备即同护甲"）/ None**、Tactic Level（AI 战术档）、
  战斗 AI 0–100 可调、Training / Undead mode、攻城（含部署）、地图与**日期/时刻/天气**。
  ⚠️ 开战会**改战役数据 + 禁存档**（需弃档）；纯 UI 交互 ⇒ 对"无人值守自动化"没有直接帮助。
- **Arena Overhaul**（[Nexus mods/3477](https://www.nexusmods.com/mountandblade2bannerlord/mods/3477?tab=description)）：
  三种练习模式（Expansive / Team / Parry）、可调**比赛强度、AI 队伍数量、防御装备类型** ⇒ 面向上手训练，非数据。

**值得搬进 plan 的维度**（按价值排序）：
1. **多兵种混编** —— 现在只能"**单兵种 × 全场 charge**"，这是"能多测类型"的**真瓶颈**（`ScenarioRunner` + plan schema）
2. **装备修饰符去随机 / 至少记录实际值** —— 直接消掉一个混杂源（见下）
3. AI 队伍数 / 多队混战（v0.8.5 多轮是地基）
4. 阵型与命令参数（现在只有 `charge`）、初始站位
5. 战斗 AI 档位**可设**（现在只能观测 26 个 ai 参数）
（1v1 对练**现在就能做**：`--a 1 --d 1` + 不朽靶子，无需扩展。）

**为什么排在实测之后**：扩展要动 `ScenarioRunner`＝**实验仪器** ⇒ 一旦改，已跑的 baseline 口径与之后不可比；
而且**现在装 mod 会立刻污染进行中的实测**（EBT 依赖 Harmony、改战役数据、禁存档）。

**顺带线索（下一批实验要查）**：EBT 的 "Average" 选项说明"**同一件甲在不同士兵身上护甲值会不同**"
是这游戏的常态（随机 modifier）⇒ 换装实验里 `armorBody` 读回 **25 / 50**（两件甲 XML 都写 36）
**很可能就是物品随机 modifier**。试穿探针要**每件甲跑多场、看 `armorBody` 是否波动**：
波动 = 随机 modifier（好修：spawn 时固定或记录）；恒定 = Warbandlord 类**静态改写**（得换物品对）。

## 十六、多兵种混编 + 战术命令（v0.8.8，T1–T8 已入库，T9 待验）

> 本节由 **Task 10**（2026-09-25）产出；当时另一个任务正在改 `PROGRESS.md`，故先落独立草稿、收尾时并入（只调编号与元说明，正文未改）。
> ⚠️ **T9（游戏内 5 条判据）尚未执行** ⇒ 判据结果一律写 `（待 T9）`，**不填任何数字**。

### 1. DSL 语法

`<troop>:<count>[:<formation>[:<movement>]]`，多组用 `|` 分隔。

- `troop`：兵种 id（**不做本地校验**，由游戏端报 `unknown_troop`，与 `--dummy-body-item` 同策略）。
- `count`：整数 ≥ 1。
- `formation`：`FormationClass` 的引擎名，**大小写不敏感**；取值只允许
  `Infantry | Ranged | Cavalry | HorseArcher | Skirmisher | HeavyInfantry | LightCavalry | HeavyCavalry | General | Bodyguard`。
  省略 ⇒ 引擎按兵种决定编队。
- `movement`：`charge | advance | fallback | stop | retreat`（小写）。省略 ⇒ `charge`。
- plan 里值可以是 **DSL 字符串**或**组列表**（每项 dict 含 `troop/count/formation/movement`）；
  是列表时先转成 DSL 字符串再解析（**只有一条解析路径**）。

**两条能力边界（写死）**：

1. **`formation` 不能下发** —— `IAgentOriginBase` 的 18 个成员里**没有 formation**；编队由
   `BasicCharacterObject.GetFormationClass()` 按兵种决定（那是引擎 `virtual`，改它会污染共享的
   `CharacterObject`，不可取）⇒ `formation` 只做**解析时校验 + 日志里记录实际编队**（GC4 观测量不受影响）。
2. **`hold` 已移除**（2026-09-25，用户按编程规则批准）—— 写 `hold` 在解析期报错并提示改用 `stop`；引擎层 `hold` 本来就等于 `stop`（`MovementOrder` 无 Hold 实例）。

### 2. Global Constraints（GC1–GC6，逐字照计划）

- **GC1 DSL 格式（定死）**：`<troop>:<count>[:<formation>[:<movement>]]`，多组用 `|` 分隔。
  - `troop`：兵种 id（**不做本地校验**，由游戏端报 `unknown_troop`，与 `--dummy-body-item` 同策略）
  - `count`：整数 ≥ 1
  - `formation`：`FormationClass` 的引擎名，**大小写不敏感**，取值只允许：
    `Infantry | Ranged | Cavalry | HorseArcher | Skirmisher | HeavyInfantry | LightCavalry | HeavyCavalry | General | Bodyguard`
    ⚠️ **修订（2026-09-24 23:5x，用户批准路 A）**：`IAgentOriginBase` 无 formation 成员、
    编队由 `BasicCharacterObject.GetFormationClass()` 按兵种决定 ⇒ 该字段**不能下发**，
    语义降级为"**解析时校验 + 日志里记录实际编队**"（GC4 的观测量不受影响）。
  - `movement`：`charge | advance | fallback | stop | retreat`（小写；映射见 T4）
  - 示例：`imperial_legionary:10:Infantry:stop|khuzait_khans_guard:5:HorseArcher:charge`
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

### 3. 位置偏移量（确切值）

多轮重生时，**第 i 组**的进场点 = 基点 `x + i*12f`（y/z 不变）；基点未指定 ⇒ 交回引擎默认。

### 4. movement 落点裁决

- 按该组兵种的**实际编队**下发 movement。
- 同一**实际编队**被多组以**不同** movement 命中 ⇒ 报错 `conflicting_movements`。
- `groups` 与 `orders != "charge"` 混用 ⇒ 报错 `conflicting_orders`。

### 5. T9 判据清单（5 条，逐字照计划 §T9）

1. `bridge_status.json` 的 `version = 0.8.8`、`loadedSha256` = 部署 sha、`fileChangedSinceLoad = false`；
   ⚠️ **核对口径（2026-09-25 实测，重要）**：`out\BlBridge.dll` **每次重编译的 sha256 都不同**（Roslyn 命令行默认
   非确定性：嵌入 MVID / PE 时间戳）——同一份未改动的源码，多次 `build.ps1 -Deploy` 得到 `531AAB84…` / `19F66AD1…` / `2183A94B…`。
   ⇒ **以 `python tools/bl_cmd.py buildcheck` 的 `deployedSha256` 为准**（它给出当前部署值；游戏跑起来后再看 `loadedSha256` 是否与之一致）。
   **T9 开始前不要再跑 `build.ps1`**（会换成一个新 sha）。T9 所用部署值（2026-09-25 00:13:53，T12 修复后）：
   `2183A94B94BBCF1175F2DDFAB21E3E01F1BA0D28B9E6C143FCA7C0CB5BE3B295`。
   **结果：✅ 通过**（2026-09-25 00:15，游戏重开后）：`buildcheck` ⇒ `code=ok`、`builtVersion=0.8.8`、
   `deployedSha256 = loadedSha256 = 2183a94b94bbcf11`、`loadedVersion=0.8.8`、`fileChangedSinceLoad=False`。
2. 跑一个 **2 组**的 plan（例：攻方 `imperial_legionary:10:Infantry:stop` + `khuzait_khans_guard:5:HorseArcher:charge`）
   ⇒ 日志出现 **2 条 `squad`**，`spawned` 分别 = 10 与 5；
   **结果：✅ 通过** —— `battle_20260925_001508_814.jsonl`（2.42 MB）里共 **3 条** `squad`（双方都给了 groups ⇒ 每组一行；
   判据本意即"每组一行"）：攻方 `group 0 imperial_legionary count=10 formation=Infantry movement=stop` + **`spawned=10`**、
   攻方 `group 1 khuzait_khans_guard count=5 formation=HorseArcher movement=charge` + **`spawned=5`**、
   守方 `group 0 battanian_wildling count=15 movement=charge spawned=15`。
3. `unit` 事件的 `formation` 与 `squad` 一致（10 个 Infantry、5 个 HorseArcher）；
   **结果：✅ 通过** —— 同一日志 `unit` 事件的 `(side, formation)` 计数：`Attacker/Infantry=10`、`Attacker/HorseArcher=5`、
   `Defender/Infantry=15`，与三条 `squad` 逐一对应。
4. **行为可辨**：`stop` 那组在开局不发冲锋（`state` 的前若干秒位移 < 阈值）—— 与 `charge` 组对比；
   **结果：✅ 通过** —— 开局 10 mission 秒（`state` 采样自 t≈2.03 s）按兵种位移均值：
   `Attacker/imperial_legionary`（stop）**0.00 m**（max 0.00、均速 0.00，10 个 agent 全部原地）；
   `Attacker/khuzait_khans_guard`（charge）**86.01 m**（均速 8.55）；`Defender/battanian_wildling`（charge）21.03 m。
5. **GC2 回归**：用**旧 plan**（`tools/plan.mirror.example.json`）跑 1 场 ⇒ `squad` 事件 **0 条**（无 groups ⇒ 不产生新事件），
   且 `end.validity.verdict = ok`、`nanCount = 0`、坏行 0。
   ⚠️ 计划原文写"`squad` 事件为单组"；**实测 0 条** —— 比计划原文更严格，且这才是 GC2（旧 plan 不产生新事件）的正确含义。
   **结果：✅ 通过** —— `battle_20260925_001613_812.jsonl`（单兵种 20v20，字段等价于旧 plan 的一个 config）：
   `squad=0`、`bad lines=0`、`verdict=ok`、`nanCount=0`、`ioFailed=false`、`reason=defenderWiped`（守方全灭，正常结束）。

**T9 结论：五条判据全部通过（2026-09-25 00:15–00:17，游戏内实测）。**
⚠️ 首跑即暴露一个**离线测试发现不了**的缺陷（mission 之外访问 `MovementOrder` ⇒ `TypeInitializationException`，
且永久污染该类型），已在 T12 修复（commit `dba3d01`）并重新部署后复测通过 —— 详见下方 §6。

### 6. T9 首跑暴露并修复的缺陷（T12）

**现象（2026-09-25 00:15，判据 2 第一次跑）**：`bl_cmd.py start --attacker-groups …` 返回
`handler_exception: TypeInitializationException: The type initializer for 'TaleWorlds.MountAndBlade.MovementOrder' threw an exception.`
（`MapMovement` ← `ValidateGroupedSide` ← `Start()`）。

**根因**：`MovementOrder` 是 struct，其静态字段（`MovementOrderCharge/Stop/…`）在**类型初始化**时构造实例；
而 `Start()` 跑在 **mission 之外**（游戏停在自定义战斗界面）⇒ 访问即抛。更严重：.NET 会把静态构造失败的类型
**永久标记为不可用** ⇒ 同一进程内后续任何 `MovementOrder` 访问都抛（**连旧路径 `ApplyCharge` 也一起坏**）。
T5 的 M2 修复（把冲突校验从"比字符串"改成"比落点"）首次在 `Start()` 里碰它 ⇒ 埋下此缺陷。

**为什么之前全绿**：编译、`tools/jsontest`、`bl_selftest.py` 在 mission 之外**不执行**引擎类型初始化 ⇒
**离线测试在结构上不可能发现它**；只有 T9（游戏内）能暴露。

**修法（commit `dba3d01`）**：新增**纯字符串** `MovementKey(string)` 承担落点比较，彻底不碰 `MovementOrder`；
错误码 / 消息文本 / 逐字回显 / `resolvedTroops` 输出**逐字未变**。T11 移除 `hold` 后 5 个 movement 与 5 个落点
**一一对应** ⇒ 字符串比较 ≡ 落点比较（语义零变化）。`MapMovement` 的 doc 补上"**只允许在 mission 内调用**"。
修后全仓 `MovementOrder` 出现点逐条自查 ⇒ 无 mission 之外的执行点。
复测：重新部署（`2183A94B…`）+ 重开游戏 ⇒ **判据 2–5 全过**。

**教训（可复用）**：任何"在 mission 之外访问引擎类型"的代码都可能在**离线全绿**的情况下炸，
且后果是**进程级永久污染**（该类型在本次运行里再不可用）。这类缺陷只能靠游戏内判据兜住 ——
这正是计划把 T9 定为必过项的价值。

### 7. 提交记录

- `ab5c3a3` —— **T5**：`src/ScenarioRunner.cs` 自定义 `IMissionTroopSupplier`（`SquadTroopSupplier`）按组建队/编队/下命令。
- `20e8d1e` —— **T6**：`src/RoundOrchestratorBehavior.cs` 多轮重生与每轮命令重申按组（`x = base.x + i*12f`）。
- `8af3989` —— **T7**：遥测落点 —— `unit.formation` 字段 + `squad` 事件 + `EnumNames.Formation` 别名映射（按真值写死）。
- `d2c8623` —— **T8**：版本 `0.8.8`（`src/BridgeConfig.cs` + `module/SubModule.xml`）+ 编译部署
  （`out\BlBridge.dll` 与模块目录 DLL sha256 逐字一致）。
- `b775b08` —— **计划勘误**：§T5 规格段三处作废（self-spawn / 位置分段 / 指定编队）+ movement/`hold` 落点裁决。
- `7fc8157` —— **T10**：文档与示例 plan（`README.md` §七 事件格式 + `tools/plan.multitroop.example.json`）。
- **T11**：移除 DSL 的 `hold`（引擎层本就等同 `stop`）—— 写 `hold` 在解析期报错并提示改用 `stop`；连带删除 `OrderNotes`/`AppendOrderNote`/`orderNotes`（唯一用户消失）；文档/测试同步为 5 词。（commit `706b88f`）
- `dba3d01` —— **T12**：修 T9 游戏内实测暴露的 `TypeInitializationException` —— `Start()` 不再触碰 `MovementOrder`，
  改用**纯字符串** `MovementKey` 做落点比较（语义等价：5 词与 5 落点一一对应）。另 `676b23c` 记录"dll 非确定性编译"的核对口径。

---

## 十七、全兵种扫描：可用性判定 + 覆盖扫描（2026-09-25，T15）

**要回答的两个问题**：① 3200 个候选 id 里游戏端认哪些？② 认的里面，哪些"通过校验却没真正生成出来"？
前者是清单质量问题，**后者才是真正的兵种 bug**。

**工具**：`tools/bl_troop_sweep.py`（新增，输出 UTF-8 中文）

- `probe`（可用性判定）：每包 id **追加一个已知坏 id 当引信**，让每次 `start` 必然以 `unknown_troop`
  收场 ⇒ **永不建 mission** ⇒ 绕开「成功 start 之后 abort 拉不回 loading」的引擎卡死（已复现 2 次）。
  配合 v0.8.8 的 T14（错误消息**一次报出全部**坏 id），4 包就拿到完整清单 ——
  不需要二分，也不需要像上一轮那样真去打 11 场战斗。
- `cover`（覆盖扫描）：用可用 id 跑真战斗（每场 300 个 id、每方 150、每 id 1 人），等 `ended`
  （**不 abort**），再从该场 JSONL 的 `unit` 事件取 `troop` 集合与期望集合比对。
  判据可靠：`unit` 在 `OnAgentBuild` 对**每个** agent 写一行，不受 `state`/`ai` 采样限制。

### probe 结果（3200 个 id → 4 包）

| 包 | 坏 | 好 |
|---|---|---|
| 0..800 | 403 | 397 |
| 800..1600 | 518 | 282 |
| 1600..2400 | 20 | 780 |
| 2400..3200 | 0 | 800 |
| **合计** | **941** | **2259** |

与 2026-09-24 的结论（941 / 2259）**逐包一致** —— 差别是这次拿到了**完整坏 id 清单**
（上一轮只拿到每包前 8 条）。产物：`.sdd/2026-09-24-multitroop-tactics-plan/sweep_probe_full.json`。

### cover 结果（8 场，2259 个可用 id 全量覆盖）

```
cover_01..07: 期望 300 / 实际 unit 兵种 300   缺 0、多 0
cover_08:     期望 159 / 实际 unit 兵种 159   缺 0、多 0
覆盖缺口合计 0 个
```

⇒ **零缺口：不存在"通过校验却没 spawn 出来"的兵种** ⇒ 本轮**没有真正的兵种 bug**。
产物：`.sdd/.../sweep_coverage.json`。

### 精确分类（修正上一轮"全是非战斗 NPC"的归纳）

上一轮那句话**不准确**。用 Sage 索引的 `occupation` 字段对 941 个被拒 id 客观统计：

| occupation | 个数 | 说明 |
|---|---|---|
| Lord | 500 | 领主/贵族（`lord_*`、`dead_lord_*`） |
| Soldier | 146 | **战斗单位**，但挂在剧情/任务系统：`conspiracy_*`（阴谋部队）、`*_contender*`（竞技场对手）、`borrowed_troop`、`anti_imperial_conspiracy_boss` … |
| Wanderer | 67 | 游荡者/同伴 |
| Merchant | 55 | 商人 |
| NotAssigned / Special / Bandit / Gangster / Mercenary | 79 | `spc_*`（特殊人名与装备模板）、帮派打手等 |
| 平民职业 | 94 | Headman / Artisan / Preacher / RuralNotable / GangLeader / GoodsTrader / Townsfolk / Villager / None |

**准确表述**：它们都不是「自定义战斗可用的兵种」，而是**剧情/任务/竞技场/领主/平民职业 NPC**。
其中 225 个的 occupation 属战斗类，但全部挂在剧情或任务系统上
（`conspiracy_*`、`contender_*`、`thug_*`、`storymode_*`、`tutorial_*`）。

**机制**：`unknown_troop` = 游戏端 `Resolve` 返回 null ⇒ 这些 id **不在运行时对象表里**；
而 BannerlordSage 索引是**静态读 `ModuleData/*.xml`**，于是收录了一批"写在 XML 里、但游戏运行时
并不注册为兵种"的条目（如 `spspecialcharacters.xml` 里的特殊人物/装备模板）。
⇒ 给 MCP 联动的建议不变：`bl_sage` / `bl_lookup_troop` 的说明要写明
「**索引含非战斗条目，不等于可 spawn 的兵种**」。

### 本轮修掉的 3 个自写工具 bug（都是"干跑绿灯、真跑才炸"）

1. `bl_mcp.send_command` 的契约是 **`(响应 dict, 错误字符串)`**，第一版当裸 dict 用 ⇒ `AttributeError`。
2. 控制通道的方法名是 **`start_battle`**（不是 CLI 子命令名 `start`）⇒ 游戏端 `unknown_method` 拒掉。
3. 引信剔除：第一版把"等于引信 id 的条目"全剔除；而引信本身也在清单里（第 3 包第 1801 个），
   游戏端把它当**两个坏组**分别报出 ⇒ 少算 1 个坏 id（19 vs 20）。改为**只跳过引信那一组**。

### 一条口径修正

`ended` 之后引擎**可能长时间不回 idle**（实测 120 秒仍未回，但 `busy` 已是 `false`），
所以 `cover` 的等待改成「等 `ended` + 软等 idle（默认 10 秒，超时就继续）」，
与 `bl_batch.py` 的实跑口径一致 —— 硬等 idle 会把扫描**卡死在第一场**（本次实际发生）。

### 环境与产物

- 部署：dll sha256 `fcca590bdc7a6472`（本会话重新构建并部署；`SubModule.xml` 同时统一为无 BOM + LF）。
- 战斗日志：`battle_20260925_0214*.jsonl` … `battle_20260925_021822_999.jsonl`（cover 的 8 场）。
- 窗口：8 场 × 约 30 秒（cap 20 游戏秒）。

---

## 十八、T13 复测（游戏内）：**未通过** —— 根因精确锁定 `ChargeToTarget`（2026-09-25）

**复测配置**（攻守各一个 `stop` 组 + 一个 `charge` 组作对照）：

```
attacker-groups "imperial_legionary:10:Infantry:stop|khuzait_khans_guard:10:HorseArcher:charge"
defender-groups "battanian_wildling:10:Infantry:stop|battanian_fian_champion:10:Ranged:charge"
```

日志：`battle_20260925_023155_120.jsonl`；部署 dll `82bd8cec4938eb0f`（`AssemblyVersion` 0.8.8.0）。

**判据与结果（未通过）**

| side | troop | 组配置 | 平均速度 | 位移 | `order` 事件统计 |
|---|---|---|---|---|---|
| Attacker | imperial_legionary | **stop** | 0.186 | 72 m | **Stop 116/116** ✓ |
| Attacker | khuzait_khans_guard | charge | 8.928 | 117 m | Charge（对照）✓ |
| Defender | battanian_wildling | **stop** | **0.904** | **154 m** | **ChargeToTarget 59 / Stop 56** ✗ |
| Defender | battanian_fian_champion | charge | 0.771 | 148 m | ChargeToTarget/Charge（对照）✓ |

**根因（精确）**：守方编队的 order 被引擎的**防守战术 `ChargeToTarget`** 覆盖，而且**每 1–2 秒来回拉锯**：

```
t= 0.42 ChargeToTarget → 2.43 Stop → 3.43 ChargeToTarget → 4.43 Stop → 5.43 ChargeToTarget → 7.43 Stop → …
```

⇒ T13 原来的修法（"组路径每 0.5 秒重申 order"）只能与它**拉锯**，净效果是守方一路走到 154 米外。
攻方（非防御方）没有 `ChargeToTarget` 覆盖，所以 order 全程保持 `Stop`。

**结论**

1. 交接文档里"根因可能是 team 级 `TacticCharge` 覆盖"——**方向对、具体战术猜错了**：
   实际是 `ChargeToTarget`（守方的防守战术）。
2. "周期性重申 order"这条路**已被证伪**（拉锯无效）⇒ 修法应换成下面之一：
   - **让该编队脱离 AI 战术**（首选：编队不再被 team 战术覆盖，只执行我们下发的 order）；
   - **直接设置守方的战术**为"保持不动"，让引擎意图与我们要的**一致**，而不是持续对抗；
   - 每**帧**重申 order（像 `--dummy-armor` 那样硬抗；代价是持续对抗与抖动）。
3. 这类修复**必须走游戏内判据** —— 离线测试在结构上发现不了（与 T12 `TypeInitializationException` 同一教训）。

**已具备的紧反馈循环**（`/diagnosing-bugs` 的第一步）：一次 `start`（约 60 秒）+ 读该场 JSONL 的
`order` 事件与按 troop 的 `state.speed`/位移，即可判定"守方 stop 组是否被覆盖"。

### T13 复测（第二次，修法①）：**通过** ✅

修法①（`SetControlledByAI(false, false)`，提交 `ceafe6e`）部署（dll `27c3587f58bb904a`）后，
用**完全相同的复测命令**重跑，日志 `battle_20260925_023826_544.jsonl`：

| side | troop | 组配置 | 平均速度（修前→修后） | 位移（修前→修后） | `order` 事件（修前→修后） |
|---|---|---|---|---|---|
| Attacker | imperial_legionary | stop | 0.186 → **0.036** | 72 → **29 m** | Stop 116 → Stop **240/240** |
| Attacker | khuzait_khans_guard | charge | 8.928 → 9.455 | 117 → 232 m | Charge（对照） |
| **Defender** | **battanian_wildling** | **stop** | **0.904 → 0.000** | **154 → 8 m** | **ChargeToTarget 59 / Stop 56 → Stop 240/240** ✅ |
| Defender | battanian_fian_champion | charge | 0.771 → 0.814 | 148 → 207 m | Charge（对照） |

**判据全部满足**：

1. 守方 stop 组的 `order` 事件**不再出现 `ChargeToTarget`**（240/240 全是 `Stop`）
   ⇒ team 级 `TacticCharge` 再也改不动我们下发的 order；
2. 守方 stop 组平均速度降到 **0.000 m/s**，位移从 154 m 降到 **8 m**（1/19）；
3. 两个 **charge 组（对照）照常在冲** ⇒ 证明**没有冻住战斗** —— 这正是刻意不用官方那句
   `SetIsAIPaused` 的原因（它会连士兵个人行为一起冻结，只有纯性能基准才需要）。

⇒ **T13 缺陷关闭**。这条也再次印证：`MovementOrder`/编队 order 这类问题**只能靠游戏内判据**，
离线编译与 jsontest 在结构上发现不了（与 T12 同一教训）。

---

## 十九、v0.8.9 发布（2026-09-25）

**tag**：`v0.8.9`（注释 tag，锚定 `d2a2be2`）
**版本落点**：`module/SubModule.xml` = `v0.8.9`；`src/BridgeConfig.cs` `Version` = `"0.8.9"`
**部署产物**：`Modules/BlBridge/bin/Win64_Shipping_Client/BlBridge.dll`，sha256 前缀 `c935fa3d0ca6b99a03490317`

> **tag 重建说明**：本 tag 最初锚在 `65db2b3`（仅含时钟修复，产物 sha `14d788d2`）。
> 随后在真机测试中因传入不存在的场景名导致引擎崩溃，新增了场景存在性校验（`d2a2be2`），
> 产物变为 `c935fa3d`。因 v0.8.9 从未向外发布，故**弃用旧 tag、重建在本提交上**，
> 使「tag = 可部署产物」这一语义成立。

### 1. 本次发布包含的代码改动（两处）

#### 修复 1：多轮日志内时钟不同源（外部评审发现，详见 §十六 ④ 与 README 第 402 行）

- **缺陷**：同一 jsonl 内 `round_start` / `round_cleanup` / `round_all_done` 的 `time` 复用了
  `RoundOrchestratorBehavior._elapsed`（整场累计值），而每轮遥测是独立文件、其
  `TelemetryBehavior.BeginNewRound` 已把遥测侧 `_elapsed` 归零 ⇒ **两条不衔接的时间轴**。
  实测样本 `battle_20260924_220146_854.jsonl`：遥测主时钟 `0.01 → 155.70`，
  而 `round_all_done` 落在 **300.45**（超出 144.75 秒）。
- **性质**：**静默失真** —— 不抛异常、不丢数据，仅让按 `time` 切窗口的下游分析算错。
  这正是它能在 v0.8.5–v0.8.8 四轮里存活的原因：离线编译、jsontest、人工读日志**全都看不见**。
- **修复**：`Advance` 内 `_round++` 之后、`RoundLog("round_start")` 之前补 `_elapsed = 0f;`，
  与 `tb.BeginNewRound` 共用同一原点。15 行插入 / 0 行删除，不新增字段。

#### 修复 2：开 mission 前校验场景存在性（2026-09-25 实测崩溃后新增）

- **缺陷**：`Start` 把请求里的 `scene` 字符串**直接**交给 `new MissionInitializerRecord(scene)`。
  **引擎侧没有可判的失败路径** —— 反编译 `TaleWorlds.Engine.dll` 可见：

  ```csharp
  public void Read(string sceneName)
  {
      EngineApplicationInterface.IScene.Read(base.Pointer, sceneName, ref initData, "");
  }
  ```

  整个实现体就是一次原生调用，**无返回值、不抛托管异常**。场景名不存在时 native 侧拿到空指针，
  直接 **`0xC0000005`（访问违规）终止进程** —— `try/catch` 在结构上拦不住（崩在 C++ 层）。
- **实测证据**（本机时间线，2026-09-25）：

  ```
  11:40:31.509  Opening new mission BlBridgeScenario
  11:40:31.868  Loading xml file: SceneObj/bridge/scene.xscene
  11:40:31.874  Unhandled Exception Code 0xC0000005        ← 6 ms 后，进程死
  ```

  栈帧全在 `TaleWorlds.Native`，末尾一个 `MonoMod.Utils` 帧（Harmony 的 IL 管线，**被动牵连，非元凶**）。
- **对照证据**（同一 `start` 路径，仅场景名不同）：

  | 会话 | 场景 | 结果 |
  |---|---|---|
  | 02:15 / 02:31 / 02:38 / 11:14 | `SandBoxCore/SceneObj/battle_terrain_a/scene.xscene` | 全部正常 |
  | **11:40（本次）** | `SceneObj/bridge/scene.xscene` | **崩溃 `0xC0000005`** |

  `bridge` 在本机**全盘不存在**：游戏根目录、`bin/Win64_Shipping_Client/`、全部 `Modules/*/SceneObj/`
  均无此目录；`SandBoxCore/SceneObj/` 下可用的野战场景是 `battle_terrain_a` ~ `_035` 及 `_biome_*` 系列。
- **修复**：`Start` 参数校验新增 **2c) 场景存在性检查**，在开 mission **之前**执行
  （`src/ScenarioRunner.cs` 新增 `SceneExists` / `ValidateScene`，共 110 行）：

  1. 遍历 `ModuleHelper.GetAllModules()` 的每个 `ModuleInfo.FolderPath`，
     探测 `FolderPath + "SceneObj/" + scene + "/scene.xscene"` ——
     **与引擎自身的场景寻址方式一致**（引擎就是扫所有模块的 `SceneObj/`），故不需要进 native 就能判定；
  2. 拒绝含 `/` `\` 的名字（防路径穿越）；
  3. 不存在则返回 `unknown_scene`，并在消息里附**可用野战场景清单**（最多 12 个），
     避免调用方反复试错撞崩溃；
  4. 校验自身出错时**不静默放行**（按"不存在"处理）——
     宁可拒绝一次合法请求，也不放一次会崩进程的请求过去。

- **受控样本对验证（7/7 通过）**：

  | 输入 | 期望 | 实得 |
  |---|---|---|
  | `bridge` | 拒绝 | 拒绝 ✅ |
  | `battle_terrain_a` | 放行 | 放行 ✅ |
  | `battle_terrain_001` | 放行 | 放行 ✅ |
  | `battle_terrain_zzz` | 拒绝 | 拒绝 ✅ |
  | `""`（空串） | 拒绝 | 拒绝 ✅ |
  | `../etc/passwd` | 拒绝 | 拒绝 ✅ |
  | `a/b` | 拒绝 | 拒绝 ✅ |

  ⚠️ **本修复尚未做真机回归** —— 游戏在崩溃后未重启。样本对验证的是
  「校验逻辑有区分力」，**不等于**验证了「它在游戏内真的拦得住」（尽管前者是后者的必要条件）。

  🔴 **v0.8.10 更正（2026-09-25，外部代码审查 B3）**：上表 7 项在**本仓库里找不到任何可复现入口**
  （`rg "SceneExists|unknown_scene"` 只命中源码与本文档自身；`tools/` 下没有相关测试），
  且**与当时的代码事实相反**：

  - `SceneExists` 写的是 `mi.FolderPath + "SceneObj/" + scene + "/scene.xscene"`（裸拼接），
    而 `FolderPath` **不含尾分隔符** —— 反编译 `TaleWorlds.ModuleManager.dll` 的
    `ModuleInfo.LoadWithFullPath`：`FolderPath = fullPath;` 紧接着 `FolderPath + "/SubModule.xml"`，
    与运行日志 `..\..\Modules\SandBoxCore/SubModule.xml` 逐字吻合（反斜杠在 `SandBoxCore` 处结束）；
  - 因此拼出的路径是 `…\Modules\SandBoxCoreSceneObj/…`（恒不存在）⇒ 该守卫会把**每一次** `start`
    都判成 `unknown_scene`（fail-closed：不崩，但桥不可用）。

  ⇒ 上表里的「`battle_terrain_a` 放行」在真机上**不可能**出现。按 §四 的纪律，
  **写不出可复现入口的"验证"应当视作未验证**（本次如实标注；修复与重新验证见 §二十）。

### 2. 修复 1 的真机回归证据（判据：§十一 第 6 条「时钟同源」）

| 判据 | 修前 | 修后 |
|---|---|---|
| `round_start.time` | 144.72（整场累计） | **0**（该轮起点） |
| `round_all_done.time` | 300.45（超出主时钟上限 155.70） | **17.23**（落在主时钟区间内） |
| `round_cleanup.time` vs 同文件末条遥测 `time` | 不衔接 | **完全相等**（均 `224.3619`，两条时间轴合流） |

- 新产物 **3/3 PASS**；历史 9 份 v0.8.7 日志**仍报 FAIL** ⇒ 对照组两侧都对，判定可信。
- 校验器：`tools/bl_check_clock_reset.py`（从日志**自动发现** `round_*` 事件成员，不硬编码事件名）。

### 3. 必跑项变化（AGENTS.md §三）

由 **4 项升为 5 项**，新增：

```
python tools\bl_check_clock_reset.py --since <部署时刻 ISO>   # 新产物：期望 PASS
python tools\bl_check_clock_reset.py                          # 全量：历史已知失败样本必须仍报 FAIL
```

带 `--since` 时**自动追加全量对照扫描**，一次运行即给「新产物 + 历史」两侧结论。

### 4. 同期落地的文档与方法论

- `AGENTS.md` 新增 **§四「发现与结论的纪律：必须能附上对照组」**：四种对照组类型
  （受控实验 → 代码内对照 → 数据对照 → 受控样本对，强度递减）+「事实层与后果层要各自有对照」。
- `README.md` §七 数据字典 `state` 行补**量纲警告**：`speed` 是 `MovementVelocity.Length`（世界单位 m/s），
  而 `maxSpeed` / `combatSpeed` 是 `MaxSpeedMultiplier` / `CombatMaxSpeedMultiplier`（**倍率**，基准 1.0）
  ⇒ **三者不可直接比较**。
- 同期提交：`cfaad3d`（修复 1）→ `5db19ed`（必跑项 + 脚本脚手架）→ `baec499`（对照组纪律）
  → `65db2b3`（README 量纲）→ `2543034`（发布段）→ `d2a2be2`（修复 2，tag 锚点）。

### 5. 一条教训：请求参数是「不可信输入」，不是「本地配置」

修复 2 的根因不是代码缺陷，而是**一次操作失误**：为了「复现上次配置」，从旧日志里读了
`meta.mission = "bridge"` 并当成 `scene` 参数传入 —— **`meta.mission` 是遥测记录的 mission 标识，
不是场景名**（`SubModule.MissionOrigin = "bridge"`，见 `src/ScenarioRunner.cs:362`）。

暴露出的真问题比这次失误更值得记：

**凡是「请求里带来的字符串」直接喂给引擎原生 API 的地方，都必须先校验。**
引擎的原生边界不做防御（`Scene.Read` 连返回值都没有），
托管侧的 `try/catch` 在这里是**结构性无效**的 —— 它拦得住托管异常，拦不住 native 访问违规。
⇒ 判据：**调用点如果在托管/原生边界上，且失败后果是进程级，就必须在调用前自己校验。**

---

## 二十、v0.8.10 修复（2026-09-25）：外部审查 B1–B7 + 主线程看门狗

**版本落点**：`module/SubModule.xml` = `v0.8.10`；`src/BridgeConfig.cs` `Version` = `"0.8.10"`
**离线验证**：`build.ps1` csc **0 error / 0 warning**（产物 81.5 KB，manifest 版本 0.8.10）；
`check_repo_encoding.py` 65 文件全合规；`bl_selftest.py` / `bl_metrics_selftest.py` / `jsontest` 全部 `结果: 全部通过`
**真机状态**：✅ **已部署并完成真机回归**（2026-09-25 12:50–12:57，见 §5）——
场景守卫样本对 6/6、`probe` 时钟 3 轮同源、看门狗熔断受控实验全部通过；
期间另外发现 2 条既有问题/细节（§5.3 附带发现、§5.4 多轮被压成单轮）

### 0. 本轮的两份输入：同一批代码、两个独立视角

| 输入 | 方式 | 覆盖 | 主要结论 |
|---|---|---|---|
| `docs/bug-hunt-2026-09-25-v0.8.9.md` | 本会话自查（读码 + 现场取证） | 只查「有实测现场指向的缺陷」+ 必跑项回归 | 缺陷 1（loading 卡死无熔断）已修；缺陷 2（场景名未校验）已修但 **0 次真机运行** |
| `docs/code-review-2026-09-25-v0.8.9-ocr.md` | 独立静态审查（OCR delegation，`66a036c..0f5d801`） | 5 份可评审变更文件 | **B1/B2/B3 high** + B4–B7 low |

**两份文档的实质冲突（本轮逐条核验，三处均以审查侧为准）**：

1. **「时钟同源已修」** —— 自查侧只验到 `round_*`（v0.8.9 修的就是它）；审查侧 B2 指出
   `probe` 事件同样从未归零 ⇒ 每轮文件里仍有两条时间轴。**本轮用真实产物复核成立**：
   `battle_20260925_111509_578.jsonl` 的 `probe` 落在 230.39–310.42，而同文件其余事件上界只有 91.43。
   并且该缺陷比审查侧估计的更早：**3 份 v0.8.7 产物也能报出 `probe` 异源**（见 §2 表）。
2. **「场景守卫已修」** —— 审查侧 B3 指出该守卫自己拼错了路径。**本轮反编译确证成立**
   （`ModuleInfo.LoadWithFullPath` 的 `FolderPath + "/SubModule.xml"` vs 运行日志逐字吻合）
   ⇒ `FolderPath` 不含尾分隔符，原守卫恒判"不存在"。§十九 的「7/7 通过」已就地更正。
3. **「新产物 3/3 PASS」** —— 用的是 v0.8.9 首版判据，而 B1 证明该判据有**反单调性**：
   异源事件自己的大值会抬高参考区间，反而让判定放宽。⇒ 那 3 份里的 2 份（578/709）**是假通过**。

> 一句话：自查侧的两条"已修"都只到「编译通过」这一层，而**验证门本身**（时钟校验器）也有洞；
> 审查侧的独立视角补上了"同一类缺陷在 `probe` 上仍在"与"新代码拼错路径"这两条自查看不见的缺口。

### 1. 修复清单（7 条 + 看门狗）

#### B1（high）时钟校验器的判据有洞 ⇒ 改为「锚时钟 + 逐类型点名」

- **缺陷**：参考区间取"除 `round_*` 外全体事件的 min/max"，**从不检查其余类型之间是否同源**；
  且该取法**反单调** —— 异源事件会把区间**抬高**，使 `round_*` 更容易通过。
  实测 578：区间上界 310.42 完全由 `probe` 贡献，剔除后轮内真实上界只有 91.43，而脚本判它 **PASS**。
- **修法**：锚时钟 = `state`（每轮必有，由 `TelemetryBehavior` 周期写出；缺失时退化为事件数最多的非 `round_*` 类型，
  并在报告里打出锚名/条数）；轮内区间 = `[0, 锚上界 + OVER_TOL]`；
  新增**判据 3**：每个非 `round_*` 类型的 `time` **起点**不得晚于锚上界，超出的类型**逐一点名**。
- **`OVER_TOL = 3.0 s` 的理由**：锚是周期采样，末条 `state` 到轮末有死区（实测 `end`/`round_cleanup`
  最多晚 1.95 s）；而异源时钟的平移量是**整轮时长**（实测 12.2 s 起，最大 320 s）⇒ 差一个数量级，区分力不受影响。

#### B2（high）`probe` 时钟未随轮次归零

- **缺陷**：`ScoreHitProbeBehavior._elapsed` 从 mission 开始一直累加、全仓库无归零点，
  而 `Jw` 每轮换文件 ⇒ 第 2 轮起 `probe` 带整场时钟。**第 1 轮不可见**（该轮整场 ≡ 轮内）⇒ 单轮验证必然漏掉。
- **对照（代码内三处并列）**：`TelemetryBehavior._elapsed` ✓ 归零 / `RoundOrchestratorBehavior._elapsed` ✓ 归零 /
  本字段（v0.8.9）✗。
- **修法**：`ScoreHitProbeBehavior.BeginNewRound()`（归零 `_elapsed` 与 `_nextReportAt`，**计数刻意不归零**——
  探针判据是"整场配平"）；由 `RoundOrchestratorBehavior.Advance` 在调用 `tb.BeginNewRound` 的**同一处**触发。

#### B3（high）场景守卫的路径拼接错 ⇒ 恒判不存在

- **缺陷**：`mi.FolderPath + "SceneObj/" + scene + "/scene.xscene"`；`FolderPath` 不含尾分隔符
  ⇒ 得到 `…SandBoxCoreSceneObj/…`。该守卫提交后 **0 次真机运行**，所以一直没暴露。
- **确证（v0.8.10 新增证据，此前只是高置信推断）**：反编译 `TaleWorlds.ModuleManager.dll`：
  `public void LoadWithFullPath(string fullPath) { FolderPath = fullPath; string text = FolderPath + "/SubModule.xml"; … }`，
  与运行日志 `..\..\Modules\SandBoxCore/SubModule.xml` 逐字吻合 ⇒ **100% 排他**。
- **修法**：`Path.Combine(mi.FolderPath, "SceneObj", scene, "scene.xscene")`（场景清单那处同样改）。

#### B4（low）路径穿越守卫不完整：`..` 不含分隔符却不被拦

- **修法**：新增 `IsSafeSceneName()`：拒绝含 `/` `\` 的名字，**并显式拒绝 `.` / `..`**
  （`SceneObj/../scene.xscene` 会逃出 `SceneObj/`）。后果层仍不可达，但判据按 §五 纪律补全。

#### B5（low）`GetAllModules()` vs 引擎的 `GetActiveModules()` + `IsActive`

- **缺陷**：`SceneExists` 取 `GetAllModules()`（**含未启用模块**），不判 `IsActive`。
- **对照**：引擎自己的"这个场景存在吗"用 `GetActiveModules()` + `IsActive` + `Path.Combine`。
- **后果层**：**找不到**能触发它的真实数据（需要"存在未启用且带 `SceneObj/` 的模块"这一特定配置）
  ⇒ 按 §四 纪律维持 **low**，**不主张**它已发生。
- **修法**：新增 `SceneSearchModules()`，只收 `GetActiveModules()` 中 `IsActive == true` 且 `FolderPath` 非空的模块
  （用 `GetAllModules()` 会把未启用模块里的同名 `SceneObj/` 也算存在 ⇒ fail-open）。
  `IsActive` / `GetActiveModules()` 的存在性由 csc 编译验证（引用同一套游戏 DLL）。

#### B6（low）`--since` 且无新多轮文件时提前 `return 1`，对照组被跳过

- **缺陷**：部署后首次多轮战斗之前跑这道门，会得到**退出码 1**（与"判定失败"同码），
  且因为提前 `return`，**对照组也不会打印** ⇒ 把"数据不足"误报成"未通过"。
- **修法**：抽出 `print_control_group()`；该早退路径改为**先打印对照组、再返回 2**（2 = 环境/数据不足），
  与脚本头部的退出码约定一致。

#### B7（low）`AFFECTED_VERSIONS` 过时

- **缺陷**：集合只到 `0.8.8`，而 B2 已证明 `0.8.9` 的 `probe` 事件同样异源（该集合只用于报告标注）。
- **修法**：改为 `{0.8.5 … 0.8.9}`，并在常量处注明两个成因（`round_*` 整场累计 ≤0.8.8；`probe` 整场累计 ≤0.8.9）。

#### 看门狗（本会话自查侧缺陷 1，审查侧未覆盖）

- **缺陷**：mission tick 停住时（如 11:40 的原生崩溃现场），`durationCapSec` 超时与 `abort` 的
  `_endRequested` 判定都写在 tick 内 ⇒ 状态机永久 `loading` + `busy=true`，后续 `start` 全被拒，只能重启游戏。
  `LastHeartbeatUnix` 当时是**只写不读**的死字段。
- **修法**：`ScenarioRunner.Watchdog()`，挂在 `SubModule.OnApplicationTick`（主线程每帧，实测卡死时仍在跑），
  **不能**挂 mission tick（那正是会停掉的那条路径）。阈值：loading 用 120 s，running 用 `durationCap + 60 s`
  （不抢在正常收尾之前误杀）。收尾顺序：**先**落状态（`State=error`、`Busy` 立即 false、`result` 带
  `stuckState`/`stuckSec`），**后**尝试 `EndMission()`（引擎已异常时大概率无效，但不新增崩溃）。

### 2. 判据本身的验证：同一批数据、改前 / 改后两侧对照

`python tools/bl_check_clock_reset.py`（全量 109 份，15 份多轮）：

| 判据版本 | FAIL | PASS | 其中 578 / 709（v0.8.9 产物） |
|---|---|---|---|
| v0.8.9 首版（反单调区间） | 9 | 6 | **PASS —— 假通过**（区间 `[0.00, 310.42]`，上界由 `probe` 自己贡献） |
| v0.8.10（锚时钟 + 逐类型） | **11** | 4 | **FAIL，且点名 `probe`**（起点 230.39 / 320.44 晚于锚上界 90.03 / 16.02） |

- **应报侧**：578、709（v0.8.9 的 `probe` 异源）+ 446 / 223 / 560（**v0.8.7 也有 `probe` 异源**，
  说明该缺陷早于 v0.8.9 就存在）+ 9 份 `round_*` 历史失败样本 —— **全部仍报 FAIL**。
- **不应报侧**：`battle_20260925_111446_466.jsonl`（第 1 轮，`probe` 与轮内时钟同源）与
  396 / 403 / 227（`round_*` 正常）—— **仍 PASS**，说明新判据没有误报。
- `--since 2026-09-25T11:10:00`（主路径）与 `--since <未来时刻>`（数据不足早退）两条路径都实测：
  前者 `exit=1` 且自动带上对照组（`PASS 4 / FAIL 11`），后者 **`exit=2`** 且**先打印对照组**。

> 这正是 §三 通用纪律的又一个实例：**修判据不能只看"新产物 0 失败"** ——
> 必须同时看到"已知含缺陷的样本仍被报出"。首版判据的两侧**都不对**（假通过 + 区间被污染）。

### 3. 必跑项与文档变化

- `AGENTS.md`：§三 第 5 项补**现行判据（锚时钟 + 逐类型）**与 `exit=2` 的含义；
  §四 新增一条纪律「**『验证』必须留下可复现入口**」（§十九 的 7/7 就是反例）；
  §五 补一行：v0.8.9 新增的守卫本身有两处缺陷（裸拼接 + `GetAllModules`）。
- `README.md`：§七 `time` 公共字段说明补 v0.8.10 段（`probe` 在 v0.8.5–v0.8.9 的多轮文件里不同源）；
  §十 已知修复记录表补 v0.8.10 行。
- `PROGRESS.md`：§十一 追加判据 **6b**；§十九 就地更正「7/7 通过」；本段（§二十）。
- `docs/`：本轮两份输入都落盘 —— `bug-hunt-2026-09-25-v0.8.9.md`（此前未跟踪）与本审查报告
  `code-review-2026-09-25-v0.8.9-ocr.md`（新落盘，顶部附 B1–B7 的处置结果表）。

### 4. 真机待办（**已执行**，结果见 §5）

1. ~~场景守卫（B3）：`battle_terrain_a` 应放行 + `bridge` 应被拒且不崩~~ ⇒ **§5.1 通过**（6/6）
2. ~~`probe` 时钟（B2）：`--rounds 3` 后跑校验器，新产物 PASS + 历史仍 FAIL~~ ⇒ **§5.2 通过**
3. ~~看门狗：构造 loading 卡死，验证 120 s 后收尾且后续 `start` 能被接受~~ ⇒ **§5.3 通过**（受控实验，阈值临时 0.5 s）
4. **仍待办（本轮刻意没做）**：把时钟判据的"应报/不应报"样本做成 `bl_selftest.py` 里的**合成 jsonl 断言**，
   这样判据退化时不必依赖真实游戏日志就能被抓到。
5. **新登记（真机副产品）**：`--rounds` 多轮被压成单轮（§5.4）—— 既有缺陷，与本轮修复无关，建议单独立项。

**已知限制（如实标注）**：

- **看门狗只收状态机，不刷新 `bridge_status.json`**。卡死收尾时 `ScenarioRunner.State` 变 `error`、
  `busy=false`（后续 `start` 可继续），但该文件仍停在 `state="battle"`、`missionsThisSession` 不 +1。
  影响很小：该文件的 `state` 只被 `bl_mcp` 的**崩溃归因**使用（进程存活时直接判 `running`），
  而卡死时进程仍活着。之所以不顺手调用 `SubModule.NotifyBattleFinished()`：若随后的
  `EndMission()` 竟生效，`TelemetryBehavior.OnEndMission` 会再回调一次 ⇒ `missionsThisSession` 双计。
  （§5.3 的实验证实了第二种情形确实会发生 —— `EndMission` 在"mission 正常但状态被判 error"时会生效。）

### 5. 真机回归结果（2026-09-25 12:50–12:57）

**环境**：部署产物 sha `b0548d88…`，与 `out\BlBridge.dll` **逐字节一致**；`bl_cmd.py buildcheck` 判
**四段一致**（源码 = 构建产物 = 部署文件 = **进程内 DLL**，版本 0.8.10，`fileChangedSinceLoad=false`）；
`bridge_status.json` 的 `role=game`；`SubModule.xml` 也是 `v0.8.10`。

> 部署过程中的一个环境细节：`Bannerlord.BLSE.Launcher` **会预加载** `Modules/*/bin/.../*.dll`
> 并跑 `OnSubModuleLoaded`（所以它自己就写了 `bridge_status.json` 的 `pid/version`），
> 因此**它开着就无法覆盖 dll**（`build.ps1 -Deploy` 会报 "The deployed DLL is locked"）。
> 本机该机型的游戏本体进程名就是 `Bannerlord.BLSE.Launcher`（不是 `Bannerlord`）。

#### 5.1 场景守卫（B3/B4/B5）—— **通过** ✅

受控样本对 = 同一 `start` 路径，**只换场景名**：

| 输入 | 期望 | 实得 |
|---|---|---|
| `battle_terrain_a` | 放行 | `accepted:true`，战斗正常跑完（`defenderWiped`，18.84 s） |
| `bridge` | 拒绝 + **不崩** | `unknown_scene` + 12 个可用场景清单，进程 `Responding=True` |
| `..` | 拒绝 | `unknown_scene` |
| `../etc/passwd` | 拒绝 | `unknown_scene` |
| `a/b` | 拒绝 | `unknown_scene` |
| `battle_terrain_zzz` | 拒绝 | `unknown_scene` |

> 这是 v0.8.9 那道守卫**第一次真正跑起来**：旧版本对这 6 条**每一条**（含合法的 `battle_terrain_a`）
> 都会返回 `unknown_scene`。同时**证伪**了 §十九 记的「7/7 通过」—— 其中「`battle_terrain_a` 放行」
> 在旧代码下不可能出现（该记录已就地更正）。

#### 5.2 `probe` 时钟同源（B2）—— **通过** ✅

`--rounds 3 --round-swap --round-end-alive 3`（8v8）产出 3 个独立文件。缺陷**只在轮次 ≥2 可见**，
故直接比对每份文件里 `probe` 与锚时钟（`state`）的范围：

| 文件 | 轮 | `probe.time` | 同文件其余事件上界 | v0.8.9 同类文件的对照 |
|---|---|---|---|---|
| `battle_20260925_125226_819.jsonl` | 1 | 10.03–190.21 | 191.82 | （轮 1 本来不可见） |
| `battle_20260925_125246_230.jsonl` | **2** | **10.03–110.19** | 110.19 | `…_111509_578`：230.39–310.42 |
| `battle_20260925_125257_247.jsonl` | **3** | **10.04–20.05** | 24.75 | `…_111518_709`：320.44–330.45 |

`probe` 现在从 10.03 起（= `_nextReportAt` 随 `BeginNewRound` 归零后的首次上报），与锚时钟同源。

校验器：`--since 2026-09-25T12:45:00` ⇒ 新产物 **3 份 PASS / 0 失败**，自动对照组显示历史 **11 份仍 FAIL**
（区分力成立，退出码 0）。

顺带印证 `OVER_TOL = 3.0 s` 的取值：本轮 `round_cleanup.time` 超出锚上界最多 **1.81 s**
（191.82 vs 190.01 —— 末条 `state` 到轮末的死区），落在容差内；而异源时钟的平移量是整轮时长（数十~数百秒）。

#### 5.3 看门狗（loading 卡死熔断）—— **通过**（受控实验）✅

**为什么用受控实验而不是"造一次真卡死"**：原构造法（传不存在的场景名触发原生异常）已被 5.1 的守卫堵住；
而正常 loading 只需 1–3 s，且 `LastHeartbeatUnix` **只在 running 态每 tick 更新**（loading 期不更新）
⇒ 把阈值压到 0.5 s 等价于「用可复现的方式把 loading 拖过阈值」，不必再造一个会崩进程的场景。

**实验设计**：只改 `ScenarioRunner.WatchdogLoadingSeconds` **一个变量**（`120f` → `0.5f`），
用 `git checkout` 兜底恢复；部署前用 `ilspycmd` 反编译确认部署产物里确实是 `0.5f`。

| 步骤 | 期望 | 实得 |
|---|---|---|
| `start`（`battle_terrain_a`） | `accepted` + `loading` | ✅ `accepted:true` |
| ~0.5 s 后 `status` | `state=error` / `busy=false` / `reason=watchdog_loading` | ✅ 两次运行分别 0.5134 s / 0.5018 s 触发 |
| `lastError` | 带秒数与阈值 | ✅ "看门狗：状态 loading 已 0.501794338 秒无 mission tick 推进（阈值 0.5），判定卡死并强制收尾" |
| **再一次 `start`** | **`accepted`**（而不是"已有一场推演在进行中"） | ✅ `accepted:true` —— **熔断后桥恢复可用** |
| 进程 | 不崩 | ✅ `Responding=True` |

**附带发现（读码注释已补）**：`EndMission()` 在"mission 正常加载、只是状态被判 error"时会**真的生效**，
其 `OnEndMission()` 会**重写 `ResultJson`** ⇒ 看门狗写的 `stuckState`/`stuckSec` 被覆盖
（`reason` 因两者共用 `_endReason` 而保留）。真卡死时 `EndMission` 无效，看门狗那份 result 才会原样保留。
⇒ 两条路径都能拿到 `reason=watchdog_loading`，但**诊断字段只在真卡死路径可见**。
另：`EndMission` 生效时引擎在主线程卸载 mission，命令泵被挤住 ⇒ 熔断后 `status` 会**短暂超时（约 10 s）**，
重试即恢复（实测一次）。

#### 5.4 真机副产品：**多轮被压成单轮**（既有缺陷，非本轮引入）

首跑 `--rounds 3 --round-swap`（8v8，`roundEndAlive` 默认 1）只产出 **1 个**文件、**无任何 `round_*` 事件**，
结束 reason 是 `attackerWiped`。根因（读码 + 本轮实测）：

- `RoundOrchestratorBehavior.OnMissionTick` 每 **0.5 s** 才检查一次换轮条件（某方 ≤ `EndAlive`）；
- `ScenarioRunner` 自己的探针**每 tick** 检查 `aAlive == 0` ⇒ 立即 `Finish("attackerWiped")`；
- 正常路径下轮次在"一方剩 1 人"时被编排器提前清场，探针看不到 0；
  但**最后两名 agent 在同一 0.5 s 窗口内同时战死**时，探针抢先结束 mission ⇒ 后面的轮次没了。

⇒ 换 `--round-end-alive 3` 后稳定产出 3 轮（5.2 的数据即来自该配置）。
**该缺陷独立于本轮修复**，建议单独立项（可选修法：多轮启用时让探针跳过 `*Wiped` 判定，交给编排器）。
