@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo Не найдено виртуальное окружение venv.
    pause
    exit /b 1
)
"venv\Scripts\python.exe" -m scripts.admin_panel
pause
