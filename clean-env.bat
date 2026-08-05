@echo off
setlocal enabledelayedexpansion

set "ROOT=%~dp0"
set "FORCE="

rem Check all arguments for force flag
:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="force" set "FORCE=1"
if /i "%~1"=="--force" set "FORCE=1"
if /i "%~1"=="-f" set "FORCE=1"
if /i "%~1"=="/f" set "FORCE=1"
shift
goto parse_args
:args_done

echo ==========================================
echo   Python Environment Cleanup Utility
echo ==========================================
echo.
echo This will remove:
echo   - backend\.py-runtime (Python runtime)
echo   - backend\.venv (Virtual environment)
echo.

if defined FORCE (
  echo Force mode: will skip confirmation
  echo.
) else (
  echo Warning: This action cannot be undone.
  echo.
  choice /M "Are you sure you want to continue"
  if errorlevel 2 (
    echo.
    echo Cleanup cancelled.
    pause
    exit /b 0
  )
)

echo.
echo Starting cleanup...
echo.

powershell.exe -NoProfile -File "%ROOT%scripts\clean-env.ps1"

if errorlevel 1 (
  echo.
  echo Cleanup failed. See the message above.
  pause
  exit /b 1
)

echo.
echo ==========================================
echo   Cleanup completed successfully!
echo ==========================================
echo.
echo Next step: Run start-agent.bat to set up a new environment
echo.
if not defined CI pause
exit /b 0
