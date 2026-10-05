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

> **交付产物的 sha 变化说明（如实标注）**：§5.1–§5.4 跑的是 `b0548d88…`；回归之后，为把 §5.3 的实验发现
> 写进 `Watchdog()` 的注释，源码重新编译并部署，部署产物变为 **`B03E353D…`**（`out\` 与部署目录逐字节一致）。
> **与回归产物的唯一差异是注释**（不参与行为），并已用 `ilspycmd` 反编译确认新产物里
> `WatchdogLoadingSeconds = 120f`（正式版、非实验版）。⇒ 真机证据对应当前交付物的**行为**，
> 但"验证过的二进制"与"最终部署的二进制"不是同一份（差一段注释）—— 按 §四 的纪律如实标注。

> **上述 sha 差异已闭环（13:02–13:04，同一组确认在最终产物上重跑）**：
> `buildcheck` 四段一致（`loadedSha256=b03e353dcf9bc0d9` = 部署 = 构建，`fileChangedSinceLoad=false`）；
> 场景样本对 `battle_terrain_a` 放行（6v6 打完）+ `bridge` ⇒ `unknown_scene` 且进程不崩；
> `--rounds 3 --round-swap --round-end-alive 3` 产出 3 份文件，校验器新产物 **3 份 PASS**、历史 **11 份仍 FAIL**。
> ⇒ **交付产物 `B03E353D…` 自身也已在真机上验证过**，不是只验了前一份二进制。
> （本轮 `round_cleanup` 超出锚上界 0.26 / 1.95 / 0.90 s —— 1.95 s 与设计 `OVER_TOL` 时按实测估的上限一致。）

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

---

## 二十一、功能回归测试（2026-09-25，v0.8.10）

**范围**：离线工具链（14 个）+ MCP 16 个工具 + 游戏端命令/参数面。
**真机战斗规模按用户指定：100 v 100**（此前各轮多用 8v8 / 20v20）。

### 1. 离线层（不需要游戏）—— 全部通过 ✅

| 项 | 命令 | 结果 |
|---|---|---|
| 编码体检 | `check_repo_encoding.py` | ✅ 67 个跟踪文件全合规（UTF-8 无 BOM + LF） |
| 离线自测 | `bl_selftest.py` | ✅ `结果: 全部通过`，exit 0 |
| 指标自测 | `bl_metrics_selftest.py` | ✅ 同上 |
| C# 单测 | `tools/jsontest/build_and_run.ps1` | ✅ 同上 |
| 时钟同源 | `bl_check_clock_reset.py` | ✅ 三条路径都对：全量 `exit=1`（含 11 份历史失败样本）/ `--since` `exit=1` + 对照组 / 数据不足 `exit=2` + 对照组 |
| 分析器 | `bl_analyze.py` / `bl_metrics.py` | ✅ 8v8 与 **100v100**（5.9 MB）产物均正常：200 入场 / 4179 命中 / 164 阵亡 / 332 s |
| 伤害分析 | `bl_dummy_analyze.py`（`auto`/`range`/`--compare`） | ✅ 三种口径都出数；`range` 口径在 100v100 靶场上 2388 样本 |
| A/B 对比 | `bl_compare.py`（`--a/--b`、`--manifest`） | ✅ 出报告（示例 manifest 里的旧文件缺失时提示 `!!` 后继续） |
| 阵亡画像 | `bl_death_compare.py`（多文件） | ✅ 两口径都报，样本不足时拒绝给数（显示 `-`） |
| 兵种索引 | `bl_sage.py --status/--check/--search/--culture/--limit` | ✅ 索引可用（1981 兵种），`OK/MISS` 与退出码区分正确；`--search` 是 **SQL LIKE（需 `%` 通配）**、`--culture` **必须配合 `--search`**（help 已写明，误用会打印 usage） |
| 状态转储 | `status_dump.py` | ✅ |
| 一次性探针 | `hit_semantics_probe.py` / `dump_agent_hits.py` | ✅ 结论仍是 `dmg-absorbed` ≡ `hpAfter` 差分（吻合 98.1%） |
| 扫描工具 | `bl_troop_sweep probe --dry-run` / `cover --help` | ✅ 分包计划正确（4 id → 2 包 + 引信）；需自备 `--ids-file`（见 F4） |
| 跑批 dry-run | `bl_batch.py --dry-run` | ✅ 计划展开为逐场 `bl_cmd start/wait` |
| MCP 注册 | `register_mcp.py --show` | ✅ **修复后**（见 F3） |

### 2. MCP 层（真机，16 个工具）

- **查询类 11/11 通过**：`bl_status` / `bl_battle_status` / `bl_build_check` / `bl_config` / `bl_read_config` /
  `bl_lookup_troop`（found+missing 双路径）/ `bl_list_battles` / `bl_analyze` / `bl_read_events` /
  `bl_fast_forward`（开 + 关）
- **批处理/配置类**：`bl_run_batch`（dryRun ✅）、`bl_batch_report`（✅ 对过期示例数据优雅降级）、
  `bl_apply_config`（✅ `changed`/`missing`/`dryRun` 三路径；值不做校验见 F5）
- **真机动作类**：`bl_start_battle` **100v100** ✅、`bl_wait_for_state` ✅、`bl_abort` ✅

### 3. 真机层（100 v 100）

| 项 | 结果 |
|---|---|
| 100v100 基础场（`orders=charge`） | ✅ `130823_240`：200 agent、4179 命中、164 阵亡、332 s（游戏内）、5.9 MB；结束后 `aInitial/dInitial` 正常 |
| 100v100 靶场（`dummySide=defender` + `freezeDummies` + `unlimitedAmmo` + `dummyArmor` + `cap=60`） | ✅ `130859_686`：4529 命中、靶子侧 100 存活 |
| 场景守卫样本对 | ✅ 6/6（§5.1） |
| 多轮 8v8（`--rounds 3 --round-swap --round-end-alive 3`） | ✅ 3 文件时钟同源（§5.2） |
| 看门狗（正式阈值 120 s）**真实触发** | ✅ 见 F1：`abort` 在 loading 态 ⇒ 120.0036 s 后收尾为 `error` |
| `abort`（loading 态） | ⚠️ 会让 mission 卡住 ⇒ 触发 F1/F2 |
| 多兵种组 / 参数组合场 / 多轮 100v100 / 跑批+compare / `troop_sweep` / 错误码 | ✅ 全部通过 —— 明细见 §7 |

### 4. 发现的缺陷与缺口（按严重度）

**F1（medium-high）看门狗的"恢复可用"在真实卡死场景下只完成一半**
- 事实层：`abort` 在 loading 态后 mission tick 停住（`ticks=7351` 不再增长、`missionTime=0`、`verdict=stalled`），
  120 s 后看门狗把状态机收成 `error`/`busy=false` ✅（§5.3 的机制生效）
- 后果层：**引擎的 GameState 栈没恢复** —— `start` 一直被拒：`wrong_state（当前状态: MissionState）`，
  且 `Mission.Current != null`（残留）。⇒ §5.3 的"熔断后 start 仍 accepted"**只在 `EndMission()` 生效时成立**
  （那次实验是"mission 正常加载"），真实卡死时用户仍需手动退出/重启游戏。
- 连带（本轮已修文案）：原 `wrong_state` 文案让用户"点 Custom Battle"，而界面已在 mission 里 —— 照做也进不去。
  新增 `StuckMissionHint()`：只在"停在 MissionState + 引擎还有 mission + 本状态机不 Busy"时给出
  "按 ESC 退出该战斗 / 退回主菜单；无响应则重启游戏"。

**F2（medium）`abort` 在 loading 态会让 mission 卡死**
- 事实层：`Abort()` 无条件 `m.EndMission()`，而 loading 中的 mission 调 EndMission 不会正常收尾
  （`ScenarioProbe.OnMissionTick` 只在 running 态消费 `_endRequested`）⇒ mission tick 停。
- 后果层：**本轮实测**（100v100 第 3 场，13:09）：无日志文件产出、`MissionState` 残留、需看门狗兜底。
- 建议：`Abort` 在 loading 态改为"只置 `_endRequested=true`，等 running 或等看门狗收尾"，不要直接 EndMission。

**F3（low，已修）`register_mcp.py` 从未能运行**
- `NameError: name 'HERE' is not defined` —— 第 17 行用 `HERE`，而它到第 22 行才定义 ⇒ 该工具**任何调用都崩**。
- 修：把 `HERE = ...` 提到 `sys.path.insert` 之前；`--show` 已实测通过。

**F4（low）`bl_troop_sweep` 的默认 `--ids-file` 指向不入库路径**
- `DEFAULT_IDS = .sdd/2026-09-24-multitroop-tactics-plan/sweep_expected_ids.json`，而 `.sdd/` 明确不入库
  （PROGRESS §十七 已归档清理）⇒ 不带 `--ids-file` 必然失败；`cover` 的 `--good-file` 同理。

**F5（low）`bl_apply_config` 的值不做类型/范围校验**
- 实测 `value="abc"` 被当作合法新值接受（`dryRun` 无害；真写靠备份兜底）。
- 另外：传错 `edits` 形状时抛的是裸 `TypeError("string indices must be integers")`，不告诉调用方正确格式
  （应为 `[{"path": "...", "value": "..."}]`）。

**F6（low）`bl_analyze` / `bl_compare` 在多轮**中间轮**文件上读不到时长与攻守人数**
- 它们只从 `end` 事件取 `duration_sec` / `aInitial` / `dInitial`，而多轮模式下只有**最后一轮**写 `end`
  （中间轮写 `round_cleanup`）⇒ 中间轮显示 `时长 -`、`攻方 None→None`。不是数据缺失，是取值口径单一。

**F7（low）MCP 的 `bl_start_battle` 参数面窄于 CLI**
- 缺少 `rounds` / `roundSwap` / `roundEndAlive` / `roundSpawnAttacker|Defender` / `randomSeed` /
  `allowAnyState` / `attackerGroups` / `defenderGroups` ⇒ **多轮与多兵种组走不了 MCP**，只能走 `bl_cmd.py`。

**F8（low）靶场交叉校验的 `byHp` 量纲错**
- `DummyRangeBehavior.OnScoreHit`：`byHp = HealthLimit - Health`，而 `HealthLimit = hpMax + 9999`
  （防死用）⇒ 它算的是"距满血还差多少"，**不是本次伤害**；只有"每击后立刻回满"时才相等。
- 数据对照（100v100 靶场 `130859_686`）：7/2388 不一致，逐条满足 `byHp = 上一击残留 + 本次 applied`
  （如 `applied=20, byHp=37`；`27 → 58`）。
- ⇒ `_mismatchCount`（游戏端自报）与 `bl_dummy_analyze` 那句"**应为 0**"是**口径误报**，不是数据缺陷。
- 建议修法：只在 `hp >= limit`（已回满）时计入校验，或让 `byHp` 改为"上一次该 agent 的 hp − 本次 hp"。

**F9（low）`bl_cmd start` 在组模式下仍强制要求 `--attacker/--defender`**
- 事实层：argparse 里二者是 `required`，而 help 文本写的是"给了 `--attacker-groups` 则 `--attacker/--a` **被忽略**"
  ⇒ 省略它们会直接 argparse 报错，请求根本没发出去。
- 后果层：本轮实测踩到 —— `start --attacker-groups ... --defender-groups ...` 被 argparse 拒绝，
  而我的输出过滤只抓 `"accepted"/"code"` 之类的 JSON 字段 ⇒ 没看见错误，`wait` 白等了 3 分钟。
- 建议：help 文本改为"仍须提供（组模式下仅用于回显，实际兵力由 groups 决定）"，或让二者在组模式下变为可选。

### 5. 本轮已修

| # | 修法 | 产物 / 验证 |
|---|---|---|
| F3 | `HERE` 定义位置（Python，不影响 dll） | `tools/register_mcp.py`；`--show` 实测通过 |
| F1 文案 | 新增 `StuckMissionHint()` 并接进 `wrong_state` 分支 | dll `21903D82…` |
| F2 / F5 / F6 / F7 / F8 / F4 / F9 / O2 | 修法见 §8；**真机复测见 §9（三项全过）** | dll `41D5BE0D…`（`out\` 与部署逐字节一致） |

### 6. 操作坑（本轮新增）

- **`ilspycmd` 反编译部署目录的 dll 会让它无法被覆盖**：实测 `Copy-Item` 报
  `The requested operation cannot be performed on a file with a user-mapped section open`。
  ⇒ 验证产物请反编译 **`out\BlBridge.dll`**，不要碰 `Modules\BlBridge\...\BlBridge.dll`；
  若已经踩上，等一会儿重试即可（`build.ps1` 会按"进程在跑但 dll 未锁"放行，输出 `deploying anyway`）。
- **`Bannerlord.BLSE.Launcher` 在启动早期就会 mmap `Modules/*/*.dll`**（此时 `File.Open` 独占探测
  可能仍显示"可写"，但 `Copy-Item` 会失败）⇒ 部署时机要抢在启动器早期，或干脆先关掉它。

### 7. 真机执行结果（本节原为"待做"清单，2026-09-25 13:18–13:26 全部跑完）

| 项 | 命令要点 | 结果 |
|---|---|---|
| 多兵种组 100v100 | `--attacker-groups 'imperial_legionary:60:Infantry:charge｜khuzait_khans_guard:40:HorseArcher:charge'` + 守方两组 `stop` | ✅ `accepted`，组求和 100+100，正常打完（50 s） |
| 参数组合场 100v100 | `--orders default --player-side defender --random-seed 12345 --round-spawn-attacker/-defender` | ✅ accepted；`meta.randomSeed=12345` 已落盘验证（未指定的场次为 `-1`） |
| 加速通道 | `fastforward --on` → `speed`（战斗中）→ `--off` | ✅ 战斗内 `isFastForward=true`、`sceneTimeSpeed=1`、`appliedFrames=100`；关闭后 `forced=false` |
| 多轮 100v100 | `--rounds 3 --round-swap --round-end-alive 3` | ✅ 产出 3 文件（6.1 / 2.4 / 1.6 MB）、每轮 `unit=200`；时钟校验 **3/3 PASS**；`probe` 与锚同源（轮 1 `[10.04, 310.47]` vs 锚 310.02；轮 2 `[10.03, 170.23]` vs 176.04） |
| 跑批 + 对比 | `bl_batch --plan <100v100 换边双跑>` → `bl_compare --manifest` / `bl_cmd compare --manifest` | ✅ 两场 100v100（A 0:46 / B 0:53）、`runs.json` 产出、报告含主指标 / 全程对照 / 胜负分布 / validity |
| `troop_sweep probe`（真机） | 4 id（3 真 1 假）/ `--chunk 2` | ✅ 好 3 / 坏 1，与清单逐条一致（每包 ~27 s，含引信，**未建 mission**） |
| `troop_sweep cover`（真机） | `--good-file <probe 产出>` | ✅ 期望 3 / 实际 3：缺 0、多 0（46.9 s） |
| 错误码（协议层，绕开 CLI 校验） | 直接 `send_command("start_battle", ...)` | ✅ `bad_groups`（formation 非法；`hold` 已移除并提示改用 `stop`）、`unknown_troop`、`unknown_scene`（附 12 个可用场景） |
| 错误码（CLI 前置校验） | `bl_cmd start` 直传坏 id / 坏 DSL | ✅ 被客户端拦下并给指引（`--skip-troop-check` 可绕过，绕过时游戏端仍正确报 `unknown_troop`） |

**过程中新增两条观察**：

- **O1（low）** `cover` 等"回 idle"在 100v100 下会超时（打印"10 秒内未回 idle —— 继续下一场"）。
  与 F1 同源：`State=ended` 立即可见，但 `bridge_status.json` 回到 `idle` 要等 TelemetryBehavior 的结束回调
  走完，200 agent 规模下 >10 s。
- **O2（low）协议 method 名是 `start_battle`**（不是 `start`）—— raw 调用方容易猜错（本轮实测踩到
  `unknown_method: 未知方法: start`）。建议在 README 协议章节列一张 method 表。

### 8. 修复记录（用户裁定"全部修"）

| # | 实际落地的修法 | 验证状态 |
|---|---|---|
| F2 | `ScenarioRunner.Abort()`：`State == RunStateLoading` 时**只置** `_endReason` / `_endRequested`，**不调** `EndMission()`；running 态行为不变（由 tick 消费标志后 `Finish("aborted")`） | 编译 ✓；真机见 §9 |
| F8 | `DummyRangeBehavior.OnScoreHit`：用 `hpBefore = hp + applied` 反推"本次命中**前**是否满血"，满血才算差分值，否则写 `-1`（不可用）并跳过校验；`bl_dummy_analyze` 只统计可用样本，并把跳过的条数一并报出 | 编译 ✓；真机见 §9 |
| F7 | `bl_mcp.py` 的 `bl_start_battle` 补 9 个参数（`allowAnyState` / `rounds` / `roundEndAlive` / `roundSwap` / `roundSpawnAttacker` / `roundSpawnDefender` / `randomSeed` / `attackerGroups` / `defenderGroups`），组 DSL 本地先校验；兵种校验改为**组感知**（组模式下查组内兵种，而非被忽略的单值） | `bl_selftest` ✓（16 工具断言不变）；真机见 §9 |
| F5 | `apply_config`：入参必须是 `[{"path": …, "value": …}]`（形状错时给正确格式）；拒绝空值/布尔值/含 XML 特殊字符的值；新增 `warnings` 数值启发式（旧值是数字、新值不是 ⇒ 提醒，不拒绝） | 编译/自测 ✓；离线复测见 §9 |
| F6 | `bl_common.end_metrics()`：缺 `end` 时用最后一个 `round_*` 取时长、`unit` 按 `side` 取人数；`bl_analyze` 与 `bl_compare` 均已接入 | 离线复测见 §9 |
| F4 | `_load_json_list` 报错补提示：默认路径指向**不入库**的 `.sdd/…`，请显式 `--ids-file` | 离线可验 |
| F9 | `--attacker-groups` / `--defender-groups` 的 help 改为"组内兵力取代 `--attacker/--a`，但 `--attacker/--defender` **仍须照常提供**（仅用于回显）" | 离线可验 |
| O2 | README 增**协议 method 表**（6 个，与 `src/CommandPump.cs` 逐字核对）+ 布尔/数字参数的类型约定 | ✓ |

### 9. 修复后的真机复测（13:40–13:43，部署产物 `41D5BE0D…`）—— 三项全部通过 ✅

| # | 步骤 | 期望 | 实得 |
|---|---|---|---|
| F2 | `start`(100v100) → **立刻** `abort` | 正常收尾、无引擎残留 | ✅ `aborted:true` → **10 s 内** `state=ended` / **`reason=aborted`** / `missionActive=false` / `verdict=no_mission`（修复前：loading 卡死 → **120 s** 后 `reason=watchdog_loading` → `MissionState` 残留） |
| F2 | 紧接着再 `start` | `accepted`（而不是 `wrong_state`） | ✅ `accepted:true`，且该场正常打完（13:41:04） |
| F8 | 靶场 100v100 → `bl_dummy_analyze --mode range` | 不一致 **0/N** | ✅ **不一致 0/2838 (0.0%)**，并如实报出"另 10 条未回满、该口径不适用（写 -1）"（修复前 7/2388 误报） |
| F7 | MCP `bl_start_battle` 带 `rounds=3` + `roundSwap` + `roundEndAlive=3`（100v100） | `accepted` 且产出 3 文件 | ✅ accepted；3 文件（6.1/1.3/2.8 MB）、时钟校验 **3/3 PASS**、`probe` 无越界 |
| F7 | MCP `bl_start_battle` 带 `attackerGroups`/`defenderGroups`（100v100） | `accepted` | ✅ accepted（组求和 100+100），正常收尾 |

> F2 这一条是本轮最有价值的闭环：**"loading 态 abort 把桥卡死、只能重启游戏"的根因消除** ——
> 复测里 abort 后 10 秒内就干净收尾，且下一次 `start` 立刻被接受。

---

## 二十二、全兵种 200v200 分批测试（2026-09-25 13:56–14:01，"正常战场 + 每兵种自带命令"）

**目标（用户指定）**：不用靶场、双方真实参战；**每个兵种设置属于自己的命令**；全可用兵种；规模 **200 v 200**。

**兵种清单与命令映射**（自动生成，非手写）：

| 项 | 做法 | 结果 |
|---|---|---|
| 兵种来源 | 扫全部 6020 个 XML（69 个含 `NPCCharacter`），取带 `default_group` 的唯一 id | **3203** 个（Infantry 1929 / Cavalry 652 / Ranged 420 / HorseArcher 202） |
| 可用性过滤 | `troop_sweep probe`（每包 800，引信 `tutorial_npc_basic_melee`） | **好 2259 / 坏 944** —— 与 T15 那次（2259 / 941）几乎逐数复现 |
| formation | 直接取该兵种在 XML 里的 `default_group` | 与 DSL 的 formation 取值同名，无需翻译 |
| movement（命令） | 步兵 `advance` / 弓手 `stop` / 骑兵 `charge` / 骑射 `charge` | 用户裁定："接近正常战场的默认战术" |
| 分批 | 每批攻守各 200 个兵种（每兵种 1 人），DSL 约 7.8 KB/方 | 2259 → **6 批** |

**覆盖结果 —— 逐数吻合，缺口 0** ✅

| 批 | 日志 | unit | 唯一兵种 | hit | kill |
|---|---|---|---|---|---|
| 1 | `battle_20260925_135637_391.jsonl` | 400 | 400 | 3512 | 385 |
| 2 | `battle_20260925_135710_926.jsonl` | 400 | 400 | 2730 | 288 |
| 3 | `battle_20260925_135737_569.jsonl` | 400 | 400 | 2578 | 246 |
| 4 | `battle_20260925_135757_893.jsonl` | 400 | 400 | 1587 | 193 |
| 5 | `battle_20260925_135818_124.jsonl` | 400 | 400 | **0** | 0 | ⚠️ 见下 |
| 6 | `battle_20260925_135825_051.jsonl` | 259 | 259 | 3060 | 294 |
| **合计** | | | **2259** | 13467 | 1406 |

> `probe` 判定的 2259 个可用兵种**全部 spawn 且逐个落进日志**，覆盖缺口 **0**。

**"每兵种自带命令"是否真的生效 —— 生效** ✅（`order` 事件的 (formation, order) 交叉表）

| 我们声明的 | 引擎侧实测 |
|---|---|
| `Infantry → advance` | `Infantry / Advance` 75 条 |
| `Ranged → stop` | `Ranged / Stop` 82 条 |
| `Cavalry → charge` | `Cavalry / Charge` 66 条 |
| `HorseArcher → charge` | `HorseArcher / Charge` 62 条 |

`squad` 事件逐兵种声明正确，例如
`{"troop":"Vlandian_sea_captain","formation":"Infantry","movement":"advance","spawned":1}`。
首帧编队人数刚好把每方 200 人分完（攻 100/38/44/18、守 119/33/34/14）⇒ 命令作用到了**每一个** agent。

**澄清一处易被误读的现象**：`order` 事件里有大量 `formation="Unset"`（371 条）—— 它们**全是空编队**
（`count=0`，371/371）。引擎对"声明了但无人"的组也会发一次 order 事件，**不是**"371 个兵没被编队"。

**两条异常（如实记录，均待定位）**：

1. **批 5 那次 0 命中 / 7.5 游戏秒**（`135818_124`）；**复现时正常**（`140055_862`：3562 hit / 328 kill / 218 秒）
   ⇒ 判为**偶发**。下次遇到应立即抓 `status` 的 `result.reason`（本轮抓到的 `result` 为空）。
2. **批 6 日志时间跨度 1047 游戏秒 > `durationCapSec=600`**（`135825_051`）—— 与 1 可能同源。

**我的执行器缺陷（非产品问题）**：`start` 之后立刻轮询 `status`，可能读到**上一批残留的 `ended`**，
从而跳过真正的 loading 期（首跑批 5 报"4 秒"即此因）。修法：`start` 后先等 `loading/running`，再等 `ended`。

**看门狗阈值 300 s 的实测依据**：本轮每批**总耗时 17–25 秒（含 loading）** ⇒ 400 组/场的 loading 很快，
**120 s 本来也够**；300 s 是按用户裁定取的保守值（余量更大，代价是真卡死要等更久才兜住）。

---

## 二十三、游戏内 UI 入口 + 双分包（v0.8.12，2026-09-25，分支 `prototype/ui-probe`）

**本轮定位（用户裁定）**：选项 **A + B + C 一起做**，并明确最终形态 —— **一个 mod 包 = 两个单元**：
单元 A（mod 包，游戏/启动器读；玩家用界面、AI 用控制端口）+ 单元 B（**MCP 包，放在 mod 包内**，
AI 读完它的介绍即可加载，从而既能像玩家一样用界面、又能直接连 mod 的控制通道）。
实施计划（本轮产物）：`docs/superpowers/plans/2026-09-25-ui-entry-plan.md`。

### 0. 现状核实 + 一处记载更正（本轮实测）

| 项 | 值 |
|---|---|
| 仓库 / 分支 | `C:\Users\LCGX\CodeBuddy\20260923171333\BlBridge`，`prototype/ui-probe` @ `543fe57`，工作区干净 |
| 对照分支 | `main` @ `657b982`（攻城轮，无 UI 相关代码） |
| **更正** | `docs/prototype-ui-probe-2026-09-25.md:6` 原写"结论已回填到 `PROGRESS.md` 的对应章节与后续实施计划"，**实测不成立**（本文件当时无 UI 章节、`docs/superpowers/plans/` 无 UI 计划）。本轮补齐：本 §二十三 + 上述计划文件；该行已就地更正 |

**原型轮结论（此前只存在于 `docs/`，本轮补记）**：5 个"猜不出来"的问题全部真机答完，其中第 5 问（按钮点不动）
的根因是 **prefab 的 `ButtonWidget` 缺 `DoNotPassEventsToChildren="true"`** —— 子 `TextWidget` 抢走
`MousePressed/MouseReleased`，`HandleClick()` 永不执行 ⇒ `Command.Click` 静默不触发（真人点与合成点一样没反应）。
修复后真机 `ExecutePing: clicks=1..24`。两条环境事实：**宿主会回收本会话启动的游戏进程树**（无人值守启动须走计划任务）、
**`Prefab` 是 XML（注释里不得出现 `--`）**。

### 1. 本轮落地（W1–W9，逐项带判据）

| # | 改动落点 | 判据 | 状态 |
|---|---|---|---|
| W1 | `src/UiEntry.cs`（新增）+ `src/CommandPump.cs` 分发三个新 method | `list_ui` / `open_ui` / `close_ui`；**入口 id 不存在必须报 `unknown_ui`**（官方 `ExecuteInitialStateOptionWithId` 是静默失败，我们刻意改成显式错误） | ✅ 真机已过（§5） |
| W2 | `src/ScenarioRunner.cs`：`IsBattleSetupState()` 白名单 + `ActiveGameStateName()` | 停在自有面板态时 `start_battle` 应 `accepted`；白名单外仍必须 `wrong_state`（旧行为 `CustomBattleState` 必须仍通过） | ✅ 真机已过（§5 ③④） |
| W3 | `src/BattleSetupScreen.cs` + `src/BattleSetupVM.cs` + `module/GUI/Prefabs/BlBridge/BattleSetupScreen.xml` | 面板可开、按钮可点、能开一场战斗 | ✅ 真机已过（§5 ②⑤，含真人点击） |
| W4 | VM 的「开始战斗」构造与 MCP **同形**请求并调用同一个 `ScenarioRunner.Start` | 同一请求经 UI 与经 MCP 各跑一次，响应字段同形且都 `accepted` | ✅ 真机对照已做（§5 ⑤，「`requestId=ui`」即"走哪条路"的证据） |
| W5 | `tools/bl_mcp.py`（+3 工具，21→**24**）、`tools/bl_cmd.py`（+`list-ui`/`open-ui`/`close-ui`）、`README.md`（method 表 6→**9**、工具表补齐到 24）、`tools/bl_selftest.py`（+5 断言） | `bl_selftest` 全绿 | ✅ 已核实 |
| W6 | **双分包**：`build.ps1 -Deploy` 新增 `mcp/` 子包部署（`module/mcp/{README.md,manifest.json}` + `tools\*.py` 全量复制，`manifest.json` 的 version 从 `BridgeConfig.Version` 同步）；`tools/register_mcp.py` 改为**自定位** | 部署后从 `Modules\BlBridge\mcp\` 跑 `register_mcp.py --show` 得到 `gameDirSource: self:deployed-in-module`；`bl_cmd.py buildcheck` 可跑 | ✅ 已核实（见 §2） |
| W7 | 原型退役（删 `ProtoUi.cs` / `ProtoUiScreen.xml` / `SubModule.cs` 那一行） | —— | **刻意未做**：真机通过后再删（先证新屏可跑，再删旧的） |
| W8 | 真机验收 | 见 §5 结果 | ✅ 7 条判据全部通过（+2 条反向判据） |
| W9 | 文档同步（本文件 / README / 计划文件） | —— | ✅ |

**顺带修掉的两处既有问题**：① `bl_build_check` 从**部署副本**跑时会报出一个并不存在的 `srcDir`
（`<module>\src`）—— 现改为如实标注 `sourceCheck: skipped_no_src_dir`，并把结论降级为"三段一致"；
② `ScenarioRunner` 里"必须停在自定义战斗界面"的报文补上"或用 `open_ui` 唤起面板"，
且**该前置条件从此不再必须由人完成**（`open_ui id=CustomBattle` 官方入口 id 见下一段）。

**本轮取证（一手，非推演）**：

- `Module.GetInitialStateOptions()`(Module.cs:1550) / `GetInitialStateOptionWithId()`(1555) /
  `ExecuteInitialStateOptionWithId()`(1567，`?.DoAction()` **静默失败**)；`InitialStateOption` 公开成员
  `Id/Name/OrderIndex/IsHidden/IsDisabledAndReason/EnabledHint/DoAction`（InitialStateOption.cs:6-38）。
- 官方入口 id：`CustomBattle`(5000) / `Multiplayer`(9997) / `Options`(9998) / `Credits`(9999) / `Exit`(10000) / `Editor`(-1)；
  另有 `SandBoxNewGame`（NavalDLC 在调）。
- 返回主菜单的官方路径：`CustomBattleVM.ExecuteBack()` → `Game.Current.GameStateManager.PopState(0)`（反编译 CustomBattleVM.cs:693）——
  `close_ui` 与面板上的「返回主菜单」都走它，**不去 pop 别人的状态**。
- `.mcpb` 规范（联网核实）：manifest 0.3 必填 `manifest_version/name/version/description/author/server`；
  `mcp_config` 支持 `${__dirname}` 变量替换；官方**推荐 Node**（宿主自带），Python 走 `server.type: python|uv`；
  **agent session 默认不支持 bundle** ⇒ 注册脚本仍是主路径。同类先例：Minecraft MCP Mod / MC-MCP / 网易 ModPC / rimcp-v2 / unity-mcp。

### 2. 离线验证（全部实跑，可复现）

| 检查 | 命令 | 结果 |
|---|---|---|
| 编译 + 部署 | `powershell -ExecutionPolicy Bypass -File .\build.ps1 -Deploy` | ✅ 24 个源文件 → **111 KB**；**最终判定版 `dll sha256 = AD425E547DB9FD2F…`**（真机过程中共部署 3 轮，每轮修掉一个真机缺陷，见 §3b）；部署 `GUI/`(2) + `ModuleData/`(2) + **`mcp/`(21 文件, ~366 KB)**；每轮旧 DLL 均自动备份（最近一个 `BlBridge.dll.bak_20260925_192612`），模块版本同步为 v0.8.12 |
| Python 自测 | `python tools\bl_selftest.py` | ✅ **245 项断言全过**（含新增 6 项：工具数 24、`list_ui`/`open_ui`(带 uiId / 不带 uiId)/`close_ui`(默认 / 带 state) 往返） |
| C# 离线单测 | `tools\jsontest\build_and_run.ps1` | ✅ 全部通过 |
| 编码体检 | `python tools\check_repo_encoding.py` | ✅ 73 个跟踪文件全部 UTF-8 无 BOM + LF（新文件已单文件核验行尾） |
| 时钟同源 | `python tools\bl_check_clock_reset.py` | ⚠️ **全量**：11 份历史文件仍报 FAIL（**对照组按预期成立**）；`--since 18:47:37`：无新产物 ⇒ `exit 2`（数据不足）——本轮的"新产物 0 失败"那半边**要等真机跑完才有**，不得当作已验 |
| 双分包零配置 | `python "<游戏根>\Modules\BlBridge\mcp\register_mcp.py" --show` | ✅ `gameDirSource = self:deployed-in-module`（上溯三级自定位）；`bl_cmd.py buildcheck` 可跑 |

### 3. 真机结果（2026-09-25 19:18–19:30，三轮启动；最终判定版 `dll sha256 = AD425E547DB9FD2F…`）

启动方式：**Windows 计划任务**（`Register-ScheduledTask -Principal (LogonType Interactive)` + `Start-ScheduledTask`），
理由见原型结论文档 §七（宿主会回收本会话启动的进程树）。每轮都是一次完整闭环，判据逐条对照：

| # | 步骤 | 结果 | 证据 |
|---|---|---|---|
| ① | 主菜单 `bl_cmd.py list-ui` | ✅ | 11 个入口：官方 6 个（含 `CustomBattle` 自定义战斗）+ 我们的 `BlBridgeBattleSetup`（**中文名"BlBridge 战场面板"渲染正常**）+ 原型探针；**113 个 `battle_*` 场景** |
| ② | `bl_cmd.py open-ui --ui-id CustomBattle` | ✅ | `requested:true` → 轮询到 `activeState=CustomBattleState` ⇒ **"必须由人点进 CustomBattle"这个前提被取消**，agent 自己走进去 |
| ③ | `bl_cmd.py close-ui --state CustomBattleState` | ✅ | `closeRequested:true` → `activeState==""`（回主菜单）。**这条是本轮新增能力**：没有它 agent 进去就出不来 |
| ④ | `bl_cmd.py open-ui`（自有面板） | ✅ | `activeState=BattleSetupState`；日志：`BattleSetupScreen.OnInitialize` → `LoadMovie OK: BattleSetupScreen -> movie=not-null` → `OnFrameTick: LoadingWindow.DisableGlobalLoadingWindow() called`（第 2 帧） |
| ⑤ | 面板态 `bl_cmd.py start --attacker imperial_legionary --defender battanian_wildling --a 20 --d 20` | ✅ | `accepted:true`（**不再 `wrong_state`**）⇒ W2 白名单生效；21.7 s 打完（`battle_20260925_192819_127.jsonl`，2094 KB），面板自动回来（`OnActivate（战斗结束回到面板）`） |
| ⑥ | **真人点面板上的「开始战斗」** | ✅ | `11:29:18.501 \| UI start_battle request: {"attackerTroop":"imperial_legionary","attackerCount":20,"defenderTroop":"battanian_wildling","defenderCount":20,"scene":"battle_terrain_a","durationCapSec":600,"orders":"charge","playerSide":"attacker"}` → `11:29:18.647 \| UI start_battle response: {…"id":"ui"…"accepted":true…}`；`status.requestId = "ui"`；`battle_20260925_192918_982.jsonl`（2324 KB）；战斗结束后面板再次 `OnActivate` |
| ⑦ | 反向判据：面板态 `open-ui --ui-id CustomBattle` | ✅ | `wrong_state_for_ui`（**且游戏不再卡死**，`activeState` 仍是 `BattleSetupState`）—— 这正是 §4 修掉的 F2 |
| ⑧ | 反向判据：面板态调自有入口 | ✅ | `requested:false, alreadyOpen:true`（幂等：**不执行**入口动作，所以不会触发 ⑤ 的闸门） |
| ⑨ | 引擎自带 disabled 入口（`--ui-id Exit`） | ✅ | `ui_disabled`（引擎自己报 `disabled=true`，我们如实转达、不硬闯） |

**W4「同一条管线」的真机证据**：⑥ 里面板构造的请求键名/取值与 ⑤（端口）逐字同形，两条路都 `accepted`；
区分点只有一个 —— `status.requestId`：端口发起的是 16 位 hex，界面发起的是 **`ui`**（这就是"走的哪条路"的可核验标记）。
界面截图（`%USERPROFILE%\Documents\...\BlBridge\ui\panel-final.png`）：中文无方块、绑定全部生效、
默认场景已是 `battle_terrain_a (38/113)`。

### 3b. 真机抓到的三个缺陷（离线自测结构性抓不到）与修复

| # | 缺陷 | 现象 | 根因 | 修法 |
|---|---|---|---|---|
| **F1（high）** | `open_ui` 的参数取名 `id` | 不带参数时返回 `unknown_ui: 没有这个入口 id: 85a0d3d3fba54d4e` | `src/Jmini.cs` 是**扁平**读取器（按"文本里第一个 `"key"`"取值），而请求信封自带 `"id"`（16 位 hex 请求 id）⇒ `Jmini.Str(raw,"id")` 恒读到信封那个值 | 参数改名 **`uiId`**；并写进 `AGENTS.md` 的"控制通道参数命名硬规则"。**假游戏端用真 JSON 解析 ⇒ 自测天然抓不到这类碰撞**（第一轮真机才暴露） |
| **F2（high）** | `open_ui` 在非主菜单状态执行 | 从 `CustomBattleState` 调 `open_ui` 后**卡在 `GameLoadingState` 90 s+ 不推进**，日志无异常、进程仍响应（CPU 465 s）⇒ 只能重启游戏 | `InitialStateOption` 的 action 基本都是 `MBGameManager.StartNewGame(...)`，它做 `CleanAndPushState(GameLoadingState)` 并重跑数据加载；**已加载 Game 时执行它会让状态机会卡住** | ① 加**主菜单闸门**：`ActiveGameStateName() != ""` 一律 `wrong_state_for_ui`（Fail Fast）；② `close_ui` 加 `state` 参数，白名单放行 `CustomBattleState`（官方「返回」同一个 `PopState`）⇒ agent 进得去也出得来 |
| **F3（low）** | 面板默认场景 | 默认落在 `battle_terrain_001`（113 个场景里字典序第一） | 直接取列表首项 | 显式优先 `battle_terrain_a`（与 `bl_cmd.py --scene` / MCP 默认逐字对齐 —— 同一条管线的要求） |

**本轮最值得记的一条**：F1 说明"离线自测全绿"不等于"控制通道没问题" ——
假游戏端是 `json.loads`（认嵌套），真游戏端是 `Jmini`（扁平），**两者对同一个请求的解释不同**。
⇒ 控制通道新增/改名参数，必须真机过一遍，或至少确认参数名不与信封键同集合。

### 4. 未决（需用户裁定，本轮未擅自决定）

1. **控制端口形态**：维持文件 IPC（本轮选择）／另加 localhost TCP（更接近字面"端口"，多一条代码路径 + 安全面）。
2. **`.mcpb` 是否作为强制分发物**（宿主支持时一键装很好，但 agent session 不支持 bundle）。
3. **面板要暴露哪些参数**：本轮只做「攻守兵种 + 双方人数 + 场景 + 开始/刷新/返回」（**纯按钮轮选、无文本输入** ——
   因为"键盘输入到 Gauntlet 层"没有真机证据，唯一被验证过的交互原语是按钮点击）。
   候选兵种取自 T15 已判定可用的集合（`docs/ledgers/sweep_probe_full-2026-09-25.json` 的 `good`），
   后续是否做**兵种选择器 / 战术组 DSL 输入**属下一轮范围裁定。
4. **分支去留**：W7 之后 `prototype/ui-probe` 是否改名 `feat/ui-entry` 并合回 `main`。

---

## 二十四、v0.8.13：原型退役 + 官方风格面板（兵种形象/数据）（2026-09-25 晚）

**用户裁定**：① 原型**退役**；②「界面太丑太简陋，官方能做到的先抄过来」——具体点名**官方选兵种时显示兵种形象与数据**、
**选地图时显示地图样貌**。

### 1. 原型退役（W7，已完成）

删除 `src/ProtoUi.cs` + `module/GUI/Prefabs/BlBridge/ProtoUiScreen.xml`（`git` 里可恢复：`git checkout 543fe57 -- src/ProtoUi.cs`），
`src/SubModule.cs` 里的 `ProtoUi.Register()` 一行与 `CNs` 语言文件里两个 `BlBridgeProto_*` 键一并删除。
部署后 `GUI/` 只剩 **1 个文件**（`BattleSetupScreen.xml`），`list_ui` 里 `BlBridgeProtoBattleTest` 不再出现。

### 2. 面板改成官方机制（逐条抄，附官方出处）

| 抄的东西 | 官方出处（`Modules\SandBoxCore\GUI\Prefabs\CustomBattle\`） | 我们怎么用 |
|---|---|---|
| **兵种形象** | `TroopTypeSelectionPopUp.xml:32` → `<ImageIdentifierWidget DataSource="{Visual}" AdditionalArgs="@AdditionalArgs" ImageId="@Id" TextureProviderName="@TextureProviderName"/>` | 每张卡片一个 `ImageIdentifierWidget`，`Visual` = `CharacterImageIdentifierVM(CharacterCode.CreateFrom(character))`（官方 `CustomBattleTroopTypeVM.cs:169` 原句） |
| **等级图标** | `TroopTypeSelectionPopUp.xml:34`（`Sprite="@Text"`，数据来自 `CustomBattleTroopTypeVM.GetCharacterTierData`） | 把官方那段拼串逻辑抄成 `BuildTierIconSprite`：`"General\\TroopTierIcons\\icon_tier_" + clamp(ceil((Level-5)/5),0,7)`（Hero 记 0） |
| **选中标记** | `TroopTypeSelectionPopUp.xml:48` → `Sprite="SPGeneral\GameMenu\companion_selected_check"` + `IsVisible="@IsSelected"` | 卡片右侧打勾；`ButtonType="Radio"` + `IsSelected="@IsSelected"` 取官方 `ArmyComposition.xml:43-50` 的写法 |
| **列表 + 卡片** | `ArmyComposition.xml:43-50`（`ListPanel DataSource=` + `ItemTemplate` + Radio 按钮） | 攻守两列各 10 张卡片 |
| **面板/分隔线** | `CustomBattleScreen.xml:68/90` → `Sprite="flat_panel_9" ExtendLeft="53" ExtendRight="52" AlphaFactor="0.5"` + `SPGeneral\TownManagement\title_divider` + `StdAssets\subpage_divider` | 三列面板 + 标题分隔线 |
| **暗底** | `TroopTypeSelectionPopUp.xml:12` → `Sprite="BlankWhiteSquare" Color="#000000B0"` | 全屏压暗，让面板浮起来 |

**我们自己加的一条**（超出官方）：卡片上直接显示一行数据（`Lv / 兵种类型 / 数值最高的 4 项技能`）。
理由：官方把技能放在**悬停 tooltip** 里，而 tooltip 在"看截图/自动化"场景读不到。数据来源 `character.GetSkillValue(SkillObject)`。

### 3. 真机结果（`dll sha256 = 1ED66EE9E2BA7D12…`）

- 面板进入 `BattleSetupState`、`LoadMovie OK`、全局加载窗口第 2 帧关闭 —— 与 v0.8.12 判据一致（未回归）。
- **截图**：`%USERPROFILE%\Documents\Mount and Blade II Bannerlord\BlBridge\ui\panel-official-style-v2.png`
  —— 两列共 20 张卡片全部渲染出**真实兵种形象**、中文名、等级图标、数据行；中间列显示人数/场景/两侧选中项/日志/按钮。
- **卡片选择**：截图里守方选中项已从默认 `battanian_wildling` 变为 `battanian_hero`，且该卡片带官方打勾
  ⇒ 有人（人）点了那张卡：**点卡片选择 = 真机验证过，VM→界面回写（中间文字 + 打勾）双向通**。
  ⚠️ 诚实标注：**选择变更目前不写日志**，所以这条只有截图证据，没有日志证据（下一轮补日志）。

### 4. 关于"选地图显示地图样貌"——官方**没有**这个功能（取证结论）

三条一手证据：
1. 官方地图选择控件是**纯文字下拉**：`CustomBattleScreen.xml:125-131` → `Standard.DropdownWithHorizontalControl` 绑 `{MapSelection}`；
2. 地图项的 VM **没有任何图像字段**：`MapItemVM.cs:7-62` 只有 `MapName` / `MapId` / `ForcedSceneLevel` / `NameText`（`SelectorItemVM` 子类）；
3. 游戏里**不存在**场景预览贴图：`SceneObj\battle_terrain_a\` 只有 `scene.xscene` / `terrain.bin` / `navmesh.bin` / `atmosphere.xml` / `flora.bin`，
   全库搜 `*battle_terrain*` 在 `SceneObj` 之外 **0 命中**。

⇒ 想"显示地图样貌"只有我们自己造：要么预先为若干常用场景截图存进 mod 包（113 个全做不现实），
要么用引擎的 tableau/截图通道在面板里现场渲染（成本与风险都高）。**需用户裁定**（见 §4 未决）。

### 5. 下一轮候补（已识别，未做）

1. **兵种选择弹窗 + 搜索**（官方 `TroopTypeSelectionPopUp` 的完整形态：全兵种、搜索框、全选/还原）；
2. **大号 3D 形象**（官方 `ArmyComposition.xml:17` 的 `CharacterTableauWidget`，绑 `CharacterViewModel` + `BodyProperties/EquipmentCode/...`）
   —— 需要先摸清 `CharacterViewModel` 的填充 API（它在 `ViewModelCollection`，**不在反编译索引里**，索引查它会得到假阴性）；
3. **护甲/武器图标列**（官方 `ArmyCompositionItemVM` 的 `ArmorsList`/`WeaponsList` + `EquipmentTypeVisualBrushWidget`）；
4. 卡片选择/开战写日志（把 §3 那条"只有截图证据"补成日志证据）；
5. 地图样貌（取决于用户裁定）。

---

## [2026-09-25] §二十五 面板退役：官方界面即入口 + 上帝视角（v0.8.14）

### 1. 用户裁定（推翻了上一节的整个方向）

> "我们白设计了 …… 他有接口，我们只设计把场景和兵种接进去不就行了？"
> "甚至可以把自己设计的自定义删除了，官方本身就自带了。"
> "控制通道肯定留着，只不过只让 AI 调用 …… 我们人就老老实实用官方的玩。"

**依据（两条一手来源）**：
- 官方战斗界面（用户截图 `blbridge-official-custombattle-reference.png`）：
  游戏类型 / **玩家类型（指挥官）** / **选择攻守方（攻击方·防守方）** / 地图·季节·时间·雨雪密度·雾密度 + 军团规模。
- 官方开战接口是**公开静态方法**：`CustomBattleHelper.StartGame(CustomBattleData)`（`CustomBattleHelper.cs:80`），
  `CustomBattleData` 字段全 public（`GameTypeStringId` / `SceneId` / `WallHitpointPercentages` /
  `IsPlayerAttacker` / `SceneUpgradeLevel` / `IsSallyOut` …）。⇒ 我们的活只剩"填数据"，不是"造界面"。

⇒ **面板（`BattleSetup*` 4 个文件 + prefab + 语言文件 + 主菜单入口注册）全部删除**；
`close_ui` 保留但白名单收缩为单值 `CustomBattleState`（`open_ui` 是门，没有出口 agent 进去就出不来）；
`wrong_state` 守卫同理收回单值。

### 2. 官方两张表的取证（决定"能不能接 mod 兵种 / 更多场景"）

| 表 | 声明 | 加载 | 结论 |
|---|---|---|---|
| `CustomBattleScenes` | `<XmlName id="CustomBattleScenes" path="custom_battle_scenes"/>` | `CustomGame.cs:116` `MBObjectManager.GetMergedXmlForManaged(...)` —— **合并** | 可扩：加带 flag 的场景条目即可 |
| `NPCCharacters`（`custombattlecharacters.xml`） | 同表 id，`IncludedGameTypes=CustomGame/EditorGame` | `CustomGame.cs:125` `LoadXML(...)` | 见下"反转" |

- 场景表属性全集（实测）：`id / name / terrain` + `is_siege_map`(94) / `is_village_map`(102) /
  `is_lords_hall_map`(17) / `forced_scene_level`(14) / `forest_density`(6)。
- **NavalDLC 用的是同一张表**（`naval_custom_battle_scenes.xml`，37 条）：
  `is_naval_map`(21)=**海战**、`is_naval_raid_map`(16)=**海上掠夺**，新地形 `River/CoastalSea/OpenSea`。
  ⇒ **"游戏类型"那一行不是硬编码**，是从场景表 flag 推导的（官方只定义了 Battle/Siege/Village 三个常量）。
- **反转**：`custombattlecharacters.xml` 全表只有 **24 条、`is_hero="true"` 24/24**，全是 6 文化 × 4 个领主/指挥官
  （`commander_1..`），**一个正规军兵种都没有**（`imperial_legionary` 命中 0）。
  ⇒ 界面兵种列表是**运行时**从当前游戏类型已加载的兵种表里按"文化 + 编队"枚举的
  （`ArmyCompositionItemVM.cs:252-256` 逐个过 `IsValidUnitItem`，只判 `DefaultFormationClass`）。
  ⇒ 推论（**待真机核对**）：第三方 mod 兵种很可能**本来就出现在**官方界面里，无需我们做任何事。

### 3. 本轮交付（v0.8.14）

| 项 | 位置 | 说明 |
|---|---|---|
| 上帝视角 | `src/SpectatorWatch.cs` | 实现官方 `ICameraModeLogic`，`GetMissionCameraLockMode` 返回 `SpectatorCameraTypes.Free`；参数 `spectate=true`（CLI `--spectate` / MCP `spectate`）。**消费即清**：标志在 `SubModule.OnBeforeMissionBehaviorInitialize` 用完立刻复位，绝不带进玩家自己的战斗 |
| 官方场景全表 | `src/CustomBattleScenes.cs` | 读合并表（与官方 `CustomGame` 同一调用形态），`list_ui` 吐 314 行 + `modes` 计数 + 每行 `exists`（目录是否真在磁盘上，防 0xC0000005） |
| 面板退役 | 删 `BattleSetup*.cs` ×3、`module/GUI/*`、`ModuleData/Languages/CNs/*`；`UiEntry` 去掉 `EnsureRegistered` | `BlBridge.dll` 118.5 KB → **98.5 KB** |

**引擎侧依据（上帝视角）**：`MissionScreen.cs:395-397` 从 mission behaviors 里挑 `ICameraModeLogic`；
`:3805-3815` 用其返回值决定相机锁模式；`SpectatorCameraTypes` 全集
`Invalid=-1, Free=0, LockToMainPlayer=1, LockToAnyAgent=2, LockToAnyPlayer=3, LockToPlayerFormation=4,
LockToTeamMembers=5, LockToTeamMembersView=6, LockToPosition=7`；官方先例 `TournamentBehavior.cs:81-87`。

**离线验证**：`bl_selftest.py` **全部通过**（含新增：`list_ui` 带回场景全表与模式计数、
`open_ui` 默认 `CustomBattle`、`close_ui` 默认 `CustomBattleState`、`spectate` 透传）；编译 0 error。

### 4. ⚠️ 未验证项（诚实清单，下一轮真机逐条打勾）

1. `spectate=true` 真机下的**镜头行为**：`lockedToMainPlayer=true` 时本类仍返回 `Free` ——
   按 `MissionScreen:3826-3828` 语义，true 是"引擎想锁主玩家"（例如部署阶段），
   强行自由镜头可能让那些阶段偏离官方流程。**每次询问都写进 `ui.log`**（前 3 次 + 总计），
   跑一次即可看到引擎怎么问、结果如何，再用证据决定要不要在部署阶段让步。
2. **身体仍在**：`commander_1` 必须留在玩家方 party（v0.8.11 的崩溃教训），所以上帝视角是"自由镜头"
   而不是"没有身体"。"彻底无身体"（V2）单独一轮做 —— 要重验那条历史崩溃点。
3. `list_ui` 的 314 行 JSON（约 35 KB）走文件 IPC 的**大小上限**（`RequestGuard`）是否受得住 ——
   只读过请求上限，没核过响应侧上限；真机跑一次 `list-ui` 即知，必要时用 `sceneLimit`。
4. 官方界面里**是否已列出 mod 兵种**（第 2 节的推论）—— 人眼确认即可，不用改代码。

### 5. 明确未做（已识别）

- XML 扩展层（在**我们自己的模块**里声明 `CustomBattleScenes` / `NPCCharacters` 同 id 表，
  补 mod 场景与 mod 兵种）—— 机制已查清，但"官方表里到底缺什么"要靠第 4 节第 4 条的真机核对结论，
  否则就是瞎加条目。
- V2「无身体观战」。

### 6. 本节的真机验证结果（同日晚，逐条打勾）

| 第 4 节的那条 | 结果 |
|---|---|
| 4.1 上帝视角真机行为 | ✅ **通**。`ui.log`：`SpectatorWatchBehavior 已挂载` → 引擎询问 `lockedToMainPlayer=False missionMode=Deployment` → 返回 `Free`；整场**被询问 26880 次**。截图 `ui_20260925_202142.png`：自由观察镜头（城墙/城门/营地全景）+ 官方观战 UI（上一个/下一个角色）。战果 `defenderWiped`（攻方 42/51 存活，35.6 秒）。**顺带推翻了我原先的担心**：`lockedToMainPlayer` 全程 `False`，不存在"部署阶段引擎要锁主玩家"的情况 |
| 4.2 身体仍在 | 如实存在：上帝视角 = 自由镜头，不是无身体（V2 仍待做） |
| 4.3 314 行 JSON 是否超 IPC 上限 | ✅ 通（317 行 / 约 35 KB 正常往返；`list_ui --mode siege --limit 5` 与全表都跑过） |
| 4.4 官方界面是否已列出 mod 兵种 | ⏳ 未查（需人眼看官方兵种弹层） |

顺带验证了**全自动闭环**：主菜单 →`open_ui`→ 官方界面 →`start_battle`(攻城) → 跑完 →`close_ui`→ 主菜单 →`open_ui uiId=Exit`→ 游戏退出。全程无鼠标。

---

## [2026-09-25] §二十六 崩溃 A/B 与 `list_ui` 加固（v0.8.14 续）

### 1. 那次崩溃的 A/B（**不可复现**，未坐实元凶）

现象：`0xE0434352`（**托管异常未捕获**）崩在 `20:14:34`，即 `open_ui CustomBattle` 之后引擎在
`loading managed_core_parameters.xml` 的**数据加载窗口**里（前一行是 `Warbandlord\ModuleData\*.mbproj`）。
栈：`clr.dll` → `TaleWorlds.Native` → **`MonoMod.Utils`**（BLSE 的 detour 基础设施 ⇒ 只有被 patch 的方法才走这里）。

| 轮次 | 做法 | 结果 |
|---|---|---|
| 1 | 主菜单 → `open_ui` → **全程不碰端口** 45 秒 | **没崩**；官方界面正常（截图 `ui_20260925_201914.png`）；错误日志 0.2 KB（只有文件头） |
| 2 | 回主菜单 → `open_ui` → **加载期猛调 `list_ui` × 14** | **也没崩**；14 次全部返回 `CustomBattleState`；错误日志仍 0.2 KB |

⇒ 结论：**我们新写的 `list_ui` 不是已坐实的元凶**（从"嫌疑"降为"未坐实的可能"）。
证据更指向第三方 patch（`MonoMod`）或资源缺失（崩前成片的 `Could not find the event index for: event:/...`）。
**诚实边界**：只跑两轮、该崩溃只发生过一次 —— 是"没能复现"，不是"已排除"。

**但捞到一条真发现**：第二轮**轮询 #1 无响应** —— 引擎在切状态/加载的那一刻，我们的泵答不上来。
⇒ "别在引擎切换期干重活"这条判断独立成立，于是有了下面的加固。

### 2. `list_ui` 场景表加固（本轮改动）

| 改动 | 原因 |
|---|---|
| **不再用** `MBObjectManager.GetMergedXmlForManaged`（引擎的加载通道） | 只读数据不该走带全局状态/副作用的通道，尤其不该在引擎自己的加载窗口里插进去 |
| 改为**扫各模块 `SubModule.xml` 的 `<XmlName id="CustomBattleScenes" path="…"/>` 声明**，直接解析 `ModuleData/<path>.xml` | 纯文件读；合并语义与引擎一致（**同 id 后者覆盖**）；代价=看不到"运行期动态注册"的表（官方与常见 mod 都是静态声明，且 `sourceFiles` 会体现） |
| 结果**缓存**（TTL 120 秒） | 省掉重复磁盘 IO；响应里带 `fromCache` 便于核对 |
| 逐行 `exists`（磁盘存在性）**只对真正吐出去的行算**，且**切换窗口内整段跳过** | 旧实现 317 行 × 每行一次全模块目录扫描；现在是"按需 + 可降级" |
| `open_ui` 成功后开 **45 秒切换窗口**，窗口内 `list_ui` 走缓存 + 跳过磁盘检查，并标 `existsChecked=false` + `note` | 降级而不是拒答：AI 正是靠这个窗口轮询 `activeState` 看自己有没有走进去 |

真机验证：主菜单首次 `list_ui` → `fromCache=false / sourceFiles=2 / existsChecked=true / missingInReturned=0`；
50 秒后再调 → `fromCache=true`（缓存命中）、`existsChecked=true`。
**切换窗口内的降级路径本次没能观测到**：那一次请求**根本没被应答**（与第二轮轮询 #1 同一现象）——
说明窗口判断是对的（那一刻主线程被引擎占着），但"降级响应"这条路要等一次"答上了"的切换才能看到。

### 2b. 相机插桩的结论（v0.8.15，用户报告"野战=上帝视角、围城=观战视角"）

**我们先前的判断错了两次，这次有插桩证据。**

用户观察：`start --spectate` 的**野战**是 RTS 式上帝视角，**围城**却是"观战视角"（锁定某个 agent、带上一个/下一个角色）。

插桩（`src/SpectatorWatch.cs`，v0.8.15 临时诊断）给出的对照：

| | 野战 `battle_terrain_a` | 围城 `sturgia_castle_siege_001` |
|---|---|---|
| `ICameraModeLogic` 实现者（按下标） | `[30:FlyCameraMissionView, 43:SpectatorWatchBehavior]` | `[47:FlyCameraMissionView, 88:SpectatorWatchBehavior]` |
| 首个（`MissionScreen.cs:396` 是 `FirstOrDefault` ⇒ 它胜出） | **FlyCameraMissionView** | **FlyCameraMissionView** |
| 我们的方法被调用次数 | **8**（仅初始化期探测） | **8511**（约每帧） |
| 行为数变化 | 73 | 120 → **116**（有行为被移除） |

⇒ **野战里根本轮不到我们**：`FlyCameraMissionView` 排在下标 30 就先被选中了。而它是 **`RTSCamera.dll`（用户装的 mod）** 里的类 —— 在官方反编译源码里**搜不到**（全模块 DLL 二分搜索唯一命中 `Modules\RTSCamera\bin\...\RTSCamera.dll`）。
**所以用户在野战看到的"RTS 上帝视角"是 RTSCamera 提供的，与我们的 `spectate` 无关。**

那围城为什么不一样？RTSCamera 自己的配置（`Documents\...\Configs\RTSCamera\RTSCameraConfig.xml`）写着：

```xml
<DefaultToFreeCamera>DeploymentStage</DefaultToFreeCamera>
<ElevatedCameraTriggerMode>WhenOpeningOrderUI</ElevatedCameraTriggerMode>
<ElevatedHeight>10</ElevatedHeight>
<ElevatedHeightInSiege>0</ElevatedHeightInSiege>   <!-- ← 攻城专用抬升高度 = 0，等于关掉 -->
```

⇒ **RTSCamera 默认在攻城时不给抬升上帝视角**，引擎那套观察者镜头就露出来了。**这不是我们的 bug，是 mod 的默认配置。**

**已做的最小改动**（用户裁定前先试探）：`ElevatedHeightInSiege` `0 → 10`（备份 `RTSCameraConfig.xml.bak_before_20260925`）。
真机对照：不带 `spectate` 的围城 = 第三人称跟身；带 `spectate` = 观察者镜头；改配置后 = 视角明显抬高拉远（截图 `ui_20260925_204927.png`）。
若要"像野战那样常驻自由上帝视角"，还需把 `DefaultToFreeCamera` 改成 `Always`（合法值：`DeploymentStage`/`Always`/`Never`）与/或 `ElevatedCameraTriggerMode` 改成 `Always`（现为 `WhenOpeningOrderUI` ⇒ 只有打开命令 UI 时才抬升）。

**由此得出的一条设计结论**：我们的 `SpectatorWatchBehavior`（`ICameraModeLogic → Free`）是**重复建设** ——
野战被 RTSCamera 抢先（无效果），攻城给的是观察者镜头（不是用户想要的自由视角）。
要不要删它，等用户裁定（见 §二十七）。

### 2c. B 方案的落地（v0.8.15：我们当 RTSCamera 的「参数管理员」）

用户裁定："**B 做好**，还能装 RTSCamera 用它的功能吗" ⇒ 能，这正是 B 的形态（我们**不碰它的代码**，只读写它的配置）。

**为什么不做"把 RTSCamera 抄进我们 mod"（用户后来提的方案）**：
- 分发包里**没有许可文件**（只有 CHANGELOG/README，且 README 只讲功能）⇒ 默认按"保留所有权利"处理，抄 DLL/反编译代码进我们要发布的包 = 侵权风险；功能可以自研（clean-room），代码不能抄。
  > ⚠️ **更正（2026-09-25，用户给出 GitHub 链接后实查；措辞就地保留，不删历史）**：本条**结论作废**，只有"分发包里没有 LICENSE 文件"这个**事实**成立。
  > - 依据**就在我们自己的安装包**里：`Modules\RTSCamera\README.html:1368`（中文版 `README.zh-CN.html:1366`）写着
  >   `You can get source code at github.com/lzh-mb-mod/RTSCamera` ⇒ README **不只是讲功能**。
  > - 上游仓实查（GitHub API）：`lzh-mb-mod/RTSCamera` = **MIT**，`Copyright (c) 2020 Li Zhenhuan`，C#，默认分支 `master`，最后推送 2026-09-15；
  >   `source/` 内含 `RTSCamera.sln` + `RTSCamera` + **`RTSCamera.CommandSystem`** + `RTSCameraAgentComponent` + `library`；
  >   同组织 `MissionLibrary` / `BattleMiniMap` / `CinematicCamera` / `EnhancedBattleTest` / `ImprovedCombatAI` **均为 MIT**。
  > - ⇒ 正确定性：**RTSCamera 系可合法阅读 / 修改 / 分发（MIT），条件是保留版权与许可声明**；
  >   "抄不得"只剩**合规手续**（附 LICENSE、保留版权行），不再是障碍。
  > - **只改法律面，不改工程量面**：下面那条"体量"依旧成立，"用户要的效果只需改配置"（B 方案）也依旧是最省事的路径。
  > - **可复现入口**：`curl -s https://api.github.com/repos/lzh-mb-mod/RTSCamera | findstr license` 与
  >   `findstr /i "source code" "…\Modules\RTSCamera\README.html"`（本机两处路径已在上面写明）。
- 体量：`RTSCamera.dll` 267 KB IL + 自带 `MissionLibrary.dll`(20 KB) + `RTSCameraAgentComponent.dll`(8 KB) + GUI prefabs + 60~70 个配置项 + 自己的 MCM 页面 + 战帆船只接管 ⇒ 全抄等于重写一个中型 View/输入 mod，维护从此归我们。
- 而用户要的效果（攻城也能有抬升视角）**只需要改它的配置**。

**实现**（`tools/bl_rts.py`，与 Warbandlord 的 `bl_apply_config` 同纪律：形状校验 → 拒绝 XML 特殊字符 → 备份 → 临时文件 → XML 解析校验 → 原子替换 → 回读核对）：

| 面 | 内容 |
|---|---|
| 配置 | `(Documents)\Mount and Blade II Bannerlord\Configs\RTSCamera\RTSCameraConfig.xml`（扁平 `<Key>值</Key>`，UTF-8 **带 BOM**，写回保留） |
| 读 | `bl_rts_config`（MCP）/ `bl_cmd.py rts-config`（CLI） |
| 写 | `bl_apply_rts_config`（MCP，支持 `preset`/`edits`/`dry_run`/`allow_missing`）/ `bl_cmd.py rts-apply`（`--preset` / `--set K=V` / `--dry-run`） |
| 预设 | `siege-god`(攻城抬升10) / `free-always` / `elevated-always` / `god-full`（后三个含未证实枚举值时会给 warnings，**不静默**） |
| 开战联动 | `bl_start_battle` 的 `rtsPreset` 参数 / `bl_cmd.py start --rts-preset`：**发请求之前**写配置（因为 RTSCamera 每场开始时读一次） |
| 我们的相机 | `SpectatorWatchBehavior` **降级为兜底**：`SubModule` 里检测到 RTSCamera 已启用就不挂（避免两边抢 `ICameraModeLogic`/输入），只当"没装 RTSCamera 的机器"的兜底 |

**真机验证**：`rts-apply --preset god-full` → 写入并回读核对通过（`ElevatedHeightInSiege` 0→10、`DefaultToFreeCamera`→Always、`TriggerMode`→Always 并给出"未证实"警告）+ 自动备份。
离线自测新增 7 条断言（读/dry-run 不落盘/预设+备份+回读/未知键拒绝/XML 特殊字符拒绝/未知预设拒绝/未指定键保持原值）—— **全部通过**，工具数 26。

**⚠️ 一条新证据（B 的时序约束）**：我早先写的 `ElevatedHeightInSiege=10` **在 20:53 那次退出后被覆写回 0** ⇒
RTSCamera **只在启动时读一次配置，退出时会用内存值把文件覆写回去**。
⇒ B 的正确姿势是"**每场开战前写**"（`--rts-preset` 正是如此），不是"改一次永久"；要永久必须在游戏内 MCM 改。
另：`ElevatedCameraTriggerMode=Always` 属于**未证实的枚举值**（DLL 里存在 `Always` 串，但没有证据表明本键接受），已撤回为 `WhenOpeningOrderUI`。

**已办（2026-09-25 真机，v0.8.16）**：① 视角验收完成 —— 见 §二十八；② "C# 兜底降级"已部署（`0.8.16` / 进程内 `loadedSha256=3042dec51a4ed7ac`）并真机读到让位日志；③ README 工具表已补（含 `bl_ghost_camera`）。
**仍待办**：关掉游戏后跑一次 `build.ps1 -Deploy`，把最新 `tools\*.py` 同步进发货副本 —— 本次部署时游戏正在跑、DLL 被占用，之后又改了 `bl_mcp.py` / `bl_cmd.py` / `bl_launch.ps1`，所以 `Modules\BlBridge\mcp\` 里那两份落后于仓库（**MCP 实际加载的是仓库 `tools\bl_mcp.py`，见 §二十八第 4 条**，故不影响本机使用）。

### 3. ⚠️ 口径差异（重要，别当 bug 修）

我们的合并视图 **303 条**；旧实现（引擎口径）**317 条**。差额来自**同 id 重复**：
官方 `custom_battle_scenes.xml` 内部就有同一个场景 id 出现多次的条目（实测该文件 373 个 `<Scene>` 节点、
唯一 id 只有 312），叠加 NavalDLC 与它的 9 条跨文件重名 ⇒ 引擎那份列表里会出现**重复行**。
我们按 id 合并（后者覆盖），列表更干净、按 id 选场景无歧义 —— **有意的差异**，不是解析错误。
若哪天要与官方界面做逐行对照，先记得这 14 条的差。

---

## [2026-09-25] §二十八 相机三案收口：A/B/C 全部真机验完（v0.8.16）

用户裁定"**两个一起做**"（视角验收 + C 方案），并要求"**下次我开启先把 RTSCamera 关了**"。本轮把三件事一次验完。

### 1. 三案的最终结论（每条都有真机出处）

| 案 | 形态 | 真机结论 |
|---|---|---|
| **A** | 什么都不做（用户按 X 用 RTSCamera） | 可用，但"要人按键"；AI 场次无效 |
| **B** | 我们当 RTSCamera 的**参数管理员**（`bl_apply_rts_config`） | 上一轮已验证写入/回读；本轮复跑 `god-full` 成功（自动备份 `RTSCameraConfig.xml.bak_20260925_211148`） |
| **C** | 我们直接用**引擎自带**的幽灵/自由相机（`MissionScreen.IsCheatGhostMode`） | **本轮落地并真机通过**（见第 3 条） |

**并查清了一件一直含糊的事**：`FlyCameraMissionView`（上一轮插桩里"抢在我们前面"的那个）**不在官方源码索引里** —— 搜 `GetMissionCameraLockMode` 全树只有 5 处（官方 `TournamentBehavior`、多人模式 2 处、接口自身、消费方 `MissionScreen`）⇒ 它属于 **RTSCamera**。于是：
- 装了 RTSCamera：它的 `ICameraModeLogic` 排在我们前面（`[47:..., 88:我们]`，已有真机日志）⇒ 抢不过；
- **不装 RTSCamera：我们就是唯一实现者** ⇒ 我们的 `Free` 生效。

### 2. 我们自己的相机（RTSCamera 关掉后）到底怎么样 —— 真机

启动方式（新增能力，见第 5 条）：`bl_launch.ps1 -ExcludeModules "RTSCamera,RTSCamera.CommandSystem"` ⇒ `kept 41 of 43`。

证据链（`ui.log` + `siege_debug.log`）：
```
13:22:07  calling OpenSiegeMissionWithDeployment playerChar=commander_1 isPlayerAttacker=True atk=41 def=30
13:22:08  spectate: SpectatorWatchBehavior 已挂载（兜底：本机未装 RTSCamera）   ← 这次真挂上了
13:22:09  flags@tick IsSiegeBattle=True TeamAIType=Siege attackerTeamAI=TeamAISiegeAttacker
13:22:09  FinishDeployment OK
```
画面（`ui\ui_20260925_212239.png`）：**攻城场景的抬高俯瞰全景** —— 城墙、城门、攻方营地、成排小兵；左下角是**引擎自带的观察相机 HUD**：
```
当前正在冲锋 · 命令距今 3.3 秒
摄像机移动速度: 37.17 米/秒   ← 相机可驱动
现在摄像机: 302
上一个角色 / 下一个角色
```
⇒ **结论：我们的兜底相机在"没装 RTSCamera"的机器上确实可用**（不是纯粹的重复建设）。上一轮"重复建设"的判断只在**本机装了 RTSCamera** 的前提下成立 —— §二十七 2b 那条设计结论据此**收窄为条件成立**（措辞已在上文就地保留，不删历史）。

### 3. C 方案（幽灵相机）真机通过

`src/GhostCamera.cs` + `CommandPump` 的 `ghost_camera` + CLI `ghost` + MCP `bl_ghost_camera`（第 27 个工具）。

引擎依据（反编译源码，行号来自检索返回）：`MissionScreen.cs:224` `IsCheatGhostMode { get; set; }` 公开可写；`:3809-3811` 打开它即 `val5 = Free`；**`:3840` 幽灵模式下命令 UI 开着也保持自由**；`:3298` 相机输入由引擎自己处理；`:833` 官方自己就这么开。

真机（同一场围城内，**无需重启**）：
```
13:22:52.993 | ghost_camera: IsCheatGhostMode -> true（回读=true）
13:22:53.498 | ghost_camera: IsCheatGhostMode -> false（回读=false）
```
接口回读：`ghost status` = `inMission=true, ghostCamera=true`；`ghost off` 后回读 false。

**诚实边界（写清，免得被当成"全功能"）**：
1. 要能**自己用 WASD 飞**还需 `Game.Current.CheatMode`（`:2868` 把输入门控在它下面），它只读、最终来自 `engine_config.txt` 的 `cheat_mode`（默认 0）—— **我们不去改全局作弊开关**；
2. 它是**开发者通道**，行为未经 RTSCamera 那样的大量玩家验证；装了 RTSCamera 的机器优先用 B；
3. 不在 mission 里调用会明确返回 `not_in_mission`，**不静默假装成功**。

### 4. 通道复核（顺手验掉的两个疑点）

- **面板删干净后通道没坏**：`open_ui CustomBattle` → `requested=true` →（稍后）`activeState=CustomBattleState` → 开战守卫放行（`state=loading` accepted）。
- **`activeState` 出现过一次空串**：`open_ui` 触发后 ~7 s 内两次 `list_ui` 都读到 `""`，而同一场稍后（以及战斗结束后）都稳定读到 `CustomBattleState` ⇒ 那是"状态尚未落地"的**瞬时读数**（正落在 `InTransitionWindow` 想覆盖的时段），**不是回归**。若要更严，可把窗口内的 `activeState` 标成 `unknown(pending)` 而不是空串（本轮未改）。
- **MCP 加载路径（重要，省得以后白部署）**：`~\.codebuddy\mcp.json` 里 `blbridge` 指向 **仓库** `tools\bl_mcp.py`，**不是** `Modules\BlBridge\mcp\` ⇒ 改工具脚本后**只要重启 MCP**，无需部署；`Modules\BlBridge\mcp\` 那份是**发货副本**（由 `build.ps1 -Deploy` 从 `tools\*.py` 复制）。

### 5. 新增能力：无人值守 A/B 启动（`-ExcludeModules`）

`tools\bl_launch.ps1` 加 `[string[]]$ExcludeModules`（MCP `bl_launch_game` 暴露为 `excludeModules`）。存在理由：A/B 对照要"同一次启动、只差一个模块"，而"去启动器里取消勾选"既不可复现、无人值守时也点不到。

两条纪律（都来自本项目的老教训）：
- **拼错即中止**：名字不在模块表里 → `EXCLUDE FAILED: ...` + 列出已知模块 + `exit 1`（否则就是"静默 no-op"）；
- **兼容两种传参**：`-ExcludeModules A,B` 与 `-ExcludeModules "A,B"` 都接受。真机第一次就踩到 —— PS 把带引号的 `"A,B"` 绑成**一个** `[string[]]` 元素，被防呆逻辑当场拦下（**拦对了**，这是防呆的第一次实战）。

### 6. 本轮交付清单

| 面 | 内容 |
|---|---|
| C# | `src/GhostCamera.cs`（新）、`CommandPump.cs`（+`ghost_camera`）、`BridgeConfig.Version = 0.8.16` |
| CLI | `bl_cmd.py ghost status\|on\|off\|toggle` |
| MCP | `bl_ghost_camera`（第 27 个工具）、`bl_launch_game` 的 `excludeModules` |
| 脚本 | `bl_launch.ps1` 的 `-ExcludeModules` |
| 自测 | 257 条断言全过（工具数断言 26 → 27） |
| 部署 | `build.ps1 -Deploy` → `0.8.16`；`module\SubModule.xml -> v0.8.16`；进程内 `loadedSha256=3042dec51a4ed7ac` |
| 文档 | 本节 + README 工具表（27 个）+ §二十七 2c 待办收口 |

### 7. 仍未做（诚实留档）

- **发货副本落后**：`Modules\BlBridge\mcp\` 里的 `bl_mcp.py` / `bl_cmd.py` 是在部署**之后**才改的 ⇒ 关掉游戏后跑一次 `build.ps1 -Deploy` 即可（不影响本机 MCP）。
- **玩家自用的"免按键"上帝视角**：`bl_ghost_camera` 目前只有端口通道。用户自己手打时若没装 RTSCamera，得先跟 AI 说一声才能切；要真正"自己按键切"需给 `ghost_camera` 加一个热键绑定（未做）。
- **`MissionScreen` 的观察者相机 HUD**（"上一个角色/下一个角色"）来自引擎自带观察镜头，我们没接管任何 UI。

---

## [2026-09-25] §二十九 相机速度通道（v0.8.17）+ §二十七 2c 许可证更正 + 上游源码

### 1. 起因（用户三句话）

1. "上帝视角和俯视角怎么能进行命令和操纵" ⇒ 取证结论见第 2 条（**不是"能不能加功能"的问题，是"通道归谁"的问题**）；
2. "原版相机移动速度太慢了" ⇒ 本轮做掉，见第 3 条；
3. 给出 GitHub 链接 `github.com/lzh-mb-mod/RTSCamera`（并说明"这是最新的"）⇒ 连带把 §二十七 2c 那条**错误结论**更正掉，见第 5 条。

### 2. "上帝视角/俯视角下怎么下令与操纵"（取证结论，本轮不改代码）

| 问题 | 结论 | 依据 |
|---|---|---|
| 上帝视角下能下令吗 | **能，但那条通道是 RTSCamera 的**：按 X 进上帝视角 → 命令面板保持打开（`KeepOrderUIOpenInFreeCamera=true`）→ 鼠标点/拖下令 | 上游真源码；§二十八 第 1 条 |
| 那我们自己的幽灵相机呢 | 只切镜头（`MissionScreen.IsCheatGhostMode`）；**命令面板是另一套系统，我们一行都没碰**。`Mission.IsOrderMenuOpen` 是 public 字段（`Mission.cs:980`），但整个反编译索引树里**找不到它的写入点** ⇒ 开关它的代码在**未索引的 GauntletUI 程序集**里，"程序化开面板"必须先反编译取证 | 索引检索 |
| 俯视角（抬升相机）呢 | 它就是"跟随角色那台相机 + 本地偏移"，命令面板本来就能用；攻城默认不抬升（`ElevatedHeightInSiege=0`），用 `--rts-preset siege-god` 改 | §二十七 2b/2c |
| "操纵"（接管士兵） | 原版**只在主角已阵亡**时才允许接管友军，且禁英雄 / 禁残血(<25%) / 禁正在用器械（`Mission.CanTakeControlOfAgent`，`Mission.cs:6332`）；`TakeControlOfAgent` 只做"关快进 + `Controller = Player`"，**不换 `Mission.MainAgent`**。随时接管只有两条：RTSCamera 的 `ControlTroop`，或 cheat 键 `Ctrl+Alt+小键盘5`（`CheatsHotKeyCategory.cs:65`）。**装了 RTSCamera 会把 `CanTakeControlOfAgent` 恒置 false**，原版那条路被它永久关掉 | 反编译索引 |
| 我们要补什么 | 运行时下令：`Formation.SetMovementOrder`（我们已在用，`ScenarioRunner.cs:1425/1457/1600`）；接管：`Mission.MainAgent`(有 setter) + `Controller` + 关掉 `MissionMainAgentController`。**都还没做**，属候选 | — |

### 3. 相机速度：为什么慢，以及三条腿（本轮落地）

引擎自由相机的速度是**两个常量相乘**，本来就不是"配置项"：

| 量 | 默认 | 事实 |
|---|---|---|
| `_cameraSpeedMultiplier` | 1 | 基础速度 = `10f * 它 * (…)`（`MissionScreen.cs:1741`）；改它的热键（Ctrl+↑ ×1.5 / Ctrl+↓ ×2÷3 / Ctrl+中键重置 / Ctrl+滚轮）**被 `Game.Current.CheatMode` 门控**（`:1714`） |
| `_shiftSpeedMultiplier` | **3** | `:1749` `num6 *= (float)_shiftSpeedMultiplier` —— 这一句**不在**作弊门控里 ⇒ **不需要作弊模式** |
| `_cameraSpeed` 分量值域 | ±20 | `:1710-1712` 硬 clamp ⇒ 倍率加到某个量之后**实际位移可能不再变快**（**未取证**，见第 6 条） |

落地形态（`src/CameraSpeed.cs` + `CommandPump` 的 `camera_speed` + MCP `bl_camera_speed` + CLI `camera-speed`）：

| 腿 | 手段 | 需作弊模式 | 备注 |
|---|---|---|---|
| `shift` | 官方控制台函数 `mission.set_shift_camera_speed`（`MissionScreen.cs:797`），经 `CommandLineFunctionality.CallFunction`（`TaleWorlds.Library`，**public static**） | 否 | 零反射零 Harmony；**必须先自己调 `CollectCommandLineFunctions()` 填表**，否则 `found=false` |
| `base` | 反射写 `MissionScreen._cameraSpeedMultiplier` | 否 | ⚠️ 受上面那条 ±20 clamp 影响 |
| `rts` | 反射 `ACameraControllerManager.Get().Instance.MovementSpeedFactor`（RTSCamera 的 `ICameraController`） | 否 | **最有效**：它的 clamp 随 `cameraBasicSpeed` 一起缩放（上游真源码 `FlyCameraMissionView.cs:506/577-580`），没有固定天花板 |

三条腿统一"**写完回读**"，失败**点名是哪条腿**（`not_in_mission` / `leg_unavailable` / `bad_value`），不静默。

### 4. 离线验证（全部实跑，可复现）

| 项 | 结果 |
|---|---|
| `python tools\check_repo_encoding.py` | **合规**（76 个跟踪文本文件，UTF-8 无 BOM + LF） |
| `python tools\bl_selftest.py` | **全部通过**（工具数断言 27 → **28**；新增两条：`bl_camera_speed` 已注册、`mode` 枚举齐全） |
| `python tools\bl_metrics_selftest.py` | 全部通过 |
| `powershell -File tools\jsontest\build_and_run.ps1` | 全部通过 |
| `python tools\bl_check_clock_reset.py` | **FAIL 11 份 —— 这是预期的对照组那一侧**（全量跑时历史已知失败样本必须仍报 FAIL）。本轮没跑战斗，没有新产物，故无法给 `--since` 那一侧 |
| `build.ps1 -Deploy` | `0.8.17`；`module/SubModule.xml -> v0.8.17`；`module/mcp/manifest.json -> 0.8.17`；`mcp/` 26 个文件已同步；`dll sha256 = 13A8940CAD274368…` |

### 5. §二十七 2c 的许可证更正 + 上游源码（含可复现入口）

- 更正**就地写在 §二十七 2c**（保留原文、不删历史）。核心：**RTSCamera 系是 MIT**；
  依据是**本机安装包自己的 README**（`Modules\RTSCamera\README.html:1368` / `README.zh-CN.html:1366` → `github.com/lzh-mb-mod/RTSCamera`）。
- 上游仓实查：`lzh-mb-mod/RTSCamera` = **MIT**（`Copyright (c) 2020 Li Zhenhuan`）；
  `source/` 含 `RTSCamera` + **`RTSCamera.CommandSystem`** + `RTSCameraAgentComponent` + `library`（子模块 = MissionLibrary）+ `RTSCamera.sln`。
- **已 clone 一份只读源码**：`E:\Document\rts-camera-upstream`（HEAD `c292c0d8`，2026-09-15）。
  版本核对（这是"它到底对应我们哪个版本"的硬判据）：
  `source\RTSCamera\Modules\RTSCamera\SubModule.xml` = **`<Version value="v5.4.16"/>`** 且 `DependedModule Native v1.4.8`
  ⇒ 与本机安装的 v5.4.16 **同一版本**。tag 列表里对应 1.4.8 的最新就是 `release-v5.4.16-for-bannerlord-v1.4.8`；
  另存在 `release-v5.5.0-for-bannerlord-v1.5.1` —— 那是给游戏 **1.5.1** 的，与本机 1.4.8 无关。
- ⇒ **收益**：以后读 RTSCamera 用**原始 C# 源码**（常量、枚举、注释俱全），不必再用 `E:\Document\rts-decomp\` 的 ILSpy 产物；
  文档里凡"枚举含义靠推断"的地方（例：`ElevatedCameraTriggerMode` 的合法值）可以一次性坐实。

### 6. 仍待办（诚实留档）

1. ✅ **`bl_camera_speed` 的真机验证（必做）**：已于 2026-09-25 22:03–22:12 执行 —— **结果见第 7 条**（四条负对照 + 三条腿全部生效，写前读后一致）。
2. ⏳ **"真的更快吗"这一步还没做**：需要一次**排除 RTSCamera** 的启动（+ `spectate`）才读得到引擎那个速度读数，
   理由见第 7 条 C-1；本轮未做（要关游戏重开），**等用户裁定**。
3. `ElevatedCameraTriggerMode=Always` 那个"未证实枚举值"：现在有上游真源码了，应当去 `RTSCameraConfig.cs` 把它读死（本轮未做）。
4. 🆕 **修第 8 条那处失败文案**（一行改动），随下次部署一起走 + 真机复验。

### 7. 真机验证结果（2026-09-25 22:03–22:12，v0.8.17）

**启动与身份**：`bl_launch_game` 起游戏 → 进程 `Bannerlord.BLSE.Standalone` pid **60920**；
`bl_status` 回读 `assemblyVersion=0.8.17.0`、`mvid=dffe8eadbca34c79aafb8b083912f32d`、`loadedSha256=13a8940cad274368`
⇒ **跑的就是本轮部署的那份 DLL**（构建身份自证，不是"以为部署了"）。

**A. 四条负对照（主菜单，无 mission）—— 全部按设计报错，不静默**

| 调用 | 结果 | 这条在验什么 |
|---|---|---|
| `camera-speed`（status） | `ok:true, inMission:false`；三条腿都 `available:false` 且**各带自己的 why** | "失败要点名"这条纪律 |
| `camera-speed shift 20` | `ok:false, code=not_in_mission`，error = 引擎原话 `No Mission Available` | 引用了引擎的原话，不是自己编的 |
| `camera-speed shift 0` | `ok:false, code=bad_value`（"value 必须在 0.1 ~ 1000 之间，收到 0"） | 值校验**先于** mission 校验 |
| `camera-speed rts 5` | `ok:false, code=leg_unavailable`，why = "相机控制器尚未注册：RTSCamera 只在 mission 内把自己登记进去" | 说明反射**找到了类型**、只是实例为空 —— 能区分"没装 RTSCamera"与"装了但不在战斗里" |

**B. 战斗内（`battle_terrain_a`，40v40，`orders=charge`，22:07–22:10）—— 三条腿全部生效**

| 步骤 | 回读结果 |
|---|---|
| 初值 `status` | `inMission:true`；**`engineShift=3` / `engineBase=1` / `rtsCamera=1`** —— 与源码/文档里的默认值**逐个吻合**（对推导的独立验证） |
| `shift 20` | `readBack=20, changed=true` ⇒ 官方控制台函数路径（含先调 `CollectCommandLineFunctions()` 填表）真机可用 |
| `base 5` | `readBack=5, changed=true` ⇒ 反射字段名 `MissionScreen._cameraSpeedMultiplier` 在本版本仍正确 |
| `rts 5` | `readBack=5, changed=true` ⇒ `ACameraControllerManager.Get().Instance` 那条反射链通，且**AI 场次里 RTSCamera 也把自己登记了** |
| 复核 `status` | `20 / 5 / 5` —— 写进去的值确实留在引擎对象上，不是"发完就没了" |
| `ghost on`（顺手复验） | `ghostCamera:true` —— C 方案在 v0.8.17 里没被本次改动弄坏 |

**C. 本轮顺手查清的两件事（都属于"原来会误判"的）**

1. **引擎那个"摄像机移动速度"读数只在观察者相机下出现**：装了 RTSCamera 时，它的
   `Patch_MissionGauntletSpectatorControl` 会在**自由视角下隐藏 spectator HUD** ⇒ 幽灵相机里**看不到**那个数字。
   所以"倍率改了到底有没有更快"必须在**排除 RTSCamera 的启动**里读（`-ExcludeModules RTSCamera,RTSCamera.CommandSystem` + `spectate`），
   环境与 §二十八 第 2 条同款。⇒ 这也是第 6 条待办 2 的成因。
2. **我们自己的 runner 还跑不了真海战**：`Mission.IsNavalBattle => MissionTeamAIType == MissionTeamAITypeEnum.NavalBattle`（`Mission.cs:1379`），
   而 `ScenarioRunner` 只设 `FieldBattle`（`:1344`）或 `Siege`（`:1817-1825`）⇒ 拿海战场景名开战，`IsNavalBattle` 恒 false，
   等于"**在海上地图打野战**"。⇒ 本文档 §29 那份"海战里哪些功能被禁"的核对，**本轮无法用我们自己的 runner 复现**
   （要复现得走官方界面的海战入口，或给 runner 加 `NavalBattle` 分支）。

### 8. 真机暴露的一处文案缺陷（已定位，待随下次部署修）

主菜单下 `camera-speed shift 20` 的失败原因写成了 `读回值无法解析：No Mission Available` ——
真实语义是"**不在 mission 里**"，现有文案会让人以为是自己解析出了 bug。

修法（一行）：`src/CameraSpeed.cs` 的 `CliReadShift`，在 `"读回值无法解析"` 之前先判
`r.IndexOf("No Mission Available") >= 0` ⇒ 返回 `why = "当前不在 mission 里（引擎原话：No Mission Available）"`。

本轮**没有动源码**：游戏还在跑、DLL 被占用无法部署，改了就变成"源码 ≠ 已部署产物"
（`bl_build_check` 会如实报 `stale_source` —— 这条纪律比"改得早"重要）。

### 9. v0.8.18：启动拆两步 + 测速探针 + 第一轮真机 A/B（2026-09-25 22:20–23:0x）

**9.1 用户要求：启动拆两步**（原话："第一步启动 blse，第二步检测到游戏窗口打开开始屏幕快照…出现启动动画时虚拟按 esc…然后每 5s 判定一次是否进入到了自定义里"）
已落地：`bl_mcp._enter_custom_battle()`（`bl_launch_game` 默认在启动后自动走它；也单独暴露为 `bl_cmd.py enter-battle`）。
判据不猜：主菜单就绪 = 控制通道能应答 + `moduleLoaded=true` + `activeState==""` + `CustomBattle` 入口未禁用；随后 ESC 跳过场（**有上限 40 s，且只在未就绪时发**）→ `open_ui` → **每 5 秒**轮询 activeState。

真机时间线（两次都一样，可复现）：
```
t=0.4s   主菜单就绪（连发 ESC 0 次）
t=0.7s   open_ui(CustomBattle) → requested=True
t=+29.0s activeState=''            ← 空串其实是"还在落地"，不是主菜单
t=+34.3s activeState='CustomBattleState'   ← 34.7 s 才真进去
```
⇒ **这就是"从菜单开始很慢"的真身**：不是检索慢，是 `CustomBattleState` 本身要 ~30 s 才落地；旧流程在这 34 s 里必然吃 `wrong_state`（表现为"等上一次失败"）。

**9.2 新增测速探针**（`camera_speed` 的 `mode=probe` / CLI `camera-speed probe [--end]`）
两次调用夹住一段飞行，量 `MissionScreen.CombatCamera.Frame.origin` 的位移 ÷ 墙钟秒数。存在理由见 §7 的 C-1：引擎那个"摄像机移动速度"读数我们拿不到，只能自己量。

**9.3 第一轮真机 A/B（人按住 W，A/B/A2 各 6 s）—— 结果：`rts` 这条腿**没有**可测效果**

| 段 | 设置 | 位移 | 平均速度 |
|---|---|---|---|
| A | `rts=1` | 197.7 m / 6.30 s | **31.37 m/s** |
| B | `rts=5` | 221.6 m / 6.04 s | **36.67 m/s** |
| A2 | `rts=1`（对照） | 220.3 m / 6.32 s | **34.84 m/s** |

- 设置值本身没问题：三段 `readBack` 都等于请求值（1/5/1）。
- **但 5 倍系数只差出 ~5%**，而且 **A 与 A2 自己就差了 10%**（31.4 vs 34.8）⇒ 差异落在噪声里。
- 三个数字都挤在 **31~37 m/s**，恰好贴近**引擎**那条"每轴 ±20 的 clamp"的对角上限（20√3 ≈ 34.6）——
  这提示真正在驱动这台相机的可能仍是**引擎的自由相机分支**，而不是 RTSCamera 的 `UpdateFlyCamera`。
  （**未坐实**，下一步的对照就是去测 `base` / `shift` 两条腿：若它们能动、`rts` 不能动，则该假设成立。）
- 诚实结论：**"rts=5 更快"这件事本轮没有证据支持**；`MovementSpeedFactor` 的源码链路（`FlyCameraMissionView.cs:506`）读起来是对的，但真机看不到效果。

**9.4 本轮顺带发现的两个真 bug**

1. ✅ **已修** `bl_mcp._game_window_id()`：只认 `isGame`，而 gridhand 的 `windows list` **不返回该字段** ⇒ 恒返回 None（真机实测）。改为标题兜底识别。
2. ⏳ **未修** `tools/bl_launch.ps1`：它把**游戏主窗口当成模态对话框**反复发回车，最后误报 `LAUNCH FAILED (no game window within timeout)` —— 而进程（pid 66752）与窗口都在，游戏已到主菜单。
   证据：日志里连续 `dialog -> answering [Mount and Blade II Bannerlord - Singleplayer PID: 66752 …]`。
   修法方向：对话框只认窗口类 `#32770`，别按标题当对话框。

**9.5 工具能力边界（读 gridhand `--help` 得到，别再猜）**：`screenshot --window-id/--grid/--cell`、`windows list|raise`、`mouse click --cell`（**必须带 `--window-id`**）、`key type`、`key press <combo>`。
**没有 hold/时长参数** ⇒ "按住 W"这类持续输入**只能由人来做**；连发 `key press` 代替长按会引入大间隔（每次 gridhand 调用 ~0.4 s），实测会被惯性衰减吃掉。

### 10. v0.8.20：把 BUTR/Bannerlord.GABS 的四条做法抄进来（用户："全部执行开写吧"）

来源：读 `BUTR/Bannerlord.GABS` 的 `Tools/CoreTools.cs` / `MainThreadDispatcher.cs` / `docs/gauntlet-ui.md` 与 `pardeike/GABS` 的 `docs/GABP_BRIDGE_DEVELOPMENT.md`。
**它不是状态机库**（是 MCP 服务端 + 游戏侧 mod 工具集），且 `supported-game-versions.txt` 全文是 `v1.3.15 v1.3.13 v1.2.12` —— **不含 1.4.8** ⇒ **思路抄，包不装**。

| # | 抄的是什么 | 落地 | 真机结果 |
|---|---|---|---|
| 1 | **`wait_for_state` 的"Loading 屏二次校验"** | `bl_mcp._menu_state()` 现在要求 `topScreen` **非空**且不含 `Loading` | ✅ 见下 10.1 |
| 2 | **`core/skip_video`**（判 `VideoPlaybackState` → `OnVideoFinished()`，不模拟 ESC） | 新 `src/GameFlow.cs::HandleSkipVideo` + 命令泵 `skip_video` + MCP `bl_skip_video` + CLI `skip-video` | ✅ 已接进等主菜单的循环（优先于 ESC） |
| 3 | **`core/set_cheat_mode`**（反射私有 setter/后备字段 + 回读） | 新 `src/GameFlow.cs::HandleCheatMode` + `cheat_mode` 通道 + MCP `bl_cheat_mode` + CLI `cheat` | ✅ 已实现（判据：回读不符就 `ok:false` 并点名） |
| 4 | **`core/list_commands` 的"重复键"陷阱** | `CameraSpeed.EnsureCliCollected()` 先看 `AllFunctions` 里有没有数据，有就不再 `CollectCommandLineFunctions()` | ✅ 加固 |

工具数 28 → **30**（`bl_skip_video` / `bl_cheat_mode`）；`0.8.20` 已部署（`dll sha256 = 9C7A1F36DB28AC1F…`）；编码体检 / `bl_selftest`（含两条新断言）/ `bl_metrics_selftest` / jsontest 全过；`bl_check_clock_reset` 全量仍报历史样本 FAIL（预期对照组）。

**10.1 崩游戏那条路被证据钉死了**（v0.8.20 实测，游戏 pid 74456）：
- 启动后 **t=2.1 s**：`moduleLoaded=True, activeState='', topScreen='', options=9` —— **`topScreen` 是空串**，而 options 已经有 9 项、activeState 也是空串。
  ⇒ 旧的"就绪"判据（activeState 空 + 有 CustomBattle 入口）在这种时刻**会误判**，这正是 9.6 把游戏打崩的条件。
- 真主菜单：`topScreen='GauntletInitialScreen'`（非空）。
- ⇒ 新判据：**`topScreen` 非空 + 不含 `Loading`**（空串 = 屏幕栈还没压上来）。
- 截图核对：`ui\enter_battle_confirm.png` 就是主菜单（War Sails v1.2.8 / Bannerlord v1.4.8.119303）。

**10.2 安全门 + 全链路端到端（autoOpen 显式打开）**：
```
t=0.2s  list_ui: topScreen='GauntletInitialScreen' options=9
t=0.2s  主菜单就绪
t=0.5s  open_ui(CustomBattle) → requested=True
t=+20.7s list_ui 失败×1：no_response: 等待游戏响应超时（15s）…（**这条失败现在被记账了，不再静默**）
t=+30.7s activeState=''
t=+36.0s activeState='CustomBattleState'   ⇒ ok:true / in_custom_battle
```
⇒ 默认（`autoOpen=false`）仍然停在 `await_confirm` + 截图；这次是为了验证链路显式传了 `autoOpen=true`。

**10.3 仍待验证**：新判据的**负例**（启动初期 `topScreen==''` 时必须判"未就绪"）目前只用**观测**（t=2.1 s 的读数）证明过，还没在**一次全新启动**里跑完整条链（需要再起一次游戏，约 1 分钟）。
另：`bl_cheat_mode` / `bl_skip_video` 只有"实现 + 离线自测"，**尚未真机各打一次**（按 AGENTS.md 第一节，新控制通道参数必须真机过一遍）。

**10.4 真机验证结果（2026-09-25 22:55–22:58，两轮全新启动，游戏 pid 89100）**

| 项 | 结果 |
|---|---|
| **新判据的负例** | ✅ **第一次真正跑在启动窗口里**：`t=1.1s list_ui: moduleLoaded=True activeState='' topScreen='' options=9` → **没判就绪、没 fire open_ui**；`t=5.9s` 屏幕栈压上来后才"主菜单就绪"。⇒ 9.6 那次崩游戏的条件**已被判据挡住**（ESC 兜底 1 次，skip_video 0 次） |
| **`bl_cheat_mode`（正例）** | ✅ 主菜单：`nativeConfig=false, gameCurrent=null` → 游戏内 `cheat on`：**`nativeConfig=true, gameCurrent=true, changed=true`**，复核仍为 true ⇒ **`Game.Current.CheatMode` 确实跟着翻** ⇒ 引擎自由相机的 `Ctrl+↑/↓`、`Ctrl+中键` 倍率热键与观察者 HUD 的"摄像机移动速度"读数**已解锁** |
| **`bl_skip_video`（负例）** | ✅ 启动初期：`ok:false, code=not_in_game, error="Game.Current == null（游戏还没进入状态机阶段）"`（点名清楚、不静默） |
| **`bl_skip_video`（正例）** | ❌ **未证**：两轮启动都没抓到 `VideoPlaybackState` |
| **新发现（待办 #1）** | 主菜单与启动初期 **`Game.Current` 是 null**，而本实现走 `Game.Current.GameStateManager.ActiveState` ⇒ 开场动画那一段**可能根本够不到**（BUTR 用的是**静态** `GameStateManager.Current.ActiveState`）。修法：先试静态 `GameStateManager.Current`，拿不到再退回 `Game.Current.GameStateManager` |

**现场状态（留档，便于下次接手）**：游戏停在官方自定义战斗界面；**作弊模式已开**（进程内有效，重启回到 `engine_config.txt` 的设置）——要关：`bl_cheat_mode off` / `bl_cmd.py cheat off`。

**10.5 v0.8.21：`skip_video` 改走静态 `GameStateManager.Current`（正例 ✅，真机 23:00）**

- **修因**（10.4 的发现）：旧实现走 `Game.Current.GameStateManager.ActiveState`，而启动期/主菜单 **`Game.Current` 是 null** ⇒ 开场动画那一段**根本够不到**，功能在最需要它的时刻失效。
- **新实现**：先试 `TaleWorlds.Core.GameStateManager.Current`（**public static**，`GameStateManager.cs:53`），拿不到再退回 `Game.Current.GameStateManager`；返回里加 `via` 说明是哪条路拿到的。版本 `0.8.21` 已部署（`dll sha256 = 54C9043863832849…`）。
- **真机状态序列**（pid 91328，启动窗口内每 3 s 采样，`via` 全程 `GameStateManager.Current(static)`）：

| t | skip_video | 活动状态 | topScreen |
|---|---|---|---|
| 4.0 s | `not_video` | `''` | `''` |
| **7.9 s** | **`ok:true`** | **`VideoPlaybackState`** | `GauntletInitialScreen` |
| 11.3 s → 53 s | `not_video` | **`InitialState`** | `GauntletInitialScreen` |

⇒ ① **正例成立**：t=7.9 s 时活动状态确实是 `VideoPlaybackState`，`OnVideoFinished()` 调成功（开场动画被跳过）；② 静态那条路在 `Game.Current == null` 的时刻照样可用 —— 换路是必要的，不是风格问题。
⇒ 顺带把"从菜单开始很慢"的时间账算清了：动画 ~8 s 结束 → `InitialState`（主菜单）持续 ~35 s → `open_ui` 之后 `CustomBattleState` 还要 30+ s。

- ⚠️ **新发现（待办，刻意不夹带修）**：**主菜单的真实活动状态名是 `InitialState`**，而我们的 `ScenarioRunner.ActiveGameStateName()`（即 `list_ui.activeState`）在启动期与主菜单**一直返回空串** —— 因为它只看 `Game.Current.GameStateManager`，与 10.4 那处**同一个根因**。
  **不能只改一处**：`bl_mcp._menu_state()` 现在把 `activeState != ""` 判为"不在主菜单"，一旦 `activeState` 开始返回 `InitialState`，这条判据会**把真主菜单判成未就绪**。
  ⇒ 一次性方案：`ActiveGameStateName()` 改用静态 manager **且** `_menu_state()` 接受 `""` 或 `InitialState`（并继续优先信 `topScreen` 非空 + 无 `Loading`）。

**10.6 v0.8.22：状态名"静态优先"的连带修改 —— 一共**三处**，其中一处是**真机抓出来的****

- **C# 取值唯一化**：新增 `ScenarioRunner.ActiveStateManager()`（静态优先，已兜异常）⇒ `ActiveGameStateName()`、`skip_video`、`close_ui` 的 PopState 全走它。效果：`activeState` 在**主菜单报 `InitialState`**（以前是空串），启动期也**不再**是"空串"一种解释。
- **新增 `src/MainMenuStates.cs`**（只依赖 BCL）：`IsMenuLevel(name)` = `""`（取不到名字）或 `InitialState`（主菜单本体）——「主菜单层面」的**唯一实现**，供 C# 判据与**离线单测**共用（碰 TaleWorlds 的 `ScenarioRunner` 编不进离线单测，才单开这个文件）。
- **Python 镜像**：`bl_mcp._MAIN_MENU_ACTIVE_STATES = ("", "InitialState")` + `_menu_state()` 按这个集合判（不再"非空即不在主菜单"）。
- ⚠️ **第三处（改之前我没想到，真机当场抓到）**：`UiEntry` 的 **open_ui 闸门**原先写的是 `stateBefore.Length != 0`（"空串 = 主菜单"）。静态优先一改，真主菜单变成 `InitialState` ⇒ **`open_ui` 一律被拒**：
  `wrong_state_for_ui`，连 `Exit` 都点不出去（0.8.21 真机 23:07 实测，最后只能**强杀进程**才部署得下去）。修：改用 `MainMenuStates.IsMenuLevel(stateBefore)`；修后 `open_ui CustomBattle` → `requested=true`、`stateBefore=InitialState`。
  > 教训：**status 名换了来源，凡是对它做等值/空串判断的地方都要重新过一遍** —— 判据散在多处就是这个代价。
- **防再漂移的断言（两层）**：
  - 离线 C# 单测（`tools/jsontest`，已把 `MainMenuStates.cs` 纳入编译）：正例 `""`/`null`/`InitialState`，反例 `VideoPlaybackState`/`CustomBattleState`/`MapState`/`CampaignState`/`MissionState`；
  - `bl_selftest.py` ⑬：`_menu_state` 同套正/反例 **+ 跨语言同集合**（直接读 `src/MainMenuStates.cs` 断言常量写法、读 `src/UiEntry.cs` 断言闸门确实走 `MainMenuStates`）。
- **真机（0.8.22，dll sha `10E0F8BD84F20E6F`，23:10~23:14）**：
  启动序列 `''`/空屏 → **`VideoPlaybackState`**（skip_video 成功，`via=GameStateManager.Current(static)`）→ **`InitialState`**（门就绪，t≈15 s）→ `open_ui CustomBattle` `requested=true`（`stateBefore=InitialState`）→ **`CustomBattleState`/`CustomBattleScreen`**；`close_ui` + `open-ui Exit` 也恢复正常（0.8.21 时被自己的闸门拒）。
- 顺带修掉一个**编码体检的盲区**：新建的 4 个 `.cs`（`CameraSpeed`/`GameFlow`/`GhostCamera`/`MainMenuStates`）写盘时是 **CRLF**，而 `check_repo_encoding.py` 只查 **git 跟踪**的文件 ⇒ 没报；`bl_cmd.py buildcheck` 的 **`stale_source`**（改动 4 个）抓到了，归一为 LF 后**必须重建+重部署**。
  ⇒ 纪律补充：**新文件写盘后，要么 `git add` 进体检范围，要么单独验字节**（`$b=[IO.File]::ReadAllBytes(...); $b -contains 13`；注意 .NET 的相对路径按**进程启动目录**解析，要用绝对路径）。

**10.7 v0.8.23/0.8.24：`order` —— 第一个"战斗中途改令"通道（真机 ✅，含一个被真机抓出的自家坑）**

- 新增 `src/BattleOrders.cs`（协议层）+ `src/OrderSpec.cs`（**只依赖 BCL** 的名字表/校验器 ⇒ 可进离线单测）+ `CommandPump` 的 `order` 方法 + MCP 工具 `bl_order` + CLI `bl_cmd.py order`。
- 两条硬约束写进代码注释：① **必须在 mission 内**（mission 之外碰 `MovementOrder` 会抛 `TypeInitializationException` 并把该类型**永久**标记为不可用 ⇒ 直接拒 `no_mission`，不做"先试试看"）；② **连带 `SetControlledByAI(false,false)`**（`detachAI`，默认 true）。
- 判据：每条报 `orderBefore` / `orderAfter`（`MovementOrder.OrderEnum`）—— "生效没有"靠**当场回读**，不靠"我们调了 API"。未实现的参数（`arrangement`/`firing`/`target`/`position`）传了就报 `unsupported_param`（`Jmini` 是扁平读取器、枚举不了键，故"任意怪键"由 MCP 侧 `additionalProperties:false` 挡）。

**⚠️ 真机抓出的自家坑（这轮最有价值的一条）**：`order` 设完 stop、回读 `Charge→Stop` ✅，可 **12 秒后复读 `orderBefore` 又是 `Charge`**。
不是引擎战术，是**我们自己的 T13 周期重申**：组路径每 `OrderRefreshSeconds = 0.5s` 把各组 `spec.Movement` 重新下发（`ReapplyGroupOrders`，`ScenarioRunner.cs:1582` 起）。
⇒ 修法（v0.8.24）：`order` 同时改掉"待重申的那个值"（新增 `ScenarioProbe.OverridePendingMovement`，改 `spec.Movement`），并回传 `pendingSpecsUpdated` 让调用方看得见（**0 = 该方没走组路径 / 该编队上没有组** ⇒ 可能被 team 战术改回，如实写在 `note` 里）。
修后真机：t1 `Stop→Charge`（pend=1）→ 10~12 秒后复读 **`orderBefore = Charge`（留住了）**。

- **真机证据**（0.8.24，dll sha `0BB301D4AF2C8938`；判据来自 `bl_order` 返回值与 `logs/battles/*.jsonl` 的 `end` 事件）：
  - **行为证据（最关键）**：开战 DSL 双方都 `stop` ⇒ 基线 `aAlive=4 / dAlive=4` 且不掉人；中途把攻方改成 `charge` 后，本场以 **`reason = defenderWiped`（aAlive=4, dAlive=0, kills=4）** 结束 ⇒ **令真的改变了行为**，不只是改了回读字符串。
  - 全体编队（不给 `formation`）：守方 `Infantry#0 Stop→Charge (units=12)`，pend=1。
  - 目标编队上没有组（`Ranged`）：`Stop→Charge (units=0)`，**pend=0**，`note` 说明可能被 team 战术改回、隔几秒再读一次来判定。
  - `detachAI=false` 对照：`Stop→Advance`，8 秒后复读 **before 仍是 `Advance`**（pend=1 的同步与 detachAI 无关）。
  - 错误路径：mission 外 `no_mission`；`movement=jump` → `bad_movement`；`formation=Infantryy` → `bad_formation`；`arrangement=shieldwall` → `unsupported_param`；`side=nobody` → `bad_side`。
- **顺带修掉"两个错误互相遮蔽"的次序问题**：原先 `formation` 的语法校验排在 mission 门之后 ⇒ 没战斗时 `formation=Infantryy` 被报成 `no_mission`。v0.8.24 把纯语法校验提到 mission 门之前（真机确认：mission 外也报 `bad_formation`）。
- **断言**：`GuardTest` 覆盖 `OrderSpec`（名字表 / 下标 / 大小写 / 别名不收 / 越界 / 空串 + 与 `SquadSpec` **同集合**）；`bl_selftest` 覆盖 `bl_order` 往返（参数透传 + 回读带回 + 缺参在本地就拒）。离线单测 69 → **115** 项，Python 自测 257 → **281** 项断言，MCP 工具 30 → **31** 个。
- **当前现场**：游戏在跑 0.8.24（`buildcheck` 四段一致）；无战斗进行中（smoke 那场已 abort）。

**10.8 v0.8.25/0.8.26：`control_agent` —— 接管士兵（最小版）。能换主角色，但无真人场次里 `Controller` 回读仍是 AI（如实报 `controller_not_verified`，不假装成功）**

- 新增 `src/ControlAgent.cs` + `CommandPump` 的 `control_agent` + MCP `bl_control_agent` + CLI `bl_cmd.py control-agent`。
  做法出处：RTSCamera `ControlTroopLogic.SetToMainAgent` / `ForceControlAgent` + MissionLibrary `Utility.PlayerControlAgent` / `AIControlMainAgent`（MIT，只抄做法）。
- **五步**（缺一步就出问题，逐条有出处）：
  1) **老主角色交回 AI**：`Controller = AI` + `CommonAIComponent/HumanAIComponent.Initialize()` + `Formation.OnUnitAddedOrRemoved()`
     （RTSCamera 注释写明：同一编队里两个 `Controller == Player` 的 agent 会让**编队逻辑栈溢出**）；
  2) `Mission.MainAgent = 目标`；
  3) 目标 `Controller = Player` + `AIStateFlags = None` + 解除 agent/坐骑速度上限 + 摘 `VictoryComponent`；
  4) 复位 `MissionScreen._isPlayerAgentAdded`（反射，RTSCamera 同款做法；失败只降级 `screenReset=false`）；
  5) **当场回读**（`MainAgent` + `Controller`）。
- **选择器**：`agentIndex` > `troop` > `formation` > 该方第一个存活者；**默认与条件选择器都跳过当前 MainAgent**
  （v0.8.26 真机教训：自定义战斗里玩家方的 MainAgent 一直都在，第一个存活者往往就是它 ⇒ 默认选择会**稳定**撞
  `already_main_agent`，功能看上去像坏的）。只允许玩家方，敌方一律 `not_player_team`。
- **真机结果（AI 对 AI 自定义战斗，无真人；0.8.26 dll sha `BBA830CE5D43A7A4`）**：
  `Mission.MainAgent` 确实被换成目标（22 → 20 → 22，回读确认；游戏不崩、战斗继续 `12 v 12 running`），
  但 `Controller` **当场回读仍是 `AI`** ⇒ `ok=false` / `code=controller_not_verified`（**不再假装成功**）。
  引擎侧能对上的两处依据：
    * `Agent.Controller` 的 setter 会顺带 `Mission.MainAgent = this`（`Agent.cs:1199` 起）；
    * `MissionMainAgentController.Mission_OnMainAgentChanged` 在任何主角色变更时把 `_isPlayerAgentAdded` 置 **true**（`MissionMainAgentController.cs:229`）。
  ⇒ **这个通道在没有真人玩家的场次里做不到"真正接管"**（引擎把玩家方主角色保持 AI 驱动，`MissionScreen` 也没有 re-add 玩家 agent）
  ⇒ **要真接管必须在真人场次验证**（等用户自己开一场；这类"环境受限"的结论按纪律只能记成待验证，不能记成通过）。
- **错误路径真机全覆盖**：`no_mission`（主菜单）/ `not_player_team`（敌方目标）/ `no_target`（不存在的 agentIndex）/ `already_main_agent` /
  `no_original`（没 take 过就 release）/ `already_original` / `unsupported_param`（`mount` 等）/ `bad_mode`。
- **断言**：`bl_selftest` 新增 `bl_control_agent` 往返（take/release 参数透传 + 回读字段带回 + mode 非法**本地**就拒）；
  MCP 工具 31 → **32**；Python 断言 281 → **285**。
- **顺带修掉一个"体检漏检"（一天内两次踩到）**：`check_repo_encoding.py` 原先只查 `git ls-files`，
  于是**新建文件写盘时的 CRLF 直接漏过**（两次都是 `bl_cmd.py buildcheck` 的 `stale_source` 先抓到）。
  现在把「**未跟踪但没被 ignore**」的文件也算进来（检查面 76 → **83** 个文件）—— 体检该比 buildcheck 早一步。

**10.8.1 真人场次判定：✅ 接管成立（2026-09-25 23:55，用户自己开的战局）**

- 现场：用户在打**自己的**战局（`missionMode=2` = Battle，约 400 v 400），先按 RTSCamera 的 **E** 接管了"步兵领袖位"的英雄（`commander_1`，index **999**，hero）。
- **判据链（一次 take，全程只有这一次写操作）**：
  1. `status` → **`mainAgentIsPlayerController=true`**、`mainAgent={999, 阿科耳, controller=Player}`、`candidates=362`
     ⇒ 环境里**有真人在控制**（这一步同时否掉"`Controller` 读数是坏的 / 我们读错了"这个可能：同一读法在这里给的是 `Player`）；
  2. `take`（默认选择器，跳过当前 MainAgent）→ **`ok=true` / `mainAgentChanged=true` / `controllerAfter=Player` /
     `oldHandedToAI=true` / `screenReset=true`**，目标 `695 帝国资深步兵` 从 AI 变 Player；
  3. **+8 秒复查**：`plc=true`、`mainIndex=695`、`controller=Player` ⇒ **留得住**
     （对比 AI 场次里同一操作回读仍是 `AI`）；
  4. 用 `take --agent-index 999` 换回英雄 → `ok=true`、`controllerAfter=Player`（用户回到自己的角色，屏幕上无缝）。
- ⇒ **结论定稿**：AI 对 AI 场次里的 `controller_not_verified` **是环境导致**（该场没有真人 ⇒ 引擎不认 Player 控制器），
  本通道在**真人场次里成立**。判据始终是**回读**，不是"我们调了 setter"。
- ⚠️ 现场还证实了一个自家 bug（**已改、待部署**）：`release` 的"原始主角色"没按场次隔离
  —— 旧实现里 `_originalMainAgentIndex` 会**保留上一场的 22**，`release` 会把主角色交给一个无关的人。
  这一局因为用 `take --agent-index 999` 换回，**没有踩到**；但这也是"下标跨场重号"必须按场次隔离的直接证据。
- **待办**：① 用户打完这一局后部署 **0.8.27**（含跨场隔离修复；当前 `buildcheck` 会显示 `stale_source`，是如实状态）；
  ② 部署后用**真正的 `release`** 再验一次（这局用的是 take 换回）；③ 镜头跟随仍是"复位 `_isPlayerAgentAdded`"这一档
  —— RTSCamera 那套平滑推镜（`Utility.BeforeSetMainAgent`/`SmoothMoveToAgent`）没做。

**10.8.2 0.8.27 已部署 + 跨场隔离修复真机验证通过（2026-09-25 23:58 起，AI 场次即可验）**

- 版本 **0.8.27**，dll sha `700830769C6C577E`；`buildcheck` 四段一致；离线全绿（编码 83 文件 / selftest 285 项 0 NG / metrics / jsontest）。
- 判据链（一场全新 AI 战斗，12v12 双方 stop）：
  1. `status` → **`originalIndex = -1`** ⇒ **不再残留上一场的 22**（bug 消失，这是本次修复的核心断言）；
  2. 第一次 `take` → `originalIndex = **23**`（**本场**原始主角色）、`mainAgentChanged=true`；
  3. 再 `take` 另一个目标 → `originalIndex` **仍是 23**（不被改写）、`mainAgentAfter=20`；
  4. **真正的 `release`** → `restored = **23**`、`mainAgentAfter = 23` ⇒ **换回的是本场原始主角色，不是 22** ✓
     （`ok=false / controller_not_verified` 依旧 —— AI 场次没有真人，引擎不认 Player 控制器，如实报）。
- ⇒ "agent 下标跨场重号"这条坑闭环：**已修、已验**。真人场次下的 `release` 仍等用户下一局顺手验一次（预期同 ②③④ 的形态，只是 `ok=true`）。

**10.8.3 真人场次完整验证：`take` + **真正的 `release`** 双双通过（2026-09-26 00:13:51–00:14:00）**

- 手段：后台看守脚本（`live_release_test.log`），**只在 `plc==true`（引擎认为有玩家在控制）时才动手**，否则只读轮询。
- 判据链：
  - `00:13:21` `plc` 由 false → **true**：`mainAgent={999, 阿科耳, commander_1, hero, controller=Player}`、`candidates=400` ⇒ 环境确认；
  - ① `take`：**`ok=true`**、`controllerAfter=Player`、`oldHandedToAI=true`、`originalIndex=**999**`；
    `before=999(阿科耳)` → **`after=617(帝国资深步兵)`**，两者 controller 都是 `Player`；
  - ② **+8 秒**：`plc=true`、`mainAgent` 仍是 617、`controller=Player` ⇒ **留得住**；
  - ③ **`release`：`ok=true`、`mainAgentChanged=true`、`controllerAfter=Player`、
    `restored={999, 阿科耳, commander_1, controller=Player}`** ⇒ **换回的是原来那个英雄**（不是别的 index）
    —— 这正是 0.8.27"原始主角色按场次隔离"在真人场次的效果；
  - ④ 收尾：`plc` 随用户切窗口在 true/false 之间摆动（**失焦即暂停 ⇒ 引擎改报 AI**），与"暂停假说"一致。
- 附注：`00:18:43` 出现 `mainAgent=909 帝国重装骑兵 (Player)` —— 那是**用户自己按 E** 接管的（RTSCamera 的 ControlTroop），不是我们的脚本。
- ⇒ `bl_control_agent`（最小版）**闭环完成**：take / 8 秒留存 / release 三项真人验证全过；`release` 的响应里没有 `originalIndex` 字段（脚本读成 None），
  不是缺陷但值得下次顺手补上（便于与 `status` 对齐）。

**10.10 v0.8.28/0.8.29：`order` 扩到**编队阵列**与**射击纪律**（并抓出一个"静默失效"的自家 bug）**

- 引擎侧依据（读码确认，不是猜的）：
  * `Formation.SetArrangementOrder(ArrangementOrder)`（`Formation.cs:730`）/ `Formation.SetFiringOrder(FiringOrder)`（`:784`）；
  * 阵列共 **8** 档（`ArrangementOrder.cs` 的静态字段：Line / ShieldWall / Circle / Square / Skein / Column / Loose / Scatter）；
  * 射击纪律**只有 2 档**（`FiringOrder.cs` 的 `RangedWeaponUsageOrderEnum`：FireAtWill / HoldYourFire）——旧版那个"近距离才开火"已不存在；
  * 回读：`Formation.ArrangementOrder.OrderEnum` / `Formation.FiringOrder.OrderEnum`（两者都是 `public get; private set;`）。
- 接口变化：`movement` **不再是必填**，但 `movement` / `arrangement` / `firing` **至少要给一个**（都不给 ⇒ `bad_request`）；
  新增 `bad_arrangement` / `bad_firing`；`unsupported_param` 清单改为 `position` / `target` / `riding`。
  校验依旧全部走**只依赖 BCL 的 `OrderSpec`**，在碰那三个 struct（`MovementOrder` / `ArrangementOrder` / `FiringOrder`，静态字段会在 mission 之外把类型永久弄坏）之前完成。
- **⚠️ 抓到一个"静默失效"的 bug（靠回读才暴露）**：`MapFiring` 里写成 `firing == "holdFire"`，而入参在校验阶段已被 `ToLowerInvariant()`
  ⇒ 永远不成立 ⇒ **`holdFire` 被静默当成 `fireAtWill`**。真机表现：`firingBefore/After` 都是 `FireAtWill`（看起来"命令发了"其实没生效）。
  修法：改成 `string.Equals(..., StringComparison.OrdinalIgnoreCase)`。**这条正好证明"回读判据"的价值** —— 只看"我们调了 API"会把它算成成功。
- **真机验证（0.8.29，AI 场次，12v12 双方 stop）**：
  ① `arrangement=shieldwall + firing=holdFire`（不给 movement）：`arr: Line→ShieldWall`、`fire: FireAtWill→**HoldYourFire**`、`movement` 保持 Stop（没被碰）；
  ② +8 秒再问：**`firingBefore = HoldYourFire`**（留得住，不在组路径重申范围）；
  ③ 切回：`arr: ShieldWall→Line`、`fire: HoldYourFire→FireAtWill`；
  ④ 错误路径：`bad_arrangement`（wedge）/ `bad_firing`（holdFireUntilClose）/ `bad_request`（三者都不给）/ `unsupported_param`（position）。
- 数字：jsontest 115 → **121**；Python 断言 285 → **287**；`buildcheck` 四段一致（**0.8.29**，dll sha `D0DE79F9FAE34F7A`）。

**10.9 A/B：临时排除 RTSCamera 跑一遍（2026-09-26 00:05-00:08，用户要求"我们做的是不是 RTS 自带的"）**

- 手段：`tools/bl_launch.ps1 -ExcludeModules RTSCamera,RTSCamera.CommandSystem`（这条 v0.8.16 就留好了，正是为这种 "同一次启动、只差一个模块" 的对照）。
- 结果（同一台机、同一套 BlBridge 0.8.27，只差那两个模块）：

| 观测 | **无 RTSCamera** | **有 RTSCamera** |
|---|---|---|
| `camera_speed status` 的 `rtsCamera` 腿 | **available=false**，`why=没找到类型 MissionLibrary.Controller.Camera.ACameraControllerManager（本机应该没装 RTSCamera，或它的共享库没加载）` | **available=true** |
| `engineShift` / `engineBase` 腿 | true / true | true / true |
| `ghost_camera`（引擎自带 `IsCheatGhostMode`，C 方案） | ✅ `on/off` 都成功 | ✅ |
| `order` 中途改令 + 回读 | ✅ `Stop→Charge`，`pendingSpecsUpdated=1` | ✅（同） |
| `camera_speed shift 20` | ✅ `readBack=20`（引擎控制台函数，不依赖 RTS） | ✅ |
| AI 场次里的 `Mission.MainAgent` | **null**（本场根本没有主角色） | **index 23**（!!!） |

- **两条结论**：
  1. **我们的腿不依赖 RTSCamera**：Shift/基础倍率走引擎、幽灵相机走引擎自带开关、改令走 `Formation.SetMovementOrder`；
     而且 RTSCamera 不在时 `rts` 那条腿**如实报 `leg_unavailable` 并点名原因**（不静默、不假装成功）。
  2. **⚠️ 新发现**：AI 对 AI 场次里的 `Mission.MainAgent` **是 RTSCamera 给的**（无它时为 null）。
     ⇒ 之前那几轮 "AI 场次里 take 把 MainAgent 22→20→22" 操作的其实是 **RTSCamera 提供的那个主角色**；
     这条要写进后续任何 AI 场次结论里（换机器/关 RTS 时行为会不同）。
- 收尾：已恢复**正常启动（带 RTSCamera）**，游戏停在自定义战斗界面，`rtsCamera.available` 复查为 true。

**10.11 v0.8.30：`order` 加**指定点移动**（`position`）+ `release` 补 `originalIndex`（2026-09-26 17:0x–17:2x）**

- 起因：交接待办 ① 「`order --position`（指定点移动）—— 这才是点地面移动」与 ③（`release` 响应补 `originalIndex`，一行）。
- 引擎侧依据（读反编译确认，不是猜的）：
  * `MovementOrder.MovementOrderMove(WorldPosition)`（`MovementOrder.cs` 里 `new MovementOrder(MovementOrderEnum.Move, position)`）；
    `Move` 的 `OnApply` 会 `formation.SetPositioning(CreateNewOrderWorldPositionMT(...))`；
  * `WorldPosition` 必须带当前 `Scene`（`new WorldPosition(scene, vec3)`，`TaleWorlds.Engine.WorldPosition`），
    否则 `IsValid=false`、落点没意义；Z 有效性初始为 `Invalid`，`GetPosition` 内部会按地面/导航网格补 ⇒ **z 可以省略**；
  * 回读：`MovementOrder.GetPosition(Formation)`（引擎按导航网格算出的落点）+ `Formation.GetAveragePositionOfUnits(bool,bool)`（编队重心）。
- 安全边界照旧（这是本项目的硬规矩）：坐标解析写在**只依赖 BCL** 的 `OrderSpec.TryParsePosition`
  （`"x,y"` / `"x,y,z"`，各分量 `|值| ≤ 10000`，非数字 / NaN / Infinity / 分量数不是 2~3 / 越界 **一律拒**），
  在碰 `MovementOrder` / `WorldPosition` **之前**跑完。`movement` 与 `position` **互斥**（一个编队只能有一个 movement order）
  ⇒ `bad_request`；新增错误码 `bad_position`。
- **本轮唯一需要动引擎路径的地方（也是真机才会暴露的那个坑）**：组路径那 0.5 秒一次的重申**只认 movement 名字**，
  指定点移动没有名字可重申 ⇒ 不给它让路就会在**半秒内被我们自己**重申回 `Stop`/`Charge`（而 `Formation` 上当场看是 `Move`
  —— 只看"我调了 API"会把它算成功）。做法：
  * `SquadSpec` 增 `ManualMove + MoveX/MoveY/MoveZ`（仍 **BCL-only**，要能进离线单测）；
  * `ScenarioProbe.OverridePendingMoveToPosition` 打标；`ReapplySideOrders` 见到标记就**照原样重申那个点**；
  * `OverridePendingMovement`（movement 通道）**清掉**该标记 —— 否则改回 movement 后旧目标点会被反复重申。
- 新增两个回读字段：`moveTarget`（落点）与 `formationCenter`（编队重心，米）。后者是**为行为判据**加的：
  `orderAfter=Move` 只证明"令写进了那个 order 对象"，**不证明"兵在走"**；本项目 §3 要求行为证据 ⇒
  隔几秒再调一次，用两次重心差 + 与目标点距离就能判"在不在往那儿走"。
- **真机验证（2026-09-26，AI 场次 12v12，攻方按组 8 步兵 stop + 4 弓 stop，10 倍速）**：

| 判据 | 实测 |
|---|---|
| 下发 + 当场回读 | `order --position "60,60" --side attacker --formation Infantry`：`orderBefore=Stop → orderAfter=Move`、`moveTarget=(60.0,60.0)`（引擎原样收下请求的点）、`pendingSpecsUpdated=1` |
| 持久性（A/B 判据） | 隔 **12 秒**复读：`orderBefore` 仍是 `Move`（组路径 0.5 s 重申没把它改回 `Stop`）|
| **行为证据** | `--position "120,0"`：t0 重心 `(575.6,645.8)`，+20 秒 `(493.1,392.1)` ⇒ 与目标点距离 **790.3 m → 541.2 m**（近了 **249 m**）|
| 清标记 | 之后 `order stop --side attacker --formation Infantry`：+12 秒复读 `orderBefore=Stop`（旧的 `Move` 目标点**没有**被重申回来 ⇒ 标记确实清了）|
| 错误分支（**走原始通道**，绕过 Python 侧本地拦截）| `movement`+`position` → `bad_request`；`"a,b"`/`"1"`/`"1e9,0"` → `bad_position`；都不给 → `bad_request`；`target` → `unsupported_param` |
- **顺手修的一处文案缺陷（真机原始通道抓到的）**：`unsupported_param` 的消息还写着"本通道目前能做
  movement / arrangement / firing"（**不含 position**）⇒ 已改成 movement / position / arrangement / firing。
  MCP 侧 `bl_order` 的 `unsupported_param` hint 同样过期（写着"arrangement / firing / target / position 还没实现"）⇒ 一并改。
- 接口变化：`movement` 仍可省；`movement` / `position` / `arrangement` / `firing` **四者至少一个**；
  CLI 侧 `order` 的 movement 变成可选位置参数，新增 `--position "x,y[,z]"`（互斥与"都不给"在 Python 侧就拒，退出码 2）。
  `bl_control_agent` 的 `release` 补 `"originalIndex"` 结构化字段（此前只在 `note` 文案里，调用方做断言只能抠字符串）。
- 数字：jsontest **121 → 131**（新增 10 条 position 解析断言）；Python 断言 **287 → 290**；
  编码体检 **83 文件** ✅；`buildcheck` **四段一致**（**0.8.30**，最终 dll sha `EEA244BF86789195`）。
- 部署链（诚实记录，同一版本号部署过 3 次）：`A9BF4DEF`（position 三项回读）→ `BB4718B7`（加 `formationCenter`）
  → **`EEA244BF`（最终：文案修复 + 全套冒烟）**。
- 收尾：游戏停在**自定义战斗界面**（v0.8.30 进程内），计划任务 `BlBridgeDevLaunch` 已注销，临时探针脚本已删。

**10.12 v0.8.31：`order` 加**指定目标**（`target`：冲锋到敌方某编队）+ 空编队信号（2026-09-26 17:2x–17:4x）**

- 起因：交接待办 ②「`order --target`（指定目标）」。**口径**（本轮唯一需要拍板的地方，写明留给下一个会话）：
  **只做"编队目标"，不做 agent/实体目标**。依据：`MovementOrderChargeToTarget(Formation)` 绑的是编队对象，
  回读有硬判据（`MovementOrder.TargetFormation`，`OnApply` 里会 `SetTargetFormation`）；而
  `AttackEntity` / `Follow` 绑具体 agent，一死目标就失效，还得另定"怎么挑敌方单位"的口径
  ⇒ 先做能验的，agent 目标继续报 `unsupported_param`（不交半成品）。
- 接口：`target` = **敌方编队**名或下标 0~4（敌方 = 与 `side` 相对的那一方）。
  `movement` / `position` / `target` **三者互斥**（都往同一个 `Formation.SetMovementOrder` 写）。
  互斥检查做成"数一遍 + 列出冲突项"，**不是三条两两判断** —— v0.8.30 就是两两判，加 target 时才发现会漏配组合。
  新增错误码：`bad_target` / `no_enemy_team` / `no_target_formation` / `target_formation_empty`。
- 组路径"手动令优先"从**单标记**升级为**三态**（`SquadSpec.ManualKind`：0 按名字 / 1 指定点 / 2 指定目标）：
  一个编队只能有一个 movement order，用两个 bool 就可能同时为真 ⇒ 单值枚举式更诚实；
  `ManualNone` 必须是 **0**（= 字段默认值 ⇒ 开战 DSL 那条老路径行为不变，离线单测锁这条）。
  目标编队被打空后重申**跳过**并 `AppendOrderError`，**不**退回 `s.Movement`（那等于偷偷把"冲这个编队"换成"冲锋"）。
- **真机自己踩到的那个坑，顺手补成显式信号**：我把守方的 `formation=Infantry` 当成"那 12 个芬恩勇士"，
  其实**芬恩勇士是弓手**、在 `Ranged`，`Infantry` 是**空**的 ⇒ 令照样写进去、`ok=true`、**但没人执行**
  （只有 `count=0` 和 `formationCenter=(invalid)` 两个弱信号）。这正是本项目最忌讳的"看着成功其实没生效"⇒
  新增 `emptyFormations`（>0 时 applied 里对应条目带 `emptyFormation:true`）+ note 里点名
  "编队按**兵种**自动分：弓手在 Ranged、近战步兵在 Infantry、骑兵在 Cavalry…先核对 formation"。
- **真机验证（2026-09-26 17:3x–17:4x，AI 场次 12v12：攻方 8 军团兵 Infantry + 4 弓手 Ranged，守方 12 芬恩勇士 Ranged；10 倍速）**：

| 判据 | 实测 |
|---|---|
| 下发 + 回读 | `order --target Infantry --side defender --formation Ranged`：`orderBefore=Stop → orderAfter=ChargeToTarget`、`targetAfter=Infantry`、`target`/`targetSide=attacker`、`pendingSpecsUpdated=1` |
| 持久性 | 隔 **15 秒**复读 `orderBefore` 仍 `ChargeToTarget`（手动令优先生效）|
| **行为证据** | `targetDistance` **214.9 m → 114.5 m**（同一场 18 秒那轮是 212.7 → 102.5 m）|
| 清标记 | 之后 `order stop --side defender --formation Ranged`：`ChargeToTarget → Stop`，+16 秒复读仍是 `Stop`（旧目标没被重申回来）|
| 空编队信号 | `order stop --side defender --formation Infantry`（守方没步兵）⇒ `ok=true` + `count=0` + `emptyFormation=true` + `emptyFormations=1` + note 点名 |
| 错误分支（**原始通道**，绕开 Python 侧本地拦截）| 三者同给 → `bad_request`（列出三项）；`bogus` → `bad_target`；攻方目标指向守方**空**的 Infantry → `target_formation_empty`（点名"已经打光了"且不静默换冲锋）；`riding` → `unsupported_param` |
| position 回归 | 活编队上 `--position "400,400"`：`Stop → Move`，`moveTarget=(455.8,427.5)` —— **首次观测到引擎把落点夹到导航网格内**（请求值 ≠ 回读值是引擎修正，不是没写进去）|
- 数字：jsontest **131 → 134**（三态常量 + 默认值断言）；Python 断言 **290 → 295**；编码体检 **83 文件** ✅；
  `buildcheck` **四段一致**（**0.8.31**，最终 dll sha `AB936AE7D8DAD449`）。
- 部署链（诚实记录，同一版本号部署过 2 次）：`5D330F15`（target + 三态）→ **`AB936AE7`（最终：+ 空编队信号 + 全套冒烟）**。
- 收尾：游戏停在**自定义战斗界面**（v0.8.31 进程内），计划任务已注销，临时探针脚本已删。

**10.13 v0.8.32：`order` 补齐 `riding`（上下马）与 `targetAgent`（攻击指定敌方单位）＋ 开战 DSL `formation` 死字段的显式信号 ＋ RTSCamera 平滑推镜（2026-09-26 18:3x–19:5x）**

- 起因：交接待办四项一次做完 —— ①`order --riding` ②开战 DSL `formation` 死字段 ③`target` 的 agent/实体目标 ④RTSCamera 平滑推镜。
- 引擎侧依据（反编译 + 上游真源码，不猜）：
  * `Formation.SetRidingOrder(RidingOrder)`（`Formation.cs:771`，内部有 `if (RidingOrder != order)` 守卫 ⇒ 值本来就相同时
    **什么都不做**，回读仍相等）；档位只有 **3** 个（`RidingOrder.cs:5-10`）：`Free` / `Mount` / `Dismount`；
    回读 `Formation.RidingOrder`（public get）。`RidingOrder` 与 `MovementOrder` 同类 —— 静态字段在类型初始化时构造。
  * `MovementOrder.MovementOrderAttackEntity(GameEntity, bool surroundEntity)`（`MovementOrder.cs:417`）；
    回读判据 = `MovementOrderEnum.AttackEntity` + `MovementOrder.TargetEntity`（`:83` 是 public 字段）；
    `Agent → GameEntity` 走 `Agent.AgentVisuals.GetEntity()`（`MBAgentVisuals.cs:46`）。
    原版同款用法：`OrderController.cs:1033`（`OrderType.AttackEntity`，`surround = !(missionObject is CastleGate)`）。
  * `FormationClassExtensions.FallbackClass()`（`FormationClass.cs:96-107`）是"实际编队家族"的折叠口径：
    `Ranged/Skirmisher→Ranged`、`Cavalry/HeavyCavalry→Cavalry`、`HorseArcher/LightCavalry→HorseArcher`、其余→`Infantry`。
  * RTSCamera 的平滑推镜三方法在 **`MissionSharedLibrary.Utilities.Utility`**（RTSCamera 附带的 `MissionLibrary.dll`，
    本机反编译核对）：`BeforeSetMainAgent(Agent):bool` / `AfterSetMainAgent(bool, MissionScreen, bool)`；
    且 `AfterSetMainAgent(should=false, …)` 那一支**正是**我们原本手写的 `_isPlayerAgentAdded = false`
    ⇒ v0.8.31 之前只做了"半条腿"（`should=true` 那一支的 `SmoothMoveToAgent` 没调）。
- 口径（本轮拍板，写给下一个会话）：
  * `targetAgent` 用 **agent 下标**（`Agent.Index`）指定，调用方从遥测 / `bl_control_agent status` 取 ——
    **不自己"挑最像的敌人"**（挑选口径一旦藏进工具里，实验就不可复现）；
  * `movement / position / target / targetAgent` **四者互斥**（都写同一个 `Formation.SetMovementOrder`）；
    `riding` 与它们**正交**（管"骑不骑"、不管"去哪"），**不参与**那条互斥；
  * 开战 DSL 的 `formation` 取「**保留字段 + 运行时比对 + 显式告警**」这一档（不删、也不改成硬拒 —— 后者属动开战通道、
    回归面更大，**留给用户再次拍板**）。
- **真机抓到的缺陷（本轮唯一一个，已修 + 已复验）**：`formationWarnings` **跨场残留** —— 清空语句原先写在
  `if (attackerGrouped || defenderGrouped)` 块内 ⇒ **不带 groups 的 start 不会重置**。
  受控实验：A 场（带 groups、场景名故意写错 ⇒ start 失败）→ B 场（不带 groups）⇒ **B 场响应里出现了 A 场的告警**。
  修法：清空移到 `Start()` 开头（Busy 短路之后，正在跑的那一场不该被一个被拒的请求清掉）；复验 B 场 ⇒ `formationWarnings: []`。
- 真机验证（0.8.32，AI 场次；未注明处均已通过）：

| 判据 | 实测 |
|---|---|
| `riding` 下发 + 回读 | `order --riding dismount --side defender --formation Infantry`：`ridingBefore=Free → ridingAfter=Dismount` |
| `riding` 持久性 | 隔 **12 秒**复读：`ridingBefore=Dismount`（既不在组路径重申范围，也没弹回 Free）|
| `riding` 往返 | `mount` ⇒ `Dismount→Mount`；`free` ⇒ `Mount→Free` |
| `riding` 错误分支（**原始通道**）| `ride` ⇒ `bad_riding`（消息点名三档）|
| `formationWarnings` 正向 | 弓手写 `Infantry` / 步兵写 `Ranged` ⇒ **2 条**，逐条点名"组号 + 兵种 + 实际编队" |
| `formationWarnings` 修复 | 不带 groups 的 start ⇒ **`[]`**（修复前会带出上一场的告警）|
| `targetAgent` 下发 + 回读 | `order --targetAgent 3 --side attacker --formation Infantry`：`orderBefore=Stop → orderAfter=AttackEntity`、`targetAgentTroop=battanian_fian_champion#3`、`targetEntitySet=true`、`targetAgentAlive=true` |
| `targetAgent` 持久性 | 隔 **10 秒**复读：`orderBefore=Charge`（组路径重申照常）但 **`orderAfter` 仍是 `AttackEntity`** ⇒ 手动令优先成立 |
| `targetAgent` **行为证据** | `targetDistance` **154.3 m → 11.9 m**（10 秒内）；同批 `count` 40 → 21（确实在打）|
| `targetAgent` 清标记 | `order stop` ⇒ `AttackEntity→Stop`；+12 秒复读仍是 `Stop`（旧目标没有被重申回来）|
| 四者互斥（原始通道）| `movement+targetAgent` / `position+targetAgent` / `target+targetAgent` ⇒ 都 `bad_request` 并列出冲突项 |
| `targetAgent` 负向 | 自己人 ⇒ `target_agent_not_enemy`；不存在下标 ⇒ `no_target_agent` |
| 空编队信号（回归）| `targetAgent` 打到空编队 ⇒ `emptyFormation:true` + `count=0`（老信号在新参数上照常生效）|
| `cameraFollow`（RTSCamera）| `applied=true`、`shouldSmooth=true`、`why=""`（上游两个方法**真的调到了**）；⚠️ `lastFollowed=(none)` —— AI 场次没有真人控制器 ⇒ 跟随目标没写进去，**真人场次才能验到** |

- 接口变化：`order` 新增 `riding`（`free/mount/dismount`）与 `targetAgent`（整数下标）；`UnsupportedParams` **清空**
  （已无"先占位、后实现"的参数，空表结构保留）；`bl_start_battle` 返回值新增 `formationWarnings`；
  `bl_control_agent` 返回值新增 `cameraFollow`（`applied` / `shouldSmooth` / `lastFollowed` / `shouldSmoothFlagNext` / `why`），
  并新增 `src/CameraFollow.cs`（反射调 RTSCamera，找不到就如实报 `why`，**不静默**、不改旧行为）。
  `targetAgent` 走 `SquadSpec.ManualKind` 第四态（`ManualAttackAgent = 3`，且 `ManualNone` 必须 = 0 的老断言仍锁着）；
  目标**会死** ⇒ 阵亡后重申**跳过**并记 order error，**不**退回 `s.Movement`。
- 数字：jsontest **134 → 146**；Python 断言 **295 → 306**；编码体检 **84 文件** ✅；
  `buildcheck` **四段一致**（**0.8.32**，最终 dll sha `30730ABA6761BAA9`）。
- 部署链（诚实记录，同一版本号部署过 2 次）：`3F279C73A4E557BE`（首版）→ **`30730ABA6761BAA9`（最终：formationWarnings 残留修复）**。
- 未核实 / 未处置：① `riding` 的**行为**变化（真下马 / 真上马）未逐步观测 —— 本轮只验"值写进去且留得住"，
  步兵编队给 `mount` 也不报错（引擎是否让他们去找马未记）；② `cameraFollow` 的 `lastFollowed` 在真人场次没验；
  ③ 开战 DSL 的 `formation` 仍是**仅回显**（只是不再静默），"硬拒 / 删字段"两档**留给用户拍板**；
  ④ 收尾：游戏留在**自定义战斗界面**（v0.8.32 进程内），计划任务 `BlBridgeDevLaunch` 已注销，临时探针脚本已删。

## [2026-09-27] §三十 MCP 工具「描述 / 诊断输出」误导性审计与修复（v0.8.34）

> 完整记录见 `docs/mcp-tool-desc-audit-2026-09-27.md`。这里只放结论与数字。

### 1. 起因
换用新的 MCP 调用方式（列工具 → 取描述 → **真的 call 一次**），顺带检验它会不会把 AI 带偏。
当场抓到 2 个"名不副实"的缺陷 —— 都不是文案问题，而是**输出看着正常、实际误导**。

### 2. 缺陷与修复
| # | 现象 | 真实情况 | 处置 |
|---|---|---|---|
| D1 | `bl_get_screen` / `bl_get_viewmodel_property` 列得出、一调就 `unknown tool` | 只加了 `TOOLS` 表 + 命名表，漏了 `call_tool()` 派发分支 | 补派发；新增 `tools/bl_check_dispatch.py`（C1 声明⇒派发 / C2 派发⇒声明 / C3 声明⇒分组，含 3 类注入故障自测）；`bl_selftest.py` ④ 加真派发判据 + 不存在工具名对照组 |
| D2 | `bl_status` 报 `verdict: running` / 游戏进程存活 | **pid 复用**：pid 6040 = `MSI_Central_Service.exe`，游戏没开 | 新增 `_pid_image_name` / `_pid_is_game`（验映像名 `bannerlord|mountandblade|taleworlds`），4 个调用点全换；诊断结果新增 `pidImageName` |

**D1 的负向实验（证明判据不恒绿）**：把 `if name == "bl_get_screen":` 改名 ⇒ 自测立刻 2 条 FAIL / EXIT=1，还原后回绿。
**D2 的真机判据（本机实测）**：`_pid_alive(6040)=True`（旧，误报 running）vs `_pid_is_game(6040)=False`（新）⇒ `verdict=clean_exit`，并给出 `pidImageName=MSI_Central_Service.exe`。

### 3. 规则升格（AGENTS.md §三）
- 新增工具 = **同步四处**（C# `Dispatch` 分支 / `TOOLS` 表 / `call_tool()` 派发分支 / `gabp_names.json`）。
- 新增「工具描述与诊断输出的硬规则」：描述要写清前置条件（游戏在跑？要哪版 DLL？哪个状态？）；
  出处/署名搬进 `docs/`；"有没有"不等于"是不是"；探测不出就返回未知，不冒充结论。
- 描述改法落地：`bl_get_screen` / `bl_get_viewmodel_property` 写明"游戏在跑 + 已部署 v0.8.34+ DLL"；
  `bl_status` 补上它还会返回构建一致性与会话诊断。

### 4. 数字
- Python 断言 **306 → 316**（`bl_selftest.py` 全绿 EXIT=0）。
- `bl_check_dispatch.py`：声明 34 / 派发 34 / 分组 34；`--selftest` 3 类故障全抓到。
- `bl_check_gabp_names.py`：C# 17 method ↔ 表 17 条；MCP 34 tool ↔ 表 34 条；8 类故障全抓到。
- 编码体检：96 个跟踪文本文件全合规（UTF-8 无 BOM + LF）。
- `build.ps1` 编译 OK → `out/BlBridge.dll`（0.8.34，169 KB，33 源文件）；**未部署**。

### 5. 仍待办
1. **真机判据未做**：v0.8.34 的 C# 侧（`UiInspector`）未部署、游戏当前也未运行 ⇒ 真机一行证据都没有。
   需要 `build.ps1 -Deploy` + 启游戏 + 在主菜单/自定义战斗界面各抓一次。
2. **MCP 服务进程仍跑旧代码**：需重启 blbridge 这个 MCP server，`bl_status` 才会带 `pidImageName`、
   `bl_get_screen` 才调得动。
3. 短名单 #2（`inventory` / `get_inventory`）未开工。
4. `~/.codebuddy/mcp.json` 里 blbridge 的 description 仍写"（20 工具）"（实际 core+config 组 22 个）—— 用户本机配置，未改。


### 6. 真机判据（2026-09-27 20:33–20:47，部署并跑到 v0.8.35，进程内 sha `720e350db84b76e4`）

> `bl_status.buildCheck = 四段一致（源码 = 构建 = 部署 = 进程内，0.8.35）`。完整记录见
> `docs/mcp-tool-desc-audit-2026-09-27.md` §7。为部署 DLL 强制关过一次游戏，之后重开（pid 40848）。

**`bl_get_screen`（只读读界面）**

| 场景 | screenType | 层 / dataSource | 按钮 |
|---|---|---|---|
| 主菜单 | `GauntletInitialScreen` | `MainMenu` / `InitialMenuVM` | 11 个（载入游戏…退出游戏 + 1 个 disabled 的 AnnouncementButton） |
| 自定义战斗界面（`open_ui` 后） | `CustomBattleScreen` | `CustomBattle` / `CustomBattleVM` | 16 个（开始 / 返回 / 随机 / 取消 / 完成 / 切换 + DropdownButton×2 + AddTroopButton×8） |

**`bl_get_viewmodel_property`（只读读 VM）**

| 用例 | 结果 |
|---|---|
| `CurrentLanguageString` @ MainMenu | `"简体中文"` |
| `MenuOptions` + `subProperties=NameText,IsDisabled` | `count=10`，10 项，`IsDisabled` 全 False |
| 层名写错 `NoSuchLayer` | `no_data_source`：层 NoSuchLayer 没有可读取的 ViewModel |

**真机新暴露 2 个问题（都是"会误导调用方"，已修/已标注）**

1. **按钮 id 不能当主键**：主菜单 11 个按钮只有 1 个有 id；自定义战斗界面 16 个里 `AddTroopButton` 重复 8 次。
   ⇒ 描述改成"优先用 text 定位，id 常空且不唯一"（写入实测数字）。引擎侧形态，未改行为（不偏离上游 GABS）。
2. **"属性不存在"与"值为 null"原来都返回 `ok:true / value:null`** ⇒ 调用方分不清写错属性名和真的空值。
   ⇒ v0.8.35 改为：不存在 = `ok:false` / `property_not_found` + **反射列出真实可用属性名**
   （实测：`属性 IsMultiplayer 在 InitialMenuVM 上不存在（可用的属性：Announcement, CurrentLanguageString, …）`）；
   子属性写错 = `missingSubProperties: ["NotAField"]`。
   改法：`TraversePropertyPath` → `TryTraversePropertyPath` + `SuggestProperties`。
   ⚠️ 这块**只有真机判据、没有离线单测**（`UiInspector` 依赖引擎类型，离线跑不起来）—— 以后改它必须重跑本节。

**pid 身份校验的对照（同一台机器、同一个字段）**

| 时刻 | pid | `pidImageName` | `verdict` |
|---|---|---|---|
| 审计前（游戏没开，pid 被复用） | 6040 | `MSI_Central_Service.exe` | `clean_exit` ✅（改前是 running ❌） |
| 真机（游戏真的在跑） | 40848 | `Bannerlord.BLSE.Standalone.exe` | `running` ✅ |

### 7. 本轮数字与状态
- 版本 **0.8.34 → 0.8.35**；部署 dll sha `720E350DB84B76E4`；旧 DLL 备份 `BlBridge.dll.bak_20260927_204125`。
- Python 断言 306 → **316**；`bl_check_dispatch.py` 34/34/34；`bl_check_gabp_names.py` 17/34 双向对齐；
  编码体检 97 文件全合规；`build.ps1 -Deploy` OK。
- 游戏当前**停在主菜单**（pid 40848）。短名单 #2（`inventory` / `get_inventory`）未开工。


### 8. 启动链路提速（2026-09-27 晚，用户指出"进菜单/进选兵界面检测慢"；对照上游 GABS 后收紧）

**上游对照**（读了 GABS 的 `CoreTools.cs` / `MenuTools.cs` / `launch-bannerlord.ps1`，已记入 lessons §五）：
它**没有**任何"快速进游戏"的魔法 —— 无启动下限、`wait_for_state` 用 **500ms** 轮询（钳 100–5000ms）
+ topScreen 不含 Loading 的二次验证（我们 v0.8.20 已抄）、`skip_video`（我们已抄）、
进界面与我们同一条路（`GetInitialStateOptions → DoAction` ≈ 我们的 `ExecuteInitialStateOptionWithId`）。
⇒ 差距不在游戏侧，在我们自己的等待策略。

**改了三处（全在 `tools/bl_mcp.py`，不动 C#）**

| # | 改动 | 依据 |
|---|---|---|
| 1 | **await_confirm 路径砍掉 45s 下限**：菜单就绪即返回。下限本来防的是"自动 fire open_ui"，而这条路径根本不 fire（只截图），等下限没有保护对象 | 实测：菜单 7.8s 就绪却拖到 45.0s 才返回（白等 35s） |
| 2 | auto_open 的安全下限 45s → **20s**（可配 `minStartupSec`）：45s 是 v0.8.18 定的，早于 v0.8.20 的主菜单硬判据（topScreen=GauntletInitialScreen + CustomBattle 入口 enabled，正是为那次启动期崩溃加的）；硬判据已在，下限退居兜底 | 真机 A/B ×2：20s fire open_ui 两次都没崩 |
| 3 | 轮询收紧：主菜单 2s→1s；落地确认 5s→**1s 且 list_ui 超时 15s→6s**（真机发现 open_ui 后 ~30s 官方状态在主线程加载、控制通道不应答，15s 超时 ×2 就吃掉 30s，"1s 轮询"名存实亡）；失败只记第 1 条 + 每 10 条报数；`bl_wait_for_state` 轮询 2s→1s | 同一轮 B 组时间线 |

**真机 A/B（同一台机器、同一天）**

| 路径 | 旧（v0.8.34 等价行为） | 新（本轮） |
|---|---|---|
| 默认（await_confirm） | 菜单 9.9s 就绪 → **45.0s** 才返回 | 菜单 7.8s 就绪 → **19.1s** 返回（含启动第一步窗口等待） |
| autoOpen 一路到底 | 45s 下限 + 5s 轮询 + 15s 超时 ≈ 80s 量级 | **60.2s** 进到 CustomBattleState（n=2，均不崩） |
| 落地确认 | 两次 no_response 各干等 15s | +7.7s 即记"加载期不应答"，+29.5s 见 CustomBattleState |

**没做的（诚实留档）**：CustomBattleState 本身 ~30s 才落地是**官方状态**的成本（open_ui 后主线程忙、
连控制通道都不应答，轮询再快也测不到更早）—— 这个没动，也动不了（除非 patch 官方加载，违背零依赖原则）。
另外上游 `launch-bannerlord.ps1` 有个值得抄的小技巧：启动前把 `engine_config.txt` 的
`safely_exited=0` 改回 1，从源头少一个 Safe Mode 弹窗（我们现在靠自动应答，没出过事，先记账不抄）。


## [2026-09-27] §三十一 L2 #2：inventory/get_inventory（v0.8.36，真机闭环）

> 同 §三十 的"脱壳抄"模板做短名单 #2 第一条。上游 = BUTR/Bannerlord.GABS `Tools/InventoryTools.cs`
> 的 `inventory/get_inventory`（本轮已读源码本体，记账见 lessons §四）。

### 1. 落地了什么（三处同步 + 必跑校验全绿）

| 层 | 新增 |
|---|---|
| 游戏侧 | `src/InventoryProbe.cs`（新文件）：`MainParty.ItemRoster` + `Hero.MainHero.Gold` 只读；`Campaign.Current == null` ⇒ 如实报 `no_campaign`；`limit` 默认 50 |
| 调度 | `CommandPump.Dispatch` 加 `get_inventory` 分支 |
| 构建 | `build.ps1` 引用清单加 `TaleWorlds.CampaignSystem.dll`（库存读取需要；34 源文件，172 KB） |
| MCP | `bl_get_inventory`（TOOLS + call_tool 派发 + core 组）；工具 34 → **35** |
| 命名表 | categories 加 `inventory`；methods 加 `inventory/get_inventory`（kind=read，**needs=campaign**——`bl_check_gabp_names.py` 的 NEEDS_ALLOWED 同步登记）；tools 加 `bl_get_inventory` |
| 自测 | 假游戏端 get_inventory 应答 + 真派发判据 + limit 透传判据；计数 34→35 |

编译即取证：`ItemRoster` 索引访问 / `IsEmpty` / `EquipmentElement.Item` / `Amount` /
`ItemObject.Name/StringId/ItemType/Value/Weight/Tier` 全部对 1.4.8 引用程序集可解析。
（实现注：roster 类型用 `var` + 索引访问，不写类型名 —— ItemRoster 的命名空间随版本搬过，少一个会断的编译点；
`MobileParty` 在 1.4.8 在 `TaleWorlds.CampaignSystem.Party`，首轮编译 CS0246 已修正。）

### 2. 真机判据（负向 + 正向都拿到）

| 用例 | 结果 |
|---|---|
| 主菜单调 `bl_get_inventory` | `ok=false / no_campaign`：「没有战役上下文 —— 主菜单 / 自定义战斗里读不了库存」✅ |
| `bl_list_ui` 主菜单入口 | 9 项：`CampaignResumeGame` / `ContinueCampaign` / `StoryModeNewGame` / `SandBoxNewGame` / `CustomBattle` / `Options` / `WarbandlordConfigMenu` / `Credits` / `Exit` |
| `open_ui(ContinueCampaign)` → 进战役 → `bl_get_inventory(limit=8)` | `ok=true`，**gold=43752**，`totalElements=58`，按 limit 返回 8 条真实物品（谷物 x44 / 巴旦尼亚马驹 x2 / 通用马 x6 / 驮马 x11 / 骡子 x10 / 大陆骑乘马 x5 / 小鱼 x18 / 奶酪 x5），name（中文）/ id / quantity / type / value / weight / tier（-1/Tier1/Tier2）逐项正确 ✅ |

过程小坑（记一笔）：进战役的等待判据第一版用"topScreen 不含 Loading/Initial"，被 `PreloadScreen` 骗过
（3.5s 就误判进图，拿到的还是 no_campaign）；改成等 `MapScreen` 才对。顺带实测：ContinueCampaign 从主菜单
到地图 ~165s（中间出现 `GauntletSaveLoadScreen` 一闪），比自定义战斗慢一个量级 —— 真机判据脚本以后都要按这个量级给 timeout。
另：本机战役地图 topScreen 是 `NavalMapScreen`（海战 mod 的地图替换），activeState 仍是 `MapState`。

### 3. 状态与待办

- 版本 **0.8.35 → 0.8.36**；部署 dll sha `8271A370B9C52092`；旧 DLL 备份 `BlBridge.dll.bak_20260927_211652`。
- 验收：`bl_check_gabp_names.py`（7 判据 + 8 类故障注入）✅、`bl_check_dispatch.py` 35/35/35 ✅、
  `bl_selftest.py` 全绿 ✅、编码体检 98 文件 ✅。
- 游戏当前**停在用户战役地图**（读取操作，无任何写行为）。
- 短名单后续：menu / barter / conversation / party（上游对应实现体已定位，逐条照此模板 + 单独真机判据）。
  ⚠️ 这些也全是 needs=campaign —— 每条都要过一遍"进战役"这道慢门（~165s），考虑把"ContinueCampaign 进战役"
  固化成 `bl_launch_game` 的 `uiId` 路径（open_ui 已天然支持任意入口 id）。


### 4. §三十一补：存档工具端到端真机 + 弹窗取证 + 一处被用户纠正的错误归因（2026-09-27 深夜）

**被用户纠正的归因（记下来防再犯）**：此前写"ContinueCampaign ~165s、比自定义战斗慢一个量级"是**错的**。
真因：读旧档弹"模组不匹配"确认框（这些模组已被移除：BattleSizeResized / RBMAILite…取消|是），
**没有人点** ⇒ 游戏停在 GauntletSaveLoadScreen 等人，被误当成加载慢。正常换档不比自定义战斗慢
（本轮实测见下）。教训：等待循环只盯 list_ui，把"被模态弹窗挡住"当成了"在加载"——
超时归因里该多一个"有弹窗没应答"的形态。

**新增（v0.8.36）：campaign/list_saves + campaign/load_save（脱壳抄上游 core/list_saves / core/load_save）**

- `MBSaveLoad.GetSaveFiles()` → name/isCorrupted/**meta 全键值**（meta 里 `Module_*` 键就是每个模组的启停
  状态 —— "模组不匹配"弹窗的数据源就在这，将来可在 load 前做本地预检）；`LoadSaveGameData(name)` +
  `MBGameManager.StartNewGame(new SandBoxGameManager(result))` **按名直载，不经过存档选择界面**。
- 引用新增：`TaleWorlds.SaveSystem.dll`（SaveGameFileInfo/LoadResult 在这，不在 SaveLoad.dll）+ Sandbox 模块的
  `SandBox.dll`。坑：`LoadResult` 在 `TaleWorlds.SaveSystem.Load` 子命名空间；`MobileParty` 在
  `TaleWorlds.CampaignSystem.Party`。
- MCP：`bl_list_saves` / `bl_load_save`（needs=main_menu；战役中拒 in_campaign；名字不存在 ⇒
  save_not_found 并列出可用档）。工具 35 → **37**。
- 自测抓到一个新误导：C# 把失败装在 `result.ok=false` 里而信封 ok=true ⇒ MCP 层曾把"没启动"报成
  "启动成功"，已修（看第二层）。

**读档期弹窗取证（复现路径 = 读旧档，用户指点）**

- 弹窗窗口 title=`no_active_wnd`、**没有 Win32 Button 子窗口** ⇒ 引擎自绘对话框，词表点按钮无效，
  **VK_RETURN（回车=确认=『是』）就是正确动作**；实测两次换旧档（saveauto1/saveauto2）都在回车后正常进图。
- `_answer_late_dialogs()`（bl_launch.ps1 -AnswerSec 独立应答模式 + bl_mcp 等待循环 15s 节流调用）已接进
  load_save 等待路径；词表另补了 是/Yes 并剥 `(&X)` 助记符（对照实验：WinForms 真弹窗，裸"是"匹配不上
  `是(&Y)`，会走默认键 —— 不能赌默认键）。
- `list_ui` 的 options 顺带取证：主菜单 9 项含 `ContinueCampaign`（继续战役）——open_ui 本来就能进任意入口。

**端到端真机判据（无人值守，全程无人工）**

| 步骤 | 结果 |
|---|---|
| 启动→主菜单 | 19.4s（await_confirm 即刻返回生效） |
| `bl_list_saves` | 4 个存档，meta 带 `Module_*` 全键 |
| `bl_load_save('saveauto1')`（模组不一致旧档）→ 弹窗自动应答 → MapScreen | **49.9s** |
| 启动→换档→进图全程 | **71.3s**；第二跑（saveauto2）**27.8s**（启动后）进图 |

**遗留**：① display_mode=0（用户的无边框设置）在 `engine_config.txt`，运行中改文件会被游戏覆盖 ⇒
让用户游戏内改或退出游戏后改，工具不碰；② "失焦不暂停"（用户想挂机）→ 战役侧时间保活，下一项做。


### 5. §三十一补：失焦保活 bl_campaign_time（用户挂机需求，A/B 对照真机通过）

需求原话：'AI 操纵游戏时切换窗口游戏不出暂停……一边玩单机一边打 lol'。
机制：战役地图失焦时引擎把 Campaign.Current.TimeControlMode 改成 Stop。
做法（每帧重申，与 fast_forward 同一先例）：CampaignProbe.Tick() 挂 OnApplicationTick，
保活开着时发现 Stop 就恢复到失焦前档位；对话/菜单中（CurrentMenuContext != null）**不干预**
（剧情强制暂停不能被破坏，这是安全阀）。

- MCP：l_campaign_time（mode=status/on/off；status 回 keepAwake/timeControlMode/restores；
  restores>0 = 发生过失焦暂停且被救回）。工具 37 → **38**。
- 真机 A/B 对照（最小化窗口 15s 制造失焦）：
  - A 组（off）：恢复后 	imeControlMode=Stop ✅（引擎确实失焦暂停）
  - B 组（on）：StoppablePlay 保持、restores=1 ✅（第一帧救回，后台期间一直保持）
- ⚠️ 语义：开着时**手动暂停也会被立刻解除**（这就是保活的本意）；对话/菜单暂停不干预。

## [2026-09-27] §三十二 失焦"暂停菜单"的真凶：原版 StopGameOnFocusLost（v0.8.38，真机闭环）

### 1. 需求收窄（用户原话纠正）
用户 2026-09-27 深夜："我要的功能已经做出来了，我只要我切换窗口不出这个暂停界面就好了，游戏时间暂停不用改。"
⇒ 需求 = **切窗口不弹暂停菜单**；时间档位/推进不用动。§三十一 的"时间保活"因此不是本题的解。

### 2. 根因（三层，全部有反编译源码 + 真机读数）
1. `MapScreen.OnFocusChangeOnGameWindow(false)`（SandBox.View 13964）：失焦且
   `BannerlordConfig.StopGameOnFocusLost` 为真 ⇒ 自动 `OnEscapeMenuToggled(true)` ⇒ **暂停菜单被打开**。
2. `OnEscapeMenuToggled(true)`（14885）里 `Game.Current.GameStateManager.RegisterActiveStateDisableRequest(this)`
   ⇒ **MapState 不再 Tick ⇒ 战役整体冻结**（campaignDays 一动不动就是这条的指纹）。
3. 这条路径**不改 TimeControlMode**（全程 StoppablePlay）⇒ §三十一 保活的判据
   （"看到 Stop 就恢复"）**结构上看不见它** ⇒ 这就是"保活开着还是被暂停"的真正原因。

### 3. 判据补全（v0.8.37 / v0.8.38：少一个都会误判）
- `campaignDays`（v0.8.37）= `CampaignTime.Now.ToDays`。**TimeControlMode 不为 Stop 不等于时间在走**：
  `Campaign.TickMapTime` 的 StoppablePlay 分支要 `!IsMainPartyWaiting` 才推进（Campaign.cs 848-853）。
- `pauseMenuOpen`（v0.8.38）= `ScreenManager.TopScreen as SandBox.View.Map.MapScreen` → `IsEscapeMenuOpened`。
  构建脚本为此显式加 `Modules\SandBox\bin\Win64_Shipping_Client\SandBox.View.dll` 引用
  （它不以 `TaleWorlds.` 开头，原来的 glob 抓不到）。

### 4. 真机 A/B（同一脚本 dayrate.py、同一台机、同一档 save001）
改 `StopGameOnFocusLost` 之前（每天数都是 26044.6791 = 冻结）：

| 步骤 | 前台 | timeControlMode | pauseMenuOpen |
|---|---|---|---|
| 有焦点 12s | 游戏 | StoppablePlay | False |
| **失焦 12s** | 别的前台窗口 | StoppablePlay | **True** ← 失焦把它打开了 |
| 有焦点 12s | 游戏 | StoppablePlay | **True** ← 切回来不会自动关 |
| **失焦 25s** | 别的前台窗口 | StoppablePlay | **True** |
| 有焦点 12s | 游戏 | StoppablePlay | **True** |

关掉之后（同脚本重跑）：

| 步骤 | 前台 | timeControlMode | pauseMenuOpen | campaignDays |
|---|---|---|---|---|
| 有焦点 12s | 游戏 | StoppablePlay | False | 26044.6791 |
| **失焦 12s** | 别的前台窗口 | StoppablePlay | **False** | 26044.6791 |
| 有焦点 12s | 游戏 | StoppablePlay | False | 26044.6791 |
| **失焦 25s** | 别的前台窗口 | StoppablePlay | **False** | → **26044.7046（在走）** |
| 有焦点 12s | 游戏 | StoppablePlay | False | 26044.7046 |

判据两边都验过：同一脚本改前 B 段 +21.8s 就 `True`，改后 4 次失焦全程 `False`。

### 5. 修法（零代码根治）
`C:\Users\<你>\Documents\Mount and Blade II Bannerlord\Configs\BannerlordConfig.txt`：
`StopGameOnFocusLost=False`（改前已备份 `BannerlordConfig.txt.bak_before_focus_20260927`）。
- 游戏内同一选项：「当游戏窗口失去焦点时停止当前游戏」（`str_options_type.StopGameOnFocusLost`）。
- ⚠️ 游戏退出时会按内存值重写该文件 ⇒ 运行中改无效，必须"退出游戏后改"或"游戏内改"。
- 关掉后原版不再于失焦时打开暂停菜单，并且**不依赖** BlBridge 的任何保活。

### 6. 本轮被纠正的两个错误归因（防再犯）
- "保活已解决时间层、只差战役层" ⇒ 错：当时判据里没有 pauseMenuOpen，暂停菜单在判据里是隐形的。
  教训与 §三十一补第 4 节同源：**判据不全时，"测不到"会被误读成"问题不存在"**。
- "最小化不触发、必须真人 alt-tab" ⇒ 错：失焦事件确实在投递（rgl_log 有
  `OnGameWindowFocusChange: False` + `TopScreen: NavalMapScreen`），当时只是判据看不见。

### 7. 状态与数字
- v0.8.38 已部署，dll sha256 `CB2F281175BF1B1D`；`build.ps1 -Deploy` 自动备份了旧 DLL。
- 自测全绿；`check_repo_encoding.py` 99 个跟踪文件合规。
- MCP 工具数不变（**38**）：本轮只给 `bl_campaign_time status` 加了 `campaignDays` / `pauseMenuOpen` 两个返回字段。
- `bl_campaign_time mode=on`（时间保活）当前**关闭**：本修复不依赖它，且它会把"手动暂停"也顶掉。

### 8. 遗留（用户明确不需要改，留档备查）
失焦时战役时间的推进速率明显低于前台（实测失焦 25s 涨 0.0255 天、91s 涨 0.0274 天；主队"等待中"时
StoppablePlay 本就不推进）。用户 2026-09-27 明确"游戏时间暂停不用改" ⇒ 不改。

## [2026-09-27] §三十三 按用户要求移除"时间保活"（v0.8.39，真机双向判据）

### 1. 用户要什么
"你把那个时间暂停的修改改回来昂 那个我不需要"（承接 §三十二 用户原话："我只要我切换窗口不出这个
暂停界面就好了，游戏时间暂停不用改"）⇒ 撤掉 BlBridge 对游戏时间档位的**任何写操作**，只留只读诊断。

### 2. 撤掉了什么
- `src/CampaignProbe.cs`：删除 `_keepAwake` / `_lastMode` / `_restores` 与 `Tick()`
  （§三十一 那套"每帧把 Stop 恢复回原档位"）。
- `src/SubModule.cs`：`OnApplicationTick` 不再调用 `CampaignProbe.Tick()`（其余命令泵/看门狗不动）。
- `campaign_time` 从 **write 变 read**：`CommandPump.cs` 注释、`bl_mcp.py` 描述+入参（enum 只剩 `status`）、
  `gabp_names.json`（kind=read）、`README.md` 工具表同步。
- 传 `mode=on/off` **显式失败**：MCP 层 code=`keep_awake_removed`；直接走控制通道由 C# 同样拒绝（不静默失效）。
- 返回字段去掉 `keepAwake` / `restores`，保留 `inCampaign` / `timeControlMode` / `inMenuContext` /
  `campaignDays`（v0.8.37）/ `pauseMenuOpen`（v0.8.38）。

### 3. 真机双向判据（v0.8.39 部署后）

| 入口 | 输入 | 结果 |
|---|---|---|
| MCP 层 | `status` | `{"ok":true,"inCampaign":true,"pauseMenuOpen":false,"timeControlMode":"Stop","inMenuContext":false,"campaignDays":26044.6791}` ✅ |
| MCP 层 | `mode=on` | `ok=false / code=keep_awake_removed` ✅ |
| MCP 层 | `mode=off` | 同上 ✅ |
| 直连控制通道 | `campaign_time {mode:on}` | 信封 ok=false、里层 code=`keep_awake_removed` ✅（绕过 MCP 层也被拒） |
| 直连控制通道 | `campaign_time {}` | 与 status 同形 ✅ |

### 4. 保留了什么 / 为什么
只读字段 `campaignDays` + `pauseMenuOpen` 保留：它们是 §三十二 定位"暂停菜单"的判据，删掉就会退回
"测不到 = 问题不存在"的老坑。若用户连只读诊断也要去掉，只需删 CampaignProbe 里那两行 JSON 拼接。

### 5. 状态
- v0.8.39 已部署，dll sha256 `6C05C6E101BE2A99`；旧 DLL 由 `build.ps1 -Deploy` 自动备份。
- 四道校验全绿：自测 / 命名表（7 条判据，`campaign/time` 已是 kind=read）/ 派发表 38=38=38 / 编码 99 文件合规。
- ⚠️ 与 §三十二 的关系：本次回退**没有触碰**治"切窗口弹暂停菜单"的那一项
  （`Configs\BannerlordConfig.txt` 的 `StopGameOnFocusLost=false`），那是用户真正要的修复，仍在生效。


---

## [2026-10-05] §三十四 EBT 拆件：装备修饰符遥测 + 战术档位 + 环境旋钮（v0.8.41）

> 起点不是"想加功能"，而是一次外部评估：`G:/mods/EnhancedBattleTest`（EBT v4.2.1，
> 上游 `github.com/lzh-mb-mod/EnhancedBattleTest` HEAD `20d27cb`）**能不能加强 BlBridge**。
> 结论：**不能当运行时后端**（EBT 整套开战链建在 campaign 上：`PartyBase`/`MapEvent`/临时 `MobileParty`/
> 英雄身份替换/`SaveGuard` 禁存档；而 BlBridge 建在 CustomBattle 无战役分支上），
> 但**能拆件**。本节点落地了其中三项**零 Harmony** 的部分。
> 完整评估（含逐条取证与证据强度）见工作区 `EBT-to-BlBridge-评估-2026-10-05.md`。

### 1. `equip` 事件 —— 先把 §十五 那个悬案结账

**要纠正的既有结论**：§十五 写「`armorBody` 是确定性的（3/3 逐场一致）⇒ **不是随机 modifier**」。
**这条推断不成立**：`AgentBuildData.AgentEquipmentSeed` 来自 `IAgentOriginBase.Seed` / `UniqueSeed`，
种子若每场确定，则由它抽出的随机 modifier **同样每场一致** ⇒ "3/3 一致"分辨不了
「没有 modifier」与「**种子确定的** modifier」，而后者是随 **agent 序号**漂移的隐藏变量。

**做法（零 Harmony）**：新增 `t="equip"` 事件，每个 agent 一条，落 `Agent.SpawnEquipment` 的逐槽
`{i, slot, item, mod, modName, modArmor, modDamage, modSpeed, modHitPoints}`。

- **为什么不塞进 `ai` 事件**：`ai` 的字段集合已被既有分析脚本按 key 消费，加 12 个槽位会把它撑成两种东西。
  新开一个事件类型 ⇒ 旧事件逐字节不变（GC2）。
- **判别判据**（拿到数据一眼可判，不用改代码）：同一 `troop`、同一场、**不同 agent 的 `mod` 是否各不相同**。
  不同 ⇒ 随机 modifier 成立；全 `""` ⇒ 该路径确实不加，换装路线可继续。
- **`modArmor` 就是能解释"同一件 XML 甲、运行时护甲值不同"的那个数**：反编译取证
  `ItemModifier.ModifyArmor(int armorValue) => Math.Max(armorValue + Armor, 1)`
  ⇒ **修饰符对护甲的贡献是纯加法**。`ItemModifier` 的 `Armor` 是 `int`（不是 float，别被名字骗）。
- 槽位上限用 `EquipmentIndex.NumEquipmentSetSlots`（= 12，反编译取证），只写有物品的槽。
- 数据源是 `Agent.SpawnEquipment`（入场静态装备），**不是** `Equipment`（`MissionEquipment`，带耐久/装弹）。

### 2. 战术档位：`attackerTacticLevel` / `defenderTacticLevel`

**引擎真身（反编译取证）**：`CustomBattleCombatant.GetTacticsSkillAmount()`
`= _characters.Max(h => h.GetSkillValue(DefaultSkills.Tactics))`；
`MissionCombatantsLogic.EarlyStart` 拿它**分 20 / 50 两档**决定给该方挂哪些 `TacticOption`：
`<20` 只有 `TacticCharge`；`>=20` 追加 `TacticFullScaleAttack`（守方另 `TacticDefensiveEngagement`/`TacticDefensiveLine`；
攻方另 `TacticRangedHarrassmentOffensive`）；`>=50` 再追加 `TacticFrontalCavalryCharge`
（守方另 `TacticDefensiveRing`/`TacticHoldChokePoint`；攻方另 `TacticCoordinatedRetreat`）。
⇒ 这就是 EBT README 所说"战术等级 0-20 / 20-50 / 50+"的**引擎真身**。

**为什么以前没发现这条维度**：`ScenarioProbe.ApplyCharge()` 会 `ClearTacticOptions()` 只留 `TacticCharge`
（v0.7.3 为消除"攻方冲、守方站"的方向偏差而做）⇒ 整条"战术随战术技能分档"被抹掉。
**所以：默认 `orders=charge` 时档位看不出效果，要看效果必须配 `orders=default`**（已写进工具描述与 README）。

**实现 `src/TacticsCombatant.cs`**：一个只读包装（`IBattleCombatant`），只在请求了档位时才包。
取证两条：① `CustomBattleCombatant` 非 sealed，但 `GetTacticsSkillAmount` 是 **virtual final**（子类无法覆盖）；
② `MissionCombatantsLogic` 的 4 个 ctor 参数**声明类型全是 `IBattleCombatant`**，全类**无下转型** ⇒ 包装不会 InvalidCast。
`CustomBattleMissionSpawnHandler` 仍拿**未包装**的 `CustomBattleCombatant`（它的参数类型是具体类）。

**档位域**：`-1`（不覆盖）或 `0..100`；越界**显式报 `bad_tactic_level`**（静默接受 999 会做出"看起来设了、其实等于 100"的实验）。
`status`/响应/`meta` 同时给**请求值**与**引擎原生值**（`aNative`/`dNative`），否则覆盖值无法与原生值对照。

### 3. 环境旋钮（只做**已核标量字段**）

| 参数 | 落到 | 未传时 |
|---|---|---|
| `terrain` | `MissionInitializerRecord.TerrainType` | **-1**（引擎按场景决定，= 改动前的值） |
| `randomTerrainSeed` | `RandomTerrainSeed` + **同时开** `NeedsRandomTerrain` | 0 / false |
| `aiFriendlyFireMultiplier` | `DamageToFriendsMultiplier`（默认 1f） | 不写 |
| `keepCorpses` | `DisableCorpseFadeOut` | 不写 |
| `sceneLevel` | 攻城 `sceneUpgradeLevel`（原本写死 3） | 3 |
| `timeOfDay` | 攻城 `timeOfDay`（原本写死 6f） | 6f |

- 地形名取 `TaleWorlds.Core.TerrainType` **全表 23 项**（小写）；非法名**显式报 `bad_terrain` + 候选清单**，
  不静默兜底成 plain。**`bl_common.TERRAINS` ↔ `src/BattleEnv.cs` 的 `TerrainTable` 由自测 ⑭ 双向对账**（防两表漂移）。
- ⚠️ **攻城路径不支持前四项**（走官方 `OpenSiegeMissionWithDeployment`，它自建 record）⇒
  传了会被**显式忽略**并进 `envNotes`（响应与 `status` 都有）。**不静默是刻意的** ——
  "参数写了没生效"是本项目最贵的一类坑（2026-09-24 曾因此废掉 9 场实验）。
- ⚠️ **时刻/天气/雾没做**：`AtmosphereInfo` 是 `TaleWorlds.Library` 的 ValueType，含
  Sun/Rain/Snow/Ambient/Fog/Sky/Nautical/Time/Area/PostPro **十个子结构 + `IsValid`**；
  手搓一份要把子结构全部核清，核错就是黑屏或原生崩溃（EBT 是自己写了 100 行 `AtmosphereModel` 才做到的）。
  ⇒ 留待专门取证，**本轮不猜**。
- `IntOrQuoted()`：新参数**同时接受**裸数字与带引号字符串 —— 与 `Jmini.Bool` 收两种形态同源
  （v0.8.14 真机踩到 `spectate` 被静默丢弃）。**不动 `Jmini` 本体**，只给新参数用。

### 4. 顺带修掉的两处**既有红灯**（不是本次引入，但挡着绿灯）

1. `bl_selftest` 的 `len(tools) == 44`：源码 `TOOLS` 已是 **45**（v0.8.34 的 5 个 + v0.8.40 的 7 个 + 现状），
   断言没跟上；而生产配置 `BLBRIDGE_TOOLSET=core+config+lab` 下只有 41 个 ⇒ **这条判据两头都不符，等于谁也没在守**。
   ⇒ 改为**与源码 `TOOLS` 对账**（AST 取字面量，与 `bl_check_dispatch.py` 同一套做法），数字不再手工维护。
2. `bl_check_gabp_names` C1 缺 `bl_blockade`：补 `config/blockade`
   （离线只读计算 + `action=emit` 落盘 `map_blockades.xml`，读写靠 action 区分 ⇒ 用名词短语）。

另外：`bl_mcp.py` 的 `TOOLS` 里 **`terrain` 的说明必须是纯字面量** ——
`bl_check_dispatch.py` 用 `ast.literal_eval(TOOLS)` 做静态核对，任何表达式（`+` / `.join()`）
都会让它当场报 `malformed node or string`（**本次实测踩到并已修正**）。

### 5. 验证（全部离线可复现）

| 门 | 命令 | 结果 |
|---|---|---|
| 编译 | `build.ps1 -Deploy` | EXIT=0，**0 警告**，`out\BlBridge.dll` 194.5 KB；dll sha256 `C8896DD41E58E836…`；旧 DLL 备份 `BlBridge.dll.bak_20261005_035732` |
| C# 单测 | `tools\jsontest\build_and_run.ps1` | **全部通过**（仅既有的 `SquadSpec.cs` 三处 CS0649 警告） |
| Python 自测 | `tools\bl_selftest.py` | **全部通过**（含新增 ⑭：terrain/档位校验器 + C#↔Python 表对账） |
| 命名表 | `tools\bl_check_gabp_names.py` | 7 条判据全过（27 method / 45 tool / 12 category） |
| 派发表 | `tools\bl_check_dispatch.py` | 声明 45 / 派发 45 / 分组 45，全过 |
| 链条 | `bl_cmd.py buildcheck` | `code=game_offline`、`builtVersion=0.8.41`、`deployedSha256=c8896dd41e58e836`、文件链条一致 |

### 6. 待真机验证（**未做，不许写成已验证**）

1. **包 `TacticsCombatant` 后 `MissionCombatantsLogic` 是否真的接受** —— 静态取证（参数声明类型 + 全类无下转型）
   支持它安全，但**没在真机跑过**。一次 `start` 就能判：若抛 `InvalidCastException`，`Start` 的 try/catch
   会返回 `open_failed` + 异常名。**不传该参数时行为逐字节不变**（风险被 opt-in 框住）。
2. **档位 0 vs 60 是否真的分岔**：配 `orders=default` 跑两场，看 `AIStateFlags` / 接战时刻分布 /
   `order` 事件里的 tactic 名是否出现 `TacticFullScaleAttack` 等。
3. **`equip` 事件在真机是否落得下来**（`OnAgentBuild` 之后 `SpawnEquipment` 是否已填好）——
   当前从状态采样处触发（首见 agent 时），理论上已就绪，但**要一次真机确认**。
4. `terrain=snow` 是否真的改变可观测行为（移动/命中）—— **别直接断言，跑 A/B**。

### 7. 未做 / 明确不做

- **不装 EBT 当后端**：理由见开头四条的任一条；EBT 一开战**禁存档**，与"删模块即完全回退"的性质冲突。
- **不碰 `Mission.SpawnAgent` patch**：EBT 用它强制 `ItemModifier = null`，能彻底消掉装备随机，
  但会破 BlBridge「**不 patch 任何方法**」的设计承诺 ⇒ 这是项目级决策。
  **闸门**：先看 `equip` 事件的判别结果，**只有证明混杂源真的存在**才值得动这一刀。
- 攻城机械清单 / 破墙数：EBT 有，我们暂不加 —— 攻城当前仍是已知崩溃区（`SiegeTrace` 那条线），
  **先修崩再加旋钮**。

---

## [2026-10-05] §三十五 动作账本（v0.8.42）—— 学 Bannerlord.GameMaster 的 `CommandLogger`

### 0. 这一节的来源

§三十四 的评估里给 GameMaster 打了两条"值得学"，本节落地第一条：
**每条命令一行、追加写、可轮转的动作流水**。
两个**互相独立**的项目收敛到同一件事 ⇒ 这不是口味问题：
- `Bannerlord.GameMaster/Console/Common/Execution/CommandLogger.cs`：`Timestamp / Command / Status SUCCESS|FAILED / output`，
  落 `Documents\...\Configs\GameMaster\command_log_<ts>.txt`，**只留最近 5 个文件**（`MAX_LOG_FILES = 5`），
  缓冲队列 + 1 秒 Timer 刷盘 + `ForceFlush()`；异常**无条件**写 RGL、自定义文件按开关。
- `TAOM_CheatPanel.Runtime.RecentActionsLog` + `RecentActionEntry`：模组自己的动作流水，且**对用户可见**
  （字符串实证：`"Cheat settings changed for this session, but could not be saved. See Action History."`）。

### 1. 缺口（取证，不是感觉）

`CommandPump.HandleOne` 把 `id` / `method` / 错误码 / 异常全算出来了，然后：

```csharp
WriteResponse(id, response);   // → done/<id>.json（同 id 再来会 File.Delete 覆盖）
SafeDelete(path);              // → 把 pending 请求本身删掉
```

⇒ **一次请求处理完，磁盘上什么都不剩**。同 id 覆写、pending 被删、done 由外部消费。
"我到底下过什么命令 / 哪条被拒了 / 花了多久" 全靠 battle 日志反推。
另有 `Pump()` 的 `catch { }` **完全静默** —— 巡目录失败的表现只是"命令没反应"。

### 2. 实现

**新增 `src/ActionLedger.cs`**（约 190 行含注释），落 `<LogDir>\commands\actions.jsonl`，一行一请求：

| 字段 | 含义 |
|---|---|
| `t` / `seq` | UTC 时间戳 / **进程内单调序号**（判丢行与写者交错，比时间戳可靠） |
| `runToken` | 游戏进程会话（`Protocol.RunToken`）⇒ 账本能按会话分组 |
| `id` / `method` | 请求 id / 方法名（拒绝路径上 method 可能为空） |
| `ok` / `code` | 成功与否 / 失败码 |
| `ms` / `bytes` | 处理耗时 / 请求文件大小（读不到 -1） |
| `uncertain` | 协议里的 `outcomeUncertain`（true ⇒ 外部绝不盲目重试） |
| `note` | 成功时 `state=<状态>`；失败时错误消息（截断 300） |
| `args` | 请求参数片段（截断 600；账本是**索引**不是副本） |

**接线在 `CommandPump`**，关键取舍：**一个判定分支都不改**（GC2）。做法是在 `HandleOne` 末尾
用 `Jmini` **从已组装好的响应里反读** `ok` / `code` / `message` / `state`：

```csharp
bool ok = Jmini.Bool(response, "ok", false);
string code = ok ? "" : Jmini.Str(response, "code", "");
string note = ok ? ("state=" + Jmini.Str(response, "state", "")) : Jmini.Str(response, "message", "");
ActionLedger.Record(id, method, ok, code, ms, bytes, uncertain, note, ArgsSnippet(raw));
```

⇒ 拒绝路径（过大/id 非法/版本不符/过期/未知方法/处理器异常）**全都自动进账本**，不需要逐个分支加代码。
唯一的结构改动：把 `string method` 从内层 `else` 提到 `HandleOne` 顶部（原来是块内声明），
因为账本要把它带出来。

**两条"故意不抄"**（写清楚，免得下个会话以为漏了）：
1. **不抄缓冲队列**：GameMaster 用 `ConcurrentQueue` + 1s Timer，那是它控制台 UI 线程的高频写决定的。
   我们的写入上限是**泵的 4 Hz**（`PollInterval` = 250ms）⇒ 每条 `File.AppendAllText` 直接落盘，
   反而让"崩溃前最后几条请求"更容易保住 —— 那恰恰是账本最该保住的部分。
2. **轮转默认关闭**：`maxActionLogBytes` 默认 **0 = 不轮转**。GameMaster 是无条件只留 5 个文件；
   但 `battles/` 是**实验数据**（60+ 场历史要留着做对比），账本是**过程记录**。
   ⇒ 两者默认策略**故意相反**：battle 日志永不自动删（没实现），账本"你显式开了才删"。
   开了之后只留 `actionLogKeepFiles`（默认 5）个归档。

**异常第二出口**：外层 catch 与 `Pump()` 的静默 catch 都改成
`ActionLedger.ExceptionToRgl(...)` → 引擎的 `TaleWorlds.Library.Debug.Print`。
理由：走到那里说明连读文件/判大小都失败了，而那时 `<LogDir>` 很可能本身不可写
⇒ 自定义账本写不进去，`rgl_log_<pid>.txt` 是唯一还能留痕的地方。

**配置新增**：`maxActionLogBytes`（0 或 1024~1GiB；0~1024 之间**显式拒绝**，否则每几行轮转一次等于垃圾场）、
`actionLogKeepFiles`（1~100）。`EffectiveJson()` 同步加这两个键。

**读侧**：`bl_common.load_actions()` + `bl_cmd.py actions [--limit N] [--fail-only] [--json] [--path …]`。
`load_actions` 的口径**故意与 `load_events` 相反**：坏行**计数 + 带出原文**，不静默跳过 ——
v0.8.4 的非法 JSON 正是靠静默活下来的。

### 3. 顺带修掉的两个真问题

1. **`bl_common.default_log_dir()` 不读 `BLBRIDGE_LOG_DIR`**：`bl_mcp.py:69` 一直在读它，
   而 `bl_common` 不读 ⇒ "设了变量跑 MCP/自测"与"跑 CLI 子命令"看到**两个不同目录**。
   自测里本来就有几处 `os.environ["BLBRIDGE_LOG_DIR"] = ...`（合成战斗那段），
   在 `bl_common` 不读它的时候那些设置只对 MCP 生效。已统一到一处口径。
2. **`load_actions` 自己被抓到的崩溃**：自测 ⑮ 用合成的 `[1,2,3]` 行（**合法 JSON 但不是对象**）
   当场把 `ev.get()` 打成 `AttributeError` —— 一个"读审计日志"的动作变成崩溃。
   已加 `isinstance(ev, dict)` 守卫并计入坏行。**这条是测试抓出来的，不是想出来的。**

### 4. 验证（六道门全绿）

| 门 | 结果 |
|---|---|
| `build.ps1 -Deploy` | EXIT=0，**0 警告**，198 KB；dll sha256 `A3A9DCB578E93899…`；旧 DLL 备份 `BlBridge.dll.bak_20261005_041355` |
| `tools\jsontest\build_and_run.ps1` | **全部通过**（仅既有 `SquadSpec.cs` 三处 CS0649） |
| `bl_selftest.py` | **全部通过**（新增 ⑮：账本读侧口径 + **`src/ActionLedger.cs` 写侧 12 字段与读侧期望集双向对账**） |
| `bl_check_gabp_names.py` | 7 条判据全过（27 method / 45 tool / 12 category） |
| `bl_check_dispatch.py` | 声明 45 / 派发 45 / 分组 45 |
| `check_repo_encoding.py` | 106 文件合规（UTF-8 无 BOM + LF） |
| `bl_cmd.py buildcheck` | `builtVersion=0.8.42`、文件链条一致、`code=game_offline` |

### 5. 待真机验证（**未做**）

1. 重启游戏后，跑一条 `bl_cmd.py ping` / `status`，核对 `commands\actions.jsonl` 是否落行、
   字段是否齐全（`ok/code/ms/bytes/note`）。
2. 故意发一次**必然失败**的请求（如 `bl_cmd.py start --attacker nosuch_troop --defender nosuch_troop`），
   核对账本里出现 `ok=false` + `code=unknown_troop`，且 `ms`/`args` 合理。
3. `bl_cmd.py actions --fail-only` 与 `--limit` 的实际输出形状。
4. 配 `maxActionLogBytes` 后轮转是否发生、归档是否按 `actionLogKeepFiles` 裁剪（可离线造假文件验）。

### 6. 未做（下一步候选，仍是 §三十四 里列的）

- **响应信封构造器**（学 `CommandResult : ResultBase<>`）：把 `StatusJson` / `*Json()` 的手拼字符串
  收敛到一个构造器。本节点**没有动**它 —— 它是大范围重构，风险与收益都要单独评估。
  ⚠️ 但本次顺手暴露了一个更便宜的洞：**自测并不校验"每个响应都是合法 JSON"**。
  加这条断言成本极低，且正好能替 §三十四 里那个 P0 兜底。
- `EntityFinderResult` 式统一查找结果、`*Safety`/compat 垫片命名约定、发布流水 —— 均未动。

---

## [2026-10-05] §三十六 真机验证 T1–T5 + 修掉「错的客户端」+ 一处既有缺陷（v0.8.42）

> 执行者：dsh-agent。游戏 v1.4.8.119303，会话 pid 19416（`runToken=44230207dd02`），
> 完整报告：`C:\Users\LCGX\WorkBuddy\2026-10-05-03-35-59\T1-T5-真机验证报告-2026-10-05.md`。

### 1. ★ 先决修复：`bl_launch.ps1` 给的是「错的客户端」

**§三十五 §5 那 4 条待验之所以一直做不完，根因是启动器根本没加载对模块。**

`tools/bl_launch.ps1` 的模块清单是**硬编码**的，与用户 `Configs\LauncherData.xml` 的实际勾选失配 **6 项**：
缺 `RBM` / `RBM_WS` / `BattleSizeResized`；多出**磁盘上已不存在**的 `Warbandlord`
（`Modules\Warbandlord` 只剩 `config.xml`、无 `SubModule.xml` ⇒ 引擎**静默跳过**）。

**唯一可靠判据 = rgl 日志的 `Loading assembly:` 行**：改前连续 5 次启动**每次都只有 `BlBridge.dll`、
从未出现 `RBM.dll`**；官方启动器那次（pid 35852）载入了 `RBM.dll` 并存活 21 min。

**修复**：`$mods` 改为**从 `LauncherData.xml` 现场派生**；exe **保持 `Standalone.exe`**
（无须界面 ⇒ 保住无人值守）；另加「清单点名但磁盘上没有」的显式 WARNING。
**验证**：派生清单与官方那次 `Command Args` 经 `Compare-Object -SyncWindow 0` → **44/44 逐项同序零差异**；
修后 pid 19416 立即载入 `RBM.dll`。

**两条勘误**（都影响后续排查方向）：
- `BLSE_lasterror.log` 的 `Modules directory not found!` **不是病因**（mtime 早近一个月，且同批启动里
  `BlBridge.dll` 等都成功载入）。
- **不要改用 `Bannerlord.BLSE.Launcher.exe`** —— 那个 exe **就是官方启动器 UI 本身**
  （反编译 `Program.BLSE.Shared.Program.Main`：自己前缀 `args[0]="launcher"` 再转发），
  换来"清单对了但要人按 PLAY"，且合成点击到不了 Gauntlet。

> 已入知识库：`[[blse]]`（`E:\ObsidianDocument\entities\blse.md`）+ 快照
> `raw/transcripts/dsh-agent-blse-launch-modes-2026-10-05.md`。

### 2. T1–T5 结果

| # | 项 | 结果 |
|---|---|---|
| T1 | 账本失败路径 | ✅ `unknown_scene` 落账本（`FAIL start_battle unknown_scene 3.0014`）；`--fail-only`/`--limit` 实形核对通过；解析失败 0。⚠️ 配方**须补 `allowAnyState=true`**，否则先被 `wrong_state` 拦（官方入口落到 `NavalCustomBattleState`） |
| T2 | `equip` + §十五 | ⚠️ 事件落得下来（20 agent × 9 槽，`bodySeed` 逐人不同）；**180/180 槽 `mod` 全空**且 `modName`/`modArmor` 键**一个都没出现** ⇒ `ItemModifier` 真为 `null`（接线正确，`:700-708` 读的就是它）。**样本仅 1 场**，未定论 |
| T3① | 档位被引擎接受 | ✅ **通过**：`aRequested=60/dRequested=0`、`accepted`、**无 InvalidCast** ⇒ v0.8.41 唯一风险点解除 |
| T3② | 档位是否分岔 | ❌ **不足以区分**（A 组 n=4 均 336.0/sd 175.6 vs B 组 n=4 均 376.0/sd 241.9，**t=0.268**） |
| T4 | 环境旋钮 | ✅ `terrain=snow`/`terrainSeed=7`/`friendlyFire=0`/`keepCorpses` 全落 meta；⚠️ **行为效应未测** |
| T5 | 账本轮转 | ✅ **17/17**，且驱动的是**真实 `ActionLedger.cs`** |

### 3. ★ 本轮唯一真实缺陷发现：`order` 事件的 `tactic` 恒为 `"none"`

`ScenarioRunner.cs:2330` 写的是 `string tactic = "none";`，**此后从未重新赋值**，
第 2369 行原样写进事件 ⇒ **所有 `order` 事件的 `tactic` 字段无意义（占位）**。

另有一处**结构性不可达**（同一条判据的第二重障碍）：`order` 事件**只在 groups 路径写**
（`:2312-2314`），而 groups 路径的 `ApplyOrders` 无条件 `ClearTacticOptions()` +
`AddTacticOption(TacticCharge)`（`:1792-1793`）⇒ 档位效果被自己抹掉，
`order` 里**永不可能**出现 `TacticFullScaleAttack`。

⇒ 所以交接 §6 的 T3② 判据（"看 `order` 的 tactic 名"）**从一开始就测不出东西**，两个独立原因各自足够致命。
**这是既有缺陷（`order` 事件 v0.8.13 引入时就存在），非 v0.8.41/42 引入。**
`order` 的 movement order 字段不受影响。**未修**（修法见 §4）。

### 4. T5 的做法（值得照抄）与两条环境注记

**不重写轮转逻辑来测**（那验的是副本）—— 直接编译**真实的** `src/ActionLedger.cs` + `BridgeConfig.cs`
+ `BridgeProtocol.cs` + `BuildInfo.cs` + `JsonlWriter.cs` + `Jmini.cs` + `BridgeConfigFile.cs`
（全部纯 BCL），只把 `BridgeConfig._logDir` 这个**缓存字段**用反射指向临时目录。
harness：`E:\Document\t5-ledger-rotation\{LedgerRotationTest.cs, Shims.cs, build_and_run.ps1}`
（`Shims.cs` 只补 `TaleWorlds.Library.Debug.Print` 与 `CommandPump` 的两个路径助手，
轮转/裁剪/格式化**全走出货代码**）。

**环境注记**：① 本机**新编译的 `.exe` 会被拒绝执行并被 Defender 隔离**，而 `Assembly.LoadFrom` 加载 DLL 正常
⇒ harness 编译成 **DLL** 在进程内跑。② `BridgeConfigFile.Apply` 只在 **SubModule 加载时**读一次
（`SubModule.cs:54`）、**不热重载** ⇒ 真机改 `blbridge_game.json` 必须重启游戏。

### 5. 下一步（未做，按优先级）

1. **给 `order` 事件补真实 tactic 读取**（顺带清掉 §3 的占位缺陷）—— 这是解锁 T3② 的唯一路径。
2. **T4 的行为效应**：`terrain=snow` vs 默认 做 A/B，看移动/命中是否变（不要直接断言）；
   攻城路径另验 `envNotes` 应显式报出"前四项被忽略" + `sceneLevel`/`timeOfDay` 生效。
3. **T2 补样本**：不同兵种（含重甲）各 2–3 场，看 `mod` 是否仍然全空。
4. 计划任务 vs `Start-Process` 的**同源因果对照实验**（现象一致但未做对照）。
5. §三十五 §6 那条便宜的洞仍在：**自测不校验"每个响应都是合法 JSON"**，值得补。

---

## [2026-10-05] §三十七 T3②/T4/T2 补测（v0.8.43）：修两处既有缺陷，挖出「RBM 下档位是空操作」

> 执行者 dsh-agent。游戏 v1.4.8.119303，会话 pid 30164 / 36324。完整报告见工作区 `T1-T5-真机验证报告-2026-10-05.md` §8–§12。

### 1. 修掉两处**既有**缺陷（都为"能观测"服务）

| # | 缺陷 | 修法 |
|---|---|---|
| 1 | **`order` 事件的 `tactic` 恒为 `"none"`** —— `ScenarioRunner.cs` 的 `string tactic = "none";` 此后从未重新赋值，原样落盘 ⇒ 该字段是**占位**，交接里"看 order 的 tactic 名判断档位分岔"那条判据**永远测不出东西**（自 `order` 事件 v0.8.13 引入时就在） | 反射读 `TeamAIComponent._availableTactics` / `_currentTactic`（本仓库既有反射先例）。**三态可分辨**：`(unknown)` 读不到 / `(none)` 确实没有 / 真实名 |
| 2 | **攻城场次 meta 谎报未应用的 env 参数** —— 同一场里 `envNotes` 说"terrain 已被忽略"、`meta` 却写 `terrain:"snow"`，**两处对同一事实相反**（`BattleEnv` 状态在攻城路径未复位） | 攻城路径记 `envNotes` 的同时 `BattleEnv.Reset()`，meta 如实写 `""/-1/-1`。真机验证：回包 `env` 由 `{"terrain":"snow"}` → `null`；落盘 meta 由 `'snow'` → `''` |

另新增 **`tactics` 事件**（每 5s 每方一行，`requested` + `actual`）：因为 `orders=default` 且**不带 groups** 时
`order` 事件一条都不写（它只在 groups 路径产生，而 groups 路径会 `ClearTacticOptions()` 只留 `TacticCharge`）
⇒ 档位 A/B **整场没有任何观测出口**。触发条件限定为「请求过档位」（v0.8.41 才有的参数、默认 -1）
⇒ **GC2 实测成立**（不传档位时 0 条事件）。

**版本 v0.8.42 → v0.8.43**，已 build + deploy（`4209af2dda2ccd9a`），五道门全绿。

### 2. ★ 决定性发现：**装了 RBM 时，战术档位是空操作**（归属 RBM，非本仓库缺陷）

**换边自对照**（强于统计）：

| 场次 | 攻方 requested | 守方 requested | 攻方 `actual` |
|---|---|---|---|
| 实验 | **60** | 0 | `RBMTacticEmbolon`+`TacticFullScaleAttack`+`TacticCoordinatedRetreat` |
| **换边** | **0** | **60** | **逐字相同** |

攻方档位 60→0，战术集**一字未变**。

**根因（反编译确证）**：
- 引擎 `MissionCombatantsLogic.EarlyStart` **确实**按 `GetTacticsSkillAmount()` 分 20/50 两档 —— 而 `TacticsCombatant` 覆盖的正是这个值 ⇒ **纯原版下档位生效**。
- RBM `RBMAIPatcher.cs:26-28` 对**同一方法**挂**纯 Postfix**；该 Postfix（`Tactics/Lifecycle.cs:62-111`）**无条件 `team.ClearTacticOptions()`**，再**只按 `BasicCulture.StringId` + `team.Side`** 重建。
- 全 RBM 上游 grep `GetTacticsSkillAmount` → **零命中** ⇒ 档位这个自变量**没有任何代码路径会读它**。

⇒ 也解释了上一轮 **T3② t=0.268**：当时判"样本不足"只对了一半，更根本的是**自变量没接上**。
**RBM 侧已由项目负责人接手。**

### 3. T4 补测

- **T4a `terrain` 行为效应**：A/B 各 3 轮，**无可测差异**（胜负同为 58:0，命中/伤害/速度同分布）。
  **机制（唯一消费者）**：`SandboxAgentStatCalculateModel.cs:1556-1566` —— `Mission.TerrainType`
  **只决定队长是否吃到 `Tactics.ExtendedSkirmish`(Snow/Steppe) 或 `DecisiveBattle`(Plain/…)**，
  进而只改 `MaxSpeedMultiplier`。被测兵种不带这些 Perk ⇒ 分支恒不触发 ⇒ **本来就该没差异**。
  ⚠️ 所以 `terrain` **不是无效参数**，是"只在特定队长 Perk 组合下才影响数值"。
- **T4b/c 攻城 `envNotes`**：✅ 明确列出被忽略的四项 + 原因（官方 `OpenSiegeMissionWithDeployment` 自建 record）。
  ⚠️ **`sceneLevel`/`timeOfDay` 是否生效未验**。
- **⚠️ 一次不可复现崩溃**：旧构建跑攻城时 `0xC0000005`（`rgl_log_errors_14804.txt`）。**做了隔离**：
  同场景不传 env ✅ 正常；**传同一组 env 复跑也 ✅ 正常** ⇒ **不可复现**。
  是否与本轮改动有关**未定**，按纪律**不写成"已排除"**。

### 4. T2 补样本 —— 结论显著加强

**12 场 / 461 个 `equip` 事件 / 4149 个槽位 / 3 兵种 / 71 个不同 `bodySeed`**：
**非空 `mod` 仍为 0**，且 `modName`/`modArmor`/`modDamage`/`modSpeed`/`modHitPoints` 键**一次都没出现**
（`TelemetryBehavior.cs:709` 仅在 `mod != null` 时写）⇒ `EquipmentElement.ItemModifier` 恒为 `null`，**跨兵种成立**。
§三十六 §2 的样本量保留意见**已解除**。

### 5. 下一步（未做，按优先级）

1. **纯原版（`excludeModules` 排除 RBM）下复测档位** —— 坐实"引擎那条链本身是好的"。
2. **`sceneLevel` / `timeOfDay` 是否生效**（攻城路径）。
3. `randomTerrainSeed` / `aiFriendlyFireMultiplier` / `keepCorpses` 的**行为效应**（本轮只验落盘）。
4. 那次 `0xC0000005` 的定性（需更多复现尝试或更长跑批）。
5. 计划任务 vs `Start-Process` 的同源因果对照；§三十五 §6 的"响应 JSON 合法性"断言。

---

## [2026-10-05] §三十八 纯原版对照：档位确实生效 ⇒ 锁定 RBM 侧 + 一条影响面更大的方法论

> 执行者 dsh-agent。环境 `bl_launch_game(excludeModules=["RBM","RBM_WS"])`，pid 33364，v0.8.43。
> **零代码改动、零 patch**，纯取证。完整数据见工作区报告 §13–§17。

### 1. ★ 纯原版换边自对照：`attackerTacticLevel`/`defenderTacticLevel` **完全生效**

三场 `orders=default` 20v20（`imperial_legionary` vs `sturgian_spearman`）：

| 场次 | 攻 req | 守 req | 攻方实际战术集 | 守方实际战术集 |
|---|---|---|---|---|
| 对照 | -1 | -1 | *(0 条 `tactics` 事件 ⇒ GC2)* | — |
| 实验 | **60** | 0 | **5 个**：`Charge`+`FullScaleAttack`+`RangedHarrassmentOffensive`+`FrontalCavalryCharge`+`CoordinatedRetreat` | **仅 `TacticCharge`** |
| **换边** | **0** | **60** | **仅 `TacticCharge`** | **7 个**：`Charge`+`FullScaleAttack`+`DefensiveEngagement`+`DefensiveLine`+`FrontalCavalryCharge`+`DefensiveRing`+`HoldChokePoint` |

**与引擎 `MissionCombatantsLogic.EarlyStart` 的 `<20` / `>=20` / `>=50` 三档逻辑逐项吻合**
（`<20` 只有 Charge；`>=20` 追加 FullScaleAttack，攻方另加 RangedHarrassmentOffensive、
守方另加 DefensiveEngagement/DefensiveLine；`>=50` 追加 FrontalCavalryCharge，
攻方另加 CoordinatedRetreat、守方另加 DefensiveRing/HoldChokePoint）。

**攻方 60→0：战术集 5 个 → 1 个。守方 0→60：1 个 → 7 个。** ⇒ 换边自对照成立。

⇒ **结论**：纯原版下档位生效，`TacticsCombatant` 包装被引擎正常接受（无 InvalidCast）。
**与 §三十七 §2 的 RBM 环境形成干净对照 ⇒「档位在装了 RBM 后失效」的因果只在 RBM 的 Postfix**，
不必怀疑引擎那条链或本仓库的包装。**（这条是给 RBM 侧的硬对照。）**

顺带实证：`excludeModules` 回路本身可用 —— 42 模块（44−2）、`Command Args` 无 RBM/RBM_WS、
**RBM assembly 零载入**、`BlBridge.dll` 正常载入。

### 2. ⚠️ 新发现（**可复现**）：`orders=default` + 守方高防御档位 ⇒ **永不接战**

守方 req=60 那场跑到 **2700 游戏秒、0 命中、双方满血 20/20**；**复跑同样复现**（516 s 仍 0 命中，已 `bl_abort`）。

**它是"战术僵持"不是"卡死"**：两军最终相距 **271.3 m**（对照两场为 1.8 / 4.1 m），双方速度归零。

**隔离对照（唯一变量 `orders`）**：同配置换 `orders=charge` ⇒ **239 命中 / 83.5 s 打完 20:0**。

**根因**：`orders=default` 保留引擎战术菜单 ⇒ 守方选中
`DefensiveEngagement`/`DefensiveLine`/`DefensiveRing`/`HoldChokePoint`；
反编译这些类的 `Defend()`：**只挂防御行为**（`BehaviorDefend`/`BehaviorDefensiveRing`/
`BehaviorProtectFlank`/`BehaviorFireFromInfantryCover`），**没有任何"向前推进"**；
且 `GetTacticWeight()` 含 `CalculateNotEngagingTacticalAdvantage` / `IsDefenseApplicable`
⇒ **愈不接战愈优**。攻方仅原生 `TacticCharge`，**未接敌时不主动拉近 271 m** ⇒ 互等。
**原版战术设计使然（真实战役里守方本就在城/阵地上等），非本仓库缺陷。**

> ⚠️ **操作含义**：`orders=default` 是为"让档位可见"而用，但它**放弃了 v0.7.3 的对称化**
> （`orders=charge` 强制双方冲锋、消除"攻方进攻/守方原地"的偏差）。
> ⇒ **档位 A/B 只看胜负或命中数会被这个僵持污染**：换边那场 0 命中 **不是"档位无效"，
> 而是"两军没打起来"**。用档位做 A/B 必须**避开"`default` + 高防御档位"**，
> 或把"不接战"显式当成一个结果类别记录。

### 3. ★★ 方法论（**影响面大于 T3② 本身，务必往下传**）

> **RBM 在场时，任何"技能值驱动"的 AI 行为都被换成"文化驱动"。**
> **因此用 RBM 环境做的 AI 战术基准，测的是参战兵种的文化，不是技能。**

依据即 §三十七 §2 的反编译取证：RBM 的 `EarlyStart` Postfix **无条件 `ClearTacticOptions()`**，
再**只按 `BasicCulture.StringId` + `team.Side`** 挂自己的战术
（`empire`→`RBMTacticEmbolon`、`battania`→`RBMTacticAttackSplitArchers`、
`sturgia`/`nord`→`RBMTacticAttackSplitInfantry`…），
且全 RBM 上游 grep `GetTacticsSkillAmount` **零命中**。

⇒ **对过去所有在 RBM 环境下跑的"战术/AI"类基准都成立**：那些结论的自变量是**兵种文化**。
**这比"档位没接上"这一个自变量影响更大 —— 它关系到"我们以为在测什么"。**

> **操作建议**：涉及战术/AI 的 A/B，**要么在纯原版环境跑**
> （`excludeModules=["RBM","RBM_WS"]`，已验证零改动可行），
> **要么在结论里显式声明自变量是文化**。

### 4. 附带观察
`bl_open_ui(CustomBattle)` 在**纯原版**下落到 `CustomBattleState`；**带 RBM** 时落到
`NavalCustomBattleState`（即 §三十七 §6 那条"T1 必须补 `allowAnyState`"的成因）。

### 5. `overriddenBy` 标签 —— 把「档位没接上」变成一眼可读

**目的**：调用方不必靠统计猜"档位有没有生效"。**只读、不 patch、不干预**（不触碰"不 patch 任何方法"的承诺）。
实现在 `tactics` 事件上加 `overriddenBy` 字段（恒在；空串 = 未检测到覆盖）。

**⚠️ 第一版判据有假阴性（已修，教训值得记）**：原先用「可用集里是否出现 `RBMTactic*` 类名」判定，
真机**漏报守方** —— 守方 req=0 明显已被 RBM 覆盖（应只有 `TacticCharge`，实际 4 个），
但它那支**没有 `RBMTactic*` 类名**（RBM 的守卫分支按**文化条件**追加，`sturgian_spearman` 未命中）
⇒ **只看类名必漏**。

**现判据（不依赖文化知识）**：按引擎 `MissionCombatantsLogic.EarlyStart` 的 FieldBattle 三档逻辑
算出**应有集**，与观测集做**集合相等**比较；不等即判被覆盖。RBM 模块同时激活才写成因 `"RBM"`，
否则 `"(unknown)"`（两条独立证据能对上才写成因）。

**双向真机验证（完整对照组）**：

| 环境 | 方 | req | `overriddenBy` | 实际战术集 |
|---|---|---|---|---|
| 带 RBM | 攻 | 60 | **`RBM`** ✅ | `RBMTacticEmbolon`+… |
| 带 RBM | 守 | 0 | **`RBM`** ✅（修好后） | `DefensiveEngagement`+…（4 个） |
| 纯原版 | 攻 | 60 | **`''`** ✅ | 5 个，与应有集完全一致 |
| 纯原版 | 守 | 0 | **`''`** ✅ | 仅 `TacticCharge`，与应有集一致 |

⇒ 正例报 `RBM`、反例报空 ⇒ **不是恒真噪声**。

### 6. 顺带纠正一条**立论不成立**的"P0"（避免后人按错前提动手）

交接 §8 的 P0 之一写着「让 `Protocol.Failure` 自己入账（现在只在两处 catch 显式调）」。
**实测前提不成立**，账本**早已 100% 覆盖**：

| 判据 | 实测 |
|---|---|
| `ActionLedger.Record`（账本行）调用处 | **恰好 1 处**（`CommandPump.cs`），在 try/catch **之外**、**无条件**执行 |
| `HandleOne` 内有无提前 `return` | **0 处** ⇒ 无法跳过入账 |
| `HandleOne` 的 7 处 `Protocol.Failure` | 全是 `response = …` **赋值**，汇到那一处入账 |
| 各 handler 的 `return Protocol.Failure(…)` | 返回到 `Dispatch`，其返回值赋给 `response` ⇒ 照样入账 |
| 「两处 catch」实际调用的是 | `ActionLedger.ExceptionToRgl`（**RGL 第二出口**），**不是** ledger |

真机佐证：账本 203 行 / 解析失败 0 / `runToken` 分组正常；`done/` 里 **10896 个真实响应
用 `json.loads` 全通过**（ok=true 9269 / ok=false 1627）。

⇒ **"让失败自己入账"解决的是一个已经解决的问题。** 真正剩下的两个缺口是
**账本写失败静默**（磁盘满/权限错时少行而无人知）与**非法响应会让账本静默记错**
（坏 JSON ⇒ `Jmini.Bool(...,false)` 把成功记成失败），二者都在 §三十五 §6 的"响应 JSON 合法性"那条上，
**不该按原描述去加"构造即入账"**（那会让纯字符串构造器反向依赖 `CommandPump`，
并可能让同一请求记两行、破坏"每请求一行"的不变式）。

---

## [2026-10-05] §三十九 「自定义战斗界面」写死单状态名的连带缺陷（v0.8.44，三处 + 两条断言）

> 执行者 dsh-agent。起因：给 `bl_cmd.py enter-battle` 补 `--auto-open` 之后，真机验证**又**失败 ——
> 顺藤摸出一个**同一族缺陷在三处各有一份**的问题。全部已修并真机闭环。

### 1. 缺陷族：把「自定义战斗界面」写死成单个状态名 `CustomBattleState`

装了 **NavalDLC** 时，从主菜单 `open_ui(CustomBattle)` 落的是 **`NavalCustomBattleState`**
（官方把自定义战斗入口劫持到海战选兵界面）。而三处判据都只认前者：

| # | 位置 | 症状（真机实测） |
|---|---|---|
| 1 | `bl_mcp._enter_custom_battle` 的等待条件 | `--auto-open` fire 成功后**永远等不到**，140 s 超时报 `wait_custom_battle` |
| 2 | `ScenarioRunner.IsBattleSetupState`（开战守卫） | `bl_start_battle` 报 `wrong_state`，**要调用方手动传 `allowAnyState=true` 才绕过** —— 那是把缺陷推给调用方 |
| 3 | `UiEntry.HandleCloseUi` / `RequestClose`（出口） | `close_ui` 报 `not_open` ⇒ **进去了出不来**（而进去那扇门正是本模块开的） |

**为什么潜伏这么久**：`--auto-open` 与 `close_ui` 之前**没人从带 NavalDLC 的状态走过**；
而 ② 被 `allowAnyState` 这个"绕过开关"掩盖了（调用方以为那是正常用法）。
我先前手写跑批脚本用 `-match 'CustomBattleState'`（子串匹配）也**侥幸绕开**了 ①。

### 2. 修法：一族的后缀匹配，而不是放宽成任意状态

判据统一为 **`state.EndsWith("CustomBattleState")`**（C# 侧 `ScenarioRunner.IsBattleSetupState`
为唯一真相源，`UiEntry` 复用它；Python 侧 `_CUSTOM_BATTLE_STATE_SUFFIX`）：

- ✅ 接受 `CustomBattleState` 与 `NavalCustomBattleState`
- ❌ 仍拒绝 `MapState` / `CampaignState` / `InitialState` / `VideoPlaybackState` / `""` / `CustomBattleStateX`
  （它们**不以 `CustomBattleState` 结尾**）—— 集合外一律拒绝，不靠 catch 兜底

### 3. 顺带补的 CLI 能力对等

`bl_cmd.py enter-battle` **漏传 `auto_open`**（MCP 侧 `bl_launch_game` 早就支持 `autoOpen`）
⇒ CLI **永远**返回 `await_confirm`，无人值守在一步断链。已补 `--auto-open` / `--min-startup-sec`
（默认关闭，套 20 s 安全下限 —— 默认关是刻意的安全门，不是缺陷）。

### 4. ★ 两条新断言（⑰⑲），且**都做了注入验证**

**⑰** CLI↔MCP 参数对等：静态（源码级）+ **动态（走真 `bl_cmd.main()` 派发 + 探针）** +
**负对照（不传 `--auto-open` 时必须仍是 `False`）**。

**⑲** 落地判据后缀匹配：正例 2 个、反例 7 个，外加**用 AST 判"代码里不许再有写死比较"**。

> ⚠️ **⑲ 那条断言我改了四版才成立，是本节点最值得记的教训**（**"恒绿的断言 = 零守护"**）：
> ① 裸 substring → 把**注释里对旧写法的引用**判成缺陷（假阳性）；
> ② 只按 `#` 剥注释 → docstring 里也引用了旧写法，仍假阳性；
> ③ token 流用空格 join 后 substring → `==` 与字符串之间的空白形式对不上，
>    **注入真缺陷后仍然全绿**（恒绿！）；
> ④ 用 `tokenize` 剔 COMMENT+STRING → **把字符串字面量本身也剔掉了**，而
>    `"CustomBattleState"` 就是字符串 ⇒ 仍然恒绿。
> ⇒ 最终用 **AST 只看 `ast.Compare` 节点**（注释/docstring 天然不在其中）。
> **并且每一版都跑了注入验证**才判定它有效 —— 只有最后一版做到"注入 → 变红并点名 line 2005"。
> 这与 §三十八 的纪律一致：**没有对照组的验证不是验证**。

### 5. 真机闭环（**三条腿全部验过**，不是只跑单测）

| # | 项 | 结果 |
|---|---|---|
| ① | `bl_cmd.py enter-battle --auto-open` | ✅ `ok=true / phase=in_custom_battle / state=NavalCustomBattleState`（同命令修前 140 s 超时失败） |
| ② | `bl_start_battle` **不传 `allowAnyState`**，停在 `NavalCustomBattleState` | ✅ **`accepted:true`**（修前必报 `wrong_state`，要调用方绕过）；战斗正常打完 `defenderWiped` |
| ③ | `bl_cmd.py close-ui` | ✅ `ok=true / closeRequested=true`，**且真的回到主菜单**（`activeState=InitialState`、`topScreen=GauntletInitialScreen`） |
| — | 四段哈希链 | ✅ `builtVersion=0.8.44`、`deployedSha256 == loadedSha256 == f3f8319ef8b04b43` |

**六道门全绿**：encoding(106 文件) / jsontest / bl_selftest(新增⑰⑲) / gabp 7 判据 / dispatch 45=45=45 / buildcheck。

> ② 的对照最有力：**同一个调用、只少了那个"绕过开关"**，修前 `wrong_state`、修后 `accepted`。
> 这正是"把缺陷推给调用方"的典型形态 —— 调用方（我）先前还把 `allowAnyState=true`
> 当成正常用法写进了交接文档。

### 6. 下一步（未做）

- `allowAnyState` 这个开关**是否还需要保留**：修完 ② 之后它可能只剩"从**主菜单**直接开战"
  这一个合法用途（真机实测：主菜单时 `open_ui` 之外直接 start 确实需要它）。
  值得复核文档，**别再把"绕过 `wrong_state`"写成它的用途**（那会把缺陷当特性固化）。
- 交接日志 §6 的 T1 配方里那句"必须补 `allowAnyState=true`，否则先被 `wrong_state` 拦"
  **已被 v0.8.44 推翻**，需同步更正（本次未改交接日志）。

---

## [2026-10-05] §四十 响应信封断言（零覆盖的洞）+ 攻城 sceneLevel/timeOfDay 取证 + 一处**已记结论被反证**

> 执行者 dsh-agent。本轮**零 C# 行为改动**（只加测试 + 修测试脚本编码），v0.8.44 四段链未变。
> 起因：用户点名"① 补响应信封形状断言（唯一真正零覆盖的洞）"+"③ 攻城 sceneLevel/timeOfDay"。

### 1. ★ 缺陷：`BridgeProtocol.cs` 从未进过任何测试（用户判断**完全正确**，已取证）

三条写在 `BridgeProtocol.cs` 注释里的不变式，**一条守卫都没有**：

| 不变式 | 本轮的取证（不是感觉） |
|---|---|
| `ok==true` ⇔ `error==null` | `tools/jsontest/build_and_run.ps1` 的编译清单里**没有 `BridgeProtocol.cs`** |
| `process` 必填 | `GuardTest.cs` 提到 `Protocol` 的次数 = **1**，且那 1 次是 `Jmini.Int("{\"protocolVersion\":1}", …)` 的**字面量**，与真信封无关 |
| `protocolVersion` 正确 | `bl_selftest.py` 的假游戏端**自己手写响应 dict** ⇒ `Protocol.Success/Failure` 从未被调用过 |

⇒ 实测现状是好的（`commands\done\` 里 10896/10896 个真实响应能 `json.loads`），
**但没人守 ⇒ 坏了也没人知道**。这正是"手拼 JSON"这个最大风险面上的空门。

### 2. 新增 `tools/jsontest/EnvelopeTest.cs`（**24 条断言**，含 11 条注入对照组）

> 计数口径：门禁绿时该节**实际执行并打印 `[OK]` 的断言数 = 24**
> （正例 4 + 语义直断 5 + 敌意样本汇总 1 + Jmini 一致性 1 + 注入 11 + 反向对照 2）。
> 敌意样本循环里那条"逐个样本失败才报"的 `Check` 在绿时**不打印**，故不计入 24。

**★ 核心设计：不能用 `Jmini` 去验 `Jmini` 造的 JSON。** 那是自证，且**最容易恒绿**
（§三十九 那条写了四版才成立的断言就是栽在这里）。所以引入**独立裁判**：
`System.Web.Script.Serialization.JavaScriptSerializer` —— .NET 自带的**真** JSON 解析器，
与 `Jmini` 无任何共同代码。判据 = 「真解析器能解析」+「真解析器的读数与 Jmini 一致」。

| 组 | 内容 | 结果 |
|---|---|---|
| 正例 | `Protocol.Success/Failure` 的 4 种形状 | ✅ 全部 0 违规 |
| 敌意样本 | **18 条**（引号/反斜杠/换行/制表/CJK/emoji/`"code":"FAKE"`/`{"ok":true}`/`outcomeUncertain":true`/孤立代理项/5000 字/空串/`null`/`{`/`]`/`"`/`\`） | ✅ 18/18 仍是合法信封 |
| **独立裁判交叉验证** | Jmini 与真解析器的 `ok`/`code` 读数在 **36 个样本**上**完全一致** | ✅ 36/36 |
| **注入对照组** | 11 条（删 `process`/改 `ok`/改版本/删 `pid`/删 `loadedSha256`/删 `outcomeUncertain`/空 `code`/截断 JSON/空串/顶层数组/删 `result`）**逐条必须点名** | ✅ 11/11 抓到 |
| 反向对照 | 注入**之前**的那两份必须合法（证明断言不是恒红） | ✅ |

> **为什么注入对照组是必须的**：校验器若写成恒返回空清单，正例全绿而缺陷全漏（恒绿 = 零守护）。
> 11 条注入**逐条**要求点名具体违规串，才排除了这种写法。

### 3. ⚠️ 顺带修掉一个**测试脚本**陷阱：BOM-less `.ps1` 里的中文注释会吃掉下一行

给 `build_and_run.ps1` 加中文注释后，官方门禁开始报 **`compile failed`**，
而**手工跑同一批 csc 参数却 exit=0**。根因（已证实）：

- PowerShell **5.1** 对**无 BOM** 的 `.ps1` 按 **ANSI 代码页（本机 cp936）** 解码；
- 我那句中文注释的 UTF-8 尾部字节按 cp936 解出来**吞掉了行尾换行**，
  于是下一行 `& $csc …` 被并进注释 ⇒ **csc 根本没执行**，
  而 `$LASTEXITCODE` 还是上一次（失败）的值 ⇒ 报出**假的** "compile failed"。

**修法**：该脚本改为**纯 ASCII**（并把这条陷阱写进脚本注释）。
判据：文件非 ASCII 字节数 = **0**，门禁 exit=0。

> 这与 `AGENTS.md` 的编码纪律同源 —— 但既有的 `check_repo_encoding.py` 只管"UTF-8 无 BOM + LF"，
> **管不到"PS 5.1 会把无 BOM 的 UTF-8 当 ANSI 读"** 这一层。**这条值得往下传。**

### 4. ★★ 攻城 `sceneLevel` / `timeOfDay` —— **都有硬判据，已真机取证**

`T1-T5 报告 §12.2` 记"未验证"。本轮找到**两个独立硬判据**，全部实测：

#### 4.1 `sceneLevel`：引擎自己把值打进 rgl 日志（**判据 + 对照**）

反编译确证：`MissionState.OpenNew` 第一行就是
`Debug.Print("Opening new mission " + missionName + " " + rec.SceneLevels + ".\n")`，
而 `BannerlordMissions.OpenSiegeMissionWithDeployment` 把它算成
`sceneUpgradeLevel switch { 2=>"level_2", 1=>"level_1", _=>"level_3" } + " siege"`。

| 请求 | rgl_log_31576.txt 实测原文 |
|---|---|
| `sceneLevel=1` | `[15:16:36.204] Opening new mission CustomSiegeBattle level_1 siege.` |
| **不传（默认 3）** | `[15:22:57.683] Opening new mission CustomSiegeBattle level_3 siege.` |

⇒ **逐字对上**，且**有对照**（默认走 `level_3`）。`sceneLevel` 生效，**硬**。
（两场的 `siege_debug.log` 也都记了 `enter scene=empire_town_c a=10 d=5`，同场景不同 level。）

#### 4.2 `timeOfDay`：**截图判据**（引擎不写日志，只能看画面）

反编译链：`CreateAtmosphereInfoForMission(seasonString, (int)timeOfDay)` 用
**字典**映射 `{6→TOD_06_00_SemiCloudy, 12→TOD_12_00_SemiCloudy, 15→TOD_04_00_SemiCloudy,
18→TOD_03_00_SemiCloudy, 22→TOD_01_00_SemiCloudy}`，只在 `tryGetValue` 命中时才有名字；
未命中 ⇒ `AtmosphereName = null` ⇒ `IsValid == false`。而**这五个文件实测都存在**。

同场景 `empire_town_c` 的三场（40v40）截图：

| `timeOfDay` | 截图 | 画面 |
|---|---|---|
| **22** | `night_tod22.png` | **黑夜**：暗蓝天、月光云、城墙火把**点着** |
| **12** | `noon_tod12.png` | **正午**：明亮日光、蓝天、火把不显 |
| 14（**不在表里**） | `outofset_tod14.png` | **回落到场景自带的夜晚氛围**（与 22 近似） |

⇒ **`timeOfDay` 生效，硬**（判据 + 对照 + 边界）。
⚠️ **一条必须写进文档的边界**：**只有 {6,12,15,18,22} 这五个值有别**；
其它值（如 14）引擎查表落空 ⇒ **回落到场景自带氛围**。调用方传 14 会**看起来"没生效"**。
另注意 `15→TOD_04_00_SemiCloudy`：**名字叫"下午"，取的却是凌晨 4 点的氛围**（表本身如此，
不是我们的缺陷，但传 15 = 传一个 4 点的天空）。

### 5. ★★★ 反证一处**已归档结论**：`terrain` 的消费者说明**在自定义战斗里不成立**

`T1-T5 报告 §9.1`（与交接 §6.3）结论是：
> `Mission.TerrainType` 的**唯一消费者**是 `SandboxAgentStatCalculateModel.cs:1556-1566`（只影响队长 Perk）

**代码事实本身没错，但"消费者"挑错了模型** —— 那个模型**在自定义战斗里根本没被注册**：

- `SandBoxSubModule.cs:37-41`：`SandboxAgentStatCalculateModel` 的注册**整个包在
  `if (game.GameType is Campaign)` 里** ⇒ **只有战役**才用它；
- `CustomGame.cs:95`：自定义战斗注册的是 **`CustomBattleAgentStatCalculateModel`**；
- 而 `CustomBattleAgentStatCalculateModel.cs` 全文 **`Terrain` / `Perk` / `PerkHelper` / `Captain`
  命中数 = 0**（已用 filePattern 定向搜索复核，非只看摘要）。

⇒ **在自定义战斗里，`MissionInitializerRecord.TerrainType` 没有任何托管消费者**
（它随 struct 进 native `MBAPI.IMBMission.InitializeMission`，托管侧到此为止）。

**这不改变 §9.1 的实测结论**（"默认 vs snow 无可测差异"仍成立，且现在有了**更强的理由**），
但**改变了理由**：不是"被测兵种不带那个 Perk"，而是**那段代码在自定义战斗里压根不参与**。
⚠️ **推论**：`terrain` 在**战役**里才可能经由队长 Perk 影响 `MaxSpeedMultiplier`；
在 BlBridge 的自定义战斗靶场里，它**托管侧无任何影响路径**（native 侧影响未排除）。

> 方法论同上：**"某字段的唯一消费者是 X" 这类结论，必须连"X 在本环境里是否被注册"一起验**。

### 6. ★ `aiFriendlyFireMultiplier` 的行为效应 —— **观测到方向一致的效应，但作用面与名字不符；n 小，不下定量结论**

> ⚠️ **本节我先写错过一版，如实记录**：我按"野战路径不设 `MainAgent` ⇒ 该模型恒返回 1f"
> 推理，写下"几乎必然无效"。**真机一测就翻了**（下面前两条）。
> 教训与 §四十.5 同源：**否证性结论（"没有消费者/不会生效"）尤其不能只靠读码。**

#### 6.1 先纠正一个事实：野战场次**确实有 `MainAgent`**（硬）

`bl_control_agent(status)` 在 20v20 野战**进行中**实测：

```
mainAgent: { index:29, troop:"imperial_legionary", controller:"AI", health:100 }
playerTeam: "player"   mainAgentIsPlayerController: false   aliveCandidatesOnPlayerTeam: 20
```

⇒ 它**不是我设的**（野战 `CreateBehaviors` 里没有设 `MainAgent` 的代码），
是 CustomBattle 路径/引擎自己给的（`Agent.Controller` setter 会顺带写 `Mission.MainAgent`，见 `ControlAgent.cs` 的既有考证）。
**后果**：`DefaultMissionDifficultyModel` 里那道 `MainAgent != null` 的门是**开着的** ⇒ 该旋钮**有机会生效**。

#### 6.2 语义（读码）：它管的是"**受害者属于玩家方**"，不是"同队互殴"

`DefaultMissionDifficultyModel.cs:18-22` 的判据是 `victimAgent.IsFriendOf(mainAgent)`：

```csharp
Agent agent = Mission.Current?.MainAgent;
if (agent != null && victimAgent.IsFriendOf(agent))
    result = ((attackerAgent == null || attackerAgent != agent)
        ? Mission.Current.DamageToFriendsMultiplier
        : Mission.Current.DamageFromPlayerToFriendsMultiplier);
```

`Agent.IsFriendOf` → `MBAPI.IMBAgent.IsFriend(...)`（native，`IMBAgent.cs:74-75`）
；托管侧同族实现 `Team.IsFriendOf`（`Team.cs:634`）明确是"**同一方**"。
⚠️ **`MBAPI.IMBAgent.IsFriend` 是 native 边界**，我没能证明它逐字等于"同 team"——
但下面 6.3 的观测量与该读法**一致**，故按"受害者属于玩家一方"理解。

⇒ **它不是通常意义的"误伤"（加害者与受害者同队）**。真同队误伤在本引擎里本来就近乎无害：
12 场 / **418131** 次命中里 `Attacker→Attacker` 仅 **5814（1.39%）**，其中 **99.0% `blocked`/0 伤害**，
**全部同队误伤伤害合计仅 31 点**。

#### 6.3 真机 A/B：**匹配集**上效应明确（**硬**，t=5.20）

> ⚠️ **我第一次统计时把不同配置的场次混在一起了**（10v5 军团/长矛、40v40 军团/长矛、
> 20v20 军团/长矛、20v20 芬恩/军团 —— 全当成一个对照池），**那是无效对照**。
> 如实记下来：**"同一配置"这件事必须逐场核，不能按"都是今天跑的第 1 轮"就合并。**
> 下面只用**唯一既有 `ff=-1` 又有 `ff=0`、且时间交错**的配置。

**匹配集（`round==1`）**：`battanian_fian_champion`×20（Attacker）vs `imperial_legionary`×20（Defender），
场景 `battle_terrain_a`，`orders=charge`，**唯一变量 = `aiFriendlyFireMultiplier`**。
观测量 = **打到 Attacker（= 玩家方）的命中里"零伤害"（`damagedHp == 0`）的占比**。

| 场次 | `ff` | 打到玩家方命中 | **零伤害占比** |
|---|---|---|---|
| 15:36:34 | -1 | 204 | 21.6% |
| 15:37:11 | **0** | 623 | **83.0%** |
| 15:39:26 | -1 | 182 | 18.1% |
| 15:40:08 | **0** | 439 | **82.0%** |
| 15:45:34 | -1 | 201 | 16.4% |
| 15:45:56 | **0** | 328 | **42.4%** |
| 15:47:21 | -1 | 220 | 19.5% |
| 15:51:57 | -1 | 226 | 25.2% |
| 15:52:19 | **0** | 550 | **79.3%** |

**统计（Welch t，按匹配集）**：

| 组 | n | 均值 | sd |
|---|---|---|---|
| `ff=-1`（对照） | **5** | **20.2%** | **3.4** |
| `ff=0`（实验） | **4** | **71.7%** | 19.6 |

**t = 5.20**；两组**取值范围不重叠**（对照 max 25.2% < 实验 min 42.4%）。
⇒ 按本项目判据（"CI 不重叠或 t>2"）**结论成立**：**`aiFriendlyFireMultiplier=0` 显著提高
"打到玩家方的命中被打空"的比例**（~20% → ~72%）。**硬**。

⚠️ **两点保守边界（不许省）**：
1. **实验组 sd 大**（19.6，有一场只有 42.4%）⇒ **效应不是"全部归零"**，
   即 `ff=0` **没有**把伤害彻底消掉。**n 仍小（4/5）**，效应量的 CI 会很宽，本报告**不给出点估计的区间**。
2. 观测量 `toA_dmg`（累计伤害）**会顶到 2000**（20×100 HP 的池子被打穿）⇒ **饱和，不可作线性比较**。
   这正是 6.3 改用"零伤害占比"的原因。
3. **结局不构成判据**：实验组 4 场里 2 场 `defenderWiped`、对照 5 场里 1 场 `attackerWiped` ——
   **方向提示存在但样本太少**，不作为结论。

#### 6.3.1 ★★ 补样本到 **n=10 vs 10**（2026-10-05 晚）—— 效应量定下来了，t=17.91

**动机**：6.3 的 n=4/5 让"效应量的 CI 很宽"成了未结项。本轮用**逐场交错**编排器把样本补到 10 对。

**工具**：`E:\Document\blbridge-ff-ci\ff_interleave.py`（**不在仓库里**，刻意如此）。
- 为什么不用 `bl_batch.py`：① 它**不转发** `aiFriendlyFireMultiplier`；
  ② 它是"A 跑 N 场、再 B 跑 N 场"—— 正是本项目已量化的**批次效应**形态（交接 §6.5：两批「关」差 11~13%，组内仅 1.4%）。
- 本编排器**逐场 A/B 交替**，且**每对内部还换一次先后**（`-1,0` / `0,-1` / `-1,0` …），
  让任何时间趋势**均等地**落到两组，而不是落到组间。

**配方**（与 6.3 的匹配集完全一致）：`battanian_fian_champion`×20（Attacker）vs
`imperial_legionary`×20（Defender），`battle_terrain_a`，`orders=charge`，cap 300s，
**唯一变量 = `aiFriendlyFireMultiplier`**。20/20 场全部成功落盘。

| 组 | n | 均值 | sd | 95% CI（Welch, t*=2.086） | 范围 |
|---|---|---|---|---|---|
| `ff=-1`（对照） | **10** | **19.06%** | 3.13 | ±2.06 | [14.7, 24.2] |
| `ff=0`（实验） | **10** | **77.85%** | 9.90 | ±6.53 | [51.2, 85.8] |

**差值 = +58.79 个百分点（4.08×）；t = 17.91；两组范围不重叠。**
⇒ 按本项目判据（"CI 不重叠或 t>2"）**结论成立且量级明确**。**硬**。

> ⚠️ **附：本会话还有 2 场"误跑"的样本**（16:46:12 / 16:46:31）。
> 来历：我给编排器做**负对照**（`--tools` 传一个不存在的路径，期望它报错退出）时，
> **第一版脚本会静默回退到默认仓库路径** ⇒ 它**真的跑了 2 场**。
> 这两场是**同配置 / 同会话 / 交错在先**的合法数据，但**必须分开报**（不能算进"计划 20 场"）：
> 计入后为 **n=11 vs 11**：对照 **18.79%±3.10**、实验 **78.76%±9.86**，**t=19.24**，
> 结论与上面**一致**（差值 59.97 个百分点 / 4.19×）。
> ✅ 该脚本的静默回退**已修**（`--tools` 显式给了但不存在 ⇒ **直接报错退出，不回退**；
> 且"一场都没成功"现在是**非零退出码**，不再是 exit 0）。
> ⚠️ 我第一版还踩了第二个同类坑：**20 场全失败仍然 exit 0** —— 调度方根本看不出跑批什么都没做。
> 这条与 §四十二.3 的 `ledger` 缺口是**同一类**（"坏掉而无人知"），一并记下。

#### 6.4 结论与文档口径更正建议（**本轮未改 README**）

- `aiFriendlyFireMultiplier` **不是无效参数**（我先前那版推理错误），它有可观测效应。
- 但**该改名/改描述**：它设定的是"**打到玩家方身上的伤害**"倍率
  （`DamageToFriendsMultiplier`），**本靶场玩家方 = Attacker**；
  **它不是"消掉 AI 同队误伤"** —— 同队误伤在此引擎里本来就几乎不造成伤害（6.2 的 418131 扫描）。
- **更正后的描述建议**（留给下一轮改 `README.md` / `bl_mcp.py` / `bl_cmd.py` 三处）：
  > `aiFriendlyFireMultiplier`：设定**打到「玩家方」身上**的伤害倍率（引擎 `DamageToFriendsMultiplier`，
  > 默认 1）。本靶场玩家方 = Attacker（可用 `playerSide` 改）。设 0 ⇒ 打到玩家方的伤害**大幅下降**
  > （实测零伤害占比 ~19% → ~42–83%，**非绝对**）。**注意它不管"加害者与受害者同队"的误伤** ——
  > 那种误伤在引擎里本就几乎不掉血。

### 7. 本轮验证状态（不写成通过的部分见 §四十.8）

| # | 项 | 结果 | 强度 |
|---|---|---|---|
| ① | 响应信封断言（**24 条** + 11 注入对照组） | ✅ 新增并全绿 | **硬** |
| ② | v0.8.44 四段链 + 三条腿真机复验 | ✅ `buildcheck=ok`；`enter-battle --auto-open`→`NavalCustomBattleState`；`start_battle` **不传 `allowAnyState`** `accepted`；`close-ui` **真的回主菜单**（`GauntletInitialScreen`） | **硬** |
| ③ | 攻城 `sceneLevel` | ✅ 生效（rgl 日志 + 对照） | **硬** |
| ③ | 攻城 `timeOfDay` | ✅ 生效（截图 + 对照 + 边界） | **硬** |
| — | `terrain` 消费者说明被反证 | ✅ 更正理由（自定义战斗无托管消费者） | **硬** |
| — | `aiFriendlyFireMultiplier` 行为效应 | ✅ **显著**（零伤害占比 **19.06%±2.06 → 77.85%**，n=10/10，**t=17.91**，范围不重叠）；且**名字/描述与作用面不符** | **硬** |
| ④ | 计划任务 vs `Start-Process` | 未做 | — |
| ⑤ | `Mission.SpawnAgent` patch | **不做**（T2 已证该路径不加 modifier） | — |
| ⑥ | 知识库第 23 条归属 | **未裁定**（技术侧无输入，属用户决策） | — |

### 8. 本轮未做 / 仍未定

1. `aiFriendlyFireMultiplier` 的**文档口径更正** ⇒ **✅ 已完成**（README / `bl_mcp.py` / `bl_cmd.py`
   三处描述 + `BattleEnv.cs` / `ScenarioRunner.cs` 两处注释，2026-10-05 晚）。
2. ~~`aiFriendlyFireMultiplier` 的**效应量区间**~~ ⇒ **✅ 已完成**（§四十.6.3.1：n=10/10，**t=17.91**，
   对照 19.06%±2.06 / 实验 77.85%±6.53）。
3. `randomTerrainSeed` / `keepCorpses` 的**行为效应** ⇒ **两者均已取证**（§四十二）。
4. `terrain` 的 **native 侧**影响未排除（托管侧已确认无路径）。
5. 那次 `0xC0000005` 仍**未定性**（§9.3）。
6. 没有把 EnvelopeTest 的**同一套判据**接到 `bl_selftest.py`（Python 侧仍只验账本）；
   即"真机响应"与"构造器产物"之间仍缺一条端到端断言。
7. **`bl_mcp.py` 侧 `aiFriendlyFireMultiplier` 的范围校验（0..1）没测**：
   传 0.5 等中间值是否有线性效应，本轮只测了 0 与不传。

---

## [2026-10-05] §四十一 账本剩余的两个真缺口（v0.8.45）—— 都补上，**都做了注入验证**

> 执行者 dsh-agent。这是交接日志 §8 里"真正剩下的两个缺口"：
> **① 账本写失败静默 ② 非法响应让账本静默记错**。
> 两条都是**审计日志类工具最危险的失效模式**：不是坏掉，而是**坏掉而无人知**。

### 1. 缺口 B「非法响应让账本静默记错」—— 已修

**缺口核实（改前）**：`CommandPump.RecordLedger` 直接
`bool ok = Jmini.Bool(response, "ok", false);` —— `Jmini` 是**扁平只读器**、兜底 **false**。
⇒ 一个**成功**的请求，只要响应文本读不出来（截断/写坏/根本没有 `ok` 键），
账本就会记成 `ok=false`，**且与"真的失败"逐字段无法区分**（`code` 也会是 `""`）。
后果：所有基于账本的失败率/失败码统计都被污染。

**修法（刻意最小 + 一个测试上的理由）**：
把判定抽成 **`Protocol.ReadResponseOutcome(...)`**（`src/BridgeProtocol.cs`），
`CommandPump` 调它。这不只是"整理" —— **抽出来是为了能被离线断言**：
`CommandPump` 依赖 TaleWorlds、**离线编不进来**，而 `BridgeProtocol` 是**纯 BCL、已在
`tools/jsontest` 的编译清单里**。⇒ **出货代码与断言调的是同一个方法**，
不是各抄一份判定逻辑（抄一份就是"验副本不验出货代码"，违反项目纪律）。

判据：`readable = Jmini.Has(response, "ok")`。不可读时给**专属哨兵码 `ledger_unreadable`**
+ 非空说明（"该行 ok=false 只表示读不出来，不代表请求失败"），
`uncertain` 强制 false（**不能凭空说"副作用不确定"**）。

### 2. 缺口 A「账本写失败静默」—— 已修

**缺口核实（改前）**：`ActionLedger.Record` 的收尾是裸 `catch { }`（源码里只有一行注释）。
磁盘满 / 权限错 / `<LogDir>` 被清理 ⇒ **少行而无人知**。
**一个会静默丢行的审计日志比没有审计日志更危险** —— 它让人**以为**有记录。

**修法**：catch 改为三个可观测出口（**不改任何判定分支、不改行的字节格式**）：
- `ActionLedger.WriteFailureCount`：进程内累计写失败次数；
- `ActionLedger.LastWriteFailure`：最近一次失败的原因摘要（截断 200 字）；
- 首次失败写 **RGL 第二出口**（`ExceptionToRgl`，那条路本来就有，只是**没被调用**）；
  收敛成"只在首次"写 RGL，避免磁盘坏掉时每请求刷一条。

**外部可见**：`bridge_status.json` 新增 `ledger:{writeFailures,lastWriteFailure}`。
`bl_status` 是**整份读** `bridge_status.json`（`bl_mcp.py:2029`）⇒ 新字段**自动透出**，
不必改 Python 侧。

### 3. ★ 两条断言**都做了注入验证**（"我怎么证明它红了"）

新增 `tools/jsontest/LedgerGapTest.cs`（14 条断言）+ `tools/jsontest/Shims.cs`
（只为离线编译提供 `Debug.Print` 与 `CommandPump` 的两个路径助手）。

| 注入的缺陷 | 期望 | 实测 |
|---|---|---|
| `BridgeProtocol`: `readable = true`（= 旧行为，不判可读性） | 断言变红 | ✅ **exit=1**，7 条 `[FAIL]` 点名（含 `0/6`） |
| `ActionLedger`: catch 换回裸 `catch { }` | 断言变红 | ✅ **exit=1**，2 条 `[FAIL]`（`before=0 after=0`、原因为空） |

两次注入**都按字节还原**（sha256 与注入前一致：`fcf78132…` / `017102b6…`），
且全仓 grep `INJECTED|MUTANT` **零命中**。

### 4. ⚠️ 顺带踩到并定位的一个**环境陷阱**（不是仓库缺陷，但会误导排查）

`build.ps1` 一度报 `Get-FileHash : 不是 cmdlet`。**但 `Get-FileHash` 在每个 shell 里都可用**。
根因：**我用 `cmd /c` / `Start-Process`（未加 `-UseNewEnvironment`）去调 `powershell.exe` 时，
把 pwsh 7 的 `PSModulePath` 继承给了 PS 5.1** ⇒ PS 5.1 在自己的模块目录里找不到
`Microsoft.PowerShell.Utility`，**自动加载模块失败**。

| 调用方式 | `Get-FileHash` |
|---|---|
| `& powershell -File build.ps1`（直接调） | ✅ AVAILABLE |
| `Start-Process powershell ...`（无 `-UseNewEnvironment`） | ❌ MISSING（模块自动加载失败） |
| `cmd /c "powershell ..."` | ❌ MISSING |

**判据**：脚本里 `Import-Module $WINDIR\system32\WindowsPowerShell\v1.0\Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1` **显式导入即恢复**。
⇒ **在本项目里跑 `build.ps1` / 任何 PS 5.1 脚本，用直接调用，别用 `cmd /c` 或裸 `Start-Process`。**
（这与 §四十.1b 的 `.ps1` 编码陷阱是**两个独立**的环境坑。）

### 5. 构建与门禁（v0.8.45）

> ⚠️ **v0.8.45 构建过两次**（同版本号内追加了 `corpses` 字段，见 §四十二），下表是**最终**那次：
> 第一次 `9a677a6ed57b47af…`（仅两个账本修复），第二次 **`34c6c3bb8abdb4ca…`**（+`corpses`）为**当前部署**。
> 本项目**构建不可复现**（§三十三），所以两次哈希不同属正常，**以最终那次为准**。

| 项 | 值（最终） |
|---|---|
| 版本 | `0.8.44` → **`0.8.45`**（`BridgeConfig.Version` + `module/SubModule.xml`） |
| `out\BlBridge.dll` | 206 KB，`34c6c3bb8abdb4ca…` |
| `deployedSha256` | **逐字等于** out 产物与 manifest |
| 源码↔manifest | **0 处不一致**（38 个源文件） |
| 门禁 | encoding 107 文件 / jsontest **181 检查 0 失败** / selftest / gabp 7 判据 / dispatch 45=45=45 / metrics **全绿** |

> ✅ **进程内四段链已闭合**（2026-10-05 16:18 真机，pid **7080**，`runToken=933c21705883`）：
> `buildCheck.code = **ok**`，`loadedSha256 = currentFileSha256 = deployedSha256 = **34c6c3bb8abdb4ca**`，
> `loadedVersion = 0.8.45`，`fileChangedSinceLoad = false`。
> 顺带证实 `rgl_log_7080.txt` 里 `BlBridge.dll` 载入正常（启动器派生清单仍然正确）。
> （第一次闭合用的是 pid 38228 / `9a677a6ed57b47af`，那次尚未含 `corpses`。）

### 5.1 ★ 缺口 A 的外部出口**已真机可见**

`bl_status` 现在直接返回新块（`bl_mcp` 是整份读 `bridge_status.json`，无需改 Python 侧）：

```json
"ledger": { "writeFailures": 0, "lastWriteFailure": "" }
```

**判据 + 对照**（在**两次**构建上都核过）：同一时刻账本**确实在写**，而 `writeFailures` 仍为 **0**
⇒ 这个计数器**不会误报**（它是"真出过错"的指标，不是"调用过"的指标）。

| 构建 | pid | `writeFailures` | 账本文件 | 解析失败 |
|---|---|---|---|---|
| `9a677a6e…`（仅两个账本修复） | 38228 | **0** | 1530 行 | **0** |
| `34c6c3bb…`（+`corpses`，**当前部署**） | 7080 | **0** | **1609 行** | **0** |

> ⚠️ **一个观察（本轮未改，留待下轮定夺）**：`skip_video` 那条请求的**信封** `ok=true`
> （协议层成功应答），但它 `result` 里是 `{"ok":false,"code":"not_video"}`
> ⇒ 账本按**信封**记了 `ok=true`。这与 v0.8.42 的既定语义一致（"`ok` = 响应是否成功"），
> **不是我本轮引入的**；但后果是 `actions --fail-only` **看不到** `not_video` 这类
> "协议成功、操作失败"的请求。**要不要改口径是产品决策**，本轮只记录。

### 6. 仍未做（不写成通过）

1. ~~**真机验证新 `ledger` 字段**~~ ⇒ ✅ **已完成**（§四十一.5.1：`writeFailures:0` 且账本确实在写）。
2. **真机验证 `ledger_unreadable` 路径**：需要人为制造一条读不出来的响应（当前没有可达入口）
   ⇒ 本轮只在**离线**断言了判定语义 + **注入验证**过，**未在真机触发过**。
3. `aiFriendlyFireMultiplier` 的文档口径更正（§四十.6.4 已给文案）与效应量 CI。
4. **`randomTerrainSeed` 行为效应**：托管侧零读取消费者 ⇒ 只能靠 native，本轮仍未做。
   （`keepCorpses` **已取证**，见 §四十二。）
5. `0xC0000005` 定性；计划任务 vs `Start-Process` 同源因果对照。
6. **账本 `ok` 口径**：是否要把"信封 ok 但 `result.ok=false`"（如 `not_video`）也算失败？
   见 §四十一.5.1 末尾那条观察 —— 属产品决策，本轮未改。

---

## [2026-10-05] §四十二 补齐 `keepCorpses` 的行为判据（v0.8.45）—— T1-T5 报告 §12.5 的遗留项

> 执行者 dsh-agent。`keepCorpses` 与 `randomTerrainSeed` 一直标着"只验了落盘、未验行为效应"。
> 本轮给 **`keepCorpses`** 补上了**行为判据**（`randomTerrainSeed` 仍缺，理由见末尾）。

### 1. 为什么它此前"验不了"

`keepCorpses` 写的是 `MissionInitializerRecord.DisableCorpseFadeOut`，而该字段**托管侧零读取消费者**
（2026-10-05 定向复核：全树只有**写者**与序列化，没有读者）⇒ 它随 struct 进 native
`MBAPI.IMBMission.InitializeMission`。**要验它，唯一途径是找一个能观测"尸体还在不在"的量** ——
而当时的遥测里**没有**这种量（`sample` 只有 aAlive/dAlive/aHp/dHp）。

### 2. 修法：给 `sample` 事件加 `corpses`（**加字段，不动既有键**）

`src/TelemetryBehavior.cs`：
- 新增 `CorpseCount(Mission)`：数 `Mission.AllAgents` 里 `Agent.IsAddedAsCorpse()==true` 的个数；
- ⚠️ **必须用 `AllAgents`（`_allAgents`）而不是 `Agents`（`_activeAgents`）** —— 后者是"活跃 agent"，
  尸体不在其中（`Mission.cs` 的 `OnAgentDeleted` 才从 `AllAgents` 移除）；
- 取不到一律 **-1**（与 `TeamAlive`/`TeamHp` 同一约定），绝不假装是 0；
- 插在 `dHp` 之后 —— **既有键一个不删不改**（老分析脚本按 key 取，不受影响）。

### 3. ★ 真机 A/B：**判据成立，效应明确**

同一配方（`imperial_legionary`20 vs `sturgian_spearman`20，`orders=charge`，`battle_terrain_a`），
**唯一变量 = `keepCorpses`**。`sample` 每 10 秒一条，`corpses` 序列：

| 场次 | `keepCorpses` | 样本数 | `corpses` 时间序列 | max | **末值** |
|---|---|---|---|---|---|
| `162102` | **未传（对照）** | 50 | `0 0 0 0 0 0 0 2 2 5 9 12 13 7 5 1 1 0 0 …` (后 35 个全 0) | 13 | **0** |
| `162214` | **`true`** | 13 | `0 0 0 0 0 0 0 1 5 10 12 15 19` | 19 | **19** |

**读法**：
- **对照组尸体数升到 13 后回落到 0**（= 引擎的**尸体淡出**在正常工作）；
- **`keepCorpses=true` 时尸体数单调升到 19 并保持**（= 淡出被关掉了，与字段语义一致）；
- ⚠️ 实验组只跑到 13 个样本（战斗 13.1 s 就 `defenderWiped` 结束了，而对照组打了 50.8 s）
  ⇒ **两场的"末值"不能直接横比**。**真正的判据是"有没有回落"**：
  对照在 13→7→5→1→0 **回落过**，实验组**从未回落**（且落在同一上升轨迹上）。
  **这一条与采样长度无关**，所以结论成立。
- `corpses` **从未返回 -1**（两场共 63 个样本）⇒ 这个观测量在该路径上**可用**，不是"取不到"。

⇒ **`keepCorpses` 的行为效应成立**（`DisableCorpseFadeOut` 确实阻止尸体淡出）。**硬**。
⚠️ **边界**：本判据只看"尸体是否残留"，**不涉及**尸体对性能/寻路的影响（未测）。

### 4. 仍未做

- ~~**`randomTerrainSeed` 的行为效应**~~ ⇒ **本轮已取证，见 §四十二.5**。
- 尸体残留对**性能/寻路**的影响（本轮只看数量）。
- 版本仍是 **0.8.45**（本轮在同一个版本号内追加 `corpses` 字段；四段链已重闭合，
  `34c6c3bb8abdb4ca`，pid 7080）。

### 5. ★ `randomTerrainSeed` 的行为效应 —— 找到判据，**实测"场景布局不变"**

**判据的来源**（关键发现）：引擎在**每次加载场景**时都会往 rgl 日志打一份
**植被指纹** —— `Placed tree count, <数量>, <种类>` / `Placed flora count, <数量>, <种类>`。
这正好是"地形/植被布局有没有变"的**便宜且可靠**的观测量（不需要写任何代码）。

**实验**：同场景 `battle_terrain_a`、同配方（`imperial_legionary`10 vs `sturgian_spearman`10，
`orders=charge`），**唯一变量 = `randomTerrainSeed`**（不传 / `7` / `12345`）。

| 场次 | `terrainSeed` | 场景加载时刻 | 指纹（7 类，按数量） |
|---|---|---|---|
| `162102` | **-1（不传）** | 16:21:02 | poplar=830, mix=1225, pine_sprout=1306, pine=2893, grass_b=19433, plant=23443, grass_a=23565 |
| `162214` | **-1（不传）** | 16:22:14 | **逐字相同** |
| `162404` | **7** | 16:24:04 | **逐字相同** |
| `162433` | **12345** | 16:24:33 | **逐字相同** |

⇒ **4 次加载的指纹完全一致（去重后仅 1 种）**。

**结论（刻意保守）**：
- `randomTerrainSeed`（它同时把 `NeedsRandomTerrain` 置 true）**没有改变该场景的树木/植被布局**。
- ⚠️ **这不足以断言"该参数完全无效"**：指纹只覆盖**植被/tree 实例数**；
  `RandomTerrainSeed` 可能影响的是**别的东西**（例如战斗地形索引图
  `MapScene._battleTerrainIndexMap`、地表材质分布等），那些**本判据看不到**。
- ⚠️ 也**不能**据此判断"引擎的随机地形功能坏了" —— 只说明**在 `battle_terrain_a` 这个场景上、
  用这三个种子，植被布局没变**。**跨场景未测。**
⇒ 故本条记为：**"该判据下无可测差异"（中）**，**不写成"该参数无效"**。

> 与 §四十.5（`terrain`）的区别：`terrain` 是**托管侧确认无消费者**；
> `randomTerrainSeed` 是**托管侧同样零消费者 + 实测指纹不变**，但 native 侧仍有未知面。
