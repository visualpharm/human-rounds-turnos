---
title: "Client-loaded assets need a static manifest, fail-closed"
scope: repo
area: deploy
tags: [cache-busting, fingerprinting, static-assets, vbump, manifest]
created: 2026-08-20
refs: [core/static_fingerprint.py, test/test_static_manifest.py, test/static-manifest-loaders.test.mjs, test/test_listado_consistencia.py]
status: active
---

## Problem
After two fingerprinting batches (2026-08-08, 2026-08-10/19) 62 first-party
JS/CSS assets were content-hashed, yet 16 live `?v=` references in 8 files
refused to go away, and the `?v=` doctrine (`deploy.md`, `CLAUDE.md`, the
`vbump-drift-guard.py` hook) had no exit criteria — "stays authoritative for
everything NOT in MIGRATED" could be true forever.

## Current state (2026-08-21)

This historical record predates the final intake-module closure. All
first-party JS/CSS/MJS is now fingerprinted: platform assets use `MIGRATED`,
module assets use exact `{path, fingerprint: true}` manifest objects, and the
repository source invariant rejects manual first-party `?v=` references. The
hook and allowlist described below are retired historical mechanisms.

## Root cause
Every surviving holdout was an asset that a **static JS file loads by URL at
runtime**: `lab-results.js` → `listing.js`; `my-requests.js` / `chat-widget.js`
→ `directory.js`, `account-form.js`; `account-form.js` /
- `platform/admin-create-appointment.mjs` → `dni-barcode.mjs`, `dni-scan.mjs` (dynamic
`import()`); and `dni-barcode.js`'s own static `import … from
'/dni-scan.js?v=1'`. A served file cannot compute a content hash, so a
per-asset `url()` swap at the server can't reach those references — the
module's own comment called it "a design extension, not a per-asset swap".
(`offer.js` was the one genuine leftover per-asset swap.)

## Solution
1. **A closed cohort + a published map.** `static_fingerprint.CLIENT_LOADED`
   (subset of `MIGRATED`); `manifest()` = `{name: url(name)}`;
   `manifest_script()` = `<script>window.SANA_STATIC={…};</script>` — one
   tag per page, in `<head>`, before any deferred loader runs. Reaches every
   HTML pipeline the repo has: `manifest_script()` in `site_shell._shell()`,
   the reserved `{{static.manifest}}` token in `render()` (admin.html;
   extension-less, so the URL placeholder regex can't collide), and the Jinja
   global `{{ static_manifest() }}` (six `templates/*.html` +
   `templates/admin/base.html`). Rule: **a page that emits any loader emits
   the manifest; a page that emits no loader may skip it.**
2. **Each loader resolves through the map, fail-closed.** A four-line
   `staticUrl(name)` per file (the `islands.mjs` `resolveUrl()` shape): the
   URL or `null` + `console.error`, never a hardcoded fallback. `<script>`
   loaders call `cb()` on `null` (the `onerror` path); `import()` sites return
   a rejected promise (the `.catch` paths that already existed — OCR tier 2,
   "No se pudo leer el documento"). `dni-barcode.js`'s static import became a
   top-level dynamic import awaited inside `readDni()`, with a no-op `.catch`
   so a missing entry is reported by the callers, not as an unhandled
   rejection at module evaluation.
3. **The `?v=` doctrine became an exact, shrinking allowlist.**
   `test_listado_consistencia.py::TestVersionesConsistentes` scans production
   source recursively and asserts the `?v=` asset set EQUALS
   `MANUAL_VERSIONED_ALLOWLIST = {entrevista.js, entrevista.css}`; the hook
   scans the same tree. Written exit criteria: empty allowlist ⇒ delete the
   test class, the hook and the doctrine paragraphs.

## Gotchas met on the way
- The old `?v=` gate (test + hook) globbed **root-level** `*.html|*.py|*.js`
  only — blind to every Python renderer in a package (`patient_web/`,
  `modules/`, `templates/`), i.e. blind to the only file still emitting `?v=`.
- A script-order pin that searched the **bare URL** broke the moment the
  manifest carried the same URL in `<head>` (first occurrence moved); pin the
  `<script src="…">` tag instead.
- A recursive `?v=` scan trips on a **doc comment** (`/nav.js?v=65` in the
  engine's docstring, the old static import in a loader's comment): write
  examples as `?v=N` (the gate only counts numeric versions) and keep literal
  old-shape strings out of comments in the loader files.
- Console messages are developer-facing and grep-able — one English shape
  across all six loaders (`[static] <name> is not in window.SANA_STATIC`).

## What proved it
`test_cache_headers.py::TestCacheControlFingerprintedClientLoaded` (serving +
manifest shape + render token), `test_static_manifest.py` (loader ⇒ manifest,
source and rendered — home, about/privacy/terms, account, account-reset, every
`_shell()` chrome), `test_admin_static_fingerprint_rendering.py` (cohort,
legacy admin shell, wizard), `test_admin_screen_contract.py`
(`/admin/solicitudes`), `test_native_terminal.py` (both transports, stale
twin), `test_listado_consistencia.py` (the allowlist),
`static-manifest-loaders.test.mjs` + `admin-crear-turno.test.mjs` (loaders).
`scripts/smoke_prod.py` verifies the manifest on the live home page.
