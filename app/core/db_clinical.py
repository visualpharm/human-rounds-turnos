"""core/db_clinical.py — the clinical statements of the persistence layer.

The pre-visit interviews and their consultations (`interview_*`, `consultation_*`, the red-triage bell),
the patient's health profile, the pre-generated chart summaries (`summary_*`), the lab results
(`lab_analysis_*`, `patient_lab_upload_*`), the imported clinical history, «Mis pacientes»
(`patients_of_doctor`, `interviews_of_doctor`, `doctor_has_patient`) and the clinical access log
(`*_clinical_access*`; its append-only trigger lets only the retention purge delete). Split out of
`core/db.py` in stage 4 of the SQLAlchemy Core move (docs/architecture/sqlalchemy-core.md): the
statements are Core, built from `core/schema_model.py`.

Callers write `db_clinical.interview_get(...)`: `core/db.py` re-exports nothing of this module (TASK-57,
`tests/test_db_domain_modules.py`). The functions reach the connection as `db._connect()`, the layers below
as `db_wire._wire` / `db_values._jsonb`, a sibling as `db_x.<name>` and their own names bare, all at CALL
time, so a test that patches the owner patches what every caller uses.
"""

import hashlib
import json
import re
import secrets
from datetime import date, datetime

import psycopg
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.core import db
from app.core import db_accounts
from app.core import db_wire
from app.core import db_values
from app.core import db_locks
from app.core import schema_model as sm
from app.core import sqlcore


# `to_char(<date>, 'YYYY-MM-DD')`: the day of a lab report as the text the screens have always got.
# The format stays a SQL literal, as it was in the hand-written statement.
_ISO_DAY = sa.literal_column("'YYYY-MM-DD'")


def _qr():
    """`entry_mode = 'qr'` with the constant as a SQL literal (it is only ever sorted on)."""
    return sm.interviews.c.entry_mode == sa.literal_column("'qr'")


# ---- Resumen del paciente (chart_summary.py, 2026-08-01) --------------------

def summary_get(dni, doctor_id):
    """La fila completa del resumen pre-generado para (dni, médico), o None."""
    t = sm.patient_summaries
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(
            t.c.dni == str(dni or ""), t.c.doctor_id == str(doctor_id or ""))).fetchone()


def summary_upsert(dni, doctor_id, especialidad, resumen, fingerprint, ai_model):
    """Crea o reemplaza el resumen de (dni, médico). El resumen es un dict
    (se persiste como jsonb); el reemplazo es entero, nunca un merge."""
    t = sm.patient_summaries
    with db._connect() as conn, conn.cursor() as cur:
        insert = _pg.insert(t).values(
            dni=str(dni or ""), doctor_id=str(doctor_id or ""), especialidad=especialidad,
            resumen=resumen, fingerprint=fingerprint, ai_model=ai_model, generated_at=sa.func.now())
        new = insert.excluded
        sqlcore.run(cur, insert.on_conflict_do_update(
            index_elements=[t.c.dni, t.c.doctor_id],
            set_={"especialidad": new.especialidad, "resumen": new.resumen,
                  "fingerprint": new.fingerprint, "ai_model": new.ai_model,
                  "generated_at": sa.func.now()}))
        conn.commit()


# ---- Laboratorio del paciente (lab_analysis.py, 2026-08-06) -----------------

_FECHA_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _fecha_or_none(valor):
    """El día de un informe como lo guardan las columnas `fecha` (date), o
    None cuando no es un día real: «2024-13-45», «15/02/2024» o vacío se
    guardan como «sin fecha», nunca levantan. Acepta date/datetime y texto ISO
    (con o sin hora)."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor or "").strip()
    if not _FECHA_ISO_RE.match(texto):
        return None
    try:
        return date.fromisoformat(texto[:10])
    except ValueError:
        return None


def lab_analysis_for(dni):
    """Todas las filas de análisis de laboratorio ya guardadas para este DNI,
    en una sola consulta (lab_analysis.py arma el cache en memoria por
    report_hash a partir de esto — nunca una consulta por informe)."""
    dni = str(dni or "").strip()
    if not dni:
        return []
    t = sm.lab_report_analysis
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(
            t.c.id, t.c.dni, t.c.report_hash, t.c.prompt_hash,
            sa.func.to_char(t.c.fecha, _ISO_DAY).label("fecha"),
            t.c.sistema, t.c.tipo_estudio, t.c.resumen, t.c.tono, t.c.sensible, t.c.valores,
            t.c.modelo, t.c.created,
        ).where(t.c.dni == dni)).fetchall()


def lab_analysis_lecturas():
    """Las lecturas de IA de TODOS los pacientes que vale la pena traducir:
    sólo `report_hash`, `tipo_estudio`, `resumen` — sin DNI — y sin las
    sensibles ni las de fallback (éstas se reescriben al mostrar). Alimenta el
    barrido de traducción (`ai/lab_language.entradas_barrido`)."""
    t = sm.lab_report_analysis
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(
            t.c.report_hash, t.c.tipo_estudio, t.c.resumen, t.c.modelo, t.c.sensible,
        ).where(sa.not_(t.c.sensible),
                t.c.modelo.notin_(("fake", "sin_clave_api", "fallback")))).fetchall()


def lab_analysis_delete(dni, report_hash):
    """Borra la lectura cacheada de UN informe (dni, report_hash): la que
    acompañaba a un resultado que el paciente acaba de borrar — su resumen y
    sus valores son el mismo dato clínico que el archivo. Devuelve cuántas
    filas borró (0 o 1)."""
    t = sm.lab_report_analysis
    with db._connect() as conn, conn.cursor() as cur:
        n = sqlcore.run(cur, sa.delete(t).where(
            t.c.dni == str(dni or ""), t.c.report_hash == report_hash)).rowcount
        conn.commit()
        return n


def lab_analysis_upsert(dni, report_hash, prompt_hash, fecha, sistema,
                        tipo_estudio, resumen, tono, sensible, valores, modelo):
    """Crea o reemplaza la lectura de UN informe (dni, report_hash). Igual
    que summary_upsert: el reemplazo es entero, nunca un merge — una
    re-lectura (prompt editado, o un fallback que se retoma con clave real)
    pisa la fila anterior de ese mismo informe."""
    t = sm.lab_report_analysis
    with db._connect() as conn, conn.cursor() as cur:
        insert = _pg.insert(t).values(
            dni=str(dni or ""), report_hash=report_hash, prompt_hash=prompt_hash,
            fecha=_fecha_or_none(fecha), sistema=sistema, tipo_estudio=tipo_estudio,
            resumen=resumen, tono=tono, sensible=bool(sensible), valores=valores, modelo=modelo,
            created=sa.func.now())
        new = insert.excluded
        sqlcore.run(cur, insert.on_conflict_do_update(
            index_elements=[t.c.dni, t.c.report_hash],
            set_={"prompt_hash": new.prompt_hash, "fecha": new.fecha, "sistema": new.sistema,
                  "tipo_estudio": new.tipo_estudio, "resumen": new.resumen, "tono": new.tono,
                  "sensible": new.sensible, "valores": new.valores, "modelo": new.modelo,
                  "created": sa.func.now()}))
        conn.commit()


def upcoming_for_summaries(horizon_hours=36):
    """(dni, médico) de toda solicitud aprobada con turno en las próximas
    `horizon_hours` horas y médico asignado — la lista que el batch de
    pre-generación recorre (chart_summary.tick)."""
    t = sm.booking_requests
    with db._connect() as conn, conn.cursor() as cur:
        return [(str(r["dni"]), str(r["doctor_id"])) for r in sqlcore.run(cur, sa.select(
            t.c.dni, t.c.assigned_doctor_id.label("doctor_id")).distinct().where(
            t.c.status == sa.literal_column("'aprobada'"),
            t.c.assigned_doctor_id.is_not(None), t.c.assigned_doctor_id != "",
            t.c.dni.is_not(None), t.c.dni != "",
            t.c.appointment_at.is_not(None),
            t.c.appointment_at >= sa.func.now(),
            t.c.appointment_at <= sa.func.now() + db_values._interval(int(horizon_hours), "hour"),
        )).fetchall()]


# ---- Archivo clínico normalizado (sólo lectura para preparación) -----------

def clinical_history_by_dni(dni):
    """Registros importados de todas las fuentes para un DNI, en orden."""
    dni = "".join(c for c in str(dni or "") if c.isdigit())
    if not dni:
        return []
    t = sm.clinical_history_records
    with db._connect() as conn, conn.cursor() as cur:
        out = []
        for row in sqlcore.run(cur, sa.select(t.c.source_system, t.c.record).where(
                t.c.dni == dni).order_by(t.c.occurred_on.nulls_last(), t.c.id)).fetchall():
            record = dict(row["record"] or {})
            record.setdefault("sistema", row["source_system"])
            out.append(record)
        return out


def clinical_history_replace(dni, records, source_system="local_history"):
    """Atomically replace one DNI/source import and return the row count."""
    dni = "".join(c for c in str(dni or "") if c.isdigit())
    source_system = str(source_system or "").strip()
    if not dni or not source_system:
        raise ValueError("dni and source_system are required")
    clean = [dict(row) for row in (records or []) if isinstance(row, dict)]
    t = sm.clinical_history_records
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.delete(t).where(t.c.dni == dni, t.c.source_system == source_system))
        for record in clean:
            payload = json.dumps(record, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"))
            record_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            fecha = str(record.get("fecha") or "")[:10]
            try:
                date.fromisoformat(fecha)
            except ValueError:
                fecha = None
            sqlcore.run(cur, sa.insert(t).values(
                dni=dni, source_system=source_system, record_hash=record_hash, occurred_on=fecha,
                record=record))
        conn.commit()
    return len(clean)


def patient_lab_upload_insert(dni, uploaded_by_dni, *, filename=None, mime=None,
                              archivo=None, texto=None, fecha=None,
                              sin_lectura=False):
    """Guarda un resultado de laboratorio subido por el paciente y devuelve
    su id. `dni` es el DNI clínico del informe; `uploaded_by_dni` el de la
    sesión que lo subió (el único que después puede borrarlo)."""
    dni = "".join(c for c in str(dni or "") if c.isdigit())
    uploaded_by_dni = "".join(c for c in str(uploaded_by_dni or "") if c.isdigit())
    if not dni or not uploaded_by_dni:
        raise ValueError("dni and uploaded_by_dni are required")
    with db._connect() as conn, conn.cursor() as cur:
        new_id = sqlcore.run(cur, sa.insert(sm.patient_lab_uploads).values(
            dni=dni, uploaded_by_dni=uploaded_by_dni, filename=filename, mime=mime,
            archivo=archivo, texto=texto, fecha=_fecha_or_none(fecha),
            sin_lectura=bool(sin_lectura),
        ).returning(sm.patient_lab_uploads.c.id)).fetchone()["id"]
        conn.commit()
        return new_id


def _lab_upload_summary(t):
    """The columns of a lab upload that are read back: everything except the file's bytes."""
    return (t.c.id, t.c.dni, t.c.uploaded_by_dni, t.c.filename, t.c.mime, t.c.texto,
            sa.func.to_char(t.c.fecha, _ISO_DAY).label("fecha"), t.c.sin_lectura, t.c.created)


def patient_lab_uploads_by_dni(dni):
    """Los informes vivos (no borrados) subidos para ese DNI clínico, del más
    viejo al más nuevo. No devuelve los bytes del archivo — nadie los lee en
    caliente (la transcripción ya quedó en `texto`)."""
    dni = "".join(c for c in str(dni or "") if c.isdigit())
    if not dni:
        return []
    t = sm.patient_lab_uploads
    with db._connect() as conn, conn.cursor() as cur:
        return [dict(r) for r in sqlcore.run(cur, sa.select(*_lab_upload_summary(t)).where(
            t.c.dni == dni, t.c.deleted.is_(None)).order_by(t.c.fecha.nulls_last(), t.c.id)).fetchall()]


def patient_lab_upload_get(upload_id, uploaded_by_dni):
    """La fila viva (sin los bytes del archivo) de un resultado, sólo para
    quien lo subió; None si no existe, ya está borrada o es de otra persona.
    Lo usa el borrado para saber QUÉ lectura cacheada acompaña al archivo."""
    uploaded_by_dni = "".join(c for c in str(uploaded_by_dni or "") if c.isdigit())
    try:
        upload_id = int(upload_id)
    except (TypeError, ValueError):
        return None
    if not uploaded_by_dni:
        return None
    t = sm.patient_lab_uploads
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(*_lab_upload_summary(t)).where(
            t.c.id == upload_id, t.c.uploaded_by_dni == uploaded_by_dni,
            t.c.deleted.is_(None))).fetchone()
        return dict(row) if row else None


def patient_lab_upload_delete(upload_id, uploaded_by_dni):
    """El paciente borra un resultado que subió — SÓLO quien lo subió.
    Devuelve True si borró.

    «Borrar» es borrar (revisión de esquema №11, 2026-10-24): el archivo
    (`archivo`), su transcripción (`texto`) y el nombre (`filename`) quedan en
    NULL en la misma sentencia. La fila vacía se queda — es el registro de que
    pasó (`deleted`, quién la subió, de quién era) y no guarda nada clínico.
    Antes sólo se marcaba `deleted`, y el archivo seguía entero en la tabla y
    en cada volcado de la base."""
    uploaded_by_dni = "".join(c for c in str(uploaded_by_dni or "") if c.isdigit())
    try:
        upload_id = int(upload_id)
    except (TypeError, ValueError):
        return False
    if not uploaded_by_dni:
        return False
    t = sm.patient_lab_uploads
    with db._connect() as conn, conn.cursor() as cur:
        borrado = sqlcore.run(cur, sa.update(t).values(
            deleted=sa.func.now(), archivo=None, texto=None, filename=None,
        ).where(t.c.id == upload_id, t.c.uploaded_by_dni == uploaded_by_dni,
                t.c.deleted.is_(None))).rowcount > 0
        conn.commit()
        return borrado


def purge_lab_upload_files(days, limit=500):
    """Vence el ARCHIVO (bytea) de los resultados subidos hace más de `days`
    días: queda NULL; la transcripción (`texto`), que es lo que el médico lee,
    y la fila se conservan. Mismo espíritu que la retención de las órdenes de
    derivación (`booking_referrals.sweep_referrals`): se borra el archivo, no
    el registro. `days<=0` no hace nada (el «0 = no limpiar» de la instalación
    municipal). Idempotente: una fila ya vaciada no es candidata. LIMIT-bounded
    como el resto de las purgas de housekeeping. Devuelve cuántos archivos
    venció."""
    try:
        days, limit = int(days), int(limit)
    except (TypeError, ValueError):
        return 0
    if days <= 0 or limit <= 0:
        return 0
    t, due = sm.patient_lab_uploads, sm.patient_lab_uploads.alias("due")
    with db._connect() as conn, conn.cursor() as cur:
        n = sqlcore.run(cur, sa.update(t).values(archivo=None).where(t.c.id.in_(
            sa.select(due.c.id).where(due.c.archivo.is_not(None), due.c.created < db_values._days_ago(days))
            .order_by(due.c.id).limit(limit)))).rowcount
        conn.commit()
        return n


# ---- registro de accesos a datos clínicos (core/access_log.py) --------------

def insert_clinical_access(actor, actor_role, action, dni_hash, target_id=None,
                           allowed=True):
    """Agrega UNA fila al registro de accesos. A diferencia de los analytics de
    arriba, LANZA si la base falla: quien registra (access_log.record) decide
    qué hacer — el registro de un acceso no se pierde en silencio."""
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.insert(sm.clinical_access_log).values(
            actor=actor, actor_role=actor_role or "", action=action, dni_hash=dni_hash,
            target_id=None if target_id is None else str(target_id), allowed=bool(allowed)))


def insert_clinical_access_many(actor, actor_role, action, rows):
    """Las filas de UNA lista (rows = [(dni_hash, target_id), ...]) en un solo
    viaje. Lanza igual que insert_clinical_access."""
    if not rows:
        return
    t = sm.clinical_access_log
    with db._connect() as conn, conn.cursor() as cur:
        # `inline()`: no `RETURNING id` — nobody reads the keys of a batch
        sqlcore.run_many(cur, [sa.insert(t).inline().values(
            actor=actor, actor_role=actor_role or "", action=action, dni_hash=h, target_id=target,
            allowed=sa.true()) for h, target in rows])


def list_clinical_access(dni_hash, limit=200):
    """Quién abrió los datos clínicos de UN paciente, lo más nuevo primero."""
    t = sm.clinical_access_log
    with db._connect() as conn, conn.cursor() as cur:
        return [dict(r) for r in sqlcore.run(cur, sa.select(
            t.c.ts, t.c.actor, t.c.actor_role, t.c.action, t.c.target_id, t.c.allowed,
        ).where(t.c.dni_hash == dni_hash).order_by(t.c.ts.desc(), t.c.id.desc())
            .limit(max(1, min(int(limit), 1000)))).fetchall()]


def purge_clinical_access_log(days, limit=5000):
    """Borra filas del registro de accesos más viejas que `days` días. El
    trigger de sólo-agregado (GUARD_SQL de la revisión 0026) acepta el DELETE sólo con
    turnos.access_log_purge en esta transacción y nunca sobre una fila de menos
    de un año: `days` por debajo de 365 no borra nada más nuevo que eso."""
    days = max(365, int(days))
    t, old = sm.clinical_access_log, sm.clinical_access_log.alias("old")
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.select(sa.func.set_config(
            sa.literal_column("'turnos.access_log_purge'"), sa.literal_column("'on'"), sa.true())))
        return sqlcore.run(cur, sa.delete(t).where(t.c.id.in_(
            sa.select(old.c.id).where(old.c.ts < db_values._days_ago(days))
            .order_by(old.c.ts).limit(int(limit))))).rowcount


# ---- entrevista previa (modules/intake/) ----------------------------------

def interviews_by_session_keys(keys):
    """{session_key: {id, dni}} de las entrevistas cuyas conversaciones lista
    «Chats» (canal 'interview'): el registro de accesos necesita de quién es
    cada una (K10, 2026-10-23)."""
    keys = [str(k) for k in (keys or []) if k]
    if not keys:
        return {}
    t = sm.interviews
    with db._connect() as conn, conn.cursor() as cur:
        return {r["session_key"]: {"id": r["id"], "dni": r["dni"]} for r in sqlcore.run(
            cur, sa.select(t.c.id, t.c.dni, t.c.session_key).where(t.c.session_key.in_(keys))
        ).fetchall()}


def _intake_day_bounds(dia):
    """[inicio, fin) del día `dia` (YYYY-MM-DD) en el huso de la
    instalación (tz.py) — nunca el date del proceso ni el offset que
    devuelva Postgres."""
    from app.core import tz as _tz
    from datetime import datetime as _dt, timedelta as _td
    start = _dt.strptime(str(dia), "%Y-%m-%d").replace(tzinfo=_tz.tzinfo())
    return start, start + _td(days=1)


def interview_create(doctor_id=None, entry_mode="qr", request_id=None, dni=None,
                     entered_name=None, entered_surname=None, matched=False):
    """Crea la fila y devuelve el dict completo (con session_key
    'interview:<id>' ya persistida — dos pasos en la misma conexión; el
    placeholder inicial es random para no chocar el UNIQUE entre creates
    concurrentes). `doctor_id=None` (fase 2): una entrevista post-reserva
    puede crearse ANTES de que el hospital asigne médico — interview_agent
    interpola "el médico" genérico en ese caso. `patient_token` se genera
    acá (secrets.token_urlsafe, opaco e impredecible) — ES la autorización
    del chat del paciente, mismo modelo que appointment_offers.token."""
    if request_id is not None:
        # A request-bound interview only through the one-per-request gate.
        return interview_bind(request_id, doctor_id=doctor_id, entry_mode=entry_mode,
                                 dni=dni, matched=matched, entered_name=entered_name,
                                 entered_surname=entered_surname)
    with db._connect() as conn, conn.cursor() as cur:
        return _interview_insert(cur, doctor_id, entry_mode, None, dni,
                                    entered_name, entered_surname, matched)


def _interview_insert(cur, doctor_id, entry_mode, request_id, dni,
                      entered_name, entered_surname, matched):
    t = sm.interviews
    token = secrets.token_urlsafe(24)
    iid = sqlcore.run(cur, sa.insert(t).values(
        doctor_id=str(doctor_id) if doctor_id is not None else None,
        entry_mode=str(entry_mode), request_id=request_id, dni=dni,
        entered_name=entered_name, entered_surname=entered_surname, matched=bool(matched),
        session_key="pending:" + secrets.token_hex(12), patient_token=token,
    ).returning(t.c.id)).fetchone()["id"]
    return sqlcore.run(cur, sa.update(t).values(session_key=f"interview:{iid}")
                       .where(t.c.id == iid).returning(*t.c)).fetchone()


def interview_bind(request_id, doctor_id=None, entry_mode="post_booking", dni=None,
                   matched=True, entered_name=None, entered_surname=None,
                   authenticated=None):
    """THE way an interview gets bound to a request — one per request
    (`interviews_request_uniq`; IntakeTriage.tla OneInterviewPerRequest).
    Serialized per request under an advisory lock; the unique index is the
    backstop.

    - `post_booking` (the patient's own authenticated entry): returns the
      request's existing non-QR interview if there is one. A QR interview
      bound to the request is detached (`request_id` NULL, its former
      request kept in `detached_request_id`, nothing deleted) and the new
      one takes the binding: someone typing the patient's DNI at the door
      must never decide what the patient and the doctor see.
    - `qr` (door entry by DNI): NEVER returns an existing QR interview, and
      the anonymous one (typed DNI) never returns any existing interview —
      its token would hand a stranger that session. If the request already
      has one, the new interview starts unbound (`matched` false), exactly
      like a DNI that matched no appointment. The logged-in scan
      (`authenticated=True`, identity = session) continues the patient's
      own non-QR interview if there is one.

    Returns the interview row."""
    if authenticated is None:
        authenticated = entry_mode != "qr"
    t = sm.interviews
    with db._connect() as conn, conn.cursor() as cur:
        db_locks._advisory_xact_lock(cur, "interview_request:", db_values._text_value(str(int(request_id))))
        existing = sqlcore.run(cur, sa.select(t).where(
            t.c.request_id == int(request_id)).with_for_update()).fetchone()
        bind_to = int(request_id)
        if existing is not None:
            if existing["entry_mode"] != "qr" and authenticated:
                return existing
            if entry_mode == "qr" or existing["entry_mode"] != "qr":
                bind_to, matched = None, False
            else:
                sqlcore.run(cur, sa.update(t).values(
                    detached_request_id=t.c.request_id, request_id=None, updated=sa.func.now(),
                ).where(t.c.id == existing["id"]))
        return _interview_insert(cur, doctor_id, entry_mode, bind_to, dni,
                                    entered_name, entered_surname, matched)


def interview_get(iid):
    t = sm.interviews
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(t.c.id == int(iid))).fetchone()


def interview_by_patient_token(token):
    """La entrevista cuyo token opaco (URL /entrevista/... y /api/intake/t/...)
    es `token`, o None. El token ES la autorización — el caller nunca vuelve
    a chequear sesión/DNI contra esta fila."""
    token = str(token or "").strip()
    if not token:
        return None
    t = sm.interviews
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(t.c.patient_token == token)).fetchone()


def interview_for_request(request_id):
    """La entrevista más reciente (preferentemente post-booking, no QR) atada a una solicitud, o None."""
    t = sm.interviews
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(t.c.request_id == int(request_id))
                           .order_by(_qr().asc(), t.c.id.desc()).limit(1)).fetchone()


def interview_set_status(iid, status):
    t = sm.interviews
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.update(t).values(status=str(status), updated=sa.func.now())
                    .where(t.c.id == int(iid)))


def health_profile_get(dni):
    """Lo que ya sabemos de esta persona, o {} — nunca None, así quien lo usa
    no tiene que preguntar. Devuelve {"datos", "updated", "source_interview_id"}."""
    dni = str(dni or "").strip()
    if not dni:
        return {}
    t = sm.patient_health_profile
    with db._connect() as conn, conn.cursor() as cur:
        fila = sqlcore.run(cur, sa.select(
            t.c.dni, t.c.datos, t.c.source_interview_id, t.c.updated).where(t.c.dni == dni)).fetchone()
    return dict(fila) if fila else {}


def health_profile_save(dni, datos, source_interview_id=None):
    """Pisa el perfil de esta persona con `datos` (ya fusionado por quien
    llama — acá no se decide qué queda y qué se va). Un DNI vacío es un
    no-op: una entrevista por QR sin identificar no tiene a quién recordarle
    nada."""
    dni = str(dni or "").strip()
    if not dni:
        return
    t = sm.patient_health_profile
    with db._connect() as conn, conn.cursor() as cur:
        insert = _pg.insert(t).values(
            dni=dni, datos=datos or {}, source_interview_id=source_interview_id,
            updated=sa.func.now())
        new = insert.excluded
        sqlcore.run(cur, insert.on_conflict_do_update(
            index_elements=[t.c.dni],
            set_={"datos": new.datos, "source_interview_id": new.source_interview_id,
                  "updated": sa.func.now()}))


def interview_update_analysis(iid, triage, summary, brief, differential,
                              suggested_exams, red_flags, ai_model):
    """Persiste el análisis (fase 3) — incluyendo una RE-corrida después de
    reabrir una entrevista 'done' (fase "Actualizar entrevista"). Si el
    triage nuevo es ROJO, la alerta se resetea sola (`alert_seen_at`/`_by`
    vuelven a NULL) para que la campanita vuelva a sonar — sin esto, una
    entrevista que ya había sido vista por el médico (aunque fuera con un
    triage distinto o ya ackeada) quedaría escalando en silencio. Es un
    no-op inofensivo la primera vez que se analiza (esas columnas ya están
    en NULL), así que no hace falta distinguir "primera vez" de "reanálisis"
    acá adentro."""
    t = sm.interviews
    changes = {
        "triage": triage, "summary": summary, "brief": db_values._jsonb(brief),
        "differential": db_values._jsonb(differential), "suggested_exams": db_values._jsonb(suggested_exams),
        "red_flags": db_values._jsonb(red_flags), "ai_model": ai_model, "analyzed_at": sa.func.now(),
        "updated": sa.func.now(),
    }
    if triage == 'ROJO':
        changes.update(alert_seen_at=None, alert_seen_by=None)
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.update(t).values(**changes).where(t.c.id == int(iid)))


def interview_reopen(iid):
    """Reabre una entrevista 'done' para que el paciente agregue información
    nueva ("Actualizar entrevista", 2026-08-01): vuelve a 'active' y marca
    `reopened_at`, conservando el transcript entero (nada se borra). Guard
    igual que `interview_mark_alert_seen`: sólo matchea WHERE status='done',
    así una carrera (dos reopens casi simultáneos, o un doble click) no pisa
    nada — RETURNING vacío y el caller lo trata como "ya estaba abierta"."""
    t = sm.interviews
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).values(
            status='active', reopened_at=sa.func.now(), updated=sa.func.now(),
        ).where(t.c.id == int(iid), t.c.status == 'done').returning(*t.c)).fetchone()


def interviews_for_day(doctor_ids, dia):
    """Todas las entrevistas MATCHEADAS (request_id no nulo) de esos médicos
    creadas el día `dia` (preferentemente post-booking, no QR) — un solo query para toda la cola.
    En caso de duplicados por DNI, gana la no-QR."""
    ids = [str(d) for d in (doctor_ids or []) if str(d or "").strip()]
    if not ids:
        return []
    start, end = _intake_day_bounds(dia)
    t = sm.interviews
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(
            t.c.doctor_id.in_(ids), t.c.request_id.is_not(None),
            t.c.created >= start, t.c.created < end,
        ).order_by(_qr().desc(), t.c.id)).fetchall()


def interviews_by_request_ids(request_ids):
    """{request_id: fila} para esos turnos — un solo query (preferentemente post-booking, no QR).

    Lo usa "Mis turnos" (doctor_appointments.py), que mira 14 días y por eso
    no puede pedir día por día como la cola. La última entrevista gana: un
    turno reabierto tiene más de una fila y la que vale es la más nueva (no-QR si la hay)."""
    ids = [int(r) for r in (request_ids or []) if str(r or "").strip().isdigit()]
    if not ids:
        return {}
    t = sm.interviews
    with db._connect() as conn, conn.cursor() as cur:
        return {int(row["request_id"]): row for row in sqlcore.run(cur, sa.select(t).where(
            t.c.request_id.in_(ids)).order_by(_qr().desc(), t.c.id)).fetchall()}


def interviews_unmatched_for_day(doctor_id, dia):
    """Entrevistas por QR de ese médico SIN turno matcheado ese día
    ("sin turno asignado" en la cola)."""
    start, end = _intake_day_bounds(dia)
    t = sm.interviews
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(
            t.c.doctor_id == str(doctor_id), t.c.request_id.is_(None),
            t.c.created >= start, t.c.created < end).order_by(t.c.id)).fetchall()


# ---- consultas (modules/intake/consult_admin.py) -----------------------------

def _consultation_values(interview_id, doctor_id, diagnoses, exams, meds, notes,
                         receta_json, orden_json, receta_token, orden_token, created_by):
    return dict(
        interview_id=int(interview_id), doctor_id=str(doctor_id), diagnoses=db_values._jsonb(diagnoses),
        exams=db_values._jsonb(exams), meds=db_values._jsonb(meds), notes=notes,
        receta_json=db_values._jsonb(receta_json), orden_json=db_values._jsonb(orden_json),
        receta_token=receta_token, orden_token=orden_token, created_by=created_by)


def consultation_create(interview_id, doctor_id, diagnoses, exams, meds,
                        notes, receta_json, orden_json, receta_token,
                        orden_token, created_by):
    t = sm.consultations
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.insert(t).values(**_consultation_values(
            interview_id, doctor_id, diagnoses, exams, meds, notes, receta_json, orden_json,
            receta_token, orden_token, created_by)).returning(*t.c)).fetchone()


def consultation_by_token(token):
    """La consulta cuyo receta_token U orden_token es `token` (el caller
    mira cuál de los dos matcheó para saber qué documento servir)."""
    token = str(token or "").strip()
    if not token:
        return None
    t = sm.consultations
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(
            sa.or_(t.c.receta_token == token, t.c.orden_token == token))).fetchone()


class ConsultationExists(Exception):
    """Ya hay una consulta vigente y el pedido no la nombró para reemplazarla
    (o nombró otra: alguien la reemplazó mientras tanto). `vigente` es la
    fila que el panel tiene que mostrar."""

    def __init__(self, vigente):
        super().__init__("la entrevista ya tiene una consulta confirmada")
        self.vigente = vigente


def consultation_confirm(interview_id, doctor_id, diagnoses, exams, meds,
                         notes, receta_json, orden_json, receta_token,
                         orden_token, created_by, reemplaza_id=None):
    """Persiste la consulta confirmada de una entrevista — UNA vigente por
    entrevista (problems.md №1, 2026-10-23).

    Bloquea la fila de la entrevista (FOR UPDATE) para que dos «Confirmar»
    simultáneos se serialicen: el segundo ve la vigente y recibe
    `ConsultationExists`. Con una vigente, sólo un `reemplaza_id` igual a su id
    crea la nueva, y la vieja queda anulada (`superseded_at`/`superseded_by`)
    en la MISMA transacción. `consultations_vigente_uniq` es la red de la
    base si algo saltea este camino."""
    interviews, t = sm.interviews, sm.consultations
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.select(interviews.c.id).where(
            interviews.c.id == int(interview_id)).with_for_update())
        vigente = sqlcore.run(cur, sa.select(t).where(
            t.c.interview_id == int(interview_id), t.c.superseded_at.is_(None),
        ).order_by(t.c.id.desc()).limit(1)).fetchone()
        if vigente and str(vigente["id"]) != str(reemplaza_id or ""):
            raise ConsultationExists(vigente)
        if vigente:
            sqlcore.run(cur, sa.update(t).values(superseded_at=sa.func.now())
                        .where(t.c.id == vigente["id"]))
        try:
            nueva = sqlcore.run(cur, sa.insert(t).values(**_consultation_values(
                interview_id, doctor_id, diagnoses, exams, meds, notes, receta_json, orden_json,
                receta_token, orden_token, created_by)).returning(*t.c)).fetchone()
        except psycopg.errors.UniqueViolation as e:
            raise ConsultationExists(None) from e
        if vigente:
            sqlcore.run(cur, sa.update(t).values(superseded_by=nueva["id"])
                        .where(t.c.id == vigente["id"]))
        return nueva


def consultation_current_for_interview(interview_id):
    """La consulta VIGENTE (no anulada) de la entrevista, o None; la fila trae
    además `reemplazos` = cuántas consultas de esa entrevista quedaron
    anuladas antes."""
    c, o = sm.consultations.alias("c"), sm.consultations.alias("o")
    replaced = (sa.select(sa.func.count())
                .where(o.c.interview_id == c.c.interview_id, o.c.superseded_at.is_not(None))
                .scalar_subquery())
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(c, replaced.label("reemplazos")).where(
            c.c.interview_id == int(interview_id), c.c.superseded_at.is_(None),
        ).order_by(c.c.id.desc()).limit(1)).fetchone()


def consultation_replacement_of(row, hops=20):
    """La consulta que hoy reemplaza a `row` (anulada), o None si `row` sigue
    vigente: sigue la cadena `superseded_by` hasta la no anulada. Si la cadena
    se corta (fila inconsistente), cae a la vigente de la misma entrevista.
    Sólo lectura — el imprimible viejo la usa para decir quién lo reemplazó."""
    if not row or not row.get("superseded_at"):
        return None
    t = sm.consultations
    with db._connect() as conn, conn.cursor() as cur:
        actual = row
        for _ in range(hops):
            if not actual.get("superseded_at"):
                return actual
            siguiente = actual.get("superseded_by")
            if not siguiente:
                break
            actual = sqlcore.run(cur, sa.select(t).where(t.c.id == int(siguiente))).fetchone()
            if not actual:
                break
        else:
            actual = None
        if actual and not actual.get("superseded_at"):
            return actual
        return sqlcore.run(cur, sa.select(t).where(
            t.c.interview_id == int(row["interview_id"]), t.c.superseded_at.is_(None),
        ).order_by(t.c.id.desc()).limit(1)).fetchone()


def consultations_confirmed_for(interview_ids):
    """{interview_id: created} de las entrevistas con consulta vigente — un
    solo query para la cola y «Mis turnos» («Atendido» en vez de «Iniciar
    consulta»)."""
    ids = [int(i) for i in (interview_ids or []) if str(i or "").strip().isdigit()]
    if not ids:
        return {}
    t = sm.consultations
    with db._connect() as conn, conn.cursor() as cur:
        return {int(r["interview_id"]): r["created"] for r in sqlcore.run(cur, sa.select(
            t.c.interview_id, t.c.created).where(
            t.c.interview_id.in_(ids), t.c.superseded_at.is_(None))).fetchall()}


def consultation_latest_for_interview(interview_id):
    t = sm.consultations
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(t.c.interview_id == int(interview_id))
                           .order_by(t.c.id.desc()).limit(1)).fetchone()


# ---- «Mis pacientes» (doctor_patients.py, 2026-08-01) ----------------------
#
# Longitudinal roster for a doctor: every patient EVER referred to them, not
# just today's (that's «Pacientes de hoy», list_approved_day above). Two
# independent sources, matched by id — no free-text name fallback here on
# purpose: a booking only ever gets `assigned_doctor_id`/`slot_doctor_id`
# written once the hospital (or auto_confirmar/a concrete slot pick) actually
# resolved a doctor, so an id-only match doesn't miss the legacy rows
# `count_appointments_of`'s comment worries about (those predate the
# QUEUE/interview features this screen is built for).

def _roster_page(query, t, completo, meses, cursor, limit):
    """The recency window, the keyset cursor and the page size that `patients_of_doctor` and
    `interviews_of_doctor` share, applied to `query` (newest first, the id breaking ties)."""
    if not completo:
        query = query.where(t.c.created >= sa.func.now() - db_values._interval(int(meses), "month"))
    if cursor:
        try:
            c_created, c_id = cursor
        except (TypeError, ValueError):
            c_created = c_id = None
        if c_created is not None and c_id is not None:
            query = query.where(sa.tuple_(t.c.created, t.c.id) < sa.tuple_(
                sa.literal(c_created), sa.literal(c_id)))
    limit = max(1, min(int(limit or 500), 500))
    return query.order_by(t.c.created.desc(), t.c.id.desc()).limit(limit)


# A request in one of these states no longer makes its patient the doctor's: neither the roster, nor the
# order, nor the case chat of that request stay open to the doctor (2026-11-14, security review M-2 bypass).
DOCTOR_DEAD_STATUSES = ("rechazada", "cancelada")


def patients_of_doctor(medico_id, completo=False, meses=12, cursor=None, limit=500):
    """Solicitudes referidas a este médico por id — assigned_doctor_id
    (aprobación) o slot_doctor_id (el paciente reservó un turno concreto de
    esa agenda). Fuente #1 de «Mis pacientes».

    Por default (`completo=False`) recorta a los últimos `meses` (12): «Mis
    pacientes» venía trayendo TODO el historial, de cualquier estado, sin
    límite de tiempo ni de filas (auditoría 2026-08-06) — un médico con años
    de agenda esperaba una tabla que crecía sin techo. `completo=True` es el
    opt-in explícito ("ver historial completo") para levantar el recorte de
    fecha; `limit` sigue acotando la página de todos modos (tope 500).
    `cursor=(created, id)` de la última fila ya vista habilita paginación
    keyset (más reciente → más vieja, ORDER BY created DESC, id DESC — el id
    desempata filas con el mismo `created`). Sin cursor, `limit` sigue siendo
    el default de siempre: llamar la función tal cual se llamaba antes sigue
    devolviendo una lista plana, sólo que ahora acotada."""
    medico_id = str(medico_id or "")
    if not medico_id:
        return []
    t = sm.booking_requests
    query = _roster_page(sa.select(t).where(
        sa.or_(t.c.assigned_doctor_id == medico_id, t.c.slot_doctor_id == medico_id),
        t.c.status.not_in(DOCTOR_DEAD_STATUSES)),
        t, completo, meses, cursor, limit)
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(sqlcore.run(cur, query).fetchall(), "booking_requests")


def doctor_has_patient(medico_id, dni):
    """¿Es este DNI clínico paciente de este médico? Lo es si tiene una
    solicitud asignada a su agenda o reservada en ella (las mismas dos columnas
    que patients_of_doctor, sin recorte de fecha; una rechazada o cancelada no cuenta) o una entrevista
    previa dirigida a él. Lo usa access_log.may_see: un médico sólo abre la
    ficha y los datos clínicos de los suyos (K10, 2026-10-23)."""
    medico_id, dni = str(medico_id or ""), str(dni or "")
    if not medico_id or not dni:
        return False
    requests, interviews = sm.booking_requests, sm.interviews
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(sa.or_(
            sa.exists().where(requests.c.dni == dni, requests.c.status.not_in(DOCTOR_DEAD_STATUSES), sa.or_(
                requests.c.assigned_doctor_id == medico_id, requests.c.slot_doctor_id == medico_id)),
            # only an interview the desk matched to a real turn: a QR start with any DNI, typed by an
            # anonymous visitor, is no doctor-patient relationship (matched=False)
            sa.exists().where(interviews.c.dni == dni, interviews.c.doctor_id == medico_id,
                              interviews.c.matched.is_(True)),
        ).label("suyo"))).fetchone()
    return bool(row and row["suyo"])


def interviews_of_doctor(medico_id, completo=False, meses=12, cursor=None, limit=500):
    """Entrevistas previas de este médico — a diferencia de interviews_for_day
    (recorta a UN día y sólo a las matcheadas con turno), esta es la fuente
    #2 de «Mis pacientes»: incluye las entrevistas por QR que nunca
    matchearon una solicitud.

    Mismo contrato que patients_of_doctor de arriba: `completo=False` (el
    default) recorta a los últimos `meses` (12), `completo=True` es el
    opt-in de "ver historial completo", y `cursor=(created, id)` + `limit`
    habilitan paginación keyset sobre `interviews`. Sin cursor, sigue
    devolviendo la lista plana de siempre."""
    medico_id = str(medico_id or "")
    if not medico_id:
        return []
    t = sm.interviews
    query = _roster_page(sa.select(t).where(t.c.doctor_id == medico_id),
                         t, completo, meses, cursor, limit)
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, query).fetchall()


def users_by_dnis(dnis):
    """{dni: usuario público} para una lista de DNIs, una sola consulta
    (mismo patrón batcheado que patient_ranking.by_dnis) — «Mis pacientes»
    prefiere el nombre/contacto de la CUENTA sobre lo que quedó tipeado en
    una solicitud vieja."""
    limpios = sorted({str(d) for d in (dnis or []) if str(d or "").strip()})
    if not limpios:
        return {}
    t = sm.users
    with db._connect() as conn, conn.cursor() as cur:
        return {str(r["dni"]): db_accounts._user_public(db_wire._wire(r, "users")) for r in sqlcore.run(
            cur, sa.select(t).where(t.c.dni.in_(limpios))).fetchall()}


# ---- Red-triage alert bell (2026-08-01) -------------------------------------
#
# A ROJO triage is the one analysis result that shouldn't sit unread in a
# table cell — the bell surfaces it until someone explicitly acks it.
# `doctor_ids=None` means "every doctor" (admin/operador scope); an empty
# list means "this account has no linked doctor", which must return nothing,
# not everything — callers pass `None` only when they mean "no filter".

def _unseen_red(doctor_ids):
    """The unseen red-triage interviews of `doctor_ids` as a `WHERE` list, or None when the caller
    has no doctor at all. 'ROJO' stays a SQL literal: `interviews_red_alert_idx` is a partial
    index on `triage = 'ROJO' AND alert_seen_at IS NULL`."""
    t = sm.interviews
    conditions = [t.c.triage == sa.literal_column("'ROJO'"), t.c.alert_seen_at.is_(None)]
    if doctor_ids is not None:
        ids = [str(d) for d in doctor_ids if str(d or "").strip()]
        if not ids:
            return None
        conditions.append(t.c.doctor_id.in_(ids))
    return conditions


def red_alerts(doctor_ids=None):
    """Entrevistas con triage ROJO sin acknowledgear, más recientes primero.
    `doctor_ids`: None = todas (admin/operador); lista = sólo esos médicos."""
    t = sm.interviews
    conditions = _unseen_red(doctor_ids)
    if conditions is None:
        return []
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(*conditions).order_by(t.c.created.desc())).fetchall()


def red_alert_count(doctor_ids=None):
    """Igual que red_alerts pero sólo el número — lo que pide el polling de
    60s del bell (the product owner: "count query only; full list fetched on open")."""
    conditions = _unseen_red(doctor_ids)
    if conditions is None:
        return 0
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(sa.func.count().label("n")).select_from(sm.interviews)
                          .where(*conditions)).fetchone()
        return int((row or {}).get("n") or 0)


def interview_mark_alert_seen(iid, usuario):
    """Ack de una alerta roja: RETURNING * sólo si estaba sin ver (una fila
    ya ackeada no se re-marca ni pisa quién la vio primero)."""
    t = sm.interviews
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).values(
            alert_seen_at=sa.func.now(), alert_seen_by=str(usuario or "") or None,
        ).where(t.c.id == int(iid), t.c.alert_seen_at.is_(None)).returning(*t.c)).fetchone()
