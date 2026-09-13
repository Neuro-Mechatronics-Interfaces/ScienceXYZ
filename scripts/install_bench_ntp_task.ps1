<#
.SYNOPSIS
    Install the bench NTP responder (scripts/bench_ntp_server.py) as a Windows
    Scheduled Task that starts on boot and stays running, serving 192.168.100.14:123.

.DESCRIPTION
    The SciFi-2 bench LAN has no internet and the router serves no NTP, so this
    laptop is the time source (see docs/adb.md). This installer registers a
    Scheduled Task that launches the Python SNTP responder at system startup (as
    SYSTEM), restarts it if it exits, binds UDP 123 on the bench IP, and opens the
    inbound firewall. It then starts the task immediately so no reboot is needed.

    Prefer scripts/setup_laptop_ntp_server.ps1 (the built-in Windows Time service)
    when you want the served time disciplined from the internet automatically; this
    task-based option serves the laptop's *current* clock, so keep Windows time
    sync on. Use this when you specifically want bench_ntp_server.py as the server.

    RUN IN AN ELEVATED (Administrator) PowerShell. Idempotent. Undo with -Uninstall.

.PARAMETER BindIp
    Bench IP to bind (default 192.168.100.14). Use 0.0.0.0 to serve all NICs.

.PARAMETER Uninstall
    Remove the scheduled task and the firewall rule.
#>
[CmdletBinding()]
param(
    [string]$BindIp = "192.168.100.14",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this in an elevated (Administrator) PowerShell."
    }
}
Assert-Admin

$taskName   = "SciFi2-Bench-NTP"
$fwRuleName = "SciFi-2 Bench NTP (UDP 123 in)"
$repoRoot   = Split-Path -Parent $PSScriptRoot
$serverPy   = Join-Path $repoRoot "scripts\bench_ntp_server.py"

if ($Uninstall) {
    Write-Host "Uninstalling bench NTP task and firewall rule..." -ForegroundColor Yellow
    Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue |
        Unregister-ScheduledTask -Confirm:$false
    Get-NetFirewallRule -DisplayName $fwRuleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    Write-Host "Removed." -ForegroundColor Green
    return
}

if (-not (Test-Path $serverPy)) { throw "not found: $serverPy" }

# Resolve a python launcher that exists for the SYSTEM account (absolute path).
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $python) { throw "python/py not found on PATH; install Python or edit this script." }
Write-Host "python: $python" -ForegroundColor Cyan
Write-Host "server: $serverPy" -ForegroundColor Cyan
Write-Host "bind  : ${BindIp}:123" -ForegroundColor Cyan

Write-Host "1) Firewall: allow inbound UDP 123..." -ForegroundColor Cyan
if (-not (Get-NetFirewallRule -DisplayName $fwRuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $fwRuleName -Direction Inbound -Protocol UDP `
        -LocalPort 123 -Action Allow -Profile Any | Out-Null
    Write-Host "   added." -ForegroundColor Green
} else { Write-Host "   already present." -ForegroundColor Green }

Write-Host "2) Register the scheduled task (start at boot, run as SYSTEM, restart on fail)..." -ForegroundColor Cyan
$action = New-ScheduledTaskAction -Execute $python `
    -Argument "`"$serverPy`" --bind $BindIp --port 123"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "Serves NTP to the SciFi-2 bench LAN from bench_ntp_server.py." | Out-Null
Write-Host "   registered: $taskName" -ForegroundColor Green

Write-Host "3) Start it now (no reboot needed)..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 2

Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State | Format-Table -AutoSize
$bound = Get-NetUDPEndpoint -LocalPort 123 -ErrorAction SilentlyContinue
if ($bound) {
    Write-Host "UDP 123 is bound:" -ForegroundColor Green
    $bound | Select-Object LocalAddress, OwningProcess | Format-Table -AutoSize
} else {
    Write-Host "WARNING: nothing bound on UDP 123 yet. Check Task Scheduler history and" -ForegroundColor Red
    Write-Host "that no other service (w32time server role) already owns 123." -ForegroundColor Red
}
Write-Host "Done. The responder auto-starts on boot and serves ${BindIp}:123." -ForegroundColor Green
Write-Host "Keep Windows time sync ON so the served clock stays accurate." -ForegroundColor Green
