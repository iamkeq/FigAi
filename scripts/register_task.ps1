<#
.SYNOPSIS
    Registers Fig to run at logon via Windows Task Scheduler.
.DESCRIPTION
    Creates a scheduled task that launches Fig with pythonw.exe (no console window)
    when you log on. Runs only while you're logged on, and restarts automatically
    up to 5 times if it crashes.
.EXAMPLE
    .\scripts\register_task.ps1
.EXAMPLE
    .\scripts\register_task.ps1 -PythonwPath "C:\Python312\pythonw.exe"
#>

param(
    [string]$TaskName = "FigAI",
    [string]$PythonwPath = (Join-Path (Resolve-Path "$PSScriptRoot\..").Path ".venv\Scripts\pythonw.exe"),
    [string]$WorkingDirectory = (Resolve-Path "$PSScriptRoot\..").Path
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PythonwPath)) {
    Write-Error "pythonw.exe not found at '$PythonwPath'. Create the virtual environment first: python -m venv .venv; .venv\Scripts\pip install -e ."
    exit 1
}

$action = New-ScheduledTaskAction -Execute $PythonwPath -Argument "-m figai" -WorkingDirectory $WorkingDirectory
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero)  # Task Scheduler's default would kill Fig after 72 hours

function New-StartupShortcut {
    # No-admin fallback: a shortcut in the Startup folder launches Fig hidden at
    # logon. Unlike the scheduled task it doesn't restart Fig if it crashes.
    $link = Join-Path ([Environment]::GetFolderPath("Startup")) "FigAI.lnk"
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
    $shortcut.TargetPath = $PythonwPath
    $shortcut.Arguments = "-m figai"
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.WindowStyle = 7
    $shortcut.Save()
    return $link
}

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
        -Description "Fig: personal background AI assistant" -Force -ErrorAction Stop | Out-Null
    # Confirm it really exists -- never report success on faith.
    Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
} catch {
    Write-Warning "Could not register the scheduled task: $($_.Exception.Message)"
    Write-Warning "For the full setup (restarts on crash), re-run this from PowerShell opened with 'Run as administrator'."
    $link = New-StartupShortcut
    Write-Host "Fallback installed instead: '$link' starts Fig (hidden) at every logon. Start it now with:"
    Write-Host "  .\scripts\figai.ps1 start"
    exit 1
}

Write-Host "Registered scheduled task '$TaskName'."
Write-Host "It starts automatically at your next logon, or right now with:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Unregister it with:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
