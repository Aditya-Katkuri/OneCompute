<#
.SYNOPSIS
  Personal, LOCAL-ONLY OneCompute measurement observer for a ~1-week self-pilot. Records this
  machine's CPU/GPU/RAM into the on-device usage profile, survives reboot/sleep, and is easy to
  find in Task Manager.

.DESCRIPTION
  Runs:  python -m worker --measure-only --measure-interval <IntervalSec>   (local-only, no network)
  in a console window titled "OneCompute Observer" so you can spot it in Task Manager (Processes ->
  the "OneCompute Observer" window under Apps, or match the PID reported by -Status in Details).

  It writes ONLY CPU/GPU/RAM percentages to a local profile
  (%LOCALAPPDATA%\OneCompute\usage_profile.json). It never pulls or runs a job, never uploads, and
  never touches the network. Sleep/hibernate is covered by the process resuming on wake; reboot is
  covered by the Startup-folder autostart below (no admin, no Scheduled Task, so it works on managed
  machines where task creation is blocked).

  -Install   drop a launcher in your Startup folder so the observer relaunches at every logon, then
             start it now. No admin required.
  -Uninstall remove the Startup launcher (a running observer keeps going until you close its window).
  -Status    print whether the observer looks ALIVE (profile last updated N seconds ago), the sample
             count, any running observer PIDs, and whether autostart is on. Starts nothing.

.EXAMPLE
  # Start observing now AND autostart at logon for the week (recommended):
  powershell -ExecutionPolicy Bypass -File scripts\observe_me.ps1 -Install

.EXAMPLE
  # Check any day that it's still collecting:
  powershell -ExecutionPolicy Bypass -File scripts\observe_me.ps1 -Status

.EXAMPLE
  # When the week is done, stop autostart (then close the observer window):
  powershell -ExecutionPolicy Bypass -File scripts\observe_me.ps1 -Uninstall
#>
param(
    [double]$IntervalSec = 30,
    [string]$Url = "",
    [string]$ProfileFile = "",
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Py = if (Test-Path $VenvPy) { $VenvPy } else { "python" }
$ProfilePath = Join-Path $env:LOCALAPPDATA "OneCompute\usage_profile.json"
if ($ProfileFile) { $ProfilePath = $ProfileFile }  # -ProfileFile names this device's profile (multi-person pilot)
$TelemPath = Join-Path $env:LOCALAPPDATA "OneCompute\pilot-telemetry.jsonl"
$Title = "OneCompute Observer"
$StartupDir = [Environment]::GetFolderPath("Startup")
$StartupCmd = Join-Path $StartupDir "OneCompute-Observer.cmd"

function Get-ObserverPids {
    try {
        Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
            Where-Object { $_.CommandLine -and $_.CommandLine -match 'worker' -and $_.CommandLine -match 'measure-only' } |
            Select-Object -ExpandProperty ProcessId
    } catch { @() }
}

if ($Status) {
    Write-Host "OneCompute observer status" -ForegroundColor Cyan
    # Definitive liveness: is a measure-only process actually running?
    $procs = @(Get-ObserverPids)
    if ($procs.Count -gt 0) {
        Write-Host ("  observer : RUNNING  (PIDs {0} -- find in Task Manager > Details)" -f ($procs -join ', ')) -ForegroundColor Green
    } else {
        Write-Host "  observer : NOT RUNNING (no measure-only process found)" -ForegroundColor Yellow
    }
    # Freshness from the telemetry log, which appends EVERY sample (the profile only saves ~every 5 min).
    $freshFile = if (Test-Path $TelemPath) { $TelemPath } elseif (Test-Path $ProfilePath) { $ProfilePath } else { $null }
    if ($freshFile) {
        $age = [int]((Get-Date) - (Get-Item $freshFile).LastWriteTime).TotalSeconds
        Write-Host ("  last sample : {0}s ago" -f $age)
    } else {
        Write-Host "  last sample : (none yet)"
    }
    if (Test-Path $TelemPath) {
        $samples = (Get-Content $TelemPath | Measure-Object -Line).Lines
        Write-Host ("  samples logged : {0}" -f $samples)
    }
    if (Test-Path $ProfilePath) {
        $psave = [int]((Get-Date) - (Get-Item $ProfilePath).LastWriteTime).TotalSeconds
        Write-Host ("  profile saved : {0}s ago  (saves about every 5 min)" -f $psave)
    }
    Write-Host ("  profile : {0}" -f $ProfilePath)
    $auto = if (Test-Path $StartupCmd) { "ON  ($StartupCmd)" } else { "OFF" }
    Write-Host ("  autostart at logon : {0}" -f $auto)
    return
}

if ($Uninstall) {
    if (Test-Path $StartupCmd) {
        Remove-Item $StartupCmd -Force
        Write-Host "Removed autostart -> $StartupCmd" -ForegroundColor Green
    } else {
        Write-Host "No autostart entry found." -ForegroundColor Yellow
    }
    Write-Host "A currently-running observer keeps going until you close its '$Title' window."
    return
}

$urlArg = if ($Url) { " --url `"$Url`"" } else { "" }
$profArg = if ($ProfileFile) { " --profile `"$ProfileFile`"" } else { "" }

if ($Install) {
    $cmdBody = "@echo off`r`ntitle $Title`r`n`"$Py`" -m worker --measure-only --measure-interval $IntervalSec$urlArg$profArg`r`n"
    Set-Content -Path $StartupCmd -Value $cmdBody -Encoding ASCII
    Write-Host "Installed autostart -> $StartupCmd" -ForegroundColor Green
    Start-Process -FilePath $StartupCmd
    Write-Host "Observer started in a new '$Title' window; it relaunches at every logon." -ForegroundColor Green
    Write-Host "Find it in Task Manager (the '$Title' window). Check anytime: scripts\observe_me.ps1 -Status"
    Write-Host "Profile: $ProfilePath"
    return
}

# Default (no switch): run the observer in THIS window (manual/foreground use).
$Host.UI.RawUI.WindowTitle = $Title
Write-Host "Starting OneCompute observer (LOCAL-only, interval ${IntervalSec}s)." -ForegroundColor Green
Write-Host "Window title: '$Title'  -> find it in Task Manager. Check anytime with: scripts\observe_me.ps1 -Status"
Write-Host "Profile: $ProfilePath"
Write-Host "----------------------------------------------------------------------"
$argList = @("-m", "worker", "--measure-only", "--measure-interval", "$IntervalSec")
if ($Url) { $argList += @("--url", $Url) }
if ($ProfileFile) { $argList += @("--profile", $ProfileFile) }
& $Py @argList
