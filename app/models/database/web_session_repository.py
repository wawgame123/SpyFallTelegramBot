from sqlalchemy import delete, select

from app.models.database.models import WebSession


class WebSessionRepository:
    def __init__(self, factory, scope):
        self.factory = factory
        self.scope = scope

    def active(self, now, limit):
        with self.factory.begin() as db:
            db.execute(delete(WebSession).where(WebSession.expires_at <= now))
            query = select(WebSession.digest, WebSession.expires_at, WebSession.user_id, WebSession.name).where(
                WebSession.scope == self.scope,
            ).order_by(WebSession.expires_at.desc(), WebSession.digest).limit(limit)
            return db.execute(query).all()

    def add(self, digest, user_id, name, expires_at, now):
        with self.factory.begin() as db:
            db.execute(delete(WebSession).where(WebSession.expires_at <= now))
            db.add(WebSession(digest=digest, scope=self.scope, user_id=user_id, name=name, expires_at=expires_at))

    def remove(self, digest):
        with self.factory.begin() as db:
            db.execute(delete(WebSession).where(WebSession.scope == self.scope, WebSession.digest == digest))
