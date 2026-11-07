from app.database.worker import run_db
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot import stats_view
from app.bot.ui import safe_answer
from app.bot.keyboards.render import to_plain_markup
from app.game.session import Screen
from app.services.stats_service import SORTS, StatsService

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
    sort = call.data.split(":")[2]
    if sort not in SORTS:
        sort = "points"
    await _edit(call, stats_view.rating_screen((await run_db(stats.leaderboard, sort)), sort, call.from_user.id))


@router.callback_query(F.data == "stat:profile")
async def on_profile(call: CallbackQuery, stats: StatsService) -> None:
    name = call.from_user.first_name or call.from_user.username or "Игрок"
    await _edit(call, stats_view.profile_screen((await run_db(stats.profile, call.from_user.id)), name))
