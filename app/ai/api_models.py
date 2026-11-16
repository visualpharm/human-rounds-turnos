"""api_models.py — Pydantic models of the `ai` package's JSON API: the
request body of the pre-visit chart summary (`chart_summary_router.py`) and
the success answers of the patient's lab results (`lab_results_router.py`),
the chart summary, the AI costs report (`ai_costs_router.py`), the public AI
capability probe (`ai_health_router.py`) and the editable prompts
(`prompts_router.py`).

The body model documents the API (`/openapi.json`, admin-only) and lets
`core.api_body` tell a body the panel never sends from one it does; the
domain still gets the body as it came — see `core/api_body.py`. The answer
models only document the API (`core.api_body.docs` in each route's
`openapi_extra`): the handlers still return their ready `Response`. Rows the
domain builds from open-ended data (a lab report's values, a chart summary,
the cost report's tables) are `dict`s; the models name the keys every answer
has.
"""

from app.core.api_body import BodyContract, BodyModel, ResponseModel
from app.core.http_json import json_response

BODY = BodyContract(message=lambda reason: f"JSON inválido: {reason}",
                    respond=lambda _request, payload, status: json_response(payload, status=status))
"""POST /api/admin/patient-summary: the route's flat `{"error": ...}`. A malformed body still reads as
`{}`; this answers the bodies it did not survive — JSON that is not an object."""


class PatientSummaryIn(BodyModel):
    """Generate the chart summary again: the patient's DNI and, for an admin, the doctor it is for (a
    doctor's own session decides that by itself)."""
    dni: str | None = None
    medico: str | None = None


# ---- the patient's lab results -------------------------------------------------

class LabReport(ResponseModel):
    """One lab report, newest first. A sensitive report carries `fecha`,
    `sensible: true` and `tercero` only — the redaction is server-side.
    `tercero`: uploaded by the patient (or someone on their account) and read
    unverified; `upload_id` is set for the patient's own uploads, which they
    may delete; `items` are the report's values with their reference ranges."""
    fecha: str | None
    tercero: bool
    sensible: bool | None = None
    upload_id: int | None = None
    tipo_estudio: str | None = None
    resumen: str | None = None
    tono: str | None = None
    servicio: str | None = None
    items: list[dict] | None = None


class LabResultsOut(ResponseModel):
    """«Resultados de laboratorio» of the signed-in patient: the reports, the
    values that can be charted over time, how many reports still wait for
    their AI reading, the state of each source consulted, the booking link
    for «a clinician» and, when the lab provider's key is set, the link that
    opens its own signed page (`/account/lab-results/open`)."""
    informes: list[LabReport]
    series: list[dict]
    analisis_pendiente: int
    fuentes_estado: dict[str, str]
    clinico_href: str
    laboratorio_href: str | None


class LabUploadOut(ResponseModel):
    """An uploaded result was stored; `sin_lectura` when the AI could not
    read it (yet) — the file is kept either way."""
    ok: bool
    id: int
    sin_lectura: bool


# ---- the pre-visit chart summary ------------------------------------------------

class PatientSummaryOut(ResponseModel):
    """«Resumen del paciente» for one doctor: the summary itself (headline,
    trends with their full series, indicators, sources line — the model's
    reading or the deterministic one), when it was generated, which model
    wrote it (or why none did: `fake`, `sin_clave_api`, `ai_no_disponible`,
    `fallback`), the doctor's specialty and the state of each clinical
    archive consulted. `generado` is null and `external_history_enabled`
    absent only when the summary could not be stored."""
    resumen: dict
    generado: str | None
    modelo: str | None
    especialidad: str | None
    fuentes_estado: dict[str, str]
    external_history_enabled: bool | None = None


# ---- the AI costs report ----------------------------------------------------------

class AiCostsSummary(ResponseModel):
    total_usd: float
    operaciones: int
    costo_prom_op: float
    reconocidas: int
    no_reconocidas: int
    tasa_reconocimiento: float | None
    run_rate_mensual_usd: float | None
    dias_completos: int
    burn_desde: str | None
    burn_hasta: str | None
    ips: int
    costo_mediano_por_ip: float | None
    por_1000_pacientes_usd: float


class AiCostsOut(ResponseModel):
    """«Costos de IA» over the last `dias` days: the totals, one row per
    operation, one per day, the costliest visitors (by hashed IP) and those of
    them flagged as suspicious, and whether the abuse cooldowns are on."""
    dias: int
    resumen: AiCostsSummary
    por_operacion: list[dict]
    diario: list[dict]
    top_ips: list[dict]
    sospechosos: list[dict]
    abuso_activo: bool


class AiStatusOut(ResponseModel):
    """Whether an AI-backed card (chat, DNI reading) should be offered now."""
    disponible: bool


# ---- the editable prompts ----------------------------------------------------------

class PromptVariable(ResponseModel):
    """A placeholder the prompt may use and what it holds."""
    nombre: str
    que_es: str


class PromptItem(ResponseModel):
    """One editable prompt as the panel shows it: what it does and where, its
    variables, the English instructions sent to the model (`texto_en`), the
    Spanish version staff edit (`texto_es`, null until translated or when it
    no longer matches), the fixed technical part, the code's original, whether
    the two languages are aligned, and who last edited it. `error_traduccion`
    only when a translation was asked for and could not be made."""
    clave: str
    titulo: str
    que_hace: str
    donde: str
    variables: list[PromptVariable]
    editado: bool
    texto_en: str
    texto_es: str | None
    tecnico_en: str
    original_en: str
    alineado: bool
    actualizado: str | None
    actualizado_por: str | None
    error_traduccion: str | None = None


class PromptsOut(ResponseModel):
    items: list[PromptItem]


class PromptChange(ResponseModel):
    """A paragraph the Spanish edit changed, and its English counterpart."""
    tipo: str
    es_antes: str
    es_ahora: str
    en_antes: str
    en_ahora: str


class PromptSavedOut(ResponseModel):
    """The prompt after the edit and the paragraphs it changed (none when the
    English was saved directly)."""
    ok: bool
    item: PromptItem
    cambios: list[PromptChange]


class PromptRestoredOut(ResponseModel):
    """The prompt after its edit was dropped: the code's original again."""
    ok: bool
    item: PromptItem


class PromptHistoryEntry(ResponseModel):
    id: int
    texto_en: str | None
    texto_es: str | None
    actualizado: str | None
    actualizado_por: str | None


class PromptHistoryOut(ResponseModel):
    items: list[PromptHistoryEntry]
