"""Shared native FastAPI dependencies.

``require_admin`` extracts plain request values and delegates role policy to
``admin_auth.evaluate_admin_gate``. It converts a rejected domain result into
an HTTP exception; it does not construct, imitate, or forward a legacy request
handler. Authentication routers call the same value-only domain boundary.

``require_admin_screen`` is the variant for server-routed HTML screens: a 401
gate failure (no session) becomes a 303 redirect to /admin/login?next=<path>,
a 403 "2fa_requerido" becomes a 303 to the Account 2FA setup, and any other
403 renders the standalone forbidden page (JSON for non-browser Accept).
"""

import json
import logging
import sys
import threading
import time
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import APIKeyCookie
from starlette.concurrency import run_in_threadpool

from app.auth import admin_auth
from app.core import api_body
from app.core import app_modules
from app.core import db
from app.core import http_json
from app.core import i18n
from app.core import launch_signals


# The staff session cookie as an OpenAPI security scheme (`staffSession`): the two gates below declare it,
# so every operation behind them documents the requirement. It decides nothing — the gate reads the
# credential itself (`admin_auth.request_credential`), and never raises from here (`auto_error=False`).
STAFF_SESSION = APIKeyCookie(
    name=admin_auth.COOKIE_NAME,
    scheme_name="staffSession",
    description="The staff session cookie, set by `POST /api/admin/login` (or a passkey sign-in). Where "
                "staff sign in through the municipal portal the identity comes from the portal's gateway "
                "instead. The role and its permissions decide what the session may do.",
    auto_error=False,
)

# `openapi_extra` of a route whose handler awaits the staff gate itself (`check_admin`, or its router's own
# call of `admin_auth`), usually to keep a check ahead of it: `routers/api_docs.py` documents it like a
# declared gate — the session and the 401/403. Documentation only. `tests/test_api_docs_schema.py` sends
# every staff and patient operation an anonymous request, so a route that answers 401 and documents no
# session is red.
STAFF_GATE_DOCS = {"x-turnos-gate": "staff"}
# The same for the patient's session (`cuentas/account_deps.py`): a route that reads it in its handler and
# answers 401 without one, and a route an anonymous visitor may use too (FastAPI lists every declared
# scheme as required). Here, not in `cuentas`, so a router can name them without importing the accounts.
PATIENT_SESSION_DOCS = {"x-turnos-session": "required"}
PATIENT_SESSION_OPTIONAL_DOCS = {"x-turnos-session": "optional"}


class AdminGateError(HTTPException):
    """Auth-gate failure carrying the public flat 401/403 JSON contract."""


class ScreenGateError(HTTPException):
    """Auth-gate failure for HTML screens: 401→login redirect, 403 2FA→setup
    redirect, other 403→forbidden page (JSON for non-browser Accept)."""


class PatientNotSignedIn(HTTPException):
    """A route that needs the patient's account (`cuentas.account_deps.current_account`) was called
    without one."""

    def __init__(self):
        super().__init__(status_code=401)


class EarlyResponse(Exception):
    """A dependency's own answer: the ready `Response` its route used to return from the handler before
    anything else ran (a path that does not name a known item, a closed entry, a refused gate). Raising it
    from a dependency stops the request there, so a check the route makes ahead of its body or its query
    keeps its place once those are dependencies too: FastAPI resolves a route's dependencies in the order of
    its signature. `render_early_response` sends the response as it is. No `status_code` attribute, on
    purpose: an answer is not an error, and Sentry's Starlette integration only reports exceptions that
    carry one."""

    def __init__(self, response):
        super().__init__(response.status_code)
        self.response = response


async def render_early_response(_request, exc: EarlyResponse) -> Response:
    return exc.response


def install_early_response(app) -> None:
    """The handler alone, for an app built around one router (the tests')."""
    app.add_exception_handler(EarlyResponse, render_early_response)


_PATIENT_NOT_SIGNED_IN_BODY = b'{"error": "no_autenticado"}'


def render_patient_not_signed_in(_request, _exc) -> Response:
    """The 401 the patient account routes built by hand (`cuentas.native_result.Result.json`), byte for
    byte: the same body, headers and header order (`tests/test_account_deps.py` compares the two)."""
    return Response(content=_PATIENT_NOT_SIGNED_IN_BODY, status_code=401,
                    media_type="application/json; charset=utf-8",
                    headers={"Cache-Control": "no-store", "Content-Length": str(len(_PATIENT_NOT_SIGNED_IN_BODY))})


def render_admin_gate_error(_request, exc: AdminGateError) -> Response:
    """Render the stable flat error body used by all native consumers."""
    body = json.dumps(exc.detail, ensure_ascii=False).encode("utf-8")
    return Response(
        content=body,
        status_code=exc.status_code,
        media_type=None,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
        },
    )


def render_screen_gate_error(request: Request, exc: ScreenGateError) -> Response:
    """Render auth-gate failures for HTML screens.

    A 401 (no session) redirects to /admin/login with ?next=<canonical_path>.
    A 403 "2fa_requerido" redirects to the only screen that can repair it
    (the same /admin/account?setup=2fa target the client contract uses), so
    typing a screen URL with an expired enrolment deadline lands on the setup
    flow instead of raw JSON. Every other 403 renders the standalone Spanish
    forbidden page for browsers (API-shaped clients still get the exact JSON
    body via Accept negotiation).
    """
    if exc.status_code == 401:
        from app.admin_panel import admin_navigation
        # Preserve validated editor state through a server-side session expiry,
        # matching the client 401 continuation. Unknown query data stays out.
        full_target = request.url.path + ("?" + request.url.query if request.url.query else "")
        canonical = (admin_navigation.canonical_admin_target(full_target)
                     or admin_navigation.canonical_admin_target(request.url.path))
        if canonical:
            target = f"/admin/login?{urlencode({'next': canonical})}"
        else:
            target = "/admin/login"
        return Response(
            status_code=303,
            headers={"Location": target},
        )
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    if detail.get("error") == "2fa_requerido":
        return Response(
            status_code=303,
            headers={"Location": "/admin/account?setup=2fa"},
        )
    if "text/html" in (request.headers.get("accept") or ""):
        from app.auth import forbidden_screen
        body = forbidden_screen.page_html(request.url.path)
        return Response(
            content=body,
            status_code=exc.status_code,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Content-Length": str(len(body)),
                "Cache-Control": "no-store",
            },
        )
    body = json.dumps(exc.detail, ensure_ascii=False).encode("utf-8")
    return Response(
        content=body,
        status_code=exc.status_code,
        media_type=None,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
        },
    )


def install_gate_handlers(app) -> None:
    """Register the API (flat JSON) and screen (303 to login) gate renderers, the patient routes' 401, the
    503 for a pool-exhausted `DbUnavailable` no route caught, a dependency's own early answer, and the family
    answer for a request body that does not decode (`core.api_body`)."""
    app.add_exception_handler(AdminGateError, render_admin_gate_error)
    app.add_exception_handler(ScreenGateError, render_screen_gate_error)
    app.add_exception_handler(PatientNotSignedIn, render_patient_not_signed_in)
    install_early_response(app)
    install_db_unavailable_handler(app)
    api_body.install(app)


_DB_UNAVAILABLE_BODY = b'{"error": "base de datos no disponible"}'

# One `db_unavailable` launch signal per this many seconds per process (the product owner's hub, 2026-11-10): an outage
# answers every request with the 503, and the signal tells that it happened, not how often. The 503 itself
# is a warning, which reaches no error tracker.
_DB_UNAVAILABLE_SIGNAL_EVERY_S = 900
_db_signal_lock = threading.Lock()
_db_signal_last = []  # the monotonic time of this process's last signal, once there was one
_clock = time.monotonic  # the test seam


def _report_db_unavailable():
    launch_signals.handled("db_unavailable")


def signal_db_unavailable():
    """Reports a 503 «database unavailable» as the closed code `db_unavailable` (core/launch_signals.py) at most
    once per `_DB_UNAVAILABLE_SIGNAL_EVERY_S` per process, so the hourly counter is not written on every refused
    request either. Called only by `answer_db_unavailable` below, so every such 503 shares this one window. The
    report runs on its own thread: `handled` writes that counter to the very database that is failing, and that
    wait must hold neither the answer nor the event loop. Returns the thread, or None inside the window. Never
    raises."""
    now = _clock()
    with _db_signal_lock:
        if _db_signal_last and now - _db_signal_last[0] < _DB_UNAVAILABLE_SIGNAL_EVERY_S:
            return None
        _db_signal_last[:] = [now]
    try:
        thread = threading.Thread(target=_report_db_unavailable, name="db-unavailable-signal", daemon=True)
        thread.start()
        return thread
    except Exception:  # noqa: BLE001 — no thread left to start: the 503 still answers
        return None


DB_UNAVAILABLE = {"error": "base de datos no disponible"}


# The patient's own appointment routes (booking, cancelling, the waitlist offer, the reminder's confirmation): an
# unknown outcome there gives the patient the next step — look at «Mis turnos» — instead of a staff sentence
# (remedies, not errors). Sign-in and sign-up (/api/account) are not about an appointment: neutral sentence.
_PATIENT_PATHS = ("/api/requests", "/api/offer", "/api/confirm", "/api/appointment", "/appointment")


def _patient_request(request):
    path = getattr(getattr(request, "url", None), "path", "") or ""
    return any(path == p or path.startswith(p + "/") for p in _PATIENT_PATHS)


def _commit_unknown(request=None):
    """The body for `db.DbCommitUnknown`: the commit broke, so the write may have landed — the answer says the
    outcome is unknown instead of inviting a retry (the product owner's hub review, 2026-11-11). On a patient route it is the
    patient's sentence plus the way to check (`detalle` + `accion`, the shape of the booking limit's answer)."""
    lang = i18n.active()
    if _patient_request(request):
        text = i18n.t_or("admin.srv.error.db_resultado_desconocido_paciente",
                         "No sabemos si quedó guardado. Revisá antes de repetir.", lang)
        return {"error": text, "detalle": text, "outcome": "unknown",
                "accion": {"texto": i18n.t_or("service.success.view_requests", "Ver mis turnos", lang),
                           "url": "/my-requests"}}
    return {"error": i18n.t_or("admin.srv.error.db_resultado_desconocido",
                               "la base no respondió al guardar, revisá si quedó antes de repetir", lang),
            "outcome": "unknown"}


def answer_db_unavailable(build=None, *, body=None, request=None, exc=None):
    """THE answer for a database that does not answer (one of `DB_UNAVAILABLE_ERRORS` below, nothing broader):
    reports it through `signal_db_unavailable` and answers 503. Every 503 about the database goes through here —
    the routes that catch `DbUnavailable` themselves, the domain results they render and the handler for the one
    no route caught — so a new route cannot forget the signal (the product owner's hub, 2026-11-10);
    `tests/test_db_unavailable_answer.py` turns any other 503 in the server code red.

    The answer is the one each caller always sent, byte for byte:
      * by default the JSON of `core.http_json` with `body` (`DB_UNAVAILABLE` when None), compressed by
        `gzip_json_response` when the caller passes its `request`;
      * `build(status)` for any other shape — an HTML page, a `Result`, an `AuthResult`, a `(status, body)`
        tuple, an `HTTPException` to raise — whatever `build` returns is returned.

    The one exception to «byte for byte»: when the error being answered (`exc`, by default the one the caller is
    handling) is `db.DbCommitUnknown`, the default JSON says the outcome is unknown (`_commit_unknown`) instead
    of «base de datos no disponible»; a caller's own `body` or `build` keeps its shape."""
    signal_db_unavailable()
    status = 503
    if build is not None:
        return build(status)
    if body is None:
        failed = exc if exc is not None else sys.exc_info()[1]
        payload = _commit_unknown(request) if isinstance(failed, db.DbCommitUnknown) else DB_UNAVAILABLE
    else:
        payload = body
    if request is not None:
        return http_json.gzip_json_response(request, payload, status)
    return http_json.json_response(payload, status)


def _uncaught_db_unavailable(status, body=_DB_UNAVAILABLE_BODY, retry=True):
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(body)),
        "Cache-Control": "no-store",
    }
    if retry:
        headers["Retry-After"] = "3"
    return Response(content=body, status_code=status, media_type=None, headers=headers)


async def render_db_unavailable(_request, _exc) -> Response:
    """The answer for a `db.DbUnavailable` no route caught: the same 503 and body the
    routes that do catch it already send, instead of a bare «500 Internal Server Error».
    It is what an exhausted pool (every connection busy for longer than
    `TURNOS_DB_POOL_TIMEOUT`) looks like to a handler without its own try/except;
    `Retry-After` tells a client it is worth asking again. The reason is logged: it is what
    tells «pool busy» from «database broken» (wrong credentials, renamed database). A `db.DbCommitUnknown` (the
    commit broke, the write may have landed) answers that the outcome is unknown and without `Retry-After`."""
    logging.getLogger("uvicorn.error").warning("DbUnavailable sin atrapar: %s", _exc)
    if isinstance(_exc, db.DbCommitUnknown):
        body = json.dumps(_commit_unknown(_request), ensure_ascii=False).encode("utf-8")
        return answer_db_unavailable(lambda status: _uncaught_db_unavailable(status, body, retry=False))
    return answer_db_unavailable(_uncaught_db_unavailable)


# What «the database does not answer» is, for the handler below and for a route that has to catch it among
# other failures: only these answer `answer_db_unavailable`; anything else is that route's own error, without
# the signal (the product owner's hub, 2026-11-10: db_unavailable means the database is unavailable and nothing else). A
# connection lost under a statement is one of them: the pooled `with` raises it as `db.DbConnectionLost`.
DB_UNAVAILABLE_ERRORS = db.UNAVAILABLE_ERRORS


def install_db_unavailable_handler(app) -> None:
    for error in DB_UNAVAILABLE_ERRORS:
        app.add_exception_handler(error, render_db_unavailable)


def require_admin(solo_admin=False, permitir_medico=False,
                   permitir_admisionista=False, permitir_reportes=False,
                   permitir_marca=False):
    """Return a FastAPI dependency that yields an authorized identity.

    A rejected gate becomes ``AdminGateError`` with the status and body from
    ``admin_auth.evaluate_admin_gate``. The matched route TEMPLATE travels
    with the request values: it is what ``core.permissions`` maps to
    the permission the route needs.
    """

    async def _dep(request: Request, _session: str | None = Depends(STAFF_SESSION)) -> dict:
        cookie_token = admin_auth.request_credential(request)
        route = getattr(request.scope.get("route"), "path", None)
        gate = await run_in_threadpool(
            admin_auth.evaluate_admin_gate, cookie_token, request.method,
            request.url.path, solo_admin, permitir_medico, permitir_admisionista,
            permitir_reportes, permitir_marca, route=route,
        )
        if not gate.ok:
            raise AdminGateError(status_code=gate.status, detail=gate.body)
        # Request-scoped role for the direct-screen render (admin_auth's
        # bind_role docstring): every /admin/* screen that passes THIS gate
        # leaves its verified role readable as {{ screen_rol() }} in Jinja —
        # the shared base initializes shell facilities (the triage bell)
        # from it without each route passing its own context variable.
        # Set in the async task that will also run the route body, so the
        # ContextVar is live exactly for this request's render and nothing
        # else; it authorizes nothing by itself.
        admin_auth.bind_role(gate.identity.get("rol"))
        admin_auth.bind_permissions(gate.permissions)
        admin_auth.bind_2fa(gate.security)
        return gate.identity

    return _dep


async def check_admin(request, solo_admin=False, permitir_medico=False,
                      permitir_admisionista=False, permitir_reportes=False,
                      permitir_marca=False):
    """Run ``require_admin(...)`` on ``request`` right now and return the identity.

    For a route that awaits the gate by hand instead of injecting it with ``Depends(...)``, so an
    id-format check can run BEFORE authenticating and keep that order. Raises ``AdminGateError`` on
    rejection; the callers do not catch it (its handler renders the flat 401/403 body)."""
    return await require_admin(
        solo_admin=solo_admin, permitir_medico=permitir_medico,
        permitir_admisionista=permitir_admisionista,
        permitir_reportes=permitir_reportes, permitir_marca=permitir_marca,
    )(request)


def require_admin_screen(solo_admin=False, permitir_medico=False,
                         permitir_admisionista=False, permitir_reportes=False,
                         permitir_marca=False):
    """Return a FastAPI dependency for server-routed HTML screens.

    Like ``require_admin``, but a 401 gate failure raises ``ScreenGateError``
    instead of ``AdminGateError``, so the screen exception handler can redirect
    to login with a next parameter instead of returning JSON.
    """

    async def _dep(request: Request, _session: str | None = Depends(STAFF_SESSION)) -> dict:
        cookie_token = admin_auth.request_credential(request)
        gate = await run_in_threadpool(
            admin_auth.evaluate_admin_gate, cookie_token, request.method,
            request.url.path, solo_admin, permitir_medico, permitir_admisionista,
            permitir_reportes, permitir_marca,
        )
        if not gate.ok:
            raise ScreenGateError(status_code=gate.status, detail=gate.body)
        # Request-scoped role for the direct-screen render, same as require_admin
        admin_auth.bind_role(gate.identity.get("rol"))
        admin_auth.bind_permissions(gate.permissions)
        admin_auth.bind_2fa(gate.security)
        return gate.identity

    return _dep


def require_module_enabled(module_id):
    """Tier 4 slice 3 (docs/roadmap/tier4-replatform-design.md, "Module
    contract adapter") — the dependency a `router_attr`-mounted endpoint
    declares to get the SAME observable enable/disable behavior the legacy
    registry gets for free from `routing.dispatch()`'s
    `app_modules.route_enabled()` check: `Depends(require_module_enabled
    ("intake"))` raises a plain 404 the moment `app_modules.is_enabled(id)`
    is False.

    This has to happen at the DEPENDENCY layer, not by un-mounting the
    router, because Starlette can't cleanly remove a route at runtime — the
    router is mounted ONCE, unconditionally, at `app.py` import time (see
    `app_modules.mount_module_routers()`), exactly mirroring how a legacy
    route module is imported into `route_modules()`'s registry once and
    gated per-request afterward, never re-imported on enable/disable.

    404, not 401/403: a disabled module's route must be INDISTINGUISHABLE
    from a path that never existed (the product owner, 2026-08-05 — see docs/modules.md,
    "What disabling turnos actually takes down today") — the same doctrine
    `server.py`'s static-file fallback already enforces for the legacy
    path, ported here for the ASGI one. Checks ONLY `is_enabled()`, not
    broken-ness — exact parity with `route_enabled()`, which never checks
    broken-ness either (a broken module's router never gets mounted at all
    by `mount_module_routers()`, so there's nothing left to gate here)."""

    async def _dep():
        if not app_modules.is_enabled(module_id):
            raise HTTPException(status_code=404, detail={"error": "no_encontrado"})
        return True

    return _dep
