#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Repair the BLSE deployment that Vortex symlinks, which stops the game starting.

.DESCRIPTION
    THE PROBLEM
    -----------
    Vortex's staging directory is on C: and the game is on G: -- different volumes.
    So Vortex can only deploy with SYMLINKS. That is fine for most mods, but it is
    fatal for BLSE:

        bin\Win64_Shipping_Client\Bannerlord.BLSE.Standalone.exe
          -> C:\Users\...\Vortex\...\BLSE 1.7.2...\bin\Win64_Shipping_Client\...

    Windows resolves the process's real path to the C: copy. BLSE walks up two
    levels from its own path to find the game root, lands on the Vortex staging
    directory, finds no Modules\ there, and dies with DirectoryNotFoundException.
    A dialog appears and the process exits.

    bl_launch.ps1 dismisses that dialog automatically, so from outside all you see is
    "LAUNCH FAILED (no game window within timeout)". The cause was handled away.

    WHY NO VORTEX SETTING FIXES THIS PERMANENTLY
    --------------------------------------------
        Symlink (current)  works across volumes, but BLSE derives the wrong root
        Hardlink           REQUIRES THE SAME VOLUME. C: -> G: cannot be hardlinked
        Copy               runs, but Vortex then no longer knows what is in the game
                           directory and stops syncing changes

    So the only workable shape is: put real copies of those 9 files into the game's
    bin. BLSE lives in the game's bin, walks up two levels, and lands on the game
    root -- which is where it needs to be.

    THE COST, STATED PLAINLY
    ------------------------
    Vortex will no longer consider those 9 files its own. Therefore:
      * after every Vortex "Deploy Mods", run this script again
      * before upgrading BLSE, run with -Revert so Vortex takes them back

.PARAMETER GameDir
    Game root. Auto-detected from the Steam library list when omitted.

.PARAMETER Check
    Report only, change nothing. Exit 0 = healthy, 1 = needs repair.

.PARAMETER Revert
    Replace the real files with symlinks again, handing them back to Vortex.

.EXAMPLE
    .\fix-blse-deploy.ps1 -Check     # is it broken right now?
.EXAMPLE
    .\fix-blse-deploy.ps1            # repair
.EXAMPLE
    .\fix-blse-deploy.ps1 -Revert    # hand back to Vortex before a BLSE upgrade
#>
[CmdletBinding()]
param(
    [string]$GameDir,
    [switch]$Check,
    [switch]$Revert
)

# NOTE ON LANGUAGE: this file is pure ASCII ON PURPOSE.
#
# The repo's other .ps1 files contain zero non-ASCII characters, and that is not an
# accident -- it is the only way to be safe across PowerShell 5.1 (which reads a
# BOM-less file as the ANSI code page) and PowerShell 7 (which reads it as UTF-8).
# The first version of THIS file had Chinese strings and no BOM; under 5.1 the
# characters were mojibake and the parser failed with "missing terminator". Writing
# English here costs a translation and buys an invariant.

$ErrorActionPreference = 'Stop'

# The full BLSE payload per vortex.deployment.bannerlord-blse.json.
$BLSE_FILES = @(
    'Bannerlord.BLSE.AppDomainManager.dll'
    'Bannerlord.BLSE.Launcher.exe'
    'Bannerlord.BLSE.Launcher.exe.config'
    'Bannerlord.BLSE.LauncherEx.exe'
    'Bannerlord.BLSE.LauncherEx.exe.config'
    'Bannerlord.BLSE.Shared.dll'
    'Bannerlord.BLSE.Standalone.exe'
    'Bannerlord.BLSE.Standalone.exe.config'
    'TaleWorlds.MountAndBlade.Launcher.exe.config'
)

function Find-GameDir {
    # Steam libraries can live on any drive, so consult the library list rather than
    # guessing drive letters. This is the same mistake ("no game installed") that
    # cost this project a whole defect once: a universal negative needs an exhaustive
    # search, and 'C:' 'D:' 'E:' is not exhaustive.
    $steamRoots = @()
    foreach ($k in @('HKCU:\SOFTWARE\Valve\Steam', 'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam')) {
        try {
            $p = (Get-ItemProperty -Path $k -ErrorAction Stop).SteamPath
            if ($p) { $steamRoots += ($p -replace '/', '\') }
        } catch { }
    }
    foreach ($root in $steamRoots) {
        $vdf = Join-Path $root 'steamapps\libraryfolders.vdf'
        if (-not (Test-Path $vdf)) { continue }
        $hits = [regex]::Matches((Get-Content -LiteralPath $vdf -Raw),
                                 '"path"\s+"(?<p>[^"]+)"')
        foreach ($h in $hits) {
            # VDF escapes backslashes; Group value may carry a trailing space.
            $lib = $h.Groups['p'].Value.Replace('\\', '\').Trim()
            $cand = Join-Path $lib 'steamapps\common\Mount & Blade II Bannerlord'
            if (Test-Path -LiteralPath $cand) { return $cand }
        }
    }
    return $null
}

if (-not $GameDir) { $GameDir = Find-GameDir }
if (-not $GameDir) {
    Write-Host "ERROR: cannot locate the game. Pass -GameDir." -ForegroundColor Red
    exit 3
}
if (-not (Test-Path -LiteralPath $GameDir)) {
    Write-Host "ERROR: game directory does not exist: $GameDir" -ForegroundColor Red
    exit 3
}
$GameDir = (Resolve-Path -LiteralPath $GameDir).Path.Trim()
$binDir  = Join-Path $GameDir 'bin\Win64_Shipping_Client'

Write-Host "game : $GameDir" -ForegroundColor DarkGray
Write-Host "bin  : $binDir" -ForegroundColor DarkGray
Write-Host ""

if (-not (Test-Path -LiteralPath $binDir)) {
    Write-Host "ERROR: bin directory not found: $binDir" -ForegroundColor Red
    exit 3
}

# Classify each payload file: symlink / real / missing.
$report = foreach ($f in $BLSE_FILES) {
    $p = Join-Path $binDir $f
    if (-not (Test-Path -LiteralPath $p)) {
        [pscustomobject]@{ Name = $f; State = 'missing'; Target = ''; Size = 0 }
        continue
    }
    $item   = Get-Item -LiteralPath $p -Force
    $isLink = [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
    [pscustomobject]@{
        Name   = $f
        State  = if ($isLink) { 'link' } else { 'real' }
        Target = if ($isLink) { $item.Target } else { '' }
        Size   = $item.Length
    }
}

$links   = @($report | Where-Object { $_.State -eq 'link' })
$reals   = @($report | Where-Object { $_.State -eq 'real' })
$missing = @($report | Where-Object { $_.State -eq 'missing' })

Write-Host ("links {0} / real {1} / missing {2}" -f $links.Count, $reals.Count, $missing.Count) -ForegroundColor Yellow
foreach ($r in $report) {
    $color = switch ($r.State) { 'real' { 'Green' } 'link' { 'Red' } default { 'DarkYellow' } }
    Write-Host ("  {0,-46} {1,-8} {2,12:N0} B" -f $r.Name, $r.State, $r.Size) -ForegroundColor $color
}
Write-Host ""

# The game starts or not depending on ONE file: Standalone.exe. The other eight
# follow it, but the verdict is that one.
#
# VERDICT RULE -- must be "not healthy", not "is a link".
#
# The first version read `$needsFix = ($standalone.State -eq 'link')`, and a test run
# proved it wrong: with Standalone.exe DELETED the script printed
#     links 0 / real 8 / missing 1
#     OK: BLSE files are real copies. The game can start.
# A missing launcher is not "not a link", it is "cannot start". Checking one fault
# shape and reporting health on every other shape is this project's recurring bug, so
# the rule is now phrased as health, and any state other than 'real' blocks.
$standalone = $report | Where-Object { $_.Name -eq 'Bannerlord.BLSE.Standalone.exe' }
$needsFix    = ($standalone.State -ne 'real')
$brokenShape = $standalone.State   # 'link' | 'missing' | 'real'

if ($Check) {
    if ($needsFix) {
        if ($brokenShape -eq 'link') {
            Write-Host "BROKEN: BLSE is a symlink. The game will show 'Error from BLSE!' and exit." -ForegroundColor Red
            Write-Host "Repair: .\fix-blse-deploy.ps1" -ForegroundColor Yellow
        } else {
            Write-Host "BROKEN: Standalone.exe is MISSING. The game cannot start at all." -ForegroundColor Red
            Write-Host "Repair: .\fix-blse-deploy.ps1  (it will copy it back from staging)" -ForegroundColor Yellow
        }
        exit 1
    }
    Write-Host "OK: BLSE files are real copies. The game can start." -ForegroundColor Green
    exit 0
}

# ── Revert: hand the files back to Vortex as symlinks ─────────────────────
if ($Revert) {
    if ($reals.Count -eq 0) {
        Write-Host "Nothing to revert: everything is already a symlink." -ForegroundColor DarkGray
        exit 0
    }
    # Prefer the target recorded on any existing link; fall back to scanning staging.
    $stageBin = $null
    foreach ($r in $report) {
        if ($r.Target) { $stageBin = Split-Path $r.Target -Parent; break }
    }
    if (-not $stageBin) {
        $stageRoot = Join-Path $env:APPDATA 'Vortex\mountandblade2bannerlord\mods'
        $blse = Get-ChildItem -LiteralPath $stageRoot -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like 'Bannerlord Software Extender (BLSE)*' } |
                Select-Object -First 1
        if ($blse) { $stageBin = Join-Path $blse.FullName 'bin\Win64_Shipping_Client' }
    }
    if (-not $stageBin -or -not (Test-Path -LiteralPath $stageBin)) {
        Write-Host "ERROR: cannot find the Vortex staging copy. Use Vortex's Deploy Mods instead." -ForegroundColor Red
        exit 3
    }
    Write-Host "Handing back to Vortex: $stageBin" -ForegroundColor Yellow
    foreach ($r in $reals) {
        $dest = Join-Path $binDir $r.Name
        $src  = Join-Path $stageBin $r.Name
        if (-not (Test-Path -LiteralPath $src)) { Write-Host "  skip (no source): $($r.Name)" -ForegroundColor DarkYellow; continue }
        Remove-Item -LiteralPath $dest -Force
        try {
            New-Item -ItemType SymbolicLink -Path $dest -Target $src -ErrorAction Stop | Out-Null
            Write-Host "  relinked $($r.Name)" -ForegroundColor Green
        } catch {
            Copy-Item -LiteralPath $src -Destination $dest -Force
            Write-Host "  copied (needs admin for a link) $($r.Name)" -ForegroundColor DarkYellow
        }
    }
    exit 0
}

# ── Fix: replace symlinks with real copies, and restore anything missing ──
if (-not $needsFix) {
    Write-Host "Nothing to do: BLSE files are already real copies." -ForegroundColor Green
    exit 0
}

# Two fault shapes, both repaired from the same source:
#   link    -- delete the link, copy the bytes
#   missing -- just copy the bytes
# The fix used to iterate `$links` only, so a MISSING file was reported by the
# summary and then ignored by the repair. Same blind spot as the verdict, one layer
# down: the loop could see one kind of damage.
$stageBin = $null
foreach ($r in $report) {
    if ($r.Target) { $stageBin = Split-Path $r.Target -Parent; break }
}
if (-not $stageBin) {
    $stageRoot = Join-Path $env:APPDATA 'Vortex\mountandblade2bannerlord\mods'
    $blse = Get-ChildItem -LiteralPath $stageRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like 'Bannerlord Software Extender (BLSE)*' } |
            Select-Object -First 1
    if ($blse) { $stageBin = Join-Path $blse.FullName 'bin\Win64_Shipping_Client' }
}
Write-Host "source: $stageBin" -ForegroundColor DarkGray

$toRepair = @($report | Where-Object { $_.State -ne 'real' })
Write-Host "Repairing $($toRepair.Count) file(s)" -ForegroundColor Yellow
$failed = 0
foreach ($r in $toRepair) {
    $dest = Join-Path $binDir $r.Name
    # A link has a recorded target; a missing file has none, so fall back to staging.
    $src = if ($r.Target) { $r.Target } else { Join-Path $stageBin $r.Name }

    if (-not $src -or -not (Test-Path -LiteralPath $src)) {
        Write-Host "  FAIL $($r.Name): no source to copy from ($src)" -ForegroundColor Red
        $failed++
        continue
    }
    try {
        if ($r.State -eq 'link') {
            # Delete the LINK itself (never the target), then copy the real bytes in.
            [System.IO.File]::Delete($dest)
        }
        Copy-Item -LiteralPath $src -Destination $dest -Force
        $item = Get-Item -LiteralPath $dest -Force
        $stillLink = [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
        if ($stillLink -or $item.Length -eq 0) {
            Write-Host ("  BAD  {0,-44} link={1} size={2}" -f $r.Name, $stillLink, $item.Length) -ForegroundColor Red
            $failed++
        } else {
            Write-Host ("  ok   {0,-44} {1:N0} B  (was {2})" -f $r.Name, $item.Length, $r.State) -ForegroundColor Green
        }
    } catch {
        Write-Host ("  FAIL {0,-44} {1}" -f $r.Name, $_.Exception.Message) -ForegroundColor Red
        $failed++
    }
}

Write-Host ""
if ($failed -gt 0) {
    Write-Host "FAILED: $failed file(s) not repaired." -ForegroundColor Red
    exit 1
}

$sa = Get-Item -LiteralPath (Join-Path $binDir 'Bannerlord.BLSE.Standalone.exe') -Force
$saLink = [bool]($sa.Attributes -band [IO.FileAttributes]::ReparsePoint)
Write-Host ("REPAIRED: Standalone.exe = {0:N0} B, isLink = {1}" -f $sa.Length, $saLink) -ForegroundColor Green
Write-Host ""
Write-Host "Next: start the game, then verify." -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File D:\GitHub\Bannerlord-blbridge\tools\bl_launch.ps1" -ForegroundColor DarkGray
Write-Host "  cd D:\GitHub\Bannerlord-blbridge\tools; python bl_live_verify.py" -ForegroundColor DarkGray
Write-Host ""
Write-Host "WARNING: re-run this script after every Vortex 'Deploy Mods'." -ForegroundColor Yellow
exit 0
