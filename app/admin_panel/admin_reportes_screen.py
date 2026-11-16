"""admin_reportes_screen.py — Tier 5 slice 5 (docs/roadmap/
tier5-frontend-design.md, "Progressive admin-section migration"): the
server-routed twin of the legacy `#reportes` hash section (the restricted
reports dashboard — Management/Doctors/Patients/AI-costs/SUMAR
sub-reports), fifth real consumer of the `admin_sections[].screen`
mechanism after intake's `entrevistas` pilot and turnos' `cola`/
`pacientes`/`mis-turnos` siblings.

Its OWN sibling file under `admin_panel/`, same convention every other
turnos-owned admin screen already follows (admin_cola_screen.py/
admin_pacientes_screen.py/admin_mis_turnos_screen.py — see
modules/turnos/module.json's own status_note: turnos' files stay
physically flat).

**No new business logic** — every report calculation stays in
`reports.py` (+ `reports_router.py`, the FastAPI twin) and the live
catalog aggregations in `db.py`/`patient_web/site_*.py`; this route only
decides WHETHER the page is servable (module enabled, session
authorized) and renders the shell + the native `platform/admin-reports.mjs`
controller. The default
"Resumen" landing view (the card grid `renderResumen` builds) is produced by
that renderer's client-side `fetch()` to `/api/admin/reports` —
nothing is recomputed or re-fetched here.

Role gate — a THIRD shape, distinct from both `cola` and `mis-turnos`.
`reportes`'s `admin_sections` manifest entry declares
`roles: ["admin", "reportes"]`, the SAME two roles the underlying data
endpoints themselves admit: every handler in `reports.py` and
`reports_router.py` gates with
`admin_auth.require_admin(handler, solo_admin=True, permitir_reportes=True)`
(reports ARE solo_admin — the 'reportes' role is the ONE non-admin role
solo_admin exempts, via admin_auth.require_admin's own
`not (rol == "reportes" and permitir_reportes)` clause). Unlike `cola`
(`roles: ["admin", "medico", "admisionista"]`, plain `permitir_medico=/
permitir_admisionista=` flags, NO solo_admin) and unlike `mis-turnos`
(`roles: ["medico"]`, no admin, needs an explicit post-check because
`permitir_medico=True` would still admit admin), reportes' byte-compatible
gate IS `solo_admin=True, permitir_reportes=True` verbatim —
`deps.require_admin_screen(solo_admin=True, permitir_reportes=True)` reproduces
`app_modules._section_allowed_for_role()`'s reading of this section with
no extra check afterward: the 'reportes' role passes solo_admin through
the opt-in, admin passes solo_admin directly, and medico/admisionista/
marca/no-session fall on the same 403/401 the data endpoints already
answer. This is the slice-5 section the intake pilot's docstring was
waiting on — not the `mis-turnos` gap (roles without admin), but its
mirror: a `roles` list WITH admin where the non-admin member is the
solo_admin opt-in rather than a standalone `permitir_*` flag.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import format as formatting
from app.core import screen_page

_SECTION_ID = "reportes"
_TEMPLATE = "admin/reportes.html"

# ROUTER-level dependency (not per-parameter): same pattern already used by
# admin_cola_screen.py/admin_mis_turnos_screen.py — a disabled module 404s
# before the role gate is even evaluated.
router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
screen_router = router  # name the manifest expects (screen.router_attr)


@router.get("/admin/reportes")  # == module.json's admin_sections[].screen.path
async def reportes_screen(
    identity: dict = Depends(deps.require_admin_screen(solo_admin=True, permitir_reportes=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
        reports_currency_json=formatting.country_currency_json(),
    )
