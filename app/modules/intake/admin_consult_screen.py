"""Server-routed ``/admin/consult`` intake consultation workspace.

The route renders only the authenticated shell and the existing
native intake-owned controller. Interview reads, doctor scoping, and
consultation confirmation remain exclusively in ``consult_admin.py``.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import screen_page

_MODULE_ID = "intake"
_SECTION_ID = "consulta"
_TEMPLATE = "modules/intake/admin_consult.html"

router = APIRouter(dependencies=[Depends(deps.require_module_enabled(_MODULE_ID))])
screen_router = router


@router.get("/admin/consult")
async def consult_screen(
    identity: dict = Depends(deps.require_admin_screen(permitir_medico=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
        namespaces=("admin", "admin.queue"),
    )
