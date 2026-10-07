# Self-test for the module-list existence gate in bl_launch.ps1.
#
# WHY THIS EXISTS (real defect, 2026-10-08): the launcher used to pass a module name from
# LauncherData.xml straight into `_MODULES_` even when that module was NO LONGER on disk.
# The old code only printed a WARNING claiming "the engine will skip them" -- measured
# behaviour is the opposite: the game dies at the loading screen.  Concretely, after
# BannerlordSage was uninstalled (its SubModule.xml gone) but LauncherData still said
# IsSelected=true, every launch through this script crashed while the user's own Steam
# launch worked fine.
#
# Judge pairs (each needs a "must drop" and a "must keep" side, or the check is worthless):
#   A  selected + on disk                -> KEPT
#   B  selected but SubModule.xml gone   -> DROPPED  (this is the crash condition)
#   C  case mismatch (Sandbox vs SandBox)-> KEPT under the on-disk spelling
#   D  duplicate entries                 -> deduped to one
#   E  not selected                      -> never enters the list at all
#
# ASCII ONLY: PowerShell 5.1 decodes a BOM-less .ps1 as the ANSI codepage (cp936 here), so a
# UTF-8 non-ASCII byte can swallow the next newline and merge lines into a comment.  That has
# already produced a bogus "compile failed" in this repo.

param(
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

# ---------------------------------------------------------------------------------
# Reproduce the gate logic against a THROWAWAY fixture, so this test does not depend on
# whatever is currently installed (a test whose result depends on unrelated on-disk state
# reports failures that are really "the fixture moved" -- a mistake already made once).
# ---------------------------------------------------------------------------------
$fixture = Join-Path ([System.IO.Path]::GetTempPath()) ('modgate_' + [Guid]::NewGuid().ToString('N'))
$modulesDir = Join-Path $fixture 'Modules'
New-Item -ItemType Directory -Path $modulesDir -Force | Out-Null

# Good modules (SubModule.xml present)
foreach ($m in @('Native', 'SandBox', 'BlBridge', 'RBM')) {
    New-Item -ItemType Directory -Path (Join-Path $modulesDir $m) -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $modulesDir ($m + '\SubModule.xml')) -Value '<Module/>' -Encoding ASCII
}
# A module directory that exists but has NO SubModule.xml (the BannerlordSage end-state)
New-Item -ItemType Directory -Path (Join-Path $modulesDir 'GoneModule') -Force | Out-Null
Set-Content -LiteralPath (Join-Path $modulesDir 'GoneModule\readme.txt') -Value 'log remnant' -Encoding ASCII

function Invoke-Gate([string[]]$selected) {
    # This mirrors bl_launch.ps1's gate verbatim in structure.
    $resolved = New-Object System.Collections.ArrayList
    $droppedAbsent = New-Object System.Collections.ArrayList
    $fixedCase = New-Object System.Collections.ArrayList
    $seenMods = @{}
    foreach ($name in $selected) {
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        if ($seenMods.ContainsKey($name.ToLowerInvariant())) { continue }
        $seenMods[$name.ToLowerInvariant()] = $true

        $exact = Join-Path $modulesDir ($name + '\SubModule.xml')
        if (Test-Path -LiteralPath $exact) { [void]$resolved.Add($name); continue }

        $real = $null
        if (Test-Path -LiteralPath $modulesDir) {
            $real = Get-ChildItem -LiteralPath $modulesDir -Directory -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -ieq $name } | Select-Object -First 1
        }
        if ($real -and (Test-Path -LiteralPath (Join-Path $real.FullName 'SubModule.xml'))) {
            [void]$resolved.Add($real.Name)
            if ($real.Name -cne $name) { [void]$fixedCase.Add(($name + ' -> ' + $real.Name)) }
            continue
        }
        [void]$droppedAbsent.Add($name)
    }
    return [pscustomobject]@{
        Resolved = @($resolved); Dropped = @($droppedAbsent); FixedCase = @($fixedCase)
    }
}

Write-Host ''
Write-Host '[A] selected and on disk -> KEPT'
$r = Invoke-Gate @('Native', 'BlBridge')
Check 'A1 Native kept'   ($r.Resolved -contains 'Native')   ($r.Resolved -join ',')
Check 'A2 BlBridge kept' ($r.Resolved -contains 'BlBridge') ($r.Resolved -join ',')
Check 'A3 nothing dropped' ($r.Dropped.Count -eq 0) ($r.Dropped -join ',')

Write-Host ''
Write-Host '[B] selected but SubModule.xml gone -> DROPPED (the crash condition)'
$r = Invoke-Gate @('Native', 'GoneModule', 'BlBridge')
Check 'B1 GoneModule DROPPED'      ($r.Dropped -contains 'GoneModule')  ($r.Dropped -join ',')
Check 'B2 GoneModule NOT in list'  (-not ($r.Resolved -contains 'GoneModule')) ($r.Resolved -join ',')
Check 'B3 the good ones survive'   (($r.Resolved -contains 'Native') -and ($r.Resolved -contains 'BlBridge')) ($r.Resolved -join ',')
$r = Invoke-Gate @('GhostModuleNotOnDiskAtAll')
Check 'B4 a name with no directory at all is dropped too' ($r.Dropped -contains 'GhostModuleNotOnDiskAtAll') ($r.Dropped -join ',')

Write-Host ''
Write-Host '[C] case mismatch -> resolved to the on-disk spelling'
$r = Invoke-Gate @('sandbox')          # disk has SandBox
# NOTE (measured): on Windows, Test-Path itself is case-INSENSITIVE, so 'sandbox\SubModule.xml'
# hits the exact branch and the case-fallback code is normally NOT reached.  That is fine --
# the outcome we care about is that the module is KEPT and the list carries a usable name.
# So assert the OUTCOME, not which internal branch ran: asserting the branch is how the first
# version of this test failed for the wrong reason.
Check 'C1 resolved to SandBox (not dropped)' ($r.Resolved -contains 'SandBox') ($r.Resolved -join ',')
Check 'C2 not dropped' ($r.Dropped.Count -eq 0) ($r.Dropped -join ',')
Check 'C3 no duplicate spelling sneaks in' ((@($r.Resolved).Count) -eq 1) ($r.Resolved -join ',')

Write-Host ''
Write-Host '[D] duplicates -> deduped'
$r = Invoke-Gate @('Native', 'Native', 'BlBridge', 'blbridge')
Check 'D1 Native appears once' ((@($r.Resolved | Where-Object { $_ -eq 'Native' })).Count -eq 1) ($r.Resolved -join ',')
Check 'D2 BlBridge appears once' ((@($r.Resolved | Where-Object { $_ -ieq 'BlBridge' })).Count -eq 1) ($r.Resolved -join ',')
Check 'D3 order preserved' ((@($r.Resolved)[0] -eq 'Native') -and (@($r.Resolved)[1] -eq 'BlBridge')) ($r.Resolved -join ',')

Write-Host ''
Write-Host '[E] the real-world end-state from 2026-10-08'
# LauncherData said BannerlordSage selected, but only its log remnant survived.
# (Bannerlord.Harmony must exist on disk in this fixture too, otherwise it is -- correctly --
#  dropped and the count assertion fails for the wrong reason.  That was the first version's
#  mistake: the fixture was incomplete and the test blamed the code.)
foreach ($m in @('Bannerlord.Harmony')) {
    New-Item -ItemType Directory -Path (Join-Path $modulesDir $m) -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $modulesDir ($m + '\SubModule.xml')) -Value '<Module/>' -Encoding ASCII
}
New-Item -ItemType Directory -Path (Join-Path $modulesDir 'BannerlordSage') -Force | Out-Null
Set-Content -LiteralPath (Join-Path $modulesDir 'BannerlordSage\__folder_managed_by_vortex') -Value 'x' -Encoding ASCII
$r = Invoke-Gate @('Bannerlord.Harmony', 'Native', 'BannerlordSage', 'RBM')
Check 'E1 BannerlordSage dropped (this is what crashed the load)' ($r.Dropped -contains 'BannerlordSage') ($r.Dropped -join ',')
Check 'E2 the other three keep loading' ((@($r.Resolved).Count) -eq 3) ($r.Resolved -join ',')
Check 'E3 the wildcard string has no absent name' (-not (($r.Resolved -join '*') -match 'BannerlordSage')) ($r.Resolved -join '*')

Remove-Item -LiteralPath $fixture -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ''
if ($fails.Count -gt 0) {
    Write-Host ("FAILED " + $fails.Count + " check(s):")
    foreach ($f in $fails) { Write-Host ("  - " + $f) }
    exit 1
}
Write-Host 'ALL PASS'
exit 0
