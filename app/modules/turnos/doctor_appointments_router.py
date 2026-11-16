"""doctor_appointments_router.py — Native-migration campaign, family F7
(docs/roadmap/native-migration-campaign-20260826.md): the FastAPI `router`
twin of `doctor_appointments.py`: the doctor's own agenda (list/feed/stats/
referral read), the front-desk day queue, and the doctor-compatible-services
lookup the wizard's "any doctor" path uses.

`doctor_appointments` IS `turnos`-owned, so `Depends(deps.
require_module_enabled("turnos"))` gates this router exactly as every
sibling router does. Two paths write: the doctor's new calendar address and
«Ocultar» on the desk extension's panel of changes.

`{doctor}` is a plain single-segment converter, not `{...:path}`. It keeps
doctor ids bounded to one segment; an encoded slash reaches the native
terminal and receives the shared `{"error": "not found"}` 404 before this
router sees it. `{req_id}` on `/my-appointments/<id>/order` is different:
the domain accepts numeric request ids only, so `_REQ_ID_RE` enforces that
contract before auth; so does `/day-queue/changes/<id>/hide`.

Twelve paths — the module's complete public surface:
  GET  /api/admin/doctors/{doctor}/compatible-services
  GET  /api/admin/doctors/{doctor}/appointments
  GET  /api/admin/doctors/stats
  GET  /api/admin/my-appointments
  GET  /api/admin/my-appointments/feed
  POST /api/admin/my-appointments/feed/rotate
  GET  /api/admin/my-appointments/{req_id}/order
  GET  /api/admin/day-queue
  GET  /api/admin/day-queue/calendar
  GET  /api/admin/day-queue/changes
  POST /api/admin/day-queue/changes/{req_id}/hide
  GET  /api/admin/day-queue/events

Business logic: every endpoint below calls the corresponding pure
`*_data(...)` domain function in `doctor_appointments.py`. `run_in_threadpool`
wraps every domain call: these do synchronous Postgres/catalog reads.

Auth — this file calls `core.deps.require_admin(...)`, a direct structural
port of `admin_auth.require_admin`. `_require_admin` below is a THIN call-through to
`core.deps.require_admin(...)`, awaited manually (not injected via
`Depends(...)` on the endpoint signature) so `my_appointment_order`'s
id-format check keeps running BEFORE authenticating. Rejections raise
`core.deps.AdminGateError` (an
`HTTPException` subclass); `app.py` registers a handler for it that
renders the exact flat legacy body (`render_admin_gate_error`) — callers
here do NOT catch it, they let it propagate, same mechanism the ~22
already-native admin screens use via `Depends(...)`.

Terminal 404: `/api/admin/my-appointments/<non-digit>/order` returns the
shared terminal response (`core.terminal_response.TerminalNotFound` ->
`{"error": "not found"}`) before auth or a domain read.
"""

import re

from fastapi import APIRouter, Depends, Query, Request, Response

from app.core.terminal_response import TerminalNotFound
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from app.auth import admin_auth
from app.core import db
from app.core import db_operators
from app.core import api_body
from app.core import deps
from app.modules.turnos import api_models
from app.modules.turnos import doctor_appointments as da
from app.modules.turnos import queue_events
from app.core.http_json import gzip_json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
_DAYS = api_body.query_docs(get={"days": api_body.Query(
    "How many days ahead, 1 to 90 (a number outside is held to it; not a number is 14)",
    {"type": "integer", "minimum": 1, "maximum": 90})})

_REQ_ID_RE = re.compile(r"^\d+$")


# ---- native admin auth gate (see module docstring) -------------------------

# Thin call-through to `core.deps.require_admin(...)` (native-migration F1's shim-free rewrite of the SAME
# gate `admin_auth.require_admin` itself uses) — no local reimplementation of the decision tree. Awaited
# manually instead of injected via `Depends(...)` on the endpoint signature so `my_appointment_order`'s
# id-format check keeps running BEFORE authenticating. Raises `core.deps.AdminGateError` on rejection;
# callers do NOT catch it — see module docstring for why that's correct.
_require_admin = deps.check_admin


# ---- shared response builders ----------------------------------------------

async def _domain_call(request, fn, *args):
    """Runs a pure domain function in the threadpool; returns (result, None)
    on success or (None, error_response), preserving 503 for DbUnavailable
    and the status/body carried by DomainError."""
    try:
        result = await run_in_threadpool(fn, *args)
    except da.DomainError as exc:
        return None, _json_response(request, exc.body, status=exc.status)
    except db.DbUnavailable:
        return None, deps.answer_db_unavailable(request=request)
    return result, None


def _days_param(days):
    """Same string-in behavior as `doctor_appointments._parse_days`, fed
    from the typed query parameter instead of a `parse_qs` dict."""
    return da._parse_days({"days": [days or ""]})


# `?days=` as text, parsed leniently by `_days_param`; `_DAYS` documents the number it takes.
_DAYS_TEXT = Query(None, include_in_schema=False)


# ---- GET /api/admin/doctors/{doctor}/compatible-services -------------------

@router.get("/api/admin/doctors/{doctor}/compatible-services", openapi_extra=api_body.docs(
    get=(None, api_models.CompatibleServicesOut)) | deps.STAFF_GATE_DOCS)
async def compatible_services(doctor: str, request: Request, especialidad: str | None = Query(
        None, description="The specialty to rank by, for a doctor not saved yet (a saved one's own otherwise)")):
    await _require_admin(request)
    especialidad_qs = (especialidad or "").strip()
    result, err = await _domain_call(
        request, da.compatible_services_data, doctor, especialidad_qs)
    if err is not None:
        return err
    return _json_response(request, result)


# ---- GET /api/admin/doctors/{doctor}/appointments (admin, any doctor) ------

@router.get("/api/admin/doctors/{doctor}/appointments", openapi_extra=api_body.docs(
    get=(None, api_models.DoctorAppointmentsOut)) | _DAYS | deps.STAFF_GATE_DOCS)
async def doctor_appointments_admin(doctor: str, request: Request, days: str | None = _DAYS_TEXT):
    identity = await _require_admin(request)
    dias = _days_param(days)
    result, err = await _domain_call(
        request, da.doctor_appointments_admin_data, identity, doctor, dias)
    if err is not None:
        return err
    return _json_response(request, result)


# ---- GET /api/admin/doctors/stats (admin/reportes) -------------------------

@router.get("/api/admin/doctors/stats", openapi_extra=api_body.docs(
    get=(None, api_models.DoctorsStatsOut)) | deps.STAFF_GATE_DOCS)
async def doctors_stats(request: Request):
    await _require_admin(request, permitir_reportes=True)
    result, err = await _domain_call(request, da.doctors_stats_data)
    if err is not None:
        return err
    return _json_response(request, result)


# ---- GET /api/admin/my-appointments (medico only) --------------------------

@router.get("/api/admin/my-appointments", openapi_extra=api_body.docs(
    get=(None, api_models.MyAppointmentsOut)) | _DAYS | deps.STAFF_GATE_DOCS)
async def my_appointments(request: Request, days: str | None = _DAYS_TEXT):
    identity = await _require_admin(request, permitir_medico=True)
    dias = _days_param(days)
    result, err = await _domain_call(request, da.my_appointments_data, identity, dias)
    if err is not None:
        return err
    return _json_response(request, result)


# ---- GET /api/admin/my-appointments/feed (medico only) ---------------------

@router.get("/api/admin/my-appointments/feed", openapi_extra=api_body.docs(
    get=(None, api_models.DoctorFeedOut)) | deps.STAFF_GATE_DOCS)
async def my_appointments_feed(request: Request):
    identity = await _require_admin(request, permitir_medico=True)
    result, err = await _domain_call(request, da.feed_url_data, identity)
    if err is not None:
        return err
    return _json_response(request, result)


# ---- POST /api/admin/my-appointments/feed/rotate (medico only) -------------
# A new calendar subscription address; the old one answers 404 from now on.

@router.post("/api/admin/my-appointments/feed/rotate", openapi_extra=api_body.docs(
    post=(None, api_models.DoctorFeedOut)) | deps.STAFF_GATE_DOCS)
async def my_appointments_feed_rotate(request: Request):
    from app.modules.turnos import calendar_feed
    identity = await _require_admin(request, permitir_medico=True)
    if identity.get("rol") != "medico":
        return _json_response(request, {"error": "solo_medico"}, status=403)
    try:
        result = await run_in_threadpool(calendar_feed.rotate_doctor_feed, identity)
    except calendar_feed.NotFoundError:
        return _json_response(request, {"error": "cuenta_sin_medico"}, status=400)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(request=request)
    return _json_response(request, result)


# ---- GET /api/admin/my-appointments/{req_id}/order (medico, owner only) ----

@router.get("/api/admin/my-appointments/{req_id}/order", openapi_extra=deps.STAFF_GATE_DOCS)
async def my_appointment_order(req_id: str, request: Request):
    if not _REQ_ID_RE.match(req_id):
        # Keep malformed ids at the native terminal boundary before auth.
        raise TerminalNotFound()
    identity = await _require_admin(request, permitir_medico=True)
    result, err = await _domain_call(request, da.my_referral_data, identity, int(req_id))
    if err is not None:
        return err
    mime, data = result
    headers = {
        "Content-Length": str(len(data)),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if mime == "application/octet-stream":
        # Bytes that match no allowlisted format are never rendered inline.
        headers["Content-Disposition"] = 'attachment; filename="orden.bin"'
    return Response(content=data, media_type=mime, headers=headers)


# ---- GET /api/admin/day-queue (admin, operator, medico, admisionista) ------

@router.get("/api/admin/day-queue", openapi_extra=api_body.docs(
    get=(None, api_models.DayQueueOut)) | deps.STAFF_GATE_DOCS)
async def day_queue(
        request: Request,
        dia: str | None = Query(None, description="The day, `YYYY-MM-DD`; anything else is today"),
        medico: str | None = Query(None, description=(
            "Only this doctor's patients (its id); a doctor's session is held to its own anyway"))):
    identity = await _require_admin(
        request, permitir_medico=True, permitir_admisionista=True)
    dia = da._parse_day({"dia": [dia or ""]})
    filtro = (medico or "").strip() or None
    result, err = await _domain_call(request, da.day_queue_data, identity, dia, filtro)
    if err is not None:
        return err
    result["hsi_instalar"] = await run_in_threadpool(_hsi_install, identity)
    return _json_response(request, result)


def _hsi_install(identity):
    """Where the desk installs its HSI extension (`core.hsi_extension.install`)
    — the extension draws «Cargar en HSI» on this screen. Not for a doctor's
    own day, and not with a server push to HSI (the reference-deployment counterpart, 2026-10-27)."""
    from app.core import hsi_extension
    from app.modules.turnos import booking_requests
    if identity.get("rol") == "medico" or booking_requests._emr_sync_enabled():
        return None
    return hsi_extension.install()


@router.get("/api/admin/day-queue/calendar", openapi_extra=api_body.docs(
    get=(None, api_models.QueueCalendarOut)) | deps.STAFF_GATE_DOCS)
async def queue_calendar(
        request: Request,
        desde: str | None = Query(None, description=(
            "First day of the range, `YYYY-MM-DD` (required: a missing or bad range is a 400)")),
        hasta: str | None = Query(None, description=(
            "Last day of the range, `YYYY-MM-DD`, at most 366 days after `desde`")),
        dia: str | None = Query(None, description=(
            "The selected day, for the nearest days with appointments before and after it (default today)")),
        medico: str | None = Query(None, description=(
            "Only this doctor (its id); a doctor's session is held to its own anyway")),
        centro: str | None = Query(None, description="Only this centre (its slug or name)")):
    identity = await _require_admin(request, permitir_medico=True, permitir_admisionista=True)
    result, err = await _domain_call(request, da.queue_calendar_data, identity,
        desde, hasta, dia, medico, centro)
    return err if err is not None else _json_response(request, result)


@router.get("/api/admin/day-queue/changes", openapi_extra=api_body.docs(
    get=(None, api_models.QueueChangesOut)) | deps.STAFF_GATE_DOCS)
async def day_queue_changes(request: Request, ids: str | None = Query(None, description=(
        "The request ids, comma-separated (whole numbers, at most 100; anything else is skipped)"))):
    """What became of the requests the desk's HSI extension loaded (cancelled, moved, attended)."""
    identity = await _require_admin(
        request, permitir_medico=True, permitir_admisionista=True)
    ids = da.parse_change_ids(ids)
    result, err = await _domain_call(request, da.day_changes_data, identity, ids)
    return err if err is not None else _json_response(request, result)


@router.post("/api/admin/day-queue/changes/{req_id}/hide", openapi_extra=api_body.docs(
    post=(None, api_models.OkOut)) | deps.STAFF_GATE_DOCS)
async def day_queue_change_hide(req_id: str, request: Request):
    """«Ocultar» on the extension's panel of changes: hidden for the whole desk, kept in the history."""
    if not _REQ_ID_RE.match(req_id):
        raise TerminalNotFound()
    identity = await _require_admin(
        request, permitir_medico=True, permitir_admisionista=True)
    result, err = await _domain_call(request, da.hide_change_data, identity, int(req_id))
    return err if err is not None else _json_response(request, result)


class _FeedResponse(StreamingResponse):
    """The queue feed's response, which owns the close of its LISTEN connection.

    The stream generator closes it in its own `finally`, but that never runs when the client is
    gone before the first byte (the generator never starts), and a background task never runs
    when the send fails or the request is cancelled. Whatever way the response ends, this one
    closes the connection (shielded from cancellation, never raising); closing twice is harmless."""

    def __init__(self, listener, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._listener = listener

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await queue_events.close_quietly(self._listener)


@router.get("/api/admin/day-queue/events", openapi_extra=deps.STAFF_GATE_DOCS)
async def day_queue_events(request: Request):
    """Cookie-authenticated, role- and center-scoped queue invalidations."""
    identity = await _require_admin(
        request, permitir_medico=True, permitir_admisionista=True)
    scoped_identity = dict(identity)
    scoped_identity["centros_permitidos"] = await run_in_threadpool(
        admin_auth.centros_permitidos, identity)
    if identity.get("rol") == "medico":
        scoped_identity["medico_id"] = await run_in_threadpool(
            db_operators.operator_doctor_id, identity.get("usuario"))
        medico = await run_in_threadpool(
            da.doctor_of, scoped_identity["medico_id"])
        scoped_identity["medico_nombre"] = (medico or {}).get("nombre") or ""
    # Opened, and the first read done, before the response starts: a database that cannot be
    # reached is the standard 503 `DbUnavailable` answer (EventSource retries), not an error inside
    # the stream. The response owns the close of the LISTEN connection (`_FeedResponse`).
    listener, cursor = await queue_events.open_feed(request.headers.get("last-event-id"))
    return _FeedResponse(
        listener,
        queue_events.stream(listener, cursor, scoped_identity, request.is_disconnected),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
