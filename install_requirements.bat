@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"
set "VENV_PY=%HERE%\.venv\Scripts\python.exe"

echo ============================================================
echo  WaW skin recolor - full setup ^(Python + venv + all packages^)
echo ============================================================
echo.
echo Uses a folder ".venv" next to this script so the same Python
echo and libraries are used every time ^(no PATH confusion^).
echo.

REM --- Already have venv: just refresh packages ---
if exist "%VENV_PY%" (
  echo Found existing virtual environment.
  goto :PIP_INTO_VENV
)

REM --- Find any system Python once, to create the venv ---
call :RESOLVE_SYSTEM_PYTHON
if not defined PYTHON_EXE call :INSTALL_PYTHON_WINGET
if not defined PYTHON_EXE call :RESOLVE_SYSTEM_PYTHON

if not defined PYTHON_EXE (
  echo.
  echo ERROR: No usable Python was found after setup.
  echo.
  echo Install **Python 3.10+** from https://www.python.org/downloads/
  echo In the installer, check **"Add python.exe to PATH"** and **tcl/tk** ^(for Tk GUI^).
  echo Then run this script again.
  echo.
  pause
  exit /b 1
)

echo.
echo Using Python to create venv ^(one time^) :
echo   !PYTHON_EXE!
"!PYTHON_EXE!" -m venv "%HERE%\.venv"
if errorlevel 1 (
  echo.
  echo venv creation failed. If you see a permission error, move this folder out of
  echo Program Files or run from your Desktop/Documents folder.
  pause
  exit /b 1
)

if not exist "%VENV_PY%" (
  echo ERROR: %VENV_PY% missing after venv create.
  pause
  exit /b 1
)

:PIP_INTO_VENV
echo.
echo Installing / upgrading packages into .venv ...
"%VENV_PY%" -m ensurepip --upgrade 2>nul
"%VENV_PY%" -m pip install -U pip setuptools wheel
if errorlevel 1 (
  echo pip bootstrap failed.
  pause
  exit /b 1
)
"%VENV_PY%" -m pip install -r "%HERE%\requirements.txt"
if errorlevel 1 (
  echo.
  echo pip install failed. Check errors above.
  pause
  exit /b 1
)

call :INSTALL_TEXCONV

call :RENAME_IW_FILES

echo.
echo ============================================================
echo  Done. Run **run_iwd_recolor_gui.bat** to start the tool.
echo ============================================================
pause
exit /b 0

REM ============================================================
:RESOLVE_SYSTEM_PYTHON
set "PYTHON_EXE="
REM Prefer py launcher pinned versions ^(avoids wrong python on PATH^)
for %%T in (-3.14 -3.13 -3.12 -3.11 -3.10 -3) do (
  for /f "delims=" %%i in ('py %%T -c "import sys; print(sys.executable)" 2^>nul') do (
    if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
  )
)
if defined PYTHON_EXE exit /b 0

for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%i"
if defined PYTHON_EXE exit /b 0

for %%V in (314 313 312 311 310 39) do (
  if exist "!LocalAppData!\Programs\Python\Python%%V\python.exe" (
    set "PYTHON_EXE=!LocalAppData!\Programs\Python\Python%%V\python.exe"
    exit /b 0
  )
)
for %%V in (314 313 312 311 310) do (
  if exist "C:\Program Files\Python%%V\python.exe" (
    set "PYTHON_EXE=C:\Program Files\Python%%V\python.exe"
    exit /b 0
  )
  if exist "!ProgramFiles(x86)!\Python%%V\python.exe" (
    set "PYTHON_EXE=!ProgramFiles(x86)!\Python%%V\python.exe"
    exit /b 0
  )
)
exit /b 0

REM ============================================================
:INSTALL_PYTHON_WINGET
where winget >nul 2>&1
if errorlevel 1 exit /b 0
if not exist "%SystemRoot%\System32\winget.exe" exit /b 0

echo No Python found. Installing via **winget** ^(may take a few minutes^)...
echo.

winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo Retrying Python 3.13...
  winget install --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements
)
if errorlevel 1 (
  echo Retrying Python 3.14...
  winget install --id Python.Python.3.14 -e --accept-package-agreements --accept-source-agreements
)
if errorlevel 1 (
  echo Last try: 3.12 with silent PATH options ^(may still work^)...
  winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements --override "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0"
)

echo Waiting for installer to finish writing files...
timeout /t 5 /nobreak >nul
exit /b 0

REM ============================================================
:INSTALL_TEXCONV
where texconv >nul 2>&1
if not errorlevel 1 exit /b 0
if exist "%LocalAppData%\Microsoft\WinGet\Links\texconv.exe" exit /b 0
if exist "C:\Program Files\Microsoft DirectX Texture Converter\texconv.exe" exit /b 0
if exist "%ProgramFiles(x86)%\Microsoft DirectX Texture Converter\texconv.exe" exit /b 0
where winget >nul 2>&1
if errorlevel 1 (
  echo.
  echo WARNING: **texconv** not found. The COLOR button needs DirectXTex texconv.
  echo Install manually: winget install Microsoft.DirectXTex.Texconv
  echo or copy texconv.exe into this folder: %HERE%
  exit /b 0
)
echo.
echo Installing **DirectXTex texconv** ^(DDS/PNG conversion for COLOR^)...
winget install --id Microsoft.DirectXTex.Texconv -e --accept-package-agreements --accept-source-agreements
echo Waiting for texconv to finish registering...
timeout /t 4 /nobreak >nul
exit /b 0

REM ============================================================
:RENAME_IW_FILES
echo.
echo Optional: renaming iw_00..iw_04 -^> localized_english_* ^(skip if missing^)...
call :TRY_RENAME_IW iw_00 localized_english_aa
call :TRY_RENAME_IW iw_01 localized_english_aab
call :TRY_RENAME_IW iw_02 localized_english_aac
call :TRY_RENAME_IW iw_03 localized_english_aad
call :TRY_RENAME_IW iw_04 localized_english_aae
exit /b 0

:TRY_RENAME_IW
set "RSRC=%~1"
set "RDST=%~2"
if exist "%HERE%\%RSRC%.iwd" (
  if exist "%HERE%\%RDST%.iwd" (
    echo   Skip %RSRC%.iwd: %RDST%.iwd already exists.
    exit /b 0
  )
  ren "%HERE%\%RSRC%.iwd" "%RDST%.iwd"
  if errorlevel 1 ( echo   Could not rename %RSRC%.iwd ) else ( echo   Renamed %RSRC%.iwd -^> %RDST%.iwd )
  exit /b 0
)
if exist "%HERE%\%RSRC%" (
  if exist "%HERE%\%RDST%" (
    echo   Skip %RSRC%: %RDST% already exists.
    exit /b 0
  )
  ren "%HERE%\%RSRC%" "%RDST%"
  if errorlevel 1 ( echo   Could not rename %RSRC% ) else ( echo   Renamed %RSRC% -^> %RDST% )
  exit /b 0
)
exit /b 0
