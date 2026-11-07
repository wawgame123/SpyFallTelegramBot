from collections import OrderedDict
import math


class FailedJoinLimiter:
    def __init__(self, clock, attempts=5, window=60.0, max_users=10000):
        self.clock, self.attempts, self.window, self.max_users = clock, attempts, window, max_users
        self._users = OrderedDict()

    def wait_seconds(self, user_id):
        value = self._users.get(user_id)
        if value is None:
            return 0
        start, failures = value
        remaining = start + self.window - self.clock()
        if remaining <= 0:
            self._users.pop(user_id, None)
            return 0
        return max(1, math.ceil(remaining)) if failures >= self.attempts else 0

    def failed(self, user_id):
        now = self.clock()
        start, failures = self._users.pop(user_id, (now, 0))
        if now - start >= self.window:
            start, failures = now, 0
        if len(self._users) >= self.max_users:
            self._users.popitem(last=False)
        self._users[user_id] = (start, failures + 1)

    def succeeded(self, user_id):
        self._users.pop(user_id, None)
