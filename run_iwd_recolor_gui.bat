@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

call :resolve_python
if not defined PYTHON_EXE (
  echo Python not found. Run **install_requirements.bat** first ^(it can install Python via winget^).
  pause
  exit /b 1
)

"!PYTHON_EXE!" "%~dp0iwd_recolor_gui.py"
set ERR=%errorlevel%
if %ERR% neq 0 pause
exit /b %ERR%

:resolve_python
set "PYTHON_EXE="
for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%i"
if defined PYTHON_EXE exit /b 0
for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%i"
if defined PYTHON_EXE exit /b 0
for %%V in (314 313 312 311 310) do (
  if exist "!LocalAppData!\Programs\Python\Python%%V\python.exe" (
    set "PYTHON_EXE=!LocalAppData!\Programs\Python\Python%%V\python.exe"
    exit /b 0
  )
)
exit /b 0
