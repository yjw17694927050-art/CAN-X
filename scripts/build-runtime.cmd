@echo off
rem Build the packaged headless runtime sidecar and stage it for Tauri bundling.
setlocal
pushd "%~dp0.."
".venv\Scripts\pyinstaller.exe" --noconfirm --clean --distpath build\runtime-dist --workpath build\runtime-work packaging\canx-runtime.spec
if errorlevel 1 (popd & exit /b 1)
if not exist "apps\desktop\src-tauri\binaries" mkdir "apps\desktop\src-tauri\binaries"
copy /y "build\runtime-dist\canx-runtime.exe" "apps\desktop\src-tauri\binaries\canx-runtime-x86_64-pc-windows-msvc.exe" >nul
if errorlevel 1 (popd & exit /b 1)
popd
exit /b 0
