<#
.SYNOPSIS
    Sync THIS Windows 11 laptop's clock from the M4 MacBook Pro (192.168.100.106)
    when it is reachable on the bench LAN, using the Mac as the NTP time source.

.DESCRIPTION
    This is the inverse of scripts/setup_laptop_ntp_server.ps1 (which makes this
    laptop serve NTP). Here the MacBook Pro is the reference clock and this
    Windows machine is the SNTP client: w32tm queries UDP 123 on 192.168.100.106
    and steps/slews this machine's clock to match.

    A stock macOS does NOT answer NTP from LAN peers by default -- it runs a time
    CLIENT, not a server. So the script first PROBES the Mac: it pings it, then
    sends one SNTP request to 192.168.100.106:123. If the Mac does not answer NTP,
    the script does not touch this clock; it prints the one command to run on the
    Mac to turn on NTP serving, then exits (nonzero) so nothing silently fails.

    By default the change is a ONE-SHOT resync: the script records the current
    w32time source config, points w32time at the Mac, resyncs once, then RESTORES
    the previous config. Pass -Persist to leave w32time configured to keep the
    Mac as its manual peer (useful for a long bench session; revert with -Restore).

    RUN THIS IN AN ELEVATED (Administrator) PowerShell. It is idempotent.

.PARAMETER MacIp
    IP of the MacBook Pro NTP source (default 192.168.100.106).

.PARAMETER Persist
    Leave w32time configured to keep syncing from the Mac. This does NOT pin the
    Mac as the only source: it writes a fallback peer list with the Mac FIRST and
    the internet pool AFTER it (-InternetFallback), so w32time uses the Mac while
    it is reachable and automatically falls back to internet time if the Mac
    leaves the network. Revert later with -Restore.

.PARAMETER InternetFallback
    Peer list appended after the Mac under -Persist, used when the Mac is
    unreachable (default: time.windows.com / pool.ntp.org / time.google.com).

.PARAMETER Restore
    Restore w32time to sync from the internet pool (-InternetFallback) and undo a
    prior -Persist. Does not require the Mac to be reachable.

.NOTES
    Operator bench tooling, run by a human at the console. Does not run
    synapsectl and does not touch the SciFi-2 device.
#>
[CmdletBinding()]
param(
    [string]$MacIp = "192.168.100.106",
    [string]$InternetFallback = "time.windows.com,0x9 pool.ntp.org,0x9 time.google.com,0x9",
    [switch]$Persist,
    [switch]$Restore
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

$defaultPeer = $InternetFallback

if ($Restore) {
    Write-Host "Restoring w32time to the internet pool ($InternetFallback)..." -ForegroundColor Yellow
    & w32tm /config /manualpeerlist:"$InternetFallback" /syncfromflags:manual /update | Out-Host
    Restart-Service w32time
    & w32tm /resync /rediscover | Out-Host
    Write-Host "Restored. Current source:" -ForegroundColor Green
    & w32tm /query /source | Out-Host
    return
}

# --- 1) Is the Mac on the network at all? -----------------------------------
Write-Host "1) Probing $MacIp reachability (ICMP)..." -ForegroundColor Cyan
if (-not (Test-Connection -ComputerName $MacIp -Count 2 -Quiet)) {
    Write-Host "   $MacIp does not answer ping; the MacBook is not on the network." -ForegroundColor Red
    Write-Host "   Nothing changed. Bring the Mac onto 192.168.100.0/24 and retry." -ForegroundColor Red
    exit 1
}
Write-Host "   reachable." -ForegroundColor Green

# --- 2) Is the Mac actually SERVING NTP on UDP 123? -------------------------
# Send one 48-byte SNTP client request and wait briefly for a server reply.
# Stock macOS does not serve NTP to peers; this catches that before we retarget
# w32time at a host that will never answer.
Write-Host "2) Probing $MacIp:123 for an NTP server reply..." -ForegroundColor Cyan
$servesNtp = $false
try {
    $udp = New-Object System.Net.Sockets.UdpClient
    $udp.Client.ReceiveTimeout = 2000
    $udp.Connect($MacIp, 123)
    # LI=0, VN=4, Mode=3 (client); rest zeroed.
    $req = New-Object byte[] 48
    $req[0] = 0x23
    [void]$udp.Send($req, $req.Length)
    $remote = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
    $resp = $udp.Receive([ref]$remote)
    if ($resp.Length -ge 48 -and (($resp[0] -band 0x07) -eq 4)) {
        $servesNtp = $true    # Mode 4 = server reply
    }
} catch {
    $servesNtp = $false
} finally {
    if ($udp) { $udp.Close() }
}

if (-not $servesNtp) {
    Write-Host "   $MacIp is up but did NOT answer NTP on UDP 123." -ForegroundColor Red
    Write-Host "   macOS does not serve NTP to peers by default. Enable it ON THE MAC:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "     # macOS Ventura+ (uses timed via ntp-restrict):" -ForegroundColor Gray
    Write-Host "     sudo sh -c 'echo \"restrict 192.168.100.0 mask 255.255.255.0\" >> /etc/ntp-restrict.conf'" -ForegroundColor Gray
    Write-Host "     sudo launchctl enable system/com.apple.timed" -ForegroundColor Gray
    Write-Host ""
    Write-Host "     # Older macOS with ntpd:" -ForegroundColor Gray
    Write-Host "     sudo launchctl load -w /System/Library/LaunchDaemons/org.ntp.ntpd.plist" -ForegroundColor Gray
    Write-Host ""
    Write-Host "   Also allow inbound UDP 123 in the Mac's firewall. Then re-run this script." -ForegroundColor Yellow
    Write-Host "   Nothing changed on this Windows machine." -ForegroundColor Red
    exit 2
}
Write-Host "   Mac is serving NTP." -ForegroundColor Green

# --- 3) Record current config so a one-shot sync can be undone --------------
$prevPeer = $null
try {
    $q = & w32tm /query /peers 2>$null
    $q2 = & w32tm /query /configuration 2>$null
    # NtpServer line under [TimeProviders] holds the manual peer list.
    $line = ($q2 | Select-String -Pattern 'NtpServer:').ToString()
    if ($line -match 'NtpServer:\s*(.+?)\s*\(') { $prevPeer = $Matches[1].Trim() }
} catch { }
if (-not $prevPeer) { $prevPeer = $defaultPeer }

# --- 4) Point w32time at the Mac and resync ---------------------------------
# For -Persist, write a FALLBACK peer list (Mac first, then the internet pool):
# w32tm tries peers in order and moves past an unreachable one, so the clock
# uses the Mac while it is present and falls back to internet time when it is
# not. For a one-shot, use the Mac alone so the verification below measures the
# Mac cleanly; the prior list is restored afterward.
if ($Persist) {
    $peerList = "${MacIp},0x9 $InternetFallback"
} else {
    $peerList = "${MacIp},0x9"
}
Write-Host "3) Pointing w32time at '$peerList' and syncing..." -ForegroundColor Cyan
Set-Service w32time -StartupType Automatic
Start-Service w32time -ErrorAction SilentlyContinue
& w32tm /config /manualpeerlist:"$peerList" /syncfromflags:manual /update | Out-Host
Restart-Service w32time
& w32tm /resync /rediscover | Out-Host

Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan
& w32tm /query /source | Out-Host
& w32tm /query /status | Out-Host
Write-Host "Offset this machine vs the Mac (stripchart, 1 sample):" -ForegroundColor Cyan
& w32tm /stripchart /computer:$MacIp /samples:1 /dataonly | Out-Host

# --- 5) Restore prior config unless -Persist --------------------------------
if ($Persist) {
    Write-Host ""
    Write-Host "-Persist set: w32time peer list is now '$peerList'." -ForegroundColor Green
    Write-Host "It uses the Mac while reachable and falls back to the internet pool" -ForegroundColor Green
    Write-Host "if the Mac leaves the network. Revert with:  scripts\sync_time_from_macbook.ps1 -Restore" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "One-shot done; restoring previous peer list ($prevPeer)..." -ForegroundColor Cyan
    & w32tm /config /manualpeerlist:"$prevPeer" /syncfromflags:manual /update | Out-Host
    Restart-Service w32time
    Write-Host "Clock is now synced to the Mac; source config restored." -ForegroundColor Green
    Write-Host "Use -Persist to keep the Mac as the ongoing source." -ForegroundColor Green
}
