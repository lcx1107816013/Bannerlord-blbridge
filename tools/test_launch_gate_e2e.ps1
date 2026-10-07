# End-to-end test of the REAL bl_launch.ps1 gate, against a REAL stale entry.
#
# WHY: the unit test above reproduces the gate logic, but reproduction is not the same as
# "the shipped script actually drops it".  This test drives the real script and checks its
# printed module count changes when a stale selection is present.
#
# HOW: point the script at a COPY of the live LauncherData.xml?  No -- the script hardcodes
# the user's path, and editing the user's real file to test would be reckless.  Instead we
# back up the real file, inject ONE stale entry (a module name that is selected but has no
# SubModule.xml on disk), run the launcher in answer-only mode, then RESTORE the file and
# verify the restore byte-for-byte.
#
# ASCII only (PS 5.1 decodes BOM-less .ps1 as cp936 here; non-ASCII has already caused a
# bogus "compile failed" in this repo).

param(
    [string]$Repo = 'C:\Users\LCGX\CodeBuddy\20260923171333\BlBridge',
    [string]$LauncherData = "$env:USERPROFILE\Documents\Mount and Blade II Bannerlord\Configs\LauncherData.xml"
)

$ErrorActionPreference = 'Stop'
$fails = New-Object System.Collections.ArrayList

function Check([string]$name, [bool]$cond, [string]$detail) {
    if ($cond) { Write-Host ("  [OK]   " + $name) }
    else { Write-Host ("  [FAIL] " + $name + "  " + $detail); [void]$fails.Add($name + " :: " + $detail) }
}

function Get-Count([string]$text, [string]$pattern) {
    $m = [regex]::Match($text, $pattern)
    if ($m.Success) { return [int]$m.Groups[1].Value }
    return -1
}

# ---- baseline run -----------------------------------------------------------------
Write-Host '[1] baseline (live LauncherData as-is)'
$out1 = & powershell -ExecutionPolicy Bypass -File (Join-Path $Repo 'tools\bl_launch.ps1') -AnswerSec 3 2>&1 | Out-String
$n1 = Get-Count $out1 'module list ready: (\d+) module'
Write-Host ("    ready=" + $n1)
Check 'baseline reports a module count' ($n1 -gt 0) $out1.Substring(0, [Math]::Min(200, $out1.Length))
Check 'baseline has nothing dropped' ($out1 -notmatch 'DROPPED module') 'unexpected DROPPED in baseline'

# ---- inject a stale selection -----------------------------------------------------
Write-Host ''
Write-Host '[2] inject one stale entry (selected, but no SubModule.xml on disk)'
$backup = [System.IO.Path]::GetTempFileName()
Copy-Item -LiteralPath $LauncherData -Destination $backup -Force
$origBytes = [System.IO.File]::ReadAllBytes($LauncherData)

try {
    $raw = [System.IO.File]::ReadAllText($LauncherData, [System.Text.Encoding]::UTF8)
    # Pick a name that certainly has no directory on disk.
    $ghost = 'ZzzGhostModuleQaTest'
    $inject = "<UserModData>`r`n        <Id>$ghost</Id>`r`n        <LastKnownVersion>v0.0.0</LastKnownVersion>`r`n        <IsSelected>true</IsSelected>`r`n      </UserModData>`r`n      "
    $idx = $raw.IndexOf('<UserModData>')
    $raw2 = $raw.Insert($idx, $inject)
    [System.IO.File]::WriteAllText($LauncherData, $raw2, (New-Object System.Text.UTF8Encoding($true)))
    Write-Host ("    injected " + $ghost)

    $out2 = & powershell -ExecutionPolicy Bypass -File (Join-Path $Repo 'tools\bl_launch.ps1') -AnswerSec 3 2>&1 | Out-String
    $n2 = Get-Count $out2 'module list ready: (\d+) module'
    Write-Host ("    ready=" + $n2)
    $droppedLine = ($out2 -split "`n" | Where-Object { $_ -match 'DROPPED module' }) -join ' '
    Write-Host ("    " + $droppedLine.Trim())

    Check 'A stale entry is DROPPED (not passed to the engine)' ($out2 -match 'DROPPED module') 'no DROPPED line emitted'
    Check 'B the ghost module is named in the warning' ($out2 -match [regex]::Escape($ghost)) 'ghost name missing from output'
    Check 'C the ready count is unchanged vs baseline (ghost not counted)' ($n2 -eq $n1) ("baseline=" + $n1 + " injected=" + $n2)
    Check 'D the reason is printed (loading-screen crash)' ($out2 -match 'crashes the game at the loading screen') 'reason line missing'
    Check 'E it states LauncherData.xml is not modified' ($out2 -match 'LauncherData\.xml itself is NOT modified') 'not-modified note missing'
}
finally {
    # Restore byte-for-byte and verify.
    Copy-Item -LiteralPath $backup -Destination $LauncherData -Force
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    $nowBytes = [System.IO.File]::ReadAllBytes($LauncherData)
    $same = ($nowBytes.Length -eq $origBytes.Length)
    if ($same) {
        for ($i = 0; $i -lt $nowBytes.Length; $i++) {
            if ($nowBytes[$i] -ne $origBytes[$i]) { $same = $false; break }
        }
    }
    Write-Host ''
    Write-Host '[3] restore check'
    Check 'F LauncherData.xml restored byte-for-byte' $same ("len orig=" + $origBytes.Length + " now=" + $nowBytes.Length)
}

Write-Host ''
if ($fails.Count -gt 0) {
    Write-Host ("FAILED " + $fails.Count + " check(s):")
    foreach ($f in $fails) { Write-Host ("  - " + $f) }
    exit 1
}
Write-Host 'ALL PASS'
exit 0
