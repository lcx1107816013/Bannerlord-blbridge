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
        'TaleWorlds.CampaignSystem.dll',
        'TaleWorlds.Core.dll',
        'TaleWorlds.Library.dll',
        'TaleWorlds.ObjectSystem.dll',
        'TaleWorlds.Engine.dll',
        'TaleWorlds.DotNet.dll',
        'TaleWorlds.Localization.dll',
        'TaleWorlds.InputSystem.dll',
        # v0.8.36: save/load (SaveGameFileInfo / LoadResult live here, NOT in SaveLoad.dll)
        'TaleWorlds.SaveSystem.dll')) {
    $p = Join-Path $gameBin $n
    if (Test-Path $p) { $refs += $p } else { Write-Warning "missing game assembly: $n" }
}

# v0.8.36: SandBoxGameManager lives in the Sandbox MODULE (not in game bin); needed by load_save.
$sandBoxDll = Join-Path $GameDir 'Modules\Sandbox\bin\Win64_Shipping_Client\SandBox.dll'
if (Test-Path $sandBoxDll) { $refs += $sandBoxDll } else { Write-Warning "missing module assembly: $sandBoxDll" }

# v0.8.38: SandBox.View.dll = MapScreen (IsEscapeMenuOpened). The name does not start with
# "TaleWorlds.", so the glob above will not pick it up -- it has to be listed explicitly.
# Why we need it: the pause menu (Esc) registers an ActiveStateDisableRequest on GameStateManager,
# which stops MapState from ticking while TimeControlMode stays "play" -- i.e. the one pause shape
# our keep-awake judge (TimeControlMode == Stop) cannot see.
$sandBoxViewDll = Join-Path $GameDir 'Modules\SandBox\bin\Win64_Shipping_Client\SandBox.View.dll'
if (Test-Path $sandBoxViewDll) { $refs += $sandBoxViewDll } else { Write-Warning "missing module assembly: $sandBoxViewDll" }

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

# System.ValueTuple: InitialStateOption's constructor takes Func<(bool, TextObject)>, so the
# compiler must resolve the ValueTuple type. The reference assemblies in use are the 4.0 runtime
# fallback (its mscorlib predates ValueTuple) while the game assemblies were compiled against the
# System.ValueTuple facade -- so add it explicitly, or the main menu entry cannot compile (CS0012).
foreach ($cand in @(
        (Join-Path $env:WINDIR 'Microsoft.NET\assembly\GAC_MSIL\System.ValueTuple\v4.0_4.0.0.0__cc7b13ffcd2ddd51\System.ValueTuple.dll'),
        'C:\Program Files\dotnet\sdk\*\Microsoft\Microsoft.NET.Build.Extensions\net461\lib\System.ValueTuple.dll',
        'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\System.ValueTuple.dll')) {
    $hit = @(Get-Item $cand -ErrorAction SilentlyContinue | Sort-Object FullName | Select-Object -Last 1)
    if ($hit.Count -gt 0) {
        $refs += $hit[0].FullName
        Write-Host ("[0/3] System.ValueTuple: " + $hit[0].FullName)
        break
    }
}
if (-not ($refs | Where-Object { $_ -like '*System.ValueTuple.dll' })) {
    Write-Warning 'System.ValueTuple.dll not found; InitialStateOption (main menu entry) will not compile'
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

# Also reference TaleWorlds.* assemblies that ship INSIDE module folders. The main menu entry
# + a self-written Gauntlet screen need both of these, and neither is next to the game exe:
#   Modules\CustomBattle\...\TaleWorlds.MountAndBlade.CustomBattle.dll  (CustomGameManager, CustomGame)
#   Modules\Native\...\TaleWorlds.MountAndBlade.View.dll                ([GameStateScreen], ScreenBase helpers)
# De-duplicate by FILE NAME, not path: several modules ship their own copy of the same assembly
# (TaleWorlds.MountAndBlade.Multiplayer.dll exists under both CustomBattle and Multiplayer),
# and referencing two paths with the same assembly identity fails with CS1703.
$refNames = @{}
foreach ($r in $refs) { $refNames[(Split-Path $r -Leaf).ToLowerInvariant()] = $true }
$moduleBinDirs = @(Get-ChildItem (Join-Path $GameDir 'Modules') -Directory -ErrorAction SilentlyContinue |
    ForEach-Object { Join-Path $_.FullName 'bin\Win64_Shipping_Client' } |
    Where-Object { Test-Path $_ })
foreach ($dir in $moduleBinDirs) {
    foreach ($dll in @(Get-ChildItem $dir -Filter 'TaleWorlds.*.dll' -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -inotmatch '^TaleWorlds\.Native\.dll$' })) {
        $key = $dll.Name.ToLowerInvariant()
        if ($refNames.ContainsKey($key)) { continue }
        # same readability guard as above: skip mixed-mode/native binaries
        try { [void][Reflection.AssemblyName]::GetAssemblyName($dll.FullName) } catch { continue }
        $refNames[$key] = $true
        $refs += $dll.FullName
    }
}

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
$genLines = @(
    '// generated by build.ps1 -- do not edit; source of truth is BridgeConfig.Version'
    'using System.Reflection;'
    ('[assembly: AssemblyVersion("{0}")]' -f $asmVersion)
    ('[assembly: AssemblyFileVersion("{0}")]' -f $asmVersion)
) 
# ASCII + LF via WriteAllText: Set-Content writes CRLF on Windows (see AGENTS.md "encoding rules").
[System.IO.File]::WriteAllText($genVersion, (($genLines -join "`n") + "`n"), [System.Text.Encoding]::ASCII)
$sources += $genVersion
Write-Host ("[1/3] version: BridgeConfig={0} assembly={1}" -f $version, $asmVersion)

# /debug:full + /pathmap (v0.8.50, 2026-10-07): ship line numbers, without leaking the build path.
#
# WHY: crash stacks used to carry ONLY method names, so an agent could not jump to
#      the source line -> the "read the data, then fix the code" loop broke at step 1.
#
# MEASURED (controlled experiment, not assumed):
#   /debug-          -> stack has NO line number          (the old setting)
#   /debug:portable  -> stack has NO line number          (.NET Framework ignores portable PDBs!)
#   /debug:full      -> stack HAS `... .cs:line 24`       (24 == the real throw line)
#   => /debug:full is required; portable silently does nothing on net472.
#
# /pathmap rewrites the absolute build path to a symbolic root. MEASURED: line numbers
#   stay correct, and the absolute path disappears from the stack AND the PDB.
#   Without it the PDB embeds e.g. C:\Users\<name>\... -> username leak if shipped.
#   (PathMap value must be the repo root; keep ASCII-only for PS 5.1.)
$pathMapFrom = ($root.TrimEnd('\'))
$cscArgs = @(
    '/nologo', '/target:library', '/platform:x64', '/optimize+', '/debug:full',
    ('/pathmap:' + $pathMapFrom + '=/'),
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

    # The RELIABLE signal is whether the DLL is *loaded* (= locked). Name matching alone gave
    # false positives: stale "Bannerlord.BLSE.Launcher" processes left over from earlier sessions
    # (CPU 0, no game body) kept `-Deploy` refusing to run, and the DLL had to be synced by hand.
    # So: lock wins, process names only warn. (Same rule as Coop's DeploymentEnvironment:
    # confirm idle before AND during deploy.)
    $active = Get-ActiveGameProcess
    $dllBusy = $false
    if (Test-Path $dllTarget) {
        try {
            $fs = [System.IO.File]::Open($dllTarget, 'Open', 'ReadWrite', 'None')
            $fs.Close()
        } catch {
            $dllBusy = $true
        }
    }
    if ($dllBusy) {
        throw "The deployed DLL is locked (some process has it loaded) -- close the game before deploying."
    }
    if ($active.Count -gt 0) {
        Write-Warning ("Bannerlord* process(es) present (PID {0}) but the DLL is NOT locked -- treating them as stale leftovers, deploying anyway." -f ($active -join ', '))
    }

    if (-not (Test-Path $targetBin)) { New-Item -ItemType Directory -Path $targetBin -Force | Out-Null }

    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $backup = $null
    if (Test-Path $dllTarget) {
        $backup = $dllTarget + '.bak_' + $stamp
        Copy-Item $dllTarget $backup -Force
        # Keep only the 3 newest backups *beside the DLL* (they accumulate otherwise; each is ~40 KB).
        # But do NOT delete the older ones: ARCHIVE them instead.
        #
        # Why (2026-10-08, found while auditing evidence): the previous version deleted them, and the
        # pruning sorts by LastWriteTime -- while the FILENAME stamp is the DEPLOY time, not the BUILD
        # time. The two differ, so one deploy silently removed the two `.bak` files that a written
        # conclusion had cited as its only counter-example ("that DLL lacks scan_bad_data").
        # The claim became impossible to re-check on disk: the evidence had been auto-recycled.
        # Lesson: a cited artifact must be retained, not pruned by a build script.
        #
        # Keeping them in place is not an option (the bin dir should stay tidy and the count unbounded),
        # so they move to a sibling archive dir. Nothing resolves a DLL by listing that directory --
        # every consumer uses the exact `BlBridge.dll` path -- and the game loads only the assembly
        # named in SubModule.xml, so extra non-.dll files there are inert.
        $bakArchive = Join-Path $target 'bak_archive'
        $stale = @(Get-ChildItem (Split-Path $dllTarget -Parent) -Filter ((Split-Path $dllTarget -Leaf) + '.bak_*') |
            Sort-Object LastWriteTime -Descending | Select-Object -Skip 3)
        if ($stale.Count -gt 0) {
            if (-not (Test-Path $bakArchive)) { New-Item -ItemType Directory -Path $bakArchive -Force | Out-Null }
            foreach ($old in $stale) {
                $dest = Join-Path $bakArchive $old.Name
                # Never overwrite an archived copy: same-second stamps are possible, and losing one
                # is exactly the failure this block exists to prevent.
                if (Test-Path $dest) { $dest = $dest + '.' + (Get-Date -Format 'fff') }
                try {
                    Move-Item -LiteralPath $old.FullName -Destination $dest -Force -ErrorAction Stop
                    $h = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash
                    Write-Host ("      archived old backup: {0} -> bak_archive (sha256 {1})" -f $old.Name, $h.Substring(0, 16))
                } catch {
                    Write-Warning ("could not archive {0}: {1}" -f $old.Name, $_.Exception.Message)
                }
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

    # Ship UI resources and localization with the module. Before this, the module carried only
    # the DLL + SubModule.xml; a self-written Gauntlet prefab under module\GUI\Prefabs must land
    # in Modules\BlBridge\GUI\Prefabs, otherwise LoadMovie("<name>", vm) cannot find it.
    foreach ($sub in @('GUI', 'ModuleData')) {
        $from = Join-Path $moduleSrc $sub
        if (-not (Test-Path $from)) { continue }
        $to = Join-Path $target $sub
        if (-not (Test-Path $to)) { New-Item -ItemType Directory -Path $to -Force | Out-Null }
        Copy-Item -Path (Join-Path $from '*') -Destination $to -Recurse -Force
        $n = @(Get-ChildItem $from -Recurse -File).Count
        Write-Host ("      deployed {0}/ ({1} file(s))" -f $sub, $n)
    }

    # ---- MCP sub-package (unit B, v0.8.12 dual package) --------------------
    # Shipped inside the mod on purpose: once the AI has installed the mod it can read
    # mcp\README.md + manifest.json and wire the mod's control channel into MCP tools,
    # so there is no second package to distribute.
    # tools\*.py is the single source of truth; this step only copies. ALL of tools\*.py is
    # copied (stdlib-only, and a missing module would only blow up when some rarely used
    # branch imports it -- the "green on a dry run, explodes on the real run" class of bug).
    # NOTE: this script stays ASCII-only on purpose (PS 5.1 reads a BOM-less .ps1 as ANSI).
    $mcpSrc = Join-Path $moduleSrc 'mcp'
    $mcpTarget = Join-Path $target 'mcp'
    if (-not (Test-Path $mcpTarget)) { New-Item -ItemType Directory -Path $mcpTarget -Force | Out-Null }
    # manifest.json version is single-sourced from BridgeConfig.Version (same rule as
    # SubModule.xml): rewrite the repo copy in place, then copy. Hand-maintained versions
    # drift ("manifest says 0.8.9, DLL says 0.8.12"), which is exactly what we forbid.
    $manifestSrc = Join-Path $mcpSrc 'manifest.json'
    if (Test-Path $manifestSrc) {
        $mj = Get-Content -LiteralPath $manifestSrc -Raw
        $mjNew = [regex]::Replace($mj, '("version"\s*:\s*")[^"]*(")', ('${1}' + $version + '${2}'))
        if ($mjNew -ne $mj) {
            [System.IO.File]::WriteAllText($manifestSrc, $mjNew, $utf8NoBom)
            Write-Host ("      module/mcp/manifest.json version -> {0}" -f $version)
        }
    }
    Copy-Item -Path (Join-Path $mcpSrc '*') -Destination $mcpTarget -Recurse -Force
    Copy-Item -Path (Join-Path $root 'tools\*.py') -Destination $mcpTarget -Force
    # Two NON-.py files are referenced from inside the deployed mcp\ directory and were missed by
    # the '*.py' glob above, so a clean deploy shipped a broken bl_launch_game:
    #   bl_launch.ps1    - bl_mcp.py resolves it via _TOOLS_DIR (same dir as bl_mcp.py):
    #                      bl_launch_game + the load-time modal-dialog answering both run it.
    #   gabp_names.json  - read by bl_check_gabp_names.py (GABP naming alignment check).
    # Copy them explicitly; keep this list in sync if any other non-.py data file is added.
    Copy-Item -Path (Join-Path $root 'tools\bl_launch.ps1') -Destination $mcpTarget -Force
    Copy-Item -Path (Join-Path $root 'tools\gabp_names.json') -Destination $mcpTarget -Force
    $mcpFiles = @(Get-ChildItem $mcpTarget -Recurse -File)
    Write-Host ("      deployed mcp/ ({0} file(s), {1} KB)" -f $mcpFiles.Count,
        [math]::Round((($mcpFiles | Measure-Object -Property Length -Sum).Sum) / 1KB, 1))

    # the user may have launched the game while we were copying -- check once more (by lock, not name)
    $dllBusy2 = $false
    try {
        $fs2 = [System.IO.File]::Open($dllTarget, 'Open', 'ReadWrite', 'None')
        $fs2.Close()
    } catch {
        $dllBusy2 = $true
    }
    if ($dllBusy2) {
        throw "The deployed DLL got locked during the copy -- the game may have started mid-deploy. Rerun after closing it."
    }

    Copy-Item $outDll $dllTarget -Force

    # Deploy the PDB next to the DLL (v0.8.50, 2026-10-07).
    #
    # WHY: measured -- without the PDB the runtime stack has NO line numbers, so
    #      `bl_source_map` can only fall back to the symbol index. With it, frames
    #      from OUR code carry `file:line` directly (the most authoritative source).
    #      Measured first: `-Deploy` copied only the DLL, so the deployed build was
    #      STILL symbol-less even though the build produced a PDB -- the whole
    #      line-number feature silently did nothing in the real (deployed) game.
    #
    # Privacy: the PDB is built with /pathmap, so it carries /src/X.cs -- verified
    #          no build-machine path (no username) is embedded.
    $pdbSrc = [System.IO.Path]::ChangeExtension($outDll, '.pdb')
    if (Test-Path $pdbSrc) {
        Copy-Item $pdbSrc ([System.IO.Path]::ChangeExtension($dllTarget, '.pdb')) -Force
        $pdbHash = (Get-FileHash $pdbSrc -Algorithm SHA256).Hash.Substring(0, 16)
        Write-Host ("      pdb  sha256 (head) = {0}..." -f $pdbHash)
    } else {
        Write-Warning "no PDB produced by the build -- stacks will have no line numbers"
    }

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
