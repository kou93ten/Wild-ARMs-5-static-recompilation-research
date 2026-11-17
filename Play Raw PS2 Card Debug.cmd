@echo off
rem Diagnostic title-screen launch using the preserved .ps2 card directly.
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\play_native_debug.ps1" -PersistentCard %*
echo.
pause
