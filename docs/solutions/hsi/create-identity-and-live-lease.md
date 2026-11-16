---
title: Preserve one outstanding EMR create and its live lease
scope: repo
area: hsi
tags: [outbox, concurrency, idempotency, crash-recovery]
created: 2026-09-25
refs: [2026-09-24-hsi-unlinked-accept-crash-window]
status: active
---

## Problem
A changed appointment time produces a different create idempotency key.
Backfill can enqueue it while the old create is still unsettled. Manual retry
could also clear an unexpired sending lease and let a second worker claim it.

## Root cause
Key uniqueness protects identical payloads, not one clinical appointment.
Reset excluded only confirmed rows; it did not preserve a live worker's lease.

## Solution
`hsi_outbox_enqueue` serializes appointment-create producers with a transaction
advisory lock and returns the oldest unsettled create without changing its
payload, key or lease. Other requests and operations remain independent.
`hsi_outbox_reset` admits pending/error/conflict or an expired sending lease,
never a live sending lease or a confirmed intent.

## How to apply
Run the managed backend-focused `tests/test_hsi_outbox_identity.py` proof.
Eight real-Postgres tests cover concurrent producers, backfill after a time
change, all unsettled states, lease ownership, expiry and operation isolation.
Against the original database methods they produce seven failures (including
four subtests); corrected methods pass all eight. Bridge conformance also passes.
These are narrow safeguards. The subsequent
[atomic acknowledgement fix](atomic-appointment-acknowledgement.md) routes
unlinked moves through this identity and preserves the latest patient choice.
Do not mark the whole crash-window finding closed or enable live HSI from this.
