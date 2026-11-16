"""admin_cola_screen.py — Tier 5 slice 5 (docs/roadmap/tier5-frontend-design.md,
"Progressive admin-section migration"): the server-routed twin of the legacy
`#cola` hash section ("Pacientes de hoy"), second real consumer of the
`admin_sections[].screen` mechanism slice 4 proved on `intake`'s
`entrevistas` pilot.

Its OWN sibling file at repo root, not `modules/turnos/` — `turnos`'s
files are still physically flat (see modules/turnos/module.json's own
status_note: "not yet moved under modules/turnos/"), same convention every
other turnos-owned Python file already follows.

**No new business logic** — `doctor_appointments.py`'s GET
`/api/admin/day-queue` is untouched and still does 100% of the data work;
this route only decides WHETHER the page is servable (module enabled,
session authorized) and renders the initial queue plus the native
`static/platform/admin-queue.mjs` controller. Its mounted owner hydrates the
queue and owns subsequent requests, mutations, listeners, and disposal.

Role gate: `cola`'s `admin_sections` manifest entry declares
`roles: ["admin", "medico", "admisionista"]` — the SAME three roles
`doctor_appointments._day_queue()` itself already admits
(`admin_auth.require_admin(handler, permitir_medico=True,
permitir_admisionista=True)`, see that function's own comment: "the
admission desk sees the whole day queue... only the 'medico' role gets
filtered down"). `deps.require_admin_screen(permitir_medico=True,
permitir_admisionista=True)` is therefore byte-compatible with
`app_modules._section_allowed_for_role()`'s reading of this section with
no extra check needed afterward — unlike `mis-turnos` (whose `roles` list
excludes 'admin'), `cola`'s roles list is exactly "admin plus the two
opt-in flags", the same shape `require_admin_screen`'s own default-admits-admin
behavior already produces.

`rol` template context: `admin-queue.mjs` reads `window.CURRENT_ROLE`
(unguarded, several call sites — "Crear turno" visibility, the medico-only
entrevista banners) for role-scoped UI it can't derive from the DOM alone.
Legacy admin.html sets that global from its own client-side `applyRol()`
(the whoami dance this screen deliberately skips — see templates/admin/
base.html's docstring); the template's `scripts` block sets it directly
from the SAME identity this route already resolved server-side, so the
real renderer sees the real role from byte one instead of silently
degrading every role-gated control to "hidden".
"""

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.modules.turnos import doctor_appointments
from app.core import db, deps, i18n
from app.core import format as display_format
from app.core import screen_page

_SECTION_ID = "cola"
_TEMPLATE = "admin/cola.html"


def name_age(turno):
    """«Name, N años» — the row's one link, as admin-queue.mjs:nombreEdad
    writes it (the product owner, 2026-11-09); the bare name when the birth date is
    unknown."""
    lang = i18n.active()
    nombre = display_format.person_name(turno.get("paciente")) or i18n.t_or(
        "admin.queue.overlay.sin_nombre", "(sin nombre)", lang)
    edad = display_format.age_years(turno.get("nacimiento"))
    if edad is None:
        return nombre
    anios = i18n.tn_or("admin.queue.table.edad_anios", edad, "{n} año", "{n} años", lang)
    return i18n.t_or("admin.queue.table.nombre_edad", "{nombre}, {edad}", lang, edad=anios, nombre=nombre)  # the name last: it is free text


# Dependencia a nivel ROUTER (no de parámetro): mismo patrón que
# doctor_appointments_router.py/admin_patients_router.py ya usan para
# turnos — un módulo deshabilitado 404ea sin llegar siquiera a evaluar el
# gate de rol. "turnos" no es un módulo que se deshabilite en la práctica
# (todo lo demás depende de él — ver modules/intake/module.json's
# `requires`), pero el contrato es el mismo para cualquier router_attr.
router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
screen_router = router  # nombre que el manifiesto espera (screen.router_attr)


@router.get("/admin/cola")  # == module.json's admin_sections[].screen.path
async def cola_screen(
    identity: dict = Depends(deps.require_admin_screen(permitir_medico=True, permitir_admisionista=True)),
):
    # The server sends usable queue rows in the very first HTML document.
    # A database outage leaves a neutral empty shape; the existing client
    # loader still gets one opportunity to recover after JavaScript starts.
    try:
        queue_data = await run_in_threadpool(
            doctor_appointments.day_queue_data,
            identity, doctor_appointments._today_art_iso(), None)
    except doctor_appointments.DomainError as exc:
        # A medico account can exist before an administrator links it to a
        # doctor profile.  The JSON API keeps returning its explicit 400
        # contract, but the account's landing page must still render instead
        # of turning that expected setup state into a 500.
        if exc.body != {"error": "cuenta_sin_medico"}:
            raise
        queue_data = {
            "dia": doctor_appointments._today_art_iso(), "hoy": True,
            "grupos": [], "total": 0, "account_unlinked": True,
        }
    except db.DbUnavailable:
        queue_data = {
            "dia": doctor_appointments._today_art_iso(), "hoy": True,
            "grupos": [], "total": 0, "server_unavailable": True,
        }
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
        rol=identity.get("rol"),
        queue_data=queue_data,
        queue_display_name=display_format.person_name,
        queue_name_age=name_age,
    )
