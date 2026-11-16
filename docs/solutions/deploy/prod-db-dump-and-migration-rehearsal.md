---
title: Prod Postgres dump from the dev machine + migration rehearsal on the real data
scope: repo
area: deploy
tags: [postgres, coolify, migration, backup, rehearsal]
created: 2026-07-30
refs: [cc53096]
status: active
---

## Problem
A schema migration (e.g. `migrations/001_english_schema.py`) must be rehearsed against REAL prod data before pushing, and a pre-migration backup taken — but the Coolify Postgres (`sana-turnos-db`, uuid `<prod-db-uuid>`) is not publicly reachable and there is no SSH to the host.

## Root cause
`is_public=false` on the Coolify database resource; the only remote path is the Coolify API's public-port TCP proxy.

## Solution
1. `PATCH /api/v1/databases/<uuid>` body `{"is_public": true}` (token `COOLIFY_ACCESS_TOKEN` in the operator's private secrets file, host coolify.brieflysites.com). The TCP proxy takes ~20–60 s to start — retry `pg_dump` in a loop, "Connection refused" just means not up yet.
2. Read `external_db_url` from the same GET endpoint. **It already ends with the db name** — appending `/sana_turnos` yields FATAL `database "sana_turnos/sana_turnos"`. Use `"$EXT?sslmode=prefer"` as-is.
3. `pg_dump "$EXT?sslmode=prefer" -Fc -f <workspace>/sana-turnos-backups/prod-pre-<change>-<date>.dump`, sanity-check with `pg_restore -l`.
4. `PATCH {"is_public": false}` IMMEDIATELY after — never leave the port open.
5. Rehearse: `createdb sana_turnos_prodcopy` + `pg_restore --no-owner` locally, then boot the NEW code against it (`DATABASE_URL=...prodcopy python3 -c "from core import db; db._connect()"`) — the boot applies pending schema changes exactly as prod will at deploy. Verify the migration bookkeeping (`alembic_version` at the expected head — since the legacy-runner retirement, a normal boot never touches `schema_migrations`; on this era's dumps it's a legacy fossil, present and full), renamed tables, row counts, and one wire-format read (e.g. dict keys unchanged). Drop the copy after.

## How to apply
Any future schema change (an Alembic revision since the 2026-08 cutover — numbered `migrations/` files are frozen): dump → rehearse on the prod copy → push, in that order. Same toggle also answers "query prod DB directly" one-offs (e.g. confirm `SELECT version_num FROM alembic_version` post-deploy). Related: [[backups-postgres-coolify]] (scheduled local backups on the host itself).
