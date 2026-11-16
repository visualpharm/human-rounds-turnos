# admin_panel/ — the staff console

## Screens and gates

- Every admin destination is a real server-routed `/admin/<screen>` URL: a section declares `admin_sections[].screen` (`{path, module, router_attr, template?}`) in its module manifest and renders a Jinja template off `templates/admin/base.html`. The hash shell was retired 2026-09-01; there is no client-side section registry to register with.
- A screen's page is assembled by `core/screen_page.admin_page(identity, template, section, namespaces=(...), **context)` (template, sidebar, language, client catalogs, theme, `no-store`); the `*_screen.py` keeps only its gate (`Depends`), its context and its early exits — never a second copy of the assembly. `tests/test_admin_screen_page_contract.py` pins every screen.
- A new role is normally a preset in `permissions.BUILTIN_ROLES` (stored in `roles`, the account's slug in `operator_access.role_slug`; `operators.role` keeps its base value) — no CHECK change. A fifth value of `operators.role` itself goes in five places: `admin_panel/admin_users._ROLES`, a forward Alembic revision on `operators_role_check` (the baseline is frozen) with `app/core/schema_model.py` and `alembic/schema-head.txt`, `core.db_operators._OPERATOR_TABLE_ROLES`, `permissions.LEGACY_ROLES`, and `applyRol` in `admin-app.js`. Missing one kills the connection silently when that account is created.
- The three catalog editors (`/admin/services`, `/admin/centers`, `/admin/doctors`) share one module and template but three distinct one-route routers; the loader never deduplicates a router object.
- `/admin/account` is the only screen that admits an expired-2FA session, by exact path; `/admin/accounts`, `/admin/legal` and `/admin/appearance` are not 2FA repair surfaces.
- `/admin/appearance` degrades per block: render on a 2xx GET, remove silently on 404, treat 401/403 as the session boundary, one reload line otherwise.
- A fresh login lands role-aware: `admin`/`admisionista` → `/admin/solicitudes`, `medico` → `/admin/cola`, `reportes` → `/admin/reportes`; unknown roles fail safe. A session that dies on a direct screen re-enters through `/admin/login?next=<screen>`, never `/admin`.
- `/admin/hsi`: literals in `locales/{es,en}/admin.hsi.json`; the `static/platform/admin-hsi.mjs` controller is inert until mounted, owns its reads, mutations and listeners per mount, disposes on exit; a failed panel offers Retry, a failed or stalled import offers Reload within eight seconds; the conflicts table stacks when its container is at most 900px wide.
- Deshabilitar, never eliminar, an entity that has appointments (`db_slots.count_appointments_of`); a doctor also when any row names it (`db_slots.DOCTOR_ID_COLUMNS`: interviews, consultations, summaries, day queue, its account). A doctor with upcoming approved appointments (`db_slots.doctor_upcoming_appointments`) is refused with 409 — the desk reassigns or cancels first; pending requests merely assigned to it are released to «sin asignar» (2026-10-23).
- A screen or API that shows clinical data (interview, summary, referral, triage, history) records the read through `core/access_log` and scopes by `access_log.scoped(identity)` — never a bare `rol == "medico"`. Admin-only «Quién abrió sus datos» lives in the ficha. Detail → `docs/architecture/registro-de-accesos.md`.
- The copy of ALL a person's data (`GET /api/admin/patients/{dni}/data-export`, administrator only) is read only, has no secrets, leaves its own `exportacion` row in the access log before anything is built, and takes its contents from `core/personal_data.REGISTRY` — a new table or personal column is registered there in the same change. Detail → `docs/architecture/derechos-de-los-titulares.md`.
- Standard rejection reasons are the closed lists `MOTIVOS_ORDEN` and `MOTIVOS_GENERAL` in `admin-app.js`, never free text. AI prompts are editable from the panel (`app/ai/prompts.py`): canonical English, staff edit the Spanish. The two legal documents (`app/patient_web/legal.py`) say exactly what `app/core/pii.py` does — change one, change the other.
- Panel tables use one engine, `static/platform/admin-table.mjs`. A new control copies the existing control that does the same job (class, size, colour, icon, wording) and adds its pair to `tests/ui-evals.test.mjs` eval 3.
- A list's search row (search, filters, column gear, buttons) is 44px from ONE rule at the end of `admin-direct.css` (`--adm-row-h`); a screen keeps no toolbar height of its own, and a new list search sits in one of the rows that rule names (`tests/admin-list-row-height.test.mjs`).

## Divider audit hook (MANDATORY)

Whenever HTML or CSS is added or edited, inspect every `<hr>`, separator
element, `border-top`, and `border-bottom` in the touched surface before
finishing. A divider is allowed only between two unbounded plain-text regions
that otherwise run together. Remove it when either side is a bordered, filled,
or rectangular control/card, and remove it between two such blocks — the
existing chrome is the separation. Run `node --test
tests/admin-divider-contract.test.mjs` after every admin UI change.

## Admin simplification contract (MANDATORY)

`python3 scripts/eval_admin_simplification.py --fixtures` checks four DOM
contracts on rendered fixtures: an editable binary setting is a checkbox, not
a Yes/No select; an unavailable optional property is absent (no mobile label,
no disabled placeholder); repeated mutation of the same row type has visible
selection with a group operation or Shift-range selection; an editable empty
section has a creation action or form, never a dead end (a blank
selected-filter result is a no-match, not a creation form). Run it after every
admin UI change and review the rendered surface for semantics the fixture
cannot know. The Claude hook is advisory; Codex runs the evaluator directly.
