"""reports_api_models.py — Pydantic models of the success answers of the
staff reports (`reports_router.py`: Gestión, Pacientes, the login panel,
searches, reviews, cost recovery, SUMAR+, statistics, capacity and the
«Recorrido» booking path) and of the system status (`system_status_router.py`:
«Sistema» and the state of HSI). They only document the API: the routes
return their ready `Response`, so no byte of an answer goes through them
(`core/api_body.py`).

The rows name every key the report code writes; a value the code computes
from free-form data (a reason, a center's name) is text. Open shapes — a
data file's analysis, a status item's evidence — stay `dict`.
"""

from pydantic import Field

from app.core.api_body import ResponseModel


# ---- shared ------------------------------------------------------------------

class HeroPhrase(ResponseModel):
    """One computed sentence that opens a report: `tono` is "ok", "atencion",
    "critico" or "info". An action of «Recorrido» also names its `motivo` and,
    when it is done on a screen, links it (`enlace`: {href, texto})."""
    texto: str
    tono: str
    motivo: str | None = None
    enlace: dict | None = None


class DayVolume(ResponseModel):
    """New accounts on one day (`fecha`, YYYY-MM-DD in Buenos Aires time)."""
    fecha: str
    total: int


class RequestDayVolume(DayVolume):
    """Requests created on one day, by their state."""
    aprobadas: int
    rechazadas: int
    pendientes: int


class AgeBucket(ResponseModel):
    """One band of the population pyramid: all, women and men."""
    rango: str
    cantidad: int
    f: int
    m: int


class WeekCount(ResponseModel):
    """Completed consultations of one week (`semana`: its ISO Monday)."""
    semana: str
    consultas: int


# ---- GET /api/admin/reports (Gestión) ---------------------------------------

class ReportsKpis(ResponseModel):
    """Pending now, and the period's requests and medians (hours to an answer,
    days to the appointment)."""
    pendientes: int
    solicitudes_periodo: int
    pct_aprobadas: float | None
    mediana_horas_resolucion: float | None
    mediana_dias_turno: float | None
    mediana_horas_pendiente: float | None
    max_horas_pendiente: float | None
    esperas_pendientes_horas: list[float]


class SpecialtyRow(ResponseModel):
    """Requests per weekly hour offered, for one specialty (cluster)."""
    cluster: str
    nombre: str
    solicitudes: int
    horas_semana: float
    ratio: float | None
    sin_oferta: bool
    espera_mediana_dias: float | None


class RejectionRow(ResponseModel):
    motivo: str
    cantidad: int


class SupplyDemandRow(ResponseModel):
    """A point of the «requests vs hours offered» scatter: a cluster (`centro`
    null) or a service with requests in the period."""
    id: str
    nombre: str
    centro: str | None
    horas_semana: float
    solicitudes: int
    solicitudes_por_hora: float | None
    espera_mediana_dias: float | None
    sin_oferta: bool
    sugerencia_horas: float | None


class SupplyDemand(ResponseModel):
    """By cluster and by service; `equilibrio` is the period's median ratio."""
    clusters: list[SupplyDemandRow]
    servicios: list[SupplyDemandRow]
    equilibrio: float | None


class HeatmapMismatch(ResponseModel):
    """One of the five hours where demand most exceeds supply."""
    dia: str
    hora: int
    oferta: float
    demanda: float
    diferencia: float
    label: str


class Heatmap(ResponseModel):
    """Supply and demand per weekday (`dias`, Sunday first) and hour (`horas`)."""
    dias: list[str]
    horas: list[int]
    oferta: list[list[float]]
    demanda: list[list[float]]
    desajuste: list[HeatmapMismatch]


class LeverReason(ResponseModel):
    motivo: str
    n: int


class LeverGroup(ResponseModel):
    """Rejections grouped as avoidable before sending, lack of capacity, other."""
    id: str
    nombre: str
    n: int
    pct: float | None
    motivos: list[LeverReason]


class Levers(ResponseModel):
    grupos: list[LeverGroup]
    hero: str


class PathReason(ResponseModel):
    """Why requests that reached the previous step did not pass this one;
    `en_curso` when they still may (pending, appointment ahead)."""
    key: str
    n: int
    texto: str
    en_curso: bool


class PathStep(ResponseModel):
    """A step of the booking path that is on: how many passed it, what share of
    the first step (`pct`), how many stayed behind and why."""
    key: str
    label: str
    n: int
    perdidos: int
    en_curso: int
    motivos: list[PathReason]
    pct: float | None


class Attendance(ResponseModel):
    presente: int
    ausente: int
    sin_dato: int


class ReportsFunnel(ResponseModel):
    """The management funnel: the «Recorrido» steps unfiltered (`pasos`,
    `frases`, `con_turno`) and the period's counters."""
    pasos: list[PathStep]
    frases: list[HeroPhrase]
    con_turno: int
    holds_creados: int
    holds_convertidos_en_solicitud: int
    solicitudes: int
    aprobadas: int
    rechazadas: int
    canceladas: int
    pendientes: int
    asistencia: Attendance
    recordatorios_enviados: int


class WaitlistReport(ResponseModel):
    """Freed appointments offered to the waitlist; `rechazadas` is always null
    (the schema cannot tell a rejection from an expiry)."""
    ofertas_enviadas: int
    aceptadas: int
    expiradas: int
    rechazadas: int | None
    turnos_recuperados: int


class Savings(ResponseModel):
    """Hours the automation gives back to the team; only `disponible` (false)
    while no AI call was ever recorded."""
    disponible: bool
    gasto_usd: float | None = None
    operaciones: int | None = None
    minutos_ahorrados: float | None = None
    horas_operador: float | None = None
    costo_hora_ars: float | None = None
    ahorro_ars: float | None = None
    roi: float | None = None
    supuestos: list[str] | None = None


class ReportsOut(ResponseModel):
    """GET /api/admin/reports: the Gestión report of the last `dias` days."""
    dias: int
    kpis: ReportsKpis
    especialidades: list[SpecialtyRow]
    rechazos: list[RejectionRow]
    volumen: list[RequestDayVolume]
    hero: list[HeroPhrase]
    oferta_demanda: SupplyDemand
    heatmap: Heatmap
    palancas: Levers
    funnel: ReportsFunnel
    lista_espera: WaitlistReport
    ahorro: Savings


# ---- GET /api/admin/reports/patients ------------------------------------------

class PatientsKpis(ResponseModel):
    total_cuentas: int
    nuevas_periodo: int
    pct_con_email: float | None
    pct_con_domicilio: float | None


class NameCount(ResponseModel):
    nombre: str
    cantidad: int


class CoverageReport(ResponseModel):
    pct_con_obra_social: float | None
    pct_con_prepaga: float | None
    top_obras_sociales: list[NameCount]


class MonthActivity(ResponseModel):
    """Distinct patients who asked for an appointment in a month (YYYY-MM-01)."""
    mes: str
    pacientes: int


class DependentsCount(ResponseModel):
    total: int
    adultos_con_dependientes: int


class PatientActivity(ResponseModel):
    pacientes_con_solicitud: int
    solicitudes_totales: int
    promedio_por_paciente: float | None


class PatientsReportOut(ResponseModel):
    """GET /api/admin/reports/patients: aggregated counts, no person in them."""
    dias: int
    kpis: PatientsKpis
    cobertura: CoverageReport
    edades: list[AgeBucket]
    volumen: list[DayVolume]
    actividad_mensual: list[MonthActivity]
    dependientes: DependentsCount
    actividad: PatientActivity


# ---- GET /api/admin/reports/login-panel ---------------------------------------

class LoginPanelReportOut(ResponseModel):
    """GET /api/admin/reports/login-panel: the staff login screen's friction.
    A session is a sid that tried to log in (login_ok or login_error); the
    closed gate experiment (memes vs consejos, 2026-07) is gone from the data."""
    dias: int
    sesiones: int
    logins: int
    errores: int
    sesiones_con_error: int
    ms_mediana_login: float | None


# ---- GET /api/admin/reports/searches ------------------------------------------

class SearchRow(ResponseModel):
    """A normalized search, how many times it was made and how many found something."""
    q: str
    veces: int
    con_resultados: int


class SearchesReportOut(ResponseModel):
    dias: int
    top: list[SearchRow]


# ---- GET /api/admin/reports/reviews -------------------------------------------

class ReviewsReportOut(ResponseModel):
    """GET /api/admin/reports/reviews, from the data pack's files: the analysis
    (`fuente` "analisis": `centers`, `comparison`, `_meta`, as
    `scripts/analizar_resenas.py` writes them), else the curated reviews per
    center (`fuente` "curado"), else none (`fuente` null, `centers` empty)."""
    fuente: str | None
    centers: dict
    comparison: dict | None = None
    meta: dict | None = Field(default=None, alias="_meta")


# ---- GET /api/admin/reports/cost-recovery ---------------------------------------

class CostRecoveryKpis(ResponseModel):
    consultas_totales: int
    consultas_con_cobertura: int
    pct_del_total: float | None
    pacientes_con_cobertura: int
    presentes: int
    ausentes: int


class InsurerRow(ResponseModel):
    """Completed consultations of the patients of one insurer (a patient with two counts for both)."""
    nombre: str
    consultas: int
    presentes: int
    pacientes: int


class CostRecoveryReportOut(ResponseModel):
    dias: int
    hero: list[HeroPhrase]
    kpis: CostRecoveryKpis
    por_obra_social: list[InsurerRow]
    tendencia: list[WeekCount]
    sin_dato: int


# ---- GET /api/admin/reports/sumar ----------------------------------------------

class SumarKpis(ResponseModel):
    prestaciones: int
    pacientes: int
    pct_del_total: float | None


class TracerRow(ResponseModel):
    clave: str
    label: str
    consultas: int


class SumarReportOut(ResponseModel):
    """The SUMAR+ program's population: consultations of patients with public
    coverage only."""
    dias: int
    hero: list[HeroPhrase]
    kpis: SumarKpis
    trazadoras: list[TracerRow]
    tendencia: list[WeekCount]


# ---- GET /api/admin/reports/statistics -----------------------------------------

class StatisticsKpis(ResponseModel):
    consultas_totales: int
    presentes: int
    ausentes: int
    sin_marca: int
    pct_asistencia: float | None


class StatisticsSpecialtyRow(ResponseModel):
    cluster: str
    label: str
    consultas: int
    presentes: int
    ausentes: int
    sin_marca: int
    horas_ofrecidas: float
    consultas_por_hora: float | None


class CenterCount(ResponseModel):
    centro: str
    consultas: int


class StatisticsReportOut(ResponseModel):
    """The month's statistics (`mes`: YYYY-MM)."""
    mes: str
    hero: list[HeroPhrase]
    kpis: StatisticsKpis
    por_especialidad: list[StatisticsSpecialtyRow]
    edades: list[AgeBucket]
    por_centro: list[CenterCount]


# ---- GET /api/admin/reports/capacity -------------------------------------------

class OccupancyRow(ResponseModel):
    """Appointments taken of the real slots of the next 14 days, per cluster."""
    cluster: str
    label: str
    capacidad_14d: int
    ocupados_14d: int
    pct: float | None


class WeekDemand(ResponseModel):
    semana: str
    solicitudes: int
    busquedas: int


class UnmetRow(ResponseModel):
    """A cluster with requests in the last 90 days and no schedule hour."""
    cluster: str
    label: str
    solicitudes: int


class Season(ResponseModel):
    """The high-season projection, only while `TURNOS_SEASON_FACTOR` is set."""
    disponible: bool
    factor: float | None = None
    proyeccion: float | None = None
    supuesto: str | None = None


class CapacityReportOut(ResponseModel):
    hero: list[HeroPhrase]
    ocupacion: list[OccupancyRow]
    demanda_semanal: list[WeekDemand]
    sin_oferta: list[UnmetRow]
    temporada: Season


# ---- GET /api/admin/reports/funnel («Recorrido») ---------------------------------

class PathFilters(ResponseModel):
    centro: str | None
    servicio: str | None
    medico: str | None


class PathOption(ResponseModel):
    id: str
    nombre: str
    n: int


class PathOptions(ResponseModel):
    """What can be filtered by: what has requests in the period, by count."""
    centros: list[PathOption]
    servicios: list[PathOption]
    medicos: list[PathOption]


class PathStepSetting(ResponseModel):
    key: str
    on: bool
    label: str


class PathLoss(ResponseModel):
    """The transition where most requests stayed behind."""
    desde: str
    hasta: str
    n: int
    pct: int
    motivo: str | None


class PathNoData(ResponseModel):
    """A step the installation has no data for yet."""
    key: str
    label: str


class PatientPathReportOut(ResponseModel):
    """GET /api/admin/reports/funnel: the booking path of the last `dias` days,
    narrowed by center, service and doctor; `acciones` keyed by step."""
    dias: int
    filtros: PathFilters
    opciones: PathOptions
    pasos_config: list[PathStepSetting]
    pasos: list[PathStep]
    mayor_perdida: PathLoss | None
    hero: list[HeroPhrase]
    acciones: dict[str, HeroPhrase]
    sin_datos: list[PathNoData]


class PatientPathStepsOut(ResponseModel):
    """POST /api/admin/reports/funnel/steps: the step setting as saved."""
    pasos_config: list[PathStepSetting]


# ---- GET /api/admin/system, /api/admin/system/hsi ---------------------------------

class StatusItem(ResponseModel):
    """One line of a «Sistema» card: `estado` "ok", "warn", "error" or "na";
    `campo` names the setting a «Configurar →» link opens; `cuando` an instant
    with the installation's offset; `backup` the evidence of the last copy."""
    label: str
    valor: str | None = None
    estado: str
    detalle: str | None = None
    campo: str | None = None
    enlace: dict | None = None
    cuando: str | None = None
    backup: dict | None = None
    ai_register: bool | None = None


class StatusCluster(ResponseModel):
    """A card: its worst item's `estado`, a summary line and the items."""
    clave: str
    titulo: str
    estado: str
    resumen: str
    items: list[StatusItem]


class SystemStatusOut(ResponseModel):
    """GET /api/admin/system: every external dependency of the installation,
    how many cards are ok out of `total`."""
    estado: str
    ok: int
    total: int
    clusters: list[StatusCluster]


class HsiCenter(ResponseModel):
    """A live center and its institution number in HSI ("" when it has none)."""
    slug: str
    nombre: str
    institucion: str


class HsiStatusOut(ResponseModel):
    """GET /api/admin/system/hsi: how a turno reaches HSI here (`modo`
    "servidor", "extension" or "apagado"), why (`motivo`), where the desk
    installs the extension (`extension`: {tienda, zip, guia}, or null) and
    each center's institution number."""
    modo: str
    motivo: str
    conector: str | None
    modo_texto: str
    motivo_texto: str
    extension: dict | None
    centros: list[HsiCenter]
    con_numero: int
    total: int
    centros_texto: str
    centros_detalle: str | None
