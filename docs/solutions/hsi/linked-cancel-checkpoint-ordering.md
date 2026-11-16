---
title: Preserve corrective cancellation authority across late responses
scope: repo
area: hsi
tags: [outbox, cancellation, concurrency, checkpoints]
created: 2026-09-25
refs: [tests/test_hsi_cancel_ordering.py, tests/test_hsi_integrity.py, tests/test_emr_bridge_conformance.py]
status: active
---

## Problem
A cancellation using checkpoint A could report success after a remote move to
B, replace B's checkpoint and hide the required corrective cancellation C2.
A selected patient cancellation also used to write pending after its local
cancellation transaction. If C2 confirmed in that gap, the late pending write
left the booking visibly unsynchronized even after its own superseded intent
and a scoped backfill/replay pass.

## Root cause
One-day absence was treated as cancellation. Per-intent lease fencing did not
order distinct intents; insertion order also cannot establish authority because
a producer may enqueue an already-stale checkpoint after C2. The selected
patient path had a second, unconditional status write outside the booking lock.

## Solution
The legacy adapter verifies a complete finite diary range for the same ID before
accepting disappearance, including after the write. Malformed reads or incomplete
ranges remain uncertain. Core settlement locks the booking and claimed lease;
only confirmed predecessor lineage matching the current normalized identity and
slot establishes corrective authority. Older responses cannot regress that
correction. Apparent success while a move is unresolved stays retryable, allowing
convergence when the move later skips. R acknowledgement supersedes only matching
older failed cancel payloads and their exact conflict records; sending work stays.
The selected patient path chooses pending in the original cancellation transaction
for a linked booking, under that same booking lock. Its later enqueue never
blindly changes status. Backfill can recover a failed enqueue. A redundant
non-lineage direct cancel may still reach the bridge and settle superseded after
C2; the confirmed C2 status remains authoritative.

## How to apply
Run the managed disposable-Postgres ordering suite and adapter integrity suite.
Cover both acknowledgement orders, correction retry/conflict/success, late older
failures, stale later producers, same-slot version changes, a held replay, and
a held selected patient cancellation through scoped backfill/replay. The bridge
conformance module retains bookings between tests; a whole-module replay can
process unrelated rows and reused synthetic remote IDs can falsely heal a status.
Inspect only the target booking's outbox and replay its own remaining intents.
Substituting prior production code must expose the race, then restore exact bytes.
Related: [lease fencing](worker-settlement-lease-fencing.md).

This proves bounded local response ordering, not remote-side fencing. Reclaimed
cancelled moves are covered separately by [[cancelled-reassign-reclaim]]. The
legacy adapter has no key probe but can observe an exact B appointment as
positive-only recovery. Full engineering acceptance, release, and live HSI
behavior require separate evidence.
