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
  [string]$StartupDir = "",
  [switch]$AllowInsecureLocalhost,
  [switch]$AtStartup,
  [switch]$DryRun,
  [switch]$Status,
  [switch]$Uninstall,
  [switch]$Purge
)

$ErrorActionPreference = "Stop"
$DataDir = Join-Path $env:LOCALAPPDATA "OneCompute"
$ConfigPath = Join-Path $DataDir "observer-config.json"
$Config = $null
if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
  try {
    $Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
  } catch {
    Write-Warning "Ignoring invalid observer config at $ConfigPath"
  }
}
if (-not $PSBoundParameters.ContainsKey("TaskName") -and $Config -and $Config.task_name) {
  $TaskName = [string]$Config.task_name
}
if (-not $PSBoundParameters.ContainsKey("StartupDir") -and $Config -and $Config.startup_dir) {
  $StartupDir = [string]$Config.startup_dir
}
$ConfiguredProfile = if ($Config -and $Config.profile_path) { [string]$Config.profile_path } else { "" }
$ConfiguredExecutable = if ($Config -and $Config.executable_path) {
  [string]$Config.executable_path
} else {
  ""
}
$DefaultProfilePath = [IO.Path]::GetFullPath((Join-Path $DataDir "usage_profile.json"))
$ProfilePath = if ($ProfileFile) {
  $ProfileFile
} elseif ($ConfiguredProfile) {
  $ConfiguredProfile
} else {
  $DefaultProfilePath
}
$ProfilePath = [IO.Path]::GetFullPath($ProfilePath)
$TelemetryPath = Join-Path $DataDir "pilot-telemetry.jsonl"
$IdentityPath = Join-Path $DataDir "observer-id"
$StartupRoot = if ($StartupDir) { $StartupDir } else { [Environment]::GetFolderPath("Startup") }
$StartupLink = Join-Path $StartupRoot "OneCompute-Observer.lnk"
$LegacyStartupCmd = Join-Path $StartupRoot "OneCompute-Observer.cmd"

function Assert-ParticipantContext {
  $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  if ($sid -in @("S-1-5-18", "S-1-5-19", "S-1-5-20")) {
    throw "Run in the participant's user context. In Intune, enable 'Run this script using the logged-on credentials'. Do not use SYSTEM, LOCAL SERVICE, or NETWORK SERVICE."
  }
}

function Test-ObserverProcess {
  param([object]$Process)
  if (-not $Process.CommandLine) { return $false }
  $isConfiguredExecutable = $false
  if ($ConfiguredExecutable -and $Process.ExecutablePath) {
    try {
      $isConfiguredExecutable =
        [IO.Path]::GetFullPath([string]$Process.ExecutablePath) -ieq
        [IO.Path]::GetFullPath($ConfiguredExecutable)
    } catch {
      $isConfiguredExecutable = $false
    }
  }
  $isPythonModule = $Process.Name -in @("python.exe", "pythonw.exe") -and
    $Process.CommandLine -match '(?i)(^|\s)-m\s+worker(\s|$)'
  if (-not $isConfiguredExecutable -and -not $isPythonModule) { return $false }
  if ($Process.CommandLine -notmatch '(?i)(^|\s)--measure-only(\s|$)') { return $false }
  if ($Process.CommandLine -match '(?i)(^|\s)--profile(\s|=)') {
    $escapedProfile = [regex]::Escape($ProfilePath)
    $profileArgument = '(?i)(?:^|\s)--profile(?:\s+|=)(?:"' +
      $escapedProfile + '"|' + $escapedProfile + ')(?=\s|$)'
    return $Process.CommandLine -match $profileArgument
  }
  return $ProfilePath -eq $DefaultProfilePath
}

function Get-ObserverPids {
  try {
    @(
      Get-CimInstance Win32_Process |
        Where-Object { Test-ObserverProcess $_ } |
        Select-Object -ExpandProperty ProcessId
    )
  } catch {
    @()
  }
}

function Stop-ObserverProcesses {
  $pids = @(Get-ObserverPids)
  foreach ($observerPid in $pids) {
    Stop-Process -Id $observerPid -Force -ErrorAction SilentlyContinue
  }
  if ($pids.Count -gt 0) {
    Wait-Process -Id $pids -Timeout 10 -ErrorAction SilentlyContinue
  }
}

function Stop-ObserverTask {
  $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($existing -and $existing.State -ne "Disabled") {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  }
  return $existing
}

function Remove-PathResilient {
  param([string]$Path)
  if (-not $Path) { return }
  for ($attempt = 0; $attempt -lt 3; $attempt++) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $Path)) {
      Write-Host "Deleted $Path"
      return
    }
    Start-Sleep -Milliseconds 200
  }
  Write-Warning "Could not delete $Path"
}

function Remove-ObserverData {
  $profileDir = Split-Path -Parent $ProfilePath
  $profileLeaf = Split-Path -Leaf $ProfilePath
  $profileStem = [IO.Path]::GetFileNameWithoutExtension($ProfilePath)
  $profileSuffix = [IO.Path]::GetExtension($ProfilePath)
  $paths = @($ProfilePath, "$ProfilePath.lock", $TelemetryPath, $IdentityPath, $ConfigPath)
  $paths += @(Get-ChildItem -LiteralPath $DataDir -Filter "pilot-telemetry.jsonl.*" -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName)
  $paths += @(Get-ChildItem -LiteralPath $DataDir -Filter ".observer-id.*.tmp" -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName)
  $paths += @(Get-ChildItem -LiteralPath $DataDir -Filter ".observer-config.*.tmp" -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName)
  $paths += @(Get-ChildItem -LiteralPath $profileDir -Filter ".$profileLeaf.*.tmp" -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName)
  $paths += @(Get-ChildItem -LiteralPath $profileDir -Filter ".$profileLeaf.*.probe" -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName)
  $paths += @(Get-ChildItem -LiteralPath $profileDir -Filter "$profileStem.corrupt-*$profileSuffix" -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName)
  foreach ($path in ($paths | Select-Object -Unique)) {
    Remove-PathResilient $path
  }
}

function Remove-StartupPersistence {
  Remove-PathResilient $StartupLink
  Remove-PathResilient $LegacyStartupCmd
}

function Save-ObserverConfig {
  New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
  $temporary = Join-Path $DataDir (".observer-config.{0}.tmp" -f [guid]::NewGuid())
  try {
    @{
      version = 1
      profile_path = $ProfilePath
      executable_path = $execute
      startup_dir = $StartupRoot
      mechanism = "scheduled-task"
      task_name = $TaskName
    } | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $ConfigPath -Force
  } finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
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
      $profileJson = Get-Content -LiteralPath $ProfilePath -Raw | ConvertFrom-Json
      $samples = [int]$profileJson.availability.sample_count
    } catch {
      Write-Host "Profile JSON is invalid." -ForegroundColor Red
    }
    Write-Host "  profile age : ${age}s"
    Write-Host "  samples : $samples"
  } else {
    Write-Host "  profile : not created yet"
  }
  Write-Host "  profile path : $ProfilePath"
  if (Test-Path -LiteralPath $StartupLink) {
    Write-Host "  Startup shortcut : PRESENT" -ForegroundColor Yellow
  } elseif (Test-Path -LiteralPath $LegacyStartupCmd) {
    Write-Host "  legacy Startup launcher : PRESENT" -ForegroundColor Yellow
  }
  if (Test-Path -LiteralPath $TelemetryPath) {
    Write-Host "  legacy timeline : PRESENT; purge after validating the upgraded observer" -ForegroundColor Yellow
  } else {
    Write-Host "  legacy timeline : absent"
  }
  return
}

if ($Purge -or $Uninstall) {
  $existing = Stop-ObserverTask
  Stop-ObserverProcesses
  if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task '$TaskName'."
  } else {
    Write-Host "No scheduled task '$TaskName' found."
  }
  Remove-StartupPersistence
  Stop-ObserverProcesses
  if ($Purge) {
    Remove-ObserverData
    Write-Host "Observer stopped, uninstalled, and local measurement data purged." -ForegroundColor Green
  } else {
    Write-Host "Local profile retained at $ProfilePath. Run -Purge to delete it."
  }
  return
}

if (-not $Url) { throw "Provide -Url https://<orchestrator-host>:<port>." }
Assert-ParticipantContext
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
  "--measurement-device-class", $DeviceClass,
  "--profile", $ProfilePath
) | ForEach-Object { $workerArgs.Add($_) }
if ($MeasurementId) { $workerArgs.Add("--measurement-id"); $workerArgs.Add($MeasurementId) }
if ($TlsCa) { $workerArgs.Add("--tls-ca"); $workerArgs.Add($TlsCa) }
if ($ClientCert) { $workerArgs.Add("--client-cert"); $workerArgs.Add($ClientCert) }
if ($ClientKey) { $workerArgs.Add("--client-key"); $workerArgs.Add($ClientKey) }

if ($Exe) {
  if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) { throw "-Exe path not found: $Exe" }
  $execute = (Resolve-Path $Exe).Path
  $allArgs = @($workerArgs)
  $workDir = Split-Path -Parent $execute
} else {
  $workDir = (Resolve-Path $RepoDir).Path
  $venvPython = Join-Path $workDir ".venv\Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Observer Python not found at $venvPython. Run 'uv sync --extra dev' or pass -Exe."
  }
  $execute = (Resolve-Path -LiteralPath $venvPython).Path
  $allArgs = @("-m", "worker") + @($workerArgs)
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
  New-ScheduledTaskPrincipal -UserId $me -LogonType S4U -RunLevel Limited
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

if ((Test-Path -LiteralPath $StartupLink) -or (Test-Path -LiteralPath $LegacyStartupCmd)) {
  throw "A Startup-folder observer already exists. Run observe_me.ps1 -Uninstall first."
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

try {
  Save-ObserverConfig
} catch {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  throw
}
try {
  Start-ScheduledTask -TaskName $TaskName
} catch {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Remove-PathResilient $ConfigPath
  throw
}
$bootNote = if ($AtStartup) { " and at boot" } else { "" }
Write-Host "Installed '$TaskName' against $Url (every ${IntervalSec}s)." -ForegroundColor Green
Write-Host "  Starts now and at logon$bootNote; no job execution, live usage stream, or sample timeline."
Write-Host "  Local profile: $ProfilePath"
Write-Host "  Status: $PSCommandPath -Status"
Write-Host "  Retain data on opt-out: $PSCommandPath -Uninstall"
Write-Host "  Delete all local pilot data: $PSCommandPath -Purge"
