"""api_models.py — Pydantic models of the platform's own JSON answers: health
and readiness, the modules and their navigation, the interface string
catalog and the demo installation's entry points. Like every `ResponseModel`
they only document the API (the routes return ready `Response`s) — see
`core/api_body.py`.
"""

from pydantic import RootModel

from app.core.api_body import ResponseModel


class HealthOut(ResponseModel):
    """The process is up, and its build (the short commit, or "unknown")."""
    status: str
    version: str


class ReadyOut(ResponseModel):
    """The database answers and its schema is at head; otherwise a 503."""
    status: str


class AdminModulesOut(ResponseModel):
    """The panel's sections and navigation the role may see (a platform
    section by its id, a module's as its manifest entry); `modules`, the state
    of every module, for an admin only."""
    sections: list[str | dict]
    nav_entries: list[dict]
    modules: list[dict] | None = None


class NavEntry(ResponseModel):
    path: str
    label: str


class NavOut(ResponseModel):
    """The patient site's links to the enabled modules, translated and in order."""
    nav_entries: list[NavEntry]


class I18nCatalogOut(RootModel[dict[str, str]]):
    """A language's interface strings: dotted key -> text."""


class DemoOut(ResponseModel):
    """The demo installation: fictional data and its one-click entries."""
    demo: bool
    data: str
    admin_url: str
    doctor_url: str
    patient_url: str
