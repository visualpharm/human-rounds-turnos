"""core/db_reports.py — the aggregations behind the admin panel's «Reportes»: management (requests by
service / status / day, medians, rejections, the CSV rows), patients (accounts, coverage, ages,
dependents, activity by month), the «insights» (holds by day and hour, time preference, funnel,
offers, PUCO hits), cost recovery / SUMAR+ (completed consultations), the weekly demand series and
the statistics rows.

Split out of `core/db.py` in stage 6 of the SQLAlchemy Core move (docs/architecture/sqlalchemy-core.md):
the statements are Core, built from `core/schema_model.py`. `create_appointment_offer`, which lives
among them in the old file, is a write of the waitlist and stays in `core/db.py`.

Callers write `db_reports.<name>(...)`: `core/db.py` re-exports nothing of this module (TASK-57,
`tests/test_db_domain_modules.py`). The functions reach the connection as `db._connect()`, the layers below as
`db_wire._wire_all` / `db_values._days_ago` and a sibling as `db_x.<name>`, all at CALL time.

The contract of the whole module (unchanged): the functions do NOT catch exceptions — a database that is
down raises `DbUnavailable` from `_connect()` and `admin_panel/reports.py` turns it into a 503 — and over
an empty table they return zeros / None / [] (`percentile_cont` of an empty set is NULL, never an error).
"""

import sqlalchemy as sa

from app.core import db
from app.core import db_wire
from app.core import db_values
from app.core import schema_model as sm
from app.core import sqlcore


_br = sm.booking_requests
_users = sm.users


def _lit(text):
    """A SQL constant (`'aprobada'`, `'month'`) that stays in the statement as a literal, as it was in
    the hand-written text."""
    return sa.literal_column("'%s'" % text)


def _status(value):
    return _br.c.status == _lit(value)


def _in_window(column, dias):
    """`<column> > now() - <dias> * interval '1 day'`."""
    return column > db_values._days_ago(dias)


def _span(delta, divisor):
    """`EXTRACT(EPOCH FROM <delta>) / <divisor>`: a duration in hours (3600.0) or days (86400.0).
    The division is spelled with `op("/")`: Core's own `/` on numeric operands adds a
    `CAST(<divisor> AS NUMERIC)` that the hand-written statement never had."""
    return sa.extract("epoch", delta).op("/")(sa.literal_column(divisor))


def _median(expr):
    """`percentile_cont(0.5) WITHIN GROUP (ORDER BY <expr>)`."""
    return sa.func.percentile_cont(sa.literal_column("0.5")).within_group(expr)


def _count_where(cond):
    return sa.func.count().filter(cond)


def _local(column):
    """`<column> AT TIME ZONE '<installation tz>'` (the zone is an inlined literal, see `db_values._local_date`)."""
    return sqlcore.at_zone(column, db_values._ART_SQL)


def _month_start(timestamp):
    """`date_trunc('month', <timestamp>)`."""
    return sa.func.date_trunc(_lit("month"), timestamp)


# ---- reportes de gestión ----------------------------------------------------------------

# Who a turno that never waited for an answer is signed by: the desk and the doctor load it already
# approved (`mesa:<user>` / `medico:<user>`, admin_panel/admin_appointments.py) and a request confirmed on
# the spot is `auto:web` (modules/turnos/booking_create.py). A request answered from the queue carries the
# operator's plain username.
_BORN_APPROVED_BY = ("mesa:", "medico:", "auto:")


def _born_approved(c):
    quien = sa.func.coalesce(c.resolved_by, _lit(""))
    return sa.or_(*[sa.func.starts_with(quien, _lit(prefijo)) for prefijo in _BORN_APPROVED_BY])


def reports_kpis(dias=30):
    """Pendientes ahora (no depende del período) + resumen del período:
    total de solicitudes, % aprobadas sobre resueltas, mediana de horas
    solicitud->respuesta (resuelta_en; quedan afuera las filas viejas sin
    ese campo y los turnos que nacieron aprobados, `_born_approved`: nadie
    esperó una respuesta) y mediana de días creada->turno asignado
    (aprobadas con turno_dt)."""
    c = _br.c
    pendiente_wait = _span(sa.func.now() - c.created, "3600.0")
    with db._connect() as conn, conn.cursor() as cur:
        pendientes = sqlcore.run(cur, sa.select(sa.func.count().label("n")).where(
            _status("pendiente"))).fetchone()["n"]

        espera_pendiente = sqlcore.run(cur, sa.select(
            _median(pendiente_wait).label("mediana"), sa.func.max(pendiente_wait).label("maxima"),
        ).where(_status("pendiente"))).fetchone()

        # La lista completa de esperas (horas, una por solicitud pendiente) para
        # graficar la distribución real, no sólo la mediana. La cola pendiente es
        # chica por diseño (se resuelve a diario); tope defensivo por si alguna
        # vez no lo es. Sin PII: son duraciones sueltas, sin id ni orden estable
        # que permita mapearlas a una solicitud puntual.
        h = pendiente_wait.label("h")
        esperas_pendientes = [round(float(r["h"]), 2) for r in sqlcore.run(
            cur, sa.select(h).where(_status("pendiente")).order_by(h).limit(sa.literal_column("500"))).fetchall()]

        row = sqlcore.run(cur, sa.select(
            sa.func.count().label("total"),
            _count_where(_status("aprobada")).label("aprobadas"),
            _count_where(c.status.in_([_lit("aprobada"), _lit("rechazada")])).label("resueltas"),
        ).where(_in_window(c.created, dias))).fetchone()
        total = row["total"] or 0
        resueltas = row["resueltas"] or 0
        pct_aprobadas = round(100.0 * row["aprobadas"] / resueltas, 1) if resueltas else None

        mh = sqlcore.run(cur, sa.select(
            _median(_span(c.resolved_at - c.created, "3600.0")).label("mediana"),
        ).where(c.resolved_at.is_not(None), sa.not_(_born_approved(c)),
                _in_window(c.created, dias))).fetchone()["mediana"]

        md = sqlcore.run(cur, sa.select(
            _median(_span(c.appointment_at - c.created, "86400.0")).label("mediana"),
        ).where(_status("aprobada"), c.appointment_at.is_not(None),
                _in_window(c.created, dias))).fetchone()["mediana"]

        return {
            "pendientes": pendientes,
            "solicitudes_periodo": total,
            "pct_aprobadas": pct_aprobadas,
            "mediana_horas_resolucion": round(float(mh), 1) if mh is not None else None,
            "mediana_dias_turno": round(float(md), 1) if md is not None else None,
            "mediana_horas_pendiente": (round(float(espera_pendiente["mediana"]), 1)
                                         if espera_pendiente["mediana"] is not None else None),
            "max_horas_pendiente": (round(float(espera_pendiente["maxima"]), 1)
                                     if espera_pendiente["maxima"] is not None else None),
            "esperas_pendientes_horas": esperas_pendientes,
        }


def reports_booking_requests_by_service(dias=30):
    """[(servicio_id, cantidad)] solicitudes creadas en el período, agrupadas
    por servicio — reports.py las reagrupa por especialidad (cluster) contra
    el catálogo vivo."""
    c = _br.c
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(c.service_id, sa.func.count().label("cantidad"))
                           .where(_in_window(c.created, dias)).group_by(c.service_id)).fetchall()
        return [(r["service_id"], r["cantidad"]) for r in rows]


def reports_approved_appointment(dias=30):
    """[(servicio_id, created, turno_dt)] de aprobadas con turno asignado,
    creadas en el período. Crudo a propósito: reports.py agrupa por
    especialidad y calcula UNA mediana sobre el conjunto agrupado — promediar
    medianas ya calculadas por servicio daría un número incorrecto."""
    c = _br.c
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(c.service_id, c.created, c.appointment_at).where(
            _status("aprobada"), c.appointment_at.is_not(None), _in_window(c.created, dias))).fetchall()
        return [(r["service_id"], r["created"], r["appointment_at"]) for r in rows]


def reports_rejections(dias=30):
    """[(motivo_rechazo, cantidad)] del período — reports.py agrupa lo que
    no matchea la lista estándar de motivos (admin.html:MOTIVOS_STD) bajo
    'Otro motivo'."""
    c = _br.c
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(c.rejection_reason, sa.func.count().label("cantidad")).where(
            _status("rechazada"), _in_window(c.created, dias)).group_by(c.rejection_reason)).fetchall()
        return [(r["rejection_reason"], r["cantidad"]) for r in rows]


def reports_volume(dias=30):
    """[{fecha, total, aprobadas, rechazadas, pendientes}] por día del
    período, agrupado en hora de Buenos Aires fija -03:00 (mismo criterio que
    calendar_feed.py: nunca confiar en el TZ del proceso). `aprobadas` son las
    que alguna vez tuvieron turno (_ever_approved, el paso «Les dieron turno»);
    rechazadas y pendientes, por el estado ACTUAL de cada solicitud."""
    c = _br.c
    dia = db_values._local_date(c.created).label("dia")
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            dia, sa.func.count().label("total"),
            _count_where(_ever_approved(c)).label("aprobadas"),
            _count_where(_status("rechazada")).label("rechazadas"),
            _count_where(_status("pendiente")).label("pendientes"),
        ).where(_in_window(c.created, dias)).group_by(dia).order_by(dia)).fetchall()
        return [
            {"fecha": r["dia"].isoformat(), "total": r["total"], "aprobadas": r["aprobadas"],
             "rechazadas": r["rechazadas"], "pendientes": r["pendientes"]}
            for r in rows
        ]


def reports_export_rows(dias=90):
    """Filas crudas para el CSV de gestión (GET /api/admin/reports/
    solicitudes.csv). Trae nombre/apellido SOLO para que reports.py derive
    iniciales — el CSV nunca expone el nombre completo del paciente (export
    de gestión, no un registro clínico)."""
    c = _br.c
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            c.created, c.service_id, c.center_slug, c.status, c.rejection_reason,
            c.resolved_by, c.resolved_at, c.appointment_at, c.first_name, c.last_name,
        ).where(_in_window(c.created, dias)).order_by(c.created.desc())).fetchall()
        return db_wire._wire_all(rows, "booking_requests")


# ---- reportes de pacientes ---------------------------------------------------------------
#
# Agregaciones sobre `users`/`dependents`/`booking_requests` para el sub-reporte "Pacientes".
# NINGUNA de estas funciones devuelve nombre/apellido/DNI — el nombre completo y el DNI son PII
# clínica que este reporte no necesita (a diferencia del CSV de gestión, que sólo expone
# iniciales). Las funciones que tocan `coverage` devuelven el jsonb crudo sin el DNI de esa fila —
# reports.py lo normaliza y cuenta en agregado, nunca por persona.

def reports_patients_kpis(dias=30):
    """Total de cuentas (todas), nuevas en el período, y qué porcentaje tiene
    email/domicilio cargado (ninguno de los dos es obligatorio en el
    registro)."""
    c = _users.c
    with db._connect() as conn, conn.cursor() as cur:
        r = sqlcore.run(cur, sa.select(
            sa.func.count().label("total"),
            _count_where(_in_window(c.created, dias)).label("nuevas"),
            _count_where(sa.and_(c.email.is_not(None), c.email != sa.literal_column("''"))).label("con_email"),
            _count_where(c.address.is_not(None)).label("con_domicilio"),
        )).fetchone()
        total = r["total"] or 0
        return {
            "total_cuentas": total,
            "nuevas_periodo": r["nuevas"] or 0,
            "pct_con_email": round(100.0 * r["con_email"] / total, 1) if total else None,
            "pct_con_domicilio": round(100.0 * r["con_domicilio"] / total, 1) if total else None,
        }


def reports_patients_volume(dias=30):
    """[{fecha, total}] de cuentas NUEVAS por día del período, agrupado en
    hora de Buenos Aires fija (mismo criterio que reports_volume)."""
    c = _users.c
    dia = db_values._local_date(c.created).label("dia")
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(dia, sa.func.count().label("total")).where(
            _in_window(c.created, dias)).group_by(dia).order_by(dia)).fetchall()
        return [{"fecha": r["dia"].isoformat(), "total": r["total"]} for r in rows]


def reports_patients_active_months(meses=12):
    """Pacientes distintos que hicieron al menos una solicitud, por mes.
    Completa meses sin actividad con cero para que la serie siempre tenga los
    12 puntos necesarios para leer una tendencia honesta."""
    c = _br.c
    this_month = _month_start(_local(sa.func.now()))
    first_month = this_month - (sa.literal(meses, sa.Integer) - sa.literal_column("1")) * sa.literal_column(
        "interval '1 month'", sa.Interval)
    month = sa.literal_column("interval '1 month'", sa.Interval)
    meses_cte = sa.select(
        sa.cast(sa.func.generate_series(first_month, this_month, month), sa.Date).label("mes")
    ).cte("meses")
    actividad = sa.select(
        sa.cast(_month_start(_local(c.created)), sa.Date).label("mes"),
        sa.func.count(sa.distinct(c.dni)).label("pacientes"),
    ).where(c.created >= first_month).group_by(sa.literal_column("1")).cte("actividad")
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            meses_cte.c.mes, sa.func.coalesce(actividad.c.pacientes, sa.literal_column("0")).label("pacientes"),
        ).select_from(meses_cte.outerjoin(actividad, meses_cte.c.mes == actividad.c.mes))
         .order_by(meses_cte.c.mes)).fetchall()
        return [{"mes": r["mes"].isoformat(), "pacientes": r["pacientes"] or 0} for r in rows]


def reports_patients_coverage():
    """[cobertura, ...] jsonb de TODAS las cuentas con cobertura cargada — sin
    el DNI de esa fila. reports.py normaliza las dos formas que puede tener
    el jsonb (la nueva `{obras_sociales:[...], prepaga:{...}}` y la singular
    pre-2026-07-16 `{obra_social:{...}, prepaga:"texto"}`, que quedó tal cual
    en las filas escritas antes de esa fecha — nunca se migró en el lugar) y
    cuenta en agregado."""
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(_users.c.coverage).where(_users.c.coverage.is_not(None))).fetchall()
        return [r["coverage"] for r in rows]


def reports_patients_ages():
    """[(nacimiento, sexo), ...] de las cuentas con fecha de nacimiento
    cargada — sin nombre ni DNI (sexo puede ser None). reports.py calcula la
    edad y arma los buckets por sexo para la pirámide poblacional (evita
    duplicar en SQL la lógica de "hoy" que ya vive en Python)."""
    c = _users.c
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(c.birth_date, c.sex).where(c.birth_date.is_not(None))).fetchall()
        return [(r["birth_date"], r["sex"]) for r in rows]


def reports_patients_dependents():
    """Cuántos menores/personas a cargo hay declarados en total, y cuántas
    cuentas adultas declararon al menos uno."""
    with db._connect() as conn, conn.cursor() as cur:
        r = sqlcore.run(cur, sa.select(
            sa.func.count().label("n"),
            sa.func.count(sa.distinct(sm.dependents.c.adult_dni)).label("con_dep"),
        )).fetchone()
        return {"total": r["n"] or 0, "adultos_con_dependientes": r["con_dep"] or 0}


def reports_patients_booking_requests_by_patient(dias=30):
    """[cantidad, ...] de solicitudes del período agrupadas por
    `solicitante_dni` — SIN el DNI (sólo la cantidad de cada grupo).
    reports.py cuenta cuántos pacientes distintos pidieron turno y el
    promedio de solicitudes por paciente activo; el DNI en sí nunca sale de
    esta función."""
    c = _br.c
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(sa.func.count().label("n")).where(
            _in_window(c.created, dias), c.requester_dni.is_not(None)).group_by(c.requester_dni)).fetchall()
        return [r["n"] for r in rows]


# ---- reportes "insights" (2026-07-20: rework story-driven de Reportes) ---------------------

def reports_median_wait_prior(dias=30):
    """Mediana de días creada->turno_dt del período INMEDIATAMENTE ANTERIOR al
    actual (mismo largo `dias`, desplazado una vez hacia atrás) — permite que
    el hero narrativo diga si la espera bajó o subió respecto de antes, no
    sólo el valor absoluto del período actual (que ya da reports_kpis)."""
    c = _br.c
    with db._connect() as conn, conn.cursor() as cur:
        m = sqlcore.run(cur, sa.select(
            _median(_span(c.appointment_at - c.created, "86400.0")).label("mediana"),
        ).where(_status("aprobada"), c.appointment_at.is_not(None),
                c.created <= db_values._days_ago(dias), _in_window(c.created, 2 * dias))).fetchone()["mediana"]
        return round(float(m), 1) if m is not None else None


def reports_holds_day_hour(dias=30):
    """[(dia, hora)] de TODOS los turno_holds creados en el período (soft-lock
    de slot_holds.py) — tomados o no, un hold es el momento exacto en que
    alguien ELIGIÓ ese día/hora en el wizard (nunca por sólo mirar la
    grilla), así que es la señal de demanda más fina que hay: mejor que
    turno_dt (que sólo existe para las aprobadas) porque también captura lo
    que la gente quiso y no llegó a convertirse en solicitud."""
    c = sm.slot_holds.c
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            sa.func.to_char(c.day, _lit("YYYY-MM-DD")).label("day"),
            sa.func.to_char(c.time, _lit("HH24:MI")).label("time"),
        ).where(_in_window(c.created, dias))).fetchall()
        return [(r["day"], r["time"]) for r in rows]


def reports_time_preference(dias=30):
    """[(preferencia_horario, created)] de solicitudes del período que
    declararon una preferencia de franja pero NO tienen turno_dt (ya sea
    porque el servicio no tiene agenda online — solicitud con orden — o
    porque todavía no fue aprobada): estas son la única fuente de demanda
    horaria para esas solicitudes (turno_dt/turno_holds ya cubren las que sí
    eligieron un horario concreto). Evita contar la misma solicitud dos
    veces en el heatmap de demanda."""
    c = _br.c
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(c.time_preference, c.created).where(
            _in_window(c.created, dias), c.time_preference.is_not(None),
            c.appointment_at.is_(None))).fetchall()
        return [(r["time_preference"], r["created"]) for r in rows]


def reports_funnel(dias=30):
    """Embudo de gestión del período: solicitudes por estado final, asistencia
    de las aprobadas, recordatorios efectivamente marcados (recordatorio_dt
    seteado — un envío que falla devuelve la marca, también el último intento
    del día (2026-11-10), así que un recordatorio que nunca salió no cuenta;
    tampoco cuenta el que no tenía nada que mandar — sin email ni teléfono, o
    los canales apagados por configuración: queda marcado `reminder_skipped`),
    y conversión de
    holds (soft-lock) en solicitudes reales. reports.py arma el payload
    'funnel' con esto (+ reports_appointment_offers, que vive en su propia
    tabla)."""
    c = _br.c
    aprobada = _status("aprobada")
    holds = sm.slot_holds.c
    with db._connect() as conn, conn.cursor() as cur:
        s = sqlcore.run(cur, sa.select(
            sa.func.count().label("total"),
            _count_where(aprobada).label("aprobadas"),
            _count_where(_status("rechazada")).label("rechazadas"),
            _count_where(_status("cancelada")).label("canceladas"),
            _count_where(_status("pendiente")).label("pendientes"),
            _count_where(sa.and_(aprobada, c.attendance == _lit("presente"))).label("presente"),
            _count_where(sa.and_(aprobada, c.attendance == _lit("ausente"))).label("ausente"),
            _count_where(sa.and_(aprobada, c.attendance.is_(None))).label("sin_dato"),
            _count_where(sa.and_(c.reminder_at.is_not(None), c.reminder_skipped.is_(False))).label("recordatorios"),
        ).where(_in_window(c.created, dias))).fetchone()
        h = sqlcore.run(cur, sa.select(
            sa.func.count().label("creados"),
            _count_where(holds.request_id.is_not(None)).label("convertidos"),
        ).where(_in_window(holds.created, dias))).fetchone()
        return {
            "solicitudes": s["total"] or 0,
            "aprobadas": s["aprobadas"] or 0,
            "rechazadas": s["rechazadas"] or 0,
            "canceladas": s["canceladas"] or 0,
            "pendientes": s["pendientes"] or 0,
            "presente": s["presente"] or 0,
            "ausente": s["ausente"] or 0,
            "sin_dato_asistencia": s["sin_dato"] or 0,
            "recordatorios_enviados": s["recordatorios"] or 0,
            "holds_creados": h["creados"] or 0,
            "holds_convertidos_en_solicitud": h["convertidos"] or 0,
        }


def _path_filters(dias, centro=None, servicio=None, medico=None):
    """The cohort of the «Recorrido» report: requests created in the last `dias` days, narrowed by
    center, service and doctor (the assigned one, else the one whose slot the patient picked)."""
    c = _br.c
    conds = [_in_window(c.created, dias)]
    if centro:
        conds.append(c.center_slug == str(centro))
    if servicio:
        conds.append(c.service_id == str(servicio))
    if medico:
        conds.append(sa.func.coalesce(c.assigned_doctor_id, c.slot_doctor_id) == str(medico))
    return conds


def _ever_approved(c):
    """A request that was given an appointment: approved now, or approved/restored at some point (an
    approval later cancelled still counts). One definition for the step «Les dieron turno» of Recorrido,
    the Gestión funnel and its daily series (critical review 08.11, E7)."""
    ev = sm.appointment_events.c
    return sa.or_(_status("aprobada"), sa.exists().where(
        ev.request_id == c.id, ev.kind.in_([_lit("aprobada"), _lit("restaurada")])))


# Days after which a still-pending request counts as hung («colgada»): the SQL interval of
# `reports_patient_path` and the `{dias}` of the report's phrase (`admin_panel/reports_funnel.py`).
COLGADA_DIAS = 30


def reports_patient_path(dias=30, centro=None, servicio=None, medico=None):
    """How far each request of the period got, as counts only (never a DNI, name or id).

    One row per combination of what the database records about a request: its status, attendance and
    confirmation, whether it was ever approved (`status = 'aprobada'` or an «aprobada»/«restaurada» event
    in `appointment_events` — a request approved and later cancelled is still one that got a turno) and
    whether its appointment time is already past. `admin_panel/reports_funnel.py` folds these rows into
    the steps the admin chose, in the order they chose; the set of rows is small (a few dozen at most)."""
    c = _br.c
    aprobada = _ever_approved(c)
    paso = sa.and_(c.appointment_at.is_not(None), c.appointment_at < sa.func.now())
    # A pending request older than a month hung: the report names it apart from the ones still waiting.
    colgada = sa.and_(c.status == _lit("pendiente"),
                      c.created < sa.func.now() - sa.text("interval '%d days'" % COLGADA_DIAS))
    inner = sa.select(
        c.status.label("status"), c.attendance.label("attendance"), c.confirmation.label("confirmation"),
        aprobada.label("aprobada"), paso.label("paso"), colgada.label("colgada"),
    ).where(*_path_filters(dias, centro, servicio, medico)).subquery()
    k = inner.c
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            k.status, k.attendance, k.confirmation, k.aprobada, k.paso, k.colgada, sa.func.count().label("n"),
        ).group_by(k.status, k.attendance, k.confirmation, k.aprobada, k.paso, k.colgada)).fetchall()
        return [{
            "status": r["status"], "attendance": r["attendance"], "confirmation": r["confirmation"],
            "aprobada": bool(r["aprobada"]), "paso": bool(r["paso"]), "colgada": bool(r["colgada"]),
            "n": int(r["n"] or 0),
        } for r in rows]


def reports_patient_path_options(dias=30):
    """[{centro, servicio, medico, n}]: the requests of the period by center, service and doctor, for the
    report's filter lists (ids only; the names come from the catalog)."""
    c = _br.c
    medico = sa.func.coalesce(c.assigned_doctor_id, c.slot_doctor_id).label("medico")
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            c.center_slug.label("centro"), c.service_id.label("servicio"), medico, sa.func.count().label("n"),
        ).where(_in_window(c.created, dias)).group_by(c.center_slug, c.service_id, medico)).fetchall()
        return [{"centro": r["centro"], "servicio": r["servicio"], "medico": r["medico"], "n": int(r["n"] or 0)}
                for r in rows]


def reports_appointment_offers(dias=30):
    """[(estado, cantidad)] de `ofertas_turno` (waitlist.py) CREADAS en el
    período, agrupadas por estado tal cual vive en la tabla ('ofrecida' |
    'aceptada' | 'expirada' | 'vacante'). OJO: waitlist.reject_offer
    también deja la fila en 'expirada' — el esquema no distingue un rechazo
    explícito ("no lo quiero") de un vencimiento por tiempo. reports.py
    documenta esa limitación en el payload en vez de inventar una
    distinción que la base no tiene."""
    c = sm.appointment_offers.c
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(c.status, sa.func.count().label("cantidad")).where(
            _in_window(c.created, dias)).group_by(c.status)).fetchall()
        return [(r["status"], r["cantidad"]) for r in rows]


def reports_puco_found(dias=30):
    """[cobertura, ...] jsonb de cuentas NUEVAS del período que tienen
    cobertura cargada (sin el DNI de esa fila) — reports.py cuenta cuántas
    tienen al menos una entrada con origen='puco' (el padrón SISA encontró
    algo al registrarse, ver puco.py) como proxy de cuántas veces esa
    consulta le ahorró tipear la obra social a mano al operador/paciente
    (ver 'ahorro' en reports.py). Misma disciplina de privacidad que
    reports_patients_coverage: nunca el DNI de la fila."""
    c = _users.c
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(c.coverage).where(
            _in_window(c.created, dias), c.coverage.is_not(None))).fetchall()
        return [r["coverage"] for r in rows]


# ---- reportes nuevos (2026-07-24: recupero de costos, SUMAR+, estadística, capacidad estacional) ----

def reports_consultations_completed(dias=90):
    """Filas crudas de consultas YA REALIZADAS (turno_dt en el pasado) dentro
    de los últimos `dias` días — ventana sobre `turno_dt`, no sobre
    `created`: "Recupero de costos" y "SUMAR+" facturan/cuentan por consulta
    ATENDIDA, no por solicitud creada. LEFT JOIN con `usuarios.cobertura` por
    dni: una fila sin cuenta propia (dependiente que nunca se registró solo,
    o un dni que no matchea ninguna cuenta) trae cobertura=None, que
    reports.py trata igual que una cuenta con cobertura vacía — "sin
    cobertura declarada" no distingue el motivo. Alimenta DOS sub-reportes
    con la MISMA query (reports.py hace dos lecturas distintas de las
    mismas filas): "Recupero de costos" (consultas CON obra social/prepaga)
    y "SUMAR+" (población SIN ninguna cobertura declarada, candidata al
    programa)."""
    s = _br.alias("s")
    u = _users.alias("u")
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(
            s.c.id, s.c.created, s.c.dni, s.c.first_name.label("nombre"), s.c.last_name.label("apellido"),
            s.c.service_id.label("servicio_id"), s.c.center_slug.label("centro_slug"),
            s.c.appointment_at.label("turno_dt"), s.c.attendance.label("asistencia"),
            s.c.birth_date.label("nacimiento"), u.c.coverage.label("cobertura"),
        ).select_from(s.outerjoin(u, u.c.dni == s.c.dni)).where(
            s.c.status == _lit("aprobada"), s.c.appointment_at.is_not(None),
            s.c.appointment_at <= sa.func.now(), _in_window(s.c.appointment_at, dias),
        ).order_by(s.c.appointment_at.desc())).fetchall()


def _by_week(column, table, semanas):
    """[(monday ISO date, count)] of the rows of `table` whose `column` falls in the last `semanas` weeks."""
    semana = sa.cast(sa.func.date_trunc(_lit("week"), _local(column)), sa.Date).label("semana")
    cutoff = sa.func.now() - (sa.literal(semanas, sa.Integer) * sa.literal_column("7")) * sa.literal_column(
        "interval '1 day'", sa.Interval)
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(semana, sa.func.count().label("cantidad")).select_from(table).where(
            column > cutoff).group_by(semana).order_by(semana)).fetchall()
        return [(r["semana"].isoformat(), r["cantidad"]) for r in rows]


def reports_booking_requests_by_week(semanas=8):
    """[(semana_iso, cantidad)] de solicitudes CREADAS en las últimas
    `semanas` semanas ISO (lunes a lunes, hora de Buenos Aires) —
    `semana_iso` es el lunes de esa semana (YYYY-MM-DD). Señal de demanda
    ENTRANTE para el reporte "Capacidad estacional" (a diferencia de
    reports_volume, que agrupa por día para el sub-reporte de gestión)."""
    return _by_week(_br.c.created, _br, semanas)


def reports_searches_by_week(semanas=8):
    """Igual que reports_booking_requests_by_week pero sobre `searches.ts` —
    demanda "no convertida" (alguien buscó pero no llegó a pedir turno), la
    misma tabla que alimenta el sub-reporte "Búsquedas"."""
    return _by_week(sm.searches.c.ts, sm.searches, semanas)


def reports_statistics_rows(desde_iso, hasta_iso):
    """[(servicio_id, centro_slug, asistencia, nacimiento, sexo)] de
    aprobadas cuyo `turno_dt` cae en el rango de FECHAS ART [desde_iso,
    hasta_iso) — `hasta_iso` EXCLUSIVE, pasar el primer día del mes
    SIGUIENTE. Fuente del sub-reporte "Estadística" (estilo C2, va a la
    oficina de estadística): a propósito NO trae dni/nombre/apellido — este
    reporte no necesita PII y así queda estructuralmente imposible que se le
    escape una fila con identidad al CSV agregado."""
    c = _br.c
    local_day = db_values._local_date(c.appointment_at)
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(c.service_id, c.center_slug, c.attendance, c.birth_date, c.sex).where(
            _status("aprobada"), c.appointment_at.is_not(None),
            local_day >= sa.cast(sa.literal(desde_iso, sa.Text), sa.Date),
            local_day < sa.cast(sa.literal(hasta_iso, sa.Text), sa.Date),
        )).fetchall()
        return [(r["service_id"], r["center_slug"], r["attendance"], r["birth_date"], r["sex"])
                for r in rows]
