"""core/db_booking.py — the booking requests of the persistence layer: creation and the patient's own data,
the referral-file bookkeeping, the staff lists, the appointment state machine (approve, reject, cancel,
reassign, attendance) with its audit trail, the confirmation link, the reminder claims, the HSI re-push
list and the attendance / ranking history.

Split out of `core/db.py` in stage 6 of the SQLAlchemy Core move (docs/architecture/sqlalchemy-core.md):
the statements are Core, built from `core/schema_model.py`. The statements whose shape never changes are
built once at import (`sqlcore.prepare`, named `bindparam()`s): `booking_requests` is a 40-column table and
building a `RETURNING *` statement on every call costs more than the round trip. The exceptions
(`AlreadyProcessed`, `SlotConflict`, `LimitReached`, `OverbookingCapacityReached`) are defined here (they came over
from `core/db.py` in the last stage of TASK-57): callers catch `db_booking.SlotConflict`.

Callers write `db_booking.<name>(...)`: `core/db.py` re-exports nothing of this module (`tests/test_db_domain_modules.py`).
The functions reach the connection as `db._connect()`, the layers below as `db_wire._wire` / `db_values._coerce_dt`, a
sibling as `db_slots.<name>` and their own names bare, all at CALL time, so a test that patches the owner patches what
every caller uses.
"""

import secrets
import unicodedata
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.core import db
from app.core import db_slots
from app.core import db_wire
from app.core import db_values
from app.core import schema_model as sm
from app.core import sqlcore


bk = sm.booking_requests
ae = sm.appointment_events
_ROW = tuple(bk.c)
_TS = bk.c.appointment_at.type


def _lit(value):
    """A state name as the SQL literal the hand-written statements had (`status='aprobada'`): the
    partial indexes on `booking_requests` match their predicates by text, a bound parameter would not."""
    return sa.literal_column("'%s'" % value)


def _bp(name, column=None, **kw):
    """A named parameter of a prepared statement, typed like `column`. A `None` value stays `col = NULL`
    (it finds nothing), exactly what the hand-written SQL did: the structure is fixed, so the
    `IS NULL` trap of `col == None` cannot arise."""
    return sa.bindparam(name, type_=column.type if column is not None else None, **kw)


def _in(name, column):
    """`column IN (<list bound at run time>)`."""
    return column.in_(sa.bindparam(name, expanding=True, type_=column.type))


_P = sqlcore.prepare
_PENDIENTE, _APROBADA, _CANCELADA, _RECHAZADA = (_lit(s) for s in ("pendiente", "aprobada", "cancelada", "rechazada"))
_ACTIVE_STATES = [_PENDIENTE, _APROBADA]


def _xact_lock(cur, key):
    """`pg_advisory_xact_lock(hashtext(<key>)::bigint)`: the lock named by one ready-made key."""
    _XACT_LOCK.run(cur, key=key)


_XACT_LOCK = _P(sa.select(sa.func.pg_advisory_xact_lock(
    sa.cast(sa.func.hashtext(sa.bindparam("key", type_=sa.Text)), sa.BigInteger))))

_CHECKPOINT_INSERT = _pg.insert(sm.hsi_checkpoints).values(
    entity_type=sa.literal_column("'appointment'"), entity_id=_bp("entity_id", sm.hsi_checkpoints.c.entity_id),
    remote=_bp("remote", sm.hsi_checkpoints.c.remote))
_UPSERT_CHECKPOINT = _P(_CHECKPOINT_INSERT.on_conflict_do_update(
    index_elements=[sm.hsi_checkpoints.c.entity_type, sm.hsi_checkpoints.c.entity_id],
    set_={"remote": _CHECKPOINT_INSERT.excluded.remote, "observed_at": sa.func.now()}))


def _checkpoint(cur, hsi_result):
    """The HSI checkpoint of an appointment, upserted in the caller's transaction: only when the result
    carries both the checkpoint and the remote appointment id."""
    checkpoint = hsi_result.get("checkpoint")
    appointment_id = hsi_result.get("appointment_id")
    if checkpoint and appointment_id:
        _UPSERT_CHECKPOINT.run(cur, entity_id=str(appointment_id), remote=db_values._json_clean(checkpoint))


# ---- operaciones -----------------------------------------------------------

_INSERT_REQUEST = _P(sa.insert(bk).values(
    dni=_bp("dni", bk.c.dni), first_name=_bp("first_name", bk.c.first_name),
    last_name=_bp("last_name", bk.c.last_name), birth_date=_bp("birth_date", bk.c.birth_date),
    sex=_bp("sex", bk.c.sex), contact=_bp("contact", bk.c.contact), service_id=_bp("service_id", bk.c.service_id),
    center_slug=_bp("center_slug", bk.c.center_slug), status=_PENDIENTE,
    referral_mime=_bp("referral_mime", bk.c.referral_mime),
    time_preference=_bp("time_preference", bk.c.time_preference),
    requester_dni=_bp("requester_dni", bk.c.requester_dni),
    slot_doctor_id=_bp("slot_doctor_id", bk.c.slot_doctor_id)).returning(bk.c.id))


def create_booking_request(dni, nombre, apellido, nacimiento, contacto,
                    servicio_id, centro_slug, orden_mime=None, sexo=None,
                    preferencia_horario=None, solicitante_dni=None,
                    slot_medico_id=None, actor=None):
    """Inserta una solicitud en estado 'pendiente'. Devuelve el id nuevo.
    (El archivo de la orden se guarda aparte y se registra con set_referral.)"""
    with db._connect() as conn, conn.cursor() as cur:
        sid = _INSERT_REQUEST.run(
            cur, dni=dni, first_name=nombre, last_name=apellido, birth_date=db_values._coerce_date(nacimiento),
            sex=sexo, contact=contacto, service_id=servicio_id, center_slug=centro_slug,
            referral_mime=orden_mime, time_preference=preferencia_horario,
            requester_dni=solicitante_dni or dni, slot_doctor_id=slot_medico_id).fetchone()["id"]
        _log_appointment_event(cur, sid, "creada", actor, None, "pendiente", None, None)
        return sid


_SET_REFERRAL = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(
    referral_path=_bp("referral_path", bk.c.referral_path)))
_SET_REFERRAL_MIME = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(
    referral_path=_bp("referral_path", bk.c.referral_path), referral_mime=_bp("referral_mime", bk.c.referral_mime)))


def set_referral(sid, orden_path, orden_mime=None):
    """Registra dónde quedó guardado el archivo de la orden."""
    with db._connect() as conn, conn.cursor() as cur:
        if orden_mime is None:
            _SET_REFERRAL.run(cur, sid=sid, referral_path=orden_path)
        else:
            _SET_REFERRAL_MIME.run(cur, sid=sid, referral_path=orden_path, referral_mime=orden_mime)


_SAVE_AI = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(
    ai_json=_bp("ai_json", bk.c.ai_json)).returning(*_ROW))


def save_ai(sid, ai_json):
    """Guarda el análisis de IA (jsonb) de la orden."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(_SAVE_AI.run(cur, sid=sid, ai_json=ai_json).fetchone(), "booking_requests")


def update_booking_patient_data(sid, campos):
    """Partial update of the patient's own data on one booking: only the keys
    present in `campos` (nombre, apellido, nacimiento, sexo) are written.
    Returns the updated row, or None when the booking does not exist."""
    columna = {"nombre": "first_name", "apellido": "last_name",
               "nacimiento": "birth_date", "sexo": "sex"}
    values = {}
    for k, v in campos.items():
        if k not in columna:
            continue
        values[columna[k]] = db_values._coerce_date(v) if k == "nacimiento" else v
    if not values:
        return get_booking_request(sid)
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(sqlcore.run(cur, sa.update(bk).where(bk.c.id == sid).values(
            **values).returning(*_ROW)).fetchone(), "booking_requests")


_GET = _P(sa.select(bk).where(bk.c.id == _bp("sid", bk.c.id)))


def get_booking_request(sid):
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(_GET.run(cur, sid=sid).fetchone(), "booking_requests")


_ASSIGN_PENDING = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id), bk.c.status == _PENDIENTE).values(
    assigned_doctor=_bp("medico", bk.c.assigned_doctor),
    assigned_doctor_id=_bp("medico_id", bk.c.assigned_doctor_id)).returning(*_ROW))


def assign_pending_booking_request(sid, medico, medico_id):
    """Asigna o reasigna sin approve. No toca solicitudes ya resueltas."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(_ASSIGN_PENDING.run(cur, sid=sid, medico=medico, medico_id=medico_id).fetchone(),
                        "booking_requests")


_UNASSIGN_PENDING = _P(sa.update(bk).where(
    bk.c.assigned_doctor_id == _bp("medico_id", bk.c.assigned_doctor_id), bk.c.status == _PENDIENTE
).values(assigned_doctor=sa.null(), assigned_doctor_id=sa.null()))


def unassign_pending_of_doctor(medico_id):
    """Disabled doctor → its PENDING requests go back to «sin asignar» (the
    queue re-suggests a bookable doctor). Resolved requests keep theirs:
    that is history. Returns how many were released."""
    with db._connect() as conn, conn.cursor() as cur:
        return _UNASSIGN_PENDING.run(cur, medico_id=medico_id).rowcount


_PENDING_AI = _P(sa.select(
    bk.c.id, bk.c.dni, bk.c.first_name.label("nombre"), bk.c.last_name.label("apellido"),
    bk.c.service_id.label("servicio_id"), bk.c.referral_path.label("orden_path"),
    bk.c.referral_mime.label("orden_mime"),
).where(
    bk.c.status == _PENDIENTE, bk.c.referral_path.is_not(None),
    bk.c.ai_json["verdict"].astext == _lit("pendiente_ia"),
).order_by(bk.c.created.asc()).limit(sa.bindparam("limit", type_=sa.Integer)))
_COUNT_PENDING_AI = _P(sa.select(sa.func.count().label("n")).select_from(bk).where(
    bk.c.ai_json["verdict"].astext == _lit("pendiente_ia")))


def booking_requests_pending_ai(limit=25):
    """Solicitudes que entraron mientras la IA estaba caída: orden guardada
    pero ai_json.verdict = 'pendiente_ia'. Las reprocesa ai_health cuando la IA
    vuelve. Sólo las que todavía tienen el archivo y siguen pendientes."""
    with db._connect() as conn, conn.cursor() as cur:
        return _PENDING_AI.run(cur, limit=limit).fetchall()


def count_pending_ai():
    """Cuántas solicitudes esperan análisis de IA (para el admin)."""
    try:
        with db._connect() as conn, conn.cursor() as cur:
            return _COUNT_PENDING_AI.run(cur).fetchone()["n"]
    except Exception:  # noqa: BLE001
        return 0


# ---- retención de archivos de orden (booking_referrals.sweep_referrals) ----
#
# Auditoría 2026-08-06: `data/ordenes/` retiene el archivo de la orden PARA
# SIEMPRE (promedio 118 KB, hasta 25 MB cada uno, uno por solicitud). Estas
# dos funciones son el lado Postgres del barrido periódico que vive en
# booking_requests.py: acá sólo se decide QUÉ solicitud llegó a un estado
# terminal hace más de N días -- nunca se toca el disco desde db.py.

def referrals_to_purge(retention_days, limit=200):
    """Filas con archivo de orden cuya solicitud llegó a un estado TERMINAL
    hace más de `retention_days`:
      - 'aprobada' con `appointment_at` ya pasado (el turno ya ocurrió,
        haya venido el paciente o no -- eso lo dice `attendance`, no esto),
      - 'rechazada' (resuelta),
      - 'cancelada' (por el paciente o el operador).
    Una 'aprobada' con el turno todavía en el futuro NUNCA es candidata --
    `terminal_at` da NULL para ella y el WHERE de abajo la descarta. Una
    'pendiente' tampoco: no tiene ninguna de las tres condiciones. Acotado
    por LIMIT para que un backlog grande se vaya vaciando en varias pasadas
    en vez de trabar la tabla en una sola."""
    terminal_at = sa.case(
        (sa.and_(bk.c.status == _APROBADA, bk.c.appointment_at.is_not(None)), bk.c.appointment_at),
        (bk.c.status == _CANCELADA, sa.func.coalesce(bk.c.cancelled_at, bk.c.resolved_at, bk.c.created)),
        (bk.c.status == _RECHAZADA, sa.func.coalesce(bk.c.resolved_at, bk.c.created)),
        else_=sa.null(),
    ).label("terminal_at")
    t = sa.select(bk.c.id, bk.c.referral_path, terminal_at).where(
        bk.c.referral_path.is_not(None)).subquery("t")
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t.c.id, t.c.referral_path.label("orden_path")).where(
            t.c.terminal_at.is_not(None), t.c.terminal_at < db_values._days_ago(int(retention_days))
        ).order_by(t.c.terminal_at.asc()).limit(int(limit))).fetchall()


_CLEAR_REFERRAL = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(
    referral_path=sa.null(), referral_mime=sa.null()))


def clear_referral_path(sid):
    """Limpia `referral_path`/`referral_mime` DESPUÉS de que el barrido ya
    borró el archivo del disco -- para que la próxima pasada no lo
    reintente, y para que "ver orden" en el admin sepa que ya no hay
    archivo (en vez de ofrecer un link roto)."""
    with db._connect() as conn, conn.cursor() as cur:
        _CLEAR_REFERRAL.run(cur, sid=sid)


# Quién canceló: el `actor` del último evento "cancelada" de la solicitud (`cancel` lo escribe en la
# misma transacción). Sin evento (canceladas antes de `appointment_events`) queda NULL: no se sabe.
_CANCEL_ACTOR_OF = sa.select(ae.c.actor).where(
    ae.c.request_id == bk.c.id, ae.c.kind == _CANCELADA).order_by(ae.c.id.desc()).limit(1)

_CANCEL_ACTOR = _P(sa.select(ae.c.actor).where(
    ae.c.request_id == _bp("sid", ae.c.request_id), ae.c.kind == _CANCELADA).order_by(ae.c.id.desc()).limit(1))


def cancel_actor(sid):
    """`appointment_events.actor` de la cancelación de la solicitud, o None si no consta."""
    with db._connect() as conn, conn.cursor() as cur:
        fila = _CANCEL_ACTOR.run(cur, sid=sid).fetchone()
        return fila["actor"] if fila else None


# Qué era la solicitud al cancelarla (`status_from` de su último evento "cancelada"): la ficha restaura
# solo un turno APROBADO (2026-11-05); un pedido nunca tuvo lugar en la agenda, y sin evento no se sabe.
_CANCELLED_FROM_OF = sa.select(ae.c.status_from).where(
    ae.c.request_id == bk.c.id, ae.c.kind == _CANCELADA).order_by(ae.c.id.desc()).limit(1)

_CANCELLED_FROM = _P(sa.select(ae.c.status_from).where(
    ae.c.request_id == _bp("sid", ae.c.request_id), ae.c.kind == _CANCELADA).order_by(ae.c.id.desc()).limit(1))


def cancelled_from(sid):
    """El estado que tenía la solicitud antes de su última cancelación, o None si no consta."""
    with db._connect() as conn, conn.cursor() as cur:
        fila = _CANCELLED_FROM.run(cur, sid=sid).fetchone()
        return fila["status_from"] if fila else None


_LIST_BY_DNI = _P(sa.select(
    bk.c.id, bk.c.dni, bk.c.first_name, bk.c.last_name, bk.c.requester_dni, bk.c.service_id,
    bk.c.center_slug, bk.c.status, bk.c.appointment_at, bk.c.time_preference,
    bk.c.assigned_doctor, bk.c.assigned_doctor_id, bk.c.rejection_reason,
    bk.c.created, bk.c.ai_json, bk.c.requested_at,
    _CANCEL_ACTOR_OF.correlate(bk).scalar_subquery().label("cancelled_actor"),
).where(sa.or_(bk.c.dni == _bp("dni", bk.c.dni), bk.c.requester_dni == _bp("dni", bk.c.requester_dni))
        ).order_by(bk.c.created.desc()))


def list_by_dni(dni):
    """Vista de la cuenta: turnos propios y de sus personas a cargo.

    Conserva el nombre histórico por compatibilidad. Incluye metadatos +
    ai_json (para 'Cómo prepararte', R4 —
    _public_row en solicitudes.py sólo reexpone ai_json.preparacion, nunca el
    resto), sin exponer el archivo de la orden. `preferencia_horario` y
    `medico_asignado` (2026-07-18): la tabla "Mis turnos" los necesita para la
    celda Turno (pendiente muestra la preferencia elegida; aprobada, quién
    atiende) — faltaban acá y quedaban None pese a estar en la fila real
    (bug descubierto probando la tabla nueva: SELECT * en get_booking_request los
    traía bien, este SELECT explícito los omitía). `medico_asignado_id`
    (2026-07-19): la tarjeta destacada "Tu próximo turno" necesita el id del
    catálogo para resolver la foto del médico (doctor_photos.photo_url) — la
    columna ya existía (approve() la completa best-effort al approve) pero
    este SELECT explícito seguía sin traerla, mismo patrón que el bug de
    arriba. `requested_at` (2026-10-26, Alembic 0037): el turno pedido, que
    una solicitud rechazada o cancelada sigue mostrando en «Anteriores»."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(_LIST_BY_DNI.run(cur, dni=dni).fetchall(), "booking_requests")


# Sin filtro de estado, la cola del admin usada mostrar la tabla ENTERA sin
# LIMIT -- con meses de uso, miles de filas en cada carga de /api/admin/requests
# (auditoría 2026-08-06). El default ahora es la vista ACCIONABLE: TODAS las
# pendientes (nunca se recortan -- son justo las que hay que resolver) + una
# ventana reciente de resueltas, para que "Solicitudes" siga mostrando el
# historial de los últimos días sin cargar el histórico completo. Un `estado`
# explícito (una pestaña puntual, ej. "aprobada") sigue devolviendo SÓLO ese
# estado, también acotado por LIMIT/OFFSET -- ninguna pestaña vuelve a traer
# la tabla entera. `count_admin` da el total sin el LIMIT, para el "N de M".
# (`_ADMIN_LIST_DEFAULT_LIMIT` y `_ADMIN_LIST_RESOLVED_WINDOW_DAYS` quedan en core/db.py.)

_BY_STATE = bk.c.status == _bp("estado", bk.c.status)
_ADMIN_VIEW = sa.or_(
    bk.c.status == _PENDIENTE,
    sa.and_(bk.c.status != _PENDIENTE,
            bk.c.created > sa.func.now() - (sa.bindparam("window", type_=sa.Integer)
                                            * sa.literal_column("interval '1 day'", sa.Interval))))
_PAGE = (sa.bindparam("limit", type_=sa.Integer), sa.bindparam("offset", type_=sa.Integer))
# Quién canceló, con la misma subconsulta que `_LIST_BY_DNI`: la fila de la cola dice «por el paciente» /
# «por el centro» / «Cancelada» (sin columna nueva, sin migración).
_ADMIN_CANCEL_ACTOR = _CANCEL_ACTOR_OF.correlate(bk).scalar_subquery().label("cancelled_actor")
_LIST_STATE = _P(sa.select(bk, _ADMIN_CANCEL_ACTOR).where(_BY_STATE).order_by(bk.c.created.desc()).limit(_PAGE[0]).offset(_PAGE[1]))
_LIST_DEFAULT = _P(sa.select(bk, _ADMIN_CANCEL_ACTOR).where(_ADMIN_VIEW).order_by(bk.c.created.desc()).limit(_PAGE[0]).offset(_PAGE[1]))
_COUNT_STATE = _P(sa.select(sa.func.count().label("n")).select_from(bk).where(_BY_STATE))
_COUNT_DEFAULT = _P(sa.select(sa.func.count().label("n")).select_from(bk).where(_ADMIN_VIEW))


def list_admin(estado=None, limit=None, offset=0):
    """Cola del admin: registros completos (incluye ai_json), acotados.

    Ver el comentario de arriba para el criterio por defecto sin `estado`.
    """
    limit = int(limit) if limit else _ADMIN_LIST_DEFAULT_LIMIT
    offset = int(offset or 0)
    with db._connect() as conn, conn.cursor() as cur:
        if estado:
            rows = _LIST_STATE.run(cur, estado=estado, limit=limit, offset=offset).fetchall()
        else:
            rows = _LIST_DEFAULT.run(cur, window=_ADMIN_LIST_RESOLVED_WINDOW_DAYS, limit=limit,
                                     offset=offset).fetchall()
        return db_wire._wire_all(rows, "booking_requests")


def count_admin(estado=None):
    """Total que `list_admin(estado)` vería SIN el LIMIT -- misma condición
    por defecto (pendientes + ventana reciente de resueltas) -- para que la
    cola pueda mostrar "N de M" sin traer las N filas completas."""
    with db._connect() as conn, conn.cursor() as cur:
        if estado:
            return _COUNT_STATE.run(cur, estado=estado).fetchone()["n"]
        return _COUNT_DEFAULT.run(cur, window=_ADMIN_LIST_RESOLVED_WINDOW_DAYS).fetchone()["n"]


_IN_RANGE = _P(sa.select(bk).where(
    bk.c.status == _APROBADA, bk.c.appointment_at >= _bp("desde", bk.c.appointment_at),
    bk.c.appointment_at < _bp("hasta", bk.c.appointment_at)).order_by(bk.c.appointment_at))


def approved_in_range(desde, hasta):
    """Solicitudes aprobadas con turno en [desde, hasta).

    Reemplaza el patrón "traer TODAS las aprobadas y filtrar en Python" que
    corría en cada aprobación para el chequeo de colisión de médico. El rango
    acotado usa ``booking_requests_status_appointment_idx`` en vez de escanear
    el histórico completo de aprobadas. ``desde``/``hasta`` deben ser tz-aware
    (se comparan contra la columna ``timestamptz`` por instante, sin depender
    del offset)."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(_IN_RANGE.run(cur, desde=desde, hasta=hasta).fetchall(), "booking_requests")


# ---- asistencia / ausentismo ---------------------------------------------

def _hsi_no_shows(*columns):
    """The HSI appointments of the patients in `:dnis` that were missed and have no local request linked
    yet (a linked one is counted as the request, never twice): `columns` over `a` (the appointment) and
    `p` (its patient). Returns the statement (to order) and `a`."""
    a, p, q = sm.hsi_appointments.alias("a"), sm.hsi_patients.alias("p"), bk.alias("q")
    return sa.select(*[c(a, p) for c in columns]).select_from(
        a.join(p, p.c.hsi_patient_id == a.c.hsi_patient_id)
    ).where(
        _in("dnis", p.c.dni), a.c.payload["state"].astext == "no_show",
        ~sa.exists().where(q.c.hsi_appointment_id == a.c.hsi_appointment_id)
    ), a


_NO_SHOW_COLUMNS = (lambda a, p: p.c.dni, lambda a, p: a.c.hsi_appointment_id,
                    lambda a, p: a.c.payload["scheduled_at"].astext.label("scheduled_at"))
_SUMMARY_NO_SHOWS_STMT, _a = _hsi_no_shows(*_NO_SHOW_COLUMNS)
_SUMMARY_NO_SHOWS = _P(_SUMMARY_NO_SHOWS_STMT.order_by(_a.c.payload["scheduled_at"].astext.desc()))
_RANKING_NO_SHOWS = _P(_hsi_no_shows(*_NO_SHOW_COLUMNS)[0])
_SUMMARY_COUNTS = _P(sa.select(
    bk.c.dni,
    sa.func.count().filter(bk.c.attendance.is_not(None)).label("visitas"),
    sa.func.count().filter(bk.c.attendance == _lit("presente")).label("presentes"),
    sa.func.count().filter(bk.c.attendance == _lit("ausente")).label("faltas"),
).where(_in("dnis", bk.c.dni)).group_by(bk.c.dni))
_SUMMARY_EVENTS = _P(sa.select(
    sm.attendance_events.c.request_id.label("solicitud_id"), sm.attendance_events.c.dni,
    sm.attendance_events.c.attendance_from.label("de_asistencia"),
    sm.attendance_events.c.attendance_to.label("a_asistencia"),
    sm.attendance_events.c.operator.label("operador"), sm.attendance_events.c.source.label("origen"),
    sm.attendance_events.c.created,
).where(_in("dnis", sm.attendance_events.c.dni)).order_by(
    sm.attendance_events.c.created.desc(), sm.attendance_events.c.id.desc()))


def attendance_summary_by_dnis(dnis, limite=3):
    """Estado vigente + bitácora reciente para las filas del operador.

    ``booking_requests.attendance`` cuenta los turnos vinculados y sus
    correcciones. Las ausencias HSI ya importadas también cuentan cuando
    todavía no existe una solicitud Sana vinculada; al vincularla, ``NOT
    EXISTS`` evita contarla dos veces. ``attendance_events`` conserva cada
    cambio local para auditoría.
    """
    clean = sorted({str(d or "").strip() for d in dnis if str(d or "").strip()})
    empty = lambda: {"visitas": 0, "presentes": 0, "faltas": 0, "faltas_hsi": 0,
                     "bloqueado_web": False, "historial": []}
    out = {dni: empty() for dni in clean}
    if not clean:
        return out
    with db._connect() as conn, conn.cursor() as cur:
        for row in _SUMMARY_COUNTS.run(cur, dnis=clean).fetchall():
            item = out.setdefault(row["dni"], empty())
            item["visitas"] = int(row.get("visitas") or 0)
            item["presentes"] = int(row.get("presentes") or 0)
            item["faltas"] = int(row.get("faltas") or 0)
        for row in _SUMMARY_EVENTS.run(cur, dnis=clean).fetchall():
            event = dict(row)
            if isinstance(event.get("created"), datetime):
                event["created"] = event["created"].isoformat()
            out.setdefault(row["dni"], empty())["historial"].append(event)
        for row in _SUMMARY_NO_SHOWS.run(cur, dnis=clean).fetchall():
            item = out.setdefault(row["dni"], empty())
            item["visitas"] += 1
            item["faltas"] += 1
            item["faltas_hsi"] += 1
            scheduled_at = row.get("scheduled_at")
            item["historial"].append({
                "solicitud_id": None, "dni": row["dni"], "de_asistencia": None,
                "a_asistencia": "ausente", "operador": "HSI", "origen": "hsi",
                "created": scheduled_at,
            })

    def _history_time(event):
        value = event.get("created")
        if isinstance(value, datetime):
            return value.timestamp()
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return 0

    for item in out.values():
        # Historial breve en la tabla: suficiente para operar sin incrustar
        # una auditoría ilimitada en cada fila del listado.
        item["historial"] = sorted(item["historial"], key=_history_time, reverse=True)[:12]
        item["bloqueado_web"] = item["faltas"] >= max(1, int(limite or 3))
    return out


_OF_PATIENT = _P(sa.select(
    bk.c.id, bk.c.service_id, bk.c.center_slug, bk.c.status, bk.c.appointment_at,
    bk.c.requested_at, bk.c.assigned_doctor, bk.c.assigned_doctor_id, bk.c.attendance,
    bk.c.rejection_reason, bk.c.created, bk.c.overbooking, bk.c.hsi_appointment_id, bk.c.hsi_status,
    _CANCELLED_FROM_OF.correlate(bk).scalar_subquery().label("cancelled_from"),
).where(bk.c.dni == _bp("dni", bk.c.dni)).order_by(
    sa.func.coalesce(bk.c.appointment_at, bk.c.requested_at, bk.c.created).desc(),
    bk.c.id.desc(),
).limit(100))


def requests_of_patient(dni):
    """Todos los turnos y pedidos de ESTA persona como paciente (no los que
    pidió por un dependiente), el más reciente primero, tope 100: la lista de
    «Turnos» de su ficha en el panel. Sin el archivo de la orden ni el ai_json."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(_OF_PATIENT.run(cur, dni=str(dni or "").strip()).fetchall(),
                                 "booking_requests")


_GRANTED_COUNTS = _P(sa.select(
    bk.c.dni,
    sa.func.count().label("n"),
    sa.func.min(bk.c.appointment_at).filter(bk.c.appointment_at > sa.func.now()).label("proximo"),
).where(_in("dnis", bk.c.dni), bk.c.status == _APROBADA).group_by(bk.c.dni))


def granted_counts_by_dnis(dnis):
    """`{dni: {"otorgados": n, "proximo": datetime|None}}` en UNA consulta: los
    turnos aprobados de cada paciente (pasados y por venir) y el próximo. Es lo
    que dice la columna «Turnos» de la lista; los que no tienen ninguno no
    aparecen en el resultado."""
    limpios = sorted({str(d or "").strip() for d in dnis if str(d or "").strip()})
    if not limpios:
        return {}
    with db._connect() as conn, conn.cursor() as cur:
        return {r["dni"]: {"otorgados": int(r["n"] or 0), "proximo": r["proximo"]}
                for r in _GRANTED_COUNTS.run(cur, dnis=limpios).fetchall()}


_SERVICES_OF = _P(sa.select(
    bk.c.dni, bk.c.service_id, sa.func.count().label("n"),
).where(
    _in("dnis", bk.c.dni), bk.c.status.in_([_PENDIENTE, _APROBADA]), bk.c.service_id.isnot(None),
).group_by(bk.c.dni, bk.c.service_id))


def services_by_dnis(dnis):
    """`{dni: [(service_id, n), ...]}` en UNA consulta: los servicios por los que
    cada paciente tiene turnos (aprobados o pedidos pendientes), el que más veces
    aparece primero. Es lo que dice la columna «Servicio» de la lista; los que no
    tienen ninguno no aparecen en el resultado."""
    limpios = sorted({str(d or "").strip() for d in dnis if str(d or "").strip()})
    if not limpios:
        return {}
    out = {}
    with db._connect() as conn, conn.cursor() as cur:
        for r in _SERVICES_OF.run(cur, dnis=limpios).fetchall():
            out.setdefault(r["dni"], []).append((r["service_id"], int(r["n"] or 0)))
    for lista in out.values():
        lista.sort(key=lambda p: (-p[1], str(p[0])))
    return out


_DOCTOR_ID = sa.func.coalesce(bk.c.assigned_doctor_id, bk.c.slot_doctor_id)

_DOCTORS_OF = _P(sa.select(
    bk.c.dni, _DOCTOR_ID.label("medico_id"), sa.func.count().label("n"),
).where(
    _in("dnis", bk.c.dni), bk.c.status.in_([_PENDIENTE, _APROBADA]), _DOCTOR_ID.isnot(None),
).group_by(bk.c.dni, _DOCTOR_ID))


def doctors_by_dnis(dnis):
    """`{dni: [(medico_id, n), ...]}` en UNA consulta: los médicos con quienes
    cada paciente tiene turnos (aprobados o pedidos pendientes), el que más
    veces aparece primero. Es lo que dice la columna «Médico» de la lista; los
    que no tienen ninguno no aparecen en el resultado."""
    limpios = sorted({str(d or "").strip() for d in dnis if str(d or "").strip()})
    if not limpios:
        return {}
    out = {}
    with db._connect() as conn, conn.cursor() as cur:
        for r in _DOCTORS_OF.run(cur, dnis=limpios).fetchall():
            out.setdefault(r["dni"], []).append((r["medico_id"], int(r["n"] or 0)))
    for lista in out.values():
        lista.sort(key=lambda p: (-p[1], str(p[0])))
    return out


_RANKING_ROWS = _P(sa.select(
    bk.c.id, bk.c.dni, bk.c.service_id, bk.c.status, bk.c.appointment_at, bk.c.attendance,
    bk.c.confirmation, bk.c.cancelled_at, bk.c.created,
).where(
    _in("dnis", bk.c.dni), bk.c.status.in_([_APROBADA, _CANCELADA]),
    sa.func.coalesce(bk.c.appointment_at, bk.c.created) >= _bp("desde", bk.c.created),
).order_by(bk.c.appointment_at.nulls_last()))


def booking_requests_for_ranking(dnis, ventana_dias=1095):
    """Historial mínimo que necesita patient_ranking.py, en UNA consulta.

    Devuelve sólo las columnas que entran en la fórmula (no el ai_json ni la
    orden: la lista de la consola son 200 pacientes). Se limita a los turnos
    OTORGADOS —aprobados o cancelados después de haberlo sido— porque una
    solicitud pendiente o rechazada nunca ocupó un lugar en la agenda; el
    módulo lo vuelve a chequear, pero filtrarlo acá es lo que mantiene la
    consulta barata.

    Las ausencias importadas de HSI que todavía no tienen solicitud Sana
    vinculada se agregan como filas sintéticas, con el mismo ``NOT EXISTS``
    que usa ``attendance_summary_by_dnis``: si no, el diálogo de visitas
    diría "3 ausencias" y el ranking calcularía sobre 0, y dos números que se
    contradicen en la misma pantalla no los cree nadie.
    """
    limpios = sorted({str(d or "").strip() for d in dnis if str(d or "").strip()})
    if not limpios:
        return []
    desde = datetime.now(timezone.utc) - timedelta(days=max(30, int(ventana_dias or 1095)))
    with db._connect() as conn, conn.cursor() as cur:
        filas = [db_wire._wire(r, "booking_requests")
                 for r in _RANKING_ROWS.run(cur, dnis=limpios, desde=desde).fetchall()]
        for row in _RANKING_NO_SHOWS.run(cur, dnis=limpios).fetchall():
            scheduled_at = row.get("scheduled_at")
            if not scheduled_at:
                continue
            try:
                cuando = datetime.fromisoformat(scheduled_at).astimezone(db_values._ART)
            except ValueError:
                continue
            if cuando < desde:
                continue
            filas.append({
                "id": None, "dni": row["dni"],
                # Sin servicio conocido no se puede juzgar redundancia: se le
                # da un id propio por turno para que nunca agrupe con otro.
                "servicio_id": f"hsi:{row.get('hsi_appointment_id')}",
                "estado": "aprobada", "turno_dt": cuando, "asistencia": "ausente",
                "confirmacion": None, "cancelada_en": None, "created": cuando,
            })
    return filas


def attendance_summary_dni(dni, limite=3):
    return attendance_summary_by_dnis([dni], limite).get(
        str(dni or "").strip(),
        {"visitas": 0, "presentes": 0, "faltas": 0, "faltas_hsi": 0,
         "bloqueado_web": False, "historial": []})


_LOCK_ROW = _P(sa.select(bk).where(bk.c.id == _bp("sid", bk.c.id)).with_for_update())
_SET_ATTENDANCE = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(
    attendance=_bp("attendance", bk.c.attendance), attendance_updated_at=sa.func.now(),
    attendance_updated_by=_bp("operator", bk.c.attendance_updated_by)).returning(*_ROW))
_INSERT_ATTENDANCE_EVENT = _P(sa.insert(sm.attendance_events).values(
    request_id=_bp("sid", sm.attendance_events.c.request_id), dni=_bp("dni", sm.attendance_events.c.dni),
    attendance_from=_bp("attendance_from", sm.attendance_events.c.attendance_from),
    attendance_to=_bp("attendance_to", sm.attendance_events.c.attendance_to),
    operator=_bp("operator", sm.attendance_events.c.operator), source=_bp("source", sm.attendance_events.c.source)))
_queue = sm.day_queue_events
_INSERT_QUEUE_EVENT = _P(sa.insert(_queue).values(
    appointment_day=_bp("appointment_day", _queue.c.appointment_day), center_slug=_bp("center_slug", _queue.c.center_slug),
    assigned_doctor_id=_bp("assigned_doctor_id", _queue.c.assigned_doctor_id),
    assigned_doctor=_bp("assigned_doctor", _queue.c.assigned_doctor)).returning(_queue.c.id))
_NOTIFY_QUEUE = _P(sa.select(sa.func.pg_notify(sa.literal_column("'turnos_day_queue'"),
                                               sa.bindparam("payload", type_=sa.Text))))


def mark_attendance(sid, asistencia, operador, origen="sana", exigir_no_futuro=True):
    """Cambia la asistencia vigente y agrega SIEMPRE una entrada auditable.

    La ruta administrativa (y, desde la cola del día, el propio médico) usa
    ``exigir_no_futuro`` y falla cerrada para una solicitud pendiente/
    rechazada/cancelada o un turno de un día ART todavía POR VENIR — el
    mismo día ya es marcable (el médico va tildando presente/ausente a
    medida que atiende, no recién a la noche), sólo el día siguiente en
    adelante queda bloqueado. Antes exigía que la hora exacta ya hubiera
    pasado (``exigir_pasado``); ese nombre murió con la regla (responsable del producto,
    2026-07-24 — el médico necesita marcar ausencias durante la jornada).
    La importación HSI puede preservar una marca clínica ya registrada sin
    volver a encolar la misma modificación.
    """
    if asistencia not in ("presente", "ausente"):
        raise ValueError("asistencia_invalida")
    if origen not in ("sana", "hsi"):
        raise ValueError("origen_invalido")
    with db._connect() as conn, conn.cursor() as cur:
        row = db_wire._wire(_LOCK_ROW.run(cur, sid=sid).fetchone(), "booking_requests")
        if not row:
            return None
        if exigir_no_futuro and (row.get("estado") != "aprobada" or not row.get("turno_dt")
                                  or row["turno_dt"].astimezone(db_values._ART).date() > datetime.now(db_values._ART).date()):
            raise ValueError("asistencia_no_elegible")
        anterior = row.get("asistencia")
        # A repeated HSI poll is not a new clinical event. Operator corrections
        # are intentionally still auditable, even when they repeat the value.
        if origen == "hsi" and anterior == asistencia:
            return row
        updated = db_wire._wire(_SET_ATTENDANCE.run(cur, sid=sid, attendance=asistencia, operator=operador).fetchone(),
                           "booking_requests")
        _INSERT_ATTENDANCE_EVENT.run(
            cur, sid=sid, dni=row.get("dni") or "", attendance_from=anterior, attendance_to=asistencia,
            operator=operador, source=origen)
        _log_appointment_event(
            cur, sid, "asistencia", operador if origen == "sana" else f"hsi:{operador}",
            row.get("estado"), row.get("estado"), row.get("turno_dt"), row.get("turno_dt"),
            anterior, asistencia)
        # Persist this with the attendance mutation. The NOTIFY is only the
        # latency hint: replay from day_queue_events remains correct after a
        # missed notification, reconnect, or worker restart.
        appointment_at = updated.get("turno_dt")
        if appointment_at:
            appointment_day = appointment_at.astimezone(db_values._ART).date()
            event_id = _INSERT_QUEUE_EVENT.run(
                cur, appointment_day=appointment_day, center_slug=updated.get("centro_slug"),
                assigned_doctor_id=updated.get("medico_asignado_id"),
                assigned_doctor=updated.get("medico_asignado")).fetchone()["id"]
            # Postgres delivers this notification only after commit, so a
            # listener can always read the referenced durable event.
            _NOTIFY_QUEUE.run(cur, payload=str(event_id))
        return updated


_LOCK_STATE = _P(sa.select(bk.c.status, bk.c.appointment_at, bk.c.service_id).where(
    bk.c.id == _bp("sid", bk.c.id)).with_for_update())


def _lock_appointment_state(cur, sid):
    """Row-locks the request and returns its status/appointment BEFORE the
    caller's write, for the "from" half of `appointment_events`. Empty dict
    when the row doesn't exist (the caller's own UPDATE then matches
    nothing, as before)."""
    return dict(_LOCK_STATE.run(cur, sid=sid).fetchone() or {})


_SLOT_TAKEN = _P(sa.select(sa.func.count().label("n")).select_from(bk).where(
    bk.c.status == _APROBADA, sa.not_(bk.c.overbooking), bk.c.service_id == _bp("service", bk.c.service_id),
    bk.c.appointment_at == _bp("when", bk.c.appointment_at), bk.c.id != _bp("sid", bk.c.id)))


def slot_taken(service, when, sid):
    """Whether another regular approved appointment holds (service, time) — the read twin of
    `_enforce_slot_capacity` with capacity 1, for the patient file's «Restaurar» (2026-11-05)."""
    when = db_values._coerce_dt(when)
    if service is None or when is None:
        return False
    with db._connect() as conn, conn.cursor() as cur:
        return _SLOT_TAKEN.run(cur, service=service, when=when, sid=sid).fetchone()["n"] > 0


def _enforce_slot_capacity(cur, sid, capacity, turno_dt, servicio_id, previo):
    """SlotHold.tla OneApprovedPerSlot (verificación formal 2026-09-24): a
    web-offered slot holds `capacity` regular approvals — the public model is
    one appointment per (service, time) (site_availability._web_slots_enabled;
    `slot_holds` keys holds and "taken" by exactly that pair). `try_take`
    reads the approved set and takes the hold in two transactions, so two
    auto-confirmations of a doctor-less service (they approve with no doctor
    id, outside `booking_requests_doctor_slot_uniq`) could both pass. This is
    the same check, atomic: under a per-slot advisory lock in the approving
    transaction; the loser gets `SlotConflict` and stays pending. Only the
    web paths pass a capacity — the desk may still give several patients the
    same time ("a las 8, por orden de llegada"), which a unique index would
    have forbidden."""
    service = servicio_id if servicio_id is not None else previo.get("service_id")
    when = db_values._coerce_dt(turno_dt)
    if not capacity or service is None or when is None:
        return
    _xact_lock(cur, f"slot:{service}:{when.astimezone(timezone.utc).isoformat()}")
    if _SLOT_TAKEN.run(cur, service=service, when=when, sid=sid).fetchone()["n"] >= int(capacity):
        raise SlotConflict(f"slot capacity {capacity} reached for {service} at {when}")


_LOG_EVENT = _P(sa.insert(ae).values(
    request_id=_bp("sid", ae.c.request_id), kind=_bp("kind", ae.c.kind), actor=_bp("actor", ae.c.actor),
    status_from=_bp("status_from", ae.c.status_from), status_to=_bp("status_to", ae.c.status_to),
    appointment_from=_bp("appointment_from", ae.c.appointment_from),
    appointment_to=_bp("appointment_to", ae.c.appointment_to),
    attendance_from=_bp("attendance_from", ae.c.attendance_from),
    attendance_to=_bp("attendance_to", ae.c.attendance_to)))


def _log_appointment_event(cur, sid, kind, actor, status_from, status_to,
                           appointment_from, appointment_to,
                           attendance_from=None, attendance_to=None):
    """One `appointment_events` row, on the caller's cursor — i.e. in the
    SAME transaction as the change it records: a rolled-back change leaves
    no event, a committed one always has its event."""
    _LOG_EVENT.run(cur, sid=sid, kind=kind, actor=actor, status_from=status_from, status_to=status_to,
                   appointment_from=appointment_from, appointment_to=appointment_to,
                   attendance_from=attendance_from, attendance_to=attendance_to)


_EVENTS = _P(sa.select(ae).where(ae.c.request_id == _bp("sid", ae.c.request_id)).order_by(ae.c.id))


def appointment_events(sid):
    """The request's history, oldest first."""
    with db._connect() as conn, conn.cursor() as cur:
        return [dict(r) for r in _EVENTS.run(cur, sid=sid).fetchall()]


_TOCA = sa.bindparam("toca", type_=sa.Boolean)
_APPROVE_SET = dict(
    appointment_at=_bp("appointment_at", bk.c.appointment_at),
    hsi_patient_id=_bp("hsi_patient_id", bk.c.hsi_patient_id),
    hsi_appointment_id=_bp("hsi_appointment_id", bk.c.hsi_appointment_id),
    hsi_status=_bp("hsi_status", bk.c.hsi_status),
    service_id=sa.func.coalesce(_bp("servicio_id", bk.c.service_id), bk.c.service_id),
    center_slug=sa.func.coalesce(_bp("centro_slug", bk.c.center_slug), bk.c.center_slug),
    assigned_doctor=sa.case((_TOCA, _bp("medico", bk.c.assigned_doctor)), else_=bk.c.assigned_doctor),
    assigned_doctor_id=sa.case((_TOCA, _bp("medico_id", bk.c.assigned_doctor_id)), else_=bk.c.assigned_doctor_id),
    overbooking=_bp("overbooking", bk.c.overbooking), resolved_by=_bp("resolved_by", bk.c.resolved_by),
    resolved_at=sa.func.now())
_APPROVE = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id), bk.c.status == _PENDIENTE).values(
    status=_APROBADA, **_APPROVE_SET).returning(*_ROW))
# La mesa devuelve a la agenda un turno cancelado (2026-11-05): mismo UPDATE con CAS sobre 'cancelada';
# la cancelación y la confirmación que la pidió («no voy») dejan de contar, como un turno recién dado.
_RESTORE = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id), bk.c.status == _CANCELADA).values(
    status=_APROBADA, cancelled_at=sa.null(), confirmation=sa.null(), confirmation_at=sa.null(),
    confirmation_channel=sa.null(), **_APPROVE_SET).returning(*_ROW))


def approve(sid, turno_dt, hsi_result=None, servicio_id=None, medico_asignado=None,
           resuelta_por=None, medico_asignado_id=None, sobreturno=False,
           overbooking_capacity=None, overbooking_window=None,
           overbooking_lock_key=None, overbooking_center_slug=None,
           slot_capacity=None, centro_slug=None, restaurar=False):
    """Aprueba: fija turno_dt y el resultado de HSI. hsi_result =
    {estado, patient_id, appointment_id}. `servicio_id`/`medico_asignado`
    (opcionales) son la reasignación del operador — None deja lo que había.
    `medico_asignado`/`medico_asignado_id` son UN par: si viene cualquiera de
    los dos se escriben juntos (id None = el nombre no resolvió a un médico
    del catálogo); la resolución la hace el caller con
    `doctor_appointments.resolve_pair` (2026-10-23).
    `centro_slug` (optional) = the center of the service the operator
    redirected the request to; None keeps the center it had.
    `resuelta_por` = usuario admin/operador que aprobó (auditoría).
    `sobreturno` exime a esta fila de `booking_requests_doctor_slot_uniq` —
    pasarlo True es la única forma de aprobar un (médico, horario) ya ocupado
    sin chocar contra el índice.

    Status-CAS: el UPDATE sólo pega si la fila SIGUE 'pendiente'
    (`WHERE id=%s AND status='pendiente'`) — levanta `AlreadyProcessed` si
    otra corrida ya la resolvió. Además atrapa la unique-violation del índice
    parcial y levanta `SlotConflict` cuando `sobreturno=False` y otra
    solicitud ganó ese mismo (médico, horario) en la misma carrera. Devuelve
    el registro actualizado en el caso normal.

    `restaurar=True`: la mesa devuelve a la agenda un turno CANCELADO (ficha, 2026-11-05) — CAS
    sobre 'cancelada', sin `cancelled_at` ni confirmación, evento "restaurada"."""
    hsi_result = hsi_result or {}
    # El médico es UN par (problems.md №3, 2026-10-23): si viene nombre o id,
    # se escriben los DOS tal cual llegan (el caller ya los resolvió contra el
    # catálogo con doctor_appointments.resolve_pair); sin ninguno, el par
    # queda como estaba. Nunca un COALESCE por columna: dejaba el id del
    # médico anterior debajo de un nombre nuevo.
    medico_asignado = (medico_asignado or "").strip() or None
    medico_asignado_id = str(medico_asignado_id or "").strip() or None
    toca_medico = bool(medico_asignado or medico_asignado_id)
    import psycopg
    try:
        with db._connect() as conn, conn.cursor() as cur:
            previo = _lock_appointment_state(cur, sid)
            if not sobreturno:
                _enforce_slot_capacity(cur, sid, slot_capacity, turno_dt, servicio_id, previo)
            if overbooking_capacity is not None:
                if not sobreturno or not overbooking_window or not overbooking_lock_key:
                    raise ValueError("overbooking capacity requires a marked appointment, window and lock key")
                window_start, window_end = overbooking_window
                # This lock is deliberately wider than one appointment minute:
                # percentage capacity is shared by every regular slot in this
                # schedule band on this ART date.  Count and status-CAS update
                # occur in the same transaction so simultaneous approvals in
                # different slots cannot both consume its last allowance.
                _xact_lock(cur, overbooking_lock_key)
                usados = _overbooking_count_cur(
                    cur, window_start, window_end, medico_asignado,
                    medico_asignado_id, center_slug=overbooking_center_slug,
                    exclude_sid=sid)
                if usados >= int(overbooking_capacity):
                    raise OverbookingCapacityReached("percentage overbooking capacity exhausted")
            fila = (_RESTORE if restaurar else _APPROVE).run(
                cur, sid=sid, appointment_at=db_values._coerce_dt(turno_dt), hsi_patient_id=hsi_result.get("patient_id"),
                hsi_appointment_id=hsi_result.get("appointment_id"), hsi_status=hsi_result.get("estado"),
                servicio_id=servicio_id, centro_slug=centro_slug, toca=toca_medico, medico=medico_asignado, medico_id=medico_asignado_id,
                overbooking=bool(sobreturno), resolved_by=resuelta_por).fetchone()
            if fila is None:
                raise AlreadyProcessed(f"booking_requests id={sid} ya no estaba "
                                       + ("cancelada" if restaurar else "pendiente"))
            _log_appointment_event(cur, sid, "restaurada" if restaurar else "aprobada", resuelta_por,
                                      previo.get("status"), fila["status"],
                                      previo.get("appointment_at"), fila["appointment_at"])
            row = db_wire._wire(fila, "booking_requests")
            _checkpoint(cur, hsi_result)
            return row
    except psycopg.errors.UniqueViolation as e:
        raise SlotConflict(f"booking_requests_doctor_slot_uniq: {e}") from e


_OVERBOOKED_ROWS = _P(sa.select(bk.c.id, bk.c.assigned_doctor, bk.c.assigned_doctor_id).where(
    bk.c.status == _APROBADA, bk.c.overbooking == sa.true(),
    bk.c.appointment_at >= _bp("desde", bk.c.appointment_at), bk.c.appointment_at < _bp("hasta", bk.c.appointment_at),
    bk.c.center_slug.is_not_distinct_from(_bp("center_slug", bk.c.center_slug))))


def _overbooking_count_cur(cur, desde, hasta, medico, medico_id=None, center_slug=None, exclude_sid=None):
    """Count active extra appointments for one resolved clinician and band."""
    rows = _OVERBOOKED_ROWS.run(cur, desde=db_values._coerce_dt(desde), hasta=db_values._coerce_dt(hasta),
                                center_slug=center_slug).fetchall()
    # Current rows resolve the catalog id. Legacy extras may only have a
    # free-text name, so retain them when its canonical patient-site form is
    # the same clinician; an id-only predicate would silently reopen a budget.
    def norm(value):
        return unicodedata.normalize("NFKD", str(value or "")).encode(
            "ascii", "ignore").decode().lower()
    wanted = norm(medico)
    return sum(1 for row in rows
               if row["id"] != exclude_sid
               and ((bool(medico_id) and row["assigned_doctor_id"] == medico_id)
                    or (row["assigned_doctor_id"] is None and norm(row["assigned_doctor"]) == wanted)))


def overbooking_count(desde, hasta, medico, medico_id=None, center_slug=None, exclude_sid=None):
    """Read-only counterpart of the atomic percentage-capacity count."""
    with db._connect() as conn, conn.cursor() as cur:
        return _overbooking_count_cur(cur, desde, hasta, medico, medico_id, center_slug, exclude_sid)


def _approved_future(extra=None):
    """The approved requests whose time is not before `:desde` (default now) nor after `:hasta` (default:
    no end), in time order; `extra` is one more condition."""
    stmt = sa.select(bk).where(
        bk.c.status == _APROBADA, bk.c.appointment_at.is_not(None),
        bk.c.appointment_at >= sa.func.coalesce(_bp("desde", bk.c.appointment_at), sa.func.now()),
        bk.c.appointment_at <= sa.func.coalesce(_bp("hasta", bk.c.appointment_at),
                                                sa.literal_column("'infinity'::timestamptz")))
    if extra is not None:
        stmt = stmt.where(extra)
    return _P(stmt.order_by(bk.c.appointment_at.asc()))


_APPROVED_FUTURE = _approved_future()
_APPROVED_FUTURE_OF_DOCTOR = _approved_future(sa.or_(
    bk.c.assigned_doctor_id == _bp("medico_id", bk.c.assigned_doctor_id), bk.c.assigned_doctor_id.is_(None)))


def list_approved_future(desde=None, hasta=None):
    """Solicitudes aprobadas con turno_dt >= `desde` (default ahora) y,
    si se pasa, <= `hasta`, orden ascendente. Hospital-wide a propósito —
    para callers que de verdad recorren TODOS los médicos a la vez en un
    solo query batcheado (booking_requests.py `_approved_by_doctor_art`,
    slot_holds.py `_approved_future_slots`, reports.py `_capacity`,
    doctor_appointments.py `_doctors_stats`). Un caller de UN médico usa
    `list_approved_future_for_doctor` en cambio (auditoría 2026-08-06: el
    feed ICS externo de cada médico —sondeado por su calendario cada pocos
    minutos— pegaba este SELECT * hospital-wide para quedarse con las filas
    de un solo profesional)."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(_APPROVED_FUTURE.run(cur, desde=desde, hasta=hasta).fetchall(), "booking_requests")


def list_approved_future_for_doctor(medico_id, desde=None, hasta=None):
    """Igual que `list_approved_future` (mismos parámetros `desde`/`hasta`),
    pero acotada a UN médico: `assigned_doctor_id = medico_id` empujado al
    WHERE de SQL en vez de traer TODAS las solicitudes aprobadas del
    hospital para filtrar del lado de Python (auditoría 2026-08-06 — así
    trabajaba `doctor_appointments.appointments_of`, de donde salen el feed
    ICS de cada médico, "Mis turnos" y sus stats).

    También trae las filas SIN `assigned_doctor_id` resuelto todavía
    (legacy, o el operador guardó sólo el nombre libre — ver el comentario
    de esa columna en alembic/baseline/0001_schema.sql): el matcheo por nombre tolerante
    (orden invertido, apellido parcial — `doctor_appointments._belongs_to`/
    `_name_matches`) no se puede expresar en SQL, así que el caller lo
    sigue aplicando en Python — pero ya sobre este recorte (el médico
    pedido + lo que todavía no tiene id), nunca sobre el hospital entero."""
    if not medico_id:
        return []
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(_APPROVED_FUTURE_OF_DOCTOR.run(
            cur, desde=desde, hasta=hasta, medico_id=str(medico_id)).fetchall(), "booking_requests")


_TO_CONFIRM = _P(sa.select(bk).where(
    bk.c.status == _APROBADA, bk.c.appointment_at.is_not(None),
    bk.c.appointment_at > sa.func.coalesce(_bp("ahora", bk.c.appointment_at), sa.func.now()),
    bk.c.confirmation.is_(None),
    sa.or_(bk.c.dni == _bp("dni", bk.c.dni), bk.c.requester_dni == _bp("dni", bk.c.requester_dni)),
).order_by(bk.c.appointment_at.asc()).limit(1))


def booking_request_to_confirm(dni, ahora=None):
    """El turno aprobado más próximo de esa persona que todavía espera un
    "¿vas a venir?" — el que un SÍ/NO suelto por WhatsApp está contestando.

    Sólo turnos FUTUROS y sin confirmación previa: contestar dos veces cambia
    la respuesta (se re-consulta y devuelve el mismo turno sólo si se limpió),
    pero un "sí" suelto días después no puede tocar un turno ya pasado. Mira
    tanto a quien se atiende como a quien pidió el turno (una madre contesta
    por el turno del hijo desde SU teléfono)."""
    d = "".join(ch for ch in str(dni or "") if ch.isdigit())
    if not d:
        return None
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(_TO_CONFIRM.run(cur, dni=d, ahora=ahora).fetchone(), "booking_requests")


_MARK_CONFIRMATION = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(
    confirmation=_bp("valor", bk.c.confirmation), confirmation_at=sa.func.now(),
    confirmation_channel=_bp("canal", bk.c.confirmation_channel)).returning(*_ROW))


def mark_confirmation(sid, valor, canal=None):
    """Guarda el sí/no del paciente. Devuelve la fila actualizada, o None si no
    existe."""
    if valor not in ("si", "no"):
        raise ValueError("confirmacion inválida")
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(_MARK_CONFIRMATION.run(cur, sid=sid, valor=valor, canal=canal).fetchone(),
                        "booking_requests")


_TOKEN_OF = _P(sa.select(bk.c.confirm_token).where(bk.c.id == _bp("sid", bk.c.id)))
_SET_TOKEN = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(
    confirm_token=_bp("token", bk.c.confirm_token)).returning(bk.c.confirm_token))


def booking_request_confirm_token(sid):
    """El token del link "¿vas a poder venir?" de esa solicitud, creándolo la
    primera vez. Idempotente: si la fila ya tiene uno se devuelve ese —
    reenviar el recordatorio nunca invalida el link que el paciente ya tiene
    en la bandeja. Devuelve None si la solicitud no existe."""
    with db._connect() as conn, conn.cursor() as cur:
        fila = _TOKEN_OF.run(cur, sid=sid).fetchone()
        if fila is None:
            return None
        actual = (fila.get("confirm_token") or "").strip()
        if actual:
            return actual
        token = secrets.token_urlsafe(24)
        fila = _SET_TOKEN.run(cur, sid=sid, token=token).fetchone()
        return fila.get("confirm_token") if fila else None


_BY_TOKEN = _P(sa.select(bk).where(bk.c.confirm_token == _bp("token", bk.c.confirm_token)))


def booking_request_by_confirm_token(token):
    """La solicitud detrás de un token de confirmación, o None. El token ES la
    autenticación (llegó por email a la cuenta que pidió el turno), igual que
    appointment_offers.token."""
    t = str(token or "").strip()
    if not t:
        return None
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(_BY_TOKEN.run(cur, token=t).fetchone(), "booking_requests")


_HSI_PENDING = _P(sa.select(bk).where(
    bk.c.status.in_([_APROBADA, _CANCELADA]), bk.c.appointment_at.is_not(None),
    bk.c.appointment_at >= sa.func.coalesce(_bp("desde", bk.c.appointment_at), sa.func.now()),
    bk.c.hsi_status.in_([_lit("error"), _lit("pendiente")]),
).order_by(bk.c.appointment_at.asc()))


def booking_requests_hsi_pending(desde=None):
    """Aprobadas con turno futuro que NO quedaron impactadas en HSI
    (hsi_estado 'error' = HSI caído al approve, 'pendiente' = hsi_client
    ausente en ese momento). Fuente de hsi_resync.tick. Sólo turnos con
    turno_dt >= `desde` (default ahora): un turno pasado ya no se puede
    agendar en HSI y reintentaría para siempre. SELECT * porque
    solicitudes._push_hsi necesita la identidad completa del paciente."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(_HSI_PENDING.run(cur, desde=desde).fetchall(), "booking_requests")


_UPDATE_HSI = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(
    hsi_patient_id=sa.func.coalesce(_bp("patient_id", bk.c.hsi_patient_id), bk.c.hsi_patient_id),
    hsi_appointment_id=sa.func.coalesce(_bp("appointment_id", bk.c.hsi_appointment_id), bk.c.hsi_appointment_id),
    hsi_status=_bp("estado", bk.c.hsi_status)).returning(*_ROW))


def update_hsi(sid, hsi_result):
    """Registra un push a HSI que finalmente salió (hsi_resync reintentando
    una fila 'error'/'pendiente'). No toca estado ni turno_dt — sólo las
    columnas hsi_*. Devuelve el registro actualizado."""
    hsi_result = hsi_result or {}
    with db._connect() as conn, conn.cursor() as cur:
        row = db_wire._wire(_UPDATE_HSI.run(cur, sid=sid, patient_id=hsi_result.get("patient_id"),
                                       appointment_id=hsi_result.get("appointment_id"),
                                       estado=hsi_result.get("estado")).fetchone(), "booking_requests")
        _checkpoint(cur, hsi_result)
        return row


_REMIND_COLUMNS = (bk.c.id, bk.c.first_name, bk.c.last_name, bk.c.dni, bk.c.requester_dni, bk.c.service_id,
                   bk.c.center_slug, bk.c.contact, bk.c.appointment_at, bk.c.assigned_doctor,
                   bk.c.assigned_doctor_id, bk.c.ai_json)
_TO_REMIND = _P(sa.select(*_REMIND_COLUMNS, bk.c.confirm_token).where(
    bk.c.status == _APROBADA, bk.c.reminder_at.is_(None),
    db_values._local_date(bk.c.appointment_at) == sa.bindparam("fecha", type_=sa.Date)).order_by(bk.c.appointment_at))
_TO_REMIND_INTERVIEW = _P(sa.select(*_REMIND_COLUMNS).where(
    bk.c.status == _APROBADA, bk.c.interview_reminder_at.is_(None),
    db_values._local_date(bk.c.appointment_at) == sa.bindparam("fecha", type_=sa.Date)).order_by(bk.c.appointment_at))


def booking_requests_to_remind(fecha_art):
    """Solicitudes aprobadas cuyo turno cae en `fecha_art` (date, ya resuelta
    en America/Argentina/Buenos_Aires por el llamador) y todavía no se les
    mandó el recordatorio del día antes. `turno_dt` es timestamptz: SIEMPRE
    convertir a la zona del hospital antes de comparar la fecha — confiar en
    el offset crudo de la columna es el gotcha documentado en CLAUDE.md (el
    proceso puede correr con el TZ del sistema en otro huso).

    Trae `contact` y `confirm_token` porque el recordatorio pide confirmación:
    el teléfono es a dónde va el "¿vas a poder venir?" por WhatsApp
    (notifications._whatsapp_reminder lo leía de `contacto` y esta consulta no
    lo traía — el mensaje nunca salía, ni siquiera con proveedor configurado),
    y el token es el link de un clic del email."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(_TO_REMIND.run(cur, fecha=fecha_art).fetchall(), "booking_requests")


_MARK_REMINDER = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(reminder_at=sa.func.now()))
_CLAIM_REMINDER = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id), bk.c.reminder_at.is_(None)).values(
    reminder_at=sa.func.now()).returning(bk.c.id))
_MARK_REMINDER_SKIPPED = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(reminder_skipped=True))
_RELEASE = {c: _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values({c: None})) for c in ("reminder_at", "interview_reminder_at")}
_MARK_INTERVIEW_REMINDER = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(
    interview_reminder_at=sa.func.now()))
_CLAIM_INTERVIEW_REMINDER = _P(sa.update(bk).where(
    bk.c.id == _bp("sid", bk.c.id), bk.c.interview_reminder_at.is_(None)
).values(interview_reminder_at=sa.func.now()).returning(bk.c.id))


def mark_reminder(sid):
    """Marks the day-before reminder unconditionally (seeds and tests; the sweep claims with `claim_reminder`)."""
    with db._connect() as conn, conn.cursor() as cur:
        _MARK_REMINDER.run(cur, sid=sid)


def claim_reminder(sid):
    """Reclama el recordatorio del día antes ANTES de mandarlo: `UPDATE ...
    SET reminder_at=now() WHERE id=%s AND reminder_at IS NULL RETURNING id`.
    Dos ticks concurrentes (dos procesos, o un barrido solapado con el
    anterior) sólo dejan que UNO gane la fila — el otro ve `reminder_at` ya
    seteado y no manda un segundo mail. El reclamo mismo ES la marca, tomada ANTES de mandar; True si
    esta llamada lo ganó. Un envío que falla lo devuelve (`release_reminder`), uno sin nada que mandar
    lo conserva (`mark_reminder_skipped`)."""
    with db._connect() as conn, conn.cursor() as cur:
        return _CLAIM_REMINDER.run(cur, sid=sid).fetchone() is not None


def mark_reminder_skipped(sid):
    """Nothing to send: the claim stays, and the report does not count it as sent (the product owner's hub, 2026-11-10)."""
    with db._connect() as conn, conn.cursor() as cur:
        _MARK_REMINDER_SKIPPED.run(cur, sid=sid)


def release_reminder(sid, column="reminder_at"):
    """`column` (o `interview_reminder_at`) a NULL tras un envío fallido: el próximo barrido lo reintenta."""
    with db._connect() as conn, conn.cursor() as cur:
        _RELEASE[column].run(cur, sid=sid)


def booking_requests_to_remind_interview(fecha_art):
    """Candidatas al SEGUNDO mail del día antes (la entrevista previa,
    reminders.py:tick_interview) — mismo criterio de fecha que
    booking_requests_to_remind pero barrido independiente
    (interview_reminder_at, no reminder_at). Trae assigned_doctor_id (que el
    barrido del turno no necesita y por eso no lo trae) porque acá SÍ hace
    falta para el toggle por médico de intake_admin.enabled_for."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(_TO_REMIND_INTERVIEW.run(cur, fecha=fecha_art).fetchall(), "booking_requests")


def mark_interview_reminder(sid):
    """Best-effort, mismo contrato que mark_reminder: se marca SIEMPRE que se
    procesa la fila, salga o no el mail (módulo intake apagado, entrevista ya
    completada, cuenta sin email...) — nunca se re-escanea."""
    with db._connect() as conn, conn.cursor() as cur:
        _MARK_INTERVIEW_REMINDER.run(cur, sid=sid)


def claim_interview_reminder(sid):
    """Igual que `claim_reminder`, para el segundo mail del día antes (la
    invitación a la entrevista previa, `interview_reminder_at`) — reclama
    ANTES de mandar, para que un tick solapado no duplique el mail."""
    with db._connect() as conn, conn.cursor() as cur:
        return _CLAIM_INTERVIEW_REMINDER.run(cur, sid=sid).fetchone() is not None


_REJECT = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id), _in("desde", bk.c.status)).values(
    status=_RECHAZADA, rejection_reason=_bp("motivo", bk.c.rejection_reason),
    resolved_by=_bp("resolved_by", bk.c.resolved_by), resolved_at=sa.func.now()).returning(*_ROW))


def reject(sid, motivo, resuelta_por=None, desde=("pendiente",)):
    """Rechaza. Status-CAS, mismo contrato que `approve`: sólo pega si la fila
    SIGUE uno de los estados en `desde` (default 'pendiente'; puede incluir
    'aprobada' cuando el resolver rechaza conflictos HSI) — levanta
    `AlreadyProcessed` si otra corrida ya la resolvió primero, sin tocar nada.
    Devuelve el registro actualizado en el caso normal."""
    with db._connect() as conn, conn.cursor() as cur:
        previo = _lock_appointment_state(cur, sid)
        fila = _REJECT.run(cur, sid=sid, desde=list(desde), motivo=motivo, resolved_by=resuelta_por).fetchone()
        if fila is None:
            raise AlreadyProcessed(f"booking_requests id={sid} ya no estaba pendiente")
        _log_appointment_event(cur, sid, "rechazada", resuelta_por,
                                  previo.get("status"), fila["status"],
                                  previo.get("appointment_at"), fila["appointment_at"])
        return db_wire._wire(fila, "booking_requests")


_PENDING_HSI = sa.bindparam("pending_hsi", type_=sa.Boolean)
_CANCEL = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id), bk.c.status.in_(_ACTIVE_STATES)).values(
    status=_CANCELADA, cancelled_at=sa.func.now(),
    hsi_status=sa.case((sa.and_(_PENDING_HSI, bk.c.hsi_appointment_id.is_not(None)), "pendiente"),
                       else_=bk.c.hsi_status)).returning(*_ROW))
_SET_HSI_STATUS = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(
    hsi_status=_bp("hsi_status", bk.c.hsi_status)).returning(*_ROW))
_UNSETTLED_INTENT = _P(sa.select(sm.hsi_outbox.c.state).where(
    sm.hsi_outbox.c.entity_type == "appointment", sm.hsi_outbox.c.entity_id == _bp("entity", sm.hsi_outbox.c.entity_id),
    sm.hsi_outbox.c.state != "confirmed").order_by(sm.hsi_outbox.c.id.desc()).limit(1))
_HSI_STATUS_OF = _P(sa.select(bk.c.hsi_status).where(bk.c.id == _bp("sid", bk.c.id)))
_MOVE_EVENT = _P(sa.select(ae.c.id, ae.c.appointment_to).where(
    ae.c.request_id == _bp("sid", ae.c.request_id), ae.c.kind.in_(("reasignada", "aprobada", "restaurada"))
).order_by(ae.c.id.desc()).limit(1))


def _no_connector_hsi_status(cur, sid, current, when_absent="skip"):
    """Choose visible status under the booking lock without creating work.

    An existing linked intent still owns its uncertainty after selection is
    removed. Its acknowledgement also takes this booking lock, so a later
    acknowledgement cannot be overwritten by an older local-only decision."""
    intent = _UNSETTLED_INTENT.run(cur, entity=str(sid)).fetchone()
    if not intent:
        return when_absent
    if current in ("pendiente", "error"):
        return current
    return "error" if intent["state"] in ("error", "conflict") else "pendiente"


def _appointment_move_generation(cur, sid, appointment_at):
    """The committed local event for this slot, read under the booking lock."""
    event = _MOVE_EVENT.run(cur, sid=sid).fetchone()
    if event is None:
        # Pre-audit linked approvals may have no event at all. Zero is their
        # one stable baseline; the first real local move creates an event.
        return 0
    if event["appointment_to"] != appointment_at:
        raise ValueError("approved appointment has no matching move event")
    return event["id"]


def cancel(sid, actor=None, skip_hsi_without_connector=False, pending_hsi_with_connector=False):
    """El paciente cancela su propia solicitud (pendiente o aprobada con turno
    futuro — solicitudes.py._cancelar ya validó eso antes de llamar acá).
    `resuelta_por`/`resuelta_en` quedan intactos: esos campos son la auditoría
    de una decisión del HOSPITAL (approve/reject), no de esta acción del
    paciente. `cancelada_en` sí se sella acá: con cuánto aviso canceló es lo
    único que distingue "avisó a tiempo y el lugar se reofreció" de "avisó
    sobre la hora y se perdió igual" (patient_ranking.py). Quién canceló
    (`actor`: "paciente"/"solicitante") queda en `appointment_events`.
    Devuelve el registro actualizado."""
    with db._connect() as conn, conn.cursor() as cur:
        previo = _lock_appointment_state(cur, sid)
        fila = _CANCEL.run(cur, sid=sid, pending_hsi=bool(pending_hsi_with_connector)).fetchone()
        if fila is None:
            return None
        if skip_hsi_without_connector:
            visible = _no_connector_hsi_status(
                cur, sid, fila["hsi_status"],
                when_absent="skip" if fila["hsi_appointment_id"] else fila["hsi_status"])
            fila = _SET_HSI_STATUS.run(cur, sid=sid, hsi_status=visible).fetchone()
        _log_appointment_event(cur, sid, "cancelada", actor,
                                  previo.get("status"), fila["status"],
                                  previo.get("appointment_at"), fila["appointment_at"])
        return db_wire._wire(fila, "booking_requests")


_RESCHEDULE_PREFERENCE = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(
    time_preference=_bp("preferencia", bk.c.time_preference)).returning(*_ROW))


def reschedule_preference(sid, preferencia):
    """El paciente cambia el horario que prefiere en una solicitud todavía
    PENDIENTE (sin turno_dt propio — el hospital no la confirmó todavía):
    sólo actualiza el texto de preferencia, nada más que approve/reject
    tocan. Devuelve el registro actualizado."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(_RESCHEDULE_PREFERENCE.run(cur, sid=sid, preferencia=preferencia).fetchone(),
                        "booking_requests")


_REASSIGN = _P(sa.update(bk).where(bk.c.id == _bp("sid", bk.c.id)).values(
    appointment_at=_bp("appointment_at", bk.c.appointment_at),
    hsi_patient_id=sa.func.coalesce(_bp("patient_id", bk.c.hsi_patient_id), bk.c.hsi_patient_id),
    hsi_appointment_id=sa.func.coalesce(_bp("appointment_id", bk.c.hsi_appointment_id), bk.c.hsi_appointment_id),
    hsi_status=sa.func.coalesce(_bp("estado", bk.c.hsi_status), bk.c.hsi_status)).returning(*_ROW))


def reassign_appointment(sid, turno_dt, hsi_result=None, actor=None, slot_capacity=None, cur=None):
    """Reasigna el turno_dt de una solicitud YA aprobada a uno más cercano (el
    paciente aceptó una oferta de waitlist.py). Deja estado='aprobada' —
    a diferencia de `approve`, esto no es una aprobación nueva, es mover la
    fecha de una que ya lo estaba. `hsi_result` (opcional) = {estado,
    patient_id, appointment_id} de empujar el turno nuevo a HSI; con
    COALESCE contra los valores previos si no se pasa nada. Si el horario
    cambió, `appointment_events` guarda de cuál a cuál y quién (`actor`).
    `cur` (opcional) = el cursor de la transacción de quien llama: el diario
    de HSI mueve el horario que el EMR confirmó dentro de su propio acuse.
    Devuelve el registro actualizado."""
    hsi_result = hsi_result or {}
    import contextlib
    import psycopg
    try:
        with contextlib.ExitStack() as stack:
            if cur is None:
                conn = stack.enter_context(db._connect())
                cur = stack.enter_context(conn.cursor())
            previo = _lock_appointment_state(cur, sid)
            if not previo:
                return None
            if previo.get("status") != "aprobada":
                raise SlotConflict("reassign_appointment: request is no longer approved")
            _enforce_slot_capacity(cur, sid, slot_capacity, turno_dt, None, previo)
            hsi_status = hsi_result.get("estado")
            if hsi_status == "skip":
                hsi_status = _no_connector_hsi_status(
                    cur, sid, _HSI_STATUS_OF.run(cur, sid=sid).fetchone()["hsi_status"])
            fila = _REASSIGN.run(cur, sid=sid, appointment_at=db_values._coerce_dt(turno_dt),
                                 patient_id=hsi_result.get("patient_id"),
                                 appointment_id=hsi_result.get("appointment_id"),
                                 estado=hsi_status).fetchone()
            if fila is not None and fila["appointment_at"] != previo.get("appointment_at"):
                _log_appointment_event(cur, sid, "reasignada", actor,
                                          previo.get("status"), fila["status"],
                                          previo.get("appointment_at"), fila["appointment_at"])
            return db_wire._wire(fila, "booking_requests")
    except psycopg.errors.UniqueViolation as e:
        # Same guard as `approve`: moving an approved row onto a (doctor,
        # time) another approved row already holds.
        raise SlotConflict(f"reassign_appointment: {e}") from e


_BY_IDEM_KEY = _P(sa.select(bk).where(bk.c.idem_key == _bp("idem_key", bk.c.idem_key)))
_INSERT_LIMITED = _pg.insert(bk).values(
    dni=_bp("dni", bk.c.dni), first_name=_bp("first_name", bk.c.first_name),
    last_name=_bp("last_name", bk.c.last_name), birth_date=_bp("birth_date", bk.c.birth_date),
    sex=_bp("sex", bk.c.sex), contact=_bp("contact", bk.c.contact), service_id=_bp("service_id", bk.c.service_id),
    center_slug=_bp("center_slug", bk.c.center_slug), status=_PENDIENTE,
    referral_mime=_bp("referral_mime", bk.c.referral_mime),
    time_preference=_bp("time_preference", bk.c.time_preference),
    requester_dni=_bp("requester_dni", bk.c.requester_dni),
    slot_doctor_id=_bp("slot_doctor_id", bk.c.slot_doctor_id), idem_key=_bp("idem_key", bk.c.idem_key),
    patient_note=_bp("patient_note", bk.c.patient_note),
    requested_at=_bp("requested_at", bk.c.requested_at))
_INSERT_LIMITED = _P(_INSERT_LIMITED.on_conflict_do_nothing(
    index_elements=[bk.c.idem_key], index_where=bk.c.idem_key.is_not(None)).returning(*_ROW))


def create_booking_request_with_limit(dni, nombre, apellido, nacimiento, contacto,
                    servicio_id, centro_slug, limite, orden_mime=None, sexo=None,
                    preferencia_horario=None, solicitante_dni=None,
                    slot_medico_id=None, idem_key=None,
                    estados_activos=("pendiente", "aprobada"), actor=None,
                    nota_paciente=None, turno_pedido=None):
    """Como `create_booking_request`, pero cuenta-y-crea ATÓMICAMENTE bajo un
    `pg_advisory_xact_lock` scopeado a (dni, servicio_id): cierra la carrera
    de dos POSTs simultáneos del mismo paciente colándose los dos bajo el
    límite (el viejo camino — `booking_limits.check` contaba en una
    transacción, `create_booking_request` insertaba en otra — dejaba una
    ventana exacta para eso).

    `limite` = el número YA resuelto por `booking_limits.limit_for` (doctor →
    servicio → default del sitio). Se resuelve afuera porque necesita leer el
    catálogo (`catalog.services()`/`catalog.doctors()`), que no es
    responsabilidad de este módulo — este función sólo hace la parte
    atómica: contar+insertar bajo lock.

    `idem_key` (opcional, normalmente el `hold_token` del wizard): un submit
    repetido (doble tap, retry tras timeout) con la MISMA key nunca cuenta
    dos veces contra el límite ni crea una segunda fila — devuelve la fila
    que ya existía. `None`/vacío (clientes viejos sin token) se comporta
    exactamente como `create_booking_request`: sin dedup, cada llamada crea
    una fila.

    Devuelve la fila creada (o la existente, si `idem_key` se repitió —
    mismo dict, MÁS la clave interna `_ya_existia` (bool, sólo presente
    cuando se pasó `idem_key`): True si esta llamada encontró una fila que
    YA existía (no insertó nada), False si fue la que insertó. El caller
    (`booking_requests._create`) la necesita para distinguir "gané la
    carrera de inserción" de "perdí y me devolvieron la fila del ganador" —
    ambas vuelven con `estado='pendiente'` cuando el servicio no
    auto-confirma, así que comparar contra el estado NO alcanza (2026-08-05
    tier 14 fix, finding B del plan de integridad: sin esto, cada perdedor
    de una carrera de 8 hilos re-ejecutaba el resto de `_create` — IA,
    archivo de orden, notificación — sobre la MISMA fila). Levanta
    `LimitReached` si el paciente ya está en el límite — el caller la atrapa
    para el 409 "límite_turnos_activos" existente (mismo `.limite`/
    `.abiertas` que ya arma ese mensaje)."""
    idem_key = (idem_key or "").strip() or None
    with db._connect() as conn, conn.cursor() as cur:
        if idem_key:
            # Fast path: a repeated submit must never re-count against the
            # limit, even if the limit is now full — the row it's re-fetching
            # is itself one of the "abiertas" being counted.
            existente = _BY_IDEM_KEY.run(cur, idem_key=idem_key).fetchone()
            if existente is not None:
                fila = db_wire._wire(existente, "booking_requests")
                fila["_ya_existia"] = True
                return fila
        # Advisory lock scoped to (dni, servicio_id): serializes concurrent
        # creates for the SAME patient+service so the count below can't race
        # with another create seeing the same "not yet inserted" count.
        # hashtext() returns int4; cast to bigint for the single-key
        # pg_advisory_xact_lock(bigint) overload. Auto-released at the end of
        # this transaction (commit or rollback) — no unlock call needed.
        _xact_lock(cur, f"booklimit:{dni}:{servicio_id}")
        if idem_key:
            # Re-check AFTER acquiring the lock (2026-08-05 tier 14 fix): the
            # fast-path SELECT above ran BEFORE the lock, so a sibling call
            # holding the SAME (dni, servicio_id) lock right now — with the
            # SAME idem_key — may have committed its insert while we were
            # queued waiting for it. Without this, the count just below would
            # see that sibling's row as one more "abierta" against OURSELVES
            # and raise `LimitReached` for a retry that should always
            # succeed, limit or no limit (reproduced: 8 threads hammering a
            # limite=1 service with the same idem_key, some lost the lock
            # queue and got a spurious LimitReached here).
            existente = _BY_IDEM_KEY.run(cur, idem_key=idem_key).fetchone()
            if existente is not None:
                fila = db_wire._wire(existente, "booking_requests")
                fila["_ya_existia"] = True
                return fila
        estados = tuple(estados_activos or ())
        abiertas = db_slots._count_active_requests_cur(cur, dni, servicio_id, estados) if estados else 0
        if abiertas >= limite:
            raise LimitReached(limite, abiertas)
        fila = _INSERT_LIMITED.run(
            cur, dni=dni, first_name=nombre, last_name=apellido, birth_date=db_values._coerce_date(nacimiento),
            sex=sexo, contact=contacto, service_id=servicio_id, center_slug=centro_slug,
            referral_mime=orden_mime, time_preference=preferencia_horario,
            requester_dni=solicitante_dni or dni, slot_doctor_id=slot_medico_id, idem_key=idem_key,
            patient_note=nota_paciente, requested_at=turno_pedido).fetchone()
        creada_ahora = fila is not None
        if creada_ahora:
            _log_appointment_event(cur, fila["id"], "creada", actor,
                                      None, "pendiente", None, None)
        if fila is None:
            # idem_key: another concurrent request with the SAME key won the
            # race between our fast-path SELECT above and this INSERT.
            fila = _BY_IDEM_KEY.run(cur, idem_key=idem_key).fetchone()
        wired = db_wire._wire(fila, "booking_requests")
        if idem_key:
            wired["_ya_existia"] = not creada_ahora
        return wired


# Sin filtro de estado, la cola del admin usada mostrar la tabla ENTERA sin
# LIMIT -- con meses de uso, miles de filas en cada carga de /api/admin/requests
# (auditoría 2026-08-06). El default ahora es la vista ACCIONABLE: TODAS las
# pendientes (nunca se recortan -- son justo las que hay que resolver) + una
# ventana reciente de resueltas, para que "Solicitudes" siga mostrando el
# historial de los últimos días sin cargar el histórico completo. Un `estado`
# explícito (una pestaña puntual, ej. "aprobada") sigue devolviendo SÓLO ese
# estado, también acotado por LIMIT/OFFSET -- ninguna pestaña vuelve a traer
# la tabla entera. `count_admin` da el total sin el LIMIT, para el "N de M".
_ADMIN_LIST_DEFAULT_LIMIT = 300


_ADMIN_LIST_RESOLVED_WINDOW_DAYS = 30


class AlreadyProcessed(Exception):
    """CAS perdido en approve()/reject(): la solicitud ya no estaba
    'pendiente' cuando la UPDATE corrió (otro approve/reject la resolvió
    primero, en paralelo — ej. dos clicks "Aprobar" del mismo operador en dos
    pestañas, o cola + panel resolviendo la misma fila a la vez). Cero
    efectos secundarios: la UPDATE no matcheó ninguna fila, no se tocó nada."""


class SlotConflict(Exception):
    """`approve()` perdió la carrera contra `booking_requests_doctor_slot_uniq`:
    otra solicitud ganó ese (médico, horario) en el mismo instante. La
    transacción de ESTE approve se revirtió por completo (ni el UPDATE ni el
    insert de hsi_checkpoints quedaron aplicados)."""


class OverbookingCapacityReached(Exception):
    """A percentage overbooking block already used its shared daily budget."""


class LimitReached(Exception):
    """`create_booking_request_with_limit` no creó nada: el paciente ya tiene
    `abiertas` solicitudes activas de este servicio, que es `limite`. Sin
    efecto (no se insertó fila, el advisory lock se liberó al terminar la
    transacción). `.abiertas` / `.limite` quedan en la excepción para que el
    caller arme el mensaje sin una segunda consulta."""

    def __init__(self, limite, abiertas):
        self.limite = limite
        self.abiertas = abiertas
        super().__init__(f"límite de turnos activos alcanzado: {abiertas}/{limite}")
