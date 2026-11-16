"""booking_requests_store — persistence boundary for modules/turnos/booking_requests.py.

This is the ONLY module that modules/turnos/booking_requests.py may reach
persistence through, per the DB-coupling boundary series started by
modules/turnos/waitlist_store.py (slice 1, commit 91aaa293, unmerged branch
glm/turnos-persistence-adapter). Dependency direction: booking_requests ->
booking_requests_store -> core.db, never booking_requests -> core.db
directly, and never core -> modules.

Native charter: the booking_requests table family (create/approve/reject/
cancel/reschedule/attendance/HSI-link writes) and its slot-hold linkage.
Everything else exposed here is a documented cross-family pass-through
(waitlist, users/operators/dependents, intake, HSI connector, REFEPS cache)
kept 1:1 with core.db so booking_requests.py never imports core.db itself.

Every function below is a verbatim-signature delegation: same name, same
parameters, same defaults, single `return <owner module>.<name>(...)` body. No SQL, no
connection, no wire/coerce primitive lives here — those implementations stay
in the `core/db_*.py` module that owns each name. See
docs/roadmap/db-coupling-slice2-booking-store.md for the full audit and
invariants this file is bound by.
"""

from app.core import db
from app.core import db_booking
from app.core import db_operators
from app.core import db_accounts
from app.core import db_hsi
from app.core import db_clinical
from app.core import db_slots
from app.core import db_catalog
from app.core import db_backup

# Exception re-exports — same objects, never copies. `except` clauses in the
# orchestrator (and any other module that already does `except db.X`) must
# keep catching exactly what core.db raises.
DbUnavailable = db.DbUnavailable
LimitReached = db_booking.LimitReached
AlreadyProcessed = db_booking.AlreadyProcessed
SlotConflict = db_booking.SlotConflict
OverbookingCapacityReached = db_booking.OverbookingCapacityReached


# --- Booking-request family (native charter: the booking_requests table and
# its columns) -----------------------------------------------------------

def create_booking_request_with_limit(dni, nombre, apellido, nacimiento, contacto,
                    servicio_id, centro_slug, limite, orden_mime=None, sexo=None,
                    preferencia_horario=None, solicitante_dni=None,
                    slot_medico_id=None, idem_key=None,
                    estados_activos=("pendiente", "aprobada"), actor=None,
                    nota_paciente=None, turno_pedido=None):
    return db_booking.create_booking_request_with_limit(dni=dni, nombre=nombre, apellido=apellido, nacimiento=nacimiento, contacto=contacto, servicio_id=servicio_id, centro_slug=centro_slug, limite=limite, orden_mime=orden_mime, sexo=sexo, preferencia_horario=preferencia_horario, solicitante_dni=solicitante_dni, slot_medico_id=slot_medico_id, idem_key=idem_key, estados_activos=estados_activos, actor=actor, nota_paciente=nota_paciente, turno_pedido=turno_pedido)


def get_booking_request(sid):
    return db_booking.get_booking_request(sid=sid)


def update_booking_patient_data(sid, campos):
    return db_booking.update_booking_patient_data(sid=sid, campos=campos)


def user_admin_update(dni, campos):
    return db_accounts.user_admin_update(dni, campos)


def get_booking_request_by_idem_key(idem_key):
    return db_slots.get_booking_request_by_idem_key(idem_key=idem_key)


def set_referral(sid, orden_path, orden_mime=None):
    return db_booking.set_referral(sid=sid, orden_path=orden_path, orden_mime=orden_mime)


def save_ai(sid, ai_json):
    return db_booking.save_ai(sid=sid, ai_json=ai_json)


def clear_referral_path(sid):
    return db_booking.clear_referral_path(sid=sid)


def referrals_to_purge(retention_days, limit=200):
    return db_booking.referrals_to_purge(retention_days=retention_days, limit=limit)


def purged_file_record(path, reason, conn=None):
    return db_backup.purged_file_record(path=path, reason=reason, conn=conn)


def purged_file_paths(conn=None):
    return db_backup.purged_file_paths(conn=conn)


def assign_pending_booking_request(sid, medico, medico_id):
    return db_booking.assign_pending_booking_request(sid=sid, medico=medico, medico_id=medico_id)


def approve(sid, turno_dt, hsi_result=None, servicio_id=None, medico_asignado=None,
           resuelta_por=None, medico_asignado_id=None, sobreturno=False,
           overbooking_capacity=None, overbooking_window=None,
           overbooking_lock_key=None, overbooking_center_slug=None, slot_capacity=None,
           centro_slug=None, restaurar=False):
    return db_booking.approve(sid=sid, turno_dt=turno_dt, hsi_result=hsi_result, servicio_id=servicio_id, medico_asignado=medico_asignado, resuelta_por=resuelta_por, medico_asignado_id=medico_asignado_id, sobreturno=sobreturno, overbooking_capacity=overbooking_capacity, overbooking_window=overbooking_window, overbooking_lock_key=overbooking_lock_key, overbooking_center_slug=overbooking_center_slug, slot_capacity=slot_capacity, centro_slug=centro_slug, restaurar=restaurar)


def reject(sid, motivo, resuelta_por=None):
    return db_booking.reject(sid=sid, motivo=motivo, resuelta_por=resuelta_por)


def cancel(sid, actor=None, skip_hsi_without_connector=False,
           pending_hsi_with_connector=False):
    return db_booking.cancel(sid=sid, actor=actor,
                     skip_hsi_without_connector=skip_hsi_without_connector,
                     pending_hsi_with_connector=pending_hsi_with_connector)


def cancel_actor(sid):
    return db_booking.cancel_actor(sid=sid)


def cancelled_from(sid):
    return db_booking.cancelled_from(sid=sid)


def slot_taken(service, when, sid):
    return db_booking.slot_taken(service=service, when=when, sid=sid)


def mark_attendance(sid, asistencia, operador, origen="sana", exigir_no_futuro=True):
    return db_booking.mark_attendance(sid=sid, asistencia=asistencia, operador=operador, origen=origen, exigir_no_futuro=exigir_no_futuro)


def update_hsi(sid, hsi_result):
    return db_booking.update_hsi(sid=sid, hsi_result=hsi_result)


def reassign_appointment(sid, turno_dt, hsi_result=None, actor=None, slot_capacity=None):
    return db_booking.reassign_appointment(sid=sid, turno_dt=turno_dt, hsi_result=hsi_result, actor=actor, slot_capacity=slot_capacity)


def reschedule_preference(sid, preferencia):
    return db_booking.reschedule_preference(sid=sid, preferencia=preferencia)


def list_admin(estado=None, limit=None, offset=0):
    return db_booking.list_admin(estado=estado, limit=limit, offset=offset)


def count_admin(estado=None):
    return db_booking.count_admin(estado=estado)


def list_by_dni(dni):
    return db_booking.list_by_dni(dni=dni)


def approved_in_range(desde, hasta):
    return db_booking.approved_in_range(desde=desde, hasta=hasta)


def list_approved_future(desde=None, hasta=None):
    return db_booking.list_approved_future(desde=desde, hasta=hasta)


def overbooking_count(desde, hasta, medico, medico_id=None, center_slug=None, exclude_sid=None):
    return db_booking.overbooking_count(desde=desde, hasta=hasta, medico=medico,
                                medico_id=medico_id, center_slug=center_slug,
                                exclude_sid=exclude_sid)


def attendance_summary_dni(dni, limite=3):
    return db_booking.attendance_summary_dni(dni=dni, limite=limite)


def attendance_summary_by_dnis(dnis, limite=3):
    return db_booking.attendance_summary_by_dnis(dnis=dnis, limite=limite)


# --- Holds linkage (slot-holds family, the four booking-coupled fns;
# slot_holds.py itself stays a later slice) ------------------------------

def hold_link_booking_request(token, sid, expira_dt):
    return db_slots.hold_link_booking_request(token=token, sid=sid, expira_dt=expira_dt)


def holds_release_booking_request(sid):
    return db_slots.holds_release_booking_request(sid=sid)


def hold_of_booking_request(sid):
    return db_slots.hold_of_booking_request(sid=sid)


def hold_release(token):
    return db_slots.hold_release(token=token)


# --- Cross-family pass-throughs (documented, re-point when the owning
# family's store exists) ---------------------------------------------------

def waitlist_active(solicitud_id):
    return db_slots.waitlist_active(solicitud_id=solicitud_id)


def get_user(dni):
    return db_accounts.get_user(dni=dni)


def operator_doctor_id(usuario):
    return db_operators.operator_doctor_id(usuario=usuario)


def get_dependent(adulto_dni, dni):
    return db_slots.get_dependent(adulto_dni=adulto_dni, dni=dni)


def interview_for_request(request_id):
    return db_clinical.interview_for_request(request_id=request_id)


def hsi_appointments_by_dni(dni):
    return db_hsi.hsi_appointments_by_dni(dni=dni)


def hsi_sync_status(limit=100):
    return db_hsi.hsi_sync_status(limit=limit)


def hsi_outbox_reset(id_):
    return db_hsi.hsi_outbox_reset(id_=id_)


def refeps_get(matricula):
    return db_catalog.refeps_get(matricula=matricula)


def refeps_set(matricula, items, ok=True):
    return db_catalog.refeps_set(matricula=matricula, items=items, ok=ok)
