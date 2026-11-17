<#
Replay the F9 field and battle states with the HUD enhancement on and off.
The city state is scripted to leave its menu and open the PAUSE overlay.
#>

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$capture = Join-Path $PSScriptRoot 'capture_native_present.ps1'
$checker = Join-Path $PSScriptRoot 'check_wa5_hud_text_capture.py'
$previous = [Environment]::GetEnvironmentVariable('PS2_WA5_GLYPH', 'Process')

$cases = @(
    @{
        Kind = 'field'; State = 'work/native-play/20261112-125802-f5352c/reports/2026-11-12_13-00-38_f180284/report.state'
        Frame = 180286; Count = 8; Press = @()
    },
    @{
        Kind = 'battle'; State = 'work/native-play/20261113-010037-e5363a/reports/2026-11-13_01-04-25_f029038/report.state'
        Frame = 29040; Count = 8; Press = @()
    },
    @{
        Kind = 'pause'; State = 'work/native-play/20261112-225151-68563f/reports/2026-11-12_22-56-14_f015554/report.state'
        Frame = 15600; Count = 24; Press = @('15558:circle:3', '15590:start:3')
    },
    @{
        Kind = 'equip'; State = 'work/native-play/20261113-021125-f11a4a/reports/2026-11-13_02-13-55_f008840/report.state'
        Frame = 8844; Count = 10; Press = @()
    }
)

try {
    foreach ($case in $cases) {
        $state = (Resolve-Path -LiteralPath (Join-Path $project $case.State)).Path
        $directories = @()
        foreach ($mode in @('0', '1')) {
            $env:PS2_WA5_GLYPH = $mode
            $output = & $capture -State $state -CardSource "$state.ps2" -Frame $case.Frame `
                -Width 1920 -Height 1080 -Scale 4 -ConsecutiveFrames $case.Count `
                -SceneProbe -Press $case.Press
            $picture = @($output | Where-Object { $_ -like 'Screenshot: *' } | Select-Object -Last 1)
            if (-not $picture) { throw "Missing $($case.Kind) capture (PS2_WA5_GLYPH=$mode)" }
            $directories += (Split-Path -Parent ($picture -replace '^Screenshot: ', ''))
        }
        & python $checker $case.Kind $directories[0] $directories[1]
        if ($LASTEXITCODE -ne 0) { throw "$($case.Kind) HUD text comparison failed." }
        Write-Output "$($case.Kind) enhanced capture: $($directories[1])"
    }
} finally {
    [Environment]::SetEnvironmentVariable('PS2_WA5_GLYPH', $previous, 'Process')
}
