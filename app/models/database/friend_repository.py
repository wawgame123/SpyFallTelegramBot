from sqlalchemy import delete, func, select

from app.models.database.models import WebFriend

MAX_FRIENDS = 250


class FriendListFull(Exception):
    pass


class FriendRepository:
    def __init__(self, factory):
        self.factory = factory

    def list(self, user_id):
        query = select(WebFriend.friend_id, WebFriend.name).where(WebFriend.user_id == user_id)
        with self.factory() as db:
            return dict(db.execute(query).tuples().all())

    def save(self, user_id, friend_id, name, add):
        with self.factory.begin() as db:
            if not add:
                db.execute(delete(WebFriend).where(WebFriend.user_id == user_id, WebFriend.friend_id == friend_id))
                return
            if db.get(WebFriend, (user_id, friend_id)) is not None:
                return
            count = db.scalar(select(func.count()).select_from(WebFriend).where(WebFriend.user_id == user_id))
            if count >= MAX_FRIENDS:
                raise FriendListFull
            db.add(WebFriend(user_id=user_id, friend_id=friend_id, name=name))
