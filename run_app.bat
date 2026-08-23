@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "src\app.py"
) else (
    py -3.13 "src\app.py"
)

