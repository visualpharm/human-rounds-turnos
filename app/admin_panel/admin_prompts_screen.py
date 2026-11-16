"""Server-routed ``/admin/prompts`` twin of the legacy prompt editor.

The screen owns only the ASGI shell, module/role gate, and direct URL.  The
existing ``admin-prompts.js`` controller still owns prompt-list reads, exact
key selection, translations, saves, restores, and history through the
unchanged ``/api/admin/prompts*`` API.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import screen_page

_SECTION_ID = "prompts"
_TEMPLATE = "admin/prompts.html"

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
screen_router = router


@router.get("/admin/prompts")
async def prompts_screen(
    identity: dict = Depends(deps.require_admin_screen(solo_admin=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
    )
