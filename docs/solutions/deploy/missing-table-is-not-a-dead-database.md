# "Database broken" can mean a missing table, not a dead Postgres

**Date:** 2026-08-10 · **Area:** deploy / schema

## Symptom

Patient «Resultados de laboratorio» (or another feature) fails or logs
errors. Operator instinct: "the database is broken — restart it."

Postgres logs show:

```
ERROR: relation "lab_report_analysis" does not exist
```

Meanwhile `/ready` still returns `200 {"status":"ok"}`, `docker ps` shows
Postgres `healthy`, and disk is fine (~30%).

## Root cause

After the Alembic cutover (Tier 4 slice 4), `_SCHEMA_SQL` reaches a
database **only** through `0001_baseline` on a genuinely fresh install.
Existing prod/demo DBs are stamped at head and never replay the baseline
body.

`lab_report_analysis` was added to `db._SCHEMA_SQL` (and the app code that
queries it shipped) **without** a forward Alembic revision. Same trap
already documented in `0002_alert_claims.py`'s header. Prod was at
`0005_demo_refresh_completion` with 49 tables and no `lab_report_analysis`.

Restarting Postgres cannot create a relation the migration chain never
defined.

## Fix

1. **Immediate heal** (prod, IF NOT EXISTS — safe, additive):

```bash
ssh root@<host-B-ip> \
  'docker exec <prod-postgres> psql -U postgres -d sana_turnos -c "
   CREATE TABLE IF NOT EXISTS lab_report_analysis (
     id bigserial PRIMARY KEY,
     dni text NOT NULL,
     report_hash text NOT NULL,
     prompt_hash text NOT NULL,
     fecha text, sistema text, tipo_estudio text, resumen text, tono text,
     sensible boolean NOT NULL DEFAULT false,
     valores jsonb, modelo text,
     created timestamptz NOT NULL DEFAULT now(),
     UNIQUE (dni, report_hash)
   );
   CREATE INDEX IF NOT EXISTS lab_report_analysis_dni_idx ON lab_report_analysis (dni);
  "'
```

2. **Durable fix:** ship `alembic/versions/0007_existing_install_shape.py`
   (revises `0005_demo_refresh_completion`) and redeploy so the next boot
   stamps head. Do not rely on `_SCHEMA_SQL` alone for any new table.

## Graceful degradation (app)

`lab_analysis.analizar` already catches cache read/upsert failures and
continues with empty/fallback rows — so a missing cache table should not
take down `/ready` or the rest of the account. The patient still sees a
degraded labs view (no AI cache) until the table exists. That is correct
degradation; it is not a substitute for the migration.

## Auto-heal scripts

Restart-based heal scripts are for the **disk-full PANIC loop** class
([db-unavailable-means-disk-full.md](db-unavailable-means-disk-full.md)),
not for schema lag. Use
`humanrounds-pinamar/scripts/db-heal.sh` on the São Paulo host: prune when
disk is high, restart only *unhealthy* Postgres containers, and **report**
missing relations without making up DDL.

## Rule

**Before restarting a "broken" Coolify Postgres:** check `df -h`, then
`docker logs <db> | grep -E 'PANIC|does not exist'`. ENOSPC → prune disk.
Missing relation → Alembic forward revision. Restart is almost never the
first move.
