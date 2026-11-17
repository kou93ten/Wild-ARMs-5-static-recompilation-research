param([switch]$Quick)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$checkpoint = Join-Path $PSScriptRoot 'run_native_checkpoint.ps1'
$oldBattle = Join-Path $project 'work\native-play\20261110-143433-a4593b\reports\2026-11-10_14-39-49_f018885'
$session = Join-Path $project 'work\native-play\20261110-150713-21e276'
$dialogue = Join-Path $session 'reports\2026-11-10_15-10-21_f030040'
$field = Join-Path $session 'reports\2026-11-10_15-14-32_f045106'
$recording = Join-Path $session 'states\run_2026-11-10_15-07-15_f018885.inputs'

function Invoke-Checkpoint([string]$label, [string[]]$arguments) {
    $output = & pwsh.exe -NoProfile -File $checkpoint @arguments 2>&1
    $code = $LASTEXITCODE
    foreach ($message in $output) { Write-Host $message }
    if ($code -ne 0) { throw "$label replay exited with code $code" }
    $line = $output | Where-Object { $_ -like 'Run: *' } | Select-Object -Last 1
    if (-not $line) { throw "$label replay did not report a run folder" }
    $folder = ([string]$line).Substring(5).Trim()
    $log = Get-Content -LiteralPath (Join-Path $folder 'run.log') -Raw
    if ($log -notmatch '\[ee\] stopped after \d+ frames, .*frame limit reached' -or $log -match '\[ee\] no translated code') {
        throw "$label replay did not reach its frame limit cleanly: $folder"
    }
    return $folder
}

if (-not $Quick) {
    if (-not (Test-Path -LiteralPath $recording -PathType Leaf)) { throw "Missing input recording: $recording" }
    $target = 21540
    $trimmed = Join-Path $project 'work\native-runs\battle-to-rebecca-21540.inputs'
    $lines = foreach ($line in (Get-Content -LiteralPath $recording)) {
        if ($line -match '^[ph][12]?\s+(\d+)\s' -and [long]$Matches[1] -gt $target) { continue }
        if ($line -match '^e\s+') { continue }
        $line
    }
    @($lines) + "e $target" | Set-Content -LiteralPath $trimmed -Encoding ascii
    $battleRun = Invoke-Checkpoint 'battle to Rebecca' @('-Scenario','saved-field','-Frames','21540',
        '-LoadState',(Join-Path $oldBattle 'report.state'),'-CardSource',(Join-Path $oldBattle 'report.state.ps2'),
        '-NativeReplay',$trimmed,'-FrameAt','21496')
    $battleLog = Get-Content -LiteralPath (Join-Path $battleRun 'run.log') -Raw
    if ($battleLog -notmatch '\[replay\] same to the end: frames 18885 to 21540, 177 checkpoints all equal') {
        throw "Battle input recording diverged: $battleRun"
    }
    Write-Output 'Battle-to-Rebecca input replay: 177/177 checkpoints equal.'

    $null = Invoke-Checkpoint 'dialogue' @('-Scenario','saved-field','-Frames','30160',
        '-LoadState',(Join-Path $dialogue 'report.state'),'-CardSource',(Join-Path $dialogue 'report.state.ps2'),'-FrameAt','30159')
}

$null = Invoke-Checkpoint 'Area 04' @('-Scenario','saved-field','-Frames','45226',
    '-LoadState',(Join-Path $field 'report.state'),'-CardSource',(Join-Path $field 'report.state.ps2'),'-FrameAt','45225')

$previousScale = $env:PS2_SCALE
try {
    $env:PS2_SCALE = '2'
    $hdRun = Invoke-Checkpoint 'Area 04 at 2x' @('-Scenario','saved-field','-Frames','45146',
        '-LoadState',(Join-Path $field 'report.state'),'-CardSource',(Join-Path $field 'report.state.ps2'),'-FrameAt','45145')
} finally {
    if ($null -eq $previousScale) { Remove-Item Env:PS2_SCALE -ErrorAction SilentlyContinue }
    else { $env:PS2_SCALE = $previousScale }
}
$picture = Join-Path $hdRun 'shots\frame_45145.ppm'
$header = Get-Content -LiteralPath $picture -TotalCount 2
if ($header[0] -ne 'P6' -or $header[1] -ne '1024 896') { throw "2x render has an unexpected size: $picture" }
Write-Output "Area 04 rendered cleanly at 1024x896: $picture"
