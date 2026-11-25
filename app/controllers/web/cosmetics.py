from aiohttp import web

from app.models.database.appearance_repository import AppearanceRepository
from app.models.database.worker import run_db

STARTER_ITEMS = {
    "hat": {
        "fedora": "Шляпа шпиона",
        "crown": "Корона",
        "beanie": "Шапка с помпоном",
        "party": "Праздничный колпак",
        "wizard": "Шляпа мага",
        "pirate": "Пиратская треуголка",
        "cap": "Кепка",
        "beret": "Берет",
        "helmet": "Шлем пилота",
        "halo": "Нимб",
        "devil": "Рожки",
        "rabbit-ears": "Заячьи уши",
        "cat-ears": "Кошачьи уши",
        "antlers": "Оленьи рога",
        "flower-crown": "Цветочный венок",
        "leaf": "Листок",
    },
    "face": {
        "pigeon-spy": "Клюв и тёмные очки",
        "moustache-mask": "Маска с носом и усами",
        "glasses": "Тёмные очки",
        "cat": "Кошачья мордочка",
        "fox": "Лисья мордочка",
        "moustache": "Усы детектива",
        "heart-glasses": "Очки-сердечки",
        "round-glasses": "Круглые очки",
        "monocle": "Монокль",
        "bandit": "Маска агента",
        "face-mask": "Защитная маска",
        "robot": "Панель робота",
        "bear": "Мордочка медведя",
        "panda": "Мордочка панды",
        "bunny": "Заячья мордочка",
        "blush": "Румянец",
        "star-cheek": "Звёзды на щеках",
        "freckles": "Веснушки",
    },
    "outfit": {
        "spy-trench": "Плащ сыщика",
        "bomber": "Бомбер",
        "hoodie": "Худи",
        "tuxedo": "Смокинг",
        "raincoat": "Дождевик",
    },
}

POSITION_LIMITS = {
    "hat": {"arc": (-65, 65)},
    "face": {"x": (-42, 42), "y": (-42, 42)},
}
COMMON_LIMITS = {"rotation": (-180, 180), "scale": (50, 150)}


def validate_placement(look, placement):
    if not isinstance(placement, dict) or not placement.keys() <= POSITION_LIMITS.keys():
        raise web.HTTPBadRequest(text="Неверное положение украшений")
    for slot, pose in placement.items():
        limits = {**POSITION_LIMITS[slot], **COMMON_LIMITS}
        if not look[slot] or not isinstance(pose, dict) or pose.keys() != limits.keys() | {"mirror"}:
            raise web.HTTPBadRequest(text="Неверное положение украшений")
        if type(pose["mirror"]) is not bool or any(
            type(pose[key]) is not int or not low <= pose[key] <= high for key, (low, high) in limits.items()
        ):
            raise web.HTTPBadRequest(text="Настройки украшения вышли за допустимые пределы")
        if slot == "face" and pose["x"] ** 2 + pose["y"] ** 2 > 42 ** 2:
            raise web.HTTPBadRequest(text="Украшение для лица должно быть внутри аватара")


class Cosmetics:
    def __init__(self, factory):
        self.repository = AppearanceRepository(factory) if factory is not None else None

    async def catalog(self, request):
        return web.json_response({
            slot: [{"id": item_id, "label": label} for item_id, label in items.items()]
            for slot, items in STARTER_ITEMS.items()
        })

    async def equipped(self, user_ids):
        ids = tuple(user_ids)
        if not ids or self.repository is None:
            return {}
        return await run_db(self.repository.list, ids)

    async def save(self, user_id, data):
        if data.keys() - {"placement", "outfit"} != {"hat", "face"}:
            raise web.HTTPBadRequest(text="Неверный набор украшений")
        for slot in STARTER_ITEMS:
            value = data.get(slot)
            if value is not None and (not isinstance(value, str) or value not in STARTER_ITEMS[slot]):
                raise web.HTTPBadRequest(text="Это украшение недоступно")
        if self.repository is None:
            raise web.HTTPServiceUnavailable(text="Не удалось подключить косметику")
        placement = data.get("placement", {})
        validate_placement(data, placement)
        outfit = {"outfit": data["outfit"]} if "outfit" in data else {}
        await run_db(self.repository.save, user_id, data["hat"], data["face"], placement or None, **outfit)
        return data
