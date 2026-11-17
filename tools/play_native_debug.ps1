param(
    [string]$SourceCard = '',
    [string]$LoadReport = '',
    [switch]$LoadLatest,
    [switch]$PersistentCard,
    [switch]$SaveArchive,
    [ValidateSet('4:3', '16:10', '16:9', '21:9')]
    [string]$Aspect = '4:3',
    [ValidateSet('Windowed', 'Borderless', 'Fullscreen')]
    [string]$DisplayMode = 'Windowed',
    [ValidateRange(0, 16384)]
    [int]$WindowWidth = 0,
    [ValidateRange(0, 16384)]
    [int]$WindowHeight = 0,
    [ValidateRange(0, 6)]
    [int]$RenderScale = 0,
    [switch]$AutoRenderScale,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
if (($WindowWidth -eq 0) -ne ($WindowHeight -eq 0)) { throw 'Set both -WindowWidth and -WindowHeight, or neither.' }
if ($WindowWidth -ne 0 -and ($WindowWidth -lt 320 -or $WindowHeight -lt 240)) {
    throw 'Window size must be at least 320x240.'
}
$project = Split-Path -Parent $PSScriptRoot
$checkout = Join-Path $project 'external\ps2-native-recomp'
$runner = Join-Path $checkout 'build\native-wildarms5\ps2run.exe'
$elf = Join-Path $checkout 'games\wildarms5\disc\SLUS_216.15'
$iso = 'D:\Emulation\PS2\Wild Arms V\WildArmsV.iso'
$gameDir = Join-Path $checkout 'games\wildarms5'
$defaultCard = Join-Path $project 'work\capture-sessions\current\memcard-slot-1-final.ps2'
$playerCard = Join-Path $project 'work\native-play\player-card.ps2'
$archivePath = Join-Path $project 'work\saves\wild-arms-v.wavsave'
$saveTool = Join-Path $PSScriptRoot 'save_archive.py'
$python = Join-Path $project 'work\toolchain\python\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $pythonCommand = Get-Command python3, python -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pythonCommand) { $python = $pythonCommand.Source }
}
if ($LoadReport -and $SourceCard) { throw 'Use either -LoadReport or -SourceCard; a report has its own matching card.' }
if ($LoadLatest -and ($LoadReport -or $SourceCard)) { throw 'Use -LoadLatest without -LoadReport or -SourceCard; it selects the newest state.' }
if ($PersistentCard -and $SourceCard) { throw 'Use either -PersistentCard or -SourceCard.' }
if ($SaveArchive -and ($PersistentCard -or $SourceCard)) { throw 'Use -SaveArchive by itself, or choose a raw .ps2 card for debugging.' }

function Test-NativeStateHeader([string]$path) {
    try { $stream = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite) }
    catch { return $false }
    try {
        $header = New-Object byte[] 12
        if ($stream.Read($header, 0, $header.Length) -ne $header.Length) { return $false }
        $magic = [System.Text.Encoding]::ASCII.GetString($header, 0, 8)
        $version = [System.BitConverter]::ToUInt32($header, 8)
        return $magic -eq 'PS2STATE' -and $version -ge 4 -and $version -le 9
    } finally { $stream.Dispose() }
}

# F9 reports and F5 quick saves each have a card copy beside their native state.
# Choose by state write time across sessions, so the shortcut never points at a
# hard-coded report and does not accidentally select an input recording's state.
$capture = $null
if ($LoadLatest) {
    $candidates = @()
    $playRoot = Join-Path $project 'work\native-play'
    foreach ($playSession in (Get-ChildItem -LiteralPath $playRoot -Directory -ErrorAction SilentlyContinue)) {
        $reports = Join-Path $playSession.FullName 'reports'
        if (Test-Path -LiteralPath $reports -PathType Container) {
            foreach ($folder in (Get-ChildItem -LiteralPath $reports -Directory)) {
                $state = Get-Item -LiteralPath (Join-Path $folder.FullName 'report.state') -ErrorAction SilentlyContinue
                $cardCopy = Get-Item -LiteralPath (Join-Path $folder.FullName 'report.state.ps2') -ErrorAction SilentlyContinue
                if ($state -and $cardCopy -and $state.Length -ge 1MB -and $cardCopy.Length -ge 1MB -and (Test-NativeStateHeader $state.FullName)) {
                    $candidates += [pscustomobject]@{ Kind = 'F9'; Source = $folder.FullName; State = $state.FullName; Card = $cardCopy.FullName; TimeUtc = $state.LastWriteTimeUtc }
                }
            }
        }
        $states = Join-Path $playSession.FullName 'states'
        if (Test-Path -LiteralPath $states -PathType Container) {
            foreach ($state in (Get-ChildItem -LiteralPath $states -File -Filter 'quick_*.state')) {
                $cardCopy = Get-Item -LiteralPath ($state.FullName + '.ps2') -ErrorAction SilentlyContinue
                if ($cardCopy -and $state.Length -ge 1MB -and $cardCopy.Length -ge 1MB -and (Test-NativeStateHeader $state.FullName)) {
                    $candidates += [pscustomobject]@{ Kind = 'F5'; Source = $state.FullName; State = $state.FullName; Card = $cardCopy.FullName; TimeUtc = $state.LastWriteTimeUtc }
                }
            }
        }
    }
    $capture = $candidates | Sort-Object TimeUtc, State -Descending | Select-Object -First 1
    if (-not $capture) { throw "No complete F5 or F9 debug state found under $playRoot" }
}

$report = if ($capture -and $capture.Kind -eq 'F9') { $capture.Source } elseif ($LoadReport) { (Resolve-Path -LiteralPath $LoadReport).Path } else { '' }
$loadState = if ($capture) { $capture.State } elseif ($report) { Join-Path $report 'report.state' } else { '' }
$archiveSeed = if (Test-Path -LiteralPath $playerCard -PathType Leaf) { $playerCard } else { $defaultCard }
$cardSource = if ($SaveArchive) {
    if (Test-Path -LiteralPath $archivePath -PathType Leaf) { $archivePath } else { $archiveSeed }
} elseif ($PersistentCard) {
    if (Test-Path -LiteralPath $playerCard -PathType Leaf) { $playerCard } else { $defaultCard }
} elseif ($capture) { $capture.Card } elseif ($report) { Join-Path $report 'report.state.ps2' } elseif ($SourceCard) { (Resolve-Path -LiteralPath $SourceCard).Path } else { $defaultCard }

foreach ($path in (@($runner, $elf, $iso, $cardSource) + @($loadState | Where-Object { $_ }))) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing required file: $path" }
}
if ($SaveArchive) {
    $required = @($python, $saveTool)
    if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) { $required += $archiveSeed }
    foreach ($path in $required) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing archive tool or seed card: $path" }
    }
}

$name = '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 6))
$session = Join-Path $project "work\native-play\$name"
$card = if ($PersistentCard) { $playerCard } else { Join-Path $session 'card.ps2' }
$sharedKeys = Join-Path $project 'work\native-play\keys.ini'
$misses = Join-Path $session 'misses.txt'
$ram = Join-Path $session 'ram.bin'
$modeArgument = switch ($DisplayMode) {
    'Windowed' { '--window' }
    'Borderless' { '--borderless' }
    'Fullscreen' { '--fullscreen' }
}
$arguments = @($elf, '--disc', $iso, $modeArgument, '--aspect', $Aspect, '--memcard', $card,
               '--quick-dir', $session, '--game-dir', $gameDir, '--misses', $misses)
if ($WindowWidth -and $DisplayMode -ne 'Borderless') { $arguments += @('--window-size', "${WindowWidth}x${WindowHeight}") }
if ($loadState) { $arguments += @('--load-state', $loadState) }
$exactScene = $env:PS2_SCENE_GEOMETRY -eq '1'
$sceneGridScale = if ($exactScene -and $env:PS2_SCENE_GRID_SCALE -in @('2', '3')) { [int]$env:PS2_SCENE_GRID_SCALE } else { 0 }
$useAutoRenderScale = $AutoRenderScale -and -not $PSBoundParameters.ContainsKey('RenderScale')

if ($DryRun) {
    Write-Output "Executable: $runner"
    if ($capture) { Write-Output "Selected capture: $($capture.Kind) $($capture.Source)" }
    Write-Output "Source card: $cardSource"
    Write-Output "Memory card in use: $card$(if ($PersistentCard) { ' (persistent across sessions)' } elseif ($SaveArchive) { ' (temporary, generated from archive)' })"
    if ($SaveArchive) { Write-Output "Portable save archive: $archivePath" }
    if ($loadState) { Write-Output "Load state: $loadState" }
    Write-Output "Session folder to create: $session"
    if ($useAutoRenderScale) { Write-Output 'GS internal: automatic 1x-4x from actual output height at startup' }
    elseif ($RenderScale) { Write-Output "GS internal: $($RenderScale * 512)x$($RenderScale * 448) (${RenderScale}x)" }
    if ($sceneGridScale) {
        Write-Output "Scene renderer: experimental $(512 * $sceneGridScale)x$(448 * $sceneGridScale) internal color/depth target (${sceneGridScale}x PS2 grid)"
    } elseif ($exactScene) { Write-Output 'Scene renderer: experimental exact-size color/depth target' }
    if ($DisplayMode -eq 'Borderless') { Write-Output 'Borderless output uses the current desktop resolution.' }
    Write-Output "Arguments: $($arguments -join ' ')"
    return
}

New-Item -ItemType Directory -Force -Path $session | Out-Null
if (-not $SaveArchive -and $cardSource -ne $card) { Copy-Item -LiteralPath $cardSource -Destination $card }
if (Test-Path -LiteralPath $sharedKeys -PathType Leaf) {
    Copy-Item -LiteralPath $sharedKeys -Destination (Join-Path $session 'keys.ini')
}
$sourceHash = (Get-FileHash -LiteralPath $cardSource -Algorithm SHA256).Hash.ToLowerInvariant()
if (-not $SaveArchive -and (Get-FileHash -LiteralPath $card -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sourceHash) {
    throw 'Memory-card source did not verify'
}
@{
    started_local = (Get-Date).ToString('o')
    runner = $runner
    runner_sha256 = (Get-FileHash -LiteralPath $runner -Algorithm SHA256).Hash.ToLowerInvariant()
    disc = $iso
    aspect = $Aspect
    display_mode = $DisplayMode
    window_width = $WindowWidth
    window_height = $WindowHeight
    render_scale = $RenderScale
    auto_render_scale = [bool]$useAutoRenderScale
    exact_scene_probe = $exactScene
    scene_grid_scale = $sceneGridScale
    scene_internal_width = if ($sceneGridScale) { 512 * $sceneGridScale } elseif ($exactScene -and $DisplayMode -eq 'Windowed' -and $WindowWidth) { $WindowWidth } else { $null }
    scene_internal_height = if ($sceneGridScale) { 448 * $sceneGridScale } elseif ($exactScene -and $DisplayMode -eq 'Windowed' -and $WindowHeight) { $WindowHeight } else { $null }
    gs_internal_width = if ($RenderScale) { 512 * $RenderScale } else { $null }
    gs_internal_height = if ($RenderScale) { 448 * $RenderScale } else { $null }
    source_card = if ($SaveArchive) { $null } else { $cardSource }
    source_card_sha256 = if ($SaveArchive) { $null } else { $sourceHash }
    save_archive = if ($SaveArchive) { $archivePath } else { $null }
    save_archive_sha256_before = if ($SaveArchive -and (Test-Path -LiteralPath $archivePath)) { $sourceHash } else { $null }
    archive_seed_card = if ($SaveArchive) { $archiveSeed } else { $null }
    scratch_card = if ($PersistentCard) { $null } else { $card }
    persistent_card = if ($PersistentCard) { $card } else { $null }
    load_report = if ($report) { $report } else { $null }
    load_capture = if ($capture) { $capture.Source } elseif ($report) { $report } else { $null }
    load_kind = if ($capture) { $capture.Kind } elseif ($report) { 'F9' } else { $null }
    load_state_sha256 = if ($loadState) { (Get-FileHash -LiteralPath $loadState -Algorithm SHA256).Hash.ToLowerInvariant() } else { $null }
    arguments = $arguments
} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $session 'session.json') -Encoding utf8

$env:PS2_DEBUG_REQUEUE = '1'
$env:PS2_RECORD = '1'
$env:PS2_PAD_LOG = '1'
$env:PS2_DUMP = $ram
# Work around the FINISH/IMR ordering at Wild ARMs V's battle transition.
$env:PS2_GS_IMR_PENDING_IRQ = '0'
$env:PS2_GS_REJECT_INVALID_STQ = '1'
# Entry 0x1B8 in the captured VU1 field program produces missing terrain
# when compiled. Keep the rest of VU1 on the faster compiled path.
$env:PS2_VU_INTERPRET_ENTRIES = '1B8'
if ($useAutoRenderScale) {
    $env:PS2_SCALE = ''
    $env:PS2_SCALE_AUTO = '1'
} elseif ($PSBoundParameters.ContainsKey('RenderScale')) {
    if ($RenderScale) { $env:PS2_SCALE = [string]$RenderScale }
    $env:PS2_SCALE_AUTO = '0'
}
Write-Output "Native play session: $session"
if ($capture) { Write-Output "Resuming newest $($capture.Kind) state: $($capture.Source)" }
if ($PersistentCard) { Write-Output "Persistent memory card: $card" }
if ($SaveArchive) { Write-Output "Portable save archive: $archivePath" }
Write-Output "Aspect ratio: $Aspect"
Write-Output "Display mode: $DisplayMode"
if ($sceneGridScale) {
    Write-Output "Scene renderer: experimental $(512 * $sceneGridScale)x$(448 * $sceneGridScale) internal color/depth target (${sceneGridScale}x PS2 grid)"
} elseif ($exactScene) { Write-Output 'Scene renderer: experimental exact-size color/depth target' }
if ($WindowWidth -and $DisplayMode -ne 'Borderless') { Write-Output "Requested window size: ${WindowWidth}x${WindowHeight}" }
if ($DisplayMode -eq 'Borderless') { Write-Output 'Borderless output uses the current desktop resolution.' }
if ($RenderScale) {
    $sizeMessage = "GS internal: $($RenderScale * 512)x$($RenderScale * 448) (${RenderScale}x)"
    if ($WindowWidth -and $DisplayMode -ne 'Borderless') { $sizeMessage += "; requested output: ${WindowWidth}x${WindowHeight}" }
    Write-Output $sizeMessage
} elseif ($useAutoRenderScale) {
    Write-Output 'GS internal: automatic 1x-4x from actual output height at startup'
}
Write-Output 'F1: controls/menu    F5: quick save    F8: quick load    F9: capture state, screenshot, log, and inputs'
Write-Output 'The game window is opening. Play with a controller or keyboard.'
if ($SaveArchive) {
    $archiveArguments = @('run', '--archive', $archivePath, '--card', $card, '--runner', $runner)
    if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) { $archiveArguments += @('--seed-card', $archiveSeed) }
    & $python $saveTool @archiveArguments -- @arguments
} else {
    & $runner @arguments
}
$exitCode = $LASTEXITCODE
if (Test-Path -LiteralPath (Join-Path $session 'keys.ini') -PathType Leaf) {
    Copy-Item -LiteralPath (Join-Path $session 'keys.ini') -Destination $sharedKeys -Force
}
Write-Output "Native runner exit code: $exitCode"
Write-Output "Session folder: $session"
Write-Output "F9 reports: $(Join-Path $session 'reports')"
Write-Output "F5 states and input recordings: $(Join-Path $session 'states')"
if ($SaveArchive) {
    if (Test-Path -LiteralPath $archivePath -PathType Leaf) {
        $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
        Write-Output "Save archive updated: $($archiveHash -ne $sourceHash)"
    } else {
        Write-Output 'Save archive was not created; the temporary card remains in the session folder.'
    }
} else {
    Write-Output "Memory card changed: $(((Get-FileHash -LiteralPath $card -Algorithm SHA256).Hash.ToLowerInvariant()) -ne $sourceHash)"
}
exit $exitCode
