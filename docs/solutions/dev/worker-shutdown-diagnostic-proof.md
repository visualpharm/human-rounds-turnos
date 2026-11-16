---
title: Identify worker shutdown waits without changing cancellation
scope: repo
area: dev
tags: [worker, shutdown, diagnostics, acceptance]
created: 2026-09-04
refs: [test/test_worker_diagnostics.py, test/_workerproc.py]
status: active
---

## Problem
A 45-second shutdown failure logged only a stop request. Its final 40 lines
could not identify which asynchronous phase was still waiting.

## Root cause
Stop is a cooperative flag, not evidence that prune, registration, side-task
shutdown or unregister has completed. A passing shortened mock is not cleanup
proof. A class-level skip also freezes the pre-setup database state and can
silently skip the very test intended to validate the diagnosis.

## Solution
The opt-in test bootstrap traces the real installed dependency methods and
executes the real worker entrypoint. A ready handshake gates the diagnostic
signal; the loop callback emits bounded function/file/line locations only.
WorkerProc requests a snapshot inside its existing budget and preserves the
whole bounded dump before group-kill, not merely its final polling tasks.

## How to apply
Run the managed focused test_worker_diagnostics.py proof: a synthetic table
lock blocks the real prune DELETE, the dump names that wait, and release lets
native registration and graceful unregistration finish without an orphan.
Only then use diagnostics on a failing lifecycle case. No missing-DB success,
budget increase, fake half-second success or cancellation of unregister.
The controlled lock proves diagnostic capability, not the historical cause.

## Captured failure and targeted correction
The next real worker run reproduced the failure inside native shutdown's
side-task gather, with the periodic deferrer asleep after the sole cancellation
request. Prune/register had finished; unregister had not started.

The installed psycopg-pool reset path can catch CancelledError and close a
connection without re-raising. Python retains the task cancellation count. A
controlled regression exercises that installed catch, the real periodic loop,
and native Worker shutdown: the stock deferrer sleeps again, while the guarded
deferrer re-raises before sleeping and native unregister completes. The precise
pool timing in the original process trace was not observed directly.

The production adapters change only periodic/heartbeat call boundaries;
Worker.run, its job loop, shutdown and unregister remain library-owned. Keep
the 45-second process test and real database lifecycle gate as independent
proofs. Reevaluate these adapters when upgrading the pinned queue/pool libraries.

## Heartbeat follow-up boundary
The same controlled pool-reset seam proves that the native heartbeat loop can
also continue after its sole cancellation is consumed. Its adapter wraps only
the constructed worker app's `update_heartbeat` call and re-raises if the task
is still cancelling after that native call returns; it does not copy a
Procrastinate loop or replace Worker.run, shutdown, or unregister.

A later `test_ai_cache_queue_proof` timeout ended after the async stop request,
which localizes it before normal worker completion but does not identify the
stuck side task or prove heartbeat was involved. A diagnostic-enabled managed
rerun passed without a trace, so it supplies no historical-cause evidence.

An independent composition regression calls the real JobsManager through the
adapter and native in-memory connector dispatch, verifies the stored heartbeat
advances, and proves a second app's manager is unchanged. This closes the
fake-manager-only test gap; it is not a forced real-Postgres cancellation window.

Boot-lifecycle test doubles must preserve the native constructor contract:
each worker retains its constructing app, and each app owns an async
`job_manager.update_heartbeat`. Otherwise the adapter fails before signal and
drain assertions execute. Fix those doubles, never bypass the adapter or relax
the real process exit-code, handler-restoration, or shutdown-deadline checks.
