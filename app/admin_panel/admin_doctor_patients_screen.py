"""Server-routed ``/admin/my-patients`` screen for the longitudinal roster.

The roster data remains exclusively in ``admin_panel.doctor_patients``.  This
screen only applies the same module and role gates as that endpoint, renders
the shared admin shell, and lets the existing ``admin-doctor-patients.js``
renderer hydrate its established ``#sec-mis-pacientes`` container.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import screen_page

_SECTION_ID = "mis-pacientes"
_TEMPLATE = "admin/my-patients.html"

# The disabled-module response must be indistinguishable from a missing URL.
router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
screen_router = router


@router.get("/admin/my-patients")
async def my_patients_screen(
    identity: dict = Depends(deps.require_admin_screen(permitir_medico=True)),
):
    """Render for exactly the admin and medico roles the roster API admits."""
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
        rol=identity.get("rol"),
    )
