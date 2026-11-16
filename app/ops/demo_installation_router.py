"""demo_installation_router.py — native ASGI transport for the public
sandbox's four entry routes (family W2c of
docs/roadmap/native-migration-campaign-20260826.md).

Real `async def` endpoints. Each endpoint calls the domain functions in
`ops.demo_installation` (`demo_metadata`, `patient_access_state`,
`doctor_access_identity`) plus `cuentas.accounts_router.render_account`, the native
account-page renderer family F6 built for exactly this "same page, multiple
entry points" reuse. The real Starlette request is passed through unchanged;
`run_in_threadpool` wraps every domain call that touches the DB.

None of `demo_installation.py`'s `_blocks_legacy()` gate applies to the four
paths owned here: that gate only fires for the LEGACY page/API paths listed
in `_LEGACY_PAGES`/`_LEGACY_API`, none of which overlap this router's own
four routes — verified by reading `_blocks_legacy()`'s own tables.

  GET /api/demo                    public demo metadata
  GET /account/demo-access         passwordless demo-patient session
  GET /admin/demo-doctor-access    passwordless demo-doctor session
  GET /admin/demo-access           passwordless demo-admin session

Disabled demo (`TURNOS_DEMO_MODE` unset/falsy): these paths return the shared
terminal 404 via `core.terminal_response.TerminalNotFound`
(`{"error": "not found"}`, the bytes deployed main
serves for these routes with the demo off)."""

from fastapi import APIRouter, Request, Response

from app.core import api_body, api_models
from app.core.terminal_response import TerminalNotFound
from starlette.concurrency import run_in_threadpool

from app.auth import admin_auth
from app.cuentas import accounts
from app.cuentas import accounts_router as accounts_native
from app.ops import demo_installation
from app.core.http_json import gzip_json_response as _json_response

router = APIRouter()


def _require_enabled():
    if not demo_installation.enabled():
        # Shared terminal 404 — with the demo disabled, these routes "do
        # not exist"; deployed main serves exactly these bytes for them.
        raise TerminalNotFound()


def _redirect_with_cookie(location, cookie):
    return Response(
        status_code=302,
        headers={"Location": location, "Set-Cookie": cookie,
                 "Cache-Control": "no-store", "Content-Length": "0"})


@router.get("/api/demo", openapi_extra=api_body.docs(get=(None, api_models.DemoOut)))
async def get_demo_metadata(request: Request):
    _require_enabled()
    return _json_response(request, demo_installation.demo_metadata())


@router.get("/account/demo-access")
async def get_account_demo_access(request: Request):
    _require_enabled()
    try:
        user, token = await run_in_threadpool(demo_installation.patient_access_state)
    except demo_installation.DemoAccessError as exc:
        return _json_response(request, exc.body, status=exc.status)
    set_cookie = accounts._set_cookie(token)
    return await run_in_threadpool(
        accounts_native.render_account, request, bootstrap_user=user,
        set_cookie=set_cookie, canonical_path="/account")


@router.get("/admin/demo-doctor-access")
async def get_admin_demo_doctor_access(request: Request):
    _require_enabled()
    try:
        usuario, rol = await run_in_threadpool(demo_installation.doctor_access_identity)
    except demo_installation.DemoAccessError as exc:
        return _json_response(request, exc.body, status=exc.status)
    # The one-click entry IS the demo doctor's login: it leaves the trace every
    # login leaves (the last access), or the account reads «Invitado» to the
    # admin forever (audit 2026-11-05). `marcar_acceso` never blocks the entry.
    # Lands on «Hoy» — the doctor's own day of work, the screen the demo is for
    # (2026-11-10, plan slice 1): a real doctor's login lands there too
    # (`admin_navigation._PREFERRED_SCREEN`), and «Mis pacientes» was one click
    # further from the walkthrough.
    await run_in_threadpool(admin_auth.marcar_acceso, usuario)
    token = admin_auth._mint_token(usuario, rol)
    cookie = admin_auth._set_cookie_from(request.headers, token)
    from app.admin_panel import admin_navigation  # lazy: admin_panel sits above ops
    landing = admin_navigation.default_path_for_role(rol) or "/admin/account"
    return _redirect_with_cookie(landing, cookie)


@router.get("/admin/demo-access")
async def get_admin_demo_access(request: Request):
    _require_enabled()
    token = admin_auth._mint_token(demo_installation.demo_user(), "admin")
    cookie = admin_auth._set_cookie_from(request.headers, token)
    return _redirect_with_cookie("/admin/solicitudes", cookie)
