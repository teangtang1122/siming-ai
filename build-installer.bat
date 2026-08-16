@echo off
setlocal

set "ROOT=%~dp0"
powershell.exe -NoProfile -File "%ROOT%scripts\build-installer.ps1" %*
if errorlevel 1 goto :failed

exit /b 0

:failed
echo.
echo Installer packaging failed. See the message above.
if not defined CI pause
exit /b 1
