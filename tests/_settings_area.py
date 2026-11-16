"""_settings_area.py — which operations of the staff panel's settings,
content, users and roles, staff sign-in, the platform and every operation
outside `/api/` must document their success answer (TASK-76, OpenAPI stage
D, area d) and the reasons the ones that answer no JSON document are
excluded; the checks of an answer are the shared ones of
`tests/_response_models.py`. Shared by
`tests/test_settings_response_docs.py` (the schema and the golden answers,
DB-less), `tests/test_openapi_response_coverage.py` (every operation of the
schema in exactly one area) and `tests/routes/test_settings_response_models.py`
(every operation answered by the live app).

The area is read from the built schema and the routes that answer it
(`routers/api_docs.build_schema`, `api_docs._route_index`), never from the
source text: every operation that areas a, b and c do not hold. Their
boundaries are restated here (`other_area`), and
`test_openapi_response_coverage.py` compares the restatement with their own
lists.

Not named test_*.py: unittest discovery must not collect it.
"""

import _response_models as rm

# ---- the other areas, restated ------------------------------------------------

# Area b (`tests/_staff_response_models.py`): the staff operations of booking
# requests, patients and the doctor's appointments, by the module that defines
# the endpoint, except the settings the `turnos` module serves (theme, sign-in
# mode, Turnstile), which are this area's.
_B_MODULES = (
    "app.modules.turnos",
    "app.admin_panel.admin_patients_router",
    "app.admin_panel.admin_appointments_router",
    "app.admin_panel.doctor_patients_router",
    "app.admin_panel.doctor_workspace_screen",
)
_B_NOT = ("/api/admin/theme", "/api/admin/auth-mode", "/api/admin/turnstile")

# Area c (`tests/_insight_area.py`): the groups «AI», «Pre-visit interview» and
# «Chat», and the panel's reports, system status, AI register and usability study.
_C_TAGS = ("AI", "Pre-visit interview", "Chat")
_C_PANEL = ("/api/admin/reports", "/api/admin/system", "/api/admin/ai-register", "/api/admin/usability")


def _under(path, prefixes):
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def other_area(path, operation, module):
    """"a", "b" or "c" when one of the other areas holds the operation, else None."""
    if not path.startswith("/api/"):
        return None
    tag = operation.get("tags", [None])[0]
    if not path.startswith("/api/admin/") and tag in rm.AREA_TAGS:
        return "a"
    if path.startswith("/api/admin/") and not _under(path, _B_NOT) and any(
            module == m or module.startswith(m + ".") for m in _B_MODULES):
        return "b"
    if tag in _C_TAGS or (tag == "Staff panel" and _under(path, _C_PANEL)):
        return "c"
    return None


def area_operations(schema, app):
    """[(method, path, operation)] of the area, in schema order."""
    from app.routers import api_docs
    index = api_docs._route_index(app)
    out = []
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if other_area(path, operation, index[(path, method)].endpoint.__module__) is None:
                out.append((method, path, operation))
    return out


# ---- what answers no JSON document --------------------------------------------

# Outside /api/, by class: (reason, methods, paths). A new route outside /api/
# that answers JSON gets a model instead.
_CLASSES = (
    ("an HTML page (text/html)", ("get",), (
        "/", "/about.html", "/account", "/account/demo-access", "/account/reset", "/admin/account",
        "/admin/ai-register", "/admin/ai-register/print", "/admin/appearance", "/admin/centers",
        "/admin/chats", "/admin/cola", "/admin/cola/print", "/admin/consult", "/admin/doctors",
        "/admin/entrevistas", "/admin/hsi", "/admin/knowledge", "/admin/legal", "/admin/login",
        "/admin/mis-turnos", "/admin/my-patients", "/admin/my-schedules", "/admin/onboarding",
        "/admin/pacientes", "/admin/plan", "/admin/preparations", "/admin/prompts", "/admin/reportes",
        "/admin/reset-password", "/admin/roles", "/admin/services", "/admin/settings",
        "/admin/solicitudes", "/admin/system", "/admin/usability", "/admin/users",
        "/appointment/confirm/{token}", "/appointment/offer/{token}", "/auth/renaper/callback",
        "/auth/sso", "/auth/test", "/blood-donation", "/blood-donation/", "/center/{slug}", "/centers",
        "/centers/", "/d/{cid}", "/datos", "/datos/", "/doctor/{mid}", "/doctors", "/doctors/",
        "/e/{code}", "/entrevista/{sid}", "/index.html", "/news", "/news/", "/news/pedidos",
        "/news/pedidos/", "/o/{token}", "/preparation/{pid}", "/privacy.html", "/r/{token}",
        "/service/{rest}", "/study", "/study/s/{token}", "/study/{rest}", "/telemedicine",
        "/telemedicine/", "/terms.html", "/upload-order", "/zoonosis", "/zoonosis/",
    )),
    ("a redirect (3xx with Location, no body) to the current address", ("get",), (
        "/account/portal-login", "/admin", "/admin/demo-access", "/admin/demo-doctor-access",
        "/admin/users/import", "/app", "/app.html", "/app/", "/auth/renaper", "/clinica/{rest}",
        "/clinicas", "/clinicas/", "/cobertura/{rest}", "/directorio", "/directorio.html",
        "/directorio/", "/my-requests", "/my-requests/", "/obras-sociales", "/obras-sociales/",
        "/panel", "/panel.html", "/panel/", "/request/{rest}",
    )),
    ("a file: an image, a calendar (text/calendar), XML, plain text or the stored document", ("get",), (
        "/account/lab-results/open", "/appointment/{token}.ics", "/avatar-sprite/{h}.webp",
        "/doctor-thumb/{name}.webp", "/feed/appointments/{token}.ics", "/feed/doctor/{token}.ics",
        "/llms.txt", "/logo", "/logo.png", "/robots.txt", "/sitemap.xml", "/{entity}-photo/{eid}",
    )),
    ("the MCP server's own protocol (JSON-RPC over streamable HTTP), described by MCP, not here",
     ("get", "post", "delete"), ("/mcp", "/mcp/")),
    ("the first-run installer: its HTML form pages and their 303 redirects (a 404 once installed)",
     ("get", "post", "put", "delete"), ("/setup", "/setup/{sub}")),
    ("the static files and the not-found page for any other path", (
        "get", "head", "post", "put", "patch", "delete", "options"), ("/{full_path}",)),
    ("HEAD: the headers of the GET, no body", ("head",), ("/health", "/ready")),
)
_LEGACY_TAG = "Legacy URLs"
_LEGACY_REASON = "an old Spanish address, kept as a permanent redirect to the current one"

# Under /api/ (area d's own), and why.
_API_EXCLUDED = {
    ("post", "/api/admin/table-export"): "the panel's table as a CSV file for Excel (text/csv attachment), not JSON",
}

EXCLUDED = dict(_API_EXCLUDED)
for _reason, _methods, _paths in _CLASSES:
    for _path in _paths:
        for _method in _methods:
            EXCLUDED[(_method, _path)] = _reason


def excluded(method, path, operation):
    """The reason an operation of the area documents no answer, or None."""
    if operation.get("tags", [None])[0] == _LEGACY_TAG:
        return _LEGACY_REASON
    return EXCLUDED.get((method, path))
