from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.views.telegram.keyboards.reply import ALL_BUTTONS
from app.views.telegram.keyboards.menu import back_menu
from app.views.telegram.history_view import ROLE_TEXT, fmt_dt, outcome_text
from app.views.telegram.tracker import ScreenTracker
from app.views.telegram.ui import present, safe_answer
from app.core.config import Settings
from app.core.security import is_admin
from app.models.database.worker import run_db
from app.models.game.session import Screen
from app.models.services.admin_service import AdminError, AdminService
from app.models.services.history_service import HistoryService
from app.models.services.history_admin_service import HistoryAdminService
from app.models.services.pack_service import PackError, PackService
from app.models.services.penalty import describe_ban
from app.models.services.settings_service import SettingsService

router = Router()


class HistoryEdit(StatesGroup):
    value = State()


def _admin(settings, user):
    return is_admin(settings, user.id, user.username)


def _back(rid):
    return [("К раунду", f"hist:rd:{rid}")]


def _round_screen(detail, tz_minutes, back):
    rid = detail["id"]
    players = "\n".join(
        f"{escape(name)} · {ROLE_TEXT[role]} · <b>{score:+d}</b> оч."
        for _, name, score, role in detail["players"]
    )
    pack = detail["pack_title"] or detail["theme"]
    rows = [[(name[:50], f"hist:player:{rid}:{uid}")] for uid, name, _, _ in detail["players"]]
    rows.insert(0, [(f"Набор: {pack}"[:60], f"hist:pack:{rid}")])
    rows.append([back])
    if back[0] == "К моим играм":
        rows.append([("К раундам лобби", f"hist:lr:{detail['session_id']}:0")])
    return Screen(
        f"<b>Раунд {detail['round_no']}</b> · {fmt_dt(detail['finished_at'], tz_minutes)}\n\n"
        f"Набор: <b>{escape(pack)}</b>\nСлово: <b>{escape(detail['word'])}</b>\n"
        f"Шпион: {escape(detail['spy_name'])}\n"
        f"Итог: {escape(outcome_text(detail['outcome'], detail['spy_name']))}\n\n"
        f"<b>Игроки и очки</b>\n{players}",
        rows, html=True,
    )


def _player_screen(detail, target, admin, privileged, settings, actor):
    player = next((p for p in detail["players"] if p[0] == target), None)
    if player is None:
        raise AdminError("Игрок не участвовал в этом раунде")
    rid = detail["id"]
    text = f"Игрок: {player[1]}\nTelegram ID: {target}"
    rows = []
    if privileged:
        info = admin.user_info(target)
        text += f"\n\nБлокировка: {describe_ban(info['ban_seconds'])}\nРаундов: {info['rounds']}\nСоздано наборов: {info['packs']}"
        if target not in (actor, settings.admin_id):
            for label, duration in [("Бан на 1 час", "60"), ("Бан на 1 день", "1440"), ("Бан на 7 дней", "10080"), ("Бан навсегда", "perm")]:
                rows.append([(label, f"hist:admin:ban:{rid}:{target}:{duration}")])
        rows.append([("Снять блокировку", f"hist:admin:unban:{rid}:{target}")])
    rows += [_back(rid), [("Закрыть", "menu:home")]]
    return Screen(text, rows)


def _pack_screen(detail, factory, privileged, packs, actor):
    rid = detail["id"]
    historical = detail["pack_title"] or detail["theme"]
    pid = detail["pack_id"]
    if pid is None:
        return Screen(f"Набор в истории: {historical}\n\nВ старом раунде точный ID набора не сохранялся. Управление недоступно, чтобы не изменить чужой набор с похожим названием.", [_back(rid)])
    if not privileged:
        try:
            info = packs.info(actor, pid)
            text = f"Набор в раунде: {historical}\nСейчас: {info.title}\nСлов: {info.words_count}"
        except PackError:
            text = f"Набор в раунде: {historical}\nСейчас набор удалён или недоступен."
        return Screen(text, [_back(rid)])
    try:
        p = HistoryAdminService(factory).pack(pid)
    except AdminError as err:
        return Screen(f"Набор в истории: {historical}\nID: {pid}\n\n{err}", [_back(rid)])
    status = "заблокирован" if p["hidden"] else ("публичный" if p["public"] else "приватный")
    words = ", ".join(p["words"][:15])
    text = f"Набор: {p['title']}\nНазвание в истории: {historical}\nID: {pid}\nАвтор ID: {p['owner'] if p['owner'] is not None else 'встроенный набор'}\nСтатус: {status}\nСлов: {len(p['words'])}\n\nПримеры: {words}"
    rows = [[("Изменить название", f"hist:admin:rename:{rid}")], [("Заменить слова", f"hist:admin:words:{rid}")], [("Сделать приватным" if p["public"] else "Сделать публичным", f"hist:admin:{'private' if p['public'] else 'public'}:{rid}")], [("Снять блокировку набора" if p["hidden"] else "Заблокировать набор", f"hist:admin:{'restore' if p['hidden'] else 'hide'}:{rid}")], [("Удалить набор", f"hist:admin:delete:{rid}")], _back(rid), [("Закрыть", "menu:home")]]
    return Screen(text[:3800], rows)


async def _show(call, screen):
    from app.controllers.telegram.handlers.history import _show as show
    await show(call, screen)


@router.callback_query(F.data.startswith("hist:rd:"))
async def on_round(call: CallbackQuery, history: HistoryService, user_settings: SettingsService, state: FSMContext):
    await state.set_state(None)
    parts = call.data.split(":")
    try:
        detail = await run_db(history.round_detail, call.from_user.id, int(parts[2]))
    except (ValueError, IndexError):
        detail = None
    if detail is None:
        return await safe_answer(call, "Раунд недоступен", show_alert=True)
    from app.controllers.telegram.handlers.history import _int
    context = parts[3:]
    if len(context) == 3 and context[0] == "l":
        page, list_page = _int(context[1]), _int(context[2])
        back = ("К раундам лобби", f"hist:lr:{detail['session_id']}:{page}:{list_page}")
    else:
        page = _int(context[1]) if len(context) == 2 and context[0] == "g" else 0
        back = ("К моим играм", f"hist:games:{page}")
    saved = await state.get_data()
    if not context and saved.get("history_round") == detail["id"]:
        back = saved["history_back"]
    await state.set_data({"history_round": detail["id"], "history_back": back})
    tz = await run_db(user_settings.tz_minutes, call.from_user.id)
    await _show(call, _round_screen(detail, tz, back))


@router.callback_query(F.data.startswith("hist:sp:"))
@router.callback_query(F.data.startswith("hist:player:"))
async def on_player(call: CallbackQuery, history: HistoryService, factory, settings: Settings, state: FSMContext):
    await state.set_state(None)
    try:
        _, kind, context, target = call.data.split(":")
        rid = (await run_db(history.player_round, call.from_user.id, int(context), int(target))) if kind == "sp" else int(context)
        detail = (await run_db(history.round_detail, call.from_user.id, rid)) if rid else None
        if detail is None:
            raise AdminError("Игрок или история недоступны")
        screen = (await run_db(_player_screen, detail, int(target), AdminService(factory), _admin(settings, call.from_user), settings, call.from_user.id))
    except (ValueError, AdminError) as err:
        return await safe_answer(call, str(err), show_alert=True)
    await _show(call, screen)


@router.callback_query(F.data.startswith("hist:pack:"))
async def on_pack(call: CallbackQuery, history: HistoryService, factory, settings: Settings, packs: PackService, state: FSMContext):
    await state.set_state(None)
    try:
        detail = (await run_db(history.round_detail, call.from_user.id, int(call.data.split(":")[2])))
    except (ValueError, IndexError):
        detail = None
    if detail is None:
        return await safe_answer(call, "Раунд недоступен", show_alert=True)
    await _show(call, (await run_db(_pack_screen, detail, factory, _admin(settings, call.from_user), packs, call.from_user.id)))


@router.callback_query(F.data.startswith("hist:admin:"))
async def on_admin_action(call: CallbackQuery, history: HistoryService, factory, settings: Settings, packs: PackService, state: FSMContext):
    if not _admin(settings, call.from_user):
        return await safe_answer(call, "Только для администратора", show_alert=True)
    await state.clear()
    try:
        parts = call.data.split(":")
        action, rid = parts[2], int(parts[3])
        detail = (await run_db(history.round_detail, call.from_user.id, rid))
        if detail is None:
            raise AdminError("Раунд недоступен")
        if action in ("ban", "banok", "unban"):
            target = int(parts[4])
            if not any(p[0] == target for p in detail["players"]):
                raise AdminError("Игрок не участвовал в раунде")
            if action != "unban" and target in (call.from_user.id, settings.admin_id):
                raise AdminError("Нельзя заблокировать администратора")
            admin = AdminService(factory)
            if action == "ban":
                duration = parts[5]
                if duration not in ("60", "1440", "10080", "perm"):
                    raise AdminError("Недопустимый срок")
                label = {"60": "на 1 час", "1440": "на 1 день", "10080": "на 7 дней", "perm": "навсегда"}[duration]
                return await _show(call, Screen(f"Заблокировать игрока {target} {label}?\nБлокировка запретит вход в новые онлайн-комнаты.", [[("Подтвердить бан", f"hist:admin:banok:{rid}:{target}:{duration}")], [("Отмена", f"hist:player:{rid}:{target}")]]))
            if action == "banok":
                if parts[5] not in ("60", "1440", "10080", "perm"):
                    raise AdminError("Недопустимый срок")
                (await run_db(admin.ban, target, None if parts[5] == "perm" else int(parts[5])))
            else:
                (await run_db(admin.unban, target))
            return await _show(call, (await run_db(_player_screen, detail, target, admin, True, settings, call.from_user.id)))
        pid = detail["pack_id"]
        if pid is None:
            raise AdminError("В этом старом раунде ID набора неизвестен")
        manager = HistoryAdminService(factory)
        if action in ("rename", "words"):
            (await run_db(manager.pack, pid))
            await state.set_state(HistoryEdit.value)
            await state.set_data({"round_id": rid, "action": action})
            prompt = "Введите новое название набора." if action == "rename" else "Отправьте полный новый список слов через запятую или с новой строки. Он ЗАМЕНИТ все текущие слова."
            return await _show(call, Screen(prompt, [[("Отмена", f"hist:pack:{rid}")], [("Закрыть", "menu:home")]]))
        if action == "delete":
            p = (await run_db(manager.pack, pid))
            return await _show(call, Screen(f"Удалить набор «{p['title']}» (ID {pid})?\nСлова будут удалены без возможности восстановления. История игр сохранится.", [[("Да, удалить", f"hist:admin:deleteok:{rid}")], [("Отмена", f"hist:pack:{rid}")]]))
        if action not in ("deleteok", "private", "public", "hide", "restore"):
            raise AdminError("Неизвестное действие")
        (await run_db(manager.update, pid, "delete" if action == "deleteok" else action))
        await _show(call, (await run_db(_pack_screen, detail, factory, True, packs, call.from_user.id)))
    except (ValueError, IndexError, AdminError, PackError) as err:
        await safe_answer(call, str(err), show_alert=True)


@router.message(HistoryEdit.value, F.text, ~F.text.startswith("/"), ~F.text.in_(ALL_BUTTONS), F.chat.type == "private")
async def on_edit_value(message: Message, state: FSMContext, history: HistoryService, factory, settings: Settings, packs: PackService, tracker: ScreenTracker):
    if not _admin(settings, message.from_user):
        await state.clear()
        return
    data = await state.get_data()
    rid = data.get("round_id")
    detail = (await run_db(history.round_detail, message.from_user.id, rid)) if rid else None
    if detail is None or detail["pack_id"] is None:
        await state.clear()
        return await present(message, tracker, "Запись или набор недоступны.", back_menu())
    from app.controllers.telegram.handlers.history import _markup
    from app.views.telegram.ui import delete_quietly
    await delete_quietly(message.bot, message.chat.id, message.message_id)
    try:
        (await run_db(HistoryAdminService(factory).update, detail["pack_id"], data["action"], message.text))
    except (AdminError, PackError) as err:
        screen = Screen(f"{err}\nПовторите ввод или отмените действие.", [[("Отмена", f"hist:pack:{rid}")], [("Закрыть", "menu:home")]])
        return await present(message, tracker, screen.text, _markup(screen))
    await state.clear()
    screen = (await run_db(_pack_screen, detail, factory, True, packs, message.from_user.id))
    await present(message, tracker, screen.text, _markup(screen))
