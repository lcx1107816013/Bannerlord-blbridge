# BlBridge 项目规则（给 AI agent / 协作者）

## 一、编码规则（硬规则，2026-09-25 定）

**全仓库一律 UTF-8 无 BOM + LF 行尾**。理由与两条走过的弯路见 `README.md` §十一。

| 位置 | 规则 |
|---|---|
| 源文件（`.py` / `.cs` / `.md` / `.json` / `.xml` / `.ps1` / `.txt`） | UTF-8 **无 BOM**，LF 换行 |
| Python 的 stdout/stderr | 每个输出脚本入口调用 `bl_common.safe_streams()`（= `reconfigure(encoding="utf-8")`） |
| Python 读写文件 | 显式 `encoding="utf-8"`；要容忍 BOM 的输入用 `utf-8-sig`；二进制一律 `"rb"`/`"wb"` |
| Python 读子进程输出 | 显式 `encoding="utf-8"`；**不要**依赖 `PYTHONIOENCODING` / locale |
| C# 控制台 | `Console.OutputEncoding = Encoding.UTF8` |
| C# 读写文件 | `Encoding.UTF8` 或 `new UTF8Encoding(false)`（写文件**不带 BOM**） |
| PowerShell 写文本文件 | **别用** `Set-Content -Encoding UTF8`（PS 5.1 会加 BOM）⇒ `[System.IO.File]::WriteAllText($p, $s, (New-Object System.Text.UTF8Encoding $false))` |

**核心判据**：乱码的根因是**写入编码 ≠ 读取编码**，不是文案语言。
本项目的输出消费端是调用这些工具的 AI / 管道。**本机实测**：系统 `chcp 936` / ANSI `gb2312`，
而宿主 PowerShell（5.1）的 `[Console]::OutputEncoding` 是 `utf-8`，Python 却默认按 locale(cp936) 写
⇒ 写入/读取不一致就整片 `U+FFFD`。所以口径钉死在 UTF-8，**不依赖控制台代码页**。

> 改任何输出前，先读 `bl_common.safe_streams()` 的 docstring。

## 二、遇到「不支持 UTF-8 的代码」怎么处理

**不是**把文案改成英文，**也不是**加"编码兼容"补丁（那只是把错配挪到另一边），而是
**把那段代码改成显式 UTF-8**：

| 症状 | 改法 |
|---|---|
| `open(p)` / `open(p, "w")` 没写编码（走 locale） | 补 `encoding="utf-8"` |
| `decode("gbk")`、`encode("gbk")`、依赖 `locale.getpreferredencoding()` | 换 `utf-8` |
| 子进程输出按 locale 解 | `subprocess.run(..., text=True, encoding="utf-8")` |
| `Set-Content ... -Encoding UTF8`（PS 5.1 加 BOM） | 用 `[System.IO.File]::WriteAllText(..., UTF8Encoding($false))` |
| JSON/配置按严格 `utf-8` 读却带了 BOM | 写侧去 BOM；读侧容忍 BOM 用 `utf-8-sig` |
| 文件带 CRLF | 转 LF（见「怎么验证」里的体检脚本会报出来） |

## 三、改完必跑（验证）

```powershell
python tools\check_repo_encoding.py                  # 编码体检：UTF-8 无 BOM + LF，有违规则退出码 1
python tools\bl_selftest.py                          # 离线自测（合成数据 + MCP 协议 + 控制通道 + 构建链）
python tools\bl_metrics_selftest.py                  # 指标模块自测
powershell -ExecutionPolicy Bypass -File tools\jsontest\build_and_run.ps1   # C# 离线单测（Jmini/RequestGuard/SquadSpec…）
python tools\bl_check_clock_reset.py                 # 多轮日志「时钟同源」校验（见下）
```

**第 5 项（时钟同源校验）是正式必跑项**，理由不是「它很重要」，而是**它的判据本身就是对照实验**：

```
python tools\bl_check_clock_reset.py --since <部署时刻 ISO>   # 新产物：期望 PASS
python tools\bl_check_clock_reset.py                          # 全量：历史已知失败样本**必须仍报 FAIL**
```

**一次跑必须同时给出两边结果**：
- 新产物 **0 失败** —— 说明修复生效；
- 历史日志里的已知失败样本**仍被报出** —— 说明这个校验器**真的在判定**，而不是永远返回 PASS。

只有新产物那半边，等于没有对照组：一个恒返回 PASS 的脚本、一个数据源接错的脚本、
一个正则写错把所有事件都跳过的脚本 —— 都会「通过」。**这也是整个项目所有验证的通用纪律**：
`bl_metrics` 的合成事件手算期望、`bl_compare` 的换边双跑、jsontest 的「应报 / 不应报」样本对，
本质都是同一件事 —— **没有对照组的验证不是验证，只是自证**。

细节见修复记录与 PROGRESS §十一 判据 6：`round_*` 的 time 曾用整场累计值当轮内值
（v0.8.5–v0.8.8），**症状是静默错** —— 无异常、无报错，只是下游按 time 切窗口时算错。

## 四、发现与结论的纪律：必须能附上对照组

**任何"发现了问题"的结论，severity 都必须可追溯到一个具体对照组。写不出来的，降级或撤回。**

对照组的合法形式（按强度，优先用靠前的）：

| 类型 | 形态 | 本仓库实例 |
|---|---|---|
| **受控实验** | 只改一个变量，两种取值对照 | `FlushEveryLine` True/False 各自实测「未 Close 前可见字节」 |
| **代码内对照** | 同类实现一个有防护、一个没有 | 三个 orders 循环里两个有 `if (s == null) continue;`，`ApplyOrders` 没有 |
| **数据对照** | 实测产物里「应存在 / 应缺席」 | 106 份日志：`source="respawn"` 零命中 ⇒ 该路径从未执行 |
| **受控样本对** | 「应报 / 不应报」成对 | jsontest 77 项断言 |

**关键是「事实层」与「后果层」要各自有对照**：

- 事实层：代码确实如此（读代码 / 跑实验）。
- 后果层：**这个后果真的可达吗**（该场景在真实数据/调用路径里是否出现过）。

**最容易失手的是后果层。** 实测教训：某条「0 基 / 1 基 group 编号混用」的发现，
事实层成立（两个事件确实一个 0 基一个 1 基），但后果层写的「同批日志里关联会整体错位一组」
**不可达** —— 那两个事件在全部日志里从未共存（其中一个零命中），该条由 medium **降为 low**。

**操作检查法**：给后果层也指定对照 = 「找一份能触发该后果的真实数据或调用」。
找不到 ⇒ 后果不可达 ⇒ severity 必须下调。这一步能拦住绝大多数**严重度膨胀**
（把推演出的后果当成已发生的问题写进「影响」段）。

另外两条与编码相关的纪律（踩过坑）：

- PowerShell 5.1 读**无 BOM 的 UTF-8 `.ps1`** 会按 ANSI 解 ⇒ `build.ps1` 刻意只用 ASCII；
  往脚本里写中文前先确认编码，或保持 ASCII。

## 五、托管/原生边界：请求参数是「不可信输入」，不是「本地配置」

**凡把请求里带来的字符串直接喂给引擎原生 API 的地方，都必须先自己校验。**

理由是结构性的，不是「小心一点」就能解决：

- 引擎的原生边界**不做防御**。反编译 `TaleWorlds.Engine.dll` 可见 `Scene.Read(string sceneName)`
  的整个实现体就是一次 `EngineApplicationInterface.IScene.Read(...)` —— **无返回值、不抛托管异常**。
- 因此托管侧的 `try/catch` 在这种调用点上**结构性无效**：它拦得住托管异常，拦不住 native 访问违规。
  失败后果是**进程级**（`0xC0000005`），不是「这个请求失败」。

⇒ **判据**：一个调用点若同时满足「跨托管/原生边界」+「失败后果是进程级」，
则**必须在调用前自己校验**，不能指望 catch、不能指望引擎报错。

实测案例（v0.8.9，2026-09-25）：`Start` 把请求里的 `scene` 字符串直接交给
`new MissionInitializerRecord(scene)`。传入不存在的 `bridge` ⇒ 6 ms 后
`Loading xml file: SceneObj/bridge/scene.xscene` → `0xC0000005`，整个游戏进程死。
对照：同一请求传 `battle_terrain_a`（存在于 `SandBoxCore/SceneObj/`）则完全正常。

修复方式值得照抄：**绕开原生边界去校验** —— 引擎自己就是扫所有模块的 `SceneObj/`，
所以托管侧遍历 `ModuleHelper.GetAllModules()` 的 `FolderPath` 探测
`SceneObj/<scene>/scene.xscene`，判定结果与引擎一致，且**不需要进 native**。

顺带记一条操作教训：**`meta.mission` 不是场景名。** 本次崩溃的触发方式是从旧日志里读了
`meta.mission = "bridge"` 当成 `scene` 传进去 —— 那是遥测记录的 mission 标识
（`SubModule.MissionOrigin`）。**日志字段的语义要从写它的代码确认，不能从名字猜。**
- 脚本里 `print` 非 ASCII 时不要靠控制台编码兜底 —— 见第一节的 `safe_streams()`。
