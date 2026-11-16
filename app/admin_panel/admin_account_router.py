"""admin_account_router.py — native FastAPI router for `admin_account.py` —
the panel user's own "Mi cuenta" (profile/password/2FA), as opposed to
`admin_users.py` (managing OTHER operators). Docs/roadmap/
native-migration-campaign-20260826.md, family F5.

Platform-core (`admin_account` is in `PLATFORM_ROUTES`) — mounted directly
in `app.py`, unconditionally, same as `admin_users_router.py`.

Every endpoint gates with
the shared account role flags (`permitir_medico`, `permitir_admisionista`,
`permitir_reportes`, `permitir_marca`) and calls the domain functions (`account_status`
/ `update_profile` / `change_password` / `totp_start` / `totp_confirm` /
`totp_remove`). This router is the sole HTTP transport for the account surface.

  GET  /api/admin/account
  POST /api/admin/account/profile
  POST /api/admin/account/password
  POST /api/admin/account/2fa/start
  POST /api/admin/account/2fa/confirm
  POST /api/admin/account/2fa/remove"""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.admin_panel import admin_account, api_models
from app.admin_panel._native_support import ApiError, gate, json_response
from app.core import api_body, deps, native_gate

router = APIRouter()

_ROLE_FLAGS = dict(permitir_medico=True, permitir_admisionista=True,
                    permitir_reportes=True, permitir_marca=True)


async def _gated_identity(request):
    return await gate(request, **_ROLE_FLAGS)


# The same gate as a dependency, for the routes that also take a body: it answers ahead of it.
_GATE = native_gate.staff(**_ROLE_FLAGS)


@router.get("/api/admin/account", openapi_extra=api_body.docs(
    get=(None, api_models.StaffAccountOut)) | deps.STAFF_GATE_DOCS)
async def get_account(request: Request):
    try:
        identity = await _gated_identity(request)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    result = await run_in_threadpool(admin_account.account_status, identity)
    return json_response(result)


@router.post("/api/admin/account/profile", openapi_extra=api_body.docs(
    post=(api_models.AccountProfileIn, api_models.AccountProfileOut)) | deps.STAFF_GATE_DOCS)
async def post_account_profile(
        request: Request, identity: dict = Depends(_GATE),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.AccountProfileIn))):
    try:
        result = await api_body.run(
            body, admin_account.update_profile, identity, body.value,
            expected=(admin_account.AccountError,))
    except admin_account.AccountError as exc:
        return json_response({"error": str(exc)}, status=exc.status)
    return json_response(result)


@router.post("/api/admin/account/password", openapi_extra=api_body.docs(
    post=(api_models.AccountPasswordIn, api_models.AccountPasswordOut)) | deps.STAFF_GATE_DOCS)
async def post_account_password(
        request: Request, identity: dict = Depends(_GATE),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.AccountPasswordIn))):
    try:
        result = await api_body.run(
            body, admin_account.change_password, identity, body.value,
            expected=(admin_account.AccountError,))
    except admin_account.AccountError as exc:
        return json_response({"error": str(exc)}, status=exc.status)
    response = json_response(result)
    if result.get("ok"):
        from app.auth import admin_auth
        # A rename or a new password re-issues the cookie of THIS browser (the new password closes
        # every other session of the account). Same session id as the cookie it replaces: signing
        # out later closes every copy of it.
        sid = admin_auth.session_id_of(admin_auth.request_credential(request))
        response.headers["Set-Cookie"] = admin_auth._set_cookie_from(
            request.headers,
            admin_auth._mint_token(result.get("usuario") or identity["usuario"], identity["rol"], sid=sid))
    return response


def _reissue_cookie(request, identity, response, result):
    """A changed authenticator closes every other session of the account (the cookie carries a
    stamp of the 2FA secret); the browser that made the change gets a fresh cookie with the same
    session id, like a password change does."""
    from app.auth import admin_auth
    if result.get("ok") and not admin_auth.staff_portal.enabled():
        sid = admin_auth.session_id_of(admin_auth.request_credential(request))
        response.headers["Set-Cookie"] = admin_auth._set_cookie_from(
            request.headers, admin_auth._mint_token(identity["usuario"], identity["rol"], sid=sid))
    return response


@router.post("/api/admin/account/2fa/start", openapi_extra=api_body.docs(
    post=(api_models.TotpStartIn, api_models.TotpStartOut)) | deps.STAFF_GATE_DOCS)
async def post_account_2fa_start(request: Request):
    try:
        identity = await _gated_identity(request)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — no body, or not JSON: nothing to read
        payload = None
    try:
        result = await run_in_threadpool(admin_account.totp_start, identity, payload)
    except admin_account.AccountError as exc:
        return json_response({"error": str(exc)}, status=exc.status)
    return json_response(result)


@router.post("/api/admin/account/2fa/confirm", openapi_extra=api_body.docs(
    post=(api_models.TotpConfirmIn, api_models.TotpOut)) | deps.STAFF_GATE_DOCS)
async def post_account_2fa_confirm(
        request: Request, identity: dict = Depends(_GATE),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.TotpConfirmIn))):
    try:
        result = await api_body.run(
            body, admin_account.totp_confirm, identity, body.value,
            expected=(admin_account.AccountError,))
    except admin_account.AccountError as exc:
        return json_response({"error": str(exc)}, status=exc.status)
    return _reissue_cookie(request, identity, json_response(result), result)


@router.post("/api/admin/account/2fa/remove", openapi_extra=api_body.docs(
    post=(api_models.TotpRemoveIn, api_models.TotpOut)) | deps.STAFF_GATE_DOCS)
async def post_account_2fa_remove(
        request: Request, identity: dict = Depends(_GATE),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.TotpRemoveIn))):
    try:
        result = await api_body.run(
            body, admin_account.totp_remove, identity, body.value,
            expected=(admin_account.AccountError,))
    except admin_account.AccountError as exc:
        return json_response({"error": str(exc)}, status=exc.status)
    return _reissue_cookie(request, identity, json_response(result), result)
