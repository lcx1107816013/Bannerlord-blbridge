# BlBridge v0.8.9 代码审查报告（外部，OCR delegation）

- 审查工具：open-code-review (OCR) delegation 模式，v1.12.9 —— OCR 只做文件筛选与规则解析，评审由宿主 agent 完成
- 被审仓库：`C:\Users\LCGX\CodeBuddy\20260923171333\BlBridge`
- 范围：`--from 66a036c --to HEAD`，mode=range
  - `from` = `66a036c`（最近一次合并，v0.8.8 多兵种混编）
  - `to` = `HEAD` = `0f5d801`
  - `merge_base` = `66a036cf656d98c963f208eb5235c612d42eb5f8`
- 判据来源：`ocr delegate rule` 两个规则组（system/default 覆盖 xml+cs；`**/*.{py,pyi,ipynb}` 覆盖 py）
- 审查日期：2026-09-25

> **入库与处置说明（v0.8.10，2026-09-25）**：本报告由外部链路产出、原样入库；宿主侧对 B1–B7 的
> 逐条核验、修复与验证记录见 `PROGRESS.md` §二十。处置结果一目：
>
> | # | 结论 | 处置 |
> |---|---|---|
> | B1 | 成立（判据反单调 ⇒ 假通过） | ✅ 已修：锚时钟 `state` + 逐类型起点点名；实测 578/709 由 PASS 变 FAIL 并点名 `probe` |
> | B2 | 成立（且**早于预估**：v0.8.7 产物里也有 `probe` 异源） | ✅ 已修：`ScoreHitProbeBehavior.BeginNewRound()`，由 `Advance` 同处调用 |
> | B3 | 成立，且由**反编译确证**（不再依赖推断）：`FolderPath` 不含尾分隔符 | ✅ 已修：`Path.Combine`；⚠️ 仍需真机两次 `start` 验证 |
> | B4 | 成立（后果不可达，维持 low） | ✅ 已修：显式拒绝 `.` / `..` |
> | B5 | 成立（后果层无对照，维持 low） | ✅ 已修：`GetActiveModules()` + `IsActive` |
> | B6 | 成立 | ✅ 已修：抽出对照组函数；该路径改为先打印对照组、返回 2 |
> | B7 | 成立 | ✅ 已修：`AFFECTED_VERSIONS` 补 `0.8.9` 并注明两类成因 |
>
> 报告中的行号为**改动前**的 `0f5d801` 版本，修复后已不对应。

---

## 一、覆盖统计（结论先行）

| 指标 | 值 |
|---|---|
| total_files | 8 |
| reviewable_files | 5 |
| excluded_files | 3（`AGENTS.md` / `PROGRESS.md` / `README.md`，工具判定 `unsupported_ext`） |
| reviewed_files | 5 |
| skipped_files | 0 |
| **coverage_rate（可评审清单）** | **5/5 = 100%** |
| **coverage_rate（全部变更文件）** | **5/8 = 62.5%** |

逐文件处置：

| 文件 | 状态 | 变更量 | 处置 |
|---|---|---|---|
| `module/SubModule.xml` | modified | +1/-1 | reviewed —— 仅版本号 v0.8.8→v0.8.9，无问题 |
| `src/BridgeConfig.cs` | modified | +1/-1 | reviewed —— 仅 `Version` 常量，无问题 |
| `src/RoundOrchestratorBehavior.cs` | modified | +15/-0 | reviewed —— 归零点位置正确，且经实测数据验证（见 §2 已验证项） |
| `src/ScenarioRunner.cs` | modified | +110/-0 | reviewed —— 3 条发现（B1、B3、B4） |
| `tools/bl_check_clock_reset.py` | added | +265/-0 | reviewed —— 3 条发现（B2、B5、B6） |

---

## 二、发现汇总（按严重度）

| # | 文件 | 位置 | 类别 | 严重度 |
|---|---|---|---|---|
| B1 | `tools/bl_check_clock_reset.py` | `analyze()` 主时钟构造（105–113 行） | test/correctness | **high** |
| B2 | `src/ScoreHitProbeBehavior.cs` | 34 / 53 / 118 行 | bug | **high** |
| B3 | `src/ScenarioRunner.cs` | 517、553 行 | bug | **high** |
| B4 | `src/ScenarioRunner.cs` | 512 行 | security | low |
| B5 | `src/ScenarioRunner.cs` | 514 行 | correctness | low |
| B6 | `tools/bl_check_clock_reset.py` | 184–189 行 | maintainability | low |
| B7 | `tools/bl_check_clock_reset.py` | 61、67 行 | documentation | low |

一句话结论：**v0.8.9 修对了 `round_*` 的时钟，但同一类缺陷在 `probe` 事件上仍然存在，而新版时钟校验器不但查不出来，还会把这种文件判成 PASS；同时新增的场景守卫有一行高置信度的路径拼接缺陷，且该守卫至今 0 次运行。**

---

## 三、逐条细节

### B1 —— 时钟校验器的判据有洞：无法发现"非 round_* 事件之间"的异源，且污染反使判定放宽（high）

**位置**：`tools/bl_check_clock_reset.py:105-113`

```104:113:tools/bl_check_clock_reset.py
    # 主时钟 = 除 round_* 外，所有带 time 的事件的取值范围
    main = [e[TIME_KEY] for e in events
            if TIME_KEY in e and isinstance(e[TIME_KEY], (int, float))
            and not str(e.get(TYPE_KEY, "")).startswith(ROUND_PREFIX)]
    ...
    lo, hi = min(main), max(main)
```

**问题**：脚本自称校验"每份 jsonl 内**所有事件**的 `time` 共享同一原点"，但实现只做了一件事 —— 把 `round_*` 与"其余全体事件的 min/max 区间"比。它**从不检查其余事件之间是否同源**。更糟的是，"其余全体"这个取法具有**反单调性**：

> 任何一条落在更大时间轴上的异源事件，都会**扩大** `[lo, hi]`，从而让 `round_*` 更容易落进区间、更容易通过。即：**加入它本该发现的缺陷，会让判定更宽松**。

**对照组（数据对照 + 代码内对照，均为真实产物）**：

对 `battle_20260925_111509_578.jsonl`，脚本打印的参考区间是 `主时钟[0.00,310.42]`：

| 事件类型 | 份数 | min_time | max_time |
|---|---:|---:|---:|
| unit | 30 | 0.000 | 0.000 |
| ai | 40 | 2.029 | 2.029 |
| state | 1750 | 2.029 | 90.029 |
| sample | 9 | 10.029 | 90.029 |
| hit | 129 | 68.935 | 91.379 |
| kill | 16 | 75.439 | 91.434 |
| round_cleanup | 1 | 91.434 | 91.434 |
| **probe** | **9** | **230.391** | **310.423** |

即：该区间上限 `310.42` **完全由 `probe` 这一个异源类型贡献**；剔除它，真正的轮内主时钟上界只有 `91.43`。同一现象见 `battle_20260925_111518_709.jsonl`（`probe` 320.442–330.445，其余事件 ≤17.235）。

脚本对这两份文件都输出 `PASS` 并打印"**通过** —— 3 份多轮文件时钟同源"，**这是假通过**。

**修法建议**：以某个**指定锚时钟**（`state` 这类每轮必有、由 `TelemetryBehavior` 写出的心跳）为参考区间，然后要求**其余每一个事件类型**都落在该区间内（超出的类型逐一点名），而不是把全体取 min/max。另建议在 `--since` 之外再加一次"跨类型同源"检查，并给一个已知含异源事件的样本作为必须报出的对照组。

---

### B2 —— `ScoreHitProbeBehavior` 自带时钟未随轮次归零：每轮文件里 `probe` 事件带整场时钟（high）

**位置**：`src/ScoreHitProbeBehavior.cs:34`（字段）、`:53`（累加）、`:118`（写出）

```50:59:src/ScoreHitProbeBehavior.cs
        public override void OnMissionTick(float dt)
        {
            base.OnMissionTick(dt);
            _elapsed += dt;
            if (_elapsed >= _nextReportAt)
            {
                _nextReportAt = _elapsed + 10f;
                Report("probe");
            }
        }
```

`_elapsed` 从 mission 开始一直累加，**全仓库没有任何一处把它归零**；`Report()` 把 `time = _elapsed` 写进**当前正在写的那个轮次文件**。该行为在 `SubModule.cs:80` 无条件挂载。

**代码内对照**（同类实现三处并列，只有它没归零）：

| 时钟 | 归零点 | 结论 |
|---|---|---|
| `TelemetryBehavior._elapsed` | `TelemetryBehavior.cs:128`（`BeginNewRound` 内 `_elapsed = 0f`） | 正确 |
| `RoundOrchestratorBehavior._elapsed` | `RoundOrchestratorBehavior.cs:177`（v0.8.9 本次新增） | 正确 |
| **`ScoreHitProbeBehavior._elapsed`** | **无** | **异源** |

**数据对照**（真实产物，直接可复核）：

| 文件 | 轮次 | `probe` 时间范围 | 同文件其余事件时间上界 | 是否异源 |
|---|---|---|---|---|
| `battle_20260925_111446_466.jsonl` | 1 | 10.024–220.355 | 224.362 | **否**（第 1 轮的整场时钟 ≡ 轮内时钟，故不可见） |
| `battle_20260925_111509_578.jsonl` | 2 | 230.391–310.423 | 91.434 | **是**（超出 200 s） |
| `battle_20260925_111518_709.jsonl` | 3 | 320.442–330.445 | 17.235 | **是**（超出 300 s） |

第 1 轮"看不出来"这一点本身就构成对照：**该缺陷只在轮次 ≥2 时可见**，所以单轮验证必然漏掉它。

**后果层**：v0.8.9 的立项目标正是"同一份日志内只有一条时间轴"，并已把「时钟同源」写进 `AGENTS.md` 作为**正式必跑项**、对外声称成立。实测表明每轮文件里仍有两条时间轴，且新版校验器为它盖章通过 —— **"验证门给出假通过"这一后果已经发生**（见 B1）。任何按 `time` 消费 `probe` 快照的下游（包括让 AI 读日志做时间对齐）都会静默算错。当前 `tools/` 下没有直接消费者，故不主张"某工具已算错"。

**修法**：给 `ScoreHitProbeBehavior` 增加轮次归零（由 `RoundOrchestratorBehavior.Advance` 在调用 `tb.BeginNewRound` 的同一处一并调用），并同时重置 `_nextReportAt = 10f`；或改为向 `TelemetryBehavior` 取轮内时钟。之后重跑一场 `--rounds 3`，用 B1 修好后的校验器复核。

---

### B3 —— `SceneExists` 的探测路径用裸拼接，未用 `Path.Combine`，且隐含假设 `FolderPath` 以分隔符结尾（high）

**位置**：`src/ScenarioRunner.cs:517`、`:553`

```514:517:src/ScenarioRunner.cs
                foreach (ModuleInfo mi in ModuleHelper.GetAllModules())
                {
                    if (mi == null || string.IsNullOrEmpty(mi.FolderPath)) continue;
                    string probe = mi.FolderPath + "SceneObj/" + scene + "/scene.xscene";
```

若 `ModuleInfo.FolderPath` **不以分隔符结尾**，`probe` 会变成 `...\Modules\SandBoxCoreSceneObj/battle_terrain_a/scene.xscene` —— 恒不存在。

**代码内对照：引擎自己所有由 `FolderPath` 拼子路径的实现，全都自己提供分隔符，无一例外。**

| 引擎位置 | 写法 |
|---|---|
| `TaleWorlds.Core/GameTextManager.cs:134` | `module.FolderPath + "/ModuleData/global_strings.xml"` —— **显式补 `"/"`** |
| `SandBox/Sandbox/MapScene.cs:275` | `Path.Combine(activeModule.FolderPath, "SceneObj", "Main_map", "scene.xscene")` |
| `TaleWorlds.MountAndBlade/MBInitialScreenBase.cs:133` | `Path.Combine(activeModule.FolderPath, "Videos", "initial_menu")` |
| `TaleWorlds.MountAndBlade/Module.cs:1044` | `Path.Combine(module2.FolderPath, "bin", Common.ConfigName)` |

新代码是**唯一**一处裸拼接。

**数据对照（引擎运行日志，2026-09-25，`C:\ProgramData\Mount and Blade II Bannerlord\logs\rgl_log_61288.txt`）**：

```
[11:39:00.790] LoadWithFullPath  subModulePath = ..\..\Modules\SandBoxCore/SubModule.xml
[11:39:00.819] opening ..\..\Modules\SandBoxCore/ModuleData/Languages\BR/language_data.xml
```

模块根渲染为 `..\..\Modules\SandBoxCore`，**其后紧跟 `/`，既无双斜杠也无尾反斜杠**。若 `FolderPath` 带尾分隔符，这两行会出现 `SandBoxCore\/ModuleData`。该行正是 `Module.cs:299` 把 `FolderPath` 交给 `LocalizedTextManager.LoadLocalizationXmls`、再按 `FolderPath + "/ModuleData/..."` 拼出的字符串。

**后果层对照（为什么这条必须报）**：该守卫于 **11:57 提交**（`d2a2be2`），而 `BlBridge\battles\` 下最新日志为 **11:15:18**（共 109 份，全部早于提交）——

> **这段代码至今运行过 0 次。**

所以"它是否是对的"完全没有运行证据；而一旦拼接错，**每一次 `start` 都会返回 `unknown_scene`**（`fail-closed`，不会崩，但桥不可用；`tools/bl_troop_sweep.py` 的每一次 start 也会被拒）。

**剩余不确定性（如实标注）**：`ModuleInfo.FolderPath` 的源码未能直接读到（`TaleWorlds.ModuleManager` 未在反编译索引内，`read_csharp_type ModuleInfo` / `ModuleHelper` 均返回未找到），故本条不是 100% 排他证明，而是上述三条对照构成的高置信推断。

**修法（一行，且使该假设彻底消失）**：

```csharp
string probe = Path.Combine(mi.FolderPath, "SceneObj", scene, "scene.xscene");
// 以及 ValidateScene 内：
string sceneObj = Path.Combine(mi.FolderPath, "SceneObj");
```

改完后**必须真机跑一次** `start`：一次传 `battle_terrain_a`（应放行）、一次传 `bridge`（应被拒且不崩）—— 这两次即构成受控样本对。

> **v0.8.10 补证（宿主侧）**：上述"剩余不确定性"已被消除 —— `ilspycmd` 反编译
> `TaleWorlds.ModuleManager.dll` 得 `ModuleInfo.LoadWithFullPath`：
> `FolderPath = fullPath;` 紧接着 `string text = FolderPath + "/SubModule.xml";`，
> 与运行日志逐字吻合 ⇒ **FolderPath 不含尾分隔符，100% 排他**。

---

### B4 —— 场景名校验的路径穿越守卫不完整：只挡分隔符，`..` 仍可逃出 `SceneObj/`（low）

**位置**：`src/ScenarioRunner.cs:512`

```512:512:src/ScenarioRunner.cs
                if (scene.IndexOf('/') >= 0 || scene.IndexOf('\\') >= 0) return false;
```

注释写的是"场景名不得含路径分隔符（否则等于允许请求越权指向任意文件）"，但这是**拼接进文件系统路径**的场景，判据应是"归一化后仍在 `<module>/SceneObj/` 之下"。`scene = ".."` 的探测路径为 `<module>/SceneObj/../scene.xscene`，逃出了 `SceneObj/`。

**后果层对照**：要让守卫**放行**，需要 `<module>/scene.xscene` 恰好存在（罕见），故实际后果不可达；即使放行，`MissionInitializerRecord("..")` 依旧会崩 —— 即该缺口只可能"放行一个仍会崩的名字"，不会造成越权读。因此定 **low**。

**修法**：显式拒绝 `.` / `..` 段，或 `Path.GetFullPath(probe)` 后用 `StartsWith(sceneObjRoot)` 校验。这与本项目 `AGENTS.md` §五「托管/原生边界：请求参数是不可信输入」的纪律同源，建议按该纪律统一处理。

---

### B5 —— `SceneExists` 用 `GetAllModules()`，引擎同类实现用 `GetActiveModules()` + `IsActive`（low）

**位置**：`src/ScenarioRunner.cs:514`

**代码内对照（引擎自己的"这个场景存在吗"实现，`SandBox/Sandbox/MapScene.cs:272-276`）**：

```272:277:Source/Modules/SandBox/bin/Win64_Shipping_Client/SandBox/Sandbox/MapScene.cs
  	private ModuleInfo GetMainMapModule()
  	{
  		ModuleInfo result = null;
  		foreach (ModuleInfo activeModule in ModuleHelper.GetActiveModules())
  		{
  			if (activeModule.IsActive && File.Exists(Path.Combine(activeModule.FolderPath, "SceneObj", "Main_map", "scene.xscene")))
```

引擎取 `GetActiveModules()` 且**额外**判 `IsActive`；新代码取 `GetAllModules()` 且不判 `IsActive`。

**后果层**：若 `GetAllModules()` 含未启用模块，而引擎只从启用模块解析场景，则"未启用模块里存在同名 `SceneObj`"时会放行、引擎照崩 —— 即**原缺陷漏拦**（fail-open）。**我无法拿到能触发该后果的真实数据**（本机模块管理器未反编译，且这需要"存在未启用且带 SceneObj 的模块"这一特定配置）。按本项目 `AGENTS.md` §四"后果层找不到对照 ⇒ severity 下调"的纪律，定 **low**，并**不主张**它已发生。

**修法（零成本，建议随 B3 一起改）**：循环内加 `if (mi == null || !mi.IsActive) continue;`，或直接改用 `ModuleHelper.GetActiveModules()`。

---

### B6 —— `--since` 且无新多轮文件时提前 `return 1`，对照组被跳过（low）

**位置**：`tools/bl_check_clock_reset.py:184-189`

```184:189:tools/bl_check_clock_reset.py
    if not multi:
        print()
        print("!! 未发现任何多轮日志。")
        print("   本判据需要 Rounds>1 的战斗才能验证 —— 请先跑一场 --rounds 3 的战斗。")
        print("   单轮战斗**不会**触发该类缺陷，跑它只会得到假阳性通过。")
        return 1
```

操作者在部署后、首次多轮战斗之前运行该门，会得到**退出码 1**（与"判定失败"同码），且因为提前返回，**对照组也不会打印**。这会把"数据不足"误报为"未通过"，与脚本头部"2 = 环境问题"的退出码约定不一致。

**修法**：该分支返回 2（环境/数据不足），或至少先把对照组输出完再返回。

---

### B7 —— `AFFECTED_VERSIONS` 已过时（low）

**位置**：`tools/bl_check_clock_reset.py:61`

```61:61:tools/bl_check_clock_reset.py
AFFECTED_VERSIONS = {"0.8.5", "0.8.6", "0.8.7", "0.8.8"}
```

仅用于报告标注、不参与判定。但 B2 已证明 **0.8.9 的 `probe` 事件同样异源**，该集合作为"受影响版本"的注记已不准确。建议改为"受影响：≤当前版本（probe 时钟）"或加入 0.8.9 并注明范围。

---

## 四、已验证无问题的部分（含证据）

- **`src/RoundOrchestratorBehavior.cs` 的归零位置正确**。`_elapsed = 0f` 放在 `_round++` 之后、`RoundLog("round_start")` 之前，且清场事件 `round_cleanup` 在其之前写出 —— 顺序上完全正确。实测产物佐证：`round_start.time` 精确为 `0`（578、709 两份文件），`round_cleanup` 保留轮末时间（91.43364 / 224.3619），与同文件 `kill` 事件末值**逐位相等**（两个 Behavior 在同一 mission tick 循环内累加相同 `dt` 之和，结构上必然相等，故判据 `±1e-6` 并非侥幸）。
- **`tools/bl_check_clock_reset.py` 的对照组机制本身是有效设计且实测有效**。运行 `python tools\bl_check_clock_reset.py --since 2026-09-25T11:10:00` 得到：新产物 3 份全 PASS；全量 15 份多轮文件中 9 份 FAIL（均为 v0.8.7 历史产物，如 `battle_20260924_220146_854.jsonl` 报 `round_start.time=144.72 FAIL(应≈0)`）—— **两侧都对，区分力成立**。这条设计值得保留（问题在于 B1 的参考区间定义，而不是这套"新产物 + 历史失败样本"的双侧机制）。
- **`round_*` 事件类型的动态发现**（不硬编码 `round_start`/`round_all_done`）是对上一版教训的正确修正，覆盖到了 `round_cleanup`。
- `module/SubModule.xml`、`src/BridgeConfig.cs` 仅版本号变更，无问题。

---

## 五、对照组复审（对本文档自身发现逐条自查）

按项目 `AGENTS.md` §四的硬规则，逐条给出"事实层"与"后果层"的对照组，并据此决定维持/降级：

| # | 事实层对照组 | 后果层对照组 | 结论 |
|---|---|---|---|
| B1 | 代码读 + 数据：578 的 `hi=310.42` 可拆解为唯一来源 `probe`；剔除后为 91.43 | **同一份真实文件**上脚本打印 PASS，而其内部明显存在两条时间轴 | 维持 **high** |
| B2 | 代码内对照（三个时钟两归一不归）+ 数据表（三份文件 3/3 可复核） | 真实产物已存在（578/709）；"验证门假通过"已发生 | 维持 **high** |
| B3 | 代码内对照 4/4 + 引擎运行日志中模块根无双分隔符 | **未能在真机触发**（守卫 0 次运行）；但后果路径 = 每一次 start，可达性无争议，前提（FolderPath 无尾分隔符）由 3 组对照支持、非 100% 排他 | 维持 **high**，但如实标注"前提为高置信推断 + 必须真机确认" |
| B4 | 代码读：`..` 不含分隔符故不被拦 | **找不到**能让它放行且产生越权后果的真实布局 | 维持 **low**（后果不可达） |
| B5 | 代码内对照：引擎用 `GetActiveModules()`+`IsActive` | **找不到**能触发该后果的真实数据（需"未启用模块含 SceneObj"） | 维持 **low**（后果层无对照，按纪律下调） |
| B6 | 代码读 + 命令行实际行为（`--since` 无新产物即返回 1） | 可由操作者一步复现（部署后立即跑门） | 维持 **low** |
| B7 | 常量与 B2 结论对比 | 仅影响报告标注 | 维持 **low** |

自撤销/降级情况：本次**无条目因"写不出对照组"而被撤回**；B4、B5 是在**已按纪律下调**后保留的（原拟 medium），即本轮共拦下 2 处严重度膨胀 —— 均失手在后果层，与项目既有教训一致。

---

## 六、建议的处置顺序

1. **B3**（场景守卫一行改 `Path.Combine` + 加 `mi.IsActive`）→ 立刻真机两次 `start` 验证（合法场景放行 / 非法场景被拒），这是当前唯一"未经任何运行"的代码，优先级最高。
2. **B2**（`ScoreHitProbeBehavior` 轮次归零）→ 与 `RoundOrchestratorBehavior.Advance` 同处调用。
3. **B1**（校验器改为"指定锚时钟 + 逐类型越界点名"）→ 否则 B2 修完也无法被这套门守住。
4. B4/B5 随 B3 一并闭合；B6/B7 为脚本卫生。

> 说明：本次审查按约定**只报告、未改动被审仓库任何文件**（`git status --short` 保持为空）。评审用的临时脚本与中间产物均落在工作区 `C:\Users\LCGX\CodeBuddy\20260925120629`，未进入仓库。
