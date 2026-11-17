@echo off
setlocal
cd /d "%~dp0"
if exist "dist\AuroraMonitor.exe" (
  start "" "dist\AuroraMonitor.exe"
  exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "main.py"
  exit /b 0
)
echo Python environment missing. See README.md
pause
exit /b 1
