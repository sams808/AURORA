@echo off
rem Build the frozen Windows app, its installer and the portable zip from a CLEAN virtual environment (a global
rem site-packages drags torch, sklearn and friends into the bundle: 962 MB instead of ~300 MB). From the repo root:
rem     packaging\build_win.bat              build dist\AURORA, the setup and the zip
rem     packaging\build_win.bat /install     the same, then install the fresh setup silently for this user
rem Output: dist\AURORA\ (AURORA.exe + aurora-cli.exe + licences), dist\AURORA-<v>-windows-x64-setup.exe,
rem         dist\AURORA-<v>-windows-x64.zip
rem The build environment .venv-build is Python 3.12 (uv or the py launcher), with the pins of
rem packaging\constraints.txt; it is recreated when its Python, constraints.txt or pyproject.toml changed.
rem Never build while AURORA runs from dist\AURORA: PyInstaller replaces that folder.
setlocal
set "ROOT=%~dp0.."
pushd "%ROOT%"
set "ROOT=%CD%"
set "INSTALL="
if /i "%~1"=="/install" set "INSTALL=1"
set "VENV=%ROOT%\.venv-build"
set "PYV=%VENV%\Scripts\python.exe"
set "STAMPFILE=%VENV%\aurora-build-stamp.txt"

rem ---------------------------------------------------------------- build environment (recreated when stale)
set "HAVE="
set "NOW="
if exist "%STAMPFILE%" set /p HAVE=<"%STAMPFILE%"
if exist "%PYV%" for /f "delims=" %%s in ('""%PYV%" "%ROOT%\packaging\specutil.py" stamp" 2^>nul') do set "NOW=%%s"
if "%NOW%"=="" goto :newvenv
if not "%NOW%"=="%HAVE%" goto :newvenv
if not "%NOW:~0,7%"=="py3.12." goto :newvenv
goto :venvok

:newvenv
if exist "%VENV%" echo recreating the stale build environment %VENV%
if exist "%VENV%" rmdir /s /q "%VENV%"
where uv >nul 2>nul
if errorlevel 1 goto :pylauncher
uv venv --seed --python 3.12 "%VENV%" || goto :fail
goto :created
:pylauncher
py -3.12 -c "pass" >nul 2>nul
if errorlevel 1 (
    echo AURORA builds with Python 3.12: install it ^(winget install Python.Python.3.12^) or uv ^(winget install astral-sh.uv^), then run this script again.
    goto :fail
)
py -3.12 -m venv "%VENV%" || goto :fail
:created
"%PYV%" -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" || (echo the build environment is not Python 3.12 & goto :fail)
"%PYV%" -m pip install --quiet --upgrade pip || goto :fail
"%PYV%" -m pip install --quiet "%ROOT%[build]" -c "%ROOT%\packaging\constraints.txt" || goto :fail
"%PYV%" "%ROOT%\packaging\specutil.py" stamp > "%STAMPFILE%" || goto :fail
:venvok
rem AURORA itself, non-editable, every time (its metadata carries the version being built)
"%PYV%" -m pip install --quiet --no-deps --force-reinstall "%ROOT%" -c "%ROOT%\packaging\constraints.txt" || goto :fail
for /f "delims=" %%v in ('""%PYV%" "%ROOT%\packaging\specutil.py" version"') do set "VERSION=%%v"
if "%VERSION%"=="" (echo could not read the version from aurora\__init__.py & goto :fail)
echo building AURORA %VERSION% with Python 3.12 from %VENV%

rem ---------------------------------------------------------------- freeze, self-test
"%PYV%" -m PyInstaller --noconfirm --clean --log-level WARN "%ROOT%\packaging\aurora.spec" --distpath "%ROOT%\dist" --workpath "%ROOT%\build" || goto :fail
"%ROOT%\dist\AURORA\aurora-cli.exe" --version || goto :fail
"%ROOT%\dist\AURORA\aurora-cli.exe" --selftest --out "%ROOT%\dist\selftest-windows.json" || (echo frozen self-test FAILED & goto :fail)

rem ---------------------------------------------------------------- installer (Inno Setup 6), portable zip
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" goto :noiscc
"%ISCC%" /Q "/DMyAppVersion=%VERSION%" "%ROOT%\packaging\aurora.iss" || goto :fail
goto :zip
:noiscc
echo Inno Setup 6 not found, so no installer was built. Install it with: winget install --id JRSoftware.InnoSetup -e
set "INSTALL="
:zip
if exist "%ROOT%\dist\AURORA-%VERSION%-windows-x64.zip" del /q "%ROOT%\dist\AURORA-%VERSION%-windows-x64.zip"
powershell -NoProfile -Command "Compress-Archive -Force -Path '%ROOT%\dist\AURORA' -DestinationPath '%ROOT%\dist\AURORA-%VERSION%-windows-x64.zip'" || goto :fail
echo built %ROOT%\dist\AURORA, AURORA-%VERSION%-windows-x64-setup.exe and AURORA-%VERSION%-windows-x64.zip

rem ---------------------------------------------------------------- optional: install for this user
if not defined INSTALL goto :done
echo installing AURORA %VERSION% for this user (%LOCALAPPDATA%\Programs\AURORA)
"%ROOT%\dist\AURORA-%VERSION%-windows-x64-setup.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /MERGETASKS="desktopicon" /LOG="%ROOT%\dist\install.log" || (echo the installation failed: see dist\install.log & goto :fail)
echo installed: %LOCALAPPDATA%\Programs\AURORA\AURORA.exe
:done
popd
endlocal
exit /b 0
:fail
popd
endlocal
exit /b 1
