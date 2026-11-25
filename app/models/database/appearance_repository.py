from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.database.models import AvatarAppearance

_UNSET = object()


class AppearanceRepository:
    def __init__(self, factory):
        self.factory = factory

    def list(self, user_ids):
        ids = {user_id for user_id in user_ids if user_id > 0}
        if not ids:
            return {}
        query = select(AvatarAppearance).where(AvatarAppearance.user_id.in_(ids))
        with self.factory() as db:
            result = {}
            for entry in db.scalars(query):
                if entry.hat or entry.face or entry.outfit:
                    look = {"hat": entry.hat, "face": entry.face}
                    if entry.placement:
                        look["placement"] = entry.placement
                    if entry.outfit:
                        look["outfit"] = entry.outfit
                    result[entry.user_id] = look
            return result

    def save(self, user_id, hat, face, placement=None, *, outfit=_UNSET):
        values = {"hat": hat, "face": face, "placement": placement}
        if outfit is not _UNSET:
            values["outfit"] = outfit
        with self.factory.begin() as db:
            if db.get_bind().dialect.name == "sqlite":
                insert = sqlite_insert(AvatarAppearance).values(user_id=user_id, **values)
                query = insert.on_conflict_do_update(index_elements=[AvatarAppearance.user_id], set_=values)
            else:
                insert = mysql_insert(AvatarAppearance).values(user_id=user_id, **values)
                query = insert.on_duplicate_key_update(**values)
            db.execute(query)
