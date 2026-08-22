@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_exe.ps1"
set "RSVIEWER_BUILD_EXIT=%ERRORLEVEL%"

echo.
if "%RSVIEWER_BUILD_EXIT%"=="0" (
    echo Build finished: %~dp0dist\RSViewer.exe
) else (
    echo Build failed with exit code %RSVIEWER_BUILD_EXIT%.
)
echo.
pause
exit /b %RSVIEWER_BUILD_EXIT%
