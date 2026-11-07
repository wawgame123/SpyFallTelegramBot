from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_ONLINE = "Играть онлайн"
BTN_LOCAL = "Один телефон"
BTN_PACKS = "Наборы слов"
BTN_HISTORY = "История игр"
BTN_PROFILE = "Профиль"
BTN_RATING = "Рейтинг"
BTN_ROOM = "Моя комната"
BTN_SETTINGS = "Настройки"
BTN_ADMIN = "Админ"

ALL_BUTTONS = {
    BTN_ONLINE,
    BTN_LOCAL,
    BTN_PACKS,
    BTN_HISTORY,
    BTN_PROFILE,
    BTN_RATING,
    BTN_ROOM,
    BTN_SETTINGS,
    BTN_ADMIN,
}


def main_reply_keyboard(admin: bool = False) -> ReplyKeyboardMarkup:
    last_row = [BTN_ROOM, BTN_SETTINGS] + ([BTN_ADMIN] if admin else [])
    rows = [
        [BTN_ONLINE, BTN_LOCAL],
        [BTN_PACKS, BTN_HISTORY],
        [BTN_PROFILE, BTN_RATING],
        last_row,
    ]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Сообщение",
    )
