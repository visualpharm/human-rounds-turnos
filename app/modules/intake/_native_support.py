"""_native_support.py — shared plumbing for the native FastAPI routers of
family W2b (modules/intake — docs/roadmap/native-migration-campaign-20260826.md).

Deliberately NOT imported by any domain module (`interview_patient.py`,
`intake_admin.py`, `consult_admin.py`, `printable.py`): they import no
FastAPI (see `interview_patient_router.py`'s own module docstring). This file imports `fastapi`/`starlette` freely because
only the four `*_router.py` files (named by `router_attr`, imported by
`app_modules.mount_module_routers()`) ever import it.

`ApiError`, `gate()` and `read_json_body()` live in `core/native_gate.py` (the
same three serve family F5 of `admin_panel`); the routers import them from
here. `gate()` is called MANUALLY inside the endpoint body of a route that
checks something first: several routes in this family run a `?lang=` switch
check BEFORE the auth gate (`_maybe_lang_switch`, imported from
`core.site_identity_router` in `printable_router.py`/`intake_admin_router.py`),
and FastAPI resolves `Depends()` defaults before the endpoint body runs at
all — using `Depends` there would silently reorder the gate ahead of the lang
switch, a real behavior change. A route whose gate comes first and that also
takes a body declares both in its signature, the gate ahead
(`core.native_gate.staff`, the same `gate()` as a dependency)."""

from fastapi import Response

from app.core.http_json import json_response  # noqa: F401 — the family's routers import it from here
from app.core.native_gate import ApiError, gate, read_json_body  # noqa: F401 — same
from app.core.terminal_response import json_not_found_response as terminal_not_found_response  # noqa: F401 — same
from app.core.terminal_response import redirect_response  # noqa: F401 — same


def html_response(request, body_str, status=200):
    """Build the native HTML response with shared opportunistic gzip."""
    from app.core import gzip_bridge
    raw = body_str.encode("utf-8") if isinstance(body_str, str) else body_str
    out, encoding = gzip_bridge.body_for(request, raw)
    headers = {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Length": str(len(out)),
    }
    if encoding:
        headers["Content-Encoding"] = encoding
        headers["Vary"] = "Accept-Encoding"
    return Response(content=out, status_code=status, headers=headers)
