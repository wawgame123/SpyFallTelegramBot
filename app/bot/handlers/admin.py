from app.database.worker import run_db
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards.menu import back_menu
from app.bot.tracker import ScreenTracker
from app.bot.ui import present, safe_answer
from app.core.config import Settings
from app.core.security import is_admin
from app.services.admin_service import AdminError, AdminService, parse_duration
from app.services.penalty import describe_ban

router = Router()

BAN_USAGE = "Использование: /ban ID [срок]. Срок: 30 (минут), 2ч, 7д или навсегда. По умолчанию 1д."
UNBAN_USAGE = "Использование: /unban ID [сброс]. Слово «сброс» обнуляет счётчик нарушений."
DELPACK_USAGE = "Использование: /delpack НОМЕР. Номера наборов показывает команда /packs."


def _admin(settings: Settings, user) -> bool:
    return is_admin(settings, user.id, user.username)


async def _cleanup(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


async def _reply(message: Message, tracker: ScreenTracker, text: str, markup=None) -> None:
    await _cleanup(message)
    await present(message, tracker, text, markup or back_menu())


def _args(command: CommandObject) -> list[str]:
    return (command.args or "").split()


@router.message(Command("ban"), F.chat.type == "private")
async def on_ban(
    message: Message,
    command: CommandObject,
    settings: Settings,
    admin: AdminService,
    tracker: ScreenTracker,
) -> None:
    if not _admin(settings, message.from_user):
        return
    args = _args(command)
    if not args or not args[0].isdigit():
        await _reply(message, tracker, BAN_USAGE)
        return
    target = int(args[0])
    if target == message.from_user.id or target == settings.admin_id:
        await _reply(message, tracker, "Нельзя заблокировать администратора.")
        return
    try:
        minutes = parse_duration(args[1]) if len(args) > 1 else 1440
        until = (await run_db(admin.ban, target, minutes))
    except (ValueError, AdminError) as err:
        await _reply(message, tracker, f"{err}\n\n{BAN_USAGE}")
        return
    label = "навсегда" if minutes is None else f"на {args[1] if len(args) > 1 else '1д'}"
    await _reply(message, tracker, f"Игрок {target} заблокирован {label}.")


@router.message(Command("unban"), F.chat.type == "private")
async def on_unban(
    message: Message,
    command: CommandObject,
    settings: Settings,
    admin: AdminService,
    tracker: ScreenTracker,
) -> None:
    if not _admin(settings, message.from_user):
        return
    args = _args(command)
    if not args or not args[0].isdigit():
        await _reply(message, tracker, UNBAN_USAGE)
        return
    reset = len(args) > 1 and args[1].lower() in ("сброс", "reset")
    changed = (await run_db(admin.unban, int(args[0]), reset_strikes=reset))
    if changed:
        text = f"Игрок {args[0]} разблокирован" + (", счётчик нарушений обнулён." if reset else ".")
    else:
        text = "У этого игрока нет блокировки."
    await _reply(message, tracker, text)


@router.message(Command("bans"), F.chat.type == "private")
async def on_bans(
    message: Message, settings: Settings, admin: AdminService, tracker: ScreenTracker
) -> None:
    if not _admin(settings, message.from_user):
        return
    rows = (await run_db(admin.list_bans))
    if not rows:
        await _reply(message, tracker, "Активных блокировок нет.")
        return
    lines = [
        f"{r.user_id} ({r.name}): {r.left_text}, причина: {r.reason_text}, нарушений: {r.strikes}"
        for r in rows[:30]
    ]
    await _reply(message, tracker, "Блокировки:\n" + "\n".join(lines))


@router.message(Command("packs"), F.chat.type == "private")
async def on_packs(
    message: Message,
    command: CommandObject,
    settings: Settings,
    admin: AdminService,
    tracker: ScreenTracker,
) -> None:
    if not _admin(settings, message.from_user):
        return
    packs, total = (await run_db(admin.list_packs, command.args, limit=15))
    if not packs:
        await _reply(message, tracker, "Наборы не найдены.")
        return
    text = "Наборы (номер: название):\n" + "\n".join(p.line() for p in packs)
    if total > len(packs):
        text += f"\n\nПоказано {len(packs)} из {total}. Уточните поиск: /packs текст"
    await _reply(message, tracker, text + "\n\nУдалить: /delpack НОМЕР")


@router.message(Command("delpack"), F.chat.type == "private")
async def on_delpack(
    message: Message,
    command: CommandObject,
    settings: Settings,
    admin: AdminService,
    tracker: ScreenTracker,
) -> None:
    if not _admin(settings, message.from_user):
        return
    args = _args(command)
    if not args or not args[0].isdigit():
        await _reply(message, tracker, DELPACK_USAGE)
        return
    try:
        pack = (await run_db(admin.get_pack, int(args[0])))
    except AdminError as err:
        await _reply(message, tracker, str(err))
        return
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да, удалить", callback_data=f"adm:del:{pack.id}")],
            [InlineKeyboardButton(text="Отмена", callback_data="menu:home")],
        ]
    )
    await _reply(
        message,
        tracker,
        f"Удалить набор?\n\n{pack.line()}\n\nЭто действие нельзя отменить.",
        markup,
    )


@router.callback_query(F.data.startswith("adm:del:"))
async def on_delpack_confirm(call: CallbackQuery, settings: Settings, admin: AdminService) -> None:
    if not _admin(settings, call.from_user):
        await safe_answer(call, "Недоступно", show_alert=True)
        return
    raw = call.data.rsplit(":", 1)[1]
    if not raw.isdigit():
        await safe_answer(call)
        return
    try:
        pack = (await run_db(admin.delete_pack, int(raw)))
    except AdminError as err:
        await safe_answer(call, str(err), show_alert=True)
        return
    try:
        await call.message.edit_text(f"Набор «{pack.title}» удалён.", reply_markup=back_menu())
    except TelegramBadRequest:
        pass
    await safe_answer(call, "Удалено")
