@echo off
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b %errorlevel%
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
set "CANX_TEST_PYTHON=%~dp0..\.venv\Scripts\python.exe"
pushd "%~dp0..\apps\desktop\src-tauri"
cargo fmt
if errorlevel 1 exit /b %errorlevel%
cargo fmt --check
if errorlevel 1 exit /b %errorlevel%
cargo clippy -- -D warnings
if errorlevel 1 exit /b %errorlevel%
cargo test
set "result=%errorlevel%"
popd
exit /b %result%
