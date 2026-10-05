# BlBridge 交接日志（第二份）· v0.8.33–v0.8.46 入库与结案

- 项目：`C:\Users\LCGX\CodeBuddy\20260923171333\BlBridge`
- 分支：**`prototype/ui-probe`**，HEAD **`8740359`**
- 当前版本：**v0.8.46**（源码 / 构建 / 部署 / 进程内**四段链已闭合**，`loadedSha256 = c01a7312de4259dd`）
- 工作区：**干净**（`git status --short` 为空）
- **本文件随仓库入库** ⇒ 与代码同寿命，不再只活在外部工作区

> 本份是 [[BlBridge-交接日志-2026-10-05]]（外部工作区，59 KB，记到 v0.8.45、当时 HEAD `af4cd0c` 且"全部改动未提交"）
> 的**续篇**。那份讲"改了什么、为什么"；这份讲**"已经进 git 了，接下来怎么接手"**。
> 本文只写**事实与判据**；未验证的一律标注"未验证 / 未做"。

---

## 1. ★ 先看 git：一个必须先知道的事实

```
远端：**没有配任何 remote**（git remote -v 为空）
分支：main  657b982
      prototype/ui-probe  8740359   ← 当前分支，无 upstream
```

**关键实测结论**：

| 判据 | 值 | 含义 |
|---|---|---|
| `git rev-list --count main..prototype/ui-probe` | **14** | prototype 领先 main 14 个 commit |
| `git rev-list --count prototype/ui-probe..main` | **0** | main **没有**任何 prototype 没有的提交 |
| `git merge-base --is-ancestor main prototype/ui-probe` | **成立** | **main 是 prototype 的严格祖先** |

⇒ **main → prototype 是纯 fast-forward，零冲突风险、零 merge commit。**
`git checkout main && git merge --ff-only prototype/ui-probe` 即可。

⚠️ **但"要不要合"是项目决策，我没做**（见 §6）。`main` 停在 `657b982`（一个攻城特性提交，
是 `af4cd0c` 的祖先）⇒ 也就是说 **v0.8.33–v0.8.46 全部只在 prototype 分支上**。

## 2. 这 14 个 commit 里，前 7 个是本轮入库的（`af4cd0c..HEAD`）

| commit | 版本 | 规模 | 内容 |
|---|---|---|---|
| `0539337` | v0.8.45 | 40 files, +9166/−301 | 账本两个**静默失效**缺口 + `keepCorpses` 行为判据 + 三处描述更正 |
| `e624f51` | — | 1 file, +67 | 记录首次入库的**切分依据**与提交后验证（PROGRESS §四十三） |
| `d1736f2` | v0.8.45 | 3 files, +466 | **信封判据接到真实响应** + `0xC0000005` 定性 + 两项行为对照 |
| `cfd9d32` | — | 10 files, +1921 | 5 份评估文档 + `tools/l2probe/` + AI 教程源文件**入库** |
| `0d23a77` | **v0.8.46** | 14 files, +563/−26 | 账本「**两个 ok**」口径定案（方案 A）+ 编码门禁**覆盖面自检** |
| `327cf12` | — | 1 file, +157 | `terrain`/`randomTerrainSeed` 的 **native 判据** + `0xC0000005` 深挖（PROGRESS §四十六） |
| `8740359` | — | 1 file, +33 | 补记 dump **全流解析**：为什么"JIT vs detour"结不了案（§四十六.1.7） |

（更早的 7 个：`82227f5` UI 原型 → `4c0edc3` v0.8.14/15 → `9c7c937` v0.8.17–31 → `af4cd0c` v0.8.32。）

## 3. 构建身份（实测，不是推断）

| 项 | 值 |
|---|---|
| 版本 | 0.8.46（`src/BridgeConfig.cs` / `module/SubModule.xml` / `module/mcp/manifest.json` 三处一致） |
| 源码 | **38** 个 `.cs` |
| 产物 | `out\BlBridge.dll`，**202 KB**，`sha256 = c01a7312de4259dd…` |
| 部署 | `G:\...\Modules\BlBridge\bin\Win64_Shipping_Client\BlBridge.dll`，**同一哈希** |
| 进程内 | `bl_status.build.loadedSha256 = c01a7312de4259dd`，`fileChangedSinceLoad=false` |
| `buildCheck` | `"四段一致：源码 = 构建产物 = 部署文件 = 进程内 DLL（版本 0.8.46）"` |
| `src` ↔ manifest | **0 处失配**（逐文件 sha256 核对） |

⚠️ **构建不可复现**（同一源码连编两次 DLL 字节不同）⇒ DLL 哈希只能做"同一次构建 ↔ 部署副本"的判据；
证明"语义没变"要用 IL 对照。

## 4. 门禁：十道全绿（本轮每次改动后都复跑）

| 门禁 | 结果 |
|---|---|
| `check_repo_encoding.py` | OK —— **111/111** 个跟踪文本文件（**修前是 110/111**，漏的正是 `tools/l2probe/_refs.rsp`） |
| `bl_selftest.py` | OK |
| `bl_metrics_selftest.py` | OK |
| `bl_check_gabp_names.py`（+`--selftest`） | OK |
| `bl_check_dispatch.py`（+`--selftest`） | OK |
| `tools/jsontest/build_and_run.ps1` | **191 OK / 0 FAIL**（修前 181） |
| `bl_check_clock_reset.py` | 全量跑 **exit 1 且 11 条历史 FAIL** ⇒ **有区分力**（恒绿才是坏消息）；`--since 2026-10-05T08:34:32Z` 3/3 PASS |
| `bl_check_envelope.py --inject --sample 15` | OK —— 注入对照 **192/192 全抓到** |

⚠️ **`bl_check_clock_reset.py` 只判多轮战斗**：单轮跑会给**假 PASS**，必须 `rounds=3`。

## 5. ★★ 本轮**推翻/更正**的既有结论（接手前必读，别按旧前提动手）

这是本份最有价值的部分 —— 这个项目的纪律是"**已归档的结论也可能被反证**"。

| # | 旧结论（曾归档） | 本轮实测 | 处置 |
|---|---|---|---|
| 1 | 账本 `ok` 是成败唯一口径 | 响应里有**两个** `ok`：信封 + `result`。10 个 handler 返回**裸 body** ⇒「信封成功但操作失败」**旧账本完全看不出**（实测 **2.00%**、10 个方法） | v0.8.46 方案 A 加 `resultOk`（三态） |
| 2 | （我建议的）"只改读侧就能补上账本口径" | **被否证**：字段根本没写进去，真实码在 26 行里 **0 命中** ⇒ 读侧无从筛起 | 改做写侧 |
| 3 | `0xC0000005` 是"纯 native"崩溃 | **反汇编故障点指令**：`48 8b 89 b8 01 00 00` = `mov rcx,[rcx+0x1b8]`，故障目标正好 `0x1b8` ⇒ **空指针解引用**，且代码在**模块外**内存 | 见 §7 |
| 4 | `terrain` / `randomTerrainSeed` "无效"（因为截图看不出） | **判据错了**（截图取决于相机朝向）。改用引擎日志的 **13 条地形相关行**：`terrain` 39 vs 3 场**差集 0**；`seed` 47/4/4 次跨会话**逐字相同** | 记为"**该判据下无可测差异**"，**不写"参数无效"** |
| 5 | "账本写失败静默"已修 | 修了，但**又发现第二处**：响应读不出来时 `Jmini.Bool(...,false)` 兜底是 **false** ⇒ 成功请求被记成**失败** | v0.8.45 加哨兵码 `ledger_unreadable` |
| 6 | 编码体检覆盖全部跟踪文件 | **`.rsp` 不在 `TEXT_EXT`** ⇒ `tools/l2probe/_refs.rsp` 静默不被检查（而它恰好合规 ⇒ 谁也没发现） | v0.8.46 加 `.rsp` + **覆盖面自检**（后缀两边都不在 ⇒ exit 1） |

**我自己在这轮犯的错（如实留档）**：

- minidump 解析器把 `MINIDUMP_MODULE.ModuleNameRva` 的偏移写成 **+68**（正确是 **+20**）⇒ 模块名全垃圾。
  基址/大小是对的（故"不在任何模块内"仍成立），但**名字错曾让我误判**。
- 一度断言"崩溃在 JIT 代码里" —— **下得太快**。该 dump 类型是 `0x200121`（**非 full-memory**）、
  `MemoryList` 有 **48608** 个碎片范围（中位仅 **0xa**）⇒ **"在捕获范围内"不等于"已提交/可执行"**。
- `terrain` 第一版 diff 比了**全部**行，得到"25 vs 1"的**假差异** —— 逐条看全是无关噪声
  （shader 缓存耗时、音效事件索引）。**"有差异"必须逐条确认是它造成的。**
- 部署副本 `mcp/bl_cmd.py` **落后一个版本**（缺 `--op-fail-only`）—— 老问题复发，已按
  `build.ps1:342` 的同一规则（`Copy-Item tools\*.py`）同步，现**0 处不一致**。

## 6. ⚠️ 未做 / 需要人裁定的

1. **要不要把 prototype 合进 main**：实测**零冲突**（§1），但 `main` 的定位（稳定线？发布线？）
   我不知道 ⇒ **没动**。
2. **要不要推远端**：**本仓库根本没配 remote** ⇒ 物理上推不了。若要，需先给仓库地址。
3. **`0xC0000005` 的属主**（JIT 还是 Harmony detour）：**本 dump 上无法定案**，原因明确 ——
   `MemoryInfoList(16)` **缺席** ⇒ 拿不到**页保护**（而区分 JIT 与 detour 正是看页属性）；
   `FunctionTable(13)` 头部干净但 **129/137 个描述符解出垃圾**。
   **要定案需要**：① full-memory dump；② 带符号 dump；③ 挂调试器复现。
   ⚠️ 另有 4 条 stream（14 UnloadedModuleList / 15 MiscInfo / 22 ProcessVmCounters /
   7 的部分字段）**我解出的是乱码** ⇒ 按纪律**不采信、不据此下结论**。
4. **那 16 秒里引擎走到哪**：界碑对照已把范围收窄到"**未进入 mission tick**"（见 §7），
   但**逐步执行流**需要已删的 rgl 日志或复现。
5. **`terrain`/`randomTerrainSeed` 的 native 侧**：只能断言"引擎**打印**的地形相关行逐字相同"，
   看不到地形索引图 / 地表材质分布。

## 7. `0xC0000005` 的完整判据链（留给下一个查崩溃的人）

**一手来源**（报告说的 `rgl_log_errors_14804.txt` **已被引擎轮转删除**）：

| 来源 | 内容 |
|---|---|
| Windows 事件日志 | `0xc0000005` / 偏移 `0x7ff7d7db51bb` / pid **14804** / 2026-10-05 12:07:59（本地） |
| 崩溃转储 | `%LOCALAPPDATA%\CrashDumps\Bannerlord.BLSE.Standalone.exe.14804.dmp`（**70,722,309 B**） |

**指令级证据**（不依赖寄存器 —— 该 dump 的 GPR **含 `Rip` 全读成 0**，退化，故未采信）：

```
故障地址 0x7ff7d7db51bb 处字节 : 48 8b 89 b8 01 00 00
译码                          : mov rcx, [rcx+0x1b8]
异常记录的目标地址             : 0x1b8
```

`[base+disp]` 的载入，故障地址正好等于 `disp` **当且仅当 `base == 0`** ⇒ **空指针解引用**。

**界碑对照**（`siege_debug.log` 按 `enter scene=` 切出 25 个 run）：

| run 起点（UTC） | 界碑数 | `returned` | `probe attached` | `flags@tick` | `waiting for agents` |
|---|---|---|---|---|---|
| **04:07:42（崩溃那场）** | **6** | ✅ | ✅ | **❌** | **❌** |
| 04:12:49（下一场，存活） | 8 | ✅ | ✅ | ✅ | ✅ |

⇒ 崩溃发生在我们代码**仍在等第一个 mission tick** 的窗口内（`OnMissionTick` 一次都没跑过）。

**可复现入口**：`E:\Document\blbridge-crash\` —— `parse_minidump2.py`（模块名已修）、
`decisive.py`、`streams.py`、`functable_filtered.py`、`runs.py`、`registers.py`。
**`terrain` 侧**：`E:\Document\blbridge-terrain\` —— `ab_terrain3.py`、`ab_seed.py`、`sample_seeds.py`。

## 8. 环境陷阱（本轮踩到的，别重复）

| 陷阱 | 表现 | 正确做法 |
|---|---|---|
| **PS 5.1 读无 BOM 的 `.ps1` 按 ANSI** | 中文注释吃掉下一行、中文字面量破坏解析 | **`.ps1` 一律纯 ASCII** |
| **PS 5.1 `Get-Content` 读 UTF-8 数据文件按 ANSI** | `ConvertFrom-Json` 报 "Invalid object passed in"（数据本身没问题） | 显式 `-Encoding UTF8` |
| `cmd /c` / 裸 `Start-Process` 继承 pwsh7 的 `PSModulePath` | PS 5.1 里 `Get-FileHash` "not recognized" | 直接 `& powershell -ExecutionPolicy Bypass -File` |
| **`tools/*.py` 改了但没部署** | 部署副本落后一个版本，MCP 侧看不到新参数 | 部署 = `Copy-Item tools\*.py`（`build.ps1:342`）；或只同步脚本不动 DLL |
| 我自己的编排器**静默失败** | 曾 **20 场全失败仍 exit 0** | 采样/编排器必须**失败即非零退出**（本轮 `sample_seeds.py` 正确做到 9/9 失败 → exit 1） |

## 9. 下一步候选（未做，按优先级）

1. `0xC0000005` 换采集方式（full-memory dump）—— 唯一能定案"属主"的路。
2. 把 `EnvelopeTest` 的断言**折进 `bl_selftest.py`**（目前两处分开）。
3. 计划任务 vs `Start-Process` 的**同源因果对照**（现象一致但未做对照）。
4. 上一条的**纯原版换边复测**（已做过一部分 §三十八，是否要更多场景未定）。
5. 攻城的**逐帧 / 姿态级**行为效应（`sceneLevel` / `timeOfDay` 目前只取证到"引擎日志层面生效"）。
