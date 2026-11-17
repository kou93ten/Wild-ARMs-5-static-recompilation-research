@echo off
rem Experimental exact-pixel 3D renderer. Edit these display settings as needed.
rem The scene is rendered at the selected display size; HUD/effects still use
rem the GS reference path while renderer parity work continues.
rem Cinematics with unsupported full-screen postprocess use the GS fallback.
rem DISPLAY_MODE: Windowed, Borderless, or Fullscreen.
rem ASPECT: 4:3, 16:10, 16:9, or 21:9.
rem -LoadLatest
set "ASPECT=16:9"
set "DISPLAY_MODE=Borderless"
set "WINDOW_WIDTH=3840"
set "WINDOW_HEIGHT=2160"
rem GS texture/effect backing follows the actual output height at startup:
rem up to 480p=1x, 720p=2x, 1080p=3x, above 1080p=4x.
rem The 3D color and depth targets still match the drawable size exactly.
rem Pass -RenderScale 4 to force the previously validated backing scale.
rem -LoadLatest
set "PS2_SCENE_GEOMETRY=1"
set "PS2_SCENE_GRID_SCALE=0"

cd /d "%~dp0"
echo Experimental exact-resolution 3D renderer enabled.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\play_native_debug.ps1" -SaveArchive -Aspect "%ASPECT%" -DisplayMode "%DISPLAY_MODE%" -WindowWidth %WINDOW_WIDTH% -WindowHeight %WINDOW_HEIGHT% -AutoRenderScale %*
echo.
pause
