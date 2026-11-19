@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "SRC="
if exist "..\.env" set "SRC=%~dp0.."
if not defined SRC if exist "C:\MoueBot\.env" set "SRC=C:\MoueBot"
if not defined SRC if exist "C:\MoueBot_old\.env" set "SRC=C:\MoueBot_old"
if not defined SRC (
  echo Не нашёл старую папку с файлом .env.
  echo Скопируйте .env и bot.db в эту папку вручную.
  pause
  exit /b 1
)
echo Копирую данные из %SRC%
copy /y "%SRC%\.env" ".env" >nul && echo .env скопирован
if exist "%SRC%\bot.db" (
  copy /y "%SRC%\bot.db" "bot.db" >nul && echo bot.db скопирован
) else (
  echo bot.db не найден, будет создана новая база
)
echo.
echo Готово. Теперь запускайте start.bat
pause
