param(
    [string]$Root = 'c:\Users\LCGX\CodeBuddy\20260923171333\BlBridge'
)
$ErrorActionPreference = 'Stop'
$csc = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe'
if (-not (Test-Path $csc)) { $csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" }
$out = Join-Path $Root 'out\guardtest.exe'
$src = @(
    (Join-Path $Root 'tools\jsontest\GuardTest.cs'),
    (Join-Path $Root 'src\Jmini.cs'),
    (Join-Path $Root 'src\RequestGuard.cs'),
    (Join-Path $Root 'src\JsonlWriter.cs'),
    (Join-Path $Root 'src\BridgeConfig.cs'),
    (Join-Path $Root 'src\ProbePolicy.cs'),
    (Join-Path $Root 'src\BuildInfo.cs'),
    (Join-Path $Root 'src\BridgeConfigFile.cs'),
    (Join-Path $Root 'src\SquadSpec.cs'),      # T4：纯 BCL（不碰 TaleWorlds），故可离线单测
    (Join-Path $Root 'src\MainMenuStates.cs'), # v0.8.22：主菜单层面状态名（与 bl_mcp.py 的集合同源）
    (Join-Path $Root 'src\OrderSpec.cs')       # v0.8.23：改令名字表/校验器（安全边界，碰 TaleWorlds 之前）
)
Write-Output ("[1/2] compile offline unit tests -> " + $out)
& $csc /nologo /target:exe /out:$out @src
if ($LASTEXITCODE -ne 0) { Write-Output 'compile failed'; exit 1 }
Write-Output '[2/2] run'
& $out
exit $LASTEXITCODE
