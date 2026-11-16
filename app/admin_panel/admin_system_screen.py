"""Server-routed platform-core System status screen.

This is the direct ``/admin/system`` twin of legacy ``/admin#sistema``.
It renders only the authenticated shell and the existing
``admin-system.js`` controller. Infrastructure probes remain exclusively in
``GET /api/admin/system`` and therefore run client-side after first paint;
rendering this HTML never contacts a database or an external service.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import screen_page

_SECTION_ID = "sistema"
_TEMPLATE = "admin/system.html"

router = APIRouter()


@router.get("/admin/system")
async def system_screen(
    identity: dict = Depends(deps.require_admin_screen(solo_admin=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
        namespaces=("admin", "backup"),
    )
