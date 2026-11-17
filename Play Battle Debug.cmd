@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\play_native_debug.ps1" -LoadReport "%~dp0work\native-play\20261110-143433-a4593b\reports\2026-11-10_14-39-49_f018885" %*
echo.
pause
