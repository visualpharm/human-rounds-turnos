---
title: Observe the complete HSI worker acknowledgement
scope: repo
area: dev
tags: [hsi, worker, acceptance, postgres]
created: 2026-09-05
refs: [d0138824]
status: active
---

## Problem
Full CI sometimes observed booking HSI status `ok` while its outbox intent
was still `sending`, failing the automatic worker-retry acceptance test.

## Root cause
The real replay first commits confirmed remote identifiers to the booking,
then writes checkpoints, then confirms the outbox in a separate transaction.
Booking success alone is not evidence that the whole journal acknowledgement
has completed. This ordering has an intentional recoverable crash window.

## Solution
Wait within the original deadline for both booking success and its single
confirmed intent. Keep the attempt count, remote POST count, subsequent tick,
and fresh-worker exactly-once assertions. Do not alter production retry code.
A separate disposable-Postgres reader regression commits the intermediate
`ok`/`sending` state: the old signal is true, the completion signal is false,
and only the actual journal-confirm transaction makes it true.

## How to apply
Run the managed `test_hsi_worker_retry_acceptance.py` suite. The reader test
is not a substitute for its real-worker and loopback-HTTP acceptance test.
Avoid longer sleeps, wider timeouts, or acceptance based on a partial write.
