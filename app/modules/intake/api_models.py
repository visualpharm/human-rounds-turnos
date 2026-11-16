"""api_models.py — Pydantic models of the patient's pre-consult interview JSON
API (`interview_patient_router.py`) and the family answer to a body those
routes cannot read. They document the API and let `core.api_body` tell a
body the interview page never sends from one it does; the domain still gets
the body as it came — see `core/api_body.py`.

Also the success answers of the interview, the doctor's review of it
(`consult_admin_router.py`) and the interview settings and red alerts
(`intake_admin_router.py`); they only document: the handlers return their
ready `Response`.
"""

from app.core.api_body import BodyContract, BodyModel, ResponseModel
from app.modules.intake._native_support import json_response

BODY = BodyContract(message=lambda reason: f"JSON inválido: {reason}",
                    respond=lambda _request, payload, status: json_response(payload, status=status))
"""The 400 «JSON inválido: <reason>» the interview routes always answered."""


class QrStartIn(BodyModel):
    """The patient's DNI typed at the doctor's door (QR)."""
    dni: str | None = None


class AttachmentIn(BodyModel):
    """A photo the patient attaches to a message (base64 or a data URL)."""
    data_b64: str | None = None
    mime: str | None = None
    filename: str | None = None


class InterviewMessageIn(BodyModel):
    texto: str | None = None
    adjunto: AttachmentIn | None = None


class QrStartOut(ResponseModel):
    """The interview's own token: the page continues at /entrevista/…?t=<it>."""
    patient_token: str


class InterviewMessageOut(ResponseModel):
    """The interviewer's next turn; `fin` closes the interview."""
    texto: str
    fin: bool
    opciones: list


class CaseMessageIn(BodyModel):
    """One answer of «Contanos tu caso» (a tapped option arrives as its text)."""
    texto: str | None = None


class CaseStartOut(ResponseModel):
    """The case chat's token and opening question; `disponible` false means
    no model can ask right now and the wizard keeps the plain box."""
    disponible: bool
    token: str | None = None
    texto: str | None = None
    fin: bool | None = None
    opciones: list | None = None


class ConsultConfirmIn(BodyModel):
    """The doctor's review of the pre-consult: what they keep, and the three
    `*_reviewed` snapshots the confirmation must match (409 otherwise)."""
    diagnosticos: list | None = None
    estudios: list | None = None
    medicacion: list | None = None
    notas: str | None = None
    profile_reviewed: bool | None = None
    profile: dict | None = None
    patient_reviewed: dict | None = None
    choices_reviewed: dict | None = None
    reemplaza_id: int | None = None


class ConsultConfirmOut(ResponseModel):
    """Links to the prescription and the order the confirmation issued."""
    ok: bool
    receta_url: str
    orden_url: str


SETTINGS = BodyContract(message=lambda reason: f"JSON inválido: {reason}",
                        respond=lambda _request, payload, status: json_response(payload, status=status))
"""The interview settings: a body that does not decode is a 400 «JSON inválido: <reason>»
(it used to be an uncaught 500)."""


class IntakeSettingsIn(BodyModel):
    """The interview switch: for the whole installation (`sistema`), for some
    doctors (`medico_ids` + `enabled`), or one doctor's `columnas`/`override`."""
    sistema: bool | None = None
    medico_ids: list[str] | None = None
    enabled: bool | None = None
    medico_id: str | None = None
    columnas: list | None = None
    override: bool | None = None


class IntakeMySettingsIn(BodyModel):
    override: bool | None = None


# ---- answers ------------------------------------------------------------------

class InterviewLine(ResponseModel):
    """One message of the interview as the patient saw it: `role` "user" or
    "assistant", the text without the agent's markers, and the options of the
    question that brought them (only on that message)."""
    role: str
    texto: str
    opciones: list[str] | None = None


class InterviewTranscriptOut(ResponseModel):
    """The interview's state (`active`, `done`, ...) and its messages; a first
    visit to an active interview is answered with its opening question."""
    status: str
    mensajes: list[InterviewLine]


class DoorLinkOut(ResponseModel):
    """A new door QR for the doctor: the poster's address, the old QR stops working."""
    ok: bool
    url: str


class IntakeColumn(ResponseModel):
    """A column of «Pacientes de hoy» a doctor's account may show."""
    key: str
    label: str
    default: bool


class IntakeDoctorRow(ResponseModel):
    """A doctor of the interview settings: the override as saved (null =
    inherit the installation's), what `enabled_for` decides, the poster's
    links (`pdf_url` only while the interview is on), whether a door QR exists,
    the linked account and its saved «Pacientes de hoy» columns."""
    id: str
    nombre: str
    especialidad: str
    override: bool | None
    efectivo: bool
    cartel_url: str
    pdf_url: str | None
    tiene_token: bool
    cuenta: str | None
    columnas: list[str] | None


class IntakeSettingsOut(ResponseModel):
    """The installation's switch, the columns a doctor may choose and every doctor."""
    enabled_system: bool
    columnas_disponibles: list[IntakeColumn]
    medicos: list[IntakeDoctorRow]


class IntakeSettingsSavedOut(ResponseModel):
    """A saved interview setting: `ok` alone for the installation's switch or one
    doctor's override; the doctors saved (`medico_ids`, `enabled`, and how many of
    the selection were skipped, `errores`) for a selection; the columns kept, in
    order and once each, for an account's «Pacientes de hoy»."""
    ok: bool
    medico_ids: list[str] | None = None
    enabled: bool | None = None
    errores: int | None = None
    columnas: list[str] | None = None


class IntakeMySettingsOut(ResponseModel):
    """The signed-in doctor's own interview switch and poster links."""
    override: bool | None
    efectivo: bool
    cartel_url: str
    pdf_url: str | None


class RedAlertCountOut(ResponseModel):
    """Red-triage interviews not yet seen, among the doctors the session may see."""
    count: int


class RedAlert(ResponseModel):
    """A red-triage interview: who (name, else «DNI …», else «Sin nombre»), the
    contact, the service and the doctor, when, and the request it came from.
    Each detail is best-effort and may be null."""
    id: int
    triage: str | None
    paciente: str
    dni: str | None
    contacto: str | None
    servicio: str | None
    medico_id: str | None
    medico: str | None
    fecha: str | None
    request_id: int | None


class RedAlertsOut(ResponseModel):
    items: list[RedAlert]


class ConsultDoctor(ResponseModel):
    """The doctor responsible, as the printables head it; empty when the doctor
    is not in the catalog."""
    nombre: str | None = None
    especialidad: str | None = None
    matricula: str | None = None


class ConsultMessage(ResponseModel):
    """A message of the transcript the doctor reads: `content` is the text, or the
    patient's message parts when they attached a photo."""
    role: str
    content: str | list | None


class ConsultCatalogs(ResponseModel):
    """The choices of the review form for the doctor's specialty."""
    estudios: list[dict]
    diagnosticos: list[dict]
    medicamentos: list[dict]
    frecuencias: list[str]


class ConfirmedConsultation(ResponseModel):
    """The consultation already issued for the interview, read-only with its
    links; `seleccion` is what was chosen, as saved, for «Reemplazar»."""
    id: int
    confirmada_at: str | None
    confirmada_por: str | None
    diagnosticos: list
    estudios: list
    medicacion: list
    notas: str
    receta_url: str
    orden_url: str
    reemplazos: int
    seleccion: dict


class ConsultOut(ResponseModel):
    """The pre-consult interview the doctor reviews: who, how it came in, the
    AI's reading of it (triage, summary, brief, differential, suggested studies,
    red flags), the transcript, the form's catalogs, what the patient has told in
    earlier interviews and its proposed update, and the consultation already
    confirmed (or null). Timestamps are ISO in the installation's time zone."""
    id: int
    doctor_id: str | None
    request_id: int | None
    entry_mode: str | None
    entered_name: str | None
    entered_surname: str | None
    matched: bool
    nombre: str
    dni: str | None
    medico: ConsultDoctor
    session_key: str | None
    status: str | None
    triage: str | None
    summary: str | None
    brief: dict | list
    differential: list
    suggested_exams: list
    red_flags: list
    ai_model: str | None
    analyzed_at: str | None
    reopened_at: str | None
    created: str | None
    updated: str | None
    mensajes: list[ConsultMessage]
    catalogos: ConsultCatalogs
    perfil: dict
    perfil_propuesta: dict
    confirmada: ConfirmedConsultation | None
