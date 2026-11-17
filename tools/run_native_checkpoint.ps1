param(
    [ValidateSet('saved-field', 'new-game')]
    [string]$Scenario = 'saved-field',
    [int]$Frames = 6200,
    [string[]]$Press = @(),
    [string[]]$Axis = @(),
    [string]$Pcsx2Movie = '',
    [long]$Pcsx2MovieAnchorNative = 0,
    [long]$Pcsx2MovieAnchorSource = 0,
    [int]$SaveStateAt = 0,
    [string]$LoadState = '',
    [string]$NativeReplay = '',
    [string]$CardSource = '',
    [string]$RunnerPath = '',
    [ValidateSet('4:3', '16:10', '16:9', '21:9')]
    [string]$Aspect = '4:3',
    [int[]]$FrameAt = @(),
    [switch]$TraceSyscalls,
    [switch]$TraceThreads,
    [switch]$TraceIO,
    [switch]$TraceIopHle,
    [switch]$BridgePcsx2Sif,
    [string]$BridgePcsx2IopMemory = ''
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$checkout = Join-Path $project 'external\ps2-native-recomp'
$runner = if ($RunnerPath) { (Resolve-Path -LiteralPath $RunnerPath).Path } else { Join-Path $checkout 'build\native-wildarms5\ps2run.exe' }
$elf = Join-Path $checkout 'games\wildarms5\disc\SLUS_216.15'
$gameDir = Join-Path $checkout 'games\wildarms5'
$iso = 'D:\Emulation\PS2\Wild Arms V\WildArmsV.iso'
$referenceCard = Join-Path $project 'work\checkpoints\run-01-final\memcard-slot-1.ps2'
$expectedCardHash = '48dd0383eab4e6c2330655a365866eabc731d3b354c856140d6d25bc0f45f975'

foreach ($file in @($runner, $elf, $iso)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing required file: $file" }
}

$runName = '{0}-{1}-{2}' -f $Scenario, (Get-Date -Format 'yyyyMMdd-HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 6))
$runDir = Join-Path $project "work\native-runs\$runName"
$shots = Join-Path $runDir 'shots'
New-Item -ItemType Directory -Force -Path $shots | Out-Null
$card = Join-Path $runDir 'card.ps2'

if ($Scenario -eq 'saved-field') {
    $sourceCard = if ($CardSource) { (Resolve-Path -LiteralPath $CardSource).Path } else { $referenceCard }
    if (-not (Test-Path -LiteralPath $sourceCard -PathType Leaf)) { throw "Missing memory card: $sourceCard" }
    $sourceHash = (Get-FileHash -LiteralPath $sourceCard -Algorithm SHA256).Hash.ToLowerInvariant()
    if (-not $CardSource -and $sourceHash -ne $expectedCardHash) {
        throw 'The reference memory card differs from the captured baseline.'
    }
    Copy-Item -LiteralPath $sourceCard -Destination $card
    $presses = @('3000:start:30', '3300:cross:12', '4000:up:12', '4150:cross:12')
} else {
    $presses = @('3000:start:30', '3300:right:12', '3450:cross:12', '3700:up:12', '3850:cross:12', '4700:cross:12')
}
$presses += $Press

$arguments = @($elf, '--disc', $iso, '--game-dir', $gameDir, '--aspect', $Aspect, '--memcard', $card, '--frames', "$Frames",
               '--frames-dir', $shots, '--frame-every', '300',
               '--misses', (Join-Path $runDir 'misses.txt'))
foreach ($inputSpec in $presses) { $arguments += @('--press', $inputSpec) }
foreach ($axisSpec in $Axis) { $arguments += @('--axis', $axisSpec) }
if ($Pcsx2Movie) {
    if (-not (Test-Path -LiteralPath $Pcsx2Movie -PathType Leaf)) { throw "Missing input movie: $Pcsx2Movie" }
    $arguments += @('--pcsx2-movie', (Resolve-Path -LiteralPath $Pcsx2Movie).Path,
                    '--pcsx2-movie-anchor', ("{0}:{1}" -f $Pcsx2MovieAnchorNative, $Pcsx2MovieAnchorSource))
}
foreach ($frame in $FrameAt) { $arguments += @('--frame-at', "$frame") }
if ($TraceSyscalls) { $arguments += '--trace-syscalls' }
if ($TraceThreads) { $arguments += '--trace-threads' }
if ($TraceIO) { $arguments += '--trace-io' }
if ($TraceIopHle) { $arguments += '--trace-iop-hle' }
if ($BridgePcsx2Sif) {
    $iopSource = if ($BridgePcsx2IopMemory) { (Resolve-Path -LiteralPath $BridgePcsx2IopMemory).Path } else {
        Join-Path $project 'work\pcsx2-import\slot04-validated\extracted\iopMemory.bin'
    }
    $arguments += @('--bridge-pcsx2-sif', '--bridge-pcsx2-iop-memory',
                    $iopSource)
}
if ($LoadState) {
    if (-not (Test-Path -LiteralPath $LoadState -PathType Leaf)) { throw "Missing native state: $LoadState" }
    $arguments += @('--load-state', (Resolve-Path -LiteralPath $LoadState).Path)
}
if ($NativeReplay) {
    if (-not (Test-Path -LiteralPath $NativeReplay -PathType Leaf)) { throw "Missing native input recording: $NativeReplay" }
    $arguments += @('--replay', (Resolve-Path -LiteralPath $NativeReplay).Path)
}
if ($SaveStateAt -gt 0) {
    $arguments += @('--save-state', ("{0}:{1}" -f $SaveStateAt, (Join-Path $runDir 'checkpoint.p2state')))
}

@{
    scenario = $Scenario
    frames = $Frames
    aspect = $Aspect
    presses = $presses
    axes = $Axis
    pcsx2_movie = if ($Pcsx2Movie) { (Resolve-Path -LiteralPath $Pcsx2Movie).Path } else { $null }
    pcsx2_movie_anchor_native = $Pcsx2MovieAnchorNative
    pcsx2_movie_anchor_source = $Pcsx2MovieAnchorSource
    frame_at = $FrameAt
    trace_syscalls = [bool]$TraceSyscalls
    trace_threads = [bool]$TraceThreads
    trace_io = [bool]$TraceIO
    trace_iop_hle = [bool]$TraceIopHle
    bridge_pcsx2_sif = [bool]$BridgePcsx2Sif
    bridge_pcsx2_iop_memory = if ($BridgePcsx2IopMemory) { (Resolve-Path -LiteralPath $BridgePcsx2IopMemory).Path } else { $null }
    source_card = if ($Scenario -eq 'saved-field') { $sourceCard } else { $null }
    source_card_sha256 = if ($Scenario -eq 'saved-field') { $sourceHash } else { $null }
    load_state = if ($LoadState) { (Resolve-Path -LiteralPath $LoadState).Path } else { $null }
    native_replay = if ($NativeReplay) { (Resolve-Path -LiteralPath $NativeReplay).Path } else { $null }
    save_state_at = $SaveStateAt
    runner_sha256 = (Get-FileHash -LiteralPath $runner -Algorithm SHA256).Hash.ToLowerInvariant()
    disc = $iso
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runDir 'run.json') -Encoding utf8

$env:PS2_DEBUG_REQUEUE = '1'
$env:PS2_GS_IMR_PENDING_IRQ = '0'
$env:PS2_GS_REJECT_INVALID_STQ = '1'
$env:PS2_GPU_LOW_POWER = '1'
$env:PS2_DUMP = Join-Path $runDir 'ram.bin'
$log = Join-Path $runDir 'run.log'
& $runner @arguments 2>&1 | Out-File -FilePath $log -Encoding utf8
$result = $LASTEXITCODE
Write-Output "Run: $runDir"
Write-Output "Exit code: $result"
if ($SaveStateAt -gt 0) { Write-Output "Native state: $(Join-Path $runDir 'checkpoint.p2state')" }
Get-Content -LiteralPath $log | Select-String '^\[ee\] stopped after|^\[ee\] no translated code|^\[mc\] card use so far' | ForEach-Object { $_.Line }
if ($Scenario -eq 'saved-field') {
    $cardHash = (Get-FileHash -LiteralPath $card -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Output "Scratch card changed: $($cardHash -ne $sourceHash)"
}
exit $result
