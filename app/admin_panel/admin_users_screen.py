"""Server-routed platform-core Users screen.

This is the direct ``/admin/users`` twin of the legacy
``/admin#usuarios`` surface. It renders only the authenticated shell and
imports the canonical ``platform/admin-users.mjs`` controller. User reads
and mutations remain behind the existing client-side API routes.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from app.core import deps
from app.core import screen_page

_SECTION_ID = "usuarios"
_TEMPLATE = "admin/users.html"

router = APIRouter()


@router.get("/admin/users/import")
async def users_import_screen():
    # Legacy address: the doctor import is «Cargar lista» in Médicos since
    # 2026-11-03 (one path for a roster); Médicos gates its own screen.
    return RedirectResponse("/admin/doctors?import=1", status_code=308)


@router.get("/admin/users")
async def users_screen(
    identity: dict = Depends(deps.require_admin_screen(solo_admin=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
        current_user=identity.get("usuario") or "",
    )
