"""slot_holds.py — soft-lock on appointment slots while the wizard is filled in.

Decisión 2026-07-16 (reunión con el mantenedor): hoy NADA bloquea un slot — dos
personas pueden elegir el mismo horario y lo que viaja es sólo un texto
("preferencia"). Esto agrega un lock server-side de corta duración: el
momento en que alguien EN EL WIZARD hace click en un horario concreto, ese
slot queda tomado para los demás por unos minutos mientras completa el resto
del formulario. Sólo MIRAR la grilla nunca bloquea nada — el lock es al
elegir, no al listar.

``slot_holds_router.py`` owns the native HTTP transport; this module owns the
value-oriented slot-hold decisions and persistence calls.

Routes:
  POST   /api/slot-hold          {servicio_id, dia, hora, token?}
                                   -> 200 {ok:true, token, ttl_min}
                                   -> 409 {"error":"turno_tomado"} (another token holds it)
                                   -> 429 (rate limit per IP)
  DELETE /api/slot-hold/<token>  releases the token's hold -> always {ok:true}

`taken(exclude_token=None)` is used by patient_web/site_*.py (band + wizard) so it
never offers/promotes a slot someone else is holding — the SAME source for
the band and for what the wizard lets you pick (the existing invariant "what
the band promises, the wizard can offer"). `exclude_token` lets the hold's
own owner still see their chosen slot (it's not hidden from themselves).

Import-safe: stdlib + db/catalog/abuse_guard only (all already lazy-safe);
never blocks server boot.
"""

import os
import re
import json
import time
import hmac
import hashlib
import secrets
from datetime import timezone

from app.core import db
from app.core import paths
from app.core import db_booking
from app.core import db_slots
from app.core import config
from app.core import tz as _tz
from app.core import rate_window
from app.modules.turnos import hsi_busy

# Tier 2 batch D: this file moved from repo root into modules/turnos/, two
# directories deeper — three dirname() calls to reach the repo root now.
ROOT = paths.PROJECT_ROOT

_ART = _tz.tzinfo()  # instalación (zoneinfo, DST-safe — ver tz.py)
# Cuánto le guardamos el horario a quien lo está eligiendo (responsable del producto, 2026-07-28:
# "reserve three minutes for a logged in user and ten for a non-logged in
# one"). Eran 15 para todos. Quien ya tiene sesión no completa NADA más que
# confirmar —la cuenta, el paciente y el contacto ya están— y el 99% termina en
# segundos; quien no la tiene todavía tiene que registrarse o ingresar en el
# medio, que es lo que justifica los 10. Un TTL corto es lo que devuelve el
# horario a la agenda cuando alguien abandona la pestaña.
_TTL_LOGGED_IN_MIN = 3
_TTL_ANON_MIN = 10
# El default del módulo es el LARGO a propósito: `booking_requests._create` reusa
# `try_take` en el submit final y ahí un TTL corto no aporta nada — sólo
# podría vencerse mientras se escribe la fila.
_TTL_MIN = _TTL_ANON_MIN
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def _env(key, default=""):
    return config.get(key, default)


def _service_by_id(sid):
    try:
        from app.patient_web import catalog
        for it in catalog.services() or []:
            if isinstance(it, dict) and str(it.get("id")) == str(sid):
                return it
    except Exception:  # noqa: BLE001
        pass
    return None


def _service_holdable(s):
    """Whether a slot for this service can ever be soft-locked at all.

    Delegates to `site_availability._offers_appointments` — the SAME test that
    decides whether the wizard can offer a concrete time for it: always true
    for `solicitud`/`solicitud_con_orden`, and ALSO true for
    `walkin`/`walkin_con_orden` when the service opens its same-day listing
    (`apertura_mismo_dia` — the one crack in "walk-in never offers
    appointments", see `_page_service_appointment`, which books those on the
    wizard exactly like a `solicitud*` service). Data-integrity guards
    (2026-08-05, tier 14): a service the front desk can book
    (`admin_appointments`, gated on that same `_offers_appointments`) must
    always be a service whose live patient holds this module can see —
    otherwise the desk could book straight over a patient mid-wizard on a
    same-day-opening slot with no conflict ever detected. Before this fix
    only `solicitud*` was holdable, so a patient could never even acquire a
    hold on those same-day slots in the first place.

    Lazy import: patient_web/site_*.py is heavy and this file stays import-safe at
    module load (see the module docstring); patient_web/site_*.py itself only
    lazy-imports slot_holds inside functions, so there's no load-time cycle
    either way."""
    if not isinstance(s, dict):
        return False
    try:
        from app.patient_web import site_availability
        return bool(site_availability._offers_appointments(s))
    except Exception:  # noqa: BLE001 — degrade to the narrower, always-safe test
        return s.get("modo") in ("solicitud", "solicitud_con_orden")


# ---- taken: active holds + future approved (short cache, public read) --

_OC_TTL = 3.0  # segundos — mismo orden de magnitud que el TTL de catalog._db_rows
_TAKEN_CACHE = {"ts": 0.0, "set": set()}


def _taken_raw():
    """{(servicio_id, dia, hora)} of active holds (ANY token) + future
    approved appointments + times under a live waitlist offer, not filtered by exclude_token — this is cached,
    the per-token filter is applied afterward (cheap, in memory)."""
    now = time.time()
    if now - _TAKEN_CACHE["ts"] < _OC_TTL:
        return _TAKEN_CACHE["set"]
    out = set()
    for h in db_slots.holds_active():
        out.add((str(h.get("servicio_id")), str(h.get("dia")), str(h.get("hora"))))
    for row in _approved_future_slots():
        out.add(row)
    for row in _live_offer_slots():
        out.add(row)
    out |= hsi_busy.busy_by_service()  # appointments created straight in HSI
    _TAKEN_CACHE["ts"] = now
    _TAKEN_CACHE["set"] = out
    return out


def _approved_future_slots():
    """(servicio_id, dia, hora) in ART for every approved request with a
    future appointment. `listar_aprobadas_futuras` already filters
    turno_dt >= now() in SQL (UTC), so all that's left is projecting each
    timestamptz to the hospital's local date/time — CONVERTING before
    reading day/hour is the gotcha already documented in CLAUDE.md (never
    trust the column's raw offset)."""
    out = []
    try:
        rows = db_booking.list_approved_future()
    except db.DbUnavailable:
        return out
    for r in rows:
        sid = r.get("servicio_id")
        dt = r.get("turno_dt")
        if not sid or dt is None:
            continue
        local = dt.astimezone(_ART) if dt.tzinfo else dt.replace(tzinfo=timezone.utc).astimezone(_ART)
        out.append((str(sid), local.strftime("%Y-%m-%d"), local.strftime("%H:%M")))
    return out


def _live_offer_slots():
    """(servicio_id, dia, hora) in ART of every time offered to a waitlisted patient whose window is
    still open (the email says «por 5 minutos este turno es tuyo, garantizado»): the offer holds its
    time like a hold does. It stops holding when the window closes, when the patient says no, and
    when she takes it (then the approved appointment holds it)."""
    try:
        from app.modules.turnos import waitlist_store
        rows = waitlist_store.live_offer_times()
    except db.DbUnavailable:
        return []
    out = []
    for sid, dt in rows:
        if not sid or dt is None:
            continue
        local = dt.astimezone(_ART) if dt.tzinfo else dt.replace(tzinfo=timezone.utc).astimezone(_ART)
        out.append((str(sid), local.strftime("%Y-%m-%d"), local.strftime("%H:%M")))
    return out


def held_or_offered():
    """{(servicio_id, dia, hora)} under an active hold (any token) or a live waitlist offer, read
    NOW (no cache): someone is choosing or has been promised that time, so the desk must not put a
    cancelled appointment back on it (the patient file's «Restaurar», 2026-11-05)."""
    out = {(str(h.get("servicio_id")), str(h.get("dia")), str(h.get("hora")))
           for h in db_slots.holds_active()}
    out.update(_live_offer_slots())
    return out


def take_for_restore(servicio_id, dia, hora, token):
    """True if `token` gets the minute's hold — the same atomic insert a patient's grid click goes
    through — so nobody can start choosing it while the desk's «Restaurar» writes. Unlike `try_take`
    it does not refuse a minute another doctor of the same service holds: the restore checks its
    own doctor (`booking_decisions.restore_blocker`). The caller releases it in a `finally`."""
    ok = db_slots.hold_acquire(servicio_id, dia, hora, token)
    invalidate()
    return ok


def invalidate():
    """Discards the taken-slots cache — no need to call it in the normal
    path (the 3s TTL already refreshes it), but it's left handy for tests."""
    _TAKEN_CACHE["ts"] = 0.0
    _TAKEN_CACHE["set"] = set()


def taken(exclude_token=None):
    """{(servicio_id, dia, hora)} held by OTHERS — active holds from any
    token except `exclude_token` (the hold's owner still sees THEIR own
    chosen slot) plus approved requests with a future appointment plus the
    times a waitlisted patient has been offered and may still claim.
    `exclude_token` is one token or several (a browser's own token and its
    account's, see `owner_token`)."""
    base = set(_taken_raw())
    if isinstance(exclude_token, (list, tuple, set, frozenset)):
        own = {t for t in exclude_token if t}
    else:
        own = {exclude_token} if exclude_token else set()
    if own:
        mine = set()
        for h in db_slots.holds_active():
            # only a LOOSE hold is the caller's pick; one linked to a request
            # already stands for that appointment and blocks its owner too
            if h.get("token") in own and not h.get("solicitud_id"):
                mine.add((str(h.get("servicio_id")), str(h.get("dia")), str(h.get("hora"))))
        base -= mine
    return base


# ---- own rate limit (sliding window per IP) --------------------------

_HITS = {}
_HOUR = 3600


def _per_hour():
    try:
        v = _env("TURNOS_HOLD_PER_HOUR")
        return int(v) if v else 120
    except ValueError:
        return 120


def _allowed(ip):
    return rate_window.allow(_HITS, ip, _per_hour(), _HOUR)


# ---- acquire a hold (shared by the endpoint and booking_requests._create) --

def try_take(servicio_id, dia, hora, token, ttl_min=_TTL_MIN):
    """True if `token` gets (servicio_id, dia, hora) — took it fresh or
    renewed it —, False if it's taken (by a hold from ANOTHER token or by an
    already-approved request). The single entry point to a hold: used both
    by `POST /api/slot-hold` (grid click) and by `booking_requests._create`
    (final submit with a chosen slot) so the "approved always wins" rule
    isn't duplicated in two places."""
    if (servicio_id, dia, hora) in (set(_approved_future_slots()) | set(_live_offer_slots())
                                    | hsi_busy.busy_by_service()):
        return False
    ok = db_slots.hold_acquire(servicio_id, dia, hora, token, ttl_min=ttl_min)
    invalidate()  # the next taken() call must already see this change
    return ok


# ---- POST /api/slot-hold ---------------------------------------------------

_PROCESS_KEY = secrets.token_hex(32)


def owner_token(user):
    """The hold token of a logged-in patient: one per ACCOUNT, not per browser
    (2026-11-15). The same person with the confirmation open on the phone and
    on the desktop is one owner — the desktop's hold must not hide the slot
    from the phone. Keyed HMAC of the DNI, so it can't be guessed from the
    DNI and says nothing about it. None without a session."""
    if not user:
        return None
    dni = user.get("dni") if isinstance(user, dict) else getattr(user, "dni", None)
    dni = str(dni or "").strip()
    if not dni:
        return None
    secret = config.get("TURNOS_ADMIN_SECRET")
    password = config.get("TURNOS_ADMIN_PASS")
    # never a key written in the source: the installation's secret, else its
    # admin password (as admin_auth signs), else one random per process
    raw = secret or ("pass:" + str(password) if password else _PROCESS_KEY)
    key = hashlib.sha256(("st-slot-hold$" + str(raw)).encode("utf-8")).digest()
    return "acct-" + hmac.new(key, dni.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def own_tokens(hold_token, user):
    """Every token whose holds count as the caller's own: the browser's token
    plus its account's."""
    return tuple(t for t in (hold_token, owner_token(user)) if t)


def create_hold(ip, raw_body, ttl_for, owner_for=None):
    """Pure domain logic for `POST /api/slot-hold`.

    `ip`: the caller's real IP, already resolved from explicit headers and
    client host by the transport. `raw_body`: the raw request body bytes;
    an empty body is treated as `{}`. `ttl_for`: a zero-arg callable resolving to the
    hold's TTL in minutes, called LAZILY on purpose: it reads Postgres (via
    `_session_user`/`accounts.current_user`) and the original code only ever paid
    for that once every cheaper check already passed. `owner_for`: an
    optional zero-arg callable resolving to the logged-in patient (or None),
    also lazy; with a patient the hold is taken under the account's token
    (`owner_token`) while the browser's own token is returned, so every
    device of the same account holds the same slot as one owner. A browser token the client
    sends that isn't the account's gives up its loose holds first (the
    slot it picked before logging in moves to the account).

    Returns `(status, payload)`."""
    if not _allowed(ip):
        return 429, {"error": "demasiadas consultas, esperá un rato"}

    try:
        body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception as e:  # noqa: BLE001
        return 400, {"error": f"JSON inválido: {e}"}

    servicio_id = str(body.get("servicio_id") or "").strip()
    dia = str(body.get("dia") or "").strip()
    hora = str(body.get("hora") or "").strip()
    token = str(body.get("token") or "").strip() or secrets.token_urlsafe(16)

    if not servicio_id or not _DAY_RE.match(dia) or not _TIME_RE.match(hora) \
            or db_slots.slot_key(dia, hora) is None:   # «2026-13-45» pasa la regex, no existe
        return 400, {"error": "servicio_id/dia/hora inválidos"}

    s = _service_by_id(servicio_id)
    if not s or not _service_holdable(s):
        return 400, {"error": "servicio no reservable"}

    from app.modules.turnos import booking_horizon
    error = booking_horizon.check_slot(servicio_id, dia, hora)
    if error:
        return 409, error
    ttl = ttl_for()
    # The browser keeps ITS token (the wizard also sends it as the booking's
    # idem_key, so it must stay per browser); the hold itself goes under the
    # account's token, and the routes resolve both as the caller's own.
    owner = token
    account = owner_token(owner_for()) if owner_for else None
    if account and account != token:
        if body.get("token") and not token.startswith("acct-"):
            db_slots.hold_release(token)   # another account's token is never released here
        owner = account
    if not try_take(servicio_id, dia, hora, owner, ttl_min=ttl):
        return 409, {"error": "turno_tomado"}
    return 200, {"ok": True, "token": token, "ttl_min": ttl}


def _session_user(request):
    """The patient logged in on `request`, or None (also when it can't tell)."""
    try:
        from app.cuentas import accounts
        return accounts.current_user(request)
    except Exception:  # noqa: BLE001
        return None


def _ttl_for(user):
    """Hold minutes depending on whether there's a session or not. When it
    can't tell (DB down, odd import — `_session_user` answers None) it returns
    the LONG one: erring on the side of holding someone's slot a bit too long
    beats taking it away."""
    if user:
        return _TTL_LOGGED_IN_MIN
    return _TTL_ANON_MIN


def release_hold(token, user=None):
    """Pure domain logic for `DELETE /api/slot-hold/<token>`. Always
    succeeds — `db_slots.hold_release` on a token nobody holds is a no-op, not an
    error, same contract as before. With a logged-in `user` the account's
    loose hold goes too (that's where `create_hold` put it). Returns `(status, payload)`."""
    for t in own_tokens(token, user):
        db_slots.hold_release(t)
    invalidate()
    return 200, {"ok": True}
