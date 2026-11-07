from app.database.worker import run_db
import re
from collections.abc import Callable

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

SELECTED = "[x] "
UNSELECTED = "[ ] "

LABEL_EMOJI = {
    "Играть онлайн": "🎮",
    "Один телефон": "📱",
    "Наборы слов": "📚",
    "История игр": "📜",
    "Профиль": "👤",
    "Рейтинг": "🏆",
    "Моя комната": "🚪",
    "Настройки": "⚙️",
    "Часовой пояс": "🌐",
    "Админ": "🛠",
    "Закрыть": "✖️",
    "Назад": "⬅️",
    "Вперёд": "➡️",
    "Отмена": "✖️",
    "Создать комнату": "➕",
    "Войти по коду": "🔑",
    "Вернуться в комнату": "↩️",
    "Тестовые лобби": "🧪",
    "Тестовое лобби на 1 игрока": "🧪",
    "Тестовое лобби на 2 игроков": "🧪",
    "Жалобы на наборы": "🚩",
    "Продолжить игру": "▶️",
    "Начать новую": "🆕",
    "Начать игру": "▶️",
    "Начать раунд": "▶️",
    "Начать обсуждение": "▶️",
    "Следующий раунд": "▶️",
    "Готов": "✅",
    "Не готов": "❌",
    "Пригласить": "📨",
    "Скопировать ссылку": "📋",
    "Выйти": "🚪",
    "Показать роль": "👁",
    "Показать": "👁",
    "Моя роль": "🎭",
    "Закончить ход": "✅",
    "Закончить ход и предложить голосование": "🗳",
    "Передать ход": "➡️",
    "Передать ход и предложить голосование": "🗳",
    "Пропустить ход игрока": "⏭",
    "Дальше": "➡️",
    "Голосовать": "🗳",
    "Угадать слово": "🕵️",
    "Не угадывать": "🚫",
    "Да": "✅",
    "Да, выйти": "🚪",
    "Другое слово": "✏️",
    "Изменить настройки": "⚙️",
    "Завершить игру": "🏁",
    "Темы": "🎯",
    "Темы: выбрать": "🎯",
    "Количество": "👥",
    "Выбрать все": "✅",
    "Очистить": "🧹",
    "Готово": "✔️",
    "Готово, назад в лобби": "✔️",
    "Правила игры": "📖",
    "Мои наборы": "📁",
    "Готовые наборы": "📚",
    "Избранное": "⭐",
    "Публичная библиотека": "🌐",
    "Создать набор": "🧩",
    "Импорт набора": "📥",
    "К разделам": "🗂",
    "Поиск": "🔎",
    "Слова": "🔤",
    "Добавить слова": "➕",
    "Переименовать": "✏️",
    "Сделать публичным": "🌐",
    "Сделать личным": "🔒",
    "Копировать": "📋",
    "Экспорт": "📤",
    "В избранное": "⭐",
    "Убрать из избранного": "☆",
    "Удалить набор": "🗑",
    "Да, удалить": "🗑",
    "Удалить": "🗑",
    "Изменить": "✏️",
    "Пожаловаться": "🚩",
    "К набору": "↩️",
    "Категория": "🗂",
    "Все категории": "🗂",
    "Сбросить фильтры": "🧹",
    "Оставить и снять жалобы": "✅",
    "Скрыть": "🙈",
    "Мои игры": "🎮",
    "История лобби": "📜",
    "Игры этого лобби": "🎲",
    "К списку лобби": "↩️",
    "К лобби": "↩️",
    "По очкам": "🥇",
    "По победам": "🏅",
    "Победы за шпиона": "🕵️",
    "Процент побед": "📊",
    "В меню": "🏠",
}

PREFIX_EMOJI = {
    "Сортировка:": "↕️",
    "Порядок тем:": "🔀",
    "Моя роль:": "🎭",
    "Эмодзи на кнопках:": "🙂",
    "Эмодзи:": "🙂",
    "Цветные кнопки:": "🎨",
    "Раундов в игре": "🔁",
    "Секунд на ход": "⏱",
    "Игроков в комнате": "👥",
}

THEME_EMOJI = {
    "Животные": "🐾",
    "Еда и напитки": "🍕",
    "Города и страны": "🌍",
    "Фильмы и сериалы": "🎬",
    "Видеоигры": "🎮",
    "Технологии": "💻",
    "Профессии": "👷",
    "Спорт": "⚽",
}

SUCCESS = {
    "Начать игру", "Начать раунд", "Следующий раунд", "Да", "Готов", "Создать комнату",
    "Создать набор", "Показать роль", "Показать", "Голосовать", "Закончить ход",
    "Закончить ход и предложить голосование", "Готово", "Готово, назад в лобби",
    "Продолжить игру", "Играть онлайн", "Начать обсуждение",
}
DANGER = {
    "Выйти", "Удалить набор", "Да, удалить", "Пожаловаться", "Скрыть", "Удалить",
    "Завершить игру", "Не готов", "Не угадывать",
}
PRIMARY = {
    "Один телефон", "Наборы слов", "Моя комната", "Угадать слово", "Моя роль", "Настройки",
    "Пригласить", "Скопировать ссылку", "Войти по коду", "Поиск", "Рейтинг", "Профиль", "История игр", "Админ",
    "Начать новую", "Передать ход и предложить голосование",
}

_LEADING = re.compile(r"^[^\w\[(]+")
_COUNT = re.compile(r"\s*\(\d+\)\s*$")


def split_marker(label: str) -> tuple[str | None, str]:
    if label.startswith(SELECTED):
        return "on", label[len(SELECTED) :]
    if label.startswith(UNSELECTED):
        return "off", label[len(UNSELECTED) :]
    return None, label


def strip_decor(text: str) -> str:
    return _LEADING.sub("", text).strip()


def emoji_for(label: str) -> str | None:
    if label in LABEL_EMOJI:
        return LABEL_EMOJI[label]
    for prefix, emoji in PREFIX_EMOJI.items():
        if label.startswith(prefix):
            return emoji
    title = _COUNT.sub("", label)
    for name, emoji in THEME_EMOJI.items():
        if title == name:
            return emoji
    return None


def decorate_text(label: str) -> str:
    marker, rest = split_marker(label)
    if marker == "on":
        return f"🟢 {rest}"
    if marker == "off":
        return f"⚪ {rest}"
    emoji = emoji_for(rest)
    return f"{emoji} {rest}" if emoji else label


def style_for(label: str) -> str | None:
    marker, rest = split_marker(label)
    if marker == "on":
        return "primary"
    key = rest
    if key in DANGER:
        return "danger"
    if key in SUCCESS:
        return "success"
    if key in PRIMARY:
        return "primary"
    return None


def decorate_markup(markup, emoji: bool, colors: bool):
    def apply(button, cls):
        data = button.model_dump(exclude_none=True)
        label = data["text"]
        if colors:
            style = style_for(label)
            if style:
                data["style"] = style
        if emoji:
            data["text"] = decorate_text(label)
        return cls(**data)

    if isinstance(markup, InlineKeyboardMarkup):
        return InlineKeyboardMarkup(
            inline_keyboard=[[apply(b, InlineKeyboardButton) for b in row] for row in markup.inline_keyboard]
        )
    if isinstance(markup, ReplyKeyboardMarkup):
        data = markup.model_dump(exclude_none=True)
        data["keyboard"] = [[apply(b, KeyboardButton) for b in row] for row in markup.keyboard]
        return ReplyKeyboardMarkup(**data)
    return markup


class ButtonStyleMiddleware(BaseRequestMiddleware):
    def __init__(self, prefs_for: Callable[[int], object]):
        self._prefs_for = prefs_for

    async def __call__(self, make_request, bot, method):
        markup = getattr(method, "reply_markup", None)
        chat_id = getattr(method, "chat_id", None)
        if isinstance(chat_id, int) and isinstance(markup, (InlineKeyboardMarkup, ReplyKeyboardMarkup)):
            prefs = (await run_db(self._prefs_for, chat_id))
            if prefs.emoji or prefs.colors:
                method = method.model_copy(
                    update={"reply_markup": decorate_markup(markup, prefs.emoji, prefs.colors)}
                )
        return await make_request(bot, method)
