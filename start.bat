@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\vladi\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"
:loop
"%PY%" bot.py
echo.
echo Бот остановился. Перезапуск через 30 секунд. Закройте окно, чтобы выйти.
timeout /t 30 >nul
goto loop
