# Offline test harness for the EditorProbe module.
#
# Compiles the probe's pure-BCL plumbing (JSON, config/session, actions, server)
# together with EditorProbeTest.cs into a console EXE, and runs it. This needs NO
# running game and NO editor: it exercises the wire contract and the session file
# that the archive client validates.
#
# The game-dependent file (EditorProbeMainThread.cs) and the module entry point
# (EditorProbeSubModule.cs) ARE compiled in, because the tests assert that
# `status` is answerable without an editor. They reference TaleWorlds assemblies,
# so this script uses the same reference set as build-editorprobe.ps1.

param(
    [string]$GameDir = 'G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord'
)

$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$srcDir = Join-Path $root 'src\EditorProbe'
$testDir = Join-Path $root 'tools\editorprobe-test'
$outDir = Join-Path $root 'out'
$exe = Join-Path $outDir 'EditorProbeTest.exe'

$cscCandidates = @(
    'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe',
    'C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\Roslyn\csc.exe'
)
$csc = $cscCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) { throw 'Roslyn csc not found.' }

$editorBin = Join-Path $GameDir 'bin\Win64_Shipping_wEditor'
if (-not (Test-Path $editorBin)) { throw "Modding Kit not found: $editorBin" }

$refs = @()
foreach ($n in @(
        'TaleWorlds.MountAndBlade.dll', 'TaleWorlds.Core.dll', 'TaleWorlds.Library.dll',
        'TaleWorlds.Engine.dll', 'TaleWorlds.DotNet.dll', 'TaleWorlds.Localization.dll',
        'TaleWorlds.ScreenSystem.dll')) {
    $p = Join-Path $editorBin $n
    if (-not (Test-Path $p)) { throw "Missing editor assembly: $p" }
    $refs += $p
}

$refAsmDir = 'C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8'
if (-not (Test-Path (Join-Path $refAsmDir 'mscorlib.dll'))) {
    $refAsmDir = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
}
foreach ($n in @('mscorlib.dll', 'System.dll', 'System.Core.dll', 'System.Xml.dll')) {
    $p = Join-Path $refAsmDir $n
    if (Test-Path $p) { $refs += $p }
}

$netstandard = $null
foreach ($cand in @(
        (Join-Path $refAsmDir 'Facades\netstandard.dll'),
        (Join-Path $refAsmDir 'netstandard.dll'),
        (Join-Path $editorBin 'netstandard.dll'))) {
    if (Test-Path $cand) { $netstandard = $cand; break }
}
if ($netstandard) { $refs += $netstandard }

$sources = @()
$sources += (Get-ChildItem -Path $srcDir -Filter '*.cs' -Recurse | Select-Object -ExpandProperty FullName)
$sources += (Get-ChildItem -Path $testDir -Filter '*.cs' -Recurse | Select-Object -ExpandProperty FullName)

if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }

$cscArgs = @('/nologo', '/target:exe', '/langversion:7.3', '/main:EditorProbeTest.Program', "/out:$exe")
foreach ($r in $refs) { $cscArgs += "/reference:$r" }
foreach ($s in $sources) { $cscArgs += $s }

Write-Host "[1/2] compiling $($sources.Count) sources -> $exe"
& $csc @cscArgs
if ($LASTEXITCODE -ne 0) { throw "csc failed with exit code $LASTEXITCODE" }

Write-Host "[2/2] running"
& $exe
exit $LASTEXITCODE
