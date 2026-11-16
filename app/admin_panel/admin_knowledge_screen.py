"""admin_knowledge_screen.py — Tier 5 direct Wiki/knowledge screen
(docs/roadmap/tier5-frontend-design.md, "Progressive admin-section
migration"): the server-routed twin of the legacy `#knowledge` hash
section, ninth `/admin/<screen>` twin in that sequence and the eighth
landed turnos-owned one (entrevistas, cola, pacientes, mis-turnos,
reportes, solicitudes, preparations).

Its OWN sibling file under `admin_panel/`, same convention every other
turnos-owned admin screen already follows. The identifier is already
English (`knowledge`), so the NEW route (`/admin/knowledge`), template
(`admin/knowledge.html`) and `_SECTION_ID = "knowledge"` all stay on
that spelling. The LEGACY hash/DOM identifier is preserved verbatim:
`window.SANA_ADMIN_SCREEN`, `#sec-knowledge`, `data-nav="knowledge"`
and `#knowledge` / `#knowledge/<id>` remain the compatibility surface
the shared renderer keys on. Direct-URL item state is the query
`?item=<id>` (client-owned; this controller does not read it).

**No new business logic** — `admin_content.py`'s
`/api/admin/content/knowledge` CRUD (including properties / soft-disable
/ restore) is untouched and still does 100% of the data work; this
route only decides WHETHER the page is servable (module enabled,
session authorized) and renders the shell + the same
`admin-knowledge.js` that already paints the legacy `#knowledge` hash
section, via its own client-side `fetch()`.

Role gate — entrevistas/preparations shape: the manifest entry
declares no `roles` key, so `app_modules._section_allowed_for_role()`
reads "no `roles` → `rol == 'admin'`", matching `applyRol`'s
`'nav-knowledge': esAdmin`. `deps.require_admin_screen(solo_admin=True)`
reproduces that exactly — no `permitir_*` flag, no post-check. No
session → 401; every live non-admin role (medico, admisionista,
reportes) → 403; the retired `marca` role never reaches the 403 branch
(its token fails `_validar_payload`'s whitelist) and records as 401.

The data-API gate is deliberately NOT aligned: GET
`/api/admin/content/knowledge` still admits medico/admisionista/
reportes (shared catalog read); every WRITE is solo_admin. The
manifest governs the SCREEN.

No `rol` template context: the controller never reads
`window.CURRENT_ROLE`. Sidebar items come from the shared helper
(`core.screen_nav.nav_items`), never a private `_nav_items`
copy.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import screen_page

_SECTION_ID = "knowledge"
_TEMPLATE = "admin/knowledge.html"

# ROUTER-level dependency (not per-parameter): a disabled module 404s
# before the role gate is even evaluated — same pattern every sibling
# screen router already uses.
router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
screen_router = router  # name the manifest expects (screen.router_attr)


@router.get("/admin/knowledge")  # == module.json's admin_sections[].screen.path
async def knowledge_screen(
    identity: dict = Depends(deps.require_admin_screen(solo_admin=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
    )
