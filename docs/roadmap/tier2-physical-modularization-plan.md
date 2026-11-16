# Tier 2 physical modularization — dependency graph and batch plan

Written 2026-08-06 against `78f0486f`, as the prerequisite the
close-modularization card demands before any Tier 2 batching: derive the
dependency graph from the current tree, assign non-overlapping ownership,
then execute in tier-bounded batches. Governing order (supersedes earlier
sequencing): **Tier 2 closure → Tier 4 closure → Tier 5 closure → Tier 6
implementation → Tier 7 publication.**

## Measured starting point (2026-08-06)

- **224 root code files** (162 `.py` + 62 `.js`). Only `modules/intake/` is
  physically self-contained; `modules/turnos/` is manifest-only.
- Import fan-in concentrates in a real platform core: `db` (57 importers),
  `config` (39), `module_router` (37), `theme` (29), `deps` (29),
  `admin_auth` (26), `i18n` (25), `catalog` (21), `tz` (18),
  `site_identity` (18).
- `server.py` imports 63 root modules (the legacy dispatcher);
  `app.py` imports 17; `routing.py` 20. These are the composition points —
  every batch touches their import blocks, which is why batches integrate
  **sequentially**, one at a time, with the remaining batches rebasing on
  the integrated result (per the card's reporting rules).

## Target shape

Two destinations, chosen per cluster:

1. **`modules/<id>/`** — a feature a deployment could disable. Owns its
   backend, `*_router.py` FastAPI twins, templates, locales, JS assets,
   admin-section registration, and its `module.json`. `modules/intake/`
   is the reference; **`modules/turnos/` gaining its real implementation
   is the flagship outcome** of this tier.
2. **A domain package at root** (e.g. `core/`, `auth/`) — shared
   infrastructure no deployment turns off. Not a module, but no longer a
   flat pile of root files.

Dependency direction (enforced by a new test per batch): modules and
domain packages import from `core/` (and each other only via explicit,
listed contracts); `core/` never imports from a module. `server.py` and
`app.py` remain composition roots until Tier 4 retires `server.py`.

**No permanent shims.** A batch moves its files AND rewrites every
importer repo-wide in the same commit. A temporary root shim is allowed
only inside a batch's own worktree while it works, never in the
integrated result — the Tier 2 gate counts root files, and a shim is a
root file. (`legacy_shim.py` / connector compat shims from Phase 6b are
pre-existing and out of scope here.)

## Ownership assignment (non-overlapping, by root `.py` module)

Every batch also claims the root `.js` assets, templates, locales and
tests that belong to its files — the executing batch enumerates them via
`rg` before moving, and no two batches may claim the same file.

- **Batch A — SATISFIED BY PRIOR WORK (Phase 6b), nothing to move.**
  Verified 2026-08-06: `hsi_client`, `hsi_resolver`, `hsi_resync`,
  `hsi_sync`, `webturno` — plus `msd_data`, `puco`, `refeps`, `renaper` —
  are 12-line compat shims (`sys.modules[__name__] = _connector`); the
  real implementations already live in `connectors/<id>/` and import only
  core infra. The original Batch A claim was mis-derived from filenames
  without reading contents. Retiring the nine shims is **Batch L** below.
- **Batch B — `notifications/` package** (pattern-prover, first executed):
  `notifications`, `email_mailgun`, `gmail_confirm`, `confirmations`,
  `reminders`, `otp_send`.
- **Batch C — `auth/` package**:
  `admin_auth`, `panel_login(+_router)`, `passkeys(+_router)`,
  `passkeys_webauthn_adapter`, `admin_password_reset(+_router)`,
  `auth_mode`, `turnstile`, `site_auth`, `mcp_guard`, `ip_exempt`.
- **Batch D — `modules/turnos/` implementation** (flagship, largest):
  `booking_requests(+_router)`, `booking_limits`,
  `appointment_offer(+_router)`, `appointment_confirm(+_router)`,
  `slot_holds(+_router)`, `doctor_appointments(+_router)`,
  `calendar_busy`, `calendar_feed(+_router)`, `waitlist`,
  `waitlist_queue`, `remedies`, `searches(+_router)`,
  `preparation(+_router)`, `prep_model`, `patient_ranking`.
- **Batch E — `ai/` package**:
  `ai_client`, `ai_health(+_router)`, `ai_costs(+_router)`,
  `ai_cooldown`, `ai_referral`, `prompts(+_router)`,
  `orden_analysis_cache`, `abuse_guard`, `chart_summary(+_router)`,
  `lab_analysis`, `lab_results(+_router)`.
- **Batch F — `modules/chat/`** (disable-able feature):
  `chat_agent`, `chat_api(+_router)`, `chat_language`, `chat_tools`,
  `whatsapp_chat`, `voice(+_router)`, `mcp_api`.
- **Batch G — `cuentas/` package** (patient accounts + coverage):
  `accounts(+_router)`, `account_photo(+_router)`, `auth_renaper`,
  `health_coverage`, `ocr_dni(+_router)`. (`renaper`, `refeps`, `puco`
  are Phase 6b connector shims — Batch L's, not this batch's.)
- **Batch H — `patient_web/` package** (public site):
  `patient_site(+_router)`, `catalog`, `knowledge`, `legal(+_router)`,
  `seo`, `sitemap(+_router)`, `branding(+_router)`,
  `avatar_sprite(+_router)`, `doctor_photos(+_router)`, `searches` is
  batch D's (booking search), not this one's.
- **Batch I — `admin_panel/` package** (staff screens):
  `admin_appointments(+_router)`, `admin_patients(+_router)`,
  `admin_content(+_router)`, `admin_users(+_router)`,
  `admin_account(+_router)`, `admin_preferences(+_router)`,
  `admin_cola_screen`, `admin_mis_turnos_screen`,
  `admin_pacientes_screen`, `doctor_patients(+_router)`,
  `reports(+_router)`, `system_status(+_router)`,
  `settings_admin(+_router)`.
- **Batch J — `ops/` package** (lifecycle + demo):
  `setup`, `data_backups`, `demo_installation`, `demo_refresh`,
  `housekeeping`, `cache_warmup`, `translate_warmup`, `watcher`.
  (`msd_data` is a Phase 6b connector shim — Batch L's.)
- **Batch K — `core/` package** (LAST — highest blast radius, done when
  every other batch has already shrunk the importer set):
  `db`, `config`, `deps`, `module_router`, `theme`, `tz`, `sitedata`,
  `site_identity(+_router)`, `data_packs`, `format`,
  `static_fingerprint`, `jinja_env`, `i18n`, `i18n_middleware`,
  `pack_l10n`, `translate`, `routing`, `app_modules`, `gzip_bridge`,
  `proxy_trust`, `health`, `error_tracking`, `pii`, `legacy_urls`,
  `host_redirect`, `abuse_guard` is batch E's, not core's.
- **Batch L — retire the nine Phase 6b compat shims** (late, after the
  domains have moved): rewrite every importer AND every
  `unittest.mock.patch("<old_name>...")` string to the
  `connectors.<id>.<mod>` path, handle `webturno.py`'s direct-script
  dispatch (document `python3 -m connectors.webturno.webturno` or keep a
  tiny script under `scripts/`), update the shim-identity tests
  (`test_connector_shims.py`, parts of `test_hsi_connector.py`,
  `test_slot_source_connectors.py`) to assert direct imports instead,
  then delete the nine root shims. Root count −9. Read
  `docs/connectors.md` "Compat shims" first — the monkeypatch-visibility
  semantics there are exactly why the strings must be rewritten in the
  same commit as the imports.

**Deliberate root exceptions** (stay at root, reported in the gate):
`server.py` (Tier 4 retires it), `app.py`, `asgi_main.py`, `manage.py`,
`legacy_shim.py` (pre-existing compat, Tier 4 scope). Anything else left
at root when Tier 2 closes needs its own stated reason.

## Batch order and integration protocol

Order: **B → C → D → E → F → G → H → I → J → L → K.** (A is satisfied by
prior work.) B proves the pattern cheaply; C before D because booking
imports auth; L after the domains have moved; K last.
Within this order, batches whose file sets and importer overlap are
demonstrably disjoint (e.g. E and F, H and I) MAY run in parallel
worktrees, but **integration into `main` is strictly one at a time**,
followed by rebasing the remaining batch worktrees.

Each batch, in its own worktree:

1. Enumerate its claim: the `.py` files above + their `_router` twins +
   `rg`-derived js/templates/locales/tests. Publish the claim list in the
   batch commit message.
2. `git mv` into the package; add `__init__.py`; rewrite ALL importers
   repo-wide (including `module.json` `router_attr` paths, `server.py`
   and `app.py` import blocks, tests, scripts).
3. For module batches (D, F): move admin-section registration, nav
   entries, assets and locales into the module and extend `module.json`
   to the intake shape; disable/enable acceptance test must pass without
   central wiring edits.
4. Add/extend the dependency-direction test for the new package.
5. Green gate before integration: focused tests while editing, then full
   `scripts/ci_local.sh` in BOTH modes at the batch's integration point.
6. Independent adversarial review of the diff (an implementing agent
   never certifies its own batch), then integrate to `main`, push, smoke
   prod + demo, rebase remaining worktrees.

## Tier 2 closure gate (from the governing card, restated)

- `modules/turnos/` no longer manifest-only; modules own backend, routes,
  templates, UI registration, assets.
- Disable/enable and add-a-screen acceptance tests pass without central
  wiring edits; imports prove relocated files are executed.
- Root-file count materially reflects the target (report before/after:
  starting point is 224; target is the deliberate-exceptions list plus
  entry HTML/CSS assets not yet owned by Tier 5 work).
- Full local CI green in both modes + independent diff/runtime review.
- Only then does Tier 4 closure begin.
