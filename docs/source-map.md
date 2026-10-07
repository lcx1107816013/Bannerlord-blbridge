# 源码定位（栈帧 → 文件:行号）

> 建立日期：2026-10-07　代码：`tools/bl_symbols.py`（索引）+ `tools/bl_source_map.py`（定位）
> 真机验证：**已通过**（栈里出现行号，且指向正确语句）

---

## 一、它解决什么

崩溃栈原本**只有方法名**：

```
at HarmonyLib.PatchTools.GetOriginalMethod(HarmonyMethod attr)
```

⇒ agent 拿到崩溃后**无法跳到源码那一行** ⇒「看完数据再改代码」这条路断在第一步。
本工具把栈帧映射到**源码 文件:行号**，并附**真实源码片段**。

---

## 二、实测依据（受控实验，不是照着记忆改参数）

| 编译参数 | 栈里有行号 | 行号正确 |
|---|---|---|
| `/debug-`（**改造前的本项目**） | ❌ | — |
| `/debug:portable` | ❌ | — |
| **`/debug:full`** | ✅ | ✅ **24** = throw 语句真实行号 |

### ★ 两个必须记住的坑（都实际踩过）

1. **`.NET Framework` 不认 portable PDB** —— 加 `/debug:portable` **白加**，
   栈里照样没有行号。必须 `/debug:full`（Windows PDB）。
2. **栈文本会被本地化**：中文 Windows 是
   `在 Foo.Bar() 位置 /src/X.cs:行号 316`，英文才是 `in /src/X.cs:line 316`。
   首版判据只认英文 `":line "` ⇒ 中文系统上**明明有行号却判成没有**，
   差点把"方案不可行"这个错误结论写进设计。
   ⇒ 解析**与语言无关**：认「路径样 + 冒号 + 数字」这个**形状**，不认任何关键词。

### 隐私：`/pathmap` 必开

PDB 与栈文本都会带**编译机绝对路径**（实测 `E:\Document\...`；
若构建机在 `C:\Users\<名字>\` 下就**泄漏用户名**）。
⇒ `build.ps1` 用 `/pathmap:<repo>=/` 改写成 `/src/X.cs`。
**实测**：行号仍准确，且 PDB 里 `LCGX` / `C:\Users` **零命中**。

---

## 三、真机验证结果（2026-10-07）

受控托管异常在游戏内触发后，`crashguard.jsonl` 里的栈：

```
at BlBridge.SubModule.OnApplicationTick(Single dt) in /src/SubModule.cs:line 316
at TaleWorlds.MountAndBlade.Module.OnApplicationTick(Single dt)
at TaleWorlds.DotNet.Managed.ApplicationTick_Patch1(Single dt)
```

三件事同时成立：

| 项 | 证据 |
|---|---|
| 行号出现 | `/src/SubModule.cs:line 316` |
| **行号精确** | 源码 316 行正是 `throw new InvalidOperationException(` |
| 路径已符号化 | `/src/...`，**无** `C:\Users\LCGX` |
| 无副作用 | 游戏照常运行（3720 MB），守卫 3 个目标正常 |

工具消费同一条栈的输出（`>>` 指向该行）：

```
SubModule.cs:316
   313 |             if (PendingManagedThrow)
   314 |             {
   315 |                 PendingManagedThrow = false;
>> 316 |                 throw new InvalidOperationException(
```

---

## 四、两条数据路径（**各有边界，不假装能覆盖全部**）

| 路径 | 来源 | 覆盖 |
|---|---|---|
| **A. 栈里自带行号** | 栈文本 | 只有**带 PDB 编译**的程序集（我们自己的，`/debug:full`） |
| **B. 符号索引** | `out/BlBridge.symbols.json`（PDB → 方法→文件:行号） | **我们自己的** DLL |

★ 两者都**只覆盖我们自己的代码**。第三方帧（`HarmonyLib.*` / `TaleWorlds.*`）
**如实说"无法定位"**，并区分原因：

- `第三方程序集（本来就无我们的源码）` —— 它的 DLL 不在我们仓库里；
- `本地符号索引未覆盖该方法` —— 有源码但索引没覆盖（可能过期）。

**绝不编造行号。** 报告里也不会出现"看起来合理的假位置"。

---

## 五、第三方崩溃定位（v0.8.51 · 从"给不出"变为"约 75% 可给"）

### 5.1 动机（当时的实测边界）

统计真机 `exceptions.jsonl` 全部 171 条异常、194 个栈帧：

| 项 | 值 |
|---|---|
| 总帧数 | 194 |
| 落在 `BlBridge` 自己 | **2（1.0%）** |
| **第三方/BCL 帧** | **192（99.0%）** |

⇒ **只覆盖我们自己的代码 = 几乎没覆盖真实崩溃**。这是本节的立项理由。

### 5.2 做法：为第三方 DLL 建符号索引

第三方 mod **常自带 PDB**（实测模块目录下 **123 个**，且 123/123 都有同名 DLL）。

```
python tools/bl_symbols.py --third-party
目录 46 个 | 带 PDB 的 DLL 113 个 | 成功 111 | 无匹配 PDB 2 | 失败 0
```

索引落在 `out/symbols-thirdparty/`（**已被 .gitignore 挡掉** —— 含第三方路径信息）。

### 5.3 ★ 最大的风险：PDB 与 DLL **错配**（会给错行号，比没有更坏）

实测确认 **Mono.Cecil 自带 GUID/age 校验**：

| 判据 | 结果 |
|---|---|
| 配对正确 | ✅ 读出符号（RBM 196 方法） |
| **塞入别的 PDB** | ✅ 抛 `SymbolsNotMatchingException: Symbols were found but are not matching the assembly` |
| PDB 不存在 | ✅ 抛异常 |
| 同目录多版本（`Bloodlust` 6 套 / `MCM` 11 套） | ✅ 逐个试，只收匹配的 |

⇒ 实现为「**逐个候选 PDB 试，只收校验通过的**」，**绝不"取旁边那个 pdb"**。

### 5.4 游戏版本消歧

很多 mod 目录里**同时放 42 个版本**的 dll+pdb。实测确认那串版本号是**游戏版本**
（供 `ModuleLoader` 按 `LoaderFilter` 挑），**不是** mod 版本：

- 各版本 `FileVersion` **全是同一个 mod 版本**（如 5.12.3.0）；
- 但**同一方法在不同版本里行号不同**（实测 144 vs 149）。

⇒ 用 `<game>/Modules/Native/SubModule.xml` 或 `LauncherData.xml` 的
`Native.LastKnownVersion` 取游戏版本（本机 `1.4.8`），只把**该版本**的索引当候选。

⚠️ **两种命名形态都要认**（实测踩到）：`...implementation.1.4.8`（点号）与
`...mboptionscreen.v1.4.0`（**`v` 前缀**）。首版只认前者 ⇒ MCM 的 11 个版本
一个都没消歧，全部以"歧义"被拒，把覆盖率提升**几乎全吃掉**。

### 5.5 实测覆盖率

真实方法名抽样 333 个（从 111 个索引随机取）：

| 结局 | 数量 | 占比 |
|---|---|---|
| ✅ 已定位 | 252 | **75.7%** |
| ⚠ 拒：同名方法散在多个程序集（真歧义） | ~61 | ~18% |
| ⚠ 拒：候选行号不可信（PDB 损坏） | 20 | 6.0% |

**对比改造前：1.0% → ~75%。**

### 5.6 ★ 本轮抓到的 4 个"假行号"缺陷（都看着正常，实际在骗 agent）

| # | 缺陷 | 危害 |
|---|---|---|
| 1 | **文件名撞车**：第三方 PDB 经 `/pathmap` 后也是 `/src/SubModule.cs`，而**我们的主入口同名**（实测 `SubModule.cs` 在 RBM/Bloodlust/BellumCivile/StrategicCampaignAI **每个**第三方索引里都有） | 一条 **RBM** 的帧配上**我们自己的**源码片段 ⇒ agent 照着**我们的代码**去改 RBM 的问题 |
| 2 | **调用顺序依赖**：`locate()` 的短名兜底把 `RBM.SubModule.OnSubModuleLoad` 配到 `BlBridge.SubModule.OnSubModuleLoad`（`OnSubModuleLoad` 是所有 mod 的通用入口名，**必然**碰撞） | 同上；且靠"调用方记得先判归属"是不可靠的约定 |
| 3 | **尾匹配过宽**：`TaleWorlds.MountAndBlade.Mission.Tick` 被配到任意叫 `Tick` 的方法（10 个候选）；`HarmonyLib...GetOriginalMethod` 被配到 `BUTR.CrashReport...GetOriginalMethod`（**类型不同、只是方法同名**）却报得"很确定" | 假行号 |
| 4 | **PDB 损坏行号**：`BellumCivile` / `ButterLib` 的 PDB 里出现 `line = 16707566` | agent 会拿"第 16707566 行"去改代码 |

**修法**：① 白名单 `_is_our_frame()`（只有 `BlBridge.*` 读我们的 `src/`，且守卫**放进 `locate()` 本体**而非靠调用顺序）；
② 尾匹配必须**同时要求类型名对得上**；③ `MAX_PLAUSIBLE_LINE = 2_000_000` 上界，
超界判"不可信"并如实说明，**不报位置**。

### 5.7 仍然存在的边界（如实，不许当成"已解决"）

1. **~18% 是真歧义，不是失败**：同一个方法名在多个 mod 里各自实现
   （如 `HarmonyExtensions.TryPatch` —— BUTR 共享库被复制进每个 mod），行号依实现而异。
   **"不猜"是正确行为**：猜错会给错行号。
2. **6% 是第三方 PDB 自身损坏**（`line=16707566`）—— 不是我们的问题。
3. **第三方源码片段永远没有**：我们本来就没有它们的源码（行号可给，上下文得靠 agent 自己反编译）。
4. **未做真机验证**：本轮是离线验证（真实 DLL/PDB + 真实方法名抽样）。
   真机验证需要游戏运行 + 一次真实第三方崩溃。
5. **`bl_crash` 的 minidump 托管栈**（SOS / `dotnet-dump` 方向）**仍未做** ——
   那是与"栈文本"不同的另一条数据源。


---

## 六、用法

```powershell
# 1) 构建（build.ps1 现在产 PDB 并随 -Deploy 一起部署；/pathmap 已开）
.\build.ps1 -Deploy

# 2) 生成符号索引（基于 PDB，权威）
python tools\bl_symbols.py --build
python tools\bl_symbols.py --third-party   # 第三方（out/symbols-thirdparty/）
python tools\bl_symbols.py --check        # 只报状态

# 3) 定位
python tools\bl_source_map.py --from-exceptions                 # 读真机异常逐条定位
python tools\bl_source_map.py --resolve BlBridge.CrashGuard.Finalizer
python tools\bl_source_map.py --stack "at Foo.Bar() 位置 /src/X.cs:行号 12"
# 或经 MCP：bl_source_map { "resolve": "..." } / { "fromExceptions": true }
```

---

## 六、第三方**源码片段**：能给，但有条件（修正 v0.8.51 早前的说法）

> ⚠️ **更正**：本文件早前写过"第三方源码片段永远没有"——**那句话是错的**。
> 实测模块目录下有 **413 个 `.cs`**（`RBMDev` 394 + `StrategicCampaignAI` 19）。

### 6.1 什么时候**有**源码片段

**当 mod 把开发树一起装进来时**（本机 RBM 就是这类 —— 你在 `RBMDev` 里开发）。

实测能配上源码树的索引：

| 索引 | 覆盖 |
|---|---|
| `RBM` / `RBMAI` / `RBMCampaign` / `RBMCombat` / `RBMConfig` / `RBMTournament` | **100%**（12/12、77/77、195/195、63/63、22/22、5/5） |
| `StrategicCampaignAI` | **100%**（14/14） |
| 其余 56 个索引 | 部分覆盖（如 ButterLib 1/26 —— 只装了 DLL，没装源码） |

**且行号对得上**（实测）：索引 `get_Fade` → `line=69`，源码第 69 行正是方法体的 `{`
（PDB 给的是**方法体第一个序列点**，所以落在 `{` 而非声明行 —— 语义正确）。

### 6.2 ★ 但**绝不能按文件名跨 mod 找源码**

那正是本轮抓到的撞车 bug：`SubModule.cs` 在 **RBM / Bloodlust / BellumCivile /
StrategicCampaignAI 每一个**第三方索引里都存在，而我们的主入口同名。
按 basename 找 ⇒ 一条 RBM 的帧配上**我们的**源码 ⇒ agent 照着错代码改。

⇒ 正确做法（尚未实现）：**先确定这帧属于哪个程序集，再在该程序集的源码根里找**。
判据是"索引的 assembly ↔ 源码根"的映射，而不是文件名。

---

## 七、第三方崩溃的**端到端**验证（离线可复现）

`tools/thirdpartyprobe/` 造一个**真的**"第三方 mod"（真 DLL + 真 PDB，从它内部抛异常），
把真实栈喂给定位工具，**不需要游戏**（所以能进闸门）：

```powershell
python tools\thirdpartyprobe\run_probe.py      # 12/12 通过
```

实测拿到的栈（中文 Windows 的本地化形态）：

```
在 FakeThirdPartyMod.FakeBehavior.OnApplicationTick(Single dt) 位置 /tools/thirdpartyprobe/FakeThirdPartyMod.cs:行号 40
在 FakeThirdPartyMod.Inner.Tick(Single dt) 位置 /tools/thirdpartyprobe/FakeThirdPartyMod.cs:行号 49
在 Driver.Main() 位置 /out/thirdpartyprobe_53404_0/driver.cs:行号 5
```

判据里最关键的两条：

| 判据 | 为什么关键 |
|---|---|
| **D2 反向对照**：不给该索引 ⇒ 无法定位 | 防"判据恒真"（有没有索引都报成功） |
| **D5 索引行号 == 运行时栈行号** | 不一致就是**最隐蔽的错**（索引与实际不符） |

### 7.1 真机限制（如实）

真机跑"第三方崩溃"需要往游戏目录部署一个会崩的第三方 mod，
而 `G:\...\Bannerlord` 在 DSH 工作区之外 ⇒ 需要**单次提权**才能写。
本轮的端到端验证是**离线**的（真 DLL/PDB/栈，只是不在游戏进程里）。

---

## 七、验证入口

| 项 | 命令 | 结果 |
|---|---|---|
| 定位自测 | `python tools\bl_source_map_selftest.py` | **33/33** |
| 全量闸门 | 见 `AGENTS.md` §三 | **17/17 PASS** |

自测覆盖（每条配对照组）：中/英/无关键词三种栈形态给出同一行号、
无行号**不谎报**、第三方帧如实降级且**区分两种原因**、索引缺失时明说并给命令、
纯第三方栈**不报错**（正常结果）、Windows 反斜杠、只读性、
`--resolve` 命中与未命中。

---

## 八、实现的坑（都记进代码注释）

1. **`.gitignore` 已挡 `/out/`** ⇒ PDB 与索引不会误入仓库（它们含路径信息）。
2. **抽取器运行期缺 `Mono.Cecil.dll`** —— 编译期 `/r:` 引用 ≠ 运行期可加载。
   症状是 Windows 那句「由于 Exception.ToString() 失败…」，**几乎无法诊断**。
   ⇒ 必须把 Cecil 的 DLL 拷到 exe 旁边（与 `crashguardprobe` 同一个坑）。
3. **`-Deploy` 原本只拷 DLL** ⇒ 即使构建产了 PDB，**部署后的游戏仍拿不到行号**，
   整个功能在真实场景**静默失效**。已补 PDB 部署（实测踩到）。
4. **C4 闸门误报** —— 新增 `import bl_common as _bc` 第一次把**共享库**纳入扫描，
   C4 报"缺 CLI 入口"。那是误报（库 ≠ 工具）⇒ 登记进 `CLI_EXEMPT`；
   但这一步**又让 C4 自测失效**（它按 `sorted(mods)[0]` 挑注入对象，正好挑到被豁免的库）
   ⇒ 改为**先剔除豁免项**再挑。**闸门的区分力必须自己有对照组。**
