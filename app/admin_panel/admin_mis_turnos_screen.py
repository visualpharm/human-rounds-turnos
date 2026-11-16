"""admin_mis_turnos_screen.py — Tier 5 slice 5 (docs/roadmap/
tier5-frontend-design.md, "Progressive admin-section migration"): the
server-routed twin of the legacy `#mis-turnos` hash section (the doctor's
own upcoming appointments), fourth real consumer of the
`admin_sections[].screen` mechanism.

**No new business logic** — `doctor_appointments.py`'s GET
`/api/admin/my-appointments` is untouched and still does 100% of the data
work; this route only decides WHETHER the page is servable (module
enabled, session authorized) and renders the shell + the same
`admin-my-appointments.js` that already paints the legacy `#mis-turnos`
hash section.

Role gate — the ONE case this slice's sibling screens (`cola`, `pacientes`)
didn't need to solve: `mis-turnos`'s `admin_sections` manifest entry
declares `roles: ["medico"]` ONLY, no "admin" — confirmed against
`admin.html`'s own `applyRol()` (`'nav-mis-turnos': esMedico`, admin
excluded) AND `doctor_appointments._my_appointments()`'s own explicit
`if identity.get("rol") != "medico"` 403 (admin_auth.require_admin's
`permitir_medico=True` alone would still ADMIT an admin session — the
generic "admin always passes" shape every OTHER `permitir_*` flag has).
`modules/intake/admin_entrevistas_screen.py`'s own docstring names this
EXACT case as a named follow-up ("a section that later grows a `roles`
list without 'admin' would need its own screen route to translate that
list correctly... left as a named follow-up for whichever slice-5 section
first needs it") — this is that section. The fix mirrors the underlying
API's own two-step gate exactly: `deps.require_admin_screen(permitir_medico=True)`
gets a valid admin/medico session past the generic gate (2FA, demo lock,
'reportes'/'marca' rejection, ...), then an explicit `rol == "medico"`
check. The data endpoint still answers an admin 403 `solo_medico`; the
SCREEN sends that admin (303) to his role's start page instead of rendering
the JSON error (review 03/11) — no more permissive policy, the page still
never renders for him.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from app.admin_panel import admin_navigation
from app.core import deps
from app.core import screen_page

_SECTION_ID = "mis-turnos"
_TEMPLATE = "admin/mis-turnos.html"

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
screen_router = router  # nombre que el manifiesto espera (screen.router_attr)


@router.get("/admin/mis-turnos")  # == module.json's admin_sections[].screen.path
async def mis_turnos_screen(
    identity: dict = Depends(deps.require_admin_screen(permitir_medico=True)),
):
    # Segundo paso del gate — ver el docstring del módulo. Un admin no tiene
    # agenda propia: va a su pantalla de inicio, nunca a un JSON de error
    # (revisión 03/11). La API de datos sigue respondiendo 403 solo_medico.
    if identity.get("rol") != "medico":
        landing = admin_navigation.default_path_for_role(identity.get("rol")) or "/admin/account"
        return RedirectResponse(landing, status_code=303, headers={"Cache-Control": "no-store"})
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
    )
