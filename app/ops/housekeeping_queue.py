"""housekeeping_queue.py — the table-hygiene sweep as a procrastinate
periodic task (Tier 4 periodic-daemon batch 2,
docs/roadmap/tier4-replatform-design.md, "Worker topology"): the SAME
`housekeeping.tick_purges()` the daemon thread's full tick runs every
TURNOS_HOUSEKEEPING_INTERVAL_H hours, registered on a queue app instead
(purges only — see below for why the disk check is NOT part of this).
Third job migrated, first of the batch that proves the mechanism holds
for the DAILY-cadence sweeps (waitlist/reminders are minute-scale).

Deliberately a SEPARATE sibling module from `housekeeping.py`, never
imported at that module's top level — same reasoning as
`waitlist_queue.py` vs `waitlist.py` (see that module's docstring):
`housekeeping.py` is imported unconditionally by `server.py:main()`, so
anything at ITS top level becomes an unconditional cost of the default
(no queue) deployment shape. Only `worker.py` imports this module, and
only when the worker process actually runs.

The task body is `tick_purges()` — the bounded table purges ONLY, a
genuinely DB-only job with no filesystem access and no outbound of any
kind. The disk check (measurement + at-most-once advisory email) is
deliberately NOT here (Codex gate, round 8): its claim-before-send
contract can deliver zero after a crash, which fails the queue batch's
literal no-skipped-work bar, so it stays on the WEB process's legacy
schedule in every topology (housekeeping.start() keeps a reduced
disk-only thread when the purges are delegated). Consequence: this job
touches no filesystem. Proven by tests/test_queue_worker.py (the queue job performs zero
outbound and touches no alert claims even under alert-triggering
conditions).
"""
import sys

_QUEUE = "housekeeping"
_TASK_NAME = "ops.housekeeping.sweep"


def _log(msg):
    print(f"[housekeeping_queue] {msg}", file=sys.stderr, flush=True)


def register(app, interval_min, cron=None):
    """Registers the periodic task on a procrastinate App built by the
    CALLER (worker.py; tests build their own). Registration mechanics
    (cron derivation, the `cron` test seam, the double-gated mid-task
    hold TestBatch2CrashRecovery kills through —
    `queue_infra.test_hold`, formerly a private copy here) live ONCE in
    `queue_infra.register_periodic_sweep` — this module only owns the
    sweep body."""
    from app.core import queue_infra

    def _cuerpo(timestamp):  # noqa: ANN001 — periodic tasks receive the unix ts
        from app.ops import housekeeping
        # tick_purges() never raises (its own contract): each purge
        # guards itself exactly like the thread loop relies on. Never
        # tick() here — the disk check is web-side only (round-8 gate).
        out = housekeeping.tick_purges()
        total = sum(out.values())
        if total:
            _log(f"barrido: {total} fila(s) purgada(s) {out}")
        return out

    return queue_infra.register_periodic_sweep(
        app, queue=_QUEUE, task_name=_TASK_NAME,
        interval_min=interval_min, cron=cron, body=_cuerpo)
