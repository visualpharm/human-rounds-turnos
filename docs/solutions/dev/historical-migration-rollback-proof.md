---
title: Test a reversible migration without crossing later forward-only revisions
scope: repo
area: dev
tags: [alembic, rollback, testing]
created: 2026-09-14
refs: [test/test_queue_sse.py, test/test_integrity_races.py]
status: active
---

## Problem
The queue table's downgrade test began at current head. After newer
forward-only migrations landed, it failed before reaching revision 0009.

## Root cause
The test treated a migration that was once latest as permanently latest.
Rewriting the version stamp or enabling later downgrades would hide the
actual contract. Separately, a revision lookup read a UTF-8 ini file using
the managed runner's ASCII locale despite needing only the script path.

## Solution
Create a run-scoped, disposable database and upgrade normally to 0009.
Insert a synthetic booking, downgrade to 0008, and upgrade back to 0009.
Assert the exact version, table removal/recreation, unchanged booking row,
and successful insert into the recreated table. Keep the current-head
fixture untouched and register database cleanup immediately after creation.
For script-only revision lookup, use Config() plus script_location, matching
the runtime configuration pattern without reading an unnecessary ini file.

## How to apply
Run the managed focused queue SSE and integrity-race suites. Do not stamp
away later migrations, change their runtime downgrade policy, or infer a
production rollback capability from this historical table proof. Complete
the combined verification ladder before release.
