@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The Python environment is missing. Follow the Setup section in README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\launch.py
if errorlevel 1 pause
