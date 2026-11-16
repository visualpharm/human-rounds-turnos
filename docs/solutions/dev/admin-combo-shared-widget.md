# The admin combo box is one shared module, not a piece of admin-app.js

**Date:** 2026-08-21; ESM cutover 2026-09-04; platform placement 2026-09-05 · **Area:** admin direct screens · **Files:** `static/platform/admin-combo.mjs`, `admin-app.js`, `admin-content.js`, `admin-patients.js`, `core/static_fingerprint.py`, `test/admin-combo.test.mjs`

## Problem

The typeable combo (`.cmb` / `.cmb-in` / `.cmb-list`: "Derivar a", "Médico", the
doctor's per-slot service, the Pacientes doctor filter) was defined inside
`admin-app.js` — markup (`comboHtml`), filter, keyboard, pointer and focus
delegation, all in the legacy queue controller. `admin-content.js` and
`admin-patients.js` originally reached it through `window.comboHtml`, which only
existed when admin-app.js had loaded. That coupling was first separated into a
classic shared file, then physically retired in the ESM cutover below.

## Solution

`static/platform/admin-combo.mjs` is the ONLY implementation. Its named exports are `html`,
`register`, `parts`, `filter`, `open`, `pick`, and `close`. It publishes no
browser global and has no CommonJS or classic-script twin. What used to be two
hard-coded class checks inside the widget is a per-hidden-class policy registry:

- `Combo.register('med-sel', {freeText: true})` — typed text that matches
  no item becomes the value (the queue's "Médico"). Unregistered classes are
  catalog-only: free text reverts to the last valid display.
- `Combo.register('svc-sel', {emptyHtml: q => …})` — the queue's "the
  hospital does not do this study" message; the hook returning `''` falls back
  to the shared "Sin resultados" (same `admin.queue.solicitudes.sin_resultados`
  key as before).

admin-app.js keeps those two registrations and the `noSeRealiza` data, and
calls `Combo.html(...)`; admin-content.js calls `combo().html(...)`, where
`combo()` returns the imported module or throws.

## Load order and fail-visible contract

Each consumer resolves `platform/admin-combo.mjs` through `window.SANA_STATIC` and awaits
that promise before its first render. Templates no longer load a classic combo
script. The module needs nothing at load time;
`window.SanaSearch` (stemming filter) and `window.SanaI18n` are read at call
time and are optional.

A consumer that needs the combo **fails loudly** when the module import rejects;
admin-content.js also throws if the doctor editor is called before readiness.
None paints a `<select>` or a private copy. `test/admin-combo.test.mjs`
pins the boundary, the behavior on a hand-rolled DOM (no jsdom in this repo),
"exactly one file implements the markup/filter/listeners", the registry entry,
no globals, and all three readiness gates.

## Gotchas

- A rejected module import is a boot failure, not permission to restore a
  compatibility global or silently render a different control.
- `account-form.js` has its own patient-site `comboHtml(id, placeholder, valor)`
  — a different widget (prepaga / localidad), different signature, not `.cmb`.
