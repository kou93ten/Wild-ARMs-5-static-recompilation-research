@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\play_native_debug.ps1" -LoadLatest %*
echo.
pause
