<#
.SYNOPSIS
    Start, stop, restart, or check on Fig -- no command window needed.
.DESCRIPTION
    Fig runs as a windowless background process (pythonw.exe), so there's no
    terminal to keep open. If the "FigAI" scheduled task exists (see
    register_task.ps1), start/stop go through it; otherwise Fig is started
    directly as a hidden background process.
.EXAMPLE
    .\scripts\figai.ps1 start
    .\scripts\figai.ps1 status
    .\scripts\figai.ps1 logs
#>

param(
    [ValidateSet("start", "stop", "restart", "status", "logs")]
    [string]$Action = "status",
    [string]$TaskName = "FigAI"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"

function Get-FigProcesses {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -match "-m\s+figai" -and $_.CommandLine -notmatch "--cli" }
}

function Stop-Fig {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and $task.State -eq "Running") { Stop-ScheduledTask -TaskName $TaskName }
    Get-FigProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Start-Fig {
    if (Get-FigProcesses) { Write-Host "Fig is already running."; return }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Started via scheduled task '$TaskName'."
    } else {
        if (-not (Test-Path $Pythonw)) { Write-Error "No virtual environment at '$Root\.venv'. Run setup first." }
        Start-Process -FilePath $Pythonw -ArgumentList "-m figai" -WorkingDirectory $Root -WindowStyle Hidden
        Write-Host "Started in the background."
    }
}

switch ($Action) {
    "start"   { Start-Fig }
    "stop"    { Stop-Fig; Write-Host "Stopped." }
    "restart" { Stop-Fig; Start-Sleep -Seconds 2; Start-Fig }
    "status"  {
        $running = @(Get-FigProcesses)
        if ($running.Count -gt 0) { Write-Host "Fig is running (PID $($running[0].ProcessId))." } else { Write-Host "Fig is not running." }
    }
    "logs"    { Get-Content (Join-Path $Root "logs\figai.log") -Tail 50 -Wait }
}
