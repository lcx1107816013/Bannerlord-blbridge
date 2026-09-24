# BlBridge build script (ASCII only on purpose: PowerShell 5.1 reads .ps1 as ANSI/GBK
# when there is no UTF-8 BOM, which would corrupt non-ASCII characters.)
#
# Usage:
#   cd c:\Users\LCGX\CodeBuddy\20260923171333\BlBridge
#   powershell -ExecutionPolicy Bypass -File .\build.ps1            # compile to out\BlBridge.dll
#   powershell -ExecutionPolicy Bypass -File .\build.ps1 -Deploy    # compile + copy into Modules\BlBridge
#
# Requires the Roslyn compiler (shipped with VS Build Tools). It targets .NET Framework 4.x
# and is needed because the game's assembly uses 'in' (readonly ref) parameters, which need C# 7.2+.

param(
    [string]$GameDir = 'G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord',
    [string]$ModuleId = 'BlBridge',
    [string]$LangVersion = '7.3',
    [switch]$Deploy
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$srcDir = Join-Path $root 'src'
$moduleSrc = Join-Path $root 'module'
$outDir = Join-Path $root 'out'
$outDll = Join-Path $outDir ($ModuleId + '.dll')

# ---------- locate a Roslyn compiler ----------
$cscCandidates = @(
    'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe',
    'C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\Roslyn\csc.exe',
    'C:\Program Files\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\Roslyn\csc.exe',
    'C:\Program Files\Microsoft Visual Studio\2022\Enterprise\MSBuild\Current\Bin\Roslyn\csc.exe',
    'C:\Program Files\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe'
)
$csc = $cscCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) {
    # fall back: dotnet SDK Roslyn (csc.dll, run through dotnet)
    $cscDll = Get-ChildItem 'C:\Program Files\dotnet\sdk' -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName 'Roslyn\bincore\csc.dll' } |
        Where-Object { Test-Path $_ } | Select-Object -Last 1
    if ($cscDll) {
        $csc = $cscDll
        $useDotnet = $true
    }
}
if (-not $csc) { throw 'Roslyn csc not found. Install VS Build Tools (or the .NET SDK) and retry.' }
if ($useDotnet) { Write-Host "[0/3] compiler: dotnet $csc" } else { Write-Host "[0/3] compiler: $csc" }

# ---------- references ----------
$gameBin = Join-Path $GameDir 'bin\Win64_Shipping_Client'
if (-not (Test-Path $gameBin)) { throw "Game bin directory not found: $gameBin" }

$refs = @()
foreach ($n in @(
        'TaleWorlds.MountAndBlade.dll',
        'TaleWorlds.Core.dll',
        'TaleWorlds.Library.dll',
        'TaleWorlds.ObjectSystem.dll',
        'TaleWorlds.Engine.dll',
        'TaleWorlds.DotNet.dll',
        'TaleWorlds.Localization.dll',
        'TaleWorlds.InputSystem.dll')) {
    $p = Join-Path $gameBin $n
    if (Test-Path $p) { $refs += $p } else { Write-Warning "missing game assembly: $n" }
}

# framework reference assemblies (preferred) or runtime assemblies (fallback)
$refAsmDir = 'C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8'
if (-not (Test-Path (Join-Path $refAsmDir 'mscorlib.dll'))) {
    $refAsmDir = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
}
foreach ($n in @('mscorlib.dll', 'System.dll', 'System.Core.dll', 'System.Xml.dll')) {
    $p = Join-Path $refAsmDir $n
    if (Test-Path $p) { $refs += $p } else { Write-Warning "missing framework assembly: $n" }
}

# netstandard facade: the game assemblies are built against netstandard 2.0
foreach ($cand in @(
        (Join-Path $refAsmDir 'Facades\netstandard.dll'),
        (Join-Path $refAsmDir 'netstandard.dll'),
        (Join-Path $gameBin 'netstandard.dll'),
        'C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8\Facades\netstandard.dll',
        'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\Facades\netstandard.dll')) {
    if (Test-Path $cand) { $refs += $cand; Write-Host ("[0/3] netstandard: " + $cand); break }
}
if (-not ($refs | Where-Object { $_ -like '*netstandard.dll' })) {
    # .NET SDK ships the netstandard facade for .NET Framework targeting (net461 lib)
    $p = Get-Item 'C:\Program Files\dotnet\sdk\*\Microsoft\Microsoft.NET.Build.Extensions\net*\lib\netstandard.dll' -ErrorAction SilentlyContinue |
        Sort-Object FullName | Select-Object -Last 1
    if ($p) { $refs += $p.FullName; Write-Host ("[0/3] netstandard: " + $p.FullName) }
    else { Write-Warning 'netstandard.dll facade not found; compile may fail with CS0012' }
}

# also reference every TaleWorlds.* assembly shipped next to the game executable.
# (Deliberately limited to the TaleWorlds.* prefix: sweeping all DLLs pulls in native
#  ones (CS0009) and framework duplicates such as System.Management.dll (CS1703).)
$refs += @(Get-ChildItem $gameBin -Filter 'TaleWorlds.*.dll' -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -inotmatch '^TaleWorlds\.Native\.dll$' } |
    Where-Object {
        # TaleWorlds.Native.dll is a mixed-mode/native binary: reject anything the CLR cannot read
        try { [void][Reflection.AssemblyName]::GetAssemblyName($_.FullName); $true } catch { $false }
    } |
    ForEach-Object { $_.FullName })
$refs = @($refs | Select-Object -Unique)

# ---------- compile ----------
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
$sources = @(Get-ChildItem $srcDir -Filter '*.cs' | ForEach-Object { $_.FullName })
if ($sources.Count -eq 0) { throw "no .cs files in $srcDir" }

# Read the version once and use it for BOTH the assembly version and the manifest.
# NOTE: csc has no usable assembly-version switch -- '/version:1.2.3' just prints the
# compiler version and produces no output (verified the hard way). So the version is
# injected as a generated source file; it is still single-sourced from
# BridgeConfig.Version, so it cannot drift from the manifest.
$version = ([regex]::Match((Get-Content (Join-Path $srcDir 'BridgeConfig.cs') -Raw),
        'Version\s*=\s*"([^"]+)"')).Groups[1].Value
if (-not $version) { throw 'could not read Version from BridgeConfig.cs' }
$asmVersion = if ((@($version -split '\.')).Count -ge 4) { $version } else { $version + '.0' }
$genVersion = Join-Path $outDir 'GeneratedVersion.cs'
@(
    '// generated by build.ps1 -- do not edit; source of truth is BridgeConfig.Version'
    'using System.Reflection;'
    ('[assembly: AssemblyVersion("{0}")]' -f $asmVersion)
    ('[assembly: AssemblyFileVersion("{0}")]' -f $asmVersion)
) | Set-Content -LiteralPath $genVersion -Encoding ASCII
$sources += $genVersion
Write-Host ("[1/3] version: BridgeConfig={0} assembly={1}" -f $version, $asmVersion)

$cscArgs = @(
    '/nologo', '/target:library', '/platform:x64', '/optimize+', '/debug-',
    ('/langversion:' + $LangVersion), '/codepage:65001', '/utf8output',
    ('/out:' + $outDll)
)
foreach ($r in $refs) { $cscArgs += ('/reference:' + $r) }
$cscArgs += $sources

Write-Host ("[1/3] compiling {0} source file(s) ..." -f $sources.Count)
if ($useDotnet) { & dotnet $csc @cscArgs } else { & $csc @cscArgs }
if ($LASTEXITCODE -ne 0) { throw "compile failed (exit $LASTEXITCODE)" }

$size = [math]::Round((Get-Item $outDll).Length / 1KB, 1)
Write-Host ("[2/3] OK -> {0} ({1} KB)" -f $outDll, $size)

# ---------- build manifest (build identity for the MCP side) ----------
# Records which sources produced this DLL, so the MCP can tell apart:
#   stale_source   sources changed but nothing was rebuilt
#   stale_deploy   rebuilt but not copied into Modules
#   game_not_restarted  the running process holds an older DLL than the file on disk
$sourceHashes = [ordered]@{}
foreach ($f in (Get-ChildItem $srcDir -Filter '*.cs' | Sort-Object Name)) {
    $sourceHashes[$f.Name] = (Get-FileHash $f.FullName -Algorithm SHA256).Hash.ToLower()
}
$manifestPath = Join-Path $outDir ($ModuleId + '.manifest.json')
$manifest = [ordered]@{
    module      = $ModuleId
    version     = $version
    dllSha256   = (Get-FileHash $outDll -Algorithm SHA256).Hash.ToLower()
    dllBytes    = (Get-Item $outDll).Length
    sourceCount = $sourceHashes.Count
    sources     = $sourceHashes
    builtUtc    = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
}
# Write UTF-8 WITHOUT BOM: PowerShell 5.1's `-Encoding UTF8` adds a BOM (PS 7 does not),
# and downstream readers may parse this JSON as strict utf-8 -- so pin the encoding here,
# do not rely on luck (see AGENTS.md "encoding rules").
$utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList @($false)
$manifestJson = ($manifest | ConvertTo-Json -Depth 5) -replace "`r`n", "`n"
[System.IO.File]::WriteAllText($manifestPath, $manifestJson + "`n", $utf8NoBom)
Write-Host ("[2/3] manifest -> {0} (version {1}, {2} sources)" -f $manifestPath, $version, $sourceHashes.Count)

# ---------- deploy ----------
function Get-ActiveGameProcess {
    @(Get-Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessName -like 'Bannerlord*' -or $_.ProcessName -eq 'DedicatedServer'
        } | Select-Object -ExpandProperty Id)
}

if ($Deploy) {
    $target = Join-Path $GameDir ('Modules\' + $ModuleId)
    $targetBin = Join-Path $target 'bin\Win64_Shipping_Client'
    $dllTarget = Join-Path $targetBin ($ModuleId + '.dll')

    # Refuse to deploy while the game is running: the loaded DLL is locked by the
    # game process, so Copy-Item either fails or half-completes -- and a deploy that
    # "looks successful" is exactly how a whole evening gets spent on a stale DLL.
    # (Same rule as Coop's DeploymentEnvironment: confirm idle before AND during deploy.)
    $active = Get-ActiveGameProcess
    if ($active.Count -gt 0) {
        throw ("Game process is running (PID {0}) -- close the game completely before deploying." -f ($active -join ', '))
    }

    if (-not (Test-Path $targetBin)) { New-Item -ItemType Directory -Path $targetBin -Force | Out-Null }

    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $backup = $null
    if (Test-Path $dllTarget) {
        $backup = $dllTarget + '.bak_' + $stamp
        Copy-Item $dllTarget $backup -Force
        # keep only the 3 newest backups (they accumulate otherwise; each is ~40 KB).
        # Explicit loop + report: a silent pipeline delete is exactly the kind of
        # "looks fine but did nothing" failure this project keeps hunting.
        $stale = @(Get-ChildItem (Split-Path $dllTarget -Parent) -Filter ((Split-Path $dllTarget -Leaf) + '.bak_*') |
            Sort-Object LastWriteTime -Descending | Select-Object -Skip 3)
        foreach ($old in $stale) {
            try {
                Remove-Item -LiteralPath $old.FullName -Force -ErrorAction Stop
                Write-Host ("      pruned old backup: {0}" -f $old.Name)
            } catch {
                Write-Warning ("could not prune {0}: {1}" -f $old.Name, $_.Exception.Message)
            }
        }
    }
    # keep the module manifest version in sync with BridgeConfig.Version automatically:
    # this drift (module says v0.1.0 while the DLL says 0.6.0) was flagged by an external
    # audit; the fix is to stop hand-maintaining it. The repo copy is rewritten in place
    # (idempotent) so the source file stays honest too.
    $subModuleSrc = Join-Path $moduleSrc 'SubModule.xml'
    $xml = Get-Content -LiteralPath $subModuleSrc -Raw
    $xmlNew = [regex]::Replace($xml, '(<Version\s+value=")[^"]*(")', ('${1}v' + $version + '${2}'))
    if ($xmlNew -ne $xml) {
        # Same: write back WITHOUT BOM (PS 5.1's -Encoding UTF8 adds one). $xmlNew came from a
        # -Raw read, so its line endings already match the source file (LF).
        [System.IO.File]::WriteAllText($subModuleSrc, $xmlNew, $utf8NoBom)
        Write-Host ("      module/SubModule.xml version -> v{0}" -f $version)
    }
    Copy-Item $subModuleSrc (Join-Path $target 'SubModule.xml') -Force
    # ship the manifest next to the DLL so the MCP can verify what is deployed
    Copy-Item $manifestPath (Join-Path $target 'build_manifest.json') -Force

    # the user may have launched the game while we were copying -- check once more
    $active2 = Get-ActiveGameProcess
    if ($active2.Count -gt 0) {
        throw ("Game process appeared during deploy (PID {0}) -- deploy may be incomplete, rerun after closing the game." -f ($active2 -join ', '))
    }

    Copy-Item $outDll $dllTarget -Force

    $srcHash = (Get-FileHash $outDll -Algorithm SHA256).Hash
    $dstHash = (Get-FileHash $dllTarget -Algorithm SHA256).Hash
    if ($srcHash -ne $dstHash) { throw 'Deploy verification failed: target DLL hash differs from build output' }

    Write-Host ("[3/3] deployed to {0}" -f $target)
    Write-Host ("      dll sha256 = {0}..." -f $srcHash.Substring(0, 16))
    if ($backup) { Write-Host ("      previous dll backed up = {0}" -f (Split-Path $backup -Leaf)) }
    else { Write-Host '      (first deploy, nothing to back up)' }
} else {
    Write-Host ("[3/3] not deployed (pass -Deploy to copy into Modules\{0})" -f $ModuleId)
}
