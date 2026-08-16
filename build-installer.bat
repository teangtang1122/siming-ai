@echo off
setlocal

set "ROOT=%~dp0"
powershell.exe -NoProfile -File "%ROOT%scripts\build-installer.ps1" %*

if errorlevel 1 (
  set "BUILD_EXIT_CODE=%ERRORLEVEL%"
  echo.
  echo Installer packaging failed. See the message above.
  if not defined CI pause
  exit /b %BUILD_EXIT_CODE%
)

exit /b 0
