"""Server-routed platform-core ``/admin/appearance`` screen.

The direct twin of the legacy ``/admin#configuracion`` section ("Apariencia y
acceso" / "Appearance and access"): the site theme, how patients sign in, the
anti-bot check, the chat dictation model, and the institutional colour and
logo. The screen owns only the authenticated ASGI shell and the direct URL;
the existing ``admin-theme.js`` controller still owns every read and save
through the unchanged APIs — ``/api/admin/theme``, ``/api/admin/auth-mode``
and ``/api/admin/turnstile`` (turnos-routed), ``/api/admin/chat-voice``
(chat-routed) and ``/api/admin/branding`` (platform) — fetched client-side.
The render never calls any of them.

Platform-core (``core.app_modules._PLATFORM_ADMIN_SECTIONS``): no module
gate, so the screen answers 200 even when a block's owning module is off and
its API answers 404. That gap is the module-degradation premise: the
controller draws only the blocks whose GET answered, in both shells. Admin-only,
verbatim the data APIs' own ``solo_admin=True`` gate — copy the gate, never
infer it. Not an expired-2FA repair surface: that session stays closed here
exactly as on every other ``/admin/*`` screen.

Direct state is the bare ``/admin/appearance`` — the section has no sub-state,
so any query or hash on load is canonicalised away client-side; ``#configuracion``
on admin.html is untouched.
"""

from fastapi import APIRouter, Depends

from app.core import deps
from app.core import screen_page

_SECTION_ID = "configuracion"
_TEMPLATE = "admin/appearance.html"

router = APIRouter()


@router.get("/admin/appearance")
async def appearance_screen(
    identity: dict = Depends(deps.require_admin_screen(solo_admin=True)),
):
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
    )
