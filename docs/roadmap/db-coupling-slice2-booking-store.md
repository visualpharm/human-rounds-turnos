# DB-coupling boundary 2 — `booking_requests_store` (frozen execution plan)

Status: **frozen** 2026-08-17. Base: current `main` @ `6c34573e`. Series position:
slice 2 of the per-table-family persistence-store extractions; slice 1
(`waitlist_store.py`, commit `91aaa293` on unmerged branch
`glm/turnos-persistence-adapter`) is the shape this plan copies.

This is an execution plan, not a discussion. Ownership, invariants, tests, and
non-overlap below are the contract; an executor works top to bottom without
re-deciding them.

---

## 1. Series context — the pattern slice 1 established

Slice 1 (`refactor: isolate waitlist persistence`) created
`modules/turnos/waitlist_store.py` as the ONLY module holding SQL, a Postgres
connection, or a `core.db` wire/coerce primitive for the `waitlist` and
`appointment_offers` tables. The rules it set, which bind every later slice:

1. **Orchestrator → store → `core.db`**, never orchestrator → `core.db`, for
   the slice's table family. After slice 1, `waitlist.py` no longer imports
   `core.db` at all.
2. **Delegate, don't duplicate**: where `core/db.py` already owns a correct,
   test-seeded function (`db.create_appointment_offer`), the store delegates
   1:1 and the implementation STAYS in `core/db.py`. Moving implementations is
   a later series — 13+ other modules still call them through `core.db`, and
   `core` may never import from `modules/`.
3. **Cross-family reads may stay on `core.db`**: slice 1 left
   `appointment_offer.py`'s `db.get_booking_request` call direct. (Slice 2
   tightens this to "the store exposes them as documented pass-throughs" —
   see §4 — because slice 2's orchestrator is large enough that stragglers
   would defeat the boundary.)
4. **Semantics preserved verbatim, proven by the existing DB-backed suites**
   — no new behavioral tests to "prove" a refactor; the race suites are the
   proof.
5. **Zero behavior change, zero schema change, single revertible commit.**

## 2. The bounded audit (current main @ `6c34573e`, 2026-08-17)

Method: census over the Python tree excluding `test/`, `scripts/`,
`alembic/`, `migrations/` — (a) raw SQL / private-primitive holders
(`db._connect|cur.execute|db._wire|…`), (b) `db.<fn>(` call-site density.
Feature layer = `modules/`, `admin_panel/`, `notifications/`, `cuentas/`,
`patient_web/`, `ai/`, `auth/`. Core/worker/ops are NOT feature layer.

### 2a. Raw-SQL holders outside `core/db.py` (whole tree)

| File | Sites | Layer | Disposition |
|---|---|---|---|
| `modules/turnos/waitlist.py` | 30 | feature | **slice 1** (sibling, unmerged) |
| `modules/turnos/appointment_offer.py` | 3 | feature | **slice 1** |
| `connectors/hsi/hsi_sync_queue.py` | 13 | connector infra | excluded — §7 |
| `ops/data_backups_queue.py` | 7 | ops | excluded |
| `core/queue_infra.py` | 7 | core | excluded |
| `ops/demo_refresh.py` | 5 | ops | owned by sibling `glm/demo-refresh-store-boundary` |
| `ops/data_backups.py` | 3 | ops | excluded |
| `admin_panel/system_status.py` | 3 | feature (probes: `SELECT 1`, `to_regclass`) | too small to be a slice — health probing IS that module's job |
| `worker.py` / `core/health.py` / `admin_panel/reports.py` (docstring only) | ≤2 | infra | excluded |

**Conclusion:** after slice 1 the feature layer holds essentially no raw SQL.
The remaining feature-layer coupling to draw a boundary around is the
orchestrators' direct dependency on `core.db`'s table-specific API — exactly
the dependency direction slice 1's charter names ("depend on THIS adapter,
never on `core.db`'s internals").

### 2b. Direct `core.db` API density, feature layer (distinct fns / call sites)

| File | fns / sites | Note |
|---|---|---|
| **`modules/turnos/booking_requests.py`** | **36 / 67** | largest by ~2×; the product's core write paths; all 36 fns live in `core/db.py` |
| `admin_panel/reports.py` | 30 / 40 | read-only aggregates (`reports_*`, `costs_*`) already grouped in `core/db.py`; a store would be a read facade with no write-path payoff |
| `cuentas/accounts.py` | 18 / 37 | cohesive auth-storage family; clean but smaller — natural slice 3 |
| `modules/intake/*` (4 files) | ~25 / 57 | `interview_*`/`consultation_*` family, spread across files — later slice |
| `modules/chat/*` (4 files) | ~20 / 30+ | `chat_*` family — later slice |
| `connectors/hsi/hsi_sync.py` + `hsi_resolver.py` | ~20 / 75 | do-not-relitigate hardened journal machinery — §7 |

## 3. Decision

**Slice 2 = extract `modules/turnos/booking_requests_store.py`; re-point
`modules/turnos/booking_requests.py` off `core.db` entirely.**

Why this over every runner-up:

1. **Largest single-file coupling in the feature layer** (36 fns / 67 sites —
   double the next file), in the same `modules/turnos` family slice 1 started,
   orbiting the same tables (`waitlist.request_id` → `booking_requests.id`).
2. **It is the product's core write surface**: create-under-limit
   (advisory-locked count+insert+idempotency), approve/reject/cancel/
   reschedule/attendance, HSI link writes. The persistence seam is where the
   already-named follow-up (`db.update_hsi` idempotency hardening, flagged in
   CLAUDE.md's worker-registry entry) lands later WITHOUT reopening the HTTP
   orchestrator.
3. **The proof structure already exists**: `test_integrity_races.py` pins the
   exact races this slice must not disturb (approve races, doctor-slot
   collision, booking-limit race, idem-key race, and the route-level variants).
4. **Continuity with slice 1**: once both stores exist, the two files cover
   the whole turnos patient-flow persistence, and slice 1's one leftover
   cross-family call (`appointment_offer.py`'s `db.get_booking_request`)
   gets its natural 1-line home.

## 4. Exact ownership

### 4a. Created — `modules/turnos/booking_requests_store.py`

One new file. It imports `from core import db` at module level (same import
safety as the orchestrator's existing top-level `from core import db`; the
orchestrator's "importing at server startup must never fail" charter is
unchanged — `booking_store` is imported exactly where `db` was).

Content, in order:

1. **Module docstring** stating the layer contract in slice 1's words: the
   single module `booking_requests.py` may reach persistence through; the
   `booking_requests`-table family it natively fronts; the cross-family
   pass-throughs (each named, each marked `# cross-family read — re-point
   when <family>_store exists`); the do-not-split warning on
   `create_booking_request_with_limit` (§5, invariant 3).
2. **Exception re-exports (same objects, never copies):**
   ```python
   DbUnavailable = db.DbUnavailable
   LimitReached = db.LimitReached
   AlreadyProcessed = db.AlreadyProcessed
   SlotConflict = db.SlotConflict
   ```
3. **36 delegations** — one `def` per function, same name, same signature
   (copy verbatim), body a single `return db.<name>(...)` (or bare call where
   the original returns `None`). Grouped and commented by family:

   *Booking-request family (the store's native charter — `booking_requests`
   table and its columns):*
   `create_booking_request_with_limit`, `get_booking_request`,
   `get_booking_request_by_idem_key`, `set_referral`, `save_ai`,
   `clear_referral_path`, `referrals_to_purge`,
   `assign_pending_booking_request`, `approve`, `reject`, `cancel`,
   `mark_attendance`, `update_hsi`, `reassign_appointment`,
   `reschedule_preference`, `list_admin`, `count_admin`, `list_by_dni`,
   `approved_in_range`, `list_approved_future`,
   `attendance_summary_dni`, `attendance_summary_by_dnis`.

   *Holds linkage (slot-holds family, the three booking-coupled fns + the
   release; `slot_holds.py` itself is a LATER slice — §7):*
   `hold_link_booking_request`, `holds_release_booking_request`,
   `hold_of_booking_request`, `hold_release`.

   *Cross-family pass-throughs (documented, one line each):*
   `waitlist_active` (waitlist — slice 1's family), `get_user`,
   `operator_doctor_id`, `get_dependent` (users/operators/dependents),
   `interview_for_request` (intake), `hsi_appointments_by_dni`,
   `hsi_sync_status`, `hsi_outbox_reset` (HSI connector),
   `refeps_get`, `refeps_set` (REFEPS cache).

   The full 36-name set is checkable — it is exactly the set of `db.<fn>(`
   names `booking_requests.py` calls on current main (audited; do not add
   "helpful" extras).

### 4b. Edited — `modules/turnos/booking_requests.py`

* Import swap: `from core import db` →
  `from modules.turnos import booking_requests_store as store`.
* All 67 `db.<fn>(` call sites → `store.<fn>(`; all exception references
  (`db.DbUnavailable`, `db.LimitReached`, `db.AlreadyProcessed`,
  `db.SlotConflict`, incl. the multi-except at the `_create` handler) →
  `store.<same>`. Comments citing `db.<fn>` docstrings are re-worded to cite
  the `core.db` function by its plain name (`create_booking_request_with_limit`,
  not `db.…`) — prose only, no link targets change.
* Nothing else in the file changes. No handler logic, no routing, no i18n
  strings, no HTTP behavior.

### 4c. Edited conditionally — `modules/turnos/appointment_offer.py` (1 line)

Only if slice 1 (`waitlist_store.py`) is present in the tree at execution
time: re-point its single `db.get_booking_request` call to
`booking_requests_store.get_booking_request` and drop `from core import db`
if no other `db.` use remains in the file. If slice 1 has NOT landed, skip —
do not create a partial dependency on an absent sibling.

### 4d. NOT touched (exhaustive)

* `core/db.py` — zero edits. All 36 functions and 4 exception classes stay,
  canonical and shared; the 13 other modules that call them through `core.db`
  (`connectors/hsi/*`, `modules/intake/*`, `modules/chat/chat_tools.py`,
  `notifications/send.py`, `admin_panel/*`, `ai/*`, `core/pii.py`,
  `cuentas/accounts.py`, `ops/demo_installation.py`) are unchanged.
* `modules/turnos/module.json`, `server.py`, `asgi_main.py`, any router —
  no route surface changes; the store is an internal import.
* Any migration/`_SCHEMA_SQL` — no schema change.
* Any JS/CSS/asset — no `?v=` bump, no fingerprint change.

## 5. Invariants (each must hold after the slice)

1. **Import direction**: `booking_requests.py` contains no `from core import
   db` and no `db.` reference of any kind (call, exception, or comment
   citation of the form `db.<fn>`). Dependency is
   `booking_requests → booking_requests_store → core.db`, and never
   `core → modules`.
2. **Exception identity**: `booking_requests_store.LimitReached is
   db.LimitReached` (same for the other three). Re-export, not subclass —
   `except` clauses in the orchestrator catch the exact objects `core.db`
   raises, and any OTHER module's `except db.AlreadyProcessed` still catches
   what the store forwards.
3. **Atomicity of the limit guard**: `create_booking_request_with_limit`
   stays ONE delegated call. The store (and nothing above it) may never
   re-compose it from `count_active_requests` + `create_booking_request` —
   the advisory-lock count+insert+idem-recheck in a single transaction is the
   race the guard exists to close (proven by `TestBookingLimitRace`,
   `TestIdemKeyRace`, `TestRouteBookingLimitRace`, `TestRouteIdemKeyRace`).
4. **Wire format frozen**: every function returns the same Spanish-key wired
   dict as before, including `create_booking_request_with_limit`'s
   `_ya_existia` flag semantics (True only on an idem-key re-fetch; the
   loser-of-race path depends on it) and `save_ai`'s RETURNING-row behavior.
5. **Signatures verbatim**: the 36 wrappers copy the current parameter lists
   and defaults exactly; kwargs used at call sites must keep binding.
6. **No behavior change anywhere**: no new SQL, no re-implemented logic, no
   renamed public names, no timing/transaction-shape change. This slice adds
   one indirection layer and nothing else.
7. **Import safety**: importing `booking_requests.py` at server boot keeps
   working exactly as today (`booking_requests_store` may import `core.db`
   at module level — that cost already existed via the orchestrator's own
   top-level import).

## 6. Tests

**Proof = the existing suites, run green, unmodified:**

* `test/test_booking_requests.py` — the lifecycle suite (create/approve/
  reject/cancel/reschedule/waitlist toggle, admin queue, referral flow).
* `test/test_booking_limits.py`, `test/test_slot_holds.py`,
  `test/test_booking_request_detail.py`.
* `test/test_integrity_races.py` — `TestApproveRace`,
  `TestDoctorSlotCollision`, `TestBookingLimitRace`, `TestIdemKeyRace`,
  `TestRouteApproveRejectRace`, `TestRouteDoctorSlotCollision`,
  `TestRouteBookingLimitRace`, `TestRouteIdemKeyRace`,
  `TestRouteDeskVsPatientHold` (desk-vs-patient hold exercise the
  hold-linkage delegations).
* `test/test_waitlist.py`, `test/test_waitlist_queue.py`,
  `test/routes/test_cancel_waitlist_trigger.py` — the cancel→re-offer
  trigger crosses this orchestrator and slice 1's tables.

**One new hermetic file — `test/test_booking_requests_store_contract.py`**
(no DB, no fixtures; it pins the boundary so the next slice can't silently
un-draw it):

1. *Import-direction guard*: read `modules/turnos/booking_requests.py` source
   and assert it contains no `from core import db` / `import core.db` and no
   `db.` token outside strings already removed per §4b (simplest robust form:
   assert the literal substrings `from core import db` and `db.` are absent
   from the file's import block and call sites — follow the pattern of the
   repo's existing source-scanning tests).
2. *Identity guard*: the four exception re-exports satisfy `is` against
   `core.db`.
3. *Surface guard*: for every name in the 36-function inventory (kept as a
   list in the test), `hasattr(booking_requests_store, name)` and the
   attribute is callable; and `dir()`-diff catches accidental extras beyond
   the inventory + the four exceptions.

**Run gates (in order):**

```
TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover -s test -p "test_*.py"
node --test test/*.test.mjs
```

The full backend discover run is the gate (not just the named files — the
orchestrator is imported transitively by route fixtures everywhere). The node
run is CI parity; zero JS changes, it must be green unchanged.

## 7. Non-overlap (explicit exclusions)

* **`ops/demo_refresh.py`** — owned by sibling worktree
  `glm/demo-refresh-store-boundary`. Not touched, not wrapped, not cited as
  a consumer.
* **`connectors/hsi/*`** — `hsi_sync.py`, `hsi_resolver.py`,
  `hsi_sync_queue.py` keep calling `core.db` directly. The 2026-08-09/10
  `hsi_sync` redesign (self-rescheduling chain, advisory locks, deterministic
  race proofs) and the 2026-08-10 fallback crash-safety work are
  **do-not-relitigate**; wrapping their persistence buys nothing and risks
  re-opening proven code. Their calls into booking-family functions
  (`reassign_appointment`, `update_hsi`, `mark_attendance`, `reject`,
  `get_booking_request`, `holds_release_booking_request`,
  `hsi_outbox_reset`) stay on `core.db` — unchanged by this slice.
* **`core/queue_infra.py`, `worker.py`, `core/health.py`,
  `admin_panel/system_status.py`** — infra/health, not feature layer; their
  SQL is connectivity probing or queue-table access, not table-family
  persistence.
* **`cuentas/accounts.py`, `modules/intake/*`, `modules/chat/*`,
  `admin_panel/reports.py`, `modules/turnos/slot_holds.py`** — later slices
  in this series (accounts is the natural slice 3). Their `db.` usage is
  untouched; nothing in this slice wraps their calls.
* **`core/db.py` decomposition** — out of scope. Implementations stay; the
  per-family migration of SQL out of the monolith begins only after the
  feature seams exist.
* **`db.update_hsi` idempotency hardening** (the known
  remote-accepted/local-uncommitted exposure CLAUDE.md already tracks) —
  NOT fixed here. This slice only guarantees that when it IS fixed, the fix
  lands in `booking_requests_store` (or below) without touching
  `booking_requests.py` again.
* **Booking-limits policy, holds policy, waitlist cadence** — closed product
  decisions; this slice re-routes calls, never re-opens them.

## 8. Execution steps

1. Branch off current `main` (or the integration branch slice 1 landed on, if
   it has landed — then expect `waitlist.py`'s diff context to differ from
   this audit's, harmless).
2. Create `modules/turnos/booking_requests_store.py` per §4a (docstring,
   4 re-exports, 36 delegations grouped by family).
3. Re-point `modules/turnos/booking_requests.py` per §4b (import swap + 67
   call sites + exception references + prose comment citations).
4. §4c check: is `waitlist_store.py` in the tree? Then do the 1-line
   `appointment_offer.py` re-point; else skip.
5. Add `test/test_booking_requests_store_contract.py` per §6.
6. Run both gates (§6). Green → done. Anything red in a race suite = the
   slice broke an invariant; fix the slice, never the test.
7. Single commit: `refactor: isolate booking-request persistence (store
   boundary 2)` — same style as slice 1's. No merge to `main` from this prep
   worktree (that's the integrator's move, after slice 1).

## 9. Verification, rollback, deploy

* **Verified by**: the §6 suites (DB-backed, race-inclusive) plus the new
  contract test — all local; the slice changes no deployable behavior beyond
  an import indirection, so no new smoke step is owed beyond the repo
  standard (`scripts/smoke_prod.py` / `scripts/smoke_demo.py` after the
  eventual merge to `main`, per the deploy contract).
* **Rollback**: revert the single commit. No schema, no env, no secrets, no
  data migration — nothing else to undo.
* **Risk profile**: mechanical re-pointing over race-tested code; the only
  novel failure modes are a typo'd delegation (caught by any suite touch) or
  a copied-not-re-exported exception (caught by the identity guard).

## 10. Series ledger (state after this slice)

Feature-layer direct-`core.db` consumers, remaining: accounts is now covered
by slice 3's `cuentas/accounts_store.py` (users/sessions/OTP/reset-tokens/
dependents); intake
family, chat family, reports read-facades, slot_holds, calendar_feed /
doctor_appointments (small turnos siblings that can fold into existing
stores). Raw-SQL residue outside feature layer stays as inventoried in §2a
until the core/ops/connector tracks are picked up deliberately.

## 11. Reviewed overbooking extension — 2026-09-04

The released percentage-capacity behavior extends the historical inventory
above from 36 to 37 functions and from four to five exception re-exports:
`overbooking_count` preserves the time window, doctor name/ID, center and
excluded-request arguments; `OverbookingCapacityReached` is the exact database
exception object, not a copied class. The orchestrator still calls only the
store. `approve` passes the capacity/window/lock/center arguments to the atomic
database enforcement; the preceding count is advisory, not the final limit.

The exact surface guard includes only these two named additions. A behavioral
delegation test checks every count argument and return value, the identity guard
includes the new exception, and the database-backed booking and integrity-race
tests cover disabled/exhausted capacity and competing approvals. This documents
the intentional extension; it does not permit arbitrary new store exports.
