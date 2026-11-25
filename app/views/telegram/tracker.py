import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class ScreenTracker:
    def __init__(self, anchor_file: Path | None = None) -> None:
        self._ids: dict[int, int] = {}
        self._anchors: dict[int, int] = {}
        self._anchor_file = anchor_file
        if anchor_file is not None and anchor_file.exists():
            try:
                stored = json.loads(anchor_file.read_text(encoding="utf-8"))
                self._anchors = {int(uid): int(mid) for uid, mid in stored.items() if int(uid) > 0 and int(mid) > 0}
            except (OSError, ValueError, TypeError, AttributeError):
                log.warning("Cannot restore menu keyboard messages", exc_info=True)

    def _save_anchors(self) -> None:
        if self._anchor_file is None:
            return
        try:
            temporary = self._anchor_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._anchors), encoding="utf-8")
            temporary.replace(self._anchor_file)
        except OSError:
            log.warning("Cannot save menu keyboard messages", exc_info=True)

    def get(self, user_id: int) -> int | None:
        return self._ids.get(user_id)

    def set(self, user_id: int, message_id: int) -> None:
        self._ids[user_id] = message_id

    def pop(self, user_id: int) -> int | None:
        return self._ids.pop(user_id, None)

    def discard(self, user_id: int, message_id: int) -> None:
        if self._ids.get(user_id) == message_id:
            del self._ids[user_id]

    def get_anchor(self, user_id: int) -> int | None:
        return self._anchors.get(user_id)

    def set_anchor(self, user_id: int, message_id: int) -> None:
        self._anchors[user_id] = message_id
        self._save_anchors()

    def pop_anchor(self, user_id: int) -> int | None:
        old = self._anchors.pop(user_id, None)
        if old is not None:
            self._save_anchors()
        return old
