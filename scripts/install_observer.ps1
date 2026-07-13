<#
.SYNOPSIS
  Install (or remove) the OneCompute measurement observer as a Windows Scheduled Task so it keeps
  running across logoff, reboot, sleep/hibernate, crashes, and network drops for a measurement
  pilot. Measurement-only: it never pulls or runs a job (see docs/measurement-pilot.md).

.DESCRIPTION
  By default registers a per-user LOGON task (no admin required) that launches
      python -m worker --url <Url> --measure-only --measure-interval <IntervalSec>
  with: auto-restart on failure, start-when-available (catches a trigger missed while the device
  was off), unlimited run time, no duplicate instances, and battery-friendly settings (measurement
  imposes no load, so it runs on battery too). It starts immediately and re-launches every time the
  volunteer logs in, so a shutdown/reboot is covered by the next login and a sleep/hibernate is
  covered by the process resuming.

  -AtStartup (requires an elevated shell) also starts the observer at BOOT, before anyone logs in,
  for always-on dev boxes/servers. It runs the task whether or not a user is logged on.

  Fully reversible: -Uninstall removes the task and the observer never restarts. The only device
  artifact is the local usage profile (%LOCALAPPDATA%\OneCompute\usage_profile.json), which the
  volunteer keeps or deletes.

.EXAMPLE
  # Install (no admin), 30s cadence, against a LAN orchestrator:
  powershell -ExecutionPolicy Bypass -File scripts\install_observer.ps1 -Url http://10.0.0.5:8080

.EXAMPLE
  # Use a signed single-exe instead of a Python checkout (managed fleets):
  powershell -ExecutionPolicy Bypass -File scripts\install_observer.ps1 -Url http://10.0.0.5:8080 -Exe C:\OneCompute\onecompute-worker.exe

.EXAMPLE
  # Always-on dev box: also start at boot (run this in an elevated shell):
  powershell -ExecutionPolicy Bypass -File scripts\install_observer.ps1 -Url http://10.0.0.5:8080 -AtStartup

.EXAMPLE
  # Opt out (remove it):
  powershell -ExecutionPolicy Bypass -File scripts\install_observer.ps1 -Uninstall
#>
[CmdletBinding()]
param(
  [string]$Url,
  [int]$IntervalSec = 30,
  [string]$TaskName = "OneCompute Observer",
  [string]$Exe,                 # optional: path to a built onecompute-worker.exe
  [string]$RepoDir,             # repo root for the `uv run` path; defaults to the script's parent
  [string]$ExtraArgs = "",      # optional extra worker args, e.g. "--tls-ca C:\pki\ca.pem"
  [switch]$AtStartup,           # also start at boot (requires an elevated shell)
  [switch]$DryRun,              # build + print the task definition without registering it
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

if ($Uninstall) {
  $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'. The observer will not restart. Local profile is kept."
  } else {
    Write-Host "No scheduled task '$TaskName' found; nothing to remove."
  }
  return
}

if (-not $Url) { throw "Provide -Url http://<orchestrator-host>:8080 (or -Uninstall)." }

# Resolve the repo dir (parent of scripts\) for the `uv run` working directory.
if (-not $RepoDir) { $RepoDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }

# Build the observer command. Prefer a built exe; else `uv run python -m worker` from the repo.
$workerArgs = "--url `"$Url`" --measure-only --measure-interval $IntervalSec"
if ($ExtraArgs) { $workerArgs = "$workerArgs $ExtraArgs" }

if ($Exe) {
  if (-not (Test-Path $Exe)) { throw "-Exe path not found: $Exe" }
  $execute  = $Exe
  $argument = $workerArgs
  $workDir  = Split-Path -Parent $Exe
} else {
  $uv = Get-Command uv -ErrorAction SilentlyContinue
  if (-not $uv) { throw "uv not found on PATH. Install uv, or pass -Exe <path-to-onecompute-worker.exe>." }
  $execute  = $uv.Source
  $argument = "run python -m worker $workerArgs"
  $workDir  = $RepoDir
}

$action = New-ScheduledTaskAction -Execute $execute -Argument $argument -WorkingDirectory $workDir

$triggers = @( New-ScheduledTaskTrigger -AtLogOn )
if ($AtStartup) { $triggers += New-ScheduledTaskTrigger -AtStartup }

# Resilience knobs: restart on failure, catch a trigger missed while the device was off, run with
# no time limit, never spawn a duplicate, and run on battery (measurement imposes zero load) and
# offline (uploads are best-effort; the profile keeps building locally).
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew

$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($AtStartup) {
  # Run whether or not the user is logged on (needs admin to register); covers boot before login.
  $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType S4U -RunLevel Highest
} else {
  # Per-user, no admin: runs in the volunteer's session, re-launched at each logon.
  $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
}

$desc = "OneCompute measurement observer (measure-only; never runs a job). Auto-restarts and survives reboot/sleep for the pilot window."

if ($DryRun) {
  Write-Host "[dry run] would register scheduled task '$TaskName':"
  Write-Host "  Execute:  $execute"
  Write-Host "  Argument: $argument"
  Write-Host "  WorkDir:  $workDir"
  Write-Host "  Triggers: $(($triggers | ForEach-Object { $_.CimClass.CimClassName }) -join ', ')"
  Write-Host "  Principal LogonType=$($principal.LogonType) RunLevel=$($principal.RunLevel)"
  Write-Host "  Settings: RestartCount=$($settings.RestartCount) RestartInterval=$($settings.RestartInterval) StartWhenAvailable=$($settings.StartWhenAvailable) MultipleInstances=$($settings.MultipleInstances)"
  return
}

try {
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description $desc -Force -ErrorAction Stop | Out-Null
} catch {
  Write-Error @"
Could not register the scheduled task: $($_.Exception.Message)

On a managed/corporate device, creating a scheduled task is often blocked for a standard shell.
Options:
  1. Run this script from an ELEVATED PowerShell (Run as administrator).
  2. Have IT deploy the observer via Intune / Group Policy (the same measure-only command).
  3. For a quick pilot, just run the observer in the foreground and leave the window open:
       uv run python -m worker --url $Url --measure-only
     (It still persists locally and re-uploads; you lose only auto-restart across logoff/reboot.)
Re-run with -DryRun to print the exact task definition without registering it.
"@
  exit 1
}
Start-ScheduledTask -TaskName $TaskName

$bootNote = if ($AtStartup) { " and at boot" } else { "" }
Write-Host "Installed '$TaskName': measure-only observer against $Url (every ${IntervalSec}s)."
Write-Host "  Starts now and re-launches at logon$bootNote; auto-restarts on failure; survives sleep/reboot; runs offline + on battery."
Write-Host "  Local profile: %LOCALAPPDATA%\OneCompute\usage_profile.json (derived stats only; on-device)."
Write-Host "  Status:   Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "  Opt out:  powershell -ExecutionPolicy Bypass -File scripts\install_observer.ps1 -Uninstall"
