import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from app.database.models import LocalGameRecord


class LocalGameRepository:
    def __init__(self, factory: sessionmaker):
        self._factory = factory

    def load(self, owner_id: int) -> dict | None:
        with self._factory() as db:
            record = db.get(LocalGameRecord, owner_id)
            return json.loads(record.state) if record else None

    def save(self, owner_id: int, state: dict) -> None:
        payload = json.dumps(state, ensure_ascii=False)
        with self._factory.begin() as db:
            record = db.get(LocalGameRecord, owner_id)
            if record:
                record.state = payload
            else:
                db.add(LocalGameRecord(owner_id=owner_id, state=payload))

    def delete(self, owner_id: int) -> None:
        with self._factory.begin() as db:
            record = db.get(LocalGameRecord, owner_id)
            if record:
                db.delete(record)

    def purge_stale(self, days: int = 30) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self._factory.begin() as db:
            result = db.execute(delete(LocalGameRecord).where(LocalGameRecord.updated_at < cutoff))
            return result.rowcount or 0
