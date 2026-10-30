@echo off
setlocal
powershell.exe -NoProfile -File "%~dp0tools\bootstrap_boundary.ps1" -ToolRoot "%~dp0."
if errorlevel 1 exit /b 2
if not exist "%~dp0.local\mcp-venv\Scripts\python.exe" (
  echo Please run install.cmd first.
  exit /b 2
)
"%~dp0.local\mcp-venv\Scripts\python.exe" -B -X utf8 -I "%~dp0tools\release_update.py" %*
exit /b %errorlevel%
