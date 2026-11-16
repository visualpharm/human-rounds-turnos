"""admin_panel/roles_router.py — Roles CRUD API routes (platform-core).

Native-migration split: `core.permissions` owns pure domain functions
(list_roles, get_role, create_role, update_role, delete_role). This router
calls them directly via FastAPI, gated by `require_admin(permission='usuarios')`.

Auth: every path uses `solo_admin=True` (admin-only) per the precursor gate.
RoleError -> JSON {"error": message, "code": code, "field": field} with its
status code, matching admin_users_router's error/JSON conventions.

Three path templates:
  GET,POST     /api/admin/roles
  PUT,DELETE   /api/admin/roles/{slug}
  GET          /api/admin/permissions
"""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.core import permissions
from app.admin_panel import api_models
from app.admin_panel.native_json import json_response
from app.core import api_body, deps

router = APIRouter(dependencies=[Depends(deps.require_admin(solo_admin=True))])


async def _roles_collection(
        request: Request,
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN_I18N, api_models.RoleIn, methods={"POST"}))):
    if request.method == "GET":
        result = await run_in_threadpool(permissions.list_roles)
        return json_response(request, result)

    def _create():
        return permissions.create_role(
            body.value.get("name"),
            body.value.get("description"),
            body.value.get("permissions", [])
        )

    try:
        result = await api_body.run(body, _create, expected=(permissions.RoleError,))
    except permissions.RoleError as err:
        return json_response(
            request,
            {"error": str(err), "code": err.code, "field": err.field},
            status=err.status
        )
    return json_response(request, result)


async def _roles_item(
        slug: str, request: Request,
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN_I18N, api_models.RoleIn, methods={"PUT"}))):
    if request.method == "DELETE":
        try:
            await run_in_threadpool(permissions.delete_role, slug)
        except permissions.RoleError as err:
            return json_response(
                request,
                {"error": str(err), "code": err.code, "field": err.field},
                status=err.status
            )
        return json_response(request, {"status": "deleted"})
    try:
        result = await api_body.run(
            body, permissions.update_role, slug, body.value,
            expected=(permissions.RoleError,))
    except permissions.RoleError as err:
        return json_response(
            request,
            {"error": str(err), "code": err.code, "field": err.field},
            status=err.status
        )
    return json_response(request, result)


async def _permissions_catalog(request: Request):
    """Get the permissions catalog (id + label pairs)."""
    result = permissions.permissions_payload()
    return json_response(request, result)


router.add_api_route("/api/admin/roles", _roles_collection, methods=["GET", "POST"],
                     openapi_extra=api_body.docs(get=(None, api_models.RoleListOut),
                                                 post=(api_models.RoleIn, api_models.RoleOut)))
router.add_api_route("/api/admin/roles/{slug}", _roles_item, methods=["PUT", "DELETE"],
                     openapi_extra=api_body.docs(put=(api_models.RoleIn, api_models.RoleOut),
                                                 delete=(None, api_models.RoleDeletedOut)))
router.add_api_route("/api/admin/permissions", _permissions_catalog, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.PermissionListOut)))
