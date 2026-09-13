<#
.SYNOPSIS
    Make this Windows laptop a durable NTP server for the isolated SciFi-2 bench LAN.

.DESCRIPTION
    The bench LAN (192.168.100.0/24) has no internet and the router serves no NTP,
    so the SciFi-2 headstage (no working RTC) cannot get the time. This laptop
    reaches the internet on another path, so it can be BOTH an NTP client of the
    public pool AND an NTP server for the SciFi-2. This script configures the
    built-in Windows Time service (w32time) to do exactly that, durably (it is a
    system service and starts on boot), and opens the firewall for inbound NTP.

    The SciFi-2 is already configured (see docs/adb.md) to prefer this laptop
    (192.168.100.14) then the M4 MacBook Pro (192.168.100.106) as its NTP source.

    RUN THIS IN AN ELEVATED (Administrator) PowerShell. It is idempotent.

.NOTES
    This is operator bench tooling run by a human at the console. It does not run
    synapsectl and does not touch the device. Undo with -Disable.
#>
[CmdletBinding()]
param(
    # Public NTP source(s) this laptop disciplines its own clock from.
    [string]$UpstreamNtp = "time.windows.com,0x9 pool.ntp.org,0x9 time.google.com,0x9",
    # Revert: stop serving NTP and restore manual/off state.
    [switch]$Disable
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "This script must be run in an elevated (Administrator) PowerShell."
    }
}

Assert-Admin

$ntpServerKey = "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpServer"
$configKey    = "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Config"
$fwRuleName   = "SciFi-2 Bench NTP (UDP 123 in)"

if ($Disable) {
    Write-Host "Disabling NTP server role..." -ForegroundColor Yellow
    Set-ItemProperty -Path $ntpServerKey -Name "Enabled" -Value 0 -Type DWord
    Get-NetFirewallRule -DisplayName $fwRuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Stop-Service w32time -ErrorAction SilentlyContinue
    Set-Service  w32time -StartupType Manual
    Write-Host "NTP server role disabled. w32time set back to Manual." -ForegroundColor Green
    return
}

Write-Host "1) Ensure w32time starts automatically and is running..." -ForegroundColor Cyan
# Use the delayed-auto trigger the OS prefers for w32time.
Set-Service w32time -StartupType Automatic
Start-Service w32time

Write-Host "2) Point this laptop's own clock at public NTP (so served time is accurate)..." -ForegroundColor Cyan
& w32tm /config /manualpeerlist:"$UpstreamNtp" /syncfromflags:manual /update | Out-Host

Write-Host "3) Enable the NTP SERVER provider (answer LAN clients)..." -ForegroundColor Cyan
Set-ItemProperty -Path $ntpServerKey -Name "Enabled" -Value 1 -Type DWord
# AnnounceFlags=5 -> announce as a reliable time source even without domain hierarchy.
Set-ItemProperty -Path $configKey -Name "AnnounceFlags" -Value 5 -Type DWord

Write-Host "4) Restart w32time to apply provider change..." -ForegroundColor Cyan
Restart-Service w32time
& w32tm /config /update | Out-Host
& w32tm /resync /rediscover | Out-Host

Write-Host "5) Open the firewall for inbound NTP (UDP 123) on private/domain profiles..." -ForegroundColor Cyan
if (-not (Get-NetFirewallRule -DisplayName $fwRuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $fwRuleName -Direction Inbound -Protocol UDP `
        -LocalPort 123 -Action Allow -Profile Any | Out-Null
    Write-Host "   firewall rule added: $fwRuleName" -ForegroundColor Green
} else {
    Write-Host "   firewall rule already present." -ForegroundColor Green
}

Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan
& w32tm /query /status | Out-Host
Write-Host "NtpServer Enabled = $((Get-ItemProperty $ntpServerKey).Enabled)"
Write-Host ""
Write-Host "Done. This laptop now serves NTP on 192.168.100.14:123 and stays" -ForegroundColor Green
Write-Host "disciplined from the internet. The SciFi-2 will sync from it on reconnect." -ForegroundColor Green
Write-Host "Test from the SciFi-2 side after ~1 min, or with scripts/bench_ntp_server.py off." -ForegroundColor Green
