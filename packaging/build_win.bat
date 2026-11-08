@echo off
rem Build the frozen Windows app, its installer and a portable zip from a CLEAN virtual environment (a global
rem site-packages drags torch, sklearn and friends into the bundle: 962 MB instead of ~300 MB). From the repo root:
rem     packaging\build_win.bat
rem Output: dist\AURORA\ (AURORA.exe + aurora-cli.exe), dist\AURORA-<v>-windows-setup.exe, dist\AURORA-<v>-windows-portable.zip
setlocal
set ROOT=%~dp0..
pushd "%ROOT%"
set VENV=%ROOT%\.venv-build
if not exist "%VENV%\Scripts\python.exe" (
    py -3.12 -m venv "%VENV%" || py -3.11 -m venv "%VENV%" || goto :fail
)
"%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip || goto :fail
"%VENV%\Scripts\python.exe" -m pip install --quiet "%ROOT%[build]" -c "%ROOT%\packaging\constraints.txt" || goto :fail
for /f "delims=" %%v in ('"%VENV%\Scripts\python.exe" -c "import aurora; print(aurora.__version__)"') do set VERSION=%%v
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --clean --log-level WARN "%ROOT%\packaging\aurora.spec" --distpath "%ROOT%\dist" --workpath "%ROOT%\build" || goto :fail
"%VENV%\Scripts\python.exe" "%ROOT%\packaging\third_party_licenses.py" "%ROOT%\dist\AURORA\THIRD_PARTY_LICENSES.txt" || goto :fail
xcopy /y /q /i "%ROOT%\packaging\LICENSES" "%ROOT%\dist\AURORA\LICENSES" >nul || goto :fail
"%ROOT%\dist\AURORA\aurora-cli.exe" --selftest --out "%ROOT%\dist\selftest-windows.json" || (echo frozen self-test FAILED & goto :fail)
set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" (echo Inno Setup 6 not found: no installer built & goto :zip)
"%ISCC%" /Q /DMyAppVersion=%VERSION% "%ROOT%\packaging\aurora.iss" || goto :fail
:zip
powershell -NoProfile -Command "Compress-Archive -Force -Path '%ROOT%\dist\AURORA' -DestinationPath '%ROOT%\dist\AURORA-%VERSION%-windows-portable.zip'" || goto :fail
echo built %ROOT%\dist\AURORA, AURORA-%VERSION%-windows-setup.exe and AURORA-%VERSION%-windows-portable.zip
popd
endlocal
exit /b 0
:fail
popd
endlocal
exit /b 1
