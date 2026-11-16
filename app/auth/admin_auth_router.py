"""Native FastAPI routes for the four administrator authentication APIs.

Each endpoint extracts request values with Starlette and calls the matching
value-only operation in :mod:`auth.admin_auth`. The router owns HTTP response
construction; the domain module owns authentication, throttling, session, and
role policy. There is no handler-shaped fallback or rollback topology.
"""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.auth import admin_auth, api_models
from app.auth.auth_response import response as _response
from app.auth import staff_portal
from app.core import api_body, deps
from app.core.proxy_trust import client_host as _client_host

router = APIRouter()


@router.get("/api/admin/config", openapi_extra=api_body.docs(get=(None, api_models.StaffConfigOut)))
async def api_admin_config(request: Request):
    result = await run_in_threadpool(admin_auth.config_result, request.headers)
    return _response(result)


def _portal_credential(request: Request) -> dict:
    """The gateway identity travels only on a portal-login installation."""
    if staff_portal.enabled():
        return {"credential": admin_auth.request_credential(request)}
    return {}


@router.get("/api/admin/whoami", openapi_extra=api_body.docs(
    get=(None, api_models.WhoamiOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_whoami(request: Request):
    result = await run_in_threadpool(
        admin_auth.whoami_result, request.headers, request.method, request.url.path,
        **_portal_credential(request),
    )
    return _response(result)


@router.post("/api/admin/login", openapi_extra=api_body.docs(
    post=(api_models.StaffLoginIn, api_models.StaffLoginOut)))
async def api_admin_login(request: Request,
        body: api_body.RawRead = Depends(api_body.raw(api_models.AUTH, api_models.StaffLoginIn))):
    result = await api_body.run(
        body, admin_auth.login_result, request.headers, _client_host(request), body.raw,
        **_portal_credential(request),
    )
    return _response(result)


@router.post("/api/admin/logout", openapi_extra=api_body.docs(post=(None, api_body.OkOut)))
async def api_admin_logout(request: Request):
    result = await run_in_threadpool(admin_auth.logout_result, request.headers)
    response = _response(result)
    # «Cerrar sesión» ends every session of this browser: a person who is also a patient on this
    # computer must not leave the patient account open behind the panel's logout (TASK-55).
    from app.cuentas import accounts
    if accounts._cookie_token(request) is not None:
        await run_in_threadpool(accounts._logout_core, request)
        response.headers.append("Set-Cookie", accounts._clear_cookie())
    return response
