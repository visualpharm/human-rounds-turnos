# Bootstrap classifier's open read transaction deadlocks Alembic's own connection

**Date:** 2026-08-17 · **Area:** core/db.py `_ensure_schema`, migrations shape gate · **Class:** solved problem

## Symptom

A bridge-boot test run (`TestLegacyRunnerRetired`) hung forever — no failure,
no output. `pg_stat_activity` showed the true shape:

- the bootstrap connection **idle in transaction**, last query
  `SELECT EXISTS (SELECT 1 FROM catalog WHERE created > updated)` — the
  stamp gate's data-backfill probe;
- Alembic's connection **active, waiting on a relation lock**, executing the
  baseline replay (`CREATE TABLE IF NOT EXISTS booking_requests …` batch).

## Root cause

`_ensure_schema` classifies/repairs on `conn`, then hands off to
`_run_alembic_upgrade`, which runs on **its own separate
connection/engine** (`alembic/env.py`). psycopg opens a transaction
implicitly on `conn`'s first statement and leaves it open until an explicit
commit/rollback — so every catalog read and `EXISTS` probe the classifier or
`repair_shape_gaps` ran was still holding AccessShare locks on real tables
(`catalog`, `booking_requests`, `operators`). The baseline replay's
`ALTER TABLE`/`UPDATE` statements on those same tables queue behind them.
Mutual deadlock: this session idles waiting for Alembic to return; Alembic
waits for this session's locks; neither ever proceeds. `pg_advisory_lock` is
NOT involved — it is session-level and orthogonal.

It never bit before because `apply_pending()` ends with `conn.commit()`, and
the pre-repair partial path always went through it last; adding
`repair_shape_gaps` (whose final no-op gap recomputation reads and returns
without committing) put fresh read locks between the last commit and the
Alembic handoff.

## Fix

One `conn.commit()` in `_ensure_schema`, after classification/bridge/repair
and immediately before `_run_alembic_upgrade` — closes the read transaction
and releases its locks; the session-level advisory lock survives the commit,
so the cross-process bootstrap exclusion is unchanged.

## Rule

Any code path where connection A does reads (even "harmless" SELECTs) and
then synchronously waits on connection B doing DDL over the same tables must
end A's transaction first. "Read-only" does not mean "lock-free": an open
transaction's AccessShare blocks AccessExclusive, and an idle-in-transaction
session never times out on its own.

## Proof

`test/test_alembic_cutover.py` bridge-path tests
(`TestLegacyRunnerRetired`, `TestStampGateProvesBaselineShape`) complete in
seconds against a real Postgres; before the fix the same tests hung
indefinitely.
