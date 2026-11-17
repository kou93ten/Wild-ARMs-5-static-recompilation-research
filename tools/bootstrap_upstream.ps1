param(
    [string]$UpstreamSource = 'https://github.com/exoteric33/ps2-native-recomp.git',
    [string]$Target = 'external/ps2-native-recomp'
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$targetPath = if ([IO.Path]::IsPathRooted($Target)) {
    [IO.Path]::GetFullPath($Target)
} else {
    [IO.Path]::GetFullPath((Join-Path $project $Target))
}
$patchDirectory = Join-Path $project 'patches/ps2-native-recomp'
$patch = Join-Path $patchDirectory 'current-runtime.patch'
$additions = Join-Path $patchDirectory 'additions'
$upstreamCommit = 'b1af7232b3200b12fbb8135ba583364c992f0aa5'

if (Test-Path -LiteralPath $targetPath) {
    throw "Target already exists: $targetPath. Choose a new -Target; this script will not overwrite a checkout."
}
if (-not (Test-Path -LiteralPath $patch -PathType Leaf)) { throw "Missing patch: $patch" }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $targetPath) | Out-Null
$localSource = ''
if ($UpstreamSource -notmatch '^https?://') {
    $candidate = if ([IO.Path]::IsPathRooted($UpstreamSource)) { $UpstreamSource } else { Join-Path $project $UpstreamSource }
    if (Test-Path -LiteralPath $candidate -PathType Container) {
        $localSource = (Resolve-Path -LiteralPath $candidate).Path
    }
}
if ($localSource) {
    # Offline verification can use an existing checkout's committed upstream
    # tree without copying its dirty worktree or its local game assets.
    New-Item -ItemType Directory -Path $targetPath | Out-Null
    $archive = Join-Path $targetPath '.upstream.tar'
    try {
        & git -C $localSource archive --format=tar --output=$archive $upstreamCommit
        if ($LASTEXITCODE -ne 0) { throw "Could not archive upstream commit $upstreamCommit." }
        & tar -xf $archive -C $targetPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not extract the upstream archive.' }
        # Give git apply this directory as its repository root instead of
        # letting it discover the surrounding WAV_SR repository.
        & git -C $targetPath init --quiet
        if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the offline source checkout.' }
    } finally {
        if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive }
    }
} else {
    & git clone -- $UpstreamSource $targetPath
    if ($LASTEXITCODE -ne 0) { throw 'Could not clone ps2-native-recomp.' }
    & git -C $targetPath checkout --detach $upstreamCommit
    if ($LASTEXITCODE -ne 0) { throw "Could not check out pinned upstream commit $upstreamCommit." }
}
& git -C $targetPath apply --check $patch
if ($LASTEXITCODE -ne 0) { throw 'Current runtime patch does not apply cleanly.' }
& git -C $targetPath apply $patch
if ($LASTEXITCODE -ne 0) { throw 'Could not apply current runtime patch.' }

foreach ($source in Get-ChildItem -LiteralPath $additions -File -Recurse) {
    $relative = [IO.Path]::GetRelativePath($additions, $source.FullName)
    $destination = Join-Path $targetPath $relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Copy-Item -LiteralPath $source.FullName -Destination $destination
}
Write-Output "Wild Arms V runtime source prepared at $targetPath"
