"""waitlist.py — re-offering released appointment slots by email (C1).

Idea ported from webturno/Sana ("mejorar turno"): a patient who already has
an appointment scheduled can ask "let me know if something opens up
sooner". When a slot opens up (a cancellation, or the operator releases a
spot), it's offered to the queue in FIFO order, ONE at a time, by email:

  - each patient has the slot GUARANTEED for WINDOW_MIN minutes ("for 5
    minutes this slot is yours"); if they don't take it, it moves to the
    next one (same STAGGER_MIN interval between offers);
  - it's offered up until the slot's own time (the product owner, 2026-07-18: the
    patient could ALREADY be at the hospital — there's no "less than X min
    left" cutoff; this revokes the original CORTE_MIN=30); a slot whose time
    has already passed becomes VACANT (seen on a walk-in basis).

The decision engine (`next_action`) is PURE and testable without a DB: given
the time, the current offer and the remaining queue, it says what to do.
`tick()` applies it against the state in Postgres and sends the emails;
`claim()` validates the link's token (the token IS the authentication, same
as the ICS feed); `reject_offer()` is the patient's explicit "I don't want
it" — it releases the spot RIGHT AWAY instead of waiting for the window to
expire, and moves to the next in the queue.

WIRING (2026-07-16, no longer isolated — CLAUDE.md documents the full flow):
the trigger is the patient's "Cancel" button (booking_requests.py:_cancelar calls
`release_slot` when cancelling an approved request with a future
appointment), the opt-in lives in `booking_requests.py` (POST
/api/requests/<id>/waitlist), and the email link's page (`GET
/appointment/offer/<token>` + accept/reject) lives in
`appointment_offer.py` — on accept, it reassigns the request's appointment
and pushes to HSI through the durable journal (best-effort, like an approval). The
expiration/advance sweep runs as a daemon thread by default (`start()`,
started in `server.py:main`, same pattern as `reminders.py`) — `python3 -m
waitlist tick` still works to run it by hand/cron if needed. It's
idempotent and survives restarts because all state lives in the DB, not in
timers. TURNOS_WAITLIST_QUEUE=1 (Tier 4 slice 5, docs/roadmap/
tier4-replatform-design.md) swaps the mechanism for a `procrastinate`
periodic task against the SAME Postgres — see waitlist_queue.py — calling
the exact same `tick()`; the thread and the queue worker are mutually
exclusive by construction, never both running at once.

Nothing breaks if the DB or mail aren't available: like the rest of the
site, it degrades.
"""
import secrets
import sys
import threading
from datetime import datetime, timedelta, timezone

from app.core import config

# The persistence adapter owns EVERY SQL/connection/wire line for the
# `waitlist` + `appointment_offers` tables (see waitlist_store.py). This module
# keeps the orchestration — the pure engine, the sweep, the offer run, the
# email side effect — and reaches the rows only through `store`, never through
# `core.db`'s internals. `core.db`'s public API for OTHER tables
# (booking_requests, used by appointment_offer) is unaffected.
from app.modules.turnos import waitlist_store as store
from app.core import launch_signals
from app.core import log_pii

WINDOW_MIN = 5   # minutes the slot stays guaranteed for a patient
STAGGER_MIN = 5   # offered to the next one after this interval (= the window)


# ---------------------------------------------------------------- pure engine --
#
# No DB, no network, no implicit clock: everything comes in as parameters so
# it can be tested. `oferta_vigente` is a dict with `expires_at` (or None if
# there isn't a live one); `cola` is the list of candidates that have NOT YET
# received an offer for this slot, in FIFO order.

def next_action(slot_dt, ahora, oferta_vigente, cola):
    """Decides the next step for a released appointment slot. Returns a
    tuple:
      ("esperar", None)        there's a live offer and its window hasn't expired
      ("ofrecer", candidato)   offer to the first in the queue (new, or after expiring)
      ("vacante", None)        the slot's time already passed, or there's no one left to offer
    """
    if _minutes(slot_dt, ahora) <= 0:
        return ("vacante", None)
    if oferta_vigente and ahora < oferta_vigente["expires_at"]:
        return ("esperar", None)
    if cola:
        return ("ofrecer", cola[0])
    return ("vacante", None)


def _minutes(despues, antes):
    return (despues - antes).total_seconds() / 60.0


def _now():
    return datetime.now(timezone.utc)


def _new_token():
    return secrets.token_urlsafe(24)


# ------------------------------------------------------------- DB layer --

def opt_in(solicitud_id, dni, email, servicio_id, centro_slug, turno_dt):
    """The patient joins the waitlist for a sooner slot than the one they
    already have (`turno_dt`). Idempotent per active (solicitud_id)."""
    return store.opt_in(solicitud_id, dni, email, servicio_id, centro_slug,
                        turno_dt)


def opt_out(solicitud_id):
    store.opt_out(solicitud_id)


def release_slot(servicio_id, centro_slug, slot_dt, handler=None):
    """A slot opened up: starts an offer run, offering it to the first in
    the queue. Does nothing if there's no one to offer it to or if there's
    barely any time left. Returns the id of the created offer, or None.

    The caller should ALWAYS pass a `turno_dt` that came out of Postgres
    (timestamptz -> aware), like booking_requests.py._cancelar does. But an ISO
    string with no offset (naive) is a legitimate use case for this public
    function — `next_action` compares against `_now()` (aware UTC) and
    subtracting aware-naive raises TypeError, so a naive value is assumed
    UTC here (same criterion as the rest of the module: everything temporal
    is UTC)."""
    # orchestration input rule: accept an aware datetime (timestamptz out of
    # Postgres, like booking_requests._cancelar) OR a naive ISO string; naive
    # is assumed UTC. The persistence adapter takes it from here.
    if isinstance(slot_dt, str):
        slot_dt = datetime.fromisoformat(slot_dt.replace("Z", "+00:00"))
    if slot_dt is not None and slot_dt.tzinfo is None:
        slot_dt = slot_dt.replace(tzinfo=timezone.utc)
    # the live-offer pre-check is not atomic with the subsequent insert by
    # design; store.create_offer's ON CONFLICT is the real duplicate-offer
    # guard (see _create_offer's docstring).
    if store.has_live_offer(servicio_id, centro_slug, slot_dt):
        return None
    cola = store.queue_for_slot(servicio_id, centro_slug, slot_dt, exclude_ids=set())
    return _advance(servicio_id, centro_slug, slot_dt, oferta_vigente=None,
                    cola=cola, handler=handler)


def tick(ahora=None, handler=None):
    """Idempotent sweep: for every slot with a live offer, applies the
    engine. Expires the stale ones and offers to the next, or marks the spot
    vacant. Called every ~1 min from a LaunchAgent/cron. Returns a summary."""
    ahora = ahora or _now()
    resumen = {"expiradas": 0, "ofrecidas": 0, "vacantes": 0}
    for s in store.live_offer_slots():
        r = _process_slot(s["servicio_id"], s["centro_slug"], s["slot_dt"],
                          ahora, handler)
        for k in resumen:
            resumen[k] += r.get(k, 0)
    return resumen


def _process_slot(servicio_id, centro_slug, slot_dt, ahora, handler):
    out = {"expiradas": 0, "ofrecidas": 0, "vacantes": 0}
    ofertas = store.offers_for_slot(servicio_id, centro_slug, slot_dt)
    viva = next((o for o in ofertas if o["estado"] == "ofrecida"), None)
    if viva and ahora < viva["expires_at"]:
        return out  # its window is still live: wait
    if viva:  # expired without being accepted
        store.expire_offer(viva["id"])
        out["expiradas"] += 1
    ya = {o["lista_espera_id"] for o in ofertas}
    cola = store.queue_for_slot(servicio_id, centro_slug, slot_dt, exclude_ids=ya)
    accion, cand = next_action(slot_dt, ahora, oferta_vigente=None, cola=cola)
    if accion == "ofrecer":
        _create_offer(servicio_id, centro_slug, slot_dt, cand, ahora, handler)
        out["ofrecidas"] += 1
    else:
        store.mark_slot_vacant(servicio_id, centro_slug, slot_dt)
        out["vacantes"] += 1
    return out


def _advance(servicio_id, centro_slug, slot_dt, oferta_vigente, cola, handler):
    accion, cand = next_action(slot_dt, _now(), oferta_vigente, cola)
    if accion == "ofrecer":
        return _create_offer(servicio_id, centro_slug, slot_dt, cand,
                             _now(), handler)
    if accion == "vacante":
        store.mark_slot_vacant(servicio_id, centro_slug, slot_dt)
    return None




def _create_offer(servicio_id, centro_slug, slot_dt, cand, ahora, handler):
    """Inserts through `store.create_offer` (which delegates to
    `db_slots.create_appointment_offer`) — ON CONFLICT against
    `appointment_offers_live_slot_uniq` (the only 'ofrecida' offer per
    slot). `_process_slot`/`release_slot` already pre-check for a live offer
    before calling here, but that check-then-act isn't atomic: two workers
    deciding to offer the SAME slot at once (e.g. a cancellation's
    `release_slot` racing `tick()` on that same slot) both pass the
    pre-check. The insert's ON CONFLICT is what actually settles it — losing
    it returns None here, which means "another worker already offered this
    slot" and we skip silently (no duplicate row, no duplicate mail)."""
    token = _new_token()
    oferta_id = store.create_offer(
        slot_dt, servicio_id, centro_slug, cand["id"], token, ahora,
        ahora + timedelta(minutes=WINDOW_MIN))
    if oferta_id is None:
        return None  # otro worker ya ofreció este cupo — no mandar mail
    try:
        from app.notifications import send as notifications
        notifications.notify_offer(cand, slot_dt, token, handler=handler)
    except Exception:
        pass  # mail never breaks the run (same contract as everything else)
    return oferta_id


def claim(token, ahora=None):
    """The patient clicked the email link within their window. Marks the
    offer accepted and returns (ok, oferta|motivo). The HSI reassignment is
    wired on top of this (moves the patient's request to slot_dt). The
    atomic read-then-claim lives in `store.accept_offer`."""
    return store.accept_offer(token, ahora or _now())


def reject_offer(token, ahora=None, handler=None):
    """The patient clicked "I don't want it" within their window: unlike
    letting it expire on its own (tick() catches it the next minute), this
    releases the spot RIGHT AWAY and offers it to the next in the queue
    without waiting. Returns (ok, oferta|motivo) — idempotent: an offer
    that's no longer 'ofrecida' (aceptada/expirada/vacante) isn't reopened,
    it's just confirmed. The atomic read-then-expire lives in
    `store.expire_if_live`; this composes the next-candidate advance on top."""
    o, was_live = store.expire_if_live(token)
    if o is None:
        return (False, "no_existe")
    if not was_live:
        return (True, o)  # already resolved — nothing to do
    cola = store.queue_for_slot(o["servicio_id"], o["centro_slug"], o["slot_dt"],
                                exclude_ids={o["lista_espera_id"]})
    _advance(o["servicio_id"], o["centro_slug"], o["slot_dt"],
             oferta_vigente=None, cola=cola, handler=handler)
    return (True, o)


# ------------------------------------------------------------- sweep (thread) --
#
# Same pattern as reminders.py: a daemon thread started from server.py:main
# that runs tick() every TURNOS_WAITLIST_INTERVAL_MIN (ex
# TURNOS_LISTA_ESPERA_INTERVAL_MIN) minutes. Never brings the server down:
# any failure just stays in the log.

_DEFAULT_INTERVAL_MIN = 2
_stop = threading.Event()


def _log(msg):
    print(f"[waitlist] {msg}", file=sys.stderr, flush=True)


def _env(key, default=""):
    return config.get(key, default)


def _loop(interval_min):
    while not _stop.is_set():
        try:
            resumen = tick()
            if resumen and any(resumen.values()):
                _log(f"barrido: {resumen}")
        except Exception as e:  # noqa: BLE001 — the thread must never die
            _log(f"tick() falló: {log_pii.exc(e)}")
            launch_signals.handled("waitlist_sweep_failed")
        _stop.wait(interval_min * 60)


def _interval_min():
    try:
        # `legacy`: the pre-2026-07-17 name, so vault entries on the old env keep working without a redeploy.
        return int(config.get("TURNOS_WAITLIST_INTERVAL_MIN", default="",
                              legacy="TURNOS_LISTA_ESPERA_INTERVAL_MIN") or _DEFAULT_INTERVAL_MIN)
    except ValueError:
        return _DEFAULT_INTERVAL_MIN


def start():
    """Launches the sweep, unless TURNOS_WAITLIST_OFF=1. Never raises: a
    failure here must not stop the server from starting (same contract as
    reminders.start / watcher.start).

    Tier 4 slice 5 + the worker-topology slice that followed
    (docs/roadmap/tier4-replatform-design.md, "Background daemon threads ->
    Postgres-backed queue"): the flags decide the mechanism, mutually
    exclusive BY CONSTRUCTION — one if/elif chain, exactly one branch
    reached, never two. TURNOS_QUEUE_WORKER=1 (checked
    FIRST) means a SEPARATE worker process (worker.py) owns the sweep: this
    web process starts neither the thread nor the in-process queue.
    Otherwise TURNOS_WAITLIST_QUEUE=1 takes the in-process pilot branch and
    never starts the thread; unset (default, every deployment today) takes
    the thread branch and never even imports waitlist_queue/procrastinate.
    See tests/test_waitlist_queue.py::TestMutualExclusion and
    tests/test_queue_worker.py::TestDelegationMatrix."""
    if config.get("TURNOS_WAITLIST_OFF", default="", legacy="TURNOS_LISTA_ESPERA_OFF") == "1":
        _log("desactivado (TURNOS_WAITLIST_OFF=1)")
        return
    interval_min = _interval_min()
    from app.core import queue_infra
    modo_worker = queue_infra.worker_mode()
    if modo_worker:
        _log(f"delegado al worker externo (TURNOS_QUEUE_WORKER={modo_worker}) — este proceso no barre")
    elif _env("TURNOS_WAITLIST_QUEUE") == "1":
        _start_queue(interval_min)
    else:
        _start_thread(interval_min)


def _start_thread(interval_min):
    try:
        th = threading.Thread(target=_loop, args=(interval_min,),
                              name="waitlist", daemon=True)
        th.start()
        _log(f"iniciado (cada {interval_min} min)")
    except Exception as e:  # noqa: BLE001
        _log(f"no se pudo iniciar: {log_pii.exc(e)}")


def _start_queue(interval_min):
    """TURNOS_WAITLIST_QUEUE=1: the sweep runs as a procrastinate periodic
    task (waitlist_queue.py) instead of this thread. That module is a
    SEPARATE sibling, imported lazily right here — never at this module's
    top level — so the default (queue off) deployment never pays
    procrastinate's import cost (see waitlist_queue.py's own docstring)."""
    try:
        from app.modules.turnos import waitlist_queue
        waitlist_queue.start(interval_min)
        _log(f"cola (procrastinate) iniciada (cada {interval_min} min)")
    except Exception as e:  # noqa: BLE001
        _log(f"cola no se pudo iniciar: {log_pii.exc(e)}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "tick":
        print(tick())
