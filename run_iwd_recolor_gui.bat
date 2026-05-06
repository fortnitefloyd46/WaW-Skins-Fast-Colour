@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"
set "VENV_PY=%HERE%\.venv\Scripts\python.exe"

if exist "%VENV_PY%" (
  "%VENV_PY%" "%HERE%\iwd_recolor_gui.py"
  set ERR=!errorlevel!
  if !ERR! neq 0 (
    echo.
    echo If you see "No module named ...", run **install_requirements.bat** again.
    pause
  )
  exit /b !ERR!
)

echo ============================================================
echo  No virtual environment found at:
echo    %VENV_PY%
echo.
echo  Run **install_requirements.bat** first.
echo  It installs Python ^(if needed^), creates .venv, and installs
echo  Pillow, numpy, pygame-ce, and all other dependencies.
echo ============================================================
echo.
pause
exit /b 1
