"""Server-routed platform-core Settings screen.

The direct ``/admin/settings`` route renders only the authenticated shell and
the lifecycle-owned ``platform/admin-settings.mjs`` controller. Reads, writes, and probes
remain behind the existing client-side API routes.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import screen_page

_SECTION_ID = "settings"
_TEMPLATE = "admin/settings.html"

router = APIRouter()


@router.get("/admin/settings")
async def settings_screen(
    identity: dict = Depends(deps.require_admin_screen(solo_admin=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
        namespaces=("admin", "backup"),
    )
