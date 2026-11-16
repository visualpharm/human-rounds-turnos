"""demo_refresh_store.py — the demo refresher's persistence boundary.

`demo_refresh.py` owns the policy: WHEN a refresh runs, which phases are
required, and what counts as success, partial, or failure.  This module
owns every byte that touches Postgres on its behalf — the pooled
connection, the marker query that reads a seeded day back, the one-row
completion ledger, and the session-scoped advisory lock that serializes a
refresh across a rolling deploy's two web processes.

The lock frame is ONE pooled session held for the whole attempt: the
try-lock, the phase runs, the read-back verification, and the completion
write all happen while THAT session owns the lock, and the unlock always
follows the write — behind a rollback first when the write itself aborted
the transaction, because a session in that state rejects the unlock too,
and a rejected unlock strands the session-scoped lock on a healthy pooled
connection (independent review, 2026-08-16).  `exclusive_refresh()` below
is the only shape that guarantee survives in; yielding the connection
(and ``None`` when another process owns the refresh — that process writes
the shared completion record itself) keeps the phases callable as plain
orchestrator functions on their own connections, exactly as before.

No phase ordering, outcome computation, or daemon lives here.  Beyond the
stdlib and SQLAlchemy Core this module imports `core.db` (the pool), `core.sqlcore`
and `core.schema_model`; the seed's marker constant is still resolved lazily at
query time.

Query style (2026-10-25, SQLAlchemy Core stage 2): the marker read and the lock
statements are Core constructs run on the pooled cursor; behavior is pinned in
`tests/test_demo_refresh_store_db.py` and `tests/test_demo_refresh_completion.py`.
"""
from contextlib import contextmanager

from sqlalchemy import BigInteger, cast, func, select

from app.core import db, sqlcore
from app.core import db_backup
from app.core.schema_model import booking_requests

# One advisory-lock name shared by every demo-refresh process; `hashtext`
# maps it onto the bigint key the lock functions take.  SESSION-scoped
# (`pg_try_advisory_lock`, not its `_xact_` twin) on purpose: the phases
# between acquire and unlock run on their own connections, so the lock
# must outlive any single transaction on the holding session.
LOCK_KEY = "turnos:demo_refresh"


def day_already_seeded(dia):
    """Does the demo's own data already cover that day?

    Lets a container restart at 3 pm leave the day alone instead of wiping
    the attendance a visitor just ticked, while a restart after midnight
    (or a deploy) still refreshes it.  Reads on its own short-lived pooled
    connection — it is also the read-back proof after a reseed, which must
    not depend on anything still held open by the seeding itself.

    Only the day's QUEUE counts (`seed_demo_all.cola_del_dia`): the featured
    patient's next visit may sit on a day the seed has not built yet (09.11).
    """
    return day_queue_size(dia) > 0


def day_queue_size(dia):
    """How many of the demo's queue appointments fall on that day."""
    import scripts.seed_demo_all as seed_demo_all
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001 — durable operational ledger
        return sqlcore.run(cur, select(func.count().label("n")).select_from(booking_requests).where(
            *seed_demo_all.cola_del_dia(dia),
        )).fetchone()["n"]


@contextmanager
def exclusive_refresh():
    """Own the demo refresh's advisory lock on one pooled session.

    Yields the lock-holding connection for the body to run its phases and
    record the completion on, or ``None`` when another process already
    holds the lock (a busy caller must write nothing — not the ledger, not
    the seed).  The unlock runs in a ``finally`` on the same session, so
    it follows the completion write on both the success and the failure
    path.  On the failure path the body's aborted transaction is rolled
    back FIRST — a session that just rejected a ledger write also rejects
    the unlock, and a rejected unlock strands the session-scoped lock on a
    healthy pooled connection.  When the body AND the unlock both fail,
    the BODY's exception is the one that propagates (the unlock failure
    rides along as its implicit context, never masking the write that
    died); an unlock failure on a body that succeeded stays the frame's
    own error, because a frame that did not unlock reports no success.
    The pooled connection's own ``__exit__`` still commits or rolls back
    afterwards, unchanged from the pre-extraction shape.
    """
    with db._connect() as conn:  # noqa: SLF001 — durable operational ledger
        with conn.cursor() as cur:
            acquired = bool(sqlcore.run(cur, select(func.pg_try_advisory_lock(
                cast(func.hashtext(LOCK_KEY), BigInteger)).label("acquired"))).fetchone()["acquired"])
        if not acquired:
            yield None
            return
        body_error = None
        try:
            yield conn
        except BaseException as exc:
            body_error = exc
            # The completion write may have died mid-transaction; until
            # rollback the server rejects EVERY statement on this session,
            # the unlock included.  Rolling back here is what the pooled
            # connection's own ``__exit__`` would do anyway once the
            # exception propagates — only the order changes, so a clean
            # success path still commits untouched.
            conn.rollback()
            raise
        finally:
            try:
                with conn.cursor() as cur:
                    sqlcore.run(cur, select(func.pg_advisory_unlock(
                        cast(func.hashtext(LOCK_KEY), BigInteger))))
            except Exception:
                # An unlock that STILL fails must not hand a lock-holding
                # session back to the pool: close the raw connection so
                # ``putconn`` discards it (the same hardening rekick()
                # already carries in connectors/hsi/hsi_sync_queue.py; a
                # dead socket has released the lock server-side on its
                # own).
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 — already failing
                    pass
                if body_error is None:
                    # The body completed; the unlock failure IS the
                    # frame's error (a frame that did not unlock reports
                    # no success).
                    raise
                # The body failed too: re-raise ITS exception, so the
                # orchestrator sees the write that died, not the cleanup
                # that failed after it — the unlock failure stays chained
                # onto it as implicit context.
                raise body_error


def record_completion(conn, outcome, day):
    """Persist the attempt's outcome on the lock-holding session.

    The one-row ledger (`demo_refresh_completion`) is only ever green when
    the orchestrator says ``success`` after its full asset/referral/
    current-day contract; a partial or failed attempt still overwrites
    the previous outcome so an old success cannot stay visually green.
    Writing on ``conn`` — the session `exclusive_refresh()` yielded — is
    what makes the record part of the same serialized attempt.
    """
    db_backup.demo_refresh_completion(outcome, day, conn=conn)
