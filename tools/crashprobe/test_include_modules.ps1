# Unit-test the -IncludeModules fragment from bl_launch.ps1 WITHOUT launching the game.
#
# WHY: a bug here has a silent, expensive failure mode -- if the module never enters the
# `_MODULES_` string, the game starts and the probe simply never loads.  That looks exactly
# like "the probe is broken" and would send us debugging the wrong thing.
#
# Judge pairs (each with its "should / should not" side):
#   A  append succeeds and the name lands in the module string
#   B  a name already in the list is NOT duplicated
#   C  a typo (no SubModule.xml on disk) ABORTS instead of silently doing nothing
#   D  comma-separated form works (same trap -ExcludeModules documented)
#
# NOTE: ASCII ONLY on purpose.  PowerShell 5.1 decodes a BOM-less .ps1 as the ANSI code page
# (cp936 here), so a UTF-8 Chinese comment can swallow the following newline and merge the next
# line into the comment (see AGENTS.md).  Non-ASCII would also make this file fragile to edit.

param(
    [string]$Repo = 'C:\Users\LCGX\CodeBuddy\20260923171333\BlBridge',
    [string]$GameDir = 'G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord'
)

$ErrorActionPreference = 'Stop'
$fails = New-Object System.Collections.ArrayList

function Check([string]$name, [bool]$cond, [string]$detail) {
    if ($cond) { Write-Host ("  [OK]   " + $name) }
    else {
        Write-Host ("  [FAIL] " + $name + "  " + $detail)
        [void]$fails.Add($name + " :: " + $detail)
    }
}

# --- Extract the fragment from the real script (avoids copy/paste drift) ---
$src = Get-Content -LiteralPath (Join-Path $Repo 'tools\bl_launch.ps1') -Raw
$startMark = '# ---------- -IncludeModules: append modules for THIS launch only'
$endMark = 'if ($ExcludeModules.Count -gt 0) {'
$i = $src.IndexOf($startMark)
$j = $src.IndexOf($endMark)
if ($i -lt 0 -or $j -lt 0 -or $j -le $i) { Write-Host 'could not extract fragment'; exit 2 }
$fragment = $src.Substring($i, $j - $i)
Write-Host ("fragment: {0} chars" -f $fragment.Length)

function Run-Include([string[]]$include, [string[]]$selected, [string]$modulesDir) {
    # Mimic the variables the fragment depends on.
    $IncludeModules = $include
    $modulesDir = $modulesDir
    $mods = '_MODULES_*' + ($selected -join '*') + '*_MODULES_'
    $threw = $false
    $errMsg = ''
    try { Invoke-Expression $fragment } catch { $threw = $true; $errMsg = $_.Exception.Message }
    return [pscustomobject]@{ Mods = $mods; Selected = $selected; Threw = $threw; Err = $errMsg }
}

$modulesDir = Join-Path $GameDir 'Modules'

# ---------------------------------------------------------------------------
# FIXTURE, not the live Modules directory.
#
# WHY (learned the hard way, 2026-10-07): the first version pointed the checks at the real
# `<game>\Modules\ZzCrashProbe`.  That made the test depend on the probe being deployed -- so
# after `cleanup` removed it, D2/D3 failed even though the LOGIC was fine.  A test whose result
# depends on unrelated on-disk state reports failures that are really just "the fixture is gone".
# The fragment only consults SubModule.xml existence, so a throwaway fixture exercises it fully.
# ---------------------------------------------------------------------------
$fixture = Join-Path ([System.IO.Path]::GetTempPath()) ('incmod_' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $fixture 'ZzCrashProbe') -Force | Out-Null
Set-Content -LiteralPath (Join-Path $fixture 'ZzCrashProbe\SubModule.xml') -Value '<Module/>' -Encoding ASCII
New-Item -ItemType Directory -Path (Join-Path $fixture 'BlBridge') -Force | Out-Null
Set-Content -LiteralPath (Join-Path $fixture 'BlBridge\SubModule.xml') -Value '<Module/>' -Encoding ASCII
$modulesDir = $fixture
Write-Host ("fixture: " + $fixture)

Write-Host ''
Write-Host '[A] append: name lands in the module string'
$r = Run-Include @('ZzCrashProbe') @('Native', 'BlBridge') $modulesDir
Check 'A1 no throw' (-not $r.Threw) $r.Err
Check 'A2 name present in _MODULES_' ($r.Mods -like '*ZzCrashProbe*') $r.Mods
Check 'A3 name is the LAST entry (loads after BlBridge)' ($r.Mods.EndsWith('*ZzCrashProbe*_MODULES_')) $r.Mods
Check 'A4 original names kept' (($r.Mods -like '*Native*') -and ($r.Mods -like '*BlBridge*')) $r.Mods

Write-Host ''
Write-Host '[B] already present: no duplicate'
$r = Run-Include @('ZzCrashProbe') @('Native', 'ZzCrashProbe') $modulesDir
$cnt = ([regex]::Matches($r.Mods, 'ZzCrashProbe')).Count
Check 'B1 appears exactly once' ($cnt -eq 1) ("count=" + $cnt + "  " + $r.Mods)

Write-Host ''
Write-Host '[C] typo: must ABORT (not silently do nothing)'
$r = Run-Include @('NoSuchModuleXyz') @('Native') $modulesDir
Check 'C1 throws' ($r.Threw) 'did not throw -- a typo would silently produce a probe-less launch'
Check 'C2 error names the module' ($r.Err -like '*NoSuchModuleXyz*') $r.Err

Write-Host ''
Write-Host '[D] comma-separated form'
$r = Run-Include @('ZzCrashProbe,BlBridge') @('Native') $modulesDir
Check 'D1 both handled, no throw' (-not $r.Threw) $r.Err
Check 'D2 ZzCrashProbe present' ($r.Mods -like '*ZzCrashProbe*') $r.Mods
Check 'D3 BlBridge not duplicated' ((([regex]::Matches($r.Mods, 'BlBridge')).Count) -eq 1) $r.Mods

Write-Host ''
# Always remove the fixture (test must not leave residue in TEMP).
Remove-Item -LiteralPath $fixture -Recurse -Force -ErrorAction SilentlyContinue

if ($fails.Count -gt 0) {
    Write-Host ("FAILED " + $fails.Count + " check(s):")
    foreach ($f in $fails) { Write-Host ("  - " + $f) }
    exit 1
}
Write-Host 'ALL PASS'
exit 0
