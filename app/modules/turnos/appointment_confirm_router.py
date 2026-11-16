"""appointment_confirm_router.py — the FastAPI `router`
for `appointment_confirm.py`'s two paths (family F7 of
docs/roadmap/native-migration-campaign-20260826.md) — "¿vas a poder venir?", the
reminder-email click side. See `appointment_confirm.py`'s own module
docstring: the token IS the authentication, and every state change is a
POST on purpose (a link scanner fetching the GET page must never cancel an
appointment).

Both endpoints are real `async def`s parsing the request natively and calling
`appointment_confirm.py`'s extracted `build_confirm_page`/`resolve_answer`
domain functions directly (sync DB/notification work off the event loop via
`run_in_threadpool`); this router is the only path to these domain
functions.

`appointment_confirm` IS a `turnos`-owned route module, so `Depends(deps.
require_module_enabled("turnos"))` applies.

  GET  /appointment/confirm/{token}       server-rendered page
  POST /api/confirm/{token}/{answer}      records the answer

The GET path is declared with a plain `{token}` segment (no trailing
literal to strip, unlike calendar_feed_router.py's `.ics` routes) so
FastAPI hands this router the token as-is.

The POST path's `{answer}` segment matches ANY single path component,
but only `si` and `no` are valid. A `Literal` type would turn a bad value
into FastAPI's own 422 validation error (a DIFFERENT contract than the
404), so this endpoint validates `answer` itself and declines with
`_json_not_found()`, the shared terminal-404 contract for "the module's own
validation declined a path FastAPI already matched" (`core.terminal_response`). Every OTHER
404/410/500 in `_resolve()` is a genuine domain-level response
(`resolve_answer()`'s own typed errors) and is preserved byte-for-byte,
not replaced by this generic shape.

Reading a session/cookie (only relevant when a "no" answer frees the slot
through `confirmations.record()` -> `chat_tools.cancel_request`) is done by
handing the real Starlette `Request` to functions that only ever call
`.headers.get(...)` on it — never a send_response/wfile-shaped object
executing the banned handler protocol. Same duck type
`accounts.current_user`'s own contract already documents."""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.modules.turnos import api_models, appointment_confirm
from app.core import api_body
from app.core import deps
from app.core import gzip_bridge
from app.core.http_json import gzip_json_response as _json_response
from app.core.terminal_response import json_not_found_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


# The shared terminal-404 shape the native-migration doc specifies for "the module's own internal regex
# declined a path FastAPI already matched".
_json_not_found = json_not_found_response


def _html_response(request, body):
    """Same shape as `patient_web/site_*.py._serve_html` (via `gzip_bridge.send`):
    text/html, no explicit Cache-Control (the legacy `_serve_html` never
    sets one either), gzip'd under the same rule as `_json_response`."""
    body, encoding = gzip_bridge.body_for(request, body)
    headers = {"Content-Type": "text/html; charset=utf-8"}
    if encoding:
        headers["Content-Encoding"] = encoding
        headers["Vary"] = "Accept-Encoding"
    return Response(content=body, status_code=200, headers=headers)


@router.get("/appointment/confirm/{token}")
async def confirm_page(request: Request, token: str):
    body = await run_in_threadpool(
        appointment_confirm.build_confirm_page, request, token, request.url.query)
    return _html_response(request, body)


@router.post("/api/confirm/{token}/{answer}",
             openapi_extra=api_body.docs(post=(None, api_models.ConfirmAnswerOut)))
async def confirm_answer(request: Request, token: str, answer: str):
    if answer not in ("si", "no"):
        return _json_not_found()
    try:
        mensaje = await run_in_threadpool(appointment_confirm.resolve_answer, request, token, answer)
    except appointment_confirm.TokenInvalid:
        return _json_response(request, {"error": "token_invalido"}, status=404)
    except appointment_confirm.NotConfirmable:
        return _json_response(request, {"error": "no_confirmable"}, status=410)
    except appointment_confirm.AppointmentPassed:
        return _json_response(request, {"error": "turno_pasado"}, status=410)
    except appointment_confirm.RecordFailed:
        return _json_response(request, {"error": "no_se_pudo"}, status=500)
    return _json_response(request, {"ok": True, "confirmacion": answer, "mensaje": mensaje})
