@echo off
REM ============================================================================
REM Windows setup for The Chat Place
REM ============================================================================
REM Makes the repo's .venv and installs everything the app, its tests and the
REM Windows build need (requirements-build.txt), then checks that it all
REM imports. Modelled on Image Description Toolkit's winsetup.bat.
REM
REM   winsetup.bat          set up (an existing .venv is replaced)
REM   winsetup.bat /y       the same, without the two pauses
REM
REM Then:
REM   .venv\Scripts\pythonw TheChatPlace.pyw     run the app from source
REM   BuildAndRelease\WinBuilds\build_windows.cmd   build the installer and zip
REM
REM Error checks are flat ("if errorlevel 1 goto"), as in build_windows.cmd.
REM ============================================================================
setlocal
cd /d "%~dp0"
set "ASK=1"
if /i "%~1"=="/y" set "ASK=0"
set "PY=%~dp0.venv\Scripts\python.exe"

echo.
echo ========================================================================
echo Windows setup for The Chat Place
echo ========================================================================
echo.
echo This makes .venv in %CD%
echo and installs wxPython, PyInstaller and the rest into it.
echo.
if "%ASK%"=="1" pause

REM wxPython publishes Windows wheels for these. The release workflow uses 3.12.
set "BASE="
where py >nul 2>nul
if errorlevel 1 goto :try_python
for %%v in (3.12 3.13 3.11) do (
    if not defined BASE (
        py -%%v -c "pass" >nul 2>nul && set "BASE=py -%%v"
    )
)
:try_python
if defined BASE goto :have_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto :no_python
set "BASE=python"
:have_python
echo Python: %BASE%
%BASE% --version

if not exist .venv goto :make_venv
echo Removing the old .venv...
rmdir /s /q .venv
if exist .venv goto :venv_in_use
:make_venv
echo Creating .venv...
%BASE% -m venv .venv
if not exist "%PY%" goto :failed

echo Installing (requirements-build.txt)...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto :failed
"%PY%" -m pip install -r requirements-build.txt
if errorlevel 1 goto :pip_failed

REM pip's exit code isn't proof the environment works: check the imports.
set "PROBLEMS=0"
for %%m in (wx wx.html2 markdown theideaplace_appkit velopack truststore PyInstaller pytest) do (
    "%PY%" -c "import %%m" >nul 2>nul && echo   ok       %%m || (echo   MISSING  %%m & set /a PROBLEMS+=1 >nul)
)

echo.
echo Other things The Chat Place and its build use:
where claude >nul 2>nul && echo   ok       claude (Claude Code) || echo   missing  claude: install Claude Code with its native installer and sign in
where dotnet >nul 2>nul && echo   ok       .NET SDK (build_windows.cmd installs vpk with it) || echo   missing  .NET SDK: needed only for the installer; https://dotnet.microsoft.com/download

echo.
echo ========================================================================
if not "%PROBLEMS%"=="0" goto :import_failed
echo SETUP COMPLETE
echo ========================================================================
echo   Run from source:  .venv\Scripts\pythonw TheChatPlace.pyw
echo   Run the tests:    .venv\Scripts\python -m pytest -q tests
echo   Build the app:    BuildAndRelease\WinBuilds\build_windows.cmd
set "RESULT=0"
goto :end

:import_failed
echo SETUP FAILED: %PROBLEMS% module(s) won't import (see above)
echo ========================================================================
set "RESULT=1"
goto :end

:no_python
echo ERROR: The Chat Place needs Python 3.11 or later. Install it from python.org
echo (with "Add python.exe to PATH" ticked) and run winsetup.bat again.
set "RESULT=1"
goto :end

:venv_in_use
echo ERROR: Couldn't remove .venv. Close The Chat Place and any terminal using it,
echo then run winsetup.bat again.
set "RESULT=1"
goto :end

:pip_failed
echo.
echo ERROR: pip couldn't install everything. Scroll up for the reason.
echo Common causes: no internet, a proxy blocking PyPI, or no wxPython wheel
echo for this Python.
set "RESULT=1"
goto :end

:failed
echo ERROR: Setup failed (see the messages above).
set "RESULT=1"

:end
echo.
if "%ASK%"=="1" pause
endlocal & exit /b %RESULT%
