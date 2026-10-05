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
#      zero new lines in proto_ui.log / bridge_status.json.
#      v0.8.43 (2026-10-05): this list is no longer hardcoded -- it is derived from the user's own
#      selection in Configs\LauncherData.xml, because the hardcoded copy silently drifted and
#      launched a game WITHOUT RBM five times in a row.  See the $mods block below for the evidence.
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
    # Answer-only mode (v0.8.36): don't launch anything, just run the dialog-answering loop for
    # $AnswerSec seconds and exit.  Why: load-time modal dialogs (the "this save's module list does
    # not match" one, seen 2026-09-27 on ContinueCampaign) appear LONG after this script's normal
    # grace period has ended, so nobody answered it and the load sat there waiting for a human.
    # bl_mcp.py calls this shape while waiting for a campaign to load.
    #   .\bl_launch.ps1 -AnswerSec 8
    [int]$AnswerSec = 0,
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
            // v0.8.36: empty-caption windows are NO LONGER dropped here. Native MessageBox-style
            // dialogs (the load-time "module mismatch" one) can have an empty caption, and the
            // PowerShell side filters empty titles by class (#32770 only) after ClassOf().
            int len = GetWindowTextLength(h);
            var sb = new StringBuilder(len + 1);
            if (len > 0) GetWindowText(h, sb, sb.Capacity);
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

# -- ENTRY POINT: Standalone (no GUI) ----------------------------------------------------------
# KEEP Standalone.exe.  Reason (v0.8.43, 2026-10-05): `Bannerlord.BLSE.Launcher.exe` is the PLAY
# screen itself -- using it costs the unattended property this whole script exists for, and that
# screen is Gauntlet, so synthetic clicks do not reach it (measured: gridhand `Failed to set
# foreground window`; only a human pressing PLAY got past it).
# The "wrong client" bug was NEVER the exe -- it was the hardcoded `$mods` list drifting from the
# user's real selection.  Fix the list, keep the no-GUI exe.  See $mods construction below.
$exe = Join-Path $GameDir 'bin\Win64_Shipping_Client\Bannerlord.BLSE.Standalone.exe'
$answerOnly = ($AnswerSec -gt 0)
if (-not $answerOnly -and -not (Test-Path $exe)) { Write-Host "LAUNCH FAILED: not found: $exe"; exit 1 }

# -- MODULE LIST: derived from LauncherData.xml, NOT hardcoded --------------------------------
# Why this changed (v0.8.43, 2026-10-05, measured): the previous hardcoded list had drifted from
# the user's real selection by 6 modules -- it was missing RBM / RBM_WS / BattleSizeResized and
# still named Warbandlord, which no longer exists on disk (Modules\Warbandlord holds only a
# leftover config.xml, no SubModule.xml).  Result on the SAME machine and mod set:
#   * hardcoded list  (5 launches): 44 modules, NO RBM; `Loading assembly` shows BlBridge.dll but
#     never RBM.dll  -> the "wrong client" that wasted four launches.
#   * LauncherData.xml (1 launch, official launcher): 44 modules incl. RBM/RBM_WS/BattleSizeResized,
#     and RBM.dll DOES load.
# Raising this by hand is what got forgotten twice before (2026-10-04 memory, handoff log section 7), so
# the list is now READ FROM THE USER'S OWN SELECTION.  Verified: the ids below, filtered to
# IsSelected=true in file order, reproduce the good run's `Command Args:` EXACTLY (44/44, same order)
# -- `Compare-Object -SyncWindow 0` reports no difference.
#
# Fallback: if LauncherData.xml is missing/unreadable, use $fallbackMods and SAY SO on stdout --
# never silently launch with a guessed or empty list.
$fallbackMods = @('Bannerlord.Harmony', 'Bannerlord.ButterLib', 'Bannerlord.UIExtenderEx',
                  'Bannerlord.MBOptionScreen', 'Native', 'SandBoxCore', 'Sandbox', 'CustomBattle',
                  'StoryMode', 'BirthAndDeath', 'FastMode', 'NavalDLC',
                  'WarlordsBattlefieldWarSailsEdition', 'BlBridge')

$launcherData = Join-Path $env:USERPROFILE 'Documents\Mount and Blade II Bannerlord\Configs\LauncherData.xml'
$selected = @()
if (Test-Path $launcherData) {
    try {
        [xml]$ld = Get-Content -LiteralPath $launcherData -Encoding UTF8
        # File order is significant: it is what the engine's own launcher produced and it keeps
        # Harmony/ButterLib/UIExtenderEx/MCM ahead of the modules that depend on them.
        $selected = @($ld.UserData.SingleplayerData.ModDatas.UserModData |
                      Where-Object { $_.IsSelected -eq 'true' } |
                      ForEach-Object { [string]$_.Id } |
                      Where-Object { $_ -ne '' })
    } catch {
        Write-Host ("WARNING: could not parse " + $launcherData + " : " + $_.Exception.Message)
        $selected = @()
    }
}

if ($selected.Count -gt 0) {
    Write-Host ("module list from LauncherData.xml: " + $selected.Count + " selected module(s)")
} else {
    Write-Host ("WARNING: LauncherData.xml gave no selection; falling back to " + $fallbackMods.Count + " built-in module(s)")
    Write-Host ("  (this is the path that produced the 'wrong client' -- check " + $launcherData + ")")
    $selected = $fallbackMods
}

# Existence pre-check: a module named in the list but absent on disk is silently skipped by the
# engine, which is exactly how a stale entry (the old 'Warbandlord') hid in plain sight.  Report it
# loudly instead.  This is a WARNING, not a hard failure: the engine tolerates it and aborting here
# would be a behaviour change for anyone mid-edit.
$modulesDir = Join-Path $GameDir 'Modules'
$absent = @($selected | Where-Object { -not (Test-Path (Join-Path $modulesDir ($_ + '\SubModule.xml'))) })
if ($absent.Count -gt 0) {
    Write-Host ("WARNING: module(s) selected but NOT on disk (engine will skip them): " + ($absent -join ', '))
}
# Same check the other way: BlBridge must be in the list or this whole exercise is pointless.
if ($selected -notcontains 'BlBridge') {
    Write-Host "WARNING: 'BlBridge' is NOT in the module list; the bridge will not load"
}

$mods = '_MODULES_*' + ($selected -join '*') + '*_MODULES_'

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

$p = $null
if ($answerOnly) {
    Write-Host ("ANSWER-ONLY MODE: answering dialogs for " + $AnswerSec + "s (no launch)")
}
else {
    Write-Host ("launching: " + $exe)
    if ($SkipModuleList) { Write-Host "WARNING: -SkipModuleList given; the game will start WITHOUT community mods" }
    $p = Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory (Split-Path $exe -Parent) -PassThru
    Write-Host ("launcher pid = " + $p.Id)
}

$lastTry = @{}                                   # hwnd -> DateTime of the last answer attempt
$logged = @{}                                    # hwnd -> already printed a "window:" line
$window = [IntPtr]::Zero
$windowSeenAt = $null
$deadline = (Get-Date).AddSeconds($(if ($answerOnly) { $AnswerSec } else { $TimeoutSec }))

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
# 'Yes' / 0x662F added in v0.8.36: the load-time "module mismatch" dialog's confirm button is
# labelled exactly that (observed by the user on 2026-09-27: two buttons, qu xiao | shi).
$okTexts = @('OK', 'Ok', '&OK', 'Yes', [string][char]0x662F,
             [string][char]0x786E + [string][char]0x5B9A)

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
        # Strip the keyboard mnemonic: a real dialog's confirm button reads 'shi(&Y)' (observed
        # 2026-09-27 in the answer-only control experiment; the bare 'shi' entry did NOT match and
        # the run fell through to VK_RETURN -- which hits the DEFAULT button, and on the real
        # load-confirmation dialog that default may well be "cancel".  Never bet on the default.)
        $plain = ($text -replace '\(&[A-Za-z0-9]\)', '') -replace '&', ''
        foreach ($ok in $okTexts) {
            if ($plain -eq $ok) {
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
        # Empty title is NOT skipped blindly any more (v0.8.36): native MessageBox-style dialogs can
        # have an empty caption, and the load-time module-mismatch one is exactly that shape. The
        # skip below happens AFTER $cls is known, so only non-dialog empty-title windows go away.

        # Do NOT blacklist windows by title here.  The game window's own title contains the BLSE
        # install path, which on this machine lives under Vortex, so a title blacklist containing
        # 'Vortex' skipped the very window we were waiting for.
        $owner = 0
        [void][int]::TryParse($parts[1], [ref]$owner)
        $isOurs = (($null -ne $p) -and ($owner -eq $p.Id))
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
        # v0.8.36: empty-title windows are only interesting when they ARE dialogs (see the comment
        # above the loop); everything else with no caption stays ignored.
        if (($title -eq '') -and ($cls -ne '#32770')) { continue }

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
