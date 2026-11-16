---
title: Revision-specific rollback proof
scope: repo
area: deploy
tags: [alembic, rollback, recovery, negative-control]
created: 2026-09-10
refs: [test/test_queue_sse.py, alembic/env.py]
status: active
---

## Problem
The upgrade/runbook text claimed every downgrade raises and clearing the
revision marker repairs only metadata. Both claims could mislead recovery.

## Root cause
Documentation predated real 0009/0010 table downgrades. Existing proof checked
only the queue table, not challenge state or full booking-row preservation.

## Solution
Use the real Alembic command and disposable Postgres. Assert both owned
tables disappear/reappear across head-to-0008-to-head, preserving the exact
booking row. Seed non-empty queue and consumed challenge records, attempt a
downgrade through refusing 0008, and prove revision, tables and bytes survive.
Root negative control set transaction_per_migration=True: the test failed
with revision0008 instead of head. Restoring real configuration returned
all six tests to green; no source-only proxy or skipped database proof.

## How to apply
Read actual revisions before writing rollback advice. Do not infer safety
from a table's existence or an empty fixture. Metadata stamping requires a
proven schema/data state. Narrow migration tests do not replace an independent
database-plus-volume restore rehearsal or authorize production recovery.
