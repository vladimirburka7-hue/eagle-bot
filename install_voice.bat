@echo off
chcp 65001 >nul
set "PY=C:\Users\vladi\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"
echo Устанавливаю библиотеки для музыки (PyNaCl и davey)...
"%PY%" -m pip install PyNaCl davey
echo.
echo Готово. Теперь положите ffmpeg.exe в папку бота и перезапустите бота.
pause
