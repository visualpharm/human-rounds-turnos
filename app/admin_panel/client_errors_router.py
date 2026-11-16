"""client_errors_router.py — `POST /api/admin/client-errors`: the staff
screens' browser errors (`window.onerror` + `unhandledrejection`, reported by
`templates/admin/base.html`) into `core.error_tracking`.

Platform-core, mounted in `routers/platform.py` like the other `/api/admin/*`
routers. Only a signed-in operator may report (every panel role), so an
anonymous visitor cannot fill the error tracker. The body is capped, the
report is scrubbed by `error_tracking.browser_event` (no query strings, no
emails/DNI/phones), and each operator gets a small per-minute budget: a
render loop that throws on every frame costs one burst, not thousands of
events. The answer is always 204 to a signed-in operator — the browser has
nothing to do with the outcome.

  POST /api/admin/client-errors"""

import collections
import threading
import time

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from app.admin_panel import api_models
from app.admin_panel._native_support import ApiError, gate, json_response, read_json_body
from app.core import api_body, error_tracking
from app.core import deps

router = APIRouter()

_ROLE_FLAGS = dict(permitir_medico=True, permitir_admisionista=True, permitir_reportes=True)
_MAX_BODY = 16 * 1024
_PER_MINUTE = 10

_lock = threading.Lock()
_recent = collections.defaultdict(collections.deque)


def _within_budget(usuario, now=None):
    now = time.monotonic() if now is None else now
    with _lock:
        sent = _recent[usuario]
        while sent and now - sent[0] > 60:
            sent.popleft()
        if len(sent) >= _PER_MINUTE:
            return False
        sent.append(now)
        return True


async def _client_errors(request: Request):
    try:
        identity = await gate(request, **_ROLE_FLAGS)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    if int(request.headers.get("content-length") or 0) > _MAX_BODY:
        return json_response({"error": "demasiado grande"}, status=413)
    try:
        body = await read_json_body(request)
    except Exception:  # noqa: BLE001 — a malformed report is dropped, never echoed
        return Response(status_code=204)
    api_body.watch(request, body, api_models.ClientErrorIn)
    if isinstance(body, dict) and _within_budget(identity.get("usuario") or ""):
        await run_in_threadpool(error_tracking.capture_browser_error, body)
    return Response(status_code=204)


router.add_api_route("/api/admin/client-errors", _client_errors, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.ClientErrorIn, None, 204)) | deps.STAFF_GATE_DOCS)
