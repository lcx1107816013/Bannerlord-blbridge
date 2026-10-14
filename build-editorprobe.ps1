# EditorProbe build script (ASCII only on purpose: PowerShell 5.1 reads .ps1 as
# ANSI/GBK when there is no UTF-8 BOM, which would corrupt non-ASCII characters.)
#
# Builds the BannerlordSage.EditorProbe module. This is a SEPARATE module from
# BlBridge and is compiled against bin\Win64_Shipping_wEditor (the Modding Kit),
# NOT against bin\Win64_Shipping_Client. Keeping them apart is deliberate: BlBridge
# stays zero-dependency, and this module simply fails to build (or to load) when the
# Kit is absent, without affecting BlBridge.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\build-editorprobe.ps1
#   powershell -ExecutionPolicy Bypass -File .\build-editorprobe.ps1 -Deploy
#
# Requires the Modding Kit (Steam app 1393600, ~30 GB) and the Roslyn compiler
# shipped with VS Build Tools.

param(
    [string]$GameDir = 'G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord',
    [string]$ModuleId = 'BannerlordSage.EditorProbe',
    [string]$LangVersion = '7.3',
    [switch]$Deploy
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$srcDir = Join-Path $root 'src\EditorProbe'
$moduleSrc = Join-Path $root 'module-editorprobe'
$outDir = Join-Path $root 'out'
$outDll = Join-Path $outDir ($ModuleId + '.dll')

# ---------- locate a Roslyn compiler ----------
$cscCandidates = @(
    'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe',
    'C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\Roslyn\csc.exe',
    'C:\Program Files\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\Roslyn\csc.exe',
    'C:\Program Files\Microsoft Visual Studio\2022\Enterprise\MSBuild\Current\Bin\Roslyn\csc.exe'
)
$csc = $cscCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) { throw 'Roslyn csc not found. Install VS Build Tools and retry.' }
Write-Host "[0/4] compiler: $csc"

# ---------- references: wEditor (Modding Kit), NOT Client ----------
$editorBin = Join-Path $GameDir 'bin\Win64_Shipping_wEditor'
if (-not (Test-Path $editorBin)) {
    Write-Host "[!] Modding Kit not found: $editorBin" -ForegroundColor Yellow
    Write-Host "    This module requires Steam app 1393600 (Modding Kit)." -ForegroundColor Yellow
    Write-Host "    BlBridge is unaffected and still builds with build.ps1." -ForegroundColor Yellow
    exit 2
}

$refs = @()
foreach ($n in @(
        'TaleWorlds.MountAndBlade.dll',
        'TaleWorlds.Core.dll',
        'TaleWorlds.Library.dll',
        'TaleWorlds.Engine.dll',
        'TaleWorlds.DotNet.dll',
        'TaleWorlds.Localization.dll',
        'TaleWorlds.ScreenSystem.dll'
    )) {
    $p = Join-Path $editorBin $n
    if (-not (Test-Path $p)) { throw "Required editor assembly missing: $p" }
    $refs += $p
}
Write-Host "[1/4] references: $($refs.Count) assemblies from bin\Win64_Shipping_wEditor"

# framework reference assemblies (preferred) or runtime assemblies (fallback).
# Mirrors build.ps1: the game's assemblies are built against netstandard 2.0, so the
# netstandard facade must be referenced or every use of object fails with CS0012.
$refAsmDir = 'C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8'
if (-not (Test-Path (Join-Path $refAsmDir 'mscorlib.dll'))) {
    $refAsmDir = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
}
$frameworkRefs = @()
foreach ($n in @('mscorlib.dll', 'System.dll', 'System.Core.dll', 'System.Xml.dll')) {
    $p = Join-Path $refAsmDir $n
    if (Test-Path $p) { $frameworkRefs += $p } else { Write-Warning "missing framework assembly: $n" }
}

# netstandard facade (required by the editor assemblies)
$netstandard = $null
$netstandardCandidates = @(
    (Join-Path $refAsmDir 'Facades\netstandard.dll'),
    (Join-Path $refAsmDir 'netstandard.dll'),
    (Join-Path $editorBin 'netstandard.dll'),
    'C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8\Facades\netstandard.dll',
    'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\Facades\netstandard.dll'
)
foreach ($cand in $netstandardCandidates) {
    if (Test-Path $cand) { $netstandard = $cand; break }
}
if (-not $netstandard) {
    $found = Get-Item 'C:\Program Files\dotnet\sdk\*\Microsoft\Microsoft.NET.Build.Extensions\net*\lib\netstandard.dll' -ErrorAction SilentlyContinue |
        Sort-Object FullName | Select-Object -Last 1
    if ($found) { $netstandard = $found.FullName }
}
if ($netstandard) {
    $frameworkRefs += $netstandard
    Write-Host "[1/4] netstandard: $netstandard"
}
else {
    Write-Warning 'netstandard.dll facade not found; compile may fail with CS0012'
}

# ---------- sources ----------
if (-not (Test-Path $srcDir)) { throw "Source directory missing: $srcDir" }
$sources = Get-ChildItem -Path $srcDir -Filter '*.cs' -Recurse | Select-Object -ExpandProperty FullName
if (-not $sources -or $sources.Count -eq 0) { throw "No .cs sources under $srcDir" }
Write-Host "[2/4] sources: $($sources.Count) files"

if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }

# ---------- compile ----------
$cscArgs = @(
    '/nologo', '/target:library', '/optimize+', '/deterministic+',
    "/langversion:$LangVersion",
    "/out:$outDll"
)
foreach ($r in ($refs + $frameworkRefs)) { $cscArgs += "/reference:$r" }
foreach ($s in $sources) { $cscArgs += $s }

Write-Host "[3/4] compiling..."
& $csc @cscArgs
if ($LASTEXITCODE -ne 0) { throw "csc failed with exit code $LASTEXITCODE" }
if (-not (Test-Path $outDll)) { throw "No output DLL at $outDll" }
$size = [math]::Round((Get-Item $outDll).Length / 1KB, 1)
Write-Host "      -> $outDll ($size KB)"

# ---------- optional deploy ----------
if ($Deploy) {
    $moduleTarget = Join-Path $GameDir ('Modules\' + $ModuleId)
    # Bannerlord loads a module's assembly from bin\Win64_Shipping_Client, NOT from
    # the module root. Verified against every installed module on this machine and
    # against BlBridge itself (Modules\BlBridge\bin\Win64_Shipping_Client\BlBridge.dll).
    # A DLL at the module root is silently never loaded.
    $binTarget = Join-Path $moduleTarget 'bin\Win64_Shipping_Client'
    if (-not (Test-Path $binTarget)) { New-Item -ItemType Directory -Path $binTarget -Force | Out-Null }
    Copy-Item $outDll (Join-Path $binTarget ($ModuleId + '.dll')) -Force
    Copy-Item (Join-Path $moduleSrc 'SubModule.xml') (Join-Path $moduleTarget 'SubModule.xml') -Force

    # Remove an earlier mis-deployed copy at the module root: leaving it behind
    # would be a second, stale assembly that misleads anyone inspecting the install.
    $stale = Join-Path $moduleTarget ($ModuleId + '.dll')
    if (Test-Path $stale) { Remove-Item $stale -Force; Write-Host "      removed stale root DLL" }

    Write-Host "[4/4] deployed to $binTarget"
}
else {
    Write-Host "[4/4] not deployed (pass -Deploy to copy into Modules)"
}
