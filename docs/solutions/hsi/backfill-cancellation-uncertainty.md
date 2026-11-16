---
title: Preserve cancellation uncertainty during backfill
scope: repo
area: hsi
tags: [outbox, backfill, cancellation, concurrency]
created: 2026-09-25
refs: [tests/test_emr_bridge_conformance.py, tests/test_hsi_resync.py]
status: active
---

## Problem
A cancelled booking without a local remote ID could still have a remote
appointment after its create response was lost. Backfill marked it skipped,
hiding uncertainty or overwriting an acknowledgement that arrived after its scan.

## Root cause
The scan treated an absent local ID as remote absence and updated by ID without
an outbox lease or current booking check. Removing that write exposed fallback
stop-first starvation: a non-actionable row returned no intent and blocked others.

## Solution
Backfill never changes a cancelled-unlinked snapshot. Fenced outbox replay owns
remote proof, adoption and corrective cancellation. The thread/manual fallback
also ignores that snapshot; genuine failed attempts still stop its sweep.

## How to apply
On disposable PostgreSQL and a loopback bridge, lose a remotely accepted create
response, cancel locally, backfill and recover the original intent without a
second create. Hold a scan across acknowledgement and prove no overwrite.
Preserve pending/error/sending outbox rows and absent-outbox rows; prove later
fallback rows progress and actual failures still stop the sweep. Substitute the
old journal and fallback independently to demonstrate regression sensitivity.
A cancelled-unlinked row with no outbox is preserved, not recovered; its remote
history remains unknown. No whole-system or live HSI acceptance is claimed.
Related: [[unconfigured-intent-uncertainty]], [[legacy-fallback-reuses-durable-journal]].
