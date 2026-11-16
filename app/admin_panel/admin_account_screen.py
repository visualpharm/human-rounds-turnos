"""Server-routed platform-core Account screen.

This is the direct ``/admin/account`` twin of the legacy ``/admin#cuenta``
self-service surface (email, password, 2FA, passkeys, logout). It renders
only the authenticated shell and the native ``platform/admin-account.mjs``
controller; every account read and mutation stays behind the existing
client-side API routes (``/api/admin/account*``, ``/api/admin/passkeys*``).

Universal to authenticated staff: the gate admits exactly the account API's
role cohort (admin, operador, medico, admisionista, reportes, marca) — no
``solo_admin``, no module gate, no catalog or patient
data read. ``auth.admin_auth.require_admin`` also lets an expired-2FA
session reach this one path (equality-only), because this is the only UI
that can repair that state.
"""

from fastapi import APIRouter, Depends

from app.auth import staff_portal
from app.core import deps
from app.core import screen_page
from app.cuentas import portal_identity

_SECTION_ID = "cuenta"
_TEMPLATE = "admin/account.html"

router = APIRouter()


@router.get("/admin/account")
async def account_screen(
    identity: dict = Depends(deps.require_admin_screen(
        permitir_medico=True,
        permitir_admisionista=True,
        permitir_reportes=True,
        permitir_marca=True,
    )),
):
    # None = the installation has its own login (the button ends it); on a
    # portal installation "" = no portal sign-out known (no button), else the
    # address the button leaves through.
    portal_logout = portal_identity.logout_url() if staff_portal.enabled() else None
    return screen_page.admin_page(
        identity, _TEMPLATE, _SECTION_ID,
        current_user=identity.get("usuario") or "",
        portal_logout=portal_logout,
    )
