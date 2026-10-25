@echo off
setlocal
powershell.exe -NoProfile -File "%~dp0tools\bootstrap_install.ps1" %*
exit /b %errorlevel%
