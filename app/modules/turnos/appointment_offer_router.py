"""appointment_offer_router.py — the FastAPI `router` (family W3a of
docs/roadmap/native-migration-campaign-20260826.md) for `appointment_offer.py`'s three paths — the released-slot offer
a waitlisted patient gets by email (the page, plus "Tomar este turno" /
"No lo quiero"). See `appointment_offer.py`'s own module docstring for the
full flow; the token IS the authentication (no session involved), same as
`calendar_feed.py`'s ICS feed.

All three endpoints are real `async def`s parsing the request natively and calling
`appointment_offer.py`'s extracted `build_offer_page`/`accept_offer`/
`reject_offer_action` domain functions directly (sync DB/notification work
off the event loop via `run_in_threadpool`); this router
is the only path to these domain functions. Same split `appointment_confirm.py`/
`appointment_confirm_router.py` established for this family.

`appointment_offer` IS a `turnos`-owned route module, so `Depends(deps.
require_module_enabled("turnos"))` applies.

  GET  /appointment/offer/{token}       server-rendered page
  POST /api/offer/{token}/accept        claims the slot
  POST /api/offer/{token}/reject        releases it before the window expires

Unlike `appointment_confirm_router.py`'s `{answer}` segment (which matches
ANY single path component and needs its own terminal-404, since `si`/`no`
isn't the whole set FastAPI's template would otherwise accept), every path
template here ends either in nothing after `{token}` (the GET) or a literal
FastAPI itself already validated (`accept`/`reject`), so there is no
"matched but declined" case for this family. Every 400/404/410 below is
a genuine domain-level response (`accept_offer`/`reject_offer_action`'s own
typed errors), preserved byte-for-byte, not a generic terminal shape.

Reading a session/cookie (`waitlist.reject_offer(token, handler=...)`,
`notifications.notify_approved(updated, handler)`) is done by handing the
real Starlette `Request` to functions that only ever call
`.headers.get(...)` on it — never a send_response/wfile-shaped object
executing the banned handler protocol. Same duck type
`accounts.current_user`'s own contract already documents."""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.modules.turnos import api_models, appointment_offer
from app.core import api_body
from app.core import db
from app.core import deps
from app.core import gzip_bridge
from app.core.http_json import gzip_json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


def _html_response(request, body, status=200):
    """Same shape as `patient_web/site_*.py._serve_html` (via `gzip_bridge.send`):
    text/html, no explicit Cache-Control (the legacy `_serve_html` never
    sets one either), gzip'd under the same rule as `_json_response`."""
    body, encoding = gzip_bridge.body_for(request, body)
    headers = {"Content-Type": "text/html; charset=utf-8"}
    if encoding:
        headers["Content-Encoding"] = encoding
        headers["Vary"] = "Accept-Encoding"
    return Response(content=body, status_code=status, headers=headers)




@router.get("/appointment/offer/{token}")
async def offer_page(request: Request, token: str):
    try:
        body = await run_in_threadpool(appointment_offer.build_offer_page, token)
    except db.DbUnavailable:
        body = await run_in_threadpool(appointment_offer._database_unavailable_page)
        return deps.answer_db_unavailable(lambda status: _html_response(request, body, status=status))
    return _html_response(request, body)


@router.post("/api/offer/{token}/accept", openapi_extra=api_body.docs(post=(None, api_models.OfferAcceptOut)))
async def offer_accept(request: Request, token: str):
    try:
        sid = await run_in_threadpool(appointment_offer.accept_offer, request, token)
    except appointment_offer.TokenInvalid:
        return _json_response(request, {"error": "token_invalido"}, status=400)
    except appointment_offer.OfferClaimFailed as exc:
        status = 404 if exc.reason == "no_existe" else 410
        return _json_response(request, {"error": exc.reason}, status=status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(request=request)
    return _json_response(request, {"ok": True, "solicitud_id": sid})


@router.post("/api/offer/{token}/reject", openapi_extra=api_body.docs(post=(None, api_body.OkOut)))
async def offer_reject(request: Request, token: str):
    try:
        ok = await run_in_threadpool(appointment_offer.reject_offer_action, request, token)
    except appointment_offer.TokenInvalid:
        return _json_response(request, {"error": "token_invalido"}, status=400)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(request=request)
    return _json_response(request, {"ok": ok})
