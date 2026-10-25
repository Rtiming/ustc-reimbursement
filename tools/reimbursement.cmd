@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "RTIME_REIMBURSE_ROOT=%~dp0.."
if exist "%RTIME_REIMBURSE_ROOT%\.local\mcp-venv\Scripts\python.exe" (
  "%RTIME_REIMBURSE_ROOT%\.local\mcp-venv\Scripts\python.exe" "%RTIME_REIMBURSE_ROOT%\assistant.py" %*
) else (
  python "%RTIME_REIMBURSE_ROOT%\assistant.py" %*
)
exit /b %errorlevel%
