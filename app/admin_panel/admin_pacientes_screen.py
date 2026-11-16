"""admin_pacientes_screen.py — Tier 5 slice 5 (docs/roadmap/
tier5-frontend-design.md, "Progressive admin-section migration"): the
server-routed twin of the legacy `#pacientes` hash section, third real
consumer of the `admin_sections[].screen` mechanism (`entrevistas` in
slice 4, `cola` earlier in this same slice — see admin_cola_screen.py for
the shared reasoning this file doesn't repeat).

**No new business logic** — `admin_patients.py`'s GET
`/api/admin/patients` is untouched and still does 100% of the data work;
this route only decides WHETHER the page is servable (module enabled,
session authorized) and renders the shared shell. Its template mounts the
native `static/platform/admin-patients.mjs` controller, which owns the list,
`?patient=<dni>` detail and `?new=1` form routes. Each route invalidates stale
reads and mutations; page exit disposes the mount. The classic root script
and its Spanish asset alias are retired, not compatibility fallbacks.

Role gate: `pacientes`'s `admin_sections` manifest entry declares
`roles: ["admin", "admisionista"]` — the SAME two roles every
screen admits. The roster screen itself forbids doctors; the separately
doctor-scoped collection API retains its own permission gate.
`deps.require_admin_screen(permitir_admisionista=True)` is byte-compatible with
`_section_allowed_for_role()`'s reading of this section, same reasoning
`cola`'s screen gate documents for its own roles list.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import screen_page

_SECTION_ID = "pacientes"
_TEMPLATE = "admin/pacientes.html"

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
screen_router = router  # nombre que el manifiesto espera (screen.router_attr)


@router.get("/admin/pacientes")  # == module.json's admin_sections[].screen.path
async def pacientes_screen(
    identity: dict = Depends(deps.require_admin_screen(permitir_admisionista=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
    )
