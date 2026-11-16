"""Server-routed turnos-owned ``/admin/hsi`` screen.

The direct twin of the legacy ``/admin#hsi`` section ("Sincronización con
HSI"): the synced share of upcoming approved appointments, the conflicts the
automatic resolver (``connectors/hsi/hsi_resolver.py``) could not close, each
with its recommended resolution and bounded alternatives, and the log of
automatic resolutions. The screen owns only the authenticated ASGI shell and
the direct URL; the native ``static/platform/admin-hsi.mjs`` controller (the
classic root ``admin-hsi.js`` is physically retired) owns every read and
action through the unchanged APIs — ``GET /api/admin/hsi-sync``,
``POST /api/admin/hsi-sync/resolver`` and ``POST /api/admin/hsi-sync/conflicts``
(``modules/turnos/booking_requests.py``, turnos-routed) — fetched client-side,
mounted by the template through its fingerprinted URL with the same
generation-guarded boot ``/admin/appearance`` uses. The render never calls
any of them, never touches the HSI connector, the resolver, the database or
a socket, and echoes no configuration value: ``TURNOS_SKIP_HSI`` changes
what the API answers, never whether this route exists.

Module-gated exactly like those APIs: the router carries
``deps.require_module_enabled("turnos")`` so with turnos off the screen is the
same 404 the APIs answer. Admin-only, verbatim the data APIs' own
``_check_admin(handler, {})`` (solo_admin) gate — copy the gate, never infer
it: admin 200, medico/admisionista/reportes 403, no or invalid session 401.
With the HSI connector off the section is gone from the menu, but the screen
still answers (2026-11-10, slice 7; until then a 303 to the start page): the
same shell, and the controller draws the honest state instead of the journal
— how a turno reaches HSI here, why, each center's institution number — from
the read-only ``GET /api/admin/system/hsi``. «Sistema» and «Conectores» link
here, so the menu entry lit in that case is «Sistema».
Not an expired-2FA repair surface: that session stays closed here exactly as
on every other ``/admin/*`` screen.

Direct state is the bare ``/admin/hsi`` — the section has no sub-state, so
any query or hash on load is canonicalised away client-side by the mounted
controller.
"""

from fastapi import APIRouter, Depends

from app.core import screen_nav
from app.core import deps
from app.core import screen_page

_SECTION_ID = "hsi"
_FALLBACK_SECTION_ID = "sistema"
_TEMPLATE = "admin/hsi.html"

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])
screen_router = router


@router.get("/admin/hsi")
async def hsi_screen(
    identity: dict = Depends(deps.require_admin_screen(solo_admin=True)),
):
    # HSI connector off: no menu entry of its own, and the screen says so
    # itself (client-side) instead of sending the admin away.
    in_menu = screen_nav.has_section(_SECTION_ID, identity.get("rol"))
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID if in_menu else _FALLBACK_SECTION_ID,
    )
