import ctypes
import os
import sys
import threading
import time
from pathlib import Path

SHOW_FLAG = Path(__file__).resolve().parents[2] / ".window_show"
SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9


def parse_command(line: str) -> str | None:
    command = line.strip().lower()
    if command == "inv":
        return "hide"
    return None


def _hwnd() -> int:
    if os.name != "nt":
        return 0
    return ctypes.windll.kernel32.GetConsoleWindow()


def hide_console() -> bool:
    handle = _hwnd()
    if not handle:
        return False
    ctypes.windll.user32.ShowWindow(handle, SW_HIDE)
    return True


def show_console() -> bool:
    handle = _hwnd()
    if not handle:
        return False
    ctypes.windll.user32.ShowWindow(handle, SW_SHOW)
    ctypes.windll.user32.ShowWindow(handle, SW_RESTORE)
    ctypes.windll.user32.SetForegroundWindow(handle)
    return True


def _read_input() -> None:
    while True:
        try:
            line = sys.stdin.readline()
        except (OSError, ValueError):
            return
        if not line:
            return
        if parse_command(line) == "hide":
            print("Окно скрыто. Чтобы вернуть его, запустите show_bot.bat")
            time.sleep(0.3)
            hide_console()


def _watch_flag() -> None:
    while True:
        time.sleep(1)
        if SHOW_FLAG.exists():
            try:
                SHOW_FLAG.unlink()
            except OSError:
                pass
            show_console()


def start_console_control() -> bool:
    if os.name != "nt" or not _hwnd():
        return False
    try:
        SHOW_FLAG.unlink(missing_ok=True)
    except OSError:
        pass
    threading.Thread(target=_read_input, daemon=True).start()
    threading.Thread(target=_watch_flag, daemon=True).start()
    print("Бот запущен. Введите inv и нажмите Enter, чтобы скрыть это окно.")
    print("Вернуть окно: show_bot.bat")
    return True
