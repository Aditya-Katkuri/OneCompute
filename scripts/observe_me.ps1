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
    [int]$IntervalSec = 30,
    [string]$Url = "",
    [string]$ProfileFile = "",
    [ValidateSet("laptop", "desktop", "devbox", "xbox", "unknown")]
    [string]$DeviceClass = "unknown",
    [ValidatePattern("^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")]
    [string]$MeasurementId = "",
    [string]$TlsCa = "",
    [string]$ClientCert = "",
    [string]$ClientKey = "",
    [string]$Python = "",
    [string]$StartupDir = "",
    [string]$TaskName = "OneCompute Observer",
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Purge,
    [switch]$Status,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DataDir = Join-Path $env:LOCALAPPDATA "OneCompute"
$DefaultProfilePath = [IO.Path]::GetFullPath((Join-Path $DataDir "usage_profile.json"))
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
$ProfilePath = if ($ProfileFile) {
    $ProfileFile
} elseif ($ConfiguredProfile) {
    $ConfiguredProfile
} else {
    $DefaultProfilePath
}
$ProfilePath = [IO.Path]::GetFullPath($ProfilePath)
$TelemPath = Join-Path $DataDir "pilot-telemetry.jsonl"
$IdentityPath = Join-Path $DataDir "observer-id"
$Title = "OneCompute Observer"
$StartupRoot = if ($StartupDir) { $StartupDir } else { [Environment]::GetFolderPath("Startup") }
$StartupLink = Join-Path $StartupRoot "OneCompute-Observer.lnk"
$LegacyStartupCmd = Join-Path $StartupRoot "OneCompute-Observer.cmd"

function Quote-Argument {
    param([string]$Value)
    if ($Value -notmatch '[\s"]') { return $Value }
    return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function Resolve-ObserverPython {
    $candidate = if ($Python) { $Python } elseif (Test-Path -LiteralPath $VenvPy) { $VenvPy } else { "" }
    if (-not $candidate) {
        throw "Observer Python not found. Run 'uv sync --extra dev' or pass -Python with an absolute interpreter path."
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Python interpreter not found: $candidate"
    }
    return (Resolve-Path -LiteralPath $candidate).Path
}

function Assert-ParticipantContext {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $sid = $identity.User.Value
    if ($sid -in @("S-1-5-18", "S-1-5-19", "S-1-5-20")) {
        throw "Run in the participant's user context, not as SYSTEM, LOCAL SERVICE, or NETWORK SERVICE."
    }
    $principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
    if (
        -not $DryRun -and
        $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    ) {
        throw "Run the personal observer from a standard, non-elevated PowerShell window."
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

function Stop-Observers {
    $pids = @(Get-ObserverPids)
    foreach ($observerPid in $pids) {
        Stop-Process -Id $observerPid -Force -ErrorAction SilentlyContinue
    }
    if ($pids.Count -gt 0) {
        Wait-Process -Id $pids -Timeout 10 -ErrorAction SilentlyContinue
    }
    if ($pids.Count -gt 0) {
        Write-Host ("Stopped observer PID(s): {0}" -f ($pids -join ", ")) -ForegroundColor Green
    }
}

function Remove-PathResilient {
    param([string]$Path)
    if (-not $Path) { return }
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        if (-not (Test-Path -LiteralPath $Path)) { return }
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath $Path)) {
            Write-Host "Deleted $Path" -ForegroundColor Green
            return
        }
        Start-Sleep -Milliseconds 200
    }
    Write-Warning "Could not delete $Path"
}

function Remove-KnownData {
    $profileDir = Split-Path -Parent $ProfilePath
    $profileLeaf = Split-Path -Leaf $ProfilePath
    $profileStem = [IO.Path]::GetFileNameWithoutExtension($ProfilePath)
    $profileSuffix = [IO.Path]::GetExtension($ProfilePath)
    $paths = @($ProfilePath, "$ProfilePath.lock", $TelemPath, $IdentityPath, $ConfigPath)
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

function Remove-AllPersistence {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Green
    }
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
            executable_path = $Py
            startup_dir = $StartupRoot
            mechanism = "startup"
            task_name = $TaskName
        } | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $ConfigPath -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
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
foreach ($certificatePath in @($TlsCa, $ClientCert, $ClientKey)) {
    if ($certificatePath -and -not (Test-Path -LiteralPath $certificatePath -PathType Leaf)) {
        throw "Certificate file not found: $certificatePath"
    }
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
    if (-not $isLoopback -and (-not $TlsCa -or -not $ClientCert -or -not $ClientKey)) {
        throw "Remote measurement uploads require -TlsCa, -ClientCert, and -ClientKey for pinned mTLS."
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
            $profileJson = Get-Content -LiteralPath $ProfilePath -Raw | ConvertFrom-Json
            $sampleCount = [int]$profileJson.availability.sample_count
        } catch {
            Write-Host "  profile : INVALID JSON" -ForegroundColor Red
        }
        Write-Host ("  durable save : {0}s ago (about every minute)" -f $age)
        Write-Host ("  samples : {0}" -f $sampleCount)
    } else {
        Write-Host "  durable save : no profile yet"
    }
    Write-Host ("  profile : {0}" -f $ProfilePath)
    Write-Host ("  observer ID : {0}" -f $IdentityPath)
    $auto = if (Test-Path -LiteralPath $StartupLink) {
        "ON ($StartupLink)"
    } elseif (Test-Path -LiteralPath $LegacyStartupCmd) {
        "LEGACY ($LegacyStartupCmd)"
    } else {
        "OFF"
    }
    Write-Host ("  autostart : {0}" -f $auto)
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Host ("  managed task : {0}" -f $task.State)
    }
    if (Test-Path -LiteralPath $TelemPath) {
        Write-Host "  legacy timeline : PRESENT (run -Purge after confirming the new observer)" -ForegroundColor Yellow
    } else {
        Write-Host "  legacy timeline : absent"
    }
    return
}

if ($Purge) {
    Stop-Observers
    Remove-AllPersistence
    Stop-Observers
    Remove-KnownData
    Write-Host "Observer stopped, uninstalled, and local measurement data purged." -ForegroundColor Green
    return
}

if ($Uninstall) {
    Stop-Observers
    Remove-AllPersistence
    Stop-Observers
    Write-Host "Local profile retained at $ProfilePath. Run -Purge to delete it."
    return
}

$workerArgs = [System.Collections.Generic.List[string]]::new()
@(
    "-m", "worker",
    "--measure-only",
    "--no-telemetry",
    "--measure-interval", "$IntervalSec",
    "--measurement-device-class", $DeviceClass,
    "--profile", $ProfilePath
) | ForEach-Object { $workerArgs.Add($_) }
Add-WorkerArg $workerArgs "--url" $Url
Add-WorkerArg $workerArgs "--measurement-id" $MeasurementId
Add-WorkerArg $workerArgs "--tls-ca" $TlsCa
Add-WorkerArg $workerArgs "--client-cert" $ClientCert
Add-WorkerArg $workerArgs "--client-key" $ClientKey

if ($Install) {
    Assert-ParticipantContext
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        throw "Scheduled task '$TaskName' already exists. Run install_observer.ps1 -Uninstall first."
    }
    $Py = Resolve-ObserverPython
    $ConfiguredExecutable = $Py
    $argument = ($workerArgs | ForEach-Object { Quote-Argument $_ }) -join " "
    if ($DryRun) {
        Write-Host "[dry run] Startup shortcut '$StartupLink'"
        Write-Host "  Execute: $Py"
        Write-Host "  Arguments: $argument"
        Write-Host "  Working directory: $RepoRoot"
        return
    }
    New-Item -ItemType Directory -Path $StartupRoot -Force | Out-Null
    Remove-PathResilient $LegacyStartupCmd
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($StartupLink)
    $shortcut.TargetPath = $Py
    $shortcut.Arguments = $argument
    $shortcut.WorkingDirectory = $RepoRoot
    $shortcut.Description = $Title
    $shortcut.Save()
    try {
        Save-ObserverConfig
    } catch {
        Remove-PathResilient $StartupLink
        throw
    }
    if (@(Get-ObserverPids).Count -eq 0) {
        try {
            Start-Process -FilePath $StartupLink
        } catch {
            Remove-PathResilient $StartupLink
            Remove-PathResilient $ConfigPath
            throw
        }
        Write-Host "Observer started." -ForegroundColor Green
    } else {
        Write-Host "Observer is already running; autostart was refreshed without starting a duplicate."
    }
    Write-Host "Installed autostart -> $StartupLink" -ForegroundColor Green
    Write-Host "Profile: $ProfilePath"
    return
}

Assert-ParticipantContext
$Py = Resolve-ObserverPython
$Host.UI.RawUI.WindowTitle = $Title
$mode = if ($Url) { "secure upload to $Url" } else { "local-only, no network" }
Write-Host "Starting OneCompute observer ($mode; interval ${IntervalSec}s)." -ForegroundColor Green
Write-Host "Profile: $ProfilePath"
Write-Host "----------------------------------------------------------------------"
& $Py @workerArgs
