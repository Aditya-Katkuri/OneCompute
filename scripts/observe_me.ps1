<#
.SYNOPSIS
  Install, inspect, stop, or purge the privacy-minimized OneCompute measurement observer.

.DESCRIPTION
  The observer samples CPU/GPU/RAM locally into
  %LOCALAPPDATA%\OneCompute\usage_profile.json. Measurement mode never pulls a job, never writes a
  per-sample JSONL timeline, and never streams live utilization. When -Url is supplied, it uploads
  only a compact derived summary. Remote upload URLs must use HTTPS; HTTP is accepted only for
  localhost development.

  -Install   Create a Startup-folder launcher and start it if it is not already running.
  -Status    Show verified observer PIDs, durable profile freshness, sample count, and autostart.
  -Uninstall Stop verified observer PIDs and remove autostart, retaining the local profile.
  -Purge     Stop and uninstall, then delete the profile, legacy telemetry, and observer identity.
#>
param(
    [ValidateRange(5, 3600)]
    [double]$IntervalSec = 30,
    [string]$Url = "",
    [string]$ProfileFile = "",
    [ValidateSet("laptop", "desktop", "devbox", "xbox", "unknown")]
    [string]$DeviceClass = "unknown",
    [ValidatePattern("^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")]
    [string]$MeasurementId = "",
    [string]$TlsCa = "",
    [string]$ClientCert = "",
    [string]$ClientKey = "",
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Purge,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Py = if (Test-Path $VenvPy) { (Resolve-Path $VenvPy).Path } else { "python" }
$DataDir = Join-Path $env:LOCALAPPDATA "OneCompute"
$ProfilePath = if ($ProfileFile) { $ProfileFile } else { Join-Path $DataDir "usage_profile.json" }
$TelemPath = Join-Path $DataDir "pilot-telemetry.jsonl"
$IdentityPath = Join-Path $DataDir "observer-id"
$Title = "OneCompute Observer"
$StartupDir = [Environment]::GetFolderPath("Startup")
$StartupCmd = Join-Path $StartupDir "OneCompute-Observer.cmd"

function Test-ObserverProcess {
    param([object]$Process)
    if (-not $Process.CommandLine) { return $false }
    if ($Process.CommandLine -notmatch '(?i)(^|\s)-m\s+worker(\s|$)') { return $false }
    if ($Process.CommandLine -notmatch '(?i)(^|\s)--measure-only(\s|$)') { return $false }
    if ($Py -ne "python" -and $Process.ExecutablePath) {
        try {
            if ((Resolve-Path $Process.ExecutablePath).Path -ne $Py) { return $false }
        } catch {
            return $false
        }
    }
    return $true
}

function Get-ObserverPids {
    try {
        @(
            Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
                Where-Object { Test-ObserverProcess $_ } |
                Select-Object -ExpandProperty ProcessId
        )
    } catch {
        @()
    }
}

function Stop-Observers {
    $pids = @(Get-ObserverPids)
    foreach ($observerPid in $pids) {
        Stop-Process -Id $observerPid -ErrorAction Stop
    }
    if ($pids.Count -gt 0) {
        Write-Host ("Stopped observer PID(s): {0}" -f ($pids -join ", ")) -ForegroundColor Green
    }
}

function Remove-KnownData {
    $paths = @($ProfilePath, $TelemPath, $IdentityPath)
    $paths += @(Get-ChildItem -LiteralPath $DataDir -Filter "pilot-telemetry.jsonl.*" -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName)
    foreach ($path in ($paths | Select-Object -Unique)) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            Remove-Item -LiteralPath $path -Force
            Write-Host "Deleted $path" -ForegroundColor Green
        }
    }
}

function Add-WorkerArg {
    param([System.Collections.Generic.List[string]]$List, [string]$Name, [string]$Value)
    if ($Value) {
        $List.Add($Name)
        $List.Add($Value)
    }
}

if (($ClientCert -and -not $ClientKey) -or ($ClientKey -and -not $ClientCert)) {
    throw "-ClientCert and -ClientKey must be provided together."
}
if ($Url) {
    $uri = $null
    if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$uri)) {
        throw "-Url must be an absolute HTTP or HTTPS URL."
    }
    if ($uri.Scheme -notin @("http", "https")) {
        throw "-Url must use HTTP or HTTPS."
    }
    $isLoopback = $uri.IsLoopback
    if ($uri.Scheme -eq "http" -and -not $isLoopback) {
        throw "Remote measurement uploads require HTTPS."
    }
    if ($uri.Scheme -ne "https" -and ($TlsCa -or $ClientCert -or $ClientKey)) {
        throw "TLS certificate options require an HTTPS URL."
    }
}

if ($Status) {
    Write-Host "OneCompute observer status" -ForegroundColor Cyan
    $procs = @(Get-ObserverPids)
    if ($procs.Count -gt 0) {
        Write-Host ("  observer : RUNNING (PIDs {0})" -f ($procs -join ", ")) -ForegroundColor Green
    } else {
        Write-Host "  observer : NOT RUNNING" -ForegroundColor Yellow
    }
    if (Test-Path -LiteralPath $ProfilePath) {
        $profileItem = Get-Item -LiteralPath $ProfilePath
        $age = [int]((Get-Date) - $profileItem.LastWriteTime).TotalSeconds
        $sampleCount = 0
        try {
            $profile = Get-Content -LiteralPath $ProfilePath -Raw | ConvertFrom-Json
            $sampleCount = [int]$profile.availability.sample_count
        } catch {
            Write-Host "  profile : INVALID JSON" -ForegroundColor Red
        }
        Write-Host ("  durable save : {0}s ago (about every 5 minutes)" -f $age)
        Write-Host ("  samples : {0}" -f $sampleCount)
    } else {
        Write-Host "  durable save : no profile yet"
    }
    Write-Host ("  profile : {0}" -f $ProfilePath)
    Write-Host ("  observer ID : {0}" -f $IdentityPath)
    $auto = if (Test-Path -LiteralPath $StartupCmd) { "ON ($StartupCmd)" } else { "OFF" }
    Write-Host ("  autostart : {0}" -f $auto)
    if (Test-Path -LiteralPath $TelemPath) {
        Write-Host "  legacy timeline : PRESENT (run -Purge after confirming the new observer)" -ForegroundColor Yellow
    } else {
        Write-Host "  legacy timeline : absent"
    }
    return
}

if ($Purge) {
    Stop-Observers
    if (Test-Path -LiteralPath $StartupCmd) {
        Remove-Item -LiteralPath $StartupCmd -Force
        Write-Host "Removed autostart -> $StartupCmd" -ForegroundColor Green
    }
    Remove-KnownData
    Write-Host "Observer stopped, uninstalled, and local measurement data purged." -ForegroundColor Green
    return
}

if ($Uninstall) {
    Stop-Observers
    if (Test-Path -LiteralPath $StartupCmd) {
        Remove-Item -LiteralPath $StartupCmd -Force
        Write-Host "Removed autostart -> $StartupCmd" -ForegroundColor Green
    } else {
        Write-Host "No autostart entry found." -ForegroundColor Yellow
    }
    Write-Host "Local profile retained at $ProfilePath. Run -Purge to delete it."
    return
}

$workerArgs = [System.Collections.Generic.List[string]]::new()
@(
    "-m", "worker",
    "--measure-only",
    "--no-telemetry",
    "--measure-interval", "$IntervalSec",
    "--measurement-device-class", $DeviceClass
) | ForEach-Object { $workerArgs.Add($_) }
Add-WorkerArg $workerArgs "--url" $Url
Add-WorkerArg $workerArgs "--profile" $ProfileFile
Add-WorkerArg $workerArgs "--measurement-id" $MeasurementId
Add-WorkerArg $workerArgs "--tls-ca" $TlsCa
Add-WorkerArg $workerArgs "--client-cert" $ClientCert
Add-WorkerArg $workerArgs "--client-key" $ClientKey

if ($Install) {
    $quotedArgs = $workerArgs | ForEach-Object { '"' + ($_ -replace '"', '""') + '"' }
    $cmdBody = "@echo off`r`ntitle $Title`r`n`"$Py`" $($quotedArgs -join ' ')`r`n"
    Set-Content -LiteralPath $StartupCmd -Value $cmdBody -Encoding ASCII
    Write-Host "Installed autostart -> $StartupCmd" -ForegroundColor Green
    if (@(Get-ObserverPids).Count -eq 0) {
        Start-Process -FilePath $StartupCmd
        Write-Host "Observer started in a new '$Title' window." -ForegroundColor Green
    } else {
        Write-Host "Observer is already running; autostart was refreshed without starting a duplicate."
    }
    Write-Host "Profile: $ProfilePath"
    return
}

$Host.UI.RawUI.WindowTitle = $Title
$mode = if ($Url) { "secure upload to $Url" } else { "local-only, no network" }
Write-Host "Starting OneCompute observer ($mode; interval ${IntervalSec}s)." -ForegroundColor Green
Write-Host "Profile: $ProfilePath"
Write-Host "----------------------------------------------------------------------"
& $Py @workerArgs
