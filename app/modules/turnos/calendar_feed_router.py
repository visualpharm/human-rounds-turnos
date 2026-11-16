"""calendar_feed_router.py — the FastAPI `router`
twin of `calendar_feed.py` (family F7 of
docs/roadmap/native-migration-campaign-20260826.md) — .ics downloads and
subscription feeds for approved appointments. Each path is a real `async def`
endpoint parsing the request natively and calling `calendar_feed.py`'s
extracted `resolve_*` domain functions directly (sync DB work off the event
loop via `run_in_threadpool`). Those functions are the module's sole
behavior source.

`calendar_feed` IS `turnos`-owned, so `Depends(deps.require_module_enabled(
"turnos"))` gates the router.

  GET /api/appointment/{id}.ics         session-gated, one appointment
  GET /api/calendar/feed                session-gated, {url, hint} of own feed
                                        (url only when just created)
  POST /api/calendar/feed/regenerate    session-gated, {url, hint} of a new
                                        address; the previous one stops
  GET /feed/appointments/{token}.ics    token-is-the-auth, patient feed
  GET /feed/doctor/{token}.ics          token-is-the-auth, doctor feed
  GET /appointment/{token}.ics          token-is-the-auth, single event

All five token/id templates use a PLAIN `{sid}`/`{token}` segment (never
`{...:path}`). Starlette anchors the literal `.ics` outside the capture
group, so the endpoint receives the suffix-free id or token.

Reading a session (`_current_user`) or the client's `Accept-Encoding`
(`gzip_bridge.body_for`) is done by handing the real Starlette `Request`
to functions that only ever call `.headers.get(...)` on it — never a
send_response/wfile-shaped object executing the banned handler protocol.
`accounts.current_user`'s own contract already documents this duck type
("`.headers` real de un request HTTP ... o un dict común, ambos soportan
`.get`") — a real `Request` satisfies it for free."""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.modules.turnos import api_models, calendar_feed
from app.core import api_body
from app.core import db
from app.core import deps
from app.core.http_json import gzip_json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


def _ics_response(ics_text, filename):
    """Native ICS response. Calendar files are never gzip'd."""
    body = ics_text.encode("utf-8")
    return Response(
        content=body,
        status_code=200,
        headers={
            "Content-Type": "text/calendar; charset=utf-8",
            "Content-Length": str(len(body)),
            "Cache-Control": "no-cache, max-age=0",
            "Content-Disposition": 'inline; filename="%s"' % filename,
        },
    )


@router.get("/api/appointment/{sid}.ics", openapi_extra=deps.PATIENT_SESSION_DOCS)
async def appointment_ics(request: Request, sid: str):
    try:
        ics, filename = await run_in_threadpool(calendar_feed.resolve_appointment_ics, request, sid)
    except calendar_feed.AuthRequired:
        return _json_response(request, {"error": "auth_requerida"}, status=401)
    except calendar_feed.InvalidId:
        return _json_response(request, {"error": "id_invalido"}, status=400)
    except calendar_feed.NotFoundError:
        return _json_response(request, {"error": "no_encontrado"}, status=404)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(request=request)
    return _ics_response(ics, filename)


@router.get("/api/calendar/feed",
            openapi_extra=api_body.docs(get=(None, api_models.CalendarFeedOut)) | deps.PATIENT_SESSION_DOCS)
async def calendar_feed_url(request: Request):
    return await _feed_address(request, calendar_feed.resolve_feed_token)


@router.post("/api/calendar/feed/regenerate",
             openapi_extra=api_body.docs(post=(None, api_models.CalendarFeedOut)) | deps.PATIENT_SESSION_DOCS)
async def calendar_feed_regenerate(request: Request):
    return await _feed_address(request, calendar_feed.regenerate_feed_token)


async def _feed_address(request, resolve):
    try:
        body = await run_in_threadpool(resolve, request)
    except calendar_feed.AuthRequired:
        return _json_response(request, {"error": "auth_requerida"}, status=401)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(request=request)
    return _json_response(request, body)  # no-store, like every JSON answer


@router.get("/feed/appointments/{token}.ics")
async def feed_ics(request: Request, token: str):
    try:
        ics, filename = await run_in_threadpool(calendar_feed.resolve_feed_ics, token)
    except calendar_feed.NotFoundError:
        return _json_response(request, {"error": "no_encontrado"}, status=404)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(request=request)
    return _ics_response(ics, filename)


@router.get("/feed/doctor/{token}.ics")
async def doctor_feed_ics(request: Request, token: str):
    try:
        ics, filename = await run_in_threadpool(calendar_feed.resolve_doctor_feed_ics, token)
    except calendar_feed.NotFoundError:
        return _json_response(request, {"error": "no_encontrado"}, status=404)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(request=request)
    return _ics_response(ics, filename)


@router.get("/appointment/{token}.ics")
async def token_ics(request: Request, token: str):
    try:
        ics, filename = await run_in_threadpool(calendar_feed.resolve_token_ics, token)
    except calendar_feed.NotFoundError:
        return _json_response(request, {"error": "no_encontrado"}, status=404)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(request=request)
    return _ics_response(ics, filename)
