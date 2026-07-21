<#
.SYNOPSIS
  Install, inspect, uninstall, or purge a managed OneCompute measurement observer.

.DESCRIPTION
  Registers a resilient Windows Scheduled Task that runs only measurement mode. Current measurement
  mode never pulls jobs, writes a per-sample timeline, or streams live utilization. It periodically
  uploads one compact derived summary.

  A non-loopback pilot URL must use HTTPS with a pinned CA and a client certificate/key. Use
  -AllowInsecureLocalhost only for explicit development on the same machine.
#>
[CmdletBinding()]
param(
  [string]$Url,
  [ValidateRange(5, 3600)]
  [int]$IntervalSec = 30,
  [ValidateSet("laptop", "desktop", "devbox", "xbox", "unknown")]
  [string]$DeviceClass = "unknown",
  [ValidatePattern("^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")]
  [string]$MeasurementId = "",
  [string]$TlsCa = "",
  [string]$ClientCert = "",
  [string]$ClientKey = "",
  [string]$ProfileFile = "",
  [string]$TaskName = "OneCompute Observer",
  [string]$Exe,
  [string]$RepoDir,
  [switch]$AllowInsecureLocalhost,
  [switch]$AtStartup,
  [switch]$DryRun,
  [switch]$Status,
  [switch]$Uninstall,
  [switch]$Purge
)

$ErrorActionPreference = "Stop"
$DataDir = Join-Path $env:LOCALAPPDATA "OneCompute"
$ProfilePath = if ($ProfileFile) { $ProfileFile } else { Join-Path $DataDir "usage_profile.json" }
$TelemetryPath = Join-Path $DataDir "pilot-telemetry.jsonl"
$IdentityPath = Join-Path $DataDir "observer-id"

function Stop-ObserverTask {
  $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($existing -and $existing.State -ne "Disabled") {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  }
  return $existing
}

function Remove-ObserverData {
  $paths = @($ProfilePath, $TelemetryPath, $IdentityPath)
  $paths += @(Get-ChildItem -LiteralPath $DataDir -Filter "pilot-telemetry.jsonl.*" -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName)
  foreach ($path in ($paths | Select-Object -Unique)) {
    if ($path -and (Test-Path -LiteralPath $path)) {
      Remove-Item -LiteralPath $path -Force
      Write-Host "Deleted $path"
    }
  }
}

function Quote-Argument {
  param([string]$Value)
  if ($Value -notmatch '[\s"]') { return $Value }
  return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

if ($Status) {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Task '$TaskName': $($task.State)" -ForegroundColor Green
    Write-Host "  last run : $($info.LastRunTime)"
    Write-Host "  result : $($info.LastTaskResult)"
    Write-Host "  next run : $($info.NextRunTime)"
  } else {
    Write-Host "Task '$TaskName' is not installed." -ForegroundColor Yellow
  }
  if (Test-Path -LiteralPath $ProfilePath) {
    $item = Get-Item -LiteralPath $ProfilePath
    $age = [int]((Get-Date) - $item.LastWriteTime).TotalSeconds
    $samples = 0
    try {
      $profile = Get-Content -LiteralPath $ProfilePath -Raw | ConvertFrom-Json
      $samples = [int]$profile.availability.sample_count
    } catch {
      Write-Host "Profile JSON is invalid." -ForegroundColor Red
    }
    Write-Host "  profile age : ${age}s"
    Write-Host "  samples : $samples"
  } else {
    Write-Host "  profile : not created yet"
  }
  Write-Host "  profile path : $ProfilePath"
  if (Test-Path -LiteralPath $TelemetryPath) {
    Write-Host "  legacy timeline : PRESENT; purge after validating the upgraded observer" -ForegroundColor Yellow
  } else {
    Write-Host "  legacy timeline : absent"
  }
  return
}

if ($Purge -or $Uninstall) {
  $existing = Stop-ObserverTask
  if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
  } else {
    Write-Host "No scheduled task '$TaskName' found."
  }
  if ($Purge) {
    Remove-ObserverData
    Write-Host "Observer stopped, uninstalled, and local measurement data purged." -ForegroundColor Green
  } else {
    Write-Host "Local profile retained at $ProfilePath. Run -Purge to delete it."
  }
  return
}

if (-not $Url) { throw "Provide -Url https://<orchestrator-host>:<port>." }
$uri = $null
if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$uri)) {
  throw "-Url must be an absolute HTTP or HTTPS URL."
}
if ($uri.Scheme -notin @("http", "https")) {
  throw "-Url must use HTTP or HTTPS."
}
if (($ClientCert -and -not $ClientKey) -or ($ClientKey -and -not $ClientCert)) {
  throw "-ClientCert and -ClientKey must be provided together."
}
if ($uri.IsLoopback) {
  if ($uri.Scheme -eq "http" -and -not $AllowInsecureLocalhost) {
    throw "Plain HTTP requires the explicit -AllowInsecureLocalhost switch."
  }
} else {
  if ($uri.Scheme -ne "https") {
    throw "Remote measurement pilots require HTTPS."
  }
  if (-not $TlsCa -or -not $ClientCert -or -not $ClientKey) {
    throw "Remote measurement pilots require -TlsCa, -ClientCert, and -ClientKey for pinned mTLS."
  }
}
if ($uri.Scheme -ne "https" -and ($TlsCa -or $ClientCert -or $ClientKey)) {
  throw "TLS certificate options require an HTTPS URL."
}
foreach ($certificatePath in @($TlsCa, $ClientCert, $ClientKey)) {
  if ($certificatePath -and -not (Test-Path -LiteralPath $certificatePath -PathType Leaf)) {
    throw "Certificate file not found: $certificatePath"
  }
}

if (-not $RepoDir) {
  $RepoDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

$workerArgs = [System.Collections.Generic.List[string]]::new()
@(
  "--url", $Url,
  "--measure-only",
  "--no-telemetry",
  "--measure-interval", "$IntervalSec",
  "--measurement-device-class", $DeviceClass
) | ForEach-Object { $workerArgs.Add($_) }
if ($MeasurementId) { $workerArgs.Add("--measurement-id"); $workerArgs.Add($MeasurementId) }
if ($ProfileFile) { $workerArgs.Add("--profile"); $workerArgs.Add($ProfileFile) }
if ($TlsCa) { $workerArgs.Add("--tls-ca"); $workerArgs.Add($TlsCa) }
if ($ClientCert) { $workerArgs.Add("--client-cert"); $workerArgs.Add($ClientCert) }
if ($ClientKey) { $workerArgs.Add("--client-key"); $workerArgs.Add($ClientKey) }

if ($Exe) {
  if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) { throw "-Exe path not found: $Exe" }
  $execute = (Resolve-Path $Exe).Path
  $allArgs = @($workerArgs)
  $workDir = Split-Path -Parent $execute
} else {
  $uv = Get-Command uv -ErrorAction SilentlyContinue
  if (-not $uv) { throw "uv not found on PATH. Install uv or pass -Exe." }
  $execute = $uv.Source
  $allArgs = @("run", "python", "-m", "worker") + @($workerArgs)
  $workDir = (Resolve-Path $RepoDir).Path
}
$argument = ($allArgs | ForEach-Object { Quote-Argument $_ }) -join " "
$action = New-ScheduledTaskAction -Execute $execute -Argument $argument -WorkingDirectory $workDir
$triggers = @(New-ScheduledTaskTrigger -AtLogOn)
if ($AtStartup) { $triggers += New-ScheduledTaskTrigger -AtStartup }
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = if ($AtStartup) {
  New-ScheduledTaskPrincipal -UserId $me -LogonType S4U -RunLevel Highest
} else {
  New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
}
$description = "OneCompute privacy-minimized measurement observer. Never runs jobs or writes a sample timeline."

if ($DryRun) {
  Write-Host "[dry run] scheduled task '$TaskName'"
  Write-Host "  Execute: $execute"
  Write-Host "  Arguments: $argument"
  Write-Host "  Working directory: $workDir"
  Write-Host "  Device class: $DeviceClass"
  Write-Host "  Transport: $($uri.Scheme); client certificate=$([bool]$ClientCert)"
  return
}

try {
  Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description $description `
    -Force `
    -ErrorAction Stop | Out-Null
} catch {
  Write-Error @"
Could not register the scheduled task: $($_.Exception.Message)

Use an elevated PowerShell, deploy the same command through Intune, or use
$PSScriptRoot\observe_me.ps1 -Install for a no-admin Startup-folder observer.
"@
  exit 1
}

Start-ScheduledTask -TaskName $TaskName
$bootNote = if ($AtStartup) { " and at boot" } else { "" }
Write-Host "Installed '$TaskName' against $Url (every ${IntervalSec}s)." -ForegroundColor Green
Write-Host "  Starts now and at logon$bootNote; no job execution, live usage stream, or sample timeline."
Write-Host "  Local profile: $ProfilePath"
Write-Host "  Status: $PSCommandPath -Status"
Write-Host "  Retain data on opt-out: $PSCommandPath -Uninstall"
Write-Host "  Delete all local pilot data: $PSCommandPath -Purge"
