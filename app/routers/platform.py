"""routers/platform.py — the ordered platform-core router composition
(extracted from app.py in the Tier 4 composition extraction slice).

ORDER MATTERS and is part of the routing contract: Starlette matches by
registration order, so app.py mounts `ORDERED` exactly as written here,
before `app_modules.mount_module_routers()` and long before the legacy
catch-all. The order below is byte-for-byte the pre-extraction app.py
mounting order (pinned by tests/test_app_composition.py):

- Slice 1 (auth) — admin_auth + passkeys: /api/admin/login, /api/admin/
  logout, /api/admin/config, /api/admin/whoami, /api/admin/passkeys/*
  must never fall through to the legacy forward.
- Slice 2 (i18n/Jinja2 pilot) — site_identity.py's four pages, see
  site_identity_router.py's module docstring.
- Proxy-retirement B4 — demo_installation.py's metadata and three synthetic
  session-entry routes. The handler remains the sole sandbox-policy owner;
  its router only removes the loopback transport dependency.
- Slice 8 (first batch) — accounts.py is platform-core (in no module.json,
  see accounts_router.py's own docstring), so it mounts here directly,
  without app_modules.mount_module_routers() and without
  deps.require_module_enabled().
- Slice 8 closing batch (tier4-replatform-design.md's own honest
  inventory, "Platform-core admin/settings sections with no router file
  yet") — nine more platform-core files, exact same treatment as
  accounts_router.py: each is in tests/test_module_manifest.py's
  PLATFORM_ROUTES allowlist, none is a modules/turnos/module.json route.
- Tier 5 platform screens — `/admin/system`, `/admin/settings`,
  `/admin/users`, and `/admin/account` are direct twins of their legacy
  hash sections. They mount here because platform-core screens have no
  module manifest; `core.app_modules` records their shared-nav paths.
  `/admin/account` is the self-service twin of `admin_account_router`'s
  API (same controller, same gate roles) and the one expired-2FA repair
  surface `auth.admin_auth.require_admin` admits by exact path.
  `/admin/legal` and `/admin/appearance` are the admin-only twins of
  `#legal` and `#configuracion`; the appearance screen is platform-core
  even though four of its five blocks read turnos- and chat-routed APIs,
  so it answers 200 with those modules off and the controller degrades
  per block.
- W3b (family W3b of
  docs/roadmap/native-migration-campaign-20260826.md) — `ops.setup`
  (`/setup*`, the first-run installer wizard), `core.legacy_urls` (the old-
  Spanish-URL page-redirect family), and `cuentas.auth_renaper`
  (`/auth/renaper*`, the RENAPER/Mi Argentina federated login). Mounted LAST in
  this tuple: `legacy_urls_router` in particular owns broad old-Spanish
  literal paths and prefix families that must not shadow any
  platform-core screen mounted above.

Module-owned routers (`router_attr` in a module.json) deliberately do NOT
appear here — app.py mounts those generically via
`app_modules.mount_module_routers()`, preserving the "zero central wiring
for a new module" guarantee routing.py already gives legacy routes.
"""
from app.admin_panel import admin_account_router
from app.admin_panel import admin_account_screen
from app.admin_panel import admin_appearance_screen
from app.admin_panel import admin_legal_screen
from app.admin_panel import admin_login_screen
from app.admin_panel import admin_plan_screen
from app.admin_panel import admin_root_screen
from app.admin_panel import admin_preferences_router
from app.admin_panel import client_errors_router
from app.admin_panel import table_export_router
from app.admin_panel import roles_router
from app.admin_panel import admin_settings_screen
from app.admin_panel import admin_users_screen
from app.admin_panel import admin_users_router
from app.admin_panel import settings_admin_router
from app.admin_panel import admin_system_screen
from app.admin_panel import system_status_router
from app.auth import admin_auth_router
from app.auth import panel_login_router
from app.auth import passkeys_router
from app.auth import password_reset_router as admin_password_reset_router
from app.core import app_modules_router
from app.core import i18n_catalog_router
from app.core import legacy_urls_router
from app.core import site_identity_router
from app.cuentas import accounts_router
from app.cuentas import auth_renaper_router
from app.ops import demo_installation_router
from app.ops import setup_router
from app.patient_web import branding_router
from app.patient_web import health_data_router
from app.patient_web import legal_router
from app.routers import api_docs

# The patient sign-in's staff probe (auth/admin_auth.py) resolves the catalog
# doctor's DNI; auth may not import patient_web (dependency direction), so this
# composition package hands the catalog accessor over — the same registration
# shape notifications.send.register_turnos established.
from app.auth import admin_auth as _admin_auth
from app.patient_web import catalog as _patient_catalog

_admin_auth.register_staff_doctors_provider(_patient_catalog.doctors)

ORDERED = (
    admin_auth_router.router,
    passkeys_router.router,
    app_modules_router.router,
    site_identity_router.router,
    demo_installation_router.router,
    accounts_router.router,
    admin_root_screen.router,
    admin_login_screen.router,
    admin_users_router.router,
    roles_router.router,
    admin_preferences_router.router,
    admin_password_reset_router.router,
    admin_account_router.router,
    settings_admin_router.router,
    panel_login_router.router,
    system_status_router.router,
    admin_system_screen.router,
    admin_settings_screen.router,
    admin_users_screen.router,
    admin_account_screen.router,
    admin_legal_screen.router,
    admin_appearance_screen.router,
    admin_plan_screen.router,
    branding_router.router,
    health_data_router.router,
    legal_router.router,
    setup_router.router,
    auth_renaper_router.router,
    legacy_urls_router.router,
    # Final wave: the JS catalog API (/api/i18n/<lang>.json) — its only
    # owner used to be core.i18n.handle() on the retired dark dispatcher;
    # appended at the tail (its path overlaps nothing above).
    i18n_catalog_router.router,
    # The staff screens' browser errors (templates/admin/base.html) into
    # core.error_tracking — its path overlaps nothing above.
    client_errors_router.router,
    # The panel's «Descargar» of a small table (admin_panel/table_export.py)
    # — its path overlaps nothing above.
    table_export_router.router,
    # The API documentation (/openapi.json, /docs), admin-only; everyone
    # else still gets the native terminal's answer — overlaps nothing above.
    api_docs.router,
)
