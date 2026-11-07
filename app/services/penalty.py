LEAVE_BAN_MINUTES = [5, 30, 120, 720, 1440]
STRIKE_RESET_SECONDS = 7 * 24 * 3600
LEAVE_CONFIRM_SECONDS = 10


def next_penalty(strikes: int, last_leave_at: float, now: float) -> tuple[int, int]:
    if strikes > 0 and now - last_leave_at > STRIKE_RESET_SECONDS:
        strikes = 0
    new_strikes = strikes + 1
    minutes = LEAVE_BAN_MINUTES[min(new_strikes, len(LEAVE_BAN_MINUTES)) - 1]
    return new_strikes, minutes


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds + 0.999))
    minutes = -(-total // 60)
    if minutes < 60:
        return f"{max(minutes, 1)} мин"
    hours, rest = divmod(minutes, 60)
    return f"{hours} ч {rest} мин" if rest else f"{hours} ч"


PERMANENT_UNTIL = 4102444800.0
PERMANENT_THRESHOLD_SECONDS = 3650 * 24 * 3600


def describe_ban(seconds: float) -> str:
    if seconds > PERMANENT_THRESHOLD_SECONDS:
        return "навсегда"
    return format_duration(seconds)


def ban_message(seconds: float, reason: str) -> str:
    left = describe_ban(seconds)
    if reason == "admin":
        if left == "навсегда":
            return "Вы заблокированы администратором навсегда."
        return f"Вы заблокированы администратором. Осталось: {left}."
    return f"Вы заблокированы за выход из игры во время раунда. Осталось: {left}."
