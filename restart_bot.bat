@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Остановка бота...
call "%~dp0stop_bot.bat" nopause
timeout /t 2 >nul
if exist "venv\Scripts\python.exe" (
    echo Обновление зависимостей...
    "venv\Scripts\python.exe" -m pip install -q -r requirements.txt
)
echo Запуск бота...
call "%~dp0start_bot.bat" nopause
if errorlevel 1 pause
