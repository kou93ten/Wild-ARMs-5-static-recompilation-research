param(
    [string]$State = 'work/native-play/20261112-225151-68563f/reports/2026-11-12_22-56-14_f015554/report.state',
    [string]$CardSource = 'work/native-play/20261112-225151-68563f/reports/2026-11-12_22-56-14_f015554/report.state.ps2'
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$statePath = (Resolve-Path -LiteralPath (Join-Path $project $State)).Path
$cardPath = (Resolve-Path -LiteralPath (Join-Path $project $CardSource)).Path
$capture = Join-Path $PSScriptRoot 'capture_native_present.ps1'
$checker = Join-Path $PSScriptRoot 'check_wa5_text_capture.py'
$battleChecker = Join-Path $PSScriptRoot 'check_wa5_battle_text_capture.py'
$old = [Environment]::GetEnvironmentVariable('PS2_WA5_GLYPH', 'Process')

function Take-TextCapture([int]$frame, [bool]$enabled) {
    $env:PS2_WA5_GLYPH = if ($enabled) { '1' } else { '0' }
    $options = @{
        State = $statePath; CardSource = $cardPath; Frame = $frame
        Width = 3840; Height = 2160; Scale = 4; Mode = 'borderless'
    }
    if ($frame -eq 15571) { $options.Press = @('15560:cross:1') }
    $output = & $capture @options
    $picture = @($output | Where-Object { $_ -like 'Screenshot: *' } | Select-Object -Last 1)
    if (-not $picture) { throw "No screenshot for frame $frame (enabled=$enabled)" }
    return ($picture -replace '^Screenshot: ', '')
}

function Take-BattleCapture([bool]$enabled) {
    $env:PS2_WA5_GLYPH = if ($enabled) { '1' } else { '0' }
    $battleState = Join-Path $project 'work/native-play/20261111-194641-e84103/reports/2026-11-11_20-15-41_f128334/report.state'
    $battleCard = "$battleState.ps2"
    $options = @{
        State = $battleState; CardSource = $battleCard; Frame = 128337
        Width = 1920; Height = 1080; Scale = 4; ConsecutiveFrames = 14
        SceneProbe = $true
    }
    $output = & $capture @options
    $picture = @($output | Where-Object { $_ -like 'Screenshot: *' } | Select-Object -Last 1)
    if (-not $picture) { throw "No battle screenshot (enabled=$enabled)" }
    return (Split-Path -Parent ($picture -replace '^Screenshot: ', ''))
}

try {
    $firstOriginal = Take-TextCapture 15555 $false
    $firstEnhanced = Take-TextCapture 15555 $true
    $liveOriginal = Take-TextCapture 15571 $false
    $liveEnhanced = Take-TextCapture 15571 $true
    $battleOriginal = Take-BattleCapture $false
    $battleEnhanced = Take-BattleCapture $true
} finally {
    [Environment]::SetEnvironmentVariable('PS2_WA5_GLYPH', $old, 'Process')
}

& python $checker $firstOriginal $firstEnhanced $liveOriginal $liveEnhanced
if ($LASTEXITCODE -ne 0) { throw 'Wild Arms V text capture comparison failed.' }
& python $battleChecker $battleOriginal $battleEnhanced
if ($LASTEXITCODE -ne 0) { throw 'Wild Arms V battle text comparison failed.' }
Write-Output "Wild Arms V text rendering passed: $firstEnhanced, $liveEnhanced, $battleEnhanced"
