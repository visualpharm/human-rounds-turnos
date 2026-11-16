---
title: Legacy HSI fallback reuses the durable journal
scope: repo
area: hsi
tags: [hsi, idempotency, crash-recovery, fallback]
created: 2026-08-10
refs: [2026-08-10-capture-human-rounds-tier-4-legacy-hsi-fallback-idempotenc]
status: active
---

## Problem

The thread-only `hsi_resync` fallback retried a direct HSI create and then
wrote local success. A crash after remote acceptance could retry a second
clinical appointment.

## Root cause

The fallback bypassed `hsi_outbox`'s immutable idempotency key, claim lease,
checkpoint, and remote-confirmation replay path.

## Solution

Keep the fallback thread-only and mutually exclusive with `hsi_sync`, but
enqueue one existing durable intent and call `hsi_sync.replay_item()` for that
exact id. Create, linked reassign, and linked cancellation retain their
existing operation; no full sync, inbox reconciliation, resolver, or queue
registration is invoked.

## How to apply

Future fallback retries must call `intent_for_row()` then exact-item replay,
never a direct push (the old `_push_hsi`, deleted 2026-11-06) or `db.update_hsi`. Preserve
`SKIP_HSI`, `HSI_SYNC_OFF`, `HSI_RESYNC_OFF`, cadence, and stop-first behavior.
Prove remote-accepted/local-uncommitted replay, two-run one-effect, linked
operation selection, and armed outbound-kill zero egress on disposable DB.
