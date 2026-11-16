"""core/access_log.py — who opened a patient's clinical data, and may they.

Two rules, one place (Ley 26.529 art. 18, Ley 25.326 art. 9-10, Res. AAIP
47/2018; K10 of the launch plan, 2026-10-23):

* **Every staff read of clinical data leaves a row** in `clinical_access_log`
  — the ficha, a pre-consult interview, a consultation, the AI chart summary,
  the clinical history, the labs, a referral file, the health profile, a
  printable. Denied attempts are recorded too (`allowed = false`). The table
  only grows: the database refuses UPDATE and any DELETE outside the
  retention purge (`GUARD_SQL` in revision 0026); the application role has no
  TRUNCATE (`scripts/db_app_role.py`).
* **A doctor only reaches their own patients**: a patient is a doctor's when a
  booking request of that clinical DNI is assigned to that doctor or booked in
  that doctor's agenda, or a pre-consult interview of that DNI is addressed to
  that doctor (`db_clinical.doctor_has_patient`). Any other role is decided by its
  permissions at the central gate, as before. The one way past it is the
  permission `todos_los_pacientes` (the product owner, 2026-10-23: heads of service and
  audit work without being administrators): off by default, in no preset,
  granted per role or per user; every read it allows is still recorded.

The patient is stored as `dni_hash`, an HMAC-SHA256 of the clinical DNI keyed
with `TURNOS_ACCESS_LOG_KEY` (falls back to `TURNOS_ADMIN_SECRET`): the admin
looks a patient up by DNI, a dump of the table does not list anyone's
document. Changing the key makes older rows unreachable by DNI — keep it.

A read whose access row cannot be written is refused: `AccessLogUnavailable`
(503) when the database does not answer, `AccessLogFailed` (the server's own
error) for any other failure. The clinical data lives in the same database,
so this only bites when the log itself is broken, and then no read goes
unrecorded.
"""
import hashlib
import hmac
import threading
from datetime import datetime

from app.core import config, db, tz
from app.core import db_operators
from app.core import db_clinical
from app.core import ids as _ids

# Closed list of what was opened. The admin screen renders these ids through
# the catalog (admin.access_log.action.<id>); keep the two in step.
ACTIONS = (
    "ficha",              # the patient's file in Pacientes
    "historia_externa",   # on-demand HSI/EMR history read
    "resumen_ia",         # AI chart summary: our requests, interviews, clinical history, labs
    "entrevista",         # pre-consult interview (answers, triage, red flags, health profile)
    "consulta",           # the doctor confirms the consultation (diagnoses, meds, notes)
    "orden",              # the referral image/PDF of a booking request
    "caso",               # the whole «Contanos tu caso» chat of a booking request
    "mis_pacientes",      # a doctor's own patient roster (last interview's triage/summary)
    "alertas",            # the red-flag alerts list (triage ROJO)
    "cola",               # the day queue (triage, interview summary, referral reading)
    "mis_turnos",         # a doctor's own upcoming appointments (triage)
    "solicitudes",        # the requests queue (the AI reading of each referral)
    "agenda",             # a doctor's agenda as the schedule editor sees it
    "agenda_impresa",     # one doctor's day printed from the panel («Turnos programados»)
    "busqueda",           # a doctor found a patient who is not theirs by exact DNI
    "conversaciones",     # the admin «Chats» list showing a pre-consult interview's opening
    "exportacion",        # the copy of ALL a person's data handed to them (Ley 25.326 art. 14)
    "contrasena",         # the desk gave the patient a new temporary password (TASK-50)
    "edicion",            # the desk edited the patient's file (name, phone, email…)
    "reclamo",            # the desk resolved a document-number claim (approve / reject, TASK-68)
    "descarga",           # a small list downloaded as a file (target = the screen path)
)


class AccessDenied(Exception):
    """A doctor asked for a patient who is not theirs (404, recorded as denied)."""


class AccessLogUnavailable(db.DbUnavailable):
    """The access row could not be written because the database does not answer
    (`db.UNAVAILABLE_ERRORS`), so the read does not happen (503). A DbUnavailable
    on purpose: every route already answers that with a 503."""


class AccessLogFailed(Exception):
    """The access row could not be written for another reason (a bug, a refused
    row): the read does not happen either, but as the server's own error, not as
    a database that is down (the product owner's hub review, 2026-11-10). The message is the
    exception type alone."""


def _refusal(exc):
    """Why the access row was not written: the database does not answer
    (503 and `db_unavailable`), or the server's own failure."""
    if isinstance(exc, db.UNAVAILABLE_ERRORS):
        return AccessLogUnavailable(type(exc).__name__)
    return AccessLogFailed(type(exc).__name__)


def _key():
    raw = (config.get("TURNOS_ACCESS_LOG_KEY") or config.get("TURNOS_ADMIN_SECRET")
           or "human-rounds-access-log")
    return hashlib.sha256(("st-access-log$" + str(raw)).encode("utf-8")).digest()


def clean_dni(dni):
    return _ids.dni(dni)


def dni_hash(dni):
    """HMAC of the clinical DNI (digits only). Empty for an empty DNI."""
    digits = clean_dni(dni)
    if not digits:
        return ""
    return hmac.new(_key(), digits.encode("ascii"), hashlib.sha256).hexdigest()


def _actor(identity):
    identity = identity or {}
    return str(identity.get("usuario") or ""), str(identity.get("rol") or "")


def is_doctor(identity):
    return (identity or {}).get("rol") == "medico"


def doctor_id(identity):
    """The catalog doctor id linked to this staff account, or ""."""
    try:
        return str(db_operators.operator_doctor_id((identity or {}).get("usuario")) or "")
    except Exception:  # noqa: BLE001
        return ""


def scoped(identity, clinical=True):
    """Does this session only reach its own doctor's patients?

    `clinical` = a screen of the doctor's work (`atencion_medica`: AI summary,
    interview, consultation, external history, red alerts, own roster). There
    only the administrator reads any patient; every other role — the medico
    preset and any custom role granted `atencion_medica` — is held to the
    doctor its account is linked to. On the desk's screens (requests, day
    queue, referral files) the desk works by role, and only a medico is
    held to their own (`clinical=False`). Holding `todos_los_pacientes`
    lifts the hold on both."""
    rol = (identity or {}).get("rol")
    if rol == "admin":
        return False
    held = True if clinical else rol == "medico"
    return held and not sees_all_patients(identity)


ALL_PATIENTS = "todos_los_pacientes"


def sees_all_patients(identity):
    """Does this account hold `todos_los_pacientes` right now? Read from the
    same store as the central gate (role preset + per-user grants), so taking
    it away applies on the next request. Any doubt — no store, unknown or
    disabled role — is a no: the session stays held to its own doctor."""
    identity = identity or {}
    try:
        from app.core import permissions
        acceso = permissions.access_for(identity.get("usuario"), identity.get("rol"))
    except Exception:  # noqa: BLE001
        return False
    return acceso.get("state") == "ok" and ALL_PATIENTS in acceso.get("permissions", ())


def may_see(identity, dni, clinical=True):
    """True when this session may read this patient's clinical data. A scoped
    session needs the patient to be its doctor's; an account with no linked
    doctor then sees nobody."""
    if not scoped(identity, clinical):
        return True
    mid = doctor_id(identity)
    digits = clean_dni(dni)
    if not mid or not digits:
        return False
    return bool(db_clinical.doctor_has_patient(mid, digits))


def record(identity, action, dni, target_id=None, allowed=True):
    """Write one access row. Raises AccessLogUnavailable when the database does
    not answer, AccessLogFailed when the row could not be written otherwise."""
    if action not in ACTIONS:
        raise ValueError("unknown access-log action: %r" % (action,))
    usuario, rol = _actor(identity)
    # A QR interview may have no DNI yet: the read is still recorded (by
    # actor and target id), with an empty patient hash.
    hashed = dni_hash(dni)
    try:
        db_clinical.insert_clinical_access(usuario or "?", rol, action, hashed,
                                  target_id=target_id, allowed=allowed)
    except Exception as e:  # noqa: BLE001
        raise _refusal(e) from None


_seen = set()
_seen_day = [None]
_seen_lock = threading.Lock()
_SEEN_MAX = 50000


def record_many(identity, action, items):
    """One row per patient a LIST showed (red alerts, a doctor's roster, the
    day queue, the requests queue): `items` = [(dni, target_id), ...]. Lists
    refresh themselves all day, so the same (who, what, patient, target) is
    written once per day (installation date) per process — the question the
    log answers is «who saw this patient's data, which day»; ten years of
    every refresh would be hundreds of millions of rows. A single open
    (`record`) always writes. Raises AccessLogUnavailable or AccessLogFailed
    like `record`."""
    if action not in ACTIONS:
        raise ValueError("unknown access-log action: %r" % (action,))
    usuario, rol = _actor(identity)
    hoy = datetime.now(tz.tzinfo()).date()
    rows = []
    with _seen_lock:
        if _seen_day[0] != hoy or len(_seen) > _SEEN_MAX:
            _seen.clear()
            _seen_day[0] = hoy
        for dni, target_id in items:
            hashed = dni_hash(dni)
            key = (usuario, rol, action, hashed, None if target_id is None else str(target_id))
            if key in _seen:
                continue
            rows.append(key)
    if not rows:
        return
    # Marked as seen only AFTER the insert commits: a concurrent request of
    # the same list must not skip its rows while this insert may still fail.
    # Two concurrent ones may both write — a duplicate, never a gap.
    try:
        db_clinical.insert_clinical_access_many(usuario or "?", rol, action,
                                       [(k[3], k[4]) for k in rows])
    except Exception as e:  # noqa: BLE001
        raise _refusal(e) from None
    with _seen_lock:
        if _seen_day[0] == hoy:
            _seen.update(rows)


def check(identity, action, dni, target_id=None, clinical=True):
    """The one call a clinical read makes before it answers: decides, records
    the decision, and raises AccessDenied (recorded as denied) when a scoped
    session reaches for someone else's patient."""
    allowed = may_see(identity, dni, clinical=clinical)
    record(identity, action, dni, target_id=target_id, allowed=allowed)
    if not allowed:
        raise AccessDenied(action)


def error_body(exc):
    """(status, body) for the two refusals, the same on every route. Someone
    else's patient is a 404, not a 403 — if you can't see it, it doesn't exist
    for you (docs/architecture/admin-cola-y-agenda.md); the access row still
    records the attempt as denied."""
    if isinstance(exc, AccessDenied):
        return 404, {"error": "paciente no encontrado"}
    from app.core import deps
    return deps.answer_db_unavailable(lambda status: (status, {"error": "registro_de_accesos_no_disponible"}))


def history(dni, limit=200):
    """Who opened this patient's data, newest first — the admin's screen."""
    hashed = dni_hash(dni)
    if not hashed:
        return []
    return db_clinical.list_clinical_access(hashed, limit=limit)


def retention_days():
    """`TURNOS_HOUSEKEEPING_ACCESS_LOG_DAYS`, default ten years, never under one
    year (the table's own trigger refuses to delete younger rows anyway)."""
    try:
        days = int(config.get("TURNOS_HOUSEKEEPING_ACCESS_LOG_DAYS") or 3650)
    except (TypeError, ValueError):
        days = 3650
    return max(365, days)
