"""Run complete synchronous service calls away from Telegram's event loop.

One worker preserves the ordering and cache semantics of the sync services.
Sessions/transactions are created and closed inside each service call, never
passed between threads. The admission limit also bounds the executor queue.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from weakref import WeakKeyDictionary

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bot-db")
_limits = WeakKeyDictionary()


async def run_db(function, /, *args, **kwargs):
    loop = asyncio.get_running_loop()
    limit = _limits.setdefault(loop, asyncio.Semaphore(32))
    await limit.acquire()
    try:
        future = loop.run_in_executor(_executor, partial(function, *args, **kwargs))
    except BaseException:
        limit.release()
        raise
    # Cancelling a handler cannot interrupt a running SQL transaction. Keep its
    # admission slot until the actual operation completes, including on cancel.
    def finished(done):
        limit.release()
        if not done.cancelled():
            done.exception()
    future.add_done_callback(finished)
    return await asyncio.shield(future)
