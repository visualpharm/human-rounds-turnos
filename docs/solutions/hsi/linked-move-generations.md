---
title: Linked appointment move generations
scope: repo
area: hsi
tags: [outbox, reassign, idempotency, checkpoint]
created: 2026-09-25
refs: []
status: active
---

## Problem
An approved, linked A→B→A→B cycle could reuse the first confirmed A→B
idempotency key when the checkpoint returned to the same value. The last move
then vanished as an outbox conflict, leaving the remote slot at A.

## Root cause
The direct reassign key identified the request, remote appointment, target and
checkpoint, but not which local time change requested that target. Direct
enqueue also trusted a pre-mutation row while acknowledgement could advance
the remote checkpoint before replay.

## Solution
Use the latest committed `appointment_events` time event ID as the move
generation; a pre-audit linked row with no event uses baseline zero. Lock the
booking before validating the target and enqueuing. A still-recoverable older
create/reassign is delivered first, then its acknowledgement queues the current
move using the fresh checkpoint. The key includes generation and checkpoint;
ack-generated reassigns also retain their predecessor intent ID. Reuse active
same-generation rows and confirmed rows whose result proves the current remote
identity and slot. A refreshed checkpoint after terminal conflict can mint a
correction. Backfill and fallback skip typed stale snapshots and keep scanning.

## How to apply
Every direct linked producer calls `hsi_journal.enqueue_reassign(row, when)`
after the local mutation; it validates against the locked current booking.
Do not derive a reassign key from a caller snapshot or reset a confirmed row.
Prove cycles, retries, stale producers, and direct/ack convergence with the
disposable Postgres plus checkpoint-enforcing loopback bridge fixture.
