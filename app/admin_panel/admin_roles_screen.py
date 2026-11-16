"""Server-routed platform-core Roles screen.

This is the direct ``/admin/roles`` screen for managing custom roles
and editing role permissions. It renders the authenticated shell and
imports the frontend controller for roles management.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import screen_page

_SECTION_ID = "roles"
_TEMPLATE = "admin/roles.html"

router = APIRouter()
screen_router = router  # Alias for the manifest's screen.router_attr


@router.get("/admin/roles")
async def roles_screen(
    identity: dict = Depends(deps.require_admin_screen(solo_admin=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
        current_user=identity.get("usuario") or "",
        identity=identity,
    )
