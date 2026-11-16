# Testing a branch with a new Alembic revision migrates the local dev database

**Seen 2026-09-24** (branch `formal-open-0924`, revision `0015_formal_invariants`).

## Symptom
After running the backend suite from a worktree whose branch adds an Alembic
revision, the local dev database (`DATABASE_URL`, `localhost:5432/sana_turnos`)
reports the NEW head. `main` then can't boot locally (Alembic can't locate the
revision), and tests on `main` that read the env file's `DATABASE_URL` behave
differently (the DB looks down, so "503 when the DB is down" tests pass there and
fail on the branch — a false diff between base and branch).

## Cause
The suite runs with the checkout's env file (or `TURNOS_ENV_FILE` pointing at
main's). Some modules touch `db` before a `_dbtest.PerModuleDb` has pointed
`DATABASE_URL` at its throwaway database; the first connection runs
`_ensure_schema` → `upgrade(head)` against the dev database.

## Fix / how to test a migration branch
- Run branch suites hermetically: `TURNOS_ENV_FILE` = an EMPTY file (the mode
  `db._load_env_file` documents), `TEST_DATABASE_URL` pointing at a test lane,
  `DATABASE_URL` unset. Nothing can reach the dev database, and base vs branch
  comparisons are apples to apples.
- If it already happened and the revision is additive with no data changes,
  roll the dev database back by hand (drop the revision's objects, set
  `alembic_version` to the previous head) — check first that its backfills
  touched no rows.
