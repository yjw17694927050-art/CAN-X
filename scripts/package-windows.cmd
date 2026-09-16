@echo off
rem CAN-X V0.1.1 - single Windows packaging entry point (clean-checkout reproducible).
rem
rem Steps, in order, failing fast at the first error:
rem   1. build the packaged Python runtime   (scripts\build-runtime.cmd)
rem   2. verify the staged Tauri sidecar
rem   3. run the packaged-runtime smoke test
rem   4. build the Tauri MSI                (scripts\tauri-bundle.cmd)
rem   5. verify the MSI artifact
rem   6. return the correct exit code
rem
rem Requires only: a source checkout, installed dependencies, and the existing
rem .venv / node / rust toolchain. It rebuilds the whole chain itself; it never
rem requires a hand-run build-runtime.cmd first.
setlocal
pushd "%~dp0.."

set "SIDECAR=apps\desktop\src-tauri\binaries\canx-runtime-x86_64-pc-windows-msvc.exe"
set "RUNTIME=build\runtime-dist\canx-runtime.exe"
set "MSI_DIR=apps\desktop\src-tauri\target\release\bundle\msi"

rem Best effort: a stray packaged runtime holds a lock on the runtime exe and
rem makes the PyInstaller rebuild fail with WinError 5. Release it first.
taskkill /F /IM canx-runtime.exe >nul 2>&1

echo [1/6] building packaged Python runtime...
call scripts\build-runtime.cmd
if errorlevel 1 goto :fail

echo [2/6] verifying staged Tauri sidecar...
if not exist "%RUNTIME%" (
  echo   missing packaged runtime: %RUNTIME%
  goto :fail
)
if not exist "%SIDECAR%" (
  echo   missing staged sidecar: %SIDECAR%
  goto :fail
)
echo   ok: %SIDECAR%

echo [3/6] running packaged-runtime smoke test...
set "CANX_TEST_RUNTIME_EXE=%CD%\%RUNTIME%"
".venv\Scripts\python.exe" -m pytest tests\integration\test_packaged_runtime_smoke.py -q
if errorlevel 1 goto :fail

echo [4/6] building Tauri MSI...
call scripts\tauri-bundle.cmd
if errorlevel 1 goto :fail

echo [5/6] verifying MSI artifact...
dir /b "%MSI_DIR%\*.msi" >nul 2>&1
if errorlevel 1 (
  echo   no .msi found under %MSI_DIR%
  goto :fail
)
for /f "delims=" %%F in ('dir /b "%MSI_DIR%\*.msi"') do echo   ok: %%F

echo [6/6] packaging complete.
popd
exit /b 0

:fail
set "CANX_FAIL_CODE=%errorlevel%"
echo.
echo package-windows FAILED (last exit code %CANX_FAIL_CODE%). Stopping; later steps were not run.
popd
exit /b 1
