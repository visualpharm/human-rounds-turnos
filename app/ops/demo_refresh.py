"""demo_refresh.py — the public demo's day rolls forward on its own.

Everything `scripts/seed_demo_all.py` writes is relative to "now": today's
queue comes out of the doctors' agendas for TODAY, the 90-day history is
counted back from today. That makes the seed a snapshot, and a snapshot goes
stale — the demo was showing whatever day it was last deployed on, so
«Pacientes de hoy» was empty and the reports ended weeks ago (the product owner,
2026-08-01: "daily, make the appointments etc move to the next day, so every
day the current day is nearly all booked").

So the demo re-seeds itself once a day, just after local midnight. The seed
is idempotent and marker-scoped (`@demo.humanrounds.org`), so this is a
delete-then-recreate of the demo's own fictional rows and nothing else.

Only ever runs on a demo installation: `demo_installation.enabled()` gates
it, and `seed()` itself refuses to run without that flag too — a real
installation cannot get here even by mistake. `TURNOS_DEMO_REFRESH_OFF=1`
turns it off.

The images are a separate half: they are files, not rows, so they are copied
back on EVERY boot (`reseed_assets`) instead of waiting for the day to turn.
Otherwise a deploy that ships new pack portraits on an already-seeded day
leaves them invisible until midnight.

Nothing breaks if it fails: a refresh that raises is retried on the next tick,
the same degrade-don't-die contract as reminders.py and waitlist.py.  Unlike
those older threads, this daemon also persists one non-sensitive completion
record.  It is green only after assets, referral repair, and a current-day
generation all completed; a boot line or a surviving thread is never evidence
that the demo is current.

The persistence boundary lives in `demo_refresh_store.py`: connection, SQL,
the session advisory lock, and the completion ledger.  This module owns only
the flow — which phases run, in what order, and what their combined outcome
means.
"""
import os
import threading
import time
from datetime import datetime

from app.core import log_pii
from app.core import tz
from app.ops import demo_refresh_store

_ART = tz.tzinfo()

# The loop checks a date comparison plus one small marker query.  The full
# generation only runs on a new day, failed boot/retry, or same-day marker
# loss; it never claims success from the check alone.
INTERVALO_S = 300

# One web process must not create a second daemon just because a lifecycle
# hook is called twice.  A PostgreSQL session lock below additionally
# serializes the work across a rolling deploy's two processes.
_START_LOCK = threading.Lock()
_started = False
# The store owns the key (and every other byte of the lock/ledger SQL);
# this alias keeps the name the daemon and its tests were written against.
_LOCK_KEY = demo_refresh_store.LOCK_KEY


def _log(msg):
    print(f"[demo_refresh] {msg}", flush=True)


def _hoy():
    return datetime.now(_ART).date()


def _dia_ya_sembrado(dia):
    """Does the demo's own data already cover that day?

    Lets a container restart at 3 pm leave the day alone instead of wiping
    the attendance a visitor just ticked, while a restart after midnight
    (or a deploy) still refreshes it.  The marker query itself lives in
    the store; this seam stays so a flow step (and the tests) can stand in
    for the read.
    """
    return demo_refresh_store.day_already_seeded(dia)


def reseed():
    """Re-create the demo's fictional data around today.

    `force=True`: `refresh_once()` already made and will read back the
    day-coverage decision, so the check `seed()` does for its OTHER caller
    (Coolify's post_deployment_command, see seed_demo_all.dia_cubierto)
    would be redundant here — skip straight to reseeding."""
    import scripts.seed_demo_all as seed_demo_all
    seed_demo_all.seed(force=True)
    return True


def reseed_assets():
    """Restore the demo's pack images on the runtime volume (every boot)."""
    import scripts.seed_demo_all as seed_demo_all
    seed_demo_all.seed_assets()
    return True


def _reparar_referrals():
    """Re-seed when the demo's referrals point at files that aren't there.

    `data/` is not a persistent volume, so rows seeded by the old
    copy-into-`data/ordenes` code lost their image on the next redeploy and
    the panel showed 152 requests with no order attached — and the daily
    refresh never noticed, because the day was already seeded. Referrals now
    point straight into the pack (`seed_demo_all._adjuntar_orden`), so this
    check is one-shot in practice: it repairs the rows written before that
    change and then stops finding anything.
    """
    import scripts.seed_demo_all as seed_demo_all
    if not seed_demo_all.referrals_rotas():
        return False
    _log("las órdenes de la demo no están en disco; se regeneran los datos")
    reseed()
    return True


def _demo_enabled():
    """Read the safety boundary before any database or filesystem work."""
    if os.getenv("TURNOS_DEMO_REFRESH_OFF") == "1":
        return False
    try:
        from app.ops import demo_installation
        return demo_installation.enabled()
    except Exception:  # noqa: BLE001 — a failed guard never permits work
        return False


def _outcome(completed_steps, required_steps):
    if completed_steps == required_steps:
        return "success"
    return "partial" if completed_steps else "failed"


def refresh_once(day=None):
    """Attempt one complete, demo-only refresh and persist its safe outcome.

    ``success`` means every required phase completed and the seeded data was
    read back for the requested installation day.  ``partial`` and ``failed``
    are persisted too, so an earlier success cannot remain visually green
    after a caught failure.  ``inactive`` returns before any database work;
    ``busy`` means another process owns the same session advisory lock and
    will write the shared completion record itself.
    """
    if not _demo_enabled():
        return "inactive"
    if day is None:
        day = _hoy()
    try:
        with demo_refresh_store.exclusive_refresh() as conn:
            if conn is None:
                return "busy"
            completed = 0
            required = 3

            # A success must be a complete generation, not merely a
            # same-day row check.  Copying is idempotent and bounded by
            # the small fictional pack, and it ensures a daily/recovery
            # success verifies the volume-backed assets too.
            try:
                reseed_assets()
                completed += 1
            except Exception:  # noqa: BLE001 — record a non-green result below
                _log("demo assets were not restored; refresh will retry")

            try:
                _reparar_referrals()
                completed += 1
            except Exception:  # noqa: BLE001 — record a non-green result below
                _log("demo referral repair did not complete; refresh will retry")

            try:
                if not _dia_ya_sembrado(day):
                    _log(f"regenerating demo data for {day}")
                    reseed()
                # The write is not proof.  Read the day's queue back before
                # reporting a complete current generation: «success» over an
                # empty «Pacientes de hoy» is the lie this guards (09.11).
                if _dia_ya_sembrado(day):
                    completed += 1
                else:
                    _log(f"the demo queue for {day} is empty after the reseed "
                         "(no approved demo appointment that day); refresh will retry")
            except Exception as exc:  # noqa: BLE001 — record a non-green result below
                # The type only (test_logs_pii's static gate): an exception's text can echo a row.
                _log(f"demo day data did not complete: {log_pii.exc(exc)}; refresh will retry")

            outcome = _outcome(completed, required)
            # Same session that holds the lock — the record is part of the
            # serialized attempt, and the store's frame unlocks after it.
            demo_refresh_store.record_completion(conn, outcome, day)
            return outcome
    except Exception:  # noqa: BLE001 — DB failure has no durable lie to write
        _log("demo refresh status is unavailable; refresh will retry")
        return "failed"


def _needs_refresh(current_day, last_completed_day, assets_ready):
    """Whether the next bounded loop pass must attempt a full generation.

    A same-day marker loss is not deferred until midnight.  A failed read is
    also treated as needing a pass, so the durable outcome becomes non-green
    instead of a process retaining stale optimistic state.
    """
    if current_day != last_completed_day or not assets_ready:
        return True
    try:
        return not _dia_ya_sembrado(current_day)
    except Exception:  # noqa: BLE001 — refresh_once records the visible failure
        return True


def _mantener_escenarios():
    """Keep the demo's showcase state alive BETWEEN reseeds (every tick, not
    just at the day rollover): the catalog overlay that keeps the demo service
    open every day 8-20 with symptom capture on for the demo doctor, and the
    featured patient's appointment approved and in the future — a visitor of
    the patient demo can cancel it, and the clock passes its slot as the day
    goes on. Degrade-don't-die like everything here: a failed step logs and
    retries on the next tick. Inside `seed_demo_all` everything stays
    marker/demo-mode scoped, so a real installation never gets here."""
    if not _demo_enabled():
        return False
    try:
        import scripts.seed_demo_all as seed_demo_all
    except Exception:  # noqa: BLE001 — the daemon (and its daily reseed) must outlive it
        _log("showcase keep-alive could not load the seed; refresh will retry")
        return False
    try:
        seed_demo_all._asegurar_catalogo_demo()
    except Exception:  # noqa: BLE001 — retry on the next tick
        _log("demo catalog overlay did not complete; refresh will retry")
    try:
        seed_demo_all._asegurar_turno_destacado()
    except Exception:  # noqa: BLE001 — retry on the next tick
        _log("featured appointment keep-alive did not complete; refresh will retry")
    return True


def _loop():
    global _started
    day = None
    assets_ready = False
    try:
        while True:
            current_day = _hoy()
            # Preserve the old same-day recovery property: an accidental
            # removal of today's marker-scoped rows gets repaired within one
            # interval, not only at the next midnight or deploy.
            if _needs_refresh(current_day, day, assets_ready):
                result = refresh_once(current_day)
                if result == "success":
                    assets_ready = True
                    day = current_day
            # The showcase keep-alive is not part of a refresh and its
            # success is not part of the ledger: it runs on EVERY tick so a
            # cancelled or lapsed walkthrough is repaired within one
            # interval, at any hour of the day.
            _mantener_escenarios()
            time.sleep(INTERVALO_S)
    finally:
        with _START_LOCK:
            _started = False


def start():
    """Launch the singleton daemon. Returns False when it does not apply."""
    global _started
    if not _demo_enabled():
        return False
    with _START_LOCK:
        if _started:
            return False
        _started = True
        try:
            threading.Thread(target=_loop, name="demo-refresh", daemon=True).start()
        except Exception:  # noqa: BLE001 — leave a future lifecycle call able to retry
            _started = False
            raise
    return True


if __name__ == "__main__":  # manual check: only operates on an opted-in demo
    refresh_once()
