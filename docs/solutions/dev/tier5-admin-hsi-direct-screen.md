---
title: Tier 5 direct HSI screen — behavior-frozen extraction of the legacy HSI panel into admin-hsi.js
scope: repo
area: dev
tags: [tier5, admin, hsi, extraction, routing, modules, i18n, responsive]
created: 2026-08-21
refs: [tier5-admin-appearance-direct-screen.md, tier5-admin-legal-direct-screen.md, tier5-admin-prompts-direct-screen.md, ../hsi/resolutor-automatico-conflictos.md]
status: active
---

## Problem

After `/admin/appearance`, two admin destinations were hash-only: `hsi` and
`plan`. `plan` is Pinamar-only reference-deployment metadata (excluded by
design). `hsi` — the one engine-owned destination left with operator value —
could not get a direct screen as-is: its whole section (`HSI_PANEL`,
`loadHsiSync`, `hsiCuando`, `hsiAplicar`, `renderHsiSec`, `routeHsi`) lived
inside the 3,066-line `admin-app.js`, wired to that controller's `api()`,
`toast()`, `fmtTurno()`, `CURRENT_ROLE`, `$()` and `esc()`. A direct shell
that loads admin-app.js just to paint one section is the anti-pattern every
prior slice avoided. The legacy section also left "Cargando…" on screen
forever when the GET failed or the resolver answered `panel: null`, and its
strings were hard-coded Spanish.

## Solution

**Extraction first, screen second.** `admin-hsi.js` (root-level, in
`static_fingerprint.ADMIN_ROOT_JAVASCRIPT`; each screen imports canonical
`dates.mjs` through `window.SANA_STATIC` before its first render) carries the section verbatim in behavior:
the same five globals exported on `window` (admin-app.js's `load()` still
calls `loadHsiSync(); routeHsi();` after its direct-screen guard), the same
`SanaAdminRouter.register("hsi", …)`, hashchange listener, `#nav-hsi`
visibility + badge, the `data-hsi-request` deep-link markup (the click
handler stays in admin-app.js) and the rejection `confirm`. It owns its
helpers instead of borrowing them: the canonical `T()` resolver, `esc`,
`$`/`$$`, `fetchOutcome(path, opts) → {ok, status, json}`, person names through
`SanaFormat.nombre`, dates through the named `dates.mjs`
`corta`/`relativo` exports (the same reading `fmtTurno` gave), and
status/toast/session handling (`window.toast`/`window.showGate` in the legacy
shell, `#hsi-status` in the direct one). The desktop CSS stays in the two
shells' shared stylesheet (the css-drift guard pins admin.html and base.html
equal); the module injects only what the legacy sheet never had — 16px/44px
controls and the <=600px stacked table.

`/admin/hsi` (`admin_panel/admin_hsi_screen.py`, `templates/admin/hsi.html`)
is the manifest-declared screen: the turnos `hsi` entry became a v2 dict
(`admin.shell.nav.hsi` / `HSI`, `screen`), so the shared sidebar href flips
from `/admin#hsi` on every screen. The gate is the APIs' own
(`solo_admin` under the router-level turnos gate): proven status-for-status
against `GET /api/admin/hsi-sync` for every role in both module states
(turnos off → 404 for both). `TURNOS_SKIP_HSI` changes the API's
`hsi_activo`, never route existence (screen byte-identical under both
values). The render calls no HSI API, resolver, connector, DB or socket (all
patched to raise) and echoes no configuration value. Not an expired-2FA
repair surface. Lookalikes and the trailing slash stay closed.

Client contract, both shells: every request is one outcome; 401/403 is the
session boundary (direct: one local line + link to `/admin`; legacy:
`showGate`; the demo's read-only 403 stays a message); every failure
re-enables the controls before saying anything; a failed GET or a `panel:
null` answer on-section draws ONE `admin.hsi.load_error` line with a Retry
button (the section's own quiet control) — never an eternal «Cargando…» —
while the off-section badge refresh stays silent exactly as before. Direct
state is the bare path (`replaceState` away any query/hash, no
pushState/popstate); direct patient links go to `/admin/solicitudes` with no
query state; the legacy shell keeps `#solicitudes` + `data-hsi-request`. UI
literals moved to `locales/{es,en}/admin.hsi.json` (one namespace per file,
en mirrors es key for key, every fallback is the es literal — pinned by the
Node contract). Unknown server error codes are never shown as machine values;
the controller uses the localized human fallback.

Responsive: at <=600px the 6-column conflicts table becomes labelled
full-bleed rows (`td::before{content:attr(data-label)}`, the patient cell
first and bold as the row heading, `thead` visually hidden only, the row's
one hairline is the table's own divider moved from cell to row — nothing
added), the alternatives select is 16px/44px, the recommended button and
«Volver a revisar»/Retry are 44px. `#hsi-status` is `role=status`
`aria-live=polite`; the load-error line is a polite status too.

Truth updates that travel with the route: `test/test_admin_screen_nav.py`
(consumer, SCREENS row, four golden rows `/admin#hsi`/`hsi` →
`/admin/hsi`/`HSI`), `test/test_legacy_fallback_census.py` (composition
284→285, native 571→572, unique 388→389), `test/tier4_composition_inventory.json`
regenerated with `python3 test/test_app_composition.py --write-fixture`,
`test/test_cache_headers.py` (30→31 root admin assets),
`test/admin_legacy_source.py` (admin-hsi.js joins the logical legacy source
so the usability suite keeps reading the section), the twenty→twenty-one /
two→one hash-only wording in `CLAUDE.md`, `docs/modules.md`, the readiness
plan, `admin-panel-y-auth.md` and `hsi.md`.

## How to apply

- Extract before you route: a section welded to the monolith controller gets
  its own module with its own helpers FIRST (same globals, same registration,
  same DOM), then the direct screen loads only that module.
- Keep the legacy shell byte-compatible by exporting the old globals on
  `window` and leaving the monolith's calls in place; move nothing the
  monolith still owns (the deep-link click handler stayed).
- One outcome helper per controller; classify 404/401-403/other once; never
  let a null payload leave "Loading" on screen — give the line an action.
- A badge-only refresh may fail silently; the same request on-section must
  show the failure. Key the difference on "is the section visible", not on
  which shell loaded the file.
- Desktop CSS that the css-drift guard pins in BOTH shells stays there; the
  module injects only additive mobile/touch rules.
