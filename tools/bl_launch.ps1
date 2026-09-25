# BlBridge - unattended game launch through BLSE.
#
# WHY THIS FILE EXISTS
# --------------------
# BlBridge's purpose is that an agent can run AI-vs-AI battles by itself.  That only works if the
# agent can also START the game, and on this machine the game starts through BLSE.  A naive
# Start-Process misses two things (both learned the hard way on 2026-09-25):
#
#   1) THE MODULE LIST.  Bannerlord.BLSE.Standalone.exe WITHOUT `_MODULES_*...*_MODULES_` starts
#      the game in "no mods" mode: the SubModule.xml files are still scanned, but no community
#      module is activated, so BlBridge never loads.  Observed: `Command Args: no_watchdog` and
#      zero new lines in proto_ui.log / bridge_status.json.  The list below is copied from a run
#      that DID load everything (see the `Command Args:` line in
#      C:\ProgramData\Mount and Blade II Bannerlord\logs\rgl_log_<pid>.txt of a good run) --
#      keep the order, and keep BlBridge in it.
#
#   2) TWO MODAL DIALOGS shown BEFORE the game window appears.  If nobody answers them the
#      launcher gives up and the game never starts:
#        "Safe Mode"            - after an unclean shutdown ("Game shut down unexpectedly on
#                                 previous session...").  Answer NO: safe mode disables mods.
#                                 WM_COMMAND with IDNO (7) works on this one.
#        "Mod change detected"  - after module files changed ("...Deleting the runtime shader
#                                 cache...").  Answering OK is required.  Its button is a Chinese
#                                 "确定" and WM_COMMAND IDOK(1) did NOT work; a plain ENTER did.
#      So: Safe Mode -> PostMessage IDNO, Mod change -> SendKeys ENTER.
#
# Verifying success WITHOUT looking at the screen:
#   * a new `Register: AddInitialStateOption OK` line in
#     <Documents>\Mount and Blade II Bannerlord\BlBridge\proto_ui.log (BlBridge loaded), and
#   * a game window whose title contains "Mount and Blade II Bannerlord".
#
# USAGE
#   powershell -ExecutionPolicy Bypass -File tools\bl_launch.ps1
#   powershell -ExecutionPolicy Bypass -File tools\bl_launch.ps1 -TimeoutSec 240
#   powershell -ExecutionPolicy Bypass -File tools\bl_launch.ps1 -GameDir 'D:\...\Bannerlord'
#
# Exit code 0 = game window appeared (dialogs handled), 1 = timeout / launcher died.
#
# NOTE: keep this file ASCII-only.  PowerShell 5.1 reads a BOM-less UTF-8 .ps1 as ANSI, which
# would corrupt non-ASCII text (see AGENTS.md, section 1).

param(
    [string]$GameDir = 'G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord',
    [int]$TimeoutSec = 180,
    [switch]$SkipModuleList
)

$ErrorActionPreference = 'Continue'

Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class BlLaunch {
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
    public static List<string> Top() {
        var res = new List<string>();
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h)) return true;
            int len = GetWindowTextLength(h);
            if (len == 0) return true;
            var sb = new StringBuilder(len + 1);
            GetWindowText(h, sb, sb.Capacity);
            uint pid; GetWindowThreadProcessId(h, out pid);
            res.Add(h.ToInt64() + "|" + pid + "|" + sb.ToString());
            return true;
        }, IntPtr.Zero);
        return res;
    }
    public static IntPtr Find(string needle) {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h)) return true;
            int len = GetWindowTextLength(h);
            if (len == 0) return true;
            var sb = new StringBuilder(len + 1);
            GetWindowText(h, sb, sb.Capacity);
            if (sb.ToString().Contains(needle)) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
"@

$exe = Join-Path $GameDir 'bin\Win64_Shipping_Client\Bannerlord.BLSE.Standalone.exe'
if (-not (Test-Path $exe)) { Write-Host "LAUNCH FAILED: not found: $exe"; exit 1 }

# Module list, order matters (Harmony/ButterLib/UIExtenderEx/MCM must come first).  Copied from
# a known-good run; update it the same way if the module set changes.
$mods = '_MODULES_*Bannerlord.Harmony*Bannerlord.ButterLib*Bannerlord.UIExtenderEx*Bannerlord.MBOptionScreen*Native*SandBoxCore*Sandbox*CustomBattle*StoryMode*BirthAndDeath*FastMode*NavalDLC*WarlordsBattlefieldWarSailsEdition*BannerFix*Bloodlust*Warbandlord*WarbandlordBloodlustFix*PerfectFireArrows*RaiseYourTorch*RaiseYourBanner*MutliLittleFixes*T7TroopUnlocker*CharacterReload*BetterBanditsPlus*StrategicCampaignAI*Byzantium1071*HarvestAndProduction*BellumCivile*customloot*WanderersInParties*Bannerlord.EquipBestItem*GovernorsGonnaGovern*HeroesEvolve*BannerWand*RTSCamera*RTSCamera.CommandSystem*RealisticWeather*MBGA_AchievementEnabler*MBGA_ModernHealthBar*WB_CN*SiegeAIFix*BannerlordSage*BlBridge*_MODULES_'

$argList = @('/singleplayer')
if (-not $SkipModuleList) { $argList += $mods }
$argList += 'no_watchdog'

Write-Host ("launching: " + $exe)
if ($SkipModuleList) { Write-Host "WARNING: -SkipModuleList given; the game will start WITHOUT community mods" }
$p = Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory (Split-Path $exe -Parent) -PassThru
Write-Host ("launcher pid = " + $p.Id)

$handled = @{}
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$window = [IntPtr]::Zero
$shell = New-Object -ComObject WScript.Shell

while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 400

    foreach ($w in [BlLaunch]::Top()) {
        $parts = $w.Split('|')
        if ($parts.Count -lt 3) { continue }
        $h = $parts[0]; $title = $parts[2]
        if ($title -eq '' -or $handled.ContainsKey($h)) { continue }
        if ($title -match 'Reasonix|Vortex|Edge|QQ|Cua|HiddenDialog|Program Manager|cockpit') { continue }
        # only windows that belong to the launcher process (or the game itself)
        $owner = 0
        [void][int]::TryParse($parts[1], [ref]$owner)
        $isOurs = ($owner -eq $p.Id)
        if (-not $isOurs -and ($title -notmatch 'Mount and Blade II Bannerlord')) { continue }

        if ($title -match 'Safe Mode') {
            Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] dialog 'Safe Mode' -> answering NO (IDNO)")
            [BlLaunch]::PostMessage([IntPtr][int64]$h, 0x0111, [IntPtr]7, [IntPtr]::Zero) | Out-Null
        } elseif ($title -match 'Mod change') {
            Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] dialog 'Mod change detected' -> sending ENTER (OK)")
            [BlLaunch]::SetForegroundWindow([IntPtr][int64]$h) | Out-Null
            Start-Sleep -Milliseconds 250
            $shell.SendKeys('{ENTER}')
        } elseif ($title -match 'Mount and Blade II Bannerlord') {
            Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] game window is up: " + $title)
            $window = [IntPtr][int64]$h
        } else {
            Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] window left alone: [" + $title + "]")
        }
        $handled[$h] = $true
    }

    if ($window -ne [IntPtr]::Zero) { break }
    # Do NOT treat the standalone launcher's exit as a failure: it hands the game over to another
    # process (the pid changes), so its exit is normal.  The first version of this script broke
    # out here and printed LAUNCH FAILED even though the game had loaded BlBridge fine (proved by
    # a fresh "Register: AddInitialStateOption OK" line in proto_ui.log).
    $alive = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match 'Bannerlord|BLSE' })
    if ($alive.Count -eq 0) {
        Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] no Bannerlord process is running any more")
        break
    }
}

if ($window -eq [IntPtr]::Zero) { Write-Host 'LAUNCH FAILED (no game window within timeout)'; exit 1 }
Write-Host 'LAUNCH OK'
exit 0
