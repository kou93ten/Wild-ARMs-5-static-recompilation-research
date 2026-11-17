param(
    [Parameter(Mandatory)][string]$Manifest,
    [ValidateSet('idle','forward','back','left','right','forward-left','forward-right','back-left','back-right','camera-left','camera-right','camera-up','camera-down','move-orbit-left','move-orbit-right')]
    [string[]]$Trial = @('idle','forward','back','left','right','forward-left','forward-right','back-left','back-right','camera-left','camera-right','camera-up','camera-down','move-orbit-left','move-orbit-right'),
    [ValidateRange(300,3600)][int]$DurationFrames = 300,
    [ValidateRange(320,8192)][int]$Width = 1280,
    [ValidateRange(240,8192)][int]$Height = 720
)

$ErrorActionPreference = 'Stop'
$manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
$scenario = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if (-not $scenario.source_frame -or -not $scenario.state -or -not $scenario.card) {
    throw 'Scenario manifest needs source_frame, state, and card.'
}
foreach ($key in @('state','card')) {
    $expected = if ($key -eq 'state') { $scenario.state_sha256 } else { $scenario.card_sha256 }
    $actual = (Get-FileHash -LiteralPath $scenario.$key -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "$key hash changed since scenario creation" }
}

$start = [long]$scenario.source_frame
$inputAt = $start + 20
$end = $inputAt + $DurationFrames
$captureAt = $end - 12
$plans = @{
    'idle' = @()
    'forward' = @('ly:0')
    'back' = @('ly:255')
    'left' = @('lx:0')
    'right' = @('lx:255')
    'forward-left' = @('ly:0','lx:0')
    'forward-right' = @('ly:0','lx:255')
    'back-left' = @('ly:255','lx:0')
    'back-right' = @('ly:255','lx:255')
    'camera-left' = @('rx:0')
    'camera-right' = @('rx:255')
    'camera-up' = @('ry:0')
    'camera-down' = @('ry:255')
    'move-orbit-left' = @('ly:0','rx:0')
    'move-orbit-right' = @('ly:0','rx:255')
}
$results = @()
foreach ($name in $Trial) {
    $axes = @($plans[$name] | ForEach-Object { '{0}:{1}:{2}' -f $inputAt, $_, $DurationFrames })
    $args = @{
        State = $scenario.state
        CardSource = $scenario.card
        Frame = $captureAt
        SaveStateAt = $end
        Aspect = '16:9'
        Width = $Width
        Height = $Height
        SceneProbe = $true
        PairReference = $true
    }
    if ($axes.Count) { $args.Axis = $axes }
    $output = & (Join-Path $PSScriptRoot 'capture_native_present.ps1') @args
    $screenshot = ($output | Where-Object { $_ -like 'Screenshot: *' } | Select-Object -Last 1) -replace '^Screenshot: ', ''
    if (-not $screenshot -or -not (Test-Path -LiteralPath $screenshot)) { throw "Trial $name did not capture a screenshot" }
    $captureManifest = Join-Path (Split-Path -Parent $screenshot) 'capture.json'
    $capture = Get-Content -LiteralPath $captureManifest -Raw | ConvertFrom-Json
    $results += [ordered]@{
        trial = $name
        input = $axes
        input_frame = $inputAt
        duration_frames = $DurationFrames
        capture = $captureManifest
        screenshot = $screenshot
        reference = $capture.reference_screenshot
        derived_state = $capture.derived_state
    }
    Write-Output "${name}: $screenshot"
}
$report = [ordered]@{
    scenario_manifest = $manifestPath
    source_state_sha256 = $scenario.state_sha256
    source_card_sha256 = $scenario.card_sha256
    resolution = "${Width}x${Height}"
    results = $results
}
$reportPath = Join-Path (Split-Path -Parent $manifestPath) ('movement-suite-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding utf8
Write-Output "Report: $reportPath"
