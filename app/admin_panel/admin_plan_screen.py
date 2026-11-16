"""Server-routed Pinamar launch-roadmap screen.

This is the direct ``/admin/plan`` twin of legacy ``/admin#plan``.  It owns
only the authenticated Pinamar shell; ``platform/admin-roadmap.mjs`` is the one
renderer of the plan content.  Its direct technical view uses the English
wire-state ``?view=technical`` while the legacy ``#plan/tecnico`` bookmark
continues to work unchanged in ``admin.html``.

The roadmap belongs to Pinamar's reference deployment, not generic Human
Rounds navigation.  The route therefore returns 404 outside the Pinamar
theme, matching ``sections_payload_for_role()`` which omits its sidebar item.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core import screen_nav
from app.core import deps
from app.core import theme
from app.core import screen_page

_SECTION_ID = "plan"
_TEMPLATE = "admin/plan.html"

router = APIRouter()


@router.get("/admin/plan")
async def plan_screen(identity: dict = Depends(deps.require_admin_screen(solo_admin=True))):
    if theme.current() != "pinamar":
        raise HTTPException(status_code=404, detail={"error": "no_encontrado"})
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
        emr_sync_enabled=screen_nav.has_section("hsi", identity.get("rol")),
    )
