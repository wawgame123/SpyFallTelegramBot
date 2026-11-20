from app.database.worker import run_db
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot import stats_view
from app.bot.ui import safe_answer
from app.bot.keyboards.render import to_plain_markup
from app.game.session import Screen
from app.services.stats_service import PERIOD_DAYS, SORTS, StatsService

router = Router()


async def _edit(call: CallbackQuery, screen: Screen) -> None:
    try:
        await call.message.edit_text(screen.text, reply_markup=to_plain_markup(screen))
    except TelegramBadRequest as err:
        if "not modified" not in str(err):
            raise
    await safe_answer(call)


@router.callback_query(F.data.startswith("stat:rating:"))
async def on_rating(call: CallbackQuery, stats: StatsService) -> None:
    parts = call.data.split(":")
    sort = parts[2]
    period = parts[3] if len(parts) > 3 and parts[3] in PERIOD_DAYS else "all"
    if sort not in SORTS:
        sort = "points"
    uid = call.from_user.id
    rows, rank = await run_db(stats.rating, sort, period, uid)
    await _edit(call, stats_view.rating_screen(rows, sort, uid, period, rank))


@router.callback_query(F.data == "stat:profile")
async def on_profile(call: CallbackQuery, stats: StatsService) -> None:
    name = call.from_user.first_name or call.from_user.username or "Игрок"
    await _edit(call, stats_view.profile_screen((await run_db(stats.profile, call.from_user.id)), name))
