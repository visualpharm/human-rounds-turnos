# Refactor engineering acceptance — disposition of the 2026-08-05 audit

**Status: LITERAL AUDIT FOLLOW-THROUGH IN PROGRESS, reconciled 2026-09-03
against the pre-edit `main` baseline `2f35319b`.** This list tracks the
acceptance lines in the external audit of 2026-08-05 (archived as
`humanrounds-pinamar/audits/2026-08-05-open-source-readiness.html`) until they
are finished. It complements
[refactor-finish-line.md](refactor-finish-line.md), which bound only the Tier
4 re-platform candidate, and it replaces the open-ended "not tier
completion" language in [open-source-readiness-plan.md](open-source-readiness-plan.md)
that kept the architecture migration reopening after every sweep. The prior
engineering-closure sign-off remains historical evidence; it is not evidence
that the maintainer's broader publication and institutional audit passed.

## Three separate completion statements

1. **Backend transport migration: done.** No live ASGI route is served by the
   retired `handle(handler, method)` transport: the production source has no
   retired-transport shape and the composed app shows no trace of one
   (`test/test_transport_shapes.py`), and the closed route inventory is
   served natively with the terminal last (`test/test_route_probe.py`).
2. **Earlier architecture-refactor ship gate: historical pass.** The managed
   gates, deployments and live journeys in the final sign-off record passed
   on their named commits. This is not release verification of newer main.
3. **Literal external audit and public/institutional readiness: OPEN.** Missing
   literal acceptance evidence is now marked OPEN, even when the repository has
   a locally useful partial implementation. External work remains in
   [tier7-readiness.md](tier7-readiness.md). Those tracks must not be described
   as completed by this document.

## Why this document exists

The [September 4 review and execution order](astra-review-2026-09-04.md)
prioritizes reproduced regressions, enforced gates, dependency-group cutovers
and one frozen release candidate. Current row count: 20 MET / 38 total (52.6%
by equally counted rows, not by effort); 18 remain OPEN. Row 3.5 closed on
2026-09-05 using the already-deployed candidate's exact execution evidence.
Every OPEN row was re-checked against the tree on 2026-10-29: the count did not
change, and rows 1.1, 2.1–2.3, 4.1, 4.4, 8.2 and 8.3 now carry current facts
instead of the September counts. Rows 2.4, 3.2, 3.3, 4.5, 6.1, 6.4, 7.1–7.3
and 8.1 depend on a vendor, an outside developer, counsel or the maintainer and were
confirmed unchanged.

Between the finish-line candidate `d6af4ad1` (2026-08-27) and `22f13b66`
(2026-09-02) `main` gained 94 commits and roughly 23k inserted / 22k deleted
lines — legacy `handle()` retirement, admin hash-shell retirement, an EMR
connector boundary reworked four times in 24 hours, and a security fix — while
the readiness plan still described Tier 4 and Tier 5 as "not complete" with
open-ended residue (ES-module migration, story-per-component, Chromatic,
physical passkey soak, "decide further daemon moves"). Without a closed list,
each new session re-derived "still not complete" and opened another sweep.
This checklist scores every original audit acceptance line against the tree,
records each deliberate deviation with its reason, and names the bounded work
that remains. The audit's original row set is fixed; phases 7 and 8 are listed
individually below instead of being collapsed into a generic EXTERNAL bucket.

## Statuses

- **MET** — the audit's own acceptance line passes; the proof column says how.
- **DEVIATION** — the audit acceptance line is not met. It is deliberately
  excluded from the engineering refactor scope, with the reason recorded here
  and in the plan's "Deviations" section. It closes only the refactor decision;
  it remains visible audit/publication debt.
- **OPEN** — bounded in-repo work that still has to land before closure.
- **EXTERNAL** — owned by an institution, a vendor or a person outside the
  repo; tracked in [tier7-readiness.md](tier7-readiness.md), never blocks the
  engineering closure.

## Phase 0 — Hygiene and rights

| Item | Audit acceptance | Status | Proof / note |
|---|---|---|---|
| 0.1 Secret rotation + history scan | gitleaks + trufflehog = 0 live findings; no hardcoded test password | MET | Scan recorded in the plan's Tier 1.1 entry; `test/_dbtest.py` reads `TEST_DATABASE_URL` from the environment only. The one historical live key (Google Places) is stripped and replaced by runtime injection. |
| 0.1b Old Google Places key revoked in Google Cloud | Rotated in its own service | MET | Owner the maintainer confirmed removal of the compromised Places API integration; recorded from the product owner's direct confirmation on 2026-09-02. No key value or fingerprint is retained here. |
| 0.2 PII / ops material out of the tree | Tracked-tree hygiene gate passes for the audit's credential, workstation-path, staff-name and real-contact markers | MET | `python3 scripts/public_tree_hygiene_audit.py` scans the exact original markers case-insensitively, with word boundaries for surnames, without embedding those literals in its own source. Seven Pinamar operations documents were preserved byte-for-byte in private-repository commit `5106453` before their public copies were removed; generic engine docs, hooks and skills now use portable contracts. |
| 0.3 Provenance and licence | Rights-holder confirmation in repo, referenced from README | MET | `docs/legal/provenance-sacoturno.md`. |

## Phase 1 — Restructuring

| Item | Audit acceptance | Status | Proof / note |
|---|---|---|---|
| 1.1 Package layout, no code files in the repo root | No `*.py` / `*.js` / `*.html` in root except configs | OPEN | At exact deployed `fb87f270` (2026-09-06), the tracked root inventory is 4 Python entrypoints, 26 classic `.js` files, 6 canonical `.mjs` modules and 0 `.html` artifacts. Shared and admin primitives now live in `static/platform`; directory, upload and confirmation controllers are placed under their owning boundaries. A filesystem gate rejects ignored shadow implementations and dangling aliases too. At local candidate `ae34ab7d` (2026-09-14, catalog and day-queue native retirement integrated with main `1e09d6ed`; not yet deployed), the root inventory is 4 Python entrypoints, 3 classic `.js` (`admin-app.js`, `admin-shell.js`, `nav.js`), 0 `.mjs` and 0 `.html`: classic `admin-content.js` and `admin-queue.js` are physically retired behind owned native controllers. The remaining root code still fails the literal layout criterion. Continue together with 4.1, preserving real consumer and obsolete-route gates. Recount at 2026-10-29 (`git ls-tree HEAD`): 4 Python entrypoints (`app.py`, `asgi_main.py`, `manage.py`, `worker.py`), 2 classic `.js` (`admin-app.js`, `admin-shell.js`; `nav.js` is gone), `playwright.config.mjs` as the one config, 0 `.html`. Two files stand between this row and MET, both entangled with 4.1. |
| 1.2 `turnos` code by domain inside the package | Both runners green; `manage.py mod` still toggles turnos | MET | `core/`, `auth/`, `cuentas/`, `ai/`, `notifications/`, `patient_web/`, `admin_panel/`, `ops/`, `modules/{turnos,chat,intake}/`; Tier 2 closed 2026-08-07. |
| 1.3 Legacy cleanup | Coverage not below baseline; one commit per area | MET | Comparable detached-worktree snapshots from pre-cleanup `afb3b0b5` to post-cleanup `ec10f9d7` increased Python line coverage 80.22% → 81.92%, Python branch coverage 74.42% → 75.21%, JavaScript line/branch coverage 89.67%/87.45% → 90.09%/87.70%, and production-only JavaScript function coverage 82.11% → 84.14%. Eleven coherent area commits carry the retirements. Full method, counts and commit inventory: [audit-row13-coverage-evidence.md](audit-row13-coverage-evidence.md). |

## Phase 2 — Test harness and agent discipline

| Item | Audit acceptance | Status | Proof / note |
|---|---|---|---|
| 2.1 Browser e2e layer | Green locally and in CI; a failing spec blocks merge | OPEN | Recount at 2026-10-29: 109 Playwright spec files run locally (`npm run e2e`, `--profile local-ci-asgi`), and `.gitlab-ci.yml` runs the same five surfaces (backend, frontend, routes, cassettes, e2e) on the company's shared GitLab runners. That pipeline is by design a signal, not a merge gate (it states so in its header), and the GitHub workflows are manual-only, so "a failing spec blocks merge" has no proof. Making a pipeline blocking is the owner's decision. |
| 2.2 Routes + connector tiers, cassettes | Integration tier passes offline in CI; two parallel runs don't collide | OPEN | The routes tier, Postgres advisory-lock mutex and replay-only scrubbed cassettes exist and pass locally. The audit explicitly requires the offline tier in CI; the GitLab pipeline runs the routes and cassette jobs offline, but no recorded green run on a protected branch has been attached to this row, so it stays OPEN (re-checked 2026-10-29). |
| 2.3 Enforcement ladder | Broken unit test rejected pre-commit; broken e2e goes red in CI | OPEN | Local pre-commit rejection was adversarially exercised. A deliberately broken e2e has not been shown to fail a blocking PR check because no pipeline is configured as a blocking merge check (re-checked 2026-10-29). |
| 2.4 Standardised `.claude/` + CONTRIBUTING | A fresh agent completes "add a field to a form" with a green ladder and a story | OPEN | The public instructions and path-scoped hooks exist, but no clean-context fresh-agent run has completed the literal field + green ladder + story scenario. Story enforcement is also still open in 4.4. |

## Phase 3 — Backend re-platform

| Item | Audit acceptance | Status | Proof / note |
|---|---|---|---|
| 3.0 Data-integrity guards | Each concurrency test yields exactly one effect | MET | `test/test_integrity_races.py`, deployed 2026-08-05. |
| 3.1 FastAPI core, `server.py` gone | Full ladder green; every former URL answers the same | MET | `test/test_transport_shapes.py` (no retired-transport shape in production source, none loaded or owning a live route) and `test/test_route_probe.py` (every inventory row served by one native route or declared terminal, catch-all last, offline probe) pass in the backend suite; the retired legacy files stay deleted (`test_asgi_main_dark_mode_retirement`); the ordered route table is pinned by `test/tier4_composition_inventory.json`. They replaced `scripts/refactor_architecture_census.py --require-complete` (372/372 native-independent when last run, 2026-09-04) on 2026-10-28. `app.py` composition root under 100 lines (`test_app_composition`). |
| 3.2 Library-based auth | Login e2e (password + TOTP + passkey) green; security matrix unchanged; custom auth removed | OPEN | Passkeys use py_webauthn, sessions and TOTP use maintained libraries, and all new patient, staff and owner passwords use `pwdlib` Argon2id. The bounded historical `scrypt$...` reader delegates KDF verification to `cryptography` and upgrades a row atomically after successful login; malformed and weak Argon2 envelopes fail closed. On 2026-09-04 the exact database-backed password suite ran eight tests, the complete role matrix ran 24 tests, and the real Chromium password → TOTP → passkey journey passed, all with zero skips. `manage.py auth password-inventory --require-zero` now reports aggregate-only counts across all three credential stores and blocks while compatibility is required. Literal closure is still absent until that gate runs on the deployed installation and a dormant-account policy permits removing the reader. |
| 3.3 Jinja2 templates | i18n-fallback test green; Chromatic clean | OPEN | Native Jinja and i18n fallback tests exist, with no template-owned legacy `{{t.*}}` pipeline. The required Chromatic clean run is absent. |
| 3.4 Alembic | Clean install + upgrade pass; downgrade of latest migration works | MET | Fresh install, bridge, forward upgrade and idempotence are tested. The current `0009_day_queue_event_outbox` migration owns one additive table and has a real inverse; a disposable-PostgreSQL proof downgraded to `0008`, observed the table absent, upgraded to head and observed it restored. Earlier intentionally irreversible migrations remain unchanged. |
| 3.5 Postgres-backed queue | Two instances never duplicate; restart loses nothing; booking → HSI retry e2e green | MET | Manual durable replay (`8cb61519`) and automatic worker retry (`1fc836cc`, integrated by `b35d3c8a`) are ancestors of deployed `52d547e6`. Its immutable full ladder explicitly executed all 20 load-bearing retry, two-worker, crash, restart and watchdog tests: 20 passed, zero skipped. Both running sites served that exact commit and passed live smoke checks. The automatic test starts the registered worker and waits for its own scheduled claim, not manual replay. HSI remains opt-in and disabled for Pinamar. [Exact evidence](audit-row35-queue-evidence.md). |
| 3.6 Static fingerprinting | Changing a file changes its URL; "10 requests, one build" smoke green | MET | `core/static_fingerprint.py`; `scripts/smoke_prod.py` VERDE 2026-09-02. |
| 3.7 Operations contract | Probes pass; SIGTERM drains; optional error tracker | MET | `core/health.py` (`/health`, `/ready`), graceful drain in `worker.py`, `core/error_tracking.py` (Sentry-compatible, optional per the audit). |

## Phase 4 — Frontend and design system

| Item | Audit acceptance | Status | Proof / note |
|---|---|---|---|
| 4.1 ES modules instead of UMD globals | `"type": "module"` declared; `rg 'window.Sana'` empty | OPEN | At deployed `fb87f270` (2026-09-06), 6 root modules and 26 classic root scripts remain. The earlier Sana producer inventory, WzUpload and classic confirmation controller have been physically retired with delayed/rejected loading and interaction gates. The cold-load table preference regression is fixed and guarded behaviorally; starting an import alone never counts as hydration proof. The September 4 baseline was 17 known Sana producers, 48 classic scripts and 17 root modules, not the earlier incomplete window-only count. At local candidate `ae34ab7d` (2026-09-14; not yet deployed) the classic catalog controller and classic day queue are physically retired with owned native lifecycles (`platform/admin-content.mjs`, `platform/admin-queue.mjs`), leaving 3 classic root scripts. Remaining classic controllers, route globals, package declaration and canonical placement still need completion. Continue with the dependency groups and behavioral gates in [the review plan](astra-review-2026-09-04.md); zero known globals alone would not close this row. Recount at 2026-10-29: `package.json` still does not declare `"type": "module"` (the `node --test` suites load classic files with `require()`), and `window.Sana` is still named in `admin-app.js` and 10 files of `static/platform/` (`admin-combo`, `admin-create-appointment`, `admin-my-appointments`, `admin-queue`, `chat-widget`, `dates`, `dependents`, `i18n`, `passkeys`, `turnstile`), so `rg 'window.Sana'` is not empty. |
| 4.2 `admin.html` decomposed into `/admin/<screen>` | Direct URL renders every screen; wiring-guard hook removed | MET | `admin.html` deleted 2026-09-01 (`1c7afc93`); 15/15 declared admin sections have a direct screen, 20 templates under `templates/admin/`, `/admin/login` role-aware landing. The dead `admin-section-wiring-guard.py` hook (it diffed against `HEAD:admin.html`) and its `settings.json` entry were removed 2026-09-02 together with the CLAUDE.md sentences that cited them. |
| 4.3 Server-rendered panel screens + islands + SSE | Queue and calendar e2e green; queue updates without reload | MET | `/admin/cola` now ships useful server-rendered rows and an authenticated, role/center-scoped SSE stream backed by a non-PHI PostgreSQL event outbox. A two-browser Playwright proof observes an attendance change in browser B without navigation; reconnect cursors replay committed events across processes. A separate authenticated Playwright journey opens the doctor calendar Preact+HTM island, selects a working day without navigation, and verifies 360–1280 px layouts. |
| 4.4 Design system as public contract; story per component | CI fails on a component without a story; no literal colours | OPEN | `tokens.css`, theme guards and 29 stories exist. Since 2026-10-29 the component-without-story check exists as a ratchet in the backend suite (`test/test_story_coverage_ratchet.py`, which runs the structural classifier of `.claude/hooks/story-coverage-guard.py` over every tracked file): a new component with no story is red, and `test/story_gap_baseline.json` lists the 43 existing components still without one (of 72) and can only shrink. The row stays OPEN until that list is empty: each story must import the real module, so the remaining 43 are written one screen at a time. |
| 4.5 Patient site unchanged | Chromatic no diffs; booking wizard e2e green | OPEN | Booking e2e is green locally. No real Chromatic baseline/build has produced the required no-diff evidence. |

## Phase 5 — Data and compliance surface

| Item | Audit acceptance | Status | Proof / note |
|---|---|---|---|
| 5.1 Private data repo, config-only linkage | Engine boots and tests pass without the pack; Pinamar reads the volume | MET | Completed and exact-revision deployed 2026-08-20; `TURNOS_PRIVATE_PACKS_DIR`. |
| 5.2 Synthetic example pack | Search chips green; demo day fills; no real names/photos | MET | `packs/example-town`; `test/search-examples.test.mjs`; `demo_refresh.py`. |
| 5.3 Switchable AI inference profiles + benchmark | Config-only switch; `TURNOS_FAKE_AI=1` e2e green; benchmark report in repo | MET | `ai/ai_client.py` profiles, `scripts/bench_ai_profiles.py`, `docs/benchmarks/ai-profile-offline.md`. |
| 5.4 WAHA dev-only, Meta is the path | WAHA-less config passes; docs describe Meta for production | MET | `docs/meta-whatsapp-adoption-runbook.md`; deploy.md mentions WAHA once, as the dev option. |

## Phase 6 — Delivery artifacts

| Item | Audit acceptance | Status | Proof / note |
|---|---|---|---|
| 6.1 Versioned releases + GHCR images; prod updates only via release | `docker run ghcr.io/…:vX.Y.Z` boots; prod deploys from a release | OPEN | `.github/workflows/release.yml` exists but has never run, there are no version tags, and Pinamar still deploys from the `main` webhook. Completion requires a real versioned image boot plus a production release-based deployment; package permissions and hosted-run cost are external gates. |
| 6.2 `docker-compose.yml` minimal path | `compose up` on a clean machine → wizard → install; data survives restart | MET | Verified 2026-09-03 from a clean checkout with no `.env`: Compose built and booted the ASGI app plus Postgres, the setup wizard created a synthetic installation, and an app+DB restart preserved the configured town, operator/config rows, install pack and canonical `turnos.env`. The temporary proof stack and volumes were removed afterward. |
| 6.3 Helm chart | `helm install` on kind boots a working install; rolling update serves one build | MET | Verified 2026-09-03 with Helm 4.2.4 on a disposable kind 0.33.0 / Kubernetes 1.37.0 cluster: external PostgreSQL Secret, `helm install --wait`, live `/health`, `/ready` and `/setup`, then a `kind-v1` → `kind-v2` `helm upgrade --wait`. The completed rollout had one application image across all pods. The proof cluster was deleted afterward. |
| 6.4 Reference Coolify install | Reproducible from docs on another Coolify | OPEN | Pinamar's private deployment is documented, but no independent clean Coolify reproduction has been completed from the public instructions. |

## Phase 7 — publication

| Item | Audit acceptance | Status | Proof / note |
|---|---|---|---|
| 7.1 Community files | An external developer follows README, boots the project and runs tests without asking the author | OPEN | README, CONTRIBUTING, CODE_OF_CONDUCT, ADRs and governance files exist. No independent clean-context developer proof has been recorded. |
| 7.2 Public repository cut | New one-commit clean repository has zero secret/name/photo findings and a green new CI | OPEN | Candidate publication work exists, but the public cut, zero-history scan and first green hosted CI have not been completed as one acceptance run. |
| 7.3 Post-publication protection | Fake-secret push rejected; direct push to `main` impossible | OPEN | Requires the public repository plus enabled push protection, secret scanning and branch protection; no adversarial live proof exists. |

## Phase 8 — institutional wrapper

| Item | Audit acceptance | Status | Proof / note |
|---|---|---|---|
| 8.1 Support entity and LTS | README names support entity/channel; LTS policy published | OPEN | Generated Media, Inc., the product owner and the maintainer are named; the owner-approved D1 policy is published in `SUPPORT.md`: volunteer best-effort support, no SLA and no guaranteed LTS term. The maintainer's literal target also calls for an LTS branch with documented update windows, so this row needs either that commitment or the maintainer's explicit acceptance of D1 as the amended criterion. |
| 8.2 Compliance documents | DPA, AI data map and clinical safety case are sufficient for deploying counsel and a clinical committee | OPEN | Status at 2026-10-29: a data map exists (root `DATA_MAP.md` since 2026-11-10, 470 lines, a technical inventory awaiting sign-off with no approval record), together with the per-feature AI risk notes (`docs/compliance/risk/`, 17 files), `docs/compliance/runtime-controls.md` and `docs/compliance/ia-pba-2026.md`. `DPA.md` and `CLINICAL_SAFETY.md` do not exist. The row needs counsel's and a clinical committee's sign-off, which only they can give. |
| 8.3 Operations documents | An independent administrator restores an installation from backup using the runbook | OPEN | Status at 2026-10-29: `INCIDENT_RESPONSE.md` (252 lines) and `HIGH_AVAILABILITY.md` (188 lines) exist at the root, both still marked as drafts that are not release-ready, and the runbooks live in `docs/operations/` (`backup-and-restore.md`, `backup-format.md`, `backup-acceptance.md`, `restore-rehearsal.md`). `docs/operations/restore-rehearsal.md` records the independent restore rehearsal as NOT RUN, so the row stays OPEN until an administrator other than the author executes it and the results are kept. |

These rows are tracked in [tier7-readiness.md](tier7-readiness.md). They did
not block the earlier architecture-refactor ship gate, but they do block 100%
literal audit completion.

## Final sign-off record

1. **3.5** — decided and applied 2026-09-02: production runs
   `TURNOS_QUEUE_WORKER=1`.
2. **Ship gate** — the exact commit containing this sign-off must pass both
   managed topologies, finish successfully on production and demo, and pass
   `smoke_prod.py`, `smoke_demo.py`, and the defined HSI-disabled browser
   journeys. Coolify deployment records and the smoke output are the
   authoritative rollout evidence; they are not copied into this checklist.

Independent post-audit verification on 2026-09-03 re-ran the previously
unverified `local-ci` topology at exact SHA `ff490f66`: 5,253 backend tests
passed with exactly 99 declared external-pack skips, followed by green
frontend, routes, 31 cassette replays, cassette audit and E2E. The closure run
had already proved the `local-ci-asgi` topology, exact-SHA production and demo
deployments, both live smoke suites, and 24/24 HSI-disabled browser journeys.

Done in the same change as this checklist (2026-09-02): the wiring-guard hook
removal (4.2), the docs-truth pass (readiness plan Tier 4/5 status lines,
finish-line document, CLAUDE.md — no `handle()` transports, no `admin.html`),
and the two `test/test_app_composition.py` failures `main` had carried since
`1c7afc93` (stale composition fixture; `app.py` at the 100-line ceiling).

Also found while closing (2026-09-02): `origin/main` had been red on the
managed gate since `103fb6c2` ("Normalize EMR bridge boundary") switched the
worker to explicit HSI opt-in (`CONNECTOR_EMR_SYNC=hsi`) without reconciling
the tests that assumed `hsi_sync` registers by default; `22f13b66` repaired
four of them and missed the rest (`test_queue_worker.py` HSI classes,
`test_reports_system_native_router.py` cluster count, the `/admin/hsi` e2e
spec). Those tests were brought to the contract's intent in the same change;
the code was correct. Lesson: a lane is not "done" until
`scripts/turnos_managed_exec.py --profile local-ci-asgi` and `local-ci` are
green on the exact SHA.

Every audit line is now accounted for. Only rows marked MET satisfy the literal
acceptance line; OPEN rows remain work, regardless of how much partial local
implementation already exists.

## Stop rule

The earlier architecture-refactor programme is closed when its ship gate is
green. The maintainer's literal audit is complete only when every OPEN row above is MET
or the maintainer explicitly amends that acceptance line. Until then:

- No sweep, retirement, "prove", "harden" or "normalize" commit is audit
  follow-through unless it maps to a row above that is still OPEN. A verified regression
  is a bounded defect and is fixed as such; this rule never suppresses defects.
- A new architectural wish (ESM migration, live-SSE queue, story retrofit,
  hosted CI, versioned releases) is a product or publication card with its own
  acceptance line, never an amendment to this list.
- A session that cannot state which OPEN row it is closing stops and parks.
