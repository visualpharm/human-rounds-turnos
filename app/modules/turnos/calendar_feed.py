"""calendar_feed.py — add approved appointments to the patient's calendar.

Owner of this module:
  GET /api/appointment/<id>.ics  a single appointment (requires a session; the
                                  appointment must belong to the logged-in dni
                                  and be approved)
  GET /api/calendar/feed         {url, hint} of the logged-in user's subscription
                                  feed: the full url only the first time (the
                                  address is created then), later url null and
                                  the token's last 4 characters — the database
                                  keeps only its hash (core/feed_tokens.py)
  POST /api/calendar/feed/regenerate  {url, hint}: a new address, shown once;
                                  the previous one stops updating
  GET /feed/appointments/<token>.ics  public feed of ALL future approved
                                  appointments for the token's owner (the
                                  token IS the authentication, same as a
                                  recovery link — no one else has it)
  GET /feed/doctor/<token>.ics    same token-is-the-auth pattern, but from the
                                  DOCTOR's side: their own future approved
                                  appointments (doctor_appointments.appointments_of
                                  matches doctor↔request; operators.feed_token
                                  is the analogue of users.feed_token, hashed
                                  the same way, db_operators.operator_feed_token_issue)
  GET /appointment/<token>.ics   single-event download for the "Al calendario"
                                  link in the approved/reminder emails
                                  (2026-08-03) — NO session: reuses
                                  booking_requests.confirm_token, the same
                                  token the day-before "¿vas a poder venir?"
                                  link already authenticates a GET with
                                  (appointment_confirm.py), so a calendar
                                  download over it adds no new exposure. Only
                                  the LIVE row's estado is trusted at fetch
                                  time (not whatever it was when the email
                                  went out): a cancelled/rescheduled request
                                  just 404s, it never serves a stale event.

Format: ICS with a VTIMEZONE for the installation timezone (tz.py), built as
a single fixed-offset STANDARD block — correct for Argentina (no DST since
2009) and any other non-DST zone, but NOT a full RFC 5545 STANDARD+DAYLIGHT
pair, so a deployment whose timezone DOES observe DST would need this
VTIMEZONE construction extended before its calendar feed is fully correct
across a DST transition (flagged, not solved — see docs/architecture/data-packs.md
Phase 2c). One VEVENT per appointment; the feed has several. Each event
carries: title "{servicio} en {centro}", a LOCATION with the center's
address (so the calendar app's map works), and a DESCRIPTION with the
patient's name (to recognize whose appointment it is on a shared/family
calendar).

Import-safe: db is imported at the top (base module, same as
accounts.py/booking_requests.py); if the DB isn't there, DbUnavailable -> 503.
accounts.current_user is imported lazily (same pattern as
booking_requests._get_user) because accounts.py is authored by another agent.
"""

import json
import re
from datetime import datetime, timezone, timedelta

from app.cuentas import claim_gate
from app.cuentas import session_user
from app.core import db
from app.core import db_booking
from app.core import db_operators
from app.core import db_accounts
from app.core import db_slots
from app.core import sitedata
from app.core import site_identity
from app.core import theme
from app.core import tz as _tz

_ART = _tz.tzinfo()  # installation timezone (zoneinfo, DST-safe — see tz.py)
_TZID = _tz.tz_name()

# Same shape as appointment_confirm._TOKEN_RE (confirm_token is secrets.
# token_urlsafe(24)): rejects anything that isn't the token itself before it
# ever reaches a query.
_ICS_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ---- typed errors for the extracted domain functions ------------------------
#
# (docs/roadmap/native-migration-campaign-20260826.md)
# the resolve_* functions below take parsed arguments and either return data
# or raise ONE of these (never db.DbUnavailable's caller-facing 503 shape,
# which is already its own exception). `calendar_feed_router.py` turns that
# one domain decision tree into native responses.

class AuthRequired(Exception):
    """No logged-in user (-> 401 auth_requerida)."""


class InvalidId(Exception):
    """Non-integer appointment id (-> 400 id_invalido)."""


class NotFoundError(Exception):
    """No matching row / token / feed / event (-> 404 no_encontrado)."""


_VALARM = (
    "BEGIN:VALARM\r\n"
    "ACTION:DISPLAY\r\n"
    "DESCRIPTION:Recordatorio de turno\r\n"
    "TRIGGER:-PT30M\r\n"
    "END:VALARM\r\n"
)


def _ics_offset():
    """ICS UTC-offset string like '-0300' for the installation timezone,
    computed via zoneinfo (correct for any non-DST zone — the STANDARD-only
    VTIMEZONE below has no DAYLIGHT sub-block, see module docstring)."""
    off = datetime.now(_ART).utcoffset()
    total_min = int(off.total_seconds() // 60)
    sign = "-" if total_min < 0 else "+"
    total_min = abs(total_min)
    return "%s%02d%02d" % (sign, total_min // 60, total_min % 60)


_VTIMEZONE = (
    "BEGIN:VTIMEZONE\r\n"
    "TZID:%s\r\n"
    "BEGIN:STANDARD\r\n"
    "DTSTART:19700101T000000\r\n"
    "TZOFFSETFROM:%s\r\n"
    "TZOFFSETTO:%s\r\n"
    "TZNAME:%s\r\n"
    "END:STANDARD\r\n"
    "END:VTIMEZONE\r\n"
) % (_TZID, _ics_offset(), _ics_offset(), _ics_offset()[:3])


# ---- catalog (goes through catalog.py; tolerant fallback to the raw JSON) -----

def _service(sid):
    try:
        from app.patient_web import catalog
        for s in catalog.services():
            if s.get("id") == sid:
                return s
        return None
    except Exception:  # noqa: BLE001
        pass
    path = sitedata.path("servicios.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    for s in data.get("servicios", []):
        if s.get("id") == sid:
            return s
    return None


def _center(slug):
    try:
        from app.patient_web import catalog
        for c in catalog.centers():
            if c.get("slug") == slug:
                return c
        return None
    except Exception:  # noqa: BLE001
        pass
    path = sitedata.path("centros-salud.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    for c in data.get("centros", []):
        if c.get("slug") == slug:
            return c
    return None


# Same pattern as booking_requests._get_user: lazy import, never raises.
_current_user = session_user.current_or_none


# ---- building the ICS ---------------------------------------------------

def _escape(s):
    return str(s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _dt_local(dt):
    """turno_dt (aware, any timezone) -> Buenos Aires wall-clock time, ICS format."""
    return dt.astimezone(_ART).strftime("%Y%m%dT%H%M%S")


def _dt_from_hold(hold):
    """{'dia': 'YYYY-MM-DD', 'hora': 'HH:MM'} from a turno_holds row -> an
    aware datetime in ART (the module's own fixed timezone, never the raw
    offset). None if the hold is missing or malformed (never raises — the
    .ics degrades to a 404)."""
    if not hold:
        return None
    try:
        y, mo, d = (int(x) for x in str(hold["dia"]).split("-")[:3])
        hh, mm = (int(x) for x in str(hold["hora"]).split(":")[:2])
        return datetime(y, mo, d, hh, mm, tzinfo=_ART)
    except (KeyError, ValueError, TypeError):
        return None


def _event(row, usuario, start=None, pendiente=False):
    """A VEVENT built from a solicitudes row.

    `start` is optional: an approved one gets it from `row['turno_dt']`; a
    pending one with a hold receives it already resolved by the caller
    (`_dt_from_hold`) because the chosen time lives in `turno_holds`, not in
    the row. `pendiente` adds " (pendiente)" to the title — the SAME
    request, once approved, keeps the same UID (turno-<id>@sanaturnos): the
    patient's calendar updates the event instead of duplicating it.

    The name in the DESCRIPTION is the PATIENT's, which is the row's — not
    the account's: a child's appointment is saved with their own name and
    DNI, and the parent books it from their own session. If the row doesn't
    carry it, it falls back to the user. Returns None if the service, the
    center, or a time is missing (nowhere/nowhen to show)."""
    servicio = _service(row.get("servicio_id"))
    centro = _center(row.get("centro_slug"))
    if not servicio or not centro:
        return None

    start = start or row.get("turno_dt")
    if not start:
        return None

    titulo = "%s en %s" % (servicio.get("nombre") or row["servicio_id"], centro.get("name") or "")
    if pendiente:
        titulo += " (pendiente)"
    nombre = " ".join(x for x in (row.get("nombre"), row.get("apellido")) if x).strip()
    if not nombre:
        nombre = " ".join(x for x in (usuario.get("nombre"), usuario.get("apellido")) if x).strip()
    descripcion = ("Turno de %s. Gestionado por %s." % (nombre, theme.brand_name())) if nombre \
        else "Gestionado por %s." % theme.brand_name()
    direccion = centro.get("address")
    ubicacion = ", ".join(x for x in (direccion, site_identity.get("city"), site_identity.get("province"), "Argentina") if x)

    end = start + timedelta(minutes=40)
    return (
        "BEGIN:VEVENT\r\n"
        "UID:turno-%s@sanaturnos\r\n"
        "DTSTAMP:%s\r\n"
        "DTSTART;TZID=%s:%s\r\n"
        "DTEND;TZID=%s:%s\r\n"
        "SUMMARY:%s\r\n"
        "LOCATION:%s\r\n"
        "DESCRIPTION:%s\r\n"
        "%s"
        "END:VEVENT\r\n"
    ) % (row["id"], _stamp(), _TZID, _dt_local(start), _TZID, _dt_local(end),
         _escape(titulo), _escape(ubicacion), _escape(descripcion), _VALARM)


def _doctor_event(row):
    """VEVENT for the DOCTOR's feed. The feed leaves the installation for an
    outside calendar (Google, Apple), so it carries NOTHING that identifies
    the patient — no name, no DNI, no phone (2026-10-27, «PII stays home»):
    the title is «Turno · <servicio>», the place is the center with its
    address (the calendar's map works), and the description and URL link to
    that appointment in the panel, where the doctor sees who it is behind
    their own login."""
    servicio = _service(row.get("servicio_id"))
    centro = _center(row.get("centro_slug"))
    if not servicio or not centro:
        return None

    titulo = "Turno · %s" % (servicio.get("nombre") or row["servicio_id"])
    ubicacion = ", ".join(x for x in (centro.get("name"), centro.get("address"), site_identity.get("city"),
                                      site_identity.get("province"), "Argentina") if x)
    enlace = "%s/admin/mis-turnos#turno-%s" % (site_identity.base_url(), row["id"])
    descripcion = "Ver el turno en el panel: %s" % enlace

    start = row["turno_dt"]
    end = start + timedelta(minutes=40)
    return (
        "BEGIN:VEVENT\r\n"
        "UID:turno-%s@sanaturnos\r\n"
        "DTSTAMP:%s\r\n"
        "DTSTART;TZID=%s:%s\r\n"
        "DTEND;TZID=%s:%s\r\n"
        "SUMMARY:%s\r\n"
        "LOCATION:%s\r\n"
        "DESCRIPTION:%s\r\n"
        "URL:%s\r\n"
        "%s"
        "END:VEVENT\r\n"
    ) % (row["id"], _stamp(), _TZID, _dt_local(start), _TZID, _dt_local(end),
         _escape(titulo), _escape(ubicacion), _escape(descripcion), enlace, _VALARM)


def _build_calendar(nombre_feed, eventos):
    ics = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//%s//ES\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "METHOD:PUBLISH\r\n"
        "X-WR-CALNAME:%s\r\n"
        "X-WR-TIMEZONE:%s\r\n"
    ) % (theme.brand_name(), _escape(nombre_feed), _TZID)
    ics += _VTIMEZONE
    for ev in eventos:
        ics += ev
    ics += "END:VCALENDAR\r\n"
    return ics


def resolve_appointment_ics(request, sid):
    """Pure domain function behind GET /api/appointment/<id>.ics: `request`
    is only forwarded to `_current_user()` (session lookup — any object whose
    `.headers.get(...)` behaves like a real request's works, see
    `accounts.current_user`'s own contract). Returns `(ics_text, filename)`;
    raises `AuthRequired`/`InvalidId`/`NotFoundError`/`db.DbUnavailable`.
    It never writes a response; the native router owns that boundary."""
    usuario = _current_user(request)
    if not usuario:
        raise AuthRequired()
    try:
        sid_int = int(sid)
    except ValueError:
        raise InvalidId()
    row = db_booking.get_booking_request(sid_int)
    # Owner of the appointment = the patient (dni) OR the adult who booked it
    # (solicitante_dni): a child's appointment is saved with the child's DNI,
    # so looking only at `dni` left the parent unable to download the .ics
    # of the appointment they themselves requested. `list_by_dni` already
    # uses this same criterion (dni OR solicitante_dni).
    mio = usuario.get("dni") in (row.get("dni"), row.get("solicitante_dni")) if row else False
    if not row or not mio:
        raise NotFoundError()

    estado = row.get("estado")
    ev = None
    if estado == "aprobada" and row.get("turno_dt"):
        ev = _event(row, usuario)
    elif estado == "pendiente":
        # A pending request only has a date if it picked a concrete time in
        # the wizard (soft-lock, see slot_holds.py) — without a hold there's
        # nothing to put on the calendar yet (a band like "A la mañana"
        # doesn't count).
        hold = db_slots.hold_of_booking_request(sid_int)
        start = _dt_from_hold(hold)
        if start is not None:
            ev = _event(row, usuario, start=start, pendiente=True)

    if ev is None:
        raise NotFoundError()
    return _build_calendar(f"Mis turnos — {theme.brand_name()}", [ev]), "turno.ics"


def resolve_token_ics(token):
    """Pure domain function behind GET /appointment/<confirm_token>.ics —
    the "Al calendario" link in the approved/reminder emails
    (notifications._calendar_ics_url), NO session (2026-08-03). The token IS
    the authentication, same trust model as appointment_confirm.py's GET (it
    arrived by email to the account that requested the appointment).
    Read-only: unlike the confirm/reject POSTs, there's nothing here to
    mutate — a link scanner fetching this is harmless. Only an aprobada
    request with a concrete turno_dt has anything to download; anything else
    (still pending, cancelled since the email went out, or the token doesn't
    exist) raises NotFoundError, never serves a stale event."""
    if not token or not _ICS_TOKEN_RE.match(token):
        raise NotFoundError()
    row = db_booking.booking_request_by_confirm_token(token)
    if not row or row.get("estado") != "aprobada" or not row.get("turno_dt"):
        raise NotFoundError()
    ev = _event(row, {})
    if ev is None:
        raise NotFoundError()
    return _build_calendar(f"Mi turno — {theme.brand_name()}", [ev]), "turno.ics"


def patient_feed_url(token):
    """The subscription address of a patient's feed token."""
    return "%s/feed/appointments/%s.ics" % (site_identity.base_url(), token)


def doctor_feed_url(token):
    """The subscription address of a doctor's feed token."""
    return "%s/feed/doctor/%s.ics" % (site_identity.base_url(), token)


def _feed_view(issued, url_of):
    """{url, hint} for a screen: the full url only when the token is in the
    clear (just created or replaced), else url None and the last 4 characters."""
    token = issued.get("token")
    return {"url": url_of(token) if token else None, "hint": issued.get("hint")}


def resolve_feed_token(request):
    """Pure domain function behind GET /api/calendar/feed: the logged-in
    user's own address — never someone else's. The first call creates it and
    returns {url, hint}; later calls {url: None, hint}: the database keeps
    only the token's hash. Raises AuthRequired/db.DbUnavailable."""
    usuario = _current_user(request)
    if not usuario:
        raise AuthRequired()
    issued = db_accounts.feed_token_issue(usuario["dni"])
    if issued is None:
        raise AuthRequired()
    return _feed_view(issued, patient_feed_url)


def regenerate_feed_token(request):
    """Pure domain function behind POST /api/calendar/feed/regenerate: a new
    address for the logged-in user, shown once; the previous one stops
    resolving (404) in the same write. Returns {url, hint}; raises
    AuthRequired/db.DbUnavailable."""
    usuario = _current_user(request)
    if not usuario:
        raise AuthRequired()
    token = db_accounts.feed_token_regenerate(usuario["dni"])
    if not token:
        raise AuthRequired()
    return {"url": patient_feed_url(token), "hint": token[-4:]}


def resolve_feed_ics(token):
    """Pure domain function behind GET /feed/appointments/<token>.ics —
    public feed of ALL future approved (+ pending-with-a-hold) appointments
    for the token's owner. Returns `(ics_text, filename)`; raises
    NotFoundError/db.DbUnavailable."""
    usuario = db_accounts.user_by_feed_token(token)
    if not usuario:
        raise NotFoundError()
    rows = [] if claim_gate.hidden(usuario["dni"]) else db_booking.list_by_dni(usuario["dni"])
    ahora = datetime.now(timezone.utc)
    eventos = []
    for row in rows:
        estado = row.get("estado")
        ev = None
        if estado == "aprobada" and row.get("turno_dt"):
            if row["turno_dt"] < ahora:
                continue
            ev = _event(row, usuario)
        elif estado == "pendiente":
            # Only added to the feed if a concrete time (hold) was already
            # picked and that time is still in the future — once the
            # hospital approves it, the SAME request (same UID) moves to the
            # approved branch above and the title loses the suffix: the
            # calendar app updates the existing event instead of
            # duplicating it.
            try:
                hold = db_slots.hold_of_booking_request(row["id"])
            except db.DbUnavailable:
                continue
            start = _dt_from_hold(hold)
            if start is None or start < ahora:
                continue
            ev = _event(row, usuario, start=start, pendiente=True)
        if ev:
            eventos.append(ev)
    return _build_calendar(f"Mis turnos — {theme.brand_name()}", eventos), "mis-turnos.ics"


def rotate_doctor_feed(identity):
    """Pure body of POST /api/admin/my-appointments/feed/rotate: a new
    subscription address for the logged-in doctor; the previous one stops
    resolving (404) in the same write. Returns {url, hint}, the only moment
    besides its creation the full address is shown; raises NotFoundError for
    any role other than a doctor's own account."""
    if (identity or {}).get("rol") != "medico":
        raise NotFoundError()
    token = db_operators.operator_rotate_feed_token(identity["usuario"])
    if not token:
        raise NotFoundError()
    return {"url": doctor_feed_url(token), "hint": token[-4:]}


def resolve_doctor_feed_ics(token):
    """Pure domain function behind GET /feed/doctor/<token>.ics — the
    DOCTOR's feed, same token-is-the-auth contract as resolve_feed_ics, but
    resolving the token against `operadores` (not `usuarios`) and matching
    appointments via doctor_appointments.appointments_of
    (medico_asignado_id, or by name if that row doesn't have it yet).
    Returns `(ics_text, filename)`; raises NotFoundError/db.DbUnavailable."""
    operador = db_operators.operator_by_feed_token(token)
    medico_id = operador.get("medico_id") if operador else None
    # A deactivated account's feed address is as dead as its sessions (the patient's feed already
    # dies with the archived account, db_accounts.user_by_feed_token): the token alone would otherwise keep
    # handing the patients' names and DNIs to whoever still holds the subscription.
    if not operador or operador.get("rol") != "medico" or not medico_id or not operador.get("activo"):
        raise NotFoundError()
    try:
        from app.modules.turnos import doctor_appointments
    except Exception:  # noqa: BLE001
        raise NotFoundError()
    medico = doctor_appointments.doctor_of(medico_id)
    rows = doctor_appointments.appointments_of(medico_id, (medico or {}).get("nombre"),
                                   desde=datetime.now(timezone.utc))
    eventos = [ev for ev in (_doctor_event(row) for row in rows) if ev]
    return _build_calendar(f"Mis turnos — {theme.brand_name()}", eventos), "mis-turnos-medico.ics"
