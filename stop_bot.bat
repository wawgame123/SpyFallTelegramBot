@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "BOT_DIR=%~dp0"
powershell -NoProfile -Command "$d=$env:BOT_DIR; $p=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*app.main*' -and $_.CommandLine -like ('*'+$d+'*') }; if($p){ $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; exit 0 } else { exit 2 }"
set "RESULT=%errorlevel%"
taskkill /FI "WINDOWTITLE eq Spy Game Bot*" /F >nul 2>&1
if "%RESULT%"=="0" (
    echo Бот остановлен.
) else (
    echo Бот не запущен.
)
if not "%~1"=="nopause" pause
