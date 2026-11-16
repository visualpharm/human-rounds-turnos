"""booking_requests_router.py — the FastAPI `router` for `booking_requests.py`
(family F7 of docs/roadmap/native-migration-campaign-20260826.md): the
patient-facing booking-request flow layered with the staff/admin
review-queue console.

Real, independent `async def` endpoints. Each
endpoint parses the request natively (path params, query string, JSON/form
body, session identity) and calls `booking_requests.py`'s extracted
`_xxx_core(...)` function, returning `(status, payload)` (or the
streaming-file tuple `(status, is_json, body, content_type,
extra_headers)`). There is exactly ONE implementation of every business
rule. Synchronous DB/blocking work runs through
`starlette.concurrency.run_in_threadpool`.

Patient-facing (no auth): GET+POST /api/requests, GET /api/requests/{id},
POST {id}/cancel|reprogramar|waitlist, POST /api/analyze-order, GET
/api/orden-preview/{token}.

Staff/admin, migrated: the Settings triad (GET+POST
theme/auth-mode/turnstile), GET
/api/admin/requests (queue), GET /api/admin/refeps, GET
/api/admin/order/{id}, GET /api/admin/hsi-sync (status only), POST
hsi-sync/resolver, POST hsi-sync/conflicts, and the six per-request admin
actions (approve/assign/retry-hsi/reject/campos/attendance) — the CAS-
guarded rows wave 1 deliberately deferred (see
docs/roadmap/native-migration-campaign-20260826.md, family W2d): the
domain logic (including the `store.AlreadyProcessed`/`store.SlotConflict`
compare-and-swap handling `/approve` and `/reject` rely on) was extracted
VERBATIM into `booking_requests.py`'s own `_approve_core`/`_assign_core`/
`_retry_hsi_core`/`_reject_core`/`_edit_field_core`/`_mark_attendance_core`/
`_hsi_resolver_run_core`/`_hsi_conflicts_apply_core` — zero behavior
change; `test_integrity_races.py` continues to cover the same business logic.
Registration ORDER matters — hsi-sync/resolver + hsi-sync/conflicts sit
BEFORE `/api/admin/order/{req_id}` (test_app_composition.py pins the
inventory).

Auth — staff endpoints below call `core.deps.require_admin(...)`, a
structural port of `admin_auth.require_admin`. `_require_admin` below is a
THIN call-through to it, awaited manually (not injected via
`Depends(...)` on the endpoint signature) so the handful of endpoints that
run an id-format check BEFORE authenticating (`api_admin_order`, and the
six admin actions below) keep that pre-auth-check ordering — a param-level
`Depends` would resolve before the endpoint body runs, silently reordering
auth ahead of the id check for a malformed-id-plus-unauthenticated request.
Rejections raise `core.deps.AdminGateError` (an `HTTPException` subclass);
`app.py` registers a handler for it that renders the flat error body
(`render_admin_gate_error`) — callers here do NOT catch it, they let it
propagate, same mechanism the ~22 other admin screens use via
`Depends(...)`.

Terminal 404: a non-numeric `{req_id}`/an out-of-shape `{token}` — FastAPI
matched the route, but the path contract declines it — returns the
SHARED terminal 404 (`core.terminal_response.
json_not_found_response()`: body `{"error": "not found"}`, no-store); see
`test_module_router_fallthrough`, which pins `GET
/api/requests/not-a-number` to that shape.
"""

import re
import sys

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from app.ai import abuse_guard
from app.core import api_body, db, deps
from app.core.terminal_response import json_not_found_response
from app.modules.turnos import api_models
from app.modules.turnos import booking_requests as bkr
from app.modules.turnos import booking_create
from app.modules.turnos import booking_decisions
from app.modules.turnos import booking_emr
from app.modules.turnos import booking_patient
from app.modules.turnos import booking_queue
from app.modules.turnos import booking_referrals
from app.modules.turnos import hsi_journal
from app.modules.turnos import panel_settings
from app import connectors
from app.core.http_json import gzip_json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])

_ID_RE = re.compile(r"^\d+$")


def _hsi_selected():
    return connectors.resolve(connectors.EMR_SYNC)[0] == "hsi"


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _terminal_404():
    """The shared terminal 404 for path shapes the endpoints decline —
    same bytes `routers/native_terminal.py` serves."""
    return json_not_found_response()


def _db_unavailable_response(request):
    """«No disponible» promises that asking again is safe; a broken commit (`db.DbCommitUnknown`) may have
    written, so it answers the default body instead — on a patient route, the sentence and the way to check
    «Mis turnos» (`deps._commit_unknown`)."""
    from app.core import i18n
    if isinstance(sys.exc_info()[1], db.DbCommitUnknown):
        return deps.answer_db_unavailable(request=request)
    return deps.answer_db_unavailable(body={"error": i18n.t_or(
        "admin.srv.error.db_no_disponible", "base de datos no disponible",
        i18n.active())}, request=request)


def _stream_response(request, tup):
    """Builds the final Response from the shared `(status, is_json, body,
    content_type, extra_headers)` tuple `_referral_stream_response()`/
    `_orden_preview_core()` returns."""
    status, is_json, body, content_type, headers = tup
    if is_json:
        return _json_response(request, body, status)
    out_headers = {"Cache-Control": "no-store", "Content-Length": str(len(body))}
    out_headers.update(headers)
    return Response(content=body, status_code=status, media_type=content_type,
                     headers=out_headers)


def _qs_like_legacy(request):
    """`{key: [values]}` shape (`urllib.parse.parse_qs`) built from the
    native query string."""
    from urllib.parse import parse_qs
    return parse_qs(request.url.query)


# ---- native admin auth gate (see module docstring) -------------------------

# Thin call-through to `core.deps.require_admin(...)` (native-migration F1's shim-free rewrite of the SAME
# gate `admin_auth.require_admin` itself uses) — no local reimplementation of the decision tree. Awaited
# manually instead of injected via `Depends(...)` on the endpoint signature so the handful of callers below
# that run an id-format check BEFORE authenticating keep that exact ordering. Raises
# `core.deps.AdminGateError` on rejection; callers do NOT catch it — see module docstring for why that's
# correct.
_require_admin = deps.check_admin


async def _owner(request: Request):
    """The owner's gate as a dependency, for a route that checks nothing before it."""
    return await _require_admin(request, solo_admin=True)


# ---- patient-facing ---------------------------------------------------------

@router.api_route("/api/requests", methods=["GET", "POST"], openapi_extra=api_body.docs(
    get=(None, api_models.PatientRequestsOut),
    post=(api_models.BookingRequestIn, api_models.BookingRequestOut)) | deps.PATIENT_SESSION_DOCS)
async def api_requests(
        request: Request,
        # Decoded below, after the 401 of a visitor without a session.
        pending: api_body.PendingBody = Depends(api_body.deferred(
            api_models.BOOKING, api_models.BookingRequestIn, methods={"POST"}))):
    """ONE route object for both methods (`add_api_route(...,
    methods=["GET","POST"])`'s native twin) — this campaign's own
    `route_objects` gate counts route OBJECTS, not method rows, so
    splitting this into two `@router.get`/`@router.post` decorators would
    silently inflate that count."""
    usuario = await run_in_threadpool(bkr._get_user, request)
    if request.method == "GET":
        try:
            status, payload = await run_in_threadpool(booking_patient._patient_list_core, usuario)
        except bkr.store.DbUnavailable:
            return _db_unavailable_response(request)
        return _json_response(request, payload, status)
    # POST
    if not usuario:
        return _json_response(request, {"error": "auth_requerida"}, 401)
    body = await pending.read()
    try:
        status, payload = await api_body.run(body, booking_create.create_request, usuario, body.value)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.get("/api/requests/{req_id}",
            openapi_extra=api_body.docs(get=(None, api_models.PatientRequestOut)) | deps.PATIENT_SESSION_DOCS)
async def api_request_detail(req_id: str, request: Request):
    if not _ID_RE.match(req_id):
        return _terminal_404()
    usuario = await run_in_threadpool(bkr._get_user, request)
    try:
        status, payload = await run_in_threadpool(booking_patient._patient_detail_core, usuario, int(req_id))
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.api_route("/api/requests/{req_id}/coverage", methods=["GET", "POST"],
                  openapi_extra=api_body.docs(get=(None, api_models.CoverageStateOut),
                                              post=(api_models.CoverageChoiceIn,
                                                    api_models.CoverageChoiceOut)) | deps.PATIENT_SESSION_DOCS)
async def api_request_coverage(
        req_id: str, request: Request,
        # Decoded below, after the id, the session and the size cap.
        pending: api_body.PendingBody = Depends(api_body.deferred(
            api_models.PLAIN, api_models.CoverageChoiceIn, methods={"POST"}))):
    if not _ID_RE.match(req_id):
        return _terminal_404()
    usuario = await run_in_threadpool(bkr._get_user, request)
    body, value = api_body.NO_BODY, None
    if request.method == "POST":
        if len(await pending.load()) > 16384:
            return _json_response(request, {"error": "body demasiado grande"}, 413)
        body = await pending.read()
        value = {} if body.value is None else body.value
    from app.modules.turnos import postbooking_coverage
    try:
        status, payload = await api_body.run(
            body, postbooking_coverage.respond, usuario, int(req_id), value)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.post("/api/requests/{req_id}/cancel",
             openapi_extra=api_body.docs(post=(None, api_models.CancelOut)) | deps.PATIENT_SESSION_DOCS)
async def api_request_cancel(req_id: str, request: Request):
    if not _ID_RE.match(req_id):
        return _terminal_404()
    usuario = await run_in_threadpool(bkr._get_user, request)
    dni = await run_in_threadpool(bkr.session_dni, usuario)
    try:
        status, payload = await run_in_threadpool(booking_patient.cancel_request, dni, int(req_id))
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.post("/api/requests/{req_id}/reprogramar",
             openapi_extra=api_body.docs(post=(api_models.RescheduleIn, api_models.RescheduleOut)) | deps.PATIENT_SESSION_DOCS)
async def api_request_reprogramar(
        req_id: str, request: Request,
        pending: api_body.PendingBody = Depends(api_body.lazy(api_models.WITH_REASON, api_models.RescheduleIn))):
    if not _ID_RE.match(req_id):
        return _terminal_404()
    usuario = await run_in_threadpool(bkr._get_user, request)
    dni = await run_in_threadpool(bkr.session_dni, usuario)
    body = await pending.lazy()
    try:
        status, payload = await api_body.run(
            body, booking_patient._reschedule_core, request, dni, int(req_id), body)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.post("/api/requests/{req_id}/waitlist",
             openapi_extra=api_body.docs(post=(api_models.WaitlistIn, api_models.WaitlistOut)) | deps.PATIENT_SESSION_DOCS)
async def api_request_waitlist(
        req_id: str, request: Request,
        pending: api_body.PendingBody = Depends(api_body.lazy(api_models.WITH_REASON, api_models.WaitlistIn))):
    if not _ID_RE.match(req_id):
        return _terminal_404()
    usuario = await run_in_threadpool(bkr._get_user, request)
    dni = await run_in_threadpool(bkr.session_dni, usuario)
    body = await pending.lazy()
    try:
        status, payload = await api_body.run(
            body, booking_patient._waitlist_toggle_core, dni, int(req_id), body)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.post("/api/analyze-order",
             openapi_extra=api_body.docs(post=(api_models.AnalyzeOrderIn, api_models.AnalyzeOrderOut)))
async def api_analyze_order(
        request: Request,
        pending: api_body.PendingBody = Depends(api_body.lazy(api_models.BOOKING, api_models.AnalyzeOrderIn))):
    client = request.client
    client_host = client.host if client else ""
    ip = await run_in_threadpool(
        abuse_guard.client_ip, request.headers, client_host)
    body = await pending.lazy()
    status, payload = await api_body.run(
        body, booking_referrals._analyze_referral_core, request.headers, client_host, ip, body)
    return _json_response(request, payload, status)


@router.get("/api/orden-preview/{token}")
async def api_orden_preview(token: str, request: Request):
    if not _TOKEN_RE.match(token):
        return _terminal_404()
    tup = await run_in_threadpool(booking_referrals._orden_preview_core, token)
    return _stream_response(request, tup)


# ---- Settings triad (owner only) -------------------------------------------

@router.api_route("/api/admin/theme", methods=["GET", "POST"], openapi_extra=api_body.docs(
    get=(None, api_models.ThemeStateOut), post=(api_models.ThemeIn, api_models.ThemeOut))
    | deps.STAFF_GATE_DOCS)
async def api_admin_theme(
        request: Request, _identity: dict = Depends(_owner),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.ThemeIn, methods={"POST"}))):
    """ONE route object for both methods — see `api_requests`'s own
    comment on why this campaign's `route_objects` gate requires it."""
    if request.method == "GET":
        status, payload = await run_in_threadpool(panel_settings._theme_get_core)
        return _json_response(request, payload, status)
    status, payload = await api_body.run(body, panel_settings._theme_set_core, body.value)
    return _json_response(request, payload, status)


@router.api_route("/api/admin/auth-mode", methods=["GET", "POST"], openapi_extra=api_body.docs(
    get=(None, api_models.AuthModeStateOut), post=(api_models.AuthModeIn, api_models.AuthModeOut))
    | deps.STAFF_GATE_DOCS)
async def api_admin_auth_mode(
        request: Request, _identity: dict = Depends(_owner),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.AuthModeIn, methods={"POST"}))):
    if request.method == "GET":
        status, payload = await run_in_threadpool(panel_settings._auth_mode_get_core)
        return _json_response(request, payload, status)
    status, payload = await api_body.run(body, panel_settings._auth_mode_set_core, body.value)
    return _json_response(request, payload, status)


@router.api_route("/api/admin/turnstile", methods=["GET", "POST"], openapi_extra=api_body.docs(
    get=(None, api_models.TurnstileStateOut), post=(api_models.TurnstileIn, api_models.TurnstileOut))
    | deps.STAFF_GATE_DOCS)
async def api_admin_turnstile(
        request: Request, _identity: dict = Depends(_owner),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.TurnstileIn, methods={"POST"}))):
    if request.method == "GET":
        status, payload = await run_in_threadpool(
            panel_settings._turnstile_get_core, request.headers)
        return _json_response(request, payload, status)
    status, payload = await api_body.run(body, panel_settings._turnstile_set_core, body.value)
    return _json_response(request, payload, status)


# ---- GET /api/admin/requests (queue) ---------------------------------------

@router.get("/api/admin/requests", openapi_extra=api_body.docs(
    get=(None, api_models.QueueOut)) | api_body.query_docs(get={
    "status": "Only the requests in this state (`pendiente`, `aprobada`, ...); a doctor always sees the pending ones",
    "limit": api_body.Query("At most this many (the default view is bounded on its own)", {"type": "integer"}),
    "offset": api_body.Query("Skip this many first (default 0)", {"type": "integer"}),
}) | deps.STAFF_GATE_DOCS)
async def api_admin_requests(request: Request):
    identity = await _require_admin(
        request, permitir_medico=True, permitir_admisionista=True)
    qs = _qs_like_legacy(request)
    try:
        status, payload = await run_in_threadpool(booking_queue._admin_list_core, identity, qs)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


# ---- GET /api/admin/requests/{id}/case-chat («Ver conversación», logged) --

@router.get("/api/admin/requests/{req_id}/case-chat", openapi_extra=api_body.docs(
    get=(None, api_models.CaseChatOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_request_case_chat(req_id: str, request: Request):
    if not _ID_RE.match(req_id):
        return _terminal_404()
    identity = await _require_admin(
        request, permitir_medico=True, permitir_admisionista=True)
    try:
        status, payload = await run_in_threadpool(
            booking_queue.staff_case_chat, identity, int(req_id))
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


# ---- GET /api/admin/hsi-sync (status only) ---------------------------------

@router.get("/api/admin/hsi-sync", openapi_extra=api_body.docs(
    get=(None, api_models.HsiSyncOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_hsi_sync(request: Request):
    await _require_admin(request)
    if not _hsi_selected():
        return _terminal_404()
    try:
        status, payload = await run_in_threadpool(booking_emr._hsi_sync_status_core)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


# ---- POST /api/admin/hsi-sync/resolver & /conflicts (native, W2d follow-up)
# Registered HERE, before order/refeps, to reproduce the pinned pre-extraction
# registration order (tests/tier4_composition_inventory.json).

@router.post("/api/admin/hsi-sync/resolver", openapi_extra=api_body.docs(
    post=(None, api_models.HsiResolverOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_hsi_sync_resolver(request: Request):
    await _require_admin(request)
    if not _hsi_selected():
        return _terminal_404()
    try:
        status, payload = await run_in_threadpool(booking_emr._hsi_resolver_run_core)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


async def _hsi_admin(request: Request):
    """The admin gate, then the 404 of an installation without HSI — both ahead of the body."""
    identity = await _require_admin(request)
    if not _hsi_selected():
        raise deps.EarlyResponse(_terminal_404())
    return identity


@router.post("/api/admin/hsi-sync/conflicts", openapi_extra=api_body.docs(
    post=(api_models.HsiConflictsIn, api_models.HsiConflictsOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_hsi_sync_conflicts(
        request: Request, identity: dict = Depends(_hsi_admin),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.HsiConflictsIn))):
    try:
        status, payload = await api_body.run(
            body, booking_emr._hsi_conflicts_apply_core, identity, body.value)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


# ---- GET /api/admin/order/{id} (referral file, admin/operator/own-doctor) --

@router.get("/api/admin/order/{req_id}", openapi_extra=deps.STAFF_GATE_DOCS)
async def api_admin_order(req_id: str, request: Request):
    if not _ID_RE.match(req_id):
        return _terminal_404()
    identity = await _require_admin(
        request, permitir_medico=True, permitir_admisionista=True)
    try:
        row, err = await run_in_threadpool(bkr.staff_referral_row, identity, int(req_id))
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    if err is not None:
        status, payload = err
        return _json_response(request, payload, status)
    try:
        tup = await run_in_threadpool(booking_referrals._referral_stream_response, row)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _stream_response(request, tup)


# ---- GET /api/admin/refeps --------------------------------------------------

@router.get("/api/admin/refeps", openapi_extra=api_body.docs(
    get=(None, api_models.RefepsOut)) | api_body.query_docs(get={
    "matricula": "The license number to look up in REFEPS: the first number in the text (dots allowed)",
    "medico": "The name on the referral, to say whether the registry's holder matches it",
    "force": api_body.Query("`1` or `true`: ask the registry again instead of the stored answer",
                            {"type": "string", "enum": ["1", "true"]}),
}) | deps.STAFF_GATE_DOCS)
async def api_admin_refeps(request: Request):
    await _require_admin(request, permitir_medico=True, permitir_admisionista=True)
    qs = _qs_like_legacy(request)
    try:
        status, payload = await run_in_threadpool(booking_queue._admin_refeps_core, qs)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


async def _staff_row(request, req_id):
    """The head every staff action on one request shares: `(identity, row, None)` for a valid path, a staff
    session and a request that session may touch; otherwise `(None, None, response)` — the terminal 404 of a
    path shape the endpoint declines, the 503 of a database that is down, or the refusal of
    `_staff_row_or_error` — and the route sends that response."""
    if not _ID_RE.match(req_id):
        return None, None, _terminal_404()
    identity = await _require_admin(
        request, permitir_medico=True, permitir_admisionista=True)
    try:
        row, err = await run_in_threadpool(bkr._staff_row_or_error, identity, int(req_id))
    except bkr.store.DbUnavailable:
        return None, None, _db_unavailable_response(request)
    if err is not None:
        status, payload = err
        return None, None, _json_response(request, payload, status)
    return identity, row, None


async def _staff_row_dep(req_id: str, request: Request):
    """`_staff_row` as a dependency of a route that also takes a body: `(identity, row)`, or its
    response ahead of the body."""
    identity, row, early = await _staff_row(request, req_id)
    if early is not None:
        raise deps.EarlyResponse(early)
    return identity, row


# ---- POST /api/admin/requests/<id>/approve, assign, retry-hsi, reject,
# campos, attendance (native, W2d follow-up — see module docstring) ---------

@router.post("/api/admin/requests/{req_id}/approve", openapi_extra=api_body.docs(
    post=(api_models.ApproveIn, api_models.StaffRequestOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_request_approve(
        req_id: str, request: Request, staff: tuple = Depends(_staff_row_dep),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.ApproveIn))):
    identity, row = staff
    try:
        status, payload = await api_body.run(
            body, booking_decisions._approve_core, identity, row, int(req_id), body.value, request)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.post("/api/admin/requests/{req_id}/assign", openapi_extra=api_body.docs(
    post=(api_models.AssignIn, api_models.StaffRequestOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_request_assign(
        req_id: str, request: Request, staff: tuple = Depends(_staff_row_dep),
        pending: api_body.PendingBody = Depends(api_body.lazy(api_models.ADMIN, api_models.AssignIn))):
    identity, row = staff
    body = await pending.lazy()
    try:
        status, payload = await api_body.run(
            body, booking_decisions._assign_core, identity, row, int(req_id), body)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.post("/api/admin/requests/{req_id}/retry-hsi", openapi_extra=api_body.docs(
    post=(None, api_models.StaffRequestOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_request_retry_hsi(req_id: str, request: Request):
    if not _ID_RE.match(req_id):
        return _terminal_404()
    await _require_admin(request)
    # The retry is the durable EMR push (`hsi_journal`, via `adapter_for`),
    # so any selected EMR connector has it; only «no connector» is a 404.
    if not hsi_journal._emr_selected():
        return _terminal_404()
    try:
        status, payload = await run_in_threadpool(booking_emr._retry_hsi_core, int(req_id))
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.post("/api/admin/requests/{req_id}/reject", openapi_extra=api_body.docs(
    post=(api_models.RejectIn, api_models.StaffRequestOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_request_reject(
        req_id: str, request: Request, staff: tuple = Depends(_staff_row_dep),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.RejectIn))):
    identity, row = staff
    try:
        status, payload = await api_body.run(
            body, booking_decisions._reject_core, identity, int(req_id), body.value, request)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.post("/api/admin/requests/{req_id}/cancel", openapi_extra=api_body.docs(
    post=(None, api_models.StaffRequestOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_request_cancel(req_id: str, request: Request):
    identity, row, early = await _staff_row(request, req_id)
    if early is not None:
        return early
    try:
        status, payload = await run_in_threadpool(
            booking_decisions._desk_cancel_core, identity, row, int(req_id), request)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.post("/api/admin/requests/{req_id}/restore", openapi_extra=api_body.docs(
    post=(None, api_models.StaffRequestOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_request_restore(req_id: str, request: Request):
    """The desk puts a cancelled appointment back as it was (patient file,
    2026-11-05) — the cancel's own scope and gate."""
    identity, row, early = await _staff_row(request, req_id)
    if early is not None:
        return early
    try:
        status, payload = await run_in_threadpool(
            booking_decisions._desk_restore_core, identity, row, int(req_id), request)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.post("/api/admin/requests/{req_id}/campos", openapi_extra=api_body.docs(
    post=(api_models.EditFieldIn, api_models.FieldsOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_request_campos(
        req_id: str, request: Request, staff: tuple = Depends(_staff_row_dep),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.EditFieldIn))):
    identity, row = staff
    try:
        status, payload = await api_body.run(
            body, booking_decisions._edit_field_core, row, int(req_id), body.value)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.post("/api/admin/requests/{req_id}/patient-data", openapi_extra=api_body.docs(
    post=(api_models.PatientDataIn, api_models.PatientDataOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_request_patient_data(
        req_id: str, request: Request, staff: tuple = Depends(_staff_row_dep),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.PatientDataIn))):
    identity, row = staff
    try:
        status, payload = await api_body.run(
            body, booking_decisions._patient_data_core, row, int(req_id), body.value)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)


@router.post("/api/admin/requests/{req_id}/attendance", openapi_extra=api_body.docs(
    post=(api_models.AttendanceIn, api_models.AttendanceOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_request_attendance(
        req_id: str, request: Request,
        pending: api_body.PendingBody = Depends(api_body.lazy(api_models.ADMIN, api_models.AttendanceIn))):
    if not _ID_RE.match(req_id):
        return _terminal_404()
    identity = await _require_admin(
        request, permitir_medico=True, permitir_admisionista=True)
    body = await pending.lazy()
    try:
        status, payload = await api_body.run(
            body, booking_decisions._mark_attendance_core, identity, int(req_id), body)
    except bkr.store.DbUnavailable:
        return _db_unavailable_response(request)
    return _json_response(request, payload, status)
