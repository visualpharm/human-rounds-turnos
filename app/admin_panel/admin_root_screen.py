"""Role-aware entrypoint for the server-routed admin application.

``/admin`` is navigation only: it never renders the retired hash-routed
``admin.html`` shell.  Authentication and role policy stay in
``auth.admin_auth``; this module only maps an already-authorized staff role to
the first direct screen that role may enter.
"""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool
from urllib.parse import urlencode

from app.admin_panel import admin_navigation
from app.auth import admin_auth
from app.core import deps


router = APIRouter()

# Kept as a public compatibility constant for callers that need a simple
# role-default map. Not populated at module load (to avoid database access
# at import time). Runtime resolution below deliberately uses the current
# server-filtered navigation instead, so a disabled module cannot be restored.
# Legacy code that reads this should call admin_navigation.default_path_for_role()
# directly for each role instead.
ROLE_LANDING = {}


def _redirect(target: str) -> RedirectResponse:
    return RedirectResponse(
        target,
        status_code=307,
        headers={"Cache-Control": "no-store"},
    )


def _requested_next(request: Request) -> str:
    """The root accepts exactly one outer ``next`` parameter, if any.

    ``QueryParams.get`` silently chooses one duplicate.  That is unsuitable
    for a post-auth redirect boundary: duplicates and unrelated outer state
    are a supplied-invalid continuation, not an absent continuation.
    """
    pairs = list(request.query_params.multi_items())
    if not pairs:
        return ""
    if len(pairs) != 1 or pairs[0][0] != "next":
        return "__invalid_admin_next__"
    return pairs[0][1]


@router.get("/admin")
async def admin_root(request: Request):
    explicit_next = _requested_next(request)
    token = admin_auth.request_credential(request)
    identity = await run_in_threadpool(admin_auth._verify_token, token)
    if not identity:
        next_target = admin_navigation.canonical_admin_target(
            explicit_next,
        )
        suffix = ("?" + urlencode({"next": next_target})
                  if next_target and next_target != "/admin" else "")
        return _redirect("/admin/login" + suffix)

    target = await run_in_threadpool(
        admin_navigation.resolve_target, identity, explicit_next,
    )
    if not target:
        raise deps.AdminGateError(
            status_code=403,
            detail={"error": "rol_no_autorizado"},
        )

    gate = await run_in_threadpool(
        admin_auth.evaluate_admin_gate,
        token,
        request.method,
        target,
        False,
        True,
        True,
        True,
        False,
    )
    if not gate.ok:
        if gate.body and gate.body.get("error") == "2fa_requerido":
            return _redirect("/admin/account?setup=2fa")
        raise deps.AdminGateError(status_code=gate.status, detail=gate.body)

    return _redirect(target)
