"""booking_limits.py — how many appointments one patient can hold at once.

The product owner, 2026-08-01: "create an option for the whole website, and per doctor
apart, for the maximum number of turnos one can get. For kinesiología it could
be ten, and for most specialties just one."

The rule exists because the two cases are genuinely different, not because
someone wants a knob:

  · A specialist consultation is ONE appointment. A patient sitting on three
    open Cardiología requests is almost always a mis-click or a hedge against
    a long waitlist, and every extra one is a slot nobody else can take.
  · Rehab is a SERIES. Kinesiología is ten sessions by definition, and a limit
    of one turns the normal case into an error message.

So the limit is a number, not a boolean, and it resolves most-specific-first:

    doctor override → service override → site default (MAX_ACTIVE_REQUESTS, 1)

Same shape as `intake_admin.enabled_for` (per-doctor override falling back to
a system default), on purpose — one pattern for "the hospital can set this
globally and a professional can differ".

WHAT COUNTS. Only the appointments that are actually holding a place: pending
and approved-in-the-future, for the SAME final clinical DNI (a dependent's
appointments count against the dependent, not against the adult who booked
them) in the SAME service. Rejected, cancelled and already-attended rows never
count — the limit is about occupying the agenda, not about a patient's
history. That's also what makes the rehab case work without a second knob: the
tenth session becomes available as the earlier ones are attended.

WHERE IT IS ENFORCED. `booking_requests._create` (the patient's own POST) and
nowhere else. The front desk creating an appointment from the panel is not
subject to it: someone standing at the counter with a referral in hand is the
hospital's call, and a web-form guardrail that blocks staff is a bug (same
criterion as the no-show block, `_no_show_limit`).
"""

from app.core import config

# A limit is a number of slots, not a policy engine. The clamp keeps a typo in
# the panel (or in the environment) from either bricking every booking or
# quietly disabling the rule.
MAX_LIMIT = 60
DEFAULT_LIMIT = 1

# Services whose appointments count: what "active" means, in DB terms.
ACTIVE_STATES = ("pendiente", "aprobada")


def _clamp(value):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return min(n, MAX_LIMIT)


def site_default():
    """The whole-site maximum. 1 unless the hospital raised it."""
    return _clamp(config.get("MAX_ACTIVE_REQUESTS", DEFAULT_LIMIT)) or DEFAULT_LIMIT


def own_limit(doc):
    """The number a doctor or service sets for itself, or None when it inherits."""
    if not isinstance(doc, dict):
        return None
    return _clamp(doc.get("turnos_activos_max"))


def limit_for(service_doc=None, doctor_doc=None):
    """Effective limit: the doctor's own number wins over the service's, and
    the service's over the site default. An absent/invalid override just
    falls through — a professional who never touched the field follows the
    hospital's rule."""
    return (own_limit(doctor_doc) or own_limit(service_doc) or site_default())


def _service_doc(servicio_id):
    from app.patient_web import catalog
    for s in catalog.services():
        if str(s.get("id")) == str(servicio_id):
            return s
    return None


def _service_name(servicio_id):
    """Readable even for a service the catalog no longer lists (catalog.py)."""
    try:
        from app.patient_web import catalog
        return catalog.service_name(servicio_id) or servicio_id
    except Exception:  # noqa: BLE001
        return servicio_id


def check(dni, servicio_id, medico_id=None):
    """(ok, info). `info` carries everything the patient-facing message needs:
    the limit that applied, how many are already open, and the service name —
    soluciones.py's rule is that a rejection hands over the next concrete
    step, and "you already have your 1 Cardiología appointment" only reads as
    an answer when the number is in it.

    Best-effort: if the DB can't be read the booking is NOT blocked. A
    guardrail that turns a database hiccup into "you can't book" is worse than
    the duplicate it prevents.
    """
    from app.core import db_slots
    servicio = _service_doc(servicio_id)
    from app.patient_web import catalog
    limite = limit_for(servicio, catalog.doctor_doc(medico_id))
    try:
        abiertas = db_slots.count_active_requests(dni, servicio_id, ACTIVE_STATES)
    except Exception:  # noqa: BLE001 — see docstring
        return True, {"limite": limite, "abiertas": 0}
    info = {
        "limite": limite,
        "abiertas": abiertas,
        "servicio_id": servicio_id,
        "servicio_nombre": (servicio or {}).get("nombre") or _service_name(servicio_id),
    }
    return abiertas < limite, info
