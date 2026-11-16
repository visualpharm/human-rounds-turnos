"""api_models.py — Pydantic models of the patient's booking JSON API
(`booking_requests_router.py`, `slot_holds_router.py`, `searches_router.py`,
and the answers of `calendar_feed_router.py`, `appointment_confirm_router.py`,
`appointment_offer_router.py`, `preparation_router.py`, `study_router.py`)
and the family answers to a body those routes cannot read.

What the booking wizard and «Mis turnos» send and what the routes answer.
They document the API (`/openapi.json`, admin-only) and let
`core.api_body` tell a body the pages never send from one they do; the
domain functions still get the body as it came and keep every answer they
gave — see `core/api_body.py`.
"""

from typing import Any

from app.core.api_body import BodyContract, BodyModel, ResponseModel
from app.core.http_json import gzip_json_response
from app.cuentas.api_models import CoverageIn, CoverageOut, OkOut
from app.modules.turnos import booking_requests as bkr

BOOKING = BodyContract(message=bkr._json_invalido, respond=gzip_json_response)
"""POST /api/requests and /api/analyze-order: the translated «JSON inválido: <reason>»."""

PLAIN = BodyContract(message=lambda _reason: "JSON inválido", respond=gzip_json_response,
                     catches=(ValueError, UnicodeError))
"""POST /api/requests/{id}/coverage: «JSON inválido», no reason; only decode errors."""

ADMIN = BodyContract(message=bkr._json_invalido_admin, respond=gzip_json_response)
"""The staff's actions on a request, the HSI conflicts and the settings triad: the
translated admin «JSON inválido: <reason>»."""

WITH_REASON = BodyContract(message=lambda reason: f"JSON inválido: {reason}",
                           respond=gzip_json_response)
"""Reschedule, waitlist, slot hold, search log: the untranslated «JSON inválido: <reason>»."""


# ---- request bodies ----------------------------------------------------------

class ReferralIn(BodyModel):
    """The medical order as a data URL or bare base64."""
    filename: str | None = None
    mime: str | None = None
    data_b64: str | None = None


class BookingRequestIn(BodyModel):
    """The wizard's «Confirmá»: a service at a centre, optionally a held slot,
    an analysed referral (`orden_token`) or the referral itself (`orden`)."""
    servicio_id: str | None = None
    centro_slug: str | None = None
    paciente_dni: str | None = None
    contacto: str | None = None
    preferencia: str | None = None
    caso: str | None = None
    caso_chat: str | None = None
    idem_key: str | None = None
    slot_medico_id: str | None = None
    slot_dia: str | None = None
    slot_hora: str | None = None
    hold_token: str | None = None
    orden_token: str | None = None
    orden: ReferralIn | None = None


class CoverageChoiceIn(BodyModel):
    """After booking: confirm the coverage (`choice` "coverage" with `cobertura`)
    or none (`choice` "none"); `revision` is the one the page was given."""
    choice: str | None = None
    revision: str | None = None
    cobertura: CoverageIn | None = None


class RescheduleIn(BodyModel):
    dia: str | None = None
    hora: str | None = None
    preferencia: str | None = None


class WaitlistIn(BodyModel):
    activo: bool | None = None


class AnalyzeOrderIn(BodyModel):
    servicio_id: str | None = None
    especialidad: str | None = None
    orden: ReferralIn | None = None
    turnstile_token: str | None = None


class SlotHoldIn(BodyModel):
    servicio_id: str | None = None
    dia: str | None = None
    hora: str | None = None
    token: str | None = None


class SearchLogIn(BodyModel):
    q: str | None = None
    resultados: int | None = None
    origen: str | None = None
    sid: str | None = None
    jid: str | None = None


class EventBatchIn(BodyModel):
    """`POST /api/ev`: up to 20 anonymous page/step events (analytics_events.py cleans each one)."""
    events: list[dict] | None = None


class AnalyticsKeyIn(BodyModel):
    """`POST /api/admin/analytics/keys`: a read-only export key, named for whoever will use it."""
    label: str | None = None


class AnalyticsKeyRow(ResponseModel):
    id: int
    label: str
    created_at: str | None = None
    revoked_at: str | None = None
    last_used_at: str | None = None


class AnalyticsKeysOut(ResponseModel):
    """`GET /api/admin/analytics/keys`: the keys (never their value) and whether the stream is on."""
    enabled: bool
    keys: list[AnalyticsKeyRow]


class AnalyticsKeyCreatedOut(ResponseModel):
    """`POST /api/admin/analytics/keys`: the plaintext `key` is shown this once; only its hash is kept."""
    id: int
    label: str
    key: str


class AnalyticsVisitsOut(ResponseModel):
    """`GET /api/admin/reports/visits`: the «Visitas» report (analytics_report.py)."""
    dias: int
    eventos: int
    visitas: int
    vistas: int
    serie: list[dict]
    embudo: list[dict]
    busquedas: list[dict]
    sin_resultados: list[dict]
    rutas: list[dict]
    paginas: list[dict]
    origenes: list[dict]
    dispositivos: list[dict]
    idiomas: list[dict]
    agenda: dict


# ---- answers -----------------------------------------------------------------

class AnalysisOut(ResponseModel):
    """The referral analysis (`ai` of a booking or of /api/analyze-order)."""
    verdict: str
    resumen: str | None = None
    campos: dict | None = None
    documento: dict | None = None


class BookingRequestOut(ResponseModel):
    ok: bool
    id: int
    estado: str
    ai: AnalysisOut | None = None


class CoverageChoiceOut(ResponseModel):
    ok: bool
    supported: bool | None = None
    cobertura: CoverageOut | None = None
    revision: str | None = None
    sin_cobertura: bool | None = None


class WaitlistOut(ResponseModel):
    ok: bool
    activo: bool


class RescheduleOut(ResponseModel):
    """`solicitud`: the request as «Mis turnos» shows it, with its new slot."""
    ok: bool
    solicitud: dict


class AnalyzeOrderOut(ResponseModel):
    ok: bool
    token: str | None = None
    ai: AnalysisOut | None = None


class SlotHoldOut(ResponseModel):
    ok: bool
    token: str
    ttl_min: int


# ---- «Mis turnos»: the patient's own requests --------------------------------

class PatientRequest(ResponseModel):
    """A request as the patient sees it (`booking_requests._public_row`): never
    the referral's reading, the contact or the EMR's ids. `turno_dt` the
    appointment (local time, ISO), `turno_pedido` the one the request asked
    for, `hold` the slot a pending request holds, `entrevista` the pre-visit
    interview it offers, `cancelada_por` who cancelled it. An appointment the
    EMR holds for the patient comes as `hsi_imported`, with an "hsi-<id>" id
    and the specialty as `servicio_nombre`. The detail adds whether the
    patient asked to hear of an earlier slot (`lista_espera_activo`)."""
    id: int | str
    servicio_id: str
    estado: str
    turno_dt: str | None
    turno_pasado: bool
    medico_asignado: str | None
    created: str | None
    centro_slug: str | None = None
    preferencia_horario: str | None = None
    motivo_rechazo: str | None = None
    turno_pedido: str | None = None
    medico_asignado_id: str | None = None
    paciente_dni: str | None = None
    paciente_nombre: str | None = None
    preparacion: list = []
    hold: dict | None = None
    cancelada_por: str | None = None
    entrevista: dict | None = None
    lista_espera_activo: bool | None = None
    servicio_nombre: str | None = None
    hsi_imported: bool | None = None


class PatientRequestsOut(ResponseModel):
    solicitudes: list[PatientRequest]


class PatientRequestOut(ResponseModel):
    solicitud: PatientRequest


class CancelOut(ResponseModel):
    ok: bool
    solicitud: PatientRequest


class CoverageStateOut(ResponseModel):
    """The coverage a request can be booked with: the account's own
    (`cobertura`, `revision` to send back with a change) or the declared lack
    of one (`sin_cobertura`); `supported` false, with a `reason`, when this
    request cannot change it ("dependent", "identity")."""
    supported: bool
    cobertura: CoverageOut | None = None
    revision: str | None = None
    sin_cobertura: bool | None = None
    reason: str | None = None


class CalendarFeedOut(ResponseModel):
    """The patient's own calendar address: `url` only when it is created (the
    database keeps its hash), then null; `hint` its last four characters."""
    url: str | None
    hint: str


class ConfirmAnswerOut(ResponseModel):
    """«¿Vas a poder venir?» answered: `confirmacion` "si" or "no", and the sentence to show."""
    ok: bool
    confirmacion: str
    mensaje: str


class OfferAcceptOut(ResponseModel):
    """The offered slot is taken: the request it now belongs to (null when the
    offer's waitlist entry names none)."""
    ok: bool
    solicitud_id: int | None


class PreparationStudy(ResponseModel):
    """How to come to a study: fasting, what to bring, the steps, and the
    sources the advice comes from; `match` the words a referral is matched by."""
    id: str
    nombre: str
    match: list[str] | None = None
    ayuno: dict | None = None
    traer: list[str] | None = None
    preparacion: list[str] | None = None
    no_preparation_needed: bool | None = None
    sources: list[dict] | None = None


class PreparationStudiesOut(ResponseModel):
    estudios: list[PreparationStudy]


# ---- the usability study (demo only) ------------------------------------------

class StudySessionOut(ResponseModel):
    """Where the participant's session runs."""
    url: str


class StudyEventsOut(ResponseModel):
    accepted: int


class StudyResultOut(ResponseModel):
    task_id: str


class StudyFinishOut(ResponseModel):
    finished: bool


# ---- the staff's actions on a request (stage 5) ------------------------------

class ApproveIn(BodyModel):
    """`turno_dt`: the appointment, local time; `sobreturno`: on top of a taken slot."""
    turno_dt: str | None = None
    servicio_id: str | None = None
    medico: str | None = None
    sobreturno: bool | None = None


class AssignIn(BodyModel):
    """The doctor, by id (preferred) or by the name the catalog shows."""
    medico_id: str | None = None
    medico: str | None = None


class RejectIn(BodyModel):
    motivo: str | None = None


class EditFieldIn(BodyModel):
    """One field the referral analysis read (`campo`: dni, paciente, especialidad,
    diagnostico, medico, matricula or fecha); `valor` null clears it."""
    campo: str | None = None
    valor: str | None = None


class PatientDataIn(BodyModel):
    """Any subset of the four; what the account already has is not asked again."""
    nombre: str | None = None
    apellido: str | None = None
    nacimiento: str | None = None
    sexo: str | None = None


class AttendanceIn(BodyModel):
    asistencia: str | None = None


class HsiConflictsIn(BodyModel):
    accion: str | None = None
    conflictos: list[int | str] | None = None


class SuggestedService(ResponseModel):
    """The service the referral asks for, when it is not the one booked."""
    id: str
    nombre: str
    centro: str | None = None
    centro_nombre: str | None = None
    motivo: str | None = None


class StaffRequest(ResponseModel):
    """A booking request as the staff see it: the `booking_requests` row (the
    referral's path on disk left out; dates ISO, the appointment in the
    installation's time) plus what the panel needs. `cancelada_por` (paciente
    or centro) only on a cancelled request; `servicio_sugerido` only when the
    referral asks for another service."""
    id: int
    estado: str
    dni: str | None = None
    nombre: str | None = None
    apellido: str | None = None
    nacimiento: str | None = None
    sexo: str | None = None
    contacto: str | None = None
    servicio_id: str | None = None
    centro_slug: str | None = None
    orden_mime: str | None = None
    ai_json: dict | None = None
    motivo_rechazo: str | None = None
    turno_dt: str | None = None
    hsi_patient_id: str | None = None
    hsi_appointment_id: str | None = None
    hsi_estado: str | None = None
    asistencia: str | None = None
    asistencia_actualizada_en: str | None = None
    asistencia_actualizada_por: str | None = None
    sobreturno: bool
    created: str | None = None
    preferencia_horario: str | None = None
    solicitante_dni: str | None = None
    medico_asignado: str | None = None
    medico_asignado_id: str | None = None
    recordatorio_dt: str | None = None
    entrevista_recordatorio_dt: str | None = None
    resuelta_por: str | None = None
    resuelta_en: str | None = None
    confirmacion: str | None = None
    confirmacion_en: str | None = None
    confirmacion_canal: str | None = None
    cancelada_en: str | None = None
    slot_medico_id: str | None = None
    nota_paciente: str | None = None
    reminder_skipped: bool
    cancelada_por: str | None = None
    orden_url: str
    tiene_orden: bool
    sin_medico: bool
    medico_nombre: str
    servicio_sugerido: SuggestedService | None = None


class StaffRequestOut(ResponseModel):
    """`solicitud`: the request as the review queue shows it."""
    ok: bool
    solicitud: StaffRequest


class PatientDataOut(ResponseModel):
    ok: bool
    paciente: dict


# ---- the review queue and the rest of the staff's work on a request ---------

class Ranking(ResponseModel):
    """How the patient keeps appointments (`patient_ranking.py`): `banda`
    sin-historial, viene-siempre, falta-a-veces or falta-seguido; `nivel` none,
    good, warn or bad."""
    puntaje: int
    banda: str
    etiqueta: str
    nivel: str
    n: float
    hechos: int
    ultimo: str | None = None
    sin_historial: bool
    vino: int
    falto: int
    cancelo: int
    redundante: int
    motivos: list[str]
    detalle: str


class AttendanceEvent(ResponseModel):
    """One change of a patient's attendance mark."""
    solicitud_id: int | None = None
    dni: str
    de_asistencia: str | None = None
    a_asistencia: str
    operador: str
    origen: str
    created: str


class AttendanceSummary(ResponseModel):
    """The patient's attendance: visits, shows, no-shows (`faltas_hsi`: those
    HSI reported), whether web booking is blocked, the last changes."""
    visitas: int
    presentes: int
    faltas: int
    faltas_hsi: int | None = None
    bloqueado_web: bool
    historial: list[AttendanceEvent]


class CaseSummary(ResponseModel):
    """«Contanos tu caso»: what the patient wants, how urgent (emergencia, alta,
    media, baja), why, the key facts; `fuente` ia or texto. Empty when the
    conversation has no summary."""
    quiere: str | None = None
    urgencia: str | None = None
    por_que: str | None = None
    hechos: list[str] | None = None
    fuente: str | None = None


class RequestedSlot(ResponseModel):
    """The slot the patient picked, and whether it is still free."""
    dia: str
    hora: str
    libre: bool


class SuggestedDoctor(ResponseModel):
    """The service's next free doctor; `pedido` when it is the patient's own slot."""
    nombre: str
    slot_iso: str
    pedido: bool | None = None


class QueueRequest(StaffRequest):
    """A request of the queue: the staff row plus the patient's attendance and
    ranking, the case chat's summary when there is one, and — on a pending
    request — the slot asked for, the doctor asked for and the one suggested."""
    asistencia_resumen: AttendanceSummary
    no_show_limit: int
    ranking: Ranking
    caso_resumen: CaseSummary | None = None
    caso_chat: bool | None = None
    turno_pedido: RequestedSlot | None = None
    medico_pedido: str | None = None
    medico_sugerido: SuggestedDoctor | None = None
    resuelta_por_nombre: str | None = None


class HsiInstall(ResponseModel):
    """Where the desk installs the HSI extension: the store, the zip, the guide."""
    tienda: str | None = None
    zip: str | None = None
    guia: str | None = None


class QueueOut(ResponseModel):
    """The review queue. `total` counts the requests of the status asked for (a
    doctor's list may show fewer); without an EMR push, `hsi_extension` and
    `hsi_instalar` say where the extension that loads appointments into HSI is;
    `medicos_por_servicio`: the doctors each service may be given."""
    solicitudes: list[QueueRequest]
    total: int
    emr_sync_active: bool
    hsi_extension: str | None = None
    hsi_instalar: HsiInstall | None = None
    medicos_por_servicio: dict[str, list[str]]


class CaseMessage(ResponseModel):
    """`role` paciente or asistente; `opciones`: the answers the assistant offered."""
    role: str
    texto: str
    opciones: list[str] | None = None


class CaseChatOut(ResponseModel):
    resumen: CaseSummary
    mensajes: list[CaseMessage]


class AttendedRequest(StaffRequest):
    asistencia_resumen: AttendanceSummary
    no_show_limit: int
    ranking: Ranking


class AttendanceOut(ResponseModel):
    """`hsi_sync`: the mark's push to HSI (its state, or sin_turno_hsi_vinculado)."""
    ok: bool
    solicitud: AttendedRequest
    hsi_sync: str


class ReadField(ResponseModel):
    """One field the referral analysis read; `editado` once the staff changed it,
    `coincide` whether it matches the account (dni, paciente)."""
    valor: str | None = None
    editado: bool | None = None
    coincide: bool | None = None


class FieldsOut(ResponseModel):
    """Every field of the referral analysis after the edit."""
    ok: bool
    campos: dict[str, ReadField]


class RefepsLicense(ResponseModel):
    matricula: str | int | None = None
    profesion: str | None = None
    especialidad: str | None = None
    origen: str | None = None
    provincia: str | None = None
    situacion: str | None = None
    sancion: str | None = None


class RefepsPerson(ResponseModel):
    """A professional in the national registry (REFEPS), as it answers."""
    apellido: str | None = None
    nombre: str | None = None
    dni: str | int | None = None
    sexo: str | None = None
    activo: bool | None = None
    matriculas: list[RefepsLicense]


class RefepsOut(ResponseModel):
    """The license looked up (`source` cache, live or cache-stale, with the
    lookup's `error`): the best match for the doctor's name and up to four others."""
    matricula: str
    source: str
    checked: str | None = None
    found: bool
    match: RefepsPerson | None = None
    candidatos: int
    otros: list[RefepsPerson]
    error: str | None = None


# ---- the EMR (HSI) synchronization --------------------------------------------

class HsiMetrics(ResponseModel):
    """Approved appointments already in HSI: the future ones and all; `outbox` by state."""
    futuras: int
    futuras_ok: int
    pct_futuras: int | None = None
    total: int
    total_ok: int
    pct_total: int | None = None
    outbox: dict[str, int]


class HsiResolution(ResponseModel):
    accion: str
    label: str


class HsiConflict(ResponseModel):
    """An open conflict and the resolutions the panel offers."""
    conflicto_id: int
    solicitud_id: int | None = None
    fecha: str | None = None
    paciente: str | None = None
    medico: str | None = None
    problema: str
    cluster: str
    resoluciones: list[HsiResolution]
    created: str | None = None


class HsiResolved(ResponseModel):
    solicitud_id: int | None = None
    texto: str
    cuando: str | None = None


class HsiPanel(ResponseModel):
    metricas: HsiMetrics
    abiertos: int
    conflictos: list[HsiConflict]
    resueltos: list[HsiResolved]


class HsiSyncOut(ResponseModel):
    """The push queue by state, its unconfirmed rows and the open conflicts (raw
    rows, up to 100 each), and the panel of the resolver when HSI is on."""
    counts: dict[str, int]
    outbox: list[dict]
    conflicts: list[dict]
    hsi_activo: bool
    panel: HsiPanel | None = None


class HsiResolverOut(ResponseModel):
    """`resumen`: what the resolver did, by kind (resueltos, revinculados, reintentos_ok…)."""
    ok: bool
    resumen: dict[str, int]
    panel: HsiPanel | None = None


class HsiConflictResult(ResponseModel):
    conflicto_id: int
    solicitud_id: int | None = None
    ok: bool
    detalle: str


class HsiConflictsOut(ResponseModel):
    ok: bool
    resultados: list[HsiConflictResult]
    panel: HsiPanel | None = None


class ExternalHistoryOut(ResponseModel):
    """The patient's history in the EMR, read on demand. `reason` empty when
    `available`; otherwise flag_off, not_supported, not_deployed or error."""
    available: bool
    reason: str
    items: list[dict]


# ---- the doctors' appointments and the day queue ---------------------------------

class CompatibleService(ResponseModel):
    """A service the doctor may be given; `propio` when it is already theirs."""
    id: str
    nombre: str
    centro: str | None = None
    centro_nombre: str | None = None
    propio: bool
    usos_especialidad: int


class CompatibleServicesOut(ResponseModel):
    items: list[CompatibleService]


class DoctorRef(ResponseModel):
    id: str
    nombre: str


class AppointmentInterview(ResponseModel):
    """The pre-visit interview of the appointment."""
    id: int
    triage: str | None = None
    resumen: str | None = None
    estado: str
    actualizada: bool
    consulta_confirmada: bool


class StaffAppointment(ResponseModel):
    """An approved appointment on a doctor's agenda: the patient, how to reach
    them, where, the referral and the attendance. `ranking` and `entrevista`
    where the screen shows them."""
    id: int
    turno_dt: str
    dia_iso: str
    hora: str
    paciente: str
    dni: str | None = None
    nacimiento: str | None = None
    sexo: str | None = None
    contacto: str | None = None
    contacto_de: str | None = None
    contacto_responsable: str | None = None
    contacto_cuenta: str | None = None
    contacto_cuenta_de: str | None = None
    solicitado_en: str | None = None
    cobertura: str | None = None
    sobreturno: bool
    servicio: str
    consultorio: str | None = None
    centro: str
    centro_id: str | None = None
    hsi_institucion: Any = None
    preparacion: list
    hsi_patient_url: str | None = None
    hsi_url: str | None = None
    asistencia: str | None = None
    asistencia_en: str | None = None
    asistencia_por: str | None = None
    confirmacion: str | None = None
    tiene_orden: bool
    orden_url: str | None = None
    orden_mime: str | None = None
    ai_campos: dict | None = None
    elegible_asistencia: bool
    ranking: Ranking | None = None
    entrevista: AppointmentInterview | None = None


class DoctorDay(ResponseModel):
    dia: str
    capacidad: int
    ocupados: int


class DoctorAppointmentsOut(ResponseModel):
    """A doctor's next `dias` days: the appointments and each day's capacity."""
    medico: DoctorRef
    dias: int
    turnos: list[StaffAppointment]
    dias_detalle: list[DoctorDay]
    total: int
    hsi_url: str | None = None


class DoctorStats(ResponseModel):
    """A doctor's week and next 14 days, for the doctors list."""
    horas_semana: float
    proximo_dia: str | None = None
    proximo_dia_iso: str | None = None
    turnos_14d: int
    slots_14d: int
    ocupacion_14d: int | None = None
    tiene_foto: bool
    tiene_login: bool
    matricula: str | None = None
    servicios: int


class DoctorsStatsOut(ResponseModel):
    """By doctor id."""
    medicos: dict[str, DoctorStats]


class MyAppointmentsOut(ResponseModel):
    """The session doctor's next `dias` days (`medico` null when the account's
    doctor is not in the catalog)."""
    medico: DoctorRef | None = None
    dias: int
    turnos: list[StaffAppointment]
    entrevista_activa: bool | None = None
    tiene_horarios: bool
    total: int


class DoctorFeedOut(ResponseModel):
    """The doctor's calendar feed: `url` only when it is issued (then never
    again), `hint` its last characters."""
    url: str | None = None
    hint: str | None = None


class QueueInterview(ResponseModel):
    """A pre-visit interview of the day with no appointment."""
    id: int
    nombre: str | None = None
    hora: str | None = None
    triage: str | None = None
    resumen: str | None = None
    estado: str
    actualizada: bool
    consulta_confirmada: bool


class InterviewConfig(ResponseModel):
    """Whether the pre-visit interview is on for the doctor (`override`: their own
    choice) and the poster that invites patients to it."""
    efectivo: bool
    override: bool | None = None
    cartel_url: str


class DayQueueGroup(ResponseModel):
    """One doctor's appointments of the day and the free times (`libres`, HH:MM)."""
    medico_id: str | None = None
    medico: str | None = None
    especialidad: str | None = None
    centro: str | None = None
    centros: list[str] | None = None
    turnos: list[StaffAppointment]
    libres: list[str]


class DayQueueOut(ResponseModel):
    """«Pacientes de hoy» (or the day asked for): by doctor. A doctor's own
    queue adds the interviews without an appointment and the interview's
    setting; the desk's, the centres and where the HSI extension is."""
    dia: str
    hoy: bool
    grupos: list[DayQueueGroup]
    total: int
    centros: list[str] | None = None
    hsi_instalar: HsiInstall | None = None
    entrevistas_sin_turno: list[QueueInterview] | None = None
    entrevista_config: InterviewConfig | None = None


class QueueCalendarOut(ResponseModel):
    """The days around the one asked for: how many appointments each has, which
    need attention, the previous and next day with appointments."""
    desde: str
    hasta: str
    dia: str
    dias: list[str]
    conteos: dict[str, int]
    atencion: list[str]
    anterior: str | None = None
    siguiente: str | None = None


class ChangedAppointment(StaffAppointment):
    medico: str | None = None
    especialidad: str | None = None


class QueueChange(ResponseModel):
    """An appointment of the day that changed; `oculto` once the staff hid it."""
    id: int
    estado: str | None = None
    turno: ChangedAppointment | None = None
    oculto: bool


class QueueChangesOut(ResponseModel):
    ahora: str
    cambios: list[QueueChange]


# ---- settings triad (stage 7) -------------------------------------------------

class ThemeIn(BodyModel):
    tema: str | None = None


class AuthModeIn(BodyModel):
    auth_mode: str | None = None


class TurnstileIn(BodyModel):
    on: bool | None = None


class ThemeOut(ResponseModel):
    ok: bool
    tema: str


class AuthModeOut(ResponseModel):
    ok: bool
    auth_mode: str


class TurnstileOut(ResponseModel):
    ok: bool
    on: bool


# ---- the usability study (`study_router.py`, demo installations only) ---------

class StudySessionIn(BodyModel):
    """Taking part: `consent` must be true, `role` one of `patient`, `doctor`, `admin`."""
    consent: bool | None = None
    role: str | None = None


class StudyTargetIn(BodyModel):
    tag: str | None = None
    interactive: bool | None = None
    index: int | None = None


class StudyEventIn(BodyModel):
    """One interaction during a task: what kind (`click`, `nav`, `dead_click`, `scroll_stop`,
    `visibility`), when (ms since the task started), where (path) and on what (the element's tag, never
    its text)."""
    task_id: str | None = None
    kind: str | None = None
    t: int | None = None
    path: str | None = None
    target: StudyTargetIn | None = None


class StudyEventsIn(BodyModel):
    """1 to 100 events."""
    events: list[StudyEventIn] | None = None


class StudyResultIn(BodyModel):
    """How a task went: `outcome` (`ok`, `doubt`, `stuck`, `skipped`), `ease` 1-5, a comment and how long
    it took."""
    task_id: str | None = None
    outcome: str | None = None
    ease: int | None = None
    comment: str | None = None
    duration_ms: int | None = None


class StudyAnswersIn(BodyModel):
    """The closing questions: two free texts and `F3` (`yes`, `maybe`, `no` or empty)."""
    F1: str | None = None
    F2: str | None = None
    F3: str | None = None


class StudyFinishIn(BodyModel):
    answers: StudyAnswersIn | None = None


class ThemeStateOut(ResponseModel):
    """The site's theme and the catalog to choose from."""
    tema: str
    themes: list[dict]


class AuthModeStateOut(ResponseModel):
    """The patients' sign-in mode, its catalog, the RENAPER provider ("real"
    or "stub") and whether a RENAPER sign-in can start."""
    auth_mode: str
    modes: list[dict]
    renaper_provider: str
    renaper_conectado: bool


class TurnstileStateOut(ResponseModel):
    """The anti-bot switch, its catalog and why it may not run: no secret
    (`configurado`), a development host, or forced off by the environment."""
    on: bool
    options: list[dict]
    configurado: bool
    host_dev: bool
    forzado_off_env: bool
