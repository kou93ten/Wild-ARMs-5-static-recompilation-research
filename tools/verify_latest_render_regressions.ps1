param(
    [ValidateSet('720p', '1080p', '4k', 'all')][string]$Resolution = 'all'
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$capture = Join-Path $PSScriptRoot 'capture_native_present.ps1'
$checker = Join-Path $PSScriptRoot 'check_presentation_capture.py'
$cases = @(
    @{
        Name = 'battle-command-ui'
        Report = 'work/native-play/20261111-194641-e84103/reports/2026-11-11_20-15-41_f128334'
        Frame = 128337
        Regions = @('275,310,645,610,10', '640,475,820,610,25', '780,890,1680,1050,10')
    },
    @{
        Name = 'field-lamps'
        Report = 'work/native-play/20261111-234823-31b039/reports/2026-11-11_23-50-03_f132956'
        Frame = 132959
        Regions = @('330,350,460,520,20', '1200,400,1420,610,20', '1500,80,1900,540,20')
    },
    @{
        Name = 'battle-red-hex-effects'
        Report = 'work/native-play/20261112-014945-1da38c/reports/2026-11-12_01-55-36_f166925'
        Frame = 166930
        Regions = @('740,420,1190,680,12', '875,425,1025,545,8')
    },
    @{
        Name = 'battle-red-hex-effects-late'
        Report = 'work/native-play/20261112-014945-1da38c/reports/2026-11-12_01-55-36_f166925'
        Frame = 166963
        Regions = @('740,420,1190,680,12', '875,425,1025,545,8')
    },
    @{
        Name = 'battle-enemy-glow'
        Report = 'work/native-play/20261112-121955-59319c/reports/2026-11-12_12-21-09_f171072'
        Frame = 171076
        Regions = @('1080,460,1280,620,16')
    },
    @{
        Name = 'battle-enemy-glow-late'
        Report = 'work/native-play/20261112-121955-59319c/reports/2026-11-12_12-21-09_f171072'
        Frame = 171128
        Regions = @('1080,460,1280,620,16')
    },
    @{
        Name = 'field-camera-stability'
        Report = 'work/native-play/20261112-125802-f5352c/reports/2026-11-12_13-00-38_f180284'
        Frame = 180287
        Regions = @()
        ContinuousReady = $true
    },
    @{
        Name = 'field-camera-stability-late'
        Report = 'work/native-play/20261112-125802-f5352c/reports/2026-11-12_13-00-38_f180284'
        Frame = 180525
        Regions = @()
        ContinuousReady = $true
    },
    @{
        Name = 'field-camera-rotation'
        Report = 'work/native-play/20261112-132821-d44d33/reports/2026-11-12_13-29-53_f185624'
        Frame = 185650
        Axis = @('185625:rx:192:600')
        Regions = @()
        ContinuousReady = $true
        SynchronizedStaticGS = $true
        ConsecutiveFrames4k = 8
    },
    @{
        Name = 'field-camera-rotation-late'
        Report = 'work/native-play/20261112-132821-d44d33/reports/2026-11-12_13-29-53_f185624'
        Frame = 185907
        Axis = @('185625:rx:192:600')
        Regions = @()
        ContinuousReady = $true
        SynchronizedStaticGS = $true
        ConsecutiveFrames4k = 8
    },
    @{
        Name = 'battle-offscreen-light'
        Report = 'work/native-play/20261112-141035-43aa52/reports/2026-11-12_14-13-12_f194852'
        Frame = 194885
        Regions = @('1080,420,1500,645,8', '1425,435,1710,645,8', '1050,585,1755,765,10', '0,585,1050,855,9.5', '0,255,1920,870,7')
        ContinuousReady = $true
        SynchronizedStaticGS = $true
        ConsecutiveFrames4k = 8
    },
    @{
        Name = 'battle-offscreen-light-late'
        Report = 'work/native-play/20261112-141035-43aa52/reports/2026-11-12_14-13-12_f194852'
        Frame = 195065
        Regions = @('1080,420,1500,645,8', '1425,435,1710,645,8', '1050,585,1755,765,10', '0,585,1050,855,9.5', '0,255,1920,870,7')
        ContinuousReady = $true
        SynchronizedStaticGS = $true
        ConsecutiveFrames4k = 8
    }
)
$sizes = if ($Resolution -eq 'all') { @('720p', '1080p', '4k') } else { @($Resolution) }
$results = @()
foreach ($size in $sizes) {
    $width, $height = switch ($size) {
        '4k' { 3840, 2160 }
        '1080p' { 1920, 1080 }
        default { 1280, 720 }
    }
    foreach ($test in $cases) {
        $report = Join-Path $project $test.Report
        $options = @{
            State = Join-Path $report 'report.state'
            CardSource = Join-Path $report 'report.state.ps2'
            Frame = $test.Frame
            Aspect = '16:9'
            Mode = if ($size -eq '4k') { 'borderless' } else { 'window' }
            Width = $width
            Height = $height
            ConsecutiveFrames = if ($size -eq '4k') { if ($test.ConsecutiveFrames4k) { $test.ConsecutiveFrames4k } else { 4 } } else { 8 }
            SceneProbe = $true
            PairReference = $true
        }
        if ($test.Axis) { $options.Axis = $test.Axis }
        Write-Output "Capturing $($test.Name) at $size..."
        $output = & $capture @options
        $captureLine = @($output | Where-Object { $_ -match '^Presentations captured: \d+ in (.+)$' }) | Select-Object -Last 1
        if (-not $captureLine) { throw "Capture did not report a sequence for $($test.Name): $($output -join ' ')" }
        $directory = [regex]::Match($captureLine, '^Presentations captured: \d+ in (.+)$').Groups[1].Value
        $manifest = Join-Path $directory 'capture.json'
        $arguments = @($checker, $manifest, '--expect', 'exact')
        foreach ($region in $test.Regions) { $arguments += @('--roi', $region) }
        if ($test.ContinuousReady) { $arguments += '--continuous-ready' }
        if ($test.SynchronizedStaticGS) { $arguments += '--synchronized-static-gs' }
        & python @arguments
        if ($LASTEXITCODE -ne 0) { throw "Visual regression failed for $($test.Name) at ${size}: $manifest" }
        $results += [ordered]@{ name = $test.Name; resolution = $size; manifest = $manifest; regions = $test.Regions }
    }
}
$resultName = if ($Resolution -eq 'all') { 'latest-render-regression.json' } else { "latest-render-regression-$Resolution.json" }
$resultFile = Join-Path $project "work/native-present/$resultName"
$results | ConvertTo-Json | Set-Content -LiteralPath $resultFile -Encoding utf8
Write-Output "Passed $($results.Count) visual regression sequences. Results: $resultFile"
