"""Health probe usable as ``python -m app.health`` without bot credentials."""
import asyncio
import json
import logging
import os
import time
from pathlib import Path

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.methods import GetUpdates
from sqlalchemy import text

from app.database.worker import run_db

log = logging.getLogger(__name__)
DEFAULT_PATH = Path(__file__).resolve().parents[1] / '.bot-health.json'


def health_path():
    return Path(os.environ.get('BOT_HEALTH_FILE', str(DEFAULT_PATH)))


def check_health(path=None, *, now=None, max_age=30):
    try:
        status = json.loads((path or health_path()).read_text(encoding='utf-8'))
        age = (time.time() if now is None else now) - status['updated_at']
        if not status['healthy']:
            return False, status.get('reason', 'unhealthy')
        if not 0 <= age <= max_age:
            return False, 'heartbeat expired'
        return True, 'healthy'
    except (OSError, ValueError, KeyError, TypeError):
        return False, 'heartbeat unavailable'


class PollingHealthMiddleware(BaseRequestMiddleware):
    def __init__(self):
        self.last_poll = time.monotonic()

    async def __call__(self, make_request, bot, method):
        result = await make_request(bot, method)
        if isinstance(method, GetUpdates):
            self.last_poll = time.monotonic()
        return result


def probe_database(factory):
    with factory() as db:
        db.execute(text('SELECT 1')).scalar_one()


def write_status(path, healthy, reason):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({
        'pid': os.getpid(), 'updated_at': time.time(),
        'healthy': healthy, 'reason': reason,
    }), encoding='utf-8')
    temporary.replace(path)


async def monitor_health(factory, runtime, ticker, polling, path=None, *, interval=5):
    path = path or health_path()
    while True:
        reason = ''
        if ticker.done():
            reason = 'timer task stopped'
        elif time.monotonic() - runtime.last_cycle > 120:
            reason = 'timer task stalled'
        elif time.monotonic() - polling.last_poll > 120:
            reason = 'Telegram polling stalled'
        if not reason:
            try:
                await asyncio.wait_for(run_db(probe_database, factory), timeout=10)
            except Exception:
                reason = 'database probe failed or timed out'
                log.warning('Bot health: %s', reason)
        await asyncio.to_thread(write_status, path, not reason, reason or 'ok')
        await asyncio.sleep(interval)


if __name__ == '__main__':
    healthy, message = check_health()
    print(message)
    raise SystemExit(0 if healthy else 1)
