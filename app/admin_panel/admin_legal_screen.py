"""Server-routed platform-core ``/admin/legal`` screen.

The direct twin of the legacy ``/admin#legal`` section ("Términos y
privacidad"): the two site legal documents (terms + privacy, ``legal.py``),
editable as running text. The screen owns only the authenticated ASGI shell
and the direct URL; the existing ``admin-legal.js`` controller still owns
every read, save and restore through the unchanged ``/api/admin/legal*`` API
(``patient_web/legal.py``), fetched client-side — the render never reads a
document.

Platform-core (``core.app_modules._PLATFORM_ADMIN_SECTIONS``): no module
gate. Admin-only, verbatim the data API's own ``solo_admin=True`` gate — copy
the gate, never infer it. Not an expired-2FA repair surface: that session
stays closed here exactly as on every other ``/admin/*`` screen.

Direct state is ``/admin/legal`` (list) or ``/admin/legal?doc=<id>`` (editor),
owned by the controller through pushState/popstate; ``#legal`` and
``#legal/<doc>`` on admin.html are untouched.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import screen_page

_SECTION_ID = "legal"
_TEMPLATE = "admin/legal.html"

router = APIRouter()


@router.get("/admin/legal")
async def legal_screen(
    identity: dict = Depends(deps.require_admin_screen(solo_admin=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
    )
