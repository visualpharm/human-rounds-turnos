# Module manifest + loader contract (Tier 2 framework design)

Status: BINDING design for the Tier 2 framework commit (open-source-readiness-plan.md,
Tier 2 "Execution plan"). Written 2026-08-05 from a full audit of the central wiring a
module touches today. After the framework commit lands, adding or disabling a module
requires changes ONLY inside `modules/<id>/`. This doc merges into `docs/modules.md`
once implemented; until then it is the spec the implementation is reviewed against.

> **Current implementation note (2026-08-21):** this design contains recorded
> pre-move baselines and historical browser/network evidence. Those passages
> describe the framework rollout, not current raw-source behavior. Legacy
> string/glob `assets` entries remain ownership metadata only. Runtime static
> assets must be exact `{path, fingerprint: true}` objects; they are validated
> as module-owned single-link regular files, published through the immutable
> module registry, and rendered with `module_url()` / `module_static()`.

## Recorded pre-move baseline (gate item 1)

2026-08-05, clean worktree of `origin/main` 347a9f7: backend
`TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover` →
**2907 passed, 0 failed, 0 skipped**; frontend `node --test test/*.test.mjs` →
**836 passed, 0 failed**. The four historically flaky tests (2× `/logo.png`,
2× `TestListaEsperaApi`) all pass at this commit. Every Tier 2 slice re-runs
both suites and must match or beat this.

## What the audit found (why each contract piece exists)

Seven categories of central files a new module must hand-edit today:

1. `routing.py` — literal `GET`/`POST`/`PUT`/`DELETE` lists + a hand-added
   `import modules.<id>.*` (routing.py:84-127).
2. `admin.html` — sidebar `<a data-nav>` literals (L1226-1246) and the
   `routeCurrentAdminScreen()` if/else chain (L1638-1666).
3. 8+ `admin-*.js` routers each carrying a hardcoded `OTRAS` hide-array.
4. `nav.js` — patient nav is a hardcoded array (nav.js:61-66); manifest
   `nav_entries` is dead data today.
5. Scatter: platform files (`notifications.py`, `reminders.py`, `chart_summary.py`,
   `doctor_appointments.py`, `doctor_patients.py`, `booking_requests.py`,
   `catalog.py`, `prompts.py`, `patient_site.py`) each do their own
   `app_modules.is_enabled("intake")` + local `from modules.intake import X`.
6. Startup daemons all started centrally in `server.py:main()` (L3709-3814);
   no module-owned lifecycle.
7. i18n catalogs (`locales/{en,es}/intake.json`) and content packs
   (`packs/global/intake-*.json`) live centrally even when module-owned.

Already solved and NOT re-done: enablement (`ENABLED_MODULES` config key, no
restart), discovery (`app_modules.list_modules()`), dispatch gating
(`routing.dispatch()` → `app_modules.route_enabled()`), static serving of module
files (the generic ROOT fallback in server.py already serves `modules/<id>/foo.js`).

## Manifest schema v2 (`modules/<id>/module.json`)

Superset of v1 — every existing field keeps its meaning; v1 manifests stay valid
(new fields default to empty). New/changed fields marked ★.

```json
{
  "id": "intake",
  "name": "Entrevista previa",
  "description": "...",
  "requires": ["turnos"],                          // ★ module dependencies
  "entrypoint": "modules.intake.app",              // ★ optional; startup/shutdown hooks
  "routes": [                                       // ★ richer: dotted module + methods
    {"module": "modules.intake.routes_patient", "methods": ["GET", "POST"]},
    {"module": "modules.intake.routes_admin",   "methods": ["GET", "POST", "PUT"]}
  ],
  "admin_sections": [                               // ★ richer: renderable entries
    {"id": "entrevistas", "label_key": "intake.nav.entrevistas",
     "label": "Entrevistas", "icon": "…", "order": 40,
     "roles": ["admin", "medico"], "js": "modules/intake/admin-entrevistas.js"}
  ],
  "nav_entries": [                                  // ★ richer: order + label_key
    {"path": "/…", "label": "…", "label_key": "…", "order": 30}
  ],
  "assets": [
    "modules/intake/*.js",                         // legacy ownership metadata
    {"path": "modules/intake/client.js", "fingerprint": true} // immutable runtime asset
  ],
  "locales": "modules/intake/locales",              // ★ optional module catalog dir
  "db_tables": ["…"],                               // unchanged
  "config_keys_prefixes": ["…"]                     // unchanged
}
```

Back-compat: v1 `routes` (list of bare strings) is still accepted — bare string
`"x"` ≡ `{"module": "x", "methods": all-registered}`; v1 `admin_sections` /
`nav_entries` string/pair forms likewise normalize. Normalization lives in ONE
place (`app_modules._normalize_manifest()`); everything downstream sees v2 shape.

For `assets`, a string or glob keeps its legacy declarative ownership meaning and
does not create a served immutable URL. A fingerprinted object has exactly the two
keys `path` and `fingerprint: true`; it is never a glob. The registry rejects an
unsafe owner/path, duplicate, platform overlap, non-JS/CSS/MJS suffix, symlink,
hardlink, nonregular file, or undeclared admin-section script.

## Loader contract (`app_modules.py` grows; no new file)

1. **Deterministic discovery.** `list_modules()` sorts by module id; every derived
   ordering (routes, nav, admin sections) is stable: platform-core first, then
   modules by sorted id, then by manifest order / `order` field, ties by id.
2. **Route registration, ORDER-PRESERVING.** `routing.py`'s four lists shrink to
   PLATFORM-CORE literals only (order untouched — it is load-bearing). At
   import, routing calls `app_modules.merge_routes(method, platform_literals)`:
   the loader imports each enabled-or-disabled module's declared route modules
   (importlib) and SPLICES each one into the platform list at the position its
   manifest declares (a route entry's `after` anchor — the name of the
   platform-core module, or another already-positioned route, it must land
   immediately after; a string applies to every method the route registers on,
   a dict is per-method for the few routes that sat in genuinely different
   relative positions across GET/POST/PUT/DELETE in the pre-Tier-2 literal
   lists — `chart_summary` is the clearest example). A route with no `after`
   (the default — any brand-new module needs ZERO central wiring) appends at
   the tail, same as the original loader contract; an anchor that never
   resolves degrades the same way rather than crashing or vanishing silently.
   This is what makes `test/test_route_order.py`'s golden-order test pass: the
   EFFECTIVE GET/POST/PUT/DELETE sequences reproduce the literal pre-Tier-2
   registries (`git show 347a9f7:routing.py`) name-for-name, not just
   "platform order preserved, app routes appended after" (see Deviation #2's
   history below — that was the actual P0 bug this fixes). Modules always
   REGISTER; `dispatch()` keeps gating per-request by enablement (that part
   already worked). A module whose import raises is marked `broken` (see
   errors) and contributes nothing; the platform still boots.
3. **Disabled = not there → 404, and so is a genuinely unknown path.** DECIDED
   by the product owner, 2026-08-05 (superseded the "accepted deviation" below): dispatch
   already treats a disabled module's handler as unregistered (spy-tested);
   server.py's static-fallback now answers the friendly 404 page for ANY path
   that isn't a real, on-disk resource (`_static_denegado_es_conocido()`
   resolves against ROOT, not by extension) — a disabled module's route is
   indistinguishable from a path that never existed, by design. 403 stays
   reserved for a KNOWN resource a session lacks permission for (source files,
   secrets, `require_admin`/`_require_owner` gates) — those are untouched.
4. **Dependencies.** `enable(id)` refuses when a `requires` entry is disabled or
   missing; `disable(id)` refuses when an enabled module requires it; both return
   the human-readable reason (CLI + API surface it). No cascades — explicit only.
   Cycle detection at discovery; a cycle marks all members broken.
5. **Lifecycle, TRANSACTIONAL.** If `entrypoint` is set, the loader imports it and
   calls `startup(ctx)` for each enabled AND HEALTHY module — a module already
   reported `broken` by ANY signal (unknown dependency, a dependency cycle, or
   a prior runtime failure — `_is_broken()`, not just a stale `_RUNTIME_BROKEN`
   entry) never gets a startup attempt at all. `startup_all()`/`shutdown_all()`
   run in TOPOLOGICAL/reverse-topological order by `requires` (`_topo_order()`
   — a dependency always starts before its dependent, stops only after it), not
   alphabetical. If `startup(ctx)` registers a hook and THEN raises, the
   registration is rolled back (`hooks.unregister_module()`) before the module
   is marked broken — a failed startup never leaves a live, callable hook
   behind. `ctx` carries `db`, `config`, `register_hook`. Both entrypoint hooks
   optional; exceptions are caught, logged, and mark the module broken (never
   take the platform down).
6. **Extension points (hooks), HEALTH-GATED.** `app_modules.hooks` — a tiny
   named-hook registry: modules register callables at `startup()`; platform
   code calls `hooks.call("<point>", *a, **kw)` (returns list of results from
   HEALTHY AND enabled modules only — `_is_healthy()`, not merely
   `is_enabled()`, so a module broken mid-startup, or marked broken later by
   something else, never keeps firing a callback it registered earlier). The
   framework commit ships the registry and migrates ONE scatter call site
   end-to-end as proof (chosen: `reminders.py`'s intake hook — smallest blast
   radius, reminders.py:99). The remaining scatter sites migrate with the
   pilot / module-by-module — NOT in the framework commit. `enable(id)` also
   requires a `requires` dependency be healthy AND started (not merely named
   in the enabled-config string) — each dependency gets one `startup_one()`
   attempt first (idempotent, same lazy pattern `hooks.call()`'s own
   `ensure_started()` uses) so a fresh process gives it a fair chance before
   the health check runs.
7. **Errors are a first-class state.** `list_modules()` entries carry
   `status: enabled|disabled|broken` + `error`. `manage.py mod list` and
   `GET /api/admin/modules` show it. A broken module's routes/nav/sections are
   treated as absent.

## Admin shell contract (`admin.html` + `admin-*.js`)

1. **Section registry replaces the if/else chain.** `admin.html` exposes
   `window.SanaAdminRouter.register(sectionId, renderFn)`;
   `routeCurrentAdminScreen()` becomes a lookup in that registry. Platform
   sections keep their current files and register at load — the chain's branches
   convert mechanically. Unknown hash → existing fallback behavior.
2. **Sidebar + OTRAS derive from the manifest data — IMPLEMENTED, role-filtered.**
   On load, admin.html embeds (single fetch of `GET /api/admin/modules`) the
   section list; module-contributed sidebar entries are injected from it,
   ordered by `order`; each router's hide-list comes from a shared helper
   `SanaAdminSections.otras(mineId)` instead of a literal `OTRAS` array. First
   dispatch waits for the registry fetch (single promise gate,
   `SANA_ADMIN_SECTIONS_READY`). The endpoint is no longer `solo_admin=True`
   (Codex monitor review, P1, 2026-08-05: that blocked every non-admin panel
   role outright, including `medico`, which the shell needs the registry for
   too) — `require_admin(handler, permitir_medico=True)` lets any authenticated
   panel role reach it, and the RESPONSE is what's filtered instead
   (`app_modules.sections_payload_for_role(rol)`, reading each `admin_sections`
   entry's `roles` list — transcribed verbatim from admin.html's own
   `applyRol()` visibility matrix); the full `modules` status/error/requires
   list (the actual enable/disable-relevant data) stays admin-only.
   `PLATFORM_SECTIONS` in `admin-sections.js` shrank to the genuine
   platform-core floor (`usuarios, cuenta, settings, sistema, configuracion,
   plan, legal` — mirrors `app_modules.py`'s own `_PLATFORM_ADMIN_SECTIONS`);
   turnos/intake's sections now come ONLY from the fetch, so disabling a
   module removes its section from the sidebar on next load
   (`test/admin-sections.test.mjs` + `test/test_app_modules.py` prove this
   pair-wise, backend and frontend). A SEPARATE `FAILSAFE_SECTIONS` constant
   (today's full catalog) is the no-blank-sidebar net — used ONLY while the
   fetch hasn't resolved yet, or failed outright; a real successful response
   always wins over it.
3. **Module admin JS loads dynamically — IMPLEMENTED, dormant until a manifest
   uses it.** `admin-sections.js` appends a `<script src="entry.js">` tag for
   each enabled section's declared `js` file once the registry arrives (dedup
   by URL — two sections naming the same file share one tag), and
   `SANA_ADMIN_SECTIONS_READY` only resolves once those scripts have actually
   loaded (or failed — a bad module script degrades that ONE section, never
   blocks boot). This is genuinely wired end-to-end
   (`test/admin-sections.test.mjs`'s script-loading suite), but nothing in
   `modules/turnos/` or `modules/intake/` sets a real `js` field yet — see
   "What's NOT wired" below, unchanged: the pilot is what proves acceptance
   (b) against a real screen, not this framework commit.
4. `.claude/hooks/admin-section-wiring-guard.py` already matches this contract
   (register-based, no `OTRAS` literal) — no further change needed here.

## Patient nav contract (`nav.js`)

- New PUBLIC endpoint `GET /api/nav` (platform-core, tiny JSON: enabled modules'
  `nav_entries` merged with platform entries, i18n-resolved labels, ordered).
  `nav.js` builds the nav from it, with a `sessionStorage` cache (stale-while-
  revalidate: render cached immediately, refresh in background) so the ~1s ART
  round-trip never blocks first paint, and a hardcoded fallback equal to today's
  array if the fetch fails. Disabled module → entries gone on next load.

## Out of scope for the framework commit (recorded so nobody scope-creeps it)

- Physical file moves of turnos' 100+ root files — later slices, after the gate.
- Migrating all 8 scatter call sites — pilot + module-by-module.
- DB DDL ownership (db.py keeps creating all tables; `db_tables` stays declarative).
- i18n catalog physical move — the `locales` manifest field ships, `i18n.py` learns
  to merge module catalog dirs, but moving existing catalogs is pilot work.
- Packs content (`packs/global/intake-*.json`) stays central; ownership noted in
  the manifest description only.
- The six-router OTRAS rewrite beyond the shared helper (each router's internal
  behavior unchanged).

## Pilot (task #5, after the gate) — DONE 2026-08-05

`intake` retrofitted to the full v2 contract: its `admin_sections.entrevistas`
entry now carries a real `label_key`/`order`/`js` (`modules/intake/
admin-entrevistas.js`, physically moved out of the repo root — it used to be
a static `<script>` tag AND a sidebar `<a>` literal in `admin.html`, neither
survives), its i18n catalog (`locales/{en,es}/intake.json`) moved to
`modules/intake/locales/` via the manifest's `locales` field, and `i18n.py`
grew the module-catalog merge that field needed (`app_modules.
module_locale_roots()` — this was the one honest framework gap the pilot
filled, exactly as anticipated by "Out of scope" above). `entrypoint`/hooks
were already real from the framework commit; the pilot didn't need to touch
them further.

**Acceptance (a) — disable/enable drives the sidebar + routes, zero central
edits to DO it (retrofitting the manifest to make that possible is the
one-time setup cost; disabling itself is pure data).** Proven twice:
- Unit level: `test/test_app_modules.py`'s
  `test_disabling_intake_removes_the_real_entrevistas_section_from_the_sidebar`
  reads the REAL manifest (not a synthetic fixture) and asserts
  `sections_payload_for_role("admin")` drops "entrevistas" the moment
  `app_modules.disable("intake")` runs, and it's back after `enable()`; a
  `medico` session never sees it either way (no `roles` key → admin-only
  default). `test/test_module_runtime.py`'s pre-existing
  `test_intake_disabled_404s_the_page_and_never_calls_the_handler` already
  covered "patient surfaces silent" (real socket-bound server, `/entrevista/1`
  → 404) — no new test needed there, the pilot's own manifest change is what
  makes it exercise the REAL admin_sections shape now.
- Live-server proof (subagent, Playwright + `test/e2e/fixture.py`'s hermetic
  recipe, throwaway DB, 2026-08-05): logged into a real running server,
  confirmed "Captura de síntomas" in the sidebar; ran
  `python3 manage.py mod disable intake` (exit 0, "intake: deshabilitado");
  `GET /api/admin/intake/settings` went 401→404; `GET /entrevista/1` went
  401→404; a hard reload of `/admin` (same session cookie) showed the
  sidebar item GONE, "Solicitudes"/"Cola" unaffected; `mod enable intake`
  brought both back (sidebar item restored, API back to 401). All 6 checks
  PASS. This run surfaced a real (pre-existing, unrelated-to-gating) bug:
  `entrar()` (the fresh-login code path) never called
  `injectModuleNavEntries()` — only `boot()` (page-load-with-cookie path)
  did — so "Entrevistas" was briefly missing right after typing credentials,
  fixed in `65d2c64` (both call sites now match, regression test in
  `test/admin-navigation.test.mjs`).
- `git diff --stat` of the retrofit commit (`3e8c084`) touches admin.html,
  i18n.py, app_modules.py, locales/{en,es}/admin.shell.json and test files
  alongside `modules/intake/` — this is the ONE-TIME manifest/framework
  retrofit, not the disabling act itself; the acceptance criterion is that
  the disable/enable ACT afterward needs no further edits anywhere, which
  the proofs above demonstrate.

**Acceptance (b) — a demo screen added and removed with a commit touching
ONLY `modules/intake/`.** Two commits, `git diff --stat` on each:

> **Historical network evidence:** the raw dynamic-script request described
> below was a 2026-08-05 rollout proof. Current module scripts use immutable
> fingerprint URLs from the validated module registry.
- Add: `eaa997d` — `modules/intake/admin-intake-demo.js` (new, 84 lines) +
  `modules/intake/module.json` (+6/-2, one new `admin_sections` entry).
  2 files changed, 88 insertions(+), 2 deletions(-) — nothing outside
  `modules/intake/`. Verified live (subagent, same hermetic recipe): logged
  in, confirmed "Demo piloto" in the sidebar, clicked it, confirmed the
  screen's own real content rendered (a self-created container — the screen
  never relies on an admin.html-provided `<div>`), and confirmed
  `GET /modules/intake/admin-intake-demo.js` actually appears in the
  browser's network requests (the dynamic `<script>`-loading pipeline
  served it, not a baked-in `<script>` tag) — 3/3 checks PASS, no console
  errors.
- Remove: this commit (the one recording this evidence) deletes
  `modules/intake/admin-intake-demo.js` and reverts `modules/intake/
  module.json`'s demo entry — the demo existed only to prove the contract,
  never meant to ship. `open-source-readiness-plan.md`'s Tier 2 checkbox
  updated to reflect the pilot is DONE; module-by-module moves of the
  remaining ~100 root `turnos`-module files stay open, unaffected by this.

One test-infrastructure gap the pilot also had to fill, unrelated to the
manifest itself: `test/test_module_manifest.py`'s
`TestAdminSectionsMatchAdminHtml` only globbed `admin-*.js` at the repo
root, so `admin-entrevistas.js`'s own `SanaAdminRouter.register(...)` call
went unseen after the physical move — it only kept passing by accident (a
stray comment happened to contain the quoted id). Fixed in `43681fd`
(also globs `modules/*/admin-*.js`, mirroring the same fix already applied
to `.claude/hooks/admin-section-wiring-guard.py`).

Gates, every commit: backend `TURNOS_FAKE_AI=1 python3 -W error::
ResourceWarning -m unittest discover` 3006/3006 (0 failed); frontend
`node --test test/*.test.mjs` 878/878; `npm run e2e` 7/7.

## Deviations history — closed out by the Codex monitor review (2026-08-05)

A prior implementation pass self-approved three changes to the product owner's acceptance
contract as "accepted deviations." Codex's monitor review of that commit
(`e272ac9`) rejected that framing outright: **an executor cannot self-approve
a change to the product owner's acceptance contract** — full-suite-green and spot-route
checks don't prove a global, load-bearing order change is safe, and a
disabled-route status-code change is a security-policy decision, not an
implementation detail. This section now records what actually happened to
each of the three, closed for real:

1. **Disabled routes / unknown paths: DECIDED by the product owner, 2026-08-05.** 404 for
   anything that isn't a real, on-disk resource — a disabled module's route
   and a path that never existed are, by design, indistinguishable. 403 stays
   reserved for a KNOWN resource a session lacks permission for (source files,
   secrets, `require_admin`/`_require_owner` gates) — those are untouched.
   Implemented in `server.py` (`_static_denegado_es_conocido()`, resolves
   against ROOT rather than guessing by extension) and proven by
   `test/test_module_runtime.py`, `test/test_module_lifecycle.py`,
   `test/test_http_server.py`, `test/test_static_hardening.py`.
2. **Canonical dispatch order: FIXED, not accepted as changed.** The prior
   pass's "platform order preserved, module routes appended after" was the
   actual P0 defect the monitor review caught — turnos' routes previously
   INTERLEAVED among platform handlers (e.g. `patient_site`/`booking_requests`
   ran before `accounts`/`auth_renaper`), and appending them all after the
   platform block instead changed which handler sees an overlapping path
   first for real (the `/api/admin/login` shadow-check the prior pass ran was
   evidence of exactly this risk, not proof it was safe). The fix: every
   manifest route entry carries an `after` splice anchor (`app_modules.
   merge_routes()`, see the Loader contract's Route registration item above)
   so the loader reproduces the EXACT pre-Tier-2 literal order, verified
   name-for-name against `git show 347a9f7:routing.py` by
   `test/test_route_order.py`'s golden-order test — not spot-checked, the
   FULL GET/POST/PUT/DELETE sequences. New modules still need zero central
   wiring: no `after` anchor simply means "append at the tail," today's
   default.
3. **`intake.requires: ["turnos"]`: DECLARED FOR REAL, not deferred.**
   `modules/intake/module.json` now carries the honest dependency; disabling
   `turnos` while `intake` is enabled is refused with a human-readable reason
   naming `intake` (`test_app_modules.py`'s `TestEnableDisable`,
   `test_module_lifecycle.py`'s `TestRealIntakeTurnosEdgeHttp`). The
   dependency engine is proven against BOTH the synthetic fixture
   (`test_module_runtime.py`'s `TestDependencyRefusalWithReason`, still kept —
   it isolates the mechanics from turnos/intake's own enablement) and this
   real pair.
4. **e2e harness gotcha, applies to every worktree:** `admin_auth._env()`
   falls back to `.env` when an env var is EMPTY, so a worktree `.env` carrying
   the real dev `TURNOS_ADMIN_TOTP_SECRET`/Turnstile keys makes the seeded
   e2e admin login 401 with `{"need_totp": true}` even though the harness
   blanks those vars in the subprocess env. Blank them in the worktree `.env`
   (gitignored) before running `npm run e2e`.

**The pilot (below) is DONE (2026-08-05)** — see its section above for the
full retrofit + both acceptances' evidence (shas, diff-stats, live-proof
results). None of the deviations above closed the framework/pre-move gate
by themselves; the pilot is what exercised the contract end-to-end on a
real module, not just the mechanics proven here.

## Test plan (task #3 — contract tests, extend don't duplicate)

- `test_module_manifest.py`: v2 schema validation (normalization round-trip,
  including the `after` splice anchor's string/dict shapes), route
  dotted-paths importable, no route double-claims across v2 manifests,
  `requires` refer to real modules, no cycles.
- NEW `test_route_order.py`: the golden-order proof — routing.py's built
  GET/POST/PUT/DELETE registries equal the literal pre-Tier-2 sequences
  (`git show 347a9f7:routing.py`) name-for-name, plus `merge_routes()`'s
  splice algorithm in isolation (tail default, same-pass chain resolution,
  unresolved-anchor degrades gracefully).
- `test_module_runtime.py`: disabled → **404** (the product owner's decision, 2026-08-05);
  broken-module boot survival (inject a manifest with an unimportable route
  module into a temp modules dir → server boots, module listed broken);
  enable/disable dependency refusal with reason (synthetic fixture); live
  enable loads routes without restart.
- NEW `test_module_lifecycle.py`: transactional lifecycle — a module already
  broken by any signal never starts; a startup() that registers a hook then
  raises rolls the registration back; `hooks.call()` requires healthy, not
  merely enabled; `enable()` requires a dependency healthy AND started;
  topological/reverse-topological startup/shutdown order; the REAL
  intake→turnos dependency edge at the HTTP level.
- `test_module_nav.py`: `GET /api/nav` reflects enablement; `GET /api/admin/
  modules` carries v2 section fields, role-filtered per `sections_payload_
  for_role()` (`test_app_modules.py`'s `TestAdminModulesEndpoint*` classes —
  a `medico` session gets exactly its allowed sections and no `modules` key,
  proven both with a mocked and a REAL `admin_auth.require_admin()`).
- Frontend (`node --test`): SanaAdminRouter register/lookup unit tests;
  `SanaAdminSections.otras()` derivation, the shrunk `PLATFORM_SECTIONS` floor
  vs. the `FAILSAFE_SECTIONS` network-failure net, and the `js` dynamic
  `<script>`-loading pipeline (dedup by URL, READY gated on load completion);
  nav.js build-from-JSON with fallback.
