"""password_reset_router.py — native FastAPI router for the one-use
password-reset page for internal panel users (docs/roadmap/native-
migration-campaign-20260826.md, family F9).

Real ``async def`` endpoints. Both endpoints call the pure
domain functions on ``auth.password_reset``. Recovery requests reuse this
platform-core route with ``action=request``; no additional route is mounted.

Platform-core, mounted directly in ``app.py`` same as before.

  GET  /admin/reset-password        the page (token in ?token=, no auth)
  POST /api/admin/reset-password    submits a new password, or requests an
                                   email with action=request + usuario"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response
from starlette.background import BackgroundTask

from app.auth import api_models, password_reset
from app.auth import staff_portal
from app.auth import turnstile
from app.core import api_body, deps, i18n
from app.core.http_json import json_response as _json_response

router = APIRouter()


def _not_found():
    """A portal-login installation has no password to reset: the page and its
    API answer exactly like a path that never existed."""
    return _json_response({"error": "no_encontrado"}, status=404)


async def _password_login():
    """Ahead of the body: where staff sign in through the portal, the API is not there."""
    if staff_portal.enabled():
        raise deps.EarlyResponse(_not_found())


@router.get("/admin/reset-password")
async def get_reset_password(token: str = Query("", description="The one-use token of the reset link")):
    if staff_portal.enabled():
        # The app's own not-found answer, as for a path that never existed.
        raise HTTPException(status_code=404)
    # Upfront validity (read-only): a dead link shows the expired state
    # instead of a form whose POST was going to fail anyway. None (store
    # unreadable) falls back to the form; the POST still enforces the token.
    # The username comes from the token row, never from the query string.
    usuario = await run_in_threadpool(password_reset.token_usuario, token)
    body = password_reset.page_html(token, valido=(usuario is not False),
                                    usuario=usuario or None)
    return Response(
        content=body, status_code=200,
        headers={"Content-Type": "text/html; charset=utf-8",
                 "Cache-Control": "no-store"})


@router.post("/api/admin/reset-password", openapi_extra=api_body.docs(
    post=(api_models.ResetPasswordIn, api_models.ResetPasswordOut)))
async def post_reset_password(
        request: Request, _open: None = Depends(_password_login),
        # Malformed JSON never reaches the domain (api_models.RESET answers it).
        reset: api_body.Body = Depends(api_body.body(api_models.RESET, api_models.ResetPasswordIn))):
    body = reset.value

    if not isinstance(body, dict):
        return _json_response({"error": i18n.t_or("admin.reset.invalid_json",
            "JSON inválido.", i18n.active())}, status=400)
    if body.get("action") == "request":
        ip = turnstile.client_ip_from(request.headers, request.client.host if request.client else "")
        return _json_response(password_reset.request_response(), background=BackgroundTask(
            password_reset.request_reset, str(body.get("usuario") or ""), ip))

    password = str((body or {}).get("password") or "")
    token = str((body or {}).get("token") or "")
    try:
        result = await run_in_threadpool(password_reset.reset_password, token, password,
                                        request.headers, request.client.host if request.client else "")
    except password_reset.ResetError as exc:
        return _json_response(exc.payload, status=exc.status)
    response = _json_response(result.body, status=result.status)
    if result.set_cookie:
        response.headers["Set-Cookie"] = result.set_cookie
    return response
