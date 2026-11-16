"""Server-routed twins of the three legacy catalog editors.

``/admin/services``, ``/admin/centers`` and ``/admin/doctors`` are the direct
URLs of the ``#services`` / ``#centers`` / ``#doctors`` hash sections (manifest
ids ``servicios`` / ``centros`` / ``medicos``).  One module and one template
serve the three screens; each screen still owns a DISTINCT one-route
``APIRouter`` because ``app_modules.app_router_entries()`` mounts whatever
object a manifest ``screen.router_attr`` names, without deduplicating — a
single router listed three times would be included three times.

The screen owns only the ASGI shell, the turnos module gate, the admin-only
role gate and the direct URL. ``admin-content.js`` remains the shared renderer and
owns every list, form, save, disable and restore through the existing
``/api/admin/content/*`` API.  The shell never reads catalog data: a
``?item=<id>`` query is client state, resolved by the controller after load.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import screen_page

_TEMPLATE = "admin/content.html"

# manifest id -> (direct path, controller URL segment, title key, Spanish title)
EDITORS = {
    "servicios": ("/admin/services", "services", "admin.content.catalog.titles.services", "Servicios"),
    "centros": ("/admin/centers", "centers", "admin.content.catalog.titles.centers", "Centros"),
    "medicos": ("/admin/doctors", "doctors", "admin.content.catalog.titles.doctors", "Médicos"),
}


def _render(section_id, identity):
    path, segment, title_key, title_es = EDITORS[section_id]
    return screen_page.admin_page(
        identity, _TEMPLATE, section_id,
        rol=identity.get("rol"),
        editor={"id": section_id, "path": path, "segment": segment,
                "title_key": title_key, "title_es": title_es,
                "doctors": section_id == "medicos",
                # Services use the compact capacity editor from the same module;
                # the doctor-only calendar and user panels remain doctor-gated.
                "hours_editor": section_id in {"servicios", "medicos"}},
    )


def _router(section_id):
    router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
    path = EDITORS[section_id][0]

    @router.get(path)
    async def content_screen(identity: dict = Depends(deps.require_admin_screen(solo_admin=section_id != "medicos", permitir_medico=section_id == "medicos"))):
        return _render(section_id, identity)

    content_screen.__name__ = "%s_screen" % EDITORS[section_id][1]
    return router


services_screen_router = _router("servicios")
centers_screen_router = _router("centros")
doctors_screen_router = _router("medicos")
