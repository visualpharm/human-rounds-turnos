"""admin_users_router.py — native ASGI implementation of the panel's own
operator accounts (list/create/edit/disable/bulk actions/2FA).

Native-migration split (docs/roadmap/native-migration-campaign-20260826.md,
family F3): `admin_users.py` was split into pure domain functions
(`list_users`, `create_user`, `edit_user`, `disable_user`, `bulk_action` —
take parsed arguments, return data or raise `admin_panel.native_json.
ApiError`). This router calls the
same domain functions directly; FastAPI parses the request, `starlette.concurrency.run_in_threadpool` runs
the (synchronous, psycopg-calling) domain function off the event loop, and
`admin_panel.native_json` builds the byte-identical `Response`.

Platform-core, same footing as `accounts_router.py`/`admin_auth_router.py`
(`admin_users` is in `tests/test_module_manifest.py`'s own `PLATFORM_ROUTES`
allowlist) — no `app_modules.is_enabled()` gate, mounted unconditionally.

Auth: every path uses the established admin-only policy, so the one
router-level `Depends(deps.require_admin(solo_admin=True))` covers all
three route objects.

Same THREE path templates the pre-migration adapter mounted (one route
object per template, dispatching on method internally — matching the
architecture census's route-object accounting, not one object per method):

  GET,POST     /api/admin/users
  POST         /api/admin/users/actions/{action}   invite|link|create|disable|reset-password|reset-2fa
  PUT,DELETE   /api/admin/users/{usuario}

`{action}` is validated against `admin_users.USER_ACTIONS` — an unrecognized
value returns the shared
terminal 404 (`native_json.terminal_not_found()`), preserving the contract
for a path FastAPI's template matched but the domain's literal-action check
declined.
"""

from fastapi import APIRouter, Depends, Request

from app.admin_panel import admin_users, api_models
from app.admin_panel.native_json import ApiError, json_response, terminal_not_found
from app.core import api_body, deps
from starlette.concurrency import run_in_threadpool

router = APIRouter(dependencies=[Depends(deps.require_admin(solo_admin=True))])


async def _users_collection(
        request: Request,
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN_I18N, api_models.StaffUserIn, methods={"POST"}))):
    if request.method == "GET":
        result = await run_in_threadpool(admin_users.list_users)
        return json_response(request, result)
    try:
        result = await api_body.run(
            body, admin_users.create_user, body.value, expected=(ApiError,))
    except ApiError as err:
        return json_response(request, err.body, status=err.status)
    return json_response(request, result)


async def _known_action(action: str):
    """An action the domain does not know is the terminal 404, before the body is read."""
    if action not in admin_users.USER_ACTIONS:
        raise deps.EarlyResponse(terminal_not_found())
    return action


async def _users_actions(
        request: Request, action: str = Depends(_known_action),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN_I18N, api_models.StaffUsersActionIn))):
    try:
        result = await api_body.run(
            body, admin_users.bulk_action, action, body.value, expected=(ApiError,))
    except ApiError as err:
        return json_response(request, err.body, status=err.status)
    return json_response(request, result)


async def _users_item(
        usuario: str, request: Request,
        identity: dict = Depends(deps.require_admin(solo_admin=True)),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN_I18N, api_models.StaffUserIn, methods={"PUT"}))):
    if request.method == "DELETE":
        try:
            result = await run_in_threadpool(admin_users.disable_user, usuario)
        except ApiError as err:
            return json_response(request, err.body, status=err.status)
        return json_response(request, result)
    try:
        result = await api_body.run(
            body, admin_users.edit_user, usuario, body.value, identity, expected=(ApiError,))
    except ApiError as err:
        return json_response(request, err.body, status=err.status)
    return json_response(request, result)


router.add_api_route("/api/admin/users", _users_collection, methods=["GET", "POST"],
                     openapi_extra=api_body.docs(get=(None, api_models.StaffUsersOut),
                                                 post=(api_models.StaffUserIn, api_models.StaffUserOut)))
router.add_api_route("/api/admin/users/actions/{action}", _users_actions, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.StaffUsersActionIn,
                                                       api_models.StaffUsersActionOut)))
router.add_api_route("/api/admin/users/{usuario}", _users_item, methods=["PUT", "DELETE"],
                     openapi_extra=api_body.docs(put=(api_models.StaffUserIn, api_models.StaffUserOut),
                                                 delete=(None, api_body.OkOut)))
