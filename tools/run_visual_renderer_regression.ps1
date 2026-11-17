param(
    [string]$Area01Manifest = 'work/visual-scenarios/black-box-area01/manifest.json',
    [string]$FieldManifest = 'work/visual-scenarios/black-box-area02/manifest.json',
    [string]$BattleManifest = 'work/visual-scenarios/black-box-area02-duel-battle/manifest.json'
)

$ErrorActionPreference = 'Stop'
$area01 = Get-Content -LiteralPath (Resolve-Path -LiteralPath $Area01Manifest) -Raw | ConvertFrom-Json
$field = Get-Content -LiteralPath (Resolve-Path -LiteralPath $FieldManifest) -Raw | ConvertFrom-Json
$battle = Get-Content -LiteralPath (Resolve-Path -LiteralPath $BattleManifest) -Raw | ConvertFrom-Json
if ($area01.area -ne 'Black Box - Area 01' -or [long]$area01.source_frame -ne 5600 -or
    $field.area -ne 'Black Box - Area 02' -or [long]$field.source_frame -ne 6910 -or
    [long]$battle.source_frame -ne 7360) {
    throw 'The renderer regression requires the verified Black Box scenarios.'
}
foreach ($scenario in @($area01, $field, $battle)) {
    foreach ($key in @('state', 'card')) {
        $expected = if ($key -eq 'state') { $scenario.state_sha256 } else { $scenario.card_sha256 }
        if ((Get-FileHash -LiteralPath $scenario.$key -Algorithm SHA256).Hash -ne $expected) {
            throw "$($scenario.name) $key hash changed."
        }
    }
}

function Capture([string]$state, [string]$card, [long]$frame, [int]$count,
                 [string[]]$axes, [string[]]$presses) {
    $args = @{
        State = $state; CardSource = $card; Frame = $frame
        ConsecutiveFrames = $count; Width = 1280; Height = 720
        PairReference = $true; SceneProbe = $true
    }
    if ($axes.Count) { $args.Axis = $axes }
    if ($presses.Count) { $args.Press = $presses }
    $output = & (Join-Path $PSScriptRoot 'capture_native_present.ps1') @args
    $picture = ($output | Where-Object { $_ -like 'Screenshot: *' } | Select-Object -Last 1) -replace '^Screenshot: ', ''
    if (-not $picture -or -not (Test-Path -LiteralPath $picture -PathType Leaf)) {
        throw "Capture at frame $frame failed: $($output -join ' ')"
    }
    return Join-Path (Split-Path -Parent $picture) 'capture.json'
}

function CheckSequence([string]$manifest, [string]$expect) {
    $output = & python (Join-Path $PSScriptRoot 'check_presentation_capture.py') $manifest `
        --expect $expect --continuous-ready --synchronized-static-gs
    if ($LASTEXITCODE -ne 0) { throw "Sequence comparison failed: $manifest`n$($output -join "`n")" }
    Write-Output ($output | Select-Object -Last 1)
}

$results = [ordered]@{}
$results.area01_save_point = Capture $area01.state $area01.card 5811 12 `
    @('5620:ly:0:300', '5620:rx:255:300') @()
CheckSequence $results.area01_save_point 'exact'
$saveGlow = & python (Join-Path $PSScriptRoot 'compare_presentation_frame.py') `
    $results.area01_save_point --roi '340,390,540,650' --max-mae 8
if ($LASTEXITCODE -ne 0) { throw "Save point light comparison failed: $($saveGlow -join ' ')" }
Write-Output $saveGlow
$results.field_camera_start = Capture $field.state $field.card 7000 12 @('6930:rx:255:300') @()
CheckSequence $results.field_camera_start 'exact'
$results.field_camera_end = Capture $field.state $field.card 7218 12 @('6930:rx:255:300') @()
CheckSequence $results.field_camera_end 'exact'
$results.transition_mosaic = Capture $field.state $field.card 6970 120 @() @('6920:square:10')
CheckSequence $results.transition_mosaic 'fallback'
$results.transition_handoff = Capture $field.state $field.card 7090 60 @() @('6920:square:10')
CheckSequence $results.transition_handoff 'mixed'
$results.battle_command = Capture $battle.state $battle.card 7400 12 @() @()
CheckSequence $results.battle_command 'exact'
$results.battle_flash = Capture $battle.state $battle.card 7492 1 @() @('7370:cross:2', '7420:cross:2')
$flash = & python (Join-Path $PSScriptRoot 'compare_presentation_frame.py') $results.battle_flash --max-mae 8
if ($LASTEXITCODE -ne 0) { throw "Battle flash comparison failed: $($flash -join ' ')" }
Write-Output $flash

$report = [ordered]@{
    area01_manifest = (Resolve-Path -LiteralPath $Area01Manifest).Path
    field_manifest = (Resolve-Path -LiteralPath $FieldManifest).Path
    battle_manifest = (Resolve-Path -LiteralPath $BattleManifest).Path
    runner_sha256 = (Get-Content -LiteralPath $results.battle_flash -Raw | ConvertFrom-Json).runner_sha256
    captures = $results
    checks = @('Area 01 save point: exact with light ROI MAE <= 8',
               'field camera start/end of 300 input frames: exact', 'mosaic: GS preserved', 'handoff: GS to exact',
               'battle command: exact', 'impact flash: sampled RGB MAE <= 8')
}
$reportPath = Join-Path (Split-Path -Parent (Resolve-Path -LiteralPath $FieldManifest).Path) `
    ('renderer-regression-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding utf8
Write-Output "Renderer regression passed: $reportPath"
