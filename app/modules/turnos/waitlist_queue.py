"""waitlist_queue.py — Tier 4 slice 5
(docs/roadmap/tier4-replatform-design.md, "Background daemon threads ->
Postgres-backed queue"): the SAME `waitlist.tick()` sweep, run by a
`procrastinate` periodic task against the SAME Postgres instead of a
`threading.Thread` interval loop. No Redis, no second database — the
connector is a plain `psycopg` pool pointed at the same `DATABASE_URL`
everything else in this repo already uses.

Topology: this module is the slice 5 PILOT shape — an IN-PROCESS worker in
a daemon thread started from `waitlist.start()` when TURNOS_WAITLIST_QUEUE=1.
That in-process choice was DELIBERATELY REVERSED by the worker-topology
slice that followed (2026-08-08, the maintainer's infrastructure review: the
open-source plan promised a Postgres-backed queue BOUNDARY, not a queue
library embedded in the monolith): `worker.py` at the repo root is now the
production-legible topology — a separate OS process with its own
entrypoint, health signal and graceful shutdown, selected by
TURNOS_QUEUE_WORKER (see worker.py's docstring and the design doc's
worker-topology section). This pilot path stays intact and mutually
exclusive by construction — `waitlist.start()` picks exactly one of thread
/ in-process queue / delegate-to-the-worker — because every
deployment running today defaults to the thread, and the pilot remains the
smallest way to run the queue without a second process (dev, tests).

Deliberately a SEPARATE sibling module from `waitlist.py`, never imported
at that module's top level — only lazily, inside `waitlist._start_queue()`,
and only when `TURNOS_WAITLIST_QUEUE=1`. `waitlist.py` is imported
unconditionally today (every booking/cancel touches it, and the thread
sweep is the default for every deployment that exists right now), so
anything imported at ITS top level becomes an unconditional cost of the
DEFAULT (queue off) shape. This mirrors Tier 4 slice 3's own finding on
`router_attr` sibling modules (`docs/roadmap/tier4-replatform-design.md`,
slice 3 "Finding 1") — same reasoning, one level down the dependency stack:
`import procrastinate` pulls in `asgiref`/`croniter`/`psycopg_pool`, none of
which any deployment running today's default should ever pay for.

Race guarantees: `waitlist.tick()`/`release_slot()`/`_create_offer()` are
UNCHANGED — this module only decides WHEN/HOW `tick()` gets called, never
what it does. Tier 1.4's guards (the partial UNIQUE index on live offers,
the `FOR UPDATE`-free check-then-insert-with-ON-CONFLICT pattern, the
claim-CAS) live inside those functions, not in the caller, so multiple
procrastinate workers (if this deployment ever runs more than one replica)
calling `tick()` concurrently hit the exact same DB-level guards two
threads already hit today (proven by `tests/test_integrity_races.py::
TestWaitlistOfferRace`, which calls `waitlist.release_slot()` directly and
is untouched by this slice).

Degradation contract (same "nothing breaks because of email or HSI"
doctrine, applied to the queue infra): any failure here — psycopg import,
`DATABASE_URL` unset, schema apply failing, the worker thread dying — is
caught and logged; the observable effect is "the sweep doesn't run until
the process restarts or the underlying problem is fixed," never a crashed
server or a lost booking. `tick()` is already idempotent and safe to run
late or skip a beat (its own docstring says so) — a sweep that doesn't fire
is a delayed promotion, not a lost one, the same acceptable-degradation
shape as the thread it replaces.
"""
import sys
import threading

from app.core import queue_infra
from app.core import launch_signals
from app.core import log_pii

_QUEUE = "waitlist"
_TASK_NAME = "modules.turnos.waitlist.sweep"

_lock = threading.Lock()
_app = None
_worker_thread = None


def _log(msg):
    print(f"[waitlist_queue] {msg}", file=sys.stderr, flush=True)


# Generic machinery — extracted to core/queue_infra.py the day the worker
# entrypoint (worker.py) became this schema's second consumer; these
# aliases keep this module's public surface (and its tests) unchanged. The
# full non-idempotent-DDL/advisory-lock/connector-class reasoning lives on
# the queue_infra docstrings now.
_cron_for_interval = queue_infra.cron_for_interval
ensure_schema = queue_infra.ensure_schema
_apply_schema_standalone = queue_infra._apply_schema_standalone


def register(app, interval_min, cron=None):
    """Registers the periodic task wrapping `waitlist.tick()`, UNMODIFIED,
    on a procrastinate App built by the CALLER — this function decides
    nothing about the sweep's own logic, only its cadence. Two callers
    exist: `build_app()` below (the slice 5 in-process pilot) and
    `worker.py` (the queue worker process), which composes several job
    modules' `register()` onto ONE app.

    Registration mechanics (cron derivation, the `cron` test seam, the
    double-gated mid-task hold for crash suites) live ONCE in
    `queue_infra.register_periodic_sweep` — this module only owns the
    sweep body."""
    def _cuerpo(timestamp):  # noqa: ANN001 — periodic tasks receive the unix ts
        from app.modules.turnos import waitlist
        try:
            resumen = waitlist.tick()
        except Exception as e:  # noqa: BLE001 — same visibility the thread loop gave
            _log(f"tick() falló: {log_pii.exc(e)}")
            launch_signals.handled("waitlist_sweep_failed")
            raise
        if resumen and any(resumen.values()):
            _log(f"barrido: {resumen}")
        return resumen

    return queue_infra.register_periodic_sweep(
        app, queue=_QUEUE, task_name=_TASK_NAME,
        interval_min=interval_min, cron=cron, body=_cuerpo,
        monitor=("turnos-waitlist", max(interval_min, 5), 30))


def build_app(interval_min, cron=None):
    """The in-process pilot's App: `queue_infra.build_app()`'s async
    `PsycopgConnector` (the only class that can run a worker — see that
    module's docstring for the full connector-class finding) + this one
    module's task. `worker.py` does NOT call this — it builds one shared
    app and calls `register()` directly, so both topologies register the
    IDENTICAL task with identical cadence derivation."""
    app = queue_infra.build_app()
    _sweep = register(app, interval_min, cron=cron)
    return app, _sweep


def _run_worker(app):
    try:
        app.run_worker(queues=[_QUEUE], listen_notify=False,
                        install_signal_handlers=False)
    except Exception as e:  # noqa: BLE001 — the thread must never die silently
        _log(f"worker terminó con error: {log_pii.exc(e)}")


def start(interval_min):
    """Idempotent: a second call while a worker is already running is a
    no-op. Never raises — the caller (`waitlist._start_queue`) also wraps
    this, but a failure surfacing from HERE (schema apply, App/connector
    construction) gets its own `[waitlist_queue]`-prefixed log line before
    that outer wrapper's more generic one."""
    global _app, _worker_thread
    with _lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        from app.core import db
        if not db._db_url():
            _log("DATABASE_URL no configurada — cola no iniciada (degradación esperada)")
            return
        ensure_schema()
        app, _sweep_task = build_app(interval_min)
        _app = app
        th = threading.Thread(target=_run_worker, args=(app,),
                               name="waitlist-queue-worker", daemon=True)
        th.start()
        _worker_thread = th
