param(
    [Parameter(Mandatory)][string]$State,
    [Parameter(Mandatory)][string]$CardSource,
    [Parameter(Mandatory)][long]$Frame,
    [ValidateSet('4:3', '16:10', '16:9', '21:9')][string]$Aspect = '16:9',
    [ValidateSet('window', 'borderless', 'fullscreen')][string]$Mode = 'window',
    [Parameter(Mandatory)][int]$Width,
    [Parameter(Mandatory)][int]$Height,
    [ValidateRange(1, 6)][int]$Scale = 1,
    [ValidateRange(0, 10)][int]$TailFrames = 0,
    [ValidateRange(1, 120)][int]$ConsecutiveFrames = 1,
    [string[]]$Press = @(),
    [string[]]$Axis = @(),
    [long]$SaveStateAt = 0,
    [string]$NativeReplay = '',
    [switch]$SceneProbe,
    [ValidateSet(0, 1, 2, 3)][int]$SceneGridScale = 0,
    [switch]$PairReference,
    [switch]$RenderDoc
)

$ErrorActionPreference = 'Stop'
if ($Width -lt 320 -or $Height -lt 240 -or $Width -gt 8192 -or $Height -gt 8192) {
    throw 'Capture size must be between 320x240 and 8192x8192.'
}
if ($Frame -lt 0) { throw 'Frame must be nonnegative.' }
if ($SaveStateAt -lt 0) { throw 'SaveStateAt must be nonnegative.' }
if ($SceneGridScale -and -not $SceneProbe) { throw '-SceneGridScale requires -SceneProbe.' }
# Scene probes use the runtime's drawable-size scale selection unless the
# caller asks for a fixed comparison scale. Ordinary GS captures stay at 1x.
$useAutoScale = $SceneProbe -and -not $PSBoundParameters.ContainsKey('Scale')

$project = Split-Path -Parent $PSScriptRoot
$checkout = Join-Path $project 'external\ps2-native-recomp'
$runner = Join-Path $checkout 'build\native-wildarms5\ps2run.exe'
$elf = Join-Path $checkout 'games\wildarms5\disc\SLUS_216.15'
$game = Join-Path $checkout 'games\wildarms5'
$iso = 'D:\Emulation\PS2\Wild Arms V\WildArmsV.iso'
$statePath = (Resolve-Path -LiteralPath $State).Path
$cardSourcePath = (Resolve-Path -LiteralPath $CardSource).Path
$replayPath = if ($NativeReplay) { (Resolve-Path -LiteralPath $NativeReplay).Path } else { '' }
foreach ($file in @($runner, $elf, $iso)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing required file: $file" }
}
$renderdocCommand = 'C:\Program Files\RenderDoc\renderdoccmd.exe'
if ($RenderDoc -and -not (Test-Path -LiteralPath $renderdocCommand -PathType Leaf)) {
    throw "RenderDoc command not found: $renderdocCommand"
}

$runName = '{0}-{1}-{2}x{3}-{4}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $Aspect.Replace(':', 'x'), $Width, $Height, ([guid]::NewGuid().ToString('N').Substring(0, 6))
$runDir = Join-Path $project "work\native-present\$runName"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$card = Join-Path $runDir 'card.ps2'
$picture = Join-Path $runDir 'present.png'
$referencePicture = Join-Path $runDir 'reference.png'
$derivedState = if ($SaveStateAt) { Join-Path $runDir 'derived.state' } else { '' }
Copy-Item -LiteralPath $cardSourcePath -Destination $card

$previous = @{}
foreach ($key in @('PS2_PRESENT_CAPTURE', 'PS2_PRESENT_CAPTURE_AT', 'PS2_PRESENT_CAPTURE_COUNT', 'PS2_PRESENT_CAPTURE_REFERENCE', 'PS2_SCENE_GEOMETRY', 'PS2_SCENE_GRID_SCALE', 'PS2_SCALE', 'PS2_SCALE_AUTO',
                   'PS2_DEBUG_REQUEUE', 'PS2_GS_IMR_PENDING_IRQ', 'PS2_GS_REJECT_INVALID_STQ', 'PS2_VU_INTERPRET_ENTRIES', 'PS2_RENDERDOC_CAPTURE_AT')) {
    $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
}
try {
    $env:PS2_PRESENT_CAPTURE = $picture
    $env:PS2_PRESENT_CAPTURE_AT = [string]$Frame
    $env:PS2_PRESENT_CAPTURE_COUNT = [string]$ConsecutiveFrames
    $env:PS2_PRESENT_CAPTURE_REFERENCE = if ($PairReference) { $referencePicture } else { '' }
    $env:PS2_SCENE_GEOMETRY = if ($SceneProbe) { '1' } else { '0' }
    $env:PS2_SCENE_GRID_SCALE = [string]$SceneGridScale
    $env:PS2_RENDERDOC_CAPTURE_AT = if ($RenderDoc) { [string]$Frame } else { '' }
    $env:PS2_SCALE = if ($useAutoScale) { '' } else { [string]$Scale }
    $env:PS2_SCALE_AUTO = if ($useAutoScale) { '1' } else { '0' }
    $env:PS2_DEBUG_REQUEUE = '1'
    $env:PS2_GS_IMR_PENDING_IRQ = '0'
    $env:PS2_GS_REJECT_INVALID_STQ = '1'
    $env:PS2_VU_INTERPRET_ENTRIES = '1B8'
    $lastFrame = [Math]::Max($Frame + 1 + $TailFrames + $(if ($ConsecutiveFrames -gt 1) { 3 * $ConsecutiveFrames } else { 0 }), $SaveStateAt + 1)
    $args = '"{0}" --disc "{1}" --{2} --window-size {3}x{4} --aspect {5} --memcard "{6}" --quick-dir "{7}" --game-dir "{8}" --load-state "{9}" --frames {10}' -f $elf, $iso, $Mode, $Width, $Height, $Aspect, $card, $runDir, $game, $statePath, $lastFrame
    if ($SaveStateAt) { $args += ' --save-state "' + $SaveStateAt + ':' + $derivedState + '"' }
    foreach ($pressSpec in $Press) {
        if ($pressSpec -notmatch '^\d+:[a-z0-9]+(?:\+[a-z0-9]+)*:\d+$') { throw "Invalid press specification: $pressSpec" }
        $args += ' --press "' + $pressSpec + '"'
    }
    foreach ($axisSpec in $Axis) {
        if ($axisSpec -notmatch '^\d+:(rx|ry|lx|ly):\d{1,3}:\d+$') { throw "Invalid axis specification: $axisSpec" }
        $args += ' --axis "' + $axisSpec + '"'
    }
    if ($replayPath) { $args += ' --replay "' + $replayPath + '"' }
    if ($RenderDoc) {
        $captureTemplate = Join-Path $runDir 'renderdoc'
        $rdArgs = 'capture -w -d "{0}" -c "{1}" "{2}" {3}' -f $checkout, $captureTemplate, $runner, $args
        $process = Start-Process -FilePath $renderdocCommand -ArgumentList $rdArgs -WindowStyle Hidden -PassThru -Wait
    } else {
        $process = Start-Process -FilePath $runner -ArgumentList $args -WindowStyle Hidden -PassThru -Wait
    }
} finally {
    foreach ($key in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
    }
}

# Replay status 4 means the requested frame limit ended before the recording.
# That is expected for a single-frame probe of a longer player's recording.
if ($process.ExitCode -ne 0 -and -not ($replayPath -and $process.ExitCode -eq 4)) {
    throw "Native runner exited $($process.ExitCode). Logs: $(Join-Path $runDir 'logs')"
}
if ($SaveStateAt -and -not (Test-Path -LiteralPath $derivedState -PathType Leaf)) {
    throw "Derived state was not saved: $derivedState"
}
if ($ConsecutiveFrames -gt 1) {
    $sequencePictures = @(Get-ChildItem -LiteralPath $runDir -Filter 'present-f*-p*.png' |
        Sort-Object { [int]([regex]::Match($_.Name, '-p(\d+)\.png').Groups[1].Value) })
    if ($sequencePictures.Count -ne $ConsecutiveFrames) {
        throw "Captured $($sequencePictures.Count) of $ConsecutiveFrames requested presentations. Logs: $(Join-Path $runDir 'logs')"
    }
    $picture = $sequencePictures[0].FullName
    $referencePicture = Join-Path $runDir ($sequencePictures[0].Name -replace '^present-', 'reference-')
}
if (-not (Test-Path -LiteralPath $picture -PathType Leaf)) { throw "The native presentation screenshot was not captured. Logs: $(Join-Path $runDir 'logs')" }
$png = [System.IO.File]::ReadAllBytes($picture)
if ($png.Length -lt 24 -or $png[0] -ne 137 -or $png[1] -ne 80 -or $png[2] -ne 78 -or $png[3] -ne 71) {
    throw "Invalid PNG capture: $picture"
}
function Read-BigEndian32([byte[]]$bytes, [int]$offset) {
    return ([int64]$bytes[$offset] -shl 24) -bor ([int64]$bytes[$offset + 1] -shl 16) -bor ([int64]$bytes[$offset + 2] -shl 8) -bor [int64]$bytes[$offset + 3]
}
$actualWidth = Read-BigEndian32 $png 16
$actualHeight = Read-BigEndian32 $png 20
if ($actualWidth -ne $Width -or $actualHeight -ne $Height) {
    throw "Capture is ${actualWidth}x${actualHeight}, requested ${Width}x${Height}: $picture"
}
if ($PairReference) {
    if (-not (Test-Path -LiteralPath $referencePicture -PathType Leaf)) { throw "Paired GS reference was not captured: $referencePicture" }
    $referencePng = [System.IO.File]::ReadAllBytes($referencePicture)
    if ($referencePng.Length -lt 24 -or (Read-BigEndian32 $referencePng 16) -ne $Width -or (Read-BigEndian32 $referencePng 20) -ne $Height) {
        throw "Paired GS reference dimensions do not match ${Width}x${Height}: $referencePicture"
    }
}

$log = Get-ChildItem -LiteralPath (Join-Path $runDir 'logs') -Filter '*.log' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$effectiveGsScale = $Scale
if ($useAutoScale) {
    $autoScaleLine = Select-String -LiteralPath $log.FullName -Pattern '\[video\] auto GS backing (\d+)x for actual (\d+)x(\d+) output' | Select-Object -Last 1
    if (-not $autoScaleLine) { throw "Automatic GS backing scale was not reported: $($log.FullName)" }
    $effectiveGsScale = [int]$autoScaleLine.Matches[0].Groups[1].Value
}
$rdc = if ($RenderDoc) { Get-ChildItem -LiteralPath $runDir -Filter '*.rdc' | Select-Object -First 1 } else { $null }
if ($RenderDoc -and -not $rdc) { throw "RenderDoc capture not found in $runDir" }
$presentLine = Select-String -LiteralPath $log.FullName -Pattern '\[present-capture\] saved \d+x\d+ frame (\d+)' | Select-Object -Last 1
if (-not $presentLine) { throw "Screenshot exists but GPU capture confirmation is missing: $($log.FullName)" }
$capturedFrame = [long]$presentLine.Matches[0].Groups[1].Value
$captureItems = @()
if ($ConsecutiveFrames -gt 1) {
    foreach ($item in $sequencePictures) {
        $match = [regex]::Match($item.Name, '^present-f(\d+)-p(\d+)\.png$')
        if (-not $match.Success) { throw "Unrecognized capture filename: $($item.Name)" }
        $referenceItem = Join-Path $runDir ($item.Name -replace '^present-', 'reference-')
        if ($PairReference -and -not (Test-Path -LiteralPath $referenceItem -PathType Leaf)) {
            throw "Missing paired GS reference: $referenceItem"
        }
        $captureItems += [ordered]@{
            index = [int]$match.Groups[2].Value
            frame = [long]$match.Groups[1].Value
            present = $item.FullName
            reference = if ($PairReference) { $referenceItem } else { $null }
            matches_gs = if ($PairReference) { (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $referenceItem -Algorithm SHA256).Hash } else { $null }
        }
    }
}
$manifest = [ordered]@{
    state = $statePath
    card_source = $cardSourcePath
    aspect = $Aspect
    mode = $Mode
    requested_resolution = "${Width}x${Height}"
    captured_resolution = "${actualWidth}x${actualHeight}"
    frame_requested = $Frame
    frame_captured = $capturedFrame
    capture_count = $ConsecutiveFrames
    captures = $captureItems
    tail_frames = $TailFrames
    axes = $Axis
    presses = $Press
    derived_state = if ($SaveStateAt) { $derivedState } else { $null }
    derived_state_frame = if ($SaveStateAt) { $SaveStateAt } else { $null }
    native_replay = if ($replayPath) { $replayPath } else { $null }
    scene_probe = [bool]$SceneProbe
    renderdoc_capture = if ($rdc) { $rdc.FullName } else { $null }
    scene_grid_scale = $SceneGridScale
    scene_internal_resolution = if ($SceneGridScale) { "$(512 * $SceneGridScale)x$(448 * $SceneGridScale)" } elseif ($SceneProbe) { "${Width}x${Height}" } else { $null }
    gs_scale = $effectiveGsScale
    gs_scale_auto = [bool]$useAutoScale
    diagnostics = [ordered]@{
        scene_raw = [Environment]::GetEnvironmentVariable('PS2_SCENE_RAW', 'Process')
        scene_before_effect = [Environment]::GetEnvironmentVariable('PS2_SCENE_BEFORE_EFFECT', 'Process')
        scene_no_effect_delta = [Environment]::GetEnvironmentVariable('PS2_SCENE_NO_EFFECT_DELTA', 'Process')
        scene_no_hud_delta = [Environment]::GetEnvironmentVariable('PS2_SCENE_NO_HUD_DELTA', 'Process')
        scene_signed_effect = [Environment]::GetEnvironmentVariable('PS2_SCENE_SIGNED_EFFECT', 'Process')
        scene_skip_tbp = [Environment]::GetEnvironmentVariable('PS2_SCENE_SKIP_TBP', 'Process')
        scene_show_holes = [Environment]::GetEnvironmentVariable('PS2_SCENE_SHOW_HOLES', 'Process')
        scene_material_debug = [Environment]::GetEnvironmentVariable('PS2_SCENE_MATERIAL_DEBUG', 'Process')
        scene_show_alpha = [Environment]::GetEnvironmentVariable('PS2_SCENE_SHOW_ALPHA', 'Process')
        scene_show_before_hud = [Environment]::GetEnvironmentVariable('PS2_SCENE_SHOW_BEFORE_HUD', 'Process')
        scene_show_final_e00 = [Environment]::GetEnvironmentVariable('PS2_SCENE_SHOW_FINAL_E00', 'Process')
        scene_pixel_origin = if ([Environment]::GetEnvironmentVariable('PS2_SCENE_PIXEL_ORIGIN', 'Process') -eq '0') { 'disabled' } else { 'enabled' }
        scene_trace_frame = [Environment]::GetEnvironmentVariable('PS2_SCENE_TRACE_FRAME', 'Process')
        scene_msaa = [Environment]::GetEnvironmentVariable('PS2_SCENE_MSAA', 'Process')
        scene_use_decode = [Environment]::GetEnvironmentVariable('PS2_SCENE_USE_DECODE', 'Process')
        scene_ps2_lod = [Environment]::GetEnvironmentVariable('PS2_SCENE_PS2_LOD', 'Process')
        gs_decode = [Environment]::GetEnvironmentVariable('PS2_DECODE', 'Process')
        trace_draws = [Environment]::GetEnvironmentVariable('PS2_TRACE_DRAWS', 'Process')
        draw_range = [Environment]::GetEnvironmentVariable('PS2_DRAW_RANGE', 'Process')
        trace_verts = [Environment]::GetEnvironmentVariable('PS2_TRACE_VERTS', 'Process')
    }
    runner_sha256 = (Get-FileHash -LiteralPath $runner -Algorithm SHA256).Hash.ToLowerInvariant()
    runner_exit_code = $process.ExitCode
    screenshot = $picture
    reference_screenshot = if ($PairReference) { $referencePicture } else { $null }
    log = $log.FullName
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runDir 'capture.json') -Encoding utf8
Write-Output "Screenshot: $picture"
Write-Output "Log: $($log.FullName)"
Write-Output "Captured: ${actualWidth}x${actualHeight} ($Aspect)"
Write-Output "Presented frame: $capturedFrame"
if ($ConsecutiveFrames -gt 1) { Write-Output "Presentations captured: $($captureItems.Count) in $runDir" }
if ($SaveStateAt) { Write-Output "Derived state: $derivedState" }
