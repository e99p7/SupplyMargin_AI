@echo off
setlocal
title SupplyMargin AI - Docker installer and launcher
cd /d "%~dp0"

echo SupplyMargin AI: Docker Desktop, daemon and project startup
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_docker_and_run_windows.ps1"
set "SUPPLYMARGIN_EXIT=%ERRORLEVEL%"

echo.
if not "%SUPPLYMARGIN_EXIT%"=="0" (
  echo Startup failed with code %SUPPLYMARGIN_EXIT%.
  echo Read docs\windows_docker_ru.md or send a screenshot of this window.
) else (
  echo SupplyMargin AI is running at http://localhost:8000/
)
echo.
pause
exit /b %SUPPLYMARGIN_EXIT%
