import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class LeaveConfirmation:
    token: str
    room_id: int
    panel_id: int
    message_id: int
    text: str
    expires_at: float


class LeaveConfirmations:
    """Single-use prompts bound to their sender, room, panel and message.

    Restarting intentionally invalidates all outstanding confirmations.
    """
    def __init__(self, ttl=120.0, max_entries=4096, clock=time.monotonic):
        self.ttl = ttl
        self.max_entries = max_entries
        self.clock = clock
        self._items = OrderedDict()

    def get(self, user_id):
        item = self._items.get(user_id)
        return item if item and item.expires_at > self.clock() else None

    def pop(self, user_id):
        return self._items.pop(user_id, None)

    def new_token(self):
        return secrets.token_urlsafe(9)

    def register(self, user_id, room_id, panel_id, message_id, text, token):
        self.pop(user_id)
        while len(self._items) >= self.max_entries:
            self._items.popitem(last=False)
        self._items[user_id] = LeaveConfirmation(token, room_id, panel_id, message_id, text, self.clock() + self.ttl)

    def consume(self, user_id, message_id, token):
        if not isinstance(token, str) or not token.isascii() or len(token) > 64:
            return None
        item = self.get(user_id)
        if item is None or item.message_id != message_id or not secrets.compare_digest(item.token, token):
            return None
        return self.pop(user_id)
