@echo off
rem Edit these values to choose the native debug build's display settings.
rem ASPECT: 4:3, 16:10, 16:9, or 21:9.
rem DISPLAY_MODE: Windowed, Borderless, or Fullscreen.
rem WINDOW_WIDTH and WINDOW_HEIGHT: requested window size in pixels.
rem Windowed mode fits oversized requests inside the usable desktop.
rem Borderless mode uses the desktop resolution; Fullscreen changes display mode.
rem GS scale follows the actual output height at startup: up to 480p=1x,
rem 720p=2x, 1080p=3x, above 1080p=4x. Pass -RenderScale 4 to force 4x.
rem This launcher uses the integer-scaled GS renderer. For the experimental
rem exact-pixel scene renderer, use Play Exact Resolution Debug.cmd.
rem Additional commands: -LoadLatest
set "ASPECT=16:9"
set "DISPLAY_MODE=Borderless"
set "WINDOW_WIDTH=2560"
set "WINDOW_HEIGHT=1440"

cd /d "%~dp0"
echo Integer-scaled GS renderer enabled. For exact-resolution 3D, use Play Exact Resolution Debug.cmd.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\play_native_debug.ps1" -LoadLatest -SaveArchive -Aspect "%ASPECT%" -DisplayMode "%DISPLAY_MODE%" -WindowWidth %WINDOW_WIDTH% -WindowHeight %WINDOW_HEIGHT% -AutoRenderScale %*
echo.
pause
