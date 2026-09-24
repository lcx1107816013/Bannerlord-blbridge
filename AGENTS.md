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
```

另外两条与编码相关的纪律（踩过坑）：

- PowerShell 5.1 读**无 BOM 的 UTF-8 `.ps1`** 会按 ANSI 解 ⇒ `build.ps1` 刻意只用 ASCII；
  往脚本里写中文前先确认编码，或保持 ASCII。
- 脚本里 `print` 非 ASCII 时不要靠控制台编码兜底 —— 见第一节的 `safe_streams()`。
