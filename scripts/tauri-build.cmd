@echo off
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b %errorlevel%
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
pushd "%~dp0.."
pnpm --dir apps\desktop tauri build --no-bundle
set "result=%errorlevel%"
popd
exit /b %result%
