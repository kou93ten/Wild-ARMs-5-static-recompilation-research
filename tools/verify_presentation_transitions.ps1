param(
    [ValidateSet('720p', '1080p', '4k', 'both')][string]$Resolution = 'both',
    [ValidateRange(2, 120)][int]$Presentations = 8,
    [string[]]$CaseNames = @()
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$capture = Join-Path $PSScriptRoot 'capture_native_present.ps1'
$checker = Join-Path $PSScriptRoot 'check_presentation_capture.py'
$reports = Join-Path $project 'work/native-play'
$tests = @(
    @{ Name = 'new-cinematic-bars'; Report = '20261111-194641-e84103/reports/2026-11-11_19-59-08_f074099'; Frame = 74102; Expect = 'fallback' },
    @{ Name = 'new-dialogue-tony'; Report = '20261111-194641-e84103/reports/2026-11-11_20-06-53_f097648'; Frame = 97651; Expect = 'panel' },
    @{ Name = 'new-dialogue-party'; Report = '20261111-194641-e84103/reports/2026-11-11_20-09-59_f108765'; Frame = 108769; Expect = 'panel' },
    @{ Name = 'new-battle-command'; Report = '20261111-194641-e84103/reports/2026-11-11_20-15-41_f128334'; Frame = 128337; Expect = 'exact' },
    @{ Name = 'underwater-cinematic'; Report = '20261111-105244-074b62/reports/2026-11-11_11-02-07_f142381'; Frame = 142390; Expect = 'fallback' },
    @{ Name = 'arm-dialogue-bars'; Report = '20261111-125832-38d741/reports/2026-11-11_13-09-12_f035611'; Frame = 35951; Expect = 'fallback'; Press = @('35630:cross:4', '35720:cross:4', '35890:cross:4') },
    @{ Name = 'area-fade'; Report = '20261111-114120-afbe4c/reports/2026-11-11_11-43-07_f006233'; Frame = 6660; Expect = 'fallback'; NativeReplay = 'work/scene-probes/f9-6233-to-8494.inputs' },
    @{ Name = 'intro-logo'; Report = '20261111-122139-6f5754/reports/2026-11-11_12-21-53_f000685'; Frame = 690; Expect = 'fallback' },
    @{ Name = 'save-orb'; Report = '20261111-124717-54d003/reports/2026-11-11_12-48-52_f005522'; Frame = 5530; Expect = 'fallback' },
    @{ Name = 'ordinary-canyon'; Report = '20261111-122139-6f5754/reports/2026-11-11_12-22-00_f001101'; Frame = 1350; Expect = 'exact' },
    @{ Name = 'ordinary-battle'; Report = '20261110-160748-0d78a6/reports/2026-11-10_16-08-10_f046298'; Frame = 46310; Expect = 'exact' }
)
if ($CaseNames.Count) {
    $selected = @($CaseNames | ForEach-Object { $_ -split ',' })
    $tests = @($tests | Where-Object { $selected -contains $_.Name })
    if (-not $tests.Count) { throw "No matching cases: $($CaseNames -join ', ')" }
}
$sizes = if ($Resolution -eq 'both') { @('720p', '4k') } else { @($Resolution) }
$results = @()
foreach ($size in $sizes) {
    $width, $height = switch ($size) {
        '4k' { 3840, 2160 }
        '1080p' { 1920, 1080 }
        default { 1280, 720 }
    }
    foreach ($test in $tests) {
        $report = Join-Path $reports $test.Report
        $options = @{
            State = Join-Path $report 'report.state'
            CardSource = Join-Path $report 'report.state.ps2'
            Frame = $test.Frame
            Aspect = '16:9'
            Mode = if ($size -eq '4k') { 'borderless' } else { 'window' }
            Width = $width
            Height = $height
            ConsecutiveFrames = if ($size -eq '4k') { [Math]::Min(4, $Presentations) } else { $Presentations }
            SceneProbe = $true
            PairReference = $true
        }
        if ($test.Press) { $options.Press = $test.Press }
        if ($test.NativeReplay) { $options.NativeReplay = Join-Path $project $test.NativeReplay }
        Write-Output "Capturing $($test.Name) at $size..."
        $output = & $capture @options
        $captureLine = @($output | Where-Object { $_ -match '^Presentations captured: \d+ in (.+)$' }) | Select-Object -Last 1
        if (-not $captureLine) { throw "Capture did not report a sequence for $($test.Name): $($output -join ' ')" }
        $directory = [regex]::Match($captureLine, '^Presentations captured: \d+ in (.+)$').Groups[1].Value
        $manifest = Join-Path $directory 'capture.json'
        & python $checker $manifest --expect $test.Expect
        if ($LASTEXITCODE -ne 0) { throw "Presentation check failed for $($test.Name) at ${size}: $manifest" }
        $results += [ordered]@{ name = $test.Name; resolution = $size; expectation = $test.Expect; manifest = $manifest }
    }
}
$resultName = if ($Resolution -eq 'both' -and -not $CaseNames.Count) {
    'transition-regression.json'
} else {
    'transition-regression-{0}-{1}.json' -f $Resolution, (Get-Date -Format 'yyyyMMdd-HHmmss')
}
$resultFile = Join-Path $project "work/native-present/$resultName"
$results | ConvertTo-Json | Set-Content -LiteralPath $resultFile -Encoding utf8
Write-Output "Passed $($results.Count) presentation sequences. Results: $resultFile"
