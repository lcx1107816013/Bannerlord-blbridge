# 游戏起不来：BLSE 被 Vortex 换成符号链接

> 2026-10-18 凌晨实测。这是**环境缺陷**，不是项目缺陷，但它让整个 Bridge 真机验证
> 停摆了一整天，所以必须记下来。

---

## 症状

```
$ powershell -File tools\bl_launch.ps1
launching: G:\...\Mount & Blade II Bannerlord\bin\Win64_Shipping_Client\Bannerlord.BLSE.Standalone.exe
launcher pid = 61760
[00:47:12] window: pid=61760 class=[#32770] title=[Error from BLSE!]
[00:47:12] dialog -> answering [Error from BLSE!] (no focus needed)
        button: 4141806|Button|True|确定
        single enabled button -> BM_CLICK
[00:47:12] no Bannerlord process is running any more
LAUNCH FAILED (no game window within timeout)
```

退出码 `0x1`，`bridge_status.json` 停在 `state: "exited"`。

**关键**：启动器**自动把错误对话框点掉了**。所以第一眼只看到"超时"，
看不到真正的错误内容 —— 错误被"妥善处理"掉了。

---

## 根因

`BLSE_lasterror.log`（`bin\Win64_Shipping_Client\`）里：

```
BLSE Exception:
Version: 1.7.3.0

Type: System.IO.DirectoryNotFoundException
Message: installPath: C:\Users\LCGX\AppData\Roaming\Vortex\mountandblade2bannerlord\mods\
         Bannerlord Software Extender (BLSE) 1 1.7.2 2026-08-23T19-54Z FKnCJjs9f\.
         Modules directory not found!
Source: Bannerlord.LauncherEx
   at Bannerlord.LauncherManager.Utils.MainModuleProvider.<GetModulePaths>d__4.MoveNext()
   ...
   at Bannerlord.LauncherEx.Patches.LauncherUIPatch.InitializePostfix(...)
```

**链条**：

1. Vortex 用**符号链接**部署 BLSE：
   ```
   bin\Win64_Shipping_Client\Bannerlord.BLSE.Standalone.exe
     -> C:\Users\LCGX\...\Vortex\...\BLSE 1.7.2...\bin\Win64_Shipping_Client\Bannerlord.BLSE.Standalone.exe
   ```
2. Windows 启动它时，进程的**真实路径**是 Vortex 暂存目录那个。
3. BLSE 按**自身路径**上溯两层推游戏根目录 → 得到
   `C:\Users\LCGX\...\Vortex\...\Bannerlord Software Extender (BLSE) 1 1.7.2...\`
4. 那个目录下没有 `Modules\` → `DirectoryNotFoundException` → 弹错误框 → 退出。

**证据**：`bin` 下 8 个 BLSE 文件**全部**是 `ReparsePoint`，`size=0`；
且它们的创建时间统一是 **2026-10-17 22:09:19**（与 `vortex.deployment.bannerlord-blse.json`
的 `LastWriteTime` 一致）—— 也就是**昨晚 Vortex 重新部署过一次**，把本来是真实文件的
它们换成了链接。在那之前游戏能跑，之后就再也没起来过。

---

## 修复

把游戏目录里的 BLSE 文件**换回真实文件**（不再是链接）：

```powershell
$game  = 'G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord'
$bin   = Join-Path $game 'bin\Win64_Shipping_Client'
$stage = 'C:\Users\LCGX\AppData\Roaming\Vortex\mountandblade2bannerlord\mods\' +
         'Bannerlord Software Extender (BLSE) 1 1.7.2 2026-08-23T19-54Z FKnCJjs9f'
$src   = Join-Path $stage 'bin\Win64_Shipping_Client'

$files = @(
  'Bannerlord.BLSE.AppDomainManager.dll','Bannerlord.BLSE.Launcher.exe',
  'Bannerlord.BLSE.Launcher.exe.config','Bannerlord.BLSE.LauncherEx.exe',
  'Bannerlord.BLSE.LauncherEx.exe.config','Bannerlord.BLSE.Shared.dll',
  'Bannerlord.BLSE.Standalone.exe','Bannerlord.BLSE.Standalone.exe.config',
  'TaleWorlds.MountAndBlade.Launcher.exe.config')

foreach ($f in $files) {
  $dest = Join-Path $bin $f
  # 先删链接本身（不会删目标），再复制
  $item = Get-Item $dest -Force -ErrorAction SilentlyContinue
  if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { [IO.File]::Delete($dest) }
  Copy-Item -LiteralPath (Join-Path $src $f) -Destination $dest -Force
}
```

验证：

```powershell
Get-ChildItem (Join-Path $game 'bin\Win64_Shipping_Client') -Filter 'Bannerlord.BLSE*' -Force |
  Select-Object Name, Attributes, Length
# 期望： Attributes 不含 ReparsePoint，Length > 0
```

修完立刻能起：

```
[1] 游戏进程         PASS  22072
[2] BlBridge 已加载   PASS  moduleLoaded = true
```

---

## ⚠️ 会再次发生 —— 所以有脚本

**Vortex 每次「Deploy Mods」都会把链接建回来。** 手工修不可持续。

```powershell
# 检查
powershell -ExecutionPolicy Bypass -File tools\fix-blse-deploy.ps1 -Check
# 修
powershell -ExecutionPolicy Bypass -File tools\fix-blse-deploy.ps1
# BLSE 升级前交还给 Vortex
powershell -ExecutionPolicy Bypass -File tools\fix-blse-deploy.ps1 -Revert
```

退出码：`0` 健康/已修好，`1` 需要修，`3` 找不到路径。

### 为什么不能靠改 Vortex 设置一劳永逸

**这是本机约束，不是偏好**：

| 部署方式 | 能不能用 | 原因 |
|---|---|---|
| Symlink（当前） | ❌ | 跨卷可用，但 BLSE 推到错的根目录 —— 就是本问题 |
| **Hardlink** | ❌ **不可能** | 硬链接要求**同一个卷**。暂存目录在 `C:`，游戏在 `G:` |
| Copy | ⚠️ 能跑 | 但 Vortex 之后不知道游戏目录里是什么，改 mod 不同步 |

实测：`staging volume = C`、`game volume = G`。**跨卷把硬部署钉死了。**

所以唯一可行的形状就是本脚本做的事：**把真实副本放进游戏 bin**。

### 代价（必须知道）

**Vortex 之后不认为这 9 个文件归它管。** 因此：
- 每次 Deploy Mods 后重跑本脚本
- BLSE 升级前先 `-Revert`，让 Vortex 重新部署

---

## ★ 写这个脚本时踩的坑，值得单列

### 坑 1：PowerShell 脚本里的中文会被解析器打断

第一版脚本带中文注释，**无 BOM 的 UTF-8**。PowerShell 5.1 按 GBK 读它：

```
At fix-blse-deploy.ps1:231 char:1
+ }
+ ~
Unexpected token '}' ...
The string is missing the terminator: ".
```

乱码把一个引号吃掉了，于是**报错位置指向一个完全无关的 `}`**。

**而仓库里其他 3 个 `.ps1` 的非 ASCII 字符数都是 0** —— 那不是巧合，是约定。
已改为**纯 ASCII**（脚本内的说明也用英文），5.1 与 7 都能跑。

> 与「heredoc 陷阱」同源：含引号/非 ASCII 的脚本，先确认**解释器用什么编码读它**。

### 坑 2：判据只认一种故障形状

第一版：

```powershell
$needsFix = ($standalone.State -eq 'link')
```

测试删掉 `Standalone.exe` 后，脚本输出：

```
links 0 / real 8 / missing 1
OK: BLSE files are real copies. The game can start.
```

**文件都不在了，它说"可以启动"。**

因为 `missing` 不是 `link`，所以 "不是链接" 成立 ⇒ 判定健康。
**只认一种故障形状，对其它形状一律报健康** —— 这正是本项目反复出现的那个病。

改成按**健康**判定（`State -ne 'real'`），并把修复分支从「遍历 links」
改成「遍历所有非 real」，否则缺文件会被汇总但不会被修。

已用缺失状态实测：`-Check` 报 `BROKEN`（exit 1）→ 修复分支补回 152,256 B。

---

## 试过但不对的做法

**在 Vortex 暂存目录里给 `Modules` 建一个 Junction 指回游戏 `Modules`。**

```powershell
cmd /c mklink /J "<stage>\Modules" "<game>\Modules"
```

它确实消掉了 `DirectoryNotFoundException`。但**是错的**：它让 BLSE 把**暂存目录**
当成游戏根，于是 `Modules` 之外的一切（`Data`、`GUI`、`Shaders`、`XmlSchemas`…）
仍然指向错误的位置。**它把一个"立刻失败"变成了"以后某处诡异失败"。** 已撤销。

> 教训与本项目那条纪律同源：**让错误消失 ≠ 让事情正确。**
> 一个能跑过检查的假修复，比一个明确报错的失败更贵。

---

## 复验

```powershell
# 1) 链接是否已换成真实文件
Get-ChildItem "<game>\bin\Win64_Shipping_Client" -Filter 'Bannerlord.BLSE*' -Force |
  Select Name, @{n='IsLink';e={[bool]($_.Attributes -band [IO.FileAttributes]::ReparsePoint)}}

# 2) 启动（必须走任务计划程序：agent 会话直接起的进程会被宿主回收）
$a = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument '-ExecutionPolicy Bypass -NoProfile -File "D:\GitHub\Bannerlord-blbridge\tools\bl_launch.ps1" -TimeoutSec 300' `
  -WorkingDirectory 'D:\GitHub\Bannerlord-blbridge'
$p = New-ScheduledTaskPrincipal -UserId ($env:USERDOMAIN + '\' + $env:USERNAME) -LogonType Interactive -RunLevel Limited
$st = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName 'BlBridgeDevLaunch' -Action $a -Principal $p -Settings $st -Force
Start-ScheduledTask -TaskName 'BlBridgeDevLaunch'

# 3) 等 moduleLoaded
cd D:\GitHub\Bannerlord-blbridge\tools
python bl_live_verify.py
```

`bl_live_verify.py` 三层：进程活着 → `moduleLoaded=true`（游戏端自报）→
Bridge→Archive 直连在负载下可用。游戏不在跑时它报 **SKIP 而不是 PASS**。
