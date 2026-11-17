@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist "runtime\python.exe" (
    if exist "%USERPROFILE%\.gemini\Quick Search\runtime\python.exe" (
        cd /d "%USERPROFILE%\.gemini\Quick Search"
    )
)
if not exist "runtime\python.exe" (echo Application files are incomplete. Extract the entire ZIP file. & pause & exit /b 1)
"runtime\python.exe" -X utf8 -B "app\start.py"
if errorlevel 1 (echo Search app could not start. Check the message above. & pause)