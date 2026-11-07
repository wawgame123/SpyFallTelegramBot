from app.database.worker import run_db
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from app.bot.tracker import ScreenTracker

TRACKED_PREFIXES = ("pk:", "hist:", "stat:", "set:")
TRACKED_EXACT = {"menu:online", "online:testmenu", "online:join", "menu:rules"}
PANEL_EXACT = {"online:create", "online:resume", "menu:local", "menu:resume"}
PANEL_PREFIXES = ("online:test:",)


class TrackScreensMiddleware(BaseMiddleware):
    def __init__(self, tracker: ScreenTracker):
        self.tracker = tracker

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not isinstance(event, CallbackQuery) or user is None or event.message is None:
            return await handler(event, data)
        payload = event.data or ""
        message_id = event.message.message_id
        current = self.tracker.get(user.id)
        if payload.startswith("set:") and current is not None and current != message_id:
            from app.bot.ui import safe_answer
            await safe_answer(event, "Настройки обновлены. Используйте новые кнопки.")
            return None
        navigation = payload.startswith(TRACKED_PREFIXES) or payload in TRACKED_EXACT or payload in PANEL_EXACT or payload.startswith(PANEL_PREFIXES) or payload == "menu:home"
        # Actual game callbacks cannot adopt a menu screen. Avoid querying the
        # databases again before and after every game action.
        if not navigation:
            return await handler(event, data)
        repo = data.get("repo")
        runtime = data.get("runtime")
        local = (await run_db(repo.load, user.id)) if repo else None
        protected = bool(local and local.get("message_id") == message_id)
        if not protected and runtime:
            protected = (await run_db(runtime.service.panel_id_of, user.id)) == message_id
        if protected:
            from app.bot.ui import safe_answer
            await safe_answer(event, "Это игровой экран. Откройте раздел через меню.", show_alert=True)
            return None
        previous_screen = self.tracker.get(user.id)
        result = await handler(event, data)
        # Only creation/resume callbacks can promote this message to a panel.
        if payload in PANEL_EXACT or payload.startswith(PANEL_PREFIXES):
            if payload in ("menu:local", "menu:resume"):
                local = (await run_db(repo.load, user.id)) if repo else None
                adopted = bool(local and local.get("message_id") == message_id)
            else:
                adopted = bool(runtime and (await run_db(runtime.service.panel_id_of, user.id)) == message_id)
            if adopted:
                self.tracker.discard(user.id, message_id)
        elif payload.startswith(TRACKED_PREFIXES) or payload in TRACKED_EXACT:
            if self.tracker.get(user.id) == previous_screen:
                self.tracker.set(user.id, message_id)
        return result
