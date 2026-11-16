"""core/db_slots.py — the slot, day-queue and counter statements of the persistence layer.

The soft-lock holds on a slot (`hold_*`, `holds_*`: one statement-level race between two tokens, fail
CLOSED when the database is down), the patient's request for an exact slot, the durable day-queue events
behind the SSE stream, the waitlist lookups, the booking-limit counter (`count_active_requests`),
the «can this service, center or doctor be deleted» counters, the account's dependents, the day queue read (`list_approved_day`, `approved_calendar_groups`,
`count_appointments_on_dates`), the chat retention purge and the interviews of one patient. Split out of
`core/db.py` in stage 6 of the SQLAlchemy Core move (docs/architecture/sqlalchemy-core.md): the statements
are Core, built from `core/schema_model.py`. The request lifecycle (create, approve, reject, cancel, the
event log) stays in `core/db.py` / `core/db_booking.py`.

Callers write `db_slots.hold_acquire(...)`: `core/db.py` re-exports nothing of this module (TASK-57,
`tests/test_db_domain_modules.py`). The functions reach the connection as `db._connect()` / `db.DbUnavailable`, the
layers below as `db_wire._wire` / `db_values._ART_SQL` and their own names bare (`slot_key`), all at CALL time, so a
test that patches the owner patches what every caller uses. `slot_key`, `DOCTOR_ID_COLUMNS` and
`create_appointment_offer` (the waitlist's offer insert) came over from `core/db.py` with this stage.

Every statement whose structure never changes is built ONCE, at import (`sqlcore.prepare`): these include the
reads and writes that run on every panel or patient-site request (the day queue, the slot holds, the
booking limit, the «Mis turnos» probe of `/api/account/me`). A prepared statement keeps the old meaning of
a `None` parameter — `col = NULL`, which finds nothing — so the `IS NULL` trap of `col == None` cannot
arise there. Only the nightly chat purge and `interviews_by_dni` (a non-empty text, a NOT NULL comparison)
are built per call with `sqlcore.run`; a nullable comparison with a value that can be `None` must never be
written there with `==` (bind it: `col == sa.literal(value, col.type)`).
"""

import re
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.core import db
from app.core import db_chat
from app.core import db_wire
from app.core import db_values
from app.core import schema_model as sm
from app.core import sqlcore


_BR = sm.booking_requests
_HOLDS = sm.slot_holds
_WAIT = sm.waitlist
_DEP = sm.dependents
_USERS = sm.users


def _p(name, column):
    """A named parameter typed like `column`, for the statements built once."""
    return sa.bindparam(name, type_=column.type)


def _lit(text):
    """A SQL string constant, as the hand-written statements had it (`status='aprobada'`): it stays a
    literal so the planner sees it (the indexes on the status) and the text does not change."""
    return sa.literal_column("'%s'" % text)


def _states(*names):
    return [_lit(n) for n in names]


def _to_char(column, fmt):
    return sa.func.to_char(column, _lit(fmt))


def _at_art(column):
    """`<column> AT TIME ZONE '<installation tz>'` with the zone inlined, as it always was."""
    return sqlcore.at_zone(column, db_values._ART_SQL)


# ---- durable day-queue events (the SSE cursor) --------------------------------------------------

_EVENTS = sm.day_queue_events

_EVENT_HIGH_WATER = sqlcore.prepare(sa.select(
    sa.func.coalesce(sa.func.max(_EVENTS.c.id), sa.literal_column("0")).label("id")))

_EVENTS_AFTER = sqlcore.prepare(
    sa.select(_EVENTS.c.id, _EVENTS.c.appointment_day, _EVENTS.c.center_slug,
              _EVENTS.c.assigned_doctor_id, _EVENTS.c.assigned_doctor)
    .where(_EVENTS.c.id > _p("after", _EVENTS.c.id))
    .order_by(_EVENTS.c.id.asc())
    .limit(sa.bindparam("max_rows", type_=sa.Integer)))


def day_queue_event_high_water():
    """Latest durable queue invalidation id, or zero on a new installation."""
    with db._connect() as conn, conn.cursor() as cur:
        return int(_EVENT_HIGH_WATER.run(cur).fetchone()["id"])


def day_queue_events_after(event_id, limit=128):
    """Durable, non-PHI queue invalidations after an SSE cursor."""
    after = max(0, int(event_id or 0))
    bounded_limit = max(1, min(int(limit or 128), 512))
    with db._connect() as conn, conn.cursor() as cur:
        return list(_EVENTS_AFTER.run(cur, after=after, max_rows=bounded_limit).fetchall())


# ---- lista de espera (waitlist.py) --------------------------------------------------------------
#
# El motor de decisión vive en waitlist.py; acá sólo los dos lookups que
# necesita el wiring (solicitudes.py al cancel/optar, appointment_offer.py al
# resolver una oferta aceptada).

_WAITLIST_GET = sqlcore.prepare(sa.select(_WAIT).where(_WAIT.c.id == _p("id_", _WAIT.c.id)))

_WAITLIST_ACTIVE = sqlcore.prepare(
    sa.select(sa.literal_column("1")).select_from(_WAIT)
    .where(_WAIT.c.request_id == _p("solicitud_id", _WAIT.c.request_id), _WAIT.c.active))


def waitlist_get(id_):
    """La fila de `lista_espera` por su id (no por solicitud_id) — usa esto
    appointment_offer.py para resolver a qué solicitud pertenece una oferta ya
    aceptada (ofertas_turno.lista_espera_id)."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(_WAITLIST_GET.run(cur, id_=id_).fetchone(), "waitlist")


def waitlist_active(solicitud_id):
    """True si esa solicitud tiene un pedido de lista de espera activo hoy
    (`opt_in`/`opt_out` en waitlist.py) — para que el cliente muestre el
    checkbox marcado o no."""
    with db._connect() as conn, conn.cursor() as cur:
        return _WAITLIST_ACTIVE.run(cur, solicitud_id=solicitud_id).fetchone() is not None


# ---- holds de turno (soft-lock, slot_holds.py) --------------------------------------------------
#
# Mismo contrato "nunca romper el sitio" que el resto de db.py, con UNA
# excepción explícita: `hold_acquire` falla CERRADO (False, "no lo conseguí")
# si la base no está disponible (revertido 2026-08-05 — degradaba a True,
# éxito silencioso sin protección; ver su docstring). El resto
# (liberar/consultar) sigue degradando a no-op / lista vacía: ahí no hay
# nada que proteger, sólo lectura/limpieza best-effort.

def _hold_statements():
    h = _HOLDS
    # (a parameter never carries the name of a column the statement sets: SQLAlchemy reserves those)
    service, day, time_, token = (_p("servicio_id", h.c.service_id), _p("dia", h.c.day),
                                  _p("hora", h.c.time), _p("tok", h.c.token))
    # the move: the token's OTHER loose holds go (a client holds one slot at a time)
    move = sa.delete(h).where(h.c.token == token, h.c.request_id.is_(None),
                              sa.not_(sa.and_(h.c.service_id == service, h.c.day == day, h.c.time == time_)))
    # the opportunistic sweep of expired loose holds of ANY token
    sweep = sa.delete(h).where(h.c.request_id.is_(None), h.c.expires < sa.func.now())
    # one statement decides the race: the row comes back only for the token that owns the slot now
    take = _pg.insert(h).values(
        service_id=service, day=day, time=time_, token=token,
        expires=sa.func.now() + sa.bindparam("ttl_min", type_=sa.Integer)
        * sa.literal_column("interval '1 minute'", sa.Interval))
    take = take.on_conflict_do_update(
        index_elements=[h.c.service_id, h.c.day, h.c.time],
        # A hold linked to a pending request keeps its expiry (the
        # appointment's own time) even when its token takes it again — a
        # repeated submit of the same booking, or the account's token, which
        # spans every device and request of the patient. Only a loose hold is
        # refreshed to the new TTL.
        set_={"token": take.excluded.token,
              "expires": sa.case((sa.or_(h.c.request_id.is_(None), h.c.expires < sa.func.now()),
                                  take.excluded.expires),
                                 else_=h.c.expires)},
        where=sa.or_(h.c.expires < sa.func.now(), h.c.token == take.excluded.token),
    ).returning(h.c.id)
    release = sa.delete(h).where(h.c.token == _p("tok", h.c.token), h.c.request_id.is_(None))
    active = sa.select(h.c.service_id, _to_char(h.c.day, "YYYY-MM-DD").label("day"),
                       _to_char(h.c.time, "HH24:MI").label("time"), h.c.token,
                       h.c.request_id).where(h.c.expires > sa.func.now())
    link = (sa.update(h).where(h.c.token == _p("tok", h.c.token), h.c.request_id.is_(None))
            .values(request_id=sa.bindparam("sid", type_=h.c.request_id.type),
                    expires=sa.bindparam("expira_dt", type_=h.c.expires.type)))
    release_request = sa.delete(h).where(h.c.request_id == _p("sid", h.c.request_id))
    of_request = (sa.select(_to_char(h.c.day, "YYYY-MM-DD").label("day"), _to_char(h.c.time, "HH24:MI").label("time"))
                  .where(h.c.request_id == _p("sid", h.c.request_id)).limit(sa.literal_column("1")))
    return tuple(sqlcore.prepare(s) for s in (move, sweep, take, release, active, link, release_request, of_request))


(_HOLD_MOVE, _HOLD_SWEEP, _HOLD_TAKE, _HOLD_RELEASE, _HOLDS_ACTIVE, _HOLD_LINK,
 _HOLD_RELEASE_REQUEST, _HOLD_OF_REQUEST) = _hold_statements()


def hold_acquire(servicio_id, dia, hora, token, ttl_min=15):
    """Intenta tomar (o renovar) el slot (servicio_id, dia, hora) para `token`.

    Race-free en una sola sentencia: INSERT ... ON CONFLICT (servicio_id, dia,
    hora) DO UPDATE ... WHERE expira < now() OR token = EXCLUDED.token
    RETURNING id — fila devuelta = lo consiguió (estaba libre, vencido, o ya
    era suyo); sin fila = lo tiene OTRO token vigente.

    Semántica "un cliente, un slot" (move semantics): antes de intentar,
    libera los OTROS holds sueltos (sin solicitud_id) de ESE mismo token —
    así elegir un horario nuevo libera el anterior solo. De paso barre holds
    sueltos vencidos de CUALQUIER token (housekeeping oportunista, barato
    porque ya está en la misma transacción).

    Fail CLOSED (responsable del producto/plan 2026-08-05, revierte el comportamiento anterior):
    degrada a False (NO lo consiguió) si la base no está disponible. Si la
    base está caída la reserva no se puede persistir de todos modos — "fallar
    abierto" acá sólo apagaba en silencio la protección contra doble reserva
    justo cuando más hace falta (una caída de DB es exactamente el momento en
    que la carrera entre dos pacientes por el mismo horario es más probable,
    no menos). El caller (`slot_holds.try_take` / `booking_requests._create`)
    debe tratar False como "no lo pude tomar, reintentá" — nunca como
    "seguí igual sin protección"."""
    key = slot_key(dia, hora)
    if key is None:
        return False   # un día/hora que no existen nunca toman un slot
    dia, hora = key
    try:
        with db._connect() as conn, conn.cursor() as cur:
            _HOLD_MOVE.run(cur, servicio_id=servicio_id, dia=dia, hora=hora, tok=token)
            _HOLD_SWEEP.run(cur)
            return _HOLD_TAKE.run(cur, servicio_id=servicio_id, dia=dia, hora=hora, tok=token,
                                  ttl_min=ttl_min).fetchone() is not None
    except db.DbUnavailable:
        return False


def hold_release(token):
    """Libera los holds sueltos (sin solicitud vinculada) de `token`. No-op si
    la base no está disponible o el token no tenía nada tomado."""
    try:
        with db._connect() as conn, conn.cursor() as cur:
            _HOLD_RELEASE.run(cur, tok=token)
    except db.DbUnavailable:
        pass


def holds_active():
    """[{servicio_id, dia, hora, token}] de los holds VIGENTES (expira > now()),
    vinculados o no a una solicitud. `slot_holds.taken()` los combina con las
    solicitudes aprobadas futuras. [] si la base no está disponible."""
    try:
        with db._connect() as conn, conn.cursor() as cur:
            return db_wire._wire_all(_HOLDS_ACTIVE.run(cur).fetchall(), "slot_holds")
    except db.DbUnavailable:
        return []


def hold_link_booking_request(token, sid, expira_dt):
    """Liga el hold suelto de `token` a la solicitud `sid` recién creada y
    extiende `expira` a la fecha/hora del propio turno (un slot ya pasado no
    bloquea nada — `expira_dt` en el pasado hace que `holds_active()` lo deje
    de contar de inmediato). No-op si la base no está disponible: la
    solicitud YA se creó, un hold que no se pudo vincular no debe romper nada."""
    try:
        with db._connect() as conn, conn.cursor() as cur:
            _HOLD_LINK.run(cur, sid=sid, expira_dt=expira_dt, tok=token)
    except db.DbUnavailable:
        pass


def holds_release_booking_request(sid):
    """Borra los holds ligados a la solicitud `sid` — se llama tanto al
    approve (el `turno_dt` de la solicitud toma la posta como bloqueo) como al
    reject (el slot vuelve a estar libre) y a toda cancelación, del paciente o
    de la mesa. Nunca levanta: approve/reject/cancel YA persistieron, esto es
    limpieza best-effort."""
    try:
        with db._connect() as conn, conn.cursor() as cur:
            _HOLD_RELEASE_REQUEST.run(cur, sid=sid)
    except Exception:  # noqa: BLE001 — nunca debe romper approve/reject
        pass


def hold_of_booking_request(sid):
    """El hold vinculado a una solicitud PENDIENTE (turno ya elegido en el
    wizard, todavía sin approve) — {'dia','hora'} o None si no eligió horario
    (p.ej. una franja como "A la mañana" nunca crea hold). No filtra por
    `expira`: la vigencia (pasado/futuro) la decide quien llama, mismo
    criterio que ya se aplica a `turno_dt` de una aprobada (calendar_feed.py).
    Usado por el .ics de un turno pendiente y por el feed de calendario."""
    try:
        with db._connect() as conn, conn.cursor() as cur:
            return db_wire._wire(_HOLD_OF_REQUEST.run(cur, sid=sid).fetchone(), "slot_holds")
    except db.DbUnavailable:
        return None


def _active_by_slot_statement():
    s, h = _BR, _HOLDS
    wall = _at_art(s.c.appointment_at)
    return sqlcore.prepare(
        sa.select(s.c.id, s.c.status.label("estado"))
        .select_from(s.outerjoin(h, h.c.request_id == s.c.id))
        .where(
            s.c.dni == _p("dni", s.c.dni), s.c.service_id == _p("servicio_id", s.c.service_id),
            s.c.status.in_(_states("pendiente", "aprobada")),
            sa.or_(
                sa.and_(h.c.day == _p("hold_day", h.c.day), h.c.time == _p("hold_time", h.c.time)),
                sa.and_(s.c.appointment_at.is_not(None),
                        _to_char(wall, "YYYY-MM-DD") == sa.bindparam("dia", type_=sa.Text),
                        _to_char(wall, "HH24:MI") == sa.bindparam("hora", type_=sa.Text))))
        .order_by(s.c.created.desc())
        .limit(sa.literal_column("1")))


_ACTIVE_BY_SLOT = _active_by_slot_statement()


def active_booking_request_by_slot(dni, servicio_id, dia, hora):
    """Return this patient's active request for an exact slot, if it exists.

    Chat requests can finish durably and still lose their HTTP response when
    the local process restarts.  The natural booking key (patient + service +
    slot) lets the next attempt recover that request instead of creating a
    duplicate or reporting the slot as taken.
    """
    key = slot_key(dia, hora) or (None, None)
    try:
        with db._connect() as conn, conn.cursor() as cur:
            # `hold_day`/`hold_time` are None for an impossible slot: `h.day = NULL` matches nothing
            # (the prepared statement keeps it so; `IS NULL` would match every request without a hold)
            return _ACTIVE_BY_SLOT.run(cur, dni=dni, servicio_id=servicio_id, hold_day=key[0],
                                       hold_time=key[1], dia=dia, hora=hora).fetchone()
    except db.DbUnavailable:
        return None


# ---- the patient's live requests and the counters -------------------------------------------------

_LIVE_REQUESTS = sqlcore.prepare(
    sa.select(_BR).where(
        _BR.c.dni == _p("dni", _BR.c.dni), _BR.c.status.in_(_states("pendiente", "aprobada")),
        sa.or_(_BR.c.appointment_at > sa.func.now(),
               sa.and_(_BR.c.status == _lit("pendiente"), _BR.c.appointment_at.is_(None))))
    .order_by(_BR.c.appointment_at.asc().nulls_last(), _BR.c.id))


def live_requests_of(dni):
    """Solicitudes vivas de este paciente como paciente (no como quien pidió
    por un dependiente): pendientes, o aprobadas con el turno por venir."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(_LIVE_REQUESTS.run(cur, dni=dni).fetchall(), "booking_requests")


def _count_requests(*conditions):
    return sa.select(sa.func.count().label("n")).select_from(_BR).where(*conditions)


def _doctor_request_match(named):
    """Una solicitud nombra a un médico por el id asignado, por el id de la agenda
    en la que el paciente reservó (`slot_doctor_id`) o, en filas viejas, sólo
    por el nombre (y sólo si no tiene id: 2026-10-23). `named`: el nombre cuenta
    (parámetro `nombre`); sin nombre la tercera rama no existe (`%s <> ''` era falso)."""
    doctor = sa.bindparam("doc_id", type_=_BR.c.assigned_doctor_id.type)
    clauses = [_BR.c.assigned_doctor_id == doctor, _BR.c.slot_doctor_id == doctor]
    if named:
        clauses.append(sa.and_(
            _BR.c.assigned_doctor_id.is_(None),
            sa.func.lower(sa.func.btrim(_BR.c.assigned_doctor))
            == sa.func.lower(sa.func.btrim(sa.bindparam("nombre", type_=sa.Text)))))
    return sa.or_(*clauses)


def _doctor_upcoming(named):
    return _count_requests(
        _doctor_request_match(named), _BR.c.appointment_at > sa.func.now(),
        sa.or_(_BR.c.status == _lit("aprobada"),
               sa.and_(_BR.c.status == _lit("pendiente"),
                       _BR.c.slot_doctor_id == sa.bindparam("doc_id", type_=_BR.c.slot_doctor_id.type))))


# Toda otra tabla que guarda el id de un médico (2026-10-23, problems.md №6):
# con una sola fila que lo nombre, el médico se deshabilita y nunca se borra
# de la base — si no, esa fila apunta a un id que ya no existe. Una columna
# nueva con el id de un médico va acá (test_doctor_delete_references).
DOCTOR_ID_COLUMNS = (
    ("interviews", "doctor_id"),
    ("consultations", "doctor_id"),
    ("patient_summaries", "doctor_id"),
    ("day_queue_events", "assigned_doctor_id"),
    ("operators", "doctor_id"),
)


def _doctor_references():
    """`SELECT COALESCE(SUM(n), 0) FROM (SELECT count(*) AS n FROM <table> WHERE <col> = :id UNION ALL ...) refs`
    over `db_slots.DOCTOR_ID_COLUMNS` (a new column holding a doctor id goes there, not here)."""
    doctor = sa.bindparam("id_", type_=sa.Text)
    counts = []
    for table, col in DOCTOR_ID_COLUMNS:
        t = sm.metadata.tables[table]
        counts.append(sa.select(sa.func.count().label("n")).select_from(t).where(t.c[col] == doctor))
    refs = sa.union_all(*counts).subquery("refs")
    return sa.select(sa.func.coalesce(sa.func.sum(refs.c.n), sa.literal_column("0")).label("n"))


def _on_dates_statement():
    day = db_values._local_date(_BR.c.appointment_at)
    wanted = sa.cast(sa.bindparam("dates", type_=_pg.ARRAY(sa.Text)), _pg.ARRAY(sa.Date))
    return (sa.select(day.label("dia"), sa.func.count().label("n"))
            .where(_BR.c.status.in_(_states("pendiente", "aprobada")), _BR.c.appointment_at.is_not(None),
                   day == sa.any_(wanted))
            .group_by(sa.literal_column("1")))


# Built once (see the module docstring); `count_appointments_of` picks one by kind.
_COUNT_OF = {
    "servicios": sqlcore.prepare(_count_requests(_BR.c.service_id == _p("id_", _BR.c.service_id))),
    "centros": sqlcore.prepare(_count_requests(_BR.c.center_slug == _p("id_", _BR.c.center_slug))),
    ("medicos", False): sqlcore.prepare(_count_requests(_doctor_request_match(False))),
    ("medicos", True): sqlcore.prepare(_count_requests(_doctor_request_match(True))),
}
_DOCTOR_UPCOMING = {named: sqlcore.prepare(_doctor_upcoming(named)) for named in (False, True)}
_DOCTOR_REFERENCES = sqlcore.prepare(_doctor_references())
_ON_DATES = sqlcore.prepare(_on_dates_statement())


def count_appointments_of(tipo, id_, nombre=None):
    """Cuántas solicitudes (de cualquier estado) referencian a este servicio,
    centro o médico.

    Es la pregunta que decide si un registro se puede borrar o sólo
    deshabilitar (reunión hospital 2026-07-24: "ningún registro con turnos
    históricos puede eliminarse"). Un médico se referencia por id, pero las
    filas viejas guardaron sólo el nombre (medico_asignado existía antes que
    medico_asignado_id): se cuentan las dos formas, si no un profesional con
    historial previo a esa columna parecería borrable. El nombre cuenta SÓLO
    para filas sin id (2026-10-23): una fila con el id de otro médico es de
    ese otro, diga lo que diga su nombre guardado. También cuenta el médico
    del horario que reservó el paciente (`slot_doctor_id`)."""
    if tipo == "medicos":
        nombre = nombre or ""
        statement = _COUNT_OF[("medicos", nombre != "")]
        values = {"doc_id": id_, "nombre": nombre} if nombre != "" else {"doc_id": id_}
    elif tipo in ("servicios", "centros"):
        statement, values = _COUNT_OF[tipo], {"id_": id_}
    else:
        return 0
    with db._connect() as conn, conn.cursor() as cur:
        row = statement.run(cur, **values).fetchone()
        return int((row or {}).get("n") or 0)


def doctor_other_references(id_):
    """Cuántas filas fuera de `booking_requests` nombran a este médico por id
    (`db_slots.DOCTOR_ID_COLUMNS`)."""
    with db._connect() as conn, conn.cursor() as cur:
        return int((_DOCTOR_REFERENCES.run(cur, id_=id_).fetchone() or {}).get("n") or 0)


def doctor_upcoming_appointments(id_, nombre=None):
    """Turnos con horario por venir con este médico: aprobados, o pendientes
    que reservaron un lugar en SU agenda (`slot_doctor_id`). Con alguno, el
    médico no se deshabilita — la mesa los reasigna o cancela antes
    (2026-10-23). Una solicitud pendiente que sólo lo tiene ASIGNADO no
    cuenta: deshabilitar la devuelve a «sin asignar» (`unassign_pending_of_doctor`,
    The product owner 2026-10-23)."""
    nombre = nombre or ""
    values = {"doc_id": id_, "nombre": nombre} if nombre != "" else {"doc_id": id_}
    with db._connect() as conn, conn.cursor() as cur:
        row = _DOCTOR_UPCOMING[nombre != ""].run(cur, **values).fetchone()
        return int((row or {}).get("n") or 0)


def count_appointments_on_dates(dates):
    """{iso_date: n} of pending or approved requests whose appointment falls
    on each ART date of `dates` (site holidays, site_holidays.py: the desk
    reschedules them, nothing is cancelled automatically). Dates without
    any appointment are omitted."""
    if not dates:
        return {}
    with db._connect() as conn, conn.cursor() as cur:
        return {row["dia"].isoformat(): int(row["n"]) for row in _ON_DATES.run(cur, dates=list(dates)).fetchall()}


def _active_requests_count():
    return sqlcore.prepare(_count_requests(
        _BR.c.dni == _p("dni", _BR.c.dni), _BR.c.service_id == _p("servicio_id", _BR.c.service_id),
        _BR.c.status == sa.any_(sa.bindparam("estados", type_=_pg.ARRAY(sa.Text))),
        sa.or_(_BR.c.status != _lit("aprobada"), _BR.c.appointment_at.is_(None),
               _BR.c.appointment_at >= sa.func.now())))


_COUNT_ACTIVE = _active_requests_count()


def _count_active_requests_cur(cur, dni, servicio_id, estados):
    """Igual que `count_active_requests`, pero contra un cursor YA ABIERTO —
    factoreado así para que `create_booking_request_with_limit` pueda contar
    dentro de la MISMA transacción que el advisory lock y el insert (contar
    en una conexión aparte, como hacía `booking_limits.check` antes, es
    exactamente la carrera que ese guard cierra)."""
    row = _COUNT_ACTIVE.run(cur, dni=dni, servicio_id=servicio_id, estados=list(estados)).fetchone()
    return int((row or {}).get("n") or 0)


def count_active_requests(dni, servicio_id, estados=("pendiente", "aprobada")):
    """Cuántas solicitudes de ESE paciente en ESE servicio están ocupando un
    lugar ahora mismo: pendientes, más aprobadas cuyo turno todavía no pasó.

    Es el contador del límite de turnos activos (booking_limits.py). Cuenta
    por `dni` clínico, no por solicitante: los turnos de un hijo son del hijo,
    aunque los saque el padre — si no, un padre con tres hijos en kinesiología
    chocaría contra el límite de uno solo de ellos. Las rechazadas, las
    canceladas y las ya atendidas NO cuentan: el límite es sobre la agenda
    ocupada, no sobre el historial.

    Fuera de una transacción propia (una conexión, lectura suelta) — para
    contar y crear ATÓMICAMENTE bajo el mismo lock, usar
    `create_booking_request_with_limit`."""
    estados = tuple(estados or ())
    if not estados:
        return 0
    with db._connect() as conn, conn.cursor() as cur:
        return _count_active_requests_cur(cur, dni, servicio_id, estados)


# ---- personas a cargo ------------------------------------------------------------------------------

_DEP_COLUMNS = (_DEP.c.id, _DEP.c.dni, _DEP.c.first_name, _DEP.c.last_name, _DEP.c.birth_date, _DEP.c.sex,
                _DEP.c.relationship, _DEP.c.affidavit_at, _DEP.c.created, _DEP.c.updated)

_LIST_DEPENDENTS = sqlcore.prepare(
    sa.select(*_DEP_COLUMNS).where(_DEP.c.adult_dni == _p("adulto_dni", _DEP.c.adult_dni))
    .order_by(_DEP.c.birth_date.desc(), _DEP.c.last_name, _DEP.c.first_name))

_GET_DEPENDENT = sqlcore.prepare(
    sa.select(*_DEP_COLUMNS).where(_DEP.c.adult_dni == _p("adulto_dni", _DEP.c.adult_dni),
                                   _DEP.c.dni == _p("dni", _DEP.c.dni)))

_ADULTS_OF_DEPENDENT = sqlcore.prepare(
    sa.select(_DEP.c.adult_dni.label("dni"), _USERS.c.first_name.label("nombre"),
              _USERS.c.last_name.label("apellido"), _DEP.c.relationship.label("vinculo"))
    .select_from(_DEP.outerjoin(_USERS, _USERS.c.dni == _DEP.c.adult_dni))
    .where(_DEP.c.dni == _p("dni", _DEP.c.dni))
    .order_by(_USERS.c.last_name.asc().nulls_last(), _USERS.c.first_name, _DEP.c.adult_dni))

_DELETE_DEPENDENT = sqlcore.prepare(
    sa.delete(_DEP).where(_DEP.c.adult_dni == _p("adulto_dni", _DEP.c.adult_dni),
                          _DEP.c.dni == _p("dni", _DEP.c.dni)).returning(_DEP.c.id))


def _save_dependent_statement():
    insert = _pg.insert(_DEP).values(
        adult_dni=_p("adulto_dni", _DEP.c.adult_dni), dni=_p("dni_", _DEP.c.dni),
        first_name=_p("nombre", _DEP.c.first_name), last_name=_p("apellido", _DEP.c.last_name),
        birth_date=_p("nacimiento", _DEP.c.birth_date), sex=_p("sexo", _DEP.c.sex),
        relationship=_p("vinculo", _DEP.c.relationship))
    return sqlcore.prepare(insert.on_conflict_do_update(
        index_elements=[_DEP.c.adult_dni, _DEP.c.dni],
        set_={"first_name": insert.excluded.first_name, "last_name": insert.excluded.last_name,
              "birth_date": insert.excluded.birth_date, "sex": insert.excluded.sex,
              "relationship": insert.excluded.relationship,
              "affidavit_at": sa.func.now(), "updated": sa.func.now()},
    ).returning(*_DEP_COLUMNS))


_SAVE_DEPENDENT = _save_dependent_statement()


def list_dependents(adulto_dni):
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(_LIST_DEPENDENTS.run(cur, adulto_dni=adulto_dni).fetchall(), "dependents")


def get_dependent(adulto_dni, dni):
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(_GET_DEPENDENT.run(cur, adulto_dni=adulto_dni, dni=dni).fetchone(), "dependents")


def adults_of_dependent(dni):
    """Los adultos que tienen a `dni` a cargo, con su nombre. Un chico puede
    estar a cargo de dos (padre y madre): son dos filas en `dependents` con
    el mismo dni y distinto adult_dni. La consola lo muestra para que sacarlo
    de uno no se lea como borrarlo del sistema (responsable del producto, 2026-07-24)."""
    with db._connect() as conn, conn.cursor() as cur:
        return _ADULTS_OF_DEPENDENT.run(cur, dni=dni).fetchall()


def save_dependent(adulto_dni, dni, nombre, apellido, nacimiento, sexo, vinculo):
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(_SAVE_DEPENDENT.run(
            cur, adulto_dni=adulto_dni, dni_=dni, nombre=nombre, apellido=apellido,
            nacimiento=db_values._coerce_date(nacimiento), sexo=sexo, vinculo=vinculo).fetchone(), "dependents")


def delete_dependent(adulto_dni, dni):
    with db._connect() as conn, conn.cursor() as cur:
        return _DELETE_DEPENDENT.run(cur, adulto_dni=adulto_dni, dni=dni).fetchone() is not None


# ---- retención del chat ----------------------------------------------------------------------------

def purge_summarized_chat_messages(days=90, limit=2000):
    """Mensajes de chat (`chat_messages`) más viejos que `days` CUYA
    conversación ya tiene un resumen/compactación guardado que la cubre.
    `chat_summaries`/`chat_compactions` están indexadas por `patient_key`
    ("dni:<dni>") -- esto sólo dispara cuando la `key` de la conversación
    ES ese patient_key (el caso común: el canal/clave propio de un paciente
    logueado). Conservador por construcción: una conversación sin fila de
    resumen que la cubra NUNCA se toca, así que nada se pierde -- sólo se
    achica lo que ya está capturado en otro lado."""
    messages, m = sm.chat_messages, sm.chat_messages.alias("m")
    summaries, compactions = sm.chat_summaries, sm.chat_compactions
    covered = sa.or_(
        sa.exists().where(summaries.c.patient_key == m.c.key, summaries.c.covers_until >= m.c.created),
        sa.exists().where(compactions.c.patient_key == m.c.key, compactions.c.covers_until >= m.c.created))
    exempt = sa.literal(list(db_chat.CHAT_RETENTION_EXEMPT_CHANNELS), _pg.ARRAY(sa.Text))
    doomed = sa.select(m.c.id).where(
        m.c.created < db_values._days_ago(int(days)), m.c.channel != sa.all_(exempt), covered).limit(int(limit))
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.delete(messages).where(messages.c.id.in_(doomed))).rowcount


# ---- entrevistas de un paciente ---------------------------------------------------------------------

def interviews_by_dni(dni):
    """Todas las entrevistas previas de UN paciente (para el resumen del
    paciente, chart_summary.py) — cualquier médico, más recientes primero."""
    dni = str(dni or "").strip()
    if not dni:
        return []
    t = sm.interviews
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(t.c.dni == dni).order_by(t.c.created.desc())).fetchall()


# ---- aprobadas: grupos del calendario y la cola del día ---------------------------------------------

def _calendar_groups_statement():
    day = db_values._local_date(_BR.c.appointment_at).label("dia")
    return sqlcore.prepare(
        sa.select(day, _BR.c.assigned_doctor.label("medico_asignado"),
                  _BR.c.assigned_doctor_id.label("medico_asignado_id"),
                  _BR.c.center_slug.label("centro_slug"), sa.func.count().label("cantidad"))
        .where(_BR.c.status == _lit("aprobada"), _BR.c.appointment_at.is_not(None))
        .group_by(*(sa.literal_column(str(i)) for i in (1, 2, 3, 4)))
        .order_by(sa.literal_column("dia")))


_CALENDAR_GROUPS = _calendar_groups_statement()


def approved_calendar_groups():
    """Date/doctor/center projection only, including past appointments for navigation."""
    with db._connect() as conn, conn.cursor() as cur:
        return [dict(row, dia=row["dia"].isoformat()) for row in _CALENDAR_GROUPS.run(cur).fetchall()]


def _approved_day_statement():
    return sqlcore.prepare(
        sa.select(_BR, _USERS.c.coverage.label("cobertura_usuario"))
        .select_from(_BR.outerjoin(_USERS, _USERS.c.dni == _BR.c.dni))
        .where(_BR.c.status == _lit("aprobada"), _BR.c.appointment_at.is_not(None),
               db_values._local_date(_BR.c.appointment_at) == sa.cast(sa.bindparam("dia_iso", type_=sa.Text), sa.Date))
        # Two patients on one minute (an authorised overbooking): the
        # ordinary appointment first, then the extra one, then by arrival —
        # never the table's physical order, which an UPDATE reshuffles.
        .order_by(_BR.c.appointment_at.asc(), _BR.c.overbooking.asc(), _BR.c.id.asc()))


_APPROVED_DAY = _approved_day_statement()


def list_approved_day(dia_iso):
    """Solicitudes aprobadas cuyo turno_dt cae en el día ART `dia_iso`
    (YYYY-MM-DD), orden ascendente por turno_dt. Fuente de la cola del día
    (doctor_appointments.py `GET /api/admin/day-queue`): a diferencia de
    `list_approved_future`, que se pierde lo pasado-de-hoy, este recorte
    trae TODO el día — pasado y futuro dentro de la misma jornada — para
    que el médico vea también a quien ya atendió. La conversión de huso pasa
    por SQL (``AT TIME ZONE``) para seguir usando el índice
    (estado, turno_dt) en vez de filtrar en Python."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(_APPROVED_DAY.run(cur, dia_iso=dia_iso).fetchall(), "booking_requests")


# ---- idempotencia ------------------------------------------------------------------------------------

_BY_IDEM_KEY = sqlcore.prepare(sa.select(_BR).where(_BR.c.idem_key == _p("idem_key", _BR.c.idem_key)))


def get_booking_request_by_idem_key(idem_key):
    """La fila de `booking_requests` para ese `idem_key`, o None si todavía
    no existe ninguna. Fast-path de idempotencia a nivel de RUTA
    (`booking_requests._create`): se consulta ANTES de `booking_limits.
    check`, que no sabe nada de `idem_key` y por eso puede rechazar con 409
    "límite_turnos_activos" un retry legítimo del propio ganador — la fila
    que el `idem_key` ya creó cuenta como "abierta" contra el límite del
    MISMO paciente (2026-08-05 tier 14 fix, finding A del plan de
    integridad; reproducible incluso SECUENCIAL, sin concurrencia, con
    límite=1: el segundo POST del mismo doble-tap veía 409 en vez de éxito
    con la misma fila)."""
    idem_key = (idem_key or "").strip() or None
    if not idem_key:
        return None
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(_BY_IDEM_KEY.run(cur, idem_key=idem_key).fetchone(), "booking_requests")


_SLOT_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


_SLOT_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def slot_key(dia, hora):
    """(date, time) de un slot como lo guarda `slot_holds` (columnas `day`
    date y `time` time), o None si no es un día/hora reales. «9:00» y «09:00»
    son la misma hora; «2024-13-45» o «25:61» no son nada y nunca llegan a la
    base."""
    dia = str(dia or "").strip()
    hora = str(hora or "").strip()
    if not (_SLOT_DAY_RE.match(dia) and _SLOT_TIME_RE.match(hora)):
        return None
    try:
        return date.fromisoformat(dia), datetime.strptime(hora, "%H:%M").time()
    except ValueError:
        return None



def create_appointment_offer(slot_at, servicio_id, centro_slug, lista_espera_id,
                              token, enviada_en, expira_en):
    """Inserta una oferta de turno liberado compitiendo contra
    `appointment_offers_live_slot_uniq` — la única oferta 'ofrecida' viva por
    (service_id, center_slug, slot_at). `waitlist._create_offer` hace hoy el
    INSERT crudo sin esta protección: dos workers/ticks que decidan ofrecer
    el MISMO slot a la vez (p.ej. `release_slot` disparado por una
    cancelación justo cuando `tick()` también estaba procesando ese slot)
    terminaban con dos filas 'ofrecida' vivas y dos mails.

    Con `ON CONFLICT ... DO NOTHING` contra ese índice: si ya hay una oferta
    viva para ese slot, esta llamada no inserta nada y devuelve None — el
    llamador lo lee como "otro worker ya ofreció este cupo" y NO manda el
    mail (evita tanto la fila duplicada como el mail duplicado). Devuelve el
    id de la fila si esta llamada ganó la carrera."""
    # SQLAlchemy Core (2026-10-25, docs/architecture/sqlalchemy-core.md): the
    # statement is the one that used to be hand-written here. The 'ofrecida'
    # literal stays a literal in both the VALUES row and the ON CONFLICT
    # predicate — Postgres matches that predicate to the partial unique index
    # by its text, a bound parameter there would not match.
    from sqlalchemy import literal_column
    from sqlalchemy.dialects.postgresql import insert
    from app.core import sqlcore
    from app.core.schema_model import appointment_offers as ao
    ofrecida = literal_column("'ofrecida'")
    with db._connect() as conn, conn.cursor() as cur:
        fila = sqlcore.run(cur, insert(ao).values(
            slot_at=db_values._coerce_dt(slot_at), service_id=servicio_id, center_slug=centro_slug,
            waitlist_id=lista_espera_id, token=token, sent_at=enviada_en,
            expires_at=expira_en, status=ofrecida,
        ).on_conflict_do_nothing(
            index_elements=[ao.c.service_id, ao.c.center_slug, ao.c.slot_at],
            index_where=ao.c.status == ofrecida,
        ).returning(ao.c.id)).fetchone()
        return fila["id"] if fila else None
