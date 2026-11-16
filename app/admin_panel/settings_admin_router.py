"""settings_admin_router.py — native FastAPI router for `settings_admin.py`
— the Settings admin section (Location/Languages/API keys, secret masking,
`TURNOS_CONFIG_LOCK`). Docs/roadmap/native-migration-campaign-20260826.md,
family F5.

Platform-core (`settings_admin` is in `PLATFORM_ROUTES`) — mounted directly
in `app.py`, unconditionally, same as `admin_users_router.py`.

Every endpoint gates with
`core.deps.require_admin(solo_admin=True)` — the SAME flag `settings_admin.
_gate()` already used — via `_native_support.gate`, and calls the SAME pure
`settings_admin._payload()` / `.resolve_save()` / `.resolve_probe()` domain
functions directly. The handler-shaped transport has been retired.

GET and POST on `/api/admin/settings` share ONE route object (`methods=
["GET", "POST"]`, branching on `request.method`), same topology as the
adapted-legacy registration this replaces — the census's route-object count
is a Starlette-route count, not a method count.

  GET/POST /api/admin/settings
  POST     /api/admin/settings/probar"""

from fastapi import APIRouter, Depends, Request

from app.admin_panel import api_models, settings_admin
from app.core import ai_register, api_body
from app.core import deps, native_gate
from app.admin_panel._native_support import json_response

router = APIRouter()


def _lenient(raw):
    """A body that does not decode reads as `{}` here (the legacy leniency)."""
    try:
        return api_body.decode(raw) or {}
    except Exception:  # noqa: BLE001 — legacy silently falls back to {}
        return {}


_ADMIN = native_gate.staff(solo_admin=True)


async def _settings(
        request: Request, identity: dict = Depends(_ADMIN),
        body: api_body.Body = Depends(api_body.lenient(
            api_models.ADMIN, api_models.SettingsIn, _lenient, methods={"POST"}))):
    if request.method == "GET":
        return json_response(settings_admin._payload())

    def _save():
        grupo = str(body.value.get("grupo") or "")
        valores = body.value.get("valores") or {}
        borrar = body.value.get("borrar") or []
        with ai_register.actor(identity):
            return grupo, settings_admin.resolve_save(grupo, valores, borrar)

    try:
        grupo, resultado = api_body.call(body, _save, expected=(settings_admin.SettingsError,))
    except settings_admin.SettingsError as exc:
        return json_response({"error": str(exc)}, status=400)
    return json_response({"ok": True, grupo: resultado})


async def _settings_probar(
        _identity: dict = Depends(_ADMIN),
        body: api_body.Body = Depends(api_body.lenient(api_models.ADMIN, api_models.SettingsProbeIn, _lenient))):

    def _probe():
        return settings_admin.resolve_probe(str(body.value.get("integracion") or ""))

    return json_response(api_body.call(body, _probe))


router.add_api_route("/api/admin/settings", _settings, methods=["GET", "POST"],
                     openapi_extra=api_body.docs(get=(None, api_models.SettingsStateOut),
                                                 post=(api_models.SettingsIn, api_models.SettingsOut))
                     | deps.STAFF_GATE_DOCS)
router.add_api_route("/api/admin/settings/probar", _settings_probar, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.SettingsProbeIn, api_models.ProbeOut)) | deps.STAFF_GATE_DOCS)
