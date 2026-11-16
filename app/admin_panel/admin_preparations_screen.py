"""admin_preparations_screen.py — Tier 5 direct Preparations screen
(docs/roadmap/tier5-frontend-design.md, "Progressive admin-section
migration"): the server-routed twin of the legacy `#preparaciones` hash
section ("Cómo prepararse"), eighth `/admin/<screen>` twin in that
sequence and the seventh landed (entrevistas, cola, pacientes,
mis-turnos, reportes, solicitudes).

Its OWN sibling file under `admin_panel/`, same convention every other
turnos-owned admin screen already follows. The NEW identifier this
slice mints is English (`admin_preparations_screen`, route
`/admin/preparations`, template `admin/preparations.html`) — the
repo-wide rule that URL path segments are English. The LEGACY Spanish
hash/DOM identifier is preserved verbatim: `_SECTION_ID =
"preparaciones"` so `window.SANA_ADMIN_SCREEN`, `#sec-preparaciones`,
`data-nav="preparaciones"` and the `#preparaciones` /
`#preparaciones/<id>` sub-hash stay the compatibility surface the
shared renderer keys on.

**No new business logic** — `admin_content.py`'s
`/api/admin/content/preparations` CRUD is untouched and still does
100% of the data work; this route only decides WHETHER the page is
servable (module enabled, session authorized) and renders the shell +
the native lifecycle-owned `platform/admin-preparations.mjs`
controller (the classic root `admin-preparations.js` is physically
retired), via its own client-side `fetch()`.

Role gate — the strictest shape in the panel, the entrevistas
precedent: the manifest entry declares no `roles` key, so
`app_modules._section_allowed_for_role()` reads "no `roles` →
`rol == 'admin'`", matching `applyRol`'s `'nav-preparaciones':
esAdmin`. `deps.require_admin_screen(solo_admin=True)` reproduces that
exactly — no `permitir_*` flag, no post-check. No session → 401;
every live non-admin role (medico, admisionista, reportes) → 403;
the retired `marca` role never reaches the 403 branch (its token
fails `_validar_payload`'s whitelist) and records as 401. Distinctive
vs. cola (renders for medico+admisionista) and reportes (renders for
the `reportes` role): this screen rejects every non-admin role.

The data-API gate is deliberately NOT aligned: GET
`/api/admin/content/preparations` still admits medico/admisionista/
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

_SECTION_ID = "preparaciones"
_TEMPLATE = "admin/preparations.html"

# ROUTER-level dependency (not per-parameter): a disabled module 404s
# before the role gate is even evaluated — same pattern every sibling
# screen router already uses.
router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
screen_router = router  # name the manifest expects (screen.router_attr)


@router.get("/admin/preparations")  # == module.json's admin_sections[].screen.path
async def preparations_screen(
    identity: dict = Depends(deps.require_admin_screen(solo_admin=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
    )
