"""admin_preferences_router.py — native FastAPI router for
`admin_preferences.py` — per-operator server-side panel preferences
(docs/roadmap/native-migration-campaign-20260826.md, family F5).

Platform-core (`admin_preferences` is in `PLATFORM_ROUTES`) — mounted
directly in `app.py`, unconditionally, same as `admin_users_router.py`.

Auth runs through
`core.deps.require_admin` (via `_native_support.gate`, same role flags as
the legacy `_identity()`: `permitir_medico`, `permitir_admisionista`,
`permitir_reportes`); DB work (`db_auth.admin_preferences`/`db.
admin_preference_save`) runs in a threadpool. The handler-shaped transport
has been retired; this router directly calls the shared `get_items` and
`save_item` domain functions.

GET and PUT share ONE route object (`methods=["GET", "PUT"]`, branching on
`request.method`), same topology as the adapted-legacy registration this
replaces.

  GET/PUT /api/admin/preferences"""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.admin_panel import admin_preferences, api_models
from app.admin_panel._native_support import json_response
from app.core import api_body, db, deps, native_gate

router = APIRouter()

_ROLE_FLAGS = dict(permitir_medico=True, permitir_admisionista=True, permitir_reportes=True)


async def _preferences(
        request: Request, identity: dict = Depends(native_gate.staff(**_ROLE_FLAGS)),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN, api_models.PreferenceIn, methods={"PUT"}))):
    if request.method == "GET":
        try:
            items = await run_in_threadpool(admin_preferences.get_items, identity["usuario"])
        except db.DbUnavailable:
            return deps.answer_db_unavailable()
        return json_response({"items": items})

    def _save():
        key = str((body.value or {}).get("key") or "").strip()
        value = (body.value or {}).get("value")
        return admin_preferences.save_item(identity["usuario"], key, value)

    try:
        saved = await api_body.run(
            body, _save, expected=(admin_preferences.PreferenceError,))
    except admin_preferences.PreferenceError as exc:
        return json_response({"error": str(exc)}, status=exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response({"ok": True, "value": saved})


router.add_api_route("/api/admin/preferences", _preferences, methods=["GET", "PUT"],
                     openapi_extra=api_body.docs(get=(None, api_models.PreferencesOut),
                                                 put=(api_models.PreferenceIn, api_models.PreferenceOut))
                     | deps.STAFF_GATE_DOCS)
