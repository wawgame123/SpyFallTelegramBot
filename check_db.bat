@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo Не найдено виртуальное окружение venv.
    pause
    exit /b 1
)
"venv\Scripts\python.exe" -m scripts.db_check
pause
