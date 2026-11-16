"""core/db_wire.py — the wire-format contract of the persistence layer.

The tables and columns of Postgres are English, but every dict a `core/db_*.py` function returns keeps the OLD
Spanish keys (`_WIRE_BACK`, frozen on purpose until the consumers are translated too): `_wire(row, table)` renames
the keys of a fetched row back. One table-driven point instead of an `AS <old key>` in every statement.

Split out of `core/db.py` (the DB split of 2026-10-30): the first layer, imports no other `core/db_*.py`.
"""


# ---- wire-format contract ---------------------------------------------
#
# Rename map 2026-07-29/30 (docs/rename-map.md §3, the pre-Alembic migration 001):
# las tablas y columnas de Postgres pasaron a inglés, pero cada dict que
# db.py devuelve a un consumidor (server.py, admin_*, chat_*, reportes,
# tests) TIENE que seguir usando las claves VIEJAS (español) — ese contrato
# de wire format queda congelado a propósito hasta la pasada que también
# traduzca el frontend/consumidores (fuera de alcance acá).
#
# En vez de escribir "AS <clave_vieja>" a mano en cada SELECT/RETURNING (231
# execute() en este archivo), un único punto: `_wire(row, tabla)` renombra
# las claves nuevas de vuelta a las viejas después del fetch. Menos superficie
# de error que 231 listas de columnas explícitas, y cubre automáticamente
# cualquier `SELECT *`/`RETURNING *` sin tener que enumerar columnas.
#
# tabla_nueva -> {columna_nueva: columna_vieja}. Sólo las columnas que
# cambiaron de nombre (idéntico al `_COLUMN_RENAMES` de la migración, pero
# invertido) — las que no cambiaron no necesitan entrada, `_wire` las deja
# pasar tal cual.
_WIRE_BACK = {
    "booking_requests": {
        "first_name": "nombre", "last_name": "apellido", "birth_date": "nacimiento",
        "sex": "sexo", "contact": "contacto", "service_id": "servicio_id",
        "center_slug": "centro_slug", "status": "estado",
        "referral_path": "orden_path", "referral_mime": "orden_mime",
        "rejection_reason": "motivo_rechazo", "appointment_at": "turno_dt",
        "hsi_status": "hsi_estado", "attendance": "asistencia",
        "attendance_updated_at": "asistencia_actualizada_en",
        "attendance_updated_by": "asistencia_actualizada_por",
        "overbooking": "sobreturno", "time_preference": "preferencia_horario",
        "requester_dni": "solicitante_dni", "assigned_doctor": "medico_asignado",
        "assigned_doctor_id": "medico_asignado_id", "reminder_at": "recordatorio_dt",
        "interview_reminder_at": "entrevista_recordatorio_dt",
        "resolved_by": "resuelta_por", "resolved_at": "resuelta_en",
        "confirmation": "confirmacion", "confirmation_at": "confirmacion_en",
        "confirmation_channel": "confirmacion_canal", "cancelled_at": "cancelada_en",
        "slot_doctor_id": "slot_medico_id", "patient_note": "nota_paciente",
    },
    "attendance_events": {
        "request_id": "solicitud_id", "attendance_from": "de_asistencia",
        "attendance_to": "a_asistencia", "operator": "operador", "source": "origen",
    },
    "operators": {
        "username": "usuario", "active": "activo", "created": "creado",
        "last_access": "ultimo_acceso", "role": "rol", "doctor_id": "medico_id",
        "centers": "centros", "invited_at": "invitado_en",
    },
    "admin_preferences": {"username": "usuario", "key": "clave", "value": "valor"},
    "admin_reset_tokens": {
        "username": "usuario", "expires": "expira", "used": "usado", "created": "creado",
    },
    "admin_account_security": {
        "username": "usuario", "owner_last_access": "owner_ultimo_acceso",
    },
    "passkeys": {
        "username": "usuario", "name": "nombre", "created": "creado",
        "last_used": "ultimo_uso",
    },
    "users": {
        "first_name": "nombre", "last_name": "apellido", "birth_date": "nacimiento",
        "sex": "sexo", "contact": "contacto", "active": "activo",
        "coverage": "cobertura", "address": "domicilio", "notifications": "avisos",
    },
    "dependents": {
        "adult_dni": "adulto_dni", "first_name": "nombre", "last_name": "apellido",
        "birth_date": "nacimiento", "sex": "sexo", "relationship": "vinculo",
        "affidavit_at": "declaracion_jurada_at",
    },
    "reset_tokens": {"expires": "expira", "used": "usado"},
    "catalog": {"type": "tipo", "deleted": "borrado"},
    "otp_codes": {
        "contact": "contacto", "purpose": "proposito", "attempts": "intentos",
        "used": "usado", "expires": "expira",
    },
    "otp_verified": {
        "contact": "contacto", "purpose": "proposito",
        "verified_until": "verificado_hasta",
    },
    "refeps_cache": {"license_number": "matricula"},
    "waitlist": {
        "request_id": "solicitud_id", "service_id": "servicio_id",
        "center_slug": "centro_slug", "appointment_at": "turno_dt", "active": "activo",
    },
    "appointment_offers": {
        "slot_at": "slot_dt", "service_id": "servicio_id", "center_slug": "centro_slug",
        "waitlist_id": "lista_espera_id", "status": "estado",
    },
    "searches": {"results": "resultados", "source": "origen"},
    "slot_holds": {
        "expires": "expira", "service_id": "servicio_id", "day": "dia", "time": "hora",
        "request_id": "solicitud_id",
    },
    "ai_costs": {
        "operation": "operacion", "cost_usd": "costo_usd", "user_dni": "usuario_dni",
        "model": "modelo", "recognized": "reconocido",
    },
    "login_panel_events": {"variant": "variante", "event": "evento"},
    "chat_messages": {"channel": "canal", "key": "clave", "role": "rol", "content": "contenido"},
    "chat_handoffs": {
        "web_key": "clave_web", "created": "creado", "expires": "expira",
        "used": "usado", "used_phone": "usado_tel", "used_at": "usado_en",
    },
    "ai_prompts": {
        "key": "clave", "text_en": "texto_en", "text_es": "texto_es", "edited": "editado",
        "updated": "actualizado", "updated_by": "actualizado_por",
    },
    "ai_prompts_history": {
        "key": "clave", "text_en": "texto_en", "text_es": "texto_es", "updated": "actualizado",
        "updated_by": "actualizado_por",
    },
}


def _wire(row, tabla):
    """Renombra las claves de una fila (dict-like) de vuelta al wire format
    viejo (español) para el tabla dado. `row` puede ser None (pass-through,
    cómodo con fetchone() de un UPDATE/DELETE ... RETURNING que no matcheó)."""
    if row is None:
        return None
    mapping = _WIRE_BACK.get(tabla)
    oculto = _WIRE_HIDDEN.get(tabla, ())
    if not mapping:
        return {k: v for k, v in dict(row).items() if k not in oculto}
    return {mapping.get(k, k): v for k, v in row.items() if k not in oculto}


# Columns a whole-row read never carries. `booking_requests.case_chat` is the
# full «Contanos tu caso» conversation: staff open it one request at a time
# through a logged read (core/access_log, db_chat.case_chat_get), so the
# queue, the agenda and every other row reader never see it.
_WIRE_HIDDEN = {"booking_requests": frozenset({"case_chat"})}


def _wire_all(rows, tabla):
    return [_wire(r, tabla) for r in rows]
