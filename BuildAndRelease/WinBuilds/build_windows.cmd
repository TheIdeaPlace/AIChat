@echo off
REM ============================================================================
REM Build The Chat Place on this PC: tests, the app, a smoke test, and the
REM Velopack installer and portable zip. The same steps as the release workflow's
REM Windows job (.github\workflows\release-thechatplace.yml), unsigned: Windows
REM signing is Azure Artifact Signing, which only the workflow does.
REM BuildAndRelease\MacBuilds\build_macos.sh is the Mac one.
REM
REM   BuildAndRelease\WinBuilds\build_windows.cmd                    into releases\ and dist\
REM   BuildAndRelease\WinBuilds\build_windows.cmd C:\Users\kelly\OneDrive\thehub
REM                                                 and copy Setup and the zip there
REM
REM Uses the repo's .venv, and makes it the first time (the same .venv you run
REM the app from source with). The build isn't signed, so SmartScreen may warn
REM when you run Setup: More info, then Run anyway.
REM
REM Error checks are flat ("if errorlevel 1 goto :failed"), as in Image
REM Description Toolkit's build scripts: cmd loses the exit code of an
REM "exit /b" inside a nested block.
REM ============================================================================
setlocal
REM The output folder is read before the cd, so a relative one means relative
REM to where you ran build_windows.cmd, not this folder.
set "OUTPUT_DIR="
if not "%~1"=="" set "OUTPUT_DIR=%~f1"
cd /d "%~dp0..\.."

REM vpk is pinned to what the release workflow uses, and PyInstaller is pinned in
REM requirements-build.txt, so a local build matches a CI one. Differences:
REM the workflow uses Python 3.12 and needs release notes for the
REM version; here any Python 3.11+ works and release notes are optional.
set "VPK_VERSION=1.2.161"
set "CHANNEL=windows"
set "PY=%CD%\.venv\Scripts\python.exe"

echo ========================================================================
echo Building The Chat Place
echo ========================================================================

REM ---------------------------------------------------------------- .venv ----
if exist "%PY%" goto :have_venv
echo.
echo Creating .venv (first time only)...
where py >nul 2>nul
if errorlevel 1 goto :venv_with_python
py -3 -m venv .venv
goto :check_venv
:venv_with_python
python -m venv .venv
:check_venv
if not exist "%PY%" goto :no_python
:have_venv

echo.
echo Installing what the build needs into .venv...
"%PY%" -m pip install --quiet --upgrade pip
if errorlevel 1 goto :failed
"%PY%" -m pip install --quiet -r requirements-build.txt
if errorlevel 1 goto :failed

REM ---------------------------------------------------------------- tests ----
echo.
echo Running the tests...
"%PY%" -m pytest -q tests
if errorlevel 1 goto :failed

set "VERSION="
for /f "usebackq delims=" %%v in (`"%PY%" tools\check_version.py`) do set "VERSION=%%v"
if not defined VERSION goto :failed
echo.
echo The Chat Place %VERSION%

REM ------------------------------------------------------------------ app ----
echo.
echo Building the app (PyInstaller)...
"%PY%" tools\make_version_info.py
if errorlevel 1 goto :failed
REM --onedir because Velopack swaps an app folder in place; velopack is imported
REM lazily, so it's named; --collect-binaries wx brings WebView2Loader.dll, which
REM the formatted message view needs; the speech scripts and NVDA's controller
REM client (thechatplace\nvda, #98) are data files.
"%PY%" -m PyInstaller --noconfirm --clean --noconsole --onedir --name TheChatPlace --version-file build\version_info.txt --hidden-import velopack --hidden-import thechatplace.providers.codex --hidden-import thechatplace.providers.codex_runtime --hidden-import thechatplace.providers.codex_aside --hidden-import thechatplace.ui.codex_tools_host --hidden-import thechatplace.providers.copilot --hidden-import thechatplace.providers.copilot_runtime --collect-binaries wx --icon thechatplace\assets\app.ico --add-data "thechatplace\assets;thechatplace\assets" --add-data "thechatplace\speech;thechatplace\speech" --add-data "thechatplace\nvda;thechatplace\nvda" TheChatPlace.pyw
if errorlevel 1 goto :failed
if not exist "dist\TheChatPlace\TheChatPlace.exe" goto :failed

echo.
echo Smoke test of the built app...
if exist smoke-local.json del smoke-local.json
start "" /wait "dist\TheChatPlace\TheChatPlace.exe" --smoke-test smoke-local.json
REM Any exit code but 0 fails, including a crash's negative one, which
REM "if errorlevel 1" would let through.
if not "%ERRORLEVEL%"=="0" goto :smoke_failed
if not exist smoke-local.json goto :smoke_failed
type smoke-local.json
echo.

REM ------------------------------------------------- installer and zip ----
REM vpk must match the velopack library the app is built with, so it's put at
REM the pinned version whether or not it's already installed.
set "VPK=%USERPROFILE%\.dotnet\tools\vpk.exe"
where dotnet >nul 2>nul
if errorlevel 1 goto :vpk_without_dotnet
echo Making sure the Velopack tool (vpk) is version %VPK_VERSION%...
dotnet tool update -g vpk --version %VPK_VERSION% >nul
if errorlevel 1 goto :failed
if exist "%VPK%" goto :have_vpk
goto :no_vpk
:vpk_without_dotnet
REM No .NET SDK: use a vpk that's already there, at whatever version.
if exist "%VPK%" goto :have_vpk
goto :no_vpk
:have_vpk

echo.
echo Packing the installer and portable zip (Velopack)...
if exist releases rmdir /s /q releases
set "NOTES="
if exist "release-notes\v%VERSION%.md" set "NOTES=--releaseNotes release-notes\v%VERSION%.md"
"%VPK%" pack --packId TheChatPlace --packVersion %VERSION% --packDir dist\TheChatPlace --mainExe TheChatPlace.exe --packTitle "The Chat Place" --packAuthors "Kelly Ford" --icon thechatplace\assets\app.ico --channel %CHANNEL% --outputDir releases --instLocation PerUser --shortcuts StartMenuRoot %NOTES%
if errorlevel 1 goto :failed
if not exist "releases\TheChatPlace-%CHANNEL%-Setup.exe" goto :failed

if "%OUTPUT_DIR%"=="" goto :done
echo.
echo Copying Setup and the portable zip to %OUTPUT_DIR%...
if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"
copy /y "releases\TheChatPlace-%CHANNEL%-Setup.exe" "%OUTPUT_DIR%\" >nul
if errorlevel 1 goto :copy_failed
copy /y "releases\TheChatPlace-%CHANNEL%-Portable.zip" "%OUTPUT_DIR%\" >nul
if errorlevel 1 goto :copy_failed

:done
echo.
echo ========================================================================
echo BUILD SUCCESSFUL: The Chat Place %VERSION% (unsigned)
echo ========================================================================
echo   Installer:  releases\TheChatPlace-%CHANNEL%-Setup.exe
echo   Portable:   releases\TheChatPlace-%CHANNEL%-Portable.zip
echo   App folder: dist\TheChatPlace
if not "%OUTPUT_DIR%"=="" echo   Copied to:  %OUTPUT_DIR%
set "RESULT=0"
goto :end

:no_vpk
echo.
echo ========================================================================
echo The app is built (dist\TheChatPlace\TheChatPlace.exe), but not the
echo installer: that needs the .NET SDK for the vpk tool. Install it from
echo https://dotnet.microsoft.com/download and run build_windows.cmd again.
echo ========================================================================
set "RESULT=1"
goto :end

:no_python
echo.
echo ERROR: Couldn't make .venv. Install Python 3.11 or later from python.org
echo (with "Add python.exe to PATH" ticked) and run build_windows.cmd again.
set "RESULT=1"
goto :end

:smoke_failed
echo.
echo ERROR: The built app's smoke test found a problem:
if exist smoke-local.json type smoke-local.json
goto :failed

:copy_failed
echo.
echo ERROR: Couldn't copy into %OUTPUT_DIR%. If OneDrive is syncing an older
echo copy, wait a moment and run build_windows.cmd again.
goto :failed

:failed
echo.
echo ========================================================================
echo BUILD FAILED (see the messages above)
echo ========================================================================
set "RESULT=1"

:end
REM Started by double-clicking: keep the window open to read the result.
REM Explorer starts it as  cmd /c ""<full path>" " ; a prompt, PowerShell,
REM another script or CI doesn't, and never waits on a key here.
if defined CI goto :exit
set "LAUNCH=%cmdcmdline%"
set "LAUNCH=%LAUNCH:"=%"
if /i "%LAUNCH%"=="%ComSpec% /c %~f0 " pause
:exit
endlocal & exit /b %RESULT%
