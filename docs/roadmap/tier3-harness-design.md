# Tier 3 — enforcing test harness (design)

Status: BINDING design for Tier 3 (`open-source-readiness-plan.md`, "Tier 3 —
Enforcing test harness"). Written 2026-08-05 against `origin/main` at `4936e18`
(Tier 2's framework + pilot landed, deployed and smoked green). Same contract as
`module-contract-design.md`: what exists today and is NOT re-done, per-area design
with concrete file paths, explicit out-of-scope, sequenced slices each independently
landable and sized for one executor, then the test plan. This doc merges into
`docs/architecture/` once implemented; until then it is what an implementation is
reviewed against.

## What exists today (audited before writing this — read before touching any of it)

Three test surfaces already run, at three different depths, and this design EXTENDS
each one rather than replacing it:

1. **Backend unit/integration suite** — `test/test_*.py`, 144 files, ~3000 tests
   (2907 backend / 836 frontend at the last recorded gate,
   `module-contract-design.md`'s baseline; grown since). Runs single-process,
   single-threaded, via `unittest discover`. DB-backed files (48 of 144) use
   `test/_dbtest.py`'s `PerModuleDb`: **one throwaway database per test MODULE**,
   created once in `setUpModule`, reused by every test in that file, dropped in
   `tearDownModule`. This works only because `unittest discover` never runs two
   modules concurrently in one process — `_dbtest.py:99-124`'s teardown comment
   documents a DELIBERATE `DATABASE_URL` leak across modules that a real parallel
   runner would turn into a live bug (`test_turnstile.py` depends on inheriting the
   just-dropped test DB's URL as its own "base is down" case). **Do not parallelize
   this suite by pointing a process pool at `unittest discover` — the fixture
   lifecycle isn't built for it.** A handful of files additionally boot a REAL
   `server.Handler` on an ephemeral port in-process via a `_LiveServerMixin`
   (`test/test_http_server.py:34-60`, duplicated with small variations into
   `test/test_module_lifecycle.py` and `test/test_module_runtime.py`) — no
   subprocess, no browser, just a `ThreadingHTTPServer` wrapping the real handler.
   This is the closest thing today to a "routes tier," and Area 2 below extracts
   and generalizes it rather than inventing a fourth copy.
2. **Frontend suite** — `node --test test/*.test.mjs`, stdlib only, no npm
   dependency, 836 tests. Untouched by this design.
3. **Browser e2e (Tier 2's own harness)** — `test/e2e/*.e2e.mjs` + `test/e2e/lib/
   harness.mjs` + `test/e2e/fixture.py`, run via `npm run e2e` (gate: missing
   Playwright/Chromium/Postgres FAILS loudly, `requireOk()`) or `npm run e2e:dev`
   (opt-in skip for local convenience). One throwaway Postgres DB + one
   `python3 server.py` subprocess + one headless Chromium **per file**, hermetic
   env (`TURNOS_ENV_FILE` points at an empty temp file so a developer's real
   `.env` — real TOTP/Turnstile secrets included — is never consulted), pinned to
   the public synthetic `packs/example-town` pack via `PACK_CHAIN`, never the
   private Pinamar pack. 7 tests today across 3 files:
   - `admin.e2e.mjs` — password login + Solicitudes queue; Sistema/integrations hash-route.
   - `intake.e2e.mjs` — entry page + chat token; **QR door entry already covered**
     (`"QR door entry route responds..."`) — Tier 3's "intake QR" bullet is
     already satisfied, see Area 1's note.
   - `booking.e2e.mjs` — order-first wizard reaches "Subí tu orden" (**never
     uploads a referral or completes the AI reading** — this is the actual gap);
     "Más horarios" reaches real slot selection.
   - `fixture.py` seeds via the app's own functions (`db.interview_create`,
     `intake_admin.get_or_create_door_token`), never hand-built rows, and hands
     back the DB URL through a mode-0600 file, never stdout — same doctrine every
     new fixture flag in this design follows.

Also load-bearing and NOT re-derived here: `connectors/__init__.py`'s seven
capability ABCs and their degradation contract (`docs/connectors.md`); `pii.py`'s
scrub/restore pass and its explicit "referral image/PDF is the one deliberate
exception" carve-out; `admin_auth.py`'s home-grown TOTP (`_hotp()`,
`_totp_valido()`, no `pyotp` dependency); `db.py`'s existing
`pg_advisory_lock(hashtext('turnos:migrations'))` idiom for cross-process schema
mutation safety — Area 2 reuses this exact idiom, not a new locking pattern.

## Area 1 — Browser e2e tier expansion

Extends `test/e2e/lib/harness.mjs` and `test/e2e/fixture.py` unchanged in mechanism
(hermetic env, synthetic pack, gate-vs-convenience, mode-0600 DB-URL handoff, seed
via real app functions). New coverage is new **fixture flags** on `fixture.py` plus
new or extended `*.e2e.mjs` files — never a parallel bootstrap path.

**Real gaps, after auditing the 7 existing tests against the roadmap's 5 flows:**

| Roadmap flow | Status | What's missing |
|---|---|---|
| Booking wizard, order-first | Covered | — |
| Booking wizard, **referral branch** | Partial | Reaches "Subí tu orden," never uploads, never exercises the fake-AI reading, never completes a real appointment |
| Queue / "Pacientes de hoy" | Missing | Only "Solicitudes" (the requests queue, a different admin screen) is covered — `doctor_appointments.py`/`admin-queue.js`'s "Pacientes de hoy" isn't |
| Intake QR | **Already covered** | `intake.e2e.mjs`'s "QR door entry route responds" test — no new work, cite it in the plan update so nobody re-adds it |
| Cancel + waitlist | Missing | No test exercises a patient cancel promoting a waitlisted patient |
| Login | Partial | Password-only happy path exists; wrong-password rejection and TOTP-protected login are untested |

**Design per gap:**

- **Referral completion** — extend `booking.e2e.mjs` (same file, same
  `sana_turnos_test_e2e_booking` DB, same `electrocardiograma-hospital` service
  already seeded) with a third test: upload a referral image via the existing
  "Subí tu orden" form, let `TURNOS_FAKE_AI=1`'s canned reading resolve (already
  the server's default under the harness env — no new fixture flag needed for the
  AI side), assert the reading surfaces on Confirmá, submit, assert a real
  `booking_requests` row lands. Reuses the harness's existing service/DB — no new
  `fixture.py` flag.
- **Queue / Pacientes de hoy** — new `test/e2e/queue.e2e.mjs`, own DB
  (`sana_turnos_test_e2e_queue`). `fixture.py` gains `--queue`: seeds several
  TODAY-dated appointments across states (approved, arrived, attended) through the
  same functions the app uses to create them (never hand-built rows — the intake
  fixture's own stated principle), so a schema drift in the queue's read path
  breaks this fixture the same way it breaks the real feature.
- **Cancel + waitlist** — new `test/e2e/cancel-waitlist.e2e.mjs`, own DB.
  `fixture.py` gains `--waitlist`: seeds one full slot with an approved
  appointment plus one waitlisted patient (via `waitlist.py`'s own creation path).
  The test drives the patient-facing cancel action, then asserts the waitlisted
  patient's row reflects promotion/offer — whatever `waitlist.py`'s real observable
  side effect is (an `appointment_offers` row, a notification queued) rather than
  re-deriving the mechanism here.
- **Login** — extend `admin.e2e.mjs` with two tests: wrong password → rejected,
  stays on login; TOTP-protected operator → succeeds. `fixture.py` gains `--totp`:
  creates an operator with a known secret and returns **both** the secret and a
  currently-valid code, computed server-side by importing `admin_auth._hotp()`
  directly (the same function the real login path calls) — this is the reason it's
  a `fixture.py` flag and not a JS TOTP implementation: no new npm dependency, and
  the fixture uses the exact same code the app verifies against, so a change to
  the TOTP window/algorithm breaks the fixture the same day it breaks production,
  not silently.

Everything above stays within the existing `requireOk()`/gate-vs-convenience
contract, the existing hermetic-env mechanism, and the existing
`example-town` pack — no new harness primitive, only new fixture vocabulary and
new spec files, exactly as the instructions require.

## Area 2 — Routes tier (in-process, disposable fixtures, parallel-safe)

**What this is FOR, stated up front so it doesn't get relitigated as "a faster
copy of the backend suite":** the backend suite (Area above, #1) already covers
business logic exhaustively; e2e covers real browser flows end-to-end. Neither is
a good fit for "does this HTTP route return the right status/JSON/headers for
every role, fast, and can I run thousands of these in parallel as the suite
grows" — that gap is what `test_http_server.py`'s `_LiveServerMixin` started
answering (3 files today) without a parallel-safe fixture story. This tier
generalizes that pattern; it does NOT replace `test/test_*.py` or the e2e suite,
and no existing test moves here as part of this design.

### Foundation

- **Extract `_LiveServerMixin`** out of `test/test_http_server.py` (and its two
  near-duplicates in `test_module_lifecycle.py`/`test_module_runtime.py`) into a
  new shared `test/_livehttp.py`, mirroring `test/_dbtest.py`'s naming and
  "shared boilerplate, not a framework" scope. Existing three call sites switch to
  importing it — a pure de-dup, no behavior change, its own small commit.
- **New directory `test/routes/`**, sibling to `test/e2e/`, holding
  `test/routes/test_*.py`. Deliberately NOT discovered by the existing
  `python3 -m unittest discover -s test -p "test_*.py"` command (that command's
  root is `test/`, not `test/routes/` — no code change needed to keep them
  separate, just don't add `-s test/routes` anywhere). Reason: this tier's fixture
  lifecycle is incompatible with `_dbtest.py`'s per-MODULE-DB, deliberate-URL-leak
  design (documented above) — mixing them in one discovery run resurrects exactly
  the cross-module leak class `_dbtest.py`'s own comment warns against.
- **New `test/_routes_fixture.py`** — the disposable-fixture + parallel-safety
  layer:
  - A small, fixed **pool of worker databases** (`sana_turnos_test_routes_w0` …
    `_wN`, N = `--workers` at run time, default `min(os.cpu_count(), 4)`). Each
    worker process owns exactly one DB for its lifetime — never per test case.
    Rejected alternative: CREATE DATABASE per test case (clean but ~100-300ms
    overhead × a tier meant to run at pre-commit speed defeats its own purpose);
    rejected alternative: one shared DB + savepoint-per-test rollback (would need
    every `db.py` call in the test path pinned to one connection for the test's
    duration — `db.py`'s connection-per-call shape makes that a much larger,
    riskier change than this design's budget).
  - **Disposal = per-test TRUNCATE, not per-test CREATE DATABASE.** Before each
    test method, the fixture runs `SELECT tablename FROM pg_tables WHERE
    schemaname='public'` (never a hand-maintained table list — self-updating as
    `db.py` adds tables, avoiding exactly the kind of drift a hardcoded list would
    invite) and `TRUNCATE <tables> RESTART IDENTITY CASCADE` in one statement.
    Sub-millisecond on an empty-to-small test dataset; this is what makes "fast
    enough for a pre-commit rung" true.
  - **The advisory-lock mutex** guards ONLY the one-time-per-worker bootstrap
    (DROP/CREATE DATABASE + first schema application), the actual race when N
    worker processes start simultaneously against ONE Postgres server. Reuses the
    exact idiom already in `db.py:1027` (`pg_advisory_lock(hashtext(...))`),
    scoped to a new lock name per worker DB
    (`hashtext('turnos:routes-test-pool:sana_turnos_test_routes_w<N>')`), released
    immediately after. No new locking pattern class — the mutex is scoped to
    startup, never taken per-test (TRUNCATE within one worker's own DB needs no
    cross-process coordination).
  - A `RouteTestCase` base class (built on the extracted `_LiveServerMixin`) that
    wires the worker's DB via `DATABASE_URL` + calls the fixture's per-test
    truncate in `setUp`/`tearDown`.
- **New driver `scripts/run_routes_tests.sh`** (thin wrapper, not a new test
  runner framework): discovers `test/routes/test_*.py`, splits files across N
  worker processes (round-robin by filename, not by test count — simplest correct
  split), each process exporting `TURNOS_ROUTES_WORKER=<index>` so
  `_routes_fixture.py` binds to that worker's DB, then waits on all processes and
  fails the whole run if any exits non-zero — same fail-loud doctrine as
  `requireOk()`, no silent partial green.

### Pilot scope (proves the mechanism, doesn't chase coverage)

10-20 route tests against a couple of already-well-understood endpoints (e.g. a
handful of `booking_requests.py` and `admin_auth.require_admin` role-gating
checks) — enough to prove: (a) two worker processes booting concurrently never
collide on CREATE DATABASE (run the driver with `--workers 4` back-to-back 10x in
CI, zero flakes, is the acceptance bar); (b) truncate-based disposal actually
isolates tests (a test that leaves data behind must not leak into the next test in
the same worker). Coverage EXPANSION beyond this pilot is deliberately left
open-ended (see slices below) rather than pre-specified — this design's job is the
mechanism, not an exhaustive route inventory.

## Area 3 — Record-replay cassettes (HSI, PUCO, RENAPER, Meta)

**The seam:** every one of these four connectors calls the network through
**stdlib `urllib.request`** — confirmed by reading each module's imports
(`connectors/hsi/hsi_client.py`, `connectors/puco/puco.py`,
`connectors/renaper/renaper.py`, `otp_send.py` for `waha`/`meta`). None use
`requests`. This means a cassette layer needs to wrap exactly ONE call site
(`urllib.request.urlopen`), not four different HTTP client APIs — and it means no
new pip dependency (`requirements.txt` stays psycopg + Pillow + segno; pulling in
`vcrpy` — which monkeypatches at the socket/urllib3 level for client libraries this
repo doesn't use — would be solving a problem this codebase doesn't have).

**What already exists and is NOT re-done:** `TURNOS_FAKE_RENAPER`/
`TURNOS_FAKE_PUCO` (hand-written canned responses inside the connector itself,
deterministic, no recording); `TURNOS_SKIP_HSI=1` (no push at all) and direct
monkeypatch of `hsi_client.push_turno` (`test_booking_requests.py`, relying on the
compat-shim's shared-module-object guarantee, `docs/connectors.md`'s "Compat
shims" section). **These stay** — they're the right tool for "I don't care about
this connector's real shape, just that the caller degrades correctly" tests. The
NEW thing cassettes add: a way to assert the caller still parses the connector's
**real, current response shape** correctly, without hitting the real network in
CI and without hand-maintaining a canned dict that can silently drift from
reality. `TURNOS_FAKE_AI` is the deliberate contrast, not a precedent to extend
here — an LLM call is non-deterministic and costly to replay-record meaningfully;
these four are request/response REST-or-RPC calls with a stable shape, which is
exactly what makes them cassette-able.

### Design

- **New `test/cassettes/_vcr.py`** — a thin recorder/player around
  `urllib.request.urlopen`:
  - **Record mode** (`TURNOS_CASSETTE_MODE=record`, never set in CI — see below):
    wraps the real `urlopen`, captures `(method, url, request headers minus
    Authorization, request body, response status, response headers, response
    body)` per call into memory, and on context exit runs every string leaf of
    every captured interaction through **`pii.py`'s `scrub()`** (the same
    reversible pseudonymization used before any external AI call) plus a small,
    connector-specific field-name denylist (`dni`, `nombre`, `apellido`,
    `telefono`, `email` and each connector's own local names for these — defined
    once per connector next to its cassette directory) before writing JSON to
    disk. This directly satisfies the roadmap's explicit requirement; it is a
    best-effort net, same honest limit `pii.py` itself documents — **a human
    reviews the diff of any newly recorded cassette before it's committed**, this
    is not a fire-and-forget pipeline.
  - **Replay mode** (default — no env var needed, this is what every test run
    uses): intercepts `urlopen`, matches the call against the loaded cassette by
    `(method, normalized path, a per-connector whitelist of stable body/query
    fields — excluding volatile ones like timestamps/nonces/session tokens)`, and
    returns the recorded response verbatim. **An unmatched call in replay mode is
    a hard failure** — raises, never silently falls through to the real network.
    This mirrors the e2e gate's fail-loud doctrine: a cassette test that quietly
    hit the real HSI/RENAPER/Meta endpoint in CI would be a worse bug than a red
    test.
  - Cassettes live at `test/cassettes/<connector>/<scenario>.json` — small,
    scrubbed, committed. `<connector>` ∈ `{hsi, puco, renaper, meta}`.
- **Recording is a deliberate, manual, occasional act — never part of any
  automated rung.** Rationale, stated explicitly so nobody wires it into CI or
  nightly later: HSI's only reachable instance is a local dev container
  (`historiaclinica/hsi:v2.71.2` on `:8280`, brought up by a script outside this
  repo, per `docs/architecture/hsi.md`) — a GitHub-hosted runner has no path to
  it. RENAPER/PUCO/Meta's real endpoints touch a national ID registry, a national
  insurance lookup, and a real (test) WhatsApp Business number with per-message
  cost — none of these belong behind a scheduled cron with live credentials in
  CI secrets for a philanthropic prototype touching a real hospital's systems.
  Recording stays a `scripts/record_cassette.py --connector hsi --scenario
  push_turno_ok` run by a human on a machine with real reachability (Tailscale to
  `hsi-lab`, a local HSI container, etc.), with the diff reviewed before commit —
  same posture as any other credential-adjacent change in this repo.
- **What nightly DOES check** (see Area 4): a self-contained **cassette
  staleness/shape audit** — no network, just (a) each cassette's recorded-at
  metadata age surfaced as a warning past a threshold (e.g. 90 days), (b) each
  cassette's response body validated against a lightweight structural
  expectation (the field names/types the caller actually reads, not a full
  schema) so a cassette that's silently gone stale in SHAPE — not just staleness
  in age — is at least flagged for a human to go re-record.

### Pilot connector

**HSI first** — it's the connector with the richest existing test surface to
retrofit against (`test_hsi_resync.py`, `test_hsi_resolver.py`) and the one
CLAUDE.md calls "a hard requirement," so proving the cassette pattern here first
gives the clearest before/after signal. One scenario cassette
(`push_turno_ok.json`, the plain-success path) is the acceptance bar for the
pilot slice — PUCO/RENAPER/Meta follow mechanically once the pattern is proven
(see slices).

## Area 4 — The four-point run ladder

**Read before designing further:** this historical inventory predates the
2026-08-21 retirement of `vbump-drift-guard.py`. Today there is no git `pre-commit` hook (no
`.git/hooks/pre-commit`, no `.pre-commit-config.yaml`) and no Claude Code `Stop`
hook in this repo's `.claude/settings.json` (only `PreToolUse`/`PostToolUse`
today — `admin-section-wiring-guard.py`,
ADVISORY, `systemMessage`-only, never exit-1-blocking, per their own docstrings:
*"NO bloquea — avisa"*). `.github/workflows/ci.yml` has three jobs (backend ~3000
tests + Postgres + poppler/ffmpeg install, frontend 836 tests, e2e 7 Playwright
tests), all on every push/PR, no `schedule:` trigger anywhere — there is no
nightly today.

**Cost-tiering decision, stated once so it isn't re-litigated per rung:** the
ladder gets STRICTER and SLOWER as it goes right, and each rung only re-runs what
changed in scope from the one before it — it never means "run everything at every
rung."

| Rung | Trigger | Runs | Blocking? | Budget |
|---|---|---|---|---|
| **Stop-hook** | Every Claude Code turn that touched files | A small, fixed smoke subset: `test_module_manifest.py`'s schema validation (already fast/cheap) + a hand-picked ~10-20 canary tests from the routes tier (Area 2) once it exists | **Advisory only** (`systemMessage`, consistent with this repo's two existing hooks) | Low single-digit seconds |
| **pre-commit** | `git commit` | Frontend suite (836, stdlib, seconds) + the full routes tier (Area 2, parallel, Postgres advisory-lock pool) | **Blocking** (non-zero exit fails the commit) | Under ~20s target — a hook that takes minutes gets `--no-verify`d in practice, so this is a hard design constraint, not an aspiration |
| **CI** | push/PR | Existing 3 jobs unchanged (backend full unittest, frontend, e2e) **+ new `routes` job** (Area 2, with its own Postgres service container) **+ new `cassettes` job** (Area 3, replay-mode only, no network) | **Blocking** (merge gate — extend GitHub's required-checks list to the two new jobs once green) | Minutes, same order as today |
| **nightly** | New `schedule:` cron in a new `.github/workflows/nightly.yml` | Everything CI runs, **plus** the cassette staleness/shape audit (Area 3 — no re-recording, no live network, see rationale above) | **Not a merge gate** (nothing to block — it's scheduled, not tied to a PR); a red nightly is a regression to triage, surfaced via GitHub's default failure-email on scheduled workflows for v1 — a richer alert (Telegram) is a follow-up, not required for this design to land | Whatever the full suite costs; this is the rung allowed to be slow |

**"Red tests blocking done" — what this means concretely:** a task is not "done"
(in the sense this repo's own doctrine uses the word) unless pre-commit and CI are
both green. Stop-hook is advisory by design (it fires mid-task, before a
commit even exists, and this repo's own hook doctrine keeps that class of check
non-blocking). Nightly red is a separate signal — an open regression to fix, not
something that retroactively un-does a past "done."

**Explicitly NOT part of this ladder** (named so nobody folds it in later):
`scripts/smoke_prod.py`/`smoke_demo.py` are POST-DEPLOY verification against a
live deployed URL — a different concern (deploy verification, not the test
harness) that already has its own doctrine in `docs/architecture/deploy.md`.

## Out of scope for Tier 3 (recorded so nobody scope-creeps it)

- Migrating any existing `test/test_*.py` file into `test/routes/` — the routes
  tier is additive, not a migration target for this design.
- REFEPS, MSD/slot_source, Alepo/Marqui — `connectors.md`'s other capabilities.
  The roadmap names HSI/PUCO/RENAPER/Meta specifically; the cassette pattern
  generalizes to the rest later, same shape, not pre-built here.
- Rewiring connector call sites through `connectors.active_connector()` —
  `docs/connectors.md`'s own "Remaining work," unrelated to testing.
- A Telegram alert integration for nightly failures — GitHub's default
  scheduled-workflow failure email is the v1 signal; richer alerting is a
  follow-up.
- Any FastAPI/Jinja re-platform work (Tier 4) — this harness is built to survive
  that migration, not to precede or block it.
- Parallelizing the EXISTING `test/test_*.py` backend suite — explicitly rejected
  above; it stays serial, single-process, as `_dbtest.py` requires.
- A Settings-admin UI for any of this (cassette review, routes-tier dashboards) —
  CLI/CI-surfaced only, matching `connectors.md`'s own precedent for connector
  management.

## Execution slices (sequenced, each independently landable, one executor each)

1. **E2E: login + cancel/waitlist.** `fixture.py` gains `--totp`/`--waitlist`;
   extend `admin.e2e.mjs` (wrong-password, TOTP success); new
   `cancel-waitlist.e2e.mjs`. Gate: `npm run e2e` green, new tests fail red on
   `git stash` of the flow they cover (proves they'd have caught a regression).
2. **E2E: referral completion + queue.** Extend `booking.e2e.mjs` with the
   upload→AI-reading→confirm test; `fixture.py` gains `--queue`; new
   `queue.e2e.mjs`. Same gate as #1. Independent of #1 (different files/DBs).
3. **Routes tier foundation.** Extract `_LiveServerMixin` → `test/_livehttp.py`;
   build `test/_routes_fixture.py` (worker pool, advisory-lock bootstrap,
   per-test truncate); `scripts/run_routes_tests.sh`; 10-20 pilot tests in
   `test/routes/`. Gate: `--workers 4`, 10 consecutive runs, zero DB-collision
   flakes; a deliberately data-leaving test proves truncate isolation catches it
   (red without the truncate step, green with it).
4. **Routes tier coverage.** Add route-level tests for booking/admin/doctor-
   agenda endpoints using slice 3's harness — open-ended, land in whatever
   batches make sense; no fixed target count, just "more than the pilot's 10-20."
5. **Cassette foundation — HSI pilot.** `test/cassettes/_vcr.py`
   (record/replay wrapping `urlopen`, PII scrub pass); one recorded
   `push_turno_ok.json`; retrofit `test_hsi_resync.py` (or a new
   `test_hsi_cassette.py`) to replay against it. Gate: replay-mode test fails
   loudly if the cassette file is deleted (proves it's actually driving the
   assertion, not incidentally passing); a manually-corrupted cassette field
   flips the test red.
6. **Cassette expansion — PUCO, RENAPER, Meta.** Mechanical repeat of slice 5's
   pattern, one scenario each. Depends on slice 5 (reuses `_vcr.py` unchanged).

   **Landed 2026-08-05.** One genuinely required `_vcr.py` extension, one
   deliberate non-extension:

   - **`_StubResponse` gained `__enter__`/`__exit__`** (plain pass-through,
     `return self` / `return False`). HSI's pilot never needed it —
     `hsi_client.py:219` calls `urlopen()` bare — but PUCO
     (`connectors/puco/puco.py`'s `_consultar_ws131`), RENAPER
     (`auth_renaper.py`'s `_post_form`) and Meta (`otp_send.py`'s
     `_enviar_meta`/`_enviar_whatsapp`) all write `with
     urllib.request.urlopen(...) as resp:` — replay raised `TypeError:
     '_StubResponse' object does not support the context manager protocol`
     before any of the three connectors' own logic ran. This is the one
     change the "reuse `_vcr.py` UNCHANGED unless genuinely required" rule
     anticipated; documented in `_StubResponse`'s own docstring too.
   - **Not extended: `_decode_body`/`_match_key` stayed JSON/text-only, no
     dedicated "form" body kind.** RENAPER's OIDC token/userinfo exchange
     (`auth_renaper.py`'s `_post_form`) sends `application/
     x-www-form-urlencoded`, which `_vcr.py` captures as one opaque text
     blob rather than a dict — so `PII_FIELDS`' per-key force-tokenizing
     never reaches into that OUTBOUND body (only the JSON response side,
     where the identity claims actually live, is covered). Not fixed here:
     every value in `renaper/login_callback_ok.json`'s request body is a
     hand-picked fake fixture (`test-client-secret`, `test-authorization-
     code`), never a real credential, so there's nothing this pilot's own
     cassette needs protected — see that file's "source" note and
     `test/cassettes/renaper/fields.py`'s docstring. A live re-recording
     against the real IDP would need this solved first.
   - **Not extended: no path-templating whitelist.** PUCO's WS131 URL embeds
     the DNI directly in the PATH (`POST /sisa/services/rest/puco/{dni}`),
     not in a query/body field `PII_FIELDS`/`MATCH_FIELDS` can reach.
     `pii.py`'s generic numeric sweep also can't catch it on its own — its
     `_NUM_RE`'s `(?<![\w/])` lookbehind deliberately excludes digits
     immediately preceded by `/` (built to avoid mangling a URL, not to
     protect one). This pilot's cassette sidesteps the gap by construction
     (the DNI recorded is one of this repo's own synthetic 99-prefixed test
     fixtures, same one `test_hsi_cassette.py`'s pilot already commits in
     the clear), documented in `test/cassettes/puco/fields.py`. A live
     WS131 recording would leak a real DNI into the committed path verbatim
     until a connector-declared `PATH_ID_RE` (normalized both at scrub-time
     and at replay-match-time) is added — not built here, out of scope for
     a stub-derived pilot with nothing real to protect.
   - **Two of three cassettes genuinely recorded, one hand-authored.** PUCO
     and RENAPER's base URLs are env-overridable (`TURNOS_SISA_URL`,
     `TURNOS_RENAPER_TOKEN_URL`/`_USERINFO_URL`), so both were driven
     through the REAL connector functions against an in-process fake
     upstream (swap `urllib.request.urlopen`'s "real" callable the recorder
     wraps, no sockets), producing genuinely-recorded, genuinely-scrubbed
     cassettes — see their own "source" notes.  Meta's endpoint
     (`otp_send.py`'s `_enviar_meta`) hardcodes `https://graph.facebook.com`
     with no override, so `meta/enviar_texto_ok.json` is hand-authored to
     match the Cloud API's documented send-message response shape instead
     (`connectors production code is not touched to add one — out of scope
     for this slice`), honestly marked in its own "source" field.
7. **Run ladder — Stop-hook + pre-commit.** New `Stop` hook entry in
   `.claude/settings.json` (advisory smoke, depends on slice 3 existing for its
   canary subset — falls back to schema-validation-only until slice 3 lands);
   new `scripts/pre-commit.sh` + install step into `.git/hooks/pre-commit`
   (blocking: frontend + full routes tier). Depends on slice 3.

   **Landed 2026-08-05, measured on this repo's checkout (Postgres local,
   4-core default worker pool):**

   | Rung | Command | Measured |
   |---|---|---|
   | Stop-hook | `test_module_manifest.py` (discover) | ~0.1s |
   | Stop-hook | routes-tier canary, `scripts/run_routes_tests.sh` (11 tests, 3 files, default workers) | ~1.6s |
   | Stop-hook | **total** | ~1.7s — inside "low single-digit seconds" |
   | pre-commit | `node --test test/*.test.mjs` (878 tests) | ~0.35s |
   | pre-commit | `scripts/run_routes_tests.sh` (same 11 tests) | ~1.6s |
   | pre-commit | **total** | ~2s — well inside the ~20s budget |

   Both rungs currently run the SAME 11-test routes tier (slice 4's coverage
   expansion hasn't landed yet, so there's no separate "canary subset" to
   hand-pick from a larger pool — the whole pilot fits the canary window
   named above). `.claude/hooks/stop-smoke-guard.py` re-checks
   `test/routes/` exists at run time and skips the routes half if it
   doesn't, so the fallback-to-schema-only path still works if that
   directory is ever removed. Files: `.claude/hooks/stop-smoke-guard.py`
   (Stop hook, advisory, never exits non-zero, never emits
   `{"decision": "block"}`), `scripts/pre-commit.sh` (blocking, `SKIP_PRECOMMIT=1`
   bypass), `scripts/install-hooks.sh` (installer, documented in
   `CONTRIBUTING.md`'s "Run ladder — pre-commit hook" section).
8. **Run ladder — CI + nightly.** New `routes` and `cassettes` jobs in
   `ci.yml`; new `nightly.yml` (schedule cron: full backend + frontend + e2e +
   cassette staleness audit). Depends on slices 3 AND 5/6 (routes tier and
   cassette layer must exist before CI/nightly can run them) — lands last.

   **Landed 2026-08-05.** `.github/workflows/ci.yml` gained `workflow_call`
   to its `on:` block (so `nightly.yml` can reuse the whole job graph instead
   of re-declaring it) and two new jobs, inserted between `backend` and
   `frontend`:

   - **`routes`** — own Postgres service container (independent of
     `backend`'s, so the two never share live CREATE/DROP DATABASE traffic),
     runs `scripts/run_routes_tests.sh --workers 4` (the same driver
     pre-commit already runs locally, pinned to 4 workers so a hosted
     runner's CPU count never silently changes the collision-freedom claim).
   - **`cassettes`** — no Postgres, no service container: replay-mode
     cassette tests never touch the network by construction (`test/
     cassettes/_vcr.py`'s replay raises on any unmatched call rather than
     falling through to `urlopen`). Runs `python3 -m unittest discover -s
     test -p "test_*_cassette.py"` — the four connector-cassette suites
     already also run inside `backend`'s broad `test_*.py` discover; this is
     the dedicated, isolated signal that they specifically stay green and
     specifically stay network-free.

   New `.github/workflows/nightly.yml`: `schedule: cron: "0 5 * * *"` (05:00
   UTC = 02:00 ART) + `workflow_dispatch` (the design's own "prove the
   schedule's job definition is valid" test-plan step). Two jobs: `ci`
   (`uses: ./.github/workflows/ci.yml`, reusing backend+frontend+e2e+
   routes+cassettes verbatim) and `cassette-audit`, a new **`scripts/
   audit_cassettes.py`** — no network, no connector imports, no re-recording
   — that reads every committed `test/cassettes/<connector>/*.json` and
   checks two things per Area 3's "What nightly DOES check":

   - **Staleness** — `recorded_at` age vs. a 90-day threshold. Non-fatal by
     design: prints a `::warning::` annotation and keeps the job green, since
     an old cassette can still be correctly shaped.
   - **Shape** — each cassette's response bodies checked against the actual
     field names/types the connector's own parsing code reads off them (hand-
     derived once from each connector's source — `hsi_client.py`'s
     `diary_detail()`/`ensure_patient()`/`create_appointment()` return
     shapes, `puco.py`'s `_parse_respuesta()` XML tags, `auth_renaper.py`'s
     `_claims_reales()`/`_extraer_datos()` claim keys, `otp_send.py`'s
     `_enviar_meta()` reading `messages[0].id`). A shape mismatch prints
     `::error::` and exits 1 — this is what makes the run loud: a red job
     turns the whole scheduled workflow run red, and GitHub's own default
     failure-email for scheduled workflows is the only alert wired (per
     Area 4's explicit v1 scope — no Telegram integration here).
     Verified locally both ways: a hand-corrupted `appointmentDuration` key
     in the HSI cassette flips the audit to `exit 1`; a hand-backdated
     `recorded_at` prints the staleness `::warning::` but stays `exit 0`.

   Not attempted here (a GitHub repository *setting*, not a file this repo's
   worktree can express): flipping branch protection to require the two new
   `routes`/`cassettes` checks. The design's own wording is "once green" —
   do this after the first real run on `main` passes, not before.

## Test plan

- **Area 1** — each new/extended `*.e2e.mjs` test proves itself by failing red
  against the pre-fix behavior (e.g. the referral test fails on today's
  `booking.e2e.mjs` before the upload step is added) — the existing 7 stay green
  throughout, `npm run e2e` count grows from 7 to (7 + new) with zero regressions.
- **Area 2** — the parallelism claim is proven by repetition, not inspection: N
  back-to-back `--workers 4` runs with zero cross-worker DB collisions; the
  disposability claim is proven by one intentionally-leaking test that the
  pre-truncate version of the harness fails to isolate (red) and the truncate
  version catches (green) — a regression test for the fixture mechanism itself,
  not just for routes.
- **Area 3** — a replay test with its cassette file deleted must fail (proves the
  cassette is load-bearing, not decorative); a replay test whose cassette's
  response body is hand-edited to a wrong shape must fail (proves the caller's
  parsing is actually exercised); an unmatched-request-in-replay-mode case must
  raise, never silently pass through to the network (a fake local HTTP server
  bound to `127.0.0.1` during the test, asserting it received ZERO connections,
  is the concrete proof).
- **Area 4** — pre-commit: a deliberately failing routes-tier test must block
  `git commit` (non-zero exit), and `SKIP_PRECOMMIT=1 git commit` must bypass it
  (documented escape hatch, never used on a final pre-push commit per this repo's
  existing push doctrine); CI: the two new jobs must show up as required checks
  once green; nightly: a manual `workflow_dispatch` run (same trigger CI already
  supports) is how the slice proves the schedule's job definition is valid before
  waiting for the first real 24h cycle.

## Verification status (2026-08-06) — local complete, hosted never executed

**Read this section before trusting any "CI is green" claim about this repo.**
Two different things are true at once and must not be conflated:

- **Local reproduction is COMPLETE and GREEN**, run from a fresh
  `origin/main` worktree (`9d47f01`), throwaway Postgres databases only,
  `packs/example-town` (public synthetic pack, never Pinamar's private data):
  - Backend (`unittest discover -s test -p "test_*.py"`): **3036 tests, 0
    skipped, exit 0** (grew from the design's 2907-baseline estimate).
  - Frontend (`node --test test/*.test.mjs`): **878 tests, 0 fail, exit 0**.
  - Routes tier (`scripts/run_routes_tests.sh`, 13 files, 4 workers):
    **47 tests, exit 0**; 6 consecutive back-to-back runs, zero collisions.
  - Cassette replay (`test_*_cassette.py`) + `scripts/audit_cassettes.py`:
    **30 tests + 4 cassettes audited, exit 0** both.
  - E2E (`npm run e2e`, Playwright/Chromium, throwaway Postgres):
    **12/12 pass, exit 0**.
  - Adversarial proofs, each executed and reverted: missing Playwright
    makes `npm run e2e` FAIL loudly (not skip); a disabled `truncate_all()`
    call flips `test_fixture_isolation.py` red, restoring it goes green
    again; a hand-corrupted `appointmentDuration` field flips
    `audit_cassettes.py` to exit 1, restoring the cassette goes back to
    exit 0; an injected failing routes test blocks the real `git commit`
    hook (`scripts/pre-commit.sh` via `.git/hooks/pre-commit`), and
    `SKIP_PRECOMMIT=1` bypasses it as documented; `actionlint` passes clean
    on all three workflow YAML files.
  - One command now runs the whole thing: **`scripts/ci_local.sh`** — all
    five surfaces, per-surface exit code + runtime, a summary table, and a
    non-zero exit if anything is red. This is what a developer or the
    the dev machine nightly LaunchAgent (`scripts/nightly-local.plist`, install steps
    in that file's own header — not installed by this change) actually
    runs.
- **Hosted CI/nightly/Chromatic have NEVER executed Tier 3's jobs** — the
  private GitHub account exhausted its included Actions minutes for August
  2026 (a real cost problem: auto-running on every push/PR was burning
  roughly $10/day against a $0 hard stop once the included minutes run
  out), so every "run" GitHub shows for this period has zero steps and a
  billing annotation. That red/absent status was never test evidence and
  must never be read as one.
- **Decision (the product owner, 2026-08-05): the local ladder is now the ENFORCING
  path, not a stand-in for hosted CI.** `.github/workflows/ci.yml`,
  `nightly.yml`, and `chromatic.yml` all had their `push`/`pull_request`/
  `schedule` triggers removed — each keeps `workflow_dispatch` only, job
  definitions untouched, so a manual run is still possible if ever wanted.
  They are now manual-dispatch **spares**, not the enforcing rung. The
  enforcing rung is: **blocking pre-commit** (`scripts/pre-commit.sh`,
  frontend + routes tier, unchanged) → **`scripts/ci_local.sh`** (the full
  five-surface ladder, run by hand or nightly) → **devbox's launchd
  LaunchAgent** (`scripts/nightly-local.plist`, 05:00 ART, installed
  separately by the orchestrator after review).
- **The hosted rung stays explicitly OPEN, not closed by substitution.**
  Local-green does not retroactively make hosted CI/nightly "done" — that
  status only changes the day GitHub's Actions allowance is restored (a
  paid plan, or the monthly reset) and the now-`workflow_dispatch`-only
  workflows are actually run and observed green on `github.com`, or their
  triggers are re-enabled. Until then, "Tier 3 is verified" means "verified
  locally, hosted CI/nightly never executed" — say both halves, every time.
