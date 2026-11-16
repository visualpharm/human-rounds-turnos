# Native-route migration campaign — 2026-08-26

Baseline: commit `41b4e19c`, architecture census `adapted-legacy=283,
native-independent=9, rollback-only=4, dead-unreachable=0` (252 route objects).
Binding gate: `scripts/refactor_architecture_census.py --require-complete`
(vendored snapshot; the evolving master copy lives in the
`codex/architectural-acceptance-gate-20260826` lane — do not edit it here).

Goal: replace every adapter-backed route with an independent native FastAPI
implementation, family by family, without changing observable behavior.

## The migration pattern (every writer follows this)

A route is `adapted-legacy` when its endpoint's (or any Depends' ) static call
graph reaches `legacy_shim`, `core.routing`, `services.legacy_http`,
`services.legacy_static`, or `core.module_router._endpoint`. To retire a route:

1. **Split the legacy module** (`handle(handler, method)`-shaped) into pure
   domain functions (take parsed arguments, return data / raise typed errors)
   and a thin legacy transport layer. Keep `handle()` working by delegating to
   the extracted domain functions — the rollback topology (`server.py`) still
   dispatches through it, and shared domain code prevents drift.
2. **Rewrite the router file**: drop `module_router.route_via_handle`, write
   real `async def` endpoints that parse the request with FastAPI/Starlette
   natively, call the extracted domain functions (sync DB work through
   `starlette.concurrency.run_in_threadpool`), and build the `Response`
   directly. Same paths, same methods, same status codes, same bytes.
3. **Terminal contract**: where the legacy inner regex declined a path FastAPI
   had matched (e.g. non-numeric id), return the exact shared terminal 404:
   body `b'{"error": "not found"}'`, headers `Content-Type:
   application/json; charset=utf-8`, `Content-Length`, `Cache-Control:
   no-store`. Copy `_terminal_json_not_found()` semantics — do NOT import it
   from `core.module_router` (that keeps the module in your call graph is fine
   actually — importing the helper is allowed; only `_endpoint` is flagged —
   but prefer a local copy to keep ownership clean).
4. **Auth**: native staff endpoints use `core.deps.require_admin(...)` — after
   family F1 lands it is shim-free. Do not build ad-hoc auth.

## Hard bans

- No renamed wrappers, no additive twins, no copied legacy shells: the
  handler-shaped protocol (`send_response`/`wfile`/`handler.path` parsing)
  must genuinely not execute on the native path.
- Never delete or break the legacy `handle()` path — rollback still uses it.
- No pushes, no merges, no live servers, no deploys, no PHI in code, tests,
  fixtures, logs, or commit messages.
- Do not touch files owned by the live census lane: `core/module_router.py`,
  `modules/turnos/appointment_offer.py`, `scripts/legacy_fallback_census.py`,
  `scripts/route_parity_check.py`, `test/test_legacy_fallback_census.py`,
  `test/test_refactor_finish_line.py`, `docs/roadmap/refactor-finish-line.md`.

## Verification (before claiming done)

- `python3 scripts/census_family_check.py <family path prefix>` → every family
  route prints `native`; totals line shows adapted-legacy reduced by exactly
  the family's row count and native raised by the same; route_objects
  unchanged (`python3 scripts/refactor_architecture_census.py`).
- Existing targeted pytest for the touched modules passes:
  `python3 -m pytest test/<relevant files> -q` (no managed DB needed for
  offline suites; DB-needing tests may skip — name any you could not run).
- New/changed behavior gets a test: at minimum one offline test per migrated
  router asserting status/body parity for a representative request, using the
  same style as existing `test/test_*_router*.py` files.

## Families

| id | scope (router files → legacy modules) | approx rows |
|----|----------------------------------------|-------------|
| F1 | core/deps.py native require_admin; auth/admin_auth_router.py; auth/passkeys_router.py | ~33 (incl. ~22 dep-only screens) |
| F2 | patient_web/patient_site_router.py; patient_web/health_data_router.py | ~38 |
| F3 | admin_panel: admin_users, admin_patients, admin_appointments (+ legacy modules) | ~30 |
| F4 | admin_panel: reports, system_status (+ reports_store) | ~25 |
| F5 | admin_panel: settings_admin, admin_preferences, admin_content, admin_account, doctor_patients | ~25 |
| F6 | cuentas: accounts_router, account_photo_router, ocr_dni_router | ~28 |
| F7 | modules/turnos: booking_requests, slot_holds, searches, preparation, doctor_appointments, calendar_feed, appointment_confirm (NOT appointment_offer) | ~20 |
| F8 | ai: prompts, lab_results, chart_summary, ai_health, ai_costs | ~10 |
| F9 | auth/panel_login, auth/password_reset, core/site_identity_router, core/app_modules_router, connectors/puco | ~12 |
| W2 | wave 2: modules/chat + mcp, modules/intake, patient_web small routers, routers/native_terminal, appointment_offer, dark-only modules (ops.setup, core.legacy_urls, cuentas.auth_renaper), rollback artifact retirement | remainder |

Each family = one writer agent = one isolated worktree
(`turnos-claude-native-f<N>`), branch `claude/native-f<N>-<slug>-20260826`,
based on the integration branch tip. Integration merges one family at a time
into `claude/native-routes-integration-20260826`, re-running the census and
targeted tests after each merge. Full `local-ci` + `local-ci-asgi` run after
integration; the exact release candidate remains owned by the acceptance-gate
lane.

## Final wave (2026-08-27) — the executable legacy topology is deleted

Executed after merging `origin/main` (search info links, self-service doctor
photo, verified-2FA setup — each ported natively during the merge). This
wave supersedes the campaign-era hard ban "Never delete or break the legacy
`handle()` path — rollback still uses it": with every live route
native-independent and production/demo on `TURNOS_ASGI=1` since
2026-08-08/19, the rollback topology's retention was the LAST thing keeping
`--require-complete` red, and the acceptance card (tracking card,
2026-08-27-land-native-route-refactor...) explicitly binds completion to
zero rollback-only units.

Deleted: `server.py`, `ops/legacy_web_server.py`,
`routers/legacy_http_transport.py`, `legacy_shim.py`,
`services/legacy_http.py`, `services/legacy_static.py`, `core/routing.py`,
`core/module_router.py`, `scripts/route_parity_check.py` (its ESCENARIOS row
inventory lives on as pure data in `scripts/route_inventory_scenarios.py`).
Feature modules keep their `handle()` transports as unreachable source —
outside the census's scope by its own scope_note; bulk deletion is separate
follow-up work.

Test surface: `test/_livehttp.py`'s LiveServer serves the composed native
app (uvicorn, lifespan off); the e2e harness always spawns `asgi_main.py`;
the legacy-vehicle suites were ported to the native composition and the
suites whose subject WAS the deleted transport were retired (per-file record
in the wave's commit messages). `test_asgi_main_dark_mode_retirement.
TestLegacyEntrypointRetired` pins the deletion; the census reports
`complete=true`, 100% native-independent.

## Lifecycle decisions (W3b, 2026-08-26)

**Dark-only modules made natively routed.** `ops.setup` (`/setup*`, the
first-run installer wizard), `core.legacy_urls` (the old-Spanish-URL page-
redirect family), and `cuentas.auth_renaper` (`/auth/renaper*`, the
RENAPER/Mi Argentina federated login) were entirely absent from the native
ASGI graph — not reachable even through a legacy shim. Each module's
`handle()`-shaped transport code was split into pure/near-pure domain
functions (`page_*`/`process_*` for `setup`, `start_flow_decision`/
`callback_decision` for `auth_renaper`, the already-pure page tables for
`legacy_urls`) with new routers — `ops/setup_router.py`,
`cuentas/auth_renaper_router.py`, `core/legacy_urls_router.py` — calling the
SAME functions `handle()` now delegates to, mounted in
`routers/platform.py`'s `ORDERED` tuple. This is a deliberate route-object
count increase (route_objects grew; `test/test_app_composition.py`'s
fixture was regenerated via `--write-fixture`, per its own documented
escape hatch for a deliberate routing change).

`legacy_urls_router.py`'s scope is bounded to the PAGE-redirect family
(GET-only, self-contained 301s/404s, verified against the census
representative `GET /servicio/refactor-census`). `core.legacy_urls.handle()`
also rewrites old API/feed paths in place and lets the request continue to
whatever now owns the new path (`_new_api_path()`) — a transparent
pass-through, not route ownership. Replicating that natively would need
either an app-wide ASGI middleware (`app.py` is pinned under 100 lines by
its own composition-root acceptance test, with no margin left) or reaching
into every OTHER family's already-migrated routers to call their endpoints
under a second path — both out of this family's scope and not required by
the census (which only checks module reachability + the one representative
route). No native route serves the old API paths; dark mode remains their
only path, unchanged by this slice.

**Rollback artifact retired.** `asgi_main.py` no longer has a dark-mode
fallback: `_dark_mode()`, `import server`, and `server.main()` are gone from
its call graph. `TURNOS_ASGI` unset/not `"1"` now raises `SystemExit`
instead of silently running the pre-refactor topology
(`test/test_asgi_main_dark_mode_retirement.py` pins the new contract).
`server.py` itself is untouched — the legacy managed-CI topology still runs
it directly (`python3 server.py`, no `TURNOS_ASGI` involved); only
`asgi_main.py`'s OWN path to it is gone. Why now: production and demo have
run `TURNOS_ASGI=1` since 2026-08-08/2026-08-19 (see `asgi_main.py`'s own
docstring history) — nothing live has exercised the fallback for weeks.
Rollback: `git revert` the retirement commit; `server.py` was never
modified, so nothing else needs restoring.
