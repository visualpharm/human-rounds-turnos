# Tier 5 — frontend + design system (design)

Status: BINDING design for Tier 5 (`open-source-readiness-plan.md`, "Tier 5 —
frontend + design system (Phase 4)"). Written 2026-08-06 against `origin/main` at
`f693a2c` (Tier 2 module contract, Tier 3 test harness, and Tier 4 slices 0-6 all
implemented — slices 7-8 of Tier 4 still open-ended). Same contract as
`module-contract-design.md`, `tier3-harness-design.md`, `tier4-replatform-design.md`:
what exists today and is NOT re-done, per-area design with concrete mechanisms,
explicit out-of-scope, sequenced slices each independently landable with its own
gate, then a risk register and the decisions only the product owner can make. This doc merges
into `docs/architecture/` once implemented; until then it is what an implementation
is reviewed against.

**This is an architecture document, not a build order.** No code in this tier is
written from this doc directly — each slice below gets its own short implementation
pass, gated on the previous slice's acceptance criteria and on `scripts/ci_local.sh`
staying green throughout.

**Precise milestone language (Codex audit, 2026-08-06)**: what has landed as
of the "Slice 3 addendum" below is **mechanisms + one pilot**, not Tier 5
completion — `open-source-readiness-plan.md`'s Status section keeps Tier 5
unchecked for exactly this reason. Two ESM facades, pinned vendored
Preact+HTM, one calendar island, `tokens.css`, the screen-manifest contract,
and the `/admin/entrevistas` pilot all carry tests and are real, not
cosmetic — but `admin.html` is still 4,449 lines with roughly 465 tracked
`window.Sana*`/`globalThis.Sana*` references and roughly 110 hash-router/
OTRAS references. The three pilot defects the audit named were repaired
the same day (commits 7dfb0896/a9ebb58f/8e310d43): the server-routed
screen now renders hash-free via `window.SANA_ADMIN_SCREEN` (a guard test
forbids `location.hash =` in every screen template), the first-login race
is closed by `SanaAdminSections.refresh()` after auth, and the shared
shell gained a real narrow layout verified by a 390px browser assertion
plus a CSS anti-drift guard. Still open: story-per-component is not
enforced, and hosted Chromatic has never executed (GitHub Actions
billing-frozen). Do not retire the legacy hash shell until every enabled
screen has a direct-URL twin and ASGI is permanent for its real users.

## What exists today and is NOT re-done

- **The module contract (Tier 2/4)** — `app_modules.py`'s loader, `module.json` v2
  schema (`routes`, `admin_sections`, `nav_entries`, `locales`, `hooks`), the
  `after`-anchor splice algorithm, `router_attr` route entries mounted via
  `mount_module_routers()`/`merge_app_routers()` (Tier 4 slice 3),
  `require_module_enabled()` gating, `GET /api/admin/modules` / `GET /api/nav`.
  Verified against the real pilot: `modules/intake/module.json` already carries
  BOTH the legacy `{"module": "...interview_patient", ...}` route entries and their
  `router_attr` FastAPI siblings (`interview_patient_router.py` etc., never the
  same file — importing `fastapi` from a file dark mode loads unconditionally would
  leak the dependency into every boot), plus an `admin_sections` entry shaped
  `{"id": "entrevistas", "label_key": ..., "order": 40, "js": "modules/intake/admin-entrevistas.js"}`.
  Tier 5 EXTENDS `admin_sections`, it does not replace it — see "Module contract
  extension" below.
- **The strangler coexistence mechanism (Tier 4)** — `TURNOS_ASGI` (default off
  everywhere except a developer's own machine) selects between the untouched
  legacy `ThreadingHTTPServer` and the FastAPI/uvicorn front door with a reverse
  proxy for unmigrated routes. No Coolify environment has flipped it on yet; the
  demo-soak-then-prod-cutover decision from Tier 4's own "Decisions for the product owner" is
  still open. **This matters for Tier 5's own sequencing** — see "Sequencing
  against Tier 4's flag" below, the single biggest structural decision in this doc.
- **`admin.html` (4364 lines) + ~40 `admin-*.js` sibling files** — a hand-rolled
  hash router: `location.hash` → `routeCurrentAdminScreen()` → `SanaAdminRouter`
  registry lookup (each section self-registers on script load, replacing an
  earlier if/else chain per `module-contract-design.md`). Sidebar entries and
  each router's "hide everything else" list both derive from
  `SanaAdminSections.otras(mineId)` (fed by `GET /api/admin/modules`), guarded by
  `.claude/hooks/admin-section-wiring-guard.py` (advisory: warns if a new
  `<div id="sec-X">` has no matching `SanaAdminRouter.register("X", ...)`).
  **This IS the thing Tier 5's roadmap bullet names for deletion** — "hash router,
  OTRAS lists and their guard hook deleted" — but deletion is the LAST slice, not
  the first; see sequencing below.
- **~61 root-level UMD `window.Sana*` JS files.** Two real patterns exist today,
  confirmed by reading the source, not assumed:
  1. **True dual UMD** (the majority; every file with a `test/*.test.mjs` uses
     this shape) — `(function(root, factory){ var api = factory(); if (typeof
     module !== "undefined" && module.exports) module.exports = api; else
     root.SanaX = api; })(...)` (verbatim from `admin-charts.js`). Safe in both
     Node (`require()`) and the browser (`window.SanaX`) today, by construction.
  2. **Browser-only global IIFE** (e.g. `calendar-grid.js`) —
     `(function(root){ ...; root.SanaCalendar = {...}; })(window)`. `window` is
     referenced unconditionally, so this pattern would throw
     `ReferenceError: window is not defined` under plain Node — these files are
     consumed only via Storybook/Vite (a real browser context) or the live page,
     never `require()`'d directly.
  Both patterns write to a global; neither has a real ES `export`. This is the
  concrete gap Tier 5's "ES modules + import maps" bullet closes — see the ESM
  section below for the chosen mechanism.
- **`package.json` has NO `"type": "module"`** (CLAUDE.md's own documented
  landmine: setting it would make Node parse every plain `.js` as ESM, silently
  breaking `module.exports` for all 144 `require()`-based frontend tests). This
  constraint is NOT relaxed by this design — the mechanism chosen below works
  entirely within it.
- **Storybook** (`@storybook/html-vite`, config in `.storybook/*.mjs` — ESM,
  legal because Storybook's own config files are theirs, not the package's
  default) — 10 story files, each importing a real `window.Sana*` module and
  calling its actual render function (`stories/CalendarGrid.stories.js` does
  `import "../calendar-grid.js"; window.SanaCalendar.gridHtml(...)`) — never
  reimplemented markup. `preview.mjs` toggles `data-theme` on `<html>` via a
  toolbar control, defaulting to `humanrounds` (the public brand). Chromatic runs
  on every push, skipping itself with no token configured.
- **Two live themes, one CSS file.** `human-rounds.css` implements theming with
  `:root[data-theme="pinamar"]` / `[data-theme="humanrounds"]` blocks — NOT two
  deployments, NOT two code branches, purely CSS custom properties, per
  `design-icons-y-temas.md`. `docs/design-system/tokens/{colors,typography,
  spacing,effects,fonts,specialty-palettes}.css` is a SEPARATE, already-known-stale
  offline mirror of the Claude Design "Pinamar design system" project — the file
  itself documents that it diverges from Sana's real palette. Each theme has its
  OWN Claude Design project (`design-icons-y-temas.md`'s project-id table);
  `design-loop.json` only points at Pinamar's by default.
- **A real lesson already paid for**: `docs/solutions/design/marca-por-tema-copias-a-mano.md`
  — a theme switch to `humanrounds` still showed the OLD brand because favicons
  (`<link rel=icon>`, hardcoded per page), font-family lists in `admin.html`
  (loading all 4 fonts from both themes), and a hardcoded accent copy in
  `branding.py`'s email path are NOT CSS custom properties and a token file
  cannot fix them by construction. **tokens.css becoming "the published design
  contract" does not retroactively solve this class of bug** — it narrows the
  surface that can drift, it doesn't eliminate hand-copied brand values
  elsewhere. Recorded here so nobody claims the tokens.css slice below closes
  this lesson; it doesn't, and isn't meant to.

## Sequencing against Tier 4's flag — the honest answer

Tier 4's own design explicitly punted this: "the admin shell keeps being served
as today's single HTML file with inline routing until Tier 5 starts, whether the
routes behind it are legacy or FastAPI" (Tier 4, "Out of scope"). Tier 5 has to
resolve it for real, because a server-routed `/admin/<screen>` page is, by
definition, a route — and every route in this codebase today is either legacy
(`routing.dispatch()`) or FastAPI-mounted-behind-`TURNOS_ASGI`. There is no third
transport.

**Decision: `/admin/<screen>` pages are FastAPI-only, mounted the same way Tier 4
slice 3 mounts JSON routers (`router_attr` + `Depends(require_module_enabled(...))`),
rendering Jinja templates instead of JSON.** Reasoned, not a default:
- Building a SECOND server-side templating path for admin screens on the legacy
  `BaseHTTPRequestHandler` stack (to avoid depending on `TURNOS_ASGI`) would mean
  writing the Jinja integration TWICE — once for legacy, once for FastAPI — the
  exact "doubles the surface that can drift" trap Tier 4's own auth design
  rejected for `fastapi-users`. Tier 4 already built `jinja_env.py`, the
  `i18n_middleware.py` request-binding, and the `router_attr`-mounted-router
  pattern; Tier 5 spends that investment instead of re-deriving a parallel one.
- **The direct consequence: a server-routed admin screen is invisible with
  `TURNOS_ASGI` off.** This means Tier 5's admin-screen migration and Tier 4's
  own flag-flip timeline (dev → demo-soak → prod cutover, still an open
  "Decisions for the product owner" item in that doc) are now COUPLED — Tier 5 cannot put a
  single real screen in front of Pinamar's staff in production until Tier 4's
  flag is on in prod. This is stated explicitly because Tier 4's design asked
  for exactly this kind of coupling to be named, not discovered later.
- **What this does NOT block**: Tier 5 slices 0-3 below (ESM facades, tokens.css,
  islands mechanism, the FIRST server-routed screen) can all be built and proven
  entirely in dev, and the demo can carry `TURNOS_ASGI=1` on its own schedule
  independent of prod (Tier 4's own soak already runs there first) — so Tier 5's
  early slices do not literally wait on the product owner's prod cutover decision, only the
  LAST slice (deleting the legacy hash router for real) does.
- **The legacy hash-router admin.html is therefore not a short-lived shim to
  delete quickly — it is a long-lived coexistence surface**, symmetric to Tier
  4's own reverse-proxy: every admin section keeps its hash-routed form, fully
  functional, for as long as `TURNOS_ASGI` might be off anywhere staff use it.
  A section's server-routed twin is additive, never a replacement, until BOTH
  (a) every enabled section has one and (b) the flag is permanently on where
  that section's users are.

## ES modules + import maps — the concrete mechanism

**Recommended: a hand-authored `.mjs` ESM facade sibling per canonical UMD `.js`
module. The canonical `.js` file is untouched.** This is the "pick one with
reasons" the roadmap asks for, chosen over the two alternatives:

- **Rejected: dual-build (bundler emits both ESM and CJS from one source).**
  Rejected outright by this tier's own "vendored ESM, no build step" constraint
  for the islands work — introducing a build step for 61 small files just to
  avoid hand-writing a five-line facade each is a worse trade than the facade
  itself, and it would be the FIRST build tool this repo has ever needed (same
  "don't add a new paradigm for marginal gain" reasoning Tier 4 used to reject a
  lockfile tool swap).
- **Rejected: import-map-only in the browser, keep tests CJS, no facade file at
  all (i.e. import the `.js` file directly via the map).** Doesn't work for
  either UMD shape above: the file has no `export` statement, so
  `import { gridHtml } from "./calendar-grid.js"` fails — a bare `import
  "./calendar-grid.js"` (side-effect only) DOES execute correctly in a browser
  (it just runs the IIFE, which still writes to `window`), but gives the
  importer no named bindings, defeating the entire point of "ES modules replace
  `window.Sana*`."
- **The facade, concretely** (mechanically identical for both UMD shapes found
  above):
  ```js
  // calendar-grid.mjs — ESM facade over the canonical calendar-grid.js.
  // Browser-only: importing a script with no `export` still executes it (the
  // IIFE still assigns window.SanaCalendar) — this file just re-exports that
  // global as real ES bindings. Node never imports this file; test/*.test.mjs
  // keeps require()-ing calendar-grid.js directly, unaffected.
  import "./calendar-grid.js";
  export const { gridHtml, css } = window.SanaCalendar;
  export default window.SanaCalendar;
  ```
  **Why this is safe by construction, not by discipline**: the facade's `import`
  is a side-effecting import of the SAME file Node already requires — the UMD
  file keeps assigning `window.SanaX` regardless of whether anything ever loads
  the facade. Every existing call site that reads `window.SanaCalendar.gridHtml(...)`
  directly (admin.html inline scripts, other UMD modules) keeps working
  unmodified, forever, whether or not that module has been given a facade yet.
  This is the same "strangler, nothing breaks mid-migration" property Tier 4's
  reverse proxy has, applied to the frontend: a facade is purely additive.
- **`package.json`'s `"type"` field stays absent, permanently.** `.mjs` is
  unconditionally ESM to Node regardless of the `"type"` field; `.js` stays
  unconditionally CJS because the field is absent. The two file families never
  fight over interpretation — this is the actual mechanism that makes "keep
  `package.json` CJS-default" and "have real ES modules" simultaneously true.
- **Drift guard, not a generator.** A facade is hand-written (mechanical,
  ~5 lines, unlikely to need a generator for ~61 files that rarely gain new
  exports) but a new test (`test/esm-facade-parity.test.mjs`) diffs
  `Object.keys(window.SanaX)` (loaded the same way `test/*.test.mjs` already
  loads these files, via a jsdom-free `vm`/global shim or a headless check) against
  each facade's named exports, per migrated module — catches the UMD source
  adding an export the facade forgot to re-export, cheaply, in the existing
  frontend suite.
- **Production consumption**: a single import map, declared once in the shared
  page shell(s) (`patient_site._shell()`, the admin base Jinja template once it
  exists), maps bare specifiers to fingerprinted facade URLs —
  `{"imports": {"sana/format": "/static/format.<hash8>.mjs", ...}}` — reusing
  Tier 4 slice 6's `static_fingerprint.py` MIGRATED set (grown by one entry per
  facade created, the same "mechanical repetition, no new design decision"
  follow-up slice 6 already established for `.js`/`.css`).
- **Storybook migrates opportunistically, not in a single sweep**: a story
  updates from `import "../calendar-grid.js"` to `import { gridHtml, css } from
  "../calendar-grid.mjs"` the moment that module gets a facade — proves the ESM
  path end-to-end (Vite already handles `.mjs` natively) without a coordinated
  rewrite of all 10 stories at once.

## `admin.html` decomposition into `/admin/<screen>` — the concrete mechanism

**Per-section migration, module-contract-driven, mirroring Tier 4's own
route-by-route strangler discipline — never a whole-shell rewrite.**

- **Manifest extension**: `admin_sections` entries grow an optional `screen`
  object, superset-tolerant per the existing schema's own design (the same way
  `routes` entries grew `router_attr` in Tier 4):
  ```json
  {"id": "entrevistas", "label_key": "intake.nav.entrevistas", "order": 40,
   "js": "modules/intake/admin-entrevistas.js",
   "screen": {"path": "/admin/entrevistas", "router_attr": "screen_router",
              "template": "modules/intake/admin_entrevistas.html"}}
  ```
  An entry with no `screen` key keeps being a hash-only section (today's
  behavior, unaffected) — this is additive per-section, exactly like a
  route entry with no `router_attr` keeps flowing through the legacy path.
- **A `screen_router` module is its OWN sibling file**, never the JSON API
  router and never the legacy handler — same Tier-4-slice-3 finding restated:
  importing FastAPI/Jinja machinery from a file dark mode loads unconditionally
  would leak that cost into every boot. `modules/intake/admin_entrevistas_screen.py`
  defines `screen_router = APIRouter()`; its one route calls
  `templates.TemplateResponse("modules/intake/admin_entrevistas.html", {...})`,
  built from the SAME data functions `admin-entrevistas.js`'s client-side fetch
  already calls (no new business logic, a rendering-target change only).
- **The sidebar becomes server-rendered for migrated screens, which is a real
  simplification, not just parity.** Today's sidebar is built client-side after
  a `GET /api/admin/modules` fetch resolves (`SANA_ADMIN_SECTIONS_READY`,
  `FAILSAFE_SECTIONS` as the no-blank-sidebar net for the race). A Jinja base
  admin template instead loops over `app_modules.sections_payload_for_role(rol)`
  — the EXACT function that endpoint already calls — server-side, at request
  time: the sidebar is correct in the FIRST response byte, no fetch race, no
  failsafe list needed for any screen that has fully migrated. The legacy
  hash-routed shell keeps its current client-fetch sidebar unchanged for as
  long as ANY section hasn't migrated (the two sidebars coexist — a staff member
  navigating a migrated screen sees the server-rendered nav; navigating back to
  an unmigrated hash section returns to the old shell with its own sidebar).
- **Nav contribution reuses `nav_entries` for the PATIENT site and the new
  `admin_sections[].screen` for admin — no third registry invented.**
- **Legacy and server-routed forms of the SAME section coexist deliberately,
  proven by an acceptance test per section, not deleted on landing.** A
  section's server-routed twin ships; the hash-routed original is deleted ONLY
  once (a) the twin is proven (an e2e case exercising the real staff action
  through the new URL) and (b) `TURNOS_ASGI` is on wherever that section's real
  users are (dev first, then demo, then — per Tier 4's own still-open
  soak/cutover decision — prod).
- **Guard hook lifecycle**: `.claude/hooks/admin-section-wiring-guard.py` keeps
  running, checking the OLD contract, for exactly as long as ANY section is
  still hash-routed-only — deleting it early reopens the blank-screen bug it
  exists to catch for every section not yet migrated. It is retired in the same
  slice that deletes the hash router itself (see Execution slices, last item).

## Interactive screens as Preact+HTM islands

- **Vendored, pinned, committed — not a CDN fetch at runtime.** Preact ships an
  official ESM build (`preact/dist/preact.module.js`) and `htm` is a zero-dependency
  tagged-template library with its own ESM build; both are downloaded once,
  version-pinned, and committed under `static/vendor/` (mirrors this repo's
  existing "zero runtime dependency surface beyond what's actually shipped"
  ethos — Tier 4 used the identical framing for Python deps). A hospital-network
  CDN block becoming a production outage is the concrete risk this avoids.
- **Import map entries**: `"preact": "/static/vendor/preact.<hash8>.module.js"`,
  `"preact/hooks": "...", "htm": "..."` — fingerprinted the same way the ESM
  facades are (same MIGRATED-set mechanism, Tier 4 slice 6).
- **Islands bootstrap** (`islands.mjs`, canonical ESM — its temporary
  `window.SanaIslands` bridge was retired after the doctor editor imported it
  directly): scans `[data-island]` elements after DOM ready, resolves
  `data-island="AdminQueue"` to a dynamic `import("/static/islands/AdminQueue.<hash8>.mjs")`,
  and calls that module's default-exported `mount(el, props)` — `props` comes
  from a `data-props='{...}'` JSON attribute the server already rendered (no
  extra fetch to hydrate).
- **An island wraps an existing render function; it does not reimplement
  markup.** E.g. an `AdminQueue` island's `mount()` calls the SAME
  `window.SanaSolicitudes` string-returning render (via its ESM facade) for the
  actual card/row markup, and adds only what today's `admin.html` hand-rolls as
  imperative `STATE`/`render()`/`wire()` calls: Preact's diffing takes over
  the re-render-on-state-change loop, `htm` supplies `dangerouslySetInnerHTML`-style
  injection of the existing HTML-string output where a full JSX rewrite isn't
  warranted. This preserves the Storybook doctrine (a story still calls the
  real render function, an island is a thin interactivity shell around it, not
  a fork of the markup) and avoids doubling the amount of code that can drift
  from a redesign pull.
- **Scope discipline for elderly/low-vision patients (patient-flow-design)**:
  islands are for INTERACTIVITY, never for a patient page's PRIMARY content.
  A patient-facing template that needs an island (e.g. a slot picker with live
  re-filtering) still server-renders the full initial slot grid in HTML on
  first paint — the island only attaches richer interaction on top of markup
  that already existed and was already visible without JavaScript. No patient
  page ever mounts an island against an empty `<div>` and fetches its first
  content client-side. This is enforced by convention plus an explicit review
  item (see risk register) — admin screens, where staff hardware and literacy
  assumptions differ, have no such restriction.

## Module contract extension — where it lands (recap, ties the above together)

The Tier 2 acceptance property — disable a module → its screens/routes 404, its
nav entries disappear, zero central edits — extends to a THIRD contract shape
(JSON routers, now screen routers) the same way Tier 4 slice 3 extended it from
one to two: `admin_sections[].screen.router_attr` is mounted by the SAME
`mount_module_routers()`/`merge_app_routers()` Tier 4 already built, gated by the
SAME `Depends(require_module_enabled(id))` dependency. No new loader concept —
a screen router IS a FastAPI router; the only thing new is that its handler calls
`TemplateResponse` instead of returning JSON, which Tier 4's own adapter never
precluded.

## tokens.css as the published design contract

- **Extraction, not invention.** `human-rounds.css`'s `:root[data-theme="pinamar"]`
  / `[data-theme="humanrounds"]` custom-property blocks move, verbatim, into a
  new `tokens.css`; `human-rounds.css` becomes `@import url("tokens.css");`
  followed by everything that isn't a token (component rules, layout). The
  served `<link>` every page already references does not change name or count —
  no `?v=`/fingerprint churn beyond the one file that already gets bumped.
- **The stale offline mirror (`docs/design-system/tokens/*.css`) is retired, not
  kept in parallel.** It already documents its own divergence from the real
  palette; keeping two "canonical" token sources is the exact class of drift
  this slice exists to remove. `design-loop.json`'s design-system-sync step is
  repointed at the real `tokens.css` (diffing the CD project's exported tokens
  against THIS file, not a hand-maintained copy that nobody re-syncs).
- **Both themes stay ONE file, ONE selector scheme** — `tokens.css` is not
  split per-theme; splitting it would reintroduce exactly the "two deployments"
  shape `design-icons-y-temas.md` explicitly says this product does NOT have.
- **What this deliberately does not fix**: see "A real lesson already paid for"
  above — favicons, hardcoded font-family lists, and `branding.py`'s email
  accent copy are real brand surfaces outside CSS custom properties. This slice
  narrows the CSS side of the surface; it is not a rebrand-safety guarantee.

## Storybook / story-per-component as definition of done

- **Stays load-bearing, unchanged in spirit.** Stories keep calling real
  render functions — the only mechanical change is WHICH import syntax they
  use once a module gets an ESM facade (see above), never a switch to
  reimplemented markup.
- **"Story-per-component" becomes a gate, not an aspiration**: a NEW
  `window.Sana*` module that owns a `css` string (the `calendar-grid.js` shape
  CLAUDE.md already names as the bar for story-ability) is not "done" without a
  matching `stories/*.stories.js`. Enforced the same low-friction way
  `admin-section-wiring-guard.py` enforces its own contract: a new advisory
  PostToolUse hook (`.claude/hooks/story-coverage-guard.py`) that warns
  (never blocks) when a saved file defines a new `var css = "..."`-owning
  `window.Sana*` export with no story file referencing it.
- **Coverage expansion is open-ended**, same "no fixed target count" pattern
  Tier 3 and Tier 4 both used for their own follow-up work — this design does
  not enumerate which of the ~40 `admin-*.js` files get a story first beyond
  naming the pilot in the slices below.

## Test harness extension (Tier 3 survives, two additive fixtures)

- **The e2e Playwright tier needs zero changes for the ESM/facade/tokens work**
  — it drives a real browser, which resolves import maps and `<link>` CSS
  identically regardless of internal module mechanics.
- **The e2e tier DOES need one new capability for the islands/screen-migration
  work**: a per-migrated-screen case exercising the real staff action through
  BOTH the legacy hash URL and the new `/admin/<screen>` URL, while
  `TURNOS_ASGI=1` — mirrors Tier 4's own `harness.mjs` conditional spawn
  (`TURNOS_ASGI=1` → `asgi_main.py`) already built for exactly this dual-mode
  proof.
- **New frontend-suite addition**: `test/esm-facade-parity.test.mjs` (drift
  guard, described above) — additive, runs alongside the existing 878 frontend
  cases, never replaces the `require()`-based ones.
- **No new database or record-replay fixture flavor is needed.** Frontend/UI
  work does not touch HSI/PUCO/RENAPER/Meta cassettes or the routes-tier
  worker-DB pool.

## Out of scope for this design

- **Rewriting `admin-*.js` render logic in JSX/Preact components.** Islands wrap
  existing string-returning renders; a full component rewrite is a separate,
  much larger and unbudgeted effort with no roadmap mandate.
- **Migrating every one of the ~61 UMD modules to a facade in one slice.**
  Mechanical, per-module, open-ended — same posture as every other "coverage
  expansion" item in Tiers 3 and 4.
- **Migrating every admin section to a server-routed screen.** Per-section,
  gated on `TURNOS_ASGI`'s own rollout — see sequencing above.
- **Flipping `TURNOS_ASGI` to prod.** That decision belongs to Tier 4's own
  "Decisions for the product owner," not this doc; Tier 5 is a downstream consumer of it,
  not the owner of the timing.
- **A general-purpose component framework or router for the PATIENT site.**
  Patient pages stay server-rendered HTML per patient-flow-design; islands are
  admin-first, patient-scoped only for genuine supplementary interactivity on
  top of already-rendered content.
- **Rebranding surfaces outside CSS** (favicons, `branding.py`'s email accent,
  hardcoded font lists) — named above as explicitly NOT solved by tokens.css.
- **A bundler/build step of any kind.** Explicitly excluded by the roadmap's own
  "vendored ESM, no build step" wording; revisit only if the vendored-file count
  or the manual-facade burden genuinely becomes unmanageable, which this design
  does not expect at this repo's size.
- **Deleting `admin.html` or the hash router before every section has a proven
  server-routed twin AND `TURNOS_ASGI` is permanent where that section's real
  users are.** Named explicitly so a later slice doesn't "clean up" early.

## Execution slices (sequenced, each independently landable, one gate each)

0. **ESM facade mechanism, proven on one pilot module.** `format.js` (smallest,
   most heavily tested, true dual-UMD already) gets `format.mjs`; one story
   (`stories/Formatters.stories.js`) switches its import to the facade.
   Gate: `test/esm-facade-parity.test.mjs` (new) passes for `format`; existing
   `format.test.mjs` unaffected; the story renders identically in Chromatic,
   both themes; `package.json` diff shows zero change to `"type"`.
1. **`tokens.css` extraction.** Token blocks move out of `human-rounds.css`
   verbatim; `docs/design-system/tokens/*.css` retired; `design-loop.json`
   repointed. Gate: Chromatic shows ZERO visual diff across all 10 existing
   stories in both toolbar themes — a byte-for-byte computed-style guarantee,
   not a subjective review.
2. **Fingerprinting grown to cover `.mjs` facades and vendored JS**, mechanical,
   reuses Tier 4 slice 6's `MIGRATED` set pattern with no new serving code.
   Gate: `scripts/smoke_prod.py`'s existing fingerprinted-asset loop passes for
   the new entries once deployed.
3. **Islands bootstrap, one pilot island, inside the STILL-LEGACY admin.html.**
   Vendor Preact+HTM; `islands.mjs`; pick the smallest genuinely-interactive
   existing admin screen with the fewest external dependents as the pilot
   (candidate: the doctor Calendario tab, already isolated per
   `admin-cola-y-agenda.md`) and re-implement ONLY its interactivity as an
   island wrapping its existing render function — routing untouched this
   slice. Gate: a Playwright case drives the island's interaction (click a
   day, see the month re-render) with zero markup regression versus the
   pre-island screenshot.
4. **Manifest `admin_sections[].screen` + FIRST server-routed `/admin/<screen>`
   pilot**, on `intake` (same module Tier 2's manifest pilot and Tier 4's
   `router_attr` pilot both used — its FastAPI routes already exist from Tier 4
   slice 3, so this slice adds only the template/screen layer on top of
   already-proven routes). Legacy `#entrevistas` hash section keeps working
   unmodified. Gate: disable `intake` → both the old hash section AND the new
   `/admin/entrevistas` route disappear/404 with zero central edits outside
   `modules/intake/`; an e2e case proves the real staff action through the NEW
   URL with `TURNOS_ASGI=1`.
5. **Progressive admin-section migration, open-ended.** Each section: a
   `screen` manifest entry, a template reusing existing ESM-faced render
   modules (optionally via an island from slice 3's mechanism), an e2e
   acceptance case. No fixed order or count — same "coverage expansion, no
   quota" posture as Tier 3's routes tier and Tier 4's slice 8. Each section's
   hash-routed original is deleted only when ITS twin is proven AND
   `TURNOS_ASGI` is on wherever that section's real users are.
6. **Hash-router retirement.** Once every enabled module's every admin section
   has a proven server-routed twin, and `TURNOS_ASGI=1` is permanent in prod:
   delete `SanaAdminRouter`, `routeCurrentAdminScreen()`,
   `SanaAdminSections.otras()`, `FAILSAFE_SECTIONS`, and
   `.claude/hooks/admin-section-wiring-guard.py`; `admin.html` itself shrinks to
   whatever thin shell (if any) migrated screens still share, or is deleted
   outright if the base Jinja template fully replaces it. Gate: full
   `scripts/ci_local.sh` green; a direct load of every migrated screen's URL
   (not just the hash) works cold, including a reload; no remaining
   `location.hash` admin-routing code in the tree (grep-verified).
7. **Story-coverage gate turned on for real.** `.claude/hooks/story-coverage-guard.py`
   ships; an initial sweep adds stories for the highest-traffic
   still-uncovered `window.Sana*` modules (open-ended target, judged at
   implementation time same as every other coverage slice in this tier).

## Risk register

| Risk | Blast radius | Mitigation | Rollback |
|---|---|---|---|
| A facade's re-exported keys drift from the UMD source's actual API (someone adds a function to the `.js`, forgets the `.mjs`) | An ESM consumer silently gets `undefined` for a function that exists on `window.SanaX` | `test/esm-facade-parity.test.mjs` diffs the two export sets per migrated module, in the existing frontend suite | Delete the stale facade; the UMD global keeps working for every non-ESM consumer regardless |
| Import maps unsupported on an old hospital-desktop browser | A migrated page's ESM-only script fails to load | Feature-detect (`HTMLScriptElement.supports('importmap')`) before relying on it; the legacy hash-routed admin.html never depends on import maps at all, so it's unaffected regardless of browser age | Serve the pre-facade UMD `<script>` tags unconditionally for any page that hasn't proven import-map support site-wide |
| Two admin sidebars (client-fetched for legacy, server-rendered for migrated) drift in content or ordering during the long coexistence window | A staff member sees a different nav depending on which screen they're on | Both read the SAME `app_modules.sections_payload_for_role()` function — one calls it over HTTP, one calls it in-process; a shared contract test asserts identical payloads for a fixed role set | Coexistence is temporary by design (slice 6 removes one side entirely) |
| An island is used for a patient page's PRIMARY content, silently regressing elderly/low-vision users (violates patient-flow-design) | A patient without JS, or with a slow connection, sees a blank div instead of the appointment content | Explicit review item on every island PR touching a patient-facing template: does the SAME content already render server-side without the island? No automated gate proposed here — flagged as a manual review discipline, not a test, because "primary content" is a judgment call | Revert the island; the server-rendered HTML underneath was never removed if the convention was followed |
| Server-routed `/admin/<screen>` work is entirely gated on `TURNOS_ASGI`, which has no committed prod timeline | Tier 5's admin-facing slices (4 onward) cannot reach real Pinamar staff until Tier 4's flag flips in prod | Slices 0-3 (facades, tokens, islands mechanism) are NOT gated and deliver real value independently; slice 4's pilot can fully prove itself on demo, which can flip the flag on its own schedule ahead of prod | None needed — this is a sequencing fact, not a bug; if the product owner wants Tier 5's admin screens live in prod sooner, the lever is Tier 4's flag decision, not this design |
| Vendored Preact/HTM files go stale (a security fix ships upstream, nobody notices because there's no package-manager update path) | A known JS vulnerability sits unpatched in a committed file | Same posture this repo already has for pinned Python deps (`requirements.txt`, hand-checked) — no new problem, just a new file family to include in that existing habit | Re-vendor the fixed version; it's a file swap, not a dependency-graph change |

## Decisions for the product owner

- **Q: Confirm the sequencing coupling — Tier 5's server-routed admin screens
  ship dark (dev/demo only) until Tier 4's own `TURNOS_ASGI` prod-cutover
  decision lands, rather than building a second legacy-mode template path just
  to go live in prod sooner?**
  A (recommended): yes, accept the coupling. A parallel legacy-Jinja path would
  double the templating surface this tier deliberately avoided doubling
  elsewhere (auth libraries, queue libraries) for a head start whose length
  depends entirely on a decision Tier 4 already flagged as pending.
- **Q: Preact + htm, vendored and committed, vs. some other no-build islands
  approach (e.g. hand-rolled reactivity with no library at all)?**
  A (recommended): Preact + htm — small (≈4 kB + 1 kB gzipped), no build step
  required for either, and battle-tested for exactly this "islands on top of
  server-rendered HTML" shape. A fully hand-rolled reactivity layer would be
  more code this repo has to maintain forever for no clear benefit over an
  established, tiny library.
- **Q: Which admin section is genuinely the best slice-3 islands pilot — is the
  doctor Calendario tab still the smallest isolated candidate, or has something
  changed since `admin-cola-y-agenda.md` was last updated?**
  A (recommended): Calendario, per that doc's own framing ("the tab renders a
  month-grid calendar," already visually and functionally self-contained) — but
  this is a judgment call about which screen has the fewest hidden couplings to
  `admin.html`'s shared `STATE`, worth a quick re-check against the CURRENT
  code right before slice 3 starts, not re-derived from memory here.
- **Q: Confirm `intake` as the slice-4 server-routed-screen pilot?**
  A (recommended): yes, unchanged from the slice description above — same
  module Tier 2's manifest pilot and Tier 4's `router_attr` pilot both already
  used, and its FastAPI routes already exist, so this slice adds only the
  template/screen layer on top of already-proven routes.
## Slice 3 addendum — implemented (vendored provenance, mechanism, pilot)

Recorded 2026-08-06, as the design's own "record provenance/URLs+hashes in
the design doc" instruction for the vendored-ESM constraint above requires.

**Vendored files** (`static/vendor/`, committed, no CDN, no build step):

| File | Package@version | Source URL | SHA-256 |
|---|---|---|---|
| `static/vendor/preact.module.js` | `preact@10.29.8` | `https://unpkg.com/preact@10.29.8/dist/preact.module.js` | `c30e721ebfdc6e2ad4c18c14d2dfb82667829c8aec27de1207774e3fc16858a8` |
| `static/vendor/hooks.module.js` | `preact@10.29.8` (hooks submodule) | `https://unpkg.com/preact@10.29.8/hooks/dist/hooks.module.js` | `a6ee626f2d01570592dd569a792e3f050154aa02890eead8c223fa3ed5aa3d5a` |
| `static/vendor/htm.module.js` | `htm@3.1.1` | `https://unpkg.com/htm@3.1.1/dist/htm.module.js` | `ab33dd3f38059b9be4d5f5350128eefb2356639c4e0bbe9d9e8b3ba75847e9e4` |
| `static/vendor/htm-preact.module.js` | `htm@3.1.1` (preact binding submodule) | `https://unpkg.com/htm@3.1.1/preact/index.module.js` | `b3f51db71c12eabba61819feb74ace7cadc2744af41866c3fcad2ab960415746` |

`preact@10.29.8` and `htm@3.1.1` were npm-registry `latest` at vendor time
(2026-08-06). The htm-preact binding is `htm` pre-bound to Preact's `h` (its
own source is `import{h as r,...}from"preact";...var m=e.bind(r);export{m as
html}` — three lines, no build step needed to get a ready-to-use `html`
tagged template) — vendoring it avoids every island hand-rolling its own
`htm.bind(h)`. All four files are the unmodified, minified ESM builds each
package publishes; none was hand-edited. Re-vendoring for a version bump is
a file swap (re-download, verify hash, replace) — no dependency-graph change,
matching the risk register's own rollback note for this row.

**Islands bootstrap mechanism** (`islands.mjs`, root-level, real ESM,
fingerprinted and resolved through `window.SANA_STATIC` by
`admin-content.js`; the raw root path is never served).
Scans `[data-island]`, resolves the name via `window.SANA_ISLAND_URLS`
(injected per-request by `booking_requests.py._serve_admin()`, same
mechanism as `window.SANA_COUNTRY_PHONE`), dynamic-`import()`s the
fingerprinted island URL, calls its `mount(el, props)`. `props` merges the
element's `data-props` JSON with an optional `options.props` override (JSON
can't carry a function; the pilot needs a live `onSelect` callback into
legacy code — see `admin-content.js`'s `wireMedicoCalendario`).
The module's named `{mount,scan,update}` exports are the one call path used both by
the automatic `DOMContentLoaded` scan and by legacy code inserting
`[data-island]` markup afterward (admin.html's tab panels are built by
client-side `innerHTML`, so they postdate that scan). `update()` buffers the
latest props in a `PENDING` WeakMap when called before `mount()`'s async
dynamic import resolves, applied the moment the controller exists — closes
the real race between a legacy `fetch()` finishing and the island's own
module load finishing.

**Fingerprinting**: `static_fingerprint.py`'s `MIGRATED` now carries the four
vendor files above, `islands/MedCal.mjs`, and `calendar-grid.mjs` (a new ESM
facade over `calendar-grid.js`, built for this pilot the same way
`format.mjs` was built in slice 0). `_physical_path`'s vendor-only prefix
generalized to `_NESTED_PREFIXES = ("vendor/", "islands/")`.
`_PLACEHOLDER_RE` widened to allow `/` and an optional `module.` infix, so
admin.html's import map can be built from `{{static.vendor/preact.module.js}}`-
shaped tokens. `calendar-grid.js` joined `_PASSTHROUGH` (same reasoning as
`format.js`: the facade's own `import "./calendar-grid.js"` resolves against
`/static/` once the facade itself is fingerprinted).

**Pilot**: `static/islands/MedCal.mjs` — the doctor Calendario tab's month
grid (`admin-content.js`'s `medicoCalendarioHtml`/`wireMedicoCalendario`/
`cargarMedicoCalendario`/`renderMedicoCalendario`). Wraps
`window.SanaCalendar.gridHtml` (via the `calendar-grid.mjs` facade,
`sana/calendar-grid` bare specifier) exactly as this doc's own "wraps an
existing render function" framing requires — the island owns ONLY day
selection (a `useState`-free controller with `mount()`/`update()`, since the
data source is a legacy client-side `fetch()`, not server-rendered props);
occupancy-color classification (`medCalGridDias`, née `medCalGridHtml`) and
everything below the grid (filters, sort, per-day appointment tables, the
solicitud modal, "Crear turno") stayed exactly where they were, legacy
imperative, untouched in behavior. `#med-cal-grid` (the island) and
`#med-cal` (legacy body) are SIBLING elements now, not parent/child — this
is what lets Preact own its subtree without the legacy `host.innerHTML = …`
full-body re-render (on every filter/sort/day-table change) blowing the
island away each time.

**Storybook**: `.storybook/main.mjs` aliases the five bare specifiers
(`preact`, `preact/hooks`, `htm`, `htm/preact`, `sana/calendar-grid`)
straight to the same on-disk vendored files/facade admin.html's import map
points at — one copy of Preact/HTM, not a second npm-installed copy that
could drift. `stories/MedCalIsland.stories.js` calls the real
`static/islands/MedCal.mjs` `mount()` export, next to the existing
`stories/CalendarGrid.stories.js` (the raw `gridHtml()` story it wraps).

- **Q: How long should a migrated section's hash-routed original stay alive
  after its server-routed twin is proven, before deletion — mirroring Tier 4's
  own open "how long does demo need to soak" judgment call?**
  A (recommended): don't pre-commit to a fixed number here — tie it explicitly
  to Tier 4's own soak decision once the product owner makes it, since both are the same
  underlying risk (staff-facing surface, hospital relationship) measured on the
  same clock, not two independent timers.
