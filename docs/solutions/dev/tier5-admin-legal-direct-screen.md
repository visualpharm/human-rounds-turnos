---
title: Tier 5 direct legal screen — admin-only twin of #legal with client-owned ?doc= state
scope: repo
area: dev
tags: [tier5, admin, legal, routing, i18n, history]
created: 2026-08-21
refs: [tier5-admin-account-direct-screen.md, tier5-admin-content-direct-screens.md, tier5-admin-screen-role-gates-y-static-fn.md]
status: active
---

## Problem

After `/admin/account`, four admin destinations were still hash-only (`legal`,
`configuracion`, `hsi`, `plan`), and Tier 5's end-state says the legacy hash
shell cannot retire until every enabled screen has a direct-URL twin. Not all
four are equal: `plan` is Pinamar-only static HTML deliberately stripped from
generic installs, `configuracion` spans three owners (turnos, chat, platform)
and needs a module-degradation design first, and `hsi`'s renderer still lives
inside the 3k-line global controller behind a runtime-conditional nav. The
legal editor ("Términos y privacidad") is the one that is platform-core,
generic, admin-only, self-contained and already API-native — a pure screen
slice with zero new contracts.

## Solution

`/admin/legal` (`admin_panel/admin_legal_screen.py`, `templates/admin/legal.html`)
mounts in `routers/platform.py` right after the account screen and registers
in `core.app_modules._PLATFORM_ADMIN_SCREEN_PATHS["legal"]`, which is what
flips the shared sidebar href on every screen. The gate is the data API's own
gate copied verbatim — `deps.require_admin(solo_admin=True)` for the screen,
`admin_auth.require_admin(handler, solo_admin=True)` in `patient_web/legal.py`
— and the Python contract proves `screen.status == api.status` for every role,
not just "200 ⇔ 200". No module gate (platform-core), no document read during
the render, and NOT an expired-2FA repair surface: the locked session gets the
same `2fa_requerido` 403 as every other `/admin/*`.

`admin-legal.js` gained a direct branch keyed on
`window.SANA_ADMIN_SCREEN === "legal"`, the prompts shape: `legalHref(doc)` is
`/admin/legal?doc=<id>` direct / `#legal/<id>` legacy (English path, English
query key, ids are the wire values `terminos`/`privacidad`); `directState()`
reads `?doc=` and replaceState-canonicalizes any other query/hash; cards, the
crumb and the error link carry `data-legal-doc` (direct only) and ONE delegated
click handler on `#sec-legal` does `pushState` + `route()` for plain left clicks
(modified clicks keep the browser's open-in-new-tab on the real href); `popstate`
re-routes; the legacy branch of `route()` (hash parse, `#app` gate,
`otras("legal")`, `#adm-sub`) is byte-for-byte what it was. The session
boundary stays local: a 401/403 from the API writes
`admin.content.legal.session_expired` plus a link to `/admin` into
`#legal-status` (no `window.showGate`), and the save toast goes there too.
`ITEMS` became a null-prototype map with a `hasOwnProperty` lookup so a
user-controlled `?doc=__proto__` never renders `Object.prototype` as a document.
One behaviour-neutral a11y addition in both modes: the textarea carries
`aria-label` = document title.

Truth updates that must travel with the route: `test/test_admin_screen_nav.py`
(consumer + six golden rows `/admin#legal` → `/admin/legal`),
`test/test_legacy_fallback_census.py` (composition 282→283, native 569→570,
unique 386→387), `test/tier4_composition_inventory.json` regenerated with
`python3 test/test_app_composition.py --write-fixture` (one new GET row), and
the nineteen-screens / three-hash-only wording in `CLAUDE.md`,
`docs/modules.md`, the readiness plan and `admin-panel-y-auth.md`.

## How to apply

- Pick the next direct slice by executability, not by list order: a generic,
  admin-only, self-contained controller with a native API is a screen slice;
  anything whose renderer lives in `admin-app.js`, whose nav is
  runtime-conditional, or whose content is reference-deployment-only needs an
  extraction or a design doc first (the `hsi`/`configuracion`/`plan`
  reasoning lives in the plan that produced this slice).
- Copy the data API's gate, never infer it, and prove status-equality per role
  (`screen.status_code == api.status_code` for admin/operador/medico/
  admisionista/reportes/marca and no session).
- A user-controlled query id that indexes a client-side map needs a
  null-prototype map + `hasOwnProperty` — the legacy hash had the same hole.
- A doc-id query key is per-screen English (`?doc=`, `?prompt=`, `?item=`,
  `?setup=`) — never reuse another screen's key for a different noun, and
  never put translated text in it.
- Regenerate `tier4_composition_inventory.json` with the fixture writer, never
  by hand, and bump the census counts in the same commit.
