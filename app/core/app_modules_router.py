"""Native transport for the two platform module-registry GET endpoints.

Real ``async def`` endpoints. ``/api/admin/modules`` uses ``core.deps.require_admin(...)``
with ``permitir_medico=True``; ``/api/nav`` stays public."""

from fastapi import APIRouter, HTTPException, Request

from app.core import api_body, api_models, app_modules
from app.core import deps
from app.core import i18n
from app.core.http_json import gzip_json_response as _json_response


router = APIRouter()

_require_admin = deps.require_admin(permitir_medico=True)


async def _admin_identity_or_error(request):
    """Runs `core.deps.require_admin(permitir_medico=True)` directly
    (rather than via FastAPI's `Depends()` injection) so a 401/403 keeps
    the EXACT flat body this route has always sent — `Depends()`'s
    own exception-to-response translation would wrap `exc.detail` a second
    time (`{"detail": {"error": ...}}`), which is not the body this route
    ever produced. Returns `(identity, None)` on
    success, `(None, Response)` on failure."""
    try:
        identity = await _require_admin(request)
    except HTTPException as exc:
        return None, _json_response(request, exc.detail, status=exc.status_code)
    return identity, None


def _modules_payload(rol):
    """Build the canonical module/navigation payload for one role."""
    payload = {
        "sections": app_modules.sections_payload_for_role(rol),
        "nav_entries": app_modules.enabled_nav_entries(),
    }
    if rol == "admin":
        payload["modules"] = app_modules.modules_status()
    return payload


@router.get("/api/admin/modules", openapi_extra=api_body.docs(
    get=(None, api_models.AdminModulesOut)) | deps.STAFF_GATE_DOCS)
async def api_admin_modules(request: Request):
    identity, error_response = await _admin_identity_or_error(request)
    if error_response is not None:
        return error_response
    return _json_response(request, _modules_payload(identity.get("rol")))


@router.get("/api/nav", openapi_extra=api_body.docs(get=(None, api_models.NavOut)))
async def api_nav(request: Request):
    return _json_response(
        request, {"nav_entries": app_modules.nav_entries_for_lang(i18n.active())})
