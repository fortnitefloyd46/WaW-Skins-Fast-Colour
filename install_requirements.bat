@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo IWD team color tool - install Python ^(if needed^) and pip packages
echo.

call :resolve_python
if defined PYTHON_EXE goto :pip_install

echo Python was not found on this PC.
where winget >nul 2>&1
if errorlevel 1 goto :no_winget
if not exist "%SystemRoot%\System32\winget.exe" goto :no_winget

echo Installing Python 3.12 via winget ^(this can take a few minutes^)...
winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo First attempt failed; trying Python 3.13...
  winget install --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements
)

echo.
call :resolve_python
if defined PYTHON_EXE goto :pip_install

echo Winget finished, but Python is not visible in this window yet.
echo Searching the default install folder...
for %%V in (314 313 312 311 310) do (
  if exist "!LocalAppData!\Programs\Python\Python%%V\python.exe" (
    set "PYTHON_EXE=!LocalAppData!\Programs\Python\Python%%V\python.exe"
    goto :pip_install
  )
)

echo.
echo Python is still not found automatically.
echo Close this window, open a new Command Prompt, and run this script again.
echo Or install from https://www.python.org/downloads/ and check "Add python.exe to PATH".
pause
exit /b 1

:no_winget
echo winget is not available on this system.
echo.
echo Install Python 3.10 or newer from https://www.python.org/downloads/
echo During setup, enable **Add python.exe to PATH**, then run this script again.
pause
exit /b 1

:pip_install
echo.
echo Using: !PYTHON_EXE!
echo Installing / upgrading pip and requirements...
"!PYTHON_EXE!" -m ensurepip --upgrade 2>nul
"!PYTHON_EXE!" -m pip install -U pip setuptools wheel
if errorlevel 1 (
  echo pip upgrade failed.
  pause
  exit /b 1
)
"!PYTHON_EXE!" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo.
  echo pip install reported an error. Check the messages above.
  pause
  exit /b 1
)

call :rename_iw_files

echo.
echo Done.
pause
exit /b 0

:rename_iw_files
echo.
echo Looking for iw_00 .. iw_04 to rename to localized_english_aa .. aae ^(skip if missing^)...
call :try_rename_iw iw_00 localized_english_aa
call :try_rename_iw iw_01 localized_english_aab
call :try_rename_iw iw_02 localized_english_aac
call :try_rename_iw iw_03 localized_english_aad
call :try_rename_iw iw_04 localized_english_aae
exit /b 0

:try_rename_iw
set "RSRC=%~1"
set "RDST=%~2"
if exist "%~dp0%RSRC%.iwd" (
  if exist "%~dp0%RDST%.iwd" (
    echo   Skip %RSRC%.iwd: %RDST%.iwd already exists.
    exit /b 0
  )
  ren "%~dp0%RSRC%.iwd" "%RDST%.iwd"
  if errorlevel 1 (
    echo   Could not rename %RSRC%.iwd
  ) else (
    echo   Renamed %RSRC%.iwd -^> %RDST%.iwd
  )
  exit /b 0
)
if exist "%~dp0%RSRC%" (
  if exist "%~dp0%RDST%" (
    echo   Skip %RSRC%: %RDST% already exists.
    exit /b 0
  )
  ren "%~dp0%RSRC%" "%RDST%"
  if errorlevel 1 (
    echo   Could not rename %RSRC%
  ) else (
    echo   Renamed %RSRC% -^> %RDST%
  )
  exit /b 0
)
exit /b 0

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
