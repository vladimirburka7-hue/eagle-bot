@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\vladi\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"
echo Проверка связи с Discord. Это займёт до минуты...
"%PY%" netcheck.py
echo.
pause
