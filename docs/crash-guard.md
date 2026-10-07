# 崩溃跳过（CrashGuard）—— 设计、实测依据与安全边界

> 建立日期：2026-10-07　代码：`src/CrashGuard.cs`（MOD 侧）+ `tools/bl_crashguard.py`（MCP 侧）
> 语义验证：`tools/crashguardprobe/`（对**真实 0Harmony 2.4.2** 的受控实验）

---

## 一、三段分工（一个都不能少）

| 段 | 产物 | 实现 | 能力 |
|---|---|---|---|
| **① 阻止崩溃** | `<LogDir>\crashguard.jsonl` | `src/CrashGuard.cs`（Harmony Finalizer） | **唯一能阻止异常传播的钩子** |
| ② 观察全部异常 | `<LogDir>\exceptions.jsonl` | `src/ExceptionProbe.cs`（FirstChance） | 能观察，**不能阻止** |
| **③ 检测 + 提修复** | MCP 工具 `bl_crashguard` | `tools/bl_crashguard.py` | 读账本 + 联动词典给建议 |

### ⚠️ 三类钩子的能力差异（本设计的地基，别混为一谈）

| 钩子 | 能观察到 | **能阻止传播** |
|---|---|---|
| `AppDomain.FirstChanceException` | ✅ 最早最全（含被 catch 的） | ❌ **不能** —— 它是**通知**，返回值被忽略 |
| `AppDomain.UnhandledException` | ✅ | ❌ **不能** —— 此时进程已在终结途中 |
| **Harmony Finalizer** | ✅ | ✅ **能** —— 返回 `null` = 吞掉 |

⇒ 「跳过崩溃」**只能**由 Finalizer 提供。
`bl_exceptions` 答「发生过哪些异常」（全集）；`bl_crashguard` 答「**守卫放过了什么**」。

---

## 二、语义已用真实 DLL 实测确认（不是读文档）

`tools/crashguardprobe/` 对**真实 0Harmony 2.4.2** 跑受控实验，一条命令复现：

```powershell
python tools\crashguardprobe\run_probe.py
```

| 判据 | 结果 | 为什么必须有它 |
|---|---|---|
| A1 返回 `null` ⇒ 调用方无感 | ✅ | 吞异常成立 |
| A2/A3 `__exception` **绑定到那个异常** | ✅ | 参数名真的有意义 |
| **B1 反向对照**：原样返回 ⇒ 调用方看到异常 | ✅ | 没有它，A1 可能只是"异常根本没抛" |
| **B3 自检**：裸调用确实抛 | ✅ | 证明目标方法真的会抛 |
| C1 参数名改掉 ⇒ 报错，**不静默吞** | ✅ | "绑定"不是"恰好能编过" |
| D1 致命类型放行分支 | ✅ | 与产品代码的 IsFatal 分支同构 |

**8 条判据全过**，且闸门自带对照组（环境不足 ⇒ exit=2；破坏源码 ⇒ exit=1 抓到，非假通过）。

### ★ 实测踩到的两个坑（都写进脚本注释了）

1. **首次运行直接崩**，Windows 只给「由于 Exception.ToString() 失败，因此无法打印异常字符串」——
   逐层诊断后是**两级依赖缺失**：`0Harmony.dll` 本身，
   以及 Harmony **2.4.2 的新依赖** `MonoMod.Backports` / `MonoMod.Core` / `MonoCecil...`。
   ⇒ 所以脚本**整份拷贝** Harmony 模块目录的 DLL，而不是只拷 `0Harmony.dll`。
2. 该模块目录下 `Get-Item.Length` **全报 0**（Vortex 硬链接，AGENTS.md 记过同一条）⇒
   判"依赖是否真拷到"必须用 `ReadAllBytes().Length`。

---

## 三、安全边界（**这一段是本文最重要的部分**）

### 3.1 吞异常 ≠ 修好

异常被吞掉意味着：

- 那个方法**没有完成它本该做的事**（半完成状态）；
- 调用方**以为成功了**，继续按"成功"的假设往下走；
- ⇒ 可能产生**存档不一致 / AI 卡死 / 数值错乱**等**静默**损坏 ——
  这些**不会崩溃、不会报错**，比崩溃更难查。

⇒ 所以本功能**默认关闭**（`crashGuardEnabled: false`），
且启用后有**三道闸门**。

### 3.2 三道闸门

| 闸门 | 实现 | 为什么 |
|---|---|---|
| **① 目标白名单** | 只挂 **tick 类**方法（`Managed.ApplicationTick` / `Mission.Tick` / `ScreenManager.Tick`） | 这些方法**下次 tick 会重做** ⇒ 吞掉顶多损失一帧。`LoadSaveGame` / `ApplyResults` / `SaveAs` 这类**一次性语义**的**绝不**挂 —— 吞了就是"半完成且永不重试" |
| **② 配额** | `crashGuardSessionQuota`（默认 200） | 防"静默假活"无限延续。用完 ⇒ 之后一律放行并**如实上报** |
| **③ 熔断** | 同一签名连续 > 20 次 ⇒ 停止吞它 | 判定为**结构性故障**。继续吞只是把"崩溃"换成"卡死 + 数据损坏" |

**致命异常永不吞**（`IsFatal`）：`OutOfMemoryException` / `StackOverflowException` /
`ThreadAbortException` / `AccessViolationException` / `BadImageFormatException` /
`TypeInitializationException` / `InsufficientExecutionStackException` / `SEHException`。
理由：运行时已不可信，吞掉后**执行没有意义**，只会把明确崩溃变成随机静默错误。

### 3.3 仍然是"零 Harmony 硬依赖"（**别改坏**）

`CrashGuard` **不用 `using HarmonyLib`**，一律**反射**调 Harmony ——
与 `PatchProbe` / `McmProbe` / `UiExtendProbe` 同一范式。

理由（照 `PatchProbe.cs:45-49` 的原始取舍）：加编译期引用会让 BlBridge 在
**Harmony 未安装时无法加载**，把"可选的自保能力"变成**硬依赖**，
违背本项目「删模块即完全回退 / 不增依赖」的性质。
⇒ Harmony 不在 ⇒ 退化成 no-op，并在 `installError` 里如实写明。

### 3.4 卸载时必须撤补丁

`SubModule.OnSubModuleUnloaded` 调 `CrashGuard.Uninstall()`。
⚠️ 这比 `ExceptionProbe` 更要紧：Finalizer 是**改了别人方法的 IL**（不是订阅事件），
不撤的话模块卸载后钩子仍指向我们的方法 ⇒ 悬空引用。

---

## 四、MCP 侧读法（`bl_crashguard`）

| reason | 严重度 | 含义 |
|---|---|---|
| `breaker_open` | **critical** | 熔断已触发，守卫**主动停止吞** ⇒ 那条路径结构性坏了 |
| `quota_exhausted` | **critical** | 配额用尽 ⇒ 之后一律放行 |
| `install_failed` / `unavailable` / `target_failed` | critical / warn | 守卫**没装上**（如 Harmony 缺失） |
| `fatal_passthrough` | warn | **致命异常被放行** ⇒ 游戏**很可能仍然崩了** ⇒ 用 `bl_crash` 看 minidump |
| `swallowed` | warn | 被吞（**不是已修复**） |
| `installed` | info | 装载信息 |

**报告把 critical 排在最前**，并对每条 `swallowed` 联动词典给出中文修复建议。

### 口径纪律（写进工具描述，调用方第一眼就能判对预期）

- `action=swallow` **不等于已修复**（工具据此标注，不报成"已修复"）；
- **「没有记录」与「没有发生」是两件事**：守卫默认关闭 ⇒ 以 `enabled` 字段为准，
  **不**因文件不存在就说"没有崩溃"；
- `dropped > 0` ⇒ 队列满丢弃过记录 ⇒ **计数不全**（如实告警）。

---

## 五、怎么用

```powershell
# 1) 开启守卫（默认关闭）
#    <我的文档>\Mount and Blade II Bannerlord\BlBridge\blbridge_game.json
#    { "crashGuardEnabled": true, "crashGuardSessionQuota": 200 }
#    ← 改完必须重启游戏（与其它配置项同语义）

# 2) 读账本 + 拿修复建议
python tools\bl_crashguard.py
python tools\bl_crashguard.py --json
# 或经 MCP：bl_crashguard { "limit": 20 }
```

---

## 六、验证入口（都实跑过，可复现）

| 项 | 命令 | 结果 |
|---|---|---|
| Finalizer 语义（真实 DLL） | `python tools\crashguardprobe\run_probe.py` | 8 条判据全过 |
| 账本读取 + 建议 | `python tools\bl_crashguard_selftest.py` | 25/25（无词条）/ 27/27（有词条） |
| MCP 四处一致性 | `python tools\bl_check_dispatch.py` | 59/59 |
| 编译 | `build.ps1` | exit=0，DLL 253.5 KB |

> ⚠️ **未做真机验证**（游戏内实际吞掉一次崩溃）。
> 已验的是：Finalizer 语义（真实 Harmony DLL）、账本读写、建议生成、编译通过。
> **"真机崩溃被成功跳过"仍需在游戏里确认** —— 判据：
> 打开 `crashGuardEnabled`，触发一次已知可控的崩溃（如部署一个有意的 NRE 补丁），
> 看游戏是否继续跑 + `crashguard.jsonl` 是否出现 `action=swallow`。
