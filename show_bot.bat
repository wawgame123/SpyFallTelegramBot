@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "BOT_DIR=%~dp0"
powershell -NoProfile -Command "$d=$env:BOT_DIR; $p=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*app.main*' -and $_.CommandLine -like ('*'+$d+'*') }; if($p){exit 1}else{exit 0}"
if not errorlevel 1 (
    echo Бот не запущен. Запустите его через start_bot.bat
    if not "%~1"=="nopause" pause
    exit /b 1
)
type nul > "%~dp0.window_show"
echo Команда отправлена. Окно бота появится через секунду.
timeout /t 2 >nul
