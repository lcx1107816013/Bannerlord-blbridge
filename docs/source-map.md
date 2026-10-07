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

## 五、★ 实测边界（**这一条最重要，别当成"已解决"**）

统计真机 `exceptions.jsonl` 全部 171 条异常、194 个栈帧：

| 项 | 值 |
|---|---|
| 总帧数 | 194 |
| 落在 `BlBridge` 自己 | **2（1.0%）** |
| **第三方/BCL 帧** | **192（99.0%）** |

⇒ **对"第三方 mod 崩溃"这一类，本工具结构上给不出源码行。**
原因不在工具，而在**数据源**：`ExceptionProbe` 只取栈顶 6 帧，
而真实崩溃的栈顶多为 BCL/框架帧（`ThrowCryptographicException`、`PatchTools` 等）。

**要覆盖第三方崩溃，需要换数据源**（本工具没有解决）：

- 第三方 mod 的 DLL **可能自带 PDB**（实测模块目录下有 **122 个 `.pdb`**）
  ⇒ 可为其建独立符号索引；
- `bl_crash` 的 minidump 里有**托管栈**（SOS / `dotnet-dump` 方向）。

⇒ 这两条都是**独立立项**，不要以为本工具已经覆盖。

---

## 六、用法

```powershell
# 1) 构建（build.ps1 现在产 PDB 并随 -Deploy 一起部署；/pathmap 已开）
.\build.ps1 -Deploy

# 2) 生成符号索引（基于 PDB，权威）
python tools\bl_symbols.py --build
python tools\bl_symbols.py --check        # 只报状态

# 3) 定位
python tools\bl_source_map.py --from-exceptions                 # 读真机异常逐条定位
python tools\bl_source_map.py --resolve BlBridge.CrashGuard.Finalizer
python tools\bl_source_map.py --stack "at Foo.Bar() 位置 /src/X.cs:行号 12"
# 或经 MCP：bl_source_map { "resolve": "..." } / { "fromExceptions": true }
```

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
