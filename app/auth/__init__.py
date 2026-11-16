"""auth/ — admin/panel authentication: login, 2FA/TOTP, passkeys (WebAuthn),
password reset, patient sign-in mode, Cloudflare Turnstile, the pre-launch
site-wide Basic Auth gate, the public-MCP resource guard, and the
Turnstile IP-exemption list.

Tier 2 batch C (docs/roadmap/tier2-physical-modularization-plan.md): a
domain package, not a module — shared infra other code calls into, never
disabled at runtime. Deliberately minimal: no re-export facade. Importers
use explicit submodules:

    from auth import admin_auth                          # panel login/session/role gate
    from auth import admin_auth_router                    # FastAPI twin: /api/admin/{config,login,logout,whoami}
    from auth import panel_login                          # login-screen content + telemetry
    from auth import panel_login_router                   # FastAPI twin: /api/login-panel*
    from auth import passkeys                              # WebAuthn passkey login for the panel
    from auth import passkeys_router                       # FastAPI twin: /api/admin/passkeys*
    from auth import webauthn_adapter as passkeys_webauthn_adapter  # py_webauthn ceremony boundary
    from auth import password_reset as admin_password_reset          # one-use panel password reset page
    from auth import password_reset_router as admin_password_reset_router  # its FastAPI twin
    from auth import mode as auth_mode                     # patient sign-in mode (password vs RENAPER)
    from auth import turnstile                              # single owner of the Cloudflare Turnstile gate
    from auth import site_auth                              # pre-launch Basic Auth in front of the whole site
    from auth import mcp_guard                              # resource budgets for the public MCP connector
    from auth import ip_exempt                              # IPs exempt from admin-login Turnstile
"""
