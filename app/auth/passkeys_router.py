"""passkeys_router.py — native-migration F1: `/api/admin/passkeys*` como
router FastAPI real (docs/roadmap/native-migration-campaign-
20260826.md).

Mismo criterio que admin_auth_router.py: cada endpoint parsea el request con
FastAPI/Starlette DIRECTO (cookie, headers, query, body, peer) y llama a la
función PURA correspondiente en `passkeys.py` -- `list_result`,
`register_options_result`, `register_result`, `delete_result`,
`reset_result`, `login_options_result`, `login_result` -- el MISMO
dispatcher de negocio que ya usaba `passkeys.handle(shim, method)`, así que
la ceremonia mantenida de `webauthn` (ver auth/webauthn_adapter.py) corre sin
una segunda copia. Los siete paths son
exactamente los que documenta passkeys.py's module docstring. El `Response`
se arma desde el `AuthResult` que cada función devuelve (auth/auth_response.py)."""

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from app.auth import admin_auth, api_models
from app.auth.auth_response import response as _response
from app.auth import passkeys
from app.core import api_body, deps
from app.core.proxy_trust import client_host as _client_host

router = APIRouter()


def _gate_args(request: Request):
    token = admin_auth.request_credential(request)
    return token, request.method, request.url.path


@router.get("/api/admin/passkeys", openapi_extra=api_body.docs(
    get=(None, api_models.PasskeyListOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_passkeys_list(request: Request, usuario: str = Query("", description=(
        "Whose passkeys: an admin may name any staff member, anyone else only themselves (the default)"))):
    cookie_token, method, path = _gate_args(request)
    result = await run_in_threadpool(passkeys.list_result, cookie_token, method, path, usuario)
    return _response(result)


@router.post("/api/admin/passkeys/register-options", openapi_extra=api_body.docs(
    post=(None, api_models.PasskeyRegisterOptionsOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_passkeys_register_options(request: Request):
    cookie_token, method, path = _gate_args(request)
    result = await run_in_threadpool(
        passkeys.register_options_result, cookie_token, method, path,
        request.headers.get("Host"),
    )
    return _response(result)


@router.post("/api/admin/passkeys/register", openapi_extra=api_body.docs(
    post=(api_models.PasskeyRegisterIn, api_models.PasskeyOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_passkeys_register(request: Request,
        body: api_body.RawRead = Depends(api_body.raw(api_models.AUTH, api_models.PasskeyRegisterIn))):
    cookie_token, method, path = _gate_args(request)
    result = await api_body.run(
        body, passkeys.register_result, cookie_token, method, path,
        request.headers.get("Host"), request.headers.get("X-Forwarded-Proto"),
        body.raw,
    )
    return _response(result)


@router.post("/api/admin/passkeys/delete", openapi_extra=api_body.docs(
    post=(api_models.PasskeyDeleteIn, api_models.StaffOkOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_passkeys_delete(request: Request,
        body: api_body.RawRead = Depends(api_body.raw(api_models.AUTH, api_models.PasskeyDeleteIn))):
    cookie_token, method, path = _gate_args(request)
    result = await api_body.run(
        body, passkeys.delete_result, cookie_token, method, path, body.raw)
    return _response(result)


@router.post("/api/admin/passkeys/reset", openapi_extra=api_body.docs(
    post=(api_models.PasskeyResetIn, api_models.PasskeyResetOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_passkeys_reset(request: Request,
        body: api_body.RawRead = Depends(api_body.raw(api_models.AUTH, api_models.PasskeyResetIn))):
    cookie_token, method, path = _gate_args(request)
    result = await api_body.run(
        body, passkeys.reset_result, cookie_token, method, path, body.raw)
    return _response(result)


@router.post("/api/admin/passkeys/login-options", openapi_extra=api_body.docs(
    post=(None, api_models.PasskeyLoginOptionsOut)))
async def api_admin_passkeys_login_options(request: Request):
    result = await run_in_threadpool(passkeys.login_options_result, request.headers.get("Host"))
    return _response(result)


@router.post("/api/admin/passkeys/login", openapi_extra=api_body.docs(
    post=(api_models.PasskeyLoginIn, api_models.StaffLoginOut)))
async def api_admin_passkeys_login(request: Request,
        body: api_body.RawRead = Depends(api_body.raw(api_models.AUTH, api_models.PasskeyLoginIn))):
    result = await api_body.run(
        body, passkeys.login_result, request.headers, _client_host(request), body.raw,
    )
    return _response(result)
