---
title: Preserve remote uncertainty when a connector becomes unavailable
scope: repo
area: hsi
tags: [outbox, configuration, crash-recovery, lease]
created: 2026-09-25
refs: [tests/test_emr_bridge_conformance.py, tests/test_hsi_worker_fence.py]
status: active
---

## Problem
A remote operation could succeed before its response was lost. Removing the
connector selection or configuration then confirmed its retry as a local skip,
losing recovery and falsely changing the booking status.

## Root cause
The unconfigured branch settled every operation before checking attempt history
or remote linkage. An unavailable probe cannot establish remote absence.
Direct cancellation and moves also wrote skip despite older unsettled intents;
a separate cancellation status write could overwrite a newer acknowledgement.

## Solution
Only a first-lease create with a readable, unlinked booking may degrade locally.
All other operations remain retryable without remote calls or booking/checkpoint
changes. The local-only decision rechecks the booking status and absence of a
remote link under the settlement lock; a concurrent change invalidates the skip.
The existing attempt fence rejects stale workers. Reconfiguration resumes the
original stable intent and can adopt its already-settled remote result.
Direct no-connector status selection now shares the local mutation's booking
lock. Existing unsettled intents retain pending/error visibility without adding
journal work or remote calls. Linked rows without unsettled work retain skip;
unlinked legacy cancellations without an outbox retain their existing status.

## How to apply
Use disposable PostgreSQL and the loopback bridge to lose each operation's
response, remove configuration, then restore it. Assert original intent recovery,
no second mutation, unchanged checkpoints while offline, first-lease linked
operations, missing bookings, concurrent links and stale-lease rejection.
Exercise cancellation, rescheduling and waitlist acceptance after deselection;
pause a cancelled caller until the newer corrective cancel has confirmed.
This is bounded journal safety. Backfill status preservation is covered separately
in [[backfill-cancellation-uncertainty]] and move identity in [[linked-move-generations]].
Selected-path post-cancel status races, cross-connector ownership and legacy
probe recovery remain open. No live HSI or whole-system acceptance is claimed.
Related: [[cancelled-reassign-reclaim]], [[worker-settlement-lease-fencing]].
