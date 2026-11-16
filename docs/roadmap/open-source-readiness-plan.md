# Open-source readiness — refactoring plan

Source: external audit of 2026-08-05 (895 commits, 1,719 files; archived in the private
ops repo `humanrounds-pinamar`). The audit found 8 problem groups, 5 of them blockers,
and prescribed phases 0–8. This plan maps those phases onto tiers as executed, records
the deviations from the audit, and tracks status. Strategy (audit's, adopted): fix and
re-platform in THIS private repo → separate public code from private data → publish as
a fresh single-commit cut into a NEW repository. The existing history (secrets + PII in
every commit) is never published.

The bounded engineering completion contract is
[refactor-finish-line.md](refactor-finish-line.md). It deliberately separates
the current refactor from later public-publication, institutional and optional
daemon work so the product roadmap cannot keep moving the finish line.

## Tiers

### Tier 1 — hygiene + live-prod correctness (audit Phase 0, plus 3.0 hoisted)
1. **Secrets** (0.1): scan full history with gitleaks + trufflehog; remove the
   hardcoded Postgres password from `CLAUDE.md`, `test/_dbtest.py`,
   `test/test_searches.py` (managed local profiles and direct deployments inject
   `TEST_DATABASE_URL` externally; `.env.example` contains public config only);
   rotate anything the scan finds live.
2. **PII/ops extraction** (0.2, done 2026-08-05): `docs/pinamar/` moved to the
   private repo `humanrounds-pinamar`
   (`<workspace>/human-rounds/humanrounds-pinamar/docs/`); `.claude/inbox/`
   untracked + gitignored (files stay on disk — the capture pipeline needs
   them locally; deviation from the audit, which said "move");
   staff contacts and credential pointers move from `CLAUDE.md` to `CLAUDE.local.md`
   (natively gitignored).
3. **Provenance** (0.3): written confirmation in `docs/legal/` that Generated
   Media, Inc. holds the copyright and licensing authority for the private
   Sacoturno lineage incorporated into Human Rounds and distributes it under
   AGPL-3.0.
4. **Data-integrity guards** (audit 3.0, hoisted — these are live-prod race conditions,
   independent of re-platforming): partial unique index on (doctor, appointment_at) for
   active statuses; front desk goes through the same `slot_holds` as patients;
   status-CAS on approve/reject (pattern already at `db.py` assign-doctor); booking
   limit under a transactional advisory lock per (dni, service); idempotency key on the
   Confirmá POST; claim-CAS before sending reminder emails; partial UNIQUE on live
   waitlist offers + FOR UPDATE; holds fail closed when the DB is down; advisory lock on
   the migration runner. Acceptance: route-level concurrency tests — simultaneous
   approve/reject, two approvals on one doctor-slot, front desk vs. active patient
   hold, parallel booking-limit POSTs, repeated confirmation POSTs, overlapping
   reminder workers, competing waitlist workers, concurrent startup/migrations, legacy
   duplicate data, fresh database — each proving one durable effect, one notification
   where applicable, and the correct loser-facing API response, passing repeatedly;
   front desk must respect active patient holds (a DB uniqueness constraint alone does
   not satisfy this).

### Tier 2 — Modularization (the module contract)
A module owns its own backend code, routes, templates, UI registration and assets —
the `modules/<id>/` pattern already established by `modules/intake/`. Package layout
(`humanrounds/`, move-only commits, no renames) hoists the 100+ root `turnos`-module
files into domain subdirs (`admin/`, `booking/`, `ai/`, `reports/`, `catalog/`) as
modules or module-owned code; dead-code sweep by coverage. A module can be enabled or
disabled without editing central routing/navigation/template lists, and adding a screen
requires no wiring changes across unrelated files. Delivered on the CURRENT stack — a
separate milestone from Tier 3's test harness and from any FastAPI/Jinja re-platform
(Tier 4).

**Acceptance (outcome, not activity):**
- Disabling a module makes its routes 404 and its nav entries disappear, with zero
  edits outside that module's directory and no other screen breaking.
- Adding a new demo screen inside a module touches only that module's directory — no
  `admin.html` hash-router edits, no `OTRAS` list edits, no central template list edit.

**Execution plan:**
- Work happens in an isolated worktree based on current `origin/main` — the shared
  checkout carries unrelated live WIP from sibling sessions. Every slice lands
  through the full suite gates before merging.
- **Pre-move safety gate**, run before the first large module move: a fresh
  clean-worktree Python + Node baseline recorded; contract tests covering module
  discovery, route removal, navigation removal, and asset/template ownership, plus
  dependency-error handling; critical browser E2E coverage for the booking, admin
  and intake surfaces locked in before those surfaces move (the rest of the harness
  may still follow later, as Tier 3).
- Module manifest + loader contract defined **first**: identity, enablement,
  dependencies, routes, navigation, templates, assets, startup/shutdown,
  deterministic discovery. After that framework commit lands, adding or disabling a
  module requires changes only inside the module.
- One vertical pilot module end-to-end, proving both acceptance tests above
  (disabled → routes 404 + nav disappears with zero central edits; a demo screen
  added with no central router/`admin.html`/`OTRAS`/template-list edit) before
  repeating module by module. A manifest-only directory or a plain files-into-folders
  move does NOT count as satisfying this.
- Moves stay behavior-preserving and reviewable: never combine mass moves, renames,
  dead-code deletion and behavior changes in one chunk; re-run the clean baseline +
  module-contract gate after every module slice.

### Tier 3 — Enforcing test harness
- Browser e2e tier (Playwright: booking wizard incl. referral branch, queue +
  Pacientes de hoy, intake QR, cancel + waitlist, login); routes tier (in-process app,
  disposable fixtures, Postgres advisory-lock mutex); record-replay cassettes for
  HSI/PUCO/RENAPER/Meta; the four-point run ladder (Stop-hook → pre-commit → CI →
  nightly) with red tests blocking "done".

### Tier 4 — backend re-platform (Phase 3, strangler, route by route)
- [x] **Landed 2026-08-27** (candidate `d6af4ad1`; re-verified on `fd722b1c`
  2026-08-30). `server.py`, `legacy_shim.py`, `core/routing.py` and
  `module_router` are deleted; `scripts/refactor_architecture_census.py
  --require-complete` reports 371/371 native-independent, 0 adapted-legacy,
  0 rollback-only. `app.py` is a 98-line composition root over `routers/`,
  `services/` and `core/deps.py`; Alembic, Jinja2, procrastinate (Postgres
  queue, no Redis), `webauthn`, static fingerprinting and `/health` + `/ready`
  are all in place. Binding definition and its scope limits:
  `docs/roadmap/refactor-finish-line.md`. The 54 test-reachable legacy
  `def handle(...)` transports were deleted on 2026-08-30/31 with their tests
  re-pointed at the native routers; nothing of the legacy dispatch contract
  remains in source.
- FastAPI + uvicorn core (`app.py` < 100 lines, `routers/`, `services/`, `deps.py`);
  library auth replacing hand-rolled sessions/TOTP/passkeys (first priority); Jinja2 +
  jinja2-fragments replacing the `{{t.*}}` pipeline; Alembic; background daemon
  threads → Postgres-backed queue (procrastinate/pgqueuer — no Redis); static
  fingerprinting kills manual `?v=` and its hook; `/health` + `/ready` + graceful
  shutdown + optional Sentry-compatible error tracking.

### Tier 5 — frontend + design system (Phase 4)
- ES modules + import maps replace UMD `window.Sana*`; `admin.html` decomposed into
  server-routed screens (`/admin/<screen>`, Jinja inheritance) — hash router, OTRAS
  lists and their guard hook deleted; interactive screens as Preact+HTM islands
  (vendored ESM, no build step); tokens.css as the published design contract;
  story-per-component as definition of done.

### Tier 6 — data, compliance, delivery (Phases 5–6)
- [x] The real Pinamar pack is in a dedicated private repository, restore-drilled
  encrypted backup, and `/app/data/private-packs` production volume. The public
  tree defaults to the synthetic `example-town` pack and resolves required
  deployment packs through `TURNOS_PRIVATE_PACKS_DIR`.
- Three switchable AI inference profiles (cloud+DPA / hosted-in-jurisdiction /
  self-hosted vLLM); benchmark on an anonymized referral corpus. WAHA demoted to
  dev-only; Meta is the documented deployment path.
- Versioned releases + ghcr images; `docker-compose.yml` as the minimal install path;
  Helm chart; Coolify as a documented reference, not the only path.

### Tier 7 — publication + institutional wrapper (Phases 7–8)
- README/CONTRIBUTING/CODE_OF_CONDUCT/ADRs; fresh-cut public repo with push
  protection + secret scanning + branch protection; support entity, LTS branch,
  SECURITY.md, DPA + data map, clinical safety case, runbook/HA docs.
  Full readiness tracking: [Tier 7 readiness roadmap](tier7-readiness.md).

## Deviations from the audit (recorded 2026-08-05)
Every deviation from the audit is listed here with a reason — a deviation found mid-work
gets a placeholder entry here immediately, filled in once the reason is settled.
- **Repo is private** (`visualpharm/sana-turnos`) — confirmed. Secret rotation is
  precautionary, not incident response.
- **3.0 hoisted into Tier 1**: the booking races exist in production today; the fix is
  SQL constraints + existing in-repo patterns (status-CAS, SKIP LOCKED, ON CONFLICT),
  not re-platforming.
- **`docs/pinamar` was 2 files**, not 81 (audit counted a stale state); moved to the
  private `humanrounds-pinamar` repo on 2026-08-05.
- **`.claude/inbox` untracked, not moved** — The capture pipeline reads it locally.
- **`example-town` pack exists** — 5.2 reduced to a verification task.
- **Old Tier 2 split into Tier 2 (Modularization) + Tier 3 (test harness)** —
  reason: (a) the module contract relocates route/nav/template ownership, which the
  e2e coverage-map and specs would otherwise have to encode twice — landing the
  contract first keeps the harness specs stable instead of being rewritten once
  modularization lands; (b) modularization is deliverable on the current stack
  independently of Playwright infra, so it does not need to wait on Tier 3; (c) the
  split was directed by the project owner (the product owner) on 2026-08-05, with modularization
  named as the leading milestone. Compensating control: Tier 2 does NOT begin with
  mass moves — see the pre-move safety gate in the Tier 2 section below.
- **Closure deviations (2026-09-02)**, each with its reason in
  [audit-acceptance-checklist.md](audit-acceptance-checklist.md): root-level
  UMD JS files stay (1.1); hosted GitHub Actions / Chromatic never run, the
  local ladder is the enforcing path (2.1, 2.3, 3.3, 4.5); no ES-module
  migration, UMD + ESM facades instead (4.1); story-per-component binds new
  components only (4.4); versioned GHCR releases are publication-track work,
  Pinamar deploys from `main` (6.1).

## Status
- [x] Tier 1 local Postgres credential boundary — complete. The fixed
  credential vault-backed managed profiles are the local credential source, both
  checkout `.env` copies are removed, and local DB-backed work no longer
  instructs credentials in the checkout. This is a value-free operational
  closure; it does not claim protection from a malicious same-UID process.
- [~] Tier 1.1 Google Places revocation confirmation — externally blocked. gitleaks +
  trufflehog over all 951 commits: nothing rotation-critical; no OpenRouter/Mailgun/
  Coolify/Turnstile secret was ever committed. The one live finding — a real
  **Google Places API key** in the historical private-pack files — is partially resolved:
  the old key is stripped from every tracked file (`key=REDACTED` in its place, URL
  structure kept), the fetch scripts that consume those URLs
  (`scripts/fetch_brila_photos.py`) now substitute a real key from
  `GOOGLE_PLACES_API_KEY` at request time, and a new restricted key (project
  of the operator) is stored in the credential vault ("Google Places API key — human-rounds
  scripts") and supplied through runtime injection. The key's owner, **the maintainer**
  (Brila), was notified via Teams on 2026-08-05 14:47 — upstream message ID recorded
  in the ops repo. The prototype basic-auth default in the smoke scripts was removed and the
  credential rotated on 2026-09-27 (security audit finding 2).
  The audit's Phase 0.1 requires each live finding **rotated in its own service**, not
  just removed from the tree — **the only remaining Tier 1.1 gate** is the maintainer/Brila
  confirmation that the old key is revoked or disabled in Google Cloud. Record the
  confirmation date and owner in the private ops record, never a key value.
- [x] Tier 1.2 PII/ops extraction — complete, 2026-08-05. Outcome: tracked tree free
  of ops PII; `docs/pinamar` lives only in the private repo
  `visualpharm/humanrounds-pinamar` (audit archived there too); `.claude/inbox`
  untracked; contacts + credential pointers → `CLAUDE.local.md`.
- [x] Tier 1.3 provenance — complete, updated 2026-08-23. The corporate
  licensing position is recorded in `docs/legal/provenance-sacoturno.md`:
  Generated Media, Inc. owns and licenses the Sacoturno lineage incorporated
  into Human Rounds; Sacoturno stays private, Human Rounds is the published
  AGPL-3.0 product, and maintainership does not create personal ownership.
- [x] Tier 1.4 data-integrity guards — complete, 2026-08-05. Designed
  ([data-integrity-guards-plan.md](data-integrity-guards-plan.md)); acceptance is
  route-level concurrency tests — simultaneous approve/reject, two approvals on one
  doctor-slot, front desk vs. active patient hold, parallel booking-limit POSTs,
  repeated confirmation POSTs, overlapping reminder workers, competing waitlist
  workers, concurrent startup/migrations, legacy duplicate data, fresh database — each
  proving one durable effect, one notification where applicable, and the correct
  loser-facing API response; plus front desk respecting active patient holds (a DB
  uniqueness constraint does not satisfy this). Landed in commits `b9b94a7` (route-level
  race guards) and `911521b`; `test/test_integrity_races.py` is green end to end,
  confirmed over repeated runs with zero `expectedFailure`s, and the fix is deployed
  and smoked in production as of 2026-08-05.
- [x] Tier 2 modularization — DONE 2026-08-07 (`main` = `4f4477fe`). Three
  stages, each with its own recorded evidence: (1) pre-move gate 2026-08-05
  (green baseline 2907/836; contract tests; browser e2e booking/admin/intake
  7/7); (2) framework + pilot 2026-08-05 (manifest v2 + loader + hooks +
  `/api/nav`; admin shell on SanaAdminRouter/SanaAdminSections; `intake`
  retrofitted to the full v2 contract, both acceptance outcomes proven live —
  disable/enable drives sidebar + 404s with zero central edits; a demo screen
  added/removed touching ONLY `modules/intake/` — evidence in
  [module-contract-design.md](module-contract-design.md)'s "Pilot" section);
  (3) physical modularization batches A–L 2026-08-06/07: root went **224 → 5
  `.py` files** (`server.py`, `app.py`, `asgi_main.py`, `manage.py`,
  `legacy_shim.py` — the plan's deliberate entrypoint exceptions), code now
  lives in `core/`, `auth/`, `cuentas/`, `ai/`, `notifications/`,
  `patient_web/`, `admin_panel/`, `ops/`, `modules/{turnos,chat}/`, and the
  nine Phase 6b compat shims are retired (batch L). Every batch ran in an
  isolated worktree through the full gate (backend 3313/0, frontend 934/934,
  `ci_local.sh` 5 surfaces, ASGI e2e 17/17) plus an independent adversarial
  review in a scratch clone; closure verified live 2026-08-07 with prod smoke
  10/10 and demo smoke 12/12 at `4f4477fe` (closure record: tracking card
  2026-08-06-human-rounds-close-modularization-before-further-tiers).
  Residual, tracked forward (not blocking the tier's own acceptance): the
  coverage-driven dead-code sweep named in the tier description was executed
  only as the batch-L shim retirement; any further dead-code hunting rides
  Tier 4's service extraction.
- [x] Tier 3 test harness — DONE 2026-08-05, all 8 slices of
  [tier3-harness-design.md](tier3-harness-design.md): e2e 12 browser tests
  (login/TOTP, cancel+waitlist, referral+AI, Pacientes de hoy, intake QR),
  routes tier 13 files/47 tests parallel-safe, cassettes for
  HSI/PUCO/RENAPER/Meta (replay-only, scrubbed, staleness/shape audit),
  run ladder Stop-hook→pre-commit(~2s)→**`scripts/ci_local.sh`**→devbox
  nightly LaunchAgent. **"DONE" = locally verified and adversarially
  proven (2026-08-06, see the design doc's "Verification status"
  section) — hosted `ci.yml`/`nightly.yml`/`chromatic.yml` have never
  executed (GitHub Actions billing-frozen) and now run on
  `workflow_dispatch` only by deliberate cost decision, kept as spares,
  not the enforcing path.**
- [x] Tier 4 re-platform — **engineering-closed 2026-09-02** per
  [audit-acceptance-checklist.md](audit-acceptance-checklist.md): the
  physical-passkey soak is EXTERNAL (Pinamar staff key), production worker
  activation is the one recorded decision still pending, and the remaining
  daemons stay on threads by design. Record as of 2026-08-21:
  `app.py` is a 97-line composition root, `server.py` is a 33-line dark-mode
  entrypoint, and the ordered composition inventory contains 245 routes plus
  three middleware layers. The executable fallback census classifies all 565
  source rows as native (382 unique routes), drives 287 concrete probes, and
  finds no terminal compatibility or protocol exception. The complete legacy
  and ASGI managed gates cover the complete backend, frontend, route,
  cassette-replay/audit, and E2E surfaces with an explicit expected-skip
  allowlist. Integration, deployment, and the exact-revision public soak are
  **closed**: revision `664cf0b6` was fast-forwarded to `main` on 2026-08-21;
  production and demo both finished deployment records on that exact revision;
  both sites returned 200 to GET and HEAD `/health` and `/ready`; both smoke
  suites were green; and the demo's durable refresh read-back reported the
  current demo day complete. The non-sensitive verification record is
  [deployment evidence for `664cf0b6`](../pinamar/deployment-evidence-2026-08-21.md).
  The public ASGI rollout itself is **closed**:
  the demo has used `TURNOS_ASGI=1` since the 2026-08-08 readiness soak,
  and on 2026-08-19 current Coolify configuration plus exact-SHA deployments
  verified the same topology on production `pinamar.humanrounds.org`;
  the 2026-08-10 admission-prep slice adds **demo-only**, durable refresh
  evidence to the existing admin-only System surface: it is green only after
  assets, referrals and a read-back of the current demo day complete; a
  missing, partial, failed, stale or unreadable record is non-green. It
  proves neither process liveness (that remains `/health`) nor staff-passkey
  compatibility, and never changes production behavior while demo mode is
  off. Its local gate is the disposable-Postgres completion + Alembic
  upgrade/rerun suites, System-status suite, and legacy/ASGI route-parity
  check. A future production-flag rollback remains: unset `TURNOS_ASGI` and
  redeploy the same revision; this evidence migration itself is forward-only.
  Outside documentation and tests, the legacy `{{t.*}}` pipeline now has 82
  source occurrences across 7 files, including 68 in `admin.html`. Static
  fingerprinting now covers the whole first-party tree (slice 6, batch 3:
  the runtime-loaded assets resolve through the `window.SANA_STATIC` client
  manifest); the final intake module assets now use exact fingerprinted
  manifest entries and the first-party source invariant rejects manual `?v=`.
  Lottie (vendor hygiene), dormant
  redirected artifacts and tooling references are not counted as live
  migration debt. The queue migration
  now covers EIGHT jobs in the declarative worker registry (`worker._JOBS`,
  real worker topology outside the web process — `worker.py` entrypoint,
  health/heartbeat, graceful shutdown, supervised subprocess or standalone
  deployable behind `TURNOS_QUEUE_WORKER`): waitlist + reminders (business
  queues, batch 1, not activated), housekeeping table purges + referral-file
  retention (batch 2, 2026-08-08), the two AI-cache warmups —
  chart-summary pre-generation + translation-cache warmup (batch 3,
  2026-08-08, the first outbound-capable pair, native per-task execution
  lock, startup sweeps preserving each thread twin's boot grace) — and, as
  of batch 4 (2026-08-09), the durable HSI journal sweep (`hsi_sync`,
  self-rescheduling rather than periodic-cron — see
  [hsi.md](../architecture/hsi.md) — with its own steady-state watchdog and a
  Postgres advisory lock closing a concurrent-cold-start race, both
  proven with real two-worker Postgres suites), plus the batch-5 data-volume
  backup sweep: completion-anchored self-rescheduling preserves arbitrary
  configured intervals and the 60-second first grace, while a session advisory
  lock spans snapshot, B2 acceptance, and the Postgres journal commit across
  worker replicas or temporary web/worker topology skew. The companion
  operator-only recovery module records immutable B2 version receipts for new
  uploads and supports exact-id catalog/reconciliation/restore plus dry-run
  retention planning; it is not a worker, route, boot hook, or auto-delete
  setting. Legacy journal rows and any missing/orphan/hide/unknown evidence
  are protected, and an exact remote deletion remains a separately reviewed
  manual step. `hsi_resync`, hsi_sync's thread/manual fallback,
  deliberately remains unregistered and purely on-thread in every topology
  (`TURNOS_QUEUE_WORKER` never affects it). Its eligible outbound rows now
  share `hsi_sync.intent_for_row()` through `enqueue_and_replay_row()`, so
  exact durable replay owns the idempotency key, lease, checkpoint, remote
  probe, and local acknowledgement without making the fallback a second queue
  or inbound-reconciliation owner. The daemons still
  on threads are hsi_resync (above) plus the process-local or externally-
  coupled rest: demo_refresh, the cache/avatar in-process
  warm-ups (can never leave the web process), and ai_health —
  each needs its own persistence/safety/rollback proof before moving.
  Exact worker activation and the live queue list remain deploy-time evidence,
  never an inference from this registry. Alembic is the sole normal-boot
  migration authority; `migrations.apply_pending()` is restricted to the
  one-time bridge for a genuinely PARTIAL historical shape before Alembic
  convergence. Stored-shape passkey and successful-auth parity are proven;
  the hand-rolled passkey fallback and legacy cookie verifier remain, and the
  only passkey evidence still unavailable locally is a soak with a physical
  hospital staff key. Remaining Tier 4 closure items are that physical-passkey
  check, queue
  migration of the REMAINING daemons named above (eight jobs done across
  five batches; hsi_resync stays deliberately thread-only even though its
  outbound row replay is now durable, not just "not yet migrated"; the process-local/
  AI-health set stays thread-only because it structurally can't
  or hasn't been proven safe to move), Jinja migration off `{{t.*}}`, and
  no remaining manual-version asset work: module-owned interview assets use
  their exact fingerprinted module registry, and runtime-loaded assets use
  the `window.SANA_STATIC` client manifest. See
  [tier4-replatform-design.md](tier4-replatform-design.md)'s "Tier 4 route
  migration — CLOSED" section for the itemized remaining-work list.
- [x] Tier 5 frontend — **engineering-closed 2026-09-02 under the deviations
  recorded in [audit-acceptance-checklist.md](audit-acceptance-checklist.md)**
  (ES-module migration, story-per-component enforcement and hosted Chromatic
  are deliberate deviations, not open work). Milestone record as of 2026-08-31: Two ESM facades,
  pinned vendored Preact+HTM, one calendar island, `tokens.css`, the
  screen-manifest contract, and twenty-three direct admin screens (`/admin/account`
  is the universal staff self-service twin and the one expired-2FA repair
  surface admitted by exact path; `/admin/legal` is the admin-only
  terms + privacy twin with client-owned `?doc=` state; `/admin/appearance`
  is the admin-only appearance-and-access twin whose controller degrades per
  block when a module-owned API is 404; `/admin/hsi` is the
  admin-only turnos-gated HSI-synchronization twin whose controller was
  extracted behavior-frozen out of `admin-app.js` into `admin-hsi.js`;
  `plan` has its own `/admin/plan` direct twin, and `/admin/login` is the
  public direct gate whose successful logins land role-aware on a direct
  screen — no destination is hash-only anymore) all carry tests —
  real mechanisms, not cosmetic. `admin.html` and the hash shell were deleted
  on 2026-09-01 (`1c7afc93`); 15/15 declared sections are direct screens.
  The three pilot defects the audit
  named were fixed the same day (commits 7dfb0896/a9ebb58f/8e310d43): the
  pilot renders hash-free via `window.SANA_ADMIN_SCREEN` (guard test
  forbids `location.hash =` in screen templates), the first-login nav race
  is closed by `SanaAdminSections.refresh()` after auth (e2e no longer
  reloads), and the shared shell has a real narrow layout with a 390px
  browser assertion plus a CSS anti-drift guard. Twelve stories exist,
  story-per-component isn't enforced and hosted Chromatic has never run
  (GitHub Actions billing-frozen) — recorded as deviations in the
  acceptance checklist. The hash shell was retired once every enabled
  screen had a direct-URL twin and ASGI had been permanent for real users
  (since 2026-08-19).
- [ ] Tier 6 data/delivery — design revised 2026-08-06 against 8 Codex
  audit corrections (safe slice order, fail-closed pack contract,
  operational integrity for the private-data move, truthful per-profile
  credentials, self-hosted claims as acceptance conditions, narrowed
  benchmark claim, corrected `workflow_dispatch`/billing-lock claim,
  artifact integrity) — see
  [tier6-delivery-design.md](tier6-delivery-design.md). Slice 0 readiness
  is in tree. A synthetic private-pack evacuation rehearsal (2026-08-19,
  `scripts/rehearse_private_pack_evacuation.py`,
  `test/test_private_pack_evacuation_rehearsal.py`) proves inventory,
  archive, restore, integrity, and identical in-process rendering on
  fictional packs only, with import/suite isolation from the checked-in
  private pack, CLI before/after render parity, and metadata-independent
  archives. The real private repository, encrypted backup, production volume,
  restore drill, and public-tree removal were completed and exact-revision
  deployed on 2026-08-20. Production continues to render the externally held
  Pinamar pack; demo and the public repository contain only fictional/public
  packs.
- [ ] Tier 7 publication

Current verification (2026-08-20): managed legacy and ASGI CI are green across
backend, frontend, route parity, cassette replay/audit, and E2E. The dated
2026-08-05 failures were repaired and are no longer accepted baseline red.
