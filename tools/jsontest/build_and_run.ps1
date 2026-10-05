param(
    [string]$Root = 'c:\Users\LCGX\CodeBuddy\20260923171333\BlBridge'
)
$ErrorActionPreference = 'Stop'
$csc = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe'
if (-not (Test-Path $csc)) { $csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" }
$out = Join-Path $Root 'out\guardtest.exe'
$src = @(
    (Join-Path $Root 'tools\jsontest\GuardTest.cs'),
    (Join-Path $Root 'tools\jsontest\EnvelopeTest.cs'),  # 2026-10-05: response envelope shape
    (Join-Path $Root 'tools\jsontest\LedgerGapTest.cs'), # 2026-10-05: the two ledger gaps
    (Join-Path $Root 'tools\jsontest\Shims.cs'),         # 2026-10-05: Debug.Print + CommandPump path helpers
    (Join-Path $Root 'src\ActionLedger.cs'),   # shipped: non-silent write-failure path
    (Join-Path $Root 'src\BridgeProtocol.cs'),  # driven by EnvelopeTest (never unit-tested before)
    (Join-Path $Root 'src\Jmini.cs'),
    (Join-Path $Root 'src\RequestGuard.cs'),
    (Join-Path $Root 'src\JsonlWriter.cs'),
    (Join-Path $Root 'src\BridgeConfig.cs'),
    (Join-Path $Root 'src\ProbePolicy.cs'),
    (Join-Path $Root 'src\BuildInfo.cs'),
    (Join-Path $Root 'src\BridgeConfigFile.cs'),
    (Join-Path $Root 'src\SquadSpec.cs'),      # T4: pure BCL (no TaleWorlds) => offline-testable
    (Join-Path $Root 'src\MainMenuStates.cs'), # v0.8.22: main-menu state names (same set as bl_mcp.py)
    (Join-Path $Root 'src\OrderSpec.cs')       # v0.8.23: order name table / validator (safety boundary)
)
Write-Output ("[1/2] compile offline unit tests -> " + $out)
# /r:System.Web.Extensions.dll : EnvelopeTest uses the BCL's real JavaScriptSerializer as an
# INDEPENDENT judge for the response envelope shape -- deliberately NOT Jmini-checking Jmini-built
# JSON (that is self-certification, and the shape of assertion most likely to be vacuously green).
# NOTE: keep this file ASCII-only. PowerShell 5.1 decodes a BOM-less .ps1 as the ANSI codepage
# (cp936 here), and a UTF-8 Chinese comment's trailing bytes can swallow the following newline,
# silently merging the next line into the comment -- that is exactly how `& $csc` got eaten and the
# gate reported a bogus "compile failed" (diagnosed 2026-10-05).
& $csc /nologo /target:exe /out:$out /r:System.Web.Extensions.dll @src
if ($LASTEXITCODE -ne 0) { Write-Output 'compile failed'; exit 1 }
Write-Output '[2/2] run'
& $out
exit $LASTEXITCODE
