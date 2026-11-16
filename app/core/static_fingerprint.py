"""static_fingerprint.py — content-hash fingerprinted URLs for JS/CSS assets,
Tier 4 slice 6 (docs/roadmap/tier4-replatform-design.md, "Static
fingerprinting"). All first-party served JS/CSS/MJS assets are now either
platform entries in ``MIGRATED`` or exact module manifest entries rendered by
``module_url()``. New production references must never use manual ``?v=``.

Mechanism (matches the design's own prose almost verbatim): the SHA-256 of an
asset's bytes, first 8 hex chars, becomes part of its URL —
`/static/nav.<hash8>.js` instead of `/nav.js?v=N`. The hash is computed
lazily and cached against the platform file mtime or the full stable module-FD
identity (same "recompute at first-use,
never a separate build step" convention `i18n.py`'s `_load_catalog` already
uses for its own mtime-keyed cache) — this repo's Coolify deploy rebuilds the
container on every push, so "cached for the process's lifetime" and "computed
once per deploy" are the same thing in practice. Because the URL itself
changes the instant the content does, the response is `public, max-age=
31536000, immutable` — Cloudflare's 4h TTL (docs/architecture/deploy.md)
stops mattering for a migrated asset: there is nothing to invalidate, the old
URL simply stops being linked from anywhere.

Two callers, matching the two shapes HTML gets built in this repo:

  - Plain Python string/f-string builders (site_shell.py's `_shell()`,
    server.py's `_public_shell()`/`_render_*_page()`, admin_password_reset.py,
    setup.py, modules/intake/interview_patient.py) call `url(name)` directly
    as a Python expression — no new template syntax needed, they already
    interpolate `theme.brand_name()` etc. the same way.
  - Plain-text templates that still go through the existing
    `{{t.*}}`/`{{sitio.*}}` regex-substitution pipeline (including the legacy
    admin shell served by booking_requests.py) get a
    THIRD sibling placeholder, `{{static.<name>}}`, resolved by `render()`
    below — same shape as `i18n.render()`'s `{{t.*}}` and
    `site_identity.render()`'s `{{sitio.*}}`, wired into the SAME call sites
    (search `static_fingerprint.render(` at each `i18n.render(` call site).
  - Jinja2 pages and partials get a THIRD global,
    `static`, bound directly to `url()` — a template writes
    `{{ static('nav.js') }}`, exactly the design doc's own example, no
    wrapper needed since a fingerprinted URL is already attribute-safe
    (`[a-zA-Z0-9_.-]` only, nothing to HTML-escape). The shared
    `templates/_site_footer.html` partial is included by `index.html` and
    rendered for dynamic shells by `site_shell._site_footer()` through
    this same Jinja environment.

Serving side: `transport_target()` resolves the combined immutable registry: platform
names in `MIGRATED` and exact validated module asset names from
`app_modules.module_asset_paths()`. It recognizes `/static/<base>.<hash8>.
<ext>`, and ONLY answers when the registered name's current content hash
matches the requested URL. A stale or forged hash resolves to no target, so
the native terminal returns its ordinary no-store 404.

**One transport, one resolver**: the
URL-to-file decision lives ONLY in the platform/module immutable registry +
`_resolve()` below. The
native ASGI composition reaches it through `transport_target()`, consumed by
`routers/native_terminal.py`'s catch-all terminal (mounted last in
`app.py`), which hands the resolved physical path, content type and cache
policy to the shared hardened static resolver in `services/static_assets.py`
(disk I/O, 304s). Adding a platform or validated module registry name needs
no second transport path.

**Slice 6, batch 3 — the client manifest (2026-08-20)**: the last
first-party JS/CSS holdouts were assets that a STATIC JS file loads by URL
at runtime — and a served file cannot compute a fingerprint. Those names
now live in `CLIENT_LOADED` (a subset of MIGRATED); `manifest()` is their
`{name: url(name)}` map and `manifest_script()` the inline
`<script>window.SANA_STATIC={...};</script>` a shell emits ONCE in `<head>`,
before any deferred loader can run — through `manifest_script()` in the
f-string shells (`site_shell._shell()`), the reserved `{{static.manifest}}`
token in the renderer, or the Jinja global
`{{ static_manifest() }}` (`templates/*.html`, `templates/admin/base.html`).
Each loader (`lab-results.js`, `my-requests.js`, `platform/chat-widget.mjs`,
`platform/account-form.mjs`, `platform/admin-create-appointment.mjs`, `dni-barcode.mjs`) resolves
its target through `window.SANA_STATIC` with a tiny local resolver (the
`islands.mjs` `resolveUrl()` shape) and FAILS CLOSED — `null`/a rejected
import on a missing entry, never a hardcoded fallback URL — so a page that
forgot the manifest degrades through the exact path a failed network load
already takes today. The rule that follows: **a page that emits any loader
emits the manifest; a page that emits no loader may skip it** (the `embed=1`
interview document, for one). Every loader is itself MIGRATED, so editing one
changes its own hash — nothing to bump anywhere. The residual first-party
`?v=` inventory is now forbidden by
`tests/test_listado_consistencia.py`'s first-party source invariant.

**Tier 5 slice 2 growth** (docs/roadmap/tier5-frontend-design.md, "Fingerprinting
grown to cover .mjs facades and vendored JS"): three additive changes, no new
route, no new module —
  1. `MIGRATED` takes its first `.mjs` entry, `format.mjs` (Tier 5 slice 0's
     ESM facade). `url()`/`_resolve()` no longer split a name on its LAST
     dot (`nav.js` -> base `nav`, ext `js`) — they split the *basename* on
     its FIRST dot instead (stem + suffix), which is a no-op for every
     existing single-dot name (`nav.js`, `human-rounds.css`, `format.mjs`
     all have exactly one dot) but is what makes a future vendor name with
     a compound suffix — `preact.module.js` — hash correctly as
     `preact.<hash8>.module.js` and not `preact.module.<hash8>.js`, matching
     the exact URL shape the design's islands section names for vendored
     Preact/HTM (`/static/vendor/preact.<hash8>.module.js`).
  2. **Vendor path prepared, not yet populated**: a `MIGRATED` name starting
     with `vendor/` resolves against the REAL `static/vendor/` directory
     (`static/vendor/preact.module.js` on disk, matching where slice 3
     commits the vendored files) instead of repo root — every other name
     keeps resolving at repo root exactly as before. `_resolve()` is
     rewritten to recompute `url(name)` for each `MIGRATED` candidate and
     compare it to the request path verbatim (instead of a regex that would
     need hand-updating for every new suffix shape) — this is what lets the
     vendor directory + compound-suffix shape above fall out for free with
     no new parsing code once slice 3 actually adds a `vendor/...` entry.
     No vendor entry is added to `MIGRATED` by slice 2 itself — there is no
     file yet to hash, so `static/vendor/` ships this slice as an empty,
     `.gitkeep`-held directory only.
  3. **Formatter follow-up (2026-09-04)**: `format.mjs` is now the physical
     canonical implementation. The classic `format.js`, its browser global,
     and its unhashed passthrough were deleted; consumers import the current
     `format.mjs` URL from `window.SANA_STATIC`.

**Tier 5 slice 3 growth** ("Interactive screens as Preact+HTM islands"):
populates what slice 2 prepared, plus the one generalization slice 2 didn't
need yet —
  1. `vendor/` now holds real files: `vendor/preact.module.js`,
     `vendor/hooks.module.js`, `vendor/htm.module.js`,
     `vendor/htm-preact.module.js` (provenance/hashes in
     docs/roadmap/tier5-frontend-design.md's slice 3 addendum). Each is a
     `MIGRATED` entry now, hashing to the exact `vendor/<name>.<hash8>.module.js`
     shape slice 2 already built `url()`/`_resolve()` to produce.
  2. `_physical_path`'s vendor-only prefix check grows a SECOND nested
     bucket, `islands/`, resolving against `static/islands/` — the pilot
     island (`static/islands/MedCal.mjs`, the Calendario tab's month grid)
     needs the exact `/static/islands/<Name>.<hash8>.mjs` shape the design's
     own islands-bootstrap section names, and `islands/` is mechanically the
     same "nested under static/, not a root-level bucket" shape `vendor/`
     already established — no new resolution logic, one more prefix.
  3. `_PLACEHOLDER_RE` grows to allow a `/` inside the captured name — the
     pilot import map (preact/hooks/htm/htm-preact bare specifiers +
     `sana/calendar-grid`) is built from `{{static.*}}`
     tokens like `{{static.vendor/preact.module.js}}` and
     `{{static.islands/MedCal.mjs}}`, which the old flat-name-only regex
     couldn't match. `render()`'s behavior is unchanged for every existing
     flat-name token.
  4. `calendar-grid.mjs` (the ESM module the pilot island imports through the
     `sana/calendar-grid` bare specifier) joins `MIGRATED`, and
     the former calendar UMD originally joined `_PASSTHROUGH` while its ESM
     sibling was still a facade.
     (Follow-up slice, 2026-09-04: calendar-grid.mjs stopped being a facade
     and became the canonical module — the UMD calendar implementation and its
     global were deleted, its `_PASSTHROUGH` entry went with it, and the
     module joined `CLIENT_LOADED`.)
"""
import hashlib
import json
import os
import re
import stat

from app.core import paths

_ROOT = paths.PROJECT_ROOT

# The complete root-level admin JavaScript cohort. Keep this separate from
# MIGRATED so its closed boundary stays mechanically verifiable. Module-owned
# `modules/intake/admin-entrevistas.mjs` is fingerprinted through the module
# registry, never added to this platform-core cohort. CLIENT_LOADED names
# may be nested under `platform/` (2026-09-05 admin-primitives placement).
ADMIN_ROOT_JAVASCRIPT = frozenset({
    "admin-app.js",
    "admin-shell.js",
})

# No first-party non-admin classic utility remains in the admin shell. The
# passkey client joined CLIENT_LOADED when its classic global was retired.
ADMIN_SHELL_UTILITIES = frozenset()

# Slice 6, batch 3 (2026-08-20) — the assets some STATIC JS file loads by
# URL at runtime: platform/listing.mjs (lab-results.js and upload-order),
# platform/directory.mjs + platform/account-form.mjs (my-requests.js, platform/chat-widget.mjs,
# service.js and platform/dependents.mjs), platform/dni-barcode.mjs + platform/dni-scan.mjs (dynamic
# `import()` from platform/account-form.mjs / platform/admin-create-appointment.mjs, and
# dni-barcode.mjs's own import of dni-scan.mjs). A served file cannot compute a
# fingerprint, so the SHELL publishes this cohort's CURRENT URLs to the
# browser as `window.SANA_STATIC` (`manifest()` / `manifest_script()` below)
# and each loader resolves through that map — fail-closed, never a hardcoded
# fallback URL. Kept separate from MIGRATED (which it is a subset of) so the
# published map is exactly this closed set, mechanically verifiable, and so a
# loader's target can never silently drift back to `?v=`. Every loader file
# is itself MIGRATED: editing one changes its own hash, nothing to bump.
#
# Audit row 4.1 (2026-09-04) adds `platform/age.mjs`: the canonical age-routing ES
# module (the UMD and its browser global are gone). Loaded by
# dynamic `import()` from service.js (the wizard's and the walk-in gate's
# boot) and from the home page's inline script — both fail-open on a failed
# load, so a missing entry degrades through the exact path a failed network
# load already takes.
#
# The same day adds `platform/calendar-grid.mjs` (the canonical monthly-grid
# ES module; the former UMD calendar implementation and browser global are
# gone): dynamic `import()` from service.js (the wizard's "Otras fechas"
# grid and its css) and admin-content.js (the calendar css) — both fail-open,
# and the MedCal island imports the same module through the
# `sana/calendar-grid` import-map entry.
#
# 2026-09-05, rows 1.1/4.1: the five already-native shared primitives move
# from repo root into static/platform. Manifest keys, fingerprint URLs and
# Node imports all use the platform/ names; the old root files are gone.
# Same day, the seven already-native admin primitives follow: session,
# preferences, table, combo, filter-controls, control-fit and
# patient-summary. control-fit is MIGRATED (script-tag) not CLIENT_LOADED.
# The same placement slice moves directory.mjs the same day (its root twin
# is gone too; the manifest key is platform/directory.mjs).
CLIENT_LOADED = frozenset({
    "platform/backup-key.mjs",
    "platform/nav.mjs",
    "platform/account-requests.mjs", "platform/account-labs.mjs",
    "platform/directory.mjs",
    "platform/dni-barcode.mjs", "platform/dni-scan.mjs", "platform/age.mjs", "platform/calendar-grid.mjs",
    "platform/hours.mjs", "platform/dates.mjs", "platform/format.mjs",
    "platform/prep.mjs", "platform/islands.mjs",
    "platform/turnstile.mjs", "platform/passkeys.mjs", "platform/admin-combo.mjs",
    "platform/admin-patient-summary.mjs", "platform/admin-preferences.mjs",
    "platform/admin-filter-controls.mjs",
    # 2026-10-27: the attendance forecast («Historial» bars + its Information
    # explanation), shared by «Mis turnos» and «Pacientes».
    "platform/attendance-forecast.mjs",
    "platform/admin-session.mjs", "platform/admin-table.mjs",
    "platform/admin-prompts.mjs", "platform/admin-legal.mjs",
    "platform/admin-knowledge.mjs",
    # 2026-10-27: what symptom intake is, told to the doctor — one block for
    # the doctor's Síntomas tab, Sistema › Captura de síntomas and the
    # doctor's own «Pacientes de hoy» header.
    "platform/intake-explainer.mjs",
    # 2026-11-07: the head of every printed sheet (screen, filters, print
    # time) — nav.mjs fetches it on the patient site, the panel base links it.
    "platform/print-head.mjs",
    # 2026-11-13: the anonymous analytics beacon, fetched by nav.mjs only when window.SANA_EV is set.
    "platform/analytics.mjs",
    # 2026-09-05, the i18n cutover: every consumer that renders translated
    # text dynamic-imports the canonical module through this manifest (the
    # sanaImportStatic/SANA_STATIC pair), awaits it at its boot/render
    # boundary, and the classic root i18n.js + window.SanaI18n are retired.
    # Fail-closed on a missing entry, like every loader above.
    "platform/i18n.mjs",
    # The public weekly schedule renderer awaits both i18n and hours before
    # mounting data-semana elements. Every patient shell publishes this URL.
    "platform/week.mjs",
    # 2026-09-05, charts cutover: admin-reports.js dynamic-imports the
    # canonical chart module through this manifest and awaits it (and the
    # module's own i18n top-level await) before painting. The classic
    # root admin-charts.js + window.SanaCharts are retired.
    "platform/admin-charts.mjs",
    # 2026-09-05, search cutover: home, listing popularity ranking,
    # reports clustering, combo ranking, create-appointment suggestions
    # and upload-order matching dynamic-import the canonical search
    # module through this manifest and await it locally. The classic
    # root search.js + window.SanaSearch are retired.
    "platform/search.mjs",
    # 2026-09-05, admin-login cutover: the standalone /admin/login page
    # imports the canonical controller through this manifest (it cannot
    # assume sanaImportStatic) and awaits it before processing a queued
    # early submit. The classic root admin-login.js + window.SanaAdminLogin
    # are retired. Configuration data stays on window; there is no function
    # API namespace.
    "platform/admin-login.mjs",
    # 2026-09-05, listing cutover: home, server-mounted catalog tables,
    # lab-results and upload-order dynamic-import the canonical listing
    # module through this manifest and await it locally. The classic
    # root listing.js + window.SanaListing are retired.
    "platform/listing.mjs",
    "platform/admin-solicitudes.mjs",
    # 2026-09-05, hours-editor cutover: the services/doctors catalog editors
    # dynamic-import the canonical multi-center schedule editor through this
    # manifest and await it (and the module's own i18n top-level await)
    # before rendering any agenda. The classic root admin-hours-editor.js +
    # window.SanaHoursEditor are retired.
    "platform/admin-hours-editor.mjs",
    # 2026-09-05, admin-users cutover: /admin/users boots the canonical
    # controller through this manifest and awaits it before painting; the
    # doctors editor awaits the same module for renderDoctorUser. Account's
    # optional post-save refresh is detached from its save completion.
    # The classic root admin-users.js + window.SanaUsers /
    # window.sanaRouteUsuarios are retired.
    "platform/admin-users.mjs",
    # 2026-09-05, account-form cutover: /account, /account/reset, the wizard
    # mountAuth, my-requests, chat-widget and dependents dynamic-import the
    # canonical account module through this manifest and await it locally.
    # The classic root account-form.js + window.SanaAccount are retired.
    "platform/account-form.mjs",
    # 2026-09-05, chat-widget cutover: public templates and site_shell._shell
    # bootstrap the floating booking assistant through this manifest (the
    # shared _chat_bootstrap.html partial) and await it locally. The classic
    # root chat-widget.js + window.SanaChat are retired.
    "platform/chat-widget.mjs",
    # 2026-09-05, chat-ui cutover: the booking widget and the pre-consult
    # interview dynamic-import the shared bubble-list/composer module
    # through this manifest and await it before mounting a composer. The
    # classic root chat-ui.js + window.SanaChatUI are retired.
    "platform/chat-ui.mjs",
    # 2026-09-05, dependents cutover: /account and the wizard load the
    # canonical personas-a-cargo module through this manifest and await it
    # locally (lazy in the wizard, parallel with Account on /account). The
    # classic root dependents.js + window.SanaDependents are retired.
    "platform/dependents.mjs",
    # 2026-09-05, create-appointment cutover: Requests, the day queue and the
    # doctors calendar dynamic-import the canonical overlay through this
    # manifest and hold an early click until it resolves. The classic root
    # admin-create-appointment.js + window.SanaCreateAppointment are retired.
    "platform/admin-create-appointment.mjs",
    # 2026-10-27, d1122568: the one «¿está en HSI?» control, imported by
    # admin-create-appointment.mjs, admin-app.js and admin-queue.mjs.
    "platform/hsi-sync.mjs",
})

# Historical public paths that must redirect to the current immutable URL.
# Values are registry names, never filesystem paths supplied by a request.
_LEGACY_ASSET_ALIASES = {
    "/admin-tabla.js": "platform/admin-table.mjs",
    # Intake's root-era names plus direct raw module paths.  Old cached
    # shells learn the immutable URL; raw module files are never served.
    "/entrevista.js": "modules/intake/entrevista.js",
    "/entrevista.css": "modules/intake/entrevista.css",
    "/modules/intake/entrevista.js": "modules/intake/entrevista.js",
    "/modules/intake/entrevista.css": "modules/intake/entrevista.css",
    "/modules/intake/admin-entrevistas.mjs": "modules/intake/admin-entrevistas.mjs",
    # Turnos native controllers: relative `./wz-upload.mjs` from a hashed
    # `/static/modules/turnos/service.<hash>.mjs` resolves to the unhashed
    # path. Redirect that (and the intake-shaped /modules/turnos/* aliases)
    # to the current immutable URL. Not JS facades; not CLIENT_LOADED.
    "/static/modules/turnos/wz-upload.mjs": "modules/turnos/wz-upload.mjs",
    "/static/modules/turnos/service.mjs": "modules/turnos/service.mjs",
    "/static/modules/turnos/upload-order.mjs": "modules/turnos/upload-order.mjs",
    "/modules/turnos/wz-upload.mjs": "modules/turnos/wz-upload.mjs",
    "/modules/turnos/service.mjs": "modules/turnos/service.mjs",
    "/modules/turnos/upload-order.mjs": "modules/turnos/upload-order.mjs",
}


# Every asset migrated OFF `?v=` onto a fingerprinted URL. Adding a filename
# here — and swapping its references from `/<name>?v=NN` to `url(<name>)` /
# `{{static.<name>}}` / `{{ static('<name>') }}` at each call site — IS the
# entire migration step for that platform asset. Module assets are registered
# separately through exact v2 manifest objects, and manual first-party `?v=`
# references are rejected by the repository invariant.
MIGRATED = frozenset({
    "platform/nav.mjs", "human-rounds.css", "platform/format.mjs",
    # The shared admin login-gate stylesheet, loaded by the direct login
    # screen. Registering it keeps the gate rules in one immutable asset.
    "admin-gate.css",
    # The panel's transient toast presentation, shared by every server-routed
    # /admin/<screen> through templates/admin/base.html.
    # admin-shell.js (the lazy node + window.toast provider, an
    # ADMIN_ROOT_JAVASCRIPT member) is its runtime half.
    "admin-shell.css",
    # Server-routed screens own their shared screen CSS; this is deliberately
    # separate from the retired hash-shell stylesheet.
    "admin-direct.css", "platform/admin-control-fit.mjs",
    # Tier 5 slice 3 — vendored Preact+HTM (pinned, committed, provenance in
    # docs/roadmap/tier5-frontend-design.md).
    "vendor/preact.module.js", "vendor/hooks.module.js",
    "vendor/htm.module.js", "vendor/htm-preact.module.js",
    # Tier 5 slice 3 — the Calendario-tab pilot island and the monthly-grid
    # module it renders through (canonical since the UMD calendar implementation
    # and its global were deleted, 2026-09-04).
    "islands/MedCal.mjs", "platform/calendar-grid.mjs",
    # 2026-10-21: the panel's date field (Spanish/i18n month grid instead of
    # the browser-language native picker). The admin base template imports it
    # by its fingerprinted URL; it resolves i18n and calendar-grid through
    # window.SANA_STATIC like every other panel module.
    "platform/date-field.mjs",
    # 2026-11-07: the printed sheet's head, linked by the admin base template.
    "platform/print-head.mjs",
    "platform/analytics.mjs",
    # Tier 4 slice 6, batch 2 (2026-08-08) — every root-level patient-site/
    # shared asset whose references are ALL server-rendered.
    "platform/i18n.mjs", "platform/admin-charts.mjs",
    "platform/search.mjs", "platform/admin-login.mjs", "platform/listing.mjs",
    "platform/admin-solicitudes.mjs",
    "platform/admin-hours-editor.mjs",
    "platform/hsi-sync.mjs",
    "platform/admin-users.mjs",
    "platform/admin-system.mjs", "platform/admin-settings.mjs",
    "platform/admin-roles.mjs",
    "platform/admin-queue.mjs",
    # Catalog screens mount this entry directly; not a shared manifest dependency.
    "platform/admin-content.mjs",
    "platform/doctor-workspace.mjs", "platform/doctor-workspace.css",
    "platform/admin-queue.css",
    "platform/admin-roadmap.mjs", "platform/admin-alerts.mjs", "platform/admin-reports.mjs", "platform/admin-appearance.mjs",
    # 2026-09-08, HSI cutover: the direct /admin/hsi screen boots the native
    # lifecycle-owned controller from its fingerprinted URL; the classic root
    # admin-hsi.js (and its ADMIN_ROOT_JAVASCRIPT entry) is physically retired.
    # Not CLIENT_LOADED — nothing resolves it through window.SANA_STATIC.
    "platform/admin-hsi.mjs",
    "platform/admin-usability.mjs",
    # Template-only native roster; not a shared CLIENT_LOADED dependency.
    "platform/admin-patients.mjs",
    # Server-rendered entry only; do not change the shared CLIENT_LOADED map.
    "platform/admin-preparations.mjs",
    # Staff Account is imported by its template, not the shared client manifest.
    "platform/admin-account.mjs",
    "platform/admin-my-appointments.mjs", "platform/admin-doctor-patients.mjs",
    # The one loader and eight-second deadline of the screens whose controller
    # arrives after the page (templates import it; not CLIENT_LOADED).
    "platform/screen-boot.mjs",
    "platform/photo-zoom.mjs", "platform/week.mjs", "platform/tiles.mjs",
    "platform/centers-map.mjs",
    "platform/account.mjs", "platform/account-requests.mjs",
    "platform/account-labs.mjs",
    "platform/auth-test.mjs",
}) | ADMIN_ROOT_JAVASCRIPT | ADMIN_SHELL_UTILITIES | CLIENT_LOADED

_CACHE = {}  # platform name -> (mtime, hash8); module name -> (stable fd identity, hash8)
_MODULE_ASSETS_CACHE = None


# Nested MIGRATED names resolve under the REAL `static/` directory instead
# of repo root — `vendor/` (Tier 5 slice 2, populated slice 3), `islands/`
# (Tier 5 slice 3, the pilot island) and `platform/` (2026-09-05, the
# i18n cutover's first entry: platform-owned canonical client code placed
# under its owning package per row 1.1, not left at repo root) are the
# buckets that exist today; a future nested bucket only needs adding its
# prefix here, no other change.
_NESTED_PREFIXES = ("vendor/", "islands/", "platform/")


def _module_assets():
    """Lazy registry avoids an import cycle during module discovery."""
    global _MODULE_ASSETS_CACHE
    if _MODULE_ASSETS_CACHE is not None:
        return _MODULE_ASSETS_CACHE
    from app.core import app_modules
    _MODULE_ASSETS_CACHE = app_modules.module_asset_paths()
    return _MODULE_ASSETS_CACHE


def _registered_names():
    """Platform and module-owned immutable assets in one serving registry."""
    return frozenset(MIGRATED) | frozenset(_module_assets())


def _physical_path(name):
    """Resolve a MIGRATED name to its real file on disk. Root-level bundles
    (`nav.js`, `human-rounds.css`, remaining classic scripts) live next to
    this module. A name starting with one of `_NESTED_PREFIXES` lives under
    the REAL `static/` directory instead — `static/vendor/preact.module.js`,
    `static/islands/MedCal.mjs`, `static/platform/format.mjs` — because
    that's where the design vendors/generates these files, not a second
    root-level bucket. The root-level bundles sit in core/paths.py's
    ROOT_WEB_ASSETS_DIR: their public names never change with the directory. A
    module asset (`modules/<id>/<file>`) is the module's own file, under the
    application code root."""
    if name.startswith(_NESTED_PREFIXES):
        return os.path.join(_ROOT, "static", name)
    if name.startswith("modules/"):
        return os.path.join(_code_dir(), name)
    return os.path.normpath(os.path.join(_ROOT, paths.ROOT_WEB_ASSETS_DIR, name))


def _code_dir():
    """The directory a module asset's `modules/<id>/<file>` name is relative to."""
    return os.path.normpath(os.path.join(_ROOT, paths.CODE_DIR))


def _fd_signature(info):
    """The full identity contract shared with the hardened static resolver."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_nlink,
    )


def _open_module_asset(name):
    """Open a declared module asset through no-follow directory descriptors.

    Module manifests are validated at discovery, but a path can still be
    swapped after that validation.  Hashing through a verified descriptor
    means a later file or parent-directory symlink can never redirect this
    process outside the module tree.  Platform files keep their historic path
    behavior; this stricter route is only for manifest-owned assets.
    """
    pieces = name.split("/")
    if len(pieces) < 3 or pieces[0] != "modules" or any(part in {"", ".", ".."} for part in pieces):
        raise OSError("unsafe module asset path")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise OSError("platform does not support no-follow module asset reads")
    directory_flags = os.O_RDONLY | nofollow | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(_code_dir(), directory_flags)
    child_fd = -1
    try:
        for index, part in enumerate(pieces):
            final = index == len(pieces) - 1
            flags = os.O_RDONLY | nofollow
            if not final:
                flags |= getattr(os, "O_DIRECTORY", 0)
            else:
                flags |= getattr(os, "O_NONBLOCK", 0)
            child_fd = os.open(part, flags, dir_fd=fd)
            previous_fd = fd
            fd = child_fd
            child_fd = -1
            os.close(previous_fd)
            info = os.fstat(fd)
            if final:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise OSError("module asset is not a regular file")
            elif not stat.S_ISDIR(info.st_mode):
                raise OSError("module asset parent is not a directory")
        return fd
    except BaseException:
        for descriptor in {fd, child_fd}:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        raise


def _digest_verified_fd(fd):
    """Hash one held module descriptor only if its identity stays stable."""
    before = os.fstat(fd)
    signature = _fd_signature(before)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise OSError("module asset is not a single-link regular file")
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(fd, 128 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    after = os.fstat(fd)
    if _fd_signature(after) != signature:
        raise OSError("module asset changed while hashing")
    return signature, digest.hexdigest()[:8]


def _module_hash8(name):
    """Hash module-owned bytes from one identity-stable, no-follow FD."""
    fd = _open_module_asset(name)
    try:
        identity = _fd_signature(os.fstat(fd))
        cached = _CACHE.get(name)
        if cached and cached[0] == identity:
            return cached[1]
        checked_identity, value = _digest_verified_fd(fd)
        if checked_identity != identity:
            raise OSError("module asset identity changed before hashing")
        _CACHE[name] = (identity, value)
        return value
    finally:
        os.close(fd)


def _read_verified_module_fd(fd):
    """Read module bytes while retaining the descriptor's full identity."""
    signature, digest = _digest_verified_fd(fd)
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(fd, 128 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    if _fd_signature(os.fstat(fd)) != signature:
        raise OSError("module asset changed while reading")
    return b"".join(chunks), digest


def _fingerprinted_path(name, digest):
    dirpart, _, filename = name.rpartition("/")
    stem, _, suffix = filename.partition(".")
    prefix = f"{dirpart}/" if dirpart else ""
    return f"/static/{prefix}{stem}.{digest}.{suffix}"


def open_module_asset_matches_url(name, path, fd):
    """Whether a held module descriptor still supplies this immutable URL.

    The ASGI static resolver owns the descriptor through streaming; this
    verification closes the small resolve-to-open window without reopening a
    module path by name.
    """
    try:
        _identity, digest = _digest_verified_fd(fd)
        return _fingerprinted_path(name, digest) == path
    except OSError:
        return False
    finally:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
        except OSError:
            pass


def _hash8(name):
    if name.startswith("modules/"):
        return _module_hash8(name)
    path = _physical_path(name)
    mtime = os.path.getmtime(path)
    cached = _CACHE.get(name)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()[:8]
    _CACHE[name] = (mtime, digest)
    return digest


def _url_for(name):
    """Format one already-authorized registered name as an immutable URL."""
    return _fingerprinted_path(name, _hash8(name))


def url(name):
    """`nav.js` -> `/static/nav.<hash8>.js`; `vendor/preact.module.js` ->
    `/static/vendor/preact.<hash8>.module.js` (the hash sits after the
    basename's FIRST dot, not its last — a no-op split point for every
    single-dot name that exists today, but what lets a compound vendor
    suffix like `.module.js` hash in the middle and still read as
    "preact, some version, the module build" instead of
    `preact.module.<hash8>.js`). Raises on a name not in MIGRATED — a call
    site reaching for a fingerprinted URL on an asset that's still
    `?v=`-only is a bug (dead `?v=` reference left behind, or a
    fingerprinted URL that will 404), not something to silently fall back
    on."""
    if name not in MIGRATED:
        raise ValueError(f"{name!r} is not a platform fingerprinted asset")
    return _url_for(name)


def module_url(name):
    """Fingerprint a declared module asset without widening platform assets.

    Kept separate from :func:`url` so module renderers cannot accidentally
    turn a root asset into implicit module-owned policy.
    """
    if name not in _module_assets():
        raise ValueError(f"{name!r} is not a declared module asset")
    return _url_for(name)


def legacy_asset_redirect(path):
    """Return a current immutable target for one exact legacy asset path.

    Transport-neutral and query-free by design: both HTTP implementations use
    this function, then emit the same GET-only, empty 301 response.
    """
    name = _LEGACY_ASSET_ALIASES.get(path)
    if name is None:
        return None
    try:
        return module_url(name) if name.startswith("modules/") else url(name)
    except (OSError, ValueError):
        # A malformed/replaced module manifest must leave the old alias as an
        # ordinary miss, never turn a legacy GET into a transport-level 500.
        return None


def is_legacy_asset_alias(path):
    """Whether a path is reserved for legacy immutable-asset compatibility."""
    return path in _LEGACY_ASSET_ALIASES


# `{{static.human-rounds.css}}` / `{{static.nav.js}}` — same flat-key shape
# as `{{t.*}}`/`{{sitio.*}}`, except the "key" IS the filename (hyphen and
# dot included) since there's no separate namespace level to strip. `mjs`
# joins `js`/`css` here for symmetry with MIGRATED (Tier 5 slice 2). Tier 5
# slice 3 adds two things: `/` to the allowed characters (the pilot import
# map is built from nested tokens like
# `{{static.vendor/preact.module.js}}` and `{{static.islands/MedCal.mjs}}`
# now that slice 3 actually has files at those nested MIGRATED names — this
# is the first template to spell out a nested placeholder by hand), and an
# optional `module.` infix before the extension — same compound-suffix shape
# `url()`'s own stem/suffix split and smoke_prod.py's scraper regex already
# handle, needed here too or `vendor/preact.module.js` wouldn't match at all
# (the extension group would see `module.js`, not `js`, right after the
# first captured dot).
_PLACEHOLDER_RE = re.compile(r"\{\{static\.([a-zA-Z0-9_/-]+\.(?:module\.)?(?:js|css|mjs))\}\}")


def _resolve(path):
    """URL path -> migrated filename, iff the path is EXACTLY what `url()`
    computes for some name in MIGRATED right now. None on any mismatch
    (unknown path shape, name not migrated, or a stale/forged hash) — the
    caller falls through to ordinary static-file handling, which 404s a
    path with no such literal file.

    Recomputing `url()` per candidate (cheap: MIGRATED is a handful of
    entries and `_hash8` is mtime-cached) instead of regex-parsing the
    incoming path is what lets this function stay correct for BOTH the flat
    shape (`nav.<hash8>.js`) and a vendor entry's nested, compound-suffix
    shape (`vendor/preact.<hash8>.module.js`) with no per-shape parsing
    code to maintain — a new suffix shape only ever needs a new MIGRATED
    entry, never a regex edit here."""
    if not path.startswith("/static/"):
        return None
    for name in _registered_names():
        try:
            if _url_for(name) == path:
                return name
        except OSError:
            continue
    return None


def transport_target(path):
    """Map one public fingerprint URL to its physical static-policy target.

    The first item is a URL-shaped path consumed by the descriptor-relative
    static resolver, not a filesystem pathname.  The remaining items pin the
    legacy response's content type and cache policy.  ``None`` means the URL
    is neither a current fingerprint nor a supported relative-import
    passthrough.
    """
    if not path.startswith("/static/"):
        return None
    suffix = path[len("/static/"):]
    passthrough_type = _PASSTHROUGH.get(suffix)
    if passthrough_type is not None:
        return (f"/{suffix}", passthrough_type, "public, max-age=300")
    name = _resolve(path)
    if name is None:
        return None
    physical = f"/static/{name}" if name.startswith(_NESTED_PREFIXES) else f"/{name}"
    return (
        physical,
        None,
        "public, max-age=31536000, immutable",
    )


def manifest():
    """`{name: url(name)}` for every CLIENT_LOADED asset — the map a shell
    publishes to the browser as `window.SANA_STATIC`. Fail-closed by
    construction: `url()` raises for a name outside MIGRATED (CLIENT_LOADED
    is a subset, pinned by test), and every value is the CURRENT content
    hash, so the map can never carry a URL the serving side would 404."""
    return {name: url(name) for name in sorted(CLIENT_LOADED)}


def manifest_script():
    """The inline `<script>` a page emits ONCE in `<head>` when it loads any
    runtime loader (platform/chat-widget.mjs, my-requests.js, lab-results.js,
    platform/account-form.mjs, platform/admin-create-appointment.mjs): `window.SANA_STATIC`
    with the manifest above, the same bootstrap-global shape as
    `window.SANA_ISLAND_URLS` and `i18n.bootstrap_script()`'s
    `window.SANA_I18N`. Compact, sorted JSON; the payload charset is
    fingerprinted URLs (`[a-zA-Z0-9_./-]`) plus JSON punctuation — nothing
    to HTML-escape and no `</script>` can occur, so it is safe inside a
    <script> and inside every HTML pipeline this repo has (f-string,
    `render()` token, Jinja global) without a wrapper."""
    payload = json.dumps(manifest(), separators=(",", ":"), sort_keys=True)
    # `window.SANA_EV=1` only where the anonymous analytics stream is on: nav.mjs then loads
    # platform/analytics.mjs; without it the page never asks for that file.
    # Its own tag before the manifest: the manifest tag keeps the exact shape its readers (smoke, tests) match.
    flag = "<script>window.SANA_EV=1;</script>" if _analytics_on() else ""
    return f"{flag}<script>window.SANA_STATIC={payload};</script>{recover_script()}"


def _analytics_on():
    try:
        from app.modules.turnos import analytics_export
        return analytics_export.enabled()
    except Exception:  # noqa: BLE001 — a page never fails over its statistics
        return False


_RECOVER_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static_recover.js")
_RECOVER_SCRIPT = None


def recover_script():
    """The inline `<script>` that follows the manifest on every page: when a
    lazy module cannot be fetched because the municipal portal gateway ended
    the session (401 JSON with the sign-in URL), go once to the portal sign-in
    and come back to this page. Inline because every static file sits behind
    the same 401. Source and behavior → core/static_recover.js."""
    global _RECOVER_SCRIPT
    if _RECOVER_SCRIPT is None:
        with open(_RECOVER_JS, encoding="utf-8") as fh:
            source = fh.read().strip()
        # The header comment is for readers of the file, not for every page's bytes.
        source = source[source.index("*/") + 2:].strip() if source.startswith("/*") else source
        _RECOVER_SCRIPT = f"<script>{source}</script>"
    return _RECOVER_SCRIPT


# The reserved token the `render()` pipeline resolves to `manifest_script()`.
# It is resolved before the `{{static.<name>}}` pass below; the
# placeholder regex requires a `.js|.css|.mjs` extension, so it can never
# match this extension-less token and the two can't collide.
_MANIFEST_PLACEHOLDER = "{{static.manifest}}"


def render(text):
    """Replaces every `{{static.<name>}}` in `text` with its fingerprinted
    URL — the `{{static.*}}` sibling of `i18n.render()`'s `{{t.*}}` and
    `site_identity.render()`'s `{{sitio.*}}`. A key not in MIGRATED is left
    untouched (surfaces as a broken literal `{{static.…}}` on the page,
    same "visibly wrong instead of silently blank" choice `i18n.raw()`
    makes for a missing translation) rather than raising mid-render.
    The one reserved, extension-less token `{{static.manifest}}` resolves to
    the whole `manifest_script()` tag instead of a URL (batch 3)."""
    if _MANIFEST_PLACEHOLDER in text:
        text = text.replace(_MANIFEST_PLACEHOLDER, manifest_script())

    def _sub(m):
        name = m.group(1)
        if name not in MIGRATED:
            return m.group(0)
        return url(name)
    return _PLACEHOLDER_RE.sub(_sub, text)


def invalidate():
    """Only for tests: drop the mtime cache so a rewritten fixture reloads."""
    global _MODULE_ASSETS_CACHE
    _CACHE.clear()
    _MODULE_ASSETS_CACHE = None


# `tokens.css` (Tier 5 slice 1) lives at repo root, next to human-rounds.css,
# and is @import'ed from it with a bare relative URL — the cheapest possible
# resolution for Storybook/Vite, which reads it straight off disk with no
# server involved. But human-rounds.css itself is ALWAYS served at its
# fingerprinted `/static/human-rounds.<hash8>.css` form in production (every
# real call site uses `url()` above), so the BROWSER resolves that relative
# `@import` against `/static/`, not `/` — a request for literally
# `/static/tokens.css`. There's a real `static/` directory in this repo
# (images, fonts) that the native static resolver serves 1:1 by path, but
# `tokens.css` isn't in it — it's the root file, deliberately (a second copy
# under `static/` would fork the "one file" the design explicitly wants).
# tokens.css is NOT added to MIGRATED (no fingerprint churn for it this
# slice, per the design doc) — this is a literal, un-hashed passthrough, so
# it gets the same short max-age=300 cache as `/tokens.css`, never immutable.
#
# The former format.js passthrough was retired when format.mjs became the
# canonical implementation. Its old plain static path is now an ordinary
# static miss, matching the already-retired calendar UMD.
_PASSTHROUGH = {
    "tokens.css": "text/css",
}


# ---------------------------------------------------------------- route
