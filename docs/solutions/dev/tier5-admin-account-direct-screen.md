---
title: Tier 5 direct staff account screen and the expired-2FA repair surface
scope: repo
area: dev
tags: [tier5, admin, account, 2fa, security, routing]
created: 2026-08-21
refs: [tier5-platform-admin-system-screen.md, tier5-admin-content-direct-screens.md, tier5-admin-screen-role-gates-y-static-fn.md]
status: active
---

## Problem

Every staff member edits their own email, password, 2FA and passkeys on the
legacy `/admin#cuenta` hash section, the last platform-core destination with
no reload-safe direct URL. A direct twin has a trap the other platform screens
did not: `admin_auth.require_admin` locks an expired-2FA session out of every
path except `/api/admin/account*`, `/api/admin/whoami` and `/api/admin/logout`,
so a server-routed `/admin/account` would 403 the one person who needs it to
repair their 2FA — and widening the existing prefixes to `/admin/` would open
every other admin screen to that locked session.

## Solution

`/admin/account` (`admin_panel/admin_account_screen.py`,
`templates/admin/account.html`) mounts in `routers/platform.py` with the four
other platform screens, registered in `core.app_modules._PLATFORM_ADMIN_SCREEN_PATHS`
so the shared sidebar links to it for every role. The gate copies
`admin_account._identity` flag for flag (`permitir_medico/admisionista/
reportes/marca`, no `solo_admin`, no module gate).

The repair surface is a SEPARATE equality-only tuple in `require_admin`
(`repair_screens = ("/admin/account",)`) next to the prefix tuple: exact path
match, never `startswith`, so `/admin/accounts`, `/admin/account/x` and every
other `/admin/*` path keep failing closed, and passkey endpoints stay outside
the exception. `base.html` gained the matching direct-shell hide rule
(`body.adm-2fa-required .adm5-nav > a:not([data-nav=cuenta])`) — written with
an UNQUOTED attribute value on purpose, because every sentinel-nav test proves
a patched sidebar by the absence of the literal `data-nav="cuenta"` in the
HTML.

The template reuses the legacy footer markup (`#adm-2fa-note` + `#adm-logout`)
through a new empty `nav_footer` block in `base.html` (whose CSS already
carried `.adm-nav-footer*`/`.adm-logout`), and loads exactly
`i18n.js` → `admin-account.js`; the controller imports canonical
`static/platform/passkeys.mjs` through the fingerprint manifest. `admin-account.js` in direct
mode (`SANA_ADMIN_SCREEN === "cuenta"`) keeps its state in the query:
`?setup=2fa` means the activation is open; `directState()` accepts exactly one
`setup=2fa` and nothing else, replaceState-canonicalizing everything else to
bare; the activation pushes the setup URL, popstate re-routes, reload
re-opens, and a confirmed 2FA drops the query. A direct 401 uses the shared
login controller and preserves either `/admin/account` or the valid
`/admin/account?setup=2fa` state in `?next=`; sign-out goes to `/admin`, never
the legacy inline gate nor a reload of the JSON 401.

## How to apply

- A platform screen that an expired-2FA session must reach is admitted by
  exact path in `repair_screens`, never by adding a prefix — pin the
  lookalike denials (`/admin/accounts`, `/admin/account/`) in the same test.
- `admin_auth._validar_payload` only verifies `admin/medico/admisionista/
  reportes` tokens: a "universal" screen's role matrix asserts 200 for those
  four and screen-equals-API for `operador`/`marca` (both 401 today).
- Never write the literal `data-nav="cuenta"` anywhere in `base.html` outside
  the sidebar loop (CSS selectors included) — it breaks every screen's
  sentinel-nav test at once.
- A direct-shell comment in a Jinja template must not name a forbidden
  asset (`admin-app.js`) — the Node contract greps the template source.
