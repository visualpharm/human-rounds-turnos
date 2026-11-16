# Tier 5 admin screen — the three role-gate shapes + the `static()` template trap

Slice 5 of the progressive admin-section migration (`docs/roadmap/tier5-frontend-design.md`) added the fifth server-routed `/admin/<screen>` twin — `reportes` — alongside `cola`/`pacientes`/`mis-turnos` and intake's `entrevistas` pilot. Two lessons are reusable for slice 6+.

## 1. A manifest `roles` list maps to `deps.require_admin(...)` in THREE shapes — pick by inspection of the underlying data endpoint, not by feel

Every screen router gates with `core.deps.require_admin(...)` (the structural port of `admin_auth.require_admin`). The manifest's `admin_sections[].roles` is the contract; the screen must reproduce the SAME gate the section's own data endpoints already use, byte-for-byte. There are exactly three shapes so far:

| screen | manifest `roles` | screen's `require_admin(...)` | why |
|---|---|---|---|
| `cola` | `["admin","medico","admisionista"]` | `permitir_medico=True, permitir_admisionista=True` (no `solo_admin`) | the day-queue handler admits those three; admin passes `solo_admin`-by-default, the flags add the other two |
| `mis-turnos` | `["medico"]` (no admin) | `permitir_medico=True` **+ an explicit `rol == "medico"` post-check** | `permitir_medico=True` ALONE still admits admin — the post-check is the thing that rejects an admin session (`solo_medico`). This is the gap the intake pilot's docstring flagged |
| `reportes` | `["admin","reportes"]` | `solo_admin=True, permitir_reportes=True` (no post-check) | reports ARE solo_admin; `'reportes'` is the ONE non-admin role `solo_admin` exempts (via `require_admin`'s own `not (rol == "reportes" and permitir_reportes)` clause). Mirrors `reports.py`/`reports_router.py` verbatim |

The trap: `reportes` is the mirror of `mis-turnos`. `mis-turnos` was the first `roles` list WITHOUT admin (needs a post-check because the flag would still let admin in). `reportes` is the first `roles` list WITH admin where the non-admin member is the `solo_admin` opt-in rather than a standalone `permitir_*` flag. Copying cola's shape (`permitir_reportes=True` alone, no `solo_admin`) would have been WRONG — it would have let `marca` and other future roles through that reports.py itself rejects. The distinctive test case (`test_reportes_session_renders_the_distinctive_case`) exists precisely because the `'reportes'` session 403s on every sibling screen and must RENDER here.

Rule for slice 6: before writing the screen's gate, grep the section's own data endpoint for its `require_admin(...)` call and copy that signature exactly. The manifest `roles` list and the endpoint's gate are two readings of one policy; the screen is the third and must match.

## 2. `static` is a FUNCTION in `jinja_env`, an OBJECT in admin.html — screen templates use `{{ static('x') }}`, never `{{static.x}}`

`admin.html` references the fingerprinted matcher as `{{static.search.js}}` (attribute access on a `static` namespace object). A screen template rendered via `core.jinja_env.render(...)` does NOT see that object — `jinja_env.py` binds `ENV.globals["static"] = static_fingerprint.url`, the `url` FUNCTION. So the template syntax is the call form:

```jinja
{# screen template (jinja_env.render path) — CORRECT #}
<script src="{{ static('search.js') }}"></script>

{# WRONG — this is admin.html's legacy render path only; resolves to nothing
   under jinja_env and the template fails to render #}
<script src="{{static.search.js}}"></script>
```

`search.js`, `admin-charts.js`, and `admin-reports.js` are all in `static_fingerprint.MIGRATED` (content-hashed URLs), so they MUST go through `static()` — a hardcoded `/x.js?v=N` would 404 against the hashed route. The whole root admin cohort (`ADMIN_ROOT_JAVASCRIPT` — 29 files today, 28 when this was first written) is migrated, so EVERY server-routed screen template loads its admin assets through the FUNCTION form `{{ static('...') }}` — `cola.html`/`pacientes.html`/`mis-turnos.html` (admin-table/admin-queue/admin-patients/admin-my-appointments) just as `reportes.html` (admin-charts/admin-reports/search); only `admin.html`, on its separate legacy regex-substitution render path, still uses the attribute form `{{static.name.js}}`. `reportes` is no longer the lone exposure: it is the screen that proves the function-vs-attribute distinction across all three asset cohorts at once (charts + reports + search). The contract test pins the RESOLVED url, not just that the template rendered: `TestReportesScreenRoleGate.test_admin_session_renders` asserts the served HTML matches `/static/admin-reports\.[0-9a-f]{8}\.js` and `/static/search\.[0-9a-f]{8}\.js` (Terra review — an earlier draft of this record claimed the resolved URL was covered when no assertion actually checked it; the assertion was added rather than the claim narrowed). A `{{static.x}}` typo still fails the whole render with a Jinja `UndefinedError`-shaped stack before that regex ever runs, so the regex is belt-and-braces on top of "it rendered", not a substitute for getting the form right.

## What proved it

`test/test_admin_screen_contract.py` — three classes: `TestReportesScreenRoleGate` (5 cases: no-session 401, medico 403, admisionista 403, admin renders with `id="sec-reportes"` + the resolved `/static/admin-reports.<hash8>.js` URL + `window.SANA_ADMIN_SCREEN = "reportes"` + the resolved `/static/search.<hash8>.js` URL, and the distinctive `reportes`-renders case); `TestReportesScreenModuleGating` (disabling `turnos` 404s `/admin/reportes` indistinguishably as `{"detail":{"error":"no_encontrado"}}`, toggles live without remount, and drops the reportes nav entry from `sections_payload_for_role` with zero central edits — the turnos-core mirror of intake's leaf-disable pilot); and `TestScreenTemplatesNeverForceTheHash` (no `location.hash` shim). The composition guard (`test_app_composition_inventory.json`) gained exactly one route (`/admin/reportes` GET, before the catch-all). The browser layer (`test/e2e/admin-screen.spec.mjs`: direct-nav summary landing `.rep-sumcard`, legacy `#reportes` summary-landing hash, legacy `#reportes/gestion` SUB-REPORT deep-link proving the gestion breadcrumb + solicitudes-CSV link render and NOT the summary grid, 390px overflow) mirrors the entrevistas/cola tests and runs under `npm run e2e` — the managed E2E/full-CI gate Codex runs; it is not waived or discounted here.

See also: [tier5-frontend-design.md](../../roadmap/tier5-frontend-design.md), [admin-panel-y-auth.md](../../architecture/admin-panel-y-auth.md), the `admin_sections_note` in `modules/turnos/module.json`, and `admin_panel/admin_reportes_screen.py`'s docstring (the role-gate shape table above is reproduced there).
