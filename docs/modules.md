# App modules

Phase 6a of the open-source rework (`~/.claude/plans/large-rewrok-before-open-robust-chipmunk.md`), 2026-07-30 (manifest), extended to real runtime dispatch the same day, and to the full v2 module-contract framework 2026-08-05 (`docs/roadmap/module-contract-design.md` — the BINDING design this section folds in; keep that file for the historical rationale/audit, this file for the shipped contract). Human Rounds — Turnos is a platform core (accounts/patients, admin auth+shell, config, i18n, data packs, `ai_client`, notifications, DB) plus vertical feature apps — "modules" — that plug into it. `turnos` (the whole appointment surface) is the first module; `intake` (entrevista previa, phase 8) is the second, already merged — see `docs/architecture/entrevista-previa.md`. This file is where the NEXT module's PR starts.

## What's real today

Module enablement has REAL runtime effect, not just documentation:

- **`app_modules.py` is the loader**, not just the enable/disable engine. `routing.py`'s `GET`/`POST`/`PUT`/`DELETE` registries are no longer literal lists of every route module — they're PLATFORM-CORE literals (order untouched) with every route a `app/modules/<id>/module.json` declares SPLICED in at its manifest-declared position (`app_modules.merge_routes(method, platform_literals)`; Codex monitor review P0 fix, 2026-08-05 — an earlier draft appended every app route after the whole platform block instead, silently changing which handler answers an overlapping path first). A route entry's `after` field names the platform-core module (or another already-positioned route) it must land immediately after — a string for the same anchor on every method the route registers on, a dict when the position genuinely differed by method in the pre-Tier-2 literal lists. A route with no `after` (the default — any brand-new module needs ZERO central wiring) appends at the tail. `tests/test_route_order.py`'s golden-order test proves the EFFECTIVE registries equal the literal pre-Tier-2 sequences (`git show 347a9f7:routing.py`) name-for-name. A module's routes always REGISTER this way, enabled or disabled — `dispatch()` still asks `app_modules.route_enabled(module.__name__)` before calling a handler, exactly as before, so a disabled module's routes are skipped live, no restart, no `routing.py` edit.
- **Discovery never silently drops a broken module.** `list_modules()` gives every `app/modules/<id>/module.json` an entry, even one that's malformed JSON, missing `id`, declares an unknown `requires` target, sits on a dependency cycle, or has a route module that fails to import — `status: enabled | disabled | broken` + a human-readable `error`, surfaced by `manage.py mod list` and `GET /api/admin/modules`. A broken module's routes/nav/admin-sections are treated exactly like a disabled module's: absent. The platform always boots regardless.
- **`enable(id)` / `disable(id)`** are still `config.guardar()`-backed (the `ENABLED_MODULES` key, DB-override-over-env-over-.env precedence, `TURNOS_CONFIG_LOCK` escape hatch, *no restart needed*). New in v2: a `requires` dependency edge — `enable()` refuses unless a required module is both enabled AND HEALTHY-AND-STARTED (not merely named in the config string — Codex monitor review, P1, 2026-08-05); `disable()` refuses if some OTHER currently-enabled module requires it. No cascades, either direction — explicit only, human-readable `ValueError` reason, surfaced by `manage.py` (stderr) and `GET /api/admin/modules` (`requires` field per module). `disable()` still separately refuses to remove the LAST enabled module. `app/modules/intake/module.json` declares the REAL edges (`requires: ["turnos", "chat"]` — chat added 2026-08-07 for `interview_agent.py`'s `modules.chat.chat_language` import, see [chat-intake-manifests.md](architecture/chat-intake-manifests.md), Requires) — `manage.py mod disable turnos` or `chat` is refused while `intake` is enabled.
- **Lifecycle is transactional.** A manifest may declare `"entrypoint": "modules.<id>.app"`, a Python module exposing optional `startup(ctx)` / `shutdown(ctx)`. `startup(ctx)` runs once per enabled AND HEALTHY module at server boot (`server.py`'s `main()`, after all platform init) and again on a live `enable()`; `shutdown(ctx)` runs on a live `disable()` and at server stop. `startup_all()`/`shutdown_all()` run in TOPOLOGICAL/reverse-topological order by `requires` (Codex monitor review, P1, 2026-08-05 — was alphabetical), and a module already reported `broken` by ANY signal (unknown dependency, a cycle, or a prior runtime failure) never gets a startup attempt at all. `ctx` carries `ctx.db`, `ctx.config`, `ctx.register_hook(name, fn)`. Any exception is caught, logged, marks the module `broken`, and rolls back any hook that `startup()` registered before raising (`hooks.unregister_module()`) — a failed startup never leaves a live, callable hook behind. An entrypoint failure never takes the platform down.
- **Hooks (`app_modules.hooks`)** — a tiny named registry: a module's `startup(ctx)` registers a callable (`ctx.register_hook(name, fn)`); platform code calls `app_modules.hooks.call(name, *a, **kw)`, which only invokes the callables whose OWNING module is HEALTHY AND ENABLED right now (Codex monitor review, P1, 2026-08-05: `is_enabled()` alone let a module broken mid-startup keep firing a hook it registered before raising) and swallows any exception a callable raises. `app_modules.hooks.call()` also lazily ensures every enabled module's `startup()` has run at least once (`ensure_started()`), so a hook fires correctly even in a process that never called `server.py`'s `main()` (every test). This is what replaces a platform file's own `app_modules.is_enabled("<id>")` + `from modules.<id> import ...` scatter — the framework commit migrated ONE such site as proof: `reminders.py`'s day-before pre-consult-interview reminder now calls the named hook `"interview_reminder"` instead of importing `notifications`/`modules.intake` directly; `app/modules/intake/app.py`'s `startup()` registers it. The remaining scatter sites migrated the same way in the 2026-08-16 hook-boundary slice — all four PRODUCTION callers (turnos' `booking_requests.py` and `doctor_appointments.py`, admin_panel's `doctor_patients.py`, ai's `chart_summary.py`): every interview integration point those files hand-rolled is now a named hook in `app/modules/intake/hooks.py`'s REGISTRY that `app.py`'s `startup()` registers — all four carried their own `is_enabled` gate + best-effort fallback, but only the two turnos files actually imported `modules.intake` (booking_requests: `intake_admin` + `interview_patient`; doctor_appointments: `intake_admin`); `doctor_patients.py` and `chart_summary.py` read the interview tables straight through `core.db` behind the gate, no intake import at all. The extraction is behavior-preserving, NOT verbatim — the registry's enabled-and-healthy filter took over each caller's own gate, the two duplicated five-field `entrevista` dict literals (day view + «Mis turnos») became one `_estado_publico()` (the QR-sin-turno view's dict adds `nombre`/`hora` and stayed local), and the offer hook receives the doctor doc resolved caller-side (hooks.py's docstring enumerates every delta). No PRODUCTION code outside `app/modules/intake/` imports it anymore; the only remaining importers are dev tooling, by design (`scripts/route_parity_check.py`, which imports every route module to compare Tier 4 twins, and `scripts/seed_demo_all.py`, which reuses intake's analysis to synthesize demo interviews). `tests/test_intake_hook_boundary.py` keeps both halves true: the static dependency direction (no `modules.intake` import, no hand-rolled enablement gate in those four files) and the runtime absence/presence behavior. One cost was accepted knowingly: every `hooks.call()` re-runs `ensure_started() → list_modules() → _raw_manifests()`, an UNCACHED walk of every `module.json` — measured 2026-08-16 at ~2.6 ms per integration point (vs ~0.16 ms for the old inline `is_enabled` gate; ~7 ms pure-registry for `#cola`'s three hook calls per request; the db-backed bodies themselves cost the same as before). Bounded and fine for this load; if it ever matters, the lever is memoizing the manifest walk WITHOUT breaking the live enable/disable re-check contract.
- **`manage.py mod enable|disable|info|list`** is the operator CLI — `info` now also shows `requires`, `entrypoint`, `status`/`error`, and each route's methods.
- **`GET /api/admin/modules`** exposes this data, ROLE-FILTERED (Codex monitor review, P1, 2026-08-05: was `solo_admin=True`, which blocked every non-`admin` panel role outright, including `medico` — the admin shell's own first dispatch needs this fetch too). Any authenticated panel role may call it (`require_admin(handler, permitir_medico=True)`); the full `modules` status/error/requires list (the actual enable/disable-relevant data) is admin-only, while `sections`/`nav_entries` come back filtered to what the CALLER's role may see (`app_modules.sections_payload_for_role()`, reading each `admin_sections` entry's `roles` list).
- **`GET /api/nav`** (NEW, public, platform-core) — every enabled module's `nav_entries`, i18n-resolved for the requester's language (`i18n.active()` / `i18n.t_or()`) and ordered (`order` field, ties by declaration position). `nav.js` doesn't consume this yet (it still renders from its own hardcoded array — a sibling migration, see the design doc's "Patient nav contract"); the endpoint is real and already serves the manifest-derived data today.
- **`i18n.handle()`** (`GET /api/i18n/<lang>.json`, the `?lang=` switch) is platform-core, always on, never gated by module enablement.

Unset `ENABLED_MODULES` (an installation that's never touched this) means every discovered module is enabled — zero behavior change from before this slice existed.

## `module.json` schema (v2)

```json
{
  "id": "intake",
  "name": "Entrevista previa",
  "description": "...",
  "requires": ["turnos"],
  "entrypoint": "modules.intake.app",
  "routes": [
    {"module": "modules.intake.interview_patient", "methods": ["GET", "POST"]}
  ],
  "admin_sections": [
    {"id": "entrevistas", "label_key": null, "label": null, "roles": null, "js": null,
     "screen": {"path": "/admin/entrevistas", "router_attr": "screen_router", "template": "modules/intake/admin_entrevistas.html"}}
  ],
  "nav_entries": [
    {"path": "/doctors", "label": "Médicos", "label_key": "nav.doctors", "order": 10}
  ],
  "assets": [],
  "locales": null,
  "db_tables": ["interviews", "..."],
  "config_keys_prefixes": ["INTAKE_"]
}
```

v1 manifests (bare-string `routes`/`admin_sections`, `{path, label}` `nav_entries`, no `requires`/`entrypoint`/`assets`/`locales`) stay valid — `app_modules._normalize_manifest()` is the ONE place that upgrades them to the v2 shape above; every downstream reader (loader, admin API, CLI) only ever sees v2.

- **`requires`** — module ids this one depends on. `enable()`/`disable()` enforce it (see above); a `requires` cycle marks every member `broken`; a `requires` entry naming an unknown module also marks the module `broken`.
- **`entrypoint`** — optional dotted path to a module exposing `startup(ctx)`/`shutdown(ctx)`. See "Lifecycle" above.
- **`routes`** — v2: `{"module": "<dotted or bare name>", "methods": ["GET", ...]}`. `"module"` is whatever `routing.py`'s dispatch used to import directly (bare, e.g. `"patient_web.site_shell"`, for a module not yet physically moved under `app/modules/<id>/`; dotted, e.g. `"modules.intake.interview_patient"`, for one that is) — `importlib.import_module()` resolves either shape. `tests/test_module_manifest.py` asserts every declared route module actually imports, is registered exactly once (no double-claims across manifests), and that every module `routing.py` actually builds is classified (this manifest or the platform-core allowlist). **`router_attr`** (Tier 4 slice 3, `docs/roadmap/tier4-replatform-design.md`'s "Module contract adapter") — an optional third key naming a `fastapi.APIRouter` attribute on a SEPARATE sibling dotted module (never the SAME module a plain route entry names — see `app/modules/intake/interview_patient_router.py`'s own docstring for why: importing FastAPI from the file the legacy per-method registry already imports unconditionally at process start would leak the dependency into the default TURNOS_ASGI-off deploy path). `app_modules.app_router_entries()`/`mount_module_routers()` mount it in `app.py` (ASGI mode only); `app_modules._validate_routes()`/`_base_app_routes()` (the legacy loader) skip any entry declaring `router_attr` outright. Per-request enablement is `deps.require_module_enabled(id)` on the router, not whether it got mounted (Starlette can't unmount a route at runtime) — mirrors `route_enabled()`'s per-dispatch gate exactly, 404 on disabled, indistinguishable from an unknown path.
- **`admin_sections`** — v2: `{id, label_key, label, roles, js, screen}`. `roles` gates the server-rendered navigation and defaults to admin-only when absent. **`screen`** is the active admin-screen contract: `{path, module, router_attr, template?}` names the direct URL, sibling FastAPI router, and optional Jinja template. `app_modules.mount_module_routers()` mounts the router behind `Depends(require_module_enabled(id))`; a disabled module therefore returns the ordinary 404 without central routing changes. The former hash shell and dynamic module-script loader are retired. A `js` field now declares a fingerprinted asset exposed through the module API; its direct template owns when and how to import a canonical module. `intake`'s `entrevistas` template imports `admin-entrevistas.mjs` and invokes its named `boot()` export.
**Current admin-screen status (2026-08-31):** fifteen manifest-declared
vertical screens plus platform-core `/admin/system`, `/admin/settings`,
`/admin/users`, `/admin/account`, `/admin/legal`, `/admin/appearance`,
`/admin/plan`, and the public `/admin/login` gate now provide twenty-three
direct URLs. The
three catalog editors (`/admin/services`, `/admin/centers`, `/admin/doctors`;
manifest ids `servicios`/`centros`/`medicos`) share one module and template,
`app/admin_panel/admin_content_screen.py` + `templates/admin/content.html`, but
declare three DISTINCT one-route `router_attr` objects — the loader includes
whatever object a `screen` names and never deduplicates, so one shared router
listed three times would mount three times. `/admin/account`
(`app/admin_panel/admin_account_screen.py` + `templates/admin/account.html`) is
the universal staff self-service twin of `#cuenta`: the same
`admin-account.js` controller and account/passkey APIs, admitted for every
role `admin_account._identity` admits, no module gate, and the one
expired-2FA repair surface `auth.admin_auth.require_admin` admits by exact
path (never a prefix). `/admin/legal` (`app/admin_panel/admin_legal_screen.py` +
`templates/admin/legal.html`) is the platform-core, admin-only twin of
`#legal` (terms + privacy): the same `admin-legal.js` controller and
`/api/admin/legal*` API, gated exactly like that API (`solo_admin=True`), no
module gate, state `?doc=<terminos|privacidad>` owned client-side with real
history, and NOT an expired-2FA repair surface. `/admin/appearance`
(`app/admin_panel/admin_appearance_screen.py` + `templates/admin/appearance.html`)
is the platform-core, admin-only twin of `#configuracion` ("Apariencia y
acceso"): the same `admin-theme.js` controller and the same five APIs with
their ownership unchanged — `/api/admin/theme`, `/api/admin/auth-mode`,
`/api/admin/turnstile` stay turnos-routed, `/api/admin/chat-voice` stays
chat-routed, `/api/admin/branding` is platform — gated exactly like those
APIs (`solo_admin=True`), no module gate, so the screen is 200 while a
module-owned block's API is 404. That gap is handled by the controller in
BOTH shells: each GET is fetched with a per-request outcome, a block renders
iff its GET answered 2xx with a payload, a 404 (owning module off) removes
that block silently, any 401/403 is the session boundary (direct: a local
`#configuracion-status` line with a link to `/admin`; legacy: `showGate`),
and any other failure drops the block and shows one reload line — zero
blocks is never a blank page. No sub-state: the bare English path is
canonical (any query or hash is replaced away), a successful theme save
reloads in the direct shell (brand text, fonts and favicon are
server-injected there), and it is NOT an expired-2FA repair surface. `/admin/hsi`
(`app/admin_panel/admin_hsi_screen.py` + `templates/admin/hsi.html`) is the
turnos-owned, admin-only HSI screen. Its manifest entry carries a `screen`
object. The template imports native `static/platform/admin-hsi.mjs` through
its fingerprinted URL; the classic `admin-hsi.js` is physically absent in
this candidate. Each mount owns its requests and listeners and is disposed
on page exit. The three APIs (`GET
/api/admin/hsi-sync`, `POST …/resolver`, `POST …/conflicts`) remain
authoritative. The screen copies their `solo_admin` and turnos-module gate,
so a disabled module returns 404. It makes no server-side HSI probe; its
client controller owns the fetch outcome, session boundary, retry state, and
responsive conflicts table. The bare English path is canonical, and patient
links target `/admin/solicitudes`. No active admin destination is hash-only;
`/admin/plan` is the direct Pinamar reference-deployment metadata screen.

- **`nav_entries`** — v2: `{path, label, label_key, order}`. `label_key` resolves through `i18n.t_or()` on `GET /api/nav`; `label` is both the v1 literal AND the i18n fallback.
- **`assets`** — legacy strings/globs remain declarative ownership metadata. A
  v2 object `{path, fingerprint: true}` declares one exact module-owned
  JS/CSS/MJS file for immutable serving. The loader validates module
  ownership, regular non-symlink disk identity, extension, duplicates, and
  platform-registry overlap before it can enter the static registry.
- **`locales`** — optional path to a module-owned i18n catalog directory. REAL as of the intake pilot (2026-08-05): `i18n.py`'s `_catalog_files()` merges `<locales>/<lang>/*.json` from every discovered module (enabled or disabled — a translation catalog is inert data, gated the same way a route module always registers) AFTER the central `locales/<lang>/` tree, via `app_modules.module_locale_roots()`. `app/modules/intake/locales/{en,es}/intake.json` is the first catalog physically moved out of the central tree; the merge is generic, so a future module's `locales` field works identically with zero central i18n.py change.
- **`db_tables`** — every Postgres table (`CREATE TABLE` in `db.py`) the module owns. Disabling a module does NOT touch its tables or data — only its routes stop dispatching.
- **`config_keys_prefixes`** — env var prefixes, not an exhaustive key list.

Every list above is checked BOTH directions by `tests/test_module_manifest.py`: every entry the manifest claims must actually exist in the real code, AND every real thing (a routing.py-built module, a db.py table, …) must be classified as either this module's or explicitly on the platform-core allowlist. `tests/test_module_runtime.py` is the OTHER half of the gate — it proves the classification has live effect (boots a real socket-bound server, flips `ENABLED_MODULES` with no restart, asserts real HTTP status codes change), not just that the manifest text agrees with the code text.

## What disabling `turnos` actually takes down today

Physical extraction is now partial, so the behavioral boundary does not rely
on pathname alone: the manifest remains authoritative for both moved and
remaining compatibility files. That classification has a real consequence
worth knowing before you flip the switch: **`/admin` itself still lives in
`app/modules/turnos/booking_requests.py`**, which is turnos-owned — so disabling
`turnos` takes the whole legacy admin entry point down too, not just
patient-facing booking. What survives (verified by
`tests/test_module_runtime.py`): `GET /api/i18n/<lang>.json`, `GET
/admin/reset-password`, and every static asset. A disabled module's own route
answers a plain **404**, indistinguishable from a path that never existed.

## The platform-core / module boundary is a judgment call right now

The tree now mixes physically extracted packages with compatibility modules
that have not moved. Directory placement is therefore not the ownership
contract; `module.json`, the platform-core allowlists, and their two-way tests
are. Do not reclassify a route, table, asset, or lifecycle hook from its folder
name alone.

## Admin screens — direct URLs only

The hash admin shell (`admin.html`, `SanaAdminRouter`, and the module-script
loader) was retired on 2026-09-01. Every active admin destination is a
server-routed `/admin/<screen>` page. A module-owned screen declares
`admin_sections[].screen` with its path, sibling router module, router
attribute, and template; `app_modules.mount_module_routers()` mounts that
router under the normal module-enabled dependency. Disabling its module
therefore removes the direct route and its navigation entry without a central
router edit.

`intake`'s `entrevistas` screen declares the canonical
`app/modules/intake/admin-entrevistas.mjs` asset in its manifest. Its direct
template imports the fingerprinted module URL and calls its named `boot()`
export; Storybook calls its named `preview()` export. The classic
`admin-entrevistas.js` producer and its browser globals are retired. The
manifest's `js` field remains the asset descriptor returned by the module API;
it is not a request to load a classic script or revive the retired hash shell.

## How the NEXT module would plug in

`intake` (entrevista previa: Pre-Consult AI chat + triage, ported from the sibling
`<workspace>/sana` project's idea, no shared code) shipped as the second module,
phase 8, 2026-07-31 — see `docs/architecture/entrevista-previa.md` for what it
owns; it's also the first module with a real `entrypoint` (`app/modules/intake/app.py`).
`chat` is now the third module, with its own manifest, lifecycle, routes,
tables, hooks, and WhatsApp/chat ownership. Two more roadmap features already
have design docs in `docs/roadmap/` and are natural candidates for another
vertical module:

- **`teleconsulta`**, **`historia-clinica`** — see their respective `docs/roadmap/` design docs.

The shape a PR adding one of these follows:

1. `app/modules/<id>/module.json` — the v2 schema above, describing what the NEW module owns. `requires: ["turnos"]` if it depends on the appointment surface (most will). If it needs a NEW connector capability (unlikely for these three — they'd mostly reuse `emr_sync`/`identity_verification`), see `docs/connectors.md` instead.
2. The actual feature code — physically under `app/modules/<id>/` from day one (no existing flat-repo-root code to leave in place the way `turnos` did in Phase 6a).
3. **Nothing to edit in `routing.py`.** The manifest's `routes` list IS the registration — `app_modules.route_modules(method)` picks it up automatically once the module directory exists with a valid `module.json`.
4. If the module needs to run background work at boot, or hook into a platform sweep (a reminder, a notification, …), add an `entrypoint` (`app/modules/<id>/app.py`) with `startup(ctx)`/`shutdown(ctx)` instead of having a platform file import the new module directly — register a named hook via `ctx.register_hook(name, fn)` and have the platform call `app_modules.hooks.call(name, ...)`.
5. Extend `tests/test_module_manifest.py`'s pattern (or a sibling test file) for the new module's own manifest-vs-code checks, AND add HTTP-level enable/disable cases to `tests/test_module_runtime.py`'s pattern — a manifest/code-text agreement test alone doesn't prove the new module's routes are actually gated.
6. `manage.py mod list` picks up the new directory automatically (`app_modules.list_modules()` walks `app/modules/*/module.json`, no code change needed). `manage.py mod enable <id>` / `mod disable <id>` work immediately too — no per-module CLI wiring needed.

## Relationship to connectors

Modules and connectors are two DIFFERENT pluggable layers, easy to conflate:

- A **module** is a vertical feature app (a whole product surface: routes + admin UI + DB tables + nav).
- A **connector** is a horizontal integration with ONE external system, implementing ONE of six capabilities (`docs/connectors.md`), usable by any module that needs it.

`turnos` (the module) is currently the only consumer of every connector in the repo — but a future `intake`/`triage` module would plug into the SAME `emr_sync` connector (still HSI) without needing its own EMR integration, which is the whole point of factoring connectors out separately instead of bundling "HSI sync" as part of `turnos`'s own manifest.

Encrypted backup_runs is platform/operations state, alongside backup_journal,
backup_versions, backup_version_tombstones and purged_files (the journal of
files the retention deleted on purpose). It does not belong to a vertical
feature manifest. It is platform-owned (no manifest claims it: `tests/snapshots.json`, section
`owners`); no route was added.
