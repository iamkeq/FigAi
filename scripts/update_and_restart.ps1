<#
.SYNOPSIS
    Pulls the latest FigAI code from GitHub, reinstalls dependencies, and
    restarts Fig.
.DESCRIPTION
    Stops whatever Fig process is currently running -- the registered Task
    Scheduler task if it's running that way, or a plain python/pythonw
    process otherwise (an interactive --cli session is left alone) -- pulls
    the current branch, reinstalls the package (picks up any new
    dependencies), then starts Fig back up the same way it was running
    before.

    This is different from Slack's `!restart`, which just re-execs the
    already-running process with whatever code is already on disk. Use this
    script whenever new commits have landed on GitHub; use `!restart` for a
    quick in-place restart with no code changes.
.EXAMPLE
    .\scripts\update_and_restart.ps1
#>

param(
    [string]$TaskName = "FigAI",
    [string]$WorkingDirectory = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$PythonwPath = "$PSScriptRoot\..\.venv\Scripts\pythonw.exe",
    [string]$PipPath = "$PSScriptRoot\..\.venv\Scripts\pip.exe"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PythonwPath) -or -not (Test-Path $PipPath)) {
    Write-Error "Virtual environment not found at '$WorkingDirectory\.venv'. Run setup first: python -m venv .venv; .venv\Scripts\pip install -e `".[dev]`""
    exit 1
}

Push-Location $WorkingDirectory
try {
    Write-Host "Checking for a running Fig..."
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $usingTask = $false
    if ($task -and $task.State -eq "Running") {
        $usingTask = $true
        Write-Host "Stopping scheduled task '$TaskName'..."
        Stop-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 2
    }

    # Also catch a manually-started background process (covers running it
    # by hand, and is a harmless no-op if the scheduled task already covered
    # it). An interactive `--cli` session is left running untouched.
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -match "-m\s+figai" -and $_.CommandLine -notmatch "--cli" } |
        ForEach-Object {
            Write-Host "Stopping process $($_.ProcessId)..."
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }

    Write-Host "Pulling latest changes..."
    git pull

    Write-Host "Reinstalling dependencies (picks up anything new)..."
    & $PipPath install -e ".[dev]"

    if ($usingTask) {
        Write-Host "Starting scheduled task '$TaskName'..."
        Start-ScheduledTask -TaskName $TaskName
    } else {
        Write-Host "Starting Fig in the background..."
        Start-Process -FilePath $PythonwPath -ArgumentList "-m figai" -WorkingDirectory $WorkingDirectory
    }

    Write-Host "Done -- Fig is updated and running."
} finally {
    Pop-Location
}
