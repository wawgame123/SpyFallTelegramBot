from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.game.session import Screen


def to_markup(screen: Screen, seq: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=label, callback_data=f"lg:{seq}:{action}")
            for label, action in row
        ]
        for row in screen.rows
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def to_plain_markup(screen: Screen) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=action) for label, action in row]
            for row in screen.rows
            if row
        ]
    )
