@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Папка: %cd%
echo.
set "PY=C:\Users\vladi\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"
echo Python: %PY%
"%PY%" --version
if errorlevel 1 (
  echo.
  echo Python не найден. Проверьте установку Python.
  pause
  exit /b 1
)
echo.
if exist ".env" (echo Файл .env найден) else (echo ВНИМАНИЕ: файла .env нет в этой папке)
if exist "bot.db" (echo Файл bot.db найден) else (echo bot.db нет, будет создана новая база)
if exist "bot.py" (echo Файл bot.py найден) else (echo ВНИМАНИЕ: нет файла bot.py)
echo.
echo Запускаю бота. Если появится ошибка, скопируйте её и пришлите мне.
echo ----------------------------------------
"%PY%" bot.py
echo ----------------------------------------
echo Бот завершился. Код выхода: %errorlevel%
pause
