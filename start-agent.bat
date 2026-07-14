@echo off
setlocal

set "ROOT=%~dp0"
set "RESTART=-Restart"

if /I "%~1"=="no-restart" set "RESTART="
if /I "%~1"=="--no-restart" set "RESTART="

powershell.exe -NoProfile -File "%ROOT%scripts\start-agent.ps1" %RESTART%

if errorlevel 1 (
  echo.
  echo Startup failed. See the message above.
  pause
)
