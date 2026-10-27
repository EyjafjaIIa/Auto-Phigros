@echo off
chcp 65001 >nul
title Phigros - import charts from phone
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\1-import.ps1" %*
echo.
echo (window will stay open; press any key to close)
echo tip: add -DryRun to only check environment
pause >nul
