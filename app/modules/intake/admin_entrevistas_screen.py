"""admin_entrevistas_screen.py — Tier 5 slice 4 pilot (docs/roadmap/
tier5-frontend-design.md, "admin.html decomposition into /admin/<screen>"):
the FIRST server-routed `/admin/<screen>` page, mirroring the design's own
worked example almost verbatim.

Its OWN sibling file, never `intake_admin.py`/`intake_admin_router.py` —
same reasoning `interview_patient_router.py`'s docstring already gives for
every other `_router.py` in this module: importing FastAPI/Jinja machinery
from a file dark mode already imports unconditionally would leak that cost
into every boot. `screen_router` is mounted by the SAME
`app_modules.mount_module_routers()`/`merge_app_routers()` that mounts
every other `router_attr` (Tier 5 slice 4's "Module contract extension" —
no new loader concept, a screen router IS a FastAPI router; the only new
thing is that its handler calls `TemplateResponse` instead of returning
JSON).

**No new business logic** — `intake_admin.py`'s GET/POST
`/api/admin/intake/settings` is untouched and still does 100% of the data
work; this route only decides WHETHER the page is servable (module
enabled, session authorized) and renders the shell + the same
canonical `admin-entrevistas.mjs` that paints `#sec-entrevistas` via its
own client-side `fetch()` — see `modules/intake/admin_entrevistas.html`
for why the template doesn't pre-fetch data server-side.

Role gate: `entrevistas`'s `admin_sections` manifest entry declares no
`roles` key (see module.json's own `admin_sections_note` — the section is
solo_admin today, same as `intake_admin.py`'s own GET /settings handler
and `admin-entrevistas.mjs`'s file header both already say). Gating this
route with `deps.require_admin(solo_admin=True)` is therefore the
byte-compatible choice: it reproduces the EXACT visibility
`app_modules._section_allowed_for_role()` already computes for this
section's sidebar entry today (no `roles` -> `rol == "admin"`), rather
than inventing a new, more permissive policy for the pilot. A section
that later grows a `roles` list (e.g. turnos' "mis-turnos": ["medico"], no
admin) would need its OWN screen route to translate that list correctly —
deliberately not attempted here as a generic mechanism, because
`admin_auth.require_admin()`'s un-flagged default already admits any
admin/operador session regardless of `permitir_*`, which is NOT the same
exclusion semantics a `roles` list without "admin" expresses (see
`_section_allowed_for_role`'s docstring) — building that translator with
no real pilot to prove it against would be unverified machinery, so it's
left as a named follow-up for whichever slice-5 section first needs it.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import screen_page

_MODULE_ID = "intake"
_SECTION_ID = "entrevistas"
_TEMPLATE = "modules/intake/admin_entrevistas.html"

# Dependencia a nivel ROUTER (no de parámetro): mismo patrón que todo
# router_attr de `routes[]` ya usa (account_photo_router.py,
# admin_content_router.py, ...) — se resuelve ANTES que cualquier
# Depends() de parámetro de un endpoint puntual, así que un módulo
# deshabilitado 404ea sin llegar siquiera a evaluar el gate de rol.
router = APIRouter(dependencies=[Depends(deps.require_module_enabled(_MODULE_ID))])
screen_router = router  # nombre que el manifiesto espera (screen.router_attr)


@router.get("/admin/entrevistas")  # == module.json's admin_sections[].screen.path
async def entrevistas_screen(
    identity: dict = Depends(deps.require_admin_screen(solo_admin=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
    )
