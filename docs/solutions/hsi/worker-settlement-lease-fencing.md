---
title: Fence every worker outcome and its local side effects
scope: repo
area: hsi
tags: [outbox, leases, transactions, concurrency]
created: 2026-09-25
refs: [tests/test_hsi_worker_fence.py, tests/test_hsi_sync.py]
status: active
---

## Problem
A delayed worker failure could reset a reclaimed lease or reopen a confirmed
intent. Stale successes, skips and conflicts could also overwrite booking
sync state, remote checkpoints and mirrors, or add a false conflict.

## Root cause
Create/reassign acknowledgement checked lease ownership, but generic replay
outcomes used id-only helpers and separate transactions for their side effects.

## Solution
Every generic replay settlement now checks both sending state and attempt
generation under the outbox lock. Booking writes acquire the booking lock
first, matching appointment acknowledgement. Status, identity, checkpoint,
mirror, conflict and journal settlement commit together. Stale outcomes return
the current journal row without changing it, including inside failure handlers.
Administrative id-only helpers remain separate from the replay guarantee.

## How to apply
Run the managed worker-fence regression against disposable PostgreSQL. It
checks delayed failure, newer confirmation, reset before reclaim, stale skip
and success, legitimate current-owner writes and actual SQL rollback.
Substituting the previous replay/outcome functions causes seven assertions to
fail; restore exact bytes before the fixed gate. Mock-based journal tests must
restore the first original function even when the same name is patched twice.

This fences one intent's local settlement, not remote execution. Distinct cancel
responses now use [corrective lineage](linked-cancel-checkpoint-ordering.md).
Reclaimed reassign recovery, repeated linked move keys and unconfigured outcome
uncertainty remain open.
