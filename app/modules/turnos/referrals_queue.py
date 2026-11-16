"""referrals_queue.py — the referral-file retention sweep as a
procrastinate periodic task (Tier 4 periodic-daemon batch 2,
docs/roadmap/tier4-replatform-design.md, "Worker topology"): the SAME
`booking_referrals.sweep_referrals()` the daemon thread runs every
TURNOS_REFERRAL_SWEEP_INTERVAL_H hours, registered on a queue app
instead. Fourth job migrated.

Deliberately a SEPARATE sibling module from `booking_requests.py`,
never imported at that module's top level — same reasoning as
`waitlist_queue.py` vs `waitlist.py`: only `worker.py` imports this,
and only when the worker process actually runs.

The sweep deletes files under data/ordenes/ driven by DB state
(`db_booking.referrals_to_purge` decides candidacy; the row is never touched,
only the file and its `referral_path` pointer). That makes the job
DB-idempotent (a re-run finds no candidates left) and safe under
worker races (two workers deleting the same path: `os.remove` on the
loser hits FileNotFoundError, caught per-file, and `clear_referral_path`
is a plain idempotent UPDATE). One topology caveat, deliberate: "a
missing file on disk still clears the DB pointer" (sweep_referrals'
own contract) assumes this process SEES the data volume — true by
construction: the worker is the supervised child of the web process
(`TURNOS_QUEUE_WORKER=1`, same container).
"""
import sys

_QUEUE = "referrals"
_TASK_NAME = "modules.turnos.referrals.retention_sweep"


def _log(msg):
    print(f"[referrals_queue] {msg}", file=sys.stderr, flush=True)


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
        from app.modules.turnos import booking_referrals
        # sweep_referrals() never raises (its own contract) and re-reads
        # TURNOS_REFERRAL_RETENTION_DAYS on every pass, exactly like the
        # thread loop's body.
        borrados = booking_referrals.sweep_referrals()
        if borrados:
            _log(f"barrido: {borrados} archivo(s) purgado(s)")
        return borrados

    return queue_infra.register_periodic_sweep(
        app, queue=_QUEUE, task_name=_TASK_NAME,
        interval_min=interval_min, cron=cron, body=_cuerpo)
