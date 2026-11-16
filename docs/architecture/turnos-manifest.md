# `app/modules/turnos/module.json`: field notes

The `turnos` manifest used to carry long `*_note` prose fields next to the data they describe. Nothing reads
them (`app/core/app_modules.py` copies unknown keys through untouched; `tests/test_module_manifest.py` checks the
structure, never the prose). The notes live here and the manifest carries none (`test_module_manifest`
`TestManifestFieldsAreRead` fails on a field the loader does not read); the same arrangement for `chat` and `intake` is in
[chat-intake-manifests.md](chat-intake-manifests.md). The generic manifest schema and the loader contract are in [docs/modules.md](../modules.md);
the history of which file moved when is in `git log` (Tier 2 batches D-L, 2026-08-06 .. 2026-08-07, and the
handler retirement of 2026-08-30).

## Status

- The manifest describes reality: `tests/test_module_manifest.py` asserts it. Module ownership of routes, tables, admin
  sections and config prefixes is read from it by `app/core/app_modules.py`; `app.py` mounts every `router_attr` router
  through `app_modules.mount_module_routers()`.
- `routes[].module` values are dotted import paths (`modules.turnos.*`, `patient_web.*`, `admin_panel.*`, `ai.*`,
  `cuentas.*`, `connectors.*`), the same shape as `app/modules/intake/module.json`. A package submodule's `__name__` is its
  dotted path, so every `after` anchor that names a module must use the dotted path too.
- `patient_web`, `admin_panel`, `ai`, `cuentas`, `ops` and `connectors` are domain packages, not modules: their route
  modules are referenced from this manifest, but they have no manifest of their own. Chat route modules
  (`chat_api`, `whatsapp_chat`, `voice`, `mcp_api`) live in `app/modules/chat/` with their own manifest.
- Platform core is deliberately NOT listed: accounts/patients (`cuentas.accounts`, `cuentas.auth_renaper`), admin
  auth and shell, config, i18n, data packs, `ai_client`, notifications, DB. The line between core and module is a
  judgment call, not a hard boundary; the allowlists that draw it are the `PLATFORM_*` constants in
  `tests/test_module_manifest.py` (see `docs/modules.md`, "platform-core allowlist").
- Some `after` values name platform-core route modules that never appear as a `routes[].module` entry here
  (`admin_panel.admin_users`, `admin_panel.admin_preferences`, `admin_panel.settings_admin`, `patient_web.branding`,
  `ops.demo_installation`, `cuentas.accounts`, `cuentas.auth_renaper`). They are positions in the retired per-method
  registry (see below) and no longer affect routing.

## Routes

- Shape: `module` plus the exact HTTP `methods` it is registered for. A route with `router_attr` names a FastAPI
  `APIRouter` on the same module (a sibling `<name>_router.py` for most entries); `app.py` mounts it ahead of the
  native terminal. A plain entry (no `router_attr`) is served by the native terminal and acts as ownership and
  enablement metadata: `app_modules.route_enabled()` tells whether the owning module is on.
- `after` on a plain entry is a leftover of the retired per-method registry that `routing.py` used to build from this
  manifest (the original literal GET/POST/PUT/DELETE order); the registry died in the native-migration final wave
  (2026-08-27 .. 2026-08-31) and the anchors are kept as the record of that order. For a `router_attr` entry `after`
  is meaningful: `app_modules.merge_app_routers()` resolves it against another app module's id, to order the routers of
  two modules that claim overlapping paths; an entry with no `after` is appended at the tail, and an unresolved anchor
  also falls to the tail instead of being dropped. A string applies to every method; a dict is per-method.
- Mount order is the routing contract (Starlette matches by registration order: platform routers, then module routers,
  then the terminal last). It is pinned by `tests/tier4_composition_inventory.json` and `tests/test_app_composition.py`
  (regenerate with `python3 tests/snapshots.py --write` after a deliberate routing change). `tests/test_route_order.py`
  pins the splice algorithm; `tests/test_module_manifest.py` asserts that every declared route module imports, that each
  `router_attr` names a real `APIRouter`, and that no route is claimed twice (by two modules or by a module and the
  platform list).
- Not listed here (platform core, always on regardless of module enablement): setup, legacy_urls, demo_installation,
  i18n, accounts, auth_renaper, admin_users, admin_preferences, admin_password_reset, admin_account, passkeys,
  panel_login, settings_admin, site_identity, branding, system_status, app_modules, legal. Their admin/settings routers
  (`*_router.py`) are mounted directly in `app.py`, like `accounts_router.py`.
- Router coverage, by sibling file:
  - patient site: `patient_site_router` serves `/api/services`, `/api/doctors`, `/api/centers`, `/api/directory`,
    `/service/<id>` and `/service/<id>/book` (one `{rest:path}` template), `/api/service-slots/<id>`, `/my-requests`,
    `/request/<id>` and the catalog pages; `accounts_router` also carries the reset/OTP family.
  - booking flow: `booking_requests_router` (patient half: create/list/detail/cancel/reprogram/waitlist, the
    pre-registration referral-AI read and its signed preview; staff half: `GET /admin`, the theme/auth-mode/turnstile
    settings triad, the whole `/api/admin/requests*` review queue, HSI sync, referral read, RENAPER lookup),
    `slot_holds_router` (wizard soft-lock create/release), `appointment_offer_router` (waitlist release-slot email
    click), `appointment_confirm_router` (reminder-email confirm/decline click).
  - admin surfaces: `admin_content_router` (catalog CRUD for centers/services/doctors/preparations/knowledge, incl. the
    two-segment `knowledge/properties` literal), `admin_patients_router` (roster, dependents, clinical-history
    import), `admin_appointments_router` (front-desk walk-in booking and overbooking slots),
    `doctor_appointments_router` (doctor's agenda/stats/day-queue/compatible-services, all GET),
    `doctor_patients_router` (doctor's roster), `reports_router` (Management/Doctors/Patients/AI-costs/SUMAR
    sub-reports, thirteen GET paths).
  - others: `chart_summary_router`, `lab_results_router`, `preparation_router`, `doctor_photos_router`,
    `avatar_sprite_router`, `account_photo_router`, `ai_costs_router`, `ai_health_router`, `ocr_dni_router`,
    `prompts_router`, `searches_router`, `sitemap_router`, `connectors.puco.puco_router` (dotted nested-package
    import), `chat_api_router` (four of five paths; `/api/chat/stream` stays outside the router, see that router's
    docstring for the SSE/buffering reason), `voice_router`, `calendar_feed_router`.
  - `/api/admin/config`, `/whoami`, `/login`, `/logout` are served by `admin_auth_router.py` (platform core, mounted
    directly in `app.py`), not by the booking router. `branding_router.py` excludes `/logo.png` and `/logo`
    (static-fallback chain, see its docstring).
- `modules.turnos.doctor_appointments_router` also serves the permission-scoped `/api/admin/day-queue/calendar` date
  projection and the doctor-only `POST /api/admin/my-appointments/feed/rotate` (a new calendar subscription address; the
  old one stops resolving).
- The plain entries of the eight `modules.turnos.*` route modules (booking_requests, preparation, calendar_feed,
  doctor_appointments, appointment_offer, appointment_confirm, searches, slot_holds) were removed on 2026-08-30 with
  their `handle()` transports; each of those paths is served by its `*_router` sibling, and `after` anchors that
  pointed at a removed entry were re-anchored to that entry's own anchor (rename, not reorder). Plain entries for route
  modules outside `app/modules/turnos/` (patient_web.*, admin_panel.*, ai.*, cuentas.*, connectors.*) remain as ownership
  records.

## Admin sections

- Ids are the hash fragments (`#<section>`) the admin shell dispatches. The three content editors are listed once
  each under their Spanish ids (`servicios`/`centros`/`medicos`, the `#sec-<id>` containers and `data-nav` ids in
  `admin.html`); the English aliases (`#services`/`#centers`/`#doctors`) are a client-side translation in
  `admin-content.js` (`URL_TIPO`), not sections of their own (listing them twice duplicated every sidebar entry once
  the server-rendered nav started reading this list; removed 2026-08-21).
- `roles` is transcribed from `admin.html`'s `applyRol()` visibility matrix, the source of truth for who sees what.
  `app_modules.sections_payload_for_role()` reads it to answer `GET /api/admin/modules` per role. An entry with no
  `roles` is admin-only (`solo_admin=True`); there is no separate "operator" role (`admin_users._ROLES`:
  medico/admisionista/reportes/admin).
- Bare-string entries are v1 shape and are normalized to v2 dicts (`{id, label_key, label, roles, js, screen}`)
  by `app_modules._normalize_manifest()`. `label_key`/`label` exist so the server-rendered sidebar
  (`templates/admin/base.html`) shows real text instead of the bare id.
- Not listed (platform core): usuarios, cuenta, settings, sistema, configuracion, plan, legal. `plan`
  (Pinamar-only reference-deployment metadata) is the one remaining hash-only destination, deliberately.
- A `screen` object makes the section a server-routed page (sibling `*_screen.py`, mounted like any `router_attr`
  entry). Per-screen facts:
  - `cola`, `pacientes`, `mis-turnos`: keep `admin.html`'s static `<script>` tag and static sidebar link;
    `mis-turnos` has `roles: ["medico"]` and `admin_mis_turnos_screen.py` adds an explicit `rol == "medico"` check
    after `deps.require_admin(permitir_medico=True)` (mirrors `doctor_appointments._my_appointments()`'s own 403).
  - `reportes`: `roles: ["admin", "reportes"]`; the data endpoints gate with `require_admin(solo_admin=True,
    permitir_reportes=True)` (the `reportes` role is the one `solo_admin` exemption) and the screen mirrors that exact
    call with no post-check. `admin-reports.js`'s `route()` reads `window.SANA_ADMIN_SCREEN` like the other migrated
    renderers.
  - `solicitudes`: `admin-app.js` IS the Solicitudes controller, so its boot has a `bootDirectScreen()` branch for
    `window.SANA_ADMIN_SCREEN === 'solicitudes'` that drives the same pipeline as the legacy boot (`loadCatalogo` ->
    `SanaPreferences.load` -> `hydrateRequestPrefs` -> `load` -> `syncCrearTurno`). The screen has the same router-level
    `require_module_enabled("turnos")` and `require_admin(permitir_medico=True, permitir_admisionista=True)` as `cola`;
    its template loads `photo-zoom.js` (after `i18n.js`), `admin-preferences.js` and `admin-control-fit.js` (after
    `format.mjs`) in the legacy order.
  - `knowledge`: admin-only, `/admin/knowledge`, item state is the client-owned query `?item=<id>`
    (pushState/popstate); the label is "Wiki".
  - `prompts`: `/admin/prompts`, admin-only, exact `?prompt=<key>` state, loads only i18n plus the prompt controller,
    local status boundary for expired sessions.
  - `servicios`/`centros`/`medicos`: `/admin/services`, `/admin/centers`, `/admin/doctors`; ONE shared module
    (`admin_panel.admin_content_screen`) and ONE template (`templates/admin/content.html`), but THREE distinct
    one-route routers (`services_screen_router`/`centers_screen_router`/`doctors_screen_router`) because the loader
    does not deduplicate a router object: one router named three times would be mounted three times. Admin-only.
    Item state is `?item=<id>` (`?item=new` for the new-item form; replaceState to canonical on load, pushState on
    navigation). The doctors shell also carries the calendar island cohort (import map, `islands.js`,
    `SANA_ISLAND_URLS`, `calendar-grid.mjs`), the typeable combo widget, the weekly schedule editor, the doctor
    account panel and the create-appointment helper.
  - `hsi`: `/admin/hsi`, `admin_panel.admin_hsi_screen.screen_router`, `templates/admin/hsi.html`, admin-only, the
    same gate as the three `/api/admin/hsi-sync*` APIs under the same router-level turnos gate (module off -> 404);
    `TURNOS_SKIP_HSI` changes API content, never route existence. The controller is module-owned `admin-hsi.js`
    (`static_fingerprint.ADMIN_ROOT_JAVASCRIPT`); literals are in `locales/{es,en}/admin.hsi.json`. Direct state is
    the bare path (junk query/hash replaced away, no pushState); a failed or null panel shows one load-error line with
    Retry; the conflicts table stacks into labelled rows at <=600px.
- The `intake` manifest's own notes describe the hash shell retirement of 2026-09-01.

## Nav entries

Primary patient-site nav entries this module owns, with `label_key` (resolved by `i18n.t_or()`, the Spanish `label`
is the fallback; see `GET /api/nav` in `app/core/app_modules.py`) and an explicit `order`. `/account` is platform core and
not listed. `nav.js` still renders from its own hardcoded array; the manifest data is served but is not what `nav.js`
reads (pending migration, see `docs/roadmap/module-contract-design.md`, "Patient nav contract").

## Locales

`booking.json` and `remedy.json` are the only central `locales/{en,es}/*.json` namespaces used exclusively by this
module (`booking.*` by `booking_requests.py` and `preparation.py`, `remedy.*` by `remedies.py`), so they live in
`app/modules/turnos/locales/`. `i18n.py` merges every discovered module's `locales/<lang>/*.json`. Every other central
namespace (admin.srv, mail, upload, account.mine, admin.reports, admin.system, admin.content, admin.queue, ...) is
shared with files this module does not own and stays central.

## Doctor workspace data

Operator `onboarding_completed_at` belongs to the platform-owned `operators` table. Private original/portrait/status
photo bundles live in the runtime photo volume; `TURNOS_PHOTO_AUTO_ENHANCE` uses this module's existing `TURNOS_`
config classification. No original-photo public route and no external AI flow exists for them.

## DB tables

Every `CREATE TABLE` in `db.py` is checked against this list plus the platform allowlist by
`tests/test_module_manifest.py`: each real table must land in exactly one of the two, so a new table cannot go
unclassified. Platform core tables: operators, admin_preferences, admin_reset_tokens, admin_account_security,
passkeys, users, sessions, reset_tokens, otp_codes, otp_verified, catalog, login_panel_events, chat_messages.
`catalog` is the shared JSON-overlay key-value table (config overrides, prompts, and this module's servicios/centros/
medicos overrides all live in it): shared infrastructure, not turnos-owned. `chat_handoffs` and `chat_summaries`
belong to the `chat` manifest.

## Config keys

About 90 env vars share the declared prefixes (see `.env.example`); enumerating them one by one would rot on the next
env addition. `tests/test_module_manifest.py` instead asserts that every `TURNOS_*`/`HSI_*`/`EMR_BRIDGE_*`/`OTP_*`/
`META_WA_*`/`WEBTURNO_*`/`OWNER_*`/`MAILGUN_*`/`MAILJET_*`/`MAIL_*` key referenced anywhere in the codebase (via
`config.get(...)` or `os.environ`) carries one of these prefixes: a cheap drift check, not a full inventory.
