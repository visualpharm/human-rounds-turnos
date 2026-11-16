---
title: Recover a cancelled reassign after remote acceptance
scope: repo
area: hsi
tags: [outbox, cancellation, probe, crash-recovery]
created: 2026-09-25
refs: [tests/test_hsi_cancel_ordering.py, tests/test_hsi_legacy_reclaim_http.py, tests/test_emr_bridge_conformance.py]
status: active
---

## Problem
A remote A-to-B reassign can succeed before its local acknowledgement. If the
patient then cancels, the reclaimed R used to skip immediately, losing B and
the corrective cancel C2(B).

## Root cause
The cancelled-local shortcut ran before the remote-outcome probe. A retry of R
after cancellation is unsafe because the legacy mutation is not keyed.

## Solution
Keep the first-lease skip. On a later lease, probe R's stable key without
re-executing it. A definitive negative skips R. An exact successful result for
the same appointment, diary, patient and B slot uses the existing lease-fenced
ack transaction to record B and queue C2(B). Unavailable or invalid probes
leave R in retryable error. When the key probe is unsupported (`None`), the
legacy HSI adapter can make one read-only diary observation of the requested B
day. Exactly one row for the same appointment, diary and patient in scheduled
state at B yields the normalized move result; Core applies the same validation
and fenced acknowledgement to queue C2(B). Missing, duplicate, malformed or
drifted rows remain retryable uncertainty. This read never returns
`found:false`: absence does not prove an earlier worker cannot still finish
the move. A missing or unreadable local booking on any move is also retryable
without a remote call because its cancellation status is unknown.

## How to apply
Run the managed disposable-Postgres cancellation-ordering and legacy loopback
HTTP suites, plus bridge-v1 conformance for key-probe behavior. Cover remote
acceptance before a lost local acknowledgement, no second update-date,
corrective cancellation, absent/duplicate/drifted observations, failed
follow-up insertion and stale lease rejection. This is bounded local recovery,
not release, live verification, whole-system HSI safety or permission to
enable live HSI. Related: [[linked-cancel-checkpoint-ordering]].
