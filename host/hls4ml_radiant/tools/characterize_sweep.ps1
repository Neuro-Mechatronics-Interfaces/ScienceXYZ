# Synthesize each generated core variant at the real 40 MHz (25 ns) SDC with
# Radiant 2026.1 and tabulate LUT4 / FF / estimated Fmax / worst slack.
#
# Windows-only (radiantc.exe). Generation of the variants is done separately in
# WSL by pipeline_sweep_gen.py. Synthesis only -- no map/PAR, no device.

param(
    [string]$SweepDir  = "$PSScriptRoot\..\synthesis\sweep",
    [string]$WorkRoot  = "$PSScriptRoot\..\synthesis\sweep\char",
    [string]$RadiantC  = "C:\lscc\radiant\2026.1\bin\nt64\radiantc.exe",
    [string]$Tcl       = "$PSScriptRoot\characterize_core.tcl"
)

$ErrorActionPreference = 'Stop'
$SweepDir = (Resolve-Path $SweepDir).Path
$Tcl      = (Resolve-Path $Tcl).Path
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null

function Parse-Srr($srr) {
    $lut = $null; $ff = $null; $fmax = $null; $slack = $null; $levels = $null
    foreach ($line in Get-Content $srr) {
        if ($line -match 'Total number of LUTs:\s*(\d+)')      { $lut = [int]$Matches[1] }
        if ($line -match 'Total number of registers:\s*(\d+)') { $ff  = [int]$Matches[1] }
        # clk  40.0 MHz  34.3 MHz  25.000  29.125  -4.125  declared ...
        if ($line -match '^\s*clk\s+[\d.]+\s*MHz\s+([\d.]+)\s*MHz\s+[\d.]+\s+[\d.]+\s+(-?[\d.]+)') {
            $fmax = [double]$Matches[1]; $slack = [double]$Matches[2]
        }
        if ($line -match 'Number of logic level\(s\):\s*(\d+)' -and $null -eq $levels) {
            $levels = [int]$Matches[1]
        }
    }
    [pscustomobject]@{ LUT4=$lut; FF=$ff; FmaxMHz=$fmax; SlackNs=$slack; LogicLevels=$levels }
}

$results = @()
Get-ChildItem -Path $SweepDir -Filter 'canary_*ns.sv' | Sort-Object Name | ForEach-Object {
    $sv = $_.FullName
    if ($_.Name -match 'canary_([\d.]+)ns\.sv') { $period = $Matches[1] } else { $period = $_.BaseName }
    $proj = "char_${period}ns" -replace '\.', 'p'
    $work = Join-Path $WorkRoot $proj
    Write-Host "=== synthesizing $($_.Name) (requested-gen period ${period} ns) at 25 ns SDC ==="
    & $RadiantC $Tcl $sv $proj $work 2>&1 | Out-Null
    $srr = Join-Path $work "impl_1\${proj}_impl_1.srr"
    if (-not (Test-Path $srr)) { $srr = Join-Path $work "impl_1\$proj.srr" }
    if (Test-Path $srr) {
        $m = Parse-Srr $srr
        $meets = if ($m.SlackNs -ne $null -and $m.SlackNs -ge 0) { 'YES' } else { 'no' }
        $row = [pscustomobject]@{
            GenPeriodNs=$period; LUT4=$m.LUT4; FF=$m.FF;
            FmaxMHz=$m.FmaxMHz; SlackNs=$m.SlackNs; LogicLevels=$m.LogicLevels; Meets40MHz=$meets
        }
    } else {
        $row = [pscustomobject]@{
            GenPeriodNs=$period; LUT4=$null; FF=$null;
            FmaxMHz=$null; SlackNs=$null; LogicLevels=$null; Meets40MHz='ERR(no srr)'
        }
    }
    $results += $row
    $row | Format-List | Out-String | Write-Host
}

Write-Host "`n================ SWEEP SUMMARY (40 MHz / 25 ns target) ================"
$results | Sort-Object { [double]$_.GenPeriodNs } |
    Format-Table GenPeriodNs, LUT4, FF, FmaxMHz, SlackNs, LogicLevels, Meets40MHz -AutoSize | Out-String | Write-Host

$csv = Join-Path $WorkRoot 'sweep_results.csv'
$results | Export-Csv -NoTypeInformation -Path $csv
Write-Host "CSV: $csv"
