# The queue worker left the web process (Tier 4 worker topology)

**Date:** 2026-08-08 · **Branch:** `tier4/queue-worker-topology` → main `c23798fe`

## What shipped
`worker.py` (repo root) is the queue worker's own OS process: one procrastinate
app composing each migrated job's `register(app, …)` (waitlist, reminders,
maintenance, and filesystem data backups), HTTP `/health`+`/ready`, `procrastinate_workers`
heartbeat, graceful SIGTERM. `TURNOS_QUEUE_WORKER=1` → supervised subprocess
from `asgi_main.py` (backoff restart, lock-closed shutdown race, exit-code
contract); `=external` → own deployable (removed 2026-10-30: no installation ran it; only `=1` and
unset exist now). Deliberate reversal of slice 5's
in-process decision — reasoning recorded in
`docs/roadmap/tier4-replatform-design.md` § "Worker topology".

## Lessons that cost a review round each (verify these BEFORE shipping queue code)
1. **One flag, one parser.** Two call sites parsing `TURNOS_QUEUE_WORKER`
   differently (strip vs exact) let `"1 "` run threads AND worker at once.
   `queue_infra.worker_mode()` is the only parser.
2. **`queues=None` means ALL queues** in procrastinate — a worker with no
   registered tasks then poisons foreign jobs (TaskNotFound → failed, no
   retry). Always pass an explicit, structurally non-empty list.
3. **`JobManager.delete_old_jobs` is async.** A sync task body calling it
   without await returns a coroutine, deletes nothing, reports success —
   caught only by the product owner running it under `-W error::RuntimeWarning`. Janitor
   task is `async def` + awaited; suite runs with RuntimeWarning as error.
4. **Cron step syntax is not modular arithmetic.** `*/45` restarts at the
   hour boundary (15-min gap), `0 */5` at the day boundary, `*/59` likewise:
   any non-divisor cadence sweeps FASTER than configured. Exact-or-reject:
   divisors of 60, hours dividing 24, 1440 — everything else raises, and
   accepted values are croniter-proven across the boundary in tests.
5. **Exit codes are a supervisor contract.** 1 = transient → backoff-retry;
   2 = permanent config (no DATABASE_URL, non-expressible interval) → never
   retried. Without this the supervisor grinds a config error forever.
6. **Subprocess tests must own their processes.** A leaked worker (PPID 1)
   holding inherited stdout hung the harness: `start_new_session=True`,
   stdout=DEVNULL, killpg escalation, and a tearDownModule that FAILS the
   suite if any worker.py survives it.
7. **Demo proof under safety bounds:** demo runs waitlist/reminders OFF by
   design (no emails from Sampleton), so live durability is proven with a
   maintenance-queue marker job (todo → succeeded, started-events = 1),
   kill-the-worker recovery, heartbeat ledger and process census — never a
   business sweep with side effects.
8. **A filesystem job needs two locks.** Procrastinate serializes queued rows,
   but a legacy web-thread tick can still race the worker. Hold one
   session-scoped Postgres advisory lock across snapshot, upload, and journal
   commit, using the same checked-out connection so pool size one remains safe.
9. **Remote acceptance is not local commit.** A process killed after object
   acceptance but before the journal commit must re-upload safely and converge
   to one journal row. Test both pre-accept and post-accept SIGKILL boundaries.
10. **Persist the first grace period.** Cold-start delay belongs in the
    scheduled Postgres job row, not an in-memory sleep; the durable row proves
    restart behavior and keeps the web process out of the schedule contract.
11. **Topology is process configuration.** Never let DB or dotenv
    effective configuration decide the worker mode.
    `worker_mode()` reads process environment only. (Lesson from the removed
    `external` mode: it needed a volume attestation before any DB access.)

## Where the proof lives
`test/test_queue_worker.py` plus `test/test_data_backups_worker_proof.py`
(delegation matrix incl. whitespace,
supervisor spawn/retry/race, real-subprocess lifecycle, stopped-worker
durability, two-worker single-execution, periodic-tick uniqueness, janitor
retention row-by-row, pool-size-one backup locking, web/worker contention,
durable cold-start grace, crash boundaries, and outbound denial). Demo evidence 2026-08-08: deployed commit verified via
Coolify API, prod census = web only, demo census = web + worker.
