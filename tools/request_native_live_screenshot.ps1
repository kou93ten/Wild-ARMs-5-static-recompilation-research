param(
    [Parameter(Mandatory)][string]$RunDir,
    [ValidateRange(1,30000)][int]$TimeoutMs = 10000
)

$ErrorActionPreference = 'Stop'
$dir = (Resolve-Path -LiteralPath $RunDir).Path
$manifest = Get-Content -LiteralPath (Join-Path $dir 'run.json') -Raw | ConvertFrom-Json
$request = [string]$manifest.request_file
if (-not (Test-Path -LiteralPath $request)) { throw "Live request file is missing: $request" }
$current = [long](Get-Content -LiteralPath $request -Raw)
$next = $current + 1
$present = Join-Path $dir "present-$next.png"
$reference = Join-Path $dir "reference-$next.png"
Set-Content -LiteralPath $request -Value ([string]$next) -NoNewline
$deadline = (Get-Date).AddMilliseconds($TimeoutMs)
while ((Get-Date) -lt $deadline) {
    if ((Test-Path -LiteralPath $present) -and (Test-Path -LiteralPath $reference)) {
        Write-Output "Native: $present"
        Write-Output "GS: $reference"
        return
    }
    if (Test-Path -LiteralPath (Join-Path $dir 'run.complete')) {
        throw "Live run ended before screenshot request $next was served. Check $(Join-Path $dir 'runner.log')"
    }
    Start-Sleep -Milliseconds 50
}
throw "Screenshot request $next was not served before timeout. Check $(Join-Path $dir 'runner.log')"
