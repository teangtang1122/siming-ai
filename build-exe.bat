@echo off
setlocal

set "ROOT=%~dp0"
powershell.exe -NoProfile -File "%ROOT%scripts\build-exe.ps1" %*

if errorlevel 1 (
  set "BUILD_EXIT_CODE=%ERRORLEVEL%"
  echo.
  echo Packaging failed. See the message above.
  pause
  exit /b %BUILD_EXIT_CODE%
)

exit /b 0
