param(
    [Parameter(Mandatory)][string]$State,
    [Parameter(Mandatory)][string]$CardSource,
    [Parameter(Mandatory)][long]$Frames,
    [string[]]$Axis = @(),
    [string[]]$Press = @(),
    [ValidateRange(320,8192)][int]$Width = 1280,
    [ValidateRange(240,8192)][int]$Height = 720,
    [switch]$SceneProbe
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$checkout = Join-Path $project 'external\ps2-native-recomp'
$runner = Join-Path $checkout 'build\native-wildarms5\ps2run.exe'
$elf = Join-Path $checkout 'games\wildarms5\disc\SLUS_216.15'
$game = Join-Path $checkout 'games\wildarms5'
$iso = 'D:\Emulation\PS2\Wild Arms V\WildArmsV.iso'
$sourceState = (Resolve-Path -LiteralPath $State).Path
$sourceCard = (Resolve-Path -LiteralPath $CardSource).Path
$name = '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 6))
$dir = Join-Path $project "work\native-live\$name"
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$card = Join-Path $dir 'card.ps2'
Copy-Item -LiteralPath $sourceCard -Destination $card
$request = Join-Path $dir 'request.txt'
Set-Content -LiteralPath $request -Value '0' -NoNewline
$env:PS2_PRESENT_CAPTURE = Join-Path $dir 'present.png'
$env:PS2_PRESENT_CAPTURE_REFERENCE = Join-Path $dir 'reference.png'
$env:PS2_PRESENT_CAPTURE_REQUEST = $request
$env:PS2_PRESENT_CAPTURE_COUNT = '1'
$env:PS2_PRESENT_CAPTURE_AT = '0'
$env:PS2_SCENE_GEOMETRY = if ($SceneProbe) { '1' } else { '0' }
$env:PS2_SCALE_AUTO = if ($SceneProbe) { '1' } else { '0' }
$env:PS2_DEBUG_REQUEUE = '1'
$env:PS2_GS_IMR_PENDING_IRQ = '0'
$env:PS2_GS_REJECT_INVALID_STQ = '1'
$env:PS2_VU_INTERPRET_ENTRIES = '1B8'
$arguments = @($elf, '--disc', $iso, '--window', '--window-size', "${Width}x${Height}",
    '--aspect', '16:9', '--memcard', $card, '--quick-dir', $dir, '--game-dir', $game,
    '--load-state', $sourceState, '--frames', [string]$Frames)
foreach ($spec in $Axis) { $arguments += @('--axis', $spec) }
foreach ($spec in $Press) { $arguments += @('--press', $spec) }
[ordered]@{
    state = $sourceState
    state_sha256 = (Get-FileHash -LiteralPath $sourceState -Algorithm SHA256).Hash.ToLowerInvariant()
    card_source = $sourceCard
    card_source_sha256 = (Get-FileHash -LiteralPath $sourceCard -Algorithm SHA256).Hash.ToLowerInvariant()
    axis = $Axis
    press = $Press
    frames = $Frames
    resolution = "${Width}x${Height}"
    request_file = $request
    scene_probe = [bool]$SceneProbe
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dir 'run.json') -Encoding utf8
Write-Output "Live run: $dir"
Write-Output "Request a screenshot in another shell with: .\tools\request_native_live_screenshot.ps1 -RunDir `"$dir`""
& $runner @arguments 2>&1 | Tee-Object -FilePath (Join-Path $dir 'runner.log') | Out-Null
Set-Content -LiteralPath (Join-Path $dir 'run.complete') -Value ([string]$LASTEXITCODE) -NoNewline
Write-Output "Runner exit code: $LASTEXITCODE"
