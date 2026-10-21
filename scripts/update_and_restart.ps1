<#
.SYNOPSIS
    Updates Fig from GitHub and makes sure it's running -- on demand, or every night.
.DESCRIPTION
    Default: stop Fig, pull the current branch, reinstall dependencies, start Fig.

    -OnlyIfChanged (what the nightly task uses): fetch first. If there's nothing
    new and Fig is running, do nothing. If there's nothing new but Fig isn't
    running, just start it. Only when GitHub has new commits does it stop, pull,
    reinstall, and restart. If the pull fails, the existing code is started
    again rather than leaving Fig down, and if Fig isn't running shortly after an
    update it rolls back to the previous commit (git reset --keep) and restarts
    that, so a bad merge can't take Fig down unattended.

    -InstallNightly [-At 04:00]: register a daily Task Scheduler task that runs
    this script with -OnlyIfChanged. -RemoveNightly deletes it.

    Output is appended to logs\update.log. This is different from Slack's
    `!restart` (re-execs whatever code is on disk) and from asking Fig to
    "update yourself" (needs Fig to be up and responding).
.EXAMPLE
    .\scripts\update_and_restart.ps1
.EXAMPLE
    .\scripts\update_and_restart.ps1 -InstallNightly -At 04:00
#>

param(
    [switch]$OnlyIfChanged,
    [switch]$InstallNightly,
    [switch]$RemoveNightly,
    [string]$At = "04:00",
    [int]$HealthCheckSeconds = 20,
    [string]$TaskName = "FigAI",
    [string]$NightlyTaskName = "FigAI-NightlyUpdate",
    [string]$WorkingDirectory = (Resolve-Path "$PSScriptRoot\..").Path
)

$ErrorActionPreference = "Stop"
$Pythonw = Join-Path $WorkingDirectory ".venv\Scripts\pythonw.exe"
$Pip = Join-Path $WorkingDirectory ".venv\Scripts\pip.exe"

function Invoke-Native {
    # Native commands write progress to stderr; don't let that abort the script.
    param([scriptblock]$Block)
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Block } finally { $ErrorActionPreference = $savedPreference }
}

function Get-FigProcesses {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -match "-m\s+figai" -and $_.CommandLine -notmatch "--cli" }
}

function Test-FigRunning {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and $task.State -eq "Running") { return $true }
    return [bool](Get-FigProcesses)
}

function Stop-Fig {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and $task.State -eq "Running") { Stop-ScheduledTask -TaskName $TaskName }
    Get-FigProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

function Start-Fig {
    if (Get-FigProcesses) { Write-Host "Fig is already running."; return }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Started Fig via scheduled task '$TaskName'."
    } else {
        Start-Process -FilePath $Pythonw -ArgumentList "-m figai" -WorkingDirectory $WorkingDirectory -WindowStyle Hidden
        Write-Host "Started Fig in the background."
    }
}

if ($RemoveNightly) {
    Unregister-ScheduledTask -TaskName $NightlyTaskName -Confirm:$false -ErrorAction Stop
    Write-Host "Removed the nightly update task '$NightlyTaskName'."
    return
}

if ($InstallNightly) {
    $arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" -OnlyIfChanged"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $WorkingDirectory
    $trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]$At)
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    # Same account and session as Fig itself, so a restart lands in your desktop session.
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    try {
        Register-ScheduledTask -TaskName $NightlyTaskName -Action $action -Trigger $trigger -Principal $principal `
            -Settings $settings -Description "Nightly: pull FigAI updates if there are any and make sure Fig is running" `
            -Force -ErrorAction Stop | Out-Null
        Get-ScheduledTask -TaskName $NightlyTaskName -ErrorAction Stop | Out-Null
    } catch {
        Write-Warning "Could not register the nightly task: $($_.Exception.Message)"
        Write-Warning "Re-run this from PowerShell opened with 'Run as administrator'."
        exit 1
    }
    Write-Host "Registered '$NightlyTaskName': every day at $At it updates Fig if GitHub has new commits and makes sure Fig is running."
    Write-Host "If the PC is asleep or off at that time, it runs as soon as you're logged in again."
    Write-Host "Try it now:  Start-ScheduledTask -TaskName '$NightlyTaskName'   (then read logs\update.log)"
    Write-Host "Remove it:   .\scripts\update_and_restart.ps1 -RemoveNightly"
    return
}

if (-not (Test-Path $Pythonw) -or -not (Test-Path $Pip)) {
    Write-Error "Virtual environment not found at '$WorkingDirectory\.venv'. Run setup first: python -m venv .venv; .venv\Scripts\pip install -e `".[dev]`""
    exit 1
}

$logDir = Join-Path $WorkingDirectory "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Start-Transcript -Path (Join-Path $logDir "update.log") -Append | Out-Null
Push-Location $WorkingDirectory
try {
    Write-Host "=== $(Get-Date -Format s) Fig update (OnlyIfChanged=$OnlyIfChanged) ==="

    if ($OnlyIfChanged) {
        Invoke-Native { git fetch --quiet }
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "git fetch failed; checking that Fig is running and stopping there."
            if (-not (Test-FigRunning)) { Start-Fig }
            exit 1
        }
        $local = (Invoke-Native { git rev-parse HEAD }) | Select-Object -First 1
        $upstream = (Invoke-Native { git rev-parse "@{u}" }) | Select-Object -First 1
        if ($LASTEXITCODE -eq 0 -and $local -and $local -eq $upstream) {
            if (Test-FigRunning) {
                Write-Host "Already up to date and running; nothing to do."
            } else {
                Write-Host "Up to date but Fig isn't running -- starting it."
                Start-Fig
            }
            return
        }
        Write-Host "New commits available: updating."
    }

    $previous = (Invoke-Native { git rev-parse HEAD }) | Select-Object -First 1

    Stop-Fig

    Invoke-Native { git pull }
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "git pull failed; starting the existing code again instead of leaving Fig down."
        Start-Fig
        exit 1
    }

    Write-Host "Reinstalling dependencies (picks up anything new)..."
    Invoke-Native { & $Pip install -e ".[dev]" }
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "pip install failed; starting Fig anyway."
    }

    Start-Fig

    Start-Sleep -Seconds $HealthCheckSeconds
    if ($previous -and -not (Test-FigRunning)) {
        Write-Warning "Fig isn't running $HealthCheckSeconds seconds after the update; rolling back to $previous."
        Invoke-Native { git reset --keep $previous }
        if ($LASTEXITCODE -eq 0) {
            Invoke-Native { & $Pip install -e ".[dev]" }
            Start-Fig
            Write-Warning "Rolled back and restarted the previous version. See logs\figai.log and logs\figai-console.log."
        } else {
            Write-Warning "Rollback failed too -- Fig is down; check the repo state by hand."
        }
        exit 1
    }
    Write-Host "Done -- Fig is updated and running."
} finally {
    Pop-Location
    Stop-Transcript | Out-Null
}
