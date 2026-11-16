---
title: Acknowledge remote appointments against the current patient decision
scope: repo
area: hsi
tags: [outbox, transactions, crash-recovery, concurrency]
created: 2026-09-25
refs: [tests/test_emr_bridge_conformance.py, tests/test_hsi_sync.py]
status: active
---

## Problem
A remote create can succeed before its local link is saved. An unlinked
patient move then directly created another appointment; an old response
could overwrite the patient's newer time or lose the necessary cancellation.

## Root cause
Move callers bypassed the durable create identity. Link, follow-up and journal
confirmation used separate transactions, with status/time read before locking.

## Solution
Unlinked reschedules and waitlist accepts persist locally and reuse the first
unsettled create. Acknowledgement locks the booking row and claimed intent,
checks the lease generation, reads current status/time and atomically writes
the link, checkpoint, mirror, required follow-up and confirmation. Patient
moves reject terminal rows under the same booking lock.

## How to apply
Use the managed bridge conformance suite with real disposable PostgreSQL and
loopback HTTP: crash recovery across dates, public move/offer paths, concurrent
move/cancel, real SQL rollback and stale-worker acknowledgement. Replacing only
the previous replay function reproduces lost-time and lease failures. Restoring
the previous public move functions independently reproduces direct-create calls.
The bounded split-phase model and negative controls are retained in the
September 25 continuation artifacts; they do not prove full HSI liveness.

Generic retry and other outcome fencing is covered by
[worker settlement lease fencing](worker-settlement-lease-fencing.md).
Linked cancellation response ordering is covered by
[corrective lineage](linked-cancel-checkpoint-ordering.md). Remaining acceptance:
reclaimed reassign recovery, repeated linked move keys and unconfigured uncertainty.
No full-CI, release, live-HSI or entire-audit closure follows from this slice.
