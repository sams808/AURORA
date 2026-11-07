@echo off
rem Build the frozen Windows app from a CLEAN virtual environment (a global site-packages drags torch, sklearn and
rem friends into the bundle: 962 MB instead of ~250 MB). Run from the repo root:  packaging\build_win.bat
setlocal
set ROOT=%~dp0..
set VENV=%ROOT%\.venv-build
if not exist "%VENV%\Scripts\python.exe" (
    py -3.12 -m venv "%VENV%" || py -3.11 -m venv "%VENV%" || exit /b 1
)
"%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip || exit /b 1
"%VENV%\Scripts\python.exe" -m pip install --quiet -e "%ROOT%[build]" || exit /b 1
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --clean --log-level WARN "%ROOT%\packaging\aurora.spec" --distpath "%ROOT%\dist" --workpath "%ROOT%\build" || exit /b 1
"%ROOT%\dist\AURORA\AURORA.exe" --distcheck || (echo frozen distcheck FAILED & exit /b 1)
echo built %ROOT%\dist\AURORA
endlocal
