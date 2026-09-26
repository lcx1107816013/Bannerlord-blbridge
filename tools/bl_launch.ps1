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
#                                 cache...").  Answering OK is required.  Its button is labelled with
#                                 the Chinese word for OK and WM_COMMAND IDOK(1) did NOT work;
#                                 a plain ENTER did.
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
# HOW TO RUN IT UNATTENDED (the only shape that works on this machine)
# -------------------------------------------------------------------
# Running this script from an agent's own session does NOT keep the game alive: the host reclaims
# the process tree started by the session, so the game dies 0.3-0.7 s after "LAUNCH OK" (engine
# log stops at "Selected graphics adapter"; no exception, no BUTR crash report).  What DOES work
# is letting Windows Task Scheduler create the process:
#
#   $a = New-ScheduledTaskAction -Execute 'powershell.exe' `
#          -Argument ('-ExecutionPolicy Bypass -NoProfile -File "' + $PSScriptRoot + '\bl_launch.ps1" -TimeoutSec 300') `
#          -WorkingDirectory (Split-Path $PSScriptRoot -Parent)
#   $p = New-ScheduledTaskPrincipal -UserId ($env:USERDOMAIN + '\' + $env:USERNAME) -LogonType Interactive -RunLevel Limited
#   $st = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
#   Register-ScheduledTask -TaskName 'BlBridgeDevLaunch' -Action $a -Principal $p -Settings $st -Force
#   Start-ScheduledTask -TaskName 'BlBridgeDevLaunch'
#
# Then poll <Documents>\Mount and Blade II Bannerlord\BlBridge\bridge_status.json for
# state=loaded + the sha256 you just deployed (do NOT trust this script's LAUNCH OK for that).
# Success check used in v0.8.12 verification: 4 game starts, bl_launch answered the dialogs every
# time, each game survived (pids 48764 / 95392 / 48396), removed with
# Unregister-ScheduledTask -TaskName 'BlBridgeDevLaunch'.
#
# NOTE: keep this file ASCII-only.  PowerShell 5.1 reads a BOM-less UTF-8 .ps1 as ANSI, which
# would corrupt non-ASCII text (see AGENTS.md, section 1).

param(
    [string]$GameDir = 'G:\Program Files (x86)\Steam\steamapps\common\Mount & Blade II Bannerlord',
    [int]$TimeoutSec = 180,
    [switch]$SkipModuleList,
    # Module names to leave OUT of the _MODULES_ list (v0.8.16). Why this exists: an A/B control
    # has to be "same launch, one module different", and unchecking boxes in the BLSE launcher
    # is neither reproducible nor available at all in unattended launches. Usage:
    #   .\bl_launch.ps1 -ExcludeModules RTSCamera,RTSCamera.CommandSystem
    # Names must match the list below exactly (a typo aborts instead of silently doing nothing).
    [string[]]$ExcludeModules = @()
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
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
    public static string ClassOf(IntPtr h) {
        var sb = new StringBuilder(256);
        GetClassName(h, sb, sb.Capacity);
        return sb.ToString();
    }
    // Visible push buttons under a dialog, as "hwnd|class|enabled|text".
    // Why: the previous version answered the Mod-change dialog with SetForegroundWindow + SendKeys
    // ENTER -- that silently does nothing when Windows refuses the foreground change (which is the
    // normal case for a background/agent session), and it left no evidence either way.
    public static List<string> Buttons(IntPtr parent) {
        var res = new List<string>();
        EnumChildWindows(parent, (h, l) => {
            if (!IsWindowVisible(h)) return true;
            string cls = ClassOf(h);
            if (!cls.Equals("Button", StringComparison.OrdinalIgnoreCase)) return true;
            int len = GetWindowTextLength(h);
            var sb = new StringBuilder(len + 1);
            GetWindowText(h, sb, sb.Capacity);
            res.Add(h.ToInt64() + "|" + cls + "|" + IsWindowEnabled(h) + "|" + sb.ToString());
            return true;
        }, IntPtr.Zero);
        return res;
    }
    // BM_CLICK (0x00F5) posted straight at the button: no foreground, no focus, no keyboard.
    public static void ClickButton(IntPtr hButton) {
        SendMessage(hButton, 0x00F5, IntPtr.Zero, IntPtr.Zero);
    }
    // Last-resort: WM_KEYDOWN/WM_KEYUP with VK_RETURN (0x0D) aimed at the dialog itself.
    public static void PostEnter(IntPtr h) {
        PostMessage(h, 0x0100, (IntPtr)0x0D, IntPtr.Zero);
        PostMessage(h, 0x0101, (IntPtr)0x0D, IntPtr.Zero);
    }
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

if ($ExcludeModules.Count -gt 0) {
    # Accept BOTH "-ExcludeModules A,B" and '-ExcludeModules "A,B"'. The quoted form binds the
    # WHOLE "A,B" as a single [string[]] element (observed: the guard aborted with
    # "not present in the module list: RTSCamera,RTSCamera.CommandSystem"), so split each element
    # on commas here instead of relying on the caller's shell quoting.
    $ExcludeModules = @($ExcludeModules | ForEach-Object { $_ -split ',' } | Where-Object { $_ -ne '' })
    $marker = '_MODULES_'
    $inner = $mods.Substring($marker.Length, $mods.Length - 2 * $marker.Length)
    $all = @($inner.Split('*') | Where-Object { $_ -ne '' })
    $missing = @($ExcludeModules | Where-Object { $all -notcontains $_ })
    if ($missing.Count -gt 0) {
        Write-Host ("EXCLUDE FAILED: not present in the module list: " + ($missing -join ', '))
        Write-Host ("known modules: " + ($all -join ', '))
        exit 1
    }
    $kept = @($all | Where-Object { $ExcludeModules -notcontains $_ })
    $mods = $marker + '*' + ($kept -join '*') + '*' + $marker
    Write-Host ("excluding module(s): " + ($ExcludeModules -join ', ') + "  (kept " + $kept.Count + " of " + $all.Count + ")")
}

$argList = @('/singleplayer')
if (-not $SkipModuleList) { $argList += $mods }
$argList += 'no_watchdog'

Write-Host ("launching: " + $exe)
if ($SkipModuleList) { Write-Host "WARNING: -SkipModuleList given; the game will start WITHOUT community mods" }
$p = Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory (Split-Path $exe -Parent) -PassThru
Write-Host ("launcher pid = " + $p.Id)

$lastTry = @{}                                   # hwnd -> DateTime of the last answer attempt
$logged = @{}                                    # hwnd -> already printed a "window:" line
$window = [IntPtr]::Zero
$windowSeenAt = $null
$deadline = (Get-Date).AddSeconds($TimeoutSec)

# How long to keep answering dialogs AFTER the game window shows up.  This is the actual fix for
# the flaky launches of 2026-09-25 (user report: the first dialog got answered fast, the second one
# was never answered, the tool timed out, and only a relaunch worked): the old loop did `break` the
# moment it saw the game window, so a Mod-change dialog appearing a few seconds later was simply
# never seen.  Post-game-window dialogs do happen (the launcher deletes the shader cache then).
#
# Two exits, whichever comes first (v0.8.15, user request: "shorten the grace period, return as soon
# as the window is up and the dialogs are answered"):
#   * $graceAfterWindowSec        -- hard cap after the window appeared
#   * $quietAfterWindowSec        -- return early once the window is up and NO dialog was seen for
#                                    this long (the common case: no late dialog at all)
# Both stay short on purpose: the MCP tool call waits for this script to exit, and a long wait made
# the tool time out (the server runs tool calls serially).
$graceAfterWindowSec = 8
$quietAfterWindowSec = 4

# OK-button captions, built from char codes so this file stays ASCII-only (AGENTS.md section 1:
# PowerShell 5.1 reads a BOM-less UTF-8 .ps1 as ANSI, so a literal would be corrupted).
$okTexts = @('OK', 'Ok', '&OK', [string][char]0x786E + [string][char]0x5B9A)

# Answer one dialog WITHOUT relying on foreground/focus.  Logs the button inventory, because
# "which button is actually there" is the fact we were missing before.
function Resolve-Dialog([IntPtr]$h) {
    $btns = [BlLaunch]::Buttons($h)
    if ($btns.Count -eq 0) {
        Write-Host "        (no visible Button children)"
    } else {
        foreach ($b in $btns) { Write-Host ("        button: " + $b) }
    }
    foreach ($b in $btns) {
        $parts = $b.Split('|')
        if ($parts.Count -lt 4 -or $parts[2] -ne 'True') { continue }
        $text = $parts[3].Trim()
        foreach ($ok in $okTexts) {
            if ($text -eq $ok) {
                [BlLaunch]::ClickButton([IntPtr][int64]$parts[0])
                Write-Host ("        BM_CLICK -> [" + $text + "]")
                return
            }
        }
    }
    if ($btns.Count -eq 1) {
        $parts = $btns[0].Split('|')
        if ($parts.Count -ge 4 -and $parts[2] -eq 'True') {
            [BlLaunch]::ClickButton([IntPtr][int64]$parts[0])
            Write-Host "        single enabled button -> BM_CLICK"
            return
        }
    }
    [BlLaunch]::PostEnter($h)
    Write-Host "        no OK button matched -> posted WM_KEYDOWN/UP VK_RETURN at the dialog"
}

while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 400
    $sawDialogThisPass = $false

    foreach ($w in [BlLaunch]::Top()) {
        $parts = $w.Split('|')
        if ($parts.Count -lt 3) { continue }
        $h = $parts[0]; $title = $parts[2]
        if ($title -eq '') { continue }

        # Do NOT blacklist windows by title here.  The game window's own title contains the BLSE
        # install path, which on this machine lives under Vortex, so a title blacklist containing
        # 'Vortex' skipped the very window we were waiting for.
        $owner = 0
        [void][int]::TryParse($parts[1], [ref]$owner)
        $isOurs = ($owner -eq $p.Id)
        $cls = [BlLaunch]::ClassOf([IntPtr][int64]$h)

        # The GAME window must be recognised BEFORE the generic dialog branch.
        # Bug (2026-09-25, real machine): the game window belongs to the process tree we launched,
        # so `$isOurs` was true, the dialog test matched it, and this loop kept posting VK_RETURN
        # at the game window every 5 s while never recording $window -- the run then ended with a
        # bogus "LAUNCH FAILED (no game window within timeout)" even though pid 66752 was already
        # sitting at its main menu.  The game window's title carries "PID: <n>"; that is the only
        # reliable marker (most of the title is the BLSE install path).
        $looksLikeGame = ($title -match 'Mount and Blade II Bannerlord') -and ($title -match 'PID:')

        # '#32770' is the standard Win32 dialog class: this is how we recognise a modal dialog even
        # when its title does not match either of the two known ones.
        $isDialog = ($title -match 'Safe Mode') -or ($title -match 'Mod change') -or ($cls -eq '#32770') -or ($isOurs -and -not $looksLikeGame)
        if (-not $isDialog -and -not $looksLikeGame) { continue }

        if (-not $logged.ContainsKey($h)) {
            $logged[$h] = $true
            Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] window: pid=" + $owner + " class=[" + $cls + "] title=[" + $title + "]")
        }

        if ($title -match 'Safe Mode') {
            $sawDialogThisPass = $true
            # Safe mode disables the mods -> answer NO (IDNO = 7).  Retry every 5 s in case the
            # first message lands before the dialog is ready to process it.
            if (-not $lastTry.ContainsKey($h) -or ((Get-Date) - $lastTry[$h]).TotalSeconds -gt 5) {
                $lastTry[$h] = Get-Date
                Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] dialog 'Safe Mode' -> PostMessage IDNO")
                [BlLaunch]::PostMessage([IntPtr][int64]$h, 0x0111, [IntPtr]7, [IntPtr]::Zero) | Out-Null
            }
            continue
        }

        # Game window first (see the comment above): record it, never answer it as a dialog.
        if ($looksLikeGame) {
            if ($window -eq [IntPtr]::Zero) {
                Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] game window is up: " + $title)
                $window = [IntPtr][int64]$h
                $windowSeenAt = Get-Date
            }
            continue
        }

        if ($isDialog) {
            $sawDialogThisPass = $true
            if (-not $lastTry.ContainsKey($h) -or ((Get-Date) - $lastTry[$h]).TotalSeconds -gt 5) {
                $lastTry[$h] = Get-Date
                Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] dialog -> answering [" + $title + "] (no focus needed)")
                Resolve-Dialog ([IntPtr][int64]$h)
            }
            continue
        }

        Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] window left alone: [" + $title + "]")
    }

    # Do NOT leave as soon as the game window shows up: keep answering dialogs for a grace period
    # (see $graceAfterWindowSec above).  This is what the flaky version got wrong.
    if ($window -ne [IntPtr]::Zero -and $windowSeenAt -ne $null) {
        $sinceWindow = ((Get-Date) - $windowSeenAt).TotalSeconds

        # Early exit (v0.8.15): no dialog seen in this pass and we already waited a moment ->
        # there is nothing left to answer, so stop making the MCP call wait.
        if (-not $sawDialogThisPass -and $sinceWindow -gt $quietAfterWindowSec) {
            Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] no dialogs pending; leaving after " +
                [math]::Round($sinceWindow, 1) + "s (quiet window " + $quietAfterWindowSec + "s)")
            break
        }

        if ($sinceWindow -gt $graceAfterWindowSec) {
            Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] grace period after the game window elapsed (" + $graceAfterWindowSec + "s)")
            break
        }
    }

    # Do NOT treat the standalone launcher's exit as a failure: it hands the game over to another
    # process (the pid changes), so its exit is normal.
    $alive = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match 'Bannerlord|BLSE' })
    if ($alive.Count -eq 0) {
        Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] no Bannerlord process is running any more")
        break
    }
}

if ($window -eq [IntPtr]::Zero) {
    # Last-resort check (defence in depth): the loop above can still miss the window.  Ask the
    # processes instead of declaring failure, and SAY which path recognised it -- a false
    # "LAUNCH FAILED" costs a whole relaunch (real machine, 2026-09-25: pid 66752 was already at
    # the main menu while this script exited 1).
    $proc = @(Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessName -match 'Bannerlord' -and $_.MainWindowTitle -ne '' } |
        Select-Object -First 1)
    if ($proc.Count -gt 0) {
        $pTitle = $proc[0].MainWindowTitle
        Write-Host ("[" + (Get-Date -Format HH:mm:ss) + "] game window recognised via process fallback: pid=" +
            $proc[0].Id + " title=[" + $pTitle + "]")
        Write-Host 'LAUNCH OK (window recognised via process fallback)'
        exit 0
    }
    Write-Host 'LAUNCH FAILED (no game window within timeout)'
    exit 1
}
Write-Host 'LAUNCH OK'
exit 0
