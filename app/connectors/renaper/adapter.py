"""ABC-conforming adapter for the renaper connector.

This is what `connectors.adapter_for("identity_verification")` instantiates
— the synthetic-identity login (`cuentas/accounts.py:_login_test_identity`,
behind the LAN/dev gate) reaches `renaper.authenticate()` through here since
the registry rewire. Production Mi Argentina OIDC login (`auth_renaper.py`,
the HTTP route) is NOT on the registry: it stays config-driven
(`TURNOS_AUTH_METHODS`, `TURNOS_RENAPER_CLIENT_ID/SECRET`) by design. The
stub-identity CRUD endpoints (`list_test_identities`, `save_test_identity`,
`create_random_identity`) are renaper-only test tooling and keep calling the
module directly. A new country's connector copies this shape (see
docs/connectors.md's Aadhaar walkthrough).
"""
from __future__ import annotations

from app.connectors import IdentityVerificationConnector
from . import renaper as _impl


class RenaperConnector(IdentityVerificationConnector):
    """Wraps renaper.py's `authenticate()` — the real RENAPER/Mi Argentina
    OIDC flow lives in auth_renaper.py (the HTTP route), this module is the
    identity registry/stub half of that flow."""

    def authenticate(self, dni, provider="stub", **kwargs):
        # Every keyword reaches the implementation: `existing_user` is how the
        # login hands over a real account's own civil data so a stub login
        # never overwrites its name/birthdate with synthetic junk — dropping
        # kwargs here would silently re-introduce that clobber.
        return _impl.authenticate(dni, provider, **kwargs)
