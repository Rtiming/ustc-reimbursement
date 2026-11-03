@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "REVIEW_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "REVIEW_PYTHON=.venv\Scripts\python.exe"
"%REVIEW_PYTHON%" review.py doctor --workspace workspace
if errorlevel 1 goto failed
"%REVIEW_PYTHON%" review.py open --workspace workspace --port 0
if errorlevel 1 goto failed
exit /b 0
:failed
echo 请按“使用说明.txt”配置Python及材料依赖。
pause
