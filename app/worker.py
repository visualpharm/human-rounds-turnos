"""worker.py — the queue worker's OWN process (Tier 4 worker-topology
slice, docs/roadmap/tier4-replatform-design.md, "Worker topology").

`python3 worker.py` runs the procrastinate worker for every queue-migrated
periodic job — today the waitlist sweep (modules/turnos/waitlist_queue.py),
the reminders sweep (notifications/reminders_queue.py), the table-hygiene
sweep (ops/housekeeping_queue.py), the referral-file retention sweep
(modules/turnos/referrals_queue.py) and, since daemon batch 3, the two
AI-cache warmups — chart-summary pre-generation (ai/chart_summary_queue.py)
and the translation-cache warmup (ops/translate_warmup_queue.py) — against
the SAME Postgres (`DATABASE_URL`) everything else uses. No Redis, no second database, no
web code: this process never imports server.py, app.py or any router.

WHICH jobs, and what each is allowed to touch, is declared in ONE place:
the `_JOBS` registry below (registry-hardening round, 2026-08-08). Each
entry carries the job's queue/task identity, its ON/OFF truth, its cadence
source, and its declared capabilities — `filesystem` (it reads or deletes
files under the data volume), `outbound`, and an audited `rerun` contract
(why a post-SIGKILL re-run is safe). Registration order, startup logs and
the machine-readable audit (`python3 worker.py --jobs`,
read-only, works with or without a reachable database) all derive from
the registry; a batch-3 job joins by adding a spec, and TestJobRegistry
refuses one with undeclared capabilities. `/health` reports the consumed
queues, so registration is observable on a LIVE worker, not only in the
code. Crash recovery is scoped by queue AND by declared task name — a
task the registry doesn't declare is never re-run by this worker.

Filesystem: the worker is always the supervised child of the web process
(`TURNOS_QUEUE_WORKER=1`, same container), so it sees the same data volume
by construction. Filesystem ownership is declared in the registry
(currently referral retention, which deletes files under data/ordenes/, and
data-volume backups, which read and upload the whole mounted data tree).
The housekeeping queue job is purges-only and DB-only: the disk check
(measurement + advisory email, deduped via the Postgres `alert_claims`
at-most-once claim) deliberately stays on the WEB process's legacy schedule
in every topology, because its at-most-once delivery cannot meet the queue
batch's literal no-skipped-work bar. (A second-container mode,
`TURNOS_QUEUE_WORKER=external` with `TURNOS_WORKER_SHARED_DATA=1`, existed
until 2026-10-30; no installation ran it, and it was removed.)
It is the deliberate REVERSAL of slice 5's in-process-thread
topology (recorded in the design doc's worker-topology section): the queue
boundary now has a process boundary to match — its own entrypoint, PID,
lifecycle, health signal and graceful shutdown — instead of living as a
daemon thread inside the web monolith.

How it runs in each deployment shape (chosen by TURNOS_QUEUE_WORKER on the
WEB process — see waitlist.start()/reminders.start() for the delegation
and asgi_main.py for the supervisor):

- `TURNOS_QUEUE_WORKER=1` — asgi_main.py spawns and supervises THIS file as
  a child process in the same container (restart with backoff if it dies,
  SIGTERM forwarded on shutdown). Single-container topology: what the
  pinamar.humanrounds.org and demo containers run (checked on the host,
  2026-10-28), and the smallest production-legible shape Coolify can host
  without a second app.
- Flag unset (the code default; pinamar.humanrounds.org and the demo run
  `=1`, so this is the shape of an installation that sets nothing): the web
  process keeps its thread sweeps (or the slice 5 in-process pilot if
  TURNOS_WAITLIST_QUEUE=1) and nothing spawns this file. This entrypoint also REFUSES to run (exit 2) when
  invoked with the flag unset or invalid: the worker exists only as the
  supervised child, so a manual/one-shot run must state
  `TURNOS_QUEUE_WORKER=1` explicitly.

Liveness/readiness has two independent signals:

- HTTP, for process supervisors: `/health` (200 = process up) and `/ready`
  (200 = worker loop running AND Postgres reachable right now, 503
  otherwise) on TURNOS_WORKER_HEALTH_PORT (default 7710, bound to
  127.0.0.1).
- Postgres, for operators and the web side: procrastinate 3.x registers
  every named worker in `procrastinate_workers` with a heartbeat row
  updated every ~10s — `SELECT worker_id, last_heartbeat FROM
  procrastinate_workers` is the fleet census, no network path to the
  worker needed. The web app's own /health NEVER consults either signal:
  a down worker must not make the web app unhealthy (sweeps degrade to
  "delayed", exactly like a crashed daemon thread always has).

The web process being down does not stop this worker either — the two
coordinate through Postgres alone, though since batch 2 the referrals
job also needs the SAME data volume in sight (it deletes files on it):
"shared state" is Postgres plus, for that one job, the data volume —
never sockets or memory. Queued jobs survive this process dying at any
point (they live in `procrastinate_jobs`), and two workers running at
once fetch each job exactly once (FOR UPDATE SKIP LOCKED; periodic
defers are unique per (task, tick) via
`procrastinate_periodic_defers`) — proven by tests/test_queue_worker.py.
"Survive" needs one thing procrastinate does NOT do on its own (Codex
gate, 2026-08-08): a job whose worker is SIGKILLed mid-run stays
status='doing' forever — fetch only reads 'todo', and stalled-WORKER
pruning merely nulls the job's worker_id. This worker therefore runs its
own scoped stalled-job recovery (`_recover_stalled_jobs`): once at
startup (a replacement requeues its predecessor's in-flight job as part
of booting — recovery latency is bounded by the supervisor's restart
backoff plus that boot, not "instant") and every 5 minutes as a
periodic task for the no-restart case, requeueing 'doing'
jobs of ITS OWN queues whose worker heartbeat is older than
TURNOS_WORKER_STALLED_S (default 30 s ≈ 3 heartbeats — a healthy
worker's running jobs are never touched). Requeue re-runs the whole task
body. What that guarantees is precise: a re-run never DUPLICATES a
business mutation (every sweep's mutations are idempotent per item),
and for the batch-2 tasks the re-run converges to the same single
effect — proven mid-task-SIGKILL-and-replace in
TestBatch2CrashRecovery. Outbound NOTIFICATIONS are NOT uniformly
guaranteed delivered: each queue's delivery semantics under a kill are
audited per queue in `_recover_stalled_jobs`' docstring. The batch-2
jobs themselves carry no outbound at all (the disk alert runs web-side
only, excluded from the queue in round 8); for the not-activated
business queues, a waitlist offer or reminder killed between its DB
claim and its send is lost — a pre-existing exposure of the thread
path, recorded for the untaken business-queue activation decision.

Graceful shutdown: SIGTERM/SIGINT → procrastinate stops fetching, finishes
in-flight jobs up to TURNOS_WORKER_GRACEFUL_TIMEOUT seconds (default 20,
under Docker's stop grace), then exits 0. Failures exit loudly — unlike
the web process's degradation contract, a worker that can't work SHOULD
die — with THREE distinct codes the supervisor honors (EXIT_TRANSIENT /
EXIT_CONFIG / EXIT_FORCE_KILL below): 1 = transient (unreachable Postgres,
loop error) → backoff-restart; 2 = permanent invalid configuration (no
DATABASE_URL, an interval cron can't express) → the supervisor does NOT
retry, because a restart can't fix configuration; 3 = a SECOND
SIGTERM/SIGINT forced an immediate exit mid-shutdown → backoff-restart
(same as 1 — an operator escalating past graceful shutdown is not a
config problem), distinguished only for observability.

A second SIGTERM/SIGINT is a deliberate force-kill escape hatch, not a
repeat of the first — and it exits via `os._exit()` (`_force_exit()`
below), never by falling through to the signal's OS default disposition.
That distinction matters: Python's default disposition is silent
immediate termination for SIGTERM, but for SIGINT it is
`signal.default_int_handler`, which RAISES `KeyboardInterrupt` as a
normal, catchable Python exception at whatever point of execution the
interpreter happens to be — including code with no try/except around it.
An earlier cut of this file's signal handling restored the OS default
disposition on the first signal (so a second one would "just work"),
which is correct for SIGTERM but let a rapid double-SIGINT crash the
process with an uncaught KeyboardInterrupt and a non-contract exit code,
instead of taking any of the three codes above. `os._exit()` sidesteps
Python's exception machinery entirely — no exception raised, no
traceback, no finally blocks run, uniform for both signals. See
docs/solutions/dev/worker-signal-ownership-not-task-cancel.md.
"""
import json
import os
import signal
import socket
import sys
import threading
import time
from typing import Callable, NamedTuple

if not __package__:  # run as a file (`python3 app/worker.py`): the project root comes first
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import log_pii  # noqa: E402
from app.core import paths  # noqa: E402
from app.core import settings  # noqa: E402
from app.core import worker_stop  # noqa: E402

ROOT = paths.PROJECT_ROOT


_cancellation_aware_periodic_deferrer_class = None


def _cancellation_aware_periodic_deferrer():
    """Build the adapter only after the worker has completed bootstrap."""
    global _cancellation_aware_periodic_deferrer_class
    if _cancellation_aware_periodic_deferrer_class is not None:
        return _cancellation_aware_periodic_deferrer_class

    import asyncio
    from procrastinate import periodic as procrastinate_periodic

    class _CancellationAwarePeriodicDeferrer(procrastinate_periodic.PeriodicDeferrer):
        """Re-raise a side-task cancellation consumed by pool cleanup.

        Psycopg-pool cleanup may intentionally consume a ``CancelledError``
        while returning a periodic-defer connection. Python retains the task's
        pending cancellation count in that case; without this guard,
        Procrastinate 3.9's periodic loop starts its next cron sleep and its
        native shutdown waits for that sleep before unregistering the worker.
        """

        async def wait(self, next_tick):
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            await super().wait(next_tick)

    _cancellation_aware_periodic_deferrer_class = _CancellationAwarePeriodicDeferrer
    return _cancellation_aware_periodic_deferrer_class


async def _periodic_deferrer_with_cancellation_guard(self):
    """The installed Worker's method with only its deferrer class changed."""
    deferrer = _cancellation_aware_periodic_deferrer()(
        registry=self.app.periodic_registry,
        **self.app.periodic_defaults,
    )
    return await deferrer.worker()


def _guard_periodic_deferrer_cancellation(worker):
    """Bind the narrow adapter without copying Procrastinate's worker loop."""
    import types
    worker._periodic_deferrer = types.MethodType(
        _periodic_deferrer_with_cancellation_guard, worker)
    return worker


def _guard_heartbeat_update_cancellation(worker):
    """Keep native heartbeat ownership while restoring swallowed cancellation.

    This wraps only the constructed worker app's heartbeat call boundary.  It
    deliberately does not reproduce Procrastinate's heartbeat loop, worker
    run/shutdown, or unregister implementation.
    """
    import asyncio

    update_heartbeat = worker.app.job_manager.update_heartbeat

    async def cancellation_aware_update_heartbeat(worker_id):
        result = await update_heartbeat(worker_id)
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        return result

    worker.app.job_manager.update_heartbeat = cancellation_aware_update_heartbeat
    return worker

# Light by design: each *_queue module imports only stdlib + core.queue_infra
# at its top level (their docstrings defend that boundary) — the heavy
# business modules (waitlist, reminders, housekeeping, booking_requests,
# chart_summary, translate_warmup) stay lazy inside the registry's callables
# below, so importing THIS module (tests, tooling) costs nothing beyond the
# queue/task-name constants.
from app.ai import chart_summary_queue  # noqa: E402
from app.modules.turnos import referrals_queue, waitlist_queue, hsi_journal_queue  # noqa: E402
from app.notifications import reminders_queue  # noqa: E402
from app.ops import data_backups_queue, housekeeping_queue, translate_warmup_queue  # noqa: E402

_STATE = {"fase": "arrancando"}  # arrancando | corriendo | apagando | error

# Strong references to hsi_sync's own background startup-kick task(s) — see
# _dispatch_hsi_sync_rekick()'s docstring for why this exists (bug fix,
# 2026-08-09): asyncio.create_task() alone is not enough, the event loop
# only holds a WEAK reference for asyncio.all_tasks()'s own bookkeeping, so
# an unreferenced Task can be garbage-collected mid-flight. Discarded via
# add_done_callback the moment each task finishes.
_HSI_SYNC_STARTUP_TASKS = set()
_BACKUP_STARTUP_TASKS = set()


# The file descriptor `_log` falls back to (a module constant so a test can
# point it at a pipe).
_STDERR_FD = 2


def _log(msg):
    """One line on stderr; never raises out of a signal handler.

    The plain SIGTERM/SIGINT handler logs through here, and a signal that
    lands while the main thread is inside its own ``print`` to stderr makes
    this call re-enter the same ``BufferedWriter``: CPython refuses that
    with ``RuntimeError: reentrant call``, the exception leaves the handler
    and the worker exited 1 instead of shutting down gracefully (seen once
    in 40 rapid spawn/SIGTERM rounds at load ~200, 2026-11-06). In that
    case the line goes straight to the descriptor, unbuffered.
    """
    line = f"[worker] {msg}"
    try:
        print(line, file=sys.stderr, flush=True)
    except RuntimeError:
        data = (line + "\n").encode("utf-8", "replace")
        try:
            while data:
                data = data[os.write(_STDERR_FD, data):]
        except OSError:
            pass


# How long a signal counted by main()'s plain handler during the asyncio
# handoff may still absorb its wakeup-byte twin (`_install_async_signal_handlers`).
_HANDOFF_ECHO_TTL_S = 1.0


class _SignalState:
    """One shutdown request/counter for one ``main()`` invocation.

    The synchronous pre-loop handler and the asyncio callback both mutate
    this same object.  Keeping it per invocation matters for in-process
    tests, while keeping it shared matters for a first signal before the
    loop and a second one after it has started.

    This state never calls ``Worker.stop()`` itself.  A synchronous handler
    can run in the tiny construction-to-``Worker.run()`` stretch where
    procrastinate clears its stop event; the event-loop callback is the
    owner that calls ``Worker.stop()`` only after it can safely interleave.
    """

    def __init__(self):
        self.stop_requested = False
        self.signal_count = 0
        # True only while `_install_async_signal_handlers` hands the signals
        # to asyncio; `handoff_echoes` maps each signal the plain handler
        # counted inside that window to when it did (see that function).
        self.handoff = False
        self.handoff_echoes = {}

    def note_plain_signal(self, signum):
        """Called by main()'s plain handler before it counts a signal."""
        if self.handoff:
            self.handoff_echoes[signum] = time.monotonic()

    def take_handoff_echo(self, signum):
        """``True`` when this asyncio callback is the wakeup-byte twin of a
        signal the plain handler already counted during the handoff — the
        first callback for that signal within `_HANDOFF_ECHO_TTL_S`."""
        noted = self.handoff_echoes.pop(signum, None)
        return noted is not None and time.monotonic() - noted <= _HANDOFF_ECHO_TTL_S

    def request_stop(self, signum, phase):
        """Record the first request; force-exit on every later one.

        ``False`` means `_force_exit()` was invoked.  It only returns in
        patched in-process tests; production exits immediately.
        """
        self.signal_count += 1
        if self.signal_count >= 2:
            _force_exit(EXIT_FORCE_KILL, signum)
            return False
        self.stop_requested = True
        _log(f"SIGTERM/SIGINT received during {phase} — recording graceful "
             "shutdown; a SECOND signal exits immediately and deliberately "
             "(os._exit — see _force_exit()).")
        return True


def _db_ok():
    try:
        import sqlalchemy as sa
        from app.core import db, sqlcore
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, sa.select(sa.literal_column("1")))
        return True
    except Exception:  # noqa: BLE001 — the probe reports, never raises
        return False


def _start_health_server():
    """The HTTP liveness/readiness signal, in a daemon thread. Returns the
    server (its .server_address carries the real port when the env asked
    for 0 — the test seam for an ephemeral port). Never fatal: a health
    server that can't bind (port taken) logs and returns None — the worker
    itself still runs, and the Postgres heartbeat remains as the
    observable signal."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _HealthServer(ThreadingHTTPServer):
        def server_bind(self):
            # Health startup must not wait on reverse DNS. HTTPServer's
            # default bind calls getfqdn(), although these JSON endpoints
            # never use a canonical hostname.
            from socketserver import TCPServer
            TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address[:2]

    class _Salud(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                # `colas` = the queues THIS worker consumes (set once in
                # main() before the loop starts) — the registration half
                # of the liveness story, so an operator hitting /health
                # sees WHAT the worker owns, not only that it is up.
                code, body = 200, {"ok": True, "fase": _STATE["fase"],
                                   "queues": _STATE.get("queues", [])}
            elif self.path == "/ready":
                listo = _STATE["fase"] == "corriendo" and _db_ok()
                code = 200 if listo else 503
                body = {"ok": listo, "fase": _STATE["fase"]}
            else:
                code, body = 404, {"ok": False}
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):  # noqa: ANN002 — silencio: cada probe no es una línea de log
            pass

    host = "127.0.0.1"
    port = settings.ProcessSettings.current().worker_health_port
    try:
        httpd = _HealthServer((host, port), _Salud)
    except OSError as e:
        _log(f"salud HTTP no disponible ({host}:{port}): {log_pii.exc(e)} — sigo sin ella")
        return None
    threading.Thread(target=httpd.serve_forever, name="worker-health",
                     daemon=True).start()
    _log(f"salud en http://{host}:{httpd.server_address[1]}/health")
    return httpd


_MAINT_QUEUE = "maintenance"
_JOB_RETENTION_H = 72

# The maintenance queue's own task identities — used by the registration,
# the recovery's allowed-task scoping and the --jobs audit, so they can
# never drift apart.
_MAINT_TASKS = ("worker.maintenance.remove_old_jobs",
                "worker.maintenance.retry_stalled_jobs",
                "worker.maintenance.hsi_sync_watchdog",
                "worker.maintenance.data_backups_watchdog")
# Registered only while TURNOS_SENTRY_SIGNALS lists a feature AND a DSN is set (a default worker has exactly
# the maintenance tasks above, no extra job rows).
_SIGNALS_TASK = "worker.maintenance.launch_signals"


def _maint_tasks():
    """The maintenance tasks THIS worker registers: the fixed ones, plus the launch-signals heartbeat when on."""
    from app.core import launch_signals
    return _MAINT_TASKS + ((_SIGNALS_TASK,) if launch_signals.any_active() else ())

# Re-run contract of the maintenance queue itself (its four tasks are
# registered unconditionally below, outside the business registry):
_MAINT_RERUN = ("delete_old_jobs and the stalled-job recovery are idempotent "
                "by construction: a re-run deletes/requeues whatever is "
                "left, never duplicates. The hsi_sync watchdog re-checks "
                "chain_alive() itself before ever re-kicking, so a re-run "
                "after a SIGKILL mid-check does nothing new if the chain "
                "is already alive, and re-kicks (idempotently, via the "
                "shared queueing lock) if it genuinely is not. The "
                "launch-signals task (registered only when TURNOS_SENTRY_SIGNALS "
                "is on) is a heartbeat check-in plus a once-a-day claim: a re-run "
                "repeats the check-in and finds the day already claimed or sends "
                "nothing twice.")


class _JobSpec(NamedTuple):
    """One queue-migrated job, declared as DATA (registry-hardening round,
    2026-08-08). Before this registry, WHICH jobs the worker runs, which
    ones touch the filesystem, and why each is safe to re-run lived in
    three places (an if/else chain here, a hand-maintained tuple in
    main(), and prose in _recover_stalled_jobs' docstring)
    that had to be kept aligned by discipline. Now a job is ONE entry
    here, and everything else derives from it: registration order,
    startup logs and the machine-readable audit
    (`python3 worker.py --jobs`). TestJobRegistry pins the derivations and
    refuses a spec with an empty re-run contract — a batch-3 job cannot
    join without declaring its capabilities."""
    id: str                      # stable slug (tests/audit identity)
    label: str                   # operator-facing name (English — exposed by --jobs)
    queue: str                   # procrastinate queue this job owns
    task_name: str               # canonical task name (same as the module's)
    enabled: Callable[[], bool]  # ONE truth for "does this worker own the sweep"
    off_reason: str              # logged verbatim when enabled() is False
    cadence: Callable[[], str]   # human cadence, from the thread twin's OWN helpers
    register: Callable           # (app) -> None; delegates to the module's register()
    filesystem: bool             # touches the data volume
    outbound: bool               # may emit outbound messages when activated
    rerun: str                   # audited statement: why a post-kill re-run is safe
    # One startup sweep is deferred at worker boot (durable, queued-row
    # dedup by queueing lock, EXECUTION serialized by the per-task lock —
    # see _defer_startup_jobs). Declared where the thread twin runs its
    # first tick AT BOOT and a first-run gap of one whole cron interval
    # would be user-visible: the AI-cache warmups (batch 3 — a cold
    # install's chart summaries / translated catalog must not wait up to
    # 60 min / 6 h for the first cron boundary). The batch-2 jobs
    # deliberately stay False: their boundary-first cadence was accepted
    # at their own gate (minute-scale or daily purges of already-old
    # rows, where the first-boundary wait changes nothing a user can
    # see).
    startup: bool = False
    # Seconds the startup sweep is scheduled AFTER boot (schedule_at =
    # the boot instant from core.clock + this, on the defer — durable: the delay lives in the job row's scheduled_at,
    # not in a sleep). Preserves the thread twin's own grace verbatim:
    # translate_warmup sleeps FIRST_RUN_DELAY_S (30) before its first
    # pass, chart_summary ticks immediately (0). Pinned against the
    # module constant by TestJobRegistry.
    startup_delay_s: int = 0
    # The task registers with procrastinate's native per-job `lock`
    # (task-level default `queue_infra.task_lock_name(task)`, passed as
    # exclusive=True by the module's register()): the library refuses to
    # FETCH a job whose lock string a running job holds, so cron,
    # startup and recovery invocations serialize fleet-wide — a
    # concurrent second job WAITS and runs after, never alongside.
    # Declared True for every outbound-capable cache sweep — overlap is
    # the double-spend path (two executions reading the same stale state,
    # both paying). The serialized follow-up run then no-ops on unchanged
    # sources via the run-twice-proven fingerprint/hash guards: lock +
    # guards, together, are the spend claim. Informational here (the lock
    # is applied at registration); the two-worker proofs in
    # TestAiCacheExecutionLock keep this field honest.
    exclusive: bool = False


def _waitlist_enabled():
    """ONE truth for 'does this worker own the waitlist sweep' (same
    OFF-flag resolution waitlist.start() uses, legacy alias included)."""
    from app.core import config
    return config.get("TURNOS_WAITLIST_OFF", default="",
                      legacy="TURNOS_LISTA_ESPERA_OFF") != "1"


def _reminders_enabled():
    """ONE truth for 'does this worker own the reminders sweep' (same
    OFF-flag resolution reminders.start() uses, legacy alias included)."""
    from app.core import config
    return config.get("TURNOS_REMINDERS_OFF", default="",
                      legacy="TURNOS_RECORDATORIOS_OFF") != "1"


def _housekeeping_enabled():
    """ONE truth for 'does this worker own the housekeeping sweep' — used
    by the registry entry."""
    from app.core import config
    return config.get("TURNOS_HOUSEKEEPING_OFF") != "1"


def _referrals_enabled():
    """ONE truth for 'does this worker own the referral-retention sweep':
    the OFF flag plus the same retention<=0 explicit opt-out
    start_referral_sweep() applies (never 0-days-means-delete-today).
    Used by the registry entry."""
    from app.core import config
    if config.get("TURNOS_REFERRAL_SWEEP_OFF") == "1":
        return False
    from app.modules.turnos import booking_referrals
    return booking_referrals._referral_retention_days() > 0


def _data_backups_enabled():
    """One truth for 'does this worker own the data-volume backup'."""
    from app.ops import data_backups
    return data_backups._env("TURNOS_BACKUP_OFF") != "1" and data_backups.configured()


def _waitlist_cadence():
    from app.modules.turnos import waitlist
    return f"every {waitlist._interval_min()} min"


def _reminders_cadence():
    from app.notifications import reminders
    return f"every {reminders._interval_min()} min"


def _housekeeping_cadence():
    from app.ops import housekeeping
    return f"every {housekeeping._interval_h()}h"


def _referrals_cadence():
    from app.modules.turnos import booking_referrals
    return (f"every {booking_referrals._referral_sweep_interval_h()}h, "
            f"retention {booking_referrals._referral_retention_days()}d")


def _data_backups_cadence():
    from app.ops import data_backups
    return f"every {data_backups._interval_min()} min after completion"


def _register_waitlist(app):
    from app.modules.turnos import waitlist
    waitlist_queue.register(app, waitlist._interval_min())


def _register_reminders(app):
    from app.notifications import reminders
    reminders_queue.register(app, reminders._interval_min())


def _register_housekeeping(app):
    from app.ops import housekeeping
    housekeeping_queue.register(app, housekeeping._interval_h() * 60)


def _register_referrals(app):
    from app.modules.turnos import booking_referrals
    referrals_queue.register(app, booking_referrals._referral_sweep_interval_h() * 60)


def _register_data_backups(app):
    from app.ops import data_backups, purge_guard
    purge_guard.warn_purge_ignored("worker start")
    data_backups_queue.register(app, data_backups._interval_min())


def _chart_summary_enabled():
    """ONE truth for 'does this worker own the chart-summary pre-generation'
    — delegates to the twin's own helper (chart_summary._pregen_enabled,
    plain-environ resolution, exactly what start() uses), so the two can
    never diverge."""
    from app.ai import chart_summary
    return chart_summary._pregen_enabled()


def _translate_warmup_enabled():
    """ONE truth for 'does this worker own the translation-cache warmup':
    the OFF flag alone. Language eligibility is deliberately NOT a
    boot-time registration decision — the sweep body re-resolves
    `pending_languages()` on EVERY tick (exactly like the thread loop
    does per pass), so enabling a second language from the panel reaches
    the next sweep without a worker restart; with no second language the
    tick is a true no-op. Freezing eligibility here would strand a
    just-enabled language until someone bounced the worker."""
    from app.ops import translate_warmup
    return not translate_warmup._off()


def _chart_summary_cadence():
    from app.ai import chart_summary
    return f"every {chart_summary._tick_min()} min"


def _translate_warmup_cadence():
    from app.ops import translate_warmup
    return f"every {translate_warmup._interval_min() // 60}h"


def _register_chart_summary(app):
    from app.ai import chart_summary
    chart_summary_queue.register(app, chart_summary._tick_min())


def _register_translate_warmup(app):
    from app.ops import translate_warmup
    translate_warmup_queue.register(app, translate_warmup._interval_min())


def _hsi_sync_enabled():
    """ONE truth for 'does this worker own the durable HSI journal sweep'
    — delegates to the thread twin's own OFF resolution
    (hsi_journal._off()) and its EMR-selection resolution
    (hsi_journal._emr_selected()), so worker and thread topology can
    never diverge. No selected connector ⇒ the job is not registered at
    all: no task, no startup kick, nothing for the watchdog to keep
    alive (EMR boundary slice, 2026-09-01)."""
    from app.modules.turnos import hsi_journal
    return not hsi_journal._off() and hsi_journal._emr_selected()


def _hsi_sync_cadence():
    from app.modules.turnos import hsi_journal
    # :g strips the trailing ".0" _interval_min()'s float parse leaves on
    # the common case (whole-minute env values) without truncating a
    # genuinely fractional test-seam value.
    return f"every {hsi_journal._interval_min():g} min"


def _register_hsi_sync(app):
    from app.modules.turnos import hsi_journal, hsi_journal_queue
    hsi_journal_queue.register(app, hsi_journal._interval_min())


# THE registry. Order = registration order = the consumed-queues order the
# composition tests pin. Cadence/OFF-flag resolution stays with each thread
# twin's own helpers (never re-parsed here), so flipping topology never
# changes cadence. The `rerun` texts are the per-queue audit that used to
# live in _recover_stalled_jobs' docstring — one surface now, English like
# every value the --jobs interface exposes (Codex language gate; legacy
# Spanish log prose elsewhere in this file stays). Batch-2 provenance
# (2026-08-08, narrowed round 8): housekeeping = table purges ONLY (the
# disk check with its advisory email stays on the web process's legacy
# schedule — see housekeeping.start()); filesystem ownership is declared
# per registry entry.
def _register_backup_full(app):
    from app.ops import backup_full_queue, purge_guard
    purge_guard.warn_rotation_ignored("worker start")
    return backup_full_queue.register(app)


def _backup_full_enabled():
    from app.core import backup_settings
    return backup_settings.configured()


_JOBS = (
    _JobSpec(
        id="backup_full", label="encrypted installation backups",
        queue="backup_full", task_name="ops.backup_full.run",
        enabled=_backup_full_enabled,
        off_reason="disabled or encrypted backup destination/key unconfigured",
        cadence=lambda: "hourly due check; daily backup by default",
        register=_register_backup_full, filesystem=True, outbound=True,
        exclusive=True,
        rerun=("Session advisory lock serializes manual, thread and worker runs. "
               "Every attempt has a unique archive id and a durable evidence row. "
               "A killed upload can leave an unjournaled encrypted object, which "
               "is protected from retention; a later run takes a fresh snapshot. "
               "Retention is dry-run unless explicitly applied, requires newer "
               "restore-tested evidence and preserves three tested archives."),
    ),
    _JobSpec(
        id="waitlist", label="waitlist",
        queue=waitlist_queue._QUEUE, task_name=waitlist_queue._TASK_NAME,
        enabled=_waitlist_enabled,
        off_reason="disabled (TURNOS_WAITLIST_OFF=1)",
        cadence=_waitlist_cadence, register=_register_waitlist,
        filesystem=False, outbound=True,
        rerun=("MUTATIONS (offer/expire) are status-guarded state "
               "transitions: a re-run duplicates nothing (run-twice "
               "proven in TestRerunContractsRunTwice). An offer's "
               "NOTIFICATION is not durably coupled to its row: a death "
               "between the offer insert and the send leaves a live offer "
               "whose re-run does not resend; only its expiry advances the "
               "queue. Pre-existing exposure of the thread path, recorded "
               "for the still-untaken business-queue activation decision."),
    ),
    _JobSpec(
        id="reminders", label="reminders",
        queue=reminders_queue._QUEUE, task_name=reminders_queue._TASK_NAME,
        enabled=_reminders_enabled,
        off_reason="disabled (TURNOS_REMINDERS_OFF=1)",
        cadence=_reminders_cadence, register=_register_reminders,
        filesystem=False, outbound=True,
        rerun=("each row is claim-CASed (db_booking.claim_reminder / "
               "db_booking.claim_interview_reminder) BEFORE the send, so a re-run "
               "finds the claim taken and sends nothing: at most one mail "
               "per reminder; a death inside the claim->send window loses "
               "THAT reminder (preferred over duplicating it). Race-proven "
               "in test_integrity_races.py item 5; claim-twice pinned in "
               "TestRerunContractsRunTwice. A worker stop (core.worker_stop) "
               "ends the sweep before the next claim: unclaimed rows stay "
               "for the next sweep."),
    ),
    _JobSpec(
        id="housekeeping", label="table hygiene",
        queue=housekeeping_queue._QUEUE, task_name=housekeeping_queue._TASK_NAME,
        enabled=_housekeeping_enabled,
        off_reason="disabled (TURNOS_HOUSEKEEPING_OFF=1)",
        cadence=_housekeeping_cadence, register=_register_housekeeping,
        filesystem=False, outbound=False,
        rerun=("purges-only since round 8: five DELETEs of already-old "
               "rows; a re-run deletes nothing new (run-twice proven in "
               "TestRerunContractsRunTwice). No filesystem, no outbound "
               "(the disk check with its advisory mail runs web-side "
               "only). SIGKILL mid-task proven in TestBatch2CrashRecovery."),
    ),
    _JobSpec(
        id="referrals", label="referral retention",
        queue=referrals_queue._QUEUE, task_name=referrals_queue._TASK_NAME,
        enabled=_referrals_enabled,
        off_reason=("disabled (TURNOS_REFERRAL_SWEEP_OFF=1 "
                    "or TURNOS_REFERRAL_RETENTION_DAYS<=0)"),
        cadence=_referrals_cadence, register=_register_referrals,
        filesystem=True, outbound=False,
        rerun=("file delete + pointer NULL are both idempotent: a missing "
               "file still clears the pointer; a cleared pointer is no "
               "candidate (run-twice proven in TestRerunContractsRunTwice). "
               "SIGKILL mid-task proven in TestBatch2CrashRecovery."),
    ),
    _JobSpec(
        id="data_backups", label="data-volume backups",
        queue=data_backups_queue._QUEUE, task_name=data_backups_queue._TASK_NAME,
        enabled=_data_backups_enabled,
        off_reason="disabled (TURNOS_BACKUP_OFF=1 or B2 is unconfigured)",
        cadence=_data_backups_cadence, register=_register_data_backups,
        filesystem=True, outbound=True, startup=True,
        startup_delay_s=data_backups_queue._STARTUP_GRACE_S, exclusive=True,
        rerun=("the Postgres backup_journal records only bytes accepted by B2. "
               "The sweep holds a session advisory lock across snapshot, remote "
               "acceptance and journal commit, so web/worker topology skew cannot "
               "overlap; a kill after remote acceptance but before commit safely "
               "re-uploads and converges to one journal row. The only remote "
               "deletion is the purge of files the referral retention journaled "
               "in purged_files (exact file ids, tombstoned, retried until every "
               "version is gone; TURNOS_BACKUP_PURGE_REMOTE=0 disables it). No "
               "version pruning, restore, or other retention behaviour is part "
               "of this task."),
    ),
    # Batch-3 provenance (2026-08-08, AI-cache warmups): the first two
    # OUTBOUND-capable cache fills — their sweeps may call the external AI
    # provider (OpenRouter), which is exactly why batch 2 deferred them.
    # Neither touches the data volume (both write only Postgres cache
    # tables), so filesystem=False. Zero-egress/zero-spend proven under the armed kill switch
    # with a CONFIGURED dummy provider key in test_ai_cache_queue_proof.py.
    _JobSpec(
        id="chart_summary", label="chart summaries",
        queue=chart_summary_queue._QUEUE,
        task_name=chart_summary_queue._TASK_NAME,
        enabled=_chart_summary_enabled,
        off_reason="disabled (TURNOS_SUMMARY_PREGEN=0, or the demo without PREGEN=1)",
        cadence=_chart_summary_cadence, register=_register_chart_summary,
        filesystem=False, outbound=True, startup=True, startup_delay_s=0,
        exclusive=True,
        rerun=("cache fill only: each (dni, doctor) pair upserts ONE "
               "summary row, fingerprint-guarded, so a re-run regenerates "
               "only pairs whose sources changed and rewrites at most the "
               "same row, never a duplicate (run-twice proven in "
               "TestRerunContractsRunTwice). No outbound MESSAGE exists to "
               "lose or duplicate: the provider call is a read whose worst "
               "re-run cost is paying that one call again. SIGKILL "
               "mid-task proven in TestAiCacheCrashRecovery. A worker stop "
               "(core.worker_stop) ends the sweep before the next pair; the "
               "pairs left are filled by the next sweep."),
    ),
    _JobSpec(
        id="translate_warmup", label="catalog translation",
        queue=translate_warmup_queue._QUEUE,
        task_name=translate_warmup_queue._TASK_NAME,
        enabled=_translate_warmup_enabled,
        off_reason="disabled (TURNOS_TRANSLATE_WARMUP_OFF=1)",
        cadence=_translate_warmup_cadence, register=_register_translate_warmup,
        filesystem=False, outbound=True, startup=True, startup_delay_s=30,
        exclusive=True,
        rerun=("cache fill only: needs_translation filters by source hash "
               "and translate_batch upserts one row per (key, language), "
               "so a re-run translates only what is still stale and "
               "rewrites nothing fresh (run-twice proven in "
               "TestRerunContractsRunTwice). A death mid-batch leaves a "
               "partial cache the re-run completes; provider failures "
               "degrade to 0 translated, never an error. SIGKILL mid-task "
               "proven in TestAiCacheCrashRecovery. A worker stop "
               "(core.worker_stop) ends the sweep at the next batch, inside "
               "the supervisor's stop budget, and the next sweep translates "
               "the rest (TestSweepStopsOnSignal)."),
    ),
    # Batch-4 provenance (2026-08-09, durable HSI sync journal; narrowed on
    # review the same day): ONLY the durable bidirectional journal sweep
    # (backfill + outbox replay + reconciliation + resolver auto-close)
    # joins the registry. The thread/manual fallback (hsi_resync)
    # deliberately does NOT: it stays operator-controlled in every topology,
    # and TURNOS_QUEUE_WORKER never affects it. Its outbound rows nevertheless
    # use hsi_sync.intent_for_row() through enqueue_and_replay_row(), so the
    # existing durable journal owns exact replay and crash recovery without
    # turning the fallback into a second queue or inbound-reconciliation owner.
    _JobSpec(
        id="hsi_sync", label="EMR durable journal",
        queue=hsi_journal_queue._QUEUE, task_name=hsi_journal_queue._TASK_NAME,
        enabled=_hsi_sync_enabled,
        off_reason=("disabled (TURNOS_HSI_SYNC_OFF=1 "
                    "or no EMR connector is selected)"),
        cadence=_hsi_sync_cadence, register=_register_hsi_sync,
        filesystem=False, outbound=True, startup=True, startup_delay_s=0,
        exclusive=True,
        rerun=("every phase is idempotent per item: backfill_pending "
               "re-enqueues through the outbox's unique idempotency key "
               "(a repeat scan is harmless), outbox replay re-reads the "
               "persisted checkpoint before each mutation (drift becomes "
               "a durable conflict, never a double-push - HsiClient's "
               "patient+slot probe turns a post-crash replay into an "
               "idempotent confirmation), reconcile's inbox record is "
               "keyed by content hash (a repeat import is a no-op), and "
               "the resolver's actions are themselves re-run safe (dedup, "
               "reassign, sobreturno all re-check current state before "
               "acting). A re-run therefore converges to the same journal "
               "state, never a duplicate HSI mutation. SIGKILL mid-task: "
               "the stalled row is requeued and the same idempotent chain "
               "applies; a worker stop (core.worker_stop) ends the pass "
               "between phases and the next tick runs the phases left; "
               "the worst case is a crash between an HSI call "
               "returning 'ok' and the local upsert, which re-plays that "
               "one HTTP call, the SAME residual window the thread path "
               "always had (HSI is not itself transactional with Sana's "
               "Postgres) - proven with a genuine SIGKILL inside that "
               "exact window (TURNOS_TEST_HSI_HOLD_AFTER_ACCEPT_S) in "
               "TestHsiSyncCrashRecoveryAndContention. Cadence: NOT a "
               "periodic cron like every other job here - hsi_sync_queue.py "
               "self-reschedules its own next tick from each completion "
               "(schedule_in, durable), exactly matching the thread's own "
               "wait-then-act spacing; a fixed cron grid cannot preserve "
               "that exactly (its two clocks - completion time and grid "
               "boundaries - are independent, so a guard on top of cron "
               "either fires early or stretches toward 2x, see the "
               "module's own docstring). scheduled_at itself is exact "
               "(computed in Postgres, no app-clock skew); actual pickup "
               "adds procrastinate's own small bounded polling delay, "
               "same as every other queued job here - not instant, not "
               "unbounded. Known bounded residual: a SIGKILL strictly "
               "between a tick completing and its self-reschedule landing "
               "breaks the chain until something re-kicks it - healed by "
               "the always-on steady-state watchdog "
               "(worker.maintenance.hsi_sync_watchdog, every 5 min, same "
               "queueing lock the chain itself reuses) within its own "
               "cadence, or sooner by the next worker boot if that "
               "happens first - narrow, one-time per crash, not a "
               "steady-state cost."),
    ),
)


def _register_jobs(app):
    """Composes every enabled `_JOBS` entry onto the ONE shared app, in
    registry order, then the maintenance tasks (janitor + stalled-job
    recovery, always on). Returns the queue names to consume — the
    maintenance queue makes the list structurally non-empty."""
    colas = []
    for spec in _JOBS:
        if not spec.enabled():
            _log(f"{spec.label}: {spec.off_reason}")
            continue
        spec.register(app)
        colas.append(spec.queue)
        _log(f"{spec.label}: {spec.cadence()}")
    colas.append(_MAINT_QUEUE)
    _register_janitor(app, colas)
    _register_recovery(app, colas)
    # No EMR connector means no journal task, startup kick *or watchdog*.
    # Registering the periodic task only to make it no-op would still create
    # an EMR lifecycle surface in an unconfigured Core installation.
    if _hsi_sync_enabled():
        _register_hsi_sync_watchdog(app)
    _register_data_backups_watchdog(app)
    from app.core import launch_signals
    if launch_signals.any_active():
        _register_launch_signals(app)
    return colas


def _audit_jobs():
    """The registry, machine-readable — what `python3 worker.py --jobs`
    prints (keys in English: this is a developer/operator interface, not
    log prose). READ-ONLY and honest about its resolution: enabled() and
    cadence() go through the modules' own helpers, which resolve flags
    the same way the running worker does — DB override > env > .env
    (core.config.get). With a reachable DATABASE_URL that means a
    read-only settings lookup (the CLI entrypoint closes the pool before
    exiting — no writes, no schema apply, no procrastinate import, clean
    stderr under -W error::ResourceWarning); with no database configured
    the resolution degrades to env/.env and the audit still works
    (TestJobRegistry proves both shapes with real subprocesses)."""
    jobs = []
    for spec in _JOBS:
        jobs.append({
            "id": spec.id, "label": spec.label, "queue": spec.queue,
            "task": spec.task_name, "enabled": spec.enabled(),
            "filesystem": spec.filesystem, "outbound": spec.outbound,
            "startup": spec.startup,
            "startup_delay_s": spec.startup_delay_s,
            "exclusive": spec.exclusive,
            "cadence": spec.cadence() if spec.enabled() else None,
            "rerun": spec.rerun,
        })
    return {
        "mode": settings.ProcessSettings.current().queue_worker or None,
        "jobs": jobs,
        "maintenance": {
            "queue": _MAINT_QUEUE,
            "tasks": list(_maint_tasks()),
            "retention_h": _JOB_RETENTION_H,
            "rerun": _MAINT_RERUN,
        },
    }

# Contrato de salida con el supervisor (asgi_main): 0 = apagado con gracia;
# 1 = fallo TRANSITORIO (Postgres inalcanzable, error del loop) — el
# supervisor reintenta con backoff; 2 = configuración inválida PERMANENTE
# (sin DATABASE_URL, intervalo no expresable en cron) — reintentar no la
# arregla, el supervisor NO reintenta y lo dice fuerte (ronda Codex,
# 2026-08-08); 3 = una SEGUNDA señal SIGTERM/SIGINT forzó una salida
# inmediata en medio del apagado — el supervisor SÍ reintenta (como el 1:
# no es un problema de configuración), separado sólo para observabilidad.
EXIT_TRANSIENT = 1
EXIT_CONFIG = 2
EXIT_FORCE_KILL = 3


def _force_exit(code, signum):
    """Deliberate, immediate process exit for a SECOND (or later)
    SIGTERM/SIGINT — called by both `main()`'s early plain-signal handler
    and `_boot_and_run`'s own async handler, so the two phases behave
    identically. `os._exit()` bypasses Python's exception machinery
    entirely: no exception is raised anywhere, no traceback, no `finally`
    block runs, unlike restoring the signal's OS default disposition and
    letting the OS/interpreter do whatever that signal's default happens
    to be — silent termination for SIGTERM, but `KeyboardInterrupt` (a
    normal, catchable Python exception) for SIGINT, reachable at ANY
    unprotected point of execution (see the module docstring and
    docs/solutions/dev/worker-signal-ownership-not-task-cancel.md). A
    separate, trivially patchable function (not inlined at each call
    site) so tests can prove "the code path that forces an exit was
    reached, with the right arguments" without literally terminating the
    test process — production code never patches it, so the real
    behavior tested end-to-end is always the real `os._exit()`, proven by
    the real-subprocess regression test."""
    _log(f"segunda señal recibida ({signal.Signals(signum).name}) — salida "
         f"inmediata y deliberada (os._exit, código {code}); ninguna "
         "excepción se propaga, ningún finally corre a partir de acá")
    os._exit(code)


def _restore_main_signal_handlers(loop, handlers):
    """Remove asyncio callbacks without exposing default dispositions.

    ``loop.remove_signal_handler()`` correctly restores SIGTERM to
    ``SIG_DFL`` and SIGINT to ``default_int_handler``.  That is unsafe while
    ``main()`` is still live: a signal between that removal and main's outer
    ``finally`` used to bypass the exit-code contract.  Block both signals,
    perform the *real* asyncio removal, restore main's plain handler, then
    unblock.  A signal landing in that interval stays pending and is
    delivered to the restored shared-state handler, never to a default.

    This path is POSIX-only by construction: asyncio only installed these
    handlers from the main thread on a POSIX signal loop.  The fallback is
    retained for non-POSIX direct-call seams where asyncio did install one.
    """
    signums = tuple(handlers)
    if hasattr(signal, "pthread_sigmask"):
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, signums)
        try:
            for signum, handler in handlers.items():
                loop.remove_signal_handler(signum)
                signal.signal(signum, handler)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        return
    for signum, handler in handlers.items():
        loop.remove_signal_handler(signum)
        signal.signal(signum, handler)


def _install_async_signal_handlers(loop, handle_signal, signal_state):
    """Hand SIGTERM/SIGINT from main()'s plain handler to asyncio so that
    ONE signal is counted ONCE.

    ``loop.add_signal_handler()`` first arms ``signal.set_wakeup_fd()`` and
    only then replaces the plain handler with asyncio's no-op.  A signal
    tripped between those two steps reaches both channels: the plain
    handler (still installed) and the wakeup byte that later becomes the
    asyncio callback.  Both feed the same ``_SignalState`` counter, so one
    SIGTERM used to read as two and took the deliberate second-signal exit
    (``EXIT_FORCE_KILL``) instead of a graceful stop.

    Two guards, one per way the kernel can deliver it:

    * The main thread blocks both signals for the whole handoff — the
      mirror of ``_restore_main_signal_handlers``.  A signal aimed at it
      stays pending and is delivered after the unmask, when asyncio already
      owns the signal: the wakeup byte only, exactly once.
    * A process-directed signal is handed to ANY thread that does not block
      it, and by now the worker has its health and DB-pool threads.  If
      one of them takes the signal while the main thread is still in the
      window, the plain handler still runs on the main thread AND the byte
      is written.  ``signal_state.handoff`` marks the window: a plain
      handler that runs inside it is recorded as an echo, and the asyncio
      callback for that same signal is absorbed instead of counted.  Only
      the first callback within ``_HANDOFF_ECHO_TTL_S`` is absorbed: the
      twin byte is read on the loop's next pass, while a genuine second
      signal (an operator's repeated Ctrl-C, seconds later) still forces
      the exit.  The cost is bounded and deliberate: a second signal
      within that second of one that landed in the few-microsecond window
      counts once — the same coalescing Python's own signal module applies
      to signals it has not yet dispatched.

    Regression: ``TestAsyncSignalHandoff`` (tests/test_queue_worker.py), one
    test per delivery path.  The non-POSIX fallback keeps the plain
    installation, as in ``_restore_main_signal_handlers``.
    """
    signums = (signal.SIGTERM, signal.SIGINT)
    signal_state.handoff = True
    try:
        if hasattr(signal, "pthread_sigmask"):
            previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, signums)
            try:
                for signum in signums:
                    loop.add_signal_handler(signum, handle_signal, signum)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        else:
            for signum in signums:
                loop.add_signal_handler(signum, handle_signal, signum)
    finally:
        signal_state.handoff = False


def _register_janitor(app, colas):
    """Daily ledger janitor (Fable review 2026-08-08, finding 5): at the
    default cadences the two sweeps write ~750 succeeded job rows a day
    into the shared hospital Postgres, and procrastinate prunes stalled
    WORKERS but never old JOBS. Succeeded/cancelled rows older than
    _JOB_RETENTION_H hours go away once a day (04:30 in the CONTAINER's
    clock — UTC under Docker, 01:30 ART); FAILED rows are deliberately
    kept (they are the diagnosis trail, and rare). Scoped to `colas` —
    ONLY the queues THIS worker consumes — so a foreign/future queue's
    ledger is never swept by a worker that doesn't own it. Registered
    unconditionally, which also guarantees the consumed-queues list is
    never empty (see main()'s queues note).

    ASYNC and awaited on purpose (the product owner, 2026-08-08, reproduced on the
    first draft): `JobManager.delete_old_jobs` is a coroutine function —
    a sync body calling it without await returns an unawaited coroutine,
    deletes NOTHING, and still reports success. procrastinate runs async
    task bodies natively on the worker loop. Proven by
    test_queue_worker.py::TestJanitorRetention against real rows.

    Retention semantics, explicit: deleted = succeeded + cancelled older
    than the retention window, in this worker's queues. KEPT forever (by
    deliberate choice, same diagnosis-trail reasoning as failed): failed
    AND aborted rows — `delete_old_jobs` supports `include_aborted` and
    this janitor intentionally does not pass it; an aborted job is a
    human/operator intervention worth keeping the trace of."""
    barre = list(colas)

    @app.periodic(cron="30 4 * * *")
    @app.task(queue=_MAINT_QUEUE, name="worker.maintenance.remove_old_jobs", retry=False)
    async def _janitor(timestamp):  # noqa: ANN001 — periodic tasks receive the unix ts
        for cola in barre:
            await app.job_manager.delete_old_jobs(nb_hours=_JOB_RETENTION_H,
                                                  queue=cola,
                                                  include_cancelled=True)
        return {"colas": barre, "retencion_h": _JOB_RETENTION_H}

    return _janitor


_STALLED_FLOOR_S = 30.0


def _test_seams_enabled():
    """Master gate for every TURNOS_TEST_* seam this process honors
    (Codex gate, round 4). The implementation moved to
    `queue_infra.test_seams_enabled` in the registry-hardening round —
    the queue modules' hold seam needs the SAME gate and used to keep
    private copies; this alias keeps worker-local call sites and the
    docstring trail."""
    from app.core import queue_infra
    return queue_infra.test_seams_enabled()


def _stalled_seconds():
    """Heartbeat-staleness threshold for stalled-job recovery (seconds).
    Default 30 matches procrastinate's own default and is 3× the ~10 s
    worker heartbeat — a HEALTHY worker's in-flight jobs are never
    considered stalled. Raises ValueError on a non-numeric value —
    main() turns that into exit 2 (fail closed), never a silent default.
    Values below the floor are rejected at startup (main()) unless the
    test-only seams gate is on — see the validation there. Parsed by
    `core.settings.WorkerSettings`; its ValueError propagates to main()."""
    return settings.WorkerSettings.current().stalled_s


def _allowed_task_names():
    """Task names this worker may RE-RUN after a crash: every ENABLED
    registry job's task plus the two maintenance tasks (Codex gate,
    registry round: queue scoping alone would let a foreign or
    not-yet-declared task sitting in an owned queue be requeued and
    re-executed without ever having declared a rerun contract — the exact
    hole the registry exists to close). Derived from the registry at call
    time, so it can never drift from what is actually registered."""
    permitidos = {spec.task_name for spec in _JOBS if spec.enabled()}
    permitidos.update(_maint_tasks())
    return permitidos


async def _recover_stalled_jobs(app, colas):
    """Requeue jobs stuck in 'doing' whose worker stopped heartbeating —
    the SIGKILL hole (Codex gate, 2026-08-08): procrastinate 3.9 fetches
    only status='todo', and a worker killed mid-task leaves its job
    'doing' forever (stalled-WORKER pruning merely nulls worker_id; no
    built-in path ever re-queues the JOB). Scoped to `colas` — ONLY the
    queues this worker consumes, so a foreign queue's stuck job is never
    touched by a worker that doesn't own its tasks.

    Concurrency-safe by the database's own guard: `retry_job` runs
    procrastinate_retry_job_v2, which takes the job row FOR UPDATE and
    flips it todo only from 'doing'/'failed' — two workers recovering at
    once serialize on the row lock, the loser raises and is caught here
    per-job. Before the flip the row's `queueing_lock` is released (NULL):
    retry_job keeps it, the unique index that enforces it only covers
    'todo' rows, and a startup defer that landed while this row sat stuck
    can already hold the same key — without the release the flip loses to
    UniqueViolation every single pass and the stuck row holds its
    execution lock forever (live demo 2026-09-11→15, translate sweep job
    32832). A job requeued this way re-RUNS its whole body, so every
    queue this recovery can touch must be re-run safe.

    The guarantee this recovery gives is exactly one thing: a stuck job
    is re-run, and the re-run never DUPLICATES a business mutation. It
    does NOT promise every queue's outbound NOTIFICATION is delivered —
    delivery semantics are per queue (Codex gate, round 7 — the earlier
    blanket "same business effect" claim was false for waitlist). The
    per-queue audit lives in ONE place since the registry-hardening
    round: each `_JOBS` entry's `rerun` field (plus `_MAINT_RERUN` for
    this maintenance queue itself) — a NEW queue states its re-run
    safety THERE before joining `colas`, and TestJobRegistry refuses a
    spec without one.

    For every registered sweep, a recovered re-run converges to a single
    business effect, proven mid-task-SIGKILL per queue: batch 2 in
    TestBatch2CrashRecovery, waitlist/reminders in
    TestBusinessQueueCrashRecovery. `retry=False` on the tasks is unrelated:
    that's the policy for a task that RAISES (fail, keep the diagnosis
    trail) — this recovery only targets 'doing' rows, and deliberately
    never resurrects 'failed'. Returns how many jobs were requeued."""
    umbral = _stalled_seconds()
    permitidos = _allowed_task_names()
    requeued = 0
    for cola in colas:
        try:
            stalled = await app.job_manager.get_stalled_jobs(
                queue=cola, seconds_since_heartbeat=umbral)
        except Exception as e:  # noqa: BLE001 — a read failure never kills the loop
            _log(f"recuperación: no se pudo leer {cola}: {log_pii.exc(e)}")
            continue
        for job in stalled:
            if job.task_name not in permitidos:
                # Scoped by TASK, not only by queue: a task this worker's
                # registry does not declare (foreign producer, or a
                # future job without a rerun contract) is never re-run by
                # it — it stays 'doing', loudly, for the worker that
                # knows it.
                _log(f"recuperación: job {job.id} ({job.task_name}) salteado "
                     "— tarea no declarada en el registro de este worker")
                continue
            try:
                # Antes de retry_job: soltar el queueing_lock de la fila.
                # retry_job la pasa doing→todo SIN tocar su queueing_lock, y
                # ese índice único cubre sólo las 'todo' — así que basta con
                # que un arranque posterior haya diferido su sweep de arranque
                # mientras esta estaba trabada (el dedup de la diferición no
                # ve las 'doing') para que el UPDATE choque con
                # UniqueViolation y la recuperación pierda SIEMPRE su propio
                # retry. Así estuvo la demo 4 días (2026-09-11→15): el sweep
                # de traducción muerto a mitad de job 32832 retenía el lock
                # de ejecución y las 18 barridas encoladas detrás nunca
                # corrieron. El queueing_lock sólo dedupa FILAS EN COLA; una
                # fila que sale de 'doing' ya no lo necesita — la ejecución
                # la serializa el lock de tarea.
                await app.connector.execute_query_async(
                    "UPDATE procrastinate_jobs SET queueing_lock = NULL "
                    "WHERE id = %(job_id)s AND status = 'doing'",
                    job_id=job.id)
                await app.job_manager.retry_job(job)
                requeued += 1
                _log(f"recuperación: job {job.id} ({job.task_name}) re-encolado "
                     f"tras worker muerto (intento {job.attempts + 1})")
            except Exception as e:  # noqa: BLE001 — another worker won the race
                _log(f"recuperación: job {job.id} no re-encolado ({log_pii.exc(e)})")
    return requeued


async def _defer_startup_jobs(app):
    """One startup sweep per enabled `startup=True` registry job,
    deferred at worker boot — the first-run contract the thread twins
    keep, preserved EXACTLY across the topology change: chart_summary
    ticks immediately (startup_delay_s=0, like its thread loop) and
    translate_warmup runs after its historical 30 s boot grace
    (startup_delay_s=30 = FIRST_RUN_DELAY_S, carried durably in the job
    row's scheduled_at via schedule_at = core.clock's boot instant + the
    delay, the same instant the row's `timestamp` argument carries — never
    a sleep). Without this a
    cold install would wait up to one whole cron interval (60 min / 6 h)
    for the first boundary.

    DURABLE: the defer is a Postgres row — a worker that dies after
    deferring still gets the sweep run, and a mid-run SIGKILL is
    recovered like any declared task.

    Two distinct locks, two distinct jobs (do not conflate):
    - The QUEUEING lock here dedups QUEUED rows only (procrastinate's
      unique index covers status='todo'): a supervisor restart loop
      that boots faster than the queue drains defers one row, not many
      (AlreadyEnqueued is caught — that is the dedup working).
    - EXECUTION overlap — two workers' startup sweeps, or a cron tick
      landing while a startup sweep still runs — is PREVENTED by
      procrastinate's native per-job `lock`, registered as each
      exclusive task's DEFAULT (so this configure() call inherits it —
      Task.configure falls back to the task's own lock when given only
      queueing_lock/schedule_at): a job whose lock string a running job
      holds is not fetched, it WAITS and runs after — closing the
      overlap half of the double-spend path; the serialized follow-up
      no-ops on unchanged sources (run-twice-proven fingerprint/hash
      guards), which is the other half. Proven with real two-worker
      races in TestAiCacheExecutionLock.

    Best-effort like the startup recovery: a failure here is logged and
    the periodic cron still owns the cadence.

    hsi_sync's own gate (review fix, 2026-08-09): it self-reschedules
    (hsi_sync_queue.py) instead of running on a periodic cron, so it is
    handled ENTIRELY separately from the generic defer path below —
    delegated to hsi_sync_queue.rekick(), which (a) skips outright via
    chain_alive() when a 'todo' OR 'doing' row already exists (a worker
    booting while a PREVIOUS worker's hsi_sync tick is still genuinely
    'doing', not crashed, must never queue a second one — the execution
    lock would only serialize it, not prevent the back-to-back tick the
    instant the first one finishes) and (b), when the chain is genuinely
    not alive, schedules the kick no earlier than the last successful
    completion plus the configured interval — never blindly "now" for a
    chain that has run before, which is what would reopen the same
    back-to-back-tick race through the timing window itself. See
    hsi_sync_queue.py's own module docstring for the full reasoning and
    the two-worker proof.

    hsi_sync's kick is DISPATCHED, never awaited inline here (bug fix,
    2026-08-09, found running the FULL local CI ladder under real system
    load, not the 4 HSI-focused modules alone): rekick() can legitimately
    block for up to its own 20 s lock-contention timeout
    (_acquire_rekick_lock) when two workers boot together and race for
    hsi_sync's coordination lock. This whole function runs BEFORE
    run_worker_async() (see _boot_and_run() below) — awaiting rekick()
    inline therefore let contention on hsi_sync's OWN lock delay this
    worker's ENTIRE job-consumption loop from starting, head-of-line-
    blocking every OTHER queue (chart_summary, waitlist, ...) behind
    hsi_sync's own coordination. See _dispatch_hsi_sync_rekick()'s own
    docstring for the fix and its three explicit guarantees (visible
    exceptions, GC-safety, accounted-for shutdown) — because hsi_sync's
    outcome is no longer known synchronously, it is also no longer
    counted in this function's own `deferred` return value; it logs its
    own outcome independently once it resolves."""
    from datetime import timedelta as _timedelta
    from procrastinate import exceptions as _pexc
    from app.core import clock
    from app.modules.turnos import hsi_journal_queue as _hsi_sync_queue
    from app.ops import data_backups_queue as _data_backups_queue
    # One instant for every startup row: the job's `timestamp` argument and
    # its scheduled_at come from the same clock read (core.clock, which a
    # test can freeze), so the grace is exactly startup_delay_s after it.
    ahora = clock.utcnow()
    deferred = 0
    for spec in _JOBS:
        if not (spec.startup and spec.enabled()):
            continue
        if spec.task_name == _hsi_sync_queue._TASK_NAME:
            _dispatch_hsi_sync_rekick(app, spec)
            continue
        if spec.task_name == _data_backups_queue._TASK_NAME:
            _dispatch_data_backups_rekick(app, spec)
            continue
        try:
            deferrer = app.tasks[spec.task_name].configure(
                queueing_lock=f"startup:{spec.task_name}",
                schedule_at=(ahora + _timedelta(seconds=spec.startup_delay_s)
                             if spec.startup_delay_s else None),
            )
            await deferrer.defer_async(timestamp=int(ahora.timestamp()))
            deferred += 1
        except _pexc.AlreadyEnqueued:
            _log(f"startup sweep {spec.label}: ya en cola (dedup por "
                 "queueing lock) — no se duplica")
        except Exception as e:  # noqa: BLE001 — best-effort, el cron periódico sigue
            _log(f"startup sweep {spec.label}: no se pudo encolar ({log_pii.exc(e)})")
    return deferred


def _dispatch_hsi_sync_rekick(app, spec):
    """Fires hsi_sync's own boot-time rekick() as a background asyncio
    task instead of `await`ing it inline in `_defer_startup_jobs()`'s
    loop (see that function's own docstring for the head-of-line-
    blocking bug this closes). Returns the created Task. Three explicit
    guarantees, each independently tested (test_queue_worker.py's
    TestHsiSyncStartupDecoupling) — "fire and forget" alone would satisfy
    NONE of them:

    1. Exceptions stay VISIBLE. The task's own coroutine body wraps the
    rekick() call in the SAME try/except this loop used to have inline,
    logging the SAME message
    (f"startup sweep {spec.label}: rekick explotó ({e!r})"). Consumed
    HERE, deliberately — an exception that escaped this wrapper would
    instead reach asyncio's own default unhandled-task-exception
    handler, which only prints "Task exception was never retrieved" to
    stderr on garbage collection: easy to miss, and NOT this codebase's
    established `_log(...)` convention every other startup sweep uses.

    2. GC-safety. `asyncio.create_task()`'s own return value is the ONLY
    strong reference by default — the event loop itself keeps just a
    WEAK one (for `asyncio.all_tasks()`'s bookkeeping), so an
    unreferenced Task can be garbage-collected mid-flight, a real and
    documented asyncio gotcha, not hypothetical. Kept alive in the
    module-level `_HSI_SYNC_STARTUP_TASKS` set, discarded via
    `add_done_callback` the instant it finishes either way.

    3. Shutdown accounts for it. This task runs on the SAME event loop as
    `run_worker_async()`, but is NOT one of procrastinate's own
    job-processing tasks, so `run_worker_async()`'s own
    `shutdown_graceful_timeout` never waits for or cancels it — without
    `_drain_hsi_sync_startup_tasks()` (called once, explicitly, in a
    `finally` right after `run_worker_async()` returns OR raises — see
    `_boot_and_run()`), a still-pending kick would be silently abandoned:
    no log, no trace, just gone. See that function's own docstring for
    the deliberate policy (cancel immediately, then a bounded wait —
    inside, WITH real headroom before, the outer supervisor's own
    SIGKILL margin, never a fresh full allocation of it — to observe
    that cancellation land, logged either way, never silence)."""
    import asyncio
    from app.modules.turnos import hsi_journal_queue as _hsi_sync_queue

    async def _run():
        try:
            if await _hsi_sync_queue.rekick(app):
                _log(f"startup sweep {spec.label}: encolado en segundo plano")
            else:
                _log(f"startup sweep {spec.label}: cadena ya viva o el "
                     "rekick no se pudo encolar")
        except Exception as e:  # noqa: BLE001 — misma convención que el resto de _defer_startup_jobs
            _log(f"startup sweep {spec.label}: rekick explotó ({log_pii.exc(e)})")

    task = asyncio.create_task(_run())
    _HSI_SYNC_STARTUP_TASKS.add(task)
    task.add_done_callback(_HSI_SYNC_STARTUP_TASKS.discard)
    return task


def _dispatch_data_backups_rekick(app, spec):
    """Run the backup chain's potentially-contended boot check off-path.

    Like HSI's kick, backup bootstrap holds a session advisory lock while it
    checks and defers.  It must not delay every other worker queue at boot.
    Keep a strong reference and let the existing shutdown drain account for
    the task rather than abandoning it during worker teardown.
    """
    import asyncio
    from app.ops import data_backups, data_backups_queue

    async def _run():
        try:
            kicked = await data_backups_queue.rekick(
                app, interval_min=data_backups._interval_min(),
                startup_delay_s=spec.startup_delay_s)
            _log(f"startup sweep {spec.label}: "
                 f"{'enqueued' if kicked else 'chain already live or not queued'}")
        except Exception as e:  # noqa: BLE001 - visible, then watchdog heals
            _log(f"startup sweep {spec.label}: rekick failed ({log_pii.exc(e)})")

    task = asyncio.create_task(_run())
    _BACKUP_STARTUP_TASKS.add(task)
    task.add_done_callback(_BACKUP_STARTUP_TASKS.discard)
    return task


async def _drain_hsi_sync_startup_tasks():
    """Called ONCE, right after `run_worker_async()` returns or raises
    (worker.py's own `_boot_and_run()`, inside a `finally` so it runs on
    the crash path too) — the deliberate policy for whatever
    `_dispatch_hsi_sync_rekick()` tasks are still pending at that point:
    cancel them and account for what happens. The one outcome that must
    never happen is the silent-drop: the process exiting with the task
    simply abandoned, no trace.

    This is ONLY the boot-time kick, and it gets NO wait-for-natural-
    completion phase of its own (an earlier draft gave it a full second
    `timeout_s`-sized window before cancelling — review catch: that
    double-spent the OUTER supervisor's budget). The real budget is
    asgi_main.py's own `_terminar`: `proc.wait(drain +
    queue_infra.SUPERVISOR_SIGKILL_MARGIN_S)` before SIGKILL, where
    `drain` is `TURNOS_WORKER_GRACEFUL_TIMEOUT` — the SAME seconds
    `run_worker_async(shutdown_graceful_timeout=graceful)` already spent,
    by the time it returns, waiting for whatever real jobs were in
    flight. hsi_sync's kick isn't one of those tracked jobs, so none of
    that time was ever spent waiting on IT — which means by the time
    this function runs, only `queue_infra.SUPERVISOR_SIGKILL_MARGIN_S`
    of headroom is actually left before the outer supervisor's own
    SIGKILL. So: cancel immediately, then observe cancellation within
    `SUPERVISOR_SIGKILL_MARGIN_S - queue_infra.HSI_SYNC_DRAIN_HEADROOM_S`
    — deliberately LESS than the full remaining margin (a second review
    catch: waiting the FULL margin would land this function's own
    deadline at EXACTLY the same instant as the supervisor's SIGKILL, not
    safely before it — signal delivery, event-loop teardown and log
    flushing all cost genuine wall-clock time on either side of that
    boundary, and racing it means the SIGKILL can win before the
    "cancelled, logged" outcome below is ever observed, which is the
    exact silent-drop this function exists to prevent). See
    HSI_SYNC_DRAIN_HEADROOM_S's own comment in queue_infra.py for why
    it's a separate constant, not folded into the supervisor margin.

    An earlier draft used `asyncio.wait_for(task, timeout)` and claimed
    that made the wait "bounded". That claim was ALSO false (a separate
    review catch): `wait_for` cancels the task on timeout but then AWAITS
    that cancellation with NO further bound of its own — a coroutine
    that suppresses or delays `CancelledError` holds `wait_for` open
    indefinitely. `asyncio.wait(..., timeout=...)` does not have this
    problem: it returns at the deadline regardless of whether the
    watched task(s) finished, cancelled or not.

    The remaining, load-bearing qualifier: this function's own bounded
    return does NOT by itself bound the WHOLE process's shutdown if a
    task suppressed `CancelledError` and kept running — `asyncio.run()`'s
    own final teardown (`Runner.close()`'s `_cancel_all_tasks`, which
    gathers every remaining task with NO timeout of its own) would hang
    on it, and no asyncio primitive can force a non-cooperative coroutine
    to yield control back to the loop. That residual case is a genuine
    hard boundary OUTSIDE this function and outside Python: the
    supervisor's SIGKILL is what actually stops it, exactly the
    `drain + SUPERVISOR_SIGKILL_MARGIN_S` ceiling this function is
    designed to fit inside of, not escape.

    This scenario does not arise on the actual hsi_sync path today:
    every `except` clause reachable from `_dispatch_hsi_sync_rekick()`'s
    task, in both `worker.py` and `connectors/hsi/hsi_sync_queue.py`,
    catches `Exception`, a specific `ValueError`/`OSError`, or a named
    procrastinate exception — never a bare `except:` or
    `except BaseException`. `asyncio.CancelledError` has been a
    `BaseException` subclass since Python 3.8 specifically so that
    `except Exception` clauses like these CANNOT swallow it — it always
    propagates. `TestHsiSyncStartupDecoupling.
    test_no_bare_or_baseexception_clauses_can_swallow_cancellation`
    checks this statically (so a future edit that adds a
    `except BaseException` fails loudly), and
    `test_shutdown_cancels_and_logs_pending_rekick_task` proves it
    empirically end to end: a REAL worker subprocess, rekick genuinely
    held, SIGTERM sent, and the process exits cleanly well within the
    ACTUAL asgi_main limit (`graceful + SUPERVISOR_SIGKILL_MARGIN_S`),
    including run_worker_async's own prior drain — the true contract
    this function and `_boot_and_run()`'s `finally` exist to uphold."""
    import asyncio
    from app.core import queue_infra as _queue_infra
    pending = [t for t in list(_HSI_SYNC_STARTUP_TASKS | _BACKUP_STARTUP_TASKS)
               if not t.done()]
    if not pending:
        return
    for task in pending:
        task.cancel()
    margen = (_queue_infra.SUPERVISOR_SIGKILL_MARGIN_S
              - _queue_infra.HSI_SYNC_DRAIN_HEADROOM_S)
    cancel_done, cancel_pending = await asyncio.wait(pending, timeout=margen)
    for _task in cancel_done:
        _log("hsi_sync: kick de arranque en curso cancelado al apagar "
             "(no terminó antes de que run_worker_async() retornara)")
    for _task in cancel_pending:
        _log(f"hsi_sync: kick de arranque no respondió a la cancelación "
             f"dentro del margen de {margen:g}s — el supervisor externo "
             f"SIGKILLeará el proceso junto con él")


def _make_async_signal_callback(signal_state, running):
    """The asyncio callback of `_boot_and_run`: before the `Worker` exists
    (`running["worker"]` is None) a signal only records the stop request;
    afterwards it asks the worker to stop. A second signal force-exits."""
    def _handle_signal(signum):
        if signal_state.take_handoff_echo(signum):
            return  # already counted by main()'s plain handler
        if not signal_state.request_stop(signum, "the async worker phase"):
            return
        # A synchronous sweep body runs in a thread procrastinate cannot
        # cancel: it ends itself at its next unit boundary (core.worker_stop),
        # inside the stop budget, and leaves the rest to its next run.
        worker_stop.request()
        worker = running["worker"]
        if worker is not None:
            _log("SIGTERM/SIGINT received — requesting worker shutdown "
                 "(Worker.stop(): just sets a flag its own loop checks at "
                 "its own safe points; never interrupts work already in "
                 "progress, unlike cancelling the task). A SECOND signal "
                 "now exits immediately and deliberately (os._exit — see "
                 "_force_exit()).")
            worker.stop()
        else:
            _log("SIGTERM/SIGINT received during startup (open/recover/"
                 "sweeps) — nothing is cancelled yet (these steps are "
                 "bounded; letting them finish on their own avoids "
                 "orphaned resources, see docstring); shutting down as "
                 "soon as the current step finishes. A SECOND signal now "
                 "exits immediately and deliberately (os._exit — see "
                 "_force_exit()).")

    return _handle_signal


async def _run_opened_pool(app, colas, nombre, graceful, signal_state, running):
    """`_boot_and_run`'s middle: with the pool open, recover stalled jobs,
    dispatch the startup sweeps, build the Worker and run it. A pending
    signal ends it quietly between any two steps; the startup kick tasks
    are drained on every path that got past the first check."""
    if signal_state.stop_requested:
        _log("apagando con gracia (pedido durante la apertura, "
             "antes de arrancar nada)")
        return
    try:
        try:
            recuperados = await _recover_stalled_jobs(app, colas)
            if recuperados:
                _log(f"recuperación al arrancar: {recuperados} "
                     "job(s) re-encolado(s)")
        except Exception as e:  # noqa: BLE001 — request-mode only, never cancelled
            _log(f"recuperación al arrancar falló ({log_pii.exc(e)}) — "
                 "la tarea periódica reintenta")
        if signal_state.stop_requested:
            _log("apagando con gracia (pedido durante la "
                 "recuperación al arrancar)")
            return
        try:
            arrancados = await _defer_startup_jobs(app)
            if arrancados:
                _log(f"startup: {arrancados} barrido(s) inmediato(s) "
                     "encolado(s)")
        except Exception as e:  # noqa: BLE001 — request-mode only, never cancelled
            _log(f"startup sweeps no encolados ({log_pii.exc(e)})")
        if signal_state.stop_requested:
            _log("apagando con gracia (pedido durante los "
                 "barridos de arranque)")
            return
        # Bootstrap fully done — construction, the readiness
        # flip, and `worker.run()` below are one synchronous
        # unit with no yield point between them (see docstring).
        app.perform_import_paths()  # matches run_worker_async's public contract
        running["worker"] = app._worker(
            queues=colas, name=nombre, listen_notify=False,
            install_signal_handlers=False,
            shutdown_graceful_timeout=graceful)
        worker = running["worker"]
        _guard_periodic_deferrer_cancellation(worker)
        _guard_heartbeat_update_cancellation(worker)
        _STATE["fase"] = "corriendo"
        _log(f"worker '{nombre}' corriendo (colas: {', '.join(colas) or '—'})")
        await worker.run()
    finally:
        await _drain_hsi_sync_startup_tasks()


async def _boot_and_run(app, colas, nombre, graceful, signal_state=None,
                        restore_signal_handlers=None):
    """The whole async lifecycle of one worker process: open the pool
    once, recover stalled jobs, dispatch startup sweeps, run the queue
    worker, close. A module-level function (not a nested closure of
    `main()`) so tests can call it directly against a real or fake `app`.

    `queues` is always the explicit list, never `None` — a worker with no
    registered task for a fetched job marks it permanently failed instead
    of leaving it for a worker that knows it. `finally: await
    _drain_hsi_sync_startup_tasks()` runs on every exit path, including
    this function raising — hsi_sync's startup kick is a background task
    nothing else awaits or cancels.

    ``main()`` passes one `_SignalState` plus its plain handler.  The early
    synchronous handler and the asyncio callback therefore share one
    request flag and one counter, and this coroutine restores that plain
    handler safely before it gives the event loop back to ``main()``.
    Direct callers keep the existing seam: with no state supplied this
    function creates a private state and restores the caller's exact
    prior SIGTERM/SIGINT dispositions after its temporary callbacks end.

    Before the `Worker` object exists ("request mode"), SIGTERM/SIGINT
    only sets `stop_requested`, checked between each bounded bootstrap
    step — it never cancels anything, because a raw `task.cancel()`
    landing inside `open_async()`'s pool creation, or inside
    `_recover_stalled_jobs`/`_defer_startup_jobs`, can abandon a
    partially-created resource with no code path left to close it. That's
    why `ctx = app.open_async()` is entered/exited by hand
    (`ctx.__aenter__()`/`ctx.__aexit__()`, never `async with
    app.open_async():`) — a failed or cancelled open still needs cleanup,
    handled in `_close_after_failed_open` below.

    Once the `Worker` exists, the same handler calls `worker.stop()`
    instead — a safe flag-set its own job-fetch loop reads at its own
    checkpoints, never an interrupt, unlike `task.cancel()` which can land
    mid-way through procrastinate's own unprotected bootstrap awaits
    (`prune_stalled_workers`/`register_worker` inside `Worker.run()`).
    That's also why this calls `app._worker(...)` + `await worker.run()`
    directly instead of `app.run_worker_async()` — it needs to own
    `worker.stop()` for the whole run, not hand off to procrastinate's own
    handler-installation path (`install_signal_handlers=False`).

    A SECOND SIGTERM/SIGINT is a deliberate force-kill escape hatch, not
    a repeat of the first — the shared `_SignalState` counts invocations
    and on the second (or later) call, exits via `_force_exit()`
    (`os._exit()`) instead of calling `worker.stop()`/setting
    `stop_requested` again. Round-9 first got this right by uninstalling
    the handler on the FIRST signal and restoring Python's default
    disposition, matching procrastinate's OWN `signals.on_stop()` — that
    fixed the "second SIGTERM is silently absorbed" bug, but an
    independent review of THAT fix found a follow-on: Python's default
    disposition is silent immediate termination for SIGTERM, but for
    SIGINT specifically it's `signal.default_int_handler`, which RAISES
    `KeyboardInterrupt` as a normal, catchable exception at whatever
    point of execution the interpreter happens to be — a rapid double
    SIGINT could crash with an uncaught traceback and a non-contract exit
    code instead of taking a documented code. Fixed by never falling
    through to OS default disposition at all: this function's own handler
    stays installed for the SAME async handlers' entire lifetime (no
    longer removed on the first signal — only in the outer `finally`
    below, on normal exit), and decides its own behavior per-invocation
    via the counter, uniformly for both signals. See the module docstring
    and docs/solutions/dev/worker-signal-ownership-not-task-cancel.md.
    Regression: `test_second_signal_forces_exit_not_default_disposition`.

    Regression coverage: `test_bootstrap_phase_ordering_no_signal`,
    `test_early_sigterm_aborts_bootstrap_before_worker_exists`,
    `test_signal_during_run_calls_worker_stop_not_task_cancel` (all in
    `tests/test_queue_worker.py`, class `TestHsiSyncStartupDecoupling`);
    one signal during the handler handoff itself: `TestAsyncSignalHandoff`."""
    import asyncio
    import signal

    loop = asyncio.get_running_loop()
    handlers_installed = False
    signal_state = signal_state or _SignalState()
    if restore_signal_handlers is None:
        # A direct caller owns neither main()'s early handler nor its outer
        # finally. Snapshot the exact caller dispositions before asyncio
        # installs its callbacks so the common masked remove-and-restore
        # handoff below can return the process to precisely that state.
        restore_signal_handlers = {
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
            signal.SIGINT: signal.getsignal(signal.SIGINT),
        }
    running = {"worker": None}
    # One stop per run for the long synchronous sweeps (core.worker_stop):
    # set by the signal callback, closed in the outer finally so the next
    # run in this process (an in-process test) starts unset.
    parada = worker_stop.begin()
    if signal_state.stop_requested:
        worker_stop.request()
    _handle_signal = _make_async_signal_callback(signal_state, running)

    # POSIX signal handling only works from the main thread (same
    # precondition procrastinate's own signals.on_stop() checks) —
    # _boot_and_run is also called directly by tests, not just main().
    # signum is passed explicitly (loop.add_signal_handler's own *args
    # forwarding) so _handle_signal/_force_exit can log and pass through
    # which signal actually fired, instead of assuming. The handoff from
    # main()'s plain handler is masked and de-duplicated so one signal in
    # the middle of it is counted once (_install_async_signal_handlers).
    if threading.current_thread() is threading.main_thread():
        _install_async_signal_handlers(loop, _handle_signal, signal_state)
        handlers_installed = True

    async def _close_after_failed_open():
        # Safe unconditionally: close_async() checks `if not self.
        # _async_pool: return` before closing, whether __aenter__ failed
        # after partially assigning the pool or never assigned it at
        # all. A cleanup-step error is logged, never masks the real one.
        try:
            await ctx.__aexit__(None, None, None)
        except Exception as close_err:  # noqa: BLE001
            _log(f"cierre del pool tras un open() fallido también "
                 f"falló ({log_pii.exc(close_err)}) — se propaga el error de "
                 "apertura original de todos modos")

    try:
        ctx = app.open_async()
        try:
            await ctx.__aenter__()  # request-mode only — never cancelled
        except asyncio.CancelledError:
            # Never `except BaseException` (this file's static gate
            # forbids it) — CancelledError must keep propagating, just
            # through the same cleanup as any other open failure.
            await _close_after_failed_open()
            raise
        except Exception:
            await _close_after_failed_open()
            raise
        try:
            await _run_opened_pool(app, colas, nombre, graceful, signal_state, running)
        finally:
            # __aexit__ is `close_coro` == PsycopgConnector.close_async —
            # unconditional, whatever path got us here (an early return,
            # normal completion, or the block above raising). This is
            # exactly the step ronda 4's bug skipped by never reaching a
            # successful `async with` entry in the first place; calling it
            # by hand here (never through `async with app.open_async():`
            # directly) is what makes it reachable on every exit path
            # that reaches this point — i.e. `__aenter__` DID succeed
            # (the failure path above has its own cleanup, since this
            # `finally` is never reached if `__aenter__` raised).
            await ctx.__aexit__(None, None, None)
    finally:
        worker_stop.end(parada)
        if handlers_installed:
            # Direct calls pass their exact pre-call dispositions above;
            # main() passes its shared early handler. Both need the same
            # masked real-removal + restore handoff so no default handler
            # is observable between asyncio teardown and restoration.
            _restore_main_signal_handlers(loop, restore_signal_handlers)


def _register_recovery(app, colas):
    """Steady-state half of the recovery: every 5 minutes, requeue stalled
    jobs of this worker's own queues. The other half runs ONCE at startup
    (main() below) so a REPLACEMENT worker recovers its predecessor's
    in-flight job as part of booting, instead of waiting for the next
    periodic pass (latency bounded by restart backoff + boot, never
    claimed instant)."""
    barre = list(colas)

    @app.periodic(cron="*/5 * * * *")
    @app.task(queue=_MAINT_QUEUE, name="worker.maintenance.retry_stalled_jobs", retry=False)
    async def _recover(timestamp):  # noqa: ANN001 — periodic tasks receive the unix ts
        return await _recover_stalled_jobs(app, barre)

    return _recover


def _register_hsi_sync_watchdog(app):
    """Steady-state health check for hsi_sync's self-rescheduling chain
    (review fix, 2026-08-09): the chain's own reschedule (hsi_sync_queue.
    _sweep, at the end of each tick) can fail and be swallowed so the
    completing tick still succeeds — by design, so ONE scheduling hiccup
    never loses that tick's real result. Without an active watchdog the
    ONLY thing that would ever re-kick a chain broken that way is the
    NEXT WORKER BOOT (_defer_startup_jobs) — indefinite on a worker that
    stays up for weeks. It is registered only while an EMR connector is
    selected and sync is enabled; every 5 minutes it re-checks hsi_sync's
    OWN off-flag and EMR-connector selection live (never frozen at
    registration, same discipline translate_warmup's per-tick language
    re-check already uses) and, if enabled, calls
    hsi_sync_queue.rekick() — which itself checks
    chain_alive() at the SAME instant it computes the schedule time (not
    as a separate step here; see rekick()'s own docstring for why folding
    the check into the same function matters) and no-ops when a 'todo' OR
    'doing' row already exists. The SAME queueing lock the boot-time kick
    and the chain's own reschedule share makes this idempotent regardless."""
    @app.periodic(cron="*/5 * * * *")
    @app.task(queue=_MAINT_QUEUE, name="worker.maintenance.hsi_sync_watchdog", retry=False)
    async def _watchdog(timestamp):  # noqa: ANN001 — periodic tasks receive the unix ts
        from app.modules.turnos import hsi_journal, hsi_journal_queue
        if hsi_journal._off() or not hsi_journal._emr_selected():
            return {"skipped": "hsi_sync disabled"}
        rekicked = await hsi_journal_queue.rekick(app)
        if rekicked:
            _log("hsi_sync watchdog: cadena muerta detectada, re-arrancada")
        return {"rekicked": rekicked}

    return _watchdog


def _register_data_backups_watchdog(app):
    """Repair a missing self-rescheduling backup chain within five minutes.

    A crash after a backup job succeeds but before its next durable defer
    lands has no `doing` row for ordinary stalled-job recovery to requeue.
    This watchdog is deliberately small: the queue module atomically checks
    for a todo/doing chain and computes the safe due time under its advisory
    lock, so a healthy chain is a no-op and a repaired one never runs earlier
    than the interval after its last success.
    """
    @app.periodic(cron="*/5 * * * *")
    @app.task(queue=_MAINT_QUEUE, name="worker.maintenance.data_backups_watchdog",
              retry=False)
    async def _watchdog(timestamp):  # noqa: ANN001
        from app.ops import data_backups, data_backups_queue
        if not _data_backups_enabled():
            return {"skipped": "data backups disabled or unconfigured"}
        rekicked = await data_backups_queue.rekick(
            app, interval_min=data_backups._interval_min(),
            startup_delay_s=data_backups_queue._STARTUP_GRACE_S)
        if rekicked:
            _log("data backups watchdog: missing chain re-started")
        return {"rekicked": rekicked}

    return _watchdog


def _register_launch_signals(app):
    """The worker's own heartbeat and the daily digest (TASK-4; core/launch_signals.py, ops/launch_digest.py).

    Every 5 minutes, in the maintenance queue: one Sentry Crons check-in (`turnos-worker`) around the digest's
    cheap due check. A worker that dies, or one that stops taking jobs from the queue, misses the check-in
    and the project raises the issue. Registered only when TURNOS_SENTRY_SIGNALS lists a feature and a DSN is
    set (`_register_jobs`), so a default worker runs exactly the jobs it ran before. The digest itself sends at
    most one event a day, claimed in the database."""
    @app.periodic(cron="*/5 * * * *")
    @app.task(queue=_MAINT_QUEUE, name=_SIGNALS_TASK, retry=False)
    async def _signals(timestamp):  # noqa: ANN001 — periodic tasks receive the unix ts
        import asyncio
        from app.core import launch_signals
        if not launch_signals.any_active():
            return {"skipped": "off"}
        from app.ops import launch_digest
        with launch_signals.monitored("turnos-worker", ("crontab", "*/5 * * * *"), 10, 4):
            return await asyncio.get_running_loop().run_in_executor(None, launch_digest.tick)

    return _signals


def main():
    # Plain, synchronous SIGTERM/SIGINT handler, installed before ANY of
    # the bootstrap WITHIN THIS FUNCTION — including the `from core
    # import ...` line further down (independent-review finding: an
    # earlier cut of this function ran that import FIRST, leaving a real,
    # if small, uncovered window during the import itself; deferred below
    # the handler install instead, since nothing above needs it) — before
    # _start_health_server() (what an external readiness prober waits on
    # before sending SIGTERM), and long before asyncio.run(_boot_and_run(...))
    # exists.
    #
    # HONEST BOUND (independent-review finding), not "zero uncovered
    # time": this is the earliest point THIS FUNCTION can install a
    # handler, not the earliest point in the PROCESS's life. Two things
    # unavoidably precede it and remain genuinely unprotected — the
    # window this fix does NOT claim to close: (1) CPython's own
    # interpreter startup (parsing/compiling `worker.py`, no signal
    # handler exists in a fresh Python process until code runs); (2)
    # this MODULE's own top-level imports — `import json/os/signal/...`
    # plus `from ai import chart_summary_queue`, `from modules.turnos
    # import referrals_queue, waitlist_queue, hsi_journal_queue`,
    # `from notifications import
    # reminders_queue`, `from ops import housekeeping_queue,
    # translate_warmup_queue` (lines ~171-175) — which MUST finish
    # before `main()` is even a callable name, let alone called; no
    # statement inside `main()` can ever run before them, by Python's
    # own execution model. Those five are deliberately kept lightweight
    # ("Light by design", their own docstring above) — just queue/
    # task-name constants, not the heavy business logic — so this window
    # is small in practice, but it is not zero, and closing it further
    # would mean installing a handler at MODULE level (before those
    # imports), a materially bigger, riskier restructure than this
    # bugfix's scope (it would also have to coexist with `main()` being
    # called repeatedly in-process by this suite's own tests, which the
    # current per-call install/restore design depends on). Not attempted
    # here — documented instead.
    #
    # NOT covered by `test_rapid_spawn_sigterm_never_orphans_worker_row`
    # below (independent-review correction to an earlier draft of this
    # comment, which wrongly cited it): that test signals only AFTER
    # `health_port()` confirms the "salud en http://…" line, by which
    # point THIS window — interpreter startup + these imports — is
    # already over; it structurally never exercises this window at all.
    # The genuinely relevant probe is
    # `test_signal_immediately_after_spawn_never_crashes_or_hangs`
    # (`tests/test_queue_worker.py`, `TestWorkerProcessLifecycle`), which
    # signals immediately after `Popen()`, before any wait — a raw `-15`
    # there is an EXPECTED, DOCUMENTED outcome of landing in this window
    # (Python's default SIGTERM disposition: silent termination), not a
    # bug; what it actually asserts is the invariant that must hold
    # regardless of which window the signal lands in — bounded exit, no
    # traceback, no orphaned state (registration happens far past this
    # point, deep inside `Worker.run()`). The ORIGINAL race THIS FILE's
    # early handler closes is the separate, much larger, measured
    # (~2-4% of cycles) gap AFTER these imports — between "salud en
    # http://…" and `asyncio.run(_boot_and_run(...))` actually starting
    # — proven by `test_rapid_spawn_sigterm_never_orphans_worker_row`'s
    # own 200-cycle history in
    # docs/solutions/dev/worker-signal-ownership-not-task-cancel.md.
    #
    # Closes a real, measured
    # race (~2-4% of rapid spawn+immediate-SIGTERM cycles — see "A
    # DIFFERENT, REAL, reproducible bug" in
    # docs/solutions/dev/worker-signal-ownership-not-task-cancel.md): the
    # ONLY thing that used to install a handler in this process was
    # _boot_and_run's own loop.add_signal_handler(), which only runs once
    # asyncio.run() has actually started executing that coroutine — a
    # signal landing in the synchronous gap before that hit Python's
    # default disposition and killed the process outright, bypassing the
    # documented graceful/EXIT_TRANSIENT contract entirely.
    #
    # signal.signal() needs no event loop, so this handler covers every
    # synchronous boundary. It only records into the per-main
    # `_SignalState`; nothing is cancelled. `_boot_and_run` shares that
    # state with its temporary asyncio callback and, before it returns,
    # restores this handler under a signal mask. That means neither the
    # callback handoff nor event-loop teardown can create a default-
    # disposition window while main() remains live.
    #
    # The state is deliberately per main() invocation: tests call main()
    # repeatedly in one interpreter, but a signal before the loop and one
    # after it starts must still use the same counter.  prev_sigterm/
    # prev_sigint are captured so
    # every exit path below — including the several EXIT_CONFIG early
    # returns that happen before _start_health_server() is even called —
    # restores the EXACT disposition that existed before main() started,
    # never leaking a handler into a long-lived process (the in-process
    # tests that call worker.main() directly are the real case this
    # guards: leaving a custom SIGTERM/SIGINT handler installed on the
    # test-runner process itself would be a bug, not "harmless", even
    # though a real `python3 worker.py` subprocess exits shortly after
    # any of these paths anyway).
    signal_state = _SignalState()
    prev_sigterm = signal.getsignal(signal.SIGTERM)
    prev_sigint = signal.getsignal(signal.SIGINT)

    def _handle_early_signal(signum, frame):  # noqa: ANN001 — signal.signal callback signature
        signal_state.note_plain_signal(signum)
        signal_state.request_stop(signum, "main() before or after the event loop")

    # POSIX signal handling only works from the main thread — same
    # precondition _boot_and_run's own install below checks (tests call
    # main() from the main thread of the test process; this guard is
    # defense, matching that existing pattern exactly).
    early_installed = threading.current_thread() is threading.main_thread()
    if early_installed:
        signal.signal(signal.SIGTERM, _handle_early_signal)
        signal.signal(signal.SIGINT, _handle_early_signal)

    try:
        # Deferred to HERE (independent-review finding): these were
        # previously imported before the handler install above, which left
        # a real — if small — uncovered window during the imports
        # themselves. Keep the import inside this try/finally too: a
        # failure must restore the caller's original dispositions.
        from app.core import db, outbound_guard, queue_infra
        return _main_body(db, outbound_guard, queue_infra, signal_state,
                          _handle_early_signal)
    except KeyboardInterrupt:
        # Defense-in-depth, not the primary mechanism: with the counter +
        # _force_exit() fix above, no signal handler installed by THIS
        # file ever lets a KeyboardInterrupt reach here — a second signal
        # exits via os._exit() before Python's exception machinery gets
        # involved at all. This catches anything not enumerated today
        # (same reasoning as `_boot_and_run`'s own defensive `except
        # asyncio.CancelledError`, added for the same "the contract said
        # EVERY failure" reason), so a genuinely unanticipated
        # KeyboardInterrupt still maps to a documented code and a clear
        # log line instead of an uncaught traceback. Named catch, not
        # `except BaseException` — this file's own static gate bans that.
        _STATE["fase"] = "error"
        _log("KeyboardInterrupt llegó hasta main() sin haber sido "
             "manejado por el mecanismo de segunda señal — no debería "
             "pasar con _force_exit() activo, pero se cubre igual "
             "(exit EXIT_TRANSIENT, el supervisor reintenta)")
        return EXIT_TRANSIENT
    finally:
        # The only point that restores the caller's original dispositions.
        # `_boot_and_run` first restores this per-main handler under a
        # blocked SIGTERM/SIGINT mask, so there is no default-disposition
        # interval before this final restoration.
        if early_installed:
            signal.signal(signal.SIGTERM, prev_sigterm)
            signal.signal(signal.SIGINT, prev_sigint)


def _stop_before(boundary):
    """The answer of every "a signal ended the previous bounded step" exit:
    log it, mark the phase, and return the clean exit code 0."""
    _STATE["fase"] = "apagando"
    _log(f"apagando con gracia antes de {boundary}: una señal terminó "
         "el paso acotado anterior y no se inicia otro efecto lateral")
    return 0


def _step_outbound_guard(outbound_guard, signal_state):
    """Ladder step 1: arm the outbound kill switch when it is requested.
    Returns an exit code to stop with, or None to go on."""
    # The kill switch arms FIRST — before the schema touch, before any job
    # registration — and failing to arm it is a refusal to run, not a
    # warning: a requested-but-absent guard is exactly the fail-open hole
    # it exists to close (safe business-queue activation proof card,
    # 2026-08-08). Exit 2 (permanent config): a restart can't arm what
    # this process couldn't.
    if signal_state.stop_requested:
        return _stop_before("reading outbound-guard configuration")
    guard_requested = outbound_guard.requested()
    if signal_state.stop_requested:
        return _stop_before("arming the outbound guard")
    if guard_requested:
        try:
            outbound_guard.arm()
        except Exception as e:  # noqa: BLE001 — cualquier fallo del candado = no corro
            _log(f"TURNOS_OUTBOUND_KILL=1 pedido pero el candado no se pudo "
                 f"armar: {log_pii.exc(e)} — sin candado no corro (fail closed, exit 2)")
            return EXIT_CONFIG
        _log("candado de salida armado (TURNOS_OUTBOUND_KILL=1): ningún "
             "connect sale salvo loopback y Postgres")
    return None


def _step_validate_config(queue_infra, signal_state):
    """Ladder step 2: the topology gate and the stalled-worker threshold.
    Returns an exit code to stop with, or None to go on."""
    if signal_state.stop_requested:
        return _stop_before("topology and configuration validation")

    # Fail-closed topology gate (Codex gate, round 6): this entrypoint only
    # runs as the supervised child, TURNOS_QUEUE_WORKER=1. Unset or invalid
    # (a typo, a missing env line) exits 2: `python3 worker.py` being
    # directly runnable is not stronger than fail-closed data safety — a
    # manual one-shot run must state its mode explicitly.
    if queue_infra.worker_mode() != "1":
        _log(f"TURNOS_QUEUE_WORKER={settings.ProcessSettings.current().queue_worker!r} "
             "no es un modo válido — el worker sólo corre con '1' "
             "(supervisado por asgi_main); configuración inválida (exit 2)")
        return EXIT_CONFIG

    # Error tracking inside the worker (TURNOS_SENTRY_SIGNALS + TURNOS_SENTRY_DSN, both needed): the failure
    # codes, the daily digest and the Sentry Crons check-ins are sent from THIS process. Nothing starts, and
    # nothing is imported, on a default install.
    from app.core import launch_signals
    if launch_signals.init_sentry():
        _log("rastreo de errores (Sentry) iniciado en el worker (TURNOS_SENTRY_SIGNALS)")

    if signal_state.stop_requested:
        return _stop_before("the stalled-worker safety check")

    # Safety floor for the recovery threshold (Codex gate, 2026-08-08):
    # the worker heartbeat beats every ~10 s; a threshold BELOW ~3 beats
    # would requeue jobs of a LIVE worker that is merely late (GC,
    # saturated CPU) and run them twice. Permanent invalid config
    # (exit 2). NaN/inf are rejected outright — NaN compares False
    # against the floor and would silently pass it, and an infinite
    # threshold means recovery never fires. TURNOS_TEST_UNSAFE_STALLED=1
    # exists ONLY for the crash-recovery tests, which need second-scale
    # thresholds to avoid waiting half a minute per case; it lifts the
    # floor, never the finiteness requirement.
    import math
    try:
        umbral = _stalled_seconds()
    except ValueError:
        _log(f"TURNOS_WORKER_STALLED_S={os.environ.get('TURNOS_WORKER_STALLED_S')!r} "
             "no es un número — configuración inválida (exit 2)")
        return EXIT_CONFIG
    if not math.isfinite(umbral):
        _log(f"TURNOS_WORKER_STALLED_S={umbral!r} no es un número finito — "
             "configuración inválida (exit 2)")
        return EXIT_CONFIG
    lifted = (os.environ.get("TURNOS_TEST_UNSAFE_STALLED") == "1"
              and _test_seams_enabled())
    if umbral < _STALLED_FLOOR_S and not lifted:
        _log(f"TURNOS_WORKER_STALLED_S={umbral:g} por debajo del piso seguro "
             f"({_STALLED_FLOOR_S:g} s ≈ 3 heartbeats): re-encolaría jobs de un "
             "worker vivo — configuración inválida (exit 2)")
        return EXIT_CONFIG
    return None


_SETTINGS_WAIT_S = 120
_SETTINGS_POLL_S = 3


def _await_settings_db(signal_state, *, wait_s=None, poll_s=None):
    """Wait until the panel-settings row of the catalog is readable, then drop the settings cache.

    `config.get` swallows a failed catalog read as "no overrides", so a worker that boots while the web
    process is still applying migrations reads every panel setting (TURNOS_BACKUP_ENABLED included) as its
    default and registers no `_JOBS` entry for that setting until the next deploy; encrypted backups sat
    off from 2026-11-13 08:00 on exactly that way. Bounded; on timeout it logs and goes on."""
    from app.core import config, db_catalog
    wait_s = _SETTINGS_WAIT_S if wait_s is None else wait_s
    poll_s = _SETTINGS_POLL_S if poll_s is None else poll_s
    deadline = time.monotonic() + wait_s
    while True:
        try:
            db_catalog.catalog_get("config", "settings")
            break
        except Exception as e:  # noqa: BLE001 - the catalog is not there yet
            if signal_state.stop_requested or time.monotonic() >= deadline:
                _log(f"panel settings unreadable at boot: {log_pii.exc(e)}; jobs register with the defaults")
                break
            time.sleep(poll_s)
    config._invalidate_db_cache()


def _step_prepare_queue(db, queue_infra, signal_state):
    """Ladder step 3: DATABASE_URL, the schema, the queue app and its jobs.
    Returns (exit_code_or_None, app, queues)."""
    if signal_state.stop_requested:
        return _stop_before("DATABASE_URL validation"), None, None
    if not db._db_url():
        _log("DATABASE_URL no configurada — configuración permanente inválida (exit 2)")
        return EXIT_CONFIG, None, None

    if signal_state.stop_requested:
        return _stop_before("schema initialization"), None, None
    try:
        queue_infra.ensure_schema()
    except Exception as e:  # noqa: BLE001 — transitorio (Postgres caído): el supervisor reintenta
        _log(f"no se pudo asegurar el esquema: {log_pii.exc(e)}")
        return EXIT_TRANSIENT, None, None

    if signal_state.stop_requested:
        return _stop_before("building the queue app"), None, None
    app = queue_infra.build_app()
    _await_settings_db(signal_state)
    if signal_state.stop_requested:
        return _stop_before("registering queue jobs"), None, None
    try:
        colas = _register_jobs(app)
    except ValueError as e:
        # Un intervalo no expresable en cron: reintentar no lo arregla.
        _log(f"configuración inválida: {log_pii.exc(e)} — salida permanente (exit 2)")
        return EXIT_CONFIG, None, None
    if colas == [_MAINT_QUEUE]:
        _log("ningún job de negocio habilitado — sólo mantenimiento")
    return None, app, colas


def _step_run_worker(app, colas, signal_state, early_signal_handler):
    """Ladder step 4: the health server, the graceful budget and the async
    worker loop. Returns the process exit code."""
    if signal_state.stop_requested:
        return _stop_before("starting the health server")
    httpd = _start_health_server()
    if signal_state.stop_requested:
        if httpd is not None:
            httpd.shutdown()
        return _stop_before("starting the async worker")
    graceful = settings.ProcessSettings.current().worker_graceful_timeout
    if signal_state.stop_requested:
        if httpd is not None:
            httpd.shutdown()
        return _stop_before("entering the async worker")

    nombre = f"turnos-{socket.gethostname()}-{os.getpid()}"
    _STATE["queues"] = list(colas)
    _log(f"worker '{nombre}' arrancando (colas: {', '.join(colas) or '—'})")
    if signal_state.stop_requested:
        if httpd is not None:
            httpd.shutdown()
        return _stop_before("entering the async worker")
    try:
        import asyncio

        # `_STATE["fase"]` flips to "corriendo" (what `/ready` reports 200
        # on) INSIDE `_boot_and_run`, only once it has installed its own
        # early SIGTERM/SIGINT handler — see that function's docstring for
        # the readiness-vs-signal-handler race this closes.
        asyncio.run(_boot_and_run(
            app, colas, nombre, graceful, signal_state,
            ({signal.SIGTERM: early_signal_handler,
              signal.SIGINT: early_signal_handler}
             if early_signal_handler is not None else None)))
    except asyncio.CancelledError as e:
        # Round-9 review finding: `_boot_and_run` deliberately re-raises
        # CancelledError after cleanup (never swallows it — required for
        # correct cooperative-cancellation semantics, and this file's own
        # static gate bans `except BaseException`/bare `except:` outright).
        # But CancelledError is NOT an Exception subclass, so the plain
        # `except Exception` below never caught it — it propagated straight
        # out of main() uncaught, bypassing _STATE["fase"]="error", this
        # file's own _log(...) convention (an unstructured traceback
        # instead), AND the documented "failures exit loudly... with TWO
        # distinct codes the supervisor honors" contract (an uncaught
        # exception's exit code is a Python default, not a deliberate
        # decision this file made). Nothing in the current design calls
        # task.cancel() on this top-level coroutine, so this is defense at
        # the true top boundary against a path only reachable defensively
        # today — not a reintroduction of cancellation anywhere upstream.
        _STATE["fase"] = "error"
        _log(f"worker cancelado inesperadamente: {log_pii.exc(e)}")
        return EXIT_TRANSIENT
    except Exception as e:  # noqa: BLE001 — transitorio: el supervisor reintenta
        _STATE["fase"] = "error"
        _log(f"worker terminó con error: {log_pii.exc(e)}")
        return EXIT_TRANSIENT
    finally:
        if httpd is not None:
            httpd.shutdown()
    _STATE["fase"] = "apagando"
    _log("apagado con gracia")
    return 0


def _main_body(db, outbound_guard, queue_infra, signal_state=None,
               early_signal_handler=None):
    """The original body of main() — split out so the early-signal-handler
    install/restore above can wrap it in a single try/finally without
    re-indenting the whole function. Not called directly by anything else;
    kept as a plain function (not nested) so its own local names don't
    shadow main()'s closure variables (prev_sigterm/prev_sigint/
    early_installed) by accident.

    A short ladder of steps, each returning an exit code to stop with or
    None to go on: kill switch, configuration gates, DATABASE_URL + schema
    + queue app + jobs, then the health server and the worker loop. Every
    step checks for a pending signal at its own bounded boundaries."""
    signal_state = signal_state or _SignalState()
    code = _step_outbound_guard(outbound_guard, signal_state)
    if code is not None:
        return code
    code = _step_validate_config(queue_infra, signal_state)
    if code is not None:
        return code
    code, app, colas = _step_prepare_queue(db, queue_infra, signal_state)
    if code is not None:
        return code
    return _step_run_worker(app, colas, signal_state, early_signal_handler)


if __name__ == "__main__":
    if "--jobs" in sys.argv[1:]:
        # Registry audit: what this build would register, with each job's
        # declared capabilities and re-run contract — read-only, no mode
        # required (auditing must not need a topology decision). Flag
        # resolution may consult DB overrides exactly like the running
        # worker does; the pool is closed EXPLICITLY so the audit leaves
        # nothing behind (a GC'd psycopg pool raises
        # PythonFinalizationError at interpreter shutdown — reproduced
        # under -W error::ResourceWarning, Codex review).
        salida = json.dumps(_audit_jobs(), ensure_ascii=False, indent=2)
        from app.core import db
        db.close_pools()
        print(salida)
        sys.exit(0)
    # Every log record and uncaught exception of this process loses its
    # message: a failed job's error echoes what it was given (core/log_pii).
    log_pii.install()
    sys.exit(main())
