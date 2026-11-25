from app.models.database.worker import run_db
import asyncio
import logging
import time
from collections import OrderedDict
from weakref import WeakValueDictionary

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message

from app.views.telegram.ui import delete_quietly
from app.controllers.telegram.confirmations import LeaveConfirmations
from app.models.game.room import COUNTDOWN_INTERVAL, Room
from app.models.game.session import Screen
from app.models.services.room_service import RoomService

log = logging.getLogger(__name__)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def to_room_markup(screen: Screen) -> InlineKeyboardMarkup:
    rows = []
    for row in screen.rows:
        buttons = []
        for label, action in row:
            if action.startswith("url:"):
                buttons.append(InlineKeyboardButton(text=label, url=action[4:]))
            elif action.startswith("copy:"):
                buttons.append(
                    InlineKeyboardButton(
                        text=label,
                        copy_text=CopyTextButton(text=action[5:]),
                    )
                )
            else:
                buttons.append(InlineKeyboardButton(text=label, callback_data=f"rm:{action}"))
        rows.append(buttons)
    return InlineKeyboardMarkup(inline_keyboard=rows)


class OnlineRuntime:
    def __init__(self, bot: Bot, service: RoomService, bot_username: str, maintenance=(), prefs_for=None):
        self.bot = bot
        self.service = service
        self.bot_username = bot_username
        self._locks = WeakValueDictionary()
        self._delivery_locks = WeakValueDictionary()
        self._deliveries: dict[int, asyncio.Task] = {}
        self._dirty_rooms: set[int] = set()
        self._edit_task: asyncio.Task | None = None
        self._last_refresh: dict[int, float] = {}
        self._maintenance = list(maintenance)
        self._prefs_for = prefs_for
        self._render_slots = asyncio.Semaphore(4)
        self._panel_cache: dict[int, tuple[tuple, float]] = {}
        self._retry_after: dict[int, float] = {}
        self._pending_rooms: dict[int, int] = {}
        self.last_cycle = time.monotonic()
        self.edit_recovery = None
        self.on_room_change = None
        self.web_url = None
        self.local_web_auth = None
        self.cache_ttl = 60.0
        self.leave_confirmations = LeaveConfirmations()
        self._last_chat = OrderedDict()
        self._chat_warnings: dict[int, asyncio.Task | None] = {}

    def allow_chat_message(self, user_id, interval=1.0):
        now = time.monotonic()
        previous = self._last_chat.get(user_id)
        if previous is not None and now - previous < interval:
            return False
        self._last_chat.pop(user_id, None)
        if len(self._last_chat) >= 4096:
            self._last_chat.popitem(last=False)
        self._last_chat[user_id] = now
        return True

    async def warn_chat(self, message: Message, text: str) -> None:
        uid = message.from_user.id
        if uid in self._chat_warnings or len(self._chat_warnings) >= 4096:
            return
        self._chat_warnings[uid] = None
        try:
            warning = await message.answer(f"Ошибка: {text}")
        except BaseException:
            self._chat_warnings.pop(uid, None)
            raise
        self._chat_warnings[uid] = asyncio.create_task(self._expire_warning(uid, warning))

    async def _expire_warning(self, uid: int, message: Message) -> None:
        try:
            await asyncio.sleep(4)
        finally:
            try:
                await delete_quietly(self.bot, message.chat.id, message.message_id)
            finally:
                self._chat_warnings.pop(uid, None)

    def _defer_render(self, uid, seconds, room_id):
        if uid not in self._retry_after and len(self._retry_after) >= 4096:
            oldest = next(iter(self._retry_after))
            self._retry_after.pop(oldest)
            self._pending_rooms.pop(oldest, None)
        self._retry_after[uid] = time.monotonic() + max(1.0, seconds)
        self._pending_rooms[uid] = room_id

    def _remember(self, uid: int, signature: tuple) -> None:
        if uid not in self._panel_cache and len(self._panel_cache) >= 4096:
            self._panel_cache.pop(next(iter(self._panel_cache)))
        self._panel_cache[uid] = (signature, time.monotonic())
        self._retry_after.pop(uid, None)
        self._pending_rooms.pop(uid, None)

    def lock(self, room_id: int) -> asyncio.Lock:
        return self._locks.setdefault(room_id, asyncio.Lock())

    def _delivery_lock(self, room_id: int) -> asyncio.Lock:
        return self._delivery_locks.setdefault(room_id, asyncio.Lock())

    def in_room(self, user_id: int) -> bool:
        return self.service.room_id_of(user_id) is not None

    async def adopt_panel(self, room_id: int, user_id: int, message_id: int) -> None:
        async with self._delivery_lock(room_id):
            async with self.lock(room_id):
                adopted, old = await run_db(
                    self.service.set_panel, room_id, user_id, message_id, player_activity=True,
                )
            if not adopted:
                await delete_quietly(self.bot, user_id, message_id)
                return
            self._panel_cache.pop(user_id, None)
            self._retry_after.pop(user_id, None)
            self._pending_rooms.pop(user_id, None)
            if old and old != message_id:
                await delete_quietly(self.bot, user_id, old)

    def notify_room(self, room_id: int) -> None:
        if self.on_room_change is not None:
            self.on_room_change(room_id)

    async def refresh(self, room_id: int, *, retry=False, notify=True) -> None:
        # Serialize delivery so an older edit cannot arrive after a newer one.
        # The game-state lock is free while Telegram is responding.
        if notify:
            self.notify_room(room_id)
        async with self._delivery_lock(room_id):
            room = await run_db(self.service.get, room_id, for_render=True)
            if retry:
                members = {p["id"] for p in room.players} if room else set()
                for uid, rid in list(self._pending_rooms.items()):
                    if rid == room_id and uid not in members:
                        self._pending_rooms.pop(uid, None)
                        self._retry_after.pop(uid, None)
                        self._panel_cache.pop(uid, None)
            if room is None:
                return
            self._last_refresh[room_id] = time.monotonic()
            now = time.time()

            async def render(player):
                async with self._render_slots:
                    await self._render(room_id, room, player, now)

            results = await asyncio.gather(
                *(render(player) for player in room.players if player.get("telegram_panel", True)),
                return_exceptions=True,
            )
            # Finish all edits before permitting a newer refresh, even when a
            # particular panel fails to render.
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    def schedule_refresh(self, room_id: int) -> None:
        if room_id in self._deliveries:
            self._dirty_rooms.add(room_id)
            return
        self._deliveries[room_id] = asyncio.create_task(self._run_delivery(room_id))

    async def _run_delivery(self, room_id: int) -> None:
        try:
            while True:
                self._dirty_rooms.discard(room_id)
                await self.refresh(room_id, retry=True, notify=False)
                if room_id not in self._dirty_rooms:
                    return
        except Exception:
            log.exception("Не удалось обновить комнату %s", room_id)
        finally:
            self._deliveries.pop(room_id, None)
            self._dirty_rooms.discard(room_id)

    async def close(self) -> None:
        tasks = list(self._deliveries.values())
        tasks.extend(task for task in self._chat_warnings.values() if task is not None)
        if self._edit_task is not None:
            tasks.append(self._edit_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _retry_menu_edits(self) -> None:
        try:
            await self.edit_recovery.retry_pending(self.bot)
        except Exception:
            log.exception("Не удалось повторить обновление меню")

    async def _render(self, room_id: int, room: Room, player: dict, now: float) -> None:
        uid = player["id"]
        current = time.monotonic()
        if self._pending_rooms.get(uid) == room_id and self._retry_after.get(uid, 0) > current:
            return
        screen = room.view_for(uid, self.bot_username, now)
        markup = to_room_markup(screen)
        styles = None
        if self._prefs_for is not None:
            prefs = (await run_db(self._prefs_for, uid))
            styles = (prefs.emoji, prefs.colors)
        markup_json = markup.model_dump_json(exclude_none=True)
        signature = (room_id, player.get("panel"), screen.text, markup_json, styles)
        cached = self._panel_cache.get(uid)
        if cached and cached[0] == signature and current - cached[1] < self.cache_ttl:
            return
        if player.get("panel"):
            try:
                await self.bot.edit_message_text(
                    screen.text,
                    chat_id=uid,
                    message_id=player["panel"],
                    reply_markup=markup,
                    parse_mode="HTML",
                    link_preview_options=NO_PREVIEW,
                )
                self._remember(uid, signature)
                return
            except TelegramBadRequest as err:
                reason = str(err).lower()
                if "not modified" in reason:
                    self._remember(uid, signature)
                    return
                if not any(marker in reason for marker in ("message to edit not found", "message can't be edited")):
                    self._retry_after.pop(uid, None)
                    self._pending_rooms.pop(uid, None)
                    log.warning("Panel %s edit rejected: %s", uid, err)
                    return
            except TelegramRetryAfter as err:
                self._defer_render(uid, err.retry_after, room_id)
                log.warning("Panel %s rate limited for %s seconds", uid, err.retry_after)
                return
            except TelegramForbiddenError:
                self._retry_after.pop(uid, None)
                self._pending_rooms.pop(uid, None)
                return
            except TelegramNetworkError as err:
                self._defer_render(uid, 5, room_id)
                log.warning("Сеть: не удалось обновить панель %s: %s", uid, err)
                return
            except TelegramAPIError:
                self._defer_render(uid, 10, room_id)
                log.exception("edit failed")
                return
        try:
            sent = await self.bot.send_message(
                uid,
                screen.text,
                reply_markup=markup,
                parse_mode="HTML",
                link_preview_options=NO_PREVIEW,
            )
            async with self.lock(room_id):
                adopted, _ = await run_db(
                    self.service.set_panel, room_id, uid, sent.message_id,
                    expected_panel_id=player.get("panel"), expected_room_code=room.code,
                )
            if not adopted:
                await delete_quietly(self.bot, uid, sent.message_id)
                return
            self._remember(uid, (room_id, sent.message_id, screen.text, markup_json, styles))
        except TelegramRetryAfter as err:
            self._defer_render(uid, err.retry_after, room_id)
            log.warning("Panel %s rate limited for %s seconds", uid, err.retry_after)
        except TelegramNetworkError as err:
            self._defer_render(uid, 5, room_id)
            log.warning("Сеть: не удалось отправить панель %s: %s", uid, err)
        except (TelegramForbiddenError, TelegramBadRequest):
            self._retry_after.pop(uid, None)
            self._pending_rooms.pop(uid, None)
        except TelegramAPIError:
            self._defer_render(uid, 10, room_id)
            log.exception("send failed")

    async def retry_pending(self, *, background=False) -> None:
        """Retry even lobby/result screens, always using current membership/state."""
        now = time.monotonic()
        due = {rid for uid, rid in list(self._pending_rooms.items())
               if self._retry_after.get(uid, 0) <= now}
        for room_id in due:
            if background:
                self.schedule_refresh(room_id)
            else:
                await self.refresh(room_id, retry=True, notify=False)

    async def tick(self, *, background=False) -> None:
        for room_id in (await run_db(self.service.due_room_ids)):
            async with self.lock(room_id):
                update = (await run_db(self.service.tick_room, room_id))
            if update and update.changed:
                if background:
                    self.notify_room(room_id)
                    self.schedule_refresh(room_id)
                else:
                    await self.refresh(room_id)

    async def countdown(
        self, now: float | None = None, every: float = COUNTDOWN_INTERVAL, *, background=False,
    ) -> int:
        current = time.monotonic() if now is None else now
        active = (await run_db(self.service.active_room_ids))
        self._last_refresh = {k: v for k, v in self._last_refresh.items() if k in active}
        refreshed = 0
        for room_id in active:
            last = self._last_refresh.get(room_id)
            if last is None or current - last >= every:
                if background:
                    self.schedule_refresh(room_id)
                else:
                    await self.refresh(room_id, notify=False)
                self._last_refresh[room_id] = current
                refreshed += 1
        return refreshed

    async def run_ticker(self) -> None:
        purge_at = 0.0
        while True:
            await asyncio.sleep(COUNTDOWN_INTERVAL / 2)
            try:
                await self.tick(background=True)
                await self.countdown(background=True)
                await self.retry_pending(background=True)
                if self.edit_recovery is not None and (self._edit_task is None or self._edit_task.done()):
                    self._edit_task = asyncio.create_task(self._retry_menu_edits())
                if time.time() >= purge_at:
                    if self.on_room_change is None:
                        await run_db(self.service.purge_stale)
                    else:
                        for room_id in await run_db(self.service.purge_stale, return_ids=True):
                            self.notify_room(room_id)
                    for job in self._maintenance:
                        (await run_db(job))
                    purge_at = time.time() + 600
                self.last_cycle = time.monotonic()
            except Exception:
                log.exception("ticker failed")
