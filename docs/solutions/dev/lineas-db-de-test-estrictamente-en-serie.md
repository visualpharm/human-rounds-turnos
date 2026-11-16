# DB-backed test lanes run STRICTLY serially — a parallel runner storm reads as 35 fake failures

**Date:** 2026-08-09 · **Area:** test harness / gate discipline (Tier 4 daemon batch 3 gate)
**Updated 2026-08-09 (later the same day):** the collision domain itself was
removed — `_dbtest` now appends a per-run token to every database name
(`tier4/test-harness-per-run-db`, see "Superseded" below). Rules 2–3 stand;
rule 1 is now defense in depth, not the only line.

## The incident

During the batch-3 gate, the full ASGI backend lane reported **1 failure + 35
errors, all in `test_queue_worker.py`** — every one a `DbUnavailable: database
"sana_turnos_test_queue_worker" does not exist` raised in `setUp`. None of it
was a code regression: an adversarial-review subagent had been told it "may run
focused unittest cases", and it ran `test_queue_worker.py` cases **concurrently
with the full lane**. Both runners use the same FIXED per-module database name
(`_dbtest.PerModuleDb("sana_turnos_test_queue_worker")` — create at
`setUpModule`, **drop at `tearDownModule`**), so whichever finished first
dropped the database out from under the other. The `--jobs` audit stderr
failure in the same lane was the same dropped-DB collision. A second symptom of
the same class: `FATAL: database "sana_turnos_test_<x>" does not exist` lines
interleaved with passing dots when two lanes overlap on other per-module DBs.

## Browser harness correction — 2026-09-05

The backend run-scoping described below did not extend to
`test/e2e/lib/harness.mjs`. Overlapping browser runs still shared literal
database names; one teardown removed `sana_turnos_test_e2e_admin_screen`
under another suite. The browser harness now allocates a unique physical
name per fixture, retains the restrictive disposable prefix and 63-byte
limit, and passes `state.dbName` into every query/mutation/teardown helper.
There is no ambiguous logical-name alias registry. Teardown failures fail
the gate instead of being silently ignored.

`test/e2e/harness-db-isolation.spec.mjs` creates two same-label fixtures,
checks both, tears one down, proves its database absent and the other still
queryable. Independent managed verification passes with zero skips. The
full session-candidate browser suite then passes 121 tests with zero failures
and eight declared legacy skips. This is local candidate proof, not deployment.

## The rule

1. **All DB-backed lanes run strictly serially** — never two unittest lanes,
   never a lane plus a reviewer/verifier agent touching DB-backed tests, never
   a lane plus an ad-hoc `unittest discover` in another session. The
   per-module DB name is the collision domain, and teardown DROPS it.
2. **Reviewer/verifier subagent briefs must say "read-only; do NOT run
   DB-backed tests while lanes are live"** — or the orchestrator must sequence
   them (review after lanes, or lanes after review).
3. **Diagnosis shortcut:** a lane where dozens of errors collapse to
   `database "…_test_<module>" does not exist` in `setUp` is a harness
   collision, not N code bugs. Re-run the lane alone before touching code.

## Why not random DB names? — SUPERSEDED

Originally rejected: per-run random suffixes would allow parallelism but
leak orphaned databases on kills (the harness dies mid-run often — SIGKILL
crash suites). That objection was answered the same day by giving the
suffix an EMBEDDED timestamp instead of pure randomness, plus a reclamation
sweep, so the leak is self-healing:

- `_dbtest.run_scoped()` appends one per-process token
  (`t<hex-epoch>p<pid>r<rand>`, same stamp shape as the routes tier's
  `scripts/run_routes_tests.sh`) to every `PerModuleDb` and ad-hoc test
  database name — two concurrent invocations own DISJOINT databases, so one
  run's `DROP ... WITH (FORCE)` can no longer terminate the other's live
  connections.
- The first `set_up()` of a later run reclaims orphans:
  `reclaim_stale_databases()` drops only exact scoped-shape names, never
  the current run's token, only stamps older than 2 h, only with zero live
  sessions, and with a no-FORCE drop (a racing connection makes Postgres
  refuse — `ObjectInUse` → skipped, never terminated).
- Python 3.14's `PythonFinalizationError` from `ConnectionPool.__del__` at
  interpreter shutdown is gone: `core/db.py` registers `close_pools` in
  atexit when the first pool is created, so pools close while joining
  threads is still legal.
- The same exact-head release exposed a sibling of that shutdown noise in
  the cassette tier: a **caught `urllib.error.HTTPError` is file-like** and
  keeps the response body open, so swallowing one without `close()` printed
  an ignored `_TemporaryFileCloser.__del__` ResourceWarning at interpreter
  shutdown — exit code still 0, gate nominally green. Every handler that
  swallows an HTTPError without reading its body to EOF now closes it
  (`otp_send._enviar_meta`, `ai_client`'s failover branch, `ai_referral`'s
  PDF branch, `data_backups`' retry branch, `system_status._probe_json`);
  handlers that `e.read()` fully are fine (http.client closes on EOF), and
  re-raise paths stay open on purpose (callers read the body for detail).

Both shutdown-noise classes have regression gates that assert a REAL
subprocess's whole stderr is marker-free (`PythonFinalizationError`,
`Exception ignored`, `Traceback`, `ResourceWarning`) — not just rc=0:
open-pool exit in `test_dbtest_run_isolation.py`, caught-401 cassette replay
in `test_meta_cassette.py:TestReplayShutdownIsClean`.

Proofs (pure-logic + real-Postgres + subprocess): `test/test_dbtest_run_isolation.py`.
Serial lanes remain the DEFAULT discipline (cheaper contention, and the
advisory-lock idioms still assume low concurrency), but a reviewer agent or
sibling session running a focused suite mid-lane is now an isolated
inconvenience, not 35 fake failures.
