"""ai_screens_api_models.py — Pydantic models of the success answers of two
staff screens about the AI and the product's own evaluation: the AI register
(`ai_register_screen.py`, `/api/admin/ai-register*`) and the usability
study's findings (`admin_usability_screen.py`, `/api/admin/usability*`).

They only document the API (`core.api_body.docs` in each route's
`openapi_extra`): the handlers still return their ready `Response`. Kept
apart from `api_models.py`, which holds the bodies and answers of the other
panel screens.
"""

from app.core.api_body import ResponseModel


# ---- the AI register -----------------------------------------------------------

class AiRegisterState(ResponseModel):
    """What the register can and cannot state: the product never claims a
    registration, counts the substantial changes not yet notified, and says
    that a notification is a human's attestation."""
    registration: str
    pending_notifications: int
    human_attestation_only: bool


class AiRegisterOut(ResponseModel):
    """The AI register (`core.ai_register.dossier`), as the screen reads it and
    as `/export` downloads it: one row per AI function of the installation
    (model, provider, data, risk, state, last substantial change), the
    register's state and every recorded change (model, provider, prompt,
    data scope, enabled) with its notification status."""
    características: list[dict]
    estado_actual: AiRegisterState
    modificaciones: list[dict]


# ---- the usability study's findings -------------------------------------------------

class UsabilitySession(ResponseModel):
    id: int
    role: str
    locale: str | None
    started_at: str | None
    finished_at: str | None


class UsabilityPeriod(ResponseModel):
    """First and last study day, in the installation's timezone."""
    first: str
    last: str


class UsabilityReportOut(ResponseModel):
    """The study's report: its sessions, each task result, the findings (the
    analysis' candidates and the manual ones), whether this session may edit
    them (the owner, never the demo account), the study's definition (roles,
    tasks, final questions) and the period it covers (null before any
    session)."""
    sessions: list[UsabilitySession]
    results: list[dict]
    findings: list[dict]
    editable: bool
    definition: dict
    period: UsabilityPeriod | None


class UsabilityAnalysisOut(ResponseModel):
    """How many findings the analysis proposed (new or refreshed)."""
    candidates: int


class UsabilityFindingCreatedOut(ResponseModel):
    """A manual finding was added: its id and the number shown for it (`U-<id>`)."""
    id: int
    number: str


class UsabilityFindingUpdatedOut(ResponseModel):
    id: int
