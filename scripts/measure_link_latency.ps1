<#
.SYNOPSIS
    Characterize host->device network latency/jitter for the SciFi-2 bench link.

.DESCRIPTION
    Sends a burst of ICMP echoes to a target and reports a full latency
    distribution -- min / mean / median / p95 / p99 / max, standard deviation,
    and loss -- rather than the 4-sample average `ping` prints. Jitter (not mean
    latency) is what matters for synchronization, so the percentiles and stddev
    are the figures of merit here.

    Intended use is a before/after comparison when changing the device Wi-Fi
    radio's power-management state. Capture a baseline, apply the device change
    (operator-run, via adb), then re-run and diff the JSON.

    Host-side only. This script never touches the device; it just measures the
    path to it. Per AGENTS.md it does not run synapsectl or command the device.

.PARAMETER Target
    IP or hostname to probe. Defaults to the SciFi-2 (192.168.100.157).

.PARAMETER Count
    Number of echoes. Default 200 (a few seconds; enough for stable p95/p99).

.PARAMETER DelayMs
    Gap between echoes in milliseconds. Default 20 (~50 Hz). Lower values keep
    the radio awake and mask power-save latency, so keep this realistic for the
    idle pattern you care about; raise it to expose power-save wake cost.

.PARAMETER Label
    Free-text tag stored in the JSON (e.g. "before" / "after-powersave-off").

.PARAMETER OutFile
    Optional path to write the result JSON. Printed to the scratchpad by default
    is not assumed; if omitted, only the console summary is shown.

.EXAMPLE
    scripts\measure_link_latency.ps1 -Label before -OutFile before.json
    # ...operator disables device Wi-Fi power-save via adb...
    scripts\measure_link_latency.ps1 -Label after -OutFile after.json
#>
[CmdletBinding()]
param(
    [string]$Target = '192.168.100.157',
    [int]$Count = 200,
    [int]$DelayMs = 20,
    [string]$Label = '',
    [string]$OutFile = ''
)

$ErrorActionPreference = 'Stop'

Write-Host "Probing $Target  ($Count echoes, ${DelayMs}ms gap, label='$Label')..." -ForegroundColor Cyan

$ping = New-Object System.Net.NetworkInformation.Ping
$rtts = New-Object System.Collections.Generic.List[double]
$lost = 0

for ($i = 0; $i -lt $Count; $i++) {
    try {
        $r = $ping.Send($Target, 1000)
        if ($r.Status -eq 'Success') {
            # RoundtripTime is integer ms; adequate for jitter on a >1ms link.
            $rtts.Add([double]$r.RoundtripTime)
        } else {
            $lost++
        }
    } catch {
        $lost++
    }
    if ($DelayMs -gt 0) { Start-Sleep -Milliseconds $DelayMs }
}

if ($rtts.Count -eq 0) {
    Write-Host "All $Count echoes lost -- target unreachable." -ForegroundColor Red
    exit 1
}

$sorted = $rtts | Sort-Object
function Pct([double[]]$s, [double]$p) {
    $idx = [int][math]::Ceiling($p / 100.0 * $s.Count) - 1
    if ($idx -lt 0) { $idx = 0 }
    if ($idx -ge $s.Count) { $idx = $s.Count - 1 }
    return $s[$idx]
}
$mean = ($rtts | Measure-Object -Average).Average
$sd = [math]::Sqrt((($rtts | ForEach-Object { ($_ - $mean) * ($_ - $mean) } | Measure-Object -Sum).Sum) / $rtts.Count)

$result = [ordered]@{
    label       = $Label
    target      = $Target
    timestamp   = (Get-Date).ToString('o')
    count       = $Count
    delay_ms    = $DelayMs
    received    = $rtts.Count
    lost        = $lost
    loss_pct    = [math]::Round(100.0 * $lost / $Count, 2)
    min_ms      = $sorted[0]
    mean_ms     = [math]::Round($mean, 2)
    median_ms   = Pct $sorted 50
    p95_ms      = Pct $sorted 95
    p99_ms      = Pct $sorted 99
    max_ms      = $sorted[$sorted.Count - 1]
    stddev_ms   = [math]::Round($sd, 2)
}

Write-Host ""
Write-Host ("  min/mean/median : {0} / {1} / {2} ms" -f $result.min_ms, $result.mean_ms, $result.median_ms)
Write-Host ("  p95 / p99 / max : {0} / {1} / {2} ms" -f $result.p95_ms, $result.p99_ms, $result.max_ms)
Write-Host ("  stddev (jitter) : {0} ms" -f $result.stddev_ms) -ForegroundColor Yellow
Write-Host ("  loss            : {0} / {1}  ({2}%)" -f $result.lost, $result.count, $result.loss_pct)
Write-Host ""

if ($OutFile) {
    $result | ConvertTo-Json | Set-Content -Path $OutFile -Encoding utf8
    Write-Host "Wrote $OutFile" -ForegroundColor Green
}
