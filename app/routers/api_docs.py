"""routers/api_docs.py — the API documentation, for a signed-in admin only
(TASK-24, Pydantic stage 1).

`app.py` keeps FastAPI's own `/docs`, `/redoc` and `/openapi.json` off
(`docs_url=None, openapi_url=None`): those would be public. This router
serves `GET /openapi.json` and `GET /docs` (Swagger UI) behind the staff
gate with `solo_admin=True` — the same `core.deps.require_admin` every
admin API uses. Everybody else (anonymous, a patient, any staff role short
of admin, an expired 2FA, a database that cannot answer the gate) gets
EXACTLY what these paths answered before they existed: the native
terminal's response, produced by calling the terminal itself — `/docs` is
the denied `docs/` directory (403), `/openapi.json` the HTML 404 page
(tests/routes/test_api_docs_gate.py). The routes take the terminal's own
method list and hand every non-GET to it, so even a 405 keeps its `Allow`.

The schema is FastAPI's (`get_openapi` over the composed app), built once
per process, with these changes:
  - an operationId per (method, path). FastAPI gives a route registered for
    two methods (`GET`+`POST` on one endpoint — the route-object count is a
    pinned invariant, so they stay one route) the same id on both, and picks
    the method suffix from a set, so the id is not even stable between
    processes. Here the suffix is always the method the operation documents;
  - the request bodies and answers of `core/api_body.py`'s models, and the
    query parameters a route reads (`api_body.query_docs`, documentation
    only: the routes parse them themselves, leniently);
  - `info` (what the API is, how it signs in, how it answers errors, the
    license, the build's commit as the version) and the groups (`TAGS`): each
    operation is in the group of the package that owns its route;
  - a summary from the route's name where it has none;
  - the answers of the gate in front of an operation (401/403 of a staff API,
    303/403 of a panel screen, 401 of `account_deps.current_account`) and the
    503 of every `/api` operation. The two session cookies are security
    schemes declared by the gates themselves (`deps.STAFF_SESSION`,
    `account_deps.PATIENT_SESSION`); a handler that gates by hand (it awaits
    `deps.check_admin`, to keep a check before it, or reads the patient's
    session itself) says so with a marker in its `openapi_extra`
    (`deps.STAFF_GATE_DOCS`, `deps.PATIENT_SESSION_DOCS`), checked against an
    anonymous request to every operation.
`scripts/export_openapi.py` writes the same schema to a file
(`tests/test_api_docs_schema.py`).

Swagger UI loads from jsDelivr pinned to one version with Subresource
Integrity hashes (FastAPI's stock page loads a floating `@5` without them).
"""

import re
import warnings

from fastapi import APIRouter, Request, Response
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from app.core import api_body, db, deps, version
from app.core.http_json import json_response
from app.cuentas import account_deps
from app.routers import native_terminal

router = APIRouter()

_TERMINAL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

_SWAGGER_UI = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.34.0"
_SWAGGER_JS_SRI = "sha384-9Td7MKiIr2S83E1kZyyVi8KdJw/S8CpAvu0llLyMeg0xq/hDGiZJfVn0fV4llCmK"
_SWAGGER_CSS_SRI = "sha384-4c3vsE5f+trhFyHrFrLM1fFGUn531NIaC7dcqEWIJdoRQJ5CkJbSuSCwXLpaS1KF"

_DOCS_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex">
<title>Human Rounds — API</title>
<link rel="stylesheet" href="{_SWAGGER_UI}/swagger-ui.css" integrity="{_SWAGGER_CSS_SRI}" crossorigin="anonymous">
</head>
<body>
<div id="swagger-ui"></div>
<script src="{_SWAGGER_UI}/swagger-ui-bundle.js" integrity="{_SWAGGER_JS_SRI}" crossorigin="anonymous"></script>
<script>SwaggerUIBundle({{url: "/openapi.json", dom_id: "#swagger-ui", deepLinking: true}});</script>
</body>
</html>
""".encode("utf-8")

_METHOD_SUFFIX = re.compile(r"_(get|put|post|delete|options|head|patch|trace)$")


def unique_operation_ids(schema):
    """Give every operation the id `<route name><path>_<its own method>`."""
    seen = set()
    for path_item in schema.get("paths", {}).values():
        for method, operation in path_item.items():
            base = _METHOD_SUFFIX.sub("", operation["operationId"])
            op_id = f"{base}_{method}"
            n = 2
            while op_id in seen:
                op_id = f"{base}_{method}_{n}"
                n += 1
            seen.add(op_id)
            operation["operationId"] = op_id
    return schema


def _ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


_SUCCESS = {"200": "OK", "201": "Created", "204": "Done; no body"}


def apply_body_docs(schema):
    """Turn each route's `api_body.docs(...)` (its `x-turnos-docs`, copied by
    FastAPI into every method of the route) into the requestBody, the success
    answer (200 unless the route names another) and the 400 of the method it
    names, and add the models' schemas."""
    used = False
    for path_item in schema.get("paths", {}).values():
        for method, operation in path_item.items():
            spec = operation.pop("x-turnos-docs", None)
            entry = (spec or {}).get(method)
            if not entry:
                continue
            used = True
            responses = operation.setdefault("responses", {})
            if "body" in entry:
                operation["requestBody"] = {
                    "required": False,
                    "content": {"application/json": {"schema": _ref(entry["body"])}}}
                responses["400"] = {
                    "description": "Cuerpo que no se puede leer o que no corresponde",
                    "content": {"application/json": {"schema": _ref("ApiError")}}}
            if "raw" in entry:
                operation["requestBody"] = {
                    "required": False, "description": entry["raw"]["description"],
                    "content": {media: {"schema": {"type": "string", "format": "binary"}}
                                for media in entry["raw"]["media_types"]}}
            status = entry.get("status", "200")
            if status != "200":
                # FastAPI's own default answer is a 200; this one never answers it.
                responses.pop("200", None)
                responses[status] = {"description": _SUCCESS[status]}
            if "ok" in entry:
                responses.setdefault(status, {"description": "OK"})["content"] = {
                    "application/json": {"schema": _ref(entry["ok"])}}
    if used:
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        for name, body in api_body.component_schemas().items():
            if name in schemas and schemas[name] != body:
                raise ValueError(f"OpenAPI component {name} defined twice")
            schemas[name] = body
    return schema


def _plain_optional(schema):
    """`str | None` in a signature is FastAPI's `anyOf: [string, null]`; a query parameter is never null
    (absent is the default), so it documents as the text it is."""
    variants = [s for s in schema.get("anyOf", ()) if s != {"type": "null"}]
    if "anyOf" in schema and len(variants) == 1:
        del schema["anyOf"]
        for key, value in variants[0].items():
            schema.setdefault(key, value)


def apply_query_docs(schema):
    """Turn each route's `api_body.query_docs(...)` (its `x-turnos-query`) into
    the query `parameters` of the method it names. They are never required:
    the routes read them leniently (an absent or unusable value is the
    route's default)."""
    for path_item in schema.get("paths", {}).values():
        for method, operation in path_item.items():
            for param in operation.get("parameters", []):
                if param.get("in") == "query" and "schema" in param:
                    _plain_optional(param["schema"])
            spec = operation.pop("x-turnos-query", None)
            params = (spec or {}).get(method) or []
            known = {p["name"] for p in operation.get("parameters", []) if p.get("in") == "query"}
            for param in params:
                if param["name"] in known:
                    raise ValueError(f"query parameter {param['name']} documented twice on {method.upper()}")
                operation.setdefault("parameters", []).append({
                    "name": param["name"], "in": "query", "required": False,
                    "description": param["description"], "schema": param["schema"]})
    return schema


# The groups of the documentation, in the order the page lists them: (tag, the
# packages whose routes it holds, what it holds). An operation is in the group of
# the package its endpoint is defined in, the longest package winning; a route
# that names its own `tags=` keeps them. A package with routes and no group is
# an error (`tag_for`), never an untagged operation.
TAGS = (
    ("Patient site", ("patient_web",),
     "The public site: its pages, and the catalog its browser code reads (services, doctors, centers, "
     "the directory, availability)."),
    ("Appointments", ("modules.turnos",),
     "The `turnos` module: booking requests and the desk's review of them, slot holds, offers and "
     "confirmations, the doctors' appointments, calendar feeds, studies and preparations. Answers 404 "
     "while the module is disabled."),
    ("Patient accounts", ("cuentas",),
     "The patient's account: sign-up, sign-in, profile, dependents and the identity checks."),
    ("Pre-visit interview", ("modules.intake",),
     "The `intake` module: the pre-consultation interview, its triage and the doctor's review. Answers "
     "404 while the module is disabled."),
    ("Chat", ("modules.chat",),
     "The `chat` module: the booking chat on the site, WhatsApp, voice and the MCP connection. Answers "
     "404 while the module is disabled."),
    ("AI", ("ai",),
     "Lab results read by the AI, the chart summary, the editable prompts, AI costs and health."),
    ("Staff sign-in", ("auth",),
     "Staff sign-in and sign-out, passkeys, password reset and the login screen's content."),
    ("Staff panel", ("admin_panel",),
     "The staff panel's screens and the APIs behind them: queue, patients, doctors, content, reports, "
     "users, roles and settings."),
    ("Installation", ("ops",), "The first-run setup and the demo installation."),
    ("Connectors", ("connectors",), "Country-specific connectors (the coverage lookup)."),
    ("Platform", ("core", "routers"),
     "Health and readiness, the enabled modules, the site's identity, the interface string catalog."),
    ("Legacy URLs", ("core.legacy_urls_router",),
     "Old Spanish page addresses, kept as redirects to the current ones."),
)

_DESCRIPTION = """\
The HTTP API of a Human Rounds installation: the public site, the patient's
account and the staff panel call it from the browser. It is the application's
own interface, not a versioned public contract; this page and its schema are
served to a signed-in admin only (`scripts/export_openapi.py` writes the same
schema to a file).

**Signing in.** Two session cookies, both HttpOnly: `st_admin` for staff
(`staffSession`) and `st_ses` for patients (`patientSession`). What a staff
session may do is decided by its role and permissions; an installation where
staff or patients sign in through the municipal portal takes the identity from
the portal's gateway instead of the cookie.

**Errors.** A JSON error is one flat object, `{"error": "<what went wrong>"}`
(`ApiError`), sometimes with fields of its own. A JSON body that cannot be
read or does not match answers 400; no session 401; a session whose role may
not do it 403; a disabled module 404, as if the path did not exist; a
database that does not answer 503 (with `Retry-After` when asking again is
worth it). The exception is FastAPI's own 422 (`{"detail": [...]}`) for a
typed path parameter or a form field that does not validate.
"""

_LICENSE = {"name": "AGPL-3.0", "url": "https://www.gnu.org/licenses/agpl-3.0.html"}

# TAGS names packages inside the application package (`patient_web`, `modules.turnos`);
# a route's module carries the application package in front (`app.patient_web.site_shell`).
_APP_PREFIX = __name__[: -len("routers.api_docs")]


def tag_for(module):
    """The group of the routes defined in `module` (a dotted module name)."""
    if _APP_PREFIX and module.startswith(_APP_PREFIX):
        module = module[len(_APP_PREFIX):]
    best = None
    for name, packages, _about in TAGS:
        for package in packages:
            if (module == package or module.startswith(package + ".")) and (
                    best is None or len(package) > len(best[1])):
                best = (name, package)
    if best is None:
        raise ValueError(f"no OpenAPI group for the routes of {module}: add its package to app/routers/api_docs.TAGS")
    return best[0]


def _schema_routes(routes):
    """The documented `APIRoute`s, in registration order. FastAPI 0.141 keeps an
    `include_router()` lazy (an `_IncludedRouter` holding `original_router`);
    an older one flattens it into plain routes."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _schema_routes(inner.routes)
        elif isinstance(route, APIRoute) and route.include_in_schema:
            yield route


def _route_index(app):
    """(path, method) -> the route that answers it: the first one registered, as Starlette matches."""
    index = {}
    for route in _schema_routes(app.routes):
        for method in route.methods:
            index.setdefault((route.path_format, method.lower()), route)
    return index


def _calls(dependant):
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _calls(dependency)


# Every closure the two staff gates return shares its function's code.
_STAFF_API_GATE = deps.require_admin().__code__
_STAFF_SCREEN_GATE = deps.require_admin_screen().__code__


def _gate_kinds(route):
    kinds = set()
    for call in _calls(route.dependant):
        code = getattr(call, "__code__", None)
        if code is _STAFF_API_GATE:
            kinds.add("staff_api")
        elif code is _STAFF_SCREEN_GATE:
            kinds.add("staff_screen")
        elif call is account_deps.current_account:
            kinds.add("patient")
    return kinds


def gated_operations(app):
    """{"staff_api" | "staff_screen" | "patient": [(path, method), ...]} — the documented operations
    behind each gate, as their routes declare it."""
    out = {"staff_api": [], "staff_screen": [], "patient": []}
    for (path, method), route in _route_index(app).items():
        for kind in _gate_kinds(route):
            out[kind].append((path, method))
    return out


def _error(description):
    return {"description": description, "content": {"application/json": {"schema": _ref("ApiError")}}}


_GATE_ANSWERS = {
    "staff_api": {
        "401": _error("No staff session, or it expired"),
        "403": _error("The session's role may not do this, or two-factor setup is due"),
    },
    "staff_screen": {
        "303": {"description": "No staff session: to the staff login, back here after it (or to the "
                               "two-factor setup when it is due)"},
        "403": {"description": "The session's role may not open this screen"},
    },
    "patient": {"401": _error("No patient session")},
}
_DB_UNAVAILABLE = _error("The database does not answer; `Retry-After` says when to ask again")

# Words a route name carries that say nothing about the operation.
_GENERIC_WORDS = {"endpoint", "html"}


def _summary(route):
    """A sentence-case summary from the route's name (`_me_endpoint` -> «Me», `api_admin_login` ->
    «Admin login»); a name of generic words only (a factory's `_html_endpoint`) gives the path."""
    words = [word for word in route.name.lower().split("_") if word]
    if words[:1] == ["api"]:
        words = words[1:]
    while words and words[-1] == "endpoint":
        words.pop()
    if not words or set(words) <= _GENERIC_WORDS:
        return route.path_format
    return " ".join(words).capitalize()


def _with(security, scheme):
    requirement = {scheme: []}
    return security if requirement in security else security + [requirement]


_TEXT = (str, str | None, list[str], list[str] | None)


def _only_lenient_queries(route):
    """True when every parameter of `route` is an optional text query parameter: one FastAPI never refuses
    (any value is text, an absent one is the default) — the routes parse it leniently themselves."""
    queries, stack = [], [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.path_params or dependant.header_params or dependant.cookie_params or dependant.body_params:
            return False
        queries += dependant.query_params
        stack += dependant.dependencies
    return bool(queries) and all(
        not field.field_info.is_required() and field.field_info.annotation in _TEXT for field in queries)


def describe_operations(schema, app):
    """Each operation's group, summary and the common answers of the gate in front of it."""
    index = _route_index(app)
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas.setdefault("ApiError", api_body.component_schemas()["ApiError"])
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            route = index.get((path, method))
            if route is None:
                raise ValueError(f"OpenAPI operation {method.upper()} {path} has no route")
            if not route.tags:
                operation["tags"] = [tag_for(route.endpoint.__module__)]
            if route.summary is None:
                operation["summary"] = _summary(route)
            kinds = _gate_kinds(route)
            security = [s for s in operation.get("security", []) if s]
            if operation.pop("x-turnos-gate", None) == "staff":
                kinds.add("staff_api")
                security = _with(security, "staffSession")
            session = operation.pop("x-turnos-session", None)
            if session == "required":
                kinds.add("patient")
                security = _with(security, "patientSession")
            if security:
                operation["security"] = ([{}] if session == "optional" else []) + security
            responses = operation.setdefault("responses", {})
            if _only_lenient_queries(route):
                # FastAPI documents its 422 for any parameter; these never get one.
                responses.pop("422", None)
            for kind in sorted(kinds):
                for code, answer in _GATE_ANSWERS[kind].items():
                    responses.setdefault(code, answer)
            if path.startswith("/api/"):
                responses.setdefault("503", _DB_UNAVAILABLE)
    return schema


def build_schema(app):
    with warnings.catch_warnings():
        # The duplicates FastAPI warns about are exactly what
        # unique_operation_ids() rewrites.
        warnings.filterwarnings("ignore", message="Duplicate Operation ID")
        schema = get_openapi(
            title=app.title, version=version.short(), openapi_version=app.openapi_version,
            description=_DESCRIPTION, license_info=_LICENSE, routes=app.routes,
            tags=[{"name": name, "description": about} for name, _packages, about in TAGS])
    return describe_operations(apply_query_docs(apply_body_docs(unique_operation_ids(schema))), app)


async def _schema(app):
    if app.openapi_schema is None:
        app.openapi_schema = await run_in_threadpool(build_schema, app)
    return app.openapi_schema


async def _is_admin(request):
    try:
        await deps.require_admin(solo_admin=True)(request)
    except (deps.AdminGateError, db.DbUnavailable):
        return False
    return True


async def _terminal(request):
    return await native_terminal.terminal(request.url.path.lstrip("/"), request)


@router.api_route("/openapi.json", methods=_TERMINAL_METHODS, include_in_schema=False)
async def openapi_json(request: Request):
    if request.method != "GET" or not await _is_admin(request):
        return await _terminal(request)
    return json_response(await _schema(request.app))


@router.api_route("/docs", methods=_TERMINAL_METHODS, include_in_schema=False)
async def swagger_ui(request: Request):
    if request.method != "GET" or not await _is_admin(request):
        return await _terminal(request)
    return Response(content=_DOCS_HTML, status_code=200, media_type=None, headers={
        "Content-Type": "text/html; charset=utf-8",
        "Content-Length": str(len(_DOCS_HTML)),
        "Cache-Control": "no-store",
    })
