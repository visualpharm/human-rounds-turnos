"""native_gate.py - the staff gate and the JSON body the native admin routers share (2026-10-28).

`admin_panel/_native_support.py` (family F5) and `modules/intake/_native_support.py` (family W2b) each
carried a copy of `ApiError`, `gate()` and `read_json_body()`; `tests/test_native_support_contract.py`
pins what the pair answered before they became one.

Not a second auth implementation: `gate()` calls `core.deps.require_admin(**flags)` - the SAME
`admin_auth.require_admin(shim, ...)` port every other native router uses - and only reshapes ITS OUTPUT
on failure.

Why the reshape: `core.deps.require_admin` raises `HTTPException(status_code=..., detail=<legacy body
dict>)`. Left to FastAPI's default exception handler, that turns into `{"detail": <legacy body dict>}`
on the wire - fine for the `admin_panel/admin_*_screen.py` HTML pages (a 403 on an HTML screen never had
a JSON contract to keep), but a real regression for `/api/admin/...` JSON endpoints: the admin-panel
frontend JS parses `response.json().error` at the TOP level. `gate()` catches the dependency's own
`HTTPException` before it reaches FastAPI's handler and re-raises the SAME status/body as `ApiError`, so
the router can hand it back flat - same decision, same status, same bytes, just not FastAPI's generic
`detail` envelope.
"""

from fastapi import HTTPException, Request

from app.core import api_body, deps
from app.core.http_json import json_response


class ApiError(Exception):
    """A ready-to-send `(status, payload)` pair - payload is always the exact dict the endpoint answers."""

    def __init__(self, status, payload):
        super().__init__(str(payload))
        self.status = status
        self.payload = payload


async def gate(request, **role_flags):
    """Runs `core.deps.require_admin(**role_flags)` and returns the identity dict on success, or raises
    `ApiError` with the exact 401/403 body on failure. `role_flags` are the `solo_admin` /
    `permitir_medico` / `permitir_admisionista` / `permitir_reportes` / `permitir_marca` kwargs of the
    endpoint - copy them verbatim per call site, never invent a new combination."""
    dependency = deps.require_admin(**role_flags)
    try:
        return await dependency(request)
    except HTTPException as exc:
        detail = exc.detail
        payload = detail if isinstance(detail, dict) else {"error": str(detail)}
        raise ApiError(exc.status_code, payload) from None


def staff(**role_flags):
    """`gate()` as a dependency, for a route that checks nothing before its gate: the identity, or the
    route's flat 401/403 exactly as its handler answered `ApiError` (`json_response(payload, status)`),
    raised as `deps.EarlyResponse`."""
    async def staff_gate(request: Request):
        try:
            return await gate(request, **role_flags)
        except ApiError as exc:
            raise deps.EarlyResponse(json_response(exc.payload, exc.status)) from None
    return staff_gate


async def read_json_body(request):
    """An empty body is `{}`, not an error; a non-empty malformed body raises the `json.JSONDecodeError` /
    `UnicodeDecodeError` of `json.loads`."""
    return api_body.decode(await request.body())
