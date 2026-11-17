@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
echo This optional step compares 138 files and can download online-only files.
echo Run the search app first and wait until initial file scanning finishes.
pause
"runtime\python.exe" -X utf8 -B "app\import_verified_notes.py"
pause
