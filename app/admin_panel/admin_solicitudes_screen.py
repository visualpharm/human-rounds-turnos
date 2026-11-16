"""admin_solicitudes_screen.py — Tier 5 Requests direct screen (mechanical
checkpoint): the server-routed twin of the legacy `#solicitudes` hash
section, built on the same `admin_sections[].screen` mechanism `cola`
proved on slice 5.

This file lives in `admin_panel/` alongside the existing cola/pacientes/
mis-turnos/reportes screen handlers (admin_cola_screen.py et al.), NOT under
`modules/turnos/`. The turnos BUSINESS files remain physically flat per
modules/turnos/module.json's own status_note ("not yet moved under
modules/turnos/"), but these screen handlers are platform-side admin-panel
siblings, so they follow the admin_panel/ convention those four already
established.

The route decides WHETHER the page is servable (module enabled, session
authorized) and renders the shared admin shell + the
`templates/admin/solicitudes.html` template, which gives the shared
`admin-app.js` Requests controller the exact DOM it already drives. No
business logic lives in the template; the routes under
`/api/admin/requests*` still do 100% of the data work.

Role gate: `solicitudes`'s `admin_sections` manifest entry declares
`roles: ["admin", "medico", "admisionista"]` — the SAME three roles
`cola` declares, so `deps.require_admin_screen(permitir_medico=True,
permitir_admisionista=True)` is byte-compatible with
`app_modules._section_allowed_for_role()`'s reading of this section with
no extra check needed afterward (same reasoning as `admin_cola_screen.py`
— cola's roles list is exactly "admin plus the two opt-in flags", the same
shape `require_admin_screen`'s own default-admits-admin behavior already
produces).

Adapted from `admin_cola_screen.py`: turnos module gate, the SAME shared
nav/render/i18n/theme/no-store pattern, only `_SECTION_ID`, `_TEMPLATE`,
the route path and the route function name differ.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import screen_page

_SECTION_ID = "solicitudes"
_TEMPLATE = "admin/solicitudes.html"

# Router-level (not parameter-level) dependency: same pattern as
# admin_cola_screen.py — a disabled module 404s before the role gate is
# even evaluated.
router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
screen_router = router  # the name the manifest expects (screen.router_attr)


@router.get("/admin/solicitudes")  # == module.json's admin_sections[].screen.path
async def solicitudes_screen(
    identity: dict = Depends(deps.require_admin_screen(permitir_medico=True, permitir_admisionista=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
        rol=identity.get("rol"),
    )
