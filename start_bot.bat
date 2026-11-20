@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "BOT_DIR=%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo Не найдено виртуальное окружение venv.
    echo Создайте его командой: python -m venv venv
    echo Затем установите зависимости: venv\Scripts\pip install -r requirements.txt
    if not "%~1"=="nopause" pause
    exit /b 1
)
if not exist ".env" (
    echo Не найден файл .env. Скопируйте .env.example в .env и впишите BOT_TOKEN.
    if not "%~1"=="nopause" pause
    exit /b 1
)
powershell -NoProfile -Command "$d=$env:BOT_DIR; $p=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*app.main*' -and $_.CommandLine -like ('*'+$d+'*') }; if($p){exit 1}else{exit 0}"
if errorlevel 1 (
    echo Бот уже запущен. Для перезапуска используйте restart_bot.bat
    if not "%~1"=="nopause" pause
    exit /b 1
)
start "Spy Game Bot" conhost.exe cmd /k ""%~dp0venv\Scripts\python.exe" -m app.main"
echo Бот запущен в отдельном окне "Spy Game Bot".
echo В окне бота введите inv и нажмите Enter, чтобы скрыть его. Вернуть окно: show_bot.bat
timeout /t 3 >nul
