param(
    [string]$FieldManifest = 'work/visual-scenarios/black-box-area02/manifest.json',
    [string]$BattleManifest = 'work/visual-scenarios/black-box-area02-duel-battle/manifest.json'
)

$ErrorActionPreference = 'Stop'
$field = Get-Content -LiteralPath (Resolve-Path -LiteralPath $FieldManifest) -Raw | ConvertFrom-Json
$battle = Get-Content -LiteralPath (Resolve-Path -LiteralPath $BattleManifest) -Raw | ConvertFrom-Json
if ($field.area -ne 'Black Box - Area 02' -or [long]$field.source_frame -ne 6910 -or [long]$battle.source_frame -ne 7360) {
    throw 'This replay requires the verified Black Box Area 02 field and battle checkpoints.'
}
foreach ($item in @($field, $battle)) {
    foreach ($key in @('state', 'card')) {
        $expected = if ($key -eq 'state') { $item.state_sha256 } else { $item.card_sha256 }
        $actual = (Get-FileHash -LiteralPath $item.$key -Algorithm SHA256).Hash
        if ($actual -ne $expected) { throw "Scenario $($item.name) $key hash changed." }
    }
}

function Capture([string]$state, [long]$frame, [long]$saveAt, [string[]]$press) {
    $lines = & (Join-Path $PSScriptRoot 'capture_native_present.ps1') `
        -State $state -CardSource $field.card -Frame $frame -SaveStateAt $saveAt `
        -Width 1280 -Height 720 -PairReference -Press $press
    $picture = ($lines | Where-Object { $_ -like 'Screenshot: *' } | Select-Object -Last 1) -replace '^Screenshot: ', ''
    if (-not $picture -or -not (Test-Path -LiteralPath $picture -PathType Leaf)) {
        throw "Capture at frame $frame failed: $($lines -join ' ')"
    }
    return [pscustomobject]@{
        picture = $picture
        capture_manifest = (Join-Path (Split-Path -Parent $picture) 'capture.json')
        state = (Join-Path (Split-Path -Parent $picture) 'derived.state')
    }
}

$start = Capture $field.state 7350 7360 @('6920:square:10')
$response = Capture $start.state 7650 7660 @('7370:cross:2', '7420:cross:2')
$firstHash = (Get-FileHash -LiteralPath $start.picture -Algorithm SHA256).Hash
$baselineHash = (Get-FileHash -LiteralPath $battle.preview -Algorithm SHA256).Hash
$responseHash = (Get-FileHash -LiteralPath $response.picture -Algorithm SHA256).Hash
$responseBaselineHash = (Get-FileHash -LiteralPath $battle.input_preview -Algorithm SHA256).Hash
$report = [ordered]@{
    field_manifest = (Resolve-Path -LiteralPath $FieldManifest).Path
    battle_manifest = (Resolve-Path -LiteralPath $BattleManifest).Path
    battle_capture = $start.capture_manifest
    command_response_capture = $response.capture_manifest
    duel_sign_input = @('6920:square:10')
    command_input = @('7370:cross:2', '7420:cross:2')
    battle_frame_matches_baseline = ($firstHash -eq $baselineHash)
    command_frame_matches_baseline = ($responseHash -eq $responseBaselineHash)
}
$reportPath = Join-Path (Split-Path -Parent (Resolve-Path -LiteralPath $BattleManifest).Path) `
    ('battle-replay-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
$report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding utf8
if (-not $report.battle_frame_matches_baseline -or -not $report.command_frame_matches_baseline) {
    throw "Battle replay differs from the baseline. Inspect $reportPath"
}
Write-Output "Replayed Duel Sign battle and command input: $reportPath"
