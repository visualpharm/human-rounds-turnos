# Row 3.5 — durable queue acceptance evidence

Criterion, unchanged: two instances never duplicate; restart loses nothing;
booking to HSI retry end to end is green. Verified 2026-09-05.

## Integrated code and real behavior

Manual retry proof `8cb6151951d0e5f90391d9a5484fbe3a0458e438` and automatic
worker proof `1fc836ccf6642570da95072a7a14f19ea15c1e16` (merge `b35d3c8a`)
are ancestors of deployed `52d547e67606d4d4f3436e0a2a6f65fb1644cf7d`.

`test_hsi_worker_retry_acceptance.TestWorkerClaimsDueHsiError` performs a real
approval and loopback HTTP failure, retains the durable error row and backoff,
then waits for the registered worker's scheduled claim. A later tick and a
fresh worker cannot produce another remote appointment. After the failure,
the test does not call manual replay or reset the row into a different state.

`test_hsi_booking_retry_acceptance.TestBookingFailedHsiDurableRetry` covers
the separate manual durable-replay path. The 18 methods in
`test_queue_worker.TestHsiSyncCrashRecoveryAndContention` cover real two-worker
serialization, SIGKILL recovery, the remote-accepted/local-uncommitted crash
window, cold and rolling restarts, lock contention and watchdog recovery,
including deliberate broken-lock controls that prove the race is real.

## Executed and deployed proof

The immutable candidate tree was `a06527198d843f59d1dc25fea9c38ea59ce512d6`.
Managed `local-ci-asgi` run `run.cmUNkd` executed 5,354 backend tests in
905.625 seconds: zero failures/errors, 99 declared external-pack skips.
The exact 20 tests above appear as `ok`; none belongs to the skip list.
The complete ladder also passed 1,365 frontend tests, 13 route files,
31 cassette tests with a clean audit, and 198 browser tests with eight
declared mode skips. Source/tree were unchanged through terminal success.

Production and demo running containers returned the same full `SOURCE_COMMIT`.
Both live smoke commands passed. HSI remained
opt-in and disabled on the Pinamar reference deployment; no live patient or
institutional integration was invoked to obtain this proof.

Reproduce with `python3 scripts/turnos_managed_exec.py --profile local-ci-asgi`.
Durable evidence keys: `astra-52d547e6-release-evidence.md`,
`local-ci/run.cmUNkd/backend.log`, `backend-results.json`, and the two live
smoke reports in the private verification artifact store. Raw logs are not
published into the engine repository. This closes row 3.5 only.
