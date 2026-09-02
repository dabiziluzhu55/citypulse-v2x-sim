@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart_citypulse.ps1" -RunSmokeTest
if errorlevel 1 (
  echo.
  echo CityPulse startup failed. Check outputs\runtime logs.
  pause
  exit /b 1
)
endlocal
