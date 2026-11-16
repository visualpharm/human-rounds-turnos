"""api_models.py — Pydantic models of the admin panel's JSON API and the
family answer to a body those routes cannot read. They document the API and
let `core.api_body` tell a body the panel never sends from one it does; the
domain still gets the body as it came — see `core/api_body.py`.
"""

from typing import Any

from pydantic import Field, RootModel

from app.core import i18n
from app.core.api_body import BodyContract, BodyModel, ResponseModel
from app.core.http_json import gzip_json_response, json_response
from app.modules.turnos.api_models import HsiInstall, Ranking

ADMIN = BodyContract(message=lambda reason: f"JSON inválido: {reason}",
                     respond=lambda _request, payload, status: json_response(payload, status=status))
"""The 400 «JSON inválido: <reason>» the panel's own routes always answered."""

ADMIN_GZIP = BodyContract(message=lambda reason: f"JSON inválido: {reason}",
                          respond=gzip_json_response)
"""The same, for the routes that gzip (`admin_panel.native_json`)."""

ADMIN_I18N = BodyContract(
    message=lambda reason: i18n.t_or("admin.srv.error.json_invalido", "JSON inválido: {motivo}",
                                     i18n.active(), motivo=reason),
    respond=gzip_json_response)
"""Staff accounts and roles: the translated admin «JSON inválido: <reason>», gzip."""


# ---- own account and preferences ---------------------------------------------

class AccountProfileIn(BodyModel):
    email: str | None = None


class AccountPasswordIn(BodyModel):
    """`usuario`: a new username, when the account changes it with the password."""
    actual: str | None = None
    nueva: str | None = None
    usuario: str | None = None


class TotpStartIn(BodyModel):
    """`actual`: the current password, asked when the account already has an authenticator."""
    actual: str | None = None


class TotpConfirmIn(BodyModel):
    """The `setup_token` of /2fa/start and a code from the authenticator app."""
    setup_token: str | None = None
    codigo: str | None = None


class TotpRemoveIn(BodyModel):
    actual: str | None = None


class PreferenceIn(BodyModel):
    """One panel preference of the signed-in staff member (`value`: any JSON)."""
    key: str | None = None
    value: Any = None


class DoctorProfileIn(BodyModel):
    """A doctor's own public profile, edited on Cuenta. Never an id: the
    server edits the doctor linked to the session, nothing else."""
    nombre_pila: str | None = None
    apellido: str | None = None
    especialidad: str | None = None
    telefono: str | None = None


class AccountProfileOut(ResponseModel):
    ok: bool
    email: str | None


class AccountPasswordOut(ResponseModel):
    """`usuario` only when the username changed (a new session cookie comes with it)."""
    ok: bool
    usuario: str | None = None


class TotpOut(ResponseModel):
    """`2fa_deadline`: when 2FA becomes mandatory again after removing it."""
    ok: bool
    deadline_2fa: str | None = Field(default=None, alias="2fa_deadline")


class PreferenceOut(ResponseModel):
    ok: bool
    value: Any = None


# ---- desk appointment (stage 5) ----------------------------------------------

class DeskAppointmentIn(BodyModel):
    """A same-day appointment the desk books for a patient by DNI."""
    dni: str | None = None
    servicio_id: str | None = None
    dia: str | None = None
    hora: str | None = None
    medico: str | None = None
    nota: str | None = None
    sobreturno: bool | None = None


class DeskHsi(ResponseModel):
    """The EMR push of the new appointment (`estado` "skip" with no connector),
    or, without one, the extension that loads it into HSI."""
    conectado: bool
    estado: str
    extension: str | None = None
    instalar: HsiInstall | None = None


class DeskAppointmentOut(ResponseModel):
    ok: bool
    id: int
    sobreturno: bool
    sin_ficha: bool | None = None
    turno_dt: str | None = None
    paciente: str | None = None
    servicio: str | None = None
    hsi: DeskHsi | None = None


# ---- patients, staff accounts and roles (stage 6) ----------------------------

class PatientIn(BodyModel):
    """The desk's patient form; only the keys present change on edit."""
    dni: str | None = None
    password: str | None = None
    nombre: str | None = None
    apellido: str | None = None
    contacto: str | None = None
    email: str | None = None
    sexo: str | None = None
    nacimiento: str | None = None
    activo: bool | None = None


class PatientDependentIn(BodyModel):
    dni: str | None = None
    nombre: str | None = None
    apellido: str | None = None
    nacimiento: str | None = None
    sexo: str | None = None
    vinculo: str | None = None


class ClinicalHistoryIn(BodyModel):
    """Records of the patient's clinical history imported from `source_system`."""
    records: list | None = None
    source_system: str | None = None


class StaffUserIn(BodyModel):
    """A staff account: create (`access_mode` "invite" sends a link instead of a
    password) or edit (only the keys present change; `totp` "reset"/"remove")."""
    usuario: str | None = None
    password: str | None = None
    actual: str | None = None   # the caller's own current password, when editing their own account
    access_mode: str | None = None
    rol: str | None = None
    email: str | None = None
    medico_id: str | None = None
    centros: list[str] | None = None
    activo: bool | None = None
    totp: bool | str | None = None
    forzar_2fa: bool | None = None
    permisos_agregados: list[str] | None = None
    permisos_quitados: list[str] | None = None


class StaffUsersActionIn(BodyModel):
    """The doctors (`medico_ids`) a bulk action applies to; `emails` by doctor id."""
    medico_ids: list[str] | None = None
    emails: dict[str, str] | None = None
    usuario: str | None = None
    email: str | None = None


class RoleIn(BodyModel):
    name: str | None = None
    description: str | None = None
    permissions: list[str] | None = None
    enabled: bool | None = None


class ItemOut(ResponseModel):
    """`item`: the saved row as the panel lists it."""
    ok: bool
    item: dict


class ClinicalHistoryOut(ResponseModel):
    ok: bool
    dni: str
    source_system: str
    count: int


class StaffUsersActionOut(ResponseModel):
    """One result per doctor (`ok` false with its `error` when it did not apply)
    and how many applied or failed."""
    ok: bool
    resultados: list[dict]
    completados: int
    errores: int


class RoleOut(ResponseModel):
    """`locked`: the admin role, never edited; `users`: the accounts holding it."""
    slug: str
    name: str
    description: str | None = None
    builtin: bool
    enabled: bool
    locked: bool
    users: int
    permissions: list[str] | None = None


# ---- answers of the patients screen, the desk and the doctor's workspace -----

class PatientRow(ResponseModel):
    """A patient account as the panel shows it (the `users` row, its secrets
    left out); dates are ISO strings."""
    id: int
    dni: str
    nombre: str | None = None
    apellido: str | None = None
    nacimiento: str | None = None
    sexo: str | None = None
    contacto: str | None = None
    email: str | None = None
    created: str | None = None
    updated: str | None = None
    identity_kind: str
    activo: bool | None = None
    cobertura: dict | None = None
    domicilio: dict | None = None
    avisos: dict | None = None
    renaper: dict | None = None
    lang: str | None = None
    coverage_revision: str


class PatientOut(ResponseModel):
    """The saved patient. `password_temporal`: the password the server made up
    when the form gave none; `turnos_cancelados`: the appointments cancelled
    with an archive."""
    ok: bool
    item: PatientRow | None
    password_temporal: str | None = None
    turnos_cancelados: int | None = None


class DependentAdult(ResponseModel):
    """An adult a dependent is linked to."""
    dni: str
    nombre: str | None = None
    apellido: str | None = None
    vinculo: str
    identity_kind: str | None = None


class PatientDependent(ResponseModel):
    """A dependent (`vinculo`: hijo, hija, tutelado, tutelada or otro) and the adults it is linked to."""
    id: int
    dni: str
    nombre: str
    apellido: str
    nacimiento: str
    sexo: str
    vinculo: str
    declaracion_jurada_at: str | None = None
    created: str | None = None
    updated: str | None = None
    identity_kind: str | None = None
    adultos: list[DependentAdult] = []


class PatientDependentOut(ResponseModel):
    ok: bool
    item: PatientDependent


class DependentDeleteOut(ResponseModel):
    """`adultos`: the adults the dependent is still linked to."""
    ok: bool
    adultos: list[DependentAdult]


class TempPasswordOut(ResponseModel):
    ok: bool
    password_temporal: str


class DniClaimResolveIn(BodyModel):
    """The desk's decision on one open claim: `aprobar` true confirms (or hands the number to the claimant)."""
    aprobar: bool | None = None
    nota: str | None = None


class DniClaimItem(ResponseModel):
    """`titular_*`: who holds the number now (only for a pending claim); `estado`: pending or unverified."""
    id: int
    dni: str
    estado: str
    nombre: str | None = None
    apellido: str | None = None
    contacto: str | None = None
    email: str | None = None
    titular_dni: str | None = None
    titular_nombre: str | None = None
    titular_apellido: str | None = None


class DniClaimsOut(ResponseModel):
    items: list[DniClaimItem]


class DniClaimResolveOut(ResponseModel):
    ok: bool
    estado: str
    dni: str


class PatientDoctor(ResponseModel):
    nombre: str
    n: int


class PatientAppointments(ResponseModel):
    otorgados: int
    proximo: str | None = None


class PatientListItem(ResponseModel):
    """A row of the patients list: the account's main fields plus what the list
    computes. `ultimo_contacto` only in the recent-contacts view."""
    dni: str
    nombre: str | None = None
    apellido: str | None = None
    nacimiento: str | None = None
    sexo: str | None = None
    contacto: str | None = None
    email: str | None = None
    activo: bool | None = None
    created: str | None = None
    coverage_revision: str | None = None
    identity_kind: str | None = None
    ultimo_contacto: str | None = None
    servicio: str
    ranking: Ranking
    medicos: list[PatientDoctor]
    turnos: PatientAppointments
    cobertura_nombre: str
    localidad: str


class PatientListOut(ResponseModel):
    """`modo`: recientes (no search) or busqueda; absent for a doctor's own list."""
    items: list[PatientListItem]
    total: int
    offset: int
    limit: int
    modo: str | None = None


class PrimaryCoverage(ResponseModel):
    """The coverage the patient uses first: `tipo` prepaga or obra_social."""
    clave: str
    tipo: str
    nombre: str | None = None
    afiliado: str | None = None
    plan: str | None = None
    origen: str | None = None


class PatientAppointment(ResponseModel):
    """An appointment or request on the patient's file."""
    id: int
    estado: str
    servicio: str
    servicio_id: str | None = None
    restaurable: bool
    centro: str
    medico: str
    dia: str | None = None
    hora: str | None = None
    pedido: bool
    futuro: bool
    asistencia: str


class PatientDetailOut(ResponseModel):
    """The patient's file: the account, its dependents, the adults it depends on,
    its appointments, and whether this session may open the access log."""
    item: PatientRow
    cobertura_principal: PrimaryCoverage | None = None
    ranking: Ranking
    dependientes: list[PatientDependent]
    a_cargo_de: list[DependentAdult]
    ver_accesos: bool
    turnos: list[PatientAppointment]


class AccessLogEntry(ResponseModel):
    """One read of the patient's clinical data, or a refused attempt."""
    dia: str | None = None
    hora: str | None = None
    usuario: str
    rol: str
    accion: str
    objeto: str | None = None
    permitido: bool


class AccessLogOut(ResponseModel):
    """Newest first, at most 500; `retencion_dias`: how long the log keeps a row."""
    items: list[AccessLogEntry]
    retencion_dias: int


class FreeSlot(ResponseModel):
    dia: str
    hora: str
    medico: str


class OverbookingSlot(ResponseModel):
    """A taken slot that still admits `restantes` appointments on top."""
    dia: str
    hora: str
    medico: str
    restantes: int


class AppointmentSlotsOut(ResponseModel):
    """The desk's free slots of a service. `medicos`: the doctors to choose from
    when the slots name none; `motivo`: why there are none for the doctor asked."""
    libres: list[FreeSlot]
    sobreturnos: list[OverbookingSlot]
    medicos: list[str] | None = None
    motivo: str | None = None


class DoctorOption(ResponseModel):
    id: str
    nombre: str


class LastInterview(ResponseModel):
    id: int
    triage: str | None = None
    resumen: str | None = None
    estado: str
    fecha: str | None = None


class NextAppointment(ResponseModel):
    fecha: str


class DoctorPatient(ResponseModel):
    """A patient of the doctor: how to reach them (theirs, the adult in charge,
    the request's), their last pre-visit interview and next appointment."""
    dni: str
    nombre: str | None = None
    contacto: str | None = None
    contacto_de: str | None = None
    contacto_responsable: str | None = None
    contacto_cuenta: str | None = None
    contacto_cuenta_de: str | None = None
    ultima_entrevista: LastInterview | None = None
    proximo_turno: NextAppointment | None = None


class DoctorPatientsOut(ResponseModel):
    """The patients of one doctor (`medico_id` null until one is picked);
    `medicos`: the doctors to pick from, for a session not held to its own."""
    medico_id: str | None
    medico: str | None
    entrevista_activa: bool | None = None
    items: list[DoctorPatient]
    medicos: list[DoctorOption] | None = None


class DoctorOwnProfile(ResponseModel):
    nombre: str
    nombre_pila: str
    apellido: str
    especialidad: str
    telefono: str


class DoctorProfileOut(ResponseModel):
    """`item` null when the account has no linked doctor; `especialidades`: the list to choose from."""
    ok: bool
    item: DoctorOwnProfile | None
    especialidades: list[str]


class OnboardingOut(ResponseModel):
    """`next`: where the doctor goes now (the account's security step while 2FA is due)."""
    ok: bool
    next: str


# ---- settings and content (stage 7) ------------------------------------------

class SettingsIn(BodyModel):
    """One block of the settings screen: `grupo` (chat, turnos, feriados,
    seguridad, connectors, location, languages, api_keys, backup_key), its
    `valores` and, for keys, the names to `borrar`."""
    grupo: str | None = None
    valores: dict | None = None
    borrar: list[str] | None = None


class SettingsProbeIn(BodyModel):
    integracion: str | None = None


class ContentItemIn(BodyModel):
    """A catalog item (centre, service, doctor, preparation or wiki page): the
    fields of its type, validated by `patient_web.catalog.validate`."""


class KnowledgePropertiesIn(BodyModel):
    content: str | None = None


class SettingsOut(ResponseModel):
    """`ok` plus the saved block under its `grupo` name (exactly one of the
    others)."""
    ok: bool
    location: dict | None = None
    languages: dict | None = None
    chat: dict | None = None
    turnos: dict | None = None
    feriados: dict | None = None
    seguridad: dict | None = None
    connectors: dict | None = None
    api_keys: list[dict] | None = None
    backup_key: dict | None = None


class ProbeOut(ResponseModel):
    """`key_activa` ("primary", "backup" or "none") only for the AI probe."""
    ok: bool
    detalle: str | None = None
    key_activa: str | None = None


class KnowledgeActionOut(ResponseModel):
    """`properties` proposed for a wiki article (`knowledge/properties`), or the
    check of the golden conversations (`knowledge/golden`: `total`, `passed`,
    one result each)."""
    properties: dict | None = None
    total: int | None = None
    passed: int | None = None
    results: list[dict] | None = None


# ---- panel tools: error reports, table export, report steps, AI register, usability ----

class ClientErrorIn(BodyModel):
    """One browser error of a staff screen (templates/admin/base.html): never the query string, the page
    text or form values. `kind` is `error` or `unhandledrejection`."""
    kind: str | None = None
    message: str | None = None
    source: str | None = None
    line: int | None = None
    col: int | None = None
    stack: str | None = None
    screen: str | None = None


class TableExportIn(BodyModel):
    """The table the panel shows, to be answered as a CSV file: the screen's path, its title and day
    (`YYYY-MM-DD`), the column headings, the rows as text cells, and the DNI behind each row (for the
    clinical access log)."""
    screen: str | None = None
    title: str | None = None
    day: str | None = None
    columns: list[str] | None = None
    rows: list[list[str]] | None = None
    patients: list[str] | None = None


class FunnelStepIn(BodyModel):
    key: str | None = None
    on: bool | None = None


class FunnelStepsIn(BodyModel):
    """The order of the «Recorrido» report's steps and which are on."""
    steps: list[FunnelStepIn] | None = None


class AiRegisterNotifyIn(BodyModel):
    """The register change notified to the authority: its id, the notice's reference and date."""
    id: int | None = None
    reference: str | None = None
    date: str | None = None


class UsabilityAnalyzeIn(BodyModel):
    """An empty object: the analysis takes nothing from the body."""


class UsabilityFindingIn(BodyModel):
    """A finding written by hand: the role and task of the study it is about, `severity` (`low`,
    `medium`, `high`), a title and the evidence."""
    role: str | None = None
    task_id: str | None = None
    severity: str | None = None
    title: str | None = None
    detail: str | None = None


class UsabilityFindingUpdateIn(BodyModel):
    """A finding's new status and, optionally, the fix: its note, the commit and the live https URL."""
    status: str | None = None
    fix_note: str | None = None
    commit: str | None = None
    live_url: str | None = None
    detail: str | None = None


class ContentListOut(ResponseModel):
    """A catalog list, each row with the fields the panel computes, and the
    site-wide booking defaults the rows fall back to."""
    items: list[dict]
    turnos_max_sitio: int
    dias_anticipacion_sitio: int
    apertura_turnos_sitio: str
    feriados_sitio: list[dict]


class ContentItemOut(ResponseModel):
    """The saved or restored row (restoring an id that exists nowhere is a
    404 `no_encontrado`) and, for a new doctor that is a person, its account
    or invitation (`cuenta`)."""
    ok: bool
    item: dict | None
    cuenta: dict | None = None


class ContentDeletedOut(ResponseModel):
    """How many appointments name the row, and whether it was kept disabled
    (`conservado`) instead of deleted."""
    ok: bool
    turnos: int
    conservado: bool


class DoctorImportOut(ResponseModel):
    """The import of a doctors' file (preview while `dry_run`): one entry per
    line and the counts by outcome."""
    dry_run: bool
    rows: list[dict]
    validas: int
    creadas: int
    existentes: int
    errores: int


# ---- staff accounts, roles, settings: the answers (OpenAPI stage D) ---------

class StaffUserOut(ItemOut):
    """`ItemOut` plus `totp` when a code was generated, and on an invitation
    whether the email went out (`email_enviado`) or, when it did not, the link
    to hand over (`enlace`)."""
    totp: dict | None = None
    email_enviado: bool | None = None
    enlace: str | None = None


class StaffUsersOut(ResponseModel):
    """The staff accounts, the roles to assign and the permission catalog
    (`{}` when the permissions cannot be read); `acceso_portal` and
    `acceso_portal_totp` on a portal-login installation."""
    items: list[dict]
    roles: list[dict]
    permissions: list[dict] | dict
    acceso_portal: bool | None = None
    acceso_portal_totp: bool | None = None


class RoleListOut(RootModel[list[RoleOut]]):
    """Every role: the built-in ones first, in preset order, then the custom ones by name."""


class RoleDeletedOut(ResponseModel):
    """A custom role nobody held, deleted."""
    status: str


class PermissionOut(ResponseModel):
    id: str
    label: str


class PermissionListOut(RootModel[list[PermissionOut]]):
    """The permission catalog, in display order."""


class SettingsStateOut(ResponseModel):
    """Every block of the settings screen; `lock` when `TURNOS_CONFIG_LOCK` is
    on (saved values do not apply)."""
    lock: bool
    location: dict
    languages: dict
    chat: dict
    turnos: dict
    feriados: dict
    seguridad: dict
    connectors: dict
    api_keys: list[dict]


class StaffAccountOut(ResponseModel):
    """The signed-in account on «Cuenta» and its 2FA state; `2fa_no_aplica` and
    `acceso_portal` on a portal-login installation."""
    usuario: str
    rol: str
    email: str | None
    es_dueno: bool
    tiene_2fa: bool
    deadline_2fa: str | None = Field(default=None, alias="2fa_deadline")
    vencido_2fa: bool = Field(alias="2fa_vencido")
    no_aplica_2fa: bool | None = Field(default=None, alias="2fa_no_aplica")
    acceso_portal: bool | None = None


class TotpStartOut(ResponseModel):
    """A new 2FA secret to confirm within `expires_in` seconds (`qr_svg` null
    when the QR library is missing)."""
    ok: bool
    secret: str
    otpauth_url: str
    qr_svg: str | None
    setup_token: str
    expires_in: int


class PreferencesOut(ResponseModel):
    """Every panel preference of the caller, by key."""
    items: dict[str, Any]
