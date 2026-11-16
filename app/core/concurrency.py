"""concurrency.py — how many requests may run database code at the same time.

The handlers are `async def`; the database code behind them is synchronous
(psycopg) and runs in the worker threads of AnyIO's default thread limiter
(`starlette.concurrency.run_in_threadpool`). Two numbers decide what happens
under a burst, and they have to be read together:

  * `TURNOS_THREADPOOL_SIZE` — threads in that limiter (AnyIO's default: 40);
  * `TURNOS_DB_POOL` / `TURNOS_DB_POOL_TIMEOUT` (core/db.py) — connections in
    the pool (default 15) and how long a request waits for a free one (3 s)
    before the answer is the 503 «base de datos no disponible».

More threads than connections is fine while requests spend most of their time
outside the database; it only turns into 503s when the surplus threads all
want a connection for longer than the wait. Measured figures and the
recommended pairing: docs/architecture/capacity.md.

Applied once, from `ops/web_runtime.start_background_services()` (it runs inside the
app lifespan), because the limiter belongs to the running event loop.
"""
from app.core import settings

THREADPOOL_SIZE_DEFAULT = settings.THREADPOOL_SIZE_DEFAULT  # AnyIO's own default, kept explicit


def threadpool_size():
    """`TURNOS_THREADPOOL_SIZE`: the default when unset, empty or not a number, never below 1
    (`core.settings.ProcessSettings`)."""
    return settings.ProcessSettings.current().threadpool_size


def apply_threadpool_size():
    """Set the default thread limiter to `threadpool_size()`. Must run inside
    the event loop (the limiter is bound to it). Returns the size applied."""
    import anyio.to_thread

    size = threadpool_size()
    anyio.to_thread.current_default_thread_limiter().total_tokens = size
    return size
